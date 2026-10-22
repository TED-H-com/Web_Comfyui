#!/usr/bin/env python3
"""Read-only preflight audit for the pinned MiniMax H3 runtime."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from artifacts import ArtifactError, load_manifest, select_family, verify_file
else:
    from .artifacts import ArtifactError, load_manifest, select_family, verify_file


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_MANIFEST = REPO_ROOT / "manifests/minimax_h3/models.lock.json"
DEFAULT_WORKFLOW_MANIFEST = REPO_ROOT / "manifests/minimax_h3/workflows.lock.json"
MIN_COMFY_VERSION = (0, 30, 0)


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for component in value.split(".")[:3]:
        digits = "".join(character for character in component if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


def _load_local_comfy_version(comfy_dir: Path) -> str | None:
    version_file = comfy_dir / "comfyui_version.py"
    try:
        for line in version_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("__version__"):
                _, raw = line.split("=", 1)
                value = raw.strip().strip("\"'")
                return value or None
    except (OSError, ValueError):
        return None
    return None


def _json_get(url: str, timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "castle-h3-audit/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object from {url}")
    return payload


def _object_info_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("ComfyUI URL must use HTTP or HTTPS")
    hostname = parsed.hostname
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("audit only connects to a loopback ComfyUI endpoint")
    return f"{base_url.rstrip('/')}/object_info"


def _remote_comfy_version(base_url: str, timeout: float) -> str | None:
    try:
        payload = _json_get(f"{base_url.rstrip('/')}/system_stats", timeout)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    system = payload.get("system")
    if not isinstance(system, dict):
        return None
    value = system.get("comfyui_version")
    return str(value) if value is not None else None


def audit(
    *,
    comfy_dir: Path,
    models_dir: Path,
    family: str,
    model_manifest_path: Path,
    workflow_manifest_path: Path,
    comfy_url: str,
    timeout: float,
    skip_hashes: bool,
) -> list[Check]:
    checks: list[Check] = []
    model_manifest = load_manifest(model_manifest_path)
    workflow_manifest = load_manifest(workflow_manifest_path)
    artifacts = select_family(model_manifest.artifacts, family)

    local_version = _load_local_comfy_version(comfy_dir)
    if local_version is None:
        checks.append(Check("comfy-version", "fail", f"not found under {comfy_dir}"))
    elif _version_tuple(local_version) < MIN_COMFY_VERSION:
        checks.append(
            Check("comfy-version", "fail", f"{local_version}; require >= 0.30.0")
        )
    else:
        checks.append(Check("comfy-version", "pass", local_version))

    python = comfy_dir / ".venv/bin/python"
    checks.append(
        Check(
            "comfy-venv",
            "pass" if python.is_file() else "fail",
            str(python),
        )
    )

    for artifact in artifacts:
        path = models_dir / artifact.destination
        if not path.is_file():
            checks.append(Check(f"model:{artifact.destination}", "fail", "missing"))
        elif skip_hashes:
            actual = path.stat().st_size
            state = "pass" if actual == artifact.expected_bytes else "fail"
            checks.append(
                Check(
                    f"model:{artifact.destination}",
                    state,
                    f"size {actual}/{artifact.expected_bytes}; SHA256 skipped",
                )
            )
        else:
            state = "pass" if verify_file(path, artifact) else "fail"
            checks.append(Check(f"model:{artifact.destination}", state, "size + SHA256"))

    try:
        object_info_url = _object_info_url(comfy_url)
        object_info = _json_get(object_info_url, timeout)
        remote_version = _remote_comfy_version(comfy_url, timeout)
        checks.append(
            Check(
                "loopback-api",
                "pass",
                f"{object_info_url}; version {remote_version or 'not reported'}",
            )
        )
        required = workflow_manifest.raw.get("required_object_info_nodes", [])
        missing = sorted(node for node in required if node not in object_info)
        if missing:
            checks.append(Check("object-info-nodes", "fail", ", ".join(missing)))
        else:
            checks.append(
                Check("object-info-nodes", "pass", f"{len(required)} required nodes")
            )
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        json.JSONDecodeError,
        socket.timeout,
    ) as exc:
        checks.append(Check("loopback-api", "fail", str(exc)))

    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-dir", default=str(Path.home() / "comfyui"))
    parser.add_argument("--models-dir")
    parser.add_argument("--family", choices=("fl", "ref", "all"), default="fl")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--model-manifest", default=str(DEFAULT_MODEL_MANIFEST))
    parser.add_argument("--workflow-manifest", default=str(DEFAULT_WORKFLOW_MANIFEST))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--skip-hashes",
        action="store_true",
        help="check file sizes only (fast, but not a release-quality audit)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    comfy_dir = Path(args.comfy_dir).expanduser()
    models_dir = (
        Path(args.models_dir).expanduser()
        if args.models_dir
        else comfy_dir / "models"
    )
    try:
        checks = audit(
            comfy_dir=comfy_dir,
            models_dir=models_dir,
            family=args.family,
            model_manifest_path=Path(args.model_manifest).expanduser(),
            workflow_manifest_path=Path(args.workflow_manifest).expanduser(),
            comfy_url=args.comfy_url,
            timeout=args.timeout,
            skip_hashes=args.skip_hashes,
        )
    except (ArtifactError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps([check.__dict__ for check in checks], indent=2))
    else:
        for check in checks:
            marker = "ok" if check.state == "pass" else "FAIL"
            print(f"[{marker}] {check.name}: {check.detail}")
    return 0 if all(check.state == "pass" for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(run())
