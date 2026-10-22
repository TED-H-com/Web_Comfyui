"""Compile reviewed shot outputs into one deterministic edit timeline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ProductionPack
from .production_state import ProductionPhase, ProductionState, ReviewDecision


EDIT_TIMELINE_VERSION = "castle.edit-timeline.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_DIMENSIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class EditClip:
    shot_id: str
    ordinal: int
    asset_version_id: str
    asset_sha256: str
    source_duration_seconds: float
    timeline_start_seconds: float
    timeline_end_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "shot_id": self.shot_id,
            "ordinal": self.ordinal,
            "asset_version_id": self.asset_version_id,
            "asset_sha256": self.asset_sha256,
            "source_duration_seconds": self.source_duration_seconds,
            "timeline_start_seconds": self.timeline_start_seconds,
            "timeline_end_seconds": self.timeline_end_seconds,
        }


@dataclass(frozen=True, slots=True)
class CaptionCue:
    cue_id: str
    shot_id: str
    speaker_entity_id: str
    text: str
    language: str
    start_seconds: float
    end_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "cue_id": self.cue_id,
            "shot_id": self.shot_id,
            "speaker_entity_id": self.speaker_entity_id,
            "text": self.text,
            "language": self.language,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
        }


@dataclass(frozen=True, slots=True)
class EditSoundEffect:
    cue_id: str
    shot_id: str
    asset_version_id: str
    asset_sha256: str
    timeline_start_seconds: float
    source_start_seconds: float
    duration_seconds: float
    gain_db: float

    def to_dict(self) -> dict[str, object]:
        return {
            "cue_id": self.cue_id, "shot_id": self.shot_id,
            "asset_version_id": self.asset_version_id,
            "asset_sha256": self.asset_sha256,
            "timeline_start_seconds": self.timeline_start_seconds,
            "source_start_seconds": self.source_start_seconds,
            "duration_seconds": self.duration_seconds, "gain_db": self.gain_db,
        }


@dataclass(frozen=True, slots=True)
class EditTimeline:
    project_id: str
    owner_user_id: str
    pack_hash: str
    production_state_hash: str
    width: int
    height: int
    fps: int
    total_duration_seconds: float
    clips: tuple[EditClip, ...]
    captions: tuple[CaptionCue, ...]
    sound_effects: tuple[EditSoundEffect, ...]
    subtitle_profile: str
    audio_mix_profile: str
    branding_profile: str
    deliverable_profile: str
    soundtrack_asset_version_id: str | None
    soundtrack_asset_sha256: str | None
    soundtrack_gain_db: float | None
    output_token: str
    timeline_hash: str
    schema_version: str = EDIT_TIMELINE_VERSION

    def _material(self) -> dict[str, object]:
        material = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "owner_user_id": self.owner_user_id,
            "pack_hash": self.pack_hash,
            "production_state_hash": self.production_state_hash,
            "video": {"width": self.width, "height": self.height, "fps": self.fps},
            "total_duration_seconds": self.total_duration_seconds,
            "clips": [item.to_dict() for item in self.clips],
            "captions": [item.to_dict() for item in self.captions],
            "profiles": {
                "subtitle": self.subtitle_profile,
                "audio_mix": self.audio_mix_profile,
                "branding": self.branding_profile,
                "deliverable": self.deliverable_profile,
            },
            "output_token": self.output_token,
        }
        if self.soundtrack_asset_version_id is not None:
            material["soundtrack"] = {
                "asset_version_id": self.soundtrack_asset_version_id,
                "asset_sha256": self.soundtrack_asset_sha256,
                "gain_db": self.soundtrack_gain_db,
            }
        if self.sound_effects:
            material["sound_effects"] = [item.to_dict() for item in self.sound_effects]
        return material

    def __post_init__(self) -> None:
        if self.schema_version != EDIT_TIMELINE_VERSION:
            raise ValueError("edit timeline version is unsupported")
        if _SHA.fullmatch(self.pack_hash) is None or _SHA.fullmatch(self.production_state_hash) is None:
            raise ValueError("edit timeline source identity is invalid")
        if _SHA.fullmatch(self.timeline_hash) is None or _hash(self._material()) != self.timeline_hash:
            raise ValueError("edit timeline hash does not match its contents")
        if len(self.clips) == 0 or len({item.shot_id for item in self.clips}) != len(self.clips):
            raise ValueError("edit timeline clips are invalid")
        if self.soundtrack_asset_version_id is None:
            if self.soundtrack_asset_sha256 is not None or self.soundtrack_gain_db is not None:
                raise ValueError("edit timeline soundtrack evidence is incomplete")
        elif (
            not isinstance(self.soundtrack_asset_sha256, str)
            or _SHA.fullmatch(self.soundtrack_asset_sha256) is None
            or not isinstance(self.soundtrack_gain_db, (int, float))
            or isinstance(self.soundtrack_gain_db, bool)
            or not -36 <= float(self.soundtrack_gain_db) <= 0
        ):
            raise ValueError("edit timeline soundtrack evidence is invalid")
        object.__setattr__(self, "clips", tuple(self.clips))
        object.__setattr__(self, "captions", tuple(self.captions))
        if len(self.sound_effects) > 8000 or len({item.cue_id for item in self.sound_effects}) != len(self.sound_effects):
            raise ValueError("edit timeline sound effects are invalid")
        object.__setattr__(self, "sound_effects", tuple(self.sound_effects))

    def to_dict(self) -> dict[str, object]:
        return {**self._material(), "timeline_hash": self.timeline_hash}


def _build(values: Mapping[str, Any]) -> EditTimeline:
    material = {
        "schema_version": EDIT_TIMELINE_VERSION,
        "project_id": values["project_id"],
        "owner_user_id": values["owner_user_id"],
        "pack_hash": values["pack_hash"],
        "production_state_hash": values["production_state_hash"],
        "video": {
            "width": values["width"], "height": values["height"], "fps": values["fps"],
        },
        "total_duration_seconds": values["total_duration_seconds"],
        "clips": [item.to_dict() for item in values["clips"]],
        "captions": [item.to_dict() for item in values["captions"]],
        "profiles": {
            "subtitle": values["subtitle_profile"],
            "audio_mix": values["audio_mix_profile"],
            "branding": values["branding_profile"],
            "deliverable": values["deliverable_profile"],
        },
        "output_token": values["output_token"],
    }
    if values["soundtrack_asset_version_id"] is not None:
        material["soundtrack"] = {
            "asset_version_id": values["soundtrack_asset_version_id"],
            "asset_sha256": values["soundtrack_asset_sha256"],
            "gain_db": values["soundtrack_gain_db"],
        }
    if values["sound_effects"]:
        material["sound_effects"] = [item.to_dict() for item in values["sound_effects"]]
    return EditTimeline(**values, timeline_hash=_hash(material))


def compile_edit_timeline(pack: ProductionPack, state: ProductionState) -> EditTimeline:
    """Use only accepted immutable shot versions; no filename or route fallback."""

    if not isinstance(pack, ProductionPack) or not isinstance(state, ProductionState):
        raise TypeError("pack and production state are required")
    if state.phase not in {ProductionPhase.EDITING, ProductionPhase.DELIVERY_REVIEW}:
        raise ValueError("production must pass story review before editing")
    if (
        state.project_id != pack.project.project_id
        or state.pack_hash != pack.content_hash
        or state.shot_ids != tuple(
            item.shot_id for item in sorted(pack.shots, key=lambda row: (row.scene_id, row.ordinal))
        )
    ):
        raise ValueError("production state and pack identity differ")
    dimensions = _DIMENSIONS.get(pack.project.aspect_ratio)
    if dimensions is None:
        raise ValueError("deliverable aspect ratio has no qualified edit profile")

    clips = []
    captions = []
    effects = []
    cursor = 0.0
    ordered_shots = sorted(pack.shots, key=lambda row: (row.scene_id, row.ordinal, row.shot_id))
    for ordinal, shot in enumerate(ordered_shots, start=1):
        accepted = [
            item for item in state.outputs[shot.shot_id]
            if item.decision is ReviewDecision.ACCEPTED
        ]
        if len(accepted) != 1:
            raise ValueError("every edit clip requires exactly one accepted output")
        selected = accepted[0]
        start = round(cursor, 6)
        end = round(start + shot.duration_seconds, 6)
        clips.append(EditClip(
            shot.shot_id, ordinal, selected.asset_version_id, selected.asset_sha256,
            shot.duration_seconds, start, end,
        ))
        for cue_index, cue in enumerate(shot.dialogue, start=1):
            cue_start = round(start + cue.start_seconds, 6)
            cue_end = round(start + cue.end_seconds, 6)
            if cue_end > end + 0.000001:
                raise ValueError("caption cue exceeds its accepted shot")
            captions.append(CaptionCue(
                f"caption:{shot.shot_id}:{cue_index}", shot.shot_id,
                cue.speaker_entity_id, cue.text, cue.language, cue_start, cue_end,
            ))
        for effect in shot.sound_effects:
            effects.append(EditSoundEffect(
                effect.cue_id, shot.shot_id, effect.asset_version_id,
                effect.asset_sha256, round(start + effect.start_seconds, 6),
                effect.source_start_seconds, effect.duration_seconds, effect.gain_db,
            ))
        cursor = end
    output_token = hashlib.sha256(
        f"{state.owner_user_id}\x00{state.project_id}\x00{state.state_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return _build(MappingProxyType({
        "project_id": state.project_id,
        "owner_user_id": state.owner_user_id,
        "pack_hash": state.pack_hash,
        "production_state_hash": state.state_hash,
        "width": dimensions[0], "height": dimensions[1], "fps": 24,
        "total_duration_seconds": round(cursor, 6),
        "clips": tuple(clips), "captions": tuple(captions),
        "sound_effects": tuple(effects),
        "subtitle_profile": pack.project.subtitle_profile,
        "audio_mix_profile": pack.project.audio_mix_profile,
        "branding_profile": pack.project.branding_profile,
        "deliverable_profile": pack.project.deliverable_profile,
        "soundtrack_asset_version_id": pack.project.soundtrack_asset_version_id,
        "soundtrack_asset_sha256": pack.project.soundtrack_asset_sha256,
        "soundtrack_gain_db": pack.project.soundtrack_gain_db,
        "output_token": output_token,
    }))


__all__ = [
    "CaptionCue", "EDIT_TIMELINE_VERSION", "EditClip", "EditSoundEffect", "EditTimeline",
    "compile_edit_timeline",
]
