"""Dormant owner-private vault for canonical Novel Scene Art requests.

This module is deliberately not imported by :mod:`webui.server`.  It performs
no HTTP, network, ComfyUI, GPU, or database work.  A later coordinator may use
it to retain the already-validated canonical submit envelope while a durable
job is waiting to be dispatched or reconciled.

The vault is intentionally narrower than a generic file store:

* the root is a pre-provisioned private directory outside ComfyUI's view dirs;
* callers supply neither paths nor filenames and there is no listing API;
* owner and job path components are keyed HMAC values;
* one immutable file contains a canonical manifest followed by the raw
  envelope, so publication has a single atomic no-replace commit point; and
* deletion requires an opaque MAC-authenticated handle issued by this store.

Raw envelopes are kept only in the private file payload.  They are excluded
from dataclass representations and are never interpolated into exceptions.
"""

from __future__ import annotations

import errno
if '.' in (__package__ or ''):
    from .. import file_lock as fcntl
else:  # top-level webui package layout
    import file_lock as fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import struct
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, NoReturn, Sequence, TypeAlias

from .platform_service_auth import (
    AuthorizedServiceRequest,
    MAX_REQUEST_BODY_BYTES,
    ServicePrincipal,
)


DEFAULT_PRIVATE_REQUEST_ROOT = Path(
    "/var/lib/web-comfyui-novel-scene-art/private/requests"
)
DEFAULT_MANAGED_ANCESTOR = DEFAULT_PRIVATE_REQUEST_ROOT.parent
MAX_CANONICAL_ENVELOPE_BYTES = MAX_REQUEST_BODY_BYTES
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
MAX_ORPHAN_TEMPORARIES = 32
MAX_LAYOUT_ENTRIES = 100_000

_SCHEMA = "castle.novel-scene-art-private-request.v1"
_MAGIC = b"CSARREQ1"
_HEADER = struct.Struct(">8sI")
_MAX_MANIFEST_BYTES = 1024
RECORD_OVERHEAD_BYTES = _HEADER.size + _MAX_MANIFEST_BYTES
_MAX_RECORD_BYTES = RECORD_OVERHEAD_BYTES + MAX_CANONICAL_ENVELOPE_BYTES
_IDENTITY_BYTES = 129
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$", re.ASCII)
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_OWNER_BINDING_RE = _DIGEST_RE
_JOB_BINDING_RE = _DIGEST_RE
_LOCATOR_RE = re.compile(r"^req_[a-f0-9]{64}$", re.ASCII)
_INCARNATION_RE = re.compile(r"^reqinc_[a-f0-9]{32}$", re.ASCII)
_FINAL_RE = re.compile(r"^(?P<binding>[a-f0-9]{64})\.vault$", re.ASCII)
_TEMP_RE = re.compile(r"^\.tmp-[a-f0-9]{32}$", re.ASCII)
_IDENTITY_TEMP_RE = re.compile(r"^\.identity\.tmp-[a-f0-9]{32}$", re.ASCII)
_ROOT_ENTRIES = frozenset({".identity", ".request-vault.lock", "owners"})
_SUBMIT_SCOPE = "scene_art.job.submit-own"
_SUBMIT_ROUTE = "/api/internal/novel-scene-art/v1/jobs"

_IDENTITY_DOMAIN = b"web-comfyui-novel-scene-art-request-vault-identity-v1\0"
_OWNER_DOMAIN = b"web-comfyui-novel-scene-art-request-vault-owner-v1\0"
_JOB_DOMAIN = b"web-comfyui-novel-scene-art-request-vault-job-v1\0"
_HANDLE_DOMAIN = b"web-comfyui-novel-scene-art-request-vault-handle-v1\0"


class RequestVaultError(RuntimeError):
    """Base class for bounded, non-sensitive request-vault failures."""


class RequestVaultConfigurationError(RequestVaultError):
    """The trusted root, UID, namespace key, or retained binding is unsafe."""


class RequestVaultAuthorizationError(RequestVaultError):
    """A nominal authentication context cannot authorize this operation."""


class RequestVaultInvalid(RequestVaultError):
    """Canonical request bytes or their declared identifiers are malformed."""


class RequestVaultConflict(RequestVaultError):
    """The owner/job locator already retains a different canonical request."""


class RequestVaultNotFound(RequestVaultError):
    """The exact owner/job/digest/size binding is unavailable."""


class RequestVaultCorrupt(RequestVaultError):
    """Private storage failed an ownership, mode, binding, or digest check."""


class RequestVaultInvalidHandle(RequestVaultError):
    """A read or delete did not carry a handle issued by this exact vault."""


class RequestVaultStaleHandle(RequestVaultError):
    """A valid handle names an older incarnation of a recreated request."""


@dataclass(frozen=True, slots=True)
class RequestVaultHandle:
    """Opaque authority for one exact owner/job/request binding.

    Sensitive routing inputs and the MAC remain hidden from ``repr``.  The
    locator is an HMAC output, not a filename supplied by a caller.
    """

    locator: str
    canonical_request_sha256: str
    byte_size: int
    storage_incarnation: str
    _owner_principal: str = field(repr=False)
    _job_id: str = field(repr=False)
    _generation_key: str = field(repr=False)
    _store_id: str = field(repr=False)
    _auth_tag: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PublishedRequest:
    handle: RequestVaultHandle
    created: bool


@dataclass(frozen=True, slots=True)
class _Manifest:
    store_id: str
    owner_binding: str
    job_binding: str
    generation_key: str
    canonical_request_sha256: str
    byte_size: int
    storage_incarnation: str

    def encoded(self) -> bytes:
        return json.dumps(
            {
                "byte_size": self.byte_size,
                "canonical_request_sha256": self.canonical_request_sha256,
                "generation_key": self.generation_key,
                "job_binding": self.job_binding,
                "owner_binding": self.owner_binding,
                "schema_version": _SCHEMA,
                "storage_incarnation": self.storage_incarnation,
                "store_id": self.store_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")


AuthContext: TypeAlias = ServicePrincipal | AuthorizedServiceRequest


def _configuration_error() -> NoReturn:
    raise RequestVaultConfigurationError("Private request vault configuration is invalid")


def _corrupt() -> NoReturn:
    raise RequestVaultCorrupt("Private request vault integrity check failed")


def _canonical_absolute(
    value: str | os.PathLike[str], label: str
) -> Path:
    try:
        raw = os.fspath(value)
        encoded = raw.encode("utf-8", "strict") if isinstance(raw, str) else b""
    except (TypeError, ValueError, OSError, UnicodeError):
        raise RequestVaultConfigurationError(
            f"{label} must be one canonical absolute path"
        ) from None
    if (
        not isinstance(raw, str)
        or not raw
        or len(encoded) > 4096
        or "\x00" in raw
        or not raw.startswith("/")
        or raw.startswith("//")
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise RequestVaultConfigurationError(
            f"{label} must be one canonical absolute path"
        )
    return Path(raw)


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _read_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _create_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _snapshot(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _validate_directory(
    fd: int, *, expected_uid: int, device: int | None = None
) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != DIRECTORY_MODE
        or (device is not None and info.st_dev != device)
    ):
        _corrupt()
    return info


def _validate_file(
    fd: int,
    *,
    expected_uid: int,
    device: int,
    minimum: int = 0,
    maximum: int | None = None,
    exact_size: int | None = None,
    allowed_links: frozenset[int] = frozenset({1}),
) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != FILE_MODE
        or info.st_dev != device
        or info.st_nlink not in allowed_links
        or info.st_size < minimum
        or (maximum is not None and info.st_size > maximum)
        or (exact_size is not None and info.st_size != exact_size)
    ):
        _corrupt()
    return info


def _absolute_directory_chain(path: Path) -> list[tuple[Path, int, os.stat_result]]:
    current = os.open(os.path.sep, _directory_flags())
    result = [(Path(os.path.sep), current, os.fstat(current))]
    built = Path(os.path.sep)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."} or "/" in component or "\x00" in component:
                _configuration_error()
            built = built / component
            current = os.open(component, _directory_flags(), dir_fd=current)
            info = os.fstat(current)
            if not stat.S_ISDIR(info.st_mode):
                _configuration_error()
            result.append((built, current, info))
        return result
    except BaseException:
        for _path, fd, _info in reversed(result):
            os.close(fd)
        raise


def _close_chain(chain: Sequence[tuple[Path, int, os.stat_result]]) -> None:
    for _path, fd, _info in reversed(chain):
        os.close(fd)


def _write_all(fd: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(fd, value[offset : offset + 1024 * 1024])
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        offset += written


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _scanned_names(fd: int, *, maximum: int) -> Iterator[str]:
    """Yield directory names incrementally and stop before an unbounded scan."""

    if type(maximum) is not int or maximum < 0:
        _corrupt()
    scanner = None
    try:
        scanner = os.scandir(fd)
        count = 0
        for entry in scanner:
            count += 1
            if count > maximum:
                _corrupt()
            name = entry.name
            if not isinstance(name, str) or not name or "\x00" in name:
                _corrupt()
            yield name
    except RequestVaultError:
        raise
    except OSError as error:
        raise RequestVaultCorrupt(
            "Private request vault directory cannot be scanned safely"
        ) from error
    finally:
        if scanner is not None:
            scanner.close()


@dataclass(slots=True)
class _ProcessLock:
    mutex: threading.RLock = field(default_factory=threading.RLock)
    references: int = 0
    active_operation: object | None = None


_LOCK_REGISTRY_GUARD = threading.RLock()
_LOCK_REGISTRY: dict[tuple[int, int], _ProcessLock] = {}
_LOCK_REGISTRY_PID = os.getpid()
_OPEN_VAULTS: weakref.WeakSet[object] = weakref.WeakSet()
_FORKED_ACTIVE_LOCK_IDENTITIES: frozenset[tuple[int, int]] = frozenset()
# Reentrancy lets an idle/coordination-thread fork reach the child invalidation
# hook.  Every region that holds this guard while transient vault descriptors
# exist performs internal bounded filesystem work only: it has no caller
# callback, DB, or network seam capable of initiating same-thread fork.
_FORK_DESCRIPTOR_GUARD = threading.RLock()


def _before_fork() -> None:
    _FORK_DESCRIPTOR_GUARD.acquire()


def _after_fork_parent() -> None:
    _FORK_DESCRIPTOR_GUARD.release()


def _reset_lock_registry_after_fork() -> None:
    global _LOCK_REGISTRY_GUARD
    global _LOCK_REGISTRY
    global _LOCK_REGISTRY_PID
    global _OPEN_VAULTS
    global _FORKED_ACTIVE_LOCK_IDENTITIES
    global _FORK_DESCRIPTOR_GUARD

    inherited = tuple(_OPEN_VAULTS)
    active_identities = set(_FORKED_ACTIVE_LOCK_IDENTITIES)
    for vault in inherited:
        identity = getattr(vault, "_lock_identity", None)
        process_lock = getattr(vault, "_process_lock", None)
        if (
            isinstance(identity, tuple)
            and len(identity) == 2
            and process_lock is not None
            and getattr(process_lock, "active_operation", None) is not None
        ):
            active_identities.add(identity)

    _LOCK_REGISTRY_GUARD = threading.RLock()
    _LOCK_REGISTRY = {}
    _LOCK_REGISTRY_PID = os.getpid()
    _OPEN_VAULTS = weakref.WeakSet()
    _FORKED_ACTIVE_LOCK_IDENTITIES = frozenset(active_identities)

    # Never enter an inherited mutex in the child. Descriptor fields are
    # cleared before close, and duplicate numeric descriptors are closed once,
    # so a stale object cannot later close a reused child descriptor.
    closed_fds: set[int] = set()
    for vault in inherited:
        for name in ("_owners_fd", "_lock_fd", "_root_fd"):
            fd = getattr(vault, name, -1)
            try:
                object.__setattr__(vault, name, -1)
            except BaseException:
                continue
            if isinstance(fd, int) and fd >= 0 and fd not in closed_fds:
                closed_fds.add(fd)
                try:
                    os.close(fd)
                except OSError:
                    pass
        for name, value in (("_process_lock", None), ("_operation_depth", 0)):
            try:
                object.__setattr__(vault, name, value)
            except BaseException:
                pass

    # The prepare hook acquired the inherited guard. Replace it rather than
    # releasing an inherited synchronization primitive in the child.
    _FORK_DESCRIPTOR_GUARD = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork,
        after_in_parent=_after_fork_parent,
        after_in_child=_reset_lock_registry_after_fork,
    )


def _prepare_lock_registry() -> None:
    if _LOCK_REGISTRY_PID != os.getpid():
        _reset_lock_registry_after_fork()


def _register_open_vault(vault: object) -> None:
    _prepare_lock_registry()
    with _FORK_DESCRIPTOR_GUARD:
        with _LOCK_REGISTRY_GUARD:
            _OPEN_VAULTS.add(vault)


def _discard_open_vault(vault: object) -> None:
    _prepare_lock_registry()
    with _FORK_DESCRIPTOR_GUARD:
        with _LOCK_REGISTRY_GUARD:
            _OPEN_VAULTS.discard(vault)


def _acquire_constructor_fork_guard(selected: Path) -> None:
    """Wait for unrelated descriptor windows, but reject this root promptly."""

    while not _FORK_DESCRIPTOR_GUARD.acquire(timeout=0.01):
        with _LOCK_REGISTRY_GUARD:
            active_same_root = any(
                getattr(vault, "_pid", None) == os.getpid()
                and getattr(vault, "_root", None) == selected
                and getattr(vault, "_operation_depth", 0) != 0
                for vault in tuple(_OPEN_VAULTS)
            )
        if active_same_root:
            raise RequestVaultConfigurationError(
                "Another private request operation is already active"
            )


def _retain_process_lock(identity: tuple[int, int]) -> _ProcessLock:
    _prepare_lock_registry()
    with _LOCK_REGISTRY_GUARD:
        value = _LOCK_REGISTRY.get(identity)
        if value is None:
            value = _ProcessLock()
            _LOCK_REGISTRY[identity] = value
        value.references += 1
        return value


def _release_process_lock(identity: tuple[int, int], value: _ProcessLock) -> None:
    _prepare_lock_registry()
    with _LOCK_REGISTRY_GUARD:
        current = _LOCK_REGISTRY.get(identity)
        if current is value:
            current.references -= 1
            if current.references <= 0:
                del _LOCK_REGISTRY[identity]


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise RequestVaultInvalid(f"{label} is invalid")
    return value


def _job_identifier(value: object) -> str:
    if type(value) is not str or _OWNER_RE.fullmatch(value) is None:
        raise RequestVaultInvalid("job identifier is invalid")
    return value


def _storage_incarnation(value: object) -> str:
    if type(value) is not str or _INCARNATION_RE.fullmatch(value) is None:
        raise RequestVaultInvalid("storage incarnation is invalid")
    return value


def _require_canonical_json_envelope(value: bytes) -> None:
    """Verify the generic canonical-JSON property already promised by HTTP."""

    try:
        parsed = json.loads(
            value.decode("utf-8", "strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(parsed, dict):
            raise ValueError("root is not an object")
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ) as error:
        raise RequestVaultInvalid("Canonical request envelope is invalid") from error
    if not hmac.compare_digest(value, canonical):
        raise RequestVaultInvalid("Canonical request envelope is invalid")


def _authorized_owner(
    context: object, *, job_id: str | None
) -> str:
    if type(context) is ServicePrincipal:
        if (
            type(context.principal_id) is not str
            or type(context.key_id) is not str
            or type(context.scopes) is not tuple
            or any(type(scope) is not str for scope in context.scopes)
        ):
            raise RequestVaultAuthorizationError("Request vault access denied")
        owner = context.principal_id
        key_id = context.key_id
        scopes = context.scopes
    elif type(context) is AuthorizedServiceRequest:
        if (
            type(context.principal_id) is not str
            or type(context.owner_principal) is not str
            or type(context.key_id) is not str
            or type(context.scope) is not str
            or type(context.route_template) is not str
            or type(context.max_body_bytes) is not int
            or type(context.atomic_owner_lookup_required) is not bool
        ):
            raise RequestVaultAuthorizationError("Request vault access denied")
        owner = context.owner_principal
        key_id = context.key_id
        scopes = (context.scope,)
        if (
            context.principal_id != owner
            or context.scope != _SUBMIT_SCOPE
            or context.route_template != _SUBMIT_ROUTE
            or context.max_body_bytes != MAX_CANONICAL_ENVELOPE_BYTES
            or context.atomic_owner_lookup_required
            or context.job_id is not None
        ):
            raise RequestVaultAuthorizationError("Request vault access denied")
    else:
        raise RequestVaultAuthorizationError("Request vault access denied")
    if (
        _OWNER_RE.fullmatch(owner) is None
        or _KEY_ID_RE.fullmatch(key_id) is None
        or _SUBMIT_SCOPE not in scopes
    ):
        raise RequestVaultAuthorizationError("Request vault access denied")
    if job_id is not None:
        _job_identifier(job_id)
    return owner


class _ThreadBoundContext:
    """Prevent a lock-owning context from being exited by another thread."""

    __slots__ = ("_context", "_owner_thread", "_state", "_state_lock")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("thread-bound contexts are immutable")

    def __init__(self, context: object) -> None:
        object.__setattr__(self, "_context", context)
        object.__setattr__(self, "_owner_thread", None)
        object.__setattr__(self, "_state", "idle")
        object.__setattr__(self, "_state_lock", threading.Lock())

    def __enter__(self) -> object:
        owner = threading.current_thread()
        with self._state_lock:
            if self._state != "idle":
                _configuration_error()
            object.__setattr__(self, "_owner_thread", owner)
            object.__setattr__(self, "_state", "entering")
        try:
            result = self._context.__enter__()  # type: ignore[attr-defined]
        except BaseException:
            with self._state_lock:
                object.__setattr__(self, "_owner_thread", None)
                object.__setattr__(self, "_state", "closed")
            raise
        with self._state_lock:
            object.__setattr__(self, "_state", "active")
        return result

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> object:
        with self._state_lock:
            if (
                self._state != "active"
                or self._owner_thread is not threading.current_thread()
            ):
                raise RequestVaultConfigurationError(
                    "Private request vault operation must exit in its owning thread"
                )
            object.__setattr__(self, "_state", "exiting")
        try:
            return self._context.__exit__(  # type: ignore[attr-defined]
                exception_type, exception, traceback
            )
        finally:
            with self._state_lock:
                object.__setattr__(self, "_owner_thread", None)
                object.__setattr__(self, "_state", "closed")


class RequestPublicationSession:
    """Thread-bound authority held under one vault process/flock session."""

    __slots__ = ("_store", "_pid", "_thread", "_token", "_active")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("request publication sessions are immutable")

    def __init__(
        self, store: "PrivateCanonicalRequestVault", token: object
    ) -> None:
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_pid", os.getpid())
        object.__setattr__(self, "_thread", threading.current_thread())
        object.__setattr__(self, "_token", token)
        object.__setattr__(self, "_active", True)

    def publish_reserved(
        self,
        context: AuthContext,
        *,
        canonical_envelope: bytes,
        declared_sha256: str,
        job_id: str,
        generation_key: str,
        storage_incarnation: str,
    ) -> PublishedRequest:
        return self._store._session_publish_reserved(
            self,
            context,
            canonical_envelope=canonical_envelope,
            declared_sha256=declared_sha256,
            job_id=job_id,
            generation_key=generation_key,
            storage_incarnation=storage_incarnation,
        )

    def probe_exact(
        self,
        context: AuthContext,
        *,
        job_id: str,
        generation_key: str,
        declared_sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> RequestVaultHandle | None:
        return self._store._session_probe_exact(
            self,
            context,
            job_id=job_id,
            generation_key=generation_key,
            declared_sha256=declared_sha256,
            byte_size=byte_size,
            storage_incarnation=storage_incarnation,
        )

    def delete_exact(
        self,
        context: AuthContext,
        *,
        job_id: str,
        generation_key: str,
        declared_sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> bool:
        return self._store._session_delete_exact(
            self,
            context,
            job_id=job_id,
            generation_key=generation_key,
            declared_sha256=declared_sha256,
            byte_size=byte_size,
            storage_incarnation=storage_incarnation,
        )


class PrivateCanonicalRequestVault:
    """Immutable, owner-separated store for canonical submit envelopes."""

    def __init__(
        self,
        root: str | os.PathLike[str] = DEFAULT_PRIVATE_REQUEST_ROOT,
        *,
        managed_ancestor: str | os.PathLike[str] = DEFAULT_MANAGED_ANCESTOR,
        expected_uid: int,
        namespace_key: bytes,
    ) -> None:
        if (
            type(expected_uid) is not int
            or expected_uid < 0
            or expected_uid != os.geteuid()
        ):
            _configuration_error()
        if (
            type(namespace_key) is not bytes
            or len(namespace_key) < 32
            or len(namespace_key) > 128
        ):
            _configuration_error()
        selected = _canonical_absolute(root, "private request root")
        anchor = _canonical_absolute(managed_ancestor, "managed ancestor")
        try:
            relative = selected.relative_to(anchor)
        except ValueError:
            _configuration_error()
        if not relative.parts:
            _configuration_error()

        chain: list[tuple[Path, int, os.stat_result]] = []
        self._root = selected
        self._managed_ancestor = anchor
        self._expected_uid = expected_uid
        self._namespace_key = bytes(namespace_key)
        self._pid = os.getpid()
        self._root_fd = -1
        self._owners_fd = -1
        self._lock_fd = -1
        self._root_identity: tuple[int, int] | None = None
        self._owners_identity: tuple[int, int] | None = None
        self._lock_identity: tuple[int, int] | None = None
        self._identity_marker_identity: tuple[int, int] | None = None
        self._managed_bindings: tuple[tuple[Path, tuple[int, int]], ...] = ()
        self._process_lock: _ProcessLock | None = None
        self._operation_depth = 0
        _acquire_constructor_fork_guard(selected)
        try:
            _register_open_vault(self)
            try:
                chain = _absolute_directory_chain(selected)
            except OSError as error:
                raise RequestVaultConfigurationError(
                    "Private request vault path is not a no-follow directory chain"
                ) from error
            indexed = {path: index for index, (path, _fd, _info) in enumerate(chain)}
            if anchor not in indexed:
                _configuration_error()
            managed = chain[indexed[anchor] :]
            device = managed[-1][2].st_dev
            for _path, fd, _info in managed:
                try:
                    _validate_directory(fd, expected_uid=expected_uid, device=device)
                except RequestVaultCorrupt:
                    _configuration_error()
            self._device = device
            self._root_identity = _identity(managed[-1][2])
            self._managed_bindings = tuple(
                (path, _identity(info)) for path, _fd, info in managed
            )
            self._root_fd = os.dup(managed[-1][1])
            self._validate_root_names(allow_temporaries=True)
            self._lock_fd = self._open_or_create_lock()
            lock_info = _validate_file(
                self._lock_fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=0,
            )
            self._lock_identity = _identity(lock_info)
            if self._lock_identity in _FORKED_ACTIVE_LOCK_IDENTITIES:
                raise RequestVaultConfigurationError(
                    "A child forked during an active request operation cannot "
                    "reopen the same private vault"
                )
            process_lock = _retain_process_lock(self._lock_identity)
            self._process_lock = process_lock
            if not process_lock.mutex.acquire(blocking=False):
                self._process_lock = None
                _release_process_lock(self._lock_identity, process_lock)
                raise RequestVaultConfigurationError(
                    "Another private request operation is already active"
                )
            try:
                if process_lock.active_operation is not None:
                    _configuration_error()
                bootstrap_token = object()
                process_lock.active_operation = bootstrap_token
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                    try:
                        self._cleanup_identity_temporaries()
                        self._store_id = self._load_or_create_store_identity()
                        identity_raw, identity_info = self._read_named_file(
                            self._root_fd,
                            ".identity",
                            maximum=_IDENTITY_BYTES,
                            exact_size=_IDENTITY_BYTES,
                        )
                        if identity_raw != self._identity_payload(self._store_id):
                            _configuration_error()
                        self._identity_marker_identity = _identity(identity_info)
                        self._owners_fd = self._open_or_create_directory(
                            self._root_fd, "owners"
                        )
                        self._owners_identity = _identity(
                            _validate_directory(
                                self._owners_fd,
                                expected_uid=self._expected_uid,
                                device=self._device,
                            )
                        )
                        os.fsync(self._root_fd)
                        self._recover_orphan_temporaries()
                        # A crash after the no-replace link but before unlinking
                        # its staging name leaves the final file at link count 2.
                        # Recover that one narrowly verified shape before the
                        # steady-state tree validator requires link count 1.
                        self._validate_tree_layout()
                        self._validate_root_names(allow_temporaries=False)
                    finally:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                finally:
                    if process_lock.active_operation is bootstrap_token:
                        process_lock.active_operation = None
            finally:
                process_lock.mutex.release()
        except BaseException:
            self.close()
            raise
        finally:
            try:
                _close_chain(chain)
            finally:
                _FORK_DESCRIPTOR_GUARD.release()

    def __enter__(self) -> "PrivateCanonicalRequestVault":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    @property
    def store_identity(self) -> str:
        """Return the validated non-secret identity of this vault incarnation.

        The value is stable for the same provisioned root and namespace key.
        Reading it revalidates every retained root binding; a closed or rebound
        vault fails closed instead of returning cached metadata.
        """

        with self._locked():
            if _DIGEST_RE.fullmatch(self._store_id) is None:
                _corrupt()
            return self._store_id

    def close(self) -> None:
        # A forked child must never touch a mutex or flock inherited while a
        # now-nonexistent parent thread held it.  It owns only its copies of
        # these descriptors, so closing them directly is safe and local.
        if getattr(self, "_pid", os.getpid()) != os.getpid():
            self._close_descriptors()
            self._process_lock = None
            self._operation_depth = 0
            _discard_open_vault(self)
            return
        process_lock = getattr(self, "_process_lock", None)
        lock_identity = getattr(self, "_lock_identity", None)
        if process_lock is not None:
            with process_lock.mutex:
                if self._operation_depth:
                    _configuration_error()
                self._close_descriptors()
                self._process_lock = None
            if lock_identity is not None:
                _release_process_lock(lock_identity, process_lock)
        else:
            self._close_descriptors()
        _discard_open_vault(self)

    def _close_descriptors(self) -> None:
        with _FORK_DESCRIPTOR_GUARD:
            for name in ("_owners_fd", "_lock_fd", "_root_fd"):
                fd = getattr(self, name, -1)
                if isinstance(fd, int) and fd >= 0:
                    setattr(self, name, -1)
                    os.close(fd)

    def _identity_payload(self, store_id: str) -> bytes:
        tag = hmac.new(
            self._namespace_key,
            _IDENTITY_DOMAIN + store_id.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return (store_id + "\n" + tag).encode("ascii")

    def _read_store_identity(self) -> str | None:
        try:
            raw, _info = self._read_named_file(
                self._root_fd,
                ".identity",
                maximum=_IDENTITY_BYTES,
                exact_size=_IDENTITY_BYTES,
            )
        except FileNotFoundError:
            return None
        try:
            text = raw.decode("ascii", "strict")
        except UnicodeError:
            _configuration_error()
        parts = text.split("\n")
        if len(parts) != 2 or _DIGEST_RE.fullmatch(parts[0]) is None:
            _configuration_error()
        expected = self._identity_payload(parts[0])
        if not hmac.compare_digest(raw, expected):
            _configuration_error()
        return parts[0]

    def _load_or_create_store_identity(self) -> str:
        existing = self._read_store_identity()
        if existing is not None:
            return existing
        store_id = secrets.token_hex(32)
        value = self._identity_payload(store_id)
        name = ".identity.tmp-" + secrets.token_hex(16)
        fd = -1
        present = False
        try:
            fd = os.open(name, _create_flags(), FILE_MODE, dir_fd=self._root_fd)
            present = True
            os.fchmod(fd, FILE_MODE)
            _write_all(fd, value)
            os.fsync(fd)
            _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=len(value),
            )
            os.fsync(self._root_fd)
            try:
                os.link(
                    name,
                    ".identity",
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                winner = self._read_store_identity()
                if winner is None:
                    _configuration_error()
                return winner
            linked = os.fstat(fd)
            named_temp = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            named_final = os.stat(
                ".identity", dir_fd=self._root_fd, follow_symlinks=False
            )
            if (
                linked.st_nlink != 2
                or _snapshot(named_temp) != _snapshot(linked)
                or _snapshot(named_final) != _snapshot(linked)
            ):
                _configuration_error()
            os.fsync(self._root_fd)
            os.unlink(name, dir_fd=self._root_fd)
            present = False
            os.fsync(self._root_fd)
            final = _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=len(value),
            )
            named_final = os.stat(
                ".identity", dir_fd=self._root_fd, follow_symlinks=False
            )
            if _snapshot(named_final) != _snapshot(final):
                _configuration_error()
            return store_id
        except RequestVaultError:
            raise
        except OSError as error:
            raise RequestVaultConfigurationError(
                "Private request vault identity could not be published"
            ) from error
        finally:
            if fd >= 0:
                os.close(fd)
            if present:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                except FileNotFoundError:
                    pass

    def _cleanup_identity_temporaries(self) -> None:
        names: list[str] = []
        for name in _scanned_names(self._root_fd, maximum=MAX_LAYOUT_ENTRIES):
            if _IDENTITY_TEMP_RE.fullmatch(name) is not None:
                names.append(name)
                if len(names) > 8:
                    _configuration_error()
        names.sort()
        if len(names) > 8:
            _configuration_error()
        for name in names:
            try:
                fd = os.open(name, _read_flags(), dir_fd=self._root_fd)
            except OSError:
                _configuration_error()
            try:
                info = _validate_file(
                    fd,
                    expected_uid=self._expected_uid,
                    device=self._device,
                    exact_size=_IDENTITY_BYTES,
                    allowed_links=frozenset({1, 2}),
                )
                named = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
                if _snapshot(named) != _snapshot(info):
                    _configuration_error()
                if info.st_nlink == 2:
                    try:
                        final_fd = os.open(
                            ".identity", _read_flags(), dir_fd=self._root_fd
                        )
                    except OSError:
                        _configuration_error()
                    try:
                        final = os.fstat(final_fd)
                        if _snapshot(final) != _snapshot(info):
                            _configuration_error()
                    finally:
                        os.close(final_fd)
                os.unlink(name, dir_fd=self._root_fd)
            finally:
                os.close(fd)
        if names:
            os.fsync(self._root_fd)

    def _open_or_create_lock(self) -> int:
        try:
            try:
                fd = os.open(
                    ".request-vault.lock",
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    FILE_MODE,
                    dir_fd=self._root_fd,
                )
                os.fchmod(fd, FILE_MODE)
                os.fsync(fd)
                os.fsync(self._root_fd)
            except FileExistsError:
                fd = os.open(
                    ".request-vault.lock",
                    os.O_RDWR
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._root_fd,
                )
            return fd
        except OSError as error:
            raise RequestVaultConfigurationError(
                "Private request vault lock is unavailable"
            ) from error

    def _open_or_create_directory(self, parent_fd: int, name: str) -> int:
        if name != "owners" and _OWNER_BINDING_RE.fullmatch(name) is None:
            _corrupt()
        try:
            try:
                os.mkdir(name, DIRECTORY_MODE, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            _validate_directory(
                fd, expected_uid=self._expected_uid, device=self._device
            )
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _snapshot(named) != _snapshot(os.fstat(fd)):
                _corrupt()
            return fd
        except RequestVaultError:
            raise
        except OSError as error:
            raise RequestVaultCorrupt(
                "Private request vault directory integrity check failed"
            ) from error

    def _try_open_directory(self, parent_fd: int, name: str) -> int | None:
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RequestVaultCorrupt(
                "Private request vault directory integrity check failed"
            ) from error
        try:
            _validate_directory(
                fd, expected_uid=self._expected_uid, device=self._device
            )
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _snapshot(named) != _snapshot(os.fstat(fd)):
                _corrupt()
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _read_named_file(
        self,
        parent_fd: int,
        name: str,
        *,
        maximum: int,
        exact_size: int | None = None,
        allowed_links: frozenset[int] = frozenset({1}),
    ) -> tuple[bytes, os.stat_result]:
        try:
            fd = os.open(name, _read_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise RequestVaultCorrupt(
                "Private request vault file cannot be opened safely"
            ) from error
        try:
            before = _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                maximum=maximum,
                exact_size=exact_size,
                allowed_links=allowed_links,
            )
            chunks: list[bytes] = []
            total = 0
            while total <= maximum:
                chunk = os.read(fd, min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after = os.fstat(fd)
            if total != before.st_size or total > maximum or _snapshot(after) != _snapshot(before):
                _corrupt()
            try:
                named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise RequestVaultCorrupt(
                    "Private request vault file binding changed while reading"
                ) from error
            if _snapshot(named) != _snapshot(before):
                _corrupt()
            return b"".join(chunks), before
        finally:
            os.close(fd)

    def _validate_root_names(self, *, allow_temporaries: bool) -> None:
        for name in _scanned_names(self._root_fd, maximum=MAX_LAYOUT_ENTRIES):
            if name in _ROOT_ENTRIES:
                continue
            if allow_temporaries and (
                _TEMP_RE.fullmatch(name) is not None
                or _IDENTITY_TEMP_RE.fullmatch(name) is not None
            ):
                continue
            _corrupt()

    def _validate_tree_layout(self) -> None:
        self._validate_root_names(allow_temporaries=True)
        count = 0
        for owner_name in _scanned_names(
            self._owners_fd, maximum=MAX_LAYOUT_ENTRIES
        ):
            count += 1
            if _OWNER_BINDING_RE.fullmatch(owner_name) is None:
                _corrupt()
            owner_fd = self._try_open_directory(self._owners_fd, owner_name)
            if owner_fd is None:
                _corrupt()
            try:
                for final_name in _scanned_names(
                    owner_fd, maximum=MAX_LAYOUT_ENTRIES - count
                ):
                    count += 1
                    if _FINAL_RE.fullmatch(final_name) is None:
                        _corrupt()
                    try:
                        fd = os.open(final_name, _read_flags(), dir_fd=owner_fd)
                    except OSError as error:
                        raise RequestVaultCorrupt(
                            "Private request vault file cannot be opened safely"
                        ) from error
                    try:
                        _validate_file(
                            fd,
                            expected_uid=self._expected_uid,
                            device=self._device,
                            minimum=_HEADER.size + 2,
                            maximum=_MAX_RECORD_BYTES,
                        )
                    finally:
                        os.close(fd)
            finally:
                os.close(owner_fd)

    def _verify_runtime_bindings(self) -> None:
        if os.getpid() != self._pid:
            _configuration_error()
        if self._root_fd < 0 or self._owners_fd < 0 or self._lock_fd < 0:
            _configuration_error()
        for path, expected in self._managed_bindings:
            try:
                chain = _absolute_directory_chain(path)
            except (OSError, RequestVaultError) as error:
                raise RequestVaultCorrupt(
                    "Private request vault path binding changed"
                ) from error
            try:
                info = _validate_directory(
                    chain[-1][1],
                    expected_uid=self._expected_uid,
                    device=self._device,
                )
                if _identity(info) != expected:
                    _corrupt()
            finally:
                _close_chain(chain)
        held_root = os.fstat(self._root_fd)
        if self._root_identity is None or _identity(held_root) != self._root_identity:
            _corrupt()

        held_lock = _validate_file(
            self._lock_fd,
            expected_uid=self._expected_uid,
            device=self._device,
            exact_size=0,
        )
        try:
            named_lock = os.open(
                ".request-vault.lock",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
        except OSError as error:
            raise RequestVaultCorrupt(
                "Private request vault lock binding changed"
            ) from error
        try:
            if (
                self._lock_identity is None
                or _identity(os.fstat(named_lock)) != self._lock_identity
                or _identity(held_lock) != self._lock_identity
            ):
                _corrupt()
        finally:
            os.close(named_lock)

        try:
            raw, identity_info = self._read_named_file(
                self._root_fd,
                ".identity",
                maximum=_IDENTITY_BYTES,
                exact_size=_IDENTITY_BYTES,
            )
        except FileNotFoundError:
            _corrupt()
        if (
            self._identity_marker_identity is None
            or _identity(identity_info) != self._identity_marker_identity
            or raw != self._identity_payload(self._store_id)
        ):
            _corrupt()
        held_owners = _validate_directory(
            self._owners_fd,
            expected_uid=self._expected_uid,
            device=self._device,
        )
        named_owners = self._try_open_directory(self._root_fd, "owners")
        if named_owners is None:
            _corrupt()
        try:
            if (
                self._owners_identity is None
                or _identity(held_owners) != self._owners_identity
                or _identity(os.fstat(named_owners)) != self._owners_identity
            ):
                _corrupt()
        finally:
            os.close(named_owners)
        self._validate_root_names(allow_temporaries=False)

    def _locked(self) -> _ThreadBoundContext:
        return _ThreadBoundContext(self._locked_context())

    def publication_session(self) -> _ThreadBoundContext:
        """Hold the vault lock across DB-first publication hand-offs."""

        return _ThreadBoundContext(self._publication_session_context())

    def _validate_publication_session(
        self, session: object
    ) -> RequestPublicationSession:
        value = self._process_lock
        if (
            type(session) is not RequestPublicationSession
            or session._store is not self
            or type(session._pid) is not int
            or session._pid != os.getpid()
            or session._pid != self._pid
            or session._thread is not threading.current_thread()
            or session._active is not True
            or value is None
            or self._operation_depth != 1
            or value.active_operation is not session._token
        ):
            _configuration_error()
        return session

    @contextmanager
    def _publication_session_context(
        self,
    ) -> Iterator[RequestPublicationSession]:
        if os.getpid() != self._pid:
            _configuration_error()
        value = self._process_lock
        if value is None:
            _configuration_error()
        with value.mutex:
            if self._process_lock is not value or self._lock_fd < 0:
                _configuration_error()
            if self._operation_depth or value.active_operation is not None:
                _configuration_error()
            token = object()
            session = RequestPublicationSession(self, token)
            self._operation_depth = 1
            value.active_operation = token
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                try:
                    with _FORK_DESCRIPTOR_GUARD:
                        self._verify_runtime_bindings()
                    yield session
                finally:
                    object.__setattr__(session, "_active", False)
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                if value.active_operation is token:
                    value.active_operation = None
                self._operation_depth = 0

    @contextmanager
    def _session_operation(
        self, session: RequestPublicationSession
    ) -> Iterator[None]:
        self._validate_publication_session(session)
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            self._verify_runtime_bindings()
            yield

    @contextmanager
    def _locked_context(self) -> Iterator[None]:
        # This check must precede even reading/entering the inherited RLock.
        if os.getpid() != self._pid:
            _configuration_error()
        value = self._process_lock
        if value is None:
            _configuration_error()
        with value.mutex:
            if self._process_lock is not value or self._lock_fd < 0:
                _configuration_error()
            if self._operation_depth or value.active_operation is not None:
                _configuration_error()
            token = object()
            self._operation_depth = 1
            value.active_operation = token
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                try:
                    with _FORK_DESCRIPTOR_GUARD:
                        self._verify_runtime_bindings()
                        yield
                finally:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                if value.active_operation is token:
                    value.active_operation = None
                self._operation_depth = 0

    def _owner_binding(self, owner: str) -> str:
        return hmac.new(
            self._namespace_key,
            _OWNER_DOMAIN
            + self._store_id.encode("ascii")
            + b"\0"
            + owner.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def _job_binding(self, owner_binding: str, job_id: str) -> str:
        return hmac.new(
            self._namespace_key,
            _JOB_DOMAIN
            + self._store_id.encode("ascii")
            + b"\0"
            + owner_binding.encode("ascii")
            + b"\0"
            + job_id.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _record(manifest: _Manifest, envelope: bytes) -> bytes:
        encoded = manifest.encoded()
        if not encoded or len(encoded) > _MAX_MANIFEST_BYTES:
            _corrupt()
        return _HEADER.pack(_MAGIC, len(encoded)) + encoded + envelope

    def _parse_record(
        self,
        raw: bytes,
        *,
        expected_owner_binding: str | None = None,
        expected_job_binding: str | None = None,
    ) -> tuple[_Manifest, bytes]:
        if type(raw) is not bytes or not _HEADER.size + 2 <= len(raw) <= _MAX_RECORD_BYTES:
            _corrupt()
        try:
            magic, manifest_size = _HEADER.unpack(raw[: _HEADER.size])
        except struct.error:
            _corrupt()
        if (
            magic != _MAGIC
            or manifest_size <= 0
            or manifest_size > _MAX_MANIFEST_BYTES
            or _HEADER.size + manifest_size >= len(raw)
        ):
            _corrupt()
        manifest_raw = raw[_HEADER.size : _HEADER.size + manifest_size]
        envelope = raw[_HEADER.size + manifest_size :]
        try:
            value = json.loads(
                manifest_raw.decode("ascii", "strict"),
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
            _corrupt()
        keys = {
            "byte_size",
            "canonical_request_sha256",
            "generation_key",
            "job_binding",
            "owner_binding",
            "schema_version",
            "storage_incarnation",
            "store_id",
        }
        if not isinstance(value, dict) or set(value) != keys:
            _corrupt()
        if (
            value["schema_version"] != _SCHEMA
            or value["store_id"] != self._store_id
            or type(value["owner_binding"]) is not str
            or _OWNER_BINDING_RE.fullmatch(value["owner_binding"]) is None
            or type(value["job_binding"]) is not str
            or _JOB_BINDING_RE.fullmatch(value["job_binding"]) is None
            or type(value["generation_key"]) is not str
            or _DIGEST_RE.fullmatch(value["generation_key"]) is None
            or type(value["canonical_request_sha256"]) is not str
            or _DIGEST_RE.fullmatch(value["canonical_request_sha256"]) is None
            or type(value["storage_incarnation"]) is not str
            or _INCARNATION_RE.fullmatch(value["storage_incarnation"]) is None
            or type(value["byte_size"]) is not int
            or not 1 <= value["byte_size"] <= MAX_CANONICAL_ENVELOPE_BYTES
            or value["byte_size"] != len(envelope)
            or (
                expected_owner_binding is not None
                and value["owner_binding"] != expected_owner_binding
            )
            or (expected_job_binding is not None and value["job_binding"] != expected_job_binding)
        ):
            _corrupt()
        manifest = _Manifest(
            store_id=value["store_id"],
            owner_binding=value["owner_binding"],
            job_binding=value["job_binding"],
            generation_key=value["generation_key"],
            canonical_request_sha256=value["canonical_request_sha256"],
            byte_size=value["byte_size"],
            storage_incarnation=value["storage_incarnation"],
        )
        if (
            not hmac.compare_digest(manifest_raw, manifest.encoded())
            or not hmac.compare_digest(
                hashlib.sha256(envelope).hexdigest(),
                manifest.canonical_request_sha256,
            )
        ):
            _corrupt()
        return manifest, envelope

    def _read_object(
        self, owner_fd: int, owner_binding: str, job_binding: str
    ) -> tuple[_Manifest, bytes, os.stat_result]:
        name = job_binding + ".vault"
        try:
            raw, info = self._read_named_file(
                owner_fd,
                name,
                maximum=_MAX_RECORD_BYTES,
            )
        except FileNotFoundError as error:
            raise RequestVaultNotFound(
                "Canonical request is unavailable"
            ) from error
        manifest, envelope = self._parse_record(
            raw,
            expected_owner_binding=owner_binding,
            expected_job_binding=job_binding,
        )
        return manifest, envelope, info

    def _new_handle(
        self,
        *,
        owner: str,
        job_id: str,
        generation_key: str,
        manifest: _Manifest,
    ) -> RequestVaultHandle:
        locator = "req_" + manifest.job_binding
        payload = b"\0".join(
            item.encode("ascii")
            for item in (
                owner,
                job_id,
                generation_key,
                locator,
                manifest.canonical_request_sha256,
                str(manifest.byte_size),
                manifest.storage_incarnation,
                self._store_id,
            )
        )
        tag = hmac.new(
            self._namespace_key, _HANDLE_DOMAIN + payload, hashlib.sha256
        ).hexdigest()
        return RequestVaultHandle(
            locator=locator,
            canonical_request_sha256=manifest.canonical_request_sha256,
            byte_size=manifest.byte_size,
            storage_incarnation=manifest.storage_incarnation,
            _owner_principal=owner,
            _job_id=job_id,
            _generation_key=generation_key,
            _store_id=self._store_id,
            _auth_tag=tag,
        )

    def _validate_handle(
        self, context: object, handle: object
    ) -> tuple[str, str, str, str]:
        if type(handle) is not RequestVaultHandle:
            raise RequestVaultInvalidHandle("Request vault handle is invalid")
        if (
            type(handle._owner_principal) is not str
            or _OWNER_RE.fullmatch(handle._owner_principal) is None
            or type(handle._job_id) is not str
            or _OWNER_RE.fullmatch(handle._job_id) is None
            or type(handle._generation_key) is not str
            or _DIGEST_RE.fullmatch(handle._generation_key) is None
            or type(handle._store_id) is not str
            or _DIGEST_RE.fullmatch(handle._store_id) is None
            or type(handle._auth_tag) is not str
            or _DIGEST_RE.fullmatch(handle._auth_tag) is None
            or type(handle.storage_incarnation) is not str
            or _INCARNATION_RE.fullmatch(handle.storage_incarnation) is None
            or type(handle.locator) is not str
            or _LOCATOR_RE.fullmatch(handle.locator) is None
            or type(handle.canonical_request_sha256) is not str
            or _DIGEST_RE.fullmatch(handle.canonical_request_sha256) is None
            or type(handle.byte_size) is not int
            or not 1 <= handle.byte_size <= MAX_CANONICAL_ENVELOPE_BYTES
        ):
            raise RequestVaultInvalidHandle("Request vault handle is invalid")
        owner = _authorized_owner(context, job_id=None)
        if owner != handle._owner_principal or handle._store_id != self._store_id:
            raise RequestVaultInvalidHandle("Request vault handle is invalid")
        owner_binding = self._owner_binding(owner)
        job_binding = self._job_binding(owner_binding, handle._job_id)
        if handle.locator != "req_" + job_binding:
            raise RequestVaultInvalidHandle("Request vault handle is invalid")
        expected = self._new_handle(
            owner=owner,
            job_id=handle._job_id,
            generation_key=handle._generation_key,
            manifest=_Manifest(
                store_id=self._store_id,
                owner_binding=owner_binding,
                job_binding=job_binding,
                generation_key=handle._generation_key,
                canonical_request_sha256=handle.canonical_request_sha256,
                byte_size=handle.byte_size,
                storage_incarnation=handle.storage_incarnation,
            ),
        )
        if not hmac.compare_digest(handle._auth_tag, expected._auth_tag):
            raise RequestVaultInvalidHandle("Request vault handle is invalid")
        return owner, handle._job_id, owner_binding, job_binding

    def _open_owner(self, owner_binding: str, *, create: bool) -> int | None:
        if _OWNER_BINDING_RE.fullmatch(owner_binding) is None:
            _corrupt()
        if create:
            return self._open_or_create_directory(self._owners_fd, owner_binding)
        return self._try_open_directory(self._owners_fd, owner_binding)

    def _unlink_held_temporary(self, name: str, fd: int) -> None:
        try:
            named = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        held = os.fstat(fd)
        if _identity(named) != _identity(held):
            _corrupt()
        os.unlink(name, dir_fd=self._root_fd)
        os.fsync(self._root_fd)

    def _recover_orphan_temporaries(self) -> None:
        names: list[str] = []
        for name in _scanned_names(self._root_fd, maximum=MAX_LAYOUT_ENTRIES):
            if _TEMP_RE.fullmatch(name) is not None:
                names.append(name)
                if len(names) > MAX_ORPHAN_TEMPORARIES:
                    _corrupt()
        names.sort()
        for name in names:
            try:
                fd = os.open(name, _read_flags(), dir_fd=self._root_fd)
            except OSError as error:
                raise RequestVaultCorrupt(
                    "Interrupted request publication is unsafe"
                ) from error
            try:
                info = _validate_file(
                    fd,
                    expected_uid=self._expected_uid,
                    device=self._device,
                    maximum=_MAX_RECORD_BYTES,
                    allowed_links=frozenset({1, 2}),
                )
                named = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
                if _snapshot(named) != _snapshot(info):
                    _corrupt()
                if info.st_nlink == 2:
                    raw = self._read_fd_exact(fd, info, allowed_links=frozenset({2}))
                    manifest, _envelope = self._parse_record(raw)
                    owner_fd = self._open_owner(manifest.owner_binding, create=False)
                    if owner_fd is None:
                        _corrupt()
                    try:
                        try:
                            final_fd = os.open(
                                manifest.job_binding + ".vault",
                                _read_flags(),
                                dir_fd=owner_fd,
                            )
                        except OSError:
                            _corrupt()
                        try:
                            final = os.fstat(final_fd)
                            if _snapshot(final) != _snapshot(info):
                                _corrupt()
                        finally:
                            os.close(final_fd)
                    finally:
                        os.close(owner_fd)
                self._unlink_held_temporary(name, fd)
            finally:
                os.close(fd)

    def _read_fd_exact(
        self,
        fd: int,
        before: os.stat_result,
        *,
        allowed_links: frozenset[int],
    ) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        checked = _validate_file(
            fd,
            expected_uid=self._expected_uid,
            device=self._device,
            maximum=_MAX_RECORD_BYTES,
            exact_size=before.st_size,
            allowed_links=allowed_links,
        )
        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_RECORD_BYTES:
            chunk = os.read(fd, min(1024 * 1024, _MAX_RECORD_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        if total != checked.st_size or _snapshot(after) != _snapshot(checked):
            _corrupt()
        return b"".join(chunks)

    def publish(
        self,
        context: AuthContext,
        *,
        canonical_envelope: bytes,
        declared_sha256: str,
        job_id: str,
        generation_key: str,
    ) -> PublishedRequest:
        """Compatibility publication with a vault-generated incarnation."""

        with self.publication_session() as session:
            if type(session) is not RequestPublicationSession:
                _configuration_error()
            return self._session_publish_legacy(
                session,
                context,
                canonical_envelope=canonical_envelope,
                declared_sha256=declared_sha256,
                job_id=job_id,
                generation_key=generation_key,
            )

    def _session_publish_reserved(
        self,
        session: RequestPublicationSession,
        context: AuthContext,
        *,
        canonical_envelope: bytes,
        declared_sha256: str,
        job_id: str,
        generation_key: str,
        storage_incarnation: str,
    ) -> PublishedRequest:
        incarnation = _storage_incarnation(storage_incarnation)
        return self._session_publish_validated(
            session,
            context,
            canonical_envelope=canonical_envelope,
            declared_sha256=declared_sha256,
            job_id=job_id,
            generation_key=generation_key,
            storage_incarnation=incarnation,
        )

    def _session_publish_legacy(
        self,
        session: RequestPublicationSession,
        context: AuthContext,
        *,
        canonical_envelope: bytes,
        declared_sha256: str,
        job_id: str,
        generation_key: str,
    ) -> PublishedRequest:
        return self._session_publish_validated(
            session,
            context,
            canonical_envelope=canonical_envelope,
            declared_sha256=declared_sha256,
            job_id=job_id,
            generation_key=generation_key,
            storage_incarnation=None,
        )

    def _session_publish_validated(
        self,
        session: RequestPublicationSession,
        context: AuthContext,
        *,
        canonical_envelope: bytes,
        declared_sha256: str,
        job_id: str,
        generation_key: str,
        storage_incarnation: str | None,
    ) -> PublishedRequest:
        """Atomically retain one validated envelope under its owner/job key."""

        selected_job = _job_identifier(job_id)
        owner = _authorized_owner(context, job_id=selected_job)
        generation = _digest(generation_key, "generation key")
        declared = _digest(declared_sha256, "canonical request digest")
        incarnation = storage_incarnation
        if (
            type(canonical_envelope) is not bytes
            or not 1 <= len(canonical_envelope) <= MAX_CANONICAL_ENVELOPE_BYTES
            or not hmac.compare_digest(
                hashlib.sha256(canonical_envelope).hexdigest(), declared
            )
        ):
            raise RequestVaultInvalid("Canonical request envelope is invalid")
        _require_canonical_json_envelope(canonical_envelope)
        owner_binding = self._owner_binding(owner)
        job_binding = self._job_binding(owner_binding, selected_job)

        with self._session_operation(session):
            owner_fd = self._open_owner(owner_binding, create=True)
            assert owner_fd is not None
            final_name = job_binding + ".vault"
            temp_name = ".tmp-" + secrets.token_hex(16)
            temp_fd = -1
            temp_present = False
            try:
                try:
                    existing, envelope, _info = self._read_object(
                        owner_fd, owner_binding, job_binding
                    )
                except RequestVaultNotFound:
                    pass
                else:
                    if (
                        existing.owner_binding != owner_binding
                        or existing.job_binding != job_binding
                        or existing.generation_key != generation
                        or existing.canonical_request_sha256 != declared
                        or existing.byte_size != len(canonical_envelope)
                        or (
                            incarnation is not None
                            and existing.storage_incarnation != incarnation
                        )
                        or not hmac.compare_digest(envelope, canonical_envelope)
                    ):
                        raise RequestVaultConflict(
                            "Owner/job already retains a different canonical request"
                        )
                    return PublishedRequest(
                        handle=self._new_handle(
                            owner=owner,
                            job_id=selected_job,
                            generation_key=generation,
                            manifest=existing,
                        ),
                        created=False,
                    )

                manifest = _Manifest(
                    store_id=self._store_id,
                    owner_binding=owner_binding,
                    job_binding=job_binding,
                    generation_key=generation,
                    canonical_request_sha256=declared,
                    byte_size=len(canonical_envelope),
                    storage_incarnation=(
                        incarnation
                        if incarnation is not None
                        else "reqinc_" + secrets.token_hex(16)
                    ),
                )
                record = self._record(manifest, canonical_envelope)
                handle = self._new_handle(
                    owner=owner,
                    job_id=selected_job,
                    generation_key=generation,
                    manifest=manifest,
                )

                temp_fd = os.open(
                    temp_name, _create_flags(), FILE_MODE, dir_fd=self._root_fd
                )
                temp_present = True
                os.fchmod(temp_fd, FILE_MODE)
                _write_all(temp_fd, record)
                os.fsync(temp_fd)
                staged = _validate_file(
                    temp_fd,
                    expected_uid=self._expected_uid,
                    device=self._device,
                    exact_size=len(record),
                )
                os.fsync(self._root_fd)
                try:
                    os.link(
                        temp_name,
                        final_name,
                        src_dir_fd=self._root_fd,
                        dst_dir_fd=owner_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    existing, envelope, _info = self._read_object(
                        owner_fd, owner_binding, job_binding
                    )
                    if (
                        existing.owner_binding != owner_binding
                        or existing.job_binding != job_binding
                        or existing.generation_key != generation
                        or existing.canonical_request_sha256 != declared
                        or existing.byte_size != len(canonical_envelope)
                        or (
                            incarnation is not None
                            and existing.storage_incarnation != incarnation
                        )
                        or not hmac.compare_digest(envelope, canonical_envelope)
                    ):
                        raise RequestVaultConflict(
                            "Owner/job already retains a different canonical request"
                        )
                    self._unlink_held_temporary(temp_name, temp_fd)
                    temp_present = False
                    return PublishedRequest(
                        handle=self._new_handle(
                            owner=owner,
                            job_id=selected_job,
                            generation_key=generation,
                            manifest=existing,
                        ),
                        created=False,
                    )
                linked = os.fstat(temp_fd)
                final_info = os.stat(
                    final_name, dir_fd=owner_fd, follow_symlinks=False
                )
                if (
                    linked.st_nlink != 2
                    or _identity(staged) != _identity(linked)
                    or _snapshot(final_info) != _snapshot(linked)
                ):
                    _corrupt()
                os.fsync(owner_fd)
                self._unlink_held_temporary(temp_name, temp_fd)
                temp_present = False
                final_raw, final = self._read_named_file(
                    owner_fd, final_name, maximum=_MAX_RECORD_BYTES
                )
                if (
                    not hmac.compare_digest(final_raw, record)
                    or final.st_nlink != 1
                ):
                    _corrupt()
                return PublishedRequest(handle=handle, created=True)
            except RequestVaultError:
                raise
            except OSError as error:
                raise RequestVaultCorrupt(
                    "Canonical request could not be published atomically"
                ) from error
            finally:
                if temp_fd >= 0:
                    if temp_present:
                        try:
                            self._unlink_held_temporary(temp_name, temp_fd)
                        except RequestVaultError:
                            pass
                    os.close(temp_fd)
                os.close(owner_fd)

    def _session_probe_exact(
        self,
        session: RequestPublicationSession,
        context: AuthContext,
        *,
        job_id: str,
        generation_key: str,
        declared_sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> RequestVaultHandle | None:
        selected_job = _job_identifier(job_id)
        owner = _authorized_owner(context, job_id=selected_job)
        generation = _digest(generation_key, "generation key")
        declared = _digest(declared_sha256, "canonical request digest")
        incarnation = _storage_incarnation(storage_incarnation)
        if (
            type(byte_size) is not int
            or not 1 <= byte_size <= MAX_CANONICAL_ENVELOPE_BYTES
        ):
            raise RequestVaultInvalid("canonical request size is invalid")
        owner_binding = self._owner_binding(owner)
        job_binding = self._job_binding(owner_binding, selected_job)
        with self._session_operation(session):
            owner_fd = self._open_owner(owner_binding, create=False)
            if owner_fd is None:
                return None
            try:
                try:
                    manifest, _envelope, _info = self._read_object(
                        owner_fd, owner_binding, job_binding
                    )
                except RequestVaultNotFound:
                    return None
            finally:
                os.close(owner_fd)
        if (
            manifest.generation_key != generation
            or manifest.canonical_request_sha256 != declared
            or manifest.byte_size != byte_size
        ):
            raise RequestVaultConflict(
                "Owner/job retains a different canonical request"
            )
        if not hmac.compare_digest(manifest.storage_incarnation, incarnation):
            raise RequestVaultStaleHandle(
                "Persisted request names a different storage incarnation"
            )
        return self._new_handle(
            owner=owner,
            job_id=selected_job,
            generation_key=generation,
            manifest=manifest,
        )

    def _session_delete_exact(
        self,
        session: RequestPublicationSession,
        context: AuthContext,
        *,
        job_id: str,
        generation_key: str,
        declared_sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> bool:
        selected_job = _job_identifier(job_id)
        owner = _authorized_owner(context, job_id=selected_job)
        generation = _digest(generation_key, "generation key")
        declared = _digest(declared_sha256, "canonical request digest")
        incarnation = _storage_incarnation(storage_incarnation)
        if (
            type(byte_size) is not int
            or not 1 <= byte_size <= MAX_CANONICAL_ENVELOPE_BYTES
        ):
            raise RequestVaultInvalid("canonical request size is invalid")
        owner_binding = self._owner_binding(owner)
        job_binding = self._job_binding(owner_binding, selected_job)
        with self._session_operation(session):
            owner_fd = self._open_owner(owner_binding, create=False)
            if owner_fd is None:
                return False
            final_name = job_binding + ".vault"
            try:
                try:
                    manifest, _envelope, info = self._read_object(
                        owner_fd, owner_binding, job_binding
                    )
                except RequestVaultNotFound:
                    return False
                if (
                    manifest.generation_key != generation
                    or manifest.canonical_request_sha256 != declared
                    or manifest.byte_size != byte_size
                ):
                    raise RequestVaultConflict(
                        "Owner/job retains a different canonical request"
                    )
                if not hmac.compare_digest(
                    manifest.storage_incarnation, incarnation
                ):
                    raise RequestVaultStaleHandle(
                        "Persisted request names a different storage incarnation"
                    )
                named = os.stat(
                    final_name, dir_fd=owner_fd, follow_symlinks=False
                )
                if _snapshot(named) != _snapshot(info):
                    _corrupt()
                os.unlink(final_name, dir_fd=owner_fd)
                os.fsync(owner_fd)
                return True
            finally:
                os.close(owner_fd)

    def recover_exact(
        self,
        context: AuthContext,
        *,
        job_id: str,
        generation_key: str,
        declared_sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> RequestVaultHandle:
        """Issue a handle only for an exact persisted incarnation binding."""

        selected_job = _job_identifier(job_id)
        owner = _authorized_owner(context, job_id=selected_job)
        generation = _digest(generation_key, "generation key")
        declared = _digest(declared_sha256, "canonical request digest")
        incarnation = _storage_incarnation(storage_incarnation)
        if (
            type(byte_size) is not int
            or not 1 <= byte_size <= MAX_CANONICAL_ENVELOPE_BYTES
        ):
            raise RequestVaultInvalid("canonical request size is invalid")
        owner_binding = self._owner_binding(owner)
        job_binding = self._job_binding(owner_binding, selected_job)
        with self._locked():
            owner_fd = self._open_owner(owner_binding, create=False)
            if owner_fd is None:
                raise RequestVaultNotFound("Canonical request is unavailable")
            try:
                manifest, _envelope, _info = self._read_object(
                    owner_fd, owner_binding, job_binding
                )
            finally:
                os.close(owner_fd)
        if (
            manifest.generation_key != generation
            or manifest.canonical_request_sha256 != declared
            or manifest.byte_size != byte_size
        ):
            raise RequestVaultNotFound("Canonical request is unavailable")
        if not hmac.compare_digest(manifest.storage_incarnation, incarnation):
            raise RequestVaultStaleHandle(
                "Persisted request metadata names an older storage incarnation"
            )
        return self._new_handle(
            owner=owner,
            job_id=selected_job,
            generation_key=generation,
            manifest=manifest,
        )

    def read_exact(
        self,
        context: AuthContext,
        handle: RequestVaultHandle,
        *,
        declared_sha256: str,
        byte_size: int,
    ) -> bytes:
        """Return exact bytes without exposing any filesystem locator."""

        declared = _digest(declared_sha256, "canonical request digest")
        if (
            type(byte_size) is not int
            or not 1 <= byte_size <= MAX_CANONICAL_ENVELOPE_BYTES
        ):
            raise RequestVaultInvalid("canonical request size is invalid")
        _owner, _job_id, owner_binding, job_binding = self._validate_handle(
            context, handle
        )
        if (
            handle.canonical_request_sha256 != declared
            or handle.byte_size != byte_size
        ):
            raise RequestVaultNotFound("Canonical request is unavailable")
        with self._locked():
            owner_fd = self._open_owner(owner_binding, create=False)
            if owner_fd is None:
                raise RequestVaultNotFound("Canonical request is unavailable")
            try:
                manifest, envelope, _info = self._read_object(
                    owner_fd, owner_binding, job_binding
                )
            finally:
                os.close(owner_fd)
        if (
            manifest.storage_incarnation != handle.storage_incarnation
        ):
            raise RequestVaultStaleHandle(
                "Request vault handle names an older storage incarnation"
            )
        if (
            manifest.generation_key != handle._generation_key
            or manifest.canonical_request_sha256 != declared
            or manifest.byte_size != byte_size
        ):
            raise RequestVaultNotFound("Canonical request is unavailable")
        return envelope

    def delete(
        self, context: AuthContext, handle: RequestVaultHandle
    ) -> bool:
        """Delete exactly one request using this vault's MAC handle only."""

        _owner, _job_id, owner_binding, job_binding = self._validate_handle(
            context, handle
        )
        with self._locked():
            owner_fd = self._open_owner(owner_binding, create=False)
            if owner_fd is None:
                return False
            final_name = job_binding + ".vault"
            try:
                try:
                    manifest, _envelope, info = self._read_object(
                        owner_fd, owner_binding, job_binding
                    )
                except RequestVaultNotFound:
                    return False
                if (
                    manifest.storage_incarnation != handle.storage_incarnation
                ):
                    raise RequestVaultStaleHandle(
                        "Request vault handle names an older storage incarnation"
                    )
                if (
                    manifest.generation_key != handle._generation_key
                    or manifest.canonical_request_sha256
                    != handle.canonical_request_sha256
                    or manifest.byte_size != handle.byte_size
                ):
                    raise RequestVaultInvalidHandle(
                        "Request vault handle is invalid"
                    )
                named = os.stat(
                    final_name, dir_fd=owner_fd, follow_symlinks=False
                )
                if _snapshot(named) != _snapshot(info):
                    _corrupt()
                os.unlink(final_name, dir_fd=owner_fd)
                os.fsync(owner_fd)
                return True
            finally:
                os.close(owner_fd)


__all__ = [
    "DEFAULT_MANAGED_ANCESTOR",
    "DEFAULT_PRIVATE_REQUEST_ROOT",
    "DIRECTORY_MODE",
    "FILE_MODE",
    "MAX_CANONICAL_ENVELOPE_BYTES",
    "MAX_LAYOUT_ENTRIES",
    "MAX_ORPHAN_TEMPORARIES",
    "PrivateCanonicalRequestVault",
    "PublishedRequest",
    "RequestPublicationSession",
    "RequestVaultAuthorizationError",
    "RequestVaultConfigurationError",
    "RequestVaultConflict",
    "RequestVaultCorrupt",
    "RequestVaultError",
    "RequestVaultHandle",
    "RequestVaultInvalid",
    "RequestVaultInvalidHandle",
    "RequestVaultNotFound",
    "RequestVaultStaleHandle",
]
