"""Dormant owner-private CAS for generated Novel Scene Art PNG outputs.

This module is deliberately not imported by ``webui.server`` and performs no
HTTP, database, ComfyUI, GPU, or network work.  A future platform controller
may adopt already-sanitized renderer bytes here before exposing an opaque
owner-bound result handle to the Oracle worker.

The existing provider ``PrivateProviderMediaStore`` is intentionally not
wrapped: that store's public input is a provider ZIP/import declaration and it
has materially different MIME, quota, rendition, and ownership semantics.
Reusing it would require manufacturing a fake provider import and would widen
the Novel Scene Art service account's authority.  This smaller store reuses
the same security pattern instead: HMAC owner namespaces, held directory file
descriptors, no-follow opens, immutable content addressing, exact modes, and
atomic publication.

The filesystem root is provisioned outside this module.  In production it is
expected to be a dedicated ``/var/lib/...`` tree owned by the platform service
UID.  Tests use a pre-provisioned temporary tree.  Callers never supply a path
or filename.  ``storage_incarnation`` is an internal CAS anti-ABA value; it is
deliberately distinct from the platform job store's integer ``output_version``.
Coordinator reads must bind that value through ``read_exact_incarnation``;
the older ``read_exact`` API remains only as a low-level compatibility seam.
"""

from __future__ import annotations

import errno
if '.' in (__package__ or ''):
    from .. import file_lock as fcntl
else:  # top-level webui package layout
    import file_lock as fcntl
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import stat
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator, Sequence

from .errors import SceneArtError
from .png import sanitize_png
from .protocol import OUTPUT_HEIGHT, OUTPUT_WIDTH


DEFAULT_PRIVATE_OUTPUT_ROOT = Path(
    "/var/lib/web-comfyui-novel-scene-art/private/outputs"
)
DEFAULT_MANAGED_ANCESTOR = DEFAULT_PRIVATE_OUTPUT_ROOT.parent
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
MIME_TYPE = "image/png"

_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNER_BINDING_RE = _DIGEST_RE
_PREFIX_RE = re.compile(r"^[0-9a-f]{2}$")
_INCARNATION_RE = re.compile(r"^inc_[0-9a-f]{32}$")
_STORE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_TEMP_RE = re.compile(r"^\.tmp-[0-9a-f]{32}$")
_DELETE_RE = re.compile(r"^\.delete-[0-9a-f]{32}$")
_IDENTITY_TEMP_RE = re.compile(r"^\.identity\.tmp-[0-9a-f]{32}$")
_ROOT_ENTRIES = frozenset({".identity", ".store.lock", "owners"})
_OBJECT_ENTRIES = frozenset({"image.png", "manifest.json"})
_IDENTITY_BYTES = 129
_MAX_MANIFEST_BYTES = 1024
_MAX_STARTUP_SCAN_ENTRIES = 100_000
_MAX_ROOT_SCAN_ENTRIES = 16
_MANIFEST_SCHEMA = "castle.novel-scene-art-private-output.v1"
_IDENTITY_DOMAIN = b"web-comfyui-novel-scene-art-output-identity-v2\0"
_HANDLE_DOMAIN = b"web-comfyui-novel-scene-art-output-handle-v1\0"
_PREPARED_DOMAIN = b"web-comfyui-novel-scene-art-prepared-output-v1\0"
_OWNER_DOMAIN = b"web-comfyui-novel-scene-art-output-owner-v1\0"
_QUARANTINE_DOMAIN = b"web-comfyui-novel-scene-art-output-quarantine-v1\0"
_SESSION_ISSUER = object()


class GeneratedOutputStoreError(RuntimeError):
    """Base class for private generated-output failures."""


class GeneratedOutputConfigurationError(GeneratedOutputStoreError):
    """The configured root, UID, key, or Comfy view boundary is unsafe."""


class GeneratedOutputInvalid(GeneratedOutputStoreError):
    """Candidate bytes are not the exact configured PNG profile."""


class GeneratedOutputNotFound(GeneratedOutputStoreError):
    """An exact owner/digest/size tuple is unavailable."""


class GeneratedOutputCorrupt(GeneratedOutputStoreError):
    """Private output storage failed an integrity check."""


class GeneratedOutputInvalidHandle(GeneratedOutputStoreError):
    """A delete request did not carry a handle issued by this store."""


class GeneratedOutputStaleHandle(GeneratedOutputStoreError):
    """A valid handle names an older incarnation of the same CAS object."""


@dataclass(frozen=True, slots=True)
class PngProfile:
    """Trusted startup configuration, never request-controlled."""

    width: int = OUTPUT_WIDTH
    height: int = OUTPUT_HEIGHT
    maximum_bytes: int = MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or self.width <= 0
            or self.height <= 0
            or self.width > 4096
            or self.height > 4096
            or self.width * self.height > 16_777_216
        ):
            raise GeneratedOutputConfigurationError("PNG profile dimensions are unsafe")
        if (
            type(self.maximum_bytes) is not int
            or self.maximum_bytes <= 0
            or self.maximum_bytes > MAX_OUTPUT_BYTES
        ):
            raise GeneratedOutputConfigurationError("PNG profile byte limit is unsafe")


@dataclass(frozen=True, slots=True)
class OutputHandle:
    """Opaque, MAC-authenticated authority for one exact stored incarnation."""

    owner_principal: str = field(repr=False)
    sha256: str
    byte_size: int
    width: int
    height: int
    storage_incarnation: str
    mime_type: str = MIME_TYPE
    _store_id: str = field(default="", repr=False)
    _auth_tag: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class PublishedOutput:
    handle: OutputHandle
    created: bool


@dataclass(frozen=True, slots=True)
class PreparedOutput:
    """One sanitized candidate bound to one exact store and PNG profile.

    The sanitized bytes and owner are deliberately absent from ``repr``.  The
    private authentication tag prevents a caller from manufacturing or
    modifying a prepared value and then using it as publication authority.
    """

    owner_principal: str = field(repr=False)
    sha256: str
    byte_size: int
    profile: PngProfile
    mime_type: str = MIME_TYPE
    _sanitized_bytes: bytes = field(default=b"", repr=False)
    _store_id: str = field(default="", repr=False)
    _auth_tag: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class _ObjectManifest:
    store_id: str
    owner_binding: str
    sha256: str
    byte_size: int
    width: int
    height: int
    storage_incarnation: str

    def encoded(self) -> bytes:
        return json.dumps(
            {
                "byte_size": self.byte_size,
                "height": self.height,
                "storage_incarnation": self.storage_incarnation,
                "owner_binding": self.owner_binding,
                "schema_version": _MANIFEST_SCHEMA,
                "sha256": self.sha256,
                "store_id": self.store_id,
                "width": self.width,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")


@dataclass(slots=True)
class _OwnerLayout:
    owner_fd: int
    sha256_fd: int
    prefix_fd: int

    def close(self) -> None:
        for name in ("prefix_fd", "sha256_fd", "owner_fd"):
            value = getattr(self, name)
            if value >= 0:
                os.close(value)
                setattr(self, name, -1)


def _canonical_absolute(value: str | os.PathLike[str], label: str) -> Path:
    raw = os.fspath(value)
    try:
        encoded_length = len(raw.encode("utf-8", "strict")) if isinstance(raw, str) else -1
    except UnicodeError:
        encoded_length = -1
    if (
        not isinstance(raw, str)
        or not raw
        or encoded_length < 1
        or encoded_length > 4096
        or "\x00" in raw
        or not raw.startswith("/")
        or raw.startswith("//")
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise GeneratedOutputConfigurationError(
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


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _snapshot(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
    )


def _validate_directory(
    fd: int,
    *,
    expected_uid: int,
    device: int | None = None,
    exact_mode: bool = True,
) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (exact_mode and info.st_uid != expected_uid)
        or (exact_mode and stat.S_IMODE(info.st_mode) != DIRECTORY_MODE)
        or (device is not None and info.st_dev != device)
    ):
        raise GeneratedOutputCorrupt("private output directory integrity failed")
    return info


def _validate_file(
    fd: int,
    *,
    expected_uid: int,
    device: int,
    maximum: int | None = None,
    exact_size: int | None = None,
) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != FILE_MODE
        or info.st_dev != device
        or info.st_nlink != 1
        or (maximum is not None and info.st_size > maximum)
        or (exact_size is not None and info.st_size != exact_size)
    ):
        raise GeneratedOutputCorrupt("private output file integrity failed")
    return info


def _absolute_directory_chain(path: Path) -> list[tuple[Path, int, os.stat_result]]:
    """Open every component without following symlinks; caller closes the FDs."""

    current = os.open(os.path.sep, _directory_flags())
    result: list[tuple[Path, int, os.stat_result]] = [
        (Path(os.path.sep), current, os.fstat(current))
    ]
    built = Path(os.path.sep)
    try:
        for name in path.parts[1:]:
            built = built / name
            current = os.open(name, _directory_flags(), dir_fd=current)
            info = os.fstat(current)
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(os.fspath(built))
            result.append((built, current, info))
        return result
    except BaseException:
        for _item_path, fd, _info in reversed(result):
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


def _bounded_directory_names(
    directory_fd: int,
    maximum: int,
    *,
    error_type: type[GeneratedOutputStoreError] = GeneratedOutputCorrupt,
    message: str = "private output directory exceeds its entry bound",
) -> tuple[str, ...]:
    """Return at most ``maximum`` names without an unbounded listdir allocation."""

    if type(maximum) is not int or maximum < 0:
        raise GeneratedOutputConfigurationError("directory scan bound is invalid")
    names: list[str] = []
    try:
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                if len(names) >= maximum:
                    raise error_type(message)
                names.append(entry.name)
    except GeneratedOutputStoreError:
        raise
    except OSError as error:
        raise error_type(message) from error
    return tuple(names)


@dataclass(slots=True)
class _ProcessLock:
    mutex: threading.RLock = field(default_factory=threading.RLock)
    references: int = 0
    active_operation: object | None = None


_LOCK_REGISTRY_GUARD = threading.RLock()
_LOCK_REGISTRY: dict[tuple[int, int], _ProcessLock] = {}
_LOCK_REGISTRY_PID = os.getpid()
_OPEN_STORES: weakref.WeakSet[object] = weakref.WeakSet()
_FORKED_ACTIVE_LOCK_IDENTITIES: frozenset[tuple[int, int]] = frozenset()
# Reentrancy is deliberate: publication-session caller code may fork while no
# transient CAS descriptor exists, and the child hook must then invalidate the
# session rather than deadlock.  Regions that hold this guard across transient
# descriptors contain internal filesystem calls only, with no caller callback,
# DB, or network seam from which same-thread fork can be invoked.
_FORK_DESCRIPTOR_GUARD = threading.RLock()


def _before_fork() -> None:
    _FORK_DESCRIPTOR_GUARD.acquire()


def _after_fork_parent() -> None:
    _FORK_DESCRIPTOR_GUARD.release()


def _reset_lock_registry_after_fork() -> None:
    global _LOCK_REGISTRY_GUARD
    global _LOCK_REGISTRY
    global _LOCK_REGISTRY_PID
    global _OPEN_STORES
    global _FORKED_ACTIVE_LOCK_IDENTITIES
    global _FORK_DESCRIPTOR_GUARD

    # A child must discard every inherited descriptor copy without consulting
    # an RLock that may have been owned by a vanished parent thread. Closing a
    # forked descriptor copy leaves the parent's descriptor (and flock) intact.
    # Remember locks that were active at the instant of fork so a fresh child
    # constructor fails closed instead of waiting on a parent that may be
    # synchronously waiting for that child.
    inherited = tuple(_OPEN_STORES)
    active_identities = set(_FORKED_ACTIVE_LOCK_IDENTITIES)
    for store in inherited:
        identity = getattr(store, "_lock_identity", None)
        process_lock = getattr(store, "_process_lock", None)
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
    _OPEN_STORES = weakref.WeakSet()
    _FORKED_ACTIVE_LOCK_IDENTITIES = frozenset(active_identities)

    closed_fds: set[int] = set()
    for store in inherited:
        for name in ("_lock_fd", "_owners_fd", "_root_fd"):
            fd = getattr(store, name, -1)
            try:
                object.__setattr__(store, name, -1)
            except BaseException:
                continue
            if isinstance(fd, int) and fd >= 0 and fd not in closed_fds:
                closed_fds.add(fd)
                try:
                    os.close(fd)
                except OSError:
                    pass
        for name, value in (
            ("_process_lock", None),
            ("_active_publication_session", None),
            ("_operation_depth", 0),
        ):
            try:
                object.__setattr__(store, name, value)
            except BaseException:
                pass
    # The old guard was acquired by the at-fork prepare hook. It must not be
    # released in the child; replace it with a pristine child-process lock.
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


def _register_open_store(store: object) -> None:
    _prepare_lock_registry()
    with _FORK_DESCRIPTOR_GUARD:
        with _LOCK_REGISTRY_GUARD:
            _OPEN_STORES.add(store)


def _discard_open_store(store: object) -> None:
    _prepare_lock_registry()
    with _FORK_DESCRIPTOR_GUARD:
        with _LOCK_REGISTRY_GUARD:
            _OPEN_STORES.discard(store)


def _acquire_constructor_fork_guard(selected: Path) -> None:
    """Wait for unrelated descriptor windows, but reject this root promptly."""

    while not _FORK_DESCRIPTOR_GUARD.acquire(timeout=0.01):
        with _LOCK_REGISTRY_GUARD:
            active_same_root = any(
                getattr(store, "_pid", None) == os.getpid()
                and getattr(store, "root", None) == selected
                and getattr(store, "_operation_depth", 0) != 0
                for store in tuple(_OPEN_STORES)
            )
        if active_same_root:
            raise GeneratedOutputConfigurationError(
                "another private output operation is already active"
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


class PublicationSession:
    """Thread-bound capability holding the private CAS process/flock guard.

    Instances are issued only while :meth:`GeneratedOutputStore.publication_session`
    owns the store-wide operation lock.  Every method revalidates exact object
    identity, store identity, PID, thread, and active operation token.
    """

    __slots__ = ("_store", "_store_id", "_pid", "_thread", "_token")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("publication sessions are immutable")

    def __init__(
        self,
        store: "GeneratedOutputStore",
        token: object,
        issuer: object,
    ) -> None:
        if issuer is not _SESSION_ISSUER:
            raise TypeError("publication sessions are store-issued")
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_store_id", store._store_id)
        object.__setattr__(self, "_pid", os.getpid())
        object.__setattr__(self, "_thread", threading.current_thread())
        object.__setattr__(self, "_token", token)

    def __copy__(self) -> "PublicationSession":
        copied = object.__new__(PublicationSession)
        for name in self.__slots__:
            object.__setattr__(copied, name, getattr(self, name))
        return copied

    def __repr__(self) -> str:
        active = self._store._session_is_active(self)
        return f"PublicationSession(active={active})"

    def probe_live_exact(
        self, owner_principal: str, sha256: str, byte_size: int
    ) -> OutputHandle | None:
        """Probe one DB-derived owner/digest/size tuple without enumeration."""

        return self._store._session_probe_live_exact(
            self, owner_principal, sha256, byte_size
        )

    def publish_prepared(self, prepared: PreparedOutput) -> PublishedOutput:
        """Publish one exact prepared value while the guard remains held."""

        return self._store._session_publish_prepared(self, prepared)

    def validate_prepared(self, prepared: PreparedOutput) -> None:
        """Verify exact prepared authority without publishing or exposing it."""

        self._store._session_validate_prepared(self, prepared)

    def read_exact_incarnation(
        self,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> bytes:
        """Read one exact incarnation while physical deletion is excluded."""

        return self._store._session_read_exact_incarnation(
            self,
            owner_principal,
            sha256,
            byte_size,
            storage_incarnation,
        )

    def recover_delete_handle_exact(
        self,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> OutputHandle | None:
        """Recover exact deletion authority while the guard remains held."""

        return self._store._session_recover_delete_handle_exact(
            self,
            owner_principal,
            sha256,
            byte_size,
            storage_incarnation,
        )

    def delete(self, handle: OutputHandle) -> bool:
        """Delete one exact incarnation while the guard remains held."""

        return self._store._session_delete(self, handle)


class _ThreadBoundContext:
    """Keep a lock-owning context from being resumed by another thread."""

    __slots__ = ("_context", "_label", "_owner_thread", "_state", "_state_lock")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("thread-bound contexts are immutable")

    def __init__(self, context: object, label: str) -> None:
        object.__setattr__(self, "_context", context)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_owner_thread", None)
        object.__setattr__(self, "_state", "idle")
        object.__setattr__(self, "_state_lock", threading.Lock())

    def __enter__(self) -> object:
        owner = threading.current_thread()
        with self._state_lock:
            if self._state != "idle":
                raise GeneratedOutputConfigurationError(
                    f"{self._label} is already active"
                )
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
                raise GeneratedOutputConfigurationError(
                    f"{self._label} must exit in its owning thread"
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


class GeneratedOutputStore:
    """Owner-separated immutable CAS for one configured sanitized PNG profile."""

    def __init__(
        self,
        root: str | os.PathLike[str] = DEFAULT_PRIVATE_OUTPUT_ROOT,
        *,
        managed_ancestor: str | os.PathLike[str] = DEFAULT_MANAGED_ANCESTOR,
        expected_uid: int,
        namespace_key: bytes,
        comfy_view_dirs: Sequence[str | os.PathLike[str]],
        profile: PngProfile = PngProfile(),
    ) -> None:
        if type(expected_uid) is not int or expected_uid < 0:
            raise GeneratedOutputConfigurationError("expected UID is invalid")
        if expected_uid != os.geteuid():
            raise GeneratedOutputConfigurationError(
                "store process does not match the configured service UID"
            )
        if (
            type(namespace_key) is not bytes
            or len(namespace_key) < 32
            or len(namespace_key) > 128
        ):
            raise GeneratedOutputConfigurationError(
                "namespace key must contain 32 to 128 bytes"
            )
        if isinstance(comfy_view_dirs, (str, bytes)) or not isinstance(
            comfy_view_dirs, Sequence
        ) or not comfy_view_dirs or len(comfy_view_dirs) > 32:
            raise GeneratedOutputConfigurationError(
                "Comfy VIEW_DIRS must be an explicit non-empty sequence"
            )
        if type(profile) is not PngProfile:
            raise TypeError("profile must be PngProfile")

        selected = _canonical_absolute(root, "private output root")
        anchor = _canonical_absolute(managed_ancestor, "managed ancestor")
        try:
            relative = selected.relative_to(anchor)
        except ValueError as error:
            raise GeneratedOutputConfigurationError(
                "private output root must descend from its managed ancestor"
            ) from error
        if not relative.parts:
            raise GeneratedOutputConfigurationError(
                "managed ancestor must be a strict ancestor of the output root"
            )

        protected = tuple(
            _canonical_absolute(item, "Comfy view directory")
            for item in comfy_view_dirs
        )
        if len(set(protected)) != len(protected):
            raise GeneratedOutputConfigurationError("Comfy VIEW_DIRS contain duplicates")
        for item in protected:
            if _overlaps(selected, item) or _overlaps(
                Path(os.path.realpath(selected)), Path(os.path.realpath(item))
            ):
                raise GeneratedOutputConfigurationError(
                    "private output root overlaps a Comfy view directory"
                )

        root_chain: list[tuple[Path, int, os.stat_result]] = []
        view_chains: list[list[tuple[Path, int, os.stat_result]]] = []
        _acquire_constructor_fork_guard(selected)
        try:
            root_chain = _absolute_directory_chain(selected)
            for item in protected:
                view_chains.append(_absolute_directory_chain(item))
        except OSError as error:
            if root_chain:
                _close_chain(root_chain)
            for chain in view_chains:
                _close_chain(chain)
            _FORK_DESCRIPTOR_GUARD.release()
            raise GeneratedOutputConfigurationError(
                "private output or Comfy VIEW_DIRS contain a symlink or non-directory"
            ) from error
        except BaseException:
            if root_chain:
                _close_chain(root_chain)
            for chain in view_chains:
                _close_chain(chain)
            _FORK_DESCRIPTOR_GUARD.release()
            raise

        self.root = selected
        self.managed_ancestor = anchor
        self.comfy_view_dirs = protected
        self._expected_uid = expected_uid
        self._namespace_key = bytes(namespace_key)
        self._profile = profile
        self._profile_binding = (
            profile.width,
            profile.height,
            profile.maximum_bytes,
        )
        self._pid = os.getpid()
        self._root_fd = -1
        self._owners_fd = -1
        self._lock_fd = -1
        self._lock_identity: tuple[int, int] | None = None
        self._identity_marker_identity: tuple[int, int] | None = None
        self._owners_identity: tuple[int, int] | None = None
        self._process_lock: _ProcessLock | None = None
        self._operation_depth = 0
        self._active_publication_session: PublicationSession | None = None
        self._managed_bindings: tuple[tuple[Path, tuple[int, int]], ...] = ()
        self._view_bindings: tuple[tuple[Path, tuple[int, int]], ...] = ()
        self._root_identity: tuple[int, int] | None = None
        try:
            _register_open_store(self)
            indexed = {path: (fd, info) for path, fd, info in root_chain}
            if anchor not in indexed:
                raise GeneratedOutputConfigurationError(
                    "managed ancestor is not in the canonical output path"
                )
            anchor_index = next(
                index for index, (path, _fd, _info) in enumerate(root_chain)
                if path == anchor
            )
            managed = root_chain[anchor_index:]
            root_info = managed[-1][2]
            device = root_info.st_dev
            for _path, fd, _info in managed:
                try:
                    _validate_directory(
                        fd, expected_uid=expected_uid, device=device
                    )
                except GeneratedOutputCorrupt as error:
                    raise GeneratedOutputConfigurationError(
                        "managed output ancestor ownership, mode, or filesystem is unsafe"
                    ) from error

            root_final_identity = _identity(root_info)
            root_chain_identities = {_identity(info) for _path, _fd, info in root_chain}
            for chain in view_chains:
                view_final_identity = _identity(chain[-1][2])
                view_chain_identities = {
                    _identity(info) for _path, _fd, info in chain
                }
                if (
                    root_final_identity in view_chain_identities
                    or view_final_identity in root_chain_identities
                ):
                    raise GeneratedOutputConfigurationError(
                        "private output and Comfy VIEW_DIRS share an ancestor identity"
                    )

            self._device = device
            self._root_identity = root_final_identity
            with _FORK_DESCRIPTOR_GUARD:
                self._root_fd = os.dup(managed[-1][1])
            self._managed_bindings = tuple(
                (path, _identity(info)) for path, _fd, info in managed
            )
            self._view_bindings = tuple(
                (path, _identity(chain[-1][2]))
                for path, chain in zip(protected, view_chains, strict=True)
            )
            existing = set(
                _bounded_directory_names(
                    self._root_fd,
                    _MAX_ROOT_SCAN_ENTRIES,
                    error_type=GeneratedOutputConfigurationError,
                    message="private output root exceeds its entry bound",
                )
            )
            if any(
                name not in _ROOT_ENTRIES
                and _IDENTITY_TEMP_RE.fullmatch(name) is None
                for name in existing
            ):
                raise GeneratedOutputConfigurationError(
                    "private output root contains unexpected objects"
                )
            with _FORK_DESCRIPTOR_GUARD:
                self._lock_fd = self._open_or_create_lock()
            lock_info = _validate_file(
                self._lock_fd,
                expected_uid=self._expected_uid,
                device=self._device,
            )
            self._lock_identity = _identity(lock_info)
            if self._lock_identity in _FORKED_ACTIVE_LOCK_IDENTITIES:
                raise GeneratedOutputConfigurationError(
                    "a child forked during an active output operation cannot "
                    "reopen the same private store"
                )
            process_lock = _retain_process_lock(self._lock_identity)
            self._process_lock = process_lock
            if not process_lock.mutex.acquire(blocking=False):
                self._process_lock = None
                _release_process_lock(self._lock_identity, process_lock)
                raise GeneratedOutputConfigurationError(
                    "another private output operation is already active"
                )
            try:
                if self._process_lock.active_operation is not None:
                    raise GeneratedOutputConfigurationError(
                        "nested private output operations are unsupported"
                    )
                bootstrap_token = object()
                self._process_lock.active_operation = bootstrap_token
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                    try:
                        self._cleanup_identity_temporaries()
                        self._store_id = self._load_or_create_store_identity()
                        identity_raw, identity_info = self._read_file(
                            self._root_fd,
                            ".identity",
                            maximum=_IDENTITY_BYTES,
                            exact_size=_IDENTITY_BYTES,
                        )
                        if not hmac.compare_digest(
                            identity_raw,
                            self._identity_payload(self._store_id),
                        ):
                            raise GeneratedOutputConfigurationError(
                                "private output identity is inconsistent"
                            )
                        self._identity_marker_identity = _identity(identity_info)
                        with _FORK_DESCRIPTOR_GUARD:
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
                        self._validate_root_layout()
                    finally:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                finally:
                    if self._process_lock.active_operation is bootstrap_token:
                        self._process_lock.active_operation = None
            finally:
                process_lock.mutex.release()
            with self._locked():
                self._scavenge_orphan_temporary_outputs()
        except BaseException:
            self.close()
            raise
        finally:
            try:
                _close_chain(root_chain)
                for chain in view_chains:
                    _close_chain(chain)
            finally:
                _FORK_DESCRIPTOR_GUARD.release()

    def __enter__(self) -> "GeneratedOutputStore":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    @property
    def store_identity(self) -> str:
        """Return the validated non-secret identity of this storage ledger."""

        with self._locked():
            if _STORE_ID_RE.fullmatch(self._store_id) is None:
                raise GeneratedOutputCorrupt(
                    "private output identity is invalid"
                )
            return self._store_id

    @property
    def profile(self) -> PngProfile:
        """Return the exact validated immutable PNG profile for this store."""

        with self._locked():
            return self._validated_profile()

    def close(self) -> None:
        process_lock = getattr(self, "_process_lock", None)
        lock_identity = getattr(self, "_lock_identity", None)
        if os.getpid() != getattr(self, "_pid", os.getpid()):
            # After fork, the child must not touch the inherited RLock or the
            # process-local registry. Closing its descriptor copies cannot
            # unlock or close the parent's descriptor copies.
            with _FORK_DESCRIPTOR_GUARD:
                for name in ("_lock_fd", "_owners_fd", "_root_fd"):
                    fd = getattr(self, name, -1)
                    if isinstance(fd, int) and fd >= 0:
                        setattr(self, name, -1)
                        try:
                            os.close(fd)
                        except OSError:
                            pass
            self._process_lock = None
            self._active_publication_session = None
            self._operation_depth = 0
            _discard_open_store(self)
            return
        if process_lock is not None:
            with process_lock.mutex:
                self._close_fds(process_lock, lock_identity)
        else:
            self._close_fds(None, lock_identity)
        _discard_open_store(self)

    def _close_fds(
        self,
        process_lock: _ProcessLock | None,
        lock_identity: tuple[int, int] | None,
    ) -> None:
        if process_lock is not None and self._process_lock is not process_lock:
            return
        if self._operation_depth:
            raise GeneratedOutputConfigurationError(
                "private output store cannot close during an operation"
            )
        with _FORK_DESCRIPTOR_GUARD:
            for name in ("_lock_fd", "_owners_fd", "_root_fd"):
                fd = getattr(self, name, -1)
                if isinstance(fd, int) and fd >= 0:
                    setattr(self, name, -1)
                    os.close(fd)
        self._process_lock = None
        if process_lock is not None and lock_identity is not None:
            _release_process_lock(lock_identity, process_lock)

    def _cleanup_identity_temporaries(self) -> None:
        names = sorted(
            name
            for name in _bounded_directory_names(
                self._root_fd,
                _MAX_ROOT_SCAN_ENTRIES,
                error_type=GeneratedOutputConfigurationError,
                message="private output root exceeds its entry bound",
            )
            if _IDENTITY_TEMP_RE.fullmatch(name) is not None
        )
        if len(names) > 8:
            raise GeneratedOutputConfigurationError(
                "too many interrupted identity publications"
            )
        for name in names:
            try:
                fd = os.open(name, _read_flags(), dir_fd=self._root_fd)
            except OSError as error:
                raise GeneratedOutputConfigurationError(
                    "interrupted identity publication is unsafe"
                ) from error
            try:
                opened = os.fstat(fd)
                try:
                    named = os.stat(
                        name, dir_fd=self._root_fd, follow_symlinks=False
                    )
                except OSError as error:
                    raise GeneratedOutputConfigurationError(
                        "interrupted identity publication is unsafe"
                    ) from error
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_uid != self._expected_uid
                    or stat.S_IMODE(opened.st_mode) != FILE_MODE
                    or opened.st_dev != self._device
                    or opened.st_nlink not in {1, 2}
                    or _snapshot(named) != _snapshot(opened)
                ):
                    raise GeneratedOutputConfigurationError(
                        "interrupted identity publication is unsafe"
                    )
                if opened.st_nlink == 2:
                    # A crash after the atomic no-replace link but before
                    # unlinking its staging name leaves exactly these two
                    # names for one inode.  No other hard-link shape is safe.
                    try:
                        identity_fd = os.open(
                            ".identity", _read_flags(), dir_fd=self._root_fd
                        )
                    except OSError as error:
                        raise GeneratedOutputConfigurationError(
                            "interrupted identity publication is unsafe"
                        ) from error
                    try:
                        identity_info = os.fstat(identity_fd)
                        if (
                            _snapshot(identity_info) != _snapshot(opened)
                            or identity_info.st_size != _IDENTITY_BYTES
                        ):
                            raise GeneratedOutputConfigurationError(
                                "interrupted identity publication is unsafe"
                            )
                    finally:
                        os.close(identity_fd)
                os.unlink(name, dir_fd=self._root_fd)
            finally:
                os.close(fd)
        if names:
            os.fsync(self._root_fd)

    def _read_store_identity(self) -> str | None:
        try:
            fd = os.open(".identity", _read_flags(), dir_fd=self._root_fd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise GeneratedOutputConfigurationError(
                "private output identity is unavailable"
            ) from error
        try:
            info = _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=_IDENTITY_BYTES,
            )
            chunks: list[bytes] = []
            total = 0
            while total <= _IDENTITY_BYTES:
                chunk = os.read(fd, _IDENTITY_BYTES + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            value = b"".join(chunks)
            after = os.fstat(fd)
            if len(value) != _IDENTITY_BYTES or _snapshot(after) != _snapshot(info):
                raise GeneratedOutputCorrupt("private output identity changed")
        finally:
            os.close(fd)
        try:
            decoded = value.decode("ascii")
        except UnicodeError as error:
            raise GeneratedOutputConfigurationError(
                "private output identity is invalid"
            ) from error
        parts = decoded.split("\n")
        if (
            len(parts) != 2
            or _STORE_ID_RE.fullmatch(parts[0]) is None
            or _STORE_ID_RE.fullmatch(parts[1]) is None
            or not hmac.compare_digest(value, self._identity_payload(parts[0]))
        ):
            raise GeneratedOutputConfigurationError(
                "private output identity is invalid"
            )
        return parts[0]

    def _identity_payload(self, store_id: str) -> bytes:
        if type(store_id) is not str or _STORE_ID_RE.fullmatch(store_id) is None:
            raise GeneratedOutputConfigurationError(
                "private output identity is invalid"
            )
        profile = self._validated_profile()
        fields = (
            store_id,
            str(profile.width),
            str(profile.height),
            str(profile.maximum_bytes),
            MIME_TYPE,
            _MANIFEST_SCHEMA,
        )
        binding = b"\0".join(field.encode("ascii") for field in fields)
        tag = hmac.new(
            self._namespace_key,
            _IDENTITY_DOMAIN + binding,
            hashlib.sha256,
        ).hexdigest()
        return (store_id + "\n" + tag).encode("ascii")

    def _load_or_create_store_identity(self) -> str:
        existing = self._read_store_identity()
        if existing is not None:
            return existing
        store_id = secrets.token_hex(32)
        value = self._identity_payload(store_id)
        temporary = ".identity.tmp-" + secrets.token_hex(16)
        temporary_created = False
        fd = -1
        try:
            fd = os.open(
                temporary, _create_flags(), FILE_MODE, dir_fd=self._root_fd
            )
            temporary_created = True
            os.fchmod(fd, FILE_MODE)
            _write_all(fd, value)
            os.fsync(fd)
            _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=_IDENTITY_BYTES,
            )
            os.fsync(self._root_fd)
            # The bootstrap flock serializes compliant constructors, while
            # link(2) gives publication true no-replace semantics against a
            # pre-provisioner that does not participate in that lock.  A
            # check-then-rename would be able to overwrite such a winner.
            try:
                os.link(
                    temporary,
                    ".identity",
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                # A non-locking provisioner may itself have used the same
                # hard-link publication protocol and not yet removed its
                # staging name.  Finalize only validated identity temporaries
                # before reading the immutable winner.
                self._cleanup_identity_temporaries()
                winner = self._read_store_identity()
                if winner is None:
                    raise GeneratedOutputConfigurationError(
                        "private output identity winner disappeared"
                    )
                return winner

            linked = os.fstat(fd)
            try:
                named_temporary = os.stat(
                    temporary, dir_fd=self._root_fd, follow_symlinks=False
                )
                named_identity = os.stat(
                    ".identity", dir_fd=self._root_fd, follow_symlinks=False
                )
            except OSError as error:
                raise GeneratedOutputConfigurationError(
                    "private output identity publication binding changed"
                ) from error
            if (
                not stat.S_ISREG(linked.st_mode)
                or linked.st_uid != self._expected_uid
                or stat.S_IMODE(linked.st_mode) != FILE_MODE
                or linked.st_dev != self._device
                or linked.st_nlink != 2
                or linked.st_size != _IDENTITY_BYTES
                or _snapshot(named_temporary) != _snapshot(linked)
                or _snapshot(named_identity) != _snapshot(linked)
            ):
                raise GeneratedOutputConfigurationError(
                    "private output identity publication binding changed"
                )

            # Persist the winning name before removing the staging name, then
            # persist that unlink and require the final marker to be a
            # single-link exact file again.
            os.fsync(self._root_fd)
            os.unlink(temporary, dir_fd=self._root_fd)
            temporary_created = False
            os.fsync(self._root_fd)
            final_info = _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=_IDENTITY_BYTES,
            )
            try:
                named_identity = os.stat(
                    ".identity", dir_fd=self._root_fd, follow_symlinks=False
                )
            except OSError as error:
                raise GeneratedOutputConfigurationError(
                    "private output identity publication binding changed"
                ) from error
            if _snapshot(named_identity) != _snapshot(final_info):
                raise GeneratedOutputConfigurationError(
                    "private output identity publication binding changed"
                )
            published = self._read_store_identity()
            if published is None or published != store_id:
                raise GeneratedOutputConfigurationError(
                    "private output identity publication failed"
                )
            return published
        except OSError as error:
            raise GeneratedOutputConfigurationError(
                "private output identity could not be published atomically"
            ) from error
        finally:
            if fd >= 0:
                os.close(fd)
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                except FileNotFoundError:
                    pass

    def _open_or_create_lock(self) -> int:
        created = False
        try:
            try:
                fd = os.open(
                    ".store.lock",
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    FILE_MODE,
                    dir_fd=self._root_fd,
                )
                created = True
            except FileExistsError:
                fd = os.open(
                    ".store.lock",
                    os.O_RDWR
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._root_fd,
                )
            if created:
                os.fchmod(fd, FILE_MODE)
            return fd
        except OSError as error:
            raise GeneratedOutputConfigurationError(
                "private output lock is unavailable"
            ) from error

    def _validate_root_layout(self) -> None:
        if set(
            _bounded_directory_names(
                self._root_fd,
                _MAX_ROOT_SCAN_ENTRIES,
                message="private output root exceeds its entry bound",
            )
        ) != _ROOT_ENTRIES:
            raise GeneratedOutputCorrupt(
                "private output root contains unexpected objects"
            )

    def _scavenge_orphan_temporary_outputs(self) -> None:
        """Remove only incomplete ``.tmp-*`` publications under the lock.

        The traversal is internal, bounded, owner-HMAC-only, and never exposed
        as a caller listing API.  Live digest objects and delete quarantines
        are validated as directories but are never removed here.
        """

        scanned = 0

        def entries(directory_fd: int) -> tuple[str, ...]:
            nonlocal scanned
            values = _bounded_directory_names(
                directory_fd,
                _MAX_STARTUP_SCAN_ENTRIES - scanned,
                error_type=GeneratedOutputConfigurationError,
                message="private output startup scan exceeds its bound",
            )
            scanned += len(values)
            return values

        def required_directory(parent_fd: int, name: str) -> int:
            selected_fd = self._try_open_directory(parent_fd, name)
            if selected_fd is None:
                raise GeneratedOutputCorrupt(
                    "private output layout changed during startup recovery"
                )
            return selected_fd

        for owner_name in entries(self._owners_fd):
            if _OWNER_BINDING_RE.fullmatch(owner_name) is None:
                raise GeneratedOutputCorrupt(
                    "private output owner namespace is invalid"
                )
            owner_fd = required_directory(self._owners_fd, owner_name)
            try:
                owner_entries = entries(owner_fd)
                if any(name != "sha256" for name in owner_entries):
                    raise GeneratedOutputCorrupt(
                        "private output owner layout is invalid"
                    )
                if "sha256" not in owner_entries:
                    continue
                sha256_fd = required_directory(owner_fd, "sha256")
                try:
                    for prefix in entries(sha256_fd):
                        if _PREFIX_RE.fullmatch(prefix) is None:
                            raise GeneratedOutputCorrupt(
                                "private output digest prefix is invalid"
                            )
                        prefix_fd = required_directory(sha256_fd, prefix)
                        try:
                            for name in entries(prefix_fd):
                                if _TEMP_RE.fullmatch(name) is not None:
                                    self._remove_temporary_directory(
                                        prefix_fd, name
                                    )
                                    continue
                                if (
                                    _DIGEST_RE.fullmatch(name) is None
                                    and _DELETE_RE.fullmatch(name) is None
                                ):
                                    raise GeneratedOutputCorrupt(
                                        "private output object name is invalid"
                                    )
                                object_fd = required_directory(
                                    prefix_fd, name
                                )
                                os.close(object_fd)
                        finally:
                            os.close(prefix_fd)
                finally:
                    os.close(sha256_fd)
            finally:
                os.close(owner_fd)

    def _open_or_create_directory(self, parent_fd: int, name: str) -> int:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", name) is None:
            raise GeneratedOutputCorrupt("derived directory name is invalid")
        try:
            os.mkdir(name, DIRECTORY_MODE, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "private output directory could not be reserved"
            ) from error
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            _validate_directory(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
            )
            return fd
        except BaseException:
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass
            raise GeneratedOutputCorrupt(
                "private output directory integrity failed"
            ) from None

    def _try_open_directory(self, parent_fd: int, name: str) -> int | None:
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "private output directory cannot be opened safely"
            ) from error
        try:
            _validate_directory(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
            )
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _owner_binding(self, owner_principal: str) -> str:
        if type(owner_principal) is not str or _OWNER_RE.fullmatch(
            owner_principal
        ) is None:
            raise ValueError("owner principal is invalid")
        return hmac.new(
            self._namespace_key,
            _OWNER_DOMAIN
            + self._store_id.encode("ascii")
            + b"\0"
            + owner_principal.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def _validated_profile(self) -> PngProfile:
        profile = self._profile
        binding = self._profile_binding
        if (
            type(profile) is not PngProfile
            or type(binding) is not tuple
            or len(binding) != 3
            or any(type(value) is not int for value in binding)
            or (
                profile.width,
                profile.height,
                profile.maximum_bytes,
            )
            != binding
        ):
            raise GeneratedOutputCorrupt("private output PNG profile binding changed")
        return profile

    def _prepared_tag(
        self,
        owner_principal: str,
        digest: str,
        byte_size: int,
        profile: PngProfile,
    ) -> str:
        fields = (
            owner_principal,
            digest,
            str(byte_size),
            str(profile.width),
            str(profile.height),
            str(profile.maximum_bytes),
            MIME_TYPE,
            self._store_id,
        )
        payload = b"\0".join(item.encode("ascii") for item in fields)
        return hmac.new(
            self._namespace_key, _PREPARED_DOMAIN + payload, hashlib.sha256
        ).hexdigest()

    def prepare_png(
        self, owner_principal: str, png_bytes: bytes
    ) -> PreparedOutput:
        """Sanitize exactly once and authenticate an in-memory candidate."""

        if os.getpid() != self._pid:
            raise GeneratedOutputConfigurationError(
                "store instances cannot be inherited across fork"
            )
        if (
            self._process_lock is None
            or self._root_fd < 0
            or self._lock_fd < 0
            or self._owners_fd < 0
        ):
            raise GeneratedOutputConfigurationError("private output store is closed")
        profile = self._validated_profile()
        self._owner_binding(owner_principal)
        if (
            type(png_bytes) is not bytes
            or len(png_bytes) > profile.maximum_bytes
        ):
            raise GeneratedOutputInvalid(
                "candidate is not a bounded PNG byte string"
            )
        try:
            sanitized = sanitize_png(
                png_bytes,
                expected_width=profile.width,
                expected_height=profile.height,
                maximum_bytes=profile.maximum_bytes,
            )
        except SceneArtError as error:
            raise GeneratedOutputInvalid(
                "candidate is not the configured PNG profile"
            ) from error
        data = sanitized.data
        if (
            type(data) is not bytes
            or not data
            or len(data) > profile.maximum_bytes
        ):
            raise GeneratedOutputInvalid(
                "candidate is not the configured PNG profile"
            )
        digest = hashlib.sha256(data).hexdigest()
        return PreparedOutput(
            owner_principal=owner_principal,
            sha256=digest,
            byte_size=len(data),
            profile=profile,
            _sanitized_bytes=data,
            _store_id=self._store_id,
            _auth_tag=self._prepared_tag(
                owner_principal, digest, len(data), profile
            ),
        )

    def _validate_prepared(self, prepared: PreparedOutput) -> str:
        if type(prepared) is not PreparedOutput:
            raise GeneratedOutputInvalid("prepared output is invalid")
        try:
            owner_binding = self._owner_binding(prepared.owner_principal)
            valid = (
                prepared._store_id == self._store_id
                and type(prepared.sha256) is str
                and _DIGEST_RE.fullmatch(prepared.sha256) is not None
                and type(prepared.byte_size) is int
                and 0 < prepared.byte_size <= self._profile.maximum_bytes
                and type(prepared.profile) is PngProfile
                and prepared.profile == self._profile
                and type(prepared.mime_type) is str
                and prepared.mime_type == MIME_TYPE
                and type(prepared._sanitized_bytes) is bytes
                and len(prepared._sanitized_bytes) == prepared.byte_size
                and type(prepared._store_id) is str
                and type(prepared._auth_tag) is str
                and _DIGEST_RE.fullmatch(prepared._auth_tag) is not None
                and hmac.compare_digest(
                    hashlib.sha256(prepared._sanitized_bytes).hexdigest(),
                    prepared.sha256,
                )
            )
            expected_tag = self._prepared_tag(
                prepared.owner_principal,
                prepared.sha256,
                prepared.byte_size,
                prepared.profile,
            )
        except (AttributeError, TypeError, ValueError, UnicodeError):
            raise GeneratedOutputInvalid("prepared output is invalid") from None
        if not valid or not hmac.compare_digest(expected_tag, prepared._auth_tag):
            raise GeneratedOutputInvalid("prepared output is invalid")
        return owner_binding

    def _quarantine_name(
        self, owner_binding: str, digest: str, storage_incarnation: str
    ) -> str:
        if (
            _OWNER_BINDING_RE.fullmatch(owner_binding) is None
            or _DIGEST_RE.fullmatch(digest) is None
            or _INCARNATION_RE.fullmatch(storage_incarnation) is None
        ):
            raise GeneratedOutputCorrupt("derived quarantine binding is invalid")
        token = hmac.new(
            self._namespace_key,
            _QUARANTINE_DOMAIN
            + self._store_id.encode("ascii")
            + b"\0"
            + owner_binding.encode("ascii")
            + b"\0"
            + digest.encode("ascii")
            + b"\0"
            + storage_incarnation.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:32]
        return ".delete-" + token

    def _open_owner_layout(
        self, owner_binding: str, digest: str, *, create: bool
    ) -> _OwnerLayout | None:
        if _OWNER_BINDING_RE.fullmatch(owner_binding) is None:
            raise GeneratedOutputCorrupt("derived owner binding is invalid")
        if _DIGEST_RE.fullmatch(digest) is None:
            raise ValueError("sha256 is invalid")
        opener = self._open_or_create_directory if create else self._try_open_directory
        owner_fd = opener(self._owners_fd, owner_binding)
        if owner_fd is None:
            return None
        sha256_fd = -1
        prefix_fd = -1
        try:
            sha256_fd = opener(owner_fd, "sha256")
            if sha256_fd is None:
                os.close(owner_fd)
                return None
            prefix_fd = opener(sha256_fd, digest[:2])
            if prefix_fd is None:
                os.close(sha256_fd)
                os.close(owner_fd)
                return None
            return _OwnerLayout(owner_fd, sha256_fd, prefix_fd)
        except BaseException:
            for fd in (prefix_fd, sha256_fd, owner_fd):
                if fd >= 0:
                    os.close(fd)
            raise

    def _verify_runtime_bindings(self) -> None:
        if os.getpid() != self._pid:
            raise GeneratedOutputConfigurationError(
                "store instances cannot be inherited across fork"
            )
        if self._root_fd < 0 or self._lock_fd < 0 or self._owners_fd < 0:
            raise GeneratedOutputConfigurationError("private output store is closed")
        self._validated_profile()
        for path, expected_identity in self._managed_bindings:
            try:
                chain = _absolute_directory_chain(path)
            except OSError as error:
                raise GeneratedOutputCorrupt(
                    "managed output path binding changed"
                ) from error
            try:
                fd = chain[-1][1]
                info = _validate_directory(
                    fd,
                    expected_uid=self._expected_uid,
                    device=self._device,
                )
                if _identity(info) != expected_identity:
                    raise GeneratedOutputCorrupt(
                        "managed output path binding changed"
                    )
            finally:
                _close_chain(chain)
        try:
            root_chain = _absolute_directory_chain(self.root)
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "managed output path binding changed"
            ) from error
        try:
            root_identities = {
                _identity(info) for _path, _fd, info in root_chain
            }
            if (
                self._root_identity is None
                or _identity(root_chain[-1][2]) != self._root_identity
            ):
                raise GeneratedOutputCorrupt(
                    "managed output path binding changed"
                )
            for path, expected_identity in self._view_bindings:
                try:
                    view_chain = _absolute_directory_chain(path)
                except OSError as error:
                    raise GeneratedOutputCorrupt(
                        "Comfy VIEW_DIR binding changed"
                    ) from error
                try:
                    view_identity = _identity(view_chain[-1][2])
                    view_identities = {
                        _identity(info) for _path, _fd, info in view_chain
                    }
                    if (
                        view_identity != expected_identity
                        or self._root_identity in view_identities
                        or view_identity in root_identities
                    ):
                        raise GeneratedOutputCorrupt(
                            "Comfy VIEW_DIR binding changed or overlaps private output"
                        )
                finally:
                    _close_chain(view_chain)
        finally:
            _close_chain(root_chain)
        held = _validate_file(
            self._lock_fd,
            expected_uid=self._expected_uid,
            device=self._device,
        )
        try:
            named_fd = os.open(
                ".store.lock",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._root_fd,
            )
        except OSError as error:
            raise GeneratedOutputCorrupt("private output lock binding changed") from error
        try:
            named = _validate_file(
                named_fd,
                expected_uid=self._expected_uid,
                device=self._device,
            )
            if (
                self._lock_identity is None
                or _identity(held) != self._lock_identity
                or _identity(named) != self._lock_identity
            ):
                raise GeneratedOutputCorrupt(
                    "private output lock binding changed"
                )
        finally:
            os.close(named_fd)
        identity_raw, identity_info = self._read_file(
            self._root_fd,
            ".identity",
            maximum=_IDENTITY_BYTES,
            exact_size=_IDENTITY_BYTES,
        )
        if (
            self._identity_marker_identity is None
            or _identity(identity_info) != self._identity_marker_identity
            or not hmac.compare_digest(
                identity_raw, self._identity_payload(self._store_id)
            )
        ):
            raise GeneratedOutputCorrupt(
                "private output identity binding changed"
            )
        held_owners = _validate_directory(
            self._owners_fd,
            expected_uid=self._expected_uid,
            device=self._device,
        )
        named_owners_fd = self._try_open_directory(self._root_fd, "owners")
        if named_owners_fd is None:
            raise GeneratedOutputCorrupt("private output owners binding changed")
        try:
            named_owners = os.fstat(named_owners_fd)
            if (
                self._owners_identity is None
                or _identity(held_owners) != self._owners_identity
                or _identity(named_owners) != self._owners_identity
            ):
                raise GeneratedOutputCorrupt(
                    "private output owners binding changed"
                )
        finally:
            os.close(named_owners_fd)
        self._validate_root_layout()

    def _operation(self) -> _ThreadBoundContext:
        return _ThreadBoundContext(
            self._operation_context(), "private output operation"
        )

    @contextmanager
    def _operation_context(self) -> Iterator[object]:
        if os.getpid() != self._pid:
            raise GeneratedOutputConfigurationError(
                "store instances cannot be inherited across fork"
            )
        value = self._process_lock
        if value is None:
            raise GeneratedOutputConfigurationError("private output store is closed")
        with value.mutex:
            if self._process_lock is not value or self._lock_fd < 0:
                raise GeneratedOutputConfigurationError(
                    "private output store is closed"
                )
            if self._operation_depth or value.active_operation is not None:
                raise GeneratedOutputConfigurationError(
                    "nested private output operations are unsupported"
                )
            token = object()
            self._operation_depth = 1
            value.active_operation = token
            flock_held = False
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                flock_held = True
                try:
                    with _FORK_DESCRIPTOR_GUARD:
                        self._verify_runtime_bindings()
                    yield token
                finally:
                    if flock_held:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                if value.active_operation is token:
                    value.active_operation = None
                self._operation_depth = 0

    def _locked(self) -> _ThreadBoundContext:
        return _ThreadBoundContext(
            self._locked_context(), "private output locked operation"
        )

    @contextmanager
    def _locked_context(self) -> Iterator[None]:
        with self._operation():
            with _FORK_DESCRIPTOR_GUARD:
                yield

    def publication_session(self) -> _ThreadBoundContext:
        return _ThreadBoundContext(
            self._publication_session_context(), "publication session"
        )

    @contextmanager
    def _publication_session_context(self) -> Iterator[PublicationSession]:
        """Hold the CAS process mutex and filesystem flock across coordination.

        The caller may commit a durable database materialization intent while
        this context is open, publish or probe the exact object, and then
        commit adoption before releasing the guard.  No path or inventory is
        exposed.
        """

        with self._operation() as token:
            session = PublicationSession(self, token, _SESSION_ISSUER)
            self._active_publication_session = session
            try:
                yield session
            finally:
                if self._active_publication_session is session:
                    self._active_publication_session = None

    def _session_is_active(self, session: object) -> bool:
        value = self._process_lock
        return bool(
            type(session) is PublicationSession
            and self._active_publication_session is session
            and session._store is self
            and session._store_id == self._store_id
            and session._pid == self._pid == os.getpid()
            and session._thread is threading.current_thread()
            and value is not None
            and value.active_operation is session._token
            and self._operation_depth == 1
            and self._lock_fd >= 0
        )

    def _validate_publication_session(
        self, session: PublicationSession
    ) -> None:
        if not self._session_is_active(session):
            raise GeneratedOutputConfigurationError(
                "publication session is invalid or inactive"
            )
        self._verify_runtime_bindings()

    def _write_exclusive(
        self, parent_fd: int, name: str, value: bytes
    ) -> tuple[int, os.stat_result]:
        try:
            fd = os.open(name, _create_flags(), FILE_MODE, dir_fd=parent_fd)
            os.fchmod(fd, FILE_MODE)
            _write_all(fd, value)
            os.fsync(fd)
            info = _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                exact_size=len(value),
            )
            return fd, info
        except BaseException:
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass
            raise

    def _read_file(
        self, parent_fd: int, name: str, *, maximum: int, exact_size: int | None = None
    ) -> tuple[bytes, os.stat_result]:
        try:
            fd = os.open(name, _read_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "private output object is incomplete"
            ) from error
        try:
            before = _validate_file(
                fd,
                expected_uid=self._expected_uid,
                device=self._device,
                maximum=maximum,
                exact_size=exact_size,
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
            if (
                total != before.st_size
                or total > maximum
                or _snapshot(after) != _snapshot(before)
            ):
                raise GeneratedOutputCorrupt(
                    "private output object changed while reading"
                )
            try:
                named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise GeneratedOutputCorrupt(
                    "private output object binding changed while reading"
                ) from error
            if _snapshot(named) != _snapshot(before):
                raise GeneratedOutputCorrupt(
                    "private output object binding changed while reading"
                )
            return b"".join(chunks), before
        finally:
            os.close(fd)

    def _parse_manifest(
        self,
        raw: bytes,
        *,
        owner_binding: str,
        digest: str,
    ) -> _ObjectManifest:
        try:
            value = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=lambda pairs: _unique_json_object(pairs),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
            raise GeneratedOutputCorrupt("private output manifest is invalid") from error
        keys = {
            "byte_size",
            "height",
            "owner_binding",
            "schema_version",
            "sha256",
            "storage_incarnation",
            "store_id",
            "width",
        }
        if not isinstance(value, dict) or set(value) != keys:
            raise GeneratedOutputCorrupt("private output manifest is invalid")
        if (
            value["schema_version"] != _MANIFEST_SCHEMA
            or value["store_id"] != self._store_id
            or value["owner_binding"] != owner_binding
            or value["sha256"] != digest
            or type(value["byte_size"]) is not int
            or value["byte_size"] <= 0
            or value["byte_size"] > self._profile.maximum_bytes
            or type(value["width"]) is not int
            or value["width"] != self._profile.width
            or type(value["height"]) is not int
            or value["height"] != self._profile.height
            or not isinstance(value["storage_incarnation"], str)
            or _INCARNATION_RE.fullmatch(value["storage_incarnation"]) is None
        ):
            raise GeneratedOutputCorrupt("private output manifest is invalid")
        manifest = _ObjectManifest(
            store_id=value["store_id"],
            owner_binding=value["owner_binding"],
            sha256=value["sha256"],
            byte_size=value["byte_size"],
            width=value["width"],
            height=value["height"],
            storage_incarnation=value["storage_incarnation"],
        )
        if not hmac.compare_digest(raw, manifest.encoded()):
            raise GeneratedOutputCorrupt("private output manifest is not canonical")
        return manifest

    def _read_object(
        self,
        prefix_fd: int,
        *,
        owner_binding: str,
        digest: str,
        object_name: str | None = None,
    ) -> tuple[bytes, _ObjectManifest, os.stat_result]:
        selected_name = object_name or digest
        if selected_name != digest and _DELETE_RE.fullmatch(selected_name) is None:
            raise GeneratedOutputCorrupt("private output object name is invalid")
        object_fd = self._try_open_directory(prefix_fd, selected_name)
        if object_fd is None:
            raise GeneratedOutputNotFound("generated output is unavailable")
        try:
            if set(_bounded_directory_names(object_fd, 3)) != _OBJECT_ENTRIES:
                raise GeneratedOutputCorrupt(
                    "private output object contains unexpected entries"
                )
            manifest_raw, _manifest_info = self._read_file(
                object_fd, "manifest.json", maximum=_MAX_MANIFEST_BYTES
            )
            manifest = self._parse_manifest(
                manifest_raw, owner_binding=owner_binding, digest=digest
            )
            image, _image_info = self._read_file(
                object_fd,
                "image.png",
                maximum=self._profile.maximum_bytes,
                exact_size=manifest.byte_size,
            )
            if not hmac.compare_digest(hashlib.sha256(image).hexdigest(), digest):
                raise GeneratedOutputCorrupt(
                    "private output content digest does not match"
                )
            try:
                named = os.stat(
                    selected_name, dir_fd=prefix_fd, follow_symlinks=False
                )
            except OSError as error:
                raise GeneratedOutputCorrupt(
                    "private output directory binding changed while reading"
                ) from error
            opened = os.fstat(object_fd)
            if _snapshot(named) != _snapshot(opened):
                raise GeneratedOutputCorrupt(
                    "private output directory binding changed while reading"
                )
            return image, manifest, opened
        finally:
            os.close(object_fd)

    def _new_handle(
        self, owner_principal: str, manifest: _ObjectManifest
    ) -> OutputHandle:
        fields = (
            owner_principal,
            manifest.sha256,
            str(manifest.byte_size),
            str(manifest.width),
            str(manifest.height),
            manifest.storage_incarnation,
            MIME_TYPE,
            self._store_id,
        )
        payload = b"\0".join(item.encode("ascii") for item in fields)
        tag = hmac.new(
            self._namespace_key, _HANDLE_DOMAIN + payload, hashlib.sha256
        ).hexdigest()
        return OutputHandle(
            owner_principal=owner_principal,
            sha256=manifest.sha256,
            byte_size=manifest.byte_size,
            width=manifest.width,
            height=manifest.height,
            storage_incarnation=manifest.storage_incarnation,
            _store_id=self._store_id,
            _auth_tag=tag,
        )

    def _manifest_for_handle(
        self, owner_binding: str, handle: OutputHandle
    ) -> _ObjectManifest:
        return _ObjectManifest(
            store_id=self._store_id,
            owner_binding=owner_binding,
            sha256=handle.sha256,
            byte_size=handle.byte_size,
            width=handle.width,
            height=handle.height,
            storage_incarnation=handle.storage_incarnation,
        )

    def _open_exact_quarantine(
        self,
        prefix_fd: int,
        *,
        quarantine: str,
        expected: _ObjectManifest,
    ) -> tuple[int, tuple[int, int], frozenset[str]] | None:
        """Open and validate any restart-safe stage of one exact deletion."""

        directory_fd = self._try_open_directory(prefix_fd, quarantine)
        if directory_fd is None:
            return None
        try:
            opened = _validate_directory(
                directory_fd,
                expected_uid=self._expected_uid,
                device=self._device,
            )
            entries = frozenset(_bounded_directory_names(directory_fd, 3))
            if not entries.issubset(_OBJECT_ENTRIES):
                raise GeneratedOutputCorrupt(
                    "quarantined output contains unexpected entries"
                )
            # Deletion order is image, durable directory sync, manifest,
            # durable directory sync.  Therefore image-only is not a valid
            # crash state.  A remaining manifest authenticates a manifest-only
            # stage; an empty directory is authenticated by the HMAC-derived
            # quarantine name plus the already-MAC-validated handle.
            if "image.png" in entries and "manifest.json" not in entries:
                raise GeneratedOutputCorrupt(
                    "quarantined output has an impossible partial state"
                )
            if "manifest.json" in entries:
                raw, _info = self._read_file(
                    directory_fd,
                    "manifest.json",
                    maximum=_MAX_MANIFEST_BYTES,
                )
                manifest = self._parse_manifest(
                    raw,
                    owner_binding=expected.owner_binding,
                    digest=expected.sha256,
                )
                if manifest != expected:
                    raise GeneratedOutputCorrupt(
                        "quarantined output does not match its exact incarnation"
                    )
            if "image.png" in entries:
                image, _info = self._read_file(
                    directory_fd,
                    "image.png",
                    maximum=self._profile.maximum_bytes,
                    exact_size=expected.byte_size,
                )
                if not hmac.compare_digest(
                    hashlib.sha256(image).hexdigest(), expected.sha256
                ):
                    raise GeneratedOutputCorrupt(
                        "quarantined output digest does not match"
                    )
            self._validate_named_directory_binding(
                prefix_fd,
                quarantine,
                directory_fd,
                _identity(opened),
            )
            return directory_fd, _identity(opened), entries
        except BaseException:
            os.close(directory_fd)
            raise

    def _validate_named_directory_binding(
        self,
        parent_fd: int,
        name: str,
        directory_fd: int,
        expected_identity: tuple[int, int],
    ) -> None:
        opened = _validate_directory(
            directory_fd,
            expected_uid=self._expected_uid,
            device=self._device,
        )
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "private output directory binding changed"
            ) from error
        if (
            not stat.S_ISDIR(named.st_mode)
            or named.st_uid != self._expected_uid
            or stat.S_IMODE(named.st_mode) != DIRECTORY_MODE
            or named.st_dev != self._device
            or _identity(opened) != expected_identity
            or _identity(named) != expected_identity
        ):
            raise GeneratedOutputCorrupt(
                "private output directory binding changed"
            )

    def _cleanup_exact_quarantine(
        self,
        prefix_fd: int,
        *,
        quarantine: str,
        expected: _ObjectManifest,
    ) -> bool:
        """Resume deletion from every durable child-unlink boundary."""

        opened = self._open_exact_quarantine(
            prefix_fd,
            quarantine=quarantine,
            expected=expected,
        )
        if opened is None:
            return False
        directory_fd, directory_identity, entries = opened
        try:
            for name in ("image.png", "manifest.json"):
                if name not in entries:
                    continue
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
                self._validate_named_directory_binding(
                    prefix_fd,
                    quarantine,
                    directory_fd,
                    directory_identity,
                )
        finally:
            os.close(directory_fd)
        try:
            os.rmdir(quarantine, dir_fd=prefix_fd)
        except FileNotFoundError:
            # Another compliant deleter cannot run while the store lock is
            # held.  Missing here is therefore an integrity failure, not an
            # idempotent entry condition.
            raise GeneratedOutputCorrupt(
                "quarantined output binding changed before removal"
            ) from None
        os.fsync(prefix_fd)
        return True

    def _validate_handle(self, handle: OutputHandle) -> str:
        if type(handle) is not OutputHandle:
            raise GeneratedOutputInvalidHandle("delete requires an issued output handle")
        try:
            owner_binding = self._owner_binding(handle.owner_principal)
        except (TypeError, ValueError, UnicodeError):
            raise GeneratedOutputInvalidHandle("output handle is invalid") from None
        valid_shape = (
            handle._store_id == self._store_id
            and type(handle.sha256) is str
            and _DIGEST_RE.fullmatch(handle.sha256) is not None
            and type(handle.byte_size) is int
            and 0 < handle.byte_size <= self._profile.maximum_bytes
            and type(handle.width) is int
            and handle.width == self._profile.width
            and type(handle.height) is int
            and handle.height == self._profile.height
            and type(handle.storage_incarnation) is str
            and _INCARNATION_RE.fullmatch(handle.storage_incarnation) is not None
            and type(handle.mime_type) is str
            and handle.mime_type == MIME_TYPE
            and type(handle._auth_tag) is str
            and _DIGEST_RE.fullmatch(handle._auth_tag) is not None
        )
        if not valid_shape:
            raise GeneratedOutputInvalidHandle("output handle is invalid")
        try:
            provisional = _ObjectManifest(
                store_id=handle._store_id,
                owner_binding=owner_binding,
                sha256=handle.sha256,
                byte_size=handle.byte_size,
                width=handle.width,
                height=handle.height,
                storage_incarnation=handle.storage_incarnation,
            )
            expected = self._new_handle(handle.owner_principal, provisional)
        except (TypeError, ValueError, UnicodeError, AttributeError):
            raise GeneratedOutputInvalidHandle("output handle is invalid") from None
        if not hmac.compare_digest(expected._auth_tag, handle._auth_tag):
            raise GeneratedOutputInvalidHandle("output handle is invalid")
        return owner_binding

    def _remove_temporary_directory(
        self,
        parent_fd: int,
        name: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        if _TEMP_RE.fullmatch(name) is None and _DELETE_RE.fullmatch(name) is None:
            raise GeneratedOutputCorrupt("temporary output name is invalid")
        directory_fd = self._try_open_directory(parent_fd, name)
        if directory_fd is None:
            return
        try:
            opened_identity = _identity(os.fstat(directory_fd))
            if expected_identity is not None and opened_identity != expected_identity:
                raise GeneratedOutputCorrupt(
                    "temporary output directory identity changed"
                )
            entries = set(_bounded_directory_names(directory_fd, 3))
            if not entries.issubset(_OBJECT_ENTRIES):
                raise GeneratedOutputCorrupt(
                    "temporary output contains unexpected entries"
                )
            for entry in sorted(entries):
                fd = os.open(entry, _read_flags(), dir_fd=directory_fd)
                try:
                    _validate_file(
                        fd,
                        expected_uid=self._expected_uid,
                        device=self._device,
                    )
                finally:
                    os.close(fd)
                os.unlink(entry, dir_fd=directory_fd)
            os.fsync(directory_fd)
            try:
                named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise GeneratedOutputCorrupt(
                    "temporary output directory binding changed"
                ) from error
            if _identity(named) != opened_identity or _snapshot(named) != _snapshot(
                os.fstat(directory_fd)
            ):
                raise GeneratedOutputCorrupt(
                    "temporary output directory binding changed"
                )
        finally:
            os.close(directory_fd)
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)

    def _publish_prepared_locked(
        self, prepared: PreparedOutput
    ) -> PublishedOutput:
        owner_binding = self._validate_prepared(prepared)
        owner_principal = prepared.owner_principal
        data = prepared._sanitized_bytes
        digest = prepared.sha256
        layout = self._open_owner_layout(owner_binding, digest, create=True)
        assert layout is not None
        temporary_name = ".tmp-" + secrets.token_hex(16)
        temporary_fd = -1
        image_fd = -1
        manifest_fd = -1
        temporary_created = False
        try:
            existing_fd = self._try_open_directory(layout.prefix_fd, digest)
            if existing_fd is not None:
                os.close(existing_fd)
                existing_data, existing_manifest, _info = self._read_object(
                    layout.prefix_fd,
                    owner_binding=owner_binding,
                    digest=digest,
                )
                if not hmac.compare_digest(existing_data, data):
                    raise GeneratedOutputCorrupt(
                        "content-addressed output conflicts with stored bytes"
                    )
                return PublishedOutput(
                    self._new_handle(owner_principal, existing_manifest), False
                )

            os.mkdir(temporary_name, DIRECTORY_MODE, dir_fd=layout.prefix_fd)
            temporary_created = True
            temporary_fd = os.open(
                temporary_name, _directory_flags(), dir_fd=layout.prefix_fd
            )
            _validate_directory(
                temporary_fd,
                expected_uid=self._expected_uid,
                device=self._device,
            )
            incarnation = "inc_" + secrets.token_hex(16)
            manifest = _ObjectManifest(
                store_id=self._store_id,
                owner_binding=owner_binding,
                sha256=digest,
                byte_size=len(data),
                width=self._profile.width,
                height=self._profile.height,
                storage_incarnation=incarnation,
            )
            image_fd, image_info = self._write_exclusive(
                temporary_fd, "image.png", data
            )
            manifest_fd, manifest_info = self._write_exclusive(
                temporary_fd, "manifest.json", manifest.encoded()
            )
            os.fsync(temporary_fd)
            try:
                os.rename(
                    temporary_name,
                    digest,
                    src_dir_fd=layout.prefix_fd,
                    dst_dir_fd=layout.prefix_fd,
                )
                temporary_created = False
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
                # A compliant concurrent writer may have won the immutable
                # directory rename. This invocation removes only its own
                # fully-fsynced private staging directory.
                for fd in (manifest_fd, image_fd, temporary_fd):
                    if fd >= 0:
                        os.close(fd)
                manifest_fd = image_fd = temporary_fd = -1
                self._remove_temporary_directory(
                    layout.prefix_fd, temporary_name
                )
                temporary_created = False
                existing_data, existing_manifest, _info = self._read_object(
                    layout.prefix_fd,
                    owner_binding=owner_binding,
                    digest=digest,
                )
                if not hmac.compare_digest(existing_data, data):
                    raise GeneratedOutputCorrupt(
                        "content-addressed output conflicts with stored bytes"
                    )
                return PublishedOutput(
                    self._new_handle(owner_principal, existing_manifest), False
                )
            published_fd = self._try_open_directory(layout.prefix_fd, digest)
            if published_fd is None:
                raise GeneratedOutputCorrupt("atomic output publication was lost")
            try:
                published = os.fstat(published_fd)
                staged = os.fstat(temporary_fd)
                if _identity(published) != _identity(staged):
                    raise GeneratedOutputCorrupt(
                        "atomic output publication identity changed"
                    )
                named_image = os.stat(
                    "image.png", dir_fd=published_fd, follow_symlinks=False
                )
                named_manifest = os.stat(
                    "manifest.json", dir_fd=published_fd, follow_symlinks=False
                )
                if (
                    _snapshot(named_image) != _snapshot(image_info)
                    or _snapshot(named_manifest) != _snapshot(manifest_info)
                ):
                    raise GeneratedOutputCorrupt(
                        "atomic output child identity changed"
                    )
            finally:
                os.close(published_fd)
            os.fsync(layout.prefix_fd)
            return PublishedOutput(self._new_handle(owner_principal, manifest), True)
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "private output could not be published atomically"
            ) from error
        finally:
            for fd in (manifest_fd, image_fd, temporary_fd):
                if fd >= 0:
                    os.close(fd)
            if temporary_created:
                self._remove_temporary_directory(
                    layout.prefix_fd, temporary_name
                )
            layout.close()

    def _session_publish_prepared(
        self,
        session: PublicationSession,
        prepared: PreparedOutput,
    ) -> PublishedOutput:
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            return self._publish_prepared_locked(prepared)

    def _session_validate_prepared(
        self,
        session: PublicationSession,
        prepared: PreparedOutput,
    ) -> None:
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            self._validate_prepared(prepared)

    def publish_png(self, owner_principal: str, png_bytes: bytes) -> PublishedOutput:
        """Sanitize once and atomically publish into the owner-private CAS."""

        prepared = self.prepare_png(owner_principal, png_bytes)
        with self.publication_session() as session:
            return session.publish_prepared(prepared)

    def _exact_lookup_fields(
        self, owner_principal: str, sha256: str, byte_size: int
    ) -> str:
        owner_binding = self._owner_binding(owner_principal)
        if type(sha256) is not str or _DIGEST_RE.fullmatch(sha256) is None:
            raise ValueError("sha256 is invalid")
        if (
            type(byte_size) is not int
            or byte_size <= 0
            or byte_size > self._profile.maximum_bytes
        ):
            raise ValueError("byte size is invalid")
        return owner_binding

    def _probe_live_exact_locked(
        self, owner_principal: str, sha256: str, byte_size: int
    ) -> OutputHandle | None:
        owner_binding = self._exact_lookup_fields(
            owner_principal, sha256, byte_size
        )
        layout = self._open_owner_layout(owner_binding, sha256, create=False)
        if layout is None:
            return None
        try:
            try:
                _data, manifest, _info = self._read_object(
                    layout.prefix_fd,
                    owner_binding=owner_binding,
                    digest=sha256,
                )
            except GeneratedOutputNotFound:
                return None
            if manifest.byte_size != byte_size:
                raise GeneratedOutputNotFound("generated output is unavailable")
            return self._new_handle(owner_principal, manifest)
        finally:
            layout.close()

    def _session_probe_live_exact(
        self,
        session: PublicationSession,
        owner_principal: str,
        sha256: str,
        byte_size: int,
    ) -> OutputHandle | None:
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            return self._probe_live_exact_locked(
                owner_principal, sha256, byte_size
            )

    def _read_exact_locked(
        self, owner_principal: str, sha256: str, byte_size: int
    ) -> bytes:
        owner_binding = self._exact_lookup_fields(
            owner_principal, sha256, byte_size
        )
        layout = self._open_owner_layout(owner_binding, sha256, create=False)
        if layout is None:
            raise GeneratedOutputNotFound("generated output is unavailable")
        try:
            data, manifest, _info = self._read_object(
                layout.prefix_fd,
                owner_binding=owner_binding,
                digest=sha256,
            )
            if manifest.byte_size != byte_size:
                raise GeneratedOutputNotFound("generated output is unavailable")
            return data
        finally:
            layout.close()

    def read_exact(self, owner_principal: str, sha256: str, byte_size: int) -> bytes:
        """Read an owner/digest/size tuple for low-level compatibility only.

        This tuple cannot distinguish deletion followed by recreation of the
        same content-addressed bytes.  A coordinator holding persisted job
        metadata must use :meth:`read_exact_incarnation` instead.
        """

        with self._locked():
            return self._read_exact_locked(
                owner_principal, sha256, byte_size
            )

    def _read_exact_incarnation_locked(
        self,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> bytes:
        owner_binding = self._exact_lookup_fields(
            owner_principal, sha256, byte_size
        )
        if (
            type(storage_incarnation) is not str
            or _INCARNATION_RE.fullmatch(storage_incarnation) is None
        ):
            raise ValueError("storage incarnation is invalid")
        layout = self._open_owner_layout(owner_binding, sha256, create=False)
        if layout is None:
            raise GeneratedOutputNotFound("generated output is unavailable")
        try:
            data, manifest, _info = self._read_object(
                layout.prefix_fd,
                owner_binding=owner_binding,
                digest=sha256,
            )
            if manifest.byte_size != byte_size:
                raise GeneratedOutputNotFound(
                    "generated output is unavailable"
                )
            if not hmac.compare_digest(
                manifest.storage_incarnation, storage_incarnation
            ):
                raise GeneratedOutputStaleHandle(
                    "output metadata names an older object incarnation"
                )
            return data
        finally:
            layout.close()

    def _session_read_exact_incarnation(
        self,
        session: PublicationSession,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> bytes:
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            return self._read_exact_incarnation_locked(
                owner_principal,
                sha256,
                byte_size,
                storage_incarnation,
            )

    def read_exact_incarnation(
        self,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> bytes:
        """Read one exact live storage incarnation without exposing a path.

        A live object with the same owner, digest, and size but a different
        incarnation is reported as stale rather than being returned.  This is
        the coordinator-safe read seam for persisted output metadata.
        """

        with self._locked():
            return self._read_exact_incarnation_locked(
                owner_principal,
                sha256,
                byte_size,
                storage_incarnation,
            )

    @contextmanager
    def open_exact(
        self, owner_principal: str, sha256: str, byte_size: int
    ) -> Iterator[BinaryIO]:
        """Yield a legacy tuple-bound snapshot, never a filesystem pathname.

        Coordinators must prefer :meth:`read_exact_incarnation` so an ABA
        recreation cannot be mistaken for the retained job output.
        """

        data = self.read_exact(owner_principal, sha256, byte_size)
        stream = io.BytesIO(data)
        try:
            yield stream
        finally:
            stream.close()

    def _recover_delete_handle_exact_locked(
        self,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> OutputHandle | None:
        """Reissue a storage handle after restart from exact retained metadata.

        Recovery requires the exact durable storage incarnation, verifies
        either the live CAS manifest or its incarnation-bound deterministic
        in-progress quarantine, and only then reissues a handle.  An empty
        quarantine is a valid post-unlink crash state; its unguessable HMAC
        name is bound to the expected owner, digest, and incarnation.
        It proves storage identity only.  It does *not* prove that a job ACK is
        durable or that no other job references the same owner/digest CAS
        object.  A future coordinator must establish those facts from the
        owner-private job store before calling this unrouted method.  Because
        the current delete-pending tombstone does not retain ``output_size``,
        that coordinator must join the same owned job metadata to obtain the
        exact byte count; the tombstone alone is insufficient.
        """

        owner_binding = self._exact_lookup_fields(
            owner_principal, sha256, byte_size
        )
        if (
            type(storage_incarnation) is not str
            or _INCARNATION_RE.fullmatch(storage_incarnation) is None
        ):
            raise ValueError("storage incarnation is invalid")
        expected = _ObjectManifest(
            store_id=self._store_id,
            owner_binding=owner_binding,
            sha256=sha256,
            byte_size=byte_size,
            width=self._profile.width,
            height=self._profile.height,
            storage_incarnation=storage_incarnation,
        )
        layout = self._open_owner_layout(
            owner_binding, sha256, create=False
        )
        if layout is None:
            return None
        try:
            quarantine = self._quarantine_name(
                owner_binding, sha256, storage_incarnation
            )
            quarantined = self._open_exact_quarantine(
                layout.prefix_fd,
                quarantine=quarantine,
                expected=expected,
            )
            if quarantined is not None:
                os.close(quarantined[0])
                return self._new_handle(owner_principal, expected)
            try:
                _data, manifest, _info = self._read_object(
                    layout.prefix_fd,
                    owner_binding=owner_binding,
                    digest=sha256,
                )
            except GeneratedOutputNotFound:
                # A prior delete may have completed rmdir but crashed before
                # its parent fsync or durable DB finalization.
                return None
            if manifest.byte_size != byte_size:
                raise GeneratedOutputNotFound(
                    "generated output is unavailable"
                )
            if manifest.storage_incarnation != storage_incarnation:
                raise GeneratedOutputStaleHandle(
                    "retained storage incarnation does not match live output"
                )
            return self._new_handle(owner_principal, manifest)
        finally:
            layout.close()

    def _session_recover_delete_handle_exact(
        self,
        session: PublicationSession,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> OutputHandle | None:
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            return self._recover_delete_handle_exact_locked(
                owner_principal,
                sha256,
                byte_size,
                storage_incarnation,
            )

    def recover_delete_handle_exact(
        self,
        owner_principal: str,
        sha256: str,
        byte_size: int,
        storage_incarnation: str,
    ) -> OutputHandle | None:
        """Reissue a handle after exact live/quarantine verification.

        Recovery proves storage identity only. It does *not* prove that a job
        ACK is durable or that no other job references the same owner/digest
        CAS object. A coordinator must establish those facts in its job store.
        The delete-pending tombstone does not retain ``output_size``.
        """

        with self._locked():
            return self._recover_delete_handle_exact_locked(
                owner_principal,
                sha256,
                byte_size,
                storage_incarnation,
            )

    def _delete_locked(self, handle: OutputHandle) -> bool:
        owner_binding = self._validate_handle(handle)
        layout = self._open_owner_layout(
            owner_binding, handle.sha256, create=False
        )
        if layout is None:
            return False
        try:
            expected = self._manifest_for_handle(owner_binding, handle)
            quarantine = self._quarantine_name(
                owner_binding,
                handle.sha256,
                handle.storage_incarnation,
            )
            if self._cleanup_exact_quarantine(
                layout.prefix_fd,
                quarantine=quarantine,
                expected=expected,
            ):
                return True
            object_fd = self._try_open_directory(
                layout.prefix_fd, handle.sha256
            )
            if object_fd is None:
                return False
            os.close(object_fd)
            data, manifest, before = self._read_object(
                layout.prefix_fd,
                owner_binding=owner_binding,
                digest=handle.sha256,
            )
            if manifest != expected:
                raise GeneratedOutputStaleHandle(
                    "output handle names an older object incarnation"
                )
            if not hmac.compare_digest(
                hashlib.sha256(data).hexdigest(), handle.sha256
            ):
                raise GeneratedOutputCorrupt("private output digest changed")
            os.rename(
                handle.sha256,
                quarantine,
                src_dir_fd=layout.prefix_fd,
                dst_dir_fd=layout.prefix_fd,
            )
            moved_fd = self._try_open_directory(layout.prefix_fd, quarantine)
            if moved_fd is None:
                raise GeneratedOutputCorrupt("output quarantine binding was lost")
            try:
                moved = os.fstat(moved_fd)
                if _snapshot(moved) != _snapshot(before):
                    raise GeneratedOutputCorrupt(
                        "output changed during atomic deletion"
                    )
            finally:
                os.close(moved_fd)
            os.fsync(layout.prefix_fd)
            if not self._cleanup_exact_quarantine(
                layout.prefix_fd,
                quarantine=quarantine,
                expected=expected,
            ):
                raise GeneratedOutputCorrupt(
                    "output quarantine disappeared before cleanup"
                )
            return True
        except OSError as error:
            raise GeneratedOutputCorrupt(
                "private output could not be deleted atomically"
            ) from error
        finally:
            layout.close()

    def _session_delete(
        self, session: PublicationSession, handle: OutputHandle
    ) -> bool:
        with _FORK_DESCRIPTOR_GUARD:
            self._validate_publication_session(session)
            return self._delete_locked(handle)

    def delete(self, handle: OutputHandle) -> bool:
        """Delete exactly one issued incarnation; valid repeats are idempotent."""

        with self._locked():
            return self._delete_locked(handle)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


__all__ = [
    "DEFAULT_MANAGED_ANCESTOR",
    "DEFAULT_PRIVATE_OUTPUT_ROOT",
    "DIRECTORY_MODE",
    "FILE_MODE",
    "GeneratedOutputConfigurationError",
    "GeneratedOutputCorrupt",
    "GeneratedOutputInvalid",
    "GeneratedOutputInvalidHandle",
    "GeneratedOutputNotFound",
    "GeneratedOutputStaleHandle",
    "GeneratedOutputStore",
    "GeneratedOutputStoreError",
    "MAX_OUTPUT_BYTES",
    "MIME_TYPE",
    "OutputHandle",
    "PngProfile",
    "PreparedOutput",
    "PublicationSession",
    "PublishedOutput",
]
