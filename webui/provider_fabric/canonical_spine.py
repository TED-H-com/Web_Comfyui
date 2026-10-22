"""Pure owner-scoped read model for Castle characters and media.

This module deliberately has no filesystem, database, HTTP, provider, or
credential dependency.  Its caller must first authenticate a Castle actor and
produce bounded, already-sanitized snapshots from the legacy Castle stores and
the provider catalog.  The spine then applies the identity, attribution, and
profile-selection rules that must be shared by future character and media
surfaces.

The v1 contract is intentionally read-only.  It does not backfill a database,
move media, fetch a preview, adopt a remote object, or make a provider identity
claim.  In particular:

* provider characters merge into Castle characters only through an explicit
  link supplied by the server;
* unsupported legacy visual classes remain ``unknown``;
* local media receives a character link only from an exact, already-verified
  Castle output-provenance receipt; and
* browser URLs are emitted only for an explicit allowlist of same-origin media
  endpoints.

Raw provider external IDs, local paths, e-mail addresses, owner IDs, arbitrary
metadata, source URLs, and signed URLs are not part of the output schema.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit


SPINE_SCHEMA = "castle.canonical-spine.v1"
SPINE_SCHEMA_VERSION = 1

MAX_CHARACTERS = 10_000
MAX_ASSETS = 50_000
MAX_LINKS = 20_000
MAX_PROFILE_SELECTIONS = 10_000
MAX_RELATIONSHIP_EDGES = 100_000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KINDS = frozenset({"image", "video", "audio", "text", "file"})
_VISIBILITIES = frozenset({"private", "shared", "public_locked"})
_ASSET_STATES = frozenset(
    {"remote_only", "preview_cached", "local_ready", "missing", "hidden"}
)
_KNOWN_VISUAL_CLASSES = frozenset({"anime", "realistic", "illustration"})
_ALL_VISUAL_CLASSES = _KNOWN_VISUAL_CLASSES | {"unknown"}
_SECRET_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "key",
        "password",
        "secret",
        "signature",
        "token",
    }
)
_VIEW_QUERY_KEYS = frozenset(
    {"filename", "subfolder", "type", "promptId", "prompt_id", "dl"}
)
_CHARACTER_ASSET_QUERY_KEYS = frozenset(
    {"kind", "file", "theme", "pose", "thumb", "v"}
)
_ASSET_PREVIEW_PATH_RE = re.compile(
    r"^/api/assets/[a-z0-9][a-z0-9._:-]{0,127}/preview$"
)
_CHARACTER_ASSET_PATH_RE = re.compile(
    r"^/api/characters/[a-z0-9][a-z0-9._:-]{0,127}/asset$"
)

_LOCAL_CHARACTER_FIELDS = frozenset(
    {
        "owner_user_id",
        "character_id",
        "display_name",
        "visual_class",
        "visibility",
        "approved_profile_url",
    }
)
_PROVIDER_CHARACTER_FIELDS = frozenset(
    {
        "owner_user_id",
        "provider_id",
        "provider_character_id",
        "display_name",
        "visual_class",
        "profile_asset_id",
        "primary",
    }
)
_EXPLICIT_LINK_FIELDS = frozenset(
    {"owner_user_id", "provider_id", "provider_character_id", "character_id"}
)
_LOCAL_ASSET_FIELDS = frozenset(
    {
        "owner_user_id",
        "asset_id",
        "kind",
        "title",
        "visual_class",
        "state",
        "url",
        "preview_url",
        "sha256",
        "suggested_character_id",
        "verified_provenance",
    }
)
_PROVIDER_ASSET_FIELDS = frozenset(
    {
        "owner_user_id",
        "asset_id",
        "provider_id",
        "kind",
        "title",
        "visual_class",
        "state",
        "preview_url",
        "provider_character_ids",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {
        "schema",
        "authority",
        "verified",
        "owner_user_id",
        "character_id",
        "visual_class",
        "content_sha256",
    }
)
_PROFILE_SELECTION_FIELDS = frozenset(
    {"owner_user_id", "character_id", "asset_id"}
)


class CanonicalSpineError(ValueError):
    """A fail-closed contract error with a stable, non-sensitive code."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise CanonicalSpineError(code, message)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail("schema_invalid", f"{label} must be a stable identifier")
    normalized = value.strip().lower()
    if not _ID_RE.fullmatch(normalized):
        _fail("schema_invalid", f"{label} must be a stable identifier")
    return normalized


def _optional_identifier(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, label)


def _external_identifier(value: Any, label: str) -> str:
    """Validate a provider-owned identifier without changing its case.

    External IDs can be case-sensitive and are never returned publicly.  Using
    Castle's lowercase identifier normalizer here would incorrectly collapse
    two distinct provider identities such as ``Hero-A`` and ``hero-a``.
    """

    if not isinstance(value, str):
        _fail("schema_invalid", f"{label} is invalid")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > 500
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        _fail("schema_invalid", f"{label} is invalid")
    return cleaned


def _safe_text(value: Any, label: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str):
        _fail("schema_invalid", f"{label} must be text")
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        _fail("schema_invalid", f"{label} is invalid")
    return cleaned


def normalize_visual_class(value: Any) -> str:
    """Return an executable class or the conservative migration value.

    There are deliberately no fuzzy aliases.  Legacy values such as ``flux``,
    ``experimental``, ``real``, or model names do not prove an identity domain.
    """

    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in _ALL_VISUAL_CLASSES else "unknown"


def _record(
    value: Any,
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail("schema_invalid", f"{label} must be an object")
    if set(value) - allowed:
        _fail("schema_invalid", f"{label} contains unsupported fields")
    if required - set(value):
        _fail("schema_invalid", f"{label} is missing required fields")
    return value


def _records(value: Any, label: str, maximum: int) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail("schema_invalid", f"{label} must be an array")
    if len(value) > maximum:
        _fail("limit_exceeded", f"{label} exceeds its item limit")
    return tuple(value)


def _owned(record: Mapping[str, Any], owner_user_id: str, label: str) -> None:
    owner = _identifier(record.get("owner_user_id"), f"{label} owner_user_id")
    if owner != owner_user_id:
        _fail("owner_mismatch", f"{label} does not belong to the requested owner")


def _visibility(value: Any) -> str:
    if value not in _VISIBILITIES:
        _fail("schema_invalid", "character visibility is invalid")
    return str(value)


def _kind(value: Any) -> str:
    if value not in _KINDS:
        _fail("schema_invalid", "asset kind is invalid")
    return str(value)


def _state(value: Any, *, default: str) -> str:
    selected = default if value is None else value
    if selected not in _ASSET_STATES:
        _fail("schema_invalid", "asset state is invalid")
    return str(selected)


def _same_origin_api_url(value: Any) -> str | None:
    """Return an allowlisted Castle media URL, never a generic API locator."""

    if not isinstance(value, str) or not value or len(value) > 2_048:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    if not parsed.path.startswith("/api/") or parsed.path.startswith("//"):
        return None
    decoded_path = unquote(parsed.path)
    if (
        "\\" in decoded_path
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded_path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        return None
    if decoded_path == "/api/view":
        allowed_query_keys = _VIEW_QUERY_KEYS
    elif _CHARACTER_ASSET_PATH_RE.fullmatch(decoded_path):
        allowed_query_keys = _CHARACTER_ASSET_QUERY_KEYS
    elif _ASSET_PREVIEW_PATH_RE.fullmatch(decoded_path):
        allowed_query_keys = frozenset()
    else:
        return None
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=32)
    except ValueError:
        return None
    if any(key not in allowed_query_keys for key, _child in query):
        return None
    if decoded_path == "/api/view" and not any(
        key == "filename" and child for key, child in query
    ):
        return None
    for key, child in query:
        normalized = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
        if (
            normalized in _SECRET_QUERY_KEYS
            or normalized.endswith(("_secret", "_signature", "_token"))
            or normalized.startswith(("x_amz_", "x_goog_"))
            or "://" in child
            or child.startswith("//")
            or child.lower().startswith("file:")
        ):
            return None
    return value


def _add_relationship_edges(current: int, additional: int) -> int:
    """Apply one aggregate bound across every relationship-shaped input."""

    if additional < 0 or current > MAX_RELATIONSHIP_EDGES - additional:
        _fail("limit_exceeded", "relationships exceed their aggregate edge limit")
    return current + additional


def provider_character_canonical_id(
    owner_user_id: str,
    provider_id: str,
    provider_character_id: str,
) -> str:
    """Derive an opaque stable ID for an unlinked provider character."""

    owner = _identifier(owner_user_id, "owner_user_id")
    provider = _identifier(provider_id, "provider_id")
    provider_character = _external_identifier(
        provider_character_id, "provider_character_id"
    )
    digest = hashlib.sha256(
        "\0".join((SPINE_SCHEMA, owner, provider, provider_character)).encode("utf-8")
    ).hexdigest()[:32]
    return f"char.provider:{digest}"


def _known_class_conflict(left: str, right: str) -> bool:
    return left in _KNOWN_VISUAL_CLASSES and right in _KNOWN_VISUAL_CLASSES and left != right


def _public_profile(
    source: str,
    *,
    asset_id: str | None = None,
    url: str | None = None,
    placeholder_key: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"source": source}
    if asset_id is not None:
        result["asset_id"] = asset_id
    if url is not None:
        result["url"] = url
    if placeholder_key is not None:
        result["placeholder_key"] = placeholder_key
    return result


def build_canonical_spine(
    owner_user_id: str,
    *,
    local_characters: Sequence[Mapping[str, Any]] = (),
    provider_characters: Sequence[Mapping[str, Any]] = (),
    explicit_links: Sequence[Mapping[str, Any]] = (),
    local_assets: Sequence[Mapping[str, Any]] = (),
    provider_assets: Sequence[Mapping[str, Any]] = (),
    explicit_profiles: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build one deterministic owner-scoped character/media projection.

    Every input record repeats ``owner_user_id``.  The repetition is
    intentional: it prevents a caller from accidentally concatenating snapshots
    belonging to different actors before invoking the pure projection.
    """

    owner = _identifier(owner_user_id, "owner_user_id")
    local_character_rows = _records(local_characters, "local_characters", MAX_CHARACTERS)
    provider_character_rows = _records(
        provider_characters, "provider_characters", MAX_CHARACTERS
    )
    explicit_link_rows = _records(explicit_links, "explicit_links", MAX_LINKS)
    local_asset_rows = _records(local_assets, "local_assets", MAX_ASSETS)
    provider_asset_rows = _records(provider_assets, "provider_assets", MAX_ASSETS)
    explicit_profile_rows = _records(
        explicit_profiles, "explicit_profiles", MAX_PROFILE_SELECTIONS
    )
    if len(local_character_rows) + len(provider_character_rows) > MAX_CHARACTERS:
        _fail("limit_exceeded", "combined characters exceed their item limit")
    if len(local_asset_rows) + len(provider_asset_rows) > MAX_ASSETS:
        _fail("limit_exceeded", "combined assets exceed their item limit")
    relationship_edges = _add_relationship_edges(
        len(explicit_link_rows), len(explicit_profile_rows)
    )

    characters: dict[str, dict[str, Any]] = {}
    local_character_ids: set[str] = set()
    for raw in local_character_rows:
        row = _record(
            raw,
            allowed=_LOCAL_CHARACTER_FIELDS,
            required=frozenset(
                {
                    "owner_user_id",
                    "character_id",
                    "display_name",
                    "visual_class",
                    "visibility",
                }
            ),
            label="local character",
        )
        _owned(row, owner, "local character")
        character_id = _identifier(row["character_id"], "character_id")
        if character_id in characters:
            _fail("character_conflict", "local character identifiers must be unique")
        local_character_ids.add(character_id)
        characters[character_id] = {
            "character_id": character_id,
            "display_name": _safe_text(row["display_name"], "display_name", maximum=160),
            "visual_class": normalize_visual_class(row["visual_class"]),
            "visibility": _visibility(row["visibility"]),
            "origins": {"castle"},
            "legacy_profile_url": _same_origin_api_url(row.get("approved_profile_url")),
            "provider_records": [],
        }

    provider_records: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in provider_character_rows:
        row = _record(
            raw,
            allowed=_PROVIDER_CHARACTER_FIELDS,
            required=frozenset(
                {
                    "owner_user_id",
                    "provider_id",
                    "provider_character_id",
                    "display_name",
                    "visual_class",
                }
            ),
            label="provider character",
        )
        _owned(row, owner, "provider character")
        provider_id = _identifier(row["provider_id"], "provider_id")
        provider_character_id = _external_identifier(
            row["provider_character_id"], "provider_character_id"
        )
        key = (provider_id, provider_character_id)
        if key in provider_records:
            _fail("character_conflict", "provider character identifiers must be unique")
        primary = row.get("primary", False)
        if not isinstance(primary, bool):
            _fail("schema_invalid", "provider character primary must be boolean")
        profile_asset_id = _optional_identifier(
            row.get("profile_asset_id"), "profile_asset_id"
        )
        if profile_asset_id is not None:
            relationship_edges = _add_relationship_edges(relationship_edges, 1)
        provider_records[key] = {
            "provider_id": provider_id,
            "provider_character_id": provider_character_id,
            "display_name": _safe_text(row["display_name"], "display_name", maximum=160),
            "visual_class": normalize_visual_class(row["visual_class"]),
            "profile_asset_id": profile_asset_id,
            "primary": primary,
        }

    links: dict[tuple[str, str], str] = {}
    for raw in explicit_link_rows:
        row = _record(
            raw,
            allowed=_EXPLICIT_LINK_FIELDS,
            required=_EXPLICIT_LINK_FIELDS,
            label="explicit character link",
        )
        _owned(row, owner, "explicit character link")
        provider_key = (
            _identifier(row["provider_id"], "provider_id"),
            _external_identifier(
                row["provider_character_id"], "provider_character_id"
            ),
        )
        target_id = _identifier(row["character_id"], "character_id")
        if provider_key not in provider_records or target_id not in local_character_ids:
            _fail("link_target_missing", "explicit character link target was not found")
        if provider_key in links:
            _fail("link_conflict", "provider character has more than one explicit link")
        provider_class = provider_records[provider_key]["visual_class"]
        target_class = characters[target_id]["visual_class"]
        if _known_class_conflict(provider_class, target_class):
            _fail("link_visual_class_mismatch", "linked character visual classes conflict")
        links[provider_key] = target_id

    provider_to_canonical: dict[tuple[str, str], str] = {}
    for provider_key in sorted(provider_records):
        record = provider_records[provider_key]
        canonical_id = links.get(provider_key)
        if canonical_id is None:
            canonical_id = provider_character_canonical_id(owner, *provider_key)
            if canonical_id in characters:
                _fail("character_conflict", "derived provider character identifier collides")
            characters[canonical_id] = {
                "character_id": canonical_id,
                "display_name": record["display_name"],
                "visual_class": record["visual_class"],
                "visibility": "private",
                "origins": {record["provider_id"]},
                "legacy_profile_url": None,
                "provider_records": [],
            }
        character = characters[canonical_id]
        character["origins"].add(record["provider_id"])
        character["provider_records"].append(record)
        provider_to_canonical[provider_key] = canonical_id

    assets: dict[str, dict[str, Any]] = {}
    for raw in local_asset_rows:
        row = _record(
            raw,
            allowed=_LOCAL_ASSET_FIELDS,
            required=frozenset(
                {
                    "owner_user_id",
                    "asset_id",
                    "kind",
                    "title",
                    "visual_class",
                }
            ),
            label="local asset",
        )
        _owned(row, owner, "local asset")
        asset_id = _identifier(row["asset_id"], "asset_id")
        if asset_id in assets:
            _fail("asset_conflict", "asset identifiers must be unique")
        visual_class = normalize_visual_class(row["visual_class"])
        digest = row.get("sha256")
        if digest is not None and (
            not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
        ):
            _fail("schema_invalid", "local asset sha256 is invalid")
        character_ids: list[str] = []
        attribution = "none"
        provenance = row.get("verified_provenance")
        if provenance is not None:
            relationship_edges = _add_relationship_edges(relationship_edges, 1)
            receipt = _record(
                provenance,
                allowed=_PROVENANCE_FIELDS,
                required=_PROVENANCE_FIELDS,
                label="verified provenance",
            )
            if not isinstance(receipt.get("verified"), bool):
                _fail("schema_invalid", "verified provenance flag must be boolean")
            _owned(receipt, owner, "verified provenance")
            if receipt.get("verified") is True:
                if (
                    receipt.get("schema") != "castle.output-provenance.v1"
                    or receipt.get("authority") != "castle-server"
                ):
                    _fail("provenance_invalid", "verified provenance authority is invalid")
                receipt_character_id = _identifier(
                    receipt.get("character_id"), "provenance character_id"
                )
                receipt_class = normalize_visual_class(receipt.get("visual_class"))
                receipt_digest = receipt.get("content_sha256")
                if (
                    not isinstance(receipt_digest, str)
                    or not _SHA256_RE.fullmatch(receipt_digest)
                    or digest is None
                    or receipt_digest != digest
                ):
                    _fail("provenance_hash_mismatch", "verified provenance hash does not match")
                character = characters.get(receipt_character_id)
                if character is None:
                    _fail("provenance_character_missing", "verified character was not found")
                if (
                    receipt_class != visual_class
                    or character["visual_class"] != visual_class
                ):
                    _fail(
                        "provenance_visual_class_mismatch",
                        "verified provenance visual classes do not match",
                    )
                character_ids = [receipt_character_id]
                attribution = "verified_provenance"
        assets[asset_id] = {
            "asset_id": asset_id,
            "kind": _kind(row["kind"]),
            "title": _safe_text(row["title"], "asset title", maximum=500),
            "visual_class": visual_class,
            "origin": "local",
            "state": _state(row.get("state"), default="local_ready"),
            "character_ids": character_ids,
            "attribution": attribution,
            "url": _same_origin_api_url(row.get("url")),
            "preview_url": _same_origin_api_url(row.get("preview_url")),
            "provider_id": None,
        }

    for raw in provider_asset_rows:
        row = _record(
            raw,
            allowed=_PROVIDER_ASSET_FIELDS,
            required=frozenset(
                {
                    "owner_user_id",
                    "asset_id",
                    "provider_id",
                    "kind",
                    "title",
                    "visual_class",
                    "provider_character_ids",
                }
            ),
            label="provider asset",
        )
        _owned(row, owner, "provider asset")
        asset_id = _identifier(row["asset_id"], "asset_id")
        if asset_id in assets:
            _fail("asset_conflict", "asset identifiers must be unique")
        provider_id = _identifier(row["provider_id"], "provider_id")
        visual_class = normalize_visual_class(row["visual_class"])
        raw_character_ids = _records(
            row["provider_character_ids"], "provider_character_ids", MAX_CHARACTERS
        )
        relationship_edges = _add_relationship_edges(
            relationship_edges, len(raw_character_ids)
        )
        normalized_provider_ids = [
            _external_identifier(value, "provider_character_id")
            for value in raw_character_ids
        ]
        if len(normalized_provider_ids) != len(set(normalized_provider_ids)):
            _fail("asset_conflict", "provider character links must be unique")
        character_ids: list[str] = []
        for provider_character_id in normalized_provider_ids:
            provider_key = (provider_id, provider_character_id)
            canonical_id = provider_to_canonical.get(provider_key)
            if canonical_id is None:
                _fail("asset_character_missing", "provider asset character was not found")
            if (
                provider_records[provider_key]["visual_class"] != visual_class
                or characters[canonical_id]["visual_class"] != visual_class
            ):
                _fail(
                    "asset_visual_class_mismatch",
                    "provider asset and character visual classes do not match",
                )
            character_ids.append(canonical_id)
        assets[asset_id] = {
            "asset_id": asset_id,
            "kind": _kind(row["kind"]),
            "title": _safe_text(row["title"], "asset title", maximum=500),
            "visual_class": visual_class,
            "origin": provider_id,
            "state": _state(row.get("state"), default="remote_only"),
            "character_ids": sorted(character_ids),
            "attribution": "provider_link" if character_ids else "none",
            "url": None,
            "preview_url": _same_origin_api_url(row.get("preview_url")),
            "provider_id": provider_id,
        }

    selected_profiles: dict[str, str] = {}
    for raw in explicit_profile_rows:
        row = _record(
            raw,
            allowed=_PROFILE_SELECTION_FIELDS,
            required=_PROFILE_SELECTION_FIELDS,
            label="explicit profile selection",
        )
        _owned(row, owner, "explicit profile selection")
        character_id = _identifier(row["character_id"], "character_id")
        asset_id = _identifier(row["asset_id"], "asset_id")
        if character_id in selected_profiles:
            _fail("profile_conflict", "character has multiple explicit profiles")
        character = characters.get(character_id)
        asset = assets.get(asset_id)
        if character is None or asset is None:
            _fail("profile_target_missing", "profile character or asset was not found")
        if asset["kind"] != "image":
            _fail("profile_kind_invalid", "profile asset must be an image")
        if character_id not in asset["character_ids"]:
            _fail("profile_attribution_mismatch", "profile asset is not linked to its character")
        if character["visual_class"] != asset["visual_class"]:
            _fail("profile_visual_class_mismatch", "profile visual classes do not match")
        selected_profiles[character_id] = asset_id

    public_characters: list[dict[str, Any]] = []
    for character_id in sorted(characters):
        character = characters[character_id]
        explicit_asset_id = selected_profiles.get(character_id)
        if explicit_asset_id is not None:
            asset = assets[explicit_asset_id]
            profile = _public_profile(
                "explicit",
                asset_id=explicit_asset_id,
                url=asset["preview_url"] or asset["url"],
            )
        else:
            candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
            for provider_record in character["provider_records"]:
                profile_asset_id = provider_record["profile_asset_id"]
                if profile_asset_id is None:
                    continue
                asset = assets.get(profile_asset_id)
                if (
                    asset is None
                    or asset["kind"] != "image"
                    or asset["provider_id"] != provider_record["provider_id"]
                    or character_id not in asset["character_ids"]
                    or asset["visual_class"] != character["visual_class"]
                    or asset["preview_url"] is None
                ):
                    continue
                sort_key = (
                    0 if provider_record["primary"] else 1,
                    provider_record["provider_id"],
                    provider_record["provider_character_id"],
                    profile_asset_id,
                )
                candidates.append((sort_key, asset))
            if candidates:
                _sort_key, asset = min(candidates, key=lambda item: item[0])
                profile = _public_profile(
                    "provider_declared",
                    asset_id=asset["asset_id"],
                    url=asset["preview_url"],
                )
            elif character["legacy_profile_url"] is not None:
                profile = _public_profile(
                    "legacy_approved", url=character["legacy_profile_url"]
                )
            else:
                placeholder_key = hashlib.sha256(
                    f"{SPINE_SCHEMA}\0{character_id}".encode("utf-8")
                ).hexdigest()[:16]
                profile = _public_profile(
                    "placeholder", placeholder_key=f"profile:{placeholder_key}"
                )
        public_characters.append(
            {
                "character_id": character_id,
                "display_name": character["display_name"],
                "visual_class": character["visual_class"],
                "visibility": character["visibility"],
                "origins": sorted(character["origins"]),
                "profile": profile,
            }
        )

    public_assets: list[dict[str, Any]] = []
    for asset_id in sorted(assets):
        asset = assets[asset_id]
        public = {
            "asset_id": asset_id,
            "kind": asset["kind"],
            "title": asset["title"],
            "visual_class": asset["visual_class"],
            "origin": asset["origin"],
            "state": asset["state"],
            "character_ids": list(asset["character_ids"]),
            "attribution": asset["attribution"],
        }
        if asset["url"] is not None:
            public["url"] = asset["url"]
        if asset["preview_url"] is not None:
            public["preview_url"] = asset["preview_url"]
        public_assets.append(public)

    return {
        "schema": SPINE_SCHEMA,
        "schema_version": SPINE_SCHEMA_VERSION,
        "scope": "owner",
        "characters": public_characters,
        "assets": public_assets,
        "counts": {
            "characters": len(public_characters),
            "assets": len(public_assets),
        },
        "effects": {
            "filesystem_access_performed": False,
            "network_access_performed": False,
            "provider_transport_performed": False,
            "writes_performed": False,
        },
    }


__all__ = [
    "CanonicalSpineError",
    "SPINE_SCHEMA",
    "SPINE_SCHEMA_VERSION",
    "build_canonical_spine",
    "normalize_visual_class",
    "provider_character_canonical_id",
]
