"""Fail-closed, side-effect-free builders for native MiniMax H3 prompts.

The builders only produce ComfyUI API-format prompt graphs.  They implement the
official FL2VA and Ref2VA input surfaces documented in Servasyy's H3 guide:
native/preview canvases, every trained 17k+5 duration from 124 through 362
frames, first and/or last-frame I2V, and Ref2VA with up to nine pictures, three
24-fps videos (including their paired audio), and three standalone audio clips.

The stock graph remains the default.  The three independently qualified
Servasyy acceleration profiles are explicit options so a request never silently
changes the sampling graph.
"""

from __future__ import annotations

import re
from typing import Final, TypeAlias


PromptGraph: TypeAlias = dict[str, dict[str, object]]

FL2VA_MODEL: Final = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
REF2VA_MODEL: Final = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TEXT_ENCODER: Final = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE: Final = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE: Final = "minimax_h3_audio_vae_fp32.safetensors"

STOCK_STEPS: Final = 20
STOCK_SCHEDULER: Final = "simple"
STOCK_SAMPLER: Final = "res_multistep"
STOCK_FPS: Final = 24.0
STOCK_BIT_DEPTH: Final = 8
STOCK_CRF: Final = 23.0

MAX_PROMPT_CHARS: Final = 24_000
MAX_MEDIA_BASENAME_CHARS: Final = 128
MAX_OUTPUT_TOKEN_CHARS: Final = 96
UINT64_MAX: Final = (1 << 64) - 1

ALLOWED_RESOLUTIONS: Final = frozenset(
    {
        (1344, 768),
        (768, 1344),
        (992, 992),
        (864, 480),
        (480, 864),
        (640, 640),
    }
)
LEGAL_FRAME_LENGTHS: Final = tuple(range(124, 363, 17))
DURATION_PROFILES: Final = {
    # Compatibility aliases for existing internal qualification callers.
    "smoke": 22,
    "quality": 124,
    **{f"frames-{frames}": frames for frames in LEGAL_FRAME_LENGTHS},
}
ACCELERATION_PROFILES: Final = frozenset(
    {"stock", "sage", "easycache", "teacache"}
)

_MODES: Final = frozenset({"t2v", "i2v", "ref2va_image"})
_IMAGE_EXTENSIONS: Final = frozenset({"png", "jpg", "jpeg", "webp"})
_VIDEO_EXTENSIONS: Final = frozenset({"mp4", "mov", "webm", "mkv"})
_AUDIO_EXTENSIONS: Final = frozenset({"wav", "mp3", "flac", "m4a", "ogg", "aac"})
_SAFE_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SAFE_OUTPUT_TOKEN = re.compile(
    rf"[A-Za-z0-9](?:[A-Za-z0-9._-]{{0,{MAX_OUTPUT_TOKEN_CHARS - 2}}}"
    r"[A-Za-z0-9])?"
)
_ALLOWED_PROMPT_CONTROLS: Final = frozenset({"\t", "\n", "\r"})
_REFERENCE_TAG = re.compile(
    r"<\s*(Picture|Audio|Video|Subject)\s+(\d+)\s*>", re.IGNORECASE
)
_REFERENCE_TAG_LIKE = re.compile(
    r"<\s*(?:Picture|Audio|Video|Subject)\b[^>]*(?:>|$)", re.IGNORECASE
)


class MiniMaxH3PromptError(ValueError):
    """A stable, sanitized validation failure from an H3 prompt builder."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise MiniMaxH3PromptError(code, message)


def _validate_prompt(value: object) -> str:
    if type(value) is not str:
        _fail("prompt_invalid", "MiniMax H3 prompt must be text.")
    prompt = value.strip()
    if not prompt:
        _fail("prompt_invalid", "MiniMax H3 prompt must not be blank.")
    if len(prompt) > MAX_PROMPT_CHARS:
        _fail("prompt_too_long", "MiniMax H3 prompt exceeds the allowed length.")
    if any(
        (ord(character) < 32 and character not in _ALLOWED_PROMPT_CONTROLS)
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in prompt
    ):
        _fail("prompt_invalid", "MiniMax H3 prompt contains unsupported characters.")
    return prompt


def _validate_reference_tags(
    prompt: str,
    *,
    mode: str,
    pictures: int = 0,
    videos: int = 0,
    audios: int = 0,
) -> None:
    """Keep numbered tags bound to materialized graph inputs."""

    exact_matches = list(_REFERENCE_TAG.finditer(prompt))
    tag_like_matches = list(_REFERENCE_TAG_LIKE.finditer(prompt))
    if [match.span() for match in exact_matches] != [match.span() for match in tag_like_matches]:
        _fail("reference_tag_invalid", "MiniMax H3 prompt has a malformed reference tag.")
    tags = [(match.group(1).casefold(), int(match.group(2))) for match in exact_matches]
    if any(index < 1 for _, index in tags):
        _fail("reference_tag_invalid", "MiniMax H3 reference indexes start at one.")
    if mode == "t2v":
        if tags:
            _fail("reference_tag_invalid", "Text-to-video cannot use reference tags.")
        return
    if mode == "i2v":
        if any(kind != "picture" or index > pictures for kind, index in tags):
            _fail("reference_tag_invalid", "I2V prompt references an unavailable picture.")
        return
    limits = {"picture": pictures, "video": videos, "audio": audios}
    for kind, index in tags:
        if kind == "subject":
            if index > 99:
                _fail("reference_tag_invalid", "MiniMax H3 subject index is invalid.")
        elif index > limits[kind]:
            _fail("reference_tag_invalid", "Ref2VA prompt references unavailable media.")


def _validate_resolution(value: object) -> tuple[int, int]:
    if (
        type(value) is not tuple
        or len(value) != 2
        or any(type(dimension) is not int for dimension in value)
        or value not in ALLOWED_RESOLUTIONS
    ):
        _fail("resolution_invalid", "MiniMax H3 resolution is not an allowed profile.")
    return value


def _validate_duration(*, profile: object | None, frames: object | None) -> int:
    if profile is not None and frames is not None:
        _fail("duration_profile_invalid", "Choose either an H3 duration profile or frames.")
    if frames is not None:
        if type(frames) is not int or frames not in LEGAL_FRAME_LENGTHS:
            _fail("duration_profile_invalid", "MiniMax H3 frames must use the trained 17k+5 grid.")
        return frames
    if type(profile) is not str or profile not in DURATION_PROFILES:
        _fail("duration_profile_invalid", "MiniMax H3 duration profile is invalid.")
    return DURATION_PROFILES[profile]


def _validate_acceleration(value: object) -> str:
    if type(value) is not str or value not in ACCELERATION_PROFILES:
        _fail("acceleration_invalid", "MiniMax H3 acceleration profile is invalid.")
    return value


def _validate_seed(value: object) -> int:
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        _fail("seed_invalid", "MiniMax H3 seed must be an unsigned 64-bit integer.")
    return value


def _validate_media_basename(
    value: object,
    *,
    field: str,
    extensions: frozenset[str],
    media_type: str,
) -> str:
    if type(value) is not str:
        _fail("media_name_invalid", f"{field} must be a materialized {media_type} basename.")
    if (
        not value
        or len(value) > MAX_MEDIA_BASENAME_CHARS
        or ".." in value
        or _SAFE_BASENAME.fullmatch(value) is None
    ):
        _fail("media_name_invalid", f"{field} must be a materialized {media_type} basename.")
    extension = value.rpartition(".")[2].lower()
    if not extension or extension not in extensions:
        _fail("media_name_invalid", f"{field} must use an allowed {media_type} extension.")
    return value


def _validate_image_basename(value: object, *, field: str) -> str:
    return _validate_media_basename(
        value, field=field, extensions=_IMAGE_EXTENSIONS, media_type="image"
    )


def _validate_media_sequence(
    value: object,
    *,
    field: str,
    maximum: int,
    extensions: frozenset[str],
    media_type: str,
) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > maximum:
        _fail("media_inputs_invalid", f"{field} exceeds the MiniMax H3 input limit.")
    return tuple(
        _validate_media_basename(
            item,
            field=f"{field}[{index}]",
            extensions=extensions,
            media_type=media_type,
        )
        for index, item in enumerate(value)
    )


def _validate_output_prefix(value: object, *, mode: str) -> str:
    if mode not in _MODES:
        _fail("mode_invalid", "MiniMax H3 mode is invalid.")
    if type(value) is not str:
        _fail("output_prefix_invalid", "MiniMax H3 output prefix is invalid.")
    root = f"Castle/minimax-h3/{mode}/"
    if not value.startswith(root):
        _fail("output_prefix_invalid", "MiniMax H3 output prefix is outside its mode namespace.")
    token = value[len(root) :]
    if (
        not token
        or len(token) > MAX_OUTPUT_TOKEN_CHARS
        or ".." in token
        or _SAFE_OUTPUT_TOKEN.fullmatch(token) is None
    ):
        _fail("output_prefix_invalid", "MiniMax H3 output prefix is invalid.")
    return value


def _build_graph(
    *,
    model_name: str,
    conditioning_type: str,
    conditioning_inputs: dict[str, object],
    seed: int,
    output_prefix: str,
) -> PromptGraph:
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": model_name,
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": TEXT_ENCODER,
                "type": "minimax",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VIDEO_VAE},
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": AUDIO_VAE},
        },
        "5": {
            "class_type": conditioning_type,
            "inputs": conditioning_inputs,
        },
        "6": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": seed},
        },
        "7": {
            "class_type": "BasicGuider",
            "inputs": {
                "model": ["1", 0],
                "conditioning": ["5", 0],
            },
        },
        "8": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": STOCK_SAMPLER},
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["1", 0],
                "scheduler": STOCK_SCHEDULER,
                "steps": STOCK_STEPS,
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
            "inputs": {
                "samples": ["10", 0],
                "vae": ["3", 0],
            },
        },
        "12": {
            "class_type": "VAEDecodeAudio",
            "inputs": {
                "samples": ["10", 0],
                "vae": ["4", 0],
            },
        },
        "13": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["11", 0],
                "audio": ["12", 0],
                "fps": STOCK_FPS,
                "bit_depth": STOCK_BIT_DEPTH,
            },
        },
        "14": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["13", 0],
                "filename_prefix": output_prefix,
                "format": "mp4",
                "codec": "h264",
                "codec.encoding": "re-encode",
                "codec.encoding.crf": STOCK_CRF,
            },
        },
    }


def _apply_acceleration(graph: PromptGraph, acceleration: str) -> PromptGraph:
    """Apply one of the three independently qualified Servasyy graph overlays."""

    if acceleration == "stock":
        return graph
    graph["90"] = {
        "class_type": "PathchSageAttentionKJ",
        "inputs": {
            "model": ["1", 0],
            "sage_attention": "auto",
            "allow_compile": False,
        },
    }
    model_node = "90"
    if acceleration == "easycache":
        graph["91"] = {
            "class_type": "EasyCache",
            "inputs": {
                "model": ["90", 0],
                "reuse_threshold": 0.1,
                "start_percent": 0.15,
                "end_percent": 0.95,
                "verbose": False,
            },
        }
        model_node = "91"
    elif acceleration == "teacache":
        graph["91"] = {
            "class_type": "MiniMaxH3TeaCache",
            "inputs": {
                "model": ["90", 0],
                "rel_l1_thresh": 0.15,
                "start_step": 2,
                "end_step": -2,
                "total_steps": STOCK_STEPS,
            },
        }
        model_node = "91"
    graph["7"]["inputs"]["model"] = [model_node, 0]  # type: ignore[index]
    graph["9"]["inputs"]["model"] = [model_node, 0]  # type: ignore[index]
    return graph


def _validated_common(
    *,
    prompt: object,
    resolution: object,
    profile: object | None,
    frames: object | None,
    seed: object,
    output_prefix: object,
    mode: str,
    acceleration: object,
) -> tuple[str, int, int, int, int, str, str]:
    clean_prompt = _validate_prompt(prompt)
    width, height = _validate_resolution(resolution)
    length = _validate_duration(profile=profile, frames=frames)
    clean_seed = _validate_seed(seed)
    clean_prefix = _validate_output_prefix(output_prefix, mode=mode)
    clean_acceleration = _validate_acceleration(acceleration)
    return (
        clean_prompt,
        width,
        height,
        length,
        clean_seed,
        clean_prefix,
        clean_acceleration,
    )


def build_t2v_prompt(
    *,
    prompt: object,
    resolution: object,
    seed: object,
    output_prefix: object,
    profile: object | None = None,
    frames: object | None = None,
    acceleration: object = "stock",
) -> PromptGraph:
    """Build an FL2VA text-to-video graph."""

    (
        clean_prompt,
        width,
        height,
        length,
        clean_seed,
        clean_prefix,
        clean_acceleration,
    ) = _validated_common(
        prompt=prompt,
        resolution=resolution,
        profile=profile,
        frames=frames,
        seed=seed,
        output_prefix=output_prefix,
        mode="t2v",
        acceleration=acceleration,
    )
    _validate_reference_tags(clean_prompt, mode="t2v")
    graph = _build_graph(
        model_name=FL2VA_MODEL,
        conditioning_type="MiniMaxH3ImageToVideo",
        conditioning_inputs={
            "clip": ["2", 0],
            "vae": ["3", 0],
            "prompt": clean_prompt,
            "width": width,
            "height": height,
            "length": length,
        },
        seed=clean_seed,
        output_prefix=clean_prefix,
    )
    return _apply_acceleration(graph, clean_acceleration)


def build_i2v_prompt(
    *,
    prompt: object,
    resolution: object,
    seed: object,
    output_prefix: object,
    first_frame: object | None = None,
    last_frame: object | None = None,
    profile: object | None = None,
    frames: object | None = None,
    acceleration: object = "stock",
) -> PromptGraph:
    """Build FL2VA I2V with a first frame, a last frame, or both."""

    (
        clean_prompt,
        width,
        height,
        length,
        clean_seed,
        clean_prefix,
        clean_acceleration,
    ) = _validated_common(
        prompt=prompt,
        resolution=resolution,
        profile=profile,
        frames=frames,
        seed=seed,
        output_prefix=output_prefix,
        mode="i2v",
        acceleration=acceleration,
    )
    if first_frame is None and last_frame is None:
        _fail("media_inputs_invalid", "I2V requires a first frame, a last frame, or both.")
    clean_first = None
    if first_frame is not None:
        clean_first = _validate_image_basename(first_frame, field="first_frame")
    clean_last = None
    if last_frame is not None:
        clean_last = _validate_image_basename(last_frame, field="last_frame")
    _validate_reference_tags(
        clean_prompt,
        mode="i2v",
        pictures=int(clean_first is not None) + int(clean_last is not None),
    )

    conditioning_inputs: dict[str, object] = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "prompt": clean_prompt,
        "width": width,
        "height": height,
        "length": length,
    }
    graph = _build_graph(
        model_name=FL2VA_MODEL,
        conditioning_type="MiniMaxH3ImageToVideo",
        conditioning_inputs=conditioning_inputs,
        seed=clean_seed,
        output_prefix=clean_prefix,
    )
    next_node = 15
    if clean_first is not None:
        node_id = str(next_node)
        next_node += 1
        graph[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": clean_first},
        }
        conditioning_inputs["first_frame"] = [node_id, 0]
    if clean_last is not None:
        node_id = str(next_node)
        graph[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": clean_last},
        }
        conditioning_inputs["last_frame"] = [node_id, 0]
    return _apply_acceleration(graph, clean_acceleration)


def build_ref2va_prompt(
    *,
    prompt: object,
    resolution: object,
    seed: object,
    output_prefix: object,
    reference_images: object = (),
    reference_videos: object = (),
    reference_audios: object = (),
    ref_image_size: object = "match",
    profile: object | None = None,
    frames: object | None = None,
    acceleration: object = "stock",
) -> PromptGraph:
    """Build official Ref2VA with the complete image/video/audio input surface."""

    (
        clean_prompt,
        width,
        height,
        length,
        clean_seed,
        clean_prefix,
        clean_acceleration,
    ) = _validated_common(
        prompt=prompt,
        resolution=resolution,
        profile=profile,
        frames=frames,
        seed=seed,
        output_prefix=output_prefix,
        mode="ref2va_image",
        acceleration=acceleration,
    )
    images = _validate_media_sequence(
        reference_images,
        field="reference_images",
        maximum=9,
        extensions=_IMAGE_EXTENSIONS,
        media_type="image",
    )
    videos = _validate_media_sequence(
        reference_videos,
        field="reference_videos",
        maximum=3,
        extensions=_VIDEO_EXTENSIONS,
        media_type="video",
    )
    audios = _validate_media_sequence(
        reference_audios,
        field="reference_audios",
        maximum=3,
        extensions=_AUDIO_EXTENSIONS,
        media_type="audio",
    )
    if not images and not videos and not audios:
        _fail("media_inputs_invalid", "Ref2VA requires at least one reference input.")
    if type(ref_image_size) is not str or ref_image_size not in {"match", "max"}:
        _fail("ref_image_size_invalid", "MiniMax H3 reference image size is invalid.")
    _validate_reference_tags(
        clean_prompt,
        mode="ref2va_image",
        pictures=len(images),
        videos=len(videos),
        audios=len(videos) + len(audios),
    )

    conditioning_inputs: dict[str, object] = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "audio_vae": ["4", 0],
        "prompt": clean_prompt,
        "width": width,
        "height": height,
        "length": length,
        "ref_image_size": ref_image_size,
    }
    graph = _build_graph(
        model_name=REF2VA_MODEL,
        conditioning_type="MiniMaxH3ReferenceToVideo",
        conditioning_inputs=conditioning_inputs,
        seed=clean_seed,
        output_prefix=clean_prefix,
    )
    for index, basename in enumerate(images):
        node_id = str(15 + index)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": basename}}
        conditioning_inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
    for index, basename in enumerate(videos):
        node_id = str(30 + index)
        graph[node_id] = {
            "class_type": "VHS_LoadVideo",
            "inputs": {
                "video": basename,
                "force_rate": STOCK_FPS,
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": max(LEGAL_FRAME_LENGTHS),
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
        }
        conditioning_inputs[f"ref_videos.ref_video_{index}"] = [node_id, 0]
        conditioning_inputs[f"ref_video_audios.ref_video_audio_{index}"] = [node_id, 2]
    for index, basename in enumerate(audios):
        node_id = str(40 + index)
        graph[node_id] = {"class_type": "LoadAudio", "inputs": {"audio": basename}}
        conditioning_inputs[f"ref_audios.ref_audio_{index}"] = [node_id, 0]
    return _apply_acceleration(graph, clean_acceleration)


def build_ref2va_image_prompt(
    *,
    prompt: object,
    resolution: object,
    seed: object,
    output_prefix: object,
    reference_image: object,
    profile: object | None = None,
    frames: object | None = None,
    acceleration: object = "stock",
) -> PromptGraph:
    """Compatibility wrapper for the original exactly-one-picture contract."""

    clean_prompt = _validate_prompt(prompt)
    exact = [(match.group(1).casefold(), int(match.group(2))) for match in _REFERENCE_TAG.finditer(clean_prompt)]
    like = list(_REFERENCE_TAG_LIKE.finditer(clean_prompt))
    if exact != [("picture", 1)] or len(like) != 1:
        _fail(
            "reference_tag_invalid",
            "Ref2VA image prompts must contain exactly one <Picture 1> tag.",
        )
    return build_ref2va_prompt(
        prompt=clean_prompt,
        resolution=resolution,
        profile=profile,
        frames=frames,
        seed=seed,
        output_prefix=output_prefix,
        reference_images=(reference_image,),
        acceleration=acceleration,
    )


def build_prompt(request: object) -> PromptGraph:
    """Dispatch one exact request dictionary without mode aliases or fallback."""

    if type(request) is not dict:
        _fail("request_invalid", "MiniMax H3 builder request must be an object.")
    mode = request.get("mode")
    if type(mode) is not str or mode not in _MODES:
        _fail("mode_invalid", "MiniMax H3 mode is invalid.")

    base = {"mode", "prompt", "resolution", "seed", "output_prefix"}
    allowed_by_mode = {
        "t2v": base | {"profile", "frames", "acceleration"},
        "i2v": base | {"profile", "frames", "acceleration", "first_frame", "last_frame"},
        "ref2va_image": base | {"profile", "frames", "acceleration", "reference_image"},
    }
    keys = set(request)
    if (
        not base <= keys
        or not keys <= allowed_by_mode[mode]
        or (("profile" in keys) + ("frames" in keys)) != 1
        or (mode == "i2v" and not ({"first_frame", "last_frame"} & keys))
        or (mode == "ref2va_image" and "reference_image" not in keys)
    ):
        _fail("request_schema_invalid", "MiniMax H3 request fields do not match its mode.")

    arguments = {
        "prompt": request["prompt"],
        "resolution": request["resolution"],
        "profile": request.get("profile"),
        "frames": request.get("frames"),
        "seed": request["seed"],
        "output_prefix": request["output_prefix"],
        "acceleration": request.get("acceleration", "stock"),
    }
    if mode == "t2v":
        return build_t2v_prompt(**arguments)
    if mode == "i2v":
        return build_i2v_prompt(
            **arguments,
            first_frame=request.get("first_frame"),
            last_frame=request.get("last_frame"),
        )
    return build_ref2va_image_prompt(
        **arguments,
        reference_image=request["reference_image"],
    )


__all__ = [
    "ACCELERATION_PROFILES",
    "ALLOWED_RESOLUTIONS",
    "AUDIO_VAE",
    "DURATION_PROFILES",
    "FL2VA_MODEL",
    "LEGAL_FRAME_LENGTHS",
    "MAX_MEDIA_BASENAME_CHARS",
    "MAX_OUTPUT_TOKEN_CHARS",
    "MAX_PROMPT_CHARS",
    "MiniMaxH3PromptError",
    "PromptGraph",
    "REF2VA_MODEL",
    "STOCK_BIT_DEPTH",
    "STOCK_CRF",
    "STOCK_FPS",
    "STOCK_SAMPLER",
    "STOCK_SCHEDULER",
    "STOCK_STEPS",
    "TEXT_ENCODER",
    "UINT64_MAX",
    "VIDEO_VAE",
    "build_i2v_prompt",
    "build_prompt",
    "build_ref2va_prompt",
    "build_ref2va_image_prompt",
    "build_t2v_prompt",
]
