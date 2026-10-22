"""Credential-reference based provider configuration.

Only references are serializable.  Resolved values are short-lived redacted
objects and are never fields on configuration dataclasses.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FORBIDDEN_INLINE_KEYS = frozenset({
    "api_key",
    "apikey",
    "token",
    "access_token",
    "secret",
    "secret_value",
})


class SecretKind(str, Enum):
    ENV = "env"
    FILE = "file"


def is_canonical_file_secret_locator(value: object) -> bool:
    """Return whether ``value`` names one exact, non-aliased POSIX path.

    File references are durable account identity, not user-facing filenames.
    Requiring the caller to supply the real absolute spelling prevents two
    accounts from bypassing a database uniqueness constraint with ``..`` or a
    alternate lexical spelling.  The target and its parent may be absent while
    the write-only vault is reserving it.  Symlink, type, ownership and
    hard-link checks happen at component-by-component safe open time.
    """

    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        return False
    path = Path(value)
    if not path.is_absolute() or not value.startswith("/") or value.startswith("//"):
        return False
    try:
        return os.path.normpath(value) == value
    except (OSError, RuntimeError, ValueError):
        return False


def is_safe_durable_file_secret_locator(value: object) -> bool:
    """Audit a durable locator against aliases visible at validation time."""

    if not is_canonical_file_secret_locator(value):
        return False
    path = Path(value)
    try:
        for component in (path, *path.parents):
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                return False
            if component == path:
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_size > 16 * 1024
                ):
                    return False
            elif not stat.S_ISDIR(info.st_mode):
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _open_file_secret(locator: str) -> int:
    """Open one absolute file without ever traversing a symlink component."""

    parts = Path(locator).parts
    if len(parts) < 2 or parts[0] != os.path.sep:
        raise PermissionError("secret file path is invalid")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory_fd = os.open(os.path.sep, directory_flags)
    except OSError as error:
        raise PermissionError("secret file path could not be opened safely") from error
    try:
        for component in parts[1:-1]:
            try:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            except OSError as error:
                raise PermissionError(
                    "secret file path contains a symlink or non-directory"
                ) from error
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            return os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except OSError as error:
            raise PermissionError("secret file must be a regular non-symlink file") from error
    finally:
        os.close(directory_fd)


class SecretValue:
    """An explicitly revealable value whose normal representations are redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        value = str(value).strip()
        if not value:
            raise ValueError("secret resolved to an empty value")
        if len(value.encode("utf-8")) > 16 * 1024 or "\x00" in value:
            raise ValueError("secret value is invalid")
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "<SecretValue [REDACTED]>"

    def __str__(self) -> str:
        return "[REDACTED]"


@dataclass(frozen=True, slots=True)
class SecretReference:
    kind: SecretKind
    locator: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SecretKind):
            object.__setattr__(self, "kind", SecretKind(self.kind))
        locator = str(self.locator or "").strip()
        if self.kind is SecretKind.ENV:
            if not _ENV_NAME_RE.fullmatch(locator):
                raise ValueError("environment secret locator must be an uppercase variable name")
        else:
            if not is_canonical_file_secret_locator(locator):
                raise ValueError("file secret locator must be one canonical absolute path")
        object.__setattr__(self, "locator", locator)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SecretReference:
        allowed = {"kind", "locator"}
        extras = set(value) - allowed
        if extras:
            raise ValueError(f"unknown secret reference keys: {', '.join(sorted(extras))}")
        return cls(kind=SecretKind(str(value.get("kind", ""))), locator=str(value.get("locator", "")))

    def resolve(self, environ: Mapping[str, str] | None = None) -> SecretValue:
        if self.kind is SecretKind.ENV:
            source = os.environ if environ is None else environ
            if self.locator not in source:
                raise KeyError(f"required secret environment variable is not set: {self.locator}")
            return SecretValue(source[self.locator])

        fd = _open_file_secret(self.locator)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise PermissionError("secret file must be a regular non-symlink file")
            if info.st_nlink != 1:
                raise PermissionError("secret file must not have additional hard links")
            if info.st_uid != os.geteuid():
                raise PermissionError("secret file must be owned by the service user")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise PermissionError("secret file must not be group/world accessible")
            if info.st_size > 16 * 1024:
                raise PermissionError("secret file is unexpectedly large")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                value = handle.read(16 * 1024 + 1)
        finally:
            if fd >= 0:
                os.close(fd)
        return SecretValue(value)

    def to_public_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "locator": self.locator}


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_id: str
    api_base: str
    api_key_ref: SecretReference
    enabled: bool = False
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id or "").strip().lower()
        if not _PROVIDER_RE.fullmatch(provider_id):
            raise ValueError("provider_id is invalid")
        object.__setattr__(self, "provider_id", provider_id)
        api_base = str(self.api_base or "").strip().rstrip("/")
        parsed = urlsplit(api_base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("api_base must be an HTTPS URL without userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("api_base cannot contain query or fragment components")
        object.__setattr__(self, "api_base", api_base)
        if not isinstance(self.api_key_ref, SecretReference):
            raise ValueError("api_key_ref must be a SecretReference")
        if not 1 <= float(self.timeout_seconds) <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ProviderConfig:
        lowered = {str(key).lower() for key in value}
        forbidden = lowered & _FORBIDDEN_INLINE_KEYS
        if forbidden:
            raise ValueError("inline credentials are forbidden; use api_key_ref")
        allowed = {"provider_id", "api_base", "api_key_ref", "enabled", "timeout_seconds"}
        extras = set(value) - allowed
        if extras:
            raise ValueError(f"unknown provider config keys: {', '.join(sorted(extras))}")
        reference = value.get("api_key_ref")
        if not isinstance(reference, Mapping):
            raise ValueError("api_key_ref must be an object")
        enabled = value.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        return cls(
            provider_id=str(value.get("provider_id", "")),
            api_base=str(value.get("api_base", "")),
            api_key_ref=SecretReference.from_mapping(reference),
            enabled=enabled,
            timeout_seconds=float(value.get("timeout_seconds", 30.0)),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "api_base": self.api_base,
            "api_key_ref": self.api_key_ref.to_public_dict(),
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
        }
