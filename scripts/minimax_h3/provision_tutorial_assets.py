#!/usr/bin/env python3
"""Plan, verify, or explicitly download pinned MiniMax H3 tutorial assets.

The default action is a read-only plan.  Network and filesystem writes require
both ``--execute`` and ``--accept-large-downloads``.  Every selected artifact
comes from the immutable tutorial-stack manifest and is published only after
an exact byte-count and SHA-256 gate performed by ``download_pinned_ranges``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Sequence
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from download_pinned_ranges import DownloadError, DownloadSpec, RangeDownloader
else:
    from .download_pinned_ranges import DownloadError, DownloadSpec, RangeDownloader


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/tutorial_stack.lock.json"
DEFAULT_MODELS_DIR = Path.home() / "comfyui/models"
SOURCE_RE = re.compile(
    r"^(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@"
    r"(?P<revision>[0-9a-f]{40})$"
)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LARGE_DOWNLOAD_BYTES = 256 * 1024 * 1024
BUFFER_BYTES = 8 * 1024 * 1024
REPORT_SCHEMA = "castle.minimax-h3.tutorial-provision.v1"


class ProvisionError(RuntimeError):
    """A manifest, destination, or local-integrity invariant failed."""


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    repository: str
    revision: str
    source_path: str
    destination: str
    bytes: int
    sha256: str
    required: bool

    @property
    def url(self) -> str:
        encoded = quote(self.source_path, safe="/")
        return (
            f"https://huggingface.co/{self.repository}/resolve/"
            f"{self.revision}/{encoded}?download=true"
        )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProvisionError(f"cannot read tutorial manifest: {path}") from error


def _safe_relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ProvisionError(f"invalid {field}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProvisionError(f"unsafe {field}: {value!r}")
    return path.as_posix()


def load_assets(path: Path) -> tuple[Asset, ...]:
    document = _load_json(path)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ProvisionError("unsupported tutorial manifest schema")
    records = document.get("model_assets")
    if not isinstance(records, list) or not records:
        raise ProvisionError("tutorial manifest has no model assets")

    assets: list[Asset] = []
    seen_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ProvisionError("tutorial model asset must be an object")
        asset_id = record.get("id")
        if not isinstance(asset_id, str) or ID_RE.fullmatch(asset_id) is None:
            raise ProvisionError("tutorial model asset has an invalid id")
        if asset_id in seen_ids:
            raise ProvisionError(f"duplicate tutorial model asset id: {asset_id}")
        seen_ids.add(asset_id)

        source = record.get("source")
        match = SOURCE_RE.fullmatch(source) if isinstance(source, str) else None
        if match is None:
            raise ProvisionError(f"asset source is not commit-pinned: {asset_id}")
        size = record.get("bytes")
        digest = record.get("sha256")
        if type(size) is not int or size <= 0:
            raise ProvisionError(f"asset byte count is invalid: {asset_id}")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise ProvisionError(f"asset SHA-256 is invalid: {asset_id}")

        assets.append(
            Asset(
                id=asset_id,
                repository=match.group("repository"),
                revision=match.group("revision"),
                source_path=_safe_relative(
                    record.get("source_path"), field=f"{asset_id}.source_path"
                ),
                destination=_safe_relative(
                    record.get("destination"), field=f"{asset_id}.destination"
                ),
                bytes=size,
                sha256=digest,
                required=bool(record.get("required", True)),
            )
        )
    return tuple(assets)


def select_assets(
    assets: Sequence[Asset], *, required: bool, ids: Sequence[str]
) -> tuple[Asset, ...]:
    indexed = {asset.id: asset for asset in assets}
    unknown = sorted(set(ids) - set(indexed))
    if unknown:
        raise ProvisionError("unknown tutorial asset: " + ", ".join(unknown))

    selected_ids: set[str] = set(ids)
    if required:
        selected_ids.update(asset.id for asset in assets if asset.required)
    selected = [asset for asset in assets if asset.id in selected_ids]
    if not selected:
        raise ProvisionError("select --required and/or at least one --asset")

    destinations: dict[str, Asset] = {}
    result: list[Asset] = []
    for asset in selected:
        previous = destinations.get(asset.destination)
        if previous is None:
            destinations[asset.destination] = asset
            result.append(asset)
            continue
        if (
            previous.bytes != asset.bytes
            or previous.sha256 != asset.sha256
            or previous.url != asset.url
        ):
            raise ProvisionError(
                f"conflicting pins share destination: {asset.destination}"
            )
    return tuple(result)


def _models_root(value: Path) -> Path:
    root = value.expanduser()
    if not root.is_absolute() or "\x00" in os.fspath(root):
        raise ProvisionError("models directory must be an absolute path")
    try:
        info = os.lstat(root)
    except FileNotFoundError as error:
        raise ProvisionError(f"models directory does not exist: {root}") from error
    except OSError as error:
        raise ProvisionError(f"models directory is unreadable: {root}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ProvisionError("models directory must be a real directory")
    if info.st_uid != os.geteuid():
        raise ProvisionError("models directory must be owned by the current user")
    return root


def _ensure_parent(root: Path, destination: str) -> Path:
    current = root
    parts = PurePosixPath(destination).parts
    for part in parts[:-1]:
        current /= part
        try:
            os.mkdir(current, mode=0o755)
        except FileExistsError:
            pass
        except OSError as error:
            raise ProvisionError(f"cannot create model directory: {current}") from error
        info = os.lstat(current)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
        ):
            raise ProvisionError(f"unsafe model directory: {current}")
    return root / destination


def _verify(path: Path, asset: Asset) -> dict[str, object]:
    try:
        info = os.lstat(path)
    except FileNotFoundError as error:
        raise ProvisionError(f"missing tutorial asset: {asset.id}") from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        raise ProvisionError(f"unsafe tutorial asset: {asset.id}")
    if info.st_size != asset.bytes:
        raise ProvisionError(f"tutorial asset byte mismatch: {asset.id}")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(BUFFER_BYTES), b""):
            count += len(block)
            digest.update(block)
    if count != asset.bytes or digest.hexdigest() != asset.sha256:
        raise ProvisionError(f"tutorial asset SHA-256 mismatch: {asset.id}")
    return {"id": asset.id, "status": "verified", "bytes": count}


def plan(selected: Sequence[Asset], models_dir: Path) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "planned",
        "execution_performed": False,
        "network_performed": False,
        "models_dir": str(models_dir),
        "asset_count": len(selected),
        "total_bytes": sum(asset.bytes for asset in selected),
        "assets": [
            {
                "id": asset.id,
                "destination": asset.destination,
                "bytes": asset.bytes,
                "sha256": asset.sha256,
                "source": f"{asset.repository}@{asset.revision}",
            }
            for asset in selected
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument(
        "--required",
        action="store_true",
        help="select all tutorial assets not explicitly marked optional",
    )
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        metavar="ID",
        help="select one exact manifest asset; repeat as needed",
    )
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument(
        "--accept-large-downloads",
        action="store_true",
        help="explicitly accept selected downloads over 256 MiB",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--verify-only", action="store_true")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        assets = load_assets(args.manifest.expanduser())
        selected = select_assets(assets, required=args.required, ids=args.asset)
        if not 1 <= args.lanes <= 16:
            raise ProvisionError("lanes must be between 1 and 16")
        models_dir = args.models_dir.expanduser()
        if not models_dir.is_absolute() or "\x00" in os.fspath(models_dir):
            raise ProvisionError("models directory must be an absolute path")
        report = plan(selected, models_dir)
        if not args.execute and not args.verify_only:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0

        root = _models_root(models_dir)
        if args.verify_only:
            results = [
                _verify(root / asset.destination, asset) for asset in selected
            ]
            report.update(
                status="verified",
                assets=results,
                hashes_verified=True,
            )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0

        large_bytes = sum(
            asset.bytes for asset in selected if asset.bytes >= LARGE_DOWNLOAD_BYTES
        )
        if large_bytes and not args.accept_large_downloads:
            raise ProvisionError(
                "large tutorial downloads require --accept-large-downloads"
            )

        results: list[dict[str, object]] = []
        network_performed = False
        for asset in selected:
            destination = _ensure_parent(root, asset.destination)
            result = RangeDownloader(
                DownloadSpec(
                    url=asset.url,
                    destination=destination,
                    expected_bytes=asset.bytes,
                    sha256=asset.sha256,
                    lanes=args.lanes,
                )
            ).execute()
            network_performed = network_performed or bool(
                result.get("network_performed")
            )
            results.append(
                {
                    "id": asset.id,
                    "status": result.get("status"),
                    "destination": asset.destination,
                    "bytes": asset.bytes,
                    "destination_written": bool(
                        result.get("destination_written")
                    ),
                }
            )
        report.update(
            status="verified",
            execution_performed=True,
            network_performed=network_performed,
            assets=results,
            hashes_verified=True,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except (ProvisionError, DownloadError, OSError) as error:
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
