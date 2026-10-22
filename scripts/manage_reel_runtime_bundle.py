#!/usr/bin/env python3
"""Install and verify Castle's hash-pinned Reel fixed-runtime bundle.

The tracked lock file is authority. This tool never discovers new inputs, never
updates the lock, never invokes a network service, and never adopts model bytes
into Castle's downloadable artifact store.
"""

from __future__ import annotations

import argparse
import binascii
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import uuid
import zlib
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPO_ROOT / "runtime/reel/reel-runtime.lock.json"
DEFAULT_SOURCE_ROOT = Path(os.environ.get("CASTLE_REEL5_SOURCE_ROOT", Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp")).expanduser()
DEFAULT_STORE_ROOT = Path.home() / ".local/share/web-comfyui/reel-runtime"
DEFAULT_BREEZYVOICE_CACHE_ROOT = (
    Path.home()
    / ".cache/huggingface/hub/models--MediaTek-Research--BreezyVoice-300M"
)
DEFAULT_WHISPER_CACHE_ROOT = Path.home() / ".cache/whisper"

LOCK_SCHEMA = "castle-reel-runtime-lock-v1"
STORE_MARKER = ".castle-reel-runtime-store-v1"
STAGING_MARKER = ".castle-reel-runtime-staging-v1"
DEFAULT_ARCHIVE_MARKER = ".castle-reel-runtime-archive-v1"
MARKER_CONTENT = {
    STORE_MARKER: "castle-reel-runtime-store-v1\n",
    STAGING_MARKER: "castle-reel-runtime-staging-v1\n",
    DEFAULT_ARCHIVE_MARKER: "castle-reel-runtime-archive-v1\n",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
EPISODE_PATH_PATTERNS = (
    re.compile(r"(^|/)(?:reel[0-9]+|audio_(?:reel5|host|peer|champion)_[0-9]+)(?:/|$)"),
    re.compile(r"(^|/)(?:cover|turnlist|champion|qc_report|review_proxy)_[0-9]+(?:[./]|$)"),
    re.compile(r"(^|/)\.generations(?:/|$)"),
    re.compile(r"\.mp4$", re.IGNORECASE),
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SCENE_PNG_IHDR = (1024, 1536, 8, 2, 0, 0, 0)
SCENE_PNG_ROW_BYTES = 1 + (SCENE_PNG_IHDR[0] * 3)
SCENE_PNG_RAW_BYTES = SCENE_PNG_ROW_BYTES * SCENE_PNG_IHDR[1]
SCENE_PNG_MAX_FILE_BYTES = 10 * 1024 * 1024
PNG_CRITICAL_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}


class BundleError(RuntimeError):
    """Expected fail-closed bundle error with a machine-readable kind."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _entries_content_hash(entries: list[dict]) -> str:
    identity = [
        {
            "runtime_path": entry["runtime_path"],
            "bytes": entry["bytes"],
            "sha256": entry["sha256"],
            "required": entry["required"],
        }
        for entry in sorted(entries, key=lambda item: item["runtime_path"])
    ]
    return _sha256_bytes(_json_bytes(identity))


def _safe_relative_path(value: object, field: str) -> str:
    text = str(value or "").strip()
    path = PurePosixPath(text)
    raw_parts = text.split("/")
    if (
        not text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or "\\" in text
        or text.startswith("~")
    ):
        raise BundleError("invalid_manifest", f"{field} must be a safe relative POSIX path")
    return path.as_posix()


def _reject_episode_output_path(path: str) -> None:
    for pattern in EPISODE_PATH_PATTERNS:
        if pattern.search(path):
            raise BundleError(
                "invalid_manifest",
                f"runtime_path is an excluded per-episode/output class: {path}",
            )


def _load_lock(lock_path: Path) -> tuple[dict, bytes]:
    unresolved = lock_path.expanduser().absolute()
    if unresolved.is_symlink() or not unresolved.is_file():
        raise BundleError("invalid_manifest", f"lock file not found or is a symlink: {unresolved}")
    path = unresolved.resolve()
    if not path.is_file():
        raise BundleError("invalid_manifest", f"lock file not found or is a symlink: {path}")
    raw = path.read_bytes()
    try:
        lock = json.loads(raw)
    except Exception as error:
        raise BundleError("invalid_manifest", f"lock file is not valid JSON: {error}") from error
    if not isinstance(lock, dict) or lock.get("schema_version") != LOCK_SCHEMA:
        raise BundleError("invalid_manifest", f"schema_version must be {LOCK_SCHEMA}")
    bundle_id = str(lock.get("bundle_id") or "")
    if not ID_RE.fullmatch(bundle_id):
        raise BundleError("invalid_manifest", "bundle_id is invalid")
    if lock.get("layout_version") != 1:
        raise BundleError("invalid_manifest", "layout_version must be 1")

    source_roots = lock.get("source_roots")
    if not isinstance(source_roots, dict) or not source_roots:
        raise BundleError("invalid_manifest", "source_roots must be a nonempty object")
    supported_source_rules = {
        "video_mvp": {"kind": "cli", "argument": "--source-root"},
        "voice_lab": {"kind": "derived", "from": "video_mvp", "relative": ".."},
        "breezyvoice_cache": {
            "kind": "huggingface_cache",
            "repo_id": "MediaTek-Research/BreezyVoice-300M",
            "revision": "e33b502e0ac21c16b0ee0d00df66ac3fa737393d",
            "argument": "--breezyvoice-cache-root",
        },
        "whisper_cache": {
            "kind": "whisper_cache",
            "models": ["large-v3.pt", "medium.pt"],
            "argument": "--whisper-cache-root",
        },
    }
    for root_id, rule in source_roots.items():
        if root_id not in supported_source_rules or rule != supported_source_rules[root_id]:
            raise BundleError("invalid_manifest", f"unsupported or drifted source root rule: {root_id}")
    entries = lock.get("entries")
    if not isinstance(entries, list) or not entries:
        raise BundleError("invalid_manifest", "entries must be a nonempty array")

    seen_ids: set[str] = set()
    seen_runtime_paths: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise BundleError("invalid_manifest", f"entries[{index}] must be an object")
        entry_id = str(entry.get("id") or "")
        if not ID_RE.fullmatch(entry_id) or entry_id in seen_ids:
            raise BundleError("invalid_manifest", f"entries[{index}].id is invalid or duplicated")
        seen_ids.add(entry_id)
        group = str(entry.get("group") or "")
        role = str(entry.get("role") or "")
        if not ID_RE.fullmatch(group) or not ID_RE.fullmatch(role):
            raise BundleError("invalid_manifest", f"entries[{index}] group/role is invalid")
        runtime_path = _safe_relative_path(entry.get("runtime_path"), f"entries[{index}].runtime_path")
        _reject_episode_output_path(runtime_path)
        if runtime_path in seen_runtime_paths:
            raise BundleError("invalid_manifest", f"duplicate runtime_path: {runtime_path}")
        seen_runtime_paths.add(runtime_path)
        entry["runtime_path"] = runtime_path
        source = entry.get("source")
        if not isinstance(source, dict) or source.get("root") not in source_roots:
            raise BundleError("invalid_manifest", f"entries[{index}].source.root is unknown")
        source["path"] = _safe_relative_path(source.get("path"), f"entries[{index}].source.path")
        if not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0:
            raise BundleError("invalid_manifest", f"entries[{index}].bytes is invalid")
        if not HASH_RE.fullmatch(str(entry.get("sha256") or "")):
            raise BundleError("invalid_manifest", f"entries[{index}].sha256 is invalid")
        if entry.get("required") is not True or not isinstance(entry.get("sensitive"), bool):
            raise BundleError("invalid_manifest", f"entries[{index}] required/sensitive is invalid")

    if lock.get("entry_count") != len(entries):
        raise BundleError("invalid_manifest", "entry_count does not match entries")
    if lock.get("total_bytes") != sum(entry["bytes"] for entry in entries):
        raise BundleError("invalid_manifest", "total_bytes does not match entries")
    actual_content_hash = _entries_content_hash(entries)
    if lock.get("content_set_sha256") != actual_content_hash:
        raise BundleError("invalid_manifest", "content_set_sha256 does not match entries")

    declared_groups = lock.get("groups")
    if not isinstance(declared_groups, dict):
        raise BundleError("invalid_manifest", "groups must be an object")
    actual_group_names = {entry["group"] for entry in entries}
    if set(declared_groups) != actual_group_names:
        raise BundleError("invalid_manifest", "groups do not match entry groups")
    for name in sorted(actual_group_names):
        group_entries = [entry for entry in entries if entry["group"] == name]
        expected = {
            "entry_count": len(group_entries),
            "total_bytes": sum(entry["bytes"] for entry in group_entries),
            "content_sha256": _entries_content_hash(group_entries),
        }
        if declared_groups.get(name) != expected:
            raise BundleError("invalid_manifest", f"group summary does not match entries: {name}")

    archive_policy = lock.get("archive_policy")
    if not isinstance(archive_policy, dict) or archive_policy.get("required") is not True:
        raise BundleError("invalid_manifest", "archive_policy.required must be true")
    marker = str(archive_policy.get("marker") or "")
    if marker != DEFAULT_ARCHIVE_MARKER:
        raise BundleError("invalid_manifest", f"archive marker must be {DEFAULT_ARCHIVE_MARKER}")
    if not isinstance(archive_policy.get("require_distinct_device"), bool):
        raise BundleError("invalid_manifest", "archive_policy.require_distinct_device must be boolean")
    return lock, raw


def _resolve_source_roots(args: argparse.Namespace, lock: dict) -> dict[str, Path]:
    source_root = Path(args.source_root).expanduser().resolve()
    voice_lab_root = Path(args.voice_lab_root).expanduser().resolve() if args.voice_lab_root else source_root.parent
    cache_root = Path(args.breezyvoice_cache_root).expanduser().resolve()
    whisper_cache_root = Path(args.whisper_cache_root).expanduser().resolve()
    available = {
        "video_mvp": source_root,
        "voice_lab": voice_lab_root,
        "breezyvoice_cache": cache_root,
        "whisper_cache": whisper_cache_root,
    }
    resolved: dict[str, Path] = {}
    for root_id in lock["source_roots"]:
        if root_id not in available:
            raise BundleError("invalid_manifest", f"unsupported source root rule: {root_id}")
        root = available[root_id]
        if root.is_symlink() or not root.is_dir():
            raise BundleError("missing_source", f"source root missing or is a symlink: {root_id}")
        resolved[root_id] = root
    return resolved


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _invalid_scene_png(label: str, detail: str) -> None:
    raise BundleError("invalid_scene_png", f"{label} is not a valid fixed Reel scene PNG: {detail}")


def _validate_scene_png_stream(stream: BinaryIO, label: str) -> None:
    """Validate the fixed 1024x1536 RGB scene contract without reopening the file."""

    try:
        data = stream.read(SCENE_PNG_MAX_FILE_BYTES + 1)
    except OSError as error:
        _invalid_scene_png(label, f"read failed: {error}")
    if len(data) > SCENE_PNG_MAX_FILE_BYTES:
        _invalid_scene_png(label, "file exceeds the bounded scene PNG size")
    if not data.startswith(PNG_SIGNATURE):
        _invalid_scene_png(label, "PNG signature is missing")

    offset = len(PNG_SIGNATURE)
    chunk_index = 0
    seen_ihdr = False
    seen_plte = False
    seen_idat = False
    idat_closed = False
    seen_iend = False
    idat_parts: list[bytes] = []

    while offset < len(data):
        if len(data) - offset < 12:
            _invalid_scene_png(label, "truncated chunk framing")
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        if not all((65 <= byte <= 90) or (97 <= byte <= 122) for byte in chunk_type):
            _invalid_scene_png(label, "chunk type is not four ASCII letters")
        if chunk_type[2] & 0x20:
            _invalid_scene_png(label, f"chunk {chunk_type!r} has an invalid reserved bit")
        if length > SCENE_PNG_MAX_FILE_BYTES:
            _invalid_scene_png(label, f"chunk {chunk_type!r} exceeds the size bound")
        payload_start = offset + 8
        payload_end = payload_start + length
        chunk_end = payload_end + 4
        if chunk_end > len(data):
            _invalid_scene_png(label, f"chunk {chunk_type!r} is truncated")
        payload = data[payload_start:payload_end]
        declared_crc = struct.unpack_from(">I", data, payload_end)[0]
        actual_crc = binascii.crc32(chunk_type + payload) & 0xFFFFFFFF
        if declared_crc != actual_crc:
            _invalid_scene_png(label, f"chunk {chunk_type!r} has a CRC mismatch")

        if chunk_index == 0 and chunk_type != b"IHDR":
            _invalid_scene_png(label, "IHDR is not the first chunk")
        if (chunk_type[0] & 0x20) == 0 and chunk_type not in PNG_CRITICAL_CHUNKS:
            _invalid_scene_png(label, f"unknown critical chunk {chunk_type!r}")

        if chunk_type == b"IHDR":
            if seen_ihdr or chunk_index != 0 or length != 13:
                _invalid_scene_png(label, "IHDR is duplicated, misplaced, or has the wrong length")
            ihdr = struct.unpack(">IIBBBBB", payload)
            if ihdr != SCENE_PNG_IHDR:
                _invalid_scene_png(
                    label,
                    "IHDR must be 1024x1536, 8-bit RGB, non-interlaced, with standard compression/filtering",
                )
            seen_ihdr = True
        elif chunk_type == b"PLTE":
            if not seen_ihdr or seen_plte or seen_idat or not length or length % 3 or length > 768:
                _invalid_scene_png(label, "PLTE is duplicated, misplaced, or malformed")
            seen_plte = True
        elif chunk_type == b"IDAT":
            if not seen_ihdr or idat_closed:
                _invalid_scene_png(label, "IDAT chunks are misplaced or non-contiguous")
            seen_idat = True
            idat_parts.append(payload)
        elif chunk_type == b"IEND":
            if not seen_idat or seen_iend or length != 0:
                _invalid_scene_png(label, "IEND is duplicated, misplaced, or malformed")
            seen_iend = True
            if chunk_end != len(data):
                _invalid_scene_png(label, "bytes or chunks follow IEND")
        elif seen_idat:
            idat_closed = True

        offset = chunk_end
        chunk_index += 1
        if seen_iend:
            break

    if not seen_ihdr or not seen_idat or not seen_iend or offset != len(data):
        _invalid_scene_png(label, "required IHDR/IDAT/IEND structure is incomplete")

    compressed = b"".join(idat_parts)
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, SCENE_PNG_RAW_BYTES + 1)
        if len(raw) > SCENE_PNG_RAW_BYTES or decompressor.unconsumed_tail:
            _invalid_scene_png(label, "decompressed scanlines exceed the fixed size")
        raw += decompressor.flush(SCENE_PNG_RAW_BYTES + 1 - len(raw))
    except zlib.error as error:
        _invalid_scene_png(label, f"IDAT zlib stream is invalid: {error}")
    if (
        len(raw) != SCENE_PNG_RAW_BYTES
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        _invalid_scene_png(label, "IDAT does not decode to exactly one fixed-size image")
    for row in range(SCENE_PNG_IHDR[1]):
        if raw[row * SCENE_PNG_ROW_BYTES] > 4:
            _invalid_scene_png(label, f"scanline {row} has an invalid PNG filter")


def _verified_regular_file(
    path: Path,
    *,
    expected_size: int,
    expected_hash: str,
    label: str,
    deep: bool = True,
    semantic_validator: Callable[[BinaryIO, str], None] | None = None,
) -> None:
    if path.is_symlink():
        raise BundleError("unsafe_symlink", f"{label} is a symlink: {path}")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
    except OSError as error:
        raise BundleError("missing_or_drifted", f"{label} is unreadable: {path}: {error}") from error
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise BundleError("missing_or_drifted", f"{label} has wrong type/size: {path}")
        if deep:
            with os.fdopen(os.dup(fd), "rb") as handle:
                actual_hash = _hash_stream(handle)
            if actual_hash != expected_hash:
                raise BundleError("missing_or_drifted", f"{label} sha256 mismatch: {path}")
        if semantic_validator is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(fd), "rb") as handle:
                semantic_validator(handle, label)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise BundleError("source_changed_during_read", f"{label} changed while hashing: {path}")
    finally:
        os.close(fd)


def _source_path(entry: dict, roots: dict[str, Path]) -> Path:
    root = roots[entry["source"]["root"]].resolve()
    candidate = root.joinpath(*PurePosixPath(entry["source"]["path"]).parts)
    if candidate.is_symlink():
        raise BundleError("unsafe_symlink", f"source leaf must be a regular file, not symlink: {candidate}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise BundleError("source_escape", f"source escapes its declared root: {entry['id']}") from error
    return resolved


def audit_sources(lock: dict, roots: dict[str, Path], *, deep: bool = True) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for entry in lock["entries"]:
        source = _source_path(entry, roots)
        _verified_regular_file(
            source,
            expected_size=entry["bytes"],
            expected_hash=entry["sha256"],
            label=f"source {entry['id']}",
            deep=deep,
            semantic_validator=(
                _validate_scene_png_stream
                if entry["group"] == "scene_assets"
                else None
            ),
        )
        paths[entry["id"]] = source
    return paths


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_managed_root(path: Path, label: str, *, forbidden: list[Path]) -> Path:
    resolved = path.expanduser().absolute().resolve(strict=False)
    hard_forbidden = [Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve(), REPO_ROOT.resolve()]
    for item in hard_forbidden:
        candidate = item.expanduser().resolve(strict=False)
        if resolved == candidate:
            raise BundleError("unsafe_destination", f"{label} resolves to a forbidden root: {resolved}")
    for item in forbidden:
        candidate = item.expanduser().resolve(strict=False)
        if resolved == candidate or _is_relative_to(resolved, candidate) or _is_relative_to(candidate, resolved):
            raise BundleError("unsafe_destination", f"{label} overlaps a forbidden root: {candidate}")
    if len(resolved.parts) < 4:
        raise BundleError("unsafe_destination", f"{label} is too shallow to manage safely: {resolved}")
    return resolved


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _marker_text(name: str) -> str:
    return MARKER_CONTENT.get(name, f"{name.removeprefix('.')}\n")


def _require_marker(root: Path, marker_name: str, kind: str) -> None:
    marker = root / marker_name
    if marker.is_symlink() or not marker.is_file():
        raise BundleError(kind, f"required marker is missing or unsafe: {marker}")
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError as error:
        raise BundleError(kind, f"required marker is unreadable: {marker}: {error}") from error
    if text != _marker_text(marker_name):
        raise BundleError(kind, f"required marker content is invalid: {marker}")


def require_archive_root(
    archive_value: str | Path | None,
    lock: dict,
    *,
    store_root: Path,
    for_write: bool,
) -> Path:
    if not archive_value:
        raise BundleError("archive_unavailable", "archive is required but CASTLE_REEL_RUNTIME_ARCHIVE_ROOT/--archive-root is unset")
    try:
        archive = _validate_managed_root(Path(archive_value), "archive_root", forbidden=[store_root])
        if archive.is_symlink() or not archive.is_dir():
            raise BundleError("archive_unavailable", f"archive root is unavailable or is a symlink: {archive}")
        _require_marker(archive, lock["archive_policy"]["marker"], "archive_unavailable")
        if for_write and not os.access(archive, os.W_OK | os.X_OK):
            raise BundleError("archive_unavailable", f"archive root is not writable: {archive}")
        if lock["archive_policy"]["require_distinct_device"]:
            store_parent = _nearest_existing_parent(store_root)
            if archive.stat().st_dev == store_parent.stat().st_dev:
                raise BundleError("archive_not_independent", "archive and local runtime store are on the same filesystem device")
    except BundleError:
        raise
    except OSError as error:
        raise BundleError("archive_unavailable", f"archive root cannot be accessed: {archive}: {error}") from error
    return archive


def _blob_path(root: Path, digest: str) -> Path:
    return root / "blobs/sha256" / digest[:2] / digest


def _relative_managed_path(root: Path, path: Path, label: str) -> tuple[Path, tuple[str, ...]]:
    root_abs = root.expanduser().absolute()
    path_abs = path.expanduser().absolute()
    if root_abs.is_symlink() or not root_abs.is_dir():
        raise BundleError("unsafe_destination", f"managed root is missing or is a symlink: {root_abs}")
    try:
        relative = path_abs.relative_to(root_abs)
    except ValueError as error:
        raise BundleError("unsafe_destination", f"{label} escapes managed root: {path_abs}") from error
    return root_abs, relative.parts


def _ensure_managed_parent(root: Path, target: Path, label: str) -> None:
    """Create target parents without ever traversing a symlink."""
    root_abs, parts = _relative_managed_path(root, target.parent, label)
    current = root_abs
    root_resolved = root_abs.resolve(strict=True)
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise BundleError("unsafe_symlink", f"{label} parent is a symlink: {current}")
        try:
            current.mkdir(mode=0o750)
        except FileExistsError:
            pass
        if not current.is_dir() or current.is_symlink():
            raise BundleError("unsafe_destination", f"{label} parent is not a directory: {current}")
        try:
            current.resolve(strict=True).relative_to(root_resolved)
        except (OSError, ValueError) as error:
            raise BundleError("unsafe_destination", f"{label} parent escapes managed root: {current}") from error


def _require_managed_parent(root: Path, target: Path, label: str) -> None:
    """Verify an existing target parent chain without creating anything."""
    root_abs, parts = _relative_managed_path(root, target.parent, label)
    current = root_abs
    root_resolved = root_abs.resolve(strict=True)
    for part in parts:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise BundleError("unsafe_symlink", f"{label} parent is missing or unsafe: {current}")
        try:
            current.resolve(strict=True).relative_to(root_resolved)
        except (OSError, ValueError) as error:
            raise BundleError("unsafe_destination", f"{label} parent escapes managed root: {current}") from error


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_verified_blob(source: Path, target: Path, entry: dict, *, managed_root: Path) -> bool:
    _ensure_managed_parent(managed_root, target, "blob")
    if target.exists() or target.is_symlink():
        _verified_regular_file(
            target,
            expected_size=entry["bytes"],
            expected_hash=entry["sha256"],
            label="existing blob",
            deep=True,
        )
        return False
    temp = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temp, flags, 0o440)
        try:
            with source.open("rb") as src, os.fdopen(fd, "wb", closefd=False) as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
        finally:
            os.close(fd)
        os.chmod(temp, 0o440)
        _verified_regular_file(
            temp,
            expected_size=entry["bytes"],
            expected_hash=entry["sha256"],
            label="staged blob",
            deep=True,
        )
        os.replace(temp, target)
        _fsync_dir(target.parent)
    finally:
        if temp.exists() and not temp.is_symlink():
            temp.unlink()
    return True


def _copy_exact_file(source: Path, target: Path, *, expected_hash: str, managed_root: Path) -> bool:
    _ensure_managed_parent(managed_root, target, "metadata file")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or _sha256_bytes(target.read_bytes()) != expected_hash:
            raise BundleError("missing_or_drifted", f"existing metadata file drifted: {target}")
        return False
    temp = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if _sha256_bytes(temp.read_bytes()) != expected_hash:
            raise BundleError("copy_failed", f"metadata copy hash mismatch: {target}")
        os.chmod(temp, 0o440)
        os.replace(temp, target)
        _fsync_dir(target.parent)
    finally:
        if temp.exists() and not temp.is_symlink():
            temp.unlink()
    return True


def _initialize_store(store_root: Path) -> bool:
    changed = False
    if not store_root.exists():
        store_root.mkdir(parents=True, mode=0o750)
        changed = True
    if store_root.is_symlink() or not store_root.is_dir():
        raise BundleError("unsafe_destination", f"runtime store is not a regular directory: {store_root}")
    marker = store_root / STORE_MARKER
    if marker.exists() or marker.is_symlink():
        _require_marker(store_root, STORE_MARKER, "unsafe_destination")
    else:
        unexpected = [item for item in store_root.iterdir() if item.name != ".install.lock"]
        if unexpected:
            raise BundleError("unsafe_destination", "refusing to initialize a nonempty unmarked runtime store")
        marker.write_text(MARKER_CONTENT[STORE_MARKER], encoding="utf-8")
        os.chmod(marker, 0o440)
        changed = True
    return changed


@contextlib.contextmanager
def _install_lock(store_root: Path) -> Iterator[None]:
    lock_path = store_root / ".install.lock"
    try:
        fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        raise BundleError("unsafe_symlink", f"install lock is unsafe: {lock_path}: {error}") from error
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BundleError("unsafe_destination", f"install lock is not a regular file: {lock_path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _new_staging_dir(store_root: Path) -> Path:
    staging_root = store_root / ".staging"
    _ensure_managed_parent(store_root, staging_root / "placeholder", "staging root")
    stage = staging_root / f"install-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o750)
    marker = stage / STAGING_MARKER
    marker.write_text(MARKER_CONTENT[STAGING_MARKER], encoding="utf-8")
    os.chmod(marker, 0o440)
    return stage


def _safe_remove_staging(path: Path, staging_root: Path) -> None:
    """Delete only a marked direct child of the managed staging root."""
    unresolved_root = staging_root.expanduser().absolute()
    unresolved_candidate = path.expanduser().absolute()
    if unresolved_root.is_symlink() or unresolved_candidate.is_symlink():
        raise BundleError("unsafe_cleanup", "refusing symlink staging cleanup target")
    try:
        root = unresolved_root.resolve(strict=True)
        candidate = unresolved_candidate.resolve(strict=True)
    except OSError as error:
        raise BundleError("unsafe_cleanup", f"cannot validate staging cleanup target: {error}") from error
    if (
        candidate == root
        or candidate.parent != root
        or not _is_relative_to(candidate, root)
        or candidate.is_symlink()
        or not candidate.name.startswith("install-")
    ):
        raise BundleError("unsafe_cleanup", f"refusing unsafe staging cleanup target: {candidate}")
    _require_marker(candidate, STAGING_MARKER, "unsafe_cleanup")
    shutil.rmtree(candidate)


def _materialize_release(store_root: Path, lock_path: Path, lock: dict, raw_lock: bytes) -> int:
    release = store_root / "releases" / lock["bundle_id"]
    lock_hash = _sha256_bytes(raw_lock)
    if release.exists() or release.is_symlink():
        _verify_release(release, lock, raw_lock, deep=True)
        return 0
    stage = _new_staging_dir(store_root)
    staging_root = stage.parent
    moved = False
    try:
        staged_release = stage / lock["bundle_id"]
        tree = staged_release / "tree"
        _ensure_managed_parent(store_root, tree / "placeholder", "staged release tree")
        for entry in lock["entries"]:
            blob = _blob_path(store_root, entry["sha256"])
            target = tree.joinpath(*PurePosixPath(entry["runtime_path"]).parts)
            _ensure_managed_parent(store_root, target, "release entry")
            try:
                os.link(blob, target, follow_symlinks=False)
            except OSError:
                _copy_verified_blob(blob, target, entry, managed_root=store_root)
        _copy_exact_file(
            lock_path,
            staged_release / "manifest.lock.json",
            expected_hash=lock_hash,
            managed_root=store_root,
        )
        _verify_release(staged_release, lock, raw_lock, deep=True)
        _ensure_managed_parent(store_root, release, "release directory")
        os.replace(staged_release, release)
        _fsync_dir(release.parent)
        moved = True
    finally:
        # After the staged release is moved, only the marker remains in stage.
        # On failure, this removes only the marked UUID staging directory.
        if stage.exists():
            _safe_remove_staging(stage, staging_root)
    return 1 if moved else 0


def _activate_release(store_root: Path, bundle_id: str) -> bool:
    current = store_root / "current"
    relative_target = Path("releases") / bundle_id / "tree"
    expected = (store_root / relative_target).resolve()
    if current.is_symlink():
        try:
            if current.resolve(strict=True) == expected:
                return False
        except OSError:
            pass
    elif current.exists():
        raise BundleError("unsafe_destination", "current exists and is not a symlink")
    temp = store_root / f".current.tmp-{uuid.uuid4().hex}"
    try:
        os.symlink(relative_target.as_posix(), temp)
        os.replace(temp, current)
        _fsync_dir(store_root)
    finally:
        if temp.is_symlink():
            temp.unlink()
    if current.resolve(strict=True) != expected:
        raise BundleError("activation_failed", "current did not resolve to the selected release")
    return True


def _verify_release(release: Path, lock: dict, raw_lock: bytes, *, deep: bool) -> None:
    if release.is_symlink() or not release.is_dir():
        raise BundleError("missing_or_drifted", f"release is missing or is a symlink: {release}")
    manifest = release / "manifest.lock.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.read_bytes() != raw_lock:
        raise BundleError("missing_or_drifted", f"release lock copy drifted: {manifest}")
    tree = release / "tree"
    if tree.is_symlink() or not tree.is_dir():
        raise BundleError("missing_or_drifted", f"release tree is missing or is a symlink: {tree}")
    expected_paths = {entry["runtime_path"] for entry in lock["entries"]}
    actual_paths: set[str] = set()
    for path in tree.rglob("*"):
        if path.is_symlink():
            raise BundleError("unsafe_symlink", f"release tree contains a symlink: {path}")
        if path.is_file():
            actual_paths.add(path.relative_to(tree).as_posix())
    if actual_paths != expected_paths:
        raise BundleError("missing_or_drifted", "release tree file set does not match the lock")
    for entry in lock["entries"]:
        target = tree.joinpath(*PurePosixPath(entry["runtime_path"]).parts)
        _verified_regular_file(
            target,
            expected_size=entry["bytes"],
            expected_hash=entry["sha256"],
            label=f"release {entry['id']}",
            deep=deep,
        )


def _verify_blob_set(root: Path, lock: dict, *, deep: bool, label: str) -> None:
    for entry in lock["entries"]:
        target = _blob_path(root, entry["sha256"])
        _require_managed_parent(root, target, f"{label} blob")
        _verified_regular_file(
            target,
            expected_size=entry["bytes"],
            expected_hash=entry["sha256"],
            label=f"{label} blob {entry['id']}",
            deep=deep,
        )


def _verify_archive(archive: Path, lock: dict, raw_lock: bytes, *, deep: bool) -> None:
    manifest = archive / "manifests" / f"{lock['bundle_id']}.lock.json"
    _require_managed_parent(archive, manifest, "archive manifest")
    if manifest.is_symlink() or not manifest.is_file() or manifest.read_bytes() != raw_lock:
        raise BundleError("archive_drifted", f"archive lock copy missing or drifted: {manifest}")
    _verify_blob_set(archive, lock, deep=deep, label="archive")


def _verify_local(store_root: Path, lock: dict, raw_lock: bytes, *, deep: bool) -> None:
    if store_root.is_symlink() or not store_root.is_dir():
        raise BundleError("missing_or_drifted", f"local runtime store is unavailable: {store_root}")
    _require_marker(store_root, STORE_MARKER, "missing_or_drifted")
    _verify_blob_set(store_root, lock, deep=deep, label="local")
    release = store_root / "releases" / lock["bundle_id"]
    _verify_release(release, lock, raw_lock, deep=deep)
    current = store_root / "current"
    if not current.is_symlink():
        raise BundleError("missing_or_drifted", "current is missing or is not an atomic symlink")
    try:
        if current.resolve(strict=True) != (release / "tree").resolve(strict=True):
            raise BundleError("missing_or_drifted", "current does not target the locked release")
    except OSError as error:
        raise BundleError("missing_or_drifted", f"current target is unreadable: {error}") from error


def install_bundle(
    *,
    lock_path: Path,
    lock: dict,
    raw_lock: bytes,
    source_paths: dict[str, Path],
    store_root: Path,
    archive_root: Path,
) -> dict:
    store_root = _validate_managed_root(store_root, "store_root", forbidden=[archive_root])
    local_changed = 0
    archive_changed = 0
    activated = False
    store_created = False
    # Archive readiness is checked by the caller before any local mutation.
    store_root.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    store_created = _initialize_store(store_root)
    with _install_lock(store_root):
        for entry in lock["entries"]:
            source = source_paths[entry["id"]]
            local_blob = _blob_path(store_root, entry["sha256"])
            if _copy_verified_blob(source, local_blob, entry, managed_root=store_root):
                local_changed += 1
            archive_blob = _blob_path(archive_root, entry["sha256"])
            if _copy_verified_blob(local_blob, archive_blob, entry, managed_root=archive_root):
                archive_changed += 1
        archive_manifest = archive_root / "manifests" / f"{lock['bundle_id']}.lock.json"
        if _copy_exact_file(
            lock_path,
            archive_manifest,
            expected_hash=_sha256_bytes(raw_lock),
            managed_root=archive_root,
        ):
            archive_changed += 1
        _verify_archive(archive_root, lock, raw_lock, deep=True)
        local_changed += _materialize_release(store_root, lock_path, lock, raw_lock)
        activated = _activate_release(store_root, lock["bundle_id"])
        _verify_local(store_root, lock, raw_lock, deep=True)
    return {
        "ok": True,
        "command": "install",
        "bundle_id": lock["bundle_id"],
        "content_set_sha256": lock["content_set_sha256"],
        "entry_count": lock["entry_count"],
        "total_bytes": lock["total_bytes"],
        "store_created": store_created,
        "local_changed": local_changed,
        "archive_changed": archive_changed,
        "activated": activated,
        "archive_verified": True,
    }


def verify_bundle(
    *,
    lock: dict,
    raw_lock: bytes,
    store_root: Path,
    archive_value: str | Path | None,
    deep: bool,
) -> dict:
    result = {
        "ok": False,
        "command": "verify",
        "bundle_id": lock["bundle_id"],
        "content_set_sha256": lock["content_set_sha256"],
        "mode": "deep" if deep else "quick",
        "local_ok": False,
        "archive_ok": False,
        "errors": [],
    }
    try:
        safe_store = _validate_managed_root(store_root, "store_root", forbidden=[])
        _verify_local(safe_store, lock, raw_lock, deep=deep)
        result["local_ok"] = True
    except BundleError as error:
        result["errors"].append({"kind": error.kind, "scope": "local", "message": str(error)})
        safe_store = store_root.expanduser().absolute().resolve(strict=False)
    try:
        archive = require_archive_root(
            archive_value,
            lock,
            store_root=safe_store,
            for_write=False,
        )
        _verify_archive(archive, lock, raw_lock, deep=deep)
        result["archive_ok"] = True
    except BundleError as error:
        result["errors"].append({"kind": error.kind, "scope": "archive", "message": str(error)})
    result["ok"] = result["local_ok"] and result["archive_ok"]
    if not result["archive_ok"]:
        result["failure_kind"] = result["errors"][-1]["kind"] if result["errors"] else "archive_unavailable"
    elif not result["local_ok"]:
        result["failure_kind"] = result["errors"][0]["kind"]
    return result


def _common_arguments(parser: argparse.ArgumentParser, *, include_archive: bool = True) -> None:
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--voice-lab-root", type=Path)
    parser.add_argument("--breezyvoice-cache-root", type=Path, default=DEFAULT_BREEZYVOICE_CACHE_ROOT)
    parser.add_argument("--whisper-cache-root", type=Path, default=DEFAULT_WHISPER_CACHE_ROOT)
    parser.add_argument("--store-root", type=Path, default=DEFAULT_STORE_ROOT)
    if include_archive:
        parser.add_argument(
            "--archive-root",
            type=Path,
            default=os.environ.get("CASTLE_REEL_RUNTIME_ARCHIVE_ROOT") or None,
        )
    parser.add_argument("--json", action="store_true", dest="as_json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit-source", help="Verify every tracked source byte without writing")
    _common_arguments(audit, include_archive=False)
    install = subparsers.add_parser("install", help="Import, archive, materialize, and atomically activate")
    _common_arguments(install)
    verify = subparsers.add_parser("verify", help="Verify local materialization and required independent archive")
    _common_arguments(verify)
    status = subparsers.add_parser("status", help="Report readiness; archive absence is unhealthy")
    _common_arguments(status)
    return parser


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    if result.get("ok"):
        print(
            f"reel_runtime_{result.get('command')} ok=true "
            f"bundle={result.get('bundle_id')} entries={result.get('entry_count', '')}"
        )
    else:
        print(
            f"reel_runtime_{result.get('command')} ok=false "
            f"failure_kind={result.get('failure_kind', 'verification_failed')}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        lock, raw_lock = _load_lock(args.lock)
        lock_path = Path(args.lock).expanduser().resolve()
        if args.command == "audit-source":
            roots = _resolve_source_roots(args, lock)
            audit_sources(lock, roots, deep=True)
            result = {
                "ok": True,
                "command": "audit-source",
                "mode": "deep",
                "bundle_id": lock["bundle_id"],
                "content_set_sha256": lock["content_set_sha256"],
                "entry_count": lock["entry_count"],
                "total_bytes": lock["total_bytes"],
                "writes_performed": 0,
            }
            _emit(result, args.as_json)
            return 0
        if args.command == "install":
            source_roots = _resolve_source_roots(args, lock)
            store_root = _validate_managed_root(Path(args.store_root), "store_root", forbidden=list(source_roots.values()))
            # Fail closed before source copying, store creation, or release materialization.
            archive_root = require_archive_root(
                args.archive_root,
                lock,
                store_root=store_root,
                for_write=True,
            )
            source_paths = audit_sources(lock, source_roots, deep=True)
            result = install_bundle(
                lock_path=lock_path,
                lock=lock,
                raw_lock=raw_lock,
                source_paths=source_paths,
                store_root=store_root,
                archive_root=archive_root,
            )
            _emit(result, args.as_json)
            return 0
        if args.command in {"verify", "status"}:
            result = verify_bundle(
                lock=lock,
                raw_lock=raw_lock,
                store_root=Path(args.store_root),
                archive_value=args.archive_root,
                deep=True,
            )
            result["command"] = args.command
            _emit(result, args.as_json)
            return 0 if result["ok"] else 2
        raise BundleError("unsupported_command", f"unsupported command: {args.command}")
    except BundleError as error:
        result = {
            "ok": False,
            "command": getattr(args, "command", "unknown"),
            "failure_kind": error.kind,
            "error": str(error),
        }
        _emit(result, getattr(args, "as_json", False))
        return 2
    except OSError as error:
        result = {
            "ok": False,
            "command": getattr(args, "command", "unknown"),
            "failure_kind": "io_error",
            "error": str(error),
        }
        _emit(result, getattr(args, "as_json", False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
