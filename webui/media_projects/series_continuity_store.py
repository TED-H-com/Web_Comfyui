"""Owner-scoped, hash-chained series bible and episode continuity storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any, Mapping


SERIES_BIBLE_VERSION = "castle.series-bible.v1"
EPISODE_CONTINUITY_VERSION = "castle.episode-continuity.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class SeriesContinuityConflict(RuntimeError):
    pass


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _state(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 200:
        raise ValueError(f"{label} is invalid")
    result = {}
    for key, child in value.items():
        stable = _id(key, f"{label} key")
        if not isinstance(child, str) or not child.strip() or len(child.strip()) > 500:
            raise ValueError(f"{label} value is invalid")
        result[stable] = child.strip()
    return dict(sorted(result.items()))


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


class SeriesContinuityStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.database_path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise ValueError("series continuity parent must be owner-only")
        if not self.database_path.exists():
            descriptor = os.open(
                self.database_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
            os.close(descriptor)
        info = self.database_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("series continuity database must be owner-only")
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS series_bible_revisions (
                    owner_user_id TEXT NOT NULL, series_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, title TEXT NOT NULL,
                    entity_ids_json TEXT NOT NULL, canon_json TEXT NOT NULL,
                    previous_bible_hash TEXT, bible_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY(owner_user_id, series_id, revision)
                );
                CREATE TABLE IF NOT EXISTS series_episode_continuity (
                    owner_user_id TEXT NOT NULL, series_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    state_before_json TEXT NOT NULL, event_summary TEXT NOT NULL,
                    state_after_json TEXT NOT NULL, director_plan_hash TEXT NOT NULL,
                    pack_proposal_hash TEXT NOT NULL, previous_episode_hash TEXT,
                    episode_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY(owner_user_id, series_id, episode_id),
                    UNIQUE(owner_user_id, series_id, ordinal)
                );
                CREATE TRIGGER IF NOT EXISTS series_bible_no_update
                BEFORE UPDATE ON series_bible_revisions BEGIN SELECT RAISE(ABORT, 'series bible is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS series_episode_no_update
                BEFORE UPDATE ON series_episode_continuity BEGIN SELECT RAISE(ABORT, 'series episode is immutable'); END;
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _bible(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": SERIES_BIBLE_VERSION,
            "series_id": row["series_id"], "title": row["title"],
            "entity_ids": json.loads(row["entity_ids_json"]),
            "canon": json.loads(row["canon_json"]), "revision": row["revision"],
            "previous_bible_hash": row["previous_bible_hash"], "bible_hash": row["bible_hash"],
        }

    def get_bible(self, owner_user_id: str, series_id: str) -> dict[str, Any]:
        owner, series = _id(owner_user_id, "owner_user_id"), _id(series_id, "series_id")
        with self._connect() as connection:
            row = connection.execute("""
                SELECT * FROM series_bible_revisions
                 WHERE owner_user_id = ? AND series_id = ? ORDER BY revision DESC LIMIT 1
            """, (owner, series)).fetchone()
        if row is None:
            raise KeyError(series)
        return self._bible(row)

    def create_bible(
        self, owner_user_id: str, series_id: str, title: str,
        entity_ids: list[str], canon: Mapping[str, str],
    ) -> dict[str, Any]:
        owner, series = _id(owner_user_id, "owner_user_id"), _id(series_id, "series_id")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ValueError("series title is invalid")
        entities = sorted({_id(item, "entity_id") for item in entity_ids})
        if not entities or len(entities) != len(entity_ids) or len(entities) > 100:
            raise ValueError("series entities are invalid")
        state = _state(canon, "canon")
        material = {"owner": owner, "series": series, "revision": 1, "title": title.strip(), "entities": entities, "canon": state}
        digest = _hash(material)
        with self._lock, self._connect() as connection:
            try:
                connection.execute("""
                    INSERT INTO series_bible_revisions (
                        owner_user_id, series_id, revision, title, entity_ids_json,
                        canon_json, previous_bible_hash, bible_hash
                    ) VALUES (?, ?, 1, ?, ?, ?, NULL, ?)
                """, (owner, series, title.strip(), json.dumps(entities), json.dumps(state), digest))
                connection.commit()
            except sqlite3.IntegrityError as error:
                raise SeriesContinuityConflict("series bible already exists") from error
        return self.get_bible(owner, series)

    def append_episode(
        self, owner_user_id: str, series_id: str, *, episode_id: str, ordinal: int,
        state_before: Mapping[str, str], event_summary: str, state_after: Mapping[str, str],
        director_plan_hash: str, pack_proposal_hash: str, expected_bible_hash: str,
    ) -> dict[str, Any]:
        owner, series = _id(owner_user_id, "owner_user_id"), _id(series_id, "series_id")
        episode = _id(episode_id, "episode_id")
        before, after = _state(state_before, "state_before"), _state(state_after, "state_after")
        if (
            isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1
            or not isinstance(event_summary, str) or not event_summary.strip() or len(event_summary.strip()) > 3000
            or any(not isinstance(value, str) or _HASH.fullmatch(value) is None for value in (
                director_plan_hash, pack_proposal_hash, expected_bible_hash,
            ))
        ):
            raise ValueError("episode continuity fields are invalid")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            bible_row = connection.execute("""
                SELECT * FROM series_bible_revisions WHERE owner_user_id = ? AND series_id = ?
                ORDER BY revision DESC LIMIT 1
            """, (owner, series)).fetchone()
            if bible_row is None:
                raise KeyError(series)
            bible = self._bible(bible_row)
            if bible["bible_hash"] != expected_bible_hash:
                raise SeriesContinuityConflict("series bible changed")
            previous = connection.execute("""
                SELECT * FROM series_episode_continuity WHERE owner_user_id = ? AND series_id = ?
                ORDER BY ordinal DESC LIMIT 1
            """, (owner, series)).fetchone()
            expected_ordinal = 1 if previous is None else int(previous["ordinal"]) + 1
            expected_before = bible["canon"] if previous is None else json.loads(previous["state_after_json"])
            if ordinal != expected_ordinal or before != expected_before:
                raise SeriesContinuityConflict("episode does not continue the prior canon")
            previous_hash = None if previous is None else previous["episode_hash"]
            material = {
                "owner": owner, "series": series, "episode": episode, "ordinal": ordinal,
                "before": before, "event": event_summary.strip(), "after": after,
                "director": director_plan_hash, "proposal": pack_proposal_hash,
                "previous": previous_hash,
            }
            episode_hash = _hash(material)
            revision = int(bible["revision"]) + 1
            merged_canon = {**bible["canon"], **after}
            next_material = {
                "owner": owner, "series": series, "revision": revision,
                "title": bible["title"], "entities": bible["entity_ids"], "canon": merged_canon,
                "previous": bible["bible_hash"],
            }
            next_bible_hash = _hash(next_material)
            try:
                connection.execute("""
                    INSERT INTO series_episode_continuity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """, (owner, series, episode, ordinal, json.dumps(before), event_summary.strip(),
                       json.dumps(after), director_plan_hash, pack_proposal_hash, previous_hash, episode_hash))
                connection.execute("""
                    INSERT INTO series_bible_revisions (
                        owner_user_id, series_id, revision, title, entity_ids_json,
                        canon_json, previous_bible_hash, bible_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (owner, series, revision, bible["title"], json.dumps(bible["entity_ids"]),
                       json.dumps(merged_canon), bible["bible_hash"], next_bible_hash))
                connection.commit()
            except sqlite3.IntegrityError as error:
                raise SeriesContinuityConflict("episode continuity already exists") from error
        return {
            "schema_version": EPISODE_CONTINUITY_VERSION,
            "series_id": series, "episode_id": episode, "ordinal": ordinal,
            "state_before": before, "event_summary": event_summary.strip(), "state_after": after,
            "director_plan_hash": director_plan_hash, "pack_proposal_hash": pack_proposal_hash,
            "previous_episode_hash": previous_hash, "episode_hash": episode_hash,
            "bible": self.get_bible(owner, series),
        }


__all__ = [
    "EPISODE_CONTINUITY_VERSION", "SERIES_BIBLE_VERSION",
    "SeriesContinuityConflict", "SeriesContinuityStore",
]
