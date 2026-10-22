"""Idempotently adopt one verified local H3 output into Castle's media spine."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from pathlib import Path
from typing import Mapping

from media_projects.output_binding import bind_verified_output
from media_projects.production_store import ProductionStateStore

from .h3_worker import H3QueueRequest
from .models import CharacterRef, MediaKind, VisualClass
from .store import ProviderFabricStore, StoreConflictError


def _stable_suffix(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:32]


def _stable_file(path: Path, expected_size: int) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size != expected_size
        ):
            raise ValueError("H3 output file identity changed")
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, 8 * 1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            total != expected_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ValueError("H3 output changed during adoption")
        return digest.hexdigest()
    finally:
        os.close(fd)


def adopt_h3_output(
    store: ProviderFabricStore,
    request: H3QueueRequest,
    receipt: Mapping[str, object],
    *,
    output_root: Path,
    production_store: ProductionStateStore | None = None,
) -> str:
    """Bind a receipt to its task, immutable bytes, character, and local account."""

    if not isinstance(store, ProviderFabricStore) or not isinstance(request, H3QueueRequest):
        raise TypeError("store and H3 queue request are required")
    task = store.get_owned_task(request.owner_user_id, request.task_id)
    if (
        task.get("provider_id") != "local_comfy"
        or task.get("client_request_id") != request.client_request_id
    ):
        raise ValueError("H3 receipt task identity changed")
    outbox = store.get_outbox(str(task["outbox_id"]))
    decision = store.get_route_decision(str(outbox["decision_id"]))
    if (
        decision.get("selected_provider_id") != "local_comfy"
        or decision.get("selected_variant_id") != request.variant_id
    ):
        raise ValueError("H3 receipt route identity changed")
    intent_record = store.get_intent(str(outbox["intent_id"]))
    intent = intent_record.get("intent")
    if not isinstance(intent, Mapping):
        raise ValueError("H3 intent is invalid")
    characters = intent.get("characters")
    if not isinstance(characters, list) or len(characters) != 1:
        raise ValueError("H3 output must retain one character lineage")
    character = characters[0]
    if not isinstance(character, Mapping) or not isinstance(character.get("character_id"), str):
        raise ValueError("H3 character identity is invalid")
    visual = VisualClass(str(intent.get("visual_class")))

    root = Path(output_root).resolve(strict=True)
    path = Path(str(receipt.get("output_path") or "")).resolve(strict=True)
    try:
        contained = os.path.commonpath((str(root), str(path))) == str(root)
    except ValueError:
        contained = False
    size = receipt.get("byte_size")
    digest = str(receipt.get("output_sha256") or "")
    if not contained or not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("H3 output escaped its trusted root")
    if not hmac.compare_digest(_stable_file(path, size), digest):
        raise ValueError("H3 output digest changed before adoption")

    prompt_id = str(receipt.get("prompt_id") or "")
    suffix = _stable_suffix(prompt_id, digest)
    projection_id = f"local-h3-output:{suffix}"
    version_id = f"assetver:{suffix}"
    account_id = str(task["account_id"])
    metadata = {
        "source": "local_minimax_h3",
        "intent_id": str(intent_record.get("intent_id") or ""),
        "task_id": request.task_id,
        "variant_id": request.variant_id,
        "graph_sha256": request.graph_sha256,
    }
    store.project_owned_remote_asset(
        request.owner_user_id,
        projection_id,
        account_id,
        f"h3:{prompt_id}",
        MediaKind.VIDEO,
        visual_class=visual,
        metadata=metadata,
    )
    asset = store.get_owned_remote_asset(request.owner_user_id, projection_id)
    versions = [item for item in asset["versions"] if item["version_id"] == version_id]
    if not versions:
        try:
            store.add_owned_remote_asset_version(
                request.owner_user_id,
                projection_id,
                path.as_uri(),
                version_id=version_id,
                version_number=1,
                provider_version_id=prompt_id,
                mime_type="video/mp4",
                byte_size=size,
                sha256=digest,
                metadata={"verified_local_generation": True},
            )
        except StoreConflictError:
            pass
        asset = store.get_owned_remote_asset(request.owner_user_id, projection_id)
        versions = [item for item in asset["versions"] if item["version_id"] == version_id]
    if len(versions) != 1 or any(
        versions[0].get(key) != value
        for key, value in {
            "version_number": 1, "mime_type": "video/mp4",
            "byte_size": size, "sha256": digest,
        }.items()
    ):
        raise StoreConflictError("H3 output adoption identity conflicts")
    try:
        store.put_owned_asset_catalog_annotation(
            request.owner_user_id,
            projection_id,
            display_name=None,
            audio_role="unclassified",
            media_role="finished",
            expected_revision=0,
        )
    except StoreConflictError:
        # Adoption is idempotent.  A later owner catalog edit is authoritative
        # and must not be overwritten when the same receipt is replayed.
        pass
    store.put_owned_character(
        request.owner_user_id,
        CharacterRef(
            str(character["character_id"]),
            visual,
            str(character.get("display_name") or character["character_id"]),
        ),
    )
    store.link_owned_asset_character(
        request.owner_user_id, projection_id, str(character["character_id"])
    )
    instruction = intent.get("creative_instruction")
    if production_store is not None and isinstance(instruction, Mapping):
        bind_verified_output(
            production_store,
            owner_user_id=request.owner_user_id,
            instruction=instruction,
            asset_version_id=version_id,
            asset_sha256=digest,
            task_id=request.task_id,
        )
    return projection_id


__all__ = ["adopt_h3_output"]
