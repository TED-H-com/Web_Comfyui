"""Strict zero-execution API helpers for series bibles and episodes."""

from __future__ import annotations

from typing import Any, Mapping

from .series_continuity_store import SeriesContinuityStore


CREATE_SERIES_VERSION = "castle.series-create.v1"
APPEND_EPISODE_VERSION = "castle.series-episode-append.v1"
SERIES_RESPONSE_VERSION = "castle.series.response.v1"
EPISODE_RESPONSE_VERSION = "castle.series-episode.response.v1"


def _exact(body: Mapping[str, Any], fields: set[str]) -> None:
    if not isinstance(body, Mapping) or set(body) != fields:
        raise ValueError("series request fields are invalid")


def _authority() -> dict[str, Any]:
    return {
        "owner_scoped": True,
        "continuity_enforced": True,
        "provider_submission_authorized": False,
        "gpu_execution_authorized": False,
        "credits_authorized": 0,
        "publishing_authorized": False,
    }


def create_series_api(
    store: SeriesContinuityStore, owner_user_id: str, body: Mapping[str, Any],
) -> dict[str, Any]:
    _exact(body, {"schema_version", "series_id", "title", "entity_ids", "canon"})
    if body["schema_version"] != CREATE_SERIES_VERSION:
        raise ValueError("series request version is invalid")
    bible = store.create_bible(
        owner_user_id, body["series_id"], body["title"], body["entity_ids"], body["canon"],
    )
    return {"schema_version": SERIES_RESPONSE_VERSION, "ok": True, "bible": bible, "authority": _authority()}


def get_series_api(
    store: SeriesContinuityStore, owner_user_id: str, series_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": SERIES_RESPONSE_VERSION, "ok": True,
        "bible": store.get_bible(owner_user_id, series_id), "authority": _authority(),
    }


def append_series_episode_api(
    store: SeriesContinuityStore, owner_user_id: str, series_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    _exact(body, {
        "schema_version", "episode_id", "ordinal", "state_before", "event_summary",
        "state_after", "director_plan_hash", "pack_proposal_hash", "expected_bible_hash",
    })
    if body["schema_version"] != APPEND_EPISODE_VERSION:
        raise ValueError("episode request version is invalid")
    episode = store.append_episode(
        owner_user_id, series_id, episode_id=body["episode_id"], ordinal=body["ordinal"],
        state_before=body["state_before"], event_summary=body["event_summary"],
        state_after=body["state_after"], director_plan_hash=body["director_plan_hash"],
        pack_proposal_hash=body["pack_proposal_hash"], expected_bible_hash=body["expected_bible_hash"],
    )
    return {
        "schema_version": EPISODE_RESPONSE_VERSION, "ok": True,
        "episode": episode, "authority": _authority(),
    }


__all__ = [
    "APPEND_EPISODE_VERSION", "CREATE_SERIES_VERSION", "EPISODE_RESPONSE_VERSION",
    "SERIES_RESPONSE_VERSION", "append_series_episode_api", "create_series_api", "get_series_api",
]
