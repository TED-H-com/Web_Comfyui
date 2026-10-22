"""Owner-scoped deterministic edit execution and delivery review receipts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from provider_fabric.media_materializer import PrivateProviderMediaStore
from provider_fabric.models import MediaAsset, MediaKind, VisualClass
from provider_fabric.owned_media_source import (
    owned_provider_asset_uri, resolve_owned_media_source,
)
from provider_fabric.store import ProviderFabricStore

from .edit_executor import EditReceipt, execute_edit_timeline
from .edit_timeline import compile_edit_timeline
from .production_state import ProductionPhase, checkpoint_delivery_review
from .production_store import ProductionStateStore, ProductionStoreConflict


EDIT_REQUEST_VERSION = "castle.production-edit-execute.v1"
EDIT_RESPONSE_VERSION = "castle.production-edit-execute.response.v1"
DELIVERY_REVIEW_VERSION = "castle.production-delivery-review.v1"


def _hash(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stable_file(path: Path, expected_sha256: str) -> int:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1:
            raise ValueError("production edit output is unsafe")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(fd, 8 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or digest.hexdigest() != expected_sha256
        ):
            raise ValueError("production edit output changed")
        return size
    finally:
        os.close(fd)


def _public_receipt(project_id: str, state_hash: str, receipt: EditReceipt) -> dict[str, object]:
    material = {
        "schema_version": "castle.production-edit-receipt.v5",
        "project_id": project_id,
        "production_state_hash": state_hash,
        "timeline_hash": receipt.timeline_hash,
        "output_sha256": receipt.output_sha256,
        "byte_size": receipt.byte_size,
        "duration_seconds": receipt.duration_seconds,
        "video": {"width": receipt.width, "height": receipt.height, "fps": receipt.fps},
        "subtitle_emitted": receipt.subtitle_path is not None,
        "subtitle_burned_in": receipt.subtitle_burned_in,
        "contact_sheet": {
            "sha256": receipt.contact_sheet_sha256,
            "byte_size": receipt.contact_sheet_byte_size,
            "width": receipt.contact_sheet_width,
            "height": receipt.contact_sheet_height,
        },
        "soundtrack_mixed": receipt.soundtrack_mixed,
        "soundtrack_ducking_applied": receipt.soundtrack_ducking_applied,
        "sound_effect_count": receipt.sound_effect_count,
        "mechanical_qa_passed": True,
    }
    return {**material, "receipt_hash": _hash(material)}


def execute_production_edit_api(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    media_store: PrivateProviderMediaStore,
    owner_user_id: str,
    project_id: str,
    body: Mapping[str, Any],
    *,
    output_root: Path,
    allowed_source_roots: tuple[Path, ...],
) -> dict[str, Any]:
    if (
        not isinstance(body, Mapping)
        or set(body) != {"schema_version", "expected_state_hash", "confirmed"}
        or body.get("schema_version") != EDIT_REQUEST_VERSION
        or body.get("confirmed") is not True
    ):
        raise ValueError("production edit execution request is invalid")
    state = production_store.get(owner_user_id, project_id)
    if state.state_hash != body.get("expected_state_hash"):
        raise ProductionStoreConflict("production changed before edit execution")
    if state.phase not in {ProductionPhase.EDITING, ProductionPhase.DELIVERY_REVIEW}:
        raise ValueError("production is not ready for deterministic editing")
    if state.phase is ProductionPhase.DELIVERY_REVIEW:
        prior = production_store.get_latest_edit_receipt_for_project(
            owner_user_id, project_id
        )
        prior_review = production_store.get_delivery_review(
            owner_user_id, project_id, str(prior.get("receipt_hash"))
        )
        if prior_review.get("decision") != "rejected":
            raise ValueError("delivery edit retry requires a rejected review")
    pack = production_store.get_pack(owner_user_id, project_id)
    timeline = compile_edit_timeline(pack, state)
    try:
        existing = production_store.get_edit_receipt(
            owner_user_id, project_id, state.state_hash
        )
    except KeyError:
        existing = None
    if existing is not None:
        if existing.get("timeline_hash") != timeline.timeline_hash:
            raise ProductionStoreConflict("stored edit receipt timeline differs")
        path = production_store.get_edit_output_path(
            owner_user_id, project_id, state.state_hash
        )
        size = _stable_file(path, str(existing.get("output_sha256") or ""))
        if size != existing.get("byte_size"):
            raise ProductionStoreConflict("stored edit output size differs")
        public = existing
    else:
        root = Path(output_root).expanduser().absolute()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("production edit root is unsafe")
        owner_dir = root / hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
        owner_dir.mkdir(mode=0o700, exist_ok=True)
        project_dir = owner_dir / project_id
        project_dir.mkdir(mode=0o700, exist_ok=True)

        def source_resolver(version_id: str) -> Path:
            evidence = provider_store.get_owned_remote_asset_version_source_by_id(
                owner_user_id, version_id
            )
            projection_id = str(evidence["projection_id"])
            projected = provider_store.get_owned_remote_asset(owner_user_id, projection_id)
            visual = projected.get("visual_class")
            asset = MediaAsset(
                asset_id=f"edit-source:{hashlib.sha256(version_id.encode()).hexdigest()}",
                kind=MediaKind(projected["media_kind"]),
                uri=owned_provider_asset_uri(projection_id, version_id),
                visual_class=VisualClass(visual) if visual else None,
                character_ids=tuple(projected.get("character_ids") or ()),
                mime_type=evidence.get("mime_type"),
                byte_size=evidence.get("byte_size"),
                sha256=evidence.get("sha256"),
                duration_seconds=evidence.get("duration_seconds"),
            )
            return resolve_owned_media_source(
                provider_store, media_store, owner_user_id, asset,
                allowed_file_roots=allowed_source_roots,
            )

        receipt = execute_edit_timeline(timeline, source_resolver, project_dir)
        public = _public_receipt(project_id, state.state_hash, receipt)
        receipt_hash = str(public["receipt_hash"])
        projection_id = f"production-edit:{receipt_hash[:32]}"
        version_id = f"assetver:production-edit:{receipt_hash[:32]}"
        visual_classes = {item.visual_class for item in pack.entities}
        visual_class = next(iter(visual_classes)) if len(visual_classes) == 1 else None
        provider_store.project_owned_remote_asset(
            owner_user_id, projection_id, "local-main",
            f"production-edit:{project_id}:{receipt_hash}", MediaKind.VIDEO,
            visual_class=visual_class,
            metadata={
                "title": f"{pack.project.title} · final edit",
                "production_project_id": project_id,
                "edit_receipt_hash": receipt_hash,
            },
        )
        projected = provider_store.get_owned_remote_asset(owner_user_id, projection_id)
        versions = [item for item in projected["versions"] if item["version_id"] == version_id]
        if not versions:
            provider_store.add_owned_remote_asset_version(
                owner_user_id, projection_id, receipt.output_path.as_uri(),
                version_id=version_id, mime_type="video/mp4",
                byte_size=receipt.byte_size, sha256=receipt.output_sha256,
                duration_seconds=receipt.duration_seconds,
                metadata={"timeline_hash": timeline.timeline_hash},
            )
        else:
            observed = versions[0]
            if (
                observed.get("sha256") != receipt.output_sha256
                or observed.get("byte_size") != receipt.byte_size
                or observed.get("mime_type") != "video/mp4"
            ):
                raise ProductionStoreConflict("production edit media version conflicts")
        character_ids = tuple(sorted({
            item.character_id for item in pack.entities if item.character_id is not None
        }))
        for character_id in character_ids:
            provider_store.link_owned_asset_character(
                owner_user_id, projection_id, character_id
            )
        for clip in timeline.clips:
            source = provider_store.get_owned_remote_asset_version_source_by_id(
                owner_user_id, clip.asset_version_id
            )
            provider_store.add_owned_asset_lineage(
                owner_user_id, str(source["projection_id"]), projection_id,
                "edit_input",
            )
        if timeline.soundtrack_asset_version_id is not None:
            source = provider_store.get_owned_remote_asset_version_source_by_id(
                owner_user_id, timeline.soundtrack_asset_version_id
            )
            provider_store.add_owned_asset_lineage(
                owner_user_id, str(source["projection_id"]), projection_id,
                "soundtrack_input",
            )
        for effect in timeline.sound_effects:
            source = provider_store.get_owned_remote_asset_version_source_by_id(
                owner_user_id, effect.asset_version_id
            )
            provider_store.add_owned_asset_lineage(
                owner_user_id, str(source["projection_id"]), projection_id,
                "sound_effect_input",
            )
        public = {**public, "output_asset_version_id": version_id}
        persisted = production_store.put_edit_receipt(
            owner_user_id, project_id, state.state_hash, public,
            output_path=receipt.output_path,
        )
        if persisted != public:
            raise ProductionStoreConflict("persisted edit receipt differs")
    return {
        "schema_version": EDIT_RESPONSE_VERSION,
        "ok": True,
        "receipt": {
            **public,
            "output_url": (
                f"/api/productions/{project_id}/edits/"
                f"{public['receipt_hash']}/output"
            ),
            "contact_sheet_url": (
                f"/api/productions/{project_id}/edits/"
                f"{public['receipt_hash']}/contact-sheet"
            ),
        },
        "authority": {
            "owner_scoped": True,
            "accepted_versions_server_resolved": True,
            "mechanical_qa_passed": True,
            "provider_transport_performed": False,
            "gpu_queue_authorized": False,
            "credits_spent": 0,
            "publishing_authorized": False,
        },
    }


def resolve_production_edit_output(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
    receipt_hash: str,
) -> tuple[Path, dict[str, object]]:
    receipt, path = store.get_edit_receipt_by_hash(
        owner_user_id, project_id, receipt_hash
    )
    size = _stable_file(path, str(receipt.get("output_sha256") or ""))
    if size != receipt.get("byte_size"):
        raise ProductionStoreConflict("production edit output size differs")
    return path, receipt


def resolve_production_contact_sheet(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
    receipt_hash: str,
) -> tuple[Path, dict[str, object]]:
    output, receipt = resolve_production_edit_output(
        store, owner_user_id, project_id, receipt_hash
    )
    evidence = receipt.get("contact_sheet")
    if not isinstance(evidence, Mapping):
        raise ProductionStoreConflict("production contact sheet evidence is missing")
    path = output.with_name(output.stem + ".contact.jpg")
    size = _stable_file(path, str(evidence.get("sha256") or ""))
    if size != evidence.get("byte_size"):
        raise ProductionStoreConflict("production contact sheet size differs")
    return path, receipt


def review_production_delivery_api(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(body, Mapping)
        or set(body) != {"schema_version", "expected_state_hash", "receipt_hash", "decision", "note"}
        or body.get("schema_version") != DELIVERY_REVIEW_VERSION
        or body.get("decision") not in {"accepted", "rejected"}
        or not isinstance(body.get("note"), str)
        or len(body["note"]) > 1000
    ):
        raise ValueError("production delivery review request is invalid")
    state = store.get(owner_user_id, project_id)
    if state.state_hash != body.get("expected_state_hash"):
        try:
            replay = store.get_delivery_review(
                owner_user_id, project_id, str(body.get("receipt_hash") or "")
            )
        except KeyError:
            replay = None
        if (
            replay is None or state.previous_state_hash != body.get("expected_state_hash")
            or replay.get("production_state_hash") != body.get("expected_state_hash")
            or replay.get("decision") != body.get("decision")
            or replay.get("note") != body.get("note")
        ):
            raise ProductionStoreConflict("production changed before delivery review")
        return {
            "schema_version": "castle.production-delivery-review.response.v1",
            "ok": True, "review": replay,
            "production_state_hash": state.state_hash,
            "authority": {"owner_scoped": True, "publishing_authorized": False},
        }
    if state.phase is not ProductionPhase.DELIVERY_REVIEW or state.previous_state_hash is None:
        raise ValueError("production is not open for delivery review")
    if body["decision"] == "rejected" and not body["note"].strip():
        raise ValueError("rejected delivery review requires a note")
    receipt = store.get_edit_receipt(owner_user_id, project_id, state.previous_state_hash)
    if receipt.get("receipt_hash") != body.get("receipt_hash"):
        raise ValueError("delivery review receipt differs from the assembled edit")
    material = {
        "schema_version": DELIVERY_REVIEW_VERSION,
        "project_id": project_id,
        "production_state_hash": state.state_hash,
        "receipt_hash": body["receipt_hash"],
        "decision": body["decision"],
        "note": body["note"],
    }
    review = {**material, "review_hash": _hash(material)}
    persisted = store.put_delivery_review(
        owner_user_id, project_id, str(body["receipt_hash"]), review
    )
    if persisted != review:
        raise ProductionStoreConflict("persisted delivery review differs")
    checkpoint = store.append(
        checkpoint_delivery_review(state), expected_previous_hash=state.state_hash
    )
    return {
        "schema_version": "castle.production-delivery-review.response.v1",
        "ok": True,
        "review": review,
        "production_state_hash": checkpoint.state_hash,
        "authority": {"owner_scoped": True, "publishing_authorized": False},
    }


__all__ = [
    "DELIVERY_REVIEW_VERSION", "EDIT_REQUEST_VERSION", "EDIT_RESPONSE_VERSION",
    "execute_production_edit_api", "review_production_delivery_api",
    "resolve_production_contact_sheet", "resolve_production_edit_output",
]
