from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from .config import Settings, ensure_dirs, settings_from_env
from .cover_audit import validate_cover_audit
from .db import json_loads, stable_json, utc_ts
from .events import add_event


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
PROJECT_REUSE_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def artifact_id() -> str:
    return f"art_{uuid.uuid4().hex[:20]}"


def safe_filename(name: str, fallback: str = "artifact.bin") -> str:
    name = Path(str(name or "")).name.strip()
    if not name:
        name = fallback
    name = SAFE_NAME_RE.sub("_", name)
    name = name.strip("._")
    return name or fallback


def content_kind(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0] or ""
    if guessed.startswith("image/"):
        return "image"
    if guessed.startswith("video/"):
        return "video"
    if guessed.startswith("audio/"):
        return "audio"
    if guessed in ("application/json", "text/json"):
        return "json"
    if guessed.startswith("text/"):
        return "text"
    return "file"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def default_volume(conn: sqlite3.Connection) -> dict:
    row = conn.execute("select * from volumes where id = ?", ("local-local-workstation",)).fetchone()
    if row is None:
        settings = ensure_dirs(settings_from_env())
        now = utc_ts()
        conn.execute(
            "insert into volumes(id, base_path, kind, created_at) values (?, ?, ?, ?)",
            ("local-local-workstation", str(settings.artifact_root.resolve()), "local", now),
        )
        return {"id": "local-local-workstation", "base_path": str(settings.artifact_root.resolve()), "kind": "local"}
    return dict(row)


def artifact_abs_path(conn: sqlite3.Connection, artifact_row: sqlite3.Row | dict) -> Path | None:
    row = dict(artifact_row)
    volume_id = row.get("volume_id")
    rel = row.get("relative_path")
    if not volume_id or not rel:
        return None
    vol = conn.execute("select * from volumes where id = ?", (volume_id,)).fetchone()
    if vol is None:
        return None
    base = Path(vol["base_path"]).resolve()
    target = (base / rel).resolve()
    try:
        if not target.is_relative_to(base):
            return None
    except AttributeError:
        if os.path.commonpath([str(base), str(target)]) != str(base):
            return None
    return target


def _approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:20]}"


def _clean_tag_value(value, *, max_len: int = 80) -> str:
    return str(value or "").strip()[:max_len]


def normalize_tags(tags: list[dict] | list[str] | None) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for tag in tags or []:
        family = ""
        name = ""
        if isinstance(tag, dict):
            family = _clean_tag_value(tag.get("family") or tag.get("type") or "tag")
            name = _clean_tag_value(tag.get("name") or tag.get("value") or tag.get("tag"))
        else:
            raw = _clean_tag_value(tag)
            if ":" in raw:
                family, name = [_clean_tag_value(part) for part in raw.split(":", 1)]
            else:
                family, name = "tag", raw
        if not family or not name:
            continue
        key = (family, name)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"family": family, "name": name})
    return normalized


def artifact_tags(conn: sqlite3.Connection, artifact_id: str) -> list[dict]:
    rows = conn.execute(
        "select family, name from artifact_tags where artifact_id = ? order by family, name",
        (artifact_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_approval(conn: sqlite3.Connection, artifact_id: str) -> dict | None:
    row = conn.execute(
        "select * from approvals where artifact_id = ? order by created_at desc limit 1",
        (artifact_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def artifact_public(row: sqlite3.Row | dict, conn: sqlite3.Connection | None = None) -> dict:
    item = dict(row)
    item["metadata"] = json_loads(item.pop("metadata_json", None), {})
    item["source_meta"] = json_loads(item.pop("source_meta_json", None), {})
    item["inline"] = json_loads(item.pop("inline_json", None), None)
    if item.get("volume_id") and item.get("relative_path"):
        item["uri"] = f"castle://{item['volume_id']}/{item['relative_path']}"
        item["file_url"] = f"/api/orchestrator/artifacts/{item['id']}/file"
    if conn is not None and item.get("id"):
        item["tags"] = artifact_tags(conn, item["id"])
        item["latest_approval"] = latest_approval(conn, item["id"])
    return item


def _require_artifact(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
    row = conn.execute("select * from artifacts where id = ?", (artifact_id,)).fetchone()
    if row is None:
        raise KeyError("Artifact not found")
    return row


def _upsert_tags(conn: sqlite3.Connection, artifact_id: str, tags: list[dict] | list[str] | None, now: float) -> list[dict]:
    normalized = normalize_tags(tags)
    for tag in normalized:
        conn.execute(
            "insert or ignore into tag_registry(family, name, description, created_at) values (?, ?, null, ?)",
            (tag["family"], tag["name"], now),
        )
        conn.execute(
            "insert or ignore into artifact_tags(artifact_id, family, name) values (?, ?, ?)",
            (artifact_id, tag["family"], tag["name"]),
        )
    return normalized


def set_artifact_tags(
    conn: sqlite3.Connection,
    artifact_id: str,
    tags: list[dict] | list[str] | None,
    *,
    replace: bool = False,
) -> dict:
    row = _require_artifact(conn, artifact_id)
    now = utc_ts()
    if replace:
        conn.execute("delete from artifact_tags where artifact_id = ?", (artifact_id,))
    applied = _upsert_tags(conn, artifact_id, tags, now)
    conn.execute("update artifacts set updated_at = ? where id = ?", (now, artifact_id))
    add_event(
        conn,
        "artifact.tags_updated",
        "Artifact tags updated",
        run_id=row["run_id"],
        artifact_id=artifact_id,
        data={"replace": replace, "tags": applied},
    )
    updated = _require_artifact(conn, artifact_id)
    return artifact_public(updated, conn)


def update_artifact_metadata(
    conn: sqlite3.Connection,
    artifact_id: str,
    metadata: dict | None,
    *,
    replace: bool = False,
) -> dict:
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    row = _require_artifact(conn, artifact_id)
    current = json_loads(row["metadata_json"], {}) or {}
    if not isinstance(current, dict):
        current = {}
    next_metadata = dict(metadata) if replace else {**current, **metadata}
    now = utc_ts()
    conn.execute(
        "update artifacts set metadata_json = ?, updated_at = ? where id = ?",
        (stable_json(next_metadata), now, artifact_id),
    )
    add_event(
        conn,
        "artifact.metadata_updated",
        "Artifact metadata updated",
        run_id=row["run_id"],
        artifact_id=artifact_id,
        data={"replace": replace, "keys": sorted(str(key) for key in metadata.keys())},
    )
    updated = _require_artifact(conn, artifact_id)
    return artifact_public(updated, conn)


def set_artifact_gate_state(conn: sqlite3.Connection, artifact_id: str, gate_state: str) -> dict:
    allowed = {"not_required", "pending", "passed", "failed", "needs_review", "approved", "rejected"}
    state = str(gate_state or "").strip()
    if state not in allowed:
        raise ValueError(f"gate_state must be one of: {', '.join(sorted(allowed))}")
    row = _require_artifact(conn, artifact_id)
    now = utc_ts()
    conn.execute("update artifacts set gate_state = ?, updated_at = ? where id = ?", (state, now, artifact_id))
    add_event(
        conn,
        "artifact.gate_updated",
        f"Artifact gate state set to {state}",
        run_id=row["run_id"],
        artifact_id=artifact_id,
        data={"gate_state": state},
    )
    updated = _require_artifact(conn, artifact_id)
    return artifact_public(updated, conn)


def record_artifact_approval(
    conn: sqlite3.Connection,
    artifact_id: str,
    decision: str,
    *,
    actor: str = "operator",
    note: str | None = None,
    run_id: str | None = None,
) -> dict:
    allowed = {"approved", "rejected", "needs_work"}
    decision = str(decision or "").strip()
    if decision not in allowed:
        raise ValueError(f"decision must be one of: {', '.join(sorted(allowed))}")
    row = _require_artifact(conn, artifact_id)
    previous = latest_approval(conn, artifact_id)
    aid = _approval_id()
    now = utc_ts()
    target_run_id = run_id or row["run_id"]
    conn.execute(
        """
        insert into approvals(id, artifact_id, run_id, decision, actor, note, supersedes_id, created_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            artifact_id,
            target_run_id,
            decision,
            str(actor or "operator")[:120],
            str(note or "")[:1000] or None,
            previous["id"] if previous else None,
            now,
        ),
    )
    gate_state = {"approved": "approved", "rejected": "rejected", "needs_work": "needs_review"}[decision]
    conn.execute("update artifacts set gate_state = ?, updated_at = ? where id = ?", (gate_state, now, artifact_id))
    add_event(
        conn,
        f"artifact.{decision}",
        f"Artifact {decision}",
        run_id=target_run_id,
        artifact_id=artifact_id,
        data={"approval_id": aid, "decision": decision, "actor": str(actor or "operator")[:120]},
        level="warning" if decision != "approved" else "info",
    )
    updated = _require_artifact(conn, artifact_id)
    return artifact_public(updated, conn)


def _store_relpath(source_name: str, aid: str) -> Path:
    now = utc_ts()
    # Local time grouping is operational only; artifact id is the stable identity.
    day = __import__("datetime").datetime.fromtimestamp(now).strftime("%Y%m%d")
    return Path(day) / f"{aid}_{safe_filename(source_name)}"


def copy_into_store(source: Path, aid: str, settings: Settings | None = None, name: str | None = None) -> tuple[str, Path]:
    settings = ensure_dirs(settings or settings_from_env())
    rel = _store_relpath(name or source.name, aid)
    target = (settings.artifact_root / rel).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex[:8]}")
    shutil.copy2(source, tmp)
    os.replace(tmp, target)
    return rel.as_posix(), target


def adopt_artifact(
    conn: sqlite3.Connection,
    source_path: str | Path,
    *,
    run_id: str | None = None,
    artifact_type: str | None = None,
    metadata: dict | None = None,
    tags: list[dict] | None = None,
    copy: bool = True,
) -> dict:
    source = Path(source_path).expanduser().resolve()
    if not copy:
        raise ValueError("copy=false artifact adoption is disabled until trusted volumes are registered")
    if not source.is_file():
        raise ValueError("Artifact source file not found")
    try:
        with source.open("rb"):
            pass
    except OSError as error:
        raise ValueError(f"Artifact source is not readable: {error}") from error

    digest = sha256_file(source)
    existing = conn.execute(
        "select * from artifacts where content_hash = ? order by created_at asc limit 1",
        (digest,),
    ).fetchone()
    if existing is not None:
        if metadata:
            update_artifact_metadata(conn, existing["id"], metadata, replace=False)
        if tags:
            set_artifact_tags(conn, existing["id"], tags, replace=False)
        item = artifact_public(_require_artifact(conn, existing["id"]), conn)
        item["adopted"] = False
        return item

    aid = artifact_id()
    volume = default_volume(conn)
    rel, target = copy_into_store(source, aid)
    now = utc_ts()
    source_meta = {
        "source_name": source.name,
        "source_size": source.stat().st_size,
        "source_mtime": source.stat().st_mtime,
        "source_path_hash": path_fingerprint(source),
        "copied_into_store": bool(copy),
    }
    conn.execute(
        """
        insert into artifacts(
          id, run_id, producing_task_id, artifact_type, status, gate_state,
          volume_id, relative_path, inline_json, content_hash, input_hash,
          source_type, source_uri, source_meta_json, metadata_json, version,
          created_at, updated_at
        ) values (?, ?, null, ?, 'available', 'not_required', ?, ?, null, ?, null,
          'adopted', ?, ?, ?, 'v1', ?, ?)
        """,
        (
            aid,
            run_id,
            artifact_type or content_kind(target),
            volume["id"],
            rel,
            digest,
            f"local-path-sha256:{source_meta['source_path_hash']}",
            stable_json(source_meta),
            stable_json(metadata or {}),
            now,
            now,
        ),
    )
    _upsert_tags(conn, aid, tags, now)
    add_event(conn, "artifact.adopted", f"Adopted {source.name}", run_id=run_id, artifact_id=aid)
    item = conn.execute("select * from artifacts where id = ?", (aid,)).fetchone()
    public = artifact_public(item, conn)
    public["adopted"] = True
    return public


def commit_task_outputs(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    task_id: str,
    input_hash: str,
    outputs: list[dict],
    settings: Settings | None = None,
) -> list[dict]:
    committed = []
    volume = default_volume(conn)
    for index, output in enumerate(outputs):
        temp_path = Path(str(output.get("path") or "")).expanduser().resolve()
        if not temp_path.is_file():
            raise RuntimeError(f"Task output missing: {temp_path}")
        name = safe_filename(str(output.get("name") or temp_path.name), f"output-{index}.bin")
        aid = artifact_id()
        rel, final_path = copy_into_store(temp_path, aid, settings=settings, name=name)
        digest = sha256_file(final_path)
        now = utc_ts()
        artifact_type = str(output.get("artifact_type") or content_kind(final_path))
        metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
        conn.execute(
            """
            insert into artifacts(
              id, run_id, producing_task_id, artifact_type, status, gate_state,
              volume_id, relative_path, inline_json, content_hash, input_hash,
              source_type, source_uri, source_meta_json, metadata_json, version,
              created_at, updated_at
            ) values (?, ?, ?, ?, 'available', 'not_required', ?, ?, null, ?, ?,
              'generated', null, '{}', ?, 'v1', ?, ?)
            """,
            (
                aid,
                run_id,
                task_id,
                artifact_type,
                volume["id"],
                rel,
                digest,
                input_hash,
                stable_json(metadata),
                now,
                now,
            ),
        )
        out_name = str(output.get("output_name") or output.get("name") or f"output_{index}")
        conn.execute(
            "insert into task_outputs(task_id, name, artifact_id) values (?, ?, ?)",
            (task_id, out_name, aid),
        )
        if isinstance(output.get("tags"), list):
            _upsert_tags(conn, aid, output.get("tags"), now)
        add_event(conn, "artifact.created", f"Created artifact {name}", run_id=run_id, task_id=task_id, artifact_id=aid)
        item = conn.execute("select * from artifacts where id = ?", (aid,)).fetchone()
        committed.append(artifact_public(item, conn))
    return committed


def reusable_task_artifacts(
    conn: sqlite3.Connection,
    task_type: str,
    input_hash: str,
) -> list[sqlite3.Row]:
    """Return one complete, available output set for a semantic task input.

    M4 resolves bound artifact inputs only when a node becomes runnable, so the
    worker and the create-run path must share exactly one reuse lookup.  A
    partial/missing output set is never reusable.
    """
    latest = conn.execute(
        """
        select id from tasks
        where task_type = ?
          and input_hash = ?
          and status = 'completed'
        order by completed_at desc, created_at desc
        limit 1
        """,
        (task_type, input_hash),
    ).fetchone()
    if latest is None:
        return []
    total_outputs = conn.execute(
        "select count(*) as count from task_outputs where task_id = ?",
        (latest["id"],),
    ).fetchone()["count"]
    rows = conn.execute(
        """
        select a.*, o.task_id as source_task_id, o.name as output_name
        from task_outputs o
        join artifacts a on a.id = o.artifact_id
        where o.task_id = ?
          and a.status = 'available'
          and (a.volume_id is not null or a.inline_json is not null)
        order by a.created_at asc
        """,
        (latest["id"],),
    ).fetchall()
    if len(rows) != total_outputs:
        return []
    for row in rows:
        if row["volume_id"] and row["relative_path"]:
            path = artifact_abs_path(conn, row)
            if path is None or not path.is_file():
                return []
    if task_type.startswith("castle.project_") and not _reusable_project_artifacts(
        conn, rows,
    ):
        return []
    if task_type == "castle.reel_cover" and not _reusable_reel_cover_artifacts(
        conn, rows,
    ):
        return []
    return rows


def _reusable_project_artifacts(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> bool:
    """Rehash bounded project contracts before cloning them into another run.

    Qualification receipts and descriptors are small control-plane documents.
    Reusing one whose bytes no longer match the persisted digest could turn a
    stale or modified contract into apparent evidence for a new run.  Keep the
    byte check scoped to ``castle.project_*`` so large media artifacts in other
    workflows do not acquire an unbounded read on every reuse lookup.
    """
    for row in rows:
        expected = str(row["content_hash"] or "")
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            return False

        has_file = bool(row["volume_id"] and row["relative_path"])
        has_partial_file_ref = bool(row["volume_id"]) != bool(row["relative_path"])
        has_inline = row["inline_json"] is not None
        if has_partial_file_ref or has_file == has_inline:
            return False

        if has_file:
            path = artifact_abs_path(conn, row)
            if path is None:
                return False
            try:
                details = path.stat()
            except OSError:
                return False
            if not path.is_file() or details.st_size > PROJECT_REUSE_MAX_ARTIFACT_BYTES:
                return False
            try:
                actual = sha256_file(path)
            except OSError:
                return False
        else:
            inline = row["inline_json"]
            if not isinstance(inline, str):
                return False
            encoded = inline.encode("utf-8")
            if len(encoded) > PROJECT_REUSE_MAX_ARTIFACT_BYTES:
                return False
            actual = hashlib.sha256(encoded).hexdigest()

        if actual != expected:
            return False
    return True


def _reusable_reel_cover_artifacts(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> bool:
    """Require one complete schema-4 authority set before scheduler reuse."""
    required = {
        "cover": ("image", "cover"),
        "metadata": ("json", "cover"),
        "cover_kv": ("json", "cover_kv"),
        "cover_authority": ("json", "cover_authority"),
        "cover_bundle": ("archive", "cover_bundle"),
    }
    by_name = {str(row["output_name"] or ""): row for row in rows}
    if set(by_name) != set(required) or len(by_name) != len(rows):
        return False
    common_audit = None
    common_input_hash = None
    for name, (artifact_type, role) in required.items():
        row = by_name[name]
        if str(row["artifact_type"] or "") != artifact_type:
            return False
        path = artifact_abs_path(conn, row)
        if (
            path is None
            or not path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(row["content_hash"] or ""))
            or sha256_file(path) != row["content_hash"]
        ):
            return False
        try:
            metadata = json_loads(row["metadata_json"], {}) or {}
            audit = validate_cover_audit(metadata.get("cover_audit"))
        except (TypeError, ValueError):
            return False
        if (
            metadata.get("workflow") != "castle.reel_cover"
            or metadata.get("stage") != "cover"
            or metadata.get("role") != role
            or metadata.get("task_schema_version") != "4"
        ):
            return False
        if common_audit is None:
            common_audit = audit
            common_input_hash = metadata.get("input_hash")
        elif audit != common_audit or metadata.get("input_hash") != common_input_hash:
            return False
    try:
        validate_cover_audit(
            common_audit,
            expected_final_sha256=str(by_name["cover"]["content_hash"] or ""),
            expected_metadata_sha256=str(by_name["cover_kv"]["content_hash"] or ""),
            expected_authority_sha256=str(
                by_name["cover_authority"]["content_hash"] or ""),
        )
    except (TypeError, ValueError):
        return False
    return True


def clone_reused_artifact(
    conn: sqlite3.Connection,
    source_row: sqlite3.Row | dict,
    *,
    run_id: str,
    task_id: str,
    input_hash: str,
    output_name: str,
) -> dict:
    source = dict(source_row)
    aid = artifact_id()
    now = utc_ts()
    source_meta = json_loads(source.get("source_meta_json"), {}) or {}
    source_meta.update({
        "reused_from_artifact_id": source.get("id"),
        "reused_from_task_id": source.get("producing_task_id"),
    })
    metadata = json_loads(source.get("metadata_json"), {}) or {}
    metadata.update({
        "reused": True,
        "reused_from_artifact_id": source.get("id"),
        "reused_from_task_id": source.get("producing_task_id"),
    })
    conn.execute(
        """
        insert into artifacts(
          id, run_id, producing_task_id, artifact_type, status, gate_state,
          volume_id, relative_path, inline_json, content_hash, input_hash,
          source_type, source_uri, source_meta_json, metadata_json, version,
          created_at, updated_at
        ) values (?, ?, ?, ?, 'available', ?, ?, ?, ?, ?, ?,
          'reused', ?, ?, ?, ?, ?, ?)
        """,
        (
            aid,
            run_id,
            task_id,
            source.get("artifact_type") or "file",
            # A reused byte object belongs to a new run and therefore needs a
            # fresh review decision.  Never inherit an approval/rejection from
            # the source run.
            "not_required",
            source.get("volume_id"),
            source.get("relative_path"),
            source.get("inline_json"),
            source.get("content_hash"),
            input_hash,
            f"artifact:{source.get('id')}",
            stable_json(source_meta),
            stable_json(metadata),
            source.get("version") or "v1",
            now,
            now,
        ),
    )
    conn.execute(
        "insert into task_outputs(task_id, name, artifact_id) values (?, ?, ?)",
        (task_id, output_name, aid),
    )
    for tag in artifact_tags(conn, str(source.get("id") or "")):
        conn.execute(
            "insert or ignore into artifact_tags(artifact_id, family, name) values (?, ?, ?)",
            (aid, tag["family"], tag["name"]),
        )
    add_event(
        conn,
        "artifact.reused",
        f"Reused artifact {source.get('id')}",
        run_id=run_id,
        task_id=task_id,
        artifact_id=aid,
        data={"source_artifact_id": source.get("id"), "source_task_id": source.get("producing_task_id")},
    )
    row = conn.execute("select * from artifacts where id = ?", (aid,)).fetchone()
    return artifact_public(row, conn)
