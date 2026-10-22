"""Create an authoritative provider quote from one immutable preparation."""

from __future__ import annotations

from typing import Any, Mapping

from provider_fabric.service import ActorContext, ProviderFabricService
from provider_fabric.store import ProviderFabricStore

from .production_state import ProductionPhase
from .production_store import ProductionStateStore, ProductionStoreConflict


QUOTE_REQUEST_VERSION = "castle.production-shot-quote.v1"
QUOTE_RESPONSE_VERSION = "castle.production-shot-quote.response.v1"


def quote_prepared_production_shot_api(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    service: ProviderFabricService,
    actor: ActorContext,
    project_id: str,
    shot_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild exact public plan references and obtain a zero-write quote."""

    if (
        not isinstance(body, Mapping)
        or set(body) != {"schema_version", "expected_state_hash", "preparation_hash"}
        or body.get("schema_version") != QUOTE_REQUEST_VERSION
    ):
        raise ValueError("production quote request is invalid")
    state = production_store.get(actor.user_id, project_id)
    if state.state_hash != body.get("expected_state_hash"):
        raise ProductionStoreConflict("production changed before quoting")
    if state.phase not in {
        ProductionPhase.ASSETS_LOCKED, ProductionPhase.GENERATING,
        ProductionPhase.STORY_REVIEW,
    }:
        raise ValueError("production is not open for shot quoting")
    preparation_hash = body.get("preparation_hash")
    if not isinstance(preparation_hash, str):
        raise ValueError("preparation hash is invalid")
    preparation = production_store.get_preparation(
        actor.user_id, project_id, preparation_hash
    )
    context = production_store.get_preparation_context(
        actor.user_id, project_id, preparation_hash
    )
    pack = production_store.get_pack(actor.user_id, project_id)
    shot = next((item for item in pack.shots if item.shot_id == shot_id), None)
    if (
        shot is None or preparation.get("shot_id") != shot_id
        or context.get("shot_id") != shot_id
        or context.get("production_state_hash") != state.state_hash
        or context.get("pack_hash") != pack.content_hash
    ):
        raise ProductionStoreConflict("production preparation scope changed")
    if state.phase is ProductionPhase.STORY_REVIEW and not isinstance(
        context.get("repair"), Mapping
    ):
        raise ValueError("story-review quote requires a rejected-shot repair")
    entity_by_id = {item.entity_id: item for item in pack.entities}
    entities = [entity_by_id[item] for item in shot.entity_ids if item in entity_by_id]
    characters = [item for item in entities if item.character_id is not None]
    multi_subject = shot.required_capability.value == "multi_subject_video"
    if (
        len(characters) != len(shot.entity_ids)
        or len({item.character_id for item in characters}) != len(characters)
        or len({item.visual_class for item in characters}) != 1
        or (multi_subject and not 2 <= len(characters) <= 4)
        or (not multi_subject and len(characters) != 1)
    ):
        raise ValueError("production provider route has invalid character coverage")
    character_by_id = {item.character_id: item for item in characters}
    subject_by_resource = (
        {
            item.profile_asset_id: item.character_id
            for item in characters
            if item.profile_asset_id is not None
        }
        if multi_subject else {}
    )
    if multi_subject and (
        len(subject_by_resource) != len(characters)
        or not set(subject_by_resource).issubset(shot.input_asset_ids)
    ):
        raise ValueError("production multi-subject identity bindings are incomplete")
    raw_inputs = context.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ProductionStoreConflict("production execution inputs are unavailable")
    assets = []
    selected_subject_resources: set[str] = set()
    for item in raw_inputs:
        if not isinstance(item, Mapping):
            raise ProductionStoreConflict("production execution input is malformed")
        execution_id = item.get("execution_asset_id")
        if not isinstance(execution_id, str):
            raise ProductionStoreConflict("production execution input identity is malformed")
        binding = production_store.get_execution_asset(actor.user_id, execution_id)
        if (
            binding.get("projection_id") != item.get("projection_id")
            or binding.get("asset_version_id") != item.get("asset_version_id")
        ):
            raise ProductionStoreConflict("production execution asset binding changed")
        projected = provider_store.get_owned_remote_asset(
            actor.user_id, binding["projection_id"]
        )
        linked_character_ids = projected.get("character_ids")
        if (
            not isinstance(linked_character_ids, list)
            or any(not isinstance(value, str) for value in linked_character_ids)
        ):
            raise ValueError("production input character binding is invalid")
        if multi_subject and item.get("field_name") == "subjects":
            resource_id = item.get("resource_id")
            expected_character_id = subject_by_resource.get(resource_id)
            if (
                expected_character_id is None
                or set(linked_character_ids) != {expected_character_id}
                or resource_id in selected_subject_resources
            ):
                raise ValueError(
                    "production subject input is not linked to its exact character"
                )
            selected_subject_resources.add(resource_id)
            asset_character_ids = [expected_character_id]
        elif multi_subject:
            if not set(linked_character_ids).issubset(character_by_id):
                raise ValueError("production input is linked outside the selected shot")
            asset_character_ids = list(linked_character_ids)
        else:
            character = characters[0]
            if character.character_id not in linked_character_ids:
                raise ValueError("production input is not linked to the selected character")
            asset_character_ids = [character.character_id]
        assets.append({
            "asset_id": execution_id,
            "kind": projected["media_kind"],
            "uri": "castle://unresolved-production-media",
            "visual_class": projected.get("visual_class"),
            "character_ids": asset_character_ids,
            "mime_type": None,
            "byte_size": None,
            "sha256": None,
            "duration_seconds": None,
        })
    if multi_subject and selected_subject_resources != set(subject_by_resource):
        raise ValueError("production subject input coverage is incomplete")
    binding = {
        "schema_version": "castle.production-binding.v1",
        "project_id": project_id,
        "pack_hash": pack.content_hash,
        "state_hash": state.state_hash,
        "shot_id": shot_id,
        "route_variant_id": context["route_variant_id"],
    }
    repair = context.get("repair")
    repair_note = repair.get("review_note") if isinstance(repair, Mapping) else None
    observable_action = shot.observable_action
    if isinstance(repair_note, str) and repair_note.strip():
        observable_action = (
            f"{observable_action}\n修復要求：{repair_note.strip()}"
        )
        if len(observable_action) > 2000:
            raise ValueError("repair guidance exceeds the provider instruction limit")
    autoip_instruction: dict[str, Any] = {}
    if context.get("provider_id") == "autoip":
        execution_preparation = preparation
        base_hash = preparation.get("base_preparation_hash")
        if isinstance(base_hash, str):
            execution_preparation = production_store.get_preparation(
                actor.user_id, project_id, base_hash
            )
        payload = execution_preparation.get("payload")
        request = payload.get("request") if isinstance(payload, Mapping) else None
        fields = request.get("fields") if isinstance(request, Mapping) else None
        if (
            not isinstance(payload, Mapping)
            or payload.get("kind") != "autoip_request"
            or not isinstance(request, Mapping)
            or request.get("operation") != shot.required_capability.value
            or not isinstance(fields, Mapping)
        ):
            raise ProductionStoreConflict("AutoIP preparation fields are unavailable")
        descriptions = fields.get("descriptions[]")
        if descriptions is not None:
            if (
                not isinstance(descriptions, list)
                or not descriptions
                or len(descriptions) > 4
                or any(not isinstance(item, str) or not item.strip() for item in descriptions)
            ):
                raise ProductionStoreConflict("AutoIP subject descriptions are invalid")
            autoip_instruction["subject_descriptions"] = descriptions
        for key in ("dialogue_text", "negative_prompt"):
            value = fields.get(key)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ProductionStoreConflict(f"AutoIP {key} is invalid")
                autoip_instruction[key] = value
        selected_seed = context.get("selected_options", {}).get("seed")
        if selected_seed is not None:
            wire_seed = fields.get("seed")
            if not (
                selected_seed == "random" and wire_seed is None
                or isinstance(selected_seed, int) and wire_seed == selected_seed
            ):
                raise ProductionStoreConflict("AutoIP seed binding changed")
            autoip_instruction["seed"] = selected_seed
    plan = service.plan_intent(actor, {
        "schema_version": 1,
        "capability": shot.required_capability.value,
        "visual_class": characters[0].visual_class.value,
        "characters": [{
            "character_id": character.character_id,
            "visual_class": character.visual_class.value,
            "display_name": character.display_name,
            "external_refs": [],
        } for character in characters],
        "assets": assets,
        "options": dict(context["selected_options"]),
        "preferred_providers": [context["provider_id"]],
        "provider_id": context["provider_id"],
        "max_credits": pack.project.max_credits,
        "action_id": context["action_id"],
        "feature_id": context["feature_id"],
        "profile_key": context["profile_key"],
        "creative_instruction": {
            "schema_version": "castle.creative-instruction.v1",
            "shot_id": shot_id,
            "frame_intent": shot.frame_intent,
            "observable_action": observable_action,
            "camera": shot.camera,
            "audio_intent": shot.audio_intent,
            "language": pack.project.language,
            "production_binding": binding,
            **autoip_instruction,
        },
    })
    return {
        "schema_version": QUOTE_RESPONSE_VERSION,
        "ok": True,
        "project_id": project_id,
        "shot_id": shot_id,
        "preparation_hash": preparation_hash,
        "plan": plan,
        "authority": {
            "provider_write_performed": False,
            "credits_spent": 0,
            "explicit_confirmation_required": True,
        },
    }


__all__ = [
    "QUOTE_REQUEST_VERSION", "QUOTE_RESPONSE_VERSION",
    "quote_prepared_production_shot_api",
]
