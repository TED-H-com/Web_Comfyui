"""Deterministic, zero-spend shadow routing for exact variant profiles.

Shadow routing is an audit surface, not a submission surface.  It accepts only
in-memory contracts, performs no provider or ComfyUI I/O, never resolves a
credential, never writes a store/outbox, and returns no confirmation token.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .models import CapabilityOffering, CharacterRef, EvidenceState, RouteDecision
from .router import DeterministicRouter, POLICY_VERSION
from .variant_profiles import (
    CharacterCapabilityGrant,
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
    MaterializedVariantRequest,
    VariantProfile,
    VariantProfileError,
    materialize_variant_request,
    normalize_asset_selection,
    normalize_submitted_options,
    profile_contract_errors,
)


SHADOW_SCHEMA_VERSION = "castle.provider-shadow-route.v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


class ShadowRoutingError(ValueError):
    """The shadow catalog itself is ambiguous or malformed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _provider_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ShadowRoutingError(
            "shadow_request_invalid", "Preferred providers must use exact lowercase identifiers."
        )
    return value


@dataclass(frozen=True, slots=True)
class ShadowRouteRequest:
    """One trusted character grant plus inert user/server selections."""

    grant: CharacterCapabilityGrant
    submitted_options: Mapping[str, Any] = field(default_factory=dict)
    client_assets: Mapping[str, Any] = field(default_factory=dict)
    server_assets: Mapping[str, Any] = field(default_factory=dict)
    additional_characters: tuple[Any, ...] = ()
    preferred_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.grant, CharacterCapabilityGrant):
            raise ShadowRoutingError(
                "shadow_request_invalid", "Shadow request requires a trusted capability grant."
            )
        try:
            submitted_options = normalize_submitted_options(self.submitted_options)
            client_assets = normalize_asset_selection(
                self.client_assets, label="client assets"
            )
            server_assets = normalize_asset_selection(
                self.server_assets, label="server assets"
            )
        except VariantProfileError as error:
            raise ShadowRoutingError(
                error.code, "Shadow request inputs failed their inert public contract."
            ) from error
        if set(client_assets).intersection(server_assets):
            raise ShadowRoutingError(
                "input_slot_ambiguous",
                "An input slot cannot be present in both client and server selections.",
            )
        all_assets = tuple(
            item
            for selection in (*client_assets.values(), *server_assets.values())
            for item in (selection if isinstance(selection, tuple) else (selection,))
        )
        asset_ids = tuple(item.asset_id for item in all_assets)
        if len(asset_ids) != len(set(asset_ids)):
            raise ShadowRoutingError(
                "duplicate_input_asset",
                "One asset cannot satisfy multiple exact input slots.",
            )
        characters = tuple(self.additional_characters)
        if len(characters) > 15 or any(
            not isinstance(item, CharacterRef) for item in characters
        ):
            raise ShadowRoutingError(
                "character_contract_invalid",
                "Additional character references are invalid or too large.",
            )
        character_ids = (self.grant.character.character_id,) + tuple(
            item.character_id for item in characters
        )
        if len(character_ids) != len(set(character_ids)) or any(
            item.visual_class is not self.grant.character.visual_class
            for item in characters
        ):
            raise ShadowRoutingError(
                "character_contract_invalid",
                "Additional characters must be unique and use the exact visual class.",
            )
        object.__setattr__(self, "submitted_options", submitted_options)
        object.__setattr__(self, "client_assets", client_assets)
        object.__setattr__(self, "server_assets", server_assets)
        object.__setattr__(self, "additional_characters", characters)
        if isinstance(self.preferred_providers, (str, bytes, bytearray)):
            raise ShadowRoutingError(
                "shadow_request_invalid",
                "Preferred providers must be a sequence of exact identifiers.",
            )
        preferred = tuple(_provider_id(item) for item in self.preferred_providers)
        if len(preferred) > 16 or len(preferred) != len(set(preferred)):
            raise ShadowRoutingError(
                "shadow_request_invalid", "Preferred providers are duplicate or too large."
            )
        object.__setattr__(self, "preferred_providers", preferred)


@dataclass(frozen=True, slots=True)
class ShadowCandidate:
    profile_key: str
    provider_id: str | None
    variant_id: str | None
    eligible: bool
    selected: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_key": self.profile_key,
            "provider_id": self.provider_id,
            "variant_id": self.variant_id,
            "eligible": self.eligible,
            "selected": self.selected,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class ShadowRouteResult:
    """Public-safe shadow result with hard-coded no-side-effect assertions."""

    selected_provider_id: str | None
    selected_variant_id: str | None
    candidates: tuple[ShadowCandidate, ...]
    policy_version: str
    capability: str
    visual_class: str
    character_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]
    option_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "mode": "shadow_only",
            "routable": bool(self.selected_provider_id and self.selected_variant_id),
            "selected_provider_id": self.selected_provider_id,
            "selected_variant_id": self.selected_variant_id,
            "policy_version": self.policy_version,
            "capability": self.capability,
            "visual_class": self.visual_class,
            "character_ids": list(self.character_ids),
            "asset_ids": list(self.asset_ids),
            "option_keys": list(self.option_keys),
            "candidates": [item.to_dict() for item in self.candidates],
            "transport_dispatched": False,
            "provider_probe_performed": False,
            "credential_resolved": False,
            "persisted": False,
            "outbox_created": False,
            "credits_spent": 0,
            "confirmation_available": False,
        }


@dataclass(slots=True)
class _CandidateState:
    key: str
    profile: VariantProfile | None
    offering: CapabilityOffering | None
    materialized: MaterializedVariantRequest | None
    reasons: list[str]

    def add(self, *codes: str) -> None:
        for code in codes:
            if code and code not in self.reasons:
                self.reasons.append(code)


def _catalog_by_key(
    values: Sequence[Any],
    expected_type: type,
    label: str,
) -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, expected_type):
            raise ShadowRoutingError(
                "shadow_catalog_invalid", f"{label} contains an invalid contract object."
            )
        key = value.key
        if key in catalog:
            raise ShadowRoutingError(
                "shadow_catalog_ambiguous", f"{label} contains a duplicate key."
            )
        catalog[key] = value
    return catalog


def _offering_contract_errors(
    profile: VariantProfile,
    offering: CapabilityOffering,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if offering.provider_id != profile.provider_id or offering.variant_id != profile.variant_id:
        reasons.append("offering_identity_mismatch")
    if offering.capability is not profile.capability:
        reasons.append("offering_capability_mismatch")
    if offering.visual_classes != frozenset({profile.visual_class}):
        reasons.append("offering_visual_scope_not_exact")
    if offering.option_keys != frozenset(profile.option_values):
        reasons.append("offering_option_contract_mismatch")
    if (
        offering.evidence is EvidenceState.VERIFIED
        and offering.evidence_id != profile.execution_evidence_id
    ):
        reasons.append(
            "offering_technical_evidence_mismatch"
            if profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY
            else "offering_golden_evidence_mismatch"
        )
    return tuple(reasons)


def _materialization_identity(value: MaterializedVariantRequest) -> str:
    """Compare complete inert request semantics without publishing private URIs."""

    payload = {
        "intent": value.intent.to_dict(),
        "options": dict(value.options),
        "slot_asset_ids": dict(value.slot_asset_ids),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _safe_asset_ids(request: ShadowRouteRequest) -> tuple[str, ...]:
    values: set[str] = set()
    for mapping in (request.client_assets, request.server_assets):
        for selection in mapping.values():
            for item in (selection if isinstance(selection, tuple) else (selection,)):
                asset_id = getattr(item, "asset_id", None)
                if isinstance(asset_id, str) and _ID_RE.fullmatch(asset_id):
                    values.add(asset_id)
    return tuple(sorted(values))


def shadow_route(
    request: ShadowRouteRequest,
    profiles: Sequence[VariantProfile],
    offerings: Sequence[CapabilityOffering],
) -> ShadowRouteResult:
    """Audit the exact route without touching transport, storage, or secrets."""

    if not isinstance(request, ShadowRouteRequest):
        raise TypeError("request must be a ShadowRouteRequest")
    profile_catalog = _catalog_by_key(tuple(profiles), VariantProfile, "profiles")
    offering_catalog = _catalog_by_key(tuple(offerings), CapabilityOffering, "offerings")

    states: list[_CandidateState] = []
    for key in request.grant.profile_keys:
        profile = profile_catalog.get(key)
        offering = offering_catalog.get(key)
        state = _CandidateState(key, profile, offering, None, [])
        states.append(state)
        if request.grant.character.visual_class.value == "unknown":
            state.add("unknown_visual_class")
        if not request.grant.enabled:
            state.add("action_disabled")
        if not request.grant.runtime_ready:
            state.add("action_runtime_unready")
        if profile is None:
            state.add("profile_missing")
            continue
        state.add(*profile_contract_errors(profile, request.grant))
        if not profile.execution_ready:
            if profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
                state.add(
                    "profile_technical_evidence_missing"
                    if not profile.technical_evidence_id
                    else "profile_runtime_unready"
                )
            else:
                if not profile.release_ready:
                    state.add("profile_release_unready")
                if not profile.runtime_ready:
                    state.add("profile_runtime_unready")
                if not profile.golden_current:
                    state.add("profile_golden_stale")
        if offering is None:
            state.add("offering_missing")
        else:
            state.add(*_offering_contract_errors(profile, offering))
        try:
            state.materialized = materialize_variant_request(
                profile,
                request.grant,
                submitted_options=request.submitted_options,
                client_assets=request.client_assets,
                server_assets=request.server_assets,
                additional_characters=request.additional_characters,
                preferred_providers=request.preferred_providers,
            )
        except VariantProfileError as error:
            state.add(error.code)

    viable = [
        state
        for state in states
        if not state.reasons and state.offering is not None and state.materialized is not None
    ]
    identities = {
        _materialization_identity(state.materialized)
        for state in viable
        if state.materialized is not None
    }
    if len(identities) > 1:
        for state in viable:
            state.add("candidate_intent_divergence")
        viable = []

    decision = RouteDecision(None, None, (), (), POLICY_VERSION)
    if viable:
        common_intent = viable[0].materialized.intent
        decision = DeterministicRouter().route(
            common_intent,
            tuple(state.offering for state in viable if state.offering is not None),
        )
        viable_keys = {state.key for state in viable}
        decision_keys = tuple(decision.candidate_keys)
        rejected_keys = tuple(item.offering_key for item in decision.rejections)
        selected_decision_key = (
            f"{decision.selected_provider_id}:{decision.selected_variant_id}"
            if decision.selected_provider_id and decision.selected_variant_id
            else None
        )
        if (
            len(decision_keys) != len(set(decision_keys))
            or len(rejected_keys) != len(set(rejected_keys))
            or not set(decision_keys).issubset(viable_keys)
            or not set(rejected_keys).issubset(viable_keys)
            or set(decision_keys).intersection(rejected_keys)
            or (selected_decision_key is not None and selected_decision_key not in decision_keys)
        ):
            raise ShadowRoutingError(
                "shadow_router_invariant",
                "The pure router returned a decision outside the viable shadow catalog.",
            )
        state_by_key = {state.key: state for state in viable}
        for rejection in decision.rejections:
            state = state_by_key.get(rejection.offering_key)
            if state is not None:
                state.add(rejection.reason_code)

    eligible_keys = set(decision.candidate_keys)
    selected_key = (
        f"{decision.selected_provider_id}:{decision.selected_variant_id}"
        if decision.selected_provider_id and decision.selected_variant_id
        else None
    )
    public_candidates: list[ShadowCandidate] = []
    for state in states:
        public_candidates.append(
            ShadowCandidate(
                profile_key=state.key,
                provider_id=state.profile.provider_id if state.profile else None,
                variant_id=state.profile.variant_id if state.profile else None,
                eligible=state.key in eligible_keys and not state.reasons,
                selected=state.key == selected_key and not state.reasons,
                reason_codes=tuple(state.reasons),
            )
        )

    materialized = next(
        (state.materialized for state in states if state.materialized is not None),
        None,
    )
    character_ids = (
        tuple(item.character_id for item in materialized.intent.characters)
        if materialized is not None
        else (request.grant.character.character_id,)
    )
    return ShadowRouteResult(
        selected_provider_id=decision.selected_provider_id,
        selected_variant_id=decision.selected_variant_id,
        candidates=tuple(public_candidates),
        policy_version=decision.policy_version,
        capability=request.grant.capability.value,
        visual_class=request.grant.character.visual_class.value,
        character_ids=character_ids,
        asset_ids=_safe_asset_ids(request),
        option_keys=tuple(sorted(str(key) for key in request.submitted_options)),
    )


__all__ = [
    "SHADOW_SCHEMA_VERSION",
    "ShadowCandidate",
    "ShadowRouteRequest",
    "ShadowRouteResult",
    "ShadowRoutingError",
    "shadow_route",
]
