"""Versioned quality evidence and provider-parity decisions.

Castle never claims that the local 5090 route matches an external provider from
one lucky output or one opaque similarity score.  This module turns that rule
into a deterministic contract.  It stores only hashes, bounded measurements,
and human verdicts; media and private review notes remain in the artifact store.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .models import CapabilityKind, VisualClass


QUALITY_CONTRACT_VERSION = "castle.quality-evidence.v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_METRIC_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_HARD_GATES = frozenset({
    "format",
    "rights_consent",
    "visual_class",
    "identity",
    "outfit",
})
_KNOWN_GATES = _HARD_GATES | {
    "temporal",
    "anatomy_composition",
    "audio",
    "lip_sync",
    "prompt_adherence",
}


def _id(value: str, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(result):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return result


def _digest(value: str, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(result):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _text(value: str, label: str, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or any(ord(char) < 32 for char in result):
        raise ValueError(f"{label} is invalid")
    return result


def _finite(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _canonical_hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class RouteOrigin(str, Enum):
    LOCAL = "local"
    EXTERNAL = "external"


class GateVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class HumanVerdict(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class RuntimeFingerprint:
    """Everything whose change invalidates a golden receipt."""

    workflow_sha256: str
    model_sha256s: tuple[str, ...]
    engine_revision: str
    variant_revision: str
    attention_backend: str
    acceleration_profile: str
    settings_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_sha256", _digest(self.workflow_sha256, "workflow_sha256"))
        models = tuple(_digest(value, "model_sha256") for value in self.model_sha256s)
        if not models or len(models) != len(set(models)) or len(models) > 64:
            raise ValueError("model_sha256s must contain 1-64 unique digests")
        object.__setattr__(self, "model_sha256s", tuple(sorted(models)))
        object.__setattr__(self, "engine_revision", _text(self.engine_revision, "engine_revision", 200))
        object.__setattr__(self, "variant_revision", _id(self.variant_revision, "variant_revision"))
        object.__setattr__(self, "attention_backend", _id(self.attention_backend, "attention_backend"))
        object.__setattr__(self, "acceleration_profile", _id(self.acceleration_profile, "acceleration_profile"))
        object.__setattr__(self, "settings_sha256", _digest(self.settings_sha256, "settings_sha256"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_sha256": self.workflow_sha256,
            "model_sha256s": list(self.model_sha256s),
            "engine_revision": self.engine_revision,
            "variant_revision": self.variant_revision,
            "attention_backend": self.attention_backend,
            "acceleration_profile": self.acceleration_profile,
            "settings_sha256": self.settings_sha256,
        }

    @property
    def digest(self) -> str:
        return _canonical_hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    capability: CapabilityKind
    visual_class: VisualClass
    semantics_id: str
    input_sha256s: tuple[str, ...]
    required_gates: frozenset[str] = field(default_factory=lambda: _HARD_GATES)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _id(self.case_id, "case_id"))
        if not isinstance(self.capability, CapabilityKind):
            object.__setattr__(self, "capability", CapabilityKind(self.capability))
        if not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        if self.visual_class is VisualClass.UNKNOWN:
            raise ValueError("unknown visual class cannot have an executable golden case")
        object.__setattr__(self, "semantics_id", _id(self.semantics_id, "semantics_id"))
        inputs = tuple(_digest(value, "input_sha256") for value in self.input_sha256s)
        if not inputs or len(inputs) > 64 or len(inputs) != len(set(inputs)):
            raise ValueError("input_sha256s must contain 1-64 unique digests")
        object.__setattr__(self, "input_sha256s", tuple(sorted(inputs)))
        gates = frozenset(_id(value, "required_gate") for value in self.required_gates)
        if not _HARD_GATES.issubset(gates) or not gates.issubset(_KNOWN_GATES):
            raise ValueError("required_gates must include every hard gate and only known gates")
        object.__setattr__(self, "required_gates", gates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "capability": self.capability.value,
            "visual_class": self.visual_class.value,
            "semantics_id": self.semantics_id,
            "input_sha256s": list(self.input_sha256s),
            "required_gates": sorted(self.required_gates),
        }


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    verdict: GateVerdict
    evidence_ids: tuple[str, ...]
    metric_values: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        gate = _id(self.gate, "gate")
        if gate not in _KNOWN_GATES:
            raise ValueError("gate is not recognized")
        object.__setattr__(self, "gate", gate)
        if not isinstance(self.verdict, GateVerdict):
            object.__setattr__(self, "verdict", GateVerdict(self.verdict))
        evidence = tuple(_id(value, "evidence_id") for value in self.evidence_ids)
        if self.verdict in {GateVerdict.PASS, GateVerdict.WARN, GateVerdict.FAIL} and not evidence:
            raise ValueError("evaluated gates require evidence")
        if len(evidence) != len(set(evidence)) or len(evidence) > 32:
            raise ValueError("evidence_ids must be unique and bounded")
        object.__setattr__(self, "evidence_ids", evidence)
        metrics: list[tuple[str, float]] = []
        for name, value in self.metric_values:
            name = str(name or "").strip().lower()
            if not _SAFE_METRIC_RE.fullmatch(name):
                raise ValueError("metric name is invalid")
            metrics.append((name, _finite(value, f"metric {name}")))
        metrics.sort()
        if len(metrics) != len({name for name, _value in metrics}) or len(metrics) > 64:
            raise ValueError("metric values must be unique and bounded")
        object.__setattr__(self, "metric_values", tuple(metrics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict.value,
            "evidence_ids": list(self.evidence_ids),
            "metric_values": {name: value for name, value in self.metric_values},
        }


@dataclass(frozen=True, slots=True)
class HumanReview:
    reviewer_id: str
    verdict: HumanVerdict
    blind_review: bool
    review_evidence_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewer_id", _id(self.reviewer_id, "reviewer_id"))
        if not isinstance(self.verdict, HumanVerdict):
            object.__setattr__(self, "verdict", HumanVerdict(self.verdict))
        if self.review_evidence_id is not None:
            object.__setattr__(
                self,
                "review_evidence_id",
                _id(self.review_evidence_id, "review_evidence_id"),
            )
        if self.verdict is not HumanVerdict.PENDING and not self.review_evidence_id:
            raise ValueError("completed human reviews require evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "verdict": self.verdict.value,
            "blind_review": self.blind_review,
            "review_evidence_id": self.review_evidence_id,
        }


@dataclass(frozen=True, slots=True)
class GoldenReceipt:
    receipt_id: str
    case_id: str
    route_variant_id: str
    origin: RouteOrigin
    runtime: RuntimeFingerprint
    output_sha256: str
    repeat_index: int
    gate_results: tuple[GateResult, ...]
    human_review: HumanReview
    duration_seconds: float
    peak_vram_bytes: int | None = None
    peak_ram_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _id(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "case_id", _id(self.case_id, "case_id"))
        object.__setattr__(self, "route_variant_id", _id(self.route_variant_id, "route_variant_id"))
        if not isinstance(self.origin, RouteOrigin):
            object.__setattr__(self, "origin", RouteOrigin(self.origin))
        if not isinstance(self.runtime, RuntimeFingerprint):
            raise ValueError("runtime must be a RuntimeFingerprint")
        object.__setattr__(self, "output_sha256", _digest(self.output_sha256, "output_sha256"))
        if not isinstance(self.repeat_index, int) or isinstance(self.repeat_index, bool) or self.repeat_index < 1:
            raise ValueError("repeat_index must be a positive integer")
        results = tuple(self.gate_results)
        if any(not isinstance(value, GateResult) for value in results):
            raise ValueError("gate_results must contain GateResult values")
        if len(results) != len({value.gate for value in results}):
            raise ValueError("gate_results cannot repeat a gate")
        object.__setattr__(self, "gate_results", results)
        if not isinstance(self.human_review, HumanReview):
            raise ValueError("human_review must be HumanReview")
        object.__setattr__(self, "duration_seconds", _finite(self.duration_seconds, "duration_seconds"))
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        for name in ("peak_vram_bytes", "peak_ram_bytes"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def key(self) -> tuple[str, str, int]:
        return self.case_id, self.route_variant_id, self.repeat_index

    def gate_map(self) -> dict[str, GateResult]:
        return {value.gate: value for value in self.gate_results}

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "case_id": self.case_id,
            "route_variant_id": self.route_variant_id,
            "origin": self.origin.value,
            "runtime": self.runtime.to_dict(),
            "runtime_sha256": self.runtime.digest,
            "output_sha256": self.output_sha256,
            "repeat_index": self.repeat_index,
            "gate_results": [value.to_dict() for value in self.gate_results],
            "human_review": self.human_review.to_dict(),
            "duration_seconds": self.duration_seconds,
            "peak_vram_bytes": self.peak_vram_bytes,
            "peak_ram_bytes": self.peak_ram_bytes,
        }


@dataclass(frozen=True, slots=True)
class ParityVerdict:
    claimable: bool
    reason_codes: tuple[str, ...]
    local_receipt_ids: tuple[str, ...]
    reference_receipt_ids: tuple[str, ...]
    contract_version: str = QUALITY_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "claimable": self.claimable,
            "reason_codes": list(self.reason_codes),
            "local_receipt_ids": list(self.local_receipt_ids),
            "reference_receipt_ids": list(self.reference_receipt_ids),
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True, slots=True)
class RouteGoldenVerdict:
    activatable: bool
    reason_codes: tuple[str, ...]
    receipt_ids: tuple[str, ...]
    golden_id: str | None
    contract_version: str = QUALITY_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "activatable": self.activatable,
            "reason_codes": list(self.reason_codes),
            "receipt_ids": list(self.receipt_ids),
            "golden_id": self.golden_id,
            "contract_version": self.contract_version,
        }


def evaluate_route_golden(
    case: GoldenCase,
    route_variant_id: str,
    origin: RouteOrigin,
    receipts: Sequence[GoldenReceipt],
    *,
    current_runtime: RuntimeFingerprint | None = None,
    minimum_repeats: int = 2,
    operator_reviewer_id: str = "operator",
) -> RouteGoldenVerdict:
    """Qualify one exact route cell; evidence never transfers to another cell."""

    if not isinstance(case, GoldenCase):
        raise TypeError("case must be a GoldenCase")
    route_variant_id = _id(route_variant_id, "route_variant_id")
    if not isinstance(origin, RouteOrigin):
        origin = RouteOrigin(origin)
    operator_reviewer_id = _id(operator_reviewer_id, "operator_reviewer_id")
    if not 2 <= minimum_repeats <= 10:
        raise ValueError("minimum_repeats must be between 2 and 10")
    selected = [
        item for item in receipts
        if item.case_id == case.case_id
        and item.route_variant_id == route_variant_id
        and item.origin is origin
    ]
    reasons: set[str] = set()
    if len(selected) < minimum_repeats:
        reasons.add("route_repeats_missing")
    if len({item.key for item in selected}) != len(selected) or len({item.receipt_id for item in selected}) != len(selected):
        reasons.add("duplicate_receipt")
    if len({item.output_sha256 for item in selected}) != len(selected):
        reasons.add("repeat_outputs_not_distinct")
    runtimes = {item.runtime.digest for item in selected}
    if len(runtimes) > 1:
        reasons.add("repeat_runtime_changed")
    if current_runtime is not None and any(
        item.runtime.digest != current_runtime.digest for item in selected
    ):
        reasons.add("current_runtime_changed")
    for item in selected:
        gates = item.gate_map()
        for gate in case.required_gates:
            result = gates.get(gate)
            if result is None:
                reasons.add("required_gate_missing")
            elif result.verdict is not GateVerdict.PASS:
                reasons.add("required_gate_not_passed")
        review = item.human_review
        if review.reviewer_id != operator_reviewer_id:
            reasons.add("operator_review_missing")
        if review.verdict is not HumanVerdict.APPROVE:
            reasons.add("human_approval_missing")
    ids = tuple(sorted(item.receipt_id for item in selected))
    activatable = not reasons
    golden_id = None
    if activatable:
        material = {
            "case": case.to_dict(), "route_variant_id": route_variant_id,
            "origin": origin.value, "receipt_ids": list(ids),
            "runtime_sha256": next(iter(runtimes)),
        }
        golden_id = "golden:autoip:" + _canonical_hash(material)[:32]
    return RouteGoldenVerdict(
        activatable=activatable,
        reason_codes=tuple(sorted(reasons)),
        receipt_ids=ids,
        golden_id=golden_id,
    )


def evaluate_provider_parity(
    case: GoldenCase,
    local_variant_id: str,
    reference_variant_id: str,
    receipts: Sequence[GoldenReceipt],
    *,
    current_local_runtime: RuntimeFingerprint,
    minimum_local_repeats: int = 2,
    operator_reviewer_id: str = "operator",
) -> ParityVerdict:
    """Return whether a narrowly scoped local/provider parity claim is valid."""

    if not isinstance(case, GoldenCase):
        raise TypeError("case must be a GoldenCase")
    local_variant_id = _id(local_variant_id, "local_variant_id")
    reference_variant_id = _id(reference_variant_id, "reference_variant_id")
    operator_reviewer_id = _id(operator_reviewer_id, "operator_reviewer_id")
    if local_variant_id == reference_variant_id:
        raise ValueError("local and reference variants must be distinct")
    if not 2 <= minimum_local_repeats <= 10:
        raise ValueError("minimum_local_repeats must be between 2 and 10")

    relevant = [receipt for receipt in receipts if receipt.case_id == case.case_id]
    reasons: set[str] = set()
    keys = [receipt.key for receipt in relevant]
    receipt_ids = [receipt.receipt_id for receipt in relevant]
    if len(keys) != len(set(keys)) or len(receipt_ids) != len(set(receipt_ids)):
        reasons.add("duplicate_receipt")

    local = [
        receipt for receipt in relevant
        if receipt.route_variant_id == local_variant_id and receipt.origin is RouteOrigin.LOCAL
    ]
    references = [
        receipt for receipt in relevant
        if receipt.route_variant_id == reference_variant_id and receipt.origin is RouteOrigin.EXTERNAL
    ]
    if len(local) < minimum_local_repeats:
        reasons.add("local_repeats_missing")
    if not references:
        reasons.add("provider_reference_missing")
    if len({receipt.output_sha256 for receipt in local}) != len(local):
        reasons.add("local_repeat_outputs_not_distinct")
    if any(receipt.runtime.digest != current_local_runtime.digest for receipt in local):
        reasons.add("local_runtime_changed")

    def audit_receipt(receipt: GoldenReceipt, *, require_blind: bool) -> None:
        gate_map = receipt.gate_map()
        for gate in case.required_gates:
            result = gate_map.get(gate)
            if result is None:
                reasons.add("required_gate_missing")
            elif result.verdict is not GateVerdict.PASS:
                reasons.add("required_gate_not_passed")
        review = receipt.human_review
        if review.reviewer_id != operator_reviewer_id:
            reasons.add("operator_review_missing")
        if review.verdict is not HumanVerdict.APPROVE:
            reasons.add("human_approval_missing")
        if require_blind and not review.blind_review:
            reasons.add("blind_review_missing")

    for receipt in local:
        audit_receipt(receipt, require_blind=True)
    for receipt in references:
        audit_receipt(receipt, require_blind=True)

    # Claims are scoped to exact input semantics and executable visual class by
    # the GoldenCase.  Receipts for another case never make this one eligible.
    return ParityVerdict(
        claimable=not reasons,
        reason_codes=tuple(sorted(reasons)),
        local_receipt_ids=tuple(sorted(receipt.receipt_id for receipt in local)),
        reference_receipt_ids=tuple(sorted(receipt.receipt_id for receipt in references)),
    )


__all__ = [
    "GateResult",
    "GateVerdict",
    "GoldenCase",
    "GoldenReceipt",
    "HumanReview",
    "HumanVerdict",
    "ParityVerdict",
    "QUALITY_CONTRACT_VERSION",
    "RouteOrigin",
    "RouteGoldenVerdict",
    "RuntimeFingerprint",
    "evaluate_provider_parity",
    "evaluate_route_golden",
]
