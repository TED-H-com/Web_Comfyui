"""Unrouted stage-to-object-to-database owner import coordinator.

Lock order is fixed: private stage lease, private media publication, then the
provider-store confirmation transaction.  Published content-addressed objects
are immutable; if database confirmation fails they remain unreachable and may
only be reclaimed by a later reachability/quarantine maintenance job.
"""

from __future__ import annotations

from typing import Any, Mapping

from .import_storage import ImportStageNotFound, PrivateImportStageStore
from .media_materializer import MaterializedImport, PrivateProviderMediaStore
from .owner_import import OwnerImportDecisionPlan
from .owner_import_previews import build_owner_asset_previews
from .owner_import_renditions import build_owner_profile_renditions
from .store import ProviderFabricStore


def confirm_staged_owner_import(
    *,
    stage_store: PrivateImportStageStore,
    media_store: PrivateProviderMediaStore,
    provider_store: ProviderFabricStore,
    owner_user_id: str,
    account_id: str,
    stage_id: str,
    stage_token: str,
    receipt_id: str,
    request: Mapping[str, Any],
    decision_plan: OwnerImportDecisionPlan,
    expected_plan_sha256: str,
    expected_graph_sha256: str,
    expected_stable_graph_sha256: str,
    expected_snapshot_sha256: str,
) -> dict[str, Any]:
    """Publish and atomically bind one exact, actively leased private stage.

    A successful receipt is followed, under the same stage lease, by replacing
    source bytes with an authenticated short-lived replay tombstone.  A lost
    response can therefore resolve the exact receipt without retaining or
    reparsing the provider export.
    """

    if not isinstance(stage_store, PrivateImportStageStore):
        raise TypeError("stage_store must be a PrivateImportStageStore")
    if not isinstance(media_store, PrivateProviderMediaStore):
        raise TypeError("media_store must be a PrivateProviderMediaStore")
    if not isinstance(provider_store, ProviderFabricStore):
        raise TypeError("provider_store must be a ProviderFabricStore")
    if not isinstance(decision_plan, OwnerImportDecisionPlan):
        raise TypeError("decision_plan must be an OwnerImportDecisionPlan")

    try:
        consumed = stage_store.load_consumed_stage(
            owner_user_id,
            account_id,
            stage_id,
            stage_token,
            import_decision_sha256=decision_plan.import_decision_sha256,
            decision_plan_sha256=decision_plan.plan_sha256,
            plan_sha256=expected_plan_sha256,
            stable_graph_sha256=expected_stable_graph_sha256,
        )
    except ImportStageNotFound:
        consumed = None
    if consumed is not None:
        receipt = provider_store.get_owned_provider_import_receipt(
            owner_user_id, consumed.receipt_id
        )
        if (
            receipt["account_id"] != account_id
            or receipt["import_decision_sha256"]
            != decision_plan.import_decision_sha256
            or receipt["plan_sha256"] != expected_plan_sha256
        ):
            raise ImportStageNotFound("provider import stage was not found")
        result = dict(receipt)
        result["replayed"] = True
        result["stage_consumed"] = True
        return result

    with stage_store.active_stage_lease(
        owner_user_id, account_id, stage_id, stage_token
    ) as staged:
        rendered_profiles = build_owner_profile_renditions(staged, decision_plan)
        rendered_previews = build_owner_asset_previews(
            staged, decision_plan, rendered_profiles
        )
        materialized_originals = media_store.publish_bundled_originals(
            owner_user_id, staged
        )
        materialized_renditions = media_store.publish_import_renditions(
            owner_user_id, staged, rendered_profiles, rendered_previews
        )
        materialized = MaterializedImport(
            originals=materialized_originals.originals,
            created_object_count=(
                materialized_originals.created_object_count
                + materialized_renditions.created_object_count
            ),
            created_bytes=(
                materialized_originals.created_bytes
                + materialized_renditions.created_bytes
            ),
            reused_object_count=(
                materialized_originals.reused_object_count
                + materialized_renditions.reused_object_count
            ),
            profile_renditions=materialized_renditions.profile_renditions,
            asset_previews=materialized_renditions.asset_previews,
        )
        # Reopen and rehash every immutable object immediately before the DB
        # transaction; MaterializedImport itself is evidence, never authority.
        unique_objects: set[tuple[str, int, str]] = set()
        for item in materialized.originals:
            unique_objects.add(
                (item.sha256, item.byte_size, item.signature_family)
            )
        for item in materialized.profile_renditions:
            unique_objects.add(
                (item.rendition_sha256, item.byte_size, item.signature_family)
            )
        for item in materialized.asset_previews:
            unique_objects.add(
                (item.rendition_sha256, item.byte_size, item.signature_family)
            )
        for digest, byte_size, signature_family in sorted(unique_objects):
            media_store.verify_owned_object(
                owner_user_id, digest, byte_size, signature_family
            )
        receipt = provider_store.confirm_owned_provider_import(
            owner_user_id,
            account_id,
            receipt_id,
            staged.bundle,
            request,
            decision_plan,
            expected_plan_sha256=expected_plan_sha256,
            expected_graph_sha256=expected_graph_sha256,
            expected_stable_graph_sha256=expected_stable_graph_sha256,
            expected_snapshot_sha256=expected_snapshot_sha256,
            materialized_import=materialized,
            profile_renditions=rendered_profiles,
            asset_previews=rendered_previews,
        )
        stage_store.consume_active_stage(
            owner_user_id,
            staged,
            receipt_id=receipt["receipt_id"],
            import_decision_sha256=decision_plan.import_decision_sha256,
            decision_plan_sha256=decision_plan.plan_sha256,
            plan_sha256=expected_plan_sha256,
            stable_graph_sha256=expected_stable_graph_sha256,
        )
    result = dict(receipt)
    result["materialization"] = materialized.to_public_dict()
    result["stage_consumed"] = True
    return result


__all__ = ["confirm_staged_owner_import"]
