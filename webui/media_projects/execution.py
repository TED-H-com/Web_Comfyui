"""Pure qualification-run projection for Castle media projects.

The qualification run is deliberately non-executing.  It persists and checks
the exact production-pack/compiler contract without authorizing providers,
credits, generation, or publishing.  This module performs no I/O and has no
orchestrator dependency; it only produces a strict ``custom.dag`` request for
the server-owned bridge to submit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

from .compiler import (
    COMPILER_VERSION,
    CompiledProject,
    FailureAction,
    GatePolicy,
    ProjectTask,
    RetryMode,
    RetryPolicy,
    compile_project,
)
from .contracts import MAX_PACK_BYTES, ProductionPack, parse_production_pack
from .preview import preview_production_pack


PROJECT_RUN_REQUEST_VERSION = "castle.project-run.request.v1"
QUALIFICATION_MODE = "qualification_no_spend"
QUALIFICATION_PLAN_VERSION = "castle.project-qualification-plan.v1"
QUALIFICATION_NODE_VERSION = "castle.project-qualification-node.v1"
QUALIFICATION_RECORD_VERSION = "castle.project-qualification-record.v1"
PACK_VALIDATE_INPUT_VERSION = "castle.project-pack-validate.input.v1"
CONTRACT_STAGE_INPUT_VERSION = "castle.project-contract-stage.input.v1"

PACK_VALIDATE_TASK_TYPE = "castle.project_pack_validate"
CONTRACT_STAGE_TASK_TYPE = "castle.project_contract_stage"
CUSTOM_DAG_WORKFLOW_TYPE = "custom.dag"
PREPARE_TASK_KEY = "prepare"
QUALIFICATION_PLAN_OUTPUT = "qualification_plan"
PROJECT_PACK_OUTPUT = "project_pack"
_RESERVED_PREPARE_OUTPUTS = {QUALIFICATION_PLAN_OUTPUT, PROJECT_PACK_OUTPUT, "receipt"}

# The production-pack parser owns the strict pack limit.  This only leaves a
# small, deterministic allowance for the request wrapper.
MAX_PROJECT_RUN_REQUEST_BYTES = MAX_PACK_BYTES + 64 * 1024
MAX_QUALIFICATION_NODES = 63

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_TASK_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_OUTPUT_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9._-]{0,127}$")

_REQUEST_FIELDS = {"schema_version", "mode", "preview_hash", "production_pack"}
_PLAN_FIELDS = {
    "schema_version",
    "project_id",
    "pack_hash",
    "preview_hash",
    "compiler_version",
    "nodes",
    "plan_hash",
}
_NODE_FIELDS = {
    "schema_version",
    "node_id",
    "task_key",
    "output_name",
    "source_key",
    "source_task_type",
    "source_lane",
    "compiler_input_hash",
    "depends_on",
    "route_binding",
    "resource_locks",
    "retry_policy",
    "gate",
    "reuse_accepted_output",
    "authority",
}
_RETRY_FIELDS = {
    "mode",
    "max_attempts",
    "backoff_seconds",
    "same_input_hash_required",
    "retryable_outcomes",
    "no_retry_after_states",
    "allow_precharge_route_fallback",
    "max_precharge_route_attempts",
}
_GATE_FIELDS = {"gate_id", "checks", "hard_fail", "human_required", "failure_action"}
_AUTHORITY_FIELDS = {
    "qualification_only",
    "execution_authorized",
    "provider_submission_authorized",
    "credits_authorized",
    "publishing_authorized",
}
_QUALIFICATION_AUTHORITY = {
    "qualification_only": True,
    "execution_authorized": False,
    "provider_submission_authorized": False,
    "credits_authorized": False,
    "publishing_authorized": False,
}


class ProjectQualificationError(ValueError):
    """The no-spend qualification request or projection is invalid."""


class ProjectRunRequestError(ProjectQualificationError):
    """The project-run request is not the exact supported v1 contract."""


class QualificationPlanError(ProjectQualificationError):
    """The deterministic qualification plan is invalid or stale."""


def canonical_json_bytes(value: Any, label: str = "qualification value") -> bytes:
    """Return the one canonical byte representation used by all plan hashes.

    Descriptor artifacts must contain these exact bytes, with no trailing
    newline, so their artifact content hash equals :func:`qualification_node_hash`.
    """

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProjectQualificationError(f"{label} must contain JSON values only") from error


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ProjectQualificationError(f"{label} must be an object with string keys")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    extra = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if extra:
        raise ProjectQualificationError(f"{label} has unknown fields: {', '.join(extra)}")
    if missing:
        raise ProjectQualificationError(f"{label} is missing fields: {', '.join(missing)}")


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProjectQualificationError(f"{label} must be a lowercase SHA256")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ProjectQualificationError(f"{label} must be a lowercase stable identifier")
    return value


def _string_sequence(value: Any, label: str, *, maximum: int = 256) -> list[str]:
    if not isinstance(value, list):
        raise ProjectQualificationError(f"{label} must be an array")
    if len(value) > maximum or any(not isinstance(item, str) for item in value):
        raise ProjectQualificationError(f"{label} contains invalid or too many values")
    if len(value) != len(set(value)):
        raise ProjectQualificationError(f"{label} values must be unique")
    return list(value)


def _valid_node_id(value: Any, label: str = "node_id") -> str:
    if not isinstance(value, str) or len(value) > 384:
        raise ProjectQualificationError(f"{label} is invalid")
    if value.startswith("project:"):
        _identifier(value[len("project:"):], label)
        return value
    if value.startswith("shot:"):
        body = value[len("shot:"):]
        if ":" not in body:
            raise ProjectQualificationError(f"{label} is invalid")
        shot_id, task_key = body.rsplit(":", 1)
        _identifier(shot_id, label)
        _identifier(task_key, label)
        return value
    raise ProjectQualificationError(f"{label} is invalid")


def _node_tokens(node_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:20]
    return f"q_{digest}", f"descriptor_{digest}"


def _canonical_retry_policy(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "retry_policy")
    _exact_keys(raw, _RETRY_FIELDS, "retry_policy")
    try:
        policy = RetryPolicy(
            mode=RetryMode(raw["mode"]),
            max_attempts=raw["max_attempts"],
            backoff_seconds=tuple(raw["backoff_seconds"]),
            same_input_hash_required=raw["same_input_hash_required"],
            retryable_outcomes=tuple(raw["retryable_outcomes"]),
            no_retry_after_states=tuple(raw["no_retry_after_states"]),
            allow_precharge_route_fallback=raw["allow_precharge_route_fallback"],
            max_precharge_route_attempts=raw["max_precharge_route_attempts"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectQualificationError("retry_policy is invalid") from error
    canonical = policy.to_dict()
    if dict(raw) != canonical:
        raise ProjectQualificationError("retry_policy is not canonical")
    return canonical


def _canonical_gate(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = _mapping(value, "gate")
    _exact_keys(raw, _GATE_FIELDS, "gate")
    try:
        gate = GatePolicy(
            gate_id=raw["gate_id"],
            checks=tuple(raw["checks"]),
            hard_fail=raw["hard_fail"],
            human_required=raw["human_required"],
            failure_action=FailureAction(raw["failure_action"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectQualificationError("gate is invalid") from error
    canonical = gate.to_dict()
    if dict(raw) != canonical:
        raise ProjectQualificationError("gate is not canonical")
    return canonical


def validate_qualification_node_descriptor(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize one qualification-only compiler descriptor."""

    value = _mapping(raw, "qualification node descriptor")
    _exact_keys(value, _NODE_FIELDS, "qualification node descriptor")
    if value["schema_version"] != QUALIFICATION_NODE_VERSION:
        raise QualificationPlanError(
            f"qualification node version must be {QUALIFICATION_NODE_VERSION}"
        )
    node_id = _valid_node_id(value["node_id"])
    source_key = _identifier(value["source_key"], "source_key")
    if node_id.startswith("project:"):
        if node_id != f"project:{source_key}":
            raise QualificationPlanError("project node_id does not match source_key")
    elif not node_id.endswith(f":{source_key}"):
        raise QualificationPlanError("shot node_id does not match source_key")

    expected_task_key, expected_output_name = _node_tokens(node_id)
    task_key = value["task_key"]
    output_name = value["output_name"]
    if not isinstance(task_key, str) or not _TASK_TOKEN_RE.fullmatch(task_key):
        raise QualificationPlanError("qualification task_key is invalid")
    if not isinstance(output_name, str) or not _OUTPUT_TOKEN_RE.fullmatch(output_name):
        raise QualificationPlanError("qualification output_name is invalid")
    if task_key != expected_task_key or output_name != expected_output_name:
        raise QualificationPlanError("qualification task/output token is not derived from node_id")

    source_task_type = _identifier(value["source_task_type"], "source_task_type")
    source_lane = value["source_lane"]
    if not isinstance(source_lane, str) or (
        source_lane not in {"control", "cpu", "review", "route-bound"}
        and not _RESOURCE_RE.fullmatch(source_lane)
    ):
        raise QualificationPlanError("source_lane is invalid")
    compiler_input_hash = _sha256(value["compiler_input_hash"], "compiler_input_hash")

    depends_on = _string_sequence(value["depends_on"], "depends_on")
    depends_on = [_valid_node_id(item, "dependency node_id") for item in depends_on]
    if node_id in depends_on:
        raise QualificationPlanError("qualification node cannot depend on itself")

    route_binding = value["route_binding"]
    if route_binding is not None:
        route_binding = _identifier(route_binding, "route_binding")
    resource_locks = _string_sequence(value["resource_locks"], "resource_locks", maximum=64)
    if any(not _RESOURCE_RE.fullmatch(item) for item in resource_locks):
        raise QualificationPlanError("resource_locks contains an invalid lock")
    if route_binding is not None and (source_lane != "route-bound" or resource_locks):
        raise QualificationPlanError("route-bound source metadata is inconsistent")
    if source_lane == "route-bound" and route_binding is None:
        raise QualificationPlanError("route-bound source requires route_binding")

    if not isinstance(value["reuse_accepted_output"], bool):
        raise QualificationPlanError("reuse_accepted_output must be a boolean")
    authority = _mapping(value["authority"], "authority")
    _exact_keys(authority, _AUTHORITY_FIELDS, "authority")
    if dict(authority) != _QUALIFICATION_AUTHORITY:
        raise QualificationPlanError("qualification node authority must remain no-spend")

    descriptor = {
        "schema_version": QUALIFICATION_NODE_VERSION,
        "node_id": node_id,
        "task_key": task_key,
        "output_name": output_name,
        "source_key": source_key,
        "source_task_type": source_task_type,
        "source_lane": source_lane,
        "compiler_input_hash": compiler_input_hash,
        "depends_on": depends_on,
        "route_binding": route_binding,
        "resource_locks": resource_locks,
        "retry_policy": _canonical_retry_policy(value["retry_policy"]),
        "gate": _canonical_gate(value["gate"]),
        "reuse_accepted_output": value["reuse_accepted_output"],
        "authority": dict(_QUALIFICATION_AUTHORITY),
    }
    # Reject non-canonical scalar/container representations even if a custom
    # Mapping implementation compares unusually.
    canonical_json_bytes(descriptor, "qualification node descriptor")
    return descriptor


def qualification_node_hash(raw: Mapping[str, Any]) -> str:
    """Hash the exact canonical descriptor artifact bytes (without newline)."""

    descriptor = validate_qualification_node_descriptor(raw)
    return hashlib.sha256(
        canonical_json_bytes(descriptor, "qualification node descriptor")
    ).hexdigest()


def _parent_dependency(dependency: str) -> str:
    if dependency.startswith("child:"):
        try:
            prefix, shot_id, task_key = dependency.split(":", 2)
        except ValueError as error:
            raise QualificationPlanError("compiled child dependency is invalid") from error
        if prefix != "child":
            raise QualificationPlanError("compiled child dependency is invalid")
        _identifier(shot_id, "compiled child shot_id")
        _identifier(task_key, "compiled child task key")
        return f"shot:{shot_id}:{task_key}"
    _identifier(dependency, "compiled parent dependency")
    return f"project:{dependency}"


def _descriptor(task: ProjectTask, *, node_id: str, depends_on: list[str]) -> dict[str, Any]:
    task_key, output_name = _node_tokens(node_id)
    return validate_qualification_node_descriptor({
        "schema_version": QUALIFICATION_NODE_VERSION,
        "node_id": node_id,
        "task_key": task_key,
        "output_name": output_name,
        "source_key": task.key,
        "source_task_type": task.task_type,
        "source_lane": task.lane,
        "compiler_input_hash": task.input_hash,
        "depends_on": list(depends_on),
        "route_binding": task.route_binding,
        "resource_locks": list(task.resource_locks),
        "retry_policy": task.retry_policy.to_dict(),
        "gate": task.gate.to_dict() if task.gate is not None else None,
        "reuse_accepted_output": task.reuse_accepted_output,
        "authority": dict(_QUALIFICATION_AUTHORITY),
    })


def _flatten_compiled_project(compiled: CompiledProject) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for task in compiled.parent_tasks:
        nodes.append(_descriptor(
            task,
            node_id=f"project:{task.key}",
            depends_on=[_parent_dependency(item) for item in task.depends_on],
        ))
    for run in compiled.shot_runs:
        for task in run.tasks:
            dependencies = [f"shot:{run.shot_id}:{item}" for item in task.depends_on]
            if not dependencies:
                dependencies = [f"project:{run.parent_dependency}"]
            nodes.append(_descriptor(
                task,
                node_id=f"shot:{run.shot_id}:{task.key}",
                depends_on=dependencies,
            ))

    if len(nodes) > MAX_QUALIFICATION_NODES:
        raise QualificationPlanError(
            "compiled project exceeds 63 qualification nodes; prepare plus nodes "
            "must fit the custom.dag 64-task limit"
        )
    node_ids = [node["node_id"] for node in nodes]
    task_keys = [node["task_key"] for node in nodes]
    output_names = [node["output_name"] for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise QualificationPlanError("compiled qualification node ids collide")
    if len(task_keys) != len(set(task_keys)):
        raise QualificationPlanError("derived qualification task keys collide")
    if len(output_names) != len(set(output_names)):
        raise QualificationPlanError("derived qualification output names collide")
    if set(output_names) & _RESERVED_PREPARE_OUTPUTS:
        raise QualificationPlanError("derived qualification output name is reserved")
    known = set(node_ids)
    for node in nodes:
        unknown = set(node["depends_on"]) - known
        if unknown:
            raise QualificationPlanError(
                f"compiled node {node['node_id']} depends on an unknown node"
            )
    return nodes


def _parse_context(
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], ProductionPack, dict[str, Any], CompiledProject]:
    try:
        value = _mapping(raw, "project-run request")
        _exact_keys(value, _REQUEST_FIELDS, "project-run request")
    except ProjectQualificationError as error:
        raise ProjectRunRequestError(str(error)) from error
    if value["schema_version"] != PROJECT_RUN_REQUEST_VERSION:
        raise ProjectRunRequestError(
            f"project-run request version must be {PROJECT_RUN_REQUEST_VERSION}"
        )
    if value["mode"] != QUALIFICATION_MODE:
        raise ProjectRunRequestError(f"project-run mode must be {QUALIFICATION_MODE}")
    try:
        expected_preview_hash = _sha256(value["preview_hash"], "preview_hash")
    except ProjectQualificationError as error:
        raise ProjectRunRequestError(str(error)) from error
    pack_raw = value["production_pack"]
    if not isinstance(pack_raw, Mapping):
        raise ProjectRunRequestError("production_pack must be an object")
    try:
        pack = parse_production_pack(pack_raw)
    except (TypeError, ValueError) as error:
        raise ProjectRunRequestError(str(error)) from error
    request = {
        "schema_version": PROJECT_RUN_REQUEST_VERSION,
        "mode": QUALIFICATION_MODE,
        "preview_hash": expected_preview_hash,
        "production_pack": pack.to_dict(),
    }
    if len(canonical_json_bytes(request, "project-run request")) > MAX_PROJECT_RUN_REQUEST_BYTES:
        raise ProjectRunRequestError(
            f"project-run request exceeds {MAX_PROJECT_RUN_REQUEST_BYTES} bytes"
        )

    try:
        preview = preview_production_pack(pack)
    except (TypeError, ValueError) as error:
        raise ProjectRunRequestError(str(error)) from error
    actual_preview_hash = str(preview.get("preview_hash") or "")
    if not hmac.compare_digest(expected_preview_hash, actual_preview_hash):
        raise ProjectRunRequestError("preview_hash is stale for this production_pack")
    if not preview.get("static_plan_ready"):
        raise ProjectRunRequestError("production_pack failed the no-spend lint gate")
    try:
        compiled = compile_project(pack)
    except (TypeError, ValueError) as error:
        raise ProjectRunRequestError("production_pack failed deterministic compilation") from error
    if compiled.pack_hash != pack.content_hash:
        raise ProjectRunRequestError("compiled production-pack hash is inconsistent")
    return request, pack, preview, compiled


def parse_project_run_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Parse, re-preview, and recompile an exact no-spend request.

    The returned request is canonical data.  A stale preview, lint failure, or
    compilation mismatch is rejected instead of being silently refreshed.
    """

    request, _, _, _ = _parse_context(raw)
    return request


def _build_plan(
    pack: ProductionPack,
    preview: Mapping[str, Any],
    compiled: CompiledProject,
) -> dict[str, Any]:
    nodes = _flatten_compiled_project(compiled)
    material: dict[str, Any] = {
        "schema_version": QUALIFICATION_PLAN_VERSION,
        "project_id": compiled.project_id,
        "pack_hash": pack.content_hash,
        "preview_hash": preview["preview_hash"],
        "compiler_version": compiled.compiler_version,
        "nodes": nodes,
    }
    plan = {
        **material,
        "plan_hash": hashlib.sha256(
            canonical_json_bytes(material, "qualification plan")
        ).hexdigest(),
    }
    return validate_qualification_plan(plan)


def validate_qualification_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact plan schema and its deterministic self-hash."""

    value = _mapping(raw, "qualification plan")
    _exact_keys(value, _PLAN_FIELDS, "qualification plan")
    if value["schema_version"] != QUALIFICATION_PLAN_VERSION:
        raise QualificationPlanError(
            f"qualification plan version must be {QUALIFICATION_PLAN_VERSION}"
        )
    project_id = _identifier(value["project_id"], "project_id")
    pack_hash = _sha256(value["pack_hash"], "pack_hash")
    preview_hash = _sha256(value["preview_hash"], "preview_hash")
    if value["compiler_version"] != COMPILER_VERSION:
        raise QualificationPlanError("qualification plan compiler_version is stale")
    if not isinstance(value["nodes"], list):
        raise QualificationPlanError("qualification plan nodes must be an array")
    if not value["nodes"]:
        raise QualificationPlanError("qualification plan requires at least one node")
    if len(value["nodes"]) > MAX_QUALIFICATION_NODES:
        raise QualificationPlanError("qualification plan exceeds the custom.dag task limit")
    nodes = [validate_qualification_node_descriptor(item) for item in value["nodes"]]

    for field in ("node_id", "task_key", "output_name"):
        values = [node[field] for node in nodes]
        if len(values) != len(set(values)):
            raise QualificationPlanError(f"qualification plan has duplicate {field}")
    known = {node["node_id"] for node in nodes}
    for node in nodes:
        if set(node["depends_on"]) - known:
            raise QualificationPlanError(
                f"qualification node {node['node_id']} has an unknown dependency"
            )
    temporary: set[str] = set()
    permanent: set[str] = set()
    dependencies = {node["node_id"]: node["depends_on"] for node in nodes}

    def visit(node_id: str) -> None:
        if node_id in permanent:
            return
        if node_id in temporary:
            raise QualificationPlanError(
                f"qualification plan contains a dependency cycle at {node_id}"
            )
        temporary.add(node_id)
        for dependency in dependencies[node_id]:
            visit(dependency)
        temporary.remove(node_id)
        permanent.add(node_id)

    for node_id in dependencies:
        visit(node_id)

    material = {
        "schema_version": QUALIFICATION_PLAN_VERSION,
        "project_id": project_id,
        "pack_hash": pack_hash,
        "preview_hash": preview_hash,
        "compiler_version": COMPILER_VERSION,
        "nodes": nodes,
    }
    expected_hash = hashlib.sha256(
        canonical_json_bytes(material, "qualification plan")
    ).hexdigest()
    plan_hash = _sha256(value["plan_hash"], "plan_hash")
    if not hmac.compare_digest(expected_hash, plan_hash):
        raise QualificationPlanError("qualification plan_hash is stale")
    return {**material, "plan_hash": plan_hash}


def prepare_qualification_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the deterministic no-spend plan from an exact run request."""

    _, pack, preview, compiled = _parse_context(raw)
    return _build_plan(pack, preview, compiled)


def _project_record(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": QUALIFICATION_RECORD_VERSION,
        "mode": QUALIFICATION_MODE,
        "project_id": plan["project_id"],
        "pack_hash": plan["pack_hash"],
        "preview_hash": plan["preview_hash"],
        "compiler_version": plan["compiler_version"],
        "plan_hash": plan["plan_hash"],
        "node_count": len(plan["nodes"]),
    }


def build_qualification_run_body(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Build a safe, persistent ``custom.dag`` no-spend qualification body."""

    request, pack, preview, compiled = _parse_context(raw)
    plan = _build_plan(pack, preview, compiled)
    node_task_keys = {node["node_id"]: node["task_key"] for node in plan["nodes"]}

    prepare_task = {
        "key": PREPARE_TASK_KEY,
        "task_type": PACK_VALIDATE_TASK_TYPE,
        "deps": [],
        "bindings": {},
        "input": {
            "schema_version": PACK_VALIDATE_INPUT_VERSION,
            "request": request,
            "expected_plan_hash": plan["plan_hash"],
        },
        "reuse": True,
        # Qualification is deterministic and spends nothing, so one recovery
        # attempt is safe when a worker dies before writing its sentinel.
        "max_attempts": 2,
    }
    tasks: list[dict[str, Any]] = [prepare_task]
    for node in plan["nodes"]:
        output_name = node["output_name"]
        descriptor_hash = qualification_node_hash(node)
        tasks.append({
            "key": node["task_key"],
            "task_type": CONTRACT_STAGE_TASK_TYPE,
            "deps": [
                PREPARE_TASK_KEY,
                *(node_task_keys[item] for item in node["depends_on"]),
            ],
            "bindings": {
                "descriptor_artifact_id": {
                    "task": PREPARE_TASK_KEY,
                    "output": output_name,
                    "select": "artifact_id",
                },
                "descriptor_content_hash": {
                    "task": PREPARE_TASK_KEY,
                    "output": output_name,
                    "select": "content_hash",
                },
            },
            "input": {
                "schema_version": CONTRACT_STAGE_INPUT_VERSION,
                "expected_node_id": node["node_id"],
                "expected_descriptor_sha256": descriptor_hash,
            },
            "reuse": True,
            "max_attempts": 2,
        })

    if len(tasks) > 64:
        # Kept adjacent to the emitted graph as a defense against future
        # projection changes, in addition to the compiler-node limit above.
        raise QualificationPlanError("qualification custom.dag exceeds 64 tasks")
    return {
        "workflow_type": CUSTOM_DAG_WORKFLOW_TYPE,
        "title": f"Project qualification: {plan['project_id']}",
        "idempotency_key": f"castle-project-qualification-v1-{plan['plan_hash']}",
        "input": {
            "global_input": {},
            "project_record": _project_record(plan),
        },
        "tasks": tasks,
    }


__all__ = [
    "CONTRACT_STAGE_INPUT_VERSION",
    "CONTRACT_STAGE_TASK_TYPE",
    "MAX_PROJECT_RUN_REQUEST_BYTES",
    "MAX_QUALIFICATION_NODES",
    "PACK_VALIDATE_INPUT_VERSION",
    "PACK_VALIDATE_TASK_TYPE",
    "PROJECT_PACK_OUTPUT",
    "PROJECT_RUN_REQUEST_VERSION",
    "PREPARE_TASK_KEY",
    "QUALIFICATION_MODE",
    "QUALIFICATION_NODE_VERSION",
    "QUALIFICATION_PLAN_OUTPUT",
    "QUALIFICATION_PLAN_VERSION",
    "QUALIFICATION_RECORD_VERSION",
    "ProjectQualificationError",
    "ProjectRunRequestError",
    "QualificationPlanError",
    "build_qualification_run_body",
    "canonical_json_bytes",
    "parse_project_run_request",
    "prepare_qualification_plan",
    "qualification_node_hash",
    "validate_qualification_node_descriptor",
    "validate_qualification_plan",
]
