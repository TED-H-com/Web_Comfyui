"""Strict human brief contract and deterministic storyboard draft compiler."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from provider_fabric.models import VisualClass


BRIEF_VERSION = "castle.production-brief.v1"
DRAFT_VERSION = "castle.storyboard-draft.v1"
MAX_BRIEF_BYTES = 256 * 1024
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_ASPECTS = frozenset({"16:9", "9:16", "1:1"})
_PLATFORMS = frozenset({"shorts-vertical", "social-square", "web-landscape", "private-review"})
_PLATFORM_ASPECTS = MappingProxyType({
    "shorts-vertical": "9:16",
    "social-square": "1:1",
    "web-landscape": "16:9",
})
_SECRET_KEY_RE = re.compile(r"(?:api.?key|authorization|cookie|credential|password|secret|token)", re.I)


def production_usage_scope(platform: str) -> str:
    """Map a validated production platform to its rights usage scope."""

    if platform not in _PLATFORMS:
        raise ValueError("platform is unsupported")
    return "private" if platform == "private-review" else "social"


class ProductionMode(str, Enum):
    SINGLE = "single"
    SERIES = "series"
    DRAMA = "drama"


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    result = unicodedata.normalize("NFC", value).strip()
    if not result or len(result) > maximum or any(ord(char) < 32 and char not in "\n\t" for char in result):
        raise ValueError(f"{label} is blank, too long, or contains controls")
    return result


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a stable identifier")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} fields do not match the contract")
    if any(_SECRET_KEY_RE.search(key) for key in value):
        raise ValueError(f"{label} contains a forbidden credential field")


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{label} is outside its allowed range")
    return result


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BriefCharacter:
    character_id: str
    display_name: str
    visual_class: VisualClass
    profile_asset_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "character_id", _id(self.character_id, "character_id"))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name", 160))
        if not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        if self.visual_class not in {VisualClass.ANIME, VisualClass.REALISTIC, VisualClass.ILLUSTRATION}:
            raise ValueError("brief characters must have a known visual class")
        object.__setattr__(self, "profile_asset_id", _id(self.profile_asset_id, "profile_asset_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "display_name": self.display_name,
            "visual_class": self.visual_class.value,
            "profile_asset_id": self.profile_asset_id,
        }


@dataclass(frozen=True, slots=True)
class BriefSoundEffect:
    cue_id: str
    asset_version_id: str
    start_seconds: float
    source_start_seconds: float
    duration_seconds: float
    gain_db: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "cue_id", _id(self.cue_id, "sound effect cue_id"))
        object.__setattr__(self, "asset_version_id", _id(self.asset_version_id, "sound effect asset_version_id"))
        object.__setattr__(self, "start_seconds", _number(self.start_seconds, "sound effect start", 0, 60))
        object.__setattr__(self, "source_start_seconds", _number(self.source_start_seconds, "sound effect source start", 0, 86400))
        object.__setattr__(self, "duration_seconds", _number(self.duration_seconds, "sound effect duration", 0.05, 60))
        object.__setattr__(self, "gain_db", _number(self.gain_db, "sound effect gain", -36, 6))

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id, "asset_version_id": self.asset_version_id,
            "start_seconds": self.start_seconds,
            "source_start_seconds": self.source_start_seconds,
            "duration_seconds": self.duration_seconds, "gain_db": self.gain_db,
        }


@dataclass(frozen=True, slots=True)
class StoryBeat:
    beat_id: str
    summary: str
    action: str
    camera: str
    audio: str
    duration_seconds: float
    character_ids: tuple[str, ...]
    sound_effects: tuple[BriefSoundEffect, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "beat_id", _id(self.beat_id, "beat_id"))
        for field, maximum in (("summary", 1000), ("action", 1600), ("camera", 500), ("audio", 1000)):
            object.__setattr__(self, field, _text(getattr(self, field), field, maximum))
        object.__setattr__(self, "duration_seconds", _number(self.duration_seconds, "beat duration", 1, 60))
        ids = tuple(_id(item, "beat character_id") for item in self.character_ids)
        if not ids or len(ids) > 8 or len(ids) != len(set(ids)):
            raise ValueError("a beat requires 1-8 unique characters")
        object.__setattr__(self, "character_ids", ids)
        effects = tuple(self.sound_effects)
        if len(effects) > 16 or len({item.cue_id for item in effects}) != len(effects):
            raise ValueError("a beat supports at most 16 unique sound effects")
        if any(item.start_seconds + item.duration_seconds > self.duration_seconds for item in effects):
            raise ValueError("sound effect exceeds its beat")
        object.__setattr__(self, "sound_effects", effects)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "beat_id": self.beat_id,
            "summary": self.summary,
            "action": self.action,
            "camera": self.camera,
            "audio": self.audio,
            "duration_seconds": self.duration_seconds,
            "character_ids": list(self.character_ids),
        }
        if self.sound_effects:
            value["sound_effects"] = [item.to_dict() for item in self.sound_effects]
        return value


@dataclass(frozen=True, slots=True)
class ProductionBrief:
    mode: ProductionMode
    title: str
    premise: str
    audience: str
    platform: str
    language: str
    aspect_ratio: str
    series_id: str | None
    characters: tuple[BriefCharacter, ...]
    beats: tuple[StoryBeat, ...]
    soundtrack_asset_version_id: str | None = None
    soundtrack_gain_db: float | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProductionBrief":
        if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
            raise ValueError("production brief must be an object")
        required = {
            "schema_version", "mode", "title", "premise", "audience", "platform",
            "language", "aspect_ratio", "series_id", "characters", "beats",
        }
        allowed = required | {"soundtrack_asset_version_id", "soundtrack_gain_db"}
        if not required.issubset(raw) or not set(raw).issubset(allowed):
            raise ValueError("production brief fields do not match the contract")
        if raw["schema_version"] != BRIEF_VERSION:
            raise ValueError("production brief version is unsupported")
        if not isinstance(raw["characters"], list) or not isinstance(raw["beats"], list):
            raise ValueError("characters and beats must be arrays")
        characters = []
        for item in raw["characters"]:
            if not isinstance(item, Mapping):
                raise ValueError("brief character must be an object")
            _exact(item, {"character_id", "display_name", "visual_class", "profile_asset_id"}, "brief character")
            characters.append(BriefCharacter(**item))
        beats = []
        for item in raw["beats"]:
            if not isinstance(item, Mapping):
                raise ValueError("story beat must be an object")
            required_beat = {"beat_id", "summary", "action", "camera", "audio", "duration_seconds", "character_ids"}
            if frozenset(item) not in {frozenset(required_beat), frozenset(required_beat | {"sound_effects"})}:
                raise ValueError("story beat fields do not match the contract")
            effects = item.get("sound_effects", [])
            if not isinstance(effects, list):
                raise ValueError("story beat sound_effects must be an array")
            parsed_effects = []
            for effect in effects:
                if not isinstance(effect, Mapping):
                    raise ValueError("sound effect must be an object")
                _exact(effect, {"cue_id", "asset_version_id", "start_seconds", "source_start_seconds", "duration_seconds", "gain_db"}, "sound effect")
                parsed_effects.append(BriefSoundEffect(**effect))
            beats.append(StoryBeat(**{**item, "character_ids": tuple(item["character_ids"]), "sound_effects": tuple(parsed_effects)}))
        return cls(
            mode=ProductionMode(raw["mode"]),
            title=raw["title"],
            premise=raw["premise"],
            audience=raw["audience"],
            platform=raw["platform"],
            language=raw["language"],
            aspect_ratio=raw["aspect_ratio"],
            series_id=raw["series_id"],
            characters=tuple(characters),
            beats=tuple(beats),
            soundtrack_asset_version_id=raw.get("soundtrack_asset_version_id"),
            soundtrack_gain_db=raw.get("soundtrack_gain_db"),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ProductionMode):
            object.__setattr__(self, "mode", ProductionMode(self.mode))
        for field, maximum in (("title", 200), ("premise", 2000), ("audience", 500), ("language", 32)):
            object.__setattr__(self, field, _text(getattr(self, field), field, maximum))
        if self.platform not in _PLATFORMS or self.aspect_ratio not in _ASPECTS:
            raise ValueError("platform or aspect ratio is unsupported")
        expected_aspect = _PLATFORM_ASPECTS.get(self.platform)
        if expected_aspect is not None and self.aspect_ratio != expected_aspect:
            raise ValueError("platform and aspect ratio contradict each other")
        series_id = self.series_id
        if self.mode is ProductionMode.SINGLE:
            if series_id is not None:
                raise ValueError("single productions cannot claim a series")
        else:
            object.__setattr__(self, "series_id", _id(series_id, "series_id"))
        if not 1 <= len(self.characters) <= 16 or not 1 <= len(self.beats) <= 120:
            raise ValueError("brief character or beat count is outside its bound")
        character_ids = {item.character_id for item in self.characters}
        if len(character_ids) != len(self.characters):
            raise ValueError("brief character IDs must be unique")
        beat_ids = {item.beat_id for item in self.beats}
        if len(beat_ids) != len(self.beats):
            raise ValueError("story beat IDs must be unique")
        if any(not set(beat.character_ids).issubset(character_ids) for beat in self.beats):
            raise ValueError("story beat references an unknown character")
        if self.soundtrack_asset_version_id is None:
            if self.soundtrack_gain_db is not None:
                raise ValueError("soundtrack gain requires an asset version")
        else:
            object.__setattr__(
                self, "soundtrack_asset_version_id",
                _id(self.soundtrack_asset_version_id, "soundtrack_asset_version_id"),
            )
            object.__setattr__(
                self, "soundtrack_gain_db",
                _number(self.soundtrack_gain_db, "soundtrack_gain_db", -36, 0),
            )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": BRIEF_VERSION,
            "mode": self.mode.value,
            "title": self.title,
            "premise": self.premise,
            "audience": self.audience,
            "platform": self.platform,
            "language": self.language,
            "aspect_ratio": self.aspect_ratio,
            "series_id": self.series_id,
            "characters": [item.to_dict() for item in self.characters],
            "beats": [item.to_dict() for item in self.beats],
        }
        if self.soundtrack_asset_version_id is not None:
            value.update({
                "soundtrack_asset_version_id": self.soundtrack_asset_version_id,
                "soundtrack_gain_db": self.soundtrack_gain_db,
            })
        return value


def compile_storyboard_draft(brief: ProductionBrief) -> Mapping[str, Any]:
    if not isinstance(brief, ProductionBrief):
        raise TypeError("brief must be a ProductionBrief")
    characters = {item.character_id: item for item in brief.characters}
    scenes = []
    for ordinal, beat in enumerate(brief.beats, 1):
        classes = sorted({characters[item].visual_class.value for item in beat.character_ids})
        blockers = []
        if len(classes) != 1:
            blockers.append("mixed_visual_classes_require_separate_shots")
        scenes.append({
            "scene_id": f"scene-{ordinal:03d}",
            "ordinal": ordinal,
            "beat_id": beat.beat_id,
            "summary": beat.summary,
            "shot": {
                "shot_id": f"shot-{ordinal:03d}",
                "duration_seconds": beat.duration_seconds,
                "character_ids": list(beat.character_ids),
                "visual_class": classes[0] if len(classes) == 1 else None,
                "frame_intent": beat.summary,
                "observable_action": beat.action,
                "camera": beat.camera,
                "audio_intent": beat.audio,
                **({"sound_effects": [item.to_dict() for item in beat.sound_effects]} if beat.sound_effects else {}),
                "input_asset_ids": [characters[item].profile_asset_id for item in beat.character_ids],
                "required_capability": (
                    "multi_subject_video" if len(beat.character_ids) > 1 else "image_to_video"
                ),
                "route_variant_id": None,
                "required_options": [],
                "blockers": blockers,
            },
        })
    brief_dict = brief.to_dict()
    draft = {
        "schema_version": DRAFT_VERSION,
        "brief_hash": _hash(brief_dict),
        "mode": brief.mode.value,
        "series_id": brief.series_id,
        "title": brief.title,
        "premise": brief.premise,
        "audience": brief.audience,
        "platform": brief.platform,
        "language": brief.language,
        "aspect_ratio": brief.aspect_ratio,
        "target_duration_seconds": sum(item.duration_seconds for item in brief.beats),
        "characters": [item.to_dict() for item in brief.characters],
        "scenes": scenes,
        "unresolved": [
            "character_rights_and_consent",
            "immutable_asset_versions",
            "provider_routes_and_goldens",
            "authoritative_quotes",
            "claim_sources",
            "continuity_state",
        ],
        "execution_authorized": False,
    }
    if brief.soundtrack_asset_version_id is not None:
        draft["soundtrack"] = {
            "asset_version_id": brief.soundtrack_asset_version_id,
            "gain_db": brief.soundtrack_gain_db,
        }
    return MappingProxyType({**draft, "draft_hash": _hash(draft)})


def compile_brief_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_BRIEF_BYTES:
        raise ValueError("production brief exceeds its byte limit")
    return dict(compile_storyboard_draft(ProductionBrief.from_dict(raw)))


def bind_canonical_brief_characters(
    raw: Mapping[str, Any],
    canonical_characters: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Require browser character claims to equal server-owned registry data."""

    if not isinstance(raw, Mapping) or not isinstance(canonical_characters, Mapping):
        raise TypeError("brief and canonical characters must be mappings")
    result = dict(raw)
    submitted = raw.get("characters")
    if not isinstance(submitted, list) or not submitted:
        raise ValueError("brief characters are required")
    rebound = []
    observed = set()
    for item in submitted:
        if not isinstance(item, Mapping):
            raise ValueError("brief character must be an object")
        character_id = item.get("character_id")
        if not isinstance(character_id, str) or character_id in observed:
            raise ValueError("brief character identity is invalid")
        observed.add(character_id)
        canonical = canonical_characters.get(character_id)
        if not isinstance(canonical, Mapping):
            raise ValueError("brief character is not available")
        expected = {
            "character_id": character_id,
            "display_name": canonical.get("display_name"),
            "visual_class": canonical.get("visual_class"),
            "profile_asset_id": canonical.get("profile_asset_id"),
        }
        if dict(item) != expected:
            raise ValueError("brief character claims do not match the canonical registry")
        rebound.append(expected)
    result["characters"] = rebound
    return result


__all__ = [
    "BRIEF_VERSION", "DRAFT_VERSION", "MAX_BRIEF_BYTES", "BriefCharacter",
    "BriefSoundEffect", "ProductionBrief", "ProductionMode", "StoryBeat", "bind_canonical_brief_characters", "compile_brief_request",
    "compile_storyboard_draft", "production_usage_scope",
]
