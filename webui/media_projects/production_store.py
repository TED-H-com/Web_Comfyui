"""Owner-scoped append-only persistence for hash-chained production states."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Mapping

from .contracts import ProductionPack, parse_production_pack
from .production_state import ProductionState, parse_production_state


class ProductionStoreConflict(RuntimeError):
    pass


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS production_state_revisions (
    owner_user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    state_hash TEXT NOT NULL UNIQUE,
    previous_state_hash TEXT,
    pack_hash TEXT NOT NULL,
    phase TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(owner_user_id, project_id, revision),
    UNIQUE(owner_user_id, project_id, state_hash)
);
CREATE INDEX IF NOT EXISTS production_state_latest
ON production_state_revisions(owner_user_id, project_id, revision DESC);
CREATE TABLE IF NOT EXISTS production_packs (
    owner_user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    pack_hash TEXT NOT NULL,
    pack_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(owner_user_id, project_id),
    UNIQUE(owner_user_id, project_id, pack_hash)
);
CREATE TABLE IF NOT EXISTS production_preparations (
    owner_user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    preparation_hash TEXT NOT NULL,
    production_state_hash TEXT NOT NULL,
    preparation_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(owner_user_id, project_id, preparation_hash),
    UNIQUE(preparation_hash)
);
CREATE TABLE IF NOT EXISTS production_preparation_contexts (
    owner_user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    preparation_hash TEXT NOT NULL
        REFERENCES production_preparations(preparation_hash),
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(owner_user_id, project_id, preparation_hash)
);
CREATE TABLE IF NOT EXISTS production_execution_assets (
    owner_user_id TEXT NOT NULL,
    execution_asset_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    preparation_hash TEXT NOT NULL
        REFERENCES production_preparations(preparation_hash),
    projection_id TEXT NOT NULL,
    asset_version_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    PRIMARY KEY(owner_user_id, execution_asset_id),
    UNIQUE(owner_user_id, project_id, preparation_hash, field_name, projection_id,
           asset_version_id)
);
CREATE TABLE IF NOT EXISTS production_edit_receipts (
    owner_user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    production_state_hash TEXT NOT NULL,
    timeline_hash TEXT NOT NULL,
    receipt_hash TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL,
    output_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(owner_user_id, project_id, production_state_hash)
);
CREATE TABLE IF NOT EXISTS production_delivery_reviews (
    owner_user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    receipt_hash TEXT NOT NULL REFERENCES production_edit_receipts(receipt_hash),
    decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
    note TEXT NOT NULL,
    review_hash TEXT NOT NULL UNIQUE,
    review_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(owner_user_id, project_id, receipt_hash)
);
CREATE TRIGGER IF NOT EXISTS production_state_no_update
BEFORE UPDATE ON production_state_revisions
BEGIN SELECT RAISE(ABORT, 'production state is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_state_no_delete
BEFORE DELETE ON production_state_revisions
BEGIN SELECT RAISE(ABORT, 'production state is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_pack_no_update
BEFORE UPDATE ON production_packs
BEGIN SELECT RAISE(ABORT, 'production pack is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_pack_no_delete
BEFORE DELETE ON production_packs
BEGIN SELECT RAISE(ABORT, 'production pack is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_preparation_no_update
BEFORE UPDATE ON production_preparations
BEGIN SELECT RAISE(ABORT, 'production preparation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_preparation_no_delete
BEFORE DELETE ON production_preparations
BEGIN SELECT RAISE(ABORT, 'production preparation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_preparation_context_no_update
BEFORE UPDATE ON production_preparation_contexts
BEGIN SELECT RAISE(ABORT, 'production preparation context is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_preparation_context_no_delete
BEFORE DELETE ON production_preparation_contexts
BEGIN SELECT RAISE(ABORT, 'production preparation context is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_execution_asset_no_update
BEFORE UPDATE ON production_execution_assets
BEGIN SELECT RAISE(ABORT, 'production execution asset is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_execution_asset_no_delete
BEFORE DELETE ON production_execution_assets
BEGIN SELECT RAISE(ABORT, 'production execution asset is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_edit_receipt_no_update
BEFORE UPDATE ON production_edit_receipts
BEGIN SELECT RAISE(ABORT, 'production edit receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_edit_receipt_no_delete
BEFORE DELETE ON production_edit_receipts
BEGIN SELECT RAISE(ABORT, 'production edit receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_delivery_review_no_update
BEFORE UPDATE ON production_delivery_reviews
BEGIN SELECT RAISE(ABORT, 'production delivery review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS production_delivery_review_no_delete
BEFORE DELETE ON production_delivery_reviews
BEGIN SELECT RAISE(ABORT, 'production delivery review is immutable'); END;
"""


class ProductionStateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.path.parent.stat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid():
            raise ValueError("production store parent is unsafe")
        fd = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1
            ):
                raise ValueError("production store file is unsafe")
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)
        connection = self._connect()
        try:
            connection.executescript(_SCHEMA)
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @staticmethod
    def _encode(state: ProductionState) -> str:
        return json.dumps(
            state.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )

    @staticmethod
    def _decode(raw: str) -> ProductionState:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ProductionStoreConflict("stored production state is malformed") from error
        try:
            return parse_production_state(value)
        except (TypeError, ValueError, KeyError) as error:
            raise ProductionStoreConflict("stored production state failed integrity validation") from error

    def create(self, state: ProductionState) -> ProductionState:
        if not isinstance(state, ProductionState) or state.revision != 1 or state.previous_state_hash is not None:
            raise ValueError("initial production state is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO production_state_revisions(
                       owner_user_id, project_id, revision, state_hash,
                       previous_state_hash, pack_hash, phase, state_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state.owner_user_id, state.project_id, state.revision,
                    state.state_hash, state.previous_state_hash, state.pack_hash,
                    state.phase.value, self._encode(state),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProductionStoreConflict("production already exists or state identity collided") from error
        finally:
            connection.close()
        return self.get(state.owner_user_id, state.project_id)

    def create_with_pack(
        self, state: ProductionState, pack: ProductionPack,
    ) -> ProductionState:
        """Atomically persist one canonical pack and its initial state."""

        if (
            not isinstance(state, ProductionState) or not isinstance(pack, ProductionPack)
            or state.revision != 1 or state.previous_state_hash is not None
            or state.owner_user_id == "" or state.project_id != pack.project.project_id
            or state.pack_hash != pack.content_hash
        ):
            raise ValueError("initial production pack and state differ")
        pack_json = json.dumps(
            pack.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO production_packs(
                       owner_user_id, project_id, pack_hash, pack_json
                   ) VALUES (?, ?, ?, ?)""",
                (state.owner_user_id, state.project_id, state.pack_hash, pack_json),
            )
            connection.execute(
                """INSERT INTO production_state_revisions(
                       owner_user_id, project_id, revision, state_hash,
                       previous_state_hash, pack_hash, phase, state_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state.owner_user_id, state.project_id, state.revision,
                    state.state_hash, state.previous_state_hash, state.pack_hash,
                    state.phase.value, self._encode(state),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProductionStoreConflict("production already exists or pack identity collided") from error
        finally:
            connection.close()
        return self.get(state.owner_user_id, state.project_id)

    def get_pack(self, owner_user_id: str, project_id: str) -> ProductionPack:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT pack_hash, pack_json FROM production_packs
                    WHERE owner_user_id = ? AND project_id = ?""",
                (owner_user_id, project_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(project_id)
        try:
            raw = json.loads(str(row["pack_json"]))
            pack = parse_production_pack(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProductionStoreConflict("stored production pack failed integrity validation") from error
        if pack.project.project_id != project_id or pack.content_hash != row["pack_hash"]:
            raise ProductionStoreConflict("stored production pack index differs from its artifact")
        return pack

    def put_preparation(
        self,
        owner_user_id: str,
        project_id: str,
        *,
        shot_id: str,
        production_state_hash: str,
        preparation: Mapping[str, object],
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Idempotently persist one inert hash-bound preparation artifact."""

        if not isinstance(preparation, Mapping):
            raise TypeError("preparation must be a mapping")
        preparation_hash = preparation.get("preparation_hash")
        if (
            not isinstance(preparation_hash, str)
            or preparation.get("project_id") != project_id
            or preparation.get("shot_id") != shot_id
            or not self.contains_revision(
                owner_user_id, project_id,
                state_hash=production_state_hash,
                pack_hash=str(self.get_pack(owner_user_id, project_id).content_hash),
            )
        ):
            raise ValueError("production preparation identity is invalid")
        encoded = json.dumps(
            dict(preparation), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        context_encoded = None if context is None else json.dumps(
            dict(context), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM production_preparations
                    WHERE owner_user_id = ? AND project_id = ? AND preparation_hash = ?""",
                (owner_user_id, project_id, preparation_hash),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO production_preparations(
                           owner_user_id, project_id, shot_id, preparation_hash,
                           production_state_hash, preparation_json
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        owner_user_id, project_id, shot_id, preparation_hash,
                        production_state_hash, encoded,
                    ),
                )
            elif any(existing[key] != value for key, value in {
                "shot_id": shot_id,
                "production_state_hash": production_state_hash,
                "preparation_json": encoded,
            }.items()):
                raise ProductionStoreConflict("production preparation identity conflicts")
            if context_encoded is not None:
                stored_context = connection.execute(
                    """SELECT context_json FROM production_preparation_contexts
                        WHERE owner_user_id = ? AND project_id = ?
                          AND preparation_hash = ?""",
                    (owner_user_id, project_id, preparation_hash),
                ).fetchone()
                if stored_context is None:
                    connection.execute(
                        """INSERT INTO production_preparation_contexts(
                               owner_user_id, project_id, preparation_hash, context_json
                           ) VALUES (?, ?, ?, ?)""",
                        (owner_user_id, project_id, preparation_hash, context_encoded),
                    )
                elif stored_context["context_json"] != context_encoded:
                    raise ProductionStoreConflict(
                        "production preparation context conflicts"
                    )
                raw_inputs = context.get("inputs") if context is not None else None
                if not isinstance(raw_inputs, list):
                    raise ValueError("production preparation context inputs are invalid")
                for item in raw_inputs:
                    if not isinstance(item, Mapping):
                        raise ValueError("production execution asset is invalid")
                    execution_asset_id = item.get("execution_asset_id")
                    projection_id = item.get("projection_id")
                    asset_version_id = item.get("asset_version_id")
                    field_name = item.get("field_name")
                    if any(
                        not isinstance(value, str) or not value
                        for value in (
                            execution_asset_id, projection_id,
                            asset_version_id, field_name,
                        )
                    ):
                        raise ValueError("production execution asset is invalid")
                    existing_asset = connection.execute(
                        """SELECT * FROM production_execution_assets
                            WHERE owner_user_id = ? AND execution_asset_id = ?""",
                        (owner_user_id, execution_asset_id),
                    ).fetchone()
                    values = {
                        "project_id": project_id,
                        "preparation_hash": preparation_hash,
                        "projection_id": projection_id,
                        "asset_version_id": asset_version_id,
                        "field_name": field_name,
                    }
                    if existing_asset is None:
                        connection.execute(
                            """INSERT INTO production_execution_assets(
                                   owner_user_id, execution_asset_id, project_id,
                                   preparation_hash, projection_id,
                                   asset_version_id, field_name
                               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (
                                owner_user_id, execution_asset_id, project_id,
                                preparation_hash, projection_id,
                                asset_version_id, field_name,
                            ),
                        )
                    elif any(existing_asset[key] != value for key, value in values.items()):
                        raise ProductionStoreConflict(
                            "production execution asset conflicts"
                        )
            connection.commit()
        except ProductionStoreConflict:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProductionStoreConflict("production preparation identity collided") from error
        finally:
            connection.close()
        return self.get_preparation(owner_user_id, project_id, preparation_hash)

    def get_preparation_context(
        self, owner_user_id: str, project_id: str, preparation_hash: str,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT context_json FROM production_preparation_contexts
                    WHERE owner_user_id = ? AND project_id = ?
                      AND preparation_hash = ?""",
                (owner_user_id, project_id, preparation_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(preparation_hash)
        try:
            value = json.loads(str(row["context_json"]))
        except json.JSONDecodeError as error:
            raise ProductionStoreConflict(
                "stored production preparation context is malformed"
            ) from error
        if not isinstance(value, dict):
            raise ProductionStoreConflict(
                "stored production preparation context is malformed"
            )
        return value

    def get_execution_asset(
        self, owner_user_id: str, execution_asset_id: str,
    ) -> dict[str, str]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM production_execution_assets
                    WHERE owner_user_id = ? AND execution_asset_id = ?""",
                (owner_user_id, execution_asset_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(execution_asset_id)
        return {
            key: str(row[key]) for key in (
                "owner_user_id", "execution_asset_id", "project_id",
                "preparation_hash", "projection_id", "asset_version_id",
                "field_name",
            )
        }

    def put_edit_receipt(
        self,
        owner_user_id: str,
        project_id: str,
        production_state_hash: str,
        receipt: Mapping[str, object],
        *,
        output_path: Path,
    ) -> dict[str, object]:
        receipt_hash = receipt.get("receipt_hash") if isinstance(receipt, Mapping) else None
        timeline_hash = receipt.get("timeline_hash") if isinstance(receipt, Mapping) else None
        if (
            not isinstance(receipt_hash, str) or not isinstance(timeline_hash, str)
            or not self.contains_revision(
                owner_user_id, project_id, state_hash=production_state_hash,
                pack_hash=self.get_pack(owner_user_id, project_id).content_hash,
            )
        ):
            raise ValueError("production edit receipt identity is invalid")
        encoded = json.dumps(
            dict(receipt), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        private_path = str(Path(output_path).absolute())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM production_edit_receipts
                    WHERE owner_user_id = ? AND project_id = ?
                      AND production_state_hash = ?""",
                (owner_user_id, project_id, production_state_hash),
            ).fetchone()
            values = {
                "timeline_hash": timeline_hash,
                "receipt_hash": receipt_hash,
                "receipt_json": encoded,
                "output_path": private_path,
            }
            if row is None:
                connection.execute(
                    """INSERT INTO production_edit_receipts(
                           owner_user_id, project_id, production_state_hash,
                           timeline_hash, receipt_hash, receipt_json, output_path
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        owner_user_id, project_id, production_state_hash,
                        timeline_hash, receipt_hash, encoded, private_path,
                    ),
                )
            elif any(row[key] != value for key, value in values.items()):
                raise ProductionStoreConflict("production edit receipt conflicts")
            connection.commit()
        except ProductionStoreConflict:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProductionStoreConflict("production edit receipt collided") from error
        finally:
            connection.close()
        return self.get_edit_receipt(owner_user_id, project_id, production_state_hash)

    def get_edit_receipt(
        self, owner_user_id: str, project_id: str, production_state_hash: str,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT receipt_json FROM production_edit_receipts
                    WHERE owner_user_id = ? AND project_id = ?
                      AND production_state_hash = ?""",
                (owner_user_id, project_id, production_state_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(production_state_hash)
        value = json.loads(str(row["receipt_json"]))
        if not isinstance(value, dict):
            raise ProductionStoreConflict("production edit receipt is malformed")
        return value

    def get_edit_output_path(
        self, owner_user_id: str, project_id: str, production_state_hash: str,
    ) -> Path:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT output_path FROM production_edit_receipts
                    WHERE owner_user_id = ? AND project_id = ?
                      AND production_state_hash = ?""",
                (owner_user_id, project_id, production_state_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(production_state_hash)
        return Path(str(row["output_path"]))

    def get_edit_receipt_by_hash(
        self, owner_user_id: str, project_id: str, receipt_hash: str,
    ) -> tuple[dict[str, object], Path]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT receipt_json, output_path FROM production_edit_receipts
                    WHERE owner_user_id = ? AND project_id = ? AND receipt_hash = ?""",
                (owner_user_id, project_id, receipt_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(receipt_hash)
        value = json.loads(str(row["receipt_json"]))
        if not isinstance(value, dict) or value.get("receipt_hash") != receipt_hash:
            raise ProductionStoreConflict("production edit receipt index differs")
        return value, Path(str(row["output_path"]))

    def get_latest_edit_receipt_for_project(
        self, owner_user_id: str, project_id: str,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT receipt_json FROM production_edit_receipts
                    WHERE owner_user_id = ? AND project_id = ?
                    ORDER BY rowid DESC LIMIT 1""",
                (owner_user_id, project_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(project_id)
        value = json.loads(str(row["receipt_json"]))
        if not isinstance(value, dict):
            raise ProductionStoreConflict("production edit receipt is malformed")
        return value

    def put_delivery_review(
        self, owner_user_id: str, project_id: str,
        receipt_hash: str, review: Mapping[str, object],
    ) -> dict[str, object]:
        review_hash = review.get("review_hash") if isinstance(review, Mapping) else None
        decision = review.get("decision") if isinstance(review, Mapping) else None
        note = review.get("note") if isinstance(review, Mapping) else None
        if (
            not isinstance(review_hash, str) or decision not in {"accepted", "rejected"}
            or not isinstance(note, str) or len(note) > 1000
        ):
            raise ValueError("production delivery review is invalid")
        encoded = json.dumps(
            dict(review), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute(
                """SELECT 1 FROM production_edit_receipts
                    WHERE owner_user_id = ? AND project_id = ? AND receipt_hash = ?""",
                (owner_user_id, project_id, receipt_hash),
            ).fetchone()
            if receipt is None:
                raise KeyError(receipt_hash)
            row = connection.execute(
                """SELECT * FROM production_delivery_reviews
                    WHERE owner_user_id = ? AND project_id = ? AND receipt_hash = ?""",
                (owner_user_id, project_id, receipt_hash),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO production_delivery_reviews(
                           owner_user_id, project_id, receipt_hash, decision,
                           note, review_hash, review_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        owner_user_id, project_id, receipt_hash, decision,
                        note, review_hash, encoded,
                    ),
                )
            elif row["review_json"] != encoded:
                raise ProductionStoreConflict("production delivery review conflicts")
            connection.commit()
        except (KeyError, ProductionStoreConflict):
            connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProductionStoreConflict("production delivery review collided") from error
        finally:
            connection.close()
        return self.get_delivery_review(owner_user_id, project_id, receipt_hash)

    def get_delivery_review(
        self, owner_user_id: str, project_id: str, receipt_hash: str,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT review_json FROM production_delivery_reviews
                    WHERE owner_user_id = ? AND project_id = ? AND receipt_hash = ?""",
                (owner_user_id, project_id, receipt_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(receipt_hash)
        value = json.loads(str(row["review_json"]))
        if not isinstance(value, dict):
            raise ProductionStoreConflict("production delivery review is malformed")
        return value

    def get_preparation(
        self, owner_user_id: str, project_id: str, preparation_hash: str,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM production_preparations
                    WHERE owner_user_id = ? AND project_id = ? AND preparation_hash = ?""",
                (owner_user_id, project_id, preparation_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(preparation_hash)
        try:
            value = json.loads(str(row["preparation_json"]))
        except json.JSONDecodeError as error:
            raise ProductionStoreConflict("stored production preparation is malformed") from error
        if (
            not isinstance(value, dict)
            or value.get("project_id") != project_id
            or value.get("shot_id") != row["shot_id"]
            or value.get("preparation_hash") != preparation_hash
        ):
            raise ProductionStoreConflict("stored production preparation index differs")
        return value

    def get_latest_preparation(
        self, owner_user_id: str, project_id: str, shot_id: str,
    ) -> dict[str, object]:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT preparation_hash FROM production_preparations
                    WHERE owner_user_id = ? AND project_id = ? AND shot_id = ?
                    ORDER BY rowid DESC LIMIT 1""",
                (owner_user_id, project_id, shot_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(shot_id)
        return self.get_preparation(
            owner_user_id, project_id, str(row["preparation_hash"])
        )

    def append(self, state: ProductionState, *, expected_previous_hash: str) -> ProductionState:
        if (
            not isinstance(state, ProductionState) or state.revision <= 1
            or state.previous_state_hash != expected_previous_hash
        ):
            raise ValueError("production state compare-and-swap contract is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """SELECT * FROM production_state_revisions
                    WHERE owner_user_id = ? AND project_id = ?
                    ORDER BY revision DESC LIMIT 1""",
                (state.owner_user_id, state.project_id),
            ).fetchone()
            if (
                latest is None or latest["state_hash"] != expected_previous_hash
                or int(latest["revision"]) + 1 != state.revision
                or latest["pack_hash"] != state.pack_hash
            ):
                raise ProductionStoreConflict("production state changed since it was loaded")
            connection.execute(
                """INSERT INTO production_state_revisions(
                       owner_user_id, project_id, revision, state_hash,
                       previous_state_hash, pack_hash, phase, state_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state.owner_user_id, state.project_id, state.revision,
                    state.state_hash, state.previous_state_hash, state.pack_hash,
                    state.phase.value, self._encode(state),
                ),
            )
            connection.commit()
        except ProductionStoreConflict:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProductionStoreConflict("production state append conflicted") from error
        finally:
            connection.close()
        return self.get(state.owner_user_id, state.project_id)

    def get(self, owner_user_id: str, project_id: str) -> ProductionState:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM production_state_revisions
                    WHERE owner_user_id = ? AND project_id = ?
                    ORDER BY revision DESC LIMIT 1""",
                (owner_user_id, project_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(project_id)
        state = self._decode(str(row["state_json"]))
        if any(
            row[key] != value
            for key, value in {
                "owner_user_id": state.owner_user_id,
                "project_id": state.project_id,
                "revision": state.revision,
                "state_hash": state.state_hash,
                "previous_state_hash": state.previous_state_hash,
                "pack_hash": state.pack_hash,
                "phase": state.phase.value,
            }.items()
        ):
            raise ProductionStoreConflict("stored production index differs from its artifact")
        return state

    def list_latest(
        self, owner_user_id: str, *, limit: int = 50,
    ) -> list[tuple[ProductionState, ProductionPack, str]]:
        """List an owner's latest immutable production revisions, newest first."""

        if not isinstance(owner_user_id, str) or not owner_user_id:
            raise ValueError("production owner is invalid")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("production list limit is invalid")
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT states.*, packs.pack_json, packs.pack_hash AS stored_pack_hash,
                          states.created_at AS updated_at
                     FROM production_state_revisions AS states
                     JOIN (
                         SELECT project_id, MAX(revision) AS revision
                           FROM production_state_revisions
                          WHERE owner_user_id = ?
                          GROUP BY project_id
                     ) AS latest
                       ON latest.project_id = states.project_id
                      AND latest.revision = states.revision
                     JOIN production_packs AS packs
                       ON packs.owner_user_id = states.owner_user_id
                      AND packs.project_id = states.project_id
                    WHERE states.owner_user_id = ?
                    ORDER BY states.created_at DESC, states.project_id ASC
                    LIMIT ?""",
                (owner_user_id, owner_user_id, limit),
            ).fetchall()
        finally:
            connection.close()
        result: list[tuple[ProductionState, ProductionPack, str]] = []
        for row in rows:
            state = self._decode(str(row["state_json"]))
            try:
                pack = parse_production_pack(json.loads(str(row["pack_json"])))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ProductionStoreConflict(
                    "stored production pack failed integrity validation"
                ) from error
            if any(row[key] != value for key, value in {
                "owner_user_id": state.owner_user_id,
                "project_id": state.project_id,
                "revision": state.revision,
                "state_hash": state.state_hash,
                "previous_state_hash": state.previous_state_hash,
                "pack_hash": state.pack_hash,
                "phase": state.phase.value,
            }.items()) or (
                state.owner_user_id != owner_user_id
                or pack.project.project_id != state.project_id
                or pack.content_hash != state.pack_hash
                or row["stored_pack_hash"] != state.pack_hash
            ):
                raise ProductionStoreConflict(
                    "stored production list index differs from its artifact"
                )
            updated_at = str(row["updated_at"])
            if not updated_at or len(updated_at) > 40:
                raise ProductionStoreConflict("stored production timestamp is invalid")
            result.append((state, pack, updated_at))
        return result

    def get_revision(
        self, owner_user_id: str, project_id: str, state_hash: str,
    ) -> ProductionState:
        """Read one exact immutable owner/project revision by its content hash."""

        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM production_state_revisions
                    WHERE owner_user_id = ? AND project_id = ? AND state_hash = ?""",
                (owner_user_id, project_id, state_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(state_hash)
        state = self._decode(str(row["state_json"]))
        if any(
            row[key] != value
            for key, value in {
                "owner_user_id": state.owner_user_id,
                "project_id": state.project_id,
                "revision": state.revision,
                "state_hash": state.state_hash,
                "previous_state_hash": state.previous_state_hash,
                "pack_hash": state.pack_hash,
                "phase": state.phase.value,
            }.items()
        ):
            raise ProductionStoreConflict("stored production index differs from its artifact")
        return state

    def contains_revision(
        self,
        owner_user_id: str,
        project_id: str,
        *,
        state_hash: str,
        pack_hash: str,
    ) -> bool:
        """Prove a signed generation binding names one immutable project revision."""

        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT state_json FROM production_state_revisions
                    WHERE owner_user_id = ? AND project_id = ?
                      AND state_hash = ? AND pack_hash = ?""",
                (owner_user_id, project_id, state_hash, pack_hash),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return False
        state = self._decode(str(row["state_json"]))
        return (
            state.owner_user_id == owner_user_id
            and state.project_id == project_id
            and state.state_hash == state_hash
            and state.pack_hash == pack_hash
        )


__all__ = ["ProductionStateStore", "ProductionStoreConflict"]
