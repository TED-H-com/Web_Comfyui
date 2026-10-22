from __future__ import annotations

import html
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote

from .config import Settings, ensure_dirs, settings_from_env
from .db import json_loads, stable_json, transaction, utc_ts
from .events import add_event
from .review_role_contract import (
    required_review_artifact_ids_are_distinct,
    review_artifact_matches_role,
    unique_review_artifact_role,
)
from .review_transport import reel_review_transport, _state_path as review_transport_state_path
from .store import (
    artifact_abs_path,
    artifact_id,
    artifact_public,
    artifact_tags,
    default_volume,
    record_artifact_approval,
    set_artifact_gate_state,
    set_artifact_tags,
    sha256_file,
    safe_filename,
)


REVIEW_REQUIRED_ROLES = ("final", "proxy", "cover", "qc")
HARD_REQUIRED_ROLES = ("final", "proxy", "cover", "qc")
RELEASE_BINDING_ROLES = ("cover", "final", "proxy", "qc")
REVIEW_PAGE_ROLE = "review_page"
REVIEW_STATUS_APPLY_STATES = {"completed", "completed_approved", "needs_review"}
SECRET_RE = re.compile(r"(CASTLE_ORCH_SERVICE_TOKEN|TOKEN|SECRET|COOKIE|PASSWORD|API[_-]?KEY)", re.IGNORECASE)
LOCAL_PATH_RE = re.compile(r"(/home/[^\s<>'\"]+|/tmp/[^\s<>'\"]+)")
REVIEW_BINDING_KEYS = frozenset({
    "binding",
    "proxy_artifact_id",
    "proxy_artifact_created_at_us",
    "proxy_sha256",
    "cover_artifact_id",
    "cover_artifact_created_at_us",
    "cover_sha256",
    "media_revision",
    "release_binding_sha256",
})
REVIEW_BINDING_ARTIFACT_ID_RE = re.compile(r"art_[A-Za-z0-9]{8,80}")
REVIEW_BINDING_SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_REVIEW_BINDING_CREATED_AT_US = 2 ** 53 - 1


def parse_reel_review_binding_query(query: str) -> dict | None:
    if not query:
        return None
    try:
        values = parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=len(REVIEW_BINDING_KEYS),
        )
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid Reel review media binding") from error
    if set(values) != REVIEW_BINDING_KEYS or any(len(items) != 1 for items in values.values()):
        raise ValueError("Invalid Reel review media binding")
    proxy_artifact_id = values["proxy_artifact_id"][0]
    proxy_sha256 = values["proxy_sha256"][0]
    proxy_created_at_raw = values["proxy_artifact_created_at_us"][0]
    cover_artifact_id = values["cover_artifact_id"][0]
    cover_sha256 = values["cover_sha256"][0]
    cover_created_at_raw = values["cover_artifact_created_at_us"][0]
    media_revision = values["media_revision"][0]
    release_binding_sha256 = values["release_binding_sha256"][0]
    if (
        values["binding"][0] != "v2"
        or REVIEW_BINDING_ARTIFACT_ID_RE.fullmatch(proxy_artifact_id) is None
        or REVIEW_BINDING_SHA256_RE.fullmatch(proxy_sha256) is None
        or not proxy_created_at_raw.isdecimal()
        or REVIEW_BINDING_ARTIFACT_ID_RE.fullmatch(cover_artifact_id) is None
        or REVIEW_BINDING_SHA256_RE.fullmatch(cover_sha256) is None
        or not cover_created_at_raw.isdecimal()
        or REVIEW_BINDING_SHA256_RE.fullmatch(media_revision) is None
        or REVIEW_BINDING_SHA256_RE.fullmatch(release_binding_sha256) is None
    ):
        raise ValueError("Invalid Reel review media binding")
    proxy_created_at_us = int(proxy_created_at_raw)
    cover_created_at_us = int(cover_created_at_raw)
    if any(value <= 0 or value > MAX_REVIEW_BINDING_CREATED_AT_US for value in (
        proxy_created_at_us, cover_created_at_us,
    )):
        raise ValueError("Invalid Reel review media binding")
    return {
        "proxy_artifact_id": proxy_artifact_id,
        "proxy_artifact_created_at_us": proxy_created_at_us,
        "proxy_sha256": proxy_sha256,
        "cover_artifact_id": cover_artifact_id,
        "cover_artifact_created_at_us": cover_created_at_us,
        "cover_sha256": cover_sha256,
        "media_revision": media_revision,
        "release_binding_sha256": release_binding_sha256,
    }


def _artifact_created_at_us(row: dict) -> int:
    try:
        created_at = float(row.get("created_at"))
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(created_at):
        return 0
    return int(created_at * 1_000_000)


def reel_media_revision(run_id: str, proxy_sha256: str, cover_sha256: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"reel-r2-media-revision-v1\0")
    digest.update(run_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(proxy_sha256.encode("ascii"))
    digest.update(b"\0")
    digest.update(cover_sha256.encode("ascii"))
    return digest.hexdigest()


def reel_release_binding(run_id: str, roles: dict[str, dict]) -> dict:
    items = {}
    for role in RELEASE_BINDING_ROLES:
        row = roles.get(role)
        if not isinstance(row, dict):
            raise ValueError("Reel review media binding mismatch")
        artifact_id_value = row.get("id")
        artifact_sha256 = row.get("content_hash")
        artifact_created_at_us = _artifact_created_at_us(row)
        if (
            not isinstance(artifact_id_value, str)
            or REVIEW_BINDING_ARTIFACT_ID_RE.fullmatch(artifact_id_value) is None
            or not isinstance(artifact_sha256, str)
            or REVIEW_BINDING_SHA256_RE.fullmatch(artifact_sha256) is None
            or artifact_created_at_us <= 0
            or artifact_created_at_us > MAX_REVIEW_BINDING_CREATED_AT_US
        ):
            raise ValueError("Reel review media binding mismatch")
        items[role] = {
            "artifact_id": artifact_id_value,
            "artifact_created_at_us": artifact_created_at_us,
            "sha256": artifact_sha256,
        }
    return {
        "schema_version": "reel-review-release-binding-v1",
        "run_id": run_id,
        "roles": items,
    }


def reel_release_binding_sha256(binding: dict) -> str:
    digest = hashlib.sha256()
    digest.update(b"reel-review-release-binding-v1\0")
    digest.update(str(binding["run_id"]).encode("utf-8"))
    digest.update(b"\0")
    for role in RELEASE_BINDING_ROLES:
        item = binding["roles"][role]
        for value in (
            role,
            item["artifact_id"],
            str(item["artifact_created_at_us"]),
            item["sha256"],
        ):
            digest.update(value.encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def canonical_reel_review_binding(run_id: str, roles: dict[str, dict]) -> dict:
    release = reel_release_binding(run_id, roles)
    binding = {}
    for role in RELEASE_BINDING_ROLES:
        item = release["roles"][role]
        binding[f"{role}_artifact_id"] = item["artifact_id"]
        binding[f"{role}_artifact_created_at_us"] = item["artifact_created_at_us"]
        binding[f"{role}_sha256"] = item["sha256"]
    binding["media_revision"] = reel_media_revision(
        run_id, binding["proxy_sha256"], binding["cover_sha256"])
    binding["release_binding_sha256"] = reel_release_binding_sha256(release)
    return binding


def validate_reel_review_binding(
    run_id: str,
    roles: dict[str, dict],
    binding: dict | None,
) -> None:
    if binding is None:
        return
    canonical = canonical_reel_review_binding(run_id, roles)
    browser_keys = {
        "proxy_artifact_id", "proxy_artifact_created_at_us", "proxy_sha256",
        "cover_artifact_id", "cover_artifact_created_at_us", "cover_sha256",
        "media_revision", "release_binding_sha256",
    }
    if frozenset(binding) not in {frozenset(browser_keys), frozenset(canonical)}:
        raise ValueError("Reel review media binding mismatch")
    if any(canonical.get(key) != value for key, value in binding.items()):
        raise ValueError("Reel review media binding mismatch")
    proxy = roles.get("proxy")
    final = roles.get("final")
    if not isinstance(proxy, dict) or not isinstance(final, dict):
        raise ValueError("Reel review media binding mismatch")
    metadata = json_loads(proxy.get("metadata_json"), {})
    if not isinstance(metadata, dict):
        metadata = {}
    if (
        metadata.get("workflow") != "artifact.make_proxy"
        or metadata.get("source_artifact_id") != final.get("id")
        or metadata.get("source_content_hash") != final.get("content_hash")
    ):
        raise ValueError("Reel review media binding mismatch")


def _tags_by_artifact(conn, artifact_id_value: str) -> set[tuple[str, str]]:
    return {(tag["family"], tag["name"]) for tag in artifact_tags(conn, artifact_id_value)}


def _role_for_artifact(conn, artifact_row) -> str | None:
    tags = _tags_by_artifact(conn, artifact_row["id"])
    return unique_review_artifact_role(
        name for family, name in tags if family == "role")


def reel_review_artifacts(conn, run_id: str) -> dict[str, dict]:
    rows = conn.execute(
        """
        select * from artifacts
        where run_id = ?
          and status = 'available'
        order by created_at desc, id desc
        """,
        (run_id,),
    ).fetchall()
    roles: dict[str, dict] = {}
    for row in rows:
        role = _role_for_artifact(conn, row)
        if (
            role
            and role not in roles
            and review_artifact_matches_role(row, role)
        ):
            roles[role] = dict(row)
    if not required_review_artifact_ids_are_distinct(roles):
        return {}
    return roles


def maybe_enqueue_pipeline_proxy(conn, run_id: str) -> bool:
    run = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
    if run is None or run["workflow_type"] != "ai_sister.reel_pipeline":
        return False
    if run["status"] not in {"pending", "running", "completed", "needs_review"}:
        return False
    failed_task = conn.execute(
        "select 1 from tasks where run_id = ? and status in ('failed', 'cancelled') limit 1",
        (run_id,),
    ).fetchone()
    if failed_task is not None:
        return False
    roles = reel_review_artifacts(conn, run_id)
    final = roles.get("final")
    if final is None or roles.get("proxy") is not None:
        return False
    existing = conn.execute(
        "select 1 from tasks where run_id = ? and key = 'auto_proxy' limit 1",
        (run_id,),
    ).fetchone()
    if existing is not None:
        return False
    canonical_proxy_task = conn.execute(
        "select 1 from tasks where run_id = ? and key = 'review_proxy' limit 1",
        (run_id,),
    ).fetchone()
    if canonical_proxy_task is not None:
        return False
    path = artifact_abs_path(conn, final)
    if path is None or not path.is_file():
        return False
    source_hash = str(final["content_hash"] or "")
    if not source_hash:
        return False

    task_id = f"task_{uuid.uuid4().hex[:20]}"
    task_input = {
        "source_path": str(path),
        "source_artifact_id": final["id"],
        "source_content_hash": source_hash,
        "output_name": "review_proxy.mp4",
    }
    task_payload = dict(task_input)
    task_payload.update({
        "_run_id": run_id,
        "_task_id": task_id,
        "_workflow_type": run["workflow_type"],
        "_workflow_version_id": run["workflow_version_id"],
    })
    thash = stable_json({"task_type": "artifact.make_proxy", "input": task_input})
    input_digest = hashlib.sha256(thash.encode("utf-8")).hexdigest()
    now = utc_ts()
    conn.execute(
        """
        insert into tasks(
          id, run_id, key, task_type, status, lane, priority,
          attempt, max_attempts, reuse_policy, reused_from_task_id,
          input_json, input_hash, created_at, updated_at
        ) values (?, ?, 'auto_proxy', 'artifact.make_proxy', 'pending', 'cpu', ?,
          0, 1, 'off', null, ?, ?, ?, ?)
        """,
        (task_id, run_id, int(run["priority"] or 0), stable_json(task_payload), input_digest, now, now),
    )
    conn.execute(
        "insert into task_inputs(task_id, name, artifact_id, value_json) values (?, 'payload', ?, ?)",
        (task_id, final["id"], stable_json(task_payload)),
    )
    if run["status"] in {"completed", "needs_review"}:
        conn.execute(
            "update workflow_runs set status='running', completed_at=null, updated_at=? where id=?",
            (now, run_id),
        )
    add_event(
        conn,
        "task.enqueued",
        "Enqueued review proxy generation",
        run_id=run_id,
        task_id=task_id,
        data={"reason": "missing_proxy", "source_artifact_id": final["id"]},
    )
    return True


def _sanitize_text(value) -> str:
    text = str(value if value is not None else "")
    text = SECRET_RE.sub("[redacted]", text)
    text = LOCAL_PATH_RE.sub("[local-path]", text)
    return text


def _load_qc_summary(conn, qc_row) -> dict:
    path = artifact_abs_path(conn, qc_row)
    data = None
    if path and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = None
    if not isinstance(data, dict):
        return {"verdict": "UNKNOWN", "checks": []}
    checks = []
    for item in data.get("checks") or []:
        if not isinstance(item, dict):
            continue
        checks.append({
            "check": _sanitize_text(item.get("check"))[:120],
            "level": _sanitize_text(item.get("level"))[:40],
            "detail": _sanitize_text(item.get("detail"))[:500],
        })
    return {
        "verdict": _sanitize_text(data.get("verdict") or "UNKNOWN")[:40],
        "theme": _sanitize_text(data.get("theme") or "")[:80],
        "checks": checks,
    }


def _copy_review_media(conn, role: str, row: dict, bundle_dir: Path) -> str | None:
    path = artifact_abs_path(conn, row)
    if path is None or not path.is_file():
        return None
    suffix = path.suffix or ".bin"
    filename = safe_filename(f"{role}{suffix}", f"{role}.bin")
    target = bundle_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    source_stat = path.stat()
    try:
        target_stat = target.stat()
    except FileNotFoundError:
        target_stat = None
    if (
        target_stat is not None
        and target_stat.st_size == source_stat.st_size
        and target_stat.st_mtime_ns == source_stat.st_mtime_ns
    ):
        return filename
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(path, tmp)
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return filename


def _qc_table(qc: dict) -> str:
    checks = qc.get("checks") or []
    if not checks:
        return '<p class="qc-none">QC report unavailable.</p>'
    rows = []
    for item in checks:
        level = str(item.get("level") or "?").upper()
        klass = "ok" if level == "PASS" else ("fail" if level == "FAIL" else "warn")
        rows.append(
            '<tr class="lv-{klass}"><td class="ck">{check}</td><td class="lvl">'
            '<span class="dot {klass}"></span>{level}</td><td class="dt">{detail}</td></tr>'.format(
                klass=klass,
                check=html.escape(str(item.get("check") or "")),
                level=html.escape(level),
                detail=html.escape(str(item.get("detail") or "")),
            )
        )
    verdict = str(qc.get("verdict") or "UNKNOWN").upper()
    pill = "ok" if verdict == "PASS" else ("fail" if verdict == "FAIL" else "warn")
    return (
        f'<div class="qc-head">Automatic QC <span class="pill {pill}">{html.escape(verdict)}</span></div>'
        '<table class="qc"><tr><th>Check</th><th>Verdict</th><th>Detail</th></tr>'
        + "".join(rows)
        + "</table>"
    )


def _render_review_html(
    run: dict,
    roles: dict[str, dict],
    media: dict[str, str],
    qc: dict,
    transport: dict | None = None,
) -> str:
    title = _sanitize_text(run.get("title") or run.get("id") or "AI-Sister reel")
    run_id = _sanitize_text(run.get("id") or "")
    # Review uses the bounded proxy first. The full final stays available as a
    # fallback/artifact, but should not make a phone pull ~100 MB just to QC.
    transport = transport if isinstance(transport, dict) else {}
    proxy_artifact_id = str((roles.get("proxy") or {}).get("id") or "").strip()
    final_artifact_id = str((roles.get("final") or {}).get("id") or "").strip()
    cover_artifact_id = str((roles.get("cover") or {}).get("id") or "").strip()
    proxy_artifact_url = (
        f'/api/orchestrator/artifacts/{quote(proxy_artifact_id, safe="")}/file'
        if proxy_artifact_id else ""
    )
    final_artifact_url = (
        f'/api/orchestrator/artifacts/{quote(final_artifact_id, safe="")}/file'
        if final_artifact_id else ""
    )
    cover_artifact_url = (
        f'/api/orchestrator/artifacts/{quote(cover_artifact_id, safe="")}/file'
        if cover_artifact_id else ""
    )
    video_src = (
        transport.get("review_url") or proxy_artifact_url
        or media.get("proxy") or final_artifact_url or media.get("final") or ""
    )
    cover_src = transport.get("cover_url") or cover_artifact_url or media.get("cover") or ""
    video = (
        f'<video controls preload="none" playsinline poster="{html.escape(cover_src)}" src="{html.escape(video_src)}"></video>'
        if video_src else '<div class="missing">No final/proxy video artifact.</div>'
    )
    master_link = (
        '<a class="master-link" target="_blank" rel="noreferrer" '
        f'href="{final_artifact_url}">'
        'Open or download original master on local server (large file)</a>'
        if final_artifact_id else ""
    )
    cover = f'<img class="cover" src="{html.escape(cover_src)}" alt="cover">' if cover_src else ""
    artifact_rows = []
    for role in REVIEW_REQUIRED_ROLES:
        row = roles.get(role)
        if not row:
            continue
        artifact_rows.append(
            f'<span class="pill mut">{html.escape(role)} · {html.escape(str(row.get("id") or ""))}</span>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>{html.escape(title)} · Castle Review</title>
<style>
  :root{{ --bg:#0f1216; --card:#171c23; --ink:#eef2f7; --mut:#9aa7b5; --acc:#ff9a3c;
         --good:#5ad17a; --warn:#ffd166; --bad:#ff6b6b; --line:#2a313a; }}
  *{{ box-sizing:border-box; -webkit-text-size-adjust:100%; }}
  body{{ margin:0; background:var(--bg); color:var(--ink);
        font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
        line-height:1.55; padding:0 0 80px; }}
  .wrap{{ max-width:880px; margin:0 auto; padding:22px 16px; }}
  h1{{ font-size:24px; margin:2px 0 4px; line-height:1.2; }}
  .sub{{ color:var(--mut); font-size:14px; margin:0 0 14px; overflow-wrap:anywhere; }}
  .summary,.ep{{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin:14px 0; }}
  .pill{{ display:inline-block; padding:2px 9px; border-radius:10px; font-size:12px; font-weight:700; margin:0 5px 5px 0; background:#222a33; color:var(--ink); }}
  .pill.ok{{ background:rgba(90,209,122,.16); color:var(--good); }}
  .pill.warn{{ background:rgba(255,209,102,.16); color:var(--warn); }}
  .pill.fail{{ background:rgba(255,107,107,.18); color:var(--bad); }}
  .pill.mut{{ background:#1c2229; color:var(--mut); }}
  .body{{ display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start; }}
  .reelwrap{{ flex:0 0 auto; }}
  video{{ width:300px; max-width:78vw; border-radius:12px; background:#000; display:block; }}
  .master-link{{ display:block; margin-top:10px; border:1px solid var(--line); border-radius:10px;
                padding:9px 12px; color:var(--ink); background:#222a33; text-align:center;
                font-size:13px; font-weight:750; text-decoration:none; }}
  .master-link:hover{{ border-color:var(--acc); color:var(--acc); }}
  img.cover{{ width:120px; max-width:34vw; border-radius:10px; display:block; margin:0 0 10px; }}
  .qc-wrap{{ flex:1 1 360px; min-width:280px; }}
  .qc-head{{ font-size:14px; color:var(--mut); margin:2px 0 8px; }}
  table.qc{{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.qc th{{ text-align:left; color:var(--mut); font-weight:600; font-size:12px; padding:4px 8px; border-bottom:1px solid var(--line); }}
  table.qc td{{ padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
  td.ck,td.lvl{{ white-space:nowrap; }}
  td.dt{{ color:var(--mut); overflow-wrap:anywhere; }}
  .dot{{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; vertical-align:middle; }}
  .dot.ok{{ background:var(--good); }} .dot.warn{{ background:var(--warn); }} .dot.fail{{ background:var(--bad); }}
  .authority{{ border-radius:12px; padding:12px; margin-top:12px; color:var(--mut);
              background:#12171d; border:1px solid var(--line); font-size:13px; }}
  .missing,.qc-none{{ color:var(--mut); font-size:13.5px; }}
  @media(max-width:640px){{ video{{ width:100%; max-width:100%; }} .qc-wrap{{ min-width:100%; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{html.escape(title)}</h1>
  <p class="sub">Castle diagnostic bundle · {html.escape(run_id)}</p>
  <div class="summary">
    <div>{''.join(artifact_rows)}</div>
    <div class="authority">Verdicts are submitted only in AI-Sister Admin. This local server page is for artifact diagnostics and original-master access.</div>
  </div>
  <section class="ep">
    <div class="body">
      <div class="reelwrap">{cover}{video}{master_link}</div>
      <div class="qc-wrap">{_qc_table(qc)}</div>
    </div>
  </section>
</div>
</body>
</html>
"""


def _review_relpath(run_id: str) -> Path:
    safe_run = safe_filename(run_id, "run")
    return Path("review_bundles") / safe_run / "review.html"


def _existing_review_page(conn, run_id: str):
    rows = conn.execute(
        """
        select * from artifacts
        where run_id = ? and status = 'available'
        order by created_at desc, id desc
        """,
        (run_id,),
    ).fetchall()
    candidates = [
        row for row in rows
        if _role_for_artifact(conn, row) == REVIEW_PAGE_ROLE
        and review_artifact_matches_role(row, REVIEW_PAGE_ROLE)
    ]
    if len(candidates) > 1:
        raise ValueError("Ambiguous canonical review page artifacts")
    return candidates[0] if candidates else None


def review_reconciliation_fingerprint(conn, run_id: str, settings: Settings) -> str:
    """Cheap change detection; never hash videos or mutate review state here."""
    def signature(path):
        if path is None:
            return None
        try:
            value = path.stat()
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        except FileNotFoundError:
            return None

    run = conn.execute("select * from workflow_runs where id=?", (run_id,)).fetchone()
    # Intermediate frames/audio do not participate in review. Keep every
    # candidate (including ambiguous/invalid ones), so adding/removing role
    # tags or fixing a candidate still invalidates the snapshot.
    review_roles = (*REVIEW_REQUIRED_ROLES, REVIEW_PAGE_ROLE)
    placeholders = ",".join("?" for _ in review_roles)
    rows = conn.execute(
        f"""select a.* from artifacts a where a.run_id=? and exists (
            select 1 from artifact_tags t where t.artifact_id=a.id
            and t.family='role' and t.name in ({placeholders})
        ) order by a.id""", (run_id, *review_roles),
    ).fetchall()
    artifacts = []
    bundle = settings.artifact_root / "review_bundles" / safe_filename(run_id, "run")
    for row in rows:
        path = artifact_abs_path(conn, row)
        artifacts.append((dict(row), sorted(_tags_by_artifact(conn, row["id"])), signature(path)))
    tasks = [tuple(row) for row in conn.execute(
        "select id,key,status,updated_at from tasks where run_id=? order by id", (run_id,)
    )]
    # Missing/deleted generated files and transport changes also need repair,
    # even if the canonical source artifact rows have not changed.
    bundle_files = sorted((path.name, signature(path)) for path in bundle.iterdir()) if bundle.is_dir() else []
    payload = (dict(run) if run else None, artifacts, tasks, bundle_files,
               signature(review_transport_state_path()), signature(settings.artifact_root / _review_relpath(run_id)))
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _insert_or_update_review_page(conn, run_id: str, html_text: str, settings: Settings | None = None) -> dict:
    settings = ensure_dirs(settings or settings_from_env())
    # Resolve canonical identity before touching the bundle on disk.  If the
    # database contains two valid review-page artifacts, fail closed without
    # rewriting the file referenced by either row.
    existing = _existing_review_page(conn, run_id)
    volume = default_volume(conn)
    rel = _review_relpath(run_id)
    target = (settings.artifact_root / rel).resolve()
    encoded = html_text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    metadata = {
        "workflow": "ai_sister.reel_pipeline",
        "review_bundle": True,
        "run_id": run_id,
        "idempotency_key": f"{run_id}:review_page",
    }
    same_record = existing is not None and (
        existing["content_hash"] == digest
        and existing["relative_path"] == rel.as_posix()
        and existing["volume_id"] == volume["id"]
        and existing["metadata_json"] == stable_json(metadata)
    )
    if same_record and target.is_file() and sha256_file(target) == digest:
        return artifact_public(existing, conn)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(html_text)
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    now = utc_ts()
    if same_record:
        # Repair missing/corrupt HTML without churning identical database rows.
        return artifact_public(existing, conn)
    if existing is None:
        aid = artifact_id()
        conn.execute(
            """
            insert into artifacts(
              id, run_id, producing_task_id, artifact_type, status, gate_state,
              volume_id, relative_path, inline_json, content_hash, input_hash,
              source_type, source_uri, source_meta_json, metadata_json, version,
              created_at, updated_at
            ) values (?, ?, null, 'html', 'available', 'not_required', ?, ?, null, ?, null,
              'generated', null, '{}', ?, 'v1', ?, ?)
            """,
            (aid, run_id, volume["id"], rel.as_posix(), digest, stable_json(metadata), now, now),
        )
        set_artifact_tags(conn, aid, [{"family": "workflow", "name": "reel5"}, {"family": "role", "name": REVIEW_PAGE_ROLE}])
        add_event(conn, "run.review_page_generated", "Generated reel review page", run_id=run_id, artifact_id=aid)
        row = conn.execute("select * from artifacts where id = ?", (aid,)).fetchone()
    else:
        changed = existing["content_hash"] != digest or existing["relative_path"] != rel.as_posix()
        conn.execute(
            "update artifacts set volume_id=?, relative_path=?, content_hash=?, metadata_json=?, updated_at=? where id=?",
            (volume["id"], rel.as_posix(), digest, stable_json(metadata), now, existing["id"]),
        )
        if changed:
            add_event(conn, "run.review_page_generated", "Updated reel review page", run_id=run_id, artifact_id=existing["id"])
        row = conn.execute("select * from artifacts where id = ?", (existing["id"],)).fetchone()
    return artifact_public(row, conn)


def recompute_reel_review_status(conn, run_id: str) -> dict:
    run = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError("Run not found")
    roles = reel_review_artifacts(conn, run_id)
    gated_role_names = [role for role in HARD_REQUIRED_ROLES if role in roles]
    gated = [roles[role] for role in gated_role_names]
    states = {row["id"]: row["gate_state"] for row in gated}
    role_states = {role: roles[role]["gate_state"] for role in gated_role_names}
    missing_roles = [role for role in HARD_REQUIRED_ROLES if role not in roles]
    current = run["status"]
    computed_status = current
    if missing_roles:
        computed_status = "needs_review"
    elif any(state in {"rejected", "needs_review", "failed"} for state in role_states.values()):
        computed_status = "needs_review"
    elif all(role_states.get(role) == "approved" for role in HARD_REQUIRED_ROLES) and all(
        state == "approved" for state in role_states.values()
    ):
        computed_status = "completed_approved"
    elif gated:
        computed_status = "needs_review"
    # workflow_runs.status is execution state. Review state remains derived
    # from artifact gates, except the explicit completed_approved release state.
    target_run_status = (
        "completed_approved"
        if computed_status == "completed_approved"
        else "completed"
    )
    has_execution_failure = conn.execute(
        """
        select 1 from tasks
        where run_id = ? and status in ('failed', 'cancelled')
        limit 1
        """,
        (run_id,),
    ).fetchone() is not None
    can_apply = (
        current in REVIEW_STATUS_APPLY_STATES
        and not has_execution_failure
    )
    applied = False
    if can_apply and target_run_status != current:
        now = utc_ts()
        conn.execute(
            "update workflow_runs set status=?, updated_at=? where id=?",
            (target_run_status, now, run_id),
        )
        applied = True
        add_event(
            conn,
            "run.review_status",
            f"Review status {computed_status}",
            run_id=run_id,
            level="warning" if computed_status == "needs_review" else "info",
            data={"gate_states": states, "role_states": role_states, "missing_roles": missing_roles},
        )
    return {
        "required_gate_states": states,
        "required_role_states": role_states,
        "missing_roles": missing_roles,
        "status": computed_status,
        "status_applied": applied,
        "run_status": target_run_status if can_apply else current,
    }


def prepare_reel_review(
    conn,
    run_id: str,
    settings: Settings | None = None,
    *,
    expected_binding: dict | None = None,
) -> dict:
    run = conn.execute("select * from workflow_runs where id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError("Run not found")
    if run["workflow_type"] != "ai_sister.reel_pipeline":
        raise ValueError("Run is not an ai_sister.reel_pipeline run")
    roles = reel_review_artifacts(conn, run_id)
    validate_reel_review_binding(run_id, roles, expected_binding)
    settings = ensure_dirs(settings or settings_from_env())
    bundle_dir = settings.artifact_root / "review_bundles" / safe_filename(run_id, "run")
    media = {}
    transport = reel_review_transport(run_id, roles)
    for role in ("proxy", "cover"):
        if role in roles:
            filename = _copy_review_media(conn, role, roles[role], bundle_dir)
            if filename:
                media[role] = filename
    qc = _load_qc_summary(conn, roles["qc"]) if "qc" in roles else {"verdict": "UNKNOWN", "checks": []}
    if not media.get("proxy") and "final" in roles:
        filename = _copy_review_media(conn, "final", roles["final"], bundle_dir)
        if filename:
            media["final"] = filename
    page = _render_review_html(dict(run), roles, media, qc, transport)
    return {
        "run": dict(run),
        "roles": roles,
        "page": page,
        "settings": settings,
        "transport": transport,
    }


def finalize_reel_review(conn, run_id: str, prepared: dict, settings: Settings | None = None) -> dict:
    roles = prepared["roles"]
    for role in REVIEW_REQUIRED_ROLES:
        row = roles.get(role)
        if row and row["gate_state"] == "not_required":
            updated = set_artifact_gate_state(conn, row["id"], "pending")
            row["gate_state"] = updated["gate_state"]
    page = prepared["page"]
    settings = settings or prepared.get("settings")
    review_artifact = _insert_or_update_review_page(conn, run_id, page, settings)
    maybe_enqueue_pipeline_proxy(conn, run_id)
    status = recompute_reel_review_status(conn, run_id)
    transport_generation = hashlib.sha256(json.dumps([
        [
            role,
            str((roles.get(role) or {}).get("id") or ""),
            str((roles.get(role) or {}).get("content_hash") or ""),
        ]
        for role in sorted(HARD_REQUIRED_ROLES)
    ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    return {
        "review_artifact": review_artifact,
        "required_roles": [role for role in HARD_REQUIRED_ROLES if role in roles],
        "transport_generation": transport_generation,
        **status,
    }


def ensure_reel_review(conn, run_id: str, settings: Settings | None = None) -> dict:
    prepared = prepare_reel_review(conn, run_id, settings)
    with transaction(conn):
        return finalize_reel_review(conn, run_id, prepared, settings)


def submit_reel_review(
    conn,
    run_id: str,
    decision: str,
    *,
    actor: str = "operator",
    note: str = "",
    ensure_result: dict | None = None,
) -> dict:
    decision = str(decision or "").strip().lower()
    if decision in {"pass", "passed", "approve", "approved"}:
        approval_decision = "approved"
    elif decision in {"fail", "failed", "reject", "rejected", "needs_work"}:
        approval_decision = "needs_work"
    else:
        raise ValueError("decision must be pass or fail")
    ensure_result = ensure_result or {"required_roles": []}
    roles = reel_review_artifacts(conn, run_id)
    changed = []
    for role in REVIEW_REQUIRED_ROLES:
        row = roles.get(role)
        if not row:
            continue
        latest = conn.execute(
            "select * from approvals where artifact_id = ? order by created_at desc limit 1",
            (row["id"],),
        ).fetchone()
        if latest is not None and latest["decision"] == approval_decision:
            continue
        artifact = record_artifact_approval(
            conn,
            row["id"],
            approval_decision,
            actor=actor,
            note=note,
            run_id=run_id,
        )
        changed.append({"role": role, "artifact_id": artifact["id"]})
    status = recompute_reel_review_status(conn, run_id)
    if changed:
        event_type = "run.review_passed" if approval_decision == "approved" else "run.review_failed"
        add_event(
            conn,
            event_type,
            "Reel review PASS" if approval_decision == "approved" else "Reel review FAIL",
            run_id=run_id,
            level="info" if approval_decision == "approved" else "warning",
            data={"actor": actor, "changed": changed, "note": note[:1000]},
        )
    return {**ensure_result, **status, "decision": approval_decision, "changed": changed, "idempotent": not changed}
