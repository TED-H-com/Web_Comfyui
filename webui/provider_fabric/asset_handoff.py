"""Owner-scoped immutable media handoff into the existing route planner."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .models import MediaKind
from .store import ProviderFabricStore
from .variant_profiles import (
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
    VariantProfile,
)


REQUEST_VERSION = "castle.media-asset-handoff.v1"
RESPONSE_VERSION = "castle.media-asset-handoff.response.v1"
_VERSION_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,109}$")


def _planner_asset_id(version_id: str) -> str:
    """Namespace an immutable version so canonical lookup cannot select latest."""

    if _VERSION_ID.fullmatch(version_id) is None:
        raise ValueError("media handoff asset version cannot be routed")
    return f"provider-version:{version_id}"


def _profile_reasons(profile: VariantProfile, kind: MediaKind) -> list[str]:
    reasons: list[str] = []
    if not profile.execution_ready:
        if profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
            reasons.append(
                "technical_compatibility_missing"
                if not profile.technical_evidence_id
                else "runtime_not_ready"
            )
        else:
            if not profile.release_ready:
                reasons.append("release_not_approved")
            if not profile.runtime_ready:
                reasons.append("runtime_not_ready")
            if not profile.golden_current or not profile.golden_id:
                reasons.append("quality_golden_missing")
    # A server-bound slot means the browser may not substitute the bytes.  The
    # handoff is itself server-resolved, so it may safely bind this exact owned
    # version when it satisfies every required slot.
    required = [slot for slot in profile.input_slots if slot.required]
    if not required or any(slot.media_kind is not kind for slot in required):
        reasons.append("additional_input_required")
    return reasons


def prepare_asset_handoff(
    store: ProviderFabricStore,
    owner_user_id: str,
    asset_id: str,
    body: Mapping[str, Any],
    profiles: Iterable[VariantProfile],
    *,
    rights_by_character: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return exact-class planner requests without planning or executing work."""

    if not isinstance(store, ProviderFabricStore):
        raise TypeError("provider store is required")
    if not isinstance(body, Mapping) or set(body) != {"schema_version", "asset_version_id"}:
        raise ValueError("media handoff request is invalid")
    if body.get("schema_version") != REQUEST_VERSION:
        raise ValueError("media handoff request version is unsupported")
    version_id = body.get("asset_version_id")
    if not isinstance(version_id, str):
        raise ValueError("media handoff asset version is invalid")
    asset = store.get_owned_remote_asset(owner_user_id, asset_id)
    versions = [item for item in asset["versions"] if item.get("version_id") == version_id]
    if len(versions) != 1:
        raise KeyError(version_id)
    version = versions[0]
    if version.get("source_available") is not True:
        raise ValueError("media handoff source is unavailable")
    try:
        kind = MediaKind(asset["media_kind"])
    except (KeyError, ValueError) as error:
        raise ValueError("media handoff kind is unsupported") from error
    visual_class = asset.get("visual_class")
    if visual_class not in {"anime", "realistic", "illustration"}:
        raise ValueError("media handoff visual class is unavailable")
    character_ids = tuple(asset.get("character_ids") or ())
    metadata = asset.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    if asset_id.startswith("autoip-history:") or metadata.get("source") == "autoip_history":
        evidence = store.get_owned_remote_asset_classification(
            owner_user_id, asset_id,
        )
        if (
            evidence is None
            or evidence.get("asset_version_id") != version_id
            or evidence.get("visual_class") != visual_class
            or tuple(evidence.get("character_ids") or ()) != character_ids
        ):
            raise ValueError(
                "AutoIP history handoff requires exact classification evidence"
            )
    characters = []
    for character_id in character_ids:
        character = store.get_owned_character(owner_user_id, character_id)
        if character.get("visual_class") != visual_class:
            raise ValueError("media handoff character class differs")
        characters.append({
            "character_id": character_id,
            "visual_class": visual_class,
            "display_name": character["display_name"],
            "external_refs": [],
        })
    rights = rights_by_character or {}
    if not isinstance(rights, Mapping):
        raise TypeError("media handoff rights must be a mapping")
    planner_asset_id = _planner_asset_id(version_id)
    candidates = []
    for profile in sorted(tuple(profiles), key=lambda item: item.key):
        if not isinstance(profile, VariantProfile):
            raise TypeError("handoff profiles must contain VariantProfile values")
        if profile.visual_class.value != visual_class:
            continue
        if not any(slot.media_kind is kind for slot in profile.input_slots):
            continue
        reasons = _profile_reasons(profile, kind)
        if visual_class == "realistic" and any(
            not isinstance(rights.get(character_id), Mapping)
            or rights[character_id].get("consent_state") not in {"self_owned", "documented"}
            or profile.provider_id not in (rights[character_id].get("allowed_provider_ids") or ())
            or profile.capability.value not in (rights[character_id].get("allowed_capabilities") or ())
            or "private" not in (rights[character_id].get("allowed_usage_scopes") or ())
            for character_id in character_ids
        ):
            reasons.append("person_rights_scope_missing")
        candidate: dict[str, Any] = {
            "profile_key": profile.key,
            "provider_id": profile.provider_id,
            "variant_id": profile.variant_id,
            "action_id": profile.action_id,
            "feature_id": profile.feature_id,
            "capability": profile.capability.value,
            "visual_class": visual_class,
            "option_values": {
                key: list(values) for key, values in profile.option_values.items()
            },
            "selectable": not reasons,
            "unavailable_reason_codes": reasons,
            "planner_request": None,
        }
        if not reasons:
            options = {key: values[0] for key, values in profile.option_values.items()}
            planner_request = {
                "schema_version": 1,
                "capability": profile.capability.value,
                "visual_class": visual_class,
                "characters": characters,
                "assets": [{
                    "asset_id": planner_asset_id,
                    "kind": kind.value,
                    "uri": f"castle://provider-assets/{asset_id}/{version_id}",
                    "visual_class": visual_class,
                    "character_ids": list(character_ids),
                    "mime_type": version.get("mime_type"),
                    "byte_size": version.get("byte_size"),
                    "sha256": version.get("sha256"),
                    "duration_seconds": version.get("duration_seconds"),
                }],
                "options": options,
                "preferred_providers": [profile.provider_id],
                "provider_id": profile.provider_id,
                # External provider cost is obtained by the read-only quote
                # path and accepted at explicit confirmation time.  The
                # handoff must neither invent nor require a spend ceiling.
                "max_credits": 0 if profile.provider_id == "local_comfy" else None,
            }
            # Exact character-linked work retains its server-authorized grant.
            # A classed but unlinked owner asset is generic media and therefore
            # carries no character permission binding into the planner.
            if character_ids:
                planner_request.update({
                    "action_id": profile.action_id,
                    "feature_id": profile.feature_id,
                    "profile_key": profile.key,
                })
            candidate["planner_request"] = planner_request
        candidates.append(candidate)
    return {
        "schema_version": RESPONSE_VERSION,
        "ok": True,
        "asset": {
            "asset_id": asset_id,
            "asset_version_id": version_id,
            "planner_asset_id": planner_asset_id,
            "kind": kind.value,
            "visual_class": visual_class,
            "character_ids": list(character_ids),
        },
        "candidates": candidates,
        "authority": {
            "owner_scoped": True,
            "asset_version_server_resolved": True,
            "profile_catalog_server_resolved": True,
            "provider_submission_authorized": False,
            "gpu_execution_authorized": False,
            "credits_authorized": 0,
        },
    }


__all__ = ["REQUEST_VERSION", "RESPONSE_VERSION", "prepare_asset_handoff"]
