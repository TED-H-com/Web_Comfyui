from __future__ import annotations

import sqlite3

from .db import json_loads, stable_json, utc_ts


def add_event(
    conn: sqlite3.Connection,
    event_type: str,
    message: str,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    artifact_id: str | None = None,
    level: str = "info",
    data: dict | None = None,
) -> int:
    cur = conn.execute(
        """
        insert into events(run_id, task_id, artifact_id, event_type, level, message, data_json, created_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, task_id, artifact_id, event_type, level, message, stable_json(data or {}), utc_ts()),
    )
    return int(cur.lastrowid)


def list_events(conn: sqlite3.Connection, after: int = 0, limit: int = 100, run_id: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit or 100), 500))
    params: list = [after]
    where = "id > ?"
    if run_id:
        where += " and run_id = ?"
        params.append(run_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        select * from events
        where {where}
        order by id asc
        limit ?
        """,
        params,
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["data"] = json_loads(item.pop("data_json", None), {})
        items.append(item)
    return items
