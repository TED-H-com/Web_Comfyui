from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import Settings, ensure_dirs, settings_from_env
from .db import utc_ts


RESOURCE_IDS = {"gpu:rtx5090", "service:comfyui", "lane:cpu", "local:web"}


def _reservation_id() -> str:
    return f"res_{uuid.uuid4().hex[:20]}"


@contextmanager
def _reservation_lock(settings: Settings):
    lock_path = settings.resource_reservations_path.with_name(f"{settings.resource_reservations_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_reservations(settings: Settings | None = None) -> list[dict]:
    settings = ensure_dirs(settings or settings_from_env())
    try:
        data = json.loads(settings.resource_reservations_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    items = data.get("reservations") if isinstance(data, dict) else data
    return [item for item in items if isinstance(item, dict)]


def _write_reservations(items: list[dict], settings: Settings | None = None) -> None:
    settings = ensure_dirs(settings or settings_from_env())
    path = settings.resource_reservations_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = {"reservations": items[-200:], "updated_at": utc_ts()}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _ttl_seconds(value, default: int = 3600) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        ttl = default
    return max(60, min(ttl, 24 * 3600))


def active_reservations(settings: Settings | None = None, *, resource_id: str | None = None, now: float | None = None) -> list[dict]:
    now = now if now is not None else utc_ts()
    active = []
    for item in _read_reservations(settings):
        if not item.get("active", True):
            continue
        if resource_id and item.get("resource_id") != resource_id:
            continue
        expires_at = float(item.get("expires_at") or 0)
        if expires_at and expires_at <= now:
            continue
        active.append(item)
    return active


def blocked_lanes(settings: Settings | None = None) -> set[str]:
    lanes = set()
    for item in active_reservations(settings):
        resource_id = str(item.get("resource_id") or "")
        if resource_id == "gpu:rtx5090" or resource_id == "service:comfyui":
            lanes.add("gpu")
        elif resource_id.startswith("lane:"):
            lane = resource_id.split(":", 1)[1]
            if lane:
                lanes.add(lane)
    return lanes


def reserve_resource(resource_id: str, body: dict | None = None, settings: Settings | None = None) -> dict:
    settings = ensure_dirs(settings or settings_from_env())
    body = body if isinstance(body, dict) else {}
    resource_id = str(resource_id or "").strip()
    if resource_id not in RESOURCE_IDS:
        raise ValueError(f"resource_id must be one of: {', '.join(sorted(RESOURCE_IDS))}")
    now = utc_ts()
    reservation = {
        "id": _reservation_id(),
        "resource_id": resource_id,
        "reason": str(body.get("reason") or "manual reservation")[:500],
        "actor": str(body.get("actor") or "operator")[:120],
        "active": True,
        "created_at": now,
        "expires_at": now + _ttl_seconds(body.get("ttl_seconds") or body.get("ttlSeconds")),
    }
    with _reservation_lock(settings):
        items = _read_reservations(settings)
        items.append(reservation)
        _write_reservations(items, settings)
    return reservation


def release_resource(resource_id: str, body: dict | None = None, settings: Settings | None = None) -> tuple[list[dict], int]:
    settings = ensure_dirs(settings or settings_from_env())
    body = body if isinstance(body, dict) else {}
    resource_id = str(resource_id or "").strip()
    if resource_id not in RESOURCE_IDS:
        raise ValueError(f"resource_id must be one of: {', '.join(sorted(RESOURCE_IDS))}")
    target_id = str(body.get("reservation_id") or body.get("reservationId") or "").strip()
    now = utc_ts()
    released = []
    with _reservation_lock(settings):
        items = _read_reservations(settings)
        for item in items:
            if not item.get("active", True):
                continue
            if item.get("resource_id") != resource_id:
                continue
            if target_id and item.get("id") != target_id:
                continue
            item["active"] = False
            item["released_at"] = now
            item["released_by"] = str(body.get("actor") or "operator")[:120]
            released.append(item)
        _write_reservations(items, settings)
    return released, len(released)


def reservations_public(settings: Settings | None = None) -> dict:
    return {"active": active_reservations(settings), "all": _read_reservations(settings)}
