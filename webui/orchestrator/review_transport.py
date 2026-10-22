from __future__ import annotations

import json
import os
import re
from pathlib import Path


STATE_SCHEMA = "castle-reel-review-sync-v1"
DEFAULT_STATE = Path.home() / ".local/share/web-comfyui/reel-review-sync.json"
MAX_STATE_BYTES = 4 * 1024 * 1024
RUN_ID_PATTERN = r"(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9_-]{0,78}[A-Za-z0-9])"
RUN_ID_RE = re.compile(RUN_ID_PATTERN)
HASH_RE = re.compile(r"[0-9a-f]{64}")
ERROR_RE = re.compile(r"[a-z0-9_.-]{0,80}")
PROXY_KEY_RE = re.compile(
    rf"reel-review/v1/(?P<run>{RUN_ID_PATTERN})/proxy-(?P<hash>[0-9a-f]{{64}})\.mp4"
)
COVER_KEY_RE = re.compile(
    rf"reel-review/v1/(?P<run>{RUN_ID_PATTERN})/cover-(?P<hash>[0-9a-f]{{64}})\.(?:png|jpg|webp)"
)


def _bounded_int(value, *, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(parsed, maximum))


def _state_path() -> Path:
    return Path(os.environ.get("CASTLE_REEL_SYNC_STATE", DEFAULT_STATE)).expanduser()


def load_reel_review_transport_state() -> tuple[dict, str]:
    path = _state_path()
    if not path.exists():
        return {}, ""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
            return {}, "state_invalid"
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, "state_invalid"
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != STATE_SCHEMA
        or not isinstance(state.get("runs"), dict)
    ):
        return {}, "state_invalid"
    return state["runs"], ""


def _artifact_matches(state_asset: object, role_row: dict | None) -> bool:
    if not isinstance(state_asset, dict) or not isinstance(role_row, dict):
        return False
    digest = str(role_row.get("content_hash") or "")
    return (
        HASH_RE.fullmatch(digest) is not None
        and state_asset.get("artifact_id") == role_row.get("id")
        and state_asset.get("content_hash") == digest
    )


def _verified_key(value: object, *, run_id: str, digest: str, role: str) -> str | None:
    if not isinstance(value, str):
        return None
    match = (PROXY_KEY_RE if role == "proxy" else COVER_KEY_RE).fullmatch(value)
    if match is None or match.group("run") != run_id or match.group("hash") != digest:
        return None
    return value


def reel_review_transport(
    run_id: str,
    roles: dict[str, dict],
    *,
    state_snapshot: tuple[dict, str] | None = None,
) -> dict:
    result = {
        "status": "pending",
        "attempt": 0,
        "next_retry_at": 0,
        "error_code": "",
        "permanent": False,
        "registered_at": 0,
        "review_url": None,
        "cover_url": None,
    }
    if RUN_ID_RE.fullmatch(run_id) is None:
        result.update(status="failed", error_code="state_invalid")
        return result
    rows, state_error = (
        state_snapshot if state_snapshot is not None
        else load_reel_review_transport_state()
    )
    if state_error:
        result.update(status="failed", error_code=state_error)
        return result
    row = rows.get(run_id)
    if row is None:
        return result
    if not isinstance(row, dict) or row.get("status") not in {"pending", "failed", "ready"}:
        result.update(status="failed", error_code="state_invalid")
        return result
    error_code = str(row.get("error_code") or "")
    permanent = row.get("permanent", False)
    if ERROR_RE.fullmatch(error_code) is None:
        error_code = "state_invalid"
    if not isinstance(permanent, bool) or (permanent and row["status"] != "failed"):
        result.update(status="failed", error_code="state_invalid", permanent=True)
        return result
    result.update(
        status=row["status"],
        attempt=_bounded_int(row.get("attempt"), maximum=10_000),
        next_retry_at=_bounded_int(row.get("next_retry_at")),
        error_code=error_code,
        permanent=permanent,
        registered_at=_bounded_int(row.get("registered_at")),
    )
    if row["status"] != "ready":
        return result

    proxy_row = roles.get("proxy")
    cover_row = roles.get("cover")
    proxy_state = row.get("proxy")
    cover_state = row.get("cover")
    if not _artifact_matches(proxy_state, proxy_row) or not _artifact_matches(cover_state, cover_row):
        result.update(status="pending", error_code="new_artifact_pending", registered_at=0)
        return result
    proxy_digest = str(proxy_row.get("content_hash") or "")
    cover_digest = str(cover_row.get("content_hash") or "")
    proxy_key = _verified_key(proxy_state.get("key"), run_id=run_id, digest=proxy_digest, role="proxy")
    cover_key = _verified_key(cover_state.get("key"), run_id=run_id, digest=cover_digest, role="cover")
    if proxy_key is None or cover_key is None:
        result.update(status="failed", error_code="state_invalid", registered_at=0)
        return result
    # R2 keys prove that registration completed, but Castle must never turn
    # them into public CDN URLs. The authenticated ai-sister admin API owns
    # signed-URL issuance; Castle's own review page keeps using its local copy.
    return result
