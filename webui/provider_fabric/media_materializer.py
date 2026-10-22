"""Private owner-keyed materialization of validated provider media originals.

This module is intentionally not routed.  It accepts a revalidated
``StagedImport``, reads only exact bundled ZIP members without extraction, and
publishes immutable, owner-separated content-addressed originals.  It performs
no database, HTTP, network, preview-rendition, stage-deletion, provider, or GPU
work.

Paths are derived only from an HMAC actor binding and a SHA-256 digest.  All
filesystem traversal below uses held directory descriptors and no-follow
opens.  Publication uses same-filesystem link-and-unlink, which is atomic and
cannot replace an existing destination.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import io
import os
import re
import secrets
import stat
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .clipmode_import import (
    DEFAULT_LIMITS,
    ClipModeImportBundle,
    ClipModeImportError,
    ImportLimits,
    ImportedMediaRecord,
    import_clipmode_export,
)
from .config import SecretValue
from .import_storage import StagedImport
from .owner_import_renditions import OwnerProfileRendition
from .owner_import_previews import OwnerAssetPreviewRendition
from .media_signatures import (
    SIGNATURE_PROBE_BYTES,
    classify_declared_media,
    signature_family_for_mime,
)


_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_IDENTITY_MARKER_BYTES = 64
_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_OWNER_BINDING_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIX_RE = re.compile(r"^[0-9a-f]{2}$")
_OPERATION_RE = re.compile(r"^op_[0-9a-f]{32}$")
_INCOMING_FILE_RE = re.compile(r"^blob_([0-9a-f]{64})$")
_ROOT_ENTRIES = frozenset({".identity", ".objects.lock", "owners"})
_OWNER_ENTRIES = frozenset({"incoming", "objects"})
_FAMILY_VERIFICATION_MIME: Mapping[str, str] = {
    "avif": "image/avif",
    "flac": "audio/flac",
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "mp4": "video/mp4",
    "mpeg_audio": "audio/mpeg",
    "ogg": "audio/ogg",
    "png": "image/png",
    "quicktime": "video/quicktime",
    "utf8_text": "text/plain",
    "wav": "audio/wav",
    "webm": "video/webm",
    "webp": "image/webp",
}


class MediaMaterializerError(RuntimeError):
    """Base class for sanitized private-media failures."""


class MediaMaterializerConfigurationError(MediaMaterializerError):
    """The private root, key, or configured limits are unsafe."""


class MediaMaterializerStageInvalid(MediaMaterializerError):
    """The supplied stage is expired, foreign, or no longer exact."""


class MediaMaterializerConflict(MediaMaterializerError):
    """Publication cannot reserve quota or an immutable object."""


class MediaMaterializerCorrupt(MediaMaterializerError):
    """Private object storage or staged media failed revalidation."""


@dataclass(frozen=True, slots=True)
class MediaMaterializerLimits:
    max_member_bytes: int = 256 * 1024 * 1024
    max_total_publish_bytes: int = 512 * 1024 * 1024
    max_owner_object_bytes: int = 64 * 1024 * 1024 * 1024
    max_total_object_bytes: int = 512 * 1024 * 1024 * 1024
    max_owner_allocated_bytes: int = 96 * 1024 * 1024 * 1024
    max_total_allocated_bytes: int = 768 * 1024 * 1024 * 1024
    max_owner_object_count: int = 1_000_000
    max_total_object_count: int = 8_000_000
    max_objects_per_import: int = 2_000
    free_space_reserve_bytes: int = 2 * 1024 * 1024 * 1024
    free_inode_reserve_count: int = 10_000
    import_limits: ImportLimits = field(default_factory=lambda: DEFAULT_LIMITS)

    def __post_init__(self) -> None:
        values = (
            self.max_member_bytes,
            self.max_total_publish_bytes,
            self.max_owner_object_bytes,
            self.max_total_object_bytes,
            self.max_owner_allocated_bytes,
            self.max_total_allocated_bytes,
            self.max_owner_object_count,
            self.max_total_object_count,
            self.max_objects_per_import,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise MediaMaterializerConfigurationError(
                "private-media limits must be positive integers"
            )
        if (
            type(self.free_space_reserve_bytes) is not int
            or self.free_space_reserve_bytes < 0
        ):
            raise MediaMaterializerConfigurationError(
                "private-media free-space reserve is invalid"
            )
        if (
            type(self.free_inode_reserve_count) is not int
            or self.free_inode_reserve_count < 0
        ):
            raise MediaMaterializerConfigurationError(
                "private-media free-inode reserve is invalid"
            )
        if self.max_total_publish_bytes < self.max_member_bytes:
            raise MediaMaterializerConfigurationError(
                "aggregate publish limit is smaller than its member limit"
            )
        if self.max_owner_object_bytes < self.max_member_bytes:
            raise MediaMaterializerConfigurationError(
                "owner object quota is smaller than its member limit"
            )
        if self.max_total_object_bytes < self.max_owner_object_bytes:
            raise MediaMaterializerConfigurationError(
                "global object quota is smaller than its owner quota"
            )
        if self.max_total_allocated_bytes < self.max_owner_allocated_bytes:
            raise MediaMaterializerConfigurationError(
                "global allocated-byte quota is smaller than its owner quota"
            )
        if self.max_total_object_count < self.max_owner_object_count:
            raise MediaMaterializerConfigurationError(
                "global object-count quota is smaller than its owner quota"
            )
        if not isinstance(self.import_limits, ImportLimits):
            raise MediaMaterializerConfigurationError("import_limits is invalid")


@dataclass(frozen=True, slots=True)
class MaterializedOriginal:
    """Internal evidence for one imported media record's original bytes."""

    media_record_id: str = field(repr=False)
    sha256: str = field(repr=False)
    byte_size: int
    declared_mime: str
    signature_family: str
    created: bool


@dataclass(frozen=True, slots=True)
class MaterializedProfileRendition:
    source_character_key: str = field(repr=False)
    canonical_character_id: str = field(repr=False)
    projection_id: str = field(repr=False)
    version_id: str = field(repr=False)
    source_sha256: str = field(repr=False)
    rendition_sha256: str = field(repr=False)
    byte_size: int
    declared_mime: str
    signature_family: str
    width: int
    height: int
    created: bool


@dataclass(frozen=True, slots=True)
class MaterializedAssetPreviewRendition:
    source_media_record_id: str = field(repr=False)
    projection_id: str = field(repr=False)
    version_id: str = field(repr=False)
    source_sha256: str = field(repr=False)
    rendition_sha256: str = field(repr=False)
    byte_size: int
    declared_mime: str
    signature_family: str
    width: int
    height: int
    selection_source: str
    created: bool


@dataclass(frozen=True, slots=True)
class MaterializedImport:
    originals: tuple[MaterializedOriginal, ...] = field(repr=False)
    created_object_count: int
    created_bytes: int
    reused_object_count: int
    profile_renditions: tuple[MaterializedProfileRendition, ...] = field(
        default=(), repr=False
    )
    asset_previews: tuple[MaterializedAssetPreviewRendition, ...] = field(
        default=(), repr=False
    )

    def to_public_dict(self) -> dict[str, int]:
        """Return a bounded summary with no owner, digest, path, or source ID."""

        return {
            "original_count": len(self.originals),
            "created_object_count": self.created_object_count,
            "created_bytes": self.created_bytes,
            "reused_object_count": self.reused_object_count,
        }


@dataclass(frozen=True, slots=True)
class _Declaration:
    member: str = field(repr=False)
    digest: str
    byte_size: int
    mime_type: str
    family: str


@dataclass(frozen=True, slots=True)
class _ObjectUsage:
    logical_bytes: int = 0
    allocated_bytes: int = 0
    object_count: int = 0

    def plus(self, other: "_ObjectUsage") -> "_ObjectUsage":
        return _ObjectUsage(
            self.logical_bytes + other.logical_bytes,
            self.allocated_bytes + other.allocated_bytes,
            self.object_count + other.object_count,
        )


@dataclass(slots=True)
class _OwnerLayout:
    owner_fd: int
    incoming_fd: int
    objects_fd: int
    sha256_fd: int

    def close(self) -> None:
        for name in ("sha256_fd", "objects_fd", "incoming_fd", "owner_fd"):
            fd = getattr(self, name)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)


def _repository_root() -> Path:
    return Path(__file__).absolute().parents[2]


def _canonical_absolute(value: str | os.PathLike[str], label: str) -> Path:
    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not raw.startswith("/")
        or raw.startswith("//")
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise MediaMaterializerConfigurationError(
            f"{label} must be one canonical absolute path"
        )
    return Path(raw)


def _overlaps(one: Path, two: Path) -> bool:
    try:
        common = Path(os.path.commonpath((os.fspath(one), os.fspath(two))))
    except ValueError:
        return False
    return common == one or common == two


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


def _validate_directory(fd: int, *, device: int | None = None) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE
        or (device is not None and info.st_dev != device)
    ):
        raise MediaMaterializerCorrupt("private-media directory integrity failed")
    return info


def _validate_file(
    fd: int,
    *,
    device: int,
    size: int | None = None,
    links: frozenset[int] = frozenset({1}),
) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != _FILE_MODE
        or info.st_dev != device
        or info.st_nlink not in links
        or (size is not None and info.st_size != size)
    ):
        raise MediaMaterializerCorrupt("private-media file integrity failed")
    return info


def _open_or_create_absolute_root(path: Path) -> int:
    parts = path.parts
    if len(parts) < 2 or parts[0] != os.path.sep:
        raise MediaMaterializerConfigurationError("private-media root is invalid")
    try:
        current = os.open(os.path.sep, _directory_flags())
    except OSError as error:
        raise MediaMaterializerConfigurationError(
            "filesystem root is unavailable"
        ) from error
    try:
        for index, name in enumerate(parts[1:], start=1):
            final = index == len(parts) - 1
            try:
                os.mkdir(name, _DIRECTORY_MODE, dir_fd=current)
                os.fsync(current)
            except FileExistsError:
                pass
            except OSError as error:
                raise MediaMaterializerConfigurationError(
                    "private-media root could not be created safely"
                ) from error
            try:
                next_fd = os.open(name, _directory_flags(), dir_fd=current)
            except OSError as error:
                raise MediaMaterializerConfigurationError(
                    "private-media root contains a symlink or non-directory"
                ) from error
            os.close(current)
            current = next_fd
            if final:
                try:
                    _validate_directory(current)
                except MediaMaterializerCorrupt as error:
                    raise MediaMaterializerConfigurationError(
                        "private-media root ownership or mode is unsafe"
                    ) from error
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
        if current is not state:
            return
        if state.references <= 0:
            raise MediaMaterializerConfigurationError(
                "private-media process lock reference count is invalid"
            )
        state.references -= 1
        if state.references == 0:
            if state.active_owner_thread is not None or state.active_depth:
                raise MediaMaterializerConfigurationError(
                    "active private-media process lock cannot be released"
                )
            del _PROCESS_LOCK_REGISTRY[identity]


class PrivateProviderMediaStore:
    """Owner-separated, immutable content-addressed original-media storage."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        identity_key: SecretValue,
        *,
        forbidden_roots: Sequence[str | os.PathLike[str]],
        limits: MediaMaterializerLimits = MediaMaterializerLimits(),
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(identity_key, SecretValue):
            raise TypeError("identity_key must be SecretValue")
        key = identity_key.reveal().encode("utf-8")
        if len(key) < 32:
            raise ValueError("identity_key must contain at least 32 bytes")
        if isinstance(forbidden_roots, (str, bytes)) or not isinstance(
            forbidden_roots, Sequence
        ):
            raise TypeError("forbidden_roots must be an explicit sequence")
        if not forbidden_roots:
            raise MediaMaterializerConfigurationError(
                "forbidden_roots must enumerate protected runtime storage"
            )
        if not isinstance(limits, MediaMaterializerLimits):
            raise TypeError("limits must be MediaMaterializerLimits")
        if not callable(clock):
            raise TypeError("clock must be callable")

        selected = _canonical_absolute(root, "private-media root")
        protected = [_repository_root()]
        protected.extend(
            _canonical_absolute(item, "forbidden root") for item in forbidden_roots
        )
        selected_real = Path(os.path.realpath(selected))
        if any(
            _overlaps(selected, item)
            or _overlaps(selected_real, Path(os.path.realpath(item)))
            for item in protected
        ):
            raise MediaMaterializerConfigurationError(
                "private-media root overlaps protected storage"
            )

        self.root = selected
        self._identity_key = key
        self._limits = limits
        self._clock = clock
        self._pid = os.getpid()
        self._thread_lock = threading.RLock()
        self._critical_depth = 0
        self._root_fd = _open_or_create_absolute_root(selected)
        self._device = _validate_directory(self._root_fd).st_dev
        self._owners_fd = -1
        self._lock_fd = -1
        self._lock_identity: tuple[int, int] | None = None
        self._process_lock_state: _ProcessLockState | None = None
        self._process_lock_pid: int | None = None
        try:
            self._verify_identity_marker()
            self._owners_fd = self._open_or_create_directory(self._root_fd, "owners")
            self._lock_fd = os.open(
                ".objects.lock",
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                _FILE_MODE,
                dir_fd=self._root_fd,
            )
            lock = _validate_file(self._lock_fd, device=self._device)
            self._lock_identity = (lock.st_dev, lock.st_ino)
            os.fsync(self._root_fd)
            self._validate_root_layout()
            self._process_lock_state = _retain_process_lock_state(
                self._lock_identity
            )
            self._process_lock_pid = self._pid
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "PrivateProviderMediaStore":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._prepare_after_fork()
        state = self._process_lock_state
        if state is not None and self._process_lock_pid == os.getpid():
            # Match operation lock ordering: process state, then instance.
            # This prevents close from overtaking an operation that has
            # reserved the process lease but is waiting for the instance lock.
            with state.thread_lock:
                if state.active_owner_thread is not None or state.active_depth:
                    raise MediaMaterializerConfigurationError(
                        "private-media store cannot close during an operation"
                    )
                self._close_instance(state)
        else:
            # Constructor-failure and already-closed paths have no registered
            # process coordinator to acquire.
            self._close_instance(None)

    def _close_instance(self, expected_state: _ProcessLockState | None) -> None:
        with self._thread_lock:
            if self._critical_depth:
                raise MediaMaterializerConfigurationError(
                    "private-media store cannot close during an operation"
                )
            if (
                expected_state is not None
                and self._process_lock_state is not expected_state
            ):
                # A concurrent idempotent close already released this instance.
                return
            for name in ("_lock_fd", "_owners_fd", "_root_fd"):
                fd = getattr(self, name, -1)
                if isinstance(fd, int) and fd >= 0:
                    os.close(fd)
                    setattr(self, name, -1)
            state = self._process_lock_state
            identity = self._lock_identity
            state_pid = self._process_lock_pid
            self._process_lock_state = None
            self._process_lock_pid = None
            if (
                state is not None
                and identity is not None
                and state_pid == os.getpid()
            ):
                _release_process_lock_state(identity, state)

    def owner_binding(self, owner_user_id: str) -> str:
        if not isinstance(owner_user_id, str) or not _OWNER_RE.fullmatch(
            owner_user_id
        ):
            raise ValueError("owner_user_id is invalid")
        return hmac.new(
            self._identity_key,
            b"castle-provider-import-owner-v1\0" + owner_user_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._prepare_after_fork()
        state = self._process_lock_state
        if (
            state is None
            or self._process_lock_pid != os.getpid()
            or self._lock_identity is None
        ):
            raise MediaMaterializerConfigurationError(
                "private-media process lock identity is unavailable"
            )
        owner_thread = threading.get_ident()

        # One process-wide Python lock is shared by every instance that opened
        # this flock inode.  Separate flock open-file descriptions owned by the
        # same process otherwise deadlock when one thread nests two instances.
        with state.thread_lock:
            if state.active_owner_thread is not None or state.active_depth:
                raise MediaMaterializerConfigurationError(
                    "nested private-media operations are unsupported"
                )
            state.active_owner_thread = owner_thread
            state.active_depth = 1
            try:
                with self._thread_lock:
                    if self._lock_fd < 0:
                        raise MediaMaterializerConfigurationError(
                            "private-media store is closed"
                        )
                    if self._critical_depth:
                        raise MediaMaterializerConfigurationError(
                            "nested private-media operations are unsupported"
                        )
                    lock_fd = self._lock_fd
                    acquisition_pid = os.getpid()
                    flock_acquired = False
                    self._critical_depth = 1
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX)
                        flock_acquired = True
                        self._validate_named_lock(lock_fd)
                        self._validate_root_layout()
                        yield
                    finally:
                        try:
                            # A child unwinding an inherited context shares
                            # the parent's open-file description and must not
                            # unlock the still-active parent lease.
                            if flock_acquired and os.getpid() == acquisition_pid:
                                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        finally:
                            self._critical_depth = 0
            finally:
                state.active_depth = 0
                state.active_owner_thread = None

    def _prepare_after_fork(self) -> None:
        if os.getpid() == self._pid:
            return
        self._thread_lock = threading.RLock()
        self._refresh_lock_after_fork()

    def _refresh_lock_after_fork(self) -> None:
        """Give a forked child its own flock open-file description."""

        current_pid = os.getpid()
        if current_pid == self._pid:
            return
        if (
            self._root_fd < 0
            or self._lock_fd < 0
            or self._lock_identity is None
        ):
            raise MediaMaterializerConfigurationError("private-media store is closed")
        try:
            replacement = os.open(
                ".objects.lock",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
            info = _validate_file(replacement, device=self._device)
        except BaseException:
            try:
                os.close(replacement)
            except (OSError, UnboundLocalError):
                pass
            raise MediaMaterializerConfigurationError(
                "private-media lock is unavailable after fork"
            ) from None
        if (info.st_dev, info.st_ino) != self._lock_identity:
            os.close(replacement)
            raise MediaMaterializerConfigurationError(
                "private-media lock identity changed after fork"
            )
        inherited = self._lock_fd
        self._lock_fd = replacement
        self._pid = current_pid
        self._critical_depth = 0
        if inherited >= 0:
            os.close(inherited)
        self._process_lock_state = _retain_process_lock_state(self._lock_identity)
        self._process_lock_pid = current_pid

    def _validate_named_lock(self, held_fd: int) -> None:
        if self._lock_identity is None:
            raise MediaMaterializerConfigurationError(
                "private-media lock identity is unavailable"
            )
        held = _validate_file(held_fd, device=self._device)
        try:
            named_fd = os.open(
                ".objects.lock",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
        except OSError as error:
            raise MediaMaterializerConfigurationError(
                "private-media lock is unavailable"
            ) from error
        try:
            named = _validate_file(named_fd, device=self._device)
            if (
                (held.st_dev, held.st_ino) != self._lock_identity
                or (named.st_dev, named.st_ino) != self._lock_identity
            ):
                raise MediaMaterializerConfigurationError(
                    "private-media lock identity changed"
                )
        finally:
            os.close(named_fd)

    def _validate_root_layout(self) -> None:
        if set(os.listdir(self._root_fd)) != _ROOT_ENTRIES:
            raise MediaMaterializerCorrupt(
                "private-media root contains unexpected objects"
            )

    def _open_or_create_directory(self, parent_fd: int, name: str) -> int:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", name):
            raise MediaMaterializerCorrupt("derived directory name is invalid")
        try:
            os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise MediaMaterializerCorrupt(
                "private-media directory could not be reserved"
            ) from error
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            _validate_directory(fd, device=self._device)
            return fd
        except BaseException:
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass
            raise MediaMaterializerCorrupt(
                "private-media directory integrity failed"
            ) from None

    def _open_directory(self, parent_fd: int, name: str) -> int:
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            _validate_directory(fd, device=self._device)
            return fd
        except BaseException:
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass
            raise MediaMaterializerCorrupt(
                "private-media directory integrity failed"
            ) from None

    def _write_exclusive(self, parent_fd: int, name: str, value: bytes) -> None:
        fd = -1
        try:
            fd = os.open(name, _create_flags(), _FILE_MODE, dir_fd=parent_fd)
            offset = 0
            while offset < len(value):
                written = os.write(fd, value[offset : offset + 1024 * 1024])
                if written <= 0:
                    raise OSError(errno.EIO, "short write")
                offset += written
            os.fsync(fd)
            _validate_file(fd, device=self._device, size=len(value))
        except OSError as error:
            raise MediaMaterializerConflict(
                "private-media file could not be reserved"
            ) from error
        finally:
            if fd >= 0:
                os.close(fd)

    def _read_small_file(self, parent_fd: int, name: str, maximum: int) -> bytes:
        try:
            fd = os.open(name, _read_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise MediaMaterializerCorrupt(
                "private-media marker is unavailable"
            ) from error
        try:
            info = _validate_file(fd, device=self._device)
            if info.st_size > maximum:
                raise MediaMaterializerCorrupt("private-media marker is invalid")
            value = b""
            while len(value) <= maximum:
                chunk = os.read(fd, maximum + 1 - len(value))
                if not chunk:
                    break
                value += chunk
            if len(value) != info.st_size or len(value) > maximum:
                raise MediaMaterializerCorrupt("private-media marker is invalid")
            return value
        finally:
            os.close(fd)

    def _verify_identity_marker(self) -> None:
        expected = hmac.new(
            self._identity_key,
            b"castle-provider-media-root-identity-v1",
            hashlib.sha256,
        ).hexdigest().encode("ascii")
        try:
            self._write_exclusive(self._root_fd, ".identity", expected)
            os.fsync(self._root_fd)
            return
        except MediaMaterializerConflict:
            pass
        try:
            observed = self._read_small_file(
                self._root_fd, ".identity", _IDENTITY_MARKER_BYTES
            )
        except MediaMaterializerError as error:
            raise MediaMaterializerConfigurationError(
                "private-media identity marker is unavailable"
            ) from error
        if not hmac.compare_digest(observed, expected):
            raise MediaMaterializerConfigurationError(
                "identity key does not match the private-media root"
            )

    def _owner_layout(self, owner_binding: str, *, create: bool) -> _OwnerLayout:
        if not _OWNER_BINDING_RE.fullmatch(owner_binding):
            raise MediaMaterializerCorrupt("derived owner binding is invalid")
        owner_exists = owner_binding in os.listdir(self._owners_fd)
        if not owner_exists and not create:
            raise MediaMaterializerCorrupt("private owner directory is unavailable")

        # Existing layouts are evidence, not a migration opportunity.  A
        # read/scan must never allocate hidden inodes, and a partial layout
        # left by a crash or external mutation must fail closed.  Only a truly
        # new owner may create the four directories, after reserving all four
        # inodes up front.
        if owner_exists:
            owner_fd = self._open_directory(self._owners_fd, owner_binding)
        else:
            self._check_filesystem_reserve(required_inodes=4)
            owner_fd = self._open_or_create_directory(self._owners_fd, owner_binding)
        incoming_fd = objects_fd = sha256_fd = -1
        try:
            entries = set(os.listdir(owner_fd))
            if owner_exists and entries != _OWNER_ENTRIES:
                raise MediaMaterializerCorrupt(
                    "private owner directory layout is incomplete"
                )
            if not owner_exists and entries:
                raise MediaMaterializerCorrupt(
                    "new private owner directory is not empty"
                )
            incoming_fd = (
                self._open_directory(owner_fd, "incoming")
                if owner_exists
                else self._open_or_create_directory(owner_fd, "incoming")
            )
            objects_fd = (
                self._open_directory(owner_fd, "objects")
                if owner_exists
                else self._open_or_create_directory(owner_fd, "objects")
            )
            object_entries = set(os.listdir(objects_fd))
            if owner_exists and object_entries != {"sha256"}:
                raise MediaMaterializerCorrupt(
                    "private object directory layout is incomplete"
                )
            if not owner_exists and object_entries:
                raise MediaMaterializerCorrupt(
                    "new private object directory is not empty"
                )
            sha256_fd = (
                self._open_directory(objects_fd, "sha256")
                if owner_exists
                else self._open_or_create_directory(objects_fd, "sha256")
            )
            self._check_filesystem_reserve()
            return _OwnerLayout(owner_fd, incoming_fd, objects_fd, sha256_fd)
        except BaseException:
            for fd in (sha256_fd, objects_fd, incoming_fd, owner_fd):
                if fd >= 0:
                    os.close(fd)
            raise

    @staticmethod
    def _snapshot(info: os.stat_result) -> tuple[int, ...]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_nlink,
            info.st_size,
            info.st_blocks,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    @staticmethod
    def _allocated_bytes(info: os.stat_result) -> int:
        blocks = getattr(info, "st_blocks", None)
        if type(blocks) is not int or blocks < 0:
            raise MediaMaterializerCorrupt(
                "private-media block allocation evidence is invalid"
            )
        # POSIX st_blocks is always reported in 512-byte units, independent of
        # the filesystem's fragment size.
        return blocks * 512

    def _filesystem_status(self) -> os.statvfs_result:
        try:
            filesystem = os.fstatvfs(self._root_fd)
        except OSError as error:
            raise MediaMaterializerConflict(
                "private-media filesystem capacity is unavailable"
            ) from error
        if (
            type(filesystem.f_bavail) is not int
            or filesystem.f_bavail < 0
            or type(filesystem.f_frsize) is not int
            or filesystem.f_frsize <= 0
            or type(filesystem.f_favail) is not int
            or filesystem.f_favail < 0
        ):
            raise MediaMaterializerCorrupt(
                "private-media filesystem capacity evidence is invalid"
            )
        return filesystem

    @staticmethod
    def _allocation_bound(byte_size: int, fragment_size: int) -> int:
        return ((byte_size + fragment_size - 1) // fragment_size) * fragment_size

    def _check_filesystem_reserve(
        self,
        *,
        required_bytes: int = 0,
        required_inodes: int = 0,
        filesystem: os.statvfs_result | None = None,
    ) -> os.statvfs_result:
        if (
            type(required_bytes) is not int
            or required_bytes < 0
            or type(required_inodes) is not int
            or required_inodes < 0
        ):
            raise MediaMaterializerCorrupt(
                "private-media reserve request is invalid"
            )
        selected = filesystem if filesystem is not None else self._filesystem_status()
        free_bytes = selected.f_bavail * selected.f_frsize
        if (
            free_bytes
            < self._limits.free_space_reserve_bytes + required_bytes
        ):
            raise MediaMaterializerConflict(
                "private-media free-space reserve would be crossed"
            )
        if (
            selected.f_favail
            < self._limits.free_inode_reserve_count + required_inodes
        ):
            raise MediaMaterializerConflict(
                "private-media free-inode reserve would be crossed"
            )
        return selected

    def _check_persistent_quota(
        self,
        owner_usage: _ObjectUsage,
        total_usage: _ObjectUsage,
        addition: _ObjectUsage,
    ) -> None:
        if (
            owner_usage.logical_bytes + addition.logical_bytes
            > self._limits.max_owner_object_bytes
        ):
            raise MediaMaterializerConflict(
                "owner private-media logical-byte quota would be exceeded"
            )
        if (
            total_usage.logical_bytes + addition.logical_bytes
            > self._limits.max_total_object_bytes
        ):
            raise MediaMaterializerConflict(
                "global private-media logical-byte quota would be exceeded"
            )
        if (
            owner_usage.allocated_bytes + addition.allocated_bytes
            > self._limits.max_owner_allocated_bytes
        ):
            raise MediaMaterializerConflict(
                "owner private-media allocated-byte quota would be exceeded"
            )
        if (
            total_usage.allocated_bytes + addition.allocated_bytes
            > self._limits.max_total_allocated_bytes
        ):
            raise MediaMaterializerConflict(
                "global private-media allocated-byte quota would be exceeded"
            )
        if (
            owner_usage.object_count + addition.object_count
            > self._limits.max_owner_object_count
        ):
            raise MediaMaterializerConflict(
                "owner private-media object-count quota would be exceeded"
            )
        if (
            total_usage.object_count + addition.object_count
            > self._limits.max_total_object_count
        ):
            raise MediaMaterializerConflict(
                "global private-media object-count quota would be exceeded"
            )

    def _read_evidence(
        self,
        fd: int,
        *,
        expected_size: int | None = None,
        allowed_links: frozenset[int] = frozenset({1}),
        retain_text: bool = False,
    ) -> tuple[os.stat_result, str, bytes, bytes | None]:
        before = _validate_file(
            fd,
            device=self._device,
            size=expected_size,
            links=allowed_links,
        )
        if before.st_size > self._limits.max_member_bytes:
            raise MediaMaterializerCorrupt("private-media object exceeds its bound")
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        prefix = bytearray()
        text = bytearray() if retain_text else None
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, before.st_size - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > before.st_size:
                raise MediaMaterializerCorrupt(
                    "private-media object changed while reading"
                )
            digest.update(chunk)
            if len(prefix) < SIGNATURE_PROBE_BYTES:
                prefix.extend(chunk[: SIGNATURE_PROBE_BYTES - len(prefix)])
            if text is not None:
                text.extend(chunk)
        after = os.fstat(fd)
        if total != before.st_size or self._snapshot(after) != self._snapshot(before):
            raise MediaMaterializerCorrupt(
                "private-media object changed while reading"
            )
        return (
            before,
            digest.hexdigest(),
            bytes(prefix),
            bytes(text) if text is not None else None,
        )

    def _open_digest_file(self, sha256_fd: int, digest: str) -> tuple[int, int] | None:
        prefix = digest[:2]
        try:
            prefix_fd = self._open_directory(sha256_fd, prefix)
        except MediaMaterializerCorrupt:
            if prefix not in os.listdir(sha256_fd):
                return None
            raise
        try:
            try:
                object_fd = os.open(digest, _read_flags(), dir_fd=prefix_fd)
            except FileNotFoundError:
                os.close(prefix_fd)
                return None
            except OSError as error:
                raise MediaMaterializerCorrupt(
                    "private-media object cannot be opened safely"
                ) from error
            return prefix_fd, object_fd
        except BaseException:
            try:
                os.close(prefix_fd)
            except OSError:
                pass
            raise

    def _verify_digest_object(
        self,
        sha256_fd: int,
        digest: str,
        byte_size: int,
        mime_type: str,
        family: str,
    ) -> None:
        opened = self._open_digest_file(sha256_fd, digest)
        if opened is None:
            raise MediaMaterializerCorrupt("private-media object is unavailable")
        prefix_fd, object_fd = opened
        try:
            _info, observed, probe, full_text = self._read_evidence(
                object_fd,
                expected_size=byte_size,
                retain_text=family == "utf8_text",
            )
            classified = classify_declared_media(
                mime_type,
                probe,
                complete_text=full_text if family == "utf8_text" else None,
            )
            if not hmac.compare_digest(observed, digest) or classified != family:
                raise MediaMaterializerCorrupt(
                    "private-media object evidence does not match"
                )
        finally:
            os.close(object_fd)
            os.close(prefix_fd)

    def _scan_objects(self, sha256_fd: int) -> _ObjectUsage:
        logical_bytes = 0
        allocated_bytes = 0
        object_count = 0
        for prefix in os.listdir(sha256_fd):
            if not _PREFIX_RE.fullmatch(prefix):
                raise MediaMaterializerCorrupt(
                    "private object namespace contains an invalid prefix"
                )
            prefix_fd = self._open_directory(sha256_fd, prefix)
            try:
                for digest in os.listdir(prefix_fd):
                    if not _DIGEST_RE.fullmatch(digest) or digest[:2] != prefix:
                        raise MediaMaterializerCorrupt(
                            "private object namespace contains an invalid digest"
                        )
                    try:
                        fd = os.open(digest, _read_flags(), dir_fd=prefix_fd)
                    except OSError as error:
                        raise MediaMaterializerCorrupt(
                            "private-media object cannot be opened safely"
                        ) from error
                    try:
                        info, observed, _probe, _text = self._read_evidence(fd)
                        if not hmac.compare_digest(observed, digest):
                            raise MediaMaterializerCorrupt(
                                "private-media content address changed"
                            )
                        logical_bytes += info.st_size
                        allocated_bytes += self._allocated_bytes(info)
                        object_count += 1
                    finally:
                        os.close(fd)
            finally:
                os.close(prefix_fd)
        return _ObjectUsage(logical_bytes, allocated_bytes, object_count)

    def _recover_incoming(self, layout: _OwnerLayout) -> None:
        for operation in list(os.listdir(layout.incoming_fd)):
            if not _OPERATION_RE.fullmatch(operation):
                raise MediaMaterializerCorrupt(
                    "private incoming namespace contains an invalid operation"
                )
            operation_fd = self._open_directory(layout.incoming_fd, operation)
            try:
                for filename in list(os.listdir(operation_fd)):
                    matched = _INCOMING_FILE_RE.fullmatch(filename)
                    if matched is None:
                        raise MediaMaterializerCorrupt(
                            "private incoming namespace contains an invalid object"
                        )
                    digest = matched.group(1)
                    try:
                        source_fd = os.open(filename, _read_flags(), dir_fd=operation_fd)
                    except OSError as error:
                        raise MediaMaterializerCorrupt(
                            "private incoming object cannot be opened safely"
                        ) from error
                    try:
                        source = _validate_file(
                            source_fd,
                            device=self._device,
                            links=frozenset({1, 2}),
                        )
                        if source.st_nlink == 2:
                            # A two-link incoming inode can only be the result
                            # of publication after its canonical prefix and
                            # destination existed.  Recovery must not create
                            # missing namespace as a side effect of a scan.
                            prefix_fd = self._open_directory(
                                layout.sha256_fd, digest[:2]
                            )
                            try:
                                try:
                                    destination_fd = os.open(
                                        digest, _read_flags(), dir_fd=prefix_fd
                                    )
                                except OSError as error:
                                    raise MediaMaterializerCorrupt(
                                        "linked incoming object lost its destination"
                                    ) from error
                                try:
                                    destination = _validate_file(
                                        destination_fd,
                                        device=self._device,
                                        links=frozenset({2}),
                                    )
                                    _info, observed, _probe, _text = self._read_evidence(
                                        destination_fd,
                                        allowed_links=frozenset({2}),
                                    )
                                    if (
                                        (source.st_dev, source.st_ino)
                                        != (destination.st_dev, destination.st_ino)
                                        or not hmac.compare_digest(observed, digest)
                                    ):
                                        raise MediaMaterializerCorrupt(
                                            "linked incoming object evidence changed"
                                        )
                                finally:
                                    os.close(destination_fd)
                                os.unlink(filename, dir_fd=operation_fd)
                                os.fsync(operation_fd)
                                os.fsync(prefix_fd)
                            finally:
                                os.close(prefix_fd)
                        else:
                            os.unlink(filename, dir_fd=operation_fd)
                            os.fsync(operation_fd)
                    finally:
                        os.close(source_fd)
                os.fsync(operation_fd)
            finally:
                os.close(operation_fd)
            try:
                os.rmdir(operation, dir_fd=layout.incoming_fd)
                os.fsync(layout.incoming_fd)
            except OSError as error:
                raise MediaMaterializerCorrupt(
                    "private incoming operation could not be recovered"
                ) from error

    def _scan_usage_locked(
        self,
    ) -> tuple[_ObjectUsage, dict[str, _ObjectUsage]]:
        total = _ObjectUsage()
        owners: dict[str, _ObjectUsage] = {}
        for owner_binding in os.listdir(self._owners_fd):
            if not _OWNER_BINDING_RE.fullmatch(owner_binding):
                raise MediaMaterializerCorrupt(
                    "private owner namespace contains an invalid binding"
                )
            layout = self._owner_layout(owner_binding, create=False)
            try:
                self._recover_incoming(layout)
                owner_usage = self._scan_objects(layout.sha256_fd)
                owners[owner_binding] = owner_usage
                total = total.plus(owner_usage)
            finally:
                layout.close()
        return total, owners

    def _revalidate_stage(
        self, owner_user_id: str, staged: StagedImport
    ) -> ClipModeImportBundle:
        if not isinstance(staged, StagedImport):
            raise TypeError("staged must be StagedImport")
        owner_binding = self.owner_binding(owner_user_id)
        if (
            staged.owner_binding != owner_binding
            or type(staged.source) is not bytes
            or staged.content_type not in {"application/json", "application/zip"}
            or staged.expires_at <= int(self._clock())
        ):
            raise MediaMaterializerStageInvalid(
                "provider import stage is unavailable"
            )
        try:
            bundle = import_clipmode_export(
                staged.source, limits=self._limits.import_limits
            )
        except ClipModeImportError as error:
            raise MediaMaterializerStageInvalid(
                "provider import stage no longer validates"
            ) from error
        expected_kind = (
            "zip" if staged.content_type == "application/zip" else "json"
        )
        provenance = bundle.provenance
        observed = (
            provenance.source_kind,
            provenance.source_sha256,
            provenance.manifest_sha256,
            provenance.semantic_sha256,
            provenance.schema_name,
            provenance.schema_version,
            provenance.provider_scope,
            provenance.export_id,
            provenance.exported_at,
            len(staged.source),
        )
        expected = (
            expected_kind,
            staged.source_sha256,
            staged.manifest_sha256,
            staged.semantic_sha256,
            staged.source_schema,
            staged.source_schema_version,
            staged.provider_scope,
            staged.export_id,
            staged.exported_at,
            staged.byte_size,
        )
        if (
            observed != expected
            or bundle.to_dict() != staged.bundle.to_dict()
        ):
            raise MediaMaterializerStageInvalid(
                "provider import stage evidence changed"
            )
        return bundle

    def _declarations(
        self, bundle: ClipModeImportBundle
    ) -> tuple[dict[str, _Declaration], dict[str, list[ImportedMediaRecord]]]:
        if bundle.provenance.source_kind != "zip":
            if any(media.availability == "bundled" for media in bundle.media):
                raise MediaMaterializerCorrupt(
                    "bundled media requires a validated ZIP stage"
                )
            return {}, {}
        by_digest: dict[str, _Declaration] = {}
        records: dict[str, list[ImportedMediaRecord]] = {}
        members: dict[str, tuple[int, str, str]] = {}
        total_members = 0
        for media in bundle.media:
            if media.availability != "bundled":
                continue
            if (
                media.archive_member is None
                or media.byte_size is None
                or media.content_sha256 is None
                or not _DIGEST_RE.fullmatch(media.content_sha256)
                or media.byte_size <= 0
                or media.byte_size > self._limits.max_member_bytes
            ):
                raise MediaMaterializerCorrupt(
                    "bundled media declaration is incomplete"
                )
            family = signature_family_for_mime(media.mime_type)
            if family is None:
                raise MediaMaterializerCorrupt(
                    "bundled media MIME is unsupported"
                )
            member_evidence = (
                media.byte_size,
                media.content_sha256,
                media.mime_type,
            )
            prior_member = members.get(media.archive_member)
            if prior_member is not None and prior_member != member_evidence:
                raise MediaMaterializerCorrupt(
                    "bundled archive member evidence conflicts"
                )
            if prior_member is None:
                members[media.archive_member] = member_evidence
                total_members += media.byte_size
            declaration = _Declaration(
                media.archive_member,
                media.content_sha256,
                media.byte_size,
                media.mime_type,
                family,
            )
            prior_digest = by_digest.get(declaration.digest)
            if prior_digest is not None and (
                prior_digest.byte_size != declaration.byte_size
                or prior_digest.family != declaration.family
            ):
                raise MediaMaterializerCorrupt(
                    "one content digest has conflicting media evidence"
                )
            if prior_digest is None or declaration.member < prior_digest.member:
                by_digest[declaration.digest] = declaration
            records.setdefault(declaration.digest, []).append(media)
        if total_members > self._limits.max_total_publish_bytes:
            raise MediaMaterializerConflict(
                "bundled media exceeds the aggregate publish limit"
            )
        if len(by_digest) > self._limits.max_objects_per_import:
            raise MediaMaterializerConflict(
                "bundled media exceeds the object-count limit"
            )
        return by_digest, records

    def _stream_member(
        self,
        archive: zipfile.ZipFile,
        declaration: _Declaration,
        operation_fd: int,
        *,
        future_allocated_bound: int,
        future_inode_count: int,
    ) -> int:
        filename = f"blob_{declaration.digest}"
        try:
            info = archive.getinfo(declaration.member)
        except KeyError as error:
            raise MediaMaterializerCorrupt(
                "declared bundled media is unavailable"
            ) from error
        if info.filename != declaration.member or info.file_size != declaration.byte_size:
            raise MediaMaterializerCorrupt(
                "declared bundled media evidence changed"
            )
        filesystem = self._filesystem_status()
        object_bound = self._allocation_bound(
            declaration.byte_size, filesystem.f_frsize
        )
        self._check_filesystem_reserve(
            required_bytes=object_bound + future_allocated_bound,
            required_inodes=1 + future_inode_count,
            filesystem=filesystem,
        )
        try:
            fd = os.open(filename, _create_flags(), _FILE_MODE, dir_fd=operation_fd)
        except OSError as error:
            raise MediaMaterializerConflict(
                "incoming media object could not be reserved"
            ) from error
        digest = hashlib.sha256()
        prefix = bytearray()
        text = bytearray() if declaration.family == "utf8_text" else None
        total = 0
        try:
            self._check_filesystem_reserve(
                required_bytes=object_bound + future_allocated_bound,
                required_inodes=future_inode_count,
            )
            try:
                with archive.open(info, "r") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if (
                            total > declaration.byte_size
                            or total > self._limits.max_member_bytes
                        ):
                            raise MediaMaterializerCorrupt(
                                "bundled media exceeded its declared bound"
                            )
                        digest.update(chunk)
                        if len(prefix) < SIGNATURE_PROBE_BYTES:
                            prefix.extend(
                                chunk[: SIGNATURE_PROBE_BYTES - len(prefix)]
                            )
                        if text is not None:
                            text.extend(chunk)
                        offset = 0
                        while offset < len(chunk):
                            written = os.write(fd, chunk[offset:])
                            if written <= 0:
                                raise OSError(errno.EIO, "short write")
                            offset += written
                        current_allocated = self._allocated_bytes(os.fstat(fd))
                        self._check_filesystem_reserve(
                            required_bytes=(
                                max(0, object_bound - current_allocated)
                                + future_allocated_bound
                            ),
                            required_inodes=future_inode_count,
                        )
            except MediaMaterializerError:
                raise
            except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as error:
                raise MediaMaterializerCorrupt(
                    "bundled media could not be read safely"
                ) from error
            classified = classify_declared_media(
                declaration.mime_type,
                bytes(prefix),
                complete_text=bytes(text) if text is not None else None,
            )
            if (
                total != declaration.byte_size
                or not hmac.compare_digest(digest.hexdigest(), declaration.digest)
                or classified != declaration.family
            ):
                raise MediaMaterializerCorrupt(
                    "bundled media integrity does not match its declaration"
                )
            os.fsync(fd)
            persisted = _validate_file(
                fd,
                device=self._device,
                size=declaration.byte_size,
            )
            persisted_allocated = self._allocated_bytes(persisted)
            self._check_filesystem_reserve(
                required_bytes=future_allocated_bound,
                required_inodes=future_inode_count,
            )
        except BaseException:
            os.close(fd)
            try:
                os.unlink(filename, dir_fd=operation_fd)
                os.fsync(operation_fd)
            except OSError:
                pass
            raise
        else:
            os.close(fd)
        try:
            verify_fd = os.open(filename, _read_flags(), dir_fd=operation_fd)
            verified, observed, probe, full_text = self._read_evidence(
                verify_fd,
                expected_size=declaration.byte_size,
                retain_text=declaration.family == "utf8_text",
            )
            classified = classify_declared_media(
                declaration.mime_type,
                probe,
                complete_text=full_text if declaration.family == "utf8_text" else None,
            )
            if (
                not hmac.compare_digest(observed, declaration.digest)
                or classified != declaration.family
            ):
                raise MediaMaterializerCorrupt(
                    "incoming media evidence changed after persistence"
                )
            if self._allocated_bytes(verified) != persisted_allocated:
                raise MediaMaterializerCorrupt(
                    "incoming media allocation changed after persistence"
                )
        finally:
            try:
                os.close(verify_fd)
            except (OSError, UnboundLocalError):
                pass
        return persisted_allocated

    def _publish_incoming(
        self,
        layout: _OwnerLayout,
        operation_fd: int,
        declaration: _Declaration,
    ) -> bool:
        filename = f"blob_{declaration.digest}"
        prefix_missing = declaration.digest[:2] not in os.listdir(layout.sha256_fd)
        self._check_filesystem_reserve(
            required_inodes=1 if prefix_missing else 0
        )
        prefix_fd = self._open_or_create_directory(
            layout.sha256_fd, declaration.digest[:2]
        )
        linked = False
        try:
            self._check_filesystem_reserve()
            try:
                os.link(
                    filename,
                    declaration.digest,
                    src_dir_fd=operation_fd,
                    dst_dir_fd=prefix_fd,
                    follow_symlinks=False,
                )
                linked = True
                try:
                    self._check_filesystem_reserve()
                except MediaMaterializerError:
                    # No commit has been acknowledged yet.  Restore the
                    # single incoming link if directory-entry allocation
                    # crossed either filesystem reserve during link().
                    try:
                        os.unlink(declaration.digest, dir_fd=prefix_fd)
                        os.fsync(prefix_fd)
                    except OSError as error:
                        raise MediaMaterializerCorrupt(
                            "private-media reserve rollback failed"
                        ) from error
                    linked = False
                    raise
                os.fsync(prefix_fd)
            except FileExistsError:
                linked = False
            except OSError as error:
                raise MediaMaterializerConflict(
                    "private-media object could not be published atomically"
                ) from error
            if not linked:
                self._verify_digest_object(
                    layout.sha256_fd,
                    declaration.digest,
                    declaration.byte_size,
                    declaration.mime_type,
                    declaration.family,
                )
            os.unlink(filename, dir_fd=operation_fd)
            os.fsync(operation_fd)
            os.fsync(prefix_fd)
            self._verify_digest_object(
                layout.sha256_fd,
                declaration.digest,
                declaration.byte_size,
                declaration.mime_type,
                declaration.family,
            )
            return linked
        except OSError as error:
            raise MediaMaterializerCorrupt(
                "private-media publication durability failed"
            ) from error
        finally:
            os.close(prefix_fd)

    def _finish_operation(
        self, layout: _OwnerLayout, operation: str, operation_fd: int
    ) -> None:
        sync_error: OSError | None = None
        close_error: OSError | None = None
        try:
            os.fsync(operation_fd)
        except OSError as error:
            sync_error = error
        try:
            # One owner, one close attempt.  In particular, never retry close
            # after a numeric descriptor may have been recycled by the OS.
            os.close(operation_fd)
        except OSError as error:
            close_error = error
        if sync_error is not None or close_error is not None:
            raise MediaMaterializerCorrupt(
                "private incoming operation could not be synchronized"
            ) from (sync_error or close_error)
        try:
            os.rmdir(operation, dir_fd=layout.incoming_fd)
            os.fsync(layout.incoming_fd)
        except OSError as error:
            raise MediaMaterializerCorrupt(
                "private incoming operation could not be finalized"
            ) from error

    def publish_bundled_originals(
        self,
        owner_user_id: str,
        staged: StagedImport,
    ) -> MaterializedImport:
        """Publish exact ZIP-bundled originals, never previews or DB state."""

        owner_binding = self.owner_binding(owner_user_id)
        with self._locked():
            bundle = self._revalidate_stage(owner_user_id, staged)
            declarations, records = self._declarations(bundle)
            if not declarations:
                return MaterializedImport((), 0, 0, 0)
            if bundle.provenance.source_kind != "zip":
                raise MediaMaterializerStageInvalid(
                    "bundled media requires a validated ZIP stage"
                )

            total_usage, owner_usages = self._scan_usage_locked()
            owner_exists = owner_binding in os.listdir(self._owners_fd)
            self._check_filesystem_reserve(
                required_inodes=0 if owner_exists else 4
            )
            layout = self._owner_layout(owner_binding, create=True)
            self._check_filesystem_reserve()
            operation = f"op_{secrets.token_hex(16)}"
            operation_fd = -1
            try:
                existing: set[str] = set()
                for digest, declaration in sorted(declarations.items()):
                    opened = self._open_digest_file(layout.sha256_fd, digest)
                    if opened is None:
                        continue
                    prefix_fd, object_fd = opened
                    os.close(object_fd)
                    os.close(prefix_fd)
                    self._verify_digest_object(
                        layout.sha256_fd,
                        digest,
                        declaration.byte_size,
                        declaration.mime_type,
                        declaration.family,
                    )
                    existing.add(digest)

                new = [
                    declaration
                    for digest, declaration in sorted(declarations.items())
                    if digest not in existing
                ]
                owner_usage = owner_usages.get(owner_binding, _ObjectUsage())
                planned_usage = _ObjectUsage(
                    logical_bytes=sum(item.byte_size for item in new),
                    object_count=len(new),
                )
                self._check_persistent_quota(
                    owner_usage, total_usage, planned_usage
                )

                filesystem = self._filesystem_status()
                allocation_bounds = [
                    self._allocation_bound(item.byte_size, filesystem.f_frsize)
                    for item in new
                ]
                missing_prefixes = {
                    item.digest[:2]
                    for item in new
                    if item.digest[:2] not in os.listdir(layout.sha256_fd)
                }
                self._check_filesystem_reserve(
                    required_bytes=sum(allocation_bounds),
                    required_inodes=(
                        (1 if new else 0) + len(new) + len(missing_prefixes)
                    ),
                    filesystem=filesystem,
                )

                created_digests: set[str] = set()
                if new:
                    try:
                        os.mkdir(operation, _DIRECTORY_MODE, dir_fd=layout.incoming_fd)
                        os.fsync(layout.incoming_fd)
                    except OSError as error:
                        raise MediaMaterializerConflict(
                            "incoming media operation could not be reserved"
                        ) from error
                    operation_fd = self._open_directory(layout.incoming_fd, operation)
                    try:
                        with zipfile.ZipFile(io.BytesIO(staged.source), "r") as archive:
                            staged_allocated_bytes = 0
                            staged_logical_bytes = 0
                            remaining_allocated_bound = sum(allocation_bounds)
                            for index, declaration in enumerate(new):
                                remaining_allocated_bound -= allocation_bounds[index]
                                staged_allocated_bytes += self._stream_member(
                                    archive,
                                    declaration,
                                    operation_fd,
                                    future_allocated_bound=remaining_allocated_bound,
                                    future_inode_count=(
                                        len(new) - index - 1 + len(missing_prefixes)
                                    ),
                                )
                                staged_logical_bytes += declaration.byte_size
                                self._check_persistent_quota(
                                    owner_usage,
                                    total_usage,
                                    _ObjectUsage(
                                        logical_bytes=staged_logical_bytes,
                                        allocated_bytes=staged_allocated_bytes,
                                        object_count=index + 1,
                                    ),
                                )

                            # This is the final stage-lease check.  Once the
                            # first immutable link begins, commit must finish
                            # (or fail for storage integrity), regardless of a
                            # later wall-clock tick.
                            if staged.expires_at <= int(self._clock()):
                                raise MediaMaterializerStageInvalid(
                                    "provider import stage expired before publication"
                                )
                            for declaration in new:
                                if self._publish_incoming(
                                    layout, operation_fd, declaration
                                ):
                                    created_digests.add(declaration.digest)
                    except MediaMaterializerError:
                        raise
                    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as error:
                        raise MediaMaterializerCorrupt(
                            "validated ZIP source could not be materialized"
                        ) from error
                    # Transfer descriptor ownership before the helper runs.
                    # If finalization faults after close and the numeric FD is
                    # immediately reused, the outer recovery path must never
                    # close that unrelated descriptor a second time.
                    finishing_fd = operation_fd
                    operation_fd = -1
                    self._finish_operation(layout, operation, finishing_fd)

                originals: list[MaterializedOriginal] = []
                for digest, media_records in sorted(records.items()):
                    declaration = declarations[digest]
                    self._verify_digest_object(
                        layout.sha256_fd,
                        digest,
                        declaration.byte_size,
                        declaration.mime_type,
                        declaration.family,
                    )
                    for media in sorted(
                        media_records, key=lambda item: item.record_id
                    ):
                        originals.append(
                            MaterializedOriginal(
                                media.record_id,
                                digest,
                                declaration.byte_size,
                                media.mime_type,
                                declaration.family,
                                digest in created_digests,
                            )
                        )
                # A reuse-only operation never crosses the immutable-link
                # commit boundary above, so its lease must remain valid until
                # the final evidence check.  Once a new-object commit starts,
                # however, expiry cannot safely turn a durable commit into an
                # application-level failure.
                if not new and staged.expires_at <= int(self._clock()):
                    raise MediaMaterializerStageInvalid(
                        "provider import stage expired before completion"
                    )
                return MaterializedImport(
                    tuple(originals),
                    len(created_digests),
                    sum(declarations[digest].byte_size for digest in created_digests),
                    len(declarations) - len(created_digests),
                )
            except BaseException:
                if operation_fd >= 0:
                    try:
                        os.close(operation_fd)
                    except OSError:
                        pass
                    operation_fd = -1
                # Recovery is constrained to verified incoming directories and
                # never removes a destination object.  Published files remain
                # harmless unreachable orphans if a later layer fails.
                self._recover_incoming(layout)
                raise
            finally:
                layout.close()

    def publish_profile_renditions(
        self,
        owner_user_id: str,
        staged: StagedImport,
        renditions: Sequence[OwnerProfileRendition],
    ) -> MaterializedImport:
        if any(not isinstance(item, OwnerProfileRendition) for item in renditions):
            raise TypeError("renditions contains an invalid profile item")
        return self._publish_generated_renditions(owner_user_id, staged, renditions)

    def publish_asset_previews(
        self,
        owner_user_id: str,
        staged: StagedImport,
        renditions: Sequence[OwnerAssetPreviewRendition],
    ) -> MaterializedImport:
        if any(not isinstance(item, OwnerAssetPreviewRendition) for item in renditions):
            raise TypeError("renditions contains an invalid asset-preview item")
        return self._publish_generated_renditions(owner_user_id, staged, renditions)

    def publish_import_renditions(
        self,
        owner_user_id: str,
        staged: StagedImport,
        profile_renditions: Sequence[OwnerProfileRendition],
        asset_previews: Sequence[OwnerAssetPreviewRendition],
    ) -> MaterializedImport:
        """Publish every generated import rendition as one deduplicated batch."""

        return self._publish_generated_renditions(
            owner_user_id, staged, (*profile_renditions, *asset_previews)
        )

    def _publish_generated_renditions(
        self,
        owner_user_id: str,
        staged: StagedImport,
        renditions: Sequence[OwnerProfileRendition | OwnerAssetPreviewRendition],
    ) -> MaterializedImport:
        """Publish exact deterministic PNG renditions under the active owner."""

        owner_binding = self.owner_binding(owner_user_id)
        if isinstance(renditions, (str, bytes, bytearray)) or not isinstance(
            renditions, Sequence
        ):
            raise TypeError("renditions must be a sequence")
        normalized: list[OwnerProfileRendition | OwnerAssetPreviewRendition] = []
        by_digest: dict[
            str, OwnerProfileRendition | OwnerAssetPreviewRendition
        ] = {}
        seen_keys: set[str] = set()
        for rendition in renditions:
            if not isinstance(
                rendition, (OwnerProfileRendition, OwnerAssetPreviewRendition)
            ):
                raise TypeError("renditions contains an invalid item")
            item_key = (
                f"profile:{rendition.source_character_key}"
                if isinstance(rendition, OwnerProfileRendition)
                else f"asset:{rendition.source_media_record_id}"
            )
            if item_key in seen_keys:
                raise MediaMaterializerConflict("generated rendition is duplicated")
            seen_keys.add(item_key)
            if (
                rendition.mime_type != "image/png"
                or rendition.signature_family != "png"
                or rendition.byte_size != len(rendition.content)
                or rendition.byte_size <= 0
                or rendition.byte_size > min(self._limits.max_member_bytes, 4 * 1024 * 1024)
                or not _DIGEST_RE.fullmatch(rendition.rendition_sha256)
                or not hmac.compare_digest(
                    hashlib.sha256(rendition.content).hexdigest(),
                    rendition.rendition_sha256,
                )
                or classify_declared_media(
                    "image/png", rendition.content[:SIGNATURE_PROBE_BYTES]
                )
                != "png"
            ):
                raise MediaMaterializerCorrupt("generated rendition evidence is invalid")
            previous = by_digest.get(rendition.rendition_sha256)
            if previous is not None and previous.content != rendition.content:
                raise MediaMaterializerCorrupt("generated rendition digest conflicts")
            by_digest[rendition.rendition_sha256] = rendition
            normalized.append(rendition)
        total_bytes = sum(item.byte_size for item in by_digest.values())
        if total_bytes > self._limits.max_total_publish_bytes:
            raise MediaMaterializerConflict("profile renditions exceed publish limit")
        if len(by_digest) > self._limits.max_objects_per_import:
            raise MediaMaterializerConflict("profile renditions exceed object limit")
        if not normalized:
            return MaterializedImport((), 0, 0, 0, (), ())

        with self._locked():
            self._revalidate_stage(owner_user_id, staged)
            total_usage, owner_usages = self._scan_usage_locked()
            owner_exists = owner_binding in os.listdir(self._owners_fd)
            self._check_filesystem_reserve(required_inodes=0 if owner_exists else 4)
            layout = self._owner_layout(owner_binding, create=True)
            operation = f"op_{secrets.token_hex(16)}"
            operation_fd = -1
            try:
                existing: set[str] = set()
                for digest, rendition in sorted(by_digest.items()):
                    opened = self._open_digest_file(layout.sha256_fd, digest)
                    if opened is None:
                        continue
                    prefix_fd, object_fd = opened
                    os.close(object_fd)
                    os.close(prefix_fd)
                    self._verify_digest_object(
                        layout.sha256_fd,
                        digest,
                        rendition.byte_size,
                        rendition.mime_type,
                        rendition.signature_family,
                    )
                    existing.add(digest)
                new = [
                    rendition
                    for digest, rendition in sorted(by_digest.items())
                    if digest not in existing
                ]
                owner_usage = owner_usages.get(owner_binding, _ObjectUsage())
                planned = _ObjectUsage(
                    logical_bytes=sum(item.byte_size for item in new),
                    allocated_bytes=sum(
                        self._allocation_bound(item.byte_size, self._filesystem_status().f_frsize)
                        for item in new
                    ),
                    object_count=len(new),
                )
                self._check_persistent_quota(owner_usage, total_usage, planned)
                filesystem = self._filesystem_status()
                allocations = [
                    self._allocation_bound(item.byte_size, filesystem.f_frsize)
                    for item in new
                ]
                missing_prefixes = {
                    item.rendition_sha256[:2]
                    for item in new
                    if item.rendition_sha256[:2] not in os.listdir(layout.sha256_fd)
                }
                self._check_filesystem_reserve(
                    required_bytes=sum(allocations),
                    required_inodes=(
                        (1 if new else 0) + len(new) + len(missing_prefixes)
                    ),
                    filesystem=filesystem,
                )
                created: set[str] = set()
                if new:
                    os.mkdir(operation, _DIRECTORY_MODE, dir_fd=layout.incoming_fd)
                    os.fsync(layout.incoming_fd)
                    operation_fd = self._open_directory(layout.incoming_fd, operation)
                    for index, rendition in enumerate(new):
                        filename = f"blob_{rendition.rendition_sha256}"
                        fd = os.open(
                            filename, _create_flags(), _FILE_MODE, dir_fd=operation_fd
                        )
                        try:
                            offset = 0
                            while offset < rendition.byte_size:
                                written = os.write(fd, rendition.content[offset:])
                                if written <= 0:
                                    raise OSError(errno.EIO, "short write")
                                offset += written
                            os.fsync(fd)
                            _validate_file(
                                fd,
                                device=self._device,
                                size=rendition.byte_size,
                            )
                        finally:
                            os.close(fd)
                        self._check_filesystem_reserve(
                            required_bytes=sum(allocations[index + 1 :]),
                            required_inodes=len(new) - index - 1,
                        )
                    for rendition in new:
                        verify_fd = os.open(
                            f"blob_{rendition.rendition_sha256}",
                            _read_flags(),
                            dir_fd=operation_fd,
                        )
                        try:
                            _info, observed, probe, _text = self._read_evidence(
                                verify_fd, expected_size=rendition.byte_size
                            )
                            if (
                                not hmac.compare_digest(
                                    observed, rendition.rendition_sha256
                                )
                                or classify_declared_media(
                                    rendition.mime_type, probe
                                )
                                != rendition.signature_family
                            ):
                                raise MediaMaterializerCorrupt(
                                    "generated rendition changed after persistence"
                                )
                        finally:
                            os.close(verify_fd)
                    if staged.expires_at <= int(self._clock()):
                        raise MediaMaterializerStageInvalid(
                            "provider import stage expired before publication"
                        )
                    for rendition in new:
                        declaration = _Declaration(
                            "generated-profile-rendition",
                            rendition.rendition_sha256,
                            rendition.byte_size,
                            rendition.mime_type,
                            rendition.signature_family,
                        )
                        if self._publish_incoming(layout, operation_fd, declaration):
                            created.add(rendition.rendition_sha256)
                    finishing_fd = operation_fd
                    operation_fd = -1
                    self._finish_operation(layout, operation, finishing_fd)
                materialized_profiles = tuple(
                    MaterializedProfileRendition(
                        source_character_key=item.source_character_key,
                        canonical_character_id=item.canonical_character_id,
                        projection_id=item.projection_id,
                        version_id=item.version_id,
                        source_sha256=item.source_sha256,
                        rendition_sha256=item.rendition_sha256,
                        byte_size=item.byte_size,
                        declared_mime=item.mime_type,
                        signature_family=item.signature_family,
                        width=item.width,
                        height=item.height,
                        created=item.rendition_sha256 in created,
                    )
                    for item in sorted(
                        (
                            value
                            for value in normalized
                            if isinstance(value, OwnerProfileRendition)
                        ),
                        key=lambda value: value.source_character_key,
                    )
                )
                materialized_previews = tuple(
                    MaterializedAssetPreviewRendition(
                        source_media_record_id=item.source_media_record_id,
                        projection_id=item.projection_id,
                        version_id=item.version_id,
                        source_sha256=item.source_sha256,
                        rendition_sha256=item.rendition_sha256,
                        byte_size=item.byte_size,
                        declared_mime=item.mime_type,
                        signature_family=item.signature_family,
                        width=item.width,
                        height=item.height,
                        selection_source=item.selection_source,
                        created=item.rendition_sha256 in created,
                    )
                    for item in sorted(
                        (
                            value
                            for value in normalized
                            if isinstance(value, OwnerAssetPreviewRendition)
                        ),
                        key=lambda value: value.projection_id,
                    )
                )
                return MaterializedImport(
                    (),
                    len(created),
                    sum(by_digest[digest].byte_size for digest in created),
                    len(by_digest) - len(created),
                    materialized_profiles,
                    materialized_previews,
                )
            except BaseException:
                if operation_fd >= 0:
                    try:
                        os.close(operation_fd)
                    except OSError:
                        pass
                self._recover_incoming(layout)
                raise
            finally:
                layout.close()

    def verify_owned_object(
        self,
        owner_user_id: str,
        sha256: str,
        byte_size: int,
        signature_family: str,
    ) -> None:
        """Revalidate one exact owner object without returning a path or FD."""

        owner_binding = self.owner_binding(owner_user_id)
        if not isinstance(sha256, str) or not _DIGEST_RE.fullmatch(sha256):
            raise ValueError("sha256 is invalid")
        if type(byte_size) is not int or byte_size <= 0:
            raise ValueError("byte_size is invalid")
        mime_type = _FAMILY_VERIFICATION_MIME.get(signature_family)
        if mime_type is None:
            raise ValueError("signature_family is invalid")
        with self._locked():
            self._scan_usage_locked()
            layout = self._owner_layout(owner_binding, create=False)
            try:
                self._verify_digest_object(
                    layout.sha256_fd,
                    sha256,
                    byte_size,
                    mime_type,
                    signature_family,
                )
            finally:
                layout.close()

    def read_owned_object(
        self,
        owner_user_id: str,
        sha256: str,
        byte_size: int,
        signature_family: str,
        *,
        max_bytes: int,
    ) -> bytes:
        """Return one exact bounded owner object after full integrity verification.

        Callers must first resolve an owner-bound database role such as an
        exact preview rendition.  This storage primitive accepts no path and
        performs no role inference; a digest belonging to another owner is
        indistinguishable from a missing object.
        """

        owner_binding = self.owner_binding(owner_user_id)
        if not isinstance(sha256, str) or not _DIGEST_RE.fullmatch(sha256):
            raise ValueError("sha256 is invalid")
        if type(byte_size) is not int or byte_size <= 0:
            raise ValueError("byte_size is invalid")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes is invalid")
        if byte_size > max_bytes:
            raise MediaMaterializerConflict("private-media object exceeds read bound")
        mime_type = _FAMILY_VERIFICATION_MIME.get(signature_family)
        if mime_type is None:
            raise ValueError("signature_family is invalid")
        with self._locked():
            self._scan_usage_locked()
            layout = self._owner_layout(owner_binding, create=False)
            prefix_fd = -1
            object_fd = -1
            try:
                opened = self._open_digest_file(layout.sha256_fd, sha256)
                if opened is None:
                    raise MediaMaterializerCorrupt("private-media object is unavailable")
                prefix_fd, object_fd = opened
                _info, observed, probe, full_text = self._read_evidence(
                    object_fd,
                    expected_size=byte_size,
                    retain_text=signature_family == "utf8_text",
                )
                classified = classify_declared_media(
                    mime_type,
                    probe,
                    complete_text=(
                        full_text if signature_family == "utf8_text" else None
                    ),
                )
                if not hmac.compare_digest(observed, sha256) or classified != signature_family:
                    raise MediaMaterializerCorrupt(
                        "private-media object evidence does not match"
                    )
                os.lseek(object_fd, 0, os.SEEK_SET)
                chunks: list[bytes] = []
                read_digest = hashlib.sha256()
                remaining = byte_size
                while remaining:
                    chunk = os.read(object_fd, min(1024 * 1024, remaining))
                    if not chunk:
                        raise MediaMaterializerCorrupt(
                            "private-media object changed while reading"
                        )
                    chunks.append(chunk)
                    read_digest.update(chunk)
                    remaining -= len(chunk)
                if os.read(object_fd, 1) or not hmac.compare_digest(
                    read_digest.hexdigest(), sha256
                ):
                    raise MediaMaterializerCorrupt(
                        "private-media object changed while reading"
                    )
                return b"".join(chunks)
            finally:
                if object_fd >= 0:
                    os.close(object_fd)
                if prefix_fd >= 0:
                    os.close(prefix_fd)
                layout.close()


__all__ = [
    "MaterializedImport",
    "MaterializedOriginal",
    "MaterializedAssetPreviewRendition",
    "MaterializedProfileRendition",
    "MediaMaterializerConfigurationError",
    "MediaMaterializerConflict",
    "MediaMaterializerCorrupt",
    "MediaMaterializerError",
    "MediaMaterializerLimits",
    "MediaMaterializerStageInvalid",
    "PrivateProviderMediaStore",
]
