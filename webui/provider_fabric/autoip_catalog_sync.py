"""Owner-private, read-only AutoIP task and dance catalog snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Protocol

from .autoip_contract import CONTRACT_ID


SNAPSHOT_VERSION = "castle.autoip-catalog-snapshot.v1"
MAX_TASK_PAGES = 20
TASK_PAGE_SIZE = 100
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_TASK = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_PROBE = re.compile(r"^autoip-probe:[0-9a-f]{32}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_STATES = frozenset({
    "created", "submitted", "pending", "queued", "processing", "running",
    "completed", "failed", "cancelled", "timeout",
})


class AutoIPCatalogSyncError(ValueError):
    pass


class AutoIPCatalogClient(Protocol):
    def list_tasks(self, *, page: int = 1, per_page: int = 20) -> Mapping[str, Any]: ...
    def list_dance_templates(self) -> Mapping[str, Any]: ...


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _snapshot_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identity(owner_user_id: object, account_id: object) -> tuple[str, str]:
    if (
        not isinstance(owner_user_id, str) or not owner_user_id
        or not isinstance(account_id, str) or _ID.fullmatch(account_id) is None
    ):
        raise AutoIPCatalogSyncError("autoip_catalog_identity_invalid")
    return owner_user_id, account_id


def _ready(receipt: object, owner_user_id: str, account_id: str) -> None:
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != "castle.autoip-compatibility-receipt.v1"
        or receipt.get("contract_id") != CONTRACT_ID
        or receipt.get("persisted") is not True
        or receipt.get("current") is not True
        or not isinstance(receipt.get("receipt_id"), str)
        or _PROBE.fullmatch(receipt["receipt_id"]) is None
        or not isinstance(receipt.get("tested_at"), str)
        or _UTC.fullmatch(receipt["tested_at"]) is None
        or not isinstance(receipt.get("expires_at"), str)
        or _UTC.fullmatch(receipt["expires_at"]) is None
        or receipt.get("owner_user_id", owner_user_id) != owner_user_id
        or receipt.get("account_id", account_id) != account_id
    ):
        raise AutoIPCatalogSyncError("autoip_catalog_probe_not_current")


def _task_page(raw: object, page: int) -> tuple[list[dict[str, str]], int | None, int | None]:
    if not isinstance(raw, Mapping) or raw.get("page") != page or raw.get("per_page") != TASK_PAGE_SIZE:
        raise AutoIPCatalogSyncError("autoip_catalog_task_page_invalid")
    total, total_pages, tasks = raw.get("total"), raw.get("total_pages"), raw.get("tasks")
    if (
        (total is not None and (type(total) is not int or total < 0))
        or (total_pages is not None and (type(total_pages) is not int or total_pages < 0))
        or (total is None) != (total_pages is None)
        or not isinstance(tasks, list) or len(tasks) > TASK_PAGE_SIZE
    ):
        raise AutoIPCatalogSyncError("autoip_catalog_task_page_invalid")
    normalized = []
    seen = set()
    for item in tasks:
        if not isinstance(item, Mapping) or set(item) != {"task_uuid", "status"}:
            raise AutoIPCatalogSyncError("autoip_catalog_task_invalid")
        task_uuid, status = item.get("task_uuid"), item.get("status")
        if (
            not isinstance(task_uuid, str) or _TASK.fullmatch(task_uuid) is None
            or status not in _STATES or task_uuid in seen
        ):
            raise AutoIPCatalogSyncError("autoip_catalog_task_invalid")
        seen.add(task_uuid)
        normalized.append({"task_uuid": task_uuid, "status": status})
    return normalized, total, total_pages


def _templates(raw: object) -> list[dict[str, Any]]:
    if (
        not isinstance(raw, Mapping)
        or raw.get("transport_allowlisted") is not True
        or raw.get("product_visibility_granted") is not False
        or not isinstance(raw.get("templates"), list)
        or len(raw["templates"]) > 10_000
    ):
        raise AutoIPCatalogSyncError("autoip_catalog_templates_invalid")
    result, seen = [], set()
    for item in raw["templates"]:
        if not isinstance(item, Mapping) or set(item) != {
            "template_id", "name", "duration_seconds", "product_visibility_granted", "selectable",
        }:
            raise AutoIPCatalogSyncError("autoip_catalog_template_invalid")
        template_id, name, duration = item.get("template_id"), item.get("name"), item.get("duration_seconds")
        if (
            type(template_id) is not int or not 1 <= template_id <= 2**31 - 1
            or template_id in seen or not isinstance(name, str) or not 1 <= len(name) <= 200
            or name != name.strip() or type(duration) is not int or not 1 <= duration <= 3_600
            or item.get("product_visibility_granted") is not False or item.get("selectable") is not False
        ):
            raise AutoIPCatalogSyncError("autoip_catalog_template_invalid")
        seen.add(template_id)
        result.append({
            "template_id": template_id, "name": name, "duration_seconds": duration,
            "preview_available": False, "runtime_ready": False, "selectable": False,
        })
    return sorted(result, key=lambda item: (item["name"].casefold(), item["template_id"]))


class AutoIPCatalogSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AutoIPCatalogSyncError("autoip_catalog_root_unsafe")

    def _path(self, owner_user_id: str, account_id: str) -> Path:
        owner_user_id, account_id = _identity(owner_user_id, account_id)
        key = hashlib.sha256((owner_user_id + "\0" + account_id).encode()).hexdigest()
        return self.root / f"catalog-{key}.json"

    def put(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        owner, account = _identity(snapshot.get("owner_user_id"), snapshot.get("account_id"))
        path = self._path(owner, account)
        descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(_canonical(snapshot) + b"\n")
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
        return self.get(owner, account)

    def get(self, owner_user_id: str, account_id: str) -> dict[str, Any]:
        path = self._path(owner_user_id, account_id)
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 < before.st_size <= 2 * 1024 * 1024
        ):
            raise AutoIPCatalogSyncError("autoip_catalog_snapshot_unsafe")
        raw = path.read_bytes()
        after = path.lstat()
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if identity(before) != identity(after) or len(raw) != before.st_size:
            raise AutoIPCatalogSyncError("autoip_catalog_snapshot_changed")
        try:
            value = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise AutoIPCatalogSyncError("autoip_catalog_snapshot_invalid") from error
        if not isinstance(value, dict) or value.get("snapshot_hash") != _snapshot_hash({
            key: item for key, item in value.items() if key != "snapshot_hash"
        }):
            raise AutoIPCatalogSyncError("autoip_catalog_snapshot_invalid")
        if value.get("owner_user_id") != owner_user_id or value.get("account_id") != account_id:
            raise AutoIPCatalogSyncError("autoip_catalog_snapshot_owner_mismatch")
        return value


def sync_autoip_catalog(
    client: AutoIPCatalogClient,
    store: AutoIPCatalogSnapshotStore,
    owner_user_id: str,
    account_id: str,
    *,
    compatibility_receipt: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch bounded, non-spending catalogs and persist only redacted facts."""

    owner_user_id, account_id = _identity(owner_user_id, account_id)
    _ready(compatibility_receipt, owner_user_id, account_id)
    first, total, total_pages = _task_page(
        client.list_tasks(page=1, per_page=TASK_PAGE_SIZE), 1,
    )
    tasks = list(first)
    fetched_pages = 1
    if total_pages is not None:
        for page in range(2, min(total_pages, MAX_TASK_PAGES) + 1):
            rows, page_total, page_total_pages = _task_page(
                client.list_tasks(page=page, per_page=TASK_PAGE_SIZE), page,
            )
            if (page_total, page_total_pages) != (total, total_pages):
                raise AutoIPCatalogSyncError("autoip_catalog_pagination_changed")
            tasks.extend(rows)
            fetched_pages = page
    if len({item["task_uuid"] for item in tasks}) != len(tasks):
        raise AutoIPCatalogSyncError("autoip_catalog_duplicate_task")
    templates = _templates(client.list_dance_templates())
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    material = {
        "schema_version": SNAPSHOT_VERSION,
        "owner_user_id": owner_user_id,
        "account_id": account_id,
        "provider_id": "autoip",
        "contract_id": CONTRACT_ID,
        "synced_at": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_total": total,
        "task_total_pages": total_pages,
        "task_pages_fetched": fetched_pages,
        "history_complete": total_pages is not None and fetched_pages == total_pages,
        "tasks": tasks,
        "dance_templates": templates,
        "provider_write_performed": False,
        "credits_spent": 0,
        "provider_urls_persisted": False,
    }
    snapshot = {**material, "snapshot_hash": _snapshot_hash(material)}
    persisted = store.put(snapshot)
    if not hmac.compare_digest(persisted["snapshot_hash"], snapshot["snapshot_hash"]):
        raise AutoIPCatalogSyncError("autoip_catalog_persistence_mismatch")
    return persisted


def public_autoip_catalog_snapshot(
    snapshot: Mapping[str, Any], *, adopted_task_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return an owner-private browser projection without storage identities."""

    if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != SNAPSHOT_VERSION:
        raise AutoIPCatalogSyncError("autoip_catalog_snapshot_invalid")
    account_id = snapshot.get("account_id")
    if not isinstance(account_id, str) or _ID.fullmatch(account_id) is None:
        raise AutoIPCatalogSyncError("autoip_catalog_snapshot_invalid")
    tasks = snapshot.get("tasks")
    templates = snapshot.get("dance_templates")
    if not isinstance(tasks, list) or not isinstance(templates, list):
        raise AutoIPCatalogSyncError("autoip_catalog_snapshot_invalid")
    return {
        "schema_version": "castle.autoip-catalog.response.v1",
        "ok": True,
        "provider_id": "autoip",
        "account_id": account_id,
        "synced_at": snapshot.get("synced_at"),
        "history": {
            "total": snapshot.get("task_total"),
            "total_pages": snapshot.get("task_total_pages"),
            "pages_fetched": snapshot.get("task_pages_fetched"),
            "complete": snapshot.get("history_complete"),
            "tasks": [{**dict(item), "adopted": item.get("task_uuid") in adopted_task_ids}
                for item in tasks],
        },
        "dance_templates": [dict(item) for item in templates],
        "authority": {
            "owner_scoped": True,
            "compatibility_receipt_checked": True,
            "provider_write_performed": False,
            "credits_spent": 0,
            "provider_urls_disclosed": False,
            "templates_selectable": False,
        },
    }


__all__ = [
    "AutoIPCatalogSnapshotStore", "AutoIPCatalogSyncError", "MAX_TASK_PAGES",
    "SNAPSHOT_VERSION", "TASK_PAGE_SIZE", "public_autoip_catalog_snapshot",
    "sync_autoip_catalog",
]
