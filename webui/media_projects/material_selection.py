"""Deterministic, read-only material recommendations for production shots."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from provider_fabric.variant_profiles import VariantProfile


MATERIAL_SELECTION_VERSION = "castle.production-material-selection.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _catalog_preference(asset: Mapping[str, Any], slot_id: str, identity_bearing: bool) -> tuple[int, str]:
    annotation = asset.get("catalog_annotation")
    annotation = annotation if isinstance(annotation, Mapping) else {}
    media_role = annotation.get("media_role", "unclassified")
    audio_role = annotation.get("audio_role", "unclassified")
    slot = slot_id.lower().replace("-", "_")
    preferred: tuple[str, ...]
    if asset.get("media_kind") == "image":
        if "outfit" in slot or "wardrobe" in slot or "clothing" in slot:
            preferred = ("outfit", "reference", "profile")
        elif "background" in slot or "scene" in slot:
            preferred = ("background", "storyboard", "reference")
        elif "prop" in slot:
            preferred = ("prop", "reference")
        elif identity_bearing:
            preferred = ("profile", "reference", "outfit")
        else:
            preferred = ("reference", "storyboard", "background")
        role = media_role
    elif asset.get("media_kind") == "video":
        if "dance" in slot:
            preferred = ("dance_template", "motion_reference", "source_clip")
        elif "motion" in slot or "reference" in slot:
            preferred = ("motion_reference", "source_clip", "dance_template")
        else:
            preferred = ("source_clip", "motion_reference", "finished")
        role = media_role
    elif asset.get("media_kind") == "audio":
        preferred = ("voice", "music", "sound_effect", "ambience") if ("voice" in slot or "audio" in slot) else ()
        role = audio_role
    else:
        preferred = ("script", "prompt", "subtitle")
        role = media_role
    if role in preferred:
        return 120 - preferred.index(role) * 20, f"catalog_role:{role}"
    return (0, "catalog_role:unclassified")


def _candidate(
    asset: Mapping[str, Any], profile: VariantProfile, character_ids: frozenset[str],
    slot_id: str, identity_bearing: bool,
) -> dict[str, Any] | None:
    projection_id = asset.get("projection_id")
    provider_id = asset.get("provider_id")
    version = asset.get("latest_version")
    linked = asset.get("character_ids")
    if (
        not isinstance(projection_id, str) or _ID.fullmatch(projection_id) is None
        or not isinstance(provider_id, str) or _ID.fullmatch(provider_id) is None
        or not isinstance(version, Mapping) or version.get("source_available") is not True
        or not isinstance(linked, list) or any(not isinstance(item, str) for item in linked)
    ):
        return None
    version_id = version.get("version_id")
    sha256 = version.get("sha256")
    byte_size = version.get("byte_size")
    if (
        not isinstance(version_id, str) or _ID.fullmatch(version_id) is None
        or not isinstance(sha256, str) or _SHA.fullmatch(sha256) is None
        or isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size <= 0
    ):
        return None
    linked_ids = frozenset(linked)
    coverage = len(character_ids & linked_ids)
    annotation = asset.get("catalog_annotation")
    annotation = annotation if isinstance(annotation, Mapping) else {}
    title = annotation.get("display_name")
    if not isinstance(title, str) or not title.strip():
        title = asset.get("metadata", {}).get("display_name") if isinstance(asset.get("metadata"), Mapping) else None
    if not isinstance(title, str) or not title.strip():
        title = projection_id
    role_score, role_reason = _catalog_preference(asset, slot_id, identity_bearing)
    return {
        "projection_id": projection_id,
        "asset_version_id": version_id,
        "provider_id": provider_id,
        "title": title.strip()[:500],
        "character_ids": sorted(linked_ids),
        "preview_available": version.get("preview_available") is True,
        "catalog_role": role_reason.removeprefix("catalog_role:"),
        "recommendation_reasons": [role_reason],
        "score": (
            (400 if linked_ids == character_ids else 300 if coverage else 0)
            + coverage * 20
            + (10 if provider_id == profile.provider_id else 0)
            + (1 if version.get("preview_available") is True else 0)
            + role_score
        ),
    }


def recommend_shot_materials(
    profile: VariantProfile,
    character_ids: Iterable[str],
    assets: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rank immutable owner-scoped assets per profile slot without selecting execution."""

    if not isinstance(profile, VariantProfile):
        raise TypeError("profile must be a VariantProfile")
    characters = frozenset(character_ids)
    if not characters or any(not isinstance(item, str) or _ID.fullmatch(item) is None for item in characters):
        raise ValueError("shot character IDs are invalid")
    asset_list = tuple(assets)
    if len(asset_list) > 1_000 or any(not isinstance(item, Mapping) for item in asset_list):
        raise ValueError("material candidate inventory is invalid")
    slots = []
    for slot in profile.input_slots:
        candidates = []
        for asset in asset_list:
            if asset.get("media_kind") != slot.media_kind.value:
                continue
            if slot.identity_bearing and asset.get("visual_class") != profile.visual_class.value:
                continue
            candidate = _candidate(asset, profile, characters, slot.slot_id, slot.identity_bearing)
            if candidate is None or (slot.identity_bearing and not characters.intersection(candidate["character_ids"])):
                continue
            candidates.append(candidate)
        candidates.sort(key=lambda item: (-item["score"], item["projection_id"], item["asset_version_id"]))
        slots.append({
            "slot_id": slot.slot_id,
            "media_kind": slot.media_kind.value,
            "required": slot.required,
            "identity_bearing": slot.identity_bearing,
            "recommended": candidates[0] if candidates else None,
            "candidates": candidates[:50],
        })
    return {
        "schema_version": MATERIAL_SELECTION_VERSION,
        "profile_key": profile.key,
        "character_ids": sorted(characters),
        "slots": slots,
        "preparation_authorized": False,
        "execution_authorized": False,
        "credits_authorized": 0,
    }


__all__ = ["MATERIAL_SELECTION_VERSION", "recommend_shot_materials"]
