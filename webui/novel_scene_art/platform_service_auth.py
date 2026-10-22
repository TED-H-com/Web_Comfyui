"""Dormant, digest-only authentication for future Novel Scene Art routes.

This module is intentionally not imported by :mod:`webui.server`.  It contains
the complete request-boundary primitives needed by a later, separately
reviewed route integration, but importing it neither enables a route nor reads
configuration.  Activation is an explicit constructor argument and defaults
to false.

The server-side credential file contains PBKDF2-SHA256 digest records only.
The raw bearer credential is accepted only from an individual request's
``Authorization`` header and is never retained in a result or an exception.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import stat
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .platform_service_account import (
    EXPECTED_ROUTES,
    EXPECTED_SCOPES,
    SERVICE_PRINCIPAL,
)


DIGEST_SET_SCHEMA = "castle.novel-scene-art-service-account-digest-set.v1"
DIGEST_RECORD_SCHEMA = "castle.novel-scene-art-service-account-digest.v1"
DIGEST_PATH_ENV = "CASTLE_NOVEL_SCENE_ART_SERVICE_ACCOUNT_DIGEST_PATH"
PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
PBKDF2_SALT_BYTES = 32
PBKDF2_DIGEST_BYTES = 32
MAX_DIGEST_RECORD_BYTES = 16 * 1024
MAX_ACTIVE_DIGEST_RECORDS = 2
MAX_ROTATION_OVERLAP_SECONDS = 5 * 60

MAX_TOKEN_BYTES = 256
MIN_TOKEN_BYTES = 43
MAX_AUTHORIZATION_HEADER_BYTES = len("Bearer ") + MAX_TOKEN_BYTES
MAX_HEADER_COUNT = 64
MAX_HEADER_NAME_BYTES = 64
MAX_HEADER_VALUE_BYTES = 8 * 1024
MAX_HEADER_BYTES = 32 * 1024
MAX_METHOD_BYTES = 8
MAX_PATH_BYTES = 512
MAX_REQUEST_BODY_BYTES = 96 * 1024
MAX_OUTPUT_ACK_BODY_BYTES = 1024

PUBLIC_DENIAL_MESSAGE = "Service request denied"
PUBLIC_DENIAL_BODY = {"ok": False, "error": PUBLIC_DENIAL_MESSAGE}

# These are all headers named by the disabled platform contract.  The prefix
# check below additionally rejects future X-Forwarded-* variants.
FORBIDDEN_FORWARDING_HEADERS = (
    "CF-Connecting-IP",
    "CF-Ray",
    "Cf-Access-Authenticated-User-Email",
    "Cf-Access-Jwt-Assertion",
    "Forwarded",
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Proto",
)
FORBIDDEN_IDENTITY_HEADERS = (
    "Cookie",
    "Proxy-Authorization",
    "X-Api-Key",
    "X-Auth-Token",
    "X-Castle-Orchestrator-Token",
)
LOCAL_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

_DIGEST_SET_KEYS = frozenset({"schema_version", "records"})
_DIGEST_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "key_id",
        "principal_id",
        "algorithm",
        "iterations",
        "salt_b64",
        "digest_b64",
        "scopes",
        "created_at",
        "not_before",
        "expires_at",
        "revoked_at",
        "replaced_by_key_id",
    }
)
_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$", re.ASCII)
_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{43,256}$", re.ASCII)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$", re.ASCII)
_JOB_ID = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}"
_MAX_TIMESTAMP = 253_402_300_799
_DUMMY_TOKEN = b"d" * MIN_TOKEN_BYTES
_DUMMY_SALT = hashlib.sha256(b"web-comfyui-novel-scene-art-dummy-salt-v1").digest()
_DUMMY_DIGEST = hashlib.sha256(b"web-comfyui-novel-scene-art-dummy-digest-v1").digest()
_FORBIDDEN_FORWARDING_NORMALIZED = frozenset(
    name.lower() for name in FORBIDDEN_FORWARDING_HEADERS
)
_FORBIDDEN_IDENTITY_NORMALIZED = frozenset(
    name.lower() for name in FORBIDDEN_IDENTITY_HEADERS
)


class ServiceAuthConfigurationError(ValueError):
    """A server-side digest set is malformed or stored unsafely."""


class ServiceRequestDenied(PermissionError):
    """A request failed the private service boundary.

    The message and public response are deliberately identical for every
    failure.  Callers must not expose configuration, key lifecycle, route,
    ownership, or credential parsing detail.
    """

    __slots__ = ()
    public_status = 401

    def __init__(self) -> None:
        super().__init__(PUBLIC_DENIAL_MESSAGE)

    def public_response(self) -> dict[str, object]:
        return dict(PUBLIC_DENIAL_BODY)


def _configuration_error(label: str) -> "NoReturn":
    raise ServiceAuthConfigurationError(f"service digest configuration invalid: {label}")


def _request_denied() -> "NoReturn":
    raise ServiceRequestDenied()


from typing import NoReturn  # noqa: E402 (keeps the public exceptions together)


def _timestamp(value: object, label: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_TIMESTAMP:
        _configuration_error(label)
    return value


def _decode_base64url(value: object, expected_bytes: int, label: str) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or re.fullmatch(r"[A-Za-z0-9_-]+", value, re.ASCII) is None
    ):
        _configuration_error(label)
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (TypeError, ValueError) as error:
        raise ServiceAuthConfigurationError(
            f"service digest configuration invalid: {label}"
        ) from error
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != expected_bytes or not hmac.compare_digest(canonical, value):
        _configuration_error(label)
    return decoded


@dataclass(frozen=True, slots=True)
class ServiceAccountDigestRecord:
    """One bounded, expiring server-side digest; never a plaintext token."""

    key_id: str
    principal_id: str
    algorithm: str
    iterations: int
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)
    scopes: tuple[str, ...]
    created_at: int
    not_before: int
    expires_at: int
    revoked_at: int | None
    replaced_by_key_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or _KEY_ID.fullmatch(self.key_id) is None:
            _configuration_error("key_id")
        if self.principal_id != SERVICE_PRINCIPAL:
            _configuration_error("principal_id")
        if self.algorithm != PBKDF2_ALGORITHM:
            _configuration_error("algorithm")
        if type(self.iterations) is not int or self.iterations != PBKDF2_ITERATIONS:
            _configuration_error("iterations")
        if not isinstance(self.salt, bytes) or len(self.salt) != PBKDF2_SALT_BYTES:
            _configuration_error("salt_b64")
        if not isinstance(self.digest, bytes) or len(self.digest) != PBKDF2_DIGEST_BYTES:
            _configuration_error("digest_b64")
        if self.scopes != EXPECTED_SCOPES:
            _configuration_error("scopes")
        for value, label in (
            (self.created_at, "created_at"),
            (self.not_before, "not_before"),
            (self.expires_at, "expires_at"),
        ):
            _timestamp(value, label)
        if not self.created_at <= self.not_before < self.expires_at:
            _configuration_error("lifecycle")
        if self.revoked_at is not None:
            _timestamp(self.revoked_at, "revoked_at")
            if not self.created_at <= self.revoked_at <= self.expires_at:
                _configuration_error("revoked_at")
        if self.replaced_by_key_id is not None and (
            not isinstance(self.replaced_by_key_id, str)
            or _KEY_ID.fullmatch(self.replaced_by_key_id) is None
            or self.replaced_by_key_id == self.key_id
        ):
            _configuration_error("replaced_by_key_id")

    def eligible_at(self, now: int) -> bool:
        return (
            self.revoked_at is None
            and self.not_before <= now
            and now < self.expires_at
        )

    def __repr__(self) -> str:
        return (
            "ServiceAccountDigestRecord("
            f"key_id={self.key_id!r}, principal_id={self.principal_id!r}, "
            "algorithm='pbkdf2_sha256', digest=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class ServiceAccountDigestSet:
    """An atomic one-or-two-key rotation window."""

    records: tuple[ServiceAccountDigestRecord, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.records, tuple)
            or not 1 <= len(self.records) <= MAX_ACTIVE_DIGEST_RECORDS
            or any(not isinstance(record, ServiceAccountDigestRecord) for record in self.records)
        ):
            _configuration_error("records")
        key_ids = tuple(record.key_id for record in self.records)
        if len(set(key_ids)) != len(key_ids):
            _configuration_error("key_id uniqueness")
        ordered = tuple(
            sorted(
                self.records,
                key=lambda record: (record.not_before, record.created_at, record.key_id),
            )
        )
        if self.records != ordered:
            _configuration_error("record order")
        if len(self.records) == 1:
            if self.records[0].replaced_by_key_id is not None:
                _configuration_error("replacement chain")
            return
        old, new = self.records
        if old.replaced_by_key_id != new.key_id or new.replaced_by_key_id is not None:
            _configuration_error("replacement chain")
        overlap = max(
            0,
            min(old.expires_at, new.expires_at)
            - max(old.not_before, new.not_before),
        )
        if overlap > MAX_ROTATION_OVERLAP_SECONDS:
            _configuration_error("rotation overlap")

    def __repr__(self) -> str:
        key_ids = tuple(record.key_id for record in self.records)
        return f"ServiceAccountDigestSet(key_ids={key_ids!r}, digests=<redacted>)"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_invalid_unicode(value: object) -> None:
    if isinstance(value, str):
        value.encode("utf-8", "strict")
    elif isinstance(value, list):
        for item in value:
            _reject_invalid_unicode(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            key.encode("utf-8", "strict")
            _reject_invalid_unicode(item)


def _exact_object(value: object, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _configuration_error(label)
    return value


def parse_digest_set(raw: bytes) -> ServiceAccountDigestSet:
    """Parse an exact, bounded digest-set document with recursive duplicate checks."""

    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_DIGEST_RECORD_BYTES:
        _configuration_error("document size")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        _reject_invalid_unicode(value)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ServiceAuthConfigurationError(
            "service digest configuration invalid: JSON"
        ) from error
    document = _exact_object(value, _DIGEST_SET_KEYS, "document shape")
    if document["schema_version"] != DIGEST_SET_SCHEMA:
        _configuration_error("schema_version")
    rows = document["records"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ACTIVE_DIGEST_RECORDS:
        _configuration_error("records")

    records: list[ServiceAccountDigestRecord] = []
    for index, raw_record in enumerate(rows):
        record = _exact_object(raw_record, _DIGEST_RECORD_KEYS, f"records[{index}]")
        if record["schema_version"] != DIGEST_RECORD_SCHEMA:
            _configuration_error(f"records[{index}].schema_version")
        scopes = record["scopes"]
        if (
            not isinstance(scopes, list)
            or any(not isinstance(scope, str) for scope in scopes)
            or tuple(scopes) != EXPECTED_SCOPES
        ):
            _configuration_error(f"records[{index}].scopes")
        records.append(
            ServiceAccountDigestRecord(
                key_id=record["key_id"],
                principal_id=record["principal_id"],
                algorithm=record["algorithm"],
                iterations=record["iterations"],
                salt=_decode_base64url(
                    record["salt_b64"], PBKDF2_SALT_BYTES, f"records[{index}].salt_b64"
                ),
                digest=_decode_base64url(
                    record["digest_b64"],
                    PBKDF2_DIGEST_BYTES,
                    f"records[{index}].digest_b64",
                ),
                scopes=tuple(scopes),
                created_at=record["created_at"],
                not_before=record["not_before"],
                expires_at=record["expires_at"],
                revoked_at=record["revoked_at"],
                replaced_by_key_id=record["replaced_by_key_id"],
            )
        )
    return ServiceAccountDigestSet(tuple(records))


def _read_bounded_regular_file(path: Path, *, expected_uid: int | None) -> bytes:
    if (
        not path.is_absolute()
        or "\x00" in os.fspath(path)
        or path.name in {"", ".", ".."}
    ):
        _configuration_error("digest path")
    common_flags = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | common_flags | getattr(os, "O_DIRECTORY", 0)
    file_flags = os.O_RDONLY | common_flags | getattr(os, "O_NONBLOCK", 0)
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor = os.open(path.parent, directory_flags)
        parent_info = os.fstat(parent_descriptor)
        owner_uid = os.geteuid() if expected_uid is None else expected_uid
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != owner_uid
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            _configuration_error("digest parent security")
        descriptor = os.open(path.name, file_flags, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > MAX_DIGEST_RECORD_BYTES
        ):
            _configuration_error("digest file security")
        chunks: list[bytes] = []
        remaining = MAX_DIGEST_RECORD_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or after.st_ino != before.st_ino
            or after.st_dev != before.st_dev
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            _configuration_error("digest file stability")
        return raw
    except ServiceAuthConfigurationError:
        raise
    except OSError as error:
        raise ServiceAuthConfigurationError(
            "service digest configuration invalid: digest file unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def load_digest_set(
    path: str | os.PathLike[str], *, expected_uid: int | None = None
) -> ServiceAccountDigestSet:
    try:
        selected = Path(path)
    except (TypeError, ValueError, OSError) as error:
        raise ServiceAuthConfigurationError(
            "service digest configuration invalid: digest path"
        ) from error
    return parse_digest_set(_read_bounded_regular_file(selected, expected_uid=expected_uid))


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    principal_id: str
    scopes: tuple[str, ...]
    key_id: str


def _bearer_candidate(header: object) -> tuple[bytes, bool]:
    """Return fixed dummy material on every syntax failure, without echoing input."""

    if not isinstance(header, str) or len(header) > MAX_AUTHORIZATION_HEADER_BYTES:
        return _DUMMY_TOKEN, False
    try:
        encoded = header.encode("ascii", "strict")
    except UnicodeError:
        return _DUMMY_TOKEN, False
    if len(encoded) > MAX_AUTHORIZATION_HEADER_BYTES or not encoded.startswith(b"Bearer "):
        return _DUMMY_TOKEN, False
    token = encoded[len(b"Bearer ") :]
    if (
        not MIN_TOKEN_BYTES <= len(token) <= MAX_TOKEN_BYTES
        or _TOKEN.fullmatch(token.decode("ascii")) is None
    ):
        return _DUMMY_TOKEN, False
    return token, True


def _now(value: int | float | None) -> int:
    selected = time.time() if value is None else value
    if (
        isinstance(selected, bool)
        or not isinstance(selected, (int, float))
        or not math.isfinite(selected)
        or selected < 0
        or selected > _MAX_TIMESTAMP
    ):
        _request_denied()
    return int(selected)


class ServiceAccountAuthenticator:
    """Verify exact Bearer credentials against at most two digest records."""

    __slots__ = ("_digest_set", "_activation_enabled")

    def __init__(
        self,
        digest_set: ServiceAccountDigestSet | None = None,
        *,
        activation_enabled: bool = False,
    ) -> None:
        if digest_set is not None and not isinstance(digest_set, ServiceAccountDigestSet):
            _configuration_error("digest set type")
        if type(activation_enabled) is not bool:
            _configuration_error("activation flag")
        self._digest_set = digest_set
        self._activation_enabled = activation_enabled

    @property
    def enabled(self) -> bool:
        return self._activation_enabled and self._digest_set is not None

    def authenticate(
        self,
        authorization_header: object,
        *,
        now: int | float | None = None,
    ) -> ServicePrincipal:
        # Disabled is public deployment state, not secret credential state.  It
        # fails before a deliberately expensive KDF and cannot be enabled by an
        # environment variable.
        if not self.enabled:
            _request_denied()
        assert self._digest_set is not None
        candidate, syntax_valid = _bearer_candidate(authorization_header)
        current = _now(now)
        records = self._digest_set.records
        matched: ServiceAccountDigestRecord | None = None

        # Always verify exactly two slots once configured and enabled.  A
        # one-record set uses a fixed dummy second slot.  Invalid Bearer syntax
        # uses fixed dummy candidate material.  There is no key-position or
        # malformed-header early return.
        for index in range(MAX_ACTIVE_DIGEST_RECORDS):
            record = records[index] if index < len(records) else None
            salt = record.salt if record is not None else _DUMMY_SALT
            expected = record.digest if record is not None else _DUMMY_DIGEST
            actual = hashlib.pbkdf2_hmac(
                "sha256", candidate, salt, PBKDF2_ITERATIONS, dklen=PBKDF2_DIGEST_BYTES
            )
            digest_matches = hmac.compare_digest(actual, expected)
            eligible = record is not None and record.eligible_at(current)
            accepted = bool(digest_matches & eligible & syntax_valid)
            if accepted and matched is None:
                matched = record
        if matched is None:
            _request_denied()
        return ServicePrincipal(
            principal_id=matched.principal_id,
            scopes=matched.scopes,
            key_id=matched.key_id,
        )


def authenticator_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    activation_enabled: bool = False,
    expected_uid: int | None = None,
) -> ServiceAccountAuthenticator:
    """Load only the digest path; raw service-token environment keys are invalid."""

    source = os.environ if environ is None else environ
    if not isinstance(source, Mapping):
        _configuration_error("environment")
    prefix = "CASTLE_NOVEL_SCENE_ART_SERVICE_ACCOUNT_"
    for key in source:
        if (
            isinstance(key, str)
            and key.startswith(prefix)
            and key != DIGEST_PATH_ENV
        ):
            _configuration_error("raw token environment")
    locator = source.get(DIGEST_PATH_ENV)
    if locator is None:
        if activation_enabled:
            _configuration_error("digest path missing")
        return ServiceAccountAuthenticator(activation_enabled=False)
    if (
        not isinstance(locator, str)
        or not locator
        or len(locator) > 4096
        or "\x00" in locator
    ):
        _configuration_error("digest path")
    digest_set = load_digest_set(locator, expected_uid=expected_uid)
    return ServiceAccountAuthenticator(
        digest_set,
        activation_enabled=activation_enabled,
    )


@dataclass(frozen=True, slots=True)
class _BoundedHeaders:
    values: Mapping[str, tuple[str, ...]]

    def all(self, name: str) -> tuple[str, ...]:
        return self.values.get(name.lower(), ())


HeaderSource = Mapping[str, str] | Iterable[tuple[str, str]]


def _bounded_headers(headers: HeaderSource) -> _BoundedHeaders:
    if isinstance(headers, (str, bytes, bytearray)):
        _request_denied()
    raw_items = getattr(headers, "raw_items", None)
    try:
        if callable(raw_items):
            iterator = iter(raw_items())
        elif isinstance(headers, Mapping):
            iterator = iter(headers.items())
        else:
            iterator = iter(headers)
    except (TypeError, ValueError):
        _request_denied()

    collected: dict[str, list[str]] = {}
    total = 0
    try:
        for count, item in enumerate(iterator, start=1):
            if count > MAX_HEADER_COUNT or not isinstance(item, tuple) or len(item) != 2:
                _request_denied()
            name, value = item
            if (
                not isinstance(name, str)
                or not name
                or len(name) > MAX_HEADER_NAME_BYTES
                or not name.isascii()
                or _HEADER_NAME.fullmatch(name) is None
                or not isinstance(value, str)
                or len(value) > MAX_HEADER_VALUE_BYTES
                or not value.isascii()
                or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
            ):
                _request_denied()
            total += len(name) + len(value) + 4
            if total > MAX_HEADER_BYTES:
                _request_denied()
            collected.setdefault(name.lower(), []).append(value)
    except ServiceRequestDenied:
        raise
    except Exception:
        _request_denied()
    return _BoundedHeaders({key: tuple(values) for key, values in collected.items()})


def _has_forwarding_headers(headers: _BoundedHeaders) -> bool:
    return any(
        name in _FORBIDDEN_FORWARDING_NORMALIZED or name.startswith("x-forwarded-")
        for name in headers.values
    )


def is_direct_local_peer(peer_host: object, headers: HeaderSource) -> bool:
    """Return true only for an exact loopback peer with no forwarding evidence."""

    try:
        bounded = _bounded_headers(headers)
    except ServiceRequestDenied:
        return False
    return (
        isinstance(peer_host, str)
        and peer_host in LOCAL_PEERS
        and not _has_forwarding_headers(bounded)
    )


@dataclass(frozen=True, slots=True)
class RouteRule:
    method: str
    path_template: str
    scope: str
    max_body_bytes: int
    existing_owner_required: bool


ROUTE_RULES = (
    RouteRule(*EXPECTED_ROUTES[0], max_body_bytes=0, existing_owner_required=False),
    RouteRule(
        *EXPECTED_ROUTES[1],
        max_body_bytes=MAX_REQUEST_BODY_BYTES,
        existing_owner_required=False,
    ),
    RouteRule(*EXPECTED_ROUTES[2], max_body_bytes=0, existing_owner_required=True),
    RouteRule(*EXPECTED_ROUTES[3], max_body_bytes=0, existing_owner_required=True),
    RouteRule(*EXPECTED_ROUTES[4], max_body_bytes=0, existing_owner_required=True),
    RouteRule(
        *EXPECTED_ROUTES[5],
        max_body_bytes=MAX_OUTPUT_ACK_BODY_BYTES,
        existing_owner_required=True,
    ),
)


def _route_pattern(template: str) -> re.Pattern[str]:
    marker = re.escape("{job_id}")
    escaped = re.escape(template)
    if "{job_id}" not in template:
        return re.compile(rf"^{escaped}$", re.ASCII)
    return re.compile(
        rf"^{escaped.replace(marker, f'(?P<job_id>{_JOB_ID})')}$",
        re.ASCII,
    )


_COMPILED_ROUTES = tuple((rule, _route_pattern(rule.path_template)) for rule in ROUTE_RULES)


@dataclass(frozen=True, slots=True)
class AuthorizedServiceRequest:
    principal_id: str
    key_id: str
    scope: str
    route_template: str
    job_id: str | None
    owner_principal: str
    max_body_bytes: int
    atomic_owner_lookup_required: bool

    def parse_json_body(self, body: bytes) -> Mapping[str, Any]:
        return parse_bounded_json_object(body, maximum=self.max_body_bytes)

    def owned_resource_key(self) -> tuple[str, str]:
        """Bind a future atomic store query to the authenticated owner and job.

        This does not look up a resource.  A future store integration must use
        both returned operands in one query/mutation and map both missing and
        non-owned rows to the same response.  The HTTP caller never supplies an
        owner parameter.
        """

        if not self.atomic_owner_lookup_required or self.job_id is None:
            _request_denied()
        return self.owner_principal, self.job_id


def _match_route(method: object, path: object) -> tuple[RouteRule, str | None]:
    if (
        not isinstance(method, str)
        or not method
        or len(method) > MAX_METHOD_BYTES
        or not method.isascii()
        or method not in {"GET", "POST"}
        or not isinstance(path, str)
        or not path
        or len(path) > MAX_PATH_BYTES
        or not path.isascii()
        or any(character in path for character in ("?", "#", "\\", "%", "\x00"))
        or "//" in path
    ):
        _request_denied()
    for rule, pattern in _COMPILED_ROUTES:
        if method != rule.method:
            continue
        matched = pattern.fullmatch(path)
        if matched is not None:
            job_id = matched.groupdict().get("job_id")
            if job_id in {".", ".."}:
                _request_denied()
            return rule, job_id
    _request_denied()


def _validate_body_envelope(
    body: object,
    headers: _BoundedHeaders,
    maximum: int,
) -> bytes:
    if not isinstance(body, bytes) or len(body) > maximum or len(body) > MAX_REQUEST_BODY_BYTES:
        _request_denied()
    if headers.all("transfer-encoding"):
        _request_denied()
    lengths = headers.all("content-length")
    if len(lengths) > 1:
        _request_denied()
    if lengths:
        value = lengths[0]
        if re.fullmatch(r"0|[1-9][0-9]{0,7}", value, re.ASCII) is None:
            _request_denied()
        if int(value) != len(body):
            _request_denied()
    elif body:
        _request_denied()
    return body


def parse_bounded_json_object(raw: bytes, *, maximum: int) -> Mapping[str, Any]:
    """Parse an already-authorized JSON object without duplicate/non-finite values."""

    if (
        type(maximum) is not int
        or maximum <= 0
        or maximum > MAX_REQUEST_BODY_BYTES
        or not isinstance(raw, bytes)
        or not raw
        or len(raw) > maximum
    ):
        _request_denied()
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        _reject_invalid_unicode(value)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        _request_denied()
    if not isinstance(value, dict):
        _request_denied()
    return value


class ServiceAccountRouteAuthorizer:
    """Authorize only the six exact local service-account route templates."""

    __slots__ = ("_authenticator",)

    def __init__(self, authenticator: ServiceAccountAuthenticator) -> None:
        if not isinstance(authenticator, ServiceAccountAuthenticator):
            _configuration_error("authenticator")
        self._authenticator = authenticator

    def authorize(
        self,
        *,
        method: object,
        path: object,
        peer_host: object,
        headers: HeaderSource,
        body: object = b"",
        now: int | float | None = None,
    ) -> AuthorizedServiceRequest:
        bounded = _bounded_headers(headers)
        if (
            not isinstance(peer_host, str)
            or peer_host not in LOCAL_PEERS
            or _has_forwarding_headers(bounded)
        ):
            _request_denied()

        authorization_values = bounded.all("authorization")
        authorization = authorization_values[0] if len(authorization_values) == 1 else None
        principal = self._authenticator.authenticate(authorization, now=now)

        # A valid service Bearer may not be combined with a browser session,
        # proxy credential, API key, or an orchestrator-specific header.
        if any(name in bounded.values for name in _FORBIDDEN_IDENTITY_NORMALIZED):
            _request_denied()

        rule, job_id = _match_route(method, path)
        _validate_body_envelope(body, bounded, rule.max_body_bytes)
        if rule.scope not in principal.scopes:
            _request_denied()

        return AuthorizedServiceRequest(
            principal_id=principal.principal_id,
            key_id=principal.key_id,
            scope=rule.scope,
            route_template=rule.path_template,
            job_id=job_id,
            owner_principal=principal.principal_id,
            max_body_bytes=rule.max_body_bytes,
            atomic_owner_lookup_required=rule.existing_owner_required,
        )


__all__ = [
    "AuthorizedServiceRequest",
    "DIGEST_PATH_ENV",
    "DIGEST_RECORD_SCHEMA",
    "DIGEST_SET_SCHEMA",
    "FORBIDDEN_FORWARDING_HEADERS",
    "FORBIDDEN_IDENTITY_HEADERS",
    "LOCAL_PEERS",
    "MAX_ACTIVE_DIGEST_RECORDS",
    "MAX_AUTHORIZATION_HEADER_BYTES",
    "MAX_DIGEST_RECORD_BYTES",
    "MAX_HEADER_BYTES",
    "MAX_HEADER_COUNT",
    "MAX_HEADER_NAME_BYTES",
    "MAX_HEADER_VALUE_BYTES",
    "MAX_OUTPUT_ACK_BODY_BYTES",
    "MAX_REQUEST_BODY_BYTES",
    "MAX_ROTATION_OVERLAP_SECONDS",
    "MAX_TOKEN_BYTES",
    "MIN_TOKEN_BYTES",
    "PBKDF2_ALGORITHM",
    "PBKDF2_DIGEST_BYTES",
    "PBKDF2_ITERATIONS",
    "PBKDF2_SALT_BYTES",
    "PUBLIC_DENIAL_BODY",
    "PUBLIC_DENIAL_MESSAGE",
    "ROUTE_RULES",
    "RouteRule",
    "ServiceAccountAuthenticator",
    "ServiceAccountDigestRecord",
    "ServiceAccountDigestSet",
    "ServiceAccountRouteAuthorizer",
    "ServiceAuthConfigurationError",
    "ServicePrincipal",
    "ServiceRequestDenied",
    "authenticator_from_environment",
    "is_direct_local_peer",
    "load_digest_set",
    "parse_bounded_json_object",
    "parse_digest_set",
]
