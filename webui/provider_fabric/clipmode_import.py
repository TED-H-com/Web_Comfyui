"""Fail-closed, offline ClipMode export importer.

ClipMode does not currently have a supported live connector contract.  This
module therefore accepts only a user-created, sanitized v1 manifest, either as
JSON or as the root ``clipmode-manifest.json`` member of a ZIP archive.  It
does not use cookies, make network requests, discover private endpoints, or
write into the provider-fabric store.

The importer deliberately exposes a small exact schema.  Provider URLs and
generic metadata bags are absent: stable external IDs are provenance, while
signed/session URLs and credentials are never import data.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from .media_signatures import SIGNATURE_PROBE_BYTES, classify_declared_media
from .models import MediaKind, VisualClass


SCHEMA_VERSION = 1
SCHEMA_NAME = "castle.clipmode-export.v1"
PROVIDER_ID = "clipmode"
MANIFEST_MEMBER = "clipmode-manifest.json"

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ACCOUNT_SCOPE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_RFC3339_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MIME = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")

_TOP_FIELDS = frozenset(
    {
        "schema",
        "schemaVersion",
        "provider",
        "accountScope",
        "exportId",
        "exportedAt",
        "characters",
        "media",
        "lineage",
    }
)
_CHARACTER_FIELDS = frozenset(
    {
        "externalId",
        "version",
        "externalModelId",
        "displayName",
        "visualClass",
        "profileMedia",
        "createdAt",
        "updatedAt",
    }
)
_MEDIA_FIELDS = frozenset(
    {
        "externalId",
        "version",
        "displayName",
        "kind",
        "visualClass",
        "characterRefs",
        "mime",
        "bytes",
        "sha256",
        "availability",
        "archivePath",
        "createdAt",
    }
)
_REFERENCE_FIELDS = frozenset({"externalId", "version"})
_LINEAGE_FIELDS = frozenset({"parent", "child", "relationship"})

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "secret_value",
        "session",
        "session_cookie",
        "token",
        "webhook_secret",
        "xsrf_token",
    }
)
_SECRET_SUFFIXES = ("_api_key", "_cookie", "_password", "_secret", "_token")
_SECRET_VALUE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{16,})\b",
        r"\bsk-[A-Za-z0-9_-]{20,}\b",
        r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|"
        r"webhook[_ -]?secret|session[_ -]?cookie)\s*[:=]\s*[^\s,;]{8,}",
        r"(?:^|[;\s])(?:session|auth|token|jwt|sid)=[^;\s]{8,}",
        r"(?:^|;\s*)(?:__Host-|__Secure-)?[A-Za-z0-9_.-]{1,64}="
        r"[A-Za-z0-9%._~+/=-]{16,}(?:;|$)",
    )
)

_ALLOWED_MIMES: Mapping[MediaKind, frozenset[str]] = MappingProxyType(
    {
        MediaKind.IMAGE: frozenset(
            {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}
        ),
        MediaKind.VIDEO: frozenset({"video/mp4", "video/webm", "video/quicktime"}),
        MediaKind.AUDIO: frozenset(
            {
                "audio/wav",
                "audio/x-wav",
                "audio/mpeg",
                "audio/mp4",
                "audio/ogg",
                "audio/flac",
            }
        ),
        MediaKind.TEXT: frozenset({"text/plain", "text/markdown"}),
    }
)
_MIME_SUFFIXES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "image/png": frozenset({".png"}),
        "image/jpeg": frozenset({".jpg", ".jpeg"}),
        "image/webp": frozenset({".webp"}),
        "image/gif": frozenset({".gif"}),
        "image/avif": frozenset({".avif"}),
        "video/mp4": frozenset({".mp4", ".m4v"}),
        "video/webm": frozenset({".webm"}),
        "video/quicktime": frozenset({".mov"}),
        "audio/wav": frozenset({".wav"}),
        "audio/x-wav": frozenset({".wav"}),
        "audio/mpeg": frozenset({".mp3"}),
        "audio/mp4": frozenset({".m4a", ".mp4"}),
        "audio/ogg": frozenset({".ogg", ".oga"}),
        "audio/flac": frozenset({".flac"}),
        "text/plain": frozenset({".txt"}),
        "text/markdown": frozenset({".md", ".markdown"}),
    }
)
_LINEAGE_RELATIONSHIPS = frozenset(
    {"reference", "first_frame", "last_frame", "audio_reference", "derived", "assembled"}
)


class ClipModeImportError(ValueError):
    """A sanitized, stable import failure suitable for a dry-run report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ManifestValidationError(ClipModeImportError):
    """The manifest does not conform to the exact v1 schema."""


class ArchiveSafetyError(ClipModeImportError):
    """An archive failed a structural or resource-limit safety check."""


class SecretMaterialError(ClipModeImportError):
    """A manifest or bundled text file appears to contain a secret."""


class IntegrityError(ClipModeImportError):
    """Declared bundled-media integrity does not match actual bytes."""


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Hard resource bounds; callers may lower them for a specific deployment."""

    max_source_bytes: int = 128 * 1024 * 1024
    max_manifest_bytes: int = 8 * 1024 * 1024
    max_members: int = 2_000
    max_member_bytes: int = 256 * 1024 * 1024
    max_total_uncompressed_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 120.0
    max_characters: int = 10_000
    max_media: int = 50_000
    max_lineage_edges: int = 100_000
    max_text_media_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        numeric = (
            self.max_source_bytes,
            self.max_manifest_bytes,
            self.max_members,
            self.max_member_bytes,
            self.max_total_uncompressed_bytes,
            self.max_characters,
            self.max_media,
            self.max_lineage_edges,
            self.max_text_media_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in numeric):
            raise ValueError("import limits must be positive integers")
        if (
            isinstance(self.max_compression_ratio, bool)
            or not isinstance(self.max_compression_ratio, (int, float))
            or not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio < 1
        ):
            raise ValueError("max_compression_ratio must be finite and at least one")


DEFAULT_LIMITS = ImportLimits()


@dataclass(frozen=True, slots=True, order=True)
class ExternalVersionRef:
    external_id: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"externalId": self.external_id, "version": self.version}


@dataclass(frozen=True, slots=True)
class ImportedCharacterRecord:
    record_id: str
    provider_id: str
    provider_scope: str
    external_ref: ExternalVersionRef
    external_model_id: str | None
    display_name: str
    visual_class: VisualClass
    profile_media_record_id: str | None
    created_at: str | None
    updated_at: str | None
    entry_sha256: str

    @property
    def catalog_only(self) -> bool:
        return self.visual_class is VisualClass.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordId": self.record_id,
            "provider": self.provider_id,
            "providerScope": self.provider_scope,
            "externalRef": self.external_ref.to_dict(),
            "externalModelId": self.external_model_id,
            "displayName": self.display_name,
            "visualClass": self.visual_class.value,
            "catalogOnly": self.catalog_only,
            "profileMediaRecordId": self.profile_media_record_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "entrySha256": self.entry_sha256,
        }


@dataclass(frozen=True, slots=True)
class ImportedMediaRecord:
    record_id: str
    provider_id: str
    provider_scope: str
    external_ref: ExternalVersionRef
    display_name: str
    kind: MediaKind
    visual_class: VisualClass
    character_record_ids: tuple[str, ...]
    mime_type: str
    byte_size: int | None
    content_sha256: str | None
    availability: str
    archive_member: str | None
    created_at: str | None
    entry_sha256: str

    @property
    def catalog_only(self) -> bool:
        return self.visual_class is VisualClass.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordId": self.record_id,
            "provider": self.provider_id,
            "providerScope": self.provider_scope,
            "externalRef": self.external_ref.to_dict(),
            "displayName": self.display_name,
            "kind": self.kind.value,
            "visualClass": self.visual_class.value,
            "catalogOnly": self.catalog_only,
            "characterRecordIds": list(self.character_record_ids),
            "mime": self.mime_type,
            "bytes": self.byte_size,
            "sha256": self.content_sha256,
            "availability": self.availability,
            "archiveMember": self.archive_member,
            "createdAt": self.created_at,
            "entrySha256": self.entry_sha256,
        }


@dataclass(frozen=True, slots=True)
class ImportedLineageEdge:
    edge_id: str
    parent_media_record_id: str
    child_media_record_id: str
    relationship: str
    entry_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "edgeId": self.edge_id,
            "parentMediaRecordId": self.parent_media_record_id,
            "childMediaRecordId": self.child_media_record_id,
            "relationship": self.relationship,
            "entrySha256": self.entry_sha256,
        }


@dataclass(frozen=True, slots=True)
class DuplicateRecord:
    kind: str
    record_id: str
    occurrences: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "recordId": self.record_id,
            "occurrences": self.occurrences,
        }


@dataclass(frozen=True, slots=True)
class ImportProvenance:
    source_id: str
    provider_id: str
    provider_scope: str
    export_id: str
    exported_at: str
    schema_name: str
    schema_version: int
    source_kind: str
    source_sha256: str
    manifest_sha256: str
    semantic_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "provider": self.provider_id,
            "providerScope": self.provider_scope,
            "exportId": self.export_id,
            "exportedAt": self.exported_at,
            "schema": self.schema_name,
            "schemaVersion": self.schema_version,
            "sourceKind": self.source_kind,
            "sourceSha256": self.source_sha256,
            "manifestSha256": self.manifest_sha256,
            "semanticSha256": self.semantic_sha256,
        }


@dataclass(frozen=True, slots=True)
class ClipModeImportBundle:
    provenance: ImportProvenance
    characters: tuple[ImportedCharacterRecord, ...]
    media: tuple[ImportedMediaRecord, ...]
    lineage: tuple[ImportedLineageEdge, ...]
    duplicates: tuple[DuplicateRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance.to_dict(),
            "characters": [item.to_dict() for item in self.characters],
            "media": [item.to_dict() for item in self.media],
            "lineage": [item.to_dict() for item in self.lineage],
            "duplicates": [item.to_dict() for item in self.duplicates],
        }


def _error(error_type: type[ClipModeImportError], code: str, message: str) -> None:
    raise error_type(code, message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ManifestValidationError("manifest.not_canonical", "manifest is not canonical JSON data") from exc


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_id(namespace: str, account_scope: str, *values: str) -> str:
    digest = _sha(_canonical_bytes([PROVIDER_ID, account_scope, namespace, *values]))
    return f"clipmode.{namespace}:{digest}"


def _secret_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _url_has_secret(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        # Credential scanners should fail closed on malformed URL-shaped text,
        # never crash into an unsanitized parser exception.
        return True
    if parsed.username or parsed.password:
        return True
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if _secret_key(key) or lowered.startswith(("x-amz-", "x-goog-")) or lowered in {
            "signature",
            "sig",
        }:
            return True
    return False


def _string_has_secret(value: str) -> bool:
    if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        return True
    for match in re.finditer(r"https?://[^\s<>\"']+", value, re.IGNORECASE):
        if _url_has_secret(match.group(0).rstrip(".,);]")):
            return True
    return False


def _assert_no_secrets(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for key, child in current.items():
                if _secret_key(key):
                    _error(SecretMaterialError, "secret.field", "manifest contains a credential-shaped field")
                pending.append(child)
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str) and _string_has_secret(current):
            _error(SecretMaterialError, "secret.value", "manifest contains a credential-shaped value")


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _error(ManifestValidationError, "json.duplicate_key", "JSON objects cannot contain duplicate keys")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _error(ManifestValidationError, "json.non_finite", "JSON cannot contain non-finite numbers")


def _decode_json(data: bytes, *, limits: ImportLimits) -> Mapping[str, Any]:
    if len(data) > limits.max_manifest_bytes:
        _error(ArchiveSafetyError, "manifest.too_large", "manifest exceeds the configured size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("json.encoding", "manifest must be UTF-8 without binary data") from exc
    if text.startswith("\ufeff"):
        _error(ManifestValidationError, "json.bom", "manifest must not contain a byte-order mark")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ClipModeImportError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ManifestValidationError("json.invalid", "manifest is not valid bounded JSON") from exc
    if not isinstance(value, Mapping):
        _error(ManifestValidationError, "schema.root", "manifest root must be an object")
    _assert_no_secrets(value)
    return value


def _exact_fields(value: Any, fields: frozenset[str], *, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _error(ManifestValidationError, code, "manifest object does not match the exact schema")
    return value


def _bounded_list(value: Any, maximum: int, *, code: str) -> list[Any]:
    if not isinstance(value, list):
        _error(ManifestValidationError, code, "manifest collection must be an array")
    if len(value) > maximum:
        _error(ArchiveSafetyError, f"{code}.too_many", "manifest collection exceeds its item limit")
    return value


def _required_text(value: Any, *, maximum: int, code: str, reject_uri: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _error(ManifestValidationError, code, "manifest text field is invalid")
    if any(
        ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    ):
        _error(ManifestValidationError, code, "manifest text field contains control characters")
    if reject_uri and ("://" in value or value.startswith(("data:", "file:"))):
        _error(ManifestValidationError, code, "external IDs cannot be URLs or locators")
    return value


def _optional_text(value: Any, *, maximum: int, code: str, reject_uri: bool = False) -> str | None:
    if value is None:
        return None
    return _required_text(value, maximum=maximum, code=code, reject_uri=reject_uri)


def _timestamp(value: Any, *, code: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _RFC3339_UTC.fullmatch(value):
        _error(ManifestValidationError, code, "timestamp must be RFC3339 UTC")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestValidationError(code, "timestamp must be a real RFC3339 UTC instant") from exc
    return value


def _visual_class(value: Any) -> VisualClass:
    if not isinstance(value, str):
        _error(ManifestValidationError, "visual_class.invalid", "visualClass is invalid")
    try:
        return VisualClass(value)
    except ValueError as exc:
        raise ManifestValidationError(
            "visual_class.invalid",
            "visualClass must be anime, realistic, illustration, or unknown",
        ) from exc


def _media_kind(value: Any) -> MediaKind:
    if not isinstance(value, str):
        _error(ManifestValidationError, "media.kind", "media kind is invalid")
    try:
        kind = MediaKind(value)
    except ValueError as exc:
        raise ManifestValidationError("media.kind", "media kind is unsupported") from exc
    if kind not in _ALLOWED_MIMES:
        _error(ManifestValidationError, "media.kind", "media kind is unsupported for ClipMode import")
    return kind


def _reference(value: Any, *, code: str) -> ExternalVersionRef:
    item = _exact_fields(value, _REFERENCE_FIELDS, code=code)
    return ExternalVersionRef(
        _required_text(item["externalId"], maximum=300, code=code, reject_uri=True),
        _required_text(item["version"], maximum=200, code=code, reject_uri=True),
    )


def _normalized_archive_path(value: Any) -> str:
    text = _required_text(value, maximum=1024, code="archive.path")
    if "\\" in text or ":" in text or text.startswith("/") or text.endswith("/"):
        _error(ArchiveSafetyError, "archive.path", "archive member path is unsafe")
    normalized = unicodedata.normalize("NFC", text)
    if text != normalized:
        _error(ArchiveSafetyError, "archive.path", "archive member path must use NFC Unicode")
    raw_parts = text.split("/")
    if any(
        part in {"", ".", ".."}
        or part != part.strip()
        or part.endswith(".")
        for part in raw_parts
    ):
        _error(ArchiveSafetyError, "archive.path", "archive member path is unsafe")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        _error(ArchiveSafetyError, "archive.path", "archive member path is unsafe")
    return path.as_posix()


def _mime(kind: MediaKind, value: Any) -> str:
    if not isinstance(value, str):
        _error(ManifestValidationError, "media.mime", "media MIME type is invalid")
    normalized = value.strip().lower()
    if not _MIME.fullmatch(normalized) or normalized not in _ALLOWED_MIMES[kind]:
        _error(ManifestValidationError, "media.mime", "media MIME type is not allowlisted for its kind")
    return normalized


def _optional_size(value: Any, *, bundled: bool) -> int | None:
    if value is None and not bundled:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _error(ManifestValidationError, "media.bytes", "bundled media must declare a positive byte size")
    return value


def _optional_sha256(value: Any, *, bundled: bool) -> str | None:
    if value is None and not bundled:
        return None
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        _error(ManifestValidationError, "media.sha256", "bundled media must declare a lowercase SHA-256")
    return value


def _entry_hash(value: Mapping[str, Any]) -> str:
    return _sha(_canonical_bytes(value))


def _dedupe(
    values: list[tuple[ExternalVersionRef, Mapping[str, Any]]],
    *,
    kind: str,
    account_scope: str,
) -> tuple[dict[ExternalVersionRef, Mapping[str, Any]], dict[tuple[str, str], int]]:
    unique: dict[ExternalVersionRef, Mapping[str, Any]] = {}
    counts: dict[tuple[str, str], int] = {}
    hashes: dict[ExternalVersionRef, str] = {}
    for ref, normalized in values:
        digest = _entry_hash(normalized)
        if ref in unique:
            if hashes[ref] != digest:
                _error(
                    ManifestValidationError,
                    f"duplicate.{kind}.conflict",
                    "duplicate provider version has conflicting data",
                )
            stable_id = _stable_id(kind, account_scope, ref.external_id, ref.version)
            counts[(kind, stable_id)] = counts.get((kind, stable_id), 1) + 1
            continue
        unique[ref] = normalized
        hashes[ref] = digest
    return unique, counts


def _read_source(source: bytes | bytearray | memoryview | str | os.PathLike[str], limit: int) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        if len(data) > limit:
            _error(ArchiveSafetyError, "source.too_large", "import source exceeds the configured size limit")
        return data

    path = Path(source)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ArchiveSafetyError("source.open", "import source must be a readable regular non-symlink file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            _error(ArchiveSafetyError, "source.type_or_size", "import source must be a bounded regular file")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            _error(ArchiveSafetyError, "source.too_large", "import source exceeds the configured size limit")
        return data
    finally:
        os.close(descriptor)


def _zip_member_kind(info: zipfile.ZipInfo) -> str:
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.create_system == 3 and mode:
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            return "symlink"
        if file_type == stat.S_IFDIR:
            return "directory"
        # Many ZIP writers record Unix permissions but omit the file-type
        # bits.  Zero is therefore a regular/unknown member, not a device.
        if file_type not in {0, stat.S_IFREG}:
            return "special"
    return "directory" if info.is_dir() else "file"


def _validate_zip_catalog(
    archive: zipfile.ZipFile,
    limits: ImportLimits,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > limits.max_members:
        _error(ArchiveSafetyError, "archive.file_count", "archive contains too many members")
    catalog: dict[str, zipfile.ZipInfo] = {}
    collision_keys: set[str] = set()
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            _error(ArchiveSafetyError, "archive.encrypted", "encrypted ZIP members are not accepted")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            _error(ArchiveSafetyError, "archive.compression", "ZIP compression method is not allowlisted")
        if info.orig_filename != info.filename:
            _error(ArchiveSafetyError, "archive.path", "ZIP member path contains a forbidden null byte")
        kind = _zip_member_kind(info)
        if kind in {"symlink", "special"}:
            _error(ArchiveSafetyError, "archive.special_file", "ZIP links and special files are not accepted")
        raw_name = info.filename[:-1] if kind == "directory" and info.filename.endswith("/") else info.filename
        name = _normalized_archive_path(raw_name)
        collision = unicodedata.normalize("NFC", name).casefold()
        if collision in collision_keys:
            _error(ArchiveSafetyError, "archive.name_collision", "ZIP member names must be unique after normalization")
        collision_keys.add(collision)
        if kind == "directory":
            if info.file_size != 0:
                _error(ArchiveSafetyError, "archive.directory_size", "ZIP directories must be empty entries")
            continue
        if info.file_size > limits.max_member_bytes:
            _error(ArchiveSafetyError, "archive.member_size", "ZIP member exceeds its size limit")
        total += info.file_size
        if total > limits.max_total_uncompressed_bytes:
            _error(ArchiveSafetyError, "archive.total_size", "ZIP uncompressed total exceeds its size limit")
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > limits.max_compression_ratio:
            _error(ArchiveSafetyError, "archive.compression_ratio", "ZIP member has an unsafe compression ratio")
        catalog[name] = info
    if MANIFEST_MEMBER not in catalog:
        _error(ManifestValidationError, "archive.manifest_missing", "ZIP must contain one root ClipMode manifest")
    return catalog


def _validate_zip_envelope(data: bytes) -> None:
    """Reject self-extracting/polyglot prefixes, comments, and trailing data.

    Import archives are deliberately smaller than ZIP64 limits, so the classic
    zero-comment EOCD record is the only accepted outer envelope.  This keeps
    opaque bytes from riding alongside an otherwise valid sanitized export.
    """

    if not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        _error(ArchiveSafetyError, "archive.envelope", "ZIP has an unsupported outer envelope")
    offset = data.rfind(b"PK\x05\x06")
    if offset < 0 or offset + 22 != len(data):
        _error(ArchiveSafetyError, "archive.envelope", "ZIP comments and trailing data are not accepted")
    comment_length = int.from_bytes(data[offset + 20 : offset + 22], "little")
    if comment_length != 0:
        _error(ArchiveSafetyError, "archive.envelope", "ZIP comments are not accepted")


def _read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    maximum: int,
) -> bytes:
    if info.file_size > maximum:
        _error(ArchiveSafetyError, "archive.member_size", "ZIP member exceeds its read limit")
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    _error(ArchiveSafetyError, "archive.member_size", "ZIP member exceeds its read limit")
                chunks.append(chunk)
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
        raise IntegrityError("archive.read", "ZIP member could not be verified") from exc
    if total != info.file_size:
        _error(IntegrityError, "archive.size", "ZIP member size does not match its catalog entry")
    return b"".join(chunks)


def _verify_bundled_media(
    archive: zipfile.ZipFile,
    catalog: Mapping[str, zipfile.ZipInfo],
    expected: Mapping[str, tuple[int, str, str]],
    limits: ImportLimits,
) -> None:
    allowed_files = {MANIFEST_MEMBER, *expected}
    if set(catalog) != allowed_files:
        _error(ArchiveSafetyError, "archive.unreferenced_member", "ZIP contains an unreferenced regular member")

    for member in sorted(expected):
        expected_size, expected_sha, mime_type = expected[member]
        info = catalog.get(member)
        if info is None:
            _error(IntegrityError, "media.member_missing", "declared bundled media is missing")
        if info.file_size != expected_size:
            _error(IntegrityError, "media.size_mismatch", "bundled media size does not match the manifest")
        suffix = PurePosixPath(member).suffix.lower()
        if suffix not in _MIME_SUFFIXES[mime_type]:
            _error(IntegrityError, "media.extension_mismatch", "bundled media extension does not match its MIME type")

        digest = hashlib.sha256()
        total = 0
        first = bytearray()
        text_bytes = bytearray() if mime_type.startswith("text/") else None
        if text_bytes is not None and info.file_size > limits.max_text_media_bytes:
            _error(ArchiveSafetyError, "media.text_too_large", "bundled text media exceeds its size limit")
        try:
            with archive.open(info, "r") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limits.max_member_bytes or total > expected_size:
                        _error(ArchiveSafetyError, "media.stream_limit", "bundled media exceeded its declared limit")
                    digest.update(chunk)
                    if len(first) < SIGNATURE_PROBE_BYTES:
                        first.extend(chunk[: SIGNATURE_PROBE_BYTES - len(first)])
                    if text_bytes is not None:
                        text_bytes.extend(chunk)
        except ClipModeImportError:
            raise
        except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
            raise IntegrityError("media.read", "bundled media could not be verified") from exc
        if total != expected_size or digest.hexdigest() != expected_sha:
            _error(IntegrityError, "media.hash_mismatch", "bundled media hash does not match the manifest")
        complete_text = bytes(text_bytes) if text_bytes is not None else None
        if (
            classify_declared_media(
                mime_type,
                bytes(first),
                complete_text=complete_text,
            )
            is None
        ):
            _error(IntegrityError, "media.magic_mismatch", "bundled media content does not match its MIME type")
        if text_bytes is not None:
            try:
                text = bytes(text_bytes).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IntegrityError("media.text_encoding", "bundled text media must be UTF-8") from exc
            if _string_has_secret(text):
                _error(
                    SecretMaterialError,
                    "secret.text_media",
                    "bundled text media contains credential-shaped content",
                )


def _lineage_is_acyclic(edges: Sequence[tuple[str, str]]) -> bool:
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


def _build_bundle(
    raw: Mapping[str, Any],
    *,
    source_kind: str,
    source_sha256: str,
    manifest_sha256: str,
    limits: ImportLimits,
    archive: zipfile.ZipFile | None = None,
    catalog: Mapping[str, zipfile.ZipInfo] | None = None,
) -> ClipModeImportBundle:
    manifest = _exact_fields(raw, _TOP_FIELDS, code="schema.top")
    if (
        manifest["schema"] != SCHEMA_NAME
        or type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != SCHEMA_VERSION
    ):
        _error(ManifestValidationError, "schema.version", "manifest schema/version is unsupported")
    if manifest["provider"] != PROVIDER_ID:
        _error(ManifestValidationError, "schema.provider", "manifest provider must be clipmode")
    account_scope = _required_text(
        manifest["accountScope"], maximum=128, code="account.scope", reject_uri=True
    )
    if not _ACCOUNT_SCOPE.fullmatch(account_scope):
        _error(
            ManifestValidationError,
            "account.scope",
            "accountScope must be an opaque lowercase stable identifier",
        )
    export_id = _required_text(manifest["exportId"], maximum=200, code="export.id", reject_uri=True)
    exported_at = _timestamp(manifest["exportedAt"], code="export.timestamp")
    assert exported_at is not None

    raw_characters = _bounded_list(manifest["characters"], limits.max_characters, code="characters")
    raw_media = _bounded_list(manifest["media"], limits.max_media, code="media")
    raw_lineage = _bounded_list(manifest["lineage"], limits.max_lineage_edges, code="lineage")

    parsed_characters: list[tuple[ExternalVersionRef, Mapping[str, Any]]] = []
    for raw_character in raw_characters:
        item = _exact_fields(raw_character, _CHARACTER_FIELDS, code="schema.character")
        ref = ExternalVersionRef(
            _required_text(item["externalId"], maximum=300, code="character.external_id", reject_uri=True),
            _required_text(item["version"], maximum=200, code="character.version", reject_uri=True),
        )
        profile = None if item["profileMedia"] is None else _reference(item["profileMedia"], code="character.profile")
        normalized = {
            "externalRef": ref.to_dict(),
            "externalModelId": _optional_text(
                item["externalModelId"], maximum=300, code="character.external_model_id", reject_uri=True
            ),
            "displayName": _required_text(item["displayName"], maximum=200, code="character.display_name"),
            "visualClass": _visual_class(item["visualClass"]).value,
            "profileMedia": profile.to_dict() if profile else None,
            "createdAt": _timestamp(item["createdAt"], code="character.created_at", optional=True),
            "updatedAt": _timestamp(item["updatedAt"], code="character.updated_at", optional=True),
        }
        if normalized["createdAt"] and normalized["updatedAt"] and normalized["updatedAt"] < normalized["createdAt"]:
            _error(ManifestValidationError, "character.timestamp_order", "character updatedAt precedes createdAt")
        parsed_characters.append((ref, normalized))

    parsed_media: list[tuple[ExternalVersionRef, Mapping[str, Any]]] = []
    for raw_asset in raw_media:
        item = _exact_fields(raw_asset, _MEDIA_FIELDS, code="schema.media")
        ref = ExternalVersionRef(
            _required_text(item["externalId"], maximum=300, code="media.external_id", reject_uri=True),
            _required_text(item["version"], maximum=200, code="media.version", reject_uri=True),
        )
        kind = _media_kind(item["kind"])
        mime_type = _mime(kind, item["mime"])
        availability = item["availability"]
        if not isinstance(availability, str) or availability not in {"remote_only", "bundled"}:
            _error(ManifestValidationError, "media.availability", "media availability is invalid")
        bundled = availability == "bundled"
        if bundled and source_kind != "zip":
            _error(ManifestValidationError, "media.bundle_source", "bundled media requires a ZIP source")
        archive_member = _normalized_archive_path(item["archivePath"]) if bundled else None
        if archive_member == MANIFEST_MEMBER:
            _error(ArchiveSafetyError, "media.reserved_path", "media cannot alias the archive manifest")
        if not bundled and item["archivePath"] is not None:
            _error(ManifestValidationError, "media.remote_path", "remote-only media cannot declare an archive path")
        character_refs = tuple(
            _reference(value, code="media.character_ref")
            for value in _bounded_list(item["characterRefs"], limits.max_characters, code="media.character_refs")
        )
        if len(character_refs) != len(set(character_refs)):
            _error(
                ManifestValidationError,
                "media.character_ref_duplicate",
                "media character references must be unique",
            )
        normalized = {
            "externalRef": ref.to_dict(),
            "displayName": _required_text(item["displayName"], maximum=300, code="media.display_name"),
            "kind": kind.value,
            "visualClass": _visual_class(item["visualClass"]).value,
            "characterRefs": [value.to_dict() for value in sorted(character_refs)],
            "mime": mime_type,
            "bytes": _optional_size(item["bytes"], bundled=bundled),
            "sha256": _optional_sha256(item["sha256"], bundled=bundled),
            "availability": availability,
            "archivePath": archive_member,
            "createdAt": _timestamp(item["createdAt"], code="media.created_at", optional=True),
        }
        parsed_media.append((ref, normalized))

    characters_by_ref, character_counts = _dedupe(
        parsed_characters,
        kind="character",
        account_scope=account_scope,
    )
    media_by_ref, media_counts = _dedupe(
        parsed_media,
        kind="media",
        account_scope=account_scope,
    )
    duplicate_counts = {**character_counts, **media_counts}

    character_record_ids = {
        ref: _stable_id("character", account_scope, ref.external_id, ref.version)
        for ref in characters_by_ref
    }
    media_record_ids = {
        ref: _stable_id("media", account_scope, ref.external_id, ref.version)
        for ref in media_by_ref
    }

    media_records: list[ImportedMediaRecord] = []
    expected_files: dict[str, tuple[int, str, str]] = {}
    for ref, item in media_by_ref.items():
        resolved_character_ids: list[str] = []
        for raw_ref in item["characterRefs"]:
            character_ref = ExternalVersionRef(raw_ref["externalId"], raw_ref["version"])
            record_id = character_record_ids.get(character_ref)
            if record_id is None:
                _error(
                    ManifestValidationError,
                    "reference.character_missing",
                    "media references a missing character version",
                )
            resolved_character_ids.append(record_id)
        archive_member = item["archivePath"]
        if archive_member is not None:
            expected = (item["bytes"], item["sha256"], item["mime"])
            existing = expected_files.get(archive_member)
            if existing is not None and existing != expected:
                _error(
                    ManifestValidationError,
                    "media.archive_path_conflict",
                    "one archive member has conflicting declarations",
                )
            expected_files[archive_member] = expected
        media_records.append(
            ImportedMediaRecord(
                record_id=media_record_ids[ref],
                provider_id=PROVIDER_ID,
                provider_scope=account_scope,
                external_ref=ref,
                display_name=item["displayName"],
                kind=MediaKind(item["kind"]),
                visual_class=VisualClass(item["visualClass"]),
                character_record_ids=tuple(sorted(resolved_character_ids)),
                mime_type=item["mime"],
                byte_size=item["bytes"],
                content_sha256=item["sha256"],
                availability=item["availability"],
                archive_member=archive_member,
                created_at=item["createdAt"],
                entry_sha256=_entry_hash(item),
            )
        )

    character_records: list[ImportedCharacterRecord] = []
    media_record_map = {item.record_id: item for item in media_records}
    for ref, item in characters_by_ref.items():
        profile_record_id = None
        if item["profileMedia"] is not None:
            profile_ref = ExternalVersionRef(
                item["profileMedia"]["externalId"], item["profileMedia"]["version"]
            )
            profile_record_id = media_record_ids.get(profile_ref)
            if profile_record_id is None:
                _error(
                    ManifestValidationError,
                    "reference.profile_missing",
                    "character profile references missing media",
                )
            profile = media_record_map[profile_record_id]
            if profile.kind is not MediaKind.IMAGE:
                _error(ManifestValidationError, "reference.profile_kind", "character profile media must be an image")
            character_id = character_record_ids[ref]
            if character_id not in profile.character_record_ids:
                _error(
                    ManifestValidationError,
                    "reference.profile_owner",
                    "profile media must reference its character version",
                )
        character_records.append(
            ImportedCharacterRecord(
                record_id=character_record_ids[ref],
                provider_id=PROVIDER_ID,
                provider_scope=account_scope,
                external_ref=ref,
                external_model_id=item["externalModelId"],
                display_name=item["displayName"],
                visual_class=VisualClass(item["visualClass"]),
                profile_media_record_id=profile_record_id,
                created_at=item["createdAt"],
                updated_at=item["updatedAt"],
                entry_sha256=_entry_hash(item),
            )
        )

    parsed_edges: dict[tuple[str, str, str], ImportedLineageEdge] = {}
    edge_counts: dict[tuple[str, str], int] = {}
    for raw_edge in raw_lineage:
        item = _exact_fields(raw_edge, _LINEAGE_FIELDS, code="schema.lineage")
        parent_ref = _reference(item["parent"], code="lineage.parent")
        child_ref = _reference(item["child"], code="lineage.child")
        relationship = item["relationship"]
        if not isinstance(relationship, str) or relationship not in _LINEAGE_RELATIONSHIPS:
            _error(ManifestValidationError, "lineage.relationship", "lineage relationship is unsupported")
        parent_id = media_record_ids.get(parent_ref)
        child_id = media_record_ids.get(child_ref)
        if parent_id is None or child_id is None:
            _error(ManifestValidationError, "reference.lineage_missing", "lineage references missing media")
        if parent_id == child_id:
            _error(ManifestValidationError, "lineage.self", "lineage cannot reference itself")
        key = (parent_id, child_id, relationship)
        edge_id = _stable_id("lineage", account_scope, *key)
        normalized_edge = {
            "parentMediaRecordId": parent_id,
            "childMediaRecordId": child_id,
            "relationship": relationship,
        }
        edge = ImportedLineageEdge(edge_id, parent_id, child_id, relationship, _entry_hash(normalized_edge))
        if key in parsed_edges:
            duplicate_key = ("lineage", edge_id)
            edge_counts[duplicate_key] = edge_counts.get(duplicate_key, 1) + 1
        else:
            parsed_edges[key] = edge
    duplicate_counts.update(edge_counts)

    lineage_records = sorted(parsed_edges.values(), key=lambda item: item.edge_id)
    if not _lineage_is_acyclic(
        [(item.parent_media_record_id, item.child_media_record_id) for item in lineage_records]
    ):
        _error(ManifestValidationError, "lineage.cycle", "lineage must be acyclic")

    if source_kind == "zip":
        if archive is None or catalog is None:
            raise AssertionError("ZIP validation context is required")
        _verify_bundled_media(archive, catalog, expected_files, limits)
    elif expected_files:
        raise AssertionError("JSON imports cannot have bundled files")

    character_records.sort(key=lambda item: item.record_id)
    media_records.sort(key=lambda item: item.record_id)
    duplicates = tuple(
        DuplicateRecord(kind, record_id, occurrences)
        for (kind, record_id), occurrences in sorted(duplicate_counts.items())
    )

    semantic_payload = {
        "schema": SCHEMA_NAME,
        "schemaVersion": SCHEMA_VERSION,
        "provider": PROVIDER_ID,
        "accountScope": account_scope,
        "exportId": export_id,
        "exportedAt": exported_at,
        "characters": [item.to_dict() for item in character_records],
        "media": [item.to_dict() for item in media_records],
        "lineage": [item.to_dict() for item in lineage_records],
    }
    semantic_sha256 = _sha(_canonical_bytes(semantic_payload))
    provenance = ImportProvenance(
        source_id=f"clipmode.import:{semantic_sha256}",
        provider_id=PROVIDER_ID,
        provider_scope=account_scope,
        export_id=export_id,
        exported_at=exported_at,
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        source_kind=source_kind,
        source_sha256=source_sha256,
        manifest_sha256=manifest_sha256,
        semantic_sha256=semantic_sha256,
    )
    return ClipModeImportBundle(
        provenance=provenance,
        characters=tuple(character_records),
        media=tuple(media_records),
        lineage=tuple(lineage_records),
        duplicates=duplicates,
    )


def import_clipmode_export(
    source: bytes | bytearray | memoryview | str | os.PathLike[str],
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> ClipModeImportBundle:
    """Parse and integrity-check a sanitized ClipMode JSON or ZIP export.

    The function is read-only and deterministic.  It returns domain-neutral
    records suitable for a later dry-run/matching layer; it never mutates the
    provider store or extracts archive members.
    """

    if not isinstance(limits, ImportLimits):
        raise TypeError("limits must be ImportLimits")
    data = _read_source(source, limits.max_source_bytes)
    if not data:
        _error(ManifestValidationError, "source.empty", "import source is empty")
    source_sha256 = _sha(data)

    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        _validate_zip_envelope(data)
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                catalog = _validate_zip_catalog(archive, limits)
                manifest_bytes = _read_zip_member(
                    archive,
                    catalog[MANIFEST_MEMBER],
                    limits.max_manifest_bytes,
                )
                manifest = _decode_json(manifest_bytes, limits=limits)
                return _build_bundle(
                    manifest,
                    source_kind="zip",
                    source_sha256=source_sha256,
                    manifest_sha256=_sha(manifest_bytes),
                    limits=limits,
                    archive=archive,
                    catalog=catalog,
                )
        except ClipModeImportError:
            raise
        except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
            raise ArchiveSafetyError("archive.invalid", "source is not a valid bounded ZIP archive") from exc

    manifest = _decode_json(data, limits=limits)
    return _build_bundle(
        manifest,
        source_kind="json",
        source_sha256=source_sha256,
        manifest_sha256=_sha(data),
        limits=limits,
    )


__all__ = [
    "ArchiveSafetyError",
    "ClipModeImportBundle",
    "ClipModeImportError",
    "DEFAULT_LIMITS",
    "DuplicateRecord",
    "ExternalVersionRef",
    "ImportLimits",
    "ImportProvenance",
    "ImportedCharacterRecord",
    "ImportedLineageEdge",
    "ImportedMediaRecord",
    "IntegrityError",
    "MANIFEST_MEMBER",
    "ManifestValidationError",
    "PROVIDER_ID",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "SecretMaterialError",
    "import_clipmode_export",
]
