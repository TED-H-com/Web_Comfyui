"""Deterministic editorial plan derived from a Castle storyboard draft."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


DIRECTOR_PLAN_VERSION = "castle.director-plan.v1"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _role(mode: str, index: int, count: int) -> str:
    if count == 1:
        return "hook_and_payoff" if mode == "single" else "episode_micro_arc"
    if index == 0:
        return {"single": "hook", "series": "recap_hook", "drama": "setup"}[mode]
    if index == count - 1:
        return {"single": "payoff", "series": "next_episode_hook", "drama": "cliffhanger"}[mode]
    if mode == "drama" and count >= 4 and index == count - 2:
        return "reversal"
    return "development" if mode != "drama" else "escalation"


def compile_director_plan(draft: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(draft, Mapping) or draft.get("schema_version") != "castle.storyboard-draft.v1":
        raise ValueError("storyboard draft is invalid")
    mode = draft.get("mode")
    scenes = draft.get("scenes")
    draft_hash = draft.get("draft_hash")
    if mode not in {"single", "series", "drama"} or not isinstance(scenes, list) or not scenes:
        raise ValueError("storyboard mode or scenes are invalid")
    if not isinstance(draft_hash, str) or len(draft_hash) != 64:
        raise ValueError("storyboard hash is invalid")
    shots = []
    previous_characters: set[str] = set()
    for index, scene in enumerate(scenes):
        shot = scene.get("shot") if isinstance(scene, Mapping) else None
        if not isinstance(shot, Mapping):
            raise ValueError("storyboard shot is invalid")
        character_ids = shot.get("character_ids")
        duration = shot.get("duration_seconds")
        if not isinstance(character_ids, list) or not character_ids or not isinstance(duration, (int, float)):
            raise ValueError("storyboard shot fields are invalid")
        current = set(character_ids)
        transition_in = "opening" if index == 0 else (
            "cut_on_action" if current.intersection(previous_characters) else "clean_cut"
        )
        transition_out = "ending_hold" if index == len(scenes) - 1 else "continue"
        shots.append({
            "shot_id": shot.get("shot_id"),
            "narrative_role": _role(mode, index, len(scenes)),
            "pacing": "brisk" if duration <= 4 else "balanced" if duration <= 8 else "deliberate",
            "transition_in": transition_in,
            "transition_out": transition_out,
            "audio_bridge": "none" if index == 0 else "room_tone_bridge",
            "continuity_locks": [
                f"{character_id}.{field}"
                for character_id in sorted(current)
                for field in ("identity", "wardrobe", "voice")
            ],
        })
        previous_characters = current
    plan = {
        "schema_version": DIRECTOR_PLAN_VERSION,
        "draft_hash": draft_hash,
        "mode": mode,
        "editorial_strategy": {
            "single": "hook_develop_payoff",
            "series": "recap_develop_next_hook",
            "drama": "setup_escalate_reverse_cliffhanger",
        }[mode],
        "shots": shots,
        "execution_authorized": False,
    }
    return {**plan, "plan_hash": _hash(plan)}


__all__ = ["DIRECTOR_PLAN_VERSION", "compile_director_plan"]
