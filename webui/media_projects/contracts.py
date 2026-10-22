"""Strict, provider-neutral contracts for resumable Castle media projects.

The production pack is data, not executable code.  It deliberately contains
only opaque references to credentials, consent records, and media.  Provider
payloads are compiled later, after this contract and its pre-spend gate pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

from provider_fabric.models import CapabilityKind, VisualClass


PACK_VERSION = "castle.production-pack.v1"
MAX_PACK_BYTES = 2 * 1024 * 1024
MAX_ENTITIES = 500
MAX_EVIDENCE_ITEMS = 1000
MAX_ROUTE_VARIANTS = 1000
MAX_SCENES = 1000
MAX_SHOTS = 5000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_ATOM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9._-]{0,127}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_ASPECT_RE = re.compile(r"^[1-9][0-9]{0,2}:[1-9][0-9]{0,2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|webhook[_-]?secret|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|authorization|password|cookies?|"
    r"credentials?)(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_VALUE_RES = (
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?<![A-Za-z0-9])(?:"
        r"gh[pousr]_[A-Za-z0-9]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|"
        r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
        r"xox[A-Za-z0-9]*[-.][A-Za-z0-9.-]{16,}|"
        r"(?:aip|autoip)_[A-Za-z0-9][A-Za-z0-9._-]{15,}"
        r")(?![A-Za-z0-9])"
    ),
)
_OPAQUE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9._~+=-]{38,}[A-Za-z0-9=])(?![A-Za-z0-9])"
)
_URL_PATH_SECRET_RE = re.compile(
    r"(?:^|/)(?:access[-_]?token|api[-_]?key|credential|signature|signed[-_]?token|sig)"
    r"(?:/|=|:)([A-Za-z0-9._~+%=-]{16,})(?:$|[/?#&])",
    re.IGNORECASE,
)
_SECRET_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "authorization", "credential",
    "key", "password", "secret", "signature", "sig", "token",
}
_SECRET_QUERY_PART_RE = re.compile(r"(?:^|[-_])(?:credential|password|secret|signature|token)(?:$|[-_])", re.IGNORECASE)


def _looks_like_opaque_token(candidate: str) -> bool:
    """Conservatively recognize unlabelled high-entropy credential material."""

    if len(candidate) < 40 or re.fullmatch(r"[0-9a-fA-F]+", candidate):
        return False
    if not all(
        re.search(pattern, candidate)
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[._~+=-]")
    ):
        return False
    counts = Counter(candidate)
    entropy = -sum(
        (count / len(candidate)) * math.log2(count / len(candidate))
        for count in counts.values()
    )
    return len(counts) >= 20 and entropy >= 4.5


def _contains_secret_value(value: str) -> bool:
    # Percent-decoding is important for credentials hidden in URL paths or
    # fragments.  The exception paths below remain structural and never echo
    # either scan view.
    views = (value, unquote(value)) if "%" in value else (value,)
    for view in views:
        if any(pattern.search(view) for pattern in _SECRET_VALUE_RES):
            return True
        if any(_looks_like_opaque_token(match.group(1)) for match in _OPAQUE_TOKEN_RE.finditer(view)):
            return True

    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"https", "castle"}:
        return False
    path = unquote(parsed.path)
    fragment = unquote(parsed.fragment)
    if _URL_PATH_SECRET_RE.search(path) or _URL_PATH_SECRET_RE.search("/" + fragment.lstrip("/")):
        return True
    fragment_query = fragment.lstrip("?#")
    fragment_keys = (
        {key.lower() for key, _ in parse_qsl(fragment_query, keep_blank_values=True)}
        if "=" in fragment_query
        else set()
    )
    return bool(
        fragment_keys & _SECRET_QUERY_KEYS
        or any(_SECRET_QUERY_PART_RE.search(key) for key in fragment_keys)
    )


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip().lower()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return normalized


def _atom(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip().lower()
    if not _ATOM_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase identifier without colons")
    return normalized


def _resource(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip().lower()
    if not _RESOURCE_RE.fullmatch(normalized):
        raise ValueError(f"{label} must look like resource-type:resource-id")
    return normalized


def _text(value: Any, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{label} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in normalized):
        raise ValueError(f"{label} contains control characters")
    if _contains_secret_value(normalized):
        raise ValueError(f"{label} appears to contain credential material")
    return normalized


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return 0.0 if result == 0 else result


def _sequence(value: Any, label: str, maximum: int = 1000) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{label} has more than {maximum} items")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    label: str,
    *,
    required: set[str] | None = None,
) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{label} has unknown fields: {', '.join(extra)}")
    missing = sorted((required or set()) - set(value))
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")


def _unique_ids(values: Sequence[Any], label: str, maximum: int) -> tuple[str, ...]:
    if len(values) > maximum:
        raise ValueError(f"{label} has more than {maximum} items")
    normalized = tuple(_id(value, label) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} values must be unique")
    return normalized


def _enum(value: Any, cls: type[Enum], label: str):
    if isinstance(value, cls):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    try:
        return cls(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error


def _visual_class(value: Any) -> VisualClass:
    return _enum(value, VisualClass, "visual_class")


def _capability(value: Any) -> CapabilityKind:
    return _enum(value, CapabilityKind, "capability")


def _reject_raw_secrets(value: Any, *, path: str = "$", depth: int = 0, budget: list[int] | None = None) -> None:
    """Reject common credential containers/patterns without echoing values."""

    if depth > 16:
        raise ValueError("production pack nesting is too deep")
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("production pack contains too many values")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if _SECRET_KEY_RE.search(key):
                raise ValueError(f"credential-like field is forbidden at {path}")
            _reject_raw_secrets(item, path=f"{path}.{key}", depth=depth + 1, budget=budget)
    elif isinstance(value, list):
        for item in value:
            _reject_raw_secrets(item, path=f"{path}[]", depth=depth + 1, budget=budget)
    elif isinstance(value, str) and _contains_secret_value(value):
        raise ValueError(f"credential-like value is forbidden at {path}")


def _canonical_json_bytes(value: Any, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain JSON values only") from error


class EntityKind(str, Enum):
    FICTIONAL = "fictional"
    CONSENTING_PERSON = "consenting_person"
    NON_HUMAN = "non_human"
    LOCATION = "location"
    PROP = "prop"
    UNKNOWN = "unknown"


class ConsentState(str, Enum):
    NOT_REQUIRED = "not_required"
    SELF_OWNED = "self_owned"
    DOCUMENTED = "documented"
    UNKNOWN = "unknown"
    REVOKED = "revoked"

    @property
    def executable(self) -> bool:
        return self in {self.NOT_REQUIRED, self.SELF_OWNED, self.DOCUMENTED}


class EvidenceKind(str, Enum):
    SOURCE = "source"
    CONSENT = "consent"
    RIGHTS = "rights"
    CALIBRATION = "calibration"
    GOLDEN = "golden"
    TECHNICAL_COMPATIBILITY = "technical_compatibility"


class ExecutionDomain(str, Enum):
    LOCAL = "local"
    EXTERNAL = "external"


class UsageScope(str, Enum):
    PRIVATE = "private"
    INTERNAL = "internal"
    PUBLIC_NONCOMMERCIAL = "public_noncommercial"
    COMMERCIAL = "commercial"


class DialogueMode(str, Enum):
    NONE = "none"
    NATIVE = "native"
    SEPARATE_VOICE = "separate_voice"
    EXTERNAL_AUDIO = "external_audio"


class StatePhase(str, Enum):
    BEFORE = "before"
    DURING = "during"
    AFTER = "after"


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    project_id: str
    title: str
    premise: str
    audience: str
    platform_profile: str
    language: str
    aspect_ratio: str
    target_duration_seconds: float
    subtitle_profile: str
    audio_mix_profile: str
    branding_profile: str
    deliverable_profile: str
    branding_asset_ids: tuple[str, ...]
    ai_disclosure_required: bool
    usage_scope: UsageScope = UsageScope.PRIVATE
    quality_tier: str = "quality"
    max_credits: int | None = 0
    allow_external_providers: bool = False
    provider_allowlist: tuple[str, ...] = ("local_comfy",)
    allow_precharge_fallback: bool = True
    max_precharge_route_attempts: int = 2
    soundtrack_asset_version_id: str | None = None
    soundtrack_asset_sha256: str | None = None
    soundtrack_gain_db: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _id(self.project_id, "project_id"))
        object.__setattr__(self, "title", _text(self.title, "title", 200))
        object.__setattr__(self, "premise", _text(self.premise, "premise", 2000))
        object.__setattr__(self, "audience", _text(self.audience, "audience", 500))
        object.__setattr__(self, "platform_profile", _id(self.platform_profile, "platform_profile"))
        if not isinstance(self.language, str) or not _LANGUAGE_RE.fullmatch(self.language.strip()):
            raise ValueError("language must be a BCP-47-like tag")
        object.__setattr__(self, "language", self.language.strip())
        if not isinstance(self.aspect_ratio, str) or not _ASPECT_RE.fullmatch(self.aspect_ratio.strip()):
            raise ValueError("aspect_ratio must look like 16:9")
        object.__setattr__(self, "aspect_ratio", self.aspect_ratio.strip())
        object.__setattr__(
            self,
            "target_duration_seconds",
            _number(self.target_duration_seconds, "target_duration_seconds", 1, 3600),
        )
        for name in ("subtitle_profile", "audio_mix_profile", "branding_profile", "deliverable_profile"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        object.__setattr__(
            self,
            "branding_asset_ids",
            _unique_ids(tuple(self.branding_asset_ids), "branding_asset_id", 32),
        )
        object.__setattr__(
            self,
            "ai_disclosure_required",
            _bool(self.ai_disclosure_required, "ai_disclosure_required"),
        )
        object.__setattr__(self, "usage_scope", _enum(self.usage_scope, UsageScope, "usage_scope"))
        object.__setattr__(self, "quality_tier", _id(self.quality_tier, "quality_tier"))
        if self.max_credits is not None:
            object.__setattr__(
                self,
                "max_credits",
                _integer(self.max_credits, "max_credits", 0, 10_000_000),
            )
        object.__setattr__(
            self,
            "allow_external_providers",
            _bool(self.allow_external_providers, "allow_external_providers"),
        )
        providers = tuple(_atom(item, "provider_allowlist") for item in tuple(self.provider_allowlist))
        if not providers or len(providers) > 32 or len(providers) != len(set(providers)):
            raise ValueError("provider_allowlist must contain 1-32 unique providers")
        object.__setattr__(self, "provider_allowlist", providers)
        object.__setattr__(
            self,
            "allow_precharge_fallback",
            _bool(self.allow_precharge_fallback, "allow_precharge_fallback"),
        )
        attempts = _integer(
            self.max_precharge_route_attempts,
            "max_precharge_route_attempts",
            1,
            5,
        )
        if not self.allow_precharge_fallback and attempts != 1:
            raise ValueError("max_precharge_route_attempts must be 1 when fallback is disabled")
        object.__setattr__(self, "max_precharge_route_attempts", attempts)
        soundtrack_fields = (
            self.soundtrack_asset_version_id,
            self.soundtrack_asset_sha256,
            self.soundtrack_gain_db,
        )
        if self.soundtrack_asset_version_id is None:
            if any(value is not None for value in soundtrack_fields[1:]):
                raise ValueError("soundtrack evidence requires an asset version")
        else:
            object.__setattr__(
                self, "soundtrack_asset_version_id",
                _id(self.soundtrack_asset_version_id, "soundtrack_asset_version_id"),
            )
            if (
                not isinstance(self.soundtrack_asset_sha256, str)
                or _SHA256_RE.fullmatch(self.soundtrack_asset_sha256) is None
            ):
                raise ValueError("soundtrack requires an exact sha256")
            object.__setattr__(
                self, "soundtrack_gain_db",
                _number(self.soundtrack_gain_db, "soundtrack_gain_db", -36, 0),
            )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "project_id": self.project_id,
            "title": self.title,
            "premise": self.premise,
            "audience": self.audience,
            "platform_profile": self.platform_profile,
            "language": self.language,
            "aspect_ratio": self.aspect_ratio,
            "target_duration_seconds": self.target_duration_seconds,
            "subtitle_profile": self.subtitle_profile,
            "audio_mix_profile": self.audio_mix_profile,
            "branding_profile": self.branding_profile,
            "deliverable_profile": self.deliverable_profile,
            "branding_asset_ids": list(self.branding_asset_ids),
            "ai_disclosure_required": self.ai_disclosure_required,
            "usage_scope": self.usage_scope.value,
            "quality_tier": self.quality_tier,
            "max_credits": self.max_credits,
            "allow_external_providers": self.allow_external_providers,
            "provider_allowlist": list(self.provider_allowlist),
            "allow_precharge_fallback": self.allow_precharge_fallback,
            "max_precharge_route_attempts": self.max_precharge_route_attempts,
        }
        if self.soundtrack_asset_version_id is not None:
            value.update({
                "soundtrack_asset_version_id": self.soundtrack_asset_version_id,
                "soundtrack_asset_sha256": self.soundtrack_asset_sha256,
                "soundtrack_gain_db": self.soundtrack_gain_db,
            })
        return value


@dataclass(frozen=True, slots=True)
class EntitySpec:
    entity_id: str
    kind: EntityKind
    display_name: str
    visual_class: VisualClass
    consent_state: ConsentState
    consent_ref: str | None = None
    consent_evidence_ids: tuple[str, ...] = ()
    allowed_provider_ids: tuple[str, ...] = ()
    allowed_capabilities: frozenset[CapabilityKind] = field(default_factory=frozenset)
    allowed_usage_scopes: frozenset[UsageScope] = field(default_factory=frozenset)
    voice_use_allowed: bool = False
    character_id: str | None = None
    profile_asset_id: str | None = None
    wardrobe_asset_id: str | None = None
    voice_asset_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _id(self.entity_id, "entity_id"))
        object.__setattr__(self, "kind", _enum(self.kind, EntityKind, "entity kind"))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name", 160))
        object.__setattr__(self, "visual_class", _visual_class(self.visual_class))
        object.__setattr__(self, "consent_state", _enum(self.consent_state, ConsentState, "consent_state"))
        for name in ("consent_ref", "character_id", "profile_asset_id", "wardrobe_asset_id", "voice_asset_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _id(value, name))
        object.__setattr__(
            self,
            "consent_evidence_ids",
            _unique_ids(tuple(self.consent_evidence_ids), "consent_evidence_id", 20),
        )
        providers = tuple(_atom(item, "allowed_provider_id") for item in tuple(self.allowed_provider_ids))
        if len(providers) > 32 or len(providers) != len(set(providers)):
            raise ValueError("allowed_provider_ids must contain at most 32 unique providers")
        object.__setattr__(self, "allowed_provider_ids", providers)
        capabilities = frozenset(_capability(item) for item in self.allowed_capabilities)
        if len(capabilities) > len(CapabilityKind):
            raise ValueError("allowed_capabilities is too large")
        object.__setattr__(self, "allowed_capabilities", capabilities)
        usage_scopes = frozenset(_enum(item, UsageScope, "allowed_usage_scope") for item in self.allowed_usage_scopes)
        if not usage_scopes:
            raise ValueError("allowed_usage_scopes cannot be empty")
        object.__setattr__(self, "allowed_usage_scopes", usage_scopes)
        object.__setattr__(self, "voice_use_allowed", _bool(self.voice_use_allowed, "voice_use_allowed"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind.value,
            "display_name": self.display_name,
            "visual_class": self.visual_class.value,
            "consent_state": self.consent_state.value,
            "consent_ref": self.consent_ref,
            "consent_evidence_ids": list(self.consent_evidence_ids),
            "allowed_provider_ids": list(self.allowed_provider_ids),
            "allowed_capabilities": sorted(item.value for item in self.allowed_capabilities),
            "allowed_usage_scopes": sorted(item.value for item in self.allowed_usage_scopes),
            "voice_use_allowed": self.voice_use_allowed,
            "character_id": self.character_id,
            "profile_asset_id": self.profile_asset_id,
            "wardrobe_asset_id": self.wardrobe_asset_id,
            "voice_asset_id": self.voice_asset_id,
        }


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    kind: EvidenceKind
    title: str
    source_uri: str
    source_hash: str | None
    is_private: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _id(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "kind", _enum(self.kind, EvidenceKind, "evidence kind"))
        object.__setattr__(self, "title", _text(self.title, "evidence title", 300))
        uri = _text(self.source_uri, "source_uri", 4096)
        parsed = urlsplit(uri)
        if parsed.scheme not in {"https", "castle"}:
            raise ValueError("source_uri must use HTTPS or castle")
        if not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("source_uri must have a host and no embedded credentials")
        query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if query_keys & _SECRET_QUERY_KEYS or any(_SECRET_QUERY_PART_RE.search(key) for key in query_keys):
            raise ValueError("source_uri must not contain credential-like query parameters")
        object.__setattr__(self, "source_uri", uri)
        if self.source_hash is not None:
            if not isinstance(self.source_hash, str):
                raise ValueError("source_hash must be a string or null")
            digest = self.source_hash.strip().lower()
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError("source_hash must be SHA256")
            object.__setattr__(self, "source_hash", digest)
        if self.kind in {
            EvidenceKind.CONSENT,
            EvidenceKind.RIGHTS,
            EvidenceKind.CALIBRATION,
            EvidenceKind.GOLDEN,
            EvidenceKind.TECHNICAL_COMPATIBILITY,
        } and not self.source_hash:
            raise ValueError(f"{self.kind.value} evidence requires an immutable source_hash")
        object.__setattr__(self, "is_private", _bool(self.is_private, "is_private"))
        if self.kind in {EvidenceKind.CONSENT, EvidenceKind.RIGHTS} and not self.is_private:
            raise ValueError("consent and rights evidence must remain private")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind.value,
            "title": self.title,
            "source_uri": self.source_uri,
            "source_hash": self.source_hash,
            "is_private": self.is_private,
        }


@dataclass(frozen=True, slots=True)
class FactualClaim:
    claim_id: str
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _id(self.claim_id, "claim_id"))
        object.__setattr__(self, "text", _text(self.text, "claim text", 1000))
        evidence_ids = _unique_ids(tuple(self.evidence_ids), "claim evidence_id", 20)
        if not evidence_ids:
            raise ValueError("factual claims require at least one evidence_id")
        object.__setattr__(self, "evidence_ids", evidence_ids)

    def to_dict(self) -> dict[str, Any]:
        return {"claim_id": self.claim_id, "text": self.text, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True, slots=True)
class DialogueCue:
    speaker_entity_id: str
    text: str
    start_seconds: float
    end_seconds: float
    language: str
    audio_asset_id: str | None = None
    source_start_seconds: float | None = None
    source_end_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "speaker_entity_id", _id(self.speaker_entity_id, "speaker_entity_id"))
        object.__setattr__(self, "text", _text(self.text, "dialogue text", 2000))
        start = _number(self.start_seconds, "dialogue start", 0, 3600)
        end = _number(self.end_seconds, "dialogue end", 0.001, 3600)
        if end <= start:
            raise ValueError("dialogue end must be after start")
        object.__setattr__(self, "start_seconds", start)
        object.__setattr__(self, "end_seconds", end)
        if not isinstance(self.language, str) or not _LANGUAGE_RE.fullmatch(self.language.strip()):
            raise ValueError("dialogue language is invalid")
        object.__setattr__(self, "language", self.language.strip())
        if self.audio_asset_id is not None:
            object.__setattr__(self, "audio_asset_id", _id(self.audio_asset_id, "audio_asset_id"))
            if self.source_start_seconds is None or self.source_end_seconds is None:
                raise ValueError("external dialogue audio requires explicit source start/end")
            source_start = _number(self.source_start_seconds, "source audio start", 0, 3600)
            source_end = _number(self.source_end_seconds, "source audio end", 0.001, 3600)
            if source_end <= source_start:
                raise ValueError("source audio end must be after start")
            if abs((source_end - source_start) - (end - start)) > 0.05:
                raise ValueError("source audio duration must align with the dialogue cue")
            object.__setattr__(self, "source_start_seconds", source_start)
            object.__setattr__(self, "source_end_seconds", source_end)
        elif self.source_start_seconds is not None or self.source_end_seconds is not None:
            raise ValueError("source audio timing requires audio_asset_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker_entity_id": self.speaker_entity_id,
            "text": self.text,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "language": self.language,
            "audio_asset_id": self.audio_asset_id,
            "source_start_seconds": self.source_start_seconds,
            "source_end_seconds": self.source_end_seconds,
        }


@dataclass(frozen=True, slots=True)
class SoundEffectCue:
    cue_id: str
    asset_version_id: str
    asset_sha256: str
    start_seconds: float
    source_start_seconds: float
    duration_seconds: float
    gain_db: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "cue_id", _id(self.cue_id, "sound effect cue_id"))
        object.__setattr__(self, "asset_version_id", _id(self.asset_version_id, "sound effect asset_version_id"))
        if not isinstance(self.asset_sha256, str) or _SHA256_RE.fullmatch(self.asset_sha256) is None:
            raise ValueError("sound effect requires an exact sha256")
        object.__setattr__(self, "start_seconds", _number(self.start_seconds, "sound effect start", 0, 3600))
        object.__setattr__(self, "source_start_seconds", _number(self.source_start_seconds, "sound effect source start", 0, 3600))
        object.__setattr__(self, "duration_seconds", _number(self.duration_seconds, "sound effect duration", 0.05, 60))
        object.__setattr__(self, "gain_db", _number(self.gain_db, "sound effect gain_db", -36, 6))

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "asset_version_id": self.asset_version_id,
            "asset_sha256": self.asset_sha256,
            "start_seconds": self.start_seconds,
            "source_start_seconds": self.source_start_seconds,
            "duration_seconds": self.duration_seconds,
            "gain_db": self.gain_db,
        }


@dataclass(frozen=True, slots=True)
class RouteVariant:
    route_variant_id: str
    provider_id: str
    provider_account_id: str
    variant_id: str
    execution_domain: ExecutionDomain
    capability: CapabilityKind
    visual_classes: frozenset[VisualClass]
    option_keys: frozenset[str]
    resource_lane: str
    resource_locks: tuple[str, ...]
    golden_evidence_id: str | None
    release_ready: bool
    estimated_credits: int | None = None
    execution_basis: str = "quality_golden"
    technical_evidence_id: str | None = None
    execution_evidence_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_variant_id", _id(self.route_variant_id, "route_variant_id"))
        provider_id = _atom(self.provider_id, "provider_id")
        object.__setattr__(self, "provider_id", provider_id)
        account_id = _id(self.provider_account_id, "provider_account_id")
        object.__setattr__(self, "provider_account_id", account_id)
        object.__setattr__(self, "variant_id", _id(self.variant_id, "variant_id"))
        object.__setattr__(self, "execution_domain", _enum(self.execution_domain, ExecutionDomain, "execution_domain"))
        object.__setattr__(self, "capability", _capability(self.capability))
        classes = frozenset(_visual_class(item) for item in self.visual_classes)
        if not classes:
            raise ValueError("route visual_classes cannot be empty")
        if VisualClass.UNKNOWN in classes:
            raise ValueError("unknown visual class cannot be executable")
        object.__setattr__(self, "visual_classes", classes)
        options = frozenset(_id(item, "option_key") for item in self.option_keys)
        if len(options) > 128:
            raise ValueError("option_keys has too many values")
        object.__setattr__(self, "option_keys", options)
        expected_lane = f"provider:{provider_id}"
        lane = _resource(self.resource_lane, "resource_lane")
        if lane != expected_lane:
            raise ValueError(f"resource_lane must be {expected_lane}")
        object.__setattr__(self, "resource_lane", lane)
        locks = tuple(_resource(item, "resource_lock") for item in self.resource_locks)
        if not locks or len(locks) > 16 or len(locks) != len(set(locks)):
            raise ValueError("resource_locks must contain 1-16 unique locks")
        required_account_lock = f"provider-account:{account_id}"
        if required_account_lock not in locks:
            raise ValueError(f"resource_locks must include {required_account_lock}")
        object.__setattr__(self, "resource_locks", locks)
        golden_id = self.golden_evidence_id
        if golden_id is not None:
            golden_id = _id(golden_id, "golden_evidence_id")
            object.__setattr__(self, "golden_evidence_id", golden_id)
        object.__setattr__(self, "release_ready", _bool(self.release_ready, "release_ready"))
        if self.estimated_credits is not None:
            object.__setattr__(
                self,
                "estimated_credits",
                _integer(self.estimated_credits, "estimated_credits", 0, 10_000_000),
            )
        basis = self.execution_basis
        if basis not in {"quality_golden", "technical_compatibility"}:
            raise ValueError("execution_basis is invalid")
        object.__setattr__(self, "execution_basis", basis)
        technical_id = self.technical_evidence_id
        if technical_id is not None:
            technical_id = _id(technical_id, "technical_evidence_id")
            object.__setattr__(self, "technical_evidence_id", technical_id)
        expected_evidence_id = (
            technical_id if basis == "technical_compatibility" else golden_id
        )
        if expected_evidence_id is None:
            raise ValueError("route requires execution evidence for its execution_basis")
        supplied_evidence_id = self.execution_evidence_id
        if supplied_evidence_id is not None:
            supplied_evidence_id = _id(
                supplied_evidence_id, "execution_evidence_id"
            )
            if supplied_evidence_id != expected_evidence_id:
                raise ValueError("execution evidence does not match execution_basis")
        object.__setattr__(self, "execution_evidence_id", expected_evidence_id)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "route_variant_id": self.route_variant_id,
            "provider_id": self.provider_id,
            "provider_account_id": self.provider_account_id,
            "variant_id": self.variant_id,
            "execution_domain": self.execution_domain.value,
            "capability": self.capability.value,
            "visual_classes": sorted(item.value for item in self.visual_classes),
            "option_keys": sorted(self.option_keys),
            "resource_lane": self.resource_lane,
            "resource_locks": list(self.resource_locks),
            "golden_evidence_id": self.golden_evidence_id,
            "release_ready": self.release_ready,
            "estimated_credits": self.estimated_credits,
        }
        if self.execution_basis == "technical_compatibility":
            value.update({
                "execution_basis": self.execution_basis,
                "technical_evidence_id": self.technical_evidence_id,
                "execution_evidence_id": self.execution_evidence_id,
            })
        return value


@dataclass(frozen=True, slots=True)
class ShotSpec:
    shot_id: str
    scene_id: str
    ordinal: int
    duration_seconds: float
    frame_intent: str
    observable_action: str
    camera: str
    audio_intent: str
    entity_ids: tuple[str, ...]
    input_asset_ids: tuple[str, ...]
    required_capability: CapabilityKind
    required_options: frozenset[str]
    eligible_visual_variant_ids: tuple[str, ...]
    dialogue_mode: DialogueMode = DialogueMode.NONE
    eligible_voice_variant_ids: tuple[str, ...] = ()
    dialogue: tuple[DialogueCue, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    factual_claims: tuple[FactualClaim, ...] = ()
    state_phase: StatePhase = StatePhase.DURING
    continuity_keys: tuple[str, ...] = ()
    continuity_values: Mapping[str, str] = field(default_factory=dict)
    negative: str = ""
    sound_effects: tuple[SoundEffectCue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _id(self.shot_id, "shot_id"))
        object.__setattr__(self, "scene_id", _id(self.scene_id, "scene_id"))
        object.__setattr__(self, "ordinal", _integer(self.ordinal, "shot ordinal", 1, 100_000))
        object.__setattr__(self, "duration_seconds", _number(self.duration_seconds, "shot duration", 0.25, 60))
        for name, maximum in (
            ("frame_intent", 1000),
            ("observable_action", 2000),
            ("camera", 500),
            ("audio_intent", 1000),
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name, maximum))
        entity_ids = _unique_ids(tuple(self.entity_ids), "entity_id", 32)
        if not entity_ids:
            raise ValueError("shot entity_ids cannot be empty")
        object.__setattr__(self, "entity_ids", entity_ids)
        object.__setattr__(self, "input_asset_ids", _unique_ids(tuple(self.input_asset_ids), "input_asset_id", 64))
        object.__setattr__(self, "required_capability", _capability(self.required_capability))
        options = frozenset(_id(item, "required_option") for item in self.required_options)
        if len(options) > 64:
            raise ValueError("required_options has too many values")
        object.__setattr__(self, "required_options", options)
        visual_variants = _unique_ids(tuple(self.eligible_visual_variant_ids), "eligible_visual_variant_id", 32)
        if not visual_variants:
            raise ValueError("eligible_visual_variant_ids cannot be empty")
        object.__setattr__(self, "eligible_visual_variant_ids", visual_variants)
        object.__setattr__(self, "dialogue_mode", _enum(self.dialogue_mode, DialogueMode, "dialogue_mode"))
        object.__setattr__(
            self,
            "eligible_voice_variant_ids",
            _unique_ids(tuple(self.eligible_voice_variant_ids), "eligible_voice_variant_id", 16),
        )
        cues = tuple(self.dialogue)
        if len(cues) > 100 or any(not isinstance(cue, DialogueCue) for cue in cues):
            raise ValueError("dialogue must contain at most 100 DialogueCue objects")
        object.__setattr__(self, "dialogue", cues)
        if self.dialogue_mode is DialogueMode.NONE and cues:
            raise ValueError("dialogue_mode none cannot contain dialogue")
        if self.dialogue_mode is not DialogueMode.NONE and not cues:
            raise ValueError("dialogue mode requires dialogue cues")
        if self.dialogue_mode is DialogueMode.EXTERNAL_AUDIO and any(not cue.audio_asset_id for cue in cues):
            raise ValueError("external_audio dialogue requires an audio asset for every cue")
        if self.dialogue_mode in {DialogueMode.NATIVE, DialogueMode.SEPARATE_VOICE} and any(cue.audio_asset_id for cue in cues):
            raise ValueError("native/separate voice dialogue cannot bind external cue audio")
        if self.dialogue_mode is DialogueMode.NATIVE and "native_audio" not in options:
            raise ValueError("native dialogue requires the native_audio option")
        if self.dialogue_mode is DialogueMode.EXTERNAL_AUDIO and (
            self.required_capability is not CapabilityKind.LIP_SYNC and "audio_reference" not in options
        ):
            raise ValueError("external dialogue requires lip_sync or the audio_reference option")
        if self.dialogue_mode is DialogueMode.SEPARATE_VOICE and not self.eligible_voice_variant_ids:
            raise ValueError("separate voice dialogue requires eligible voice variants")
        if self.dialogue_mode is not DialogueMode.SEPARATE_VOICE and self.eligible_voice_variant_ids:
            raise ValueError("voice variants are only valid for separate_voice dialogue")
        object.__setattr__(self, "evidence_ids", _unique_ids(tuple(self.evidence_ids), "evidence_id", 100))
        claims = tuple(self.factual_claims)
        if len(claims) > 50 or any(not isinstance(item, FactualClaim) for item in claims):
            raise ValueError("factual_claims must contain at most 50 FactualClaim objects")
        if len({claim.claim_id for claim in claims}) != len(claims):
            raise ValueError("factual claim IDs must be unique within a shot")
        object.__setattr__(self, "factual_claims", claims)
        object.__setattr__(self, "state_phase", _enum(self.state_phase, StatePhase, "state_phase"))
        continuity_keys = _unique_ids(tuple(self.continuity_keys), "continuity_key", 100)
        object.__setattr__(self, "continuity_keys", continuity_keys)
        raw_values = _mapping(self.continuity_values, "continuity_values")
        if len(raw_values) > 100:
            raise ValueError("continuity_values has too many keys")
        values = {
            _id(key, "continuity key"): _text(value, "continuity value", 500)
            for key, value in raw_values.items()
        }
        if not set(values).issubset(continuity_keys):
            raise ValueError("continuity_values keys must appear in continuity_keys")
        object.__setattr__(self, "continuity_values", MappingProxyType(values))
        object.__setattr__(self, "negative", _text(self.negative, "negative", 1000, allow_empty=True))
        effects = tuple(self.sound_effects)
        if len(effects) > 16 or any(not isinstance(item, SoundEffectCue) for item in effects):
            raise ValueError("sound_effects must contain at most 16 SoundEffectCue objects")
        if len({item.cue_id for item in effects}) != len(effects):
            raise ValueError("sound effect cue IDs must be unique within a shot")
        if any(item.start_seconds + item.duration_seconds > self.duration_seconds + 0.000001 for item in effects):
            raise ValueError("sound effect cue exceeds its shot")
        object.__setattr__(self, "sound_effects", effects)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "ordinal": self.ordinal,
            "duration_seconds": self.duration_seconds,
            "frame_intent": self.frame_intent,
            "observable_action": self.observable_action,
            "camera": self.camera,
            "audio_intent": self.audio_intent,
            "entity_ids": list(self.entity_ids),
            "input_asset_ids": list(self.input_asset_ids),
            "required_capability": self.required_capability.value,
            "required_options": sorted(self.required_options),
            "eligible_visual_variant_ids": list(self.eligible_visual_variant_ids),
            "dialogue_mode": self.dialogue_mode.value,
            "eligible_voice_variant_ids": list(self.eligible_voice_variant_ids),
            "dialogue": [cue.to_dict() for cue in self.dialogue],
            "evidence_ids": list(self.evidence_ids),
            "factual_claims": [claim.to_dict() for claim in self.factual_claims],
            "state_phase": self.state_phase.value,
            "continuity_keys": list(self.continuity_keys),
            "continuity_values": dict(self.continuity_values),
            "negative": self.negative,
        }
        if self.sound_effects:
            value["sound_effects"] = [item.to_dict() for item in self.sound_effects]
        return value


@dataclass(frozen=True, slots=True)
class SceneSpec:
    scene_id: str
    ordinal: int
    state_before: Mapping[str, str]
    event: str
    state_after: Mapping[str, str]
    shot_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_id", _id(self.scene_id, "scene_id"))
        object.__setattr__(self, "ordinal", _integer(self.ordinal, "scene ordinal", 1, 100_000))
        object.__setattr__(self, "event", _text(self.event, "scene event", 3000))
        for name in ("state_before", "state_after"):
            raw = _mapping(getattr(self, name), name)
            if len(raw) > 200:
                raise ValueError(f"{name} has too many keys")
            normalized = {
                _id(key, "state key"): _text(value, "state value", 500)
                for key, value in raw.items()
            }
            object.__setattr__(self, name, MappingProxyType(normalized))
        shot_ids = _unique_ids(tuple(self.shot_ids), "shot_id", 500)
        if not shot_ids:
            raise ValueError("scene shot_ids cannot be empty")
        object.__setattr__(self, "shot_ids", shot_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "ordinal": self.ordinal,
            "state_before": dict(self.state_before),
            "event": self.event,
            "state_after": dict(self.state_after),
            "shot_ids": list(self.shot_ids),
        }


@dataclass(frozen=True, slots=True)
class ProductionPack:
    project: ProjectSpec
    entities: tuple[EntitySpec, ...]
    evidence: tuple[EvidenceItem, ...]
    route_variants: tuple[RouteVariant, ...]
    scenes: tuple[SceneSpec, ...]
    shots: tuple[ShotSpec, ...]
    version: str = PACK_VERSION

    def __post_init__(self) -> None:
        if self.version != PACK_VERSION:
            raise ValueError(f"version must be {PACK_VERSION}")
        if not isinstance(self.project, ProjectSpec):
            raise ValueError("project must be ProjectSpec")
        specifications = (
            ("entities", EntitySpec, MAX_ENTITIES),
            ("evidence", EvidenceItem, MAX_EVIDENCE_ITEMS),
            ("route_variants", RouteVariant, MAX_ROUTE_VARIANTS),
            ("scenes", SceneSpec, MAX_SCENES),
            ("shots", ShotSpec, MAX_SHOTS),
        )
        for name, cls, maximum in specifications:
            values = tuple(getattr(self, name))
            if len(values) > maximum or any(not isinstance(item, cls) for item in values):
                raise ValueError(f"{name} contains invalid or too many items")
            object.__setattr__(self, name, values)
        if not self.entities or not self.route_variants or not self.scenes or not self.shots:
            raise ValueError("production pack requires entities, route variants, scenes, and shots")
        if len(_canonical_json_bytes(self.to_dict(), "production pack")) > MAX_PACK_BYTES:
            raise ValueError(f"production pack exceeds {MAX_PACK_BYTES} bytes")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical representation; order-free registries are sorted."""

        return {
            "version": self.version,
            "project": self.project.to_dict(),
            "entities": [item.to_dict() for item in sorted(self.entities, key=lambda item: item.entity_id)],
            "evidence": [item.to_dict() for item in sorted(self.evidence, key=lambda item: item.evidence_id)],
            "route_variants": [
                item.to_dict() for item in sorted(self.route_variants, key=lambda item: item.route_variant_id)
            ],
            "scenes": [item.to_dict() for item in sorted(self.scenes, key=lambda item: (item.ordinal, item.scene_id))],
            "shots": [item.to_dict() for item in sorted(self.shots, key=lambda item: (item.scene_id, item.ordinal, item.shot_id))],
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self.to_dict(), "production pack")).hexdigest()


def _parse_project(raw: Mapping[str, Any]) -> ProjectSpec:
    required = {
        "project_id", "title", "premise", "audience", "platform_profile", "language",
        "aspect_ratio", "target_duration_seconds", "usage_scope", "quality_tier", "max_credits",
        "subtitle_profile", "audio_mix_profile", "branding_profile", "deliverable_profile",
        "branding_asset_ids", "ai_disclosure_required",
        "allow_external_providers", "provider_allowlist", "allow_precharge_fallback",
        "max_precharge_route_attempts",
    }
    allowed = required | {
        "soundtrack_asset_version_id", "soundtrack_asset_sha256", "soundtrack_gain_db",
    }
    _exact_keys(raw, allowed, "project", required=required)
    values = dict(raw)
    values["provider_allowlist"] = tuple(_sequence(values["provider_allowlist"], "project.provider_allowlist", 32))
    values["branding_asset_ids"] = tuple(_sequence(values["branding_asset_ids"], "project.branding_asset_ids", 32))
    return ProjectSpec(**values)  # type: ignore[arg-type]


def _parse_entity(raw: Mapping[str, Any]) -> EntitySpec:
    allowed = {
        "entity_id", "kind", "display_name", "visual_class", "consent_state",
        "consent_ref", "consent_evidence_ids", "allowed_provider_ids",
        "allowed_capabilities", "allowed_usage_scopes", "voice_use_allowed", "character_id",
        "profile_asset_id", "wardrobe_asset_id", "voice_asset_id",
    }
    required = {
        "entity_id", "kind", "display_name", "visual_class", "consent_state",
        "allowed_provider_ids", "allowed_capabilities", "allowed_usage_scopes", "voice_use_allowed",
    }
    _exact_keys(raw, allowed, "entity", required=required)
    values = dict(raw)
    for name, maximum in (
        ("consent_evidence_ids", 20),
        ("allowed_provider_ids", 32),
        ("allowed_capabilities", 32),
        ("allowed_usage_scopes", 4),
    ):
        values[name] = tuple(_sequence(values.get(name, []), f"entity.{name}", maximum))
    values["allowed_capabilities"] = frozenset(values["allowed_capabilities"])
    values["allowed_usage_scopes"] = frozenset(values["allowed_usage_scopes"])
    return EntitySpec(**values)  # type: ignore[arg-type]


def _parse_evidence(raw: Mapping[str, Any]) -> EvidenceItem:
    allowed = {"evidence_id", "kind", "title", "source_uri", "source_hash", "is_private"}
    _exact_keys(raw, allowed, "evidence", required=allowed)
    return EvidenceItem(**raw)  # type: ignore[arg-type]


def _parse_claim(raw: Mapping[str, Any]) -> FactualClaim:
    allowed = {"claim_id", "text", "evidence_ids"}
    _exact_keys(raw, allowed, "factual claim", required=allowed)
    values = dict(raw)
    values["evidence_ids"] = tuple(_sequence(values["evidence_ids"], "claim.evidence_ids", 20))
    return FactualClaim(**values)  # type: ignore[arg-type]


def _parse_dialogue(raw: Mapping[str, Any]) -> DialogueCue:
    allowed = {
        "speaker_entity_id", "text", "start_seconds", "end_seconds", "language",
        "audio_asset_id", "source_start_seconds", "source_end_seconds",
    }
    required = {"speaker_entity_id", "text", "start_seconds", "end_seconds", "language"}
    _exact_keys(raw, allowed, "dialogue", required=required)
    return DialogueCue(**raw)  # type: ignore[arg-type]


def _parse_sound_effect(raw: Mapping[str, Any]) -> SoundEffectCue:
    fields = {
        "cue_id", "asset_version_id", "asset_sha256", "start_seconds",
        "source_start_seconds", "duration_seconds", "gain_db",
    }
    _exact_keys(raw, fields, "sound effect", required=fields)
    return SoundEffectCue(**raw)  # type: ignore[arg-type]


def _parse_route_variant(raw: Mapping[str, Any]) -> RouteVariant:
    allowed = {
        "route_variant_id", "provider_id", "provider_account_id", "variant_id",
        "execution_domain", "capability", "visual_classes", "option_keys",
        "resource_lane", "resource_locks", "golden_evidence_id", "release_ready",
        "estimated_credits", "execution_basis", "technical_evidence_id",
        "execution_evidence_id",
    }
    required = {
        "route_variant_id", "provider_id", "provider_account_id", "variant_id",
        "execution_domain", "capability", "visual_classes", "option_keys",
        "resource_lane", "resource_locks", "release_ready",
    }
    basis = raw.get("execution_basis", "quality_golden")
    if basis == "quality_golden":
        required.add("golden_evidence_id")
    elif basis == "technical_compatibility":
        required.add("technical_evidence_id")
    _exact_keys(raw, allowed, "route variant", required=required)
    values = dict(raw)
    values.setdefault("golden_evidence_id", None)
    values["visual_classes"] = frozenset(_sequence(values["visual_classes"], "route.visual_classes", 4))
    values["option_keys"] = frozenset(_sequence(values["option_keys"], "route.option_keys", 128))
    values["resource_locks"] = tuple(_sequence(values["resource_locks"], "route.resource_locks", 16))
    return RouteVariant(**values)  # type: ignore[arg-type]


def _parse_shot(raw: Mapping[str, Any]) -> ShotSpec:
    allowed = {
        "shot_id", "scene_id", "ordinal", "duration_seconds", "frame_intent",
        "observable_action", "camera", "audio_intent", "entity_ids",
        "input_asset_ids", "required_capability", "required_options",
        "eligible_visual_variant_ids", "dialogue_mode", "eligible_voice_variant_ids",
        "dialogue", "evidence_ids", "factual_claims", "state_phase",
        "continuity_keys", "continuity_values", "negative", "sound_effects",
    }
    required = {
        "shot_id", "scene_id", "ordinal", "duration_seconds", "frame_intent",
        "observable_action", "camera", "audio_intent", "entity_ids",
        "input_asset_ids", "required_capability", "required_options",
        "eligible_visual_variant_ids", "dialogue_mode", "state_phase",
        "continuity_keys", "continuity_values",
    }
    _exact_keys(raw, allowed, "shot", required=required)
    values = dict(raw)
    sequence_fields = {
        "entity_ids": 32,
        "input_asset_ids": 64,
        "required_options": 64,
        "eligible_visual_variant_ids": 32,
        "eligible_voice_variant_ids": 16,
        "evidence_ids": 100,
        "continuity_keys": 100,
    }
    for name, maximum in sequence_fields.items():
        values[name] = tuple(_sequence(values.get(name, []), f"shot.{name}", maximum))
    values["required_options"] = frozenset(values["required_options"])
    values["dialogue"] = tuple(
        _parse_dialogue(_mapping(item, "dialogue item"))
        for item in _sequence(values.get("dialogue", []), "shot.dialogue", 100)
    )
    values["factual_claims"] = tuple(
        _parse_claim(_mapping(item, "factual claim item"))
        for item in _sequence(values.get("factual_claims", []), "shot.factual_claims", 50)
    )
    values["sound_effects"] = tuple(
        _parse_sound_effect(_mapping(item, "sound effect item"))
        for item in _sequence(values.get("sound_effects", []), "shot.sound_effects", 16)
    )
    values["continuity_values"] = _mapping(values.get("continuity_values", {}), "shot.continuity_values")
    return ShotSpec(**values)  # type: ignore[arg-type]


def _parse_scene(raw: Mapping[str, Any]) -> SceneSpec:
    allowed = {"scene_id", "ordinal", "state_before", "event", "state_after", "shot_ids"}
    _exact_keys(raw, allowed, "scene", required=allowed)
    values = dict(raw)
    values["state_before"] = _mapping(values["state_before"], "scene.state_before")
    values["state_after"] = _mapping(values["state_after"], "scene.state_after")
    values["shot_ids"] = tuple(_sequence(values["shot_ids"], "scene.shot_ids", 500))
    return SceneSpec(**values)  # type: ignore[arg-type]


def parse_production_pack(raw: Mapping[str, Any]) -> ProductionPack:
    """Parse an exact JSON object, rejecting extras, secrets, and oversized input."""

    value = _mapping(raw, "production pack")
    encoded = _canonical_json_bytes(value, "production pack")
    if len(encoded) > MAX_PACK_BYTES:
        raise ValueError(f"production pack exceeds {MAX_PACK_BYTES} bytes")
    _reject_raw_secrets(value)
    allowed = {"version", "project", "entities", "evidence", "route_variants", "scenes", "shots"}
    _exact_keys(value, allowed, "production pack", required=allowed)
    return ProductionPack(
        version=value["version"],
        project=_parse_project(_mapping(value["project"], "project")),
        entities=tuple(
            _parse_entity(_mapping(item, "entity item"))
            for item in _sequence(value["entities"], "entities", MAX_ENTITIES)
        ),
        evidence=tuple(
            _parse_evidence(_mapping(item, "evidence item"))
            for item in _sequence(value["evidence"], "evidence", MAX_EVIDENCE_ITEMS)
        ),
        route_variants=tuple(
            _parse_route_variant(_mapping(item, "route variant item"))
            for item in _sequence(value["route_variants"], "route_variants", MAX_ROUTE_VARIANTS)
        ),
        scenes=tuple(
            _parse_scene(_mapping(item, "scene item"))
            for item in _sequence(value["scenes"], "scenes", MAX_SCENES)
        ),
        shots=tuple(
            _parse_shot(_mapping(item, "shot item"))
            for item in _sequence(value["shots"], "shots", MAX_SHOTS)
        ),
    )
