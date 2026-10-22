"""Compile an exact Castle production prompt into a native MiniMax H3 graph."""

from __future__ import annotations

import json
from typing import Any, Mapping

from minimax_h3_product import compile_product_prompt

from .provider_prompt import ProviderPromptEnvelope


_ASPECT_FROM_RATIO = {"9:16": "portrait", "16:9": "landscape", "1:1": "square"}


def _description(envelope: ProviderPromptEnvelope) -> str:
    fields = envelope.to_dict()["fields"]
    parts = []
    for key in ("action", "scene", "camera", "audio"):
        if key not in fields:
            continue
        value = fields[key]
        rendered = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        parts.append(f"{key}: {rendered}")
    result = ". ".join(parts)
    if not result:
        raise ValueError("H3 production description is empty")
    return result


def build_h3_graph(
    envelope: ProviderPromptEnvelope,
    *,
    selected_options: Mapping[str, Any],
    server_uploads: Mapping[str, str],
    seed: int,
    output_token: str,
) -> dict[str, dict[str, object]]:
    """Build a side-effect-free Comfy graph for one exact H3 variant."""

    if not isinstance(envelope, ProviderPromptEnvelope):
        raise TypeError("envelope must be a ProviderPromptEnvelope")
    if envelope.provider_id != "local_comfy" or envelope.capability != "image_to_video":
        raise ValueError("provider prompt is not bound to native H3 video")
    if set(selected_options) != {"aspect", "motion_profile", "quality"}:
        raise ValueError("H3 options must exactly match the character variant")
    expected_aspect = _ASPECT_FROM_RATIO.get(envelope.to_dict()["fields"].get("aspect"))
    if expected_aspect is None or selected_options["aspect"] != expected_aspect:
        raise ValueError("H3 aspect differs from the authored shot")

    if envelope.variant_id.startswith("image-video."):
        feature_id = "h3-image-video"
        expected_upload = "image"
    elif envelope.variant_id.startswith("reference-video."):
        feature_id = "h3-reference-video"
        expected_upload = "reference"
    else:
        raise ValueError("provider variant is not a Castle H3 character variant")
    if set(server_uploads) != {expected_upload}:
        raise ValueError("H3 server uploads do not match the exact variant")

    return compile_product_prompt(
        feature_id=feature_id,
        visual_class=envelope.visual_class,
        settings={
            "description": _description(envelope),
            "motionProfile": selected_options["motion_profile"],
            "aspect": selected_options["aspect"],
            "quality": selected_options["quality"],
            "seed": seed,
        },
        uploads=dict(server_uploads),
        output_token=output_token,
    )


__all__ = ["build_h3_graph"]
