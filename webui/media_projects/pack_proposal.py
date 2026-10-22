"""Fail-closed Production Pack proposal compiled from reviewed storyboard data."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


PACK_PROPOSAL_VERSION = "castle.production-pack-proposal.v1"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def compile_pack_proposal(
    draft: Mapping[str, Any],
    director_plan: Mapping[str, Any],
    route_review: Mapping[str, Any],
    *,
    rights_by_character: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(draft, Mapping) or draft.get("schema_version") != "castle.storyboard-draft.v1"
        or not isinstance(director_plan, Mapping) or director_plan.get("schema_version") != "castle.director-plan.v1"
        or director_plan.get("draft_hash") != draft.get("draft_hash")
        or not isinstance(route_review, Mapping) or route_review.get("schema_version") != "castle.storyboard-route-review.v1"
        or route_review.get("draft_hash") != draft.get("draft_hash")
    ):
        raise ValueError("pack proposal inputs are not hash-bound")
    rights = rights_by_character or {}
    characters = draft.get("characters")
    scenes = draft.get("scenes")
    review_shots = route_review.get("shots")
    director_shots = director_plan.get("shots")
    if not all(isinstance(item, list) for item in (characters, scenes, review_shots, director_shots)):
        raise ValueError("pack proposal collections are invalid")
    route_by_shot = {item.get("shot_id"): item for item in review_shots if isinstance(item, Mapping)}
    director_by_shot = {item.get("shot_id"): item for item in director_shots if isinstance(item, Mapping)}
    blockers = []
    entities = []
    for character in characters:
        if not isinstance(character, Mapping):
            raise ValueError("pack proposal character is invalid")
        character_id = character.get("character_id")
        visual_class = character.get("visual_class")
        record = rights.get(character_id)
        rights_ready = visual_class != "realistic" or (
            isinstance(record, Mapping)
            and record.get("consent_state") in {"self_owned", "documented"}
        )
        if not rights_ready:
            blockers.append({"code": "person_rights_missing", "path": f"entities.{character_id}"})
        entities.append({
            "entity_id": character_id,
            "display_name": character.get("display_name"),
            "visual_class": visual_class,
            "profile_asset_id": character.get("profile_asset_id"),
            "rights_ready": rights_ready,
        })
    proposed_shots = []
    proposed_routes: dict[str, dict[str, Any]] = {}
    for scene in scenes:
        shot = scene.get("shot") if isinstance(scene, Mapping) else None
        if not isinstance(shot, Mapping):
            raise ValueError("pack proposal shot is invalid")
        shot_id = shot.get("shot_id")
        review = route_by_shot.get(shot_id)
        direction = director_by_shot.get(shot_id)
        if not isinstance(review, Mapping) or not isinstance(direction, Mapping):
            raise ValueError("pack proposal shot bindings are incomplete")
        shot_blockers = list(shot.get("blockers") or ())
        selectable = set(review.get("selectable_profile_keys") or ())
        candidates = []
        for profile in review.get("candidate_profiles") or ():
            if not isinstance(profile, Mapping) or profile.get("profile_key") not in selectable:
                continue
            execution_basis = profile.get("execution_basis", "quality_golden")
            golden_id = profile.get("golden_id")
            technical_evidence_id = profile.get("technical_evidence_id")
            execution_evidence_id = profile.get("execution_evidence_id")
            expected_evidence_id = (
                technical_evidence_id
                if execution_basis == "technical_compatibility"
                else golden_id
            )
            if (
                execution_basis not in {"quality_golden", "technical_compatibility"}
                or not isinstance(expected_evidence_id, str)
                or execution_evidence_id != expected_evidence_id
            ):
                raise ValueError("selectable route execution evidence is invalid")
            profile_key = profile["profile_key"]
            route_id = "route:" + hashlib.sha256(profile_key.encode()).hexdigest()[:24]
            proposed_routes[route_id] = {
                "route_variant_id": route_id,
                "profile_key": profile_key,
                "provider_id": profile.get("provider_id"),
                "variant_id": profile.get("variant_id"),
                "capability": profile.get("capability"),
                "visual_class": profile.get("visual_class"),
                "golden_id": golden_id,
                "execution_basis": execution_basis,
                "technical_evidence_id": technical_evidence_id,
                "execution_evidence_id": execution_evidence_id,
                "option_keys": list(profile.get("required_option_keys") or ()),
            }
            candidates.append(route_id)
        if not candidates:
            shot_blockers.append("qualified_route_missing")
        for code in sorted(set(shot_blockers)):
            blockers.append({"code": code, "path": f"shots.{shot_id}"})
        proposed_shots.append({
            "shot_id": shot_id,
            "scene_id": scene.get("scene_id"),
            "character_ids": list(shot.get("character_ids") or ()),
            "input_asset_ids": list(shot.get("input_asset_ids") or ()),
            "duration_seconds": shot.get("duration_seconds"),
            "frame_intent": shot.get("frame_intent"),
            "observable_action": shot.get("observable_action"),
            "camera": shot.get("camera"),
            "audio_intent": shot.get("audio_intent"),
            **({"sound_effects": list(shot["sound_effects"])} if isinstance(shot.get("sound_effects"), list) and shot["sound_effects"] else {}),
            "eligible_route_variant_ids": sorted(candidates),
            "continuity_locks": list(direction.get("continuity_locks") or ()),
            "narrative_role": direction.get("narrative_role"),
            "transition_in": direction.get("transition_in"),
            "audio_bridge": direction.get("audio_bridge"),
        })
    blocker_list = sorted(blockers, key=lambda item: (item["path"], item["code"]))
    proposal = {
        "schema_version": PACK_PROPOSAL_VERSION,
        "draft_hash": draft["draft_hash"],
        "director_plan_hash": director_plan.get("plan_hash"),
        "route_review_hash": route_review.get("review_hash"),
        "project": {
            key: draft.get(key) for key in (
                "mode", "series_id", "title", "premise", "audience", "platform",
                "language", "aspect_ratio", "target_duration_seconds",
            )
        },
        "entities": entities,
        "route_variants": [proposed_routes[key] for key in sorted(proposed_routes)],
        "shots": proposed_shots,
        "blockers": blocker_list,
        "pack_compilation_authorized": not blocker_list,
        "execution_authorized": False,
    }
    if isinstance(draft.get("soundtrack"), Mapping):
        proposal["project"]["soundtrack_asset_version_id"] = draft["soundtrack"].get("asset_version_id")
        proposal["project"]["soundtrack_gain_db"] = draft["soundtrack"].get("gain_db")
    return {**proposal, "proposal_hash": _hash(proposal)}


__all__ = ["PACK_PROPOSAL_VERSION", "compile_pack_proposal"]
