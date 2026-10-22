"""Hash-bound resumable state for multi-shot Castle productions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ProductionPack


STATE_VERSION = "castle.production-state.v2"
LEGACY_STATE_VERSION = "castle.production-state.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ProductionPhase(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    ASSETS_LOCKED = "assets_locked"
    GENERATING = "generating"
    STORY_REVIEW = "story_review"
    EDITING = "editing"
    DELIVERY_REVIEW = "delivery_review"
    READY = "ready"


class ReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


_NEXT = {
    ProductionPhase.DRAFT: ProductionPhase.PLANNED,
    ProductionPhase.PLANNED: ProductionPhase.ASSETS_LOCKED,
    ProductionPhase.ASSETS_LOCKED: ProductionPhase.GENERATING,
    ProductionPhase.GENERATING: ProductionPhase.STORY_REVIEW,
    ProductionPhase.STORY_REVIEW: ProductionPhase.EDITING,
    ProductionPhase.EDITING: ProductionPhase.DELIVERY_REVIEW,
    ProductionPhase.DELIVERY_REVIEW: ProductionPhase.READY,
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ShotOutputVersion:
    shot_id: str
    version: int
    asset_version_id: str
    asset_sha256: str
    route_variant_id: str
    task_id: str
    decision: ReviewDecision | None = None
    review_note: str = ""

    def __post_init__(self) -> None:
        for field in ("shot_id", "asset_version_id", "route_variant_id", "task_id"):
            object.__setattr__(self, field, _id(getattr(self, field), field))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("shot output version is invalid")
        object.__setattr__(self, "asset_sha256", _digest(self.asset_sha256, "asset_sha256"))
        if self.decision is not None:
            object.__setattr__(self, "decision", ReviewDecision(self.decision))
        if not isinstance(self.review_note, str) or len(self.review_note) > 1000:
            raise ValueError("review note is invalid")
        if self.decision is None and self.review_note:
            raise ValueError("unreviewed output cannot have a review note")

    def to_dict(self) -> dict[str, object]:
        return {
            "shot_id": self.shot_id,
            "version": self.version,
            "asset_version_id": self.asset_version_id,
            "asset_sha256": self.asset_sha256,
            "route_variant_id": self.route_variant_id,
            "task_id": self.task_id,
            "decision": self.decision.value if self.decision else None,
            "review_note": self.review_note,
        }


@dataclass(frozen=True, slots=True)
class ProductionState:
    project_id: str
    owner_user_id: str
    pack_hash: str
    shot_ids: tuple[str, ...]
    phase: ProductionPhase
    revision: int
    outputs: Mapping[str, tuple[ShotOutputVersion, ...]]
    phase_receipts: Mapping[str, str]
    previous_state_hash: str | None
    state_hash: str
    schema_version: str = STATE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _id(self.project_id, "project_id"))
        object.__setattr__(self, "owner_user_id", _id(self.owner_user_id, "owner_user_id"))
        object.__setattr__(self, "pack_hash", _digest(self.pack_hash, "pack_hash"))
        object.__setattr__(self, "phase", ProductionPhase(self.phase))
        if self.schema_version not in {LEGACY_STATE_VERSION, STATE_VERSION}:
            raise ValueError("production state version is unsupported")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("production state revision is invalid")
        shots = tuple(_id(item, "shot_id") for item in self.shot_ids)
        if not shots or len(shots) != len(set(shots)):
            raise ValueError("production shot identities are invalid")
        object.__setattr__(self, "shot_ids", shots)
        normalized: dict[str, tuple[ShotOutputVersion, ...]] = {}
        if set(self.outputs) - set(shots):
            raise ValueError("production outputs contain an unknown shot")
        for shot_id in shots:
            versions = tuple(self.outputs.get(shot_id, ()))
            if any(not isinstance(item, ShotOutputVersion) for item in versions):
                raise ValueError("production output version is invalid")
            if any(item.shot_id != shot_id for item in versions):
                raise ValueError("production output lineage crossed shots")
            if tuple(item.version for item in versions) != tuple(range(1, len(versions) + 1)):
                raise ValueError("production output versions are not contiguous")
            if sum(item.decision is ReviewDecision.ACCEPTED for item in versions) > 1:
                raise ValueError("a shot cannot accept more than one output")
            normalized[shot_id] = versions
        object.__setattr__(self, "outputs", MappingProxyType(normalized))
        receipts = {
            ProductionPhase(_id(key, "phase receipt key")).value:
            _digest(value, "phase receipt hash")
            for key, value in self.phase_receipts.items()
        }
        if ProductionPhase.DRAFT.value in receipts:
            raise ValueError("draft phase cannot have a confirmation receipt")
        phase_order = tuple(ProductionPhase)
        current_index = phase_order.index(self.phase)
        if any(phase_order.index(ProductionPhase(key)) > current_index for key in receipts):
            raise ValueError("production has a receipt for a future phase")
        if self.schema_version == LEGACY_STATE_VERSION and receipts:
            raise ValueError("legacy production state cannot contain phase receipts")
        required_receipts = {item.value for item in phase_order[1:current_index + 1]}
        if self.schema_version == STATE_VERSION and set(receipts) != required_receipts:
            raise ValueError("production hash chain lacks contiguous phase confirmation receipts")
        object.__setattr__(self, "phase_receipts", MappingProxyType(receipts))
        if self.previous_state_hash is not None:
            object.__setattr__(
                self, "previous_state_hash", _digest(self.previous_state_hash, "previous_state_hash")
            )
        expected = _hash(self._material())
        if not hmac_compare(expected, self.state_hash):
            raise ValueError("production state hash does not match its contents")

    def _material(self) -> dict[str, object]:
        material = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "owner_user_id": self.owner_user_id,
            "pack_hash": self.pack_hash,
            "shot_ids": list(self.shot_ids),
            "phase": self.phase.value,
            "revision": self.revision,
            "outputs": {
                shot_id: [item.to_dict() for item in self.outputs.get(shot_id, ())]
                for shot_id in self.shot_ids
            },
            "previous_state_hash": self.previous_state_hash,
        }
        if self.schema_version == STATE_VERSION:
            material["phase_receipts"] = dict(sorted(self.phase_receipts.items()))
        return material

    def to_dict(self) -> dict[str, object]:
        return {**self._material(), "state_hash": self.state_hash}


def hmac_compare(left: str, right: object) -> bool:
    import hmac
    return isinstance(right, str) and hmac.compare_digest(left, right)


def _create(**values: object) -> ProductionState:
    schema_version = str(values.get("schema_version", STATE_VERSION))
    material = {
        "schema_version": schema_version,
        "project_id": values["project_id"],
        "owner_user_id": values["owner_user_id"],
        "pack_hash": values["pack_hash"],
        "shot_ids": list(values["shot_ids"]),
        "phase": ProductionPhase(values["phase"]).value,
        "revision": values["revision"],
        "outputs": {
            shot_id: [item.to_dict() for item in values["outputs"].get(shot_id, ())]  # type: ignore[union-attr]
            for shot_id in values["shot_ids"]  # type: ignore[union-attr]
        },
        "previous_state_hash": values["previous_state_hash"],
    }
    if schema_version == STATE_VERSION:
        material["phase_receipts"] = dict(sorted(values["phase_receipts"].items()))  # type: ignore[union-attr]
    return ProductionState(
        **values, state_hash=_hash(material), schema_version=schema_version  # type: ignore[arg-type]
    )


def start_production(pack: ProductionPack, owner_user_id: str) -> ProductionState:
    if not isinstance(pack, ProductionPack):
        raise TypeError("pack must be a ProductionPack")
    shots = tuple(item.shot_id for item in sorted(pack.shots, key=lambda row: (row.scene_id, row.ordinal)))
    return _create(
        project_id=pack.project.project_id, owner_user_id=_id(owner_user_id, "owner_user_id"),
        pack_hash=pack.content_hash, shot_ids=shots, phase=ProductionPhase.DRAFT,
        revision=1, outputs={shot_id: () for shot_id in shots}, phase_receipts={},
        previous_state_hash=None,
    )


def _next(
    state: ProductionState, *, phase: ProductionPhase | None = None, outputs=None,
    phase_receipt: str | None = None,
) -> ProductionState:
    receipts = dict(state.phase_receipts)
    if state.schema_version == LEGACY_STATE_VERSION:
        phase_order = tuple(ProductionPhase)
        for legacy_phase in phase_order[1:phase_order.index(state.phase) + 1]:
            receipts[legacy_phase.value] = _hash({
                "kind": "castle.legacy-unreceipted-phase.v1",
                "project_id": state.project_id,
                "legacy_state_hash": state.state_hash,
                "phase": legacy_phase.value,
            })
    if phase is not None:
        if phase_receipt is None:
            raise ValueError("production phase transition requires a confirmation receipt")
        receipts[phase.value] = _digest(phase_receipt, "phase receipt hash")
    return _create(
        project_id=state.project_id, owner_user_id=state.owner_user_id,
        pack_hash=state.pack_hash, shot_ids=state.shot_ids,
        phase=phase or state.phase, revision=state.revision + 1,
        outputs=outputs if outputs is not None else state.outputs,
        phase_receipts=receipts,
        previous_state_hash=state.state_hash,
    )


def advance_production(
    state: ProductionState, phase: ProductionPhase, *, confirmation_hash: str | None = None,
) -> ProductionState:
    phase = ProductionPhase(phase)
    if _NEXT.get(state.phase) is not phase:
        raise ValueError("production phase transition is not allowed")
    accepted = {
        shot_id: any(item.decision is ReviewDecision.ACCEPTED for item in versions)
        for shot_id, versions in state.outputs.items()
    }
    if phase is ProductionPhase.STORY_REVIEW and not all(state.outputs[shot] for shot in state.shot_ids):
        raise ValueError("every shot needs an output before story review")
    if phase is ProductionPhase.EDITING and not all(accepted.values()):
        raise ValueError("every shot needs one accepted output before editing")
    if confirmation_hash is None:
        confirmation_hash = _hash({
            "kind": "castle.internal-phase-transition.v1",
            "project_id": state.project_id,
            "from_state_hash": state.state_hash,
            "phase": phase.value,
        })
    return _next(state, phase=phase, phase_receipt=confirmation_hash)


def record_shot_output(
    state: ProductionState, *, shot_id: str, asset_version_id: str,
    asset_sha256: str, route_variant_id: str, task_id: str,
) -> ProductionState:
    if state.phase not in {ProductionPhase.GENERATING, ProductionPhase.STORY_REVIEW}:
        raise ValueError("shot outputs can only be recorded while generating or reviewing")
    shot_id = _id(shot_id, "shot_id")
    if shot_id not in state.outputs:
        raise ValueError("shot output does not belong to this production")
    current = state.outputs[shot_id]
    if any(item.decision is ReviewDecision.ACCEPTED for item in current):
        raise ValueError("accepted shot output cannot be overwritten")
    if (
        state.phase is ProductionPhase.STORY_REVIEW
        and (not current or current[-1].decision is not ReviewDecision.REJECTED)
    ):
        raise ValueError("story-review retry requires a rejected latest output")
    version = ShotOutputVersion(
        shot_id, len(current) + 1, asset_version_id, asset_sha256,
        route_variant_id, task_id,
    )
    outputs = dict(state.outputs)
    outputs[shot_id] = current + (version,)
    return _next(state, outputs=outputs)


def review_shot_output(
    state: ProductionState, *, shot_id: str, version: int,
    decision: ReviewDecision, note: str = "",
) -> ProductionState:
    if state.phase is not ProductionPhase.STORY_REVIEW:
        raise ValueError("shot review is only open during story review")
    shot_id = _id(shot_id, "shot_id")
    versions = state.outputs.get(shot_id)
    if versions is None or not 1 <= version <= len(versions):
        raise ValueError("shot output version does not exist")
    if versions[version - 1].decision is not None:
        raise ValueError("shot output review is immutable")
    decision = ReviewDecision(decision)
    if decision is ReviewDecision.ACCEPTED and any(
        item.decision is ReviewDecision.ACCEPTED for item in versions
    ):
        raise ValueError("a shot already has an accepted output")
    selected = versions[version - 1]
    reviewed = ShotOutputVersion(
        selected.shot_id, selected.version, selected.asset_version_id,
        selected.asset_sha256, selected.route_variant_id, selected.task_id,
        decision, note,
    )
    outputs = dict(state.outputs)
    outputs[shot_id] = versions[:version - 1] + (reviewed,) + versions[version:]
    return _next(state, outputs=outputs)


def checkpoint_delivery_review(state: ProductionState) -> ProductionState:
    """Append a hash-chain checkpoint after an immutable delivery decision."""

    if state.phase is not ProductionPhase.DELIVERY_REVIEW:
        raise ValueError("delivery checkpoint requires delivery review phase")
    return _next(state)


def parse_production_state(raw: Mapping[str, Any]) -> ProductionState:
    if not isinstance(raw, Mapping):
        raise ValueError("production state schema is invalid")
    version = raw.get("schema_version")
    expected = {
        "schema_version", "project_id", "owner_user_id", "pack_hash", "shot_ids",
        "phase", "revision", "outputs", "previous_state_hash", "state_hash",
    }
    if version == STATE_VERSION:
        expected.add("phase_receipts")
    elif version != LEGACY_STATE_VERSION:
        raise ValueError("production state version is unsupported")
    if set(raw) != expected:
        raise ValueError("production state schema is invalid")
    outputs_raw = raw["outputs"]
    if not isinstance(outputs_raw, Mapping):
        raise ValueError("production outputs are invalid")
    outputs = {}
    for shot_id, versions in outputs_raw.items():
        if not isinstance(versions, list):
            raise ValueError("production output versions are invalid")
        outputs[str(shot_id)] = tuple(ShotOutputVersion(
            shot_id=item["shot_id"], version=item["version"],
            asset_version_id=item["asset_version_id"], asset_sha256=item["asset_sha256"],
            route_variant_id=item["route_variant_id"], task_id=item["task_id"],
            decision=item["decision"], review_note=item["review_note"],
        ) for item in versions if isinstance(item, Mapping))
        if len(outputs[str(shot_id)]) != len(versions):
            raise ValueError("production output version is invalid")
    return ProductionState(
        project_id=raw["project_id"], owner_user_id=raw["owner_user_id"],
        pack_hash=raw["pack_hash"], shot_ids=tuple(raw["shot_ids"]),
        phase=raw["phase"], revision=raw["revision"], outputs=outputs,
        phase_receipts=raw.get("phase_receipts", {}),
        previous_state_hash=raw["previous_state_hash"], state_hash=raw["state_hash"],
        schema_version=raw["schema_version"],
    )


__all__ = [
    "LEGACY_STATE_VERSION", "ProductionPhase", "ProductionState", "ReviewDecision", "STATE_VERSION",
    "ShotOutputVersion", "advance_production", "parse_production_state",
    "record_shot_output", "review_shot_output", "start_production",
    "checkpoint_delivery_review",
]
