"""Exact-class option profiles for the pinned AutoIP 0.4.0 contract.

AutoIP is an owner-operated external route.  Its execution basis is a current
account compatibility receipt for the exact pinned transport operation, not a
quality-golden release ceremony.  Goldens may still be attached as useful
quality metadata, but they never impersonate or replace technical evidence.
"""

from __future__ import annotations

from dataclasses import replace
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from .autoip_contract import CONTRACT_ID
from .models import CapabilityKind, MediaKind, VisualClass
from .variant_profiles import (
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
    InputSlot,
    VariantProfile,
)
from .quality import GoldenCase, GoldenReceipt, RouteOrigin, evaluate_route_golden


# v4 reviews the 2026-10-22 skill: short shots, 480p up to 15s, and i2i v3.
# Resolution/duration combinations still require an exact live pricing entry.
PROFILE_REVISION = "autoip-0.4.0-options-v4"
SUPPORTED_VISUAL_CLASSES = (VisualClass.ANIME, VisualClass.REALISTIC)
_RECEIPT_ID = re.compile(r"^autoip-probe:[0-9a-f]{32}$")


def _profile(
    operation: str,
    visual_class: VisualClass,
    capability: CapabilityKind,
    *,
    options: Mapping[str, tuple[object, ...]],
    required_option_keys: frozenset[str] | None = None,
    slots: tuple[InputSlot, ...] = (),
) -> VariantProfile:
    return VariantProfile(
        provider_id="autoip",
        variant_id=f"{operation}.{visual_class.value}.autoip-0.4.0.v1",
        action_id=operation.replace("_", "-"),
        feature_id=f"provider-{operation.replace('_', '-')}",
        capability=capability,
        visual_class=visual_class,
        profile_revision=PROFILE_REVISION,
        option_values=options,
        required_option_keys=(
            frozenset(options)
            if required_option_keys is None
            else required_option_keys
        ),
        input_slots=slots,
        release_ready=False,
        runtime_ready=False,
        golden_current=False,
        execution_basis=EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
    )


def candidate_autoip_profiles() -> tuple[VariantProfile, ...]:
    """Return deterministic, non-runnable operation/class candidates."""

    result: list[VariantProfile] = []
    for visual_class in SUPPORTED_VISUAL_CLASSES:
        text_mode = ("anime",) if visual_class is VisualClass.ANIME else (
            "standard", "hd_4k", "v2", "v3"
        )
        result.extend((
            _profile(
                "text_to_image", visual_class, CapabilityKind.TEXT_TO_IMAGE,
                options={
                    "mode": text_mode,
                    "size": ("1024x1024", "1024x768", "768x1024", "960x540", "540x960"),
                },
            ),
            _profile(
                "image_to_image", visual_class, CapabilityKind.IMAGE_TO_IMAGE,
                options={
                    "mode": ("standard", "hd_4k", "v3"),
                    "size": ("original", "1024x1024", "1024x768", "768x1024", "960x540", "540x960"),
                    # The live OpenAPI default is 0.75.  Keep it first so the
                    # generic option picker does not silently choose the old
                    # frozen-contract fallback of 1.0.
                    "strength": (0.75, 0.5, 0.25, 1.0),
                },
                slots=(InputSlot("image", MediaKind.IMAGE, identity_bearing=True),),
            ),
            _profile(
                "image_to_video", visual_class, CapabilityKind.IMAGE_TO_VIDEO,
                options={
                    "duration_seconds": (5, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
                    # 第一個是預設值, 留最便宜的 480p (5 秒 30 點;
                    # 768p 是 75 點, 2k 是 180 點)。
                    "resolution": ("480p", "768p", "2k"),
                    "aspect_ratio": ("16:9", "9:16"),
                    # Random is an explicit Castle sentinel.  The adapters
                    # remove it from multipart fields so AutoIP chooses and
                    # reports the actual seed.
                    "seed": ("random",),
                },
                required_option_keys=frozenset({
                    "duration_seconds", "resolution", "aspect_ratio",
                }),
                slots=(
                    InputSlot("first_frame", MediaKind.IMAGE, identity_bearing=True),
                    InputSlot("last_frame", MediaKind.IMAGE, required=False, identity_bearing=True),
                ),
            ),
            _profile(
                "text_to_video", visual_class, CapabilityKind.TEXT_TO_VIDEO,
                options={
                    "duration_seconds": (5, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
                    # 第一個是預設值, 留最便宜的 480p (5 秒 30 點;
                    # 768p 是 75 點, 2k 是 180 點)。
                    "resolution": ("480p", "768p", "2k"),
                    "aspect_ratio": ("16:9", "9:16"),
                    "seed": ("random",),
                },
                required_option_keys=frozenset({
                    "duration_seconds", "resolution", "aspect_ratio",
                }),
            ),
            _profile(
                "lip_sync", visual_class, CapabilityKind.LIP_SYNC,
                options={
                    "aspect_ratio": ("16:9", "9:16"),
                    "audio_start_time": (0,),
                    "audio_duration": (5, 10, 15, 20, 25, 30, 31),
                },
                required_option_keys=frozenset({"aspect_ratio"}),
                slots=(
                    InputSlot("audio", MediaKind.AUDIO),
                    InputSlot("image", MediaKind.IMAGE, identity_bearing=True),
                ),
            ),
            _profile(
                "dance", visual_class, CapabilityKind.DANCE,
                # Template IDs come from a mutable live catalog and are not
                # yet frozen into Castle's signed option catalog.  Advertising
                # them as executable would create a dead route.  Keep the
                # uploaded-video lane exact and runnable; template mode stays
                # visibly absent until catalog IDs gain durable bindings.
                options={"motion_source": ("dance_video",)},
                slots=(
                    InputSlot("dance_video", MediaKind.VIDEO),
                    InputSlot("image", MediaKind.IMAGE, identity_bearing=True),
                ),
            ),
            _profile(
                "multi_subject_video", visual_class,
                CapabilityKind.MULTI_SUBJECT_VIDEO,
                options={
                    "duration_seconds": (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
                    # 第一個是預設值, 留最便宜的 480p (5 秒 30 點;
                    # 768p 是 75 點, 2k 是 180 點)。
                    "resolution": ("480p", "768p", "2k"),
                    "aspect_ratio": ("16:9", "9:16"),
                    "seed": ("random",),
                    # Castle semantic option: AutoIP 3.0 synthesizes native
                    # audio in the same forward pass.  The adapters consume
                    # this flag and never place it on the provider wire.
                    "native_audio": (True,),
                },
                required_option_keys=frozenset({
                    "duration_seconds", "resolution", "aspect_ratio",
                }),
                slots=(
                    InputSlot(
                        "subjects", MediaKind.IMAGE, identity_bearing=True,
                        minimum_count=1, maximum_count=4,
                    ),
                    InputSlot("voice_sample", MediaKind.AUDIO, required=False),
                ),
            ),
        ))
    return tuple(sorted(result, key=lambda item: item.key))


def qualified_autoip_profiles(
    compatibility_receipt: Mapping[str, object] | None,
    golden_cases: Sequence[GoldenCase],
    golden_receipts: Sequence[GoldenReceipt],
) -> tuple[VariantProfile, ...]:
    """Enable receipt-supported cells while retaining goldens as metadata."""

    profiles = candidate_autoip_profiles()
    contract_operations = frozenset(
        profile.capability.value for profile in profiles
    )
    receipt_id = (
        compatibility_receipt.get("receipt_id")
        if isinstance(compatibility_receipt, Mapping)
        else None
    )
    raw_operations = (
        compatibility_receipt.get("supported_operations")
        if isinstance(compatibility_receipt, Mapping)
        else None
    )
    supported_operations = (
        tuple(raw_operations)
        if isinstance(raw_operations, (list, tuple))
        and not isinstance(raw_operations, (str, bytes, bytearray))
        else ()
    )
    compatible = (
        isinstance(compatibility_receipt, Mapping)
        and compatibility_receipt.get("schema_version")
        == "castle.autoip-compatibility-receipt.v1"
        and compatibility_receipt.get("contract_id") == CONTRACT_ID
        and compatibility_receipt.get("persisted") is True
        and compatibility_receipt.get("current") is True
        and isinstance(receipt_id, str)
        and _RECEIPT_ID.fullmatch(receipt_id) is not None
        and bool(supported_operations)
        and all(isinstance(item, str) for item in supported_operations)
        and len(supported_operations) == len(set(supported_operations))
        and set(supported_operations).issubset(contract_operations)
    )
    result = []
    for profile in profiles:
        updated = profile
        cases = [case for case in golden_cases if (
            case.capability is profile.capability
            and case.visual_class is profile.visual_class
            and case.semantics_id == profile.variant_id
        )]
        if len(cases) == 1:
            verdict = evaluate_route_golden(
                cases[0], profile.variant_id, RouteOrigin.EXTERNAL, golden_receipts,
            )
            if verdict.activatable:
                updated = replace(
                    updated,
                    golden_current=True,
                    golden_id=verdict.golden_id,
                )
        if compatible and profile.capability.value in supported_operations:
            updated = replace(
                updated,
                runtime_ready=True,
                technical_evidence_id=receipt_id,
            )
        result.append(updated)
    return tuple(result)


AUTOIP_PROFILE_BY_KEY: Mapping[str, VariantProfile] = MappingProxyType(
    {item.key: item for item in candidate_autoip_profiles()}
)


def profile_contract_summary() -> dict[str, object]:
    profiles = candidate_autoip_profiles()
    return {
        "transport_contract": CONTRACT_ID,
        "profile_revision": PROFILE_REVISION,
        "profile_count": len(profiles),
        "operations": sorted({item.capability.value for item in profiles}),
        "visual_classes": sorted({item.visual_class.value for item in profiles}),
        "activation_ready": False,
        "excluded_operations": [],
        "exclusion_reason": None,
        "dance_template_lane": {
            "enabled": False,
            "reason": "dynamic_catalog_ids_not_durably_bound",
        },
    }


__all__ = [
    "AUTOIP_PROFILE_BY_KEY",
    "PROFILE_REVISION",
    "SUPPORTED_VISUAL_CLASSES",
    "candidate_autoip_profiles",
    "qualified_autoip_profiles",
    "profile_contract_summary",
]
