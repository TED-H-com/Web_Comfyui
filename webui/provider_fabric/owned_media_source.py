"""Resolve one exact owner-bound provider asset into trusted local bytes."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .media_materializer import PrivateProviderMediaStore
from .models import MediaAsset
from .store import ProviderFabricStore


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SOURCE_RE = re.compile(r"^/([^/]+)/([^/]+)$")


def owned_provider_asset_uri(projection_id: str, version_id: str) -> str:
    """Return an opaque Castle URI containing no path or content digest."""

    for value, label in ((projection_id, "projection_id"), (version_id, "version_id")):
        if not isinstance(value, str) or not value or "/" in value or "\x00" in value:
            raise ValueError(f"{label} is invalid")
    return (
        "castle://provider-assets/"
        f"{quote(projection_id, safe='')}/{quote(version_id, safe='')}"
    )


def _identity(uri: str) -> tuple[str, str]:
    parsed = urlsplit(uri)
    match = _SOURCE_RE.fullmatch(parsed.path)
    if (
        parsed.scheme != "castle" or parsed.netloc != "provider-assets"
        or parsed.query or parsed.fragment or match is None
    ):
        raise ValueError("asset is not an exact owned provider-media source")
    projection_id, version_id = (unquote(item) for item in match.groups())
    if not projection_id or not version_id or any(
        "/" in item or "\x00" in item for item in (projection_id, version_id)
    ):
        raise ValueError("provider-media source identity is invalid")
    return projection_id, version_id


def _stable_file(path: Path, expected_size: int, expected_sha256: str) -> None:
    before = path.stat(follow_symlinks=False)
    if (
        not path.is_file() or path.is_symlink() or before.st_nlink != 1
        or before.st_uid != os.geteuid() or before.st_size != expected_size
    ):
        raise ValueError("provider-media source is unavailable or changed")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or digest.hexdigest() != expected_sha256
    ):
        raise ValueError("provider-media source is unavailable or changed")


def _contained_file(uri: str, roots: tuple[Path, ...]) -> Path:
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("provider-media source has no trusted local bytes")
    candidate = Path(unquote(parsed.path))
    if not candidate.is_absolute():
        raise ValueError("provider-media file source is not absolute")
    lexical = Path(os.path.abspath(candidate))
    real = Path(os.path.realpath(lexical))
    if not any(
        lexical.is_relative_to(root) and real.is_relative_to(Path(os.path.realpath(root)))
        for root in roots
    ):
        raise ValueError("provider-media file source is outside approved storage")
    return lexical


def resolve_owned_media_source(
    store: ProviderFabricStore,
    media_store: PrivateProviderMediaStore,
    owner_user_id: str,
    asset: MediaAsset,
    *,
    allowed_file_roots: tuple[Path, ...] = (),
) -> Path:
    """Resolve and revalidate owner, version, hash, MIME and byte size."""

    projection_id, version_id = _identity(asset.uri)
    evidence = store.get_owned_remote_asset_version_source(
        owner_user_id, projection_id, version_id
    )
    digest = evidence.get("sha256")
    byte_size = evidence.get("byte_size")
    mime_type = evidence.get("mime_type")
    if (
        not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest)
        or not isinstance(byte_size, int) or byte_size <= 0
        or asset.sha256 != digest or asset.byte_size != byte_size
        or asset.mime_type != mime_type
        or asset.kind.value != evidence.get("media_kind")
        or (
            asset.visual_class is not None
            and asset.visual_class.value != evidence.get("visual_class")
        )
    ):
        raise ValueError("provider-media execution evidence does not match the asset")

    blob_digest = evidence.get("original_blob_sha256")
    if blob_digest is not None:
        if (
            blob_digest != digest
            or evidence.get("original_blob_byte_size") != byte_size
            or not isinstance(evidence.get("original_signature_family"), str)
            or evidence.get("original_receipt_active") is None
        ):
            raise ValueError("provider-media original binding is invalid")
        media_store.verify_owned_object(
            owner_user_id, digest, byte_size, evidence["original_signature_family"]
        )
        path = (
            media_store.root / "owners" / media_store.owner_binding(owner_user_id)
            / "objects" / "sha256" / digest[:2] / digest
        )
    else:
        roots = tuple(Path(item).expanduser().absolute() for item in allowed_file_roots)
        path = _contained_file(str(evidence.get("source_uri", "")), roots)
    _stable_file(path, byte_size, digest)
    return path


__all__ = ["owned_provider_asset_uri", "resolve_owned_media_source"]
