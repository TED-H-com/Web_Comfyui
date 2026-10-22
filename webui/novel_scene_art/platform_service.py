"""Dormant durable composition behind the platform HTTP controller.

This module implements the :class:`~.platform_http.NovelSceneArtPlatformService`
protocol over the real private stores: the SQLite job store, the canonical
request vault lifecycle, and the generated output lifecycle.  It contains the
last translation seam before HTTP: bounded store and lifecycle failures become
the controller's typed platform exceptions, and authoritative row projections
become the frozen public DTOs.

Like every other module in this package it stays dormant.  ``webui/server.py``
must not import it, constructing it performs no I/O beyond what its already
constructed collaborators did, and it starts with admission closed: ``submit``
refuses work until one bounded reconciliation pass over all four recovery
streams has drained completely (``startup_before_admission_required`` in the
capacity contract).  Reads, cancels, and acknowledgements are drain-side and
are never gated on that pass.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import astuple, dataclass

from .manifests import RenderRelease
from .platform_http import (
    CapabilitySnapshot,
    JobSnapshot,
    OutputAckResult,
    OutputAcknowledgement,
    OwnedBinaryOutput,
    PlatformJobStateConflict,
    PlatformOutputAckConflict,
    PlatformServiceUnavailable,
    PlatformStorageCapacityExhausted,
    PlatformSubmitConflict,
    SubmitResult,
    ValidatedPlatformSubmit,
)
from .platform_output_lifecycle import (
    DeletionResolution,
    MaterializationResolution,
    PlatformOutputLifecycle,
)
from .platform_output_store import GeneratedOutputStore
from .platform_request_lifecycle import (
    PlatformRequestLifecycle,
    PublishedCanonicalRequest,
    RequestDeletionResolution,
    RequestLifecycleStateError,
    RequestPublicationResolution,
)
from .platform_service_auth import AuthorizedServiceRequest
from .platform_store import (
    MAX_LIST_RESULTS,
    OUTPUT_ADOPTED,
    OutputAckTransition,
    OutputTombstone,
    PlatformSceneArtStore,
    SceneArtIdempotencyConflict,
    SceneArtJob,
    SceneArtJobConflict,
    SceneArtOutput,
    SceneArtOutputAckConflict,
    SceneArtRequestPublicationConflict,
    SceneArtResourceNotFound,
    SceneArtStateConflict,
    SceneArtStorageCapacityExhausted,
    StoreCapacity,
)

RECONCILIATION_BATCH_LIMIT = MAX_LIST_RESULTS

_DIGEST = re.compile(r"^[a-f0-9]{64}$", re.ASCII)


class PlatformServiceConfigurationError(RuntimeError):
    """The composition wiring or a call argument is invalid."""


class PlatformServiceConsistencyError(RuntimeError):
    """Composed authorities returned mutually inconsistent state."""


def _resolutions(value: object, expected: type, label: str) -> None:
    if type(value) is not tuple or any(type(item) is not expected for item in value):
        raise PlatformServiceConsistencyError(f"{label} resolutions are inconsistent")


@dataclass(frozen=True, slots=True)
class PlatformReconciliationReport:
    """One bounded recovery pass over all four durable backlog streams."""

    publications: tuple[RequestPublicationResolution, ...]
    request_deletions: tuple[RequestDeletionResolution, ...]
    materializations: tuple[MaterializationResolution, ...]
    output_deletions: tuple[DeletionResolution, ...]
    complete: bool

    def __post_init__(self) -> None:
        _resolutions(self.publications, RequestPublicationResolution, "publication")
        _resolutions(
            self.request_deletions, RequestDeletionResolution, "request deletion"
        )
        _resolutions(
            self.materializations, MaterializationResolution, "materialization"
        )
        _resolutions(self.output_deletions, DeletionResolution, "output deletion")
        if type(self.complete) is not bool:
            raise PlatformServiceConsistencyError(
                "reconciliation completion flag is inconsistent"
            )


class DurableNovelSceneArtPlatformService:
    """Durable ``NovelSceneArtPlatformService`` over the private platform stores."""

    __slots__ = (
        "_admission_open",
        "_capability_digest",
        "_output_lifecycle",
        "_output_store_id",
        "_release_digest",
        "_request_lifecycle",
        "_store",
    )

    def __init__(
        self,
        *,
        job_store: PlatformSceneArtStore,
        request_lifecycle: PlatformRequestLifecycle,
        output_lifecycle: PlatformOutputLifecycle,
        output_store: GeneratedOutputStore,
        release: RenderRelease,
    ) -> None:
        if type(job_store) is not PlatformSceneArtStore:
            raise PlatformServiceConfigurationError("job store is invalid")
        if type(request_lifecycle) is not PlatformRequestLifecycle:
            raise PlatformServiceConfigurationError("request lifecycle is invalid")
        if type(output_lifecycle) is not PlatformOutputLifecycle:
            raise PlatformServiceConfigurationError("output lifecycle is invalid")
        if type(output_store) is not GeneratedOutputStore:
            raise PlatformServiceConfigurationError("output store is invalid")
        if type(release) is not RenderRelease:
            raise PlatformServiceConfigurationError("release is invalid")
        store_id = output_store.store_identity
        capability_digest = release.capability_digest
        release_digest = release.release_digest
        for label, digest in (
            ("output store identity", store_id),
            ("capability digest", capability_digest),
            ("release digest", release_digest),
        ):
            if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
                raise PlatformServiceConfigurationError(f"{label} is invalid")
        capacity = job_store.capacity
        if type(capacity) is not StoreCapacity:
            raise PlatformServiceConfigurationError("store capacity is invalid")
        self._store = job_store
        self._request_lifecycle = request_lifecycle
        self._output_lifecycle = output_lifecycle
        self._output_store_id = store_id
        self._capability_digest = capability_digest
        self._release_digest = release_digest
        self._admission_open = False

    @staticmethod
    def _context(context: object) -> AuthorizedServiceRequest:
        if (
            type(context) is not AuthorizedServiceRequest
            or type(context.principal_id) is not str
            or context.owner_principal != context.principal_id
        ):
            raise PlatformServiceConfigurationError("authorized context is invalid")
        return context

    def _snapshot(self, job: object) -> JobSnapshot:
        if type(job) is not SceneArtJob:
            raise PlatformServiceConsistencyError("job projection is inconsistent")
        available = (
            job.state == OUTPUT_ADOPTED
            and type(job.output_version) is int
            and type(job.output_sha256) is str
        )
        try:
            return JobSnapshot(
                owner_principal=job.owner_principal,
                job_id=job.job_id,
                state=job.state,
                generation_key=job.generation_key,
                canonical_request_sha256=job.canonical_request_sha256,
                output_available=available,
                output_version=job.output_version if available else None,
            )
        except (TypeError, ValueError) as error:
            raise PlatformServiceConsistencyError(
                "job projection is inconsistent"
            ) from error

    @property
    def admission_open(self) -> bool:
        return self._admission_open

    def capability(self, context: AuthorizedServiceRequest) -> CapabilitySnapshot:
        selected = self._context(context)
        if not self._admission_open:
            return CapabilitySnapshot(
                owner_principal=selected.owner_principal,
                ready=False,
                capability_digest=None,
                renderer_release_digest=None,
            )
        return CapabilitySnapshot(
            owner_principal=selected.owner_principal,
            ready=True,
            capability_digest=self._capability_digest,
            renderer_release_digest=self._release_digest,
        )

    def submit(
        self,
        context: AuthorizedServiceRequest,
        submission: ValidatedPlatformSubmit,
    ) -> SubmitResult:
        selected = self._context(context)
        if type(submission) is not ValidatedPlatformSubmit:
            raise PlatformServiceConfigurationError("submission is invalid")
        if not self._admission_open:
            raise PlatformServiceUnavailable(
                "startup reconciliation has not completed"
            )
        try:
            published = self._request_lifecycle.submit(selected, submission)
        except SceneArtStorageCapacityExhausted as error:
            raise PlatformStorageCapacityExhausted(error.code) from error
        except (
            SceneArtIdempotencyConflict,
            SceneArtJobConflict,
            SceneArtRequestPublicationConflict,
        ) as error:
            raise PlatformSubmitConflict(error.code) from error
        except SceneArtStateConflict as error:
            raise PlatformJobStateConflict(error.code) from error
        except RequestLifecycleStateError as error:
            raise PlatformJobStateConflict(
                "request lifecycle state conflict"
            ) from error
        if (
            type(published) is not PublishedCanonicalRequest
            or type(published.created) is not bool
        ):
            raise PlatformServiceConsistencyError(
                "publication result is inconsistent"
            )
        job = self._snapshot(published.job)
        if (
            job.job_id != submission.job_id
            or job.generation_key != submission.generation_key
            or job.canonical_request_sha256 != submission.canonical_request_sha256
        ):
            raise PlatformServiceConsistencyError(
                "published job diverges from its submission"
            )
        return SubmitResult(job=job, created=published.created)

    def read_job(self, context: AuthorizedServiceRequest) -> JobSnapshot | None:
        selected = self._context(context)
        try:
            job = self._store.get_job(selected)
        except SceneArtResourceNotFound:
            return None
        return self._snapshot(job)

    def cancel_job(self, context: AuthorizedServiceRequest) -> JobSnapshot | None:
        selected = self._context(context)
        try:
            job = self._store.cancel_job(selected)
        except SceneArtResourceNotFound:
            return None
        except SceneArtStateConflict as error:
            raise PlatformJobStateConflict(error.code) from error
        return self._snapshot(job)

    def read_output(
        self,
        context: AuthorizedServiceRequest,
        *,
        maximum_bytes: int,
    ) -> OwnedBinaryOutput | None:
        selected = self._context(context)
        if type(maximum_bytes) is not int or maximum_bytes < 1:
            raise PlatformServiceConfigurationError("maximum output bound is invalid")
        try:
            projection = self._store.read_output(
                selected, output_store_id=self._output_store_id
            )
        except SceneArtResourceNotFound:
            return None
        except SceneArtStateConflict as error:
            raise PlatformJobStateConflict(error.code) from error
        if type(projection) is not SceneArtOutput:
            raise PlatformServiceConsistencyError("output projection is inconsistent")
        try:
            data = self._output_lifecycle.read_output(selected)
        except SceneArtResourceNotFound:
            return None
        except SceneArtStateConflict as error:
            raise PlatformJobStateConflict(error.code) from error
        if len(data) > maximum_bytes:
            raise PlatformServiceConsistencyError(
                "output exceeds the platform byte bound"
            )
        if (
            type(data) is not bytes
            or len(data) != projection.output_size
            or hashlib.sha256(data).hexdigest() != projection.output_sha256
        ):
            raise PlatformServiceConsistencyError(
                "output bytes diverge from their projection"
            )
        return OwnedBinaryOutput(
            owner_principal=projection.owner_principal,
            job_id=projection.job_id,
            body=data,
            mime_type=projection.output_mime,
            content_sha256=projection.output_sha256,
            output_version=projection.output_version,
        )

    def acknowledge_output(
        self,
        context: AuthorizedServiceRequest,
        acknowledgement: OutputAcknowledgement,
    ) -> OutputAckResult | None:
        selected = self._context(context)
        if type(acknowledgement) is not OutputAcknowledgement:
            raise PlatformServiceConfigurationError("acknowledgement is invalid")
        try:
            transition = self._output_lifecycle.acknowledge_output(
                selected,
                output_version=acknowledgement.output_version,
                output_sha256=acknowledgement.output_sha256,
            )
        except SceneArtResourceNotFound:
            return None
        except SceneArtOutputAckConflict as error:
            raise PlatformOutputAckConflict(error.code) from error
        except SceneArtStateConflict as error:
            raise PlatformJobStateConflict(error.code) from error
        if type(transition) is not OutputAckTransition:
            raise PlatformServiceConsistencyError(
                "acknowledgement transition is inconsistent"
            )
        tombstone = transition.tombstone
        if type(tombstone) is not OutputTombstone:
            raise PlatformServiceConsistencyError(
                "acknowledgement tombstone is inconsistent"
            )
        try:
            return OutputAckResult(
                owner_principal=tombstone.owner_principal,
                job_id=tombstone.job_id,
                output_sha256=tombstone.output_sha256,
                output_version=tombstone.output_version,
                deletion_state=tombstone.state,
                replayed=transition.replayed,
            )
        except (TypeError, ValueError) as error:
            raise PlatformServiceConsistencyError(
                "acknowledgement result is inconsistent"
            ) from error

    def reconcile(
        self, *, limit: int = RECONCILIATION_BATCH_LIMIT
    ) -> PlatformReconciliationReport:
        """Drain all four recovery streams once, bounded by the row capacity.

        A pass is complete when every stream returned a short batch within its
        derived batch bound, so the whole backlog was enumerated and every
        resolvable item was resolved.  Items the lifecycles deliberately
        retain (for example ``awaiting_request_bytes`` publications, whose
        capacity charge survives restart by contract) count as enumerated:
        they stay charged and individually recoverable, and do not veto
        admission.  A stream that repeats already-seen work without progress,
        or that exceeds its bound, marks the pass incomplete instead.

        A complete pass opens admission; an incomplete pass closes it again.
        The pass itself is drain-side work and is therefore legal at any
        time, including before startup has ever completed and while every
        storage quota is exhausted.
        """

        if type(limit) is not int or not 1 <= limit <= RECONCILIATION_BATCH_LIMIT:
            raise PlatformServiceConfigurationError(
                "reconciliation batch limit is invalid"
            )
        capacity = self._store.capacity
        if type(capacity) is not StoreCapacity:
            raise PlatformServiceConsistencyError("store capacity is inconsistent")
        publications, publications_complete = self._drain(
            self._request_lifecycle.reconcile_publications,
            RequestPublicationResolution,
            row_bound=capacity.metadata_max_request_publication_rows,
            limit=limit,
        )
        request_deletions, request_deletions_complete = self._drain(
            self._request_lifecycle.reconcile_deletions,
            RequestDeletionResolution,
            row_bound=capacity.metadata_max_request_publication_rows,
            limit=limit,
        )
        materializations, materializations_complete = self._drain(
            self._output_lifecycle.reconcile_materializations,
            MaterializationResolution,
            row_bound=capacity.metadata_max_output_materialization_rows,
            limit=limit,
        )
        output_deletions, output_deletions_complete = self._drain(
            self._output_lifecycle.reconcile_deletions,
            DeletionResolution,
            row_bound=capacity.metadata_max_output_tombstone_rows,
            limit=limit,
        )
        report = PlatformReconciliationReport(
            publications=publications,
            request_deletions=request_deletions,
            materializations=materializations,
            output_deletions=output_deletions,
            complete=(
                publications_complete
                and request_deletions_complete
                and materializations_complete
                and output_deletions_complete
            ),
        )
        self._admission_open = report.complete
        return report

    @staticmethod
    def _drain(step, expected: type, *, row_bound: int, limit: int):
        if type(row_bound) is not int or row_bound < 1:
            raise PlatformServiceConsistencyError(
                "reconciliation row bound is inconsistent"
            )
        resolved: list = []
        seen: set = set()
        for _ in range(row_bound // limit + 2):
            batch = step(limit=limit)
            if type(batch) is not tuple or any(
                type(item) is not expected for item in batch
            ):
                raise PlatformServiceConsistencyError(
                    "reconciliation batch is inconsistent"
                )
            resolved.extend(batch)
            if len(batch) < limit:
                return tuple(resolved), True
            keys = {astuple(item) for item in batch}
            if keys <= seen:
                return tuple(resolved), False
            seen.update(keys)
        return tuple(resolved), False


__all__ = [
    "DurableNovelSceneArtPlatformService",
    "PlatformReconciliationReport",
    "PlatformServiceConfigurationError",
    "PlatformServiceConsistencyError",
    "RECONCILIATION_BATCH_LIMIT",
]
