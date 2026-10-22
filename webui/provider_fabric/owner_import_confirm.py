"""Atomic metadata confirmation for reviewed owner-scoped ClipMode imports.

The filesystem materializer remains a preceding concern.  This module owns the
single SQLite transaction that re-plans the exact bundle, publishes its
canonical metadata graph, records complete receipt membership, and seals the
receipt.  It performs no filesystem or network I/O.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping, Sequence

from .clipmode_import import ClipModeImportBundle
from .owner_import import (
    OwnerImportDecisionPlan,
    OwnerImportPlanError,
    build_owner_import_decision_plan,
)
from .owner_import_store import (
    STORE_PLAN_VERSION,
    StoreOwnerImportPlanError,
    plan_owned_provider_import_in_connection,
)
from .media_materializer import MaterializedImport
from .owner_import_renditions import OwnerProfileRendition
from .owner_import_previews import OwnerAssetPreviewRendition


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class OwnerImportConfirmationError(ValueError):
    """A public-safe atomic confirmation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise OwnerImportConfirmationError(code, message)


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    return value


def _metadata(value: Mapping[str, Any]) -> str:
    return json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _exact_replay(
    connection: sqlite3.Connection,
    *,
    owner_user_id: str,
    decision_plan: OwnerImportDecisionPlan,
    expected_plan_sha256: str,
    expected_stable_graph_sha256: str,
    materialized_bindings: tuple[tuple[str, str, int, str, str], ...],
) -> str | None:
    row = connection.execute(
        """SELECT receipt.receipt_id, receipt.owner_user_id,
                  receipt.account_id, receipt.provider_scope,
                  receipt.plan_sha256, evidence.decision_plan_sha256,
                  evidence.stable_graph_sha256
             FROM provider_import_receipts AS receipt
             LEFT JOIN provider_import_plan_evidence AS evidence
               ON evidence.receipt_id = receipt.receipt_id
            WHERE receipt.import_decision_sha256 = ?
              AND receipt.state = 'active'""",
        (decision_plan.import_decision_sha256,),
    ).fetchone()
    if row is None:
        return None
    if (
        row["owner_user_id"] != owner_user_id
        or row["account_id"] != decision_plan.account_id
        or row["provider_scope"] != decision_plan.provider_scope
        or row["plan_sha256"] != expected_plan_sha256
        or row["decision_plan_sha256"] != decision_plan.plan_sha256
        or row["stable_graph_sha256"] != expected_stable_graph_sha256
    ):
        _fail("confirmation_conflict", "The prior import confirmation is unavailable.")
    stored_bindings = connection.execute(
        """SELECT binding.version_id, binding.sha256, blob.byte_size,
                  blob.signature_family, binding.declared_mime
             FROM provider_import_version_memberships AS membership
             JOIN provider_asset_version_blobs AS binding
               ON binding.owner_user_id = membership.owner_user_id
              AND binding.version_id = membership.version_id
              AND binding.role = 'original'
             JOIN provider_media_blobs AS blob
               ON blob.owner_user_id = binding.owner_user_id
              AND blob.sha256 = binding.sha256
             JOIN provider_import_receipts AS binding_receipt
               ON binding_receipt.receipt_id = binding.receipt_id
              AND binding_receipt.state = 'active'
            WHERE membership.receipt_id = ?
            ORDER BY binding.version_id""",
        (row["receipt_id"],),
    ).fetchall()
    stored = tuple(
        (
            str(item["version_id"]),
            str(item["sha256"]),
            int(item["byte_size"]),
            str(item["signature_family"]),
            str(item["declared_mime"]),
        )
        for item in stored_bindings
    )
    if stored != materialized_bindings:
        _fail("confirmation_conflict", "The prior import confirmation is unavailable.")
    return str(row["receipt_id"])


def _materialized_bindings(
    decision_plan: OwnerImportDecisionPlan,
    materialized_import: MaterializedImport | None,
) -> tuple[tuple[str, str, int, str, str], ...]:
    if materialized_import is None:
        return ()
    if not isinstance(materialized_import, MaterializedImport):
        raise TypeError("materialized_import must be a MaterializedImport")
    expected = {
        item.source_media_record_id: item
        for item in decision_plan.media
        if item.availability == "bundled"
    }
    supplied: dict[str, Any] = {}
    for item in materialized_import.originals:
        if item.media_record_id in supplied:
            _fail("materialization_mismatch", "Materialized media evidence is unavailable.")
        supplied[item.media_record_id] = item
    if set(supplied) != set(expected):
        _fail("materialization_mismatch", "Materialized media evidence is unavailable.")
    bindings: list[tuple[str, str, int, str, str]] = []
    for record_id, identity in sorted(expected.items()):
        item = supplied[record_id]
        if (
            item.sha256 != identity.content_sha256
            or item.byte_size != identity.byte_size
            or item.declared_mime != identity.mime_type
            or item.signature_family != identity.signature_family
        ):
            _fail("materialization_mismatch", "Materialized media evidence is unavailable.")
        bindings.append(
            (
                identity.version_id,
                item.sha256,
                item.byte_size,
                item.signature_family,
                item.declared_mime,
            )
        )
    return tuple(bindings)


def _materialized_profile_bindings(
    decision_plan: OwnerImportDecisionPlan,
    materialized_import: MaterializedImport | None,
    profile_renditions: Sequence[OwnerProfileRendition],
) -> tuple[tuple[str, str, str, str, int, str, str], ...]:
    if isinstance(profile_renditions, (str, bytes, bytearray)) or not isinstance(
        profile_renditions, Sequence
    ):
        raise TypeError("profile_renditions must be a sequence")
    expected = {
        item.source_character_key: item for item in decision_plan.profiles
    }
    rendered: dict[str, OwnerProfileRendition] = {}
    for item in profile_renditions:
        if not isinstance(item, OwnerProfileRendition):
            raise TypeError("profile_renditions contains an invalid item")
        if item.source_character_key in rendered:
            _fail("materialization_mismatch", "Profile rendition evidence is unavailable.")
        rendered[item.source_character_key] = item
    materialized = (
        {}
        if materialized_import is None
        else {
            item.source_character_key: item
            for item in materialized_import.profile_renditions
        }
    )
    if set(rendered) != set(expected) or set(materialized) != set(expected):
        _fail("materialization_mismatch", "Profile rendition evidence is unavailable.")
    bindings: list[tuple[str, str, str, str, int, str, str]] = []
    for source_key in sorted(expected):
        profile = expected[source_key]
        rendition = rendered[source_key]
        stored = materialized[source_key]
        if (
            rendition.canonical_character_id != profile.canonical_character_id
            or rendition.projection_id != profile.projection_id
            or rendition.version_id != profile.version_id
            or rendition.source_sha256 != profile.source_sha256
            or stored.canonical_character_id != profile.canonical_character_id
            or stored.projection_id != profile.projection_id
            or stored.version_id != profile.version_id
            or stored.source_sha256 != profile.source_sha256
            or stored.rendition_sha256 != rendition.rendition_sha256
            or stored.byte_size != rendition.byte_size
            or stored.declared_mime != rendition.mime_type
            or stored.signature_family != rendition.signature_family
            or stored.width != rendition.width
            or stored.height != rendition.height
        ):
            _fail("materialization_mismatch", "Profile rendition evidence is unavailable.")
        bindings.append(
            (
                profile.canonical_character_id,
                profile.projection_id,
                profile.version_id,
                rendition.rendition_sha256,
                rendition.byte_size,
                rendition.signature_family,
                rendition.mime_type,
            )
        )
    return tuple(bindings)


def _materialized_asset_preview_bindings(
    decision_plan: OwnerImportDecisionPlan,
    materialized_import: MaterializedImport | None,
    asset_previews: Sequence[OwnerAssetPreviewRendition],
) -> tuple[tuple[str, str, str, str, str, int, str, str, str], ...]:
    if isinstance(asset_previews, (str, bytes, bytearray)) or not isinstance(
        asset_previews, Sequence
    ):
        raise TypeError("asset_previews must be a sequence")
    rendered: dict[str, OwnerAssetPreviewRendition] = {}
    for item in asset_previews:
        if not isinstance(item, OwnerAssetPreviewRendition):
            raise TypeError("asset_previews contains an invalid item")
        if item.projection_id in rendered:
            _fail("materialization_mismatch", "Asset preview evidence is unavailable.")
        rendered[item.projection_id] = item
    materialized = (
        {} if materialized_import is None else
        {item.projection_id: item for item in materialized_import.asset_previews}
    )
    if set(rendered) != set(materialized):
        _fail("materialization_mismatch", "Asset preview evidence is unavailable.")
    media = {item.version_id: item for item in decision_plan.media}
    bindings = []
    for projection_id in sorted(rendered):
        rendition = rendered[projection_id]
        stored = materialized[projection_id]
        source = media.get(rendition.version_id)
        if (
            source is None
            or source.projection_id != projection_id
            or source.source_media_record_id != rendition.source_media_record_id
            or source.content_sha256 != rendition.source_sha256
            or stored.source_media_record_id != rendition.source_media_record_id
            or stored.projection_id != projection_id
            or stored.version_id != rendition.version_id
            or stored.source_sha256 != rendition.source_sha256
            or stored.rendition_sha256 != rendition.rendition_sha256
            or stored.byte_size != rendition.byte_size
            or stored.declared_mime != rendition.mime_type
            or stored.signature_family != rendition.signature_family
            or stored.width != rendition.width
            or stored.height != rendition.height
            or stored.selection_source != rendition.selection_source
        ):
            _fail("materialization_mismatch", "Asset preview evidence is unavailable.")
        bindings.append((
            projection_id, rendition.version_id, rendition.source_media_record_id,
            rendition.source_sha256, rendition.rendition_sha256, rendition.byte_size,
            rendition.signature_family, rendition.mime_type, rendition.selection_source,
        ))
    return tuple(bindings)


def confirm_owned_provider_import_in_connection(
    connection: sqlite3.Connection,
    *,
    owner_user_id: str,
    account_id: str,
    receipt_id: str,
    bundle: ClipModeImportBundle,
    request: Mapping[str, Any],
    decision_plan: OwnerImportDecisionPlan,
    expected_plan_sha256: str,
    expected_graph_sha256: str,
    expected_stable_graph_sha256: str,
    expected_snapshot_sha256: str,
    now: str,
    materialized_import: MaterializedImport | None = None,
    profile_renditions: Sequence[OwnerProfileRendition] = (),
    asset_previews: Sequence[OwnerAssetPreviewRendition] = (),
) -> dict[str, Any]:
    """Confirm one exact metadata graph inside a caller-owned write transaction."""

    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        raise ValueError("confirmation requires a caller-owned transaction")
    expected_plan_sha256 = _digest(expected_plan_sha256, "expected_plan_sha256")
    expected_graph_sha256 = _digest(expected_graph_sha256, "expected_graph_sha256")
    expected_stable_graph_sha256 = _digest(
        expected_stable_graph_sha256, "expected_stable_graph_sha256"
    )
    expected_snapshot_sha256 = _digest(
        expected_snapshot_sha256, "expected_snapshot_sha256"
    )
    try:
        recomputed_decision = build_owner_import_decision_plan(
            bundle,
            request,
            owner_user_id=owner_user_id,
            account_id=account_id,
        )
    except OwnerImportPlanError as error:
        raise OwnerImportConfirmationError(
            "decision_revalidation_failed", "The import decision is unavailable."
        ) from error
    if recomputed_decision != decision_plan:
        _fail("decision_plan_mismatch", "The import decision no longer matches.")
    materialized_bindings = _materialized_bindings(
        decision_plan, materialized_import
    )
    materialized_profiles = _materialized_profile_bindings(
        decision_plan, materialized_import, profile_renditions
    )
    materialized_previews = _materialized_asset_preview_bindings(
        decision_plan, materialized_import, asset_previews
    )

    replay_id = _exact_replay(
        connection,
        owner_user_id=owner_user_id,
        decision_plan=decision_plan,
        expected_plan_sha256=expected_plan_sha256,
        expected_stable_graph_sha256=expected_stable_graph_sha256,
        materialized_bindings=materialized_bindings,
    )
    if replay_id is not None:
        return {"receipt_id": replay_id, "replayed": True}

    try:
        plan = plan_owned_provider_import_in_connection(
            connection,
            owner_user_id=owner_user_id,
            account_id=account_id,
            bundle=bundle,
            request=request,
            decision_plan=decision_plan,
            profile_renditions=profile_renditions,
            asset_previews=asset_previews,
        )
    except StoreOwnerImportPlanError as error:
        raise OwnerImportConfirmationError("plan_stale", "The import plan is stale.") from error
    evidence = {
        "plan_sha256": plan.plan_sha256,
        "graph_sha256": plan.graph_sha256,
        "stable_graph_sha256": plan.stable_graph_sha256,
        "snapshot_sha256": plan.snapshot_sha256,
    }
    expected = {
        "plan_sha256": expected_plan_sha256,
        "graph_sha256": expected_graph_sha256,
        "stable_graph_sha256": expected_stable_graph_sha256,
        "snapshot_sha256": expected_snapshot_sha256,
    }
    if evidence != expected or plan.active_receipt_id is not None:
        _fail("plan_stale", "The import plan is stale.")

    for item in plan.characters:
        if item.disposition != "created":
            continue
        connection.execute(
            """INSERT INTO canonical_characters(
                   character_id, visual_class, display_name, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?)""",
            (item.character_id, item.visual_class, item.display_name, now, now),
        )
        connection.execute(
            """INSERT INTO canonical_character_owners(
                   character_id, owner_user_id, bound_at
               ) VALUES (?, ?, ?)""",
            (item.character_id, owner_user_id, now),
        )
    for item in plan.character_links:
        if item.disposition == "created":
            connection.execute(
                """INSERT INTO provider_character_links(
                       link_id, account_id, external_character_id, character_id,
                       visual_class, metadata_json, linked_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.link_id,
                    item.account_id,
                    item.external_character_id,
                    item.character_id,
                    item.visual_class,
                    _metadata(item.metadata),
                    now,
                ),
            )
    for item in plan.projections:
        if item.disposition != "created":
            continue
        connection.execute(
            """INSERT INTO remote_asset_projections(
                   projection_id, account_id, external_asset_id, media_kind,
                   visual_class, metadata_json, remote_created_at,
                   first_seen_at, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.projection_id,
                item.account_id,
                item.external_asset_id,
                item.media_kind,
                item.visual_class,
                _metadata(item.metadata),
                item.remote_created_at,
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO remote_asset_owners(
                   projection_id, owner_user_id, bound_at
               ) VALUES (?, ?, ?)""",
            (item.projection_id, owner_user_id, now),
        )
    for item in plan.versions:
        if item.disposition == "created":
            connection.execute(
                """INSERT INTO remote_asset_versions(
                       version_id, projection_id, version_number,
                       provider_version_id, source_uri, preview_uri, mime_type,
                       byte_size, sha256, duration_seconds, metadata_json, observed_at
                   ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?)""",
                (
                    item.version_id,
                    item.projection_id,
                    item.version_number,
                    item.provider_version_id,
                    item.source_uri,
                    item.mime_type,
                    item.byte_size,
                    item.sha256,
                    _metadata(item.metadata),
                    now,
                ),
            )
    for item in plan.asset_character_links:
        if item.disposition == "created":
            connection.execute(
                """INSERT INTO asset_character_links(
                       projection_id, character_id, linked_at
                   ) VALUES (?, ?, ?)""",
                (item.projection_id, item.character_id, now),
            )
    for item in plan.lineage:
        if item.disposition == "created":
            connection.execute(
                """INSERT INTO asset_lineage(
                       parent_projection_id, child_projection_id, relation,
                       intent_id, created_at
                   ) VALUES (?, ?, ?, NULL, ?)""",
                (
                    item.parent_projection_id,
                    item.child_projection_id,
                    item.relationship,
                    now,
                ),
            )

    provenance = bundle.provenance
    connection.execute(
        """INSERT INTO provider_import_receipts(
               receipt_id, owner_user_id, account_id, provider_id,
               provider_scope, source_schema, source_schema_version,
               export_id, exported_at, raw_source_sha256, manifest_sha256,
               semantic_sha256, mapping_sha256, profile_selection_sha256,
               account_binding_sha256, plan_version, plan_sha256,
               import_decision_sha256, state, created_at
           ) VALUES (?, ?, ?, 'clipmode', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                     'building', ?)""",
        (
            receipt_id,
            owner_user_id,
            account_id,
            decision_plan.provider_scope,
            provenance.schema_name,
            provenance.schema_version,
            provenance.export_id,
            provenance.exported_at,
            decision_plan.raw_source_sha256,
            decision_plan.manifest_sha256,
            decision_plan.semantic_sha256,
            decision_plan.mapping_sha256,
            decision_plan.profile_selection_sha256,
            decision_plan.account_binding_sha256,
            STORE_PLAN_VERSION,
            plan.plan_sha256,
            decision_plan.import_decision_sha256,
            now,
        ),
    )
    connection.execute(
        """INSERT INTO provider_import_plan_evidence(
               receipt_id, owner_user_id, decision_plan_sha256,
               stable_graph_sha256, recorded_at
           ) VALUES (?, ?, ?, ?, ?)""",
        (receipt_id, owner_user_id, decision_plan.plan_sha256, plan.stable_graph_sha256, now),
    )
    memberships = (
        ("provider_import_character_memberships", "character_id", plan.characters),
        ("provider_import_character_link_memberships", "link_id", plan.character_links),
        ("provider_import_projection_memberships", "projection_id", plan.projections),
        ("provider_import_version_memberships", "version_id", plan.versions),
    )
    for table, identifier_column, rows in memberships:
        for item in rows:
            connection.execute(
                f"""INSERT INTO {table}(
                       receipt_id, owner_user_id, {identifier_column},
                       disposition, recorded_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    receipt_id,
                    owner_user_id,
                    getattr(item, identifier_column),
                    item.disposition,
                    now,
                ),
            )
    for item in plan.lineage:
        connection.execute(
            """INSERT INTO provider_import_lineage_memberships(
                   receipt_id, owner_user_id, parent_projection_id,
                   child_projection_id, relation, disposition, recorded_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                receipt_id,
                owner_user_id,
                item.parent_projection_id,
                item.child_projection_id,
                item.relationship,
                item.disposition,
                now,
            ),
        )
    blobs: dict[str, tuple[int, str]] = {}
    for _version_id, digest, byte_size, signature_family, _mime in materialized_bindings:
        previous = blobs.get(digest)
        evidence = (byte_size, signature_family)
        if previous is not None and previous != evidence:
            _fail("materialization_mismatch", "Materialized media evidence is unavailable.")
        blobs[digest] = evidence
    for (
        _character_id,
        _projection_id,
        _version_id,
        digest,
        byte_size,
        signature_family,
        _mime,
    ) in materialized_profiles:
        previous = blobs.get(digest)
        evidence = (byte_size, signature_family)
        if previous is not None and previous != evidence:
            _fail("materialization_mismatch", "Profile rendition evidence is unavailable.")
        blobs[digest] = evidence
    for (
        _projection_id, _version_id, _record_id, _source_sha256,
        digest, byte_size, signature_family, _mime, _selection_source,
    ) in materialized_previews:
        previous = blobs.get(digest)
        evidence = (byte_size, signature_family)
        if previous is not None and previous != evidence:
            _fail("materialization_mismatch", "Asset preview evidence is unavailable.")
        blobs[digest] = evidence
    for digest, (byte_size, signature_family) in sorted(blobs.items()):
        existing_blob = connection.execute(
            """SELECT byte_size, signature_family FROM provider_media_blobs
                WHERE owner_user_id = ? AND sha256 = ?""",
            (owner_user_id, digest),
        ).fetchone()
        if existing_blob is None:
            connection.execute(
                """INSERT INTO provider_media_blobs(
                       owner_user_id, sha256, byte_size, signature_family, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (owner_user_id, digest, byte_size, signature_family, now),
            )
        elif (
            int(existing_blob["byte_size"]) != byte_size
            or existing_blob["signature_family"] != signature_family
        ):
            _fail("materialization_mismatch", "Materialized media evidence is unavailable.")
    for version_id, digest, _byte_size, _signature_family, declared_mime in materialized_bindings:
        existing_binding = connection.execute(
            """SELECT sha256, declared_mime FROM provider_asset_version_blobs
                WHERE owner_user_id = ? AND version_id = ? AND role = 'original'""",
            (owner_user_id, version_id),
        ).fetchone()
        if existing_binding is None:
            connection.execute(
                """INSERT INTO provider_asset_version_blobs(
                       owner_user_id, version_id, role, receipt_id, sha256,
                       declared_mime, bound_at
                   ) VALUES (?, ?, 'original', ?, ?, ?, ?)""",
                (owner_user_id, version_id, receipt_id, digest, declared_mime, now),
            )
        elif (
            existing_binding["sha256"] != digest
            or existing_binding["declared_mime"] != declared_mime
        ):
            _fail("materialization_mismatch", "Materialized media evidence is unavailable.")
    for (
        projection_id, version_id, _record_id, _source_sha256,
        rendition_sha256, _byte_size, _signature_family, declared_mime,
        _selection_source,
    ) in materialized_previews:
        if connection.execute(
            """SELECT 1 FROM provider_asset_version_blobs
                WHERE owner_user_id = ? AND version_id = ?
                  AND role = 'preview_rendition'""",
            (owner_user_id, version_id),
        ).fetchone() is not None:
            _fail("preview_conflict", "Asset preview is already selected.")
        connection.execute(
            """INSERT INTO provider_asset_version_blobs(
                   owner_user_id, version_id, role, receipt_id, sha256,
                   declared_mime, bound_at
               ) VALUES (?, ?, 'preview_rendition', ?, ?, ?, ?)""",
            (owner_user_id, version_id, receipt_id, rendition_sha256, declared_mime, now),
        )
        if connection.execute(
            """SELECT 1 FROM projection_preview_versions
                WHERE owner_user_id = ? AND projection_id = ?""",
            (owner_user_id, projection_id),
        ).fetchone() is not None:
            _fail("preview_conflict", "Asset preview is already selected.")
        connection.execute(
            """INSERT INTO projection_preview_versions(
                   owner_user_id, projection_id, version_id,
                   rendition_sha256, receipt_id, selection_source, selected_at
               ) VALUES (?, ?, ?, ?, ?, 'explicit', ?)""",
            (owner_user_id, projection_id, version_id, rendition_sha256, receipt_id, now),
        )
    for (
        character_id,
        projection_id,
        version_id,
        rendition_sha256,
        _byte_size,
        _signature_family,
        declared_mime,
    ) in materialized_profiles:
        if connection.execute(
            """SELECT 1 FROM canonical_character_profiles
                WHERE character_id = ?""",
            (character_id,),
        ).fetchone() is not None:
            _fail("profile_conflict", "Character profile is already selected.")
        connection.execute(
            """INSERT INTO canonical_character_profiles(
                   character_id, projection_id, selection_source, selected_at
               ) VALUES (?, ?, 'explicit', ?)""",
            (character_id, projection_id, now),
        )
        connection.execute(
            """INSERT INTO canonical_character_profile_versions(
                   character_id, owner_user_id, projection_id, version_id,
                   rendition_sha256, receipt_id, selection_source, selected_at
               ) VALUES (?, ?, ?, ?, ?, ?, 'explicit', ?)""",
            (
                character_id,
                owner_user_id,
                projection_id,
                version_id,
                rendition_sha256,
                receipt_id,
                now,
            ),
        )
    updated = connection.execute(
        """UPDATE provider_import_receipts SET state = 'active'
            WHERE receipt_id = ? AND state = 'building'""",
        (receipt_id,),
    )
    if updated.rowcount != 1:
        _fail("confirmation_failed", "The import could not be confirmed.")
    return {"receipt_id": receipt_id, "replayed": False}


__all__ = [
    "OwnerImportConfirmationError",
    "confirm_owned_provider_import_in_connection",
]
