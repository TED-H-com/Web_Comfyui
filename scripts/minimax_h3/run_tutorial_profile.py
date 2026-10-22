#!/usr/bin/env python3
"""Reproducible local validation runner for MiniMAX H3 tutorial lanes.

Dry-run is the default and has no filesystem, ComfyUI, or GPU side effects.
``--execute`` is the only submission path.  It verifies the pinned model bytes,
the live ComfyUI node schemas, and the loopback-only listener before submitting
one API-format graph.  The resulting receipt is written outside the checkout as
an owner-only JSON file.

This runner intentionally contains no credit, cost, membership, or run-count
policy.  It validates only the technical contract of the requested local run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
WEBUI_ROOT = REPO_ROOT / "webui"
for module_root in (str(HERE), str(WEBUI_ROOT)):
    if module_root not in sys.path:
        sys.path.insert(0, module_root)

import minimax_h3_native as native  # noqa: E402
import run_native as guarded  # noqa: E402
from artifacts import (  # noqa: E402
    ArtifactSpec,
    artifact_destination,
    load_manifest,
    select_family,
    verify_file,
)


RunnerError = guarded.RunnerError

SCHEMA_VERSION = 1
DEFAULT_COMFY_URL = guarded.DEFAULT_COMFY_URL
DEFAULT_COMFY_DIR = Path.home() / "comfyui"
DEFAULT_STATE_ROOT = (
    Path.home() / ".local/share/web-comfyui/minimax-h3-tutorial-runs"
)
DEFAULT_BASELINE_MANIFEST = REPO_ROOT / "manifests/minimax_h3/models.lock.json"
DEFAULT_TUTORIAL_MANIFEST = (
    REPO_ROOT / "manifests/minimax_h3/tutorial_stack.lock.json"
)
DEFAULT_GPU_QUEUE_SCRIPT = guarded.DEFAULT_GPU_QUEUE_SCRIPT

STOCK_PROFILE = "stock-20step"
LARRY_PROFILE = "larry-v4-step600-ema-6step"
SERVASYY_SAGE_PROFILE = "servasyy-sage-20step"
SERVASYY_EASYCACHE_PROFILE = "servasyy-sage-easycache-0.1-20step"
SERVASYY_TEACACHE_PROFILE = "servasyy-sage-teacache-0.15-20step"
QRUSHER_PROFILE = "qrusher-10eros-sage-20step"
BLACKWELL_FL_PROFILE = "article-blackwell-nvfp4-fl-sage-20step"
PROFILE_NAMES = (
    STOCK_PROFILE,
    LARRY_PROFILE,
    SERVASYY_SAGE_PROFILE,
    SERVASYY_EASYCACHE_PROFILE,
    SERVASYY_TEACACHE_PROFILE,
    QRUSHER_PROFILE,
    BLACKWELL_FL_PROFILE,
)

LARRY_LORA_ID = "larry-v4-step600-ema"
LARRY_LORA = "H3/minimax_h3_turbo_v4_step600_ema.safetensors"
LARRY_LORA_DESTINATION = f"loras/{LARRY_LORA}"
QRUSHER_MODEL_ID = "10eros-test4-int8"
QRUSHER_MODEL = "H3/10Eros_Max_h3_fl2va_int8_convrot_test4_pruned.safetensors"
QRUSHER_MODEL_DESTINATION = f"diffusion_models/{QRUSHER_MODEL}"
QRUSHER_SOURCE = (
    "QrusherZA/10Eros-Max-int8-convrot"
    "@658072dd8f6481664dc9027def160cfaf85ecbe0"
)
QRUSHER_EXPECTED_BYTES = 20_970_365_904
QRUSHER_SHA256 = "68babdec8c39afae1218ba6808fdd97ebfed0e233a6b31189c40494bbffe8814"
BLACKWELL_FL_MODEL_ID = "article-blackwell-fl2va-pruned-nvfp4"
BLACKWELL_FL_MODEL = "H3/minimax_h3_fl2va_pruned_nvfp4.safetensors"
BLACKWELL_FL_MODEL_DESTINATION = f"diffusion_models/{BLACKWELL_FL_MODEL}"
BLACKWELL_FL_SOURCE = (
    "lilcheaty/MiniMax-H3-NVFP4"
    "@8c5abfed61e1b6a170240792b65253fba1a65b7b"
)
BLACKWELL_FL_EXPECTED_BYTES = 12_528_636_800
BLACKWELL_FL_SHA256 = (
    "72fa9269ce551fb63ff42a32d9b46d0c122e84b4b2c511e22fa698287b088f70"
)

DEFAULT_WIDTH = 1344
DEFAULT_HEIGHT = 768
DEFAULT_FRAMES = 124
FPS = 24.0
BIT_DEPTH = 8
CRF = 23.0
MIN_FRAMES = 22
MAX_FRAMES = 362
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60
MIN_LIFECYCLE_GRACE_SECONDS = 240
LIFECYCLE_GRACE_SECONDS = max(
    MIN_LIFECYCLE_GRACE_SECONDS,
    int(guarded.SERVICE_RECYCLE_TIMEOUT_SECONDS) + 60,
)
POLL_SECONDS = 2.0
SAVE_NODE_ID = "14"
WORKER_SPEC_SCHEMA_VERSION = 1
WORKER_TOKEN_ENV = "CASTLE_H3_TUTORIAL_WORKER_TOKEN"
WORKER_STATE_ENV = "CASTLE_H3_TUTORIAL_WORKER_STATE_ROOT"
WORKER_SPEC_ARGUMENT = "--_tutorial-worker-spec"

RUN_ID_RE = re.compile(r"h3-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SAFE_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.mp4")
ALLOWED_PROMPT_CONTROLS = frozenset({"\t", "\n", "\r"})


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    name: str
    steps: int
    scheduler: str
    sampler_class: str
    unet_name: str = native.FL2VA_MODEL
    lora_destination: str | None = None


@dataclass(frozen=True, slots=True)
class OutputBinding:
    path: Path
    filename: str
    subfolder: str
    output_type: str


PROFILES: Mapping[str, ProfileSpec] = {
    STOCK_PROFILE: ProfileSpec(
        name=STOCK_PROFILE,
        steps=20,
        scheduler="simple",
        sampler_class="KSamplerSelect",
    ),
    LARRY_PROFILE: ProfileSpec(
        name=LARRY_PROFILE,
        steps=6,
        scheduler="simple",
        sampler_class="MiniMaxH3TurboSampler",
        lora_destination=LARRY_LORA_DESTINATION,
    ),
    SERVASYY_SAGE_PROFILE: ProfileSpec(
        name=SERVASYY_SAGE_PROFILE,
        steps=20,
        scheduler="simple",
        sampler_class="KSamplerSelect",
    ),
    SERVASYY_EASYCACHE_PROFILE: ProfileSpec(
        name=SERVASYY_EASYCACHE_PROFILE,
        steps=20,
        scheduler="simple",
        sampler_class="KSamplerSelect",
    ),
    SERVASYY_TEACACHE_PROFILE: ProfileSpec(
        name=SERVASYY_TEACACHE_PROFILE,
        steps=20,
        scheduler="simple",
        sampler_class="KSamplerSelect",
    ),
    QRUSHER_PROFILE: ProfileSpec(
        name=QRUSHER_PROFILE,
        steps=20,
        scheduler="simple",
        sampler_class="KSamplerSelect",
        unet_name=QRUSHER_MODEL,
    ),
    BLACKWELL_FL_PROFILE: ProfileSpec(
        name=BLACKWELL_FL_PROFILE,
        steps=20,
        scheduler="simple",
        sampler_class="KSamplerSelect",
        unet_name=BLACKWELL_FL_MODEL,
    ),
}

SERVASYY_PROFILES = frozenset(
    {
        SERVASYY_SAGE_PROFILE,
        SERVASYY_EASYCACHE_PROFILE,
        SERVASYY_TEACACHE_PROFILE,
        QRUSHER_PROFILE,
        BLACKWELL_FL_PROFILE,
    }
)


def _fail(code: str, message: str) -> None:
    raise RunnerError(code, message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise RunnerError(
            "serialization_failed", "Tutorial runner data cannot be serialized."
        ) from error


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"h3-{stamp}-{secrets.token_hex(6)}"


def _clean_prompt(value: object) -> str:
    if type(value) is not str:
        _fail("prompt_invalid", "MiniMAX H3 prompt must be text.")
    prompt = value.strip()
    if not prompt:
        _fail("prompt_invalid", "MiniMAX H3 prompt must not be blank.")
    if len(prompt) > native.MAX_PROMPT_CHARS:
        _fail("prompt_too_long", "MiniMAX H3 prompt is too long.")
    if any(
        (ord(character) < 32 and character not in ALLOWED_PROMPT_CONTROLS)
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in prompt
    ):
        _fail("prompt_invalid", "MiniMAX H3 prompt contains invalid characters.")
    return prompt


def _validate_dimensions(
    width: object, height: object, *, profile: str
) -> tuple[int, int]:
    allowed = (
        {(1152, 640), (640, 1152)}
        if profile in SERVASYY_PROFILES
        else {(1344, 768), (768, 1344)}
    )
    if (
        type(width) is not int
        or type(height) is not int
        or width % 32
        or height % 32
        or (width, height) not in allowed
    ):
        _fail(
            "resolution_invalid",
            "Tutorial validation resolution does not match the selected profile.",
        )
    return width, height


def _validate_frames(value: object) -> int:
    if (
        type(value) is not int
        or not MIN_FRAMES <= value <= MAX_FRAMES
        or (value - 5) % 17 != 0
    ):
        _fail(
            "frames_invalid",
            "MiniMAX H3 frames must be 17k+5 between 22 and 362.",
        )
    return value


def _validate_seed(value: object) -> int:
    if type(value) is not int or not 0 <= value < (1 << 64):
        _fail("seed_invalid", "Seed must be an unsigned 64-bit integer.")
    return value


def _output_subfolder(profile: str) -> str:
    if profile not in PROFILES:
        _fail("profile_invalid", "Tutorial profile is invalid.")
    return f"Castle/minimax-h3/tutorial/{profile}"


def build_tutorial_graph(
    *,
    profile: str,
    prompt: object,
    seed: object,
    run_id: str,
    width: object | None = None,
    height: object | None = None,
    frames: object = DEFAULT_FRAMES,
) -> dict[str, dict[str, object]]:
    """Build one exact API-format tutorial graph."""

    spec = PROFILES.get(profile)
    if spec is None:
        _fail("profile_invalid", "Tutorial profile is invalid.")
    if RUN_ID_RE.fullmatch(run_id) is None:
        _fail("run_id_invalid", "Tutorial run identifier is invalid.")
    clean_prompt = _clean_prompt(prompt)
    clean_seed = _validate_seed(seed)
    if width is None and height is None:
        width, height = (
            (1152, 640)
            if profile in SERVASYY_PROFILES
            else (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        )
    elif width is None or height is None:
        _fail("resolution_invalid", "Both width and height must be supplied.")
    clean_width, clean_height = _validate_dimensions(
        width, height, profile=profile
    )
    clean_frames = _validate_frames(frames)
    output_prefix = f"{_output_subfolder(profile)}/{run_id}"

    graph: dict[str, dict[str, object]] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": spec.unet_name,
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": native.TEXT_ENCODER,
                "type": "minimax",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": native.VIDEO_VAE},
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": native.AUDIO_VAE},
        },
        "5": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "prompt": clean_prompt,
                "width": clean_width,
                "height": clean_height,
                "length": clean_frames,
            },
        },
        "6": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": clean_seed},
        },
        "7": {
            "class_type": "BasicGuider",
            "inputs": {
                "model": ["1", 0],
                "conditioning": ["5", 0],
            },
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["1", 0],
                "scheduler": spec.scheduler,
                "steps": spec.steps,
                "denoise": 1.0,
            },
        },
        "10": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["6", 0],
                "guider": ["7", 0],
                "sampler": ["8", 0],
                "sigmas": ["9", 0],
                "latent_image": ["5", 1],
            },
        },
        "11": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["10", 0], "vae": ["3", 0]},
        },
        "12": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["10", 0], "vae": ["4", 0]},
        },
        "13": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["11", 0],
                "audio": ["12", 0],
                "fps": FPS,
                "bit_depth": BIT_DEPTH,
            },
        },
        SAVE_NODE_ID: {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["13", 0],
                "filename_prefix": output_prefix,
                "format": "mp4",
                "codec": "h264",
                "codec.encoding": "re-encode",
                "codec.encoding.crf": CRF,
            },
        },
    }

    if profile == STOCK_PROFILE:
        graph["8"] = {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": native.STOCK_SAMPLER},
        }
    elif profile == LARRY_PROFILE:
        graph["15"] = {
            "class_type": "MiniMaxH3TurboLoRA",
            "inputs": {
                "model": ["1", 0],
                "lora_name": LARRY_LORA,
                "strength": 1.0,
                "low_vram": False,
            },
        }
        graph["16"] = {
            "class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
            "inputs": {"model": ["15", 0]},
        }
        graph["17"] = {
            "class_type": "MiniMaxH3TurboSampler",
            "inputs": {},
        }
        graph["7"]["inputs"]["model"] = ["16", 0]  # type: ignore[index]
        graph["9"]["inputs"]["model"] = ["16", 0]  # type: ignore[index]
        graph["10"]["inputs"]["sampler"] = ["17", 0]  # type: ignore[index]
    else:
        graph["8"] = {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": native.STOCK_SAMPLER},
        }
        graph["15"] = {
            "class_type": "PathchSageAttentionKJ",
            "inputs": {
                "model": ["1", 0],
                "sage_attention": "auto",
                "allow_compile": False,
            },
        }
        model_node = "15"
        if profile == SERVASYY_EASYCACHE_PROFILE:
            graph["16"] = {
                "class_type": "EasyCache",
                "inputs": {
                    "model": ["15", 0],
                    "reuse_threshold": 0.1,
                    "start_percent": 0.15,
                    "end_percent": 0.95,
                    "verbose": False,
                },
            }
            model_node = "16"
        elif profile == SERVASYY_TEACACHE_PROFILE:
            graph["16"] = {
                "class_type": "MiniMaxH3TeaCache",
                "inputs": {
                    "model": ["15", 0],
                    "rel_l1_thresh": 0.15,
                    "start_step": 2,
                    "end_step": -2,
                    "total_steps": 20,
                },
            }
            model_node = "16"
        graph["7"]["inputs"]["model"] = [model_node, 0]  # type: ignore[index]
        graph["9"]["inputs"]["model"] = [model_node, 0]  # type: ignore[index]

    validate_graph_contract(graph, profile=profile)
    return graph


def validate_graph_contract(
    graph: Mapping[str, Mapping[str, object]], *, profile: str
) -> None:
    """Reject any mutation away from the tutorial graph contracts."""

    spec = PROFILES.get(profile)
    if spec is None:
        _fail("profile_invalid", "Tutorial profile is invalid.")
    try:
        loader = graph["1"]["inputs"]
        conditioning = graph["5"]["inputs"]
        scheduler = graph["9"]["inputs"]
        sampler = graph["10"]["inputs"]
        save = graph[SAVE_NODE_ID]["inputs"]
    except (KeyError, TypeError) as error:
        raise RunnerError("graph_invalid", "Tutorial graph is incomplete.") from error
    if not all(
        isinstance(value, dict)
        for value in (loader, conditioning, scheduler, sampler, save)
    ):
        _fail("graph_invalid", "Tutorial graph inputs are invalid.")

    classes = {
        str(node.get("class_type") or "")
        for node in graph.values()
        if isinstance(node, Mapping)
    }
    shared_required = {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "MiniMaxH3ImageToVideo",
        "RandomNoise",
        "BasicGuider",
        "BasicScheduler",
        "SamplerCustomAdvanced",
        "VAEDecode",
        "VAEDecodeAudio",
        "CreateVideo",
        "SaveVideo",
    }
    forbidden = {
        "LoadImage",
        "LoadVideo",
        "LoadAudio",
        "MiniMaxH3ReferenceToVideo",
        "MiniMaxH3SpectrumPatch",
        "MiniMaxH3Cache",
    }
    if not shared_required <= classes or classes & forbidden:
        _fail("graph_contract_violation", "Tutorial graph left its T2V lane.")
    if loader.get("unet_name") != spec.unet_name:
        _fail(
            "graph_contract_violation",
            "Tutorial graph is not bound to its pinned FL2VA checkpoint.",
        )
    expected_axes = (
        {1152, 640}
        if profile in SERVASYY_PROFILES
        else {1344, 768}
    )
    if (
        {conditioning.get("width"), conditioning.get("height")} != expected_axes
        or scheduler.get("steps") != spec.steps
        or scheduler.get("scheduler") != "simple"
        or save.get("format") != "mp4"
        or save.get("codec") != "h264"
    ):
        _fail("graph_contract_violation", "Tutorial graph settings are invalid.")

    if profile == STOCK_PROFILE:
        if classes & {
            "MiniMaxH3TurboLoRA",
            "MiniMaxH3TurboSampler",
            "MiniMaxH3MemoryEfficientSageAttentionPatch",
            "PathchSageAttentionKJ",
            "EasyCache",
            "MiniMaxH3TeaCache",
        }:
            _fail("graph_contract_violation", "Stock graph contains tutorial accelerators.")
        if (
            graph.get("8", {}).get("class_type") != "KSamplerSelect"
            or graph.get("8", {}).get("inputs", {}).get("sampler_name")
            != native.STOCK_SAMPLER
            or sampler.get("sampler") != ["8", 0]
        ):
            _fail("graph_contract_violation", "Stock graph sampler is invalid.")
        return

    if profile in SERVASYY_PROFILES:
        sage = graph.get("15")
        cache = graph.get("16")
        expected_model = (
            "15"
            if profile
            in {
                SERVASYY_SAGE_PROFILE,
                QRUSHER_PROFILE,
                BLACKWELL_FL_PROFILE,
            }
            else "16"
        )
        if (
            not isinstance(sage, Mapping)
            or sage.get("class_type") != "PathchSageAttentionKJ"
            or sage.get("inputs")
            != {
                "model": ["1", 0],
                "sage_attention": "auto",
                "allow_compile": False,
            }
            or graph.get("8", {}).get("class_type") != "KSamplerSelect"
            or graph.get("8", {}).get("inputs", {}).get("sampler_name")
            != native.STOCK_SAMPLER
            or sampler.get("sampler") != ["8", 0]
            or graph["7"]["inputs"].get("model") != [expected_model, 0]  # type: ignore[union-attr]
            or scheduler.get("model") != [expected_model, 0]
            or classes
            & {
                "MiniMaxH3TurboLoRA",
                "MiniMaxH3TurboSampler",
                "MiniMaxH3MemoryEfficientSageAttentionPatch",
            }
        ):
            _fail("graph_contract_violation", "Servasyy Sage graph is invalid.")
        if profile == SERVASYY_EASYCACHE_PROFILE:
            if (
                not isinstance(cache, Mapping)
                or cache.get("class_type") != "EasyCache"
                or cache.get("inputs")
                != {
                    "model": ["15", 0],
                    "reuse_threshold": 0.1,
                    "start_percent": 0.15,
                    "end_percent": 0.95,
                    "verbose": False,
                }
            ):
                _fail(
                    "graph_contract_violation",
                    "Servasyy EasyCache graph is invalid.",
                )
            if "MiniMaxH3TeaCache" in classes:
                _fail(
                    "graph_contract_violation",
                    "Servasyy EasyCache graph contains H3 TeaCache.",
                )
        elif profile == SERVASYY_TEACACHE_PROFILE:
            if (
                not isinstance(cache, Mapping)
                or cache.get("class_type") != "MiniMaxH3TeaCache"
                or cache.get("inputs")
                != {
                    "model": ["15", 0],
                    "rel_l1_thresh": 0.15,
                    "start_step": 2,
                    "end_step": -2,
                    "total_steps": 20,
                }
                or "EasyCache" in classes
            ):
                _fail(
                    "graph_contract_violation",
                    "Servasyy H3 TeaCache graph is invalid.",
                )
        elif cache is not None or classes & {"EasyCache", "MiniMaxH3TeaCache"}:
            _fail(
                "graph_contract_violation",
                "Servasyy Sage control contains EasyCache.",
            )
        return

    lora = graph.get("15")
    sage = graph.get("16")
    turbo = graph.get("17")
    if (
        not isinstance(lora, Mapping)
        or lora.get("class_type") != "MiniMaxH3TurboLoRA"
        or lora.get("inputs")
        != {
            "model": ["1", 0],
            "lora_name": LARRY_LORA,
            "strength": 1.0,
            "low_vram": False,
        }
        or not isinstance(sage, Mapping)
        or sage.get("class_type")
        != "MiniMaxH3MemoryEfficientSageAttentionPatch"
        or sage.get("inputs") != {"model": ["15", 0]}
        or not isinstance(turbo, Mapping)
        or turbo.get("class_type") != "MiniMaxH3TurboSampler"
        or turbo.get("inputs") != {}
        or graph["7"]["inputs"].get("model") != ["16", 0]  # type: ignore[union-attr]
        or scheduler.get("model") != ["16", 0]
        or sampler.get("sampler") != ["17", 0]
        or classes & {
            "PathchSageAttentionKJ",
            "EasyCache",
            "MiniMaxH3TeaCache",
        }
    ):
        _fail("graph_contract_violation", "Larry graph model chain is invalid.")


def _tutorial_lora_pin(path: Path) -> ArtifactSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assets = raw["model_assets"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RunnerError(
            "tutorial_manifest_invalid", "Tutorial model lock could not be read."
        ) from error
    if not isinstance(assets, list):
        _fail("tutorial_manifest_invalid", "Tutorial model lock is invalid.")
    matches = [
        item
        for item in assets
        if isinstance(item, dict) and item.get("id") == LARRY_LORA_ID
    ]
    if len(matches) != 1:
        _fail("tutorial_manifest_invalid", "Larry 600EMA lock entry is missing.")
    item = matches[0]
    destination = item.get("destination")
    expected_bytes = item.get("bytes")
    sha256 = item.get("sha256")
    if (
        destination != LARRY_LORA_DESTINATION
        or type(expected_bytes) is not int
        or expected_bytes <= 0
        or not isinstance(sha256, str)
        or SHA256_RE.fullmatch(sha256) is None
        or PurePosixPath(destination).is_absolute()
        or ".." in PurePosixPath(destination).parts
    ):
        _fail("tutorial_manifest_invalid", "Larry 600EMA lock entry is invalid.")
    return ArtifactSpec(
        remote_path=destination,
        destination=destination,
        expected_bytes=expected_bytes,
        sha256=sha256,
        families=("larry",),
    )


def _tutorial_qrusher_pin(path: Path) -> ArtifactSpec:
    """Load the exact non-T8 Qrusher checkpoint used by the A/B profile."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assets = raw["model_assets"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RunnerError(
            "tutorial_manifest_invalid", "Tutorial model lock could not be read."
        ) from error
    if not isinstance(assets, list):
        _fail("tutorial_manifest_invalid", "Tutorial model lock is invalid.")
    matches = [
        item
        for item in assets
        if isinstance(item, dict) and item.get("id") == QRUSHER_MODEL_ID
    ]
    if len(matches) != 1:
        _fail("tutorial_manifest_invalid", "Qrusher 10Eros lock entry is missing.")
    item = matches[0]
    if (
        item.get("source") != QRUSHER_SOURCE
        or item.get("source_path") != Path(QRUSHER_MODEL).name
        or item.get("destination") != QRUSHER_MODEL_DESTINATION
        or item.get("bytes") != QRUSHER_EXPECTED_BYTES
        or item.get("sha256") != QRUSHER_SHA256
    ):
        _fail("tutorial_manifest_invalid", "Qrusher 10Eros lock entry is invalid.")
    return ArtifactSpec(
        remote_path=Path(QRUSHER_MODEL).name,
        destination=QRUSHER_MODEL_DESTINATION,
        expected_bytes=QRUSHER_EXPECTED_BYTES,
        sha256=QRUSHER_SHA256,
        families=("comparison",),
    )


def _tutorial_blackwell_fl_pin(path: Path) -> ArtifactSpec:
    """Load the exact optional Blackwell FL checkpoint; never download it."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assets = raw["model_assets"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RunnerError(
            "tutorial_manifest_invalid", "Tutorial model lock could not be read."
        ) from error
    if not isinstance(assets, list):
        _fail("tutorial_manifest_invalid", "Tutorial model lock is invalid.")
    matches = [
        item
        for item in assets
        if isinstance(item, dict) and item.get("id") == BLACKWELL_FL_MODEL_ID
    ]
    if len(matches) != 1:
        _fail(
            "tutorial_manifest_invalid",
            "Article Blackwell FL NVFP4 lock entry is missing.",
        )
    item = matches[0]
    if (
        item.get("source") != BLACKWELL_FL_SOURCE
        or item.get("source_path") != Path(BLACKWELL_FL_MODEL).name
        or item.get("destination") != BLACKWELL_FL_MODEL_DESTINATION
        or item.get("bytes") != BLACKWELL_FL_EXPECTED_BYTES
        or item.get("sha256") != BLACKWELL_FL_SHA256
    ):
        _fail(
            "tutorial_manifest_invalid",
            "Article Blackwell FL NVFP4 lock entry is invalid.",
        )
    return ArtifactSpec(
        remote_path=Path(BLACKWELL_FL_MODEL).name,
        destination=BLACKWELL_FL_MODEL_DESTINATION,
        expected_bytes=BLACKWELL_FL_EXPECTED_BYTES,
        sha256=BLACKWELL_FL_SHA256,
        families=("article_blackwell",),
    )


def verify_profile_models(
    models_dir: Path,
    *,
    profile: str,
    baseline_manifest_path: Path = DEFAULT_BASELINE_MANIFEST,
    tutorial_manifest_path: Path = DEFAULT_TUTORIAL_MANIFEST,
    verifier: Callable[[Path, ArtifactSpec], bool] = verify_file,
) -> list[dict[str, object]]:
    """Verify exact byte counts and SHA256 for every model used by the graph."""

    if profile not in PROFILES:
        _fail("profile_invalid", "Tutorial profile is invalid.")
    try:
        manifest = load_manifest(baseline_manifest_path)
        selected = list(select_family(manifest.artifacts, "fl"))
    except Exception as error:
        raise RunnerError(
            "model_manifest_invalid", "Baseline H3 model lock could not be read."
        ) from error
    expected = {
        f"diffusion_models/{native.FL2VA_MODEL}",
        f"text_encoders/{native.TEXT_ENCODER}",
        f"vae/{native.VIDEO_VAE}",
        f"vae/{native.AUDIO_VAE}",
    }
    if {artifact.destination for artifact in selected} != expected:
        _fail("model_manifest_invalid", "Baseline FL2VA model scope is invalid.")
    if profile == LARRY_PROFILE:
        selected.append(_tutorial_lora_pin(tutorial_manifest_path))
    elif profile == QRUSHER_PROFILE:
        selected = [
            artifact
            for artifact in selected
            if artifact.destination != f"diffusion_models/{native.FL2VA_MODEL}"
        ]
        selected.append(_tutorial_qrusher_pin(tutorial_manifest_path))
    elif profile == BLACKWELL_FL_PROFILE:
        selected = [
            artifact
            for artifact in selected
            if artifact.destination != f"diffusion_models/{native.FL2VA_MODEL}"
        ]
        selected.append(_tutorial_blackwell_fl_pin(tutorial_manifest_path))

    evidence: list[dict[str, object]] = []
    for artifact in selected:
        try:
            _root, path = artifact_destination(models_dir, artifact.destination)
            accepted = verifier(path, artifact)
        except Exception as error:
            raise RunnerError(
                "model_verification_failed", "A pinned H3 model could not be verified."
            ) from error
        if not accepted:
            _fail(
                "model_verification_failed",
                "A pinned H3 model failed byte-count or SHA256 verification.",
            )
        evidence.append(
            {
                "destination": artifact.destination,
                "bytes": artifact.expected_bytes,
                "sha256": artifact.sha256,
            }
        )
    return evidence


def _input_schema(node_info: object) -> tuple[Mapping[str, object], Mapping[str, object]]:
    if not isinstance(node_info, Mapping):
        _fail("node_schema_invalid", "ComfyUI node metadata is invalid.")
    input_info = node_info.get("input")
    if not isinstance(input_info, Mapping):
        _fail("node_schema_invalid", "ComfyUI node metadata has no input schema.")
    required = input_info.get("required", {})
    optional = input_info.get("optional", {})
    if not isinstance(required, Mapping) or not isinstance(optional, Mapping):
        _fail("node_schema_invalid", "ComfyUI node input schema is invalid.")
    return required, optional


def _enum_options(field_schema: object) -> list[object] | None:
    if not isinstance(field_schema, (list, tuple)) or not field_schema:
        return None
    if isinstance(field_schema[0], list):
        return field_schema[0]
    metadata = field_schema[1] if len(field_schema) > 1 else None
    options = metadata.get("options") if isinstance(metadata, Mapping) else None
    if isinstance(options, list):
        if all(
            isinstance(item, Mapping) and isinstance(item.get("key"), str)
            for item in options
        ):
            return [item["key"] for item in options]
        return options
    return None


def _expanded_input_schema(
    required: Mapping[str, object],
    optional: Mapping[str, object],
    inputs: Mapping[str, object],
) -> tuple[dict[str, object], set[str]]:
    """Expand ComfyUI v3 dynamic-combo child inputs such as codec.*."""

    fields = dict(required)
    fields.update(optional)
    required_names = set(required)

    def expand(name: str, schema: object) -> None:
        if not isinstance(schema, (list, tuple)) or len(schema) < 2:
            return
        metadata = schema[1]
        options = metadata.get("options") if isinstance(metadata, Mapping) else None
        selected = inputs.get(name)
        if not isinstance(options, list):
            return
        matching = [
            option
            for option in options
            if isinstance(option, Mapping) and option.get("key") == selected
        ]
        if not matching:
            return
        child_inputs = matching[0].get("inputs")
        if not isinstance(child_inputs, Mapping):
            return
        child_required = child_inputs.get("required", {})
        child_optional = child_inputs.get("optional", {})
        if not isinstance(child_required, Mapping) or not isinstance(
            child_optional, Mapping
        ):
            _fail("node_schema_invalid", "ComfyUI dynamic input schema is invalid.")
        for child_name, child_schema in child_required.items():
            full_name = f"{name}.{child_name}"
            fields[full_name] = child_schema
            required_names.add(full_name)
            expand(full_name, child_schema)
        for child_name, child_schema in child_optional.items():
            full_name = f"{name}.{child_name}"
            fields[full_name] = child_schema
            expand(full_name, child_schema)

    for field_name, field_schema in tuple(fields.items()):
        expand(field_name, field_schema)
    return fields, required_names


def verify_node_schemas(
    object_info: Mapping[str, object],
    graph: Mapping[str, Mapping[str, object]],
    *,
    profile: str,
) -> dict[str, object]:
    """Bind graph fields and model choices to the live ``/object_info`` ABI."""

    validate_graph_contract(graph, profile=profile)
    class_names = sorted(
        {
            str(node.get("class_type") or "")
            for node in graph.values()
            if isinstance(node, Mapping)
        }
    )
    for class_name in class_names:
        if class_name not in object_info:
            _fail("node_missing", f"ComfyUI is missing required node {class_name}.")

    for node in graph.values():
        class_name = str(node.get("class_type") or "")
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            _fail("graph_invalid", "Tutorial graph node inputs are invalid.")
        required, optional = _input_schema(object_info[class_name])
        fields, required_names = _expanded_input_schema(required, optional, inputs)
        unknown = set(inputs) - set(fields)
        missing = required_names - set(inputs)
        if unknown or missing:
            _fail(
                "node_schema_mismatch",
                f"Live {class_name} schema does not match the tutorial graph.",
            )
        for name, value in inputs.items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                continue
            options = _enum_options(fields.get(name))
            if options is not None and value not in options:
                _fail(
                    "node_option_missing",
                    f"Live {class_name} schema does not expose the pinned {name} option.",
                )

    exact_bindings = [
        ("UNETLoader", "unet_name", PROFILES[profile].unet_name),
        ("CLIPLoader", "clip_name", native.TEXT_ENCODER),
        ("VAELoader", "vae_name", native.VIDEO_VAE),
        ("VAELoader", "vae_name", native.AUDIO_VAE),
    ]
    if profile == LARRY_PROFILE:
        exact_bindings.append(("MiniMaxH3TurboLoRA", "lora_name", LARRY_LORA))
    for class_name, field, expected in exact_bindings:
        required, optional = _input_schema(object_info[class_name])
        schema = (required | optional).get(field)
        options = _enum_options(schema)
        if options is None or expected not in options:
            _fail(
                "model_not_indexed",
                f"ComfyUI has not indexed the pinned {class_name} asset.",
            )
    schema_scope = {class_name: object_info[class_name] for class_name in class_names}
    return {
        "classes": class_names,
        "class_count": len(class_names),
        "schema_sha256": _sha256_json(schema_scope),
    }


def read_vram_used_mib(
    *,
    gpu_index: int = 0,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    if type(gpu_index) is not int or gpu_index < 0:
        _fail("gpu_index_invalid", "GPU index is invalid.")
    try:
        completed = run_command(
            [
                "nvidia-smi",
                f"--id={gpu_index}",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerError("vram_probe_failed", "GPU VRAM status is unavailable.") from error
    raw = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9]+", raw) is None:
        _fail("vram_probe_failed", "GPU VRAM status is invalid.")
    return int(raw)


def _raise_if_cancelled(cancel_file: Path | None) -> None:
    if cancel_file is None:
        return
    try:
        info = cancel_file.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise RunnerError(
            "run_cancelled", "MiniMAX H3 tutorial validation was cancelled."
        ) from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        _fail("run_cancelled", "MiniMAX H3 tutorial validation was cancelled.")
    _fail("run_cancelled", "MiniMAX H3 tutorial validation was cancelled.")


def wait_for_history_with_vram(
    base_url: str,
    prompt_id: str,
    *,
    timeout_seconds: int,
    initial_vram_mib: int,
    http: Callable[..., Mapping[str, Any]] = guarded.request_json,
    vram_reader: Callable[[], int] = read_vram_used_mib,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    cancel_file: Path | None = None,
) -> tuple[Mapping[str, Any], int]:
    deadline = monotonic() + timeout_seconds
    peak = initial_vram_mib
    path = "/history/" + urllib.parse.quote(prompt_id, safe="")
    while True:
        _raise_if_cancelled(cancel_file)
        peak = max(peak, vram_reader())
        history = http(base_url, path, timeout=10.0)
        item = history.get(prompt_id)
        if item is not None:
            if not isinstance(item, Mapping):
                _fail("history_invalid", "ComfyUI history item is invalid.")
            status_value = item.get("status")
            if not isinstance(status_value, Mapping):
                _fail("history_invalid", "ComfyUI history status is invalid.")
            completed = status_value.get("completed")
            if completed is True:
                peak = max(peak, vram_reader())
                if status_value.get("status_str") != "success":
                    _fail("generation_failed", "ComfyUI reported H3 generation failure.")
                return item, peak
            if completed not in {None, False}:
                _fail("history_invalid", "ComfyUI history completion state is invalid.")
        remaining = deadline - monotonic()
        if remaining <= 0:
            _fail("run_timeout", "MiniMAX H3 tutorial validation timed out.")
        sleeper(min(POLL_SECONDS, remaining))


def bind_exact_output(
    history_item: Mapping[str, Any],
    *,
    profile: str,
    run_id: str,
    output_root: Path,
) -> OutputBinding:
    outputs = history_item.get("outputs")
    save_output = outputs.get(SAVE_NODE_ID) if isinstance(outputs, Mapping) else None
    candidates = save_output.get("images") if isinstance(save_output, Mapping) else None
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or not isinstance(candidates[0], Mapping)
    ):
        _fail("output_metadata_invalid", "H3 must return exactly one saved MP4.")
    metadata = candidates[0]
    filename = metadata.get("filename")
    subfolder = metadata.get("subfolder")
    output_type = metadata.get("type")
    if (
        not isinstance(filename, str)
        or SAFE_FILENAME_RE.fullmatch(filename) is None
        or re.fullmatch(re.escape(run_id) + r"_[0-9]{5}_\.mp4", filename) is None
        or subfolder != _output_subfolder(profile)
        or output_type != "output"
    ):
        _fail("output_binding_failed", "H3 output metadata left its exact namespace.")
    try:
        root = output_root.expanduser().resolve(strict=True)
        candidate = root.joinpath(*PurePosixPath(subfolder).parts, filename)
        info = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise RunnerError("output_missing", "H3 output MP4 is missing.") from error
    try:
        contained = os.path.commonpath((str(resolved), str(root))) == str(root)
    except ValueError:
        contained = False
    if (
        not contained
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_size <= 0
    ):
        _fail("output_file_unsafe", "H3 output MP4 is unsafe.")
    return OutputBinding(
        path=resolved,
        filename=filename,
        subfolder=str(subfolder),
        output_type=str(output_type),
    )


def stable_file_sha256(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RunnerError("output_hash_failed", "H3 output could not be hashed.") from error
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 8 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            size != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            _fail("output_changed", "H3 output changed while it was hashed.")
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _file_identity(path: Path) -> tuple[int, int, int, int, int, int, int, int]:
    info = path.lstat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def ffprobe_audio_video(
    path: Path,
    *,
    width: int,
    height: int,
    frames: int,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    """Require one H.264 video stream plus at least one audio stream."""
    return guarded.probe_output(
        path,
        resolution=(width, height),
        frames=frames,
        run_command=run_command,
    )


def write_owner_receipt(
    state_root_value: str | os.PathLike[str],
    *,
    run_id: str,
    payload: Mapping[str, object],
) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        _fail("run_id_invalid", "Tutorial run identifier is invalid.")
    state_root = guarded._ensure_private_directory(state_root_value)
    receipts = guarded._ensure_private_directory(state_root / "receipts")
    path = receipts / f"{run_id}.json"
    exact = dict(payload)
    exact["schema_version"] = SCHEMA_VERSION
    exact["run_id"] = run_id
    guarded._write_exclusive_json(path, exact)
    try:
        info = path.stat()
    except OSError as error:
        raise RunnerError("receipt_write_failed", "Tutorial receipt is unavailable.") from error
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        _fail("receipt_write_failed", "Tutorial receipt is not owner-only.")
    return path


def sanitized_dry_run(
    *,
    graph: Mapping[str, Mapping[str, object]],
    profile: str,
    prompt: str,
    seed: int,
    run_id: str,
) -> dict[str, object]:
    spec = PROFILES[profile]
    classes: dict[str, int] = {}
    for node in graph.values():
        class_name = str(node.get("class_type") or "")
        classes[class_name] = classes.get(class_name, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "dry_run",
        "execution_performed": False,
        "profile": profile,
        "run_id": run_id,
        "seed": seed,
        "resolution": {
            "width": graph["5"]["inputs"]["width"],  # type: ignore[index]
            "height": graph["5"]["inputs"]["height"],  # type: ignore[index]
        },
        "frames": graph["5"]["inputs"]["length"],  # type: ignore[index]
        "fps": FPS,
        "steps": spec.steps,
        "prompt": {"sha256": _sha256_text(prompt), "characters": len(prompt)},
        "graph": {
            "sha256": _sha256_json(graph),
            "node_count": len(graph),
            "class_counts": dict(sorted(classes.items())),
        },
        "models": [
            spec.unet_name,
            native.TEXT_ENCODER,
            native.VIDEO_VAE,
            native.AUDIO_VAE,
            *([LARRY_LORA] if profile == LARRY_PROFILE else []),
        ],
        "output": {
            "subfolder": _output_subfolder(profile),
            "container": "mp4",
            "video_required": True,
            "audio_required": True,
        },
    }


def execute_profile(
    *,
    profile: str,
    graph: dict[str, dict[str, object]],
    prompt: str,
    seed: int,
    run_id: str,
    comfy_url: str,
    models_dir: Path,
    output_dir: Path,
    state_dir: Path,
    timeout_seconds: int,
    gpu_index: int,
    cleanup_requester: Callable[[str], None] | None = None,
    memory_reader: Callable[[], guarded.MemorySnapshot] = guarded.read_memory_snapshot,
    cancel_file: Path | None = None,
    queue_waiter: Callable[..., None] = guarded.wait_for_comfy_queue_idle,
) -> tuple[dict[str, object], Path, int]:
    """Preflight, submit, qualify, and receipt one local tutorial graph."""

    base_url = guarded.validate_loopback_url(comfy_url)
    created_at = _utc_now()
    prompt_id = str(uuid.uuid4())
    private_state = guarded._ensure_private_directory(state_dir)
    private_receipts = guarded._ensure_private_directory(private_state / "receipts")
    receipt_candidate = private_receipts / f"{run_id}.json"
    try:
        receipt_candidate.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RunnerError(
            "receipt_preflight_failed", "Tutorial receipt path is unavailable."
        ) from error
    else:
        _fail("state_collision", "Tutorial receipt already exists.")
    receipt: dict[str, object] = {
        "runner": "castle.minimax-h3-tutorial-profile.v1",
        "status": "failed",
        "error_code": None,
        "created_at": created_at,
        "finished_at": created_at,
        "profile": profile,
        "steps": PROFILES[profile].steps,
        "resolution": {
            "width": graph["5"]["inputs"]["width"],  # type: ignore[index]
            "height": graph["5"]["inputs"]["height"],  # type: ignore[index]
        },
        "frames": graph["5"]["inputs"]["length"],  # type: ignore[index]
        "fps": FPS,
        "seed": seed,
        "prompt_sha256": _sha256_text(prompt),
        "graph_sha256": _sha256_json(graph),
        "prompt_id": prompt_id,
        "submitted": False,
        "wall_seconds": None,
        "submission_wall_seconds": None,
        "vram": None,
        "preflight": None,
        "output": None,
        "cancel_action": None,
        "memory_cleanup": guarded.memory_cleanup_not_attempted(),
    }
    submitted = False
    submission_attempted = False
    started = time.monotonic()
    submission_started: float | None = None
    try:
        _raise_if_cancelled(cancel_file)
        listeners = guarded.verify_loopback_listener(base_url)
        guarded.wait_for_memory_headroom(memory_reader=memory_reader)
        model_evidence = verify_profile_models(models_dir, profile=profile)
        _raise_if_cancelled(cancel_file)
        object_info = guarded.request_json(base_url, "/object_info", timeout=30.0)
        node_evidence = verify_node_schemas(object_info, graph, profile=profile)
        guarded.require_memory_headroom(memory_reader())
        initial_vram = read_vram_used_mib(gpu_index=gpu_index)
        receipt["preflight"] = {
            "loopback_listeners": list(listeners),
            "models": model_evidence,
            "nodes": node_evidence,
        }

        submission_started = time.monotonic()
        _raise_if_cancelled(cancel_file)
        # From this assignment onward a transport failure may hide an
        # accepted POST, so cancellation and queue settlement are mandatory.
        submission_attempted = True
        guarded.submit_prompt(
            base_url,
            graph,
            client_id=f"castle-h3-tutorial-{secrets.token_hex(12)}",
            prompt_id=prompt_id,
        )
        submitted = True
        receipt["submitted"] = True
        history, peak_vram = wait_for_history_with_vram(
            base_url,
            prompt_id,
            timeout_seconds=timeout_seconds,
            initial_vram_mib=initial_vram,
            vram_reader=lambda: read_vram_used_mib(gpu_index=gpu_index),
            cancel_file=cancel_file,
        )
        binding = bind_exact_output(
            history,
            profile=profile,
            run_id=run_id,
            output_root=output_dir,
        )
        width = int(graph["5"]["inputs"]["width"])  # type: ignore[index]
        height = int(graph["5"]["inputs"]["height"])  # type: ignore[index]
        frames = int(graph["5"]["inputs"]["length"])  # type: ignore[index]
        output_identity = _file_identity(binding.path)
        probe = ffprobe_audio_video(
            binding.path,
            width=width,
            height=height,
            frames=frames,
        )
        size, digest = stable_file_sha256(binding.path)
        if _file_identity(binding.path) != output_identity:
            _fail("output_changed", "H3 output changed during qualification.")
        receipt["vram"] = {
            "device_initial_used_mib": initial_vram,
            "device_sampled_peak_used_mib": peak_vram,
            "device_sampled_peak_delta_mib": max(0, peak_vram - initial_vram),
            "sampling_interval_seconds": POLL_SECONDS,
        }
        receipt["output"] = {
            "path": str(binding.path),
            "filename": binding.filename,
            "subfolder": binding.subfolder,
            "type": binding.output_type,
            "bytes": size,
            "sha256": digest,
            "probe": probe,
        }
        receipt["status"] = "completed"
    except KeyboardInterrupt:
        if submission_attempted:
            receipt["cancel_action"] = guarded.cancel_owned_prompt(base_url, prompt_id)
        receipt["error_code"] = "run_interrupted"
    except RunnerError as error:
        if submission_attempted:
            receipt["cancel_action"] = guarded.cancel_owned_prompt(base_url, prompt_id)
        receipt["error_code"] = error.code
    except Exception:
        if submission_attempted:
            receipt["cancel_action"] = guarded.cancel_owned_prompt(base_url, prompt_id)
        receipt["error_code"] = "runner_failed"
    finally:
        if submission_attempted:
            queue_waiter(base_url, timeout_seconds=None)
            receipt["memory_cleanup"] = guarded.release_comfy_memory(
                base_url,
                requester=cleanup_requester,
            )
        receipt["wall_seconds"] = round(time.monotonic() - started, 3)
        if submission_started is not None:
            receipt["submission_wall_seconds"] = round(
                time.monotonic() - submission_started,
                3,
            )
        receipt["finished_at"] = _utc_now()
    receipt_path = write_owner_receipt(
        state_dir,
        run_id=run_id,
        payload=receipt,
    )
    return receipt, receipt_path, 0 if receipt["status"] == "completed" else 2


def gpu_queue_command(queue_script: Path, spec_path: Path) -> list[str]:
    """Build the prompt-free shared-GPU queue command."""

    return [
        sys.executable,
        str(queue_script),
        "--min-free-mb",
        str(guarded.GPU_MIN_FREE_MIB),
        "--max-util",
        str(guarded.GPU_MAX_UTIL_PERCENT),
        "--interval",
        str(guarded.GPU_QUEUE_INTERVAL_SECONDS),
        "--comfy-url",
        "",
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        WORKER_SPEC_ARGUMENT,
        str(spec_path),
    ]


def _tutorial_worker_environment(*, token: str, state_root: Path) -> dict[str, str]:
    environment = guarded._worker_environment(token=token, state_root=state_root)
    environment[WORKER_TOKEN_ENV] = environment.pop("CASTLE_H3_WORKER_TOKEN")
    environment[WORKER_STATE_ENV] = environment.pop("CASTLE_H3_WORKER_STATE_ROOT")
    return environment


def _mark_cancel_requested(path: Path) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return
    except OSError:
        return
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, b"cancel\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute_via_gpu_queue(
    *,
    spec: Mapping[str, object],
    state_root: Path,
    queue_script: Path,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    environment_builder: Callable[..., dict[str, str]] = _tutorial_worker_environment,
) -> int:
    """Run the signed worker while the existing queue owns its shared flock.

    There is deliberately no outer subprocess timeout.  The worker owns the
    generation timeout, cancellation, prompt interruption, qualification, and
    controlled ComfyUI recycle.  Waiting for its exit means the queue wrapper
    cannot release the flock during that lifecycle; the signed specification
    also reserves at least ``MIN_LIFECYCLE_GRACE_SECONDS`` beyond generation.
    """

    safe_queue_script = guarded._safe_queue_script(queue_script)
    private_state = guarded._ensure_private_directory(state_root)
    pending = guarded._ensure_private_directory(private_state / "pending")
    run_id = spec.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        _fail("run_id_invalid", "Tutorial run identifier is invalid.")
    spec_path = pending / f"{run_id}.json"
    cancel_path = pending / f"{run_id}.cancel"
    try:
        cancel_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RunnerError(
            "state_preflight_failed", "Tutorial queue state is unavailable."
        ) from error
    else:
        _fail("state_collision", "Tutorial queue state already exists.")

    token = secrets.token_hex(32)
    stored = dict(spec)
    stored["cancel_file"] = str(cancel_path)
    stored["lifecycle_grace_seconds"] = LIFECYCLE_GRACE_SECONDS
    stored["worker_hmac_sha256"] = guarded._worker_spec_hmac(stored, token)
    guarded._write_exclusive_json(spec_path, stored)
    process: subprocess.Popen[Any] | None = None
    previous_handlers: dict[int, Any] = {}

    def request_cancel(_signum: int, _frame: object) -> None:
        _mark_cancel_requested(cancel_path)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_cancel)
            except ValueError:
                previous_handlers.clear()
                break
        try:
            process = popen(
                gpu_queue_command(safe_queue_script, spec_path),
                env=environment_builder(token=token, state_root=private_state),
                cwd=str(REPO_ROOT),
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, ValueError) as error:
            raise RunnerError(
                "gpu_queue_failed", "Castle GPU queue could not start."
            ) from error
        while True:
            try:
                return int(process.wait())
            except KeyboardInterrupt:
                # A non-main-thread caller may not have installed our scoped
                # handlers.  Request cancellation, then continue waiting so
                # the queue keeps its flock through worker cleanup/recycle.
                _mark_cancel_requested(cancel_path)
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass
        for path in (spec_path, cancel_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _validate_tutorial_worker_spec(
    payload: object,
    *,
    expected_state_root: Path,
    token: str,
) -> tuple[dict[str, Any], dict[str, dict[str, object]]]:
    required = {
        "schema_version",
        "run_id",
        "profile",
        "prompt",
        "seed",
        "width",
        "height",
        "frames",
        "graph_sha256",
        "comfy_url",
        "models_dir",
        "output_dir",
        "state_root",
        "timeout_seconds",
        "gpu_index",
        "cancel_file",
        "lifecycle_grace_seconds",
        "worker_hmac_sha256",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") != WORKER_SPEC_SCHEMA_VERSION
    ):
        _fail("worker_spec_invalid", "Tutorial worker specification is invalid.")
    signature = payload.get("worker_hmac_sha256")
    if (
        not isinstance(signature, str)
        or SHA256_RE.fullmatch(signature) is None
        or not secrets.compare_digest(
            signature, guarded._worker_spec_hmac(payload, token)
        )
    ):
        _fail(
            "worker_spec_authentication_failed",
            "Tutorial worker specification is not authentic.",
        )
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        _fail("worker_spec_invalid", "Tutorial worker run identifier is invalid.")
    if payload.get("state_root") != str(expected_state_root):
        _fail("worker_spec_invalid", "Tutorial worker state root is invalid.")
    if payload.get("profile") not in PROFILES:
        _fail("worker_spec_invalid", "Tutorial worker profile is invalid.")
    prompt = _clean_prompt(payload.get("prompt"))
    if prompt != payload.get("prompt"):
        _fail("worker_spec_invalid", "Tutorial worker prompt is invalid.")
    timeout_seconds = payload.get("timeout_seconds")
    gpu_index = payload.get("gpu_index")
    grace = payload.get("lifecycle_grace_seconds")
    if (
        type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 24 * 60 * 60
        or type(gpu_index) is not int
        or gpu_index < 0
        or type(grace) is not int
        or grace < MIN_LIFECYCLE_GRACE_SECONDS
    ):
        _fail("worker_spec_invalid", "Tutorial worker limits are invalid.")
    for key in ("models_dir", "output_dir"):
        value = payload.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            _fail("worker_spec_invalid", "Tutorial worker path is invalid.")
    base_url = guarded.validate_loopback_url(payload.get("comfy_url"))
    if base_url != payload.get("comfy_url"):
        _fail("worker_spec_invalid", "Tutorial worker ComfyUI URL is invalid.")
    cancel_path = expected_state_root / "pending" / f"{run_id}.cancel"
    if payload.get("cancel_file") != str(cancel_path):
        _fail("worker_spec_invalid", "Tutorial worker cancellation path is invalid.")
    graph = build_tutorial_graph(
        profile=str(payload["profile"]),
        prompt=prompt,
        seed=payload.get("seed"),
        run_id=run_id,
        width=payload.get("width"),
        height=payload.get("height"),
        frames=payload.get("frames"),
    )
    graph_sha256 = payload.get("graph_sha256")
    if (
        not isinstance(graph_sha256, str)
        or SHA256_RE.fullmatch(graph_sha256) is None
        or not secrets.compare_digest(graph_sha256, _sha256_json(graph))
    ):
        _fail("worker_graph_mismatch", "Tutorial worker graph is invalid.")
    return payload, graph


def _print_execute_result(
    receipt: Mapping[str, object], receipt_path: Path, *, run_id: str
) -> None:
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "error_code": receipt["error_code"],
                "run_id": run_id,
                "prompt_id": receipt["prompt_id"],
                "receipt": str(receipt_path),
                "output": receipt["output"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _tutorial_worker_entry(spec_path_value: str) -> int:
    token = os.environ.pop(WORKER_TOKEN_ENV, "")
    state_value = os.environ.pop(WORKER_STATE_ENV, "")
    if not token or not state_value or not token.isascii():
        print("[error] worker_context_missing", file=sys.stderr)
        return 2
    try:
        state_root = guarded._ensure_private_directory(state_value)
        pending = guarded._ensure_private_directory(state_root / "pending")
        spec_path = Path(os.path.abspath(spec_path_value))
        if spec_path.parent != pending or not spec_path.name.endswith(".json"):
            _fail("worker_spec_path_invalid", "Tutorial worker path is invalid.")
        raw = guarded._read_owner_file(
            spec_path,
            max_bytes=guarded.MAX_SPEC_BYTES,
            code="worker_spec_invalid",
        )
        payload = guarded._safe_json_loads(raw, code="worker_spec_invalid")
        spec, graph = _validate_tutorial_worker_spec(
            payload,
            expected_state_root=state_root,
            token=token,
        )
        if spec_path.name != f"{spec['run_id']}.json":
            _fail("worker_spec_path_invalid", "Tutorial worker path is invalid.")
        try:
            spec_path.unlink()
        except OSError as error:
            raise RunnerError(
                "worker_spec_cleanup_failed",
                "Tutorial worker specification could not be consumed.",
            ) from error
        receipt, receipt_path, code = execute_profile(
            profile=str(spec["profile"]),
            graph=graph,
            prompt=str(spec["prompt"]),
            seed=int(spec["seed"]),
            run_id=str(spec["run_id"]),
            comfy_url=str(spec["comfy_url"]),
            models_dir=Path(str(spec["models_dir"])),
            output_dir=Path(str(spec["output_dir"])),
            state_dir=state_root,
            timeout_seconds=int(spec["timeout_seconds"]),
            gpu_index=int(spec["gpu_index"]),
            cancel_file=Path(str(spec["cancel_file"])),
        )
        _print_execute_result(receipt, receipt_path, run_id=str(spec["run_id"]))
        return code
    except RunnerError as error:
        print(f"[error] {error.code}: {error}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--dry-run",
        action="store_true",
        help="print the sanitized graph contract without side effects (default)",
    )
    action.add_argument(
        "--execute",
        action="store_true",
        help="verify and submit one graph to loopback ComfyUI",
    )
    parser.add_argument("--profile", choices=PROFILE_NAMES, default=STOCK_PROFILE)
    parser.add_argument("--prompt", required=True, help="MiniMAX H3 prompt text")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--comfy-url", default=DEFAULT_COMFY_URL)
    parser.add_argument("--comfy-dir", default=str(DEFAULT_COMFY_DIR))
    parser.add_argument("--models-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_ROOT))
    parser.add_argument(
        "--gpu-queue-script",
        default=str(DEFAULT_GPU_QUEUE_SCRIPT),
        help="owner-controlled shared GPU queue wrapper",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if len(raw_argv) == 2 and raw_argv[0] == WORKER_SPEC_ARGUMENT:
        return _tutorial_worker_entry(raw_argv[1])
    args = build_parser().parse_args(raw_argv)
    try:
        if not 1 <= args.timeout_seconds <= 24 * 60 * 60:
            _fail("timeout_invalid", "Timeout must be between one second and one day.")
        prompt = _clean_prompt(args.prompt)
        seed = args.seed if args.seed is not None else secrets.randbits(64)
        run_id = _new_run_id()
        graph = build_tutorial_graph(
            profile=args.profile,
            prompt=prompt,
            seed=seed,
            run_id=run_id,
            width=args.width,
            height=args.height,
            frames=args.frames,
        )
        if not args.execute:
            print(
                json.dumps(
                    sanitized_dry_run(
                        graph=graph,
                        profile=args.profile,
                        prompt=prompt,
                        seed=seed,
                        run_id=run_id,
                    ),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        comfy_dir = Path(args.comfy_dir).expanduser().resolve(strict=False)
        models_dir = (
            Path(args.models_dir).expanduser().resolve(strict=False)
            if args.models_dir
            else comfy_dir / "models"
        )
        output_dir = (
            Path(args.output_dir).expanduser().resolve(strict=False)
            if args.output_dir
            else comfy_dir / "output"
        )
        state_root = guarded._ensure_private_directory(args.state_dir)
        queue_script = guarded._safe_queue_script(args.gpu_queue_script)
        base_url = guarded.validate_loopback_url(args.comfy_url)
        spec: dict[str, object] = {
            "schema_version": WORKER_SPEC_SCHEMA_VERSION,
            "run_id": run_id,
            "profile": args.profile,
            "prompt": prompt,
            "seed": seed,
            "width": graph["5"]["inputs"]["width"],  # type: ignore[index]
            "height": graph["5"]["inputs"]["height"],  # type: ignore[index]
            "frames": graph["5"]["inputs"]["length"],  # type: ignore[index]
            "graph_sha256": _sha256_json(graph),
            "comfy_url": base_url,
            "models_dir": str(models_dir.resolve(strict=False)),
            "output_dir": str(output_dir.resolve(strict=False)),
            "state_root": str(state_root),
            "timeout_seconds": args.timeout_seconds,
            "gpu_index": args.gpu_index,
        }
        return execute_via_gpu_queue(
            spec=spec,
            state_root=state_root,
            queue_script=queue_script,
        )
    except RunnerError as error:
        print(f"[error] {error.code}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
