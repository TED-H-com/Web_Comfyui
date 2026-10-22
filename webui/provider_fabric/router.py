"""Deterministic, identity-class-aware provider routing."""

from __future__ import annotations

from collections.abc import Iterable

from .models import (
    CapabilityOffering,
    EvidenceState,
    RouteDecision,
    RouteIntent,
    RouteRejection,
    VisualClass,
)


POLICY_VERSION = "provider-fabric-route-v1"


class DeterministicRouter:
    """Select exactly one proven offering or return a non-routable decision.

    The router never treats anime and real identities as interchangeable.  It
    also never infers support from a provider name: an offering must be enabled,
    verified, and explicitly list the requested visual class and options.
    """

    policy_version = POLICY_VERSION

    def route(
        self,
        intent: RouteIntent,
        offerings: Iterable[CapabilityOffering],
    ) -> RouteDecision:
        ordered = sorted(offerings, key=lambda item: (item.provider_id, item.variant_id))
        rejections: list[RouteRejection] = []
        eligible: list[CapabilityOffering] = []

        character_mismatch = any(
            item.visual_class is not intent.visual_class for item in intent.characters
        )
        asset_mismatch = any(
            item.visual_class is not None and item.visual_class is not intent.visual_class
            for item in intent.assets
        )

        for offering in ordered:
            reason: tuple[str, str] | None = None
            if intent.visual_class is VisualClass.UNKNOWN:
                reason = (
                    "unknown_visual_class",
                    "Unknown visual class cannot enter a type-sensitive route.",
                )
            elif VisualClass.UNKNOWN in offering.visual_classes:
                reason = (
                    "offering_unknown_visual_class",
                    "An offering cannot claim support for an unknown visual class.",
                )
            elif character_mismatch:
                reason = (
                    "character_visual_class_mismatch",
                    "A selected character belongs to a different visual class.",
                )
            elif asset_mismatch:
                reason = (
                    "asset_visual_class_mismatch",
                    "An identity-bearing asset belongs to a different visual class.",
                )
            elif offering.capability is not intent.capability:
                reason = ("capability_mismatch", "The offering does not implement the requested capability.")
            elif not offering.enabled:
                reason = ("offering_disabled", "The offering is administratively disabled.")
            elif offering.evidence is not EvidenceState.VERIFIED:
                reason = ("offering_unproven", "The offering has not passed its visual-class verification.")
            elif intent.visual_class not in offering.visual_classes:
                reason = (
                    "visual_class_mismatch",
                    "The offering is not verified for the requested visual class.",
                )
            elif not intent.required_options.issubset(offering.option_keys):
                missing = sorted(intent.required_options - offering.option_keys)
                reason = (
                    "required_option_missing",
                    f"The offering lacks required options: {', '.join(missing)}.",
                )

            if reason:
                rejections.append(RouteRejection(offering.key, reason[0], reason[1]))
            else:
                eligible.append(offering)

        preference_rank = {
            provider_id: index for index, provider_id in enumerate(intent.preferred_providers)
        }
        fallback_rank = len(preference_rank)
        eligible.sort(
            key=lambda item: (
                preference_rank.get(item.provider_id, fallback_rank),
                item.priority,
                item.provider_id,
                item.variant_id,
            )
        )
        selected = eligible[0] if eligible else None
        return RouteDecision(
            selected_provider_id=selected.provider_id if selected else None,
            selected_variant_id=selected.variant_id if selected else None,
            candidate_keys=tuple(item.key for item in eligible),
            rejections=tuple(rejections),
            policy_version=self.policy_version,
        )
