"""Fail-closed AutoIP public REST API client.

The client implements the endpoints published by ``@autoip/mcp`` without
depending on that Node package.  POST submission is *at most once*: after the
transport is invoked, any network/decoding ambiguity produces a durable-facing
``submission_uncertain`` receipt and holds the concurrency guard.  It never
blindly retries a chargeable request.  When configured, one server-owned
Castle webhook URL is injected into every generation POST after preflight;
request/browser fields can never select or override that URL.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Callable, Iterable, Mapping, Protocol

from .config import ProviderConfig
from .autoip_contract import (
    CONTRACT_ID,
    REQUEST_SCHEMA_VERSION,
    AutoIPContractValidationError,
    NormalizedAutoIPRequest,
    loads_account_response,
    loads_credit_balance_response,
    loads_dance_template_catalog,
    loads_error_envelope,
    loads_submission_response,
    loads_pricing_response,
    loads_task_list_page,
    loads_task_status,
    normalize_generation_request,
    render_route,
)
from .media_signatures import SIGNATURE_PROBE_BYTES, classify_declared_media
from .models import (
    CapabilityKind,
    CostQuote,
    PreflightResult,
    RouteDecision,
    RouteIntent,
    TaskReceipt,
    TaskState,
    VisualClass,
)


AUTOIP_PROVIDER_ID = "autoip"
AUTOIP_API_BASE = "https://autoip.app/api/v1"
AUTOIP_WEBHOOK_PATH = "/api/webhooks/autoip"
_TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.TIMEOUT,
}
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,300}$")
_CLIENT_REQUEST_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_FORM_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\[\])?$")
_PUBLIC_WEBHOOK_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
_HARD_MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024
_TRUSTED_OUTPUT_TYPES = MappingProxyType({
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
})


def normalize_autoip_webhook_url(value: str) -> str:
    """Validate one deployment-owned public callback URL.

    The pinned AutoIP 0.4.0 contract accepts ``webhook_url`` on each task POST.
    Castle deliberately supports only its canonical receiver path over HTTPS;
    there is no request-time or browser-provided callback surface.
    """

    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        raise ValueError("AutoIP webhook URL is invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("AutoIP webhook URL is invalid") from error
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != AUTOIP_WEBHOOK_PATH
        or parsed.query
        or parsed.fragment
        or not _PUBLIC_WEBHOOK_HOST_RE.fullmatch(hostname)
        or parsed.netloc.lower() != hostname
        or hostname.endswith((".internal", ".local", ".localhost"))
    ):
        raise ValueError("AutoIP webhook URL must be the fixed public HTTPS callback")
    return f"https://{hostname}{AUTOIP_WEBHOOK_PATH}"


def _runtime_route(
    route_name: object,
    *,
    task_uuid: object | None = None,
    index: object | None = None,
) -> tuple[str, str]:
    try:
        return render_route(route_name, task_uuid=task_uuid, index=index)
    except AutoIPContractValidationError as error:
        raise AutoIPContractError("AutoIP runtime route is invalid") from error


def _check_deadline(
    deadline: float | None, monotonic: Callable[[], float]
) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise AutoIPContractError("AutoIP download wall-clock deadline exceeded")


def _request_timeout(
    deadline: float | None,
    monotonic: Callable[[], float],
    configured_timeout: float,
) -> float:
    if deadline is None:
        return configured_timeout
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AutoIPContractError("AutoIP download wall-clock deadline exceeded")
    return min(float(configured_timeout), remaining)


class AutoIPError(RuntimeError):
    pass


class AutoIPContractError(AutoIPError):
    pass


class AutoIPPreflightError(AutoIPError):
    def __init__(self, result: PreflightResult) -> None:
        super().__init__(result.detail or "AutoIP preflight rejected the request")
        self.result = result


class AutoIPApiError(AutoIPError):
    def __init__(
        self,
        status: int,
        error_code: str,
        message: str,
        *,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"AutoIP API error {status} ({error_code}): {message}")
        self.status = status
        self.error_code = error_code
        self.message = message
        self.retry_after = retry_after

    @property
    def definite_submission_rejection(self) -> bool:
        """Whether a chargeable POST is known not to have created a task."""

        return 400 <= self.status < 500 and self.status != 408


class AutoIPPollingTimeout(AutoIPError):
    def __init__(self, last_receipt: TaskReceipt) -> None:
        super().__init__("AutoIP polling deadline elapsed; the provider task may still be running")
        self.last_receipt = last_receipt


class AutoIPOperation(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"
    LIP_SYNC = "lip_sync"
    DANCE = "dance"
    MULTI_SUBJECT_VIDEO = "multi_subject_video"

    @property
    def capability(self) -> CapabilityKind:
        return CapabilityKind(self.value)

    @property
    def path(self) -> str:
        return {
            self.TEXT_TO_IMAGE: "/images/text-to-image",
            self.IMAGE_TO_IMAGE: "/images/image-to-image",
            self.IMAGE_TO_VIDEO: "/videos/image-to-video",
            self.TEXT_TO_VIDEO: "/videos/text-to-video",
            self.LIP_SYNC: "/videos/lip-sync",
            self.DANCE: "/videos/dance",
            self.MULTI_SUBJECT_VIDEO: "/videos/multi-subject-reference",
        }[self]


@dataclass(frozen=True, slots=True)
class AutoIPFile:
    field_name: str
    path: Path
    visual_class: VisualClass | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.field_name, str) or not _FORM_FIELD_RE.fullmatch(
            self.field_name
        ):
            raise ValueError("invalid multipart file field name")
        object.__setattr__(self, "path", Path(self.path))
        if self.visual_class is not None and not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        if self.content_type is not None:
            if (
                not isinstance(self.content_type, str)
                or self.content_type != self.content_type.lower()
                or self.content_type != self.content_type.strip()
                or "/" not in self.content_type
                or any(char in self.content_type for char in "\r\n")
            ):
                raise ValueError("invalid content type")


Scalar = str | int | float


@dataclass(frozen=True, slots=True)
class AutoIPJob:
    operation: AutoIPOperation
    visual_class: VisualClass
    fields: Mapping[str, Scalar | tuple[Scalar, ...]] = field(default_factory=dict)
    files: tuple[AutoIPFile, ...] = ()
    max_credits: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AutoIPOperation):
            object.__setattr__(self, "operation", AutoIPOperation(self.operation))
        if not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        cleaned: dict[str, Scalar | tuple[Scalar, ...]] = {}
        for key, value in self.fields.items():
            if not isinstance(key, str) or not _FORM_FIELD_RE.fullmatch(key):
                raise ValueError("invalid multipart field")
            values: Iterable[Any] = value if isinstance(value, tuple) else (value,)
            normalized: list[Scalar] = []
            for item in values:
                if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                    raise ValueError(f"multipart field {key} must contain scalar values")
                normalized.append(item)
            cleaned[key] = tuple(normalized) if isinstance(value, tuple) else normalized[0]
        object.__setattr__(self, "fields", MappingProxyType(cleaned))
        if self.max_credits is not None and (
            not isinstance(self.max_credits, int)
            or isinstance(self.max_credits, bool)
            or self.max_credits < 0
        ):
            raise ValueError("max_credits must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class _BoundAutoIPUpload:
    source: AutoIPFile
    content: bytes = field(repr=False)
    content_type: str


@dataclass(frozen=True, slots=True)
class _BoundAutoIPRequest:
    normalized: NormalizedAutoIPRequest
    method: str
    path: str
    uploads: tuple[_BoundAutoIPUpload, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def __repr__(self) -> str:
        names = sorted(self.headers)
        return (
            f"HttpRequest(method={self.method!r}, url={self.url!r}, "
            f"header_names={names!r}, body_bytes={len(self.body or b'')}, "
            f"timeout_seconds={self.timeout_seconds!r})"
        )


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str | tuple[str, ...]]
    body: bytes


@dataclass(slots=True)
class HttpStreamResponse:
    status: int
    headers: Mapping[str, str | tuple[str, ...]]
    stream: BinaryIO = field(repr=False)
    final_url: str

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> HttpStreamResponse:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...

    def open_stream(self, request: HttpRequest) -> HttpStreamResponse: ...


class UrllibTransport:
    """Single-attempt stdlib transport; retry policy belongs to the caller."""

    def send(self, request: HttpRequest) -> HttpResponse:
        with self.open_stream(request) as response:
            return HttpResponse(
                status=response.status,
                headers=response.headers,
                body=_read_limited(response.stream),
            )

    def open_stream(self, request: HttpRequest) -> HttpStreamResponse:
        native = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        class NoRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
                return None

        opener = urllib.request.build_opener(NoRedirects())
        try:
            response = opener.open(native, timeout=request.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        return HttpStreamResponse(
            status=int(response.status),
            headers=_preserve_response_headers(response.headers),
            stream=response,
            final_url=str(response.geturl()),
        )


@dataclass(frozen=True, slots=True)
class InFlightSnapshot:
    client_request_id: str
    external_task_id: str | None = None
    submission_uncertain: bool = False


class SingleInFlightGuard:
    """Track each submitted request without imposing an account-wide lock.

    The historical class name remains import-compatible, but distinct stable
    ``client_request_id`` values may be active together.  An uncertain outcome
    blocks only that exact request identity until explicit reconciliation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, InFlightSnapshot] = {}

    def snapshot(self, client_request_id: str | None = None) -> InFlightSnapshot | None:
        with self._lock:
            if client_request_id is not None:
                return self._active.get(client_request_id)
            # Preserve the convenient legacy inspection API when there is one
            # request; callers that operate on a request always pass its ID.
            return next(iter(self._active.values())) if len(self._active) == 1 else None

    def snapshots(self) -> tuple[InFlightSnapshot, ...]:
        with self._lock:
            return tuple(self._active.values())

    def acquire(self, client_request_id: str) -> None:
        with self._lock:
            if client_request_id in self._active:
                raise AutoIPContractError("AutoIP request is already in flight or uncertain")
            self._active[client_request_id] = InFlightSnapshot(
                client_request_id=client_request_id
            )

    def mark_submitted(self, client_request_id: str, external_task_id: str) -> None:
        with self._lock:
            self._require_owner(client_request_id)
            self._active[client_request_id] = InFlightSnapshot(
                client_request_id, external_task_id, False
            )

    def mark_uncertain(self, client_request_id: str) -> None:
        with self._lock:
            self._require_owner(client_request_id)
            self._active[client_request_id] = InFlightSnapshot(
                client_request_id, None, True
            )

    def release(self, client_request_id: str, external_task_id: str | None = None) -> None:
        with self._lock:
            self._require_owner(client_request_id)
            active = self._active[client_request_id]
            if (
                external_task_id is not None
                and active.external_task_id not in {None, external_task_id}
            ):
                raise AutoIPContractError("attempted to release a different AutoIP task")
            del self._active[client_request_id]

    def reconcile_uncertain(self, client_request_id: str, external_task_id: str | None) -> None:
        """Explicitly bind a discovered task or clear a proven non-submission."""

        with self._lock:
            self._require_owner(client_request_id)
            if not self._active[client_request_id].submission_uncertain:
                raise AutoIPContractError("the active task is not an uncertain submission")
            if external_task_id:
                self._active[client_request_id] = InFlightSnapshot(
                    client_request_id, external_task_id, False
                )
            else:
                del self._active[client_request_id]

    def _require_owner(self, client_request_id: str) -> None:
        if client_request_id not in self._active:
            raise AutoIPContractError("in-flight guard ownership mismatch")


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    balance: Mapping[str, Any]
    pricing: Mapping[str, Any]


class QuoteResolver(Protocol):
    def resolve(self, job: AutoIPJob, snapshot: QuoteSnapshot) -> CostQuote: ...


class UnknownQuoteResolver:
    """Safe default until the account's current pricing schema is verified."""

    def resolve(self, job: AutoIPJob, snapshot: QuoteSnapshot) -> CostQuote:
        del snapshot
        return CostQuote(
            provider_id=AUTOIP_PROVIDER_ID,
            capability=job.operation.capability,
            credits=None,
            remaining_credits=None,
            authoritative=False,
            detail="Current pricing response has not been mapped to this operation.",
        )


@dataclass(frozen=True, slots=True)
class OutputDownloadMetadata:
    """Server-owned identity for an authenticated output route.

    Compatibility fields for task MIME/size hints remain present but the
    strict adapter always sets them to ``None``: raw task-body metadata is not
    a public projection or adoption evidence.  Only the output response's
    bounded stream, Content-Length, digest, and shared media signature
    classifier can produce a staged receipt.
    """

    task_uuid: str
    index: int
    authenticated_url: str
    content_type: str | None
    byte_size: int | None
    requires_authorization: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_uuid": self.task_uuid,
            "index": self.index,
            "provider_content_type_hint": self.content_type,
            "provider_byte_size_hint": self.byte_size,
            "requires_authorization": self.requires_authorization,
            "adoption": "castle_backend_required",
        }


class AutoIPPlanState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AutoIPAccountHealth:
    provider_id: str
    provider_reachable: bool
    api_healthy: bool
    authenticated: bool
    configured_enabled: bool
    plan_state: AutoIPPlanState
    account_fingerprint: str | None = None
    reason_code: str | None = None
    plan_tier: str | None = None
    max_concurrent_tasks: int | None = None
    rate_limit_per_minute: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_reachable": self.provider_reachable,
            "api_healthy": self.api_healthy,
            "authenticated": self.authenticated,
            "configured_enabled": self.configured_enabled,
            "plan_state": self.plan_state.value,
            "account_fingerprint": self.account_fingerprint,
            "reason_code": self.reason_code,
            "plan_tier": self.plan_tier,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }


@dataclass(frozen=True, slots=True)
class AutoIPCreditBalance:
    available_credits: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.available_credits, int)
            or isinstance(self.available_credits, bool)
            or self.available_credits < 0
        ):
            raise ValueError("available_credits must be a non-negative integer")

    def to_dict(self) -> dict[str, int]:
        return {"available_credits": self.available_credits}


@dataclass(frozen=True, slots=True)
class AutoIPPricingEntry:
    operation: str
    credits: int
    dimensions: tuple[tuple[str, str | int], ...] = ()

    def __post_init__(self) -> None:
        operation = _pricing_token(self.operation, "operation")
        object.__setattr__(self, "operation", operation)
        if not isinstance(self.credits, int) or isinstance(self.credits, bool) or self.credits < 0:
            raise ValueError("pricing credits must be a non-negative integer")
        dimensions = tuple(sorted(self.dimensions))
        if len({name for name, _value in dimensions}) != len(dimensions):
            raise ValueError("pricing dimensions must be unique")
        for name, value in dimensions:
            _pricing_token(name, "dimension")
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError("pricing dimension values must be inert scalars")
            if isinstance(value, str):
                _pricing_token(value, "dimension value")
            elif value < 0:
                raise ValueError("pricing dimension integers cannot be negative")
        object.__setattr__(self, "dimensions", dimensions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "credits": self.credits,
            "dimensions": {name: value for name, value in self.dimensions},
        }


@dataclass(frozen=True, slots=True)
class AutoIPPricingCatalog:
    entries: tuple[AutoIPPricingEntry, ...]
    catalog_sha256: str
    authoritative: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [item.to_dict() for item in self.entries],
            "catalog_sha256": self.catalog_sha256,
            "authoritative": self.authoritative,
        }


@dataclass(frozen=True, slots=True)
class AutoIPAccountPreflight:
    ready: bool
    health: AutoIPAccountHealth
    balance: AutoIPCreditBalance | None
    pricing: AutoIPPricingCatalog | None
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "health": self.health.to_dict(),
            "balance": self.balance.to_dict() if self.balance else None,
            "pricing": self.pricing.to_dict() if self.pricing else None,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class OutputDownloadReceipt:
    task_uuid: str
    index: int
    staging_name: str
    byte_size: int
    sha256: str
    content_type: str

    def __post_init__(self) -> None:
        _required_token(self.task_uuid, "task_uuid")
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ValueError("output index must be a non-negative integer")
        if Path(self.staging_name).name != self.staging_name or self.staging_name in {".", ".."}:
            raise ValueError("staging_name must be a plain filename")
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool) or self.byte_size < 1:
            raise ValueError("byte_size must be a positive integer")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("sha256 must be a lowercase digest")
        if self.content_type not in _TRUSTED_OUTPUT_TYPES:
            raise ValueError("content_type is not a trusted output type")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_uuid": self.task_uuid,
            "index": self.index,
            "staging_name": self.staging_name,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "content_type": self.content_type,
        }


class AutoIPClient:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: HttpTransport | None = None,
        guard: SingleInFlightGuard | None = None,
        quote_resolver: QuoteResolver | None = None,
        webhook_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if config.provider_id != AUTOIP_PROVIDER_ID:
            raise ValueError("AutoIPClient requires provider_id='autoip'")
        if config.api_base != AUTOIP_API_BASE:
            raise ValueError("AutoIPClient requires the canonical AutoIP API base")
        self.config = config
        self.transport = transport or UrllibTransport()
        self.guard = guard or SingleInFlightGuard()
        self.quote_resolver = quote_resolver or UnknownQuoteResolver()
        self.webhook_url = (
            normalize_autoip_webhook_url(webhook_url)
            if webhook_url is not None
            else None
        )
        self._sleep = sleep
        self._monotonic = monotonic

    def account_health(self) -> AutoIPAccountHealth:
        """Probe ``GET /me`` while returning no account profile fields."""

        try:
            method, path = _runtime_route("account")
            response = self._send(method, path)
        except Exception:
            return AutoIPAccountHealth(
                provider_id=AUTOIP_PROVIDER_ID,
                provider_reachable=False,
                api_healthy=False,
                authenticated=False,
                configured_enabled=self.config.enabled,
                plan_state=AutoIPPlanState.UNKNOWN,
                reason_code="transport_or_credential_error",
            )
        if response.status in {401, 403}:
            return AutoIPAccountHealth(
                provider_id=AUTOIP_PROVIDER_ID,
                provider_reachable=True,
                api_healthy=True,
                authenticated=False,
                configured_enabled=self.config.enabled,
                plan_state=AutoIPPlanState.UNKNOWN,
                reason_code="authentication_failed",
            )
        if not 200 <= response.status < 300:
            return AutoIPAccountHealth(
                provider_id=AUTOIP_PROVIDER_ID,
                provider_reachable=True,
                api_healthy=False,
                authenticated=False,
                configured_enabled=self.config.enabled,
                plan_state=AutoIPPlanState.UNKNOWN,
                reason_code="provider_unhealthy",
            )
        try:
            account = loads_account_response(response.body)
        except AutoIPContractValidationError:
            return AutoIPAccountHealth(
                provider_id=AUTOIP_PROVIDER_ID,
                provider_reachable=True,
                api_healthy=False,
                authenticated=False,
                configured_enabled=self.config.enabled,
                plan_state=AutoIPPlanState.UNKNOWN,
                reason_code="invalid_account_contract",
            )
        account_fingerprint = (
            hashlib.sha256(
                f"autoip-account-v1:{account.account_identity}".encode("utf-8")
            ).hexdigest()[:24]
            if account.account_identity is not None
            else None
        )
        plan_state = AutoIPPlanState(account.plan_state)
        return AutoIPAccountHealth(
            provider_id=AUTOIP_PROVIDER_ID,
            provider_reachable=True,
            api_healthy=True,
            authenticated=True,
            configured_enabled=self.config.enabled,
            plan_state=plan_state,
            account_fingerprint=account_fingerprint,
            plan_tier=account.plan_tier,
            max_concurrent_tasks=account.max_concurrent_tasks,
            rate_limit_per_minute=account.rate_limit_per_minute,
        )

    def credit_balance(self) -> AutoIPCreditBalance:
        """Read ``GET /credits/balance`` and retain only the integer balance."""

        method, path = _runtime_route("credit_balance")
        response = self._send(method, path)
        if not 200 <= response.status < 300:
            raise _api_error(response)
        try:
            balance = loads_credit_balance_response(response.body)
        except AutoIPContractValidationError as error:
            raise AutoIPContractError("AutoIP credit balance response is invalid") from error
        return AutoIPCreditBalance(balance.available_credits)

    def pricing_catalog(self) -> AutoIPPricingCatalog:
        """Read ``GET /pricing`` and return a strict, inert pricing projection."""

        method, path = _runtime_route("pricing")
        response = self._send(method, path)
        if not 200 <= response.status < 300:
            raise _api_error(response)
        try:
            frozen = loads_pricing_response(response.body)
        except AutoIPContractValidationError as error:
            raise AutoIPContractError("AutoIP pricing response is invalid") from error
        entries = tuple(
            AutoIPPricingEntry(item.operation, item.credits, item.dimensions)
            for item in frozen.entries
        )
        public_entries = [item.to_dict() for item in entries]
        encoded = json.dumps(
            public_entries,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return AutoIPPricingCatalog(
            entries=entries,
            catalog_sha256=hashlib.sha256(encoded).hexdigest(),
            authoritative=bool(entries),
        )

    def account_preflight(self, *, minimum_credits: int = 0) -> AutoIPAccountPreflight:
        """Probe account, balance and pricing without exposing provider payloads."""

        if (
            not isinstance(minimum_credits, int)
            or isinstance(minimum_credits, bool)
            or minimum_credits < 0
        ):
            raise ValueError("minimum_credits must be a non-negative integer")
        health = self.account_health()
        reasons: list[str] = []
        if not self.config.enabled:
            reasons.append("provider_disabled")
        if not health.provider_reachable:
            reasons.append("provider_unreachable")
        if not health.api_healthy:
            reasons.append("provider_unhealthy")
        if not health.authenticated:
            reasons.append("authentication_failed")
        if health.plan_state is AutoIPPlanState.INACTIVE:
            reasons.append("plan_inactive")
        balance: AutoIPCreditBalance | None = None
        pricing: AutoIPPricingCatalog | None = None
        if health.authenticated and health.api_healthy:
            try:
                balance = self.credit_balance()
            except AutoIPError:
                # Balance is telemetry only.  Active/unlimited accounts may
                # omit or lag this endpoint; provider submission remains the
                # authority for any actual account-side rejection.
                balance = None
            try:
                pricing = self.pricing_catalog()
            except AutoIPError:
                reasons.append("pricing_unavailable")
        if pricing is not None and not pricing.authoritative:
            reasons.append("pricing_unavailable")
        unique_reasons = tuple(dict.fromkeys(reasons))
        return AutoIPAccountPreflight(
            ready=not unique_reasons,
            health=health,
            balance=balance,
            pricing=pricing,
            reason_codes=unique_reasons,
        )

    def quote(self, job: AutoIPJob) -> CostQuote:
        # Reject a divergent operation/class/variant/input contract before any
        # account transport can influence a chargeable decision.
        _bind_job(job)
        balance = self.credit_balance()
        pricing = self.pricing_catalog()
        snapshot = QuoteSnapshot(
            balance=MappingProxyType(balance.to_dict()),
            pricing=MappingProxyType(pricing.to_dict()),
        )
        quote = self.quote_resolver.resolve(job, snapshot)
        if quote.provider_id != AUTOIP_PROVIDER_ID or quote.capability is not job.operation.capability:
            raise AutoIPContractError("quote resolver returned a quote for another provider or capability")
        return quote

    def preflight(
        self,
        intent: RouteIntent,
        decision: RouteDecision,
        job: AutoIPJob,
        *,
        client_request_id: str,
        quote: CostQuote | None,
    ) -> PreflightResult:
        try:
            bound = _bind_job(job)
        except (AutoIPContractValidationError, AutoIPContractError, OSError, ValueError):
            bound = None
        return self._preflight_bound(
            intent,
            decision,
            job,
            bound=bound,
            client_request_id=client_request_id,
            quote=quote,
        )

    def _preflight_bound(
        self,
        intent: RouteIntent,
        decision: RouteDecision,
        job: AutoIPJob,
        *,
        bound: _BoundAutoIPRequest | None,
        client_request_id: str,
        quote: CostQuote | None,
    ) -> PreflightResult:
        reasons: list[str] = []
        details: list[str] = []
        if bound is None:
            reasons.append("request_contract_invalid")
            details.append("The request does not match the frozen AutoIP generation contract.")
        if not self.config.enabled:
            reasons.append("provider_disabled")
            details.append("AutoIP is disabled in provider configuration.")
        if not decision.routable or decision.selected_provider_id != AUTOIP_PROVIDER_ID:
            reasons.append("route_not_selected")
            details.append("The verified route decision did not select AutoIP.")
        operation = (
            AutoIPOperation(bound.normalized.operation.value)
            if bound is not None
            else job.operation
        )
        visual_class = (
            VisualClass(bound.normalized.visual_class)
            if bound is not None
            else job.visual_class
        )
        if intent.capability is not operation.capability:
            reasons.append("capability_mismatch")
            details.append("The routed capability differs from the AutoIP operation.")
        if intent.visual_class is not visual_class:
            reasons.append("visual_class_mismatch")
            details.append("The routed visual class differs from the AutoIP payload.")
        if any(item.visual_class is not intent.visual_class for item in intent.characters):
            reasons.append("character_visual_class_mismatch")
            details.append("A routed character belongs to a different visual class.")
        if any(
            item.visual_class is not None and item.visual_class is not intent.visual_class
            for item in intent.assets
        ):
            reasons.append("asset_visual_class_mismatch")
            details.append("A routed identity asset belongs to a different visual class.")
        if self.guard.snapshot(client_request_id) is not None:
            reasons.append("request_in_flight")
            details.append("This exact AutoIP request is already in flight or awaiting reconciliation.")
        if not _CLIENT_REQUEST_RE.fullmatch(str(client_request_id or "")):
            reasons.append("invalid_client_request_id")
            details.append("client_request_id must be a stable safe token.")
        try:
            key = self.config.api_key_ref.resolve().reveal()
            if not key.startswith("aip_"):
                reasons.append("invalid_api_key_format")
                details.append("The configured secret is not an AutoIP API key.")
        except (OSError, KeyError, PermissionError, ValueError):
            reasons.append("credential_unavailable")
            details.append("The AutoIP credential reference cannot be resolved securely.")

        if quote is None or not quote.authoritative or quote.credits is None:
            reasons.append("authoritative_quote_required")
            details.append("A current authoritative credit quote is required before submission.")
        else:
            if quote.provider_id != AUTOIP_PROVIDER_ID or quote.capability is not operation.capability:
                reasons.append("quote_scope_mismatch")
                details.append("The quote is for another provider or capability.")

        unique_reasons = tuple(dict.fromkeys(reasons))
        return PreflightResult(
            accepted=not unique_reasons,
            reason_codes=unique_reasons,
            detail=" ".join(details),
            quote=quote,
        )

    def submit(
        self,
        intent: RouteIntent,
        decision: RouteDecision,
        job: AutoIPJob,
        *,
        client_request_id: str,
        quote: CostQuote,
    ) -> TaskReceipt:
        """Submit exactly once; ambiguous transport outcomes are never retried."""

        try:
            bound = _bind_job(job)
        except (AutoIPContractValidationError, AutoIPContractError, OSError, ValueError):
            bound = None
        preflight = self._preflight_bound(
            intent,
            decision,
            job,
            bound=bound,
            client_request_id=client_request_id,
            quote=quote,
        )
        if not preflight.accepted:
            raise AutoIPPreflightError(preflight)
        if bound is None:  # Defensive: accepted implies a frozen request.
            raise AutoIPContractError("AutoIP request binding was lost after preflight")

        fields = _flatten_fields(bound.normalized.fields)
        if self.webhook_url is not None:
            # ``webhook_url`` is provider transport metadata, not part of the
            # user-controlled generation contract.  The frozen normalizer
            # rejects that field, so this fixed post-authorization injection
            # cannot be overridden or duplicated by a crafted request.
            fields.append(("webhook_url", self.webhook_url))
        body, content_type = _encode_bound_multipart(fields, bound.uploads)
        # Build and authorize the request before acquiring the account guard.
        # Failures here are definitely local and therefore are not ambiguous
        # provider submissions.
        request = self._build_request(
            bound.method,
            bound.path,
            body=body,
            extra_headers={"Content-Type": content_type},
        )
        self.guard.acquire(client_request_id)
        try:
            response = self.transport.send(request)
        except BaseException as error:
            self.guard.mark_uncertain(client_request_id)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            return TaskReceipt(
                provider_id=AUTOIP_PROVIDER_ID,
                client_request_id=client_request_id,
                state=TaskState.SUBMISSION_UNCERTAIN,
                detail=f"Submission outcome is unknown ({type(error).__name__}); do not resubmit.",
            )

        if not isinstance(response, HttpResponse):
            self.guard.mark_uncertain(client_request_id)
            return TaskReceipt(
                provider_id=AUTOIP_PROVIDER_ID,
                client_request_id=client_request_id,
                state=TaskState.SUBMISSION_UNCERTAIN,
                detail=(
                    "Submission outcome is unknown because the transport returned "
                    "no valid receipt; do not resubmit."
                ),
            )

        if not 200 <= response.status < 300 and not _definite_submission_rejection(response):
            self.guard.mark_uncertain(client_request_id)
            return TaskReceipt(
                provider_id=AUTOIP_PROVIDER_ID,
                client_request_id=client_request_id,
                state=TaskState.SUBMISSION_UNCERTAIN,
                detail=f"Submission outcome is unknown after HTTP {response.status}; do not resubmit.",
            )

        if not 200 <= response.status < 300:
            self.guard.release(client_request_id)
            raise _api_error(response)

        try:
            # This is intentionally the strict, duplicate-preserving frozen
            # 0.4.0 response decoder.  A generic JSON projection here could
            # bind the spend receipt to a contradictory task identifier.
            submission = loads_submission_response(response.body)
            task_uuid = submission.task_uuid
            credits = submission.credits_charged
            remaining = submission.remaining_credits
        except AutoIPContractValidationError as error:
            self.guard.mark_uncertain(client_request_id)
            return TaskReceipt(
                provider_id=AUTOIP_PROVIDER_ID,
                client_request_id=client_request_id,
                state=TaskState.SUBMISSION_UNCERTAIN,
                detail=(
                    "Provider accepted the request but returned an ambiguous receipt "
                    f"({type(error).__name__}); do not resubmit."
                ),
            )

        self.guard.mark_submitted(client_request_id, task_uuid)
        return TaskReceipt(
            provider_id=AUTOIP_PROVIDER_ID,
            client_request_id=client_request_id,
            state=TaskState.SUBMITTED,
            external_task_id=task_uuid,
            credits_charged=credits,
            remaining_credits=remaining,
            detail="",
        )

    def poll_task(self, task_uuid: str, *, client_request_id: str) -> TaskReceipt:
        method, path = _runtime_route("task_status", task_uuid=task_uuid)
        response = self._send(method, path)
        if not 200 <= response.status < 300:
            raise _api_error(response)
        try:
            task = loads_task_status(response.body, expected_task_id=task_uuid)
        except AutoIPContractValidationError as error:
            raise AutoIPContractError("AutoIP task status response is invalid") from error
        state = _task_state(task.status)
        outputs = tuple(MappingProxyType(item.to_dict()) for item in task.outputs)
        receipt = TaskReceipt(
            provider_id=AUTOIP_PROVIDER_ID,
            client_request_id=client_request_id,
            state=state,
            external_task_id=task_uuid,
            credits_charged=task.credits_charged,
            remaining_credits=task.remaining_credits,
            # Task messages are provider-controlled and may reflect prompts,
            # URLs, or credentials.  Keep only a local lifecycle summary in
            # the public receipt; raw provider payloads never cross the adapter.
            detail=(
                f"AutoIP task reported state {state.value}."
                + ("" if task.status in _TASK_STATE_BY_STATUS
                   else f" Unmapped provider status: {task.status}.")
            ),
            outputs=outputs,
        )
        if state in _TERMINAL_STATES:
            active = self.guard.snapshot(client_request_id)
            if (
                active
                and active.client_request_id == client_request_id
                and active.external_task_id == task_uuid
            ):
                self.guard.release(client_request_id, task_uuid)
        return receipt

    def wait_for_task(
        self,
        task_uuid: str,
        *,
        client_request_id: str,
        timeout_seconds: float = 900,
        poll_interval_seconds: float = 10,
    ) -> TaskReceipt:
        if not 10 <= timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 10 and 3600")
        if not 2 <= poll_interval_seconds <= 60:
            raise ValueError("poll_interval_seconds must be between 2 and 60")
        deadline = self._monotonic() + timeout_seconds
        while True:
            receipt = self.poll_task(task_uuid, client_request_id=client_request_id)
            if receipt.state in _TERMINAL_STATES:
                return receipt
            if self._monotonic() + poll_interval_seconds > deadline:
                raise AutoIPPollingTimeout(receipt)
            self._sleep(poll_interval_seconds)

    def list_tasks(self, *, page: int = 1, per_page: int = 20) -> Mapping[str, Any]:
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or isinstance(per_page, bool)
            or not isinstance(per_page, int)
            or page < 1
            or page > 999_999
            or not 1 <= per_page <= 100
        ):
            raise ValueError("invalid task page")
        query = urllib.parse.urlencode({"page": page, "per_page": per_page})
        method, path = _runtime_route("task_list")
        response = self._send(method, f"{path}?{query}")
        if not 200 <= response.status < 300:
            raise _api_error(response)
        try:
            return loads_task_list_page(
                response.body,
                expected_page=page,
                expected_per_page=per_page,
            ).to_dict()
        except AutoIPContractValidationError as error:
            raise AutoIPContractError("AutoIP task list response is invalid") from error

    def list_dance_templates(self) -> Mapping[str, Any]:
        method, path = _runtime_route("dance_templates")
        response = self._send(method, path)
        if not 200 <= response.status < 300:
            raise _api_error(response)
        try:
            return loads_dance_template_catalog(response.body).to_dict()
        except AutoIPContractValidationError as error:
            raise AutoIPContractError("AutoIP dance catalog response is invalid") from error

    def output_metadata(self, task_uuid: str, index: int) -> OutputDownloadMetadata:
        return self._output_metadata(task_uuid, index, deadline=None)

    def _output_metadata(
        self,
        task_uuid: str,
        index: int,
        *,
        deadline: float | None,
    ) -> OutputDownloadMetadata:
        method, path = _runtime_route("task_status", task_uuid=task_uuid)
        _output_method, _output_path = _runtime_route(
            "task_output", task_uuid=task_uuid, index=index
        )
        _check_deadline(deadline, self._monotonic)
        response = self._send(
            method,
            path,
            timeout_seconds=_request_timeout(
                deadline, self._monotonic, self.config.timeout_seconds
            ),
        )
        _check_deadline(deadline, self._monotonic)
        if not 200 <= response.status < 300:
            raise _api_error(response)
        try:
            task = loads_task_status(response.body, expected_task_id=task_uuid)
        except AutoIPContractValidationError as error:
            raise AutoIPContractError("AutoIP task status response is invalid") from error
        if task.status != "completed":
            raise AutoIPContractError("requested output belongs to a non-completed task")
        matches = [candidate for candidate in task.outputs if candidate.index == index]
        if len(matches) != 1:
            raise AutoIPContractError("requested output metadata is not present or is ambiguous")
        # Never forward an Authorization header to a provider-supplied host.
        # Always use the documented same-origin authenticated endpoint.
        authenticated_url = self._url(_output_path)
        return OutputDownloadMetadata(
            task_uuid=task_uuid,
            index=index,
            authenticated_url=authenticated_url,
            content_type=None,
            byte_size=None,
        )

    def download_output_to_staging(
        self,
        task_uuid: str,
        index: int,
        staging_root: Path,
        *,
        max_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
        metadata: OutputDownloadMetadata | None = None,
        timeout_seconds: float = 120.0,
    ) -> OutputDownloadReceipt:
        """Stream one authenticated output into a trusted staging directory.

        The returned receipt contains only an inert relative filename, digest,
        size and media type.  The API key and provider download URL are never
        serialized.  The destination name is generated by this client; callers
        cannot supply a path component.  The bounded signature classifier is
        an adoption gate, not a claim that every possible polyglot can be
        detected; downstream decoders must remain sandboxed and bounded.
        """

        if not self.config.enabled:
            raise AutoIPContractError("AutoIP is disabled in provider configuration")
        output_method, output_path = _runtime_route(
            "task_output", task_uuid=task_uuid, index=index
        )
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or not 1 <= max_bytes <= _HARD_MAX_DOWNLOAD_BYTES
        ):
            raise ValueError("max_bytes must be within the client safety range")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 600
        ):
            raise ValueError("timeout_seconds must be between 1 and 600")
        deadline = self._monotonic() + float(timeout_seconds)
        _check_deadline(deadline, self._monotonic)
        _root, root_fd = _open_staging_root(Path(staging_root))
        temporary_name: str | None = None
        unverified_final_name: str | None = None
        try:
            if metadata is not None:
                if not isinstance(metadata, OutputDownloadMetadata):
                    raise AutoIPContractError("output metadata has an invalid type")
                if metadata.task_uuid != task_uuid or metadata.index != index:
                    raise AutoIPContractError("output metadata does not match the requested task output")
            # A caller-provided metadata object is only a cache hint.  Rebind
            # the output to a fresh, route-bound, completed strict task body
            # before opening the authenticated download lane.
            self._output_metadata(task_uuid, index, deadline=deadline)

            request = self._build_request(
                output_method,
                output_path,
                timeout_seconds=_request_timeout(
                    deadline, self._monotonic, self.config.timeout_seconds
                ),
            )
            open_stream = getattr(self.transport, "open_stream", None)
            if not callable(open_stream):
                raise AutoIPContractError("configured transport cannot stream output downloads")
            response = open_stream(request)
            _check_deadline(deadline, self._monotonic)
            if not isinstance(response, HttpStreamResponse):
                raise AutoIPContractError("stream transport returned an invalid response")
            with response:
                if 300 <= response.status < 400:
                    raise AutoIPContractError("AutoIP output redirects are forbidden")
                if response.status != 200:
                    error_body = (
                        b""
                        if response.status == 206
                        else _read_limited(
                            response.stream,
                            deadline=deadline,
                            monotonic=self._monotonic,
                        )
                    )
                    raise _api_error(
                        HttpResponse(response.status, response.headers, error_body)
                    )
                if response.final_url != request.url:
                    raise AutoIPContractError("AutoIP output stream changed origin or path")
                response_type_hint = _declared_content_type(response.headers)
                content_length = _declared_content_length(response.headers)
                if content_length is not None and content_length > max_bytes:
                    raise AutoIPContractError("HTTP-declared output exceeds max_bytes")

                unique = uuid.uuid4().hex
                temporary_name = f".autoip-{unique}.part"
                fd = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_fd,
                )
                streamed_digest = hashlib.sha256()
                total = 0
                written_info: os.stat_result | None = None
                try:
                    while True:
                        _check_deadline(deadline, self._monotonic)
                        chunk = response.stream.read(min(1024 * 1024, max_bytes - total + 1))
                        _check_deadline(deadline, self._monotonic)
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes):
                            raise AutoIPContractError("output stream returned non-byte content")
                        total += len(chunk)
                        if total > max_bytes:
                            raise AutoIPContractError("streamed output exceeds max_bytes")
                        _write_all(fd, chunk)
                        streamed_digest.update(chunk)
                    if total < 1:
                        raise AutoIPContractError("AutoIP returned an empty output")
                    if content_length is not None and total != content_length:
                        raise AutoIPContractError("streamed output size differs from Content-Length")
                    os.fsync(fd)
                    written_info = os.fstat(fd)
                    _validate_staged_stat(written_info, total)
                finally:
                    os.close(fd)
                if written_info is None:
                    raise AutoIPContractError("AutoIP staged output was not finalized")
                neutral_name = f"autoip-{task_uuid}-{index}-{unique}.pending"
                os.rename(
                    temporary_name,
                    neutral_name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                )
                temporary_name = None
                unverified_final_name = neutral_name
                os.fsync(root_fd)

                verified_size, verified_digest, verified_probe = _rehash_staged_output(
                    root_fd,
                    neutral_name,
                    expected_info=written_info,
                    expected_size=total,
                    deadline=deadline,
                    monotonic=self._monotonic,
                )
                if not hmac.compare_digest(
                    streamed_digest.digest(), bytes.fromhex(verified_digest)
                ):
                    raise AutoIPContractError("staged output changed after rename")
                if classify_declared_media(response_type_hint, verified_probe) is None:
                    raise AutoIPContractError(
                        "AutoIP output bytes do not match the response Content-Type"
                    )

                # Only verified bytes may select a media-bearing extension.
                final_name = (
                    f"autoip-{task_uuid}-{index}-{unique}"
                    f"{_TRUSTED_OUTPUT_TYPES[response_type_hint]}"
                )
                os.rename(
                    neutral_name,
                    final_name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                )
                unverified_final_name = final_name
                os.fsync(root_fd)
                final_size, final_digest, final_probe = _rehash_staged_output(
                    root_fd,
                    final_name,
                    expected_info=written_info,
                    expected_size=verified_size,
                    deadline=deadline,
                    monotonic=self._monotonic,
                )
                if (
                    not hmac.compare_digest(verified_digest, final_digest)
                    or classify_declared_media(response_type_hint, final_probe) is None
                ):
                    raise AutoIPContractError("staged output changed during final adoption")
                _validate_complete_staged_media(
                    root_fd,
                    final_name,
                    response_type_hint,
                    expected_info=written_info,
                    expected_size=final_size,
                    deadline=deadline,
                    monotonic=self._monotonic,
                )
                adopted_size, adopted_digest, adopted_probe = _rehash_staged_output(
                    root_fd,
                    final_name,
                    expected_info=written_info,
                    expected_size=final_size,
                    deadline=deadline,
                    monotonic=self._monotonic,
                )
                if (
                    not hmac.compare_digest(final_digest, adopted_digest)
                    or classify_declared_media(response_type_hint, adopted_probe) is None
                ):
                    raise AutoIPContractError(
                        "staged output changed after complete media validation"
                    )
                receipt = OutputDownloadReceipt(
                    task_uuid=task_uuid,
                    index=index,
                    staging_name=final_name,
                    byte_size=adopted_size,
                    sha256=adopted_digest,
                    content_type=response_type_hint,
                )
                unverified_final_name = None
                return receipt
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
            if unverified_final_name is not None:
                try:
                    os.unlink(unverified_final_name, dir_fd=root_fd)
                    os.fsync(root_fd)
                except FileNotFoundError:
                    pass
            os.close(root_fd)

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        return self.transport.send(self._build_request(
            method,
            path,
            body=body,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        ))

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpRequest:
        secret = self.config.api_key_ref.resolve().reveal()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {secret}",
            "User-Agent": "castle-provider-fabric/1",
            **dict(extra_headers or {}),
        }
        return HttpRequest(
            method=method,
            url=self._url(path),
            headers=MappingProxyType(headers),
            body=body,
            timeout_seconds=(
                self.config.timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
        )
    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise AutoIPContractError("API path must be absolute")
        return f"{self.config.api_base}{path}"


_RUNTIME_INPUT_MIMES = MappingProxyType(
    {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }
)


def _bind_job(job: AutoIPJob) -> _BoundAutoIPRequest:
    """Materialize one immutable request through the frozen contract once."""

    uploads: list[_BoundAutoIPUpload] = []
    inputs: list[dict[str, Any]] = []
    for item in job.files:
        content = _read_upload_file(item.path, 100 * 1024 * 1024)
        mime_type = item.content_type or _RUNTIME_INPUT_MIMES.get(
            item.path.suffix.lower()
        )
        if mime_type is None:
            raise AutoIPContractError("upload MIME type is not recognized")
        signature_family = classify_declared_media(
            mime_type, content[:SIGNATURE_PROBE_BYTES]
        )
        if signature_family is None:
            raise AutoIPContractError(
                "upload bytes do not match their declared MIME type"
            )
        digest = hashlib.sha256(content).hexdigest()
        inputs.append(
            {
                "field_name": item.field_name,
                "asset_version_id": f"runtime.{digest[:32]}",
                "sha256": digest,
                "mime_type": mime_type,
                "byte_size": len(content),
                "signature_family": signature_family,
                "visual_class": (
                    item.visual_class.value if item.visual_class is not None else None
                ),
            }
        )
        uploads.append(_BoundAutoIPUpload(item, content, mime_type))
    normalized = normalize_generation_request(
        {
            "contract": CONTRACT_ID,
            "schema_version": REQUEST_SCHEMA_VERSION,
            "operation": job.operation.value,
            "visual_class": job.visual_class.value,
            "fields": dict(job.fields),
            "inputs": inputs,
        }
    )
    method, path = _runtime_route(normalized.operation.value)
    if method != "POST":
        raise AutoIPContractError("generation route is not chargeable POST")
    return _BoundAutoIPRequest(normalized, method, path, tuple(uploads))


def _flatten_fields(fields: Mapping[str, Scalar | tuple[Scalar, ...]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for name, value in fields.items():
        values = value if isinstance(value, tuple) else (value,)
        result.extend((name, str(item)) for item in values)
    return result


def _encode_bound_multipart(
    fields: Iterable[tuple[str, str]],
    uploads: tuple[_BoundAutoIPUpload, ...],
) -> tuple[bytes, str]:
    boundary = f"castle-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        if any(char in name for char in "\r\n\""):
            raise AutoIPContractError("unsafe multipart field name")
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    for upload in uploads:
        item = upload.source
        filename = item.path.name.replace("\r", "_").replace("\n", "_").replace('"', "_")
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{item.field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {upload.content_type}\r\n\r\n".encode("ascii"),
            upload.content,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _read_upload_file(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise AutoIPContractError("upload file became unavailable before submission") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise AutoIPContractError("upload must remain a regular non-symlink file")
        if info.st_size > limit:
            raise AutoIPContractError("upload file changed and now exceeds the provider limit")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise AutoIPContractError("upload file changed and now exceeds the provider limit")
        return data
    finally:
        os.close(fd)


def _read_limited(
    stream: Any,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> bytes:
    _check_deadline(deadline, monotonic)
    body = stream.read(_MAX_RESPONSE_BYTES + 1)
    _check_deadline(deadline, monotonic)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise AutoIPContractError("AutoIP response exceeded the client safety limit")
    return body


def _api_error(response: HttpResponse) -> AutoIPApiError:
    """Project an untrusted provider failure into a non-sensitive error.

    Providers and upstream proxies can reflect request data in error bodies.
    In particular, an authentication failure must never make a submitted key
    available to logs or the browser through ``str(error)``.  The raw body is
    therefore used only to accept a conservative machine-readable code; the
    human message is deliberately local and constant.
    """

    try:
        code = loads_error_envelope(response.body).error_code
    except AutoIPContractValidationError:
        code = "provider_error"
    message = "AutoIP request failed; provider response content was redacted."
    retry_after = _strict_retry_after(response.headers)
    return AutoIPApiError(response.status, code, message, retry_after=retry_after)


def _strict_retry_after(
    headers: Mapping[str, str | tuple[str, ...]],
) -> str | None:
    values = _header_values(headers, "retry-after")
    if not values:
        return None
    parsed: list[int] = []
    for value in values:
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})", value):
            return None
        seconds = int(value)
        if seconds > 86_400:
            return None
        parsed.append(seconds)
    if any(value != parsed[0] for value in parsed[1:]):
        return None
    return str(parsed[0])


def _definite_submission_rejection(response: HttpResponse) -> bool:
    """Return true only when the chargeable POST is known not to have a task.

    Provider 5xx, redirects, and HTTP 408 can arrive after upstream processing;
    treating those as retryable could double-charge.  Other 4xx responses are
    the documented validation/auth/credit/concurrency rejection family.
    """

    return 400 <= response.status < 500 and response.status != 408


def _required_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise AutoIPContractError(f"AutoIP {label} is invalid")
    return value


def _pricing_token(value: Any, label: str) -> str:
    """Validate an already-normalized pricing token without coercion."""

    if not isinstance(value, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_.:]{0,79}", value
    ):
        raise ValueError(f"pricing {label} is not an inert canonical token")
    return value


_TASK_STATE_BY_STATUS = {
    "created": TaskState.CREATED,
    "submitted": TaskState.SUBMITTED,
    "pending": TaskState.QUEUED,
    "queued": TaskState.QUEUED,
    "processing": TaskState.RUNNING,
    "running": TaskState.RUNNING,
    "completed": TaskState.COMPLETED,
    "failed": TaskState.FAILED,
    "cancelled": TaskState.CANCELLED,
    "timeout": TaskState.TIMEOUT,
}


def _task_state(value: Any) -> TaskState:
    if not isinstance(value, str):
        raise AutoIPContractError("AutoIP returned an unknown task status")
    state = _TASK_STATE_BY_STATUS.get(value)
    if state is None:
        # 不認得 ≠ 出事。對方隨時可以多一個中間狀態, 而把它丟成例外會讓整支
        # reconciliation service 死掉重啟 —— 拿停機去換一個我們沒見過的字, 不划算。
        # 一律當非終局繼續輪詢: 萬一它其實是終局失敗, 那一筆會停在 running,
        # 看得見、不扣點、也不擋別人送件, 比停機好。原字記在 receipt.detail。
        return TaskState.RUNNING
    return state


def _preserve_response_headers(native_headers: Any) -> Mapping[str, str | tuple[str, ...]]:
    if native_headers is None:
        return MappingProxyType({})
    raw_items = getattr(native_headers, "raw_items", None)
    items = raw_items() if callable(raw_items) else native_headers.items()
    collected: dict[str, list[str]] = {}
    for raw_name, raw_value in items:
        name = str(raw_name).casefold()
        collected.setdefault(name, []).append(str(raw_value))
    return MappingProxyType({
        name: values[0] if len(values) == 1 else tuple(values)
        for name, values in collected.items()
    })


def _header_values(
    headers: Mapping[str, str | tuple[str, ...]], name: str
) -> tuple[str, ...]:
    lowered = name.casefold()
    values: list[str] = []
    for key, raw_value in headers.items():
        if str(key).casefold() != lowered:
            continue
        if isinstance(raw_value, tuple):
            values.extend(str(value) for value in raw_value)
        else:
            values.append(str(raw_value))
    return tuple(values)


def _trusted_content_type(value: str) -> str:
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        raise AutoIPContractError("AutoIP output has an untrusted content type")
    content_type = str(value or "").split(";", 1)[0].strip().lower()
    if content_type not in _TRUSTED_OUTPUT_TYPES:
        raise AutoIPContractError("AutoIP output has an untrusted content type")
    return content_type


def _declared_content_type(
    headers: Mapping[str, str | tuple[str, ...]],
) -> str:
    values = _header_values(headers, "content-type")
    if not values:
        raise AutoIPContractError("AutoIP output has an untrusted content type")
    normalized = tuple(_trusted_content_type(value) for value in values)
    if any(value != normalized[0] for value in normalized[1:]):
        raise AutoIPContractError("AutoIP output has conflicting Content-Type headers")
    return normalized[0]


def _declared_content_length(
    headers: Mapping[str, str | tuple[str, ...]],
) -> int | None:
    values = _header_values(headers, "content-length")
    if not values:
        return None
    parsed: list[int] = []
    for value in values:
        if not re.fullmatch(r"[0-9]{1,20}", value):
            raise AutoIPContractError("AutoIP output has an invalid Content-Length")
        parsed.append(int(value))
    if any(value != parsed[0] for value in parsed[1:]):
        raise AutoIPContractError("AutoIP output has conflicting Content-Length headers")
    return parsed[0]


def _open_staging_root(staging_root: Path) -> tuple[Path, int]:
    if not staging_root.is_absolute():
        raise AutoIPContractError("staging_root must be an absolute path")
    if ".." in staging_root.parts:
        raise AutoIPContractError("staging_root cannot contain traversal")
    lexical = Path(os.path.abspath(staging_root))
    try:
        canonical = staging_root.resolve(strict=True)
        info = staging_root.lstat()
    except OSError as error:
        raise AutoIPContractError("staging_root must already exist") from error
    if lexical != canonical or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AutoIPContractError("staging_root cannot contain symlinks or traversal")
    if info.st_uid != os.geteuid():
        raise AutoIPContractError("staging_root must be owned by the service user")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise AutoIPContractError("staging_root cannot be group/world writable")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(canonical, flags)
    except OSError as error:
        raise AutoIPContractError("staging_root could not be opened securely") from error
    opened = os.fstat(root_fd)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        os.close(root_fd)
        raise AutoIPContractError("staging_root changed during validation")
    return canonical, root_fd


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written < 1:
            raise OSError("short write while staging AutoIP output")
        view = view[written:]


_IMAGE_FORMATS = MappingProxyType(
    {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/webp": "WEBP",
        "image/gif": "GIF",
        "image/avif": "AVIF",
    }
)
_AUDIO_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/flac",
        "audio/mp4",
        "audio/ogg",
    }
)
_VIDEO_TYPES = frozenset({"video/mp4", "video/quicktime", "video/webm"})
_IMAGE_VALIDATOR = r"""
import sys
from PIL import Image
Image.MAX_IMAGE_PIXELS = 100_000_000
expected = sys.argv[1]
stream = sys.stdin.buffer
with Image.open(stream) as image:
    if image.format != expected or image.width < 1 or image.height < 1:
        raise SystemExit(3)
    if image.width * image.height > Image.MAX_IMAGE_PIXELS:
        raise SystemExit(4)
    image.verify()
stream.seek(0)
with Image.open(stream) as image:
    if image.format != expected:
        raise SystemExit(5)
    image.load()
"""


def _validator_command(program: str, arguments: list[str]) -> list[str]:
    required = ("/usr/bin/prlimit", program)
    if any(not os.path.isfile(path) or not os.access(path, os.X_OK) for path in required):
        raise AutoIPContractError("required media validator is unavailable")
    return [
        "/usr/bin/prlimit",
        "--as=4294967296",
        "--cpu=120",
        "--nofile=64",
        "--core=0",
        "--",
        program,
        *arguments,
    ]


def _run_media_validator(
    command: list[str],
    fd: int,
    *,
    deadline: float,
    monotonic: Callable[[], float],
    expected_stdout: bytes | None = None,
) -> None:
    _check_deadline(deadline, monotonic)
    os.lseek(fd, 0, os.SEEK_SET)
    timeout = min(120.0, _request_timeout(deadline, monotonic, 120.0))
    try:
        completed = subprocess.run(
            command,
            stdin=fd,
            stdout=(subprocess.PIPE if expected_stdout is not None else subprocess.DEVNULL),
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONNOUSERSITE": "1",
            },
            timeout=timeout,
            check=False,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AutoIPContractError("complete media validation failed or timed out") from error
    _check_deadline(deadline, monotonic)
    if completed.returncode != 0:
        raise AutoIPContractError("complete media validation rejected the output")
    if expected_stdout is not None and completed.stdout.strip() != expected_stdout:
        raise AutoIPContractError("media stream type differs from Content-Type")


def _validate_complete_staged_media(
    root_fd: int,
    name: str,
    content_type: str,
    *,
    expected_info: os.stat_result,
    expected_size: int,
    deadline: float,
    monotonic: Callable[[], float],
) -> None:
    _check_deadline(deadline, monotonic)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
    except OSError as error:
        raise AutoIPContractError("staged output could not be opened for full validation") from error
    try:
        before = os.fstat(fd)
        _validate_staged_stat(before, expected_size)
        if (before.st_dev, before.st_ino) != (expected_info.st_dev, expected_info.st_ino):
            raise AutoIPContractError("staged output inode changed before media validation")
        if content_type in _IMAGE_FORMATS:
            command = _validator_command(
                sys.executable,
                ["-I", "-c", _IMAGE_VALIDATOR, _IMAGE_FORMATS[content_type]],
            )
            _run_media_validator(
                command, fd, deadline=deadline, monotonic=monotonic
            )
        elif content_type in _AUDIO_TYPES or content_type in _VIDEO_TYPES:
            expected_stream = b"audio" if content_type in _AUDIO_TYPES else b"video"
            selector = "a:0" if content_type in _AUDIO_TYPES else "v:0"
            probe = _validator_command(
                "/usr/bin/ffprobe",
                [
                    "-v", "error",
                    "-protocol_whitelist", "pipe",
                    "-select_streams", selector,
                    "-count_packets",
                    "-show_entries", "stream=codec_type",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    "pipe:0",
                ],
            )
            _run_media_validator(
                probe,
                fd,
                deadline=deadline,
                monotonic=monotonic,
                expected_stdout=expected_stream,
            )
            decoder = _validator_command(
                "/usr/bin/ffmpeg",
                [
                    "-v", "error",
                    "-nostdin",
                    "-protocol_whitelist", "pipe",
                    "-err_detect", "explode",
                    "-threads", "1",
                    "-filter_threads", "1",
                    "-filter_complex_threads", "1",
                    "-i", "pipe:0",
                    "-map", "0",
                    "-f", "null",
                    "-",
                ],
            )
            _run_media_validator(
                decoder, fd, deadline=deadline, monotonic=monotonic
            )
        else:
            raise AutoIPContractError("no complete validator exists for this media type")
        after = os.fstat(fd)
        _validate_staged_stat(after, expected_size)
        stable_fields = (
            "st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size",
            "st_mtime_ns", "st_ctime_ns",
        )
        if any(
            getattr(before, field_name) != getattr(after, field_name)
            for field_name in stable_fields
        ):
            raise AutoIPContractError("staged output changed during media validation")
    finally:
        os.close(fd)


def _validate_staged_stat(info: os.stat_result, expected_size: int) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise AutoIPContractError("staged output is not a regular file")
    if info.st_size != expected_size:
        raise AutoIPContractError("staged output size changed after rename")
    if info.st_uid != os.geteuid():
        raise AutoIPContractError("staged output owner is invalid")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise AutoIPContractError("staged output permissions are invalid")
    if info.st_nlink != 1:
        raise AutoIPContractError("staged output link count is invalid")


def _rehash_staged_output(
    root_fd: int,
    name: str,
    *,
    expected_info: os.stat_result,
    expected_size: int,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, str, bytes]:
    _check_deadline(deadline, monotonic)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
    except OSError as error:
        raise AutoIPContractError("staged output could not be reopened securely") from error
    try:
        before = os.fstat(fd)
        _validate_staged_stat(before, expected_size)
        if (before.st_dev, before.st_ino) != (expected_info.st_dev, expected_info.st_ino):
            raise AutoIPContractError("staged output inode changed after rename")
        digest = hashlib.sha256()
        probe = bytearray()
        total = 0
        while True:
            _check_deadline(deadline, monotonic)
            chunk = os.read(fd, 1024 * 1024)
            _check_deadline(deadline, monotonic)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise AutoIPContractError("staged output grew during verification")
            digest.update(chunk)
            remaining = SIGNATURE_PROBE_BYTES - len(probe)
            if remaining > 0:
                probe.extend(chunk[:remaining])
        after = os.fstat(fd)
        _validate_staged_stat(after, expected_size)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if total != expected_size or any(
            getattr(before, field_name) != getattr(after, field_name)
            for field_name in stable_fields
        ):
            raise AutoIPContractError("staged output changed during verification")
        return total, digest.hexdigest(), bytes(probe)
    finally:
        os.close(fd)


def autoip_config_from_env_reference(
    *,
    env_name: str = "CASTLE_AUTOIP_API_KEY",
    enabled: bool = False,
    api_base: str = AUTOIP_API_BASE,
) -> ProviderConfig:
    """Build a config containing an env reference, never the env value."""

    from .config import SecretKind, SecretReference

    return ProviderConfig(
        provider_id=AUTOIP_PROVIDER_ID,
        api_base=api_base,
        api_key_ref=SecretReference(SecretKind.ENV, env_name),
        enabled=enabled,
    )
