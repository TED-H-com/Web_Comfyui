"""Pure owner-bound decision substrate for offline provider imports.

This module deliberately performs no database, filesystem, HTTP, provider, or
GPU work.  It validates explicit character/profile decisions against an
already validated :class:`~provider_fabric.clipmode_import.ClipModeImportBundle`,
derives server-only graph identifiers, and produces deterministic decision
evidence suitable for a later owner-scoped store-snapshot planner.

It is *not* confirmation authority.  In particular, a successful plan from
this module does not prove that an account, mapping target, materialized blob,
or current provider-store graph is available.  Public serialization says so
explicitly and omits owner/account IDs, external provider IDs, archive member
names, derived graph IDs, and paths.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from .clipmode_import import (
    ClipModeImportBundle,
    ExternalVersionRef,
    ImportProvenance,
    ImportedCharacterRecord,
    ImportedLineageEdge,
    ImportedMediaRecord,
    PROVIDER_ID,
    SCHEMA_NAME as SOURCE_SCHEMA_NAME,
    SCHEMA_VERSION as SOURCE_SCHEMA_VERSION,
)
from .models import MediaKind, VisualClass


OWNER_IMPORT_SCHEMA_VERSION = 1
PLAN_VERSION = "clipmode-owner-import-v1"
PROFILE_INPUT_CONTRACT_VERSION = "castle-safe-image-input-v1"
MAX_SOURCE_CHARACTERS = 10_000
MAX_SOURCE_MEDIA = 50_000
MAX_SOURCE_LINEAGE = 100_000
# This is deliberately just above the number of minimal reference objects
# that can fit in the importer's default 8 MiB manifest.  It remains an
# independent planner bound when an internal caller supplies larger parser
# limits or a hand-built bundle.
MAX_SOURCE_CHARACTER_REFERENCES = 250_000

_PLAN_TYPE = "castle.provider-owner-import-plan.v1"
_MAPPING_DOMAIN = "castle.provider-owner-import.mapping.v1"
_PROFILE_DOMAIN = "castle.provider-owner-import.profile-selection.v1"
_GRAPH_DOMAIN = "castle.provider-owner-import.server-graph.v1"
_PLAN_DOMAIN = "castle.provider-owner-import.plan.v1"
_ID_DOMAIN = "castle.provider-owner-import.graph-id.v1"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_RECORD_RE = re.compile(r"^clipmode\.(?:character|media):[0-9a-f]{64}$")
_LINEAGE_RECORD_RE = re.compile(r"^clipmode\.lineage:[0-9a-f]{64}$")
_SAFE_SOURCE_KINDS = frozenset({"json", "zip"})
_ALLOWED_MIMES: Mapping[MediaKind, frozenset[str]] = MappingProxyType(
    {
        MediaKind.IMAGE: frozenset(
            {"image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"}
        ),
        MediaKind.VIDEO: frozenset(
            {"video/mp4", "video/quicktime", "video/webm"}
        ),
        MediaKind.AUDIO: frozenset(
            {
                "audio/flac",
                "audio/mp4",
                "audio/mpeg",
                "audio/ogg",
                "audio/wav",
                "audio/x-wav",
            }
        ),
        MediaKind.TEXT: frozenset({"text/markdown", "text/plain"}),
    }
)
_SIGNATURE_FAMILY_BY_MIME: Mapping[str, str] = MappingProxyType(
    {
        "audio/flac": "flac",
        "audio/mp4": "mp4",
        "audio/mpeg": "mpeg_audio",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "image/avif": "avif",
        "image/gif": "gif",
        "image/jpeg": "jpeg",
        "image/png": "png",
        "image/webp": "webp",
        "text/markdown": "utf8_text",
        "text/plain": "utf8_text",
        "video/mp4": "mp4",
        "video/quicktime": "quicktime",
        "video/webm": "webm",
    }
)
# This literal is a review seal, not a discovery cache.  Either table changing
# must fail closed until the exact MIME/signature contract is reviewed and the
# seal is intentionally updated.
_REVIEWED_MEDIA_CONTRACT_SHA256 = (
    "de5e0c1f219885ac43655054842e105321e649bc534457fee826ba9184dffb24"
)
# GIF/AVIF remain valid original catalog objects, but do not become profile
# candidates until their isolated decoder/transcoder contracts are reviewed.
_PROFILE_INPUT_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_VISUAL_CLASSES = frozenset(item.value for item in VisualClass)
_REQUEST_KEYS = frozenset(
    {"schema_version", "character_mappings", "profile_selections"}
)
_SEPARATE_MAPPING_KEYS = frozenset({"source_character_key", "mode"})
_MAP_MAPPING_KEYS = frozenset(
    {"source_character_key", "mode", "target_character_id"}
)
_PROFILE_SELECTION_KEYS = frozenset(
    {"source_character_key", "media_record_id"}
)
_LINEAGE_RELATIONSHIPS = frozenset(
    {
        "assembled",
        "audio_reference",
        "derived",
        "first_frame",
        "last_frame",
        "reference",
    }
)


class OwnerImportPlanError(ValueError):
    """A fail-closed pure-planning error with a stable, sanitized code."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise OwnerImportPlanError(code, message)


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
        raise OwnerImportPlanError(
            "decision_not_canonical", "Import decision is not canonical JSON data."
        ) from error


def _domain_sha256(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _source_stable_id(namespace: str, provider_scope: str, *values: str) -> str:
    digest = hashlib.sha256(
        _canonical_bytes([PROVIDER_ID, provider_scope, namespace, *values])
    ).hexdigest()
    return f"clipmode.{namespace}:{digest}"


def _plain_data(value: Any) -> Any:
    """Return a fresh JSON-shaped copy of an immutable internal view."""

    if isinstance(value, Mapping):
        return {key: _plain_data(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_data(child) for child in value]
    return value


def _exact_stable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("identifier_invalid", f"{label} must be an exact lowercase stable identifier.")
    return value


def _source_record_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SOURCE_RECORD_RE.fullmatch(value):
        _fail("source_record_invalid", f"{label} is not a ClipMode source record key.")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail("source_evidence_invalid", f"{label} is not a lowercase SHA-256 digest.")
    return value


def _external_identity(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 500
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "://" in value
        or value.startswith(("data:", "file:"))
    ):
        _fail("source_identity_invalid", f"{label} is invalid.")
    return value


def _derived_id(
    resource: str,
    *,
    owner_user_id: str,
    account_id: str,
    provider_scope: str,
    external_identity: str,
    external_version: str | None = None,
) -> str:
    body: dict[str, Any] = {
        "account_id": account_id,
        "external_identity": external_identity,
        "owner_user_id": owner_user_id,
        "provider_id": PROVIDER_ID,
        "provider_scope": provider_scope,
        "resource": resource,
    }
    if external_version is not None:
        body["external_version"] = external_version
    digest = _domain_sha256(_ID_DOMAIN, body)[:48]
    prefixes = {
        "character": "clipmode.character",
        "character_link": "clipmode.link",
        "projection": "clipmode.asset",
        "version": "clipmode.version",
        "lineage": "clipmode.lineage",
    }
    return f"{prefixes[resource]}:{digest}"


def _account_binding_sha256(account_id: str, provider_scope: str) -> str:
    # This intentionally matches ProviderFabricStore's schema-8 helper.  Keep
    # it local so the decision substrate has no database-module dependency.
    return hashlib.sha256(
        _canonical_bytes([PROVIDER_ID, account_id, provider_scope])
    ).hexdigest()


def _entry_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _media_signature_family(kind: MediaKind, mime_type: Any) -> str:
    try:
        contract = {
            "allowed_mimes": {
                raw_kind.value: sorted(mimes)
                for raw_kind, mimes in _ALLOWED_MIMES.items()
            },
            "signature_family_by_mime": dict(_SIGNATURE_FAMILY_BY_MIME),
        }
        contract_sha256 = hashlib.sha256(_canonical_bytes(contract)).hexdigest()
    except (AttributeError, TypeError, ValueError):
        contract_sha256 = ""
    if contract_sha256 != _REVIEWED_MEDIA_CONTRACT_SHA256:
        _fail(
            "media_signature_invalid",
            "Media MIME/signature contract differs from the reviewed mapping.",
        )
    allowed = _ALLOWED_MIMES.get(kind)
    if (
        allowed is None
        or not isinstance(mime_type, str)
        or mime_type not in allowed
    ):
        _fail(
            "media_mime_invalid",
            "Media MIME is not allowlisted for its exact kind.",
        )
    signature = _SIGNATURE_FAMILY_BY_MIME.get(mime_type)
    if not isinstance(signature, str) or not signature:
        _fail(
            "media_signature_invalid",
            "Media MIME has no reviewed signature-family mapping.",
        )
    return signature


def _import_decision_sha256(
    *,
    owner_user_id: str,
    account_id: str,
    provider_scope: str,
    raw_source_sha256: str,
    semantic_sha256: str,
    mapping_sha256: str,
    profile_selection_sha256: str,
) -> str:
    # Exact schema-8 decision identity.  Plan version/hash are deliberately
    # absent so a harmless re-plan converges on an existing active receipt.
    payload = {
        "accountId": account_id,
        "mappingSha256": mapping_sha256,
        "ownerUserId": owner_user_id,
        "profileSelectionSha256": profile_selection_sha256,
        "providerId": PROVIDER_ID,
        "providerScope": provider_scope,
        "rawSourceSha256": raw_source_sha256,
        "semanticSha256": semantic_sha256,
        "sourceSchema": SOURCE_SCHEMA_NAME,
        "sourceSchemaVersion": SOURCE_SCHEMA_VERSION,
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _exact_object(value: Any, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if (
        not isinstance(value, Mapping)
        or any(not isinstance(key, str) for key in value)
        or set(value) != keys
    ):
        _fail("request_schema_invalid", f"{label} does not match the exact schema.")
    return value


def _exact_array(value: Any, label: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        _fail("request_schema_invalid", f"{label} must be an array.")
    if len(value) > maximum:
        _fail("request_too_large", f"{label} contains too many entries.")
    return value


def _archive_member(
    value: Any,
    *,
    code: str = "profile_source_invalid",
    message: str = "Selected profile archive member is invalid.",
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 1024
        or "\x00" in value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or value.endswith("/")
    ):
        _fail(code, message)
    if value != unicodedata.normalize("NFC", value):
        _fail(code, message)
    raw_parts = value.split("/")
    if any(
        part in {"", ".", ".."}
        or part != part.strip()
        or part.endswith(".")
        for part in raw_parts
    ):
        _fail(code, message)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail(code, message)
    if value == "clipmode-manifest.json":
        _fail(code, message)
    return path.as_posix()


def _lineage_is_acyclic(edges: list[tuple[str, str]]) -> bool:
    """Check the already bounded source graph in linear time."""

    adjacency: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    for parent, child in edges:
        children = adjacency.setdefault(parent, set())
        indegree.setdefault(parent, 0)
        if child not in children:
            children.add(child)
            indegree[child] = indegree.get(child, 0) + 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for child in adjacency.get(node, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return visited == len(indegree)


@dataclass(frozen=True, slots=True)
class CharacterDecision:
    """One normalized complete decision; sensitive graph fields stay private."""

    source_character_key: str
    source_record_ids: tuple[str, ...]
    visual_class: str
    mode: str
    external_character_id: str = field(repr=False)
    canonical_character_id: str = field(repr=False)
    provider_link_id: str = field(repr=False)
    target_character_id: str | None = field(default=None, repr=False)

    def hash_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "mode": self.mode,
            "source_character_key": self.source_character_key,
        }
        if self.mode == "map":
            value["target_character_id"] = self.target_character_id
        return value

    def public_dict(self, *, has_profile_choice: bool) -> dict[str, Any]:
        value = {
            "source_character_key": self.source_character_key,
            "visual_class": self.visual_class,
            "catalog_only": self.visual_class == VisualClass.UNKNOWN.value,
            "mode": self.mode,
            "has_profile_choice": has_profile_choice,
        }
        # An explicitly selected existing Castle target is already visible in
        # the owner's catalog and must remain reviewable.  The derived ID for a
        # separate provider namespace remains server-only.
        if self.mode == "map":
            value["target_character_id"] = self.target_character_id
        return value


@dataclass(frozen=True, slots=True)
class ServerMediaIdentity:
    """Server-only provider/media identity derived without publishing secrets."""

    source_media_record_id: str
    kind: str
    visual_class: str
    availability: str
    mime_type: str
    signature_family: str
    byte_size: int | None
    content_sha256: str | None = field(repr=False)
    character_record_ids: tuple[str, ...]
    external_asset_id: str = field(repr=False)
    external_version: str = field(repr=False)
    projection_id: str = field(repr=False)
    version_id: str = field(repr=False)
    archive_member: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProfileChoice:
    """An exact single-subject bundled-image choice."""

    source_character_key: str
    media_record_id: str
    visual_class: str
    mime_type: str
    byte_size: int
    source_sha256: str = field(repr=False)
    canonical_character_id: str = field(repr=False)
    projection_id: str = field(repr=False)
    version_id: str = field(repr=False)
    archive_member: str = field(repr=False)

    def hash_dict(self) -> dict[str, str]:
        return {
            "media_record_id": self.media_record_id,
            "source_character_key": self.source_character_key,
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "source_character_key": self.source_character_key,
            "media_record_id": self.media_record_id,
            "visual_class": self.visual_class,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "single_subject_verified": True,
            "bundled_source_verified": True,
        }


@dataclass(frozen=True, slots=True)
class ServerLineageIdentity:
    source_edge_id: str
    relationship: str
    parent_projection_id: str = field(repr=False)
    child_projection_id: str = field(repr=False)
    lineage_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class OwnerImportDecisionPlan:
    """Pure decision evidence awaiting a store-aware plan and materializer."""

    owner_user_id: str = field(repr=False)
    account_id: str = field(repr=False)
    provider_scope: str = field(repr=False)
    source_kind: str
    raw_source_sha256: str
    manifest_sha256: str
    semantic_sha256: str
    account_binding_sha256: str
    mapping_sha256: str
    profile_selection_sha256: str
    import_decision_sha256: str
    server_graph_sha256: str
    plan_sha256: str
    characters: tuple[CharacterDecision, ...]
    media: tuple[ServerMediaIdentity, ...] = field(repr=False)
    profiles: tuple[ProfileChoice, ...]
    lineage: tuple[ServerLineageIdentity, ...] = field(repr=False)
    summary: Mapping[str, Any]

    @property
    def store_snapshot_validated(self) -> bool:
        return False

    @property
    def commit_available(self) -> bool:
        return False

    def to_public_dict(self) -> dict[str, Any]:
        profile_keys = {item.source_character_key for item in self.profiles}
        return {
            "schema_version": OWNER_IMPORT_SCHEMA_VERSION,
            "plan_version": PLAN_VERSION,
            "provider_id": PROVIDER_ID,
            "mode": "decision_substrate",
            "decision_validated": True,
            "store_snapshot_validated": False,
            "confirmation_available": False,
            "commit_available": False,
            "source": {
                "schema": SOURCE_SCHEMA_NAME,
                "schema_version": SOURCE_SCHEMA_VERSION,
                "source_kind": self.source_kind,
                "raw_source_sha256": self.raw_source_sha256,
                "manifest_sha256": self.manifest_sha256,
                "semantic_sha256": self.semantic_sha256,
            },
            "evidence": {
                "account_binding_sha256": self.account_binding_sha256,
                "mapping_sha256": self.mapping_sha256,
                "profile_selection_sha256": self.profile_selection_sha256,
                "import_decision_sha256": self.import_decision_sha256,
                "server_graph_sha256": self.server_graph_sha256,
                "plan_sha256": self.plan_sha256,
                "profile_input_contract_sha256": PROFILE_INPUT_CONTRACT_SHA256,
            },
            "character_decisions": [
                item.public_dict(
                    has_profile_choice=item.source_character_key in profile_keys
                )
                for item in self.characters
            ],
            "profile_selections": [item.public_dict() for item in self.profiles],
            "summary": _plain_data(self.summary),
            "provider_transport_performed": False,
            "provider_store_mutated": False,
            "source_persisted": False,
            "media_materialized": False,
            "credits_spent": 0,
        }


PROFILE_INPUT_CONTRACT_SHA256 = _domain_sha256(
    "castle.provider-owner-import.profile-input-contract.v1",
    {
        "contract_version": PROFILE_INPUT_CONTRACT_VERSION,
        "availability": "bundled",
        "kind": "image",
        "mimes": sorted(_PROFILE_INPUT_MIMES),
        "requires_declared_bytes": True,
        "requires_declared_sha256": True,
        "requires_single_subject": True,
        "rendition_materialized_by_this_module": False,
    },
)


def _validate_bundle(
    bundle: ClipModeImportBundle,
    provider_scope: str,
) -> tuple[
    dict[str, tuple[ImportedCharacterRecord, ...]],
    dict[str, ImportedMediaRecord],
]:
    if not isinstance(bundle, ClipModeImportBundle):
        raise TypeError("bundle must be a ClipModeImportBundle")
    provenance = bundle.provenance
    if not isinstance(provenance, ImportProvenance):
        _fail("source_binding_invalid", "Import provenance is invalid.")
    if (
        provenance.provider_id != PROVIDER_ID
        or provenance.schema_name != SOURCE_SCHEMA_NAME
        or type(provenance.schema_version) is not int
        or provenance.schema_version != SOURCE_SCHEMA_VERSION
        or provenance.provider_scope != provider_scope
        or not isinstance(provenance.source_kind, str)
        or provenance.source_kind not in _SAFE_SOURCE_KINDS
    ):
        _fail("source_binding_invalid", "Import source binding is inconsistent.")
    if (
        len(bundle.characters) > MAX_SOURCE_CHARACTERS
        or len(bundle.media) > MAX_SOURCE_MEDIA
        or len(bundle.lineage) > MAX_SOURCE_LINEAGE
    ):
        _fail("source_too_large", "Import source exceeds pure-planning limits.")
    _digest(provenance.source_sha256, "raw source hash")
    _digest(provenance.manifest_sha256, "manifest hash")
    _digest(provenance.semantic_sha256, "semantic hash")
    if provenance.source_id != f"clipmode.import:{provenance.semantic_sha256}":
        _fail(
            "source_identity_invalid",
            "Import source identity does not match its semantic evidence.",
        )
    _external_identity(provenance.export_id, "source export identity")
    _external_identity(provenance.exported_at, "source export timestamp")

    character_records: dict[str, ImportedCharacterRecord] = {}
    character_groups: dict[str, list[ImportedCharacterRecord]] = {}
    for item in bundle.characters:
        if not isinstance(item, ImportedCharacterRecord):
            _fail("source_record_invalid", "Import contains an invalid character record.")
        if not isinstance(item.external_ref, ExternalVersionRef):
            _fail("source_record_invalid", "Character external reference is invalid.")
        record_id = _source_record_id(item.record_id, "character record ID")
        if record_id in character_records:
            _fail("source_record_duplicate", "Import contains a duplicate character record.")
        if item.provider_id != PROVIDER_ID or item.provider_scope != provider_scope:
            _fail("source_binding_invalid", "Character source binding is inconsistent.")
        if not isinstance(item.visual_class, VisualClass):
            _fail("visual_class_invalid", "Character visual class is invalid.")
        _digest(item.entry_sha256, "character entry hash")
        external_id = _external_identity(
            item.external_ref.external_id, "character external identity"
        )
        external_version = _external_identity(
            item.external_ref.version, "character external version"
        )
        if record_id != _source_stable_id(
            "character", provider_scope, external_id, external_version
        ):
            _fail(
                "source_identity_invalid",
                "Character record identity does not match its source reference.",
            )
        character_records[record_id] = item
        character_groups.setdefault(external_id, []).append(item)

    grouped: dict[str, tuple[ImportedCharacterRecord, ...]] = {}
    group_keys: set[str] = set()
    for external_id, raw_records in character_groups.items():
        records = tuple(sorted(raw_records, key=lambda value: value.record_id))
        classes = {item.visual_class.value for item in records}
        if len(classes) != 1:
            _fail(
                "character_group_class_mismatch",
                "Versions of one source character disagree on visual class.",
            )
        versions = [item.external_ref.version for item in records]
        if len(versions) != len(set(versions)):
            _fail(
                "source_identity_duplicate",
                "Source character versions are not unique.",
            )
        group_key = records[0].record_id
        if group_key in group_keys:
            _fail("source_group_collision", "Source character group keys collide.")
        group_keys.add(group_key)
        grouped[external_id] = records

    media_records: dict[str, ImportedMediaRecord] = {}
    media_identity_shape: dict[str, tuple[str, str]] = {}
    media_versions: dict[str, set[str]] = {}
    total_character_references = 0
    for item in bundle.media:
        if not isinstance(item, ImportedMediaRecord):
            _fail("source_record_invalid", "Import contains an invalid media record.")
        if not isinstance(item.external_ref, ExternalVersionRef):
            _fail("source_record_invalid", "Media external reference is invalid.")
        record_id = _source_record_id(item.record_id, "media record ID")
        if record_id in media_records:
            _fail("source_record_duplicate", "Import contains a duplicate media record.")
        if item.provider_id != PROVIDER_ID or item.provider_scope != provider_scope:
            _fail("source_binding_invalid", "Media source binding is inconsistent.")
        if not isinstance(item.kind, MediaKind) or not isinstance(
            item.visual_class, VisualClass
        ):
            _fail("source_record_invalid", "Media kind or visual class is invalid.")
        _digest(item.entry_sha256, "media entry hash")
        external_id = _external_identity(
            item.external_ref.external_id, "media external identity"
        )
        external_version = _external_identity(
            item.external_ref.version, "media external version"
        )
        if record_id != _source_stable_id(
            "media", provider_scope, external_id, external_version
        ):
            _fail(
                "source_identity_invalid",
                "Media record identity does not match its source reference.",
            )
        _media_signature_family(item.kind, item.mime_type)
        if (
            not isinstance(item.availability, str)
            or item.availability not in {"remote_only", "bundled"}
        ):
            _fail("media_evidence_invalid", "Media availability is invalid.")
        if item.availability == "remote_only":
            if (
                item.byte_size is not None
                or item.content_sha256 is not None
                or item.archive_member is not None
            ):
                _fail(
                    "media_evidence_invalid",
                    "Remote-only media must not claim bundled byte evidence.",
                )
        else:
            if (
                provenance.source_kind != "zip"
                or isinstance(item.byte_size, bool)
                or not isinstance(item.byte_size, int)
                or item.byte_size <= 0
                or not isinstance(item.content_sha256, str)
                or not _SHA256_RE.fullmatch(item.content_sha256)
                or item.archive_member is None
            ):
                _fail(
                    "media_evidence_invalid",
                    "Bundled media evidence is incomplete or inconsistent.",
                )
            _archive_member(
                item.archive_member,
                code="media_evidence_invalid",
                message="Bundled media archive evidence is invalid.",
            )
        observed = (item.kind.value, item.visual_class.value)
        previous = media_identity_shape.setdefault(external_id, observed)
        if previous != observed:
            _fail(
                "media_group_identity_mismatch",
                "Versions of one source asset disagree on kind or visual class.",
            )
        versions = media_versions.setdefault(external_id, set())
        if item.external_ref.version in versions:
            _fail("source_identity_duplicate", "Source media versions are not unique.")
        versions.add(item.external_ref.version)
        if not isinstance(item.character_record_ids, tuple):
            _fail(
                "source_record_invalid",
                "Media character references are not an exact source tuple.",
            )
        reference_count = len(item.character_record_ids)
        if reference_count > MAX_SOURCE_CHARACTERS:
            _fail(
                "source_too_large",
                "One media record contains too many character references.",
            )
        total_character_references += reference_count
        if total_character_references > MAX_SOURCE_CHARACTER_REFERENCES:
            _fail(
                "source_too_large",
                "Import source contains too many media character references.",
            )
        seen_character_ids: set[str] = set()
        previous_character_id: str | None = None
        for raw_character_id in item.character_record_ids:
            character_id = _source_record_id(
                raw_character_id, "media character record ID"
            )
            if character_id in seen_character_ids:
                _fail(
                    "media_character_duplicate",
                    "Media character references are duplicated.",
                )
            if (
                previous_character_id is not None
                and character_id < previous_character_id
            ):
                _fail(
                    "media_character_order_invalid",
                    "Media character references are not in canonical order.",
                )
            seen_character_ids.add(character_id)
            previous_character_id = character_id
            character = character_records.get(character_id)
            if character is None:
                _fail(
                    "media_character_missing",
                    "Media references a character outside the validated source.",
                )
            if character.visual_class.value != item.visual_class.value:
                _fail(
                    "media_character_class_mismatch",
                    "Media and every referenced character must share one visual class.",
                )
        media_records[record_id] = item

    for item in character_records.values():
        profile_record_id = item.profile_media_record_id
        if profile_record_id is None:
            continue
        profile_id = _source_record_id(
            profile_record_id, "character profile media record ID"
        )
        profile = media_records.get(profile_id)
        if profile is None:
            _fail(
                "source_record_invalid",
                "Character profile references missing source media.",
            )
        if (
            profile.kind is not MediaKind.IMAGE
            or item.record_id not in profile.character_record_ids
        ):
            _fail(
                "source_record_invalid",
                "Character profile source relationship is invalid.",
            )

    lineage_ids: set[str] = set()
    lineage_pairs: list[tuple[str, str]] = []
    for edge in bundle.lineage:
        if not isinstance(edge, ImportedLineageEdge):
            _fail("source_record_invalid", "Import contains an invalid lineage edge.")
        if not isinstance(edge.edge_id, str) or not _LINEAGE_RECORD_RE.fullmatch(
            edge.edge_id
        ):
            _fail("source_record_invalid", "Import lineage identity is invalid.")
        if edge.edge_id in lineage_ids:
            _fail("source_record_duplicate", "Import contains a duplicate lineage edge.")
        lineage_ids.add(edge.edge_id)
        if (
            not isinstance(edge.relationship, str)
            or edge.relationship not in _LINEAGE_RELATIONSHIPS
        ):
            _fail("source_record_invalid", "Import lineage relationship is invalid.")
        parent_record_id = _source_record_id(
            edge.parent_media_record_id, "lineage parent media record ID"
        )
        child_record_id = _source_record_id(
            edge.child_media_record_id, "lineage child media record ID"
        )
        expected_edge_id = _source_stable_id(
            "lineage",
            provider_scope,
            parent_record_id,
            child_record_id,
            edge.relationship,
        )
        if edge.edge_id != expected_edge_id:
            _fail(
                "source_identity_invalid",
                "Lineage identity does not match its source relationship.",
            )
        if (
            parent_record_id not in media_records
            or child_record_id not in media_records
        ):
            _fail("lineage_media_missing", "Lineage references missing source media.")
        if parent_record_id == child_record_id:
            _fail("source_record_invalid", "Import lineage cannot reference itself.")
        _digest(edge.entry_sha256, "lineage entry hash")

        normalized_edge = {
            "parentMediaRecordId": parent_record_id,
            "childMediaRecordId": child_record_id,
            "relationship": edge.relationship,
        }
        if edge.entry_sha256 != _entry_sha256(normalized_edge):
            _fail(
                "source_entry_mismatch",
                "Lineage record does not match its source entry evidence.",
            )
        lineage_pairs.append((parent_record_id, child_record_id))

    if not _lineage_is_acyclic(lineage_pairs):
        _fail("lineage_cycle", "Import lineage must be acyclic.")

    for item in character_records.values():
        profile = (
            media_records[item.profile_media_record_id]
            if item.profile_media_record_id is not None
            else None
        )
        normalized_character = {
            "externalRef": item.external_ref.to_dict(),
            "externalModelId": item.external_model_id,
            "displayName": item.display_name,
            "visualClass": item.visual_class.value,
            "profileMedia": profile.external_ref.to_dict() if profile else None,
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }
        if item.entry_sha256 != _entry_sha256(normalized_character):
            _fail(
                "source_entry_mismatch",
                "Character record does not match its source entry evidence.",
            )

    for item in media_records.values():
        character_refs = sorted(
            character_records[record_id].external_ref
            for record_id in item.character_record_ids
        )
        normalized_media = {
            "externalRef": item.external_ref.to_dict(),
            "displayName": item.display_name,
            "kind": item.kind.value,
            "visualClass": item.visual_class.value,
            "characterRefs": [value.to_dict() for value in character_refs],
            "mime": item.mime_type,
            "bytes": item.byte_size,
            "sha256": item.content_sha256,
            "availability": item.availability,
            "archivePath": item.archive_member,
            "createdAt": item.created_at,
        }
        if item.entry_sha256 != _entry_sha256(normalized_media):
            _fail(
                "source_entry_mismatch",
                "Media record does not match its source entry evidence.",
            )

    semantic_payload = {
        "schema": SOURCE_SCHEMA_NAME,
        "schemaVersion": SOURCE_SCHEMA_VERSION,
        "provider": PROVIDER_ID,
        "accountScope": provider_scope,
        "exportId": provenance.export_id,
        "exportedAt": provenance.exported_at,
        "characters": [
            item.to_dict()
            for item in sorted(
                character_records.values(), key=lambda value: value.record_id
            )
        ],
        "media": [
            item.to_dict()
            for item in sorted(
                media_records.values(), key=lambda value: value.record_id
            )
        ],
        "lineage": [
            item.to_dict()
            for item in sorted(bundle.lineage, key=lambda value: value.edge_id)
        ],
    }
    if provenance.semantic_sha256 != hashlib.sha256(
        _canonical_bytes(semantic_payload)
    ).hexdigest():
        _fail(
            "source_semantic_mismatch",
            "Import bundle does not match its semantic source evidence.",
        )

    return grouped, media_records


def _normalize_mapping_request(
    raw: Any,
    group_by_key: Mapping[str, tuple[ImportedCharacterRecord, ...]],
) -> dict[str, tuple[str, str | None]]:
    rows = _exact_array(raw, "character_mappings", len(group_by_key))
    result: dict[str, tuple[str, str | None]] = {}
    for raw_row in rows:
        if not isinstance(raw_row, Mapping) or any(
            not isinstance(key, str) for key in raw_row
        ):
            _fail(
                "request_schema_invalid",
                "character mapping does not match the exact schema.",
            )
        mode = raw_row.get("mode")
        if not isinstance(mode, str) or mode not in {"map", "separate"}:
            _fail("mapping_mode_invalid", "Character mapping mode is invalid.")
        expected = _MAP_MAPPING_KEYS if mode == "map" else _SEPARATE_MAPPING_KEYS
        row = _exact_object(raw_row, expected, "character mapping")
        source_key = _source_record_id(
            row["source_character_key"], "source character key"
        )
        if source_key not in group_by_key:
            _fail("mapping_source_missing", "Character mapping source is unavailable.")
        if source_key in result:
            _fail("mapping_duplicate", "Character mapping source is duplicated.")
        target = (
            _exact_stable_id(row["target_character_id"], "target_character_id")
            if mode == "map"
            else None
        )
        result[source_key] = (mode, target)
    return result


def _normalize_profile_request(
    raw: Any,
    group_by_key: Mapping[str, tuple[ImportedCharacterRecord, ...]],
) -> dict[str, str]:
    rows = _exact_array(raw, "profile_selections", len(group_by_key))
    result: dict[str, str] = {}
    for raw_row in rows:
        row = _exact_object(
            raw_row, _PROFILE_SELECTION_KEYS, "profile selection"
        )
        source_key = _source_record_id(
            row["source_character_key"], "source character key"
        )
        media_record_id = _source_record_id(
            row["media_record_id"], "profile media record ID"
        )
        if source_key not in group_by_key:
            _fail("profile_character_missing", "Profile character source is unavailable.")
        if source_key in result:
            _fail("profile_duplicate", "Profile character selection is duplicated.")
        result[source_key] = media_record_id
    return result


def build_owner_import_decision_plan(
    bundle: ClipModeImportBundle,
    request: Mapping[str, Any],
    *,
    owner_user_id: str,
    account_id: str,
) -> OwnerImportDecisionPlan:
    """Validate and hash an owner-bound decision without planning a commit.

    The caller must later revalidate the account, mapping targets, current
    graph, materialized bytes, and this exact plan inside a schema-8
    owner-aware confirmation transaction.
    """

    owner_user_id = _exact_stable_id(owner_user_id, "owner_user_id")
    if owner_user_id.startswith("castle-system:"):
        _fail("owner_invalid", "Offline imports require a user owner.")
    account_id = _exact_stable_id(account_id, "account_id")
    if account_id == "clipmode-import":
        _fail("account_invalid", "The legacy import namespace is unavailable.")
    if not isinstance(request, Mapping):
        _fail("request_schema_invalid", "Import decision request must be an object.")
    request = _exact_object(request, _REQUEST_KEYS, "import decision request")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        _fail("request_version_invalid", "Import decision schema version is unsupported.")

    if not isinstance(bundle, ClipModeImportBundle):
        raise TypeError("bundle must be a ClipModeImportBundle")
    provider_scope = _exact_stable_id(
        bundle.provenance.provider_scope, "provider_scope"
    )
    grouped_by_external, media_by_record = _validate_bundle(bundle, provider_scope)
    group_by_key = {
        records[0].record_id: records for records in grouped_by_external.values()
    }
    requested_mappings = _normalize_mapping_request(
        request["character_mappings"], group_by_key
    )
    requested_profiles = _normalize_profile_request(
        request["profile_selections"], group_by_key
    )

    characters: list[CharacterDecision] = []
    character_by_source_key: dict[str, CharacterDecision] = {}
    character_group_by_record: dict[str, CharacterDecision] = {}
    for source_key in sorted(group_by_key):
        records = group_by_key[source_key]
        external_id = records[0].external_ref.external_id
        mode, target = requested_mappings.get(source_key, ("separate", None))
        derived_character = _derived_id(
            "character",
            owner_user_id=owner_user_id,
            account_id=account_id,
            provider_scope=provider_scope,
            external_identity=external_id,
        )
        decision = CharacterDecision(
            source_character_key=source_key,
            source_record_ids=tuple(item.record_id for item in records),
            visual_class=records[0].visual_class.value,
            mode=mode,
            external_character_id=external_id,
            canonical_character_id=target if target is not None else derived_character,
            provider_link_id=_derived_id(
                "character_link",
                owner_user_id=owner_user_id,
                account_id=account_id,
                provider_scope=provider_scope,
                external_identity=external_id,
            ),
            target_character_id=target,
        )
        characters.append(decision)
        character_by_source_key[source_key] = decision
        for record in records:
            character_group_by_record[record.record_id] = decision

    media: list[ServerMediaIdentity] = []
    media_identity_by_record: dict[str, ServerMediaIdentity] = {}
    for record_id in sorted(media_by_record):
        item = media_by_record[record_id]
        identity = ServerMediaIdentity(
            source_media_record_id=record_id,
            kind=item.kind.value,
            visual_class=item.visual_class.value,
            availability=item.availability,
            mime_type=item.mime_type,
            signature_family=_media_signature_family(item.kind, item.mime_type),
            byte_size=item.byte_size,
            content_sha256=item.content_sha256,
            character_record_ids=item.character_record_ids,
            external_asset_id=item.external_ref.external_id,
            external_version=item.external_ref.version,
            projection_id=_derived_id(
                "projection",
                owner_user_id=owner_user_id,
                account_id=account_id,
                provider_scope=provider_scope,
                external_identity=item.external_ref.external_id,
            ),
            version_id=_derived_id(
                "version",
                owner_user_id=owner_user_id,
                account_id=account_id,
                provider_scope=provider_scope,
                external_identity=item.external_ref.external_id,
                external_version=item.external_ref.version,
            ),
            archive_member=item.archive_member,
        )
        media.append(identity)
        media_identity_by_record[record_id] = identity

    profiles: list[ProfileChoice] = []
    profile_targets: set[str] = set()
    for source_key in sorted(requested_profiles):
        media_record_id = requested_profiles[source_key]
        selected = media_by_record.get(media_record_id)
        identity = media_identity_by_record.get(media_record_id)
        if selected is None or identity is None:
            _fail("profile_media_missing", "Selected profile media is unavailable.")
        character = character_by_source_key[source_key]
        if selected.kind is not MediaKind.IMAGE:
            _fail("profile_kind_invalid", "Selected profile media must be an image.")
        if (
            selected.availability != "bundled"
            or bundle.provenance.source_kind != "zip"
        ):
            _fail("profile_not_bundled", "Selected profile media must be bundled.")
        if selected.mime_type not in _PROFILE_INPUT_MIMES:
            _fail("profile_mime_unsupported", "Selected profile image MIME is unsupported.")
        if (
            isinstance(selected.byte_size, bool)
            or not isinstance(selected.byte_size, int)
            or selected.byte_size <= 0
            or not isinstance(selected.content_sha256, str)
            or not _SHA256_RE.fullmatch(selected.content_sha256)
            or selected.archive_member is None
        ):
            _fail("profile_source_invalid", "Selected profile source evidence is incomplete.")
        archive_member = _archive_member(selected.archive_member)
        if len(selected.character_record_ids) != 1:
            _fail(
                "profile_subject_invalid",
                "Selected profile must reference exactly one source character.",
            )
        subject = selected.character_record_ids[0]
        subject_group = character_group_by_record.get(subject)
        if subject_group is None or subject_group.source_character_key != source_key:
            _fail(
                "profile_character_mismatch",
                "Selected profile belongs to another source character.",
            )
        if selected.visual_class.value != character.visual_class:
            _fail(
                "profile_class_mismatch",
                "Selected profile and character visual classes differ.",
            )
        if character.canonical_character_id in profile_targets:
            _fail(
                "profile_target_duplicate",
                "One canonical character cannot receive multiple profile choices.",
            )
        profile_targets.add(character.canonical_character_id)
        profiles.append(
            ProfileChoice(
                source_character_key=source_key,
                media_record_id=media_record_id,
                visual_class=selected.visual_class.value,
                mime_type=selected.mime_type,
                byte_size=selected.byte_size,
                source_sha256=selected.content_sha256,
                canonical_character_id=character.canonical_character_id,
                projection_id=identity.projection_id,
                version_id=identity.version_id,
                archive_member=archive_member,
            )
        )

    lineage: list[ServerLineageIdentity] = []
    for edge in sorted(bundle.lineage, key=lambda item: item.edge_id):
        parent = media_identity_by_record[edge.parent_media_record_id]
        child = media_identity_by_record[edge.child_media_record_id]
        lineage.append(
            ServerLineageIdentity(
                source_edge_id=edge.edge_id,
                relationship=edge.relationship,
                parent_projection_id=parent.projection_id,
                child_projection_id=child.projection_id,
                lineage_id=_derived_id(
                    "lineage",
                    owner_user_id=owner_user_id,
                    account_id=account_id,
                    provider_scope=provider_scope,
                    external_identity=edge.edge_id,
                ),
            )
        )

    mapping_payload = {
        "schema_version": OWNER_IMPORT_SCHEMA_VERSION,
        "decisions": [item.hash_dict() for item in characters],
    }
    profile_payload = {
        "schema_version": OWNER_IMPORT_SCHEMA_VERSION,
        "selections": [item.hash_dict() for item in profiles],
    }
    mapping_sha256 = _domain_sha256(_MAPPING_DOMAIN, mapping_payload)
    profile_selection_sha256 = _domain_sha256(_PROFILE_DOMAIN, profile_payload)
    account_binding_sha256 = _account_binding_sha256(account_id, provider_scope)
    import_decision_sha256 = _import_decision_sha256(
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=provider_scope,
        raw_source_sha256=bundle.provenance.source_sha256,
        semantic_sha256=bundle.provenance.semantic_sha256,
        mapping_sha256=mapping_sha256,
        profile_selection_sha256=profile_selection_sha256,
    )

    server_graph_payload = {
        "characters": [
            {
                "canonical_character_id": item.canonical_character_id,
                "external_character_id": item.external_character_id,
                "mode": item.mode,
                "provider_link_id": item.provider_link_id,
                "source_character_key": item.source_character_key,
                "source_record_ids": list(item.source_record_ids),
                "visual_class": item.visual_class,
            }
            for item in characters
        ],
        "lineage": [
            {
                "child_projection_id": item.child_projection_id,
                "lineage_id": item.lineage_id,
                "parent_projection_id": item.parent_projection_id,
                "relationship": item.relationship,
                "source_edge_id": item.source_edge_id,
            }
            for item in lineage
        ],
        "media": [
            {
                "archive_member": item.archive_member,
                "availability": item.availability,
                "byte_size": item.byte_size,
                "character_record_ids": list(item.character_record_ids),
                "content_sha256": item.content_sha256,
                "external_asset_id": item.external_asset_id,
                "external_version": item.external_version,
                "kind": item.kind,
                "mime_type": item.mime_type,
                "signature_family": item.signature_family,
                "projection_id": item.projection_id,
                "source_media_record_id": item.source_media_record_id,
                "version_id": item.version_id,
                "visual_class": item.visual_class,
            }
            for item in media
        ],
        "profiles": [
            {
                "archive_member": item.archive_member,
                "canonical_character_id": item.canonical_character_id,
                "media_record_id": item.media_record_id,
                "projection_id": item.projection_id,
                "source_character_key": item.source_character_key,
                "source_sha256": item.source_sha256,
                "version_id": item.version_id,
            }
            for item in profiles
        ],
    }
    server_graph_sha256 = _domain_sha256(_GRAPH_DOMAIN, server_graph_payload)

    visual_counts = Counter(item.visual_class for item in characters)
    summary = {
        "characters": len(characters),
        "character_versions": len(bundle.characters),
        "media": len(media),
        "lineage": len(lineage),
        "separate_characters": sum(item.mode == "separate" for item in characters),
        "mapped_characters": sum(item.mode == "map" for item in characters),
        "profile_selections": len(profiles),
        "selected_profile_bytes": sum(item.byte_size for item in profiles),
        "bundled_media": sum(item.availability == "bundled" for item in media),
        "remote_only_media": sum(item.availability == "remote_only" for item in media),
        "unknown_class_blockers": int(visual_counts.get("unknown", 0)),
        "character_visual_classes": {
            key: int(visual_counts.get(key, 0)) for key in sorted(_VISUAL_CLASSES)
        },
    }
    plan_core = {
        "type": _PLAN_TYPE,
        "schema_version": OWNER_IMPORT_SCHEMA_VERSION,
        "plan_version": PLAN_VERSION,
        "source": {
            "schema": SOURCE_SCHEMA_NAME,
            "schema_version": SOURCE_SCHEMA_VERSION,
            "source_kind": bundle.provenance.source_kind,
            "raw_source_sha256": bundle.provenance.source_sha256,
            "manifest_sha256": bundle.provenance.manifest_sha256,
            "semantic_sha256": bundle.provenance.semantic_sha256,
        },
        "account_binding_sha256": account_binding_sha256,
        "mapping_sha256": mapping_sha256,
        "profile_selection_sha256": profile_selection_sha256,
        "import_decision_sha256": import_decision_sha256,
        "server_graph_sha256": server_graph_sha256,
        "profile_input_contract_sha256": PROFILE_INPUT_CONTRACT_SHA256,
        "summary": summary,
        "store_snapshot_validated": False,
        "commit_available": False,
    }
    plan_sha256 = _domain_sha256(_PLAN_DOMAIN, plan_core)
    return OwnerImportDecisionPlan(
        owner_user_id=owner_user_id,
        account_id=account_id,
        provider_scope=provider_scope,
        source_kind=bundle.provenance.source_kind,
        raw_source_sha256=bundle.provenance.source_sha256,
        manifest_sha256=bundle.provenance.manifest_sha256,
        semantic_sha256=bundle.provenance.semantic_sha256,
        account_binding_sha256=account_binding_sha256,
        mapping_sha256=mapping_sha256,
        profile_selection_sha256=profile_selection_sha256,
        import_decision_sha256=import_decision_sha256,
        server_graph_sha256=server_graph_sha256,
        plan_sha256=plan_sha256,
        characters=tuple(characters),
        media=tuple(media),
        profiles=tuple(profiles),
        lineage=tuple(lineage),
        summary=MappingProxyType(
            {
                **summary,
                "character_visual_classes": MappingProxyType(
                    summary["character_visual_classes"]
                ),
            }
        ),
    )


__all__ = [
    "CharacterDecision",
    "MAX_SOURCE_CHARACTERS",
    "MAX_SOURCE_CHARACTER_REFERENCES",
    "MAX_SOURCE_LINEAGE",
    "MAX_SOURCE_MEDIA",
    "OWNER_IMPORT_SCHEMA_VERSION",
    "OwnerImportDecisionPlan",
    "OwnerImportPlanError",
    "PLAN_VERSION",
    "PROFILE_INPUT_CONTRACT_SHA256",
    "PROFILE_INPUT_CONTRACT_VERSION",
    "ProfileChoice",
    "ServerLineageIdentity",
    "ServerMediaIdentity",
    "build_owner_import_decision_plan",
]
