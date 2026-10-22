from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

from .config import Settings, settings_from_env
from .db import connect, initialize, stable_json, transaction, utc_ts
from .events import add_event
from .review import (
    finalize_reel_review,
    prepare_reel_review,
    reel_media_revision,
    reel_release_binding_sha256,
    submit_reel_review,
)


SOURCE = "ai-sister"
EVENT_SCHEMA = "reel-review-outbox-v2"
RELEASE_BINDING_ROLES = ("cover", "final", "proxy", "qc")
RUN_ID_RE = re.compile(
    r"(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9_-]{0,78}[A-Za-z0-9])"
)
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}")
ARTIFACT_ID_RE = re.compile(r"art_[A-Za-z0-9]{8,80}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_SAFE_INTEGER = 2**53 - 1


class ReelReviewBridgeError(RuntimeError):
    code = "review_bridge_error"


class ReelReviewBridgeEventError(ReelReviewBridgeError):
    code = "invalid_review_event"


class ReelReviewBridgeIdentityError(ReelReviewBridgeError):
    code = "review_event_identity_conflict"


class ReelReviewBridgeApplyError(ReelReviewBridgeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _bounded_text(value, *, name: str, maximum: int, nullable: bool = False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ReelReviewBridgeEventError(f"invalid {name}")
    return value


def _positive_safe_integer(value, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_SAFE_INTEGER
    ):
        raise ReelReviewBridgeEventError(f"invalid {name}")
    return value


def _oracle_payload_sha256(event: dict) -> str:
    payload = {
        "reelId": event["reel_id"],
        "mediaRevision": event["media_revision"],
        "requestedStatus": event["requested_status"],
        "notes": event["notes"],
        "reviewer": event["reviewer"],
        "releaseBindingSha256": event["binding"]["release_binding_sha256"],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_reel_review_outbox_event(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("schema_version") != EVENT_SCHEMA:
        raise ReelReviewBridgeEventError("invalid event schema")
    source_event_id = _positive_safe_integer(raw.get("id"), name="event id")
    review_revision = _positive_safe_integer(
        raw.get("review_revision"), name="review revision"
    )
    created_at = _positive_safe_integer(raw.get("created_at"), name="created at")
    request_id = _bounded_text(raw.get("request_id"), name="request id", maximum=128)
    reel_id = _bounded_text(raw.get("reel_id"), name="reel id", maximum=80)
    media_revision = _bounded_text(
        raw.get("media_revision"), name="media revision", maximum=64
    )
    payload_sha256 = _bounded_text(
        raw.get("payload_sha256"), name="payload digest", maximum=64
    )
    reviewer = _bounded_text(raw.get("reviewer"), name="reviewer", maximum=120)
    notes = raw.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > 2000 or "\x00" in notes:
            raise ReelReviewBridgeEventError("invalid notes")
    decision = raw.get("decision")
    requested_status = raw.get("requested_status")
    if (
        (decision, requested_status)
        not in {("pass", "approved"), ("fail", "needs_fix")}
        or REQUEST_ID_RE.fullmatch(request_id) is None
        or RUN_ID_RE.fullmatch(reel_id) is None
        or SHA256_RE.fullmatch(media_revision) is None
        or SHA256_RE.fullmatch(payload_sha256) is None
    ):
        raise ReelReviewBridgeEventError("invalid event identity")
    binding = raw.get("binding")
    expected_binding_keys = {
        f"{role}_{suffix}"
        for role in RELEASE_BINDING_ROLES
        for suffix in ("artifact_id", "artifact_created_at_us", "sha256")
    } | {"release_binding_sha256"}
    if not isinstance(binding, dict) or set(binding) != expected_binding_keys:
        raise ReelReviewBridgeEventError("invalid media binding")
    normalized_binding = {}
    release_roles = {}
    for role in RELEASE_BINDING_ROLES:
        artifact_id = binding.get(f"{role}_artifact_id")
        artifact_sha256 = binding.get(f"{role}_sha256")
        artifact_created_at_us = _positive_safe_integer(
            binding.get(f"{role}_artifact_created_at_us"),
            name=f"{role} artifact created at",
        )
        if (
            not isinstance(artifact_id, str)
            or ARTIFACT_ID_RE.fullmatch(artifact_id) is None
            or not isinstance(artifact_sha256, str)
            or SHA256_RE.fullmatch(artifact_sha256) is None
        ):
            raise ReelReviewBridgeEventError("invalid media binding")
        normalized_binding[f"{role}_artifact_id"] = artifact_id
        normalized_binding[f"{role}_artifact_created_at_us"] = artifact_created_at_us
        normalized_binding[f"{role}_sha256"] = artifact_sha256
        release_roles[role] = {
            "artifact_id": artifact_id,
            "artifact_created_at_us": artifact_created_at_us,
            "sha256": artifact_sha256,
        }
    release_digest = binding.get("release_binding_sha256")
    release = {
        "schema_version": "reel-review-release-binding-v1",
        "run_id": reel_id,
        "roles": release_roles,
    }
    if (
        not isinstance(release_digest, str)
        or SHA256_RE.fullmatch(release_digest) is None
        or reel_release_binding_sha256(release) != release_digest
        or reel_media_revision(
            reel_id,
            normalized_binding["proxy_sha256"],
            normalized_binding["cover_sha256"],
        ) != media_revision
    ):
        raise ReelReviewBridgeEventError("invalid media binding")
    normalized_binding["media_revision"] = media_revision
    normalized_binding["release_binding_sha256"] = release_digest
    event = {
        "schema_version": EVENT_SCHEMA,
        "source_event_id": str(source_event_id),
        "source_request_id": request_id,
        "reel_id": reel_id,
        "media_revision": media_revision,
        "review_revision": review_revision,
        "payload_sha256": payload_sha256,
        "decision": decision,
        "requested_status": requested_status,
        "notes": notes,
        "reviewer": reviewer,
        "created_at": created_at,
        "binding": normalized_binding,
    }
    if _oracle_payload_sha256(event) != payload_sha256:
        raise ReelReviewBridgeEventError("payload digest mismatch")
    event["event_sha256"] = hashlib.sha256(
        stable_json(event).encode("utf-8")
    ).hexdigest()
    return event


def _settings_for_db(
    db_path: str | Path | None,
    settings: Settings | None,
) -> Settings:
    resolved = settings or settings_from_env()
    if db_path is not None:
        resolved = replace(resolved, db_path=Path(db_path).expanduser())
    return resolved


def _receipt_for_event(conn, event: dict):
    return conn.execute(
        "select * from reel_review_receipts where source=? and source_event_id=?",
        (SOURCE, event["source_event_id"]),
    ).fetchone()


def _assert_receipt_identity(conn, event: dict):
    receipt = _receipt_for_event(conn, event)
    if receipt is not None and receipt["event_sha256"] != event["event_sha256"]:
        raise ReelReviewBridgeIdentityError("event id payload changed")
    request_receipt = conn.execute(
        "select * from reel_review_receipts where source=? and source_request_id=?",
        (SOURCE, event["source_request_id"]),
    ).fetchone()
    if (
        request_receipt is not None
        and request_receipt["source_event_id"] != event["source_event_id"]
    ):
        raise ReelReviewBridgeIdentityError("request id event changed")
    return receipt


def _write_receipt(
    conn,
    event: dict,
    *,
    result: str,
    error_code: str | None,
    canonical_status: str | None,
) -> None:
    now = utc_ts()
    conn.execute(
        """
        insert into reel_review_receipts(
          source, source_event_id, source_request_id, reel_id, media_revision,
          review_revision, payload_sha256, event_sha256, release_binding_sha256,
          binding_json, decision, notes, reviewer, result,
          error_code, canonical_status, created_at, updated_at, applied_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(source, source_event_id) do update set
          result=excluded.result,
          error_code=excluded.error_code,
          canonical_status=excluded.canonical_status,
          updated_at=excluded.updated_at,
          applied_at=excluded.applied_at
        """,
        (
            SOURCE,
            event["source_event_id"],
            event["source_request_id"],
            event["reel_id"],
            event["media_revision"],
            event["review_revision"],
            event["payload_sha256"],
            event["event_sha256"],
            event["binding"]["release_binding_sha256"],
            stable_json(event["binding"]),
            event["decision"],
            event["notes"],
            event["reviewer"],
            result,
            error_code,
            canonical_status,
            now,
            now,
            now if result in {"applied", "stale"} else None,
        ),
    )


def _safe_apply_error_code(exc: BaseException) -> str:
    if isinstance(exc, KeyError):
        return "run_not_found"
    if isinstance(exc, sqlite3.Error):
        return "database_error"
    if isinstance(exc, OSError):
        return "media_io_error"
    text = str(exc).lower()
    if "binding" in text:
        return "media_binding_mismatch"
    if "ai_sister.reel_pipeline" in text:
        return "workflow_type_mismatch"
    if "ambiguous" in text:
        return "review_artifact_ambiguous"
    return "canonical_gate_apply_failed"


def _record_failed_receipt(settings: Settings, event: dict, code: str) -> None:
    with connect(settings) as conn:
        with transaction(conn):
            receipt = _assert_receipt_identity(conn, event)
            if receipt is not None and receipt["result"] in {"applied", "stale"}:
                return
            _write_receipt(
                conn,
                event,
                result="failed",
                error_code=code,
                canonical_status=None,
            )


def apply_reel_review_outbox_event(
    raw_event: dict,
    *,
    db_path: str | Path | None = None,
    settings: Settings | None = None,
) -> dict:
    """Apply one Oracle verdict through Castle's canonical gates.

    This is an internal callable used by Castle's existing outbound sync job.
    It does not expose a Castle inbound route.
    """
    event = normalize_reel_review_outbox_event(raw_event)
    settings = _settings_for_db(db_path, settings)
    initialize(settings)
    try:
        with connect(settings) as conn:
            with transaction(conn):
                receipt = _assert_receipt_identity(conn, event)
                if receipt is not None and receipt["result"] in {"applied", "stale"}:
                    return {
                        "ok": True,
                        "result": receipt["result"],
                        "idempotent": True,
                        "canonical_status": receipt["canonical_status"],
                    }
                same_revision = conn.execute(
                    """
                    select * from reel_review_receipts
                    where source=? and reel_id=? and review_revision=?
                      and source_event_id != ?
                    limit 1
                    """,
                    (
                        SOURCE,
                        event["reel_id"],
                        event["review_revision"],
                        event["source_event_id"],
                    ),
                ).fetchone()
                if same_revision is not None:
                    raise ReelReviewBridgeIdentityError(
                        "review revision belongs to another event"
                    )
                newer = conn.execute(
                    """
                    select * from reel_review_receipts
                    where source=? and reel_id=? and review_revision>?
                    order by review_revision desc limit 1
                    """,
                    (SOURCE, event["reel_id"], event["review_revision"]),
                ).fetchone()
                if newer is not None:
                    canonical_status = newer["canonical_status"]
                    if not canonical_status:
                        run = conn.execute(
                            "select status from workflow_runs where id=?",
                            (event["reel_id"],),
                        ).fetchone()
                        canonical_status = run["status"] if run is not None else None
                    _write_receipt(
                        conn,
                        event,
                        result="stale",
                        error_code=None,
                        canonical_status=canonical_status,
                    )
                    return {
                        "ok": True,
                        "result": "stale",
                        "idempotent": False,
                        "canonical_status": canonical_status,
                    }
                prepared = prepare_reel_review(
                    conn,
                    event["reel_id"],
                    settings,
                    expected_binding=event["binding"],
                )
                ensured = finalize_reel_review(
                    conn, event["reel_id"], prepared, settings
                )
                review = submit_reel_review(
                    conn,
                    event["reel_id"],
                    event["decision"],
                    actor=f"ai-sister:{event['reviewer']}"[:120],
                    note=(event["notes"] or "")[:1000],
                    ensure_result=ensured,
                )
                canonical_status = str(
                    review.get("run_status") or review.get("status") or ""
                )
                _write_receipt(
                    conn,
                    event,
                    result="applied",
                    error_code=None,
                    canonical_status=canonical_status,
                )
                add_event(
                    conn,
                    "run.review_bridge_applied",
                    "Applied AI-Sister Reel review",
                    run_id=event["reel_id"],
                    data={
                        "source_event_id": event["source_event_id"],
                        "review_revision": event["review_revision"],
                        "decision": event["decision"],
                    },
                )
                return {
                    "ok": True,
                    "result": "applied",
                    "idempotent": bool(review.get("idempotent")),
                    "canonical_status": canonical_status,
                }
    except ReelReviewBridgeIdentityError:
        raise
    except Exception as exc:
        code = _safe_apply_error_code(exc)
        _record_failed_receipt(settings, event, code)
        raise ReelReviewBridgeApplyError(code) from exc
