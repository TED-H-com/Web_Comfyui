"""Framework-neutral owner-scoped API contract for durable production review."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .contracts import parse_production_pack
from .lint import lint_production_pack
from .production_state import (
    ProductionPhase, ReviewDecision, advance_production, review_shot_output,
    start_production,
)
from .production_store import ProductionStateStore


CREATE_VERSION = "castle.production-create.v1"
MUTATION_VERSION = "castle.production-mutation.v1"
RESPONSE_VERSION = "castle.production.response.v1"
RESUME_RESPONSE_VERSION = "castle.production-resume.response.v1"
LIST_RESPONSE_VERSION = "castle.production-list.response.v1"
PHASE_CONFIRMATION_VERSION = "castle.production-phase-confirmation.v1"

_PHASE_ACKNOWLEDGEMENTS = {
    ProductionPhase.PLANNED: "scope_rights_budget_reviewed",
    ProductionPhase.ASSETS_LOCKED: "immutable_assets_selected",
    ProductionPhase.GENERATING: "open_generation_window_without_spend",
    ProductionPhase.STORY_REVIEW: "all_shot_outputs_received",
    ProductionPhase.EDITING: "all_shots_accepted",
    ProductionPhase.DELIVERY_REVIEW: "mechanical_delivery_qa_passed",
    ProductionPhase.READY: "final_delivery_review_accepted",
}


def _phase_confirmation_hash(
    state, phase: ProductionPhase, confirmation: object,
) -> str:
    expected = _PHASE_ACKNOWLEDGEMENTS.get(phase)
    if (
        expected is None or not isinstance(confirmation, Mapping)
        or set(confirmation) != {"schema_version", "pack_hash", "acknowledgement"}
        or confirmation.get("schema_version") != PHASE_CONFIRMATION_VERSION
        or confirmation.get("pack_hash") != state.pack_hash
        or confirmation.get("acknowledgement") != expected
    ):
        raise ValueError("production phase confirmation is invalid")
    material = {
        "schema_version": PHASE_CONFIRMATION_VERSION,
        "owner_user_id": state.owner_user_id,
        "project_id": state.project_id,
        "from_state_hash": state.state_hash,
        "target_phase": phase.value,
        "confirmation": dict(confirmation),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _postproduction(store: ProductionStateStore, state) -> dict[str, Any] | None:
    try:
        receipt = store.get_latest_edit_receipt_for_project(
            state.owner_user_id, state.project_id
        )
    except KeyError:
        return None
    if state.phase not in {
        ProductionPhase.EDITING, ProductionPhase.DELIVERY_REVIEW,
        ProductionPhase.READY,
    }:
        return None
    receipt_hash = str(receipt.get("receipt_hash") or "")
    try:
        review = store.get_delivery_review(
            state.owner_user_id, state.project_id, receipt_hash
        )
    except KeyError:
        review = None
    return {
        "receipt": {
            **receipt,
            "output_url": (
                f"/api/productions/{state.project_id}/edits/"
                f"{receipt_hash}/output"
            ),
            **({"contact_sheet_url": (
                f"/api/productions/{state.project_id}/edits/"
                f"{receipt_hash}/contact-sheet"
            )} if receipt.get("schema_version") in {
                "castle.production-edit-receipt.v2", "castle.production-edit-receipt.v3",
                "castle.production-edit-receipt.v4",
                "castle.production-edit-receipt.v5",
            } else {}),
        },
        "delivery_review": review,
    }


def production_response(
    state, *, store: ProductionStateStore | None = None,
) -> dict[str, Any]:
    shots = []
    for shot_id in state.shot_ids:
        versions = state.outputs[shot_id]
        shots.append({
            "shot_id": shot_id,
            "accepted_version": next(
                (item.version for item in versions if item.decision is ReviewDecision.ACCEPTED),
                None,
            ),
            "versions": [{
                "version": item.version,
                "asset_version_id": item.asset_version_id,
                "route_variant_id": item.route_variant_id,
                "decision": item.decision.value if item.decision else None,
                "review_note": item.review_note,
            } for item in versions],
        })
    return {
        "schema_version": RESPONSE_VERSION,
        "ok": True,
        "production": {
            "project_id": state.project_id,
            "pack_hash": state.pack_hash,
            "phase": state.phase.value,
            "revision": state.revision,
            "state_hash": state.state_hash,
            "completed_gates": list(state.phase_receipts),
            "shots": shots,
            "postproduction": _postproduction(store, state) if store is not None else None,
        },
        "authority": {
            "owner_scoped": True,
            "provider_submission_authorized": False,
            "gpu_execution_authorized": False,
            "publishing_authorized": False,
        },
    }


def create_production_api(
    store: ProductionStateStore,
    owner_user_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(body, Mapping) or set(body) != {"schema_version", "production_pack"}:
        raise ValueError("production create request is invalid")
    if body.get("schema_version") != CREATE_VERSION:
        raise ValueError("production create version is unsupported")
    pack = parse_production_pack(body["production_pack"])
    report = lint_production_pack(pack)
    if report.fails:
        raise ValueError("production pack has blocking findings")
    return production_response(store.create_with_pack(
        start_production(pack, owner_user_id), pack,
    ), store=store)


def get_production_api(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
) -> dict[str, Any]:
    return production_response(store.get(owner_user_id, project_id), store=store)


def list_productions_api(
    store: ProductionStateStore,
    owner_user_id: str,
) -> dict[str, Any]:
    """Return resumable summaries without exposing packs, prompts, or locators."""

    items = []
    for state, pack, updated_at in store.list_latest(owner_user_id):
        items.append({
            "project_id": state.project_id,
            "title": pack.project.title,
            "phase": state.phase.value,
            "revision": state.revision,
            "state_hash": state.state_hash,
            "pack_hash": state.pack_hash,
            "shot_count": len(state.shot_ids),
            "completed_shot_count": sum(bool(state.outputs[shot_id]) for shot_id in state.shot_ids),
            "accepted_shot_count": sum(
                any(item.decision is ReviewDecision.ACCEPTED for item in state.outputs[shot_id])
                for shot_id in state.shot_ids
            ),
            "updated_at": updated_at,
        })
    return {
        "schema_version": LIST_RESPONSE_VERSION,
        "ok": True,
        "items": items,
        "authority": {
            "owner_scoped": True,
            "provider_submission_authorized": False,
            "gpu_execution_authorized": False,
            "publishing_authorized": False,
        },
    }


def resume_production_api(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Return the owner's immutable pack with its latest resumable state."""

    state = store.get(owner_user_id, project_id)
    pack = store.get_pack(owner_user_id, project_id)
    if state.pack_hash != pack.content_hash:
        from .production_store import ProductionStoreConflict
        raise ProductionStoreConflict("production pack and state differ")
    response = production_response(state, store=store)
    return {
        "schema_version": RESUME_RESPONSE_VERSION,
        "ok": True,
        "production_pack": pack.to_dict(),
        "production": response["production"],
        "authority": response["authority"],
    }


def mutate_production_api(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(body, Mapping) or set(body) != {
        "schema_version", "expected_state_hash", "operation",
    }:
        raise ValueError("production mutation request is invalid")
    if body.get("schema_version") != MUTATION_VERSION:
        raise ValueError("production mutation version is unsupported")
    expected = body.get("expected_state_hash")
    operation = body.get("operation")
    if not isinstance(expected, str) or not isinstance(operation, Mapping):
        raise ValueError("production mutation identity is invalid")
    current = store.get(owner_user_id, project_id)
    if current.state_hash != expected:
        from .production_store import ProductionStoreConflict
        raise ProductionStoreConflict("production state changed since it was loaded")
    kind = operation.get("kind")
    if kind == "advance" and set(operation) == {"kind", "phase", "confirmation"}:
        phase = ProductionPhase(operation["phase"])
        if phase is ProductionPhase.DELIVERY_REVIEW:
            receipt = store.get_edit_receipt(owner_user_id, project_id, current.state_hash)
            if receipt.get("mechanical_qa_passed") is not True:
                raise ValueError("delivery review requires a passed edit receipt")
        elif phase is ProductionPhase.READY:
            receipt = store.get_latest_edit_receipt_for_project(
                owner_user_id, project_id
            )
            review = store.get_delivery_review(
                owner_user_id, project_id, str(receipt.get("receipt_hash"))
            )
            if review.get("decision") != "accepted":
                raise ValueError("ready requires an accepted delivery review")
        changed = advance_production(
            current, phase,
            confirmation_hash=_phase_confirmation_hash(
                current, phase, operation["confirmation"]
            ),
        )
    elif kind == "review_shot" and set(operation) == {
        "kind", "shot_id", "version", "decision", "note",
    }:
        changed = review_shot_output(
            current,
            shot_id=operation["shot_id"],
            version=operation["version"],
            decision=ReviewDecision(operation["decision"]),
            note=operation["note"],
        )
    else:
        raise ValueError("production mutation operation is unsupported")
    return production_response(
        store.append(changed, expected_previous_hash=expected), store=store
    )


__all__ = [
    "CREATE_VERSION", "MUTATION_VERSION", "PHASE_CONFIRMATION_VERSION", "RESPONSE_VERSION",
    "RESUME_RESPONSE_VERSION", "create_production_api", "get_production_api",
    "resume_production_api", "mutate_production_api",
    "production_response",
]
