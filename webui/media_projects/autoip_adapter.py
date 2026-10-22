"""Translate an exact Castle prompt envelope into the pinned AutoIP contract."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from provider_fabric.autoip_contract import (
    CONTRACT_ID,
    REQUEST_SCHEMA_VERSION,
    AutoIPContractOperation,
    NormalizedAutoIPRequest,
    normalize_generation_request,
)

from .provider_prompt import ProviderPromptEnvelope


_REQUIRED_OPTION_KEYS = {
    AutoIPContractOperation.TEXT_TO_IMAGE: frozenset({"mode", "size"}),
    AutoIPContractOperation.IMAGE_TO_IMAGE: frozenset({"strength", "size", "mode"}),
    AutoIPContractOperation.IMAGE_TO_VIDEO: frozenset({"duration_seconds", "resolution", "aspect_ratio"}),
    AutoIPContractOperation.TEXT_TO_VIDEO: frozenset({"duration_seconds", "resolution", "aspect_ratio"}),
    AutoIPContractOperation.LIP_SYNC: frozenset({"aspect_ratio"}),
    AutoIPContractOperation.DANCE: frozenset({"motion_source"}),
    AutoIPContractOperation.MULTI_SUBJECT_VIDEO: frozenset({"duration_seconds", "resolution", "aspect_ratio"}),
}
_OPTIONAL_OPTION_KEYS = {
    AutoIPContractOperation.TEXT_TO_IMAGE: frozenset(),
    AutoIPContractOperation.IMAGE_TO_IMAGE: frozenset(),
    AutoIPContractOperation.IMAGE_TO_VIDEO: frozenset({"seed"}),
    AutoIPContractOperation.TEXT_TO_VIDEO: frozenset({"seed"}),
    AutoIPContractOperation.LIP_SYNC: frozenset({"audio_start_time", "audio_duration"}),
    AutoIPContractOperation.DANCE: frozenset(),
    AutoIPContractOperation.MULTI_SUBJECT_VIDEO: frozenset({"seed", "native_audio"}),
}
_RANDOM_SEED = "random"


def _prompt(envelope: ProviderPromptEnvelope, maximum: int) -> str:
    fields = envelope.to_dict()["fields"]
    parts = []
    for name in ("subject", "action", "scene", "light", "camera", "style", "quality"):
        if name in fields:
            value = fields[name]
            rendered = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            parts.append(f"{name}: {rendered}")
    constraints = fields.get("constraints", {})
    negative = constraints.get("negative", "") if isinstance(constraints, Mapping) else ""
    if negative:
        parts.append(f"constraints: {negative}")
    result = ". ".join(parts)
    if len(result) > maximum:
        raise ValueError("canonical prompt exceeds the pinned AutoIP operation limit")
    return result


def _provider_inputs(
    operation: AutoIPContractOperation,
    inputs: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Translate bounded Castle slot names to the pinned AutoIP wire contract."""

    result: list[Mapping[str, Any]] = []
    for item in inputs:
        if (
            operation is AutoIPContractOperation.MULTI_SUBJECT_VIDEO
            and isinstance(item, Mapping)
            and item.get("field_name") == "subjects"
        ):
            result.append({**item, "field_name": "images[]"})
        else:
            result.append(item)
    return result


def _subject_descriptions(
    envelope_fields: Mapping[str, Any],
    image_count: int,
) -> list[str]:
    """Return index-aligned, gender-neutral subject labels for AutoIP."""

    subjects = envelope_fields.get("subject")
    if (
        isinstance(subjects, (str, bytes, bytearray))
        or not isinstance(subjects, (list, tuple))
        or len(subjects) != image_count
    ):
        return []
    descriptions = []
    for item in subjects:
        if isinstance(item, Mapping):
            value = item.get("display_name") or item.get("character_id")
        else:
            value = item
        if not isinstance(value, str) or not value.strip():
            return []
        normalized = value.strip()
        if len(normalized) > 200:
            raise ValueError("AutoIP subject description exceeds the pinned limit")
        descriptions.append(normalized)
    return descriptions


def _dialogue_text(envelope_fields: Mapping[str, Any]) -> str:
    audio = envelope_fields.get("audio")
    if not isinstance(audio, Mapping):
        return ""
    cues = audio.get("dialogue")
    if (
        isinstance(cues, (str, bytes, bytearray))
        or not isinstance(cues, (list, tuple))
    ):
        return ""
    parts = []
    for cue in cues:
        text = cue.get("text") if isinstance(cue, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            return ""
        parts.append(text.strip())
    result = "\n".join(parts)
    if len(result) > 1000:
        raise ValueError("AutoIP dialogue exceeds the pinned limit")
    return result


def build_autoip_request(
    envelope: ProviderPromptEnvelope,
    *,
    selected_options: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
) -> NormalizedAutoIPRequest:
    """Build and re-validate one inert request; performs no network or spend."""

    if not isinstance(envelope, ProviderPromptEnvelope):
        raise TypeError("envelope must be a ProviderPromptEnvelope")
    if envelope.provider_id != "autoip":
        raise ValueError("provider prompt is not bound to AutoIP")
    try:
        operation = AutoIPContractOperation(envelope.capability)
    except ValueError as error:
        raise ValueError("provider capability has no AutoIP operation") from error
    if not isinstance(selected_options, Mapping) or any(
        not isinstance(key, str) for key in selected_options
    ):
        raise TypeError("selected_options must be a string-keyed mapping")
    required = _REQUIRED_OPTION_KEYS[operation]
    allowed = required | _OPTIONAL_OPTION_KEYS[operation]
    supplied = set(selected_options)
    if not required.issubset(supplied) or not supplied.issubset(allowed):
        raise ValueError("AutoIP options do not match the operation signature")

    prompt_max = 500 if operation is AutoIPContractOperation.DANCE else (
        2000 if operation in {AutoIPContractOperation.TEXT_TO_IMAGE, AutoIPContractOperation.IMAGE_TO_IMAGE}
        else 4000
    )
    fields: dict[str, Any] = {}
    if operation not in {AutoIPContractOperation.LIP_SYNC, AutoIPContractOperation.DANCE}:
        fields["prompt"] = _prompt(envelope, prompt_max)
    elif _prompt(envelope, prompt_max):
        fields["prompt"] = _prompt(envelope, prompt_max)

    fields.update(selected_options)
    if fields.get("seed") == _RANDOM_SEED:
        # Omitting seed is the provider's documented random mode.  Never send
        # Castle's sentinel as a wire value.
        fields.pop("seed")
    fields.pop("native_audio", None)
    fields.pop("motion_source", None)
    constraints = envelope.to_dict()["fields"].get("constraints", {})
    negative = constraints.get("negative", "") if isinstance(constraints, Mapping) else ""
    if negative and operation in {
        AutoIPContractOperation.TEXT_TO_IMAGE,
        AutoIPContractOperation.MULTI_SUBJECT_VIDEO,
    }:
        fields["negative_prompt"] = negative

    provider_inputs = _provider_inputs(operation, inputs)
    if operation is AutoIPContractOperation.LIP_SYNC:
        fields.setdefault("audio_start_time", 0)
        authored_duration = envelope.to_dict()["fields"].get("duration", 5)
        if (
            isinstance(authored_duration, bool)
            or not isinstance(authored_duration, (int, float))
        ):
            raise ValueError("AutoIP lip-sync duration is invalid")
        fields.setdefault("audio_duration", min(31, max(1, authored_duration)))
    elif operation is AutoIPContractOperation.DANCE:
        if selected_options.get("motion_source") != "dance_video" or sum(
            1 for item in provider_inputs if item.get("field_name") == "dance_video"
        ) != 1:
            raise ValueError("AutoIP dance requires one uploaded dance video")
    elif operation is AutoIPContractOperation.MULTI_SUBJECT_VIDEO:
        image_count = sum(
            1 for item in provider_inputs if item.get("field_name") == "images[]"
        )
        descriptions = _subject_descriptions(envelope.to_dict()["fields"], image_count)
        if descriptions:
            fields["descriptions[]"] = descriptions
        dialogue = _dialogue_text(envelope.to_dict()["fields"])
        has_voice_sample = any(
            item.get("field_name") == "voice_sample" for item in provider_inputs
        )
        if has_voice_sample and not dialogue:
            raise ValueError("AutoIP voice sample requires authored dialogue")
        if dialogue:
            fields["dialogue_text"] = dialogue

    # Project timing/aspect are immutable: an option picker cannot silently
    # change the authored shot contract while selecting a provider.
    envelope_fields = envelope.to_dict()["fields"]
    if "duration_seconds" in selected_options:
        authored = envelope_fields.get("duration")
        if isinstance(authored, bool) or int(authored) != authored or selected_options["duration_seconds"] != int(authored):
            raise ValueError("AutoIP duration differs from the authored shot")
    if "aspect_ratio" in selected_options and selected_options["aspect_ratio"] != envelope_fields.get("aspect"):
        raise ValueError("AutoIP aspect ratio differs from the authored shot")

    return normalize_generation_request({
        "contract": CONTRACT_ID,
        "schema_version": REQUEST_SCHEMA_VERSION,
        "operation": operation.value,
        "visual_class": envelope.visual_class,
        "fields": fields,
        "inputs": provider_inputs,
    })


__all__ = ["build_autoip_request"]
