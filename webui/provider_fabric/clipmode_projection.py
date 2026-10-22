"""Atomic metadata projection for validated ClipMode export bundles.

This module is the deliberately narrow bridge between :mod:`clipmode_import`
and :class:`ProviderFabricStore`.  It performs no network requests, never uses
browser cookies, and does not extract bundled media bytes.  A caller must first
obtain a deterministic dry-run plan and then explicitly confirm the exact plan,
source semantic hash, provider scope, and account binding at commit time.

The store intentionally has no public multi-record transaction API.  Rather
than pretending that a sequence of its single-operation methods is atomic, the
bridge opens the store's SQLite path directly, repeats every precondition under
``BEGIN IMMEDIATE``, and commits the complete metadata projection in one
transaction.  This wrapper is coupled only to explicitly reviewed store schemas
and fails closed if that contract changes; it should move behind a public store
unit-of-work API when one is introduced.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .clipmode_import import ClipModeImportBundle, ImportedCharacterRecord, ImportedMediaRecord
from .store import ProviderFabricStore, SCHEMA_VERSION


BRIDGE_VERSION = 1
# Projection reads are coupled to the exact reviewed store contract.  Legacy
# writes remain disabled until the owner-aware materializer can create the
# schema-9 receipt graph atomically; the schema-16 catalog/classification,
# history-adoption receipt fields, AutoIP concurrency-index removal, and
# reconciliation-only task transition plus character model bindings are
# read-only to this bridge and do not re-authorize the old write path. Unknown
# future schemas must still fail closed, and the old unowned ``_apply`` path
# remains disabled.
SUPPORTED_READ_STORE_SCHEMAS = frozenset({16})
SUPPORTED_LEGACY_WRITE_STORE_SCHEMAS: frozenset[int] = frozenset()
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "secret_value",
        "session",
        "session_cookie",
        "token",
        "webhook_secret",
        "xsrf_token",
    }
)
_SECRET_SUFFIXES = ("_api_key", "_cookie", "_password", "_secret", "_token")
_STORED_VISUAL_CLASSES = frozenset({"anime", "realistic", "illustration", "unknown"})


class ClipModeProjectionError(RuntimeError):
    """Base class for sanitized projection failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProjectionConflictError(ClipModeProjectionError):
    """The dry run or current database state is not safe to commit."""


class ProjectionConfirmationError(ClipModeProjectionError):
    """A caller did not confirm the exact dry-run/account/source binding."""


@dataclass(frozen=True, slots=True)
class ProjectionAction:
    """One deterministic, public, credential-free plan operation."""

    action: str
    resource: str
    key: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "resource": self.resource,
            "key": self.key,
            "code": self.code,
        }


@dataclass(frozen=True, slots=True)
class ProjectionConflict:
    resource: str
    key: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "resource": self.resource,
            "key": self.key,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ClipModeProjectionPlan:
    bridge_version: int
    plan_hash: str
    source_semantic_sha256: str
    account_id: str
    provider_scope: str
    account_binding_sha256: str
    account_requirement: Mapping[str, Any]
    mapping_sha256: str
    counts: Mapping[str, int]
    source_duplicates: int
    actions: tuple[ProjectionAction, ...]
    conflicts: tuple[ProjectionConflict, ...]

    @property
    def ready(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridgeVersion": self.bridge_version,
            "planHash": self.plan_hash,
            "sourceSemanticSha256": self.source_semantic_sha256,
            "accountId": self.account_id,
            "providerScope": self.provider_scope,
            "accountBindingSha256": self.account_binding_sha256,
            "accountRequirement": dict(self.account_requirement),
            "mappingSha256": self.mapping_sha256,
            "ready": self.ready,
            "counts": dict(self.counts),
            "sourceDuplicates": self.source_duplicates,
            "actions": [item.to_dict() for item in self.actions],
            "conflicts": [item.to_dict() for item in self.conflicts],
        }


@dataclass(frozen=True, slots=True)
class ClipModeProjectionResult:
    plan_hash: str
    source_semantic_sha256: str
    account_id: str
    provider_scope: str
    account_binding_sha256: str
    committed: bool
    replayed: bool
    counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "planHash": self.plan_hash,
            "sourceSemanticSha256": self.source_semantic_sha256,
            "accountId": self.account_id,
            "providerScope": self.provider_scope,
            "accountBindingSha256": self.account_binding_sha256,
            "committed": self.committed,
            "replayed": self.replayed,
            "counts": dict(self.counts),
        }


@dataclass(frozen=True, slots=True)
class _DesiredCharacter:
    key: str
    canonical_id: str
    external_id: str
    display_name: str
    visual_class: str
    link_id: str
    link_metadata: Mapping[str, Any]
    mapping_explicit: bool


@dataclass(frozen=True, slots=True)
class _DesiredProjection:
    key: str
    projection_id: str
    external_id: str
    media_kind: str
    visual_class: str
    metadata: Mapping[str, Any]
    remote_created_at: str | None


@dataclass(frozen=True, slots=True)
class _DesiredVersion:
    key: str
    version_id: str
    projection_id: str
    provider_version_id: str
    source_uri: str
    mime_type: str
    byte_size: int | None
    sha256: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _DesiredState:
    characters: tuple[_DesiredCharacter, ...]
    projections: tuple[_DesiredProjection, ...]
    versions: tuple[_DesiredVersion, ...]
    asset_character_links: tuple[tuple[str, str, str], ...]
    lineage: tuple[tuple[str, str, str, str], ...]


def _canonical_json(value: Any) -> str:
    _assert_public(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("projection data is not canonical JSON") from error


def _sha(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _secret_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _assert_public(value: Any, path: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _secret_key(key):
                raise ValueError(f"{path} contains a forbidden secret field")
            _assert_public(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_public(child, f"{path}[{index}]")


def _stable_id(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return normalized


def _digest_id(prefix: str, *parts: str) -> str:
    return f"clipmode.{prefix}:{_sha(list(parts))[:48]}"


def _binding_hash(account_id: str, provider_scope: str) -> str:
    return _sha(["clipmode", account_id, provider_scope])


def _mapping(character_mapping: Mapping[str, str] | None) -> dict[str, str]:
    if character_mapping is None:
        return {}
    if not isinstance(character_mapping, Mapping):
        raise ValueError("character_mapping must be a mapping")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in character_mapping.items():
        key = _stable_id(raw_key, "imported character record ID")
        value = _stable_id(raw_value, "canonical character ID")
        if key in normalized:
            raise ValueError("character_mapping contains a duplicate record ID")
        normalized[key] = value
    return normalized


def _validate_bundle(bundle: ClipModeImportBundle) -> None:
    """Defend the bridge boundary even if a caller hand-built the dataclass."""

    provenance = bundle.provenance
    if provenance.provider_id != "clipmode":
        raise ValueError("bundle provenance provider must be clipmode")
    _stable_id(provenance.provider_scope, "bundle provider_scope")
    if not _SHA256_RE.fullmatch(provenance.semantic_sha256):
        raise ValueError("bundle semantic hash is invalid")
    records: Sequence[Any] = (*bundle.characters, *bundle.media)
    for item in records:
        _stable_id(item.record_id, "bundle record_id")
        if item.provider_id != "clipmode" or item.provider_scope != provenance.provider_scope:
            raise ValueError("bundle record provider binding is inconsistent")
        if item.visual_class.value not in _STORED_VISUAL_CLASSES:
            raise ValueError("bundle record visual class is invalid")
        if not _SHA256_RE.fullmatch(item.entry_sha256):
            raise ValueError("bundle record entry hash is invalid")
    for edge in bundle.lineage:
        _stable_id(edge.edge_id, "bundle lineage edge_id")
        if not _SHA256_RE.fullmatch(edge.entry_sha256):
            raise ValueError("bundle lineage entry hash is invalid")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _connect(path: Path, *, readonly: bool, timeout: float = 10.0) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            timeout=timeout,
            isolation_level=None,
            uri=True,
        )
    else:
        connection = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    if not readonly:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


@contextmanager
def _read_snapshot(store: ProviderFabricStore) -> Iterator[sqlite3.Connection]:
    connection = _connect(Path(store.path), readonly=True)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.execute("ROLLBACK")
    finally:
        connection.close()


@contextmanager
def _write_transaction(store: ProviderFabricStore) -> Iterator[sqlite3.Connection]:
    connection = _connect(Path(store.path), readonly=False)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _store_read_contract(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION or version not in SUPPORTED_READ_STORE_SCHEMAS:
        raise ClipModeProjectionError(
            "store.schema_unsupported",
            "ClipMode projection requires the reviewed provider store schema",
        )


def _store_legacy_write_contract(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in SUPPORTED_LEGACY_WRITE_STORE_SCHEMAS:
        raise ClipModeProjectionError(
            "store.write_schema_unsupported",
            "Legacy ClipMode projection writes are disabled",
        )


def _latest_character(records: Sequence[ImportedCharacterRecord]) -> ImportedCharacterRecord:
    return max(
        records,
        key=lambda item: (
            item.updated_at or "",
            item.created_at or "",
            item.external_ref.version,
            item.record_id,
        ),
    )


def _latest_media(records: Sequence[ImportedMediaRecord]) -> ImportedMediaRecord:
    return max(
        records,
        key=lambda item: (item.created_at or "", item.external_ref.version, item.record_id),
    )


def _desired_state(
    connection: sqlite3.Connection,
    bundle: ClipModeImportBundle,
    account_id: str,
    mapping: Mapping[str, str],
) -> tuple[_DesiredState, list[ProjectionConflict]]:
    conflicts: list[ProjectionConflict] = []
    character_by_record = {item.record_id: item for item in bundle.characters}
    character_ids = set(character_by_record)
    unknown_mapping_keys = sorted(set(mapping) - character_ids)
    for record_id in unknown_mapping_keys:
        conflicts.append(
            ProjectionConflict(
                "character_mapping",
                record_id,
                "mapping.record_missing",
                "mapping references a character record outside this import",
            )
        )

    media_by_record = {item.record_id: item for item in bundle.media}
    media_groups: dict[str, list[ImportedMediaRecord]] = {}
    for item in bundle.media:
        media_groups.setdefault(item.external_ref.external_id, []).append(item)

    projection_by_record: dict[str, str] = {}
    projection_version_by_record: dict[str, str] = {}
    projections: list[_DesiredProjection] = []
    versions: list[_DesiredVersion] = []
    for external_id, records in sorted(media_groups.items()):
        key = min(item.record_id for item in records)
        kinds = {item.kind.value for item in records}
        classes = {item.visual_class.value for item in records}
        if len(kinds) != 1 or len(classes) != 1:
            conflicts.append(
                ProjectionConflict(
                    "asset_projection",
                    key,
                    "asset.version_identity_mismatch",
                    "versions of one external asset disagree on kind or visual class",
                )
            )
        latest = _latest_media(records)
        existing = connection.execute(
            """SELECT projection_id FROM remote_asset_projections
               WHERE account_id = ? AND external_asset_id = ?""",
            (account_id, external_id),
        ).fetchone()
        projection_id = (
            existing["projection_id"]
            if existing is not None
            else _digest_id(
                "asset",
                account_id,
                bundle.provenance.provider_scope,
                external_id,
            )
        )
        import_metadata = {
            "provider": "clipmode",
            "providerScope": bundle.provenance.provider_scope,
            "latestRecordId": latest.record_id,
            "latestEntrySha256": latest.entry_sha256,
            "displayName": latest.display_name,
            "catalogOnly": latest.catalog_only,
            "projectionOnly": True,
            "sourceId": bundle.provenance.source_id,
            "sourceSemanticSha256": bundle.provenance.semantic_sha256,
            "sources": [
                {
                    "sourceId": bundle.provenance.source_id,
                    "semanticSha256": bundle.provenance.semantic_sha256,
                    "exportId": bundle.provenance.export_id,
                    "exportedAt": bundle.provenance.exported_at,
                }
            ],
        }
        projections.append(
            _DesiredProjection(
                key=key,
                projection_id=projection_id,
                external_id=external_id,
                media_kind=latest.kind.value,
                visual_class=latest.visual_class.value,
                metadata={"clipmodeImport": import_metadata},
                remote_created_at=latest.created_at,
            )
        )
        for item in sorted(records, key=lambda value: value.record_id):
            version_id = _digest_id(
                "version",
                account_id,
                bundle.provenance.provider_scope,
                item.external_ref.external_id,
                item.external_ref.version,
            )
            projection_by_record[item.record_id] = projection_id
            projection_version_by_record[item.record_id] = version_id
            version_metadata = {
                "clipmodeImport": {
                    "provider": "clipmode",
                    "providerScope": bundle.provenance.provider_scope,
                    "recordId": item.record_id,
                    "entrySha256": item.entry_sha256,
                    "availability": item.availability,
                    "archiveMember": item.archive_member,
                    "catalogOnly": item.catalog_only,
                    "projectionOnly": True,
                    "bytesExtracted": False,
                }
            }
            versions.append(
                _DesiredVersion(
                    key=item.record_id,
                    version_id=version_id,
                    projection_id=projection_id,
                    provider_version_id=item.external_ref.version,
                    source_uri=f"castle://remote-metadata/{version_id}",
                    mime_type=item.mime_type,
                    byte_size=item.byte_size,
                    sha256=item.content_sha256,
                    metadata=version_metadata,
                )
            )

    character_groups: dict[str, list[ImportedCharacterRecord]] = {}
    for item in bundle.characters:
        character_groups.setdefault(item.external_ref.external_id, []).append(item)

    character_target_by_record: dict[str, str] = {}
    characters: list[_DesiredCharacter] = []
    for external_id, records in sorted(character_groups.items()):
        key = min(item.record_id for item in records)
        classes = {item.visual_class.value for item in records}
        if len(classes) != 1:
            conflicts.append(
                ProjectionConflict(
                    "character",
                    key,
                    "character.version_class_mismatch",
                    "versions of one external character disagree on visual class",
                )
            )
        explicit_targets = {mapping[item.record_id] for item in records if item.record_id in mapping}
        if len(explicit_targets) > 1:
            conflicts.append(
                ProjectionConflict(
                    "character_mapping",
                    key,
                    "mapping.version_target_mismatch",
                    "versions of one external character map to different canonical characters",
                )
            )
        existing_link = connection.execute(
            """SELECT * FROM provider_character_links
               WHERE account_id = ? AND external_character_id = ?""",
            (account_id, external_id),
        ).fetchone()
        explicit_target = next(iter(explicit_targets), None)
        if existing_link is not None and explicit_target is not None:
            if existing_link["character_id"] != explicit_target:
                conflicts.append(
                    ProjectionConflict(
                        "character_mapping",
                        key,
                        "mapping.existing_link_mismatch",
                        "explicit mapping disagrees with the existing provider identity link",
                    )
                )
        if explicit_target is not None:
            canonical_id = explicit_target
        elif existing_link is not None:
            canonical_id = existing_link["character_id"]
        else:
            canonical_id = _digest_id(
                "character",
                account_id,
                bundle.provenance.provider_scope,
                external_id,
            )
        latest = _latest_character(records)
        profile_projection_id = (
            projection_by_record.get(latest.profile_media_record_id)
            if latest.profile_media_record_id is not None
            else None
        )
        profile_version_id = (
            projection_version_by_record.get(latest.profile_media_record_id)
            if latest.profile_media_record_id is not None
            else None
        )
        if latest.profile_media_record_id is not None and profile_projection_id is None:
            conflicts.append(
                ProjectionConflict(
                    "character",
                    key,
                    "character.profile_projection_missing",
                    "character profile media cannot be projected",
                )
            )
        link_metadata = {
            "clipmodeImport": {
                "provider": "clipmode",
                "providerScope": bundle.provenance.provider_scope,
                "displayName": latest.display_name,
                "externalModelId": latest.external_model_id,
                "catalogOnly": latest.catalog_only,
                "profileProjectionId": profile_projection_id,
                "profileVersionId": profile_version_id,
                "latestRecordId": latest.record_id,
                "sourceId": bundle.provenance.source_id,
                "sourceSemanticSha256": bundle.provenance.semantic_sha256,
                "sources": [
                    {
                        "sourceId": bundle.provenance.source_id,
                        "semanticSha256": bundle.provenance.semantic_sha256,
                        "exportId": bundle.provenance.export_id,
                        "exportedAt": bundle.provenance.exported_at,
                    }
                ],
                "versions": [
                    {
                        "recordId": item.record_id,
                        "providerVersionId": item.external_ref.version,
                        "entrySha256": item.entry_sha256,
                    }
                    for item in sorted(records, key=lambda value: value.record_id)
                ],
            }
        }
        link_id = (
            existing_link["link_id"]
            if existing_link is not None
            else _digest_id(
                "link",
                account_id,
                bundle.provenance.provider_scope,
                external_id,
            )
        )
        characters.append(
            _DesiredCharacter(
                key=key,
                canonical_id=canonical_id,
                external_id=external_id,
                display_name=latest.display_name,
                visual_class=latest.visual_class.value,
                link_id=link_id,
                link_metadata=link_metadata,
                mapping_explicit=explicit_target is not None,
            )
        )
        for item in records:
            character_target_by_record[item.record_id] = canonical_id

    asset_links: set[tuple[str, str, str]] = set()
    for media in bundle.media:
        projection_id = projection_by_record[media.record_id]
        for character_record_id in media.character_record_ids:
            canonical_id = character_target_by_record.get(character_record_id)
            if canonical_id is None:
                conflicts.append(
                    ProjectionConflict(
                        "asset_character_link",
                        media.record_id,
                        "asset.character_target_missing",
                        "asset character reference cannot be projected",
                    )
                )
                continue
            character_record = character_by_record[character_record_id]
            if character_record.visual_class.value != media.visual_class.value:
                conflicts.append(
                    ProjectionConflict(
                        "asset_character_link",
                        media.record_id,
                        "asset.character_class_mismatch",
                        "asset and linked character visual classes differ",
                    )
                )
            asset_links.add((media.record_id, projection_id, canonical_id))

    lineage: set[tuple[str, str, str, str]] = set()
    for edge in bundle.lineage:
        parent = projection_by_record.get(edge.parent_media_record_id)
        child = projection_by_record.get(edge.child_media_record_id)
        if parent is None or child is None:
            conflicts.append(
                ProjectionConflict(
                    "asset_lineage",
                    edge.edge_id,
                    "lineage.projection_missing",
                    "lineage endpoint cannot be projected",
                )
            )
        elif parent == child:
            conflicts.append(
                ProjectionConflict(
                    "asset_lineage",
                    edge.edge_id,
                    "lineage.version_collapse",
                    "version-level lineage collapses to one immutable asset projection",
                )
            )
        else:
            lineage.add((edge.edge_id, parent, child, edge.relationship))

    return (
        _DesiredState(
            characters=tuple(sorted(characters, key=lambda item: item.key)),
            projections=tuple(sorted(projections, key=lambda item: item.key)),
            versions=tuple(sorted(versions, key=lambda item: item.key)),
            asset_character_links=tuple(sorted(asset_links)),
            lineage=tuple(sorted(lineage)),
        ),
        conflicts,
    )


def _json_object(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ProjectionConflictError("store.metadata_invalid", "stored metadata is not an object")
    return parsed


def _merge_namespace(raw: str | None, desired: Mapping[str, Any]) -> dict[str, Any]:
    current = _json_object(raw) if raw is not None else {}
    merged = dict(current)
    current_namespace = current.get("clipmodeImport")
    current_namespace = dict(current_namespace) if isinstance(current_namespace, Mapping) else {}
    desired_namespace = dict(desired["clipmodeImport"])
    namespace = dict(desired_namespace)
    for field, identity_field in (("sources", "sourceId"), ("versions", "recordId")):
        combined: dict[str, Any] = {}
        for item in current_namespace.get(field, ()):
            if isinstance(item, Mapping) and isinstance(item.get(identity_field), str):
                combined[item[identity_field]] = dict(item)
        for item in desired_namespace.get(field, ()):
            if isinstance(item, Mapping) and isinstance(item.get(identity_field), str):
                combined[item[identity_field]] = dict(item)
        if combined:
            namespace[field] = [combined[key] for key in sorted(combined)]
    merged["clipmodeImport"] = namespace
    _assert_public(merged)
    return merged


def _version_history_conflict(raw: str, desired: Mapping[str, Any]) -> bool:
    current = _json_object(raw).get("clipmodeImport")
    if not isinstance(current, Mapping):
        return False
    existing = {
        item.get("recordId"): dict(item)
        for item in current.get("versions", ())
        if isinstance(item, Mapping) and isinstance(item.get("recordId"), str)
    }
    for item in desired["clipmodeImport"].get("versions", ()):
        old = existing.get(item.get("recordId"))
        if old is not None and old != dict(item):
            return True
    return False


def _add_action(
    actions: list[ProjectionAction],
    conflicts: list[ProjectionConflict],
    action: str,
    resource: str,
    key: str,
    code: str,
    message: str | None = None,
) -> None:
    actions.append(ProjectionAction(action, resource, key, code))
    if action == "conflict":
        conflicts.append(ProjectionConflict(resource, key, code, message or "projection conflict"))


def _path_exists(
    adjacency: Mapping[str, set[str]], start: str, target: str
) -> bool:
    pending = [start]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, ()))
    return False


def _observed_account_scopes(
    connection: sqlite3.Connection, account_id: str
) -> set[str]:
    scopes: set[str] = set()
    queries = (
        (
            "SELECT metadata_json FROM provider_character_links WHERE account_id = ?",
            (account_id,),
        ),
        (
            "SELECT metadata_json FROM remote_asset_projections WHERE account_id = ?",
            (account_id,),
        ),
    )
    for query, parameters in queries:
        for row in connection.execute(query, parameters):
            namespace = _json_object(row["metadata_json"]).get("clipmodeImport")
            if not isinstance(namespace, Mapping):
                continue
            if namespace.get("provider") != "clipmode":
                continue
            scope = namespace.get("providerScope")
            if not isinstance(scope, str) or not _ID_RE.fullmatch(scope):
                raise ClipModeProjectionError(
                    "store.scope_binding_invalid",
                    "stored ClipMode account scope binding is invalid",
                )
            scopes.add(scope)
    return scopes


def _plan_in_connection(
    connection: sqlite3.Connection,
    bundle: ClipModeImportBundle,
    account_id: str,
    mapping: Mapping[str, str],
) -> tuple[ClipModeProjectionPlan, _DesiredState]:
    _store_read_contract(connection)
    account_row = connection.execute(
        "SELECT account_id, provider_id, enabled FROM provider_accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    account_owner = connection.execute(
        """SELECT owner_user_id FROM provider_account_owners
           WHERE account_id = ?""",
        (account_id,),
    ).fetchone()
    account_exists = account_row is not None
    owner_scoped = account_owner is not None
    provider_matches = bool(account_row is not None and account_row["provider_id"] == "clipmode")
    observed_scopes = _observed_account_scopes(connection, account_id) if provider_matches else set()
    scope_compatible = not observed_scopes or observed_scopes == {
        bundle.provenance.provider_scope
    }
    account_requirement = {
        "provider": "clipmode",
        "exists": account_exists,
        "providerMatches": provider_matches,
        "enabled": bool(account_row["enabled"]) if account_row is not None else False,
        "ownerScoped": owner_scoped,
        "scopeObserved": bool(observed_scopes),
        "scopeMatches": scope_compatible,
        "scopeBound": provider_matches and scope_compatible,
    }
    actions: list[ProjectionAction] = []
    conflicts: list[ProjectionConflict] = []
    if not account_exists:
        _add_action(
            actions,
            conflicts,
            "conflict",
            "provider_account",
            account_id,
            "account.missing",
            "the selected provider account does not exist",
        )
    elif not provider_matches:
        _add_action(
            actions,
            conflicts,
            "conflict",
            "provider_account",
            account_id,
            "account.provider_mismatch",
            "the selected account is not bound to ClipMode",
        )
    elif owner_scoped:
        _add_action(
            actions,
            conflicts,
            "conflict",
            "provider_account",
            account_id,
            "account.owner_scoped_commit_unsupported",
            "owner-aware ClipMode adoption is not available",
        )
    elif not scope_compatible:
        _add_action(
            actions,
            conflicts,
            "conflict",
            "provider_account",
            account_id,
            "account.scope_mismatch",
            "the selected account is already bound to another ClipMode provider scope",
        )
    else:
        _add_action(actions, conflicts, "noop", "provider_account", account_id, "account.bound")

    desired, desired_conflicts = _desired_state(connection, bundle, account_id, mapping)
    conflicts.extend(desired_conflicts)
    actions.extend(
        ProjectionAction("conflict", item.resource, item.key, item.code)
        for item in desired_conflicts
    )

    for item in desired.characters:
        canonical = connection.execute(
            "SELECT * FROM canonical_characters WHERE character_id = ?",
            (item.canonical_id,),
        ).fetchone()
        if item.mapping_explicit and canonical is None:
            _add_action(
                actions,
                conflicts,
                "conflict",
                "canonical_character",
                item.key,
                "mapping.target_missing",
                "explicit character mappings must target an existing canonical character",
            )
        elif canonical is None:
            _add_action(
                actions,
                conflicts,
                "create",
                "canonical_character",
                item.key,
                "character.create",
            )
        else:
            stored_class = canonical["visual_class"]
            if stored_class == item.visual_class:
                _add_action(
                    actions,
                    conflicts,
                    "noop",
                    "canonical_character",
                    item.key,
                    "character.exists",
                )
            elif stored_class == "unknown" and item.visual_class != "unknown":
                _add_action(
                    actions,
                    conflicts,
                    "update",
                    "canonical_character",
                    item.key,
                    "character.classify",
                )
            else:
                _add_action(
                    actions,
                    conflicts,
                    "conflict",
                    "canonical_character",
                    item.key,
                    "character.visual_class_mismatch",
                    "canonical and imported character visual classes are incompatible",
                )

        link = connection.execute(
            """SELECT * FROM provider_character_links
               WHERE account_id = ? AND external_character_id = ?""",
            (account_id, item.external_id),
        ).fetchone()
        if link is None:
            _add_action(actions, conflicts, "create", "character_link", item.key, "link.create")
        elif link["character_id"] != item.canonical_id:
            _add_action(
                actions,
                conflicts,
                "conflict",
                "character_link",
                item.key,
                "link.target_mismatch",
                "provider character identity is already linked elsewhere",
            )
        elif link["visual_class"] not in {item.visual_class, "unknown"}:
            _add_action(
                actions,
                conflicts,
                "conflict",
                "character_link",
                item.key,
                "link.visual_class_mismatch",
                "provider character link has an incompatible visual class",
            )
        elif _version_history_conflict(link["metadata_json"], item.link_metadata):
            _add_action(
                actions,
                conflicts,
                "conflict",
                "character_link",
                item.key,
                "link.version_immutable_mismatch",
                "provider character version metadata is immutable",
            )
        else:
            merged = _merge_namespace(link["metadata_json"], item.link_metadata)
            if _canonical_json(merged) == _canonical_json(_json_object(link["metadata_json"])):
                _add_action(actions, conflicts, "noop", "character_link", item.key, "link.exists")
            else:
                _add_action(actions, conflicts, "update", "character_link", item.key, "link.metadata")

    for item in desired.projections:
        projection = connection.execute(
            """SELECT * FROM remote_asset_projections
               WHERE account_id = ? AND external_asset_id = ?""",
            (account_id, item.external_id),
        ).fetchone()
        if projection is None:
            _add_action(
                actions, conflicts, "create", "asset_projection", item.key, "asset.create"
            )
        elif projection["projection_id"] != item.projection_id:
            _add_action(
                actions,
                conflicts,
                "conflict",
                "asset_projection",
                item.key,
                "asset.projection_id_mismatch",
                "external asset identity is bound to another projection",
            )
        elif projection["media_kind"] != item.media_kind:
            _add_action(
                actions,
                conflicts,
                "conflict",
                "asset_projection",
                item.key,
                "asset.kind_mismatch",
                "external asset kind is immutable",
            )
        elif projection["visual_class"] not in {item.visual_class, "unknown"}:
            _add_action(
                actions,
                conflicts,
                "conflict",
                "asset_projection",
                item.key,
                "asset.visual_class_mismatch",
                "external asset visual class is incompatible",
            )
        elif projection["visual_class"] != item.visual_class and item.visual_class == "unknown":
            _add_action(
                actions,
                conflicts,
                "conflict",
                "asset_projection",
                item.key,
                "asset.unknown_cannot_downgrade",
                "an imported unknown asset cannot downgrade a classified projection",
            )
        else:
            merged = _merge_namespace(projection["metadata_json"], item.metadata)
            changed = (
                projection["visual_class"] != item.visual_class
                or _canonical_json(merged)
                != _canonical_json(_json_object(projection["metadata_json"]))
                or (
                    item.remote_created_at is not None
                    and projection["remote_created_at"] != item.remote_created_at
                )
            )
            _add_action(
                actions,
                conflicts,
                "update" if changed else "noop",
                "asset_projection",
                item.key,
                "asset.metadata" if changed else "asset.exists",
            )

    for item in desired.versions:
        version = connection.execute(
            """SELECT * FROM remote_asset_versions
               WHERE projection_id = ? AND provider_version_id = ?""",
            (item.projection_id, item.provider_version_id),
        ).fetchone()
        if version is None:
            version_by_id = connection.execute(
                "SELECT version_id FROM remote_asset_versions WHERE version_id = ?",
                (item.version_id,),
            ).fetchone()
            if version_by_id is not None:
                _add_action(
                    actions,
                    conflicts,
                    "conflict",
                    "asset_version",
                    item.key,
                    "version.id_collision",
                    "deterministic asset version ID is already in use",
                )
            else:
                _add_action(
                    actions, conflicts, "create", "asset_version", item.key, "version.create"
                )
        else:
            metadata = _json_object(version["metadata_json"])
            stored_namespace = metadata.get("clipmodeImport")
            desired_namespace = item.metadata["clipmodeImport"]
            immutable_match = (
                version["version_id"] == item.version_id
                and version["source_uri"] == item.source_uri
                and version["mime_type"] == item.mime_type
                and version["byte_size"] == item.byte_size
                and version["sha256"] == item.sha256
                and stored_namespace == desired_namespace
            )
            _add_action(
                actions,
                conflicts,
                "noop" if immutable_match else "conflict",
                "asset_version",
                item.key,
                "version.exists" if immutable_match else "version.immutable_mismatch",
                None if immutable_match else "provider asset version metadata is immutable",
            )

    character_classes = {
        row["character_id"]: row["visual_class"]
        for row in connection.execute("SELECT character_id, visual_class FROM canonical_characters")
    }
    character_classes.update({item.canonical_id: item.visual_class for item in desired.characters})
    projection_classes = {
        row["projection_id"]: row["visual_class"]
        for row in connection.execute("SELECT projection_id, visual_class FROM remote_asset_projections")
    }
    projection_classes.update({item.projection_id: item.visual_class for item in desired.projections})
    for key, projection_id, character_id in desired.asset_character_links:
        if projection_classes.get(projection_id) != character_classes.get(character_id):
            _add_action(
                actions,
                conflicts,
                "conflict",
                "asset_character_link",
                key,
                "asset.character_store_class_mismatch",
                "projected asset and canonical character classes are incompatible",
            )
            continue
        existing = connection.execute(
            """SELECT 1 FROM asset_character_links
               WHERE projection_id = ? AND character_id = ?""",
            (projection_id, character_id),
        ).fetchone()
        _add_action(
            actions,
            conflicts,
            "noop" if existing else "create",
            "asset_character_link",
            key,
            "asset_character.exists" if existing else "asset_character.create",
        )

    adjacency: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT parent_projection_id, child_projection_id FROM asset_lineage"
    ):
        adjacency.setdefault(row["parent_projection_id"], set()).add(row["child_projection_id"])
    for key, parent, child, relation in desired.lineage:
        existing = connection.execute(
            """SELECT 1 FROM asset_lineage WHERE parent_projection_id = ?
               AND child_projection_id = ? AND relation = ?""",
            (parent, child, relation),
        ).fetchone()
        if existing:
            _add_action(actions, conflicts, "noop", "asset_lineage", key, "lineage.exists")
        elif _path_exists(adjacency, child, parent):
            _add_action(
                actions,
                conflicts,
                "conflict",
                "asset_lineage",
                key,
                "lineage.cycle",
                "projected lineage would create a cycle",
            )
        else:
            _add_action(actions, conflicts, "create", "asset_lineage", key, "lineage.create")
            adjacency.setdefault(parent, set()).add(child)

    actions.sort(key=lambda item: (item.resource, item.key, item.action, item.code))
    unique_conflicts = {
        (item.resource, item.key, item.code, item.message): item for item in conflicts
    }
    conflicts = sorted(
        unique_conflicts.values(), key=lambda item: (item.resource, item.key, item.code)
    )
    counts = {
        action: sum(1 for item in actions if item.action == action)
        for action in ("create", "update", "noop", "conflict")
    }
    body = {
        "bridgeVersion": BRIDGE_VERSION,
        "sourceSemanticSha256": bundle.provenance.semantic_sha256,
        "accountId": account_id,
        "providerScope": bundle.provenance.provider_scope,
        "accountBindingSha256": _binding_hash(account_id, bundle.provenance.provider_scope),
        "accountRequirement": account_requirement,
        "mappingSha256": _sha(dict(sorted(mapping.items()))),
        "counts": counts,
        "sourceDuplicates": sum(item.occurrences - 1 for item in bundle.duplicates),
        "actions": [item.to_dict() for item in actions],
        "conflicts": [item.to_dict() for item in conflicts],
    }
    plan = ClipModeProjectionPlan(
        bridge_version=BRIDGE_VERSION,
        plan_hash=_sha(body),
        source_semantic_sha256=bundle.provenance.semantic_sha256,
        account_id=account_id,
        provider_scope=bundle.provenance.provider_scope,
        account_binding_sha256=body["accountBindingSha256"],
        account_requirement=account_requirement,
        mapping_sha256=body["mappingSha256"],
        counts=counts,
        source_duplicates=body["sourceDuplicates"],
        actions=tuple(actions),
        conflicts=tuple(conflicts),
    )
    _assert_public(plan.to_dict())
    return plan, desired


def plan_clipmode_projection(
    bundle: ClipModeImportBundle,
    store: ProviderFabricStore,
    *,
    account_id: str,
    character_mapping: Mapping[str, str] | None = None,
) -> ClipModeProjectionPlan:
    """Return a deterministic, read-only plan for one validated import bundle."""

    if not isinstance(bundle, ClipModeImportBundle):
        raise TypeError("bundle must be a ClipModeImportBundle")
    if not isinstance(store, ProviderFabricStore):
        raise TypeError("store must be a ProviderFabricStore")
    _validate_bundle(bundle)
    account_id = _stable_id(account_id, "account_id")
    mapping = _mapping(character_mapping)
    with _read_snapshot(store) as connection:
        plan, _ = _plan_in_connection(connection, bundle, account_id, mapping)
    return plan


def _apply(
    connection: sqlite3.Connection,
    bundle: ClipModeImportBundle,
    account_id: str,
    desired: _DesiredState,
) -> None:
    now = _timestamp()
    for item in desired.characters:
        canonical = connection.execute(
            "SELECT visual_class FROM canonical_characters WHERE character_id = ?",
            (item.canonical_id,),
        ).fetchone()
        if canonical is None:
            connection.execute(
                """INSERT INTO canonical_characters(
                       character_id, visual_class, display_name, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (item.canonical_id, item.visual_class, item.display_name, now, now),
            )
        elif canonical["visual_class"] == "unknown" and item.visual_class != "unknown":
            connection.execute(
                """UPDATE canonical_characters SET visual_class = ?, updated_at = ?
                   WHERE character_id = ?""",
                (item.visual_class, now, item.canonical_id),
            )

    for item in desired.characters:
        link = connection.execute(
            """SELECT * FROM provider_character_links
               WHERE account_id = ? AND external_character_id = ?""",
            (account_id, item.external_id),
        ).fetchone()
        if link is None:
            connection.execute(
                """INSERT INTO provider_character_links(
                       link_id, account_id, external_character_id, character_id,
                       visual_class, metadata_json, linked_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.link_id,
                    account_id,
                    item.external_id,
                    item.canonical_id,
                    item.visual_class,
                    _canonical_json(item.link_metadata),
                    now,
                ),
            )
        else:
            merged = _merge_namespace(link["metadata_json"], item.link_metadata)
            connection.execute(
                """UPDATE provider_character_links SET visual_class = ?, metadata_json = ?
                   WHERE link_id = ?""",
                (item.visual_class, _canonical_json(merged), link["link_id"]),
            )

    for item in desired.projections:
        projection = connection.execute(
            """SELECT * FROM remote_asset_projections
               WHERE account_id = ? AND external_asset_id = ?""",
            (account_id, item.external_id),
        ).fetchone()
        if projection is None:
            connection.execute(
                """INSERT INTO remote_asset_projections(
                       projection_id, account_id, external_asset_id, media_kind,
                       visual_class, metadata_json, remote_created_at,
                       first_seen_at, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.projection_id,
                    account_id,
                    item.external_id,
                    item.media_kind,
                    item.visual_class,
                    _canonical_json(item.metadata),
                    item.remote_created_at,
                    now,
                    now,
                ),
            )
        else:
            merged = _merge_namespace(projection["metadata_json"], item.metadata)
            connection.execute(
                """UPDATE remote_asset_projections
                   SET visual_class = ?, metadata_json = ?,
                       remote_created_at = COALESCE(?, remote_created_at), last_seen_at = ?
                   WHERE projection_id = ?""",
                (
                    item.visual_class,
                    _canonical_json(merged),
                    item.remote_created_at,
                    now,
                    item.projection_id,
                ),
            )

    for item in desired.versions:
        existing = connection.execute(
            """SELECT 1 FROM remote_asset_versions
               WHERE projection_id = ? AND provider_version_id = ?""",
            (item.projection_id, item.provider_version_id),
        ).fetchone()
        if existing is None:
            row = connection.execute(
                """SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
                   FROM remote_asset_versions WHERE projection_id = ?""",
                (item.projection_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO remote_asset_versions(
                       version_id, projection_id, version_number, provider_version_id,
                       source_uri, preview_uri, mime_type, byte_size, sha256,
                       duration_seconds, metadata_json, observed_at
                   ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?)""",
                (
                    item.version_id,
                    item.projection_id,
                    int(row["next_version"]),
                    item.provider_version_id,
                    item.source_uri,
                    item.mime_type,
                    item.byte_size,
                    item.sha256,
                    _canonical_json(item.metadata),
                    now,
                ),
            )

    for _, projection_id, character_id in desired.asset_character_links:
        connection.execute(
            """INSERT OR IGNORE INTO asset_character_links(
                   projection_id, character_id, linked_at
               ) VALUES (?, ?, ?)""",
            (projection_id, character_id, now),
        )

    for _, parent, child, relation in desired.lineage:
        connection.execute(
            """INSERT OR IGNORE INTO asset_lineage(
                   parent_projection_id, child_projection_id, relation, intent_id, created_at
               ) VALUES (?, ?, ?, NULL, ?)""",
            (parent, child, relation, now),
        )


def _plan_hash_valid(plan: ClipModeProjectionPlan) -> bool:
    body = plan.to_dict()
    body.pop("planHash", None)
    body.pop("ready", None)
    return _sha(body) == plan.plan_hash


def _target_is_complete(plan: ClipModeProjectionPlan) -> bool:
    return plan.ready and plan.counts["create"] == 0 and plan.counts["update"] == 0


def commit_clipmode_projection(
    bundle: ClipModeImportBundle,
    store: ProviderFabricStore,
    plan: ClipModeProjectionPlan,
    *,
    expected_plan_hash: str,
    expected_source_semantic_sha256: str,
    account_id: str,
    provider_scope: str,
    account_binding_sha256: str,
    confirmed: bool,
    character_mapping: Mapping[str, str] | None = None,
) -> ClipModeProjectionResult:
    """Atomically commit a previously reviewed plan or return an idempotent replay.

    Every confirmation value is intentionally repeated by the caller so a UI
    cannot accidentally commit a stale plan after the selected account, export,
    scope, or explicit character mappings changed.
    """

    if not isinstance(bundle, ClipModeImportBundle):
        raise TypeError("bundle must be a ClipModeImportBundle")
    if not isinstance(store, ProviderFabricStore):
        raise TypeError("store must be a ProviderFabricStore")
    if not isinstance(plan, ClipModeProjectionPlan):
        raise TypeError("plan must be a ClipModeProjectionPlan")
    _validate_bundle(bundle)
    if confirmed is not True:
        raise ProjectionConfirmationError(
            "confirmation.required", "explicit projection confirmation is required"
        )
    account_id = _stable_id(account_id, "account_id")
    provider_scope = _stable_id(provider_scope, "provider_scope")
    expected_plan_hash = str(expected_plan_hash or "").strip().lower()
    expected_source_semantic_sha256 = str(expected_source_semantic_sha256 or "").strip().lower()
    account_binding_sha256 = str(account_binding_sha256 or "").strip().lower()
    if not all(
        _SHA256_RE.fullmatch(value)
        for value in (
            expected_plan_hash,
            expected_source_semantic_sha256,
            account_binding_sha256,
        )
    ):
        raise ProjectionConfirmationError(
            "confirmation.hash_invalid", "confirmation hashes must be lowercase SHA-256 digests"
        )
    expected_binding = _binding_hash(account_id, provider_scope)
    confirmation_matches = (
        _plan_hash_valid(plan)
        and expected_plan_hash == plan.plan_hash
        and expected_source_semantic_sha256 == plan.source_semantic_sha256
        and expected_source_semantic_sha256 == bundle.provenance.semantic_sha256
        and account_id == plan.account_id
        and provider_scope == plan.provider_scope
        and provider_scope == bundle.provenance.provider_scope
        and account_binding_sha256 == plan.account_binding_sha256
        and account_binding_sha256 == expected_binding
    )
    if not confirmation_matches:
        raise ProjectionConfirmationError(
            "confirmation.binding_mismatch",
            "plan, source, scope, mapping, or account confirmation no longer matches",
        )
    if not plan.ready:
        raise ProjectionConflictError(
            "plan.has_conflicts", "a projection plan containing conflicts cannot be committed"
        )
    mapping = _mapping(character_mapping)
    if _sha(dict(sorted(mapping.items()))) != plan.mapping_sha256:
        raise ProjectionConfirmationError(
            "confirmation.mapping_mismatch", "character mapping changed after dry run"
        )

    try:
        with _write_transaction(store) as connection:
            _store_legacy_write_contract(connection)
            current, desired = _plan_in_connection(connection, bundle, account_id, mapping)
            if current.plan_hash == plan.plan_hash:
                _apply(connection, bundle, account_id, desired)
                replayed = False
                result_counts = plan.counts
            elif _target_is_complete(current):
                replayed = True
                result_counts = current.counts
            else:
                raise ProjectionConflictError(
                    "plan.stale",
                    "provider store changed after dry run; review a new plan before committing",
                )
    except ClipModeProjectionError:
        raise
    except sqlite3.Error as error:
        raise ClipModeProjectionError(
            "commit.database_failure",
            "ClipMode metadata projection failed and was rolled back",
        ) from error

    result = ClipModeProjectionResult(
        plan_hash=plan.plan_hash,
        source_semantic_sha256=bundle.provenance.semantic_sha256,
        account_id=account_id,
        provider_scope=provider_scope,
        account_binding_sha256=account_binding_sha256,
        committed=True,
        replayed=replayed,
        counts=result_counts,
    )
    _assert_public(result.to_dict())
    return result


__all__ = [
    "BRIDGE_VERSION",
    "ClipModeProjectionError",
    "ClipModeProjectionPlan",
    "ClipModeProjectionResult",
    "ProjectionAction",
    "ProjectionConflict",
    "ProjectionConflictError",
    "ProjectionConfirmationError",
    "commit_clipmode_projection",
    "plan_clipmode_projection",
]
