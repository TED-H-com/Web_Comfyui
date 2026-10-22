"""Durable, provider-neutral storage for Castle's provider fabric.

The live Castle database and this database intentionally have separate files.
That boundary lets the provider fabric evolve and be restored without coupling
provider task recovery to UI sessions.  Only *references* to credentials are
stored here; resolved credentials are never accepted by this module.

The store uses a connection per operation.  Transactions that reserve work use
``BEGIN IMMEDIATE`` so two service processes cannot both claim the same durable
request identity, while independent requests may execute concurrently.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from .config import (
    SecretReference,
    is_safe_durable_file_secret_locator,
)
from .models import (
    CapabilityOffering,
    CharacterRef,
    MediaKind,
    RouteDecision,
    RouteIntent,
    TaskState,
    VisualClass,
)

if TYPE_CHECKING:
    from .clipmode_import import ClipModeImportBundle
    from .owner_import import OwnerImportDecisionPlan
    from .owner_import_store import StoreOwnerImportPlan
    from .media_materializer import MaterializedImport


SCHEMA_VERSION = 16
SYSTEM_LOCAL_OWNER_ID = "castle-system:local-comfy"
CHARACTER_LORA_CAPABILITY_ID = "photoshoot.sdxl-character-lora.v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_PUBLIC_INTENT_ID_RE = re.compile(r"^intent:[0-9a-f]{32}$")
_MIME_RE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TRIGGER_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_AUTOIP_TASK_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_AUTOIP_DEFERRED_DETAIL_RE = re.compile(
    r"^deferred_provider_rejection:http_429:"
    r"[a-z0-9][a-z0-9._-]{0,79}:retry_after_(?:0|[1-9][0-9]{0,4})$"
)
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "secret_value",
        "session",
        "session_cookie",
        "token",
        "webhook_secret",
        "xsrf_token",
    }
)
_SECRET_SUFFIXES = ("_api_key", "_cookie", "_password", "_secret", "_token")
_STORED_VISUAL_CLASSES = frozenset({"anime", "realistic", "illustration", "unknown"})
_EXECUTABLE_VISUAL_CLASSES = _STORED_VISUAL_CLASSES - {"unknown"}
_UNSET = object()
_IMPORT_MEMBERSHIP_DISPOSITIONS = frozenset({"created", "reused", "mapped"})
_IMPORT_BLOB_ROLES = frozenset({"original", "preview_rendition"})
_IMPORT_SIGNATURE_FAMILIES = frozenset(
    {
        "avif",
        "flac",
        "gif",
        "jpeg",
        "mp4",
        "mpeg_audio",
        "ogg",
        "png",
        "quicktime",
        "utf8_text",
        "wav",
        "webm",
        "webp",
    }
)
_SAFE_PREVIEW_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_AUTOIP_HISTORY_MIMES_BY_KIND = {
    "image": frozenset({
        "image/avif", "image/gif", "image/jpeg", "image/png", "image/webp",
    }),
    "video": frozenset({"video/mp4", "video/quicktime", "video/webm"}),
    "audio": frozenset({
        "audio/flac", "audio/mp4", "audio/mpeg", "audio/ogg", "audio/wav",
        "audio/x-wav",
    }),
}


class ProviderFabricStoreError(RuntimeError):
    """Base class for store contract failures."""


class StoreConflictError(ProviderFabricStoreError):
    """A uniqueness or concurrent-reservation invariant was violated."""


class InvalidTaskTransition(ProviderFabricStoreError):
    """An external task attempted an invalid lifecycle transition."""


class SecretFieldError(ValueError):
    """A supposedly public payload contained a credential-shaped field."""


def _credential_reference(value: object) -> SecretReference:
    if not isinstance(value, SecretReference):
        raise SecretFieldError("credential_ref must be an opaque SecretReference")
    if value.kind.value == "file" and not is_safe_durable_file_secret_locator(
        value.locator
    ):
        raise SecretFieldError("file credential locator is unsafe")
    return value


def default_database_path() -> Path:
    """Return the provider-fabric database path without creating it."""

    configured = os.environ.get("CASTLE_PROVIDER_DATABASE_PATH")
    if configured:
        return Path(configured).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "web-comfyui" / "provider-fabric" / "provider-fabric.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _stable_id(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return normalized


def matches_deterministic_autoip_history_identity(
    owner_user_id: object,
    *,
    provider_id: object,
    account_id: object,
    projection_id: object,
    external_asset_id: object,
    asset_version_id: object,
    provider_version_id: object,
    metadata: object,
) -> bool:
    """Prove the exact IDs emitted by verified AutoIP-history adoption."""

    if (
        provider_id != "autoip"
        or not isinstance(owner_user_id, str)
        or not _ID_RE.fullmatch(owner_user_id)
        or not isinstance(account_id, str)
        or not _ID_RE.fullmatch(account_id)
        or not isinstance(projection_id, str)
        or not _ID_RE.fullmatch(projection_id)
        or not isinstance(asset_version_id, str)
        or not _ID_RE.fullmatch(asset_version_id)
        or not isinstance(external_asset_id, str)
        or not isinstance(provider_version_id, str)
        or not isinstance(metadata, Mapping)
        or metadata.get("source") != "autoip_history"
        or metadata.get("classification_required") is not True
    ):
        return False
    task_id = metadata.get("task_id")
    output_index = metadata.get("output_index")
    if (
        not isinstance(task_id, str)
        or _AUTOIP_TASK_UUID_RE.fullmatch(task_id) is None
        or type(output_index) is not int
        or output_index < 0
    ):
        return False
    suffix = hashlib.sha256(
        "\0".join((
            owner_user_id, account_id, task_id, str(output_index),
        )).encode("utf-8")
    ).hexdigest()[:32]
    return (
        projection_id == f"autoip-history:{suffix}"
        and asset_version_id == f"assetver:autoip-history:{suffix}"
        and external_asset_id == f"history:{task_id}:output:{output_index}"
        and provider_version_id == f"history:{task_id}:{output_index}"
    )


def is_supported_autoip_history_version(
    media_kind: object,
    mime_type: object,
    byte_size: object,
) -> bool:
    """Return whether adoption and local source resolution share this format."""

    return bool(
        isinstance(media_kind, str)
        and isinstance(mime_type, str)
        and mime_type in _AUTOIP_HISTORY_MIMES_BY_KIND.get(media_kind, ())
        and isinstance(byte_size, int)
        and not isinstance(byte_size, bool)
        and byte_size > 0
    )


def is_supported_autoip_history_visual_version(
    media_kind: object,
    mime_type: object,
    byte_size: object,
) -> bool:
    """Return whether a supported history version can carry visual evidence."""

    return bool(
        media_kind in {"image", "video"}
        and is_supported_autoip_history_version(media_kind, mime_type, byte_size)
    )


def _sqlite_matches_deterministic_autoip_history_identity(
    owner_user_id: object,
    provider_id: object,
    account_id: object,
    projection_id: object,
    external_asset_id: object,
    asset_version_id: object,
    provider_version_id: object,
    metadata_json: object,
) -> int:
    try:
        metadata = json.loads(metadata_json) if isinstance(metadata_json, str) else None
        return int(matches_deterministic_autoip_history_identity(
            owner_user_id,
            provider_id=provider_id,
            account_id=account_id,
            projection_id=projection_id,
            external_asset_id=external_asset_id,
            asset_version_id=asset_version_id,
            provider_version_id=provider_version_id,
            metadata=metadata,
        ))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0


def _external_id(value: str, label: str = "external_id") -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 500 or "\x00" in normalized:
        raise ValueError(f"{label} is invalid")
    return normalized


def _optional_text(value: str | None, label: str, *, maximum: int = 1000) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{label} is invalid")
    return normalized


def _visual_value(value: VisualClass | str, *, executable: bool = False) -> str:
    """Return the canonical visual class.

    ``real`` is accepted only as a temporary compatibility spelling for model
    objects created before the canonical enum was expanded; it is never stored.
    """

    raw = str(value.value if isinstance(value, VisualClass) else value).strip().lower()
    canonical = "realistic" if raw == "real" else raw
    allowed = _EXECUTABLE_VISUAL_CLASSES if executable else _STORED_VISUAL_CLASSES
    if canonical not in allowed:
        if executable and canonical == "unknown":
            raise ValueError("unknown visual class cannot be executed or routed")
        raise ValueError("visual_class is invalid")
    return canonical


def _secret_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _assert_public_data(value: Any, path: str = "payload") -> None:
    """Reject fields and URL query parameters that commonly carry secrets."""

    if isinstance(value, SecretReference):
        raise SecretFieldError(f"{path} cannot contain a credential reference")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _secret_key(key):
                raise SecretFieldError(f"{path}.{key} is a forbidden secret field")
            _assert_public_data(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_public_data(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            raise SecretFieldError(f"{path} URL cannot contain credentials")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if _secret_key(key) or key.lower().startswith(("x-amz-", "x-goog-")):
                raise SecretFieldError(f"{path} URL cannot contain signed or secret query fields")


def _json(value: Any) -> str:
    _assert_public_data(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("value is not canonical JSON data") from error


def _from_json(value: str) -> Any:
    return json.loads(value)


def _new_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def _sha256(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


def _model_basename(value: str, label: str) -> str:
    """Validate a model filename without accepting a filesystem locator."""

    normalized = str(value or "").strip()
    if (
        len(normalized) > 128
        or _MODEL_SEGMENT_RE.fullmatch(normalized) is None
        or not normalized.lower().endswith(".safetensors")
    ):
        raise ValueError(f"{label} must be a safe safetensors filename")
    return normalized


def _relative_model_name(value: str, label: str = "model_name") -> str:
    """Validate a POSIX name relative to the server-owned LoRA model root.

    This deliberately returns a *name*, not a path.  Browser/API callers can
    never use it to select an arbitrary host file, and executors must resolve
    it beneath their configured LoRA root before verifying the stored digest.
    """

    normalized = str(value or "").strip()
    parts = normalized.split("/")
    if (
        not normalized
        or len(normalized) > 300
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in parts)
        or any(_MODEL_SEGMENT_RE.fullmatch(part) is None for part in parts)
        or not parts[-1].lower().endswith(".safetensors")
    ):
        raise ValueError(f"{label} must be a safe relative safetensors model name")
    return normalized


def _model_strength(value: float, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 2
    ):
        raise ValueError(f"{label} must be between 0 and 2")
    return float(value)


def _utc_timestamp(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not _RFC3339_UTC_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        datetime.fromisoformat(normalized[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be a real RFC3339 UTC instant") from error
    return normalized


_MIGRATION_1 = r"""
CREATE TABLE IF NOT EXISTS provider_accounts (
    account_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_kind TEXT NOT NULL CHECK (credential_kind IN ('env', 'file')),
    credential_locator TEXT NOT NULL CHECK (length(credential_locator) BETWEEN 1 AND 4096),
    api_base TEXT,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (length(account_id) BETWEEN 1 AND 128 AND account_id = lower(account_id)),
    CHECK (length(provider_id) BETWEEN 1 AND 128 AND provider_id = lower(provider_id)),
    UNIQUE (provider_id, account_id)
);

CREATE TABLE IF NOT EXISTS capability_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    source_revision TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    captured_at TEXT NOT NULL,
    UNIQUE (account_id, source_revision),
    UNIQUE (account_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS capability_offerings (
    snapshot_id TEXT NOT NULL REFERENCES capability_snapshots(snapshot_id),
    capability TEXT NOT NULL CHECK (capability IN (
        'text_to_image', 'image_to_image', 'image_to_video', 'text_to_video',
        'lip_sync', 'dance', 'multi_subject_video', 'character_training',
        'voice_clone', 'text_to_speech'
    )),
    variant_id TEXT NOT NULL,
    visual_class TEXT NOT NULL CHECK (visual_class IN ('anime', 'realistic', 'illustration')),
    evidence TEXT NOT NULL CHECK (evidence IN ('unproven', 'probed', 'verified')),
    evidence_id TEXT,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    priority INTEGER NOT NULL,
    option_keys_json TEXT NOT NULL CHECK (json_valid(option_keys_json)),
    PRIMARY KEY (snapshot_id, capability, variant_id, visual_class),
    CHECK (evidence != 'verified' OR evidence_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS canonical_characters (
    character_id TEXT PRIMARY KEY,
    visual_class TEXT NOT NULL CHECK (visual_class IN ('anime', 'realistic', 'illustration', 'unknown')),
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_character_links (
    link_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    external_character_id TEXT NOT NULL,
    character_id TEXT NOT NULL REFERENCES canonical_characters(character_id),
    visual_class TEXT NOT NULL CHECK (visual_class IN ('anime', 'realistic', 'illustration', 'unknown')),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    linked_at TEXT NOT NULL,
    UNIQUE (account_id, external_character_id)
);

CREATE TABLE IF NOT EXISTS remote_asset_projections (
    projection_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    external_asset_id TEXT NOT NULL,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('image', 'video', 'audio', 'text', 'file')),
    visual_class TEXT CHECK (visual_class IS NULL OR visual_class IN (
        'anime', 'realistic', 'illustration', 'unknown'
    )),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    remote_created_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (account_id, external_asset_id)
);

CREATE TABLE IF NOT EXISTS remote_asset_versions (
    version_id TEXT PRIMARY KEY,
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    provider_version_id TEXT,
    source_uri TEXT NOT NULL CHECK (length(source_uri) BETWEEN 1 AND 8192),
    preview_uri TEXT,
    mime_type TEXT,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
    duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    observed_at TEXT NOT NULL,
    UNIQUE (projection_id, version_number),
    UNIQUE (projection_id, provider_version_id)
);

CREATE TABLE IF NOT EXISTS asset_character_links (
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    character_id TEXT NOT NULL REFERENCES canonical_characters(character_id),
    linked_at TEXT NOT NULL,
    PRIMARY KEY (projection_id, character_id)
);

CREATE TABLE IF NOT EXISTS generation_intents (
    intent_id TEXT PRIMARY KEY,
    request_key TEXT NOT NULL UNIQUE,
    capability TEXT NOT NULL CHECK (capability IN (
        'text_to_image', 'image_to_image', 'image_to_video', 'text_to_video',
        'lip_sync', 'dance', 'multi_subject_video', 'character_training',
        'voice_clone', 'text_to_speech'
    )),
    visual_class TEXT NOT NULL CHECK (visual_class IN ('anime', 'realistic', 'illustration')),
    intent_json TEXT NOT NULL CHECK (json_valid(intent_json)),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_decisions (
    decision_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL REFERENCES generation_intents(intent_id),
    selected_account_id TEXT REFERENCES provider_accounts(account_id),
    selected_provider_id TEXT,
    selected_variant_id TEXT,
    policy_version TEXT NOT NULL,
    candidate_keys_json TEXT NOT NULL CHECK (json_valid(candidate_keys_json)),
    rejections_json TEXT NOT NULL CHECK (json_valid(rejections_json)),
    quote_json TEXT CHECK (quote_json IS NULL OR json_valid(quote_json)),
    created_at TEXT NOT NULL,
    CHECK ((selected_provider_id IS NULL) = (selected_variant_id IS NULL)),
    CHECK (selected_account_id IS NULL OR selected_provider_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS submission_outbox (
    outbox_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL REFERENCES generation_intents(intent_id),
    decision_id TEXT NOT NULL REFERENCES route_decisions(decision_id),
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    client_request_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'claimed', 'submitted', 'submission_uncertain', 'done', 'abandoned'
    )),
    request_payload_json TEXT NOT NULL CHECK (json_valid(request_payload_json)),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, client_request_id)
);

CREATE TABLE IF NOT EXISTS external_tasks (
    task_id TEXT PRIMARY KEY,
    outbox_id TEXT NOT NULL UNIQUE REFERENCES submission_outbox(outbox_id),
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    provider_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    external_task_id TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'created', 'submitted', 'queued', 'running', 'completed', 'failed',
        'cancelled', 'timeout', 'submission_uncertain'
    )),
    state_revision INTEGER NOT NULL DEFAULT 0 CHECK (state_revision >= 0),
    credits_charged INTEGER CHECK (credits_charged IS NULL OR credits_charged >= 0),
    remaining_credits INTEGER CHECK (remaining_credits IS NULL OR remaining_credits >= 0),
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT,
    UNIQUE (account_id, client_request_id),
    UNIQUE (account_id, external_task_id),
    CHECK (state IN ('created', 'submission_uncertain') OR external_task_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_autoip_task_in_flight_per_account
ON external_tasks(account_id)
WHERE provider_id = 'autoip'
  AND state IN ('created', 'submission_uncertain', 'submitted', 'queued', 'running');

CREATE TABLE IF NOT EXISTS external_task_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES external_tasks(task_id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    external_task_id TEXT,
    detail TEXT NOT NULL DEFAULT '',
    happened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_lineage (
    parent_projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    child_projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    relation TEXT NOT NULL,
    intent_id TEXT REFERENCES generation_intents(intent_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (parent_projection_id, child_projection_id, relation),
    CHECK (parent_projection_id != child_projection_id)
);

CREATE TABLE IF NOT EXISTS credit_ledger (
    ledger_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    task_id TEXT REFERENCES external_tasks(task_id),
    intent_id TEXT REFERENCES generation_intents(intent_id),
    entry_kind TEXT NOT NULL CHECK (entry_kind IN (
        'reservation', 'charge', 'refund', 'adjustment', 'balance_snapshot'
    )),
    delta INTEGER NOT NULL,
    balance_after INTEGER CHECK (balance_after IS NULL OR balance_after >= 0),
    idempotency_key TEXT NOT NULL,
    detail_json TEXT NOT NULL CHECK (json_valid(detail_json)),
    created_at TEXT NOT NULL,
    UNIQUE (account_id, idempotency_key),
    CHECK (entry_kind != 'charge' OR delta <= 0),
    CHECK (entry_kind != 'refund' OR delta >= 0),
    CHECK (entry_kind != 'balance_snapshot' OR delta = 0)
);

CREATE TRIGGER IF NOT EXISTS provider_character_visual_insert
BEFORE INSERT ON provider_character_links
WHEN NEW.visual_class != (SELECT visual_class FROM canonical_characters WHERE character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'provider character visual class mismatch'); END;

CREATE TRIGGER IF NOT EXISTS provider_character_visual_update
BEFORE UPDATE ON provider_character_links
WHEN NEW.visual_class != (SELECT visual_class FROM canonical_characters WHERE character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'provider character visual class mismatch'); END;

DROP TRIGGER IF EXISTS canonical_character_visual_guard;
DROP TRIGGER IF EXISTS canonical_character_classification_cascade;
CREATE TRIGGER canonical_character_visual_guard
BEFORE UPDATE OF visual_class ON canonical_characters
WHEN OLD.visual_class != NEW.visual_class
 AND NOT (OLD.visual_class = 'unknown' AND NEW.visual_class IN (
    'anime', 'realistic', 'illustration'
 ))
BEGIN SELECT RAISE(ABORT, 'classified character visual class is immutable'); END;

CREATE TRIGGER canonical_character_classification_cascade
AFTER UPDATE OF visual_class ON canonical_characters
WHEN OLD.visual_class = 'unknown' AND NEW.visual_class != 'unknown'
BEGIN
    UPDATE provider_character_links
       SET visual_class = NEW.visual_class
     WHERE character_id = NEW.character_id;
    UPDATE remote_asset_projections
       SET visual_class = NEW.visual_class
     WHERE projection_id IN (
        SELECT projection_id FROM asset_character_links
        WHERE character_id = NEW.character_id
     );
END;

CREATE TRIGGER IF NOT EXISTS asset_character_visual_insert
BEFORE INSERT ON asset_character_links
WHEN (SELECT visual_class FROM remote_asset_projections WHERE projection_id = NEW.projection_id) IS NULL
  OR (SELECT visual_class FROM remote_asset_projections WHERE projection_id = NEW.projection_id)
     != (SELECT visual_class FROM canonical_characters WHERE character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'asset character visual class mismatch'); END;

CREATE TRIGGER IF NOT EXISTS projection_visual_guard
BEFORE UPDATE OF visual_class ON remote_asset_projections
WHEN EXISTS (
    SELECT 1 FROM asset_character_links acl
    JOIN canonical_characters cc ON cc.character_id = acl.character_id
    WHERE acl.projection_id = OLD.projection_id
      AND (NEW.visual_class IS NULL OR NEW.visual_class != cc.visual_class)
)
BEGIN SELECT RAISE(ABORT, 'linked asset visual class is immutable'); END;

CREATE TRIGGER IF NOT EXISTS route_account_provider_insert
BEFORE INSERT ON route_decisions
WHEN NEW.selected_account_id IS NOT NULL
 AND NEW.selected_provider_id != (
    SELECT provider_id FROM provider_accounts WHERE account_id = NEW.selected_account_id
 )
BEGIN SELECT RAISE(ABORT, 'route account provider mismatch'); END;

CREATE TRIGGER IF NOT EXISTS outbox_decision_guard
BEFORE INSERT ON submission_outbox
WHEN NEW.intent_id IS NOT (SELECT intent_id FROM route_decisions WHERE decision_id = NEW.decision_id)
 OR NEW.account_id IS NOT (SELECT selected_account_id FROM route_decisions WHERE decision_id = NEW.decision_id)
BEGIN SELECT RAISE(ABORT, 'outbox route mismatch'); END;

CREATE TRIGGER IF NOT EXISTS outbox_identity_immutable
BEFORE UPDATE ON submission_outbox
WHEN OLD.intent_id != NEW.intent_id OR OLD.decision_id != NEW.decision_id
 OR OLD.account_id != NEW.account_id OR OLD.client_request_id != NEW.client_request_id
 OR OLD.request_payload_json != NEW.request_payload_json
BEGIN SELECT RAISE(ABORT, 'outbox identity is immutable'); END;

CREATE TRIGGER IF NOT EXISTS outbox_state_transition
BEFORE UPDATE OF state ON submission_outbox
WHEN OLD.state != NEW.state AND NOT (
    (OLD.state = 'pending' AND NEW.state IN ('claimed', 'abandoned')) OR
    (OLD.state = 'claimed' AND NEW.state IN ('pending', 'submitted', 'submission_uncertain', 'abandoned')) OR
    (OLD.state = 'submission_uncertain' AND NEW.state IN ('submitted', 'done', 'abandoned')) OR
    (OLD.state = 'submitted' AND NEW.state IN ('done', 'abandoned'))
)
BEGIN SELECT RAISE(ABORT, 'invalid outbox state transition'); END;

CREATE TRIGGER IF NOT EXISTS external_task_binding_insert
BEFORE INSERT ON external_tasks
WHEN NEW.account_id != (SELECT account_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.client_request_id != (SELECT client_request_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.provider_id != (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
BEGIN SELECT RAISE(ABORT, 'external task binding mismatch'); END;

CREATE TRIGGER IF NOT EXISTS external_task_identity_immutable
BEFORE UPDATE ON external_tasks
WHEN OLD.outbox_id != NEW.outbox_id OR OLD.account_id != NEW.account_id
 OR OLD.provider_id != NEW.provider_id OR OLD.client_request_id != NEW.client_request_id
BEGIN SELECT RAISE(ABORT, 'external task identity is immutable'); END;

CREATE TRIGGER IF NOT EXISTS external_task_state_transition
BEFORE UPDATE OF state ON external_tasks
WHEN OLD.state != NEW.state AND NOT (
    (OLD.state = 'created' AND NEW.state IN ('submitted', 'submission_uncertain', 'cancelled')) OR
    (OLD.state = 'submission_uncertain' AND NEW.state IN (
        'submitted', 'queued', 'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'submitted' AND NEW.state IN (
        'queued', 'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'queued' AND NEW.state IN (
        'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'running' AND NEW.state IN (
        'completed', 'failed', 'cancelled', 'timeout'
    ))
)
BEGIN SELECT RAISE(ABORT, 'invalid external task state transition'); END;

CREATE TRIGGER IF NOT EXISTS capability_snapshots_no_update
BEFORE UPDATE ON capability_snapshots BEGIN SELECT RAISE(ABORT, 'capability snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS capability_snapshots_no_delete
BEFORE DELETE ON capability_snapshots BEGIN SELECT RAISE(ABORT, 'capability snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS capability_offerings_no_update
BEFORE UPDATE ON capability_offerings BEGIN SELECT RAISE(ABORT, 'capability offering is immutable'); END;
CREATE TRIGGER IF NOT EXISTS capability_offerings_no_delete
BEFORE DELETE ON capability_offerings BEGIN SELECT RAISE(ABORT, 'capability offering is immutable'); END;
CREATE TRIGGER IF NOT EXISTS generation_intents_no_update
BEFORE UPDATE ON generation_intents BEGIN SELECT RAISE(ABORT, 'generation intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS generation_intents_no_delete
BEFORE DELETE ON generation_intents BEGIN SELECT RAISE(ABORT, 'generation intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS route_decisions_no_update
BEFORE UPDATE ON route_decisions BEGIN SELECT RAISE(ABORT, 'route decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS route_decisions_no_delete
BEFORE DELETE ON route_decisions BEGIN SELECT RAISE(ABORT, 'route decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS task_events_no_update
BEFORE UPDATE ON external_task_events BEGIN SELECT RAISE(ABORT, 'task event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS task_events_no_delete
BEFORE DELETE ON external_task_events BEGIN SELECT RAISE(ABORT, 'task event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS remote_asset_versions_no_update
BEFORE UPDATE ON remote_asset_versions BEGIN SELECT RAISE(ABORT, 'asset version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS remote_asset_versions_no_delete
BEFORE DELETE ON remote_asset_versions BEGIN SELECT RAISE(ABORT, 'asset version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS asset_lineage_no_update
BEFORE UPDATE ON asset_lineage BEGIN SELECT RAISE(ABORT, 'asset lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS asset_lineage_no_delete
BEFORE DELETE ON asset_lineage BEGIN SELECT RAISE(ABORT, 'asset lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS credit_ledger_no_update
BEFORE UPDATE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'credit ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS credit_ledger_no_delete
BEFORE DELETE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'credit ledger is immutable'); END;
"""


_MIGRATION_2 = r"""
CREATE TABLE IF NOT EXISTS webhook_replay_claims (
    provider_id TEXT NOT NULL,
    replay_key TEXT NOT NULL CHECK (
        length(replay_key) = 64 AND replay_key = lower(replay_key)
    ),
    signed_at INTEGER NOT NULL CHECK (signed_at > 0),
    expires_at INTEGER NOT NULL CHECK (expires_at >= signed_at),
    external_task_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider_id, replay_key),
    CHECK (length(provider_id) BETWEEN 1 AND 128 AND provider_id = lower(provider_id)),
    CHECK (length(external_task_id) BETWEEN 1 AND 500)
);

CREATE INDEX IF NOT EXISTS webhook_replay_expiry
ON webhook_replay_claims(expires_at);

CREATE TRIGGER IF NOT EXISTS webhook_replay_no_update
BEFORE UPDATE ON webhook_replay_claims
BEGIN SELECT RAISE(ABORT, 'webhook replay claim is immutable'); END;
"""


_MIGRATION_3 = r"""
-- Local providers do not have a credential.  Rebuild the account table so
-- `none` is represented structurally instead of by a fake environment secret.
-- The caller disables foreign_keys around this transaction.  Do not rename
-- the old parent first: SQLite would rewrite child foreign keys to that
-- temporary name.  The create/copy/drop/rename order leaves every child
-- reference pointing at the final `provider_accounts` table.
CREATE TABLE provider_accounts_v3_new (
    account_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_kind TEXT NOT NULL CHECK (credential_kind IN ('none', 'env', 'file')),
    credential_locator TEXT CHECK (
        (credential_kind = 'none' AND credential_locator IS NULL)
        OR
        (credential_kind IN ('env', 'file') AND length(credential_locator) BETWEEN 1 AND 4096)
    ),
    api_base TEXT,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (length(account_id) BETWEEN 1 AND 128 AND account_id = lower(account_id)),
    CHECK (length(provider_id) BETWEEN 1 AND 128 AND provider_id = lower(provider_id)),
    CHECK (provider_id = 'local_comfy' OR credential_kind != 'none'),
    CHECK (provider_id != 'local_comfy' OR credential_kind = 'none'),
    UNIQUE (provider_id, account_id)
);

INSERT INTO provider_accounts_v3_new(
    account_id, provider_id, display_name, credential_kind,
    credential_locator, api_base, enabled, created_at, updated_at
)
SELECT
    account_id,
    provider_id,
    display_name,
    CASE WHEN provider_id = 'local_comfy' THEN 'none' ELSE credential_kind END,
    CASE WHEN provider_id = 'local_comfy' THEN NULL ELSE credential_locator END,
    api_base,
    enabled,
    created_at,
    updated_at
FROM provider_accounts;

-- These two child-table triggers name the parent in their bodies.  SQLite
-- validates trigger SQL after DROP, so remove and recreate the exact guards
-- around the short interval where the parent name is absent.
DROP TRIGGER route_account_provider_insert;
DROP TRIGGER external_task_binding_insert;
DROP TABLE provider_accounts;
ALTER TABLE provider_accounts_v3_new RENAME TO provider_accounts;

CREATE TRIGGER route_account_provider_insert
BEFORE INSERT ON route_decisions
WHEN NEW.selected_account_id IS NOT NULL
 AND NEW.selected_provider_id != (
    SELECT provider_id FROM provider_accounts WHERE account_id = NEW.selected_account_id
 )
BEGIN SELECT RAISE(ABORT, 'route account provider mismatch'); END;

CREATE TRIGGER external_task_binding_insert
BEFORE INSERT ON external_tasks
WHEN NEW.account_id != (SELECT account_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.client_request_id != (SELECT client_request_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.provider_id != (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
BEGIN SELECT RAISE(ABORT, 'external task binding mismatch'); END;

CREATE TEMP TABLE provider_fabric_fk_guard(
    violation_count INTEGER NOT NULL CHECK (violation_count = 0)
);
INSERT INTO provider_fabric_fk_guard
SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE provider_fabric_fk_guard;
"""


_MIGRATION_4 = r"""
-- ClipMode is metadata-import-only until a documented public API contract is
-- available.  Give those imports a credentialless account namespace without
-- implying that live sync or generation is configured.  The database itself
-- keeps such accounts disabled, in addition to the application-level guard.
CREATE TABLE provider_accounts_v4_new (
    account_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_kind TEXT NOT NULL CHECK (credential_kind IN ('none', 'env', 'file')),
    credential_locator TEXT CHECK (
        (credential_kind = 'none' AND credential_locator IS NULL)
        OR
        (credential_kind IN ('env', 'file') AND length(credential_locator) BETWEEN 1 AND 4096)
    ),
    api_base TEXT,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (length(account_id) BETWEEN 1 AND 128 AND account_id = lower(account_id)),
    CHECK (length(provider_id) BETWEEN 1 AND 128 AND provider_id = lower(provider_id)),
    CHECK (provider_id IN ('local_comfy', 'clipmode') OR credential_kind != 'none'),
    CHECK (provider_id != 'local_comfy' OR credential_kind = 'none'),
    CHECK (NOT (provider_id = 'clipmode' AND credential_kind = 'none' AND enabled = 1)),
    UNIQUE (provider_id, account_id)
);

INSERT INTO provider_accounts_v4_new(
    account_id, provider_id, display_name, credential_kind,
    credential_locator, api_base, enabled, created_at, updated_at
)
SELECT
    account_id,
    provider_id,
    display_name,
    credential_kind,
    credential_locator,
    api_base,
    enabled,
    created_at,
    updated_at
FROM provider_accounts;

DROP TRIGGER route_account_provider_insert;
DROP TRIGGER external_task_binding_insert;
DROP TABLE provider_accounts;
ALTER TABLE provider_accounts_v4_new RENAME TO provider_accounts;

CREATE TRIGGER route_account_provider_insert
BEFORE INSERT ON route_decisions
WHEN NEW.selected_account_id IS NOT NULL
 AND NEW.selected_provider_id != (
    SELECT provider_id FROM provider_accounts WHERE account_id = NEW.selected_account_id
 )
BEGIN SELECT RAISE(ABORT, 'route account provider mismatch'); END;

CREATE TRIGGER external_task_binding_insert
BEFORE INSERT ON external_tasks
WHEN NEW.account_id != (SELECT account_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.client_request_id != (SELECT client_request_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.provider_id != (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
BEGIN SELECT RAISE(ABORT, 'external task binding mismatch'); END;

CREATE TEMP TABLE provider_fabric_fk_guard(
    violation_count INTEGER NOT NULL CHECK (violation_count = 0)
);
INSERT INTO provider_fabric_fk_guard
SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE provider_fabric_fk_guard;
"""


_MIGRATION_5 = r"""
-- Bind every browser-readable confirmed intent to the stable, non-PII Castle
-- actor that confirmed it.  Older service confirmations already persisted the
-- actor in payload_json; only values that exactly satisfy the canonical stable
-- identifier grammar are promoted into this explicit ACL.  Missing or malformed
-- legacy values remain unowned and therefore inaccessible through owner-scoped
-- APIs.
CREATE TABLE generation_intent_owners (
    intent_id TEXT PRIMARY KEY REFERENCES generation_intents(intent_id),
    owner_user_id TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    CHECK (length(owner_user_id) BETWEEN 1 AND 128),
    CHECK (instr(owner_user_id, char(0)) = 0),
    CHECK (owner_user_id = lower(owner_user_id)),
    CHECK (substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (owner_user_id NOT GLOB '*[^a-z0-9._:-]*')
);

INSERT INTO generation_intent_owners(intent_id, owner_user_id, bound_at)
SELECT
    intent_id,
    json_extract(payload_json, '$.owner_user_id'),
    created_at
FROM generation_intents
WHERE json_type(payload_json, '$.owner_user_id') = 'text'
  AND length(json_extract(payload_json, '$.owner_user_id')) BETWEEN 1 AND 128
  AND instr(json_extract(payload_json, '$.owner_user_id'), char(0)) = 0
  AND json_extract(payload_json, '$.owner_user_id') =
      lower(json_extract(payload_json, '$.owner_user_id'))
  AND substr(json_extract(payload_json, '$.owner_user_id'), 1, 1) GLOB '[a-z0-9]'
  AND json_extract(payload_json, '$.owner_user_id') NOT GLOB '*[^a-z0-9._:-]*';

CREATE INDEX generation_intent_owners_recent
ON generation_intent_owners(owner_user_id, bound_at DESC, intent_id DESC);

CREATE TRIGGER generation_intent_owner_payload_guard
BEFORE INSERT ON generation_intent_owners
WHEN json_type(
         (SELECT payload_json FROM generation_intents WHERE intent_id = NEW.intent_id),
         '$.owner_user_id'
     ) IS NOT 'text'
  OR NEW.owner_user_id IS NOT json_extract(
         (SELECT payload_json FROM generation_intents WHERE intent_id = NEW.intent_id),
         '$.owner_user_id'
     )
BEGIN SELECT RAISE(ABORT, 'intent owner does not match immutable payload'); END;

CREATE TRIGGER generation_intent_owners_no_update
BEFORE UPDATE ON generation_intent_owners
BEGIN SELECT RAISE(ABORT, 'intent owner is immutable'); END;

CREATE TRIGGER generation_intent_owners_no_delete
BEFORE DELETE ON generation_intent_owners
BEGIN SELECT RAISE(ABORT, 'intent owner is immutable'); END;
"""


_MIGRATION_6 = r"""
-- Canonical characters and provider-projected assets were intentionally
-- created without a guessed owner in the foundation schema.  Add explicit,
-- immutable ACL bindings now.  Legacy rows are *not* backfilled: an unowned
-- row remains invisible to owner-scoped browser APIs until a separate,
-- reviewed adoption workflow exists.
CREATE TABLE provider_account_owners (
    account_id TEXT PRIMARY KEY REFERENCES provider_accounts(account_id),
    owner_user_id TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    CHECK (length(owner_user_id) BETWEEN 1 AND 128),
    CHECK (instr(owner_user_id, char(0)) = 0),
    CHECK (owner_user_id = lower(owner_user_id)),
    CHECK (substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (owner_user_id NOT GLOB '*[^a-z0-9._:-]*')
);

CREATE INDEX provider_account_owners_by_user
ON provider_account_owners(owner_user_id, account_id);

CREATE TABLE canonical_character_owners (
    character_id TEXT PRIMARY KEY REFERENCES canonical_characters(character_id),
    owner_user_id TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    CHECK (length(owner_user_id) BETWEEN 1 AND 128),
    CHECK (instr(owner_user_id, char(0)) = 0),
    CHECK (owner_user_id = lower(owner_user_id)),
    CHECK (substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (owner_user_id NOT GLOB '*[^a-z0-9._:-]*')
);

CREATE INDEX canonical_character_owners_by_user
ON canonical_character_owners(owner_user_id, character_id);

CREATE TABLE remote_asset_owners (
    projection_id TEXT PRIMARY KEY REFERENCES remote_asset_projections(projection_id),
    owner_user_id TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    CHECK (length(owner_user_id) BETWEEN 1 AND 128),
    CHECK (instr(owner_user_id, char(0)) = 0),
    CHECK (owner_user_id = lower(owner_user_id)),
    CHECK (substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (owner_user_id NOT GLOB '*[^a-z0-9._:-]*')
);

CREATE INDEX remote_asset_owners_by_user
ON remote_asset_owners(owner_user_id, projection_id);

-- Profile selection is a Castle asset relationship, never a provider hotlink.
-- The selected projection must already be an image linked to the character;
-- callers can replace the selection without deleting the prior asset.
CREATE TABLE canonical_character_profiles (
    character_id TEXT PRIMARY KEY REFERENCES canonical_characters(character_id),
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    -- System/provider attestation requires a separate verified receipt type.
    -- This first slice stores only a user's explicit selection.
    selection_source TEXT NOT NULL CHECK (selection_source = 'explicit'),
    selected_at TEXT NOT NULL
);

-- Binding an account that already participates in a legacy graph is not an
-- adoption workflow.  Reject partial claims at the database boundary; a later
-- audited adopter must validate and transfer the complete graph atomically.
CREATE TRIGGER provider_account_owner_graph_insert
BEFORE INSERT ON provider_account_owners
WHEN EXISTS (
    SELECT 1 FROM provider_character_links WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM remote_asset_projections WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM route_decisions WHERE selected_account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM submission_outbox WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM external_tasks WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM credit_ledger WHERE account_id = NEW.account_id
    LIMIT 1
)
BEGIN SELECT RAISE(ABORT, 'provider account requires reviewed legacy adoption'); END;

CREATE TRIGGER canonical_character_owner_graph_insert
BEFORE INSERT ON canonical_character_owners
WHEN EXISTS (
    SELECT 1
      FROM provider_character_links AS link
      LEFT JOIN provider_account_owners AS account_owner
        ON account_owner.account_id = link.account_id
     WHERE link.character_id = NEW.character_id
       AND account_owner.owner_user_id IS NOT NEW.owner_user_id
) OR EXISTS (
    SELECT 1
      FROM asset_character_links AS link
      LEFT JOIN remote_asset_owners AS asset_owner
        ON asset_owner.projection_id = link.projection_id
     WHERE link.character_id = NEW.character_id
       AND asset_owner.owner_user_id IS NOT NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'character requires reviewed legacy adoption'); END;

CREATE TRIGGER remote_asset_owner_graph_insert
BEFORE INSERT ON remote_asset_owners
WHEN NOT EXISTS (
    SELECT 1
      FROM remote_asset_projections AS projection
      JOIN provider_account_owners AS account_owner
        ON account_owner.account_id = projection.account_id
       AND account_owner.owner_user_id = NEW.owner_user_id
     WHERE projection.projection_id = NEW.projection_id
) OR EXISTS (
    SELECT 1 FROM remote_asset_versions
    WHERE projection_id = NEW.projection_id
) OR EXISTS (
    SELECT 1
      FROM asset_character_links AS link
      LEFT JOIN canonical_character_owners AS character_owner
        ON character_owner.character_id = link.character_id
     WHERE link.projection_id = NEW.projection_id
       AND character_owner.owner_user_id IS NOT NEW.owner_user_id
) OR EXISTS (
    SELECT 1
      FROM asset_lineage AS lineage
      LEFT JOIN remote_asset_owners AS neighbour_owner
        ON neighbour_owner.projection_id = CASE
            WHEN lineage.parent_projection_id = NEW.projection_id
            THEN lineage.child_projection_id
            ELSE lineage.parent_projection_id
        END
     WHERE (lineage.parent_projection_id = NEW.projection_id
            OR lineage.child_projection_id = NEW.projection_id)
       AND neighbour_owner.owner_user_id IS NOT NEW.owner_user_id
) OR EXISTS (
    SELECT 1
      FROM canonical_character_profiles AS profile
      LEFT JOIN canonical_character_owners AS character_owner
        ON character_owner.character_id = profile.character_id
     WHERE profile.projection_id = NEW.projection_id
       AND character_owner.owner_user_id IS NOT NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'asset requires reviewed legacy adoption'); END;

CREATE TRIGGER provider_account_owners_no_update
BEFORE UPDATE ON provider_account_owners
BEGIN SELECT RAISE(ABORT, 'provider account owner is immutable'); END;

CREATE TRIGGER provider_account_owners_no_delete
BEFORE DELETE ON provider_account_owners
BEGIN SELECT RAISE(ABORT, 'provider account owner is immutable'); END;

CREATE TRIGGER canonical_character_owners_no_update
BEFORE UPDATE ON canonical_character_owners
BEGIN SELECT RAISE(ABORT, 'character owner is immutable'); END;

CREATE TRIGGER canonical_character_owners_no_delete
BEFORE DELETE ON canonical_character_owners
BEGIN SELECT RAISE(ABORT, 'character owner is immutable'); END;

CREATE TRIGGER remote_asset_owners_no_update
BEFORE UPDATE ON remote_asset_owners
BEGIN SELECT RAISE(ABORT, 'asset owner is immutable'); END;

CREATE TRIGGER remote_asset_owners_no_delete
BEFORE DELETE ON remote_asset_owners
BEGIN SELECT RAISE(ABORT, 'asset owner is immutable'); END;

CREATE TRIGGER provider_accounts_identity_immutable
BEFORE UPDATE OF account_id, provider_id, created_at ON provider_accounts
WHEN OLD.account_id IS NOT NEW.account_id
 OR OLD.provider_id IS NOT NEW.provider_id
 OR OLD.created_at IS NOT NEW.created_at
BEGIN SELECT RAISE(ABORT, 'provider account identity is immutable'); END;

CREATE TRIGGER canonical_characters_identity_immutable
BEFORE UPDATE OF character_id, created_at ON canonical_characters
WHEN OLD.character_id IS NOT NEW.character_id
 OR OLD.created_at IS NOT NEW.created_at
BEGIN SELECT RAISE(ABORT, 'canonical character identity is immutable'); END;

CREATE TRIGGER provider_character_identity_immutable
BEFORE UPDATE OF link_id, account_id, external_character_id, character_id, linked_at
ON provider_character_links
WHEN OLD.link_id IS NOT NEW.link_id
 OR OLD.account_id IS NOT NEW.account_id
 OR OLD.external_character_id IS NOT NEW.external_character_id
 OR OLD.character_id IS NOT NEW.character_id
 OR OLD.linked_at IS NOT NEW.linked_at
BEGIN SELECT RAISE(ABORT, 'provider character identity is immutable'); END;

CREATE TRIGGER provider_character_owned_delete_guard
BEFORE DELETE ON provider_character_links
WHEN EXISTS (
    SELECT 1 FROM canonical_character_owners
    WHERE character_id = OLD.character_id
) OR EXISTS (
    SELECT 1 FROM provider_account_owners
    WHERE account_id = OLD.account_id
)
BEGIN SELECT RAISE(ABORT, 'owned provider character link cannot be deleted'); END;

CREATE TRIGGER remote_asset_projection_identity_immutable
BEFORE UPDATE OF projection_id, account_id, external_asset_id, media_kind, first_seen_at
ON remote_asset_projections
WHEN OLD.projection_id IS NOT NEW.projection_id
 OR OLD.account_id IS NOT NEW.account_id
 OR OLD.external_asset_id IS NOT NEW.external_asset_id
 OR OLD.media_kind IS NOT NEW.media_kind
 OR OLD.first_seen_at IS NOT NEW.first_seen_at
BEGIN SELECT RAISE(ABORT, 'asset projection identity is immutable'); END;

CREATE TRIGGER remote_asset_projection_visual_immutable
BEFORE UPDATE OF visual_class ON remote_asset_projections
WHEN OLD.visual_class IS NOT NEW.visual_class
 AND NOT (
     (OLD.visual_class IS NULL AND NEW.visual_class IS NOT NULL)
     OR (OLD.visual_class = 'unknown' AND NEW.visual_class IN (
         'anime', 'realistic', 'illustration'
     ))
 )
BEGIN SELECT RAISE(ABORT, 'classified asset visual class is immutable'); END;

CREATE TRIGGER provider_character_owner_insert
BEFORE INSERT ON provider_character_links
WHEN (SELECT owner_user_id FROM provider_account_owners
      WHERE account_id = NEW.account_id)
     IS NOT
     (SELECT owner_user_id FROM canonical_character_owners
      WHERE character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'provider account and character owner mismatch'); END;

CREATE TRIGGER provider_character_owner_update
BEFORE UPDATE OF account_id, character_id ON provider_character_links
WHEN (SELECT owner_user_id FROM provider_account_owners
      WHERE account_id = NEW.account_id)
     IS NOT
     (SELECT owner_user_id FROM canonical_character_owners
      WHERE character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'provider account and character owner mismatch'); END;

CREATE TRIGGER remote_asset_owner_binding_insert
BEFORE INSERT ON remote_asset_owners
WHEN (SELECT owner_user_id FROM provider_account_owners
      WHERE account_id = (
          SELECT account_id FROM remote_asset_projections
          WHERE projection_id = NEW.projection_id
      ))
     IS NOT NEW.owner_user_id
BEGIN SELECT RAISE(ABORT, 'provider account and asset owner mismatch'); END;

CREATE TRIGGER asset_character_owner_insert
BEFORE INSERT ON asset_character_links
WHEN (SELECT owner_user_id FROM remote_asset_owners
      WHERE projection_id = NEW.projection_id)
     IS NOT
     (SELECT owner_user_id FROM canonical_character_owners
      WHERE character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'asset and character owner mismatch'); END;

CREATE TRIGGER asset_character_links_no_update
BEFORE UPDATE ON asset_character_links
BEGIN SELECT RAISE(ABORT, 'asset character link is immutable'); END;

CREATE TRIGGER asset_character_owned_delete_guard
BEFORE DELETE ON asset_character_links
WHEN EXISTS (
    SELECT 1 FROM remote_asset_owners
    WHERE projection_id = OLD.projection_id
) OR EXISTS (
    SELECT 1 FROM canonical_character_owners
    WHERE character_id = OLD.character_id
)
BEGIN SELECT RAISE(ABORT, 'owned asset character link cannot be deleted'); END;

CREATE TRIGGER asset_character_profile_delete_guard
BEFORE DELETE ON asset_character_links
WHEN EXISTS (
    SELECT 1 FROM canonical_character_profiles
    WHERE character_id = OLD.character_id
      AND projection_id = OLD.projection_id
)
BEGIN SELECT RAISE(ABORT, 'profile asset link cannot be deleted'); END;

CREATE TRIGGER asset_lineage_owner_insert
BEFORE INSERT ON asset_lineage
WHEN (SELECT owner_user_id FROM remote_asset_owners
      WHERE projection_id = NEW.parent_projection_id)
     IS NOT
     (SELECT owner_user_id FROM remote_asset_owners
      WHERE projection_id = NEW.child_projection_id)
BEGIN SELECT RAISE(ABORT, 'asset lineage owner mismatch'); END;

CREATE TRIGGER asset_lineage_intent_owner_insert
BEFORE INSERT ON asset_lineage
WHEN NEW.intent_id IS NOT NULL
 AND (SELECT owner_user_id FROM remote_asset_owners
      WHERE projection_id = NEW.child_projection_id)
     IS NOT
     (SELECT owner_user_id FROM generation_intent_owners
      WHERE intent_id = NEW.intent_id)
BEGIN SELECT RAISE(ABORT, 'asset lineage intent owner mismatch'); END;

CREATE TRIGGER canonical_character_profile_insert
BEFORE INSERT ON canonical_character_profiles
WHEN NOT EXISTS (
     SELECT 1
       FROM canonical_character_owners AS character_owner
       JOIN remote_asset_owners AS asset_owner
         ON asset_owner.projection_id = NEW.projection_id
        AND asset_owner.owner_user_id = character_owner.owner_user_id
      WHERE character_owner.character_id = NEW.character_id
 )
 OR (SELECT media_kind FROM remote_asset_projections
     WHERE projection_id = NEW.projection_id) != 'image'
 OR NOT EXISTS (
     SELECT 1 FROM asset_character_links
     WHERE character_id = NEW.character_id
       AND projection_id = NEW.projection_id
 )
BEGIN SELECT RAISE(ABORT, 'invalid character profile asset'); END;

CREATE TRIGGER canonical_character_profile_update
BEFORE UPDATE ON canonical_character_profiles
WHEN OLD.character_id != NEW.character_id
 OR NOT EXISTS (
     SELECT 1
       FROM canonical_character_owners AS character_owner
       JOIN remote_asset_owners AS asset_owner
         ON asset_owner.projection_id = NEW.projection_id
        AND asset_owner.owner_user_id = character_owner.owner_user_id
      WHERE character_owner.character_id = NEW.character_id
 )
 OR (SELECT media_kind FROM remote_asset_projections
     WHERE projection_id = NEW.projection_id) != 'image'
 OR NOT EXISTS (
     SELECT 1 FROM asset_character_links
     WHERE character_id = NEW.character_id
       AND projection_id = NEW.projection_id
 )
BEGIN SELECT RAISE(ABORT, 'invalid character profile asset'); END;
"""


_MIGRATION_7 = r"""
-- Execution ownership must travel with every durable routing artifact.  NULL
-- remains reserved for the pre-owner legacy graph; no owned row may silently
-- fall back to that compatibility lane.
ALTER TABLE capability_snapshots ADD COLUMN actor_user_id TEXT;
ALTER TABLE route_decisions ADD COLUMN actor_user_id TEXT;
ALTER TABLE submission_outbox ADD COLUMN actor_user_id TEXT;
ALTER TABLE credit_ledger ADD COLUMN actor_user_id TEXT;
ALTER TABLE provider_accounts ADD COLUMN credential_state TEXT NOT NULL
    DEFAULT 'configured'
    CHECK (credential_state IN ('not_applicable', 'pending', 'configured'));

-- A worker may cancel a claimed task before any provider request is sent, so
-- that terminal task has no provider receipt.  Rebuild the table to encode
-- that durable state instead of inventing an external_task_id.  Migration 7
-- runs with foreign-key enforcement disabled around this parent-table rebuild
-- and verifies the full graph before committing.
DROP TRIGGER provider_account_owner_graph_insert;
DROP TRIGGER external_task_binding_insert;
DROP TRIGGER external_task_identity_immutable;
DROP TRIGGER external_task_state_transition;

CREATE TABLE external_tasks_v7_new (
    task_id TEXT PRIMARY KEY,
    outbox_id TEXT NOT NULL UNIQUE REFERENCES submission_outbox(outbox_id),
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    provider_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    external_task_id TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'created', 'submitted', 'queued', 'running', 'completed', 'failed',
        'cancelled', 'timeout', 'submission_uncertain'
    )),
    state_revision INTEGER NOT NULL DEFAULT 0 CHECK (state_revision >= 0),
    credits_charged INTEGER CHECK (credits_charged IS NULL OR credits_charged >= 0),
    remaining_credits INTEGER CHECK (remaining_credits IS NULL OR remaining_credits >= 0),
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT,
    actor_user_id TEXT,
    UNIQUE (account_id, client_request_id),
    UNIQUE (account_id, external_task_id),
    CHECK (
        state IN ('created', 'submission_uncertain', 'cancelled')
        OR external_task_id IS NOT NULL
    )
);

INSERT INTO external_tasks_v7_new(
    task_id, outbox_id, account_id, provider_id, client_request_id,
    external_task_id, state, state_revision, credits_charged,
    remaining_credits, detail, created_at, updated_at, terminal_at,
    actor_user_id
)
SELECT
    task_id, outbox_id, account_id, provider_id, client_request_id,
    external_task_id, state, state_revision, credits_charged,
    remaining_credits, detail, created_at, updated_at, terminal_at,
    NULL
FROM external_tasks;

DROP TABLE external_tasks;
ALTER TABLE external_tasks_v7_new RENAME TO external_tasks;

CREATE UNIQUE INDEX one_autoip_task_in_flight_per_account
ON external_tasks(account_id)
WHERE provider_id = 'autoip'
  AND state IN ('created', 'submission_uncertain', 'submitted', 'queued', 'running');

CREATE TRIGGER external_task_binding_insert
BEFORE INSERT ON external_tasks
WHEN NEW.account_id != (SELECT account_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.client_request_id != (SELECT client_request_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id)
 OR NEW.provider_id != (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
BEGIN SELECT RAISE(ABORT, 'external task binding mismatch'); END;

CREATE TRIGGER external_task_identity_immutable
BEFORE UPDATE ON external_tasks
WHEN OLD.outbox_id != NEW.outbox_id OR OLD.account_id != NEW.account_id
 OR OLD.provider_id != NEW.provider_id OR OLD.client_request_id != NEW.client_request_id
BEGIN SELECT RAISE(ABORT, 'external task identity is immutable'); END;

CREATE TRIGGER external_task_state_transition
BEFORE UPDATE OF state ON external_tasks
WHEN OLD.state != NEW.state AND NOT (
    (OLD.state = 'created' AND NEW.state IN ('submitted', 'submission_uncertain', 'cancelled')) OR
    (OLD.state = 'submission_uncertain' AND NEW.state IN (
        'submitted', 'queued', 'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'submitted' AND NEW.state IN (
        'queued', 'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'queued' AND NEW.state IN (
        'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'running' AND NEW.state IN (
        'completed', 'failed', 'cancelled', 'timeout'
    ))
)
BEGIN SELECT RAISE(ABORT, 'invalid external task state transition'); END;

-- A task is born only when a worker claims an outbox.  Provider receipts,
-- credit facts, terminal timestamps, and later states must arrive through the
-- guarded transition methods, never in the initial row.
CREATE TRIGGER external_task_initial_state
BEFORE INSERT ON external_tasks
WHEN NEW.state != 'created'
 OR NEW.state_revision != 0
 OR NEW.external_task_id IS NOT NULL
 OR NEW.credits_charged IS NOT NULL
 OR NEW.remaining_credits IS NOT NULL
 OR NEW.detail != ''
 OR NEW.terminal_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'invalid external task initial state'); END;

CREATE TRIGGER external_task_controlled_insert
BEFORE INSERT ON external_tasks
WHEN castle_provider_task_insert_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'external task requires controlled claim insertion'); END;

CREATE TRIGGER external_task_controlled_update
BEFORE UPDATE ON external_tasks
WHEN castle_provider_task_update_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'external task requires controlled transition'); END;

-- A pending outbox is authority for a worker to create provider work.  Only a
-- persisted confirmation bundle (or the explicitly isolated legacy enqueue
-- lane) may mint that authority; a well-shaped raw clone must never be enough.
CREATE TRIGGER submission_outbox_controlled_insert
BEFORE INSERT ON submission_outbox
WHEN castle_provider_outbox_insert_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'submission outbox requires controlled confirmation'); END;

CREATE TRIGGER submission_outbox_controlled_update
BEFORE UPDATE ON submission_outbox
WHEN castle_provider_outbox_update_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'submission outbox requires controlled transition'); END;

-- Task events are append-only audit evidence.  Only the store's claim and
-- transition transactions may create them; an arbitrary connection must not
-- fabricate history around an otherwise valid task row.
CREATE TRIGGER external_task_event_controlled_insert
BEFORE INSERT ON external_task_events
WHEN castle_provider_task_event_insert_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'external task event requires controlled insertion'); END;

CREATE TRIGGER provider_account_owner_graph_insert
BEFORE INSERT ON provider_account_owners
WHEN EXISTS (
    SELECT 1 FROM capability_snapshots WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM provider_character_links WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM remote_asset_projections WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM route_decisions WHERE selected_account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM submission_outbox WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM external_tasks WHERE account_id = NEW.account_id
    UNION ALL
    SELECT 1 FROM credit_ledger WHERE account_id = NEW.account_id
    LIMIT 1
)
BEGIN SELECT RAISE(ABORT, 'provider account requires reviewed legacy adoption'); END;

CREATE TEMP TABLE external_task_v7_fk_guard(
    violation_count INTEGER NOT NULL CHECK (violation_count = 0)
);
INSERT INTO external_task_v7_fk_guard
SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE external_task_v7_fk_guard;

UPDATE provider_accounts
   SET credential_state = 'not_applicable'
 WHERE credential_kind = 'none';

-- One vault file may never back two Castle account/owner namespaces.  Audit
-- legacy data before creating the index so migration aborts rather than
-- guessing which account owns a duplicate locator.
CREATE TEMP TABLE provider_file_locator_guard(
    noncanonical_count INTEGER NOT NULL CHECK (noncanonical_count = 0),
    duplicate_count INTEGER NOT NULL CHECK (duplicate_count = 0)
);
INSERT INTO provider_file_locator_guard
SELECT
    (SELECT COUNT(*)
       FROM provider_accounts
      WHERE credential_kind = 'file'
        AND castle_provider_file_locator_is_canonical(credential_locator) != 1),
    (SELECT COUNT(*) FROM (
        SELECT credential_locator
          FROM provider_accounts
         WHERE credential_kind = 'file'
         GROUP BY credential_locator
        HAVING COUNT(*) > 1
    ));
DROP TABLE provider_file_locator_guard;

CREATE UNIQUE INDEX one_provider_account_per_vault_file
ON provider_accounts(credential_locator)
WHERE credential_kind = 'file';

-- This row is the explicit grant which makes an owned account executable.
-- Merely having an owner sidecar is deliberately insufficient.  External
-- accounts are private to that exact owner.  The one credentialless local
-- runtime may instead be granted to all already-authenticated Castle actors;
-- authentication itself remains the service layer's responsibility.
CREATE TABLE provider_account_entitlements (
    account_id TEXT PRIMARY KEY REFERENCES provider_accounts(account_id),
    access_scope TEXT NOT NULL CHECK (
        access_scope IN ('private', 'authenticated_users')
    ),
    granted_at TEXT NOT NULL
);

-- The immutable v6 owner sidecar is an explicit private grant source only for
-- a credential-bearing external account while no execution graph exists.
-- Credentialless ClipMode namespaces remain manual-import-only and receive no
-- execution entitlement.  Accounts already routed before this migration stay
-- fail-closed for a reviewed adoption instead.
INSERT INTO provider_account_entitlements(account_id, access_scope, granted_at)
SELECT owner.account_id, 'private', owner.bound_at
  FROM provider_account_owners AS owner
 JOIN provider_accounts AS account USING(account_id)
 WHERE account.provider_id != 'local_comfy'
   AND account.credential_kind != 'none'
   AND owner.owner_user_id NOT GLOB 'castle-system:*'
   AND NOT EXISTS (
       SELECT 1 FROM capability_snapshots WHERE account_id = owner.account_id
   )
   AND NOT EXISTS (
       SELECT 1 FROM route_decisions
       WHERE selected_account_id = owner.account_id
   )
   AND NOT EXISTS (
       SELECT 1 FROM submission_outbox WHERE account_id = owner.account_id
   )
   AND NOT EXISTS (
       SELECT 1 FROM external_tasks WHERE account_id = owner.account_id
   )
   AND NOT EXISTS (
       SELECT 1 FROM credit_ledger WHERE account_id = owner.account_id
   );

CREATE UNIQUE INDEX one_authenticated_local_runtime
ON provider_account_entitlements(access_scope)
WHERE access_scope = 'authenticated_users';

-- Owner bindings are immutable, so refuse to migrate an impossible shape
-- rather than carrying it into v7 forever.  ``local_comfy`` is owned only by
-- the fixed system principal; every other provider is owned only by a user.
CREATE TEMP TABLE provider_account_owner_shape_guard(
    invalid_count INTEGER NOT NULL CHECK (invalid_count = 0)
);
INSERT INTO provider_account_owner_shape_guard
SELECT COUNT(*)
  FROM provider_account_owners AS owner
  JOIN provider_accounts AS account USING(account_id)
 WHERE NOT (
     (account.provider_id = 'local_comfy'
      AND owner.owner_user_id = 'castle-system:local-comfy')
     OR
     (account.provider_id != 'local_comfy'
      AND owner.owner_user_id NOT GLOB 'castle-system:*')
 );
DROP TABLE provider_account_owner_shape_guard;

-- Schema 6 required the account owner and resource owner to be identical.
-- Schema 7 keeps that rule for private external namespaces, but the one
-- system-owned local runtime is an execution resource shared by authenticated
-- actors.  Its characters and outputs still belong to the exact actor who
-- catalogued them.  Recreate these triggers here (rather than editing only the
-- historical migration) so an existing v6 database receives the same rules.
DROP TRIGGER provider_character_owner_insert;
CREATE TRIGGER provider_character_owner_insert
BEFORE INSERT ON provider_character_links
WHEN NOT (
    (
        NOT EXISTS (
            SELECT 1 FROM provider_account_owners
             WHERE account_id = NEW.account_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM provider_account_entitlements
             WHERE account_id = NEW.account_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM canonical_character_owners
             WHERE character_id = NEW.character_id
        )
    ) OR EXISTS (
        SELECT 1
          FROM provider_accounts AS account
          JOIN provider_account_owners AS account_owner USING(account_id)
          JOIN canonical_character_owners AS character_owner
            ON character_owner.character_id = NEW.character_id
          LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
         WHERE account.account_id = NEW.account_id
           AND (
               (
                   account.provider_id != 'local_comfy'
                   AND account_owner.owner_user_id = character_owner.owner_user_id
               ) OR (
                   account.provider_id = 'local_comfy'
                   AND account_owner.owner_user_id = 'castle-system:local-comfy'
                   AND entitlement.access_scope = 'authenticated_users'
                   AND character_owner.owner_user_id NOT GLOB 'castle-system:*'
               )
           )
    )
)
BEGIN SELECT RAISE(ABORT, 'provider account and character owner mismatch'); END;

DROP TRIGGER provider_character_owner_update;
CREATE TRIGGER provider_character_owner_update
BEFORE UPDATE OF account_id, character_id ON provider_character_links
WHEN NOT (
    (
        NOT EXISTS (
            SELECT 1 FROM provider_account_owners
             WHERE account_id = NEW.account_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM provider_account_entitlements
             WHERE account_id = NEW.account_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM canonical_character_owners
             WHERE character_id = NEW.character_id
        )
    ) OR EXISTS (
        SELECT 1
          FROM provider_accounts AS account
          JOIN provider_account_owners AS account_owner USING(account_id)
          JOIN canonical_character_owners AS character_owner
            ON character_owner.character_id = NEW.character_id
          LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
         WHERE account.account_id = NEW.account_id
           AND (
               (
                   account.provider_id != 'local_comfy'
                   AND account_owner.owner_user_id = character_owner.owner_user_id
               ) OR (
                   account.provider_id = 'local_comfy'
                   AND account_owner.owner_user_id = 'castle-system:local-comfy'
                   AND entitlement.access_scope = 'authenticated_users'
                   AND character_owner.owner_user_id NOT GLOB 'castle-system:*'
               )
           )
    )
)
BEGIN SELECT RAISE(ABORT, 'provider account and character owner mismatch'); END;

DROP TRIGGER remote_asset_owner_binding_insert;
CREATE TRIGGER remote_asset_owner_binding_insert
BEFORE INSERT ON remote_asset_owners
WHEN NOT EXISTS (
    SELECT 1
      FROM remote_asset_projections AS projection
      JOIN provider_accounts AS account USING(account_id)
      JOIN provider_account_owners AS account_owner USING(account_id)
      LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
     WHERE projection.projection_id = NEW.projection_id
       AND (
           (
               account.provider_id != 'local_comfy'
               AND account_owner.owner_user_id = NEW.owner_user_id
           ) OR (
               account.provider_id = 'local_comfy'
               AND account_owner.owner_user_id = 'castle-system:local-comfy'
               AND entitlement.access_scope = 'authenticated_users'
               AND NEW.owner_user_id NOT GLOB 'castle-system:*'
           )
       )
)
BEGIN SELECT RAISE(ABORT, 'provider account and asset owner mismatch'); END;

DROP TRIGGER remote_asset_owner_graph_insert;
CREATE TRIGGER remote_asset_owner_graph_insert
BEFORE INSERT ON remote_asset_owners
WHEN NOT EXISTS (
    SELECT 1
      FROM remote_asset_projections AS projection
      JOIN provider_accounts AS account USING(account_id)
      JOIN provider_account_owners AS account_owner USING(account_id)
      LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
     WHERE projection.projection_id = NEW.projection_id
       AND (
           (
               account.provider_id != 'local_comfy'
               AND account_owner.owner_user_id = NEW.owner_user_id
           ) OR (
               account.provider_id = 'local_comfy'
               AND account_owner.owner_user_id = 'castle-system:local-comfy'
               AND entitlement.access_scope = 'authenticated_users'
               AND NEW.owner_user_id NOT GLOB 'castle-system:*'
           )
       )
) OR EXISTS (
    SELECT 1 FROM remote_asset_versions
    WHERE projection_id = NEW.projection_id
) OR EXISTS (
    SELECT 1
      FROM asset_character_links AS link
      LEFT JOIN canonical_character_owners AS character_owner
        ON character_owner.character_id = link.character_id
     WHERE link.projection_id = NEW.projection_id
       AND character_owner.owner_user_id IS NOT NEW.owner_user_id
) OR EXISTS (
    SELECT 1
      FROM asset_lineage AS lineage
      LEFT JOIN remote_asset_owners AS neighbour_owner
        ON neighbour_owner.projection_id = CASE
            WHEN lineage.parent_projection_id = NEW.projection_id
            THEN lineage.child_projection_id
            ELSE lineage.parent_projection_id
        END
     WHERE (lineage.parent_projection_id = NEW.projection_id
            OR lineage.child_projection_id = NEW.projection_id)
       AND neighbour_owner.owner_user_id IS NOT NEW.owner_user_id
) OR EXISTS (
    SELECT 1
      FROM canonical_character_profiles AS profile
      LEFT JOIN canonical_character_owners AS character_owner
        ON character_owner.character_id = profile.character_id
     WHERE profile.projection_id = NEW.projection_id
       AND character_owner.owner_user_id IS NOT NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'asset requires reviewed legacy adoption'); END;

-- Keep sidecar validity independent of SQLite's per-connection foreign_keys
-- PRAGMA.  Install this guard in v7 for both fresh databases and historical
-- schema-6 upgrades without rewriting the schema-6 migration text.
CREATE TRIGGER IF NOT EXISTS provider_account_owner_parent_insert
BEFORE INSERT ON provider_account_owners
WHEN NOT EXISTS (
    SELECT 1 FROM provider_accounts WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider account owner requires existing account'); END;

CREATE TRIGGER provider_account_owner_system_guard
BEFORE INSERT ON provider_account_owners
WHEN NOT (
    (
        (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
            = 'local_comfy'
        AND NEW.owner_user_id = 'castle-system:local-comfy'
    ) OR (
        (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
            != 'local_comfy'
        AND NEW.owner_user_id NOT GLOB 'castle-system:*'
    )
) AND NOT EXISTS (
    SELECT 1 FROM capability_snapshots WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM provider_character_links WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM remote_asset_projections WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM route_decisions WHERE selected_account_id = NEW.account_id
    UNION ALL SELECT 1 FROM submission_outbox WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM external_tasks WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM credit_ledger WHERE account_id = NEW.account_id
    LIMIT 1
)
BEGIN SELECT RAISE(ABORT, 'invalid provider account owner shape'); END;

-- Schema-6 databases already have the broader owner graph trigger, so add a
-- separate v7 guard for capability evidence instead of relying on the updated
-- migration-6 definition being replayed during upgrade.
CREATE TRIGGER provider_account_owner_capability_insert
BEFORE INSERT ON provider_account_owners
WHEN EXISTS (
    SELECT 1 FROM capability_snapshots WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider account requires reviewed legacy adoption'); END;

CREATE TRIGGER provider_account_entitlement_insert
BEFORE INSERT ON provider_account_entitlements
WHEN NOT EXISTS (
    SELECT 1 FROM provider_accounts WHERE account_id = NEW.account_id
) OR NOT EXISTS (
    SELECT 1 FROM provider_account_owners WHERE account_id = NEW.account_id
) OR (
    NOT (
    NEW.access_scope = 'private'
    AND (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
        != 'local_comfy'
    AND (SELECT credential_kind FROM provider_accounts WHERE account_id = NEW.account_id)
        != 'none'
    AND (SELECT owner_user_id FROM provider_account_owners
         WHERE account_id = NEW.account_id) IS NOT NULL
    AND (SELECT owner_user_id FROM provider_account_owners
         WHERE account_id = NEW.account_id) NOT GLOB 'castle-system:*'
) AND NOT (
    NEW.access_scope = 'authenticated_users'
    AND (SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id)
        = 'local_comfy'
    AND (SELECT credential_kind FROM provider_accounts WHERE account_id = NEW.account_id)
        = 'none'
    AND (SELECT credential_locator FROM provider_accounts WHERE account_id = NEW.account_id)
        IS NULL
    AND (SELECT owner_user_id FROM provider_account_owners
         WHERE account_id = NEW.account_id) = 'castle-system:local-comfy'
    AND (
        (
            substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 1, 17)
                = 'http://127.0.0.1:'
            AND length(substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 18))
                BETWEEN 1 AND 5
            AND substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 18)
                NOT GLOB '*[^0-9]*'
            AND CAST(substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 18) AS INTEGER)
                BETWEEN 1 AND 65535
        ) OR (
            substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 1, 17)
                = 'http://localhost:'
            AND length(substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 18))
                BETWEEN 1 AND 5
            AND substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 18)
                NOT GLOB '*[^0-9]*'
            AND CAST(substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 18) AS INTEGER)
                BETWEEN 1 AND 65535
        ) OR (
            substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 1, 13)
                = 'http://[::1]:'
            AND length(substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 14))
                BETWEEN 1 AND 5
            AND substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 14)
                NOT GLOB '*[^0-9]*'
            AND CAST(substr((SELECT api_base FROM provider_accounts WHERE account_id = NEW.account_id), 14) AS INTEGER)
                BETWEEN 1 AND 65535
        )
    )
)
) OR EXISTS (
    SELECT 1 FROM capability_snapshots WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM route_decisions
     WHERE selected_account_id = NEW.account_id
    UNION ALL SELECT 1 FROM submission_outbox WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM external_tasks WHERE account_id = NEW.account_id
    UNION ALL SELECT 1 FROM credit_ledger WHERE account_id = NEW.account_id
    LIMIT 1
)
BEGIN SELECT RAISE(ABORT, 'invalid provider account entitlement'); END;

-- A raw connection may have foreign_keys disabled and may contain an orphaned
-- sidecar created before these guards existed.  Never let a later account
-- insert turn that orphan into an executable ACL graph.
CREATE TRIGGER provider_account_sidecar_order_guard
BEFORE INSERT ON provider_accounts
WHEN EXISTS (
    SELECT 1 FROM provider_account_owners WHERE account_id = NEW.account_id
) OR EXISTS (
    SELECT 1 FROM provider_account_entitlements WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider account sidecar requires parent-first insertion'); END;

-- Foreign-key enforcement is connection-local in SQLite.  Keep canonical
-- ownership parent-first even for raw connections with foreign_keys disabled,
-- and prevent an old orphan sidecar from becoming valid when its parent ID is
-- created later.
CREATE TRIGGER canonical_character_owner_parent_insert
BEFORE INSERT ON canonical_character_owners
WHEN NOT EXISTS (
    SELECT 1 FROM canonical_characters WHERE character_id = NEW.character_id
)
BEGIN SELECT RAISE(ABORT, 'character owner requires existing character'); END;

CREATE TRIGGER canonical_character_sidecar_order_guard
BEFORE INSERT ON canonical_characters
WHEN EXISTS (
    SELECT 1 FROM canonical_character_owners WHERE character_id = NEW.character_id
)
BEGIN SELECT RAISE(ABORT, 'character owner sidecar requires parent-first insertion'); END;

CREATE TRIGGER provider_account_credential_shape_insert
BEFORE INSERT ON provider_accounts
WHEN ((NEW.credential_kind = 'none') IS NOT
      (NEW.credential_state = 'not_applicable'))
 OR (NEW.credential_state = 'pending' AND NEW.enabled != 0)
BEGIN SELECT RAISE(ABORT, 'invalid provider credential lifecycle'); END;

-- Existing rows receive ``configured`` during migration, but no fresh
-- credential-bearing account may acquire that state through an arbitrary SQL
-- connection (including by omitting the defaulted column).  Reviewed store
-- creation methods also start at ``pending`` and use the same narrow finalize
-- transition; browser provisioning performs that transition only after the
-- write-only vault succeeds.
CREATE TRIGGER provider_account_controlled_insert_guard
BEFORE INSERT ON provider_accounts
WHEN castle_provider_account_insert_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'provider account requires controlled creation'); END;

CREATE TRIGGER provider_account_file_locator_insert_guard
BEFORE INSERT ON provider_accounts
WHEN NEW.credential_kind = 'file' AND NOT (
    substr(NEW.credential_locator, 1, 1) = '/'
    AND instr(NEW.credential_locator, '//') = 0
    AND instr(NEW.credential_locator, '/./') = 0
    AND instr(NEW.credential_locator, '/../') = 0
    AND substr(NEW.credential_locator, -2) != '/.'
    AND substr(NEW.credential_locator, -3) != '/..'
)
BEGIN SELECT RAISE(ABORT, 'provider file locator must be canonical'); END;

CREATE TRIGGER provider_account_credential_shape_update
BEFORE UPDATE ON provider_accounts
WHEN ((NEW.credential_kind = 'none') IS NOT
      (NEW.credential_state = 'not_applicable'))
 OR (NEW.credential_state = 'pending' AND NEW.enabled != 0)
BEGIN SELECT RAISE(ABORT, 'invalid provider credential lifecycle'); END;

CREATE TRIGGER provider_account_credential_binding_immutable
BEFORE UPDATE OF credential_kind, credential_locator ON provider_accounts
WHEN (OLD.credential_kind IS NOT NEW.credential_kind
      OR OLD.credential_locator IS NOT NEW.credential_locator)
BEGIN SELECT RAISE(ABORT, 'provider credential binding is immutable'); END;

CREATE TRIGGER provider_account_file_locator_update_guard
BEFORE UPDATE OF credential_kind, credential_locator ON provider_accounts
WHEN NEW.credential_kind = 'file' AND NOT (
    substr(NEW.credential_locator, 1, 1) = '/'
    AND instr(NEW.credential_locator, '//') = 0
    AND instr(NEW.credential_locator, '/./') = 0
    AND instr(NEW.credential_locator, '/../') = 0
    AND substr(NEW.credential_locator, -2) != '/.'
    AND substr(NEW.credential_locator, -3) != '/..'
)
BEGIN SELECT RAISE(ABORT, 'provider file locator must be canonical'); END;

CREATE TRIGGER provider_account_credential_state_transition
BEFORE UPDATE OF credential_state ON provider_accounts
WHEN OLD.credential_state IS NOT NEW.credential_state AND NOT (
    OLD.credential_state = 'pending' AND NEW.credential_state = 'configured'
    AND castle_provider_credential_finalize_authorized() = 1
)
BEGIN SELECT RAISE(ABORT, 'invalid provider credential state transition'); END;

CREATE TRIGGER provider_account_entitlements_no_update
BEFORE UPDATE ON provider_account_entitlements
BEGIN SELECT RAISE(ABORT, 'provider account entitlement is immutable'); END;

CREATE TRIGGER provider_account_entitlements_no_delete
BEFORE DELETE ON provider_account_entitlements
BEGIN SELECT RAISE(ABORT, 'provider account entitlement is immutable'); END;

-- SQLite REPLACE is a delete+insert conflict algorithm, and delete triggers are
-- not recursive on arbitrary external connections by default.  Explicit
-- duplicate-insert guards make immutable execution/ACL rows reject REPLACE at
-- the statement boundary instead of depending on a per-connection PRAGMA.
CREATE TRIGGER provider_account_no_replace
BEFORE INSERT ON provider_accounts
WHEN EXISTS (
    SELECT 1 FROM provider_accounts WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider account identity is immutable'); END;

CREATE TRIGGER provider_account_no_delete
BEFORE DELETE ON provider_accounts
BEGIN SELECT RAISE(ABORT, 'provider account identity is immutable'); END;

CREATE TRIGGER canonical_character_no_replace
BEFORE INSERT ON canonical_characters
WHEN EXISTS (
    SELECT 1 FROM canonical_characters WHERE character_id = NEW.character_id
)
BEGIN SELECT RAISE(ABORT, 'canonical character identity is immutable'); END;

CREATE TRIGGER canonical_character_no_delete
BEFORE DELETE ON canonical_characters
BEGIN SELECT RAISE(ABORT, 'canonical character identity is immutable'); END;

CREATE TRIGGER provider_character_link_no_replace
BEFORE INSERT ON provider_character_links
WHEN EXISTS (
    SELECT 1 FROM provider_character_links
     WHERE link_id = NEW.link_id
        OR (account_id = NEW.account_id
            AND external_character_id = NEW.external_character_id)
)
BEGIN SELECT RAISE(ABORT, 'provider character identity is immutable'); END;

CREATE TRIGGER remote_asset_projection_no_replace
BEFORE INSERT ON remote_asset_projections
WHEN EXISTS (
    SELECT 1 FROM remote_asset_projections
     WHERE projection_id = NEW.projection_id
        OR (account_id = NEW.account_id
            AND external_asset_id = NEW.external_asset_id)
)
BEGIN SELECT RAISE(ABORT, 'asset projection identity is immutable'); END;

CREATE TRIGGER remote_asset_projection_no_delete
BEFORE DELETE ON remote_asset_projections
BEGIN SELECT RAISE(ABORT, 'asset projection identity is immutable'); END;

CREATE TRIGGER generation_intent_owner_no_replace
BEFORE INSERT ON generation_intent_owners
WHEN EXISTS (
    SELECT 1 FROM generation_intent_owners WHERE intent_id = NEW.intent_id
)
BEGIN SELECT RAISE(ABORT, 'intent owner is immutable'); END;

CREATE TRIGGER provider_account_owner_no_replace
BEFORE INSERT ON provider_account_owners
WHEN EXISTS (
    SELECT 1 FROM provider_account_owners WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider account owner is immutable'); END;

CREATE TRIGGER canonical_character_owner_no_replace
BEFORE INSERT ON canonical_character_owners
WHEN EXISTS (
    SELECT 1 FROM canonical_character_owners WHERE character_id = NEW.character_id
)
BEGIN SELECT RAISE(ABORT, 'character owner is immutable'); END;

CREATE TRIGGER remote_asset_owner_no_replace
BEFORE INSERT ON remote_asset_owners
WHEN EXISTS (
    SELECT 1 FROM remote_asset_owners WHERE projection_id = NEW.projection_id
)
BEGIN SELECT RAISE(ABORT, 'asset owner is immutable'); END;

CREATE TRIGGER provider_account_entitlement_no_replace
BEFORE INSERT ON provider_account_entitlements
WHEN EXISTS (
    SELECT 1 FROM provider_account_entitlements WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider account entitlement is immutable'); END;

CREATE TRIGGER capability_snapshot_no_replace
BEFORE INSERT ON capability_snapshots
WHEN EXISTS (
    SELECT 1 FROM capability_snapshots
     WHERE snapshot_id = NEW.snapshot_id
        OR (account_id = NEW.account_id AND source_revision = NEW.source_revision)
        OR (account_id = NEW.account_id AND content_sha256 = NEW.content_sha256)
)
BEGIN SELECT RAISE(ABORT, 'capability snapshot is immutable'); END;

CREATE TRIGGER capability_offering_no_replace
BEFORE INSERT ON capability_offerings
WHEN EXISTS (
    SELECT 1 FROM capability_offerings
     WHERE snapshot_id = NEW.snapshot_id
       AND capability = NEW.capability
       AND variant_id = NEW.variant_id
       AND visual_class = NEW.visual_class
)
BEGIN SELECT RAISE(ABORT, 'capability offering is immutable'); END;

CREATE TRIGGER generation_intent_no_replace
BEFORE INSERT ON generation_intents
WHEN EXISTS (
    SELECT 1 FROM generation_intents
     WHERE intent_id = NEW.intent_id OR request_key = NEW.request_key
)
BEGIN SELECT RAISE(ABORT, 'generation intent is immutable'); END;

CREATE TRIGGER route_decision_no_replace
BEFORE INSERT ON route_decisions
WHEN EXISTS (
    SELECT 1 FROM route_decisions WHERE decision_id = NEW.decision_id
)
BEGIN SELECT RAISE(ABORT, 'route decision is immutable'); END;

CREATE TRIGGER submission_outbox_no_replace
BEFORE INSERT ON submission_outbox
WHEN EXISTS (
    SELECT 1 FROM submission_outbox
     WHERE outbox_id = NEW.outbox_id
        OR (account_id = NEW.account_id AND client_request_id = NEW.client_request_id)
)
BEGIN SELECT RAISE(ABORT, 'submission outbox identity is immutable'); END;

CREATE TRIGGER submission_outbox_no_delete
BEFORE DELETE ON submission_outbox
BEGIN SELECT RAISE(ABORT, 'submission outbox identity is immutable'); END;

CREATE TRIGGER submission_outbox_initial_state
BEFORE INSERT ON submission_outbox
WHEN NEW.state != 'pending'
 OR NEW.attempt_count != 0
 OR NEW.lease_owner IS NOT NULL
 OR NEW.lease_expires_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'submission outbox must start pending'); END;

CREATE TRIGGER external_task_no_replace
BEFORE INSERT ON external_tasks
WHEN EXISTS (
    SELECT 1 FROM external_tasks
     WHERE task_id = NEW.task_id
        OR outbox_id = NEW.outbox_id
        OR (account_id = NEW.account_id AND client_request_id = NEW.client_request_id)
        OR (
            NEW.external_task_id IS NOT NULL
            AND account_id = NEW.account_id
            AND external_task_id = NEW.external_task_id
        )
)
BEGIN SELECT RAISE(ABORT, 'external task identity is immutable'); END;

CREATE TRIGGER task_event_no_replace
BEFORE INSERT ON external_task_events
WHEN NEW.event_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM external_task_events WHERE event_id = NEW.event_id
)
BEGIN SELECT RAISE(ABORT, 'task event is immutable'); END;

CREATE TRIGGER remote_asset_version_no_replace
BEFORE INSERT ON remote_asset_versions
WHEN EXISTS (
    SELECT 1 FROM remote_asset_versions
     WHERE version_id = NEW.version_id
        OR (projection_id = NEW.projection_id
            AND version_number = NEW.version_number)
        OR (
            NEW.provider_version_id IS NOT NULL
            AND projection_id = NEW.projection_id
            AND provider_version_id = NEW.provider_version_id
        )
)
BEGIN SELECT RAISE(ABORT, 'asset version is immutable'); END;

CREATE TRIGGER asset_lineage_no_replace
BEFORE INSERT ON asset_lineage
WHEN EXISTS (
    SELECT 1 FROM asset_lineage
     WHERE parent_projection_id = NEW.parent_projection_id
       AND child_projection_id = NEW.child_projection_id
       AND relation = NEW.relation
)
BEGIN SELECT RAISE(ABORT, 'asset lineage is immutable'); END;

CREATE TRIGGER credit_ledger_no_replace
BEFORE INSERT ON credit_ledger
WHEN EXISTS (
    SELECT 1 FROM credit_ledger
     WHERE ledger_id = NEW.ledger_id
        OR (account_id = NEW.account_id AND idempotency_key = NEW.idempotency_key)
)
BEGIN SELECT RAISE(ABORT, 'credit ledger is immutable'); END;

CREATE TRIGGER webhook_replay_no_replace
BEFORE INSERT ON webhook_replay_claims
WHEN EXISTS (
    SELECT 1 FROM webhook_replay_claims
WHERE provider_id = NEW.provider_id AND replay_key = NEW.replay_key
)
BEGIN SELECT RAISE(ABORT, 'webhook replay claim is immutable'); END;

CREATE TRIGGER authenticated_local_account_update_guard
BEFORE UPDATE OF credential_kind, credential_locator, api_base ON provider_accounts
WHEN EXISTS (
    SELECT 1 FROM provider_account_entitlements
    WHERE account_id = OLD.account_id AND access_scope = 'authenticated_users'
) AND NOT (
    NEW.provider_id = 'local_comfy'
    AND NEW.credential_kind = 'none'
    AND NEW.credential_locator IS NULL
    AND (
        (
            substr(NEW.api_base, 1, 17) = 'http://127.0.0.1:'
            AND length(substr(NEW.api_base, 18)) BETWEEN 1 AND 5
            AND substr(NEW.api_base, 18) NOT GLOB '*[^0-9]*'
            AND CAST(substr(NEW.api_base, 18) AS INTEGER) BETWEEN 1 AND 65535
        ) OR (
            substr(NEW.api_base, 1, 17) = 'http://localhost:'
            AND length(substr(NEW.api_base, 18)) BETWEEN 1 AND 5
            AND substr(NEW.api_base, 18) NOT GLOB '*[^0-9]*'
            AND CAST(substr(NEW.api_base, 18) AS INTEGER) BETWEEN 1 AND 65535
        ) OR (
            substr(NEW.api_base, 1, 13) = 'http://[::1]:'
            AND length(substr(NEW.api_base, 14)) BETWEEN 1 AND 5
            AND substr(NEW.api_base, 14) NOT GLOB '*[^0-9]*'
            AND CAST(substr(NEW.api_base, 14) AS INTEGER) BETWEEN 1 AND 65535
        )
    )
)
BEGIN SELECT RAISE(ABORT, 'shared local account must remain credentialless loopback'); END;

-- Provider task receipts are durable transport identity.  They may be learned
-- once after reservation, but cannot be rebound by a later poll or callback.
CREATE TRIGGER external_task_receipt_immutable
BEFORE UPDATE OF external_task_id ON external_tasks
WHEN OLD.external_task_id IS NOT NULL
 AND OLD.external_task_id IS NOT NEW.external_task_id
BEGIN SELECT RAISE(ABORT, 'external task receipt is immutable'); END;

CREATE TRIGGER external_tasks_no_delete
BEFORE DELETE ON external_tasks
BEGIN SELECT RAISE(ABORT, 'external task is immutable'); END;

CREATE TRIGGER external_task_id_immutable
BEFORE UPDATE OF task_id ON external_tasks
WHEN OLD.task_id IS NOT NEW.task_id
BEGIN SELECT RAISE(ABORT, 'external task identity is immutable'); END;

CREATE TRIGGER external_task_terminal_immutable
BEFORE UPDATE ON external_tasks
WHEN OLD.state IN ('completed', 'failed', 'cancelled', 'timeout') AND (
    OLD.state IS NOT NEW.state
    OR OLD.external_task_id IS NOT NEW.external_task_id
    OR OLD.state_revision IS NOT NEW.state_revision
    OR OLD.credits_charged IS NOT NEW.credits_charged
    OR OLD.remaining_credits IS NOT NEW.remaining_credits
    OR OLD.detail IS NOT NEW.detail
    OR OLD.updated_at IS NOT NEW.updated_at
    OR OLD.terminal_at IS NOT NEW.terminal_at
)
BEGIN SELECT RAISE(ABORT, 'terminal external task facts are immutable'); END;

CREATE TRIGGER external_task_terminal_timestamp_insert
BEFORE INSERT ON external_tasks
WHEN (NEW.state IN ('completed', 'failed', 'cancelled', 'timeout'))
     IS NOT (NEW.terminal_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'external task terminal timestamp mismatch'); END;

CREATE TRIGGER external_task_terminal_timestamp_update
BEFORE UPDATE ON external_tasks
WHEN (NEW.state IN ('completed', 'failed', 'cancelled', 'timeout'))
     IS NOT (NEW.terminal_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'external task terminal timestamp mismatch'); END;

-- Capability evidence mutates account routing behavior, so an owned snapshot
-- is accepted only from the exact immutable account owner.  Shared local
-- capabilities are consequently written as the fixed system actor, not by an
-- arbitrary user who merely has execution access.
CREATE TRIGGER capability_snapshot_actor_insert
BEFORE INSERT ON capability_snapshots
WHEN NEW.actor_user_id IS NOT (
    SELECT owner_user_id FROM provider_account_owners
    WHERE account_id = NEW.account_id
) OR (
    NEW.actor_user_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM provider_account_entitlements
        WHERE account_id = NEW.account_id
    )
)
BEGIN SELECT RAISE(ABORT, 'capability snapshot actor is not account owner'); END;

CREATE TRIGGER capability_snapshot_actor_format
BEFORE INSERT ON capability_snapshots
WHEN NEW.actor_user_id IS NOT NULL AND (
    length(NEW.actor_user_id) NOT BETWEEN 1 AND 128
    OR instr(NEW.actor_user_id, char(0)) != 0
    OR NEW.actor_user_id != lower(NEW.actor_user_id)
    OR substr(NEW.actor_user_id, 1, 1) NOT GLOB '[a-z0-9]'
    OR NEW.actor_user_id GLOB '*[^a-z0-9._:-]*'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution actor'); END;

CREATE TRIGGER route_actor_format
BEFORE INSERT ON route_decisions
WHEN NEW.actor_user_id IS NOT NULL AND (
    length(NEW.actor_user_id) NOT BETWEEN 1 AND 128
    OR instr(NEW.actor_user_id, char(0)) != 0
    OR NEW.actor_user_id != lower(NEW.actor_user_id)
    OR substr(NEW.actor_user_id, 1, 1) NOT GLOB '[a-z0-9]'
    OR NEW.actor_user_id GLOB '*[^a-z0-9._:-]*'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution actor'); END;

CREATE TRIGGER route_actor_access_insert
BEFORE INSERT ON route_decisions
WHEN NEW.actor_user_id IS NOT (
    SELECT owner_user_id FROM generation_intent_owners
    WHERE intent_id = NEW.intent_id
) OR (
    NEW.selected_account_id IS NOT NULL
    AND NOT (
        (
            NEW.actor_user_id IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM provider_account_owners
                WHERE account_id = NEW.selected_account_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM provider_account_entitlements
                WHERE account_id = NEW.selected_account_id
            )
        ) OR EXISTS (
            SELECT 1
              FROM provider_account_owners AS owner
              JOIN provider_account_entitlements AS entitlement USING(account_id)
              JOIN provider_accounts AS account USING(account_id)
             WHERE owner.account_id = NEW.selected_account_id
               AND NEW.actor_user_id IS NOT NULL
               AND (
                    (
                        entitlement.access_scope = 'private'
                        AND owner.owner_user_id = NEW.actor_user_id
                        AND account.provider_id != 'local_comfy'
                    ) OR (
                        entitlement.access_scope = 'authenticated_users'
                        AND owner.owner_user_id = 'castle-system:local-comfy'
                        AND account.provider_id = 'local_comfy'
                    )
               )
        )
    )
)
BEGIN SELECT RAISE(ABORT, 'route actor cannot access selected account'); END;

CREATE TRIGGER route_execution_ready_insert
BEFORE INSERT ON route_decisions
WHEN NEW.selected_account_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM provider_accounts AS account
     WHERE account.account_id = NEW.selected_account_id
       AND account.enabled = 1
       AND (
           (account.provider_id = 'local_comfy'
            AND account.credential_state = 'not_applicable')
           OR
           (account.provider_id != 'local_comfy'
            AND account.credential_kind != 'none'
            AND account.credential_state = 'configured')
       )
)
BEGIN SELECT RAISE(ABORT, 'route requires execution-ready account'); END;

CREATE TRIGGER route_actor_immutable
BEFORE UPDATE OF actor_user_id ON route_decisions
WHEN OLD.actor_user_id IS NOT NEW.actor_user_id
BEGIN SELECT RAISE(ABORT, 'route actor is immutable'); END;

CREATE TRIGGER outbox_actor_format
BEFORE INSERT ON submission_outbox
WHEN NEW.actor_user_id IS NOT NULL AND (
    length(NEW.actor_user_id) NOT BETWEEN 1 AND 128
    OR instr(NEW.actor_user_id, char(0)) != 0
    OR NEW.actor_user_id != lower(NEW.actor_user_id)
    OR substr(NEW.actor_user_id, 1, 1) NOT GLOB '[a-z0-9]'
    OR NEW.actor_user_id GLOB '*[^a-z0-9._:-]*'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution actor'); END;

CREATE TRIGGER outbox_actor_access_insert
BEFORE INSERT ON submission_outbox
WHEN NEW.actor_user_id IS NOT (
    SELECT actor_user_id FROM route_decisions WHERE decision_id = NEW.decision_id
) OR NEW.actor_user_id IS NOT (
    SELECT owner_user_id FROM generation_intent_owners WHERE intent_id = NEW.intent_id
) OR NOT (
    (
        NEW.actor_user_id IS NULL
        AND NOT EXISTS (
            SELECT 1 FROM provider_account_owners WHERE account_id = NEW.account_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM provider_account_entitlements WHERE account_id = NEW.account_id
        )
    ) OR EXISTS (
        SELECT 1
          FROM provider_account_owners AS owner
          JOIN provider_account_entitlements AS entitlement USING(account_id)
          JOIN provider_accounts AS account USING(account_id)
         WHERE owner.account_id = NEW.account_id
           AND NEW.actor_user_id IS NOT NULL
           AND (
                (
                    entitlement.access_scope = 'private'
                    AND owner.owner_user_id = NEW.actor_user_id
                    AND account.provider_id != 'local_comfy'
                ) OR (
                    entitlement.access_scope = 'authenticated_users'
                    AND owner.owner_user_id = 'castle-system:local-comfy'
                    AND account.provider_id = 'local_comfy'
                )
           )
    )
)
BEGIN SELECT RAISE(ABORT, 'outbox actor cannot access selected account'); END;

CREATE TRIGGER outbox_execution_ready_insert
BEFORE INSERT ON submission_outbox
WHEN NOT EXISTS (
    SELECT 1 FROM provider_accounts AS account
     WHERE account.account_id = NEW.account_id
       AND account.enabled = 1
       AND (
           (account.provider_id = 'local_comfy'
            AND account.credential_state = 'not_applicable')
           OR
           (account.provider_id != 'local_comfy'
            AND account.credential_kind != 'none'
            AND account.credential_state = 'configured')
       )
)
BEGIN SELECT RAISE(ABORT, 'outbox requires execution-ready account'); END;

CREATE TRIGGER outbox_actor_immutable
BEFORE UPDATE OF actor_user_id ON submission_outbox
WHEN OLD.actor_user_id IS NOT NEW.actor_user_id
BEGIN SELECT RAISE(ABORT, 'outbox actor is immutable'); END;

CREATE TRIGGER external_task_actor_format
BEFORE INSERT ON external_tasks
WHEN NEW.actor_user_id IS NOT NULL AND (
    length(NEW.actor_user_id) NOT BETWEEN 1 AND 128
    OR instr(NEW.actor_user_id, char(0)) != 0
    OR NEW.actor_user_id != lower(NEW.actor_user_id)
    OR substr(NEW.actor_user_id, 1, 1) NOT GLOB '[a-z0-9]'
    OR NEW.actor_user_id GLOB '*[^a-z0-9._:-]*'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution actor'); END;

CREATE TRIGGER external_task_actor_insert
BEFORE INSERT ON external_tasks
WHEN NEW.actor_user_id IS NOT (
    SELECT actor_user_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id
) OR (
    NEW.actor_user_id IS NULL
    AND (
        EXISTS (
            SELECT 1
              FROM submission_outbox AS outbox
              JOIN generation_intent_owners AS owner USING(intent_id)
             WHERE outbox.outbox_id = NEW.outbox_id
        )
        OR EXISTS (
            SELECT 1 FROM provider_account_owners
             WHERE account_id = NEW.account_id
        )
        OR EXISTS (
            SELECT 1 FROM provider_account_entitlements
             WHERE account_id = NEW.account_id
        )
    )
)
BEGIN SELECT RAISE(ABORT, 'external task actor does not match outbox'); END;

CREATE TRIGGER external_task_execution_ready_insert
BEFORE INSERT ON external_tasks
WHEN NOT EXISTS (
    SELECT 1 FROM provider_accounts AS account
     WHERE account.account_id = NEW.account_id
       AND account.enabled = 1
       AND (
           (account.provider_id = 'local_comfy'
            AND account.credential_state = 'not_applicable')
           OR
           (account.provider_id != 'local_comfy'
            AND account.credential_kind != 'none'
            AND account.credential_state = 'configured')
       )
)
BEGIN SELECT RAISE(ABORT, 'external task requires execution-ready account'); END;

CREATE TRIGGER external_task_actor_access_update
BEFORE UPDATE ON external_tasks
WHEN NEW.actor_user_id IS NOT (
    SELECT actor_user_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id
) OR (
    NEW.actor_user_id IS NULL
    AND (
        EXISTS (
            SELECT 1
              FROM submission_outbox AS outbox
              JOIN generation_intent_owners AS owner USING(intent_id)
             WHERE outbox.outbox_id = NEW.outbox_id
        )
        OR EXISTS (
            SELECT 1 FROM provider_account_owners
             WHERE account_id = NEW.account_id
        )
        OR EXISTS (
            SELECT 1 FROM provider_account_entitlements
             WHERE account_id = NEW.account_id
        )
    )
)
BEGIN SELECT RAISE(ABORT, 'external task actor cannot access execution graph'); END;

CREATE TRIGGER external_task_actor_immutable
BEFORE UPDATE OF actor_user_id ON external_tasks
WHEN OLD.actor_user_id IS NOT NEW.actor_user_id
BEGIN SELECT RAISE(ABORT, 'external task actor is immutable'); END;

CREATE TRIGGER external_task_binding_update_guard
BEFORE UPDATE ON external_tasks
WHEN NEW.account_id IS NOT (
        SELECT account_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id
     )
 OR NEW.client_request_id IS NOT (
        SELECT client_request_id FROM submission_outbox WHERE outbox_id = NEW.outbox_id
     )
 OR NEW.provider_id IS NOT (
        SELECT provider_id FROM provider_accounts WHERE account_id = NEW.account_id
     )
BEGIN SELECT RAISE(ABORT, 'external task binding mismatch'); END;

CREATE TRIGGER credit_ledger_actor_format
BEFORE INSERT ON credit_ledger
WHEN NEW.actor_user_id IS NOT NULL AND (
    length(NEW.actor_user_id) NOT BETWEEN 1 AND 128
    OR instr(NEW.actor_user_id, char(0)) != 0
    OR NEW.actor_user_id != lower(NEW.actor_user_id)
    OR substr(NEW.actor_user_id, 1, 1) NOT GLOB '[a-z0-9]'
    OR NEW.actor_user_id GLOB '*[^a-z0-9._:-]*'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution actor'); END;

CREATE TRIGGER credit_ledger_actor_insert
BEFORE INSERT ON credit_ledger
WHEN (NEW.task_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM external_tasks WHERE task_id = NEW.task_id
)) OR (NEW.intent_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM generation_intents WHERE intent_id = NEW.intent_id
)) OR (
    NEW.task_id IS NOT NULL
    AND NEW.actor_user_id IS NOT (
        SELECT actor_user_id FROM external_tasks WHERE task_id = NEW.task_id
    )
) OR (
    NEW.intent_id IS NOT NULL
    AND NEW.actor_user_id IS NOT (
        SELECT owner_user_id FROM generation_intent_owners WHERE intent_id = NEW.intent_id
    )
) OR (
    NEW.task_id IS NOT NULL
    AND NEW.account_id IS NOT (
        SELECT account_id FROM external_tasks WHERE task_id = NEW.task_id
    )
) OR (
    NEW.task_id IS NOT NULL
    AND NEW.intent_id IS NOT NULL
    AND NEW.intent_id IS NOT (
        SELECT outbox.intent_id
          FROM external_tasks AS task
          JOIN submission_outbox AS outbox USING(outbox_id)
         WHERE task.task_id = NEW.task_id
    )
) OR NOT (
    (
        NEW.actor_user_id IS NULL
        AND NOT EXISTS (
            SELECT 1 FROM provider_account_owners WHERE account_id = NEW.account_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM provider_account_entitlements WHERE account_id = NEW.account_id
        )
    ) OR EXISTS (
        SELECT 1
          FROM provider_account_owners AS owner
          JOIN provider_account_entitlements AS entitlement USING(account_id)
          JOIN provider_accounts AS account USING(account_id)
         WHERE owner.account_id = NEW.account_id
           AND NEW.actor_user_id IS NOT NULL
           AND (
                (
                    entitlement.access_scope = 'private'
                    AND owner.owner_user_id = NEW.actor_user_id
                    AND account.provider_id != 'local_comfy'
                ) OR (
                    entitlement.access_scope = 'authenticated_users'
                    AND owner.owner_user_id = 'castle-system:local-comfy'
                    AND account.provider_id = 'local_comfy'
                )
           )
    )
)
BEGIN SELECT RAISE(ABORT, 'credit actor cannot access account'); END;
"""


_MIGRATION_8 = r"""
-- A confirmed offline import becomes an immutable active fact only after its
-- complete child graph is inserted and sealed in the same transaction.
-- Preparation, expiry, cancellation, and failed filesystem publication live
-- outside this graph; ``building`` is never externally visible or durable.
CREATE TABLE provider_import_receipts (
    receipt_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    provider_id TEXT NOT NULL CHECK (provider_id = 'clipmode'),
    provider_scope TEXT NOT NULL,
    source_schema TEXT NOT NULL CHECK (source_schema = 'castle.clipmode-export.v1'),
    source_schema_version INTEGER NOT NULL CHECK (source_schema_version = 1),
    export_id TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    raw_source_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    semantic_sha256 TEXT NOT NULL,
    mapping_sha256 TEXT NOT NULL,
    profile_selection_sha256 TEXT NOT NULL,
    account_binding_sha256 TEXT NOT NULL,
    plan_version TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    import_decision_sha256 TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'building' CHECK (state IN ('building', 'active')),
    created_at TEXT NOT NULL,
    CHECK (length(receipt_id) BETWEEN 1 AND 128 AND receipt_id = lower(receipt_id)),
    CHECK (substr(receipt_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (receipt_id NOT GLOB '*[^a-z0-9._:-]*'),
    CHECK (length(owner_user_id) BETWEEN 1 AND 128 AND owner_user_id = lower(owner_user_id)),
    CHECK (substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (owner_user_id NOT GLOB '*[^a-z0-9._:-]*'),
    CHECK (owner_user_id NOT GLOB 'castle-system:*'),
    CHECK (length(provider_scope) BETWEEN 1 AND 128 AND provider_scope = lower(provider_scope)),
    CHECK (substr(provider_scope, 1, 1) GLOB '[a-z0-9]'),
    CHECK (provider_scope NOT GLOB '*[^a-z0-9._:-]*'),
    CHECK (length(export_id) BETWEEN 1 AND 200 AND instr(export_id, char(0)) = 0),
    CHECK (length(exported_at) BETWEEN 1 AND 80 AND instr(exported_at, char(0)) = 0),
    CHECK (length(plan_version) BETWEEN 1 AND 128 AND plan_version = lower(plan_version)),
    CHECK (substr(plan_version, 1, 1) GLOB '[a-z0-9]'),
    CHECK (plan_version NOT GLOB '*[^a-z0-9._:-]*'),
    CHECK (length(raw_source_sha256) = 64 AND raw_source_sha256 = lower(raw_source_sha256)
           AND raw_source_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(manifest_sha256) = 64 AND manifest_sha256 = lower(manifest_sha256)
           AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(semantic_sha256) = 64 AND semantic_sha256 = lower(semantic_sha256)
           AND semantic_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(mapping_sha256) = 64 AND mapping_sha256 = lower(mapping_sha256)
           AND mapping_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(profile_selection_sha256) = 64
           AND profile_selection_sha256 = lower(profile_selection_sha256)
           AND profile_selection_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(account_binding_sha256) = 64
           AND account_binding_sha256 = lower(account_binding_sha256)
           AND account_binding_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(plan_sha256) = 64 AND plan_sha256 = lower(plan_sha256)
           AND plan_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(import_decision_sha256) = 64
           AND import_decision_sha256 = lower(import_decision_sha256)
           AND import_decision_sha256 NOT GLOB '*[^0-9a-f]*')
);

CREATE INDEX provider_import_receipts_by_owner
ON provider_import_receipts(owner_user_id, created_at DESC, receipt_id DESC);

CREATE TRIGGER provider_import_receipt_insert_guard
BEFORE INSERT ON provider_import_receipts
WHEN castle_provider_import_insert_authorized() != 1
 OR NEW.state != 'building'
 OR NEW.account_id = 'clipmode-import'
 OR NOT EXISTS (
    SELECT 1
      FROM provider_accounts AS account
      JOIN provider_account_owners AS owner USING(account_id)
      LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
     WHERE account.account_id = NEW.account_id
       AND account.provider_id = NEW.provider_id
       AND account.provider_id = 'clipmode'
       AND account.credential_kind = 'none'
       AND account.credential_locator IS NULL
       AND account.api_base IS NULL
       AND account.enabled = 0
       AND account.credential_state = 'not_applicable'
       AND owner.owner_user_id = NEW.owner_user_id
       AND entitlement.account_id IS NULL
)
BEGIN SELECT RAISE(ABORT, 'invalid owned manual import receipt'); END;

CREATE TRIGGER provider_import_receipt_no_replace
BEFORE INSERT ON provider_import_receipts
WHEN EXISTS (
    SELECT 1 FROM provider_import_receipts WHERE receipt_id = NEW.receipt_id
) OR EXISTS (
    SELECT 1 FROM provider_import_receipts
     WHERE import_decision_sha256 = NEW.import_decision_sha256
)
BEGIN SELECT RAISE(ABORT, 'provider import receipt is immutable'); END;

CREATE TRIGGER provider_import_receipt_no_delete
BEFORE DELETE ON provider_import_receipts
BEGIN SELECT RAISE(ABORT, 'provider import receipt is immutable'); END;

-- A manual-import account can never be turned into an executable or remote
-- account, including through a raw connection after receipts exist.
CREATE TRIGGER clipmode_manual_account_shape_insert_v8
BEFORE INSERT ON provider_accounts
WHEN NEW.provider_id = 'clipmode' AND NEW.credential_kind = 'none' AND (
    NEW.credential_locator IS NOT NULL OR NEW.api_base IS NOT NULL
    OR NEW.enabled != 0 OR NEW.credential_state != 'not_applicable'
)
BEGIN SELECT RAISE(ABORT, 'invalid clipmode manual import account'); END;

CREATE TRIGGER clipmode_manual_account_shape_update_v8
BEFORE UPDATE ON provider_accounts
WHEN (OLD.provider_id = 'clipmode' AND OLD.credential_kind = 'none') AND (
    NEW.provider_id != 'clipmode' OR NEW.credential_kind != 'none'
    OR NEW.credential_locator IS NOT NULL OR NEW.api_base IS NOT NULL
    OR NEW.enabled != 0 OR NEW.credential_state != 'not_applicable'
)
BEGIN SELECT RAISE(ABORT, 'invalid clipmode manual import account'); END;

CREATE TRIGGER provider_import_account_sidecar_order_guard
BEFORE INSERT ON provider_accounts
WHEN EXISTS (
    SELECT 1 FROM provider_import_receipts WHERE account_id = NEW.account_id
)
BEGIN SELECT RAISE(ABORT, 'provider import receipt requires parent-first insertion'); END;

-- Every object touched by a receipt is recorded separately.  The disposition
-- is evidence from the reviewed plan: created now, reused from an earlier
-- receipt, or explicitly mapped to an already-owned object.
CREATE TABLE provider_import_character_memberships (
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    owner_user_id TEXT NOT NULL,
    character_id TEXT NOT NULL REFERENCES canonical_characters(character_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('created', 'reused', 'mapped')),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (receipt_id, character_id)
);

CREATE TABLE provider_import_character_link_memberships (
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    owner_user_id TEXT NOT NULL,
    link_id TEXT NOT NULL REFERENCES provider_character_links(link_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('created', 'reused', 'mapped')),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (receipt_id, link_id)
);

CREATE TABLE provider_import_projection_memberships (
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    owner_user_id TEXT NOT NULL,
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('created', 'reused', 'mapped')),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (receipt_id, projection_id)
);

CREATE TABLE provider_import_version_memberships (
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    owner_user_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES remote_asset_versions(version_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('created', 'reused', 'mapped')),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (receipt_id, version_id)
);

CREATE TRIGGER provider_import_character_membership_insert
BEFORE INSERT ON provider_import_character_memberships
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1 FROM provider_import_receipts
     WHERE receipt_id = NEW.receipt_id AND owner_user_id = NEW.owner_user_id
       AND state = 'building'
 ) OR NOT EXISTS (
    SELECT 1 FROM canonical_character_owners
     WHERE character_id = NEW.character_id AND owner_user_id = NEW.owner_user_id
 ) OR (
    NEW.disposition = 'created' AND EXISTS (
        SELECT 1 FROM provider_import_character_memberships
         WHERE character_id = NEW.character_id
    )
 ) OR (
    NEW.disposition = 'reused' AND NOT EXISTS (
        SELECT 1 FROM provider_import_character_memberships
         WHERE character_id = NEW.character_id
    )
 )
BEGIN SELECT RAISE(ABORT, 'invalid import character membership'); END;

CREATE TRIGGER provider_import_character_link_membership_insert
BEFORE INSERT ON provider_import_character_link_memberships
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN provider_character_links AS link
        ON link.link_id = NEW.link_id AND link.account_id = receipt.account_id
      JOIN canonical_character_owners AS owner
        ON owner.character_id = link.character_id
       AND owner.owner_user_id = receipt.owner_user_id
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 ) OR (
    NEW.disposition = 'created' AND EXISTS (
        SELECT 1 FROM provider_import_character_link_memberships
         WHERE link_id = NEW.link_id
    )
 ) OR (
    NEW.disposition = 'reused' AND NOT EXISTS (
        SELECT 1 FROM provider_import_character_link_memberships
         WHERE link_id = NEW.link_id
    )
 )
BEGIN SELECT RAISE(ABORT, 'invalid import character-link membership'); END;

CREATE TRIGGER provider_import_projection_membership_insert
BEFORE INSERT ON provider_import_projection_memberships
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN remote_asset_projections AS projection
        ON projection.projection_id = NEW.projection_id
       AND projection.account_id = receipt.account_id
      JOIN remote_asset_owners AS owner
        ON owner.projection_id = projection.projection_id
       AND owner.owner_user_id = receipt.owner_user_id
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 ) OR (
    NEW.disposition = 'created' AND EXISTS (
        SELECT 1 FROM provider_import_projection_memberships
         WHERE projection_id = NEW.projection_id
    )
 ) OR (
    NEW.disposition = 'reused' AND NOT EXISTS (
        SELECT 1 FROM provider_import_projection_memberships
         WHERE projection_id = NEW.projection_id
    )
 )
BEGIN SELECT RAISE(ABORT, 'invalid import projection membership'); END;

CREATE TRIGGER provider_import_version_membership_insert
BEFORE INSERT ON provider_import_version_memberships
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN remote_asset_versions AS version ON version.version_id = NEW.version_id
      JOIN remote_asset_projections AS projection
        ON projection.projection_id = version.projection_id
       AND projection.account_id = receipt.account_id
      JOIN remote_asset_owners AS owner
        ON owner.projection_id = projection.projection_id
       AND owner.owner_user_id = receipt.owner_user_id
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 ) OR (
    NEW.disposition = 'created' AND EXISTS (
        SELECT 1 FROM provider_import_version_memberships
         WHERE version_id = NEW.version_id
    )
 ) OR (
    NEW.disposition = 'reused' AND NOT EXISTS (
        SELECT 1 FROM provider_import_version_memberships
         WHERE version_id = NEW.version_id
    )
 )
BEGIN SELECT RAISE(ABORT, 'invalid import version membership'); END;

CREATE TRIGGER provider_import_character_membership_no_replace
BEFORE INSERT ON provider_import_character_memberships
WHEN EXISTS (SELECT 1 FROM provider_import_character_memberships
             WHERE receipt_id = NEW.receipt_id AND character_id = NEW.character_id)
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_character_link_membership_no_replace
BEFORE INSERT ON provider_import_character_link_memberships
WHEN EXISTS (SELECT 1 FROM provider_import_character_link_memberships
             WHERE receipt_id = NEW.receipt_id AND link_id = NEW.link_id)
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_projection_membership_no_replace
BEFORE INSERT ON provider_import_projection_memberships
WHEN EXISTS (SELECT 1 FROM provider_import_projection_memberships
             WHERE receipt_id = NEW.receipt_id AND projection_id = NEW.projection_id)
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_version_membership_no_replace
BEFORE INSERT ON provider_import_version_memberships
WHEN EXISTS (SELECT 1 FROM provider_import_version_memberships
             WHERE receipt_id = NEW.receipt_id AND version_id = NEW.version_id)
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;

CREATE TRIGGER provider_import_character_membership_no_update
BEFORE UPDATE ON provider_import_character_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_character_membership_no_delete
BEFORE DELETE ON provider_import_character_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_character_link_membership_no_update
BEFORE UPDATE ON provider_import_character_link_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_character_link_membership_no_delete
BEFORE DELETE ON provider_import_character_link_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_projection_membership_no_update
BEFORE UPDATE ON provider_import_projection_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_projection_membership_no_delete
BEFORE DELETE ON provider_import_projection_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_version_membership_no_update
BEFORE UPDATE ON provider_import_version_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;
CREATE TRIGGER provider_import_version_membership_no_delete
BEFORE DELETE ON provider_import_version_memberships
BEGIN SELECT RAISE(ABORT, 'import membership is append-only'); END;

-- Object paths are derived from this owner/digest pair by the materializer.
-- No absolute path, archive member name, or provider URL is stored here.
CREATE TABLE provider_media_blobs (
    owner_user_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    signature_family TEXT NOT NULL CHECK (signature_family IN (
        'avif', 'flac', 'gif', 'jpeg', 'mp4', 'mpeg_audio', 'ogg', 'png',
        'quicktime', 'utf8_text', 'wav', 'webm', 'webp'
    )),
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_user_id, sha256),
    CHECK (length(owner_user_id) BETWEEN 1 AND 128 AND owner_user_id = lower(owner_user_id)),
    CHECK (substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'),
    CHECK (owner_user_id NOT GLOB '*[^a-z0-9._:-]*'),
    CHECK (owner_user_id NOT GLOB 'castle-system:*'),
    CHECK (length(sha256) = 64 AND sha256 = lower(sha256)
           AND sha256 NOT GLOB '*[^0-9a-f]*')
);

CREATE TRIGGER provider_media_blob_insert
BEFORE INSERT ON provider_media_blobs
WHEN castle_provider_import_insert_authorized() != 1 OR NOT EXISTS (
    SELECT 1 FROM provider_import_receipts
     WHERE owner_user_id = NEW.owner_user_id AND state = 'building'
)
BEGIN SELECT RAISE(ABORT, 'invalid owner media blob'); END;
CREATE TRIGGER provider_media_blob_no_replace
BEFORE INSERT ON provider_media_blobs
WHEN EXISTS (SELECT 1 FROM provider_media_blobs
             WHERE owner_user_id = NEW.owner_user_id AND sha256 = NEW.sha256)
BEGIN SELECT RAISE(ABORT, 'provider media blob is immutable'); END;
CREATE TRIGGER provider_media_blob_no_update
BEFORE UPDATE ON provider_media_blobs
BEGIN SELECT RAISE(ABORT, 'provider media blob is immutable'); END;
CREATE TRIGGER provider_media_blob_no_delete
BEFORE DELETE ON provider_media_blobs
BEGIN SELECT RAISE(ABORT, 'provider media blob is immutable'); END;

CREATE TABLE provider_asset_version_blobs (
    owner_user_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES remote_asset_versions(version_id),
    role TEXT NOT NULL CHECK (role IN ('original', 'preview_rendition')),
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    sha256 TEXT NOT NULL,
    declared_mime TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    PRIMARY KEY (owner_user_id, version_id, role),
    FOREIGN KEY (owner_user_id, sha256)
        REFERENCES provider_media_blobs(owner_user_id, sha256),
    CHECK (length(declared_mime) BETWEEN 3 AND 255
           AND declared_mime = lower(declared_mime)
           AND instr(declared_mime, '/') > 1
           AND instr(declared_mime, char(0)) = 0)
);

CREATE TRIGGER provider_asset_version_blob_insert
BEFORE INSERT ON provider_asset_version_blobs
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN provider_import_version_memberships AS membership
        ON membership.receipt_id = receipt.receipt_id
       AND membership.owner_user_id = receipt.owner_user_id
       AND membership.version_id = NEW.version_id
      JOIN remote_asset_versions AS version ON version.version_id = NEW.version_id
      JOIN remote_asset_projections AS projection
        ON projection.projection_id = version.projection_id
       AND projection.account_id = receipt.account_id
      JOIN remote_asset_owners AS owner
        ON owner.projection_id = projection.projection_id
       AND owner.owner_user_id = receipt.owner_user_id
      JOIN provider_media_blobs AS blob
        ON blob.owner_user_id = receipt.owner_user_id
       AND blob.sha256 = NEW.sha256
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 ) OR (
    NEW.role = 'original' AND NOT EXISTS (
        SELECT 1
          FROM remote_asset_versions AS version
          JOIN provider_media_blobs AS blob
            ON blob.owner_user_id = NEW.owner_user_id
           AND blob.sha256 = NEW.sha256
         WHERE version.version_id = NEW.version_id
           AND version.sha256 = NEW.sha256
           AND version.byte_size = blob.byte_size
           AND version.mime_type = NEW.declared_mime
    )
 ) OR (
    NEW.role = 'preview_rendition'
    AND NEW.declared_mime NOT IN ('image/jpeg', 'image/png', 'image/webp')
 ) OR NOT EXISTS (
    SELECT 1
      FROM provider_media_blobs AS blob
     WHERE blob.owner_user_id = NEW.owner_user_id
       AND blob.sha256 = NEW.sha256
       AND (
           (blob.signature_family = 'png' AND NEW.declared_mime = 'image/png')
           OR (blob.signature_family = 'jpeg' AND NEW.declared_mime = 'image/jpeg')
           OR (blob.signature_family = 'webp' AND NEW.declared_mime = 'image/webp')
           OR (blob.signature_family = 'gif' AND NEW.declared_mime = 'image/gif')
           OR (blob.signature_family = 'avif' AND NEW.declared_mime = 'image/avif')
           OR (blob.signature_family = 'mp4'
               AND NEW.declared_mime IN ('video/mp4', 'audio/mp4'))
           OR (blob.signature_family = 'quicktime'
               AND NEW.declared_mime = 'video/quicktime')
           OR (blob.signature_family = 'webm'
               AND NEW.declared_mime = 'video/webm')
           OR (blob.signature_family = 'wav'
               AND NEW.declared_mime IN ('audio/wav', 'audio/x-wav'))
           OR (blob.signature_family = 'mpeg_audio'
               AND NEW.declared_mime = 'audio/mpeg')
           OR (blob.signature_family = 'ogg'
               AND NEW.declared_mime = 'audio/ogg')
           OR (blob.signature_family = 'flac'
               AND NEW.declared_mime = 'audio/flac')
           OR (blob.signature_family = 'utf8_text'
               AND NEW.declared_mime IN ('text/plain', 'text/markdown'))
       )
 )
BEGIN SELECT RAISE(ABORT, 'invalid asset-version blob binding'); END;

CREATE TRIGGER provider_asset_version_blob_no_replace
BEFORE INSERT ON provider_asset_version_blobs
WHEN EXISTS (
    SELECT 1 FROM provider_asset_version_blobs
     WHERE owner_user_id = NEW.owner_user_id
       AND version_id = NEW.version_id AND role = NEW.role
)
BEGIN SELECT RAISE(ABORT, 'asset-version blob binding is immutable'); END;
CREATE TRIGGER provider_asset_version_blob_no_update
BEFORE UPDATE ON provider_asset_version_blobs
BEGIN SELECT RAISE(ABORT, 'asset-version blob binding is immutable'); END;
CREATE TRIGGER provider_asset_version_blob_no_delete
BEFORE DELETE ON provider_asset_version_blobs
BEGIN SELECT RAISE(ABORT, 'asset-version blob binding is immutable'); END;

-- Serving a preview never means "latest version".  It resolves this exact
-- owner/projection/version/rendition tuple and then opens the digest-derived
-- private object path.
CREATE TABLE projection_preview_versions (
    owner_user_id TEXT NOT NULL,
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    version_id TEXT NOT NULL REFERENCES remote_asset_versions(version_id),
    rendition_sha256 TEXT NOT NULL,
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    selection_source TEXT NOT NULL CHECK (selection_source = 'explicit'),
    selected_at TEXT NOT NULL,
    PRIMARY KEY (owner_user_id, projection_id)
);

CREATE TRIGGER projection_preview_version_insert
BEFORE INSERT ON projection_preview_versions
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN provider_import_projection_memberships AS projection_membership
        ON projection_membership.receipt_id = receipt.receipt_id
       AND projection_membership.owner_user_id = receipt.owner_user_id
       AND projection_membership.projection_id = NEW.projection_id
      JOIN provider_import_version_memberships AS version_membership
        ON version_membership.receipt_id = receipt.receipt_id
       AND version_membership.owner_user_id = receipt.owner_user_id
       AND version_membership.version_id = NEW.version_id
      JOIN remote_asset_versions AS version
        ON version.version_id = NEW.version_id
       AND version.projection_id = NEW.projection_id
      JOIN remote_asset_owners AS owner
        ON owner.projection_id = NEW.projection_id
       AND owner.owner_user_id = receipt.owner_user_id
      JOIN provider_asset_version_blobs AS binding
        ON binding.owner_user_id = receipt.owner_user_id
       AND binding.version_id = NEW.version_id
       AND binding.role = 'preview_rendition'
       AND binding.sha256 = NEW.rendition_sha256
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 )
BEGIN SELECT RAISE(ABORT, 'invalid projection preview version'); END;

CREATE TRIGGER projection_preview_version_no_replace
BEFORE INSERT ON projection_preview_versions
WHEN EXISTS (
    SELECT 1 FROM projection_preview_versions
     WHERE owner_user_id = NEW.owner_user_id
       AND projection_id = NEW.projection_id
)
BEGIN SELECT RAISE(ABORT, 'projection preview version is immutable'); END;
CREATE TRIGGER projection_preview_version_no_update
BEFORE UPDATE ON projection_preview_versions
BEGIN SELECT RAISE(ABORT, 'projection preview version is immutable'); END;
CREATE TRIGGER projection_preview_version_no_delete
BEFORE DELETE ON projection_preview_versions
BEGIN SELECT RAISE(ABORT, 'projection preview version is immutable'); END;

-- Keep the schema-6 profile pointer for compatibility, but extend an imported
-- profile with an immutable exact version and safe rendition.  Once extended,
-- the old pointer cannot be moved behind the exact-version record.
CREATE TABLE canonical_character_profile_versions (
    character_id TEXT PRIMARY KEY REFERENCES canonical_character_profiles(character_id),
    owner_user_id TEXT NOT NULL,
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    version_id TEXT NOT NULL REFERENCES remote_asset_versions(version_id),
    rendition_sha256 TEXT NOT NULL,
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    selection_source TEXT NOT NULL CHECK (selection_source = 'explicit'),
    selected_at TEXT NOT NULL
);

CREATE TRIGGER canonical_character_profile_version_insert
BEFORE INSERT ON canonical_character_profile_versions
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN provider_import_character_memberships AS character_membership
        ON character_membership.receipt_id = receipt.receipt_id
       AND character_membership.owner_user_id = receipt.owner_user_id
       AND character_membership.character_id = NEW.character_id
      JOIN provider_import_projection_memberships AS projection_membership
        ON projection_membership.receipt_id = receipt.receipt_id
       AND projection_membership.owner_user_id = receipt.owner_user_id
       AND projection_membership.projection_id = NEW.projection_id
      JOIN provider_import_version_memberships AS version_membership
        ON version_membership.receipt_id = receipt.receipt_id
       AND version_membership.owner_user_id = receipt.owner_user_id
       AND version_membership.version_id = NEW.version_id
      JOIN canonical_character_owners AS character_owner
        ON character_owner.character_id = NEW.character_id
       AND character_owner.owner_user_id = receipt.owner_user_id
      JOIN canonical_character_profiles AS profile
        ON profile.character_id = NEW.character_id
       AND profile.projection_id = NEW.projection_id
      JOIN remote_asset_projections AS projection
        ON projection.projection_id = NEW.projection_id
       AND projection.media_kind = 'image'
       AND projection.account_id = receipt.account_id
      JOIN remote_asset_owners AS asset_owner
        ON asset_owner.projection_id = projection.projection_id
       AND asset_owner.owner_user_id = receipt.owner_user_id
      JOIN asset_character_links AS asset_link
        ON asset_link.projection_id = projection.projection_id
       AND asset_link.character_id = NEW.character_id
      JOIN remote_asset_versions AS version
        ON version.version_id = NEW.version_id
       AND version.projection_id = NEW.projection_id
      JOIN projection_preview_versions AS preview
        ON preview.owner_user_id = receipt.owner_user_id
       AND preview.projection_id = NEW.projection_id
       AND preview.version_id = NEW.version_id
       AND preview.rendition_sha256 = NEW.rendition_sha256
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 )
BEGIN SELECT RAISE(ABORT, 'invalid exact character profile version'); END;

CREATE TRIGGER canonical_character_profile_version_no_replace
BEFORE INSERT ON canonical_character_profile_versions
WHEN EXISTS (
    SELECT 1 FROM canonical_character_profile_versions
     WHERE character_id = NEW.character_id
)
BEGIN SELECT RAISE(ABORT, 'exact character profile version is immutable'); END;
CREATE TRIGGER canonical_character_profile_version_no_update
BEFORE UPDATE ON canonical_character_profile_versions
BEGIN SELECT RAISE(ABORT, 'exact character profile version is immutable'); END;
CREATE TRIGGER canonical_character_profile_version_no_delete
BEFORE DELETE ON canonical_character_profile_versions
BEGIN SELECT RAISE(ABORT, 'exact character profile version is immutable'); END;

CREATE TRIGGER canonical_character_profile_exact_projection_guard
BEFORE UPDATE ON canonical_character_profiles
WHEN EXISTS (
    SELECT 1 FROM canonical_character_profile_versions
     WHERE character_id = OLD.character_id
 ) AND (
    OLD.character_id IS NOT NEW.character_id
    OR OLD.projection_id IS NOT NEW.projection_id
    OR OLD.selection_source IS NOT NEW.selection_source
    OR OLD.selected_at IS NOT NEW.selected_at
)
BEGIN SELECT RAISE(ABORT, 'exact character profile projection is immutable'); END;

CREATE TRIGGER canonical_character_profile_exact_insert_guard
BEFORE INSERT ON canonical_character_profiles
WHEN EXISTS (
    SELECT 1 FROM canonical_character_profile_versions
     WHERE character_id = NEW.character_id
)
BEGIN SELECT RAISE(ABORT, 'exact character profile projection is immutable'); END;

CREATE TRIGGER canonical_character_profile_exact_delete_guard
BEFORE DELETE ON canonical_character_profiles
WHEN EXISTS (
    SELECT 1 FROM canonical_character_profile_versions
     WHERE character_id = OLD.character_id
)
BEGIN SELECT RAISE(ABORT, 'exact character profile projection is immutable'); END;

-- Sealing is the sole permitted receipt update.  It proves that every child
-- relation has its required parent membership in this exact receipt, then
-- makes all later inserts fail through their ``state = 'building'`` guards.
CREATE TRIGGER provider_import_receipt_seal_guard
BEFORE UPDATE OF state ON provider_import_receipts
WHEN castle_provider_import_insert_authorized() != 1
 OR OLD.state != 'building'
 OR NEW.state != 'active'
 OR OLD.receipt_id IS NOT NEW.receipt_id
 OR OLD.owner_user_id IS NOT NEW.owner_user_id
 OR OLD.account_id IS NOT NEW.account_id
 OR OLD.provider_id IS NOT NEW.provider_id
 OR OLD.provider_scope IS NOT NEW.provider_scope
 OR OLD.source_schema IS NOT NEW.source_schema
 OR OLD.source_schema_version IS NOT NEW.source_schema_version
 OR OLD.export_id IS NOT NEW.export_id
 OR OLD.exported_at IS NOT NEW.exported_at
 OR OLD.raw_source_sha256 IS NOT NEW.raw_source_sha256
 OR OLD.manifest_sha256 IS NOT NEW.manifest_sha256
 OR OLD.semantic_sha256 IS NOT NEW.semantic_sha256
 OR OLD.mapping_sha256 IS NOT NEW.mapping_sha256
 OR OLD.profile_selection_sha256 IS NOT NEW.profile_selection_sha256
 OR OLD.account_binding_sha256 IS NOT NEW.account_binding_sha256
 OR OLD.plan_version IS NOT NEW.plan_version
 OR OLD.plan_sha256 IS NOT NEW.plan_sha256
 OR OLD.import_decision_sha256 IS NOT NEW.import_decision_sha256
 OR OLD.created_at IS NOT NEW.created_at
 OR EXISTS (
    SELECT 1
      FROM provider_import_character_link_memberships AS link_membership
      JOIN provider_character_links AS link USING(link_id)
     WHERE link_membership.receipt_id = OLD.receipt_id
       AND NOT EXISTS (
           SELECT 1 FROM provider_import_character_memberships AS character_membership
            WHERE character_membership.receipt_id = OLD.receipt_id
              AND character_membership.character_id = link.character_id
       )
 )
 OR EXISTS (
    SELECT 1
      FROM provider_import_version_memberships AS version_membership
      JOIN remote_asset_versions AS version USING(version_id)
     WHERE version_membership.receipt_id = OLD.receipt_id
       AND NOT EXISTS (
           SELECT 1 FROM provider_import_projection_memberships AS projection_membership
            WHERE projection_membership.receipt_id = OLD.receipt_id
              AND projection_membership.projection_id = version.projection_id
       )
 )
 OR EXISTS (
    SELECT 1
      FROM provider_import_projection_memberships AS projection_membership
      JOIN remote_asset_projections AS projection USING(projection_id)
      JOIN asset_character_links AS asset_link USING(projection_id)
     WHERE projection_membership.receipt_id = OLD.receipt_id
       AND NOT EXISTS (
           SELECT 1 FROM provider_import_character_memberships AS character_membership
            WHERE character_membership.receipt_id = OLD.receipt_id
              AND character_membership.character_id = asset_link.character_id
       )
 )
BEGIN SELECT RAISE(ABORT, 'provider import receipt graph is incomplete'); END;

CREATE TRIGGER provider_import_receipt_no_other_update
BEFORE UPDATE ON provider_import_receipts
WHEN NOT (
    castle_provider_import_insert_authorized() = 1
    AND OLD.state = 'building'
    AND NEW.state = 'active'
)
BEGIN SELECT RAISE(ABORT, 'provider import receipt is immutable'); END;
"""


_MIGRATION_9 = r"""
-- Schema 8 could prove character/link/projection/version membership, but not
-- an imported lineage edge or the stable decision graph used for replay.
-- These two append-only relations close that gap without attempting to infer
-- provenance for historical receipts.
CREATE TABLE provider_import_plan_evidence (
    receipt_id TEXT PRIMARY KEY REFERENCES provider_import_receipts(receipt_id),
    owner_user_id TEXT NOT NULL,
    decision_plan_sha256 TEXT NOT NULL,
    stable_graph_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (length(decision_plan_sha256) = 64
           AND decision_plan_sha256 = lower(decision_plan_sha256)
           AND decision_plan_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(stable_graph_sha256) = 64
           AND stable_graph_sha256 = lower(stable_graph_sha256)
           AND stable_graph_sha256 NOT GLOB '*[^0-9a-f]*')
);

CREATE TABLE provider_import_lineage_memberships (
    receipt_id TEXT NOT NULL REFERENCES provider_import_receipts(receipt_id),
    owner_user_id TEXT NOT NULL,
    parent_projection_id TEXT NOT NULL,
    child_projection_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('created', 'reused')),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (receipt_id, parent_projection_id, child_projection_id, relation),
    FOREIGN KEY (parent_projection_id, child_projection_id, relation)
        REFERENCES asset_lineage(parent_projection_id, child_projection_id, relation)
);

CREATE TRIGGER provider_import_plan_evidence_insert
BEFORE INSERT ON provider_import_plan_evidence
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1 FROM provider_import_receipts
     WHERE receipt_id = NEW.receipt_id
       AND owner_user_id = NEW.owner_user_id
       AND state = 'building'
 )
BEGIN SELECT RAISE(ABORT, 'invalid import plan evidence'); END;

CREATE TRIGGER provider_import_lineage_membership_insert
BEFORE INSERT ON provider_import_lineage_memberships
WHEN castle_provider_import_insert_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_import_receipts AS receipt
      JOIN provider_import_projection_memberships AS parent_membership
        ON parent_membership.receipt_id = receipt.receipt_id
       AND parent_membership.owner_user_id = receipt.owner_user_id
       AND parent_membership.projection_id = NEW.parent_projection_id
      JOIN provider_import_projection_memberships AS child_membership
        ON child_membership.receipt_id = receipt.receipt_id
       AND child_membership.owner_user_id = receipt.owner_user_id
       AND child_membership.projection_id = NEW.child_projection_id
      JOIN remote_asset_projections AS parent
        ON parent.projection_id = NEW.parent_projection_id
       AND parent.account_id = receipt.account_id
      JOIN remote_asset_projections AS child
        ON child.projection_id = NEW.child_projection_id
       AND child.account_id = receipt.account_id
      JOIN remote_asset_owners AS parent_owner
        ON parent_owner.projection_id = parent.projection_id
       AND parent_owner.owner_user_id = receipt.owner_user_id
      JOIN remote_asset_owners AS child_owner
        ON child_owner.projection_id = child.projection_id
       AND child_owner.owner_user_id = receipt.owner_user_id
      JOIN asset_lineage AS lineage
        ON lineage.parent_projection_id = NEW.parent_projection_id
       AND lineage.child_projection_id = NEW.child_projection_id
       AND lineage.relation = NEW.relation
     WHERE receipt.receipt_id = NEW.receipt_id
       AND receipt.owner_user_id = NEW.owner_user_id
       AND receipt.state = 'building'
 ) OR (
    NEW.disposition = 'created' AND EXISTS (
        SELECT 1 FROM provider_import_lineage_memberships
         WHERE parent_projection_id = NEW.parent_projection_id
           AND child_projection_id = NEW.child_projection_id
           AND relation = NEW.relation
    )
 ) OR (
    NEW.disposition = 'reused' AND NOT EXISTS (
        SELECT 1 FROM provider_import_lineage_memberships
         WHERE parent_projection_id = NEW.parent_projection_id
           AND child_projection_id = NEW.child_projection_id
           AND relation = NEW.relation
    )
 )
BEGIN SELECT RAISE(ABORT, 'invalid import lineage membership'); END;

CREATE TRIGGER provider_import_plan_evidence_no_update
BEFORE UPDATE ON provider_import_plan_evidence
BEGIN SELECT RAISE(ABORT, 'import plan evidence is append-only'); END;
CREATE TRIGGER provider_import_plan_evidence_no_delete
BEFORE DELETE ON provider_import_plan_evidence
BEGIN SELECT RAISE(ABORT, 'import plan evidence is append-only'); END;
CREATE TRIGGER provider_import_lineage_membership_no_replace
BEFORE INSERT ON provider_import_lineage_memberships
WHEN EXISTS (
    SELECT 1 FROM provider_import_lineage_memberships
     WHERE receipt_id = NEW.receipt_id
       AND parent_projection_id = NEW.parent_projection_id
       AND child_projection_id = NEW.child_projection_id
       AND relation = NEW.relation
)
BEGIN SELECT RAISE(ABORT, 'import lineage membership is append-only'); END;
CREATE TRIGGER provider_import_lineage_membership_no_update
BEFORE UPDATE ON provider_import_lineage_memberships
BEGIN SELECT RAISE(ABORT, 'import lineage membership is append-only'); END;
CREATE TRIGGER provider_import_lineage_membership_no_delete
BEFORE DELETE ON provider_import_lineage_memberships
BEGIN SELECT RAISE(ABORT, 'import lineage membership is append-only'); END;

-- The existing schema-8 seal trigger remains authoritative for its original
-- relations.  This additional guard requires schema-9 evidence for every new
-- receipt and proves that every imported lineage edge is represented once.
CREATE TRIGGER provider_import_receipt_v9_seal_guard
BEFORE UPDATE OF state ON provider_import_receipts
WHEN OLD.state = 'building' AND NEW.state = 'active' AND (
    NOT EXISTS (
        SELECT 1 FROM provider_import_plan_evidence
         WHERE receipt_id = OLD.receipt_id
           AND owner_user_id = OLD.owner_user_id
    ) OR EXISTS (
        SELECT 1
          FROM provider_import_lineage_memberships AS membership
         WHERE membership.receipt_id = OLD.receipt_id
           AND membership.owner_user_id != OLD.owner_user_id
    )
)
BEGIN SELECT RAISE(ABORT, 'provider import receipt lacks schema-9 evidence'); END;
"""

_MIGRATION_10 = r"""
CREATE TABLE asset_catalog_annotations (
    owner_user_id TEXT NOT NULL,
    projection_id TEXT NOT NULL REFERENCES remote_asset_projections(projection_id),
    display_name TEXT CHECK (
        display_name IS NULL OR length(display_name) BETWEEN 1 AND 160
    ),
    audio_role TEXT NOT NULL CHECK (audio_role IN (
        'unclassified', 'music', 'sound_effect', 'voice', 'ambience'
    )),
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_user_id, projection_id)
);
CREATE INDEX asset_catalog_annotations_by_owner_role
ON asset_catalog_annotations(owner_user_id, audio_role, projection_id);
CREATE TRIGGER asset_catalog_annotation_owner_insert
BEFORE INSERT ON asset_catalog_annotations
WHEN NOT EXISTS (
    SELECT 1 FROM remote_asset_owners
     WHERE projection_id = NEW.projection_id
       AND owner_user_id = NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'asset catalog annotation owner mismatch'); END;
CREATE TRIGGER asset_catalog_annotation_identity_immutable
BEFORE UPDATE OF owner_user_id, projection_id ON asset_catalog_annotations
BEGIN SELECT RAISE(ABORT, 'asset catalog annotation identity is immutable'); END;
CREATE TRIGGER asset_catalog_annotation_revision_guard
BEFORE UPDATE ON asset_catalog_annotations
WHEN NEW.revision != OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'asset catalog annotation revision must advance once'); END;
CREATE TRIGGER asset_catalog_annotation_no_delete
BEFORE DELETE ON asset_catalog_annotations
BEGIN SELECT RAISE(ABORT, 'asset catalog annotation cannot be deleted'); END;
"""

_MIGRATION_11 = r"""
ALTER TABLE asset_catalog_annotations
ADD COLUMN media_role TEXT NOT NULL DEFAULT 'unclassified' CHECK (media_role IN (
    'unclassified',
    'profile', 'outfit', 'reference', 'background', 'prop', 'storyboard',
    'dance_template', 'motion_reference', 'source_clip', 'finished',
    'prompt', 'script', 'subtitle'
));
CREATE INDEX asset_catalog_annotations_by_owner_media_role
ON asset_catalog_annotations(owner_user_id, media_role, projection_id);
"""

_MIGRATION_12 = r"""
-- Audit legacy history rows under the same IMMEDIATE transaction which adds
-- the evidence guards.  This closes the audit-to-migration race: no writer can
-- classify or link another history row between the check and trigger install.
CREATE TEMP TABLE castle_schema12_history_audit (value INTEGER NOT NULL);
CREATE TEMP TRIGGER castle_schema12_history_audit_guard
BEFORE INSERT ON castle_schema12_history_audit
WHEN EXISTS (
    SELECT 1
      FROM remote_asset_projections AS projection
     WHERE (
           projection.projection_id LIKE 'autoip-history:%'
           OR json_extract(projection.metadata_json, '$.source') = 'autoip_history'
       )
       AND (
           projection.visual_class NOT IN ('unknown')
           OR EXISTS (
               SELECT 1 FROM asset_character_links AS link
                WHERE link.projection_id = projection.projection_id
           )
       )
)
BEGIN SELECT RAISE(ABORT, 'schema 12 requires AutoIP history assets to remain unclassified'); END;
INSERT INTO castle_schema12_history_audit(value) VALUES (1);
DROP TRIGGER castle_schema12_history_audit_guard;
DROP TABLE castle_schema12_history_audit;

-- Human classification is a one-way, version-bound fact.  The mutable catalog
-- label is intentionally separate: renaming an asset or changing its media
-- purpose must never rewrite the evidence which made a visual class and exact
-- character attribution usable.
CREATE TABLE asset_classification_evidence (
    classification_id TEXT PRIMARY KEY CHECK (
        length(classification_id) BETWEEN 1 AND 128
        AND classification_id = lower(classification_id)
        AND substr(classification_id, 1, 1) GLOB '[a-z0-9]'
        AND classification_id NOT GLOB '*[^a-z0-9._:-]*'
    ),
    owner_user_id TEXT NOT NULL CHECK (
        length(owner_user_id) BETWEEN 1 AND 128
        AND owner_user_id = lower(owner_user_id)
        AND substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'
        AND owner_user_id NOT GLOB '*[^a-z0-9._:-]*'
        AND owner_user_id NOT GLOB 'castle-system:*'
    ),
    projection_id TEXT NOT NULL UNIQUE
        REFERENCES remote_asset_projections(projection_id),
    asset_version_id TEXT NOT NULL
        REFERENCES remote_asset_versions(version_id),
    visual_class TEXT NOT NULL CHECK (visual_class IN (
        'anime', 'realistic', 'illustration'
    )),
    character_ids_json TEXT NOT NULL CHECK (
        json_valid(character_ids_json)
        AND json_type(character_ids_json) = 'array'
        AND json_array_length(character_ids_json) BETWEEN 1 AND 4
    ),
    classification_version INTEGER NOT NULL CHECK (classification_version = 1),
    state TEXT NOT NULL DEFAULT 'building' CHECK (state IN ('building', 'active')),
    classified_at TEXT NOT NULL CHECK (
        length(classified_at) BETWEEN 1 AND 80
        AND instr(classified_at, char(0)) = 0
    )
);

CREATE INDEX asset_classification_evidence_by_owner
ON asset_classification_evidence(owner_user_id, classified_at, projection_id);

-- History adoption never supplies a trusted class.  It must first create the
-- unknown projection/version and then use the evidence-building transaction;
-- neither a direct INSERT nor the legacy character-cascade path may bypass it.
CREATE TRIGGER autoip_history_projection_insert_unclassified
BEFORE INSERT ON remote_asset_projections
WHEN (
    NEW.projection_id LIKE 'autoip-history:%'
    OR json_extract(NEW.metadata_json, '$.source') = 'autoip_history'
)
 AND NEW.visual_class IS NOT NULL
 AND NEW.visual_class != 'unknown'
BEGIN SELECT RAISE(ABORT, 'AutoIP history class requires explicit evidence'); END;

CREATE TRIGGER autoip_history_projection_metadata_immutable
BEFORE UPDATE OF metadata_json ON remote_asset_projections
WHEN OLD.metadata_json IS NOT NEW.metadata_json
 AND (
    OLD.projection_id LIKE 'autoip-history:%'
    OR json_extract(OLD.metadata_json, '$.source') = 'autoip_history'
    OR json_extract(NEW.metadata_json, '$.source') = 'autoip_history'
 )
BEGIN SELECT RAISE(ABORT, 'AutoIP history identity metadata is immutable'); END;

CREATE TRIGGER asset_classification_evidence_insert_guard
BEFORE INSERT ON asset_classification_evidence
WHEN NEW.state != 'building'
 OR castle_provider_asset_classification_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM remote_asset_projections AS projection
      JOIN provider_accounts AS account USING(account_id)
      JOIN remote_asset_owners AS owner USING(projection_id)
      JOIN remote_asset_versions AS version
        ON version.projection_id = projection.projection_id
       AND version.version_id = NEW.asset_version_id
     WHERE projection.projection_id = NEW.projection_id
       AND owner.owner_user_id = NEW.owner_user_id
       AND account.provider_id = 'autoip'
       AND projection.media_kind IN ('image', 'video')
       AND (
           (projection.media_kind = 'image' AND version.mime_type IN (
               'image/avif', 'image/gif', 'image/jpeg', 'image/png', 'image/webp'
           ))
           OR (projection.media_kind = 'video' AND version.mime_type IN (
               'video/mp4', 'video/quicktime', 'video/webm'
           ))
       )
       AND version.byte_size > 0
       AND (projection.visual_class IS NULL OR projection.visual_class = 'unknown')
       AND json_extract(projection.metadata_json, '$.source') = 'autoip_history'
       AND json_extract(projection.metadata_json, '$.classification_required') = 1
       AND projection.external_asset_id = (
           'history:' || json_extract(projection.metadata_json, '$.task_id')
           || ':output:' || json_extract(projection.metadata_json, '$.output_index')
       )
       AND version.provider_version_id = (
           'history:' || json_extract(projection.metadata_json, '$.task_id')
           || ':' || json_extract(projection.metadata_json, '$.output_index')
       )
       AND json_extract(version.metadata_json, '$.validated_download') = 1
       AND json_extract(version.metadata_json, '$.legacy_history') = 1
       AND castle_provider_autoip_history_identity_matches(
           NEW.owner_user_id,
           account.provider_id,
           projection.account_id,
           projection.projection_id,
           projection.external_asset_id,
           version.version_id,
           version.provider_version_id,
           projection.metadata_json
       ) = 1
       AND version.sha256 IS NOT NULL
       AND length(version.sha256) = 64
       AND version.version_number = (
           SELECT MAX(latest.version_number)
             FROM remote_asset_versions AS latest
            WHERE latest.projection_id = projection.projection_id
       )
       AND 1 = (
           SELECT COUNT(*) FROM remote_asset_versions AS only_version
            WHERE only_version.projection_id = projection.projection_id
       )
 )
 OR EXISTS (
    SELECT 1
      FROM json_each(NEW.character_ids_json) AS selected
      LEFT JOIN canonical_characters AS character
        ON character.character_id = selected.value
       AND character.visual_class = NEW.visual_class
      LEFT JOIN canonical_character_owners AS owner
        ON owner.character_id = character.character_id
       AND owner.owner_user_id = NEW.owner_user_id
     WHERE selected.type != 'text'
        OR character.character_id IS NULL
        OR owner.character_id IS NULL
 )
 OR EXISTS (
    SELECT selected.value
      FROM json_each(NEW.character_ids_json) AS selected
     GROUP BY selected.value
    HAVING COUNT(*) != 1
 )
 OR EXISTS (
    SELECT 1 FROM asset_character_links
     WHERE projection_id = NEW.projection_id
 )
BEGIN SELECT RAISE(ABORT, 'invalid asset classification evidence'); END;

-- A projection may move out of NULL/unknown only while the exact version-bound
-- evidence is being built.  Character classification keeps its older reviewed
-- cascade path: an already linked unknown asset follows the now-classified
-- character when every link resolves to that exact new class.
CREATE TRIGGER asset_visual_classification_evidence_guard
BEFORE UPDATE OF visual_class ON remote_asset_projections
WHEN OLD.visual_class IS NOT NEW.visual_class
 AND NOT (
    OLD.visual_class = 'unknown'
    AND OLD.projection_id NOT LIKE 'autoip-history:%'
    AND COALESCE(json_extract(OLD.metadata_json, '$.source'), '') != 'autoip_history'
    AND EXISTS (
        SELECT 1 FROM asset_character_links
         WHERE projection_id = OLD.projection_id
    )
    AND NOT EXISTS (
        SELECT 1
          FROM asset_character_links AS link
          JOIN canonical_characters AS character USING(character_id)
         WHERE link.projection_id = OLD.projection_id
           AND character.visual_class != NEW.visual_class
    )
 )
 AND NOT EXISTS (
    SELECT 1 FROM asset_classification_evidence AS evidence
     WHERE evidence.projection_id = OLD.projection_id
       AND evidence.visual_class = NEW.visual_class
       AND evidence.state = 'building'
 )
BEGIN SELECT RAISE(ABORT, 'asset visual class requires explicit evidence'); END;

CREATE TRIGGER asset_classification_evidence_seal_guard
BEFORE UPDATE ON asset_classification_evidence
WHEN NOT (
    castle_provider_asset_classification_authorized() = 1
    AND OLD.state = 'building'
    AND NEW.state = 'active'
    AND OLD.classification_id IS NEW.classification_id
    AND OLD.owner_user_id IS NEW.owner_user_id
    AND OLD.projection_id IS NEW.projection_id
    AND OLD.asset_version_id IS NEW.asset_version_id
    AND OLD.visual_class IS NEW.visual_class
    AND OLD.character_ids_json IS NEW.character_ids_json
    AND OLD.classification_version IS NEW.classification_version
    AND OLD.classified_at IS NEW.classified_at
    AND EXISTS (
        SELECT 1 FROM remote_asset_projections
         WHERE projection_id = OLD.projection_id
           AND visual_class = OLD.visual_class
    )
    AND NOT EXISTS (
        SELECT 1
          FROM json_each(OLD.character_ids_json) AS selected
         WHERE NOT EXISTS (
            SELECT 1 FROM asset_character_links AS link
             WHERE link.projection_id = OLD.projection_id
               AND link.character_id = selected.value
         )
    )
    AND (
        SELECT COUNT(*) FROM asset_character_links
         WHERE projection_id = OLD.projection_id
    ) = json_array_length(OLD.character_ids_json)
 )
BEGIN SELECT RAISE(ABORT, 'asset classification evidence cannot be sealed'); END;

CREATE TRIGGER asset_classification_evidence_no_replace
BEFORE INSERT ON asset_classification_evidence
WHEN EXISTS (
    SELECT 1 FROM asset_classification_evidence
     WHERE classification_id = NEW.classification_id
        OR projection_id = NEW.projection_id
)
BEGIN SELECT RAISE(ABORT, 'asset classification evidence is append-only'); END;

CREATE TRIGGER asset_classification_evidence_no_delete
BEFORE DELETE ON asset_classification_evidence
BEGIN SELECT RAISE(ABORT, 'asset classification evidence is append-only'); END;

-- Only the receipt-building transaction may create the reviewed links.  Once
-- active, the exact roster is closed; otherwise a normal same-class link could
-- silently inherit the projection's evidence without being reviewed.
CREATE TRIGGER asset_classification_character_set_frozen
BEFORE INSERT ON asset_character_links
WHEN (
    EXISTS (
        SELECT 1 FROM asset_classification_evidence
         WHERE projection_id = NEW.projection_id
    )
    OR EXISTS (
        SELECT 1 FROM remote_asset_projections
         WHERE projection_id = NEW.projection_id
           AND (
               projection_id LIKE 'autoip-history:%'
               OR json_extract(metadata_json, '$.source') = 'autoip_history'
           )
    )
)
 AND NOT EXISTS (
    SELECT 1
      FROM asset_classification_evidence AS evidence,
           json_each(evidence.character_ids_json) AS selected
     WHERE evidence.projection_id = NEW.projection_id
       AND evidence.state = 'building'
       AND selected.type = 'text'
       AND selected.value = NEW.character_id
 )
BEGIN SELECT RAISE(ABORT, 'classified asset character set is frozen'); END;

-- Classification is bound to the sole exact version present when the receipt
-- is built.  A later version must not inherit projection-level class/character
-- links without its own review, so the version set becomes permanently closed.
CREATE TRIGGER asset_classification_version_set_frozen
BEFORE INSERT ON remote_asset_versions
WHEN EXISTS (
    SELECT 1 FROM asset_classification_evidence
     WHERE projection_id = NEW.projection_id
)
BEGIN SELECT RAISE(ABORT, 'classified asset version set is frozen'); END;
"""

_MIGRATION_13 = r"""
-- A history task is shown as adopted only after every verified output graph
-- has been created.  Per-output projection writes are intentionally
-- retryable, so this immutable task-level seal is the commit point which
-- distinguishes a complete adoption from a recoverable partial attempt.
CREATE TABLE autoip_history_adoption_receipts (
    receipt_id TEXT PRIMARY KEY CHECK (
        length(receipt_id) BETWEEN 1 AND 128
        AND receipt_id = lower(receipt_id)
        AND substr(receipt_id, 1, 1) GLOB '[a-z0-9]'
        AND receipt_id NOT GLOB '*[^a-z0-9._:-]*'
    ),
    owner_user_id TEXT NOT NULL CHECK (
        length(owner_user_id) BETWEEN 1 AND 128
        AND owner_user_id = lower(owner_user_id)
        AND substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'
        AND owner_user_id NOT GLOB '*[^a-z0-9._:-]*'
        AND owner_user_id NOT GLOB 'castle-system:*'
    ),
    account_id TEXT NOT NULL REFERENCES provider_accounts(account_id),
    task_uuid TEXT NOT NULL CHECK (
        length(task_uuid) = 36
        AND task_uuid = lower(task_uuid)
        AND task_uuid NOT GLOB '*[^0-9a-f-]*'
    ),
    output_indexes_json TEXT NOT NULL CHECK (
        json_valid(output_indexes_json)
        AND json_type(output_indexes_json) = 'array'
        AND json_array_length(output_indexes_json) BETWEEN 1 AND 16
    ),
    assets_json TEXT NOT NULL CHECK (
        json_valid(assets_json)
        AND json_type(assets_json) = 'array'
        AND json_array_length(assets_json) BETWEEN 1 AND 16
        AND json_array_length(assets_json) = json_array_length(output_indexes_json)
    ),
    adoption_version INTEGER NOT NULL CHECK (adoption_version = 1),
    sealed_at TEXT NOT NULL CHECK (
        length(sealed_at) BETWEEN 1 AND 80
        AND instr(sealed_at, char(0)) = 0
    ),
    UNIQUE (owner_user_id, account_id, task_uuid)
);

CREATE INDEX autoip_history_adoption_receipts_by_owner
ON autoip_history_adoption_receipts(owner_user_id, account_id, task_uuid);

CREATE TRIGGER autoip_history_adoption_receipt_insert_guard
BEFORE INSERT ON autoip_history_adoption_receipts
WHEN castle_provider_autoip_history_adoption_authorized() != 1
 OR NOT EXISTS (
    SELECT 1
      FROM provider_accounts AS account
      JOIN provider_account_owners AS owner USING(account_id)
     WHERE account.account_id = NEW.account_id
       AND account.provider_id = 'autoip'
       AND owner.owner_user_id = NEW.owner_user_id
 )
BEGIN SELECT RAISE(ABORT, 'invalid AutoIP history adoption receipt'); END;

CREATE TRIGGER autoip_history_adoption_receipt_no_replace
BEFORE INSERT ON autoip_history_adoption_receipts
WHEN EXISTS (
    SELECT 1 FROM autoip_history_adoption_receipts
     WHERE receipt_id = NEW.receipt_id
        OR (
            owner_user_id = NEW.owner_user_id
            AND account_id = NEW.account_id
            AND task_uuid = NEW.task_uuid
        )
)
BEGIN SELECT RAISE(ABORT, 'AutoIP history adoption receipt is append-only'); END;

CREATE TRIGGER autoip_history_adoption_receipt_no_update
BEFORE UPDATE ON autoip_history_adoption_receipts
BEGIN SELECT RAISE(ABORT, 'AutoIP history adoption receipt is append-only'); END;

CREATE TRIGGER autoip_history_adoption_receipt_no_delete
BEFORE DELETE ON autoip_history_adoption_receipts
BEGIN SELECT RAISE(ABORT, 'AutoIP history adoption receipt is append-only'); END;
"""

_MIGRATION_14 = r"""
-- Parallel work is an operator capacity decision, not an account-level
-- authorization boundary.  Exact request idempotency remains enforced by the
-- UNIQUE(account_id, client_request_id) constraints on outbox and tasks.
DROP INDEX IF EXISTS one_autoip_task_in_flight_per_account;
"""

_MIGRATION_15 = r"""
-- A provider GET may authoritatively report completion after a previously
-- observed provider failure.  Normal task transitions keep terminal facts
-- immutable; only the store's reconciliation-only authorization value (2)
-- may correct failed -> completed for the same durable external task.
DROP TRIGGER IF EXISTS external_task_controlled_update;
CREATE TRIGGER external_task_controlled_update
BEFORE UPDATE ON external_tasks
WHEN castle_provider_task_update_authorized() NOT IN (1, 2)
BEGIN SELECT RAISE(ABORT, 'external task requires controlled transition'); END;

DROP TRIGGER IF EXISTS external_task_state_transition;
CREATE TRIGGER external_task_state_transition
BEFORE UPDATE OF state ON external_tasks
WHEN OLD.state != NEW.state AND NOT (
    (OLD.state = 'created' AND NEW.state IN ('submitted', 'submission_uncertain', 'cancelled')) OR
    (OLD.state = 'submission_uncertain' AND NEW.state IN (
        'submitted', 'queued', 'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'submitted' AND NEW.state IN (
        'queued', 'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'queued' AND NEW.state IN (
        'running', 'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (OLD.state = 'running' AND NEW.state IN (
        'completed', 'failed', 'cancelled', 'timeout'
    )) OR
    (
        OLD.state = 'failed'
        AND NEW.state = 'completed'
        AND castle_provider_task_update_authorized() = 2
    )
)
BEGIN SELECT RAISE(ABORT, 'invalid external task state transition'); END;

DROP TRIGGER IF EXISTS external_task_terminal_immutable;
CREATE TRIGGER external_task_terminal_immutable
BEFORE UPDATE ON external_tasks
WHEN OLD.state IN ('completed', 'failed', 'cancelled', 'timeout') AND (
    OLD.state IS NOT NEW.state
    OR OLD.external_task_id IS NOT NEW.external_task_id
    OR OLD.state_revision IS NOT NEW.state_revision
    OR OLD.credits_charged IS NOT NEW.credits_charged
    OR OLD.remaining_credits IS NOT NEW.remaining_credits
    OR OLD.detail IS NOT NEW.detail
    OR OLD.updated_at IS NOT NEW.updated_at
    OR OLD.terminal_at IS NOT NEW.terminal_at
) AND NOT (
    castle_provider_task_update_authorized() = 2
    AND OLD.state = 'failed'
    AND NEW.state = 'completed'
    AND OLD.external_task_id IS NOT NULL
    AND OLD.external_task_id IS NEW.external_task_id
    AND NEW.state_revision = OLD.state_revision + 1
    AND (OLD.credits_charged IS NULL OR OLD.credits_charged IS NEW.credits_charged)
    AND (OLD.remaining_credits IS NULL OR OLD.remaining_credits IS NEW.remaining_credits)
    AND OLD.terminal_at IS NEW.terminal_at
)
BEGIN SELECT RAISE(ABORT, 'terminal external task facts are immutable'); END;
"""

_MIGRATION_16 = r"""
-- Character-generation models are an owner-scoped, append-only fact.  The
-- stored names are relative to server-owned model roots; they are never host
-- paths and are not serialized to the browser.  A new model is a new revision
-- rather than an update to an existing row.
CREATE TABLE canonical_character_model_bindings (
    binding_id TEXT PRIMARY KEY CHECK (
        length(binding_id) BETWEEN 1 AND 128
        AND binding_id = lower(binding_id)
        AND substr(binding_id, 1, 1) GLOB '[a-z0-9]'
        AND binding_id NOT GLOB '*[^a-z0-9._:-]*'
    ),
    owner_user_id TEXT NOT NULL CHECK (
        length(owner_user_id) BETWEEN 1 AND 128
        AND owner_user_id = lower(owner_user_id)
        AND substr(owner_user_id, 1, 1) GLOB '[a-z0-9]'
        AND owner_user_id NOT GLOB '*[^a-z0-9._:-]*'
        AND owner_user_id NOT GLOB 'castle-system:*'
    ),
    character_id TEXT NOT NULL REFERENCES canonical_characters(character_id),
    capability_id TEXT NOT NULL CHECK (
        length(capability_id) BETWEEN 1 AND 128
        AND capability_id = lower(capability_id)
        AND substr(capability_id, 1, 1) GLOB '[a-z0-9]'
        AND capability_id NOT GLOB '*[^a-z0-9._:-]*'
    ),
    base_model_name TEXT NOT NULL CHECK (
        length(base_model_name) BETWEEN 1 AND 128
        AND instr(base_model_name, char(0)) = 0
        AND instr(base_model_name, '/') = 0
        AND instr(base_model_name, char(92)) = 0
        AND lower(substr(base_model_name, -12)) = '.safetensors'
    ),
    base_model_sha256 TEXT NOT NULL CHECK (
        length(base_model_sha256) = 64
        AND base_model_sha256 = lower(base_model_sha256)
        AND base_model_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    model_name TEXT NOT NULL CHECK (
        length(model_name) BETWEEN 1 AND 300
        AND instr(model_name, char(0)) = 0
        AND substr(model_name, 1, 1) != '/'
        AND instr(model_name, char(92)) = 0
        AND instr(model_name, '//') = 0
        AND model_name NOT LIKE '../%'
        AND model_name NOT LIKE './%'
        AND model_name NOT LIKE '%/../%'
        AND model_name NOT LIKE '%/./%'
        AND model_name NOT LIKE '%/..'
        AND model_name NOT LIKE '%/.'
        AND lower(substr(model_name, -12)) = '.safetensors'
    ),
    model_sha256 TEXT NOT NULL CHECK (
        length(model_sha256) = 64
        AND model_sha256 = lower(model_sha256)
        AND model_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    trigger_token TEXT NOT NULL CHECK (
        length(trigger_token) BETWEEN 1 AND 64
        AND instr(trigger_token, char(0)) = 0
    ),
    model_strength REAL NOT NULL CHECK (model_strength BETWEEN 0 AND 2),
    clip_strength REAL NOT NULL CHECK (clip_strength BETWEEN 0 AND 2),
    training_receipt_schema TEXT NOT NULL CHECK (
        training_receipt_schema = 'castle.clipmode-sdxl-training-receipt.v1'
    ),
    training_receipt_sha256 TEXT NOT NULL CHECK (
        length(training_receipt_sha256) = 64
        AND training_receipt_sha256 = lower(training_receipt_sha256)
        AND training_receipt_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    qualification_state TEXT NOT NULL CHECK (
        qualification_state IN ('structurally_verified', 'quality_qualified')
    ),
    qualification_revision INTEGER NOT NULL CHECK (qualification_revision > 0),
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at TEXT NOT NULL CHECK (
        length(created_at) BETWEEN 1 AND 80 AND instr(created_at, char(0)) = 0
    ),
    UNIQUE (owner_user_id, character_id, capability_id, revision),
    UNIQUE (owner_user_id, character_id, capability_id, model_sha256)
);

CREATE INDEX canonical_character_model_bindings_latest
ON canonical_character_model_bindings(
    owner_user_id, character_id, capability_id, revision DESC
);

CREATE TRIGGER canonical_character_model_binding_owner_guard
BEFORE INSERT ON canonical_character_model_bindings
WHEN NOT EXISTS (
    SELECT 1 FROM canonical_character_owners AS owner
     WHERE owner.character_id = NEW.character_id
       AND owner.owner_user_id = NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'character model binding owner mismatch'); END;

CREATE TRIGGER canonical_character_model_binding_revision_guard
BEFORE INSERT ON canonical_character_model_bindings
WHEN NEW.revision != COALESCE((
    SELECT MAX(binding.revision) + 1
      FROM canonical_character_model_bindings AS binding
     WHERE binding.owner_user_id = NEW.owner_user_id
       AND binding.character_id = NEW.character_id
       AND binding.capability_id = NEW.capability_id
), 1)
BEGIN SELECT RAISE(ABORT, 'character model binding revision must be sequential'); END;

CREATE TRIGGER canonical_character_model_binding_no_replace
BEFORE INSERT ON canonical_character_model_bindings
WHEN EXISTS (
    SELECT 1 FROM canonical_character_model_bindings AS binding
     WHERE binding.binding_id = NEW.binding_id
        OR (
            binding.owner_user_id = NEW.owner_user_id
            AND binding.character_id = NEW.character_id
            AND binding.capability_id = NEW.capability_id
            AND binding.revision = NEW.revision
        )
)
BEGIN SELECT RAISE(ABORT, 'character model binding is append-only'); END;

CREATE TRIGGER canonical_character_model_binding_no_update
BEFORE UPDATE ON canonical_character_model_bindings
BEGIN SELECT RAISE(ABORT, 'character model binding is append-only'); END;

CREATE TRIGGER canonical_character_model_binding_no_delete
BEFORE DELETE ON canonical_character_model_bindings
BEGIN SELECT RAISE(ABORT, 'character model binding is append-only'); END;
"""


class ProviderFabricStore:
    """SQLite repository with explicit public/private serialization boundaries."""

    def __init__(
        self,
        database_path: str | os.PathLike[str] | None = None,
        *,
        clock: Callable[[], str] = _now,
        epoch_clock: Callable[[], float] = time.time,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.path = Path(database_path).expanduser() if database_path else default_database_path()
        self._clock = clock
        self._epoch_clock = epoch_clock
        self._timeout = float(timeout_seconds)
        if not 0 < self._timeout <= 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        self._prepare_path()
        self.initialize()

    def _prepare_path(self) -> None:
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not parent_existed:
            try:
                self.path.parent.chmod(0o700)
            except PermissionError:
                pass
        if self.path.exists() and self.path.is_symlink():
            raise PermissionError("provider-fabric database cannot be a symlink")

    def _connect(
        self,
        *,
        asset_classification: bool = False,
        autoip_history_adoption: bool = False,
        credential_finalize: bool = False,
        import_insert: bool = False,
        provider_account_insert: bool = False,
        task_insert: bool = False,
        task_update: bool = False,
        task_reconciliation: bool = False,
        task_event_insert: bool = False,
        outbox_insert: bool = False,
        outbox_update: bool = False,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self._timeout,
            isolation_level=None,
            uri=False,
        )
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "castle_provider_asset_classification_authorized",
            0,
            lambda: int(asset_classification),
        )
        connection.create_function(
            "castle_provider_autoip_history_adoption_authorized",
            0,
            lambda: int(autoip_history_adoption),
        )
        connection.create_function(
            "castle_provider_autoip_history_identity_matches",
            8,
            _sqlite_matches_deterministic_autoip_history_identity,
        )
        connection.create_function(
            "castle_provider_credential_finalize_authorized",
            0,
            lambda: int(credential_finalize),
        )
        connection.create_function(
            "castle_provider_import_insert_authorized",
            0,
            lambda: int(import_insert),
        )
        connection.create_function(
            "castle_provider_account_insert_authorized",
            0,
            lambda: int(provider_account_insert),
        )
        connection.create_function(
            "castle_provider_task_event_insert_authorized",
            0,
            lambda: int(task_event_insert),
        )
        connection.create_function(
            "castle_provider_task_insert_authorized",
            0,
            lambda: int(task_insert),
        )
        connection.create_function(
            "castle_provider_task_update_authorized",
            0,
            lambda: 2 if task_reconciliation else int(task_update),
        )
        connection.create_function(
            "castle_provider_outbox_insert_authorized",
            0,
            lambda: int(outbox_insert),
        )
        connection.create_function(
            "castle_provider_outbox_update_authorized",
            0,
            lambda: int(outbox_update),
        )
        connection.create_function(
            "castle_provider_file_locator_is_canonical",
            1,
            lambda value: int(is_safe_durable_file_secret_locator(value)),
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a read connection and always close its file descriptors."""

        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(
        self,
        *,
        immediate: bool = True,
        asset_classification: bool = False,
        autoip_history_adoption: bool = False,
        credential_finalize: bool = False,
        import_insert: bool = False,
        provider_account_insert: bool = False,
        task_insert: bool = False,
        task_update: bool = False,
        task_reconciliation: bool = False,
        task_event_insert: bool = False,
        outbox_insert: bool = False,
        outbox_update: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect(
            asset_classification=asset_classification,
            autoip_history_adoption=autoip_history_adoption,
            credential_finalize=credential_finalize,
            import_insert=import_insert,
            provider_account_insert=provider_account_insert,
            task_insert=task_insert,
            task_update=task_update,
            task_reconciliation=task_reconciliation,
            task_event_insert=task_event_insert,
            outbox_insert=outbox_insert,
            outbox_update=outbox_update,
        )
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def initialize(self) -> int:
        """Apply every missing numbered migration; safe to call repeatedly.

        A migration owns its entire transaction, records itself only after all
        DDL succeeds, and must use ``IF [NOT] EXISTS`` where practical so a
        process can recover after an interrupted first initialization.
        """

        connection = self._connect()
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY CHECK (version > 0),
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL
                )"""
            )
            previous_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if previous_version > SCHEMA_VERSION:
                raise ProviderFabricStoreError(
                    f"database schema {previous_version} is newer than supported {SCHEMA_VERSION}"
                )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            unsupported = {version for version in applied if version > SCHEMA_VERSION}
            if unsupported:
                raise ProviderFabricStoreError(
                    f"database has unsupported migrations: {sorted(unsupported)}"
                )
            if previous_version and previous_version not in applied:
                raise ProviderFabricStoreError(
                    "database user_version has no matching migration record"
                )
            if 1 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_1
                    + """
                    INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        1,
                        'provider_fabric_foundation',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 1;
                    COMMIT;
                    """
                )
                applied.add(1)
            if 2 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_2
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        2,
                        'durable_webhook_replay_claims',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 2;
                    COMMIT;
                    """
                )
                applied.add(2)
            if 3 not in applied:
                # SQLite's documented parent-table rebuild requires FK
                # enforcement to be disabled *before* BEGIN.  The migration
                # itself still runs foreign_key_check inside the transaction,
                # so a bad copy rolls back together with its migration row.
                connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + _MIGRATION_3
                        + """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (
                            3,
                            'credentialless_local_provider_accounts',
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        );
                        PRAGMA user_version = 3;
                        COMMIT;
                        """
                    )
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")
                applied.add(3)
            if 4 not in applied:
                connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + _MIGRATION_4
                        + """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (
                            4,
                            'credentialless_clipmode_import_accounts',
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        );
                        PRAGMA user_version = 4;
                        COMMIT;
                        """
                    )
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")
                applied.add(4)
            if 5 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_5
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        5,
                        'owner_scoped_intent_status',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 5;
                    COMMIT;
                    """
                )
                applied.add(5)
            if 6 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_6
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        6,
                        'owner_scoped_character_media_spine',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 6;
                    COMMIT;
                    """
                )
                applied.add(6)
            if 7 not in applied:
                connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + _MIGRATION_7
                        + """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (
                            7,
                            'actor_scoped_provider_execution',
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        );
                        PRAGMA user_version = 7;
                        COMMIT;
                        """
                    )
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")
                applied.add(7)
            if 8 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_8
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        8,
                        'owner_scoped_offline_import_spine',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 8;
                    COMMIT;
                    """
                )
                applied.add(8)
            if 9 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_9
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        9,
                        'offline_import_lineage_and_plan_evidence',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 9;
                    COMMIT;
                    """
                )
                applied.add(9)
            if 10 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_10
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        10,
                        'owner_scoped_asset_catalog_annotations',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 10;
                    COMMIT;
                    """
                )
                applied.add(10)
            if 11 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_11
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        11,
                        'owner_scoped_all_media_catalog_roles',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 11;
                    COMMIT;
                    """
                )
                applied.add(11)
            if 12 not in applied:
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + _MIGRATION_12
                        + """
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES (
                            12,
                            'owner_scoped_asset_classification_evidence',
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        );
                        PRAGMA user_version = 12;
                        COMMIT;
                        """
                    )
                except sqlite3.IntegrityError as error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    if str(error) == (
                        "schema 12 requires AutoIP history assets to remain "
                        "unclassified"
                    ):
                        raise ProviderFabricStoreError(str(error)) from error
                    raise
                applied.add(12)
            if 13 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_13
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        13,
                        'sealed_autoip_history_task_adoptions',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 13;
                    COMMIT;
                    """
                )
                applied.add(13)
            if 14 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_14
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        14,
                        'parallel_autoip_account_tasks',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 14;
                    COMMIT;
                    """
                )
                applied.add(14)
            if 15 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_15
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        15,
                        'authoritative_terminal_completion_reconciliation',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 15;
                    COMMIT;
                    """
                )
                applied.add(15)
            if 16 not in applied:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _MIGRATION_16
                    + """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (
                        16,
                        'owner_scoped_character_model_bindings',
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    );
                    PRAGMA user_version = 16;
                    COMMIT;
                    """
                )
                applied.add(16)
            if applied != set(range(1, SCHEMA_VERSION + 1)):
                raise ProviderFabricStoreError("database migration history is incomplete")
            row = connection.execute("PRAGMA user_version").fetchone()
            version = int(row[0])
        finally:
            connection.close()
        self.path.chmod(0o600)
        return version

    @property
    def schema_version(self) -> int:
        with self._read_connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @property
    def journal_mode(self) -> str:
        with self._read_connection() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def register_provider_account(
        self,
        account_id: str,
        provider_id: str,
        display_name: str,
        credential_ref: SecretReference,
        *,
        api_base: str | None = None,
        enabled: bool = False,
    ) -> dict[str, Any]:
        account_id = _stable_id(account_id, "account_id")
        provider_id = _stable_id(provider_id, "provider_id")
        if provider_id == "local_comfy":
            raise ValueError("local_comfy accounts must be registered without a credential")
        display_name = _external_id(display_name, "display_name")
        credential_ref = _credential_reference(credential_ref)
        api_base = _optional_text(api_base, "api_base", maximum=4096)
        if api_base:
            parsed = urlsplit(api_base)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("api_base must be an HTTPS URL without credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("api_base cannot include query or fragment fields")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        now = self._clock()
        with self._transaction(
            credential_finalize=True, provider_account_insert=True
        ) as connection:
            existing = connection.execute(
                """SELECT a.provider_id, o.owner_user_id
                   FROM provider_accounts AS a
                   LEFT JOIN provider_account_owners AS o
                     ON o.account_id = a.account_id
                   WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if existing is not None and existing["owner_user_id"] is not None:
                raise StoreConflictError("owned provider account requires owner-scoped mutation")
            if existing and existing["provider_id"] != provider_id:
                raise StoreConflictError("an account cannot change providers")
            if existing is None:
                connection.execute(
                    """INSERT INTO provider_accounts(
                           account_id, provider_id, display_name, credential_kind,
                           credential_locator, api_base, enabled, created_at, updated_at,
                           credential_state
                       ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 'pending')""",
                    (
                        account_id,
                        provider_id,
                        display_name,
                        credential_ref.kind.value,
                        credential_ref.locator,
                        api_base,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """UPDATE provider_accounts
                          SET credential_state = 'configured', enabled = ?, updated_at = ?
                        WHERE account_id = ?""",
                    (int(enabled), now, account_id),
                )
            else:
                connection.execute(
                    """UPDATE provider_accounts SET
                           display_name = ?, credential_kind = ?, credential_locator = ?,
                           api_base = ?, enabled = ?, updated_at = ?
                       WHERE account_id = ?""",
                    (
                        display_name,
                        credential_ref.kind.value,
                        credential_ref.locator,
                        api_base,
                        int(enabled),
                        now,
                        account_id,
                    ),
                )
        return self.get_provider_account(account_id)

    def register_local_provider_account(
        self,
        account_id: str,
        display_name: str,
        *,
        api_base: str = "http://127.0.0.1:8188",
        enabled: bool = False,
    ) -> dict[str, Any]:
        """Register the loopback Comfy runtime without inventing a secret."""

        account_id = _stable_id(account_id, "account_id")
        display_name = _external_id(display_name, "display_name")
        api_base = _optional_text(api_base, "api_base", maximum=4096)
        if api_base is None:
            raise ValueError("local api_base is required")
        parsed = urlsplit(api_base)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("local api_base must be a loopback HTTP origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("local api_base has an invalid port") from error
        if port is None or not 1 <= port <= 65535:
            raise ValueError("local api_base requires an explicit valid port")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        now = self._clock()
        with self._transaction(provider_account_insert=True) as connection:
            existing = connection.execute(
                """SELECT a.provider_id, o.owner_user_id
                   FROM provider_accounts AS a
                   LEFT JOIN provider_account_owners AS o
                     ON o.account_id = a.account_id
                   WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if existing is not None and existing["owner_user_id"] is not None:
                raise StoreConflictError("owned provider account requires owner-scoped mutation")
            if existing and existing["provider_id"] != "local_comfy":
                raise StoreConflictError("an account cannot change providers")
            if existing is None:
                connection.execute(
                    """INSERT INTO provider_accounts(
                           account_id, provider_id, display_name, credential_kind,
                           credential_locator, api_base, enabled, created_at, updated_at,
                           credential_state
                       ) VALUES (?, 'local_comfy', ?, 'none', NULL, ?, ?, ?, ?,
                                 'not_applicable')""",
                    (account_id, display_name, api_base.rstrip("/"), int(enabled), now, now),
                )
            else:
                connection.execute(
                    """UPDATE provider_accounts SET display_name = ?, api_base = ?,
                           enabled = ?, updated_at = ? WHERE account_id = ?""",
                    (display_name, api_base.rstrip("/"), int(enabled), now, account_id),
                )
        return self.get_provider_account(account_id)

    def register_manual_import_account(
        self,
        account_id: str,
        provider_id: str,
        display_name: str,
        *,
        enabled: bool = False,
    ) -> dict[str, Any]:
        """Register a metadata-import namespace with no live provider access.

        Today only ClipMode has this boundary.  ``enabled`` must remain false:
        importing a vendor export is not evidence of a supported generation or
        sync credential contract.
        """

        account_id = _stable_id(account_id, "account_id")
        provider_id = _stable_id(provider_id, "provider_id")
        display_name = _external_id(display_name, "display_name")
        if provider_id != "clipmode":
            raise ValueError("manual import accounts are supported only for clipmode")
        if enabled is not False:
            raise ValueError("manual import accounts cannot enable live execution")
        now = self._clock()
        with self._transaction(provider_account_insert=True) as connection:
            existing = connection.execute(
                """SELECT a.provider_id, a.credential_kind, o.owner_user_id
                   FROM provider_accounts AS a
                   LEFT JOIN provider_account_owners AS o
                     ON o.account_id = a.account_id
                   WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if existing is not None and existing["owner_user_id"] is not None:
                raise StoreConflictError("owned provider account requires owner-scoped mutation")
            if existing and existing["provider_id"] != provider_id:
                raise StoreConflictError("an account cannot change providers")
            if existing and existing["credential_kind"] != "none":
                raise StoreConflictError(
                    "a configured provider account cannot become metadata-import-only"
                )
            if existing is None:
                connection.execute(
                    """INSERT INTO provider_accounts(
                           account_id, provider_id, display_name, credential_kind,
                           credential_locator, api_base, enabled, created_at, updated_at,
                           credential_state
                       ) VALUES (?, ?, ?, 'none', NULL, NULL, 0, ?, ?,
                                 'not_applicable')""",
                    (account_id, provider_id, display_name, now, now),
                )
            else:
                connection.execute(
                    """UPDATE provider_accounts SET display_name = ?,
                           credential_kind = 'none', credential_locator = NULL,
                           api_base = NULL, enabled = 0, updated_at = ?
                       WHERE account_id = ?""",
                    (display_name, now, account_id),
                )
        return self.get_provider_account(account_id)

    def register_owned_manual_import_account(
        self,
        owner_user_id: str,
        account_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        """Atomically create one private, non-executable ClipMode namespace.

        ``account_id`` is supplied by the trusted service after it derives a
        stable namespace from the authenticated actor and reviewed provider
        scope.  The store validates its durable identifier shape but never
        derives identity from a display name or claims the fixed legacy
        ``clipmode-import`` account.  The row has no credential, API base, or
        execution entitlement and can never be enabled.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if owner_user_id.startswith("castle-system:"):
            raise ValueError("manual import accounts require a user owner")
        account_id = _stable_id(account_id, "account_id")
        if account_id == "clipmode-import":
            raise ValueError("the legacy clipmode-import namespace cannot be claimed")
        display_name = _external_id(display_name, "display_name")
        now = self._clock()
        try:
            with self._transaction(provider_account_insert=True) as connection:
                existing = connection.execute(
                    """SELECT a.provider_id, a.credential_kind,
                              a.credential_locator, a.api_base, a.enabled,
                              a.credential_state, o.owner_user_id,
                              e.access_scope
                         FROM provider_accounts AS a
                         LEFT JOIN provider_account_owners AS o USING(account_id)
                         LEFT JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ?""",
                    (account_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """INSERT INTO provider_accounts(
                               account_id, provider_id, display_name,
                               credential_kind, credential_locator, api_base,
                               enabled, created_at, updated_at, credential_state
                           ) VALUES (?, 'clipmode', ?, 'none', NULL, NULL, 0,
                                     ?, ?, 'not_applicable')""",
                        (account_id, display_name, now, now),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_owners(
                               account_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (account_id, owner_user_id, now),
                    )
                else:
                    exact_namespace = (
                        existing["owner_user_id"] == owner_user_id
                        and existing["provider_id"] == "clipmode"
                        and existing["credential_kind"] == "none"
                        and existing["credential_locator"] is None
                        and existing["api_base"] is None
                        and int(existing["enabled"]) == 0
                        and existing["credential_state"] == "not_applicable"
                        and existing["access_scope"] is None
                    )
                    if not exact_namespace:
                        raise StoreConflictError("provider account is unavailable")
                    connection.execute(
                        """UPDATE provider_accounts
                              SET display_name = ?, updated_at = ?
                            WHERE account_id = ?""",
                        (display_name, now, account_id),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        result = self.get_owned_provider_account(owner_user_id, account_id)
        result["access_scope"] = "manual_import"
        return result

    def set_provider_account_enabled(self, account_id: str, enabled: bool) -> dict[str, Any]:
        """Change only the administrative execution gate for an account."""

        account_id = _stable_id(account_id, "account_id")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        with self._transaction() as connection:
            account = connection.execute(
                """SELECT a.provider_id, a.credential_kind, o.owner_user_id
                   FROM provider_accounts AS a
                   LEFT JOIN provider_account_owners AS o
                     ON o.account_id = a.account_id
                   WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if account is None:
                raise KeyError(account_id)
            if account["owner_user_id"] is not None:
                raise StoreConflictError("owned provider account requires owner-scoped mutation")
            if (
                enabled
                and account["credential_kind"] == "none"
                and account["provider_id"] != "local_comfy"
            ):
                raise ValueError("manual import accounts cannot enable live execution")
            cursor = connection.execute(
                """UPDATE provider_accounts SET enabled = ?, updated_at = ?
                   WHERE account_id = ?""",
                (int(enabled), self._clock(), account_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(account_id)
        return self.get_provider_account(account_id)

    def get_provider_account(self, account_id: str) -> dict[str, Any]:
        account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
        if row is None:
            raise KeyError(account_id)
        return self._public_provider_account(row)

    @staticmethod
    def _public_provider_account(row: sqlite3.Row) -> dict[str, Any]:
        """Serialize an account without exposing its credential locator.

        A file locator is operationally sensitive even though it is not the
        credential value: it reveals deployment layout and can become a secret
        read primitive if a future caller resolves it.  Browser-facing reads
        therefore receive only the credential kind and configured state.
        """

        return {
            "account_id": row["account_id"],
            "provider_id": row["provider_id"],
            "display_name": row["display_name"],
            "api_base": row["api_base"],
            "enabled": bool(row["enabled"]),
            "authentication": {
                "kind": row["credential_kind"],
                "configured": row["credential_state"] == "configured",
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_provider_accounts(
        self,
        *,
        provider_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        """Return a deterministic public account inventory.

        This deliberately has no pagination because provider accounts are an
        operator-managed, naturally small set.  Media and task inventories use
        separate bounded APIs.
        """

        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        if not isinstance(include_disabled, bool):
            raise ValueError("include_disabled must be boolean")
        clauses: list[str] = []
        values: list[Any] = []
        if provider_id is not None:
            clauses.append("provider_id = ?")
            values.append(provider_id)
        if not include_disabled:
            clauses.append("enabled = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_accounts"
                + where
                + " ORDER BY provider_id, account_id",
                values,
            ).fetchall()
        return [self._public_provider_account(row) for row in rows]

    def bind_provider_account_owner(
        self,
        owner_user_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        """Explicitly bind one previously unowned provider account.

        This method is intentionally not exposed as a browser route.  It is an
        operator migration primitive; once written, the binding cannot be
        reassigned or deleted.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        try:
            with self._transaction() as connection:
                account = connection.execute(
                    "SELECT provider_id FROM provider_accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if account is None:
                    raise KeyError(account_id)
                existing = connection.execute(
                    """SELECT owner_user_id FROM provider_account_owners
                       WHERE account_id = ?""",
                    (account_id,),
                ).fetchone()
                if existing is None:
                    legacy_rows = connection.execute(
                        """SELECT 1 FROM capability_snapshots
                           WHERE account_id = ?
                           UNION ALL
                           SELECT 1 FROM provider_character_links
                           WHERE account_id = ?
                           UNION ALL
                           SELECT 1 FROM remote_asset_projections
                           WHERE account_id = ?
                           UNION ALL
                           SELECT 1 FROM route_decisions
                           WHERE selected_account_id = ?
                           UNION ALL
                           SELECT 1 FROM submission_outbox
                           WHERE account_id = ?
                           UNION ALL
                           SELECT 1 FROM external_tasks
                           WHERE account_id = ?
                           UNION ALL
                           SELECT 1 FROM credit_ledger
                           WHERE account_id = ?
                           LIMIT 1""",
                        (account_id,) * 7,
                    ).fetchone()
                    if legacy_rows is not None:
                        raise StoreConflictError(
                            "provider account requires reviewed legacy adoption"
                        )
                    valid_owner_shape = (
                        account["provider_id"] == "local_comfy"
                        and owner_user_id == SYSTEM_LOCAL_OWNER_ID
                    ) or (
                        account["provider_id"] != "local_comfy"
                        and not owner_user_id.startswith("castle-system:")
                    )
                    if not valid_owner_shape:
                        raise StoreConflictError("invalid provider account owner shape")
                    connection.execute(
                        """INSERT INTO provider_account_owners(
                               account_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (account_id, owner_user_id, self._clock()),
                    )
                else:
                    valid_owner_shape = (
                        account["provider_id"] == "local_comfy"
                        and owner_user_id == SYSTEM_LOCAL_OWNER_ID
                    ) or (
                        account["provider_id"] != "local_comfy"
                        and not owner_user_id.startswith("castle-system:")
                    )
                    if not valid_owner_shape:
                        raise StoreConflictError("invalid provider account owner shape")
                    if existing["owner_user_id"] != owner_user_id:
                        raise StoreConflictError("provider account is unavailable")
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_provider_account(owner_user_id, account_id)

    def get_owned_provider_account(
        self,
        owner_user_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT a.* FROM provider_accounts AS a
                   JOIN provider_account_owners AS o
                     ON o.account_id = a.account_id
                    AND o.owner_user_id = ?
                   WHERE a.account_id = ?""",
                (owner_user_id, account_id),
            ).fetchone()
        if row is None:
            raise KeyError(account_id)
        return self._public_provider_account(row)

    def list_owned_provider_accounts(
        self,
        owner_user_id: str,
        *,
        provider_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        if not isinstance(include_disabled, bool):
            raise ValueError("include_disabled must be boolean")
        clauses = ["o.owner_user_id = ?"]
        values: list[Any] = [owner_user_id]
        if provider_id is not None:
            clauses.append("a.provider_id = ?")
            values.append(provider_id)
        if not include_disabled:
            clauses.append("a.enabled = 1")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT a.* FROM provider_accounts AS a
                   JOIN provider_account_owners AS o ON o.account_id = a.account_id
                   WHERE """
                + " AND ".join(clauses)
                + " ORDER BY a.provider_id, a.account_id",
                values,
            ).fetchall()
        return [self._public_provider_account(row) for row in rows]

    @staticmethod
    def _external_api_base(value: str | None) -> str | None:
        api_base = _optional_text(value, "api_base", maximum=4096)
        if api_base:
            parsed = urlsplit(api_base)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("api_base must be an HTTPS URL without credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("api_base cannot include query or fragment fields")
        return api_base

    @staticmethod
    def _local_api_base(value: str) -> str:
        api_base = _optional_text(value, "api_base", maximum=4096)
        if api_base is None:
            raise ValueError("local api_base is required")
        parsed = urlsplit(api_base)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("local api_base must be a loopback HTTP origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("local api_base has an invalid port") from error
        if port is None or not 1 <= port <= 65535:
            raise ValueError("local api_base requires an explicit valid port")
        return api_base.rstrip("/")

    @staticmethod
    def _public_accessible_account(row: sqlite3.Row) -> dict[str, Any]:
        result = ProviderFabricStore._public_provider_account(row)
        result["access_scope"] = row["access_scope"]
        return result

    def register_owned_provider_account(
        self,
        owner_user_id: str,
        account_id: str,
        provider_id: str,
        display_name: str,
        credential_ref: SecretReference,
        *,
        api_base: str | None = None,
        enabled: bool = False,
    ) -> dict[str, Any]:
        """Create or refresh one private external account for its exact owner.

        An unowned legacy row is never claimed by this method.  A v6 row which
        already has the same immutable owner may be completed with its explicit
        private execution entitlement because the v6 owner-binding guard proves
        that it has no legacy execution graph.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if owner_user_id.startswith("castle-system:"):
            raise ValueError("external provider accounts require a user owner")
        account_id = _stable_id(account_id, "account_id")
        provider_id = _stable_id(provider_id, "provider_id")
        if provider_id == "local_comfy":
            raise ValueError("local_comfy must use the system local account contract")
        display_name = _external_id(display_name, "display_name")
        credential_ref = _credential_reference(credential_ref)
        api_base = self._external_api_base(api_base)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        now = self._clock()
        try:
            with self._transaction(
                credential_finalize=True, provider_account_insert=True
            ) as connection:
                existing = connection.execute(
                    """SELECT a.provider_id, a.credential_state,
                              o.owner_user_id, e.access_scope
                         FROM provider_accounts AS a
                         LEFT JOIN provider_account_owners AS o USING(account_id)
                         LEFT JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ?""",
                    (account_id,),
                ).fetchone()
                if existing is not None:
                    if existing["owner_user_id"] is None:
                        raise StoreConflictError(
                            "unowned provider account requires reviewed legacy adoption"
                        )
                    if existing["owner_user_id"] != owner_user_id:
                        raise StoreConflictError("provider account is unavailable")
                    if existing["credential_state"] == "pending":
                        raise StoreConflictError(
                            "pending credential requires reservation finalization"
                        )
                    if existing["provider_id"] != provider_id:
                        raise StoreConflictError("an account cannot change providers")
                    if existing["access_scope"] not in {None, "private"}:
                        raise StoreConflictError("provider account entitlement is incompatible")
                    connection.execute(
                        """UPDATE provider_accounts SET
                               display_name = ?, credential_kind = ?,
                               credential_locator = ?, api_base = ?, enabled = ?,
                               credential_state = 'configured', updated_at = ?
                           WHERE account_id = ?""",
                        (
                            display_name,
                            credential_ref.kind.value,
                            credential_ref.locator,
                            api_base,
                            int(enabled),
                            now,
                            account_id,
                        ),
                    )
                    if existing["access_scope"] is None:
                        legacy_execution = connection.execute(
                            """SELECT 1 FROM capability_snapshots WHERE account_id = ?
                               UNION ALL SELECT 1 FROM route_decisions WHERE selected_account_id = ?
                               UNION ALL SELECT 1 FROM submission_outbox WHERE account_id = ?
                               UNION ALL SELECT 1 FROM external_tasks WHERE account_id = ?
                               UNION ALL SELECT 1 FROM credit_ledger WHERE account_id = ?
                               LIMIT 1""",
                            (account_id,) * 5,
                        ).fetchone()
                        if legacy_execution is not None:
                            raise StoreConflictError(
                                "provider account requires reviewed legacy execution adoption"
                            )
                        connection.execute(
                            """INSERT INTO provider_account_entitlements(
                                   account_id, access_scope, granted_at
                               ) VALUES (?, 'private', ?)""",
                            (account_id, now),
                        )
                else:
                    connection.execute(
                        """INSERT INTO provider_accounts(
                               account_id, provider_id, display_name, credential_kind,
                               credential_locator, api_base, enabled, created_at, updated_at,
                               credential_state
                           ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 'pending')""",
                        (
                            account_id,
                            provider_id,
                            display_name,
                            credential_ref.kind.value,
                            credential_ref.locator,
                            api_base,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_owners(
                               account_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (account_id, owner_user_id, now),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_entitlements(
                               account_id, access_scope, granted_at
                           ) VALUES (?, 'private', ?)""",
                        (account_id, now),
                    )
                    connection.execute(
                        """UPDATE provider_accounts
                              SET credential_state = 'configured', enabled = ?, updated_at = ?
                            WHERE account_id = ?""",
                        (int(enabled), now, account_id),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_accessible_provider_account(owner_user_id, account_id)

    def reserve_owned_provider_account_credential(
        self,
        owner_user_id: str,
        account_id: str,
        provider_id: str,
        display_name: str,
        credential_ref: SecretReference,
        *,
        api_base: str | None = None,
    ) -> dict[str, Any]:
        """Reserve an exact-owner disabled account before secret-file I/O.

        A failed vault write leaves a harmless, retryable ``pending`` row.  The
        immutable owner sidecar prevents another actor from racing for the same
        vault namespace, while credential resolution and execution remain
        unavailable until :meth:`finalize_owned_provider_account_credential`.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if owner_user_id.startswith("castle-system:"):
            raise ValueError("external provider accounts require a user owner")
        account_id = _stable_id(account_id, "account_id")
        provider_id = _stable_id(provider_id, "provider_id")
        if provider_id == "local_comfy":
            raise ValueError("local_comfy must use the system local account contract")
        display_name = _external_id(display_name, "display_name")
        credential_ref = _credential_reference(credential_ref)
        locator_parts = Path(credential_ref.locator).parts
        if (
            credential_ref.kind.value != "file"
            or len(locator_parts) < 4
            or locator_parts[-1] != "api-key.secret"
            or locator_parts[-2] != account_id
            or locator_parts[-3] != provider_id
        ):
            raise SecretFieldError(
                "reserved credential locator must match its provider account vault slot"
            )
        api_base = self._external_api_base(api_base)
        now = self._clock()
        try:
            with self._transaction(provider_account_insert=True) as connection:
                existing = connection.execute(
                    """SELECT a.provider_id, a.display_name, a.credential_kind,
                              a.credential_locator, a.api_base, a.enabled,
                              a.credential_state, o.owner_user_id, e.access_scope
                         FROM provider_accounts AS a
                         LEFT JOIN provider_account_owners AS o USING(account_id)
                         LEFT JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ?""",
                    (account_id,),
                ).fetchone()
                if existing is not None:
                    exact_pending = (
                        existing["owner_user_id"] == owner_user_id
                        and existing["access_scope"] == "private"
                        and existing["provider_id"] == provider_id
                        and existing["display_name"] == display_name
                        and existing["credential_kind"] == credential_ref.kind.value
                        and existing["credential_locator"] == credential_ref.locator
                        and existing["api_base"] == api_base
                        and existing["enabled"] == 0
                        and existing["credential_state"] == "pending"
                    )
                    if not exact_pending:
                        raise StoreConflictError("provider account is unavailable")
                else:
                    connection.execute(
                        """INSERT INTO provider_accounts(
                               account_id, provider_id, display_name, credential_kind,
                               credential_locator, api_base, enabled, created_at,
                               updated_at, credential_state
                           ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 'pending')""",
                        (
                            account_id,
                            provider_id,
                            display_name,
                            credential_ref.kind.value,
                            credential_ref.locator,
                            api_base,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_owners(
                               account_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (account_id, owner_user_id, now),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_entitlements(
                               account_id, access_scope, granted_at
                           ) VALUES (?, 'private', ?)""",
                        (account_id, now),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_accessible_provider_account(owner_user_id, account_id)

    def finalize_owned_provider_account_credential(
        self,
        owner_user_id: str,
        account_id: str,
        credential_ref: SecretReference,
    ) -> dict[str, Any]:
        """Mark one exact pending reservation configured after vault success."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        credential_ref = _credential_reference(credential_ref)
        try:
            with self._transaction(credential_finalize=True) as connection:
                cursor = connection.execute(
                    """UPDATE provider_accounts SET credential_state = 'configured',
                              updated_at = ?
                         WHERE account_id = ? AND credential_state = 'pending'
                           AND credential_kind = ? AND credential_locator = ?
                           AND enabled = 0
                           AND EXISTS (
                               SELECT 1 FROM provider_account_owners AS owner
                               JOIN provider_account_entitlements AS entitlement
                                 USING(account_id)
                               WHERE owner.account_id = provider_accounts.account_id
                                 AND owner.owner_user_id = ?
                                 AND entitlement.access_scope = 'private'
                           )""",
                    (
                        self._clock(),
                        account_id,
                        credential_ref.kind.value,
                        credential_ref.locator,
                        owner_user_id,
                    ),
                )
                if cursor.rowcount != 1:
                    current = connection.execute(
                        """SELECT a.credential_state, a.credential_kind,
                                  a.credential_locator
                             FROM provider_accounts AS a
                             JOIN provider_account_owners AS owner USING(account_id)
                             JOIN provider_account_entitlements AS entitlement
                               USING(account_id)
                            WHERE a.account_id = ? AND owner.owner_user_id = ?
                              AND entitlement.access_scope = 'private'""",
                        (account_id, owner_user_id),
                    ).fetchone()
                    if current is None:
                        raise KeyError(account_id)
                    if not (
                        current["credential_state"] == "configured"
                        and current["credential_kind"] == credential_ref.kind.value
                        and current["credential_locator"] == credential_ref.locator
                    ):
                        raise StoreConflictError("provider credential reservation mismatch")
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_accessible_provider_account(owner_user_id, account_id)

    def register_system_local_provider_account(
        self,
        account_id: str,
        display_name: str,
        *,
        api_base: str = "http://127.0.0.1:8188",
        enabled: bool = False,
    ) -> dict[str, Any]:
        """Create the one credentialless loopback runtime shared explicitly.

        The account is owned by a fixed non-user principal.  Its
        ``authenticated_users`` entitlement means any caller of an actor-aware
        store API has already been authenticated by Castle; it is not public or
        anonymous access.
        """

        account_id = _stable_id(account_id, "account_id")
        display_name = _external_id(display_name, "display_name")
        api_base = self._local_api_base(api_base)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        now = self._clock()
        try:
            with self._transaction(provider_account_insert=True) as connection:
                existing = connection.execute(
                    """SELECT a.provider_id, a.credential_kind,
                              o.owner_user_id, e.access_scope
                         FROM provider_accounts AS a
                         LEFT JOIN provider_account_owners AS o USING(account_id)
                         LEFT JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ?""",
                    (account_id,),
                ).fetchone()
                if existing is not None:
                    if existing["owner_user_id"] is None:
                        # One narrow deployment bootstrap is reviewable without
                        # guessing: the historical, exact local-main loopback
                        # namespace, with matching trusted config and no graph.
                        # No other unowned account is adopted here.
                        if (
                            account_id != "local-main"
                            or existing["provider_id"] != "local_comfy"
                            or existing["credential_kind"] != "none"
                        ):
                            raise StoreConflictError(
                                "unowned provider account requires reviewed legacy adoption"
                            )
                        current_base = connection.execute(
                            "SELECT api_base FROM provider_accounts WHERE account_id = ?",
                            (account_id,),
                        ).fetchone()["api_base"]
                        graph = connection.execute(
                            """SELECT 1 FROM capability_snapshots WHERE account_id = ?
                               UNION ALL SELECT 1 FROM provider_character_links WHERE account_id = ?
                               UNION ALL SELECT 1 FROM remote_asset_projections WHERE account_id = ?
                               UNION ALL SELECT 1 FROM route_decisions WHERE selected_account_id = ?
                               UNION ALL SELECT 1 FROM submission_outbox WHERE account_id = ?
                               UNION ALL SELECT 1 FROM external_tasks WHERE account_id = ?
                               UNION ALL SELECT 1 FROM credit_ledger WHERE account_id = ?
                               LIMIT 1""",
                            (account_id,) * 7,
                        ).fetchone()
                        if current_base != api_base or graph is not None:
                            raise StoreConflictError(
                                "local-main requires reviewed legacy adoption"
                            )
                        connection.execute(
                            """INSERT INTO provider_account_owners(
                                   account_id, owner_user_id, bound_at
                               ) VALUES (?, ?, ?)""",
                            (account_id, SYSTEM_LOCAL_OWNER_ID, now),
                        )
                        connection.execute(
                            """INSERT INTO provider_account_entitlements(
                                   account_id, access_scope, granted_at
                               ) VALUES (?, 'authenticated_users', ?)""",
                            (account_id, now),
                        )
                        connection.execute(
                            """UPDATE provider_accounts SET display_name = ?, enabled = ?,
                                   updated_at = ? WHERE account_id = ?""",
                            (display_name, int(enabled), now, account_id),
                        )
                        existing = None
                    if existing is None:
                        pass
                    elif existing["owner_user_id"] != SYSTEM_LOCAL_OWNER_ID:
                        raise StoreConflictError("provider account is unavailable")
                    elif existing["provider_id"] != "local_comfy":
                        raise StoreConflictError("an account cannot change providers")
                    elif existing["credential_kind"] != "none":
                        raise StoreConflictError("system local account cannot have a credential")
                    elif existing["access_scope"] not in {None, "authenticated_users"}:
                        raise StoreConflictError("provider account entitlement is incompatible")
                    elif existing is not None:
                        connection.execute(
                        """UPDATE provider_accounts SET display_name = ?, api_base = ?,
                               enabled = ?, updated_at = ? WHERE account_id = ?""",
                        (display_name, api_base, int(enabled), now, account_id),
                    )
                    if existing is not None and existing["access_scope"] is None:
                        legacy_execution = connection.execute(
                            """SELECT 1 FROM capability_snapshots WHERE account_id = ?
                               UNION ALL SELECT 1 FROM route_decisions WHERE selected_account_id = ?
                               UNION ALL SELECT 1 FROM submission_outbox WHERE account_id = ?
                               UNION ALL SELECT 1 FROM external_tasks WHERE account_id = ?
                               UNION ALL SELECT 1 FROM credit_ledger WHERE account_id = ?
                               LIMIT 1""",
                            (account_id,) * 5,
                        ).fetchone()
                        if legacy_execution is not None:
                            raise StoreConflictError(
                                "local account requires reviewed legacy execution adoption"
                            )
                        connection.execute(
                            """INSERT INTO provider_account_entitlements(
                                   account_id, access_scope, granted_at
                               ) VALUES (?, 'authenticated_users', ?)""",
                            (account_id, now),
                        )
                else:
                    connection.execute(
                        """INSERT INTO provider_accounts(
                               account_id, provider_id, display_name, credential_kind,
                               credential_locator, api_base, enabled, created_at, updated_at,
                               credential_state
                           ) VALUES (?, 'local_comfy', ?, 'none', NULL, ?, ?, ?, ?,
                                     'not_applicable')""",
                        (account_id, display_name, api_base, int(enabled), now, now),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_owners(
                               account_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (account_id, SYSTEM_LOCAL_OWNER_ID, now),
                    )
                    connection.execute(
                        """INSERT INTO provider_account_entitlements(
                               account_id, access_scope, granted_at
                           ) VALUES (?, 'authenticated_users', ?)""",
                        (account_id, now),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        # Use a normal authenticated actor only for the access projection.  The
        # stored account owner remains the fixed system principal.
        return self.get_accessible_provider_account(SYSTEM_LOCAL_OWNER_ID, account_id)

    def update_owned_provider_account(
        self,
        owner_user_id: str,
        account_id: str,
        *,
        display_name: str | object = _UNSET,
        api_base: str | None | object = _UNSET,
        enabled: bool | object = _UNSET,
    ) -> dict[str, Any]:
        """Update non-credential fields of one exact-owner external account."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        assignments: list[str] = []
        values: list[Any] = []
        if display_name is not _UNSET:
            assignments.append("display_name = ?")
            values.append(_external_id(str(display_name), "display_name"))
        if api_base is not _UNSET:
            assignments.append("api_base = ?")
            values.append(self._external_api_base(api_base))
        if enabled is not _UNSET:
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be boolean")
            assignments.append("enabled = ?")
            values.append(int(enabled))
        try:
            with self._transaction() as connection:
                account = connection.execute(
                    """SELECT a.provider_id
                         FROM provider_accounts AS a
                         JOIN provider_account_owners AS o USING(account_id)
                         JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ? AND o.owner_user_id = ?
                          AND e.access_scope = 'private'
                          AND a.provider_id != 'local_comfy'
                          AND a.credential_state = 'configured'""",
                    (account_id, owner_user_id),
                ).fetchone()
                if account is None:
                    raise KeyError(account_id)
                if assignments:
                    assignments.append("updated_at = ?")
                    values.append(self._clock())
                    values.append(account_id)
                    connection.execute(
                        "UPDATE provider_accounts SET "
                        + ", ".join(assignments)
                        + " WHERE account_id = ?",
                        values,
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_accessible_provider_account(owner_user_id, account_id)

    def set_owned_provider_account_enabled(
        self,
        owner_user_id: str,
        account_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        return self.update_owned_provider_account(
            owner_user_id, account_id, enabled=enabled
        )

    def get_accessible_provider_account(
        self,
        actor_user_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        actor_user_id = _stable_id(actor_user_id, "actor_user_id")
        account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT a.*, e.access_scope
                     FROM provider_accounts AS a
                     JOIN provider_account_owners AS o USING(account_id)
                     JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE a.account_id = ? AND (
                        (e.access_scope = 'private' AND o.owner_user_id = ?
                         AND a.provider_id != 'local_comfy')
                        OR
                        (e.access_scope = 'authenticated_users'
                         AND o.owner_user_id = ?
                         AND a.provider_id = 'local_comfy')
                    )""",
                (account_id, actor_user_id, SYSTEM_LOCAL_OWNER_ID),
            ).fetchone()
        if row is None:
            raise KeyError(account_id)
        return self._public_accessible_account(row)

    def list_accessible_provider_accounts(
        self,
        actor_user_id: str,
        *,
        provider_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        actor_user_id = _stable_id(actor_user_id, "actor_user_id")
        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        if not isinstance(include_disabled, bool):
            raise ValueError("include_disabled must be boolean")
        clauses = [
            "((e.access_scope = 'private' AND o.owner_user_id = ? "
            "AND a.provider_id != 'local_comfy') OR "
            "(e.access_scope = 'authenticated_users' AND o.owner_user_id = ? "
            "AND a.provider_id = 'local_comfy'))"
        ]
        values: list[Any] = [actor_user_id, SYSTEM_LOCAL_OWNER_ID]
        if provider_id is not None:
            clauses.append("a.provider_id = ?")
            values.append(provider_id)
        if not include_disabled:
            clauses.append("a.enabled = 1")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT a.*, e.access_scope
                     FROM provider_accounts AS a
                     JOIN provider_account_owners AS o USING(account_id)
                     JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE """
                + " AND ".join(clauses)
                + " ORDER BY a.provider_id, a.account_id",
                values,
            ).fetchall()
        return [self._public_accessible_account(row) for row in rows]

    def list_owned_manual_import_accounts(
        self,
        owner_user_id: str,
        *,
        provider_id: str = "clipmode",
    ) -> list[dict[str, Any]]:
        """List exact-owner, non-executable metadata import namespaces.

        A manual ClipMode export namespace is useful to its owner even though
        it deliberately has no execution entitlement or credential.  Keep it
        out of the executable-account query while still making it visible in
        that actor's provider inventory.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        provider_id = _stable_id(provider_id, "provider_id")
        if provider_id != "clipmode":
            raise ValueError("manual import inventory is supported only for clipmode")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT a.*, 'manual_import' AS access_scope
                     FROM provider_accounts AS a
                     JOIN provider_account_owners AS o USING(account_id)
                     LEFT JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE o.owner_user_id = ?
                      AND a.provider_id = 'clipmode'
                      AND a.credential_kind = 'none'
                      AND a.credential_state = 'not_applicable'
                      AND a.enabled = 0
                      AND e.account_id IS NULL
                    ORDER BY a.account_id""",
                (owner_user_id,),
            ).fetchall()
        return [self._public_accessible_account(row) for row in rows]

    def plan_owned_provider_import(
        self,
        owner_user_id: str,
        account_id: str,
        bundle: "ClipModeImportBundle",
        request: Mapping[str, Any],
        decision_plan: "OwnerImportDecisionPlan",
        *,
        profile_renditions: Sequence["OwnerProfileRendition"] = (),
        asset_previews: Sequence["OwnerAssetPreviewRendition"] = (),
    ) -> "StoreOwnerImportPlan":
        """Revalidate one owner import against one read-only store snapshot.

        The companion planner receives this store-owned connection and never
        opens the database path itself.  Requiring the original request as
        well as the bundle and pure decision prevents a caller from presenting
        a hand-built plan whose private graph fields were not recomputed.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if owner_user_id.startswith("castle-system:"):
            raise ValueError("provider imports require a user owner")
        account_id = _stable_id(account_id, "account_id")
        if account_id == "clipmode-import":
            raise ValueError(
                "the legacy clipmode-import namespace cannot receive imports"
            )
        from .owner_import_store import plan_owned_provider_import_in_connection

        with self._read_connection() as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            try:
                return plan_owned_provider_import_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    account_id=account_id,
                    bundle=bundle,
                    request=request,
                    decision_plan=decision_plan,
                    profile_renditions=profile_renditions,
                    asset_previews=asset_previews,
                )
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")

    def confirm_owned_provider_import(
        self,
        owner_user_id: str,
        account_id: str,
        receipt_id: str,
        bundle: "ClipModeImportBundle",
        request: Mapping[str, Any],
        decision_plan: "OwnerImportDecisionPlan",
        *,
        expected_plan_sha256: str,
        expected_graph_sha256: str,
        expected_stable_graph_sha256: str,
        expected_snapshot_sha256: str,
        materialized_import: "MaterializedImport | None" = None,
        profile_renditions: Sequence["OwnerProfileRendition"] = (),
        asset_previews: Sequence["OwnerAssetPreviewRendition"] = (),
    ) -> dict[str, Any]:
        """Atomically re-plan, publish, and receipt one metadata-only import.

        Media objects must already have completed their separate durable
        publication protocol before this transaction. Profile pointers are
        accepted only with exact rendered evidence and matching durable
        materialization supplied by the owner-import coordinator.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if owner_user_id.startswith("castle-system:"):
            raise ValueError("provider imports require a user owner")
        account_id = _stable_id(account_id, "account_id")
        if account_id == "clipmode-import":
            raise ValueError(
                "the legacy clipmode-import namespace cannot receive imports"
            )
        receipt_id = _stable_id(receipt_id, "receipt_id")
        from .owner_import_confirm import confirm_owned_provider_import_in_connection

        try:
            with self._transaction(import_insert=True) as connection:
                result = confirm_owned_provider_import_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    account_id=account_id,
                    receipt_id=receipt_id,
                    bundle=bundle,
                    request=request,
                    decision_plan=decision_plan,
                    expected_plan_sha256=expected_plan_sha256,
                    expected_graph_sha256=expected_graph_sha256,
                    expected_stable_graph_sha256=expected_stable_graph_sha256,
                    expected_snapshot_sha256=expected_snapshot_sha256,
                    now=self._clock(),
                    materialized_import=materialized_import,
                    profile_renditions=profile_renditions,
                    asset_previews=asset_previews,
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        receipt = self.get_owned_provider_import_receipt(
            owner_user_id, str(result["receipt_id"])
        )
        receipt["replayed"] = bool(result["replayed"])
        return receipt

    @staticmethod
    def compute_import_account_binding_sha256(
        provider_id: str,
        account_id: str,
        provider_scope: str,
    ) -> str:
        """Bind reviewed import evidence to one exact provider namespace."""

        provider_id = _stable_id(provider_id, "provider_id")
        account_id = _stable_id(account_id, "account_id")
        provider_scope = _stable_id(provider_scope, "provider_scope")
        return hashlib.sha256(
            _json([provider_id, account_id, provider_scope]).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def compute_import_decision_sha256(
        owner_user_id: str,
        account_id: str,
        provider_id: str,
        provider_scope: str,
        source_schema: str,
        source_schema_version: int,
        raw_source_sha256: str,
        semantic_sha256: str,
        mapping_sha256: str,
        profile_selection_sha256: str,
    ) -> str:
        """Compute the schema-8 immutable import-decision identity.

        Plan evidence is deliberately absent: confirming the same reviewed
        source/mapping/profile decision after a harmless re-plan must converge
        on the first receipt instead of creating a second active fact.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        provider_id = _stable_id(provider_id, "provider_id")
        provider_scope = _stable_id(provider_scope, "provider_scope")
        source_schema = _external_id(source_schema, "source_schema")
        if (
            isinstance(source_schema_version, bool)
            or not isinstance(source_schema_version, int)
            or source_schema_version < 1
        ):
            raise ValueError("source_schema_version must be a positive integer")
        payload = {
            "accountId": account_id,
            "mappingSha256": _sha256(mapping_sha256, "mapping_sha256"),
            "ownerUserId": owner_user_id,
            "profileSelectionSha256": _sha256(
                profile_selection_sha256, "profile_selection_sha256"
            ),
            "providerId": provider_id,
            "providerScope": provider_scope,
            "rawSourceSha256": _sha256(raw_source_sha256, "raw_source_sha256"),
            "semanticSha256": _sha256(semantic_sha256, "semantic_sha256"),
            "sourceSchema": source_schema,
            "sourceSchemaVersion": source_schema_version,
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _import_memberships(
        records: Sequence[Mapping[str, Any]],
        identifier_key: str,
    ) -> list[tuple[str, str]]:
        if isinstance(records, (str, bytes, bytearray)) or not isinstance(
            records, Sequence
        ):
            raise ValueError("import memberships must be a sequence")
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                identifier_key,
                "disposition",
            }:
                raise ValueError("import membership does not match the exact schema")
            identifier = _stable_id(record[identifier_key], identifier_key)
            disposition = str(record["disposition"] or "").strip().lower()
            if disposition not in _IMPORT_MEMBERSHIP_DISPOSITIONS:
                raise ValueError("import membership disposition is invalid")
            if identifier in seen:
                raise ValueError("import membership identifiers must be unique")
            seen.add(identifier)
            normalized.append((identifier, disposition))
        return normalized

    @staticmethod
    def _exact_import_records(
        records: Sequence[Mapping[str, Any]],
        keys: frozenset[str],
        label: str,
    ) -> list[Mapping[str, Any]]:
        if isinstance(records, (str, bytes, bytearray)) or not isinstance(
            records, Sequence
        ):
            raise ValueError(f"{label} must be a sequence")
        result: list[Mapping[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping) or set(record) != keys:
                raise ValueError(f"{label} does not match the exact schema")
            result.append(record)
        return result

    def record_owned_provider_import(
        self,
        owner_user_id: str,
        receipt_id: str,
        account_id: str,
        *,
        provider_scope: str,
        export_id: str,
        exported_at: str,
        raw_source_sha256: str,
        manifest_sha256: str,
        semantic_sha256: str,
        mapping_sha256: str,
        profile_selection_sha256: str,
        account_binding_sha256: str,
        plan_version: str,
        plan_sha256: str,
        decision_plan_sha256: str,
        stable_graph_sha256: str,
        import_decision_sha256: str | None = None,
        provider_id: str = "clipmode",
        source_schema: str = "castle.clipmode-export.v1",
        source_schema_version: int = 1,
        character_memberships: Sequence[Mapping[str, Any]] = (),
        character_link_memberships: Sequence[Mapping[str, Any]] = (),
        projection_memberships: Sequence[Mapping[str, Any]] = (),
        version_memberships: Sequence[Mapping[str, Any]] = (),
        lineage_memberships: Sequence[Mapping[str, Any]] = (),
        blobs: Sequence[Mapping[str, Any]] = (),
        version_blob_bindings: Sequence[Mapping[str, Any]] = (),
        projection_previews: Sequence[Mapping[str, Any]] = (),
        character_profiles: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Record one already-published, fully validated offline import.

        This is the only controlled insertion lane for the schema-9 receipt
        graph.  It performs no filesystem or network I/O and creates no
        canonical provider objects; the future materializer/projector must
        publish and validate those first, then call this method from its one
        confirmation transaction.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if owner_user_id.startswith("castle-system:"):
            raise ValueError("provider imports require a user owner")
        receipt_id = _stable_id(receipt_id, "receipt_id")
        account_id = _stable_id(account_id, "account_id")
        if account_id == "clipmode-import":
            raise ValueError("the legacy clipmode-import namespace cannot receive imports")
        provider_id = _stable_id(provider_id, "provider_id")
        provider_scope = _stable_id(provider_scope, "provider_scope")
        source_schema = _external_id(source_schema, "source_schema")
        if provider_id != "clipmode":
            raise ValueError("offline import receipts support only clipmode")
        if source_schema != "castle.clipmode-export.v1" or source_schema_version != 1:
            raise ValueError("offline import source schema is unsupported")
        export_id = _external_id(export_id, "export_id")
        if len(export_id) > 200:
            raise ValueError("export_id is invalid")
        if "://" in export_id or export_id.startswith(("data:", "file:")):
            raise ValueError("export_id cannot be a URL or locator")
        exported_at = _utc_timestamp(exported_at, "exported_at")
        plan_version = _stable_id(plan_version, "plan_version")

        raw_source_sha256 = _sha256(raw_source_sha256, "raw_source_sha256")
        manifest_sha256 = _sha256(manifest_sha256, "manifest_sha256")
        semantic_sha256 = _sha256(semantic_sha256, "semantic_sha256")
        mapping_sha256 = _sha256(mapping_sha256, "mapping_sha256")
        profile_selection_sha256 = _sha256(
            profile_selection_sha256, "profile_selection_sha256"
        )
        account_binding_sha256 = _sha256(
            account_binding_sha256, "account_binding_sha256"
        )
        expected_account_binding = self.compute_import_account_binding_sha256(
            provider_id,
            account_id,
            provider_scope,
        )
        if account_binding_sha256 != expected_account_binding:
            raise StoreConflictError("import account binding digest mismatch")
        plan_sha256 = _sha256(plan_sha256, "plan_sha256")
        decision_plan_sha256 = _sha256(
            decision_plan_sha256, "decision_plan_sha256"
        )
        stable_graph_sha256 = _sha256(
            stable_graph_sha256, "stable_graph_sha256"
        )
        computed_decision = self.compute_import_decision_sha256(
            owner_user_id,
            account_id,
            provider_id,
            provider_scope,
            source_schema,
            source_schema_version,
            raw_source_sha256,
            semantic_sha256,
            mapping_sha256,
            profile_selection_sha256,
        )
        if import_decision_sha256 is not None and _sha256(
            import_decision_sha256, "import_decision_sha256"
        ) != computed_decision:
            raise StoreConflictError("import decision digest mismatch")

        characters = self._import_memberships(
            character_memberships, "character_id"
        )
        character_links = self._import_memberships(
            character_link_memberships, "link_id"
        )
        projections = self._import_memberships(
            projection_memberships, "projection_id"
        )
        versions = self._import_memberships(version_memberships, "version_id")
        normalized_lineage: list[tuple[str, str, str, str]] = []
        seen_lineage: set[tuple[str, str, str]] = set()
        for record in self._exact_import_records(
            lineage_memberships,
            frozenset(
                {
                    "parent_projection_id",
                    "child_projection_id",
                    "relationship",
                    "disposition",
                }
            ),
            "lineage membership",
        ):
            parent = _stable_id(
                record["parent_projection_id"], "parent_projection_id"
            )
            child = _stable_id(
                record["child_projection_id"], "child_projection_id"
            )
            relationship = _external_id(record["relationship"], "relationship")
            if len(relationship) > 200 or parent == child:
                raise ValueError("lineage membership is invalid")
            disposition = str(record["disposition"] or "").strip().lower()
            if disposition not in {"created", "reused"}:
                raise ValueError("lineage membership disposition is invalid")
            identity = (parent, child, relationship)
            if identity in seen_lineage:
                raise ValueError("lineage memberships must be unique")
            seen_lineage.add(identity)
            normalized_lineage.append((*identity, disposition))

        normalized_blobs: list[tuple[str, int, str]] = []
        seen_blobs: set[str] = set()
        for record in self._exact_import_records(
            blobs, frozenset({"sha256", "byte_size", "signature_family"}), "blob"
        ):
            digest = _sha256(record["sha256"], "blob sha256")
            byte_size = record["byte_size"]
            if (
                isinstance(byte_size, bool)
                or not isinstance(byte_size, int)
                or byte_size <= 0
            ):
                raise ValueError("blob byte_size must be a positive integer")
            signature_family = str(record["signature_family"] or "").strip().lower()
            if signature_family not in _IMPORT_SIGNATURE_FAMILIES:
                raise ValueError("blob signature_family is invalid")
            if digest in seen_blobs:
                raise ValueError("blob digests must be unique within an import")
            seen_blobs.add(digest)
            normalized_blobs.append((digest, byte_size, signature_family))

        normalized_bindings: list[tuple[str, str, str, str]] = []
        seen_bindings: set[tuple[str, str]] = set()
        for record in self._exact_import_records(
            version_blob_bindings,
            frozenset({"version_id", "role", "sha256", "declared_mime"}),
            "version blob binding",
        ):
            version_id = _stable_id(record["version_id"], "version_id")
            role = str(record["role"] or "").strip().lower()
            if role not in _IMPORT_BLOB_ROLES:
                raise ValueError("version blob role is invalid")
            digest = _sha256(record["sha256"], "binding sha256")
            declared_mime = str(record["declared_mime"] or "").strip().lower()
            if not _MIME_RE.fullmatch(declared_mime):
                raise ValueError("binding declared_mime is invalid")
            if role == "preview_rendition" and declared_mime not in _SAFE_PREVIEW_MIMES:
                raise ValueError("preview rendition MIME is not safe")
            key = (version_id, role)
            if key in seen_bindings:
                raise ValueError("version blob roles must be unique within an import")
            seen_bindings.add(key)
            normalized_bindings.append((version_id, role, digest, declared_mime))

        normalized_previews: list[tuple[str, str, str, str]] = []
        seen_previews: set[str] = set()
        for record in self._exact_import_records(
            projection_previews,
            frozenset(
                {"projection_id", "version_id", "rendition_sha256", "selection_source"}
            ),
            "projection preview",
        ):
            projection_id = _stable_id(record["projection_id"], "projection_id")
            version_id = _stable_id(record["version_id"], "version_id")
            digest = _sha256(record["rendition_sha256"], "rendition_sha256")
            selection_source = str(record["selection_source"] or "").strip().lower()
            if selection_source != "explicit":
                raise ValueError("projection preview selection must be explicit")
            if projection_id in seen_previews:
                raise ValueError("projection previews must be unique within an import")
            seen_previews.add(projection_id)
            normalized_previews.append(
                (projection_id, version_id, digest, selection_source)
            )

        normalized_profiles: list[tuple[str, str, str, str, str]] = []
        seen_profiles: set[str] = set()
        for record in self._exact_import_records(
            character_profiles,
            frozenset(
                {
                    "character_id",
                    "projection_id",
                    "version_id",
                    "rendition_sha256",
                    "selection_source",
                }
            ),
            "character profile",
        ):
            character_id = _stable_id(record["character_id"], "character_id")
            projection_id = _stable_id(record["projection_id"], "projection_id")
            version_id = _stable_id(record["version_id"], "version_id")
            digest = _sha256(record["rendition_sha256"], "rendition_sha256")
            selection_source = str(record["selection_source"] or "").strip().lower()
            if selection_source != "explicit":
                raise ValueError("character profile selection must be explicit")
            if character_id in seen_profiles:
                raise ValueError("character profiles must be unique within an import")
            seen_profiles.add(character_id)
            normalized_profiles.append(
                (character_id, projection_id, version_id, digest, selection_source)
            )

        now = self._clock()
        replayed = False
        stored_receipt_id = receipt_id
        try:
            with self._transaction(import_insert=True) as connection:
                existing_receipt = connection.execute(
                    """SELECT receipt.*, evidence.decision_plan_sha256,
                              evidence.stable_graph_sha256,
                              evidence.owner_user_id AS evidence_owner_user_id
                         FROM provider_import_receipts AS receipt
                         LEFT JOIN provider_import_plan_evidence AS evidence
                           ON evidence.receipt_id = receipt.receipt_id
                        WHERE receipt.import_decision_sha256 = ?
                          AND receipt.state = 'active'""",
                    (computed_decision,),
                ).fetchone()
                if existing_receipt is not None:
                    expected_receipt = {
                        "owner_user_id": owner_user_id,
                        "account_id": account_id,
                        "provider_id": provider_id,
                        "provider_scope": provider_scope,
                        "source_schema": source_schema,
                        "source_schema_version": source_schema_version,
                        "export_id": export_id,
                        "exported_at": exported_at,
                        "raw_source_sha256": raw_source_sha256,
                        "manifest_sha256": manifest_sha256,
                        "semantic_sha256": semantic_sha256,
                        "mapping_sha256": mapping_sha256,
                        "profile_selection_sha256": profile_selection_sha256,
                        "account_binding_sha256": account_binding_sha256,
                        "plan_version": plan_version,
                        "plan_sha256": plan_sha256,
                        "decision_plan_sha256": decision_plan_sha256,
                        "stable_graph_sha256": stable_graph_sha256,
                        "evidence_owner_user_id": owner_user_id,
                    }
                    if any(
                        existing_receipt[key] != value
                        for key, value in expected_receipt.items()
                    ):
                        raise StoreConflictError("provider import receipt is unavailable")
                    replay_memberships = (
                        (
                            "provider_import_character_memberships",
                            "character_id",
                            characters,
                        ),
                        (
                            "provider_import_character_link_memberships",
                            "link_id",
                            character_links,
                        ),
                        (
                            "provider_import_projection_memberships",
                            "projection_id",
                            projections,
                        ),
                        (
                            "provider_import_version_memberships",
                            "version_id",
                            versions,
                        ),
                    )
                    for table, identifier_column, supplied in replay_memberships:
                        stored = connection.execute(
                            f"""SELECT {identifier_column}, disposition
                                   FROM {table}
                                  WHERE receipt_id = ?
                                  ORDER BY {identifier_column}""",
                            (existing_receipt["receipt_id"],),
                        ).fetchall()
                        stored_by_id = {
                            str(row[identifier_column]): str(row["disposition"])
                            for row in stored
                        }
                        supplied_by_id = dict(supplied)
                        if set(stored_by_id) != set(supplied_by_id):
                            raise StoreConflictError(
                                "provider import receipt membership mismatch"
                            )
                        for identifier, supplied_disposition in supplied_by_id.items():
                            stored_disposition = stored_by_id[identifier]
                            if stored_disposition == "mapped":
                                compatible = supplied_disposition == "mapped"
                            else:
                                compatible = supplied_disposition in {"created", "reused"}
                            if not compatible:
                                raise StoreConflictError(
                                    "provider import receipt membership mismatch"
                                )
                    stored_lineage = connection.execute(
                        """SELECT parent_projection_id, child_projection_id,
                                  relation, disposition
                             FROM provider_import_lineage_memberships
                            WHERE receipt_id = ?
                            ORDER BY parent_projection_id,
                                     child_projection_id, relation""",
                        (existing_receipt["receipt_id"],),
                    ).fetchall()
                    stored_lineage_by_id = {
                        (
                            str(row["parent_projection_id"]),
                            str(row["child_projection_id"]),
                            str(row["relation"]),
                        ): str(row["disposition"])
                        for row in stored_lineage
                    }
                    supplied_lineage_by_id = {
                        (parent, child, relation): disposition
                        for parent, child, relation, disposition in normalized_lineage
                    }
                    if set(stored_lineage_by_id) != set(supplied_lineage_by_id):
                        raise StoreConflictError(
                            "provider import receipt lineage mismatch"
                        )
                    if any(
                        disposition not in {"created", "reused"}
                        for disposition in supplied_lineage_by_id.values()
                    ):
                        raise StoreConflictError(
                            "provider import receipt lineage mismatch"
                        )
                    stored_receipt_id = str(existing_receipt["receipt_id"])
                    replayed = True
                else:
                    connection.execute(
                        """INSERT INTO provider_import_receipts(
                               receipt_id, owner_user_id, account_id, provider_id,
                               provider_scope, source_schema, source_schema_version,
                               export_id, exported_at, raw_source_sha256,
                               manifest_sha256, semantic_sha256, mapping_sha256,
                               profile_selection_sha256, account_binding_sha256,
                               plan_version, plan_sha256, import_decision_sha256,
                               state, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                     ?, ?, ?, 'building', ?)""",
                        (
                            receipt_id,
                            owner_user_id,
                            account_id,
                            provider_id,
                            provider_scope,
                            source_schema,
                            source_schema_version,
                            export_id,
                            exported_at,
                            raw_source_sha256,
                            manifest_sha256,
                            semantic_sha256,
                            mapping_sha256,
                            profile_selection_sha256,
                            account_binding_sha256,
                            plan_version,
                            plan_sha256,
                            computed_decision,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO provider_import_plan_evidence(
                               receipt_id, owner_user_id,
                               decision_plan_sha256, stable_graph_sha256,
                               recorded_at
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            receipt_id,
                            owner_user_id,
                            decision_plan_sha256,
                            stable_graph_sha256,
                            now,
                        ),
                    )
                    for identifier, disposition in characters:
                        connection.execute(
                            """INSERT INTO provider_import_character_memberships(
                                   receipt_id, owner_user_id, character_id,
                                   disposition, recorded_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (receipt_id, owner_user_id, identifier, disposition, now),
                        )
                    for identifier, disposition in character_links:
                        connection.execute(
                            """INSERT INTO provider_import_character_link_memberships(
                                   receipt_id, owner_user_id, link_id,
                                   disposition, recorded_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (receipt_id, owner_user_id, identifier, disposition, now),
                        )
                    for identifier, disposition in projections:
                        connection.execute(
                            """INSERT INTO provider_import_projection_memberships(
                                   receipt_id, owner_user_id, projection_id,
                                   disposition, recorded_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (receipt_id, owner_user_id, identifier, disposition, now),
                        )
                    for identifier, disposition in versions:
                        connection.execute(
                            """INSERT INTO provider_import_version_memberships(
                                   receipt_id, owner_user_id, version_id,
                                   disposition, recorded_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (receipt_id, owner_user_id, identifier, disposition, now),
                        )
                    for parent, child, relationship, disposition in normalized_lineage:
                        connection.execute(
                            """INSERT INTO provider_import_lineage_memberships(
                                   receipt_id, owner_user_id,
                                   parent_projection_id, child_projection_id,
                                   relation, disposition, recorded_at
                               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (
                                receipt_id,
                                owner_user_id,
                                parent,
                                child,
                                relationship,
                                disposition,
                                now,
                            ),
                        )
                    for digest, byte_size, signature_family in normalized_blobs:
                        existing_blob = connection.execute(
                            """SELECT byte_size, signature_family
                                 FROM provider_media_blobs
                                WHERE owner_user_id = ? AND sha256 = ?""",
                            (owner_user_id, digest),
                        ).fetchone()
                        if existing_blob is None:
                            connection.execute(
                                """INSERT INTO provider_media_blobs(
                                       owner_user_id, sha256, byte_size,
                                       signature_family, created_at
                                   ) VALUES (?, ?, ?, ?, ?)""",
                                (
                                    owner_user_id,
                                    digest,
                                    byte_size,
                                    signature_family,
                                    now,
                                ),
                            )
                        elif (
                            int(existing_blob["byte_size"]) != byte_size
                            or existing_blob["signature_family"] != signature_family
                        ):
                            raise StoreConflictError("provider media blob evidence mismatch")
                    for version_id, role, digest, declared_mime in normalized_bindings:
                        existing_binding = connection.execute(
                            """SELECT sha256, declared_mime
                                 FROM provider_asset_version_blobs
                                WHERE owner_user_id = ? AND version_id = ? AND role = ?""",
                            (owner_user_id, version_id, role),
                        ).fetchone()
                        if existing_binding is None:
                            connection.execute(
                                """INSERT INTO provider_asset_version_blobs(
                                       owner_user_id, version_id, role, receipt_id,
                                       sha256, declared_mime, bound_at
                                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    owner_user_id,
                                    version_id,
                                    role,
                                    receipt_id,
                                    digest,
                                    declared_mime,
                                    now,
                                ),
                            )
                        elif (
                            existing_binding["sha256"] != digest
                            or existing_binding["declared_mime"] != declared_mime
                        ):
                            raise StoreConflictError(
                                "asset-version blob binding is unavailable"
                            )
                    for projection_id, version_id, digest, selection_source in normalized_previews:
                        existing_preview = connection.execute(
                            """SELECT version_id, rendition_sha256, selection_source
                                 FROM projection_preview_versions
                                WHERE owner_user_id = ? AND projection_id = ?""",
                            (owner_user_id, projection_id),
                        ).fetchone()
                        if existing_preview is None:
                            connection.execute(
                                """INSERT INTO projection_preview_versions(
                                       owner_user_id, projection_id, version_id,
                                       rendition_sha256, receipt_id,
                                       selection_source, selected_at
                                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    owner_user_id,
                                    projection_id,
                                    version_id,
                                    digest,
                                    receipt_id,
                                    selection_source,
                                    now,
                                ),
                            )
                        elif (
                            existing_preview["version_id"] != version_id
                            or existing_preview["rendition_sha256"] != digest
                            or existing_preview["selection_source"] != selection_source
                        ):
                            raise StoreConflictError("projection preview is unavailable")
                    for (
                        character_id,
                        projection_id,
                        version_id,
                        digest,
                        selection_source,
                    ) in normalized_profiles:
                        existing_exact = connection.execute(
                            """SELECT projection_id, version_id,
                                      rendition_sha256, selection_source
                                 FROM canonical_character_profile_versions
                                WHERE character_id = ?""",
                            (character_id,),
                        ).fetchone()
                        if existing_exact is not None:
                            if (
                                existing_exact["projection_id"] != projection_id
                                or existing_exact["version_id"] != version_id
                                or existing_exact["rendition_sha256"] != digest
                                or existing_exact["selection_source"] != selection_source
                            ):
                                raise StoreConflictError(
                                    "exact character profile is unavailable"
                                )
                            continue
                        connection.execute(
                            """INSERT INTO canonical_character_profiles(
                                   character_id, projection_id,
                                   selection_source, selected_at
                               ) VALUES (?, ?, 'explicit', ?)
                               ON CONFLICT(character_id) DO UPDATE SET
                                   projection_id = excluded.projection_id,
                                   selection_source = excluded.selection_source,
                                   selected_at = excluded.selected_at""",
                            (character_id, projection_id, now),
                        )
                        connection.execute(
                            """INSERT INTO canonical_character_profile_versions(
                                   character_id, owner_user_id, projection_id,
                                   version_id, rendition_sha256, receipt_id,
                                   selection_source, selected_at
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                character_id,
                                owner_user_id,
                                projection_id,
                                version_id,
                                digest,
                                receipt_id,
                                selection_source,
                                now,
                            ),
                        )
                    connection.execute(
                        """UPDATE provider_import_receipts
                              SET state = 'active'
                            WHERE receipt_id = ? AND state = 'building'""",
                        (receipt_id,),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        result = self.get_owned_provider_import_receipt(
            owner_user_id, stored_receipt_id
        )
        result["replayed"] = replayed
        return result

    def get_owned_provider_import_receipt(
        self,
        owner_user_id: str,
        receipt_id: str,
    ) -> dict[str, Any]:
        """Return one immutable receipt only for its exact owner."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        receipt_id = _stable_id(receipt_id, "receipt_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT * FROM provider_import_receipts
                    WHERE receipt_id = ? AND owner_user_id = ? AND state = 'active'""",
                (receipt_id, owner_user_id),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            counts = {
                "characters": connection.execute(
                    "SELECT COUNT(*) FROM provider_import_character_memberships WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "character_links": connection.execute(
                    "SELECT COUNT(*) FROM provider_import_character_link_memberships WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "projections": connection.execute(
                    "SELECT COUNT(*) FROM provider_import_projection_memberships WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "versions": connection.execute(
                    "SELECT COUNT(*) FROM provider_import_version_memberships WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "lineage": connection.execute(
                    "SELECT COUNT(*) FROM provider_import_lineage_memberships WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "blob_bindings": connection.execute(
                    "SELECT COUNT(*) FROM provider_asset_version_blobs WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "projection_previews": connection.execute(
                    "SELECT COUNT(*) FROM projection_preview_versions WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
                "character_profiles": connection.execute(
                    "SELECT COUNT(*) FROM canonical_character_profile_versions WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()[0],
            }
        return {
            "receipt_id": row["receipt_id"],
            "owner_user_id": row["owner_user_id"],
            "account_id": row["account_id"],
            "provider_id": row["provider_id"],
            "provider_scope": row["provider_scope"],
            "source_schema": row["source_schema"],
            "source_schema_version": row["source_schema_version"],
            "export_id": row["export_id"],
            "exported_at": row["exported_at"],
            "raw_source_sha256": row["raw_source_sha256"],
            "manifest_sha256": row["manifest_sha256"],
            "semantic_sha256": row["semantic_sha256"],
            "mapping_sha256": row["mapping_sha256"],
            "profile_selection_sha256": row["profile_selection_sha256"],
            "account_binding_sha256": row["account_binding_sha256"],
            "plan_version": row["plan_version"],
            "plan_sha256": row["plan_sha256"],
            "import_decision_sha256": row["import_decision_sha256"],
            "state": row["state"],
            "created_at": row["created_at"],
            "membership_counts": {key: int(value) for key, value in counts.items()},
        }

    def resolve_owned_projection_preview(
        self,
        owner_user_id: str,
        projection_id: str,
    ) -> dict[str, Any]:
        """Resolve exact safe-rendition evidence without deriving a file path."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT preview.projection_id, preview.version_id,
                          preview.rendition_sha256, preview.selection_source,
                          preview.selected_at, binding.declared_mime,
                          blob.byte_size, blob.signature_family
                     FROM projection_preview_versions AS preview
                     JOIN provider_import_receipts AS preview_receipt
                       ON preview_receipt.receipt_id = preview.receipt_id
                      AND preview_receipt.owner_user_id = preview.owner_user_id
                      AND preview_receipt.state = 'active'
                     JOIN provider_asset_version_blobs AS binding
                       ON binding.owner_user_id = preview.owner_user_id
                      AND binding.version_id = preview.version_id
                      AND binding.role = 'preview_rendition'
                      AND binding.sha256 = preview.rendition_sha256
                     JOIN provider_import_receipts AS binding_receipt
                       ON binding_receipt.receipt_id = binding.receipt_id
                      AND binding_receipt.owner_user_id = binding.owner_user_id
                      AND binding_receipt.state = 'active'
                     JOIN provider_media_blobs AS blob
                       ON blob.owner_user_id = binding.owner_user_id
                      AND blob.sha256 = binding.sha256
                     JOIN remote_asset_owners AS owner
                       ON owner.projection_id = preview.projection_id
                      AND owner.owner_user_id = preview.owner_user_id
                    WHERE preview.owner_user_id = ?
                      AND preview.projection_id = ?""",
                (owner_user_id, projection_id),
            ).fetchone()
        if row is None:
            raise KeyError(projection_id)
        return dict(row)

    def resolve_owned_character_profile_version(
        self,
        owner_user_id: str,
        character_id: str,
    ) -> dict[str, Any]:
        """Resolve an explicitly selected exact-version character profile."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        character_id = _stable_id(character_id, "character_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT profile.character_id, profile.projection_id,
                          profile.version_id, profile.rendition_sha256,
                          profile.selection_source, profile.selected_at,
                          binding.declared_mime, blob.byte_size,
                          blob.signature_family
                     FROM canonical_character_profile_versions AS profile
                     JOIN provider_import_receipts AS profile_receipt
                       ON profile_receipt.receipt_id = profile.receipt_id
                      AND profile_receipt.owner_user_id = profile.owner_user_id
                      AND profile_receipt.state = 'active'
                     JOIN canonical_character_owners AS owner
                       ON owner.character_id = profile.character_id
                      AND owner.owner_user_id = profile.owner_user_id
                     JOIN provider_asset_version_blobs AS binding
                       ON binding.owner_user_id = profile.owner_user_id
                      AND binding.version_id = profile.version_id
                      AND binding.role = 'preview_rendition'
                      AND binding.sha256 = profile.rendition_sha256
                     JOIN provider_import_receipts AS binding_receipt
                       ON binding_receipt.receipt_id = binding.receipt_id
                      AND binding_receipt.owner_user_id = binding.owner_user_id
                      AND binding_receipt.state = 'active'
                     JOIN provider_media_blobs AS blob
                       ON blob.owner_user_id = binding.owner_user_id
                      AND blob.sha256 = binding.sha256
                    WHERE profile.owner_user_id = ?
                      AND profile.character_id = ?""",
                (owner_user_id, character_id),
            ).fetchone()
        if row is None:
            raise KeyError(character_id)
        return dict(row)

    def credential_reference(self, account_id: str) -> SecretReference:
        """Load a legacy-unowned opaque reference; never a resolved value."""

        account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT a.credential_kind, a.credential_locator,
                          a.credential_state,
                          o.owner_user_id, e.access_scope
                     FROM provider_accounts AS a
                     LEFT JOIN provider_account_owners AS o USING(account_id)
                     LEFT JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
        if row is None:
            raise KeyError(account_id)
        if row["owner_user_id"] is not None or row["access_scope"] is not None:
            raise StoreConflictError("owned provider account requires actor-scoped access")
        if row["credential_kind"] == "none" or row["credential_state"] != "configured":
            raise KeyError("provider account has no credential")
        return SecretReference(kind=row["credential_kind"], locator=row["credential_locator"])

    def credential_reference_for_actor(
        self,
        actor_user_id: str,
        account_id: str,
    ) -> SecretReference:
        """Load an opaque credential reference only through account ACLs."""

        actor_user_id = _stable_id(actor_user_id, "actor_user_id")
        account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT a.credential_kind, a.credential_locator,
                          a.credential_state
                     FROM provider_accounts AS a
                     JOIN provider_account_owners AS o USING(account_id)
                     JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE a.account_id = ? AND (
                        (e.access_scope = 'private' AND o.owner_user_id = ?
                         AND a.provider_id != 'local_comfy')
                        OR
                        (e.access_scope = 'authenticated_users'
                         AND o.owner_user_id = ?
                         AND a.provider_id = 'local_comfy')
                    )""",
                (account_id, actor_user_id, SYSTEM_LOCAL_OWNER_ID),
            ).fetchone()
        if row is None:
            raise KeyError(account_id)
        if row["credential_kind"] == "none" or row["credential_state"] != "configured":
            raise KeyError("provider account has no credential")
        return SecretReference(kind=row["credential_kind"], locator=row["credential_locator"])

    def record_capability_snapshot(
        self,
        account_id: str,
        offerings: Sequence[CapabilityOffering],
        *,
        source_revision: str,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        return self._record_capability_snapshot(
            None,
            account_id,
            offerings,
            source_revision=source_revision,
            snapshot_id=snapshot_id,
        )

    def record_owned_capability_snapshot(
        self,
        owner_user_id: str,
        account_id: str,
        offerings: Sequence[CapabilityOffering],
        *,
        source_revision: str,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Append capability evidence as the exact immutable account owner."""

        return self._record_capability_snapshot(
            _stable_id(owner_user_id, "owner_user_id"),
            account_id,
            offerings,
            source_revision=source_revision,
            snapshot_id=snapshot_id,
        )

    def _record_capability_snapshot(
        self,
        actor_user_id: str | None,
        account_id: str,
        offerings: Sequence[CapabilityOffering],
        *,
        source_revision: str,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        account_id = _stable_id(account_id, "account_id")
        source_revision = _external_id(source_revision, "source_revision")
        normalized: list[dict[str, Any]] = []
        for offering in offerings:
            if not isinstance(offering, CapabilityOffering):
                raise ValueError("offerings must contain CapabilityOffering objects")
            item = offering.to_dict()
            item["visual_classes"] = sorted(
                _visual_value(value, executable=True) for value in item["visual_classes"]
            )
            normalized.append(item)
        normalized.sort(
            key=lambda item: (item["capability"], item["variant_id"], item["provider_id"])
        )
        canonical = _json(normalized)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identity_digest = hashlib.sha256(
            f"{account_id}\n{canonical}".encode("utf-8")
        ).hexdigest()
        snapshot_id = _stable_id(
            snapshot_id or f"caps:{identity_digest[:24]}", "snapshot_id"
        )
        now = self._clock()
        with self._transaction() as connection:
            account = connection.execute(
                """SELECT a.provider_id, o.owner_user_id, e.access_scope
                   FROM provider_accounts AS a
                   LEFT JOIN provider_account_owners AS o
                     ON o.account_id = a.account_id
                   LEFT JOIN provider_account_entitlements AS e
                     ON e.account_id = a.account_id
                   WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if account is None:
                raise KeyError(account_id)
            if actor_user_id is None:
                if account["owner_user_id"] is not None or account["access_scope"] is not None:
                    raise StoreConflictError(
                        "owned provider account requires owner-scoped mutation"
                    )
            elif (
                account["owner_user_id"] != actor_user_id
                or account["access_scope"] is None
            ):
                raise StoreConflictError("provider account is unavailable")
            if any(item["provider_id"] != account["provider_id"] for item in normalized):
                raise ValueError("capability offering provider does not match its account")
            existing = connection.execute(
                """SELECT snapshot_id, content_sha256, actor_user_id
                     FROM capability_snapshots
                   WHERE account_id = ? AND (source_revision = ? OR content_sha256 = ?)""",
                (account_id, source_revision, digest),
            ).fetchone()
            if existing:
                if existing["content_sha256"] != digest:
                    raise StoreConflictError("source_revision already describes different capabilities")
                if existing["actor_user_id"] != actor_user_id:
                    raise StoreConflictError(
                        "capability snapshot belongs to another execution lane"
                    )
                return self.get_capability_snapshot(existing["snapshot_id"])
            connection.execute(
                """INSERT INTO capability_snapshots(
                       snapshot_id, account_id, source_revision, content_sha256,
                       captured_at, actor_user_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot_id, account_id, source_revision, digest, now, actor_user_id),
            )
            for item in normalized:
                for visual_class in item["visual_classes"]:
                    connection.execute(
                        """INSERT INTO capability_offerings(
                               snapshot_id, capability, variant_id, visual_class, evidence,
                               evidence_id, enabled, priority, option_keys_json
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            snapshot_id,
                            item["capability"],
                            item["variant_id"],
                            visual_class,
                            item["evidence"],
                            item["evidence_id"],
                            int(item["enabled"]),
                            item["priority"],
                            _json(item["option_keys"]),
                        ),
                    )
        return self.get_capability_snapshot(snapshot_id)

    def get_capability_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot_id = _stable_id(snapshot_id, "snapshot_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM capability_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            offering_rows = connection.execute(
                """SELECT * FROM capability_offerings WHERE snapshot_id = ?
                   ORDER BY capability, variant_id, visual_class""",
                (snapshot_id,),
            ).fetchall()
        if row is None:
            raise KeyError(snapshot_id)
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for offering in offering_rows:
            key = (offering["capability"], offering["variant_id"])
            target = grouped.setdefault(
                key,
                {
                    "capability": offering["capability"],
                    "variant_id": offering["variant_id"],
                    "visual_classes": [],
                    "evidence": offering["evidence"],
                    "evidence_id": offering["evidence_id"],
                    "enabled": bool(offering["enabled"]),
                    "priority": offering["priority"],
                    "option_keys": _from_json(offering["option_keys_json"]),
                },
            )
            target["visual_classes"].append(offering["visual_class"])
        return {
            "snapshot_id": row["snapshot_id"],
            "account_id": row["account_id"],
            "source_revision": row["source_revision"],
            "content_sha256": row["content_sha256"],
            "captured_at": row["captured_at"],
            "offerings": list(grouped.values()),
        }

    def latest_capability_snapshot(self, account_id: str) -> dict[str, Any] | None:
        """Return the newest snapshot for a legacy-unowned account."""

        account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            account = connection.execute(
                """SELECT o.owner_user_id, e.access_scope
                     FROM provider_accounts AS a
                     LEFT JOIN provider_account_owners AS o USING(account_id)
                     LEFT JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if account is None:
                raise KeyError(account_id)
            if account["owner_user_id"] is not None or account["access_scope"] is not None:
                raise StoreConflictError(
                    "owned provider account requires actor-scoped access"
                )
            row = connection.execute(
                """SELECT snapshot_id FROM capability_snapshots
                   WHERE account_id = ?
                   ORDER BY captured_at DESC, snapshot_id DESC
                   LIMIT 1""",
                (account_id,),
            ).fetchone()
        return self.get_capability_snapshot(row["snapshot_id"]) if row else None

    def latest_accessible_capability_snapshot(
        self,
        actor_user_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        """Return the newest immutable snapshot after an account ACL check."""

        actor_user_id = _stable_id(actor_user_id, "actor_user_id")
        account_id = _stable_id(account_id, "account_id")
        # Keep the access check and lookup in one read transaction so a future
        # entitlement design cannot introduce a time-of-check gap.
        with self._read_connection() as connection:
            account = connection.execute(
                """SELECT 1
                     FROM provider_accounts AS a
                     JOIN provider_account_owners AS o USING(account_id)
                     JOIN provider_account_entitlements AS e USING(account_id)
                    WHERE a.account_id = ? AND (
                        (e.access_scope = 'private' AND o.owner_user_id = ?
                         AND a.provider_id != 'local_comfy')
                        OR
                        (e.access_scope = 'authenticated_users'
                         AND o.owner_user_id = ?
                         AND a.provider_id = 'local_comfy')
                    )""",
                (account_id, actor_user_id, SYSTEM_LOCAL_OWNER_ID),
            ).fetchone()
            if account is None:
                raise KeyError(account_id)
            row = connection.execute(
                """SELECT snapshot_id FROM capability_snapshots
                   WHERE account_id = ?
                     AND actor_user_id = (
                         SELECT owner_user_id FROM provider_account_owners
                         WHERE account_id = ?
                     )
                   ORDER BY captured_at DESC, snapshot_id DESC
                   LIMIT 1""",
                (account_id, account_id),
            ).fetchone()
        return self.get_capability_snapshot(row["snapshot_id"]) if row else None

    def put_character(self, character: CharacterRef) -> dict[str, Any]:
        if not isinstance(character, CharacterRef):
            raise ValueError("character must be a CharacterRef")
        now = self._clock()
        with self._transaction() as connection:
            existing = connection.execute(
                """SELECT c.visual_class, o.owner_user_id
                   FROM canonical_characters AS c
                   LEFT JOIN canonical_character_owners AS o
                     ON o.character_id = c.character_id
                   WHERE c.character_id = ?""",
                (character.character_id,),
            ).fetchone()
            if existing is not None and existing["owner_user_id"] is not None:
                raise StoreConflictError("owned character requires owner-scoped mutation")
            visual_class = _visual_value(character.visual_class)
            if existing is not None:
                stored_class = str(existing["visual_class"])
                if stored_class != visual_class:
                    if stored_class != "unknown" or visual_class == "unknown":
                        raise StoreConflictError(
                            "a classified character cannot change visual class"
                        )
                    connection.execute(
                        """UPDATE canonical_characters
                           SET visual_class = ?, display_name = ?, updated_at = ?
                           WHERE character_id = ?""",
                        (visual_class, character.display_name, now, character.character_id),
                    )
                else:
                    connection.execute(
                        """UPDATE canonical_characters
                           SET display_name = ?, updated_at = ? WHERE character_id = ?""",
                        (character.display_name, now, character.character_id),
                    )
            else:
                connection.execute(
                    """INSERT INTO canonical_characters(
                           character_id, visual_class, display_name, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        character.character_id,
                        visual_class,
                        character.display_name,
                        now,
                        now,
                    ),
                )
        return self.get_character(character.character_id)

    def get_character(self, character_id: str) -> dict[str, Any]:
        character_id = _stable_id(character_id, "character_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_characters WHERE character_id = ?", (character_id,)
            ).fetchone()
            links = connection.execute(
                """SELECT link_id, account_id, external_character_id, linked_at
                   FROM provider_character_links WHERE character_id = ?
                   ORDER BY account_id, external_character_id""",
                (character_id,),
            ).fetchall()
        if row is None:
            raise KeyError(character_id)
        return {
            "character_id": row["character_id"],
            "visual_class": row["visual_class"],
            "display_name": row["display_name"],
            "provider_links": [dict(item) for item in links],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def put_owned_character(
        self,
        owner_user_id: str,
        character: CharacterRef,
    ) -> dict[str, Any]:
        """Create or update one canonical character inside an exact owner ACL.

        An existing unowned legacy row is deliberately not claimable here.
        Ownership adoption needs a separate reviewed workflow so a caller
        cannot win an identifier race and acquire historical provider links.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if not isinstance(character, CharacterRef):
            raise ValueError("character must be a CharacterRef")
        visual_class = _visual_value(character.visual_class)
        now = self._clock()
        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    """SELECT c.visual_class, o.owner_user_id
                       FROM canonical_characters AS c
                       LEFT JOIN canonical_character_owners AS o
                         ON o.character_id = c.character_id
                       WHERE c.character_id = ?""",
                    (character.character_id,),
                ).fetchone()
                if existing is not None and existing["owner_user_id"] != owner_user_id:
                    raise StoreConflictError("canonical character is unavailable")
                if existing is None:
                    connection.execute(
                        """INSERT INTO canonical_characters(
                               character_id, visual_class, display_name,
                               created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            character.character_id,
                            visual_class,
                            character.display_name,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO canonical_character_owners(
                               character_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (character.character_id, owner_user_id, now),
                    )
                else:
                    stored_class = str(existing["visual_class"])
                    if stored_class != visual_class:
                        if stored_class != "unknown" or visual_class == "unknown":
                            raise StoreConflictError(
                                "a classified character cannot change visual class"
                            )
                        connection.execute(
                            """UPDATE canonical_characters
                               SET visual_class = ?, display_name = ?, updated_at = ?
                               WHERE character_id = ?""",
                            (
                                visual_class,
                                character.display_name,
                                now,
                                character.character_id,
                            ),
                        )
                    else:
                        connection.execute(
                            """UPDATE canonical_characters
                               SET display_name = ?, updated_at = ?
                               WHERE character_id = ?""",
                            (character.display_name, now, character.character_id),
                        )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_character(owner_user_id, character.character_id)

    def get_owned_character(
        self,
        owner_user_id: str,
        character_id: str,
    ) -> dict[str, Any]:
        """Read one character only when the exact immutable owner matches."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        character_id = _stable_id(character_id, "character_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT c.*, p.projection_id AS profile_asset_id,
                          p.selection_source AS profile_selection_source
                   FROM canonical_characters AS c
                   JOIN canonical_character_owners AS o
                     ON o.character_id = c.character_id
                    AND o.owner_user_id = ?
                   LEFT JOIN canonical_character_profiles AS p
                     ON p.character_id = c.character_id
                   WHERE c.character_id = ?""",
                (owner_user_id, character_id),
            ).fetchone()
            if row is None:
                raise KeyError(character_id)
            links = connection.execute(
                """SELECT link_id, account_id, external_character_id, linked_at
                   FROM provider_character_links WHERE character_id = ?
                   ORDER BY account_id, external_character_id""",
                (character_id,),
            ).fetchall()
        return {
            "character_id": row["character_id"],
            "visual_class": row["visual_class"],
            "display_name": row["display_name"],
            "owner_user_id": owner_user_id,
            "profile_asset_id": row["profile_asset_id"],
            "profile_selection_source": row["profile_selection_source"],
            "provider_links": [dict(item) for item in links],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_owned_characters(
        self,
        owner_user_id: str,
        *,
        visual_class: VisualClass | str | None = None,
        after: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a bounded, owner-only canonical roster page."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        visual = _visual_value(visual_class) if visual_class is not None else None
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        values: list[Any] = [owner_user_id]
        clauses = ["o.owner_user_id = ?"]
        if visual is not None:
            clauses.append("c.visual_class = ?")
            values.append(visual)
        if after is not None:
            if not isinstance(after, tuple) or len(after) != 2:
                raise ValueError("after must be an (updated_at, character_id) tuple")
            after_time = _optional_text(after[0], "after updated_at", maximum=80)
            after_id = _stable_id(after[1], "after character_id")
            clauses.append(
                "(c.updated_at < ? OR (c.updated_at = ? AND c.character_id < ?))"
            )
            values.extend((after_time, after_time, after_id))
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT c.*, p.projection_id AS profile_asset_id,
                          p.selection_source AS profile_selection_source
                   FROM canonical_characters AS c
                   JOIN canonical_character_owners AS o
                     ON o.character_id = c.character_id
                   LEFT JOIN canonical_character_profiles AS p
                     ON p.character_id = c.character_id
                   WHERE """
                + " AND ".join(clauses)
                + " ORDER BY c.updated_at DESC, c.character_id DESC LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
            selected = rows[:limit]
            ids = [str(row["character_id"]) for row in selected]
            links_by_character: dict[str, list[dict[str, Any]]] = {
                character_id: [] for character_id in ids
            }
            if ids:
                placeholders = ",".join("?" for _ in ids)
                link_rows = connection.execute(
                    f"""SELECT character_id, link_id, account_id,
                               external_character_id, linked_at
                        FROM provider_character_links
                        WHERE character_id IN ({placeholders})
                        ORDER BY character_id, account_id, external_character_id""",
                    ids,
                ).fetchall()
                for link in link_rows:
                    links_by_character[str(link["character_id"])].append({
                        "link_id": link["link_id"],
                        "account_id": link["account_id"],
                        "external_character_id": link["external_character_id"],
                        "linked_at": link["linked_at"],
                    })
        items = [
            {
                "character_id": row["character_id"],
                "visual_class": row["visual_class"],
                "display_name": row["display_name"],
                "owner_user_id": owner_user_id,
                "profile_asset_id": row["profile_asset_id"],
                "profile_selection_source": row["profile_selection_source"],
                "provider_links": links_by_character[str(row["character_id"])],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in selected
        ]
        next_after = None
        if len(rows) > limit and selected:
            tail = selected[-1]
            next_after = (tail["updated_at"], tail["character_id"])
        return {"items": items, "next_after": next_after}

    @staticmethod
    def _public_character_model_binding(row: sqlite3.Row) -> dict[str, Any]:
        """Return the private server-side binding contract.

        Despite the historical ``_public_*`` store naming convention, this
        object contains server model names and must not be serialized by HTTP
        APIs.  ``ProviderFabricHttpApi`` projects only an opaque entrypoint.
        """

        return {
            "binding_id": row["binding_id"],
            "owner_user_id": row["owner_user_id"],
            "character_id": row["character_id"],
            "capability_id": row["capability_id"],
            "base_model_name": row["base_model_name"],
            "base_model_sha256": row["base_model_sha256"],
            "model_name": row["model_name"],
            "model_sha256": row["model_sha256"],
            "trigger_token": row["trigger_token"],
            "model_strength": float(row["model_strength"]),
            "clip_strength": float(row["clip_strength"]),
            "training_receipt_schema": row["training_receipt_schema"],
            "training_receipt_sha256": row["training_receipt_sha256"],
            "qualification_state": row["qualification_state"],
            "qualification_revision": int(row["qualification_revision"]),
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
        }

    def append_owned_character_model_binding(
        self,
        owner_user_id: str,
        character_id: str,
        *,
        base_model_name: str,
        base_model_sha256: str,
        model_name: str,
        model_sha256: str,
        trigger_token: str,
        training_receipt_schema: str,
        training_receipt_sha256: str,
        revision: int,
        capability_id: str = CHARACTER_LORA_CAPABILITY_ID,
        model_strength: float = 1.0,
        clip_strength: float = 1.0,
        qualification_state: str = "structurally_verified",
        qualification_revision: int = 1,
    ) -> dict[str, Any]:
        """Append one immutable owner/canonical-character model revision.

        This is a trusted publisher boundary, not an HTTP mutation.  Model
        inputs are safe names relative to server-owned roots and immutable
        digests; no caller-controlled host path can enter the database.
        Replaying the exact same revision is idempotent.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        character_id = _stable_id(character_id, "character_id")
        capability_id = _stable_id(capability_id, "capability_id")
        base_model_name = _model_basename(base_model_name, "base_model_name")
        base_model_sha256 = _sha256(base_model_sha256, "base_model_sha256")
        model_name = _relative_model_name(model_name)
        model_sha256 = _sha256(model_sha256, "model_sha256")
        trigger_token = str(trigger_token or "").strip()
        if _TRIGGER_TOKEN_RE.fullmatch(trigger_token) is None:
            raise ValueError("trigger_token is invalid")
        training_receipt_schema = str(training_receipt_schema or "").strip()
        if training_receipt_schema != "castle.clipmode-sdxl-training-receipt.v1":
            raise ValueError("training_receipt_schema is unsupported")
        training_receipt_sha256 = _sha256(
            training_receipt_sha256, "training_receipt_sha256"
        )
        if qualification_state not in {"structurally_verified", "quality_qualified"}:
            raise ValueError("qualification_state is invalid")
        if (
            isinstance(qualification_revision, bool)
            or not isinstance(qualification_revision, int)
            or qualification_revision < 1
        ):
            raise ValueError("qualification_revision must be a positive integer")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("revision must be a positive integer")
        model_strength = _model_strength(model_strength, "model_strength")
        clip_strength = _model_strength(clip_strength, "clip_strength")
        identity = json.dumps({
            "owner_user_id": owner_user_id,
            "character_id": character_id,
            "capability_id": capability_id,
            "base_model_name": base_model_name,
            "base_model_sha256": base_model_sha256,
            "model_name": model_name,
            "model_sha256": model_sha256,
            "trigger_token": trigger_token,
            "model_strength": model_strength,
            "clip_strength": clip_strength,
            "training_receipt_schema": training_receipt_schema,
            "training_receipt_sha256": training_receipt_sha256,
            "qualification_state": qualification_state,
            "qualification_revision": qualification_revision,
            "revision": revision,
        }, sort_keys=True, separators=(",", ":"), allow_nan=False)
        binding_id = "modelbind:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        now = self._clock()
        facts = {
            "binding_id": binding_id,
            "owner_user_id": owner_user_id,
            "character_id": character_id,
            "capability_id": capability_id,
            "base_model_name": base_model_name,
            "base_model_sha256": base_model_sha256,
            "model_name": model_name,
            "model_sha256": model_sha256,
            "trigger_token": trigger_token,
            "model_strength": model_strength,
            "clip_strength": clip_strength,
            "training_receipt_schema": training_receipt_schema,
            "training_receipt_sha256": training_receipt_sha256,
            "qualification_state": qualification_state,
            "qualification_revision": qualification_revision,
            "revision": revision,
        }
        try:
            with self._transaction() as connection:
                owned = connection.execute(
                    """SELECT 1 FROM canonical_character_owners
                       WHERE owner_user_id = ? AND character_id = ?""",
                    (owner_user_id, character_id),
                ).fetchone()
                if owned is None:
                    raise KeyError(character_id)
                existing = connection.execute(
                    """SELECT * FROM canonical_character_model_bindings
                       WHERE owner_user_id = ? AND character_id = ?
                         AND capability_id = ? AND revision = ?""",
                    (owner_user_id, character_id, capability_id, revision),
                ).fetchone()
                if existing is not None:
                    current = self._public_character_model_binding(existing)
                    if all(current[key] == value for key, value in facts.items()):
                        return current
                    raise StoreConflictError(
                        "character model binding revision already contains different facts"
                    )
                latest = connection.execute(
                    """SELECT MAX(revision) AS revision
                       FROM canonical_character_model_bindings
                       WHERE owner_user_id = ? AND character_id = ?
                         AND capability_id = ?""",
                    (owner_user_id, character_id, capability_id),
                ).fetchone()
                expected_revision = 1 if latest["revision"] is None else int(latest["revision"]) + 1
                if revision != expected_revision:
                    raise StoreConflictError(
                        f"character model binding revision must be {expected_revision}"
                    )
                connection.execute(
                    """INSERT INTO canonical_character_model_bindings(
                           binding_id, owner_user_id, character_id, capability_id,
                           base_model_name, base_model_sha256, model_name,
                           model_sha256, trigger_token, model_strength,
                           clip_strength, training_receipt_schema,
                           training_receipt_sha256, qualification_state,
                           qualification_revision, revision, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        binding_id, owner_user_id, character_id, capability_id,
                        base_model_name, base_model_sha256, model_name,
                        model_sha256, trigger_token, model_strength,
                        clip_strength, training_receipt_schema,
                        training_receipt_sha256, qualification_state,
                        qualification_revision, revision, now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.resolve_owned_character_model_binding(
            owner_user_id, character_id, capability_id
        )

    def resolve_owned_character_model_binding(
        self,
        owner_user_id: str,
        character_id: str,
        capability_id: str = CHARACTER_LORA_CAPABILITY_ID,
    ) -> dict[str, Any]:
        """Resolve the newest immutable binding inside the exact owner ACL."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        character_id = _stable_id(character_id, "character_id")
        capability_id = _stable_id(capability_id, "capability_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT binding.*
                     FROM canonical_character_model_bindings AS binding
                     JOIN canonical_character_owners AS owner
                       ON owner.character_id = binding.character_id
                      AND owner.owner_user_id = binding.owner_user_id
                    WHERE binding.owner_user_id = ?
                      AND binding.character_id = ?
                      AND binding.capability_id = ?
                    ORDER BY binding.revision DESC
                    LIMIT 1""",
                (owner_user_id, character_id, capability_id),
            ).fetchone()
        if row is None:
            raise KeyError(character_id)
        return self._public_character_model_binding(row)

    def link_provider_character(
        self,
        account_id: str,
        external_character_id: str,
        character_id: str,
        *,
        visual_class: VisualClass | str,
        metadata: Mapping[str, Any] | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        account_id = _stable_id(account_id, "account_id")
        character_id = _stable_id(character_id, "character_id")
        external_character_id = _external_id(external_character_id)
        visual_class = _visual_value(visual_class)
        metadata_json = _json(dict(metadata or {}))
        link_id = _stable_id(link_id or _new_id("charlink"), "link_id")
        now = self._clock()
        try:
            with self._transaction() as connection:
                account_owner = connection.execute(
                    """SELECT owner_user_id FROM provider_account_owners
                       WHERE account_id = ?""",
                    (account_id,),
                ).fetchone()
                character_owner = connection.execute(
                    """SELECT owner_user_id FROM canonical_character_owners
                       WHERE character_id = ?""",
                    (character_id,),
                ).fetchone()
                if account_owner is not None or character_owner is not None:
                    raise StoreConflictError(
                        "owned character link requires owner-scoped mutation"
                    )
                existing = connection.execute(
                    """SELECT * FROM provider_character_links
                       WHERE account_id = ? AND external_character_id = ?""",
                    (account_id, external_character_id),
                ).fetchone()
                if existing:
                    if existing["character_id"] != character_id:
                        raise StoreConflictError(
                            "external character ID is already linked to another canonical character"
                        )
                    return self._public_character_link(existing)
                connection.execute(
                    """INSERT INTO provider_character_links(
                           link_id, account_id, external_character_id, character_id,
                           visual_class, metadata_json, linked_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        link_id,
                        account_id,
                        external_character_id,
                        character_id,
                        visual_class,
                        metadata_json,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_provider_character_link(link_id)

    def link_owned_provider_character(
        self,
        owner_user_id: str,
        account_id: str,
        external_character_id: str,
        character_id: str,
        *,
        visual_class: VisualClass | str,
        metadata: Mapping[str, Any] | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        """Link a provider identity without permitting a cross-owner target."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        character_id = _stable_id(character_id, "character_id")
        external_character_id = _external_id(external_character_id)
        visual_class = _visual_value(visual_class)
        metadata_json = _json(dict(metadata or {}))
        link_id = _stable_id(link_id or _new_id("charlink"), "link_id")
        now = self._clock()
        try:
            with self._transaction() as connection:
                owned_account = connection.execute(
                    """SELECT 1
                         FROM provider_accounts AS account
                         JOIN provider_account_owners AS account_owner USING(account_id)
                         LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
                        WHERE account.account_id = ? AND (
                            (
                                account.provider_id != 'local_comfy'
                                AND account_owner.owner_user_id = ?
                            ) OR (
                                entitlement.access_scope = 'authenticated_users'
                                AND account.provider_id = 'local_comfy'
                                AND account_owner.owner_user_id = ?
                            )
                        )""",
                    (account_id, owner_user_id, SYSTEM_LOCAL_OWNER_ID),
                ).fetchone()
                if owned_account is None:
                    raise KeyError(account_id)
                owned = connection.execute(
                    """SELECT c.visual_class
                       FROM canonical_characters AS c
                       JOIN canonical_character_owners AS o
                         ON o.character_id = c.character_id
                        AND o.owner_user_id = ?
                       WHERE c.character_id = ?""",
                    (owner_user_id, character_id),
                ).fetchone()
                if owned is None:
                    raise KeyError(character_id)
                if str(owned["visual_class"]) != visual_class:
                    raise StoreConflictError("provider character visual class mismatch")
                existing = connection.execute(
                    """SELECT l.*, o.owner_user_id
                       FROM provider_character_links AS l
                       LEFT JOIN canonical_character_owners AS o
                         ON o.character_id = l.character_id
                       WHERE l.account_id = ? AND l.external_character_id = ?""",
                    (account_id, external_character_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["owner_user_id"] != owner_user_id
                        or existing["character_id"] != character_id
                    ):
                        raise StoreConflictError("provider character identity is unavailable")
                    return self._public_character_link(existing)
                connection.execute(
                    """INSERT INTO provider_character_links(
                           link_id, account_id, external_character_id, character_id,
                           visual_class, metadata_json, linked_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        link_id,
                        account_id,
                        external_character_id,
                        character_id,
                        visual_class,
                        metadata_json,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_provider_character_link(owner_user_id, link_id)

    @staticmethod
    def _public_character_link(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "link_id": row["link_id"],
            "account_id": row["account_id"],
            "external_character_id": row["external_character_id"],
            "character_id": row["character_id"],
            "visual_class": row["visual_class"],
            "metadata": _from_json(row["metadata_json"]),
            "linked_at": row["linked_at"],
        }

    def get_provider_character_link(self, link_id: str) -> dict[str, Any]:
        link_id = _stable_id(link_id, "link_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_character_links WHERE link_id = ?", (link_id,)
            ).fetchone()
        if row is None:
            raise KeyError(link_id)
        return self._public_character_link(row)

    def get_owned_provider_character_link(
        self,
        owner_user_id: str,
        link_id: str,
    ) -> dict[str, Any]:
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        link_id = _stable_id(link_id, "link_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT l.* FROM provider_character_links AS l
                   JOIN canonical_character_owners AS o
                     ON o.character_id = l.character_id
                    AND o.owner_user_id = ?
                   WHERE l.link_id = ?""",
                (owner_user_id, link_id),
            ).fetchone()
        if row is None:
            raise KeyError(link_id)
        return self._public_character_link(row)

    def project_remote_asset(
        self,
        projection_id: str,
        account_id: str,
        external_asset_id: str,
        kind: MediaKind | str,
        *,
        visual_class: VisualClass | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        remote_created_at: str | None = None,
    ) -> dict[str, Any]:
        projection_id = _stable_id(projection_id, "projection_id")
        account_id = _stable_id(account_id, "account_id")
        external_asset_id = _external_id(external_asset_id)
        kind = MediaKind(kind).value
        visual = _visual_value(visual_class) if visual_class is not None else None
        metadata_json = _json(dict(metadata or {}))
        remote_created_at = _optional_text(remote_created_at, "remote_created_at", maximum=80)
        now = self._clock()
        try:
            with self._transaction() as connection:
                owned_account = connection.execute(
                    """SELECT 1 FROM provider_account_owners
                       WHERE account_id = ?""",
                    (account_id,),
                ).fetchone()
                if owned_account is not None:
                    raise StoreConflictError(
                        "owned provider account requires owner-scoped projection"
                    )
                existing = connection.execute(
                    """SELECT * FROM remote_asset_projections
                       WHERE account_id = ? AND external_asset_id = ?""",
                    (account_id, external_asset_id),
                ).fetchone()
                if existing:
                    if existing["projection_id"] != projection_id:
                        raise StoreConflictError("external asset ID already has a projection")
                    if existing["media_kind"] != kind or existing["visual_class"] != visual:
                        raise StoreConflictError("an asset projection cannot change identity fields")
                    connection.execute(
                        """UPDATE remote_asset_projections
                           SET metadata_json = ?, remote_created_at = COALESCE(?, remote_created_at),
                               last_seen_at = ? WHERE projection_id = ?""",
                        (metadata_json, remote_created_at, now, projection_id),
                    )
                else:
                    connection.execute(
                        """INSERT INTO remote_asset_projections(
                               projection_id, account_id, external_asset_id, media_kind,
                               visual_class, metadata_json, remote_created_at,
                               first_seen_at, last_seen_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            projection_id,
                            account_id,
                            external_asset_id,
                            kind,
                            visual,
                            metadata_json,
                            remote_created_at,
                            now,
                            now,
                        ),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_remote_asset(projection_id)

    def project_owned_remote_asset(
        self,
        owner_user_id: str,
        projection_id: str,
        account_id: str,
        external_asset_id: str,
        kind: MediaKind | str,
        *,
        visual_class: VisualClass | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        remote_created_at: str | None = None,
    ) -> dict[str, Any]:
        """Atomically project metadata and bind it to one exact owner."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        account_id = _stable_id(account_id, "account_id")
        external_asset_id = _external_id(external_asset_id)
        kind = MediaKind(kind).value
        visual = _visual_value(visual_class) if visual_class is not None else None
        metadata_json = _json(dict(metadata or {}))
        remote_created_at = _optional_text(remote_created_at, "remote_created_at", maximum=80)
        now = self._clock()
        try:
            with self._transaction() as connection:
                owned_account = connection.execute(
                    """SELECT 1
                         FROM provider_accounts AS account
                         JOIN provider_account_owners AS account_owner USING(account_id)
                         LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
                        WHERE account.account_id = ? AND (
                            (
                                account.provider_id != 'local_comfy'
                                AND account_owner.owner_user_id = ?
                            ) OR (
                                entitlement.access_scope = 'authenticated_users'
                                AND account.provider_id = 'local_comfy'
                                AND account_owner.owner_user_id = ?
                            )
                        )""",
                    (account_id, owner_user_id, SYSTEM_LOCAL_OWNER_ID),
                ).fetchone()
                if owned_account is None:
                    raise KeyError(account_id)
                existing = connection.execute(
                    """SELECT p.*, o.owner_user_id
                       FROM remote_asset_projections AS p
                       LEFT JOIN remote_asset_owners AS o
                         ON o.projection_id = p.projection_id
                       WHERE p.account_id = ? AND p.external_asset_id = ?""",
                    (account_id, external_asset_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["projection_id"] != projection_id
                        or existing["owner_user_id"] != owner_user_id
                    ):
                        raise StoreConflictError("provider asset identity is unavailable")
                    if existing["media_kind"] != kind or existing["visual_class"] != visual:
                        raise StoreConflictError("an asset projection cannot change identity fields")
                    connection.execute(
                        """UPDATE remote_asset_projections
                           SET metadata_json = ?,
                               remote_created_at = COALESCE(?, remote_created_at),
                               last_seen_at = ?
                           WHERE projection_id = ?""",
                        (metadata_json, remote_created_at, now, projection_id),
                    )
                else:
                    id_collision = connection.execute(
                        """SELECT 1 FROM remote_asset_projections
                           WHERE projection_id = ?""",
                        (projection_id,),
                    ).fetchone()
                    if id_collision is not None:
                        raise StoreConflictError("provider asset identity is unavailable")
                    connection.execute(
                        """INSERT INTO remote_asset_projections(
                               projection_id, account_id, external_asset_id, media_kind,
                               visual_class, metadata_json, remote_created_at,
                               first_seen_at, last_seen_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            projection_id,
                            account_id,
                            external_asset_id,
                            kind,
                            visual,
                            metadata_json,
                            remote_created_at,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO remote_asset_owners(
                               projection_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (projection_id, owner_user_id, now),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_remote_asset(owner_user_id, projection_id)

    def add_remote_asset_version(
        self,
        projection_id: str,
        source_uri: str,
        *,
        version_id: str | None = None,
        version_number: int | None = None,
        provider_version_id: str | None = None,
        preview_uri: str | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        sha256: str | None = None,
        duration_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
        _owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        projection_id = _stable_id(projection_id, "projection_id")
        if _owner_user_id is not None:
            _owner_user_id = _stable_id(_owner_user_id, "owner_user_id")
        version_id = _stable_id(version_id or _new_id("assetver"), "version_id")
        source_uri = _external_id(source_uri, "source_uri")
        preview_uri = _optional_text(preview_uri, "preview_uri", maximum=8192)
        provider_version_id = _optional_text(
            provider_version_id, "provider_version_id", maximum=500
        )
        for uri in (source_uri, preview_uri):
            if uri is not None and urlsplit(uri).scheme not in {"https", "http", "castle", "file"}:
                raise ValueError("asset URI uses an unsupported scheme")
        if mime_type is not None:
            mime_type = str(mime_type).strip().lower()
            if not _MIME_RE.fullmatch(mime_type):
                raise ValueError("mime_type is invalid")
        if byte_size is not None and (
            not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0
        ):
            raise ValueError("byte_size must be a non-negative integer")
        if sha256 is not None:
            sha256 = str(sha256).strip().lower()
            if not _SHA256_RE.fullmatch(sha256):
                raise ValueError("sha256 must be a lowercase digest")
        if duration_seconds is not None and float(duration_seconds) < 0:
            raise ValueError("duration_seconds must be non-negative")
        metadata_json = _json(dict(metadata or {}))
        now = self._clock()
        try:
            with self._transaction() as connection:
                owner = connection.execute(
                    """SELECT owner_user_id FROM remote_asset_owners
                       WHERE projection_id = ?""",
                    (projection_id,),
                ).fetchone()
                stored_owner = str(owner["owner_user_id"]) if owner is not None else None
                if stored_owner != _owner_user_id:
                    raise StoreConflictError("asset version target is unavailable")
                if version_number is None:
                    row = connection.execute(
                        """SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
                           FROM remote_asset_versions WHERE projection_id = ?""",
                        (projection_id,),
                    ).fetchone()
                    version_number = int(row["next_version"])
                if not isinstance(version_number, int) or version_number < 1:
                    raise ValueError("version_number must be a positive integer")
                connection.execute(
                    """INSERT INTO remote_asset_versions(
                           version_id, projection_id, version_number, provider_version_id,
                           source_uri, preview_uri, mime_type, byte_size, sha256,
                           duration_seconds, metadata_json, observed_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        version_id,
                        projection_id,
                        version_number,
                        provider_version_id,
                        source_uri,
                        preview_uri,
                        mime_type,
                        byte_size,
                        sha256,
                        duration_seconds,
                        metadata_json,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_remote_asset(projection_id)

    def add_owned_remote_asset_version(
        self,
        owner_user_id: str,
        projection_id: str,
        source_uri: str,
        *,
        version_id: str | None = None,
        version_number: int | None = None,
        provider_version_id: str | None = None,
        preview_uri: str | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        sha256: str | None = None,
        duration_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Owner-checked wrapper around immutable asset-version insertion."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        self.get_owned_remote_asset(owner_user_id, projection_id)
        self.add_remote_asset_version(
            projection_id,
            source_uri,
            version_id=version_id,
            version_number=version_number,
            provider_version_id=provider_version_id,
            preview_uri=preview_uri,
            mime_type=mime_type,
            byte_size=byte_size,
            sha256=sha256,
            duration_seconds=duration_seconds,
            metadata=metadata,
            _owner_user_id=owner_user_id,
        )
        return self.get_owned_remote_asset(owner_user_id, projection_id)

    def link_asset_character(self, projection_id: str, character_id: str) -> None:
        projection_id = _stable_id(projection_id, "projection_id")
        character_id = _stable_id(character_id, "character_id")
        try:
            with self._transaction() as connection:
                asset_owner = connection.execute(
                    """SELECT owner_user_id FROM remote_asset_owners
                       WHERE projection_id = ?""",
                    (projection_id,),
                ).fetchone()
                character_owner = connection.execute(
                    """SELECT owner_user_id FROM canonical_character_owners
                       WHERE character_id = ?""",
                    (character_id,),
                ).fetchone()
                if asset_owner is not None or character_owner is not None:
                    raise StoreConflictError(
                        "owned asset link requires owner-scoped mutation"
                    )
                connection.execute(
                    """INSERT OR IGNORE INTO asset_character_links(
                           projection_id, character_id, linked_at
                       ) VALUES (?, ?, ?)""",
                    (projection_id, character_id, self._clock()),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error

    def link_owned_asset_character(
        self,
        owner_user_id: str,
        projection_id: str,
        character_id: str,
    ) -> None:
        """Link only records belonging to the exact same authenticated owner."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        character_id = _stable_id(character_id, "character_id")
        try:
            with self._transaction() as connection:
                asset = connection.execute(
                    """SELECT 1 FROM remote_asset_owners
                       WHERE projection_id = ? AND owner_user_id = ?""",
                    (projection_id, owner_user_id),
                ).fetchone()
                character = connection.execute(
                    """SELECT 1 FROM canonical_character_owners
                       WHERE character_id = ? AND owner_user_id = ?""",
                    (character_id, owner_user_id),
                ).fetchone()
                if asset is None:
                    raise KeyError(projection_id)
                if character is None:
                    raise KeyError(character_id)
                connection.execute(
                    """INSERT OR IGNORE INTO asset_character_links(
                           projection_id, character_id, linked_at
                       ) VALUES (?, ?, ?)""",
                    (projection_id, character_id, self._clock()),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error

    def set_owned_character_profile(
        self,
        owner_user_id: str,
        character_id: str,
        projection_id: str,
    ) -> dict[str, Any]:
        """Select an already linked, same-owner image as the profile asset."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        character_id = _stable_id(character_id, "character_id")
        projection_id = _stable_id(projection_id, "projection_id")
        try:
            with self._transaction() as connection:
                character = connection.execute(
                    """SELECT 1 FROM canonical_character_owners
                       WHERE character_id = ? AND owner_user_id = ?""",
                    (character_id, owner_user_id),
                ).fetchone()
                asset = connection.execute(
                    """SELECT 1 FROM remote_asset_owners
                       WHERE projection_id = ? AND owner_user_id = ?""",
                    (projection_id, owner_user_id),
                ).fetchone()
                if character is None:
                    raise KeyError(character_id)
                if asset is None:
                    raise KeyError(projection_id)
                connection.execute(
                    """INSERT INTO canonical_character_profiles(
                           character_id, projection_id, selection_source, selected_at
                       ) VALUES (?, ?, ?, ?)
                       ON CONFLICT(character_id) DO UPDATE SET
                           projection_id = excluded.projection_id,
                           selection_source = excluded.selection_source,
                           selected_at = excluded.selected_at""",
                    (
                        character_id,
                        projection_id,
                        "explicit",
                        self._clock(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_character(owner_user_id, character_id)

    def add_asset_lineage(
        self,
        parent_projection_id: str,
        child_projection_id: str,
        relation: str,
        *,
        intent_id: str | None = None,
        _owner_user_id: str | None = None,
    ) -> None:
        parent_projection_id = _stable_id(parent_projection_id, "parent_projection_id")
        child_projection_id = _stable_id(child_projection_id, "child_projection_id")
        relation = _stable_id(relation, "relation")
        intent_id = _stable_id(intent_id, "intent_id") if intent_id else None
        if _owner_user_id is not None:
            _owner_user_id = _stable_id(_owner_user_id, "owner_user_id")
        try:
            with self._transaction() as connection:
                if parent_projection_id == child_projection_id:
                    raise ValueError("an asset cannot be its own lineage parent")
                owner_rows = connection.execute(
                    """SELECT projection_id, owner_user_id FROM remote_asset_owners
                       WHERE projection_id IN (?, ?)""",
                    (parent_projection_id, child_projection_id),
                ).fetchall()
                owners = {
                    str(row["projection_id"]): str(row["owner_user_id"])
                    for row in owner_rows
                }
                if (
                    owners.get(parent_projection_id) != _owner_user_id
                    or owners.get(child_projection_id) != _owner_user_id
                ):
                    raise StoreConflictError("asset lineage target is unavailable")
                cycle = connection.execute(
                    """WITH RECURSIVE descendants(projection_id) AS (
                           SELECT child_projection_id FROM asset_lineage
                           WHERE parent_projection_id = ?
                           UNION
                           SELECT lineage.child_projection_id FROM asset_lineage AS lineage
                           JOIN descendants ON lineage.parent_projection_id = descendants.projection_id
                       )
                       SELECT 1 FROM descendants WHERE projection_id = ? LIMIT 1""",
                    (child_projection_id, parent_projection_id),
                ).fetchone()
                if cycle:
                    raise StoreConflictError("asset lineage cannot contain a cycle")
                connection.execute(
                    """INSERT OR IGNORE INTO asset_lineage(
                           parent_projection_id, child_projection_id, relation,
                           intent_id, created_at
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        parent_projection_id,
                        child_projection_id,
                        relation,
                        intent_id,
                        self._clock(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error

    def add_owned_asset_lineage(
        self,
        owner_user_id: str,
        parent_projection_id: str,
        child_projection_id: str,
        relation: str,
        *,
        intent_id: str | None = None,
    ) -> None:
        """Add lineage only when both assets belong to the same exact owner."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        parent_projection_id = _stable_id(parent_projection_id, "parent_projection_id")
        child_projection_id = _stable_id(child_projection_id, "child_projection_id")
        self.get_owned_remote_asset(owner_user_id, parent_projection_id)
        self.get_owned_remote_asset(owner_user_id, child_projection_id)
        self.add_asset_lineage(
            parent_projection_id,
            child_projection_id,
            relation,
            intent_id=intent_id,
            _owner_user_id=owner_user_id,
        )

    def get_remote_asset(self, projection_id: str) -> dict[str, Any]:
        projection_id = _stable_id(projection_id, "projection_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT p.*, a.provider_id, a.credential_kind
                     FROM remote_asset_projections AS p
                     JOIN provider_accounts AS a USING(account_id)
                    WHERE p.projection_id = ?""",
                (projection_id,),
            ).fetchone()
            versions = connection.execute(
                """SELECT v.*,
                          original.sha256 AS original_blob_sha256,
                          original_receipt.receipt_id AS original_receipt_active,
                          preview.sha256 AS preview_blob_sha256,
                          preview_receipt.receipt_id AS preview_receipt_active,
                          pinned.rendition_sha256 AS pinned_preview_sha256,
                          pinned_receipt.receipt_id AS pinned_receipt_active
                     FROM remote_asset_versions AS v
                     LEFT JOIN remote_asset_owners AS owner
                       ON owner.projection_id = v.projection_id
                    LEFT JOIN provider_asset_version_blobs AS original
                       ON original.owner_user_id = owner.owner_user_id
                      AND original.version_id = v.version_id
                      AND original.role = 'original'
                     LEFT JOIN provider_import_receipts AS original_receipt
                       ON original_receipt.receipt_id = original.receipt_id
                      AND original_receipt.owner_user_id = original.owner_user_id
                      AND original_receipt.state = 'active'
                     LEFT JOIN provider_asset_version_blobs AS preview
                       ON preview.owner_user_id = owner.owner_user_id
                      AND preview.version_id = v.version_id
                      AND preview.role = 'preview_rendition'
                     LEFT JOIN provider_import_receipts AS preview_receipt
                       ON preview_receipt.receipt_id = preview.receipt_id
                      AND preview_receipt.owner_user_id = preview.owner_user_id
                      AND preview_receipt.state = 'active'
                     LEFT JOIN projection_preview_versions AS pinned
                       ON pinned.owner_user_id = owner.owner_user_id
                      AND pinned.projection_id = v.projection_id
                      AND pinned.version_id = v.version_id
                      AND pinned.rendition_sha256 = preview.sha256
                     LEFT JOIN provider_import_receipts AS pinned_receipt
                       ON pinned_receipt.receipt_id = pinned.receipt_id
                      AND pinned_receipt.owner_user_id = pinned.owner_user_id
                      AND pinned_receipt.state = 'active'
                    WHERE v.projection_id = ?
                    ORDER BY v.version_number""",
                (projection_id,),
            ).fetchall()
            characters = connection.execute(
                """SELECT character_id FROM asset_character_links WHERE projection_id = ?
                   ORDER BY character_id""",
                (projection_id,),
            ).fetchall()
            parents = connection.execute(
                """SELECT parent_projection_id, relation, intent_id FROM asset_lineage
                   WHERE child_projection_id = ? ORDER BY parent_projection_id, relation""",
                (projection_id,),
            ).fetchall()
        if row is None:
            raise KeyError(projection_id)
        local_import_namespace = (
            row["provider_id"] == "clipmode" and row["credential_kind"] == "none"
        )
        return {
            "projection_id": row["projection_id"],
            "account_id": row["account_id"],
            "external_asset_id": row["external_asset_id"],
            "media_kind": row["media_kind"],
            "visual_class": row["visual_class"],
            "metadata": _from_json(row["metadata_json"]),
            "remote_created_at": row["remote_created_at"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "character_ids": [item["character_id"] for item in characters],
            "versions": [
                {
                    "version_id": item["version_id"],
                    "version_number": item["version_number"],
                    "provider_version_id": item["provider_version_id"],
                    "source_available": (
                        item["original_blob_sha256"] is not None
                        and item["original_receipt_active"] is not None
                        if local_import_namespace
                        else bool(item["source_uri"])
                    ),
                    "preview_available": (
                        item["preview_blob_sha256"] is not None
                        and item["pinned_preview_sha256"] is not None
                        and item["preview_receipt_active"] is not None
                        and item["pinned_receipt_active"] is not None
                        if local_import_namespace
                        else bool(item["preview_uri"])
                    ),
                    "mime_type": item["mime_type"],
                    "byte_size": item["byte_size"],
                    "sha256": item["sha256"],
                    "duration_seconds": item["duration_seconds"],
                    "metadata": _from_json(item["metadata_json"]),
                    "observed_at": item["observed_at"],
                }
                for item in versions
            ],
            "parents": [dict(item) for item in parents],
        }

    def get_owned_remote_asset(
        self,
        owner_user_id: str,
        projection_id: str,
    ) -> dict[str, Any]:
        """Return an owned projection; missing and cross-owner are identical."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM remote_asset_owners
                   WHERE projection_id = ? AND owner_user_id = ?""",
                (projection_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise KeyError(projection_id)
        result = self.get_remote_asset(projection_id)
        result["owner_user_id"] = owner_user_id
        return result

    def get_owned_remote_asset_version_source(
        self, owner_user_id: str, projection_id: str, version_id: str
    ) -> dict[str, Any]:
        """Return private execution evidence for one exact owned version.

        ``source_uri`` may contain a private local path.  Callers must never
        serialize this result into an HTTP response.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        version_id = _stable_id(version_id, "version_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT p.projection_id, p.media_kind, p.visual_class,
                          v.version_id, v.source_uri, v.mime_type, v.byte_size,
                          v.sha256, v.duration_seconds,
                          original.sha256 AS original_blob_sha256,
                          blob.byte_size AS original_blob_byte_size,
                          blob.signature_family AS original_signature_family,
                          receipt.receipt_id AS original_receipt_active
                     FROM remote_asset_projections AS p
                     JOIN remote_asset_owners AS owner
                       ON owner.projection_id = p.projection_id
                      AND owner.owner_user_id = ?
                     JOIN remote_asset_versions AS v
                       ON v.projection_id = p.projection_id
                      AND v.version_id = ?
                     LEFT JOIN provider_asset_version_blobs AS original
                       ON original.owner_user_id = owner.owner_user_id
                      AND original.version_id = v.version_id
                      AND original.role = 'original'
                     LEFT JOIN provider_media_blobs AS blob
                       ON blob.owner_user_id = original.owner_user_id
                      AND blob.sha256 = original.sha256
                     LEFT JOIN provider_import_receipts AS receipt
                       ON receipt.receipt_id = original.receipt_id
                      AND receipt.owner_user_id = original.owner_user_id
                      AND receipt.state = 'active'
                    WHERE p.projection_id = ?""",
                (owner_user_id, version_id, projection_id),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return dict(row)

    def get_owned_remote_asset_version_source_by_id(
        self, owner_user_id: str, version_id: str,
    ) -> dict[str, Any]:
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        version_id = _stable_id(version_id, "version_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT version.projection_id
                     FROM remote_asset_versions AS version
                     JOIN remote_asset_owners AS owner
                       ON owner.projection_id = version.projection_id
                      AND owner.owner_user_id = ?
                    WHERE version.version_id = ?""",
                (owner_user_id, version_id),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self.get_owned_remote_asset_version_source(
            owner_user_id, str(row["projection_id"]), version_id
        )

    @staticmethod
    def _public_asset_classification(row: Mapping[str, Any]) -> dict[str, Any]:
        character_ids = _from_json(str(row["character_ids_json"]))
        if not isinstance(character_ids, list):
            raise ProviderFabricStoreError("asset classification evidence is invalid")
        return {
            "classification_id": row["classification_id"],
            "projection_id": row["projection_id"],
            "asset_version_id": row["asset_version_id"],
            "visual_class": row["visual_class"],
            "character_ids": character_ids,
            "classification_version": row["classification_version"],
            "classified_at": row["classified_at"],
        }

    @staticmethod
    def _asset_classification_graph_matches(
        connection: sqlite3.Connection,
        row: Mapping[str, Any],
    ) -> bool:
        try:
            selected = _from_json(str(row["character_ids_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= 4
            or any(not isinstance(item, str) for item in selected)
            or selected != sorted(set(selected))
        ):
            return False
        actual = connection.execute(
            """SELECT projection.visual_class,
                      (SELECT COUNT(*)
                         FROM remote_asset_versions AS version
                        WHERE version.projection_id = projection.projection_id
                      ) AS version_count,
                      (SELECT COUNT(*)
                         FROM remote_asset_versions AS version
                        WHERE version.projection_id = projection.projection_id
                          AND version.version_id = ?
                      ) AS exact_version_count
                 FROM remote_asset_projections AS projection
                WHERE projection.projection_id = ?""",
            (row["asset_version_id"], row["projection_id"]),
        ).fetchone()
        actual_links = [
            str(link["character_id"])
            for link in connection.execute(
                """SELECT character_id FROM asset_character_links
                    WHERE projection_id = ? ORDER BY character_id""",
                (row["projection_id"],),
            ).fetchall()
        ]
        return bool(
            actual is not None
            and actual["visual_class"] == row["visual_class"]
            and actual["version_count"] == 1
            and actual["exact_version_count"] == 1
            and actual_links == selected
        )

    def seal_owned_autoip_history_adoption(
        self,
        owner_user_id: str,
        account_id: str,
        task_uuid: str,
        assets: Sequence[Mapping[str, object]],
    ) -> dict[str, Any]:
        """Seal one completed history task after every output graph exists.

        Individual projection writes remain safely retryable.  This append-only
        receipt is inserted only after the exact output-index set and all of its
        deterministic projection/version identities have been revalidated in
        one immediate transaction.  An exact replay is idempotent.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        if (
            not isinstance(task_uuid, str)
            or _AUTOIP_TASK_UUID_RE.fullmatch(task_uuid) is None
        ):
            raise ValueError("task_uuid must be a canonical UUID")
        if (
            not isinstance(assets, Sequence)
            or isinstance(assets, (str, bytes, bytearray))
            or not 1 <= len(assets) <= 16
        ):
            raise ValueError("assets must contain 1..16 adopted outputs")

        normalized: list[dict[str, object]] = []
        for item in assets:
            if not isinstance(item, Mapping) or set(item) != {
                "output_index", "projection_id", "asset_version_id",
            }:
                raise ValueError("adopted output identity is invalid")
            output_index = item.get("output_index")
            if type(output_index) is not int or output_index < 0:
                raise ValueError("adopted output index is invalid")
            normalized.append({
                "output_index": output_index,
                "projection_id": _stable_id(
                    item.get("projection_id"), "projection_id",
                ),
                "asset_version_id": _stable_id(
                    item.get("asset_version_id"), "asset_version_id",
                ),
            })
        normalized.sort(key=lambda item: int(item["output_index"]))
        indexes = [int(item["output_index"]) for item in normalized]
        if len(indexes) != len(set(indexes)):
            raise ValueError("adopted output indexes must be distinct")
        output_indexes_json = _json(indexes)
        assets_json = _json(normalized)
        material = _json({
            "account_id": account_id,
            "adoption_version": 1,
            "assets": normalized,
            "owner_user_id": owner_user_id,
            "task_uuid": task_uuid,
        })
        receipt_id = "autoipadopt:" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:32]
        sealed_at = _utc_timestamp(self._clock(), "sealed_at")

        try:
            with self._transaction(
                autoip_history_adoption=True,
            ) as connection:
                account = connection.execute(
                    """SELECT account.provider_id
                         FROM provider_accounts AS account
                         JOIN provider_account_owners AS owner USING(account_id)
                        WHERE account.account_id = ?
                          AND owner.owner_user_id = ?""",
                    (account_id, owner_user_id),
                ).fetchone()
                if account is None or account["provider_id"] != "autoip":
                    raise KeyError(account_id)

                task_projection_ids = {
                    str(row["projection_id"])
                    for row in connection.execute(
                        """SELECT projection.projection_id
                             FROM remote_asset_projections AS projection
                             JOIN remote_asset_owners AS owner
                               ON owner.projection_id = projection.projection_id
                              AND owner.owner_user_id = ?
                            WHERE projection.account_id = ?
                              AND json_extract(
                                  projection.metadata_json, '$.source'
                              ) = 'autoip_history'
                              AND json_extract(
                                  projection.metadata_json, '$.task_id'
                              ) = ?""",
                        (owner_user_id, account_id, task_uuid),
                    ).fetchall()
                }
                if task_projection_ids != {
                    str(item["projection_id"]) for item in normalized
                }:
                    raise StoreConflictError(
                        "AutoIP history task output set conflicts"
                    )

                for item in normalized:
                    projection_id = str(item["projection_id"])
                    asset_version_id = str(item["asset_version_id"])
                    output_index = int(item["output_index"])
                    row = connection.execute(
                        """SELECT projection.account_id,
                                  projection.external_asset_id,
                                  projection.media_kind,
                                  projection.visual_class,
                                  projection.metadata_json,
                                  account.provider_id,
                                  version.version_number,
                                  version.provider_version_id,
                                  version.mime_type,
                                  version.byte_size,
                                  version.sha256,
                                  version.metadata_json AS version_metadata_json,
                                  (SELECT COUNT(*)
                                     FROM remote_asset_versions AS candidate
                                    WHERE candidate.projection_id = projection.projection_id
                                  ) AS version_count,
                                  (SELECT COUNT(*)
                                     FROM asset_character_links AS link
                                    WHERE link.projection_id = projection.projection_id
                                  ) AS character_link_count
                             FROM remote_asset_projections AS projection
                             JOIN remote_asset_owners AS owner
                               ON owner.projection_id = projection.projection_id
                              AND owner.owner_user_id = ?
                             JOIN provider_accounts AS account
                               ON account.account_id = projection.account_id
                             JOIN remote_asset_versions AS version
                               ON version.projection_id = projection.projection_id
                              AND version.version_id = ?
                            WHERE projection.projection_id = ?
                              AND projection.account_id = ?""",
                        (
                            owner_user_id, asset_version_id, projection_id,
                            account_id,
                        ),
                    ).fetchone()
                    if row is None:
                        raise StoreConflictError(
                            "AutoIP history adoption graph is incomplete"
                        )
                    metadata = _from_json(str(row["metadata_json"]))
                    version_metadata = _from_json(
                        str(row["version_metadata_json"])
                    )
                    if (
                        not isinstance(metadata, Mapping)
                        or metadata.get("task_id") != task_uuid
                        or metadata.get("output_index") != output_index
                        or not matches_deterministic_autoip_history_identity(
                            owner_user_id,
                            provider_id=row["provider_id"],
                            account_id=row["account_id"],
                            projection_id=projection_id,
                            external_asset_id=row["external_asset_id"],
                            asset_version_id=asset_version_id,
                            provider_version_id=row["provider_version_id"],
                            metadata=metadata,
                        )
                        or not isinstance(version_metadata, Mapping)
                        or version_metadata.get("validated_download") is not True
                        or version_metadata.get("legacy_history") is not True
                        or not is_supported_autoip_history_version(
                            row["media_kind"], row["mime_type"], row["byte_size"]
                        )
                        or row["version_number"] != 1
                        or row["version_count"] != 1
                    ):
                        raise StoreConflictError(
                            "AutoIP history adoption identity conflicts"
                        )
                    _sha256(row["sha256"], "asset version sha256")

                    if row["visual_class"] in _EXECUTABLE_VISUAL_CLASSES:
                        evidence = connection.execute(
                            """SELECT * FROM asset_classification_evidence
                                WHERE owner_user_id = ? AND projection_id = ?
                                  AND state = 'active'""",
                            (owner_user_id, projection_id),
                        ).fetchone()
                        if (
                            evidence is None
                            or not self._asset_classification_graph_matches(
                                connection, evidence,
                            )
                        ):
                            raise StoreConflictError(
                                "AutoIP history classification lacks sealed evidence"
                            )
                    elif (
                        row["visual_class"] not in {None, "unknown"}
                        or row["character_link_count"] != 0
                    ):
                        raise StoreConflictError(
                            "AutoIP history adoption graph is inconsistent"
                        )

                existing = connection.execute(
                    """SELECT * FROM autoip_history_adoption_receipts
                        WHERE owner_user_id = ? AND account_id = ? AND task_uuid = ?""",
                    (owner_user_id, account_id, task_uuid),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["receipt_id"] != receipt_id
                        or existing["output_indexes_json"] != output_indexes_json
                        or existing["assets_json"] != assets_json
                        or existing["adoption_version"] != 1
                    ):
                        raise StoreConflictError(
                            "AutoIP history task adoption is already sealed"
                        )
                    replayed = True
                    sealed = existing
                else:
                    connection.execute(
                        """INSERT INTO autoip_history_adoption_receipts(
                               receipt_id, owner_user_id, account_id, task_uuid,
                               output_indexes_json, assets_json,
                               adoption_version, sealed_at
                           ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                        (
                            receipt_id, owner_user_id, account_id, task_uuid,
                            output_indexes_json, assets_json, sealed_at,
                        ),
                    )
                    sealed = connection.execute(
                        """SELECT * FROM autoip_history_adoption_receipts
                            WHERE receipt_id = ?""",
                        (receipt_id,),
                    ).fetchone()
                    if sealed is None:
                        raise ProviderFabricStoreError(
                            "AutoIP history adoption receipt was not sealed"
                        )
                    replayed = False
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error

        return {
            "receipt_id": sealed["receipt_id"],
            "account_id": sealed["account_id"],
            "task_uuid": sealed["task_uuid"],
            "output_indexes": indexes,
            "assets": normalized,
            "adoption_version": sealed["adoption_version"],
            "sealed_at": sealed["sealed_at"],
            "replayed": replayed,
        }

    def preflight_owned_autoip_history_adoption(
        self,
        owner_user_id: str,
        account_id: str,
        task_uuid: str,
        assets: Sequence[Mapping[str, object]],
    ) -> dict[str, Any]:
        """Reject task/output drift before a retry performs any DB mutation."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        if (
            not isinstance(task_uuid, str)
            or _AUTOIP_TASK_UUID_RE.fullmatch(task_uuid) is None
        ):
            raise ValueError("task_uuid must be a canonical UUID")
        if (
            not isinstance(assets, Sequence)
            or isinstance(assets, (str, bytes, bytearray))
            or not 1 <= len(assets) <= 16
        ):
            raise ValueError("assets must contain 1..16 adopted outputs")
        normalized: list[dict[str, object]] = []
        for item in assets:
            if not isinstance(item, Mapping) or set(item) != {
                "output_index", "projection_id", "asset_version_id",
            }:
                raise ValueError("adopted output identity is invalid")
            output_index = item.get("output_index")
            if type(output_index) is not int or output_index < 0:
                raise ValueError("adopted output index is invalid")
            normalized.append({
                "output_index": output_index,
                "projection_id": _stable_id(
                    item.get("projection_id"), "projection_id",
                ),
                "asset_version_id": _stable_id(
                    item.get("asset_version_id"), "asset_version_id",
                ),
            })
        normalized.sort(key=lambda item: int(item["output_index"]))
        indexes = [int(item["output_index"]) for item in normalized]
        if len(indexes) != len(set(indexes)):
            raise ValueError("adopted output indexes must be distinct")
        assets_json = _json(normalized)
        output_indexes_json = _json(indexes)
        expected_by_projection = {
            str(item["projection_id"]): item for item in normalized
        }

        with self._read_connection() as connection:
            account = connection.execute(
                """SELECT 1
                     FROM provider_accounts AS account
                     JOIN provider_account_owners AS owner USING(account_id)
                    WHERE account.account_id = ?
                      AND account.provider_id = 'autoip'
                      AND owner.owner_user_id = ?""",
                (account_id, owner_user_id),
            ).fetchone()
            if account is None:
                raise KeyError(account_id)
            receipt = connection.execute(
                """SELECT * FROM autoip_history_adoption_receipts
                    WHERE owner_user_id = ? AND account_id = ? AND task_uuid = ?""",
                (owner_user_id, account_id, task_uuid),
            ).fetchone()
            if receipt is not None and (
                receipt["output_indexes_json"] != output_indexes_json
                or receipt["assets_json"] != assets_json
                or receipt["adoption_version"] != 1
            ):
                raise StoreConflictError(
                    "AutoIP history task adoption is already sealed"
                )
            existing = connection.execute(
                """SELECT projection.projection_id, projection.metadata_json,
                          version.version_id
                     FROM remote_asset_projections AS projection
                     JOIN remote_asset_owners AS owner
                       ON owner.projection_id = projection.projection_id
                      AND owner.owner_user_id = ?
                     LEFT JOIN remote_asset_versions AS version
                       ON version.projection_id = projection.projection_id
                    WHERE projection.account_id = ?
                      AND json_extract(
                          projection.metadata_json, '$.source'
                      ) = 'autoip_history'
                      AND json_extract(
                          projection.metadata_json, '$.task_id'
                      ) = ?""",
                (owner_user_id, account_id, task_uuid),
            ).fetchall()
            for row in existing:
                expected = expected_by_projection.get(str(row["projection_id"]))
                metadata = _from_json(str(row["metadata_json"]))
                if (
                    expected is None
                    or not isinstance(metadata, Mapping)
                    or metadata.get("output_index") != expected["output_index"]
                    or (
                        row["version_id"] is not None
                        and row["version_id"] != expected["asset_version_id"]
                    )
                ):
                    raise StoreConflictError(
                        "AutoIP history task output set conflicts"
                    )
        return {
            "sealed": receipt is not None,
            "output_indexes": indexes,
            "assets": normalized,
        }

    def list_owned_autoip_history_adoptions(
        self,
        owner_user_id: str,
        account_id: str,
        *,
        after: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a bounded page of task-level adoption seals for one owner."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        account_id = _stable_id(account_id, "account_id")
        if after is not None and (
            not isinstance(after, str)
            or _AUTOIP_TASK_UUID_RE.fullmatch(after) is None
        ):
            raise ValueError("after must be a canonical task UUID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._read_connection() as connection:
            account = connection.execute(
                """SELECT 1
                     FROM provider_accounts AS account
                     JOIN provider_account_owners AS owner USING(account_id)
                    WHERE account.account_id = ?
                      AND account.provider_id = 'autoip'
                      AND owner.owner_user_id = ?""",
                (account_id, owner_user_id),
            ).fetchone()
            if account is None:
                raise KeyError(account_id)
            rows = connection.execute(
                """SELECT receipt_id, task_uuid,
                          json_array_length(output_indexes_json) AS output_count,
                          adoption_version, sealed_at
                     FROM autoip_history_adoption_receipts
                    WHERE owner_user_id = ? AND account_id = ?
                      AND (? IS NULL OR task_uuid > ?)
                    ORDER BY task_uuid
                    LIMIT ?""",
                (owner_user_id, account_id, after, after, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        return {
            "items": [dict(row) for row in visible],
            "next_after": str(visible[-1]["task_uuid"])
            if has_more and visible else None,
        }

    def get_owned_remote_asset_classification(
        self,
        owner_user_id: str,
        projection_id: str,
    ) -> dict[str, Any] | None:
        """Read only a sealed, internally consistent owner classification."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        with self._read_connection() as connection:
            owned = connection.execute(
                """SELECT 1 FROM remote_asset_owners
                    WHERE projection_id = ? AND owner_user_id = ?""",
                (projection_id, owner_user_id),
            ).fetchone()
            if owned is None:
                raise KeyError(projection_id)
            row = connection.execute(
                """SELECT * FROM asset_classification_evidence
                    WHERE projection_id = ? AND owner_user_id = ? AND state = 'active'""",
                (projection_id, owner_user_id),
            ).fetchone()
            if row is None:
                return None
            if not self._asset_classification_graph_matches(connection, row):
                raise StoreConflictError(
                    "asset classification evidence is inconsistent"
                )
            return self._public_asset_classification(row)

    def classify_owned_remote_asset(
        self,
        owner_user_id: str,
        projection_id: str,
        asset_version_id: str,
        *,
        visual_class: VisualClass | str,
        character_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Bind one unclassified visual version to an exact class and roster.

        Classification is intentionally irreversible.  It is bound to the
        latest immutable hashed version, requires 1..4 already-classified
        characters owned by the same actor, and seals the class plus links in
        one immediate transaction.  An exact replay is idempotent; any changed
        target is a conflict rather than a reclassification.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        asset_version_id = _stable_id(asset_version_id, "asset_version_id")
        visual = _visual_value(visual_class, executable=True)
        if (
            not isinstance(character_ids, Sequence)
            or isinstance(character_ids, (str, bytes, bytearray))
        ):
            raise ValueError("character_ids must be an array")
        selected = [_stable_id(item, "character_id") for item in character_ids]
        if not 1 <= len(selected) <= 4 or len(set(selected)) != len(selected):
            raise ValueError("character_ids must contain 1..4 distinct characters")
        selected = sorted(selected)
        character_ids_json = _json(selected)
        classification_material = _json({
            "asset_version_id": asset_version_id,
            "character_ids": selected,
            "classification_version": 1,
            "owner_user_id": owner_user_id,
            "projection_id": projection_id,
            "visual_class": visual,
        })
        classification_id = "assetclass:" + hashlib.sha256(
            classification_material.encode("utf-8")
        ).hexdigest()[:32]
        classified_at = _utc_timestamp(self._clock(), "classified_at")

        try:
            with self._transaction(asset_classification=True) as connection:
                owned = connection.execute(
                    """SELECT 1 FROM remote_asset_owners
                        WHERE projection_id = ? AND owner_user_id = ?""",
                    (projection_id, owner_user_id),
                ).fetchone()
                if owned is None:
                    raise KeyError(projection_id)
                existing = connection.execute(
                    """SELECT * FROM asset_classification_evidence
                        WHERE projection_id = ?""",
                    (projection_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["state"] == "active"
                        and existing["classification_id"] == classification_id
                        and existing["owner_user_id"] == owner_user_id
                        and existing["asset_version_id"] == asset_version_id
                        and existing["visual_class"] == visual
                        and existing["character_ids_json"] == character_ids_json
                        and existing["classification_version"] == 1
                    ):
                        if not self._asset_classification_graph_matches(
                            connection, existing
                        ):
                            raise StoreConflictError(
                                "asset classification evidence is inconsistent"
                            )
                        replay = self._public_asset_classification(existing)
                        replay["replayed"] = True
                        return replay
                    raise StoreConflictError(
                        "asset classification is already fixed"
                    )

                asset = connection.execute(
                    """SELECT p.account_id, p.media_kind, p.visual_class, p.external_asset_id,
                              p.metadata_json, account.provider_id,
                              version.version_number, version.provider_version_id,
                              version.mime_type, version.byte_size, version.sha256,
                              version.metadata_json AS version_metadata_json,
                              (SELECT MAX(latest.version_number)
                                 FROM remote_asset_versions AS latest
                                WHERE latest.projection_id = p.projection_id
                              ) AS latest_version_number
                         FROM remote_asset_projections AS p
                         JOIN provider_accounts AS account
                           ON account.account_id = p.account_id
                         JOIN remote_asset_owners AS owner
                           ON owner.projection_id = p.projection_id
                          AND owner.owner_user_id = ?
                         JOIN remote_asset_versions AS version
                           ON version.projection_id = p.projection_id
                          AND version.version_id = ?
                        WHERE p.projection_id = ?""",
                    (owner_user_id, asset_version_id, projection_id),
                ).fetchone()
                if asset is None:
                    raise KeyError(projection_id)
                if asset["media_kind"] not in {"image", "video"}:
                    raise ValueError("only image and video assets can be visually classified")
                metadata = _from_json(str(asset["metadata_json"]))
                version_metadata = _from_json(str(asset["version_metadata_json"]))
                if (
                    not matches_deterministic_autoip_history_identity(
                        owner_user_id,
                        provider_id=asset["provider_id"],
                        account_id=asset["account_id"],
                        projection_id=projection_id,
                        external_asset_id=asset["external_asset_id"],
                        asset_version_id=asset_version_id,
                        provider_version_id=asset["provider_version_id"],
                        metadata=metadata,
                    )
                    or not isinstance(version_metadata, Mapping)
                    or version_metadata.get("validated_download") is not True
                    or version_metadata.get("legacy_history") is not True
                    or not is_supported_autoip_history_visual_version(
                        asset["media_kind"], asset["mime_type"], asset["byte_size"]
                    )
                ):
                    raise ValueError(
                        "asset is not deterministic AutoIP history evidence"
                    )
                if asset["visual_class"] not in {None, "unknown"}:
                    raise StoreConflictError("asset visual class is already fixed")
                if asset["version_number"] != asset["latest_version_number"]:
                    raise StoreConflictError("asset version changed; reload before classifying")
                version_count = connection.execute(
                    """SELECT COUNT(*) FROM remote_asset_versions
                        WHERE projection_id = ?""",
                    (projection_id,),
                ).fetchone()[0]
                if version_count != 1:
                    raise StoreConflictError(
                        "asset history must have one exact version"
                    )
                _sha256(asset["sha256"], "asset version sha256")
                placeholders = ",".join("?" for _ in selected)
                characters = connection.execute(
                    f"""SELECT character.character_id, character.visual_class
                           FROM canonical_characters AS character
                           JOIN canonical_character_owners AS owner
                             ON owner.character_id = character.character_id
                            AND owner.owner_user_id = ?
                          WHERE character.character_id IN ({placeholders})
                          ORDER BY character.character_id""",
                    (owner_user_id, *selected),
                ).fetchall()
                if (
                    [str(row["character_id"]) for row in characters] != selected
                    or any(row["visual_class"] != visual for row in characters)
                ):
                    raise StoreConflictError(
                        "asset classification characters are unavailable or use another class"
                    )
                linked = connection.execute(
                    "SELECT 1 FROM asset_character_links WHERE projection_id = ? LIMIT 1",
                    (projection_id,),
                ).fetchone()
                if linked is not None:
                    raise StoreConflictError(
                        "asset already has character attribution"
                    )

                connection.execute(
                    """INSERT INTO asset_classification_evidence(
                           classification_id, owner_user_id, projection_id,
                           asset_version_id, visual_class, character_ids_json,
                           classification_version, state, classified_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 1, 'building', ?)""",
                    (
                        classification_id,
                        owner_user_id,
                        projection_id,
                        asset_version_id,
                        visual,
                        character_ids_json,
                        classified_at,
                    ),
                )
                connection.execute(
                    "UPDATE remote_asset_projections SET visual_class = ? WHERE projection_id = ?",
                    (visual, projection_id),
                )
                connection.executemany(
                    """INSERT INTO asset_character_links(
                           projection_id, character_id, linked_at
                       ) VALUES (?, ?, ?)""",
                    [
                        (projection_id, character_id, classified_at)
                        for character_id in selected
                    ],
                )
                connection.execute(
                    """UPDATE asset_classification_evidence
                          SET state = 'active'
                        WHERE classification_id = ? AND state = 'building'""",
                    (classification_id,),
                )
                sealed = connection.execute(
                    """SELECT * FROM asset_classification_evidence
                        WHERE classification_id = ? AND state = 'active'""",
                    (classification_id,),
                ).fetchone()
                if sealed is None:
                    raise ProviderFabricStoreError(
                        "asset classification evidence was not sealed"
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        result = self._public_asset_classification(sealed)
        result["replayed"] = False
        return result

    def put_owned_asset_catalog_annotation(
        self,
        owner_user_id: str,
        projection_id: str,
        *,
        display_name: str | None,
        audio_role: str,
        expected_revision: int,
        media_role: str = "unclassified",
    ) -> dict[str, Any]:
        """Create/update owner catalog labels without mutating provider metadata."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        projection_id = _stable_id(projection_id, "projection_id")
        display_name = _optional_text(display_name, "display_name", maximum=160)
        if audio_role not in {"unclassified", "music", "sound_effect", "voice", "ambience"}:
            raise ValueError("audio_role is unsupported")
        media_roles = {
            "image": {"unclassified", "profile", "outfit", "reference", "background", "prop", "storyboard", "finished"},
            "video": {"unclassified", "dance_template", "motion_reference", "source_clip", "finished"},
            "audio": {"unclassified"},
            "text": {"unclassified", "prompt", "script", "subtitle"},
        }
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        now = self._clock()
        try:
            with self._transaction() as connection:
                asset = connection.execute(
                    """SELECT p.media_kind
                         FROM remote_asset_projections AS p
                         JOIN remote_asset_owners AS o USING(projection_id)
                        WHERE p.projection_id = ? AND o.owner_user_id = ?""",
                    (projection_id, owner_user_id),
                ).fetchone()
                if asset is None:
                    raise KeyError(projection_id)
                if asset["media_kind"] != "audio" and audio_role != "unclassified":
                    raise ValueError("audio_role applies only to audio assets")
                if media_role not in media_roles.get(str(asset["media_kind"]), {"unclassified"}):
                    raise ValueError("media_role does not apply to this media kind")
                existing = connection.execute(
                    """SELECT revision FROM asset_catalog_annotations
                        WHERE owner_user_id = ? AND projection_id = ?""",
                    (owner_user_id, projection_id),
                ).fetchone()
                if existing is None:
                    if expected_revision != 0:
                        raise StoreConflictError("asset catalog annotation revision changed")
                    connection.execute(
                        """INSERT INTO asset_catalog_annotations(
                               owner_user_id, projection_id, display_name, audio_role,
                               media_role, revision, updated_at
                           ) VALUES (?, ?, ?, ?, ?, 1, ?)""",
                        (owner_user_id, projection_id, display_name, audio_role, media_role, now),
                    )
                else:
                    if existing["revision"] != expected_revision:
                        raise StoreConflictError("asset catalog annotation revision changed")
                    connection.execute(
                        """UPDATE asset_catalog_annotations
                              SET display_name = ?, audio_role = ?, media_role = ?,
                                  revision = revision + 1, updated_at = ?
                            WHERE owner_user_id = ? AND projection_id = ?""",
                        (display_name, audio_role, media_role, now, owner_user_id, projection_id),
                    )
                row = connection.execute(
                    """SELECT display_name, audio_role, media_role, revision, updated_at
                         FROM asset_catalog_annotations
                        WHERE owner_user_id = ? AND projection_id = ?""",
                    (owner_user_id, projection_id),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return dict(row)

    def list_owned_remote_assets(
        self,
        owner_user_id: str,
        *,
        account_id: str | None = None,
        provider_id: str | None = None,
        kind: MediaKind | str | None = None,
        visual_class: VisualClass | str | None = None,
        character_id: str | None = None,
        catalog_role: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a bounded metadata page for one exact owner only."""

        return self._list_remote_assets_page(
            _stable_id(owner_user_id, "owner_user_id"),
            account_id=account_id,
            provider_id=provider_id,
            kind=kind,
            visual_class=visual_class,
            character_id=character_id,
            catalog_role=catalog_role,
            after=after,
            limit=limit,
        )

    def list_remote_assets(
        self,
        *,
        account_id: str | None = None,
        provider_id: str | None = None,
        kind: MediaKind | str | None = None,
        visual_class: VisualClass | str | None = None,
        character_id: str | None = None,
        catalog_role: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Internal all-owner inventory retained for migration/audit tools."""

        return self._list_remote_assets_page(
            None,
            account_id=account_id,
            provider_id=provider_id,
            kind=kind,
            visual_class=visual_class,
            character_id=character_id,
            catalog_role=catalog_role,
            after=after,
            limit=limit,
        )

    def _list_remote_assets_page(
        self,
        owner_user_id: str | None,
        *,
        account_id: str | None = None,
        provider_id: str | None = None,
        kind: MediaKind | str | None = None,
        visual_class: VisualClass | str | None = None,
        character_id: str | None = None,
        catalog_role: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a bounded metadata-only remote media page.

        Provider source and preview URIs are intentionally not selected.  A
        later, authenticated fetch endpoint must resolve those private values
        server-side and enforce its own origin, MIME, redirect, and size policy.
        The cursor is an internal ``(last_seen_at, projection_id)`` pair; an
        HTTP adapter should wrap it in a versioned opaque token.
        """

        if account_id is not None:
            account_id = _stable_id(account_id, "account_id")
        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        media_kind = MediaKind(kind).value if kind is not None else None
        visual = _visual_value(visual_class) if visual_class is not None else None
        if character_id is not None:
            character_id = _stable_id(character_id, "character_id")
        if catalog_role is not None:
            catalog_role = _stable_id(catalog_role, "catalog_role")
            roles_by_kind = {
                "image": {"unclassified", "profile", "outfit", "reference", "background", "prop", "storyboard", "finished"},
                "video": {"unclassified", "dance_template", "motion_reference", "source_clip", "finished"},
                "audio": {"unclassified", "music", "sound_effect", "voice", "ambience"},
                "text": {"unclassified", "prompt", "script", "subtitle"},
            }
            if media_kind is None or catalog_role not in roles_by_kind.get(media_kind, set()):
                raise ValueError("catalog_role requires a matching media kind")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        after_seen: str | None = None
        after_id: str | None = None
        if after is not None:
            if not isinstance(after, tuple) or len(after) != 2:
                raise ValueError("after must be a (last_seen_at, projection_id) tuple")
            after_seen = _optional_text(after[0], "after last_seen_at", maximum=80)
            after_id = _stable_id(after[1], "after projection_id")

        clauses: list[str] = []
        values: list[Any] = []
        if owner_user_id is not None:
            clauses.append("o.owner_user_id = ?")
            values.append(owner_user_id)
        if account_id is not None:
            clauses.append("p.account_id = ?")
            values.append(account_id)
        if provider_id is not None:
            clauses.append("a.provider_id = ?")
            values.append(provider_id)
        if media_kind is not None:
            clauses.append("p.media_kind = ?")
            values.append(media_kind)
        if visual is not None:
            # A legacy/unclassified projection is serialized publicly as
            # ``unknown``.  Keep the filter contract identical to the visible
            # taxonomy so NULL rows cannot disappear from the unknown tab.
            clauses.append(
                "(p.visual_class = ? OR p.visual_class IS NULL)"
                if visual == VisualClass.UNKNOWN.value
                else "p.visual_class = ?"
            )
            values.append(visual)
        if character_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM asset_character_links acl "
                "WHERE acl.projection_id = p.projection_id AND acl.character_id = ?)"
            )
            values.append(character_id)
        if catalog_role is not None:
            column = "ca.audio_role" if media_kind == "audio" else "ca.media_role"
            clauses.append(f"COALESCE({column}, 'unclassified') = ?")
            values.append(catalog_role)
        if after_seen is not None and after_id is not None:
            clauses.append(
                "(p.last_seen_at < ? OR "
                "(p.last_seen_at = ? AND p.projection_id < ?))"
            )
            values.extend((after_seen, after_seen, after_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT
                       p.projection_id, p.account_id, a.provider_id,
                       p.external_asset_id, p.media_kind, p.visual_class,
                       p.metadata_json, p.remote_created_at, p.first_seen_at,
                       p.last_seen_at,
                       ca.display_name AS catalog_display_name,
                       ca.audio_role AS catalog_audio_role,
                       ca.media_role AS catalog_media_role,
                       ca.revision AS catalog_revision,
                       ca.updated_at AS catalog_updated_at,
                       (SELECT COUNT(*) FROM remote_asset_versions AS counted
                         WHERE counted.projection_id = p.projection_id
                       ) AS version_count,
                       v.version_id, v.version_number, v.provider_version_id,
                       CASE
                         WHEN a.provider_id = 'clipmode' AND a.credential_kind = 'none'
                         THEN EXISTS (
                             SELECT 1
                               FROM provider_asset_version_blobs AS original
                               JOIN provider_import_receipts AS receipt
                                 ON receipt.receipt_id = original.receipt_id
                                AND receipt.owner_user_id = original.owner_user_id
                                AND receipt.state = 'active'
                              WHERE original.owner_user_id = o.owner_user_id
                                AND original.version_id = v.version_id
                                AND original.role = 'original'
                         )
                         ELSE (v.source_uri IS NOT NULL AND length(v.source_uri) > 0)
                       END AS source_available,
                       CASE
                         WHEN a.provider_id = 'clipmode' AND a.credential_kind = 'none'
                         THEN EXISTS (
                             SELECT 1
                               FROM provider_asset_version_blobs AS preview
                               JOIN provider_import_receipts AS preview_receipt
                                 ON preview_receipt.receipt_id = preview.receipt_id
                                AND preview_receipt.owner_user_id = preview.owner_user_id
                                AND preview_receipt.state = 'active'
                               JOIN projection_preview_versions AS pinned
                                 ON pinned.owner_user_id = preview.owner_user_id
                                AND pinned.projection_id = p.projection_id
                                AND pinned.version_id = preview.version_id
                                AND pinned.rendition_sha256 = preview.sha256
                               JOIN provider_import_receipts AS pinned_receipt
                                 ON pinned_receipt.receipt_id = pinned.receipt_id
                                AND pinned_receipt.owner_user_id = pinned.owner_user_id
                                AND pinned_receipt.state = 'active'
                              WHERE preview.owner_user_id = o.owner_user_id
                                AND preview.version_id = v.version_id
                                AND preview.role = 'preview_rendition'
                         )
                         ELSE (v.preview_uri IS NOT NULL AND length(v.preview_uri) > 0)
                       END AS preview_available,
                       v.mime_type, v.byte_size, v.sha256, v.duration_seconds,
                       v.metadata_json AS version_metadata_json, v.observed_at
                   FROM remote_asset_projections AS p
                   JOIN provider_accounts AS a ON a.account_id = p.account_id
                   LEFT JOIN remote_asset_owners AS o ON o.projection_id = p.projection_id
                   LEFT JOIN asset_catalog_annotations AS ca
                     ON ca.projection_id = p.projection_id
                    AND ca.owner_user_id = o.owner_user_id
                   LEFT JOIN remote_asset_versions AS v
                     ON v.projection_id = p.projection_id
                    AND v.version_number = (
                        SELECT MAX(v2.version_number)
                        FROM remote_asset_versions AS v2
                        WHERE v2.projection_id = p.projection_id
                    )"""
                + where
                + " ORDER BY p.last_seen_at DESC, p.projection_id DESC LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
            selected = rows[:limit]
            projection_ids = [row["projection_id"] for row in selected]
            character_map: dict[str, list[str]] = {item: [] for item in projection_ids}
            if projection_ids:
                placeholders = ",".join("?" for _ in projection_ids)
                character_rows = connection.execute(
                    f"""SELECT projection_id, character_id FROM asset_character_links
                        WHERE projection_id IN ({placeholders})
                        ORDER BY projection_id, character_id""",
                    projection_ids,
                ).fetchall()
                for character in character_rows:
                    character_map[character["projection_id"]].append(character["character_id"])

        items: list[dict[str, Any]] = []
        for row in selected:
            latest_version = None
            if row["version_id"] is not None:
                latest_version = {
                    "version_id": row["version_id"],
                    "version_number": row["version_number"],
                    "provider_version_id": row["provider_version_id"],
                    "source_available": bool(row["source_available"]),
                    "preview_available": bool(row["preview_available"]),
                    "mime_type": row["mime_type"],
                    "byte_size": row["byte_size"],
                    "sha256": row["sha256"],
                    "duration_seconds": row["duration_seconds"],
                    "metadata": _from_json(row["version_metadata_json"]),
                    "observed_at": row["observed_at"],
                }
            items.append({
                "projection_id": row["projection_id"],
                "account_id": row["account_id"],
                "provider_id": row["provider_id"],
                "external_asset_id": row["external_asset_id"],
                "media_kind": row["media_kind"],
                "visual_class": row["visual_class"],
                "metadata": _from_json(row["metadata_json"]),
                "remote_created_at": row["remote_created_at"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "version_count": row["version_count"],
                "catalog_annotation": (
                    {
                        "display_name": row["catalog_display_name"],
                        "audio_role": row["catalog_audio_role"],
                        "media_role": row["catalog_media_role"],
                        "revision": row["catalog_revision"],
                        "updated_at": row["catalog_updated_at"],
                    }
                    if row["catalog_revision"] is not None else None
                ),
                "character_ids": character_map[row["projection_id"]],
                "latest_version": latest_version,
            })
        next_after = None
        if len(rows) > limit and selected:
            tail = selected[-1]
            next_after = (tail["last_seen_at"], tail["projection_id"])
        return {"items": items, "next_after": next_after}

    @staticmethod
    def _validate_intent_visual_class(intent: RouteIntent) -> None:
        intent_visual = _visual_value(intent.visual_class, executable=True)
        mismatched_characters = [
            item.character_id
            for item in intent.characters
            if _visual_value(item.visual_class) != intent_visual
        ]
        mismatched_assets = [
            item.asset_id
            for item in intent.assets
            if item.visual_class is not None and _visual_value(item.visual_class) != intent_visual
        ]
        if mismatched_characters or mismatched_assets:
            raise ValueError("intent contains a real/anime visual class mismatch")

    def create_intent(
        self,
        intent_id: str,
        request_key: str,
        intent: RouteIntent,
        *,
        owner_user_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        intent_id = _stable_id(intent_id, "intent_id")
        request_key = _stable_id(request_key, "request_key")
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if not isinstance(intent, RouteIntent):
            raise ValueError("intent must be a RouteIntent")
        self._validate_intent_visual_class(intent)
        intent_data = intent.to_dict()
        intent_data["visual_class"] = _visual_value(intent.visual_class, executable=True)
        for character in intent_data["characters"]:
            character["visual_class"] = _visual_value(character["visual_class"])
        for asset in intent_data["assets"]:
            if asset["visual_class"] is not None:
                asset["visual_class"] = _visual_value(asset["visual_class"])
        intent_json = _json(intent_data)
        payload_data = dict(payload or {})
        payload_owner = payload_data.get("owner_user_id")
        if payload_owner is not None and payload_owner != owner_user_id:
            raise ValueError("payload owner does not match owner_user_id")
        payload_data["owner_user_id"] = owner_user_id
        payload_json = _json(payload_data)
        created_at = self._clock()
        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    """SELECT generation_intents.*, generation_intent_owners.owner_user_id
                       FROM generation_intents
                       LEFT JOIN generation_intent_owners USING(intent_id)
                       WHERE generation_intents.request_key = ?""",
                    (request_key,),
                ).fetchone()
                if existing:
                    if (
                        existing["intent_json"] != intent_json
                        or existing["payload_json"] != payload_json
                        or existing["owner_user_id"] != owner_user_id
                    ):
                        raise StoreConflictError("request_key already identifies another immutable intent")
                    return self._public_intent(existing)
                connection.execute(
                    """INSERT INTO generation_intents(
                           intent_id, request_key, capability, visual_class,
                           intent_json, payload_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        intent_id,
                        request_key,
                        intent.capability.value,
                        intent_data["visual_class"],
                        intent_json,
                        payload_json,
                        created_at,
                    ),
                )
                connection.execute(
                    """INSERT INTO generation_intent_owners(
                           intent_id, owner_user_id, bound_at
                       ) VALUES (?, ?, ?)""",
                    (intent_id, owner_user_id, created_at),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_intent(intent_id)

    def _owned_confirmation_candidate(
        self,
        owner_user_id: str,
        intent_id: str,
        request_key: str,
        intent: RouteIntent,
        decision_id: str,
        decision: RouteDecision,
        selected_account_id: str,
        outbox_id: str,
        client_request_id: str,
        *,
        payload: Mapping[str, Any],
        quote: Mapping[str, Any],
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        intent_id = _stable_id(intent_id, "intent_id")
        request_key = _stable_id(request_key, "request_key")
        decision_id = _stable_id(decision_id, "decision_id")
        selected_account_id = _stable_id(
            selected_account_id, "selected_account_id"
        )
        outbox_id = _stable_id(outbox_id, "outbox_id")
        client_request_id = _stable_id(client_request_id, "client_request_id")
        if not isinstance(intent, RouteIntent):
            raise ValueError("intent must be a RouteIntent")
        if not isinstance(decision, RouteDecision):
            raise ValueError("decision must be a RouteDecision")
        if decision.selected_provider_id is None or decision.selected_variant_id is None:
            raise ValueError("confirmed route must select a provider variant")
        self._validate_intent_visual_class(intent)

        intent_data = intent.to_dict()
        intent_data["visual_class"] = _visual_value(intent.visual_class, executable=True)
        for character in intent_data["characters"]:
            character["visual_class"] = _visual_value(character["visual_class"])
        for asset in intent_data["assets"]:
            if asset["visual_class"] is not None:
                asset["visual_class"] = _visual_value(asset["visual_class"])
        intent_json = _json(intent_data)

        payload_data = dict(payload)
        payload_owner = payload_data.get("owner_user_id")
        if payload_owner is not None and payload_owner != owner_user_id:
            raise ValueError("payload owner does not match owner_user_id")
        payload_data["owner_user_id"] = owner_user_id
        payload_json = _json(payload_data)
        request_payload_json = _json(dict(request_payload))
        quote_data = dict(quote)
        if quote_data.get("provider_id") != decision.selected_provider_id:
            raise ValueError("quote provider does not match selected route")
        if quote_data.get("capability") != intent.capability.value:
            raise ValueError("quote capability does not match intent")
        if quote_data.get("account_id") != selected_account_id:
            raise ValueError("quote account does not match selected route")
        quoted_credits = quote_data.get("credits")
        if (
            not isinstance(quoted_credits, int)
            or isinstance(quoted_credits, bool)
            or quoted_credits < 0
        ):
            raise ValueError("quote credits must be a non-negative integer")
        quote_json = _json(quote_data)
        candidate_keys_json = _json(list(decision.candidate_keys))
        rejections_json = _json([item.to_dict() for item in decision.rejections])
        return {
            "owner_user_id": owner_user_id,
            "intent_id": intent_id,
            "request_key": request_key,
            "decision_id": decision_id,
            "selected_account_id": selected_account_id,
            "outbox_id": outbox_id,
            "client_request_id": client_request_id,
            "intent_json": intent_json,
            "payload_json": payload_json,
            "request_payload_json": request_payload_json,
            "quote_json": quote_json,
            "candidate_keys_json": candidate_keys_json,
            "rejections_json": rejections_json,
        }

    def find_exact_owned_confirmation(
        self,
        owner_user_id: str,
        intent_id: str,
        request_key: str,
        intent: RouteIntent,
        decision_id: str,
        decision: RouteDecision,
        selected_account_id: str,
        outbox_id: str,
        client_request_id: str,
        *,
        payload: Mapping[str, Any],
        quote: Mapping[str, Any],
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return an exact durable confirmation without consulting mutable state.

        No matching identifiers means this is a new confirmation and returns
        ``None``.  Any partial bundle or immutable mismatch is a conflict; it
        must never fall through into a second persistence attempt.
        """

        candidate = self._owned_confirmation_candidate(
            owner_user_id,
            intent_id,
            request_key,
            intent,
            decision_id,
            decision,
            selected_account_id,
            outbox_id,
            client_request_id,
            payload=payload,
            quote=quote,
            request_payload=request_payload,
        )
        with self._read_connection() as connection:
            intents = connection.execute(
                """SELECT i.*, o.owner_user_id
                     FROM generation_intents AS i
                     LEFT JOIN generation_intent_owners AS o USING(intent_id)
                    WHERE i.intent_id = ? OR i.request_key = ?""",
                (candidate["intent_id"], candidate["request_key"]),
            ).fetchall()
            decisions = connection.execute(
                """SELECT * FROM route_decisions
                    WHERE decision_id = ? OR intent_id = ?""",
                (candidate["decision_id"], candidate["intent_id"]),
            ).fetchall()
            outboxes = connection.execute(
                """SELECT * FROM submission_outbox
                    WHERE outbox_id = ? OR intent_id = ?
                       OR (account_id = ? AND client_request_id = ?)""",
                (
                    candidate["outbox_id"],
                    candidate["intent_id"],
                    candidate["selected_account_id"],
                    candidate["client_request_id"],
                ),
            ).fetchall()
            if not intents and not decisions and not outboxes:
                return None
            if len(intents) != 1 or len(decisions) != 1 or len(outboxes) != 1:
                raise StoreConflictError(
                    "confirmation has an incomplete or ambiguous historical persistence bundle"
                )

            stored_intent = intents[0]
            expected_intent = (
                candidate["intent_id"],
                candidate["request_key"],
                intent.capability.value,
                _visual_value(intent.visual_class, executable=True),
                candidate["intent_json"],
                candidate["payload_json"],
                candidate["owner_user_id"],
            )
            observed_intent = tuple(
                stored_intent[key]
                for key in (
                    "intent_id",
                    "request_key",
                    "capability",
                    "visual_class",
                    "intent_json",
                    "payload_json",
                    "owner_user_id",
                )
            )
            stored_decision = decisions[0]
            expected_decision = (
                candidate["decision_id"],
                candidate["intent_id"],
                candidate["selected_account_id"],
                decision.selected_provider_id,
                decision.selected_variant_id,
                decision.policy_version,
                candidate["candidate_keys_json"],
                candidate["rejections_json"],
                candidate["quote_json"],
                candidate["owner_user_id"],
            )
            observed_decision = tuple(
                stored_decision[key]
                for key in (
                    "decision_id",
                    "intent_id",
                    "selected_account_id",
                    "selected_provider_id",
                    "selected_variant_id",
                    "policy_version",
                    "candidate_keys_json",
                    "rejections_json",
                    "quote_json",
                    "actor_user_id",
                )
            )
            stored_outbox = outboxes[0]
            expected_outbox = (
                candidate["outbox_id"],
                candidate["intent_id"],
                candidate["decision_id"],
                candidate["selected_account_id"],
                candidate["client_request_id"],
                candidate["request_payload_json"],
                candidate["owner_user_id"],
            )
            observed_outbox = tuple(
                stored_outbox[key]
                for key in (
                    "outbox_id",
                    "intent_id",
                    "decision_id",
                    "account_id",
                    "client_request_id",
                    "request_payload_json",
                    "actor_user_id",
                )
            )
            if (
                observed_intent != expected_intent
                or observed_decision != expected_decision
                or observed_outbox != expected_outbox
            ):
                raise StoreConflictError(
                    "confirmation identifies different immutable route data"
                )

            return {
                "intent": self._public_intent(stored_intent),
                "decision": {
                    "decision_id": stored_decision["decision_id"],
                    "intent_id": stored_decision["intent_id"],
                    "selected_account_id": stored_decision["selected_account_id"],
                    "selected_provider_id": stored_decision["selected_provider_id"],
                    "selected_variant_id": stored_decision["selected_variant_id"],
                    "policy_version": stored_decision["policy_version"],
                    "candidate_keys": _from_json(stored_decision["candidate_keys_json"]),
                    "rejections": _from_json(stored_decision["rejections_json"]),
                    "quote": _from_json(stored_decision["quote_json"]),
                    "created_at": stored_decision["created_at"],
                },
                "outbox": {
                    "outbox_id": stored_outbox["outbox_id"],
                    "intent_id": stored_outbox["intent_id"],
                    "decision_id": stored_outbox["decision_id"],
                    "account_id": stored_outbox["account_id"],
                    "client_request_id": stored_outbox["client_request_id"],
                    "state": stored_outbox["state"],
                    "attempt_count": stored_outbox["attempt_count"],
                    "leased": bool(stored_outbox["lease_owner"]),
                    "created_at": stored_outbox["created_at"],
                    "updated_at": stored_outbox["updated_at"],
                },
            }

    def persist_owned_confirmation(
        self,
        owner_user_id: str,
        intent_id: str,
        request_key: str,
        intent: RouteIntent,
        decision_id: str,
        decision: RouteDecision,
        selected_account_id: str,
        outbox_id: str,
        client_request_id: str,
        *,
        payload: Mapping[str, Any],
        quote: Mapping[str, Any],
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist the confirmed intent, route, and outbox in one transaction.

        Validation or insertion failure rolls the whole bundle back.  A replay
        succeeds only when all three immutable rows already exist and exactly
        match; a historical partial bundle is reported as a conflict and is
        never completed implicitly.
        """

        candidate = self._owned_confirmation_candidate(
            owner_user_id,
            intent_id,
            request_key,
            intent,
            decision_id,
            decision,
            selected_account_id,
            outbox_id,
            client_request_id,
            payload=payload,
            quote=quote,
            request_payload=request_payload,
        )
        owner_user_id = candidate["owner_user_id"]
        intent_id = candidate["intent_id"]
        request_key = candidate["request_key"]
        decision_id = candidate["decision_id"]
        selected_account_id = candidate["selected_account_id"]
        outbox_id = candidate["outbox_id"]
        client_request_id = candidate["client_request_id"]
        intent_json = candidate["intent_json"]
        payload_json = candidate["payload_json"]
        request_payload_json = candidate["request_payload_json"]
        quote_json = candidate["quote_json"]
        candidate_keys_json = candidate["candidate_keys_json"]
        rejections_json = candidate["rejections_json"]
        now = self._clock()
        try:
            with self._transaction(outbox_insert=True) as connection:
                account = connection.execute(
                    """SELECT a.provider_id, a.enabled, a.credential_kind,
                              a.credential_state
                         FROM provider_accounts AS a
                         JOIN provider_account_owners AS o USING(account_id)
                         JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ? AND (
                            (e.access_scope = 'private' AND o.owner_user_id = ?
                             AND a.provider_id != 'local_comfy')
                            OR
                            (e.access_scope = 'authenticated_users'
                             AND o.owner_user_id = ?
                             AND a.provider_id = 'local_comfy')
                        )""",
                    (selected_account_id, owner_user_id, SYSTEM_LOCAL_OWNER_ID),
                ).fetchone()
                if account is None:
                    raise StoreConflictError("provider account is unavailable")
                if account["provider_id"] != decision.selected_provider_id:
                    raise StoreConflictError("selected provider account changed")
                if (
                    account["provider_id"] != "local_comfy"
                    and (
                        account["credential_kind"] == "none"
                        or account["credential_state"] != "configured"
                    )
                ):
                    raise StoreConflictError("provider credential is unavailable")

                existing_intents = connection.execute(
                    """SELECT i.*, o.owner_user_id
                         FROM generation_intents AS i
                         LEFT JOIN generation_intent_owners AS o USING(intent_id)
                        WHERE i.intent_id = ? OR i.request_key = ?""",
                    (intent_id, request_key),
                ).fetchall()
                if existing_intents:
                    if len(existing_intents) != 1:
                        raise StoreConflictError(
                            "confirmation identifiers describe different intents"
                        )
                    existing_intent = existing_intents[0]
                    if (
                        existing_intent["intent_id"] != intent_id
                        or existing_intent["request_key"] != request_key
                        or existing_intent["intent_json"] != intent_json
                        or existing_intent["payload_json"] != payload_json
                        or existing_intent["owner_user_id"] != owner_user_id
                    ):
                        raise StoreConflictError(
                            "confirmation identifies different immutable intent data"
                        )
                    stored_decision = connection.execute(
                        "SELECT * FROM route_decisions WHERE decision_id = ?",
                        (decision_id,),
                    ).fetchone()
                    stored_outbox = connection.execute(
                        "SELECT * FROM submission_outbox WHERE outbox_id = ?",
                        (outbox_id,),
                    ).fetchone()
                    if stored_decision is None or stored_outbox is None:
                        raise StoreConflictError(
                            "confirmation has an incomplete historical persistence bundle"
                        )
                    expected_decision = (
                        intent_id,
                        selected_account_id,
                        decision.selected_provider_id,
                        decision.selected_variant_id,
                        decision.policy_version,
                        candidate_keys_json,
                        rejections_json,
                        quote_json,
                        owner_user_id,
                    )
                    observed_decision = tuple(
                        stored_decision[key]
                        for key in (
                            "intent_id",
                            "selected_account_id",
                            "selected_provider_id",
                            "selected_variant_id",
                            "policy_version",
                            "candidate_keys_json",
                            "rejections_json",
                            "quote_json",
                            "actor_user_id",
                        )
                    )
                    expected_outbox = (
                        intent_id,
                        decision_id,
                        selected_account_id,
                        client_request_id,
                        request_payload_json,
                        owner_user_id,
                    )
                    observed_outbox = tuple(
                        stored_outbox[key]
                        for key in (
                            "intent_id",
                            "decision_id",
                            "account_id",
                            "client_request_id",
                            "request_payload_json",
                            "actor_user_id",
                        )
                    )
                    if (
                        observed_decision != expected_decision
                        or observed_outbox != expected_outbox
                    ):
                        raise StoreConflictError(
                            "confirmation identifies different immutable route data"
                        )
                else:
                    if not bool(account["enabled"]):
                        raise StoreConflictError("provider account is disabled")
                    connection.execute(
                        """INSERT INTO generation_intents(
                               intent_id, request_key, capability, visual_class,
                               intent_json, payload_json, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            intent_id,
                            request_key,
                            intent.capability.value,
                            _visual_value(intent.visual_class, executable=True),
                            intent_json,
                            payload_json,
                            now,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO generation_intent_owners(
                               intent_id, owner_user_id, bound_at
                           ) VALUES (?, ?, ?)""",
                        (intent_id, owner_user_id, now),
                    )
                    connection.execute(
                        """INSERT INTO route_decisions(
                               decision_id, intent_id, selected_account_id,
                               selected_provider_id, selected_variant_id, policy_version,
                               candidate_keys_json, rejections_json, quote_json, created_at,
                               actor_user_id
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            decision_id,
                            intent_id,
                            selected_account_id,
                            decision.selected_provider_id,
                            decision.selected_variant_id,
                            decision.policy_version,
                            candidate_keys_json,
                            rejections_json,
                            quote_json,
                            now,
                            owner_user_id,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO submission_outbox(
                               outbox_id, intent_id, decision_id, account_id,
                               client_request_id, state, request_payload_json,
                               attempt_count, created_at, updated_at, actor_user_id
                           ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?)""",
                        (
                            outbox_id,
                            intent_id,
                            decision_id,
                            selected_account_id,
                            client_request_id,
                            request_payload_json,
                            now,
                            now,
                            owner_user_id,
                        ),
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return {
            "intent": self.get_intent(intent_id),
            "decision": self.get_route_decision(decision_id),
            "outbox": self.get_outbox(outbox_id),
        }

    @staticmethod
    def _public_intent(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "intent_id": row["intent_id"],
            "request_key": row["request_key"],
            "capability": row["capability"],
            "visual_class": row["visual_class"],
            "intent": _from_json(row["intent_json"]),
            "payload": _from_json(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        intent_id = _stable_id(intent_id, "intent_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM generation_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return self._public_intent(row)

    def get_intent_status(self, intent_id: str, owner_user_id: str) -> dict[str, Any]:
        """Return one owner-scoped, transport-free confirmed-intent snapshot.

        Ownership is part of the SQL predicate so an unowned legacy intent, a
        cross-user lookup, and a missing identifier are indistinguishable.  The
        result intentionally omits request payloads, provider task identifiers,
        account identifiers, event details, and all credential references.
        """

        supplied_intent_id = intent_id
        intent_id = _stable_id(intent_id, "intent_id")
        if intent_id != supplied_intent_id or not _PUBLIC_INTENT_ID_RE.fullmatch(intent_id):
            raise KeyError(intent_id)
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT
                       generation_intents.intent_id,
                       generation_intents.capability,
                       generation_intents.visual_class,
                       generation_intents.created_at,
                       route_decisions.selected_provider_id AS provider_id,
                       route_decisions.quote_json,
                       submission_outbox.state AS outbox_state,
                       submission_outbox.updated_at AS outbox_updated_at,
                       external_tasks.task_id AS internal_task_id,
                       external_tasks.state AS task_state,
                       external_tasks.external_task_id AS internal_external_task_id,
                       external_tasks.credits_charged,
                       external_tasks.remaining_credits,
                       external_tasks.updated_at AS task_updated_at,
                       EXISTS (
                           SELECT 1
                             FROM remote_asset_projections AS output_projection
                             JOIN remote_asset_owners AS output_owner
                               ON output_owner.projection_id = output_projection.projection_id
                              AND output_owner.owner_user_id =
                                  generation_intent_owners.owner_user_id
                             JOIN remote_asset_versions AS output_version
                               ON output_version.projection_id =
                                  output_projection.projection_id
                            WHERE output_projection.account_id = submission_outbox.account_id
                              AND external_tasks.state = 'completed'
                              AND json_extract(
                                    output_projection.metadata_json, '$.source'
                                  ) = 'autoip_generation'
                              AND json_extract(
                                    output_projection.metadata_json, '$.intent_id'
                                  ) = generation_intents.intent_id
                              AND json_extract(
                                    output_projection.metadata_json, '$.task_id'
                                  ) = external_tasks.task_id
                              AND json_extract(
                                    output_version.metadata_json, '$.validated_download'
                                  ) = 1
                              AND json_type(
                                    output_version.metadata_json, '$.validated_download'
                                  ) = 'true'
                              AND output_version.source_uri LIKE 'file:%'
                              AND output_version.byte_size > 0
                              AND output_version.sha256 IS NOT NULL
                              AND length(output_version.sha256) = 64
                              AND output_version.sha256 NOT GLOB '*[^0-9a-f]*'
                              AND (
                                  (
                                      output_projection.media_kind = 'image'
                                      AND output_version.mime_type IN (
                                          'image/avif', 'image/gif', 'image/jpeg',
                                          'image/png', 'image/webp'
                                      )
                                  ) OR (
                                      output_projection.media_kind = 'video'
                                      AND output_version.mime_type IN (
                                          'video/mp4', 'video/quicktime', 'video/webm'
                                      )
                                  ) OR (
                                      output_projection.media_kind = 'audio'
                                      AND output_version.mime_type IN (
                                          'audio/mpeg', 'audio/wav', 'audio/x-wav',
                                          'audio/flac', 'audio/mp4', 'audio/ogg'
                                      )
                                  )
                              )
                       ) AS output_verified
                   FROM generation_intent_owners
                   JOIN generation_intents USING(intent_id)
                   JOIN submission_outbox USING(intent_id)
                   JOIN route_decisions
                     ON route_decisions.decision_id = submission_outbox.decision_id
                    AND route_decisions.intent_id = generation_intents.intent_id
                   LEFT JOIN external_tasks
                     ON external_tasks.outbox_id = submission_outbox.outbox_id
                   WHERE generation_intent_owners.intent_id = ?
                     AND generation_intent_owners.owner_user_id = ?
                   LIMIT 2""",
                (intent_id, owner_user_id),
            ).fetchall()
        if not rows:
            raise KeyError(intent_id)
        if len(rows) != 1:
            raise ProviderFabricStoreError("intent status has ambiguous submissions")
        row = rows[0]

        outbox_state = str(row["outbox_state"])
        task_state = row["task_state"]
        if task_state is not None:
            task_state = str(task_state)
        task_exists = row["internal_task_id"] is not None
        receipt_available = row["internal_external_task_id"] is not None
        terminal_states = frozenset({"completed", "failed", "cancelled", "timeout"})

        if outbox_state == "abandoned" and task_state == "cancelled":
            state = task_state
        elif outbox_state == "abandoned" and not task_exists:
            state = "abandoned"
        elif outbox_state == "pending" and not task_exists:
            state = "pending"
        elif outbox_state == "claimed" and task_state == "created":
            state = "preparing"
        elif outbox_state == "submission_uncertain" and task_state == "submission_uncertain":
            state = "submission_uncertain"
        elif outbox_state == "submitted" and task_state in {"submitted", "queued", "running"}:
            state = task_state
        elif outbox_state == "done" and task_state in terminal_states:
            state = task_state
        else:
            raise ProviderFabricStoreError("intent status state is inconsistent")

        try:
            quote = _from_json(row["quote_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ProviderFabricStoreError("intent status quote is invalid") from error
        if not isinstance(quote, Mapping):
            raise ProviderFabricStoreError("intent status quote is invalid")
        if quote.get("provider_id") != row["provider_id"]:
            raise ProviderFabricStoreError("intent status quote provider is invalid")
        if quote.get("capability") != row["capability"]:
            raise ProviderFabricStoreError("intent status quote capability is invalid")
        credits_authorized = quote.get("credits")
        if (
            not isinstance(credits_authorized, int)
            or isinstance(credits_authorized, bool)
            or credits_authorized < 0
        ):
            raise ProviderFabricStoreError("intent status authorized credits are invalid")
        credits_charged = row["credits_charged"]
        remaining_credits = row["remaining_credits"]

        created_at = row["created_at"]
        updated_at = row["task_updated_at"] or row["outbox_updated_at"]
        provider_id = row["provider_id"]
        if (
            not isinstance(created_at, str)
            or not created_at
            or len(created_at) > 80
            or "\x00" in created_at
            or not isinstance(updated_at, str)
            or not updated_at
            or len(updated_at) > 80
            or "\x00" in updated_at
            or not isinstance(provider_id, str)
            or not provider_id
        ):
            raise ProviderFabricStoreError("intent status identity is invalid")

        terminal = state in terminal_states or state == "abandoned"
        return {
            "intent_id": str(row["intent_id"]),
            "provider_id": provider_id,
            "capability": str(row["capability"]),
            "visual_class": str(row["visual_class"]),
            "state": state,
            "terminal": terminal,
            "retry_available": False,
            # Once a worker owns an outbox row, a concurrent socket write may
            # already be in progress even before a provider receipt is durable.
            "transport_may_have_occurred": task_exists,
            "provider_receipt_available": receipt_available,
            # Adoption rehashes the downloaded file before recording an
            # immutable version.  Report verification only for that exact
            # same-owner account/intent/task graph.
            "output_verified": bool(row["output_verified"]),
            "credits_authorized": credits_authorized,
            "credits_charged": credits_charged,
            "remaining_credits": remaining_credits,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def list_owned_production_intent_statuses(
        self, owner_user_id: str, project_id: str, *, limit: int = 400,
    ) -> dict[str, Any]:
        """List durable intent snapshots bound to one owner production."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        project_id = _stable_id(project_id, "project_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 400:
            raise ValueError("production intent page limit is invalid")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT generation_intents.intent_id, generation_intents.payload_json,
                          generation_intent_owners.bound_at
                     FROM generation_intent_owners
                     JOIN generation_intents USING(intent_id)
                    WHERE generation_intent_owners.owner_user_id = ?
                      AND json_extract(
                            generation_intents.payload_json,
                            '$.creative_instruction.production_binding.project_id'
                          ) = ?
                    ORDER BY generation_intent_owners.bound_at DESC,
                             generation_intents.intent_id DESC
                    LIMIT ?""",
                (owner_user_id, project_id, limit + 1),
            ).fetchall()
        truncated = len(rows) > limit
        items = []
        for row in rows[:limit]:
            try:
                payload = _from_json(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as error:
                raise ProviderFabricStoreError("production intent payload is invalid") from error
            instruction = payload.get("creative_instruction") if isinstance(payload, Mapping) else None
            binding = instruction.get("production_binding") if isinstance(instruction, Mapping) else None
            if (
                not isinstance(binding, Mapping)
                or set(binding) != {
                    "schema_version", "project_id", "pack_hash", "state_hash",
                    "shot_id", "route_variant_id",
                }
                or binding.get("schema_version") != "castle.production-binding.v1"
                or binding.get("project_id") != project_id
            ):
                raise ProviderFabricStoreError("production intent binding is invalid")
            items.append({
                "binding": dict(binding),
                "status": self.get_intent_status(str(row["intent_id"]), owner_user_id),
            })
        return {"items": items, "truncated": truncated}

    def record_route_decision(
        self,
        decision_id: str,
        intent_id: str,
        decision: RouteDecision,
        *,
        selected_account_id: str | None = None,
        quote: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision_id = _stable_id(decision_id, "decision_id")
        intent_id = _stable_id(intent_id, "intent_id")
        if not isinstance(decision, RouteDecision):
            raise ValueError("decision must be a RouteDecision")
        selected_account_id = (
            _stable_id(selected_account_id, "selected_account_id")
            if selected_account_id
            else None
        )
        quote_json = _json(dict(quote)) if quote is not None else None
        try:
            with self._transaction() as connection:
                lane = connection.execute(
                    """SELECT io.owner_user_id AS intent_owner,
                              ao.owner_user_id AS account_owner,
                              entitlement.access_scope
                         FROM generation_intents AS intent
                         LEFT JOIN generation_intent_owners AS io USING(intent_id)
                         LEFT JOIN provider_account_owners AS ao
                           ON ao.account_id = ?
                         LEFT JOIN provider_account_entitlements AS entitlement
                           ON entitlement.account_id = ?
                        WHERE intent.intent_id = ?""",
                    (selected_account_id, selected_account_id, intent_id),
                ).fetchone()
                if lane is None:
                    raise KeyError(intent_id)
                if (
                    lane["intent_owner"] is not None
                    or lane["account_owner"] is not None
                    or lane["access_scope"] is not None
                ):
                    raise StoreConflictError(
                        "owned execution requires actor-scoped confirmation"
                    )
                connection.execute(
                    """INSERT INTO route_decisions(
                           decision_id, intent_id, selected_account_id,
                           selected_provider_id, selected_variant_id, policy_version,
                           candidate_keys_json, rejections_json, quote_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision_id,
                        intent_id,
                        selected_account_id,
                        decision.selected_provider_id,
                        decision.selected_variant_id,
                        decision.policy_version,
                        _json(list(decision.candidate_keys)),
                        _json([item.to_dict() for item in decision.rejections]),
                        quote_json,
                        self._clock(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_route_decision(decision_id)

    def get_route_decision(self, decision_id: str) -> dict[str, Any]:
        decision_id = _stable_id(decision_id, "decision_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM route_decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return {
            "decision_id": row["decision_id"],
            "intent_id": row["intent_id"],
            "selected_account_id": row["selected_account_id"],
            "selected_provider_id": row["selected_provider_id"],
            "selected_variant_id": row["selected_variant_id"],
            "policy_version": row["policy_version"],
            "candidate_keys": _from_json(row["candidate_keys_json"]),
            "rejections": _from_json(row["rejections_json"]),
            "quote": _from_json(row["quote_json"]) if row["quote_json"] else None,
            "created_at": row["created_at"],
        }

    def enqueue_submission(
        self,
        outbox_id: str,
        intent_id: str,
        decision_id: str,
        account_id: str,
        client_request_id: str,
        *,
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        outbox_id = _stable_id(outbox_id, "outbox_id")
        intent_id = _stable_id(intent_id, "intent_id")
        decision_id = _stable_id(decision_id, "decision_id")
        account_id = _stable_id(account_id, "account_id")
        client_request_id = _stable_id(client_request_id, "client_request_id")
        payload_json = _json(dict(request_payload))
        now = self._clock()
        try:
            with self._transaction(outbox_insert=True) as connection:
                lane = connection.execute(
                    """SELECT io.owner_user_id AS intent_owner,
                              ao.owner_user_id AS account_owner,
                              entitlement.access_scope,
                              decision.actor_user_id
                         FROM generation_intents AS intent
                         LEFT JOIN generation_intent_owners AS io USING(intent_id)
                         LEFT JOIN route_decisions AS decision
                           ON decision.decision_id = ?
                         LEFT JOIN provider_account_owners AS ao
                           ON ao.account_id = ?
                         LEFT JOIN provider_account_entitlements AS entitlement
                           ON entitlement.account_id = ?
                        WHERE intent.intent_id = ?""",
                    (decision_id, account_id, account_id, intent_id),
                ).fetchone()
                if lane is None:
                    raise KeyError(intent_id)
                if any(
                    lane[key] is not None
                    for key in (
                        "intent_owner",
                        "account_owner",
                        "access_scope",
                        "actor_user_id",
                    )
                ):
                    raise StoreConflictError(
                        "owned execution requires actor-scoped confirmation"
                    )
                connection.execute(
                    """INSERT INTO submission_outbox(
                           outbox_id, intent_id, decision_id, account_id,
                           client_request_id, state, request_payload_json,
                           attempt_count, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)""",
                    (
                        outbox_id,
                        intent_id,
                        decision_id,
                        account_id,
                        client_request_id,
                        payload_json,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_outbox(outbox_id)

    def get_outbox(self, outbox_id: str) -> dict[str, Any]:
        outbox_id = _stable_id(outbox_id, "outbox_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM submission_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
        if row is None:
            raise KeyError(outbox_id)
        return {
            "outbox_id": row["outbox_id"],
            "intent_id": row["intent_id"],
            "decision_id": row["decision_id"],
            "account_id": row["account_id"],
            "client_request_id": row["client_request_id"],
            "state": row["state"],
            "attempt_count": row["attempt_count"],
            "leased": bool(row["lease_owner"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_pending_owned_submissions(
        self,
        *,
        provider_id: str | None = None,
        variant_ids: Sequence[str] | None = None,
        limit: int = 16,
    ) -> tuple[dict[str, str], ...]:
        """Return bounded actor-owned work locators without request payloads.

        This is a worker discovery primitive, not a claim.  The subsequent
        ``claim_owned_submission`` transaction remains the authority and may
        reject a locator lost to another worker.
        """

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        normalized_variants = tuple(
            _stable_id(item, "variant_id") for item in (variant_ids or ())
        )
        if variant_ids is not None and (
            isinstance(variant_ids, (str, bytes))
            or not normalized_variants
            or len(normalized_variants) > 32
            or len(normalized_variants) != len(set(normalized_variants))
        ):
            raise ValueError("variant_ids must contain 1 through 32 unique variants")
        variant_clause = ""
        parameters: list[Any] = [provider_id, provider_id]
        if normalized_variants:
            variant_clause = (
                " AND decision.selected_variant_id IN ("
                + ",".join("?" for _ in normalized_variants)
                + ")"
            )
            parameters.extend(normalized_variants)
        parameters.extend((SYSTEM_LOCAL_OWNER_ID, limit))
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT outbox.outbox_id, owner.owner_user_id
                     FROM submission_outbox AS outbox
                     JOIN generation_intent_owners AS owner USING(intent_id)
                     JOIN route_decisions AS decision USING(decision_id)
                     JOIN provider_accounts AS account
                       ON account.account_id = outbox.account_id
                     JOIN provider_account_entitlements AS entitlement
                       ON entitlement.account_id = outbox.account_id
                     JOIN provider_account_owners AS account_owner
                       ON account_owner.account_id = outbox.account_id
                    WHERE outbox.state = 'pending'
                      AND (? IS NULL OR account.provider_id = ?)
                      {variant_clause}
                      AND outbox.actor_user_id = owner.owner_user_id
                      AND account.enabled = 1
                      AND (
                           account.provider_id != 'autoip'
                           OR NOT EXISTS (
                               SELECT 1
                                 FROM external_tasks AS active_task
                                WHERE active_task.account_id = outbox.account_id
                                  AND active_task.state IN (
                                      'created', 'submitted', 'queued', 'running'
                                  )
                           )
                      )
                      AND (
                           (entitlement.access_scope = 'private'
                            AND account.provider_id != 'local_comfy'
                            AND account_owner.owner_user_id = owner.owner_user_id)
                        OR (entitlement.access_scope = 'authenticated_users'
                            AND account.provider_id = 'local_comfy'
                            AND account_owner.owner_user_id = ?)
                      )
                    ORDER BY outbox.created_at, outbox.outbox_id
                    LIMIT ?""".format(variant_clause=variant_clause),
                tuple(parameters),
            ).fetchall()
        return tuple({
            "outbox_id": str(row["outbox_id"]),
            "owner_user_id": str(row["owner_user_id"]),
        } for row in rows)

    def list_deferred_owned_submissions(
        self,
        *,
        provider_id: str | None = None,
        variant_ids: Sequence[str] | None = None,
        limit: int = 16,
    ) -> tuple[dict[str, str], ...]:
        """Discover definite 429 retries whose provider delay has elapsed.

        Only an explicit, durably recorded provider rejection is retryable.
        A process that disappeared while request bytes may have been in flight
        never writes this marker and therefore cannot enter this lane.
        """

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        normalized_variants = tuple(
            _stable_id(item, "variant_id") for item in (variant_ids or ())
        )
        if variant_ids is not None and (
            isinstance(variant_ids, (str, bytes))
            or not normalized_variants
            or len(normalized_variants) > 32
            or len(normalized_variants) != len(set(normalized_variants))
        ):
            raise ValueError("variant_ids must contain 1 through 32 unique variants")
        variant_clause = ""
        parameters: list[Any] = [self._clock(), provider_id, provider_id]
        if normalized_variants:
            variant_clause = (
                " AND decision.selected_variant_id IN ("
                + ",".join("?" for _ in normalized_variants)
                + ")"
            )
            parameters.extend(normalized_variants)
        parameters.extend((SYSTEM_LOCAL_OWNER_ID, limit))
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT outbox.outbox_id, task.task_id, owner.owner_user_id
                     FROM submission_outbox AS outbox
                     JOIN external_tasks AS task USING(outbox_id)
                     JOIN generation_intent_owners AS owner USING(intent_id)
                     JOIN route_decisions AS decision USING(decision_id)
                     JOIN provider_accounts AS account
                       ON account.account_id = outbox.account_id
                     JOIN provider_account_entitlements AS entitlement
                       ON entitlement.account_id = outbox.account_id
                     JOIN provider_account_owners AS account_owner
                       ON account_owner.account_id = outbox.account_id
                    WHERE outbox.state = 'claimed'
                      AND task.state = 'created'
                      AND task.external_task_id IS NULL
                      AND task.detail LIKE 'deferred_provider_rejection:%'
                      AND outbox.lease_owner IS NULL
                      AND outbox.lease_expires_at IS NOT NULL
                      AND outbox.lease_expires_at <= ?
                      AND (? IS NULL OR account.provider_id = ?)
                      {variant_clause}
                      AND outbox.actor_user_id = owner.owner_user_id
                      AND task.actor_user_id = owner.owner_user_id
                      AND account.enabled = 1
                      AND (
                           (entitlement.access_scope = 'private'
                            AND account.provider_id != 'local_comfy'
                            AND account_owner.owner_user_id = owner.owner_user_id)
                        OR (entitlement.access_scope = 'authenticated_users'
                            AND account.provider_id = 'local_comfy'
                            AND account_owner.owner_user_id = ?)
                      )
                    ORDER BY outbox.lease_expires_at, outbox.created_at,
                             outbox.outbox_id
                    LIMIT ?""".format(variant_clause=variant_clause),
                tuple(parameters),
            ).fetchall()
        return tuple({
            "outbox_id": str(row["outbox_id"]),
            "task_id": str(row["task_id"]),
            "owner_user_id": str(row["owner_user_id"]),
        } for row in rows)

    def list_owned_reconcilable_tasks(
        self,
        *,
        provider_id: str | None = None,
        account_id: str | None = None,
        external_task_id: str | None = None,
        limit: int = 16,
    ) -> tuple[dict[str, str], ...]:
        """Discover bounded submitted work without exposing provider identifiers.

        An exact provider-task lookup may also rediscover a locally failed row.
        This is the webhook/operator recovery lane for an authoritative later
        completion; untargeted polling never scans terminal failures.
        """

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if provider_id is not None:
            provider_id = _stable_id(provider_id, "provider_id")
        if account_id is not None:
            account_id = _stable_id(account_id, "account_id")
        if external_task_id is not None:
            external_task_id = _external_id(external_task_id, "external_task_id")
        allow_terminal_correction = external_task_id is not None
        now = self._clock()
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT task.task_id, owner.owner_user_id
                     FROM external_tasks AS task
                     JOIN submission_outbox AS outbox USING(outbox_id)
                     JOIN generation_intent_owners AS owner USING(intent_id)
                     JOIN provider_accounts AS account USING(account_id)
                     JOIN provider_account_owners AS account_owner USING(account_id)
                     JOIN provider_account_entitlements AS entitlement USING(account_id)
                    WHERE (
                           (
                               task.state IN ('submitted', 'queued', 'running')
                               AND outbox.state = 'submitted'
                           )
                        OR (
                               ? = 1
                               AND task.state = 'failed'
                               AND outbox.state = 'done'
                           )
                      )
                      AND (? IS NULL OR account.provider_id = ?)
                      AND (? IS NULL OR task.account_id = ?)
                      AND (? IS NULL OR task.external_task_id = ?)
                      AND task.external_task_id IS NOT NULL
                      AND task.actor_user_id = owner.owner_user_id
                      AND outbox.actor_user_id = owner.owner_user_id
                      AND account.enabled = 1
                      AND (outbox.lease_owner IS NULL OR outbox.lease_expires_at <= ?)
                      AND (
                           (entitlement.access_scope = 'private'
                            AND account.provider_id != 'local_comfy'
                            AND account_owner.owner_user_id = owner.owner_user_id)
                        OR (entitlement.access_scope = 'authenticated_users'
                            AND account.provider_id = 'local_comfy'
                            AND account_owner.owner_user_id = ?)
                      )
                    ORDER BY task.updated_at, task.task_id
                    LIMIT ?""",
                (
                    int(allow_terminal_correction),
                    provider_id,
                    provider_id,
                    account_id,
                    account_id,
                    external_task_id,
                    external_task_id,
                    now,
                    SYSTEM_LOCAL_OWNER_ID,
                    limit,
                ),
            ).fetchall()
        return tuple({
            "task_id": str(row["task_id"]),
            "owner_user_id": str(row["owner_user_id"]),
        } for row in rows)

    def has_external_task(
        self,
        provider_id: str,
        external_task_id: str,
        *,
        account_id: str | None = None,
    ) -> bool:
        """Return whether one exact provider task is durably known.

        Webhook payloads are untrusted hints.  This lookup is deliberately
        backed only by ``external_tasks`` and rejects ambiguous cross-account
        identities instead of guessing which owner should be reconciled.
        """

        provider_id = _stable_id(provider_id, "provider_id")
        external_task_id = _external_id(external_task_id, "external_task_id")
        if account_id is not None:
            account_id = _stable_id(account_id, "account_id")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT task.task_id
                     FROM external_tasks AS task
                     JOIN provider_accounts AS account USING(account_id)
                    WHERE task.provider_id = ?
                      AND account.provider_id = ?
                      AND task.external_task_id = ?
                      AND (? IS NULL OR task.account_id = ?)
                    LIMIT 2""",
                (
                    provider_id,
                    provider_id,
                    external_task_id,
                    account_id,
                    account_id,
                ),
            ).fetchall()
        return len(rows) == 1

    def claim_owned_reconciliation(
        self,
        owner_user_id: str,
        task_id: str,
        *,
        lease_owner: str,
        lease_expires_at: str,
        allow_terminal_correction: bool = False,
    ) -> dict[str, Any]:
        """Atomically lease one actor-owned provider-status reconciliation."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        lease_expires_at = _optional_text(
            lease_expires_at, "lease_expires_at", maximum=80
        )
        if lease_expires_at is None:
            raise ValueError("lease_expires_at is required")
        if not isinstance(allow_terminal_correction, bool):
            raise TypeError("allow_terminal_correction must be a boolean")
        now = self._clock()
        try:
            with self._transaction(outbox_update=True) as connection:
                row = connection.execute(
                    """SELECT task.outbox_id
                         FROM external_tasks AS task
                         JOIN submission_outbox AS outbox USING(outbox_id)
                         JOIN generation_intent_owners AS owner USING(intent_id)
                         JOIN provider_accounts AS account USING(account_id)
                         JOIN provider_account_owners AS account_owner USING(account_id)
                         JOIN provider_account_entitlements AS entitlement USING(account_id)
                        WHERE task.task_id = ?
                          AND task.actor_user_id = ?
                          AND owner.owner_user_id = ?
                          AND outbox.actor_user_id = ?
                          AND (
                               (
                                   task.state IN ('submitted', 'queued', 'running')
                                   AND outbox.state = 'submitted'
                               )
                            OR (
                                   ? = 1
                                   AND task.state = 'failed'
                                   AND outbox.state = 'done'
                               )
                          )
                          AND task.external_task_id IS NOT NULL
                          AND account.enabled = 1
                          AND (outbox.lease_owner IS NULL OR outbox.lease_expires_at <= ?)
                          AND (
                               (entitlement.access_scope = 'private'
                                AND account.provider_id != 'local_comfy'
                                AND account_owner.owner_user_id = ?)
                            OR (entitlement.access_scope = 'authenticated_users'
                                AND account.provider_id = 'local_comfy'
                                AND account_owner.owner_user_id = ?)
                          )""",
                    (
                        task_id,
                        owner_user_id,
                        owner_user_id,
                        owner_user_id,
                        int(allow_terminal_correction),
                        now,
                        owner_user_id,
                        SYSTEM_LOCAL_OWNER_ID,
                    ),
                ).fetchone()
                if row is None:
                    raise StoreConflictError("task is not available for reconciliation")
                connection.execute(
                    """UPDATE submission_outbox
                          SET lease_owner = ?, lease_expires_at = ?, updated_at = ?
                        WHERE outbox_id = ?""",
                    (lease_owner, lease_expires_at, now, row["outbox_id"]),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_task(owner_user_id, task_id)

    def release_owned_reconciliation(
        self,
        owner_user_id: str,
        task_id: str,
        *,
        lease_owner: str,
    ) -> None:
        """Release only the exact worker's live reconciliation lease."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        now = self._clock()
        with self._transaction(outbox_update=True) as connection:
            row = connection.execute(
                """SELECT outbox.outbox_id
                     FROM external_tasks AS task
                     JOIN submission_outbox AS outbox USING(outbox_id)
                     JOIN generation_intent_owners AS owner USING(intent_id)
                    WHERE task.task_id = ?
                      AND task.actor_user_id = ?
                      AND outbox.actor_user_id = ?
                      AND owner.owner_user_id = ?
                      AND outbox.lease_owner = ?""",
                (task_id, owner_user_id, owner_user_id, owner_user_id, lease_owner),
            ).fetchone()
            if row is None:
                raise StoreConflictError("reconciliation lease is unavailable")
            connection.execute(
                """UPDATE submission_outbox
                      SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                    WHERE outbox_id = ?""",
                (now, row["outbox_id"]),
            )

    def claim_submission(
        self,
        outbox_id: str,
        *,
        task_id: str | None = None,
        lease_owner: str = "provider-worker",
        lease_expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Atomically claim an outbox row and create its CREATED task reservation."""

        outbox_id = _stable_id(outbox_id, "outbox_id")
        task_id = _stable_id(task_id or _new_id("task"), "task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        lease_expires_at = _optional_text(
            lease_expires_at, "lease_expires_at", maximum=80
        )
        now = self._clock()
        try:
            with self._transaction(
                task_insert=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                outbox = connection.execute(
                    """SELECT outbox.*, intent_owner.owner_user_id AS intent_owner,
                              account_owner.owner_user_id AS account_owner,
                              entitlement.access_scope
                         FROM submission_outbox AS outbox
                         LEFT JOIN generation_intent_owners AS intent_owner
                           ON intent_owner.intent_id = outbox.intent_id
                         LEFT JOIN provider_account_owners AS account_owner
                           ON account_owner.account_id = outbox.account_id
                         LEFT JOIN provider_account_entitlements AS entitlement
                           ON entitlement.account_id = outbox.account_id
                        WHERE outbox.outbox_id = ?""",
                    (outbox_id,),
                ).fetchone()
                if outbox is None:
                    raise KeyError(outbox_id)
                if any(
                    outbox[key] is not None
                    for key in (
                        "actor_user_id",
                        "intent_owner",
                        "account_owner",
                        "access_scope",
                    )
                ):
                    raise StoreConflictError(
                        "owned execution requires actor-scoped worker access"
                    )
                if outbox["state"] != "pending":
                    raise StoreConflictError("outbox is not pending")
                provider = connection.execute(
                    """SELECT provider_id FROM provider_accounts
                       WHERE account_id = ? AND enabled = 1""",
                    (outbox["account_id"],),
                ).fetchone()
                if provider is None:
                    raise StoreConflictError("provider account is disabled")
                if provider["provider_id"] == "autoip" and connection.execute(
                    """SELECT 1 FROM external_tasks
                        WHERE account_id = ?
                          AND state IN ('created', 'submitted', 'queued', 'running')
                        LIMIT 1""",
                    (outbox["account_id"],),
                ).fetchone() is not None:
                    raise StoreConflictError(
                        "AutoIP account concurrency capacity is occupied"
                    )
                connection.execute(
                    """INSERT INTO external_tasks(
                           task_id, outbox_id, account_id, provider_id, client_request_id,
                           state, state_revision, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'created', 0, ?, ?)""",
                    (
                        task_id,
                        outbox_id,
                        outbox["account_id"],
                        provider["provider_id"],
                        outbox["client_request_id"],
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO external_task_events(
                           task_id, from_state, to_state, happened_at
                       ) VALUES (?, NULL, 'created', ?)""",
                    (task_id, now),
                )
                connection.execute(
                    """UPDATE submission_outbox
                       SET state = 'claimed', attempt_count = attempt_count + 1,
                           lease_owner = ?, lease_expires_at = ?, updated_at = ?
                       WHERE outbox_id = ?""",
                    (lease_owner, lease_expires_at, now, outbox_id),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_task(task_id)

    def claim_owned_submission(
        self,
        owner_user_id: str,
        outbox_id: str,
        *,
        task_id: str | None = None,
        lease_owner: str = "provider-worker",
        lease_expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Claim one actor-owned outbox and propagate its actor to the task."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        outbox_id = _stable_id(outbox_id, "outbox_id")
        task_id = _stable_id(task_id or _new_id("task"), "task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        lease_expires_at = _optional_text(
            lease_expires_at, "lease_expires_at", maximum=80
        )
        now = self._clock()
        try:
            with self._transaction(
                task_insert=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                outbox = connection.execute(
                    """SELECT outbox.* FROM submission_outbox AS outbox
                         JOIN generation_intent_owners AS owner USING(intent_id)
                         JOIN provider_accounts AS account
                           ON account.account_id = outbox.account_id
                         JOIN provider_account_owners AS account_owner
                           ON account_owner.account_id = outbox.account_id
                         JOIN provider_account_entitlements AS entitlement
                           ON entitlement.account_id = outbox.account_id
                        WHERE outbox.outbox_id = ?
                          AND outbox.actor_user_id = ?
                          AND owner.owner_user_id = ?
                          AND account.enabled = 1
                          AND (
                              (
                                  entitlement.access_scope = 'private'
                                  AND account_owner.owner_user_id = ?
                                  AND account.provider_id != 'local_comfy'
                              ) OR (
                                  entitlement.access_scope = 'authenticated_users'
                                  AND account_owner.owner_user_id = ?
                                  AND account.provider_id = 'local_comfy'
                              )
                          )""",
                    (
                        outbox_id,
                        owner_user_id,
                        owner_user_id,
                        owner_user_id,
                        SYSTEM_LOCAL_OWNER_ID,
                    ),
                ).fetchone()
                if outbox is None:
                    raise KeyError(outbox_id)
                if outbox["state"] != "pending":
                    raise StoreConflictError("outbox is not pending")
                provider = connection.execute(
                    "SELECT provider_id FROM provider_accounts WHERE account_id = ?",
                    (outbox["account_id"],),
                ).fetchone()
                if provider["provider_id"] == "autoip" and connection.execute(
                    """SELECT 1 FROM external_tasks
                        WHERE account_id = ?
                          AND state IN ('created', 'submitted', 'queued', 'running')
                        LIMIT 1""",
                    (outbox["account_id"],),
                ).fetchone() is not None:
                    raise StoreConflictError(
                        "AutoIP account concurrency capacity is occupied"
                    )
                connection.execute(
                    """INSERT INTO external_tasks(
                           task_id, outbox_id, account_id, provider_id, client_request_id,
                           state, state_revision, created_at, updated_at, actor_user_id
                       ) VALUES (?, ?, ?, ?, ?, 'created', 0, ?, ?, ?)""",
                    (
                        task_id,
                        outbox_id,
                        outbox["account_id"],
                        provider["provider_id"],
                        outbox["client_request_id"],
                        now,
                        now,
                        owner_user_id,
                    ),
                )
                connection.execute(
                    """INSERT INTO external_task_events(
                           task_id, from_state, to_state, happened_at
                       ) VALUES (?, NULL, 'created', ?)""",
                    (task_id, now),
                )
                connection.execute(
                    """UPDATE submission_outbox
                       SET state = 'claimed', attempt_count = attempt_count + 1,
                           lease_owner = ?, lease_expires_at = ?, updated_at = ?
                       WHERE outbox_id = ?""",
                    (lease_owner, lease_expires_at, now, outbox_id),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_task(owner_user_id, task_id)

    def defer_owned_autoip_submission(
        self,
        owner_user_id: str,
        task_id: str,
        *,
        lease_owner: str,
        retry_at: str,
        detail: str,
    ) -> dict[str, Any]:
        """Durably defer a POST that AutoIP definitely rejected with HTTP 429.

        The existing task reservation is retained.  Only this explicit marker
        can later authorize another POST attempt; a worker crash or ambiguous
        transport outcome cannot accidentally become retryable.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        retry_at = _optional_text(retry_at, "retry_at", maximum=80)
        if retry_at is None or _RFC3339_UTC_RE.fullmatch(retry_at) is None:
            raise ValueError("retry_at must be an RFC3339 UTC timestamp")
        if not isinstance(detail, str) or _AUTOIP_DEFERRED_DETAIL_RE.fullmatch(detail) is None:
            raise ValueError("deferred AutoIP detail is invalid")
        now = self._clock()
        if retry_at <= now:
            raise ValueError("retry_at must be in the future")
        try:
            with self._transaction(
                task_update=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                current = connection.execute(
                    """SELECT task.*, outbox.state AS outbox_state,
                              outbox.lease_owner AS outbox_lease_owner
                         FROM external_tasks AS task
                         JOIN submission_outbox AS outbox USING(outbox_id)
                         JOIN provider_accounts AS account USING(account_id)
                         JOIN generation_intent_owners AS owner USING(intent_id)
                        WHERE task.task_id = ?
                          AND task.actor_user_id = ?
                          AND outbox.actor_user_id = ?
                          AND owner.owner_user_id = ?
                          AND task.provider_id = 'autoip'
                          AND account.provider_id = 'autoip'""",
                    (task_id, owner_user_id, owner_user_id, owner_user_id),
                ).fetchone()
                if current is None:
                    raise KeyError(task_id)
                if (
                    current["state"] != TaskState.CREATED.value
                    or current["external_task_id"] is not None
                    or current["outbox_state"] != "claimed"
                    or current["outbox_lease_owner"] != lease_owner
                ):
                    raise StoreConflictError(
                        "AutoIP submission is not eligible for definite rejection deferral"
                    )
                connection.execute(
                    """UPDATE external_tasks
                          SET state_revision = state_revision + 1,
                              detail = ?, updated_at = ?
                        WHERE task_id = ?""",
                    (detail, now, task_id),
                )
                connection.execute(
                    """INSERT INTO external_task_events(
                           task_id, from_state, to_state, detail, happened_at
                       ) VALUES (?, 'created', 'created', ?, ?)""",
                    (task_id, detail, now),
                )
                connection.execute(
                    """UPDATE submission_outbox
                          SET lease_owner = NULL, lease_expires_at = ?, updated_at = ?
                        WHERE outbox_id = ?""",
                    (retry_at, now, current["outbox_id"]),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_task(owner_user_id, task_id)

    def reclaim_owned_deferred_autoip_submission(
        self,
        owner_user_id: str,
        task_id: str,
        *,
        lease_owner: str,
        lease_expires_at: str,
    ) -> dict[str, Any]:
        """Lease one due, definitely rejected AutoIP request for one retry."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        lease_expires_at = _optional_text(
            lease_expires_at, "lease_expires_at", maximum=80
        )
        if (
            lease_expires_at is None
            or _RFC3339_UTC_RE.fullmatch(lease_expires_at) is None
        ):
            raise ValueError("lease_expires_at must be an RFC3339 UTC timestamp")
        now = self._clock()
        if lease_expires_at <= now:
            raise ValueError("lease_expires_at must be in the future")
        try:
            with self._transaction(
                task_update=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                current = connection.execute(
                    """SELECT task.*, outbox.state AS outbox_state,
                              outbox.lease_owner AS outbox_lease_owner,
                              outbox.lease_expires_at AS retry_at
                         FROM external_tasks AS task
                         JOIN submission_outbox AS outbox USING(outbox_id)
                         JOIN provider_accounts AS account USING(account_id)
                         JOIN generation_intent_owners AS owner USING(intent_id)
                        WHERE task.task_id = ?
                          AND task.actor_user_id = ?
                          AND outbox.actor_user_id = ?
                          AND owner.owner_user_id = ?
                          AND task.provider_id = 'autoip'
                          AND account.provider_id = 'autoip'""",
                    (task_id, owner_user_id, owner_user_id, owner_user_id),
                ).fetchone()
                if current is None:
                    raise KeyError(task_id)
                if (
                    current["state"] != TaskState.CREATED.value
                    or current["external_task_id"] is not None
                    or current["outbox_state"] != "claimed"
                    or current["outbox_lease_owner"] is not None
                    or current["retry_at"] is None
                    or current["retry_at"] > now
                    or _AUTOIP_DEFERRED_DETAIL_RE.fullmatch(current["detail"] or "")
                    is None
                ):
                    raise StoreConflictError(
                        "deferred AutoIP submission is unavailable"
                    )
                if connection.execute(
                    """SELECT 1 FROM external_tasks
                        WHERE account_id = ? AND task_id != ?
                          AND state IN ('created', 'submitted', 'queued', 'running')
                        LIMIT 1""",
                    (current["account_id"], task_id),
                ).fetchone() is not None:
                    raise StoreConflictError(
                        "AutoIP account concurrency capacity is occupied"
                    )
                # Clear the retry marker before provider I/O.  If this worker
                # disappears mid-POST, the request remains non-retryable rather
                # than risking a second charge.
                detail = "submission_retry_in_progress"
                connection.execute(
                    """UPDATE external_tasks
                          SET state_revision = state_revision + 1,
                              detail = ?, updated_at = ?
                        WHERE task_id = ?""",
                    (detail, now, task_id),
                )
                connection.execute(
                    """INSERT INTO external_task_events(
                           task_id, from_state, to_state, detail, happened_at
                       ) VALUES (?, 'created', 'created', ?, ?)""",
                    (task_id, detail, now),
                )
                connection.execute(
                    """UPDATE submission_outbox
                          SET attempt_count = attempt_count + 1,
                              lease_owner = ?, lease_expires_at = ?, updated_at = ?
                        WHERE outbox_id = ?""",
                    (
                        lease_owner,
                        lease_expires_at,
                        now,
                        current["outbox_id"],
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_owned_task(owner_user_id, task_id)

    def transition_task(
        self,
        task_id: str,
        state: TaskState | str,
        *,
        external_task_id: str | None = None,
        credits_charged: int | None = None,
        remaining_credits: int | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        task_id = _stable_id(task_id, "task_id")
        state = TaskState(state)
        external_task_id = (
            _external_id(external_task_id, "external_task_id")
            if external_task_id is not None
            else None
        )
        if credits_charged is not None and (
            not isinstance(credits_charged, int)
            or isinstance(credits_charged, bool)
            or credits_charged < 0
        ):
            raise ValueError("credits_charged must be a non-negative integer")
        if remaining_credits is not None and (
            not isinstance(remaining_credits, int)
            or isinstance(remaining_credits, bool)
            or remaining_credits < 0
        ):
            raise ValueError("remaining_credits must be a non-negative integer")
        detail = str(detail)[:4000] if detail is not None else None
        now = self._clock()
        try:
            with self._transaction(
                task_update=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                current = connection.execute(
                    """SELECT task.*, intent_owner.owner_user_id AS intent_owner,
                              account_owner.owner_user_id AS account_owner,
                              entitlement.access_scope
                         FROM external_tasks AS task
                         JOIN submission_outbox AS outbox USING(outbox_id)
                         LEFT JOIN generation_intent_owners AS intent_owner
                           ON intent_owner.intent_id = outbox.intent_id
                         LEFT JOIN provider_account_owners AS account_owner
                           ON account_owner.account_id = task.account_id
                         LEFT JOIN provider_account_entitlements AS entitlement
                           ON entitlement.account_id = task.account_id
                        WHERE task.task_id = ?""",
                    (task_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(task_id)
                if any(
                    current[key] is not None
                    for key in (
                        "actor_user_id",
                        "intent_owner",
                        "account_owner",
                        "access_scope",
                    )
                ):
                    raise StoreConflictError(
                        "owned execution requires actor-scoped worker access"
                    )
                if (
                    current["external_task_id"] is not None
                    and external_task_id is not None
                    and external_task_id != current["external_task_id"]
                ):
                    raise StoreConflictError("external task receipt is immutable")
                terminal_repeat = TaskState(current["state"]).terminal
                if terminal_repeat:
                    if current["state"] != state.value:
                        raise InvalidTaskTransition(
                            "invalid external task state transition"
                        )
                    requested_facts = (
                        (external_task_id, current["external_task_id"]),
                        (credits_charged, current["credits_charged"]),
                        (remaining_credits, current["remaining_credits"]),
                        (detail, current["detail"]),
                    )
                    if any(
                        requested is not None and requested != stored
                        for requested, stored in requested_facts
                    ):
                        raise StoreConflictError("terminal external task facts are immutable")
                else:
                    resolved_external_id = external_task_id or current["external_task_id"]
                    resolved_detail = current["detail"] if detail is None else detail
                    terminal_at = now if state.terminal else None
                    connection.execute(
                        """UPDATE external_tasks SET
                               external_task_id = ?, state = ?,
                               state_revision = state_revision + 1,
                               credits_charged = COALESCE(?, credits_charged),
                               remaining_credits = COALESCE(?, remaining_credits),
                               detail = ?, updated_at = ?, terminal_at = ?
                           WHERE task_id = ?""",
                        (
                            resolved_external_id,
                            state.value,
                            credits_charged,
                            remaining_credits,
                            resolved_detail,
                            now,
                            terminal_at,
                            task_id,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO external_task_events(
                               task_id, from_state, to_state, external_task_id,
                               detail, happened_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            task_id,
                            current["state"],
                            state.value,
                            resolved_external_id,
                            resolved_detail,
                            now,
                        ),
                    )
                    if state is TaskState.CANCELLED and current["state"] == "created":
                        outbox_state = "abandoned"
                    elif state is TaskState.SUBMISSION_UNCERTAIN:
                        outbox_state = "submission_uncertain"
                    elif state in {TaskState.SUBMITTED, TaskState.QUEUED, TaskState.RUNNING}:
                        outbox_state = "submitted"
                    elif state.terminal:
                        outbox_state = "done"
                    else:
                        outbox_state = "claimed"
                    connection.execute(
                        """UPDATE submission_outbox SET state = ?, lease_owner = NULL,
                               lease_expires_at = NULL, updated_at = ?
                           WHERE outbox_id = ?""",
                        (outbox_state, now, current["outbox_id"]),
                    )
        except sqlite3.IntegrityError as error:
            message = str(error)
            if "invalid external task state transition" in message:
                raise InvalidTaskTransition(message) from error
            raise StoreConflictError(message) from error
        return self.get_task(task_id)

    def transition_owned_task(
        self,
        owner_user_id: str,
        task_id: str,
        state: TaskState | str,
        *,
        external_task_id: str | None = None,
        credits_charged: int | None = None,
        remaining_credits: int | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """Advance an actor-owned task without crossing execution lanes."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        state = TaskState(state)
        external_task_id = (
            _external_id(external_task_id, "external_task_id")
            if external_task_id is not None
            else None
        )
        if credits_charged is not None and (
            not isinstance(credits_charged, int)
            or isinstance(credits_charged, bool)
            or credits_charged < 0
        ):
            raise ValueError("credits_charged must be a non-negative integer")
        if remaining_credits is not None and (
            not isinstance(remaining_credits, int)
            or isinstance(remaining_credits, bool)
            or remaining_credits < 0
        ):
            raise ValueError("remaining_credits must be a non-negative integer")
        detail = str(detail)[:4000] if detail is not None else None
        now = self._clock()
        try:
            with self._transaction(
                task_update=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                current = connection.execute(
                    """SELECT task.* FROM external_tasks AS task
                         JOIN submission_outbox AS outbox USING(outbox_id)
                         JOIN provider_accounts AS account
                           ON account.account_id = task.account_id
                         JOIN generation_intent_owners AS owner USING(intent_id)
                        WHERE task.task_id = ?
                          AND task.actor_user_id = ?
                          AND outbox.actor_user_id = ?
                          AND owner.owner_user_id = ?
                          AND task.account_id = outbox.account_id
                          AND task.client_request_id = outbox.client_request_id
                          AND task.provider_id = account.provider_id""",
                    (task_id, owner_user_id, owner_user_id, owner_user_id),
                ).fetchone()
                if current is None:
                    raise KeyError(task_id)
                if (
                    current["external_task_id"] is not None
                    and external_task_id is not None
                    and external_task_id != current["external_task_id"]
                ):
                    raise StoreConflictError("external task receipt is immutable")
                terminal_repeat = TaskState(current["state"]).terminal
                if terminal_repeat:
                    if current["state"] != state.value:
                        raise InvalidTaskTransition(
                            "invalid external task state transition"
                        )
                    requested_facts = (
                        (external_task_id, current["external_task_id"]),
                        (credits_charged, current["credits_charged"]),
                        (remaining_credits, current["remaining_credits"]),
                        (detail, current["detail"]),
                    )
                    if any(
                        requested is not None and requested != stored
                        for requested, stored in requested_facts
                    ):
                        raise StoreConflictError("terminal external task facts are immutable")
                else:
                    resolved_external_id = external_task_id or current["external_task_id"]
                    resolved_detail = current["detail"] if detail is None else detail
                    terminal_at = now if state.terminal else None
                    connection.execute(
                        """UPDATE external_tasks SET
                               external_task_id = ?, state = ?,
                               state_revision = state_revision + 1,
                               credits_charged = COALESCE(?, credits_charged),
                               remaining_credits = COALESCE(?, remaining_credits),
                               detail = ?, updated_at = ?, terminal_at = ?
                           WHERE task_id = ?""",
                        (
                            resolved_external_id,
                            state.value,
                            credits_charged,
                            remaining_credits,
                            resolved_detail,
                            now,
                            terminal_at,
                            task_id,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO external_task_events(
                               task_id, from_state, to_state, external_task_id,
                               detail, happened_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            task_id,
                            current["state"],
                            state.value,
                            resolved_external_id,
                            resolved_detail,
                            now,
                        ),
                    )
                    if state is TaskState.CANCELLED and current["state"] == "created":
                        outbox_state = "abandoned"
                    elif state is TaskState.SUBMISSION_UNCERTAIN:
                        outbox_state = "submission_uncertain"
                    elif state in {TaskState.SUBMITTED, TaskState.QUEUED, TaskState.RUNNING}:
                        outbox_state = "submitted"
                    elif state.terminal:
                        outbox_state = "done"
                    else:
                        outbox_state = "claimed"
                    connection.execute(
                        """UPDATE submission_outbox SET state = ?, lease_owner = NULL,
                               lease_expires_at = NULL, updated_at = ?
                           WHERE outbox_id = ?""",
                        (outbox_state, now, current["outbox_id"]),
                    )
        except sqlite3.IntegrityError as error:
            message = str(error)
            if "invalid external task state transition" in message:
                raise InvalidTaskTransition(message) from error
            raise StoreConflictError(message) from error
        return self.get_owned_task(owner_user_id, task_id)

    def reconcile_owned_completed_task(
        self,
        owner_user_id: str,
        task_id: str,
        *,
        external_task_id: str,
        credits_charged: int | None = None,
        remaining_credits: int | None = None,
        detail: str | None = None,
        lease_owner: str,
    ) -> dict[str, Any]:
        """Correct one failed task from an authoritative completion receipt.

        This is deliberately narrower than :meth:`transition_owned_task`: the
        provider task identifier must already be durable and identical, only
        ``failed -> completed`` is permitted, existing billing facts cannot be
        replaced, and no submission row is made pending.  It therefore cannot
        submit work or create another charge.
        """

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        external_task_id = _external_id(external_task_id, "external_task_id")
        lease_owner = _stable_id(lease_owner, "lease_owner")
        if credits_charged is not None and (
            not isinstance(credits_charged, int)
            or isinstance(credits_charged, bool)
            or credits_charged < 0
        ):
            raise ValueError("credits_charged must be a non-negative integer")
        if remaining_credits is not None and (
            not isinstance(remaining_credits, int)
            or isinstance(remaining_credits, bool)
            or remaining_credits < 0
        ):
            raise ValueError("remaining_credits must be a non-negative integer")
        detail = str(detail)[:4000] if detail is not None else None
        now = self._clock()
        try:
            with self._transaction(
                task_update=True,
                task_reconciliation=True,
                task_event_insert=True,
                outbox_update=True,
            ) as connection:
                current = connection.execute(
                    """SELECT task.*, outbox.state AS outbox_state,
                              outbox.lease_owner AS reconciliation_lease_owner
                         FROM external_tasks AS task
                         JOIN submission_outbox AS outbox USING(outbox_id)
                         JOIN provider_accounts AS account
                           ON account.account_id = task.account_id
                         JOIN generation_intent_owners AS owner USING(intent_id)
                        WHERE task.task_id = ?
                          AND task.actor_user_id = ?
                          AND outbox.actor_user_id = ?
                          AND owner.owner_user_id = ?
                          AND task.account_id = outbox.account_id
                          AND task.client_request_id = outbox.client_request_id
                          AND task.provider_id = account.provider_id
                          AND task.provider_id = 'autoip'""",
                    (task_id, owner_user_id, owner_user_id, owner_user_id),
                ).fetchone()
                if current is None:
                    raise KeyError(task_id)
                if current["external_task_id"] != external_task_id:
                    raise StoreConflictError("external task receipt is immutable")
                if current["outbox_state"] != "done":
                    raise StoreConflictError(
                        "terminal completion correction requires a finished outbox"
                    )
                if current["reconciliation_lease_owner"] not in (None, lease_owner):
                    raise StoreConflictError("reconciliation lease is owned elsewhere")
                for requested, stored, label in (
                    (credits_charged, current["credits_charged"], "credits charged"),
                    (
                        remaining_credits,
                        current["remaining_credits"],
                        "remaining credits",
                    ),
                ):
                    if stored is not None and requested is not None and requested != stored:
                        raise StoreConflictError(
                            f"authoritative completion {label} conflicts"
                        )
                if current["state"] == TaskState.COMPLETED.value:
                    connection.execute(
                        """UPDATE submission_outbox
                              SET lease_owner = NULL, lease_expires_at = NULL,
                                  updated_at = ?
                            WHERE outbox_id = ?""",
                        (now, current["outbox_id"]),
                    )
                elif current["state"] != TaskState.FAILED.value:
                    raise InvalidTaskTransition(
                        "terminal completion correction requires a failed task"
                    )
                else:
                    resolved_credits = (
                        current["credits_charged"]
                        if current["credits_charged"] is not None
                        else credits_charged
                    )
                    resolved_remaining = (
                        current["remaining_credits"]
                        if current["remaining_credits"] is not None
                        else remaining_credits
                    )
                    resolved_detail = current["detail"] if detail is None else detail
                    connection.execute(
                        """UPDATE external_tasks SET
                               state = 'completed',
                               state_revision = state_revision + 1,
                               credits_charged = ?, remaining_credits = ?,
                               detail = ?, updated_at = ?
                           WHERE task_id = ?""",
                        (
                            resolved_credits,
                            resolved_remaining,
                            resolved_detail,
                            now,
                            task_id,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO external_task_events(
                               task_id, from_state, to_state, external_task_id,
                               detail, happened_at
                           ) VALUES (?, 'failed', 'completed', ?, ?, ?)""",
                        (task_id, external_task_id, resolved_detail, now),
                    )
                    connection.execute(
                        """UPDATE submission_outbox
                              SET state = 'done', lease_owner = NULL,
                                  lease_expires_at = NULL, updated_at = ?
                            WHERE outbox_id = ?""",
                        (now, current["outbox_id"]),
                    )
        except sqlite3.IntegrityError as error:
            message = str(error)
            if "invalid external task state transition" in message:
                raise InvalidTaskTransition(message) from error
            raise StoreConflictError(message) from error
        return self.get_owned_task(owner_user_id, task_id)

    def get_owned_task(self, owner_user_id: str, task_id: str) -> dict[str, Any]:
        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        task_id = _stable_id(task_id, "task_id")
        with self._read_connection() as connection:
            row = connection.execute(
                """SELECT task.* FROM external_tasks AS task
                     JOIN submission_outbox AS outbox USING(outbox_id)
                     JOIN generation_intent_owners AS owner USING(intent_id)
                    WHERE task.task_id = ?
                      AND task.actor_user_id = ?
                      AND outbox.actor_user_id = ?
                      AND owner.owner_user_id = ?""",
                (task_id, owner_user_id, owner_user_id, owner_user_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            events = connection.execute(
                """SELECT event_id, from_state, to_state, external_task_id, happened_at
                   FROM external_task_events WHERE task_id = ? ORDER BY event_id""",
                (task_id,),
            ).fetchall()
        return self._public_task(row, events)

    def get_task(self, task_id: str) -> dict[str, Any]:
        task_id = _stable_id(task_id, "task_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM external_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            events = connection.execute(
                """SELECT event_id, from_state, to_state, external_task_id, happened_at
                   FROM external_task_events WHERE task_id = ? ORDER BY event_id""",
                (task_id,),
            ).fetchall()
        if row is None:
            raise KeyError(task_id)
        return self._public_task(row, events)

    @staticmethod
    def _public_task(
        row: sqlite3.Row,
        events: Sequence[sqlite3.Row],
    ) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "outbox_id": row["outbox_id"],
            "account_id": row["account_id"],
            "provider_id": row["provider_id"],
            "client_request_id": row["client_request_id"],
            "external_task_id": row["external_task_id"],
            "state": row["state"],
            "state_revision": row["state_revision"],
            "credits_charged": row["credits_charged"],
            "remaining_credits": row["remaining_credits"],
            "has_detail": bool(row["detail"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "terminal_at": row["terminal_at"],
            "events": [dict(item) for item in events],
        }

    def append_credit_entry(
        self,
        account_id: str,
        entry_kind: str,
        delta: int,
        idempotency_key: str,
        *,
        ledger_id: str | None = None,
        balance_after: int | None = None,
        task_id: str | None = None,
        intent_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
        _owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        account_id = _stable_id(account_id, "account_id")
        entry_kind = _stable_id(entry_kind, "entry_kind")
        idempotency_key = _external_id(idempotency_key, "idempotency_key")
        requested_ledger_id = (
            _stable_id(ledger_id, "ledger_id") if ledger_id is not None else None
        )
        ledger_id = requested_ledger_id or _new_id("credit")
        task_id = _stable_id(task_id, "task_id") if task_id else None
        intent_id = _stable_id(intent_id, "intent_id") if intent_id else None
        if _owner_user_id is not None:
            _owner_user_id = _stable_id(_owner_user_id, "owner_user_id")
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise ValueError("delta must be an integer")
        if balance_after is not None and (
            not isinstance(balance_after, int)
            or isinstance(balance_after, bool)
            or balance_after < 0
        ):
            raise ValueError("balance_after must be a non-negative integer")
        detail_json = _json(dict(detail or {}))
        try:
            with self._transaction() as connection:
                lane = connection.execute(
                    """SELECT o.owner_user_id, e.access_scope
                         FROM provider_accounts AS a
                         LEFT JOIN provider_account_owners AS o USING(account_id)
                         LEFT JOIN provider_account_entitlements AS e USING(account_id)
                        WHERE a.account_id = ?""",
                    (account_id,),
                ).fetchone()
                if lane is None:
                    raise KeyError(account_id)
                if _owner_user_id is None:
                    if lane["owner_user_id"] is not None or lane["access_scope"] is not None:
                        raise StoreConflictError(
                            "owned execution requires actor-scoped credit access"
                        )
                elif (
                    lane["owner_user_id"] != _owner_user_id
                    or lane["access_scope"] != "private"
                ):
                    raise KeyError(account_id)
                existing = connection.execute(
                    """SELECT * FROM credit_ledger
                       WHERE account_id = ? AND idempotency_key = ?""",
                    (account_id, idempotency_key),
                ).fetchone()
                if existing:
                    if (
                        existing["entry_kind"] != entry_kind
                        or existing["delta"] != delta
                        or existing["balance_after"] != balance_after
                        or existing["task_id"] != task_id
                        or existing["intent_id"] != intent_id
                        or existing["detail_json"] != detail_json
                        or existing["actor_user_id"] != _owner_user_id
                        or (
                            requested_ledger_id is not None
                            and existing["ledger_id"] != requested_ledger_id
                        )
                    ):
                        raise StoreConflictError(
                            "credit idempotency key already identifies another entry"
                        )
                    return self._public_credit(existing)
                connection.execute(
                    """INSERT INTO credit_ledger(
                           ledger_id, account_id, task_id, intent_id, entry_kind,
                           delta, balance_after, idempotency_key, detail_json, created_at,
                           actor_user_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ledger_id,
                        account_id,
                        task_id,
                        intent_id,
                        entry_kind,
                        delta,
                        balance_after,
                        idempotency_key,
                        detail_json,
                        self._clock(),
                        _owner_user_id,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(str(error)) from error
        return self.get_credit_entry(ledger_id)

    def append_owned_credit_entry(
        self,
        owner_user_id: str,
        account_id: str,
        entry_kind: str,
        delta: int,
        idempotency_key: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Append an idempotent credit fact inside one exact owner lane."""

        owner_user_id = _stable_id(owner_user_id, "owner_user_id")
        if "_owner_user_id" in kwargs:
            raise TypeError("_owner_user_id is server-owned")
        return self.append_credit_entry(
            account_id,
            entry_kind,
            delta,
            idempotency_key,
            _owner_user_id=owner_user_id,
            **kwargs,
        )

    @staticmethod
    def _public_credit(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "ledger_id": row["ledger_id"],
            "account_id": row["account_id"],
            "task_id": row["task_id"],
            "intent_id": row["intent_id"],
            "entry_kind": row["entry_kind"],
            "delta": row["delta"],
            "balance_after": row["balance_after"],
            "idempotency_key": row["idempotency_key"],
            "detail": _from_json(row["detail_json"]),
            "created_at": row["created_at"],
        }

    def get_credit_entry(self, ledger_id: str) -> dict[str, Any]:
        ledger_id = _stable_id(ledger_id, "ledger_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM credit_ledger WHERE ledger_id = ?", (ledger_id,)
            ).fetchone()
        if row is None:
            raise KeyError(ledger_id)
        return self._public_credit(row)

    def claim_webhook_replay(
        self,
        provider_id: str,
        replay_key: str,
        signed_at: int,
        expires_at: int,
        external_task_id: str,
    ) -> bool:
        """Atomically claim a verified webhook delivery across processes/restarts.

        Expired claims may be purged, but a live claim is immutable and never
        evicted for capacity.  ``False`` therefore means the exact delivery was
        already accepted; callers must not schedule another reconciliation.
        """

        provider_id = _stable_id(provider_id, "provider_id")
        replay_key = str(replay_key or "").strip().lower()
        if not _SHA256_RE.fullmatch(replay_key):
            raise ValueError("replay_key must be a lowercase SHA-256 digest")
        if (
            not isinstance(signed_at, int)
            or isinstance(signed_at, bool)
            or signed_at <= 0
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or expires_at < signed_at
        ):
            raise ValueError("webhook replay timestamps are invalid")
        external_task_id = _external_id(external_task_id, "external_task_id")
        now_epoch = int(self._epoch_clock())
        if expires_at < now_epoch:
            raise ValueError("webhook replay claim is already expired")
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM webhook_replay_claims WHERE expires_at < ?",
                (now_epoch,),
            )
            existing = connection.execute(
                """SELECT 1 FROM webhook_replay_claims
                   WHERE provider_id = ? AND replay_key = ?""",
                (provider_id, replay_key),
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """INSERT INTO webhook_replay_claims(
                       provider_id, replay_key, signed_at, expires_at,
                       external_task_id, received_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    provider_id,
                    replay_key,
                    signed_at,
                    expires_at,
                    external_task_id,
                    self._clock(),
                ),
            )
            return True

    @staticmethod
    def canonical_public_json(value: Mapping[str, Any] | Sequence[Any]) -> str:
        """Serialize a public record deterministically after another secret check."""

        return _json(value)


__all__ = [
    "CHARACTER_LORA_CAPABILITY_ID",
    "InvalidTaskTransition",
    "ProviderFabricStore",
    "ProviderFabricStoreError",
    "SCHEMA_VERSION",
    "SecretFieldError",
    "StoreConflictError",
    "default_database_path",
    "is_supported_autoip_history_visual_version",
    "matches_deterministic_autoip_history_identity",
]
