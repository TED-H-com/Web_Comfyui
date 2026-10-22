"""Dormant contract for routing Novel Scene Art through Castle's web platform.

This module is deliberately not imported by ``webui/server.py``.  It parses a
non-secret, disabled policy document and audits reusable platform primitives;
it cannot authenticate a request, submit a ComfyUI prompt, or activate a route.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


SCHEMA_VERSION = "castle.novel-scene-art-platform-service-account.v2"
MANIFEST_RELATIVE_PATH = Path(
    "manifests/novel_scene_art/platform-service-account.disabled.json"
)
SERVICE_PRINCIPAL = "castle-service:novel-scene-art"
PLATFORM_ORIGIN = "http://127.0.0.1:8190"
ROUTE_PREFIX = "/api/internal/novel-scene-art/v1/"
PRIVATE_SINK_ROOT = "/var/lib/web-comfyui-novel-scene-art/private/outputs"
SUBMIT_SCHEMA_ID = "castle.novel-scene-art-platform-submit.v1"
ACK_SCHEMA_ID = "castle.novel-scene-art-output-ack.v1"
CAPACITY_SCHEMA_VERSION = "castle.novel-scene-art-capacity.v1"
PRIVATE_STORAGE_ROOT = "/var/lib/web-comfyui-novel-scene-art/private"
PRIVATE_DATABASE_PATH = f"{PRIVATE_STORAGE_ROOT}/platform.sqlite3"

REQUEST_MAX_RECORD_BYTES = 99_340
REQUEST_MAX_CHARGED_OBJECTS = 16
REQUEST_MAX_CHARGED_LOGICAL_BYTES = 1_589_440
REQUEST_MAX_CHARGED_ALLOCATED_BYTES = 2_097_152
OUTPUT_MAX_PNG_BYTES = 12 * 1024 * 1024
OUTPUT_MAX_MANIFEST_BYTES = 1_024
OUTPUT_MAX_CHARGED_OBJECTS = 32
OUTPUT_MAX_CHARGED_LOGICAL_BYTES = 402_685_952
OUTPUT_MAX_CHARGED_ALLOCATED_BYTES = 512 * 1024 * 1024
PRIVATE_FILESYSTEM_HARD_LIMIT_BYTES = 1024 * 1024 * 1024
PRIVATE_FILESYSTEM_HARD_LIMIT_INODES = 4_096
METADATA_PAGE_SIZE_BYTES = 4_096
METADATA_MAX_PAGE_COUNT = 65_536
METADATA_MAX_MAIN_DATABASE_BYTES = 256 * 1024 * 1024
METADATA_MAX_WAL_BYTES = 64 * 1024 * 1024
METADATA_MAX_SHM_BYTES = 8 * 1024 * 1024

EXPECTED_SUBMIT_SCHEMA_DEFINITION = {
    "root_exact_fields": ["render_request", "schema_sha256", "schema_version"],
    "render_request_exact_fields": [
        "generation_key", "job_id", "output", "recipe_id", "render_plan",
        "render_plan_hash", "request_id", "scene_pack", "scene_pack_hash",
        "schema_version", "style_preset", "workflow_contract_revision",
    ],
    "scene_pack_exact_fields": [
        "identity_slots", "moment", "participants", "point_of_view",
        "presentation", "recipe", "render", "schema_version", "source",
        "story_context",
    ],
    "scene_pack_nested_exact_fields": {
        "identity_slots[]": [
            "adult_attestation_revision", "character_id", "character_name",
            "identity_artifact_manifest_sha256", "identity_pack_revision",
            "license_ref", "persona_id", "provenance_ref",
            "qualification_receipt_sha256", "visual_class", "visual_profile",
        ],
        "identity_slots[].visual_profile": ["appearance", "data_boundary", "digest"],
        "moment": [
            "active_scene_id", "adult_evidence_policy_revision",
            "adult_evidence_set_hash", "cast_revision", "character_profile_hash",
            "content_route", "continuity_digest_hash", "revision",
            "scene_certificate", "source_beat_offset", "source_beat_ordinal",
            "stage_revision",
        ],
        "participants[]": [
            "adult_evidence_digest", "adult_status", "character_id",
            "character_name", "depiction",
        ],
        "point_of_view": ["depiction", "reader_character_id", "reader_role_name"],
        "recipe": ["id", "style_preset", "workflow_contract_revision"],
        "render": ["height", "identity_policy", "medium", "width"],
        "source": [
            "book_id", "book_revision", "chapter_ordinal", "output_locale",
            "source_revision",
        ],
        "story_context": ["chapter_summary", "data_boundary", "recent_cues"],
    },
    "scene_pack_nested_bounded_fields": {
        "presentation": {
            "required": ["book_title", "chapter_title", "scene_heading", "scene_ordinal"],
            "allowed": [
                "book_title", "chapter_title", "scene_family", "scene_heading",
                "scene_ordinal",
            ],
        },
        "story_context.recent_cues[]": {
            "required": ["event_type", "kind", "text", "text_provenance"],
            "allowed": [
                "actor_name", "event_type", "kind", "speaker_name", "text",
                "text_provenance",
            ],
        },
    },
    "render_plan_exact_fields": [
        "adapter", "compiler", "identity_manifest", "max_identity_slots",
        "model_manifest", "output", "profile_id", "renderer_release_digest",
        "schema_version", "screening", "seed", "seed_policy", "template_catalog",
        "variation_index", "workflow", "workflow_contract_revision",
    ],
    "render_plan_identity_manifest_exact_fields": [
        "id", "manifest_sha256", "revision", "slots",
    ],
    "render_plan_identity_slot_exact_fields": [
        "adult_attestation_revision", "artifact_manifest_sha256",
        "identity_pack_revision", "license_ref", "persona_id", "provenance_ref",
        "qualification_receipt_sha256", "visual_class",
    ],
    "server_pinned_exact_subtrees": [
        "render_request.output", "render_request.render_plan.adapter",
        "render_request.render_plan.compiler",
        "render_request.render_plan.identity_manifest",
        "render_request.render_plan.model_manifest",
        "render_request.render_plan.output", "render_request.render_plan.screening",
        "render_request.render_plan.template_catalog",
        "render_request.render_plan.workflow",
    ],
    "output_exact_fields": ["height", "max_bytes", "mime_type", "width"],
    "caller_extensible_objects": [],
    "duplicate_keys": "reject_recursively",
    "unknown_fields": "reject_recursively",
    "canonical_encoding": "utf8-json-sort-keys-no-whitespace",
    "canonical_body_sha256_persisted": True,
    "maximum_body_bytes": 98_304,
}

EXPECTED_ACK_SCHEMA_DEFINITION = {
    "root_exact_fields": ["output_sha256", "output_version", "schema_version"],
    "duplicate_keys": "reject_recursively",
    "unknown_fields": "reject_recursively",
    "output_sha256": "lowercase-hex-sha256",
    "output_version": "integer-1-to-2147483647",
    "maximum_body_bytes": 1_024,
}


def _schema_sha256(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


EXPECTED_SUBMIT_SCHEMA_SHA256 = _schema_sha256(EXPECTED_SUBMIT_SCHEMA_DEFINITION)
EXPECTED_ACK_SCHEMA_SHA256 = _schema_sha256(EXPECTED_ACK_SCHEMA_DEFINITION)

EXPECTED_SCOPES = (
    "scene_art.capability.read",
    "scene_art.job.cancel-own",
    "scene_art.job.read-own",
    "scene_art.job.submit-own",
    "scene_art.output.ack-own",
    "scene_art.output.read-own",
)
EXPECTED_ROUTES = (
    ("GET", f"{ROUTE_PREFIX}capability", "scene_art.capability.read"),
    ("POST", f"{ROUTE_PREFIX}jobs", "scene_art.job.submit-own"),
    ("GET", f"{ROUTE_PREFIX}jobs/{{job_id}}", "scene_art.job.read-own"),
    ("POST", f"{ROUTE_PREFIX}jobs/{{job_id}}/cancel", "scene_art.job.cancel-own"),
    ("GET", f"{ROUTE_PREFIX}jobs/{{job_id}}/output", "scene_art.output.read-own"),
    ("POST", f"{ROUTE_PREFIX}jobs/{{job_id}}/output-ack", "scene_art.output.ack-own"),
)
EXPECTED_RESOURCE_ROUTE_PATHS = tuple(route[1] for route in EXPECTED_ROUTES[2:])
EXPECTED_STATE_VALUES = {
    "SUBMIT_INTENT": "submit_intent",
    "SUBMITTED": "submitted",
    "QUEUED": "queued",
    "RUNNING": "running",
    "SUBMISSION_UNCERTAIN": "submission_uncertain",
    "OUTPUT_ADOPTED": "output_adopted",
    "OUTPUT_ACKNOWLEDGED": "output_acknowledged",
    "CANCELLED": "cancelled",
    "FAILED": "failed",
}
EXPECTED_STATE_TRANSITIONS = (
    ("submit_intent", "submitted"),
    ("submit_intent", "submission_uncertain"),
    ("submit_intent", "failed"),
    ("submission_uncertain", "submitted"),
    ("submission_uncertain", "failed"),
    ("submitted", "queued"),
    ("submitted", "running"),
    ("submitted", "cancelled"),
    ("submitted", "failed"),
    ("queued", "running"),
    ("queued", "cancelled"),
    ("queued", "failed"),
    ("running", "output_adopted"),
    ("running", "cancelled"),
    ("running", "failed"),
    ("output_adopted", "output_acknowledged"),
)
EXPECTED_BLOCKERS = (
    "dedicated_scene_art_service_auth_missing",
    "service_account_credential_lifecycle_missing",
    "private_storage_namespace_key_lifecycle_missing",
    "platform_private_process_isolation_missing",
    "shared_comfy_renderer_inventory_qualification_missing",
    "exact_service_account_route_acl_missing",
    "atomic_owner_resource_lookup_missing",
    "strict_platform_submit_parser_missing",
    "own_job_cancel_route_missing",
    "service_principal_actor_binding_missing",
    "scene_art_provider_variant_missing",
    "scene_art_platform_submission_adapter_missing",
    "scene_art_durable_state_machine_missing",
    "service_account_rate_and_inflight_limits_missing",
    "generated_output_private_materializer_missing",
    "private_request_vault_integration_missing",
    "dedicated_filesystem_or_project_quota_missing",
    "request_publication_reconciliation_missing",
    "output_publication_reconciliation_missing",
    "gallery_history_exclusion_missing",
    "gallery_disk_scan_exclusion_missing",
    "gallery_view_exclusion_missing",
    "gallery_admin_bypass_fence_missing",
    "direct_comfy_tunnel_private_output_fence_missing",
    "raw_comfy_8188_external_ingress_removal_missing",
    "runtime_canonical_view_dirs_validation_missing",
    "view_dir_free_renderer_io_transport_missing",
    "comfy_prompt_history_purge_missing",
    "digest_bound_output_download_missing",
    "oracle_ack_bound_output_deletion_missing",
    "durable_output_ack_tombstone_missing",
    "oracle_claim_attestation_missing",
    "approved_render_release_missing",
    "qualified_output_screener_missing",
    "bounded_platform_canary_missing",
    "operator_activation_approval_missing",
)
EXPECTED_BASELINE_FACTS = (
    "cookie_auth_guards_generate",
    "single_broad_orchestrator_bearer_exists",
    "service_token_character_acl_bypass_exists",
    "job_owner_is_email_based",
    "job_index_is_bounded_json",
    "gallery_recursively_scans_comfy_output",
    "gallery_admin_output_bypass_exists",
    "provider_fabric_owner_outbox_exists",
    "provider_fabric_at_most_once_worker_exists",
    "owner_scoped_private_media_store_exists",
    "local_comfy_account_is_shared_authenticated_users",
    "dedicated_scene_art_service_routes_absent",
    "scene_art_private_output_fences_absent",
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "mode",
        "activation_allowed",
        "platform",
        "service_account",
        "credential_contract",
        "process_isolation_contract",
        "route_acl",
        "resource_acl_contract",
        "submit_request_contract",
        "workflow_acl",
        "job_contract",
        "capacity_contract",
        "durability_contract",
        "output_contract",
        "output_ack_contract",
        "reusable_platform_patterns",
        "baseline_expectations",
        "activation_blockers",
    }
)
_PLATFORM_KEYS = frozenset(
    {
        "origin",
        "local_peer_required",
        "forwarding_headers_forbidden",
        "direct_comfy_api_forbidden",
        "native_comfy_multitenancy_assumed",
    }
)
_SERVICE_ACCOUNT_KEYS = frozenset(
    {
        "principal_id",
        "auth_scheme",
        "plaintext_server_token_forbidden",
        "raw_token_environment_forbidden",
        "shared_orchestrator_token_forbidden",
        "browser_session_forbidden",
        "administrator_role_forbidden",
        "scopes",
    }
)
_CREDENTIAL_KEYS = frozenset(
    {
        "digest_set_schema",
        "digest_record_schema",
        "digest_set_exact_fields",
        "digest_record_exact_fields",
        "server_digest_set_locator_env",
        "server_stores_digest_only",
        "authorization_scheme_prefix",
        "token_character_pattern",
        "token_minimum_ascii_bytes",
        "token_maximum_ascii_bytes",
        "authorization_header_maximum_bytes",
        "algorithm",
        "iterations",
        "salt_bytes",
        "digest_bytes",
        "minimum_records",
        "maximum_records",
        "maximum_rotation_overlap_seconds",
        "lifecycle_timestamp_format",
        "not_before_inclusive",
        "expires_at_exclusive",
        "revoked_record_rejected",
        "replaced_by_must_reference_next_record",
        "constant_time_all_slots_required",
        "client_secret_delivery",
        "client_credential_name",
        "client_runtime_path",
        "client_environment_secret_forbidden",
        "digest_set_file_mode",
        "digest_set_parent_mode",
        "digest_set_maximum_bytes",
        "digest_set_regular_file_required",
        "digest_set_owner_only_required",
        "digest_set_symlink_forbidden",
    }
)
_PROCESS_ISOLATION_KEYS = frozenset(
    {
        "logical_service_account_is_process_isolation",
        "castle_private_boundary_outside_comfy_uid_required",
        "server_digest_owner_must_differ_from_comfy_uid",
        "private_store_owner_must_differ_from_comfy_uid",
        "same_uid_renderer_persistent_private_access_forbidden",
        "same_comfy_instance_retained",
        "separate_comfy_instance_required",
        "shared_comfy_in_process_generation_is_trusted_boundary",
        "cross_custom_node_memory_isolation_claimed",
        "approved_comfy_node_inventory_required",
    }
)
_RESOURCE_ACL_KEYS = frozenset(
    {
        "resource_route_paths",
        "lookup_key_fields",
        "lookup_operation",
        "authenticated_principal_is_owner_operand",
        "caller_owner_parameter_forbidden",
        "existence_probe_before_owner_filter_forbidden",
        "mutations_bind_owner_in_same_atomic_statement",
        "missing_status",
        "non_owner_status",
        "uniform_not_found_error_code",
        "missing_and_non_owner_response_identical",
        "missing_and_non_owner_timing_class_uniform_required",
    }
)
_SUBMIT_KEYS = frozenset(
    {
        "schema_id",
        "schema_sha256",
        "schema_definition",
        "strict_parser_required",
        "schema_digest_field_must_match",
        "render_request_v2_semantic_validation_required",
        "graph_prompt_model_path_smuggling_forbidden",
    }
)
_WORKFLOW_KEYS = frozenset(
    {
        "workflow_id",
        "scene_pack_contract",
        "render_request_contract",
        "render_release_contract",
        "approved_release_digest",
        "approved_workflow_digest",
        "approved_template_catalog_digest",
        "approved_character_allowlist_digest",
        "visual_class",
        "caller_supplied_graph_allowed",
        "caller_supplied_prompt_text_allowed",
        "caller_supplied_model_path_allowed",
        "caller_supplied_output_path_allowed",
        "server_compilation_required",
        "exact_character_registry_required",
        "adult_attestation_required",
        "fictional_animated_adults_only",
    }
)
_JOB_KEYS = frozenset(
    {
        "durable_pattern",
        "provider_id",
        "account_id",
        "owner_principal",
        "owner_principal_type",
        "visibility",
        "idempotency_binding",
        "durable_intent_before_prompt",
        "at_most_once_submission",
        "exact_variant_claim_required",
        "raw_comfy_queue_route_forbidden",
        "max_pending_jobs",
        "max_active_jobs",
        "max_submissions_per_minute",
        "shared_gpu_queue_required",
        "platform_dispatch_required",
        "service_account_direct_comfy_access_forbidden",
        "service_account_direct_gpu_access_forbidden",
        "service_account_direct_filesystem_access_forbidden",
        "own_job_cancel_only",
    }
)
_CAPACITY_KEYS = frozenset(
    {
        "schema_version",
        "owner_principal",
        "accounting_scope",
        "atomic_admission_store",
        "admission_transaction_mode",
        "enforcement_implemented",
        "activation_without_enforcement_forbidden",
        "caller_supplied_limits_forbidden",
        "environment_limit_overrides_forbidden",
        "oracle_per_player_quota_required",
        "capacity_error_status",
        "capacity_error_code",
        "capacity_error_exposes_usage",
        "admission_control",
        "request_vault",
        "output_store",
        "metadata_store",
        "filesystem",
        "reconciliation",
    }
)
_ADMISSION_CAPACITY_KEYS = frozenset(
    {
        "max_pending_jobs",
        "pending_job_states",
        "max_active_jobs",
        "active_job_states",
        "max_new_jobs_per_window",
        "rate_window_seconds",
        "rate_window_kind",
        "rate_event",
        "exact_idempotent_replay_consumes_rate",
    }
)
_REQUEST_CAPACITY_KEYS = frozenset(
    {
        "max_record_bytes",
        "max_charged_objects",
        "max_charged_logical_bytes",
        "max_charged_allocated_bytes",
        "max_pending_publications",
        "max_orphan_temporaries",
        "charged_states",
        "full_record_bytes_charged",
        "reservations_charged",
        "delete_pending_charged_until_finalized",
        "orphan_temporaries_charged_until_reconciled",
        "cross_job_content_dedupe_allowed",
    }
)
_OUTPUT_CAPACITY_KEYS = frozenset(
    {
        "max_png_bytes",
        "max_manifest_bytes",
        "max_charged_objects",
        "max_charged_logical_bytes",
        "max_charged_allocated_bytes",
        "max_materialization_holds",
        "max_unacknowledged_job_references",
        "max_orphan_temporaries",
        "charged_object_states",
        "pending_job_reserves_maximum_object",
        "unique_owner_digest_charged_once",
        "each_materialization_counts_hold",
        "materialization_digest_reserves_object_until_resolved",
        "unacknowledged_job_reference_charged",
        "acknowledged_job_reference_charged",
        "delete_pending_charged_until_finalized",
        "orphan_temporaries_charged_until_reconciled",
        "unacknowledged_age_eviction_forbidden",
    }
)
_METADATA_CAPACITY_KEYS = frozenset(
    {
        "database_path",
        "database_and_sidecars_inside_project_quota_required",
        "page_size_bytes",
        "max_page_count",
        "max_main_database_bytes",
        "max_wal_bytes",
        "max_shm_bytes",
        "wal_autocheckpoint_pages",
        "max_job_rows",
        "max_request_publication_rows",
        "max_output_object_rows",
        "max_output_materialization_rows",
        "max_output_tombstone_rows",
        "output_tombstone_minimum_retention_days",
        "actual_wal_and_shm_bytes_measured_before_admission",
        "terminal_deleted_and_tombstone_rows_charged_until_purge",
        "idempotency_rows_evicted_for_capacity",
        "automatic_capacity_purge_implemented",
        "new_admission_blocked_at_page_row_or_sidecar_limit",
    }
)
_FILESYSTEM_CAPACITY_KEYS = frozenset(
    {
        "private_root",
        "dedicated_filesystem_or_project_quota_required",
        "statvfs_only_capacity_boundary_forbidden",
        "hard_limit_bytes",
        "hard_limit_inodes",
        "backing_filesystem_free_space_reserve_bytes",
        "backing_filesystem_free_inode_reserve_count",
        "backing_filesystem_reserves_outside_project_quota",
        "publication_headroom_bytes",
        "max_concurrent_request_publications",
        "max_concurrent_output_publications",
    }
)
_RECONCILIATION_CAPACITY_KEYS = frozenset(
    {
        "startup_before_admission_required",
        "batch_limit",
        "reservation_survives_restart",
        "unknown_object_policy",
        "quota_drift_policy",
        "new_allocations_blocked_while_over_limit",
        "exact_replays_allowed_at_limit",
        "reads_acks_cancels_deletes_allowed_at_limit",
        "deletion_reconciliation_prioritized_at_high_watermark",
    }
)
_DURABILITY_KEYS = frozenset(
    {
        "state_values",
        "allowed_transitions",
        "submit_intent_persisted_before_prompt",
        "deterministic_prompt_id_persisted_with_submit_intent",
        "provider_submission_uses_persisted_prompt_id",
        "prompt_id_unique_per_owner",
        "ambiguous_submit_enters_submission_uncertain",
        "submission_uncertain_blind_retry_forbidden",
        "submission_uncertain_reconcile_by_prompt_id_required",
        "history_absence_never_authorizes_resubmit",
        "idempotency_unique_key_fields",
        "canonical_request_sha256_persisted",
        "same_key_same_digest_returns_existing",
        "same_key_different_digest_status",
        "same_key_different_digest_error_code",
    }
)
_OUTPUT_KEYS = frozenset(
    {
        "renderer_output_transport",
        "private_store_pattern",
        "private_sink_root",
        "private_sink_outside_view_dirs_required",
        "scene_art_image_bytes_in_comfy_view_dirs_forbidden",
        "comfy_view_dir_staging_forbidden",
        "view_dirs_source",
        "static_test_default_view_dirs",
        "static_test_defaults_are_activation_evidence",
        "runtime_existing_canonical_paths_required",
        "runtime_no_symlink_components_required",
        "runtime_overlap_check_both_directions_required",
        "runtime_filesystem_identity_check_required",
        "runtime_mount_alias_check_required",
        "runtime_validation_before_activation_and_each_open_required",
        "private_directory_mode",
        "private_file_mode",
        "digest_only_filename_required",
        "private_owner_principal",
        "mime_type",
        "max_bytes",
        "gallery_history_exclusion_required",
        "gallery_disk_scan_exclusion_required",
        "gallery_view_exclusion_required",
        "gallery_event_exclusion_required",
        "reconciler_exclusion_required",
        "gallery_admin_bypass_forbidden",
        "direct_comfy_history_view_tunnel_fence_required",
        "raw_comfy_8188_external_ingress_removal_required",
        "submission_forbidden_until_direct_tunnel_fence",
        "history_metadata_is_only_allowed_transient_comfy_artifact",
        "comfy_metadata_history_exposure_race_qualification_required",
        "digest_bound_download_required",
        "delete_only_after_oracle_ack",
        "comfy_prompt_history_purge_after_durable_adoption",
        "caller_selected_path_forbidden",
    }
)
_OUTPUT_ACK_KEYS = frozenset(
    {
        "schema_id",
        "schema_sha256",
        "schema_definition",
        "owner_lookup_contract_required",
        "digest_and_version_must_match_persisted_output",
        "mismatch_status",
        "mismatch_error_code",
        "tombstone_schema",
        "tombstone_exact_fields",
        "tombstone_key_fields",
        "tombstone_states",
        "ack_and_delete_pending_tombstone_atomic",
        "tombstone_commit_before_byte_deletion",
        "pending_deletion_reconciled_after_restart",
        "deletion_completion_persisted",
        "exact_ack_replay_idempotent",
        "tombstone_retention_days",
    }
)


class PlatformServiceAccountContractError(ValueError):
    """The checked-in disabled integration contract is not exact."""


class PlatformServiceAccountDisabled(RuntimeError):
    """Raised by the deliberately inert authorization boundary."""


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise PlatformServiceAccountContractError(f"{label} fields are not exact")
    return value


def _true(value: object, label: str) -> None:
    if value is not True:
        raise PlatformServiceAccountContractError(f"{label} must be true")


def _false(value: object, label: str) -> None:
    if value is not False:
        raise PlatformServiceAccountContractError(f"{label} must be false")


def _exact_integer(value: object, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise PlatformServiceAccountContractError(f"{label} is not exact")


def _exact_text(value: object, expected: str, label: str) -> None:
    if type(value) is not str or value != expected:
        raise PlatformServiceAccountContractError(f"{label} is not exact")


def _exact_strings(value: object, expected: tuple[str, ...], label: str) -> None:
    if (
        type(value) is not list
        or any(type(item) is not str for item in value)
        or tuple(value) != expected
    ):
        raise PlatformServiceAccountContractError(f"{label} is not exact")


def _validate_capacity_contract(
    value: object,
    *,
    max_pending_jobs: int,
    max_active_jobs: int,
    max_submissions_per_minute: int,
) -> None:
    """Validate declared limits, which the platform store enforces at admission."""

    capacity = _object(value, _CAPACITY_KEYS, "capacity_contract")
    expected_text = {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "owner_principal": SERVICE_PRINCIPAL,
        "accounting_scope": "fixed_service_principal",
        "atomic_admission_store": "scene_art_platform_sqlite",
        "admission_transaction_mode": "begin_immediate",
        "capacity_error_code": "scene_art_storage_capacity_exhausted",
    }
    for key, expected in expected_text.items():
        _exact_text(capacity[key], expected, f"capacity_contract.{key}")
    _exact_integer(
        capacity["capacity_error_status"],
        503,
        "capacity_contract.capacity_error_status",
    )
    for key in (
        "activation_without_enforcement_forbidden",
        "caller_supplied_limits_forbidden",
        "environment_limit_overrides_forbidden",
        "oracle_per_player_quota_required",
    ):
        _true(capacity[key], f"capacity_contract.{key}")
    _true(
        capacity["enforcement_implemented"],
        "capacity_contract.enforcement_implemented",
    )
    _false(
        capacity["capacity_error_exposes_usage"],
        "capacity_contract.capacity_error_exposes_usage",
    )

    admission = _object(
        capacity["admission_control"],
        _ADMISSION_CAPACITY_KEYS,
        "capacity_contract.admission_control",
    )
    admission_integers = {
        "max_pending_jobs": max_pending_jobs,
        "max_active_jobs": max_active_jobs,
        "max_new_jobs_per_window": max_submissions_per_minute,
        "rate_window_seconds": 60,
    }
    for key, expected in admission_integers.items():
        _exact_integer(
            admission[key], expected, f"capacity_contract.admission_control.{key}"
        )
    _exact_strings(
        admission["pending_job_states"],
        (
            "submit_intent",
            "submitted",
            "queued",
            "running",
            "submission_uncertain",
        ),
        "capacity_contract.admission_control.pending_job_states",
    )
    _exact_strings(
        admission["active_job_states"],
        ("running", "submission_uncertain"),
        "capacity_contract.admission_control.active_job_states",
    )
    _exact_text(
        admission["rate_window_kind"],
        "sliding",
        "capacity_contract.admission_control.rate_window_kind",
    )
    _exact_text(
        admission["rate_event"],
        "new_submit_intent_committed",
        "capacity_contract.admission_control.rate_event",
    )
    _false(
        admission["exact_idempotent_replay_consumes_rate"],
        "capacity_contract.admission_control.exact_idempotent_replay_consumes_rate",
    )

    request = _object(
        capacity["request_vault"],
        _REQUEST_CAPACITY_KEYS,
        "capacity_contract.request_vault",
    )
    request_integers = {
        "max_record_bytes": REQUEST_MAX_RECORD_BYTES,
        "max_charged_objects": REQUEST_MAX_CHARGED_OBJECTS,
        "max_charged_logical_bytes": REQUEST_MAX_CHARGED_LOGICAL_BYTES,
        "max_charged_allocated_bytes": REQUEST_MAX_CHARGED_ALLOCATED_BYTES,
        "max_pending_publications": max_pending_jobs,
        "max_orphan_temporaries": 32,
    }
    for key, expected in request_integers.items():
        _exact_integer(
            request[key], expected, f"capacity_contract.request_vault.{key}"
        )
    _exact_strings(
        request["charged_states"],
        ("publish_intent", "live", "delete_pending"),
        "capacity_contract.request_vault.charged_states",
    )
    for key in (
        "full_record_bytes_charged",
        "reservations_charged",
        "delete_pending_charged_until_finalized",
        "orphan_temporaries_charged_until_reconciled",
    ):
        _true(request[key], f"capacity_contract.request_vault.{key}")
    _false(
        request["cross_job_content_dedupe_allowed"],
        "capacity_contract.request_vault.cross_job_content_dedupe_allowed",
    )

    output = _object(
        capacity["output_store"],
        _OUTPUT_CAPACITY_KEYS,
        "capacity_contract.output_store",
    )
    output_integers = {
        "max_png_bytes": OUTPUT_MAX_PNG_BYTES,
        "max_manifest_bytes": OUTPUT_MAX_MANIFEST_BYTES,
        "max_charged_objects": OUTPUT_MAX_CHARGED_OBJECTS,
        "max_charged_logical_bytes": OUTPUT_MAX_CHARGED_LOGICAL_BYTES,
        "max_charged_allocated_bytes": OUTPUT_MAX_CHARGED_ALLOCATED_BYTES,
        "max_materialization_holds": max_pending_jobs,
        "max_unacknowledged_job_references": OUTPUT_MAX_CHARGED_OBJECTS,
        "max_orphan_temporaries": 1,
    }
    for key, expected in output_integers.items():
        _exact_integer(
            output[key], expected, f"capacity_contract.output_store.{key}"
        )
    _exact_strings(
        output["charged_object_states"],
        ("live", "delete_pending"),
        "capacity_contract.output_store.charged_object_states",
    )
    for key in (
        "pending_job_reserves_maximum_object",
        "unique_owner_digest_charged_once",
        "each_materialization_counts_hold",
        "materialization_digest_reserves_object_until_resolved",
        "unacknowledged_job_reference_charged",
        "delete_pending_charged_until_finalized",
        "orphan_temporaries_charged_until_reconciled",
        "unacknowledged_age_eviction_forbidden",
    ):
        _true(output[key], f"capacity_contract.output_store.{key}")
    _false(
        output["acknowledged_job_reference_charged"],
        "capacity_contract.output_store.acknowledged_job_reference_charged",
    )

    metadata = _object(
        capacity["metadata_store"],
        _METADATA_CAPACITY_KEYS,
        "capacity_contract.metadata_store",
    )
    _exact_text(
        metadata["database_path"],
        PRIVATE_DATABASE_PATH,
        "capacity_contract.metadata_store.database_path",
    )
    metadata_integers = {
        "page_size_bytes": METADATA_PAGE_SIZE_BYTES,
        "max_page_count": METADATA_MAX_PAGE_COUNT,
        "max_main_database_bytes": METADATA_MAX_MAIN_DATABASE_BYTES,
        "max_wal_bytes": METADATA_MAX_WAL_BYTES,
        "max_shm_bytes": METADATA_MAX_SHM_BYTES,
        "wal_autocheckpoint_pages": 4_096,
        "max_job_rows": 65_536,
        "max_request_publication_rows": 65_536,
        "max_output_object_rows": 65_536,
        "max_output_materialization_rows": 65_536,
        "max_output_tombstone_rows": 65_536,
        "output_tombstone_minimum_retention_days": 90,
    }
    for key, expected in metadata_integers.items():
        _exact_integer(
            metadata[key], expected, f"capacity_contract.metadata_store.{key}"
        )
    for key in (
        "database_and_sidecars_inside_project_quota_required",
        "actual_wal_and_shm_bytes_measured_before_admission",
        "terminal_deleted_and_tombstone_rows_charged_until_purge",
        "new_admission_blocked_at_page_row_or_sidecar_limit",
    ):
        _true(metadata[key], f"capacity_contract.metadata_store.{key}")
    for key in (
        "idempotency_rows_evicted_for_capacity",
        "automatic_capacity_purge_implemented",
    ):
        _false(metadata[key], f"capacity_contract.metadata_store.{key}")

    filesystem = _object(
        capacity["filesystem"],
        _FILESYSTEM_CAPACITY_KEYS,
        "capacity_contract.filesystem",
    )
    _exact_text(
        filesystem["private_root"],
        PRIVATE_STORAGE_ROOT,
        "capacity_contract.filesystem.private_root",
    )
    filesystem_integers = {
        "hard_limit_bytes": PRIVATE_FILESYSTEM_HARD_LIMIT_BYTES,
        "hard_limit_inodes": PRIVATE_FILESYSTEM_HARD_LIMIT_INODES,
        "backing_filesystem_free_space_reserve_bytes": 2 * 1024 * 1024 * 1024,
        "backing_filesystem_free_inode_reserve_count": 10_000,
        "publication_headroom_bytes": 16 * 1024 * 1024,
        "max_concurrent_request_publications": 1,
        "max_concurrent_output_publications": 1,
    }
    for key, expected in filesystem_integers.items():
        _exact_integer(
            filesystem[key], expected, f"capacity_contract.filesystem.{key}"
        )
    for key in (
        "dedicated_filesystem_or_project_quota_required",
        "statvfs_only_capacity_boundary_forbidden",
        "backing_filesystem_reserves_outside_project_quota",
    ):
        _true(filesystem[key], f"capacity_contract.filesystem.{key}")

    reconciliation = _object(
        capacity["reconciliation"],
        _RECONCILIATION_CAPACITY_KEYS,
        "capacity_contract.reconciliation",
    )
    _exact_integer(
        reconciliation["batch_limit"],
        128,
        "capacity_contract.reconciliation.batch_limit",
    )
    _exact_text(
        reconciliation["unknown_object_policy"],
        "retain_charge_and_fail_closed",
        "capacity_contract.reconciliation.unknown_object_policy",
    )
    _exact_text(
        reconciliation["quota_drift_policy"],
        "drain_only_fail_closed",
        "capacity_contract.reconciliation.quota_drift_policy",
    )
    for key in _RECONCILIATION_CAPACITY_KEYS - {
        "batch_limit",
        "unknown_object_policy",
        "quota_drift_policy",
    }:
        _true(reconciliation[key], f"capacity_contract.reconciliation.{key}")

    if (
        REQUEST_MAX_CHARGED_LOGICAL_BYTES
        != REQUEST_MAX_RECORD_BYTES * REQUEST_MAX_CHARGED_OBJECTS
        or OUTPUT_MAX_CHARGED_LOGICAL_BYTES
        != (OUTPUT_MAX_PNG_BYTES + OUTPUT_MAX_MANIFEST_BYTES)
        * OUTPUT_MAX_CHARGED_OBJECTS
        or REQUEST_MAX_CHARGED_ALLOCATED_BYTES
        < REQUEST_MAX_CHARGED_LOGICAL_BYTES
        or OUTPUT_MAX_CHARGED_ALLOCATED_BYTES
        < OUTPUT_MAX_CHARGED_LOGICAL_BYTES
        or PRIVATE_FILESYSTEM_HARD_LIMIT_BYTES
        <= REQUEST_MAX_CHARGED_ALLOCATED_BYTES
        + OUTPUT_MAX_CHARGED_ALLOCATED_BYTES
        + METADATA_MAX_MAIN_DATABASE_BYTES
        + METADATA_MAX_WAL_BYTES
        + METADATA_MAX_SHM_BYTES
        + filesystem["publication_headroom_bytes"]
        or filesystem["publication_headroom_bytes"]
        < request["max_orphan_temporaries"] * REQUEST_MAX_RECORD_BYTES
        + output["max_orphan_temporaries"]
        * (OUTPUT_MAX_PNG_BYTES + OUTPUT_MAX_MANIFEST_BYTES)
        or METADATA_MAX_MAIN_DATABASE_BYTES
        != METADATA_PAGE_SIZE_BYTES * METADATA_MAX_PAGE_COUNT
        or metadata["wal_autocheckpoint_pages"] * METADATA_PAGE_SIZE_BYTES
        > METADATA_MAX_WAL_BYTES
    ):
        raise PlatformServiceAccountContractError(
            "capacity contract limits are internally inconsistent"
        )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PlatformServiceAccountContractError(
                f"duplicate JSON field is forbidden: {key}"
            )
        value[key] = item
    return value


def _canonical_existing_directory(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise PlatformServiceAccountContractError(f"{label} must be absolute")
    current = Path(candidate.anchor)
    try:
        for component in candidate.parts[1:]:
            current = current / component
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise PlatformServiceAccountContractError(
                    f"{label} contains a symbolic link"
                )
        canonical = candidate.resolve(strict=True)
    except OSError as error:
        raise PlatformServiceAccountContractError(
            f"{label} must be an existing canonical directory"
        ) from error
    if canonical != candidate or not canonical.is_dir():
        raise PlatformServiceAccountContractError(
            f"{label} must be an existing canonical directory"
        )
    return canonical


def validate_runtime_view_dir_separation(
    private_sink: Path,
    configured_view_dirs: Mapping[str, Path] | Sequence[Path],
) -> tuple[Path, tuple[Path, ...]]:
    """Validate supplied runtime paths; static manifest defaults never call this."""

    raw_roots = (
        tuple(configured_view_dirs.values())
        if isinstance(configured_view_dirs, Mapping)
        else tuple(configured_view_dirs)
    )
    if not raw_roots:
        raise PlatformServiceAccountContractError("configured VIEW_DIRS are required")
    sink = _canonical_existing_directory(Path(private_sink), "private sink")
    roots = tuple(
        _canonical_existing_directory(Path(root), f"VIEW_DIRS[{index}]")
        for index, root in enumerate(raw_roots)
    )
    if len(roots) != len(set(roots)):
        raise PlatformServiceAccountContractError("configured VIEW_DIRS are duplicated")
    for root in roots:
        if (
            sink.is_relative_to(root)
            or root.is_relative_to(sink)
            or os.path.samefile(sink, root)
        ):
            raise PlatformServiceAccountContractError(
                "private sink overlaps a configured VIEW_DIRS root"
            )
    return sink, roots


@dataclass(frozen=True, slots=True)
class PlatformServiceAccountPlan:
    """A safe public projection of the disabled plan, never a credential."""

    principal_id: str
    route_acl: tuple[tuple[str, str, str], ...]
    workflow_id: str
    private_sink_root: str
    activation_blockers: tuple[str, ...]

    @property
    def active(self) -> bool:
        return False

    def required_scope(self, method: str, path_template: str) -> str | None:
        candidate = (str(method).upper(), str(path_template))
        for route_method, route_path, scope in self.route_acl:
            if candidate == (route_method, route_path):
                return scope
        return None

    def authorize(self, *_args: object, **_kwargs: object) -> None:
        """Fail closed even if a caller tries to wire this parser prematurely."""

        raise PlatformServiceAccountDisabled(
            "Novel Scene Art platform service-account routes are disabled"
        )

    def public_report(
        self,
        baseline: Mapping[str, bool] | None = None,
        *,
        capacity_enforcement_verified: bool = False,
    ) -> dict[str, object]:
        if type(capacity_enforcement_verified) is not bool:
            raise PlatformServiceAccountContractError(
                "capacity_enforcement_verified must be a strict boolean"
            )
        observed = dict(baseline or {})
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "off",
            "active": False,
            "principal_id": self.principal_id,
            "workflow_id": self.workflow_id,
            "route_count": len(self.route_acl),
            "submit_schema_sha256": EXPECTED_SUBMIT_SCHEMA_SHA256,
            "output_ack_schema_sha256": EXPECTED_ACK_SCHEMA_SHA256,
            "capacity_contract_declared": True,
            "capacity_enforcement_verified": capacity_enforcement_verified,
            "runtime_view_dirs_verified": False,
            "process_isolation_verified": False,
            "baseline_matches": bool(observed)
            and all(observed.get(key) is True for key in EXPECTED_BASELINE_FACTS),
            "baseline": observed,
            "activation_blockers": list(self.activation_blockers),
        }


def validate_plan(value: object) -> PlatformServiceAccountPlan:
    plan = _object(value, _TOP_LEVEL_KEYS, "plan")
    if plan["schema_version"] != SCHEMA_VERSION or plan["mode"] != "off":
        raise PlatformServiceAccountContractError("plan schema or mode is invalid")
    _false(plan["activation_allowed"], "activation_allowed")

    platform = _object(plan["platform"], _PLATFORM_KEYS, "platform")
    if platform["origin"] != PLATFORM_ORIGIN:
        raise PlatformServiceAccountContractError("platform origin is not exact loopback")
    for key in ("local_peer_required", "direct_comfy_api_forbidden"):
        _true(platform[key], f"platform.{key}")
    _false(
        platform["native_comfy_multitenancy_assumed"],
        "platform.native_comfy_multitenancy_assumed",
    )
    forwarding = platform["forwarding_headers_forbidden"]
    if (
        not isinstance(forwarding, list)
        or len(forwarding) != len(set(forwarding))
        or tuple(forwarding) != tuple(sorted(forwarding))
        or not {"CF-Ray", "Forwarded", "X-Forwarded-For"}.issubset(forwarding)
    ):
        raise PlatformServiceAccountContractError("forwarding-header fence is incomplete")

    account = _object(plan["service_account"], _SERVICE_ACCOUNT_KEYS, "service_account")
    if (
        account["principal_id"] != SERVICE_PRINCIPAL
        or account["auth_scheme"] != "bearer"
    ):
        raise PlatformServiceAccountContractError("service-account identity is invalid")
    for key in (
        "plaintext_server_token_forbidden",
        "raw_token_environment_forbidden",
        "shared_orchestrator_token_forbidden",
        "browser_session_forbidden",
        "administrator_role_forbidden",
    ):
        _true(account[key], f"service_account.{key}")
    _exact_strings(account["scopes"], EXPECTED_SCOPES, "service_account.scopes")

    credential = _object(
        plan["credential_contract"], _CREDENTIAL_KEYS, "credential_contract"
    )
    expected_credential_values = {
        "digest_set_schema": "castle.novel-scene-art-service-account-digest-set.v1",
        "digest_record_schema": "castle.novel-scene-art-service-account-digest.v1",
        "server_digest_set_locator_env":
            "CASTLE_NOVEL_SCENE_ART_SERVICE_ACCOUNT_DIGEST_PATH",
        "authorization_scheme_prefix": "Bearer ",
        "token_character_pattern": "^[A-Za-z0-9._~-]+$",
        "token_minimum_ascii_bytes": 43,
        "token_maximum_ascii_bytes": 256,
        "authorization_header_maximum_bytes": 263,
        "algorithm": "pbkdf2_sha256",
        "iterations": 600_000,
        "salt_bytes": 32,
        "digest_bytes": 32,
        "minimum_records": 1,
        "maximum_records": 2,
        "maximum_rotation_overlap_seconds": 300,
        "lifecycle_timestamp_format": "unix_seconds_integer",
        "client_secret_delivery": "systemd_LoadCredentialEncrypted",
        "client_credential_name": "novel-scene-art.token",
        "client_runtime_path": "%d/novel-scene-art.token",
        "digest_set_file_mode": "0600",
        "digest_set_parent_mode": "0700",
        "digest_set_maximum_bytes": 16_384,
    }
    if any(
        credential[key] != expected
        for key, expected in expected_credential_values.items()
    ):
        raise PlatformServiceAccountContractError("credential contract is invalid")
    _exact_strings(
        credential["digest_set_exact_fields"],
        ("records", "schema_version"),
        "credential_contract.digest_set_exact_fields",
    )
    _exact_strings(
        credential["digest_record_exact_fields"],
        (
            "algorithm", "created_at", "digest_b64", "expires_at", "iterations",
            "key_id", "not_before", "principal_id", "replaced_by_key_id",
            "revoked_at", "salt_b64", "schema_version", "scopes",
        ),
        "credential_contract.digest_record_exact_fields",
    )
    for key in _CREDENTIAL_KEYS - frozenset(expected_credential_values) - {
        "digest_set_exact_fields",
        "digest_record_exact_fields",
    }:
        _true(credential[key], f"credential_contract.{key}")

    isolation = _object(
        plan["process_isolation_contract"],
        _PROCESS_ISOLATION_KEYS,
        "process_isolation_contract",
    )
    for key in (
        "castle_private_boundary_outside_comfy_uid_required",
        "server_digest_owner_must_differ_from_comfy_uid",
        "private_store_owner_must_differ_from_comfy_uid",
        "same_uid_renderer_persistent_private_access_forbidden",
        "same_comfy_instance_retained",
        "shared_comfy_in_process_generation_is_trusted_boundary",
        "approved_comfy_node_inventory_required",
    ):
        _true(isolation[key], f"process_isolation_contract.{key}")
    for key in (
        "logical_service_account_is_process_isolation",
        "separate_comfy_instance_required",
        "cross_custom_node_memory_isolation_claimed",
    ):
        _false(isolation[key], f"process_isolation_contract.{key}")

    raw_routes = plan["route_acl"]
    if not isinstance(raw_routes, list):
        raise PlatformServiceAccountContractError("route_acl must be a list")
    routes: list[tuple[str, str, str]] = []
    for index, raw in enumerate(raw_routes):
        route = _object(raw, frozenset({"method", "path", "scope"}), f"route_acl[{index}]")
        item = (str(route["method"]), str(route["path"]), str(route["scope"]))
        routes.append(item)
    if tuple(routes) != EXPECTED_ROUTES:
        raise PlatformServiceAccountContractError("route ACL is not exact")
    if {item[2] for item in routes} != set(EXPECTED_SCOPES):
        raise PlatformServiceAccountContractError("route scopes are incomplete")

    resource = _object(
        plan["resource_acl_contract"], _RESOURCE_ACL_KEYS, "resource_acl_contract"
    )
    _exact_strings(
        resource["resource_route_paths"],
        EXPECTED_RESOURCE_ROUTE_PATHS,
        "resource_acl_contract.resource_route_paths",
    )
    _exact_strings(
        resource["lookup_key_fields"],
        ("owner_principal", "job_id"),
        "resource_acl_contract.lookup_key_fields",
    )
    if (
        resource["lookup_operation"] != "single_atomic_storage_query"
        or resource["missing_status"] != 404
        or resource["non_owner_status"] != 404
        or resource["uniform_not_found_error_code"]
        != "scene_art_resource_not_found"
    ):
        raise PlatformServiceAccountContractError("owner resource lookup is invalid")
    for key in _RESOURCE_ACL_KEYS - {
        "resource_route_paths", "lookup_key_fields", "lookup_operation",
        "missing_status", "non_owner_status", "uniform_not_found_error_code",
    }:
        _true(resource[key], f"resource_acl_contract.{key}")

    submit = _object(
        plan["submit_request_contract"], _SUBMIT_KEYS, "submit_request_contract"
    )
    if (
        submit["schema_id"] != SUBMIT_SCHEMA_ID
        or submit["schema_definition"] != EXPECTED_SUBMIT_SCHEMA_DEFINITION
        or submit["schema_sha256"] != EXPECTED_SUBMIT_SCHEMA_SHA256
        or _schema_sha256(submit["schema_definition"]) != submit["schema_sha256"]
    ):
        raise PlatformServiceAccountContractError("submit schema is not exact")
    for key in (
        "strict_parser_required",
        "schema_digest_field_must_match",
        "render_request_v2_semantic_validation_required",
        "graph_prompt_model_path_smuggling_forbidden",
    ):
        _true(submit[key], f"submit_request_contract.{key}")

    workflow = _object(plan["workflow_acl"], _WORKFLOW_KEYS, "workflow_acl")
    expected_workflow_values = {
        "workflow_id": "novel-scene-art-v2",
        "scene_pack_contract": "novel-scene-pack-v2",
        "render_request_contract": "novel-scene-art-render-request-v2",
        "render_release_contract": "novel-scene-art-render-release-v1",
    }
    if any(workflow[key] != expected for key, expected in expected_workflow_values.items()):
        raise PlatformServiceAccountContractError("workflow contracts are not exact")
    if any(
        workflow[key] is not None
        for key in (
            "approved_release_digest",
            "approved_workflow_digest",
            "approved_template_catalog_digest",
            "approved_character_allowlist_digest",
        )
    ):
        raise PlatformServiceAccountContractError(
            "disabled plan cannot name approved release material"
        )
    if workflow["visual_class"] != "anime":
        raise PlatformServiceAccountContractError("workflow visual class must be anime")
    for key in (
        "caller_supplied_graph_allowed",
        "caller_supplied_prompt_text_allowed",
        "caller_supplied_model_path_allowed",
        "caller_supplied_output_path_allowed",
    ):
        _false(workflow[key], f"workflow_acl.{key}")
    for key in (
        "server_compilation_required",
        "exact_character_registry_required",
        "adult_attestation_required",
        "fictional_animated_adults_only",
    ):
        _true(workflow[key], f"workflow_acl.{key}")

    job = _object(plan["job_contract"], _JOB_KEYS, "job_contract")
    expected_job_values = {
        "durable_pattern": "provider_fabric_owned_outbox_extension",
        "provider_id": "local_comfy",
        "account_id": "local-main",
        "owner_principal": SERVICE_PRINCIPAL,
        "owner_principal_type": "service",
        "visibility": "private-service",
        "idempotency_binding": "generation_key",
        "max_pending_jobs": 8,
        "max_active_jobs": 1,
        "max_submissions_per_minute": 4,
    }
    if any(job[key] != expected for key, expected in expected_job_values.items()):
        raise PlatformServiceAccountContractError("job contract is invalid")
    for key in (
        "durable_intent_before_prompt",
        "at_most_once_submission",
        "exact_variant_claim_required",
        "raw_comfy_queue_route_forbidden",
        "shared_gpu_queue_required",
        "platform_dispatch_required",
        "service_account_direct_comfy_access_forbidden",
        "service_account_direct_gpu_access_forbidden",
        "service_account_direct_filesystem_access_forbidden",
        "own_job_cancel_only",
    ):
        _true(job[key], f"job_contract.{key}")

    _validate_capacity_contract(
        plan["capacity_contract"],
        max_pending_jobs=job["max_pending_jobs"],
        max_active_jobs=job["max_active_jobs"],
        max_submissions_per_minute=job["max_submissions_per_minute"],
    )

    durability = _object(
        plan["durability_contract"], _DURABILITY_KEYS, "durability_contract"
    )
    if durability["state_values"] != EXPECTED_STATE_VALUES:
        raise PlatformServiceAccountContractError("durable states are not exact")
    transitions = durability["allowed_transitions"]
    if (
        not isinstance(transitions, list)
        or tuple(tuple(item) for item in transitions) != EXPECTED_STATE_TRANSITIONS
    ):
        raise PlatformServiceAccountContractError("durable transitions are not exact")
    _exact_strings(
        durability["idempotency_unique_key_fields"],
        ("owner_principal", "generation_key"),
        "durability_contract.idempotency_unique_key_fields",
    )
    if (
        durability["same_key_different_digest_status"] != 409
        or durability["same_key_different_digest_error_code"]
        != "scene_art_idempotency_conflict"
    ):
        raise PlatformServiceAccountContractError("idempotency conflict is invalid")
    for key in _DURABILITY_KEYS - {
        "state_values", "allowed_transitions", "idempotency_unique_key_fields",
        "same_key_different_digest_status", "same_key_different_digest_error_code",
    }:
        _true(durability[key], f"durability_contract.{key}")

    output = _object(plan["output_contract"], _OUTPUT_KEYS, "output_contract")
    if (
        output["renderer_output_transport"]
        != "direct_private_sink_without_comfy_view_dir_bytes"
        or output["private_owner_principal"] != SERVICE_PRINCIPAL
        or output["private_store_pattern"]
        != "owner_scoped_content_addressed_generated_output_extension"
        or output["private_sink_root"] != PRIVATE_SINK_ROOT
        or output["view_dirs_source"] != "runtime_webui_server_VIEW_DIRS"
        or output["private_directory_mode"] != "0700"
        or output["private_file_mode"] != "0600"
        or output["mime_type"] != "image/png"
        or output["max_bytes"] != 12 * 1024 * 1024
    ):
        raise PlatformServiceAccountContractError("output contract is invalid")
    private_sink = Path(str(output["private_sink_root"]))
    static_defaults = output["static_test_default_view_dirs"]
    _exact_strings(
        static_defaults,
        (
            str(Path.home() / "comfyui/input"),
            str(Path.home() / "comfyui/output"),
            str(Path.home() / "comfyui/temp"),
        ),
        "output_contract.static_test_default_view_dirs",
    )
    view_roots = tuple(
        Path(value) for value in static_defaults
    )
    if any(
        private_sink.is_relative_to(root) or root.is_relative_to(private_sink)
        for root in view_roots
    ):
        raise PlatformServiceAccountContractError("private sink overlaps a Comfy view root")
    _false(output["static_test_defaults_are_activation_evidence"],
           "output_contract.static_test_defaults_are_activation_evidence")
    for key in _OUTPUT_KEYS - {
        "renderer_output_transport",
        "private_store_pattern",
        "private_sink_root",
        "view_dirs_source",
        "static_test_default_view_dirs",
        "static_test_defaults_are_activation_evidence",
        "private_directory_mode",
        "private_file_mode",
        "private_owner_principal",
        "mime_type",
        "max_bytes",
    }:
        _true(output[key], f"output_contract.{key}")

    ack = _object(
        plan["output_ack_contract"], _OUTPUT_ACK_KEYS, "output_ack_contract"
    )
    if (
        ack["schema_id"] != ACK_SCHEMA_ID
        or ack["schema_definition"] != EXPECTED_ACK_SCHEMA_DEFINITION
        or ack["schema_sha256"] != EXPECTED_ACK_SCHEMA_SHA256
        or _schema_sha256(ack["schema_definition"]) != ack["schema_sha256"]
        or ack["mismatch_status"] != 409
        or ack["mismatch_error_code"] != "scene_art_output_ack_conflict"
        or ack["tombstone_schema"] != "castle.novel-scene-art-output-tombstone.v1"
        or ack["tombstone_retention_days"] != 90
    ):
        raise PlatformServiceAccountContractError("output ACK contract is invalid")
    _exact_strings(
        ack["tombstone_exact_fields"],
        (
            "acknowledged_at", "deleted_at", "job_id", "output_sha256",
            "output_version", "owner_principal", "schema_version", "state",
        ),
        "output_ack_contract.tombstone_exact_fields",
    )
    _exact_strings(
        ack["tombstone_key_fields"],
        ("owner_principal", "job_id", "output_sha256", "output_version"),
        "output_ack_contract.tombstone_key_fields",
    )
    _exact_strings(
        ack["tombstone_states"],
        ("delete_pending", "deleted"),
        "output_ack_contract.tombstone_states",
    )
    for key in _OUTPUT_ACK_KEYS - {
        "schema_id", "schema_sha256", "schema_definition", "mismatch_status",
        "mismatch_error_code", "tombstone_schema", "tombstone_exact_fields",
        "tombstone_key_fields", "tombstone_states", "tombstone_retention_days",
    }:
        _true(ack[key], f"output_ack_contract.{key}")

    reusable = plan["reusable_platform_patterns"]
    if (
        not isinstance(reusable, list)
        or not reusable
        or len(reusable) != len(set(reusable))
        or any(not isinstance(item, str) or not item for item in reusable)
    ):
        raise PlatformServiceAccountContractError("reusable platform patterns are invalid")

    baseline = _object(
        plan["baseline_expectations"],
        frozenset(EXPECTED_BASELINE_FACTS),
        "baseline_expectations",
    )
    for key in EXPECTED_BASELINE_FACTS:
        _true(baseline[key], f"baseline_expectations.{key}")

    _exact_strings(plan["activation_blockers"], EXPECTED_BLOCKERS, "activation_blockers")
    return PlatformServiceAccountPlan(
        principal_id=SERVICE_PRINCIPAL,
        route_acl=tuple(routes),
        workflow_id=str(workflow["workflow_id"]),
        private_sink_root=str(output["private_sink_root"]),
        activation_blockers=EXPECTED_BLOCKERS,
    )


def _load_plan_value(path: Path | None = None) -> object:
    selected = path or Path(__file__).resolve().parents[2] / MANIFEST_RELATIVE_PATH
    try:
        raw = selected.read_bytes()
    except OSError as error:
        raise PlatformServiceAccountContractError("platform plan is unavailable") from error
    if len(raw) > 64 * 1024:
        raise PlatformServiceAccountContractError("platform plan is too large")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PlatformServiceAccountContractError("platform plan is not valid UTF-8 JSON") from error


def load_plan(path: Path | None = None) -> PlatformServiceAccountPlan:
    return validate_plan(_load_plan_value(path))


def audit_current_platform(repository: Path | None = None) -> dict[str, bool]:
    """Read source only and record the reviewed baseline, without importing it."""

    root = (repository or Path(__file__).resolve().parents[2]).resolve()
    try:
        server = (root / "webui/server.py").read_text(encoding="utf-8")
        store = (root / "webui/provider_fabric/store.py").read_text(encoding="utf-8")
        worker = (root / "webui/provider_fabric/provider_worker.py").read_text(
            encoding="utf-8"
        )
        media = (root / "webui/provider_fabric/media_materializer.py").read_text(
            encoding="utf-8"
        )
    except OSError as error:
        raise PlatformServiceAccountContractError("platform source is unavailable") from error

    generate_route = re.search(
        r'elif path == "/api/generate":\s+if not self\.require_auth\(\):', server
    )
    return {
        "cookie_auth_guards_generate": generate_route is not None,
        "single_broad_orchestrator_bearer_exists": all(
            marker in server
            for marker in (
                "CASTLE_ORCH_SERVICE_TOKEN_PATH",
                "def is_orchestrator_service_authenticated(self)",
                "hmac.compare_digest(provided, configured)",
            )
        ),
        "service_token_character_acl_bypass_exists": server.count(
            "if service_token:\n        return True"
        ) >= 3,
        "job_owner_is_email_based": all(
            marker in server
            for marker in (
                "def job_owner_email(",
                'indexed.get("email")',
                'submitted_by.get("email")',
            )
        ),
        "job_index_is_bounded_json": all(
            marker in server
            for marker in (
                'JOB_INDEX_PATH = CHARACTER_LIBRARY / ".job_index.json"',
                ")[:2000]",
                'atomic_write_json(JOB_INDEX_PATH, {"version": 1',
            )
        ),
        "gallery_recursively_scans_comfy_output": 'COMFY_OUTPUT.rglob("*")' in server,
        "gallery_admin_output_bypass_exists": (
            'if not (user and user.get("role") == "admin"):' in server
            and "def api_view(self, query: str)" in server
        ),
        "provider_fabric_owner_outbox_exists": all(
            marker in store
            for marker in (
                "CREATE TABLE IF NOT EXISTS submission_outbox",
                "def list_pending_owned_submissions(",
                "def claim_owned_submission(",
                "def transition_owned_task(",
            )
        ),
        "provider_fabric_at_most_once_worker_exists": all(
            marker in worker
            for marker in (
                "At-most-once worker boundary",
                "def run_once(",
                "SUBMISSION_UNCERTAIN",
            )
        ),
        "owner_scoped_private_media_store_exists": all(
            marker in media
            for marker in (
                "class PrivateProviderMediaStore:",
                "Owner-separated, immutable content-addressed",
                "_DIRECTORY_MODE = 0o700",
                "_FILE_MODE = 0o600",
            )
        ),
        "local_comfy_account_is_shared_authenticated_users": all(
            marker in store
            for marker in (
                'SYSTEM_LOCAL_OWNER_ID = "castle-system:local-comfy"',
                "def register_system_local_provider_account(",
                "'authenticated_users'",
            )
        ),
        "dedicated_scene_art_service_routes_absent": ROUTE_PREFIX not in server,
        "scene_art_private_output_fences_absent": (
            PRIVATE_SINK_ROOT not in server and "NovelSceneArtPrivate" not in server
        ),
    }


def _verify_capacity_enforcement(capacity_contract: Mapping[str, object]) -> None:
    """Prove the platform store enforces admission capacity behaviorally.

    The probe builds a throwaway store under a fresh temporary directory with
    deliberately tiny bounds, drives it to configured admission bounds, and
    requires the exact fail-closed surface the capacity contract declares:
    admission blocks with the digit-free 503 capacity error, exact idempotent
    replays stay admitted, the sliding window releases rate capacity, and a
    drain both succeeds at the bound and frees capacity for new work. It
    never reads or writes the production paths named in the manifest.
    """

    import tempfile

    from . import platform_store
    from .platform_service_auth import ServicePrincipal

    try:
        platform_store.StoreCapacity.from_capacity_contract(capacity_contract)
    except platform_store.PlatformStoreError as error:
        raise PlatformServiceAccountContractError(
            "capacity contract does not parse into an enforceable store capacity"
        ) from error

    overhead = platform_store.REQUEST_RECORD_OVERHEAD_BYTES
    probe_capacity = platform_store.StoreCapacity(
        max_pending_jobs=2,
        max_active_jobs=1,
        max_new_jobs_per_window=1,
        rate_window_seconds=60,
        request_max_record_bytes=overhead + 64,
        request_max_charged_objects=8,
        request_max_charged_logical_bytes=8 * (overhead + 64),
        request_max_charged_allocated_bytes=16 * 4096,
        request_max_pending_publications=8,
        output_max_png_bytes=4096,
        output_max_manifest_bytes=1024,
        output_max_charged_objects=8,
        output_max_charged_logical_bytes=8 * (4096 + 1024),
        output_max_charged_allocated_bytes=16 * 8192,
        output_max_materialization_holds=2,
        output_max_unacknowledged_job_references=8,
        metadata_page_size_bytes=4096,
        metadata_max_page_count=65536,
        metadata_max_wal_bytes=67108864,
        metadata_max_shm_bytes=8388608,
        metadata_wal_autocheckpoint_pages=4096,
        metadata_max_job_rows=65536,
        metadata_max_request_publication_rows=65536,
        metadata_max_output_object_rows=65536,
        metadata_max_output_materialization_rows=65536,
        metadata_max_output_tombstone_rows=65536,
    )

    shared_vault_id = hashlib.sha256(
        b"capacity-probe:request-vault"
    ).hexdigest()

    def submission_fields(label: str) -> dict[str, object]:
        def digest(kind: str) -> str:
            return hashlib.sha256(
                f"capacity-probe:{kind}:{label}".encode("utf-8")
            ).hexdigest()

        return {
            "job_id": f"capacity-probe-job-{label}",
            "publication_id": "reqpub_" + digest("publication")[:32],
            "request_id": f"capacity-probe-request-{label}",
            "generation_key": digest("generation"),
            "canonical_request_sha256": digest("request"),
            "canonical_request_size": 64,
            "request_vault_id": shared_vault_id,
            "scene_pack_sha256": digest("scene-pack"),
            "render_plan_sha256": digest("render-plan"),
            "renderer_release_sha256": digest("release"),
        }

    def require(condition: bool, reason: str) -> None:
        if condition is not True:
            raise PlatformServiceAccountContractError(reason)

    with tempfile.TemporaryDirectory(
        prefix="scene-art-capacity-probe-"
    ) as root:
        moments = [1_700_000_000]
        store = platform_store.PlatformSceneArtStore(
            Path(root) / "capacity-probe.sqlite3",
            capacity=probe_capacity,
            clock=lambda: moments[0],
        )
        principal = ServicePrincipal(
            principal_id=SERVICE_PRINCIPAL,
            scopes=tuple(EXPECTED_SCOPES),
            key_id="capacity-probe-key",
        )
        first = submission_fields("first")
        require(
            store.begin_request_publication(principal, **first).created,
            "capacity probe could not admit its first job",
        )
        try:
            store.begin_request_publication(
                principal, **submission_fields("second")
            )
        except platform_store.SceneArtStorageCapacityExhausted as error:
            require(
                error.public_status == 503
                and error.code == "scene_art_storage_capacity_exhausted"
                and not any(ch.isdigit() for ch in str(error)),
                "capacity error surface does not match the contract",
            )
        else:
            raise PlatformServiceAccountContractError(
                "admission rate bound is not enforced"
            )
        require(
            store.begin_request_publication(principal, **first).created
            is False,
            "exact idempotent replay must stay admitted at capacity",
        )
        moments[0] += probe_capacity.rate_window_seconds + 1
        second = submission_fields("second")
        require(
            store.begin_request_publication(principal, **second).created,
            "sliding admission window does not release rate capacity",
        )
        moments[0] += probe_capacity.rate_window_seconds + 1
        try:
            store.begin_request_publication(
                principal, **submission_fields("third")
            )
        except platform_store.SceneArtStorageCapacityExhausted:
            pass
        else:
            raise PlatformServiceAccountContractError(
                "pending-job bound is not enforced"
            )
        store.mark_failed(
            principal,
            job_id=str(second["job_id"]),
            error_code="capacity_probe_drain",
        )
        moments[0] += probe_capacity.rate_window_seconds + 1
        require(
            store.begin_request_publication(
                principal, **submission_fields("fourth")
            ).created,
            "drained capacity does not admit new work",
        )


def verify_disabled_integration(
    repository: Path | None = None,
    manifest: Path | None = None,
) -> dict[str, object]:
    value = _load_plan_value(manifest)
    plan = validate_plan(value)
    baseline = audit_current_platform(repository)
    _verify_capacity_enforcement(value["capacity_contract"])
    report = plan.public_report(
        baseline, capacity_enforcement_verified=True
    )
    report["contract_valid"] = True
    report["ready"] = False
    return report


__all__ = [
    "EXPECTED_BASELINE_FACTS",
    "EXPECTED_ACK_SCHEMA_DEFINITION",
    "EXPECTED_ACK_SCHEMA_SHA256",
    "EXPECTED_BLOCKERS",
    "EXPECTED_ROUTES",
    "EXPECTED_SCOPES",
    "EXPECTED_STATE_TRANSITIONS",
    "EXPECTED_STATE_VALUES",
    "EXPECTED_SUBMIT_SCHEMA_DEFINITION",
    "EXPECTED_SUBMIT_SCHEMA_SHA256",
    "MANIFEST_RELATIVE_PATH",
    "PlatformServiceAccountContractError",
    "PlatformServiceAccountDisabled",
    "PlatformServiceAccountPlan",
    "audit_current_platform",
    "load_plan",
    "validate_plan",
    "verify_disabled_integration",
    "validate_runtime_view_dir_separation",
]
