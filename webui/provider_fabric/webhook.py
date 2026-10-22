"""Fail-closed verification for AutoIP webhook notifications.

The provider body is intentionally not retained.  A valid notification only
causes Castle to schedule an authenticated ``GET /tasks/{uuid}`` reconciliation;
the webhook cannot inject output URLs, prompts, billing details, or task state
directly into the public catalog.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .config import SecretValue


MAX_WEBHOOK_BYTES = 1024 * 1024
DEFAULT_TOLERANCE_SECONDS = 300
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUS_MAP = {
    "created": "created",
    "submitted": "submitted",
    "pending": "queued",
    "queued": "queued",
    "processing": "running",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "timeout": "timeout",
}


class WebhookVerificationError(ValueError):
    """A webhook failed framing, signature, freshness, or schema validation."""


class WebhookReplayError(WebhookVerificationError):
    """A previously accepted signed body was delivered again."""


class ReplayGuard(Protocol):
    def claim(
        self,
        replay_key: str,
        expires_at: int,
        *,
        signed_at: int | None = None,
        external_task_id: str | None = None,
    ) -> bool: ...


class ReplayClaimStore(Protocol):
    def claim_webhook_replay(
        self,
        provider_id: str,
        replay_key: str,
        signed_at: int,
        expires_at: int,
        external_task_id: str,
    ) -> bool: ...


class MemoryReplayGuard:
    """Bounded process-local guard; production callers should persist claims."""

    def __init__(self, *, maximum_entries: int = 4096, clock: Callable[[], float] = time.time) -> None:
        if not 16 <= maximum_entries <= 100_000:
            raise ValueError("maximum_entries must be between 16 and 100000")
        self._maximum_entries = maximum_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._claims: OrderedDict[str, int] = OrderedDict()

    def claim(
        self,
        replay_key: str,
        expires_at: int,
        *,
        signed_at: int | None = None,
        external_task_id: str | None = None,
    ) -> bool:
        del signed_at, external_task_id
        if not _HEX_RE.fullmatch(str(replay_key)):
            raise ValueError("replay_key must be a SHA-256 digest")
        now = int(self._clock())
        with self._lock:
            expired = [key for key, expiry in self._claims.items() if expiry < now]
            for key in expired:
                self._claims.pop(key, None)
            if replay_key in self._claims:
                return False
            # Never evict a still-live claim: doing so would make that signed
            # delivery replayable inside the acceptance window.  Capacity
            # pressure therefore fails closed until a claim expires.
            if len(self._claims) >= self._maximum_entries:
                return False
            self._claims[replay_key] = int(expires_at)
            self._claims.move_to_end(replay_key)
            return True


class PersistentReplayGuard:
    """ReplayGuard adapter backed by ProviderFabricStore.

    The verifier learns the signed timestamp and task only after signature and
    schema validation, so these are bound immediately before ``claim``.
    Instances are single-request objects and cannot accidentally reuse context.
    """

    def __init__(
        self,
        store: ReplayClaimStore,
        *,
        provider_id: str = "autoip",
    ) -> None:
        self._store = store
        self._provider_id = provider_id

    def claim(
        self,
        replay_key: str,
        expires_at: int,
        *,
        signed_at: int | None = None,
        external_task_id: str | None = None,
    ) -> bool:
        if signed_at is None or not external_task_id:
            raise ValueError("persistent replay claims require signed task context")
        return self._store.claim_webhook_replay(
            self._provider_id,
            replay_key,
            signed_at,
            expires_at,
            external_task_id,
        )


@dataclass(frozen=True, slots=True)
class VerifiedAutoIPWebhook:
    """Minimal inert signal used to schedule authoritative task polling."""

    task_uuid: str
    status_hint: str
    signed_at: int
    body_sha256: str
    replay_key: str
    event_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": "autoip",
            "task_uuid": self.task_uuid,
            "status_hint": self.status_hint,
            "signed_at": self.signed_at,
            "body_sha256": self.body_sha256,
            "replay_key": self.replay_key,
            "event_type": self.event_type,
            "action": "poll_authoritative_task",
        }


class AutoIPWebhookVerifier:
    def __init__(
        self,
        replay_guard: ReplayGuard,
        *,
        clock: Callable[[], float] = time.time,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        max_body_bytes: int = MAX_WEBHOOK_BYTES,
    ) -> None:
        if not 30 <= tolerance_seconds <= 900:
            raise ValueError("tolerance_seconds must be between 30 and 900")
        if not 1024 <= max_body_bytes <= MAX_WEBHOOK_BYTES:
            raise ValueError("max_body_bytes is outside the webhook safety range")
        self._replay_guard = replay_guard
        self._clock = clock
        self._tolerance = int(tolerance_seconds)
        self._max_body_bytes = int(max_body_bytes)

    def verify(
        self,
        raw_body: bytes,
        signature_header: str,
        signing_secret: SecretValue,
    ) -> VerifiedAutoIPWebhook:
        if not isinstance(raw_body, bytes):
            raise WebhookVerificationError("webhook body must be raw bytes")
        if not 2 <= len(raw_body) <= self._max_body_bytes:
            raise WebhookVerificationError("webhook body size is invalid")
        if not isinstance(signing_secret, SecretValue):
            raise TypeError("signing_secret must be a resolved SecretValue")
        timestamp, supplied = _parse_signature(signature_header)
        now = int(self._clock())
        if abs(now - timestamp) > self._tolerance:
            raise WebhookVerificationError("webhook timestamp is outside the allowed window")

        signed_payload = str(timestamp).encode("ascii") + b"." + raw_body
        expected = hmac.new(
            signing_secret.reveal().encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise WebhookVerificationError("webhook signature is invalid")

        payload = _strict_json_object(raw_body)
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
        task_uuid = _safe_token(
            data.get("task_uuid") or data.get("task_id") or payload.get("task_uuid"),
            "task_uuid",
        )
        raw_status = str(data.get("status") or payload.get("status") or "").strip().lower()
        if raw_status not in _STATUS_MAP:
            raise WebhookVerificationError("webhook status is unsupported")
        raw_event_type = payload.get("type") or payload.get("event_type")
        event_type = None
        if raw_event_type not in (None, ""):
            event_type = _safe_token(raw_event_type, "event_type").lower()

        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        replay_key = hashlib.sha256(
            b"autoip-webhook-v1\0" + str(timestamp).encode("ascii") + b"\0" + bytes.fromhex(expected)
        ).hexdigest()
        if not self._replay_guard.claim(
            replay_key,
            now + self._tolerance,
            signed_at=timestamp,
            external_task_id=task_uuid,
        ):
            raise WebhookReplayError("webhook delivery was already accepted")
        return VerifiedAutoIPWebhook(
            task_uuid=task_uuid,
            status_hint=_STATUS_MAP[raw_status],
            signed_at=timestamp,
            body_sha256=body_sha256,
            replay_key=replay_key,
            event_type=event_type,
        )


def _parse_signature(value: str) -> tuple[int, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 512 or any(char in raw for char in "\r\n"):
        raise WebhookVerificationError("webhook signature header is invalid")
    fields: dict[str, list[str]] = {}
    for component in raw.split(","):
        if "=" not in component:
            raise WebhookVerificationError("webhook signature header is invalid")
        name, field_value = (part.strip() for part in component.split("=", 1))
        fields.setdefault(name, []).append(field_value)
    if set(fields) != {"t", "v1"} or len(fields["t"]) != 1 or len(fields["v1"]) != 1:
        raise WebhookVerificationError("webhook signature fields are invalid")
    timestamp_text = fields["t"][0]
    if not re.fullmatch(r"[0-9]{10,13}", timestamp_text):
        raise WebhookVerificationError("webhook timestamp is invalid")
    timestamp = int(timestamp_text)
    signature = fields["v1"][0].lower()
    if not _HEX_RE.fullmatch(signature):
        raise WebhookVerificationError("webhook v1 signature is invalid")
    return timestamp, signature


def _strict_json_object(raw_body: bytes) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WebhookVerificationError("webhook JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw_body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                WebhookVerificationError("webhook JSON contains a non-finite number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WebhookVerificationError("webhook body is not valid UTF-8 JSON") from error
    if not isinstance(value, Mapping):
        raise WebhookVerificationError("webhook body must be a JSON object")
    _validate_json_tree(value)
    return value


def _validate_json_tree(value: Any, *, depth: int = 0, items: list[int] | None = None) -> None:
    if items is None:
        items = [0]
    items[0] += 1
    if items[0] > 20_000 or depth > 32:
        raise WebhookVerificationError("webhook JSON is too complex")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 200 or any(ord(char) < 32 for char in key):
                raise WebhookVerificationError("webhook JSON key is invalid")
            _validate_json_tree(child, depth=depth + 1, items=items)
    elif isinstance(value, list):
        for child in value:
            _validate_json_tree(child, depth=depth + 1, items=items)
    elif isinstance(value, str):
        if len(value) > 100_000 or "\x00" in value:
            raise WebhookVerificationError("webhook JSON string is invalid")
    elif isinstance(value, float) and not math.isfinite(value):
        raise WebhookVerificationError("webhook JSON number is invalid")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise WebhookVerificationError("webhook JSON value is invalid")


def _safe_token(value: Any, label: str) -> str:
    token = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise WebhookVerificationError(f"webhook {label} is invalid")
    return token


__all__ = [
    "AutoIPWebhookVerifier",
    "DEFAULT_TOLERANCE_SECONDS",
    "MAX_WEBHOOK_BYTES",
    "MemoryReplayGuard",
    "PersistentReplayGuard",
    "ReplayGuard",
    "ReplayClaimStore",
    "VerifiedAutoIPWebhook",
    "WebhookReplayError",
    "WebhookVerificationError",
]
