"""Exact local MiniMAX H3 variant profiles for character planning."""

from __future__ import annotations

from typing import AbstractSet

from .h3_catalog import H3_VARIANT_CONTRACTS, h3_golden_id
from .models import CapabilityKind, MediaKind, VisualClass
from .variant_profiles import InputSlot, VariantProfile
from .variant_profiles import EXECUTION_BASIS_TECHNICAL_COMPATIBILITY


PROFILE_REVISION = "minimax-h3-character-profiles-v1"


def h3_character_profiles(
    ready_contracts: AbstractSet[str] = frozenset(),
) -> tuple[VariantProfile, ...]:
    profiles = []
    for variant_id, contract_id in H3_VARIANT_CONTRACTS.items():
        visual_class = VisualClass.ANIME if ".anime." in variant_id else VisualClass.REALISTIC
        reference = variant_id.startswith("reference-video.")
        action_id = "h3-reference-video" if reference else "h3-image-video"
        feature_id = "h3-reference-video" if reference else "h3-image-video"
        slot_id = "reference" if reference else "image"
        aspect = (
            ("portrait", "landscape", "square")
            if visual_class is VisualClass.ANIME
            else ("portrait", "landscape")
        )
        motion = (
            ("gentle", "expressive")
            if visual_class is VisualClass.ANIME
            else ("natural", "presenter")
        )
        ready = contract_id in ready_contracts
        profiles.append(VariantProfile(
            provider_id="local_comfy",
            variant_id=variant_id,
            action_id=action_id,
            feature_id=feature_id,
            capability=CapabilityKind.IMAGE_TO_VIDEO,
            visual_class=visual_class,
            profile_revision=PROFILE_REVISION,
            option_values={
                "aspect": aspect,
                "motion_profile": motion,
                "quality": ("quality",),
            },
            required_option_keys=frozenset({"aspect", "motion_profile", "quality"}),
            input_slots=(InputSlot(
                slot_id,
                MediaKind.IMAGE,
                server_bound=True,
                identity_bearing=True,
            ),),
            release_ready=True,
            runtime_ready=ready,
            # A local owner-operated runtime is executable from exact model,
            # node, workflow, and license readiness. Goldens remain optional
            # quality metadata rather than an authorization ceremony.
            golden_current=False,
            golden_id=None,
            execution_basis=EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
            technical_evidence_id=(
                f"h3-runtime:{contract_id}" if ready else None
            ),
        ))
    return tuple(sorted(profiles, key=lambda item: item.key))


__all__ = ["PROFILE_REVISION", "h3_character_profiles", "h3_golden_id"]
