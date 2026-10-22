"""Pure, fail-closed preview for Castle production packs.

This module intentionally knows nothing about HTTP, files, provider clients,
ComfyUI, or the orchestrator.  It parses one in-memory request, runs the static
lint gate, and (only for a green gate) projects the compiler output into a
compact, non-executable DAG.  Runtime readiness and authoritative prices must
be checked by later, separately authorized stages.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .compiler import CompiledProject, ProjectTask, compile_project
from .contracts import (
    MAX_PACK_BYTES,
    ProductionPack,
    parse_production_pack,
)
from .lint import GateReport, lint_production_pack


PREVIEW_REQUEST_VERSION = "castle.project-dry-run.request.v1"
PREVIEW_VERSION = "castle.project-dry-run.response.v1"
MAX_PREVIEW_REQUEST_BYTES = MAX_PACK_BYTES + 64 * 1024
MAX_PREVIEW_SHOTS = 200
MAX_PREVIEW_ROUTE_VARIANTS = 256
MAX_PREVIEW_LINT_WORK = 4_096
MAX_PREVIEW_FAILS = 100
MAX_PREVIEW_WARNS = 50
MAX_PREVIEW_RESPONSE_BYTES = 4 * 1024 * 1024


class PreviewError(ValueError):
    """Base class for safe public preview validation failures."""


class PreviewRequestError(PreviewError):
    """The request wrapper is not the exact supported v1 shape."""


class PreviewPackError(PreviewError):
    """The wrapped production pack does not satisfy its strict contract."""


class PreviewLimitError(PreviewPackError):
    """A valid production pack exceeds this preview endpoint's tighter cap."""


# Compatibility spelling for adapters written during the initial design pass.
ProductionPackValidationError = PreviewPackError


def _canonical_bytes(value: Any, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain JSON values only") from error


def _preview_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value, "production-pack preview")).hexdigest()


def _node_id(scope: str, task_key: str) -> str:
    return f"{scope}:{task_key}"


def _parent_dependency(dependency: str) -> str:
    if dependency.startswith("child:"):
        _, shot_id, task_key = dependency.split(":", 2)
        return _node_id(f"shot:{shot_id}", task_key)
    return _node_id("project", dependency)


def _compact_task(
    task: ProjectTask,
    *,
    scope: str,
    dependencies: list[str],
) -> dict[str, Any]:
    """Project a compiler task without payloads, locks, receipts, or private refs."""

    return {
        "key": _node_id(scope, task.key),
        "task_type": task.task_type,
        "lane": task.lane,
        "depends_on": dependencies,
        "input_hash": task.input_hash,
        "route_binding": task.route_binding,
        "gate_id": task.gate.gate_id if task.gate is not None else None,
        "human_required": task.gate.human_required if task.gate is not None else False,
        "materialization_required": task.route_binding is not None,
    }


def _compact_dag(compiled: CompiledProject) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for task in compiled.parent_tasks:
        nodes.append(_compact_task(
            task,
            scope="project",
            dependencies=[_parent_dependency(item) for item in task.depends_on],
        ))
    for run in compiled.shot_runs:
        scope = f"shot:{run.shot_id}"
        for task in run.tasks:
            dependencies = [_node_id(scope, item) for item in task.depends_on]
            if not dependencies:
                dependencies = [_node_id("project", run.parent_dependency)]
            nodes.append(_compact_task(
                task,
                scope=scope,
                dependencies=dependencies,
            ))
    return {
        "project_id": compiled.project_id,
        "compiler_version": compiled.compiler_version,
        "node_count": len(nodes),
        "nodes": nodes,
    }


def _route_candidates(pack: ProductionPack) -> list[dict[str, Any]]:
    usages: dict[str, list[dict[str, str]]] = {
        route.route_variant_id: [] for route in pack.route_variants
    }
    for shot in sorted(pack.shots, key=lambda item: (item.scene_id, item.ordinal, item.shot_id)):
        for route_id in shot.eligible_visual_variant_ids:
            if route_id in usages:
                usages[route_id].append({"shot_id": shot.shot_id, "purpose": "visual"})
        for route_id in shot.eligible_voice_variant_ids:
            if route_id in usages:
                usages[route_id].append({"shot_id": shot.shot_id, "purpose": "voice"})
    return [
        {
            "route_variant_id": route.route_variant_id,
            "provider_id": route.provider_id,
            "execution_domain_declared": route.execution_domain.value,
            "capability": route.capability.value,
            "visual_classes": sorted(item.value for item in route.visual_classes),
            "option_keys": sorted(route.option_keys),
            "release_ready_declared": route.release_ready,
            "estimated_credits_declared": route.estimated_credits,
            "resource_lane_declared": route.resource_lane,
            "used_by": usages[route.route_variant_id],
            "runtime_verified": False,
            "quote_verified": False,
        }
        for route in sorted(pack.route_variants, key=lambda item: item.route_variant_id)
    ]


def _expected_expensive_nodes(pack: ProductionPack) -> list[dict[str, Any]]:
    """Describe possible generation work even when lint blocks compilation."""

    result: list[dict[str, Any]] = []
    for shot in sorted(pack.shots, key=lambda item: (item.scene_id, item.ordinal, item.shot_id)):
        candidates = [("visual_generate", "shot.visual_generate", "visual", shot.eligible_visual_variant_ids)]
        if shot.eligible_voice_variant_ids:
            candidates.append(("voice_generate", "shot.voice_generate", "voice", shot.eligible_voice_variant_ids))
        for task_key, task_type, purpose, raw_route_ids in candidates:
            route_ids = sorted(raw_route_ids)
            result.append({
                "shot_id": shot.shot_id,
                "task_key": task_key,
                "task_type": task_type,
                "purpose": purpose,
                "eligible_route_variant_ids": route_ids,
                "requires_separate_execution_authorization": True,
            })
    return sorted(result, key=lambda item: (item["shot_id"], item["task_key"]))


def _authority(static_plan_ready: bool) -> dict[str, Any]:
    return {
        "computed_by": "castle_server",
        "static_contract_checked": True,
        "dag_compiled": static_plan_ready,
        "route_metadata_source": "production_pack_declaration",
        "runtime_catalog_checked": False,
        "provider_health_checked": False,
        "runtime_readiness_checked": False,
        "authoritative_quotes_checked": False,
        "credentials_accessed": False,
    }


def _side_effects() -> dict[str, Any]:
    return {
        "project_records_created": 0,
        "orchestrator_runs_created": 0,
        "tasks_scheduled": 0,
        "scheduler_state_changes": 0,
        "comfy_prompts_queued": 0,
        "provider_transports_attempted": 0,
        "credits_authorized": 0,
        "credits_spent": 0,
        "publishing_attempts": 0,
    }


def _lint_work(pack: ProductionPack) -> int:
    """Bound the request relationships that the static linter must traverse."""

    work = (
        len(pack.entities)
        + len(pack.evidence)
        + len(pack.route_variants)
        + len(pack.scenes)
        + len(pack.shots)
    )
    for entity in pack.entities:
        work += len(entity.consent_evidence_ids)
    for scene in pack.scenes:
        work += len(scene.shot_ids) + len(scene.state_before) + len(scene.state_after)
    for shot in pack.shots:
        route_count = len(shot.eligible_visual_variant_ids) + len(
            shot.eligible_voice_variant_ids
        )
        work += route_count * max(1, len(shot.entity_ids))
        work += route_count * max(1, len(shot.required_options))
        work += len(shot.entity_ids) + len(shot.evidence_ids)
        work += len(shot.dialogue) + len(shot.continuity_keys)
        work += sum(1 + len(claim.evidence_ids) for claim in shot.factual_claims)
    return work


def _public_gate_report(gate: GateReport) -> dict[str, Any]:
    """Expose actionable blockers without private evidence registry IDs."""

    def findings(values, maximum: int) -> list[dict[str, str]]:
        return [
            {"code": item.code, "path": item.path}
            for item in values[:maximum]
        ]

    fail_count = len(gate.fails)
    warn_count = len(gate.warns)
    return {
        "ok": gate.ok,
        "fails": findings(gate.fails, MAX_PREVIEW_FAILS),
        "warns": findings(gate.warns, MAX_PREVIEW_WARNS),
        "fail_count": fail_count,
        "warn_count": warn_count,
        "findings_truncated": (
            fail_count > MAX_PREVIEW_FAILS or warn_count > MAX_PREVIEW_WARNS
        ),
        "pack_hash": gate.pack_hash,
        "minimum_estimated_credits": gate.minimum_estimated_credits,
        "contract_version": gate.contract_version,
    }


def _summary(pack: ProductionPack, gate: GateReport, compiled: CompiledProject | None) -> dict[str, Any]:
    return {
        "entity_count": len(pack.entities),
        "scene_count": len(pack.scenes),
        "shot_count": len(pack.shots),
        "route_variant_count": len(pack.route_variants),
        "parent_task_count": 0 if compiled is None else len(compiled.parent_tasks),
        "shot_task_count": 0 if compiled is None else sum(
            len(run.tasks) for run in compiled.shot_runs
        ),
        "expected_expensive_node_count": sum(
            1 + (1 if shot.eligible_voice_variant_ids else 0) for shot in pack.shots
        ),
        "minimum_declared_estimated_credits": gate.minimum_estimated_credits,
    }


def preview_production_pack(pack: ProductionPack) -> dict[str, Any]:
    """Return a deterministic, in-memory static preview for a parsed pack."""

    if not isinstance(pack, ProductionPack):
        raise TypeError("pack must be a ProductionPack")
    if len(pack.shots) > MAX_PREVIEW_SHOTS:
        raise PreviewLimitError(f"preview supports at most {MAX_PREVIEW_SHOTS} shots")
    if len(pack.route_variants) > MAX_PREVIEW_ROUTE_VARIANTS:
        raise PreviewLimitError(
            f"preview supports at most {MAX_PREVIEW_ROUTE_VARIANTS} route variants"
        )
    if _lint_work(pack) > MAX_PREVIEW_LINT_WORK:
        raise PreviewLimitError("preview production pack is too complex")
    gate: GateReport = lint_production_pack(pack)
    compiled = compile_project(pack) if gate.ok else None
    static_plan_ready = compiled is not None
    material: dict[str, Any] = {
        "schema_version": PREVIEW_VERSION,
        "ok": True,
        "mode": "preview_only",
        "static_plan_ready": static_plan_ready,
        "execution_authorized": False,
        "pack": {
            "version": pack.version,
            "project_id": pack.project.project_id,
            "hash": pack.content_hash,
        },
        "authority": _authority(static_plan_ready),
        "side_effects": _side_effects(),
        "gate_report": _public_gate_report(gate),
        "summary": _summary(pack, gate, compiled),
        "route_candidates": _route_candidates(pack),
        "expected_expensive_nodes": _expected_expensive_nodes(pack),
        "dag": _compact_dag(compiled) if compiled is not None else None,
    }
    result = {**material, "preview_hash": _preview_hash(material)}
    if len(_canonical_bytes(result, "production-pack preview")) > MAX_PREVIEW_RESPONSE_BYTES:
        raise PreviewLimitError("preview response is too large")
    return result


def preview_production_pack_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Parse the exact v1 wrapper and return a pure static preview.

    Unknown wrapper fields are rejected.  This function performs no I/O and
    accepts no actor, runtime, provider client, scheduler, or persistence
    dependency by design.
    """

    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        raise PreviewRequestError("preview request must be an object with string keys")
    keys = set(raw)
    required = {"schema_version", "production_pack"}
    extra = sorted(keys - required)
    missing = sorted(required - keys)
    if extra:
        raise PreviewRequestError(f"preview request has unknown fields: {', '.join(extra)}")
    if missing:
        raise PreviewRequestError(f"preview request is missing fields: {', '.join(missing)}")
    if raw["schema_version"] != PREVIEW_REQUEST_VERSION:
        raise PreviewRequestError(f"preview request version must be {PREVIEW_REQUEST_VERSION}")
    try:
        encoded = _canonical_bytes(raw, "preview request")
    except ValueError as error:
        raise PreviewRequestError(str(error)) from error
    if len(encoded) > MAX_PREVIEW_REQUEST_BYTES:
        raise PreviewRequestError(f"preview request exceeds {MAX_PREVIEW_REQUEST_BYTES} bytes")
    pack_raw = raw["production_pack"]
    if not isinstance(pack_raw, Mapping):
        raise PreviewPackError("production_pack must be an object")
    try:
        pack = parse_production_pack(pack_raw)
    except ValueError as error:
        raise PreviewPackError(str(error)) from error
    return preview_production_pack(pack)
