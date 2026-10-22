"""Server-owned, zero-spend preparation of one durable production shot."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from collections import Counter
from typing import Any, Callable, Mapping

from provider_fabric.store import ProviderFabricStore
from provider_fabric.variant_profiles import (
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
    VariantProfile,
)

from .contracts import ProductionPack, ShotSpec
from .production_execution import prepare_visual_execution
from .production_state import ProductionPhase
from .production_store import ProductionStateStore, ProductionStoreConflict
from .material_selection import recommend_shot_materials


PREPARE_REQUEST_VERSION = "castle.production-shot-prepare.v1"
PREPARE_RESPONSE_VERSION = "castle.production-shot-prepare.response.v1"
PREPARATION_OPTIONS_VERSION = "castle.production-shot-preparation-options.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_MIME = {
    "image/avif": ("avif", ".avif"),
    "image/jpeg": ("jpeg", ".jpg"),
    "image/png": ("png", ".png"),
    "image/webp": ("webp", ".webp"),
    "video/mp4": ("mp4", ".mp4"),
    "video/quicktime": ("quicktime", ".mov"),
    "video/webm": ("webm", ".webm"),
    "audio/flac": ("flac", ".flac"),
    "audio/mpeg": ("mpeg_audio", ".mp3"),
    "audio/mp4": ("mp4", ".m4a"),
    "audio/ogg": ("ogg", ".ogg"),
    "audio/wav": ("wav", ".wav"),
    "audio/x-wav": ("wav", ".wav"),
}
ProfileResolver = Callable[[str], VariantProfile]


def _profile_reason_codes(profile: VariantProfile) -> list[str]:
    if profile.execution_ready:
        return []
    if profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
        return [
            "technical_compatibility_missing"
            if not profile.technical_evidence_id
            else "runtime_not_ready"
        ]
    return [
        code for ready, code in (
            (profile.release_ready, "release_not_approved"),
            (profile.runtime_ready, "runtime_not_ready"),
            (profile.golden_current and bool(profile.golden_id), "quality_golden_missing"),
        ) if not ready
    ]


def production_shot_preparation_options(
    production_store: ProductionStateStore,
    owner_user_id: str,
    project_id: str,
    shot_id: str,
    *,
    profiles: tuple[VariantProfile, ...],
    provider_store: ProviderFabricStore | None = None,
) -> dict[str, Any]:
    """Project exact qualified profiles for one persisted shot without assets or I/O."""

    state = production_store.get(owner_user_id, project_id)
    pack = production_store.get_pack(owner_user_id, project_id)
    shot = next((item for item in pack.shots if item.shot_id == shot_id), None)
    if shot is None or shot_id not in state.shot_ids:
        raise KeyError(shot_id)
    routes = {
        route.route_variant_id: route for route in pack.route_variants
        if route.route_variant_id in shot.eligible_visual_variant_ids
    }
    candidates = []
    for profile in profiles:
        if not isinstance(profile, VariantProfile):
            raise TypeError("preparation profiles must use Castle contracts")
        matches = [route for route in routes.values() if (
            route.provider_id == profile.provider_id
            and route.variant_id == profile.variant_id
            and route.capability is profile.capability
            and profile.visual_class in route.visual_classes
            and set(profile.required_option_keys).issubset(route.option_keys)
            and set(route.option_keys).issubset(profile.option_values)
        )]
        if len(matches) != 1:
            continue
        qualified = profile.execution_ready
        material_selection = None
        if provider_store is not None:
            inventory = {}
            for slot in profile.input_slots:
                for character_id in shot.entity_ids:
                    after = None
                    for _page in range(5):
                        page = provider_store.list_owned_remote_assets(
                            owner_user_id,
                            kind=slot.media_kind,
                            visual_class=profile.visual_class if slot.identity_bearing else None,
                            character_id=character_id if slot.identity_bearing else None,
                            after=after,
                            limit=200,
                        )
                        for asset in page["items"]:
                            inventory[asset["projection_id"]] = asset
                        after = page["next_after"]
                        if after is None:
                            break
            material_selection = recommend_shot_materials(
                profile, shot.entity_ids, inventory.values()
            )
        candidate = {
            "route_variant_id": matches[0].route_variant_id,
            "profile": profile.to_public_dict(),
            "qualified": qualified,
            "reason_codes": _profile_reason_codes(profile),
        }
        if material_selection is not None:
            candidate["material_selection"] = material_selection
        candidates.append(candidate)
    return {
        "schema_version": PREPARATION_OPTIONS_VERSION,
        "ok": True,
        "project_id": project_id,
        "shot_id": shot_id,
        "pack_hash": pack.content_hash,
        "state_hash": state.state_hash,
        "phase": state.phase.value,
        "candidates": sorted(
            candidates,
            key=lambda item: (item["route_variant_id"], item["profile"]["provider_id"]),
        ),
        "authority": {
            "owner_scoped": True,
            "runtime_catalog_checked": True,
            "asset_versions_resolved": False,
            "material_candidates_resolved": provider_store is not None,
            "provider_submission_authorized": False,
            "gpu_queue_authorized": False,
            "credits_authorized": 0,
        },
    }


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_inputs(
    store: ProviderFabricStore,
    owner_user_id: str,
    profile: VariantProfile,
    submitted: object,
    *,
    identity_bindings: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict[str, object]]:
    if not isinstance(submitted, list) or len(submitted) > 32:
        raise ValueError("production preparation inputs are invalid")
    records = []
    seen = set()
    identity_bindings = identity_bindings or {}
    selected_identity_resources = {
        slot_id: set() for slot_id in identity_bindings
    }
    slot_by_id = {slot.slot_id: slot for slot in profile.input_slots}
    for item in submitted:
        if not isinstance(item, Mapping) or set(item) != {
            "resource_id", "projection_id", "asset_version_id", "field_name",
        }:
            raise ValueError("production preparation input schema is invalid")
        values = tuple(item.get(key) for key in (
            "resource_id", "projection_id", "asset_version_id", "field_name",
        ))
        if any(not isinstance(value, str) or _ID.fullmatch(value) is None for value in values):
            raise ValueError("production preparation input identity is invalid")
        resource_id, projection_id, version_id, field_name = values
        if (resource_id, projection_id, version_id, field_name) in seen:
            raise ValueError("production preparation input is duplicated")
        seen.add((resource_id, projection_id, version_id, field_name))
        slot = slot_by_id.get(field_name)
        if slot is None:
            raise ValueError("production preparation input slot is unsupported")
        asset = store.get_owned_remote_asset(owner_user_id, projection_id)
        if field_name in identity_bindings:
            expected_character_id = identity_bindings[field_name].get(resource_id)
            linked_character_ids = asset.get("character_ids")
            if (
                expected_character_id is None
                or not isinstance(linked_character_ids, list)
                or any(not isinstance(value, str) for value in linked_character_ids)
                or set(linked_character_ids) != {expected_character_id}
                or resource_id in selected_identity_resources[field_name]
            ):
                raise ValueError(
                    "production preparation identity asset does not match the shot"
                )
            selected_identity_resources[field_name].add(resource_id)
        versions = [row for row in asset["versions"] if row.get("version_id") == version_id]
        if len(versions) != 1:
            raise ValueError("production preparation asset version is unavailable")
        version = versions[0]
        source = store.get_owned_remote_asset_version_source(
            owner_user_id, projection_id, version_id
        )
        mime = version.get("mime_type")
        signature = _MIME.get(mime)
        sha256 = version.get("sha256")
        byte_size = version.get("byte_size")
        if (
            signature is None or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
            or not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size <= 0
            or asset.get("media_kind") != slot.media_kind.value
            or not (
                source.get("original_receipt_active") is not None
                or urlsplit(str(source.get("source_uri") or "")).scheme == "file"
            )
        ):
            raise ValueError("production preparation asset bytes are not canonical")
        visual_class = asset.get("visual_class")
        if slot.identity_bearing and visual_class != profile.visual_class.value:
            raise ValueError("production preparation identity class differs from the profile")
        records.append({
            "source_asset_id": resource_id,
            "field_name": field_name,
            "asset_version_id": version_id,
            "sha256": sha256,
            "mime_type": mime,
            "byte_size": byte_size,
            "signature_family": signature[0],
            "visual_class": visual_class,
            "basename": f"castle_{sha256[:32]}{signature[1]}",
        })
    counts = Counter(str(item["field_name"]) for item in records)
    if any(
        not slot.minimum_count <= counts.get(slot.slot_id, 0) <= slot.maximum_count
        for slot in profile.input_slots
    ):
        raise ValueError("production preparation input cardinality is invalid")
    if any(
        selected_identity_resources[slot_id] != set(bindings)
        for slot_id, bindings in identity_bindings.items()
    ):
        raise ValueError("production preparation identity coverage is incomplete")
    return records


def _multi_subject_identity_bindings(
    pack: ProductionPack,
    shot: ShotSpec,
    profile: VariantProfile,
) -> dict[str, dict[str, str]]:
    if profile.capability.value != "multi_subject_video":
        return {}
    subject_slots = [
        slot for slot in profile.input_slots
        if slot.slot_id == "subjects" and slot.identity_bearing
    ]
    if len(subject_slots) != 1:
        raise ValueError("multi-subject profile has no exact identity slot")
    entities = {item.entity_id: item for item in pack.entities}
    bindings: dict[str, str] = {}
    for entity_id in shot.entity_ids:
        entity = entities.get(entity_id)
        if (
            entity is None
            or entity.character_id is None
            or entity.profile_asset_id is None
            or entity.profile_asset_id not in shot.input_asset_ids
            or entity.profile_asset_id in bindings
            or entity.character_id in bindings.values()
        ):
            raise ValueError("multi-subject shot identity bindings are incomplete")
        bindings[entity.profile_asset_id] = entity.character_id
    slot = subject_slots[0]
    if not slot.minimum_count <= len(bindings) <= slot.maximum_count:
        raise ValueError("multi-subject shot exceeds the provider identity bound")
    return {"subjects": bindings}


def prepare_production_shot_api(
    production_store: ProductionStateStore,
    provider_store: ProviderFabricStore,
    owner_user_id: str,
    project_id: str,
    shot_id: str,
    body: Mapping[str, Any],
    *,
    profile_resolver: ProfileResolver,
) -> dict[str, Any]:
    """Resolve exact owner assets and persist an inert preparation artifact."""

    if (
        not isinstance(production_store, ProductionStateStore)
        or not isinstance(provider_store, ProviderFabricStore)
        or not isinstance(body, Mapping)
        or set(body) != {
            "schema_version", "expected_state_hash", "route_variant_id",
            "profile_key", "selected_options", "inputs",
        }
        or body.get("schema_version") != PREPARE_REQUEST_VERSION
        or not callable(profile_resolver)
    ):
        raise ValueError("production shot preparation request is invalid")
    state = production_store.get(owner_user_id, project_id)
    if state.state_hash != body.get("expected_state_hash"):
        raise ProductionStoreConflict("production state changed since preparation was requested")
    if state.phase not in {ProductionPhase.ASSETS_LOCKED, ProductionPhase.GENERATING}:
        raise ValueError("production is not open for shot preparation")
    if shot_id not in state.shot_ids:
        raise ValueError("production shot is unavailable")
    pack = production_store.get_pack(owner_user_id, project_id)
    shot = next((item for item in pack.shots if item.shot_id == shot_id), None)
    if shot is None:
        raise ValueError("production shot is unavailable")
    profile_key = body.get("profile_key")
    if not isinstance(profile_key, str):
        raise ValueError("production profile key is invalid")
    profile = profile_resolver(profile_key)
    if not isinstance(profile, VariantProfile) or profile.key != profile_key:
        raise ValueError("production profile resolver returned a different profile")
    if not profile.execution_ready or not profile.execution_evidence_id:
        raise ValueError("production profile is not qualified")
    route_variant_id = body.get("route_variant_id")
    selected_options = body.get("selected_options")
    if (
        not isinstance(route_variant_id, str) or _ID.fullmatch(route_variant_id) is None
        or not isinstance(selected_options, Mapping)
    ):
        raise ValueError("production route or options are invalid")
    records = _canonical_inputs(
        provider_store,
        owner_user_id,
        profile,
        body.get("inputs"),
        identity_bindings=_multi_subject_identity_bindings(pack, shot, profile),
    )
    seed_material = {
        "state_hash": state.state_hash,
        "shot_id": shot_id,
        "route_variant_id": route_variant_id,
        "profile_key": profile_key,
        "selected_options": dict(selected_options),
        "asset_versions": [item["asset_version_id"] for item in records],
    }
    token = _canonical_hash(seed_material)
    local = profile.provider_id == "local_comfy"
    prepared = prepare_visual_execution(
        pack,
        shot_id=shot_id,
        route_variant_id=route_variant_id,
        profile=profile,
        selected_options=selected_options,
        canonical_inputs=records,
        seed=int(token[:16], 16) if local else None,
        output_token=f"production_{token[:32]}" if local else None,
    )
    artifact = prepared.to_dict()
    context_inputs = []
    for item in body["inputs"]:
        execution_digest = _canonical_hash({
            "preparation_hash": artifact["preparation_hash"],
            "projection_id": item["projection_id"],
            "asset_version_id": item["asset_version_id"],
            "field_name": item["field_name"],
        })
        context_inputs.append({
            **dict(item),
            "execution_asset_id": f"production-media:{execution_digest}",
        })
    context = {
        "schema_version": "castle.production-shot-preparation-context.v1",
        "project_id": project_id,
        "shot_id": shot_id,
        "production_state_hash": state.state_hash,
        "pack_hash": pack.content_hash,
        "route_variant_id": route_variant_id,
        "profile_key": profile.key,
        "action_id": profile.action_id,
        "feature_id": profile.feature_id,
        "provider_id": profile.provider_id,
        "variant_id": profile.variant_id,
        "selected_options": dict(selected_options),
        "inputs": context_inputs,
    }
    persisted = production_store.put_preparation(
        owner_user_id, project_id, shot_id=shot_id,
        production_state_hash=state.state_hash, preparation=artifact,
        context=context,
    )
    if persisted != artifact:
        raise ProductionStoreConflict("persisted production preparation differs")
    return {
        "schema_version": PREPARE_RESPONSE_VERSION,
        "ok": True,
        "preparation": artifact,
        "authority": {
            "owner_scoped": True,
            "asset_versions_server_resolved": True,
            "provider_submission_authorized": False,
            "gpu_queue_authorized": False,
            "credits_authorized": 0,
            "publishing_authorized": False,
        },
    }


__all__ = [
    "PREPARATION_OPTIONS_VERSION", "PREPARE_REQUEST_VERSION", "PREPARE_RESPONSE_VERSION",
    "prepare_production_shot_api", "production_shot_preparation_options",
]
