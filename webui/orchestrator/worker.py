from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from .config import (
    WEBUI_ROOT,
    Settings,
    atomic_write_private_text,
    ensure_dirs,
    ensure_private_dir,
    open_private_text_append,
    settings_from_env,
)
from .db import connect, initialize, json_loads, transaction, utc_ts
from .events import add_event
from .resources import blocked_lanes, release_resource
from .review import ensure_reel_review, review_reconciliation_fingerprint
from .review_sync_trigger import reel_review_sync_unit, trigger_reel_review_sync
from .store import (
    artifact_abs_path,
    clone_reused_artifact,
    commit_task_outputs,
    reusable_task_artifacts,
    set_artifact_gate_state,
    sha256_file,
)
from .tasks import (
    _path_is_allowed,
    _task_allowed_roots,
    reel_script_provider_outage,
    reel_synth_sentence_hard_fail,
    reel_synth_sentence_hard_fail_data,
    task_input_signature,
)


_WORKER = None
_WORKER_LOCK = threading.Lock()
CANCEL_KILL_GRACE_SECONDS = 5.0
HEARTBEAT_INTERVAL_SECONDS = 5.0
REVIEW_RECONCILE_INTERVAL_SECONDS = 30.0
SYSTEMD_SCOPE_UNIT_PREFIX = "castle-orch-task-"


def process_identity(pid: int) -> tuple[str, str]:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        _before, _sep, after = stat.rpartition(")")
        fields = after.strip().split()
        start = fields[19]
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
        return start, cmdline
    except Exception:
        return "", ""


def process_matches(pid: int, start_time: str | None, marker: str | None) -> bool:
    if not pid:
        return False
    start, cmdline = process_identity(pid)
    if not start:
        return False
    if start_time and str(start_time) != start:
        return False
    if marker and marker not in cmdline:
        return False
    return True


def safe_killpg(pgid: int, sig: int, start_time: str | None = None, marker: str | None = None) -> bool:
    if pgid <= 1 or pgid == os.getpgrp():
        return False
    if (start_time or marker) and not process_matches(pgid, start_time, marker):
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except OSError:
        return False


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _json_pointer_value(value, pointer: str):
    current = value
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ValueError("JSON pointer does not resolve")
    if current is None or isinstance(current, float) and not (-1e308 < current < 1e308):
        raise ValueError("JSON pointer resolved to an unsupported value")
    return current


def _parse_progress_line(line: str) -> tuple[str, str, dict] | None:
    text = line.rstrip()
    if text.startswith("CASTLE_PROGRESS "):
        try:
            data = json.loads(text.split(" ", 1)[1])
        except json.JSONDecodeError:
            return None
        label = str(data.get("label") or data.get("stage") or "progress")
        return "task.progress", label, data
    if text.startswith("[gpu-queue]"):
        return "task.gpu_queue", text[:240], {"line": text[:1000]}
    return None


def _systemd_scope_unit_name(task_id: str) -> str:
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "", str(task_id))
    return f"{SYSTEMD_SCOPE_UNIT_PREFIX}{safe_task_id or 'task'}.scope"


def request_run_cancel(run_id: str) -> int:
    initialize()
    now = utc_ts()
    changed = 0
    with connect() as conn:
        with transaction(conn):
            run = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError("Run not found")
            if run["status"] in ("completed", "failed", "cancelled"):
                return 0
            conn.execute(
                "update workflow_runs set status='cancel_requested', updated_at=?, cancelled_at=? where id=?",
                (now, now, run_id),
            )
            for task in conn.execute(
                "select * from tasks where run_id=? and status in ('pending','running')",
                (run_id,),
            ).fetchall():
                if task["status"] == "pending":
                    conn.execute(
                        "update tasks set status='cancelled', updated_at=?, completed_at=? where id=?",
                        (now, now, task["id"]),
                    )
                    add_event(conn, "task.cancelled", "Cancelled pending task", run_id=run_id, task_id=task["id"])
                else:
                    conn.execute(
                        "update tasks set status='cancel_requested', updated_at=? where id=?",
                        (now, task["id"]),
                    )
                    work_dir = Path(task["work_dir"] or "")
                    if work_dir:
                        try:
                            (work_dir / "cancel").touch()
                        except OSError:
                            pass
                    pid = int(task["pgid"] or task["pid"] or 0)
                    safe_killpg(pid, signal.SIGTERM, task["process_start_time"], task["process_marker"])
                    add_event(conn, "task.cancel_requested", "Cancel requested", run_id=run_id, task_id=task["id"])
                changed += 1
            add_event(conn, "run.cancel_requested", "Run cancellation requested", run_id=run_id)
            active = conn.execute(
                "select count(*) from tasks where run_id=? and status in ('running','cancel_requested')",
                (run_id,),
            ).fetchone()[0]
            if not active:
                conn.execute(
                    "update workflow_runs set status='cancelled', completed_at=?, updated_at=? where id=?",
                    (now, now, run_id),
                )
                add_event(conn, "run.cancelled", "Run cancelled", run_id=run_id, level="warning")
    return changed


def _cancel_pending_descendants_for_task(conn: sqlite3.Connection, task: sqlite3.Row | dict, error: str) -> int:
    now = utc_ts()
    descendants = conn.execute(
        """
        with recursive downstream(task_id) as (
          select task_id from task_dependencies where depends_on_task_id = ?
          union
          select d.task_id from task_dependencies d
          join downstream x on x.task_id = d.depends_on_task_id
        )
        select t.* from tasks t
        join downstream d on d.task_id = t.id
        where t.run_id = ? and t.status = 'pending'
        """,
        (task["id"], task["run_id"]),
    ).fetchall()
    changed = 0
    for child in descendants:
        conn.execute(
            "update tasks set status='cancelled', completed_at=?, updated_at=?, error=? where id=?",
            (now, now, error[:1000], child["id"]),
        )
        add_event(
            conn,
            "task.cancelled",
            error,
            run_id=task["run_id"],
            task_id=child["id"],
            level="warning",
            data={"upstream_task_id": task["id"]},
        )
        changed += 1
    return changed


def _maybe_finish_run_from_counts(conn: sqlite3.Connection, run_id: str) -> None:
    run = conn.execute(
        "select status from workflow_runs where id=?", (run_id,),
    ).fetchone()
    if run is None:
        return
    # A late sibling may finish after a run-level fail-closed policy has
    # already stopped the graph.  Never let task counts turn that terminal
    # decision into completed/cancelled (or emit a contradictory event).
    if run["status"] in {"completed", "failed", "cancelled", "needs_review"}:
        return
    rows = conn.execute("select status, count(*) as count from tasks where run_id=? group by status", (run_id,)).fetchall()
    counts = {r["status"]: r["count"] for r in rows}
    now = utc_ts()
    if counts.get("running") or counts.get("pending") or counts.get("cancel_requested"):
        return
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
        conn.execute("update workflow_runs set status='completed', completed_at=?, updated_at=? where id=?", (now, now, run_id))
        add_event(conn, "run.completed", "Run completed", run_id=run_id)


def request_task_cancel(task_id: str) -> int:
    initialize()
    now = utc_ts()
    changed = 0
    with connect() as conn:
        with transaction(conn):
            task = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError("Task not found")
            run = conn.execute("select * from workflow_runs where id = ?", (task["run_id"],)).fetchone()
            if run is None:
                raise KeyError("Run not found")
            if task["status"] in ("completed", "failed", "cancelled"):
                return 0
            if run["status"] in ("completed", "failed", "cancelled"):
                return 0
            if task["status"] == "pending":
                conn.execute(
                    "update tasks set status='cancelled', completed_at=?, updated_at=?, error=? where id=?",
                    (now, now, "Cancelled by task request", task_id),
                )
                add_event(conn, "task.cancelled", "Cancelled pending task", run_id=task["run_id"], task_id=task_id, level="warning")
                changed += 1
            elif task["status"] == "running":
                conn.execute(
                    "update tasks set status='cancel_requested', updated_at=? where id=?",
                    (now, task_id),
                )
                work_dir = Path(task["work_dir"] or "")
                if work_dir:
                    try:
                        (work_dir / "cancel").touch()
                    except OSError:
                        pass
                pid = int(task["pgid"] or task["pid"] or 0)
                safe_killpg(pid, signal.SIGTERM, task["process_start_time"], task["process_marker"])
                add_event(conn, "task.cancel_requested", "Task cancellation requested", run_id=task["run_id"], task_id=task_id, level="warning")
                changed += 1
            elif task["status"] == "cancel_requested":
                changed += 0
            else:
                raise ValueError(f"Cannot cancel task in status {task['status']}")
            changed += _cancel_pending_descendants_for_task(conn, task, "Cancelled because an upstream task was cancelled")
            _maybe_finish_run_from_counts(conn, task["run_id"])
    return changed


class OrchestratorWorker:
    def __init__(self, settings: Settings | None = None):
        self.settings = ensure_dirs(settings or settings_from_env())
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.worker_id = f"castle-web-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.lock_file = None
        self._systemd_run_scope_available_cache: bool | None = None
        self._review_check_after = 0.0
        self._review_fingerprints: dict[str, str] = {}

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        initialize(self.settings)
        self.thread = threading.Thread(target=self.run, name="castle-orchestrator-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        self.lock_file = self.settings.lock_path.open("w")
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Castle orchestrator worker not started: lock is held", flush=True)
            self.lock_file.close()
            return
        try:
            print(f"Castle orchestrator worker started ({self.worker_id})", flush=True)
            recovery_required = True
            while not self.stop_event.is_set():
                try:
                    if recovery_required:
                        self.recover_incomplete()
                        recovery_required = False
                    task = self.claim_next_task()
                    if task is None:
                        self._ensure_completed_reel_reviews()
                        self.stop_event.wait(self.settings.worker_interval)
                        continue
                    self.execute_task(task)
                except Exception as error:
                    recovery_required = True
                    print(
                        "Castle orchestrator worker iteration failed; "
                        f"will recover and continue: {error}",
                        flush=True,
                    )
                    traceback.print_exc()
                    self.stop_event.wait(max(0.2, self.settings.worker_interval))
        finally:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.lock_file.close()

    def recover_incomplete(self) -> None:
        self._adopt_started_files()
        with connect(self.settings) as conn:
            rows = conn.execute(
                "select * from tasks where status in ('running','cancel_requested')"
            ).fetchall()
        for task in rows:
            self._recover_task(dict(task))

    def _ensure_completed_reel_reviews(self) -> None:
        """Finalize review outside scheduler transactions after all-reuse runs."""
        if time.monotonic() < self._review_check_after:
            return
        # Normal task completion prepares review immediately. This is only the
        # recovery/all-reuse backstop, not work to repeat on every idle poll.
        self._review_check_after = time.monotonic() + REVIEW_RECONCILE_INTERVAL_SECONDS
        with connect(self.settings) as conn:
            run_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    select id from workflow_runs
                    where workflow_type='ai_sister.reel_pipeline' and status='completed'
                    order by updated_at asc
                    """
                ).fetchall()
            ]
        self._review_fingerprints = {
            key: value for key, value in self._review_fingerprints.items() if key in run_ids
        }
        for run_id in run_ids:
            if self.stop_event.is_set():
                break
            try:
                with connect(self.settings) as conn:
                    fingerprint = review_reconciliation_fingerprint(conn, run_id, self.settings)
                    if self._review_fingerprints.get(run_id) == fingerprint:
                        continue
                    review_result = ensure_reel_review(conn, run_id, self.settings)
                outcome = self._maybe_trigger_reel_review_sync(run_id, review_result)
                if outcome.get("status") not in {"failed", "invalid"}:
                    # Cache the inputs seen BEFORE rendering: concurrent input
                    # changes must still be picked up on the next pass.
                    self._review_fingerprints[run_id] = fingerprint
            except Exception as error:
                with connect(self.settings) as conn:
                    with transaction(conn):
                        add_event(
                            conn,
                            "run.review_prepare_failed",
                            str(error)[:1000],
                            run_id=run_id,
                            level="error",
                        )

    def _maybe_trigger_reel_review_sync(
        self, run_id: str, review_result: object,
    ) -> dict:
        """Dispatch optional transport after DB commit; never affect the run."""
        expected_unit = reel_review_sync_unit(run_id, review_result)
        with connect(self.settings) as conn:
            row = conn.execute(
                "select input_json from workflow_runs where id = ?", (run_id,),
            ).fetchone()
        run_input = json_loads(row["input_json"], {}) if row is not None else {}
        if (
            isinstance(run_input, dict)
            and run_input.get("release_mode") == "review_only"
        ):
            return {"status": "disabled", "reason": "run_review_only"}
        if expected_unit is not None:
            with connect(self.settings) as conn:
                prior = conn.execute(
                    """
                    select data_json from events
                    where run_id = ? and event_type = 'run.review_sync_triggered'
                    order by id desc
                    """,
                    (run_id,),
                ).fetchall()
            if any(
                json_loads(event["data_json"], {}).get("unit") == expected_unit
                for event in prior
            ):
                return {
                    "status": "already_triggered",
                    "reason": "transport_generation_already_queued",
                    "unit": expected_unit,
                }
        try:
            outcome = trigger_reel_review_sync(run_id, review_result)
        except Exception:
            outcome = {"status": "failed", "reason": "trigger_error"}
        if outcome.get("status") not in {"queued", "failed", "invalid"}:
            return outcome
        try:
            with connect(self.settings) as conn:
                with transaction(conn):
                    queued = outcome.get("status") == "queued"
                    add_event(
                        conn,
                        "run.review_sync_triggered" if queued
                        else "run.review_sync_trigger_failed",
                        "Queued Reel review transport" if queued
                        else "Could not queue Reel review transport",
                        run_id=run_id,
                        level="info" if queued else "warning",
                        data={
                            "status": str(outcome.get("status") or ""),
                            "reason": str(outcome.get("reason") or "")[:80],
                            "unit": str(outcome.get("unit") or "")[:160],
                        },
                    )
        except Exception:
            # Transport observability is best-effort and cannot alter a Reel's
            # already committed workflow/review result.
            pass
        return outcome

    def _adopt_started_files(self) -> None:
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                select id from tasks
                where status in ('running','cancel_requested') and pid is null
                """
            ).fetchall()

        candidates: list[tuple[str, Path, dict]] = []
        for row in rows:
            task_id = str(row["id"] or "")
            if not task_id.startswith("task_") or Path(task_id).name != task_id:
                continue
            started_path = self.settings.work_root / task_id / "started.json"
            started = _read_json(started_path)
            if not started or str(started.get("task_id") or "") != task_id:
                continue
            candidates.append((task_id, started_path, started))

        if not candidates:
            return
        with connect(self.settings) as conn:
            with transaction(conn):
                for task_id, started_path, started in candidates:
                    conn.execute(
                        """
                        update tasks set pid=?, pgid=?, process_start_time=?, process_marker=?,
                          work_dir=coalesce(work_dir, ?), updated_at=?
                        where id=? and status in ('running','cancel_requested') and pid is null
                        """,
                        (
                            started.get("pid"),
                            started.get("pgid"),
                            started.get("process_start_time"),
                            started.get("marker"),
                            str(started_path.parent),
                            utc_ts(),
                            task_id,
                        ),
                    )

    def _recover_task(self, task: dict) -> None:
        work_dir = Path(task.get("work_dir") or self.settings.work_root / task["id"])
        sentinel = work_dir / "sentinel.json"
        data = _read_json(sentinel)
        if data:
            try:
                self.finish_task(task, data, recovered=True)
            except Exception as error:
                self.mark_task_terminal(task, "failed", f"Recovery failed: {error}", recovered=True)
            return
        pid = int(task.get("pid") or 0)
        if pid and process_matches(pid, task.get("process_start_time"), task.get("process_marker")):
            with connect(self.settings) as conn:
                with transaction(conn):
                    add_event(
                        conn,
                        "task.recovered_running",
                        "Recovered still-running task",
                        run_id=task["run_id"],
                        task_id=task["id"],
                    )
            threading.Thread(
                target=self.monitor_orphan,
                args=(task,),
                name=f"castle-orphan-monitor-{task['id']}",
                daemon=True,
            ).start()
            return
        self._release_recovered_reservation(task, work_dir)
        self.mark_task_terminal(task, "cancelled" if task.get("status") == "cancel_requested" else "failed", "Task died without completion sentinel")

    def monitor_orphan(self, task: dict) -> None:
        # The restarted web process is no longer the parent, so do not waitpid.
        # Poll the sentinel and process identity.
        deadline_sleep = self.settings.worker_interval
        work_dir = Path(task.get("work_dir") or self.settings.work_root / task["id"])
        log_path = work_dir / "logs" / "stdout.log"
        try:
            log_offset = log_path.stat().st_size
        except OSError:
            log_offset = 0
        adopted_pid = int(task.get("pid") or 0)
        adopted_start_time = task.get("process_start_time")
        adopted_marker = task.get("process_marker")
        threading.Thread(
            target=self._tail_task_log,
            args=(
                log_path,
                log_offset,
                lambda: not process_matches(adopted_pid, adopted_start_time, adopted_marker),
                task,
            ),
            name=f"castle-orphan-tail-{task['id']}",
            daemon=True,
        ).start()
        cancel_sent_at = None
        while not self.stop_event.is_set():
            with connect(self.settings) as conn:
                row = conn.execute("select * from tasks where id = ?", (task["id"],)).fetchone()
                if row is None or row["status"] not in ("running", "cancel_requested"):
                    return
                task = dict(row)
            work_dir = Path(task.get("work_dir") or self.settings.work_root / task["id"])
            data = _read_json(work_dir / "sentinel.json")
            if data:
                try:
                    self.finish_task(task, data, recovered=True)
                except Exception as error:
                    self.mark_task_terminal(task, "failed", f"Recovery failed: {error}", recovered=True)
                return
            pid = int(task.get("pid") or 0)
            if not process_matches(pid, task.get("process_start_time"), task.get("process_marker")):
                self._release_recovered_reservation(task, work_dir)
                self.mark_task_terminal(task, "cancelled" if task.get("status") == "cancel_requested" else "failed", "Recovered task exited without sentinel")
                return
            self._heartbeat(task["id"])
            if task.get("status") == "cancel_requested":
                try:
                    (work_dir / "cancel").touch()
                except OSError:
                    pass
                now_ts = utc_ts()
                if cancel_sent_at is None:
                    cancel_sent_at = now_ts
                sig = signal.SIGKILL if now_ts - cancel_sent_at > CANCEL_KILL_GRACE_SECONDS else signal.SIGTERM
                safe_killpg(int(task.get("pgid") or pid), sig, task.get("process_start_time"), task.get("process_marker"))
            time.sleep(deadline_sleep)

    def claim_next_task(self) -> dict | None:
        with connect(self.settings) as conn:
            with transaction(conn):
                self._finalize_cancelled_runs(conn)
                if self.settings.scheduler_pause_path.exists():
                    return None
                blocked = sorted(blocked_lanes(self.settings))
                lane_filter = ""
                params: list[str] = []
                if blocked:
                    lane_filter = f"and t.lane not in ({','.join('?' for _ in blocked)})"
                    params.extend(blocked)
                while True:
                    row = conn.execute(
                        f"""
                        select t.* from tasks t
                        join workflow_runs r on r.id = t.run_id
                        where t.status = 'pending'
                          and t.lane in ('cpu', 'gpu')
                          {lane_filter}
                          and r.status in ('pending','running')
                          and not exists (
                            select 1 from task_dependencies d
                            join tasks dt on dt.id = d.depends_on_task_id
                            where d.task_id = t.id and dt.status not in ('completed','skipped')
                          )
                        order by t.priority desc, t.created_at asc
                        limit 1
                        """,
                        params,
                    ).fetchone()
                    if row is None:
                        self._finalize_cancelled_runs(conn)
                        return None
                    task = dict(row)
                    try:
                        task, reused = self._materialize_task_bindings(conn, task)
                    except Exception as error:
                        now = utc_ts()
                        message = str(error)[:1000]
                        conn.execute(
                            """
                            update tasks set status='failed', completed_at=?, updated_at=?, error=?
                            where id=? and status='pending'
                            """,
                            (now, now, message, task["id"]),
                        )
                        add_event(
                            conn,
                            "task.binding_failed",
                            message,
                            run_id=task["run_id"],
                            task_id=task["id"],
                            level="error",
                        )
                        self._cancel_pending_descendants(conn, task, message)
                        self._maybe_finish_run(conn, task["run_id"])
                        continue
                    if reused:
                        continue
                    break
                now = utc_ts()
                work_dir = self.settings.work_root / task["id"]
                marker = f"castle-orch-task-{task['id']}"
                conn.execute(
                    """
                    update tasks set status='running', lease_owner=?, lease_expires_at=?,
                      heartbeat_at=?, process_marker=?, work_dir=?, log_uri=?,
                      started_at=coalesce(started_at, ?), updated_at=?
                    where id=?
                    """,
                    (
                        self.worker_id,
                        now + 120,
                        now,
                        marker,
                        str(work_dir),
                        f"task-log:{task['id']}",
                        now,
                        now,
                        task["id"],
                    ),
                )
                conn.execute(
                    """
                    update workflow_runs set status='running', started_at=coalesce(started_at, ?), updated_at=?
                    where id=? and status='pending'
                    """,
                    (now, now, task["run_id"]),
                )
                add_event(
                    conn,
                    "task.claimed",
                    f"{task['lane']} task claimed by local worker",
                    run_id=task["run_id"],
                    task_id=task["id"],
                    data={
                        "lane": task["lane"],
                        "task_type": task["task_type"],
                        "attempt": int(task.get("attempt") or 0),
                        "max_attempts": int(task.get("max_attempts") or 1),
                    },
                )
                row = conn.execute("select * from tasks where id = ?", (task["id"],)).fetchone()
                return dict(row)

    def _materialize_task_bindings(
        self,
        conn: sqlite3.Connection,
        task: dict,
    ) -> tuple[dict, bool]:
        """Bind named upstream outputs and decide reuse at the runnable edge."""
        payload = json_loads(task.get("input_json"), {})
        bindings = payload.get("_input_bindings")
        if not isinstance(bindings, dict) or not bindings:
            return task, False
        runtime_payload = dict(payload)
        for field, binding in sorted(bindings.items()):
            if not isinstance(binding, dict):
                raise ValueError(f"binding {field} is not an object")
            producer_key = str(binding.get("task") or "")
            output_name = str(binding.get("output") or "")
            selector = str(binding.get("select") or "artifact_ref")
            rows = conn.execute(
                """
                select a.*, o.name as output_name, t.id as source_task_id
                from tasks t
                join task_outputs o on o.task_id = t.id
                join artifacts a on a.id = o.artifact_id
                where t.run_id = ? and t.key = ? and t.status = 'completed'
                  and o.name = ? and a.status = 'available'
                order by a.created_at desc
                """,
                (task["run_id"], producer_key, output_name),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    f"binding {field} requires exactly one output "
                    f"{producer_key}.{output_name}; found {len(rows)}")
            artifact = rows[0]
            content_hash = str(artifact["content_hash"] or "")
            if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
                raise ValueError(f"binding {field} output has no valid content hash")
            path = artifact_abs_path(conn, artifact)
            if path is not None:
                if not path.is_file() or sha256_file(path) != content_hash:
                    raise ValueError(f"binding {field} artifact bytes do not match content hash")
            if selector == "artifact_ref":
                if path is None:
                    raise ValueError(f"binding {field} artifact_ref requires a file artifact")
                value = {
                    "kind": "artifact",
                    "artifact_id": artifact["id"],
                    "content_hash": content_hash,
                }
            elif selector == "artifact_id":
                value = artifact["id"]
            elif selector == "content_hash":
                value = content_hash
            elif selector == "json_value":
                if path is None or not path.is_file() or path.stat().st_size > 1024 * 1024:
                    raise ValueError(f"binding {field} json_value requires a small file artifact")
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    value = _json_pointer_value(document, str(binding.get("pointer") or ""))
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                    raise ValueError(f"binding {field} cannot resolve JSON pointer") from error
            elif selector == "path_ref":
                source_path = Path(str(binding.get("path") or "")).expanduser().resolve()
                if not source_path.is_file() or not _path_is_allowed(source_path, _task_allowed_roots()):
                    raise ValueError(f"binding {field} path_ref is not an allowed file")
                if sha256_file(source_path) != content_hash:
                    raise ValueError(f"binding {field} path_ref bytes do not match upstream output")
                value = {
                    "kind": "path",
                    "path": str(source_path),
                    "content_hash": content_hash,
                }
            else:
                raise ValueError(f"binding {field} has unsupported selector")
            runtime_payload[field] = value
            conn.execute(
                """
                insert into task_inputs(task_id, name, artifact_id, value_json)
                values (?, ?, ?, ?)
                on conflict(task_id, name) do update set
                  artifact_id=excluded.artifact_id,
                  value_json=excluded.value_json
                """,
                (task["id"], field, artifact["id"], json.dumps(value, ensure_ascii=False, sort_keys=True)),
            )
        semantic_hash = task_input_signature(task["task_type"], runtime_payload)
        conn.execute(
            "update tasks set input_json=?, input_hash=?, updated_at=? where id=?",
            (json.dumps(runtime_payload, ensure_ascii=False, sort_keys=True), semantic_hash, utc_ts(), task["id"]),
        )
        conn.execute(
            "update task_inputs set value_json=? where task_id=? and name='payload'",
            (json.dumps(runtime_payload, ensure_ascii=False, sort_keys=True), task["id"]),
        )
        task = dict(conn.execute("select * from tasks where id=?", (task["id"],)).fetchone())
        if task.get("reuse_policy") != "input_hash":
            return task, False
        reusable = reusable_task_artifacts(conn, task["task_type"], semantic_hash)
        if not reusable:
            return task, False
        cloned = [
            clone_reused_artifact(
                conn,
                row,
                run_id=task["run_id"],
                task_id=task["id"],
                input_hash=semantic_hash,
                output_name=str(row["output_name"] or "reused"),
            )
            for row in reusable
        ]
        now = utc_ts()
        reused_from_task_id = str(
            reusable[0]["source_task_id"] or reusable[0]["producing_task_id"] or "")
        conn.execute(
            """
            update tasks set status='completed', completed_at=?, updated_at=?,
              reused_from_task_id=?, error=null
            where id=?
            """,
            (now, now, reused_from_task_id or None, task["id"]),
        )
        add_event(
            conn,
            "task.reused",
            "Task reused completed artifacts after binding inputs",
            run_id=task["run_id"],
            task_id=task["id"],
            data={
                "reused_from_task_id": reused_from_task_id,
                "artifacts": [artifact["id"] for artifact in cloned],
                "input_hash": semantic_hash,
                "bound_inputs": sorted(bindings),
            },
        )
        self._apply_post_adoption_gate_states(conn, task, cloned)
        self._maybe_finish_run(conn, task["run_id"])
        return task, True

    def execute_task(self, task: dict) -> None:
        work_root = Path(os.path.abspath(self.settings.work_root))
        expected_work_dir = work_root / str(task["id"])
        work_dir = Path(os.path.abspath(task["work_dir"]))
        if work_dir != expected_work_dir:
            raise ValueError("task work_dir must be the task's private orchestrator directory")
        ensure_private_dir(work_dir)
        ensure_private_dir(work_dir / "logs")
        ensure_private_dir(work_dir / "tmp")
        input_path = work_dir / "input.json"
        atomic_write_private_text(input_path, task["input_json"])
        sentinel = work_dir / "sentinel.json"
        if sentinel.exists():
            sentinel.unlink()
        log_path = work_dir / "logs" / "stdout.log"
        cmd = [
            self.settings.python,
            "-m",
            "orchestrator.task_wrapper",
            "--task-id",
            task["id"],
            "--task-type",
            task["task_type"],
            "--attempt",
            str(int(task.get("attempt") or 0)),
            "--input-json",
            str(input_path),
            "--work-dir",
            str(work_dir),
            "--marker",
            str(task["process_marker"]),
            "--sentinel",
            str(sentinel),
        ]
        spawn_cmd = self._task_spawn_command(task["id"], cmd)
        proc = None
        reader = None
        abandon_for_recovery = False
        cancel_sent_at = None
        last_heartbeat_at = 0.0
        try:
            log_offset = log_path.stat().st_size if log_path.exists() else 0
            log_handle = open_private_text_append(
                log_path,
                encoding="utf-8",
                buffering=1,
            )
            try:
                proc = subprocess.Popen(
                    spawn_cmd,
                    cwd=str(WEBUI_ROOT),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log_handle.close()
            reader = threading.Thread(
                target=self._tail_task_log,
                args=(log_path, log_offset, lambda: proc.poll() is not None, task),
                name=f"castle-task-tail-{task['id']}",
                daemon=True,
            )
            reader.start()
            start_time, _cmdline = process_identity(proc.pid)
            now = utc_ts()
            with connect(self.settings) as conn:
                with transaction(conn):
                    conn.execute(
                        """
                        update tasks set pid=?, pgid=?, process_start_time=?, heartbeat_at=?,
                          lease_expires_at=?, updated_at=?
                        where id=?
                        """,
                        (proc.pid, proc.pid, start_time, now, now + 120, now, task["id"]),
                    )
                    add_event(conn, "task.started", "Task process started", run_id=task["run_id"], task_id=task["id"], data={"pid": proc.pid})
            while proc.poll() is None:
                if self.stop_event.is_set():
                    abandon_for_recovery = True
                    with connect(self.settings) as conn:
                        with transaction(conn):
                            add_event(
                                conn,
                                "task.detached",
                                "Worker stopped while detached task process continued",
                                run_id=task["run_id"],
                                task_id=task["id"],
                                level="warning",
                                data={"pid": proc.pid},
                            )
                    threading.Thread(
                        target=self._reap_detached_process,
                        args=(proc,),
                        name=f"castle-detached-reaper-{task['id']}",
                        daemon=True,
                    ).start()
                    return
                now_ts = utc_ts()
                try:
                    if self._task_wants_cancel(task["id"]):
                        if cancel_sent_at is None:
                            cancel_sent_at = now_ts
                            try:
                                (work_dir / "cancel").touch()
                            except OSError:
                                pass
                        sig = signal.SIGKILL if now_ts - cancel_sent_at > CANCEL_KILL_GRACE_SECONDS else signal.SIGTERM
                        safe_killpg(proc.pid, sig, start_time, str(task["process_marker"]))
                    if now_ts - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                        self._heartbeat(task["id"])
                        last_heartbeat_at = now_ts
                except sqlite3.Error as error:
                    # A temporary control-plane outage must not terminate a
                    # healthy detached task.  Recovery can commit its sentinel
                    # after the database becomes available again.
                    print(
                        f"Castle orchestrator task control unavailable for {task['id']}: {error}",
                        flush=True,
                    )
                    self.stop_event.wait(min(1.0, max(0.2, self.settings.worker_interval)))
                    continue
                time.sleep(0.5)
            if reader is not None:
                reader.join(timeout=5)
            data = _read_json(sentinel)
            if not data:
                latest = self._task_row(task["id"]) or task
                self._release_recovered_reservation(dict(latest), work_dir)
                if self._task_status(task["id"]) == "cancel_requested":
                    data = {"status": "cancelled", "outputs": [], "error": "cancelled without sentinel"}
                else:
                    data = {"status": "failed", "outputs": [], "error": f"missing completion sentinel, exit={proc.returncode}"}
            latest = self._task_row(task["id"]) or task
            self.finish_task(dict(latest), data)
        except Exception as error:
            latest = self._task_row(task["id"]) or task
            self.mark_task_terminal(dict(latest), "failed", str(error))
        finally:
            if proc is not None and proc.poll() is None and not abandon_for_recovery:
                safe_killpg(proc.pid, signal.SIGTERM)
            if reader is not None:
                reader.join(timeout=1)

    def _task_spawn_command(self, task_id: str, cmd: list[str]) -> list[str]:
        if not self._systemd_run_scope_available():
            return cmd
        return [
            "systemd-run",
            "--user",
            "--scope",
            "--collect",
            "--quiet",
            "--unit",
            _systemd_scope_unit_name(task_id),
            "--",
            *cmd,
        ]

    def _systemd_run_scope_available(self) -> bool:
        if self._systemd_run_scope_available_cache is not None:
            return self._systemd_run_scope_available_cache
        if shutil.which("systemd-run") is None:
            print("Castle orchestrator worker: systemd-run scope unavailable; using plain detached task spawn", flush=True)
            self._systemd_run_scope_available_cache = False
            return False
        try:
            probe = subprocess.run(
                ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", "/bin/true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            available = probe.returncode == 0
        except Exception:
            available = False
        if not available:
            print("Castle orchestrator worker: systemd-run scope probe failed; using plain detached task spawn", flush=True)
        self._systemd_run_scope_available_cache = available
        return available

    def _capture_output(self, stream, log_path: Path, task: dict) -> None:
        if stream is None:
            return
        try:
            with open_private_text_append(
                log_path,
                encoding="utf-8",
                errors="replace",
            ) as log:
                for line in stream:
                    log.write(line)
                    log.flush()
                    parsed = _parse_progress_line(line)
                    if parsed:
                        event_type, message, data = parsed
                        self._record_task_event(task["id"], task["run_id"], event_type, message, data)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _reap_detached_process(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait()
        except Exception:
            pass

    def _tail_task_log(self, log_path: Path, offset: int, done, task: dict) -> None:
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as log:
                log.seek(offset)
                while True:
                    pos = log.tell()
                    line = log.readline()
                    if line:
                        if not line.endswith("\n"):
                            log.seek(pos)
                            line = ""
                        else:
                            parsed = _parse_progress_line(line)
                            if parsed:
                                event_type, message, data = parsed
                                self._record_task_event(task["id"], task["run_id"], event_type, message, data)
                            continue
                    if done():
                        remainder = log.read()
                        for tail_line in remainder.splitlines(True):
                            parsed = _parse_progress_line(tail_line)
                            if parsed:
                                event_type, message, data = parsed
                                self._record_task_event(task["id"], task["run_id"], event_type, message, data)
                        return
                    if self.stop_event.is_set():
                        return
                    time.sleep(0.1)
        except FileNotFoundError:
            return

    def _record_task_event(self, task_id: str, run_id: str, event_type: str, message: str, data: dict) -> None:
        now = utc_ts()
        with connect(self.settings) as conn:
            with transaction(conn):
                conn.execute(
                    "update tasks set heartbeat_at=?, lease_expires_at=?, updated_at=? where id=? and status in ('running','cancel_requested')",
                    (now, now + 120, now, task_id),
                )
                add_event(conn, event_type, message, run_id=run_id, task_id=task_id, data=data)

    def _task_row(self, task_id: str) -> sqlite3.Row | None:
        with connect(self.settings) as conn:
            return conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()

    def _task_status(self, task_id: str) -> str:
        row = self._task_row(task_id)
        return str(row["status"]) if row else ""

    def _task_wants_cancel(self, task_id: str) -> bool:
        return self._task_status(task_id) == "cancel_requested"

    def _heartbeat(self, task_id: str) -> None:
        now = utc_ts()
        with connect(self.settings) as conn:
            conn.execute(
                "update tasks set heartbeat_at=?, lease_expires_at=?, updated_at=? where id=? and status in ('running','cancel_requested')",
                (now, now + 120, now, task_id),
            )
            conn.commit()

    def finish_task(self, task: dict, sentinel: dict, recovered: bool = False) -> None:
        status = str(sentinel.get("status") or "")
        if status == "success":
            review_result = None
            with connect(self.settings) as conn:
                ensure_review_after_commit = False
                with transaction(conn):
                    outputs = sentinel.get("outputs") if isinstance(sentinel.get("outputs"), list) else []
                    committed = commit_task_outputs(
                        conn,
                        run_id=task["run_id"],
                        task_id=task["id"],
                        input_hash=task["input_hash"],
                        outputs=outputs,
                        settings=self.settings,
                    )
                    now = utc_ts()
                    conn.execute(
                        """
                        update tasks set status='completed', completed_at=?, updated_at=?,
                          lease_owner=null, lease_expires_at=null, error=null
                        where id=?
                        """,
                        (now, now, task["id"]),
                    )
                    add_event(
                        conn,
                        "task.completed",
                        "Task completed",
                        run_id=task["run_id"],
                        task_id=task["id"],
                        data={"artifacts": [a["id"] for a in committed], "recovered": recovered},
                    )
                    self._apply_post_adoption_gate_states(conn, task, committed)
                    self._maybe_finish_run(conn, task["run_id"])
                    run = conn.execute("select * from workflow_runs where id = ?", (task["run_id"],)).fetchone()
                    script_needs_review = any(
                        (artifact.get("metadata") or {}).get("authority_type")
                        == "needs_review_draft"
                        and (artifact.get("metadata") or {}).get("review_required")
                        is True
                        and (artifact.get("metadata") or {}).get("release_eligible")
                        is False
                        for artifact in committed
                    )
                    # A draft awaiting review is a label on the reel, not a stop.
                    # Cancelling the 18 downstream tasks here meant no episode
                    # ever reached render, so the operator had nothing to review
                    # and the repair lane had nothing to rank. Nothing downstream
                    # reads release_eligible to stop rendering. Publication is
                    # still withheld by publish_mode, pinned to 'review_only'
                    # by a CHECK constraint, and by the separate final approval
                    # and release-gate verification.
                    if script_needs_review:
                        add_event(
                            conn,
                            "run.review_status",
                            "Verified script draft requires operator review",
                            run_id=task["run_id"],
                            level="warning",
                            data={
                                "reason": "script_needs_review",
                                "task_id": task["id"],
                                "auto_retry": False,
                            },
                        )
                    if (
                        run is not None
                        and run["workflow_type"] == "ai_sister.reel_pipeline"
                        and run["status"] in {"pending", "running", "completed"}
                    ):
                        ensure_review_after_commit = True
                if ensure_review_after_commit:
                    review_result = ensure_reel_review(
                        conn, task["run_id"], self.settings)
            if review_result is not None:
                self._maybe_trigger_reel_review_sync(
                    task["run_id"], review_result)
            self._cleanup_task_tmp(task)
            return
        if status == "cancelled":
            self.mark_task_terminal(task, "cancelled", str(sentinel.get("error") or "Cancelled"), recovered=recovered)
            return
        self.mark_task_terminal(task, "failed", str(sentinel.get("error") or "Task failed"), recovered=recovered)

    def mark_task_terminal(self, task: dict, status: str, error: str = "", recovered: bool = False) -> None:
        now = utc_ts()
        if status not in ("failed", "cancelled"):
            status = "failed"
        with connect(self.settings) as conn:
            with transaction(conn):
                run = conn.execute("select * from workflow_runs where id = ?", (task["run_id"],)).fetchone()
                billing_block_reel = (
                    status == "failed"
                    and str(error or "").startswith("billing_block:")
                    and task.get("task_type") in {"castle.reel_cover", "castle.reel_qc"}
                    and run is not None
                )
                script_provider_outage = (
                    status == "failed"
                    and run is not None
                    and run["workflow_type"] == "ai_sister.reel_pipeline"
                    and reel_script_provider_outage(task, error)
                )
                synth_sentence_hard_fail = (
                    status == "failed"
                    and run is not None
                    and run["workflow_type"] == "ai_sister.reel_pipeline"
                    and reel_synth_sentence_hard_fail(task, error)
                )
                if script_provider_outage:
                    conn.execute(
                        """
                        update tasks set status='failed', completed_at=?, updated_at=?,
                          lease_owner=null, lease_expires_at=null, error=?
                        where id=?
                        """,
                        (now, now, error[:1000] if error else None, task["id"]),
                    )
                    add_event(
                        conn,
                        "task.failed",
                        error or status,
                        run_id=task["run_id"],
                        task_id=task["id"],
                        level="error",
                        data={
                            "terminal_reason": "script_provider_outage",
                            "auto_retry": False,
                            "recovered": recovered,
                        },
                    )
                    self._cancel_all_pending_for_terminal(
                        conn,
                        task,
                        "Terminal script provider outage",
                        terminal_reason="script_provider_outage",
                    )
                    conn.execute(
                        """
                        update workflow_runs set status='failed', completed_at=?,
                          updated_at=?, error=? where id=?
                        """,
                        (
                            now,
                            now,
                            "script_provider_outage: no verified script artifact was produced",
                            task["run_id"],
                        ),
                    )
                    add_event(
                        conn,
                        "run.failed",
                        "Script provider outage produced no reviewable artifact",
                        run_id=task["run_id"],
                        level="error",
                        data={
                            "reason": "script_provider_outage",
                            "task_id": task["id"],
                            "auto_retry": False,
                        },
                    )
                    self._cleanup_task_tmp(task)
                    return
                if synth_sentence_hard_fail:
                    review_data = reel_synth_sentence_hard_fail_data(error)
                    conn.execute(
                        """
                        update tasks set status='failed', completed_at=?, updated_at=?,
                          lease_owner=null, lease_expires_at=null, error=?
                        where id=?
                        """,
                        (now, now, error[:1000] if error else None, task["id"]),
                    )
                    add_event(
                        conn,
                        "task.failed",
                        error or status,
                        run_id=task["run_id"],
                        task_id=task["id"],
                        level="error",
                        data={
                            "terminal_reason": "synth_sentence_hard_fail",
                            "auto_retry": False,
                            "recovered": recovered,
                        },
                    )
                    self._cancel_pending_descendants(conn, task, error)
                    conn.execute(
                        """
                        update workflow_runs set status='failed',
                          completed_at=?, updated_at=?, error=? where id=?
                        """,
                        (
                            now,
                            now,
                            "synth_sentence_hard_fail: producer emitted no renderable draft",
                            task["run_id"],
                        ),
                    )
                    add_event(
                        conn,
                        "run.failed",
                        "Synth producer emitted no renderable draft",
                        run_id=task["run_id"],
                        level="warning",
                        data={
                            "reason": "synth_sentence_hard_fail",
                            "task_id": task["id"],
                            "episode": review_data.get("episode"),
                            "turn_idx": review_data.get("turn_idx"),
                            "speaker": review_data.get("speaker"),
                            "failed_sentence_idx": review_data.get(
                                "failed_sentence_idx"),
                            "authored_text": review_data.get("authored_text"),
                            "failed_sentence": review_data.get(
                                "failed_sentence"),
                            "manifest": review_data.get("manifest"),
                        },
                    )
                    self._cleanup_task_tmp(task)
                    return
                if billing_block_reel:
                    conn.execute(
                        """
                        update tasks set status='failed', completed_at=?, updated_at=?,
                          lease_owner=null, lease_expires_at=null, error=?
                        where id=?
                        """,
                        (now, now, error[:1000] if error else None, task["id"]),
                    )
                    add_event(
                        conn,
                        "task.failed",
                        error or status,
                        run_id=task["run_id"],
                        task_id=task["id"],
                        level="error",
                        data={"billing_block": True, "recovered": recovered},
                    )
                    # Built-in M4 workflows are real DAGs too.  Billing is a
                    # fail-closed stop for every declared downstream node, not
                    # a custom.dag-only behavior.
                    self._cancel_pending_descendants(conn, task, error)
                    conn.execute(
                        "update workflow_runs set status='failed', completed_at=?, updated_at=?, error=? where id=?",
                        (now, now, "billing_block: required provider unavailable", task["run_id"]),
                    )
                    add_event(
                        conn,
                        "run.failed",
                        "Required provider billing blocked execution",
                        run_id=task["run_id"],
                        level="warning",
                        data={"reason": "billing_block", "task_id": task["id"]},
                    )
                    self._cleanup_task_tmp(task)
                    return
                if status == "failed":
                    attempt = int(task.get("attempt") or 0)
                    max_attempts = max(1, int(task.get("max_attempts") or 1))
                    if attempt + 1 < max_attempts and not billing_block_reel:
                        conn.execute(
                            """
                            update tasks set status='pending', attempt=?, completed_at=null,
                              lease_owner=null, lease_expires_at=null, heartbeat_at=null,
                              pid=null, pgid=null, process_start_time=null, error=?,
                              updated_at=?
                            where id=?
                            """,
                            (attempt + 1, error[:1000] if error else None, now, task["id"]),
                        )
                        conn.execute(
                            "update workflow_runs set status='running', updated_at=? where id=? and status in ('pending','running')",
                            (now, task["run_id"]),
                        )
                        add_event(
                            conn,
                            "task.retry_scheduled",
                            f"Retry scheduled ({attempt + 2}/{max_attempts})",
                            run_id=task["run_id"],
                            task_id=task["id"],
                            level="warning",
                            data={
                                "attempt": attempt,
                                "next_attempt": attempt + 1,
                                "max_attempts": max_attempts,
                                "error": error[:1000] if error else "",
                                "recovered": recovered,
                            },
                        )
                        self._cleanup_task_tmp(task)
                        return
                conn.execute(
                    """
                    update tasks set status=?, completed_at=?, updated_at=?,
                      lease_owner=null, lease_expires_at=null, error=?
                    where id=?
                    """,
                    (status, now, now, error[:1000] if error else None, task["id"]),
                )
                event_type = "task.cancelled" if status == "cancelled" else "task.failed"
                add_event(
                    conn,
                    event_type,
                    error or status,
                    run_id=task["run_id"],
                    task_id=task["id"],
                    level="warning" if status == "cancelled" else "error",
                    data={"recovered": recovered},
                )
                if status in {"failed", "cancelled"}:
                    self._cancel_pending_descendants(conn, task, error)
                self._maybe_finish_run(conn, task["run_id"])
        self._cleanup_task_tmp(task)

    def _cleanup_task_tmp(self, task: dict) -> None:
        work_dir = Path(task.get("work_dir") or self.settings.work_root / task["id"])
        shutil.rmtree(work_dir / "tmp", ignore_errors=True)

    def _release_recovered_reservation(self, task: dict, work_dir: Path) -> None:
        path = work_dir / "gpu_reservation.json"
        data = _read_json(path)
        if not data or data.get("released"):
            return
        resource_id = str(data.get("resource_id") or "").strip()
        reservation_id = str(data.get("reservation_id") or "").strip()
        actor = str(data.get("actor") or "orchestrator.worker.recovery").strip()
        if not resource_id or not reservation_id:
            return
        released, count = release_resource(resource_id, {"reservation_id": reservation_id, "actor": "orchestrator.worker.recovery"}, self.settings)
        if count:
            data["released"] = True
            data["released_at"] = utc_ts()
            data["released_by"] = "orchestrator.worker.recovery"
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            except OSError:
                pass
            with connect(self.settings) as conn:
                with transaction(conn):
                    add_event(
                        conn,
                        "task.resource_released",
                        "Recovered stale GPU reservation",
                        run_id=task["run_id"],
                        task_id=task["id"],
                        data={"resource_id": resource_id, "reservation_id": reservation_id, "actor": actor, "released": len(released)},
                    )

    def _maybe_finish_run(self, conn: sqlite3.Connection, run_id: str) -> None:
        _maybe_finish_run_from_counts(conn, run_id)

    def _apply_post_adoption_gate_states(self, conn: sqlite3.Connection, task: dict, artifacts: list[dict]) -> None:
        for artifact in artifacts:
            metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
            if metadata.get("workflow") != "castle.reel_qc" or metadata.get("role") != "qc":
                continue
            verdict = str(metadata.get("verdict") or "").upper()
            gate_state = "needs_review" if verdict == "FAIL" else "pending"
            set_artifact_gate_state(conn, artifact["id"], gate_state)
            add_event(
                conn,
                "artifact.gate_initialized",
                f"Initialized QC gate {gate_state}",
                run_id=task["run_id"],
                task_id=task["id"],
                artifact_id=artifact["id"],
                data={"gate_state": gate_state, "verdict": verdict or "UNKNOWN"},
            )

    def _cancel_pending_descendants(self, conn: sqlite3.Connection, task: dict, error: str) -> None:
        now = utc_ts()
        descendants = conn.execute(
            """
            with recursive downstream(task_id) as (
              select task_id from task_dependencies where depends_on_task_id = ?
              union
              select d.task_id from task_dependencies d
              join downstream x on x.task_id = d.depends_on_task_id
            )
            select t.* from tasks t
            join downstream d on d.task_id = t.id
            where t.run_id = ? and t.status = 'pending'
            """,
            (task["id"], task["run_id"]),
        ).fetchall()
        for child in descendants:
            conn.execute(
                "update tasks set status='cancelled', completed_at=?, updated_at=?, error=? where id=?",
                (now, now, "Cancelled because an upstream task failed", child["id"]),
            )
            add_event(
                conn,
                "task.cancelled",
                "Cancelled because an upstream task failed",
                run_id=task["run_id"],
                task_id=child["id"],
                level="warning",
                data={"upstream_task_id": task["id"], "upstream_error": error[:1000] if error else ""},
            )

    def _cancel_all_pending_for_terminal(
        self,
        conn: sqlite3.Connection,
        task: dict,
        error: str,
        *,
        terminal_reason: str,
    ) -> None:
        """Stop every unfinished sibling when a run-level terminal policy fires."""
        now = utc_ts()
        rows = conn.execute(
            """
            select * from tasks
            where run_id = ? and id != ?
              and status in ('pending', 'running', 'cancel_requested')
            """,
            (task["run_id"], task["id"]),
        ).fetchall()
        for child in rows:
            if child["status"] != "pending":
                if child["status"] == "running":
                    conn.execute(
                        "update tasks set status='cancel_requested', updated_at=?, error=? where id=?",
                        (now, error[:1000], child["id"]),
                    )
                    work_dir = Path(child["work_dir"] or "")
                    if work_dir:
                        try:
                            (work_dir / "cancel").touch()
                        except OSError:
                            pass
                    pid = int(child["pgid"] or child["pid"] or 0)
                    safe_killpg(
                        pid,
                        signal.SIGTERM,
                        child["process_start_time"],
                        child["process_marker"],
                    )
                    add_event(
                        conn,
                        "task.cancel_requested",
                        error,
                        run_id=task["run_id"],
                        task_id=child["id"],
                        level="warning",
                        data={
                            "terminal_task_id": task["id"],
                            "terminal_reason": terminal_reason,
                        },
                    )
                continue
            conn.execute(
                """
                update tasks set status='cancelled', completed_at=?, updated_at=?,
                  error=? where id=?
                """,
                (now, now, error[:1000], child["id"]),
            )
            add_event(
                conn,
                "task.cancelled",
                error,
                run_id=task["run_id"],
                task_id=child["id"],
                level="warning",
                data={
                    "terminal_task_id": task["id"],
                    "terminal_reason": terminal_reason,
                },
            )

    def _finalize_cancelled_runs(self, conn: sqlite3.Connection) -> None:
        for run in conn.execute("select * from workflow_runs where status='cancel_requested'").fetchall():
            active = conn.execute(
                "select count(*) as count from tasks where run_id=? and status in ('pending','running','cancel_requested')",
                (run["id"],),
            ).fetchone()["count"]
            if not active:
                now = utc_ts()
                conn.execute(
                    "update workflow_runs set status='cancelled', completed_at=?, updated_at=? where id=?",
                    (now, now, run["id"]),
                )
                add_event(conn, "run.cancelled", "Run cancelled", run_id=run["id"], level="warning")


def start_worker_once(settings: Settings | None = None) -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None:
            _WORKER.start()
            return
        _WORKER = OrchestratorWorker(settings)
        _WORKER.start()


def stop_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None:
            _WORKER.stop()
            _WORKER = None
