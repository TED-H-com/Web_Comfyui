#!/usr/bin/env python3
"""Resolve, translate, and qualify the pinned Servasyy article workflows.

The browser workflow is the only graph source.  In particular, connected
controller outputs override stale widget snapshots on the target node.  This
runner asserts the reviewed browser links before producing an API prompt, so a
qualification cannot silently drift into a hand-written approximation.

The resolver is intentionally kept separate from the immutable RunningHub
snapshots and reads the generated ``adapted-current`` T2V, I2V/FLF, or R2V
workflow selected on the command line.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
# Direct execution sets ``sys.path[0]`` to ``scripts/minimax_h3`` rather than
# the repository root.  Add only this resolved local root so the audited
# runner helpers are importable in both direct and unittest execution modes.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ADAPTED_T2V = (
    REPO_ROOT
    / "workflows/minimax_h3/castle/servasyy_article"
    / "h3-t2v-servasyy-article-adapted-current.json"
)
ADAPTED_I2V = (
    REPO_ROOT
    / "workflows/minimax_h3/castle/servasyy_article"
    / "h3-i2v-flf-servasyy-article-adapted-current.json"
)
ADAPTED_R2V = (
    REPO_ROOT
    / "workflows/minimax_h3/castle/servasyy_article"
    / "h3-r2v-servasyy-article-adapted-current.json"
)
EXACT_T2V = (
    REPO_ROOT
    / "workflows/minimax_h3/community/servasyy_article"
    / "h3-t2v-runninghub-2084626786919878657.json"
)
EXACT_I2V = (
    REPO_ROOT
    / "workflows/minimax_h3/community/servasyy_article"
    / "h3-i2v-flf-runninghub-2084620086811410433.json"
)
EXACT_R2V = (
    REPO_ROOT
    / "workflows/minimax_h3/community/servasyy_article"
    / "h3-r2v-runninghub-2084127823018946561.json"
)
EXACT_T2V_SHA256 = (
    "8b190846243edfc58ab45b82dc73a73d9163e98cf200168a602555b847059d82"
)
EXACT_I2V_SHA256 = (
    "a49039b0ebaaa4f19cd6509b7b5ba129ac220b4dafe35df8b9c7499a225c7222"
)
EXACT_R2V_SHA256 = (
    "8ac489422009fab42c720c402c3f4c859aa8a8ec5b28556f2162fd49eae8ef3f"
)
ADAPTED_T2V_SHA256 = (
    "56bd9eb90bdbc8f6176471599b0fb740c9dd0b665912e4bc6fe46f40445c6b08"
)
ADAPTED_I2V_SHA256 = (
    "39b8ca3ab3d5fed8e98c4bb4d6f09154c6e397cd8bd669c29f9b71c800029029"
)
ADAPTED_R2V_SHA256 = (
    "b350c4b096db4d016bc3ff907f5a7bbcbae2f7149f80e3481e8a7a0a8591afd1"
)
EXACT_T2V_WORKFLOW_ID = "2084626786919878657"
EXACT_I2V_WORKFLOW_ID = "2084620086811410433"
EXACT_R2V_WORKFLOW_ID = "2084127823018946561"
LIGHTX_V01_BYTES = 1_956_171_984
LIGHTX_V01_SHA256 = (
    "fc9b6500f0331fe925b004738baaa31bd34104741c8bf9334816f9ac3005c8c1"
)
FRAME_EXPRESSION = (
    "max(5, round(a * 24)) + "
    "(5 - (max(5, round(a * 24)) % 17)) % 17"
)
FL2VA_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
REF2VA_MODEL = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
EXPECTED_CRITICAL_LINKS = {
    228: [228, 120, 0, 121, 1, "VAE"],
    229: [229, 119, 0, 122, 1, "VAE"],
    231: [231, 129, 0, 125, 0, "NOISE"],
    232: [232, 126, 0, 125, 1, "GUIDER"],
    233: [233, 123, 0, 125, 2, "SAMPLER"],
    234: [234, 124, 0, 125, 3, "SIGMAS"],
    235: [235, 131, 1, 125, 4, "LATENT"],
    237: [237, 131, 0, 126, 1, "CONDITIONING"],
    238: [238, 122, 0, 130, 0, "IMAGE"],
    239: [239, 121, 0, 130, 1, "AUDIO"],
    240: [240, 128, 0, 131, 0, "CLIP"],
    241: [241, 119, 0, 131, 1, "VAE"],
    242: [242, 130, 0, 92, 0, "VIDEO"],
    243: [243, 132, 1, 131, 7, "INT"],
    244: [244, 115, 0, 131, 5, "INT"],
    245: [245, 115, 1, 131, 6, "INT"],
    246: [246, 133, 0, 132, 0, "FLOAT"],
    247: [247, 125, 0, 122, 0, "LATENT"],
    248: [248, 125, 0, 121, 0, "LATENT"],
    249: [249, 135, 0, 134, 0, "MODEL"],
    250: [250, 134, 0, 124, 0, "MODEL"],
    251: [251, 134, 0, 126, 0, "MODEL"],
    252: [252, 127, 0, 135, 0, "MODEL"],
}
EXPECTED_I2V_LINKS = {
    228: [228, 119, 0, 120, 0, "IMAGE"],
    229: [229, 122, 0, 123, 1, "VAE"],
    230: [230, 121, 0, 124, 1, "VAE"],
    232: [232, 131, 0, 127, 0, "NOISE"],
    233: [233, 128, 0, 127, 1, "GUIDER"],
    236: [236, 133, 1, 127, 4, "LATENT"],
    238: [238, 133, 0, 128, 1, "CONDITIONING"],
    239: [239, 124, 0, 132, 0, "IMAGE"],
    240: [240, 123, 0, 132, 1, "AUDIO"],
    241: [241, 130, 0, 133, 0, "CLIP"],
    242: [242, 121, 0, 133, 1, "VAE"],
    243: [243, 132, 0, 92, 0, "VIDEO"],
    244: [244, 114, 0, 133, 2, "IMAGE"],
    245: [245, 134, 1, 133, 7, "INT"],
    246: [246, 115, 0, 133, 5, "INT"],
    247: [247, 115, 1, 133, 6, "INT"],
    248: [248, 135, 0, 134, 0, "FLOAT"],
    249: [249, 127, 0, 124, 0, "LATENT"],
    250: [250, 127, 0, 123, 0, "LATENT"],
    256: [256, 140, 0, 137, 0, "MODEL"],
    257: [257, 129, 0, 140, 0, "MODEL"],
    263: [263, 147, 0, 128, 0, "MODEL"],
    264: [264, 125, 0, 127, 2, "SAMPLER"],
    265: [265, 126, 0, 127, 3, "SIGMAS"],
    273: [273, 137, 0, 141, 0, "MODEL"],
    276: [276, 141, 0, 144, 0, "MODEL"],
    280: [280, 147, 0, 126, 0, "MODEL"],
    282: [282, 144, 0, 147, 0, "MODEL"],
}
EXPECTED_R2V_LINKS = {
    250: [250, 120, 0, 121, 1, "VAE"],
    251: [251, 119, 0, 122, 1, "VAE"],
    253: [253, 129, 0, 125, 0, "NOISE"],
    254: [254, 126, 0, 125, 1, "GUIDER"],
    255: [255, 123, 0, 125, 2, "SAMPLER"],
    256: [256, 124, 0, 125, 3, "SIGMAS"],
    258: [258, 122, 0, 130, 0, "IMAGE"],
    259: [259, 121, 0, 130, 1, "AUDIO"],
    260: [260, 130, 0, 92, 0, "VIDEO"],
    261: [261, 132, 0, 131, 0, "FLOAT"],
    270: [270, 136, 0, 126, 1, "CONDITIONING"],
    271: [271, 136, 1, 125, 4, "LATENT"],
    272: [272, 128, 0, 136, 0, "CLIP"],
    273: [273, 119, 0, 136, 1, "VAE"],
    274: [274, 120, 0, 136, 2, "VAE"],
    275: [275, 131, 1, 136, 15, "INT"],
    276: [276, 115, 0, 136, 13, "INT"],
    277: [277, 115, 1, 136, 14, "INT"],
    278: [278, 137, 0, 136, 3, "IMAGE"],
    279: [279, 138, 0, 136, 12, "STRING"],
    280: [280, 125, 0, 122, 0, "LATENT"],
    281: [281, 125, 0, 121, 0, "LATENT"],
    282: [282, 139, 0, 136, 4, "IMAGE"],
    283: [283, 142, 0, 136, 5, "IMAGE"],
    284: [284, 143, 0, 136, 9, "AUDIO"],
    285: [285, 144, 0, 136, 10, "AUDIO"],
    287: [287, 147, 0, 146, 0, "MODEL"],
    290: [290, 127, 0, 147, 0, "MODEL"],
    291: [291, 148, 0, 124, 0, "MODEL"],
    292: [292, 148, 0, 126, 0, "MODEL"],
    293: [293, 146, 0, 148, 0, "MODEL"],
}
NODE_TYPES = {
    92: "SaveVideo",
    115: "ResolutionSelector",
    119: "VAELoader",
    120: "VAELoader",
    121: "VAEDecodeAudio",
    122: "VAEDecode",
    123: "KSamplerSelect",
    124: "BasicScheduler",
    125: "SamplerCustomAdvanced",
    126: "BasicGuider",
    127: "UNETLoader",
    128: "CLIPLoader",
    129: "RandomNoise",
    130: "CreateVideo",
    131: "MiniMaxH3ImageToVideo",
    132: "ComfyMathExpression",
    133: "PrimitiveFloat",
    134: "SpectrumSDXL",
    135: "PathchSageAttentionKJ",
}
I2V_NODE_TYPES = {
    92: "SaveVideo",
    114: "LoadImage",
    115: "ResolutionSelector",
    121: "VAELoader",
    122: "VAELoader",
    123: "VAEDecodeAudio",
    124: "VAEDecode",
    125: "KSamplerSelect",
    126: "BasicScheduler",
    127: "SamplerCustomAdvanced",
    128: "BasicGuider",
    129: "UNETLoader",
    130: "CLIPLoader",
    131: "RandomNoise",
    132: "CreateVideo",
    133: "MiniMaxH3ImageToVideo",
    134: "ComfyMathExpression",
    135: "PrimitiveFloat",
    137: "PathchSageAttentionKJ",
    138: "EasyCache",
    140: "LoraLoaderModelOnly",
    141: "MiniMaxH3MemoryEfficientSageAttentionPatch",
    144: "SolAttnPatch",
    147: "SpectrumSDXL",
}
R2V_NODE_TYPES = {
    92: "SaveVideo",
    115: "ResolutionSelector",
    119: "VAELoader",
    120: "VAELoader",
    121: "VAEDecodeAudio",
    122: "VAEDecode",
    123: "KSamplerSelect",
    124: "BasicScheduler",
    125: "SamplerCustomAdvanced",
    126: "BasicGuider",
    127: "UNETLoader",
    128: "CLIPLoader",
    129: "RandomNoise",
    130: "CreateVideo",
    131: "ComfyMathExpression",
    132: "PrimitiveFloat",
    136: "MiniMaxH3ReferenceToVideo",
    137: "LoadImage",
    138: "PrimitiveStringMultiline",
    139: "LoadImage",
    142: "LoadImage",
    143: "LoadAudio",
    144: "LoadAudio",
    146: "ModelPatchTorchSettings",
    147: "PathchSageAttentionKJ",
    148: "SpectrumSDXLBatch",
}
ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}


class QualificationError(RuntimeError):
    """A fail-closed article workflow qualification error."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise QualificationError(f"workflow_not_object:{path}")
    return parsed, payload


def _node_map(workflow: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    raw_nodes = workflow.get("nodes")
    if not isinstance(raw_nodes, list):
        raise QualificationError("workflow_nodes_invalid")
    nodes: dict[int, dict[str, Any]] = {}
    for node in raw_nodes:
        if not isinstance(node, dict) or isinstance(node.get("id"), bool):
            raise QualificationError("workflow_node_invalid")
        node_id = int(node["id"])
        if node_id in nodes:
            raise QualificationError(f"duplicate_node:{node_id}")
        nodes[node_id] = node
    return nodes


def _assert_nodes(
    nodes: Mapping[int, Mapping[str, Any]],
    expected_types: Mapping[int, str],
) -> None:
    for node_id, node_type in expected_types.items():
        node = nodes.get(node_id)
        if node is None or node.get("type") != node_type:
            raise QualificationError(f"node_mismatch:{node_id}:{node_type}")


def _assert_links(
    workflow: Mapping[str, Any], expected_links: Mapping[int, list[Any]]
) -> None:
    raw_links = workflow.get("links")
    if not isinstance(raw_links, list):
        raise QualificationError("workflow_links_invalid")
    links: dict[int, list[Any]] = {}
    for link in raw_links:
        if not isinstance(link, list) or len(link) != 6:
            raise QualificationError("workflow_link_invalid")
        link_id = int(link[0])
        if link_id in links:
            raise QualificationError(f"duplicate_link:{link_id}")
        links[link_id] = link
    for link_id, expected in expected_links.items():
        if links.get(link_id) != expected:
            raise QualificationError(
                f"critical_link_mismatch:{link_id}:"
                f"expected={expected!r}:actual={links.get(link_id)!r}"
            )


def _widgets(
    nodes: Mapping[int, Mapping[str, Any]], node_id: int, count: int
) -> list[Any]:
    values = nodes[node_id].get("widgets_values")
    if count == 0 and values is None:
        return []
    if not isinstance(values, list) or len(values) != count:
        raise QualificationError(f"widget_shape_mismatch:{node_id}:{count}")
    return values


def _resolution(values: list[Any]) -> tuple[int, int]:
    aspect_ratio, megapixels, multiple = values
    if aspect_ratio not in ASPECT_RATIOS:
        raise QualificationError("resolution_aspect_invalid")
    if (
        isinstance(megapixels, bool)
        or not isinstance(megapixels, (int, float))
        or isinstance(multiple, bool)
        or not isinstance(multiple, int)
        or megapixels <= 0
        or multiple <= 0
    ):
        raise QualificationError("resolution_controller_invalid")
    w_ratio, h_ratio = ASPECT_RATIOS[aspect_ratio]
    scale = math.sqrt(float(megapixels) * 1024 * 1024 / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    return width, height


def _frame_count(duration: object, expression: object) -> int:
    if expression != FRAME_EXPRESSION:
        raise QualificationError("frame_expression_changed")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration <= 0
    ):
        raise QualificationError("duration_controller_invalid")
    base = max(5, round(float(duration) * 24))
    return int(base + (5 - (base % 17)) % 17)


def _assert_adapted_hash(payload: bytes, expected_sha256: str) -> str:
    actual = _sha256_bytes(payload)
    if actual != expected_sha256:
        raise QualificationError(
            f"adapted_workflow_hash_mismatch:{actual}:{expected_sha256}"
        )
    return actual


def _assert_runtime_artifacts(
    *,
    unet_values: list[Any],
    expected_unet: str,
    clip_values: list[Any],
    video_vae_values: list[Any],
    audio_vae_values: list[Any],
) -> None:
    if unet_values != [expected_unet, "default"]:
        raise QualificationError("diffusion_model_changed")
    if clip_values != [TEXT_ENCODER, "minimax", "default"]:
        raise QualificationError("text_encoder_changed")
    if video_vae_values != [VIDEO_VAE]:
        raise QualificationError("video_vae_changed")
    if audio_vae_values != [AUDIO_VAE]:
        raise QualificationError("audio_vae_changed")


def _assert_output_settings(values: list[Any], expected_prefix: str) -> None:
    if values != [expected_prefix, "auto", "auto"]:
        raise QualificationError("save_video_changed")


def resolve_t2v(
    workflow_path: Path = ADAPTED_T2V,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Translate the reviewed adapted browser graph into an API prompt."""

    exact_payload = EXACT_T2V.read_bytes()
    exact_sha = _sha256_bytes(exact_payload)
    if exact_sha != EXACT_T2V_SHA256:
        raise QualificationError(
            f"exact_source_hash_mismatch:{exact_sha}:{EXACT_T2V_SHA256}"
        )
    workflow, adapted_payload = _load_json(workflow_path)
    nodes = _node_map(workflow)
    _assert_nodes(nodes, NODE_TYPES)
    _assert_links(workflow, EXPECTED_CRITICAL_LINKS)
    for node_id in NODE_TYPES:
        if int(nodes[node_id].get("mode", 0)) != 0:
            raise QualificationError(f"node_not_active:{node_id}")

    metadata = workflow.get("extra", {}).get("castle_servasyy_article", {})
    if not isinstance(metadata, dict):
        raise QualificationError("adapted_metadata_missing")
    if (
        metadata.get("kind") != "adapted-current"
        or metadata.get("source_workflow_id") != EXACT_T2V_WORKFLOW_ID
        or metadata.get("source_sha256") != EXACT_T2V_SHA256
        or metadata.get("exact_source_unchanged") is not True
    ):
        raise QualificationError("adapted_source_binding_mismatch")

    resolution_values = _widgets(nodes, 115, 3)
    width, height = _resolution(resolution_values)
    duration = _widgets(nodes, 133, 1)[0]
    frame_expression = _widgets(nodes, 132, 1)[0]
    frames = _frame_count(duration, frame_expression)
    video_conditioning = _widgets(nodes, 131, 4)
    prompt = video_conditioning[0]
    if not isinstance(prompt, str) or not prompt:
        raise QualificationError("prompt_invalid")
    stale_target_widgets = video_conditioning[1:]

    unet_values = _widgets(nodes, 127, 2)
    clip_values = _widgets(nodes, 128, 3)
    video_vae_values = _widgets(nodes, 119, 1)
    audio_vae_values = _widgets(nodes, 120, 1)
    noise_values = _widgets(nodes, 129, 2)
    scheduler_values = _widgets(nodes, 124, 3)
    sampler_values = _widgets(nodes, 123, 1)
    sage_values = _widgets(nodes, 135, 2)
    spectrum_values = _widgets(nodes, 134, 8)
    create_video_values = _widgets(nodes, 130, 2)
    save_video_values = _widgets(nodes, 92, 3)

    if resolution_values != ["16:9 (Widescreen)", 0.8, 32]:
        raise QualificationError("resolution_controller_changed")
    if duration != 5:
        raise QualificationError("duration_controller_changed")
    if noise_values != [871555678912888, "randomize"]:
        raise QualificationError("noise_control_changed")
    _assert_runtime_artifacts(
        unet_values=unet_values,
        expected_unet=FL2VA_MODEL,
        clip_values=clip_values,
        video_vae_values=video_vae_values,
        audio_vae_values=audio_vae_values,
    )
    if scheduler_values != ["simple", 20, 1]:
        raise QualificationError("scheduler_changed")
    if sampler_values != ["res_multistep"]:
        raise QualificationError("sampler_changed")
    if sage_values != ["auto", False]:
        raise QualificationError("sage_changed")
    if spectrum_values != [0.3, 3, 0.1, 2, 0.25, 6, 16, 20]:
        raise QualificationError("spectrum_changed")
    if create_video_values != [24, 8]:
        raise QualificationError("create_video_changed")
    _assert_output_settings(save_video_values, "video/MiniMax_H3/Article/T2V")
    adapted_sha = _assert_adapted_hash(adapted_payload, ADAPTED_T2V_SHA256)

    graph: dict[str, dict[str, Any]] = {
        "115": {
            "class_type": "ResolutionSelector",
            "inputs": {
                "aspect_ratio": resolution_values[0],
                "megapixels": resolution_values[1],
                "multiple": resolution_values[2],
            },
        },
        "119": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": video_vae_values[0]},
        },
        "120": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": audio_vae_values[0]},
        },
        "121": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["125", 0], "vae": ["120", 0]},
        },
        "122": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["125", 0], "vae": ["119", 0]},
        },
        "123": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": sampler_values[0]},
        },
        "124": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["134", 0],
                "scheduler": scheduler_values[0],
                "steps": scheduler_values[1],
                "denoise": scheduler_values[2],
            },
        },
        "125": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["129", 0],
                "guider": ["126", 0],
                "sampler": ["123", 0],
                "sigmas": ["124", 0],
                "latent_image": ["131", 1],
            },
        },
        "126": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["134", 0], "conditioning": ["131", 0]},
        },
        "127": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": unet_values[0],
                "weight_dtype": unet_values[1],
            },
        },
        "128": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": clip_values[0],
                "type": clip_values[1],
                "device": clip_values[2],
            },
        },
        "129": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": noise_values[0]},
        },
        "130": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["122", 0],
                "audio": ["121", 0],
                "fps": create_video_values[0],
                "bit_depth": create_video_values[1],
            },
        },
        "131": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["128", 0],
                "vae": ["119", 0],
                "prompt": prompt,
                "width": ["115", 0],
                "height": ["115", 1],
                "length": ["132", 1],
            },
        },
        "132": {
            "class_type": "ComfyMathExpression",
            "inputs": {"expression": frame_expression, "values.a": duration},
        },
        "134": {
            "class_type": "SpectrumSDXL",
            "inputs": {
                "model": ["135", 0],
                "w": spectrum_values[0],
                "m": spectrum_values[1],
                "lam": spectrum_values[2],
                "window_size": spectrum_values[3],
                "flex_window": spectrum_values[4],
                "warmup_steps": spectrum_values[5],
                "stop_caching_step": spectrum_values[6],
                "steps": spectrum_values[7],
            },
        },
        "135": {
            "class_type": "PathchSageAttentionKJ",
            "inputs": {
                "model": ["127", 0],
                "sage_attention": sage_values[0],
                "allow_compile": sage_values[1],
            },
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["130", 0],
                "filename_prefix": save_video_values[0],
                "format": save_video_values[1],
                "codec": save_video_values[2],
            },
        },
    }

    graph_sha = _sha256_bytes(_canonical(graph))
    summary: dict[str, Any] = {
        "schema_version": "castle.servasyy-article-qualification.v1",
        "task": "t2v",
        "exact_source": {
            "path": str(EXACT_T2V.relative_to(REPO_ROOT)),
            "workflow_id": EXACT_T2V_WORKFLOW_ID,
            "bytes": len(exact_payload),
            "sha256": exact_sha,
        },
        "adapted_workflow": {
            "path": str(workflow_path.relative_to(REPO_ROOT)),
            "bytes": len(adapted_payload),
            "sha256": adapted_sha,
        },
        "controller_resolution": {
            "node_id": 115,
            "widgets": resolution_values,
            "resolved_width": width,
            "resolved_height": height,
            "target_node_id": 131,
            "links": [244, 245],
        },
        "controller_duration": {
            "value_node_id": 133,
            "value_seconds": duration,
            "math_node_id": 132,
            "expression": frame_expression,
            "resolved_frames": frames,
            "target_node_id": 131,
            "links": [246, 243],
        },
        "target_stale_widgets_ignored": {
            "node_id": 131,
            "width": stale_target_widgets[0],
            "height": stale_target_widgets[1],
            "frames": stale_target_widgets[2],
            "reason": "connected controller outputs are authoritative",
        },
        "model": {
            "node_id": 127,
            "unet_name": unet_values[0],
            "weight_dtype": unet_values[1],
            "lora": None,
        },
        "text_encoder": {
            "node_id": 128,
            "clip_name": clip_values[0],
            "type": clip_values[1],
            "device": clip_values[2],
        },
        "vae": {
            "video_node_id": 119,
            "video": video_vae_values[0],
            "audio_node_id": 120,
            "audio": audio_vae_values[0],
        },
        "sampling": {
            "scheduler_node_id": 124,
            "scheduler": scheduler_values[0],
            "steps": scheduler_values[1],
            "denoise": scheduler_values[2],
            "sampler_node_id": 123,
            "sampler": sampler_values[0],
            "seed_node_id": 129,
            "seed": noise_values[0],
        },
        "sage": {
            "node_id": 135,
            "sage_attention": sage_values[0],
            "allow_compile": sage_values[1],
        },
        "spectrum": {
            "node_id": 134,
            "w": spectrum_values[0],
            "m": spectrum_values[1],
            "lam": spectrum_values[2],
            "window_size": spectrum_values[3],
            "flex_window": spectrum_values[4],
            "warmup_steps": spectrum_values[5],
            "stop_caching_step": spectrum_values[6],
            "steps": spectrum_values[7],
        },
        "prompt": {
            "node_id": 131,
            "utf8_bytes": len(prompt.encode("utf-8")),
            "sha256": _sha256_bytes(prompt.encode("utf-8")),
        },
        "output": {
            "create_node_id": 130,
            "save_node_id": 92,
            "fps": create_video_values[0],
            "bit_depth": create_video_values[1],
            "filename_prefix": save_video_values[0],
            "format": save_video_values[1],
            "codec": save_video_values[2],
        },
        "critical_link_count": len(EXPECTED_CRITICAL_LINKS),
        "translated_node_ids": sorted(int(node_id) for node_id in graph),
        "graph_sha256": graph_sha,
    }
    return graph, summary


def _assert_source_binding(
    *,
    workflow: Mapping[str, Any],
    exact_path: Path,
    exact_sha256: str,
    workflow_id: str,
) -> tuple[bytes, str]:
    exact_payload = exact_path.read_bytes()
    exact_sha = _sha256_bytes(exact_payload)
    if exact_sha != exact_sha256:
        raise QualificationError(
            f"exact_source_hash_mismatch:{exact_sha}:{exact_sha256}"
        )
    metadata = workflow.get("extra", {}).get("castle_servasyy_article", {})
    if not isinstance(metadata, dict):
        raise QualificationError("adapted_metadata_missing")
    if (
        metadata.get("kind") != "adapted-current"
        or metadata.get("source_workflow_id") != workflow_id
        or metadata.get("source_sha256") != exact_sha256
        or metadata.get("exact_source_unchanged") is not True
    ):
        raise QualificationError("adapted_source_binding_mismatch")
    return exact_payload, exact_sha


def resolve_i2v(
    workflow_path: Path = ADAPTED_I2V,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Translate the article I2V/FLF derivative, including exact v0.1 LoRA."""

    workflow, adapted_payload = _load_json(workflow_path)
    nodes = _node_map(workflow)
    _assert_nodes(nodes, I2V_NODE_TYPES)
    _assert_links(workflow, EXPECTED_I2V_LINKS)
    for node_id in I2V_NODE_TYPES:
        expected_mode = 4 if node_id in {138, 144} else 0
        if int(nodes[node_id].get("mode", 0)) != expected_mode:
            raise QualificationError(f"node_mode_mismatch:{node_id}:{expected_mode}")
    exact_payload, exact_sha = _assert_source_binding(
        workflow=workflow,
        exact_path=EXACT_I2V,
        exact_sha256=EXACT_I2V_SHA256,
        workflow_id=EXACT_I2V_WORKFLOW_ID,
    )

    resolution_values = _widgets(nodes, 115, 3)
    width, height = _resolution(resolution_values)
    duration = _widgets(nodes, 135, 1)[0]
    frame_expression = _widgets(nodes, 134, 1)[0]
    frames = _frame_count(duration, frame_expression)
    conditioning_values = _widgets(nodes, 133, 4)
    prompt = conditioning_values[0]
    if not isinstance(prompt, str) or not prompt:
        raise QualificationError("prompt_invalid")

    image_values = _widgets(nodes, 114, 2)
    video_vae_values = _widgets(nodes, 121, 1)
    audio_vae_values = _widgets(nodes, 122, 1)
    sampler_values = _widgets(nodes, 125, 1)
    scheduler_values = _widgets(nodes, 126, 3)
    unet_values = _widgets(nodes, 129, 2)
    clip_values = _widgets(nodes, 130, 3)
    noise_values = _widgets(nodes, 131, 2)
    create_video_values = _widgets(nodes, 132, 2)
    sage_values = _widgets(nodes, 137, 2)
    lora_values = _widgets(nodes, 140, 2)
    _widgets(nodes, 141, 0)
    spectrum_values = _widgets(nodes, 147, 8)
    save_video_values = _widgets(nodes, 92, 3)

    if resolution_values != ["9:16 (Portrait Widescreen)", 1, 32]:
        raise QualificationError("resolution_controller_changed")
    if duration != 5:
        raise QualificationError("duration_controller_changed")
    if noise_values != [395253358326492, "fixed"]:
        raise QualificationError("noise_control_changed")
    _assert_runtime_artifacts(
        unet_values=unet_values,
        expected_unet=FL2VA_MODEL,
        clip_values=clip_values,
        video_vae_values=video_vae_values,
        audio_vae_values=audio_vae_values,
    )
    if scheduler_values != ["simple", 12, 1]:
        raise QualificationError("scheduler_changed")
    if sampler_values != ["res_multistep"]:
        raise QualificationError("sampler_changed")
    if sage_values != ["auto", False]:
        raise QualificationError("sage_changed")
    if lora_values != [
        "H3/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors",
        0.7,
    ]:
        raise QualificationError("i2v_lora_changed")
    if spectrum_values != [0.3, 3, 0.15, 2, 0.25, 5, 12, 12]:
        raise QualificationError("spectrum_changed")
    if create_video_values != [24, 8]:
        raise QualificationError("create_video_changed")
    _assert_output_settings(
        save_video_values, "video/MiniMax_H3/Article/I2V-FLF"
    )
    adapted_sha = _assert_adapted_hash(adapted_payload, ADAPTED_I2V_SHA256)

    graph: dict[str, dict[str, Any]] = {
        "114": {
            "class_type": "LoadImage",
            "inputs": {"image": image_values[0]},
        },
        "115": {
            "class_type": "ResolutionSelector",
            "inputs": {
                "aspect_ratio": resolution_values[0],
                "megapixels": resolution_values[1],
                "multiple": resolution_values[2],
            },
        },
        "121": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": video_vae_values[0]},
        },
        "122": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": audio_vae_values[0]},
        },
        "123": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["127", 0], "vae": ["122", 0]},
        },
        "124": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["127", 0], "vae": ["121", 0]},
        },
        "125": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": sampler_values[0]},
        },
        "126": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["147", 0],
                "scheduler": scheduler_values[0],
                "steps": scheduler_values[1],
                "denoise": scheduler_values[2],
            },
        },
        "127": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["131", 0],
                "guider": ["128", 0],
                "sampler": ["125", 0],
                "sigmas": ["126", 0],
                "latent_image": ["133", 1],
            },
        },
        "128": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["147", 0], "conditioning": ["133", 0]},
        },
        "129": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": unet_values[0],
                "weight_dtype": unet_values[1],
            },
        },
        "130": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": clip_values[0],
                "type": clip_values[1],
                "device": clip_values[2],
            },
        },
        "131": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": noise_values[0]},
        },
        "132": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["124", 0],
                "audio": ["123", 0],
                "fps": create_video_values[0],
                "bit_depth": create_video_values[1],
            },
        },
        "133": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["130", 0],
                "vae": ["121", 0],
                "first_frame": ["114", 0],
                "prompt": prompt,
                "width": ["115", 0],
                "height": ["115", 1],
                "length": ["134", 1],
            },
        },
        "134": {
            "class_type": "ComfyMathExpression",
            "inputs": {"expression": frame_expression, "values.a": duration},
        },
        "137": {
            "class_type": "PathchSageAttentionKJ",
            "inputs": {
                "model": ["140", 0],
                "sage_attention": sage_values[0],
                "allow_compile": sage_values[1],
            },
        },
        "140": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["129", 0],
                "lora_name": lora_values[0],
                "strength_model": lora_values[1],
            },
        },
        "141": {
            "class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
            "inputs": {"model": ["137", 0]},
        },
        "147": {
            "class_type": "SpectrumSDXL",
            "inputs": {
                # SolAttnPatch#144 is mode=4 (bypassed); browser bypass
                # semantics collapse 141 -> 144 -> 147 to this direct edge.
                "model": ["141", 0],
                "w": spectrum_values[0],
                "m": spectrum_values[1],
                "lam": spectrum_values[2],
                "window_size": spectrum_values[3],
                "flex_window": spectrum_values[4],
                "warmup_steps": spectrum_values[5],
                "stop_caching_step": spectrum_values[6],
                "steps": spectrum_values[7],
            },
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["132", 0],
                "filename_prefix": save_video_values[0],
                "format": save_video_values[1],
                "codec": save_video_values[2],
            },
        },
    }

    summary: dict[str, Any] = {
        "schema_version": "castle.servasyy-article-qualification.v1",
        "task": "i2v",
        "exact_source": {
            "path": str(EXACT_I2V.relative_to(REPO_ROOT)),
            "workflow_id": EXACT_I2V_WORKFLOW_ID,
            "bytes": len(exact_payload),
            "sha256": exact_sha,
        },
        "adapted_workflow": {
            "path": str(workflow_path.relative_to(REPO_ROOT)),
            "bytes": len(adapted_payload),
            "sha256": adapted_sha,
        },
        "controller_resolution": {
            "node_id": 115,
            "widgets": resolution_values,
            "resolved_width": width,
            "resolved_height": height,
            "target_node_id": 133,
            "links": [246, 247],
        },
        "controller_duration": {
            "value_node_id": 135,
            "value_seconds": duration,
            "math_node_id": 134,
            "expression": frame_expression,
            "resolved_frames": frames,
            "target_node_id": 133,
            "links": [248, 245],
        },
        "target_stale_widgets_ignored": {
            "node_id": 133,
            "width": conditioning_values[1],
            "height": conditioning_values[2],
            "frames": conditioning_values[3],
            "reason": "connected controller outputs are authoritative",
        },
        "model": {
            "node_id": 129,
            "unet_name": unet_values[0],
            "weight_dtype": unet_values[1],
            "lora_node_id": 140,
            "lora_name": lora_values[0],
            "lora_strength": lora_values[1],
        },
        "text_encoder": {
            "node_id": 130,
            "clip_name": clip_values[0],
            "type": clip_values[1],
            "device": clip_values[2],
        },
        "vae": {
            "video_node_id": 121,
            "video": video_vae_values[0],
            "audio_node_id": 122,
            "audio": audio_vae_values[0],
        },
        "sampling": {
            "scheduler_node_id": 126,
            "scheduler": scheduler_values[0],
            "steps": scheduler_values[1],
            "denoise": scheduler_values[2],
            "sampler_node_id": 125,
            "sampler": sampler_values[0],
            "seed_node_id": 131,
            "seed": noise_values[0],
        },
        "sage": {
            "node_id": 137,
            "sage_attention": sage_values[0],
            "allow_compile": sage_values[1],
            "memory_efficient_node_id": 141,
        },
        "bypassed": {
            "sol_node_id": 144,
            "sol_mode": 4,
            "easy_cache_node_id": 138,
            "easy_cache_mode": 4,
        },
        "spectrum": {
            "node_id": 147,
            "w": spectrum_values[0],
            "m": spectrum_values[1],
            "lam": spectrum_values[2],
            "window_size": spectrum_values[3],
            "flex_window": spectrum_values[4],
            "warmup_steps": spectrum_values[5],
            "stop_caching_step": spectrum_values[6],
            "steps": spectrum_values[7],
        },
        "prompt": {
            "node_id": 133,
            "utf8_bytes": len(prompt.encode("utf-8")),
            "sha256": _sha256_bytes(prompt.encode("utf-8")),
        },
        "references": {
            "first_frame_node_id": 114,
            "first_frame": image_values[0],
            "first_frame_link": 244,
            "last_frame": None,
        },
        "output": {
            "create_node_id": 132,
            "save_node_id": 92,
            "fps": create_video_values[0],
            "bit_depth": create_video_values[1],
            "filename_prefix": save_video_values[0],
            "format": save_video_values[1],
            "codec": save_video_values[2],
        },
        "critical_link_count": len(EXPECTED_I2V_LINKS),
        "translated_node_ids": sorted(int(node_id) for node_id in graph),
        "graph_sha256": _sha256_bytes(_canonical(graph)),
    }
    return graph, summary


def resolve_r2v(
    workflow_path: Path = ADAPTED_R2V,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Translate the article R2V derivative with all five reference links."""

    workflow, adapted_payload = _load_json(workflow_path)
    nodes = _node_map(workflow)
    _assert_nodes(nodes, R2V_NODE_TYPES)
    _assert_links(workflow, EXPECTED_R2V_LINKS)
    for node_id in R2V_NODE_TYPES:
        if int(nodes[node_id].get("mode", 0)) != 0:
            raise QualificationError(f"node_not_active:{node_id}")
    exact_payload, exact_sha = _assert_source_binding(
        workflow=workflow,
        exact_path=EXACT_R2V,
        exact_sha256=EXACT_R2V_SHA256,
        workflow_id=EXACT_R2V_WORKFLOW_ID,
    )

    resolution_values = _widgets(nodes, 115, 3)
    width, height = _resolution(resolution_values)
    duration = _widgets(nodes, 132, 1)[0]
    frame_expression = _widgets(nodes, 131, 1)[0]
    frames = _frame_count(duration, frame_expression)
    conditioning_values = _widgets(nodes, 136, 5)
    prompt = _widgets(nodes, 138, 1)[0]
    if not isinstance(prompt, str) or not prompt:
        raise QualificationError("prompt_invalid")

    image_nodes = [137, 139, 142]
    image_values = [_widgets(nodes, node_id, 2)[0] for node_id in image_nodes]
    audio_nodes = [143, 144]
    audio_values = [_widgets(nodes, node_id, 3)[0] for node_id in audio_nodes]
    video_vae_values = _widgets(nodes, 119, 1)
    audio_vae_values = _widgets(nodes, 120, 1)
    sampler_values = _widgets(nodes, 123, 1)
    scheduler_values = _widgets(nodes, 124, 3)
    unet_values = _widgets(nodes, 127, 2)
    clip_values = _widgets(nodes, 128, 3)
    noise_values = _widgets(nodes, 129, 2)
    create_video_values = _widgets(nodes, 130, 2)
    model_patch_values = _widgets(nodes, 146, 1)
    sage_values = _widgets(nodes, 147, 2)
    spectrum_values = _widgets(nodes, 148, 8)
    save_video_values = _widgets(nodes, 92, 3)

    if resolution_values != ["16:9 (Widescreen)", 0.4, 32]:
        raise QualificationError("resolution_controller_changed")
    if duration != 10:
        raise QualificationError("duration_controller_changed")
    if conditioning_values[4] != "match":
        raise QualificationError("ref_image_size_changed")
    if noise_values != [255004581118724, "randomize"]:
        raise QualificationError("noise_control_changed")
    _assert_runtime_artifacts(
        unet_values=unet_values,
        expected_unet=REF2VA_MODEL,
        clip_values=clip_values,
        video_vae_values=video_vae_values,
        audio_vae_values=audio_vae_values,
    )
    if scheduler_values != ["simple", 25, 1]:
        raise QualificationError("scheduler_changed")
    if sampler_values != ["res_multistep"]:
        raise QualificationError("sampler_changed")
    if model_patch_values != [True]:
        raise QualificationError("model_patch_changed")
    if sage_values != ["auto", True]:
        raise QualificationError("sage_changed")
    if spectrum_values != [0.3, 3, 0.1, 2, 0.25, 8, 20, 25]:
        raise QualificationError("spectrum_changed")
    if create_video_values != [24, 8]:
        raise QualificationError("create_video_changed")
    _assert_output_settings(save_video_values, "video/MiniMax_H3/Article/R2V")
    for tag in ("<Picture 1>", "<Picture 2>", "<Picture 3>", "<Audio 1>", "<Audio 2>"):
        if tag not in prompt:
            raise QualificationError(f"reference_prompt_tag_missing:{tag}")
    adapted_sha = _assert_adapted_hash(adapted_payload, ADAPTED_R2V_SHA256)

    graph: dict[str, dict[str, Any]] = {
        "115": {
            "class_type": "ResolutionSelector",
            "inputs": {
                "aspect_ratio": resolution_values[0],
                "megapixels": resolution_values[1],
                "multiple": resolution_values[2],
            },
        },
        "119": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": video_vae_values[0]},
        },
        "120": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": audio_vae_values[0]},
        },
        "121": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["125", 0], "vae": ["120", 0]},
        },
        "122": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["125", 0], "vae": ["119", 0]},
        },
        "123": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": sampler_values[0]},
        },
        "124": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["148", 0],
                "scheduler": scheduler_values[0],
                "steps": scheduler_values[1],
                "denoise": scheduler_values[2],
            },
        },
        "125": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["129", 0],
                "guider": ["126", 0],
                "sampler": ["123", 0],
                "sigmas": ["124", 0],
                "latent_image": ["136", 1],
            },
        },
        "126": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["148", 0], "conditioning": ["136", 0]},
        },
        "127": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": unet_values[0],
                "weight_dtype": unet_values[1],
            },
        },
        "128": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": clip_values[0],
                "type": clip_values[1],
                "device": clip_values[2],
            },
        },
        "129": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": noise_values[0]},
        },
        "130": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["122", 0],
                "audio": ["121", 0],
                "fps": create_video_values[0],
                "bit_depth": create_video_values[1],
            },
        },
        "131": {
            "class_type": "ComfyMathExpression",
            "inputs": {"expression": frame_expression, "values.a": duration},
        },
        "136": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["128", 0],
                "vae": ["119", 0],
                "audio_vae": ["120", 0],
                "prompt": ["138", 0],
                "width": ["115", 0],
                "height": ["115", 1],
                "length": ["131", 1],
                "ref_image_size": conditioning_values[4],
                "ref_images.ref_image_0": ["137", 0],
                "ref_images.ref_image_1": ["139", 0],
                "ref_images.ref_image_2": ["142", 0],
                "ref_audios.ref_audio_0": ["143", 0],
                "ref_audios.ref_audio_1": ["144", 0],
            },
        },
        "137": {"class_type": "LoadImage", "inputs": {"image": image_values[0]}},
        "138": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": prompt}},
        "139": {"class_type": "LoadImage", "inputs": {"image": image_values[1]}},
        "142": {"class_type": "LoadImage", "inputs": {"image": image_values[2]}},
        "143": {"class_type": "LoadAudio", "inputs": {"audio": audio_values[0]}},
        "144": {"class_type": "LoadAudio", "inputs": {"audio": audio_values[1]}},
        "146": {
            "class_type": "ModelPatchTorchSettings",
            "inputs": {
                "model": ["147", 0],
                "enable_fp16_accumulation": model_patch_values[0],
            },
        },
        "147": {
            "class_type": "PathchSageAttentionKJ",
            "inputs": {
                "model": ["127", 0],
                "sage_attention": sage_values[0],
                "allow_compile": sage_values[1],
            },
        },
        "148": {
            "class_type": "SpectrumSDXLBatch",
            "inputs": {
                "model": ["146", 0],
                "w": spectrum_values[0],
                "m": spectrum_values[1],
                "lam": spectrum_values[2],
                "window_size": spectrum_values[3],
                "flex_window": spectrum_values[4],
                "warmup_steps": spectrum_values[5],
                "stop_caching_step": spectrum_values[6],
                "steps": spectrum_values[7],
            },
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["130", 0],
                "filename_prefix": save_video_values[0],
                "format": save_video_values[1],
                "codec": save_video_values[2],
            },
        },
    }

    summary: dict[str, Any] = {
        "schema_version": "castle.servasyy-article-qualification.v1",
        "task": "r2v",
        "exact_source": {
            "path": str(EXACT_R2V.relative_to(REPO_ROOT)),
            "workflow_id": EXACT_R2V_WORKFLOW_ID,
            "bytes": len(exact_payload),
            "sha256": exact_sha,
        },
        "adapted_workflow": {
            "path": str(workflow_path.relative_to(REPO_ROOT)),
            "bytes": len(adapted_payload),
            "sha256": adapted_sha,
        },
        "controller_resolution": {
            "node_id": 115,
            "widgets": resolution_values,
            "resolved_width": width,
            "resolved_height": height,
            "target_node_id": 136,
            "links": [276, 277],
        },
        "controller_duration": {
            "value_node_id": 132,
            "value_seconds": duration,
            "math_node_id": 131,
            "expression": frame_expression,
            "resolved_frames": frames,
            "target_node_id": 136,
            "links": [261, 275],
        },
        "target_stale_widgets_ignored": {
            "node_id": 136,
            "inline_prompt": conditioning_values[0],
            "width": conditioning_values[1],
            "height": conditioning_values[2],
            "frames": conditioning_values[3],
            "reason": "connected prompt/controller outputs are authoritative",
        },
        "model": {
            "node_id": 127,
            "unet_name": unet_values[0],
            "weight_dtype": unet_values[1],
            "lora": None,
            "fp16_accumulation_node_id": 146,
            "enable_fp16_accumulation": model_patch_values[0],
        },
        "text_encoder": {
            "node_id": 128,
            "clip_name": clip_values[0],
            "type": clip_values[1],
            "device": clip_values[2],
        },
        "vae": {
            "video_node_id": 119,
            "video": video_vae_values[0],
            "audio_node_id": 120,
            "audio": audio_vae_values[0],
        },
        "sampling": {
            "scheduler_node_id": 124,
            "scheduler": scheduler_values[0],
            "steps": scheduler_values[1],
            "denoise": scheduler_values[2],
            "sampler_node_id": 123,
            "sampler": sampler_values[0],
            "seed_node_id": 129,
            "seed": noise_values[0],
        },
        "sage": {
            "node_id": 147,
            "sage_attention": sage_values[0],
            "allow_compile": sage_values[1],
        },
        "spectrum": {
            "node_id": 148,
            "class_type": "SpectrumSDXLBatch",
            "w": spectrum_values[0],
            "m": spectrum_values[1],
            "lam": spectrum_values[2],
            "window_size": spectrum_values[3],
            "flex_window": spectrum_values[4],
            "warmup_steps": spectrum_values[5],
            "stop_caching_step": spectrum_values[6],
            "steps": spectrum_values[7],
        },
        "prompt": {
            "value_node_id": 138,
            "target_node_id": 136,
            "link": 279,
            "utf8_bytes": len(prompt.encode("utf-8")),
            "sha256": _sha256_bytes(prompt.encode("utf-8")),
        },
        "references": {
            "ref_image_size": conditioning_values[4],
            "images": [
                {"node_id": node_id, "filename": filename}
                for node_id, filename in zip(image_nodes, image_values)
            ],
            "audios": [
                {"node_id": node_id, "filename": filename}
                for node_id, filename in zip(audio_nodes, audio_values)
            ],
            "image_links": [278, 282, 283],
            "audio_links": [284, 285],
        },
        "output": {
            "create_node_id": 130,
            "save_node_id": 92,
            "fps": create_video_values[0],
            "bit_depth": create_video_values[1],
            "filename_prefix": save_video_values[0],
            "format": save_video_values[1],
            "codec": save_video_values[2],
        },
        "critical_link_count": len(EXPECTED_R2V_LINKS),
        "translated_node_ids": sorted(int(node_id) for node_id in graph),
        "graph_sha256": _sha256_bytes(_canonical(graph)),
    }
    return graph, summary


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
) -> Mapping[str, Any]:
    # Import lazily so dry-run resolution has no runner-side dependencies.
    from scripts.minimax_h3 import run_native

    return run_native.request_json(
        base_url, path, method=method, payload=payload, timeout=timeout
    )


def _expand_live_autogrow_schema(
    class_type: str,
    required: Mapping[str, Any],
    optional: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand ComfyUI v3 Autogrow groups into their dotted API field names.

    ``object_info`` exposes an Autogrow parent such as ``ref_images`` while the
    prompt API accepts its children as ``ref_images.ref_image_0``.  ComfyUI
    then rebuilds the nested dictionary passed to ``execute``.  Mirroring that
    expansion here keeps preflight from accepting bare fields that Python will
    later receive as unexpected keyword arguments.
    """

    expanded_required: dict[str, Any] = {}
    expanded_optional: dict[str, Any] = {}

    for source, source_is_required in ((required, True), (optional, False)):
        for parent, descriptor in source.items():
            if (
                not isinstance(parent, str)
                or not isinstance(descriptor, list)
                or not descriptor
            ):
                raise QualificationError(f"live_schema_invalid:{class_type}")
            if descriptor[0] != "COMFY_AUTOGROW_V3":
                target = (
                    expanded_required if source_is_required else expanded_optional
                )
                target[parent] = descriptor
                continue

            if len(descriptor) < 2 or not isinstance(descriptor[1], dict):
                raise QualificationError(f"live_schema_invalid:{class_type}")
            template = descriptor[1].get("template")
            if not isinstance(template, dict):
                raise QualificationError(f"live_schema_invalid:{class_type}")
            template_inputs = template.get("input")
            if not isinstance(template_inputs, dict):
                raise QualificationError(f"live_schema_invalid:{class_type}")

            child_descriptor: Any = None
            child_is_required = False
            child_count = 0
            for child_section, is_required in (("required", True), ("optional", False)):
                children = template_inputs.get(child_section, {})
                if not isinstance(children, dict):
                    raise QualificationError(f"live_schema_invalid:{class_type}")
                for candidate in children.values():
                    child_descriptor = candidate
                    child_is_required = is_required
                    child_count += 1
            if child_count != 1 or not isinstance(child_descriptor, list):
                raise QualificationError(f"live_schema_invalid:{class_type}")

            minimum = template.get("min", 1)
            if not isinstance(minimum, int) or minimum < 0:
                raise QualificationError(f"live_schema_invalid:{class_type}")
            names: list[str]
            if "names" in template:
                raw_names = template.get("names")
                if (
                    not isinstance(raw_names, list)
                    or not raw_names
                    or any(not isinstance(name, str) or not name for name in raw_names)
                ):
                    raise QualificationError(f"live_schema_invalid:{class_type}")
                names = raw_names
            else:
                prefix = template.get("prefix")
                maximum = template.get("max")
                if (
                    not isinstance(prefix, str)
                    or not prefix
                    or not isinstance(maximum, int)
                    or maximum < 1
                ):
                    raise QualificationError(f"live_schema_invalid:{class_type}")
                names = [f"{prefix}{index}" for index in range(maximum)]
            if minimum > len(names):
                raise QualificationError(f"live_schema_invalid:{class_type}")

            for index, child in enumerate(names):
                dotted = f"{parent}.{child}"
                if child_is_required and index < minimum:
                    expanded_required[dotted] = child_descriptor
                else:
                    expanded_optional[dotted] = child_descriptor

    return expanded_required, expanded_optional


def _verify_live_graph(
    base_url: str, graph: dict[str, dict[str, Any]]
) -> Mapping[str, Any]:
    from scripts.minimax_h3 import run_native

    validated = run_native.validate_loopback_url(base_url)
    run_native.verify_loopback_listener(validated)
    info = _request_json(validated, "/object_info", timeout=30.0)
    run_native.verify_object_info(info, graph)
    for node_id, node in graph.items():
        class_type = node["class_type"]
        live = info.get(class_type)
        if not isinstance(live, dict):
            raise QualificationError(f"live_node_missing:{node_id}:{class_type}")
        live_inputs = live.get("input")
        if not isinstance(live_inputs, dict):
            raise QualificationError(f"live_schema_invalid:{class_type}")
        static_required = live_inputs.get("required", {})
        static_optional = live_inputs.get("optional", {})
        if not isinstance(static_required, dict) or not isinstance(static_optional, dict):
            raise QualificationError(f"live_schema_invalid:{class_type}")
        required, optional = _expand_live_autogrow_schema(
            class_type, static_required, static_optional
        )
        supplied = set(node["inputs"])
        allowed = set(required) | set(optional)
        if class_type == "SaveVideo":
            allowed.update(
                key for key in supplied if key.startswith("codec.")
            )
        unknown = sorted(supplied - allowed)
        missing = sorted(set(required) - supplied)
        if unknown or missing:
            raise QualificationError(
                f"live_schema_binding_mismatch:{node_id}:{class_type}:"
                f"unknown={unknown}:missing={missing}"
            )
        for field, value in node["inputs"].items():
            if isinstance(value, list):
                continue
            descriptor = required.get(field) or optional.get(field)
            if not isinstance(descriptor, list) or not descriptor:
                continue
            options: list[Any] | None = None
            if isinstance(descriptor[0], list):
                options = descriptor[0]
            elif (
                descriptor[0] == "COMBO"
                and len(descriptor) > 1
                and isinstance(descriptor[1], dict)
                and isinstance(descriptor[1].get("options"), list)
            ):
                options = descriptor[1]["options"]
            if options is not None and value not in options:
                raise QualificationError(
                    f"live_combo_value_missing:{node_id}:{class_type}:{field}:{value}"
                )
    queue = _request_json(validated, "/queue", timeout=10.0)
    run_native.require_queue_idle(queue)
    return info


def _history_output_path(
    history_item: Mapping[str, Any], output_root: Path, *, task: str
) -> Path:
    outputs = history_item.get("outputs")
    if not isinstance(outputs, dict):
        raise QualificationError("history_outputs_invalid")
    save_output = outputs.get("92")
    if not isinstance(save_output, dict):
        raise QualificationError("save_output_missing")
    candidates = save_output.get("images")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or not isinstance(candidates[0], dict)
    ):
        raise QualificationError("save_output_ambiguous")
    metadata = candidates[0]
    filename = metadata.get("filename")
    subfolder = metadata.get("subfolder")
    output_type = metadata.get("type")
    prefixes = {"t2v": "T2V", "i2v": "I2V-FLF", "r2v": "R2V"}
    prefix = prefixes.get(task)
    if prefix is None:
        raise QualificationError("output_task_invalid")
    if (
        not isinstance(filename, str)
        or re.fullmatch(re.escape(prefix) + r"_[0-9]{5}_\.(?:mp4|webm)", filename) is None
        or subfolder != "video/MiniMax_H3/Article"
        or output_type != "output"
    ):
        raise QualificationError("save_output_namespace_mismatch")
    root = output_root.resolve(strict=True)
    candidate = root.joinpath(subfolder, filename)
    info = candidate.lstat()
    resolved = candidate.resolve(strict=True)
    if (
        not resolved.is_file()
        or info.st_size <= 0
        or os.path.commonpath((str(root), str(resolved))) != str(root)
    ):
        raise QualificationError("save_output_path_invalid")
    return resolved


def _probe_output(path: Path, *, width: int, height: int, frames: int) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if completed.returncode != 0:
        raise QualificationError("ffprobe_failed")
    parsed = json.loads(completed.stdout)
    streams = parsed.get("streams")
    if not isinstance(streams, list):
        raise QualificationError("ffprobe_streams_invalid")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(video_streams) != 1 or len(audio_streams) != 1:
        raise QualificationError("output_av_stream_count_mismatch")
    video = video_streams[0]
    if video.get("width") != width or video.get("height") != height:
        raise QualificationError("output_resolution_mismatch")
    if video.get("codec_name") != "h264":
        raise QualificationError("output_video_codec_mismatch")
    if audio_streams[0].get("codec_name") != "aac":
        raise QualificationError("output_audio_codec_mismatch")
    try:
        frame_rate = Fraction(str(video.get("avg_frame_rate")))
    except (ValueError, ZeroDivisionError) as error:
        raise QualificationError("output_frame_rate_unavailable") from error
    if frame_rate != 24:
        raise QualificationError(f"output_frame_rate_mismatch:{frame_rate}:24")
    observed_frames = video.get("nb_read_frames") or video.get("nb_frames")
    try:
        observed_frames_int = int(observed_frames)
    except (TypeError, ValueError) as error:
        raise QualificationError("output_frame_count_unavailable") from error
    if observed_frames_int != frames:
        raise QualificationError(
            f"output_frame_count_mismatch:{observed_frames_int}:{frames}"
        )
    format_info = parsed.get("format", {})
    return {
        "width": video["width"],
        "height": video["height"],
        "frames": observed_frames_int,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio_streams[0].get("codec_name"),
        "audio_streams": len(audio_streams),
        "fps": float(frame_rate),
        "duration_seconds": float(format_info.get("duration", 0.0)),
    }


def _decode_audit(path: Path, *, frames: int) -> dict[str, Any]:
    frame_decode = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "framemd5",
            "-",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if frame_decode.returncode != 0:
        raise QualificationError("full_video_decode_failed")
    frame_lines = [
        line
        for line in frame_decode.stdout.decode("ascii", errors="strict").splitlines()
        if line and not line.startswith("#")
    ]
    if len(frame_lines) != frames:
        raise QualificationError(
            f"full_decode_frame_count_mismatch:{len(frame_lines)}:{frames}"
        )
    frame_hashes = [line.rsplit(", ", 1)[-1] for line in frame_lines]

    audio_decode = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if audio_decode.returncode != 0:
        raise QualificationError("full_audio_decode_failed")
    audio_log = audio_decode.stderr.decode("utf-8", errors="replace")
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", audio_log)
    max_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", audio_log)
    if mean_match is None or max_match is None:
        raise QualificationError("audio_volume_audit_missing")
    return {
        "decoded_frames": len(frame_lines),
        "unique_frame_hashes": len(set(frame_hashes)),
        "audio_mean_db": float(mean_match.group(1)),
        "audio_peak_db": float(max_match.group(1)),
    }


def _bound_artifact(path: Path, *, root: Path) -> dict[str, Any]:
    try:
        root_resolved = root.resolve(strict=True)
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise QualificationError(f"bound_artifact_missing:{path}") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or info.st_size <= 0
        or os.path.commonpath((str(root_resolved), str(resolved))) != str(root_resolved)
    ):
        raise QualificationError(f"bound_artifact_invalid:{path}")
    size, digest = _sha256_file(resolved)
    return {
        "path": str(resolved),
        "bytes": size,
        "sha256": digest,
    }


def _bound_execution_inputs(
    graph: Mapping[str, Mapping[str, Any]],
    summary: Mapping[str, Any],
    *,
    input_root: Path,
    models_root: Path,
) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    for node_id, node in sorted(graph.items(), key=lambda item: int(item[0])):
        class_type = node.get("class_type")
        field = "image" if class_type == "LoadImage" else "audio" if class_type == "LoadAudio" else None
        if field is None:
            continue
        inputs = node.get("inputs")
        filename = inputs.get(field) if isinstance(inputs, dict) else None
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).is_absolute()
            or ".." in Path(filename).parts
        ):
            raise QualificationError(f"reference_filename_invalid:{node_id}")
        artifact = _bound_artifact(input_root / filename, root=input_root)
        references.append(
            {
                "node_id": int(node_id),
                "class_type": class_type,
                "filename": filename,
                **artifact,
            }
        )

    lora: dict[str, Any] | None = None
    if summary.get("task") == "i2v":
        model_summary = summary.get("model")
        lora_name = model_summary.get("lora_name") if isinstance(model_summary, dict) else None
        if lora_name != "H3/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors":
            raise QualificationError("i2v_lora_binding_invalid")
        lora = _bound_artifact(models_root / "loras" / lora_name, root=models_root)
        if lora["bytes"] != LIGHTX_V01_BYTES or lora["sha256"] != LIGHTX_V01_SHA256:
            raise QualificationError("i2v_lora_identity_mismatch")
    return {"references": references, "lora": lora}


def execute_qualification(
    graph: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    *,
    comfy_url: str,
    output_root: Path,
    input_root: Path,
    models_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    from scripts.minimax_h3 import run_native

    base_url = run_native.validate_loopback_url(comfy_url)
    task = str(summary.get("task") or "")
    if task not in {"t2v", "i2v", "r2v"}:
        raise QualificationError("qualification_task_invalid")
    bound_inputs = _bound_execution_inputs(
        graph,
        summary,
        input_root=input_root,
        models_root=models_root,
    )
    _verify_live_graph(base_url, graph)
    # Recheck immediately before the only state-changing request.
    if _bound_execution_inputs(
        graph,
        summary,
        input_root=input_root,
        models_root=models_root,
    ) != bound_inputs:
        raise QualificationError("execution_inputs_changed_before_submit")
    _verify_live_graph(base_url, graph)
    prompt_id = str(uuid.uuid4())
    submitted = False
    started = time.monotonic()
    try:
        run_native.submit_prompt(
            base_url,
            graph,
            client_id=f"castle-servasyy-{task}-{uuid.uuid4().hex}",
            prompt_id=prompt_id,
        )
        submitted = True
        print(
            json.dumps(
                {"event": "submitted", "task": task, "prompt_id": prompt_id},
                sort_keys=True,
            ),
            flush=True,
        )
        deadline_epoch = time.time() + timeout_seconds
        next_heartbeat = time.monotonic() + 30.0
        history_path = "/history/" + prompt_id
        while True:
            if time.time() >= deadline_epoch:
                raise QualificationError("qualification_timeout")
            history = _request_json(base_url, history_path, timeout=10.0)
            item = history.get(prompt_id)
            if item is not None:
                if not isinstance(item, dict):
                    raise QualificationError("history_item_invalid")
                status = item.get("status")
                if not isinstance(status, dict):
                    raise QualificationError("history_status_invalid")
                status_str = status.get("status_str")
                messages = status.get("messages")
                if status_str == "error":
                    raise QualificationError("comfy_generation_failed")
                if isinstance(messages, list) and any(
                    isinstance(message, (list, tuple))
                    and message
                    and message[0] in run_native.COMFY_HISTORY_FAILURE_MESSAGES
                    for message in messages
                ):
                    raise QualificationError("comfy_generation_failed")
                if status_str == "success" and status.get("completed") is True:
                    history_item = item
                    break
            if time.monotonic() >= next_heartbeat:
                queue = _request_json(base_url, "/queue", timeout=10.0)
                running = queue.get("queue_running")
                pending = queue.get("queue_pending")
                print(
                    json.dumps(
                        {
                            "event": "running",
                            "task": task,
                            "elapsed_seconds": round(time.monotonic() - started, 1),
                            "queue_running": len(running) if isinstance(running, list) else None,
                            "queue_pending": len(pending) if isinstance(pending, list) else None,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                next_heartbeat = time.monotonic() + 30.0
            time.sleep(2.0)

        output_path = _history_output_path(history_item, output_root, task=task)
        width = summary["controller_resolution"]["resolved_width"]
        height = summary["controller_resolution"]["resolved_height"]
        frames = summary["controller_duration"]["resolved_frames"]
        probe = _probe_output(output_path, width=width, height=height, frames=frames)
        decode = _decode_audit(output_path, frames=frames)
        size, digest = _sha256_file(output_path)
        if _bound_execution_inputs(
            graph,
            summary,
            input_root=input_root,
            models_root=models_root,
        ) != bound_inputs:
            raise QualificationError("execution_inputs_changed_during_run")
        result = {
            "event": "qualified",
            "task": task,
            "prompt_id": prompt_id,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "graph_sha256": summary["graph_sha256"],
            "path": str(output_path),
            "bytes": size,
            "sha256": digest,
            "probe": probe,
            "decode": decode,
            "inputs": bound_inputs,
        }
        print(json.dumps(result, sort_keys=True), flush=True)
        return result
    except Exception:
        if submitted:
            action = run_native.cancel_owned_prompt(base_url, prompt_id)
            print(
                json.dumps(
                    {
                        "event": "cancel",
                        "task": task,
                        "prompt_id": prompt_id,
                        "action": action,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            run_native.wait_for_comfy_queue_idle(base_url, timeout_seconds=None)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("t2v", "i2v", "r2v"), default="t2v")
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument(
        "--output-root", type=Path, default=(Path.home() / 'comfyui/output')
    )
    parser.add_argument(
        "--input-root", type=Path, default=(Path.home() / 'comfyui/input')
    )
    parser.add_argument(
        "--models-root", type=Path, default=(Path.home() / 'comfyui/models')
    )
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Submit the resolved prompt after live fail-closed checks.",
    )
    parser.add_argument(
        "--emit-graph",
        action="store_true",
        help="Include the translated API graph in dry-run JSON output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        resolvers = {
            "t2v": (resolve_t2v, ADAPTED_T2V),
            "i2v": (resolve_i2v, ADAPTED_I2V),
            "r2v": (resolve_r2v, ADAPTED_R2V),
        }
        resolver, default_workflow = resolvers[args.task]
        graph, summary = resolver(args.workflow or default_workflow)
        output: dict[str, Any] = {"summary": summary}
        if args.emit_graph:
            output["graph"] = graph
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        if args.execute:
            execute_qualification(
                graph,
                summary,
                comfy_url=args.comfy_url,
                output_root=args.output_root,
                input_root=args.input_root,
                models_root=args.models_root,
                timeout_seconds=args.timeout_seconds,
            )
        return 0
    except (OSError, ValueError, QualificationError) as error:
        print(
            json.dumps(
                {"event": "failed", "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
