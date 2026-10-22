"""Dormant DB-first coordinator for canonical request publication.

This module is deliberately unrouted. It holds one request-vault publication
session across every SQLite/vault hand-off, so restart reconciliation cannot
observe or delete a request between durable intent creation and exact vault
publication. Raw canonical envelopes exist only in the caller and vault; they
never enter the SQLite ledger or any public result.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from .errors import SceneArtError
from .manifests import RenderRelease
from .platform_http import (
    MAX_PLATFORM_OUTPUT_BYTES,
    ValidatedPlatformSubmit,
    _snapshot_release,
    parse_platform_submit,
)
from .platform_request_vault import (
    PrivateCanonicalRequestVault,
    PublishedRequest,
    RequestPublicationSession,
    RequestVaultError,
    RequestVaultHandle,
)
from .platform_service_account import EXPECTED_SCOPES, SERVICE_PRINCIPAL
from .platform_service_auth import (
    MAX_REQUEST_BODY_BYTES,
    AuthorizedServiceRequest,
    ServicePrincipal,
)
from .platform_store import (
    JOB_STATES,
    MAX_CANONICAL_REQUEST_BYTES,
    MAX_LIST_RESULTS,
    PendingRequestDeletion,
    PlatformSceneArtStore,
    RequestPublication,
    RequestPublicationBeginResult,
    RequestPublicationFinishResult,
    SceneArtJob,
    SceneArtResourceNotFound,
    SceneArtStateConflict,
)


REQUEST_PUBLICATION_ID_SCHEMA = (
    "castle.novel-scene-art-request-publication-id.v1"
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$", re.ASCII)
_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$", re.ASCII)
_DIGEST = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_PUBLICATION = re.compile(r"^reqpub_[a-f0-9]{32}$", re.ASCII)
_INCARNATION = re.compile(r"^reqinc_[a-f0-9]{32}$", re.ASCII)
_SUBMIT_SCOPE = "scene_art.job.submit-own"
_SUBMIT_ROUTE = "/api/internal/novel-scene-art/v1/jobs"


class RequestLifecycleError(RuntimeError):
    """Base class for bounded request lifecycle failures."""


class RequestLifecycleConfigurationError(RequestLifecycleError):
    """Trusted principal, database, or vault configuration is inconsistent."""


class RequestLifecycleAuthorizationError(RequestLifecycleError):
    """The nominal request context does not represent the fixed service."""


class RequestLifecycleConsistencyError(RequestLifecycleError):
    """Durable request metadata and exact private vault state disagree."""


class RequestLifecycleStateError(RequestLifecycleError):
    """The job became terminal while its request was being published."""


@dataclass(frozen=True, slots=True)
class PublishedCanonicalRequest:
    job: SceneArtJob
    publication: RequestPublication
    created: bool
    vault_created: bool


@dataclass(frozen=True, slots=True)
class RequestPublicationResolution:
    job_id: str
    publication_id: str
    outcome: str
    storage_incarnation: str


@dataclass(frozen=True, slots=True)
class RequestDeletionResolution:
    job_id: str
    publication_id: str
    physical_object_was_present: bool
    state: str


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _principal(value: object) -> ServicePrincipal:
    if (
        type(value) is not ServicePrincipal
        or type(value.principal_id) is not str
        or value.principal_id != SERVICE_PRINCIPAL
        or type(value.scopes) is not tuple
        or value.scopes != EXPECTED_SCOPES
        or any(type(scope) is not str for scope in value.scopes)
        or type(value.key_id) is not str
        or _KEY_ID.fullmatch(value.key_id) is None
    ):
        raise RequestLifecycleAuthorizationError(
            "request lifecycle principal is invalid"
        )
    return value


def _submit_context(value: object) -> AuthorizedServiceRequest:
    if (
        type(value) is not AuthorizedServiceRequest
        or type(value.principal_id) is not str
        or value.principal_id != SERVICE_PRINCIPAL
        or type(value.owner_principal) is not str
        or value.owner_principal != SERVICE_PRINCIPAL
        or type(value.key_id) is not str
        or _KEY_ID.fullmatch(value.key_id) is None
        or type(value.scope) is not str
        or value.scope != _SUBMIT_SCOPE
        or type(value.route_template) is not str
        or value.route_template != _SUBMIT_ROUTE
        or value.job_id is not None
        or type(value.max_body_bytes) is not int
        or value.max_body_bytes != MAX_REQUEST_BODY_BYTES
        or type(value.atomic_owner_lookup_required) is not bool
        or value.atomic_owner_lookup_required
    ):
        raise RequestLifecycleAuthorizationError(
            "request lifecycle route authorization is invalid"
        )
    return value


def _submission_shape(value: object) -> ValidatedPlatformSubmit:
    if type(value) is not ValidatedPlatformSubmit:
        raise TypeError("submission must be ValidatedPlatformSubmit")
    assert type(value) is ValidatedPlatformSubmit
    if (
        type(value.canonical_envelope) is not bytes
        or not 1 <= len(value.canonical_envelope) <= MAX_CANONICAL_REQUEST_BYTES
        or type(value.canonical_request_sha256) is not str
        or _DIGEST.fullmatch(value.canonical_request_sha256) is None
        or hashlib.sha256(value.canonical_envelope).hexdigest()
        != value.canonical_request_sha256
        or type(value.request_id) is not str
        or _IDENTIFIER.fullmatch(value.request_id) is None
        or type(value.job_id) is not str
        or _IDENTIFIER.fullmatch(value.job_id) is None
        or any(
            type(item) is not str or _DIGEST.fullmatch(item) is None
            for item in (
                value.generation_key,
                value.scene_pack_hash,
                value.render_plan_hash,
                value.renderer_release_digest,
            )
        )
        or type(value.variation_index) is not int
        or value.variation_index < 0
        or type(value.seed) is not int
        or value.seed < 0
        or type(value.max_output_bytes) is not int
        or value.max_output_bytes < 1
        or type(value.platform_enforced_max_output_bytes) is not int
        or not 1
        <= value.platform_enforced_max_output_bytes
        <= value.max_output_bytes
    ):
        raise RequestLifecycleConsistencyError(
            "validated submission projection is inconsistent"
        )
    return value


def deterministic_request_publication_id(
    *,
    owner_principal: str,
    job_id: str,
    request_id: str,
    generation_key: str,
    canonical_request_sha256: str,
    canonical_request_size: int,
    request_vault_id: str,
    scene_pack_sha256: str,
    render_plan_sha256: str,
    renderer_release_sha256: str,
) -> str:
    owner = _identifier(owner_principal, "owner principal")
    job = _identifier(job_id, "job id")
    request = _identifier(request_id, "request id")
    generation = _digest(generation_key, "generation key")
    request_digest = _digest(
        canonical_request_sha256, "canonical request digest"
    )
    vault_id = _digest(request_vault_id, "request vault identity")
    scene = _digest(scene_pack_sha256, "scene pack digest")
    plan = _digest(render_plan_sha256, "render plan digest")
    release = _digest(renderer_release_sha256, "renderer release digest")
    if (
        type(canonical_request_size) is not int
        or not 1 <= canonical_request_size <= MAX_CANONICAL_REQUEST_BYTES
    ):
        raise ValueError("canonical request size is invalid")
    encoded = json.dumps(
        {
            "canonical_request_sha256": request_digest,
            "canonical_request_size": canonical_request_size,
            "generation_key": generation,
            "job_id": job,
            "owner_principal": owner,
            "render_plan_sha256": plan,
            "renderer_release_sha256": release,
            "request_id": request,
            "request_vault_id": vault_id,
            "scene_pack_sha256": scene,
            "schema_version": REQUEST_PUBLICATION_ID_SCHEMA,
        },
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "reqpub_" + hashlib.sha256(encoded).hexdigest()[:32]


class PlatformRequestLifecycle:
    """Exact bridge from authenticated submit envelopes to the private vault."""

    __slots__ = (
        "_max_output_bytes",
        "_principal",
        "_job_store",
        "_release",
        "_vault",
        "_vault_id",
    )

    def __init__(
        self,
        *,
        principal: ServicePrincipal,
        job_store: PlatformSceneArtStore,
        request_vault: PrivateCanonicalRequestVault,
        release: RenderRelease,
        max_output_bytes: int = MAX_PLATFORM_OUTPUT_BYTES,
    ) -> None:
        selected_principal = _principal(principal)
        if type(job_store) is not PlatformSceneArtStore:
            raise TypeError("job_store must be PlatformSceneArtStore")
        if type(request_vault) is not PrivateCanonicalRequestVault:
            raise TypeError(
                "request_vault must be PrivateCanonicalRequestVault"
            )
        if (
            type(max_output_bytes) is not int
            or not 1 <= max_output_bytes <= MAX_PLATFORM_OUTPUT_BYTES
        ):
            raise ValueError("max_output_bytes is invalid")
        try:
            selected_release = _snapshot_release(release)
        except (TypeError, ValueError) as error:
            raise RequestLifecycleConfigurationError(
                "render release binding is invalid"
            ) from error
        output = selected_release.public_release.get("output")
        if not isinstance(output, Mapping):
            raise RequestLifecycleConfigurationError(
                "render release output binding is invalid"
            )
        release_maximum = output.get("max_bytes")
        if (
            type(release_maximum) is not int
            or release_maximum < 1
            or output.get("mime_type") != "image/png"
        ):
            raise RequestLifecycleConfigurationError(
                "render release output binding is invalid"
            )
        vault_id = request_vault.store_identity
        _digest(vault_id, "request vault identity")
        retained = job_store.bind_request_vault(
            selected_principal, request_vault_id=vault_id
        )
        if type(retained) is not str or retained != vault_id:
            raise RequestLifecycleConfigurationError(
                "request vault identity binding is inconsistent"
            )
        self._principal = selected_principal
        self._job_store = job_store
        self._release = selected_release
        self._max_output_bytes = min(max_output_bytes, release_maximum)
        self._vault = request_vault
        self._vault_id = vault_id

    @property
    def request_vault_identity(self) -> str:
        return self._vault_id

    def _submission(self, value: object) -> ValidatedPlatformSubmit:
        """Re-establish the HTTP parser's release-bound semantic authority.

        ``ValidatedPlatformSubmit`` is a useful transport projection, but its
        constructor intentionally cannot prove that its scalar fields were
        derived from its private canonical bytes.  Reparse those exact bytes
        against this coordinator's detached release and output limit before
        any vault lock or SQLite write, then compare every projected field.
        """

        selected = _submission_shape(value)
        try:
            parsed = parse_platform_submit(
                selected.canonical_envelope,
                release=self._release,
                platform_enforced_max_output_bytes=self._max_output_bytes,
            )
        except (SceneArtError, TypeError, ValueError) as error:
            raise RequestLifecycleConsistencyError(
                "validated submission canonical evidence is invalid"
            ) from error
        if type(parsed) is not ValidatedPlatformSubmit or any(
            getattr(parsed, name) != getattr(selected, name)
            for name in (
                "canonical_envelope",
                "canonical_request_sha256",
                "request_id",
                "job_id",
                "generation_key",
                "scene_pack_hash",
                "render_plan_hash",
                "renderer_release_digest",
                "variation_index",
                "seed",
                "max_output_bytes",
                "platform_enforced_max_output_bytes",
            )
        ):
            raise RequestLifecycleConsistencyError(
                "validated submission projection is not canonically bound"
            )
        return selected

    def _publication_id(
        self, submission: ValidatedPlatformSubmit
    ) -> str:
        return deterministic_request_publication_id(
            owner_principal=self._principal.principal_id,
            job_id=submission.job_id,
            request_id=submission.request_id,
            generation_key=submission.generation_key,
            canonical_request_sha256=submission.canonical_request_sha256,
            canonical_request_size=len(submission.canonical_envelope),
            request_vault_id=self._vault_id,
            scene_pack_sha256=submission.scene_pack_hash,
            render_plan_sha256=submission.render_plan_hash,
            renderer_release_sha256=submission.renderer_release_digest,
        )

    def _validate_job(
        self, value: object, *, job_id: str
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
        required_ints = (
            "canonical_request_size",
            "reconcile_attempts",
            "created_at",
            "updated_at",
        )
        optional_ints = (
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
                type(getattr(value, name)) is not str
                for name in required_strings
            )
            or any(
                item is not None and type(item) is not str
                for item in (
                    getattr(value, name) for name in optional_strings
                )
            )
            or any(
                type(getattr(value, name)) is not int
                for name in required_ints
            )
            or any(
                item is not None and type(item) is not int
                for item in (getattr(value, name) for name in optional_ints)
            )
            or type(value.reconcile_required) is not bool
            or value.owner_principal != self._principal.principal_id
            or value.job_id != job_id
            or value.state not in JOB_STATES
            or _DIGEST.fullmatch(value.generation_key) is None
            or _DIGEST.fullmatch(value.canonical_request_sha256) is None
            or _DIGEST.fullmatch(value.request_vault_id) is None
            or _INCARNATION.fullmatch(value.request_storage_incarnation) is None
            or _DIGEST.fullmatch(value.scene_pack_sha256) is None
            or _DIGEST.fullmatch(value.render_plan_sha256) is None
            or _DIGEST.fullmatch(value.renderer_release_sha256) is None
            or value.created_at < 0
            or value.updated_at < value.created_at
        ):
            raise RequestLifecycleConsistencyError(
                "database job projection is inconsistent"
            )
        return value

    def _validate_publication(
        self,
        value: object,
        *,
        job_id: str,
        publication_id: str,
        submission: ValidatedPlatformSubmit | None = None,
    ) -> RequestPublication:
        timestamp_names = (
            "cancelled_at",
            "published_at",
            "delete_pending_at",
            "deleted_at",
        )
        if (
            type(value) is not RequestPublication
            or any(
                type(getattr(value, name)) is not str
                for name in (
                    "owner_principal",
                    "job_id",
                    "publication_id",
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
            )
            or type(value.canonical_request_size) is not int
            or not 1
            <= value.canonical_request_size
            <= MAX_CANONICAL_REQUEST_BYTES
            or type(value.created_at) is not int
            or type(value.updated_at) is not int
            or not 0 <= value.created_at <= value.updated_at
            or any(
                item is not None and type(item) is not int
                for item in (getattr(value, name) for name in timestamp_names)
            )
            or value.owner_principal != self._principal.principal_id
            or value.job_id != job_id
            or value.publication_id != publication_id
            or _PUBLICATION.fullmatch(value.publication_id) is None
            or value.request_vault_id != self._vault_id
            or _DIGEST.fullmatch(value.generation_key) is None
            or _DIGEST.fullmatch(value.canonical_request_sha256) is None
            or _INCARNATION.fullmatch(value.request_storage_incarnation) is None
            or _DIGEST.fullmatch(value.scene_pack_sha256) is None
            or _DIGEST.fullmatch(value.render_plan_sha256) is None
            or _DIGEST.fullmatch(value.renderer_release_sha256) is None
            or value.state
            not in {"publish_intent", "live", "delete_pending", "deleted"}
        ):
            raise RequestLifecycleConsistencyError(
                "database request publication projection is inconsistent"
            )
        if submission is not None and (
            value.request_id != submission.request_id
            or value.generation_key != submission.generation_key
            or value.canonical_request_sha256
            != submission.canonical_request_sha256
            or value.canonical_request_size != len(submission.canonical_envelope)
            or value.scene_pack_sha256 != submission.scene_pack_hash
            or value.render_plan_sha256 != submission.render_plan_hash
            or value.renderer_release_sha256
            != submission.renderer_release_digest
        ):
            raise RequestLifecycleConsistencyError(
                "database request publication does not match submission"
            )
        return value

    def _validate_handle(
        self, value: object, publication: RequestPublication
    ) -> RequestVaultHandle:
        if (
            type(value) is not RequestVaultHandle
            or type(value.locator) is not str
            or not value.locator.startswith("req_")
            or len(value.locator) != 68
            or type(value.canonical_request_sha256) is not str
            or value.canonical_request_sha256
            != publication.canonical_request_sha256
            or type(value.byte_size) is not int
            or value.byte_size != publication.canonical_request_size
            or type(value.storage_incarnation) is not str
            or value.storage_incarnation
            != publication.request_storage_incarnation
            or type(value._owner_principal) is not str
            or value._owner_principal != self._principal.principal_id
            or type(value._job_id) is not str
            or value._job_id != publication.job_id
            or type(value._generation_key) is not str
            or value._generation_key != publication.generation_key
            or type(value._store_id) is not str
            or value._store_id != self._vault_id
            or type(value._auth_tag) is not str
            or _DIGEST.fullmatch(value._auth_tag) is None
        ):
            raise RequestLifecycleConsistencyError(
                "private vault returned an inconsistent exact handle"
            )
        return value

    def _begin(
        self,
        context: AuthorizedServiceRequest,
        submission: ValidatedPlatformSubmit,
        publication_id: str,
    ) -> RequestPublicationBeginResult:
        result = self._job_store.begin_request_publication(
            context,
            job_id=submission.job_id,
            publication_id=publication_id,
            request_id=submission.request_id,
            generation_key=submission.generation_key,
            canonical_request_sha256=submission.canonical_request_sha256,
            canonical_request_size=len(submission.canonical_envelope),
            request_vault_id=self._vault_id,
            scene_pack_sha256=submission.scene_pack_hash,
            render_plan_sha256=submission.render_plan_hash,
            renderer_release_sha256=submission.renderer_release_digest,
        )
        if (
            type(result) is not RequestPublicationBeginResult
            or type(result.created) is not bool
        ):
            raise RequestLifecycleConsistencyError(
                "database request begin result is inconsistent"
            )
        publication = self._validate_publication(
            result.publication,
            job_id=submission.job_id,
            publication_id=publication_id,
            submission=submission,
        )
        job = self._validate_job(result.job, job_id=submission.job_id)
        if (
            job.request_storage_incarnation
            != publication.request_storage_incarnation
            or job.generation_key != publication.generation_key
            or job.canonical_request_sha256
            != publication.canonical_request_sha256
        ):
            raise RequestLifecycleConsistencyError(
                "database request begin bindings are inconsistent"
            )
        return result

    def _probe(
        self,
        session: RequestPublicationSession,
        publication: RequestPublication,
    ) -> RequestVaultHandle | None:
        try:
            handle = session.probe_exact(
                self._principal,
                job_id=publication.job_id,
                generation_key=publication.generation_key,
                declared_sha256=publication.canonical_request_sha256,
                byte_size=publication.canonical_request_size,
                storage_incarnation=publication.request_storage_incarnation,
            )
        except RequestVaultError as error:
            raise RequestLifecycleConsistencyError(
                "private vault exact probe failed closed"
            ) from error
        if handle is None:
            return None
        return self._validate_handle(handle, publication)

    def _finish(
        self, publication: RequestPublication
    ) -> RequestPublicationFinishResult:
        result = self._job_store.finish_request_publication(
            self._principal, publication=publication
        )
        if (
            type(result) is not RequestPublicationFinishResult
            or type(result.created) is not bool
        ):
            raise RequestLifecycleConsistencyError(
                "database request finish result is inconsistent"
            )
        completed = self._validate_publication(
            result.publication,
            job_id=publication.job_id,
            publication_id=publication.publication_id,
        )
        job = self._validate_job(result.job, job_id=publication.job_id)
        if (
            completed.state != "live"
            or completed.request_storage_incarnation
            != publication.request_storage_incarnation
            or job.request_storage_incarnation
            != publication.request_storage_incarnation
        ):
            raise RequestLifecycleConsistencyError(
                "database request finish binding is inconsistent"
            )
        return result

    def _validate_deletion(
        self, value: object
    ) -> PendingRequestDeletion:
        if (
            type(value) is not PendingRequestDeletion
            or any(
                type(getattr(value, name)) is not str
                for name in (
                    "owner_principal",
                    "job_id",
                    "publication_id",
                    "request_vault_id",
                    "generation_key",
                    "canonical_request_sha256",
                    "request_storage_incarnation",
                )
            )
            or type(value.canonical_request_size) is not int
            or not 1
            <= value.canonical_request_size
            <= MAX_CANONICAL_REQUEST_BYTES
            or value.owner_principal != self._principal.principal_id
            or value.request_vault_id != self._vault_id
            or _IDENTIFIER.fullmatch(value.job_id) is None
            or _PUBLICATION.fullmatch(value.publication_id) is None
            or _DIGEST.fullmatch(value.generation_key) is None
            or _DIGEST.fullmatch(value.canonical_request_sha256) is None
            or _INCARNATION.fullmatch(value.request_storage_incarnation) is None
        ):
            raise RequestLifecycleConsistencyError(
                "database request deletion projection is inconsistent"
            )
        return value

    def _delete_one(
        self,
        session: RequestPublicationSession,
        deletion: PendingRequestDeletion,
    ) -> RequestDeletionResolution:
        selected = self._validate_deletion(deletion)
        try:
            was_present = session.delete_exact(
                self._principal,
                job_id=selected.job_id,
                generation_key=selected.generation_key,
                declared_sha256=selected.canonical_request_sha256,
                byte_size=selected.canonical_request_size,
                storage_incarnation=selected.request_storage_incarnation,
            )
        except RequestVaultError as error:
            raise RequestLifecycleConsistencyError(
                "private vault exact deletion failed closed"
            ) from error
        if type(was_present) is not bool:
            raise RequestLifecycleConsistencyError(
                "private vault deletion result is inconsistent"
            )
        finalized = self._job_store.finalize_request_deletion(
            self._principal, deletion=selected
        )
        publication = self._validate_publication(
            finalized,
            job_id=selected.job_id,
            publication_id=selected.publication_id,
        )
        if (
            publication.state != "deleted"
            or publication.request_storage_incarnation
            != selected.request_storage_incarnation
        ):
            raise RequestLifecycleConsistencyError(
                "database request deletion finalization is inconsistent"
            )
        return RequestDeletionResolution(
            selected.job_id,
            selected.publication_id,
            was_present,
            publication.state,
        )

    def submit(
        self,
        context: AuthorizedServiceRequest,
        submission: ValidatedPlatformSubmit,
    ) -> PublishedCanonicalRequest:
        authorized = _submit_context(context)
        selected = self._submission(submission)
        publication_id = self._publication_id(selected)
        with self._vault.publication_session() as session:
            if type(session) is not RequestPublicationSession:
                raise RequestLifecycleConsistencyError(
                    "private vault publication session is invalid"
                )
            begun = self._begin(authorized, selected, publication_id)
            publication = begun.publication
            handle = self._probe(session, publication)
            vault_created = False
            if publication.state == "publish_intent":
                if begun.job.state != "submit_intent":
                    raise RequestLifecycleConsistencyError(
                        "pending request job state is inconsistent"
                    )
                if handle is None:
                    try:
                        published = session.publish_reserved(
                            authorized,
                            canonical_envelope=selected.canonical_envelope,
                            declared_sha256=selected.canonical_request_sha256,
                            job_id=selected.job_id,
                            generation_key=selected.generation_key,
                            storage_incarnation=(
                                publication.request_storage_incarnation
                            ),
                        )
                    except RequestVaultError as error:
                        raise RequestLifecycleConsistencyError(
                            "private vault request publication failed closed"
                        ) from error
                    if (
                        type(published) is not PublishedRequest
                        or type(published.created) is not bool
                    ):
                        raise RequestLifecycleConsistencyError(
                            "private vault publication result is inconsistent"
                        )
                    handle = self._validate_handle(
                        published.handle, publication
                    )
                    vault_created = published.created
            elif publication.state == "live":
                if handle is None:
                    raise RequestLifecycleConsistencyError(
                        "live database request is absent from the private vault"
                    )
            else:
                raise RequestLifecycleStateError(
                    "request publication is already terminal"
                )
            if handle is None:
                raise RequestLifecycleConsistencyError(
                    "request publication did not produce an exact handle"
                )
            try:
                finished = self._finish(publication)
            except SceneArtStateConflict as error:
                try:
                    deletion = self._job_store.get_pending_request_deletion(
                        self._principal, job_id=publication.job_id
                    )
                except SceneArtResourceNotFound:
                    raise RequestLifecycleConsistencyError(
                        "request publication changed state inconsistently"
                    ) from error
                self._delete_one(session, deletion)
                raise RequestLifecycleStateError(
                    "request publication was cancelled"
                ) from error
            return PublishedCanonicalRequest(
                job=self._validate_job(
                    finished.job, job_id=publication.job_id
                ),
                publication=self._validate_publication(
                    finished.publication,
                    job_id=publication.job_id,
                    publication_id=publication.publication_id,
                ),
                created=begun.created,
                vault_created=vault_created,
            )

    def reconcile_publications(
        self, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[RequestPublicationResolution, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError("limit is invalid")
        resolutions: list[RequestPublicationResolution] = []
        with self._vault.publication_session() as session:
            if type(session) is not RequestPublicationSession:
                raise RequestLifecycleConsistencyError(
                    "private vault publication session is invalid"
                )
            publications = self._job_store.list_request_publications(
                self._principal,
                request_vault_id=self._vault_id,
                limit=limit,
            )
            if type(publications) is not tuple or any(
                type(item) is not RequestPublication for item in publications
            ):
                raise RequestLifecycleConsistencyError(
                    "database request publication list is inconsistent"
                )
            for item in publications:
                expected_id = deterministic_request_publication_id(
                    owner_principal=self._principal.principal_id,
                    job_id=item.job_id,
                    request_id=item.request_id,
                    generation_key=item.generation_key,
                    canonical_request_sha256=item.canonical_request_sha256,
                    canonical_request_size=item.canonical_request_size,
                    request_vault_id=item.request_vault_id,
                    scene_pack_sha256=item.scene_pack_sha256,
                    render_plan_sha256=item.render_plan_sha256,
                    renderer_release_sha256=item.renderer_release_sha256,
                )
                publication = self._validate_publication(
                    item,
                    job_id=item.job_id,
                    publication_id=expected_id,
                )
                handle = self._probe(session, publication)
                if handle is None:
                    resolutions.append(
                        RequestPublicationResolution(
                            publication.job_id,
                            publication.publication_id,
                            "awaiting_request_bytes",
                            publication.request_storage_incarnation,
                        )
                    )
                    continue
                try:
                    finished = self._finish(publication)
                except SceneArtStateConflict as error:
                    try:
                        deletion = self._job_store.get_pending_request_deletion(
                            self._principal, job_id=publication.job_id
                        )
                    except SceneArtResourceNotFound:
                        raise RequestLifecycleConsistencyError(
                            "request reconciliation changed state inconsistently"
                        ) from error
                    self._delete_one(session, deletion)
                    outcome = "cancelled_deleted"
                else:
                    self._validate_job(
                        finished.job, job_id=publication.job_id
                    )
                    outcome = "published"
                resolutions.append(
                    RequestPublicationResolution(
                        publication.job_id,
                        publication.publication_id,
                        outcome,
                        publication.request_storage_incarnation,
                    )
                )
        return tuple(resolutions)

    def reconcile_deletions(
        self, *, limit: int = MAX_LIST_RESULTS
    ) -> tuple[RequestDeletionResolution, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError("limit is invalid")
        with self._vault.publication_session() as session:
            if type(session) is not RequestPublicationSession:
                raise RequestLifecycleConsistencyError(
                    "private vault publication session is invalid"
                )
            deletions = self._job_store.list_pending_request_deletions(
                self._principal,
                request_vault_id=self._vault_id,
                limit=limit,
            )
            if type(deletions) is not tuple or any(
                type(item) is not PendingRequestDeletion for item in deletions
            ):
                raise RequestLifecycleConsistencyError(
                    "database request deletion list is inconsistent"
                )
            return tuple(
                self._delete_one(session, item) for item in deletions
            )


__all__ = [
    "PlatformRequestLifecycle",
    "PublishedCanonicalRequest",
    "REQUEST_PUBLICATION_ID_SCHEMA",
    "RequestDeletionResolution",
    "RequestLifecycleAuthorizationError",
    "RequestLifecycleConfigurationError",
    "RequestLifecycleConsistencyError",
    "RequestLifecycleError",
    "RequestLifecycleStateError",
    "RequestPublicationResolution",
    "deterministic_request_publication_id",
]
