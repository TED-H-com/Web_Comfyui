"""Owner-private, short-lived evidence from a non-spending AutoIP probe."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Sequence

from .autoip_contract import CONTRACT_ID
from .config import SecretReference


SCHEMA_VERSION = "castle.autoip-compatibility-receipt.v1"
MAX_VALIDITY_HOURS = 24
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_SUPPORTED_OPERATIONS = frozenset({
    "text_to_image",
    "image_to_image",
    "image_to_video",
    "text_to_video",
    "lip_sync",
    "dance",
    "multi_subject_video",
})


class AutoIPReadinessError(ValueError):
    pass


def _hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def credential_reference_fingerprint(
    reference: SecretReference,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return a one-way version binding without returning credential bytes.

    The domain prefix prevents this digest from being confused with a hash
    used by another subsystem.  AutoIP API keys are vault-validated,
    high-entropy values; neither the resolved value nor its locator is placed
    in the receipt or returned to callers.
    """

    if not isinstance(reference, SecretReference):
        raise TypeError("reference must be a SecretReference")
    secret = reference.resolve(environ).reveal()
    encoded = bytearray(secret.encode("utf-8", "strict"))
    try:
        digest = hashlib.sha256(b"castle.autoip-credential.v1\0" + encoded).hexdigest()
    finally:
        for index in range(len(encoded)):
            encoded[index] = 0
        secret = ""
    return f"sha256:{digest}"


def _operations(value: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise AutoIPReadinessError("autoip_supported_operations_invalid")
    operations = tuple(value)
    if (
        not operations
        or any(not isinstance(item, str) or item not in _SUPPORTED_OPERATIONS for item in operations)
        or len(operations) != len(set(operations))
    ):
        raise AutoIPReadinessError("autoip_supported_operations_invalid")
    return tuple(sorted(operations))


class AutoIPCompatibilityReceiptStore:
    """Atomic JSON receipts; no credential value, URL, pricing body, or provider error."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().absolute()
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.root.stat()
        except OSError as error:
            raise AutoIPReadinessError("autoip_receipt_root_unavailable") from error
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AutoIPReadinessError("autoip_receipt_root_unsafe")

    def _path(self, owner_user_id: str, account_id: str) -> Path:
        if not isinstance(owner_user_id, str) or not owner_user_id or not _ID.fullmatch(account_id):
            raise AutoIPReadinessError("autoip_receipt_identity_invalid")
        key = hashlib.sha256((owner_user_id + "\0" + account_id).encode("utf-8")).hexdigest()
        return self.root / f"probe-{key}.json"

    def record(
        self,
        owner_user_id: str,
        account_id: str,
        *,
        health: Mapping[str, Any],
        available_credits: int,
        pricing_catalog_sha256: str,
        pricing_authoritative: bool,
        credential_fingerprint: str,
        supported_operations: Sequence[str],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        operations = _operations(supported_operations)
        if (
            not isinstance(health, Mapping)
            or health.get("provider_id") != "autoip"
            or health.get("provider_reachable") is not True
            or health.get("api_healthy") is not True
            or health.get("authenticated") is not True
            or type(available_credits) is not int or available_credits < 0
            or not isinstance(pricing_catalog_sha256, str)
            or _HEX.fullmatch(pricing_catalog_sha256) is None
            or pricing_authoritative is not True
            or not isinstance(credential_fingerprint, str)
            or _CREDENTIAL_FINGERPRINT.fullmatch(credential_fingerprint) is None
        ):
            raise AutoIPReadinessError("autoip_probe_not_compatible")
        timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
        expires = timestamp + timedelta(hours=MAX_VALIDITY_HOURS)
        material = {
            "schema_version": SCHEMA_VERSION,
            "owner_user_id": owner_user_id,
            "account_id": account_id,
            "provider_id": "autoip",
            "contract_id": CONTRACT_ID,
            "tested_at": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "health": {
                "provider_reachable": True,
                "api_healthy": True,
                "authenticated": True,
            },
            "available_credits": available_credits,
            "pricing_catalog_sha256": pricing_catalog_sha256,
            "supported_operations": list(operations),
            "pricing_authoritative": True,
            "credential_fingerprint": credential_fingerprint,
            "provider_write_performed": False,
            "credits_spent": 0,
            "generation_enabled": False,
        }
        receipt = {**material, "receipt_hash": _hash(material)}
        path = self._path(owner_user_id, account_id)
        descriptor, temporary = tempfile.mkstemp(prefix=".probe-", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": f"autoip-probe:{receipt['receipt_hash'][:32]}",
            "provider_id": "autoip",
            "contract_id": CONTRACT_ID,
            "tested_at": material["tested_at"],
            "expires_at": material["expires_at"],
            "pricing_catalog_sha256": pricing_catalog_sha256,
            "supported_operations": list(operations),
            "persisted": True,
        }

    def current(
        self,
        owner_user_id: str,
        account_id: str,
        *,
        credential_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return a public current-proof summary, or fail closed for stale evidence."""

        path = self._path(owner_user_id, account_id)
        try:
            before = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= 16 * 1024
            ):
                return None
            raw = path.read_bytes()
            after = path.lstat()
        except OSError:
            return None
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(before) != identity(after) or len(raw) != before.st_size:
            return None
        try:
            receipt = json.loads(raw.decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError):
            return None
        keys = {
            "schema_version", "owner_user_id", "account_id", "provider_id", "contract_id",
            "tested_at", "expires_at", "health", "available_credits",
            "pricing_catalog_sha256", "supported_operations", "pricing_authoritative",
            "credential_fingerprint", "provider_write_performed", "credits_spent",
            "generation_enabled", "receipt_hash",
        }
        if not isinstance(receipt, dict) or set(receipt) != keys:
            return None
        material = {key: receipt[key] for key in keys - {"receipt_hash"}}
        if (
            receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("owner_user_id") != owner_user_id
            or receipt.get("account_id") != account_id
            or receipt.get("provider_id") != "autoip"
            or receipt.get("contract_id") != CONTRACT_ID
            or receipt.get("receipt_hash") != _hash(material)
            or receipt.get("pricing_authoritative") is not True
            or not isinstance(receipt.get("pricing_catalog_sha256"), str)
            or _HEX.fullmatch(receipt["pricing_catalog_sha256"]) is None
            or not isinstance(receipt.get("credential_fingerprint"), str)
            or _CREDENTIAL_FINGERPRINT.fullmatch(receipt["credential_fingerprint"]) is None
            or (
                credential_fingerprint is not None
                and receipt.get("credential_fingerprint") != credential_fingerprint
            )
            or receipt.get("provider_write_performed") is not False
            or receipt.get("credits_spent") != 0
            or receipt.get("generation_enabled") is not False
        ):
            return None
        if credential_fingerprint is not None and (
            not isinstance(credential_fingerprint, str)
            or _CREDENTIAL_FINGERPRINT.fullmatch(credential_fingerprint) is None
        ):
            return None
        try:
            operations = _operations(receipt.get("supported_operations"))
        except (AutoIPReadinessError, TypeError):
            return None
        if list(operations) != receipt.get("supported_operations"):
            return None
        try:
            tested = datetime.strptime(receipt["tested_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            expires = datetime.strptime(receipt["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not tested <= current_time < expires or expires - tested != timedelta(hours=MAX_VALIDITY_HOURS):
            return None
        return {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": f"autoip-probe:{receipt['receipt_hash'][:32]}",
            "provider_id": "autoip",
            "contract_id": CONTRACT_ID,
            "tested_at": receipt["tested_at"],
            "expires_at": receipt["expires_at"],
            "pricing_catalog_sha256": receipt["pricing_catalog_sha256"],
            "supported_operations": list(operations),
            "persisted": True,
            "current": True,
        }


__all__ = [
    "AutoIPCompatibilityReceiptStore", "AutoIPReadinessError",
    "MAX_VALIDITY_HOURS", "SCHEMA_VERSION", "credential_reference_fingerprint",
]
