"""Bind canonical shot prompts to an exact provider variant signature."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from provider_fabric.models import CapabilityKind
from provider_fabric.variant_profiles import VariantProfile

from .prompt_contract import ShotPromptContract


PROVIDER_PROMPT_VERSION = "castle.provider-prompt-envelope.v1"

# These are Castle adapter fields, not remote API parameter names.  A transport
# adapter must explicitly map this bounded signature to its pinned API/workflow.
_FIELDS_BY_CAPABILITY: Mapping[CapabilityKind, tuple[str, ...]] = MappingProxyType({
    CapabilityKind.TEXT_TO_IMAGE: (
        "subject", "action", "scene", "light", "camera", "style", "quality", "constraints",
    ),
    CapabilityKind.IMAGE_TO_IMAGE: (
        "subject", "action", "scene", "light", "camera", "style", "quality", "constraints",
    ),
    CapabilityKind.TEXT_TO_VIDEO: (
        "subject", "action", "scene", "light", "camera", "style", "quality", "constraints",
        "duration", "aspect", "audio", "language",
    ),
    CapabilityKind.IMAGE_TO_VIDEO: (
        "subject", "action", "scene", "light", "camera", "style", "quality", "constraints",
        "duration", "aspect", "audio", "language",
    ),
    CapabilityKind.LIP_SYNC: (
        "subject", "action", "scene", "camera", "style", "constraints",
        "duration", "aspect", "audio", "language",
    ),
    CapabilityKind.DANCE: (
        "subject", "action", "scene", "camera", "style", "quality", "constraints",
        "duration", "aspect",
    ),
    CapabilityKind.MULTI_SUBJECT_VIDEO: (
        "subject", "action", "scene", "light", "camera", "style", "quality", "constraints",
        "duration", "aspect", "audio", "language",
    ),
})


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("provider prompt must contain JSON-compatible values")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ProviderPromptEnvelope:
    provider_id: str
    route_variant_id: str
    variant_id: str
    capability: str
    visual_class: str
    prompt_contract_hash: str
    fields: Mapping[str, Any]
    envelope_hash: str
    schema_version: str = PROVIDER_PROMPT_VERSION

    def __post_init__(self) -> None:
        fields = _freeze(self.fields)
        material = {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "route_variant_id": self.route_variant_id,
            "variant_id": self.variant_id,
            "capability": self.capability,
            "visual_class": self.visual_class,
            "prompt_contract_hash": self.prompt_contract_hash,
            "fields": _thaw(fields),
        }
        if self.envelope_hash != _stable_hash(material):
            raise ValueError("provider prompt hash does not match its contents")
        object.__setattr__(self, "fields", fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "route_variant_id": self.route_variant_id,
            "variant_id": self.variant_id,
            "capability": self.capability,
            "visual_class": self.visual_class,
            "prompt_contract_hash": self.prompt_contract_hash,
            "fields": _thaw(self.fields),
            "envelope_hash": self.envelope_hash,
        }


def bind_provider_prompt(
    contract: ShotPromptContract,
    profile: VariantProfile,
    *,
    route_variant_id: str,
) -> ProviderPromptEnvelope:
    """Produce an inert, bounded envelope for one exact profile."""

    if not isinstance(contract, ShotPromptContract) or not isinstance(profile, VariantProfile):
        raise TypeError("contract and profile must use Castle contract types")
    if route_variant_id not in contract.eligible_variant_ids:
        raise ValueError("provider route variant is not eligible for this shot")
    if contract.request_slots["medium"] != profile.capability.value:
        raise ValueError("provider capability does not match the shot medium")
    requested_class = contract.request_slots["style"]["visual_class"]
    if requested_class != profile.visual_class.value:
        raise ValueError("provider visual class does not match the shot")
    expected_options = frozenset(contract.prompt_dimensions["constraints"]["required_options"])
    if not expected_options.issubset(profile.option_values):
        raise ValueError("provider profile does not declare every required option")
    signature = _FIELDS_BY_CAPABILITY.get(profile.capability)
    if signature is None:
        raise ValueError("provider capability has no prompt signature")

    dimensions = contract.prompt_dimensions
    slots = contract.request_slots
    source = {
        **dimensions,
        "duration": slots["duration"],
        "aspect": slots["aspect"],
        "audio": slots["audio"],
        "language": slots["language"],
    }
    fields = {name: source[name] for name in signature}
    material = {
        "schema_version": PROVIDER_PROMPT_VERSION,
        "provider_id": profile.provider_id,
        "route_variant_id": route_variant_id,
        "variant_id": profile.variant_id,
        "capability": profile.capability.value,
        "visual_class": profile.visual_class.value,
        "prompt_contract_hash": contract.contract_hash,
        "fields": _thaw(fields),
    }
    return ProviderPromptEnvelope(
        provider_id=profile.provider_id,
        route_variant_id=route_variant_id,
        variant_id=profile.variant_id,
        capability=profile.capability.value,
        visual_class=profile.visual_class.value,
        prompt_contract_hash=contract.contract_hash,
        fields=fields,
        envelope_hash=_stable_hash(material),
    )


__all__ = ["PROVIDER_PROMPT_VERSION", "ProviderPromptEnvelope", "bind_provider_prompt"]
