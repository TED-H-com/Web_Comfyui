"""Bind one verified provider output to its signed production shot lineage."""

from __future__ import annotations

import re
from typing import Mapping

from .production_state import ProductionPhase, record_shot_output
from .production_store import ProductionStateStore, ProductionStoreConflict


_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


def _binding(instruction: Mapping[str, object]) -> Mapping[str, object] | None:
    value = instruction.get("production_binding")
    if value is None:
        return None
    expected = {
        "schema_version", "project_id", "pack_hash", "state_hash", "shot_id",
        "route_variant_id",
    }
    if (
        not isinstance(value, Mapping) or set(value) != expected
        or value.get("schema_version") != "castle.production-binding.v1"
        or any(
            not isinstance(value.get(field), str)
            or _ID.fullmatch(str(value[field])) is None
            for field in ("project_id", "shot_id", "route_variant_id")
        )
        or any(
            not isinstance(value.get(field), str)
            or _SHA.fullmatch(str(value[field])) is None
            for field in ("pack_hash", "state_hash")
        )
        or value.get("shot_id") != instruction.get("shot_id")
    ):
        raise ValueError("signed production binding is invalid")
    return value


def bind_verified_output(
    store: ProductionStateStore,
    *,
    owner_user_id: str,
    instruction: Mapping[str, object],
    asset_version_id: str,
    asset_sha256: str,
    task_id: str,
) -> str | None:
    """Append an output version exactly once; unbound generation remains library-only."""

    if not isinstance(store, ProductionStateStore) or not isinstance(instruction, Mapping):
        raise TypeError("production store and creative instruction are required")
    binding = _binding(instruction)
    if binding is None:
        return None
    project_id = str(binding["project_id"])
    if not store.contains_revision(
        owner_user_id,
        project_id,
        state_hash=str(binding["state_hash"]),
        pack_hash=str(binding["pack_hash"]),
    ):
        raise ValueError("signed production revision is unavailable")
    current = store.get(owner_user_id, project_id)
    if current.pack_hash != binding["pack_hash"]:
        raise ValueError("current production pack differs from output binding")
    shot_id = str(binding["shot_id"])
    if shot_id not in current.outputs:
        raise ValueError("signed production shot is unavailable")
    for version in current.outputs[shot_id]:
        if version.asset_version_id == asset_version_id:
            if (
                version.asset_sha256 != asset_sha256
                or version.task_id != task_id
                or version.route_variant_id != binding["route_variant_id"]
            ):
                raise ProductionStoreConflict("production output identity conflicts")
            return current.state_hash
    if current.phase not in {ProductionPhase.GENERATING, ProductionPhase.STORY_REVIEW}:
        raise ValueError("production is not accepting generated shot outputs")
    changed = record_shot_output(
        current,
        shot_id=shot_id,
        asset_version_id=asset_version_id,
        asset_sha256=asset_sha256,
        route_variant_id=str(binding["route_variant_id"]),
        task_id=task_id,
    )
    return store.append(
        changed, expected_previous_hash=current.state_hash
    ).state_hash


__all__ = ["bind_verified_output"]
