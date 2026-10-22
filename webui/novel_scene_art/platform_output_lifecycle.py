"""Dormant coordinator for durable Novel Scene Art output publication.

This module is deliberately unrouted. It composes the owner-private SQLite
ledger with the owner-private generated-output CAS while holding one CAS
publication session across every database/CAS hand-off. Callers cannot choose
an owner, store identity, publication id, path, prompt, graph, or model.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .platform_output_store import (
    GeneratedOutputStaleHandle,
    GeneratedOutputStore,
    GeneratedOutputStoreError,
    OutputHandle,
    PngProfile,
    PreparedOutput,
    PublicationSession,
    PublishedOutput,
)
from .platform_service_account import EXPECTED_SCOPES, SERVICE_PRINCIPAL
from .platform_service_auth import (
    MAX_OUTPUT_ACK_BODY_BYTES,
    AuthorizedServiceRequest,
    ServicePrincipal,
)
from .platform_store import (
    JOB_STATES,
    MAX_LIST_RESULTS,
    MAX_OUTPUT_BYTES,
    OUTPUT_MATERIALIZATION_OUTCOMES,
    OUTPUT_MIME,
    TOMBSTONE_SCHEMA,
    FinalizedObjectDeletion,
    OutputAckTransition,
    OutputMaterialization,
    OutputMaterializationBeginResult,
    OutputMaterializationFinishResult,
    OutputMaterializationMissingResult,
    OutputObject,
    OutputTombstone,
    PendingObjectDeletion,
    PlatformSceneArtStore,
    SceneArtJob,
    SceneArtOutput,
    content_addressed_key,
)


PUBLICATION_ID_SCHEMA = "castle.novel-scene-art-output-publication-id.v1"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$", re.ASCII)
_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$", re.ASCII)
_DIGEST = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_INCARNATION = re.compile(r"^inc_[a-f0-9]{32}$", re.ASCII)
_PUBLICATION = re.compile(r"^pub_[a-f0-9]{32}$", re.ASCII)
_READ_SCOPE = "scene_art.output.read-own"
_ACK_SCOPE = "scene_art.output.ack-own"
_READ_ROUTE = "/api/internal/novel-scene-art/v1/jobs/{job_id}/output"
_ACK_ROUTE = "/api/internal/novel-scene-art/v1/jobs/{job_id}/output-ack"


class OutputLifecycleError(RuntimeError):
    """Base class for bounded internal lifecycle failures."""


class OutputLifecycleConfigurationError(OutputLifecycleError):
    """Trusted store/principal/profile configuration is inconsistent."""


class OutputLifecycleAuthorizationError(OutputLifecycleError):
    """A non-canonical service principal or route context was supplied."""


class OutputLifecycleConsistencyError(OutputLifecycleError):
    """Durable database state and the exact private CAS object disagree."""


@dataclass(frozen=True, slots=True)
class MaterializedOutput:
    job_id: str
    publication_id: str
    output_version: int
    output_sha256: str
    output_size: int
    output_mime: str
    output_storage_incarnation: str
    outcome: str
    cas_created: bool


@dataclass(frozen=True, slots=True)
class MaterializationResolution:
    job_id: str
    publication_id: str
    outcome: str
    output_storage_incarnation: str | None


@dataclass(frozen=True, slots=True)
class DeletionResolution:
    output_sha256: str
    output_storage_incarnation: str
    physical_object_was_present: bool
    finalized_replay: bool


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise ValueError("output version is invalid")
    return value


def _limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_LIST_RESULTS:
        raise ValueError("limit is invalid")
    return value


def _principal(value: object) -> ServicePrincipal:
    if (
        type(value) is not ServicePrincipal
        or type(value.principal_id) is not str
        or value.principal_id != SERVICE_PRINCIPAL
        or type(value.scopes) is not tuple
        or len(value.scopes) != len(EXPECTED_SCOPES)
        or any(type(scope) is not str for scope in value.scopes)
        or value.scopes != EXPECTED_SCOPES
        or type(value.key_id) is not str
        or _KEY_ID.fullmatch(value.key_id) is None
    ):
        raise OutputLifecycleAuthorizationError(
            "output lifecycle principal is invalid"
        )
    return value


def _route_context(
    value: object,
    *,
    scope: str,
    route_template: str,
    max_body_bytes: int,
) -> AuthorizedServiceRequest:
    if (
        type(value) is not AuthorizedServiceRequest
        or type(value.principal_id) is not str
        or value.principal_id != SERVICE_PRINCIPAL
        or type(value.owner_principal) is not str
        or value.owner_principal != SERVICE_PRINCIPAL
        or type(value.scope) is not str
        or value.scope != scope
        or type(value.route_template) is not str
        or value.route_template != route_template
        or type(value.job_id) is not str
        or _IDENTIFIER.fullmatch(value.job_id) is None
        or type(value.max_body_bytes) is not int
        or value.max_body_bytes != max_body_bytes
        or type(value.atomic_owner_lookup_required) is not bool
        or value.atomic_owner_lookup_required is not True
        or type(value.key_id) is not str
        or _KEY_ID.fullmatch(value.key_id) is None
    ):
        raise OutputLifecycleAuthorizationError(
            "output lifecycle route authorization is invalid"
        )
    return value


def deterministic_publication_id(
    *,
    owner_principal: str,
    job_id: str,
    output_version: int,
    output_sha256: str,
    output_size: int,
    output_mime: str,
    output_store_id: str,
    profile: PngProfile,
) -> str:
    """Derive the sole publication id from canonical trusted metadata."""

    owner = _identifier(owner_principal, "owner principal")
    job = _identifier(job_id, "job id")
    version = _version(output_version)
    digest = _digest(output_sha256, "output digest")
    store_id = _digest(output_store_id, "output store identity")
    if (
        type(output_size) is not int
        or not 1 <= output_size <= MAX_OUTPUT_BYTES
        or type(output_mime) is not str
        or output_mime != OUTPUT_MIME
        or type(profile) is not PngProfile
        or output_size > profile.maximum_bytes
    ):
        raise ValueError("output publication metadata is invalid")
    encoded = json.dumps(
        {
            "job_id": job,
            "output_mime": output_mime,
            "output_sha256": digest,
            "output_size": output_size,
            "output_store_id": store_id,
            "output_version": version,
            "owner_principal": owner,
            "profile": {
                "height": profile.height,
                "maximum_bytes": profile.maximum_bytes,
                "width": profile.width,
            },
            "schema_version": PUBLICATION_ID_SCHEMA,
        },
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "pub_" + hashlib.sha256(encoded).hexdigest()[:32]


class PlatformOutputLifecycle:
    """Exact, owner-fixed bridge between durable jobs and the private CAS."""

    __slots__ = (
        "_principal",
        "_job_store",
        "_output_store",
        "_output_store_id",
        "_profile",
        "_maximum_output_bytes",
    )

    def __init__(
        self,
        *,
        principal: ServicePrincipal,
        job_store: PlatformSceneArtStore,
        output_store: GeneratedOutputStore,
        profile: PngProfile,
    ) -> None:
        selected_principal = _principal(principal)
        if type(job_store) is not PlatformSceneArtStore:
            raise TypeError("job_store must be PlatformSceneArtStore")
        if type(output_store) is not GeneratedOutputStore:
            raise TypeError("output_store must be GeneratedOutputStore")
        if type(profile) is not PngProfile:
            raise TypeError("profile must be PngProfile")
        store_profile = output_store.profile
        if (
            type(store_profile) is not PngProfile
            or (
                store_profile.width,
                store_profile.height,
                store_profile.maximum_bytes,
            )
            != (profile.width, profile.height, profile.maximum_bytes)
        ):
            raise OutputLifecycleConfigurationError(
                "private output profile does not match lifecycle configuration"
            )
        store_id = output_store.store_identity
        _digest(store_id, "output store identity")
        retained = job_store.bind_output_store(
            selected_principal, output_store_id=store_id
        )
        if type(retained) is not str or retained != store_id:
            raise OutputLifecycleConfigurationError(
                "private output store identity binding is inconsistent"
            )
        self._principal = selected_principal
        self._job_store = job_store
        self._output_store = output_store
        self._output_store_id = store_id
        self._profile = store_profile
        self._maximum_output_bytes = store_profile.maximum_bytes

    @property
    def output_store_identity(self) -> str:
        return self._output_store_id

    @property
    def profile(self) -> PngProfile:
        return self._profile

    def _prepared(self, png_bytes: object) -> PreparedOutput:
        if (
            type(png_bytes) is not bytes
            or not png_bytes
            or len(png_bytes) > self._maximum_output_bytes
        ):
            raise ValueError("PNG candidate is invalid")
        prepared = self._output_store.prepare_png(
            self._principal.principal_id, png_bytes
        )
        if (
            type(prepared) is not PreparedOutput
            or type(prepared.owner_principal) is not str
            or prepared.owner_principal != self._principal.principal_id
            or type(prepared.sha256) is not str
            or _DIGEST.fullmatch(prepared.sha256) is None
            or type(prepared.byte_size) is not int
            or not 1 <= prepared.byte_size <= self._maximum_output_bytes
            or type(prepared.mime_type) is not str
            or prepared.mime_type != OUTPUT_MIME
            or type(prepared.profile) is not PngProfile
            or prepared.profile is not self._profile
            or type(prepared._store_id) is not str
            or prepared._store_id != self._output_store_id
            or type(prepared._auth_tag) is not str
            or _DIGEST.fullmatch(prepared._auth_tag) is None
            or type(prepared._sanitized_bytes) is not bytes
            or len(prepared._sanitized_bytes) != prepared.byte_size
            or hashlib.sha256(prepared._sanitized_bytes).hexdigest()
            != prepared.sha256
        ):
            raise OutputLifecycleConsistencyError(
                "private output preparation returned invalid metadata"
            )
        return prepared

    def _publication_id(
        self, *, job_id: str, output_version: int, prepared: PreparedOutput
    ) -> str:
        return deterministic_publication_id(
            owner_principal=self._principal.principal_id,
            job_id=job_id,
            output_version=output_version,
            output_sha256=prepared.sha256,
            output_size=prepared.byte_size,
            output_mime=prepared.mime_type,
            output_store_id=self._output_store_id,
            profile=self._profile,
        )

    def _validate_materialization(
        self,
        value: object,
        *,
        job_id: str,
        publication_id: str,
        output_version: int,
        output_sha256: str,
        output_size: int,
    ) -> OutputMaterialization:
        if (
            type(value) is not OutputMaterialization
            or type(value.owner_principal) is not str
            or value.owner_principal != self._principal.principal_id
            or type(value.job_id) is not str
            or value.job_id != job_id
            or type(value.publication_id) is not str
            or _PUBLICATION.fullmatch(value.publication_id) is None
            or value.publication_id != publication_id
            or type(value.output_store_id) is not str
            or _DIGEST.fullmatch(value.output_store_id) is None
            or value.output_store_id != self._output_store_id
            or type(value.output_version) is not int
            or value.output_version != output_version
            or type(value.output_sha256) is not str
            or _DIGEST.fullmatch(value.output_sha256) is None
            or value.output_sha256 != output_sha256
            or type(value.output_size) is not int
            or value.output_size != output_size
            or not 1 <= value.output_size <= self._maximum_output_bytes
            or type(value.output_mime) is not str
            or value.output_mime != OUTPUT_MIME
            or type(value.output_cas_key) is not str
            or value.output_cas_key != content_addressed_key(output_sha256)
            or type(value.created_at) is not int
            or type(value.updated_at) is not int
            or value.updated_at < value.created_at
        ):
            raise OutputLifecycleConsistencyError(
                "database materialization projection is inconsistent"
            )
        return value

    def _begin(
        self,
        *,
        job_id: str,
        output_version: int,
        prepared: PreparedOutput,
        publication_id: str,
    ) -> OutputMaterialization:
        result = self._job_store.begin_output_materialization(
            self._principal,
            job_id=job_id,
            publication_id=publication_id,
            output_store_id=self._output_store_id,
            output_version=output_version,
            output_sha256=prepared.sha256,
            output_size=prepared.byte_size,
            output_mime=prepared.mime_type,
            output_cas_key=content_addressed_key(prepared.sha256),
        )
        if type(result) is not OutputMaterializationBeginResult or type(
            result.created
        ) is not bool:
            raise OutputLifecycleConsistencyError(
                "database materialization begin result is invalid"
            )
        return self._validate_materialization(
            result.materialization,
            job_id=job_id,
            publication_id=publication_id,
            output_version=output_version,
            output_sha256=prepared.sha256,
            output_size=prepared.byte_size,
        )

    def _validate_handle(
        self,
        value: object,
        *,
        output_sha256: str,
        output_size: int,
        output_storage_incarnation: str | None = None,
    ) -> OutputHandle:
        if (
            type(value) is not OutputHandle
            or type(value.owner_principal) is not str
            or value.owner_principal != self._principal.principal_id
            or type(value.sha256) is not str
            or _DIGEST.fullmatch(value.sha256) is None
            or value.sha256 != output_sha256
            or type(value.byte_size) is not int
            or value.byte_size != output_size
            or not 1 <= value.byte_size <= self._maximum_output_bytes
            or type(value.width) is not int
            or value.width != self._profile.width
            or type(value.height) is not int
            or value.height != self._profile.height
            or type(value.mime_type) is not str
            or value.mime_type != OUTPUT_MIME
            or type(value.storage_incarnation) is not str
            or _INCARNATION.fullmatch(value.storage_incarnation) is None
            or (
                output_storage_incarnation is not None
                and value.storage_incarnation != output_storage_incarnation
            )
            or type(value._store_id) is not str
            or value._store_id != self._output_store_id
            or type(value._auth_tag) is not str
            or _DIGEST.fullmatch(value._auth_tag) is None
        ):
            raise OutputLifecycleConsistencyError(
                "private CAS returned an inconsistent exact handle"
            )
        return value

    def _validate_object(
        self,
        value: object,
        *,
        materialization: OutputMaterialization,
        state: str = "live",
    ) -> OutputObject:
        if type(state) is not str or state not in {"live", "delete_pending"}:
            raise OutputLifecycleConsistencyError(
                "database live-object state expectation is invalid"
            )
        if (
            type(value) is not OutputObject
            or type(value.owner_principal) is not str
            or value.owner_principal != self._principal.principal_id
            or type(value.output_store_id) is not str
            or _DIGEST.fullmatch(value.output_store_id) is None
            or value.output_store_id != self._output_store_id
            or type(value.output_sha256) is not str
            or _DIGEST.fullmatch(value.output_sha256) is None
            or value.output_sha256 != materialization.output_sha256
            or type(value.output_size) is not int
            or value.output_size != materialization.output_size
            or not 1 <= value.output_size <= self._maximum_output_bytes
            or type(value.output_mime) is not str
            or value.output_mime != materialization.output_mime
            or type(value.output_cas_key) is not str
            or value.output_cas_key != materialization.output_cas_key
            or type(value.state) is not str
            or value.state != state
            or type(value.output_storage_incarnation) is not str
            or _INCARNATION.fullmatch(value.output_storage_incarnation) is None
            or type(value.created_at) is not int
            or value.created_at < 0
            or (
                state == "live"
                and (
                    value.delete_pending_at is not None
                    or value.deleted_at is not None
                )
            )
            or (
                state == "delete_pending"
                and (
                    type(value.delete_pending_at) is not int
                    or value.delete_pending_at < value.created_at
                    or value.deleted_at is not None
                )
            )
        ):
            raise OutputLifecycleConsistencyError(
                "database live-object projection is inconsistent"
            )
        return value

    def _validate_job_projection(
        self,
        value: object,
        *,
        job_id: str,
    ) -> SceneArtJob:
        required_strings = (
            "owner_principal",
            "job_id",
            "request_id",
            "generation_key",
            "canonical_request_sha256",
            "request_vault_id",
            "request_storage_incarnation",
            "scene_pack_sha256",
            "render_plan_sha256",
            "renderer_release_sha256",
            "prompt_id",
            "state",
        )
        optional_strings = (
            "provider_submission_id",
            "last_reconcile_outcome",
            "last_error_code",
            "output_sha256",
            "output_mime",
            "output_cas_key",
            "output_storage_incarnation",
        )
        required_integers = (
            "canonical_request_size",
            "reconcile_attempts",
            "created_at",
            "updated_at",
        )
        optional_integers = (
            "dispatch_started_at",
            "submitted_at",
            "submission_uncertain_at",
            "last_reconciled_at",
            "cancelled_at",
            "output_version",
            "output_size",
            "output_adopted_at",
            "output_acknowledged_at",
        )
        if (
            type(value) is not SceneArtJob
            or any(
                type(getattr(value, field)) is not str
                for field in required_strings
            )
            or any(
                item is not None and type(item) is not str
                for item in (getattr(value, field) for field in optional_strings)
            )
            or any(
                type(getattr(value, field)) is not int
                for field in required_integers
            )
            or any(
                item is not None and type(item) is not int
                for item in (getattr(value, field) for field in optional_integers)
            )
            or type(value.reconcile_required) is not bool
            or value.owner_principal != self._principal.principal_id
            or value.job_id != job_id
            or value.state not in JOB_STATES
            or _DIGEST.fullmatch(value.generation_key) is None
            or _DIGEST.fullmatch(value.canonical_request_sha256) is None
            or _DIGEST.fullmatch(value.request_vault_id) is None
            or _DIGEST.fullmatch(value.scene_pack_sha256) is None
            or _DIGEST.fullmatch(value.render_plan_sha256) is None
            or _DIGEST.fullmatch(value.renderer_release_sha256) is None
            or value.created_at < 0
            or value.updated_at < value.created_at
        ):
            raise OutputLifecycleConsistencyError(
                "database job projection is inconsistent"
            )
        return value

    def _validate_output_projection(
        self,
        value: object,
        *,
        materialization: OutputMaterialization,
        output_storage_incarnation: str,
    ) -> SceneArtOutput:
        if (
            type(value) is not SceneArtOutput
            or type(value.owner_principal) is not str
            or value.owner_principal != self._principal.principal_id
            or type(value.job_id) is not str
            or value.job_id != materialization.job_id
            or type(value.output_version) is not int
            or value.output_version != materialization.output_version
            or type(value.output_sha256) is not str
            or value.output_sha256 != materialization.output_sha256
            or type(value.output_size) is not int
            or value.output_size != materialization.output_size
            or type(value.output_mime) is not str
            or value.output_mime != OUTPUT_MIME
            or type(value.output_cas_key) is not str
            or value.output_cas_key != materialization.output_cas_key
            or type(value.output_storage_incarnation) is not str
            or value.output_storage_incarnation != output_storage_incarnation
        ):
            raise OutputLifecycleConsistencyError(
                "database output projection is inconsistent"
            )
        return value

    def _validate_tombstone(
        self,
        value: object,
        *,
        job_id: str | None,
        output_version: int | None,
        output_sha256: str,
        required_state: str | None = None,
    ) -> OutputTombstone:
        if (
            type(value) is not OutputTombstone
            or type(value.schema_version) is not str
            or value.schema_version != TOMBSTONE_SCHEMA
            or type(value.owner_principal) is not str
            or value.owner_principal != self._principal.principal_id
            or type(value.job_id) is not str
            or _IDENTIFIER.fullmatch(value.job_id) is None
            or (job_id is not None and value.job_id != job_id)
            or type(value.output_sha256) is not str
            or value.output_sha256 != output_sha256
            or type(value.output_version) is not int
            or not 1 <= value.output_version <= 2_147_483_647
            or (
                output_version is not None
                and value.output_version != output_version
            )
            or type(value.state) is not str
            or value.state not in {"delete_pending", "deleted"}
            or (required_state is not None and value.state != required_state)
            or type(value.acknowledged_at) is not int
            or value.acknowledged_at < 0
            or (
                value.deleted_at is not None
                and (
                    type(value.deleted_at) is not int
                    or value.deleted_at < value.acknowledged_at
                )
            )
            or (value.state == "delete_pending" and value.deleted_at is not None)
            or (value.state == "deleted" and type(value.deleted_at) is not int)
        ):
            raise OutputLifecycleConsistencyError(
                "database tombstone projection is inconsistent"
            )
        return value

    def _probe_or_publish(
        self,
        session: PublicationSession,
        *,
        materialization: OutputMaterialization,
        prepared: PreparedOutput,
    ) -> tuple[OutputHandle, bool]:
        live = self._job_store.get_live_output_object_for_materialization(
            self._principal, materialization=materialization
        )
        if live is not None:
            retained = self._validate_object(live, materialization=materialization)
            try:
                handle = session.probe_live_exact(
                    self._principal.principal_id,
                    materialization.output_sha256,
                    materialization.output_size,
                )
            except GeneratedOutputStoreError as error:
                raise OutputLifecycleConsistencyError(
                    "database live object does not match the private CAS"
                ) from error
            if handle is None:
                raise OutputLifecycleConsistencyError(
                    "database live object is absent from the private CAS"
                )
            return (
                self._validate_handle(
                    handle,
                    output_sha256=materialization.output_sha256,
                    output_size=materialization.output_size,
                    output_storage_incarnation=(
                        retained.output_storage_incarnation
                    ),
                ),
                False,
            )

        try:
            handle = session.probe_live_exact(
                self._principal.principal_id,
                materialization.output_sha256,
                materialization.output_size,
            )
        except GeneratedOutputStoreError as error:
            raise OutputLifecycleConsistencyError(
                "private CAS probe failed closed"
            ) from error
        if handle is not None:
            return (
                self._validate_handle(
                    handle,
                    output_sha256=materialization.output_sha256,
                    output_size=materialization.output_size,
                ),
                False,
            )
        try:
            published = session.publish_prepared(prepared)
        except GeneratedOutputStoreError as error:
            raise OutputLifecycleConsistencyError(
                "private CAS publication failed closed"
            ) from error
        if (
            type(published) is not PublishedOutput
            or type(published.created) is not bool
        ):
            raise OutputLifecycleConsistencyError(
                "private CAS publication result is invalid"
            )
        return (
            self._validate_handle(
                published.handle,
                output_sha256=materialization.output_sha256,
                output_size=materialization.output_size,
            ),
            published.created,
        )

    def _finish(
        self,
        *,
        materialization: OutputMaterialization,
        handle: OutputHandle,
    ) -> OutputMaterializationFinishResult:
        result = self._job_store.finish_output_materialization(
            self._principal,
            materialization=materialization,
            output_storage_incarnation=handle.storage_incarnation,
        )
        if (
            type(result) is not OutputMaterializationFinishResult
            or result.materialization is not materialization
            or type(result.outcome) is not str
            or result.outcome not in OUTPUT_MATERIALIZATION_OUTCOMES
        ):
            raise OutputLifecycleConsistencyError(
                "database materialization finish result is inconsistent"
            )
        job = self._validate_job_projection(
            result.job, job_id=materialization.job_id
        )
        expected_object_state = (
            "delete_pending"
            if result.outcome == "orphan_delete_pending"
            else "live"
        )
        output_object = self._validate_object(
            result.output_object,
            materialization=materialization,
            state=expected_object_state,
        )
        if output_object.output_storage_incarnation != handle.storage_incarnation:
            raise OutputLifecycleConsistencyError(
                "database materialization finish incarnation is inconsistent"
            )
        if result.outcome == "adopted":
            if job.state != "output_adopted":
                raise OutputLifecycleConsistencyError(
                    "database adopted job state is inconsistent"
                )
            self._validate_output_projection(
                result.output,
                materialization=materialization,
                output_storage_incarnation=handle.storage_incarnation,
            )
        elif result.output is not None or job.state not in {"cancelled", "failed"}:
            raise OutputLifecycleConsistencyError(
                "orphan publication result is inconsistent"
            )
        return result

    def materialize_png(
        self,
        *,
        job_id: str,
        output_version: int,
        png_bytes: bytes,
    ) -> MaterializedOutput:
        """Prepare once, durably hold, publish/probe, and atomically finish.

        This method is not an operation-replay endpoint after an ambiguous
        return. A coordinator must reconcile any retained intent and then read
        the durable job/output state; it must not blindly call this method
        again after the final database commit may have succeeded.
        """

        selected_job = _identifier(job_id, "job id")
        version = _version(output_version)
        prepared = self._prepared(png_bytes)
        publication_id = self._publication_id(
            job_id=selected_job, output_version=version, prepared=prepared
        )
        with self._output_store.publication_session() as session:
            if type(session) is not PublicationSession:
                raise OutputLifecycleConsistencyError(
                    "private CAS publication session is invalid"
                )
            try:
                validation = session.validate_prepared(prepared)
            except GeneratedOutputStoreError as error:
                raise OutputLifecycleConsistencyError(
                    "private CAS rejected prepared output authority"
                ) from error
            if validation is not None:
                raise OutputLifecycleConsistencyError(
                    "private CAS prepared-output validation result is invalid"
                )
            materialization = self._begin(
                job_id=selected_job,
                output_version=version,
                prepared=prepared,
                publication_id=publication_id,
            )
            handle, created = self._probe_or_publish(
                session, materialization=materialization, prepared=prepared
            )
            result = self._finish(materialization=materialization, handle=handle)
        return MaterializedOutput(
            job_id=selected_job,
            publication_id=publication_id,
            output_version=version,
            output_sha256=prepared.sha256,
            output_size=prepared.byte_size,
            output_mime=prepared.mime_type,
            output_storage_incarnation=handle.storage_incarnation,
            outcome=result.outcome,
            cas_created=created,
        )

    def _reconcile_one(
        self,
        session: PublicationSession,
        materialization: OutputMaterialization,
    ) -> MaterializationResolution:
        retained = self._validate_materialization(
            materialization,
            job_id=materialization.job_id,
            publication_id=materialization.publication_id,
            output_version=materialization.output_version,
            output_sha256=materialization.output_sha256,
            output_size=materialization.output_size,
        )
        expected_publication_id = deterministic_publication_id(
            owner_principal=self._principal.principal_id,
            job_id=retained.job_id,
            output_version=retained.output_version,
            output_sha256=retained.output_sha256,
            output_size=retained.output_size,
            output_mime=retained.output_mime,
            output_store_id=retained.output_store_id,
            profile=self._profile,
        )
        if retained.publication_id != expected_publication_id:
            raise OutputLifecycleConsistencyError(
                "database materialization publication id is non-canonical"
            )
        live = self._job_store.get_live_output_object_for_materialization(
            self._principal, materialization=retained
        )
        try:
            handle = session.probe_live_exact(
                self._principal.principal_id,
                retained.output_sha256,
                retained.output_size,
            )
        except GeneratedOutputStoreError as error:
            raise OutputLifecycleConsistencyError(
                "private CAS reconciliation probe failed closed"
            ) from error
        if live is not None:
            exact_live = self._validate_object(live, materialization=retained)
            if handle is None:
                raise OutputLifecycleConsistencyError(
                    "database live object is absent during reconciliation"
                )
            selected = self._validate_handle(
                handle,
                output_sha256=retained.output_sha256,
                output_size=retained.output_size,
                output_storage_incarnation=exact_live.output_storage_incarnation,
            )
            result = self._finish(materialization=retained, handle=selected)
            return MaterializationResolution(
                retained.job_id,
                retained.publication_id,
                result.outcome,
                selected.storage_incarnation,
            )
        if handle is None:
            missing = self._job_store.resolve_missing_output_materialization(
                self._principal, materialization=retained
            )
            if (
                type(missing) is not OutputMaterializationMissingResult
                or missing.materialization is not retained
                or type(missing.job_failed) is not bool
            ):
                raise OutputLifecycleConsistencyError(
                    "database missing-publication result is inconsistent"
                )
            missing_job = self._validate_job_projection(
                missing.job, job_id=retained.job_id
            )
            if (
                missing.job_failed
                and missing_job.state != "failed"
                or not missing.job_failed
                and missing_job.state not in {"cancelled", "failed"}
            ):
                raise OutputLifecycleConsistencyError(
                    "database missing-publication job state is inconsistent"
                )
            return MaterializationResolution(
                retained.job_id, retained.publication_id, "missing", None
            )
        selected = self._validate_handle(
            handle,
            output_sha256=retained.output_sha256,
            output_size=retained.output_size,
        )
        result = self._finish(materialization=retained, handle=selected)
        return MaterializationResolution(
            retained.job_id,
            retained.publication_id,
            result.outcome,
            selected.storage_incarnation,
        )

    def reconcile_materializations(
        self, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[MaterializationResolution, ...]:
        selected_limit = _limit(limit)
        with self._output_store.publication_session() as session:
            if type(session) is not PublicationSession:
                raise OutputLifecycleConsistencyError(
                    "private CAS publication session is invalid"
                )
            materializations = self._job_store.list_output_materializations(
                self._principal,
                output_store_id=self._output_store_id,
                limit=selected_limit,
            )
            if type(materializations) is not tuple or any(
                type(item) is not OutputMaterialization
                for item in materializations
            ):
                raise OutputLifecycleConsistencyError(
                    "database materialization list is invalid"
                )
            return tuple(
                self._reconcile_one(session, item) for item in materializations
            )

    def read_output(self, context: AuthorizedServiceRequest) -> bytes:
        authorized = _route_context(
            context,
            scope=_READ_SCOPE,
            route_template=_READ_ROUTE,
            max_body_bytes=0,
        )
        with self._output_store.publication_session() as session:
            if type(session) is not PublicationSession:
                raise OutputLifecycleConsistencyError(
                    "private CAS publication session is invalid"
                )
            output = self._job_store.read_output(
                authorized, output_store_id=self._output_store_id
            )
            if (
                type(output) is not SceneArtOutput
                or type(output.owner_principal) is not str
                or output.owner_principal != self._principal.principal_id
                or type(output.job_id) is not str
                or output.job_id != authorized.job_id
                or type(output.output_version) is not int
                or not 1 <= output.output_version <= 2_147_483_647
                or type(output.output_sha256) is not str
                or _DIGEST.fullmatch(output.output_sha256) is None
                or type(output.output_size) is not int
                or not 1 <= output.output_size <= self._maximum_output_bytes
                or type(output.output_mime) is not str
                or output.output_mime != OUTPUT_MIME
                or type(output.output_cas_key) is not str
                or output.output_cas_key
                != content_addressed_key(output.output_sha256)
                or type(output.output_storage_incarnation) is not str
                or _INCARNATION.fullmatch(output.output_storage_incarnation)
                is None
            ):
                raise OutputLifecycleConsistencyError(
                    "database output projection is inconsistent"
                )
            try:
                data = session.read_exact_incarnation(
                    self._principal.principal_id,
                    output.output_sha256,
                    output.output_size,
                    output.output_storage_incarnation,
                )
            except GeneratedOutputStoreError as error:
                raise OutputLifecycleConsistencyError(
                    "database output is absent or inconsistent in private CAS"
                ) from error
            if (
                type(data) is not bytes
                or len(data) != output.output_size
                or hashlib.sha256(data).hexdigest() != output.output_sha256
            ):
                raise OutputLifecycleConsistencyError(
                    "private CAS returned inconsistent output bytes"
                )
            return data

    def acknowledge_output(
        self,
        context: AuthorizedServiceRequest,
        *,
        output_version: int,
        output_sha256: str,
    ) -> OutputAckTransition:
        authorized = _route_context(
            context,
            scope=_ACK_SCOPE,
            route_template=_ACK_ROUTE,
            max_body_bytes=MAX_OUTPUT_ACK_BODY_BYTES,
        )
        version = _version(output_version)
        digest = _digest(output_sha256, "output digest")
        transition = self._job_store.acknowledge_output(
            authorized,
            output_version=version,
            output_sha256=digest,
        )
        if (
            type(transition) is not OutputAckTransition
            or type(transition.replayed) is not bool
            or type(transition.object_state) is not str
            or transition.object_state not in {"live", "delete_pending", "deleted"}
        ):
            raise OutputLifecycleConsistencyError(
                "database acknowledgement result is inconsistent"
            )
        self._validate_tombstone(
            transition.tombstone,
            job_id=authorized.job_id,
            output_version=version,
            output_sha256=digest,
        )
        return transition

    def _validate_deletion(self, value: object) -> PendingObjectDeletion:
        if (
            type(value) is not PendingObjectDeletion
            or type(value.owner_principal) is not str
            or value.owner_principal != self._principal.principal_id
            or type(value.output_store_id) is not str
            or _DIGEST.fullmatch(value.output_store_id) is None
            or value.output_store_id != self._output_store_id
            or type(value.output_sha256) is not str
            or _DIGEST.fullmatch(value.output_sha256) is None
            or type(value.output_storage_incarnation) is not str
            or _INCARNATION.fullmatch(value.output_storage_incarnation) is None
            or type(value.output_size) is not int
            or not 1 <= value.output_size <= self._maximum_output_bytes
            or type(value.output_mime) is not str
            or value.output_mime != OUTPUT_MIME
            or type(value.output_cas_key) is not str
            or value.output_cas_key != content_addressed_key(value.output_sha256)
        ):
            raise OutputLifecycleConsistencyError(
                "database deletion projection is inconsistent"
            )
        return value

    def reconcile_deletions(
        self, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[DeletionResolution, ...]:
        selected_limit = _limit(limit)
        results: list[DeletionResolution] = []
        with self._output_store.publication_session() as session:
            if type(session) is not PublicationSession:
                raise OutputLifecycleConsistencyError(
                    "private CAS publication session is invalid"
                )
            deletions = self._job_store.list_pending_object_deletions(
                self._principal,
                output_store_id=self._output_store_id,
                limit=selected_limit,
            )
            if type(deletions) is not tuple or any(
                type(item) is not PendingObjectDeletion for item in deletions
            ):
                raise OutputLifecycleConsistencyError(
                    "database deletion list is invalid"
                )
            for item in deletions:
                deletion = self._validate_deletion(item)
                was_present = False
                exact_absent = False
                try:
                    handle = session.recover_delete_handle_exact(
                        self._principal.principal_id,
                        deletion.output_sha256,
                        deletion.output_size,
                        deletion.output_storage_incarnation,
                    )
                except GeneratedOutputStaleHandle:
                    handle = None
                    exact_absent = True
                except GeneratedOutputStoreError as error:
                    raise OutputLifecycleConsistencyError(
                        "private CAS deletion recovery failed closed"
                    ) from error
                if handle is not None:
                    selected = self._validate_handle(
                        handle,
                        output_sha256=deletion.output_sha256,
                        output_size=deletion.output_size,
                        output_storage_incarnation=(
                            deletion.output_storage_incarnation
                        ),
                    )
                    try:
                        deleted = session.delete(selected)
                    except GeneratedOutputStoreError as error:
                        raise OutputLifecycleConsistencyError(
                            "private CAS deletion failed closed"
                        ) from error
                    if type(deleted) is not bool:
                        raise OutputLifecycleConsistencyError(
                            "private CAS deletion result is invalid"
                        )
                    was_present = deleted
                    try:
                        remaining = session.recover_delete_handle_exact(
                            self._principal.principal_id,
                            deletion.output_sha256,
                            deletion.output_size,
                            deletion.output_storage_incarnation,
                        )
                    except GeneratedOutputStaleHandle:
                        remaining = None
                        exact_absent = True
                    except GeneratedOutputStoreError as error:
                        raise OutputLifecycleConsistencyError(
                            "private CAS deletion verification failed closed"
                        ) from error
                    if remaining is not None:
                        raise OutputLifecycleConsistencyError(
                            "private CAS exact object remains after deletion"
                        )
                    exact_absent = True
                elif not exact_absent:
                    exact_absent = True
                if not exact_absent:
                    raise OutputLifecycleConsistencyError(
                        "private CAS exact absence was not established"
                    )
                finalized = self._job_store.finalize_output_deletion(
                    self._principal, deletion=deletion
                )
                if (
                    type(finalized) is not FinalizedObjectDeletion
                    or finalized.deletion is not deletion
                    or type(finalized.tombstones) is not tuple
                    or type(finalized.replayed) is not bool
                ):
                    raise OutputLifecycleConsistencyError(
                        "database deletion finalization is inconsistent"
                    )
                for tombstone in finalized.tombstones:
                    self._validate_tombstone(
                        tombstone,
                        job_id=None,
                        output_version=None,
                        output_sha256=deletion.output_sha256,
                        required_state="deleted",
                    )
                results.append(
                    DeletionResolution(
                        deletion.output_sha256,
                        deletion.output_storage_incarnation,
                        was_present,
                        finalized.replayed,
                    )
                )
        return tuple(results)


__all__ = [
    "DeletionResolution",
    "MaterializationResolution",
    "MaterializedOutput",
    "OutputLifecycleAuthorizationError",
    "OutputLifecycleConfigurationError",
    "OutputLifecycleConsistencyError",
    "OutputLifecycleError",
    "PUBLICATION_ID_SCHEMA",
    "PlatformOutputLifecycle",
    "deterministic_publication_id",
]
