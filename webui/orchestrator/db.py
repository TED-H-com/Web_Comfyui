from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Settings, ensure_dirs, harden_private_file, settings_from_env


SCHEMA_VERSION = 5


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def utc_ts() -> float:
    return time.time()


def stable_json(data) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


LOCK_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _transient_lock_error(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return "locked" in message or "locking protocol" in message


def _retry_lock_operation(operation):
    for attempt in range(len(LOCK_RETRY_DELAYS_SECONDS) + 1):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if (
                not _transient_lock_error(error)
                or attempt == len(LOCK_RETRY_DELAYS_SECONDS)
            ):
                raise
            time.sleep(LOCK_RETRY_DELAYS_SECONDS[attempt])


def _enable_wal_for_new_database(conn: sqlite3.Connection) -> None:
    row = _retry_lock_operation(
        lambda: conn.execute("PRAGMA journal_mode=WAL").fetchone()
    )
    mode = str(row[0] if row else "").lower()
    if mode != "wal":
        raise sqlite3.OperationalError(
            f"new orchestrator database did not enter WAL mode: {mode or 'unknown'}"
        )


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    settings = ensure_dirs(settings or settings_from_env())
    conn = sqlite3.connect(settings.db_path, timeout=30.0, factory=ClosingConnection)
    try:
        # SQLite may create the database before honoring any application
        # pragma.  Harden it immediately, including databases left behind by
        # older releases with a process-umask-derived mode.
        harden_private_file(settings.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        # Existing WAL/SHM files are equally sensitive.  Newly-created SQLite
        # sidecars inherit the already-private database mode and are also
        # shielded by the 0700 database directory.
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                harden_private_file(Path(f"{settings.db_path}{suffix}"))
            except FileNotFoundError:
                pass
        return conn
    except Exception:
        conn.close()
        raise


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    _retry_lock_operation(lambda: conn.execute("BEGIN IMMEDIATE"))
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        try:
            _retry_lock_operation(conn.commit)
        except Exception:
            conn.rollback()
            raise


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def json_loads(value: str | bytes | None, fallback=None):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"alter table {table} add column {ddl}")


def _approvals_run_fk_cascades(conn: sqlite3.Connection) -> bool:
    try:
        rows = conn.execute("pragma foreign_key_list(approvals)").fetchall()
    except sqlite3.DatabaseError:
        return False
    for row in rows:
        if row["table"] == "workflow_runs" and row["from"] == "run_id" and str(row["on_delete"]).upper() == "CASCADE":
            return True
    return False


def _migrate_approvals_run_fk(conn: sqlite3.Connection) -> None:
    if not _approvals_run_fk_cascades(conn):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            """
            alter table approvals rename to approvals_old;
            create table approvals (
              id text primary key,
              artifact_id text references artifacts(id) on delete cascade,
              run_id text references workflow_runs(id) on delete set null,
              decision text not null,
              actor text not null,
              note text,
              supersedes_id text,
              created_at real not null
            );
            insert into approvals(id, artifact_id, run_id, decision, actor, note, supersedes_id, created_at)
            select id, artifact_id, run_id, decision, actor, note, supersedes_id, created_at
            from approvals_old;
            drop table approvals_old;
            """
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def initialize(settings: Settings | None = None) -> None:
    settings = ensure_dirs(settings or settings_from_env())
    initialize_journal_mode = (
        not settings.db_path.exists() or settings.db_path.stat().st_size == 0
    )
    with connect(settings) as conn:
        if initialize_journal_mode:
            _enable_wal_for_new_database(conn)
        conn.executescript(
            """
            create table if not exists schema_version (
              version integer primary key,
              applied_at real not null
            );

            create table if not exists volumes (
              id text primary key,
              base_path text not null,
              kind text not null default 'local',
              created_at real not null
            );

            create table if not exists workflow_versions (
              id text primary key,
              workflow_type text not null,
              version text not null,
              task_graph_json text not null,
              created_at real not null,
              unique(workflow_type, version)
            );

            create table if not exists workflow_runs (
              id text primary key,
              workflow_version_id text not null references workflow_versions(id),
              workflow_type text not null,
              title text not null,
              status text not null,
              idempotency_key text unique,
              input_json text not null,
              input_hash text not null,
              priority integer not null default 0,
              created_at real not null,
              updated_at real not null,
              started_at real,
              completed_at real,
              cancelled_at real,
              error text,
              parent_run_id text
            );

            create table if not exists tasks (
              id text primary key,
              run_id text not null references workflow_runs(id) on delete cascade,
              key text not null,
              task_type text not null,
              status text not null,
              lane text not null default 'cpu',
              priority integer not null default 0,
              attempt integer not null default 0,
              max_attempts integer not null default 1,
              reuse_policy text not null default 'off',
              reused_from_task_id text,
              input_json text not null,
              input_hash text not null,
              group_key text,
              lease_owner text,
              lease_expires_at real,
              heartbeat_at real,
              pid integer,
              pgid integer,
              process_start_time text,
              process_marker text,
              work_dir text,
              log_uri text,
              error text,
              created_at real not null,
              updated_at real not null,
              started_at real,
              completed_at real,
              unique(run_id, key)
            );

            create table if not exists task_dependencies (
              task_id text not null references tasks(id) on delete cascade,
              depends_on_task_id text not null references tasks(id) on delete cascade,
              primary key(task_id, depends_on_task_id)
            );

            create table if not exists artifacts (
              id text primary key,
              run_id text references workflow_runs(id) on delete set null,
              producing_task_id text references tasks(id) on delete set null,
              artifact_type text not null,
              status text not null,
              gate_state text not null default 'not_required',
              volume_id text references volumes(id),
              relative_path text,
              inline_json text,
              content_hash text,
              input_hash text,
              source_type text not null default 'generated',
              source_uri text,
              source_meta_json text not null default '{}',
              metadata_json text not null default '{}',
              version text not null default 'v1',
              created_at real not null,
              updated_at real not null
            );

            create table if not exists task_inputs (
              task_id text not null references tasks(id) on delete cascade,
              name text not null,
              artifact_id text references artifacts(id) on delete set null,
              value_json text,
              primary key(task_id, name)
            );

            create table if not exists task_outputs (
              task_id text not null references tasks(id) on delete cascade,
              name text not null,
              artifact_id text not null references artifacts(id) on delete cascade,
              primary key(task_id, name, artifact_id)
            );

            create table if not exists artifact_tags (
              artifact_id text not null references artifacts(id) on delete cascade,
              family text not null,
              name text not null,
              primary key(artifact_id, family, name)
            );

            create table if not exists tag_registry (
              family text not null,
              name text not null,
              description text,
              created_at real not null,
              primary key(family, name)
            );

            create table if not exists gate_reports (
              id text primary key,
              artifact_id text references artifacts(id) on delete cascade,
              task_id text references tasks(id) on delete set null,
              gate_type text not null,
              status text not null,
              report_json text not null,
              created_at real not null
            );

            create table if not exists approvals (
              id text primary key,
              artifact_id text references artifacts(id) on delete cascade,
              run_id text references workflow_runs(id) on delete set null,
              decision text not null,
              actor text not null,
              note text,
              supersedes_id text,
              created_at real not null
            );

            create table if not exists events (
              id integer primary key autoincrement,
              run_id text references workflow_runs(id) on delete cascade,
              task_id text references tasks(id) on delete cascade,
              artifact_id text references artifacts(id) on delete cascade,
              event_type text not null,
              level text not null default 'info',
              message text not null,
              data_json text not null default '{}',
              created_at real not null
            );

            create table if not exists reel_review_receipts (
              source text not null,
              source_event_id text not null,
              source_request_id text not null,
              reel_id text not null,
              media_revision text not null,
              review_revision integer not null,
              payload_sha256 text not null,
              event_sha256 text not null,
              notes text,
              reviewer text,
              release_binding_sha256 text,
              binding_json text,
              decision text not null,
              result text not null check(result in ('applied', 'failed', 'stale')),
              error_code text,
              canonical_status text,
              created_at real not null,
              updated_at real not null,
              applied_at real,
              primary key(source, source_event_id),
              unique(source, source_request_id)
            );

            create index if not exists idx_runs_status_created on workflow_runs(status, created_at desc);
            create index if not exists idx_tasks_status_lane_priority on tasks(status, lane, priority desc, created_at asc);
            create index if not exists idx_tasks_run on tasks(run_id, created_at asc);
            create index if not exists idx_tasks_reuse on tasks(task_type, input_hash, status, completed_at desc);
            create index if not exists idx_artifacts_run on artifacts(run_id, created_at desc);
            create index if not exists idx_artifacts_hash on artifacts(content_hash);
            create index if not exists idx_events_cursor on events(id);
            create index if not exists idx_events_run on events(run_id, id);
            create index if not exists idx_reel_review_receipts_revision
              on reel_review_receipts(source, reel_id, review_revision desc);
            """
        )
        _migrate_approvals_run_fk(conn)
        _ensure_column(conn, "tasks", "attempt", "attempt integer not null default 0")
        _ensure_column(conn, "reel_review_receipts", "notes", "notes text")
        _ensure_column(conn, "reel_review_receipts", "reviewer", "reviewer text")
        _ensure_column(conn, "tasks", "max_attempts", "max_attempts integer not null default 1")
        _ensure_column(conn, "tasks", "reuse_policy", "reuse_policy text not null default 'off'")
        _ensure_column(conn, "tasks", "reused_from_task_id", "reused_from_task_id text")
        _ensure_column(conn, "workflow_runs", "parent_run_id", "parent_run_id text")
        _ensure_column(
            conn,
            "reel_review_receipts",
            "release_binding_sha256",
            "release_binding_sha256 text",
        )
        _ensure_column(
            conn,
            "reel_review_receipts",
            "binding_json",
            "binding_json text",
        )
        conn.execute("create index if not exists idx_tasks_reuse on tasks(task_type, input_hash, status, completed_at desc)")
        now = utc_ts()
        conn.execute(
            "insert or ignore into schema_version(version, applied_at) values (?, ?)",
            (SCHEMA_VERSION, now),
        )
        conn.execute(
            "insert into volumes(id, base_path, kind, created_at) values (?, ?, ?, ?) "
            "on conflict(id) do update set base_path=excluded.base_path, kind=excluded.kind",
            ("local-local-workstation", str(Path(settings.artifact_root).resolve()), "local", now),
        )
        conn.commit()
