"""Private durable at-most-once render and exact-upload journal."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from .errors import SceneArtError, fail
from .manifests import canonical_json
from .protocol import (
    HASH,
    MAX_OUTPUT_BYTES,
    MAX_SAFE_INTEGER,
    error_code as validate_error_code,
    safe_id,
)
from .secure_path import DIRECTORY_FLAGS, READ_FILE_FLAGS, close_descriptors, open_absolute_directory


STATES = frozenset(
    {
        "claimed",
        "submit_intent",
        "submitted",
        "renderer_bound",
        "output_staged",
        "result_intent",
        "result_admitted",
        "upload_intent",
        "uploaded",
        "completed",
        "uncertain",
        "rejected_cleanup",
        "failure_intent",
        "failed",
    }
)


@dataclass(frozen=True, slots=True)
class Attempt:
    job_id: str
    request_id: str
    lease_token: str
    payload_hash: str
    generation_key: str
    scene_pack_hash: str
    render_plan_hash: str
    release_sha256: str
    variation_index: int
    seed: int
    prompt_id: str
    state: str
    compiled_graph_sha256: str | None
    output_node_id: str | None
    output_prefix: str | None
    output_path: str | None
    output_sha256: str | None
    output_bytes: int | None
    renderer_output_path: str | None
    renderer_output_sha256: str | None
    submission_id: str | None
    render_receipt_json: str | None
    render_receipt_hash: str | None
    screening_receipt_sha256: str | None
    last_error_code: str | None
    created_at: int
    updated_at: int


SELECT_COLUMNS = """job_id,request_id,lease_token,payload_hash,generation_key,
scene_pack_hash,render_plan_hash,release_sha256,variation_index,seed,prompt_id,state,
compiled_graph_sha256,output_node_id,output_prefix,output_path,output_sha256,output_bytes,
renderer_output_path,renderer_output_sha256,
submission_id,render_receipt_json,render_receipt_hash,screening_receipt_sha256,last_error_code,
created_at,updated_at"""


class RenderJournal:
    def __init__(self, database_path: Path) -> None:
        if not database_path.is_absolute() or "\x00" in os.fspath(database_path):
            fail("journal_path_invalid", "Journal path must be absolute")
        self.path = Path(os.path.abspath(os.fspath(database_path)))
        self._prepare_directory()
        self._migrate()

    @property
    def state_root(self) -> Path:
        return self.path.parent

    def _prepare_directory(self) -> None:
        descriptors: list[int] = []
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptors = open_absolute_directory(
                self.path.parent,
                code="journal_path_unsafe",
                message="Journal directory ancestry is unsafe",
            )
            info = os.fstat(descriptors[-1])
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                fail("journal_path_unsafe", "Journal directory must be owner-private")
            if self.path.exists():
                file_info = self.path.lstat()
                if (
                    not stat.S_ISREG(file_info.st_mode)
                    or stat.S_ISLNK(file_info.st_mode)
                    or file_info.st_uid != os.geteuid()
                    or file_info.st_nlink != 1
                    or stat.S_IMODE(file_info.st_mode) != 0o600
                ):
                    fail("journal_path_unsafe", "Journal file is unsafe")
        except SceneArtError:
            raise
        except OSError as error:
            raise SceneArtError("journal_unavailable", "Journal path is unavailable") from error
        finally:
            close_descriptors(descriptors)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        except BaseException:
            connection.close()
            raise
        os.chmod(self.path, 0o600)
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def _migrate(self) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS render_attempts (
                  job_id TEXT PRIMARY KEY,
                  request_id TEXT NOT NULL,
                  lease_token TEXT NOT NULL,
                  payload_hash TEXT NOT NULL,
                  generation_key TEXT NOT NULL,
                  scene_pack_hash TEXT NOT NULL,
                  render_plan_hash TEXT NOT NULL,
                  release_sha256 TEXT NOT NULL,
                  variation_index INTEGER NOT NULL,
                  seed INTEGER NOT NULL,
                  prompt_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  compiled_graph_sha256 TEXT,
                  output_node_id TEXT,
                  output_prefix TEXT,
                  output_path TEXT,
                  output_sha256 TEXT,
                  output_bytes INTEGER,
                  renderer_output_path TEXT,
                  renderer_output_sha256 TEXT,
                  submission_id TEXT,
                  render_receipt_json TEXT,
                  render_receipt_hash TEXT,
                  screening_receipt_sha256 TEXT,
                  last_error_code TEXT,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  CHECK (state IN (
                    'claimed','submit_intent','submitted','renderer_bound','output_staged','result_intent',
                    'result_admitted','upload_intent','uploaded','completed','uncertain',
                    'rejected_cleanup','failure_intent','failed'
                  )),
                  CHECK (length(payload_hash)=64),
                  CHECK (length(generation_key)=64),
                  CHECK (length(scene_pack_hash)=64),
                  CHECK (length(render_plan_hash)=64),
                  CHECK (length(release_sha256)=64),
                  CHECK (output_bytes IS NULL OR output_bytes > 0)
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(render_attempts)").fetchall()
            }
            for name in ("compiled_graph_sha256", "output_node_id", "output_prefix"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE render_attempts ADD COLUMN {name} TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS render_attempts_request ON render_attempts(request_id)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS render_attempts_prompt ON render_attempts(prompt_id)"
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Attempt | None:
        return None if row is None else Attempt(**dict(row))

    def get(self, job_id: str) -> Attempt | None:
        safe_id(job_id, "job_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {SELECT_COLUMNS} FROM render_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
        return self._row(row)

    def cleanup_ready(self) -> tuple[Attempt, ...]:
        return self._states(("uploaded",))

    def rejection_ready(self) -> tuple[Attempt, ...]:
        return self._states(("rejected_cleanup",))

    def failure_ready(self) -> tuple[Attempt, ...]:
        return self._states(("failure_intent",))

    def renderer_recovery_ready(self) -> tuple[Attempt, ...]:
        return self._states(("submit_intent", "submitted", "uncertain"))

    def renderer_orphan_ready(self) -> tuple[Attempt, ...]:
        return self._states(("renderer_bound",))

    def delivery_ready(self) -> tuple[Attempt, ...]:
        return self._states(
            ("output_staged", "result_intent", "result_admitted", "upload_intent", "uploaded")
        )

    def _states(self, states: tuple[str, ...]) -> tuple[Attempt, ...]:
        placeholders = ",".join("?" for _ in states)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT {SELECT_COLUMNS} FROM render_attempts
                    WHERE state IN ({placeholders}) ORDER BY updated_at,job_id LIMIT 128""",
                states,
            ).fetchall()
        return tuple(item for row in rows if (item := self._row(row)) is not None)

    def register_claim(
        self,
        *,
        job_id: str,
        request_id: str,
        lease_token: str,
        payload_hash: str,
        generation_key: str,
        scene_pack_hash: str,
        render_plan_hash: str,
        release_sha256: str,
        variation_index: int,
        seed: int,
        prompt_id: str,
    ) -> Attempt:
        for label, value in (
            ("job_id", job_id),
            ("request_id", request_id),
            ("lease_token", lease_token),
            ("prompt_id", prompt_id),
        ):
            safe_id(value, label)
        for label, value in (
            ("payload_hash", payload_hash),
            ("generation_key", generation_key),
            ("scene_pack_hash", scene_pack_hash),
            ("render_plan_hash", render_plan_hash),
            ("release_sha256", release_sha256),
        ):
            if HASH.fullmatch(value) is None:
                fail("journal_identity_invalid", f"{label} is invalid")
        if (
            type(variation_index) is not int
            or variation_index < 0
            or variation_index > 1_000_000
            or type(seed) is not int
            or seed < 0
            or seed > MAX_SAFE_INTEGER
        ):
            fail("journal_identity_invalid", "Render plan numbers are invalid")
        now = time.time_ns()
        values = (
            request_id,
            lease_token,
            payload_hash,
            generation_key,
            scene_pack_hash,
            render_plan_hash,
            release_sha256,
            variation_index,
            seed,
            prompt_id,
        )
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM render_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO render_attempts
                       (job_id,request_id,lease_token,payload_hash,generation_key,scene_pack_hash,
                        render_plan_hash,release_sha256,variation_index,seed,prompt_id,state,
                        created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,'claimed',?,?)""",
                    (job_id, *values, now, now),
                )
            else:
                actual = tuple(
                    existing[key]
                    for key in (
                        "request_id",
                        "lease_token",
                        "payload_hash",
                        "generation_key",
                        "scene_pack_hash",
                        "render_plan_hash",
                        "release_sha256",
                        "variation_index",
                        "seed",
                        "prompt_id",
                    )
                )
                if actual != values:
                    fail("journal_identity_conflict", "Existing render identity does not match claim")
            row = connection.execute(
                f"SELECT {SELECT_COLUMNS} FROM render_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
        assert row is not None
        return self._row(row)  # type: ignore[return-value]

    def _transition(
        self,
        job_id: str,
        *,
        allowed: tuple[str, ...],
        target: str,
        assignments: Mapping[str, object] | None = None,
        replay_states: tuple[str, ...] = (),
    ) -> bool:
        if target not in STATES:
            fail("journal_state_invalid", "Journal target state is invalid")
        assignments = assignments or {}
        allowed_columns = {
            "output_path",
            "output_sha256",
            "output_bytes",
            "submission_id",
            "renderer_output_path",
            "renderer_output_sha256",
            "compiled_graph_sha256",
            "output_node_id",
            "output_prefix",
            "render_receipt_json",
            "render_receipt_hash",
            "screening_receipt_sha256",
            "last_error_code",
        }
        if not set(assignments).issubset(allowed_columns):
            fail("journal_state_invalid", "Journal assignment is invalid")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM render_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                fail("journal_attempt_missing", "Render attempt is missing")
            if row["state"] in replay_states:
                for key, value in assignments.items():
                    if row[key] != value:
                        fail("journal_identity_conflict", "Journal replay binding changed")
                return False
            if row["state"] not in allowed:
                fail("journal_state_conflict", "Render attempt is not in the required state")
            columns = ["state=?", "updated_at=?"] + [f"{key}=?" for key in assignments]
            values = [target, time.time_ns(), *assignments.values(), job_id, row["state"]]
            changed = connection.execute(
                f"UPDATE render_attempts SET {','.join(columns)} WHERE job_id=? AND state=?", values
            ).rowcount
            if changed != 1:
                fail("journal_state_conflict", "Render attempt changed concurrently")
        return True

    def begin_submit(
        self,
        job_id: str,
        *,
        compiled_graph_sha256: str,
        output_node_id: str,
        output_prefix: str,
    ) -> bool:
        """Commit intent before POST /prompt; False always forbids a POST."""

        attempt = self.get(job_id)
        if attempt is None:
            fail("journal_attempt_missing", "Render attempt is missing")
        run_key = hashlib.sha256(
            (attempt.job_id + "\x00" + attempt.request_id).encode("utf-8", "strict")
        ).hexdigest()[:32]
        expected_prefix = f"NovelSceneArt/{run_key}/{attempt.prompt_id}"
        if (
            HASH.fullmatch(compiled_graph_sha256) is None
            or safe_id(output_node_id, "output_node_id") != output_node_id
            or output_prefix != expected_prefix
        ):
            fail("journal_submission_binding_invalid", "Submission binding is invalid")
        return self._transition(
            job_id,
            allowed=("claimed",),
            target="submit_intent",
            assignments={
                "compiled_graph_sha256": compiled_graph_sha256,
                "output_node_id": output_node_id,
                "output_prefix": output_prefix,
            },
            replay_states=tuple(STATES - {"claimed", "failed"}),
        )

    def mark_submitted(self, job_id: str) -> None:
        self._transition(
            job_id,
            allowed=("submit_intent",),
            target="submitted",
            replay_states=(
                "submitted",
                "output_staged",
                "result_intent",
                "result_admitted",
                "upload_intent",
                "uploaded",
                "completed",
            ),
        )

    def stage_output(
        self,
        job_id: str,
        *,
        path: Path,
        sha256: str,
        byte_count: int,
        screening_receipt_sha256: str,
        renderer_output_path: Path,
        renderer_output_sha256: str,
        render_receipt: Mapping[str, object],
        render_receipt_hash: str,
    ) -> None:
        try:
            root = self.state_root.resolve(strict=True)
            resolved = path.resolve(strict=True)
            inside = os.path.commonpath((str(root), str(resolved))) == str(root)
        except (OSError, ValueError):
            inside = False
            resolved = path
        receipt_json = canonical_json(render_receipt).decode("utf-8", "strict")
        if (
            not inside
            or any(
                HASH.fullmatch(value) is None
                for value in (
                    sha256,
                    screening_receipt_sha256,
                    renderer_output_sha256,
                    render_receipt_hash,
                )
            )
            or hashlib.sha256(receipt_json.encode("utf-8")).hexdigest() != render_receipt_hash
            or byte_count <= 0
            or byte_count > MAX_OUTPUT_BYTES
        ):
            fail("journal_output_invalid", "Staged output binding is invalid")
        self._transition(
            job_id,
            allowed=("renderer_bound",),
            target="output_staged",
            assignments={
                "output_path": str(resolved),
                "output_sha256": sha256,
                "output_bytes": byte_count,
                "screening_receipt_sha256": screening_receipt_sha256,
                "renderer_output_path": str(renderer_output_path),
                "renderer_output_sha256": renderer_output_sha256,
                "render_receipt_json": receipt_json,
                "render_receipt_hash": render_receipt_hash,
            },
            replay_states=(
                "output_staged",
                "result_intent",
                "result_admitted",
                "upload_intent",
                "uploaded",
                "completed",
            ),
        )

    def bind_renderer_output(self, job_id: str, *, path: Path, sha256: str) -> None:
        if not path.is_absolute() or HASH.fullmatch(sha256) is None:
            fail("journal_output_invalid", "Renderer output binding is invalid")
        self._transition(
            job_id,
            allowed=("submitted",),
            target="renderer_bound",
            assignments={
                "renderer_output_path": str(path),
                "renderer_output_sha256": sha256,
            },
            replay_states=(
                "renderer_bound",
                "output_staged",
                "result_intent",
                "result_admitted",
                "upload_intent",
                "uploaded",
                "completed",
            ),
        )

    def bind_failed_renderer_output(
        self,
        job_id: str,
        *,
        path: Path,
        sha256: str,
        error_code: str,
    ) -> None:
        """Atomically bind an exact failed-render output and its cleanup intent."""

        error_code = validate_error_code(error_code)
        if not path.is_absolute() or HASH.fullmatch(sha256) is None:
            fail("journal_output_invalid", "Failed renderer output binding is invalid")
        self._transition(
            job_id,
            allowed=("submit_intent", "submitted", "uncertain"),
            target="rejected_cleanup",
            assignments={
                "renderer_output_path": str(path),
                "renderer_output_sha256": sha256,
                "last_error_code": error_code,
            },
            replay_states=("rejected_cleanup", "failure_intent", "failed"),
        )

    def begin_rejected_cleanup(self, job_id: str, error_code: str) -> None:
        error_code = validate_error_code(error_code)
        self._transition(
            job_id,
            allowed=("renderer_bound",),
            target="rejected_cleanup",
            assignments={"last_error_code": error_code},
            replay_states=("rejected_cleanup", "failed"),
        )

    def mark_rejected_cleaned(self, job_id: str) -> None:
        self._transition(
            job_id,
            allowed=("rejected_cleanup",),
            target="failure_intent",
            replay_states=("failure_intent", "failed"),
        )

    def begin_failure_intent(self, job_id: str, code: str) -> None:
        code = validate_error_code(code)
        self._transition(
            job_id,
            allowed=("claimed",),
            target="failure_intent",
            assignments={"last_error_code": code},
            replay_states=("failure_intent", "failed"),
        )

    def begin_renderer_failure_intent(self, job_id: str, code: str) -> None:
        code = validate_error_code(code)
        self._transition(
            job_id,
            allowed=("submit_intent", "submitted", "uncertain"),
            target="failure_intent",
            assignments={"last_error_code": code},
            replay_states=("failure_intent", "failed"),
        )

    def mark_failure_reported(self, job_id: str) -> None:
        self._transition(
            job_id,
            allowed=("failure_intent",),
            target="failed",
            replay_states=("failed",),
        )

    def record_recovery_error(self, job_id: str, state: str, error_code: str) -> None:
        if state not in {
            "rejected_cleanup",
            "failure_intent",
            "output_staged",
            "result_intent",
            "result_admitted",
            "upload_intent",
            "uploaded",
        }:
            fail("journal_state_invalid", "Recovery state is invalid")
        error_code = validate_error_code(error_code)
        # A failure/rejection intent already contains the definitive job
        # failure code that Oracle must receive.  A later cleanup or transport
        # retry error is diagnostic only and must never replace that binding.
        assignments: Mapping[str, object] = (
            {} if state in {"rejected_cleanup", "failure_intent"} else {"last_error_code": error_code}
        )
        self._transition(
            job_id,
            allowed=(state,),
            target=state,
            assignments=assignments,
        )

    def begin_result_intent(self, job_id: str) -> bool:
        return self._transition(
            job_id,
            allowed=("output_staged",),
            target="result_intent",
            replay_states=("result_intent", "result_admitted", "upload_intent", "uploaded", "completed"),
        )

    def admit_result(self, job_id: str, submission_id: str) -> None:
        safe_id(submission_id, "submission_id")
        self._transition(
            job_id,
            allowed=("result_intent",),
            target="result_admitted",
            assignments={"submission_id": submission_id},
            replay_states=("result_admitted", "upload_intent", "uploaded", "completed"),
        )

    def begin_upload(self, job_id: str) -> bool:
        return self._transition(
            job_id,
            allowed=("result_admitted",),
            target="upload_intent",
            replay_states=("upload_intent", "uploaded", "completed"),
        )

    def mark_uploaded(self, job_id: str) -> None:
        self._transition(
            job_id,
            allowed=("upload_intent",),
            target="uploaded",
            replay_states=("uploaded", "completed"),
        )

    def mark_completed(self, job_id: str) -> None:
        self._transition(job_id, allowed=("uploaded",), target="completed", replay_states=("completed",))

    def mark_uncertain(self, job_id: str, error_code: str) -> None:
        error_code = validate_error_code(error_code)
        self._transition(
            job_id,
            allowed=("submit_intent", "submitted"),
            target="uncertain",
            assignments={"last_error_code": error_code},
            replay_states=("uncertain",),
        )

    def mark_adopted(self, job_id: str) -> None:
        self._transition(
            job_id,
            allowed=("submit_intent", "uncertain"),
            target="submitted",
            replay_states=(
                "submitted",
                "output_staged",
                "result_intent",
                "result_admitted",
                "upload_intent",
                "uploaded",
                "completed",
            ),
        )

    def mark_renderer_recovery_pending(self, job_id: str, error_code: str) -> None:
        error_code = validate_error_code(error_code)
        self._transition(
            job_id,
            allowed=("submit_intent", "submitted", "uncertain"),
            target="uncertain",
            assignments={"last_error_code": error_code},
        )

    def touch_renderer_recovery(self, job_id: str, state: str) -> None:
        if state not in {"submit_intent", "submitted", "uncertain"}:
            fail("journal_state_invalid", "Renderer recovery state is invalid")
        self._transition(job_id, allowed=(state,), target=state)

    def mark_failed_before_submit(self, job_id: str, error_code: str) -> None:
        self.begin_failure_intent(job_id, error_code)

    def write_staged_png(self, job_id: str, data: bytes) -> Path:
        safe_id(job_id, "job_id")
        if not data or len(data) > MAX_OUTPUT_BYTES:
            fail("journal_output_invalid", "Staged output size is invalid")
        directory = self.state_root / "staged-results"
        directory.mkdir(mode=0o700, exist_ok=True)
        directory_descriptors = open_absolute_directory(
            directory,
            code="journal_output_invalid",
            message="Staged output directory is unsafe",
        )
        try:
            info = os.fstat(directory_descriptors[-1])
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                fail("journal_output_invalid", "Staged output directory is unsafe")
            token = hashlib.sha256(job_id.encode("utf-8", "strict")).hexdigest()
            path = directory / f"{token}.png"
            directory_fd = directory_descriptors[-1]
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            try:
                descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
            except FileExistsError:
                descriptor = os.open(
                    path.name,
                    READ_FILE_FLAGS,
                    dir_fd=directory_fd,
                )
                try:
                    existing_info = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(existing_info.st_mode)
                        or existing_info.st_uid != os.geteuid()
                        or existing_info.st_nlink != 1
                        or stat.S_IMODE(existing_info.st_mode) != 0o600
                        or existing_info.st_size <= 0
                        or existing_info.st_size > MAX_OUTPUT_BYTES
                    ):
                        fail(
                            "journal_output_conflict",
                            "Existing staged output is unsafe",
                        )
                    chunks: list[bytes] = []
                    remaining = MAX_OUTPUT_BYTES + 1
                    while remaining:
                        chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    existing = b"".join(chunks)
                    existing_after = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                if (
                    (
                        existing_info.st_dev,
                        existing_info.st_ino,
                        existing_info.st_size,
                        existing_info.st_mtime_ns,
                        existing_info.st_ctime_ns,
                    )
                    != (
                        existing_after.st_dev,
                        existing_after.st_ino,
                        existing_after.st_size,
                        existing_after.st_mtime_ns,
                        existing_after.st_ctime_ns,
                    )
                    or len(existing) != existing_info.st_size
                    or existing != data
                ):
                    fail("journal_output_conflict", "Existing staged output has different bytes")
                return path
            try:
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(directory_fd)
            return path
        finally:
            close_descriptors(directory_descriptors)

    def cleanup_unbound_staged_png(self, job_id: str) -> None:
        """Delete only one exact private job-derived file after a stage crash."""

        safe_id(job_id, "job_id")
        filename = hashlib.sha256(job_id.encode("utf-8", "strict")).hexdigest() + ".png"
        descriptors: list[int] = []
        try:
            descriptors = open_absolute_directory(
                self.state_root,
                code="journal_output_invalid",
                message="Staged output root is unsafe",
                definite_pre_submit=False,
            )
            root_fd = descriptors[-1]
            root_info = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.geteuid()
                or root_info.st_mode & 0o077
            ):
                fail(
                    "journal_output_invalid",
                    "Staged output root is unsafe",
                    definite_pre_submit=False,
                )
            try:
                directory_fd = os.open("staged-results", DIRECTORY_FLAGS, dir_fd=root_fd)
            except FileNotFoundError:
                return
            descriptors.append(directory_fd)
            directory_info = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.geteuid()
                or directory_info.st_mode & 0o077
            ):
                fail(
                    "journal_output_invalid",
                    "Staged output directory is unsafe",
                    definite_pre_submit=False,
                )
            try:
                file_fd = os.open(filename, READ_FILE_FLAGS, dir_fd=directory_fd)
            except FileNotFoundError:
                return
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > MAX_OUTPUT_BYTES
            ):
                fail(
                    "journal_output_invalid",
                    "Unbound staged output is unsafe",
                    definite_pre_submit=False,
                )
            linked = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            binding = lambda item: (
                item.st_dev,
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )
            if binding(before) != binding(linked):
                fail(
                    "journal_output_changed",
                    "Unbound staged output changed before cleanup",
                    definite_pre_submit=False,
                )
            os.unlink(filename, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except FileNotFoundError:
            return
        except SceneArtError:
            raise
        except OSError as error:
            raise SceneArtError(
                "journal_output_invalid",
                "Unbound staged output cleanup failed",
                definite_pre_submit=False,
            ) from error
        finally:
            close_descriptors(descriptors)
