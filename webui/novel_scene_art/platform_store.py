"""Owner-private durable store for the dormant Castle scene-art platform.

The HTTP integration is intentionally absent.  Every public operation takes a
nominal context produced by :mod:`platform_service_auth`; no operation accepts
an owner string.  Resource reads and mutations always bind
``(owner_principal, job_id)`` in the same SQLite transaction/query.

Only bounded identifiers, hashes, state markers, and content-addressed output
metadata are retained.  Raw requests, prompts, workflow graphs, model names,
filesystem paths, and image bytes do not belong in this database.
"""

from __future__ import annotations

import math
import hashlib
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

from .compiler import deterministic_prompt_id_fields
from .platform_request_vault import (
    RECORD_OVERHEAD_BYTES as REQUEST_RECORD_OVERHEAD_BYTES,
)
from .platform_service_auth import AuthorizedServiceRequest, ServicePrincipal
from .secure_path import close_descriptors, open_absolute_directory


SCHEMA_VERSION = 4
TOMBSTONE_SCHEMA = "castle.novel-scene-art-output-tombstone.v1"
OUTPUT_MIME = "image/png"
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
MAX_CANONICAL_REQUEST_BYTES = 98_304
MAX_LIST_RESULTS = 128
BUSY_TIMEOUT_MS = 10_000

SUBMIT_INTENT = "submit_intent"
SUBMITTED = "submitted"
QUEUED = "queued"
RUNNING = "running"
SUBMISSION_UNCERTAIN = "submission_uncertain"
OUTPUT_ADOPTED = "output_adopted"
OUTPUT_ACKNOWLEDGED = "output_acknowledged"
CANCELLED = "cancelled"
FAILED = "failed"

JOB_STATES = frozenset(
    {
        SUBMIT_INTENT,
        SUBMITTED,
        QUEUED,
        RUNNING,
        SUBMISSION_UNCERTAIN,
        OUTPUT_ADOPTED,
        OUTPUT_ACKNOWLEDGED,
        CANCELLED,
        FAILED,
    }
)
ALLOWED_TRANSITIONS = frozenset(
    {
        (SUBMIT_INTENT, SUBMITTED),
        (SUBMIT_INTENT, SUBMISSION_UNCERTAIN),
        (SUBMIT_INTENT, FAILED),
        (SUBMISSION_UNCERTAIN, SUBMITTED),
        (SUBMISSION_UNCERTAIN, FAILED),
        (SUBMITTED, QUEUED),
        (SUBMITTED, RUNNING),
        (SUBMITTED, CANCELLED),
        (SUBMITTED, FAILED),
        (QUEUED, RUNNING),
        (QUEUED, CANCELLED),
        (QUEUED, FAILED),
        (RUNNING, OUTPUT_ADOPTED),
        (RUNNING, CANCELLED),
        (RUNNING, FAILED),
        (OUTPUT_ADOPTED, OUTPUT_ACKNOWLEDGED),
    }
)
RECONCILE_OUTCOMES = frozenset(
    {"present", "absent", "indeterminate", "terminal_failed"}
)
TOMBSTONE_STATES = frozenset({"delete_pending", "deleted"})
OUTPUT_OBJECT_STATES = frozenset({"live", "delete_pending", "deleted"})
OUTPUT_MATERIALIZATION_OUTCOMES = frozenset(
    {"adopted", "orphan_held", "orphan_delete_pending"}
)
REQUEST_OBJECT_STATES = frozenset(
    {"publish_intent", "live", "delete_pending", "deleted"}
)

_HASH = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$", re.ASCII)
_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$", re.ASCII)
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$", re.ASCII)
_STORAGE_INCARNATION = re.compile(r"^inc_[a-f0-9]{32}$", re.ASCII)
_PUBLICATION_ID = re.compile(r"^pub_[a-f0-9]{32}$", re.ASCII)
_REQUEST_PUBLICATION_ID = re.compile(r"^reqpub_[a-f0-9]{32}$", re.ASCII)
_REQUEST_STORAGE_INCARNATION = re.compile(
    r"^reqinc_[a-f0-9]{32}$", re.ASCII
)
_MAX_TIMESTAMP = 253_402_300_799
_MAX_OUTPUT_VERSION = 2_147_483_647
_READ_SCOPE = "scene_art.job.read-own"
_SUBMIT_SCOPE = "scene_art.job.submit-own"
_CANCEL_SCOPE = "scene_art.job.cancel-own"
_OUTPUT_READ_SCOPE = "scene_art.output.read-own"
_OUTPUT_ACK_SCOPE = "scene_art.output.ack-own"


class PlatformStoreError(RuntimeError):
    """A bounded, non-sensitive scene-art store failure."""

    __slots__ = ("code", "public_status")

    def __init__(self, code: str, message: str, *, public_status: int) -> None:
        self.code = code
        self.public_status = public_status
        super().__init__(message)

    def public_response(self) -> dict[str, object]:
        return {"ok": False, "error": self.code}


class StoreConfigurationError(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_store_configuration_invalid",
            "Scene art store configuration is invalid",
            public_status=500,
        )


class StoreRequestInvalid(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_store_request_invalid",
            "Scene art store request is invalid",
            public_status=400,
        )


class StoreAuthorizationError(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_store_authorization_invalid",
            "Scene art store authorization is invalid",
            public_status=403,
        )


class SceneArtResourceNotFound(PlatformStoreError):
    """Identical result for an absent job and a job owned by somebody else."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_resource_not_found",
            "Scene art resource not found",
            public_status=404,
        )


class SceneArtIdempotencyConflict(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_idempotency_conflict",
            "Scene art idempotency key conflicts with its retained request",
            public_status=409,
        )


class SceneArtJobConflict(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_job_conflict",
            "Scene art job identity conflicts with its retained request",
            public_status=409,
        )


class SceneArtStateConflict(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_state_conflict",
            "Scene art job is not in the required state",
            public_status=409,
        )


class SceneArtOutputAckConflict(PlatformStoreError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_output_ack_conflict",
            "Scene art output acknowledgement does not match retained output",
            public_status=409,
        )


class SceneArtOutputStoreConflict(PlatformStoreError):
    """The database is bound to a different private CAS identity."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_output_store_conflict",
            "Scene art output store identity conflicts with retained state",
            public_status=409,
        )


class SceneArtRequestVaultConflict(PlatformStoreError):
    """The owner partition is bound to a different request-vault identity."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_request_vault_conflict",
            "Scene art request vault identity conflicts with retained state",
            public_status=409,
        )


class SceneArtRequestPublicationConflict(PlatformStoreError):
    """A retained request-publication intent is not an exact replay."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_request_publication_conflict",
            "Scene art request publication conflicts with retained state",
            public_status=409,
        )


class SceneArtStorageCapacityExhausted(PlatformStoreError):
    """Admitting new durable work would exceed a declared capacity bound.

    The message never carries usage numbers; the capacity contract forbids
    exposing occupancy through this surface.
    """

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(
            "scene_art_storage_capacity_exhausted",
            "Scene art storage capacity is exhausted",
            public_status=503,
        )


_CAPACITY_BLOCK_BYTES = 4096
_MAX_CAPACITY_BOUND = 1_099_511_627_776


def _capacity_bound(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_CAPACITY_BOUND:
        raise StoreConfigurationError()
    return value


def _capacity_section(contract: object, key: str) -> Mapping[str, object]:
    if not isinstance(contract, Mapping):
        raise StoreConfigurationError()
    selected = contract.get(key)
    if not isinstance(selected, Mapping):
        raise StoreConfigurationError()
    return selected


def _allocated_bytes(logical_size: int) -> int:
    blocks, remainder = divmod(logical_size, _CAPACITY_BLOCK_BYTES)
    return (blocks + (1 if remainder else 0)) * _CAPACITY_BLOCK_BYTES


@dataclass(frozen=True, slots=True)
class StoreCapacity:
    """Validated logical bounds enforced inside admission transactions.

    Only new-work admission consults these bounds; drain paths (cancel,
    acknowledge, delete, reconcile) and exact idempotent replays are never
    capacity-blocked, so an over-quota store can always shrink.
    """

    max_pending_jobs: int
    max_active_jobs: int
    max_new_jobs_per_window: int
    rate_window_seconds: int
    request_max_record_bytes: int
    request_max_charged_objects: int
    request_max_charged_logical_bytes: int
    request_max_charged_allocated_bytes: int
    request_max_pending_publications: int
    output_max_png_bytes: int
    output_max_manifest_bytes: int
    output_max_charged_objects: int
    output_max_charged_logical_bytes: int
    output_max_charged_allocated_bytes: int
    output_max_materialization_holds: int
    output_max_unacknowledged_job_references: int
    metadata_page_size_bytes: int
    metadata_max_page_count: int
    metadata_max_wal_bytes: int
    metadata_max_shm_bytes: int
    metadata_wal_autocheckpoint_pages: int
    metadata_max_job_rows: int
    metadata_max_request_publication_rows: int
    metadata_max_output_object_rows: int
    metadata_max_output_materialization_rows: int
    metadata_max_output_tombstone_rows: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _capacity_bound(getattr(self, name))
        if (
            self.metadata_page_size_bytes != _CAPACITY_BLOCK_BYTES
            or self.output_max_png_bytes > MAX_OUTPUT_BYTES
            or self.request_max_record_bytes <= REQUEST_RECORD_OVERHEAD_BYTES
            or self.request_max_record_bytes
            > REQUEST_RECORD_OVERHEAD_BYTES + MAX_CANONICAL_REQUEST_BYTES
        ):
            raise StoreConfigurationError()

    @classmethod
    def from_capacity_contract(cls, contract: object) -> "StoreCapacity":
        admission = _capacity_section(contract, "admission_control")
        request_vault = _capacity_section(contract, "request_vault")
        output_store = _capacity_section(contract, "output_store")
        metadata = _capacity_section(contract, "metadata_store")
        selected = cls(
            max_pending_jobs=_capacity_bound(
                admission.get("max_pending_jobs")
            ),
            max_active_jobs=_capacity_bound(admission.get("max_active_jobs")),
            max_new_jobs_per_window=_capacity_bound(
                admission.get("max_new_jobs_per_window")
            ),
            rate_window_seconds=_capacity_bound(
                admission.get("rate_window_seconds")
            ),
            request_max_record_bytes=_capacity_bound(
                request_vault.get("max_record_bytes")
            ),
            request_max_charged_objects=_capacity_bound(
                request_vault.get("max_charged_objects")
            ),
            request_max_charged_logical_bytes=_capacity_bound(
                request_vault.get("max_charged_logical_bytes")
            ),
            request_max_charged_allocated_bytes=_capacity_bound(
                request_vault.get("max_charged_allocated_bytes")
            ),
            request_max_pending_publications=_capacity_bound(
                request_vault.get("max_pending_publications")
            ),
            output_max_png_bytes=_capacity_bound(
                output_store.get("max_png_bytes")
            ),
            output_max_manifest_bytes=_capacity_bound(
                output_store.get("max_manifest_bytes")
            ),
            output_max_charged_objects=_capacity_bound(
                output_store.get("max_charged_objects")
            ),
            output_max_charged_logical_bytes=_capacity_bound(
                output_store.get("max_charged_logical_bytes")
            ),
            output_max_charged_allocated_bytes=_capacity_bound(
                output_store.get("max_charged_allocated_bytes")
            ),
            output_max_materialization_holds=_capacity_bound(
                output_store.get("max_materialization_holds")
            ),
            output_max_unacknowledged_job_references=_capacity_bound(
                output_store.get("max_unacknowledged_job_references")
            ),
            metadata_page_size_bytes=_capacity_bound(
                metadata.get("page_size_bytes")
            ),
            metadata_max_page_count=_capacity_bound(
                metadata.get("max_page_count")
            ),
            metadata_max_wal_bytes=_capacity_bound(
                metadata.get("max_wal_bytes")
            ),
            metadata_max_shm_bytes=_capacity_bound(
                metadata.get("max_shm_bytes")
            ),
            metadata_wal_autocheckpoint_pages=_capacity_bound(
                metadata.get("wal_autocheckpoint_pages")
            ),
            metadata_max_job_rows=_capacity_bound(
                metadata.get("max_job_rows")
            ),
            metadata_max_request_publication_rows=_capacity_bound(
                metadata.get("max_request_publication_rows")
            ),
            metadata_max_output_object_rows=_capacity_bound(
                metadata.get("max_output_object_rows")
            ),
            metadata_max_output_materialization_rows=_capacity_bound(
                metadata.get("max_output_materialization_rows")
            ),
            metadata_max_output_tombstone_rows=_capacity_bound(
                metadata.get("max_output_tombstone_rows")
            ),
        )
        if (
            _capacity_bound(metadata.get("max_main_database_bytes"))
            != selected.metadata_page_size_bytes
            * selected.metadata_max_page_count
        ):
            raise StoreConfigurationError()
        return selected


@dataclass(frozen=True, slots=True)
class SceneArtJob:
    owner_principal: str
    job_id: str
    request_id: str
    generation_key: str
    canonical_request_sha256: str
    canonical_request_size: int
    request_vault_id: str
    request_storage_incarnation: str
    scene_pack_sha256: str
    render_plan_sha256: str
    renderer_release_sha256: str
    prompt_id: str
    state: str
    dispatch_started_at: int | None
    provider_submission_id: str | None
    submitted_at: int | None
    submission_uncertain_at: int | None
    reconcile_required: bool
    reconcile_attempts: int
    last_reconciled_at: int | None
    last_reconcile_outcome: str | None
    last_error_code: str | None
    cancelled_at: int | None
    output_version: int | None
    output_sha256: str | None
    output_size: int | None
    output_mime: str | None
    output_cas_key: str | None
    output_storage_incarnation: str | None
    output_adopted_at: int | None
    output_acknowledged_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class SubmitIntentResult:
    job: SceneArtJob
    created: bool


@dataclass(frozen=True, slots=True)
class SceneArtOutput:
    owner_principal: str
    job_id: str
    output_version: int
    output_sha256: str
    output_size: int
    output_mime: str
    output_cas_key: str
    output_storage_incarnation: str


@dataclass(frozen=True, slots=True)
class OutputObject:
    owner_principal: str
    output_store_id: str
    output_sha256: str
    output_storage_incarnation: str
    output_size: int
    output_mime: str
    output_cas_key: str
    state: str
    created_at: int
    delete_pending_at: int | None
    deleted_at: int | None


@dataclass(frozen=True, slots=True)
class OutputMaterialization:
    """Durable pre-publication hold for one exact owner/job output."""

    owner_principal: str
    job_id: str
    publication_id: str
    output_store_id: str
    output_version: int
    output_sha256: str
    output_size: int
    output_mime: str
    output_cas_key: str
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class OutputMaterializationBeginResult:
    materialization: OutputMaterialization
    created: bool


@dataclass(frozen=True, slots=True)
class OutputMaterializationFinishResult:
    materialization: OutputMaterialization
    job: SceneArtJob
    output_object: OutputObject
    output: SceneArtOutput | None
    outcome: str


@dataclass(frozen=True, slots=True)
class OutputMaterializationMissingResult:
    materialization: OutputMaterialization
    job: SceneArtJob
    job_failed: bool


@dataclass(frozen=True, slots=True)
class RequestPublication:
    """Persistent ledger for one exact owner/job canonical request object."""

    owner_principal: str
    job_id: str
    publication_id: str
    request_id: str
    generation_key: str
    canonical_request_sha256: str
    canonical_request_size: int
    request_vault_id: str
    request_storage_incarnation: str
    scene_pack_sha256: str
    render_plan_sha256: str
    renderer_release_sha256: str
    prompt_id: str
    state: str
    cancelled_at: int | None
    published_at: int | None
    delete_pending_at: int | None
    deleted_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class RequestPublicationBeginResult:
    publication: RequestPublication
    job: SceneArtJob
    created: bool


@dataclass(frozen=True, slots=True)
class RequestPublicationFinishResult:
    publication: RequestPublication
    job: SceneArtJob
    created: bool


@dataclass(frozen=True, slots=True)
class PendingRequestDeletion:
    owner_principal: str
    job_id: str
    publication_id: str
    request_vault_id: str
    generation_key: str
    canonical_request_sha256: str
    canonical_request_size: int
    request_storage_incarnation: str


@dataclass(frozen=True, slots=True)
class OutputAckTransition:
    tombstone: "OutputTombstone"
    replayed: bool
    object_state: str


@dataclass(frozen=True, slots=True)
class PendingObjectDeletion:
    """Exact internal deletion authority, never a caller-selected path."""

    owner_principal: str
    output_store_id: str
    output_sha256: str
    output_storage_incarnation: str
    output_size: int
    output_mime: str
    output_cas_key: str


@dataclass(frozen=True, slots=True)
class FinalizedObjectDeletion:
    deletion: PendingObjectDeletion
    tombstones: tuple["OutputTombstone", ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class OutputTombstone:
    schema_version: str
    owner_principal: str
    job_id: str
    output_sha256: str
    output_version: int
    state: str
    acknowledged_at: int
    deleted_at: int | None

    @property
    def output_cas_key(self) -> str:
        """Return audit metadata, not authority to delete shared CAS bytes."""

        return content_addressed_key(self.output_sha256)


AuthContext = ServicePrincipal | AuthorizedServiceRequest


_JOB_COLUMNS = """owner_principal,job_id,request_id,generation_key,canonical_request_sha256,
canonical_request_size,request_vault_id,request_storage_incarnation,
scene_pack_sha256,render_plan_sha256,renderer_release_sha256,prompt_id,state,
dispatch_started_at,provider_submission_id,submitted_at,
submission_uncertain_at,reconcile_required,reconcile_attempts,last_reconciled_at,
last_reconcile_outcome,last_error_code,cancelled_at,output_version,output_sha256,
output_size,output_mime,output_cas_key,output_adopted_at,output_acknowledged_at,
output_storage_incarnation,created_at,updated_at"""
_TOMBSTONE_COLUMNS = """schema_version,owner_principal,job_id,output_sha256,
output_version,state,acknowledged_at,deleted_at"""
_OBJECT_COLUMNS = """owner_principal,output_store_id,output_sha256,
output_storage_incarnation,output_size,output_mime,output_cas_key,state,
created_at,delete_pending_at,deleted_at"""
_MATERIALIZATION_COLUMNS = """owner_principal,job_id,publication_id,
output_store_id,output_version,output_sha256,output_size,output_mime,
output_cas_key,created_at,updated_at"""
_REQUEST_PUBLICATION_COLUMNS = """owner_principal,job_id,publication_id,
request_id,generation_key,canonical_request_sha256,canonical_request_size,
request_vault_id,request_storage_incarnation,scene_pack_sha256,
render_plan_sha256,renderer_release_sha256,prompt_id,state,cancelled_at,
published_at,delete_pending_at,deleted_at,created_at,updated_at"""

_SCHEMA_OBJECTS = (
    (
        "table",
        "scene_art_schema_migrations",
        """CREATE TABLE scene_art_schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at INTEGER NOT NULL
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_jobs",
        """CREATE TABLE scene_art_jobs (
          owner_principal TEXT NOT NULL,
          job_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          generation_key TEXT NOT NULL,
          canonical_request_sha256 TEXT NOT NULL,
          canonical_request_size INTEGER NOT NULL,
          request_vault_id TEXT NOT NULL,
          request_storage_incarnation TEXT NOT NULL,
          scene_pack_sha256 TEXT NOT NULL,
          render_plan_sha256 TEXT NOT NULL,
          renderer_release_sha256 TEXT NOT NULL,
          prompt_id TEXT NOT NULL,
          state TEXT NOT NULL,
          dispatch_started_at INTEGER,
          provider_submission_id TEXT,
          submitted_at INTEGER,
          submission_uncertain_at INTEGER,
          reconcile_required INTEGER NOT NULL DEFAULT 0,
          reconcile_attempts INTEGER NOT NULL DEFAULT 0,
          last_reconciled_at INTEGER,
          last_reconcile_outcome TEXT,
          last_error_code TEXT,
          cancelled_at INTEGER,
          output_version INTEGER,
          output_sha256 TEXT,
          output_size INTEGER,
          output_mime TEXT,
          output_cas_key TEXT,
          output_storage_incarnation TEXT,
          output_adopted_at INTEGER,
          output_acknowledged_at INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (owner_principal, job_id),
          UNIQUE (owner_principal, generation_key),
          UNIQUE (owner_principal, prompt_id),
          FOREIGN KEY (
            owner_principal, output_sha256, output_storage_incarnation
          ) REFERENCES scene_art_output_objects(
            owner_principal, output_sha256, output_storage_incarnation
          ) ON DELETE RESTRICT,
          FOREIGN KEY (owner_principal, request_vault_id)
            REFERENCES scene_art_request_vault_bindings(
              owner_principal, request_vault_id
            ) ON DELETE RESTRICT,
          CHECK (length(owner_principal) BETWEEN 1 AND 160),
          CHECK (length(job_id) BETWEEN 1 AND 160),
          CHECK (length(request_id) BETWEEN 1 AND 160),
          CHECK (length(prompt_id) = 36),
          CHECK (state IN (
            'submit_intent','submitted','queued','running',
            'submission_uncertain','output_adopted',
            'output_acknowledged','cancelled','failed'
          )),
          CHECK (length(generation_key) = 64),
          CHECK (length(canonical_request_sha256) = 64),
          CHECK (canonical_request_size BETWEEN 1 AND 98304),
          CHECK (length(request_vault_id) = 64 AND
                 request_vault_id NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(request_storage_incarnation) = 39 AND
                 substr(request_storage_incarnation, 1, 7) = 'reqinc_' AND
                 substr(request_storage_incarnation, 8)
                   NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(scene_pack_sha256) = 64),
          CHECK (length(render_plan_sha256) = 64),
          CHECK (length(renderer_release_sha256) = 64),
          CHECK (provider_submission_id IS NULL OR
                 provider_submission_id = prompt_id),
          CHECK (reconcile_required IN (0, 1)),
          CHECK (reconcile_attempts >= 0),
          CHECK (reconcile_required = 0 OR
                 state = 'submission_uncertain'),
          CHECK (last_reconcile_outcome IS NULL OR
                 last_reconcile_outcome IN (
                   'present','absent','indeterminate','terminal_failed'
                 )),
          CHECK (output_version IS NULL OR
                 output_version BETWEEN 1 AND 2147483647),
          CHECK (output_size IS NULL OR
                 output_size BETWEEN 1 AND 12582912),
          CHECK (output_mime IS NULL OR output_mime = 'image/png'),
          CHECK (output_sha256 IS NULL OR length(output_sha256) = 64),
          CHECK (output_cas_key IS NULL OR
                 output_cas_key = 'sha256:' || output_sha256),
          CHECK (output_storage_incarnation IS NULL OR
                 (length(output_storage_incarnation) = 36 AND
                  substr(output_storage_incarnation, 1, 4) = 'inc_' AND
                  substr(output_storage_incarnation, 5)
                    NOT GLOB '*[^0-9a-f]*')),
          CHECK ((output_version IS NULL AND output_sha256 IS NULL AND
                  output_size IS NULL AND output_mime IS NULL AND
                  output_cas_key IS NULL AND
                  output_storage_incarnation IS NULL AND
                  output_adopted_at IS NULL) OR
                 (output_version IS NOT NULL AND output_sha256 IS NOT NULL AND
                  output_size IS NOT NULL AND output_mime = 'image/png' AND
                  output_cas_key IS NOT NULL AND
                  output_storage_incarnation IS NOT NULL AND
                  output_adopted_at IS NOT NULL)),
          CHECK ((state IN ('output_adopted','output_acknowledged') AND
                  output_version IS NOT NULL) OR
                 (state NOT IN ('output_adopted','output_acknowledged') AND
                  output_version IS NULL)),
          CHECK ((state = 'output_acknowledged' AND
                  output_acknowledged_at IS NOT NULL) OR
                 (state != 'output_acknowledged' AND
                  output_acknowledged_at IS NULL))
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_request_vault_bindings",
        """CREATE TABLE scene_art_request_vault_bindings (
          owner_principal TEXT PRIMARY KEY,
          request_vault_id TEXT NOT NULL,
          bound_at INTEGER NOT NULL,
          UNIQUE (owner_principal, request_vault_id),
          CHECK (length(owner_principal) BETWEEN 1 AND 160),
          CHECK (length(request_vault_id) = 64 AND
                 request_vault_id NOT GLOB '*[^0-9a-f]*')
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_output_store_bindings",
        """CREATE TABLE scene_art_output_store_bindings (
          owner_principal TEXT PRIMARY KEY,
          output_store_id TEXT NOT NULL,
          bound_at INTEGER NOT NULL,
          UNIQUE (owner_principal, output_store_id),
          CHECK (length(owner_principal) BETWEEN 1 AND 160),
          CHECK (length(output_store_id) = 64 AND
                 output_store_id NOT GLOB '*[^0-9a-f]*')
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_request_publications",
        """CREATE TABLE scene_art_request_publications (
          owner_principal TEXT NOT NULL,
          job_id TEXT NOT NULL,
          publication_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          generation_key TEXT NOT NULL,
          canonical_request_sha256 TEXT NOT NULL,
          canonical_request_size INTEGER NOT NULL,
          request_vault_id TEXT NOT NULL,
          request_storage_incarnation TEXT NOT NULL,
          scene_pack_sha256 TEXT NOT NULL,
          render_plan_sha256 TEXT NOT NULL,
          renderer_release_sha256 TEXT NOT NULL,
          prompt_id TEXT NOT NULL,
          state TEXT NOT NULL,
          cancelled_at INTEGER,
          published_at INTEGER,
          delete_pending_at INTEGER,
          deleted_at INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (owner_principal, job_id),
          UNIQUE (owner_principal, publication_id),
          UNIQUE (owner_principal, generation_key),
          UNIQUE (owner_principal, prompt_id),
          FOREIGN KEY (owner_principal, job_id)
            REFERENCES scene_art_jobs(owner_principal, job_id)
            ON DELETE RESTRICT,
          FOREIGN KEY (owner_principal, request_vault_id)
            REFERENCES scene_art_request_vault_bindings(
              owner_principal, request_vault_id
            ) ON DELETE RESTRICT,
          CHECK (length(owner_principal) BETWEEN 1 AND 160),
          CHECK (length(job_id) BETWEEN 1 AND 160),
          CHECK (length(publication_id) = 39 AND
                 substr(publication_id, 1, 7) = 'reqpub_' AND
                 substr(publication_id, 8) NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(request_id) BETWEEN 1 AND 160),
          CHECK (length(generation_key) = 64 AND
                 generation_key NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(canonical_request_sha256) = 64 AND
                 canonical_request_sha256 NOT GLOB '*[^0-9a-f]*'),
          CHECK (canonical_request_size BETWEEN 1 AND 98304),
          CHECK (length(request_vault_id) = 64 AND
                 request_vault_id NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(request_storage_incarnation) = 39 AND
                 substr(request_storage_incarnation, 1, 7) = 'reqinc_' AND
                 substr(request_storage_incarnation, 8)
                   NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(scene_pack_sha256) = 64 AND
                 scene_pack_sha256 NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(render_plan_sha256) = 64 AND
                 render_plan_sha256 NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(renderer_release_sha256) = 64 AND
                 renderer_release_sha256 NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(prompt_id) = 36),
          CHECK (state IN (
            'publish_intent','live','delete_pending','deleted'
          )),
          CHECK (cancelled_at IS NULL OR
                 cancelled_at BETWEEN created_at AND updated_at),
          CHECK ((state = 'publish_intent' AND published_at IS NULL AND
                  delete_pending_at IS NULL AND deleted_at IS NULL) OR
                 (state = 'live' AND published_at IS NOT NULL AND
                  published_at BETWEEN created_at AND updated_at AND
                  delete_pending_at IS NULL AND deleted_at IS NULL) OR
                 (state = 'delete_pending' AND
                  delete_pending_at IS NOT NULL AND
                  delete_pending_at BETWEEN created_at AND updated_at AND
                  deleted_at IS NULL) OR
                 (state = 'deleted' AND delete_pending_at IS NOT NULL AND
                  deleted_at IS NOT NULL AND
                  delete_pending_at BETWEEN created_at AND deleted_at AND
                  deleted_at <= updated_at)),
          CHECK (created_at BETWEEN 0 AND 253402300799),
          CHECK (updated_at BETWEEN created_at AND 253402300799)
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_output_objects",
        """CREATE TABLE scene_art_output_objects (
          owner_principal TEXT NOT NULL,
          output_store_id TEXT NOT NULL,
          output_sha256 TEXT NOT NULL,
          output_storage_incarnation TEXT NOT NULL,
          output_size INTEGER NOT NULL,
          output_mime TEXT NOT NULL,
          output_cas_key TEXT NOT NULL,
          state TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          delete_pending_at INTEGER,
          deleted_at INTEGER,
          PRIMARY KEY (
            owner_principal, output_sha256, output_storage_incarnation
          ),
          FOREIGN KEY (owner_principal, output_store_id)
            REFERENCES scene_art_output_store_bindings(
              owner_principal, output_store_id
            ) ON DELETE RESTRICT,
          CHECK (length(owner_principal) BETWEEN 1 AND 160),
          CHECK (length(output_store_id) = 64 AND
                 output_store_id NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(output_sha256) = 64 AND
                 output_sha256 NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(output_storage_incarnation) = 36 AND
                 substr(output_storage_incarnation, 1, 4) = 'inc_' AND
                 substr(output_storage_incarnation, 5)
                   NOT GLOB '*[^0-9a-f]*'),
          CHECK (output_size BETWEEN 1 AND 12582912),
          CHECK (output_mime = 'image/png'),
          CHECK (output_cas_key = 'sha256:' || output_sha256),
          CHECK (state IN ('live','delete_pending','deleted')),
          CHECK ((state = 'live' AND delete_pending_at IS NULL AND
                  deleted_at IS NULL) OR
                 (state = 'delete_pending' AND
                  delete_pending_at IS NOT NULL AND deleted_at IS NULL) OR
                 (state = 'deleted' AND delete_pending_at IS NOT NULL AND
                  deleted_at IS NOT NULL))
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_output_tombstones",
        """CREATE TABLE scene_art_output_tombstones (
          schema_version TEXT NOT NULL,
          owner_principal TEXT NOT NULL,
          job_id TEXT NOT NULL,
          output_sha256 TEXT NOT NULL,
          output_version INTEGER NOT NULL,
          state TEXT NOT NULL,
          acknowledged_at INTEGER NOT NULL,
          deleted_at INTEGER,
          PRIMARY KEY (
            owner_principal, job_id, output_sha256, output_version
          ),
          FOREIGN KEY (owner_principal, job_id)
            REFERENCES scene_art_jobs(owner_principal, job_id)
            ON DELETE RESTRICT,
          CHECK (schema_version =
            'castle.novel-scene-art-output-tombstone.v1'),
          CHECK (length(output_sha256) = 64),
          CHECK (output_version BETWEEN 1 AND 2147483647),
          CHECK (state IN ('delete_pending','deleted')),
          CHECK ((state = 'delete_pending' AND deleted_at IS NULL) OR
                 (state = 'deleted' AND deleted_at IS NOT NULL))
        ) STRICT""",
    ),
    (
        "table",
        "scene_art_output_materializations",
        """CREATE TABLE scene_art_output_materializations (
          owner_principal TEXT NOT NULL,
          job_id TEXT NOT NULL,
          publication_id TEXT NOT NULL,
          output_store_id TEXT NOT NULL,
          output_version INTEGER NOT NULL,
          output_sha256 TEXT NOT NULL,
          output_size INTEGER NOT NULL,
          output_mime TEXT NOT NULL,
          output_cas_key TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (owner_principal, job_id),
          UNIQUE (owner_principal, publication_id),
          FOREIGN KEY (owner_principal, job_id)
            REFERENCES scene_art_jobs(owner_principal, job_id)
            ON DELETE RESTRICT,
          FOREIGN KEY (owner_principal, output_store_id)
            REFERENCES scene_art_output_store_bindings(
              owner_principal, output_store_id
            ) ON DELETE RESTRICT,
          CHECK (length(owner_principal) BETWEEN 1 AND 160),
          CHECK (length(job_id) BETWEEN 1 AND 160),
          CHECK (length(publication_id) = 36 AND
                 substr(publication_id, 1, 4) = 'pub_' AND
                 substr(publication_id, 5) NOT GLOB '*[^0-9a-f]*'),
          CHECK (length(output_store_id) = 64 AND
                 output_store_id NOT GLOB '*[^0-9a-f]*'),
          CHECK (output_version BETWEEN 1 AND 2147483647),
          CHECK (length(output_sha256) = 64 AND
                 output_sha256 NOT GLOB '*[^0-9a-f]*'),
          CHECK (output_size BETWEEN 1 AND 12582912),
          CHECK (output_mime = 'image/png'),
          CHECK (output_cas_key = 'sha256:' || output_sha256),
          CHECK (created_at BETWEEN 0 AND 253402300799),
          CHECK (updated_at BETWEEN created_at AND 253402300799)
        ) STRICT""",
    ),
    (
        "index",
        "scene_art_jobs_reconcile",
        """CREATE INDEX scene_art_jobs_reconcile
           ON scene_art_jobs(owner_principal,reconcile_required,updated_at,job_id)""",
    ),
    (
        "index",
        "scene_art_tombstones_pending",
        """CREATE INDEX scene_art_tombstones_pending
           ON scene_art_output_tombstones(
             owner_principal,state,acknowledged_at,job_id
           )""",
    ),
    (
        "index",
        "scene_art_output_one_live_digest",
        """CREATE UNIQUE INDEX scene_art_output_one_live_digest
           ON scene_art_output_objects(owner_principal,output_sha256)
           WHERE state='live'""",
    ),
    (
        "index",
        "scene_art_output_pending_delete",
        """CREATE INDEX scene_art_output_pending_delete
           ON scene_art_output_objects(
             owner_principal,state,delete_pending_at,
             output_sha256,output_storage_incarnation
           )""",
    ),
    (
        "index",
        "scene_art_jobs_output_references",
        """CREATE INDEX scene_art_jobs_output_references
           ON scene_art_jobs(
             owner_principal,output_sha256,output_storage_incarnation,
             state,job_id
        )""",
    ),
    (
        "index",
        "scene_art_output_materialization_holds",
        """CREATE INDEX scene_art_output_materialization_holds
           ON scene_art_output_materializations(
             owner_principal,output_store_id,output_sha256,created_at,job_id
        )""",
    ),
    (
        "index",
        "scene_art_request_publications_pending",
        """CREATE INDEX scene_art_request_publications_pending
           ON scene_art_request_publications(
             owner_principal,request_vault_id,state,created_at,job_id
           )""",
    ),
)


_V3_SCHEMA_OBJECTS = tuple(
    item
    for item in _SCHEMA_OBJECTS
    if item[1]
    not in {
        "scene_art_request_publications",
        "scene_art_request_publications_pending",
    }
)


def _normalized_schema_sql(value: object) -> str:
    if not isinstance(value, str):
        raise StoreConfigurationError()
    return " ".join(value.split())


def _invalid() -> "NoReturn":
    raise StoreRequestInvalid()


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        _invalid()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        _invalid()
    return value


def _timestamp(value: object) -> int:
    if type(value) is not int or value < 0 or value > _MAX_TIMESTAMP:
        _invalid()
    return value


def _limit(value: object) -> int:
    if type(value) is not int or value < 1 or value > MAX_LIST_RESULTS:
        _invalid()
    return value


def _output_version(value: object) -> int:
    if type(value) is not int or value < 1 or value > _MAX_OUTPUT_VERSION:
        _invalid()
    return value


def _output_size(value: object) -> int:
    if type(value) is not int or value < 1 or value > MAX_OUTPUT_BYTES:
        _invalid()
    return value


def _canonical_request_size(value: object) -> int:
    if (
        type(value) is not int
        or value < 1
        or value > MAX_CANONICAL_REQUEST_BYTES
    ):
        _invalid()
    return value


def _error_code(value: object) -> str:
    if type(value) is not str or _ERROR_CODE.fullmatch(value) is None:
        _invalid()
    return value


def _storage_incarnation(value: object) -> str:
    if (
        type(value) is not str
        or _STORAGE_INCARNATION.fullmatch(value) is None
    ):
        _invalid()
    return value


def _publication_id(value: object) -> str:
    if type(value) is not str or _PUBLICATION_ID.fullmatch(value) is None:
        _invalid()
    return value


def _request_publication_id(value: object) -> str:
    if (
        type(value) is not str
        or _REQUEST_PUBLICATION_ID.fullmatch(value) is None
    ):
        _invalid()
    return value


def _compatibility_request_publication_id(*values: str) -> str:
    if not values or any(type(value) is not str for value in values):
        _invalid()
    payload = b"castle-request-publication-compatibility-v1\0" + b"\0".join(
        value.encode("ascii", "strict") for value in values
    )
    return "reqpub_" + hashlib.sha256(payload).hexdigest()[:32]


def _request_storage_incarnation(value: object) -> str:
    if (
        type(value) is not str
        or _REQUEST_STORAGE_INCARNATION.fullmatch(value) is None
    ):
        _invalid()
    return value


def _request_publication_projection(
    value: object,
) -> RequestPublication:
    if type(value) is not RequestPublication:
        _invalid()
    assert type(value) is RequestPublication
    _identifier(value.owner_principal)
    _identifier(value.job_id)
    _request_publication_id(value.publication_id)
    _identifier(value.request_id)
    _digest(value.generation_key)
    _digest(value.canonical_request_sha256)
    _canonical_request_size(value.canonical_request_size)
    _digest(value.request_vault_id)
    _request_storage_incarnation(value.request_storage_incarnation)
    _digest(value.scene_pack_sha256)
    _digest(value.render_plan_sha256)
    _digest(value.renderer_release_sha256)
    if (
        type(value.prompt_id) is not str
        or len(value.prompt_id) != 36
        or _IDENTIFIER.fullmatch(value.prompt_id) is None
        or type(value.state) is not str
        or value.state not in REQUEST_OBJECT_STATES
        or type(value.created_at) is not int
        or not 0 <= value.created_at <= _MAX_TIMESTAMP
        or type(value.updated_at) is not int
        or not value.created_at <= value.updated_at <= _MAX_TIMESTAMP
        or (
            value.published_at is not None
            and (
                type(value.published_at) is not int
                or not value.created_at
                <= value.published_at
                <= value.updated_at
            )
        )
        or (
            value.cancelled_at is not None
            and (
                type(value.cancelled_at) is not int
                or not value.created_at
                <= value.cancelled_at
                <= value.updated_at
            )
        )
        or (
            value.state == "live"
            and (
                type(value.published_at) is not int
                or not value.created_at
                <= value.published_at
                <= value.updated_at
                or value.delete_pending_at is not None
                or value.deleted_at is not None
            )
        )
        or (
            value.state == "publish_intent"
            and (
                value.published_at is not None
                or value.delete_pending_at is not None
                or value.deleted_at is not None
            )
        )
        or (
            value.state == "delete_pending"
            and (
                type(value.delete_pending_at) is not int
                or not value.created_at
                <= value.delete_pending_at
                <= value.updated_at
                or value.deleted_at is not None
            )
        )
        or (
            value.state == "deleted"
            and (
                type(value.delete_pending_at) is not int
                or type(value.deleted_at) is not int
                or not value.created_at
                <= value.delete_pending_at
                <= value.deleted_at
                <= value.updated_at
            )
        )
    ):
        _invalid()
    return value


def _request_publication_immutable_tuple(
    value: RequestPublication,
) -> tuple[object, ...]:
    selected = _request_publication_projection(value)
    return (
        selected.owner_principal,
        selected.job_id,
        selected.publication_id,
        selected.request_id,
        selected.generation_key,
        selected.canonical_request_sha256,
        selected.canonical_request_size,
        selected.request_vault_id,
        selected.request_storage_incarnation,
        selected.scene_pack_sha256,
        selected.render_plan_sha256,
        selected.renderer_release_sha256,
        selected.prompt_id,
        selected.created_at,
    )


def _pending_request_deletion_projection(
    value: object,
) -> PendingRequestDeletion:
    if type(value) is not PendingRequestDeletion:
        _invalid()
    assert type(value) is PendingRequestDeletion
    _identifier(value.owner_principal)
    _identifier(value.job_id)
    _request_publication_id(value.publication_id)
    _digest(value.request_vault_id)
    _digest(value.generation_key)
    _digest(value.canonical_request_sha256)
    _canonical_request_size(value.canonical_request_size)
    _request_storage_incarnation(value.request_storage_incarnation)
    return value


def content_addressed_key(output_sha256: str) -> str:
    """Return the one allowed opaque CAS key; it is not a filesystem path."""

    return "sha256:" + _digest(output_sha256)


def _authenticated_owner(context: object, required_scope: str) -> str:
    """Extract an owner only from the nominal post-authentication types."""

    if type(context) is ServicePrincipal:
        principal = context
        owner = principal.principal_id
        scopes = principal.scopes
        key_id = principal.key_id
    elif type(context) is AuthorizedServiceRequest:
        request = context
        if (
            type(request.principal_id) is not str
            or type(request.owner_principal) is not str
            or request.principal_id != request.owner_principal
        ):
            raise StoreAuthorizationError()
        principal = request
        owner = request.owner_principal
        scopes = (request.scope,)
        key_id = request.key_id
    else:
        raise StoreAuthorizationError()
    if (
        type(owner) is not str
        or _IDENTIFIER.fullmatch(owner) is None
        or type(key_id) is not str
        or _KEY_ID.fullmatch(key_id) is None
        or type(scopes) is not tuple
        or any(type(scope) is not str for scope in scopes)
        or required_scope not in scopes
        or type(principal.principal_id) is not str
        or principal.principal_id != owner
    ):
        raise StoreAuthorizationError()
    return owner


def _route_job_id(context: object, required_scope: str) -> tuple[str, str]:
    owner = _authenticated_owner(context, required_scope)
    if type(context) is not AuthorizedServiceRequest:
        raise StoreAuthorizationError()
    request = context
    if (
        request.atomic_owner_lookup_required is not True
        or request.job_id is None
        or type(request.job_id) is not str
        or request.owner_principal != owner
    ):
        raise StoreAuthorizationError()
    return owner, _identifier(request.job_id)


def _internal_job_id(
    context: object, job_id: object, required_scope: str = _SUBMIT_SCOPE
) -> tuple[str, str]:
    owner = _authenticated_owner(context, required_scope)
    selected = _identifier(job_id)
    if type(context) is AuthorizedServiceRequest:
        request = context
        if request.job_id is not None and (
            type(request.job_id) is not str or request.job_id != selected
        ):
            raise StoreAuthorizationError()
    return owner, selected


def _now(clock: Callable[[], int | float]) -> int:
    try:
        selected = clock()
    except Exception as error:
        raise StoreConfigurationError() from error
    if (
        isinstance(selected, bool)
        or not isinstance(selected, (int, float))
        or not math.isfinite(selected)
        or selected < 0
        or selected > _MAX_TIMESTAMP
    ):
        raise StoreConfigurationError()
    return int(selected)


class PlatformSceneArtStore:
    """A dedicated SQLite database with no live-server defaults or route wiring."""

    __slots__ = ("database_path", "_capacity", "_clock")

    def __init__(
        self,
        database_path: Path,
        *,
        capacity: StoreCapacity,
        clock: Callable[[], int | float] = time.time,
    ) -> None:
        if (
            not isinstance(database_path, Path)
            or not database_path.is_absolute()
            or "\x00" in os.fspath(database_path)
            or len(os.fspath(database_path)) > 4096
            or type(capacity) is not StoreCapacity
            or not callable(clock)
        ):
            raise StoreConfigurationError()
        self.database_path = Path(os.path.abspath(os.fspath(database_path)))
        self._capacity = capacity
        self._clock = clock
        self._prepare_private_database()
        self._migrate()

    @property
    def capacity(self) -> StoreCapacity:
        """Return the immutable capacity contract this store enforces."""

        return self._capacity

    def _prepare_private_database(self) -> None:
        parent = self.database_path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptors = open_absolute_directory(
                parent,
                code="scene_art_store_configuration_invalid",
                message="Scene art store configuration is invalid",
            )
        except Exception as error:
            raise StoreConfigurationError() from error
        try:
            info = os.fstat(descriptors[-1])
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise StoreConfigurationError()
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(
                    self.database_path.name, flags, 0o600, dir_fd=descriptors[-1]
                )
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                os.close(descriptor)
            self._verify_private_database_file()
        except PlatformStoreError:
            raise
        except OSError as error:
            raise StoreConfigurationError() from error
        finally:
            close_descriptors(descriptors)

    def _verify_private_database_file(self) -> None:
        try:
            info = self.database_path.lstat()
        except OSError as error:
            raise StoreConfigurationError() from error
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise StoreConfigurationError()

    def _harden_sidecars(self) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            selected = Path(os.fspath(self.database_path) + suffix)
            try:
                info = selected.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise StoreConfigurationError() from error
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise StoreConfigurationError()
            if info.st_uid != os.geteuid() or info.st_nlink != 1:
                raise StoreConfigurationError()
            try:
                os.chmod(selected, 0o600, follow_symlinks=False)
            except OSError as error:
                raise StoreConfigurationError() from error

    def _connect(self) -> sqlite3.Connection:
        self._verify_private_database_file()
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            page_size = connection.execute("PRAGMA page_size").fetchone()
            if (
                page_size is None
                or page_size[0] != self._capacity.metadata_page_size_bytes
            ):
                raise StoreConfigurationError()
            page_bound = connection.execute(
                f"PRAGMA max_page_count="
                f"{self._capacity.metadata_max_page_count}"
            ).fetchone()
            if (
                page_bound is None
                or page_bound[0] < self._capacity.metadata_max_page_count
            ):
                raise StoreConfigurationError()
            connection.execute(
                f"PRAGMA wal_autocheckpoint="
                f"{self._capacity.metadata_wal_autocheckpoint_pages}"
            )
            self._harden_sidecars()
            return connection
        except (OSError, sqlite3.Error) as error:
            try:
                connection.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            raise StoreConfigurationError() from error

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._harden_sidecars()

    def _migrate(self) -> None:
        with closing(self._connect()) as connection:
            try:
                row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if row is None or str(row[0]).lower() != "wal":
                    raise StoreConfigurationError()
            except sqlite3.Error as error:
                raise StoreConfigurationError() from error
        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    """SELECT type,name,sql FROM sqlite_schema
                       WHERE name NOT LIKE 'sqlite_%'
                       ORDER BY type,name"""
                ).fetchall()
                if not existing:
                    for _kind, _name, sql in _SCHEMA_OBJECTS:
                        connection.execute(sql)
                    connection.execute(
                        """INSERT INTO scene_art_schema_migrations(version,applied_at)
                           VALUES (?,?)""",
                        (SCHEMA_VERSION, _now(self._clock)),
                    )
                else:
                    self._migrate_v3_to_v4(connection, existing)
                self._validate_schema(connection)
        except StoreConfigurationError:
            raise
        except sqlite3.Error as error:
            raise StoreConfigurationError() from error
        self._verify_private_database_file()

    def _migrate_v3_to_v4(
        self,
        connection: sqlite3.Connection,
        existing: list[sqlite3.Row],
    ) -> None:
        """Apply the sole additive migration after exact v3 verification."""

        expected_v3 = {
            (kind, name): _normalized_schema_sql(sql)
            for kind, name, sql in _V3_SCHEMA_OBJECTS
        }
        actual = {
            (str(row["type"]), str(row["name"])): _normalized_schema_sql(
                row["sql"]
            )
            for row in existing
        }
        if actual != expected_v3:
            return
        migrations = connection.execute(
            """SELECT version,applied_at FROM scene_art_schema_migrations
               ORDER BY version"""
        ).fetchall()
        if (
            len(migrations) != 1
            or type(migrations[0]["version"]) is not int
            or migrations[0]["version"] != 3
            or type(migrations[0]["applied_at"]) is not int
            or not 0 <= migrations[0]["applied_at"] <= _MAX_TIMESTAMP
        ):
            raise StoreConfigurationError()
        if connection.execute(
            "SELECT 1 FROM scene_art_jobs LIMIT 1"
        ).fetchone() is not None:
            # v3 has no durable proof that its caller-selected incarnation is
            # present in this vault. Only an empty dormant database can be
            # upgraded online; populated databases require an audited offline
            # vault reconciliation instead of silently declaring bytes live.
            raise StoreConfigurationError()
        additions = {
            name: sql
            for _kind, name, sql in _SCHEMA_OBJECTS
            if name
            in {
                "scene_art_request_publications",
                "scene_art_request_publications_pending",
            }
        }
        connection.execute(additions["scene_art_request_publications"])
        connection.execute(additions["scene_art_request_publications_pending"])
        connection.execute(
            """UPDATE scene_art_schema_migrations
               SET version=?,applied_at=? WHERE version=3""",
            (SCHEMA_VERSION, _now(self._clock)),
        )

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        expected = {
            (kind, name): _normalized_schema_sql(sql)
            for kind, name, sql in _SCHEMA_OBJECTS
        }
        rows = connection.execute(
            """SELECT type,name,sql FROM sqlite_schema
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type,name"""
        ).fetchall()
        actual = {
            (str(row["type"]), str(row["name"])): _normalized_schema_sql(row["sql"])
            for row in rows
        }
        if actual != expected:
            raise StoreConfigurationError()

        migrations = connection.execute(
            """SELECT version,applied_at FROM scene_art_schema_migrations
               ORDER BY version"""
        ).fetchall()
        if (
            len(migrations) != 1
            or type(migrations[0]["version"]) is not int
            or migrations[0]["version"] != SCHEMA_VERSION
            or type(migrations[0]["applied_at"]) is not int
            or migrations[0]["applied_at"] < 0
            or migrations[0]["applied_at"] > _MAX_TIMESTAMP
        ):
            raise StoreConfigurationError()

        integrity = connection.execute("PRAGMA quick_check").fetchall()
        if len(integrity) != 1 or integrity[0][0] != "ok":
            raise StoreConfigurationError()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise StoreConfigurationError()
        output_store_drift = connection.execute(
            """SELECT 1 FROM scene_art_output_objects AS o
               LEFT JOIN scene_art_output_store_bindings AS b
                 ON b.owner_principal=o.owner_principal
                AND b.output_store_id=o.output_store_id
               WHERE b.owner_principal IS NULL LIMIT 1"""
        ).fetchone()
        if output_store_drift is not None:
            raise StoreConfigurationError()
        materialization_store_drift = connection.execute(
            """SELECT 1 FROM scene_art_output_materializations AS m
               LEFT JOIN scene_art_output_store_bindings AS b
                 ON b.owner_principal=m.owner_principal
                AND b.output_store_id=m.output_store_id
               WHERE b.owner_principal IS NULL LIMIT 1"""
        ).fetchone()
        if materialization_store_drift is not None:
            raise StoreConfigurationError()
        request_publication_vault_drift = connection.execute(
            """SELECT 1 FROM scene_art_request_publications AS p
               LEFT JOIN scene_art_request_vault_bindings AS b
                 ON b.owner_principal=p.owner_principal
                AND b.request_vault_id=p.request_vault_id
               WHERE b.owner_principal IS NULL LIMIT 1"""
        ).fetchone()
        if request_publication_vault_drift is not None:
            raise StoreConfigurationError()
        invalid_request_publication_job = connection.execute(
            """SELECT 1 FROM scene_art_request_publications AS p
               LEFT JOIN scene_art_jobs AS j
                 ON j.owner_principal=p.owner_principal AND j.job_id=p.job_id
               WHERE j.job_id IS NULL OR
                     j.request_id!=p.request_id OR
                     j.generation_key!=p.generation_key OR
                     j.canonical_request_sha256!=p.canonical_request_sha256 OR
                     j.canonical_request_size!=p.canonical_request_size OR
                     j.request_vault_id!=p.request_vault_id OR
                     j.request_storage_incarnation!=
                       p.request_storage_incarnation OR
                     j.scene_pack_sha256!=p.scene_pack_sha256 OR
                     j.render_plan_sha256!=p.render_plan_sha256 OR
                     j.renderer_release_sha256!=p.renderer_release_sha256 OR
                     j.prompt_id!=p.prompt_id OR
                     (p.state='publish_intent' AND
                      j.state!='submit_intent') OR
                     (p.state='live' AND
                      j.state NOT IN (
                        'submit_intent','submission_uncertain'
                      )) OR
                     (p.state IN ('delete_pending','deleted') AND
                      j.state='submission_uncertain') OR
                     (p.cancelled_at IS NOT NULL AND
                      j.cancelled_at!=p.cancelled_at)
               LIMIT 1"""
        ).fetchone()
        if invalid_request_publication_job is not None:
            raise StoreConfigurationError()
        job_without_request_publication = connection.execute(
            """SELECT 1 FROM scene_art_jobs AS j
               LEFT JOIN scene_art_request_publications AS p
                 ON p.owner_principal=j.owner_principal AND p.job_id=j.job_id
               WHERE p.job_id IS NULL LIMIT 1"""
        ).fetchone()
        if job_without_request_publication is not None:
            raise StoreConfigurationError()
        invalid_materialization_job = connection.execute(
            """SELECT 1 FROM scene_art_output_materializations AS m
               JOIN scene_art_jobs AS j
                 ON j.owner_principal=m.owner_principal
                AND j.job_id=m.job_id
               WHERE j.state NOT IN ('running','cancelled','failed')
                  OR j.output_version IS NOT NULL
               LIMIT 1"""
        ).fetchone()
        if invalid_materialization_job is not None:
            raise StoreConfigurationError()
        inconsistent_materialization = connection.execute(
            """SELECT 1 FROM scene_art_output_materializations AS a
               JOIN scene_art_output_materializations AS b
                 ON b.owner_principal=a.owner_principal
                AND b.output_store_id=a.output_store_id
                AND b.output_sha256=a.output_sha256
               WHERE a.output_size!=b.output_size
                  OR a.output_mime!=b.output_mime
                  OR a.output_cas_key!=b.output_cas_key
               LIMIT 1"""
        ).fetchone()
        if inconsistent_materialization is not None:
            raise StoreConfigurationError()
        inconsistent_materialized_object = connection.execute(
            """SELECT 1 FROM scene_art_output_materializations AS m
               JOIN scene_art_output_objects AS o
                 ON o.owner_principal=m.owner_principal
                AND o.output_store_id=m.output_store_id
                AND o.output_sha256=m.output_sha256
               WHERE m.output_size!=o.output_size
                  OR m.output_mime!=o.output_mime
                  OR m.output_cas_key!=o.output_cas_key
               LIMIT 1"""
        ).fetchone()
        if inconsistent_materialized_object is not None:
            raise StoreConfigurationError()
        invalid_reference = connection.execute(
            """SELECT 1
               FROM scene_art_jobs AS j
               JOIN scene_art_output_objects AS o
                 ON o.owner_principal=j.owner_principal
                AND o.output_sha256=j.output_sha256
                AND o.output_storage_incarnation=
                    j.output_storage_incarnation
               WHERE j.state='output_adopted' AND o.state!='live'
               LIMIT 1"""
        ).fetchone()
        if invalid_reference is not None:
            raise StoreConfigurationError()
        unreferenced_live_object = connection.execute(
            """SELECT 1 FROM scene_art_output_objects AS o
               WHERE o.state='live' AND NOT EXISTS (
                 SELECT 1 FROM scene_art_jobs AS j
                 WHERE j.owner_principal=o.owner_principal
                   AND j.output_sha256=o.output_sha256
                   AND j.output_storage_incarnation=
                       o.output_storage_incarnation
                   AND j.state='output_adopted'
               ) AND NOT EXISTS (
                 SELECT 1 FROM scene_art_output_materializations AS m
                 WHERE m.owner_principal=o.owner_principal
                   AND m.output_store_id=o.output_store_id
                   AND m.output_sha256=o.output_sha256
               ) LIMIT 1"""
        ).fetchone()
        if unreferenced_live_object is not None:
            raise StoreConfigurationError()
        deletion_racing_hold = connection.execute(
            """SELECT 1 FROM scene_art_output_objects AS o
               JOIN scene_art_output_materializations AS m
                 ON m.owner_principal=o.owner_principal
                AND m.output_store_id=o.output_store_id
                AND m.output_sha256=o.output_sha256
               WHERE o.state='delete_pending' LIMIT 1"""
        ).fetchone()
        if deletion_racing_hold is not None:
            raise StoreConfigurationError()
        missing_ack = connection.execute(
            """SELECT 1 FROM scene_art_jobs AS j
               WHERE j.state='output_acknowledged' AND NOT EXISTS (
                 SELECT 1 FROM scene_art_output_tombstones AS t
                 WHERE t.owner_principal=j.owner_principal
                   AND t.job_id=j.job_id
                   AND t.output_sha256=j.output_sha256
                   AND t.output_version=j.output_version
               ) LIMIT 1"""
        ).fetchone()
        if missing_ack is not None:
            raise StoreConfigurationError()

    @staticmethod
    def _job(row: sqlite3.Row) -> SceneArtJob:
        value = dict(row)
        value["reconcile_required"] = bool(value["reconcile_required"])
        return SceneArtJob(**value)

    @staticmethod
    def _tombstone(row: sqlite3.Row) -> OutputTombstone:
        return OutputTombstone(**dict(row))

    @staticmethod
    def _object(row: sqlite3.Row) -> OutputObject:
        return OutputObject(**dict(row))

    @staticmethod
    def _materialization(row: sqlite3.Row) -> OutputMaterialization:
        return OutputMaterialization(**dict(row))

    @staticmethod
    def _request_publication(row: sqlite3.Row) -> RequestPublication:
        return RequestPublication(**dict(row))

    @staticmethod
    def _request_vault_binding(
        connection: sqlite3.Connection,
        owner: str,
        request_vault_id: str,
        *,
        create: bool,
        now: int,
    ) -> str:
        selected_vault_id = _digest(request_vault_id)
        row = connection.execute(
            """SELECT request_vault_id
               FROM scene_art_request_vault_bindings
               WHERE owner_principal=?""",
            (owner,),
        ).fetchone()
        if row is None and create:
            connection.execute(
                """INSERT INTO scene_art_request_vault_bindings(
                     owner_principal,request_vault_id,bound_at
                   ) VALUES (?,?,?)""",
                (owner, selected_vault_id, now),
            )
            return selected_vault_id
        if row is None or row["request_vault_id"] != selected_vault_id:
            raise SceneArtRequestVaultConflict()
        return selected_vault_id

    def bind_request_vault(
        self, context: AuthContext, *, request_vault_id: str
    ) -> str:
        """Bind an owner to the adapter-validated request-vault identity.

        ``request_vault_id`` is trusted controller configuration obtained from
        the opened vault. It must never be copied from an HTTP request body.
        """

        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        selected_vault_id = _digest(request_vault_id)
        now = _now(self._clock)
        with self._transaction() as connection:
            return self._request_vault_binding(
                connection,
                owner,
                selected_vault_id,
                create=True,
                now=now,
            )

    @staticmethod
    def _job_request_tuple(row: sqlite3.Row | SceneArtJob) -> tuple[object, ...]:
        return tuple(
            getattr(row, field) if type(row) is SceneArtJob else row[field]
            for field in (
                "job_id",
                "request_id",
                "generation_key",
                "canonical_request_sha256",
                "canonical_request_size",
                "request_vault_id",
                "scene_pack_sha256",
                "render_plan_sha256",
                "renderer_release_sha256",
                "prompt_id",
            )
        )

    def _sidecar_bytes(self, suffix: str) -> int:
        selected = Path(os.fspath(self.database_path) + suffix)
        try:
            info = selected.lstat()
        except FileNotFoundError:
            return 0
        except OSError as error:
            raise StoreConfigurationError() from error
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise StoreConfigurationError()
        return info.st_size

    def _admission_count(
        self,
        connection: sqlite3.Connection,
        query: str,
        parameters: tuple = (),
    ) -> int:
        row = connection.execute(query, parameters).fetchone()
        value = row[0] if row is not None else None
        if type(value) is not int or value < 0:
            raise StoreConfigurationError()
        return value

    def _admission_charged_sizes(
        self,
        connection: sqlite3.Connection,
        query: str,
    ) -> tuple[int, ...]:
        sizes = []
        for row in connection.execute(query).fetchall():
            selected = row[0]
            if type(selected) is not int or selected < 0:
                raise StoreConfigurationError()
            sizes.append(selected)
        return tuple(sizes)

    def _admit_new_job(
        self,
        connection: sqlite3.Connection,
        *,
        now: int,
        canonical_request_size: int,
    ) -> None:
        """Reject a new-job admission that would exceed a declared bound.

        Runs inside the BEGIN IMMEDIATE transaction, only on create paths and
        only after replay short-circuits and conflict checks, so exact
        idempotent replays never consume rate or quota and drain paths are
        never capacity-blocked.
        """

        capacity = self._capacity
        if (
            self._sidecar_bytes("-wal") > capacity.metadata_max_wal_bytes
            or self._sidecar_bytes("-shm") > capacity.metadata_max_shm_bytes
        ):
            raise SceneArtStorageCapacityExhausted()
        page_count = self._admission_count(connection, "PRAGMA page_count")
        if page_count >= capacity.metadata_max_page_count:
            raise SceneArtStorageCapacityExhausted()
        row_bounds = (
            ("scene_art_jobs", capacity.metadata_max_job_rows),
            (
                "scene_art_request_publications",
                capacity.metadata_max_request_publication_rows,
            ),
            (
                "scene_art_output_objects",
                capacity.metadata_max_output_object_rows,
            ),
            (
                "scene_art_output_materializations",
                capacity.metadata_max_output_materialization_rows,
            ),
            (
                "scene_art_output_tombstones",
                capacity.metadata_max_output_tombstone_rows,
            ),
        )
        for table, bound in row_bounds:
            retained = self._admission_count(
                connection, f"SELECT COUNT(*) FROM {table}"
            )
            if retained >= bound:
                raise SceneArtStorageCapacityExhausted()
        pending = self._admission_count(
            connection,
            """SELECT COUNT(*) FROM scene_art_jobs
               WHERE state IN ('submit_intent','submitted','queued',
                               'running','submission_uncertain')""",
        )
        if pending >= capacity.max_pending_jobs:
            raise SceneArtStorageCapacityExhausted()
        recent = self._admission_count(
            connection,
            "SELECT COUNT(*) FROM scene_art_jobs WHERE created_at>?",
            (now - capacity.rate_window_seconds,),
        )
        if recent >= capacity.max_new_jobs_per_window:
            raise SceneArtStorageCapacityExhausted()
        admitting_record = REQUEST_RECORD_OVERHEAD_BYTES + canonical_request_size
        if admitting_record > capacity.request_max_record_bytes:
            raise SceneArtStorageCapacityExhausted()
        request_sizes = self._admission_charged_sizes(
            connection,
            """SELECT canonical_request_size
               FROM scene_art_request_publications
               WHERE state IN ('publish_intent','live','delete_pending')""",
        )
        charged_records = tuple(
            REQUEST_RECORD_OVERHEAD_BYTES + selected
            for selected in request_sizes
        )
        if (
            len(charged_records) + 1 > capacity.request_max_charged_objects
            or sum(charged_records) + admitting_record
            > capacity.request_max_charged_logical_bytes
            or sum(_allocated_bytes(selected) for selected in charged_records)
            + _allocated_bytes(admitting_record)
            > capacity.request_max_charged_allocated_bytes
        ):
            raise SceneArtStorageCapacityExhausted()
        pending_publications = self._admission_count(
            connection,
            """SELECT COUNT(*) FROM scene_art_request_publications
               WHERE state='publish_intent'""",
        )
        if pending_publications >= capacity.request_max_pending_publications:
            raise SceneArtStorageCapacityExhausted()
        output_sizes = self._admission_charged_sizes(
            connection,
            """SELECT output_size FROM scene_art_output_objects
               WHERE state IN ('live','delete_pending')""",
        )
        reserving = pending + 1
        worst_logical = (
            capacity.output_max_png_bytes + capacity.output_max_manifest_bytes
        )
        worst_allocated = _allocated_bytes(
            capacity.output_max_png_bytes
        ) + _allocated_bytes(capacity.output_max_manifest_bytes)
        adopted = self._admission_count(
            connection,
            """SELECT COUNT(*) FROM scene_art_jobs
               WHERE state='output_adopted'""",
        )
        if (
            len(output_sizes) + reserving > capacity.output_max_charged_objects
            or sum(
                selected + capacity.output_max_manifest_bytes
                for selected in output_sizes
            )
            + reserving * worst_logical
            > capacity.output_max_charged_logical_bytes
            or sum(
                _allocated_bytes(selected)
                + _allocated_bytes(capacity.output_max_manifest_bytes)
                for selected in output_sizes
            )
            + reserving * worst_allocated
            > capacity.output_max_charged_allocated_bytes
            or adopted + reserving
            > capacity.output_max_unacknowledged_job_references
        ):
            raise SceneArtStorageCapacityExhausted()

    def begin_request_publication(
        self,
        context: AuthContext,
        *,
        job_id: str,
        publication_id: str,
        request_id: str,
        generation_key: str,
        canonical_request_sha256: str,
        canonical_request_size: int,
        request_vault_id: str,
        scene_pack_sha256: str,
        render_plan_sha256: str,
        renderer_release_sha256: str,
    ) -> RequestPublicationBeginResult:
        """Create the job and its exact vault object intent atomically."""

        owner, selected_job = _internal_job_id(context, job_id)
        selected_publication = _request_publication_id(publication_id)
        selected_request = _identifier(request_id)
        generation = _digest(generation_key)
        request_digest = _digest(canonical_request_sha256)
        request_size = _canonical_request_size(canonical_request_size)
        vault_id = _digest(request_vault_id)
        scene_pack_digest = _digest(scene_pack_sha256)
        render_plan_digest = _digest(render_plan_sha256)
        release_digest = _digest(renderer_release_sha256)
        prompt_id = deterministic_prompt_id_fields(
            job_id=selected_job,
            request_id=selected_request,
            generation_key=generation,
            render_plan_hash=render_plan_digest,
            renderer_release_digest=release_digest,
        )
        requested_job = (
            selected_job,
            selected_request,
            generation,
            request_digest,
            request_size,
            vault_id,
            scene_pack_digest,
            render_plan_digest,
            release_digest,
            prompt_id,
        )
        requested_publication = (
            owner,
            selected_job,
            selected_publication,
            selected_request,
            generation,
            request_digest,
            request_size,
            vault_id,
            scene_pack_digest,
            render_plan_digest,
            release_digest,
            prompt_id,
        )
        now = _now(self._clock)
        with self._transaction() as connection:
            existing = connection.execute(
                f"""SELECT {_JOB_COLUMNS} FROM scene_art_jobs
                    WHERE owner_principal=? AND generation_key=?""",
                (owner, generation),
            ).fetchone()
            if existing is not None:
                if self._job_request_tuple(existing) != requested_job:
                    raise SceneArtIdempotencyConflict()
                ledger_row = connection.execute(
                    f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                        FROM scene_art_request_publications
                        WHERE owner_principal=? AND job_id=?""",
                    (owner, selected_job),
                ).fetchone()
                if ledger_row is None:
                    raise SceneArtRequestPublicationConflict()
                ledger = self._request_publication(ledger_row)
                ledger_request = (
                    ledger.owner_principal,
                    ledger.job_id,
                    ledger.publication_id,
                    ledger.request_id,
                    ledger.generation_key,
                    ledger.canonical_request_sha256,
                    ledger.canonical_request_size,
                    ledger.request_vault_id,
                    ledger.scene_pack_sha256,
                    ledger.render_plan_sha256,
                    ledger.renderer_release_sha256,
                    ledger.prompt_id,
                )
                if (
                    ledger_request != requested_publication
                    or ledger.request_storage_incarnation
                    != existing["request_storage_incarnation"]
                ):
                    raise SceneArtRequestPublicationConflict()
                if ledger.state in {"delete_pending", "deleted"}:
                    raise SceneArtStateConflict()
                self._request_vault_binding(
                    connection, owner, vault_id, create=False, now=now
                )
                return RequestPublicationBeginResult(
                    publication=ledger,
                    job=self._job(existing),
                    created=False,
                )

            if connection.execute(
                """SELECT 1 FROM scene_art_jobs
                   WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone() is not None:
                raise SceneArtJobConflict()
            if connection.execute(
                """SELECT 1 FROM scene_art_request_publications
                   WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone() is not None:
                raise SceneArtJobConflict()

            self._admit_new_job(
                connection, now=now, canonical_request_size=request_size
            )
            self._request_vault_binding(
                connection, owner, vault_id, create=True, now=now
            )
            reserved_incarnation = "reqinc_" + secrets.token_hex(16)
            try:
                connection.execute(
                    """INSERT INTO scene_art_jobs(
                         owner_principal,job_id,request_id,generation_key,
                         canonical_request_sha256,canonical_request_size,
                         request_vault_id,request_storage_incarnation,
                         scene_pack_sha256,render_plan_sha256,
                         renderer_release_sha256,prompt_id,state,created_at,
                         updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'submit_intent',?,?)""",
                    (
                        owner,
                        selected_job,
                        selected_request,
                        generation,
                        request_digest,
                        request_size,
                        vault_id,
                        reserved_incarnation,
                        scene_pack_digest,
                        render_plan_digest,
                        release_digest,
                        prompt_id,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO scene_art_request_publications(
                         owner_principal,job_id,publication_id,request_id,
                         generation_key,canonical_request_sha256,
                         canonical_request_size,request_vault_id,
                         request_storage_incarnation,scene_pack_sha256,
                         render_plan_sha256,renderer_release_sha256,prompt_id,
                         state,cancelled_at,published_at,delete_pending_at,
                         deleted_at,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'publish_intent',
                                 NULL,NULL,NULL,NULL,?,?)""",
                    (
                        *requested_publication[:8],
                        reserved_incarnation,
                        *requested_publication[8:],
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise SceneArtRequestPublicationConflict() from error
            row = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone()
            if row is None:
                raise SceneArtRequestPublicationConflict()
            job_row = self._owned_row(connection, owner, selected_job)
            return RequestPublicationBeginResult(
                publication=self._request_publication(row),
                job=self._job(job_row),
                created=True,
            )

    def get_request_publication(
        self, context: AuthContext, *, job_id: str
    ) -> RequestPublication:
        owner, selected_job = _internal_job_id(context, job_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone()
        if row is None:
            raise SceneArtResourceNotFound()
        return self._request_publication(row)

    def list_request_publications(
        self,
        context: ServicePrincipal,
        *,
        request_vault_id: str,
        limit: int = MAX_LIST_RESULTS,
    ) -> tuple[RequestPublication, ...]:
        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        vault_id = _digest(request_vault_id)
        selected_limit = _limit(limit)
        with closing(self._connect()) as connection:
            self._request_vault_binding(
                connection,
                owner,
                vault_id,
                create=False,
                now=_now(self._clock),
            )
            rows = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND request_vault_id=?
                      AND state='publish_intent'
                    ORDER BY created_at,job_id LIMIT ?""",
                (owner, vault_id, selected_limit),
            ).fetchall()
        return tuple(self._request_publication(row) for row in rows)

    def finish_request_publication(
        self,
        context: ServicePrincipal,
        *,
        publication: RequestPublication,
    ) -> RequestPublicationFinishResult:
        """Mark one exact DB-reserved request object live, idempotently."""

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        supplied = _request_publication_projection(publication)
        if supplied.owner_principal != owner:
            raise SceneArtResourceNotFound()
        supplied_immutable = _request_publication_immutable_tuple(supplied)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=?""",
                (owner, supplied.job_id),
            ).fetchone()
            if row is None:
                raise SceneArtResourceNotFound()
            retained = self._request_publication(row)
            if (
                _request_publication_immutable_tuple(retained)
                != supplied_immutable
            ):
                raise SceneArtRequestPublicationConflict()
            self._request_vault_binding(
                connection,
                owner,
                retained.request_vault_id,
                create=False,
                now=now,
            )
            job_row = self._owned_row(connection, owner, retained.job_id)
            if (
                self._job_request_tuple(job_row)
                != (
                    retained.job_id,
                    retained.request_id,
                    retained.generation_key,
                    retained.canonical_request_sha256,
                    retained.canonical_request_size,
                    retained.request_vault_id,
                    retained.scene_pack_sha256,
                    retained.render_plan_sha256,
                    retained.renderer_release_sha256,
                    retained.prompt_id,
                )
                or job_row["request_storage_incarnation"]
                != retained.request_storage_incarnation
            ):
                raise SceneArtRequestPublicationConflict()
            if retained.state == "live":
                return RequestPublicationFinishResult(
                    publication=retained,
                    job=self._job(job_row),
                    created=False,
                )
            if (
                retained.state != "publish_intent"
                or job_row["state"] != SUBMIT_INTENT
                or job_row["dispatch_started_at"] is not None
            ):
                raise SceneArtStateConflict()
            changed = connection.execute(
                """UPDATE scene_art_request_publications
                   SET state='live',published_at=?,updated_at=?
                   WHERE owner_principal=? AND job_id=? AND publication_id=?
                     AND state='publish_intent'""",
                (now, now, owner, retained.job_id, retained.publication_id),
            ).rowcount
            if changed != 1:
                raise SceneArtRequestPublicationConflict()
            job_row = self._owned_row(connection, owner, retained.job_id)
            completed_row = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=? AND state='live'""",
                (owner, retained.job_id),
            ).fetchone()
            if completed_row is None:
                raise SceneArtRequestPublicationConflict()
            return RequestPublicationFinishResult(
                publication=self._request_publication(completed_row),
                job=self._job(job_row),
                created=True,
            )

    def list_pending_request_deletions(
        self,
        context: ServicePrincipal,
        *,
        request_vault_id: str,
        limit: int = MAX_LIST_RESULTS,
    ) -> tuple[PendingRequestDeletion, ...]:
        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        vault_id = _digest(request_vault_id)
        selected_limit = _limit(limit)
        with closing(self._connect()) as connection:
            self._request_vault_binding(
                connection,
                owner,
                vault_id,
                create=False,
                now=_now(self._clock),
            )
            rows = connection.execute(
                """SELECT owner_principal,job_id,publication_id,
                          request_vault_id,generation_key,
                          canonical_request_sha256,canonical_request_size,
                          request_storage_incarnation
                   FROM scene_art_request_publications
                   WHERE owner_principal=? AND request_vault_id=?
                     AND state='delete_pending'
                   ORDER BY delete_pending_at,job_id LIMIT ?""",
                (owner, vault_id, selected_limit),
            ).fetchall()
        return tuple(PendingRequestDeletion(**dict(row)) for row in rows)

    def get_pending_request_deletion(
        self, context: ServicePrincipal, *, job_id: str
    ) -> PendingRequestDeletion:
        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner, selected_job = _internal_job_id(context, job_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT owner_principal,job_id,publication_id,
                          request_vault_id,generation_key,
                          canonical_request_sha256,canonical_request_size,
                          request_storage_incarnation
                   FROM scene_art_request_publications
                   WHERE owner_principal=? AND job_id=?
                     AND state='delete_pending'""",
                (owner, selected_job),
            ).fetchone()
        if row is None:
            raise SceneArtResourceNotFound()
        return PendingRequestDeletion(**dict(row))

    def finalize_request_deletion(
        self,
        context: ServicePrincipal,
        *,
        deletion: PendingRequestDeletion,
    ) -> RequestPublication:
        """Persist exact physical absence after vault-session reconciliation."""

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        selected = _pending_request_deletion_projection(deletion)
        if selected.owner_principal != owner:
            raise SceneArtResourceNotFound()
        now = _now(self._clock)
        with self._transaction() as connection:
            row = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=?""",
                (owner, selected.job_id),
            ).fetchone()
            if row is None:
                raise SceneArtResourceNotFound()
            retained = self._request_publication(row)
            exact = (
                retained.owner_principal,
                retained.job_id,
                retained.publication_id,
                retained.request_vault_id,
                retained.generation_key,
                retained.canonical_request_sha256,
                retained.canonical_request_size,
                retained.request_storage_incarnation,
            )
            supplied = (
                selected.owner_principal,
                selected.job_id,
                selected.publication_id,
                selected.request_vault_id,
                selected.generation_key,
                selected.canonical_request_sha256,
                selected.canonical_request_size,
                selected.request_storage_incarnation,
            )
            if exact != supplied:
                raise SceneArtRequestPublicationConflict()
            if retained.state == "deleted":
                return retained
            if retained.state != "delete_pending":
                raise SceneArtStateConflict()
            changed = connection.execute(
                """UPDATE scene_art_request_publications
                   SET state='deleted',deleted_at=?,updated_at=?
                   WHERE owner_principal=? AND job_id=?
                     AND publication_id=? AND state='delete_pending'""",
                (
                    now,
                    now,
                    owner,
                    retained.job_id,
                    retained.publication_id,
                ),
            ).rowcount
            if changed != 1:
                raise SceneArtRequestPublicationConflict()
            refreshed = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=?""",
                (owner, retained.job_id),
            ).fetchone()
            if refreshed is None:
                raise SceneArtRequestPublicationConflict()
            return self._request_publication(refreshed)

    @staticmethod
    def _binding_store_id(
        connection: sqlite3.Connection,
        owner: str,
        output_store_id: str,
        *,
        create: bool,
        now: int,
    ) -> str:
        selected_store_id = _digest(output_store_id)
        row = connection.execute(
            """SELECT output_store_id FROM scene_art_output_store_bindings
               WHERE owner_principal=?""",
            (owner,),
        ).fetchone()
        if row is None and create:
            connection.execute(
                """INSERT INTO scene_art_output_store_bindings(
                     owner_principal,output_store_id,bound_at
                   ) VALUES (?,?,?)""",
                (owner, selected_store_id, now),
            )
            return selected_store_id
        if row is None or row["output_store_id"] != selected_store_id:
            raise SceneArtOutputStoreConflict()
        return selected_store_id

    @staticmethod
    def _assert_output_store_objects_bound(
        connection: sqlite3.Connection,
        owner: str,
        output_store_id: str,
    ) -> None:
        drift = connection.execute(
            """SELECT 1 FROM scene_art_output_objects
               WHERE owner_principal=? AND output_store_id!=? LIMIT 1""",
            (owner, output_store_id),
        ).fetchone()
        if drift is not None:
            raise SceneArtOutputStoreConflict()
        materialization_drift = connection.execute(
            """SELECT 1 FROM scene_art_output_materializations
               WHERE owner_principal=? AND output_store_id!=? LIMIT 1""",
            (owner, output_store_id),
        ).fetchone()
        if materialization_drift is not None:
            raise SceneArtOutputStoreConflict()

    def bind_output_store(
        self, context: AuthContext, *, output_store_id: str
    ) -> str:
        """Bind an owner partition permanently to one private CAS identity."""

        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        selected_store_id = _digest(output_store_id)
        now = _now(self._clock)
        with self._transaction() as connection:
            bound = self._binding_store_id(
                connection,
                owner,
                selected_store_id,
                create=True,
                now=now,
            )
            self._assert_output_store_objects_bound(
                connection, owner, selected_store_id
            )
            return bound

    @staticmethod
    def _owned_row(
        connection: sqlite3.Connection, owner: str, job_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            f"""SELECT {_JOB_COLUMNS} FROM scene_art_jobs
                WHERE owner_principal=? AND job_id=?""",
            (owner, job_id),
        ).fetchone()
        if row is None:
            raise SceneArtResourceNotFound()
        return row

    @staticmethod
    def _mark_request_delete_pending(
        connection: sqlite3.Connection,
        owner: str,
        job_id: str,
        *,
        now: int,
        cancelled_at: int | None = None,
    ) -> None:
        row = connection.execute(
            """SELECT state,cancelled_at
               FROM scene_art_request_publications
               WHERE owner_principal=? AND job_id=?""",
            (owner, job_id),
        ).fetchone()
        if row is None:
            return
        if row["state"] in {"delete_pending", "deleted"}:
            if cancelled_at is not None:
                if row["cancelled_at"] is None:
                    changed = connection.execute(
                        """UPDATE scene_art_request_publications
                           SET cancelled_at=?,updated_at=?
                           WHERE owner_principal=? AND job_id=?
                             AND cancelled_at IS NULL""",
                        (cancelled_at, now, owner, job_id),
                    ).rowcount
                    if changed != 1:
                        raise SceneArtRequestPublicationConflict()
                elif row["cancelled_at"] != cancelled_at:
                    raise SceneArtRequestPublicationConflict()
            return
        if row["state"] not in {"publish_intent", "live"}:
            raise SceneArtRequestPublicationConflict()
        selected_cancelled = row["cancelled_at"]
        if cancelled_at is not None:
            if selected_cancelled not in {None, cancelled_at}:
                raise SceneArtRequestPublicationConflict()
            selected_cancelled = cancelled_at
        changed = connection.execute(
            """UPDATE scene_art_request_publications
               SET state='delete_pending',cancelled_at=?,
                   delete_pending_at=?,updated_at=?
               WHERE owner_principal=? AND job_id=? AND state=?""",
            (
                selected_cancelled,
                now,
                now,
                owner,
                job_id,
                row["state"],
            ),
        ).rowcount
        if changed != 1:
            raise SceneArtRequestPublicationConflict()

    @staticmethod
    def _insert_compatibility_request_object(
        connection: sqlite3.Connection,
        *,
        owner: str,
        job_id: str,
        publication_id: str,
        request_id: str,
        generation_key: str,
        request_sha256: str,
        request_size: int,
        vault_id: str,
        request_incarnation: str,
        scene_pack_sha256: str,
        render_plan_sha256: str,
        release_sha256: str,
        prompt_id: str,
        now: int,
    ) -> None:
        connection.execute(
            """INSERT INTO scene_art_request_publications(
                 owner_principal,job_id,publication_id,request_id,
                 generation_key,canonical_request_sha256,
                 canonical_request_size,request_vault_id,
                 request_storage_incarnation,scene_pack_sha256,
                 render_plan_sha256,renderer_release_sha256,prompt_id,
                 state,cancelled_at,published_at,delete_pending_at,
                 deleted_at,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'live',NULL,?,
                         NULL,NULL,?,?)""",
            (
                owner,
                job_id,
                publication_id,
                request_id,
                generation_key,
                request_sha256,
                request_size,
                vault_id,
                request_incarnation,
                scene_pack_sha256,
                render_plan_sha256,
                release_sha256,
                prompt_id,
                now,
                now,
                now,
            ),
        )

    def create_submit_intent(
        self,
        context: AuthContext,
        *,
        job_id: str,
        request_id: str,
        generation_key: str,
        canonical_request_sha256: str,
        canonical_request_size: int,
        request_vault_id: str,
        request_storage_incarnation: str,
        scene_pack_sha256: str,
        render_plan_sha256: str,
        renderer_release_sha256: str,
    ) -> SubmitIntentResult:
        """Compatibility seam for already-published trusted request objects.

        The platform adapter must use ``begin_request_publication`` instead;
        only that path reserves an incarnation before vault publication.
        """

        owner, selected_job = _internal_job_id(context, job_id)
        selected_request = _identifier(request_id)
        generation = _digest(generation_key)
        request_digest = _digest(canonical_request_sha256)
        request_size = _canonical_request_size(canonical_request_size)
        vault_id = _digest(request_vault_id)
        request_incarnation = _request_storage_incarnation(
            request_storage_incarnation
        )
        scene_pack_digest = _digest(scene_pack_sha256)
        render_plan_digest = _digest(render_plan_sha256)
        release_digest = _digest(renderer_release_sha256)
        prompt_id = deterministic_prompt_id_fields(
            job_id=selected_job,
            request_id=selected_request,
            generation_key=generation,
            render_plan_hash=render_plan_digest,
            renderer_release_digest=release_digest,
        )
        compatibility_publication_id = _compatibility_request_publication_id(
            owner,
            selected_job,
            selected_request,
            generation,
            request_digest,
            vault_id,
            request_incarnation,
            scene_pack_digest,
            render_plan_digest,
            release_digest,
        )
        now = _now(self._clock)
        with self._transaction() as connection:
            pending_publication = connection.execute(
                """SELECT 1 FROM scene_art_request_publications
                   WHERE owner_principal=? AND state='publish_intent' AND
                         (job_id=? OR generation_key=? OR prompt_id=?)
                   LIMIT 1""",
                (owner, selected_job, generation, prompt_id),
            ).fetchone()
            if pending_publication is not None:
                raise SceneArtRequestPublicationConflict()
            existing = connection.execute(
                f"""SELECT {_JOB_COLUMNS} FROM scene_art_jobs
                    WHERE owner_principal=? AND generation_key=?""",
                (owner, generation),
            ).fetchone()
            if existing is not None:
                replay_binding = (
                    selected_job,
                    selected_request,
                    request_digest,
                    request_size,
                    vault_id,
                    request_incarnation,
                    scene_pack_digest,
                    render_plan_digest,
                    release_digest,
                    prompt_id,
                )
                retained_binding = tuple(
                    existing[key]
                    for key in (
                        "job_id",
                        "request_id",
                        "canonical_request_sha256",
                        "canonical_request_size",
                        "request_vault_id",
                        "request_storage_incarnation",
                        "scene_pack_sha256",
                        "render_plan_sha256",
                        "renderer_release_sha256",
                        "prompt_id",
                    )
                )
                if retained_binding != replay_binding:
                    raise SceneArtIdempotencyConflict()
                self._request_vault_binding(
                    connection,
                    owner,
                    vault_id,
                    create=False,
                    now=now,
                )
                return SubmitIntentResult(self._job(existing), False)
            conflicting_job = connection.execute(
                """SELECT canonical_request_sha256 FROM scene_art_jobs
                   WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone()
            if conflicting_job is not None:
                raise SceneArtJobConflict()
            self._admit_new_job(
                connection, now=now, canonical_request_size=request_size
            )
            self._request_vault_binding(
                connection,
                owner,
                vault_id,
                create=True,
                now=now,
            )
            try:
                connection.execute(
                    """INSERT INTO scene_art_jobs(
                         owner_principal,job_id,request_id,generation_key,
                         canonical_request_sha256,canonical_request_size,
                         request_vault_id,request_storage_incarnation,
                         scene_pack_sha256,render_plan_sha256,
                         renderer_release_sha256,prompt_id,state,created_at,
                         updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'submit_intent',?,?)""",
                    (
                        owner,
                        selected_job,
                        selected_request,
                        generation,
                        request_digest,
                        request_size,
                        vault_id,
                        request_incarnation,
                        scene_pack_digest,
                        render_plan_digest,
                        release_digest,
                        prompt_id,
                        now,
                        now,
                    ),
                )
                self._insert_compatibility_request_object(
                    connection,
                    owner=owner,
                    job_id=selected_job,
                    publication_id=compatibility_publication_id,
                    request_id=selected_request,
                    generation_key=generation,
                    request_sha256=request_digest,
                    request_size=request_size,
                    vault_id=vault_id,
                    request_incarnation=request_incarnation,
                    scene_pack_sha256=scene_pack_digest,
                    render_plan_sha256=render_plan_digest,
                    release_sha256=release_digest,
                    prompt_id=prompt_id,
                    now=now,
                )
            except sqlite3.IntegrityError as error:
                # BEGIN IMMEDIATE normally serializes this branch.  Keep a
                # non-sensitive fail-closed result for unusual external writers.
                raise SceneArtIdempotencyConflict() from error
            row = self._owned_row(connection, owner, selected_job)
            return SubmitIntentResult(self._job(row), True)

    def get_job(self, context: AuthorizedServiceRequest) -> SceneArtJob:
        owner, job_id = _route_job_id(context, _READ_SCOPE)
        with closing(self._connect()) as connection:
            row = self._owned_row(connection, owner, job_id)
        return self._job(row)

    def begin_dispatch(self, context: AuthContext, *, job_id: str) -> bool:
        """Return true exactly once; false always forbids another submission."""

        owner, selected_job = _internal_job_id(context, job_id)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, selected_job)
            if row["state"] != SUBMIT_INTENT or row["dispatch_started_at"] is not None:
                return False
            request_object = connection.execute(
                """SELECT state FROM scene_art_request_publications
                   WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone()
            if request_object is None or request_object["state"] != "live":
                return False
            active = self._admission_count(
                connection,
                """SELECT COUNT(*) FROM scene_art_jobs
                   WHERE state IN ('running','submission_uncertain')""",
            )
            if active >= self._capacity.max_active_jobs:
                return False
            changed = connection.execute(
                """UPDATE scene_art_jobs
                   SET dispatch_started_at=?,updated_at=?
                   WHERE owner_principal=? AND job_id=?
                     AND state='submit_intent' AND dispatch_started_at IS NULL""",
                (now, now, owner, selected_job),
            ).rowcount
            return changed == 1

    def mark_submission_uncertain(
        self,
        context: AuthContext,
        *,
        job_id: str,
        error_code: str,
    ) -> SceneArtJob:
        owner, selected_job = _internal_job_id(context, job_id)
        error = _error_code(error_code)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, selected_job)
            if row["state"] == SUBMISSION_UNCERTAIN:
                if row["last_error_code"] != error:
                    raise SceneArtStateConflict()
                return self._job(row)
            if (
                (row["state"], SUBMISSION_UNCERTAIN) not in ALLOWED_TRANSITIONS
                or row["dispatch_started_at"] is None
            ):
                raise SceneArtStateConflict()
            connection.execute(
                """UPDATE scene_art_jobs
                   SET state='submission_uncertain',submission_uncertain_at=?,
                       reconcile_required=1,last_error_code=?,updated_at=?
                   WHERE owner_principal=? AND job_id=? AND state='submit_intent'""",
                (now, error, now, owner, selected_job),
            )
            return self._job(self._owned_row(connection, owner, selected_job))

    def recover_interrupted_submissions(
        self, context: AuthContext, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[SceneArtJob, ...]:
        """Fence crashed post-intent dispatches as uncertain, never resubmit."""

        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        selected_limit = _limit(limit)
        now = _now(self._clock)
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT job_id FROM scene_art_jobs
                   WHERE owner_principal=? AND state='submit_intent'
                     AND dispatch_started_at IS NOT NULL
                   ORDER BY updated_at,job_id LIMIT ?""",
                (owner, selected_limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE scene_art_jobs
                       SET state='submission_uncertain',submission_uncertain_at=?,
                           reconcile_required=1,
                           last_error_code='submission_interrupted',updated_at=?
                       WHERE owner_principal=? AND job_id=?
                         AND state='submit_intent'
                         AND dispatch_started_at IS NOT NULL""",
                    (now, now, owner, row["job_id"]),
                )
            return tuple(
                self._job(self._owned_row(connection, owner, row["job_id"]))
                for row in rows
            )

    def list_reconciliation_required(
        self, context: AuthContext, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[SceneArtJob, ...]:
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        selected_limit = _limit(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT {_JOB_COLUMNS} FROM scene_art_jobs
                    WHERE owner_principal=? AND state='submission_uncertain'
                      AND reconcile_required=1
                    ORDER BY updated_at,job_id LIMIT ?""",
                (owner, selected_limit),
            ).fetchall()
        return tuple(self._job(row) for row in rows)

    def record_reconciliation(
        self,
        context: AuthContext,
        *,
        job_id: str,
        outcome: str,
        provider_submission_id: str | None = None,
        error_code: str | None = None,
    ) -> SceneArtJob:
        owner, selected_job = _internal_job_id(context, job_id)
        if type(outcome) is not str or outcome not in RECONCILE_OUTCOMES:
            _invalid()
        if outcome == "present":
            submission_id = _identifier(provider_submission_id)
            if error_code is not None:
                _invalid()
            terminal_error = None
        elif outcome == "terminal_failed":
            if provider_submission_id is not None:
                _invalid()
            submission_id = None
            terminal_error = _error_code(error_code)
        elif provider_submission_id is not None or error_code is not None:
            _invalid()
        else:
            submission_id = None
            terminal_error = None
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, selected_job)
            if outcome == "present" and submission_id != row["prompt_id"]:
                raise SceneArtStateConflict()
            if row["state"] != SUBMISSION_UNCERTAIN:
                raise SceneArtStateConflict()
            if outcome == "present":
                connection.execute(
                    """UPDATE scene_art_jobs
                       SET state='submitted',provider_submission_id=?,submitted_at=?,
                           reconcile_required=0,reconcile_attempts=reconcile_attempts+1,
                           last_reconciled_at=?,last_reconcile_outcome='present',
                           updated_at=?
                       WHERE owner_principal=? AND job_id=?
                         AND state='submission_uncertain'""",
                    (submission_id, now, now, now, owner, selected_job),
                )
                self._mark_request_delete_pending(
                    connection, owner, selected_job, now=now
                )
            elif outcome == "terminal_failed":
                connection.execute(
                    """UPDATE scene_art_jobs
                       SET state='failed',reconcile_required=0,
                           reconcile_attempts=reconcile_attempts+1,
                           last_reconciled_at=?,
                           last_reconcile_outcome='terminal_failed',
                           last_error_code=?,updated_at=?
                       WHERE owner_principal=? AND job_id=?
                         AND state='submission_uncertain'""",
                    (now, terminal_error, now, owner, selected_job),
                )
                self._mark_request_delete_pending(
                    connection, owner, selected_job, now=now
                )
            else:
                connection.execute(
                    """UPDATE scene_art_jobs
                       SET reconcile_attempts=reconcile_attempts+1,
                           last_reconciled_at=?,last_reconcile_outcome=?,updated_at=?
                       WHERE owner_principal=? AND job_id=?
                         AND state='submission_uncertain'""",
                    (now, outcome, now, owner, selected_job),
                )
            return self._job(self._owned_row(connection, owner, selected_job))

    def mark_submitted(
        self,
        context: AuthContext,
        *,
        job_id: str,
        provider_submission_id: str,
    ) -> SceneArtJob:
        owner, selected_job = _internal_job_id(context, job_id)
        submission_id = _identifier(provider_submission_id)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, selected_job)
            if submission_id != row["prompt_id"]:
                raise SceneArtStateConflict()
            if row["state"] in {
                SUBMITTED,
                QUEUED,
                RUNNING,
                OUTPUT_ADOPTED,
                OUTPUT_ACKNOWLEDGED,
            }:
                if row["provider_submission_id"] != submission_id:
                    raise SceneArtStateConflict()
                self._mark_request_delete_pending(
                    connection, owner, selected_job, now=now
                )
                return self._job(row)
            # An uncertain submission may advance only through the explicit
            # reconciliation path, which records the attempt and outcome.
            if row["state"] == SUBMISSION_UNCERTAIN:
                raise SceneArtStateConflict()
            if (row["state"], SUBMITTED) not in ALLOWED_TRANSITIONS:
                raise SceneArtStateConflict()
            if row["state"] == SUBMIT_INTENT and row["dispatch_started_at"] is None:
                raise SceneArtStateConflict()
            connection.execute(
                """UPDATE scene_art_jobs
                   SET state='submitted',provider_submission_id=?,submitted_at=?,
                       reconcile_required=0,updated_at=?
                   WHERE owner_principal=? AND job_id=? AND state=?""",
                (submission_id, now, now, owner, selected_job, row["state"]),
            )
            self._mark_request_delete_pending(
                connection, owner, selected_job, now=now
            )
            return self._job(self._owned_row(connection, owner, selected_job))

    def _simple_transition(
        self, context: AuthContext, *, job_id: str, target: str
    ) -> SceneArtJob:
        if target not in {QUEUED, RUNNING}:
            _invalid()
        owner, selected_job = _internal_job_id(context, job_id)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, selected_job)
            if row["state"] == target:
                return self._job(row)
            if (row["state"], target) not in ALLOWED_TRANSITIONS:
                raise SceneArtStateConflict()
            changed = connection.execute(
                """UPDATE scene_art_jobs SET state=?,updated_at=?
                   WHERE owner_principal=? AND job_id=? AND state=?""",
                (target, now, owner, selected_job, row["state"]),
            ).rowcount
            if changed != 1:
                raise SceneArtStateConflict()
            return self._job(self._owned_row(connection, owner, selected_job))

    def mark_queued(self, context: AuthContext, *, job_id: str) -> SceneArtJob:
        return self._simple_transition(context, job_id=job_id, target=QUEUED)

    def mark_running(self, context: AuthContext, *, job_id: str) -> SceneArtJob:
        return self._simple_transition(context, job_id=job_id, target=RUNNING)

    def mark_failed(
        self, context: AuthContext, *, job_id: str, error_code: str
    ) -> SceneArtJob:
        owner, selected_job = _internal_job_id(context, job_id)
        error = _error_code(error_code)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, selected_job)
            if row["state"] == SUBMISSION_UNCERTAIN:
                # Submission-uncertain failures must pass through the explicit
                # provider reconciliation path so absence cannot be mistaken
                # for terminal failure and its audit evidence cannot be lost.
                raise SceneArtStateConflict()
            if row["state"] == FAILED:
                if row["last_error_code"] != error:
                    raise SceneArtStateConflict()
                self._mark_request_delete_pending(
                    connection, owner, selected_job, now=now
                )
                return self._job(row)
            # Once provider dispatch has begun, any failure to observe a
            # definitive response is ambiguous. Preserve the reconciliation
            # fence instead of allowing a terminal pre-submit failure.
            if (
                row["state"] == SUBMIT_INTENT
                and row["dispatch_started_at"] is not None
            ):
                raise SceneArtStateConflict()
            if (row["state"], FAILED) not in ALLOWED_TRANSITIONS:
                raise SceneArtStateConflict()
            connection.execute(
                """UPDATE scene_art_jobs
                   SET state='failed',reconcile_required=0,last_error_code=?,updated_at=?
                   WHERE owner_principal=? AND job_id=? AND state=?""",
                (error, now, owner, selected_job, row["state"]),
            )
            self._mark_request_delete_pending(
                connection, owner, selected_job, now=now
            )
            return self._job(self._owned_row(connection, owner, selected_job))

    def cancel_job(self, context: AuthorizedServiceRequest) -> SceneArtJob:
        owner, job_id = _route_job_id(context, _CANCEL_SCOPE)
        now = _now(self._clock)
        with self._transaction() as connection:
            row = self._owned_row(connection, owner, job_id)
            if row["state"] == CANCELLED:
                return self._job(row)
            request_object = connection.execute(
                f"""SELECT {_REQUEST_PUBLICATION_COLUMNS}
                    FROM scene_art_request_publications
                    WHERE owner_principal=? AND job_id=?""",
                (owner, job_id),
            ).fetchone()
            publication_cancel = (
                row["state"] == SUBMIT_INTENT
                and row["dispatch_started_at"] is None
                and request_object is not None
                and request_object["state"] in {"publish_intent", "live"}
            )
            if (
                (row["state"], CANCELLED) not in ALLOWED_TRANSITIONS
                and not publication_cancel
            ):
                raise SceneArtStateConflict()
            connection.execute(
                """UPDATE scene_art_jobs
                   SET state='cancelled',cancelled_at=?,updated_at=?
                   WHERE owner_principal=? AND job_id=? AND state=?""",
                (now, now, owner, job_id, row["state"]),
            )
            if request_object is not None:
                self._mark_request_delete_pending(
                    connection,
                    owner,
                    job_id,
                    now=now,
                    cancelled_at=now,
                )
            return self._job(self._owned_row(connection, owner, job_id))

    @staticmethod
    def _owned_materialization_row(
        connection: sqlite3.Connection, owner: str, job_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            f"""SELECT {_MATERIALIZATION_COLUMNS}
                FROM scene_art_output_materializations
                WHERE owner_principal=? AND job_id=?""",
            (owner, job_id),
        ).fetchone()
        if row is None:
            raise SceneArtResourceNotFound()
        return row

    def _exact_materialization_row(
        self,
        connection: sqlite3.Connection,
        owner: str,
        materialization: OutputMaterialization,
    ) -> sqlite3.Row:
        if (
            type(materialization) is not OutputMaterialization
            or type(materialization.owner_principal) is not str
            or materialization.owner_principal != owner
        ):
            raise StoreAuthorizationError()
        selected_job = _identifier(materialization.job_id)
        selected_publication = _publication_id(materialization.publication_id)
        selected_store = _digest(materialization.output_store_id)
        selected_version = _output_version(materialization.output_version)
        digest = _digest(materialization.output_sha256)
        selected_size = _output_size(materialization.output_size)
        created_at = _timestamp(materialization.created_at)
        updated_at = _timestamp(materialization.updated_at)
        if (
            type(materialization.output_mime) is not str
            or type(materialization.output_cas_key) is not str
            or updated_at < created_at
            or materialization.output_mime != OUTPUT_MIME
            or materialization.output_cas_key
            != content_addressed_key(digest)
        ):
            _invalid()
        row = self._owned_materialization_row(
            connection, owner, selected_job
        )
        expected = (
            owner,
            selected_job,
            selected_publication,
            selected_store,
            selected_version,
            digest,
            selected_size,
            materialization.output_mime,
            materialization.output_cas_key,
            created_at,
            updated_at,
        )
        retained = tuple(
            row[column]
            for column in (
                "owner_principal",
                "job_id",
                "publication_id",
                "output_store_id",
                "output_version",
                "output_sha256",
                "output_size",
                "output_mime",
                "output_cas_key",
                "created_at",
                "updated_at",
            )
        )
        if retained != expected:
            raise SceneArtStateConflict()
        return row

    def begin_output_materialization(
        self,
        context: ServicePrincipal,
        *,
        job_id: str,
        publication_id: str,
        output_store_id: str,
        output_version: int,
        output_sha256: str,
        output_size: int,
        output_mime: str,
        output_cas_key: str,
    ) -> OutputMaterializationBeginResult:
        """Create the durable digest hold before any private-CAS publish."""

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner, selected_job = _internal_job_id(context, job_id)
        publication = _publication_id(publication_id)
        store_id = _digest(output_store_id)
        version = _output_version(output_version)
        digest = _digest(output_sha256)
        size = _output_size(output_size)
        if (
            type(output_mime) is not str
            or type(output_cas_key) is not str
            or output_mime != OUTPUT_MIME
            or output_cas_key != content_addressed_key(digest)
        ):
            _invalid()
        binding = (
            owner,
            selected_job,
            publication,
            store_id,
            version,
            digest,
            size,
            output_mime,
            output_cas_key,
        )
        now = _now(self._clock)
        with self._transaction() as connection:
            job = self._owned_row(connection, owner, selected_job)
            if job["state"] != RUNNING:
                raise SceneArtStateConflict()
            self._binding_store_id(
                connection, owner, store_id, create=True, now=now
            )
            self._assert_output_store_objects_bound(
                connection, owner, store_id
            )
            existing = connection.execute(
                f"""SELECT {_MATERIALIZATION_COLUMNS}
                    FROM scene_art_output_materializations
                    WHERE owner_principal=? AND job_id=?""",
                (owner, selected_job),
            ).fetchone()
            if existing is not None:
                retained = self._materialization(existing)
                retained_binding = (
                    retained.owner_principal,
                    retained.job_id,
                    retained.publication_id,
                    retained.output_store_id,
                    retained.output_version,
                    retained.output_sha256,
                    retained.output_size,
                    retained.output_mime,
                    retained.output_cas_key,
                )
                if retained_binding != binding:
                    raise SceneArtStateConflict()
                return OutputMaterializationBeginResult(retained, False)
            deleting = connection.execute(
                """SELECT 1 FROM scene_art_output_objects
                   WHERE owner_principal=? AND output_store_id=?
                     AND output_sha256=? AND state='delete_pending'
                   LIMIT 1""",
                (owner, store_id, digest),
            ).fetchone()
            if deleting is not None:
                raise SceneArtStateConflict()
            inconsistent = connection.execute(
                """SELECT 1 FROM scene_art_output_materializations
                   WHERE owner_principal=? AND output_store_id=?
                     AND output_sha256=?
                     AND (output_size!=? OR output_mime!=?
                          OR output_cas_key!=?)
                   UNION ALL
                   SELECT 1 FROM scene_art_output_objects
                   WHERE owner_principal=? AND output_store_id=?
                     AND output_sha256=?
                     AND (output_size!=? OR output_mime!=?
                          OR output_cas_key!=?)
                   LIMIT 1""",
                (
                    owner,
                    store_id,
                    digest,
                    size,
                    output_mime,
                    output_cas_key,
                    owner,
                    store_id,
                    digest,
                    size,
                    output_mime,
                    output_cas_key,
                ),
            ).fetchone()
            if inconsistent is not None:
                raise SceneArtStateConflict()
            if size > self._capacity.output_max_png_bytes:
                raise SceneArtStorageCapacityExhausted()
            holds = self._admission_count(
                connection,
                "SELECT COUNT(*) FROM scene_art_output_materializations",
            )
            if holds >= self._capacity.output_max_materialization_holds:
                raise SceneArtStorageCapacityExhausted()
            try:
                connection.execute(
                    """INSERT INTO scene_art_output_materializations(
                         owner_principal,job_id,publication_id,output_store_id,
                         output_version,output_sha256,output_size,output_mime,
                         output_cas_key,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    binding + (now, now),
                )
            except sqlite3.IntegrityError as error:
                raise SceneArtStateConflict() from error
            retained = self._materialization(
                self._owned_materialization_row(
                    connection, owner, selected_job
                )
            )
            return OutputMaterializationBeginResult(retained, True)

    def get_output_materialization(
        self,
        context: ServicePrincipal,
        *,
        job_id: str,
        publication_id: str,
    ) -> OutputMaterialization:
        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner, selected_job = _internal_job_id(context, job_id)
        publication = _publication_id(publication_id)
        with closing(self._connect()) as connection:
            row = self._owned_materialization_row(
                connection, owner, selected_job
            )
            materialization = self._materialization(row)
            if materialization.publication_id != publication:
                raise SceneArtResourceNotFound()
            return materialization

    def list_output_materializations(
        self,
        context: ServicePrincipal,
        *,
        output_store_id: str,
        limit: int = MAX_LIST_RESULTS,
    ) -> tuple[OutputMaterialization, ...]:
        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        store_id = _digest(output_store_id)
        selected_limit = _limit(limit)
        now = _now(self._clock)
        with self._transaction() as connection:
            self._binding_store_id(
                connection, owner, store_id, create=False, now=now
            )
            self._assert_output_store_objects_bound(
                connection, owner, store_id
            )
            rows = connection.execute(
                f"""SELECT {_MATERIALIZATION_COLUMNS}
                    FROM scene_art_output_materializations
                    WHERE owner_principal=? AND output_store_id=?
                    ORDER BY created_at,job_id LIMIT ?""",
                (owner, store_id, selected_limit),
            ).fetchall()
            return tuple(self._materialization(row) for row in rows)

    def get_live_output_object_for_materialization(
        self,
        context: ServicePrincipal,
        *,
        materialization: OutputMaterialization,
    ) -> OutputObject | None:
        """Snapshot the exact reusable object while its digest hold is live.

        The coordinator calls this under its private-CAS publication-session
        guard. A returned object must probe as the same CAS incarnation; when
        this returns ``None`` the coordinator may publish prepared bytes.
        """

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        now = _now(self._clock)
        with self._transaction() as connection:
            self._exact_materialization_row(
                connection, owner, materialization
            )
            self._binding_store_id(
                connection,
                owner,
                materialization.output_store_id,
                create=False,
                now=now,
            )
            self._assert_output_store_objects_bound(
                connection, owner, materialization.output_store_id
            )
            rows = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS}
                    FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_store_id=?
                      AND output_sha256=? AND state!='deleted'
                    ORDER BY output_storage_incarnation""",
                (
                    owner,
                    materialization.output_store_id,
                    materialization.output_sha256,
                ),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise SceneArtStateConflict()
            retained = self._object(rows[0])
            if (
                retained.state != "live"
                or retained.output_size != materialization.output_size
                or retained.output_mime != materialization.output_mime
                or retained.output_cas_key
                != materialization.output_cas_key
            ):
                raise SceneArtStateConflict()
            return retained

    def finish_output_materialization(
        self,
        context: ServicePrincipal,
        *,
        materialization: OutputMaterialization,
        output_storage_incarnation: str,
    ) -> OutputMaterializationFinishResult:
        """Atomically exchange a digest hold for an exact CAS incarnation."""

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        incarnation = _storage_incarnation(output_storage_incarnation)
        now = _now(self._clock)
        with self._transaction() as connection:
            self._exact_materialization_row(
                connection, owner, materialization
            )
            self._binding_store_id(
                connection,
                owner,
                materialization.output_store_id,
                create=False,
                now=now,
            )
            self._assert_output_store_objects_bound(
                connection, owner, materialization.output_store_id
            )
            job_row = self._owned_row(
                connection, owner, materialization.job_id
            )
            if job_row["state"] not in {RUNNING, CANCELLED, FAILED}:
                raise SceneArtStateConflict()

            object_row = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS}
                    FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_sha256=?
                      AND output_storage_incarnation=?""",
                (
                    owner,
                    materialization.output_sha256,
                    incarnation,
                ),
            ).fetchone()
            if object_row is None:
                try:
                    connection.execute(
                        """INSERT INTO scene_art_output_objects(
                             owner_principal,output_store_id,output_sha256,
                             output_storage_incarnation,output_size,output_mime,
                             output_cas_key,state,created_at,
                             delete_pending_at,deleted_at
                           ) VALUES (?,?,?,?,?,?,?,'live',?,NULL,NULL)""",
                        (
                            owner,
                            materialization.output_store_id,
                            materialization.output_sha256,
                            incarnation,
                            materialization.output_size,
                            materialization.output_mime,
                            materialization.output_cas_key,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise SceneArtStateConflict() from error
            else:
                retained_object = self._object(object_row)
                if retained_object.output_store_id != materialization.output_store_id:
                    raise SceneArtOutputStoreConflict()
                if (
                    retained_object.state != "live"
                    or retained_object.output_size != materialization.output_size
                    or retained_object.output_mime != materialization.output_mime
                    or retained_object.output_cas_key
                    != materialization.output_cas_key
                ):
                    raise SceneArtStateConflict()

            removed = connection.execute(
                """DELETE FROM scene_art_output_materializations
                   WHERE owner_principal=? AND job_id=? AND publication_id=?""",
                (
                    owner,
                    materialization.job_id,
                    materialization.publication_id,
                ),
            ).rowcount
            if removed != 1:
                raise SceneArtStateConflict()

            if job_row["state"] == RUNNING:
                changed = connection.execute(
                    """UPDATE scene_art_jobs
                       SET state='output_adopted',output_version=?,
                           output_sha256=?,output_size=?,output_mime=?,
                           output_cas_key=?,output_storage_incarnation=?,
                           output_adopted_at=?,updated_at=?
                       WHERE owner_principal=? AND job_id=? AND state='running'""",
                    (
                        materialization.output_version,
                        materialization.output_sha256,
                        materialization.output_size,
                        materialization.output_mime,
                        materialization.output_cas_key,
                        incarnation,
                        now,
                        now,
                        owner,
                        materialization.job_id,
                    ),
                ).rowcount
                if changed != 1:
                    raise SceneArtStateConflict()
                outcome = "adopted"
            else:
                adopted_hold = connection.execute(
                    """SELECT 1 FROM scene_art_jobs
                       WHERE owner_principal=? AND output_sha256=?
                         AND output_storage_incarnation=?
                         AND state='output_adopted' LIMIT 1""",
                    (
                        owner,
                        materialization.output_sha256,
                        incarnation,
                    ),
                ).fetchone()
                publication_hold = connection.execute(
                    """SELECT 1 FROM scene_art_output_materializations
                       WHERE owner_principal=? AND output_store_id=?
                         AND output_sha256=? LIMIT 1""",
                    (
                        owner,
                        materialization.output_store_id,
                        materialization.output_sha256,
                    ),
                ).fetchone()
                if adopted_hold is None and publication_hold is None:
                    changed = connection.execute(
                        """UPDATE scene_art_output_objects
                           SET state='delete_pending',delete_pending_at=?
                           WHERE owner_principal=? AND output_sha256=?
                             AND output_storage_incarnation=? AND state='live'""",
                        (
                            now,
                            owner,
                            materialization.output_sha256,
                            incarnation,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise SceneArtStateConflict()
                    outcome = "orphan_delete_pending"
                else:
                    outcome = "orphan_held"

            job = self._job(
                self._owned_row(connection, owner, materialization.job_id)
            )
            selected_object_row = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS}
                    FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_sha256=?
                      AND output_storage_incarnation=?""",
                (
                    owner,
                    materialization.output_sha256,
                    incarnation,
                ),
            ).fetchone()
            if selected_object_row is None:
                raise SceneArtStateConflict()
            output_object = self._object(selected_object_row)
            output = self._output(job) if outcome == "adopted" else None
            if outcome not in OUTPUT_MATERIALIZATION_OUTCOMES:
                raise SceneArtStateConflict()
            return OutputMaterializationFinishResult(
                materialization,
                job,
                output_object,
                output,
                outcome,
            )

    def resolve_missing_output_materialization(
        self,
        context: ServicePrincipal,
        *,
        materialization: OutputMaterialization,
    ) -> OutputMaterializationMissingResult:
        """Resolve a session-proven absence without erasing live DB claims."""

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _SUBMIT_SCOPE)
        now = _now(self._clock)
        with self._transaction() as connection:
            self._exact_materialization_row(
                connection, owner, materialization
            )
            self._binding_store_id(
                connection,
                owner,
                materialization.output_store_id,
                create=False,
                now=now,
            )
            self._assert_output_store_objects_bound(
                connection, owner, materialization.output_store_id
            )
            job = self._owned_row(
                connection, owner, materialization.job_id
            )
            if job["state"] not in {RUNNING, CANCELLED, FAILED}:
                raise SceneArtStateConflict()
            retained_claim = connection.execute(
                """SELECT 1 FROM scene_art_output_objects
                   WHERE owner_principal=? AND output_store_id=?
                     AND output_sha256=? AND state!='deleted' LIMIT 1""",
                (
                    owner,
                    materialization.output_store_id,
                    materialization.output_sha256,
                ),
            ).fetchone()
            if retained_claim is not None:
                raise SceneArtStateConflict()
            removed = connection.execute(
                """DELETE FROM scene_art_output_materializations
                   WHERE owner_principal=? AND job_id=? AND publication_id=?""",
                (
                    owner,
                    materialization.job_id,
                    materialization.publication_id,
                ),
            ).rowcount
            if removed != 1:
                raise SceneArtStateConflict()
            job_failed = job["state"] == RUNNING
            if job_failed:
                changed = connection.execute(
                    """UPDATE scene_art_jobs
                       SET state='failed',last_error_code=
                           'output_publication_missing',updated_at=?
                       WHERE owner_principal=? AND job_id=? AND state='running'""",
                    (now, owner, materialization.job_id),
                ).rowcount
                if changed != 1:
                    raise SceneArtStateConflict()
            retained_job = self._job(
                self._owned_row(connection, owner, materialization.job_id)
            )
            return OutputMaterializationMissingResult(
                materialization, retained_job, job_failed
            )

    def adopt_output(
        self,
        context: AuthContext,
        *,
        job_id: str,
        output_version: int,
        output_sha256: str,
        output_size: int,
        output_mime: str,
        output_cas_key: str,
        output_storage_incarnation: str,
        output_store_id: str,
        publication_id: str | None = None,
    ) -> SceneArtOutput:
        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        if publication_id is None:
            raise SceneArtStateConflict()
        try:
            materialization = self.get_output_materialization(
                context,
                job_id=job_id,
                publication_id=publication_id,
            )
        except SceneArtResourceNotFound as error:
            raise SceneArtStateConflict() from error
        expected = (
            _digest(output_store_id),
            _output_version(output_version),
            _digest(output_sha256),
            _output_size(output_size),
            output_mime,
            output_cas_key,
        )
        if (
            type(output_mime) is not str
            or type(output_cas_key) is not str
            or output_mime != OUTPUT_MIME
            or output_cas_key != content_addressed_key(expected[2])
        ):
            _invalid()
        retained = (
            materialization.output_store_id,
            materialization.output_version,
            materialization.output_sha256,
            materialization.output_size,
            materialization.output_mime,
            materialization.output_cas_key,
        )
        if expected != retained:
            raise SceneArtStateConflict()
        result = self.finish_output_materialization(
            context,
            materialization=materialization,
            output_storage_incarnation=output_storage_incarnation,
        )
        if result.output is None:
            raise SceneArtStateConflict()
        return result.output

    @staticmethod
    def _output(job: SceneArtJob) -> SceneArtOutput:
        if (
            job.state != OUTPUT_ADOPTED
            or job.output_version is None
            or job.output_sha256 is None
            or job.output_size is None
            or job.output_mime is None
            or job.output_cas_key is None
            or job.output_storage_incarnation is None
        ):
            raise SceneArtStateConflict()
        return SceneArtOutput(
            owner_principal=job.owner_principal,
            job_id=job.job_id,
            output_version=job.output_version,
            output_sha256=job.output_sha256,
            output_size=job.output_size,
            output_mime=job.output_mime,
            output_cas_key=job.output_cas_key,
            output_storage_incarnation=job.output_storage_incarnation,
        )

    def read_output(
        self,
        context: AuthorizedServiceRequest,
        *,
        output_store_id: str,
    ) -> SceneArtOutput:
        owner, job_id = _route_job_id(context, _OUTPUT_READ_SCOPE)
        store_id = _digest(output_store_id)
        now = _now(self._clock)
        # BEGIN IMMEDIATE keeps the job reference and exact object incarnation
        # stable until this metadata snapshot has been constructed. The byte
        # store must subsequently read that same incarnation, never digest-only.
        with self._transaction() as connection:
            self._binding_store_id(
                connection, owner, store_id, create=False, now=now
            )
            self._assert_output_store_objects_bound(
                connection, owner, store_id
            )
            row = self._owned_row(connection, owner, job_id)
            output = self._output(self._job(row))
            object_row = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS} FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_sha256=?
                      AND output_storage_incarnation=?""",
                (
                    owner,
                    output.output_sha256,
                    output.output_storage_incarnation,
                ),
            ).fetchone()
            if object_row is None:
                raise SceneArtStateConflict()
            retained = self._object(object_row)
            if retained.output_store_id != store_id:
                raise SceneArtOutputStoreConflict()
            if (
                retained.state != "live"
                or retained.output_size != output.output_size
                or retained.output_mime != output.output_mime
                or retained.output_cas_key != output.output_cas_key
            ):
                raise SceneArtStateConflict()
            return output

    def acknowledge_output(
        self,
        context: AuthorizedServiceRequest,
        *,
        output_version: int,
        output_sha256: str,
    ) -> OutputAckTransition:
        """Commit acknowledgement and delete-pending before byte deletion."""

        owner, job_id = _route_job_id(context, _OUTPUT_ACK_SCOPE)
        version = _output_version(output_version)
        digest = _digest(output_sha256)
        now = _now(self._clock)
        with self._transaction() as connection:
            job = self._owned_row(connection, owner, job_id)
            if (
                job["state"] not in {OUTPUT_ADOPTED, OUTPUT_ACKNOWLEDGED}
                or job["output_version"] != version
                or job["output_sha256"] != digest
            ):
                raise SceneArtOutputAckConflict()
            incarnation = job["output_storage_incarnation"]
            if not isinstance(incarnation, str):
                raise SceneArtOutputAckConflict()
            object_row = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS} FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_sha256=?
                      AND output_storage_incarnation=?""",
                (owner, digest, incarnation),
            ).fetchone()
            if object_row is None:
                raise SceneArtOutputAckConflict()
            retained_object = self._object(object_row)
            binding_row = connection.execute(
                """SELECT output_store_id
                   FROM scene_art_output_store_bindings
                   WHERE owner_principal=?""",
                (owner,),
            ).fetchone()
            if (
                binding_row is None
                or retained_object.output_store_id
                != binding_row["output_store_id"]
                or retained_object.output_size != job["output_size"]
                or retained_object.output_mime != job["output_mime"]
                or retained_object.output_cas_key != job["output_cas_key"]
                or (
                    job["state"] == OUTPUT_ADOPTED
                    and retained_object.state != "live"
                )
            ):
                raise SceneArtOutputAckConflict()
            tombstone = connection.execute(
                f"""SELECT {_TOMBSTONE_COLUMNS}
                    FROM scene_art_output_tombstones
                    WHERE owner_principal=? AND job_id=?
                      AND output_sha256=? AND output_version=?""",
                (owner, job_id, digest, version),
            ).fetchone()
            replayed = tombstone is not None
            if tombstone is None:
                connection.execute(
                    """INSERT INTO scene_art_output_tombstones(
                         schema_version,owner_principal,job_id,output_sha256,
                         output_version,state,acknowledged_at,deleted_at
                       ) VALUES (?,?,?,?,?,'delete_pending',?,NULL)""",
                    (TOMBSTONE_SCHEMA, owner, job_id, digest, version, now),
                )
            if job["state"] == OUTPUT_ADOPTED:
                changed = connection.execute(
                    """UPDATE scene_art_jobs
                       SET state='output_acknowledged',output_acknowledged_at=?,updated_at=?
                       WHERE owner_principal=? AND job_id=? AND state='output_adopted'
                         AND output_sha256=? AND output_version=?""",
                    (now, now, owner, job_id, digest, version),
                ).rowcount
                if changed != 1:
                    raise SceneArtOutputAckConflict()
            live_reference = connection.execute(
                """SELECT 1 FROM scene_art_jobs
                   WHERE owner_principal=? AND output_sha256=?
                     AND output_storage_incarnation=?
                     AND state='output_adopted' LIMIT 1""",
                (owner, digest, incarnation),
            ).fetchone()
            materialization_hold = connection.execute(
                """SELECT 1 FROM scene_art_output_materializations
                   WHERE owner_principal=? AND output_store_id=?
                     AND output_sha256=? LIMIT 1""",
                (
                    owner,
                    retained_object.output_store_id,
                    digest,
                ),
            ).fetchone()
            if live_reference is None and materialization_hold is None:
                connection.execute(
                    """UPDATE scene_art_output_objects
                       SET state='delete_pending',delete_pending_at=?
                       WHERE owner_principal=? AND output_sha256=?
                         AND output_storage_incarnation=? AND state='live'""",
                    (now, owner, digest, incarnation),
                )
            object_row = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS} FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_sha256=?
                      AND output_storage_incarnation=?""",
                (owner, digest, incarnation),
            ).fetchone()
            if object_row is None:
                raise SceneArtOutputAckConflict()
            object_state = self._object(object_row).state
            if object_state not in OUTPUT_OBJECT_STATES:
                raise SceneArtOutputAckConflict()
            tombstone = connection.execute(
                f"""SELECT {_TOMBSTONE_COLUMNS}
                    FROM scene_art_output_tombstones
                    WHERE owner_principal=? AND job_id=?
                      AND output_sha256=? AND output_version=?""",
                (owner, job_id, digest, version),
            ).fetchone()
            if tombstone is None:
                raise SceneArtOutputAckConflict()
            return OutputAckTransition(
                self._tombstone(tombstone), replayed, object_state
            )

    def list_pending_output_deletions(
        self, context: AuthContext, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[OutputTombstone, ...]:
        """List owner-scoped audit tombstones, never CAS deletion claims.

        Shared objects can still have live references after one job is
        acknowledged. Only :meth:`list_pending_object_deletions` returns the
        exact incarnation and size required to authorize physical deletion.
        """

        owner = _authenticated_owner(context, _OUTPUT_ACK_SCOPE)
        selected_limit = _limit(limit)
        with closing(self._connect()) as connection:
            if type(context) is AuthorizedServiceRequest:
                if (
                    not context.atomic_owner_lookup_required
                    or context.job_id is None
                ):
                    raise StoreAuthorizationError()
                job_id = _identifier(context.job_id)
                rows = connection.execute(
                    f"""SELECT {_TOMBSTONE_COLUMNS}
                        FROM scene_art_output_tombstones
                        WHERE owner_principal=? AND job_id=?
                          AND state='delete_pending'
                        ORDER BY acknowledged_at,job_id LIMIT ?""",
                    (owner, job_id, selected_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""SELECT {_TOMBSTONE_COLUMNS}
                        FROM scene_art_output_tombstones
                        WHERE owner_principal=? AND state='delete_pending'
                        ORDER BY acknowledged_at,job_id LIMIT ?""",
                    (owner, selected_limit),
                ).fetchall()
        return tuple(self._tombstone(row) for row in rows)

    def list_pending_object_deletions(
        self,
        context: ServicePrincipal,
        *,
        output_store_id: str,
        limit: int = MAX_LIST_RESULTS,
    ) -> tuple[PendingObjectDeletion, ...]:
        """Return each exact shared object once, with size/incarnation joined.

        The object ledger itself is the durable deletion queue. This supports
        cancelled/failed publications that never had a delivery ACK while
        proving no adopted job reference or pre-publication digest hold remains.
        """

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _OUTPUT_ACK_SCOPE)
        store_id = _digest(output_store_id)
        selected_limit = _limit(limit)
        now = _now(self._clock)
        with self._transaction() as connection:
            self._binding_store_id(
                connection, owner, store_id, create=False, now=now
            )
            self._assert_output_store_objects_bound(
                connection, owner, store_id
            )
            rows = connection.execute(
                """SELECT
                     o.owner_principal,o.output_store_id,o.output_sha256,
                     o.output_storage_incarnation,o.output_size,o.output_mime,
                     o.output_cas_key,o.delete_pending_at
                   FROM scene_art_output_objects AS o
                   JOIN scene_art_output_store_bindings AS b
                     ON b.owner_principal=o.owner_principal
                    AND b.output_store_id=o.output_store_id
                   WHERE o.owner_principal=?
                     AND b.output_store_id=?
                     AND o.output_store_id=?
                     AND o.state='delete_pending'
                     AND NOT EXISTS (
                       SELECT 1 FROM scene_art_jobs AS live
                       WHERE live.owner_principal=o.owner_principal
                         AND live.output_sha256=o.output_sha256
                         AND live.output_storage_incarnation=
                             o.output_storage_incarnation
                         AND live.state='output_adopted'
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM scene_art_output_materializations AS m
                       WHERE m.owner_principal=o.owner_principal
                         AND m.output_store_id=o.output_store_id
                         AND m.output_sha256=o.output_sha256
                     )
                   ORDER BY o.delete_pending_at,o.output_sha256,
                            o.output_storage_incarnation
                   LIMIT ?""",
                (owner, store_id, store_id, selected_limit),
            ).fetchall()
        return tuple(
            PendingObjectDeletion(
                owner_principal=row["owner_principal"],
                output_store_id=row["output_store_id"],
                output_sha256=row["output_sha256"],
                output_storage_incarnation=row[
                    "output_storage_incarnation"
                ],
                output_size=row["output_size"],
                output_mime=row["output_mime"],
                output_cas_key=row["output_cas_key"],
            )
            for row in rows
        )

    def finalize_output_deletion(
        self,
        context: ServicePrincipal,
        *,
        deletion: PendingObjectDeletion,
    ) -> FinalizedObjectDeletion:
        """Finalize one exact incarnation and every matching job tombstone."""

        if type(context) is not ServicePrincipal:
            raise StoreAuthorizationError()
        owner = _authenticated_owner(context, _OUTPUT_ACK_SCOPE)
        if (
            type(deletion) is not PendingObjectDeletion
            or type(deletion.owner_principal) is not str
            or deletion.owner_principal != owner
        ):
            raise StoreAuthorizationError()
        store_id = _digest(deletion.output_store_id)
        digest = _digest(deletion.output_sha256)
        incarnation = _storage_incarnation(
            deletion.output_storage_incarnation
        )
        size = _output_size(deletion.output_size)
        if (
            type(deletion.output_mime) is not str
            or type(deletion.output_cas_key) is not str
            or deletion.output_mime != OUTPUT_MIME
            or deletion.output_cas_key != content_addressed_key(digest)
        ):
            _invalid()
        now = _now(self._clock)
        with self._transaction() as connection:
            self._binding_store_id(
                connection, owner, store_id, create=False, now=now
            )
            self._assert_output_store_objects_bound(
                connection, owner, store_id
            )
            object_row = connection.execute(
                f"""SELECT {_OBJECT_COLUMNS} FROM scene_art_output_objects
                    WHERE owner_principal=? AND output_sha256=?
                      AND output_storage_incarnation=?""",
                (owner, digest, incarnation),
            ).fetchone()
            if object_row is None:
                raise SceneArtOutputAckConflict()
            retained = self._object(object_row)
            if retained.output_store_id != store_id:
                raise SceneArtOutputStoreConflict()
            if (
                retained.output_size != size
                or retained.output_mime != deletion.output_mime
                or retained.output_cas_key != deletion.output_cas_key
                or retained.state not in {"delete_pending", "deleted"}
            ):
                raise SceneArtOutputAckConflict()
            live_reference = connection.execute(
                """SELECT 1 FROM scene_art_jobs
                   WHERE owner_principal=? AND output_sha256=?
                     AND output_storage_incarnation=?
                     AND state='output_adopted' LIMIT 1""",
                (owner, digest, incarnation),
            ).fetchone()
            if live_reference is not None:
                raise SceneArtOutputAckConflict()

            if retained.state == "delete_pending":
                materialization_hold = connection.execute(
                    """SELECT 1 FROM scene_art_output_materializations
                       WHERE owner_principal=? AND output_store_id=?
                         AND output_sha256=? LIMIT 1""",
                    (owner, store_id, digest),
                ).fetchone()
                if materialization_hold is not None:
                    raise SceneArtOutputAckConflict()

            tombstones = connection.execute(
                f"""SELECT t.{_TOMBSTONE_COLUMNS.replace(',', ',t.')}
                    FROM scene_art_output_tombstones AS t
                    JOIN scene_art_jobs AS j
                      ON j.owner_principal=t.owner_principal
                     AND j.job_id=t.job_id
                     AND j.output_sha256=t.output_sha256
                     AND j.output_version=t.output_version
                    WHERE t.owner_principal=? AND t.output_sha256=?
                      AND j.output_storage_incarnation=?
                    ORDER BY t.acknowledged_at,t.job_id""",
                (owner, digest, incarnation),
            ).fetchall()
            replayed = retained.state == "deleted"
            if retained.state == "delete_pending":
                changed = connection.execute(
                    """UPDATE scene_art_output_objects
                       SET state='deleted',deleted_at=?
                       WHERE owner_principal=? AND output_sha256=?
                         AND output_storage_incarnation=?
                         AND state='delete_pending'""",
                    (now, owner, digest, incarnation),
                ).rowcount
                if changed != 1:
                    raise SceneArtOutputAckConflict()
                connection.execute(
                    """UPDATE scene_art_output_tombstones
                       SET state='deleted',deleted_at=?
                       WHERE state='delete_pending' AND owner_principal=?
                         AND output_sha256=? AND EXISTS (
                           SELECT 1 FROM scene_art_jobs AS j
                           WHERE j.owner_principal=
                                 scene_art_output_tombstones.owner_principal
                             AND j.job_id=
                                 scene_art_output_tombstones.job_id
                             AND j.output_sha256=
                                 scene_art_output_tombstones.output_sha256
                             AND j.output_version=
                                 scene_art_output_tombstones.output_version
                             AND j.output_storage_incarnation=?
                         )""",
                    (now, owner, digest, incarnation),
                )
            rows = connection.execute(
                f"""SELECT t.{_TOMBSTONE_COLUMNS.replace(',', ',t.')}
                    FROM scene_art_output_tombstones AS t
                    JOIN scene_art_jobs AS j
                      ON j.owner_principal=t.owner_principal
                     AND j.job_id=t.job_id
                     AND j.output_sha256=t.output_sha256
                     AND j.output_version=t.output_version
                    WHERE t.owner_principal=? AND t.output_sha256=?
                      AND j.output_storage_incarnation=?
                    ORDER BY t.acknowledged_at,t.job_id""",
                (owner, digest, incarnation),
            ).fetchall()
            result_tombstones = tuple(self._tombstone(row) for row in rows)
            if any(
                tombstone.state != "deleted" for tombstone in result_tombstones
            ):
                raise SceneArtOutputAckConflict()
            return FinalizedObjectDeletion(deletion, result_tombstones, replayed)


from typing import NoReturn  # noqa: E402 (keeps public definitions first)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "BUSY_TIMEOUT_MS",
    "CANCELLED",
    "FAILED",
    "FinalizedObjectDeletion",
    "JOB_STATES",
    "MAX_CANONICAL_REQUEST_BYTES",
    "MAX_LIST_RESULTS",
    "MAX_OUTPUT_BYTES",
    "OUTPUT_ACKNOWLEDGED",
    "OUTPUT_ADOPTED",
    "OUTPUT_MIME",
    "OUTPUT_MATERIALIZATION_OUTCOMES",
    "OUTPUT_OBJECT_STATES",
    "OutputAckTransition",
    "OutputMaterialization",
    "OutputMaterializationBeginResult",
    "OutputMaterializationFinishResult",
    "OutputMaterializationMissingResult",
    "OutputObject",
    "OutputTombstone",
    "PendingObjectDeletion",
    "PlatformSceneArtStore",
    "PlatformStoreError",
    "QUEUED",
    "RUNNING",
    "SUBMISSION_UNCERTAIN",
    "SUBMITTED",
    "SUBMIT_INTENT",
    "SceneArtIdempotencyConflict",
    "SceneArtJob",
    "SceneArtJobConflict",
    "SceneArtOutput",
    "SceneArtOutputAckConflict",
    "SceneArtOutputStoreConflict",
    "SceneArtRequestVaultConflict",
    "SceneArtResourceNotFound",
    "SceneArtStateConflict",
    "StoreAuthorizationError",
    "StoreConfigurationError",
    "StoreRequestInvalid",
    "SubmitIntentResult",
    "TOMBSTONE_SCHEMA",
    "content_addressed_key",
    "deterministic_prompt_id_fields",
]
