"""Servasyy-aligned MiniMax H3 product contracts for Castle Web.

This module is deliberately independent from Castle's HTTP server.  It defines
the human-facing H3 features and compiles already-materialized generic or
character-bound inputs into the native graphs from :mod:`minimax_h3_native`.

Visual class is never inferred from a prompt.  Anime and realistic characters
have distinct variant ids, option values, prompt guardrails, and release gates.
Those character variants remain fail-closed, while a request with no visual
class uses the generic T2V, I2V, or full Ref2VA surface directly.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Final, Mapping

try:  # Package import in tests/tools; script-style import in Castle server.
    from .minimax_h3_native import (
        AUDIO_VAE,
        FL2VA_MODEL,
        REF2VA_MODEL,
        TEXT_ENCODER,
        UINT64_MAX,
        VIDEO_VAE,
        PromptGraph,
        build_i2v_prompt,
        build_ref2va_image_prompt,
        build_ref2va_prompt,
        build_t2v_prompt,
    )
except ImportError:  # pragma: no cover - exercised by server.py script startup.
    from minimax_h3_native import (
        AUDIO_VAE,
        FL2VA_MODEL,
        REF2VA_MODEL,
        TEXT_ENCODER,
        UINT64_MAX,
        VIDEO_VAE,
        PromptGraph,
        build_i2v_prompt,
        build_ref2va_image_prompt,
        build_ref2va_prompt,
        build_t2v_prompt,
    )


H3_COMMON_MODELS: Final = (TEXT_ENCODER, VIDEO_VAE, AUDIO_VAE)
H3_NATIVE_NODES: Final = (
    "BasicGuider",
    "BasicScheduler",
    "CLIPLoader",
    "CreateVideo",
    "KSamplerSelect",
    "RandomNoise",
    "SamplerCustomAdvanced",
    "SaveVideo",
    "UNETLoader",
    "VAEDecode",
    "VAEDecodeAudio",
    "VAELoader",
)

MAX_DESCRIPTION_CHARS: Final = 4_000
MAX_COMPILED_PROMPT_CHARS: Final = 24_000
H3_SEED_MAX: Final = UINT64_MAX
LEGAL_FRAMES: Final = tuple(range(124, 363, 17))
ACCELERATION_OPTIONS: Final = ("stock", "sage", "easycache", "teacache")
T2V_HEADINGS: Final = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
REF2VA_HEADINGS: Final = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

_REFERENCE_TAG = re.compile(
    r"<\s*(Picture|Audio|Video|Subject)\s+(\d+)\s*>", re.IGNORECASE
)
_REFERENCE_TAG_LIKE = re.compile(
    r"<\s*(?:Picture|Audio|Video|Subject)\b[^>]*(?:>|$)", re.IGNORECASE
)

_ASPECT_OPTIONS: Final = (
    {"value": "portrait", "label": "原生直式 9:16", "width": 768, "height": 1344},
    {"value": "landscape", "label": "原生橫式 16:9", "width": 1344, "height": 768},
    {"value": "square", "label": "原生方形 1:1", "width": 992, "height": 992},
    {"value": "portrait-preview", "label": "預覽直式 9:16", "width": 480, "height": 864},
    {"value": "landscape-preview", "label": "預覽橫式 16:9", "width": 864, "height": 480},
    {"value": "square-preview", "label": "預覽方形 1:1", "width": 640, "height": 640},
)

_ACCELERATION_FIELDS: Final = [
    {"value": "stock", "label": "Stock · 官方 20 步"},
    {"value": "sage", "label": "SageAttention · 20 步"},
    {"value": "easycache", "label": "Sage + EasyCache 0.1"},
    {"value": "teacache", "label": "Sage + TeaCache 0.15"},
]


def _seed_field() -> dict[str, object]:
    # Keep the value as decimal text in the browser. JavaScript Number cannot
    # represent every uint64 seed exactly, while HTML input.value and JSON
    # strings preserve every digit. The server parses and bounds this field
    # before the native graph is compiled.
    return {
        "id": "seed",
        "type": "integer-string",
        "label": "種子（-1 隨機）",
        "default": "-1",
        "maxLength": len(str(H3_SEED_MAX)),
        "pattern": r"-1|[0-9]+",
        "inputMode": "numeric",
    }

I2V_FIRST_ALIGNMENT: Final = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced."
)

ANIME_MOTIONS: Final = {
    "gentle": (
        "subtle hand-drawn character motion, one natural blink, gentle breathing, "
        "hair and clothing sway lightly"
    ),
    "expressive": (
        "expressive but controlled 2D character acting, clear pose changes, "
        "smooth illustrated motion"
    ),
}
REALISTIC_MOTIONS: Final = {
    "natural": (
        "subtle natural human movement, one natural blink, gentle breathing, "
        "physically plausible motion"
    ),
    "presenter": (
        "maintains eye contact with the camera, small natural hand gestures, "
        "clear presenter delivery"
    ),
}

ANIME_GUARDRAIL: Final = (
    "Keep the exact illustrated identity, 2D anime or chibi design, colors, "
    "proportions, and selected outfit from the source. Keep the character fully "
    "clothed and in the same drawing style throughout. Do not turn the character "
    "into live action, photorealistic skin, or 3D realism."
)
REALISTIC_GUARDRAIL: Final = (
    "Preserve the exact adult person's identity, facial structure, skin tone, "
    "hair, and clothing from the approved source. Use natural live-action motion, "
    "stable anatomy, realistic skin texture, and physically plausible hands."
)


@dataclass(frozen=True, slots=True)
class H3CharacterVariant:
    action_id: str
    feature_id: str
    variant_id: str
    visual_class: str
    model_family: str
    motion_options: tuple[str, ...]
    aspect_options: tuple[str, ...]
    server_bound_uploads: tuple[str, ...]
    requires_consent: bool

    def public_contract(self) -> dict:
        return {
            "actionId": self.action_id,
            "featureId": self.feature_id,
            "variantId": self.variant_id,
            "visualClass": self.visual_class,
            "modelFamily": self.model_family,
            "allowedOptions": {
                "motionProfile": list(self.motion_options),
                "aspect": list(self.aspect_options),
                "quality": ["quality"],
                "frames": list(LEGAL_FRAMES),
                "acceleration": list(ACCELERATION_OPTIONS),
            },
            "serverBoundUploads": list(self.server_bound_uploads),
            "requiresConsent": self.requires_consent,
        }


_VARIANTS: Final = {
    ("anime", "image-video"): H3CharacterVariant(
        "image-video",
        "h3-image-video",
        "image-video.anime.h3-fl2va.v1",
        "anime",
        "fl",
        tuple(ANIME_MOTIONS),
        ("portrait", "landscape", "square"),
        ("image",),
        False,
    ),
    ("realistic", "image-video"): H3CharacterVariant(
        "image-video",
        "h3-image-video",
        "image-video.realistic.h3-fl2va.v1",
        "realistic",
        "fl",
        tuple(REALISTIC_MOTIONS),
        ("portrait", "landscape"),
        ("image",),
        True,
    ),
    ("anime", "reference-video"): H3CharacterVariant(
        "reference-video",
        "h3-reference-video",
        "reference-video.anime.h3-ref2va-image.v1",
        "anime",
        "ref",
        tuple(ANIME_MOTIONS),
        ("portrait", "landscape", "square"),
        ("reference",),
        False,
    ),
    ("realistic", "reference-video"): H3CharacterVariant(
        "reference-video",
        "h3-reference-video",
        "reference-video.realistic.h3-ref2va-image.v1",
        "realistic",
        "ref",
        tuple(REALISTIC_MOTIONS),
        ("portrait", "landscape"),
        ("reference",),
        True,
    ),
}


def _select_options(values: Mapping[str, str], labels: Mapping[str, tuple[str, str]]) -> list[dict]:
    return [
        {"value": value, "label": labels[value][0], "sub": labels[value][1]}
        for value in values
    ]


_ANIME_MOTION_FIELDS: Final = _select_options(
    ANIME_MOTIONS,
    {
        "gentle": ("自然微動", "眨眼、呼吸、髮絲與衣服輕微擺動"),
        "expressive": ("表情動作", "較明顯但仍維持 2D 角色設計"),
    },
)
_REALISTIC_MOTION_FIELDS: Final = _select_options(
    REALISTIC_MOTIONS,
    {
        "natural": ("自然微動", "真人尺度的眨眼、呼吸與輕微動作"),
        "presenter": ("鏡頭講解", "看鏡頭並使用小幅自然手勢"),
    },
)

H3_FEATURES: Final = (
    {
        "id": "h3-text-video",
        "studio": "video",
        "title": "文字生成有聲影片",
        "subtitle": "MiniMax H3 FL2VA · 三欄原生提示詞",
        "kind": "generate",
        "builder": "minimax_h3",
        "uploads": [],
        "fields": [
            {
                "id": "integrated_multimodal_description",
                "type": "textarea",
                "label": "integrated_multimodal_description",
                "required": True,
                "rows": 6,
                "maxLength": MAX_DESCRIPTION_CHARS,
                "placeholder": "依時間順序描述畫面、動作、鏡頭、對白與可見文字。",
            },
            {
                "id": "overall_soundscape",
                "type": "textarea",
                "label": "overall_soundscape",
                "required": True,
                "rows": 3,
                "maxLength": MAX_DESCRIPTION_CHARS,
                "placeholder": "環境音、物理聲響、對白以外的場景聲音；沒有可填 N/A。",
            },
            {
                "id": "non_diegetic_music",
                "type": "textarea",
                "label": "non_diegetic_music",
                "required": True,
                "rows": 3,
                "maxLength": MAX_DESCRIPTION_CHARS,
                "placeholder": "觀眾聽見、場景內角色聽不見的配樂；不要配樂可填 None.",
            },
            {"id": "aspect", "type": "select", "label": "畫布", "default": "landscape", "options": list(_ASPECT_OPTIONS)},
            {"id": "frames", "type": "select", "label": "幀數（17k+5）", "default": "124", "options": [{"value": str(frames), "label": f"{frames} 幀 · {frames / 24:.2f} 秒"} for frames in LEGAL_FRAMES]},
            {"id": "acceleration", "type": "select", "label": "加速模式", "default": "stock", "options": list(_ACCELERATION_FIELDS)},
            _seed_field(),
        ],
        "requiredModels": [FL2VA_MODEL, *H3_COMMON_MODELS],
        "requiredNodes": [*H3_NATIVE_NODES, "MiniMaxH3ImageToVideo", "PathchSageAttentionKJ", "EasyCache", "MiniMaxH3TeaCache"],
        "note": "可直接測試，不綁角色；Stock、Sage、EasyCache、TeaCache 都是明確選項。",
    },
    {
        "id": "h3-image-video",
        "studio": "video",
        "title": "首／尾格有聲影片",
        "subtitle": "MiniMax H3 FL2VA · I2V / FLF",
        "kind": "generate",
        "builder": "minimax_h3",
        "uploads": [
            {"id": "image", "label": "首格圖片", "accept": "image/*", "required": False},
            {"id": "last_frame", "label": "尾格圖片", "accept": "image/*", "required": False},
        ],
        "minimumUploads": 1,
        "fields": [
            {"id": "integrated_multimodal_description", "type": "textarea", "label": "integrated_multimodal_description", "required": True, "rows": 6, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "overall_soundscape", "type": "textarea", "label": "overall_soundscape", "required": True, "rows": 3, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "non_diegetic_music", "type": "textarea", "label": "non_diegetic_music", "required": True, "rows": 3, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "aspect", "type": "select", "label": "畫布", "default": "landscape", "options": list(_ASPECT_OPTIONS)},
            {"id": "frames", "type": "select", "label": "幀數（17k+5）", "default": "124", "options": [{"value": str(frames), "label": f"{frames} 幀 · {frames / 24:.2f} 秒"} for frames in LEGAL_FRAMES]},
            {"id": "acceleration", "type": "select", "label": "加速模式", "default": "stock", "options": list(_ACCELERATION_FIELDS)},
            _seed_field(),
        ],
        "requiredModels": [FL2VA_MODEL, *H3_COMMON_MODELS],
        "requiredNodes": [*H3_NATIVE_NODES, "MiniMaxH3ImageToVideo", "LoadImage", "PathchSageAttentionKJ", "EasyCache", "MiniMaxH3TeaCache"],
        "note": "首格、尾格皆可單獨使用；至少上傳一張。角色入口仍會套用動漫／真人 guardrail。",
    },
    {
        "id": "h3-reference-video",
        "studio": "video",
        "title": "多參考有聲影片",
        "subtitle": "MiniMax H3 Ref2VA · 圖片／影片／音訊",
        "kind": "generate",
        "builder": "minimax_h3",
        "uploads": [
            {"id": "reference", "label": "參考圖片 1", "accept": "image/*", "required": False},
            *[
                {"id": f"reference{index}", "label": f"參考圖片 {index}", "accept": "image/*", "required": False}
                for index in range(2, 10)
            ],
            *[
                {"id": f"video{index}", "label": f"參考影片 {index}", "accept": "video/*", "required": False}
                for index in range(1, 4)
            ],
            *[
                {"id": f"audio{index}", "label": f"參考音訊 {index}", "accept": "audio/*", "required": False}
                for index in range(1, 4)
            ],
        ],
        "minimumUploads": 1,
        "fields": [
            {"id": "subject_definitions", "type": "textarea", "label": "subject_definitions", "required": True, "rows": 5, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "summary", "type": "textarea", "label": "summary", "required": True, "rows": 3, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "retention_analysis", "type": "textarea", "label": "retention_analysis", "required": True, "rows": 4, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "detailed_description", "type": "textarea", "label": "detailed_description", "required": True, "rows": 7, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "overall_soundscape", "type": "textarea", "label": "overall_soundscape", "required": True, "rows": 3, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "non_diegetic_music", "type": "textarea", "label": "non_diegetic_music", "required": True, "rows": 3, "maxLength": MAX_DESCRIPTION_CHARS},
            {"id": "aspect", "type": "select", "label": "畫布", "default": "landscape", "options": list(_ASPECT_OPTIONS)},
            {"id": "frames", "type": "select", "label": "幀數（17k+5）", "default": "124", "options": [{"value": str(frames), "label": f"{frames} 幀 · {frames / 24:.2f} 秒"} for frames in LEGAL_FRAMES]},
            {"id": "acceleration", "type": "select", "label": "加速模式", "default": "stock", "options": list(_ACCELERATION_FIELDS)},
            _seed_field(),
        ],
        "requiredModels": [REF2VA_MODEL, *H3_COMMON_MODELS],
        "requiredNodes": [*H3_NATIVE_NODES, "MiniMaxH3ReferenceToVideo", "LoadImage", "VHS_LoadVideo", "LoadAudio", "PathchSageAttentionKJ", "EasyCache", "MiniMaxH3TeaCache"],
        "note": "可直接使用最多 9 圖、3 影片、3 音訊；六段提示詞與上傳 reference tag 必須一致。",
    },
)


class MiniMaxH3ProductError(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise MiniMaxH3ProductError(code, message)


def feature_definitions() -> list[dict]:
    return copy.deepcopy(list(H3_FEATURES))


def character_variant(visual_class: object, action_id: object) -> H3CharacterVariant | None:
    if type(visual_class) is not str or type(action_id) is not str:
        return None
    return _VARIANTS.get((visual_class.strip().lower(), action_id.strip()))


def character_variant_contract(visual_class: object, action_id: object) -> dict | None:
    variant = character_variant(visual_class, action_id)
    return variant.public_contract() if variant is not None else None


def _clean_text(value: object, *, field: str, allow_reference_tags: bool) -> str:
    if type(value) is not str:
        _fail("description_invalid", f"MiniMax H3 field {field} must be text.")
    clean = value.strip()
    if not clean or len(clean) > MAX_DESCRIPTION_CHARS or "\x00" in clean:
        _fail("description_invalid", f"MiniMax H3 field {field} is blank or too long.")
    if not allow_reference_tags and _REFERENCE_TAG_LIKE.search(clean):
        _fail("reference_tag_forbidden", "Reference tags are assigned by Castle, not by the request.")
    return clean


def _description(value: object) -> str:
    return _clean_text(value, field="description", allow_reference_tags=False)


def _section_prompt(
    settings: Mapping[str, object],
    headings: tuple[str, ...],
    *,
    allow_reference_tags: bool,
) -> str:
    sections = []
    for heading in headings:
        body = _clean_text(
            settings.get(heading),
            field=heading,
            allow_reference_tags=allow_reference_tags,
        )
        sections.append(f"{heading}:\n{body}")
    prompt = "\n\n".join(sections)
    if len(prompt) > MAX_COMPILED_PROMPT_CHARS:
        _fail("description_invalid", "MiniMax H3 compiled prompt is too long.")
    return prompt


def _exact_option(settings: Mapping[str, object], key: str, allowed: tuple[str, ...]) -> str:
    value = settings.get(key)
    if type(value) is not str or value not in allowed:
        _fail("option_invalid", f"MiniMax H3 option {key} is not allowed for this variant.")
    return value


def _resolution(aspect: str) -> tuple[int, int]:
    for option in _ASPECT_OPTIONS:
        if option["value"] == aspect:
            return int(option["width"]), int(option["height"])
    _fail("option_invalid", "MiniMax H3 aspect is invalid.")


def _frames(settings: Mapping[str, object]) -> int:
    if "frames" in settings:
        if "quality" in settings:
            _fail("duration_invalid", "MiniMax H3 accepts frames or legacy quality, not both.")
        value = settings.get("frames")
        if type(value) is str and value in {str(item) for item in LEGAL_FRAMES}:
            value = int(value)
        if type(value) is not int or value not in LEGAL_FRAMES:
            _fail("duration_invalid", "MiniMax H3 frames must use the 17k+5 grid from 124 to 362.")
        return value
    quality = _exact_option(settings, "quality", ("quality",))
    if quality != "quality":  # pragma: no cover - exact option already fails.
        _fail("duration_invalid", "MiniMax H3 legacy duration is invalid.")
    return 124


def _acceleration(settings: Mapping[str, object]) -> str:
    value = settings.get("acceleration", "stock")
    if type(value) is not str or value not in ACCELERATION_OPTIONS:
        _fail("option_invalid", "MiniMax H3 acceleration is invalid.")
    return value


def _seed(settings: Mapping[str, object]) -> int:
    value = settings.get("seed")
    if type(value) is not int or value < 0 or value > H3_SEED_MAX:
        _fail("seed_invalid", "MiniMax H3 seed must already be materialized by the server.")
    return value


def _output_prefix(feature_id: str, token: object) -> str:
    if type(token) is not str:
        _fail("output_token_invalid", "MiniMax H3 output token is invalid.")
    mode = {
        "h3-text-video": "t2v",
        "h3-image-video": "i2v",
        "h3-reference-video": "ref2va_image",
    }.get(feature_id)
    if mode is None:
        _fail("feature_invalid", "MiniMax H3 feature is invalid.")
    return f"Castle/minimax-h3/{mode}/{token}"


def _generic_i2v_uploads(uploads: Mapping[str, object]) -> tuple[object | None, object | None]:
    allowed = {"image", "first_frame", "last_frame"}
    if not set(uploads) <= allowed or ("image" in uploads and "first_frame" in uploads):
        _fail("uploads_invalid", "MiniMax H3 I2V uploads are invalid.")
    first = uploads.get("first_frame", uploads.get("image"))
    last = uploads.get("last_frame")
    if first is None and last is None:
        _fail("uploads_invalid", "MiniMax H3 I2V requires a first frame, a last frame, or both.")
    return first, last


def _ordered_uploads(uploads: Mapping[str, object], keys: tuple[str, ...]) -> tuple[object, ...]:
    values: list[object] = []
    gap = False
    for key in keys:
        value = uploads.get(key)
        if value is None:
            gap = True
            continue
        if gap:
            _fail("uploads_invalid", "MiniMax H3 reference upload slots must be contiguous.")
        values.append(value)
    return tuple(values)


def _generic_ref2va_uploads(
    uploads: Mapping[str, object],
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
    image_keys = ("reference", *(f"reference{index}" for index in range(2, 10)))
    video_keys = tuple(f"video{index}" for index in range(1, 4))
    audio_keys = tuple(f"audio{index}" for index in range(1, 4))
    allowed = {*image_keys, *video_keys, *audio_keys}
    if not set(uploads) <= allowed:
        _fail("uploads_invalid", "MiniMax H3 Ref2VA uploads are invalid.")
    images = _ordered_uploads(uploads, image_keys)
    videos = _ordered_uploads(uploads, video_keys)
    audios = _ordered_uploads(uploads, audio_keys)
    if not images and not videos and not audios:
        _fail("uploads_invalid", "MiniMax H3 Ref2VA requires at least one reference.")
    return images, videos, audios


def _validate_ref2va_tags(
    prompt: str,
    *,
    pictures: int,
    videos: int,
    audios: int,
) -> None:
    exact = list(_REFERENCE_TAG.finditer(prompt))
    tag_like = list(_REFERENCE_TAG_LIKE.finditer(prompt))
    if [match.span() for match in exact] != [match.span() for match in tag_like]:
        _fail("reference_tag_invalid", "MiniMax H3 prompt contains a malformed reference tag.")
    limits = {"picture": pictures, "video": videos, "audio": videos + audios}
    for match in exact:
        kind = match.group(1).casefold()
        index = int(match.group(2))
        if index < 1 or (kind == "subject" and index > 99):
            _fail("reference_tag_invalid", "MiniMax H3 reference index is invalid.")
        if kind != "subject" and index > limits[kind]:
            _fail("reference_tag_invalid", "MiniMax H3 prompt references unavailable media.")


def _i2v_alignment(*, first: bool, last: bool, frames: int) -> str:
    duration = frames / 24
    if first and last:
        return (
            "How the reference pictures align with the target video — Picture 1 "
            "(from Shot 1) aligns with the 0.00-second mark of the target video; "
            "Picture 2 (from Shot 1) aligns with the "
            f"{duration:.2f}-second mark of the target video."
        )
    if first:
        return I2V_FIRST_ALIGNMENT
    return (
        "How the reference pictures align with the target video — "
        f"<Picture 1> (from [Shot N]) aligns with the {duration:.2f}-second "
        "mark of the target video."
    )


def compile_product_prompt(
    *,
    feature_id: object,
    visual_class: object,
    settings: object,
    uploads: object,
    output_token: object,
) -> PromptGraph:
    """Compile one generic or character-bound request without class fallback."""

    if type(feature_id) is not str or type(settings) is not dict or type(uploads) is not dict:
        _fail("request_invalid", "MiniMax H3 product request is invalid.")
    if feature_id not in {"h3-text-video", "h3-image-video", "h3-reference-video"}:
        _fail("feature_invalid", "MiniMax H3 feature is invalid.")
    frames = _frames(settings)
    acceleration = _acceleration(settings)
    seed = _seed(settings)
    generic = visual_class in (None, "")

    if feature_id == "h3-text-video":
        if not generic or uploads:
            _fail("generic_context_invalid", "Generic H3 text video cannot claim a character context.")
        aspect = _exact_option(
            settings,
            "aspect",
            tuple(option["value"] for option in _ASPECT_OPTIONS),
        )
        prompt = _section_prompt(
            settings,
            T2V_HEADINGS,
            allow_reference_tags=False,
        )
        return build_t2v_prompt(
            prompt=prompt,
            resolution=_resolution(aspect),
            frames=frames,
            acceleration=acceleration,
            seed=seed,
            output_prefix=_output_prefix(feature_id, output_token),
        )

    if generic and feature_id == "h3-image-video":
        first, last = _generic_i2v_uploads(uploads)
        aspect = _exact_option(
            settings,
            "aspect",
            tuple(option["value"] for option in _ASPECT_OPTIONS),
        )
        body = _section_prompt(
            settings,
            T2V_HEADINGS,
            allow_reference_tags=False,
        )
        prompt = (
            f"{_i2v_alignment(first=first is not None, last=last is not None, frames=frames)}"
            f"\n\n{body}"
        )
        return build_i2v_prompt(
            prompt=prompt,
            resolution=_resolution(aspect),
            frames=frames,
            acceleration=acceleration,
            seed=seed,
            output_prefix=_output_prefix(feature_id, output_token),
            first_frame=first,
            last_frame=last,
        )

    if generic and feature_id == "h3-reference-video":
        images, videos, audios = _generic_ref2va_uploads(uploads)
        aspect = _exact_option(
            settings,
            "aspect",
            tuple(option["value"] for option in _ASPECT_OPTIONS),
        )
        prompt = _section_prompt(
            settings,
            REF2VA_HEADINGS,
            allow_reference_tags=True,
        )
        _validate_ref2va_tags(
            prompt,
            pictures=len(images),
            videos=len(videos),
            audios=len(audios),
        )
        return build_ref2va_prompt(
            prompt=prompt,
            resolution=_resolution(aspect),
            frames=frames,
            acceleration=acceleration,
            seed=seed,
            output_prefix=_output_prefix(feature_id, output_token),
            reference_images=images,
            reference_videos=videos,
            reference_audios=audios,
        )

    action_id = {
        "h3-image-video": "image-video",
        "h3-reference-video": "reference-video",
    }.get(feature_id)
    variant = character_variant(visual_class, action_id or "")
    if variant is None or variant.feature_id != feature_id:
        _fail("visual_class_unsupported", "This H3 feature has no variant for the character class.")
    description = _description(settings.get("description"))
    motion_profile = _exact_option(settings, "motionProfile", variant.motion_options)
    aspect = _exact_option(settings, "aspect", variant.aspect_options)
    guardrail = ANIME_GUARDRAIL if variant.visual_class == "anime" else REALISTIC_GUARDRAIL
    motion = (ANIME_MOTIONS if variant.visual_class == "anime" else REALISTIC_MOTIONS)[motion_profile]
    composed = f"{guardrail}\n\nMotion: {motion}.\n\nScene and audio: {description}"

    expected_slot = variant.server_bound_uploads[0]
    allowed_upload_sets = ({expected_slot},)
    if feature_id == "h3-image-video":
        allowed_upload_sets = ({expected_slot}, {expected_slot, "last_frame"})
    if set(uploads) not in allowed_upload_sets:
        _fail("uploads_invalid", "MiniMax H3 inputs do not match the exact character variant.")
    if feature_id == "h3-image-video":
        last_frame = uploads.get("last_frame")
        prompt = (
            f"{_i2v_alignment(first=True, last=last_frame is not None, frames=frames)}\n\n"
            "integrated_multimodal_description:\n"
            f"{composed}\n\n"
            "overall_soundscape:\n"
            "Use only the scene sounds explicitly requested above.\n\n"
            "non_diegetic_music:\n"
            "Use only music explicitly requested above; otherwise None."
        )
        return build_i2v_prompt(
            prompt=prompt,
            resolution=_resolution(aspect),
            frames=frames,
            acceleration=acceleration,
            seed=seed,
            output_prefix=_output_prefix(feature_id, output_token),
            first_frame=uploads["image"],
            last_frame=last_frame,
        )
    return build_ref2va_image_prompt(
        prompt=f"<Picture 1> {composed}",
        resolution=_resolution(aspect),
        frames=frames,
        acceleration=acceleration,
        seed=seed,
        output_prefix=_output_prefix(feature_id, output_token),
        reference_image=uploads["reference"],
    )


__all__ = [
    "ACCELERATION_OPTIONS",
    "ANIME_GUARDRAIL",
    "ANIME_MOTIONS",
    "H3CharacterVariant",
    "H3_COMMON_MODELS",
    "H3_FEATURES",
    "H3_NATIVE_NODES",
    "H3_SEED_MAX",
    "I2V_FIRST_ALIGNMENT",
    "LEGAL_FRAMES",
    "MAX_COMPILED_PROMPT_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MiniMaxH3ProductError",
    "REALISTIC_GUARDRAIL",
    "REALISTIC_MOTIONS",
    "REF2VA_HEADINGS",
    "T2V_HEADINGS",
    "character_variant",
    "character_variant_contract",
    "compile_product_prompt",
    "feature_definitions",
]
