"""Private, owner-bound staging for validated offline provider exports.

This module deliberately stops before materialization.  It persists one
bounded JSON/ZIP source in a private, short-lived stage, revalidates that exact
source on every load, and supports exact-owner cancellation and owner-blind
expiry.  It does not extract archives, publish durable media objects, mutate
the provider store, serve previews, contact a provider, or spend credits.

The process hosting this store must not execute untrusted ComfyUI custom-node
code under the same OS identity.  Unix owner-only modes isolate other users,
not arbitrary code running as the service UID; production routing therefore
requires a constrained WebUI/helper identity and a centrally enumerated set of
protected roots.

All filesystem traversal below is relative to held directory descriptors and
uses ``O_NOFOLLOW``.  User/provider text never becomes a pathname: owner
directories are HMAC-derived and stage identifiers are server-random.
"""

from __future__ import annotations

import base64
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
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .clipmode_import import (
    DEFAULT_LIMITS,
    ClipModeImportBundle,
    ClipModeImportError,
    ImportLimits,
    import_clipmode_export,
)
from .config import SecretValue


STAGE_SCHEMA_VERSION = 1
STAGE_TOKEN_TYPE = "castle.provider-import-stage.v1"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_METADATA_MAX_BYTES = 16 * 1024
_IDENTITY_MARKER_BYTES = 64
_STAGE_ID_RE = re.compile(r"^stage_[0-9a-f]{32}$")
_OWNER_BINDING_RE = re.compile(r"^[0-9a-f]{64}$")
_ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$")
_CONTENT_TYPES = frozenset({"application/json", "application/zip"})
_METADATA_KEYS = frozenset({"payload", "token_sha256", "metadata_mac"})
_CONSUMED_KEYS = frozenset({"payload", "token_sha256", "metadata_mac"})
_CONSUMED_PAYLOAD_KEYS = frozenset(
    {
        "type",
        "schema_version",
        "stage_id",
        "owner_binding",
        "account_id",
        "receipt_id",
        "import_decision_sha256",
        "decision_plan_sha256",
        "plan_sha256",
        "stable_graph_sha256",
        "consumed_at",
        "expires_at",
    }
)
_CONSUMED_TOKEN_TYPE = "castle.provider-import-consumed.v1"
_CONSUMED_MAX_BYTES = 8 * 1024
_PAYLOAD_KEYS = frozenset(
    {
        "type",
        "schema_version",
        "stage_id",
        "owner_binding",
        "account_id",
        "content_type",
        "byte_size",
        "source_sha256",
        "manifest_sha256",
        "semantic_sha256",
        "source_schema",
        "source_schema_version",
        "provider_scope",
        "export_id",
        "exported_at",
        "created_at",
        "expires_at",
    }
)


class ImportStageError(RuntimeError):
    """Base class for sanitized staging failures."""


class ImportStageConfigurationError(ImportStageError):
    """The private staging root or limits are unsafe."""


class ImportStageConflict(ImportStageError):
    """The owner already has a stage or a quota cannot be reserved."""


class ImportStageNotFound(ImportStageError):
    """The exact owner-bound stage is absent, expired, or unauthorized."""


class ImportStageCorrupt(ImportStageError):
    """A private staged object failed integrity revalidation."""


class _UnauthenticatedStageMetadata(ImportStageCorrupt):
    """A disposable stage was signed by an unavailable or invalid key."""


@dataclass(frozen=True, slots=True)
class ImportStageLimits:
    max_source_bytes: int = 64 * 1024 * 1024
    max_owner_staging_bytes: int = 128 * 1024 * 1024
    max_total_staging_bytes: int = 512 * 1024 * 1024
    free_space_reserve_bytes: int = 2 * 1024 * 1024 * 1024
    ttl_seconds: int = 15 * 60
    import_limits: ImportLimits = field(default_factory=lambda: DEFAULT_LIMITS)

    def __post_init__(self) -> None:
        numeric = (
            self.max_source_bytes,
            self.max_owner_staging_bytes,
            self.max_total_staging_bytes,
            self.free_space_reserve_bytes,
            self.ttl_seconds,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric):
            raise ImportStageConfigurationError("stage limits must be integers")
        if not 1 <= self.max_source_bytes <= 512 * 1024 * 1024:
            raise ImportStageConfigurationError("max_source_bytes is invalid")
        if self.max_owner_staging_bytes < self.max_source_bytes:
            raise ImportStageConfigurationError("owner staging quota is too small")
        if self.max_total_staging_bytes < self.max_owner_staging_bytes:
            raise ImportStageConfigurationError("global staging quota is too small")
        if self.free_space_reserve_bytes < 0:
            raise ImportStageConfigurationError("free-space reserve is invalid")
        if not 60 <= self.ttl_seconds <= 24 * 60 * 60:
            raise ImportStageConfigurationError("stage TTL is invalid")
        if not isinstance(self.import_limits, ImportLimits):
            raise ImportStageConfigurationError("import_limits is invalid")
        if self.import_limits.max_source_bytes < self.max_source_bytes:
            raise ImportStageConfigurationError(
                "import parser limit must cover the staging source limit"
            )


@dataclass(frozen=True, slots=True)
class StagedImport:
    stage_id: str
    account_id: str = field(repr=False)
    owner_binding: str = field(repr=False)
    content_type: str
    byte_size: int
    source_sha256: str = field(repr=False)
    manifest_sha256: str = field(repr=False)
    semantic_sha256: str = field(repr=False)
    source_schema: str
    source_schema_version: int
    provider_scope: str = field(repr=False)
    export_id: str = field(repr=False)
    exported_at: str = field(repr=False)
    created_at: int
    expires_at: int
    bundle: ClipModeImportBundle = field(repr=False, compare=False)
    token: str = field(repr=False, compare=False)
    source: bytes = field(repr=False, compare=False)

    def to_public_dict(self) -> dict[str, Any]:
        """Return only non-authorizing review metadata.

        The account binding, provider identifiers, evidence digests, bundle,
        and bearer token stay behind the actor-bound HTTP adapter.
        """

        return {
            "schema_version": STAGE_SCHEMA_VERSION,
            "stage_id": self.stage_id,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "source_schema": self.source_schema,
            "source_schema_version": self.source_schema_version,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class ConsumedImportStage:
    stage_id: str
    receipt_id: str
    import_decision_sha256: str = field(repr=False)
    decision_plan_sha256: str = field(repr=False)
    plan_sha256: str = field(repr=False)
    stable_graph_sha256: str = field(repr=False)
    expires_at: int


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ImportStageCorrupt("stage metadata is invalid") from error


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise ImportStageNotFound("provider import stage was not found")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as error:
        raise ImportStageNotFound("provider import stage was not found") from error


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_read_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_create_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _repository_root() -> Path:
    return Path(__file__).absolute().parents[2]


def _normalized_absolute(path: str | os.PathLike[str], label: str) -> Path:
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise ImportStageConfigurationError(f"{label} must be a text path")
    if not raw or "\x00" in raw or not os.path.isabs(raw) or raw.startswith("//"):
        raise ImportStageConfigurationError(f"{label} must be an absolute path")
    normalized = os.path.abspath(raw)
    if os.path.normpath(raw) != raw:
        raise ImportStageConfigurationError(f"{label} must use a canonical spelling")
    return Path(normalized)


def _overlaps(one: Path, two: Path) -> bool:
    try:
        common = Path(os.path.commonpath((os.fspath(one), os.fspath(two))))
    except ValueError:
        return False
    return common == one or common == two


def _resolved_for_overlap(path: Path) -> Path:
    """Resolve existing ancestors so protected symlink aliases also overlap."""

    return Path(os.path.realpath(os.fspath(path)))


def _validate_directory(fd: int, expected_device: int | None = None) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise ImportStageConfigurationError("stage path must contain directories")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
        raise ImportStageConfigurationError(
            "stage directories must be owner-only and owned by the service user"
        )
    if expected_device is not None and info.st_dev != expected_device:
        raise ImportStageConfigurationError("stage directories must remain on one device")
    return info


def _validate_file(
    fd: int,
    *,
    expected_device: int,
    expected_size: int | None = None,
) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise ImportStageCorrupt("staged object is not a regular file")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _FILE_MODE:
        raise ImportStageCorrupt("staged object permissions are invalid")
    if info.st_nlink != 1:
        raise ImportStageCorrupt("staged object has an unexpected hard link")
    if info.st_dev != expected_device:
        raise ImportStageCorrupt("staged object moved to another device")
    if expected_size is not None and info.st_size != expected_size:
        raise ImportStageCorrupt("staged object size changed")
    return info


def _validate_lock_file(fd: int, *, expected_device: int) -> os.stat_result:
    try:
        return _validate_file(fd, expected_device=expected_device)
    except ImportStageCorrupt as error:
        raise ImportStageConfigurationError("stage lock integrity is invalid") from error


def _open_or_create_absolute_root(path: Path) -> int:
    parts = path.parts
    if len(parts) < 2 or parts[0] != os.path.sep:
        raise ImportStageConfigurationError("stage root is invalid")
    try:
        current = os.open(os.path.sep, _directory_flags())
    except OSError as error:
        raise ImportStageConfigurationError("filesystem root is unavailable") from error
    try:
        for index, name in enumerate(parts[1:], start=1):
            final = index == len(parts) - 1
            try:
                os.mkdir(name, _DIRECTORY_MODE, dir_fd=current)
                os.fsync(current)
            except FileExistsError:
                pass
            except OSError as error:
                raise ImportStageConfigurationError(
                    "stage root could not be created safely"
                ) from error
            try:
                next_fd = os.open(name, _directory_flags(), dir_fd=current)
            except OSError as error:
                raise ImportStageConfigurationError(
                    "stage root contains a symlink or non-directory"
                ) from error
            os.close(current)
            current = next_fd
            if final:
                _validate_directory(current)
        return current
    except BaseException:
        os.close(current)
        raise


@dataclass(slots=True)
class _ProcessLockState:
    """One process-local coordinator for every open of one flock inode."""

    thread_lock: Any = field(default_factory=threading.RLock)
    references: int = 0
    active_owner_thread: int | None = None
    active_depth: int = 0


_PROCESS_LOCK_REGISTRY_PID = os.getpid()
_PROCESS_LOCK_REGISTRY_GUARD = threading.RLock()
_PROCESS_LOCK_REGISTRY: dict[tuple[int, int], _ProcessLockState] = {}


def _reset_process_lock_registry_after_fork() -> None:
    """Drop locks that may be owned by vanished parent threads in a child."""

    global _PROCESS_LOCK_REGISTRY_PID
    global _PROCESS_LOCK_REGISTRY_GUARD
    global _PROCESS_LOCK_REGISTRY
    _PROCESS_LOCK_REGISTRY_PID = os.getpid()
    _PROCESS_LOCK_REGISTRY_GUARD = threading.RLock()
    _PROCESS_LOCK_REGISTRY = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_process_lock_registry_after_fork)


def _prepare_process_lock_registry() -> None:
    # ``register_at_fork`` handles supported POSIX runtimes.  Keep this lazy
    # PID epoch as a fail-safe for runtimes that fork without invoking it.
    if os.getpid() != _PROCESS_LOCK_REGISTRY_PID:
        _reset_process_lock_registry_after_fork()


def _retain_process_lock_state(identity: tuple[int, int]) -> _ProcessLockState:
    _prepare_process_lock_registry()
    with _PROCESS_LOCK_REGISTRY_GUARD:
        state = _PROCESS_LOCK_REGISTRY.get(identity)
        if state is None:
            state = _ProcessLockState()
            _PROCESS_LOCK_REGISTRY[identity] = state
        state.references += 1
        return state


def _release_process_lock_state(
    identity: tuple[int, int], state: _ProcessLockState
) -> None:
    _prepare_process_lock_registry()
    with _PROCESS_LOCK_REGISTRY_GUARD:
        current = _PROCESS_LOCK_REGISTRY.get(identity)
        # A fork resets the child's registry before inherited instances rebind.
        # An old state is therefore intentionally absent rather than corrupt.
        if current is not state:
            return
        if state.references <= 0:
            raise ImportStageConfigurationError(
                "stage process lock reference count is invalid"
            )
        if state.references == 1:
            if state.active_owner_thread is not None or state.active_depth:
                raise ImportStageConfigurationError(
                    "active stage process lock cannot be released"
                )
            state.references = 0
            del _PROCESS_LOCK_REGISTRY[identity]
        else:
            state.references -= 1


class PrivateImportStageStore:
    """Short-lived private source storage with exact owner/token binding."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        identity_key: SecretValue,
        stage_signing_key: SecretValue,
        *,
        account_authorizer: Callable[[str, str], bool],
        forbidden_roots: Sequence[str | os.PathLike[str]],
        previous_stage_signing_keys: Sequence[SecretValue] = (),
        limits: ImportStageLimits = ImportStageLimits(),
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(identity_key, SecretValue):
            raise TypeError("identity_key must be a SecretValue")
        if not isinstance(stage_signing_key, SecretValue):
            raise TypeError("stage_signing_key must be a SecretValue")
        identity_bytes = identity_key.reveal().encode("utf-8")
        signing_bytes = stage_signing_key.reveal().encode("utf-8")
        if len(identity_bytes) < 32:
            raise ValueError("identity_key must contain at least 32 bytes")
        if len(signing_bytes) < 32:
            raise ValueError("stage_signing_key must contain at least 32 bytes")
        if hmac.compare_digest(identity_bytes, signing_bytes):
            raise ValueError("identity and stage signing keys must be distinct")
        if isinstance(previous_stage_signing_keys, (str, bytes)) or not isinstance(
            previous_stage_signing_keys, Sequence
        ):
            raise TypeError("previous_stage_signing_keys must be a sequence")
        verification_keys = [signing_bytes]
        for previous in previous_stage_signing_keys:
            if not isinstance(previous, SecretValue):
                raise TypeError("previous stage signing keys must be SecretValue")
            value = previous.reveal().encode("utf-8")
            if len(value) < 32:
                raise ValueError("previous stage signing keys must contain 32 bytes")
            if hmac.compare_digest(value, identity_bytes):
                raise ValueError(
                    "previous stage signing keys must differ from identity_key"
                )
            if not any(hmac.compare_digest(value, item) for item in verification_keys):
                verification_keys.append(value)
        if not callable(account_authorizer):
            raise TypeError("account_authorizer must be callable")
        if isinstance(forbidden_roots, (str, bytes)) or not isinstance(
            forbidden_roots, Sequence
        ):
            raise TypeError("forbidden_roots must be an explicit sequence")
        if not forbidden_roots:
            raise ImportStageConfigurationError(
                "forbidden_roots must enumerate protected runtime storage"
            )
        if not isinstance(limits, ImportStageLimits):
            raise TypeError("limits must be ImportStageLimits")
        if not callable(clock):
            raise TypeError("clock must be callable")
        selected = _normalized_absolute(root, "stage root")
        forbidden = [_repository_root()]
        forbidden.extend(
            _normalized_absolute(item, "forbidden root") for item in forbidden_roots
        )
        selected_resolved = _resolved_for_overlap(selected)
        if any(
            _overlaps(selected, item)
            or _overlaps(selected_resolved, _resolved_for_overlap(item))
            for item in forbidden
        ):
            raise ImportStageConfigurationError(
                "stage root must be disjoint from protected storage"
            )
        self.root = selected
        self._limits = limits
        self._clock = clock
        self._account_authorizer = account_authorizer
        self._identity_key = identity_bytes
        self._active_signing_key = signing_bytes
        self._verification_keys = tuple(verification_keys)
        self._pid = os.getpid()
        self._thread_lock = threading.RLock()
        self._critical_depth = 0
        self._root_fd = -1
        self._device = -1
        self._owners_fd = -1
        self._lock_fd = -1
        self._lock_identity: tuple[int, int] | None = None
        self._process_lock_state: _ProcessLockState | None = None
        self._process_lock_pid: int | None = None
        try:
            self._root_fd = _open_or_create_absolute_root(selected)
            root_info = _validate_directory(self._root_fd)
            self._device = root_info.st_dev
            self._verify_identity_marker()
            self._owners_fd = self._open_or_create_directory(self._root_fd, "owners")
            self._lock_fd = os.open(
                ".stages.lock",
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                _FILE_MODE,
                dir_fd=self._root_fd,
            )
            lock_info = _validate_lock_file(
                self._lock_fd, expected_device=self._device
            )
            self._lock_identity = (lock_info.st_dev, lock_info.st_ino)
            self._process_lock_state = _retain_process_lock_state(
                self._lock_identity
            )
            self._process_lock_pid = self._pid
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        # A fully closed instance stays idempotent even after a later fork.  In
        # particular, there is no descriptor to refresh and no child registry
        # reference to create merely so it can be released again.
        if self._process_lock_state is None and all(
            getattr(self, name, -1) < 0
            for name in ("_lock_fd", "_owners_fd", "_root_fd")
        ):
            return
        self._prepare_after_fork()
        state = self._process_lock_state
        identity = self._lock_identity
        state_pid = self._process_lock_pid

        def close_descriptors() -> None:
            if self._critical_depth:
                raise ImportStageConfigurationError(
                    "stage store cannot close during an active operation"
                )
            for name in ("_lock_fd", "_owners_fd", "_root_fd"):
                fd = getattr(self, name, -1)
                if isinstance(fd, int) and fd >= 0:
                    # Clear the published number before ``close``.  Python can
                    # run a signal handler between the system call and the next
                    # bytecode; a reentrant close must not close the same number
                    # again after the kernel has made it reusable.
                    setattr(self, name, -1)
                    os.close(fd)

        # Constructor cleanup can arrive before the named lock was retained.
        # No operation can use that unpublished partial instance, so only its
        # instance lock is needed.
        if state is None:
            with self._thread_lock:
                close_descriptors()
            return
        if identity is None or state_pid != os.getpid():
            raise ImportStageConfigurationError(
                "stage process lock identity is unavailable"
            )

        # Keep the same process-state -> instance ordering as ``_locked``.  A
        # close from another thread therefore waits for the active operation;
        # a same-thread close from inside an operation fails before touching an
        # FD.  This also prevents close from clearing the instance while an
        # operation has claimed the shared state but is waiting on its instance
        # lock.
        with state.thread_lock:
            if state.active_owner_thread is not None or state.active_depth:
                raise ImportStageConfigurationError(
                    "stage store cannot close during an active operation"
                )
            with self._thread_lock:
                # Another concurrent close may have completed while this one
                # waited for the shared state.  Treat that as an idempotent
                # close, but fail closed on any other state substitution.
                if self._process_lock_state is None:
                    if all(
                        getattr(self, name, -1) < 0
                        for name in ("_lock_fd", "_owners_fd", "_root_fd")
                    ):
                        return
                    raise ImportStageConfigurationError(
                        "stage process lock identity is unavailable"
                    )
                if (
                    self._process_lock_state is not state
                    or self._process_lock_pid != os.getpid()
                    or self._lock_identity != identity
                ):
                    raise ImportStageConfigurationError(
                        "stage process lock identity changed during close"
                    )
                close_descriptors()
                # Keep the retained state on the instance until release has
                # succeeded.  If an exceptional runtime makes release fail, a
                # later close can safely retry instead of silently leaking the
                # registry reference.
                _release_process_lock_state(identity, state)
                self._process_lock_state = None
                self._process_lock_pid = None

    def __enter__(self) -> PrivateImportStageStore:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._prepare_after_fork()
        state = self._process_lock_state
        if (
            state is None
            or self._process_lock_pid != os.getpid()
            or self._lock_identity is None
        ):
            raise ImportStageConfigurationError(
                "stage process lock identity is unavailable"
            )
        owner_thread = threading.get_ident()

        # Lock ordering is process-wide state -> instance state -> flock.  In
        # particular, a waiter never holds an instance lock while another
        # instance's active callback tries to fail-fast as nested work.
        with state.thread_lock:
            if state.active_owner_thread is not None or state.active_depth:
                raise ImportStageConfigurationError(
                    "nested stage store operations are unsupported"
                )
            state.active_owner_thread = owner_thread
            state.active_depth = 1
            try:
                with self._thread_lock:
                    if self._lock_fd < 0:
                        raise ImportStageConfigurationError("stage store is closed")
                    if self._critical_depth:
                        raise ImportStageConfigurationError(
                            "nested stage store operations are unsupported"
                        )
                    lock_fd = self._lock_fd
                    acquisition_pid = os.getpid()
                    flock_acquired = False
                    # Mark the instance active before the potentially blocking
                    # flock so a same-thread signal cannot close or reenter it.
                    self._critical_depth = 1
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX)
                        flock_acquired = True
                        self._validate_current_lock(lock_fd)
                        yield
                    finally:
                        try:
                            # A child unwinding a context inherited across fork
                            # shares the parent's open-file description.  It
                            # must never unlock the still-active parent lease.
                            if flock_acquired and os.getpid() == acquisition_pid:
                                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        finally:
                            self._critical_depth = 0
            finally:
                state.active_depth = 0
                state.active_owner_thread = None

    def _prepare_after_fork(self) -> None:
        """Discard a possibly orphan-owned Python lock before child use."""

        if os.getpid() == self._pid:
            return
        # POSIX fork leaves only the calling thread alive.  A lock inherited
        # from another parent thread can never be released in the child, so it
        # must be replaced before attempting to acquire it.
        self._thread_lock = threading.RLock()
        self._refresh_lock_after_fork()

    def _validate_current_lock(self, held_fd: int) -> None:
        if self._lock_identity is None:
            raise ImportStageConfigurationError("stage lock identity is unavailable")
        held = _validate_lock_file(held_fd, expected_device=self._device)
        if (held.st_dev, held.st_ino) != self._lock_identity:
            raise ImportStageConfigurationError("held stage lock identity changed")
        try:
            named_fd = os.open(
                ".stages.lock",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
        except OSError as error:
            raise ImportStageConfigurationError(
                "named stage lock is unavailable"
            ) from error
        try:
            named = _validate_lock_file(named_fd, expected_device=self._device)
            if (named.st_dev, named.st_ino) != self._lock_identity:
                raise ImportStageConfigurationError(
                    "named stage lock identity changed"
                )
        finally:
            os.close(named_fd)

    def _refresh_lock_after_fork(self) -> None:
        """Give a forked child its own flock open-file description."""

        current_pid = os.getpid()
        if current_pid == self._pid:
            return
        if self._root_fd < 0 or self._lock_fd < 0 or self._lock_identity is None:
            raise ImportStageConfigurationError("stage store is closed")
        try:
            replacement = os.open(
                ".stages.lock",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
        except OSError as error:
            raise ImportStageConfigurationError(
                "stage lock could not be reopened after fork"
            ) from error
        try:
            info = _validate_lock_file(replacement, expected_device=self._device)
            if (info.st_dev, info.st_ino) != self._lock_identity:
                raise ImportStageConfigurationError(
                    "stage lock identity changed after fork"
                )
        except BaseException:
            os.close(replacement)
            raise
        inherited = self._lock_fd
        self._lock_fd = replacement
        self._pid = current_pid
        self._critical_depth = 0
        os.close(inherited)
        self._process_lock_state = _retain_process_lock_state(self._lock_identity)
        self._process_lock_pid = current_pid

    def _open_or_create_directory(self, parent_fd: int, name: str) -> int:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", name):
            raise ImportStageConfigurationError("derived stage directory is invalid")
        try:
            os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise ImportStageConfigurationError(
                "stage directory could not be created safely"
            ) from error
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise ImportStageConfigurationError(
                "stage directory contains a symlink or non-directory"
            ) from error
        try:
            _validate_directory(fd, self._device)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _open_directory(self, parent_fd: int, name: str) -> int:
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise ImportStageNotFound("provider import stage was not found") from error
        try:
            _validate_directory(fd, self._device)
            return fd
        except BaseException:
            os.close(fd)
            raise ImportStageNotFound("provider import stage was not found") from None

    def owner_binding(self, owner_user_id: str) -> str:
        if not isinstance(owner_user_id, str) or not _ACCOUNT_RE.fullmatch(owner_user_id):
            raise ValueError("owner_user_id is invalid")
        return hmac.new(
            self._identity_key,
            b"castle-provider-import-owner-v1\0" + owner_user_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _authorize_account(self, owner_user_id: str, account_id: str) -> None:
        try:
            allowed = self._account_authorizer(owner_user_id, account_id)
        except Exception:
            raise ImportStageNotFound("provider import stage was not found") from None
        if allowed is not True:
            raise ImportStageNotFound("provider import stage was not found")

    @staticmethod
    def _token_mac(key: bytes, stage_id: str, nonce: bytes) -> bytes:
        return hmac.new(
            key,
            b"castle-provider-import-stage-token-v1\0"
            + stage_id.encode("ascii")
            + b"\0"
            + nonce,
            hashlib.sha256,
        ).digest()

    def _new_token(self, stage_id: str) -> str:
        nonce = secrets.token_bytes(32)
        return (
            f"{_b64encode(nonce)}."
            f"{_b64encode(self._token_mac(self._active_signing_key, stage_id, nonce))}"
        )

    def _verify_token(self, stage_id: str, token: str) -> None:
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise ImportStageNotFound("provider import stage was not found")
        nonce_text, signature_text = token.split(".", 1)
        nonce = _b64decode(nonce_text)
        supplied = _b64decode(signature_text)
        if len(nonce) != 32 or len(supplied) != 32:
            raise ImportStageNotFound("provider import stage was not found")
        valid = False
        for key in self._verification_keys:
            expected = self._token_mac(key, stage_id, nonce)
            valid |= hmac.compare_digest(supplied, expected)
        if not valid:
            raise ImportStageNotFound("provider import stage was not found")

    @staticmethod
    def _validate_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
            raise ImportStageCorrupt("stage metadata payload is invalid")
        if (
            payload.get("type") != STAGE_TOKEN_TYPE
            or payload.get("schema_version") != STAGE_SCHEMA_VERSION
            or not isinstance(payload.get("stage_id"), str)
            or not _STAGE_ID_RE.fullmatch(payload["stage_id"])
            or not isinstance(payload.get("owner_binding"), str)
            or not _OWNER_BINDING_RE.fullmatch(payload["owner_binding"])
            or not isinstance(payload.get("account_id"), str)
            or not _ACCOUNT_RE.fullmatch(payload["account_id"])
            or payload.get("content_type") not in _CONTENT_TYPES
            or isinstance(payload.get("byte_size"), bool)
            or not isinstance(payload.get("byte_size"), int)
            or payload["byte_size"] <= 0
            or not isinstance(payload.get("created_at"), int)
            or not isinstance(payload.get("expires_at"), int)
            or payload["expires_at"] <= payload["created_at"]
        ):
            raise ImportStageCorrupt("stage metadata payload is invalid")
        for key in ("source_sha256", "manifest_sha256", "semantic_sha256"):
            if not isinstance(payload.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{64}", payload[key]
            ):
                raise ImportStageCorrupt("stage metadata payload is invalid")
        for key in ("source_schema", "provider_scope", "export_id", "exported_at"):
            value = payload.get(key)
            if not isinstance(value, str) or not value or len(value) > 500 or "\x00" in value:
                raise ImportStageCorrupt("stage metadata payload is invalid")
        if (
            isinstance(payload.get("source_schema_version"), bool)
            or not isinstance(payload.get("source_schema_version"), int)
            or payload["source_schema_version"] < 1
        ):
            raise ImportStageCorrupt("stage metadata payload is invalid")
        return payload

    @staticmethod
    def _metadata_mac(key: bytes, core: bytes) -> str:
        return hmac.new(
            key,
            b"castle-provider-import-stage-metadata-v1\0" + core,
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _consumed_mac(key: bytes, core: bytes) -> str:
        return hmac.new(
            key,
            b"castle-provider-import-consumed-metadata-v1\0" + core,
            hashlib.sha256,
        ).hexdigest()

    def _owner_directories(self, owner_binding: str, *, create: bool) -> tuple[int, int]:
        if not _OWNER_BINDING_RE.fullmatch(owner_binding):
            raise ImportStageNotFound("provider import stage was not found")
        owner_fd = (
            self._open_or_create_directory(self._owners_fd, owner_binding)
            if create
            else self._open_directory(self._owners_fd, owner_binding)
        )
        try:
            stages_fd = (
                self._open_or_create_directory(owner_fd, "stages")
                if create
                else self._open_directory(owner_fd, "stages")
            )
            return owner_fd, stages_fd
        except BaseException:
            os.close(owner_fd)
            raise

    def _consumed_directory(self, owner_fd: int, *, create: bool) -> int:
        return (
            self._open_or_create_directory(owner_fd, "consumed")
            if create
            else self._open_directory(owner_fd, "consumed")
        )

    @staticmethod
    def _consumed_filename(stage_id: str) -> str:
        if not isinstance(stage_id, str) or not _STAGE_ID_RE.fullmatch(stage_id):
            raise ImportStageNotFound("provider import stage was not found")
        return f"{stage_id}.json"

    @staticmethod
    def _validate_consumed_payload(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _CONSUMED_PAYLOAD_KEYS:
            raise ImportStageCorrupt("consumed stage metadata is invalid")
        if (
            value.get("type") != _CONSUMED_TOKEN_TYPE
            or value.get("schema_version") != STAGE_SCHEMA_VERSION
            or not isinstance(value.get("stage_id"), str)
            or not _STAGE_ID_RE.fullmatch(value["stage_id"])
            or not isinstance(value.get("owner_binding"), str)
            or not _OWNER_BINDING_RE.fullmatch(value["owner_binding"])
            or not isinstance(value.get("account_id"), str)
            or not _ACCOUNT_RE.fullmatch(value["account_id"])
            or not isinstance(value.get("receipt_id"), str)
            or not _ACCOUNT_RE.fullmatch(value["receipt_id"])
            or type(value.get("consumed_at")) is not int
            or type(value.get("expires_at")) is not int
            or value["expires_at"] <= value["consumed_at"]
        ):
            raise ImportStageCorrupt("consumed stage metadata is invalid")
        for key in (
            "import_decision_sha256",
            "decision_plan_sha256",
            "plan_sha256",
            "stable_graph_sha256",
        ):
            if not isinstance(value.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{64}", value[key]
            ):
                raise ImportStageCorrupt("consumed stage metadata is invalid")
        return value

    def _read_file(
        self,
        parent_fd: int,
        name: str,
        maximum: int,
        *,
        missing_is_not_found: bool = False,
    ) -> bytes:
        try:
            fd = os.open(name, _file_read_flags(), dir_fd=parent_fd)
        except FileNotFoundError as error:
            if missing_is_not_found:
                raise ImportStageNotFound(
                    "provider import stage was not found"
                ) from error
            raise ImportStageCorrupt("staged object is unavailable") from error
        except OSError as error:
            raise ImportStageCorrupt("staged object is unavailable") from error
        try:
            info = _validate_file(fd, expected_device=self._device)
            if info.st_size > maximum:
                raise ImportStageCorrupt("staged object exceeds its bound")
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            if len(value) != info.st_size or len(value) > maximum:
                raise ImportStageCorrupt("staged object changed while reading")
            after = os.fstat(fd)
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_uid != os.geteuid()
                or stat.S_IMODE(after.st_mode) != _FILE_MODE
                or after.st_nlink != 1
                or (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                != (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            ):
                raise ImportStageCorrupt("staged object changed while reading")
            return value
        finally:
            os.close(fd)

    def _write_file(self, parent_fd: int, name: str, value: bytes) -> None:
        fd = -1
        try:
            fd = os.open(name, _file_create_flags(), _FILE_MODE, dir_fd=parent_fd)
            view = memoryview(value)
            offset = 0
            while offset < len(view):
                written = os.write(fd, view[offset : offset + 1024 * 1024])
                if written <= 0:
                    raise ImportStageCorrupt("staged object could not be persisted")
                offset += written
            os.fsync(fd)
            _validate_file(fd, expected_device=self._device, expected_size=len(value))
        except OSError as error:
            raise ImportStageConflict("staged object could not be reserved") from error
        finally:
            if fd >= 0:
                os.close(fd)

    def _verify_identity_marker(self) -> None:
        expected = hmac.new(
            self._identity_key,
            b"castle-provider-import-stage-root-identity-v1",
            hashlib.sha256,
        ).hexdigest().encode("ascii")
        created = False
        try:
            self._write_file(self._root_fd, ".identity", expected)
            os.fsync(self._root_fd)
            created = True
        except ImportStageConflict:
            pass
        if created:
            return
        try:
            observed = self._read_file(
                self._root_fd, ".identity", _IDENTITY_MARKER_BYTES
            )
        except ImportStageError as error:
            raise ImportStageConfigurationError(
                "stage identity marker is unavailable"
            ) from error
        if not hmac.compare_digest(observed, expected):
            raise ImportStageConfigurationError(
                "stage identity key does not match the private root"
            )

    def _metadata(self, stage_fd: int) -> tuple[dict[str, Any], str]:
        raw = self._read_file(stage_fd, "stage.json", _METADATA_MAX_BYTES)
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ImportStageCorrupt("stage metadata is invalid") from error
        if not isinstance(value, dict) or set(value) != _METADATA_KEYS:
            raise ImportStageCorrupt("stage metadata is invalid")
        token_sha256 = value.get("token_sha256")
        metadata_mac = value.get("metadata_mac")
        if (
            not isinstance(token_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", token_sha256)
            or not isinstance(metadata_mac, str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata_mac)
        ):
            raise ImportStageCorrupt("stage metadata authentication is invalid")
        core_value = {"payload": value.get("payload"), "token_sha256": token_sha256}
        core = _canonical_json(core_value)
        authenticated = False
        for key in self._verification_keys:
            authenticated |= hmac.compare_digest(
                metadata_mac, self._metadata_mac(key, core)
            )
        if not authenticated:
            raise _UnauthenticatedStageMetadata(
                "stage metadata signing key is unavailable"
            )
        if not hmac.compare_digest(raw, _canonical_json({
            **core_value,
            "metadata_mac": metadata_mac,
        })):
            raise ImportStageCorrupt("stage metadata is not canonical")
        payload = self._validate_payload(value.get("payload"))
        return payload, token_sha256

    def _consumed_metadata(self, consumed_fd: int, stage_id: str) -> tuple[dict[str, Any], str]:
        raw = self._read_file(
            consumed_fd,
            self._consumed_filename(stage_id),
            _CONSUMED_MAX_BYTES,
            missing_is_not_found=True,
        )
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ImportStageCorrupt("consumed stage metadata is invalid") from error
        if not isinstance(value, dict) or set(value) != _CONSUMED_KEYS:
            raise ImportStageCorrupt("consumed stage metadata is invalid")
        token_sha256 = value.get("token_sha256")
        metadata_mac = value.get("metadata_mac")
        if (
            not isinstance(token_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", token_sha256)
            or not isinstance(metadata_mac, str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata_mac)
        ):
            raise ImportStageCorrupt("consumed stage metadata is invalid")
        core_value = {"payload": value.get("payload"), "token_sha256": token_sha256}
        core = _canonical_json(core_value)
        authenticated = False
        for key in self._verification_keys:
            authenticated |= hmac.compare_digest(
                metadata_mac, self._consumed_mac(key, core)
            )
        if not authenticated:
            raise ImportStageNotFound("provider import stage was not found")
        if not hmac.compare_digest(
            raw, _canonical_json({**core_value, "metadata_mac": metadata_mac})
        ):
            raise ImportStageCorrupt("consumed stage metadata is not canonical")
        return self._validate_consumed_payload(value.get("payload")), token_sha256

    def _stage_usage(self) -> tuple[int, dict[str, int]]:
        total = 0
        owners: dict[str, int] = {}
        for owner_name in os.listdir(self._owners_fd):
            if not _OWNER_BINDING_RE.fullmatch(owner_name):
                raise ImportStageCorrupt("unexpected private owner directory")
            owner_fd = self._open_directory(self._owners_fd, owner_name)
            try:
                owner_entries = set(os.listdir(owner_fd))
                if not owner_entries <= {"stages", "consumed"} or "stages" not in owner_entries:
                    raise ImportStageCorrupt("unexpected private owner layout")
                stages_fd = self._open_directory(owner_fd, "stages")
                try:
                    subtotal = 0
                    for stage_name in os.listdir(stages_fd):
                        if not _STAGE_ID_RE.fullmatch(stage_name):
                            raise ImportStageCorrupt("unexpected private stage directory")
                        stage_fd = self._open_directory(stages_fd, stage_name)
                        try:
                            if set(os.listdir(stage_fd)) != {"source.bin", "stage.json"}:
                                raise ImportStageCorrupt(
                                    "stage directory contains unexpected objects"
                                )
                            for filename in ("source.bin", "stage.json"):
                                item = os.open(
                                    filename, _file_read_flags(), dir_fd=stage_fd
                                )
                                try:
                                    info = _validate_file(
                                        item, expected_device=self._device
                                    )
                                    subtotal += info.st_size
                                finally:
                                    os.close(item)
                        finally:
                            os.close(stage_fd)
                    if "consumed" in owner_entries:
                        consumed_fd = self._consumed_directory(owner_fd, create=False)
                        try:
                            for filename in os.listdir(consumed_fd):
                                match = re.fullmatch(r"(stage_[0-9a-f]{32})\.json", filename)
                                if match is None:
                                    raise ImportStageCorrupt(
                                        "unexpected consumed stage object"
                                    )
                                info_fd = os.open(
                                    filename, _file_read_flags(), dir_fd=consumed_fd
                                )
                                try:
                                    info = _validate_file(
                                        info_fd, expected_device=self._device
                                    )
                                    if info.st_size > _CONSUMED_MAX_BYTES:
                                        raise ImportStageCorrupt(
                                            "consumed stage metadata exceeds its bound"
                                        )
                                    subtotal += info.st_size
                                finally:
                                    os.close(info_fd)
                        finally:
                            os.close(consumed_fd)
                    owners[owner_name] = subtotal
                    total += subtotal
                finally:
                    os.close(stages_fd)
            finally:
                os.close(owner_fd)
        return total, owners

    def _remove_stage(self, stages_fd: int, stage_id: str) -> None:
        stage_fd = self._open_directory(stages_fd, stage_id)
        try:
            if set(os.listdir(stage_fd)) != {"source.bin", "stage.json"}:
                raise ImportStageCorrupt("stage directory contains unexpected objects")
            opened: list[int] = []
            try:
                for filename in ("source.bin", "stage.json"):
                    fd = os.open(filename, _file_read_flags(), dir_fd=stage_fd)
                    opened.append(fd)
                    _validate_file(fd, expected_device=self._device)
                for filename in ("source.bin", "stage.json"):
                    os.unlink(filename, dir_fd=stage_fd)
            finally:
                for fd in opened:
                    os.close(fd)
            os.fsync(stage_fd)
        except OSError as error:
            raise ImportStageCorrupt("stage could not be removed safely") from error
        finally:
            os.close(stage_fd)
        try:
            os.rmdir(stage_id, dir_fd=stages_fd)
            os.fsync(stages_fd)
        except OSError as error:
            raise ImportStageCorrupt("stage directory could not be removed safely") from error

    def _remove_incomplete_stage(self, stages_fd: int, stage_id: str) -> None:
        """Recover a create/cancel interrupted while the global lock was held."""

        stage_fd = self._open_directory(stages_fd, stage_id)
        try:
            entries = set(os.listdir(stage_fd))
            if not entries < {"source.bin", "stage.json"}:
                raise ImportStageCorrupt(
                    "incomplete stage directory contains unexpected objects"
                )
            opened: list[int] = []
            try:
                for filename in sorted(entries):
                    fd = os.open(filename, _file_read_flags(), dir_fd=stage_fd)
                    opened.append(fd)
                    _validate_file(fd, expected_device=self._device)
                for filename in sorted(entries):
                    os.unlink(filename, dir_fd=stage_fd)
            except OSError as error:
                raise ImportStageCorrupt(
                    "incomplete stage could not be removed safely"
                ) from error
            finally:
                for fd in opened:
                    os.close(fd)
            os.fsync(stage_fd)
        finally:
            os.close(stage_fd)
        try:
            os.rmdir(stage_id, dir_fd=stages_fd)
            os.fsync(stages_fd)
        except OSError as error:
            raise ImportStageCorrupt(
                "incomplete stage directory could not be removed safely"
            ) from error

    def _expire_locked(self, now: int, *, strict: bool = True) -> int:
        removed = 0
        corrupt = 0
        for owner_name in os.listdir(self._owners_fd):
            if not _OWNER_BINDING_RE.fullmatch(owner_name):
                corrupt += 1
                continue
            try:
                owner_fd = self._open_directory(self._owners_fd, owner_name)
            except ImportStageError:
                corrupt += 1
                continue
            try:
                owner_entries = set(os.listdir(owner_fd))
                if owner_entries == set():
                    stages_fd = self._open_or_create_directory(owner_fd, "stages")
                elif owner_entries <= {"stages", "consumed"} and "stages" in owner_entries:
                    stages_fd = self._open_directory(owner_fd, "stages")
                else:
                    corrupt += 1
                    continue
                try:
                    for stage_id in list(os.listdir(stages_fd)):
                        if not _STAGE_ID_RE.fullmatch(stage_id):
                            corrupt += 1
                            continue
                        try:
                            stage_fd = self._open_directory(stages_fd, stage_id)
                        except ImportStageError:
                            corrupt += 1
                            continue
                        try:
                            entries = set(os.listdir(stage_fd))
                            if entries != {"source.bin", "stage.json"}:
                                os.close(stage_fd)
                                stage_fd = -1
                                try:
                                    self._remove_incomplete_stage(stages_fd, stage_id)
                                except ImportStageError:
                                    corrupt += 1
                                else:
                                    removed += 1
                                continue
                            try:
                                payload, _token_digest = self._metadata(stage_fd)
                            except _UnauthenticatedStageMetadata:
                                os.close(stage_fd)
                                stage_fd = -1
                                try:
                                    self._remove_stage(stages_fd, stage_id)
                                except ImportStageError:
                                    corrupt += 1
                                else:
                                    removed += 1
                                continue
                            except ImportStageCorrupt:
                                corrupt += 1
                                continue
                        finally:
                            if stage_fd >= 0:
                                os.close(stage_fd)
                        if (
                            payload["stage_id"] != stage_id
                            or payload["owner_binding"] != owner_name
                        ):
                            corrupt += 1
                            continue
                        if payload["expires_at"] <= now:
                            try:
                                self._remove_stage(stages_fd, stage_id)
                            except ImportStageError:
                                corrupt += 1
                            else:
                                removed += 1
                finally:
                    os.close(stages_fd)
                if "consumed" in owner_entries:
                    try:
                        consumed_fd = self._consumed_directory(owner_fd, create=False)
                    except ImportStageError:
                        corrupt += 1
                        continue
                    try:
                        for filename in list(os.listdir(consumed_fd)):
                            match = re.fullmatch(r"(stage_[0-9a-f]{32})\.json", filename)
                            if match is None:
                                corrupt += 1
                                continue
                            stage_id = match.group(1)
                            try:
                                payload, _token_digest = self._consumed_metadata(
                                    consumed_fd, stage_id
                                )
                            except ImportStageError:
                                corrupt += 1
                                continue
                            if (
                                payload["stage_id"] != stage_id
                                or payload["owner_binding"] != owner_name
                            ):
                                corrupt += 1
                                continue
                            if payload["expires_at"] <= now:
                                try:
                                    os.unlink(filename, dir_fd=consumed_fd)
                                    os.fsync(consumed_fd)
                                except OSError:
                                    corrupt += 1
                                else:
                                    removed += 1
                    finally:
                        os.close(consumed_fd)
            finally:
                os.close(owner_fd)
        if strict and corrupt:
            raise ImportStageCorrupt("one or more private stages require repair")
        return removed

    def create_stage(
        self,
        owner_user_id: str,
        account_id: str,
        content_type: str,
        source: bytes,
    ) -> StagedImport:
        owner_binding = self.owner_binding(owner_user_id)
        if not isinstance(account_id, str) or not _ACCOUNT_RE.fullmatch(account_id):
            raise ValueError("account_id is invalid")
        if content_type not in _CONTENT_TYPES:
            raise ValueError("content_type is unsupported")
        if type(source) is not bytes or not 0 < len(source) <= self._limits.max_source_bytes:
            raise ValueError("source bytes are invalid")
        with self._locked():
            self._authorize_account(owner_user_id, account_id)
            self._expire_locked(int(self._clock()), strict=False)
            owner_fd, stages_fd = self._owner_directories(owner_binding, create=True)
            try:
                existing = os.listdir(stages_fd)
                if existing:
                    if any(not _STAGE_ID_RE.fullmatch(name) for name in existing):
                        raise ImportStageCorrupt("unexpected private stage directory")
                    raise ImportStageConflict("owner already has an active import stage")
                # The global cross-process lock intentionally covers parsing in
                # this first unrouted slice.  That is stricter than the required
                # one-parser-per-owner boundary and prevents decompression fanout.
                try:
                    bundle = import_clipmode_export(
                        source, limits=self._limits.import_limits
                    )
                except ClipModeImportError as error:
                    raise ValueError(
                        "provider export failed offline validation"
                    ) from error
                expected_kind = (
                    "zip" if content_type == "application/zip" else "json"
                )
                if bundle.provenance.source_kind != expected_kind:
                    raise ValueError(
                        "content_type does not match the provider export"
                    )
                # Offline archive validation may be comparatively expensive.
                # Recheck the mutable account grant at the mutation boundary so
                # a revoke that lands during parsing cannot create a new stage.
                self._authorize_account(owner_user_id, account_id)
                now = int(self._clock())
                stage_id = f"stage_{secrets.token_hex(16)}"
                payload = {
                    "type": STAGE_TOKEN_TYPE,
                    "schema_version": STAGE_SCHEMA_VERSION,
                    "stage_id": stage_id,
                    "owner_binding": owner_binding,
                    "account_id": account_id,
                    "content_type": content_type,
                    "byte_size": len(source),
                    "source_sha256": bundle.provenance.source_sha256,
                    "manifest_sha256": bundle.provenance.manifest_sha256,
                    "semantic_sha256": bundle.provenance.semantic_sha256,
                    "source_schema": bundle.provenance.schema_name,
                    "source_schema_version": bundle.provenance.schema_version,
                    "provider_scope": bundle.provenance.provider_scope,
                    "export_id": bundle.provenance.export_id,
                    "exported_at": bundle.provenance.exported_at,
                    "created_at": now,
                    "expires_at": now + self._limits.ttl_seconds,
                }
                token = self._new_token(stage_id)
                token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
                metadata_core = _canonical_json({
                    "payload": payload,
                    "token_sha256": token_sha256,
                })
                metadata = _canonical_json({
                    "payload": payload,
                    "token_sha256": token_sha256,
                    "metadata_mac": self._metadata_mac(
                        self._active_signing_key, metadata_core
                    ),
                })
                if len(metadata) > _METADATA_MAX_BYTES:
                    raise ImportStageCorrupt("stage metadata exceeds its bound")
                reservation_bytes = len(source) + len(metadata)
                total, owners = self._stage_usage()
                if (
                    owners.get(owner_binding, 0) + reservation_bytes
                    > self._limits.max_owner_staging_bytes
                ):
                    raise ImportStageConflict("owner staging quota would be exceeded")
                if total + reservation_bytes > self._limits.max_total_staging_bytes:
                    raise ImportStageConflict("global staging quota would be exceeded")
                filesystem = os.fstatvfs(self._root_fd)
                free_bytes = filesystem.f_bavail * filesystem.f_frsize
                if (
                    free_bytes - reservation_bytes
                    < self._limits.free_space_reserve_bytes
                ):
                    raise ImportStageConflict("staging free-space reserve would be crossed")
                self._authorize_account(owner_user_id, account_id)
                try:
                    os.mkdir(stage_id, _DIRECTORY_MODE, dir_fd=stages_fd)
                    os.fsync(stages_fd)
                except OSError as error:
                    raise ImportStageConflict("stage could not be reserved") from error
                stage_fd = self._open_directory(stages_fd, stage_id)
                try:
                    self._write_file(stage_fd, "source.bin", source)
                    reread = self._read_file(
                        stage_fd, "source.bin", self._limits.max_source_bytes
                    )
                    if not hmac.compare_digest(
                        hashlib.sha256(reread).hexdigest(), payload["source_sha256"]
                    ):
                        raise ImportStageCorrupt("staged source digest changed")
                    self._write_file(stage_fd, "stage.json", metadata)
                    os.fsync(stage_fd)
                except BaseException:
                    try:
                        for filename in ("source.bin", "stage.json"):
                            try:
                                os.unlink(filename, dir_fd=stage_fd)
                            except FileNotFoundError:
                                pass
                        os.fsync(stage_fd)
                    finally:
                        os.close(stage_fd)
                    try:
                        os.rmdir(stage_id, dir_fd=stages_fd)
                        os.fsync(stages_fd)
                    except OSError:
                        pass
                    raise
                else:
                    os.close(stage_fd)
            finally:
                os.close(stages_fd)
                os.close(owner_fd)
        return StagedImport(bundle=bundle, token=token, source=source, **{
            key: payload[key]
            for key in (
                "stage_id", "account_id", "owner_binding", "content_type",
                "byte_size", "source_sha256", "manifest_sha256",
                "semantic_sha256", "source_schema", "source_schema_version",
                "provider_scope", "export_id", "exported_at", "created_at",
                "expires_at",
            )
        })

    def _load_stage_locked(
        self,
        owner_user_id: str,
        account_id: str,
        owner_binding: str,
        stage_id: str,
        token: str,
    ) -> StagedImport:
        self._authorize_account(owner_user_id, account_id)
        self._verify_token(stage_id, token)
        owner_fd, stages_fd = self._owner_directories(owner_binding, create=False)
        try:
            stage_fd = self._open_directory(stages_fd, stage_id)
            try:
                payload, stored_token_sha256 = self._metadata(stage_fd)
                supplied_token_sha256 = hashlib.sha256(
                    token.encode("ascii")
                ).hexdigest()
                if (
                    not hmac.compare_digest(
                        supplied_token_sha256, stored_token_sha256
                    )
                    or payload["owner_binding"] != owner_binding
                    or payload["account_id"] != account_id
                    or payload["stage_id"] != stage_id
                ):
                    raise ImportStageNotFound(
                        "provider import stage was not found"
                    )
                if payload["expires_at"] <= int(self._clock()):
                    expired = True
                    source = b""
                else:
                    expired = False
                    source = self._read_file(
                        stage_fd, "source.bin", self._limits.max_source_bytes
                    )
            finally:
                os.close(stage_fd)
            if expired:
                self._remove_stage(stages_fd, stage_id)
                raise ImportStageNotFound("provider import stage was not found")
            if len(source) != payload["byte_size"] or not hmac.compare_digest(
                hashlib.sha256(source).hexdigest(), payload["source_sha256"]
            ):
                raise ImportStageCorrupt("staged source integrity check failed")
            try:
                bundle = import_clipmode_export(
                    source, limits=self._limits.import_limits
                )
            except ClipModeImportError as error:
                raise ImportStageCorrupt(
                    "staged source no longer validates"
                ) from error
            expected_kind = (
                "zip" if payload["content_type"] == "application/zip" else "json"
            )
            if bundle.provenance.source_kind != expected_kind:
                raise ImportStageCorrupt("staged source content type changed")
            observed = {
                "source_sha256": bundle.provenance.source_sha256,
                "manifest_sha256": bundle.provenance.manifest_sha256,
                "semantic_sha256": bundle.provenance.semantic_sha256,
                "source_schema": bundle.provenance.schema_name,
                "source_schema_version": bundle.provenance.schema_version,
                "provider_scope": bundle.provenance.provider_scope,
                "export_id": bundle.provenance.export_id,
                "exported_at": bundle.provenance.exported_at,
            }
            if any(payload[key] != value for key, value in observed.items()):
                raise ImportStageCorrupt("staged source evidence changed")
            if payload["expires_at"] <= int(self._clock()):
                self._remove_stage(stages_fd, stage_id)
                raise ImportStageNotFound("provider import stage was not found")
            # Loading is also the common prelude to cancellation.  Recheck the
            # mutable grant after full source validation so a revoke during the
            # parser cannot disclose the stage or authorize its deletion.
            self._authorize_account(owner_user_id, account_id)
            return StagedImport(bundle=bundle, token=token, source=source, **{
                key: payload[key]
                for key in (
                    "stage_id", "account_id", "owner_binding", "content_type",
                    "byte_size", "source_sha256", "manifest_sha256",
                    "semantic_sha256", "source_schema",
                    "source_schema_version", "provider_scope", "export_id",
                    "exported_at", "created_at", "expires_at",
                )
            })
        finally:
            os.close(stages_fd)
            os.close(owner_fd)

    def load_stage(
        self,
        owner_user_id: str,
        account_id: str,
        stage_id: str,
        token: str,
    ) -> StagedImport:
        owner_binding = self.owner_binding(owner_user_id)
        if not isinstance(account_id, str) or not _ACCOUNT_RE.fullmatch(account_id):
            raise ImportStageNotFound("provider import stage was not found")
        if not isinstance(stage_id, str) or not _STAGE_ID_RE.fullmatch(stage_id):
            raise ImportStageNotFound("provider import stage was not found")
        with self._locked():
            return self._load_stage_locked(
                owner_user_id,
                account_id,
                owner_binding,
                stage_id,
                token,
            )

    @contextmanager
    def active_stage_lease(
        self,
        owner_user_id: str,
        account_id: str,
        stage_id: str,
        token: str,
    ) -> Iterator[StagedImport]:
        """Yield one fully revalidated stage while holding its global lease.

        This is the internal load-to-materialize boundary.  The existing
        cross-process stage lock stays held for the entire caller context, so
        cancellation and owner-blind expiry serialize after the caller has
        finished.  Account authorization is checked both during the full
        source load and immediately before the lease becomes active; a revoke
        that lands while the source is being parsed therefore fails closed.

        Callers must expose only :meth:`StagedImport.to_public_dict`; the
        returned object and its bearer token remain process-internal.  The
        context never exposes the lock or any filesystem descriptor.

        The context is intentionally a whole-stage-store exclusive lease.
        Its body must therefore be fixed, bounded, local implementation code:
        never provider/network I/O, untrusted custom-node code, a user callback,
        or a wait on another thread that can enter this store.  The media-store
        materializer is the only intended nested storage boundary and lock
        order is always stage lease before media-store lock, never the reverse.
        """

        owner_binding = self.owner_binding(owner_user_id)
        if not isinstance(account_id, str) or not _ACCOUNT_RE.fullmatch(account_id):
            raise ImportStageNotFound("provider import stage was not found")
        if not isinstance(stage_id, str) or not _STAGE_ID_RE.fullmatch(stage_id):
            raise ImportStageNotFound("provider import stage was not found")
        with self._locked():
            staged = self._load_stage_locked(
                owner_user_id,
                account_id,
                owner_binding,
                stage_id,
                token,
            )

            # The parser and integrity checks above can be comparatively
            # expensive.  Recheck mutable authorization and time at the exact
            # handoff boundary rather than trusting their values from the
            # beginning of the load.
            self._authorize_account(owner_user_id, account_id)
            if staged.expires_at <= int(self._clock()):
                owner_fd, stages_fd = self._owner_directories(
                    owner_binding, create=False
                )
                try:
                    self._remove_stage(stages_fd, stage_id)
                finally:
                    os.close(stages_fd)
                    os.close(owner_fd)
                raise ImportStageNotFound("provider import stage was not found")

            yield staged

    def consume_active_stage(
        self,
        owner_user_id: str,
        staged: StagedImport,
        *,
        receipt_id: str,
        import_decision_sha256: str,
        decision_plan_sha256: str,
        plan_sha256: str,
        stable_graph_sha256: str,
    ) -> ConsumedImportStage:
        """Replace the currently leased source with a signed replay tombstone."""

        state = self._process_lock_state
        if (
            self._critical_depth != 1
            or state is None
            or state.active_owner_thread != threading.get_ident()
            or not isinstance(staged, StagedImport)
        ):
            raise ImportStageConfigurationError(
                "stage consumption requires the active stage lease"
            )
        owner_binding = self.owner_binding(owner_user_id)
        if owner_binding != staged.owner_binding:
            raise ImportStageNotFound("provider import stage was not found")
        if not isinstance(receipt_id, str) or not _ACCOUNT_RE.fullmatch(receipt_id):
            raise ValueError("receipt_id is invalid")
        digests = {
            "import_decision_sha256": import_decision_sha256,
            "decision_plan_sha256": decision_plan_sha256,
            "plan_sha256": plan_sha256,
            "stable_graph_sha256": stable_graph_sha256,
        }
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in digests.values()
        ):
            raise ValueError("consumed stage evidence is invalid")
        self._verify_token(staged.stage_id, staged.token)
        self._authorize_account(owner_user_id, staged.account_id)
        now = int(self._clock())
        if staged.expires_at <= now:
            raise ImportStageNotFound("provider import stage was not found")
        payload = {
            "type": _CONSUMED_TOKEN_TYPE,
            "schema_version": STAGE_SCHEMA_VERSION,
            "stage_id": staged.stage_id,
            "owner_binding": owner_binding,
            "account_id": staged.account_id,
            "receipt_id": receipt_id,
            **digests,
            "consumed_at": now,
            "expires_at": staged.expires_at,
        }
        token_sha256 = hashlib.sha256(staged.token.encode("ascii")).hexdigest()
        core_value = {"payload": payload, "token_sha256": token_sha256}
        core = _canonical_json(core_value)
        encoded = _canonical_json(
            {
                **core_value,
                "metadata_mac": self._consumed_mac(self._active_signing_key, core),
            }
        )
        owner_fd, stages_fd = self._owner_directories(owner_binding, create=False)
        try:
            consumed_fd = self._consumed_directory(owner_fd, create=True)
            try:
                filename = self._consumed_filename(staged.stage_id)
                try:
                    self._write_file(consumed_fd, filename, encoded)
                    os.fsync(consumed_fd)
                except ImportStageConflict:
                    observed_payload, observed_token = self._consumed_metadata(
                        consumed_fd, staged.stage_id
                    )
                    if observed_payload != payload or not hmac.compare_digest(
                        observed_token, token_sha256
                    ):
                        raise ImportStageConflict(
                            "consumed stage replay evidence conflicts"
                        ) from None
                self._remove_stage(stages_fd, staged.stage_id)
            finally:
                os.close(consumed_fd)
        finally:
            os.close(stages_fd)
            os.close(owner_fd)
        return ConsumedImportStage(
            stage_id=staged.stage_id,
            receipt_id=receipt_id,
            expires_at=staged.expires_at,
            **digests,
        )

    def load_consumed_stage(
        self,
        owner_user_id: str,
        account_id: str,
        stage_id: str,
        token: str,
        *,
        import_decision_sha256: str,
        decision_plan_sha256: str,
        plan_sha256: str,
        stable_graph_sha256: str,
    ) -> ConsumedImportStage:
        """Resolve one exact owner/token/decision tombstone for lost-response replay."""

        owner_binding = self.owner_binding(owner_user_id)
        filename = self._consumed_filename(stage_id)
        expected_digests = {
            "import_decision_sha256": import_decision_sha256,
            "decision_plan_sha256": decision_plan_sha256,
            "plan_sha256": plan_sha256,
            "stable_graph_sha256": stable_graph_sha256,
        }
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in expected_digests.values()
        ):
            raise ImportStageNotFound("provider import stage was not found")
        with self._locked():
            self._authorize_account(owner_user_id, account_id)
            self._verify_token(stage_id, token)
            owner_fd, _stages_fd = self._owner_directories(owner_binding, create=False)
            try:
                consumed_fd = self._consumed_directory(owner_fd, create=False)
                try:
                    payload, token_sha256 = self._consumed_metadata(
                        consumed_fd, stage_id
                    )
                    expected_token = hashlib.sha256(token.encode("ascii")).hexdigest()
                    if (
                        payload["owner_binding"] != owner_binding
                        or payload["account_id"] != account_id
                        or payload["stage_id"] != stage_id
                        or any(payload[key] != value for key, value in expected_digests.items())
                        or not hmac.compare_digest(token_sha256, expected_token)
                    ):
                        raise ImportStageNotFound("provider import stage was not found")
                    if payload["expires_at"] <= int(self._clock()):
                        try:
                            os.unlink(filename, dir_fd=consumed_fd)
                            os.fsync(consumed_fd)
                        except OSError as error:
                            raise ImportStageCorrupt(
                                "expired consumed stage could not be removed"
                            ) from error
                        raise ImportStageNotFound("provider import stage was not found")
                    if stage_id in os.listdir(_stages_fd):
                        stage_fd = self._open_directory(_stages_fd, stage_id)
                        try:
                            entries = set(os.listdir(stage_fd))
                        finally:
                            os.close(stage_fd)
                        if entries == {"source.bin", "stage.json"}:
                            self._remove_stage(_stages_fd, stage_id)
                        elif entries < {"source.bin", "stage.json"}:
                            self._remove_incomplete_stage(_stages_fd, stage_id)
                        else:
                            raise ImportStageCorrupt(
                                "consumed stage recovery found unexpected objects"
                            )
                    self._authorize_account(owner_user_id, account_id)
                    return ConsumedImportStage(
                        stage_id=stage_id,
                        receipt_id=payload["receipt_id"],
                        import_decision_sha256=payload["import_decision_sha256"],
                        decision_plan_sha256=payload["decision_plan_sha256"],
                        plan_sha256=payload["plan_sha256"],
                        stable_graph_sha256=payload["stable_graph_sha256"],
                        expires_at=payload["expires_at"],
                    )
                finally:
                    os.close(consumed_fd)
            finally:
                os.close(_stages_fd)
                os.close(owner_fd)

    def cancel_stage(
        self,
        owner_user_id: str,
        account_id: str,
        stage_id: str,
        token: str,
    ) -> None:
        owner_binding = self.owner_binding(owner_user_id)
        if not isinstance(account_id, str) or not _ACCOUNT_RE.fullmatch(account_id):
            raise ImportStageNotFound("provider import stage was not found")
        if not isinstance(stage_id, str) or not _STAGE_ID_RE.fullmatch(stage_id):
            raise ImportStageNotFound("provider import stage was not found")
        with self._locked():
            # Full revalidation and unlink share one cross-process critical
            # section; expiry and another cancel cannot slip between them.
            self._load_stage_locked(
                owner_user_id,
                account_id,
                owner_binding,
                stage_id,
                token,
            )
            owner_fd, stages_fd = self._owner_directories(owner_binding, create=False)
            try:
                self._authorize_account(owner_user_id, account_id)
                self._remove_stage(stages_fd, stage_id)
            finally:
                os.close(stages_fd)
                os.close(owner_fd)

    def expire_stages(self) -> int:
        with self._locked():
            return self._expire_locked(int(self._clock()))


__all__ = [
    "ConsumedImportStage",
    "ImportStageConfigurationError",
    "ImportStageConflict",
    "ImportStageCorrupt",
    "ImportStageError",
    "ImportStageLimits",
    "ImportStageNotFound",
    "PrivateImportStageStore",
    "STAGE_SCHEMA_VERSION",
    "STAGE_TOKEN_TYPE",
    "StagedImport",
]
