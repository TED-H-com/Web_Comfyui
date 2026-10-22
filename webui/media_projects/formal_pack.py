"""Compile an evidence-complete Pack Proposal into a strict ProductionPack."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .contracts import parse_production_pack
from .lint import lint_production_pack


_HASH = re.compile(r"^[0-9a-f]{64}$")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _evidence(evidence_id: str, evidence_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    item = evidence_by_id.get(evidence_id)
    if not isinstance(item, Mapping) or set(item) != {
        "evidence_id", "kind", "title", "source_uri", "source_hash", "is_private",
    } or item.get("evidence_id") != evidence_id:
        raise ValueError("required immutable evidence is missing")
    return dict(item)


def compile_formal_production_pack(
    proposal: Mapping[str, Any],
    *,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    rights_by_character: Mapping[str, Mapping[str, Any]],
    provider_accounts: Mapping[str, str],
    continuity_state: Mapping[str, str],
    soundtrack_binding: Mapping[str, Any] | None = None,
    sound_effect_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a lint-clean strict pack; never infer evidence or authorize execution."""

    if (
        not isinstance(proposal, Mapping)
        or proposal.get("schema_version") != "castle.production-pack-proposal.v1"
        or proposal.get("pack_compilation_authorized") is not True
        or proposal.get("execution_authorized") is not False
        or proposal.get("blockers") != []
        or not isinstance(evidence_by_id, Mapping)
        or not isinstance(rights_by_character, Mapping)
        or not isinstance(provider_accounts, Mapping)
        or not isinstance(continuity_state, Mapping)
    ):
        raise ValueError("production pack proposal is not compilation-ready")
    project = proposal.get("project")
    entities_raw = proposal.get("entities")
    routes_raw = proposal.get("route_variants")
    shots_raw = proposal.get("shots")
    if not all(isinstance(item, (Mapping, list)) for item in (project, entities_raw, routes_raw, shots_raw)):
        raise ValueError("production pack proposal shape is invalid")
    proposal_hash = proposal.get("proposal_hash")
    if (
        not isinstance(proposal_hash, str)
        or _HASH.fullmatch(proposal_hash) is None
        or proposal_hash != _hash({
            key: value for key, value in proposal.items() if key != "proposal_hash"
        })
    ):
        raise ValueError("production pack proposal hash is invalid")
    usage_scope = "private" if project.get("platform") == "private-review" else "public_noncommercial"
    rights_usage_scope = "private" if usage_scope == "private" else "social"
    project_id = "project:" + proposal_hash[:24]
    route_by_id = {}
    evidence = {}
    for route in routes_raw:
        if not isinstance(route, Mapping):
            raise ValueError("route binding is invalid")
        execution_basis = route.get("execution_basis", "quality_golden")
        golden_id = route.get("golden_id")
        technical_evidence_id = route.get("technical_evidence_id")
        execution_evidence_id = route.get("execution_evidence_id")
        expected_evidence_id = (
            technical_evidence_id
            if execution_basis == "technical_compatibility"
            else golden_id
        )
        provider_id = route.get("provider_id")
        account_id = provider_accounts.get(provider_id)
        if (
            execution_basis not in {"quality_golden", "technical_compatibility"}
            or not isinstance(expected_evidence_id, str)
            or execution_evidence_id != expected_evidence_id
            or not isinstance(account_id, str)
        ):
            raise ValueError("route evidence or provider account is missing")
        evidence[expected_evidence_id] = _evidence(
            expected_evidence_id, evidence_by_id
        )
        route_id = route["route_variant_id"]
        if route_id in route_by_id:
            raise ValueError("route_variant_id is duplicated")
        route_by_id[route_id] = {
            "route_variant_id": route_id,
            "provider_id": provider_id,
            "provider_account_id": account_id,
            "variant_id": route["variant_id"],
            "execution_domain": "local" if provider_id == "local_comfy" else "external",
            "capability": route["capability"],
            "visual_classes": [route["visual_class"]],
            "option_keys": list(route.get("option_keys") or ()),
            "resource_lane": f"provider:{provider_id}",
            "resource_locks": [
                f"provider-account:{account_id}",
                *(["gpu:rtx-5090"] if provider_id == "local_comfy" else []),
            ],
            "golden_evidence_id": golden_id,
            # This legacy field now means the reviewed route is executable;
            # execution_basis identifies which evidence made that true.
            "release_ready": True,
            "estimated_credits": 0 if provider_id == "local_comfy" else None,
            "execution_basis": execution_basis,
            "technical_evidence_id": technical_evidence_id,
            "execution_evidence_id": execution_evidence_id,
        }
    has_external_routes = any(
        route["provider_id"] != "local_comfy"
        for route in route_by_id.values()
    )
    entities = []
    for entity in entities_raw:
        character_id = entity.get("entity_id")
        visual_class = entity.get("visual_class")
        route_ids = {
            route_id for shot in shots_raw if character_id in (shot.get("character_ids") or ())
            for route_id in shot.get("eligible_route_variant_ids") or ()
        }
        allowed_routes = [route_by_id[route_id] for route_id in sorted(route_ids)]
        providers = sorted({route["provider_id"] for route in allowed_routes})
        capabilities = sorted({route["capability"] for route in allowed_routes})
        record = rights_by_character.get(character_id)
        if visual_class == "realistic":
            if not isinstance(record, Mapping) or record.get("consent_state") not in {"self_owned", "documented"}:
                raise ValueError("real person rights evidence is missing")
            allowed_provider_ids = record.get("allowed_provider_ids")
            allowed_capabilities = record.get("allowed_capabilities")
            allowed_usage_scopes = record.get("allowed_usage_scopes")
            if (
                not isinstance(allowed_provider_ids, (list, tuple))
                or not set(providers).issubset(set(allowed_provider_ids))
                or not isinstance(allowed_capabilities, (list, tuple))
                or not set(capabilities).issubset(set(allowed_capabilities))
                or not isinstance(allowed_usage_scopes, (list, tuple))
                or rights_usage_scope not in allowed_usage_scopes
            ):
                raise ValueError("real person rights scope does not cover the route")
            evidence_id = record.get("evidence_id")
            if not isinstance(evidence_id, str):
                raise ValueError("real person evidence binding is missing")
            evidence[evidence_id] = _evidence(evidence_id, evidence_by_id)
            consent_state, consent_ref, consent_ids, kind = (
                record["consent_state"], record.get("consent_ref"), [evidence_id], "consenting_person",
            )
        else:
            consent_state, consent_ref, consent_ids, kind = "not_required", None, [], "fictional"
        entities.append({
            "entity_id": character_id, "kind": kind, "display_name": entity["display_name"],
            "visual_class": visual_class, "consent_state": consent_state,
            "consent_ref": consent_ref, "consent_evidence_ids": consent_ids,
            "allowed_provider_ids": providers, "allowed_capabilities": capabilities,
            "allowed_usage_scopes": [usage_scope], "voice_use_allowed": False,
            "character_id": character_id, "profile_asset_id": entity["profile_asset_id"],
            "wardrobe_asset_id": None, "voice_asset_id": None,
        })
    scenes = []
    shots = []
    requested_effect_versions = set()
    for ordinal, shot in enumerate(shots_raw, 1):
        locks = list(shot.get("continuity_locks") or ())
        if any(key not in continuity_state for key in locks):
            raise ValueError("continuity state is incomplete")
        state = {key: continuity_state[key] for key in locks}
        scenes.append({
            "scene_id": shot["scene_id"], "ordinal": ordinal,
            "state_before": state, "event": shot.get("narrative_role") or "story beat",
            "state_after": state, "shot_ids": [shot["shot_id"]],
        })
        route_ids = list(shot["eligible_route_variant_ids"])
        capabilities = {route_by_id[item]["capability"] for item in route_ids}
        if len(capabilities) != 1:
            raise ValueError("shot routes disagree on capability")
        effects = []
        for effect in shot.get("sound_effects") or ():
            if not isinstance(effect, Mapping):
                raise ValueError("sound effect request is invalid")
            version_id = effect.get("asset_version_id")
            binding = sound_effect_bindings.get(version_id) if isinstance(sound_effect_bindings, Mapping) else None
            if (
                not isinstance(version_id, str) or not isinstance(binding, Mapping)
                or binding.get("asset_version_id") != version_id
                or not isinstance(binding.get("sha256"), str)
                or _HASH.fullmatch(binding["sha256"]) is None
                or binding.get("media_kind") != "audio"
                or binding.get("source_available") is not True
                or isinstance(binding.get("duration_seconds"), bool)
                or not isinstance(binding.get("duration_seconds"), (int, float))
                or binding["duration_seconds"] <= 0
                or effect.get("source_start_seconds", 0) + effect.get("duration_seconds", 0)
                    > binding["duration_seconds"]
            ):
                raise ValueError("sound effect asset evidence is missing")
            requested_effect_versions.add(version_id)
            effects.append({**dict(effect), "asset_sha256": binding["sha256"]})
        shots.append({
            "shot_id": shot["shot_id"], "scene_id": shot["scene_id"], "ordinal": ordinal,
            "duration_seconds": shot["duration_seconds"], "frame_intent": shot["frame_intent"],
            "observable_action": shot["observable_action"], "camera": shot["camera"],
            "audio_intent": shot["audio_intent"],
            "entity_ids": list(shot["character_ids"]), "input_asset_ids": list(shot["input_asset_ids"]),
            "required_capability": next(iter(capabilities)), "required_options": [],
            "eligible_visual_variant_ids": route_ids, "dialogue_mode": "none",
            "eligible_voice_variant_ids": [], "dialogue": [], "evidence_ids": [],
            "factual_claims": [], "state_phase": "during", "continuity_keys": locks,
            "continuity_values": {}, "negative": "No identity, wardrobe, voice, or visual-class drift.",
            **({"sound_effects": effects} if effects else {}),
        })
    raw = {
        "version": "castle.production-pack.v1",
        "project": {
            "project_id": project_id, "title": project["title"], "premise": project["premise"],
            "audience": project["audience"], "platform_profile": project["platform"],
            "language": project["language"], "aspect_ratio": project["aspect_ratio"],
            "target_duration_seconds": project["target_duration_seconds"], "subtitle_profile": "language-burnin",
            "audio_mix_profile": "social-standard", "branding_profile": "castle-default",
            "deliverable_profile": "mp4-h264-browser", "branding_asset_ids": [],
            "ai_disclosure_required": True, "usage_scope": usage_scope, "quality_tier": "quality",
            # A null cap represents an inert, not-yet-quoted external route.
            # It cannot authorize spending: the downstream provider plan must
            # obtain an exact live quote and explicit credit confirmation.
            "max_credits": None if has_external_routes else 0,
            "allow_external_providers": has_external_routes,
            "provider_allowlist": sorted({route["provider_id"] for route in route_by_id.values()}),
            "allow_precharge_fallback": True, "max_precharge_route_attempts": 2,
        },
        "entities": entities, "evidence": list(evidence.values()),
        "route_variants": list(route_by_id.values()), "scenes": scenes, "shots": shots,
    }
    soundtrack_version = project.get("soundtrack_asset_version_id")
    if soundtrack_version is not None:
        if (
            not isinstance(soundtrack_binding, Mapping)
            or soundtrack_binding.get("asset_version_id") != soundtrack_version
            or not isinstance(soundtrack_binding.get("sha256"), str)
            or _HASH.fullmatch(soundtrack_binding["sha256"]) is None
            or soundtrack_binding.get("media_kind") != "audio"
            or soundtrack_binding.get("source_available") is not True
        ):
            raise ValueError("soundtrack asset evidence is missing")
        raw["project"].update({
            "soundtrack_asset_version_id": soundtrack_version,
            "soundtrack_asset_sha256": soundtrack_binding["sha256"],
            "soundtrack_gain_db": project.get("soundtrack_gain_db"),
        })
    elif soundtrack_binding is not None:
        raise ValueError("unrequested soundtrack evidence was supplied")
    supplied_effect_versions = set(sound_effect_bindings or {})
    if supplied_effect_versions != requested_effect_versions:
        raise ValueError("sound effect evidence does not match the request")
    pack = parse_production_pack(raw)
    report = lint_production_pack(pack)
    if not report.ok:
        raise ValueError("compiled production pack failed its strict lint gates")
    return {
        "schema_version": "castle.formal-pack-compilation.v1",
        "production_pack": pack.to_dict(), "pack_hash": pack.content_hash,
        "execution_authorized": False,
        "spend_authorized": False,
        "live_quote_required": has_external_routes,
    }


__all__ = ["compile_formal_production_pack"]
