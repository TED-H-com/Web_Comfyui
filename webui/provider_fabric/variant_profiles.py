"""Strict, provider-neutral variant profiles for zero-spend route compilation.

This module is deliberately pure.  It has no store, credential, transport,
ComfyUI, or orchestrator dependency.  A caller must supply a trusted Castle
character capability grant, exact provider profiles, and already-authorized
asset references.  The result is an inert :class:`RouteIntent`; it is not an
execution request and cannot submit or persist work.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from .models import (
    CapabilityKind,
    CharacterRef,
    MediaAsset,
    MediaKind,
    RouteIntent,
    VisualClass,
)


PROFILE_SCHEMA_VERSION = "castle.provider-variant-profile.v1"
EXECUTION_BASIS_QUALITY_GOLDEN = "quality_golden"
EXECUTION_BASIS_TECHNICAL_COMPATIBILITY = "technical_compatibility"
_EXECUTION_BASES = frozenset({
    EXECUTION_BASIS_QUALITY_GOLDEN,
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
})
MAX_PROFILE_KEYS = 64
MAX_OPTION_KEYS = 64
MAX_OPTION_VALUES = 128
MAX_INPUT_SLOTS = 32

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_PROFILE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,256}$")
_OPTION_RE = re.compile(r"^[a-z][a-z0-9._:-]{0,127}$")
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credential_value",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "secret_value",
        "session",
        "session_cookie",
        "token",
        "webhook_secret",
        "xsrf_token",
    }
)
_SECRET_SUFFIXES = ("_api_key", "_cookie", "_password", "_secret", "_token")
_SERVER_BINDING_KEYS = frozenset(
    {
        "account",
        "account_id",
        "api_base",
        "builder",
        "checkpoint",
        "checkpoint_id",
        "endpoint",
        "lora",
        "lora_id",
        "model",
        "model_id",
        "provider",
        "provider_account_id",
        "provider_id",
        "variant",
        "variant_id",
        "workflow",
        "workflow_id",
    }
)
_SERVER_BINDING_SUFFIXES = tuple(f"_{item}" for item in sorted(_SERVER_BINDING_KEYS))

OptionScalar: TypeAlias = str | int | float | bool


class VariantProfileError(ValueError):
    """A stable, non-sensitive profile compilation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise VariantProfileError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("profile_contract_invalid", f"{label} must be an exact lowercase identifier.")
    return value


def _profile_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or ":" not in value
        or not _PROFILE_KEY_RE.fullmatch(value)
    ):
        _fail("profile_contract_invalid", "profile key is invalid.")
    return value


def _option_key(value: Any, label: str = "option key") -> str:
    if not isinstance(value, str) or not _OPTION_RE.fullmatch(value):
        _fail("option_contract_invalid", f"{label} is invalid.")
    return value


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _key_segments(value: object) -> frozenset[str]:
    return frozenset(
        item
        for item in re.split(r"[.:-]+", str(value).strip().lower())
        if item
    )


def _secret_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in _SECRET_KEYS
        or normalized.endswith(_SECRET_SUFFIXES)
        or bool(_key_segments(value).intersection(_SECRET_KEYS))
    )


def _server_binding_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in _SERVER_BINDING_KEYS
        or normalized.endswith(_SERVER_BINDING_SUFFIXES)
        or bool(_key_segments(value).intersection(_SERVER_BINDING_KEYS))
    )


def _scalar(value: Any, label: str) -> OptionScalar:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            _fail("option_contract_invalid", f"{label} exceeds the safe integer range.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1e100:
            _fail("option_contract_invalid", f"{label} must be finite and bounded.")
        return value
    if isinstance(value, str):
        if (
            not value
            or len(value) > 500
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            _fail("option_contract_invalid", f"{label} is invalid.")
        return value
    _fail("option_contract_invalid", f"{label} must be a scalar value.")


def _scalar_token(value: OptionScalar) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _scalar_allowed(value: OptionScalar, allowed: tuple[OptionScalar, ...]) -> bool:
    token = _scalar_token(value)
    return any(_scalar_token(candidate) == token for candidate in allowed)


def _option_catalog(
    raw: Mapping[str, Any] | None,
    *,
    label: str,
) -> Mapping[str, tuple[OptionScalar, ...]]:
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, Mapping) or len(raw) > MAX_OPTION_KEYS:
        _fail("option_contract_invalid", f"{label} is invalid or too large.")
    normalized: dict[str, tuple[OptionScalar, ...]] = {}
    for raw_key, raw_values in raw.items():
        key = _option_key(raw_key)
        if _secret_key(key):
            _fail("secret_field_forbidden", "Credential-shaped option fields are forbidden.")
        if _server_binding_key(key):
            _fail(
                "server_binding_forbidden",
                "Provider, model, workflow, and account bindings are server-owned.",
            )
        if (
            not isinstance(raw_values, (list, tuple))
            or isinstance(raw_values, (str, bytes, bytearray))
            or not raw_values
            or len(raw_values) > MAX_OPTION_VALUES
        ):
            _fail("option_contract_invalid", f"{label} values are invalid.")
        values = tuple(_scalar(item, f"{label} value") for item in raw_values)
        tokens = tuple(_scalar_token(item) for item in values)
        if len(tokens) != len(set(tokens)):
            _fail("option_contract_invalid", f"{label} contains duplicate values.")
        normalized[key] = values
    return MappingProxyType(dict(sorted(normalized.items())))


def _submitted_options(raw: Mapping[str, Any] | None) -> Mapping[str, OptionScalar]:
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, Mapping) or len(raw) > MAX_OPTION_KEYS:
        _fail("option_value_invalid", "Submitted options are invalid or too large.")
    normalized: dict[str, OptionScalar] = {}
    for raw_key, raw_value in raw.items():
        key = _option_key(raw_key, "submitted option key")
        if _secret_key(key):
            _fail("secret_field_forbidden", "Credential-shaped option fields are forbidden.")
        if _server_binding_key(key):
            _fail(
                "client_binding_forbidden",
                "Provider, model, workflow, and account bindings are server-owned.",
            )
        normalized[key] = _scalar(raw_value, "submitted option value")
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class InputSlot:
    """One exact variant input slot.

    ``server_bound`` means the browser may not select or replace the asset.
    ``identity_bearing`` requires the asset to match the primary character and
    the profile's visual class.
    """

    slot_id: str
    media_kind: MediaKind
    required: bool = True
    server_bound: bool = False
    identity_bearing: bool = False
    minimum_count: int | None = None
    maximum_count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot_id", _option_key(self.slot_id, "slot_id"))
        if not isinstance(self.media_kind, MediaKind):
            try:
                object.__setattr__(self, "media_kind", MediaKind(self.media_kind))
            except (TypeError, ValueError) as error:
                raise VariantProfileError(
                    "profile_contract_invalid", "Input slot media kind is invalid."
                ) from error
        for name in ("required", "server_bound", "identity_bearing"):
            if not isinstance(getattr(self, name), bool):
                _fail("profile_contract_invalid", f"Input slot {name} must be boolean.")
        minimum = 1 if self.required else 0 if self.minimum_count is None else self.minimum_count
        if self.minimum_count is not None:
            minimum = self.minimum_count
        if (
            not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(self.maximum_count, int) or isinstance(self.maximum_count, bool)
            or minimum < 0 or self.maximum_count < 1
            or minimum > self.maximum_count or self.maximum_count > 16
            or self.required is not (minimum > 0)
        ):
            _fail("profile_contract_invalid", "Input slot cardinality is invalid.")
        object.__setattr__(self, "minimum_count", minimum)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "media_kind": self.media_kind.value,
            "required": self.required,
            "server_bound": self.server_bound,
            "identity_bearing": self.identity_bearing,
            "minimum_count": self.minimum_count,
            "maximum_count": self.maximum_count,
        }


@dataclass(frozen=True, slots=True)
class VariantProfile:
    """One provider variant with one visual class and one option contract."""

    provider_id: str
    variant_id: str
    action_id: str
    feature_id: str
    capability: CapabilityKind
    visual_class: VisualClass
    profile_revision: str
    option_values: Mapping[str, tuple[OptionScalar, ...]] = field(default_factory=dict)
    required_option_keys: frozenset[str] = field(default_factory=frozenset)
    input_slots: tuple[InputSlot, ...] = ()
    release_ready: bool = False
    runtime_ready: bool = False
    golden_current: bool = False
    golden_id: str | None = None
    execution_basis: str = EXECUTION_BASIS_QUALITY_GOLDEN
    technical_evidence_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "provider_id",
            "variant_id",
            "action_id",
            "feature_id",
            "profile_revision",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        if not isinstance(self.capability, CapabilityKind):
            try:
                object.__setattr__(self, "capability", CapabilityKind(self.capability))
            except (TypeError, ValueError) as error:
                raise VariantProfileError(
                    "profile_contract_invalid", "Profile capability is invalid."
                ) from error
        if not isinstance(self.visual_class, VisualClass):
            try:
                object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
            except (TypeError, ValueError) as error:
                raise VariantProfileError(
                    "profile_contract_invalid", "Profile visual class is invalid."
                ) from error
        if self.visual_class is VisualClass.UNKNOWN:
            _fail("profile_contract_invalid", "Unknown visual class cannot own a variant profile.")
        options = _option_catalog(self.option_values, label="profile option catalog")
        object.__setattr__(self, "option_values", options)
        required = frozenset(
            _option_key(item, "required option key") for item in self.required_option_keys
        )
        if not required.issubset(options):
            _fail("profile_contract_invalid", "Required options must exist in the profile catalog.")
        object.__setattr__(self, "required_option_keys", required)
        slots = tuple(self.input_slots)
        if len(slots) > MAX_INPUT_SLOTS or any(not isinstance(item, InputSlot) for item in slots):
            _fail("profile_contract_invalid", "Profile input slots are invalid or too large.")
        slot_ids = [item.slot_id for item in slots]
        if len(slot_ids) != len(set(slot_ids)):
            _fail("profile_contract_invalid", "Profile input slot identifiers must be unique.")
        object.__setattr__(self, "input_slots", tuple(sorted(slots, key=lambda item: item.slot_id)))
        for name in ("release_ready", "runtime_ready", "golden_current"):
            if not isinstance(getattr(self, name), bool):
                _fail("profile_contract_invalid", f"{name} must be boolean.")
        if self.golden_id is not None:
            object.__setattr__(self, "golden_id", _identifier(self.golden_id, "golden_id"))
        if self.golden_current and not self.golden_id:
            _fail("profile_contract_invalid", "A current golden requires a golden_id.")
        if (
            not isinstance(self.execution_basis, str)
            or self.execution_basis not in _EXECUTION_BASES
        ):
            _fail("profile_contract_invalid", "Profile execution basis is invalid.")
        if self.technical_evidence_id is not None:
            object.__setattr__(
                self,
                "technical_evidence_id",
                _identifier(self.technical_evidence_id, "technical_evidence_id"),
            )
        if (
            self.execution_basis == EXECUTION_BASIS_QUALITY_GOLDEN
            and self.technical_evidence_id is not None
        ):
            _fail(
                "profile_contract_invalid",
                "Quality-golden profiles cannot carry technical execution evidence.",
            )

    @property
    def key(self) -> str:
        return f"{self.provider_id}:{self.variant_id}"

    @property
    def server_bound_slots(self) -> frozenset[str]:
        return frozenset(item.slot_id for item in self.input_slots if item.server_bound)

    @property
    def execution_evidence_id(self) -> str | None:
        """Return the evidence identifier selected by this profile's basis."""

        if self.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
            return self.technical_evidence_id
        return self.golden_id

    @property
    def execution_ready(self) -> bool:
        """Whether this profile has the evidence required by its execution basis."""

        if self.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
            return bool(self.runtime_ready and self.technical_evidence_id)
        return bool(
            self.release_ready
            and self.runtime_ready
            and self.golden_current
            and self.golden_id
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "provider_id": self.provider_id,
            "variant_id": self.variant_id,
            "action_id": self.action_id,
            "feature_id": self.feature_id,
            "capability": self.capability.value,
            "visual_class": self.visual_class.value,
            "profile_revision": self.profile_revision,
            "option_values": {
                key: list(values) for key, values in self.option_values.items()
            },
            "required_option_keys": sorted(self.required_option_keys),
            "input_slots": [item.to_dict() for item in self.input_slots],
            "release_ready": self.release_ready,
            "runtime_ready": self.runtime_ready,
            "golden_current": self.golden_current,
            "golden_id": self.golden_id,
            "execution_basis": self.execution_basis,
            "technical_evidence_id": self.technical_evidence_id,
            "execution_ready": self.execution_ready,
            "execution_evidence_id": self.execution_evidence_id,
        }


@dataclass(frozen=True, slots=True)
class CharacterCapabilityGrant:
    """Trusted Castle authorization for one character action entry point.

    A grant may name multiple provider profiles, but each profile must remain a
    subset of the grant's class-specific options and exact server-bound slots.
    """

    character: CharacterRef
    action_id: str
    feature_id: str
    capability: CapabilityKind
    profile_keys: tuple[str, ...]
    allowed_options: Mapping[str, tuple[OptionScalar, ...]] = field(default_factory=dict)
    server_bound_slots: frozenset[str] = field(default_factory=frozenset)
    enabled: bool = False
    runtime_ready: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.character, CharacterRef):
            _fail("authority_contract_invalid", "Grant character must be a CharacterRef.")
        object.__setattr__(self, "action_id", _identifier(self.action_id, "action_id"))
        object.__setattr__(self, "feature_id", _identifier(self.feature_id, "feature_id"))
        if not isinstance(self.capability, CapabilityKind):
            try:
                object.__setattr__(self, "capability", CapabilityKind(self.capability))
            except (TypeError, ValueError) as error:
                raise VariantProfileError(
                    "authority_contract_invalid", "Grant capability is invalid."
                ) from error
        keys = tuple(_profile_key(item) for item in self.profile_keys)
        if not keys or len(keys) > MAX_PROFILE_KEYS or len(keys) != len(set(keys)):
            _fail("authority_contract_invalid", "Grant profile keys are empty, duplicate, or too large.")
        object.__setattr__(self, "profile_keys", tuple(sorted(keys)))
        object.__setattr__(
            self,
            "allowed_options",
            _option_catalog(self.allowed_options, label="grant option catalog"),
        )
        bound = frozenset(
            _option_key(item, "server-bound slot") for item in self.server_bound_slots
        )
        object.__setattr__(self, "server_bound_slots", bound)
        for name in ("enabled", "runtime_ready"):
            if not isinstance(getattr(self, name), bool):
                _fail("authority_contract_invalid", f"Grant {name} must be boolean.")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character.character_id,
            "visual_class": self.character.visual_class.value,
            "action_id": self.action_id,
            "feature_id": self.feature_id,
            "capability": self.capability.value,
            "profile_keys": list(self.profile_keys),
            "allowed_options": {
                key: list(values) for key, values in self.allowed_options.items()
            },
            "server_bound_slots": sorted(self.server_bound_slots),
            "enabled": self.enabled,
            "runtime_ready": self.runtime_ready,
        }


@dataclass(frozen=True, slots=True)
class MaterializedVariantRequest:
    """An inert, exact route input compiled from one profile."""

    profile: VariantProfile
    intent: RouteIntent
    options: Mapping[str, OptionScalar]
    slot_asset_ids: Mapping[str, str | tuple[str, ...]]
    authorized_character_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.profile, VariantProfile) or not isinstance(self.intent, RouteIntent):
            _fail("materialization_invalid", "Materialized request types are invalid.")
        if not self.profile.execution_ready or not self.profile.execution_evidence_id:
            _fail(
                "materialization_invalid",
                "Materialized request profile has no current execution evidence.",
            )
        if (
            self.intent.capability is not self.profile.capability
            or self.intent.visual_class is not self.profile.visual_class
        ):
            _fail(
                "materialization_invalid",
                "Materialized intent does not match the exact profile.",
            )
        options = _submitted_options(self.options)
        for key, value in options.items():
            if key not in self.profile.option_values or not _scalar_allowed(
                value, self.profile.option_values[key]
            ):
                _fail(
                    "materialization_invalid",
                    "Materialized options do not match the exact profile.",
                )
        if not self.profile.required_option_keys.issubset(options):
            _fail(
                "materialization_invalid",
                "Materialized request is missing required profile options.",
            )
        if self.intent.required_options != frozenset(options):
            _fail(
                "materialization_invalid",
                "Materialized route option keys are inconsistent.",
            )
        character_ids = tuple(item.character_id for item in self.intent.characters)
        if (
            not character_ids
            or len(character_ids) != len(set(character_ids))
            or any(
                item.visual_class is not self.profile.visual_class
                for item in self.intent.characters
            )
        ):
            _fail(
                "materialization_invalid",
                "Materialized characters do not match the exact profile.",
            )
        authorized = tuple(
            _identifier(item, "authorized_character_id")
            for item in self.authorized_character_ids
        )
        if len(authorized) != len(set(authorized)) or not set(authorized).issubset(character_ids):
            _fail("materialization_invalid", "Materialized character grants are invalid.")
        if not isinstance(self.slot_asset_ids, Mapping):
            _fail("materialization_invalid", "Materialized slot bindings are invalid.")
        slot_ids = {item.slot_id for item in self.profile.input_slots}
        bindings: dict[str, str | tuple[str, ...]] = {}
        flat_bindings: list[tuple[str, str]] = []
        for raw_slot, raw_asset_ids in self.slot_asset_ids.items():
            slot = _option_key(raw_slot, "materialized slot")
            if slot not in slot_ids:
                _fail("materialization_invalid", "Materialized slot is not in the profile.")
            if isinstance(raw_asset_ids, str):
                asset_ids = (_identifier(raw_asset_ids, "materialized asset_id"),)
                bindings[slot] = asset_ids[0]
            elif (
                isinstance(raw_asset_ids, (list, tuple))
                and raw_asset_ids
                and len(raw_asset_ids) <= 16
            ):
                asset_ids = tuple(
                    _identifier(item, "materialized asset_id") for item in raw_asset_ids
                )
                bindings[slot] = asset_ids
            else:
                _fail("materialization_invalid", "Materialized slot cardinality is invalid.")
            profile_slot = next(item for item in self.profile.input_slots if item.slot_id == slot)
            if not profile_slot.minimum_count <= len(asset_ids) <= profile_slot.maximum_count:
                _fail("materialization_invalid", "Materialized slot cardinality is invalid.")
            flat_bindings.extend((slot, asset_id) for asset_id in asset_ids)
        bound_asset_ids = tuple(asset_id for _slot, asset_id in flat_bindings)
        intent_asset_ids = tuple(item.asset_id for item in self.intent.assets)
        if (
            len(bound_asset_ids) != len(set(bound_asset_ids))
            or set(bound_asset_ids) != set(intent_asset_ids)
            or len(intent_asset_ids) != len(set(intent_asset_ids))
        ):
            _fail(
                "materialization_invalid",
                "Materialized assets do not match the route intent.",
            )
        slots = {item.slot_id: item for item in self.profile.input_slots}
        if any(item.minimum_count and item.slot_id not in bindings for item in slots.values()):
            _fail(
                "materialization_invalid",
                "Materialized request is missing a required input slot.",
            )
        assets_by_id = {item.asset_id: item for item in self.intent.assets}
        selected_character_ids = set(authorized)
        for slot_id, asset_id in flat_bindings:
            slot = slots[slot_id]
            asset = assets_by_id[asset_id]
            if asset.kind is not slot.media_kind:
                _fail(
                    "materialization_invalid",
                    "Materialized input uses the wrong media kind.",
                )
            if asset.visual_class is not None and asset.visual_class is not self.profile.visual_class:
                _fail(
                    "materialization_invalid",
                    "Materialized input belongs to another visual class.",
                )
            if slot.identity_bearing and (
                not selected_character_ids
                or asset.visual_class is not self.profile.visual_class
                or selected_character_ids.isdisjoint(asset.character_ids)
            ):
                _fail(
                    "materialization_invalid",
                    "Materialized identity input is not bound to the primary character.",
                )
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "authorized_character_ids", tuple(sorted(authorized)))
        object.__setattr__(
            self,
            "slot_asset_ids",
            MappingProxyType(dict(sorted(bindings.items()))),
        )

    def to_safe_summary(self) -> dict[str, Any]:
        """Return no URIs, free-form values, provider payloads, or secrets."""

        return {
            "provider_id": self.profile.provider_id,
            "variant_id": self.profile.variant_id,
            "capability": self.intent.capability.value,
            "visual_class": self.intent.visual_class.value,
            "character_ids": [item.character_id for item in self.intent.characters],
            "asset_ids": sorted({item.asset_id for item in self.intent.assets}),
            "option_keys": sorted(self.options),
            "slot_asset_ids": dict(self.slot_asset_ids),
            "authorized_character_ids": list(self.authorized_character_ids),
        }

    def to_capability_binding(self) -> dict[str, str]:
        """Return the exact release identity safe to bind into a signed plan.

        This deliberately excludes option values, asset locators, and the
        grant's wider catalog.  A caller must re-materialize against the
        current server-side grant before execution.  Quality-qualified routes
        bind the accepted golden; technically-qualified routes bind the exact
        compatibility receipt and never relabel it as quality evidence.
        """

        binding = {
            "action_id": self.profile.action_id,
            "feature_id": self.profile.feature_id,
            "profile_key": self.profile.key,
            "profile_revision": self.profile.profile_revision,
        }
        if self.profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
            binding["technical_evidence_id"] = str(
                self.profile.technical_evidence_id
            )
        else:
            binding["golden_id"] = str(self.profile.golden_id)
        return binding


def profile_contract_errors(
    profile: VariantProfile,
    grant: CharacterCapabilityGrant,
) -> tuple[str, ...]:
    """Return stable reasons why a profile exceeds or differs from its grant."""

    if not isinstance(profile, VariantProfile) or not isinstance(grant, CharacterCapabilityGrant):
        raise TypeError("profile and grant must use provider-fabric contract types")
    reasons: list[str] = []
    if profile.key not in grant.profile_keys:
        reasons.append("profile_not_granted")
    if profile.action_id != grant.action_id:
        reasons.append("action_mismatch")
    if profile.feature_id != grant.feature_id:
        reasons.append("feature_mismatch")
    if profile.capability is not grant.capability:
        reasons.append("capability_mismatch")
    if profile.visual_class is not grant.character.visual_class:
        reasons.append("visual_class_mismatch")
    if not set(profile.option_values).issubset(grant.allowed_options):
        reasons.append("option_profile_expands_authority")
    else:
        for key, values in profile.option_values.items():
            allowed = grant.allowed_options[key]
            if any(not _scalar_allowed(value, allowed) for value in values):
                reasons.append("option_profile_expands_authority")
                break
    if profile.server_bound_slots != grant.server_bound_slots:
        reasons.append("server_binding_contract_mismatch")
    return tuple(dict.fromkeys(reasons))


AssetSelection: TypeAlias = MediaAsset | tuple[MediaAsset, ...]


def _asset_items(value: AssetSelection) -> tuple[MediaAsset, ...]:
    return (value,) if isinstance(value, MediaAsset) else value


def _asset_mapping(raw: Mapping[str, Any] | None, label: str) -> dict[str, AssetSelection]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or len(raw) > MAX_INPUT_SLOTS:
        _fail("input_contract_invalid", f"{label} is invalid or too large.")
    result: dict[str, AssetSelection] = {}
    for raw_slot, raw_assets in raw.items():
        slot = _option_key(raw_slot, "input slot")
        if isinstance(raw_assets, MediaAsset):
            result[slot] = raw_assets
            continue
        if (
            isinstance(raw_assets, (str, bytes, bytearray))
            or not isinstance(raw_assets, (list, tuple))
            or not 1 <= len(raw_assets) <= 16
            or any(not isinstance(item, MediaAsset) for item in raw_assets)
        ):
            _fail("input_contract_invalid", f"{label} must contain bounded MediaAsset selections.")
        result[slot] = tuple(raw_assets)
    return result


def normalize_submitted_options(
    raw: Mapping[str, Any] | None,
) -> Mapping[str, OptionScalar]:
    """Validate and freeze client options without selecting a profile."""

    return _submitted_options(raw)


def normalize_asset_selection(
    raw: Mapping[str, Any] | None,
    *,
    label: str = "asset selection",
) -> Mapping[str, AssetSelection]:
    """Validate and freeze slot-to-asset references without performing I/O."""

    return MappingProxyType(_asset_mapping(raw, label))


def _preferred_provider_ids(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, (list, tuple)):
        _fail(
            "route_intent_invalid",
            "Preferred providers must be an exact bounded sequence.",
        )
    values = tuple(_identifier(item, "preferred_provider") for item in raw)
    if len(values) > 16 or len(values) != len(set(values)):
        _fail(
            "route_intent_invalid",
            "Preferred providers are duplicate or too large.",
        )
    return values


def materialize_variant_request(
    profile: VariantProfile,
    grant: CharacterCapabilityGrant,
    *,
    submitted_options: Mapping[str, Any] | None = None,
    client_assets: Mapping[str, Any] | None = None,
    server_assets: Mapping[str, Any] | None = None,
    additional_characters: tuple[CharacterRef, ...] = (),
    additional_grants: tuple[CharacterCapabilityGrant, ...] = (),
    preferred_providers: tuple[str, ...] = (),
) -> MaterializedVariantRequest:
    """Compile one exact profile without I/O, persistence, quoting, or execution."""

    if not isinstance(profile, VariantProfile) or not isinstance(grant, CharacterCapabilityGrant):
        raise TypeError("profile and grant must use provider-fabric contract types")
    if grant.character.visual_class is VisualClass.UNKNOWN:
        _fail("unknown_visual_class", "Unknown visual class cannot enter a generation route.")
    errors = profile_contract_errors(profile, grant)
    if errors:
        _fail(errors[0], "Variant profile does not match its Castle capability grant.")
    if not grant.enabled:
        _fail("action_disabled", "The character action is not enabled.")
    if not grant.runtime_ready:
        _fail("action_runtime_unready", "The character action runtime is not ready.")
    if not profile.execution_ready:
        if profile.execution_basis == EXECUTION_BASIS_TECHNICAL_COMPATIBILITY:
            if not profile.runtime_ready:
                _fail(
                    "profile_runtime_unready",
                    "The provider profile runtime is not ready.",
                )
            _fail(
                "profile_technical_evidence_missing",
                "The provider profile has no current compatibility evidence.",
            )
        if not profile.release_ready:
            _fail("profile_release_unready", "The provider profile is not released.")
        if not profile.runtime_ready:
            _fail("profile_runtime_unready", "The provider profile runtime is not ready.")
        _fail(
            "profile_golden_stale",
            "The provider profile has no current accepted golden.",
        )

    options = _submitted_options(submitted_options)
    for key, value in options.items():
        if key not in grant.allowed_options or key not in profile.option_values:
            _fail("option_key_not_allowed", "An option does not belong to this exact profile.")
        if not _scalar_allowed(value, grant.allowed_options[key]):
            _fail("option_value_not_authorized", "An option value exceeds the character grant.")
        if not _scalar_allowed(value, profile.option_values[key]):
            _fail("option_value_not_allowed", "An option value does not belong to this profile.")
    if not profile.required_option_keys.issubset(options):
        _fail("required_option_missing", "A required profile option is missing.")

    client = _asset_mapping(client_assets, "client assets")
    server = _asset_mapping(server_assets, "server assets")
    if set(client).intersection(server):
        _fail("input_slot_ambiguous", "An input slot cannot be both client and server bound.")
    slots = {item.slot_id: item for item in profile.input_slots}
    supplied_slots = set(client).union(server)
    unknown_slots = supplied_slots - set(slots)
    if unknown_slots:
        _fail("input_slot_unknown", "An input asset does not belong to this exact profile.")
    for slot_id in client:
        if slots[slot_id].server_bound:
            _fail("server_bound_slot_forbidden", "A server-bound asset cannot be supplied by a client.")
    for slot_id in server:
        if not slots[slot_id].server_bound:
            _fail("client_slot_server_override", "A client input slot cannot be replaced by a server binding.")

    for item in profile.input_slots:
        selection = server.get(item.slot_id) if item.server_bound else client.get(item.slot_id)
        count = len(_asset_items(selection)) if selection is not None else 0
        if count < item.minimum_count:
            _fail("required_input_missing", "A required profile input is missing.")
        if count > item.maximum_count:
            _fail("input_cardinality_invalid", "An input slot exceeds its exact cardinality.")

    grants = (grant,) + tuple(additional_grants)
    if len(grants) > 16 or any(not isinstance(item, CharacterCapabilityGrant) for item in grants):
        _fail("authority_contract_invalid", "Additional character grants are invalid or too large.")
    for additional_grant in grants[1:]:
        grant_errors = profile_contract_errors(profile, additional_grant)
        if grant_errors:
            _fail(grant_errors[0], "An additional character grant does not match this profile.")
        if not additional_grant.enabled:
            _fail("action_disabled", "An additional character action is disabled.")
        if not additional_grant.runtime_ready:
            _fail("action_runtime_unready", "An additional character runtime is not ready.")
    granted_characters = tuple(item.character for item in grants)
    characters = granted_characters + tuple(additional_characters)
    if len(characters) > 16 or any(not isinstance(item, CharacterRef) for item in characters):
        _fail("character_contract_invalid", "Character references are invalid or too large.")
    character_ids = [item.character_id for item in characters]
    if len(character_ids) != len(set(character_ids)):
        _fail("character_contract_invalid", "Character references must be unique.")
    if any(item.visual_class is not grant.character.visual_class for item in characters):
        _fail("character_visual_class_mismatch", "A selected character belongs to another visual class.")
    authorized_character_ids = {item.character_id for item in granted_characters}

    assets: list[MediaAsset] = []
    slot_asset_ids: dict[str, str | tuple[str, ...]] = {}
    seen_asset_ids: set[str] = set()
    for slot_id, slot in slots.items():
        selection = server.get(slot_id) if slot.server_bound else client.get(slot_id)
        if selection is None:
            continue
        selection_assets = _asset_items(selection)
        for asset in selection_assets:
            if asset.kind is not slot.media_kind:
                _fail("input_media_kind_mismatch", "An input asset has the wrong media kind.")
            if asset.visual_class is not None and asset.visual_class is not profile.visual_class:
                _fail("asset_visual_class_mismatch", "An input asset belongs to another visual class.")
            if slot.identity_bearing:
                if asset.visual_class is not profile.visual_class:
                    _fail(
                        "asset_visual_class_required",
                        "An identity-bearing asset requires the exact visual class.",
                    )
                if authorized_character_ids.isdisjoint(asset.character_ids):
                    _fail("asset_character_mismatch", "An identity-bearing asset is not linked to an authorized character.")
            if asset.asset_id in seen_asset_ids:
                _fail("duplicate_input_asset", "One asset cannot satisfy multiple exact input slots.")
            seen_asset_ids.add(asset.asset_id)
            assets.append(asset)
        ids = tuple(asset.asset_id for asset in selection_assets)
        slot_asset_ids[slot_id] = ids[0] if slot.maximum_count == 1 else ids

    try:
        normalized_preferences = _preferred_provider_ids(preferred_providers)
        intent = RouteIntent(
            capability=grant.capability,
            visual_class=grant.character.visual_class,
            characters=characters,
            assets=tuple(assets),
            required_options=frozenset(options),
            preferred_providers=normalized_preferences,
        )
    except (TypeError, ValueError) as error:
        raise VariantProfileError(
            "route_intent_invalid", "The exact route intent could not be materialized."
        ) from error
    return MaterializedVariantRequest(
        profile,
        intent,
        options,
        slot_asset_ids,
        tuple(sorted(authorized_character_ids)),
    )


__all__ = [
    "CharacterCapabilityGrant",
    "EXECUTION_BASIS_QUALITY_GOLDEN",
    "EXECUTION_BASIS_TECHNICAL_COMPATIBILITY",
    "InputSlot",
    "MaterializedVariantRequest",
    "OptionScalar",
    "PROFILE_SCHEMA_VERSION",
    "VariantProfile",
    "VariantProfileError",
    "materialize_variant_request",
    "normalize_asset_selection",
    "normalize_submitted_options",
    "profile_contract_errors",
]
