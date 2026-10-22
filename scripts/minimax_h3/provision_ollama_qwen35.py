#!/usr/bin/env python3
"""Provision the pinned loopback Ollama runtime used by Javawock workflows.

The default action is a read-only plan.  ``--execute`` installs the exact
Ollama 0.32.11 archive and can install the checked-in user units.  The 9B model
is never pulled unless ``--pull-model --accept-large-downloads`` is supplied;
pulling also requires the explicit ``--start-service`` flag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/tutorial_stack.lock.json"
OLLAMA_VERSION = "0.32.11"
ARCHIVE_URL = (
    "https://github.com/ollama/ollama/releases/download/"
    "v0.32.11/ollama-linux-amd64.tar.zst"
)
ARCHIVE_BYTES = 1_421_984_062
ARCHIVE_SHA256 = "873258110663ff4e72bed8549b9e86589e1c07d99646cac257b71a4a6a72dcc7"
BINARY_BYTES = 39_085_520
BINARY_SHA256 = "1dd9261372e31cd0c6b42e2b6b7df2322883be396b17ab4deb43f0f38e4fbced"
MODEL_TAG = "vaultbox/qwen3.5-uncensored:9b"
MODEL_MANIFEST_SHA256 = (
    "c16b17fe1d89c3a34126e32c39165f2ab5f6568bb538a40ddee12dcc7390e693"
)
REPORT_SCHEMA = "castle.minimax-h3.ollama-provision.v1"
BUFFER_BYTES = 8 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UNIT_NAMES = ("web-comfyui-ollama.service",)


class ProvisionError(RuntimeError):
    """A pinned identity or safe-install invariant failed."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProvisionError(f"cannot read tutorial manifest: {path}") from error


def validate_manifest(path: Path) -> None:
    document = _load_json(path)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ProvisionError("unsupported tutorial manifest schema")
    artifacts = {
        item.get("id"): item
        for item in document.get("host_artifacts", [])
        if isinstance(item, dict)
    }
    binary = artifacts.get("ollama-0.32.11-binary")
    archive = artifacts.get("ollama-0.32.11-linux-amd64-archive")
    expected_binary = {
        "source": "ollama/ollama@v0.32.11",
        "path": ".local/opt/ollama-0.32.11/bin/ollama",
        "bytes": BINARY_BYTES,
        "sha256": BINARY_SHA256,
    }
    expected_archive = {
        "source": "ollama/ollama@v0.32.11",
        "path": ".cache/ollama-install/ollama-linux-amd64-v0.32.11.tar.zst",
        "bytes": ARCHIVE_BYTES,
        "sha256": ARCHIVE_SHA256,
    }
    for label, record, expected in (
        ("binary", binary, expected_binary),
        ("archive", archive, expected_archive),
    ):
        if not isinstance(record, dict):
            raise ProvisionError(f"manifest is missing pinned Ollama {label}")
        if any(record.get(key) != value for key, value in expected.items()):
            raise ProvisionError(f"manifest Ollama {label} pin drifted")
    services = {
        item.get("id"): item
        for item in document.get("live_services", [])
        if isinstance(item, dict)
    }
    service = services.get("ollama-qwen35-9b")
    if (
        not isinstance(service, dict)
        or service.get("url") != "http://127.0.0.1:11434"
        or service.get("tag") != MODEL_TAG
        or service.get("manifest_digest") != MODEL_MANIFEST_SHA256
    ):
        raise ProvisionError("manifest Ollama model/service pin drifted")


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(BUFFER_BYTES), b""):
                count += len(block)
                digest.update(block)
    except OSError as error:
        raise ProvisionError(f"cannot read pinned file: {path}") from error
    return count, digest.hexdigest()


def _regular_owner_file(path: Path) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as error:
        raise ProvisionError(f"cannot inspect pinned file: {path}") from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        raise ProvisionError(f"unsafe pinned file: {path}")
    return info


def _verify_file(path: Path, *, size: int, sha256: str) -> None:
    if SHA256_RE.fullmatch(sha256) is None:
        raise ProvisionError("invalid internal SHA-256 pin")
    info = _regular_owner_file(path)
    if info.st_size != size:
        raise ProvisionError(f"pinned byte count mismatch: {path}")
    actual_size, actual_sha256 = _digest(path)
    if actual_size != size or actual_sha256 != sha256:
        raise ProvisionError(f"pinned SHA-256 mismatch: {path}")


def _ensure_owner_directory(path: Path, *, mode: int = 0o755) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, mode=mode)
                info = os.lstat(current)
            except OSError as error:
                raise ProvisionError(f"cannot create directory: {current}") from error
        except OSError as error:
            raise ProvisionError(f"cannot inspect directory: {current}") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ProvisionError(f"unsafe directory path: {current}")
    info = os.lstat(path)
    if info.st_uid != os.geteuid():
        raise ProvisionError(f"directory is not owned by current user: {path}")


def ensure_archive(archive: Path) -> str:
    if archive.exists() or archive.is_symlink():
        _verify_file(archive, size=ARCHIVE_BYTES, sha256=ARCHIVE_SHA256)
        return "reused"

    _ensure_owner_directory(archive.parent)
    partial = archive.with_name(f".{archive.name}.partial")
    if partial.exists() or partial.is_symlink():
        _regular_owner_file(partial)
    curl = shutil.which("curl")
    if not curl:
        raise ProvisionError("curl is required to download the pinned Ollama archive")
    try:
        completed = subprocess.run(
            [
                curl,
                "--fail",
                "--location",
                "--retry",
                "5",
                "--retry-all-errors",
                "--continue-at",
                "-",
                "--output",
                str(partial),
                ARCHIVE_URL,
            ],
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as error:
        raise ProvisionError("could not start pinned Ollama archive download") from error
    if completed.returncode != 0:
        raise ProvisionError("pinned Ollama archive download failed; partial retained")
    _verify_file(partial, size=ARCHIVE_BYTES, sha256=ARCHIVE_SHA256)
    try:
        os.replace(partial, archive)
    except OSError as error:
        raise ProvisionError("could not publish verified Ollama archive") from error
    return "downloaded"


def install_runtime(archive: Path, target: Path) -> str:
    binary = target / "bin/ollama"
    if target.exists() or target.is_symlink():
        info = os.lstat(target)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ProvisionError(f"unsafe Ollama install target: {target}")
        _verify_file(binary, size=BINARY_BYTES, sha256=BINARY_SHA256)
        return "reused"

    _ensure_owner_directory(target.parent)
    temporary = Path(tempfile.mkdtemp(prefix=f".ollama-{OLLAMA_VERSION}-", dir=target.parent))
    try:
        tar = shutil.which("tar")
        if not tar:
            raise ProvisionError("tar with zstd support is required for Ollama")
        listed = subprocess.run(
            [tar, "--zstd", "-tf", str(archive)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if listed.returncode != 0:
            raise ProvisionError("pinned Ollama archive cannot be listed")
        for raw_name in listed.stdout.splitlines():
            name = PurePosixPath(raw_name)
            if name.is_absolute() or ".." in name.parts:
                raise ProvisionError("pinned Ollama archive has an unsafe member")
        extracted = subprocess.run(
            [
                tar,
                "--zstd",
                "--extract",
                "--no-same-owner",
                "--file",
                str(archive),
                "--directory",
                str(temporary),
            ],
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if extracted.returncode != 0:
            raise ProvisionError("pinned Ollama archive extraction failed")
        _verify_file(
            temporary / "bin/ollama",
            size=BINARY_BYTES,
            sha256=BINARY_SHA256,
        )
        os.rename(temporary, target)
        return "installed"
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _install_unit(source: Path, destination: Path) -> str:
    data = source.read_bytes()
    if destination.exists() or destination.is_symlink():
        _regular_owner_file(destination)
        if destination.read_bytes() != data:
            raise ProvisionError(f"existing user unit differs: {destination.name}")
        return "reused"
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return "installed"


def install_user_units(home: Path) -> dict[str, str]:
    unit_dir = home / ".config/systemd/user"
    _ensure_owner_directory(unit_dir)
    results: dict[str, str] = {}
    for name in UNIT_NAMES:
        source = REPO_ROOT / "scripts/systemd" / name
        if not source.is_file():
            raise ProvisionError(f"checked-in user unit is missing: {name}")
        results[name] = _install_unit(source, unit_dir / name)
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise ProvisionError("systemctl is required to install Ollama user units")
    completed = subprocess.run(
        [systemctl, "--user", "daemon-reload"],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ProvisionError("systemd user daemon-reload failed")
    return results


def _systemctl(*arguments: str) -> None:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise ProvisionError("systemctl is required for the Ollama user service")
    completed = subprocess.run(
        [systemctl, "--user", *arguments],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ProvisionError("Ollama user-service operation failed")


def _pull_model() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("resume_ollama_qwen35.py")),
            "--execute",
        ],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ProvisionError("pinned Ollama model pull did not complete")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--install-user-service", action="store_true")
    parser.add_argument("--start-service", action="store_true")
    parser.add_argument("--pull-model", action="store_true")
    parser.add_argument("--accept-large-downloads", action="store_true")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        validate_manifest(args.manifest.expanduser())
        home = args.home.expanduser()
        if not home.is_absolute() or "\x00" in os.fspath(home):
            raise ProvisionError("home must be an absolute path")
        if (args.install_user_service or args.start_service or args.pull_model) and not args.execute:
            raise ProvisionError("service installation/start/model pull requires --execute")
        if args.install_user_service and home.resolve() != Path.home().resolve():
            raise ProvisionError("user-service installation requires the current user home")
        if args.start_service and not args.install_user_service:
            raise ProvisionError("--start-service requires --install-user-service")
        if args.pull_model and not args.start_service:
            raise ProvisionError("--pull-model requires --start-service")
        if args.pull_model and not args.accept_large_downloads:
            raise ProvisionError("the 9B model pull requires --accept-large-downloads")

        archive = home / ".cache/ollama-install/ollama-linux-amd64-v0.32.11.tar.zst"
        target = home / ".local/opt/ollama-0.32.11"
        report: dict[str, object] = {
            "schema_version": REPORT_SCHEMA,
            "status": "planned",
            "execution_performed": False,
            "network_performed": False,
            "version": OLLAMA_VERSION,
            "archive": {
                "path": str(archive),
                "bytes": ARCHIVE_BYTES,
                "sha256": ARCHIVE_SHA256,
            },
            "binary": {
                "path": str(target / "bin/ollama"),
                "bytes": BINARY_BYTES,
                "sha256": BINARY_SHA256,
            },
            "install_user_service": bool(args.install_user_service),
            "start_service": bool(args.start_service),
            "pull_model": bool(args.pull_model),
            "model_tag": MODEL_TAG,
            "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        }
        if not args.execute:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0

        archive_status = ensure_archive(archive)
        runtime_status = install_runtime(archive, target)
        unit_status: dict[str, str] = {}
        if args.install_user_service:
            unit_status = install_user_units(home)
        if args.start_service:
            _systemctl("enable", "--now", UNIT_NAMES[0])
        if args.pull_model:
            _pull_model()
        report.update(
            status="verified",
            execution_performed=True,
            network_performed=(archive_status == "downloaded" or args.pull_model),
            archive_status=archive_status,
            runtime_status=runtime_status,
            units=unit_status,
            model_status="pulled-and-verified" if args.pull_model else "not-requested",
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except (ProvisionError, OSError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA,
                    "status": "error",
                    "error": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
