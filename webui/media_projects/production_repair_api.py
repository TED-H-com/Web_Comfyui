"""Create an immutable retry preparation from a rejected latest shot output."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .production_state import ProductionPhase, ReviewDecision
from .production_store import ProductionStateStore, ProductionStoreConflict


REPAIR_REQUEST_VERSION = "castle.production-shot-repair.v1"
REPAIR_RESPONSE_VERSION = "castle.production-shot-repair.response.v1"
REPAIR_PREPARATION_VERSION = "castle.production-shot-repair-preparation.v1"


def _hash(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prepare_rejected_shot_repair_api(
    store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
    shot_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(body, Mapping)
        or set(body) != {"schema_version", "expected_state_hash", "rejected_version"}
        or body.get("schema_version") != REPAIR_REQUEST_VERSION
        or isinstance(body.get("rejected_version"), bool)
        or not isinstance(body.get("rejected_version"), int)
    ):
        raise ValueError("production repair request is invalid")
    state = store.get(owner_user_id, project_id)
    if state.state_hash != body.get("expected_state_hash"):
        raise ProductionStoreConflict("production changed before repair preparation")
    if state.phase is not ProductionPhase.STORY_REVIEW:
        raise ValueError("shot repair is only available during story review")
    versions = state.outputs.get(shot_id)
    rejected_version = body["rejected_version"]
    if (
        not versions or rejected_version != len(versions)
        or versions[-1].version != rejected_version
        or versions[-1].decision is not ReviewDecision.REJECTED
        or not versions[-1].review_note.strip()
    ):
        raise ValueError("repair requires the rejected latest shot output")
    base = store.get_latest_preparation(owner_user_id, project_id, shot_id)
    if (
        base.get("schema_version") == REPAIR_PREPARATION_VERSION
        and base.get("production_state_hash") == state.state_hash
        and base.get("rejected_version") == rejected_version
        and base.get("rejected_asset_version_id") == versions[-1].asset_version_id
    ):
        return {
            "schema_version": REPAIR_RESPONSE_VERSION,
            "ok": True,
            "preparation": base,
            "authority": {
                "owner_scoped": True,
                "rejected_output_server_resolved": True,
                "provider_submission_authorized": False,
                "gpu_queue_authorized": False,
                "credits_authorized": 0,
            },
        }
    base_hash = base.get("preparation_hash")
    if not isinstance(base_hash, str):
        raise ProductionStoreConflict("base shot preparation is invalid")
    base_context = store.get_preparation_context(
        owner_user_id, project_id, base_hash
    )
    material = {
        "schema_version": REPAIR_PREPARATION_VERSION,
        "project_id": project_id,
        "shot_id": shot_id,
        "base_preparation_hash": base_hash,
        "production_state_hash": state.state_hash,
        "rejected_version": rejected_version,
        "rejected_asset_version_id": versions[-1].asset_version_id,
        "rejected_route_variant_id": versions[-1].route_variant_id,
        "review_note": versions[-1].review_note,
    }
    preparation_hash = _hash(material)
    preparation = {**material, "preparation_hash": preparation_hash}
    raw_inputs = base_context.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ProductionStoreConflict("base execution inputs are unavailable")
    repair_inputs = []
    for item in raw_inputs:
        if not isinstance(item, Mapping):
            raise ProductionStoreConflict("base execution input is malformed")
        rebuilt = {
            key: item.get(key) for key in (
                "resource_id", "projection_id", "asset_version_id", "field_name",
            )
        }
        if any(not isinstance(value, str) or not value for value in rebuilt.values()):
            raise ProductionStoreConflict("base execution input is malformed")
        execution_hash = _hash({
            "repair_preparation_hash": preparation_hash,
            **rebuilt,
        })
        repair_inputs.append({
            **rebuilt,
            "execution_asset_id": f"production-media:{execution_hash}",
        })
    context = {
        **base_context,
        "production_state_hash": state.state_hash,
        "inputs": repair_inputs,
        "repair": {
            "schema_version": "castle.production-shot-repair-context.v1",
            "base_preparation_hash": base_hash,
            "rejected_version": rejected_version,
            "rejected_asset_version_id": versions[-1].asset_version_id,
            "review_note": versions[-1].review_note,
        },
    }
    persisted = store.put_preparation(
        owner_user_id, project_id, shot_id=shot_id,
        production_state_hash=state.state_hash,
        preparation=preparation, context=context,
    )
    if persisted != preparation:
        raise ProductionStoreConflict("persisted repair preparation differs")
    return {
        "schema_version": REPAIR_RESPONSE_VERSION,
        "ok": True,
        "preparation": preparation,
        "authority": {
            "owner_scoped": True,
            "rejected_output_server_resolved": True,
            "provider_submission_authorized": False,
            "gpu_queue_authorized": False,
            "credits_authorized": 0,
        },
    }


__all__ = [
    "REPAIR_PREPARATION_VERSION", "REPAIR_REQUEST_VERSION",
    "REPAIR_RESPONSE_VERSION", "prepare_rejected_shot_repair_api",
]
