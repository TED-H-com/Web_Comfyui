"""Owner-scoped review media for verified production outputs."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from provider_fabric.store import ProviderFabricStore

from .production_store import ProductionStateStore, ProductionStoreConflict


RESPONSE_VERSION = "castle.production-output-media.response.v1"
_MIME_KIND = {
    "image/jpeg": "image", "image/png": "image", "image/webp": "image",
    "video/mp4": "video", "video/quicktime": "video", "video/webm": "video",
    "audio/flac": "audio", "audio/mpeg": "audio", "audio/mp4": "audio",
    "audio/ogg": "audio", "audio/wav": "audio", "audio/x-wav": "audio",
}
_EXTENSION = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "audio/flac": ".flac", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
}
_HASH = re.compile(r"^[0-9a-f]{64}$")
_VERIFY_LOCK = threading.Lock()
_VERIFIED: OrderedDict[tuple[str, int, int, str], None] = OrderedDict()


@dataclass(frozen=True, slots=True)
class ProductionOutputMedia:
    path: Path
    mime_type: str
    media_kind: str
    byte_size: int
    filename: str


def _version(state, shot_id: str, version: int):
    if shot_id not in state.outputs:
        raise KeyError(shot_id)
    found = [item for item in state.outputs[shot_id] if item.version == version]
    if len(found) != 1:
        raise KeyError(version)
    return found[0]


def _source(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    owner_user_id: str,
    project_id: str,
    shot_id: str,
    version: int,
):
    state = production_store.get(owner_user_id, project_id)
    output = _version(state, shot_id, version)
    source = provider_store.get_owned_remote_asset_version_source_by_id(
        owner_user_id, output.asset_version_id
    )
    if (
        source.get("version_id") != output.asset_version_id
        or source.get("sha256") != output.asset_sha256
        or not isinstance(source.get("byte_size"), int)
        or isinstance(source.get("byte_size"), bool)
        or source["byte_size"] <= 0
        or _HASH.fullmatch(output.asset_sha256) is None
        or source.get("mime_type") not in _MIME_KIND
        or source.get("media_kind") != _MIME_KIND[source["mime_type"]]
    ):
        raise ProductionStoreConflict("production output media differs from its verified binding")
    return state, output, source


def list_production_output_media_api(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    owner_user_id: str,
    project_id: str,
) -> dict[str, Any]:
    state = production_store.get(owner_user_id, project_id)
    items = []
    for shot_id in state.shot_ids:
        for output in state.outputs[shot_id]:
            _current, _bound, source = _source(
                production_store, provider_store, owner_user_id, project_id,
                shot_id, output.version,
            )
            items.append({
                "shot_id": shot_id,
                "version": output.version,
                "asset_version_id": output.asset_version_id,
                "media_kind": source["media_kind"],
                "mime_type": source["mime_type"],
                "byte_size": source["byte_size"],
                "output_url": (
                    f"/api/productions/{project_id}/shots/{shot_id}/"
                    f"versions/{output.version}/output"
                ),
            })
    return {
        "schema_version": RESPONSE_VERSION,
        "ok": True,
        "project_id": project_id,
        "pack_hash": state.pack_hash,
        "state_hash": state.state_hash,
        "items": items,
        "authority": {
            "owner_scoped": True,
            "verified_output_binding_checked": True,
            "private_source_locator_disclosed": False,
            "provider_transport_performed": False,
            "credits_spent": 0,
        },
    }


def _verify_file(path: Path, byte_size: int, digest: str) -> None:
    info = path.stat()
    if not path.is_file() or info.st_size != byte_size:
        raise ProductionStoreConflict("production output file changed")
    key = (str(path), byte_size, info.st_mtime_ns, digest)
    with _VERIFY_LOCK:
        if key in _VERIFIED:
            _VERIFIED.move_to_end(key)
            return
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    if not hmac.compare_digest(hasher.hexdigest(), digest):
        raise ProductionStoreConflict("production output file digest changed")
    with _VERIFY_LOCK:
        _VERIFIED[key] = None
        _VERIFIED.move_to_end(key)
        while len(_VERIFIED) > 128:
            _VERIFIED.popitem(last=False)


def resolve_production_output_media(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    owner_user_id: str,
    project_id: str,
    shot_id: str,
    version: int,
) -> ProductionOutputMedia:
    _state, output, source = _source(
        production_store, provider_store, owner_user_id, project_id, shot_id, version
    )
    parsed = urlsplit(str(source.get("source_uri") or ""))
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
        raise ProductionStoreConflict("production output has no safe local review source")
    path = Path(unquote(parsed.path)).resolve(strict=True)
    _verify_file(path, source["byte_size"], output.asset_sha256)
    mime_type = source["mime_type"]
    extension = _EXTENSION.get(mime_type) or mimetypes.guess_extension(mime_type) or ""
    return ProductionOutputMedia(
        path=path, mime_type=mime_type, media_kind=source["media_kind"],
        byte_size=source["byte_size"],
        filename=f"castle-{shot_id}-v{version}{extension}",
    )


__all__ = [
    "ProductionOutputMedia", "RESPONSE_VERSION",
    "list_production_output_media_api", "resolve_production_output_media",
]
