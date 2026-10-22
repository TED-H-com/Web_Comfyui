"""Read-only, owner-aware store planning for validated ClipMode imports.

The pure :mod:`provider_fabric.owner_import` plan deliberately knows nothing
about the durable provider graph.  This module is the next, still read-only,
boundary: it replays that exact pure decision from the original bundle and
request, validates one SQLite snapshot supplied by ``ProviderFabricStore``,
and returns the graph rows a future atomic confirmer may create or reuse.

This module never opens a database or filesystem path and performs no network,
provider, GPU, or mutation work.  The store owns the connection and transaction
lifetime.  Raw bundled originals are not safe profile renditions, so any
profile selection remains fail-closed until a bounded decode/re-encode lane is
available.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .clipmode_import import ClipModeImportBundle, ImportedCharacterRecord
from .owner_import import (
    OwnerImportDecisionPlan,
    OwnerImportPlanError,
    build_owner_import_decision_plan,
)
from .owner_import_renditions import OwnerProfileRendition
from .owner_import_previews import OwnerAssetPreviewRendition


STORE_PLAN_SCHEMA_VERSION = 2
STORE_PLAN_VERSION = "clipmode-owner-import-store-v2"
# This planner is coupled to the exact reviewed ProviderFabric schema.  Keep
# unknown future schemas closed; migrations 10-16 are additive to its receipt
# graph. Migration 16 adds an independent append-only character-model table and
# does not change this planner's owner-import SQL contract.
SUPPORTED_STORE_SCHEMA_VERSION = 16
_SNAPSHOT_DOMAIN = "castle.provider-owner-import.store-snapshot.v1"
_GRAPH_DOMAIN = "castle.provider-owner-import.store-graph.v1"
_PLAN_DOMAIN = "castle.provider-owner-import.store-plan.v1"
_STABLE_GRAPH_DOMAIN = "castle.provider-owner-import.stable-graph.v1"
_PROOF_DOMAIN = "castle.provider-owner-import.prior-receipts.v1"
_MAX_RELEVANT_LINEAGE_ROWS = 100_000
_MAX_RELEVANT_LINEAGE_NODES = 100_000
_MAX_LINEAGE_ROW_SCANS = 200_000
_MAX_LINEAGE_QUERY_COUNT = 4_096
_LINEAGE_QUERY_BATCH_SIZE = 400
_MAX_LINEAGE_START_PROJECTIONS = 50_000
_MAX_PRIOR_RECEIPT_PROOFS = 10_000
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_URL_PREFIXES = ("http://", "https://", "file:", "data:")


class StoreOwnerImportPlanError(ValueError):
    """A sanitized fail-closed store-planning error."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise StoreOwnerImportPlanError(code, message)


def _fail_active_receipt_graph() -> None:
    _fail(
        "active_receipt_graph_mismatch",
        "Active import receipt graph does not match this decision.",
    )


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise StoreOwnerImportPlanError(
            "store_plan_not_canonical", "Store import plan is not canonical JSON data."
        ) from error


def _domain_sha256(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _metadata_json(value: Mapping[str, Any]) -> str:
    return _canonical_bytes(_plain(value)).decode("utf-8")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(child) for child in value)
    return value


def _freeze(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_value(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("value must be a mapping")
    return frozen


def _stable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("store_binding_invalid", f"{label} is not a stable identifier.")
    return value


def _safe_display(
    value: Any,
    fallback: str,
    *,
    maximum: int,
    archive_members: frozenset[str] = frozenset(),
) -> str:
    """Return useful plain display text without retaining a locator."""

    if not isinstance(value, str):
        return fallback
    normalized = unicodedata.normalize("NFC", value).strip()
    unsafe_unicode = any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in normalized
    )
    unsafe = (
        not normalized
        or len(normalized) > maximum
        or normalized != value
        or unsafe_unicode
        or normalized.lower().startswith(_URL_PREFIXES)
        or "://" in normalized
        or "/" in normalized
        or "\\" in normalized
        or normalized in {".", ".."}
        or re.match(r"^[a-zA-Z]:", normalized) is not None
        or normalized in archive_members
    )
    return fallback if unsafe else normalized


def _archive_member_names(bundle: ClipModeImportBundle) -> frozenset[str]:
    """Return exact and canonical archive locators that must never be displayed."""

    members: set[str] = set()
    for item in bundle.media:
        value = item.archive_member
        if not isinstance(value, str):
            continue
        members.add(value)
        members.add(unicodedata.normalize("NFC", value))
    return frozenset(members)


def _character_metadata(visual_class: str, display_name: str) -> Mapping[str, Any]:
    return _freeze(
        {
            "castleImport": {
                "catalogOnly": visual_class == "unknown",
                "displayName": display_name,
                "provider": "clipmode",
                "schemaVersion": STORE_PLAN_SCHEMA_VERSION,
            }
        }
    )


def _projection_metadata(visual_class: str, display_name: str) -> Mapping[str, Any]:
    return _freeze(
        {
            "castleImport": {
                "catalogOnly": visual_class == "unknown",
                "displayName": display_name,
                "provider": "clipmode",
                "schemaVersion": STORE_PLAN_SCHEMA_VERSION,
                "sourceAvailable": False,
            }
        }
    )


def _version_metadata(
    availability: str, visual_class: str, display_name: str
) -> Mapping[str, Any]:
    return _freeze(
        {
            "castleImport": {
                "catalogOnly": visual_class == "unknown",
                "displayName": display_name,
                "provider": "clipmode",
                "schemaVersion": STORE_PLAN_SCHEMA_VERSION,
                "sourceAvailable": False,
                "sourceClass": "bundled_pending"
                if availability == "bundled"
                else "remote_only",
            }
        }
    )


@dataclass(frozen=True, slots=True)
class StoreCharacterRow:
    character_id: str = field(repr=False)
    visual_class: str
    display_name: str = field(repr=False)
    disposition: str
    mapping_mode: str
    prior_receipt_proof_sha256: str | None = field(default=None, repr=False)

    def hash_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "display_name": self.display_name,
            "disposition": self.disposition,
            "mapping_mode": self.mapping_mode,
            "prior_receipt_proof_sha256": self.prior_receipt_proof_sha256,
            "visual_class": self.visual_class,
        }


@dataclass(frozen=True, slots=True)
class StoreCharacterLinkRow:
    link_id: str = field(repr=False)
    account_id: str = field(repr=False)
    external_character_id: str = field(repr=False)
    character_id: str = field(repr=False)
    visual_class: str
    metadata: Mapping[str, Any] = field(repr=False)
    disposition: str
    prior_receipt_proof_sha256: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def hash_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "character_id": self.character_id,
            "disposition": self.disposition,
            "external_character_id": self.external_character_id,
            "link_id": self.link_id,
            "metadata": _plain(self.metadata),
            "prior_receipt_proof_sha256": self.prior_receipt_proof_sha256,
            "visual_class": self.visual_class,
        }


@dataclass(frozen=True, slots=True)
class StoreProjectionRow:
    projection_id: str = field(repr=False)
    account_id: str = field(repr=False)
    external_asset_id: str = field(repr=False)
    media_kind: str
    visual_class: str
    metadata: Mapping[str, Any] = field(repr=False)
    remote_created_at: str | None = field(default=None, repr=False)
    disposition: str = "created"
    prior_receipt_proof_sha256: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def hash_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "disposition": self.disposition,
            "external_asset_id": self.external_asset_id,
            "media_kind": self.media_kind,
            "metadata": _plain(self.metadata),
            "prior_receipt_proof_sha256": self.prior_receipt_proof_sha256,
            "projection_id": self.projection_id,
            "remote_created_at": self.remote_created_at,
            "visual_class": self.visual_class,
        }


@dataclass(frozen=True, slots=True)
class StoreVersionRow:
    version_id: str = field(repr=False)
    projection_id: str = field(repr=False)
    version_number: int
    provider_version_id: str = field(repr=False)
    source_uri: str = field(repr=False)
    mime_type: str
    byte_size: int | None
    sha256: str | None = field(repr=False)
    metadata: Mapping[str, Any] = field(repr=False)
    source_available: bool
    disposition: str
    prior_receipt_proof_sha256: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def hash_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "disposition": self.disposition,
            "metadata": _plain(self.metadata),
            "mime_type": self.mime_type,
            "prior_receipt_proof_sha256": self.prior_receipt_proof_sha256,
            "projection_id": self.projection_id,
            "provider_version_id": self.provider_version_id,
            "sha256": self.sha256,
            "source_available": self.source_available,
            "source_uri": self.source_uri,
            "version_id": self.version_id,
            "version_number": self.version_number,
        }


@dataclass(frozen=True, slots=True)
class StoreAssetCharacterLinkRow:
    projection_id: str = field(repr=False)
    character_id: str = field(repr=False)
    disposition: str

    def hash_dict(self) -> dict[str, str]:
        return {
            "character_id": self.character_id,
            "disposition": self.disposition,
            "projection_id": self.projection_id,
        }


@dataclass(frozen=True, slots=True)
class StoreLineageRow:
    lineage_id: str = field(repr=False)
    parent_projection_id: str = field(repr=False)
    child_projection_id: str = field(repr=False)
    relationship: str
    disposition: str
    prior_receipt_proof_sha256: str | None = field(default=None, repr=False)

    def hash_dict(self) -> dict[str, str]:
        return {
            "child_projection_id": self.child_projection_id,
            "disposition": self.disposition,
            "lineage_id": self.lineage_id,
            "parent_projection_id": self.parent_projection_id,
            "prior_receipt_proof_sha256": self.prior_receipt_proof_sha256,
            "relationship": self.relationship,
        }


@dataclass(frozen=True, slots=True)
class StoreOwnerImportPlan:
    """Immutable private graph plan for one exact read snapshot."""

    owner_user_id: str = field(repr=False)
    account_id: str = field(repr=False)
    provider_scope: str = field(repr=False)
    decision_plan_sha256: str
    import_decision_sha256: str
    graph_sha256: str
    stable_graph_sha256: str
    snapshot_sha256: str
    plan_sha256: str
    active_receipt_id: str | None = field(default=None, repr=False)
    characters: tuple[StoreCharacterRow, ...] = field(default=(), repr=False)
    character_links: tuple[StoreCharacterLinkRow, ...] = field(default=(), repr=False)
    projections: tuple[StoreProjectionRow, ...] = field(default=(), repr=False)
    versions: tuple[StoreVersionRow, ...] = field(default=(), repr=False)
    asset_character_links: tuple[StoreAssetCharacterLinkRow, ...] = field(
        default=(), repr=False
    )
    lineage: tuple[StoreLineageRow, ...] = field(default=(), repr=False)
    profile_renditions: tuple[OwnerProfileRendition, ...] = field(
        default=(), repr=False
    )
    asset_previews: tuple[OwnerAssetPreviewRendition, ...] = field(
        default=(), repr=False
    )
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "characters",
            "character_links",
            "projections",
            "versions",
            "asset_character_links",
            "lineage",
            "profile_renditions",
            "asset_previews",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "summary", _freeze(self.summary))

    @property
    def store_snapshot_validated(self) -> bool:
        return True

    @property
    def confirmation_available(self) -> bool:
        return False

    @property
    def commit_available(self) -> bool:
        return False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STORE_PLAN_SCHEMA_VERSION,
            "plan_version": STORE_PLAN_VERSION,
            "provider_id": "clipmode",
            "mode": "store_snapshot_plan",
            "decision_validated": True,
            "store_snapshot_validated": True,
            "confirmation_available": False,
            "commit_available": False,
            "evidence": {
                "decision_plan_sha256": self.decision_plan_sha256,
                "graph_sha256": self.graph_sha256,
                "stable_graph_sha256": self.stable_graph_sha256,
                "snapshot_sha256": self.snapshot_sha256,
                "plan_sha256": self.plan_sha256,
            },
            "summary": _plain(self.summary),
            "profile_rendition_available": bool(self.profile_renditions),
            "provider_transport_performed": False,
            "provider_store_mutated": False,
            "filesystem_mutated": False,
            "media_materialized": False,
            "credits_spent": 0,
        }


def _prior_receipt_proof(
    connection: sqlite3.Connection,
    *,
    table: str,
    identifier_column: str,
    identifier: str,
    owner_user_id: str,
    account_id: str,
    provider_scope: str,
    allow_mapped_history: bool,
    active_receipt_candidate_id: str | None = None,
) -> str:
    allowed = {
        ("provider_import_character_memberships", "character_id"),
        ("provider_import_character_link_memberships", "link_id"),
        ("provider_import_projection_memberships", "projection_id"),
        ("provider_import_version_memberships", "version_id"),
    }
    if (table, identifier_column) not in allowed:
        raise AssertionError("unreviewed provider import membership table")
    if allow_mapped_history and table != "provider_import_character_memberships":
        raise AssertionError("mapped history is valid only for character memberships")
    if active_receipt_candidate_id is not None:
        candidate = connection.execute(
            f"""SELECT membership.owner_user_id AS membership_owner,
                       membership.disposition, receipt.receipt_id,
                       receipt.owner_user_id, receipt.account_id,
                       receipt.provider_id, receipt.provider_scope,
                       receipt.state,
                       account_owner.owner_user_id AS account_owner
                  FROM {table} AS membership
                  JOIN provider_import_receipts AS receipt USING(receipt_id)
                  LEFT JOIN provider_account_owners AS account_owner
                    ON account_owner.account_id = receipt.account_id
                 WHERE membership.{identifier_column} = ?
                   AND membership.receipt_id = ?""",
            (identifier, active_receipt_candidate_id),
        ).fetchone()
        candidate_exact = candidate is not None and (
            candidate["membership_owner"] == owner_user_id
            and candidate["owner_user_id"] == owner_user_id
            and candidate["account_owner"] == owner_user_id
            and candidate["account_id"] == account_id
            and candidate["provider_id"] == "clipmode"
            and candidate["provider_scope"] == provider_scope
            and candidate["state"] == "active"
            and candidate["disposition"] in {"created", "reused"}
        )
        if not candidate_exact:
            _fail(
                "active_receipt_graph_mismatch",
                "Active import receipt graph does not match this decision.",
            )
    rows = connection.execute(
        f"""SELECT membership.owner_user_id AS membership_owner,
                   membership.disposition, receipt.receipt_id,
                   receipt.owner_user_id, receipt.account_id,
                   receipt.provider_id, receipt.provider_scope,
                   receipt.import_decision_sha256, receipt.state,
                   account_owner.owner_user_id AS account_owner
             FROM {table} AS membership
              JOIN provider_import_receipts AS receipt USING(receipt_id)
              LEFT JOIN provider_account_owners AS account_owner
                ON account_owner.account_id = receipt.account_id
             WHERE membership.{identifier_column} = ?
             ORDER BY receipt.receipt_id
             LIMIT ?""",
        (identifier, _MAX_PRIOR_RECEIPT_PROOFS + 1),
    ).fetchall()
    if not rows:
        _fail("prior_import_provenance_missing", "Existing import graph lacks provenance.")
    if len(rows) > _MAX_PRIOR_RECEIPT_PROOFS:
        _fail(
            "prior_import_provenance_too_large",
            "Existing import graph provenance exceeds planning limits.",
        )
    evidence: list[dict[str, str]] = []
    for row in rows:
        same_owner = (
            row["membership_owner"] == owner_user_id
            and row["owner_user_id"] == owner_user_id
            and row["membership_owner"] == row["owner_user_id"]
            and row["account_owner"] == owner_user_id
        )
        if not same_owner:
            _fail(
                "prior_import_provenance_conflict",
                "Existing import graph provenance belongs to another binding.",
            )
        exact = (
            row["account_id"] == account_id
            and row["provider_id"] == "clipmode"
            and row["provider_scope"] == provider_scope
            and row["state"] == "active"
            and row["disposition"] in {"created", "reused"}
        )
        if exact:
            evidence.append(
                {
                    "disposition": str(row["disposition"]),
                    "import_decision_sha256": str(row["import_decision_sha256"]),
                    "receipt_id": str(row["receipt_id"]),
                }
            )
            continue
        # A character may later be selected as an explicit mapping by the same
        # owner.  That history neither proves nor invalidates the earlier exact
        # created/reused receipt.  Other non-exact membership shapes remain a
        # fail-closed structural conflict.
        legal_same_owner_mapping = (
            allow_mapped_history
            and row["disposition"] == "mapped"
            and row["provider_id"] == "clipmode"
            and row["state"] == "active"
        )
        if not legal_same_owner_mapping:
            _fail(
                "prior_import_provenance_conflict",
                "Existing import graph provenance belongs to another binding.",
            )
    if not evidence:
        _fail(
            "prior_import_provenance_missing",
            "Existing import graph lacks provenance.",
        )
    return _domain_sha256(_PROOF_DOMAIN, evidence)


def _prior_lineage_receipt_proof(
    connection: sqlite3.Connection,
    *,
    parent_projection_id: str,
    child_projection_id: str,
    relationship: str,
    owner_user_id: str,
    account_id: str,
    provider_scope: str,
    active_receipt_candidate_id: str | None,
) -> str:
    rows = connection.execute(
        """SELECT membership.owner_user_id AS membership_owner,
                  membership.disposition, receipt.receipt_id,
                  receipt.owner_user_id, receipt.account_id,
                  receipt.provider_id, receipt.provider_scope,
                  receipt.import_decision_sha256, receipt.state,
                  account_owner.owner_user_id AS account_owner
             FROM provider_import_lineage_memberships AS membership
             JOIN provider_import_receipts AS receipt USING(receipt_id)
             LEFT JOIN provider_account_owners AS account_owner
               ON account_owner.account_id = receipt.account_id
            WHERE membership.parent_projection_id = ?
              AND membership.child_projection_id = ?
              AND membership.relation = ?
            ORDER BY receipt.receipt_id
            LIMIT ?""",
        (
            parent_projection_id,
            child_projection_id,
            relationship,
            _MAX_PRIOR_RECEIPT_PROOFS + 1,
        ),
    ).fetchall()
    if not rows:
        _fail("lineage_existing_unproven", "Stored lineage lacks import provenance.")
    if len(rows) > _MAX_PRIOR_RECEIPT_PROOFS:
        _fail("prior_import_provenance_too_large", "Lineage provenance exceeds planning limits.")
    evidence: list[dict[str, str]] = []
    active_seen = active_receipt_candidate_id is None
    for row in rows:
        exact = (
            row["membership_owner"] == owner_user_id
            and row["owner_user_id"] == owner_user_id
            and row["account_owner"] == owner_user_id
            and row["account_id"] == account_id
            and row["provider_id"] == "clipmode"
            and row["provider_scope"] == provider_scope
            and row["state"] == "active"
            and row["disposition"] in {"created", "reused"}
        )
        if not exact:
            _fail("prior_import_provenance_conflict", "Stored lineage provenance conflicts.")
        if row["receipt_id"] == active_receipt_candidate_id:
            active_seen = True
        evidence.append(
            {
                "disposition": str(row["disposition"]),
                "import_decision_sha256": str(row["import_decision_sha256"]),
                "receipt_id": str(row["receipt_id"]),
            }
        )
    if not active_seen:
        _fail_active_receipt_graph()
    return _domain_sha256(_PROOF_DOMAIN, evidence)


def _latest_character(
    records: Sequence[ImportedCharacterRecord],
) -> ImportedCharacterRecord:
    return max(
        records,
        key=lambda item: (
            item.updated_at or "",
            item.created_at or "",
            item.external_ref.version,
            item.record_id,
        ),
    )


def _validate_account(
    connection: sqlite3.Connection,
    *,
    owner_user_id: str,
    account_id: str,
    provider_scope: str,
    bundle: ClipModeImportBundle,
    decision_plan: OwnerImportDecisionPlan,
) -> tuple[dict[str, Any], str | None]:
    row = connection.execute(
        """SELECT account.provider_id, account.credential_kind,
                  account.credential_locator, account.api_base, account.enabled,
                  account.credential_state, owner.owner_user_id,
                  entitlement.access_scope
             FROM provider_accounts AS account
             LEFT JOIN provider_account_owners AS owner USING(account_id)
             LEFT JOIN provider_account_entitlements AS entitlement USING(account_id)
            WHERE account.account_id = ?""",
        (account_id,),
    ).fetchone()
    if row is None or row["owner_user_id"] != owner_user_id:
        _fail("account_unavailable", "Manual import account is unavailable.")
    exact_shape = (
        row["provider_id"] == "clipmode"
        and row["credential_kind"] == "none"
        and row["credential_locator"] is None
        and row["api_base"] is None
        and int(row["enabled"]) == 0
        and row["credential_state"] == "not_applicable"
    )
    if not exact_shape:
        _fail("account_shape_invalid", "Manual import account shape is invalid.")
    if row["access_scope"] is not None:
        _fail("account_entitlement_present", "Manual import account is executable.")

    scopes = [
        str(item["provider_scope"])
        for item in connection.execute(
            """SELECT DISTINCT provider_scope FROM provider_import_receipts
                WHERE account_id = ? AND state = 'active'
                ORDER BY provider_scope LIMIT 2""",
            (account_id,),
        ).fetchall()
    ]
    if scopes and scopes != [provider_scope]:
        _fail("account_scope_conflict", "Manual import account scope is unavailable.")
    replay = connection.execute(
        """SELECT receipt.receipt_id, receipt.owner_user_id,
                  receipt.account_id, receipt.provider_id,
                  provider_scope, source_schema, source_schema_version,
                  export_id, exported_at, raw_source_sha256,
                  manifest_sha256, semantic_sha256, mapping_sha256,
                  profile_selection_sha256, account_binding_sha256,
                  plan_version, plan_sha256, import_decision_sha256, state,
                  evidence.decision_plan_sha256,
                  evidence.stable_graph_sha256
             FROM provider_import_receipts AS receipt
             LEFT JOIN provider_import_plan_evidence AS evidence
               ON evidence.receipt_id = receipt.receipt_id
              AND evidence.owner_user_id = receipt.owner_user_id
            WHERE receipt.import_decision_sha256 = ?
              AND receipt.state = 'active'""",
        (decision_plan.import_decision_sha256,),
    ).fetchone()
    active_receipt_candidate_id: str | None = None
    if replay is not None:
        expected = {
            "owner_user_id": owner_user_id,
            "account_id": account_id,
            "provider_id": "clipmode",
            "provider_scope": provider_scope,
            "source_schema": bundle.provenance.schema_name,
            "source_schema_version": bundle.provenance.schema_version,
            "export_id": bundle.provenance.export_id,
            "exported_at": bundle.provenance.exported_at,
            "raw_source_sha256": decision_plan.raw_source_sha256,
            "manifest_sha256": decision_plan.manifest_sha256,
            "semantic_sha256": decision_plan.semantic_sha256,
            "mapping_sha256": decision_plan.mapping_sha256,
            "profile_selection_sha256": decision_plan.profile_selection_sha256,
            "account_binding_sha256": decision_plan.account_binding_sha256,
            "plan_version": STORE_PLAN_VERSION,
            "import_decision_sha256": decision_plan.import_decision_sha256,
            "decision_plan_sha256": decision_plan.plan_sha256,
            "state": "active",
        }
        if any(replay[key] != value for key, value in expected.items()):
            _fail("active_receipt_conflict", "Existing import receipt is unavailable.")
        if not isinstance(replay["stable_graph_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", replay["stable_graph_sha256"]
        ):
            _fail("active_receipt_conflict", "Existing import receipt is unavailable.")
        if not isinstance(replay["plan_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", replay["plan_sha256"]
        ):
            _fail("active_receipt_conflict", "Existing import receipt is unavailable.")
        active_receipt_candidate_id = str(replay["receipt_id"])
    snapshot = {
        "account_id": account_id,
        "credential_kind": "none",
        "credential_state": "not_applicable",
        "enabled": False,
        "entitlement": None,
        "owner_user_id": owner_user_id,
        "provider_id": "clipmode",
        "provider_scope": provider_scope,
        "observed_scopes": scopes,
        "active_receipt_candidate": active_receipt_candidate_id is not None,
    }
    return snapshot, active_receipt_candidate_id


def _validate_active_receipt_graph(
    connection: sqlite3.Connection,
    *,
    receipt_id: str | None,
    owner_user_id: str,
    characters: Sequence[StoreCharacterRow],
    character_links: Sequence[StoreCharacterLinkRow],
    projections: Sequence[StoreProjectionRow],
    versions: Sequence[StoreVersionRow],
    asset_character_links: Sequence[StoreAssetCharacterLinkRow],
    lineage: Sequence[StoreLineageRow],
    profile_renditions: Sequence[OwnerProfileRendition],
    asset_previews: Sequence[OwnerAssetPreviewRendition],
    stable_graph_sha256: str,
) -> str | None:
    """Prove that an active exact-decision receipt names this whole graph.

    A replay is a property of the complete existing graph and its immutable
    receipt memberships, not merely of the receipt header's decision digest.
    Every current non-mapped row must resolve as ``reused``.  The historical
    receipt may record ``created`` or ``reused`` for those same exact IDs,
    while an explicit character mapping must remain ``mapped`` in both views.
    """

    if receipt_id is None:
        return None

    for item in characters:
        expected_disposition = "mapped" if item.mapping_mode == "map" else "reused"
        if item.disposition != expected_disposition:
            _fail_active_receipt_graph()
    for rows in (
        character_links,
        projections,
        versions,
        asset_character_links,
        lineage,
    ):
        if any(item.disposition != "reused" for item in rows):
            _fail_active_receipt_graph()

    character_expectations: dict[str, frozenset[str]] = {}
    for item in characters:
        allowed = (
            frozenset({"mapped"})
            if item.mapping_mode == "map"
            else frozenset({"created", "reused"})
        )
        previous = character_expectations.get(item.character_id)
        if previous is not None and previous != allowed:
            _fail_active_receipt_graph()
        character_expectations[item.character_id] = allowed

    reusable = frozenset({"created", "reused"})
    tables: tuple[tuple[str, str, Mapping[str, frozenset[str]]], ...] = (
        (
            "provider_import_character_memberships",
            "character_id",
            character_expectations,
        ),
        (
            "provider_import_character_link_memberships",
            "link_id",
            {item.link_id: reusable for item in character_links},
        ),
        (
            "provider_import_projection_memberships",
            "projection_id",
            {item.projection_id: reusable for item in projections},
        ),
        (
            "provider_import_version_memberships",
            "version_id",
            {item.version_id: reusable for item in versions},
        ),
    )
    for table, identifier_column, expected in tables:
        rows = connection.execute(
            f"""SELECT owner_user_id, {identifier_column} AS identifier,
                       disposition
                  FROM {table}
                 WHERE receipt_id = ?
                 ORDER BY {identifier_column}
                 LIMIT ?""",
            (receipt_id, len(expected) + 1),
        ).fetchall()
        observed: set[str] = set()
        valid = len(rows) == len(expected)
        for row in rows:
            identifier = str(row["identifier"])
            allowed = expected.get(identifier)
            valid = (
                valid
                and row["owner_user_id"] == owner_user_id
                and identifier not in observed
                and allowed is not None
                and row["disposition"] in allowed
            )
            observed.add(identifier)
        if not valid or observed != set(expected):
            _fail_active_receipt_graph()

    lineage_rows = connection.execute(
        """SELECT owner_user_id, parent_projection_id,
                  child_projection_id, relation, disposition
             FROM provider_import_lineage_memberships
            WHERE receipt_id = ?
            ORDER BY parent_projection_id, child_projection_id, relation
            LIMIT ?""",
        (receipt_id, len(lineage) + 1),
    ).fetchall()
    expected_lineage = {
        (item.parent_projection_id, item.child_projection_id, item.relationship)
        for item in lineage
    }
    observed_lineage: set[tuple[str, str, str]] = set()
    valid_lineage = len(lineage_rows) == len(expected_lineage)
    for row in lineage_rows:
        identity = (
            str(row["parent_projection_id"]),
            str(row["child_projection_id"]),
            str(row["relation"]),
        )
        valid_lineage = (
            valid_lineage
            and row["owner_user_id"] == owner_user_id
            and identity in expected_lineage
            and identity not in observed_lineage
            and row["disposition"] in {"created", "reused"}
        )
        observed_lineage.add(identity)
    if not valid_lineage or observed_lineage != expected_lineage:
        _fail_active_receipt_graph()

    expected_profiles = {
        item.canonical_character_id: item for item in profile_renditions
    }
    profile_rows = connection.execute(
        """SELECT exact.character_id, exact.owner_user_id,
                  exact.projection_id, exact.version_id,
                  exact.rendition_sha256, exact.selection_source,
                  profile.projection_id AS profile_projection_id,
                  profile.selection_source AS profile_selection_source,
                  preview.version_id AS preview_version_id,
                  preview.rendition_sha256 AS preview_sha256,
                  preview.selection_source AS preview_selection_source,
                  preview.receipt_id AS preview_receipt_id,
                  binding.sha256 AS binding_sha256,
                  binding.declared_mime, binding.receipt_id AS binding_receipt_id,
                  blob.byte_size, blob.signature_family
             FROM canonical_character_profile_versions AS exact
             JOIN canonical_character_profiles AS profile
               ON profile.character_id = exact.character_id
             JOIN projection_preview_versions AS preview
               ON preview.owner_user_id = exact.owner_user_id
              AND preview.projection_id = exact.projection_id
             JOIN provider_asset_version_blobs AS binding
               ON binding.owner_user_id = exact.owner_user_id
              AND binding.version_id = exact.version_id
              AND binding.role = 'preview_rendition'
             JOIN provider_media_blobs AS blob
               ON blob.owner_user_id = binding.owner_user_id
              AND blob.sha256 = binding.sha256
            WHERE exact.receipt_id = ?
            ORDER BY exact.character_id
            LIMIT ?""",
        (receipt_id, len(expected_profiles) + 1),
    ).fetchall()
    observed_profiles: set[str] = set()
    valid_profiles = len(profile_rows) == len(expected_profiles)
    for row in profile_rows:
        character_id = str(row["character_id"])
        expected = expected_profiles.get(character_id)
        valid_profiles = (
            valid_profiles
            and expected is not None
            and character_id not in observed_profiles
            and row["owner_user_id"] == owner_user_id
            and row["projection_id"] == expected.projection_id
            and row["version_id"] == expected.version_id
            and row["rendition_sha256"] == expected.rendition_sha256
            and row["selection_source"] == "explicit"
            and row["profile_projection_id"] == expected.projection_id
            and row["profile_selection_source"] == "explicit"
            and row["preview_version_id"] == expected.version_id
            and row["preview_sha256"] == expected.rendition_sha256
            and row["preview_selection_source"] == "explicit"
            and row["preview_receipt_id"] == receipt_id
            and row["binding_sha256"] == expected.rendition_sha256
            and row["declared_mime"] == expected.mime_type
            and row["binding_receipt_id"] == receipt_id
            and row["byte_size"] == expected.byte_size
            and row["signature_family"] == expected.signature_family
        )
        observed_profiles.add(character_id)
    if not valid_profiles or observed_profiles != set(expected_profiles):
        _fail_active_receipt_graph()

    expected_previews = {item.projection_id: item for item in asset_previews}
    preview_rows = connection.execute(
        """SELECT preview.projection_id, preview.owner_user_id, preview.version_id,
                  preview.rendition_sha256, preview.selection_source,
                  binding.sha256 AS binding_sha256, binding.declared_mime,
                  binding.receipt_id AS binding_receipt_id,
                  blob.byte_size, blob.signature_family
             FROM projection_preview_versions AS preview
             JOIN provider_asset_version_blobs AS binding
               ON binding.owner_user_id = preview.owner_user_id
              AND binding.version_id = preview.version_id
              AND binding.role = 'preview_rendition'
             JOIN provider_media_blobs AS blob
               ON blob.owner_user_id = binding.owner_user_id
              AND blob.sha256 = binding.sha256
            WHERE preview.receipt_id = ?
            ORDER BY preview.projection_id
            LIMIT ?""",
        (receipt_id, len(expected_previews) + 1),
    ).fetchall()
    observed_previews: set[str] = set()
    valid_previews = len(preview_rows) == len(expected_previews)
    for row in preview_rows:
        projection_id = str(row["projection_id"])
        expected = expected_previews.get(projection_id)
        valid_previews = (
            valid_previews and expected is not None
            and projection_id not in observed_previews
            and row["owner_user_id"] == owner_user_id
            and row["version_id"] == expected.version_id
            and row["rendition_sha256"] == expected.rendition_sha256
            and row["selection_source"] == "explicit"
            and row["binding_sha256"] == expected.rendition_sha256
            and row["declared_mime"] == expected.mime_type
            and row["binding_receipt_id"] == receipt_id
            and row["byte_size"] == expected.byte_size
            and row["signature_family"] == expected.signature_family
        )
        observed_previews.add(projection_id)
    if not valid_previews or observed_previews != set(expected_previews):
        _fail_active_receipt_graph()

    evidence = connection.execute(
        """SELECT owner_user_id, stable_graph_sha256
             FROM provider_import_plan_evidence
            WHERE receipt_id = ?""",
        (receipt_id,),
    ).fetchone()
    if (
        evidence is None
        or evidence["owner_user_id"] != owner_user_id
        or evidence["stable_graph_sha256"] != stable_graph_sha256
    ):
        _fail_active_receipt_graph()
    return receipt_id


def _character_actual(row: sqlite3.Row | None) -> Any:
    if row is None:
        return None
    return {
        "character_id": row["character_id"],
        "display_name": row["display_name"],
        "owner_user_id": row["owner_user_id"],
        "visual_class": row["visual_class"],
    }


def _resolve_character_link(
    connection: sqlite3.Connection,
    *,
    link_id: str,
    account_id: str,
    external_character_id: str,
    character_id: str,
    visual_class: str,
    metadata: Mapping[str, Any],
    owner_user_id: str,
    provider_scope: str,
    active_receipt_candidate_id: str | None,
) -> tuple[StoreCharacterLinkRow, Any]:
    by_identity = connection.execute(
        """SELECT link.*, owner.owner_user_id
             FROM provider_character_links AS link
             LEFT JOIN canonical_character_owners AS owner
               ON owner.character_id = link.character_id
            WHERE link.account_id = ? AND link.external_character_id = ?""",
        (account_id, external_character_id),
    ).fetchone()
    by_id = connection.execute(
        """SELECT link.*, owner.owner_user_id
             FROM provider_character_links AS link
             LEFT JOIN canonical_character_owners AS owner
               ON owner.character_id = link.character_id
            WHERE link.link_id = ?""",
        (link_id,),
    ).fetchone()
    if by_identity is None and by_id is None:
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        planned = StoreCharacterLinkRow(
            link_id=link_id,
            account_id=account_id,
            external_character_id=external_character_id,
            character_id=character_id,
            visual_class=visual_class,
            metadata=metadata,
            disposition="created",
        )
        return planned, None
    if (
        by_identity is None
        or by_id is None
        or by_identity["link_id"] != by_id["link_id"]
    ):
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        _fail("character_link_collision", "Provider character identity is unavailable.")
    expected_metadata = _metadata_json(metadata)
    exact = (
        by_identity["link_id"] == link_id
        and by_identity["account_id"] == account_id
        and by_identity["external_character_id"] == external_character_id
        and by_identity["character_id"] == character_id
        and by_identity["visual_class"] == visual_class
        and by_identity["owner_user_id"] == owner_user_id
        and by_identity["metadata_json"] == expected_metadata
    )
    if not exact:
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        _fail("character_link_collision", "Provider character identity is unavailable.")
    proof = _prior_receipt_proof(
        connection,
        table="provider_import_character_link_memberships",
        identifier_column="link_id",
        identifier=link_id,
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=provider_scope,
        allow_mapped_history=False,
        active_receipt_candidate_id=active_receipt_candidate_id,
    )
    planned = StoreCharacterLinkRow(
        link_id=link_id,
        account_id=account_id,
        external_character_id=external_character_id,
        character_id=character_id,
        visual_class=visual_class,
        metadata=metadata,
        disposition="reused",
        prior_receipt_proof_sha256=proof,
    )
    actual = {
        "account_id": by_identity["account_id"],
        "character_id": by_identity["character_id"],
        "external_character_id": by_identity["external_character_id"],
        "link_id": by_identity["link_id"],
        "metadata_json": by_identity["metadata_json"],
        "owner_user_id": by_identity["owner_user_id"],
        "visual_class": by_identity["visual_class"],
    }
    return planned, actual


def _resolve_projection(
    connection: sqlite3.Connection,
    *,
    projection_id: str,
    account_id: str,
    external_asset_id: str,
    media_kind: str,
    visual_class: str,
    metadata: Mapping[str, Any],
    remote_created_at: str | None,
    owner_user_id: str,
    provider_scope: str,
    active_receipt_candidate_id: str | None,
) -> tuple[StoreProjectionRow, Any]:
    by_identity = connection.execute(
        """SELECT projection.*, owner.owner_user_id
             FROM remote_asset_projections AS projection
             LEFT JOIN remote_asset_owners AS owner USING(projection_id)
            WHERE projection.account_id = ? AND projection.external_asset_id = ?""",
        (account_id, external_asset_id),
    ).fetchone()
    by_id = connection.execute(
        """SELECT projection.*, owner.owner_user_id
             FROM remote_asset_projections AS projection
             LEFT JOIN remote_asset_owners AS owner USING(projection_id)
            WHERE projection.projection_id = ?""",
        (projection_id,),
    ).fetchone()
    if by_identity is None and by_id is None:
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        return (
            StoreProjectionRow(
                projection_id=projection_id,
                account_id=account_id,
                external_asset_id=external_asset_id,
                media_kind=media_kind,
                visual_class=visual_class,
                metadata=metadata,
                remote_created_at=remote_created_at,
                disposition="created",
            ),
            None,
        )
    if (
        by_identity is None
        or by_id is None
        or by_identity["projection_id"] != by_id["projection_id"]
    ):
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        _fail("projection_collision", "Provider asset identity is unavailable.")
    exact = (
        by_identity["projection_id"] == projection_id
        and by_identity["account_id"] == account_id
        and by_identity["external_asset_id"] == external_asset_id
        and by_identity["media_kind"] == media_kind
        and by_identity["visual_class"] == visual_class
        and by_identity["owner_user_id"] == owner_user_id
        and by_identity["metadata_json"] == _metadata_json(metadata)
        and by_identity["remote_created_at"] == remote_created_at
    )
    if not exact:
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        _fail("projection_collision", "Provider asset identity is unavailable.")
    proof = _prior_receipt_proof(
        connection,
        table="provider_import_projection_memberships",
        identifier_column="projection_id",
        identifier=projection_id,
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=provider_scope,
        allow_mapped_history=False,
        active_receipt_candidate_id=active_receipt_candidate_id,
    )
    planned = StoreProjectionRow(
        projection_id=projection_id,
        account_id=account_id,
        external_asset_id=external_asset_id,
        media_kind=media_kind,
        visual_class=visual_class,
        metadata=metadata,
        remote_created_at=remote_created_at,
        disposition="reused",
        prior_receipt_proof_sha256=proof,
    )
    actual = {
        "account_id": by_identity["account_id"],
        "external_asset_id": by_identity["external_asset_id"],
        "media_kind": by_identity["media_kind"],
        "metadata_json": by_identity["metadata_json"],
        "owner_user_id": by_identity["owner_user_id"],
        "projection_id": by_identity["projection_id"],
        "remote_created_at": by_identity["remote_created_at"],
        "visual_class": by_identity["visual_class"],
    }
    return planned, actual


def _resolve_version(
    connection: sqlite3.Connection,
    *,
    identity: Any,
    projection: StoreProjectionRow,
    version_number: int,
    owner_user_id: str,
    account_id: str,
    provider_scope: str,
    display_name: str,
    active_receipt_candidate_id: str | None,
) -> tuple[StoreVersionRow, Any, bool]:
    metadata = _version_metadata(
        identity.availability, identity.visual_class, display_name
    )
    source_uri = f"castle://remote-metadata/{identity.version_id}"
    by_identity = connection.execute(
        """SELECT version.* FROM remote_asset_versions AS version
            WHERE version.projection_id = ? AND version.provider_version_id = ?""",
        (projection.projection_id, identity.external_version),
    ).fetchone()
    by_id = connection.execute(
        "SELECT * FROM remote_asset_versions WHERE version_id = ?",
        (identity.version_id,),
    ).fetchone()
    if by_identity is None and by_id is None:
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        return (
            StoreVersionRow(
                version_id=identity.version_id,
                projection_id=projection.projection_id,
                version_number=version_number,
                provider_version_id=identity.external_version,
                source_uri=source_uri,
                mime_type=identity.mime_type,
                byte_size=identity.byte_size,
                sha256=identity.content_sha256,
                metadata=metadata,
                source_available=False,
                disposition="created",
            ),
            None,
            True,
        )
    if (
        by_identity is None
        or by_id is None
        or by_identity["version_id"] != by_id["version_id"]
    ):
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        _fail("version_collision", "Provider asset version is unavailable.")
    exact = (
        by_identity["version_id"] == identity.version_id
        and by_identity["projection_id"] == projection.projection_id
        and by_identity["provider_version_id"] == identity.external_version
        and by_identity["source_uri"] == source_uri
        and by_identity["preview_uri"] is None
        and by_identity["mime_type"] == identity.mime_type
        and by_identity["byte_size"] == identity.byte_size
        and by_identity["sha256"] == identity.content_sha256
        and by_identity["duration_seconds"] is None
        and by_identity["metadata_json"] == _metadata_json(metadata)
    )
    if not exact:
        if active_receipt_candidate_id is not None:
            _fail_active_receipt_graph()
        _fail("version_collision", "Provider asset version is unavailable.")
    proof = _prior_receipt_proof(
        connection,
        table="provider_import_version_memberships",
        identifier_column="version_id",
        identifier=identity.version_id,
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=provider_scope,
        allow_mapped_history=False,
        active_receipt_candidate_id=active_receipt_candidate_id,
    )
    planned = StoreVersionRow(
        version_id=identity.version_id,
        projection_id=projection.projection_id,
        version_number=int(by_identity["version_number"]),
        provider_version_id=identity.external_version,
        source_uri=source_uri,
        mime_type=identity.mime_type,
        byte_size=identity.byte_size,
        sha256=identity.content_sha256,
        metadata=metadata,
        source_available=False,
        disposition="reused",
        prior_receipt_proof_sha256=proof,
    )
    actual = {
        "byte_size": by_identity["byte_size"],
        "duration_seconds": by_identity["duration_seconds"],
        "metadata_json": by_identity["metadata_json"],
        "mime_type": by_identity["mime_type"],
        "preview_uri": by_identity["preview_uri"],
        "projection_id": by_identity["projection_id"],
        "provider_version_id": by_identity["provider_version_id"],
        "sha256": by_identity["sha256"],
        "source_uri": by_identity["source_uri"],
        "version_id": by_identity["version_id"],
        "version_number": by_identity["version_number"],
    }
    return planned, actual, False


def _acyclic(adjacency: Mapping[str, set[str]]) -> bool:
    # Explicit DFS frames avoid Python's recursion limit on legitimate long
    # media histories while preserving the usual white/grey/black cycle test.
    state: dict[str, int] = {}
    nodes = set(adjacency)
    for children in adjacency.values():
        nodes.update(children)
    for root in sorted(nodes):
        if state.get(root, 0) != 0:
            continue
        state[root] = 1
        stack: list[tuple[str, Any]] = [
            (root, iter(sorted(adjacency.get(root, ()))))
        ]
        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
            except StopIteration:
                state[node] = 2
                stack.pop()
                continue
            child_state = state.get(child, 0)
            if child_state == 1:
                return False
            if child_state == 2:
                continue
            state[child] = 1
            stack.append((child, iter(sorted(adjacency.get(child, ())))))
    return True


def _stable_graph_payload(
    *,
    characters: Sequence[StoreCharacterRow],
    character_links: Sequence[StoreCharacterLinkRow],
    projections: Sequence[StoreProjectionRow],
    versions: Sequence[StoreVersionRow],
    asset_character_links: Sequence[StoreAssetCharacterLinkRow],
    lineage: Sequence[StoreLineageRow],
) -> dict[str, Any]:
    """Return graph identity that is invariant across first import and replay."""

    def stable(rows: Sequence[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row.hash_dict())
            value.pop("disposition", None)
            value.pop("prior_receipt_proof_sha256", None)
            result.append(value)
        return result

    return {
        "asset_character_links": stable(asset_character_links),
        "character_links": stable(character_links),
        "characters": stable(characters),
        "lineage": stable(lineage),
        "projections": stable(projections),
        "versions": stable(versions),
    }


def _relevant_lineage(
    connection: sqlite3.Connection,
    projection_ids: Sequence[str],
    owner_user_id: str,
) -> list[dict[str, Any]]:
    if not projection_ids:
        return []
    if len(projection_ids) > _MAX_LINEAGE_START_PROJECTIONS:
        _fail(
            "lineage_graph_too_large",
            "Relevant stored lineage exceeds planning limits.",
        )
    # Walk the undirected connected component so every newly encountered node
    # is checked for both incoming and outgoing edges.  Each node is queried at
    # most once, each edge is scanned at most twice, and every resource class
    # has a hard global budget.  This avoids an unbounded recursive SQL CTE.
    collected: dict[tuple[str, str, str], sqlite3.Row] = {}
    seen_nodes = set(projection_ids)
    pending = deque(sorted(seen_nodes))
    queried_nodes: set[str] = set()
    row_scans = 0
    query_count = 0
    while pending:
        selected: list[str] = []
        while pending and len(selected) < _LINEAGE_QUERY_BATCH_SIZE:
            node = pending.popleft()
            if node in queried_nodes:
                continue
            queried_nodes.add(node)
            selected.append(node)
        if not selected:
            continue
        query_count += 1
        if query_count > _MAX_LINEAGE_QUERY_COUNT:
            _fail(
                "lineage_graph_too_large",
                "Relevant stored lineage exceeds planning limits.",
            )
        placeholders = ",".join("?" for _ in selected)
        remaining_scans = _MAX_LINEAGE_ROW_SCANS - row_scans
        if remaining_scans <= 0:
            _fail(
                "lineage_graph_too_large",
                "Relevant stored lineage exceeds planning limits.",
            )
        rows = connection.execute(
            f"""SELECT lineage.parent_projection_id,
                          lineage.child_projection_id,
                          lineage.relation, lineage.intent_id,
                          parent_owner.owner_user_id AS parent_owner,
                          child_owner.owner_user_id AS child_owner,
                          intent_owner.owner_user_id AS intent_owner
                     FROM asset_lineage AS lineage
                     LEFT JOIN remote_asset_owners AS parent_owner
                       ON parent_owner.projection_id = lineage.parent_projection_id
                     LEFT JOIN remote_asset_owners AS child_owner
                       ON child_owner.projection_id = lineage.child_projection_id
                     LEFT JOIN generation_intent_owners AS intent_owner
                       ON intent_owner.intent_id = lineage.intent_id
                    WHERE lineage.parent_projection_id IN ({placeholders})
                       OR lineage.child_projection_id IN ({placeholders})
                    LIMIT ?""",
            (*selected, *selected, remaining_scans + 1),
        ).fetchall()
        if len(rows) > remaining_scans:
            _fail(
                "lineage_graph_too_large",
                "Relevant stored lineage exceeds planning limits.",
            )
        row_scans += len(rows)
        discovered: set[str] = set()
        for row in rows:
            if row["parent_owner"] != owner_user_id or row["child_owner"] != owner_user_id:
                _fail("lineage_owner_conflict", "Stored lineage crosses an owner boundary.")
            if row["intent_id"] is not None and row["intent_owner"] != owner_user_id:
                _fail("lineage_owner_conflict", "Stored lineage crosses an owner boundary.")
            parent = str(row["parent_projection_id"])
            child = str(row["child_projection_id"])
            collected[(parent, child, str(row["relation"]))] = row
            if parent not in seen_nodes:
                discovered.add(parent)
            if child not in seen_nodes:
                discovered.add(child)
        if len(collected) > _MAX_RELEVANT_LINEAGE_ROWS:
            _fail(
                "lineage_graph_too_large",
                "Relevant stored lineage exceeds planning limits.",
            )
        if len(seen_nodes) + len(discovered) > _MAX_RELEVANT_LINEAGE_NODES:
            _fail(
                "lineage_graph_too_large",
                "Relevant stored lineage exceeds planning limits.",
            )
        seen_nodes.update(discovered)
        pending.extend(sorted(discovered))
    result: list[dict[str, Any]] = []
    for key in sorted(collected):
        row = collected[key]
        result.append(
            {
                "child_projection_id": row["child_projection_id"],
                "intent_id": row["intent_id"],
                "parent_projection_id": row["parent_projection_id"],
                "relation": row["relation"],
            }
        )
    return result


def plan_owned_provider_import_in_connection(
    connection: sqlite3.Connection,
    *,
    owner_user_id: str,
    account_id: str,
    bundle: ClipModeImportBundle,
    request: Mapping[str, Any],
    decision_plan: OwnerImportDecisionPlan,
    profile_renditions: Sequence[OwnerProfileRendition] = (),
    asset_previews: Sequence[OwnerAssetPreviewRendition] = (),
) -> StoreOwnerImportPlan:
    """Plan against a caller-owned read snapshot; never open or mutate storage."""

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be an sqlite3.Connection")
    if not connection.in_transaction:
        _fail(
            "store_transaction_required",
            "Store planning requires a caller-owned transaction.",
        )
    if connection.row_factory is not sqlite3.Row:
        _fail("store_connection_invalid", "Store connection row contract is invalid.")
    schema_row = connection.execute("PRAGMA user_version").fetchone()
    if schema_row is None or int(schema_row[0]) != SUPPORTED_STORE_SCHEMA_VERSION:
        _fail("store_schema_unsupported", "Provider store schema is unsupported.")
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0]) != 1:
        _fail("store_connection_invalid", "Store connection safety is unavailable.")
    owner_user_id = _stable_id(owner_user_id, "owner_user_id")
    account_id = _stable_id(account_id, "account_id")
    if not isinstance(bundle, ClipModeImportBundle):
        raise TypeError("bundle must be a ClipModeImportBundle")
    if not isinstance(decision_plan, OwnerImportDecisionPlan):
        raise TypeError("decision_plan must be an OwnerImportDecisionPlan")
    if (
        decision_plan.owner_user_id != owner_user_id
        or decision_plan.account_id != account_id
    ):
        _fail("decision_binding_mismatch", "Import decision binding is unavailable.")
    try:
        recomputed = build_owner_import_decision_plan(
            bundle,
            request,
            owner_user_id=owner_user_id,
            account_id=account_id,
        )
    except OwnerImportPlanError as error:
        raise StoreOwnerImportPlanError(
            "decision_revalidation_failed", "Import decision could not be revalidated."
        ) from error
    if recomputed != decision_plan:
        _fail("decision_plan_mismatch", "Import decision does not match its exact source.")
    if isinstance(profile_renditions, (str, bytes, bytearray)) or not isinstance(
        profile_renditions, Sequence
    ):
        raise TypeError("profile_renditions must be a sequence")
    supplied_renditions: dict[str, OwnerProfileRendition] = {}
    for rendition in profile_renditions:
        if not isinstance(rendition, OwnerProfileRendition):
            raise TypeError("profile_renditions contains an invalid item")
        if rendition.source_character_key in supplied_renditions:
            _fail("profile_rendition_duplicate", "Profile rendition is duplicated.")
        supplied_renditions[rendition.source_character_key] = rendition
    expected_profiles = {
        profile.source_character_key: profile for profile in recomputed.profiles
    }
    if set(supplied_renditions) != set(expected_profiles):
        _fail(
            "profile_rendition_unavailable",
            "Profile selection requires an exact safe rendition.",
        )
    normalized_renditions: list[OwnerProfileRendition] = []
    for source_key in sorted(expected_profiles):
        profile = expected_profiles[source_key]
        rendition = supplied_renditions[source_key]
        if (
            rendition.canonical_character_id != profile.canonical_character_id
            or rendition.projection_id != profile.projection_id
            or rendition.version_id != profile.version_id
            or rendition.source_sha256 != profile.source_sha256
            or rendition.mime_type != "image/png"
            or rendition.signature_family != "png"
            or rendition.byte_size != len(rendition.content)
            or rendition.byte_size <= 0
            or rendition.byte_size > 4 * 1024 * 1024
            or rendition.width <= 0
            or rendition.height <= 0
            or rendition.width > 512
            or rendition.height > 512
            or hashlib.sha256(rendition.content).hexdigest()
            != rendition.rendition_sha256
        ):
            _fail("profile_rendition_mismatch", "Profile rendition does not match its choice.")
        normalized_renditions.append(rendition)
    if isinstance(asset_previews, (str, bytes, bytearray)) or not isinstance(
        asset_previews, Sequence
    ):
        raise TypeError("asset_previews must be a sequence")
    media_by_version = {item.version_id: item for item in recomputed.media}
    normalized_previews: list[OwnerAssetPreviewRendition] = []
    preview_projections: set[str] = set()
    profiles_by_projection = {
        item.projection_id: item for item in normalized_renditions
    }
    for preview in asset_previews:
        if not isinstance(preview, OwnerAssetPreviewRendition):
            raise TypeError("asset_previews contains an invalid item")
        identity = media_by_version.get(preview.version_id)
        if preview.projection_id in preview_projections:
            _fail("asset_preview_duplicate", "Asset preview is duplicated.")
        preview_projections.add(preview.projection_id)
        profile = profiles_by_projection.get(preview.projection_id)
        if (
            identity is None
            or identity.source_media_record_id != preview.source_media_record_id
            or identity.projection_id != preview.projection_id
            or identity.content_sha256 != preview.source_sha256
            or identity.kind != "image"
            or identity.availability != "bundled"
            or preview.mime_type != "image/png"
            or preview.signature_family != "png"
            or preview.byte_size != len(preview.content)
            or preview.byte_size <= 0
            or preview.byte_size > 4 * 1024 * 1024
            or preview.width <= 0
            or preview.height <= 0
            or preview.width > 512
            or preview.height > 512
            or preview.selection_source
            not in {"explicit_import", "explicit_profile"}
            or hashlib.sha256(preview.content).hexdigest()
            != preview.rendition_sha256
            or (profile is None)
            != (preview.selection_source == "explicit_import")
            or (
                profile is not None
                and (
                    profile.version_id != preview.version_id
                    or profile.rendition_sha256 != preview.rendition_sha256
                )
            )
        ):
            _fail("asset_preview_mismatch", "Asset preview does not match its version.")
        normalized_previews.append(preview)
    if set(profiles_by_projection) - preview_projections:
        _fail(
            "profile_rendition_unavailable",
            "Profile selection requires an exact asset preview.",
        )
    normalized_previews.sort(key=lambda item: item.projection_id)

    account_snapshot, active_receipt_candidate_id = _validate_account(
        connection,
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=recomputed.provider_scope,
        bundle=bundle,
        decision_plan=recomputed,
    )

    archive_members = _archive_member_names(bundle)
    source_characters = {item.record_id: item for item in bundle.characters}
    decision_by_source_record: dict[str, Any] = {}
    character_rows: list[StoreCharacterRow] = []
    character_row_by_id: dict[str, StoreCharacterRow] = {}
    link_rows: list[StoreCharacterLinkRow] = []
    character_snapshot: list[dict[str, Any]] = []
    link_snapshot: list[dict[str, Any]] = []
    for decision in recomputed.characters:
        records = [source_characters[item] for item in decision.source_record_ids]
        latest = _latest_character(records)
        source_display_name = _safe_display(
            latest.display_name,
            "Imported character",
            maximum=160,
            archive_members=archive_members,
        )
        display_name = source_display_name
        stored = connection.execute(
            """SELECT character.*, owner.owner_user_id
                 FROM canonical_characters AS character
                 LEFT JOIN canonical_character_owners AS owner USING(character_id)
                WHERE character.character_id = ?""",
            (decision.canonical_character_id,),
        ).fetchone()
        proof: str | None = None
        if decision.mode == "map":
            if (
                stored is None
                or stored["owner_user_id"] != owner_user_id
                or stored["visual_class"] != decision.visual_class
            ):
                if active_receipt_candidate_id is not None:
                    _fail_active_receipt_graph()
                _fail("mapping_target_unavailable", "Mapped character target is unavailable.")
            if stored["display_name"] is None:
                stored_display = "Imported character"
            else:
                stored_display = _safe_display(
                    stored["display_name"],
                    "Imported character",
                    maximum=160,
                    archive_members=archive_members,
                )
                if stored["display_name"] != stored_display:
                    if active_receipt_candidate_id is not None:
                        _fail_active_receipt_graph()
                    _fail("mapping_target_unsafe", "Mapped character metadata is unsafe.")
            character_disposition = "mapped"
            display_name = stored_display
        elif stored is None:
            if active_receipt_candidate_id is not None:
                _fail_active_receipt_graph()
            character_disposition = "created"
        else:
            exact = (
                stored["owner_user_id"] == owner_user_id
                and stored["visual_class"] == decision.visual_class
                and stored["display_name"] == display_name
            )
            if not exact:
                if active_receipt_candidate_id is not None:
                    _fail_active_receipt_graph()
                _fail("character_collision", "Derived character identity is unavailable.")
            proof = _prior_receipt_proof(
                connection,
                table="provider_import_character_memberships",
                identifier_column="character_id",
                identifier=decision.canonical_character_id,
                owner_user_id=owner_user_id,
                account_id=account_id,
                provider_scope=recomputed.provider_scope,
                allow_mapped_history=True,
                active_receipt_candidate_id=active_receipt_candidate_id,
            )
            character_disposition = "reused"
        character_row = StoreCharacterRow(
            character_id=decision.canonical_character_id,
            visual_class=decision.visual_class,
            display_name=display_name,
            disposition=character_disposition,
            mapping_mode=decision.mode,
            prior_receipt_proof_sha256=proof,
        )
        metadata = _character_metadata(decision.visual_class, source_display_name)
        link_row, link_actual = _resolve_character_link(
            connection,
            link_id=decision.provider_link_id,
            account_id=account_id,
            external_character_id=decision.external_character_id,
            character_id=decision.canonical_character_id,
            visual_class=decision.visual_class,
            metadata=metadata,
            owner_user_id=owner_user_id,
            provider_scope=recomputed.provider_scope,
            active_receipt_candidate_id=active_receipt_candidate_id,
        )
        if decision.mode == "separate" and (
            (character_disposition == "created") != (link_row.disposition == "created")
        ):
            _fail("character_graph_partial", "Derived character graph is incomplete.")
        previous_character = character_row_by_id.get(character_row.character_id)
        if previous_character is None:
            character_row_by_id[character_row.character_id] = character_row
            character_rows.append(character_row)
            character_snapshot.append(
                {
                    "actual": _character_actual(stored),
                    "planned": character_row.hash_dict(),
                }
            )
        elif previous_character.hash_dict() != character_row.hash_dict():
            _fail(
                "character_target_collision",
                "Multiple source characters resolve to an incompatible target.",
            )
        link_rows.append(link_row)
        link_snapshot.append(
            {"actual": link_actual, "planned": link_row.hash_dict()}
        )
        for source_record_id in decision.source_record_ids:
            decision_by_source_record[source_record_id] = decision

    source_media = {item.record_id: item for item in bundle.media}
    projection_groups: dict[str, list[Any]] = {}
    for identity in recomputed.media:
        projection_groups.setdefault(identity.projection_id, []).append(identity)
    projection_rows: list[StoreProjectionRow] = []
    projection_by_id: dict[str, StoreProjectionRow] = {}
    projection_snapshot: list[dict[str, Any]] = []
    for projection_id in sorted(projection_groups):
        identities = projection_groups[projection_id]
        external_ids = {item.external_asset_id for item in identities}
        kinds = {item.kind for item in identities}
        visual_classes = {item.visual_class for item in identities}
        if len(external_ids) != 1 or len(kinds) != 1 or len(visual_classes) != 1:
            _fail("projection_source_conflict", "Provider asset versions disagree.")
        visual_class = next(iter(visual_classes))
        latest_source = max(
            (source_media[item.source_media_record_id] for item in identities),
            key=lambda item: (
                item.created_at or "",
                item.external_ref.version,
                item.record_id,
            ),
        )
        projection_display = _safe_display(
            latest_source.display_name,
            "Imported media",
            maximum=300,
            archive_members=archive_members,
        )
        projection_row, projection_actual = _resolve_projection(
            connection,
            projection_id=projection_id,
            account_id=account_id,
            external_asset_id=next(iter(external_ids)),
            media_kind=next(iter(kinds)),
            visual_class=visual_class,
            metadata=_projection_metadata(visual_class, projection_display),
            remote_created_at=latest_source.created_at,
            owner_user_id=owner_user_id,
            provider_scope=recomputed.provider_scope,
            active_receipt_candidate_id=active_receipt_candidate_id,
        )
        projection_rows.append(projection_row)
        projection_by_id[projection_id] = projection_row
        projection_snapshot.append(
            {"actual": projection_actual, "planned": projection_row.hash_dict()}
        )

    version_rows: list[StoreVersionRow] = []
    version_snapshot: list[dict[str, Any]] = []
    for projection_id in sorted(projection_groups):
        next_number = int(
            connection.execute(
                """SELECT COALESCE(MAX(version_number), 0) + 1 AS next_number
                     FROM remote_asset_versions WHERE projection_id = ?""",
                (projection_id,),
            ).fetchone()["next_number"]
        )
        for identity in sorted(
            projection_groups[projection_id],
            key=lambda item: (item.external_version, item.source_media_record_id),
        ):
            version_row, version_actual, consumed_number = _resolve_version(
                connection,
                identity=identity,
                projection=projection_by_id[projection_id],
                version_number=next_number,
                owner_user_id=owner_user_id,
                account_id=account_id,
                provider_scope=recomputed.provider_scope,
                display_name=_safe_display(
                    source_media[identity.source_media_record_id].display_name,
                    "Imported media",
                    maximum=300,
                    archive_members=archive_members,
                ),
                active_receipt_candidate_id=active_receipt_candidate_id,
            )
            if consumed_number:
                next_number += 1
            version_rows.append(version_row)
            version_snapshot.append(
                {"actual": version_actual, "planned": version_row.hash_dict()}
            )

    asset_link_rows: list[StoreAssetCharacterLinkRow] = []
    asset_link_snapshot: list[dict[str, Any]] = []
    for projection_id in sorted(projection_groups):
        desired_characters = sorted(
            {
                decision_by_source_record[record_id].canonical_character_id
                for identity in projection_groups[projection_id]
                for record_id in identity.character_record_ids
            }
        )
        existing_rows = connection.execute(
            """SELECT link.character_id, owner.owner_user_id
                 FROM asset_character_links AS link
                 LEFT JOIN canonical_character_owners AS owner
                   ON owner.character_id = link.character_id
                WHERE link.projection_id = ?
                ORDER BY link.character_id LIMIT ?""",
            (projection_id, len(desired_characters) + 1),
        ).fetchall()
        existing = [str(item["character_id"]) for item in existing_rows]
        if any(item["owner_user_id"] != owner_user_id for item in existing_rows):
            if active_receipt_candidate_id is not None:
                _fail_active_receipt_graph()
            _fail("asset_character_owner_conflict", "Asset link crosses an owner boundary.")
        projection_disposition = projection_by_id[projection_id].disposition
        if projection_disposition == "reused" and existing != desired_characters:
            if active_receipt_candidate_id is not None:
                _fail_active_receipt_graph()
            _fail("asset_character_graph_partial", "Stored asset character graph differs.")
        if projection_disposition == "created" and existing:
            if active_receipt_candidate_id is not None:
                _fail_active_receipt_graph()
            _fail("asset_character_collision", "Provider asset identity is unavailable.")
        for character_id in desired_characters:
            asset_link_rows.append(
                StoreAssetCharacterLinkRow(
                    projection_id=projection_id,
                    character_id=character_id,
                    disposition="reused" if character_id in existing else "created",
                )
            )
        asset_link_snapshot.append(
            {
                "existing_character_ids": existing,
                "planned_character_ids": desired_characters,
                "projection_id": projection_id,
            }
        )

    relevant_lineage = _relevant_lineage(
        connection, sorted(projection_groups), owner_user_id
    )
    existing_lineage = {
        (
            str(item["parent_projection_id"]),
            str(item["child_projection_id"]),
            str(item["relation"]),
        ): item
        for item in relevant_lineage
    }
    desired_lineage: dict[tuple[str, str, str], Any] = {}
    for item in recomputed.lineage:
        key = (item.parent_projection_id, item.child_projection_id, item.relationship)
        previous = desired_lineage.get(key)
        if previous is None or item.lineage_id < previous.lineage_id:
            desired_lineage[key] = item
    adjacency: dict[str, set[str]] = {}
    for parent, child, _ in existing_lineage:
        adjacency.setdefault(parent, set()).add(child)
    for parent, child, _ in desired_lineage:
        adjacency.setdefault(parent, set()).add(child)
    if not _acyclic(adjacency):
        _fail("lineage_cycle", "Import would create a stored lineage cycle.")
    lineage_rows: list[StoreLineageRow] = []
    for key in sorted(desired_lineage):
        item = desired_lineage[key]
        existing = existing_lineage.get(key)
        proof: str | None = None
        if existing is not None:
            proof = _prior_lineage_receipt_proof(
                connection,
                parent_projection_id=item.parent_projection_id,
                child_projection_id=item.child_projection_id,
                relationship=item.relationship,
                owner_user_id=owner_user_id,
                account_id=account_id,
                provider_scope=recomputed.provider_scope,
                active_receipt_candidate_id=active_receipt_candidate_id,
            )
        lineage_rows.append(
            StoreLineageRow(
                lineage_id=item.lineage_id,
                parent_projection_id=item.parent_projection_id,
                child_projection_id=item.child_projection_id,
                relationship=item.relationship,
                disposition="reused" if existing is not None else "created",
                prior_receipt_proof_sha256=proof,
            )
        )

    stable_graph_payload = _stable_graph_payload(
        characters=character_rows,
        character_links=link_rows,
        projections=projection_rows,
        versions=version_rows,
        asset_character_links=asset_link_rows,
        lineage=lineage_rows,
    )
    stable_graph_sha256 = _domain_sha256(
        _STABLE_GRAPH_DOMAIN, stable_graph_payload
    )
    active_receipt_id = _validate_active_receipt_graph(
        connection,
        receipt_id=active_receipt_candidate_id,
        owner_user_id=owner_user_id,
        characters=character_rows,
        character_links=link_rows,
        projections=projection_rows,
        versions=version_rows,
        asset_character_links=asset_link_rows,
        lineage=lineage_rows,
        profile_renditions=normalized_renditions,
        asset_previews=normalized_previews,
        stable_graph_sha256=stable_graph_sha256,
    )

    graph_payload = {
        "asset_character_links": [item.hash_dict() for item in asset_link_rows],
        "character_links": [item.hash_dict() for item in link_rows],
        "characters": [item.hash_dict() for item in character_rows],
        "lineage": [item.hash_dict() for item in lineage_rows],
        "projections": [item.hash_dict() for item in projection_rows],
        "versions": [item.hash_dict() for item in version_rows],
        "profile_renditions": [
            item.hash_dict() for item in normalized_renditions
        ],
        "asset_previews": [item.hash_dict() for item in normalized_previews],
    }
    graph_sha256 = _domain_sha256(_GRAPH_DOMAIN, graph_payload)
    snapshot_payload = {
        "account": account_snapshot,
        "asset_character_links": asset_link_snapshot,
        "character_links": link_snapshot,
        "characters": character_snapshot,
        "lineage": relevant_lineage,
        "projections": projection_snapshot,
        "versions": version_snapshot,
    }
    snapshot_sha256 = _domain_sha256(_SNAPSHOT_DOMAIN, snapshot_payload)
    all_dispositions = [
        *(item.disposition for item in character_rows),
        *(item.disposition for item in link_rows),
        *(item.disposition for item in projection_rows),
        *(item.disposition for item in version_rows),
        *(item.disposition for item in asset_link_rows),
        *(item.disposition for item in lineage_rows),
    ]
    summary = {
        "active_receipt_replay": active_receipt_id is not None,
        "asset_character_links": len(asset_link_rows),
        "character_links": len(link_rows),
        "characters": len(character_rows),
        "created": all_dispositions.count("created"),
        "lineage": len(lineage_rows),
        "mapped": all_dispositions.count("mapped"),
        "materialized_sources": 0,
        "profile_selections": len(normalized_renditions),
        "profile_rendition_bytes": sum(
            item.byte_size for item in normalized_renditions
        ),
        "asset_previews": len(normalized_previews),
        "asset_preview_bytes": sum(item.byte_size for item in normalized_previews),
        "projections": len(projection_rows),
        "remote_only_versions": sum(
            item.availability == "remote_only" for item in recomputed.media
        ),
        "reused": all_dispositions.count("reused"),
        "versions": len(version_rows),
    }
    plan_core = {
        "decision_plan_sha256": recomputed.plan_sha256,
        "graph_sha256": graph_sha256,
        "stable_graph_sha256": stable_graph_sha256,
        "import_decision_sha256": recomputed.import_decision_sha256,
        "plan_version": STORE_PLAN_VERSION,
        "schema_version": STORE_PLAN_SCHEMA_VERSION,
        "snapshot_sha256": snapshot_sha256,
        "summary": summary,
    }
    plan_sha256 = _domain_sha256(_PLAN_DOMAIN, plan_core)
    return StoreOwnerImportPlan(
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=recomputed.provider_scope,
        decision_plan_sha256=recomputed.plan_sha256,
        import_decision_sha256=recomputed.import_decision_sha256,
        graph_sha256=graph_sha256,
        stable_graph_sha256=stable_graph_sha256,
        snapshot_sha256=snapshot_sha256,
        plan_sha256=plan_sha256,
        active_receipt_id=active_receipt_id,
        characters=tuple(character_rows),
        character_links=tuple(link_rows),
        projections=tuple(projection_rows),
        versions=tuple(version_rows),
        asset_character_links=tuple(asset_link_rows),
        lineage=tuple(lineage_rows),
        profile_renditions=tuple(normalized_renditions),
        asset_previews=tuple(normalized_previews),
        summary=_freeze(summary),
    )


__all__ = [
    "STORE_PLAN_SCHEMA_VERSION",
    "STORE_PLAN_VERSION",
    "SUPPORTED_STORE_SCHEMA_VERSION",
    "StoreAssetCharacterLinkRow",
    "StoreCharacterLinkRow",
    "StoreCharacterRow",
    "StoreLineageRow",
    "StoreOwnerImportPlan",
    "StoreOwnerImportPlanError",
    "StoreProjectionRow",
    "StoreVersionRow",
    "plan_owned_provider_import_in_connection",
]
