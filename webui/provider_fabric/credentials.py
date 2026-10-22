"""Write-only credential provisioning for Castle provider accounts.

Secret bytes are accepted only long enough to validate and atomically persist
them.  They are never retained on an object, returned to a caller, read back by
this module, or included in an exception.  Runtime consumers receive the
existing :class:`provider_fabric.config.SecretReference` file contract.

The implementation is intentionally POSIX-only.  Castle runs as a Linux
systemd service, and the guarantees used here (``openat``, ``O_NOFOLLOW``,
``flock``, file and directory ``fsync``) should not be silently weakened on a
different platform.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets as secure_random
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .config import SecretKind, SecretReference


_STABLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_AUTOIP_KEY_RE = re.compile(rb"aip_[A-Za-z0-9][A-Za-z0-9._-]{15,507}\Z")
_MAX_SECRET_BYTES = 16 * 1024
_SECRET_FILE_SUFFIX = ".secret"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class CredentialVaultError(RuntimeError):
    """Base class for credential-vault failures."""


class CredentialPathError(CredentialVaultError):
    """A credential path is unsafe or has unsafe ownership/permissions."""


class CredentialAlreadyConfigured(CredentialVaultError):
    """Provisioning tried to replace an existing credential."""


class CredentialNotConfigured(CredentialVaultError):
    """Rotation requires a credential that does not yet exist."""


@dataclass(frozen=True, slots=True)
class CredentialWriteResult:
    """Public, secret-free result of a write-only vault operation."""

    reference: SecretReference
    fingerprint: str | None
    configured: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            # The adapter/store receives ``reference`` directly.  Browser/API
            # serialization does not need the host filesystem locator.
            "authentication": {
                "kind": self.reference.kind.value,
                "configured": self.configured,
            },
            "fingerprint": self.fingerprint,
            "configured": self.configured,
        }


def default_credential_root() -> Path:
    """Return the fixed operator-only credential root without creating it."""

    return Path(os.environ.get("CASTLE_PROVIDER_CREDENTIAL_ROOT", Path.home() / ".local" / "share" / "web-comfyui" / "provider-secrets")).expanduser()


def _stable_id(value: str, label: str) -> str:
    # Do not strip or lowercase.  IDs name on-disk security boundaries, so the
    # caller must supply the exact canonical value used by the provider store.
    if not isinstance(value, str) or not _STABLE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be an exact lowercase stable identifier")
    return value


def _path_parts(path: Path) -> tuple[str, ...]:
    if not path.is_absolute():
        raise CredentialPathError("credential root must be an absolute path")
    if "\x00" in os.fspath(path):
        raise CredentialPathError("credential root path is invalid")
    parts = path.parts
    if not parts or parts[0] != os.path.sep:
        raise CredentialPathError("credential root must be an absolute POSIX path")
    names = tuple(parts[1:])
    if not names or any(name in {"", ".", ".."} for name in names):
        raise CredentialPathError("credential root path is invalid")
    return names


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _validate_directory(
    fd: int,
    *,
    label: str,
    require_private_mode: bool,
) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise CredentialPathError(f"{label} must be a directory")
    if require_private_mode:
        if info.st_uid != os.geteuid():
            raise CredentialPathError(f"{label} must be owned by the service user")
        if stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
            raise CredentialPathError(f"{label} must have mode 0700")


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise CredentialPathError("credential directory could not be created safely") from error
    try:
        child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as error:
        raise CredentialPathError("credential path contains a symlink or non-directory") from error
    try:
        _validate_directory(child_fd, label="credential directory", require_private_mode=True)
    except BaseException:
        os.close(child_fd)
        raise
    return child_fd


def _validate_secret_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise CredentialPathError("credential target must be a regular non-symlink file")
    if info.st_nlink != 1:
        raise CredentialPathError("credential target must not have additional hard links")
    if info.st_uid != os.geteuid():
        raise CredentialPathError("credential target must be owned by the service user")
    if stat.S_IMODE(info.st_mode) != _FILE_MODE:
        raise CredentialPathError("credential target must have mode 0600")


def _target_info(directory_fd: int, filename: str) -> os.stat_result | None:
    try:
        info = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CredentialPathError("credential target could not be inspected safely") from error
    _validate_secret_file(info)
    return info


def _normalize_secret(
    provider_id: str,
    credential_id: str,
    value: str | bytes,
) -> bytearray:
    encoded: bytes | None = None
    try:
        if isinstance(value, str):
            try:
                encoded = value.encode("utf-8", "strict")
            except UnicodeError:
                # Raise after leaving the handler so the public ValueError does
                # not retain a UnicodeError containing the supplied value.
                encoded = None
        elif isinstance(value, bytes):
            encoded = value
            valid_utf8 = True
            try:
                encoded.decode("utf-8", "strict")
            except UnicodeError:
                valid_utf8 = False
            if not valid_utf8:
                encoded = None
        else:
            raise TypeError("credential value must be str or bytes")

        if encoded is None:
            raise ValueError("credential value is invalid")
        if not 16 <= len(encoded) <= _MAX_SECRET_BYTES:
            raise ValueError("credential value is invalid")
        if b"\x00" in encoded or b"\r" in encoded or b"\n" in encoded:
            raise ValueError("credential value is invalid")
        if encoded != encoded.strip():
            raise ValueError("credential value is invalid")
        if any(byte < 0x20 or byte == 0x7F for byte in encoded):
            raise ValueError("credential value is invalid")

        if provider_id == "autoip" and credential_id == "api-key":
            if _AUTOIP_KEY_RE.fullmatch(encoded) is None:
                raise ValueError("credential does not match the AutoIP API-key format")

        return bytearray(encoded)
    finally:
        # Traceback-local inspection must not recover the caller's value.
        value = b""
        encoded = None


def _fingerprint(value: bytearray) -> str:
    # A truncated one-way digest is sufficient for operators to identify a
    # high-entropy credential version without returning a usable value.
    return f"sha256:{hashlib.sha256(value).hexdigest()[:16]}"


def _zero(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


def _write_all(fd: int, value: bytearray) -> None:
    view = memoryview(value)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise OSError("credential write did not make progress")
        written += count


class ProviderCredentialVault:
    """Provision provider credentials without offering a read API."""

    __slots__ = ("_root",)

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        selected = default_credential_root() if root is None else Path(root)
        # Preserve path identity: resolve() would follow exactly the symlinks we
        # need to reject.  Custom roots must already be absolute.
        self._root = selected
        with self._open_root(create=True):
            pass

    @property
    def root(self) -> Path:
        return self._root

    def reference_for(
        self,
        provider_id: str,
        account_id: str,
        *,
        credential_id: str = "api-key",
    ) -> SecretReference:
        """Return the opaque final locator without reading or writing a secret.

        Application services use this to reserve an actor-owned account in
        their durable store before the first credential byte reaches the
        vault.  The subsequent write returns this exact reference.
        """

        provider_id = _stable_id(provider_id, "provider_id")
        account_id = _stable_id(account_id, "account_id")
        credential_id = _stable_id(credential_id, "credential_id")
        return self._reference(provider_id, account_id, credential_id)

    @contextmanager
    def _open_root(self, *, create: bool) -> Iterator[int]:
        names = _path_parts(self._root)
        try:
            current_fd = os.open(os.path.sep, _directory_flags())
        except OSError as error:
            raise CredentialPathError("filesystem root could not be opened safely") from error
        try:
            _validate_directory(
                current_fd,
                label="filesystem root",
                require_private_mode=False,
            )
            for index, name in enumerate(names):
                is_final = index == len(names) - 1
                if create:
                    try:
                        os.mkdir(name, _DIRECTORY_MODE, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    except OSError as error:
                        raise CredentialPathError(
                            "credential root could not be created safely"
                        ) from error
                try:
                    next_fd = os.open(name, _directory_flags(), dir_fd=current_fd)
                except OSError as error:
                    raise CredentialPathError(
                        "credential root contains a symlink or non-directory"
                    ) from error
                os.close(current_fd)
                current_fd = next_fd
                _validate_directory(
                    current_fd,
                    label="credential root" if is_final else "credential path component",
                    # Existing ancestors such as / and /home may be root-owned
                    # and readable.  The secret root itself is strictly private.
                    require_private_mode=is_final,
                )
            yield current_fd
        finally:
            os.close(current_fd)

    @contextmanager
    def _open_account(
        self,
        provider_id: str,
        account_id: str,
    ) -> Iterator[int]:
        with self._open_root(create=False) as root_fd:
            provider_fd = _open_directory_at(root_fd, provider_id, create=True)
            try:
                account_fd = _open_directory_at(provider_fd, account_id, create=True)
            finally:
                os.close(provider_fd)
            try:
                yield account_fd
            finally:
                os.close(account_fd)

    @contextmanager
    def _locked_account(self, account_fd: int, credential_id: str) -> Iterator[None]:
        lock_name = f".{credential_id}.lock"
        # O_NONBLOCK prevents a hostile pre-existing FIFO from blocking before
        # fstat can reject it as a non-regular file.
        base_flags = os.O_RDWR | getattr(os, "O_NONBLOCK", 0) | _file_flags()
        created = False
        try:
            lock_fd = os.open(lock_name, base_flags, dir_fd=account_fd)
        except FileNotFoundError:
            try:
                lock_fd = os.open(
                    lock_name,
                    base_flags | os.O_CREAT | os.O_EXCL,
                    _FILE_MODE,
                    dir_fd=account_fd,
                )
                created = True
            except FileExistsError:
                try:
                    lock_fd = os.open(lock_name, base_flags, dir_fd=account_fd)
                except OSError as error:
                    raise CredentialPathError(
                        "credential lock could not be opened safely"
                    ) from error
            except OSError as error:
                raise CredentialPathError("credential lock could not be created safely") from error
        except OSError as error:
            raise CredentialPathError("credential lock could not be opened safely") from error
        try:
            if created:
                os.fchmod(lock_fd, _FILE_MODE)
            _validate_secret_file(os.fstat(lock_fd))
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def _reference(
        self,
        provider_id: str,
        account_id: str,
        credential_id: str,
    ) -> SecretReference:
        path = (
            self._root
            / provider_id
            / account_id
            / f"{credential_id}{_SECRET_FILE_SUFFIX}"
        )
        return SecretReference(kind=SecretKind.FILE, locator=os.fspath(path))

    def _write(
        self,
        provider_id: str,
        account_id: str,
        credential_id: str,
        value: str | bytes,
        *,
        require_existing: bool,
    ) -> CredentialWriteResult:
        provider_id = _stable_id(provider_id, "provider_id")
        account_id = _stable_id(account_id, "account_id")
        credential_id = _stable_id(credential_id, "credential_id")
        try:
            secret_buffer = _normalize_secret(provider_id, credential_id, value)
        finally:
            value = b""
        result_fingerprint = _fingerprint(secret_buffer)
        reference = self._reference(provider_id, account_id, credential_id)
        try:
            with self._open_account(provider_id, account_id) as account_fd:
                with self._locked_account(account_fd, credential_id):
                    target_name = f"{credential_id}{_SECRET_FILE_SUFFIX}"
                    existing = _target_info(account_fd, target_name)
                    if require_existing and existing is None:
                        raise CredentialNotConfigured("credential is not configured")
                    if not require_existing and existing is not None:
                        raise CredentialAlreadyConfigured("credential is already configured")

                    temp_name: str | None = None
                    temp_fd = -1
                    try:
                        for _ in range(128):
                            candidate = (
                                f".{credential_id}.{os.getpid()}."
                                f"{secure_random.token_hex(12)}.tmp"
                            )
                            try:
                                temp_fd = os.open(
                                    candidate,
                                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _file_flags(),
                                    _FILE_MODE,
                                    dir_fd=account_fd,
                                )
                            except FileExistsError:
                                continue
                            except OSError as error:
                                raise CredentialPathError(
                                    "credential staging file could not be created safely"
                                ) from error
                            temp_name = candidate
                            break
                        if temp_fd < 0 or temp_name is None:
                            raise CredentialVaultError("credential staging name could not be allocated")

                        os.fchmod(temp_fd, _FILE_MODE)
                        _validate_secret_file(os.fstat(temp_fd))
                        _write_all(temp_fd, secret_buffer)
                        os.fsync(temp_fd)
                        os.close(temp_fd)
                        temp_fd = -1

                        # os.replace with directory descriptors maps to the
                        # renameat family and keeps all resolution beneath the
                        # already-validated account directory.
                        os.replace(
                            temp_name,
                            target_name,
                            src_dir_fd=account_fd,
                            dst_dir_fd=account_fd,
                        )
                        temp_name = None
                        _target_info(account_fd, target_name)
                        os.fsync(account_fd)
                    finally:
                        if temp_fd >= 0:
                            os.close(temp_fd)
                        if temp_name is not None:
                            try:
                                os.unlink(temp_name, dir_fd=account_fd)
                            except FileNotFoundError:
                                pass
            return CredentialWriteResult(
                reference=reference,
                fingerprint=result_fingerprint,
                configured=True,
            )
        finally:
            _zero(secret_buffer)

    def provision(
        self,
        provider_id: str,
        account_id: str,
        value: str | bytes,
        *,
        credential_id: str = "api-key",
    ) -> CredentialWriteResult:
        """Create a credential; refuse to overwrite an existing version."""

        try:
            return self._write(
                provider_id,
                account_id,
                credential_id,
                value,
                require_existing=False,
            )
        finally:
            value = b""

    def rotate(
        self,
        provider_id: str,
        account_id: str,
        value: str | bytes,
        *,
        credential_id: str = "api-key",
    ) -> CredentialWriteResult:
        """Atomically replace an existing credential without reading it back."""

        try:
            return self._write(
                provider_id,
                account_id,
                credential_id,
                value,
                require_existing=True,
            )
        finally:
            value = b""

    def delete(
        self,
        provider_id: str,
        account_id: str,
        *,
        credential_id: str = "api-key",
        confirm: bool = False,
    ) -> CredentialWriteResult:
        """Explicitly remove one credential without reading its contents."""

        provider_id = _stable_id(provider_id, "provider_id")
        account_id = _stable_id(account_id, "account_id")
        credential_id = _stable_id(credential_id, "credential_id")
        if confirm is not True:
            raise CredentialVaultError("credential deletion requires explicit confirmation")
        reference = self._reference(provider_id, account_id, credential_id)
        with self._open_account(provider_id, account_id) as account_fd:
            with self._locked_account(account_fd, credential_id):
                target_name = f"{credential_id}{_SECRET_FILE_SUFFIX}"
                if _target_info(account_fd, target_name) is not None:
                    try:
                        os.unlink(target_name, dir_fd=account_fd)
                    except OSError as error:
                        raise CredentialVaultError("credential could not be deleted safely") from error
                    os.fsync(account_fd)
        return CredentialWriteResult(
            reference=reference,
            fingerprint=None,
            configured=False,
        )


def result_to_public_mapping(result: CredentialWriteResult) -> Mapping[str, Any]:
    """Narrow serialization helper for API layers."""

    if not isinstance(result, CredentialWriteResult):
        raise TypeError("result must be a CredentialWriteResult")
    return result.to_public_dict()
