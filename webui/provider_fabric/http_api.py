"""Small, framework-neutral HTTP boundary for the provider fabric.

The web server remains responsible for authenticating a Castle session,
enforcing its same-origin/CSRF policy on writes, decoding JSON without
duplicate keys, and imposing its request-body size limit.  This adapter starts
*after* those generic HTTP controls and deliberately requires an
``ActorContext`` for every operation.  It gives server.py a narrow, testable
contract while keeping provider credentials write-only and remote media URLs
private.

No method in this module performs provider transport.  Planning is read-only;
confirmation only persists the service's durable pending outbox record.
ClipMode has one administrator-only offline preview boundary.  The separately
wired fixed AutoIP webhook accepts only a signed raw-body hint and delegates to
the normal authoritative task GET reconciler.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl

from .clipmode_preview import ClipModePreviewError, build_clipmode_import_preview
from .models import MediaKind, VisualClass
from .service import ActorContext, ProviderFabricService, ProviderServiceError
from .store import (
    CHARACTER_LORA_CAPABILITY_ID,
    ProviderFabricStore,
    ProviderFabricStoreError,
    SecretFieldError,
    StoreConflictError,
    is_supported_autoip_history_visual_version,
    matches_deterministic_autoip_history_identity,
)


HTTP_SCHEMA_VERSION = 1
DEFAULT_ASSET_PAGE_SIZE = 48
MAX_ASSET_PAGE_SIZE = 100
DEFAULT_CHARACTER_PAGE_SIZE = 200
MAX_CHARACTER_PAGE_SIZE = 200

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_PUBLIC_INTENT_ID_RE = re.compile(r"^intent:[0-9a-f]{32}$")
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]{20,4096}\.[A-Za-z0-9_-]{20,256}$")
_PUBLIC_PREVIEW_RE = re.compile(r"^/api/assets/[a-z0-9][a-z0-9._:-]{0,127}/preview$")
_PUBLIC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_ASSET_QUERY_KEYS = frozenset(
    {"kind", "visualClass", "characterId", "origin", "catalogRole", "cursor"}
)
_CHARACTER_QUERY_KEYS = frozenset({"visualClass", "cursor"})
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credential_value",
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
_SENSITIVE_TEXT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"https?://",
        r"(?:^|\s)//[a-z0-9.-]+(?:/|\s|$)",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"\baip_[A-Za-z0-9._-]{16,}\b",
        r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|"
        r"webhook[_ -]?secret|session[_ -]?cookie)\s*[:=]\s*[^\s,;]{8,}",
    )
)
_PUBLIC_METADATA_FIELDS = frozenset(
    {
        "display_name",
        "displayName",
        "title",
        "name",
        "excerpt",
        "text_preview",
        "summary",
        "width",
        "height",
        "catalogOnly",
        "projectionOnly",
        "availability",
        "local_history_key",
    }
)


PreviewResolver = Callable[[ActorContext, Mapping[str, Any]], str | None]
InventoryProjector = Callable[[ActorContext, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """JSON response data independent of ``BaseHTTPRequestHandler``."""

    status: int
    body: Mapping[str, Any]
    headers: Mapping[str, str] = field(
        default_factory=lambda: {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Content-Type": "application/json; charset=utf-8",
            "X-Content-Type-Options": "nosniff",
        }
    )


def _public_error(code: str, message: str, *, status: int = 400) -> HttpResponse:
    return HttpResponse(
        status,
        {
            "error": {
                "code": code,
                "message": message,
                "reason_codes": [],
            }
        },
    )


def _stable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ProviderServiceError("schema_invalid", f"{label} is invalid.")
    return value


def _secret_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _assert_actor(actor: ActorContext) -> ActorContext:
    if not isinstance(actor, ActorContext):
        raise ProviderServiceError(
            "authentication_required",
            "An authenticated Castle identity is required.",
            status=401,
        )
    return actor


def _public_timestamp(value: Any, fallback: Any = None) -> str | None:
    """Return only a structurally valid timestamp, preferring provider data."""

    for candidate in (value, fallback):
        if not isinstance(candidate, str) or not _PUBLIC_TIMESTAMP_RE.fullmatch(
            candidate
        ):
            continue
        try:
            datetime.fromisoformat(candidate.removesuffix("Z") + (
                "+00:00" if candidate.endswith("Z") else ""
            ))
        except ValueError:
            continue
        return candidate
    return None


def _strict_body(value: Any, allowed: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderServiceError("schema_invalid", f"{label} must be an object.")
    if any(not isinstance(key, str) for key in value):
        raise ProviderServiceError("schema_invalid", f"{label} contains an invalid field.")
    extras = set(value) - allowed
    if extras:
        raise ProviderServiceError(
            "schema_invalid",
            f"{label} contains unsupported fields.",
            reason_codes=("unknown_fields",),
        )
    return value


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise ValueError("cursor encoding is invalid")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class ProviderFabricHttpApi:
    """Translate already-authenticated HTTP requests into service/store calls."""

    def __init__(
        self,
        service: ProviderFabricService,
        store: ProviderFabricStore,
        cursor_signing_key: bytes,
        *,
        clipmode_account_id: str,
        preview_resolver: PreviewResolver | None = None,
        inventory_projector: InventoryProjector | None = None,
        asset_page_size: int = DEFAULT_ASSET_PAGE_SIZE,
        character_page_size: int = DEFAULT_CHARACTER_PAGE_SIZE,
    ) -> None:
        if not isinstance(service, ProviderFabricService):
            raise TypeError("service must be a ProviderFabricService")
        if not isinstance(store, ProviderFabricStore):
            raise TypeError("store must be a ProviderFabricStore")
        if not isinstance(cursor_signing_key, bytes) or len(cursor_signing_key) < 32:
            raise ValueError("cursor signing key must contain at least 32 bytes")
        if (
            not isinstance(asset_page_size, int)
            or isinstance(asset_page_size, bool)
            or not 1 <= asset_page_size <= MAX_ASSET_PAGE_SIZE
        ):
            raise ValueError(f"asset_page_size must be between 1 and {MAX_ASSET_PAGE_SIZE}")
        if preview_resolver is not None and not callable(preview_resolver):
            raise TypeError("preview_resolver must be callable")
        if inventory_projector is not None and not callable(inventory_projector):
            raise TypeError("inventory_projector must be callable")
        if (
            not isinstance(character_page_size, int)
            or isinstance(character_page_size, bool)
            or not 1 <= character_page_size <= MAX_CHARACTER_PAGE_SIZE
        ):
            raise ValueError(
                f"character_page_size must be between 1 and {MAX_CHARACTER_PAGE_SIZE}"
            )
        if not isinstance(clipmode_account_id, str) or not _ID_RE.fullmatch(
            clipmode_account_id
        ):
            raise ValueError("clipmode_account_id must be a stable identifier")
        self._service = service
        self._store = store
        self._cursor_key = bytes(cursor_signing_key)
        self._clipmode_account_id = clipmode_account_id
        self._preview_resolver = preview_resolver
        self._inventory_projector = inventory_projector
        self._asset_page_size = asset_page_size
        self._character_page_size = character_page_size

    @staticmethod
    def _ok(body: Mapping[str, Any], *, status: int = 200) -> HttpResponse:
        return HttpResponse(status, body)

    @staticmethod
    def _translate(operation: Callable[[], Mapping[str, Any]]) -> HttpResponse:
        try:
            return ProviderFabricHttpApi._ok(operation())
        except ProviderServiceError as error:
            status = error.status if 400 <= error.status <= 599 else 400
            return HttpResponse(status, error.to_dict())
        except (ProviderFabricStoreError, SecretFieldError):
            return _public_error(
                "provider_storage_unavailable",
                "Provider data is temporarily unavailable.",
                status=503,
            )
        except (KeyError, TypeError, ValueError):
            return _public_error("schema_invalid", "The request is invalid.")

    def get_inventory(self, actor: ActorContext) -> HttpResponse:
        """GET /api/providers.  The caller cannot omit authenticated identity."""

        def operation() -> Mapping[str, Any]:
            current = _assert_actor(actor)
            result = self._service.inventory(current)
            return (
                self._inventory_projector(current, result)
                if self._inventory_projector is not None else result
            )

        return self._translate(operation)

    @staticmethod
    def _query_pairs(
        query: str | Mapping[str, Any] | Sequence[tuple[str, str]],
        *,
        allowed_keys: frozenset[str],
    ) -> list[tuple[str, str]]:
        if isinstance(query, str):
            if len(query) > 4_096:
                raise ProviderServiceError("schema_invalid", "Query is too large.")
            try:
                pairs = parse_qsl(
                    query[1:] if query.startswith("?") else query,
                    keep_blank_values=True,
                    max_num_fields=16,
                )
            except ValueError:
                raise ProviderServiceError("schema_invalid", "Query is invalid.") from None
        elif isinstance(query, Mapping):
            pairs = []
            for key, value in query.items():
                if isinstance(value, (list, tuple)):
                    pairs.extend((key, item) for item in value)
                else:
                    pairs.append((key, value))
        elif isinstance(query, Sequence) and not isinstance(query, (str, bytes, bytearray)):
            pairs = list(query)
        else:
            raise ProviderServiceError("schema_invalid", "Query is invalid.")
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ProviderServiceError("schema_invalid", "Query is invalid.")
            key, value = pair
            if not isinstance(key, str) or not isinstance(value, str):
                raise ProviderServiceError("schema_invalid", "Query fields must be text.")
            if _secret_key(key):
                raise ProviderServiceError(
                    "secret_field_forbidden",
                    "Credential-shaped query fields are forbidden.",
                )
            if key not in allowed_keys:
                raise ProviderServiceError(
                    "schema_invalid",
                    "Query contains unsupported fields.",
                    reason_codes=("unknown_fields",),
                )
            if key in seen:
                raise ProviderServiceError("schema_invalid", "Repeated query fields are ambiguous.")
            seen.add(key)
            normalized.append((key, value))
        return normalized

    def _cursor_scope(
        self,
        actor: ActorContext,
        *,
        kind: str | None,
        visual_class: str | None,
        character_id: str | None,
        origin: str | None,
        catalog_role: str | None,
    ) -> dict[str, Any]:
        return {
            "cursor_type": "provider_assets",
            "schema_version": HTTP_SCHEMA_VERSION,
            "actor_binding": self._actor_cursor_binding(actor),
            "kind": kind,
            "visual_class": visual_class,
            "character_id": character_id,
            "origin": origin,
            "catalog_role": catalog_role,
        }

    def _encode_cursor(
        self,
        actor: ActorContext,
        after: tuple[str, str],
        *,
        kind: str | None,
        visual_class: str | None,
        character_id: str | None,
        origin: str | None,
        catalog_role: str | None,
    ) -> str:
        payload = {
            **self._cursor_scope(
                actor,
                kind=kind,
                visual_class=visual_class,
                character_id=character_id,
                origin=origin,
                catalog_role=catalog_role,
            ),
            "after": [after[0], after[1]],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        signature = hmac.new(self._cursor_key, encoded, hashlib.sha256).digest()
        return f"{_b64encode(encoded)}.{_b64encode(signature)}"

    def _decode_cursor(
        self,
        actor: ActorContext,
        cursor: str,
        *,
        kind: str | None,
        visual_class: str | None,
        character_id: str | None,
        origin: str | None,
        catalog_role: str | None,
    ) -> tuple[str, str]:
        if not isinstance(cursor, str) or not _CURSOR_RE.fullmatch(cursor):
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.")
        encoded_text, signature_text = cursor.split(".", 1)
        try:
            encoded = _b64decode(encoded_text)
            supplied = _b64decode(signature_text)
        except (TypeError, ValueError):
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.") from None
        expected = hmac.new(self._cursor_key, encoded, hashlib.sha256).digest()
        if len(supplied) != len(expected) or not hmac.compare_digest(supplied, expected):
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.")
        try:
            value = json.loads(encoded.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.") from None
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "cursor_type",
            "actor_binding",
            "kind",
            "visual_class",
            "character_id",
            "origin",
            "catalog_role",
            "after",
        }:
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.")
        scope = self._cursor_scope(
            actor,
            kind=kind,
            visual_class=visual_class,
            character_id=character_id,
            origin=origin,
            catalog_role=catalog_role,
        )
        if any(value.get(key) != expected_value for key, expected_value in scope.items()):
            raise ProviderServiceError("cursor_scope_mismatch", "Media filters changed after paging.")
        after = value.get("after")
        if not isinstance(after, list) or len(after) != 2:
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.")
        seen_at, projection_id = after
        if not isinstance(seen_at, str) or not seen_at or len(seen_at) > 80 or "\x00" in seen_at:
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.")
        try:
            projection_id = _stable_id(projection_id, "projection_id")
        except ProviderServiceError:
            raise ProviderServiceError("cursor_invalid", "Media cursor is invalid.") from None
        return seen_at, projection_id

    def _actor_cursor_binding(self, actor: ActorContext) -> str:
        return hmac.new(
            self._cursor_key,
            b"castle-provider-cursor-actor-v1\0" + actor.user_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]

    def _encode_character_cursor(
        self,
        actor: ActorContext,
        after: tuple[str, str],
        *,
        visual_class: str | None,
    ) -> str:
        payload = {
            "cursor_type": "provider_characters",
            "schema_version": HTTP_SCHEMA_VERSION,
            "actor_binding": self._actor_cursor_binding(actor),
            "visual_class": visual_class,
            "after": [after[0], after[1]],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        signature = hmac.new(self._cursor_key, encoded, hashlib.sha256).digest()
        return f"{_b64encode(encoded)}.{_b64encode(signature)}"

    def _decode_character_cursor(
        self,
        actor: ActorContext,
        cursor: str,
        *,
        visual_class: str | None,
    ) -> tuple[str, str]:
        if not isinstance(cursor, str) or not _CURSOR_RE.fullmatch(cursor):
            raise ProviderServiceError("cursor_invalid", "Character cursor is invalid.")
        encoded_text, signature_text = cursor.split(".", 1)
        try:
            encoded = _b64decode(encoded_text)
            supplied = _b64decode(signature_text)
        except (TypeError, ValueError):
            raise ProviderServiceError(
                "cursor_invalid", "Character cursor is invalid."
            ) from None
        expected = hmac.new(self._cursor_key, encoded, hashlib.sha256).digest()
        if len(supplied) != len(expected) or not hmac.compare_digest(supplied, expected):
            raise ProviderServiceError("cursor_invalid", "Character cursor is invalid.")
        try:
            value = json.loads(encoded.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderServiceError(
                "cursor_invalid", "Character cursor is invalid."
            ) from None
        if not isinstance(value, dict) or set(value) != {
            "cursor_type",
            "schema_version",
            "actor_binding",
            "visual_class",
            "after",
        }:
            raise ProviderServiceError("cursor_invalid", "Character cursor is invalid.")
        if (
            value.get("cursor_type") != "provider_characters"
            or value.get("schema_version") != HTTP_SCHEMA_VERSION
            or value.get("actor_binding") != self._actor_cursor_binding(actor)
            or value.get("visual_class") != visual_class
        ):
            raise ProviderServiceError(
                "cursor_scope_mismatch", "Character filters changed after paging."
            )
        after = value.get("after")
        if not isinstance(after, list) or len(after) != 2:
            raise ProviderServiceError("cursor_invalid", "Character cursor is invalid.")
        updated_at, character_id = after
        if (
            not isinstance(updated_at, str)
            or not updated_at
            or len(updated_at) > 80
            or "\x00" in updated_at
        ):
            raise ProviderServiceError("cursor_invalid", "Character cursor is invalid.")
        try:
            character_id = _stable_id(character_id, "character_id")
        except ProviderServiceError:
            raise ProviderServiceError(
                "cursor_invalid", "Character cursor is invalid."
            ) from None
        return updated_at, character_id

    @staticmethod
    def _public_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        public: dict[str, Any] = {}
        for key in _PUBLIC_METADATA_FIELDS:
            child = value.get(key)
            if (
                isinstance(child, str)
                and child
                and len(child) <= 500
                and not any(pattern.search(child) for pattern in _SENSITIVE_TEXT_PATTERNS)
            ):
                public[key] = child
            elif isinstance(child, bool):
                public[key] = child
            elif isinstance(child, int) and not isinstance(child, bool) and 0 <= child <= 1_000_000_000:
                # Retain bounded scalar catalog hints, never nested provider data.
                public[key] = child
        return public

    @staticmethod
    def _asset_title(item: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
        for key in ("display_name", "displayName", "title", "name"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
        return str(item.get("projection_id") or "Untitled asset")

    def _serialize_asset(self, actor: ActorContext, item: Mapping[str, Any]) -> dict[str, Any]:
        raw_metadata = item.get("metadata")
        raw_metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        metadata = self._public_metadata(raw_metadata)
        annotation = item.get("catalog_annotation")
        annotation = annotation if isinstance(annotation, Mapping) else {}
        catalog_name = annotation.get("display_name")
        if not isinstance(catalog_name, str) or not catalog_name.strip() or len(catalog_name) > 160:
            catalog_name = None
        catalog_role = annotation.get("audio_role")
        if catalog_role not in {"unclassified", "music", "sound_effect", "voice", "ambience"}:
            catalog_role = "unclassified"
        catalog_media_role = annotation.get("media_role")
        if catalog_media_role not in {
            "unclassified", "profile", "outfit", "reference", "background", "prop", "storyboard",
            "dance_template", "motion_reference", "source_clip", "finished",
            "prompt", "script", "subtitle",
        }:
            catalog_media_role = "unclassified"
        catalog_revision = annotation.get("revision")
        if isinstance(catalog_revision, bool) or not isinstance(catalog_revision, int) or catalog_revision < 1:
            catalog_revision = 0
        version = item.get("latest_version")
        version = version if isinstance(version, Mapping) else {}
        asset_id = _stable_id(item.get("projection_id"), "projection_id")
        preview_url: str | None = None
        if self._preview_resolver is not None and version.get("preview_available") is True:
            try:
                candidate = self._preview_resolver(actor, item)
            except Exception:
                candidate = None
            if (
                isinstance(candidate, str)
                and _PUBLIC_PREVIEW_RE.fullmatch(candidate)
                and candidate == f"/api/assets/{asset_id}/preview"
            ):
                preview_url = candidate
        visual_class = item.get("visual_class")
        if visual_class not in {value.value for value in VisualClass}:
            visual_class = "unknown"
        result: dict[str, Any] = {
            "asset_id": asset_id,
            "kind": str(item.get("media_kind") or ""),
            "provider_id": str(item.get("provider_id") or ""),
            "origin": str(item.get("provider_id") or ""),
            "visual_class": visual_class,
            "character_ids": list(item.get("character_ids") or ()),
            "title": catalog_name or self._asset_title(item, metadata),
            "catalog": {
                "display_name": catalog_name,
                "audio_role": catalog_role,
                "media_role": catalog_media_role,
                "revision": catalog_revision,
                "updated_at": _public_timestamp(annotation.get("updated_at")),
            },
            "created_at": _public_timestamp(
                item.get("remote_created_at"), item.get("first_seen_at")
            ),
            "updated_at": _public_timestamp(item.get("last_seen_at")),
            "state": (
                "imported"
                if item.get("provider_id") == "clipmode"
                and version.get("source_available") is True
                else "remote_only"
            ),
            "source_available": version.get("source_available") is True,
            "preview_available": version.get("preview_available") is True,
            "mime_type": version.get("mime_type"),
            "byte_size": version.get("byte_size"),
            "duration_seconds": version.get("duration_seconds"),
            "metadata": metadata,
            # A media projection proves identity and availability only.  It
            # does not prove an exact capability/variant, runtime, licence,
            # option profile, or golden-quality gate, so generation must enter
            # through the intent planner instead of an optimistic asset flag.
            "can_generate": False,
        }
        version_id = version.get("version_id")
        digest = version.get("sha256")
        if (
            isinstance(version_id, str)
            and _stable_id(version_id, "asset_version_id") == version_id
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        ):
            # The opaque immutable ID is safe selection material. The digest,
            # provider URI, local path, and signed locator remain server-only.
            result["asset_version_id"] = version_id
            result["preparation_available"] = True
        else:
            result["preparation_available"] = False
        version_metadata = version.get("metadata")
        version_metadata = version_metadata if isinstance(version_metadata, Mapping) else {}
        result["classification_available"] = bool(
            actor.role in {"admin", "operator"}
            and item.get("media_kind") in {"image", "video"}
            and visual_class == "unknown"
            and not result["character_ids"]
            and matches_deterministic_autoip_history_identity(
                actor.user_id,
                provider_id=item.get("provider_id"),
                account_id=item.get("account_id"),
                projection_id=asset_id,
                external_asset_id=item.get("external_asset_id"),
                asset_version_id=version_id,
                provider_version_id=version.get("provider_version_id"),
                metadata=raw_metadata,
            )
            and is_supported_autoip_history_visual_version(
                item.get("media_kind"), version.get("mime_type"),
                version.get("byte_size"),
            )
            and version_metadata.get("validated_download") is True
            and version_metadata.get("legacy_history") is True
            and item.get("version_count") == 1
            and result["source_available"] is True
            and result["preparation_available"] is True
        )
        result["generation_detail_available"] = bool(
            raw_metadata.get("source") == "autoip_generation"
            and isinstance(raw_metadata.get("intent_id"), str)
            and _PUBLIC_INTENT_ID_RE.fullmatch(raw_metadata["intent_id"])
        )
        for key in ("excerpt", "text_preview", "summary"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                result["excerpt"] = value
                break
        if preview_url is not None:
            result["preview_url"] = preview_url
        if (
            version.get("source_available") is True
            and item.get("media_kind") in {"image", "video", "audio"}
        ):
            result["source_url"] = f"/api/assets/{asset_id}/source"
        return result

    def _serialize_character(
        self,
        actor: ActorContext,
        item: Mapping[str, Any],
        account_providers: Mapping[str, str],
    ) -> dict[str, Any]:
        character_id = _stable_id(item.get("character_id"), "character_id")
        visual_class = item.get("visual_class")
        if visual_class not in {value.value for value in VisualClass}:
            visual_class = VisualClass.UNKNOWN.value
        display_name = item.get("display_name")
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 160
            or any(pattern.search(display_name) for pattern in _SENSITIVE_TEXT_PATTERNS)
        ):
            display_name = character_id
        else:
            display_name = display_name.strip()

        source_provider_ids = sorted(
            {
                account_providers[str(link.get("account_id"))]
                for link in item.get("provider_links") or ()
                if isinstance(link, Mapping)
                and str(link.get("account_id")) in account_providers
            }
        )
        profile_asset_id: str | None = None
        profile_url: str | None = None
        raw_profile_id = item.get("profile_asset_id")
        if raw_profile_id is not None:
            try:
                candidate_id = _stable_id(raw_profile_id, "profile_asset_id")
                profile_asset = self._store.get_owned_remote_asset(
                    actor.user_id,
                    candidate_id,
                )
                if (
                    profile_asset.get("media_kind") == MediaKind.IMAGE.value
                    and character_id in (profile_asset.get("character_ids") or ())
                ):
                    profile_asset_id = candidate_id
                    versions = profile_asset.get("versions")
                    latest = (
                        versions[-1]
                        if isinstance(versions, (list, tuple))
                        and versions
                        and isinstance(versions[-1], Mapping)
                        else {}
                    )
                    augmented_profile = {
                        **profile_asset,
                        "provider_id": account_providers.get(
                            str(profile_asset.get("account_id")), ""
                        ),
                        "latest_version": latest,
                    }
                    serialized = self._serialize_asset(actor, augmented_profile)
                    candidate_url = serialized.get("preview_url")
                    if candidate_url == f"/api/assets/{candidate_id}/preview":
                        profile_url = candidate_url
            except (
                KeyError,
                TypeError,
                ValueError,
                ProviderFabricStoreError,
                ProviderServiceError,
            ):
                # A broken profile pointer cannot widen ownership or expose a
                # private locator.  Keep the character visible with a safe
                # fallback avatar and let a later repair flow choose again.
                profile_asset_id = None
                profile_url = None

        catalog_only = visual_class == VisualClass.UNKNOWN.value
        generation_compatible = visual_class in {
            VisualClass.ANIME.value,
            VisualClass.REALISTIC.value,
        }
        generation_entrypoint: dict[str, Any] | None = None
        if generation_compatible:
            try:
                self._store.resolve_owned_character_model_binding(
                    actor.user_id,
                    character_id,
                    CHARACTER_LORA_CAPABILITY_ID,
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                ProviderFabricStoreError,
            ):
                pass
            else:
                # Deliberately opaque: model/checkpoint names, digests and the
                # training trigger remain on the trusted executor boundary.
                generation_entrypoint = {
                    "kind": "provider_character_studio",
                    "character_id": character_id,
                    "action_id": "photoshoot",
                    "feature_id": "clipmode-character-photoshoot",
                    "variant_id": CHARACTER_LORA_CAPABILITY_ID,
                    "capability_id": CHARACTER_LORA_CAPABILITY_ID,
                    "allowed_options": [
                        {
                            "id": "aspect_ratio",
                            "values": ["768x1024", "1024x1024", "1024x768"],
                            "default": "768x1024",
                        },
                        {"id": "prompt", "max_length": 4000},
                        {"id": "negative_prompt", "max_length": 4000},
                        {
                            "id": "seed",
                            "min": -1,
                            "max": 2147483647,
                            "default": -1,
                        },
                    ],
                }
        generation_enabled = generation_entrypoint is not None
        return {
            "character_id": character_id,
            "display_name": display_name,
            "visual_class": visual_class,
            "profile_asset_id": profile_asset_id,
            "profile_url": profile_url,
            "source_provider_ids": source_provider_ids,
            "catalog_only": catalog_only,
            "generation_enabled": generation_enabled,
            "generation_entrypoint": generation_entrypoint,
            "generation_blocker": (
                "unknown_visual_class"
                if catalog_only
                else (
                    "unsupported_visual_class"
                    if not generation_compatible
                    else (
                        None
                        if generation_enabled
                        else "character_model_binding_unavailable"
                    )
                )
            ),
            "updated_at": item.get("updated_at"),
        }

    def get_characters(
        self,
        actor: ActorContext,
        query: str | Mapping[str, Any] | Sequence[tuple[str, str]] = "",
    ) -> HttpResponse:
        """GET /api/provider-characters with owner-scoped canonical profiles."""

        def operation() -> Mapping[str, Any]:
            current_actor = _assert_actor(actor)
            values = dict(
                self._query_pairs(query, allowed_keys=_CHARACTER_QUERY_KEYS)
            )
            visual_class = values.get("visualClass") or None
            if visual_class is not None:
                try:
                    visual_class = VisualClass(visual_class).value
                except ValueError:
                    raise ProviderServiceError(
                        "schema_invalid", "Character visual class is invalid."
                    ) from None
            cursor = values.get("cursor") or None
            after = None
            if cursor is not None:
                after = self._decode_character_cursor(
                    current_actor,
                    cursor,
                    visual_class=visual_class,
                )
            page = self._store.list_owned_characters(
                current_actor.user_id,
                visual_class=visual_class,
                after=after,
                limit=self._character_page_size,
            )
            accessible_accounts = self._store.list_accessible_provider_accounts(
                current_actor.user_id,
                include_disabled=True,
            )
            account_providers = {
                str(account["account_id"]): str(account["provider_id"])
                for account in accessible_accounts
            }
            account_providers.update({
                str(account["account_id"]): str(account["provider_id"])
                for account in self._store.list_owned_manual_import_accounts(
                    current_actor.user_id
                )
            })
            characters = [
                self._serialize_character(current_actor, item, account_providers)
                for item in page["items"]
            ]
            next_after = page["next_after"]
            next_cursor = (
                self._encode_character_cursor(
                    current_actor,
                    next_after,
                    visual_class=visual_class,
                )
                if next_after is not None
                else None
            )
            return {
                "schema_version": HTTP_SCHEMA_VERSION,
                "characters": characters,
                "next_cursor": next_cursor,
                "filters": {"visualClass": visual_class},
                "provider_transport_performed": False,
                "credits_spent": 0,
            }

        return self._translate(operation)

    def put_asset_catalog_annotation(
        self,
        actor: ActorContext,
        projection_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        """PUT an owner-scoped mutable catalog label; media identity stays immutable."""

        def operation() -> Mapping[str, Any]:
            current = _assert_actor(actor)
            asset_id = _stable_id(projection_id, "projection_id")
            schema_version = body.get("schema_version") if isinstance(body, Mapping) else None
            fields = {"schema_version", "display_name", "audio_role", "expected_revision"}
            if schema_version == "castle.asset-catalog-annotation.v2":
                fields.add("media_role")
            request = _strict_body(body, frozenset(fields), "asset catalog annotation")
            if schema_version not in {
                "castle.asset-catalog-annotation.v1", "castle.asset-catalog-annotation.v2",
            }:
                raise ProviderServiceError("schema_invalid", "Asset catalog schema is invalid.")
            display_name = request.get("display_name")
            if display_name is not None and (
                not isinstance(display_name, str) or not display_name.strip()
                or len(display_name.strip()) > 160
            ):
                raise ProviderServiceError("schema_invalid", "Asset display name is invalid.")
            try:
                annotation = self._store.put_owned_asset_catalog_annotation(
                    current.user_id, asset_id,
                    display_name=display_name.strip() if isinstance(display_name, str) else None,
                    audio_role=request.get("audio_role"),
                    expected_revision=request.get("expected_revision"),
                    media_role=request.get("media_role", "unclassified"),
                )
            except StoreConflictError as error:
                raise ProviderServiceError(
                    "catalog_revision_changed", "Asset catalog entry changed; reload and try again.",
                    status=409,
                ) from error
            return {
                "schema_version": (
                    "castle.asset-catalog-annotation.response.v2"
                    if schema_version == "castle.asset-catalog-annotation.v2"
                    else "castle.asset-catalog-annotation.response.v1"
                ),
                "ok": True, "asset_id": asset_id,
                "catalog": {
                    "display_name": annotation["display_name"],
                    "audio_role": annotation["audio_role"],
                    "media_role": annotation["media_role"],
                    "revision": annotation["revision"],
                    "updated_at": _public_timestamp(annotation["updated_at"]),
                },
                "provider_transport_performed": False, "credits_spent": 0,
            }

        return self._translate(operation)

    def put_asset_classification(
        self,
        actor: ActorContext,
        projection_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        """Seal one local AutoIP-history class/character decision offline."""

        def operation() -> Mapping[str, Any]:
            current = _assert_actor(actor)
            if current.role not in {"admin", "operator"}:
                raise ProviderServiceError(
                    "operator_required",
                    "Asset classification requires operator access.",
                    status=403,
                )
            asset_id = _stable_id(projection_id, "projection_id")
            request = _strict_body(
                body,
                frozenset({
                    "schema_version", "asset_version_id", "visual_class",
                    "character_ids", "confirmed",
                }),
                "asset classification",
            )
            if (
                set(request) != {
                    "schema_version", "asset_version_id", "visual_class",
                    "character_ids", "confirmed",
                }
                or request.get("schema_version") != "castle.asset-classification.v1"
                or request.get("confirmed") is not True
            ):
                raise ProviderServiceError(
                    "asset_classification_invalid",
                    "Asset classification requires an exact confirmed request.",
                    status=422,
                )
            version_id = _stable_id(
                request.get("asset_version_id"), "asset_version_id"
            )
            try:
                visual_class = VisualClass(request.get("visual_class"))
            except (TypeError, ValueError):
                raise ProviderServiceError(
                    "asset_classification_invalid",
                    "Asset visual class is invalid.",
                    status=422,
                ) from None
            if visual_class is VisualClass.UNKNOWN:
                raise ProviderServiceError(
                    "asset_classification_invalid",
                    "Unknown is not a completed asset classification.",
                    status=422,
                )
            character_ids = request.get("character_ids")
            if (
                not isinstance(character_ids, list)
                or not 1 <= len(character_ids) <= 4
                or any(not isinstance(item, str) for item in character_ids)
            ):
                raise ProviderServiceError(
                    "asset_classification_invalid",
                    "Asset classification requires 1 to 4 characters.",
                    status=422,
                )
            try:
                evidence = self._store.classify_owned_remote_asset(
                    current.user_id,
                    asset_id,
                    version_id,
                    visual_class=visual_class,
                    character_ids=character_ids,
                )
            except KeyError:
                raise ProviderServiceError(
                    "asset_not_found",
                    "Media asset was not found.",
                    status=404,
                ) from None
            except StoreConflictError as error:
                raise ProviderServiceError(
                    "asset_classification_changed",
                    "Asset classification evidence changed; reload and review it again.",
                    status=409,
                ) from error
            except (TypeError, ValueError) as error:
                raise ProviderServiceError(
                    "asset_classification_invalid",
                    "Asset does not match the reviewed classification contract.",
                    status=422,
                ) from error
            return {
                "schema_version": "castle.asset-classification.response.v1",
                "ok": True,
                "asset_id": asset_id,
                "asset_version_id": evidence["asset_version_id"],
                "classification_id": evidence["classification_id"],
                "classification_version": evidence["classification_version"],
                "visual_class": evidence["visual_class"],
                "character_ids": evidence["character_ids"],
                "classified_at": _public_timestamp(evidence["classified_at"]),
                "replayed": evidence["replayed"],
                "provider_transport_performed": False,
                "provider_write_performed": False,
                "credits_spent": 0,
                "generation_authorized": False,
            }

        return self._translate(operation)

    def get_assets(
        self,
        actor: ActorContext,
        query: str | Mapping[str, Any] | Sequence[tuple[str, str]] = "",
    ) -> HttpResponse:
        """GET /api/assets with scoped, signed keyset pagination."""

        def operation() -> Mapping[str, Any]:
            current_actor = _assert_actor(actor)
            values = dict(self._query_pairs(query, allowed_keys=_ASSET_QUERY_KEYS))
            kind = values.get("kind") or None
            if kind is not None:
                try:
                    kind = MediaKind(kind).value
                except ValueError:
                    raise ProviderServiceError("schema_invalid", "Media kind is invalid.") from None
            visual_class = values.get("visualClass") or None
            if visual_class is not None:
                try:
                    visual_class = VisualClass(visual_class).value
                except ValueError:
                    raise ProviderServiceError(
                        "schema_invalid", "Media visual class is invalid."
                    ) from None
            character_id = values.get("characterId") or None
            if character_id is not None:
                character_id = _stable_id(character_id, "characterId")
            origin = values.get("origin") or None
            if origin is not None:
                origin = _stable_id(origin, "origin")
                if origin not in {"local_comfy", "autoip", "clipmode", "import"}:
                    raise ProviderServiceError("schema_invalid", "Media origin is invalid.")
            catalog_role = values.get("catalogRole") or None
            if catalog_role is not None:
                roles_by_kind = {
                    "image": {"unclassified", "profile", "outfit", "reference", "background", "prop", "storyboard", "finished"},
                    "video": {"unclassified", "dance_template", "motion_reference", "source_clip", "finished"},
                    "audio": {"unclassified", "music", "sound_effect", "voice", "ambience"},
                    "text": {"unclassified", "prompt", "script", "subtitle"},
                }
                if kind is None or catalog_role not in roles_by_kind.get(kind, set()):
                    raise ProviderServiceError("schema_invalid", "Media catalog role is invalid.")
            limit = self._asset_page_size
            cursor = values.get("cursor") or None
            after = None
            if cursor is not None:
                after = self._decode_cursor(
                    current_actor,
                    cursor,
                    kind=kind,
                    visual_class=visual_class,
                    character_id=character_id,
                    origin=origin,
                    catalog_role=catalog_role,
                )
            # Ownership is part of the SQL predicate.  Elevated roles do not
            # widen this browser boundary: missing, legacy-unowned, and another
            # actor's projections are all simply absent from the page.
            page = self._store.list_owned_remote_assets(
                current_actor.user_id,
                provider_id=None if origin in (None, "import") else origin,
                kind=kind,
                visual_class=visual_class,
                character_id=character_id,
                catalog_role=catalog_role,
                after=after,
                limit=limit,
            )
            # ``import`` is reserved for a future first-party import origin; it
            # currently returns no provider projections instead of widening the
            # query to all origins.
            raw_items = [] if origin == "import" else page["items"]
            assets = [self._serialize_asset(current_actor, item) for item in raw_items]
            next_after = page["next_after"] if origin != "import" else None
            next_cursor = (
                self._encode_cursor(
                    current_actor,
                    next_after,
                    kind=kind,
                    visual_class=visual_class,
                    character_id=character_id,
                    origin=origin,
                    catalog_role=catalog_role,
                )
                if next_after is not None
                else None
            )
            return {
                "schema_version": HTTP_SCHEMA_VERSION,
                "assets": assets,
                "next_cursor": next_cursor,
                "filters": {
                    "kind": kind,
                    "visualClass": visual_class,
                    "characterId": character_id,
                    "origin": origin,
                    "catalogRole": catalog_role,
                },
                "provider_transport_performed": False,
                "credits_spent": 0,
            }

        return self._translate(operation)

    def put_autoip_credential(
        self,
        actor: ActorContext,
        provider_id: str,
        account_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        """PUT /api/providers/{provider}/accounts/{account}/credential."""

        def operation() -> Mapping[str, Any]:
            current_actor = _assert_actor(actor)
            path_provider = _stable_id(provider_id, "provider_id")
            path_account = _stable_id(account_id, "account_id")
            request = _strict_body(
                body,
                frozenset(
                    {
                        "schema_version",
                        "provider_id",
                        "account_id",
                        "display_name",
                        "api_base",
                        "credential_value",
                    }
                ),
                "credential request",
            )
            if request.get("provider_id") != path_provider or request.get("account_id") != path_account:
                raise ProviderServiceError(
                    "provider_account_mismatch",
                    "Credential request does not match its path binding.",
                    status=409,
                )
            return self._service.provision_credential(current_actor, request)

        return self._translate(operation)

    def post_clipmode_import_preview(
        self,
        actor: ActorContext,
        source: bytes,
    ) -> HttpResponse:
        """POST /api/providers/clipmode/imports/preview with raw JSON/ZIP bytes.

        This operation is deliberately administrator-only and read-only.  The
        source is parsed in memory, never persisted or extracted, and the
        projection layer opens only a read snapshot of the configured
        credentialless ClipMode account.
        """

        def operation() -> Mapping[str, Any]:
            current_actor = _assert_actor(actor)
            if current_actor.role != "admin":
                raise ProviderServiceError(
                    "administrator_required",
                    "ClipMode import preview requires an administrator.",
                    status=403,
                )
            try:
                self._store.get_owned_provider_account(
                    current_actor.user_id,
                    self._clipmode_account_id,
                )
            except KeyError:
                # An administrator role is not an ownership bypass.  Keep a
                # missing account and a cross-owner configured account
                # indistinguishable at the HTTP boundary.
                raise ProviderServiceError(
                    "provider_account_not_configured",
                    "Provider account was not found.",
                    status=404,
                ) from None
            try:
                return build_clipmode_import_preview(
                    source,
                    self._store,
                    account_id=self._clipmode_account_id,
                )
            except ClipModePreviewError as error:
                raise ProviderServiceError(
                    error.code,
                    str(error),
                    status=error.status,
                    reason_codes=(error.reason_code,) if error.reason_code else (),
                ) from error

        return self._translate(operation)

    def post_plan(self, actor: ActorContext, body: Mapping[str, Any]) -> HttpResponse:
        """POST /api/intents/plan; no provider transport or persistence."""

        return self._translate(lambda: self._service.plan_intent(_assert_actor(actor), body))

    def post_generation(
        self,
        actor: ActorContext,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        """POST /api/generations; plan and durably queue without transport."""

        return self._translate(
            lambda: self._service.submit_generation(_assert_actor(actor), body)
        )

    def post_confirm(
        self,
        actor: ActorContext,
        plan_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        """POST /api/intents/{plan_id}/confirm with exact token/path binding."""

        def operation() -> Mapping[str, Any]:
            current_actor = _assert_actor(actor)
            path_plan = _stable_id(plan_id, "plan_id")
            request = _strict_body(
                body,
                frozenset({"schema_version", "plan_token", "confirmed", "accepted_credits"}),
                "confirmation request",
            )
            token = request.get("plan_token")
            if not isinstance(token, str):
                raise ProviderServiceError("schema_invalid", "plan_token is invalid.")
            token_plan_id = self._token_plan_id(token)
            if token_plan_id != path_plan:
                raise ProviderServiceError(
                    "plan_path_mismatch",
                    "Confirmation does not match the requested plan.",
                    status=409,
                )
            return self._service.confirm_intent(current_actor, request)

        return self._translate(operation)

    def get_intent_status(self, actor: ActorContext, intent_id: str) -> HttpResponse:
        """GET /api/intents/{intent_id}/status without provider transport."""

        def operation() -> Mapping[str, Any]:
            current_actor = _assert_actor(actor)
            try:
                path_intent = _stable_id(intent_id, "intent_id")
            except ProviderServiceError:
                raise ProviderServiceError(
                    "intent_not_found",
                    "Intent was not found.",
                    status=404,
                ) from None
            if path_intent != intent_id or not _PUBLIC_INTENT_ID_RE.fullmatch(path_intent):
                raise ProviderServiceError(
                    "intent_not_found",
                    "Intent was not found.",
                    status=404,
                )
            return self._service.get_intent_status(current_actor, path_intent)

        return self._translate(operation)

    @staticmethod
    def _token_plan_id(token: str) -> str:
        """Peek at the public signed payload solely for path binding.

        Authenticity is still verified by ``ProviderFabricService`` before any
        persistence.  Parsing here cannot turn an invalid token into an action.
        """

        if len(token) > 131_072 or token.count(".") != 1:
            raise ProviderServiceError("plan_token_invalid", "Plan token is invalid.")
        encoded, _signature = token.split(".", 1)
        try:
            payload = json.loads(_b64decode(encoded).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise ProviderServiceError("plan_token_invalid", "Plan token is invalid.") from None
        if not isinstance(payload, Mapping):
            raise ProviderServiceError("plan_token_invalid", "Plan token is invalid.")
        try:
            return _stable_id(payload.get("plan_id"), "plan_id")
        except ProviderServiceError:
            raise ProviderServiceError("plan_token_invalid", "Plan token is invalid.") from None


__all__ = [
    "DEFAULT_ASSET_PAGE_SIZE",
    "DEFAULT_CHARACTER_PAGE_SIZE",
    "HTTP_SCHEMA_VERSION",
    "HttpResponse",
    "MAX_ASSET_PAGE_SIZE",
    "MAX_CHARACTER_PAGE_SIZE",
    "ProviderFabricHttpApi",
]
