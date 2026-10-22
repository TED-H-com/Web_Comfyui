from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

from .config import settings_from_env
from .db import connect, initialize, json_loads, stable_json, transaction, utc_ts
from .events import add_event, list_events
from .resources import release_resource, reservations_public, reserve_resource
from .review import (
    HARD_REQUIRED_ROLES,
    finalize_reel_review,
    prepare_reel_review,
    parse_reel_review_binding_query,
    recompute_reel_review_status,
    reel_review_artifacts,
    submit_reel_review,
    _load_qc_summary,
)
from .review_transport import load_reel_review_transport_state, reel_review_transport
from .reel_autopublish_policy import evaluate_production_kpis
from .store import (
    adopt_artifact,
    artifact_abs_path,
    artifact_public,
    clone_reused_artifact,
    record_artifact_approval,
    reusable_task_artifacts,
    set_artifact_gate_state,
    set_artifact_tags,
    update_artifact_metadata,
)
from .tasks import (
    CUSTOM_DAG_WORKFLOW_TYPE,
    REEL_COVER_POSTGEN_RETRY_TOKEN,
    REEL_M4_WORKFLOW_VERSION,
    _reel_m4_external_implementation_hash,
    _reel_native_implementation_hash,
    infer_title,
    input_hash,
    normalize_custom_graph,
    reel_cover_postgen_retry_eligible,
    reel_script_provider_outage,
    task_input_signature,
    workflow_graph,
    workflow_topology_graph,
    workflow_version_id,
)
from .worker import request_run_cancel, request_task_cancel


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


MAIN_AUDIO_RESYNTH_MAX_IDXS = 8
MAIN_AUDIO_CACHE_REVISION_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def _sanitize_main_audio_resynth_idxs(value) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("main_audio_resynth_idxs must be an array of turn indexes")
    clean: list[int] = []
    seen: set[int] = set()
    for raw in value:
        if type(raw) is not int or raw < 0 or raw > 99:
            raise ValueError("main_audio_resynth_idxs must contain indexes 0..99")
        if raw in seen:
            continue
        seen.add(raw)
        clean.append(raw)
    if len(clean) > MAIN_AUDIO_RESYNTH_MAX_IDXS:
        raise ValueError("main_audio_resynth_idxs is too large for one repair")
    return sorted(clean)


def _sanitize_reel_pipeline_input(payload: dict) -> dict:
    for multi_key in ("episodes", "reel_ids", "batch", "ids"):
        if multi_key in payload and payload.get(multi_key):
            raise ValueError("ai_sister.reel_pipeline is single-episode only")
    legacy_keys = {
        "from_stage", "fromStage", "force_stages", "forceStages", "dry_run",
        "dryRun", "force", "env", "dry_run_sleep_seconds",
        "reservation_ttl_seconds",
    }
    supplied_legacy = sorted(key for key in legacy_keys if key in payload)
    if supplied_legacy:
        raise ValueError(
            "ai_sister.reel_pipeline M4 no longer accepts legacy controls: "
            + ", ".join(supplied_legacy))
    allowed_keys = {
        "episode", "reel_id", "preset", "sig", "stage_sig",
        "approved_script", "main_audio_resynth_idxs",
        "main_audio_cache_revision",
    }
    cleaned = {key: value for key, value in payload.items() if key in allowed_keys}
    preset = str(cleaned.get("preset") or "single-rerun").strip()
    if preset not in {"single-rerun", "rough-cut"}:
        raise ValueError("ai_sister.reel_pipeline preset must be single-rerun or rough-cut")
    cleaned["preset"] = preset
    if "approved_script" in cleaned:
        approved = cleaned["approved_script"]
        if not isinstance(approved, dict):
            raise ValueError("approved_script must be a hash-pinned input ref")
        # This is a recovery/review route, not an alternate release path.
        # Persist the eligibility decision on the run so ambient service
        # environment cannot later publish it by accident.
        cleaned["release_mode"] = "review_only"
    resynth_idxs = _sanitize_main_audio_resynth_idxs(
        cleaned.get("main_audio_resynth_idxs")
    )
    if resynth_idxs:
        if "approved_script" not in cleaned:
            raise ValueError(
                "main_audio_resynth_idxs requires approved_script recovery"
            )
        revision = str(cleaned.get("main_audio_cache_revision") or "")
        if MAIN_AUDIO_CACHE_REVISION_RE.fullmatch(revision) is None:
            raise ValueError(
                "main_audio_cache_revision is required for partial main audio resynth"
            )
        cleaned["main_audio_resynth_idxs"] = resynth_idxs
        cleaned["main_audio_cache_revision"] = revision
    else:
        cleaned.pop("main_audio_resynth_idxs", None)
        if "main_audio_cache_revision" in cleaned:
            raise ValueError(
                "main_audio_cache_revision requires main_audio_resynth_idxs"
            )
    return cleaned


def _reel_input_episode(payload: dict) -> str:
    return str(payload.get("episode") or payload.get("reel_id") or "").strip()


def _failed_reel_task_identity(
    conn: sqlite3.Connection,
    run_id: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    row = conn.execute(
        """
        select key, task_type, input_json from tasks
        where run_id = ? and status = 'failed'
        order by created_at, rowid
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None, None, None, None
    payload = json_loads(row["input_json"], {})
    if not isinstance(payload, dict):
        payload = {}
    implementation_hash = str(payload.get("implementation_hash") or "").strip()
    stage = str(payload.get("stage") or row["key"] or "").strip()
    return (
        str(row["key"] or "").strip() or None,
        str(row["task_type"] or "").strip() or None,
        stage or None,
        implementation_hash or None,
    )


def _current_reel_implementation_hash(
    task_type: str | None,
    stage: str | None,
) -> str | None:
    if not task_type or not stage:
        return None
    source_root = Path(os.environ.get(
        "CASTLE_REEL5_SOURCE_ROOT",
        str(Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp"),
    )).expanduser().resolve()
    try:
        if task_type in {"castle.reel_stage_cpu", "castle.reel_stage_gpu"}:
            return _reel_native_implementation_hash(source_root, stage)
        external = {
            "castle.reel_cover": "cover",
            "ai_sister.render_sisters": "render_sisters",
            "ai_sister.render_host": "render_host",
            "castle.reel_assemble": "assemble",
            "castle.reel_qc": "qc",
        }.get(task_type)
        if external is not None:
            return _reel_m4_external_implementation_hash(source_root, external)
    except Exception:
        return None
    return None


def _reel_source_root() -> Path:
    return Path(os.environ.get(
        "CASTLE_REEL5_SOURCE_ROOT",
        str(Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp"),
    )).expanduser().resolve()


def _failed_reel_run_episode(conn: sqlite3.Connection, run_id: str) -> str | None:
    row = conn.execute(
        "select input_json from workflow_runs where id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json_loads(row["input_json"], {})
    if not isinstance(payload, dict):
        return None
    episode = _reel_input_episode(payload)
    return episode or None


def _run_synth_listener_rejection_ledger_input(
    conn: sqlite3.Connection,
    run_id: str,
) -> dict | None:
    row = conn.execute(
        """
        select input_json from tasks
        where run_id = ? and key = 'synth'
        order by created_at, rowid
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json_loads(row["input_json"], {})
    if not isinstance(payload, dict):
        return None
    ledger = payload.get("listener_rejection_ledger")
    return ledger if isinstance(ledger, dict) else None


def _sha256_file_quiet(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _listener_rejection_ledger_path_from_helper(
    source_root: Path,
    episode: str,
) -> Path | None:
    helper = source_root / "listener_rejection_ledger.py"
    if helper.is_symlink() or not helper.is_file():
        return None
    module_name = f"_castle_listener_rejection_ledger_{uuid.uuid4().hex}"
    old_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(module_name, helper)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(source_root))
        spec.loader.exec_module(module)
        path_fn = getattr(module, "listener_rejection_ledger_path", None)
        if not callable(path_fn):
            return None
        path = Path(path_fn(source_root, episode)).expanduser()
        resolved = path.resolve()
        resolved.relative_to(source_root)
        return resolved
    except Exception:
        return None
    finally:
        sys.path[:] = old_path


def _current_listener_rejection_ledger_input(
    source_root: Path,
    episode: str,
) -> dict | None:
    path = _listener_rejection_ledger_path_from_helper(source_root, episode)
    if path is None:
        return None
    try:
        relative = path.relative_to(source_root).as_posix()
    except ValueError:
        return None
    if path.is_symlink():
        return None
    if not path.exists():
        return {"exists": False, "path": relative, "sha256": ""}
    if not path.is_file():
        return None
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    digest = _sha256_file_quiet(path)
    if not digest:
        return None
    return {"exists": True, "path": relative, "sha256": digest}


def _listener_rejection_ledger_changed_for_failed_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> bool:
    episode = _failed_reel_run_episode(conn, run_id)
    if not episode:
        return False
    recorded = _run_synth_listener_rejection_ledger_input(conn, run_id)
    if not isinstance(recorded, dict):
        return False
    recorded_exists = recorded.get("exists")
    recorded_sha256 = recorded.get("sha256")
    if recorded_exists is True:
        if not isinstance(recorded_sha256, str) or not recorded_sha256:
            return False
    elif recorded_exists is False:
        recorded_sha256 = ""
    else:
        return False

    current = _current_listener_rejection_ledger_input(_reel_source_root(), episode)
    if current is None:
        return False
    if current.get("exists") is True and not current.get("sha256"):
        return False
    return (
        bool(current.get("exists")) != bool(recorded_exists)
        or str(current.get("sha256") or "") != str(recorded_sha256 or "")
    )


def _failed_reel_relevant_task_inputs_changed(
    conn: sqlite3.Connection,
    run_id: str,
) -> bool:
    run = conn.execute(
        "select workflow_type, input_json from workflow_runs where id = ?",
        (run_id,),
    ).fetchone()
    if run is None or run["workflow_type"] != "ai_sister.reel_pipeline":
        return False
    payload = json_loads(run["input_json"], {})
    if not isinstance(payload, dict):
        return False
    try:
        graph = _graph_from_body(run["workflow_type"], payload, {})
    except Exception:
        return False
    current_by_key: dict[str, tuple[str, dict]] = {}
    for task_spec in graph.get("tasks") or []:
        key = str(task_spec.get("key") or "").strip()
        task_type = str(task_spec.get("task_type") or "").strip()
        if not key or not task_type:
            continue
        task_input = _task_input_for_spec(task_spec, payload)
        current_by_key[key] = (task_type, task_input)

    task_rows = conn.execute(
        """
        select id, key, task_type, input_hash, input_json from tasks
        where run_id = ?
        """,
        (run_id,),
    ).fetchall()
    tasks_by_id = {str(row["id"]): row for row in task_rows}
    failed = conn.execute(
        """
        select id from tasks
        where run_id = ? and status = 'failed'
        order by created_at, rowid
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if failed is None:
        return False

    parents: dict[str, set[str]] = {}
    for row in conn.execute(
        """
        select td.task_id, td.depends_on_task_id
        from task_dependencies td
        join tasks child on child.id = td.task_id
        join tasks parent on parent.id = td.depends_on_task_id
        where child.run_id = ? and parent.run_id = ?
        """,
        (run_id, run_id),
    ):
        parents.setdefault(str(row["task_id"]), set()).add(str(row["depends_on_task_id"]))

    relevant = {str(failed["id"])}
    stack = [str(failed["id"])]
    while stack:
        task_id = stack.pop()
        for parent_id in parents.get(task_id, set()):
            if parent_id not in relevant:
                relevant.add(parent_id)
                stack.append(parent_id)

    for task_id in relevant:
        recorded = tasks_by_id.get(task_id)
        if recorded is None:
            return True
        key = str(recorded["key"] or "").strip()
        task_type = str(recorded["task_type"] or "").strip()
        recorded_hash = str(recorded["input_hash"] or "").strip()
        if not key or not task_type or not recorded_hash:
            return True
        current = current_by_key.get(key)
        if current is None:
            return True
        current_task_type, current_input = current
        recorded_input = json_loads(recorded["input_json"], {})
        if not isinstance(recorded_input, dict):
            return True
        if task_input_signature(task_type, recorded_input) != recorded_hash:
            return True
        if (
            "listener_rejection_ledger" in current_input
            or "listener_rejection_ledger" in recorded_input
        ):
            # Ledger changes have their own JSON-validating stale check. Raw
            # source-file hashes here would let malformed ledgers unlock the
            # fail-closed generation latch before that validator can refuse it.
            current_input = dict(current_input)
            recorded_input = dict(recorded_input)
            current_input.pop("listener_rejection_ledger", None)
            recorded_input.pop("listener_rejection_ledger", None)
            current_hash = task_input_signature(task_type, current_input)
            recorded_hash = task_input_signature(task_type, recorded_input)
        else:
            current_hash = task_input_signature(task_type, current_input)
        if current_task_type != task_type or current_hash != recorded_hash:
            return True
    return False


def _failed_reel_run_is_stale(conn: sqlite3.Connection, run_id: str) -> bool:
    _key, task_type, stage, recorded_hash = _failed_reel_task_identity(conn, run_id)
    implementation_changed = False
    if recorded_hash:
        current_hash = _current_reel_implementation_hash(task_type, stage)
        implementation_changed = bool(current_hash and current_hash != recorded_hash)
    elif task_type and not _current_reel_implementation_hash(task_type, stage):
        # No identity was ever declared for this task type, so there is nothing
        # to compare and the guard cannot honestly claim a repeat would be
        # identical. castle.reel_prepare_render is the live case: it records no
        # implementation hash, so an episode that died there could never be
        # re-admitted no matter what changed in the code that judged it. Not
        # knowing is not the same as knowing it is unchanged.
        return True
    return (
        implementation_changed
        or _failed_reel_relevant_task_inputs_changed(conn, run_id)
        or _listener_rejection_ledger_changed_for_failed_run(conn, run_id)
    )


def _failed_approved_reel_provider_retryable(
    conn: sqlite3.Connection,
    run_id: str,
) -> bool:
    """Allow a fresh immutable run after the beats provider failed closed."""
    row = conn.execute(
        """
        select task_type, input_json, error from tasks
        where run_id = ? and status = 'failed'
        order by created_at, rowid
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if row is None or row["task_type"] != "castle.reel_stage_cpu":
        return False
    payload = json_loads(row["input_json"], {})
    if not isinstance(payload, dict) or payload.get("stage") != "beats":
        return False
    error = str(row["error"] or "").lower()
    return (
        "peer semantic prefilter" in error
        and "failed closed" in error
    )


def _failed_approved_reel_partial_base_retryable(
    conn: sqlite3.Connection,
    run_id: str,
    candidate: dict,
) -> bool:
    """Permit the full approved recovery after an invalid partial merge base."""
    if candidate.get("main_audio_resynth_idxs"):
        return False
    prior_row = conn.execute(
        "select input_json from workflow_runs where id = ?",
        (run_id,),
    ).fetchone()
    prior = json_loads(prior_row["input_json"], {}) if prior_row else {}
    if not isinstance(prior, dict) or not prior.get("main_audio_resynth_idxs"):
        return False
    row = conn.execute(
        """
        select task_type, input_json, error from tasks
        where run_id = ? and status = 'failed'
        order by created_at, rowid
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if row is None or row["task_type"] != "castle.reel_stage_gpu":
        return False
    task_input = json_loads(row["input_json"], {})
    if not isinstance(task_input, dict) or task_input.get("stage") != "synth":
        return False
    error = str(row["error"] or "").lower()
    return (
        "partial resynth baseline coverage is incomplete" in error
        or "partial resynth requires current committed main audio" in error
        or "partial resynth baseline file is missing or unsafe" in error
    )



def _enforce_reel_run_admission(conn: sqlite3.Connection, payload: dict) -> None:
    """Stop version/signature churn after a terminal same-episode result."""
    episode = _reel_input_episode(payload)
    rows = conn.execute(
        """
        select id, status, input_json from workflow_runs
        where workflow_type = 'ai_sister.reel_pipeline'
        order by created_at desc, rowid desc
        """
    ).fetchall()
    same_episode = []
    for row in rows:
        prior_input = json_loads(row["input_json"], {})
        if isinstance(prior_input, dict) and _reel_input_episode(prior_input) == episode:
            same_episode.append((row, prior_input))

    active_run = next((
        row for row, _prior in same_episode
        if row["status"] in {"pending", "running", "cancel_requested"}
    ), None)
    unfinished = next((
        row for row, _prior in same_episode
        if conn.execute(
            """
            select 1 from tasks
            where run_id = ?
              and status in ('pending', 'running', 'cancel_requested')
            limit 1
            """,
            (row["id"],),
        ).fetchone() is not None
    ), None)
    if active_run is not None or unfinished is not None:
        raise ValueError(
            "same-episode Reel already has an active run or unfinished task; "
            "quiesce its existing run before any new admission"
        )

    approved = payload.get("approved_script")
    if isinstance(approved, dict):
        approved_hash = str(approved.get("content_hash") or "")
        if any(
            isinstance(prior.get("approved_script"), dict)
            and prior["approved_script"].get("content_hash") == approved_hash
            and (
                prior_row["status"] in {"completed", "completed_approved"}
                or (
                    prior_row["status"] == "failed"
                    and not _failed_reel_run_is_stale(conn, prior_row["id"])
                    and not _failed_approved_reel_provider_retryable(
                        conn, prior_row["id"])
                    and not _failed_approved_reel_partial_base_retryable(
                        conn, prior_row["id"], payload)
                )
            )
            for prior_row, prior in same_episode
        ):
            raise ValueError(
                "this exact approved_script already has a Reel recovery run; "
                "reuse its idempotency key"
            )
        return

    # Every generated failure is a durable episode latch. Later legacy rows
    # marked completed/cancelled and approved recovery runs cannot erase it.
    # Only a future, separately persisted operator-grant action may do that;
    # request payloads are never authority.
    #
    # needs_review is deliberately not a latch. A draft the operator has not read
    # yet says nothing about whether the episode can be generated, and latching on
    # it retired episodes permanently one at a time as each one queued for review.
    latched_generated = next((
        row for row, prior in same_episode
        if not isinstance(prior.get("approved_script"), dict)
        and row["status"] == "failed"
        and not _failed_reel_run_is_stale(conn, row["id"])
    ), None)
    if latched_generated is None:
        return
    raise ValueError(
        "same-episode Reel is terminal failed; generated successors "
        "are blocked. Use one hash-verified approved_script recovery run"
    )


def _copy_requested(body: dict, default: bool = True) -> bool:
    if "copy" not in body:
        return default
    return _truthy(body.get("copy"))


def _bounded_attempts(value, default: int = 1) -> int:
    try:
        attempts = int(value)
    except (TypeError, ValueError):
        attempts = default
    return max(1, min(attempts, 5))


def _ensure_workflow_version(conn: sqlite3.Connection, workflow_type: str, graph: dict) -> str:
    version_id = workflow_version_id(workflow_type, graph)
    version = str(graph.get("version") or "v1")
    if workflow_type == CUSTOM_DAG_WORKFLOW_TYPE:
        version = f"{version}:{version_id.rsplit(':', 1)[-1]}"
    now = utc_ts()
    stored_graph = workflow_topology_graph(graph)
    conn.execute(
        """
        insert into workflow_versions(id, workflow_type, version, task_graph_json, created_at)
        values (?, ?, ?, ?, ?)
        on conflict(id) do nothing
        """,
        (version_id, workflow_type, version, stable_json(stored_graph), now),
    )
    return version_id


def _graph_from_body(workflow_type: str, payload: dict, body: dict) -> dict:
    if workflow_type == CUSTOM_DAG_WORKFLOW_TYPE or isinstance(body.get("tasks"), list) or isinstance(payload.get("tasks"), list):
        if workflow_type != CUSTOM_DAG_WORKFLOW_TYPE:
            raise ValueError("top-level tasks require workflow_type custom.dag")
        return normalize_custom_graph(body, payload)
    graph = workflow_graph(workflow_type, payload)
    default_attempts = _bounded_attempts(
        body.get("max_attempts")
        or body.get("maxAttempts")
        or payload.get("max_attempts")
        or payload.get("maxAttempts"),
        default=1,
    )
    reuse_policy = "input_hash" if _truthy(body.get("reuse") or payload.get("reuse")) else "off"
    declared_graph = (
        workflow_type == "ai_sister.reel_pipeline"
        and graph.get("version") == REEL_M4_WORKFLOW_VERSION
    )
    for task in graph["tasks"]:
        if not isinstance(task.get("input"), dict):
            task["input"] = dict(payload)
        if not declared_graph:
            task["max_attempts"] = default_attempts
            task["reuse_policy"] = reuse_policy
    return graph


def _task_input_for_spec(task_spec: dict, fallback_payload: dict) -> dict:
    task_input = task_spec.get("input")
    return dict(task_input) if isinstance(task_input, dict) else dict(fallback_payload)


def _clean_task_payload(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


def _find_reusable_artifacts(conn: sqlite3.Connection, task_type: str, thash: str) -> list[sqlite3.Row]:
    return reusable_task_artifacts(conn, task_type, thash)


def _maybe_complete_new_run(conn: sqlite3.Connection, run_id: str) -> None:
    counts = {
        row["status"]: row["count"]
        for row in conn.execute("select status, count(*) as count from tasks where run_id=? group by status", (run_id,)).fetchall()
    }
    if not counts or counts.get("pending") or counts.get("running") or counts.get("cancel_requested"):
        return
    now = utc_ts()
    if counts.get("failed"):
        conn.execute("update workflow_runs set status='failed', completed_at=?, updated_at=? where id=?", (now, now, run_id))
        add_event(conn, "run.failed", "Run failed", run_id=run_id, level="error")
    elif counts.get("cancelled") and not counts.get("completed"):
        conn.execute("update workflow_runs set status='cancelled', completed_at=?, updated_at=? where id=?", (now, now, run_id))
        add_event(conn, "run.cancelled", "Run cancelled", run_id=run_id, level="warning")
    elif counts.get("cancelled"):
        conn.execute("update workflow_runs set status='cancelled', completed_at=?, updated_at=? where id=?", (now, now, run_id))
        add_event(conn, "run.cancelled", "Run cancelled after partial work", run_id=run_id, level="warning")
    else:
        conn.execute(
            "update workflow_runs set status='completed', started_at=coalesce(started_at, ?), completed_at=?, updated_at=? where id=?",
            (now, now, now, run_id),
        )
        add_event(conn, "run.completed", "Run completed from reusable artifacts", run_id=run_id)


def _latest_task_progress(conn: sqlite3.Connection, task_id: str) -> dict | None:
    row = conn.execute(
        """
        select * from events
        where task_id = ? and event_type in ('task.progress', 'task.gpu_queue')
        order by id desc
        limit 1
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    data = json_loads(row["data_json"], {})
    progress = {
        "event": row["event_type"],
        "label": row["message"],
        "created_at": row["created_at"],
    }
    if isinstance(data, dict):
        progress.update(data)
    return progress


def _run_public(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item["input"] = json_loads(item.pop("input_json", None), {})
    tasks = conn.execute(
        "select * from tasks where run_id = ? order by created_at asc",
        (item["id"],),
    ).fetchall()
    dependency_ids: dict[str, list[str]] = {}
    for dependency in conn.execute(
        """
        select d.task_id, d.depends_on_task_id
        from task_dependencies d
        join tasks child on child.id = d.task_id
        where child.run_id = ?
        order by d.task_id, d.depends_on_task_id
        """,
        (item["id"],),
    ).fetchall():
        dependency_ids.setdefault(str(dependency["task_id"]), []).append(
            str(dependency["depends_on_task_id"])
        )
    item["tasks"] = []
    for task_row in tasks:
        task = _task_public(task_row, conn)
        task["depends_on_task_ids"] = dependency_ids.get(str(task["id"]), [])
        item["tasks"].append(task)
    artifacts = conn.execute(
        "select * from artifacts where run_id = ? order by created_at desc",
        (item["id"],),
    ).fetchall()
    item["artifacts"] = [artifact_public(a, conn) for a in artifacts]
    return item


def _recompute_reel_status_for_artifact(conn: sqlite3.Connection, artifact: dict) -> None:
    run_id = str(artifact.get("run_id") or "").strip()
    if not run_id:
        return
    run = conn.execute("select workflow_type from workflow_runs where id = ?", (run_id,)).fetchone()
    if run is not None and run["workflow_type"] == "ai_sister.reel_pipeline":
        recompute_reel_review_status(conn, run_id)


def _task_public(row: sqlite3.Row | dict, conn: sqlite3.Connection | None = None) -> dict:
    item = dict(row)
    item["input"] = json_loads(item.pop("input_json", None), {})
    if item.get("log_uri"):
        item["log_url"] = f"/api/orchestrator/tasks/{item['id']}/log"
    if conn is not None:
        progress = _latest_task_progress(conn, item["id"])
        if progress:
            item["progress"] = progress
    return item


def create_run_api(body: dict) -> dict:
    initialize()
    workflow_type = str(body.get("workflow_type") or body.get("workflowType") or "").strip()
    if not workflow_type:
        raise ValueError("workflow_type is required")
    payload = body.get("input") if isinstance(body.get("input"), dict) else {}
    if workflow_type == "ai_sister.reel_pipeline":
        payload = _sanitize_reel_pipeline_input(payload)
    graph = _graph_from_body(workflow_type, payload, body)
    if REEL_COVER_POSTGEN_RETRY_TOKEN in payload or any(
        task.get("task_type") == "castle.reel_cover"
        and isinstance(task.get("input"), dict)
        and REEL_COVER_POSTGEN_RETRY_TOKEN in task["input"]
        for task in graph.get("tasks", [])
    ):
        raise ValueError("reserved cover retry authorization cannot be supplied")
    run_input = dict(payload)
    if isinstance(body.get("tasks"), list) and "tasks" not in run_input:
        run_input["tasks"] = body["tasks"]
    title = str(body.get("title") or infer_title(workflow_type, payload)).strip()[:160]
    priority = int(body.get("priority") or 0)
    idem = str(body.get("idempotency_key") or body.get("idempotencyKey") or "").strip() or None
    parent_run_id = str(body.get("parent_run_id") or body.get("parentRunId") or body.get("rerun_of_run_id") or "").strip() or None
    ihash = input_hash({"workflow_type": workflow_type, "input": run_input, "graph": graph})
    with connect() as conn:
        with transaction(conn):
            if idem:
                existing = conn.execute(
                    "select * from workflow_runs where idempotency_key = ?",
                    (idem,),
                ).fetchone()
                if existing is not None:
                    public = _run_public(conn, existing)
                    public["idempotent_replay"] = True
                    return {"ok": True, "run": public, "idempotent": True}
            if workflow_type == "ai_sister.reel_pipeline":
                _enforce_reel_run_admission(conn, payload)
            version_id = _ensure_workflow_version(conn, workflow_type, graph)
            now = utc_ts()
            run_id = _new_id("run")
            conn.execute(
                """
                insert into workflow_runs(
                  id, workflow_version_id, workflow_type, title, status,
                  idempotency_key, input_json, input_hash, priority,
                  created_at, updated_at, parent_run_id
                ) values (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, version_id, workflow_type, title, idem, stable_json(run_input), ihash, priority, now, now, parent_run_id),
            )
            task_ids_by_key: dict[str, str] = {}
            for task_spec in graph["tasks"]:
                task_id = _new_id("task")
                task_input = _task_input_for_spec(task_spec, payload)
                bindings = task_spec.get("bindings") if isinstance(task_spec.get("bindings"), dict) else {}
                thash = task_input_signature(task_spec["task_type"], task_input)
                task_payload = dict(task_input)
                if bindings:
                    task_payload["_input_bindings"] = bindings
                task_payload.update({
                    "_run_id": run_id,
                    "_task_id": task_id,
                    "_workflow_type": workflow_type,
                    "_workflow_version_id": version_id,
                })
                max_attempts = _bounded_attempts(task_spec.get("max_attempts"), default=1)
                reuse_policy = str(task_spec.get("reuse_policy") or "off")
                # A bound node has no final semantic signature until every
                # upstream artifact exists.  The worker resolves bindings and
                # performs this same reuse lookup atomically when the node is
                # ready to claim.
                reusable = (
                    _find_reusable_artifacts(conn, task_spec["task_type"], thash)
                    if reuse_policy == "input_hash" and not bindings else []
                )
                task_status = "completed" if reusable else "pending"
                completed_at = now if reusable else None
                reused_from_task_id = str(reusable[0]["source_task_id"] or reusable[0]["producing_task_id"]) if reusable else None
                conn.execute(
                    """
                    insert into tasks(
                      id, run_id, key, task_type, status, lane, priority,
                      attempt, max_attempts, reuse_policy, reused_from_task_id,
                      input_json, input_hash, created_at, updated_at, completed_at
                    ) values (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        run_id,
                        task_spec["key"],
                        task_spec["task_type"],
                        task_status,
                        task_spec.get("lane", "cpu"),
                        priority,
                        max_attempts,
                        reuse_policy,
                        reused_from_task_id,
                        stable_json(task_payload),
                        thash,
                        now,
                        now,
                        completed_at,
                    ),
                )
                conn.execute(
                    "insert into task_inputs(task_id, name, artifact_id, value_json) values (?, 'payload', null, ?)",
                    (task_id, stable_json(task_payload)),
                )
                task_ids_by_key[task_spec["key"]] = task_id
                if reusable:
                    cloned = []
                    for row in reusable:
                        cloned.append(clone_reused_artifact(
                            conn,
                            row,
                            run_id=run_id,
                            task_id=task_id,
                            input_hash=thash,
                            output_name=str(row["output_name"] or "reused"),
                        ))
                    add_event(
                        conn,
                        "task.reused",
                        "Task reused completed artifacts",
                        run_id=run_id,
                        task_id=task_id,
                        data={
                            "reused_from_task_id": reused_from_task_id,
                            "artifacts": [artifact["id"] for artifact in cloned],
                            "input_hash": thash,
                        },
                    )
            for task_spec in graph["tasks"]:
                task_id = task_ids_by_key[task_spec["key"]]
                for dep_key in task_spec.get("deps") or []:
                    conn.execute(
                        "insert into task_dependencies(task_id, depends_on_task_id) values (?, ?)",
                        (task_id, task_ids_by_key[dep_key]),
                    )
            add_event(conn, "run.created", f"Created run {title}", run_id=run_id, data={"workflow_type": workflow_type})
            _maybe_complete_new_run(conn, run_id)
            row = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
            return {"ok": True, "run": _run_public(conn, row), "idempotent": False}


def list_runs_api(limit: int = 30, status: str | None = None) -> dict:
    initialize()
    limit = max(1, min(int(limit or 30), 100))
    with connect() as conn:
        if status:
            rows = conn.execute(
                "select * from workflow_runs where status = ? order by created_at desc limit ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "select * from workflow_runs order by created_at desc limit ?",
                (limit,),
            ).fetchall()
        runs = []
        for row in rows:
            item = dict(row)
            item["input"] = json_loads(item.pop("input_json", None), {})
            item["task_counts"] = {
                r["status"]: r["count"]
                for r in conn.execute(
                    "select status, count(*) as count from tasks where run_id = ? group by status",
                    (item["id"],),
                ).fetchall()
            }
            runs.append(item)
        return {"ok": True, "runs": runs}


def _artifact_role_public(conn: sqlite3.Connection, roles: dict[str, dict], role: str) -> dict | None:
    row = roles.get(role)
    return artifact_public(row, conn) if row else None


def _reel_review_summary(conn: sqlite3.Connection, run_id: str, roles: dict[str, dict]) -> dict:
    gated_roles = [role for role in HARD_REQUIRED_ROLES if role in roles]
    role_states = {role: roles[role]["gate_state"] for role in gated_roles}
    missing_roles = [role for role in HARD_REQUIRED_ROLES if role not in roles]
    if missing_roles:
        status = "needs_review"
    elif any(state in {"rejected", "needs_review", "failed"} for state in role_states.values()):
        status = "needs_review"
    elif all(role_states.get(role) == "approved" for role in HARD_REQUIRED_ROLES) and all(
        state == "approved" for state in role_states.values()
    ):
        status = "completed_approved"
    elif gated_roles:
        status = "needs_review"
    else:
        status = "needs_review"
    return {
        "status": status,
        "missing_roles": missing_roles,
        "required_roles": [role for role in HARD_REQUIRED_ROLES if role in roles],
        "required_role_states": role_states,
    }


def _reel_qc_summary(conn: sqlite3.Connection, qc_row: dict | None) -> dict | None:
    if not qc_row:
        return None
    qc = _load_qc_summary(conn, qc_row)
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "UNKNOWN": 0}
    for check in qc.get("checks") or []:
        level = str(check.get("level") or "UNKNOWN").upper()
        counts[level if level in counts else "UNKNOWN"] += 1
    assessment = qc.get("quality_assessment")
    if not isinstance(assessment, dict):
        assessment = {}
    return {
        "verdict": qc.get("verdict") or "UNKNOWN",
        "score": assessment.get("score"),
        "goal_score": assessment.get("goal_score"),
        "goal_met": assessment.get("goal_met"),
        "assessment_status": assessment.get("assessment_status"),
        "catastrophic_defects": assessment.get("catastrophic_defects") or [],
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "warn": counts["WARN"],
        "unknown": counts["UNKNOWN"],
        "check_count": sum(counts.values()),
    }


def _reel_failure_summary(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute(
        """
        select key, task_type, status, error, completed_at, updated_at
        from tasks
        where run_id = ? and status in ('failed', 'cancelled', 'skipped')
        order by updated_at desc, created_at desc
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_reel_reviews_api(limit: int = 80) -> dict:
    initialize()
    limit = max(1, min(int(limit or 80), 200))
    with connect() as conn:
        rows = conn.execute(
            """
            select * from workflow_runs
            where workflow_type = 'ai_sister.reel_pipeline'
            order by created_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        items = []
        transport_state = load_reel_review_transport_state()
        for row in rows:
            run = dict(row)
            run["input"] = json_loads(run.pop("input_json", None), {})
            roles = reel_review_artifacts(conn, run["id"])
            artifacts = {
                role: _artifact_role_public(conn, roles, role)
                for role in ("final", "cover", "qc", "proxy", "review_page")
                if roles.get(role)
            }
            items.append({
                "run": run,
                "episode": str(run["input"].get("episode") or run["input"].get("reel_id") or ""),
                "review": _reel_review_summary(conn, run["id"], roles),
                "artifacts": artifacts,
                "qc": _reel_qc_summary(conn, roles.get("qc")),
                "failure": _reel_failure_summary(conn, run["id"]),
                "transport": reel_review_transport(
                    run["id"], roles, state_snapshot=transport_state),
            })
        production_records = []
        for item in reversed(items):
            run_status = str(item["run"].get("status") or "")
            if run_status not in {
                "completed", "completed_approved", "needs_review", "failed",
            }:
                continue
            qc = item.get("qc") or {}
            production_records.append({
                "completed": "final" in item["artifacts"],
                "human_decision": (
                    "approved"
                    if item["review"].get("status") == "completed_approved"
                    else "pending"
                ),
                "quality_score": qc.get("score"),
                "quality_verdict": qc.get("verdict"),
                "catastrophic_defects": qc.get("catastrophic_defects") or [],
            })
        return {
            "ok": True,
            "runs": items,
            "production_kpis": evaluate_production_kpis(
                production_records,
                autopublish_enabled=_truthy(
                    os.environ.get("CASTLE_REEL_SYNC_ON_COMPLETE")),
            ),
        }


def get_run_api(run_id: str) -> dict:
    initialize()
    with connect() as conn:
        row = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Run not found")
        events = list_events(conn, after=0, limit=100, run_id=run_id)
        run = _run_public(conn, row)
        run["events"] = events
        return {"ok": True, "run": run}


def ensure_reel_review_api(run_id: str) -> dict:
    initialize()
    with connect() as conn:
        prepared = prepare_reel_review(conn, run_id)
        with transaction(conn):
            review = finalize_reel_review(conn, run_id, prepared)
            row = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
            return {"ok": True, "review": review, "run": _run_public(conn, row)}


def render_bound_reel_review_page_api(run_id: str, query: str) -> dict:
    binding = parse_reel_review_binding_query(query)
    if binding is None:
        raise ValueError("Reel review media binding is required")
    initialize()
    with connect() as conn:
        prepared = prepare_reel_review(conn, run_id, expected_binding=binding)
        roles = prepared["roles"]
        return {
            "ok": True,
            "html": prepared["page"],
            "proxy_artifact_id": roles["proxy"]["id"],
            "final_artifact_id": roles["final"]["id"],
        }


def review_reel_run_api(run_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    decision = str(body.get("decision") or body.get("verdict") or "").strip()
    if not decision:
        if _truthy(body.get("pass") or body.get("approved")):
            decision = "pass"
        elif _truthy(body.get("fail") or body.get("failed")):
            decision = "fail"
    actor = str(body.get("actor") or "operator")[:120]
    note = str(body.get("note") or "")[:1000]
    with connect() as conn:
        prepared = prepare_reel_review(conn, run_id)
        with transaction(conn):
            ensure_result = finalize_reel_review(conn, run_id, prepared)
            review = submit_reel_review(conn, run_id, decision, actor=actor, note=note, ensure_result=ensure_result)
            row = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
            return {"ok": True, "review": review, "run": _run_public(conn, row)}


def cancel_run_api(run_id: str) -> dict:
    initialize()
    changed = request_run_cancel(run_id)
    return {"ok": True, "run_id": run_id, "changed": changed}


def cancel_task_api(task_id: str) -> dict:
    initialize()
    changed = request_task_cancel(task_id)
    with connect() as conn:
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Task not found")
        run = conn.execute("select * from workflow_runs where id = ?", (row["run_id"],)).fetchone()
        return {"ok": True, "task": _task_public(row, conn), "run": _run_public(conn, run), "changed": changed}


def rerun_run_api(run_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    with connect() as conn:
        row = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Run not found")
        original_input = json_loads(row["input_json"], {})
    request = {
        "workflow_type": row["workflow_type"],
        "title": str(body.get("title") or f"{row['title']} rerun")[:160],
        "input": original_input if isinstance(original_input, dict) else {},
        "priority": int(body.get("priority") if body.get("priority") is not None else row["priority"]),
        "parent_run_id": run_id,
    }
    idem = str(body.get("idempotency_key") or body.get("idempotencyKey") or "").strip()
    if idem:
        request["idempotency_key"] = idem
    for key in ("max_attempts", "maxAttempts", "reuse"):
        if key in body:
            request[key] = body[key]
    return create_run_api(request)


def _downstream_tasks(conn: sqlite3.Connection, task_id: str, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        with recursive downstream(task_id) as (
          select task_id from task_dependencies where depends_on_task_id = ?
          union
          select d.task_id from task_dependencies d
          join downstream x on x.task_id = d.depends_on_task_id
        )
        select t.* from tasks t
        join downstream d on d.task_id = t.id
        where t.run_id = ?
        order by t.created_at asc
        """,
        (task_id, run_id),
    ).fetchall()


def retry_task_api(task_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    reset_downstream = not (body.get("reset_downstream") is False or body.get("resetDownstream") is False)
    now = utc_ts()
    with connect() as conn:
        with transaction(conn):
            task = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError("Task not found")
            run = conn.execute(
                "select * from workflow_runs where id = ?", (task["run_id"],),
            ).fetchone()
            if run is None:
                raise KeyError("Run not found")
            reel_run = run["workflow_type"] == "ai_sister.reel_pipeline"
            if reel_run and run["status"] in {
                "failed", "needs_review", "completed", "completed_approved",
                "cancelled", "cancel_requested",
            }:
                raise ValueError(
                    "terminal Reel runs are immutable; task retry is disabled"
                )
            run_input = json_loads(run["input_json"], {})
            review_only = (
                reel_run
                and isinstance(run_input, dict)
                and run_input.get("release_mode") == "review_only"
            )
            if review_only:
                raise ValueError(
                    "review-only Reel runs are immutable; task retry is disabled"
                )
            if task["status"] not in ("failed", "cancelled"):
                raise ValueError("Only failed or cancelled tasks can be retried")
            if (
                task["task_type"] == "castle.reel_cover"
                and str(task["error"] or "").startswith("billing_block:")
            ):
                raise ValueError(
                    "billing-blocked castle.reel_cover tasks cannot be requeued; resolve billing and create a new run"
                )
            if reel_script_provider_outage(dict(task), str(task["error"] or "")):
                raise ValueError(
                    "provider-outage script tasks are terminal and cannot be requeued; "
                    "use a new hash-pinned approved_script recovery run"
                )
            payload = json_loads(task["input_json"], {})
            if not isinstance(payload, dict):
                payload = {}
            payload.pop(REEL_COVER_POSTGEN_RETRY_TOKEN, None)
            patch = body.get("input_patch") or body.get("inputPatch")
            if (
                isinstance(patch, dict)
                and REEL_COVER_POSTGEN_RETRY_TOKEN in patch
            ):
                raise ValueError(
                    "reserved cover retry authorization cannot be patched")
            recover_postgen_cover = (
                task["task_type"] == "castle.reel_cover"
                and task["status"] == "failed"
                and not isinstance(patch, dict)
                and reel_cover_postgen_retry_eligible(task["error"])
            )
            if isinstance(patch, dict):
                payload.update(patch)
            payload.update({
                "_run_id": task["run_id"],
                "_task_id": task["id"],
                "_workflow_type": payload.get("_workflow_type") or "",
                "_workflow_version_id": payload.get("_workflow_version_id") or "",
            })
            if recover_postgen_cover:
                payload[REEL_COVER_POSTGEN_RETRY_TOKEN] = str(task["input_hash"] or "")
            max_attempts = _bounded_attempts(
                body.get("max_attempts")
                or body.get("maxAttempts")
                or task["max_attempts"],
                default=max(1, int(task["max_attempts"] or 1)),
            )
            thash = task_input_signature(task["task_type"], payload)
            conn.execute(
                """
                update tasks set status='pending', attempt=0, max_attempts=?, input_json=?,
                  input_hash=?, lease_owner=null, lease_expires_at=null, heartbeat_at=null,
                  pid=null, pgid=null, process_start_time=null, process_marker=null,
                  started_at=null, completed_at=null, error=null, updated_at=?
                where id=?
                """,
                (max_attempts, stable_json(payload), thash, now, task_id),
            )
            conn.execute(
                "update task_inputs set value_json = ? where task_id = ? and name = 'payload'",
                (stable_json(payload), task_id),
            )
            reset_ids = [task_id]
            if reset_downstream:
                for child in _downstream_tasks(conn, task_id, task["run_id"]):
                    if child["status"] not in ("cancelled", "failed"):
                        continue
                    if (
                        child["task_type"] == "castle.reel_cover"
                        and str(child["error"] or "").startswith("billing_block:")
                    ):
                        continue
                    child_payload = json_loads(child["input_json"], {})
                    child_hash = child["input_hash"]
                    if isinstance(child_payload, dict):
                        child_hash = task_input_signature(child["task_type"], child_payload)
                    conn.execute(
                        """
                        update tasks set status='pending', attempt=0, input_hash=?,
                          lease_owner=null, lease_expires_at=null, heartbeat_at=null,
                          pid=null, pgid=null, process_start_time=null, process_marker=null,
                          started_at=null, completed_at=null, error=null, updated_at=?
                        where id=?
                        """,
                        (child_hash, now, child["id"]),
                    )
                    reset_ids.append(child["id"])
            conn.execute(
                """
                update workflow_runs set status='pending', completed_at=null, cancelled_at=null,
                  error=null, updated_at=?
                where id=?
                """,
                (now, task["run_id"]),
            )
            add_event(
                conn,
                "task.manual_retry",
                "Manual task retry scheduled",
                run_id=task["run_id"],
                task_id=task_id,
                level="warning",
                data={"reset_task_ids": reset_ids, "max_attempts": max_attempts},
            )
            row = conn.execute("select * from workflow_runs where id = ?", (task["run_id"],)).fetchone()
            return {"ok": True, "run": _run_public(conn, row), "reset_task_ids": reset_ids}


def mark_task_skipped_api(task_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    reason = str(body.get("reason") or "marked skipped by operator")[:1000]
    actor = str(body.get("actor") or "operator")[:120]
    now = utc_ts()
    with connect() as conn:
        with transaction(conn):
            task = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError("Task not found")
            if task["status"] in ("running", "cancel_requested"):
                raise ValueError("Running tasks must be cancelled before mark-skipped")
            if task["status"] == "completed":
                raise ValueError("Completed tasks cannot be marked skipped")
            reset_ids = []
            if task["status"] != "skipped":
                conn.execute(
                    """
                    update tasks set status='skipped', completed_at=?, updated_at=?,
                      lease_owner=null, lease_expires_at=null, heartbeat_at=null,
                      pid=null, pgid=null, process_start_time=null, process_marker=null,
                      error=?
                    where id=?
                    """,
                    (now, now, reason, task_id),
                )
                add_event(
                    conn,
                    "task.skipped",
                    reason,
                    run_id=task["run_id"],
                    task_id=task_id,
                    level="warning",
                    data={"actor": actor, "reason": reason},
                )
            for child in _downstream_tasks(conn, task_id, task["run_id"]):
                error_text = str(child["error"] or "")
                if child["status"] != "cancelled" or not error_text.startswith("Cancelled because an upstream"):
                    continue
                unsatisfied = conn.execute(
                    """
                    select count(*) as count
                    from task_dependencies d
                    join tasks dt on dt.id = d.depends_on_task_id
                    where d.task_id = ?
                      and dt.status not in ('completed','skipped')
                    """,
                    (child["id"],),
                ).fetchone()["count"]
                if unsatisfied:
                    continue
                conn.execute(
                    """
                    update tasks set status='pending', completed_at=null, updated_at=?,
                      lease_owner=null, lease_expires_at=null, heartbeat_at=null,
                      pid=null, pgid=null, process_start_time=null, process_marker=null,
                      started_at=null, error=null
                    where id=?
                    """,
                    (now, child["id"]),
                )
                reset_ids.append(child["id"])
                add_event(
                    conn,
                    "task.unblocked_by_skip",
                    "Downstream task reset after upstream skip",
                    run_id=task["run_id"],
                    task_id=child["id"],
                    level="warning",
                    data={"skipped_task_id": task_id, "actor": actor},
                )
            conn.execute(
                """
                update workflow_runs set status='pending', completed_at=null, cancelled_at=null,
                  error=null, updated_at=?
                where id=? and status in ('failed','cancelled','cancel_requested','running','pending')
                """,
                (now, task["run_id"]),
            )
            _maybe_complete_new_run(conn, task["run_id"])
            row = conn.execute("select * from workflow_runs where id = ?", (task["run_id"],)).fetchone()
            return {"ok": True, "run": _run_public(conn, row), "reset_task_ids": reset_ids}


def status_api() -> dict:
    initialize()
    with connect() as conn:
        run_counts = {
            r["status"]: r["count"]
            for r in conn.execute("select status, count(*) as count from workflow_runs group by status").fetchall()
        }
        task_counts = {
            r["status"]: r["count"]
            for r in conn.execute("select status, count(*) as count from tasks group by status").fetchall()
        }
        active = conn.execute(
            """
            select t.* from tasks t
            join workflow_runs r on r.id = t.run_id
            where t.status in ('pending','running','cancel_requested')
              and r.status in ('pending','running','cancel_requested')
            order by t.priority desc, t.created_at asc
            """
        ).fetchall()
        lane_counts: dict[str, dict[str, int]] = {}
        for row in conn.execute("select lane, status, count(*) as count from tasks group by lane, status").fetchall():
            lane_counts.setdefault(row["lane"], {})[row["status"]] = row["count"]
        settings = settings_from_env()
        scheduler = scheduler_status_api(initialized=True)
        return {
            "ok": True,
            "runs": run_counts,
            "tasks": task_counts,
            "lanes": lane_counts,
            "active": [_task_public(r, conn) for r in active],
            "scheduler": scheduler["scheduler"],
            "gpu_queue": {
                "script": str(settings.gpu_queue_script),
                "script_exists": settings.gpu_queue_script.is_file(),
                "min_free_mb": settings.gpu_min_free_mb,
                "max_util": settings.gpu_max_util,
                "interval": settings.gpu_queue_interval,
            },
        }


def list_tasks_api(limit: int = 50, status: str | None = None, run_id: str | None = None, lane: str | None = None) -> dict:
    initialize()
    limit = max(1, min(int(limit or 50), 200))
    where = []
    params: list = []
    if status:
        where.append("status = ?")
        params.append(status)
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if lane:
        where.append("lane = ?")
        params.append(lane)
    sql = "select * from tasks"
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by created_at desc limit ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return {"ok": True, "tasks": [_task_public(row, conn) for row in rows]}


def get_task_api(task_id: str) -> dict:
    initialize()
    with connect() as conn:
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Task not found")
        task = _task_public(row, conn)
        deps = conn.execute(
            "select depends_on_task_id from task_dependencies where task_id = ? order by depends_on_task_id",
            (task_id,),
        ).fetchall()
        task["depends_on_task_ids"] = [dep["depends_on_task_id"] for dep in deps]
        outputs = conn.execute(
            """
            select o.name, a.* from task_outputs o
            join artifacts a on a.id = o.artifact_id
            where o.task_id = ?
            order by a.created_at desc
            """,
            (task_id,),
        ).fetchall()
        task["artifacts"] = [dict({"output_name": row["name"]}, **artifact_public(row, conn)) for row in outputs]
        return {"ok": True, "task": task}


def scheduler_status_api(initialized: bool = False) -> dict:
    if not initialized:
        initialize()
    settings = settings_from_env()
    info = {"paused": False, "reason": "", "updated_at": None}
    try:
        data = json_loads(settings.scheduler_pause_path.read_text(encoding="utf-8"), {})
        if isinstance(data, dict):
            info.update(data)
        info["paused"] = True
    except OSError:
        pass
    return {"ok": True, "scheduler": info}


def pause_scheduler_api(body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    settings = settings_from_env()
    data = {
        "paused": True,
        "reason": str(body.get("reason") or "manual pause")[:240],
        "updated_at": utc_ts(),
    }
    tmp = settings.scheduler_pause_path.with_name(f".{settings.scheduler_pause_path.name}.tmp")
    tmp.write_text(stable_json(data), encoding="utf-8")
    tmp.replace(settings.scheduler_pause_path)
    with connect() as conn:
        conn.execute(
            "insert into events(run_id, task_id, artifact_id, event_type, level, message, data_json, created_at) values (null, null, null, 'scheduler.paused', 'warning', ?, ?, ?)",
            (data["reason"], stable_json(data), utc_ts()),
        )
        conn.commit()
    return {"ok": True, "scheduler": data}


def resume_scheduler_api() -> dict:
    initialize()
    settings = settings_from_env()
    try:
        settings.scheduler_pause_path.unlink()
    except FileNotFoundError:
        pass
    data = {"paused": False, "reason": "", "updated_at": utc_ts()}
    with connect() as conn:
        conn.execute(
            "insert into events(run_id, task_id, artifact_id, event_type, level, message, data_json, created_at) values (null, null, null, 'scheduler.resumed', 'info', 'Scheduler resumed', ?, ?)",
            (stable_json(data), utc_ts()),
        )
        conn.commit()
    return {"ok": True, "scheduler": data}


def resource_reservations_api() -> dict:
    initialize()
    return {"ok": True, "reservations": reservations_public()}


def reserve_resource_api(resource_id: str, body: dict | None = None) -> dict:
    initialize()
    reservation = reserve_resource(resource_id, body)
    with connect() as conn:
        add_event(
            conn,
            "resource.reserved",
            f"Reserved {resource_id}",
            level="warning",
            data=reservation,
        )
        conn.commit()
    return {"ok": True, "reservation": reservation, "reservations": reservations_public()}


def release_resource_api(resource_id: str, body: dict | None = None) -> dict:
    initialize()
    released, count = release_resource(resource_id, body)
    with connect() as conn:
        add_event(
            conn,
            "resource.released",
            f"Released {resource_id}",
            data={"resource_id": resource_id, "released_count": count, "released": released},
        )
        conn.commit()
    return {"ok": True, "released": released, "released_count": count, "reservations": reservations_public()}


def list_events_api(after: int = 0, limit: int = 100, run_id: str | None = None) -> dict:
    initialize()
    with connect() as conn:
        events = list_events(conn, after=after, limit=limit, run_id=run_id)
        return {"ok": True, "events": events, "cursor": events[-1]["id"] if events else after}


def adopt_artifact_api(body: dict) -> dict:
    initialize()
    source = str(body.get("source_path") or body.get("sourcePath") or "").strip()
    if not source:
        raise ValueError("source_path is required")
    with connect() as conn:
        with transaction(conn):
            artifact = adopt_artifact(
                conn,
                source,
                run_id=str(body.get("run_id") or body.get("runId") or "").strip() or None,
                artifact_type=str(body.get("artifact_type") or body.get("artifactType") or "").strip() or None,
                metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
                tags=body.get("tags") if isinstance(body.get("tags"), list) else [],
                copy=_copy_requested(body),
            )
            return {"ok": True, "artifact": artifact}


def _adopt_item_body(raw, common: dict) -> dict:
    if isinstance(raw, str):
        item = {"source_path": raw}
    elif isinstance(raw, dict):
        item = dict(raw)
    else:
        raise ValueError("adopt batch items must be objects or source path strings")
    metadata = {}
    if isinstance(common.get("metadata"), dict):
        metadata.update(common["metadata"])
    if isinstance(item.get("metadata"), dict):
        metadata.update(item["metadata"])
    tags = []
    if isinstance(common.get("tags"), list):
        tags.extend(common["tags"])
    if isinstance(item.get("tags"), list):
        tags.extend(item["tags"])
    return {
        "source_path": str(item.get("source_path") or item.get("sourcePath") or "").strip(),
        "run_id": str(item.get("run_id") or item.get("runId") or common.get("run_id") or common.get("runId") or "").strip() or None,
        "artifact_type": str(item.get("artifact_type") or item.get("artifactType") or common.get("artifact_type") or common.get("artifactType") or "").strip() or None,
        "metadata": metadata,
        "tags": tags,
        "copy": _copy_requested(item, default=_copy_requested(common)),
        "index": item.get("_batch_index"),
    }


def adopt_artifacts_api(body: dict) -> dict:
    initialize()
    items = body.get("items") or body.get("artifacts") or body.get("source_paths") or body.get("sourcePaths")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    if len(items) > 200:
        raise ValueError("adopt batch accepts at most 200 items")
    artifacts = []
    errors = []
    with connect() as conn:
        for index, raw in enumerate(items):
            item = None
            try:
                item = _adopt_item_body(raw, body)
                if not item["source_path"]:
                    raise ValueError("source_path is required")
                with transaction(conn):
                    artifact = adopt_artifact(
                        conn,
                        item["source_path"],
                        run_id=item["run_id"],
                        artifact_type=item["artifact_type"],
                        metadata=item["metadata"],
                        tags=item["tags"],
                        copy=item["copy"],
                    )
                artifacts.append(artifact)
            except Exception as error:
                error_index = item.get("index") if isinstance(item, dict) and item.get("index") is not None else index
                source = ""
                if isinstance(raw, str):
                    source = raw
                elif isinstance(raw, dict):
                    source = str(raw.get("source_path") or raw.get("sourcePath") or "")
                errors.append({"index": error_index, "source_path": source, "error": str(error)})
    return {
        "ok": True,
        "artifacts": artifacts,
        "errors": errors,
        "count": len(artifacts),
        "error_count": len(errors),
    }


def _parse_artifact_tag_filter(tag: str | None) -> tuple[str | None, str | None]:
    tag = str(tag or "").strip()
    if not tag:
        return None, None
    if ":" in tag:
        family, name = [part.strip() for part in tag.split(":", 1)]
        return family or None, name or None
    return None, tag


def list_artifacts_api(
    limit: int = 50,
    artifact_type: str | None = None,
    tag: str | None = None,
    tag_family: str | None = None,
    tag_name: str | None = None,
    q: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    gate_state: str | None = None,
) -> dict:
    initialize()
    limit = max(1, min(int(limit or 50), 200))
    tag_filter_family, tag_filter_name = _parse_artifact_tag_filter(tag)
    tag_family = str(tag_family or tag_filter_family or "").strip() or None
    tag_name = str(tag_name or tag_filter_name or "").strip() or None

    joins: list[str] = []
    where: list[str] = []
    params: list = []
    if tag_family or tag_name:
        joins.append("join artifact_tags at on at.artifact_id = a.id")
        if tag_family:
            where.append("at.family = ?")
            params.append(tag_family)
        if tag_name:
            where.append("at.name = ?")
            params.append(tag_name)
    if artifact_type:
        where.append("a.artifact_type = ?")
        params.append(artifact_type)
    if run_id:
        where.append("a.run_id = ?")
        params.append(run_id)
    if status:
        where.append("a.status = ?")
        params.append(status)
    if source_type:
        where.append("a.source_type = ?")
        params.append(source_type)
    if gate_state:
        where.append("a.gate_state = ?")
        params.append(gate_state)
    query = str(q or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            """
            (
              a.id like ? or a.artifact_type like ? or a.source_type like ?
              or coalesce(a.relative_path, '') like ? or coalesce(a.source_uri, '') like ?
              or coalesce(a.content_hash, '') like ? or coalesce(a.metadata_json, '') like ?
              or coalesce(a.source_meta_json, '') like ?
              or exists (
                select 1 from artifact_tags qtag
                where qtag.artifact_id = a.id
                  and (qtag.family like ? or qtag.name like ? or (qtag.family || ':' || qtag.name) like ?)
              )
            )
            """
        )
        params.extend([like, like, like, like, like, like, like, like, like, like, like])

    sql = "select distinct a.* from artifacts a " + " ".join(joins)
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by a.created_at desc limit ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return {"ok": True, "artifacts": [artifact_public(row, conn) for row in rows]}


def list_tags_api(limit: int = 200) -> dict:
    initialize()
    limit = max(1, min(int(limit or 200), 500))
    with connect() as conn:
        rows = conn.execute(
            """
            select r.family, r.name, r.description, r.created_at, count(at.artifact_id) as artifact_count
            from tag_registry r
            left join artifact_tags at on at.family = r.family and at.name = r.name
            group by r.family, r.name, r.description, r.created_at
            order by artifact_count desc, r.family asc, r.name asc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return {"ok": True, "tags": [dict(row) for row in rows]}


def update_artifact_tags_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    tags = body.get("tags")
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")
    replace = _truthy(body.get("replace") or body.get("replaceAll"))
    with connect() as conn:
        with transaction(conn):
            artifact = set_artifact_tags(conn, artifact_id, tags, replace=replace)
            return {"ok": True, "artifact": artifact}


def update_artifact_metadata_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    replace = _truthy(body.get("replace") or body.get("replaceAll"))
    with connect() as conn:
        with transaction(conn):
            artifact = update_artifact_metadata(conn, artifact_id, metadata, replace=replace)
            return {"ok": True, "artifact": artifact}


def update_artifact_gate_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    state = str(body.get("gate_state") or body.get("gateState") or body.get("state") or "").strip()
    if not state:
        raise ValueError("gate_state is required")
    with connect() as conn:
        with transaction(conn):
            artifact = set_artifact_gate_state(conn, artifact_id, state)
            return {"ok": True, "artifact": artifact}


def approve_artifact_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    with connect() as conn:
        with transaction(conn):
            artifact = record_artifact_approval(
                conn,
                artifact_id,
                "approved",
                actor=str(body.get("actor") or "operator"),
                note=str(body.get("note") or ""),
                run_id=str(body.get("run_id") or body.get("runId") or "").strip() or None,
            )
            _recompute_reel_status_for_artifact(conn, artifact)
            return {"ok": True, "artifact": artifact}


def reject_artifact_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    with connect() as conn:
        with transaction(conn):
            artifact = record_artifact_approval(
                conn,
                artifact_id,
                "rejected",
                actor=str(body.get("actor") or "operator"),
                note=str(body.get("note") or ""),
                run_id=str(body.get("run_id") or body.get("runId") or "").strip() or None,
            )
            _recompute_reel_status_for_artifact(conn, artifact)
            return {"ok": True, "artifact": artifact}


def review_artifact_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    decision = str(body.get("decision") or "needs_work").strip()
    with connect() as conn:
        with transaction(conn):
            artifact = record_artifact_approval(
                conn,
                artifact_id,
                decision,
                actor=str(body.get("actor") or "operator"),
                note=str(body.get("note") or ""),
                run_id=str(body.get("run_id") or body.get("runId") or "").strip() or None,
            )
            _recompute_reel_status_for_artifact(conn, artifact)
            return {"ok": True, "artifact": artifact}


def get_artifact_api(artifact_id: str) -> dict:
    initialize()
    with connect() as conn:
        row = conn.execute("select * from artifacts where id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError("Artifact not found")
        return {"ok": True, "artifact": artifact_public(row, conn)}


def make_artifact_proxy_api(artifact_id: str, body: dict | None = None) -> dict:
    initialize()
    body = body if isinstance(body, dict) else {}
    with connect() as conn:
        row = conn.execute("select * from artifacts where id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError("Artifact not found")
        path = artifact_abs_path(conn, row)
        if path is None or not path.is_file():
            raise FileNotFoundError("Artifact file not found")
        artifact = artifact_public(row, conn)
    output_name = str(body.get("output_name") or body.get("outputName") or "").strip()
    if not output_name:
        output_name = f"{path.stem}_proxy.mp4" if path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"} else f"{path.stem}_proxy{path.suffix or '.bin'}"
    request = {
        "workflow_type": "artifact.make_proxy",
        "title": str(body.get("title") or f"Make proxy for {artifact_id}")[:160],
        "priority": int(body.get("priority") or 0),
        "input": {
            "source_path": str(path),
            "source_artifact_id": artifact_id,
            "output_name": output_name,
        },
    }
    for key in ("vf", "crf", "preset", "audio_bitrate"):
        if key in body:
            request["input"][key] = body[key]
    idem = str(body.get("idempotency_key") or body.get("idempotencyKey") or "").strip()
    if idem:
        request["idempotency_key"] = idem
    result = create_run_api(request)
    result["artifact"] = artifact
    return result


def artifact_file_path_api(artifact_id: str) -> Path:
    initialize()
    with connect() as conn:
        row = conn.execute("select * from artifacts where id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError("Artifact not found")
        path = artifact_abs_path(conn, row)
        if path is None or not path.is_file():
            raise FileNotFoundError("Artifact file not found")
        return path


def task_log_path_api(task_id: str) -> Path:
    initialize()
    settings = settings_from_env()
    with connect() as conn:
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Task not found")
        work_dir = Path(row["work_dir"] or settings.work_root / task_id)
        path = (work_dir / "logs" / "stdout.log").resolve()
        if not path.is_file():
            raise FileNotFoundError("Task log not found")
        return path
