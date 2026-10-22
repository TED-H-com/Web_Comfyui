"""Sanitized bridge from qualification plans to the durable orchestrator.

The bridge deliberately exposes only no-spend contract qualification.  It does
not materialize provider routes, authorize credits, queue ComfyUI, satisfy a
human gate, or claim that a production is complete.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Callable

from .compiler import COMPILER_VERSION
from .execution import (
    CONTRACT_STAGE_TASK_TYPE,
    MAX_QUALIFICATION_NODES,
    PACK_VALIDATE_TASK_TYPE,
    PREPARE_TASK_KEY,
    QUALIFICATION_MODE,
    QUALIFICATION_RECORD_VERSION,
    build_qualification_run_body,
)


PROJECT_RUN_RESPONSE_VERSION = "castle.project-run.response.v1"
PROJECT_RUN_MODE = QUALIFICATION_MODE
_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{20}$")
_TASK_ID_RE = re.compile(r"^task_[0-9a-f]{20}$")
_STAGE_KEY_RE = re.compile(r"^q_[0-9a-f]{20}$")
_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TASK_STATUSES = {
    "pending", "running", "completed", "failed", "cancel_requested",
    "cancelled", "skipped",
}
_SAFE_RUN_STATUSES = {
    "pending", "running", "completed", "failed", "cancel_requested", "cancelled",
}


class ProjectRunError(ValueError):
    """The durable run is absent or is not a Castle project qualification."""


def _project_record(run: Mapping[str, Any]) -> dict[str, Any]:
    value = run.get("input")
    record = value.get("project_record") if isinstance(value, Mapping) else None
    if not isinstance(record, Mapping):
        raise ProjectRunError("run is not a project qualification")
    expected = {
        "schema_version", "mode", "project_id", "pack_hash", "preview_hash",
        "compiler_version", "plan_hash", "node_count",
    }
    if set(record) != expected:
        raise ProjectRunError("project qualification record is malformed")
    if record.get("schema_version") != QUALIFICATION_RECORD_VERSION:
        raise ProjectRunError("project qualification record version is unsupported")
    if record.get("mode") != PROJECT_RUN_MODE:
        raise ProjectRunError("project qualification mode is unsupported")
    if (
        not isinstance(record.get("project_id"), str)
        or _PROJECT_ID_RE.fullmatch(record["project_id"]) is None
        or any(
            not isinstance(record.get(field), str)
            or _SHA256_RE.fullmatch(record[field]) is None
            for field in ("pack_hash", "preview_hash", "plan_hash")
        )
        or record.get("compiler_version") != COMPILER_VERSION
        or isinstance(record.get("node_count"), bool)
        or not isinstance(record.get("node_count"), int)
        or not 1 <= record["node_count"] <= MAX_QUALIFICATION_NODES
    ):
        raise ProjectRunError("project qualification record identity is malformed")
    return {key: record[key] for key in sorted(expected)}


def _safe_task(task: Mapping[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or "")
    task_id = str(task.get("id") or "")
    key = str(task.get("key") or "")
    attempt = task.get("attempt")
    max_attempts = task.get("max_attempts")
    reused_from = task.get("reused_from_task_id")
    if (
        _TASK_ID_RE.fullmatch(task_id) is None
        or status not in _SAFE_TASK_STATUSES
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 0
        or isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
        or attempt >= max_attempts
        or (
            reused_from is not None
            and (
                not isinstance(reused_from, str)
                or _TASK_ID_RE.fullmatch(reused_from) is None
            )
        )
    ):
        raise ProjectRunError("project qualification task state is malformed")
    return {
        "id": task_id,
        "key": key,
        "task_type": str(task.get("task_type") or ""),
        "status": status,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "reused": reused_from is not None,
        "error": "qualification_stage_failed" if task.get("error") else None,
    }


def _safe_timestamp(value: Any, label: str, *, optional: bool) -> int | float | None:
    if value is None and optional:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ProjectRunError(f"project qualification {label} is malformed")
    return value


def _non_runtime_input(task: Mapping[str, Any]) -> dict[str, Any]:
    value = task.get("input")
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ProjectRunError("project qualification task input is malformed")
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _validate_persisted_topology(
    run: Mapping[str, Any],
    record: Mapping[str, Any],
    tasks_raw: list[Mapping[str, Any]],
) -> None:
    """Rebuild the server-owned graph and match its persisted public rows."""

    run_input = run.get("input")
    if (
        not isinstance(run_input, Mapping)
        or set(run_input) != {"global_input", "project_record", "tasks"}
    ):
        raise ProjectRunError("project qualification run input is malformed")
    if run_input.get("global_input") != {} or run_input.get("project_record") != record:
        raise ProjectRunError("project qualification run identity is inconsistent")

    prepare_rows = [item for item in tasks_raw if item.get("key") == PREPARE_TASK_KEY]
    if len(prepare_rows) != 1:
        raise ProjectRunError("project qualification prepare task is missing")
    prepare_input = _non_runtime_input(prepare_rows[0])
    if set(prepare_input) != {"schema_version", "request", "expected_plan_hash"}:
        raise ProjectRunError("project qualification prepare input is malformed")
    try:
        expected_body = build_qualification_run_body(prepare_input["request"])
    except Exception as error:
        raise ProjectRunError("project qualification source contract is invalid") from error
    if (
        expected_body.get("idempotency_key") != run.get("idempotency_key")
        or expected_body.get("input", {}).get("project_record") != record
        or run_input.get("tasks") != expected_body.get("tasks")
        or prepare_input.get("expected_plan_hash") != record.get("plan_hash")
    ):
        raise ProjectRunError("project qualification persisted identity is stale")

    expected_tasks = {item["key"]: item for item in expected_body["tasks"]}
    actual_tasks = {str(item.get("key") or ""): item for item in tasks_raw}
    task_ids = {key: str(item.get("id") or "") for key, item in actual_tasks.items()}
    if (
        set(actual_tasks) != set(expected_tasks)
        or len(task_ids.values()) != len(set(task_ids.values()))
        or any(_TASK_ID_RE.fullmatch(task_id) is None for task_id in task_ids.values())
    ):
        raise ProjectRunError("project qualification task set is inconsistent")
    for key, expected in expected_tasks.items():
        actual = actual_tasks[key]
        expected_dependency_ids = sorted(task_ids[dep] for dep in expected["deps"])
        actual_dependency_ids = actual.get("depends_on_task_ids")
        if (
            actual.get("task_type") != expected["task_type"]
            or actual.get("lane") != "cpu"
            or actual.get("max_attempts") != 2
            or actual.get("reuse_policy") != "input_hash"
            or not isinstance(actual_dependency_ids, list)
            or any(
                not isinstance(task_id, str)
                or _TASK_ID_RE.fullmatch(task_id) is None
                for task_id in actual_dependency_ids
            )
            or len(actual_dependency_ids) != len(set(actual_dependency_ids))
            or sorted(actual_dependency_ids) != expected_dependency_ids
        ):
            raise ProjectRunError("project qualification task policy is inconsistent")
        actual_input = _non_runtime_input(actual)
        expected_input = expected["input"]
        allowed = set(expected_input)
        if key != PREPARE_TASK_KEY:
            allowed.update({"descriptor_artifact_id", "descriptor_content_hash"})
        if set(actual_input) - allowed or any(
            actual_input.get(field) != value for field, value in expected_input.items()
        ):
            raise ProjectRunError("project qualification task contract is inconsistent")


def project_run_response(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project a full orchestrator response without pack/evidence/artifact data."""

    run = raw.get("run") if isinstance(raw, Mapping) else None
    if not isinstance(run, Mapping):
        raise ProjectRunError("orchestrator response is missing its run")
    if run.get("workflow_type") != "custom.dag":
        raise ProjectRunError("run is not a project qualification")
    if _RUN_ID_RE.fullmatch(str(run.get("id") or "")) is None:
        raise ProjectRunError("project qualification run id is malformed")
    run_status = str(run.get("status") or "")
    idempotent_replay = run.get("idempotent_replay", False)
    created_at = _safe_timestamp(run.get("created_at"), "created_at", optional=False)
    updated_at = _safe_timestamp(run.get("updated_at"), "updated_at", optional=False)
    completed_at = _safe_timestamp(run.get("completed_at"), "completed_at", optional=True)
    if (
        run_status not in _SAFE_RUN_STATUSES
        or not isinstance(idempotent_replay, bool)
        or updated_at < created_at
        or (completed_at is not None and completed_at < created_at)
        or (run_status in {"pending", "running", "cancel_requested"} and completed_at is not None)
        or (run_status in {"completed", "failed", "cancelled"} and completed_at is None)
    ):
        raise ProjectRunError("project qualification run state is malformed")
    record = _project_record(run)
    tasks_raw = run.get("tasks")
    if not isinstance(tasks_raw, list) or any(not isinstance(item, Mapping) for item in tasks_raw):
        raise ProjectRunError("project qualification tasks are malformed")
    typed_tasks = list(tasks_raw)
    _validate_persisted_topology(run, record, typed_tasks)
    tasks = [_safe_task(item) for item in typed_tasks]
    if len(tasks) != int(record["node_count"]) + 1:
        raise ProjectRunError("project qualification task count is inconsistent")
    prepare = [task for task in tasks if task["key"] == PREPARE_TASK_KEY]
    stages = [task for task in tasks if task["key"] != PREPARE_TASK_KEY]
    if (
        len(prepare) != 1
        or prepare[0]["task_type"] != PACK_VALIDATE_TASK_TYPE
        or any(
            task["task_type"] != CONTRACT_STAGE_TASK_TYPE
            or _STAGE_KEY_RE.fullmatch(task["key"]) is None
            for task in stages
        )
        or len({task["key"] for task in tasks}) != len(tasks)
    ):
        raise ProjectRunError("project qualification task topology is inconsistent")
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task["status"]] = counts.get(task["status"], 0) + 1
    return {
        "schema_version": PROJECT_RUN_RESPONSE_VERSION,
        "ok": True,
        "mode": PROJECT_RUN_MODE,
        "run": {
            "id": str(run.get("id") or ""),
            "status": run_status,
            "idempotent_replay": idempotent_replay,
            "created_at": created_at,
            "updated_at": updated_at,
            "completed_at": completed_at,
        },
        "project": record,
        "task_counts": counts,
        "tasks": tasks,
        "authority": {
            "qualification_authorized": True,
            "provider_execution_authorized": False,
            "comfy_execution_authorized": False,
            "credentials_accessed": False,
            "credits_authorized": 0,
            "credits_spent": 0,
            "human_gates_satisfied": False,
            "production_complete": False,
        },
    }


def create_project_run_api(
    raw_request: Mapping[str, Any],
    *,
    create_run: Callable[[dict[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate a request before giving its server-derived DAG to orchestrator."""

    if not callable(create_run):
        raise TypeError("create_run must be callable")
    body = build_qualification_run_body(raw_request)
    result = project_run_response(create_run(body))
    if result["project"]["plan_hash"] != body["input"]["project_record"]["plan_hash"]:
        raise ProjectRunError("persisted project qualification identity mismatch")
    return result


def get_project_run_api(
    run_id: str,
    *,
    get_run: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Read one exact qualification run through the same sanitized projection."""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ProjectRunError("invalid project run id")
    if not callable(get_run):
        raise TypeError("get_run must be callable")
    return project_run_response(get_run(run_id))
