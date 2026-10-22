"""Offline AutoIP MCP 0.4.0 contract with reviewed REST skill amendments.

This module is deliberately not an adapter.  It performs no HTTP, credential,
filesystem, provider, store, or GPU work.  It freezes the seven chargeable
generation operations exposed by ``@autoip/mcp@0.4.0`` and normalizes only
inert, server-owned request/response data.

The user-supplied 2026-10-22 REST skill supersedes the package's old video
duration bounds: text/image-to-video accept 2..15 seconds at 480p, 2..10 at
768p, and 5..10 at 2k. Multi-reference starts at 5 seconds with the same
resolution-specific maxima. Live pricing must still contain the exact offering.
The durable package identity is retained for existing jobs; the REST amendment
revision is recorded separately and does not activate new operation families.

User-supplied callback URLs and credentials are intentionally absent from the
request contract.  The live adapter injects its deployment-owned
``webhook_url`` only after authorization; the browser cannot select or
override it.  Provider output URLs and filenames are accepted only as
explicitly untrusted response fields and are never retained or serialized by
the normalized result.  Provider MIME and size declarations are private hints
only: they cannot select an extension or media family.  The authenticated
download lane must classify and hash the actual bytes.

Every media input names an immutable Castle ``asset_version_id`` and its exact
lowercase SHA-256 digest.  A mutable projection/asset id is insufficient for a
chargeable durable intent.  The operation allowlist is transport evidence
only: it does not assert that either visual class has passed consent, option,
quality-golden, or product-visibility review for a given operation.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import math
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .media_signatures import signature_family_for_mime


PACKAGE_NAME = "@autoip/mcp"
PACKAGE_VERSION = "0.4.0"
PACKAGE_INTEGRITY = (
    "sha512-b+6LkXMANGI7Xe4sYX/609VMVQy9taxuOn0oL18fctTsqwNEQ0TikVP9YphpAdTf2jJHjTDbpNBivC/9qM8i8A=="
)
CONTRACT_ID = f"{PACKAGE_NAME}@{PACKAGE_VERSION}"
REST_SKILL_REVISION = "2026-10-22"
REQUEST_SCHEMA_VERSION = 1

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CONTRACT_JSON_BYTES = 128 * 1024
_MAX_JSON_DEPTH = 32
_MAX_JSON_TOKENS = 8_192
_MAX_JSON_NUMBER_CHARS = 128
_MAX_JSON_INTEGER_ABS = 2**63 - 1
_MAX_TASK_OUTPUTS = 64
_MAX_TASK_PAGE_SIZE = 100
_MAX_DANCE_TEMPLATES = 500
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024 * 1024
# Exact machine codes documented by the pinned 0.4.0 REST skill.  Anything
# else is provider-controlled text and collapses to ``provider_error``.
_PUBLIC_ERROR_CODES = frozenset(
    {
        "concurrency_limit",
        "insufficient_credits",
        "plan_required",
        "prompt_rejected",
        "requires_age_verification",
        "validation_failed",
    }
)


class AutoIPContractValidationError(ValueError):
    """A stable, sanitized contract error; raw provider/user values are absent."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise AutoIPContractValidationError(code, message)


class AutoIPContractOperation(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"
    LIP_SYNC = "lip_sync"
    DANCE = "dance"
    MULTI_SUBJECT_VIDEO = "multi_subject_video"


@dataclass(frozen=True, slots=True)
class AutoIPRoute:
    method: str
    path_template: str
    chargeable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path_template": self.path_template,
            "chargeable": self.chargeable,
        }


# This is an allowlist, not a product-visibility grant.  Account and pricing
# routes belong to later slices.  Castle's webhook receiver is not an AutoIP
# API route, and its fixed URL is injected only by the live transport client.
AUTOIP_ROUTES: Mapping[str, AutoIPRoute] = MappingProxyType(
    {
        "text_to_image": AutoIPRoute("POST", "/images/text-to-image", True),
        "image_to_image": AutoIPRoute("POST", "/images/image-to-image", True),
        "image_to_video": AutoIPRoute("POST", "/videos/image-to-video", True),
        "text_to_video": AutoIPRoute("POST", "/videos/text-to-video", True),
        "lip_sync": AutoIPRoute("POST", "/videos/lip-sync", True),
        "dance": AutoIPRoute("POST", "/videos/dance", True),
        "multi_subject_video": AutoIPRoute(
            "POST", "/videos/multi-subject-reference", True
        ),
        "dance_templates": AutoIPRoute("GET", "/dance-templates"),
        "account": AutoIPRoute("GET", "/me"),
        "credit_balance": AutoIPRoute("GET", "/credits/balance"),
        "pricing": AutoIPRoute("GET", "/pricing"),
        "task_list": AutoIPRoute("GET", "/tasks"),
        "task_status": AutoIPRoute("GET", "/tasks/{task_uuid}"),
        "task_output": AutoIPRoute(
            "GET", "/tasks/{task_uuid}/outputs/{index}"
        ),
    }
)


def render_route(
    route_name: object,
    *,
    task_uuid: object | None = None,
    index: object | None = None,
) -> tuple[str, str]:
    """Render one allowlisted relative route without ever producing an origin."""

    if not isinstance(route_name, str) or route_name not in AUTOIP_ROUTES:
        _fail("route_not_allowlisted", "AutoIP route is not allowlisted by this contract.")
    route = AUTOIP_ROUTES[route_name]
    path = route.path_template
    if "{task_uuid}" in path:
        if not isinstance(task_uuid, str) or not _TASK_RE.fullmatch(task_uuid):
            _fail("task_uuid_invalid", "AutoIP task identifier is invalid.")
        path = path.replace("{task_uuid}", task_uuid)
    elif task_uuid is not None:
        _fail("route_argument_invalid", "AutoIP route arguments do not match the route.")
    if "{index}" in path:
        output_index = _integer(index, "output index", minimum=0, maximum=999_999)
        path = path.replace("{index}", str(output_index))
    elif index is not None:
        _fail("route_argument_invalid", "AutoIP route arguments do not match the route.")
    return route.method, path


_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_PORTRAIT_MIMES = frozenset({"image/jpeg", "image/png"})
_AUDIO_MIMES = frozenset(
    {
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
    }
)
_DANCE_VIDEO_MIMES = frozenset({"video/mp4", "video/quicktime"})
_IMAGE_STYLES = frozenset({
    "none", "realistic", "cinematic", "neon-punk", "minimalist", "retro-pop",
    "luxury-brand", "anime", "kawaii", "cyberpunk", "fantasy-art", "watercolor",
    "ink-wash", "flat-design", "isometric", "product-shot",
})
@dataclass(frozen=True, slots=True)
class _InputRule:
    minimum: int
    maximum: int
    mime_types: frozenset[str]
    max_bytes: int
    visual: bool


_INPUT_RULES: Mapping[AutoIPContractOperation, Mapping[str, _InputRule]] = (
    MappingProxyType(
        {
            AutoIPContractOperation.TEXT_TO_IMAGE: MappingProxyType({}),
            AutoIPContractOperation.IMAGE_TO_IMAGE: MappingProxyType(
                {"image": _InputRule(1, 1, _IMAGE_MIMES, 50 * 1024 * 1024, True)}
            ),
            AutoIPContractOperation.IMAGE_TO_VIDEO: MappingProxyType(
                {
                    "first_frame": _InputRule(
                        1, 1, _IMAGE_MIMES, 50 * 1024 * 1024, True
                    ),
                    "last_frame": _InputRule(
                        0, 1, _IMAGE_MIMES, 50 * 1024 * 1024, True
                    ),
                }
            ),
            AutoIPContractOperation.TEXT_TO_VIDEO: MappingProxyType({}),
            AutoIPContractOperation.LIP_SYNC: MappingProxyType(
                {
                    "image": _InputRule(
                        1, 1, _PORTRAIT_MIMES, 10 * 1024 * 1024, True
                    ),
                    "audio": _InputRule(
                        1, 1, _AUDIO_MIMES, 50 * 1024 * 1024, False
                    ),
                }
            ),
            AutoIPContractOperation.DANCE: MappingProxyType(
                {
                    "image": _InputRule(
                        1, 1, _PORTRAIT_MIMES, 30 * 1024 * 1024, True
                    ),
                    "dance_video": _InputRule(
                        0, 1, _DANCE_VIDEO_MIMES, 100 * 1024 * 1024, False
                    ),
                }
            ),
            AutoIPContractOperation.MULTI_SUBJECT_VIDEO: MappingProxyType(
                {
                    "images[]": _InputRule(
                        1, 4, _IMAGE_MIMES, 10 * 1024 * 1024, True
                    ),
                    "voice_sample": _InputRule(
                        0, 1, _AUDIO_MIMES, 50 * 1024 * 1024, False
                    ),
                }
            ),
        }
    )
)


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPInput:
    field_name: str
    asset_version_id: str
    sha256: str
    mime_type: str
    byte_size: int
    signature_family: str
    visual_class: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "asset_version_id": self.asset_version_id,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "signature_family": self.signature_family,
            "visual_class": self.visual_class,
        }


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPRequest:
    operation: AutoIPContractOperation
    visual_class: str
    fields: Mapping[str, Any] = field(repr=False)
    inputs: tuple[NormalizedAutoIPInput, ...] = field(repr=False)

    @property
    def method(self) -> str:
        return AUTOIP_ROUTES[self.operation.value].method

    @property
    def path(self) -> str:
        return AUTOIP_ROUTES[self.operation.value].path_template

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_ID,
            "schema_version": REQUEST_SCHEMA_VERSION,
            "operation": self.operation.value,
            "visual_class": self.visual_class,
            "fields": _plain_data(self.fields),
            "inputs": [item.to_dict() for item in self.inputs],
        }


def _plain_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_data(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_data(child) for child in value]
    return value


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail("schema_invalid", f"{label} must be an object with string keys.")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    keys = set(value)
    if keys - required - optional:
        _fail("unknown_field", f"{label} contains an unknown field.")
    if required - keys:
        _fail("required_field_missing", f"{label} is missing a required field.")


def _required_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    label: str,
) -> None:
    """只斷言「我們會綁的那幾個欄位在不在」, 多出來的一律忽略。

    ⚠️ 只給**供應商回給我們的文件**用, 請求那一側永遠留 _exact_keys ——
    我們送出去的東西不准多一個欄位。

    2026-09-16: autoip.app 在 /me 加了 nsfw_enabled / strict_content_review /
    min_app_version, 在 /pricing 加了 image_to_prompt / image_mix / multi_angle /
    keyframe / video_restore / voice_clone, 同時把影片解析度從 480p+720p 換成
    480p/768p/2k。全部都是加法, 但 _exact_keys 是封閉世界, 於是整個帳號被判
    invalid_account_contract, AutoIP 這條線從 8/25 起整個死掉。
    task status 那一支本來就是開放世界(只投影認得的欄位), 這裡只是把同一個
    規矩補到 /me 跟 /pricing 上。
    """

    if required - set(value):
        _fail("required_field_missing", f"{label} is missing a required field.")


def _agreed_alias(
    mappings: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    *,
    label: str,
    normalize: Callable[[object], Any],
    required: bool = False,
    default: Any = None,
) -> Any:
    """Normalize every non-null alias and require one exact value.

    This deliberately examines every alias in every envelope layer.  Choosing
    the first value would let a provider body smuggle contradictory identity,
    charge, pagination, or route data into a field another caller may trust.
    """

    values: list[Any] = []
    for mapping in mappings:
        for name in names:
            if name in mapping and mapping[name] is not None:
                values.append(normalize(mapping[name]))
    if not values:
        if required:
            _fail("required_field_missing", f"{label} is missing.")
        return default
    first = values[0]
    if any(value != first for value in values[1:]):
        _fail("alias_mismatch", f"{label} aliases disagree.")
    return first


def _task_identifier(value: object) -> str:
    if not isinstance(value, str) or not _TASK_RE.fullmatch(value):
        _fail("task_uuid_invalid", "AutoIP task identifier is invalid.")
    return value


def _string(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
    strip: bool = False,
) -> str:
    if not isinstance(value, str):
        _fail("field_type_invalid", f"{label} must be text.")
    normalized = value.strip() if strip else value
    try:
        normalized.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise AutoIPContractValidationError(
            "field_value_invalid", f"{label} is not canonical UTF-8 text."
        ) from error
    # Zod runs in JavaScript and measures strings in UTF-16 code units.  Python
    # counts Unicode code points, which differs for astral characters.  Freeze
    # the executable package's semantics rather than Python's native length.
    utf16_length = len(normalized.encode("utf-16-le", "strict")) // 2
    if (
        not minimum <= utf16_length <= maximum
        or not unicodedata.is_normalized("NFC", normalized)
        or any(ord(character) < 32 and character not in "\n\t" for character in normalized)
        or "\x7f" in normalized
    ):
        _fail("field_value_invalid", f"{label} is outside the contract bounds.")
    return normalized


def _integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail("field_value_invalid", f"{label} must be an integer inside the contract bounds.")
    return value


def _number(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        _fail("field_value_invalid", f"{label} must be a finite number inside the contract bounds.")
    return value


def _enum(value: object, label: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail("field_value_invalid", f"{label} is not an exact allowlisted value.")
    return value


def _visual_class(value: object) -> str:
    # No lowercasing and no illustration/unknown -> realistic fallback.
    return _enum(value, "visual_class", frozenset({"anime", "realistic"}))


def _fields_for(
    operation: AutoIPContractOperation,
    raw: object,
    *,
    visual_class: str,
    image_count: int,
    voice_sample_count: int,
    dance_video_count: int,
) -> Mapping[str, Any]:
    fields = _object(raw, "generation fields")
    if len(fields) > 12:
        _fail("field_count_invalid", "Generation fields exceed the contract bound.")
    common_video = frozenset(
        {"prompt", "duration_seconds", "resolution", "aspect_ratio", "seed"}
    )
    allowed: Mapping[AutoIPContractOperation, tuple[frozenset[str], frozenset[str]]] = {
        AutoIPContractOperation.TEXT_TO_IMAGE: (
            frozenset({"prompt"}),
            frozenset({"mode", "size", "negative_prompt", "style"}),
        ),
        AutoIPContractOperation.IMAGE_TO_IMAGE: (
            frozenset({"prompt"}),
            frozenset({"strength", "size", "mode", "negative_prompt", "style"}),
        ),
        AutoIPContractOperation.IMAGE_TO_VIDEO: (
            frozenset({"prompt", "duration_seconds", "resolution"}),
            common_video - {"prompt", "duration_seconds", "resolution"},
        ),
        AutoIPContractOperation.TEXT_TO_VIDEO: (
            frozenset({"prompt", "duration_seconds", "resolution"}),
            common_video - {"prompt", "duration_seconds", "resolution"},
        ),
        AutoIPContractOperation.LIP_SYNC: (
            frozenset(),
            frozenset({"prompt", "aspect_ratio", "audio_start_time", "audio_duration"}),
        ),
        AutoIPContractOperation.DANCE: (
            frozenset(),
            frozenset({"dance_template_id", "prompt"}),
        ),
        AutoIPContractOperation.MULTI_SUBJECT_VIDEO: (
            frozenset({"prompt", "duration_seconds", "resolution"}),
            frozenset(
                {
                    "descriptions[]",
                    "aspect_ratio",
                    "dialogue_text",
                    "negative_prompt",
                    "seed",
                }
            ),
        ),
    }
    required, optional = allowed[operation]
    _exact_keys(fields, required=required, optional=optional, label="generation fields")

    normalized: dict[str, Any] = {}
    if "prompt" in fields:
        lower = 2 if operation in {
            AutoIPContractOperation.TEXT_TO_IMAGE,
            AutoIPContractOperation.IMAGE_TO_IMAGE,
        } else 0 if operation in {
            AutoIPContractOperation.LIP_SYNC,
            AutoIPContractOperation.DANCE,
        } else 5
        upper = 500 if operation is AutoIPContractOperation.DANCE else (
            4000 if operation in {
                AutoIPContractOperation.IMAGE_TO_VIDEO,
                AutoIPContractOperation.TEXT_TO_VIDEO,
                AutoIPContractOperation.MULTI_SUBJECT_VIDEO,
            } else 2000
        )
        normalized["prompt"] = _string(
            fields["prompt"], "prompt", minimum=lower, maximum=upper, strip=True
        )

    if operation is AutoIPContractOperation.TEXT_TO_IMAGE:
        mode = _enum(
            fields.get("mode", "standard"),
            "mode",
            frozenset({"standard", "hd_4k", "v2", "v3", "anime"}),
        )
        if (visual_class == "anime") != (mode == "anime"):
            _fail("visual_class_mode_mismatch", "Text-to-image mode and visual class differ.")
        normalized["mode"] = mode
        normalized["size"] = _enum(
            fields.get("size", "1024x1024"),
            "size",
            frozenset({"1024x1024", "1024x768", "768x1024", "960x540", "540x960"}),
        )
        if "negative_prompt" in fields:
            normalized["negative_prompt"] = _string(
                fields["negative_prompt"], "negative_prompt", maximum=500
            )

    if operation is AutoIPContractOperation.IMAGE_TO_IMAGE:
        normalized["strength"] = _number(
            fields.get("strength", 1.0), "strength", minimum=0.1, maximum=1.0
        )
        normalized["size"] = _enum(
            fields.get("size", "original"),
            "size",
            frozenset({"original", "1024x1024", "1024x768", "768x1024", "960x540", "540x960"}),
        )
        normalized["mode"] = _enum(
            fields.get("mode", "standard"), "mode", frozenset({"standard", "hd_4k", "v3"})
        )
        if "negative_prompt" in fields:
            if normalized["mode"] != "hd_4k":
                _fail("negative_prompt_mode_invalid", "Image-to-image negative_prompt only takes effect in hd_4k mode.")
            normalized["negative_prompt"] = _string(
                fields["negative_prompt"], "negative_prompt", maximum=2000
            )

    if "style" in fields:
        normalized["style"] = _enum(fields["style"], "style", _IMAGE_STYLES)
    if "resolution" in fields:
        # 2026-09-16 線上目錄: 480p / 768p / 2k。720p 已經不賣了。
        normalized["resolution"] = _enum(
            fields["resolution"], "resolution", frozenset({"480p", "768p", "2k"})
        )
    if "duration_seconds" in fields:
        resolution = normalized["resolution"]
        minimum = 5 if resolution == "2k" or operation is AutoIPContractOperation.MULTI_SUBJECT_VIDEO else 2
        maximum = 15 if resolution == "480p" else 10
        normalized["duration_seconds"] = _integer(
            fields["duration_seconds"], "duration_seconds", minimum=minimum, maximum=maximum
        )
    if "aspect_ratio" in fields:
        normalized["aspect_ratio"] = _enum(
            fields["aspect_ratio"], "aspect_ratio", frozenset({"16:9", "9:16"})
        )
    if "seed" in fields:
        normalized["seed"] = _integer(
            fields["seed"], "seed", minimum=1, maximum=4_294_967_295
        )
    if "audio_start_time" in fields:
        normalized["audio_start_time"] = _number(
            fields["audio_start_time"], "audio_start_time", minimum=0, maximum=86_400
        )
    if "audio_duration" in fields:
        normalized["audio_duration"] = _number(
            fields["audio_duration"], "audio_duration", minimum=1, maximum=31
        )
    if operation is AutoIPContractOperation.DANCE:
        has_template = "dance_template_id" in fields
        if has_template == (dance_video_count == 1):
            _fail("dance_source_invalid", "Dance requires exactly one allowlisted motion source.")
        if has_template:
            normalized["dance_template_id"] = _integer(
                fields["dance_template_id"],
                "dance_template_id",
                minimum=1,
                maximum=2_147_483_647,
            )
    if operation is AutoIPContractOperation.MULTI_SUBJECT_VIDEO:
        if "descriptions[]" in fields:
            descriptions = fields["descriptions[]"]
            if (
                isinstance(descriptions, (str, bytes, bytearray))
                or not isinstance(descriptions, Sequence)
                or len(descriptions) > image_count
            ):
                _fail("description_count_invalid", "Subject descriptions do not match the image inputs.")
            normalized["descriptions[]"] = tuple(
                _string(item, "subject description", maximum=200)
                for item in descriptions
            ) + ("",) * (image_count - len(descriptions))
        if "dialogue_text" in fields:
            normalized["dialogue_text"] = _string(
                fields["dialogue_text"], "dialogue_text", maximum=1000
            )
        if voice_sample_count and not normalized.get("dialogue_text", "").strip():
            _fail("dialogue_required", "A voice sample requires dialogue text.")
        if "negative_prompt" in fields:
            normalized["negative_prompt"] = _string(
                fields["negative_prompt"], "negative_prompt", maximum=1000
            )
    return MappingProxyType(normalized)


def _normalize_inputs(
    operation: AutoIPContractOperation,
    raw: object,
    *,
    visual_class: str,
) -> tuple[NormalizedAutoIPInput, ...]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        _fail("schema_invalid", "Generation inputs must be an array.")
    rules = _INPUT_RULES[operation]
    if len(raw) > sum(rule.maximum for rule in rules.values()):
        _fail("input_count_invalid", "Generation inputs exceed the operation bound.")
    counts = {field_name: 0 for field_name in rules}
    result: list[NormalizedAutoIPInput] = []
    asset_version_ids: set[str] = set()
    for item in raw:
        value = _object(item, "generation input")
        _exact_keys(
            value,
            required=frozenset(
                {
                    "field_name",
                    "asset_version_id",
                    "sha256",
                    "mime_type",
                    "byte_size",
                    "signature_family",
                    "visual_class",
                }
            ),
            label="generation input",
        )
        field_name = value["field_name"]
        if not isinstance(field_name, str) or field_name not in rules:
            _fail("input_field_invalid", "Generation input field is not allowlisted for the operation.")
        rule = rules[field_name]
        counts[field_name] += 1
        if counts[field_name] > rule.maximum:
            _fail("duplicate_input", "Generation input field exceeds its exact cardinality.")
        asset_version_id = value["asset_version_id"]
        if not isinstance(asset_version_id, str) or not _ID_RE.fullmatch(asset_version_id):
            _fail("asset_version_id_invalid", "Generation input asset version identifier is invalid.")
        if asset_version_id in asset_version_ids:
            _fail("duplicate_input", "Generation input assets must be unique.")
        asset_version_ids.add(asset_version_id)
        sha256 = value["sha256"]
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            _fail("input_sha256_invalid", "Generation input digest is invalid.")
        mime_type = value["mime_type"]
        if not isinstance(mime_type, str) or mime_type not in rule.mime_types:
            _fail("input_mime_invalid", "Generation input MIME is not allowlisted for the field.")
        expected_family = signature_family_for_mime(mime_type)
        if expected_family is None or value["signature_family"] != expected_family:
            _fail("input_signature_invalid", "Generation input signature declaration is invalid.")
        byte_size = _integer(
            value["byte_size"], "input byte_size", minimum=1, maximum=rule.max_bytes
        )
        input_class = value["visual_class"]
        if rule.visual:
            input_class = _visual_class(input_class)
            if input_class != visual_class:
                _fail("input_visual_class_mismatch", "Generation input visual class differs from the request.")
        elif input_class is not None:
            _fail("input_visual_class_invalid", "Non-visual inputs must not claim a visual class.")
        result.append(
            NormalizedAutoIPInput(
                field_name=field_name,
                asset_version_id=asset_version_id,
                sha256=sha256,
                mime_type=mime_type,
                byte_size=byte_size,
                signature_family=expected_family,
                visual_class=input_class,
            )
        )
    for field_name, rule in rules.items():
        if not rule.minimum <= counts[field_name] <= rule.maximum:
            _fail("input_count_invalid", "Generation input cardinality does not match the operation.")
    return tuple(result)


def normalize_generation_request(value: object) -> NormalizedAutoIPRequest:
    """Normalize one server-side generation intent without side effects."""

    request = _object(value, "generation request")
    _exact_keys(
        request,
        required=frozenset(
            {"contract", "schema_version", "operation", "visual_class", "fields", "inputs"}
        ),
        label="generation request",
    )
    if (
        not isinstance(request["contract"], str)
        or request["contract"] != CONTRACT_ID
        or isinstance(request["schema_version"], bool)
        or not isinstance(request["schema_version"], int)
        or request["schema_version"] != REQUEST_SCHEMA_VERSION
    ):
        _fail("contract_version_invalid", "Generation request contract version is not supported.")
    try:
        operation = AutoIPContractOperation(request["operation"])
    except (TypeError, ValueError) as error:
        raise AutoIPContractValidationError(
            "operation_invalid", "Generation operation is not allowlisted by the pinned contract."
        ) from error
    visual_class = _visual_class(request["visual_class"])
    inputs = _normalize_inputs(operation, request["inputs"], visual_class=visual_class)
    counts: dict[str, int] = {}
    for item in inputs:
        counts[item.field_name] = counts.get(item.field_name, 0) + 1
    fields = _fields_for(
        operation,
        request["fields"],
        visual_class=visual_class,
        image_count=counts.get("images[]", 0),
        voice_sample_count=counts.get("voice_sample", 0),
        dance_video_count=counts.get("dance_video", 0),
    )
    return NormalizedAutoIPRequest(operation, visual_class, fields, inputs)


def _loads_strict_json(document: object, label: str) -> Any:
    if not isinstance(document, (str, bytes, bytearray)):
        _fail("json_invalid", f"{label} JSON is invalid.")
    if len(document) > _MAX_CONTRACT_JSON_BYTES:
        # Every Unicode scalar requires at least one UTF-8 byte.  Check before
        # encoding/copying so an attacker cannot force an oversized allocation.
        _fail("json_too_large", f"{label} JSON exceeds the contract bound.")
    try:
        if isinstance(document, str):
            encoded_size = 0
            for character in document:
                codepoint = ord(character)
                if codepoint <= 0x7F:
                    encoded_size += 1
                elif codepoint <= 0x7FF:
                    encoded_size += 2
                elif 0xD800 <= codepoint <= 0xDFFF:
                    _fail("json_invalid", f"{label} JSON is invalid.")
                elif codepoint <= 0xFFFF:
                    encoded_size += 3
                else:
                    encoded_size += 4
                if encoded_size > _MAX_CONTRACT_JSON_BYTES:
                    _fail("json_too_large", f"{label} JSON exceeds the contract bound.")
            encoded = document.encode("utf-8", "strict")
        elif isinstance(document, bytes):
            encoded = document
        else:
            view = memoryview(document)
            if view.nbytes > _MAX_CONTRACT_JSON_BYTES:
                _fail("json_too_large", f"{label} JSON exceeds the contract bound.")
            encoded = view.tobytes()
    except UnicodeEncodeError as error:
        raise AutoIPContractValidationError(
            "json_invalid", f"{label} JSON is invalid."
        ) from error
    if len(encoded) > _MAX_CONTRACT_JSON_BYTES:
        _fail("json_too_large", f"{label} JSON exceeds the contract bound.")

    try:
        text_document = encoded.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise AutoIPContractValidationError(
            "json_invalid", f"{label} JSON is invalid."
        ) from error

    if any(
        unicodedata.category(character) == "Cc" and character not in "\n\t"
        for character in text_document
    ):
        _fail("json_text_invalid", f"{label} JSON contains invalid Unicode text.")

    # Bound nesting before the recursive CPython decoder sees it.  This small
    # scanner only tracks structural characters outside JSON strings; syntax
    # correctness remains json.loads' responsibility below.
    depth = 0
    in_string = False
    escaped = False
    for character in text_document:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                _fail("json_depth_invalid", f"{label} JSON exceeds the nesting bound.")
        elif character in "]}":
            depth -= 1

    def bounded_integer(raw: str) -> int:
        if len(raw) > _MAX_JSON_NUMBER_CHARS:
            _fail("json_number_invalid", f"{label} JSON contains an unbounded number.")
        try:
            parsed = int(raw, 10)
        except (ValueError, OverflowError) as error:
            raise AutoIPContractValidationError(
                "json_number_invalid", f"{label} JSON contains an invalid number."
            ) from error
        if abs(parsed) > _MAX_JSON_INTEGER_ABS:
            _fail("json_number_invalid", f"{label} JSON contains an unbounded number.")
        return parsed

    def bounded_float(raw: str) -> float:
        if len(raw) > _MAX_JSON_NUMBER_CHARS:
            _fail("json_number_invalid", f"{label} JSON contains an unbounded number.")
        try:
            parsed = float(raw)
        except (ValueError, OverflowError) as error:
            raise AutoIPContractValidationError(
                "json_number_invalid", f"{label} JSON contains an invalid number."
            ) from error
        if not math.isfinite(parsed):
            _fail("json_number_invalid", f"{label} JSON contains an unbounded number.")
        return parsed

    def reject_constant(_raw: str) -> Any:
        _fail("json_number_invalid", f"{label} JSON contains an invalid number.")

    def validate_json_string(value: str) -> None:
        try:
            value.encode("utf-8", "strict")
        except UnicodeEncodeError as error:
            raise AutoIPContractValidationError(
                "json_text_invalid", f"{label} JSON contains invalid Unicode text."
            ) from error
        if (
            not unicodedata.is_normalized("NFC", value)
            or any(
                unicodedata.category(character) == "Cc"
                and character not in "\n\t"
                for character in value
            )
        ):
            _fail("json_text_invalid", f"{label} JSON contains invalid Unicode text.")

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, child in pairs:
            validate_json_string(key)
            if key in result:
                _fail("duplicate_field", f"{label} JSON contains a duplicate field.")
            result[key] = child
        return result

    try:
        decoded = json.loads(
            text_document,
            object_pairs_hook=strict_object,
            parse_int=bounded_integer,
            parse_float=bounded_float,
            parse_constant=reject_constant,
        )
    except AutoIPContractValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError, OverflowError, MemoryError) as error:
        raise AutoIPContractValidationError(
            "json_invalid", f"{label} JSON is invalid."
        ) from error

    tokens = 0
    stack: list[tuple[Any, int]] = [(decoded, 0)]
    while stack:
        child, child_depth = stack.pop()
        tokens += 1
        if tokens > _MAX_JSON_TOKENS:
            _fail("json_token_count_invalid", f"{label} JSON exceeds the token bound.")
        if child_depth > _MAX_JSON_DEPTH:
            _fail("json_depth_invalid", f"{label} JSON exceeds the nesting bound.")
        if isinstance(child, Mapping):
            tokens += len(child)
            if tokens > _MAX_JSON_TOKENS:
                _fail("json_token_count_invalid", f"{label} JSON exceeds the token bound.")
            stack.extend((nested, child_depth + 1) for nested in child.values())
        elif isinstance(child, list):
            stack.extend((nested, child_depth + 1) for nested in child)
        elif isinstance(child, str):
            validate_json_string(child)
    return decoded


def loads_generation_request(document: object) -> NormalizedAutoIPRequest:
    """Decode strict JSON, rejecting duplicate object keys before normalization."""

    return normalize_generation_request(_loads_strict_json(document, "Generation request"))


_ACCOUNT_ACTIVE_STATES = frozenset(
    {"active", "trial", "trialing", "paid", "enabled", "valid", "current"}
)
_ACCOUNT_INACTIVE_STATES = frozenset(
    {"inactive", "expired", "cancelled", "disabled", "past_due"}
)
_ACCOUNT_ACTIVE_TIERS = frozenset({"starter", "pro", "enterprise", "custom"})
_LEGACY_ACCOUNT_IDENTITY_ALIASES = frozenset(
    {"account_id", "user_id", "uuid", "id"}
)
_LEGACY_ACCOUNT_STATE_ALIASES = frozenset(
    {"plan_status", "subscription_status", "status", "active"}
)


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPAccount:
    account_identity: str | None = field(default=None, repr=False)
    plan_state: str = "unknown"
    plan_tier: str | None = None
    max_concurrent_tasks: int | None = None
    rate_limit_per_minute: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_identity": self.account_identity,
            "plan_state": self.plan_state,
            "plan_tier": self.plan_tier,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }


def _account_identity(value: object) -> str:
    if isinstance(value, bool):
        _fail("schema_invalid", "AutoIP account identifier is invalid.")
    if isinstance(value, int):
        return str(_integer(value, "account identifier", minimum=0, maximum=2**63 - 1))
    return _string(value, "account identifier", minimum=1, maximum=300)


def _account_plan_state(value: object) -> str:
    if value is True:
        return "active"
    if value is False:
        return "inactive"
    state = _string(value, "account plan state", minimum=1, maximum=40)
    if state in _ACCOUNT_ACTIVE_STATES:
        return "active"
    if state in _ACCOUNT_INACTIVE_STATES:
        return "inactive"
    _fail("schema_invalid", "AutoIP account plan state is unknown.")


def _has_legacy_account_alias(outer: Mapping[str, Any]) -> bool:
    """Select the historical projection only from an observed legacy alias."""

    sources: list[Mapping[str, Any]] = [outer]
    nested = outer.get("data")
    if isinstance(nested, Mapping):
        sources.append(nested)
    for source in tuple(sources):
        if set(source) & (
            _LEGACY_ACCOUNT_IDENTITY_ALIASES | _LEGACY_ACCOUNT_STATE_ALIASES
        ):
            return True
        for container_name in ("plan", "subscription"):
            container = source.get(container_name)
            if isinstance(container, Mapping) and (
                set(container) & _LEGACY_ACCOUNT_STATE_ALIASES
            ):
                return True
    return False


def _validate_official_account_string(value: object, label: str) -> None:
    _string(value, label, maximum=4_096)


def _validate_official_account_optional_string(value: object, label: str) -> None:
    if value is not None:
        _validate_official_account_string(value, label)


def _normalize_official_account_response(
    outer: Mapping[str, Any],
) -> NormalizedAutoIPAccount:
    """Validate the current OpenAPI ``/me`` projection without retaining it."""

    _required_keys(
        outer,
        required=frozenset({"api_key", "limits", "plan"}),
        label="account response",
    )
    api_key = _object(outer["api_key"], "account api_key")
    _required_keys(
        api_key,
        required=frozenset(
            {"created_at", "expires_at", "key_prefix", "last_used_at", "name"}
        ),
        label="account api_key",
    )
    _validate_official_account_string(api_key["created_at"], "account api_key created_at")
    _validate_official_account_optional_string(
        api_key["expires_at"], "account api_key expires_at"
    )
    _validate_official_account_string(api_key["key_prefix"], "account api_key key_prefix")
    _validate_official_account_optional_string(
        api_key["last_used_at"], "account api_key last_used_at"
    )
    _validate_official_account_string(api_key["name"], "account api_key name")

    limits = _object(outer["limits"], "account limits")
    _required_keys(
        limits,
        required=frozenset({"max_concurrent_tasks", "rate_limit_per_minute"}),
        label="account limits",
    )
    max_concurrent_tasks = _integer(
        limits["max_concurrent_tasks"],
        "account max_concurrent_tasks",
        minimum=1,
        maximum=2**63 - 1,
    )
    rate_limit_per_minute = _integer(
        limits["rate_limit_per_minute"],
        "account rate_limit_per_minute",
        minimum=1,
        maximum=2**63 - 1,
    )
    plan_tier = _enum(outer["plan"], "account plan", _ACCOUNT_ACTIVE_TIERS)
    # API-key identity remains discarded.  Plan and numeric limits are inert
    # scheduling facts required to respect the provider's actual account
    # capacity, so retain only those bounded values.
    return NormalizedAutoIPAccount(
        None,
        "active",
        plan_tier,
        max_concurrent_tasks,
        rate_limit_per_minute,
    )


def _normalize_legacy_account_response(
    outer: Mapping[str, Any],
) -> NormalizedAutoIPAccount:
    nested = outer.get("data")
    if nested is not None and not isinstance(nested, Mapping):
        _fail("schema_invalid", "AutoIP account data must be an object.")
    sources: list[Mapping[str, Any]] = []
    if isinstance(nested, Mapping):
        sources.append(nested)
    sources.append(outer)
    identity = _agreed_alias(
        sources,
        ("account_id", "user_id", "uuid", "id"),
        label="account identifier",
        normalize=_account_identity,
    )
    plan_sources = list(sources)
    for source in tuple(sources):
        for container_name in ("plan", "subscription"):
            container = source.get(container_name)
            if container is not None and not isinstance(container, Mapping):
                _fail("schema_invalid", "AutoIP account plan container must be an object.")
            if isinstance(container, Mapping):
                plan_sources.append(container)
    plan_state = _agreed_alias(
        plan_sources,
        ("plan_status", "subscription_status", "status", "active"),
        label="account plan state",
        normalize=_account_plan_state,
        default="unknown",
    )
    return NormalizedAutoIPAccount(identity, plan_state)


def normalize_account_response(value: object) -> NormalizedAutoIPAccount:
    outer = _object(value, "account response")
    if _has_legacy_account_alias(outer):
        return _normalize_legacy_account_response(outer)
    return _normalize_official_account_response(outer)


def loads_account_response(document: object) -> NormalizedAutoIPAccount:
    return normalize_account_response(_loads_strict_json(document, "Account response"))


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPCreditBalance:
    available_credits: int

    def to_dict(self) -> dict[str, int]:
        return {"available_credits": self.available_credits}


def normalize_credit_balance_response(value: object) -> NormalizedAutoIPCreditBalance:
    outer = _object(value, "credit balance response")
    nested = outer.get("data")
    if nested is not None and not isinstance(nested, Mapping):
        _fail("schema_invalid", "AutoIP credit balance data must be an object.")
    sources = (nested, outer) if isinstance(nested, Mapping) else (outer,)
    balance = _agreed_alias(
        sources,
        ("balance", "credits", "available_credits"),
        label="available credit balance",
        normalize=lambda item: _integer(
            item, "available credit balance", minimum=0, maximum=2**63 - 1
        ),
        required=True,
    )
    return NormalizedAutoIPCreditBalance(balance)


def loads_credit_balance_response(document: object) -> NormalizedAutoIPCreditBalance:
    return normalize_credit_balance_response(
        _loads_strict_json(document, "Credit balance response")
    )


_PRICING_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
_PRICING_OPERATIONS = frozenset(item.value for item in AutoIPContractOperation)
_OFFICIAL_PRICING_OPERATION_ORDER = (
    "text_to_image",
    "image_to_image",
    "image_to_video",
    "text_to_video",
    "lip_sync",
    "dance",
    "multi_subject_reference",
)
_OFFICIAL_PRICING_OPERATIONS: Mapping[str, str] = MappingProxyType(
    {
        "text_to_image": "text_to_image",
        "image_to_image": "image_to_image",
        "image_to_video": "image_to_video",
        "text_to_video": "text_to_video",
        "lip_sync": "lip_sync",
        "dance": "dance",
        "multi_subject_reference": "multi_subject_video",
    }
)
_OFFICIAL_PRICING_MODES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "text_to_image": ("anime", "hd_4k", "standard", "v2", "v3"),
        "image_to_image": ("hd_4k", "standard"),
    }
)
_OFFICIAL_PRICING_MATRIX_OPERATIONS = frozenset(
    {"image_to_video", "text_to_video", "multi_subject_reference"}
)
_OFFICIAL_PRICING_FLAT_OPERATIONS = frozenset({"lip_sync", "dance"})
_LEGACY_PRICING_ALIASES = frozenset({"items", "pricing", "prices"})
# 影片矩陣的座標不再寫死。2026-09-16 線上是 480p/768p/2k, 480p 還長到 15 秒,
# 舊的 ("480p", "720p") 連「必填」那一關都過不了。這裡只留一個地板: 至少得有
# 480p。哪一個 (解析度, 秒數) 組合有沒有價, 交給每一次報價自己去查目錄 ——
# 那是唯一該為「你要的這一格沒有價」負責的地方, 不是整本目錄的形狀檢查。
_OFFICIAL_PRICING_MIN_RESOLUTIONS = frozenset({"480p"})
_PRICING_DURATION_RE = re.compile(r"^[1-9][0-9]{0,3}$")
_OFFICIAL_API_PATH_PREFIX = "/api/v1"


def _pricing_token(value: object, label: str) -> str:
    token = _string(value, label, minimum=1, maximum=80)
    if not _PRICING_TOKEN_RE.fullmatch(token):
        _fail("schema_invalid", f"AutoIP {label} is not a canonical token.")
    return token


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPPricingEntry:
    operation: str
    credits: int
    dimensions: tuple[tuple[str, str | int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "credits": self.credits,
            "dimensions": dict(self.dimensions),
        }


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPPricingCatalog:
    entries: tuple[NormalizedAutoIPPricingEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}


def _pricing_rows(value: object) -> tuple[Mapping[str, Any], ...]:
    rows = _sequence(value, "pricing rows", maximum=200)
    return tuple(_object(row, "pricing row") for row in rows)


def _official_pricing_credits(value: object, label: str) -> int:
    return _integer(value, label, minimum=0, maximum=2**31 - 1)


def _official_pricing_endpoint(
    value: object,
    *,
    internal_operation: str,
) -> None:
    endpoint = _string(value, "pricing endpoint", minimum=1, maximum=200)
    expected = (
        _OFFICIAL_API_PATH_PREFIX
        + AUTOIP_ROUTES[internal_operation].path_template
    )
    if endpoint != expected:
        _fail("schema_invalid", "AutoIP pricing endpoint is not allowlisted.")


def _normalize_official_pricing_response(
    outer: Mapping[str, Any],
) -> NormalizedAutoIPPricingCatalog:
    _required_keys(
        outer,
        required=frozenset({"data"}),
        label="pricing response",
    )
    data = _object(outer["data"], "pricing data")
    # 外層多一個 meta 是加法, 無所謂。但混進舊 schema 的別名就是兩份會互相
    # 矛盾的價目表 —— 那不是加法, 靜靜挑一邊就會挑到錯的價。
    if (set(outer) | set(data)) & _LEGACY_PRICING_ALIASES:
        _fail("schema_invalid", "AutoIP pricing mixes legacy and current envelopes.")
    # 認得的操作一個都不能少; 認不得的 (image_to_prompt / multi_angle / keyframe…)
    # 直接不看 —— 我們沒有那些的轉接器, 記進目錄反而會讓路由以為自己送得出去。
    _required_keys(
        data,
        required=frozenset(_OFFICIAL_PRICING_OPERATIONS),
        label="pricing data",
    )

    entries: list[NormalizedAutoIPPricingEntry] = []
    seen: set[tuple[str, tuple[tuple[str, str | int], ...]]] = set()

    def append(
        operation: str,
        credits: int,
        dimensions: tuple[tuple[str, str | int], ...] = (),
    ) -> None:
        canonical_dimensions = tuple(sorted(dimensions))
        identity = (operation, canonical_dimensions)
        if identity in seen:
            _fail("schema_invalid", "AutoIP pricing contains a duplicate offering.")
        seen.add(identity)
        entries.append(
            NormalizedAutoIPPricingEntry(
                operation,
                credits,
                canonical_dimensions,
            )
        )

    for provider_operation in _OFFICIAL_PRICING_OPERATION_ORDER:
        internal_operation = _OFFICIAL_PRICING_OPERATIONS[provider_operation]
        offering = _object(
            data[provider_operation],
            f"pricing {provider_operation} offering",
        )
        if provider_operation in _OFFICIAL_PRICING_FLAT_OPERATIONS:
            _required_keys(
                offering,
                required=frozenset({"endpoint", "credits"}),
                label=f"pricing {provider_operation} offering",
            )
            _official_pricing_endpoint(
                offering["endpoint"], internal_operation=internal_operation
            )
            append(
                internal_operation,
                _official_pricing_credits(
                    offering["credits"], f"pricing {provider_operation} credits"
                ),
            )
            continue

        if provider_operation in _OFFICIAL_PRICING_MODES:
            _required_keys(
                offering,
                required=frozenset({"endpoint", "credits_by_mode"}),
                label=f"pricing {provider_operation} offering",
            )
            _official_pricing_endpoint(
                offering["endpoint"], internal_operation=internal_operation
            )
            credits_by_mode = _object(
                offering["credits_by_mode"],
                f"pricing {provider_operation} credits_by_mode",
            )
            modes = _OFFICIAL_PRICING_MODES[provider_operation]
            _required_keys(
                credits_by_mode,
                required=frozenset(modes),
                label=f"pricing {provider_operation} credits_by_mode",
            )
            for mode in modes:
                append(
                    internal_operation,
                    _official_pricing_credits(
                        credits_by_mode[mode],
                        f"pricing {provider_operation} {mode} credits",
                    ),
                    (("mode", mode),),
                )
            # v3 is optional in older price snapshots; only advertise a quote
            # when today's provider response actually includes its price.
            if provider_operation == "image_to_image" and "v3" in credits_by_mode:
                append(
                    internal_operation,
                    _official_pricing_credits(credits_by_mode["v3"], "pricing image_to_image v3 credits"),
                    (("mode", "v3"),),
                )
            continue

        if provider_operation not in _OFFICIAL_PRICING_MATRIX_OPERATIONS:
            _fail("schema_invalid", "AutoIP pricing operation is not allowlisted.")
        _required_keys(
            offering,
            required=frozenset(
                {"endpoint", "credits_by_resolution_and_duration"}
            ),
            label=f"pricing {provider_operation} offering",
        )
        _official_pricing_endpoint(
            offering["endpoint"], internal_operation=internal_operation
        )
        matrix = _object(
            offering["credits_by_resolution_and_duration"],
            f"pricing {provider_operation} credits_by_resolution_and_duration",
        )
        _required_keys(
            matrix,
            required=_OFFICIAL_PRICING_MIN_RESOLUTIONS,
            label=f"pricing {provider_operation} resolution matrix",
        )
        for resolution in sorted(matrix):
            _pricing_token(resolution, f"pricing {provider_operation} resolution")
            durations = _object(
                matrix[resolution],
                f"pricing {provider_operation} {resolution} duration matrix",
            )
            if not durations:
                _fail("schema_invalid", "AutoIP pricing duration matrix is empty.")
            for duration in sorted(durations):
                # 「05」這種鍵仍然是壞的: 同一個秒數會有兩個寫法, 目錄就會有
                # 兩筆價。只收沒有前導零的十進位。
                if not _PRICING_DURATION_RE.fullmatch(duration):
                    _fail("schema_invalid", "AutoIP pricing duration is not canonical.")
                append(
                    internal_operation,
                    _official_pricing_credits(
                        durations[duration],
                        f"pricing {provider_operation} {resolution} {duration}s credits",
                    ),
                    (
                        ("resolution", resolution),
                        ("duration_seconds", int(duration)),
                    ),
                )
    return NormalizedAutoIPPricingCatalog(tuple(entries))


def _normalize_legacy_pricing_response(
    value: object,
) -> NormalizedAutoIPPricingCatalog:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw_rows = _pricing_rows(value)
    else:
        outer = _object(value, "pricing response")
        nested = outer.get("data")
        sources: list[Mapping[str, Any]] = []
        if isinstance(nested, Mapping):
            sources.append(nested)
        elif isinstance(nested, Sequence) and not isinstance(
            nested, (str, bytes, bytearray)
        ):
            sources.append({"items": nested})
        elif nested is not None:
            _fail("schema_invalid", "AutoIP pricing data has an invalid shape.")
        sources.append(outer)
        raw_rows = _agreed_alias(
            sources,
            ("items", "pricing", "prices"),
            label="pricing rows",
            normalize=_pricing_rows,
            required=True,
        )

    entries: list[NormalizedAutoIPPricingEntry] = []
    seen: set[tuple[str, tuple[tuple[str, str | int], ...]]] = set()
    for row in raw_rows:
        operation = _agreed_alias(
            (row,),
            ("operation", "capability", "tool", "feature", "type"),
            label="pricing operation",
            normalize=lambda item: _pricing_token(item, "pricing operation"),
            required=True,
        )
        if operation not in _PRICING_OPERATIONS:
            _fail("schema_invalid", "AutoIP pricing operation is not allowlisted.")
        credits = _agreed_alias(
            (row,),
            ("credits", "price", "cost"),
            label="pricing credits",
            normalize=lambda item: _integer(
                item, "pricing credits", minimum=0, maximum=2**31 - 1
            ),
            required=True,
        )
        dimensions: list[tuple[str, str | int]] = []
        for name in ("mode", "model", "size", "resolution"):
            if name in row and row[name] is not None:
                dimensions.append((name, _pricing_token(row[name], f"pricing {name}")))
        duration = _agreed_alias(
            (row,),
            ("duration_seconds", "duration"),
            label="pricing duration",
            normalize=lambda item: _integer(
                item, "pricing duration", minimum=0, maximum=86_400
            ),
        )
        if duration is not None:
            dimensions.append(("duration_seconds", duration))
        dimensions_tuple = tuple(sorted(dimensions))
        identity = (operation, dimensions_tuple)
        if identity in seen:
            _fail("schema_invalid", "AutoIP pricing contains a duplicate offering.")
        seen.add(identity)
        entries.append(NormalizedAutoIPPricingEntry(operation, credits, dimensions_tuple))
    return NormalizedAutoIPPricingCatalog(tuple(entries))


def normalize_pricing_response(value: object) -> NormalizedAutoIPPricingCatalog:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _normalize_legacy_pricing_response(value)
    outer = _object(value, "pricing response")
    nested = outer.get("data")
    legacy_aliases = _LEGACY_PRICING_ALIASES
    if isinstance(nested, Mapping) and (
        set(nested) & frozenset(_OFFICIAL_PRICING_OPERATIONS)
    ):
        # A response carrying any current operation is current-schema data.
        # Its exact outer/data checks below must see and reject legacy aliases
        # rather than letting a mixed envelope silently select the old parser.
        return _normalize_official_pricing_response(outer)
    if set(outer) & legacy_aliases or (
        isinstance(nested, Mapping) and set(nested) & legacy_aliases
    ) or (
        isinstance(nested, Sequence)
        and not isinstance(nested, (str, bytes, bytearray))
    ):
        return _normalize_legacy_pricing_response(outer)
    return _normalize_official_pricing_response(outer)


def loads_pricing_response(document: object) -> NormalizedAutoIPPricingCatalog:
    return normalize_pricing_response(_loads_strict_json(document, "Pricing response"))


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPSubmission:
    task_uuid: str
    credits_charged: int
    remaining_credits: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_uuid": self.task_uuid,
            "credits_charged": self.credits_charged,
            "remaining_credits": self.remaining_credits,
        }


def normalize_submission_response(value: object) -> NormalizedAutoIPSubmission:
    """Project a successful charge receipt and discard provider extensions."""

    outer = _object(value, "submission response")
    nested = outer.get("data")
    if nested is not None and not isinstance(nested, Mapping):
        _fail("schema_invalid", "Submission response data must be an object.")
    sources = (nested, outer) if isinstance(nested, Mapping) else (outer,)
    task_uuid = _agreed_alias(
        sources,
        ("task_uuid", "task_id", "id"),
        label="submission task identifier",
        normalize=_task_identifier,
        required=True,
    )
    credits = _agreed_alias(
        sources,
        ("credits_charged",),
        label="submission credits_charged",
        normalize=lambda item: _integer(
            item, "credits_charged", minimum=0, maximum=2**31 - 1
        ),
        required=True,
    )
    remaining = _agreed_alias(
        sources,
        ("remaining_credits",),
        label="submission remaining_credits",
        normalize=lambda item: _integer(
            item, "remaining_credits", minimum=0, maximum=2**63 - 1
        ),
        required=True,
    )
    return NormalizedAutoIPSubmission(
        task_uuid,
        credits,
        remaining,
    )


def loads_submission_response(document: object) -> NormalizedAutoIPSubmission:
    """Strictly decode a submission response without losing duplicate keys."""

    return normalize_submission_response(_loads_strict_json(document, "Submission response"))


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPOutput:
    index: int
    provider_mime_hint: str | None = field(default=None, repr=False)
    provider_byte_size_hint: int | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            # Provider MIME, size, URLs and filenames are deliberately absent.
            # The authenticated download lane must classify bytes, enforce its
            # own limit, hash the complete body, then authorize adoption.
            "mime_type": None,
            "signature_family": None,
            "signature_verified": False,
            "byte_size": None,
            "requires_authorization": True,
            "requires_authenticated_download": True,
            "requires_media_validation": True,
        }


def normalize_output_declaration(
    value: object,
    *,
    fallback_index: object | None = None,
) -> NormalizedAutoIPOutput:
    """Project one output without treating provider metadata as byte evidence."""

    output = _object(value, "task output")
    index = _agreed_alias(
        (output,),
        ("index", "output_index"),
        label="task output index",
        normalize=lambda item: _integer(
            item, "output index", minimum=0, maximum=999_999
        ),
        default=None,
    )
    if index is None:
        if fallback_index is None:
            _fail("output_index_missing", "Task output has no route-safe index.")
        index = _integer(fallback_index, "output index", minimum=0, maximum=999_999)

    mime_hint = _agreed_alias(
        (output,),
        ("mime_type", "content_type", "type"),
        label="provider MIME hint",
        normalize=lambda item: _string(
            item, "provider MIME hint", maximum=200, strip=True
        ),
    )
    byte_size_hint = _agreed_alias(
        (output,),
        ("byte_size", "size_bytes", "bytes"),
        label="provider output size hint",
        normalize=lambda item: _integer(
            item, "provider output size hint", minimum=1, maximum=_MAX_OUTPUT_BYTES
        ),
    )

    # These values are never retained.  Bounds prevent a direct-normalizer
    # caller from bypassing the JSON document cap with pathological metadata.
    for ignored_name in (
        "download_url",
        "url",
        "filename",
        "file_name",
        "name",
    ):
        if ignored_name in output and output[ignored_name] is not None:
            _string(
                output[ignored_name],
                "untrusted task output metadata",
                maximum=4096,
            )
    return NormalizedAutoIPOutput(index, mime_hint, byte_size_hint)


# ⛔ 2026-09-16: 這一組本來是**閘門** (`_enum(item, "task status", _TASK_STATUSES)`)。
# 那是 `/me` 跟 `/pricing` 同一個病的第四處 —— 回應側用封閉世界驗證, 對方多回一個
# 狀態字我們就整支停機: reconciler 在 4 小時內被炸掉兩次, 每次都要 systemd 重啟一輪。
# 狀態值是**對方的詞彙**, 不是我們的契約。所以現在只驗形狀 (見 `_task_status`),
# 這一組降級成「我們認得、有對照表的值」, 給 `autoip.py::_task_state()` 用。
# ⛔ 不要把它改回閘門。不認得的狀態一律當「還沒結束」繼續輪詢, 不要 raise。
_TASK_STATUSES = frozenset(
    {
        "created",
        "submitted",
        "pending",
        "queued",
        "processing",
        "running",
        "completed",
        "failed",
        "cancelled",
        "timeout",
    }
)


# 狀態只驗形狀: 非空的 token。值由 `_task_state()` 對照, 對不到就當非終局。
_TASK_STATUS_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")


def _task_status(value: object) -> str:
    token = _string(value, "task status", minimum=1, maximum=40)
    if not _TASK_STATUS_RE.fullmatch(token):
        _fail("schema_invalid", "AutoIP task status is not a canonical token.")
    return token


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPTask:
    task_uuid: str
    status: str
    credits_charged: int | None
    remaining_credits: int | None
    outputs: tuple[NormalizedAutoIPOutput, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_uuid": self.task_uuid,
            "status": self.status,
            "credits_charged": self.credits_charged,
            "remaining_credits": self.remaining_credits,
            "outputs": [item.to_dict() for item in self.outputs],
        }


def normalize_task_status(
    value: object,
    *,
    expected_task_id: object,
) -> NormalizedAutoIPTask:
    """Project status for the task already bound by the authenticated route.

    A provider body may omit its task id.  If it repeats ``task_uuid``,
    ``task_id`` or ``id``, the value is corroborating only and must match the
    route-bound id exactly.
    """

    if not isinstance(expected_task_id, str) or not _TASK_RE.fullmatch(expected_task_id):
        _fail("task_uuid_invalid", "Expected AutoIP task identifier is invalid.")
    outer = _object(value, "task status")
    nested = outer.get("data")
    if nested is not None and not isinstance(nested, Mapping):
        _fail("schema_invalid", "Task status data must be an object.")
    sources = (nested, outer) if isinstance(nested, Mapping) else (outer,)
    body_identifier = _agreed_alias(
        sources,
        ("task_uuid", "task_id", "id"),
        label="task status identifier",
        normalize=_task_identifier,
    )
    if body_identifier is not None and body_identifier != expected_task_id:
        _fail("task_uuid_mismatch", "AutoIP task body differs from the route-bound task.")

    status = _agreed_alias(
        sources,
        ("status", "state"),
        label="task lifecycle status",
        normalize=_task_status,
        required=True,
    )
    raw_outputs = _agreed_alias(
        sources,
        ("outputs",),
        label="task outputs",
        normalize=lambda item: _sequence(
            item, "task outputs", maximum=_MAX_TASK_OUTPUTS
        ),
        default=(),
    )
    if (
        isinstance(raw_outputs, (str, bytes, bytearray))
        or not isinstance(raw_outputs, Sequence)
        or len(raw_outputs) > _MAX_TASK_OUTPUTS
    ):
        _fail("output_count_invalid", "Task outputs are outside the contract bounds.")
    outputs = tuple(
        normalize_output_declaration(item, fallback_index=position)
        for position, item in enumerate(raw_outputs)
    )
    indexes = [item.index for item in outputs]
    if len(indexes) != len(set(indexes)):
        _fail("duplicate_output", "Task output indexes must be unique.")
    if status != "completed" and outputs:
        _fail("output_state_invalid", "Only a completed task may expose outputs.")
    credits = _agreed_alias(
        sources,
        ("credits_charged",),
        label="task credits_charged",
        normalize=lambda item: _integer(
            item, "credits_charged", minimum=0, maximum=2**31 - 1
        ),
    )
    remaining = _agreed_alias(
        sources,
        ("remaining_credits",),
        label="task remaining_credits",
        normalize=lambda item: _integer(
            item, "remaining_credits", minimum=0, maximum=2**63 - 1
        ),
    )
    return NormalizedAutoIPTask(
        expected_task_id,
        status,
        credits,
        remaining,
        outputs,
    )


def loads_task_status(
    document: object,
    *,
    expected_task_id: object,
) -> NormalizedAutoIPTask:
    """Strictly decode task JSON without losing top-level or output duplicates."""

    return normalize_task_status(
        _loads_strict_json(document, "Task status"),
        expected_task_id=expected_task_id,
    )


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPTaskSummary:
    task_uuid: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {"task_uuid": self.task_uuid, "status": self.status}


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPTaskPage:
    page: int
    per_page: int
    total: int | None
    total_pages: int | None
    tasks: tuple[NormalizedAutoIPTaskSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "per_page": self.per_page,
            "total": self.total,
            "total_pages": self.total_pages,
            "tasks": [task.to_dict() for task in self.tasks],
        }


def _sequence(value: object, label: str, *, maximum: int) -> Sequence[Any]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) > maximum
    ):
        _fail("schema_invalid", f"{label} must be a bounded array.")
    return value


def normalize_task_list_page(
    value: object,
    *,
    expected_page: object | None = None,
    expected_per_page: object | None = None,
) -> NormalizedAutoIPTaskPage:
    """Project a bounded task page; prompts and output metadata are discarded."""

    root = _object(value, "task list")
    nested_data = root.get("data")
    if nested_data is not None and (
        isinstance(nested_data, (str, bytes, bytearray))
        or not isinstance(nested_data, (Mapping, Sequence))
    ):
        _fail("schema_invalid", "Task list data has an invalid shape.")
    task_sources: list[Mapping[str, Any]] = []
    payload = nested_data if isinstance(nested_data, Mapping) else root
    if isinstance(nested_data, Mapping):
        task_sources.append(nested_data)
    elif isinstance(nested_data, Sequence) and not isinstance(
        nested_data, (str, bytes, bytearray)
    ):
        task_sources.append({"tasks": nested_data})
    task_sources.append(root)
    task_values = _agreed_alias(
        task_sources,
        ("tasks", "items"),
        label="task list items",
        normalize=lambda item: tuple(
            _sequence(item, "task list items", maximum=_MAX_TASK_PAGE_SIZE)
        ),
        required=True,
    )

    tasks: list[NormalizedAutoIPTaskSummary] = []
    seen_task_ids: set[str] = set()
    for raw_task in task_values:
        task = _object(raw_task, "task list item")
        identifier = _agreed_alias(
            (task,),
            ("task_uuid", "task_id", "id"),
            label="task list item identifier",
            normalize=_task_identifier,
            required=True,
        )
        if identifier in seen_task_ids:
            _fail("duplicate_task", "Task list contains a duplicate task identifier.")
        seen_task_ids.add(identifier)
        state = _agreed_alias(
            (task,),
            ("status", "state"),
            label="task list item status",
            normalize=_task_status,
            required=True,
        )
        tasks.append(NormalizedAutoIPTaskSummary(identifier, state))

    metadata_sources: list[Mapping[str, Any]] = [payload, root]
    for container in (payload, root):
        for container_name in ("meta", "pagination"):
            metadata = container.get(container_name)
            if metadata is not None and not isinstance(metadata, Mapping):
                _fail("schema_invalid", "Task list metadata must be an object.")
            if isinstance(metadata, Mapping):
                metadata_sources.append(metadata)
    if expected_page is not None:
        expected_page = _integer(expected_page, "expected page", minimum=1, maximum=999_999)
    if expected_per_page is not None:
        expected_per_page = _integer(
            expected_per_page, "expected per_page", minimum=1, maximum=_MAX_TASK_PAGE_SIZE
        )
    page = _agreed_alias(
        metadata_sources,
        ("page", "current_page"),
        label="task page",
        normalize=lambda item: _integer(
            item, "task page", minimum=1, maximum=999_999
        ),
        default=expected_page if expected_page is not None else 1,
    )
    per_page = _agreed_alias(
        metadata_sources,
        ("per_page", "page_size"),
        label="task per_page",
        normalize=lambda item: _integer(
            item, "task per_page", minimum=1, maximum=_MAX_TASK_PAGE_SIZE
        ),
        default=(
            expected_per_page
            if expected_per_page is not None
            else max(1, len(tasks))
        ),
    )
    if expected_page is not None and page != expected_page:
        _fail("task_page_mismatch", "Task page differs from the requested page.")
    if expected_per_page is not None and per_page != expected_per_page:
        _fail("task_page_mismatch", "Task page size differs from the requested page size.")
    total = _agreed_alias(
        metadata_sources,
        ("total", "total_count"),
        label="task total",
        normalize=lambda item: _integer(
            item, "task total", minimum=0, maximum=2**31 - 1
        ),
    )
    total_pages = _agreed_alias(
        metadata_sources,
        ("total_pages", "last_page"),
        label="task total pages",
        normalize=lambda item: _integer(
            item, "task total pages", minimum=0, maximum=999_999
        ),
    )
    if len(tasks) > per_page:
        _fail("task_page_invalid", "Task page contains more rows than per_page.")
    if total is not None and total < len(tasks):
        _fail("task_page_invalid", "Task total is smaller than the returned rows.")
    if (total is None) != (total_pages is None):
        _fail("task_page_invalid", "Task totals must be supplied as one consistent pair.")
    if total is not None and total_pages is not None:
        expected_total_pages = (total + per_page - 1) // per_page
        # AutoIP currently reports ``last_page: 1`` for an empty Laravel-style
        # paginator, while older captured responses used the mathematical
        # value ``total_pages: 0``.  Both are unambiguous only for the exact
        # empty first page; retain strict arithmetic for every non-empty page.
        valid_total_pages = {0, 1} if total == 0 else {expected_total_pages}
        if total_pages not in valid_total_pages:
            _fail("task_page_invalid", "Task total_pages is inconsistent with total and per_page.")
        if total == 0:
            if page != 1 or tasks:
                _fail("task_page_invalid", "A zero-page result must be page one and empty.")
        else:
            if not 1 <= page <= total_pages:
                _fail("task_page_invalid", "Task page exceeds the exact result range.")
            offset = (page - 1) * per_page
            expected_rows = min(per_page, total - offset)
            if expected_rows < 1 or len(tasks) != expected_rows:
                _fail("task_page_invalid", "Task page row count is inconsistent with its offset.")
    elif page > 1 and not tasks:
        # An empty later page without totals cannot prove that the requested
        # offset exists; do not silently treat it as a valid result page.
        _fail("task_page_invalid", "An uncounted later task page cannot be empty.")
    return NormalizedAutoIPTaskPage(page, per_page, total, total_pages, tuple(tasks))


def loads_task_list_page(
    document: object,
    *,
    expected_page: object | None = None,
    expected_per_page: object | None = None,
) -> NormalizedAutoIPTaskPage:
    return normalize_task_list_page(
        _loads_strict_json(document, "Task list"),
        expected_page=expected_page,
        expected_per_page=expected_per_page,
    )


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPDanceTemplate:
    template_id: int
    name: str
    duration_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "duration_seconds": self.duration_seconds,
            # Catalog transport review is not a Castle product visibility,
            # visual-class, consent, option, or quality-golden grant.
            "product_visibility_granted": False,
            "selectable": False,
        }


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPDanceCatalog:
    templates: tuple[NormalizedAutoIPDanceTemplate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_allowlisted": True,
            "product_visibility_granted": False,
            "templates": [template.to_dict() for template in self.templates],
        }


def normalize_dance_template_catalog(value: object) -> NormalizedAutoIPDanceCatalog:
    """Project provider templates for review, never directly for product use."""

    if isinstance(value, Mapping):
        root = _object(value, "dance template catalog")
        nested_data = root.get("data")
        if nested_data is not None and (
            isinstance(nested_data, (str, bytes, bytearray))
            or not isinstance(nested_data, (Mapping, Sequence))
        ):
            _fail("schema_invalid", "Dance template catalog data has an invalid shape.")
        template_sources: list[Mapping[str, Any]] = []
        if isinstance(nested_data, Mapping):
            template_sources.append(nested_data)
        elif isinstance(nested_data, Sequence) and not isinstance(
            nested_data, (str, bytes, bytearray)
        ):
            template_sources.append({"templates": nested_data})
        template_sources.append(root)
        template_values = _agreed_alias(
            template_sources,
            ("templates", "items"),
            label="dance templates",
            normalize=lambda item: tuple(
                _sequence(item, "dance templates", maximum=_MAX_DANCE_TEMPLATES)
            ),
            required=True,
        )
    else:
        template_values = _sequence(
            value, "dance templates", maximum=_MAX_DANCE_TEMPLATES
        )
    templates: list[NormalizedAutoIPDanceTemplate] = []
    seen_ids: set[int] = set()
    for raw_template in template_values:
        template = _object(raw_template, "dance template")
        template_id = _agreed_alias(
            (template,),
            ("id", "template_id"),
            label="dance template id",
            normalize=lambda item: _integer(
                item, "dance template id", minimum=1, maximum=2**31 - 1
            ),
            required=True,
        )
        if template_id in seen_ids:
            _fail("duplicate_template", "Dance template catalog contains a duplicate id.")
        seen_ids.add(template_id)
        normalized_name = _agreed_alias(
            (template,),
            ("name", "title"),
            label="dance template name",
            normalize=lambda item: _string(
                item, "dance template name", minimum=1, maximum=200, strip=True
            ),
            required=True,
        )
        duration_seconds = _agreed_alias(
            (template,),
            ("duration_seconds", "duration"),
            label="dance template duration",
            normalize=lambda item: _integer(
                item, "dance template duration", minimum=1, maximum=3_600
            ),
            required=True,
        )
        for ignored_name in (
            "thumbnail",
            "thumbnail_url",
            "preview_url",
            "preview_video_url",
            "video_url",
        ):
            if ignored_name in template and template[ignored_name] is not None:
                _string(
                    template[ignored_name],
                    "untrusted dance template metadata",
                    maximum=4096,
                )
        templates.append(
            NormalizedAutoIPDanceTemplate(template_id, normalized_name, duration_seconds)
        )
    return NormalizedAutoIPDanceCatalog(tuple(templates))


def loads_dance_template_catalog(document: object) -> NormalizedAutoIPDanceCatalog:
    return normalize_dance_template_catalog(
        _loads_strict_json(document, "Dance template catalog")
    )


@dataclass(frozen=True, slots=True)
class NormalizedAutoIPErrorEnvelope:
    error_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": "AutoIP request failed; provider response content was redacted.",
        }


def normalize_error_envelope(value: object) -> NormalizedAutoIPErrorEnvelope:
    """Accept the pinned error shape while dropping reflected provider text."""

    outer = _object(value, "error response")
    nested = outer.get("data")
    if nested is not None and not isinstance(nested, Mapping):
        _fail("schema_invalid", "AutoIP error response data must be an object.")
    sources = (nested, outer) if isinstance(nested, Mapping) else (outer,)

    def bounded_raw_digest(raw: object, *, label: str, maximum: int) -> bytes:
        if isinstance(raw, str):
            canonical: object = _string(raw, label, maximum=maximum)
        elif label == "error_code" and raw is None:
            canonical = raw
        elif label == "error_code" and isinstance(raw, bool):
            canonical = raw
        elif label == "error_code" and isinstance(raw, int):
            if abs(raw) > _MAX_JSON_INTEGER_ABS:
                _fail("schema_invalid", "AutoIP error_code is outside its numeric bound.")
            canonical = raw
        elif label == "error_code" and isinstance(raw, float):
            if not math.isfinite(raw) or abs(raw) > _MAX_JSON_INTEGER_ABS:
                _fail("schema_invalid", "AutoIP error_code is outside its numeric bound.")
            canonical = raw
        else:
            _fail("schema_invalid", f"AutoIP {label} has an invalid bounded shape.")
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", "strict")
        return hashlib.sha256(encoded).digest()

    def agreed_raw_alias(name: str, *, label: str, maximum: int) -> object:
        values = [
            mapping[name]
            for mapping in sources
            if name in mapping
        ]
        if not values:
            _fail("required_field_missing", f"AutoIP {label} is missing.")
        digests = [
            bounded_raw_digest(raw, label=label, maximum=maximum)
            for raw in values
        ]
        if any(not hmac.compare_digest(digests[0], digest) for digest in digests[1:]):
            _fail("alias_mismatch", f"AutoIP {label} aliases disagree.")
        return values[0]

    raw_code = agreed_raw_alias("error_code", label="error_code", maximum=120)
    agreed_raw_alias("message", label="provider error message", maximum=2_000)
    code = raw_code if isinstance(raw_code, str) and raw_code in _PUBLIC_ERROR_CODES else "provider_error"
    return NormalizedAutoIPErrorEnvelope(code)


def loads_error_envelope(document: object) -> NormalizedAutoIPErrorEnvelope:
    return normalize_error_envelope(_loads_strict_json(document, "Error response"))


def assert_contract_matches_legacy_operation_paths() -> None:
    """Fail closed if the older client enum silently drifts from this freeze."""

    from .autoip import AutoIPOperation

    expected = {
        operation.value: AUTOIP_ROUTES[operation.value].path_template
        for operation in AutoIPContractOperation
    }
    actual = {operation.value: operation.path for operation in AutoIPOperation}
    if actual != expected:
        _fail("legacy_contract_drift", "Legacy AutoIP operation paths differ from the pinned freeze.")


__all__ = (
    "AUTOIP_ROUTES",
    "CONTRACT_ID",
    "PACKAGE_INTEGRITY",
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "REQUEST_SCHEMA_VERSION",
    "REST_SKILL_REVISION",
    "AutoIPContractOperation",
    "AutoIPContractValidationError",
    "AutoIPRoute",
    "NormalizedAutoIPInput",
    "NormalizedAutoIPAccount",
    "NormalizedAutoIPCreditBalance",
    "NormalizedAutoIPDanceCatalog",
    "NormalizedAutoIPDanceTemplate",
    "NormalizedAutoIPErrorEnvelope",
    "NormalizedAutoIPOutput",
    "NormalizedAutoIPPricingCatalog",
    "NormalizedAutoIPPricingEntry",
    "NormalizedAutoIPRequest",
    "NormalizedAutoIPSubmission",
    "NormalizedAutoIPTask",
    "NormalizedAutoIPTaskPage",
    "NormalizedAutoIPTaskSummary",
    "assert_contract_matches_legacy_operation_paths",
    "loads_dance_template_catalog",
    "loads_account_response",
    "loads_credit_balance_response",
    "loads_error_envelope",
    "loads_generation_request",
    "loads_pricing_response",
    "loads_submission_response",
    "loads_task_list_page",
    "loads_task_status",
    "normalize_dance_template_catalog",
    "normalize_account_response",
    "normalize_credit_balance_response",
    "normalize_error_envelope",
    "normalize_generation_request",
    "normalize_pricing_response",
    "normalize_output_declaration",
    "normalize_submission_response",
    "normalize_task_list_page",
    "normalize_task_status",
    "render_route",
)
