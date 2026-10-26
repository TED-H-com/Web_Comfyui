"""Offline bootstrap boundary for Castle's provider fabric.

The live HTTP server intentionally does not own provider setup details.  This
module builds the framework-neutral service graph without probing ComfyUI or
contacting a vendor.  It owns separate persistent keys for plan signatures and
stable, non-PII actor identifiers.  Both keys are created atomically in an
operator-private directory and are never returned by any public runtime
response.
"""

from __future__ import annotations

if '.' in (__package__ or ''):
    from .. import file_lock as fcntl
else:  # top-level webui package layout
    import file_lock as fcntl
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .autoip import AutoIPClient, normalize_autoip_webhook_url
from .autoip_reconciliation import ReconciliationResult, run_once as reconcile_autoip_once
from .config import ProviderConfig, SecretReference, SecretValue
from .autoip_catalog_http import AutoIPCatalogHttpApi
from .autoip_catalog_sync import AutoIPCatalogSnapshotStore
from .autoip_probe_http import AutoIPProbeHttpApi
from .autoip_golden_store import AutoIPGoldenEvidenceStore
from .autoip_golden_http import AutoIPGoldenHttpApi
from .autoip_profiles import candidate_autoip_profiles, qualified_autoip_profiles
from .autoip_quote import AutoIPQuoteSource
from .autoip_readiness import (
    AutoIPCompatibilityReceiptStore,
    credential_reference_fingerprint,
)
from .credentials import ProviderCredentialVault, default_credential_root
from .http_api import ProviderFabricHttpApi
from .import_storage import PrivateImportStageStore
from .media_materializer import PrivateProviderMediaStore
from .media_preview_http import ProviderMediaPreviewHttpApi
from .owner_import_http import OwnerImportHttpApi
from .owner_import_token import OwnerImportDecisionTokenCodec
from .service import (
    ActorContext,
    CapabilityMaterializer,
    CanonicalResourceResolver,
    H3AuditSource,
    OptionValidator,
    ProviderFabricService,
)
from .store import ProviderFabricStore, StoreConflictError, default_database_path


_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_MAX_KEY_BYTES = 1024
_KEY_RE = re.compile(rb"[A-Za-z0-9_-]{43,1024}\Z")
_ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_ROLE_MAP = {
    "admin": "admin",
    "administrator": "admin",
    "owner": "admin",
    "operator": "operator",
    "editor": "operator",
    "staff": "operator",
    "member": "member",
    "user": "member",
    "viewer": "member",
}


class ProviderRuntimeError(RuntimeError):
    """Base class for fail-closed runtime bootstrap failures."""


class PersistentKeyPathError(ProviderRuntimeError):
    """A persistent-key path or an object at that path is unsafe."""


class PlanSigningKeyPathError(PersistentKeyPathError):
    """The plan-signing-key path or an object at that path is unsafe."""


class IdentityKeyPathError(PersistentKeyPathError):
    """The actor-identity-key path or an object at that path is unsafe."""


class RuntimeConfigurationError(ProviderRuntimeError):
    """Runtime configuration is invalid or conflicts with a singleton."""


def _ensure_clipmode_import_namespace(
    store: ProviderFabricStore,
    account_id: str,
    display_name: str,
) -> None:
    """Create the inert namespace or preserve an already owner-bound one.

    Owner binding is an irreversible ACL operation.  Runtime bootstrap must
    never rewrite it, but it must also remain restart-safe after an operator
    has bound the empty import namespace for the owner-scoped preview path.
    """

    try:
        store.register_manual_import_account(
            account_id,
            "clipmode",
            display_name,
            enabled=False,
        )
        return
    except StoreConflictError:
        try:
            account = store.get_provider_account(account_id)
        except KeyError as error:
            raise RuntimeConfigurationError(
                "ClipMode import namespace is unavailable"
            ) from error
        expected = {
            "provider_id": "clipmode",
            "api_base": None,
            "enabled": False,
            "authentication": {"kind": "none", "configured": False},
        }
        if any(account.get(key) != value for key, value in expected.items()):
            raise RuntimeConfigurationError(
                "ClipMode import namespace conflicts with runtime configuration"
            ) from None


def default_plan_signing_key_path() -> Path:
    """Return the persistent signing-key path without creating it."""

    return default_database_path().parent / "plan-signing.key"


def default_identity_key_path() -> Path:
    """Return the persistent actor-identity-key path without creating it."""

    return default_database_path().parent / "actor-identity.key"


def default_import_stage_root() -> Path:
    return default_database_path().parent / "provider-import-stages"


def default_provider_media_root() -> Path:
    return default_database_path().parent / "provider-media"


def _repository_root() -> Path:
    return Path(__file__).absolute().parents[2]


def _absolute_path(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or "\x00" in os.fspath(path):
        raise RuntimeConfigurationError(f"{label} must be an absolute path")
    normalized = Path(os.path.abspath(os.fspath(path)))
    try:
        inside_repository = os.path.commonpath(
            (os.fspath(normalized), os.fspath(_repository_root()))
        ) == os.fspath(_repository_root())
    except ValueError:
        inside_repository = False
    if inside_repository:
        raise RuntimeConfigurationError(f"{label} must remain outside the repository")
    return normalized


def _account_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ACCOUNT_RE.fullmatch(value):
        raise RuntimeConfigurationError(f"{label} must be an exact lowercase stable identifier")
    return value


@dataclass(frozen=True, slots=True)
class ProviderRuntimeConfig:
    """Explicit, non-secret provider bootstrap configuration.

    Local execution has two independent switches.  ``local_comfy_enabled`` is
    the operator's administrative choice; ``local_runtime_ready`` is a trusted
    readiness result supplied by the outer Castle process.  This module never
    turns either switch on by probing the network.
    """

    database_path: Path = field(default_factory=default_database_path)
    credential_root: Path = field(default_factory=default_credential_root)
    plan_signing_key_path: Path = field(default_factory=default_plan_signing_key_path)
    identity_key_path: Path = field(default_factory=default_identity_key_path)
    import_stage_root: Path = field(default_factory=default_import_stage_root)
    provider_media_root: Path = field(default_factory=default_provider_media_root)
    owner_import_enabled: bool = False
    local_account_id: str = "local-main"
    local_display_name: str = "Local RTX 5090"
    local_api_base: str = "http://127.0.0.1:8188"
    local_comfy_enabled: bool = False
    local_runtime_ready: bool = False
    local_version: str | None = None
    local_node_count: int | None = None
    clipmode_account_id: str = "clipmode-import"
    clipmode_display_name: str = "ClipMode manual import"
    autoip_webhook_account_id: str | None = None
    autoip_webhook_url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "database_path", _absolute_path(self.database_path, "database_path")
        )
        object.__setattr__(
            self, "credential_root", _absolute_path(self.credential_root, "credential_root")
        )
        object.__setattr__(
            self,
            "plan_signing_key_path",
            _absolute_path(self.plan_signing_key_path, "plan_signing_key_path"),
        )
        object.__setattr__(
            self,
            "identity_key_path",
            _absolute_path(self.identity_key_path, "identity_key_path"),
        )
        object.__setattr__(
            self,
            "import_stage_root",
            _absolute_path(self.import_stage_root, "import_stage_root"),
        )
        object.__setattr__(
            self,
            "provider_media_root",
            _absolute_path(self.provider_media_root, "provider_media_root"),
        )
        if self.import_stage_root == self.provider_media_root:
            raise RuntimeConfigurationError(
                "import stage and provider media roots must differ"
            )
        if self.database_path == self.plan_signing_key_path:
            raise RuntimeConfigurationError("database and signing-key paths must differ")
        if self.database_path == self.identity_key_path:
            raise RuntimeConfigurationError("database and identity-key paths must differ")
        if self.plan_signing_key_path == self.identity_key_path:
            raise RuntimeConfigurationError("signing-key and identity-key paths must differ")
        object.__setattr__(
            self, "local_account_id", _account_id(self.local_account_id, "local_account_id")
        )
        object.__setattr__(
            self,
            "clipmode_account_id",
            _account_id(self.clipmode_account_id, "clipmode_account_id"),
        )
        if self.autoip_webhook_account_id is not None:
            object.__setattr__(
                self,
                "autoip_webhook_account_id",
                _account_id(
                    self.autoip_webhook_account_id,
                    "autoip_webhook_account_id",
                ),
            )
        if self.autoip_webhook_url is not None:
            if self.autoip_webhook_account_id is None:
                raise RuntimeConfigurationError(
                    "AutoIP webhook URL requires one fixed account"
                )
            try:
                webhook_url = normalize_autoip_webhook_url(
                    self.autoip_webhook_url
                )
            except ValueError as error:
                raise RuntimeConfigurationError(
                    "AutoIP webhook URL is invalid"
                ) from error
            object.__setattr__(self, "autoip_webhook_url", webhook_url)
        if self.local_account_id == self.clipmode_account_id:
            raise RuntimeConfigurationError("provider account identifiers must differ")
        if self.autoip_webhook_account_id in {
            self.local_account_id,
            self.clipmode_account_id,
        }:
            raise RuntimeConfigurationError(
                "AutoIP webhook account must be separate from local/import namespaces"
            )
        for name in (
            "local_comfy_enabled",
            "local_runtime_ready",
            "owner_import_enabled",
        ):
            if not isinstance(getattr(self, name), bool):
                raise RuntimeConfigurationError(f"{name} must be boolean")
        for name in ("local_display_name", "clipmode_display_name"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 300
                or any(ord(character) < 32 for character in value)
            ):
                raise RuntimeConfigurationError(f"{name} is invalid")
            object.__setattr__(self, name, value.strip())
        if self.local_api_base != "http://127.0.0.1:8188":
            raise RuntimeConfigurationError(
                "local_api_base must be the deployment's exact loopback ComfyUI origin"
            )
        if self.local_version is not None and (
            not isinstance(self.local_version, str)
            or not self.local_version.strip()
            or len(self.local_version) > 80
            or any(ord(character) < 32 or ord(character) == 127 for character in self.local_version)
        ):
            raise RuntimeConfigurationError("local_version is invalid")
        if self.local_node_count is not None and (
            not isinstance(self.local_node_count, int)
            or isinstance(self.local_node_count, bool)
            or self.local_node_count < 0
        ):
            raise RuntimeConfigurationError("local_node_count is invalid")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _validate_private_directory(
    fd: int,
    *,
    key_label: str,
    error_type: type[PersistentKeyPathError],
) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise error_type(f"{key_label} parent must be a directory")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
        raise error_type(
            f"{key_label} parent must be owner-only and owned by the service user"
        )


def _validate_private_file(
    fd: int,
    *,
    label: str,
    error_type: type[PersistentKeyPathError],
) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise error_type(f"{label} must be a regular file")
    if info.st_nlink != 1:
        raise error_type(f"{label} must not have additional hard links")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _FILE_MODE:
        raise error_type(
            f"{label} must be owner-only and owned by the service user"
        )
    return info


def _open_private_parent(
    path: Path,
    *,
    key_label: str,
    error_type: type[PersistentKeyPathError],
) -> int:
    parts = path.parent.parts
    if not parts or parts[0] != os.path.sep:
        raise error_type(f"{key_label} path must be absolute")
    names = parts[1:]
    if not names or any(name in {"", ".", ".."} for name in names):
        raise error_type(f"{key_label} path is invalid")
    try:
        current_fd = os.open(os.path.sep, _directory_flags())
    except OSError as error:
        raise error_type("filesystem root could not be opened safely") from error
    try:
        for name in names:
            try:
                os.mkdir(name, _DIRECTORY_MODE, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as error:
                raise error_type(
                    f"{key_label} directory could not be created safely"
                ) from error
            try:
                next_fd = os.open(name, _directory_flags(), dir_fd=current_fd)
            except OSError as error:
                raise error_type(
                    f"{key_label} path contains a symlink or non-directory"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        _validate_private_directory(
            current_fd,
            key_label=key_label,
            error_type=error_type,
        )
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _read_persistent_key(
    parent_fd: int,
    filename: str,
    *,
    key_label: str,
    error_type: type[PersistentKeyPathError],
) -> SecretValue:
    try:
        fd = os.open(filename, os.O_RDONLY | _file_flags(), dir_fd=parent_fd)
    except OSError as error:
        raise error_type(f"{key_label} must be a regular non-symlink file") from error
    try:
        info = _validate_private_file(fd, label=key_label, error_type=error_type)
        if not 43 <= info.st_size <= _MAX_KEY_BYTES:
            raise error_type(f"{key_label} has an invalid size")
        chunks: list[bytes] = []
        remaining = _MAX_KEY_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if _KEY_RE.fullmatch(raw) is None:
            raise error_type(f"{key_label} has an invalid format")
        try:
            return SecretValue(raw.decode("ascii"))
        except (UnicodeError, ValueError) as error:
            raise error_type(f"{key_label} has an invalid format") from error
    finally:
        os.close(fd)


def _open_lock(
    parent_fd: int,
    filename: str,
    *,
    key_label: str,
    error_type: type[PersistentKeyPathError],
) -> int:
    # O_NONBLOCK makes a hostile pre-existing FIFO inspectable instead of
    # allowing bootstrap to wait on it before fstat can reject the object.
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NONBLOCK", 0) | _file_flags()
    try:
        fd = os.open(filename, flags, _FILE_MODE, dir_fd=parent_fd)
    except OSError as error:
        raise error_type(f"{key_label} lock could not be opened safely") from error
    try:
        _validate_private_file(
            fd,
            label=f"{key_label} lock",
            error_type=error_type,
        )
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _target_exists(
    parent_fd: int,
    filename: str,
    *,
    key_label: str,
    error_type: type[PersistentKeyPathError],
) -> bool:
    try:
        os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise error_type(f"{key_label} could not be inspected safely") from error
    return True


def _create_persistent_key(
    parent_fd: int,
    filename: str,
    *,
    key_label: str,
    error_type: type[PersistentKeyPathError],
) -> None:
    temporary_name = f".{filename}.{secrets.token_hex(16)}.tmp"
    fd = -1
    created = False
    raw = bytearray(secrets.token_urlsafe(48).encode("ascii"))
    try:
        try:
            fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _file_flags(),
                _FILE_MODE,
                dir_fd=parent_fd,
            )
            created = True
        except OSError as error:
            raise error_type(f"{key_label} could not be created atomically") from error
        view = memoryview(raw)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise error_type(f"{key_label} could not be persisted")
            offset += written
        os.fsync(fd)
        _validate_private_file(
            fd,
            label=f"temporary {key_label}",
            error_type=error_type,
        )
        os.close(fd)
        fd = -1
        if _target_exists(
            parent_fd,
            filename,
            key_label=key_label,
            error_type=error_type,
        ):
            raise error_type(f"{key_label} appeared during atomic creation")
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        created = False
        os.fsync(parent_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if created:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        for index in range(len(raw)):
            raw[index] = 0


def _load_or_create_persistent_key(
    path: str | os.PathLike[str],
    *,
    path_label: str,
    key_label: str,
    error_type: type[PersistentKeyPathError],
    allow_create: bool = True,
) -> SecretValue:
    """Atomically load or create one owner-only persistent key.

    Cooperative processes serialize through an owner-only lock file.  Path
    traversal uses ``openat`` plus ``O_NOFOLLOW`` so neither the parent nor the
    target can be redirected through a symlink.  Existing hard-linked or
    incorrectly-permissioned files are rejected rather than repaired.
    """

    selected = _absolute_path(Path(path).expanduser(), path_label)
    filename = selected.name
    if filename in {"", ".", ".."}:
        raise error_type(f"{key_label} filename is invalid")
    parent_fd = _open_private_parent(
        selected,
        key_label=key_label,
        error_type=error_type,
    )
    lock_fd = -1
    try:
        lock_fd = _open_lock(
            parent_fd,
            f".{filename}.lock",
            key_label=key_label,
            error_type=error_type,
        )
        exists = _target_exists(
            parent_fd,
            filename,
            key_label=key_label,
            error_type=error_type,
        )
        if not exists and not allow_create:
            raise error_type(
                f"{key_label} is missing for an owner-scoped provider database"
            )
        if not exists:
            _create_persistent_key(
                parent_fd,
                filename,
                key_label=key_label,
                error_type=error_type,
            )
        return _read_persistent_key(
            parent_fd,
            filename,
            key_label=key_label,
            error_type=error_type,
        )
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(parent_fd)


def load_or_create_plan_signing_key(
    path: str | os.PathLike[str] | None = None,
) -> SecretValue:
    """Atomically load or create the owner-only persistent plan-signing key."""

    selected = default_plan_signing_key_path() if path is None else path
    return _load_or_create_persistent_key(
        selected,
        path_label="plan_signing_key_path",
        key_label="plan signing key",
        error_type=PlanSigningKeyPathError,
    )


def load_or_create_identity_key(
    path: str | os.PathLike[str] | None = None,
    *,
    allow_create: bool = True,
) -> SecretValue:
    """Atomically load or create the stable Castle actor-identity key."""

    selected = default_identity_key_path() if path is None else path
    return _load_or_create_persistent_key(
        selected,
        path_label="identity_key_path",
        key_label="actor identity key",
        error_type=IdentityKeyPathError,
        allow_create=allow_create,
    )


def _load_identity_key_for_database(path: Path, key_path: Path) -> SecretValue:
    """Load/create the identity key while excluding concurrent owner writes.

    ``BEGIN IMMEDIATE`` holds SQLite's write reservation from the owner-state
    check through key creation.  A legacy or rolling Castle writer therefore
    cannot add an owner row under the former plan-key namespace inside the
    check/create gap.
    """

    owner_tables = (
        "provider_account_owners",
        "canonical_character_owners",
        "remote_asset_owners",
        "generation_intent_owners",
        "provider_account_entitlements",
    )
    try:
        connection = sqlite3.connect(path, timeout=30.0)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            has_owner_state = False
            for table in owner_tables:
                if table in existing and connection.execute(
                    f"SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1)"
                ).fetchone()[0]:
                    has_owner_state = True
                    break
            identity_key = load_or_create_identity_key(
                key_path,
                allow_create=not has_owner_state,
            )
            connection.commit()
            return identity_key
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as error:
        raise IdentityKeyPathError(
            "provider owner state could not be inspected safely"
        ) from error


def _normalize_castle_identity(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Castle identity must be text")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized or len(normalized.encode("utf-8")) > 1024 or "\x00" in normalized:
        raise ValueError("Castle identity is invalid")
    return normalized.encode("utf-8")


def actor_context_from_castle(
    identity: str,
    role: str,
    *,
    identity_key: SecretValue,
) -> ActorContext:
    """Map an authenticated Castle identity to a stable non-PII actor ID."""

    if not isinstance(identity_key, SecretValue):
        raise TypeError("identity_key must be a SecretValue")
    normalized_role = str(role or "").strip().casefold()
    mapped_role = _ROLE_MAP.get(normalized_role)
    if mapped_role is None:
        raise ValueError("Castle role is unsupported")
    digest = hmac.new(
        identity_key.reveal().encode("utf-8"),
        b"castle-provider-actor-v1\0" + _normalize_castle_identity(identity),
        hashlib.sha256,
    ).hexdigest()[:32]
    return ActorContext(user_id=f"castle-user:{digest}", role=mapped_role)


def _empty_option_profile(
    _intent: Any,
    _decision: Any,
    options: Mapping[str, Any],
) -> Mapping[str, Any]:
    if options:
        raise ValueError("no verified option-value profile is installed")
    return {}


class ProviderRuntime:
    """Initialized backend objects; browser-safe data comes through ``inventory``."""

    __slots__ = (
        "config",
        "store",
        "credential_vault",
        "service",
        "http_api",
        "media_preview_http",
        "autoip_catalog_http",
        "autoip_probe_http",
        "autoip_receipts",
        "autoip_quote_source",
        "autoip_goldens",
        "autoip_golden_http",
        "owner_import_http",
        "import_stage_store",
        "provider_media_store",
        "__identity_key",
    )

    def __init__(
        self,
        config: ProviderRuntimeConfig,
        store: ProviderFabricStore,
        credential_vault: ProviderCredentialVault,
        service: ProviderFabricService,
        http_api: ProviderFabricHttpApi,
        identity_key: SecretValue,
        autoip_probe_http: AutoIPProbeHttpApi,
        autoip_catalog_http: AutoIPCatalogHttpApi,
        autoip_receipts: AutoIPCompatibilityReceiptStore,
        autoip_quote_source: AutoIPQuoteSource,
        autoip_goldens: AutoIPGoldenEvidenceStore,
        autoip_golden_http: AutoIPGoldenHttpApi,
        media_preview_http: ProviderMediaPreviewHttpApi | None = None,
        owner_import_http: OwnerImportHttpApi | None = None,
        import_stage_store: PrivateImportStageStore | None = None,
        provider_media_store: PrivateProviderMediaStore | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.credential_vault = credential_vault
        self.service = service
        self.http_api = http_api
        self.autoip_probe_http = autoip_probe_http
        self.autoip_catalog_http = autoip_catalog_http
        self.autoip_receipts = autoip_receipts
        self.autoip_quote_source = autoip_quote_source
        self.autoip_goldens = autoip_goldens
        self.autoip_golden_http = autoip_golden_http
        self.media_preview_http = media_preview_http
        self.owner_import_http = owner_import_http
        self.import_stage_store = import_stage_store
        self.provider_media_store = provider_media_store
        self.__identity_key = identity_key

    def actor_context(self, identity: str, role: str) -> ActorContext:
        return actor_context_from_castle(identity, role, identity_key=self.__identity_key)

    def inventory(self, actor: ActorContext) -> dict[str, Any]:
        result = self.service.inventory(actor)
        for provider in result.get("providers", []):
            if provider.get("provider_id") != "autoip":
                continue
            account_id = provider.get("account_id")
            receipt = self.current_autoip_readiness(
                actor.user_id, account_id
            ) if isinstance(account_id, str) else None
            provider["compatibility_receipt"] = receipt
            provider["compatibility_receipt_current"] = receipt is not None
        return result

    def current_autoip_readiness(
        self,
        owner_user_id: str,
        account_id: str,
    ) -> Mapping[str, Any] | None:
        """Bind readiness to the exact current owner credential version."""

        try:
            account = self.store.get_owned_provider_account(
                owner_user_id, account_id
            )
            if (
                account.get("provider_id") != "autoip"
                or account.get("enabled") is not True
            ):
                return None
            reference = self.store.credential_reference_for_actor(
                owner_user_id, account_id
            )
            fingerprint = credential_reference_fingerprint(reference)
        except Exception:
            return None
        return self.autoip_receipts.current(
            owner_user_id,
            account_id,
            credential_fingerprint=fingerprint,
        )

    def verify_autoip_webhook(
        self,
        raw_body: bytes,
        signature_header: str,
    ) -> dict[str, Any]:
        """Authenticate one fixed AutoIP webhook without trusting its facts."""

        return self.service.verify_autoip_webhook(raw_body, signature_header)

    def reconcile_autoip_webhook_task(
        self,
        external_task_id: str,
        *,
        production_store: Any | None = None,
    ) -> ReconciliationResult:
        """Run the normal authoritative GET reconciler for one verified hint."""

        account_id = self.config.autoip_webhook_account_id
        if account_id is None:
            raise RuntimeConfigurationError("AutoIP webhook is not configured")
        staging_root = self.config.database_path.parent / "autoip-outputs"
        staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(staging_root, 0o700)

        def client_resolver(owner_user_id: str, selected_account_id: str) -> AutoIPClient:
            if selected_account_id != account_id:
                raise RuntimeConfigurationError("webhook task account binding changed")
            account = self.store.get_owned_provider_account(
                owner_user_id,
                selected_account_id,
            )
            if (
                account.get("provider_id") != "autoip"
                or account.get("enabled") is not True
                or not isinstance(account.get("api_base"), str)
            ):
                raise RuntimeConfigurationError("AutoIP webhook account is not ready")
            reference = self.store.credential_reference_for_actor(
                owner_user_id,
                selected_account_id,
            )
            return AutoIPClient(ProviderConfig(
                provider_id="autoip",
                api_base=account["api_base"],
                api_key_ref=reference,
                enabled=True,
            ))

        return reconcile_autoip_once(
            self.store,
            client_resolver,
            staging_root,
            lease_owner=f"autoip-webhook:{os.getpid()}:{threading.get_ident()}",
            production_store=production_store,
            account_id=account_id,
            external_task_id=external_task_id,
        )

    def autoip_profiles(self, actor: ActorContext):
        """Return owner-qualified profiles for exactly one current AutoIP account."""

        providers = [
            item for item in self.inventory(actor).get("providers", [])
            if item.get("provider_id") == "autoip"
            and item.get("compatibility_receipt_current") is True
            and isinstance(item.get("account_id"), str)
        ]
        if len(providers) != 1:
            return candidate_autoip_profiles()
        provider = providers[0]
        return self.autoip_profiles_for_account(actor.user_id, provider["account_id"])

    def autoip_profiles_for_account(self, owner_user_id: str, account_id: str):
        """Resolve goldens for one exact durable task account, never globally."""

        try:
            account = self.store.get_owned_provider_account(owner_user_id, account_id)
        except KeyError:
            return candidate_autoip_profiles()
        if account.get("provider_id") != "autoip":
            return candidate_autoip_profiles()
        compatibility = self.current_autoip_readiness(owner_user_id, account_id)
        if compatibility is None:
            return candidate_autoip_profiles()
        receipt_id = compatibility.get("receipt_id")
        if not isinstance(receipt_id, str):
            return candidate_autoip_profiles()
        try:
            snapshot = self.store.latest_accessible_capability_snapshot(
                owner_user_id, account_id
            )
        except Exception:
            return candidate_autoip_profiles()
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("source_revision") != receipt_id
            or not isinstance(snapshot.get("offerings"), list)
            or not snapshot["offerings"]
            or any(
                not isinstance(item, Mapping)
                or item.get("evidence_id") != receipt_id
                or item.get("enabled") is not True
                for item in snapshot["offerings"]
            )
        ):
            return candidate_autoip_profiles()
        snapshot_operations = {
            str(item.get("capability")) for item in snapshot["offerings"]
        }
        supported = set(compatibility.get("supported_operations") or ())
        if not supported or snapshot_operations != supported:
            return candidate_autoip_profiles()
        cases, receipts = self.autoip_goldens.current(
            owner_user_id, account_id, receipt_id,
        )
        return qualified_autoip_profiles(compatibility, cases, receipts)


def build_provider_runtime(
    config: ProviderRuntimeConfig | None = None,
    *,
    option_validator: OptionValidator | None = None,
    capability_materializer: CapabilityMaterializer | None = None,
    canonical_resource_resolver: CanonicalResourceResolver | None = None,
    h3_audit_source: H3AuditSource | None = None,
    h3_variant_contracts: Mapping[str, str] | None = None,
) -> ProviderRuntime:
    """Build the provider runtime without performing network/provider I/O."""

    selected = config or ProviderRuntimeConfig()
    if not isinstance(selected, ProviderRuntimeConfig):
        raise TypeError("config must be a ProviderRuntimeConfig")
    if option_validator is not None and not callable(option_validator):
        raise TypeError("option_validator must be callable")
    if capability_materializer is not None and not callable(capability_materializer):
        raise TypeError("capability_materializer must be callable")
    if canonical_resource_resolver is not None and not callable(
        canonical_resource_resolver
    ):
        raise TypeError("canonical_resource_resolver must be callable")
    if h3_audit_source is not None and not callable(h3_audit_source):
        raise TypeError("h3_audit_source must be callable")

    signing_key = load_or_create_plan_signing_key(selected.plan_signing_key_path)
    store = ProviderFabricStore(selected.database_path)
    identity_key = _load_identity_key_for_database(
        selected.database_path,
        selected.identity_key_path,
    )
    vault = ProviderCredentialVault(selected.credential_root)
    autoip_receipts = AutoIPCompatibilityReceiptStore(
        selected.database_path.parent / "autoip-compatibility-receipts"
    )
    autoip_quote_source = AutoIPQuoteSource(store)

    def current_autoip_readiness(
        actor: ActorContext,
        account_id: str,
    ) -> Mapping[str, Any] | None:
        try:
            account = store.get_owned_provider_account(actor.user_id, account_id)
            if (
                account.get("provider_id") != "autoip"
                or account.get("enabled") is not True
            ):
                return None
            reference = store.credential_reference_for_actor(
                actor.user_id, account_id
            )
            fingerprint = credential_reference_fingerprint(reference)
        except Exception:
            return None
        return autoip_receipts.current(
            actor.user_id,
            account_id,
            credential_fingerprint=fingerprint,
        )

    # These are the only accounts bootstrap creates.  ClipMode remains a
    # credentialless, disabled metadata namespace.  AutoIP keys are accepted
    # only through the service's explicit write-only provisioning operation.
    store.register_system_local_provider_account(
        selected.local_account_id,
        selected.local_display_name,
        api_base=selected.local_api_base,
        enabled=selected.local_comfy_enabled,
    )
    _ensure_clipmode_import_namespace(
        store,
        selected.clipmode_account_id,
        selected.clipmode_display_name,
    )

    webhook_secret_ref: SecretReference | None = None
    known_external_task = None
    if selected.autoip_webhook_account_id is not None:
        webhook_secret_ref = vault.reference_for(
            "autoip",
            selected.autoip_webhook_account_id,
            credential_id="webhook-secret",
        )

        def known_external_task(task_uuid: str) -> bool:
            return store.has_external_task(
                "autoip",
                task_uuid,
                account_id=selected.autoip_webhook_account_id,
            )

    service = ProviderFabricService(
        store,
        vault,
        signing_key,
        offerings=None,  # Always read the latest immutable store snapshots.
        quote_source=autoip_quote_source,
        option_validator=(
            option_validator if option_validator is not None else _empty_option_profile
        ),
        capability_materializer=capability_materializer,
        canonical_resource_resolver=canonical_resource_resolver,
        autoip_readiness_source=current_autoip_readiness,
        provider_accounts={
            "local_comfy": selected.local_account_id,
            "clipmode": selected.clipmode_account_id,
        },
        account_audits={},
        local_audit={
            "ready": selected.local_runtime_ready,
            "version": selected.local_version,
            "endpoint_scope": "loopback",
            "node_count": selected.local_node_count,
        },
        # The outer Castle process supplies the current exact H3 model/node and
        # existing operator-authorization audit. This closed fallback cannot
        # invent readiness when that source is absent.
        h3_audit={
            "license_blocked": True,
            "authorization_recorded": False,
            "runtime_ready": False,
            "models_ready": False,
        },
        h3_audit_source=h3_audit_source,
        h3_variant_contracts=h3_variant_contracts,
        webhook_secret_ref=webhook_secret_ref,
        known_external_task=known_external_task,
    )
    cursor_key = hmac.new(
        signing_key.reveal().encode("utf-8"),
        b"castle-provider-media-cursor-v1",
        hashlib.sha256,
    ).digest()
    provider_media_store: PrivateProviderMediaStore | None = None

    def preview_resolver(_actor: ActorContext, item: Mapping[str, Any]) -> str | None:
        if provider_media_store is None:
            return None
        projection_id = item.get("projection_id")
        if not isinstance(projection_id, str):
            return None
        return f"/api/assets/{projection_id}/preview"

    autoip_goldens = AutoIPGoldenEvidenceStore(
        selected.database_path.parent / "autoip-golden-evidence"
    )
    autoip_golden_http = AutoIPGoldenHttpApi(store, autoip_receipts, autoip_goldens)

    def project_inventory(actor: ActorContext, source: Mapping[str, Any]) -> Mapping[str, Any]:
        result = {**source, "providers": [dict(item) for item in source.get("providers", [])]}
        for provider in result["providers"]:
            if provider.get("provider_id") != "autoip":
                continue
            account_id = provider.get("account_id")
            receipt = current_autoip_readiness(
                actor, account_id
            ) if isinstance(account_id, str) else None
            provider["compatibility_receipt"] = receipt
            provider["compatibility_receipt_current"] = receipt is not None
        return result

    http_api = ProviderFabricHttpApi(
        service,
        store,
        cursor_key,
        clipmode_account_id=selected.clipmode_account_id,
        preview_resolver=preview_resolver,
        inventory_projector=project_inventory,
    )
    autoip_probe_http = AutoIPProbeHttpApi(
        store, receipt_recorder=autoip_receipts.record
    )
    autoip_catalog_http = AutoIPCatalogHttpApi(
        store,
        autoip_receipts,
        AutoIPCatalogSnapshotStore(selected.database_path.parent / "autoip-catalog-snapshots"),
        selected.database_path.parent / "autoip-outputs",
    )
    owner_import_http: OwnerImportHttpApi | None = None
    import_stage_store: PrivateImportStageStore | None = None
    media_preview_http: ProviderMediaPreviewHttpApi | None = None
    if selected.owner_import_enabled:
        signing_bytes = signing_key.reveal().encode("utf-8")
        identity_bytes = identity_key.reveal().encode("utf-8")
        stage_signing_key = SecretValue(
            hmac.new(
                signing_bytes,
                b"castle-provider-import-stage-signing-v1",
                hashlib.sha256,
            ).hexdigest()
        )
        decision_key = hmac.new(
            signing_bytes,
            b"castle-provider-import-decision-signing-v1",
            hashlib.sha256,
        ).digest()

        def import_account_id(owner_user_id: str) -> str:
            if not isinstance(owner_user_id, str):
                raise ValueError("owner identity is invalid")
            digest = hmac.new(
                identity_bytes,
                b"castle-provider-clipmode-import-account-v1\0"
                + owner_user_id.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return f"clipmode.user:{digest[:32]}"

        def authorize_import_account(owner_user_id: str, account_id: str) -> bool:
            if account_id != import_account_id(owner_user_id):
                return False
            try:
                account = store.get_owned_provider_account(owner_user_id, account_id)
            except KeyError:
                return False
            return (
                account["provider_id"] == "clipmode"
                and account["enabled"] is False
                and account["authentication"]
                == {"kind": "none", "configured": False}
            )

        protected = (
            selected.database_path,
            selected.credential_root,
            selected.plan_signing_key_path,
            selected.identity_key_path,
        )
        import_stage_store = PrivateImportStageStore(
            selected.import_stage_root,
            identity_key,
            stage_signing_key,
            account_authorizer=authorize_import_account,
            forbidden_roots=(*protected, selected.provider_media_root),
        )
        try:
            provider_media_store = PrivateProviderMediaStore(
                selected.provider_media_root,
                identity_key,
                forbidden_roots=(*protected, selected.import_stage_root),
            )
            owner_import_http = OwnerImportHttpApi(
                stage_store=import_stage_store,
                media_store=provider_media_store,
                provider_store=store,
                decision_tokens=OwnerImportDecisionTokenCodec(decision_key),
                account_id=import_account_id,
            )
            media_preview_http = ProviderMediaPreviewHttpApi(
                store, provider_media_store
            )
        except BaseException:
            import_stage_store.close()
            if provider_media_store is not None:
                provider_media_store.close()
            raise
    return ProviderRuntime(
        selected,
        store,
        vault,
        service,
        http_api,
        identity_key,
        autoip_probe_http,
        autoip_catalog_http,
        autoip_receipts,
        autoip_quote_source,
        autoip_goldens,
        autoip_golden_http,
        media_preview_http,
        owner_import_http,
        import_stage_store,
        provider_media_store,
    )


class ProviderRuntimeHolder:
    """Thread-safe lazy singleton holder for one server process."""

    __slots__ = (
        "_lock",
        "_runtime",
        "_config",
        "_option_validator",
        "_capability_materializer",
        "_canonical_resource_resolver",
        "_h3_audit_source",
        "_h3_variant_contracts",
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runtime: ProviderRuntime | None = None
        self._config: ProviderRuntimeConfig | None = None
        self._option_validator: OptionValidator | None = None
        self._capability_materializer: CapabilityMaterializer | None = None
        self._canonical_resource_resolver: CanonicalResourceResolver | None = None
        self._h3_audit_source: H3AuditSource | None = None
        self._h3_variant_contracts: dict[str, str] = {}

    def get(
        self,
        config: ProviderRuntimeConfig | None = None,
        *,
        option_validator: OptionValidator | None = None,
        capability_materializer: CapabilityMaterializer | None = None,
        canonical_resource_resolver: CanonicalResourceResolver | None = None,
        h3_audit_source: H3AuditSource | None = None,
        h3_variant_contracts: Mapping[str, str] | None = None,
    ) -> ProviderRuntime:
        with self._lock:
            if h3_variant_contracts is not None and not isinstance(
                h3_variant_contracts, Mapping
            ):
                raise TypeError("h3_variant_contracts must be an object")
            selected_h3_variants = dict(h3_variant_contracts or {})
            if self._runtime is not None:
                if config is not None and config != self._config:
                    raise RuntimeConfigurationError(
                        "provider runtime is already initialized with different configuration"
                    )
                if (
                    option_validator is not None
                    and option_validator is not self._option_validator
                ):
                    raise RuntimeConfigurationError(
                        "provider runtime is already initialized with a different option profile"
                    )
                if (
                    capability_materializer is not None
                    and capability_materializer is not self._capability_materializer
                ):
                    raise RuntimeConfigurationError(
                        "provider runtime is already initialized with a different capability materializer"
                    )
                if (
                    canonical_resource_resolver is not None
                    and canonical_resource_resolver
                    is not self._canonical_resource_resolver
                ):
                    raise RuntimeConfigurationError(
                        "provider runtime is already initialized with a different canonical resolver"
                    )
                if (
                    h3_audit_source is not None
                    and h3_audit_source is not self._h3_audit_source
                ):
                    raise RuntimeConfigurationError(
                        "provider runtime is already initialized with a different H3 audit source"
                    )
                if (
                    h3_variant_contracts is not None
                    and selected_h3_variants != self._h3_variant_contracts
                ):
                    raise RuntimeConfigurationError(
                        "provider runtime is already initialized with different H3 contracts"
                    )
                return self._runtime
            selected = config or ProviderRuntimeConfig()
            runtime = build_provider_runtime(
                selected,
                option_validator=option_validator,
                capability_materializer=capability_materializer,
                canonical_resource_resolver=canonical_resource_resolver,
                h3_audit_source=h3_audit_source,
                h3_variant_contracts=selected_h3_variants,
            )
            self._config = selected
            self._option_validator = option_validator
            self._capability_materializer = capability_materializer
            self._canonical_resource_resolver = canonical_resource_resolver
            self._h3_audit_source = h3_audit_source
            self._h3_variant_contracts = selected_h3_variants
            self._runtime = runtime
            return runtime


_DEFAULT_RUNTIME_HOLDER = ProviderRuntimeHolder()


def get_provider_runtime(
    config: ProviderRuntimeConfig | None = None,
    *,
    option_validator: OptionValidator | None = None,
    capability_materializer: CapabilityMaterializer | None = None,
    canonical_resource_resolver: CanonicalResourceResolver | None = None,
    h3_audit_source: H3AuditSource | None = None,
    h3_variant_contracts: Mapping[str, str] | None = None,
) -> ProviderRuntime:
    """Return the process singleton, constructing it once under a lock."""

    return _DEFAULT_RUNTIME_HOLDER.get(
        config,
        option_validator=option_validator,
        capability_materializer=capability_materializer,
        canonical_resource_resolver=canonical_resource_resolver,
        h3_audit_source=h3_audit_source,
        h3_variant_contracts=h3_variant_contracts,
    )


__all__ = [
    "IdentityKeyPathError",
    "PersistentKeyPathError",
    "PlanSigningKeyPathError",
    "ProviderRuntime",
    "ProviderRuntimeConfig",
    "ProviderRuntimeError",
    "ProviderRuntimeHolder",
    "RuntimeConfigurationError",
    "actor_context_from_castle",
    "build_provider_runtime",
    "default_identity_key_path",
    "default_plan_signing_key_path",
    "get_provider_runtime",
    "load_or_create_identity_key",
    "load_or_create_plan_signing_key",
]
