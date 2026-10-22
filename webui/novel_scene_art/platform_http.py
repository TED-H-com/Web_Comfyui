"""Dormant, framework-neutral HTTP controller for Novel Scene Art.

The controller starts *after* :class:`ServiceAccountRouteAuthorizer` has
authenticated an exact local route.  It deliberately has no server, database,
network, renderer, GPU, or filesystem integration.  A later route adapter must
pass the same method, path, query string, and body that were authorized.

Only typed public projections cross this boundary.  In particular, a service
implementation cannot return arbitrary mappings containing prompts, paths, or
credentials.  Existing resources are addressed by the single
``(owner_principal, job_id)`` key returned by
``AuthorizedServiceRequest.owned_resource_key()``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .contracts import parse_claim, strict_json
from .errors import SceneArtError
from .manifests import RenderRelease, canonical_json
from .platform_service_account import (
    ACK_SCHEMA_ID,
    EXPECTED_ACK_SCHEMA_DEFINITION,
    EXPECTED_ACK_SCHEMA_SHA256,
    EXPECTED_STATE_VALUES,
    EXPECTED_SUBMIT_SCHEMA_DEFINITION,
    EXPECTED_SUBMIT_SCHEMA_SHA256,
    SERVICE_PRINCIPAL,
    SUBMIT_SCHEMA_ID,
)
from .platform_service_auth import (
    AuthorizedServiceRequest,
    ROUTE_RULES,
    ServiceRequestDenied,
)
from .protocol import RENDER_REQUEST_SCHEMA, WORKFLOW_CONTRACT_REVISION


CAPABILITY_RESPONSE_SCHEMA = "castle.novel-scene-art-platform-capability.v1"
JOB_RESPONSE_SCHEMA = "castle.novel-scene-art-platform-job.v1"
OUTPUT_ACK_RESPONSE_SCHEMA = "castle.novel-scene-art-output-ack-result.v1"
MAX_PUBLIC_JSON_BYTES = 16 * 1024
MAX_PLATFORM_OUTPUT_BYTES = 12 * 1024 * 1024

_HASH = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$", re.ASCII)
_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$", re.ASCII)
_SUBMIT_KEYS = frozenset(EXPECTED_SUBMIT_SCHEMA_DEFINITION["root_exact_fields"])
_RENDER_REQUEST_KEYS = frozenset(
    EXPECTED_SUBMIT_SCHEMA_DEFINITION["render_request_exact_fields"]
)
_ACK_KEYS = frozenset(EXPECTED_ACK_SCHEMA_DEFINITION["root_exact_fields"])
_STATES = frozenset(EXPECTED_STATE_VALUES.values())
_RULES_BY_TEMPLATE = {rule.path_template: rule for rule in ROUTE_RULES}

_JSON_HEADERS = MappingProxyType(
    {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "Content-Type": "application/json; charset=utf-8",
        "X-Content-Type-Options": "nosniff",
    }
)


class _InvalidRequest(ValueError):
    pass


class _InvalidAuthorizedContext(PermissionError):
    pass


class PlatformSubmitConflict(RuntimeError):
    """The generation key exists with a different canonical request digest."""


class PlatformOutputAckConflict(RuntimeError):
    """The acknowledged digest or version does not match the adopted output."""


class PlatformJobStateConflict(RuntimeError):
    """The requested owner-scoped job transition is not currently allowed."""


class PlatformServiceUnavailable(RuntimeError):
    """The dormant service boundary cannot currently perform an operation."""


class PlatformStorageCapacityExhausted(RuntimeError):
    """A durable storage quota required by the operation is exhausted."""


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _require_digest(value: object, label: str) -> str:
    if not _is_digest(value):
        raise ValueError(f"{label} is invalid")
    return value


def _require_public_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _PUBLIC_ID.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _require_output_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise ValueError("output_version is invalid")
    return value


def _freeze_json(value: object) -> object:
    """Recursively freeze a value already round-tripped through canonical JSON."""

    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _release_integrity(release: RenderRelease) -> None:
    """Reject mutable or forged release views whose retained hashes no longer bind."""

    if type(release) is not RenderRelease:
        raise TypeError("release must be an exact RenderRelease")
    if (
        hashlib.sha256(canonical_json(release.public_release)).hexdigest()
        != _require_digest(release.release_digest, "renderer_release_digest")
        or hashlib.sha256(canonical_json(release.capability)).hexdigest()
        != _require_digest(release.capability_digest, "capability_digest")
    ):
        raise ValueError("release digest binding is invalid")

    public = release.public_release
    capability = release.capability
    if not isinstance(public, Mapping) or not isinstance(capability, Mapping):
        raise ValueError("release public binding is invalid")
    identity_manifest = public.get("identity_manifest")
    if (
        not isinstance(identity_manifest, Mapping)
        or release.profile_id != public.get("profile_id")
        or capability.get("renderer_release_digest") != release.release_digest
        or capability.get("profile_id") != public.get("profile_id")
    ):
        raise ValueError("release public binding is invalid")

    for key in (
        "workflow_contract_revision",
        "adapter",
        "workflow",
        "compiler",
        "model_manifest",
        "template_catalog",
        "screening",
        "max_identity_slots",
        "seed_policy",
        "output",
    ):
        if capability.get(key) != public.get(key):
            raise ValueError("release capability binding is invalid")
    capability_identity = capability.get("identity_manifest")
    if not isinstance(capability_identity, Mapping) or dict(capability_identity) != {
        "id": identity_manifest.get("id"),
        "revision": identity_manifest.get("revision"),
        "manifest_sha256": identity_manifest.get("manifest_sha256"),
    }:
        raise ValueError("release identity capability binding is invalid")

    personas = identity_manifest.get("personas")
    if not isinstance(personas, list):
        raise ValueError("release public identities are invalid")
    public_identities: dict[tuple[str, str], dict[str, object]] = {}
    for item in personas:
        if not isinstance(item, dict):
            raise ValueError("release public identity is invalid")
        persona_id = item.get("persona_id")
        revision = item.get("identity_pack_revision")
        if not isinstance(persona_id, str) or not isinstance(revision, str):
            raise ValueError("release public identity key is invalid")
        key = (persona_id, revision)
        if key in public_identities:
            raise ValueError("release public identities are duplicated")
        public_identities[key] = dict(item)
    if set(release.identities) != set(public_identities):
        raise ValueError("release identity lookup is inconsistent")
    for key, identity in release.identities.items():
        if (
            getattr(identity, "persona_id", None) != key[0]
            or getattr(identity, "revision", None) != key[1]
            or not isinstance(getattr(identity, "public_identity", None), Mapping)
            or dict(identity.public_identity) != public_identities[key]
        ):
            raise ValueError("release identity lookup binding is invalid")


def _snapshot_release(release: RenderRelease) -> RenderRelease:
    """Detach the controller/parser from caller-owned mutable release mappings."""

    if type(release) is not RenderRelease:
        raise TypeError("release must be an exact RenderRelease")
    try:
        snapshot = copy.deepcopy(release)
    except Exception as error:
        raise ValueError("release snapshot failed") from error
    _release_integrity(snapshot)
    return snapshot


def _job_public_dict(job: JobSnapshot) -> dict[str, object]:
    """Project only the fixed public fields of an exact internal job value."""

    if type(job) is not JobSnapshot:
        raise ValueError("service returned an invalid job")
    return {
        "schema_version": JOB_RESPONSE_SCHEMA,
        "job_id": job.job_id,
        "state": job.state,
        "generation_key": job.generation_key,
        "canonical_request_sha256": job.canonical_request_sha256,
        "output_available": job.output_available,
        "output_version": job.output_version,
    }


def _output_ack_public_dict(result: OutputAckResult) -> dict[str, object]:
    """Project only the fixed public fields of an exact acknowledgement value."""

    if type(result) is not OutputAckResult:
        raise ValueError("service returned an invalid acknowledgement")
    return {
        "schema_version": OUTPUT_ACK_RESPONSE_SCHEMA,
        "job_id": result.job_id,
        "output_sha256": result.output_sha256,
        "output_version": result.output_version,
        "deletion_state": result.deletion_state,
        "replayed": result.replayed,
    }


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    """Small JSON response value independent of a web framework."""

    status: int
    body: Mapping[str, Any]
    headers: Mapping[str, str] = field(default_factory=lambda: _JSON_HEADERS)
    body_bytes: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("status is invalid")
        if not isinstance(self.body, Mapping):
            raise TypeError("body must be a mapping")
        encoded = canonical_json(dict(self.body))
        if len(encoded) > MAX_PUBLIC_JSON_BYTES:
            raise ValueError("public JSON response is too large")
        decoded = json.loads(encoded.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("public JSON response is invalid")
        frozen = _freeze_json(decoded)
        if not isinstance(frozen, Mapping):
            raise ValueError("public JSON response is invalid")
        object.__setattr__(self, "body", frozen)
        object.__setattr__(self, "body_bytes", encoded)
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class BinaryHttpResponse:
    """Digest-bound private output response independent of a web framework."""

    status: int
    body: bytes
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.status != 200 or not isinstance(self.body, bytes):
            raise ValueError("binary response is invalid")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Only the safe, release-bound capability fields exposed to the client."""

    owner_principal: str = field(repr=False)
    ready: bool
    capability_digest: str | None
    renderer_release_digest: str | None

    def __post_init__(self) -> None:
        _require_public_id(self.owner_principal, "owner_principal")
        if type(self.ready) is not bool:
            raise ValueError("ready is invalid")
        for value, label in (
            (self.capability_digest, "capability_digest"),
            (self.renderer_release_digest, "renderer_release_digest"),
        ):
            if value is not None:
                _require_digest(value, label)
        if self.ready and (
            self.capability_digest is None or self.renderer_release_digest is None
        ):
            raise ValueError("ready capability is not release-bound")


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Bounded public projection of one owner-scoped durable job."""

    owner_principal: str = field(repr=False)
    job_id: str
    state: str
    generation_key: str
    canonical_request_sha256: str
    output_available: bool = False
    output_version: int | None = None

    def __post_init__(self) -> None:
        _require_public_id(self.owner_principal, "owner_principal")
        _require_public_id(self.job_id, "job_id")
        if self.state not in _STATES:
            raise ValueError("state is invalid")
        _require_digest(self.generation_key, "generation_key")
        _require_digest(self.canonical_request_sha256, "canonical_request_sha256")
        if type(self.output_available) is not bool:
            raise ValueError("output_available is invalid")
        if self.output_available:
            _require_output_version(self.output_version)
        elif self.output_version is not None:
            raise ValueError("unavailable output cannot have a version")


@dataclass(frozen=True, slots=True)
class ValidatedPlatformSubmit:
    """Canonical submit evidence plus the existing v2 semantic parse result."""

    canonical_envelope: bytes = field(repr=False)
    canonical_request_sha256: str
    request_id: str
    job_id: str
    generation_key: str
    scene_pack_hash: str
    render_plan_hash: str
    renderer_release_digest: str
    variation_index: int
    seed: int
    max_output_bytes: int
    platform_enforced_max_output_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_envelope, bytes) or not self.canonical_envelope:
            raise ValueError("canonical envelope is invalid")
        digest = hashlib.sha256(self.canonical_envelope).hexdigest()
        if digest != _require_digest(
            self.canonical_request_sha256, "canonical_request_sha256"
        ):
            raise ValueError("canonical request digest does not match")
        _require_public_id(self.request_id, "request_id")
        _require_public_id(self.job_id, "job_id")
        for value, label in (
            (self.generation_key, "generation_key"),
            (self.scene_pack_hash, "scene_pack_hash"),
            (self.render_plan_hash, "render_plan_hash"),
            (self.renderer_release_digest, "renderer_release_digest"),
        ):
            _require_digest(value, label)
        if (
            type(self.variation_index) is not int
            or self.variation_index < 0
            or type(self.seed) is not int
            or self.seed < 0
            or type(self.max_output_bytes) is not int
            or self.max_output_bytes <= 0
            or type(self.platform_enforced_max_output_bytes) is not int
            or self.platform_enforced_max_output_bytes <= 0
            or self.platform_enforced_max_output_bytes > self.max_output_bytes
        ):
            raise ValueError("render request semantics are invalid")


@dataclass(frozen=True, slots=True)
class SubmitResult:
    job: JobSnapshot
    created: bool

    def __post_init__(self) -> None:
        if type(self.job) is not JobSnapshot or type(self.created) is not bool:
            raise ValueError("submit result is invalid")


@dataclass(frozen=True, slots=True)
class OwnedBinaryOutput:
    """An already-adopted owner output with persisted digest and version.

    A concrete adapter must construct this only from the owner-private output
    store after that store has validated the exact PNG profile.  This HTTP
    controller deliberately rechecks binding, length, MIME, and digest without
    duplicating the output store's PNG decoder.
    """

    owner_principal: str = field(repr=False)
    job_id: str = field(repr=False)
    body: bytes = field(repr=False)
    mime_type: str
    content_sha256: str
    output_version: int

    def __post_init__(self) -> None:
        _require_public_id(self.owner_principal, "owner_principal")
        _require_public_id(self.job_id, "job_id")
        if not isinstance(self.body, bytes) or not self.body:
            raise ValueError("output body is invalid")
        if self.mime_type != "image/png":
            raise ValueError("output MIME is invalid")
        if hashlib.sha256(self.body).hexdigest() != _require_digest(
            self.content_sha256, "content_sha256"
        ):
            raise ValueError("output digest does not match")
        _require_output_version(self.output_version)


@dataclass(frozen=True, slots=True)
class OutputAcknowledgement:
    output_sha256: str
    output_version: int

    def __post_init__(self) -> None:
        _require_digest(self.output_sha256, "output_sha256")
        _require_output_version(self.output_version)


@dataclass(frozen=True, slots=True)
class OutputAckResult:
    owner_principal: str = field(repr=False)
    job_id: str
    output_sha256: str
    output_version: int
    deletion_state: str
    replayed: bool = False

    def __post_init__(self) -> None:
        _require_public_id(self.owner_principal, "owner_principal")
        _require_public_id(self.job_id, "job_id")
        _require_digest(self.output_sha256, "output_sha256")
        _require_output_version(self.output_version)
        if self.deletion_state not in {"delete_pending", "deleted"}:
            raise ValueError("deletion_state is invalid")
        if type(self.replayed) is not bool:
            raise ValueError("replayed is invalid")

OwnedResourceKey = tuple[str, str]


@runtime_checkable
class NovelSceneArtPlatformService(Protocol):
    """Narrow future service seam; it grants no renderer or storage access."""

    def capability(self, context: AuthorizedServiceRequest) -> CapabilitySnapshot: ...

    def submit(
        self, context: AuthorizedServiceRequest, submission: ValidatedPlatformSubmit
    ) -> SubmitResult: ...

    def read_job(self, context: AuthorizedServiceRequest) -> JobSnapshot | None: ...

    def cancel_job(self, context: AuthorizedServiceRequest) -> JobSnapshot | None: ...

    def read_output(
        self, context: AuthorizedServiceRequest, *, maximum_bytes: int
    ) -> OwnedBinaryOutput | None: ...

    def acknowledge_output(
        self,
        context: AuthorizedServiceRequest,
        acknowledgement: OutputAcknowledgement,
    ) -> OutputAckResult | None: ...


def _error(status: int, code: str, message: str) -> JsonHttpResponse:
    return JsonHttpResponse(
        status,
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
    )


def _invalid() -> JsonHttpResponse:
    return _error(400, "scene_art_request_invalid", "Request is invalid.")


def _denied() -> JsonHttpResponse:
    return _error(401, "scene_art_service_denied", "Service request denied.")


def _missing() -> JsonHttpResponse:
    # This exact response is used for both absent and differently-owned rows.
    return _error(404, "scene_art_resource_not_found", "Resource not found.")


def _payload_hash(request: Mapping[str, Any], release: RenderRelease) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "schemaVersion": RENDER_REQUEST_SCHEMA,
                "workflowContractRevision": WORKFLOW_CONTRACT_REVISION,
                "jobId": request.get("job_id"),
                "requestId": request.get("request_id"),
                "generationKey": request.get("generation_key"),
                "scenePackHash": request.get("scene_pack_hash"),
                "renderPlanHash": request.get("render_plan_hash"),
                "rendererReleaseDigest": release.release_digest,
            }
        )
    ).hexdigest()


def parse_platform_submit(
    body: bytes,
    *,
    release: RenderRelease,
    platform_enforced_max_output_bytes: int,
) -> ValidatedPlatformSubmit:
    """Strictly parse and semantically validate one platform-submit envelope."""

    if (
        type(platform_enforced_max_output_bytes) is not int
        or platform_enforced_max_output_bytes <= 0
        or platform_enforced_max_output_bytes > MAX_PLATFORM_OUTPUT_BYTES
    ):
        raise _InvalidRequest()
    maximum = EXPECTED_SUBMIT_SCHEMA_DEFINITION["maximum_body_bytes"]
    if type(maximum) is not int:
        raise _InvalidRequest()
    value = strict_json(body, maximum=maximum)
    if not isinstance(value, dict) or set(value) != _SUBMIT_KEYS:
        raise _InvalidRequest()
    if (
        value.get("schema_version") != SUBMIT_SCHEMA_ID
        or value.get("schema_sha256") != EXPECTED_SUBMIT_SCHEMA_SHA256
    ):
        raise _InvalidRequest()
    request = value.get("render_request")
    if not isinstance(request, dict) or set(request) != _RENDER_REQUEST_KEYS:
        raise _InvalidRequest()
    selected_release = _snapshot_release(release)

    # ``parse_claim`` is the existing semantic v2 authority.  The platform
    # envelope has no Oracle lease fields, so they are fixed parser-only values.
    # Only the render-request fields copied below survive the validation call;
    # the synthetic lease wrapper cannot reach the service seam.
    parsed = parse_claim(
        {
            "requestId": request.get("request_id"),
            "jobId": request.get("job_id"),
            "leaseToken": "platform-submit-parser-v1",
            "leaseExpiresAt": 1,
            "attempt": 1,
            "payloadHash": _payload_hash(request, selected_release),
            "renderRequest": request,
        },
        release=selected_release,
    )
    canonical = canonical_json(value)
    if len(canonical) > maximum:
        raise _InvalidRequest()
    try:
        return ValidatedPlatformSubmit(
            canonical_envelope=canonical,
            canonical_request_sha256=hashlib.sha256(canonical).hexdigest(),
            request_id=parsed.request_id,
            job_id=parsed.job_id,
            generation_key=parsed.generation_key,
            scene_pack_hash=parsed.scene_pack_hash,
            render_plan_hash=parsed.render_plan_hash,
            renderer_release_digest=parsed.renderer_release_digest,
            variation_index=parsed.variation_index,
            seed=parsed.seed,
            max_output_bytes=parsed.max_output_bytes,
            platform_enforced_max_output_bytes=platform_enforced_max_output_bytes,
        )
    except (TypeError, ValueError) as error:
        raise _InvalidRequest() from error


def parse_output_ack(body: bytes) -> OutputAcknowledgement:
    """Parse the exact, recursively duplicate-safe output ACK schema."""

    maximum = EXPECTED_ACK_SCHEMA_DEFINITION["maximum_body_bytes"]
    if type(maximum) is not int:
        raise _InvalidRequest()
    value = strict_json(body, maximum=maximum)
    if not isinstance(value, dict) or set(value) != _ACK_KEYS:
        raise _InvalidRequest()
    if value.get("schema_version") != ACK_SCHEMA_ID:
        raise _InvalidRequest()
    try:
        return OutputAcknowledgement(
            output_sha256=_require_digest(value.get("output_sha256"), "output_sha256"),
            output_version=_require_output_version(value.get("output_version")),
        )
    except (TypeError, ValueError) as error:
        raise _InvalidRequest() from error


class NovelSceneArtPlatformHttpController:
    """Translate six authenticated route contexts into a narrow service API."""

    __slots__ = (
        "_expected_output_mime",
        "_max_output_bytes",
        "_release",
        "_service",
    )

    def __init__(
        self,
        service: NovelSceneArtPlatformService,
        *,
        release: RenderRelease,
        max_output_bytes: int = MAX_PLATFORM_OUTPUT_BYTES,
    ) -> None:
        if not isinstance(service, NovelSceneArtPlatformService):
            raise TypeError("service does not implement NovelSceneArtPlatformService")
        if (
            type(max_output_bytes) is not int
            or not 1 <= max_output_bytes <= MAX_PLATFORM_OUTPUT_BYTES
        ):
            raise ValueError("max_output_bytes is invalid")
        selected_release = _snapshot_release(release)
        output = selected_release.public_release.get("output")
        if not isinstance(output, Mapping):
            raise ValueError("release output is invalid")
        release_max = output.get("max_bytes")
        mime_type = output.get("mime_type")
        if (
            type(release_max) is not int
            or release_max <= 0
            or mime_type != "image/png"
        ):
            raise ValueError("release output is invalid")
        self._service = service
        self._release = selected_release
        self._max_output_bytes = min(max_output_bytes, release_max)
        self._expected_output_mime = mime_type

    @staticmethod
    def _route_context(
        authorized: AuthorizedServiceRequest,
        *,
        method: object,
        path: object,
        query: object,
        body: object,
    ) -> tuple[object, bytes]:
        if type(authorized) is not AuthorizedServiceRequest:
            raise _InvalidAuthorizedContext()
        if not isinstance(authorized.route_template, str):
            raise _InvalidAuthorizedContext()
        rule = _RULES_BY_TEMPLATE.get(authorized.route_template)
        if rule is None:
            raise _InvalidAuthorizedContext()
        if (
            authorized.principal_id != SERVICE_PRINCIPAL
            or authorized.owner_principal != authorized.principal_id
            or not isinstance(authorized.key_id, str)
            or _KEY_ID.fullmatch(authorized.key_id) is None
            or authorized.scope != rule.scope
            or authorized.max_body_bytes != rule.max_body_bytes
            or authorized.atomic_owner_lookup_required != rule.existing_owner_required
        ):
            raise _InvalidAuthorizedContext()
        if rule.existing_owner_required:
            if (
                not isinstance(authorized.job_id, str)
                or _PUBLIC_ID.fullmatch(authorized.job_id) is None
            ):
                raise _InvalidAuthorizedContext()
            expected_path = rule.path_template.replace("{job_id}", authorized.job_id)
        else:
            if authorized.job_id is not None:
                raise _InvalidAuthorizedContext()
            expected_path = rule.path_template
        if (
            type(method) is not str
            or type(path) is not str
            or type(query) is not str
            or method != rule.method
            or path != expected_path
            or query != ""
        ):
            raise _InvalidRequest()
        if type(body) is not bytes or len(body) > rule.max_body_bytes:
            raise _InvalidRequest()
        if rule.max_body_bytes == 0 and body:
            raise _InvalidRequest()
        return rule, body

    @staticmethod
    def _owned_key(authorized: AuthorizedServiceRequest) -> OwnedResourceKey:
        try:
            key = authorized.owned_resource_key()
        except ServiceRequestDenied as error:
            raise _InvalidAuthorizedContext() from error
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or key != (authorized.owner_principal, authorized.job_id)
        ):
            raise _InvalidAuthorizedContext()
        return key

    @staticmethod
    def _job_response(job: object, expected_key: OwnedResourceKey) -> JsonHttpResponse:
        if (
            type(job) is not JobSnapshot
            or (job.owner_principal, job.job_id) != expected_key
        ):
            raise ValueError("service returned an invalid job")
        return JsonHttpResponse(200, _job_public_dict(job))

    def handle(
        self,
        authorized: AuthorizedServiceRequest,
        *,
        method: object,
        path: object,
        query: object = "",
        body: object = b"",
    ) -> JsonHttpResponse | BinaryHttpResponse:
        """Handle one already-authenticated request without performing HTTP I/O."""

        try:
            rule, raw_body = self._route_context(
                authorized,
                method=method,
                path=path,
                query=query,
                body=body,
            )
            template = rule.path_template

            if template.endswith("/capability"):
                snapshot = self._service.capability(authorized)
                if (
                    type(snapshot) is not CapabilitySnapshot
                    or snapshot.owner_principal != authorized.owner_principal
                ):
                    raise ValueError("service returned an invalid capability")
                if snapshot.capability_digest not in {
                    None,
                    self._release.capability_digest,
                } or snapshot.renderer_release_digest not in {
                    None,
                    self._release.release_digest,
                }:
                    raise ValueError("service capability is not release-bound")
                return JsonHttpResponse(
                    200,
                    {
                        "schema_version": CAPABILITY_RESPONSE_SCHEMA,
                        "ready": snapshot.ready,
                        "capability_digest": snapshot.capability_digest,
                        "renderer_release_digest": snapshot.renderer_release_digest,
                        "submit_schema_version": SUBMIT_SCHEMA_ID,
                        "submit_schema_sha256": EXPECTED_SUBMIT_SCHEMA_SHA256,
                        "output_ack_schema_version": ACK_SCHEMA_ID,
                        "output_ack_schema_sha256": EXPECTED_ACK_SCHEMA_SHA256,
                    },
                )

            if template.endswith("/jobs"):
                submission = parse_platform_submit(
                    raw_body,
                    release=self._release,
                    platform_enforced_max_output_bytes=self._max_output_bytes,
                )
                result = self._service.submit(authorized, submission)
                if (
                    type(result) is not SubmitResult
                    or type(result.job) is not JobSnapshot
                    or result.job.owner_principal != authorized.owner_principal
                    or result.job.job_id != submission.job_id
                    or result.job.generation_key != submission.generation_key
                    or result.job.canonical_request_sha256
                    != submission.canonical_request_sha256
                ):
                    raise ValueError("service returned an invalid submit result")
                return JsonHttpResponse(
                    201 if result.created else 200,
                    {**_job_public_dict(result.job), "created": result.created},
                )

            key = self._owned_key(authorized)
            if template.endswith("/cancel"):
                job = self._service.cancel_job(authorized)
                return _missing() if job is None else self._job_response(job, key)
            if template.endswith("/output"):
                output = self._service.read_output(
                    authorized,
                    maximum_bytes=self._max_output_bytes,
                )
                if output is None:
                    return _missing()
                if type(output) is not OwnedBinaryOutput:
                    raise ValueError("service returned an invalid output")
                if (
                    (output.owner_principal, output.job_id) != key
                    or output.mime_type != self._expected_output_mime
                    or len(output.body) > self._max_output_bytes
                    or hashlib.sha256(output.body).hexdigest() != output.content_sha256
                ):
                    raise ValueError("service returned an invalid output")
                digest_b64 = base64.b64encode(bytes.fromhex(output.content_sha256)).decode(
                    "ascii"
                )
                return BinaryHttpResponse(
                    200,
                    output.body,
                    {
                        "Content-Type": output.mime_type,
                        "Content-Length": str(len(output.body)),
                        "Content-Digest": f"sha-256=:{digest_b64}:",
                        "X-Content-SHA256": output.content_sha256,
                        "X-Output-Version": str(output.output_version),
                        "ETag": f'"sha256-{output.content_sha256}-v{output.output_version}"',
                        "Cache-Control": "private, no-store, max-age=0",
                        "Pragma": "no-cache",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                    },
                )
            if template.endswith("/output-ack"):
                acknowledgement = parse_output_ack(raw_body)
                result = self._service.acknowledge_output(authorized, acknowledgement)
                if result is None:
                    return _missing()
                if (
                    type(result) is not OutputAckResult
                    or (result.owner_principal, result.job_id) != key
                    or result.output_sha256 != acknowledgement.output_sha256
                    or result.output_version != acknowledgement.output_version
                ):
                    raise ValueError("service returned an invalid acknowledgement")
                return JsonHttpResponse(200, _output_ack_public_dict(result))

            job = self._service.read_job(authorized)
            return _missing() if job is None else self._job_response(job, key)
        except _InvalidAuthorizedContext:
            return _denied()
        except (_InvalidRequest, SceneArtError):
            return _invalid()
        except PlatformSubmitConflict:
            return _error(
                409,
                "scene_art_idempotency_conflict",
                "Request conflicts with existing state.",
            )
        except PlatformOutputAckConflict:
            return _error(
                409,
                "scene_art_output_ack_conflict",
                "Output acknowledgement conflicts with stored state.",
            )
        except PlatformJobStateConflict:
            return _error(
                409,
                "scene_art_state_conflict",
                "Requested job transition conflicts with stored state.",
            )
        except PlatformStorageCapacityExhausted:
            return _error(
                503,
                "scene_art_storage_capacity_exhausted",
                "Scene art storage capacity is exhausted.",
            )
        except PlatformServiceUnavailable:
            return _error(
                503,
                "scene_art_service_unavailable",
                "Service is unavailable.",
            )
        except Exception:
            # Never reflect an implementation exception, prompt, path, or token.
            return _error(500, "scene_art_internal_error", "Service request failed.")


__all__ = [
    "ACK_SCHEMA_ID",
    "BinaryHttpResponse",
    "CAPABILITY_RESPONSE_SCHEMA",
    "CapabilitySnapshot",
    "EXPECTED_ACK_SCHEMA_SHA256",
    "EXPECTED_SUBMIT_SCHEMA_SHA256",
    "JOB_RESPONSE_SCHEMA",
    "JsonHttpResponse",
    "MAX_PLATFORM_OUTPUT_BYTES",
    "NovelSceneArtPlatformHttpController",
    "NovelSceneArtPlatformService",
    "OUTPUT_ACK_RESPONSE_SCHEMA",
    "OutputAckResult",
    "OutputAcknowledgement",
    "OwnedBinaryOutput",
    "OwnedResourceKey",
    "PlatformJobStateConflict",
    "PlatformOutputAckConflict",
    "PlatformServiceUnavailable",
    "PlatformStorageCapacityExhausted",
    "PlatformSubmitConflict",
    "SUBMIT_SCHEMA_ID",
    "SubmitResult",
    "ValidatedPlatformSubmit",
    "JobSnapshot",
    "parse_output_ack",
    "parse_platform_submit",
]
