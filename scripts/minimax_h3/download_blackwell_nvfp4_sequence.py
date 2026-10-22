#!/usr/bin/env python3
"""Resume the two pinned MiniMax H3 Blackwell artifacts in strict order.

The currently managed Ref2VA downloader may already be active.  In execute
mode this command first waits for that exact user unit to become inactive and
does not probe the network or open either staging directory while it is
running.  It then resumes/verifies Ref2VA with the crash-safe range downloader,
performs an independent stable exact-file gate, and only after that gate calls
the FL2VA downloader.

Both artifacts use owner-only resumable staging, exact byte/SHA-256 pins,
fsync, and atomic publication supplied by ``download_pinned_ranges``.  The
command is a no-write, no-network validation unless ``--execute`` is present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from download_pinned_ranges import (
    DownloadError,
    DownloadSpec,
    RangeDownloader,
    stage_path_for,
)


REPORT_SCHEMA = "castle.minimax-h3.blackwell-nvfp4-sequence.v1"
REPOSITORY = "lilcheaty/MiniMax-H3-NVFP4"
REVISION = "8c5abfed61e1b6a170240792b65253fba1a65b7b"
EXPECTED_BYTES = 12_528_636_800
LANES = 16
MODEL_ROOT = Path("/data/comfyui/models/diffusion_models/H3")
ACTIVE_REF_UNIT = "castle-h3-nvfp4-ref-download.service"
SYSTEMCTL = Path("/usr/bin/systemctl")
WAIT_SECONDS = 15.0
BUFFER_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Artifact:
    role: str
    filename: str
    sha256: str

    def spec(self) -> DownloadSpec:
        return DownloadSpec(
            url=(
                f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/"
                f"{self.filename}?download=true"
            ),
            destination=MODEL_ROOT / self.filename,
            expected_bytes=EXPECTED_BYTES,
            sha256=self.sha256,
            lanes=LANES,
        )


REF2VA = Artifact(
    role="ref2va",
    filename="minimax_h3_ref2va_pruned_nvfp4.safetensors",
    sha256="c813c5eabd85e275daccbf45e6f8ac4d9d14a1827d425e5be5070c92c60b78ac",
)
FL2VA = Artifact(
    role="fl2va",
    filename="minimax_h3_fl2va_pruned_nvfp4.safetensors",
    sha256="72fa9269ce551fb63ff42a32d9b46d0c122e84b4b2c511e22fa698287b088f70",
)


def _fail(code: str, message: str) -> None:
    raise DownloadError(code, message)


def _artifact_plan(artifact: Artifact) -> dict[str, object]:
    spec = artifact.spec()
    # Constructing the downloader performs all static URL, pin and destination
    # validation.  It deliberately does not inspect a final or use the network.
    RangeDownloader(spec)
    return {
        "role": artifact.role,
        "repository": REPOSITORY,
        "revision": REVISION,
        "source_file": artifact.filename,
        "destination": str(spec.destination),
        "expected": {"bytes": spec.expected_bytes, "sha256": spec.sha256},
        "staging": str(stage_path_for(spec)),
        "lanes": spec.lanes,
    }


def _unit_state(unit: str) -> str:
    try:
        result = subprocess.run(
            [
                str(SYSTEMCTL),
                "--user",
                "show",
                unit,
                "--property=LoadState",
                "--property=ActiveState",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DownloadError(
            "ref_unit_state_unavailable",
            "The existing Ref2VA downloader state could not be read safely.",
        ) from error
    properties: dict[str, str] = {}
    for raw_line in result.stdout.splitlines():
        key, separator, value = raw_line.partition("=")
        if (
            not separator
            or key not in {"LoadState", "ActiveState"}
            or key in properties
        ):
            _fail(
                "ref_unit_state_invalid",
                "The existing Ref2VA downloader returned malformed state data.",
            )
        properties[key] = value

    load_state = properties.get("LoadState")
    state = properties.get("ActiveState")
    # A transient unit legitimately disappears across reboot.  systemctl
    # versions differ on whether an absent unit exits 0 or 4, so accept only
    # this exact absent tuple; every other non-zero or unknown result fails
    # closed rather than risking a concurrent writer.
    if (
        load_state == "not-found"
        and state == "inactive"
        and result.returncode in {0, 4}
    ):
        return "inactive"
    if result.returncode != 0 or load_state != "loaded" or state not in {
        "active",
        "activating",
        "deactivating",
        "failed",
        "inactive",
        "reloading",
    }:
        _fail(
            "ref_unit_state_invalid",
            "The existing Ref2VA downloader returned an unknown state.",
        )
    return state


def _wait_for_existing_ref_downloader() -> dict[str, object]:
    waited = False
    observations = 0
    while True:
        state = _unit_state(ACTIVE_REF_UNIT)
        observations += 1
        if state in {"inactive", "failed"}:
            return {
                "unit": ACTIVE_REF_UNIT,
                "final_state": state,
                "waited": waited,
                "observations": observations,
            }
        waited = True
        time.sleep(WAIT_SECONDS)


def _verify_exact(artifact: Artifact) -> dict[str, object]:
    path = artifact.spec().destination
    try:
        before = os.lstat(path)
    except FileNotFoundError as error:
        raise DownloadError(
            f"{artifact.role}_gate_missing",
            f"The {artifact.role.upper()} exact file is missing.",
        ) from error
    except OSError as error:
        raise DownloadError(
            f"{artifact.role}_gate_unreadable",
            f"The {artifact.role.upper()} exact file cannot be inspected.",
        ) from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        _fail(
            f"{artifact.role}_gate_unsafe",
            f"The {artifact.role.upper()} exact file is not owner-only regular data.",
        )
    if before.st_size != EXPECTED_BYTES:
        _fail(
            f"{artifact.role}_gate_bytes",
            f"The {artifact.role.upper()} exact byte gate failed.",
        )

    digest = hashlib.sha256()
    count = 0
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size != before.st_size
                or opened.st_uid != before.st_uid
                or opened.st_nlink != before.st_nlink
                or stat.S_IMODE(opened.st_mode) != 0o600
            ):
                _fail(
                    f"{artifact.role}_gate_changed",
                    f"The {artifact.role.upper()} exact file changed before hashing.",
                )
            while True:
                block = os.read(descriptor, BUFFER_BYTES)
                if not block:
                    break
                count += len(block)
                digest.update(block)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except DownloadError:
        raise
    except OSError as error:
        raise DownloadError(
            f"{artifact.role}_gate_read_failed",
            f"The {artifact.role.upper()} exact file could not be hashed.",
        ) from error

    stable_fields = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
        _fail(
            f"{artifact.role}_gate_changed",
            f"The {artifact.role.upper()} exact file changed while hashing.",
        )
    actual_sha256 = digest.hexdigest()
    if count != EXPECTED_BYTES or actual_sha256 != artifact.sha256:
        _fail(
            f"{artifact.role}_gate_digest",
            f"The {artifact.role.upper()} exact SHA-256 gate failed.",
        )
    return {
        "status": "verified",
        "path": str(path),
        "bytes": count,
        "sha256": actual_sha256,
        "stable_during_hash": True,
        "owner_only": True,
    }


def _summarize_download(report: dict[str, object]) -> dict[str, object]:
    return {
        key: report.get(key)
        for key in (
            "status",
            "destination",
            "staging",
            "expected",
            "execution_performed",
            "network_performed",
            "destination_written",
        )
    }


def execute_sequence() -> dict[str, object]:
    wait_report = _wait_for_existing_ref_downloader()

    ref_download = RangeDownloader(REF2VA.spec()).execute()
    ref_gate = _verify_exact(REF2VA)

    # This call is intentionally below the independent Ref2VA byte/SHA gate.
    fl_download = RangeDownloader(FL2VA.spec()).execute()
    fl_gate = _verify_exact(FL2VA)

    return {
        "schema_version": REPORT_SCHEMA,
        "status": "verified",
        "mode": "strictly_sequential",
        "order": ["ref2va", "ref2va_exact_gate", "fl2va", "fl2va_exact_gate"],
        "existing_ref_unit": wait_report,
        "ref2va": {
            "download": _summarize_download(ref_download),
            "exact_gate": ref_gate,
        },
        "fl2va": {
            "download": _summarize_download(fl_download),
            "exact_gate": fl_gate,
        },
    }


def dry_validation() -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "validated",
        "execution_performed": False,
        "network_performed": False,
        "filesystem_written": False,
        "mode": "strictly_sequential",
        "order": ["ref2va", "ref2va_exact_gate", "fl2va", "fl2va_exact_gate"],
        "waits_for_active_unit": ACTIVE_REF_UNIT,
        "artifacts": [_artifact_plan(REF2VA), _artifact_plan(FL2VA)],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely resume pinned Ref2VA, exact-gate it, then download FL2VA."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        report = execute_sequence() if args.execute else dry_validation()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except DownloadError as error:
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": "error",
            "error": {"code": error.code, "message": error.message},
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
