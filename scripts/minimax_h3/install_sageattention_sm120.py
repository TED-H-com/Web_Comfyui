#!/usr/bin/env python3
"""Build, install, and validate the pinned SageAttention 2.2.0 SM120 wheel.

The build is intentionally isolated from the ComfyUI Python environment:
micromamba provisions a user-local NVIDIA CUDA 12.8.61/GCC 13 toolchain, while
the resulting wheel is installed into the existing ComfyUI venv with
``--no-deps``.  Torch and Triton are verified before and after installation and
are never installed or upgraded by this program.

When the tutorial-stack lock already matches the wheel and all three installed
native extensions, the default path is idempotent: no network access, build, or
pip install is performed.  The import and small BF16 SM120 correctness checks
still run unless ``--skip-gpu-test`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/tutorial_stack.lock.json"
DEFAULT_COMFY_HOME = Path(os.environ.get("COMFY_HOME", "~/comfyui")).expanduser()
VALIDATOR = Path(__file__).with_name("validate_sageattention_sm120.py")

SOURCE_URL = "https://github.com/thu-ml/SageAttention.git"
SOURCE_REVISION = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"
SOURCE_LOCK = f"thu-ml/SageAttention@{SOURCE_REVISION}"
PACKAGE_VERSION = "2.2.0"
CUDA_NVCC_VERSION = "12.8.61"
GCC_VERSION = "13.4.0"
TARGET_ARCH = "12.0"

MICROMAMBA_VERSION = "2.9.0"
MICROMAMBA_URL = (
    f"https://micro.mamba.pm/api/micromamba/linux-64/{MICROMAMBA_VERSION}"
)
MICROMAMBA_ARCHIVE_BYTES = 6_988_090
MICROMAMBA_ARCHIVE_SHA256 = (
    "8761c382127e6363bd9e0a2451aa3ef90d071a79133f736e2f759a3bf13040dd"
)
MICROMAMBA_BINARY_BYTES = 18_292_808
MICROMAMBA_BINARY_SHA256 = (
    "366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3"
)

BUILD_PACKAGES = {
    "ninja": "1.13.0",
    "packaging": "26.2",
    "setuptools": "70.2.0",
    "wheel": "0.47.0",
}
NVIDIA_HEADER_PACKAGES = {
    "nvidia-cublas-cu12": "12.8.3.14",
    "nvidia-cudnn-cu12": "9.7.1.26",
    "nvidia-cusolver-cu12": "11.7.2.55",
    "nvidia-cusparse-cu12": "12.5.7.53",
}
EXTENSION_IDS = {
    "sageattention-qattn-sm80-extension": "sageattention._qattn_sm80",
    "sageattention-qattn-sm89-extension": "sageattention._qattn_sm89",
    "sageattention-fused-extension": "sageattention._fused",
}


class InstallError(RuntimeError):
    """A reproducibility or validation requirement was not met."""


@dataclass(frozen=True)
class LockedFile:
    artifact_id: str
    path: Path
    bytes: int
    sha256: str


@dataclass(frozen=True)
class LockConfig:
    manifest: Path
    python_version: str
    torch_version: str
    triton_version: str
    wheel: LockedFile
    extensions: dict[str, LockedFile]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-home", type=Path, default=DEFAULT_COMFY_HOME)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the locked installation without changing any files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report whether reuse, install, or build would be selected.",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Explicitly rebuild even when the locked wheel is already installed.",
    )
    parser.add_argument(
        "--skip-gpu-test",
        action="store_true",
        help="Run native imports but skip the SM120 BF16 kernel comparison.",
    )
    parser.add_argument(
        "--result",
        type=Path,
        help="Installation result JSON (default: COMFY_HOME/.build/sageattention-sm120-repro-result.json).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.check and args.force_rebuild:
        parser.error("--check and --force-rebuild are mutually exclusive")
    return args


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"path": str(path), "present": False}
    return {
        "path": str(path),
        "present": True,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def matches_lock(path: Path, locked: LockedFile) -> bool:
    if not path.is_file() or path.stat().st_size != locked.bytes:
        return False
    return sha256_file(path) == locked.sha256


def safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise InstallError(f"locked path escapes its root: {relative}") from exc
    return candidate


def locked_file(record: dict[str, Any], root: Path) -> LockedFile:
    digest = str(record.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InstallError(f"invalid SHA-256 for {record.get('id')}")
    size = record.get("bytes")
    if not isinstance(size, int) or size <= 0:
        raise InstallError(f"invalid byte count for {record.get('id')}")
    return LockedFile(
        artifact_id=str(record["id"]),
        path=safe_path(root, str(record["path"])),
        bytes=size,
        sha256=digest,
    )


def load_lock(manifest_path: Path, comfy_home: Path) -> LockConfig:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read tutorial-stack lock: {manifest_path}") from exc
    if manifest.get("schema_version") != 1:
        raise InstallError("unsupported tutorial-stack lock schema")
    runtime = manifest.get("runtime", {})
    if runtime.get("gpu_arch") != "sm_120" or runtime.get("cuda") != "12.8":
        raise InstallError("tutorial-stack lock is not the CUDA 12.8/SM120 profile")

    packages = {
        str(item.get("distribution")): str(item.get("version"))
        for item in manifest.get("python_packages", [])
    }
    try:
        torch_version = packages["torch"]
        triton_version = packages["triton"]
    except KeyError as exc:
        raise InstallError("tutorial-stack lock does not pin Torch and Triton") from exc
    if packages.get("sageattention") != PACKAGE_VERSION:
        raise InstallError("tutorial-stack lock does not pin sageattention==2.2.0")

    artifacts = {
        str(item.get("id")): item for item in manifest.get("build_artifacts", [])
    }
    try:
        wheel_record = artifacts["sageattention-2.2.0-sm120-wheel"]
    except KeyError as exc:
        raise InstallError("tutorial-stack lock has no SageAttention SM120 wheel") from exc
    if wheel_record.get("source") != SOURCE_LOCK:
        raise InstallError("SageAttention wheel source revision does not match installer pin")
    wheel = locked_file(wheel_record, comfy_home)

    extensions: dict[str, LockedFile] = {}
    for artifact_id, module_name in EXTENSION_IDS.items():
        try:
            extensions[module_name] = locked_file(artifacts[artifact_id], comfy_home)
        except KeyError as exc:
            raise InstallError(f"tutorial-stack lock has no {artifact_id}") from exc

    return LockConfig(
        manifest=manifest_path.resolve(),
        python_version=str(runtime.get("python")),
        torch_version=torch_version,
        triton_version=triton_version,
        wheel=wheel,
        extensions=extensions,
    )


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise InstallError(f"command failed ({' '.join(command)}): {detail[-4000:]}")
    return completed


def distribution_probe(python: Path, names: list[str]) -> dict[str, Any]:
    program = """
import importlib.metadata as metadata
import json
import platform
import site
import sys
names = json.loads(sys.argv[1])
versions = {}
for name in names:
    try:
        versions[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        versions[name] = None
print(json.dumps({
    "python": platform.python_version(),
    "site_packages": site.getsitepackages(),
    "versions": versions,
}, sort_keys=True))
"""
    completed = run([str(python), "-c", program, json.dumps(names)])
    return json.loads(completed.stdout)


def verify_runtime(python: Path, lock: LockConfig) -> dict[str, Any]:
    if not python.is_file():
        raise InstallError(f"ComfyUI venv Python is missing: {python}")
    names = ["torch", "triton", "sageattention", *BUILD_PACKAGES, *NVIDIA_HEADER_PACKAGES]
    probe = distribution_probe(python, names)
    if probe["python"] != lock.python_version:
        raise InstallError(
            f"Python drift: expected {lock.python_version}, found {probe['python']}"
        )
    versions = probe["versions"]
    for name, expected in (
        ("torch", lock.torch_version),
        ("triton", lock.triton_version),
    ):
        if versions.get(name) != expected:
            raise InstallError(
                f"{name} drift: expected {expected}, found {versions.get(name)}"
            )
    return probe


def clean_runtime_env() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "CC",
        "CPATH",
        "CUDA_HOME",
        "CUDAHOSTCXX",
        "CXX",
        "EXT_PARALLEL",
        "LD_LIBRARY_PATH",
        "MAX_JOBS",
        "NVCC_APPEND_FLAGS",
        "TORCH_CUDA_ARCH_LIST",
    ):
        environment.pop(name, None)
    environment["PATH"] = "/usr/bin:/bin"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def validate_install(python: Path, *, gpu: bool) -> dict[str, Any]:
    command = [str(python), str(VALIDATOR), "--json"]
    if not gpu:
        command.append("--imports-only")
    completed = run(command, env=clean_runtime_env())
    report = json.loads(completed.stdout)
    if report.get("status") != "PASS":
        raise InstallError(f"SageAttention validator failed: {report}")
    return report


def locked_install_state(
    python: Path, lock: LockConfig, runtime: dict[str, Any]
) -> tuple[bool, dict[str, Any] | None]:
    if runtime["versions"].get("sageattention") != PACKAGE_VERSION:
        return False, None
    if not all(matches_lock(record.path, record) for record in lock.extensions.values()):
        return False, None
    try:
        imports = validate_install(python, gpu=False)
    except InstallError:
        return False, None
    reported_paths = imports.get("extensions", {})
    for module_name, record in lock.extensions.items():
        if Path(str(reported_paths.get(module_name, ""))).resolve() != record.path:
            return False, None
    return True, imports


def select_action(*, wheel_locked: bool, install_locked: bool, force: bool) -> str:
    if force:
        return "build"
    if wheel_locked and install_locked:
        return "reuse"
    if wheel_locked:
        return "install"
    return "build"


def download_file(url: str, destination: Path, *, size: int, digest: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "web-comfyui-sageattention/1"})
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if temporary.stat().st_size != size or sha256_file(temporary) != digest:
            raise InstallError(f"download identity mismatch: {url}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_micromamba(comfy_home: Path) -> Path:
    tool_dir = comfy_home / ".tools/micromamba"
    binary = tool_dir / "micromamba"
    if binary.exists():
        if (
            binary.stat().st_size != MICROMAMBA_BINARY_BYTES
            or sha256_file(binary) != MICROMAMBA_BINARY_SHA256
        ):
            raise InstallError(f"unrecognized micromamba binary: {binary}")
        version = run([str(binary), "--version"]).stdout.strip()
        if version != MICROMAMBA_VERSION:
            raise InstallError(f"micromamba version drift: {version}")
        return binary

    archive = tool_dir / f"micromamba-{MICROMAMBA_VERSION}-linux-64.tar.bz2"
    if archive.exists() and (
        archive.stat().st_size != MICROMAMBA_ARCHIVE_BYTES
        or sha256_file(archive) != MICROMAMBA_ARCHIVE_SHA256
    ):
        raise InstallError(f"unrecognized micromamba archive: {archive}")
    if not archive.exists():
        download_file(
            MICROMAMBA_URL,
            archive,
            size=MICROMAMBA_ARCHIVE_BYTES,
            digest=MICROMAMBA_ARCHIVE_SHA256,
        )

    temporary = binary.with_name(f".{binary.name}.{os.getpid()}.part")
    try:
        with tarfile.open(archive, "r:bz2") as package:
            member = package.getmember("bin/micromamba")
            if not member.isfile():
                raise InstallError("micromamba archive member is not a regular file")
            source = package.extractfile(member)
            if source is None:
                raise InstallError("cannot extract micromamba binary")
            with source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        if (
            temporary.stat().st_size != MICROMAMBA_BINARY_BYTES
            or sha256_file(temporary) != MICROMAMBA_BINARY_SHA256
        ):
            raise InstallError("extracted micromamba identity mismatch")
        temporary.chmod(0o755)
        os.replace(temporary, binary)
    finally:
        temporary.unlink(missing_ok=True)
    return binary


def toolchain_report(prefix: Path) -> dict[str, str] | None:
    nvcc = prefix / "bin/nvcc"
    compiler = prefix / "bin/x86_64-conda-linux-gnu-g++"
    if not nvcc.is_file() or not compiler.is_file():
        return None
    nvcc_output = run([str(nvcc), "--version"], check=False)
    compiler_output = run([str(compiler), "--version"], check=False)
    if nvcc_output.returncode or compiler_output.returncode:
        return None
    nvcc_match = re.search(r"V(\d+\.\d+\.\d+)", nvcc_output.stdout)
    gcc_match = re.search(r"\b(\d+\.\d+\.\d+)\b", compiler_output.stdout)
    if not nvcc_match or not gcc_match:
        return None
    if nvcc_match.group(1) != CUDA_NVCC_VERSION or gcc_match.group(1) != GCC_VERSION:
        return None
    if not list((prefix / "conda-meta").glob(f"cuda-nvcc-{CUDA_NVCC_VERSION}-*.json")):
        return None
    if not list((prefix / "conda-meta").glob(f"gcc_linux-64-{GCC_VERSION}-*.json")):
        return None
    return {
        "prefix": str(prefix),
        "nvcc": nvcc_match.group(1),
        "gcc": gcc_match.group(1),
    }


def ensure_toolchain(comfy_home: Path, micromamba: Path) -> dict[str, str]:
    prefix = comfy_home / ".cuda-12.8"
    existing = toolchain_report(prefix)
    if existing:
        return existing
    if prefix.exists() and any(prefix.iterdir()):
        raise InstallError(f"existing CUDA toolchain prefix is not the pinned build: {prefix}")

    environment = dict(os.environ)
    environment["MAMBA_ROOT_PREFIX"] = str(comfy_home / ".tools/micromamba/root")
    run(
        [
            str(micromamba),
            "create",
            "-y",
            "--override-channels",
            "-c",
            "nvidia",
            "-c",
            "conda-forge",
            "-p",
            str(prefix),
            f"cuda-nvcc={CUDA_NVCC_VERSION}",
            f"gcc_linux-64={GCC_VERSION}",
            f"gxx_linux-64={GCC_VERSION}",
        ],
        env=environment,
    )
    report = toolchain_report(prefix)
    if not report:
        raise InstallError("micromamba completed but the pinned toolchain failed validation")
    return report


def normalized_git_url(value: str) -> str:
    return value.rstrip("/").removesuffix(".git")


def ensure_source(comfy_home: Path) -> Path:
    source = comfy_home / ".build/SageAttention"
    if not source.exists():
        source.mkdir(parents=True)
        run(["git", "init"], cwd=source)
        run(["git", "remote", "add", "origin", SOURCE_URL], cwd=source)
    if not (source / ".git").exists():
        raise InstallError(f"SageAttention source is not a Git checkout: {source}")
    origin = run(["git", "remote", "get-url", "origin"], cwd=source).stdout.strip()
    if normalized_git_url(origin) != normalized_git_url(SOURCE_URL):
        raise InstallError(f"unexpected SageAttention origin: {origin}")
    head = run(["git", "rev-parse", "--verify", "HEAD"], cwd=source, check=False)
    if head.returncode == 0:
        if run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            cwd=source,
            check=False,
        ).returncode:
            raise InstallError("SageAttention checkout has tracked local changes")
        if run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=source,
            check=False,
        ).returncode:
            raise InstallError("SageAttention checkout has staged local changes")
    if head.returncode or head.stdout.strip() != SOURCE_REVISION:
        run(["git", "fetch", "--depth", "1", "origin", SOURCE_REVISION], cwd=source)
        run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=source)
    final_head = run(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()
    if final_head != SOURCE_REVISION:
        raise InstallError(f"SageAttention source pin failed: {final_head}")
    return source


def site_packages_path(runtime: dict[str, Any], comfy_home: Path) -> Path:
    candidates = [Path(value).resolve() for value in runtime.get("site_packages", [])]
    expected_root = (comfy_home / ".venv").resolve()
    for candidate in candidates:
        try:
            candidate.relative_to(expected_root)
        except ValueError:
            continue
        if candidate.is_dir():
            return candidate
    raise InstallError("cannot locate ComfyUI venv site-packages")


def build_environment(
    *, comfy_home: Path, runtime: dict[str, Any], toolchain_prefix: Path
) -> tuple[dict[str, str], list[str]]:
    versions = runtime["versions"]
    for name, expected in {**BUILD_PACKAGES, **NVIDIA_HEADER_PACKAGES}.items():
        if versions.get(name) != expected:
            raise InstallError(
                f"build dependency drift for {name}: expected {expected}, found {versions.get(name)}"
            )

    site_packages = site_packages_path(runtime, comfy_home)
    include_dirs = [
        toolchain_prefix / "targets/x86_64-linux/include",
        site_packages / "nvidia/cusparse/include",
        site_packages / "nvidia/cublas/include",
        site_packages / "nvidia/cusolver/include",
        site_packages / "nvidia/cudnn/include",
    ]
    required_headers = [
        include_dirs[0] / "cuda_fp16.h",
        include_dirs[0] / "cuda_bf16.h",
        include_dirs[1] / "cusparse.h",
        include_dirs[2] / "cublas_v2.h",
        include_dirs[3] / "cusolverDn.h",
        include_dirs[4] / "cudnn.h",
    ]
    missing = [str(path) for path in required_headers if not path.is_file()]
    if missing:
        raise InstallError(f"required CUDA headers are missing: {missing}")

    compiler = toolchain_prefix / "bin/x86_64-conda-linux-gnu-c++"
    environment = dict(os.environ)
    for name in ("CFLAGS", "CPPFLAGS", "LDFLAGS", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    environment.update(
        {
            "CUDA_HOME": str(toolchain_prefix),
            "PATH": f"{toolchain_prefix / 'bin'}:{comfy_home / '.venv/bin'}:/usr/bin:/bin",
            "CC": str(toolchain_prefix / "bin/x86_64-conda-linux-gnu-cc"),
            "CXX": str(compiler),
            "CUDAHOSTCXX": str(compiler),
            "TORCH_CUDA_ARCH_LIST": TARGET_ARCH,
            "EXT_PARALLEL": "1",
            "MAX_JOBS": "1",
            "NVCC_APPEND_FLAGS": "--threads=1",
            "CPATH": ":".join(str(path) for path in include_dirs),
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment, [str(path) for path in include_dirs]


def inspect_wheel(path: Path) -> dict[str, Any]:
    identity = file_identity(path)
    if not identity.get("present"):
        raise InstallError(f"wheel does not exist: {path}")
    extension_fingerprints: dict[str, dict[str, object]] = {}
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            metadata_entries = [
                name
                for name in names
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_entries) != 1:
                raise InstallError(
                    f"wheel has {len(metadata_entries)} distribution metadata entries"
                )
            metadata = archive.read(metadata_entries[0]).decode("utf-8", errors="strict")
            if "\nName: sageattention\n" not in f"\n{metadata}" or (
                f"\nVersion: {PACKAGE_VERSION}\n" not in f"\n{metadata}"
            ):
                raise InstallError("wheel metadata is not sageattention==2.2.0")
            for module_name in EXTENSION_IDS.values():
                leaf = module_name.rsplit(".", 1)[-1]
                matches = [
                    name
                    for name in names
                    if name.startswith(f"sageattention/{leaf}.") and name.endswith(".so")
                ]
                if len(matches) != 1:
                    raise InstallError(f"wheel has {len(matches)} entries for {module_name}")
                data = archive.read(matches[0])
                if b"-arch sm_120" not in data or b".nv_fatbin" not in data:
                    raise InstallError(f"wheel extension lacks SM120 fatbin metadata: {module_name}")
                extension_fingerprints[module_name] = {
                    "entry": matches[0],
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
    except BadZipFile as exc:
        raise InstallError(f"invalid wheel archive: {path}") from exc
    return {**identity, "extensions": extension_fingerprints}


def build_wheel(
    *, comfy_home: Path, source: Path, environment: dict[str, str]
) -> tuple[Path, Path]:
    python = comfy_home / ".venv/bin/python"
    log_path = source / "build-sm120-repro.log"
    temporary_dist = Path(tempfile.mkdtemp(prefix="sageattention-dist-", dir=source))
    command = [
        str(python),
        "setup.py",
        "bdist_wheel",
        "--dist-dir",
        str(temporary_dist),
        "-v",
    ]
    with log_path.open("w", encoding="utf-8") as build_log:
        completed = subprocess.run(
            command,
            cwd=source,
            env=environment,
            text=True,
            stdout=build_log,
            stderr=subprocess.STDOUT,
        )
    if completed.returncode:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        raise InstallError(f"SageAttention wheel build failed; log={log_path}\n{tail}")
    wheels = list(temporary_dist.glob("sageattention-2.2.0-*.whl"))
    if len(wheels) != 1:
        raise InstallError(f"expected one SageAttention wheel, found {len(wheels)}")
    inspect_wheel(wheels[0])
    return wheels[0], log_path


def promote_wheel(candidate: Path, destination: Path) -> dict[str, str] | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    quarantined: dict[str, str] | None = None
    if destination.exists():
        old_hash = sha256_file(destination)
        quarantine = destination.parent / "quarantine" / old_hash[:16] / destination.name
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if quarantine.exists():
            sequence = 1
            while quarantine.with_name(f"{quarantine.stem}-{sequence}{quarantine.suffix}").exists():
                sequence += 1
            quarantine = quarantine.with_name(
                f"{quarantine.stem}-{sequence}{quarantine.suffix}"
            )
        os.replace(destination, quarantine)
        quarantined = {"from": str(destination), "to": str(quarantine)}
    os.replace(candidate, destination)
    return quarantined


def install_wheel(python: Path, wheel: Path) -> None:
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-cache-dir",
            "--force-reinstall",
            str(wheel),
        ],
        env=clean_runtime_env(),
    )


def verify_installed_against_wheel(
    validation: dict[str, Any], wheel_report: dict[str, Any]
) -> dict[str, dict[str, object]]:
    report: dict[str, dict[str, object]] = {}
    for module_name, expected in wheel_report["extensions"].items():
        path = Path(validation["extensions"][module_name]).resolve()
        actual = file_identity(path)
        if (
            actual.get("bytes") != expected["bytes"]
            or actual.get("sha256") != expected["sha256"]
        ):
            raise InstallError(f"installed extension differs from wheel: {module_name}")
        report[module_name] = actual
    return report


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def message(args: argparse.Namespace, value: str) -> None:
    print(value, file=sys.stderr if args.json else sys.stdout, flush=True)


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    comfy_home = args.comfy_home.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve()
    python = comfy_home / ".venv/bin/python"
    lock = load_lock(manifest, comfy_home)
    runtime_before = verify_runtime(python, lock)
    wheel_locked = matches_lock(lock.wheel.path, lock.wheel)
    install_locked, import_report = locked_install_state(python, lock, runtime_before)
    action = select_action(
        wheel_locked=wheel_locked,
        install_locked=install_locked,
        force=args.force_rebuild,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "source": SOURCE_URL,
        "revision": SOURCE_REVISION,
        "comfy_home": str(comfy_home),
        "manifest": str(lock.manifest),
        "action": action,
        "wheel_locked": wheel_locked,
        "install_locked": install_locked,
        "torch": runtime_before["versions"].get("torch"),
        "triton": runtime_before["versions"].get("triton"),
    }

    if args.dry_run:
        report["status"] = "DRY_RUN"
        return 0, report
    if args.check and action != "reuse":
        report["status"] = "FAIL"
        report["error"] = "locked SageAttention wheel/install is not complete"
        return 1, report

    if action == "reuse":
        message(args, "Locked SageAttention wheel and native extensions match; build/install skipped.")
        report["wheel"] = inspect_wheel(lock.wheel.path)
        report["imports"] = import_report
    else:
        wheel_path = lock.wheel.path
        build_log: Path | None = None
        toolchain: dict[str, str] | None = None
        include_dirs: list[str] | None = None
        quarantined: dict[str, str] | None = None
        if action == "build":
            message(args, "Provisioning pinned user-local CUDA/GCC toolchain.")
            micromamba = ensure_micromamba(comfy_home)
            toolchain = ensure_toolchain(comfy_home, micromamba)
            source = ensure_source(comfy_home)
            environment, include_dirs = build_environment(
                comfy_home=comfy_home,
                runtime=runtime_before,
                toolchain_prefix=comfy_home / ".cuda-12.8",
            )
            message(args, "Building SageAttention SM120 wheel with MAX_JOBS=1 and NVCC threads=1.")
            candidate, build_log = build_wheel(
                comfy_home=comfy_home, source=source, environment=environment
            )
            quarantined = promote_wheel(candidate, wheel_path)
        wheel_report = inspect_wheel(wheel_path)
        report.update(
            {
                "toolchain": toolchain,
                "include_dirs": include_dirs,
                "build_log": str(build_log) if build_log else None,
                "quarantined_wheel": quarantined,
                "wheel": wheel_report,
                "wheel_matches_manifest": matches_lock(wheel_path, lock.wheel),
            }
        )
        message(args, "Installing wheel into the ComfyUI venv with --no-deps.")
        install_wheel(python, wheel_path)
        runtime_after = verify_runtime(python, lock)
        for package in ("torch", "triton"):
            if runtime_after["versions"].get(package) != runtime_before["versions"].get(package):
                raise InstallError(f"{package} changed during SageAttention installation")
        imports = validate_install(python, gpu=False)
        report["installed_extensions"] = verify_installed_against_wheel(
            imports, wheel_report
        )
        report["imports"] = imports

    if not args.skip_gpu_test:
        message(args, "Running the small BF16 SM120 correctness comparison.")
        report["gpu_validation"] = validate_install(python, gpu=True)["gpu_validation"]
    else:
        report["gpu_validation"] = "skipped by explicit flag"
    report["status"] = "PASS"

    if action != "reuse":
        result_path = (
            args.result.expanduser().resolve()
            if args.result
            else comfy_home / ".build/sageattention-sm120-repro-result.json"
        )
        report["recorded_at"] = datetime.now(timezone.utc).isoformat()
        report["result_path"] = str(result_path)
        write_json_atomic(result_path, report)
    return 0, report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        status, report = execute(args)
    except (InstallError, OSError, json.JSONDecodeError) as exc:
        report = {"status": "FAIL", "error": str(exc)}
        status = 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif status == 0:
        print(
            f"SageAttention SM120: {report['status']} "
            f"(action={report['action']}, torch={report['torch']}, triton={report['triton']})"
        )
    else:
        print(f"SageAttention SM120: FAIL: {report.get('error')}", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
