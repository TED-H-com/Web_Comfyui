"""Owner-scoped local source resolver for unified-library playback."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .media_materializer import PrivateProviderMediaStore
from .models import MediaAsset, MediaKind, VisualClass
from .owned_media_source import (
    owned_provider_asset_uri,
    resolve_owned_media_source as resolve_trusted_owned_media_source,
)
from .store import ProviderFabricStore, ProviderFabricStoreError


_MIME_KIND = {
    "image/avif": "image", "image/gif": "image", "image/jpeg": "image",
    "image/png": "image", "image/webp": "image",
    "video/mp4": "video", "video/quicktime": "video", "video/webm": "video",
    "audio/flac": "audio", "audio/mpeg": "audio", "audio/mp4": "audio",
    "audio/ogg": "audio", "audio/wav": "audio", "audio/x-wav": "audio",
}
_EXTENSION = {
    "image/avif": ".avif", "image/gif": ".gif", "image/jpeg": ".jpg",
    "image/png": ".png", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "audio/flac": ".flac", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
}
_LOCK = threading.Lock()
_VERIFIED: OrderedDict[tuple[str, int, int, str], None] = OrderedDict()


@dataclass(frozen=True, slots=True)
class OwnedMediaSource:
    projection_id: str
    version_id: str
    path: Path
    mime_type: str
    media_kind: str
    byte_size: int
    sha256: str
    filename: str


def _verified_file(path: Path, byte_size: int, digest: str) -> None:
    info = path.stat()
    if not path.is_file() or info.st_size != byte_size:
        raise ProviderFabricStoreError("owned media source changed size")
    key = (str(path), byte_size, info.st_mtime_ns, digest)
    with _LOCK:
        if key in _VERIFIED:
            _VERIFIED.move_to_end(key)
            return
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    if not hmac.compare_digest(hasher.hexdigest(), digest):
        raise ProviderFabricStoreError("owned media source digest changed")
    with _LOCK:
        _VERIFIED[key] = None
        _VERIFIED.move_to_end(key)
        while len(_VERIFIED) > 128:
            _VERIFIED.popitem(last=False)


def resolve_owned_media_source(
    store: ProviderFabricStore,
    media_store: PrivateProviderMediaStore,
    owner_user_id: str,
    projection_id: str,
    *,
    version_id: str | None = None,
    allowed_file_roots: tuple[Path, ...] = (),
) -> OwnedMediaSource:
    asset = store.get_owned_remote_asset(owner_user_id, projection_id)
    versions = asset.get("versions")
    if not isinstance(versions, list) or not versions:
        raise KeyError(projection_id)
    selected = (
        [item for item in versions if item.get("version_id") == version_id]
        if version_id is not None else [max(versions, key=lambda item: item.get("version_number", 0))]
    )
    if len(selected) != 1:
        raise KeyError(version_id or projection_id)
    version = selected[0]
    exact_version_id = version.get("version_id")
    source = store.get_owned_remote_asset_version_source(
        owner_user_id, projection_id, exact_version_id
    )
    mime_type = source.get("mime_type")
    media_kind = source.get("media_kind")
    byte_size = source.get("byte_size")
    digest = source.get("sha256")
    if (
        source.get("projection_id") != projection_id
        or source.get("version_id") != exact_version_id
        or mime_type not in _MIME_KIND or media_kind != _MIME_KIND[mime_type]
        or not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size <= 0
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or version.get("sha256") != digest or version.get("byte_size") != byte_size
    ):
        raise ProviderFabricStoreError("owned media source metadata is inconsistent")
    visual_class = asset.get("visual_class")
    try:
        media_asset = MediaAsset(
            asset_id=f"library-source:{hashlib.sha256(exact_version_id.encode()).hexdigest()}",
            kind=MediaKind(media_kind),
            uri=owned_provider_asset_uri(projection_id, exact_version_id),
            visual_class=VisualClass(visual_class) if visual_class else None,
            character_ids=tuple(asset.get("character_ids") or ()),
            mime_type=mime_type, byte_size=byte_size, sha256=digest,
            duration_seconds=source.get("duration_seconds"),
        )
        path = resolve_trusted_owned_media_source(
            store, media_store, owner_user_id, media_asset,
            allowed_file_roots=allowed_file_roots,
        )
    except (TypeError, ValueError) as error:
        raise ProviderFabricStoreError("owned media has no safe local source") from error
    _verified_file(path, byte_size, digest)
    extension = _EXTENSION.get(mime_type) or mimetypes.guess_extension(mime_type) or ""
    return OwnedMediaSource(
        projection_id=projection_id, version_id=exact_version_id, path=path,
        mime_type=mime_type, media_kind=media_kind, byte_size=byte_size,
        sha256=digest, filename=f"web-comfyui-media-{projection_id}{extension}",
    )


__all__ = ["OwnedMediaSource", "resolve_owned_media_source"]
