"""Pure exact-class route review for unresolved storyboard drafts.

The review is explanatory data, never an execution plan.  It exposes why a
profile is or is not selectable without selecting a provider, touching a
credential, quoting work, or queueing a task.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from provider_fabric.variant_profiles import (
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
    VariantProfile,
)


REVIEW_VERSION = "castle.storyboard-route-review.v1"


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_reason_codes(profile: VariantProfile) -> list[str]:
    reasons = []
    if profile.execution_ready:
        return reasons
    if profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
        reasons.append(
            "technical_compatibility_missing"
            if not profile.technical_evidence_id
            else "runtime_not_ready"
        )
        return reasons
    if not profile.release_ready:
        reasons.append("release_not_approved")
    if not profile.runtime_ready:
        reasons.append("runtime_not_ready")
    if not profile.golden_current or not profile.golden_id:
        reasons.append("quality_golden_missing")
    return reasons


def _profile_public(profile: VariantProfile) -> dict[str, Any]:
    reasons = _profile_reason_codes(profile)
    return {
        "profile_key": profile.key,
        "provider_id": profile.provider_id,
        "variant_id": profile.variant_id,
        "action_id": profile.action_id,
        "feature_id": profile.feature_id,
        "capability": profile.capability.value,
        "visual_class": profile.visual_class.value,
        "option_values": {key: list(values) for key, values in profile.option_values.items()},
        "required_option_keys": sorted(profile.required_option_keys),
        "input_slots": [slot.to_dict() for slot in profile.input_slots],
        "selectable_for_preparation": not reasons,
        "unavailable_reason_codes": reasons,
        "golden_id": profile.golden_id if profile.golden_current else None,
        "execution_basis": profile.execution_basis,
        "technical_evidence_id": profile.technical_evidence_id,
        "execution_evidence_id": profile.execution_evidence_id,
    }


def compile_storyboard_route_review(
    draft: Mapping[str, Any],
    profiles: Iterable[VariantProfile],
    *,
    rights_by_character: Mapping[str, Mapping[str, Any]] | None = None,
    usage_scope: str = "private",
) -> dict[str, Any]:
    """Match each shot only to profiles of its exact class and capability."""

    if not isinstance(draft, Mapping) or draft.get("schema_version") != "castle.storyboard-draft.v1":
        raise ValueError("storyboard draft is invalid")
    draft_hash = draft.get("draft_hash")
    scenes = draft.get("scenes")
    if not isinstance(draft_hash, str) or len(draft_hash) != 64 or not isinstance(scenes, list):
        raise ValueError("storyboard draft is invalid")
    profile_list = tuple(profiles)
    if any(not isinstance(item, VariantProfile) for item in profile_list):
        raise TypeError("profiles must contain VariantProfile objects")
    rights = rights_by_character or {}
    if not isinstance(rights, Mapping) or not isinstance(usage_scope, str):
        raise TypeError("rights_by_character and usage_scope are invalid")

    shots = []
    for scene in scenes:
        if not isinstance(scene, Mapping) or not isinstance(scene.get("shot"), Mapping):
            raise ValueError("storyboard scene is invalid")
        shot = scene["shot"]
        shot_id = shot.get("shot_id")
        visual_class = shot.get("visual_class")
        capability = shot.get("required_capability")
        blockers = list(shot.get("blockers") or [])
        character_ids = shot.get("character_ids")
        if not isinstance(shot_id, str) or not isinstance(blockers, list):
            raise ValueError("storyboard shot is invalid")
        matches = []
        if not blockers and isinstance(visual_class, str) and isinstance(capability, str):
            matches = [
                item for item in profile_list
                if item.visual_class.value == visual_class and item.capability.value == capability
            ]
        candidates = [_profile_public(item) for item in sorted(matches, key=lambda item: item.key)]
        if visual_class == "realistic":
            if not isinstance(character_ids, list) or not character_ids:
                raise ValueError("storyboard shot characters are invalid")
            for candidate in candidates:
                denied = any(
                    not isinstance(rights.get(character_id), Mapping)
                    or rights[character_id].get("consent_state") not in {"self_owned", "documented"}
                    or candidate["provider_id"] not in (rights[character_id].get("allowed_provider_ids") or ())
                    or candidate["capability"] not in (rights[character_id].get("allowed_capabilities") or ())
                    or usage_scope not in (rights[character_id].get("allowed_usage_scopes") or ())
                    for character_id in character_ids
                )
                if denied:
                    candidate["selectable_for_preparation"] = False
                    candidate["unavailable_reason_codes"] = [
                        *candidate["unavailable_reason_codes"], "person_rights_scope_missing",
                    ]
        selectable = [item["profile_key"] for item in candidates if item["selectable_for_preparation"]]
        status = "blocked" if blockers else ("ready_to_prepare" if selectable else "awaiting_route_readiness")
        shots.append({
            "shot_id": shot_id,
            "visual_class": visual_class,
            "required_capability": capability,
            "status": status,
            "blockers": blockers,
            "candidate_profiles": candidates,
            "selectable_profile_keys": selectable,
            "selected_profile_key": None,
        })

    payload = {
        "schema_version": REVIEW_VERSION,
        "draft_hash": draft_hash,
        "shots": shots,
        "sources": {
            "clipmode": "media_source_only",
            "autoip": "exact_profile_candidates",
            "local_comfy": "exact_profile_candidates",
        },
        "selection_authorized": False,
        "execution_authorized": False,
    }
    return {**payload, "review_hash": _hash(payload)}


__all__ = ["REVIEW_VERSION", "compile_storyboard_route_review"]
