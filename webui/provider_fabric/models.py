"""Canonical, provider-neutral contracts for Castle's generation fabric.

The objects in this module deliberately do not contain credentials or provider
SDK objects.  They are safe to persist after calling ``to_dict`` and form the
boundary between the UI/orchestrator and provider adapters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: str, label: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(cleaned):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return cleaned


def _text(value: str, label: str, *, maximum: int = 500) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} is required")
    if len(cleaned) > maximum or any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{label} is invalid")
    return cleaned


def _unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(_identifier(value, label) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} values must be unique")
    return normalized


class VisualClass(str, Enum):
    """The identity domain an operation is proven to preserve.

    ``unknown`` exists for legacy catalog migration only.  Type-sensitive route
    intents and provider offerings reject it, so it can never silently select a
    realistic or anime model.
    """

    ANIME = "anime"
    REALISTIC = "realistic"
    ILLUSTRATION = "illustration"
    UNKNOWN = "unknown"


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"
    FILE = "file"


class CapabilityKind(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"
    LIP_SYNC = "lip_sync"
    DANCE = "dance"
    MULTI_SUBJECT_VIDEO = "multi_subject_video"
    CHARACTER_TRAINING = "character_training"
    VOICE_CLONE = "voice_clone"
    TEXT_TO_SPEECH = "text_to_speech"


class EvidenceState(str, Enum):
    UNPROVEN = "unproven"
    PROBED = "probed"
    VERIFIED = "verified"


class TaskState(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SUBMISSION_UNCERTAIN = "submission_uncertain"

    @property
    def terminal(self) -> bool:
        return self in {
            self.COMPLETED,
            self.FAILED,
            self.CANCELLED,
            self.TIMEOUT,
        }


@dataclass(frozen=True, slots=True)
class ExternalReference:
    provider_id: str
    external_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identifier(self.provider_id, "provider_id"))
        object.__setattr__(
            self,
            "external_id",
            _text(self.external_id, "external_id", maximum=300),
        )

    def to_dict(self) -> dict[str, str]:
        return {"provider_id": self.provider_id, "external_id": self.external_id}


@dataclass(frozen=True, slots=True)
class CharacterRef:
    character_id: str
    visual_class: VisualClass
    display_name: str | None = None
    external_refs: tuple[ExternalReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "character_id", _identifier(self.character_id, "character_id"))
        if not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        if self.display_name is not None:
            object.__setattr__(
                self,
                "display_name",
                _text(self.display_name, "display_name", maximum=160),
            )
        refs = tuple(self.external_refs)
        if any(not isinstance(item, ExternalReference) for item in refs):
            raise ValueError("external_refs must contain ExternalReference objects")
        object.__setattr__(self, "external_refs", refs)
        pairs = [(item.provider_id, item.external_id) for item in refs]
        if len(pairs) != len(set(pairs)):
            raise ValueError("external_refs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "visual_class": self.visual_class.value,
            "display_name": self.display_name,
            "external_refs": [item.to_dict() for item in self.external_refs],
        }


@dataclass(frozen=True, slots=True)
class MediaAsset:
    asset_id: str
    kind: MediaKind
    uri: str
    visual_class: VisualClass | None = None
    character_ids: tuple[str, ...] = ()
    mime_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _identifier(self.asset_id, "asset_id"))
        if not isinstance(self.kind, MediaKind):
            object.__setattr__(self, "kind", MediaKind(self.kind))
        uri = _text(self.uri, "uri", maximum=4096)
        parsed = urlsplit(uri)
        if parsed.scheme not in {"castle", "file", "http", "https"}:
            raise ValueError("uri must use castle, file, http, or https")
        if parsed.scheme in {"http", "https"} and not parsed.netloc:
            raise ValueError("remote uri must include a host")
        object.__setattr__(self, "uri", uri)
        if self.visual_class is not None and not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        object.__setattr__(
            self,
            "character_ids",
            _unique(tuple(self.character_ids), "character_id"),
        )
        if self.mime_type is not None:
            mime = str(self.mime_type).strip().lower()
            if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime):
                raise ValueError("mime_type is invalid")
            object.__setattr__(self, "mime_type", mime)
        if self.byte_size is not None and (
            not isinstance(self.byte_size, int)
            or isinstance(self.byte_size, bool)
            or self.byte_size < 0
        ):
            raise ValueError("byte_size must be a non-negative integer")
        if self.sha256 is not None:
            digest = str(self.sha256).strip().lower()
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError("sha256 must be a lowercase hexadecimal digest")
            object.__setattr__(self, "sha256", digest)
        if self.duration_seconds is not None and (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be finite and non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind.value,
            "uri": self.uri,
            "visual_class": self.visual_class.value if self.visual_class else None,
            "character_ids": list(self.character_ids),
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class CapabilityOffering:
    provider_id: str
    capability: CapabilityKind
    variant_id: str
    visual_classes: frozenset[VisualClass]
    evidence: EvidenceState = EvidenceState.UNPROVEN
    evidence_id: str | None = None
    enabled: bool = False
    priority: int = 100
    option_keys: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identifier(self.provider_id, "provider_id"))
        object.__setattr__(self, "variant_id", _identifier(self.variant_id, "variant_id"))
        if not isinstance(self.capability, CapabilityKind):
            object.__setattr__(self, "capability", CapabilityKind(self.capability))
        classes = frozenset(
            item if isinstance(item, VisualClass) else VisualClass(item)
            for item in self.visual_classes
        )
        if not classes:
            raise ValueError("visual_classes cannot be empty")
        if VisualClass.UNKNOWN in classes:
            raise ValueError("unknown visual class cannot be offered for generation")
        object.__setattr__(self, "visual_classes", classes)
        if not isinstance(self.evidence, EvidenceState):
            object.__setattr__(self, "evidence", EvidenceState(self.evidence))
        if self.evidence is EvidenceState.VERIFIED and not self.evidence_id:
            raise ValueError("verified offerings require evidence_id")
        if self.evidence_id is not None:
            object.__setattr__(
                self,
                "evidence_id",
                _text(self.evidence_id, "evidence_id", maximum=300),
            )
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError("priority must be an integer")
        object.__setattr__(
            self,
            "option_keys",
            frozenset(_identifier(item, "option_key") for item in self.option_keys),
        )

    @property
    def key(self) -> str:
        return f"{self.provider_id}:{self.variant_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "capability": self.capability.value,
            "variant_id": self.variant_id,
            "visual_classes": sorted(item.value for item in self.visual_classes),
            "evidence": self.evidence.value,
            "evidence_id": self.evidence_id,
            "enabled": self.enabled,
            "priority": self.priority,
            "option_keys": sorted(self.option_keys),
        }


@dataclass(frozen=True, slots=True)
class RouteIntent:
    capability: CapabilityKind
    visual_class: VisualClass
    characters: tuple[CharacterRef, ...] = ()
    assets: tuple[MediaAsset, ...] = ()
    required_options: frozenset[str] = field(default_factory=frozenset)
    preferred_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityKind):
            object.__setattr__(self, "capability", CapabilityKind(self.capability))
        if not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        if self.visual_class is VisualClass.UNKNOWN:
            raise ValueError("unknown visual class cannot be routed")
        characters = tuple(self.characters)
        assets = tuple(self.assets)
        if any(not isinstance(item, CharacterRef) for item in characters):
            raise ValueError("characters must contain CharacterRef objects")
        if any(not isinstance(item, MediaAsset) for item in assets):
            raise ValueError("assets must contain MediaAsset objects")
        object.__setattr__(self, "characters", characters)
        object.__setattr__(self, "assets", assets)
        object.__setattr__(
            self,
            "required_options",
            frozenset(_identifier(item, "required_option") for item in self.required_options),
        )
        object.__setattr__(
            self,
            "preferred_providers",
            _unique(tuple(self.preferred_providers), "preferred_provider"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability.value,
            "visual_class": self.visual_class.value,
            "characters": [item.to_dict() for item in self.characters],
            "assets": [item.to_dict() for item in self.assets],
            "required_options": sorted(self.required_options),
            "preferred_providers": list(self.preferred_providers),
        }


@dataclass(frozen=True, slots=True)
class RouteRejection:
    offering_key: str
    reason_code: str
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, "reason_code"))
        object.__setattr__(self, "detail", _text(self.detail, "detail", maximum=500))

    def to_dict(self) -> dict[str, str]:
        return {
            "offering_key": self.offering_key,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RouteDecision:
    selected_provider_id: str | None
    selected_variant_id: str | None
    candidate_keys: tuple[str, ...]
    rejections: tuple[RouteRejection, ...]
    policy_version: str

    def __post_init__(self) -> None:
        if bool(self.selected_provider_id) != bool(self.selected_variant_id):
            raise ValueError("selected provider and variant must be set together")
        if self.selected_provider_id is not None:
            object.__setattr__(
                self,
                "selected_provider_id",
                _identifier(self.selected_provider_id, "selected_provider_id"),
            )
            object.__setattr__(
                self,
                "selected_variant_id",
                _identifier(str(self.selected_variant_id), "selected_variant_id"),
            )
        candidates = tuple(str(item) for item in self.candidate_keys)
        rejections = tuple(self.rejections)
        if any(not isinstance(item, RouteRejection) for item in rejections):
            raise ValueError("rejections must contain RouteRejection objects")
        object.__setattr__(self, "candidate_keys", candidates)
        object.__setattr__(self, "rejections", rejections)
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, "policy_version"),
        )
        selected_key = (
            f"{self.selected_provider_id}:{self.selected_variant_id}"
            if self.selected_provider_id
            else None
        )
        if selected_key is not None and selected_key not in candidates:
            raise ValueError("selected offering must be present in candidate_keys")

    @property
    def routable(self) -> bool:
        return bool(self.selected_provider_id and self.selected_variant_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "routable": self.routable,
            "selected_provider_id": self.selected_provider_id,
            "selected_variant_id": self.selected_variant_id,
            "candidate_keys": list(self.candidate_keys),
            "rejections": [item.to_dict() for item in self.rejections],
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class CostQuote:
    provider_id: str
    capability: CapabilityKind
    credits: int | None
    remaining_credits: int | None
    authoritative: bool
    quote_id: str | None = None
    detail: str = ""
    account_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identifier(self.provider_id, "provider_id"))
        if not isinstance(self.capability, CapabilityKind):
            object.__setattr__(self, "capability", CapabilityKind(self.capability))
        for name in ("credits", "remaining_credits"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.authoritative, bool):
            raise ValueError("authoritative must be a boolean")
        if self.authoritative and not self.quote_id:
            raise ValueError("authoritative quotes require quote_id")
        if self.quote_id is not None:
            object.__setattr__(self, "quote_id", _identifier(self.quote_id, "quote_id"))
        if self.account_id is not None:
            object.__setattr__(
                self,
                "account_id",
                _identifier(self.account_id, "account_id"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "capability": self.capability.value,
            "credits": self.credits,
            "remaining_credits": self.remaining_credits,
            "authoritative": self.authoritative,
            "quote_id": self.quote_id,
            "detail": self.detail,
            "account_id": self.account_id,
        }


@dataclass(frozen=True, slots=True)
class PreflightResult:
    accepted: bool
    reason_codes: tuple[str, ...] = ()
    detail: str = ""
    quote: CostQuote | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be a boolean")
        object.__setattr__(
            self,
            "reason_codes",
            tuple(_identifier(item, "reason_code") for item in self.reason_codes),
        )
        if self.accepted and self.reason_codes:
            raise ValueError("accepted preflight cannot contain rejection reasons")

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason_codes": list(self.reason_codes),
            "detail": self.detail,
            "quote": self.quote.to_dict() if self.quote else None,
        }


@dataclass(frozen=True, slots=True)
class TaskReceipt:
    provider_id: str
    client_request_id: str
    state: TaskState
    external_task_id: str | None = None
    credits_charged: int | None = None
    remaining_credits: int | None = None
    detail: str = ""
    outputs: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identifier(self.provider_id, "provider_id"))
        object.__setattr__(
            self,
            "client_request_id",
            _identifier(self.client_request_id, "client_request_id"),
        )
        if not isinstance(self.state, TaskState):
            object.__setattr__(self, "state", TaskState(self.state))
        if self.external_task_id is not None:
            object.__setattr__(
                self,
                "external_task_id",
                _text(self.external_task_id, "external_task_id", maximum=300),
            )
        if self.state not in {TaskState.CREATED, TaskState.SUBMISSION_UNCERTAIN} and not self.external_task_id:
            raise ValueError("submitted and provider-side task states require external_task_id")
        for name in ("credits_charged", "remaining_credits"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        normalized_outputs: list[Mapping[str, Any]] = []
        for item in self.outputs:
            if not isinstance(item, Mapping):
                raise ValueError("outputs must contain mapping objects")
            normalized_outputs.append(MappingProxyType(dict(item)))
        object.__setattr__(self, "outputs", tuple(normalized_outputs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "client_request_id": self.client_request_id,
            "state": self.state.value,
            "external_task_id": self.external_task_id,
            "credits_charged": self.credits_charged,
            "remaining_credits": self.remaining_credits,
            "detail": self.detail,
            "outputs": [dict(item) for item in self.outputs],
        }
