#!/usr/bin/env python3
"""Audit or explicitly resume the pinned Javawock Ollama prompt model.

The default action is local-only audit.  Network activity is possible only
when ``--execute`` is supplied.  In execute mode the pinned Ollama client talks
to the already-loopback-bound service and lets Ollama resume its own multipart
files.  Existing partials are never renamed, deleted, or recreated here.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Iterator
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_MANIFEST = REPO_ROOT / "manifests/minimax_h3/tutorial_stack.lock.json"

EXPECTED_VERSION = "0.32.11"
EXPECTED_ORIGIN = "http://127.0.0.1:11434"
EXPECTED_TAG = "vaultbox/qwen3.5-uncensored:9b"
EXPECTED_MANIFEST_DIGEST = (
    "c16b17fe1d89c3a34126e32c39165f2ab5f6568bb538a40ddee12dcc7390e693"
)
EXPECTED_BINARY_SHA256 = (
    "1dd9261372e31cd0c6b42e2b6b7df2322883be396b17ab4deb43f0f38e4fbced"
)
EXPECTED_LAYERS = {
    "616d2ebad2b415434c1a6a0f4f43f51fddd23d8a38df6c804fe56eac28cc7efb":
        6_690_889_568,
    "d0e89cdfd58c87eff55a252bce99ad0f4f5598cf67889f04bb90e22d6123ff73":
        455,
    "b507b9c2f6ca642bffcd06665ea7c91f235fd32daeefdf875a0f938db05fb315":
        13,
    "9371364b27a52acac9d87f88bd93c9db1174d8d6ec57f6888925cdc1788871ff":
        65,
}
EXPECTED_BINARY_BYTES = 39_085_520
MAX_LOCAL_RESPONSE = 2 * 1024 * 1024
MIN_FREE_MARGIN = 1024 * 1024 * 1024


class AuditError(RuntimeError):
    """A pinned invariant is absent or has drifted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise AuditError(f"missing file: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AuditError(f"unsafe file type or link count: {path}")
    if info.st_uid != os.getuid():
        raise AuditError(f"file is not owned by the current user: {path}")
    if info.st_mode & 0o002:
        raise AuditError(f"file is world writable: {path}")
    return info


def _load_json(path: Path) -> Any:
    _require_regular(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"invalid JSON: {path}: {exc}") from exc


def load_pins(home: Path) -> dict[str, Any]:
    payload = _load_json(LOCK_MANIFEST)
    artifacts = {
        item.get("id"): item for item in payload.get("host_artifacts", [])
    }
    services = {
        item.get("id"): item for item in payload.get("live_services", [])
    }

    binary = artifacts.get("ollama-0.32.11-binary")
    service = services.get("ollama-qwen35-9b")
    if not isinstance(binary, dict) or not isinstance(service, dict):
        raise AuditError("pinned Ollama binary/service entries are missing")
    expected_binary_path = ".local/opt/ollama-0.32.11/bin/ollama"
    if binary != {
        "id": "ollama-0.32.11-binary",
        "source": "ollama/ollama@v0.32.11",
        "root": "home",
        "path": expected_binary_path,
        "bytes": EXPECTED_BINARY_BYTES,
        "sha256": EXPECTED_BINARY_SHA256,
        "profiles": ["javawock"],
    }:
        raise AuditError("pinned Ollama binary entry drifted")
    if service.get("kind") != "ollama_tag":
        raise AuditError("pinned Ollama service kind drifted")
    if service.get("url") != EXPECTED_ORIGIN:
        raise AuditError("Ollama origin is not the fixed loopback origin")
    if service.get("tag") != EXPECTED_TAG:
        raise AuditError("pinned Javawock Ollama tag drifted")
    if service.get("manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        raise AuditError("pinned Javawock Ollama manifest digest drifted")

    expected_ids = {
        "ollama-qwen35-9b-model-layer": next(iter(EXPECTED_LAYERS)),
        "ollama-qwen35-9b-config-layer":
            "d0e89cdfd58c87eff55a252bce99ad0f4f5598cf67889f04bb90e22d6123ff73",
        "ollama-qwen35-9b-template-layer":
            "b507b9c2f6ca642bffcd06665ea7c91f235fd32daeefdf875a0f938db05fb315",
        "ollama-qwen35-9b-parameters-layer":
            "9371364b27a52acac9d87f88bd93c9db1174d8d6ec57f6888925cdc1788871ff",
    }
    for artifact_id, digest in expected_ids.items():
        artifact = artifacts.get(artifact_id)
        expected_path = f".local/share/ollama/models/blobs/sha256-{digest}"
        if not isinstance(artifact, dict):
            raise AuditError(f"missing pinned layer entry: {artifact_id}")
        if (
            artifact.get("source")
            != "registry.ollama.ai/vaultbox/qwen3.5-uncensored:9b"
            or artifact.get("root") != "home"
            or artifact.get("path") != expected_path
            or artifact.get("bytes") != EXPECTED_LAYERS[digest]
            or artifact.get("sha256") != digest
            or artifact.get("profiles") != ["javawock"]
        ):
            raise AuditError(f"pinned layer entry drifted: {artifact_id}")

    return {
        "binary": home / expected_binary_path,
        "models": home / ".local/share/ollama/models",
    }


def _partial_state(final_path: Path, expected_bytes: int) -> dict[str, Any]:
    prefix = f"{final_path.name}-partial"
    partial_path = final_path.with_name(prefix)
    metadata_paths = sorted(
        final_path.parent.glob(f"{prefix}-*"),
        key=lambda item: item.name,
    )

    if final_path.exists():
        info = _require_regular(final_path)
        if info.st_size != expected_bytes:
            raise AuditError(f"final layer has the wrong size: {final_path}")
        if partial_path.exists() or metadata_paths:
            raise AuditError(f"final and partial copies coexist: {final_path}")
        digest = final_path.name.removeprefix("sha256-")
        if _sha256(final_path) != digest:
            raise AuditError(f"final layer hash mismatch: {final_path}")
        return {
            "state": "verified",
            "completed_bytes": expected_bytes,
            "remaining_bytes": 0,
            "parts": 0,
        }

    if not partial_path.exists():
        if metadata_paths:
            raise AuditError(f"partial metadata exists without data: {final_path}")
        return {
            "state": "not_started",
            "completed_bytes": 0,
            "remaining_bytes": expected_bytes,
            "parts": 0,
        }

    info = _require_regular(partial_path)
    if info.st_size != expected_bytes:
        raise AuditError(f"partial layer has the wrong logical size: {partial_path}")
    if not metadata_paths:
        raise AuditError(f"partial layer has no Ollama part metadata: {partial_path}")

    parts: list[dict[str, int]] = []
    for path in metadata_paths:
        value = _load_json(path)
        if not isinstance(value, dict) or set(value) != {
            "N", "Offset", "Size", "Completed"
        }:
            raise AuditError(f"invalid Ollama partial metadata fields: {path}")
        if not all(type(value[key]) is int for key in value):
            raise AuditError(f"non-integer Ollama partial metadata: {path}")
        parts.append(value)
    parts.sort(key=lambda value: value["N"])
    if [value["N"] for value in parts] != list(range(len(parts))):
        raise AuditError(f"non-contiguous Ollama part numbers: {final_path}")

    offset = 0
    completed = 0
    for value in parts:
        if value["Offset"] != offset or value["Size"] <= 0:
            raise AuditError(f"non-contiguous Ollama part layout: {final_path}")
        if value["Completed"] < 0 or value["Completed"] > value["Size"]:
            raise AuditError(f"invalid Ollama completed count: {final_path}")
        offset += value["Size"]
        completed += value["Completed"]
    if offset != expected_bytes:
        raise AuditError(f"Ollama part sizes do not cover the layer: {final_path}")
    if info.st_blocks * 512 < completed:
        raise AuditError(f"partial allocation is smaller than completed bytes: {final_path}")
    return {
        "state": "partial",
        "completed_bytes": completed,
        "remaining_bytes": expected_bytes - completed,
        "parts": len(parts),
    }


def _read_local_json(url: str) -> Any:
    if not url.startswith(f"{EXPECTED_ORIGIN}/"):
        raise AuditError(f"refusing non-loopback audit URL: {url}")
    try:
        with urlopen(url, timeout=5) as response:
            payload = response.read(MAX_LOCAL_RESPONSE + 1)
    except (OSError, URLError) as exc:
        raise AuditError(f"Ollama loopback service is unreachable: {exc}") from exc
    if len(payload) > MAX_LOCAL_RESPONSE:
        raise AuditError("Ollama loopback response is too large")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuditError("Ollama loopback response is not JSON") from exc


def _server_status() -> dict[str, Any]:
    version = _read_local_json(f"{EXPECTED_ORIGIN}/api/version")
    if not isinstance(version, dict) or version.get("version") != EXPECTED_VERSION:
        raise AuditError(f"Ollama server is not pinned v{EXPECTED_VERSION}")
    tags = _read_local_json(f"{EXPECTED_ORIGIN}/api/tags")
    models = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(models, list):
        raise AuditError("Ollama tags response is invalid")
    named = [
        item for item in models
        if isinstance(item, dict) and item.get("name") == EXPECTED_TAG
    ]
    if len(named) > 1:
        raise AuditError("duplicate pinned Ollama tags were returned")
    if named:
        # Ollama 0.32.11's /api/tags contract returns the manifest digest as
        # bare 64-hex.  This is deliberately distinct from layer references
        # and does not weaken the on-disk manifest SHA-256 check below.
        if named[0].get("digest") != EXPECTED_MANIFEST_DIGEST:
            raise AuditError("the Javawock Ollama tag resolves to an unpinned digest")
        return {"version": EXPECTED_VERSION, "tag_verified": True}
    return {"version": EXPECTED_VERSION, "tag_verified": False}


def audit(home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    pins = load_pins(home)
    binary = pins["binary"]
    info = _require_regular(binary)
    if info.st_size != EXPECTED_BINARY_BYTES or _sha256(binary) != EXPECTED_BINARY_SHA256:
        raise AuditError("the pinned Ollama client binary failed size/SHA-256")

    models_root = pins["models"]
    blobs_root = models_root / "blobs"
    states: dict[str, dict[str, Any]] = {}
    for digest, size in EXPECTED_LAYERS.items():
        states[digest] = _partial_state(blobs_root / f"sha256-{digest}", size)
    server = _server_status()

    manifest_path = (
        models_root / "manifests/registry.ollama.ai/vaultbox/"
        "qwen3.5-uncensored/9b"
    )
    all_verified = all(item["state"] == "verified" for item in states.values())
    if server["tag_verified"]:
        if not all_verified:
            raise AuditError("the pinned tag exists but one or more layers are incomplete")
        manifest_info = _require_regular(manifest_path)
        if manifest_info.st_size > MAX_LOCAL_RESPONSE:
            raise AuditError("the local Ollama manifest is unexpectedly large")
        if _sha256(manifest_path) != EXPECTED_MANIFEST_DIGEST:
            raise AuditError("the local Ollama manifest digest is not pinned")
    elif manifest_path.exists():
        raise AuditError("an unregistered/stale Javawock tag manifest exists")

    completed = sum(item["completed_bytes"] for item in states.values())
    remaining = sum(item["remaining_bytes"] for item in states.values())
    return {
        "status": "complete" if server["tag_verified"] and all_verified else "incomplete",
        "network_started": False,
        "tag": EXPECTED_TAG,
        "manifest_digest": EXPECTED_MANIFEST_DIGEST,
        "ollama_version": server["version"],
        "tag_verified": server["tag_verified"],
        "completed_bytes": completed,
        "remaining_bytes": remaining,
        "total_layer_bytes": sum(EXPECTED_LAYERS.values()),
        "layers": states,
    }


def _another_pull_exists() -> bool:
    own_pid = os.getpid()
    proc_root = Path("/proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        argv = [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]
        if len(argv) >= 3 and Path(argv[0]).name == "ollama" and argv[1:3] == [
            "pull", EXPECTED_TAG
        ]:
            return True
    return False


@contextlib.contextmanager
def _exclusive_pull_lock(models_root: Path) -> Iterator[None]:
    path = models_root.parent / ".castle-qwen35-pull.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AuditError("another pinned Ollama resume helper owns the lock") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def execute(home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    pins = load_pins(home)
    with _exclusive_pull_lock(pins["models"]):
        if _another_pull_exists():
            raise AuditError("another exact Ollama pull is already running")
        before = audit(home)
        if before["status"] == "complete":
            return before
        free = os.statvfs(pins["models"]).f_bavail * os.statvfs(
            pins["models"]
        ).f_frsize
        if free < before["remaining_bytes"] + MIN_FREE_MARGIN:
            raise AuditError("insufficient free space for the remaining layer bytes")

        environment = os.environ.copy()
        environment["OLLAMA_HOST"] = "127.0.0.1:11434"
        command = [str(pins["binary"]), "pull", EXPECTED_TAG]
        completed = subprocess.run(command, env=environment, check=False)
        if completed.returncode != 0:
            raise AuditError(
                "pinned Ollama pull stopped; native partials were preserved for resume"
            )
        after = audit(home)
        if after["status"] != "complete":
            raise AuditError("Ollama pull returned success but pinned verification failed")
        return after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit or explicitly resume the pinned Javawock Ollama tag."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="allow the exact pinned Ollama pull; omitted means local-only audit",
    )
    args = parser.parse_args(argv)
    try:
        result = execute() if args.execute else audit()
    except AuditError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "complete" or args.execute else 3


if __name__ == "__main__":
    sys.exit(main())
