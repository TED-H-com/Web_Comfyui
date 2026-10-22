"""Adopt one owner-authorized local Castle history file into the media spine."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from pathlib import Path

from .media_signatures import SIGNATURE_PROBE_BYTES, classify_declared_media
from .models import CharacterRef, MediaKind, VisualClass
from .store import ProviderFabricStore, StoreConflictError


_MIME_KIND = {
    "image/jpeg": MediaKind.IMAGE,
    "image/png": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE,
    "video/mp4": MediaKind.VIDEO,
    "video/webm": MediaKind.VIDEO,
    "audio/mpeg": MediaKind.AUDIO,
    "audio/mp4": MediaKind.AUDIO,
    "audio/wav": MediaKind.AUDIO,
    "audio/x-wav": MediaKind.AUDIO,
    "audio/flac": MediaKind.AUDIO,
    "audio/ogg": MediaKind.AUDIO,
}


def _hash_file(path: Path) -> tuple[str, int, bytes]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
            raise ValueError("local history source is not one stable regular file")
        digest = hashlib.sha256()
        total = 0
        probe = bytearray()
        while chunk := os.read(fd, 8 * 1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
            if len(probe) < SIGNATURE_PROBE_BYTES:
                probe.extend(chunk[:SIGNATURE_PROBE_BYTES - len(probe)])
        after = os.fstat(fd)
        if (
            total != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ValueError("local history source changed during adoption")
        return digest.hexdigest(), total, bytes(probe)
    finally:
        os.close(fd)


def _suffix(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:32]


def adopt_local_history_output(
    store: ProviderFabricStore,
    *,
    owner_user_id: str,
    account_id: str,
    source_path: Path,
    source_identity: str,
    mime_type: str,
    display_name: str,
    character: CharacterRef | None = None,
    audio_role: str = "unclassified",
) -> dict[str, object]:
    """Create/reuse an immutable version after the HTTP layer proves access."""

    if not isinstance(store, ProviderFabricStore):
        raise TypeError("provider store is required")
    if not isinstance(source_identity, str) or not source_identity or len(source_identity) > 1400:
        raise ValueError("local history source identity is invalid")
    kind = _MIME_KIND.get(mime_type)
    if kind is None:
        raise ValueError("local history media type is unsupported")
    if audio_role not in {"unclassified", "music", "sound_effect", "voice", "ambience"}:
        raise ValueError("local history audio role is invalid")
    if kind is not MediaKind.AUDIO and audio_role != "unclassified":
        raise ValueError("non-audio local history cannot have an audio role")
    supplied_path = Path(source_path)
    if supplied_path.is_symlink():
        raise ValueError("local history source cannot be a symlink")
    path = supplied_path.resolve(strict=True)
    digest, byte_size, probe = _hash_file(path)
    if classify_declared_media(mime_type, probe) is None:
        raise ValueError("local history media signature does not match its type")
    projection_id = f"local-history:{_suffix(owner_user_id, source_identity)}"
    version_id = f"assetver:{_suffix(projection_id, digest)}"
    history_key = f"local-history:{_suffix(source_identity)}"
    visual = character.visual_class if character is not None else VisualClass.UNKNOWN
    store.project_owned_remote_asset(
        owner_user_id,
        projection_id,
        account_id,
        f"castle-history:{_suffix(source_identity)}",
        kind,
        visual_class=visual,
        metadata={
            "source": "castle_local_history",
            "local_history_key": history_key,
        },
    )
    asset = store.get_owned_remote_asset(owner_user_id, projection_id)
    matching = [item for item in asset["versions"] if item["version_id"] == version_id]
    if not matching:
        next_version = len(asset["versions"]) + 1
        try:
            store.add_owned_remote_asset_version(
                owner_user_id,
                projection_id,
                path.as_uri(),
                version_id=version_id,
                version_number=next_version,
                provider_version_id=digest,
                mime_type=mime_type,
                byte_size=byte_size,
                sha256=digest,
                metadata={"verified_local_history": True},
            )
        except StoreConflictError:
            pass
        asset = store.get_owned_remote_asset(owner_user_id, projection_id)
        matching = [item for item in asset["versions"] if item["version_id"] == version_id]
    if len(matching) != 1 or any(
        matching[0].get(key) != value
        for key, value in {"mime_type": mime_type, "byte_size": byte_size, "sha256": digest}.items()
    ):
        raise StoreConflictError("local history adoption identity conflicts")
    if character is not None:
        store.put_owned_character(owner_user_id, character)
        store.link_owned_asset_character(owner_user_id, projection_id, character.character_id)
    try:
        store.put_owned_asset_catalog_annotation(
            owner_user_id,
            projection_id,
            display_name=display_name,
            audio_role=audio_role,
            media_role=("finished" if kind in {MediaKind.IMAGE, MediaKind.VIDEO} else "unclassified"),
            expected_revision=0,
        )
    except StoreConflictError:
        pass
    final = store.get_owned_remote_asset(owner_user_id, projection_id)
    selected = [item for item in final["versions"] if item["version_id"] == version_id]
    if len(selected) != 1 or not hmac.compare_digest(str(selected[0]["sha256"]), digest):
        raise StoreConflictError("local history adopted version changed")
    return {
        "projection_id": projection_id,
        "asset_version_id": version_id,
        "media_kind": kind.value,
        "visual_class": visual.value,
        "character_ids": list(final["character_ids"]),
        "sha256": digest,
        "byte_size": byte_size,
    }


__all__ = ["adopt_local_history_output"]
