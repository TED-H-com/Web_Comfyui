"""Provider-neutral prompt contracts for production-pack shots.

The UI describes intent once.  Provider adapters may later translate this
contract into their own syntax, but may not invent a character class, asset,
voice, or capability that was not locked by the production pack.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ProductionPack, ShotSpec


PROMPT_CONTRACT_VERSION = "castle.shot-prompt.v1"
REQUEST_SLOT_NAMES = (
    "medium", "duration", "aspect", "subject", "style", "character",
    "scene", "audio", "language",
)
PROMPT_DIMENSION_NAMES = (
    "subject", "action", "scene", "light", "camera", "style", "quality",
    "constraints",
)


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
    raise ValueError("prompt contract must contain JSON-compatible values")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ShotPromptContract:
    shot_id: str
    request_slots: Mapping[str, Any]
    prompt_dimensions: Mapping[str, Any]
    immutable_resource_ids: tuple[str, ...]
    eligible_variant_ids: tuple[str, ...]
    contract_hash: str
    schema_version: str = PROMPT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if tuple(self.request_slots) != REQUEST_SLOT_NAMES:
            raise ValueError("request slots must use the canonical order")
        if tuple(self.prompt_dimensions) != PROMPT_DIMENSION_NAMES:
            raise ValueError("prompt dimensions must use the canonical order")
        resource_ids = tuple(self.immutable_resource_ids)
        variants = tuple(self.eligible_variant_ids)
        if not variants or len(variants) != len(set(variants)):
            raise ValueError("eligible variants must be non-empty and unique")
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("immutable resources must be unique")
        slots = _freeze(self.request_slots)
        dimensions = _freeze(self.prompt_dimensions)
        material = {
            "schema_version": self.schema_version,
            "shot_id": self.shot_id,
            "request_slots": _thaw(slots),
            "prompt_dimensions": _thaw(dimensions),
            "immutable_resource_ids": list(resource_ids),
            "eligible_variant_ids": list(variants),
        }
        if self.contract_hash != _stable_hash(material):
            raise ValueError("prompt contract hash does not match its contents")
        object.__setattr__(self, "request_slots", slots)
        object.__setattr__(self, "prompt_dimensions", dimensions)
        object.__setattr__(self, "immutable_resource_ids", resource_ids)
        object.__setattr__(self, "eligible_variant_ids", variants)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "shot_id": self.shot_id,
            "request_slots": _thaw(self.request_slots),
            "prompt_dimensions": _thaw(self.prompt_dimensions),
            "immutable_resource_ids": list(self.immutable_resource_ids),
            "eligible_variant_ids": list(self.eligible_variant_ids),
            "contract_hash": self.contract_hash,
        }


def compile_shot_prompt(pack: ProductionPack, shot: ShotSpec) -> ShotPromptContract:
    """Project one accepted shot into a deterministic adapter input.

    Deliberately absent: provider names, credentials, prices, workflow node
    names, and free-form fallback models.  Those belong to exact route binding.
    """

    if not isinstance(pack, ProductionPack) or not isinstance(shot, ShotSpec):
        raise TypeError("pack and shot must be parsed production-pack objects")
    if shot not in pack.shots:
        raise ValueError("shot does not belong to this production pack")

    entities = {item.entity_id: item for item in pack.entities}
    selected = [entities[entity_id] for entity_id in shot.entity_ids]
    visual_classes = sorted({item.visual_class.value for item in selected})
    if len(visual_classes) != 1:
        raise ValueError("one shot cannot mix visual classes")

    character_bindings = [
        {
            "entity_id": item.entity_id,
            "character_id": item.character_id,
            "display_name": item.display_name,
            "profile_asset_id": item.profile_asset_id,
            "wardrobe_asset_id": item.wardrobe_asset_id,
            "visual_class": item.visual_class.value,
        }
        for item in selected
    ]
    resource_ids = tuple(dict.fromkeys(
        (*shot.input_asset_ids, *(
            resource_id
            for item in selected
            for resource_id in (item.profile_asset_id, item.wardrobe_asset_id, item.voice_asset_id)
            if resource_id
        ))
    ))
    request_slots = {
        "medium": shot.required_capability.value,
        "duration": shot.duration_seconds,
        "aspect": pack.project.aspect_ratio,
        "subject": [item.display_name for item in selected],
        "style": {"visual_class": visual_classes[0], "quality_tier": pack.project.quality_tier},
        "character": character_bindings,
        "scene": {"scene_id": shot.scene_id, "frame_intent": shot.frame_intent},
        "audio": {
            "intent": shot.audio_intent,
            "dialogue_mode": shot.dialogue_mode.value,
            "dialogue": [cue.to_dict() for cue in shot.dialogue],
        },
        "language": pack.project.language,
    }
    prompt_dimensions = {
        "subject": character_bindings,
        "action": shot.observable_action,
        "scene": shot.frame_intent,
        "light": "preserve_locked_scene_lighting",
        "camera": shot.camera,
        "style": {"visual_class": visual_classes[0], "quality_tier": pack.project.quality_tier},
        "quality": {"tier": pack.project.quality_tier, "target_aspect": pack.project.aspect_ratio},
        "constraints": {
            "negative": shot.negative,
            "continuity": dict(shot.continuity_values),
            "required_options": sorted(shot.required_options),
            "no_cross_class_fallback": True,
            "resource_ids_are_immutable": True,
        },
    }
    material = {
        "schema_version": PROMPT_CONTRACT_VERSION,
        "shot_id": shot.shot_id,
        "request_slots": request_slots,
        "prompt_dimensions": prompt_dimensions,
        "immutable_resource_ids": resource_ids,
        "eligible_variant_ids": shot.eligible_visual_variant_ids,
    }
    return ShotPromptContract(
        shot_id=shot.shot_id,
        request_slots=request_slots,
        prompt_dimensions=prompt_dimensions,
        immutable_resource_ids=resource_ids,
        eligible_variant_ids=shot.eligible_visual_variant_ids,
        contract_hash=_stable_hash(material),
    )
