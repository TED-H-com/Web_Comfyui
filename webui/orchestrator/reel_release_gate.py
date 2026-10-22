from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from .config import Settings, settings_from_env
from .cover_audit import validate_cover_audit
from .db import json_loads
from .reel_proxy_contract import (
    canonical_proxy_derivative_matches,
    canonical_proxy_has_faststart,
    canonical_proxy_metadata_matches,
    canonical_proxy_stream_info,
)
from .reel_qc_contract import (
    QC_JUDGE_AUTHORITY,
    QC_JUDGE_AUTHORITY_FINGERPRINT,
    QC_TASK_SCHEMA_VERSION,
    QC_QUALITY_AUTHORITY,
    QC_QUALITY_AUTHORITY_FINGERPRINT,
    QC_VISION_AUTHORITY,
    QC_VISION_AUTHORITY_FINGERPRINT,
    canonical_draft_completion,
    canonical_qc_cost,
    canonical_release_authority_matches,
    checks_verdict as reel_qc_checks_verdict,
    qc_evidence_input_hash,
    validate_quality_assessment,
)
from .review import HARD_REQUIRED_ROLES, review_artifact_matches_role
from .review_sync_trigger import review_is_ready_for_remote_sync
from .tasks import (
    input_hash,
    reel_m4_graph,
    task_input_signature,
    workflow_topology_graph,
    workflow_version_id,
)


RELEASE_GATE_SCHEMA_VERSION = "castle-reel-release-gate-v1"
ELIGIBLE_RUN_STATUSES = frozenset({"needs_review", "completed_approved"})
REVIEW_ELIGIBLE_RUN_STATUSES = frozenset({
    "completed", "needs_review", "completed_approved",
})
EXPECTED_TASK_KEYS = frozenset({
    "runtime_assets",
    "pull",
    "forum_meta",
    "script",
    "wardrobe_gate",
    "cover",
    "scene",
    "host_rig",
    "synth",
    "beats",
    "peer_synth",
    "host_reaction_bank",
    "host_text",
    "host_synth",
    "champion_synth",
    "audio_listener_clips",
    "prepare_render",
    "render_sisters",
    "render_host",
    "assemble",
    "review_proxy",
    "audio_listener_final",
    "qc",
})
APPROVED_SCRIPT_OMITTED_TASK_KEYS = frozenset({"pull", "forum_meta"})
EXPECTED_APPROVED_SCRIPT_TASK_KEYS = (
    EXPECTED_TASK_KEYS - APPROVED_SCRIPT_OMITTED_TASK_KEYS
)
TOLERATED_REVIEW_ONLY_EXTRA_TASK_KEYS = frozenset({"auto_proxy"})
COVER_REUSE_OUTPUT_NAMES = frozenset({
    "cover",
    "cover_authority",
    "cover_bundle",
    "cover_kv",
    "metadata",
})
HASH_PINNED_REUSE_TASK_KEYS = frozenset({
    "assemble",
    "audio_listener_clips",
    "audio_listener_final",
    "beats",
    "champion_synth",
    "cover",
    "host_reaction_bank",
    "host_synth",
    "host_text",
    "peer_synth",
    "qc",
    "render_host",
    "render_sisters",
    "review_proxy",
    "script",
    "synth",
})
MAX_REUSE_SOURCE_DEPTH = 8
ROLE_PRODUCER_KEYS = {
    "final": "assemble",
    "proxy": "review_proxy",
    "cover": "cover",
    "qc": "qc",
}
ROLE_OUTPUT_NAMES = {
    "final": "final",
    "proxy": "review_proxy",
    "cover": "cover",
    "qc": "qc",
}
ROLE_WORKFLOWS = {
    "final": "castle.reel_assemble",
    "proxy": "artifact.make_proxy",
    "cover": "castle.reel_cover",
    "qc": "castle.reel_qc",
}
TASK_INTERNAL_FIELDS = frozenset({
    "_run_id",
    "_task_id",
    "_workflow_type",
    "_workflow_version_id",
})
HASH_RE = re.compile(r"[0-9a-f]{64}")
RUN_ID_RE = re.compile(r"run_[A-Za-z0-9]{8,80}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RUN_BOUND_GRAPH_INPUT_FIELDS = {
    "synth": frozenset({"listener_rejection_ledger"}),
    "peer_synth": frozenset({"listener_rejection_ledger"}),
    "host_text": frozenset({"listener_rejection_ledger"}),
    "audio_listener_clips": frozenset({"listener_rejection_ledger"}),
    "review_proxy": frozenset({"implementation_hash"}),
}
MIN_VIDEO_BYTES = 1024 * 1024
MAX_PROXY_BYTES = 300 * 1024 * 1024
MIN_COVER_BYTES = 1024
MAX_COVER_BYTES = 10 * 1024 * 1024
MAX_QC_BYTES = 4 * 1024 * 1024


class ReelReleaseGateError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)


def _fail(code: str, detail: str) -> None:
    raise ReelReleaseGateError(code, detail)


def _restore_run_bound_graph_inputs(
    expected_specs: dict[str, dict],
    tasks_by_key: dict[str, dict],
    *,
    episode: str,
) -> None:
    """Keep post-run mutable state and release paths out of qualification."""
    for key, fields in RUN_BOUND_GRAPH_INPUT_FIELDS.items():
        expected = expected_specs.get(key)
        actual = tasks_by_key.get(key)
        static_input = expected.get("input") if isinstance(expected, dict) else None
        task_input = json_loads(actual.get("input_json"), None) if actual else None
        if not isinstance(static_input, dict) or not isinstance(task_input, dict):
            _fail("topology_mismatch", "run-bound task input is unavailable")
        for field in fields:
            value = task_input.get(field)
            if field == "implementation_hash":
                if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                    _fail("task_contract_mismatch", "run-bound proxy implementation is invalid")
            else:
                if (
                    not isinstance(value, dict)
                    or set(value) != {"exists", "path", "sha256"}
                    or type(value.get("exists")) is not bool
                    or value.get("path") != f"listener_rejection_ledger_{episode}.json"
                    or (
                        value["exists"]
                        and (
                            not isinstance(value.get("sha256"), str)
                            or SHA256_RE.fullmatch(value["sha256"]) is None
                        )
                    )
                    or (not value["exists"] and value.get("sha256") != "")
                ):
                    _fail("task_contract_mismatch", "run-bound listener ledger is invalid")
            static_input[field] = value


def open_read_only_connection(settings: Settings | None = None) -> sqlite3.Connection:
    """Open the orchestrator database without creating directories or WAL state."""
    settings = settings or settings_from_env()
    configured_path = settings.db_path.expanduser()
    if configured_path.is_symlink() or not configured_path.is_file():
        _fail("database_unavailable", "orchestrator database is missing or unsafe")
    db_path = configured_path.resolve()
    uri = "file:" + quote(str(db_path), safe="/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    except sqlite3.Error as error:
        raise ReelReleaseGateError(
            "database_unavailable", "could not open orchestrator database read-only") from error
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ffprobe_video(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        _fail("video_probe_unavailable", "ffprobe is required for release qualification")
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,pix_fmt,width,height,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReelReleaseGateError("video_probe_failed", "ffprobe could not inspect review video") from error
    if completed.returncode != 0:
        _fail("video_probe_failed", "ffprobe rejected review video")
    try:
        raw = json.loads(completed.stdout)
        duration = float((raw.get("format") or {}).get("duration"))
        streams = raw.get("streams")
        format_name = str((raw.get("format") or {}).get("format_name") or "")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ReelReleaseGateError("video_probe_failed", "ffprobe returned invalid metadata") from error
    if not math.isfinite(duration) or duration <= 1.0 or not isinstance(streams, list):
        _fail("video_probe_failed", "review video has no valid duration")
    video_streams = [row for row in streams if isinstance(row, dict) and row.get("codec_type") == "video"]
    audio_streams = [row for row in streams if isinstance(row, dict) and row.get("codec_type") == "audio"]
    if not video_streams or not audio_streams:
        _fail("video_probe_failed", "review video must contain video and audio streams")
    width = int(video_streams[0].get("width") or 0)
    height = int(video_streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        _fail("video_probe_failed", "review video dimensions are invalid")
    return {
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "has_audio": True,
        "video_codec": str(video_streams[0].get("codec_name") or ""),
        "audio_codec": str(audio_streams[0].get("codec_name") or ""),
        "audio_bit_rate": int(audio_streams[0].get("bit_rate") or 0),
        "pix_fmt": str(video_streams[0].get("pix_fmt") or ""),
        "format_name": format_name,
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
    }


def _task_dependencies(conn: sqlite3.Connection, run_id: str) -> dict[str, set[str]]:
    rows = conn.execute(
        """
        select child.key as task_key, child.run_id as child_run_id,
               parent.key as dependency_key, parent.run_id as parent_run_id
        from task_dependencies d
        join tasks child on child.id = d.task_id
        join tasks parent on parent.id = d.depends_on_task_id
        where child.run_id = ?
        """,
        (run_id,),
    ).fetchall()
    result: dict[str, set[str]] = {}
    for row in rows:
        if row["parent_run_id"] != run_id:
            _fail(
                "task_contract_mismatch",
                "current Reel task has a cross-run dependency",
            )
        result.setdefault(str(row["task_key"]), set()).add(str(row["dependency_key"]))
    return result


def _metadata_indicates_reuse(metadata: object) -> bool:
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("reused")
        or metadata.get("idempotent_reuse")
        or metadata.get("reused_from_artifact_id")
        or metadata.get("reused_from_task_id")
    )


def _artifact_id_from_source_uri(source_uri: object) -> str:
    text = str(source_uri or "")
    if not text.startswith("artifact:"):
        return ""
    artifact_id = text[len("artifact:"):]
    if not artifact_id or any(ch.isspace() for ch in artifact_id):
        return ""
    return artifact_id


def _reuse_compare_fields_for_task(producer_key: str) -> tuple[str, ...]:
    if producer_key == "cover":
        return (
            "workflow",
            "role",
            "implementation_hash",
            "source_content_hash",
            "theme",
            "topic",
        )
    return ("workflow", "role", "stage", "relative_path")


def _reuse_source_chain_is_valid(
    conn: sqlite3.Connection,
    artifact: dict,
    metadata: object,
    producer: dict | None,
    *,
    output_name: str,
) -> bool:
    """Accept only hash-pinned cache materialized by this run's current task."""
    producer_key = str((producer or {}).get("key") or "")
    task_is_allowed = (
        producer_key in HASH_PINNED_REUSE_TASK_KEYS
        and str((producer or {}).get("reuse_policy") or "off") == "input_hash"
        and bool(str((producer or {}).get("reused_from_task_id") or ""))
        and (
            producer_key != "cover"
            or output_name in COVER_REUSE_OUTPUT_NAMES
        )
    )
    if (
        producer is None
        or not task_is_allowed
        or artifact.get("source_type") != "reused"
        or artifact.get("status") != "available"
        or artifact.get("input_hash") != producer.get("input_hash")
        or not isinstance(metadata, dict)
        or not _metadata_indicates_reuse(metadata)
    ):
        return False
    source_id = _artifact_id_from_source_uri(artifact.get("source_uri"))
    if (
        not source_id
        or str(metadata.get("reused_from_artifact_id") or "") != source_id
        or str(metadata.get("reused_from_task_id") or "") == ""
    ):
        return False
    compare_fields = _reuse_compare_fields_for_task(producer_key)
    current_metadata = metadata
    seen = {str(artifact.get("id") or "")}
    for _depth in range(MAX_REUSE_SOURCE_DEPTH):
        if source_id in seen:
            return False
        seen.add(source_id)
        source_row = conn.execute(
            "select * from artifacts where id = ?", (source_id,)
        ).fetchone()
        if source_row is None:
            return False
        source = dict(source_row)
        if (
            source.get("status") != "available"
            or source.get("artifact_type") != artifact.get("artifact_type")
            or source.get("content_hash") != artifact.get("content_hash")
            or source.get("input_hash") != artifact.get("input_hash")
        ):
            return False
        source_metadata = json_loads(source.get("metadata_json"), {})
        if not isinstance(source_metadata, dict):
            return False
        for field in compare_fields:
            if current_metadata.get(field) != source_metadata.get(field):
                return False
        if source.get("source_type") == "generated":
            return not _metadata_indicates_reuse(source_metadata)
        if source.get("source_type") != "reused" or not _metadata_indicates_reuse(source_metadata):
            return False
        source_id = _artifact_id_from_source_uri(source.get("source_uri"))
        if (
            not source_id
            or str(source_metadata.get("reused_from_artifact_id") or "") != source_id
            or str(source_metadata.get("reused_from_task_id") or "") == ""
        ):
            return False
        current_metadata = source_metadata
    return True


def _cover_magic_is_valid(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return False
    suffix = path.suffix.lower()
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    return False


def _lexical_absolute_path(value: object, *, label: str) -> Path:
    raw = os.path.expanduser(str(value or ""))
    if not raw or not os.path.isabs(raw) or "\\" in raw:
        _fail("artifact_path_unsafe", f"{label} path is not canonical absolute POSIX")
    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts[1:]):
        _fail("artifact_path_unsafe", f"{label} path contains unsafe components")
    return Path(raw)


def _nofollow_file_identity(
    path: Path,
    *,
    label: str,
    snapshot_path: Path | None = None,
) -> tuple[int, str]:
    parts = PurePosixPath(str(path)).parts
    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    file_fd = -1
    snapshot_fd = -1
    try:
        for component in parts[1:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            _fail("artifact_path_unsafe", f"{label} is not a regular file")
        if snapshot_path is not None:
            snapshot_fd = os.open(
                snapshot_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                | os.O_CLOEXEC,
                0o600,
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if snapshot_fd >= 0:
                view = memoryview(chunk)
                while view:
                    written = os.write(snapshot_fd, view)
                    if written <= 0:
                        raise OSError("short immutable snapshot write")
                    view = view[written:]
        if snapshot_fd >= 0:
            os.fchmod(snapshot_fd, 0o400)
            os.fsync(snapshot_fd)
        return int(info.st_size), digest.hexdigest()
    except ReelReleaseGateError:
        raise
    except OSError as error:
        raise ReelReleaseGateError(
            "artifact_path_unsafe", f"{label} path contains a symlink or missing component") from error
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if file_fd >= 0:
            os.close(file_fd)
        os.close(directory_fd)


def _verified_lexical_file(
    path_value: object,
    *,
    label: str,
    expected_digest: str,
    snapshot_path: Path | None = None,
) -> Path:
    path = _lexical_absolute_path(path_value, label=label)
    size, digest = _nofollow_file_identity(
        path, label=label, snapshot_path=snapshot_path)
    if size < 0 or HASH_RE.fullmatch(expected_digest) is None or digest != expected_digest:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
        _fail("artifact_hash_mismatch", f"{label} bytes do not match the stored hash")
    return snapshot_path if snapshot_path is not None else path


def _artifact_lexical_path(
    conn: sqlite3.Connection,
    row: dict,
    *,
    label: str,
    snapshot_path: Path | None = None,
) -> Path:
    volume_id = str(row.get("volume_id") or "")
    relative = str(row.get("relative_path") or "")
    volume = conn.execute(
        "select base_path from volumes where id = ?", (volume_id,)
    ).fetchone()
    if volume is None or not relative or "\\" in relative:
        _fail("artifact_path_unsafe", f"{label} has no canonical volume path")
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        _fail("artifact_path_unsafe", f"{label} relative path is unsafe")
    base = _lexical_absolute_path(volume["base_path"], label=f"{label} volume")
    path = Path(str(base), *relative_path.parts)
    return _verified_lexical_file(
        path,
        label=label,
        expected_digest=str(row.get("content_hash") or ""),
        snapshot_path=snapshot_path,
    )


def _verified_role_file(
    conn: sqlite3.Connection,
    role: str,
    row: dict,
    *,
    snapshot_root: Path,
) -> tuple[Path, dict]:
    snapshot_id = hashlib.sha256(
        str(row.get("id") or "").encode("utf-8")).hexdigest()[:16]
    snapshot = snapshot_root / (
        f"{role}-{snapshot_id}{Path(str(row.get('relative_path') or '')).suffix}"
    )
    path = _artifact_lexical_path(
        conn,
        row,
        label=f"{role} artifact",
        snapshot_path=snapshot,
    )
    metadata = json_loads(row.get("metadata_json"), {})
    if not isinstance(metadata, dict):
        _fail("artifact_metadata_invalid", f"{role} artifact metadata is invalid")
    return path, metadata


def _json_pointer_value(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        _fail("task_binding_mismatch", "canonical JSON binding pointer is invalid")
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            _fail("task_binding_mismatch", "canonical JSON binding cannot be resolved")
    return current


def _verified_artifact_file(
    conn: sqlite3.Connection,
    artifact: dict,
    cache: dict[tuple[str, bool], Path],
    *,
    immutable_json_snapshot: bool,
    snapshot_root: Path,
) -> Path:
    artifact_id = str(artifact.get("id") or "")
    cache_key = (artifact_id, immutable_json_snapshot)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    snapshot_path = None
    if immutable_json_snapshot:
        snapshot_id = hashlib.sha256(
            artifact_id.encode("utf-8")).hexdigest()[:16]
        suffix = Path(str(artifact.get("relative_path") or "")).suffix
        snapshot_path = snapshot_root / f"bound-{snapshot_id}{suffix}"
    path = _artifact_lexical_path(
        conn,
        artifact,
        label=f"bound artifact {artifact_id}",
        snapshot_path=snapshot_path,
    )
    cache[cache_key] = path
    return path


def _validate_task_materialization(
    conn: sqlite3.Connection,
    run: dict,
    actual: dict,
    expected: dict,
    tasks_by_key: dict[str, dict],
    artifact_file_cache: dict[tuple[str, bool], Path],
    snapshot_root: Path,
) -> dict:
    key = str(actual["key"])
    task_input = json_loads(actual.get("input_json"), None)
    if not isinstance(task_input, dict):
        _fail("task_contract_mismatch", f"{key} task input is invalid")
    static_input = expected.get("input") or {}
    bindings = expected.get("bindings") or {}
    if not isinstance(static_input, dict) or not isinstance(bindings, dict):
        _fail("canonical_topology_invalid", f"{key} canonical input contract is invalid")
    expected_keys = set(static_input) | set(bindings) | set(TASK_INTERNAL_FIELDS)
    if bindings:
        expected_keys.add("_input_bindings")
    if set(task_input) != expected_keys:
        _fail("task_contract_mismatch", f"{key} task input field set differs from the canonical graph")
    for field, value in static_input.items():
        if task_input.get(field) != value:
            _fail("task_contract_mismatch", f"{key} task input differs at {field}")
    expected_internal = {
        "_run_id": run["id"],
        "_task_id": actual["id"],
        "_workflow_type": run["workflow_type"],
        "_workflow_version_id": run["workflow_version_id"],
    }
    for field, value in expected_internal.items():
        if task_input.get(field) != value:
            _fail("task_contract_mismatch", f"{key} task identity differs at {field}")
    if bindings and task_input.get("_input_bindings") != bindings:
        _fail("task_binding_mismatch", f"{key} binding declarations differ from the canonical graph")
    if task_input_signature(str(actual["task_type"]), task_input) != actual.get("input_hash"):
        _fail("task_input_hash_mismatch", f"{key} stored task input hash is not current")

    input_rows = conn.execute(
        "select * from task_inputs where task_id = ? order by name", (actual["id"],)
    ).fetchall()
    inputs_by_name = {str(row["name"]): dict(row) for row in input_rows}
    if set(inputs_by_name) != {"payload", *bindings} or len(input_rows) != 1 + len(bindings):
        _fail("task_binding_mismatch", f"{key} materialized task-input rows are incomplete")
    payload_row = inputs_by_name["payload"]
    if payload_row.get("artifact_id") is not None or json_loads(
            payload_row.get("value_json"), None) != task_input:
        _fail("task_binding_mismatch", f"{key} payload input row differs from the task")

    for field, binding in sorted(bindings.items()):
        if not isinstance(binding, dict):
            _fail("canonical_topology_invalid", f"{key}.{field} binding is invalid")
        producer_key = str(binding.get("task") or "")
        output_name = str(binding.get("output") or "")
        producer = tasks_by_key.get(producer_key)
        if producer is None:
            _fail("canonical_topology_invalid", f"{key}.{field} producer is missing")
        output_rows = conn.execute(
            """
            select a.* from task_outputs o
            join artifacts a on a.id = o.artifact_id
            where o.task_id = ? and o.name = ?
            """,
            (producer["id"], output_name),
        ).fetchall()
        if len(output_rows) != 1:
            _fail("task_binding_mismatch", f"{key}.{field} does not have one canonical producer output")
        artifact = dict(output_rows[0])
        metadata = json_loads(artifact.get("metadata_json"), {})
        source_is_current = (
            artifact.get("source_type") == "generated"
            or _reuse_source_chain_is_valid(
                conn,
                artifact,
                metadata,
                producer,
                output_name=output_name,
            )
        )
        if (
            artifact.get("run_id") != run["id"]
            or artifact.get("producing_task_id") != producer["id"]
            or artifact.get("status") != "available"
            or not source_is_current
        ):
            _fail("task_binding_mismatch", f"{key}.{field} producer artifact is not fresh/current")
        selector = str(binding.get("select") or "artifact_ref")
        path = _verified_artifact_file(
            conn,
            artifact,
            artifact_file_cache,
            immutable_json_snapshot=(selector == "json_value"),
            snapshot_root=snapshot_root,
        )
        digest = str(artifact["content_hash"])
        if selector == "artifact_ref":
            expected_value: object = {
                "kind": "artifact",
                "artifact_id": artifact["id"],
                "content_hash": digest,
            }
        elif selector == "artifact_id":
            expected_value = artifact["id"]
        elif selector == "content_hash":
            expected_value = digest
        elif selector == "path_ref":
            target = _verified_lexical_file(
                binding.get("path"),
                label=f"{key}.{field} path binding",
                expected_digest=digest,
            )
            expected_value = {
                "kind": "path",
                "path": str(target),
                "content_hash": digest,
            }
        elif selector == "json_value":
            if path.stat().st_size > 1024 * 1024:
                _fail("task_binding_mismatch", f"{key}.{field} JSON producer is too large")
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ReelReleaseGateError(
                    "task_binding_mismatch", f"{key}.{field} JSON producer is invalid") from error
            expected_value = _json_pointer_value(
                document, str(binding.get("pointer") or ""))
        else:
            _fail("canonical_topology_invalid", f"{key}.{field} selector is invalid")
        binding_row = inputs_by_name[field]
        if (
            binding_row.get("artifact_id") != artifact["id"]
            or json_loads(binding_row.get("value_json"), None) != expected_value
            or task_input.get(field) != expected_value
        ):
            _fail("task_binding_mismatch", f"{key}.{field} materialized value/lineage differs")
    return task_input


def _raw_review_roles(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    producer_task_ids: dict[str, str] | None = None,
) -> dict[str, dict]:
    rows = conn.execute(
        """
        select a.*, group_concat(at.name, ',') as raw_role_names
        from artifacts a
        join artifact_tags at
          on at.artifact_id = a.id and at.family = 'role'
        where a.run_id = ? and a.status = 'available'
          and at.name in ('final', 'proxy', 'cover', 'qc')
        group by a.id
        order by a.id
        """,
        (run_id,),
    ).fetchall()
    grouped: dict[str, list[dict]] = {
        role: [] for role in HARD_REQUIRED_ROLES
    }
    for raw in rows:
        row = dict(raw)
        role_names = [
            name for name in str(row.pop("raw_role_names") or "").split(",")
            if name
        ]
        if len(role_names) != 1:
            _fail(
                "review_role_ambiguous",
                "one artifact has multiple hard review role tags",
            )
        role = role_names[0]
        if not review_artifact_matches_role(row, role):
            _fail("review_role_invalid", f"{role} artifact does not match its raw role")
        if producer_task_ids is not None and row.get("producing_task_id") != producer_task_ids.get(role):
            continue
        grouped[role].append(row)
    for role, candidates in grouped.items():
        if len(candidates) != 1:
            _fail(
                "review_role_ambiguous",
                f"{role} must have exactly one available raw role artifact",
            )
    roles = {role: candidates[0] for role, candidates in grouped.items()}
    if len({row["id"] for row in roles.values()}) != len(roles):
        _fail("review_role_ambiguous", "hard review roles are not distinct")
    return roles


def _current_qc_script_hashes(source_root_value: object) -> dict[str, str]:
    source_root = _lexical_absolute_path(
        source_root_value, label="canonical Reel source root")
    paths = {
        "qc_reel5_sh": os.environ.get(
            "CASTLE_REEL_QC_SCRIPT", str(source_root / "qc_reel5.sh")),
        "qc_reel5_py": os.environ.get(
            "CASTLE_REEL_QC_PY", str(source_root / "qc_reel5.py")),
    }
    result = {}
    for name, value in paths.items():
        path = _lexical_absolute_path(value, label=name)
        _, digest = _nofollow_file_identity(path, label=name)
        result[name] = digest
    return result


def _validate_release_authority_files(
    source_root_value: object,
    authority_files: dict,
) -> None:
    source_root = _lexical_absolute_path(
        source_root_value, label="canonical Reel source root")
    for relative, digest in authority_files.items():
        relative_path = PurePosixPath(str(relative))
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            _fail("qc_binding_mismatch", "QC release authority path is unsafe")
        _verified_lexical_file(
            Path(str(source_root), *relative_path.parts),
            label=f"QC release authority {relative}",
            expected_digest=str(digest),
        )


def _verify_reel_candidate(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    graph_builder: Callable[[dict], dict] = reel_m4_graph,
    video_probe: Callable[[Path], dict] = _ffprobe_video,
    reject_catastrophic_qc: bool,
    eligible_statuses: frozenset[str],
    allowed_gate_states: frozenset[str],
    snapshot_root: Path,
) -> dict:
    """Verify one immutable, current Reel run without writing state."""
    run_id = str(run_id or "").strip()
    if RUN_ID_RE.fullmatch(run_id) is None:
        _fail("invalid_run_id", "run id is invalid")
    run_row = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
    if run_row is None:
        _fail("run_not_found", "workflow run was not found")
    run = dict(run_row)
    if run.get("workflow_type") != "ai_sister.reel_pipeline":
        _fail("wrong_workflow", "run is not an AI-Sister Reel pipeline")
    if run.get("status") not in eligible_statuses:
        _fail("wrong_status", "run has not reached a review-ready terminal state")
    run_input = json_loads(run.get("input_json"), None)
    if not isinstance(run_input, dict):
        _fail("run_input_invalid", "run input is invalid")
    episode = str(run_input.get("episode") or run_input.get("reel_id") or "").strip()
    if not episode.isdigit():
        _fail("run_input_invalid", "run episode is invalid")
    if str(run_input.get("preset") or "").strip() != "single-rerun":
        _fail("wrong_preset", "release qualification requires the full-quality preset")
    approved_script_review_only = (
        isinstance(run_input.get("approved_script"), dict)
        and str(run_input.get("release_mode") or "").strip() == "review_only"
    )
    expected_task_keys = (
        EXPECTED_APPROVED_SCRIPT_TASK_KEYS
        if approved_script_review_only else EXPECTED_TASK_KEYS
    )

    try:
        graph = graph_builder(dict(run_input))
    except ValueError as error:
        raise ReelReleaseGateError(
            "stale_run_graph", "run input is not accepted by the current canonical Reel graph") from error
    except Exception as error:
        raise ReelReleaseGateError(
            "current_graph_unavailable", "current canonical Reel graph could not be built") from error
    graph_tasks = graph.get("tasks")
    if not isinstance(graph_tasks, list) or len(graph_tasks) != len(expected_task_keys):
        _fail(
            "canonical_topology_invalid",
            f"current canonical Reel graph must have exactly {len(expected_task_keys)} nodes",
        )
    graph_keys = [str(task.get("key") or "") for task in graph_tasks if isinstance(task, dict)]
    if len(graph_keys) != len(graph_tasks) or len(set(graph_keys)) != len(graph_keys):
        _fail("canonical_topology_invalid", "current canonical Reel graph task keys are invalid or duplicated")
    expected_specs = {key: task for key, task in zip(graph_keys, graph_tasks)}
    if set(expected_specs) != expected_task_keys:
        _fail("canonical_topology_invalid", "current canonical Reel graph is not the required Reel graph")
    task_rows = conn.execute(
        "select * from tasks where run_id = ? order by created_at, id",
        (run_id,),
    ).fetchall()
    task_keys = [str(row["key"]) for row in task_rows]
    if len(set(task_keys)) != len(task_keys):
        _fail("topology_mismatch", "executed task set contains duplicate keys")
    tasks_by_key = {str(row["key"]): dict(row) for row in task_rows}
    _restore_run_bound_graph_inputs(
        expected_specs,
        tasks_by_key,
        episode=episode,
    )
    expected_run_hash = input_hash({
        "workflow_type": "ai_sister.reel_pipeline",
        "input": run_input,
        "graph": graph,
    })
    if str(run.get("input_hash") or "") != expected_run_hash:
        _fail("stale_run_graph", "run was not created from the current canonical graph and implementations")
    expected_version_id = workflow_version_id(
        "ai_sister.reel_pipeline", graph)
    if run.get("workflow_version_id") != expected_version_id:
        _fail(
            "stale_run_graph",
            "run workflow version is not the current canonical Reel version",
        )
    version_row = conn.execute(
        "select * from workflow_versions where id = ?", (expected_version_id,),
    ).fetchone()
    stored_topology = (
        json_loads(version_row["task_graph_json"], None)
        if version_row is not None else None
    )
    if version_row is None or (
        stored_topology != workflow_topology_graph(graph)
        and not approved_script_review_only
    ):
        _fail(
            "stale_run_graph",
            "stored workflow topology differs from the current canonical graph",
        )

    actual_task_keys = set(tasks_by_key)
    missing_task_keys = expected_task_keys - actual_task_keys
    extra_task_keys = actual_task_keys - expected_task_keys
    ignored_extra_task_keys = frozenset()
    if missing_task_keys:
        _fail("topology_mismatch", "executed task set differs from the canonical Reel graph")
    if extra_task_keys:
        if (
            approved_script_review_only
            and extra_task_keys <= TOLERATED_REVIEW_ONLY_EXTRA_TASK_KEYS
        ):
            ignored_extra_task_keys = frozenset(extra_task_keys)
        else:
            _fail("topology_mismatch", "executed task set differs from the canonical Reel graph")
    foreign_artifact = conn.execute(
        """
        select a.id
        from artifacts a
        join tasks t on t.id = a.producing_task_id
        where t.run_id = ? and (a.run_id is null or a.run_id != ?)
        limit 1
        """,
        (run_id, run_id),
    ).fetchone()
    if foreign_artifact is not None:
        _fail(
            "artifact_lineage_mismatch",
            "current Reel task is recorded as producer of a foreign-run artifact",
        )
    actual_dependencies = _task_dependencies(conn, run_id)
    artifact_file_cache: dict[tuple[str, bool], Path] = {}
    materialized_inputs: dict[str, dict] = {}
    for key in sorted(expected_task_keys):
        actual = tasks_by_key[key]
        expected = expected_specs[key]
        if actual.get("task_type") != expected.get("task_type") or actual.get("lane") != expected.get("lane"):
            _fail("task_contract_mismatch", f"{key} task type or lane differs from the canonical graph")
        if int(actual.get("max_attempts") or 0) != int(expected.get("max_attempts") or 1):
            _fail("task_contract_mismatch", f"{key} retry contract differs from the canonical graph")
        if str(actual.get("reuse_policy") or "off") != str(expected.get("reuse_policy") or "off"):
            _fail("task_contract_mismatch", f"{key} reuse contract differs from the canonical graph")
        if actual_dependencies.get(key, set()) != set(expected.get("deps") or []):
            _fail("task_contract_mismatch", f"{key} dependencies differ from the canonical graph")
        materialized_inputs[key] = _validate_task_materialization(
            conn,
            run,
            actual,
            expected,
            tasks_by_key,
            artifact_file_cache,
            snapshot_root,
        )
        if actual.get("status") != "completed":
            _fail("task_not_completed", f"{key} did not complete")
        if actual.get("reused_from_task_id"):
            reused_outputs = conn.execute(
                """
                select a.source_type
                from task_outputs o
                join artifacts a on a.id = o.artifact_id
                where o.task_id = ?
                """,
                (actual["id"],),
            ).fetchall()
            if (
                key not in HASH_PINNED_REUSE_TASK_KEYS
                or str(actual.get("reuse_policy") or "off") != "input_hash"
                or not reused_outputs
                or any(row["source_type"] != "reused" for row in reused_outputs)
            ):
                _fail("task_reused", f"{key} reused a previous task without a canonical cache chain")
        attempt = actual.get("attempt")
        if type(attempt) is not int or attempt < 0 or attempt >= int(actual["max_attempts"]):
            _fail("task_contract_mismatch", f"{key} attempt counter is outside its retry contract")

    output_rows_for_run = conn.execute(
        """
        select o.task_id, o.name, o.artifact_id,
               t.run_id as task_run_id,
               a.run_id as artifact_run_id,
               a.producing_task_id as artifact_producing_task_id
        from task_outputs o
        join tasks t on t.id = o.task_id
        left join artifacts a on a.id = o.artifact_id
        where t.run_id = ?
        order by o.task_id, o.name, o.artifact_id
        """,
        (run_id,),
    ).fetchall()
    output_names: set[tuple[str, str]] = set()
    for row in output_rows_for_run:
        pair = (str(row["task_id"]), str(row["name"]))
        if (
            row["artifact_run_id"] != run_id
            or row["artifact_producing_task_id"] != row["task_id"]
        ):
            _fail(
                "artifact_lineage_mismatch",
                "current Reel task output crosses run or producer lineage",
            )
        if pair in output_names:
            _fail(
                "artifact_lineage_mismatch",
                "current Reel task has more than one artifact for an output name",
            )
        output_names.add(pair)

    ignored_extra_task_ids = {
        str(tasks_by_key[key]["id"]) for key in ignored_extra_task_keys
    }
    orphan_role = conn.execute(
        """
        select a.id
        from artifacts a
        join artifact_tags tag
          on tag.artifact_id = a.id
         and tag.family = 'role'
        where a.run_id = ?
          and a.producing_task_id is null
          and tag.name in ('final', 'proxy', 'cover', 'qc')
        limit 1
        """,
        (run_id,),
    ).fetchone()
    if orphan_role is not None:
        _fail(
            "artifact_lineage_mismatch",
            "review role artifact has no canonical producer task",
        )
    artifact_rows = conn.execute(
        """
        select distinct a.*
        from artifacts a
        join task_outputs o on o.artifact_id = a.id
        join tasks t on t.id = o.task_id
        where a.run_id = ?
          and t.run_id = ?
          and a.producing_task_id = t.id
        order by a.created_at, a.id
        """,
        (run_id, run_id),
    ).fetchall()
    artifact_rows = [
        row for row in artifact_rows
        if str(row["producing_task_id"] or "") not in ignored_extra_task_ids
    ]
    for artifact_row in artifact_rows:
        artifact = dict(artifact_row)
        metadata = json_loads(artifact.get("metadata_json"), {})
        producer_id = artifact.get("producing_task_id")
        producer = next(
            (task for task in tasks_by_key.values() if task["id"] == producer_id), None)
        output_rows = conn.execute(
            "select task_id, name, artifact_id from task_outputs where artifact_id = ?",
            (artifact["id"],),
        ).fetchall()
        output_name = str(output_rows[0]["name"]) if len(output_rows) == 1 else ""
        reuse_allowed = _reuse_source_chain_is_valid(
            conn,
            artifact,
            metadata,
            producer,
            output_name=output_name,
        )
        if (
            artifact.get("source_type") != "generated"
            or _metadata_indicates_reuse(metadata)
        ) and not reuse_allowed:
            _fail("artifact_reused", "run contains an artifact reused from an earlier run")
        if producer is None or artifact.get("input_hash") != producer.get("input_hash"):
            _fail("artifact_lineage_mismatch", "run artifact is not bound to its canonical task input")
        if (
            len(output_rows) != 1
            or output_rows[0]["task_id"] != producer_id
            or output_rows[0]["artifact_id"] != artifact["id"]
        ):
            _fail("artifact_lineage_mismatch", "run artifact has missing or ambiguous task output lineage")

    canonical_role_producer_task_ids = {
        role: str(tasks_by_key[producer_key]["id"])
        for role, producer_key in ROLE_PRODUCER_KEYS.items()
    }
    roles = _raw_review_roles(
        conn,
        run_id,
        producer_task_ids=canonical_role_producer_task_ids,
    )

    role_files: dict[str, Path] = {}
    role_metadata: dict[str, dict] = {}
    role_report: dict[str, dict] = {}
    for role in sorted(HARD_REQUIRED_ROLES):
        row = roles[role]
        if row.get("gate_state") not in allowed_gate_states:
            _fail("review_role_blocked", f"{role} has a blocking review decision")
        expected_task = tasks_by_key[ROLE_PRODUCER_KEYS[role]]
        if row.get("producing_task_id") != expected_task.get("id"):
            _fail("review_role_producer", f"{role} was not produced by the canonical task")
        role_metadata_for_reuse = json_loads(row.get("metadata_json"), {})
        if row.get("source_type") != "generated" and not _reuse_source_chain_is_valid(
            conn,
            row,
            role_metadata_for_reuse,
            expected_task,
            output_name=ROLE_OUTPUT_NAMES[role],
        ):
            _fail("artifact_reused", f"{role} was not freshly generated")
        path, metadata = _verified_role_file(
            conn, role, row, snapshot_root=snapshot_root)
        canonical_output_rows = conn.execute(
            """
            select artifact_id from task_outputs
            where task_id = ? and name = ?
            """,
            (expected_task["id"], ROLE_OUTPUT_NAMES[role]),
        ).fetchall()
        if (
            len(canonical_output_rows) != 1
            or canonical_output_rows[0]["artifact_id"] != row["id"]
        ):
            _fail(
                "review_role_producer",
                f"{role} canonical producer/output is missing or ambiguous",
            )
        output_rows = conn.execute(
            "select task_id, name from task_outputs where artifact_id = ?",
            (row["id"],),
        ).fetchall()
        if (
            len(output_rows) != 1
            or output_rows[0]["task_id"] != expected_task["id"]
            or output_rows[0]["name"] != ROLE_OUTPUT_NAMES[role]
        ):
            _fail("review_role_producer", f"{role} has the wrong canonical task output")
        if metadata.get("workflow") != ROLE_WORKFLOWS[role] or metadata.get("role") != role:
            _fail("artifact_metadata_invalid", f"{role} artifact metadata producer is invalid")
        role_files[role] = path
        role_metadata[role] = metadata
        role_report[role] = {
            "artifact_id": row["id"],
            "sha256": row["content_hash"],
            "bytes": path.stat().st_size,
        }

    final_size = role_files["final"].stat().st_size
    proxy_size = role_files["proxy"].stat().st_size
    if final_size < MIN_VIDEO_BYTES:
        _fail("final_too_small", "final video is too small to be a real Reel")
    if proxy_size < MIN_VIDEO_BYTES or proxy_size > MAX_PROXY_BYTES:
        _fail("proxy_size_invalid", "review proxy is outside the accepted size range")
    if proxy_size >= final_size:
        _fail("proxy_not_compressed", "review proxy is not smaller than the Castle HQ final")
    try:
        final_probe = video_probe(role_files["final"])
        proxy_probe = video_probe(role_files["proxy"])
    except ReelReleaseGateError:
        raise
    except Exception as error:
        raise ReelReleaseGateError("video_probe_failed", "review video probe failed") from error
    normalized_probes = {}
    for role, probe in (("final", final_probe), ("proxy", proxy_probe)):
        try:
            normalized = {
                "duration_seconds": round(float(probe.get("duration_seconds") or 0), 3),
                "width": int(probe.get("width") or 0),
                "height": int(probe.get("height") or 0),
                "has_audio": probe.get("has_audio") is True,
                "video_codec": str(probe.get("video_codec") or ""),
                "audio_codec": str(probe.get("audio_codec") or ""),
                "audio_bit_rate": int(probe.get("audio_bit_rate") or 0),
                "pix_fmt": str(probe.get("pix_fmt") or ""),
                "format_name": str(probe.get("format_name") or ""),
                "video_stream_count": int(probe.get("video_stream_count") or 0),
                "audio_stream_count": int(probe.get("audio_stream_count") or 0),
            }
        except (AttributeError, TypeError, ValueError, OverflowError) as error:
            raise ReelReleaseGateError("video_probe_failed", f"{role} video probe is invalid") from error
        if (
            not math.isfinite(normalized["duration_seconds"])
            or normalized["duration_seconds"] <= 1.0
            or normalized["width"] <= 0
            or normalized["height"] <= 0
            or not normalized["has_audio"]
        ):
            _fail("video_probe_failed", f"{role} video probe is invalid")
        normalized_probes[role] = normalized
    final_probe = normalized_probes["final"]
    proxy_probe = normalized_probes["proxy"]
    proxy_contract_info = canonical_proxy_stream_info({
        "format": {
            "format_name": proxy_probe["format_name"],
            "duration": proxy_probe["duration_seconds"],
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": proxy_probe["video_codec"],
                "pix_fmt": proxy_probe["pix_fmt"],
                "width": proxy_probe["width"],
                "height": proxy_probe["height"],
            }
            for _ in range(proxy_probe["video_stream_count"])
        ] + [
            {
                "codec_type": "audio",
                "codec_name": proxy_probe["audio_codec"],
                "bit_rate": proxy_probe["audio_bit_rate"],
            }
            for _ in range(proxy_probe["audio_stream_count"])
        ],
    })
    if proxy_contract_info is None:
        _fail(
            "proxy_not_mobile",
            "review proxy is not canonical MP4 H.264/AAC yuv420p media",
        )
    if not canonical_proxy_derivative_matches(
        {
            "width": final_probe["width"],
            "height": final_probe["height"],
            "duration": final_probe["duration_seconds"],
        },
        proxy_contract_info,
    ):
        _fail(
            "proxy_derivative_mismatch",
            "review proxy orientation, aspect, or duration differs from the HQ final",
        )
    if not canonical_proxy_has_faststart(role_files["proxy"]):
        _fail("proxy_not_faststart", "review proxy is not optimized for mobile streaming")
    role_report["final"].update(final_probe)
    role_report["proxy"].update(proxy_probe)

    proxy_metadata = role_metadata["proxy"]
    proxy_implementation_hash = str(
        expected_specs["review_proxy"]["input"].get("implementation_hash") or "")
    if not canonical_proxy_metadata_matches(
        proxy_metadata,
        str(roles["final"].get("id") or ""),
        str(roles["final"].get("content_hash") or ""),
        implementation_hash=proxy_implementation_hash,
    ):
        _fail("proxy_binding_mismatch", "review proxy is not bound to the exact HQ final")

    cover_size = role_files["cover"].stat().st_size
    if not MIN_COVER_BYTES <= cover_size <= MAX_COVER_BYTES or not _cover_magic_is_valid(role_files["cover"]):
        _fail("cover_invalid", "cover bytes do not satisfy the review transport contract")

    qc_path = role_files["qc"]
    if qc_path.stat().st_size <= 0 or qc_path.stat().st_size > MAX_QC_BYTES:
        _fail("qc_invalid", "QC report size is invalid")
    try:
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReelReleaseGateError("qc_invalid", "QC report is not valid JSON") from error
    if not isinstance(qc, dict):
        _fail("qc_invalid", "QC report is not an object")
    qc_task_input = materialized_inputs["qc"]
    try:
        derived_verdict = reel_qc_checks_verdict(
            qc.get("checks"),
            audio_only=qc_task_input.get("audio_only") is True,
            skip_vision=qc_task_input.get("skip_vision") is True,
        )
    except ValueError as error:
        raise ReelReleaseGateError("qc_invalid", str(error)) from error
    if str(qc.get("verdict") or "").upper() != derived_verdict:
        _fail("qc_not_pass", "automatic QC verdict differs from exact check levels")
    try:
        quality_assessment = validate_quality_assessment(
            qc.get("quality_assessment"))
    except ValueError as error:
        raise ReelReleaseGateError("qc_invalid", str(error)) from error
    if quality_assessment["verdict"] != derived_verdict:
        _fail("qc_not_pass", "automatic QC verdict differs from its assessment")
    if reject_catastrophic_qc and derived_verdict == "FAIL":
        _fail("qc_not_pass", "automatic QC found a catastrophic defect")
    expected_completion = canonical_draft_completion(derived_verdict)
    final_id = str(roles["final"]["id"])
    final_hash = str(roles["final"]["content_hash"])
    quality_input = qc.get("quality_input")
    qc_metadata = role_metadata["qc"]
    qc_input_hash = str(qc.get("input_hash") or "")
    qc_final_input = (qc_metadata.get("inputs") or {}).get("final")
    source_root = expected_specs["runtime_assets"]["input"].get("source_root")
    current_script_hashes = _current_qc_script_hashes(source_root)
    if qc_metadata.get("script_hashes") != current_script_hashes:
        _fail("qc_binding_mismatch", "QC script hashes are not current")
    try:
        expected_qc_input_hash = qc_evidence_input_hash(
            qc_task_input,
            qc_reel5_sh_hash=current_script_hashes["qc_reel5_sh"],
            qc_reel5_py_hash=current_script_hashes["qc_reel5_py"],
        )
    except ValueError as error:
        raise ReelReleaseGateError("qc_binding_mismatch", str(error)) from error
    orchestrator_qc_hash = str(tasks_by_key["qc"].get("input_hash") or "")
    qc_release_authority = qc.get("release_authority")
    qc_theme = str(qc_task_input.get("theme") or "")
    raw_cost = qc.get("cost")
    try:
        expected_qc_cost = canonical_qc_cost(
            audio_only=qc_task_input.get("audio_only") is True,
            skip_vision=qc_task_input.get("skip_vision") is True,
            request_count=(
                raw_cost.get("request_count")
                if isinstance(raw_cost, dict) else None
            ),
        )
    except ValueError as error:
        raise ReelReleaseGateError("qc_binding_mismatch", str(error)) from error
    if not canonical_release_authority_matches(
        qc_release_authority,
        episode=episode,
        theme=qc_theme,
        reel_aud=str(qc_task_input.get("reel_aud") or ""),
        host_aud=str(qc_task_input.get("host_aud") or ""),
    ):
        _fail("qc_binding_mismatch", "QC release authority is incomplete or noncanonical")
    if qc_metadata.get("release_authority") != qc_release_authority:
        _fail("qc_binding_mismatch", "QC report and metadata release authorities differ")
    cover_authority = qc_release_authority["cover"]
    try:
        validate_cover_audit(
            cover_authority.get("cover_audit"),
            expected_final_sha256=str(
                (qc_task_input.get("cover") or {}).get("content_hash") or ""),
            expected_metadata_sha256=str(
                (qc_task_input.get("cover_kv") or {}).get("content_hash") or ""),
            expected_authority_sha256=str(
                (qc_task_input.get("cover_authority") or {}).get("content_hash") or ""),
        )
    except (TypeError, ValueError) as error:
        raise ReelReleaseGateError(
            "qc_binding_mismatch", "QC cover release authority is invalid") from error
    _validate_release_authority_files(
        source_root, qc_release_authority["authority_files"])

    qc_payload_refs = {
        key: value for key, value in qc_task_input.items()
        if isinstance(value, dict) and value.get("kind") in {"artifact", "path"}
    }
    metadata_inputs = qc_metadata.get("inputs")
    if not isinstance(metadata_inputs, dict) or set(metadata_inputs) != set(qc_payload_refs):
        _fail("qc_binding_mismatch", "QC metadata input projection is incomplete")
    for field, expected_ref in qc_payload_refs.items():
        actual_ref = metadata_inputs.get(field)
        required_fields = {"kind", "content_hash"}
        if expected_ref.get("kind") == "artifact":
            required_fields.add("artifact_id")
        if (
            not isinstance(actual_ref, dict)
            or any(actual_ref.get(name) != expected_ref.get(name)
                   for name in required_fields)
            or not set(actual_ref).issubset(required_fields | {"basename"})
        ):
            _fail("qc_binding_mismatch", f"QC metadata input {field} differs")
    if (
        str(qc_metadata.get("verdict") or "").upper() != derived_verdict
        or qc.get("draft_completion") != expected_completion
        or qc_metadata.get("draft_completion") != expected_completion
        or qc_metadata.get("review_required")
        is not expected_completion["review_required"]
        or qc_metadata.get("release_eligible")
        is not expected_completion["release_eligible"]
        or qc.get("episode") != episode
        or qc.get("debate_id") != episode
        or qc.get("theme") != qc_theme
        or qc.get("source_content_hash") != final_hash
        or qc_metadata.get("source_content_hash") != final_hash
        or qc_input_hash != expected_qc_input_hash
        or qc_metadata.get("input_hash") != qc_input_hash
        or qc.get("orchestrator_input_hash") != orchestrator_qc_hash
        or qc_metadata.get("orchestrator_input_hash") != orchestrator_qc_hash
        or qc.get("task_schema_version") != QC_TASK_SCHEMA_VERSION
        or qc_metadata.get("task_schema_version") != QC_TASK_SCHEMA_VERSION
        or raw_cost != expected_qc_cost
        or qc_metadata.get("cost") != expected_qc_cost
        or qc_metadata.get("implementation_hash")
        != expected_specs["qc"]["input"].get("implementation_hash")
        or quality_assessment.get("catastrophic_defects") != []
        or qc_metadata.get("quality_assessment") != quality_assessment
        or not isinstance(quality_input, dict)
        or quality_input.get("schema_version")
        != "reel-final-media-integrity-v1"
        or quality_input.get("final_mp4_sha256") != final_hash
        or qc_metadata.get("quality_input") != quality_input
        or qc.get("qc_quality_authority") != QC_QUALITY_AUTHORITY
        or qc_metadata.get("qc_quality_authority") != QC_QUALITY_AUTHORITY
        or qc.get("qc_quality_authority_fingerprint")
        != QC_QUALITY_AUTHORITY_FINGERPRINT
        or qc_metadata.get("qc_quality_authority_fingerprint")
        != QC_QUALITY_AUTHORITY_FINGERPRINT
        or qc.get("qc_judge_authority") != QC_JUDGE_AUTHORITY
        or qc_metadata.get("qc_judge_authority") != QC_JUDGE_AUTHORITY
        or qc.get("qc_judge_authority_fingerprint")
        != QC_JUDGE_AUTHORITY_FINGERPRINT
        or qc_metadata.get("qc_judge_authority_fingerprint")
        != QC_JUDGE_AUTHORITY_FINGERPRINT
        or qc.get("qc_vision_authority") != QC_VISION_AUTHORITY
        or qc_metadata.get("qc_vision_authority") != QC_VISION_AUTHORITY
        or qc.get("qc_vision_authority_fingerprint")
        != QC_VISION_AUTHORITY_FINGERPRINT
        or qc_metadata.get("qc_vision_authority_fingerprint")
        != QC_VISION_AUTHORITY_FINGERPRINT
        or not isinstance(qc_final_input, dict)
        or qc_final_input.get("kind") != "artifact"
        or qc_final_input.get("artifact_id") != final_id
        or qc_final_input.get("content_hash") != final_hash
    ):
        _fail("qc_binding_mismatch", "QC PASS is not bound to the exact final/current authority")
    role_report["qc"]["verdict"] = derived_verdict

    generation = hashlib.sha256(json.dumps([
        [role, roles[role]["id"], roles[role]["content_hash"]]
        for role in sorted(HARD_REQUIRED_ROLES)
    ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    review_result = {
        "run_status": run["status"],
        "status": run["status"],
        "missing_roles": [],
        "required_roles": sorted(HARD_REQUIRED_ROLES),
        "transport_generation": generation,
    }
    if not review_is_ready_for_remote_sync(review_result):
        _fail("review_sync_not_ready", "review bundle is not eligible for remote sync")

    attempts = {
        key: int(tasks_by_key[key].get("attempt") or 0)
        for key in sorted(expected_task_keys)
        if int(tasks_by_key[key].get("attempt") or 0) > 0
    }
    return {
        "schema_version": RELEASE_GATE_SCHEMA_VERSION,
        "ok": True,
        "run_id": run_id,
        "episode": episode,
        "preset": "single-rerun",
        "status": run["status"],
        "workflow_version_id": run["workflow_version_id"],
        "run_input_hash": expected_run_hash,
        "canonical_graph_hash": input_hash(graph),
        "task_count": len(expected_task_keys),
        "ignored_extra_tasks": sorted(ignored_extra_task_keys),
        "retried_tasks": attempts,
        "transport_generation": generation,
        "review": role_report,
    }


def _verify_with_immutable_role_snapshots(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    graph_builder: Callable[[dict], dict],
    video_probe: Callable[[Path], dict],
    reject_catastrophic_qc: bool,
    eligible_statuses: frozenset[str],
    allowed_gate_states: frozenset[str],
) -> dict:
    # ffprobe and JSON parsing reopen pathnames. Copy each already hash-bound
    # hard role from one O_NOFOLLOW descriptor into this private directory so a
    # concurrent compatibility-path replacement cannot change the bytes being
    # qualified after identity verification.
    owns_snapshot = not conn.in_transaction
    if owns_snapshot:
        conn.execute("BEGIN")
    try:
        with tempfile.TemporaryDirectory(prefix="castle-reel-release-") as raw:
            root = Path(raw)
            os.chmod(root, 0o700)
            return _verify_reel_candidate(
                conn,
                run_id,
                graph_builder=graph_builder,
                video_probe=video_probe,
                reject_catastrophic_qc=reject_catastrophic_qc,
                eligible_statuses=eligible_statuses,
                allowed_gate_states=allowed_gate_states,
                snapshot_root=root,
            )
    finally:
        if owns_snapshot:
            conn.rollback()


def verify_reel_release_candidate(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    graph_builder: Callable[[dict], dict] = reel_m4_graph,
    video_probe: Callable[[Path], dict] = _ffprobe_video,
) -> dict:
    """Require current immutable evidence with no catastrophic QC defect."""
    return _verify_with_immutable_role_snapshots(
        conn,
        run_id,
        graph_builder=graph_builder,
        video_probe=video_probe,
        reject_catastrophic_qc=True,
        eligible_statuses=ELIGIBLE_RUN_STATUSES,
        allowed_gate_states=frozenset({"pending", "approved"}),
    )


def verify_reel_review_candidate(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    graph_builder: Callable[[dict], dict] = reel_m4_graph,
    video_probe: Callable[[Path], dict] = _ffprobe_video,
) -> dict:
    """Require current evidence while retaining PASS/WARN/FAIL for review."""
    return _verify_with_immutable_role_snapshots(
        conn,
        run_id,
        graph_builder=graph_builder,
        video_probe=video_probe,
        reject_catastrophic_qc=False,
        eligible_statuses=REVIEW_ELIGIBLE_RUN_STATUSES,
        allowed_gate_states=frozenset({"pending", "approved", "needs_review"}),
    )
