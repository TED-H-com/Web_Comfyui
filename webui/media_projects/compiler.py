"""Compile accepted production packs into resumable project/shot DAGs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from provider_fabric.models import CapabilityKind

from .contracts import DialogueMode, ProductionPack, RouteVariant, ShotSpec
from .lint import GateReport, lint_production_pack
from .prompt_contract import compile_shot_prompt


COMPILER_VERSION = "castle.media-project-compiler.v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VIDEO_CAPABILITIES = {
    CapabilityKind.IMAGE_TO_VIDEO,
    CapabilityKind.TEXT_TO_VIDEO,
    CapabilityKind.LIP_SYNC,
    CapabilityKind.DANCE,
    CapabilityKind.MULTI_SUBJECT_VIDEO,
}


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("task payload must contain JSON-compatible data")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class RetryMode(str, Enum):
    NEVER = "never"
    SAME_INPUT = "same_input"
    RECONCILE_BEFORE_RETRY = "reconcile_before_retry"


class FailureAction(str, Enum):
    BLOCK_DOWNSTREAM = "block_downstream"
    HOLD_FOR_REVIEW = "hold_for_review"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    mode: RetryMode
    max_attempts: int
    backoff_seconds: tuple[float, ...]
    same_input_hash_required: bool
    retryable_outcomes: tuple[str, ...]
    no_retry_after_states: tuple[str, ...]
    allow_precharge_route_fallback: bool = False
    max_precharge_route_attempts: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RetryMode):
            object.__setattr__(self, "mode", RetryMode(self.mode))
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 5:
            raise ValueError("retry max_attempts must be an integer from 1 to 5")
        backoff = tuple(float(value) for value in self.backoff_seconds)
        if len(backoff) != self.max_attempts - 1 or any(value < 0 or value > 3600 for value in backoff):
            raise ValueError("retry backoff must have one bounded value per retry")
        object.__setattr__(self, "backoff_seconds", backoff)
        if not isinstance(self.same_input_hash_required, bool):
            raise ValueError("same_input_hash_required must be a boolean")
        outcomes = tuple(_identifier(value, "retryable_outcome") for value in self.retryable_outcomes)
        states = tuple(_identifier(value, "no_retry_after_state") for value in self.no_retry_after_states)
        if len(outcomes) != len(set(outcomes)) or len(states) != len(set(states)):
            raise ValueError("retry outcomes/states must be unique")
        object.__setattr__(self, "retryable_outcomes", outcomes)
        object.__setattr__(self, "no_retry_after_states", states)
        if self.mode is RetryMode.NEVER and (self.max_attempts != 1 or outcomes):
            raise ValueError("never retry policy must have one attempt and no retryable outcomes")
        if self.mode is RetryMode.RECONCILE_BEFORE_RETRY:
            required_stops = {"accepted", "charged", "submission_uncertain"}
            if not required_stops.issubset(states) or not self.same_input_hash_required:
                raise ValueError("provider retry must stop after acceptance/charge/uncertainty and preserve inputs")
        if not isinstance(self.allow_precharge_route_fallback, bool):
            raise ValueError("allow_precharge_route_fallback must be a boolean")
        if (
            isinstance(self.max_precharge_route_attempts, bool)
            or not isinstance(self.max_precharge_route_attempts, int)
            or not 1 <= self.max_precharge_route_attempts <= 5
        ):
            raise ValueError("max_precharge_route_attempts must be between 1 and 5")
        if not self.allow_precharge_route_fallback and self.max_precharge_route_attempts != 1:
            raise ValueError("fallback attempts must be one when route fallback is disabled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "max_attempts": self.max_attempts,
            "backoff_seconds": list(self.backoff_seconds),
            "same_input_hash_required": self.same_input_hash_required,
            "retryable_outcomes": list(self.retryable_outcomes),
            "no_retry_after_states": list(self.no_retry_after_states),
            "allow_precharge_route_fallback": self.allow_precharge_route_fallback,
            "max_precharge_route_attempts": self.max_precharge_route_attempts,
        }


@dataclass(frozen=True, slots=True)
class GatePolicy:
    gate_id: str
    checks: tuple[str, ...]
    hard_fail: bool
    human_required: bool
    failure_action: FailureAction

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _identifier(self.gate_id, "gate_id"))
        checks = tuple(_identifier(value, "gate check") for value in self.checks)
        if not checks or len(checks) != len(set(checks)):
            raise ValueError("gate checks must be non-empty and unique")
        object.__setattr__(self, "checks", checks)
        if not isinstance(self.hard_fail, bool) or not isinstance(self.human_required, bool):
            raise ValueError("gate booleans are invalid")
        if not isinstance(self.failure_action, FailureAction):
            object.__setattr__(self, "failure_action", FailureAction(self.failure_action))
        if self.human_required and self.failure_action is not FailureAction.HOLD_FOR_REVIEW:
            raise ValueError("human gates must hold for review")

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "checks": list(self.checks),
            "hard_fail": self.hard_fail,
            "human_required": self.human_required,
            "failure_action": self.failure_action.value,
        }


@dataclass(frozen=True, slots=True)
class ProjectTask:
    key: str
    task_type: str
    lane: str
    depends_on: tuple[str, ...]
    resource_locks: tuple[str, ...]
    payload: Mapping[str, Any]
    input_hash: str
    retry_policy: RetryPolicy
    gate: GatePolicy | None = None
    route_binding: str | None = None
    reuse_accepted_output: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _identifier(self.key, "task key"))
        object.__setattr__(self, "task_type", _identifier(self.task_type, "task_type"))
        if self.lane not in {"control", "cpu", "review", "route-bound"} and not _RESOURCE_RE.fullmatch(self.lane):
            raise ValueError("task lane is invalid")
        dependencies = tuple(_identifier(value, "dependency") for value in self.depends_on)
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("task dependencies must be unique")
        object.__setattr__(self, "depends_on", dependencies)
        locks = tuple(self.resource_locks)
        if any(not isinstance(value, str) or not _RESOURCE_RE.fullmatch(value) for value in locks):
            raise ValueError("task resource lock is invalid")
        if len(locks) != len(set(locks)):
            raise ValueError("task resource locks must be unique")
        object.__setattr__(self, "resource_locks", locks)
        if not isinstance(self.payload, Mapping):
            raise ValueError("task payload must be a mapping")
        object.__setattr__(self, "payload", _freeze(self.payload))
        if not isinstance(self.input_hash, str) or not _SHA256_RE.fullmatch(self.input_hash):
            raise ValueError("task input_hash must be SHA256")
        if not isinstance(self.retry_policy, RetryPolicy):
            raise ValueError("task retry_policy must be RetryPolicy")
        if self.gate is not None and not isinstance(self.gate, GatePolicy):
            raise ValueError("task gate must be GatePolicy")
        if self.route_binding is not None:
            object.__setattr__(self, "route_binding", _identifier(self.route_binding, "route_binding"))
            if self.lane != "route-bound" or locks:
                raise ValueError("unresolved provider tasks must use route-bound lane without concrete locks")
        elif self.lane == "route-bound":
            raise ValueError("route-bound lane requires a route_binding")
        if not isinstance(self.reuse_accepted_output, bool):
            raise ValueError("reuse_accepted_output must be a boolean")

    @property
    def executable(self) -> bool:
        """An unresolved route template can never be handed to a worker."""

        return self.route_binding is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "task_type": self.task_type,
            "lane": self.lane,
            "depends_on": list(self.depends_on),
            "resource_locks": list(self.resource_locks),
            "payload": _thaw(self.payload),
            "input_hash": self.input_hash,
            "retry_policy": self.retry_policy.to_dict(),
            "gate": self.gate.to_dict() if self.gate else None,
            "route_binding": self.route_binding,
            "reuse_accepted_output": self.reuse_accepted_output,
            "executable": self.executable,
        }


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    purpose: str
    route_variant_id: str
    provider_id: str
    provider_account_id: str
    variant_id: str
    capability: str
    resource_lane: str
    resource_locks: tuple[str, ...]
    estimated_credits: int | None

    @classmethod
    def from_route(cls, purpose: str, route: RouteVariant) -> "ProviderBinding":
        return cls(
            purpose=purpose,
            route_variant_id=route.route_variant_id,
            provider_id=route.provider_id,
            provider_account_id=route.provider_account_id,
            variant_id=route.variant_id,
            capability=route.capability.value,
            resource_lane=route.resource_lane,
            resource_locks=route.resource_locks,
            estimated_credits=route.estimated_credits,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "purpose", _identifier(self.purpose, "binding purpose"))
        for name in ("route_variant_id", "provider_id", "provider_account_id", "variant_id", "capability"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        if not _RESOURCE_RE.fullmatch(self.resource_lane):
            raise ValueError("binding resource lane is invalid")
        if not self.resource_locks or any(not _RESOURCE_RE.fullmatch(value) for value in self.resource_locks):
            raise ValueError("binding resource locks are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "route_variant_id": self.route_variant_id,
            "provider_id": self.provider_id,
            "provider_account_id": self.provider_account_id,
            "variant_id": self.variant_id,
            "capability": self.capability,
            "resource_lane": self.resource_lane,
            "resource_locks": list(self.resource_locks),
            "estimated_credits": self.estimated_credits,
        }


@dataclass(frozen=True, slots=True)
class ShotRunPlan:
    shot_id: str
    parent_dependency: str
    source_hash: str
    route_bindings: tuple[ProviderBinding, ...]
    tasks: tuple[ProjectTask, ...]
    plan_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _identifier(self.shot_id, "shot_id"))
        object.__setattr__(self, "parent_dependency", _identifier(self.parent_dependency, "parent_dependency"))
        if not _SHA256_RE.fullmatch(self.source_hash) or not _SHA256_RE.fullmatch(self.plan_hash):
            raise ValueError("shot hashes must be SHA256")
        if any(not isinstance(item, ProviderBinding) for item in self.route_bindings):
            raise ValueError("route_bindings must contain ProviderBinding objects")
        binding_keys = [(item.purpose, item.route_variant_id) for item in self.route_bindings]
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError("route bindings must be unique per purpose and route")
        if any(not isinstance(item, ProjectTask) for item in self.tasks):
            raise ValueError("tasks must contain ProjectTask objects")
        task_keys = [item.key for item in self.tasks]
        if len(task_keys) != len(set(task_keys)):
            raise ValueError("shot task keys must be unique")
        seen: set[str] = set()
        purposes = {item.purpose for item in self.route_bindings}
        for task in self.tasks:
            unknown_dependencies = set(task.depends_on) - seen
            if unknown_dependencies:
                raise ValueError(f"shot task {task.key} depends on later/unknown tasks")
            if task.route_binding is not None and task.route_binding not in purposes:
                raise ValueError(f"shot task {task.key} has no eligible route binding")
            seen.add(task.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "parent_dependency": self.parent_dependency,
            "source_hash": self.source_hash,
            "route_bindings": [binding.to_dict() for binding in self.route_bindings],
            "tasks": [task.to_dict() for task in self.tasks],
            "plan_hash": self.plan_hash,
        }


@dataclass(frozen=True, slots=True)
class CompiledProject:
    project_id: str
    pack_hash: str
    gate_report: GateReport
    parent_tasks: tuple[ProjectTask, ...]
    shot_runs: tuple[ShotRunPlan, ...]
    compiler_version: str = COMPILER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "pack_hash": self.pack_hash,
            "gate_report": self.gate_report.to_dict(),
            "parent_tasks": [task.to_dict() for task in self.parent_tasks],
            "shot_runs": [run.to_dict() for run in self.shot_runs],
            "compiler_version": self.compiler_version,
        }


def _never_retry() -> RetryPolicy:
    return RetryPolicy(RetryMode.NEVER, 1, (), True, (), ())


def _safe_cpu_retry() -> RetryPolicy:
    return RetryPolicy(
        RetryMode.SAME_INPUT,
        2,
        (1.0,),
        True,
        ("transient_local_error",),
        ("output_accepted",),
    )


def _provider_retry(pack: ProductionPack) -> RetryPolicy:
    return RetryPolicy(
        RetryMode.RECONCILE_BEFORE_RETRY,
        2,
        (5.0,),
        True,
        ("rate_limited_before_submit", "rejected_uncharged", "transport_failed_before_submit"),
        ("accepted", "charged", "submission_uncertain"),
        allow_precharge_route_fallback=pack.project.allow_precharge_fallback,
        max_precharge_route_attempts=pack.project.max_precharge_route_attempts,
    )


def _gate(
    gate_id: str,
    checks: tuple[str, ...],
    *,
    human: bool = False,
) -> GatePolicy:
    return GatePolicy(
        gate_id=gate_id,
        checks=checks,
        hard_fail=True,
        human_required=human,
        failure_action=FailureAction.HOLD_FOR_REVIEW if human else FailureAction.BLOCK_DOWNSTREAM,
    )


def _task(
    key: str,
    task_type: str,
    lane: str,
    depends_on: tuple[str, ...],
    payload: Mapping[str, Any],
    input_material: Mapping[str, Any],
    *,
    retry_policy: RetryPolicy | None = None,
    gate: GatePolicy | None = None,
    route_binding: str | None = None,
    resource_locks: tuple[str, ...] = (),
    reuse_accepted_output: bool = False,
) -> ProjectTask:
    retry = retry_policy or _safe_cpu_retry()
    hash_material = {
        "compiler_version": COMPILER_VERSION,
        "key": key,
        "task_type": task_type,
        "input": input_material,
        "retry_policy": retry.to_dict(),
        "gate": gate.to_dict() if gate else None,
        "route_binding": route_binding,
    }
    return ProjectTask(
        key=key,
        task_type=task_type,
        lane=lane,
        depends_on=depends_on,
        resource_locks=resource_locks,
        payload=payload,
        input_hash=_stable_hash(hash_material),
        retry_policy=retry,
        gate=gate,
        route_binding=route_binding,
        reuse_accepted_output=reuse_accepted_output,
    )


def _shot_source(pack: ProductionPack, shot: ShotSpec) -> tuple[dict[str, Any], str]:
    entities = {item.entity_id: item for item in pack.entities}
    scenes = {item.scene_id: item for item in pack.scenes}
    evidence = {item.evidence_id: item for item in pack.evidence}
    routes = {item.route_variant_id: item for item in pack.route_variants}
    evidence_ids = set(shot.evidence_ids)
    for claim in shot.factual_claims:
        evidence_ids.update(claim.evidence_ids)
    for entity_id in shot.entity_ids:
        entity = entities[entity_id]
        evidence_ids.update(entity.consent_evidence_ids)
    route_ids = shot.eligible_visual_variant_ids + shot.eligible_voice_variant_ids
    for route_id in route_ids:
        evidence_ids.add(routes[route_id].execution_evidence_id)
    source = {
        "project_output": {
            "language": pack.project.language,
            "aspect_ratio": pack.project.aspect_ratio,
            "platform_profile": pack.project.platform_profile,
            "usage_scope": pack.project.usage_scope.value,
            "quality_tier": pack.project.quality_tier,
        },
        "scene": scenes[shot.scene_id].to_dict(),
        "shot": shot.to_dict(),
        "entities": [entities[item].to_dict() for item in shot.entity_ids],
        "evidence": [evidence[item].to_dict() for item in sorted(evidence_ids)],
        "routes": [routes[item].to_dict() for item in route_ids],
    }
    return source, _stable_hash(source)


def _shot_plan(pack: ProductionPack, shot: ShotSpec) -> ShotRunPlan:
    route_map = {item.route_variant_id: item for item in pack.route_variants}
    source, source_hash = _shot_source(pack, shot)
    bindings = tuple(
        [ProviderBinding.from_route("visual", route_map[item]) for item in shot.eligible_visual_variant_ids]
        + [ProviderBinding.from_route("voice", route_map[item]) for item in shot.eligible_voice_variant_ids]
    )
    prompt_contract = compile_shot_prompt(pack, shot)
    common = {
        "project_id": pack.project.project_id,
        "shot_id": shot.shot_id,
        "scene_id": shot.scene_id,
        "shot_source_hash": source_hash,
    }
    tasks: list[ProjectTask] = []
    tasks.append(_task(
        "route_visual",
        "media.route_and_budget_gate",
        "control",
        (),
        {
            **common,
            "purpose": "visual",
            "eligible_route_variant_ids": list(shot.eligible_visual_variant_ids),
            "max_project_credits": pack.project.max_credits,
            "allow_precharge_fallback": pack.project.allow_precharge_fallback,
            "max_precharge_route_attempts": pack.project.max_precharge_route_attempts,
            "postcharge_fallback": False,
        },
        {"source_hash": source_hash, "purpose": "visual", "routes": [item.to_dict() for item in bindings if item.purpose == "visual"]},
        retry_policy=_safe_cpu_retry(),
        gate=_gate("route_budget", ("eligibility", "health", "authoritative_quote", "credit_budget", "concurrency")),
    ))
    generation_dependencies = ["route_visual"]
    if shot.input_asset_ids:
        tasks.append(_task(
            "inputs_resolve",
            "media.resolve_immutable_inputs",
            "cpu",
            (),
            {**common, "input_asset_ids": list(shot.input_asset_ids)},
            {"source_hash": source_hash, "input_asset_ids": list(shot.input_asset_ids)},
            gate=_gate("input_integrity", ("ownership", "availability", "mime", "sha256", "consent_scope")),
        ))
        generation_dependencies.append("inputs_resolve")
    if shot.dialogue_mode is DialogueMode.EXTERNAL_AUDIO:
        tasks.append(_task(
            "external_audio_resolve",
            "audio.resolve_aligned_inputs",
            "cpu",
            (),
            {**common, "dialogue": [cue.to_dict() for cue in shot.dialogue]},
            {"source_hash": source_hash, "dialogue": [cue.to_dict() for cue in shot.dialogue]},
            gate=_gate("external_audio_input", ("ownership", "decode", "trim_alignment", "duration", "voice_rights")),
        ))
        generation_dependencies.append("external_audio_resolve")
    tasks.append(_task(
        "visual_generate",
        "shot.visual_generate",
        "route-bound",
        tuple(generation_dependencies),
        {
            **common,
            "required_capability": shot.required_capability.value,
            "required_options": sorted(shot.required_options),
            "prompt_contract": prompt_contract.to_dict(),
        },
        {
            "source_hash": source_hash,
            "purpose": "visual",
            "prompt_contract_hash": prompt_contract.contract_hash,
        },
        retry_policy=_provider_retry(pack),
        route_binding="visual",
        reuse_accepted_output=True,
    ))
    tasks.append(_task(
        "format_gate",
        "shot.format_qc",
        "cpu",
        ("visual_generate",),
        common,
        {"source_hash": source_hash, "gate": "format"},
        gate=_gate("shot_format", ("decode", "kind", "duration", "fps", "resolution", "browser_playable")),
        reuse_accepted_output=True,
    ))
    tasks.append(_task(
        "class_gate",
        "shot.visual_class_qc",
        "cpu",
        ("format_gate",),
        common,
        {"source_hash": source_hash, "gate": "visual_class"},
        gate=_gate("visual_class", ("intended_class", "observed_class", "no_cross_class_fallback")),
        reuse_accepted_output=True,
    ))
    tasks.append(_task(
        "identity_gate",
        "shot.identity_outfit_qc",
        "cpu",
        ("class_gate",),
        common,
        {"source_hash": source_hash, "gate": "identity_outfit"},
        gate=_gate("identity_outfit", ("identity", "outfit", "accessories", "subject_count", "composition")),
        reuse_accepted_output=True,
    ))
    visual_terminal = "identity_gate"
    if shot.required_capability in _VIDEO_CAPABILITIES:
        tasks.append(_task(
            "temporal_gate",
            "shot.temporal_qc",
            "cpu",
            (visual_terminal,),
            common,
            {"source_hash": source_hash, "gate": "temporal"},
            gate=_gate("temporal", ("flicker", "freeze", "loop", "anatomy", "motion_adherence")),
            reuse_accepted_output=True,
        ))
        visual_terminal = "temporal_gate"

    dialogue_terminal = visual_terminal
    if shot.dialogue_mode is DialogueMode.SEPARATE_VOICE:
        tasks.append(_task(
            "route_voice",
            "media.route_and_budget_gate",
            "control",
            (),
            {
                **common,
                "purpose": "voice",
                "eligible_route_variant_ids": list(shot.eligible_voice_variant_ids),
                "max_project_credits": pack.project.max_credits,
                "allow_precharge_fallback": pack.project.allow_precharge_fallback,
                "max_precharge_route_attempts": pack.project.max_precharge_route_attempts,
                "postcharge_fallback": False,
            },
            {"source_hash": source_hash, "purpose": "voice", "routes": [item.to_dict() for item in bindings if item.purpose == "voice"]},
            gate=_gate("voice_route_budget", ("eligibility", "health", "authoritative_quote", "credit_budget", "voice_rights")),
        ))
        tasks.append(_task(
            "voice_generate",
            "shot.voice_generate",
            "route-bound",
            ("route_voice",),
            {**common, "dialogue": [cue.to_dict() for cue in shot.dialogue]},
            {"source_hash": source_hash, "purpose": "voice"},
            retry_policy=_provider_retry(pack),
            route_binding="voice",
            reuse_accepted_output=True,
        ))
        tasks.append(_task(
            "voice_gate",
            "shot.voice_qc",
            "cpu",
            ("voice_generate",),
            common,
            {"source_hash": source_hash, "gate": "voice"},
            gate=_gate("voice", ("decode", "duration", "clipping", "loudness", "speech_presence", "timbre_receipt")),
            reuse_accepted_output=True,
        ))
        tasks.append(_task(
            "mux",
            "shot.audio_video_mux",
            "cpu",
            (visual_terminal, "voice_gate"),
            common,
            {"source_hash": source_hash, "operation": "mux"},
            resource_locks=("cpu:ffmpeg",),
            reuse_accepted_output=True,
        ))
        dialogue_terminal = "mux"

    if shot.dialogue_mode is not DialogueMode.NONE:
        tasks.append(_task(
            "dialogue_gate",
            "shot.dialogue_alignment_qc",
            "cpu",
            (dialogue_terminal,),
            {**common, "dialogue_mode": shot.dialogue_mode.value},
            {"source_hash": source_hash, "gate": "dialogue_alignment"},
            gate=_gate("dialogue_alignment", ("cue_bounds", "audio_alignment", "speech_text", "lip_sync", "channel_integrity")),
            reuse_accepted_output=True,
        ))
        dialogue_terminal = "dialogue_gate"

    tasks.append(_task(
        "continuity_gate",
        "shot.continuity_qc",
        "cpu",
        (dialogue_terminal,),
        common,
        {"source_hash": source_hash, "gate": "continuity"},
        gate=_gate("continuity", ("scene_state", "character", "wardrobe", "props", "background")),
        reuse_accepted_output=True,
    ))
    tasks.append(_task(
        "accept",
        "shot.acceptance_gate",
        "control",
        ("continuity_gate",),
        common,
        {"source_hash": source_hash, "gate": "shot_acceptance"},
        retry_policy=_never_retry(),
        gate=_gate("shot_acceptance", ("all_hard_gates", "artifact_lineage", "route_receipt", "cost_receipt")),
        reuse_accepted_output=True,
    ))
    plan_material = {
        "compiler_version": COMPILER_VERSION,
        "shot_id": shot.shot_id,
        "source_hash": source_hash,
        "route_bindings": [item.to_dict() for item in bindings],
        "tasks": [item.to_dict() for item in tasks],
    }
    return ShotRunPlan(
        shot_id=shot.shot_id,
        parent_dependency="assets_lock",
        source_hash=source_hash,
        route_bindings=bindings,
        tasks=tuple(tasks),
        plan_hash=_stable_hash(plan_material),
    )


def compile_project(pack: ProductionPack) -> CompiledProject:
    """Compile only structurally accepted packs; never soften lint failures."""

    gate_report = lint_production_pack(pack)
    if not gate_report.ok:
        codes = ", ".join(sorted({finding.code for finding in gate_report.fails}))
        raise ValueError(f"production pack failed pre-spend gate: {codes}")

    shot_runs = tuple(_shot_plan(pack, shot) for shot in sorted(pack.shots, key=lambda item: (item.scene_id, item.ordinal, item.shot_id)))
    pack_ref = {"project_id": pack.project.project_id, "production_pack_hash": pack.content_hash}
    parent: list[ProjectTask] = [
        _task(
            "pack_validate", "project.production_pack_validate", "control", (), pack_ref,
            {"production_pack_hash": pack.content_hash}, retry_policy=_never_retry(),
            gate=_gate("production_pack", ("schema", "bounds", "secret_scan", "deterministic_hash")),
        ),
        _task(
            "rights_gate", "project.rights_and_consent_gate", "control", ("pack_validate",), pack_ref,
            {"production_pack_hash": pack.content_hash, "section": "rights"}, retry_policy=_never_retry(),
            gate=_gate("rights_consent", ("entity_rights", "person_consent", "provider_scope", "voice_scope", "revocation")),
        ),
        _task(
            "concept_lock", "project.concept_platform_lock", "control", ("rights_gate",), pack_ref,
            {"production_pack_hash": pack.content_hash, "section": "concept"}, retry_policy=_never_retry(),
            gate=_gate("concept_platform", ("audience", "usage_scope", "platform", "duration", "aspect", "language")),
        ),
        _task(
            "evidence_gate", "project.evidence_registry_gate", "control", ("concept_lock",), pack_ref,
            {"production_pack_hash": pack.content_hash, "section": "evidence"}, retry_policy=_never_retry(),
            gate=_gate("evidence", ("claim_sources", "content_hashes", "private_evidence")),
        ),
        _task(
            "story_lock", "project.story_continuity_lock", "control", ("evidence_gate",), pack_ref,
            {"production_pack_hash": pack.content_hash, "section": "story"}, retry_policy=_never_retry(),
            gate=_gate("story_continuity", ("scene_order", "before_event_after", "shot_membership", "dialogue_timing")),
        ),
        _task(
            "route_budget_gate", "project.route_budget_policy_gate", "control", ("story_lock",),
            {**pack_ref, "minimum_estimated_credits": gate_report.minimum_estimated_credits, "hard_credit_cap": pack.project.max_credits},
            {"production_pack_hash": pack.content_hash, "section": "routes"}, retry_policy=_never_retry(),
            gate=_gate("route_budget_policy", ("provider_allowlist", "execution_evidence", "live_quote_or_hard_cap", "no_postcharge_fallback")),
        ),
        _task(
            "assets_resolve", "project.assets_resolve", "cpu", ("route_budget_gate",), pack_ref,
            {"production_pack_hash": pack.content_hash, "section": "assets"},
            gate=_gate("asset_resolve", ("ownership", "availability", "mime", "hash", "lineage")),
        ),
        _task(
            "assets_lock", "project.assets_lock", "control", ("assets_resolve",), pack_ref,
            {"production_pack_hash": pack.content_hash, "section": "assets_lock"}, retry_policy=_never_retry(),
            gate=_gate("asset_lock", ("immutable_versions", "character_binding", "consent_snapshot")),
        ),
    ]
    accepted_keys = tuple(f"child:{run.shot_id}:accept" for run in shot_runs)
    shot_plan_hashes = {run.shot_id: run.plan_hash for run in shot_runs}
    parent.extend([
        _task(
            "story_cut", "edit.story_cut", "cpu", ("assets_lock",) + accepted_keys, pack_ref,
            {"project_output": pack.project.to_dict(), "shot_plan_hashes": shot_plan_hashes},
            resource_locks=("cpu:ffmpeg",), reuse_accepted_output=True,
        ),
        _task(
            "captions_mix", "edit.captions_audio_mix", "cpu", ("story_cut",), pack_ref,
            {"project_output": pack.project.to_dict(), "operation": "captions_mix"},
            resource_locks=("cpu:ffmpeg",), reuse_accepted_output=True,
        ),
        _task(
            "mechanical_qc", "delivery.mechanical_qc", "cpu", ("captions_mix",), pack_ref,
            {"production_pack_hash": pack.content_hash, "gate": "mechanical"},
            gate=_gate("mechanical", ("decode", "duration", "fps", "resolution", "audio", "loudness", "black", "freeze", "silence", "captions")),
            reuse_accepted_output=True,
        ),
        _task(
            "semantic_qc", "delivery.semantic_editorial_qc", "review", ("mechanical_qc",), pack_ref,
            {"production_pack_hash": pack.content_hash, "gate": "semantic"},
            gate=_gate("semantic_editorial", ("story", "prompt_adherence", "identity", "continuity", "platform_compliance", "ai_disclosure")),
            reuse_accepted_output=True,
        ),
        _task(
            "proof", "artifact.review_proxy_and_proof", "cpu", ("semantic_qc",), pack_ref,
            {"production_pack_hash": pack.content_hash, "outputs": ["review_proxy", "contact_sheet", "proof_frames", "gate_report"]},
            resource_locks=("cpu:ffmpeg",), reuse_accepted_output=True,
        ),
        _task(
            "approval", "operator.release_approval", "review", ("proof",),
            {**pack_ref, "publishing_authorized": False},
            {"production_pack_hash": pack.content_hash, "gate": "operator_approval"}, retry_policy=_never_retry(),
            gate=_gate("operator_approval", ("review_proxy", "proof", "quality_reports", "cost_receipts", "disclosure"), human=True),
            resource_locks=("review:operator",),
        ),
        _task(
            "package", "publish.package", "cpu", ("approval",),
            {**pack_ref, "publishes_externally": False, "requires_separate_publish_authorization": True},
            {"production_pack_hash": pack.content_hash, "operation": "package_only"},
            retry_policy=_safe_cpu_retry(), resource_locks=("cpu:ffmpeg",), reuse_accepted_output=True,
        ),
    ])
    return CompiledProject(
        project_id=pack.project.project_id,
        pack_hash=pack.content_hash,
        gate_report=gate_report,
        parent_tasks=tuple(parent),
        shot_runs=shot_runs,
    )


def materialize_provider_task(
    shot_run: ShotRunPlan,
    task_key: str,
    route_variant_id: str,
) -> ProjectTask:
    """Bind one route template to an eligible concrete provider lane/lock set.

    This function is intentionally separate from compilation: health and the
    authoritative quote are evaluated by the route task at execution time.  A
    worker must reject the template until this materialization has happened.
    """

    key = _identifier(task_key, "task_key")
    route_id = _identifier(route_variant_id, "route_variant_id")
    task = next((item for item in shot_run.tasks if item.key == key), None)
    if task is None:
        raise ValueError(f"unknown shot task: {key}")
    if task.route_binding is None:
        raise ValueError(f"shot task is not route-bound: {key}")
    binding = next(
        (
            item for item in shot_run.route_bindings
            if item.purpose == task.route_binding and item.route_variant_id == route_id
        ),
        None,
    )
    if binding is None:
        raise ValueError("route variant is not eligible for this task purpose")
    selected = binding.to_dict()
    payload = _thaw(task.payload)
    payload["selected_route"] = selected
    return _task(
        key=task.key,
        task_type=task.task_type,
        lane=binding.resource_lane,
        depends_on=task.depends_on,
        payload=payload,
        input_material={"template_input_hash": task.input_hash, "selected_route": selected},
        retry_policy=task.retry_policy,
        gate=task.gate,
        route_binding=None,
        resource_locks=binding.resource_locks,
        reuse_accepted_output=task.reuse_accepted_output,
    )
