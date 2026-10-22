"""Owner-scoped real-person rights records for production-pack compilation.

The store never accepts or returns evidence bytes, paths, URLs, or free-form
legal text.  It persists only an opaque evidence identifier and its SHA-256 so
later production packs can bind a private record without exposing it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CONSENT_STATES = frozenset({"self_owned", "documented"})
_USAGE_SCOPES = frozenset({"private", "internal", "social", "commercial"})
_PROVIDERS = frozenset({"local_comfy", "autoip"})
_CAPABILITIES = frozenset({
    "text_to_image", "image_to_image", "image_to_video", "text_to_video",
    "lip_sync", "dance", "multi_subject_video", "text_to_speech", "voice_clone",
})


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _values(values: Iterable[str], allowed: frozenset[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a collection")
    result = tuple(sorted(set(values)))
    if not result or len(result) > 32 or any(item not in allowed for item in result):
        raise ValueError(f"{label} is invalid")
    return result


@dataclass(frozen=True, slots=True)
class CharacterRightsRecord:
    owner_user_id: str
    character_id: str
    consent_state: str
    consent_ref: str
    evidence_id: str
    evidence_sha256: str
    allowed_provider_ids: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    allowed_usage_scopes: tuple[str, ...]
    revoked: bool = False
    revision: int = 1

    def __post_init__(self) -> None:
        for name in ("owner_user_id", "character_id", "consent_ref", "evidence_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        if self.consent_state not in _CONSENT_STATES:
            raise ValueError("consent_state is invalid")
        if not isinstance(self.evidence_sha256, str) or _HASH.fullmatch(self.evidence_sha256) is None:
            raise ValueError("evidence_sha256 is invalid")
        object.__setattr__(self, "allowed_provider_ids", _values(self.allowed_provider_ids, _PROVIDERS, "allowed_provider_ids"))
        object.__setattr__(self, "allowed_capabilities", _values(self.allowed_capabilities, _CAPABILITIES, "allowed_capabilities"))
        object.__setattr__(self, "allowed_usage_scopes", _values(self.allowed_usage_scopes, _USAGE_SCOPES, "allowed_usage_scopes"))
        if not isinstance(self.revoked, bool):
            raise ValueError("revoked must be boolean")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("revision is invalid")

    def public_summary(self) -> dict[str, object]:
        """Return selection scope without exposing evidence identity or digest."""

        return {
            "schema_version": "castle.character-rights-summary.v1",
            "character_id": self.character_id,
            "consent_state": "revoked" if self.revoked else self.consent_state,
            "allowed_provider_ids": list(self.allowed_provider_ids),
            "allowed_capabilities": list(self.allowed_capabilities),
            "allowed_usage_scopes": list(self.allowed_usage_scopes),
            "revision": self.revision,
        }


class CharacterRightsStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.database_path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise ValueError("character rights parent must be owner-only")
        if not self.database_path.exists():
            descriptor = os.open(
                self.database_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
        info = self.database_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("character rights database must be owner-only")
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS character_rights_records (
                    owner_user_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    consent_state TEXT NOT NULL,
                    consent_ref TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    allowed_provider_ids_json TEXT NOT NULL,
                    allowed_capabilities_json TEXT NOT NULL,
                    allowed_usage_scopes_json TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (owner_user_id, character_id)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> CharacterRightsRecord:
        return CharacterRightsRecord(
            owner_user_id=row["owner_user_id"], character_id=row["character_id"],
            consent_state=row["consent_state"], consent_ref=row["consent_ref"],
            evidence_id=row["evidence_id"], evidence_sha256=row["evidence_sha256"],
            allowed_provider_ids=tuple(json.loads(row["allowed_provider_ids_json"])),
            allowed_capabilities=tuple(json.loads(row["allowed_capabilities_json"])),
            allowed_usage_scopes=tuple(json.loads(row["allowed_usage_scopes_json"])),
            revoked=bool(row["revoked"]), revision=int(row["revision"]),
        )

    def get(self, owner_user_id: str, character_id: str) -> CharacterRightsRecord | None:
        owner = _id(owner_user_id, "owner_user_id")
        character = _id(character_id, "character_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM character_rights_records WHERE owner_user_id = ? AND character_id = ?",
                (owner, character),
            ).fetchone()
        return self._record(row) if row is not None else None

    def put(self, record: CharacterRightsRecord, *, expected_revision: int | None = None) -> CharacterRightsRecord:
        if not isinstance(record, CharacterRightsRecord) or record.revoked:
            raise ValueError("an active CharacterRightsRecord is required")
        now = int(time.time())
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision, created_at FROM character_rights_records WHERE owner_user_id = ? AND character_id = ?",
                (record.owner_user_id, record.character_id),
            ).fetchone()
            observed = int(current["revision"]) if current is not None else 0
            if expected_revision is not None and expected_revision != observed:
                raise RuntimeError("character rights revision changed")
            revision = observed + 1
            created = int(current["created_at"]) if current is not None else now
            values = (
                record.owner_user_id, record.character_id, record.consent_state,
                record.consent_ref, record.evidence_id, record.evidence_sha256,
                json.dumps(record.allowed_provider_ids, separators=(",", ":")),
                json.dumps(record.allowed_capabilities, separators=(",", ":")),
                json.dumps(record.allowed_usage_scopes, separators=(",", ":")),
                revision, created, now,
            )
            connection.execute("""
                INSERT INTO character_rights_records (
                    owner_user_id, character_id, consent_state, consent_ref,
                    evidence_id, evidence_sha256, allowed_provider_ids_json,
                    allowed_capabilities_json, allowed_usage_scopes_json,
                    revoked, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(owner_user_id, character_id) DO UPDATE SET
                    consent_state=excluded.consent_state, consent_ref=excluded.consent_ref,
                    evidence_id=excluded.evidence_id, evidence_sha256=excluded.evidence_sha256,
                    allowed_provider_ids_json=excluded.allowed_provider_ids_json,
                    allowed_capabilities_json=excluded.allowed_capabilities_json,
                    allowed_usage_scopes_json=excluded.allowed_usage_scopes_json,
                    revoked=0, revision=excluded.revision, updated_at=excluded.updated_at
            """, values)
            connection.commit()
        result = self.get(record.owner_user_id, record.character_id)
        assert result is not None
        return result

    def revoke(self, owner_user_id: str, character_id: str, *, expected_revision: int) -> CharacterRightsRecord:
        owner = _id(owner_user_id, "owner_user_id")
        character = _id(character_id, "character_id")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("expected_revision is invalid")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute("""
                UPDATE character_rights_records
                   SET revoked = 1, revision = revision + 1, updated_at = ?
                 WHERE owner_user_id = ? AND character_id = ? AND revision = ? AND revoked = 0
            """, (int(time.time()), owner, character, expected_revision)).rowcount
            if changed != 1:
                raise RuntimeError("character rights revision changed")
            connection.commit()
        result = self.get(owner, character)
        assert result is not None
        return result


__all__ = ["CharacterRightsRecord", "CharacterRightsStore"]
