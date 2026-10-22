"""Owner-scoped projection of durable provider intents into one production."""

from __future__ import annotations

import re
from typing import Any

from provider_fabric.store import ProviderFabricStore

from .production_store import ProductionStateStore, ProductionStoreConflict


RESPONSE_VERSION = "castle.production-intents.response.v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")


def list_production_intents_api(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    owner_user_id: str,
    project_id: str,
) -> dict[str, Any]:
    state = production_store.get(owner_user_id, project_id)
    pack = production_store.get_pack(owner_user_id, project_id)
    page = provider_store.list_owned_production_intent_statuses(
        owner_user_id, project_id
    )
    routes = {item.route_variant_id: item for item in pack.route_variants}
    shots = {item.shot_id: item for item in pack.shots}
    items = []
    for raw in page["items"]:
        binding = raw["binding"]
        status = raw["status"]
        shot = shots.get(binding.get("shot_id"))
        route = routes.get(binding.get("route_variant_id"))
        if (
            binding.get("project_id") != project_id
            or binding.get("pack_hash") != pack.content_hash
            or not isinstance(binding.get("state_hash"), str)
            or _HASH.fullmatch(binding["state_hash"]) is None
            or not production_store.contains_revision(
                owner_user_id, project_id, state_hash=binding["state_hash"],
                pack_hash=pack.content_hash,
            )
            or shot is None or route is None
            or route.route_variant_id not in shot.eligible_visual_variant_ids
            or status.get("provider_id") != route.provider_id
            or status.get("capability") != shot.required_capability.value
            or status.get("visual_class") not in {item.value for item in route.visual_classes}
        ):
            raise ProductionStoreConflict("production intent lineage differs from its pack")
        items.append({
            "shot_id": shot.shot_id,
            "route_variant_id": route.route_variant_id,
            "production_state_hash": binding["state_hash"],
            "status": status,
        })
    return {
        "schema_version": RESPONSE_VERSION,
        "ok": True,
        "project_id": project_id,
        "pack_hash": pack.content_hash,
        "current_state_hash": state.state_hash,
        "items": items,
        "truncated": page["truncated"],
        "authority": {
            "owner_scoped": True,
            "status_check_transport_performed": False,
            "status_check_credits_spent": 0,
            "retry_authorized": False,
        },
    }


__all__ = ["RESPONSE_VERSION", "list_production_intents_api"]
