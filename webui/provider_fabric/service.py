"""Framework-neutral, fail-closed service facade for the provider fabric.

The facade is deliberately smaller than an HTTP controller.  Callers pass an
authenticated :class:`ActorContext` explicitly, translate
:class:`ProviderServiceError` into their own response format, and provide all
mutable dependencies.  Planning performs no provider I/O.  Confirmation only
persists an immutable intent, route decision, and pending outbox record; this
module has no transport dispatch path and therefore cannot spend credits.

Secrets have two narrow entry points: the write-only credential vault and the
AutoIP webhook verifier.  Neither path offers secret readback or returns a host
secret-file locator.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .autoip import AUTOIP_API_BASE, AUTOIP_PROVIDER_ID
from .autoip_profiles import AUTOIP_PROFILE_BY_KEY
from .config import SecretReference, SecretValue
from .credentials import (
    CredentialAlreadyConfigured,
    CredentialNotConfigured,
    CredentialVaultError,
    ProviderCredentialVault,
)
from .models import (
    CapabilityKind,
    CapabilityOffering,
    CharacterRef,
    CostQuote,
    EvidenceState,
    ExternalReference,
    MediaAsset,
    MediaKind,
    RouteDecision,
    RouteIntent,
    RouteRejection,
    VisualClass,
)
from .router import DeterministicRouter
from .store import ProviderFabricStore, ProviderFabricStoreError, StoreConflictError
from .variant_profiles import MaterializedVariantRequest, VariantProfileError
from .webhook import (
    AutoIPWebhookVerifier,
    MemoryReplayGuard,
    PersistentReplayGuard,
    WebhookReplayError,
    WebhookVerificationError,
)


SERVICE_SCHEMA_VERSION = 1
PLAN_TOKEN_TYPE = "castle.provider-plan.v1"
CREATIVE_INSTRUCTION_TYPE = "castle.creative-instruction.v1"
_PUBLIC_INTENT_ID_RE = re.compile(r"^intent:[0-9a-f]{32}$")
DEFAULT_PLAN_TTL_SECONDS = 120

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_PROFILE_KEY_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,127}:[a-z0-9][a-z0-9._-]{0,127}$"
)
_OPTION_RE = re.compile(r"^[a-z][a-z0-9._:-]{0,127}$")
_PLAN_NONCE_RE = re.compile(r"^[0-9a-f]{32,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLES = frozenset({"admin", "operator", "member"})
_ELEVATED_ROLES = frozenset({"admin", "operator"})
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


class ProviderServiceError(RuntimeError):
    """A public-safe failure with a stable machine-readable code."""

    __slots__ = ("code", "status", "reason_codes")

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        reason_codes: Sequence[str] = (),
    ) -> None:
        self.code = _stable_id(code, "error code")
        self.status = int(status)
        self.reason_codes = tuple(_stable_id(item, "reason code") for item in reason_codes)
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "reason_codes": list(self.reason_codes),
            }
        }


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Identity already authenticated and authorized by Castle's outer layer."""

    user_id: str
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _stable_id(self.user_id, "user_id"))
        role = str(self.role or "").strip().lower()
        if role not in _ROLES:
            raise ValueError("role is invalid")
        object.__setattr__(self, "role", role)


@dataclass(frozen=True, slots=True)
class _ParsedPlan:
    intent: RouteIntent
    options: Mapping[str, Any]
    max_credits: int | None
    provider_constraint: str | None
    creative_instruction: Mapping[str, Any] | None = None
    action_id: str | None = None
    feature_id: str | None = None
    profile_key: str | None = None
    materialized: MaterializedVariantRequest | None = None


QuoteSource = Callable[
    [ActorContext, Mapping[str, Any], RouteIntent, RouteDecision, Mapping[str, Any]],
    CostQuote | None,
]
AutoIPReadinessSource = Callable[
    [ActorContext, str],
    Mapping[str, Any] | None,
]
OfferingSource = Callable[[], Sequence[CapabilityOffering]]
H3AuditSource = Callable[[], Mapping[str, Any]]
OptionValidator = Callable[
    [RouteIntent, RouteDecision, Mapping[str, Any]],
    Mapping[str, Any],
]
CapabilityMaterializer = Callable[
    [ActorContext, RouteIntent, str, str, str, Mapping[str, Any]],
    MaterializedVariantRequest,
]
CanonicalResourceResolver = Callable[
    [ActorContext, tuple[str, ...], tuple[str, ...]],
    tuple[Mapping[str, CharacterRef], Mapping[str, MediaAsset]],
]

H3_CONTRACT_IDS = frozenset(
    {
        "h3.t2v.stock.v1",
        "h3.i2v.anime.stock.v1",
        "h3.i2v.realistic.stock.v1",
        "h3.ref2va-image.anime.stock.v1",
        "h3.ref2va-image.realistic.stock.v1",
    }
)
H3_APPROVED_VARIANT_CONTRACTS = frozenset(
    {
        (
            "image-video.anime.h3-fl2va.v1",
            "h3.i2v.anime.stock.v1",
        ),
        (
            "image-video.realistic.h3-fl2va.v1",
            "h3.i2v.realistic.stock.v1",
        ),
        (
            "reference-video.anime.h3-ref2va-image.v1",
            "h3.ref2va-image.anime.stock.v1",
        ),
        (
            "reference-video.realistic.h3-ref2va-image.v1",
            "h3.ref2va-image.realistic.stock.v1",
        ),
    }
)
_H3_VARIANT_RE = re.compile(r"(?:^|[._-])h3(?:[._-]|$)")


def _stable_id(value: Any, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return normalized


def _profile_key(value: Any, label: str = "profile_key") -> str:
    normalized = str(value or "").strip().lower()
    if not _PROFILE_KEY_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be an exact provider:variant key")
    return normalized


def _strict_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extras = set(value) - allowed
    if extras:
        raise ProviderServiceError(
            "schema_invalid",
            f"{label} contains unsupported fields.",
            reason_codes=("unknown_fields",),
        )


def _secret_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _assert_public_tree(value: Any, path: str = "request", *, depth: int = 0) -> None:
    """Validate bounded JSON-like data and reject credential-shaped fields."""

    if depth > 16:
        raise ProviderServiceError("schema_invalid", "Request data is too deeply nested.")
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise ProviderServiceError("schema_invalid", "Request object is too large.")
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 160:
                raise ProviderServiceError("schema_invalid", "Request field name is invalid.")
            if _secret_key(key):
                raise ProviderServiceError(
                    "secret_field_forbidden",
                    "Secrets and session credentials are not accepted by this operation.",
                )
            _assert_public_tree(child, f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 512:
            raise ProviderServiceError("schema_invalid", "Request array is too large.")
        for index, child in enumerate(value):
            _assert_public_tree(child, f"{path}[{index}]", depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProviderServiceError("schema_invalid", "Non-finite numbers are not allowed.")
        return
    if isinstance(value, str):
        if len(value) > 20_000 or "\x00" in value:
            raise ProviderServiceError("schema_invalid", "Request text is invalid.")
        if value.startswith(("http://", "https://")):
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query:
                raise ProviderServiceError(
                    "signed_url_forbidden",
                    "Credentialed or signed remote URLs are not accepted in public intent data.",
                )
        return
    raise ProviderServiceError("schema_invalid", "Request contains a non-JSON value.")


def _canonical_json(value: Any) -> str:
    _assert_public_tree(value, "canonical")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("schema_invalid", "Request is not canonical JSON data.") from error


def _schema_version(value: Mapping[str, Any]) -> None:
    version = value.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != SERVICE_SCHEMA_VERSION:
        raise ProviderServiceError(
            "schema_version_unsupported",
            "The provider service schema version is unsupported.",
            status=409,
        )


def _safe_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderServiceError("schema_invalid", f"{label} must be an object.")
    return value


def _safe_sequence(value: Any, label: str, *, maximum: int) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ProviderServiceError("schema_invalid", f"{label} must be an array.")
    if len(value) > maximum:
        raise ProviderServiceError("schema_invalid", f"{label} has too many entries.")
    return value


def _bounded_instruction_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProviderServiceError("schema_invalid", f"{label} must be text.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ProviderServiceError("schema_invalid", f"{label} is invalid.")
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ProviderServiceError("schema_invalid", f"{label} contains control characters.")
    return normalized


def _bounded_instruction_descriptions(value: Any) -> list[str]:
    descriptions = _safe_sequence(
        value, "subject_descriptions", maximum=4
    )
    return [
        _bounded_instruction_text(item, "subject description", 200)
        for item in descriptions
    ]


def _parse_creative_instruction(value: Any, *, signed: bool = False) -> Mapping[str, Any] | None:
    """Canonicalize the immutable, public creative meaning of one storyboard shot."""

    if value is None:
        return None
    item = _safe_mapping(value, "creative_instruction")
    allowed = {
        "schema_version",
        "shot_id",
        "frame_intent",
        "observable_action",
        "camera",
        "audio_intent",
        "language",
        "production_binding",
        "subject_descriptions",
        "dialogue_text",
        "negative_prompt",
        "seed",
    }
    if signed:
        allowed.add("content_hash")
    _strict_keys(item, frozenset(allowed), "creative_instruction")
    if item.get("schema_version") != CREATIVE_INSTRUCTION_TYPE:
        raise ProviderServiceError(
            "schema_invalid", "creative_instruction schema version is unsupported."
        )
    try:
        shot_id = _stable_id(item.get("shot_id"), "shot_id")
    except ValueError as error:
        raise ProviderServiceError("schema_invalid", "creative_instruction shot_id is invalid.") from error
    material = {
        "schema_version": CREATIVE_INSTRUCTION_TYPE,
        "shot_id": shot_id,
        "frame_intent": _bounded_instruction_text(item.get("frame_intent"), "frame_intent", 1000),
        "observable_action": _bounded_instruction_text(
            item.get("observable_action"), "observable_action", 2000
        ),
        "camera": _bounded_instruction_text(item.get("camera"), "camera", 500),
        "audio_intent": _bounded_instruction_text(item.get("audio_intent"), "audio_intent", 1000),
        "language": _bounded_instruction_text(item.get("language"), "language", 32),
    }
    if "subject_descriptions" in item:
        material["subject_descriptions"] = _bounded_instruction_descriptions(
            item["subject_descriptions"]
        )
    for key, maximum in (("dialogue_text", 1000), ("negative_prompt", 1000)):
        if key in item:
            material[key] = _bounded_instruction_text(item[key], key, maximum)
    if "seed" in item:
        seed = item["seed"]
        if (
            seed != "random"
            and (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or not 1 <= seed <= 4_294_967_295
            )
        ):
            raise ProviderServiceError(
                "schema_invalid", "creative_instruction seed is invalid."
            )
        material["seed"] = seed
    binding = item.get("production_binding")
    if binding is not None:
        binding = _safe_mapping(binding, "production_binding")
        _strict_keys(
            binding,
            frozenset({
                "schema_version", "project_id", "pack_hash", "state_hash",
                "shot_id", "route_variant_id",
            }),
            "production_binding",
        )
        if binding.get("schema_version") != "castle.production-binding.v1":
            raise ProviderServiceError("schema_invalid", "production_binding version is unsupported.")
        try:
            project_id = _stable_id(binding.get("project_id"), "project_id")
            bound_shot_id = _stable_id(binding.get("shot_id"), "shot_id")
            route_variant_id = _stable_id(binding.get("route_variant_id"), "route_variant_id")
        except ValueError as error:
            raise ProviderServiceError("schema_invalid", "production_binding identity is invalid.") from error
        pack_hash = binding.get("pack_hash")
        state_hash = binding.get("state_hash")
        if (
            (binding.get("project_id"), binding.get("shot_id"), binding.get("route_variant_id"))
            != (project_id, bound_shot_id, route_variant_id)
            or
            not isinstance(pack_hash, str) or _SHA256_RE.fullmatch(pack_hash) is None
            or not isinstance(state_hash, str) or _SHA256_RE.fullmatch(state_hash) is None
            or bound_shot_id != shot_id
        ):
            raise ProviderServiceError("schema_invalid", "production_binding hash or shot differs.")
        material["production_binding"] = {
            "schema_version": "castle.production-binding.v1",
            "project_id": project_id,
            "pack_hash": pack_hash,
            "state_hash": state_hash,
            "shot_id": bound_shot_id,
            "route_variant_id": route_variant_id,
        }
    content_hash = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    if signed and item.get("content_hash") != content_hash:
        raise ProviderServiceError(
            "plan_token_invalid", "Creative instruction content hash is invalid."
        )
    return {**material, "content_hash": content_hash}


def _parse_external_ref(value: Any) -> ExternalReference:
    item = _safe_mapping(value, "external reference")
    _strict_keys(item, frozenset({"provider_id", "external_id"}), "external reference")
    try:
        return ExternalReference(
            provider_id=str(item.get("provider_id", "")),
            external_id=str(item.get("external_id", "")),
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("schema_invalid", "External reference is invalid.") from error


def _parse_character(value: Any) -> CharacterRef:
    item = _safe_mapping(value, "character")
    _strict_keys(
        item,
        frozenset({"character_id", "visual_class", "display_name", "external_refs"}),
        "character",
    )
    refs = tuple(
        _parse_external_ref(child)
        for child in _safe_sequence(item.get("external_refs", []), "external_refs", maximum=32)
    )
    display_name = item.get("display_name")
    if display_name is not None and not isinstance(display_name, str):
        raise ProviderServiceError("schema_invalid", "display_name must be text or null.")
    try:
        return CharacterRef(
            character_id=str(item.get("character_id", "")),
            visual_class=VisualClass(str(item.get("visual_class", ""))),
            display_name=display_name,
            external_refs=refs,
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("schema_invalid", "Character reference is invalid.") from error


def _parse_asset(value: Any) -> MediaAsset:
    item = _safe_mapping(value, "asset")
    _strict_keys(
        item,
        frozenset(
            {
                "asset_id",
                "kind",
                "uri",
                "visual_class",
                "character_ids",
                "mime_type",
                "byte_size",
                "sha256",
                "duration_seconds",
            }
        ),
        "asset",
    )
    uri = str(item.get("uri", ""))
    parsed_uri = urlsplit(uri)
    if (
        parsed_uri.scheme != "castle"
        or parsed_uri.username
        or parsed_uri.password
        or parsed_uri.query
        or parsed_uri.fragment
    ):
        raise ProviderServiceError(
            "asset_reference_untrusted",
            "Intent assets must use an adopted Castle asset reference.",
        )
    visual = item.get("visual_class")
    try:
        return MediaAsset(
            asset_id=str(item.get("asset_id", "")),
            kind=MediaKind(str(item.get("kind", ""))),
            uri=uri,
            visual_class=VisualClass(str(visual)) if visual is not None else None,
            character_ids=tuple(
                str(child)
                for child in _safe_sequence(
                    item.get("character_ids", []), "character_ids", maximum=64
                )
            ),
            mime_type=item.get("mime_type"),
            byte_size=item.get("byte_size"),
            sha256=item.get("sha256"),
            duration_seconds=item.get("duration_seconds"),
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("schema_invalid", "Asset reference is invalid.") from error


def _parse_options(value: Any) -> dict[str, Any]:
    options = _safe_mapping(value, "options")
    if len(options) > 64:
        raise ProviderServiceError("schema_invalid", "Too many intent options were supplied.")
    normalized: dict[str, Any] = {}
    for raw_key, child in options.items():
        if not isinstance(raw_key, str) or not _OPTION_RE.fullmatch(raw_key):
            raise ProviderServiceError("schema_invalid", "An option key is invalid.")
        if _secret_key(raw_key):
            raise ProviderServiceError(
                "secret_field_forbidden",
                "Credential-shaped options are forbidden.",
            )
        _assert_public_tree(child, f"options.{raw_key}")
        normalized[raw_key] = child
    # Round-trip strips non-standard Mapping/list implementations and proves
    # options are stable before they are included in a signature.
    return json.loads(_canonical_json(normalized))


def _intent_from_mapping(
    value: Any,
    *,
    validate_resource_visuals: bool = True,
) -> RouteIntent:
    item = _safe_mapping(value, "intent")
    _strict_keys(
        item,
        frozenset(
            {
                "capability",
                "visual_class",
                "characters",
                "assets",
                "required_options",
                "preferred_providers",
            }
        ),
        "intent",
    )
    raw_visual = str(item.get("visual_class", ""))
    if raw_visual == VisualClass.UNKNOWN.value:
        raise ProviderServiceError(
            "unknown_visual_class",
            "An unknown visual class cannot enter generation routing.",
        )
    try:
        visual_class = VisualClass(raw_visual)
        capability = CapabilityKind(str(item.get("capability", "")))
    except ValueError as error:
        raise ProviderServiceError("schema_invalid", "Capability or visual class is invalid.") from error
    characters = tuple(
        _parse_character(child)
        for child in _safe_sequence(item.get("characters", []), "characters", maximum=16)
    )
    assets = tuple(
        _parse_asset(child)
        for child in _safe_sequence(item.get("assets", []), "assets", maximum=64)
    )
    required = frozenset(
        str(child)
        for child in _safe_sequence(
            item.get("required_options", []), "required_options", maximum=64
        )
    )
    preferred = tuple(
        str(child)
        for child in _safe_sequence(
            item.get("preferred_providers", []), "preferred_providers", maximum=16
        )
    )
    if validate_resource_visuals and any(
        character.visual_class is not visual_class for character in characters
    ):
        raise ProviderServiceError(
            "visual_class_mismatch",
            "A selected character belongs to a different visual class.",
        )
    if validate_resource_visuals and any(
        asset.visual_class is not None and asset.visual_class is not visual_class
        for asset in assets
    ):
        raise ProviderServiceError(
            "visual_class_mismatch",
            "An identity-bearing asset belongs to a different visual class.",
        )
    try:
        return RouteIntent(
            capability=capability,
            visual_class=visual_class,
            characters=characters,
            assets=assets,
            required_options=required,
            preferred_providers=preferred,
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("schema_invalid", "Intent is invalid.") from error


def _parse_plan_request(value: Any) -> _ParsedPlan:
    request = _safe_mapping(value, "plan request")
    _strict_keys(
        request,
        frozenset(
            {
                "schema_version",
                "capability",
                "visual_class",
                "characters",
                "assets",
                "options",
                "preferred_providers",
                "provider_id",
                "max_credits",
                "action_id",
                "feature_id",
                "profile_key",
                "creative_instruction",
            }
        ),
        "plan request",
    )
    _schema_version(request)
    _assert_public_tree(request)
    options = _parse_options(request.get("options", {}))
    creative_instruction = _parse_creative_instruction(request.get("creative_instruction"))
    provider_constraint_raw = request.get("provider_id")
    provider_constraint = None
    if provider_constraint_raw not in (None, ""):
        try:
            provider_constraint = _stable_id(provider_constraint_raw, "provider_id")
        except ValueError as error:
            raise ProviderServiceError("schema_invalid", "provider_id is invalid.") from error
    preferred_raw = request.get("preferred_providers", [])
    preferred = [str(item) for item in _safe_sequence(
        preferred_raw, "preferred_providers", maximum=16
    )]
    if provider_constraint:
        if preferred and preferred != [provider_constraint]:
            raise ProviderServiceError(
                "schema_invalid",
                "An exact provider constraint cannot conflict with preferred_providers.",
            )
        preferred = [provider_constraint]
    raw_binding = (
        request.get("action_id"),
        request.get("feature_id"),
        request.get("profile_key"),
    )
    if any(item not in (None, "") for item in raw_binding):
        if any(item in (None, "") for item in raw_binding):
            raise ProviderServiceError(
                "schema_invalid",
                "action_id, feature_id, and profile_key must be supplied together.",
                reason_codes=("capability_binding_incomplete",),
            )
        try:
            action_id = _stable_id(raw_binding[0], "action_id")
            feature_id = _stable_id(raw_binding[1], "feature_id")
            profile_key = _profile_key(raw_binding[2])
        except ValueError as error:
            raise ProviderServiceError(
                "schema_invalid", "Character capability identifiers are invalid."
            ) from error
        if tuple(str(item) for item in raw_binding) != (
            action_id,
            feature_id,
            profile_key,
        ):
            raise ProviderServiceError(
                "schema_invalid",
                "Character capability identifiers must use their exact canonical form.",
            )
    else:
        action_id = None
        feature_id = None
        profile_key = None
    intent_mapping = {
        "capability": request.get("capability"),
        "visual_class": request.get("visual_class"),
        "characters": request.get("characters", []),
        "assets": request.get("assets", []),
        "required_options": sorted(options),
        "preferred_providers": preferred,
    }
    # Browser resource fields are only syntactic references at this point.
    # Their visual classes and metadata are replaced from the actor-scoped
    # canonical store before routing.
    intent = _intent_from_mapping(
        intent_mapping,
        validate_resource_visuals=False,
    )
    maximum = request.get("max_credits")
    if maximum is not None and (
        not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0
    ):
        raise ProviderServiceError(
            "schema_invalid",
            "max_credits must be a non-negative integer or null.",
        )
    return _ParsedPlan(
        intent=intent,
        options=options,
        max_credits=maximum,
        provider_constraint=provider_constraint,
        creative_instruction=creative_instruction,
        action_id=action_id,
        feature_id=feature_id,
        profile_key=profile_key,
    )


def _decision_from_mapping(value: Any) -> RouteDecision:
    item = _safe_mapping(value, "route decision")
    _strict_keys(
        item,
        frozenset(
            {
                "routable",
                "selected_provider_id",
                "selected_variant_id",
                "candidate_keys",
                "rejections",
                "policy_version",
            }
        ),
        "route decision",
    )
    rejections: list[RouteRejection] = []
    for raw in _safe_sequence(item.get("rejections", []), "rejections", maximum=512):
        rejection = _safe_mapping(raw, "route rejection")
        _strict_keys(
            rejection,
            frozenset({"offering_key", "reason_code", "detail"}),
            "route rejection",
        )
        try:
            rejections.append(RouteRejection(
                offering_key=str(rejection.get("offering_key", "")),
                reason_code=str(rejection.get("reason_code", "")),
                detail=str(rejection.get("detail", "")),
            ))
        except ValueError as error:
            raise ProviderServiceError("plan_token_invalid", "Plan route is invalid.") from error
    try:
        decision = RouteDecision(
            selected_provider_id=item.get("selected_provider_id"),
            selected_variant_id=item.get("selected_variant_id"),
            candidate_keys=tuple(
                str(child)
                for child in _safe_sequence(
                    item.get("candidate_keys", []), "candidate_keys", maximum=512
                )
            ),
            rejections=tuple(rejections),
            policy_version=str(item.get("policy_version", "")),
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("plan_token_invalid", "Plan route is invalid.") from error
    if item.get("routable") is not decision.routable:
        raise ProviderServiceError("plan_token_invalid", "Plan route state is inconsistent.")
    return decision


def _quote_from_mapping(value: Any) -> CostQuote:
    item = _safe_mapping(value, "quote")
    _strict_keys(
        item,
        frozenset(
            {
                "provider_id",
                "capability",
                "credits",
                "remaining_credits",
                "authoritative",
                "quote_id",
                "detail",
                "account_id",
            }
        ),
        "quote",
    )
    authoritative = item.get("authoritative")
    if not isinstance(authoritative, bool):
        raise ProviderServiceError("plan_token_invalid", "Plan quote authority is invalid.")
    try:
        return CostQuote(
            provider_id=str(item.get("provider_id", "")),
            capability=CapabilityKind(str(item.get("capability", ""))),
            credits=item.get("credits"),
            remaining_credits=item.get("remaining_credits"),
            authoritative=authoritative,
            quote_id=item.get("quote_id"),
            detail=str(item.get("detail", "")),
            account_id=item.get("account_id"),
        )
    except (TypeError, ValueError) as error:
        raise ProviderServiceError("plan_token_invalid", "Plan quote is invalid.") from error


def _capability_binding_from_token(
    value: Any,
    *,
    required: bool,
) -> Mapping[str, str] | None:
    if value is None:
        if required:
            raise ProviderServiceError(
                "plan_token_invalid",
                "Identity-bearing plan is missing its character capability binding.",
            )
        return None
    if not required or not isinstance(value, Mapping):
        raise ProviderServiceError(
            "plan_token_invalid", "Plan character capability binding is invalid."
        )
    common = frozenset(
        {"action_id", "feature_id", "profile_key", "profile_revision"}
    )
    evidence_keys = set(value) - common
    if set(value).intersection(common) != common or evidence_keys not in (
        {"golden_id"},
        {"technical_evidence_id"},
    ):
        raise ProviderServiceError(
            "plan_token_invalid", "Plan character capability binding is incomplete."
        )
    try:
        normalized = {
            "action_id": _stable_id(value.get("action_id"), "action_id"),
            "feature_id": _stable_id(value.get("feature_id"), "feature_id"),
            "profile_key": _profile_key(value.get("profile_key")),
            "profile_revision": _stable_id(
                value.get("profile_revision"), "profile_revision"
            ),
        }
        if "golden_id" in value:
            normalized["golden_id"] = _stable_id(value.get("golden_id"), "golden_id")
        else:
            normalized["technical_evidence_id"] = _stable_id(
                value.get("technical_evidence_id"), "technical_evidence_id"
            )
    except ValueError as error:
        raise ProviderServiceError(
            "plan_token_invalid", "Plan character capability identifiers are invalid."
        ) from error
    if dict(value) != normalized:
        raise ProviderServiceError(
            "plan_token_invalid", "Plan character capability identifiers are not canonical."
        )
    return normalized


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise ProviderServiceError("plan_token_invalid", "Plan token encoding is invalid.")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as error:
        raise ProviderServiceError("plan_token_invalid", "Plan token encoding is invalid.") from error


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProviderServiceError(
                    "plan_token_invalid", "Plan token contains duplicate fields."
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProviderServiceError("plan_token_invalid", "Plan token JSON is invalid.")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderServiceError("plan_token_invalid", "Plan token JSON is invalid.") from error
    if not isinstance(value, Mapping):
        raise ProviderServiceError("plan_token_invalid", "Plan token must contain an object.")
    return value


class ProviderFabricService:
    """Safe application-service boundary around provider fabric components."""

    def __init__(
        self,
        store: ProviderFabricStore,
        credential_vault: ProviderCredentialVault,
        plan_signing_key: SecretValue,
        *,
        offerings: Sequence[CapabilityOffering] | OfferingSource | None = None,
        quote_source: QuoteSource | None = None,
        option_validator: OptionValidator | None = None,
        capability_materializer: CapabilityMaterializer | None = None,
        canonical_resource_resolver: CanonicalResourceResolver | None = None,
        autoip_readiness_source: AutoIPReadinessSource | None = None,
        provider_accounts: Mapping[str, str] | None = None,
        account_audits: Mapping[str, Mapping[str, Any]] | None = None,
        local_audit: Mapping[str, Any] | None = None,
        h3_audit: Mapping[str, Any] | None = None,
        h3_audit_source: H3AuditSource | None = None,
        h3_variant_contracts: Mapping[str, str] | None = None,
        webhook_secret_ref: SecretReference | None = None,
        known_external_task: Callable[[str], bool] | None = None,
        router: DeterministicRouter | None = None,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] | None = None,
        plan_ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS,
    ) -> None:
        if not isinstance(store, ProviderFabricStore):
            raise TypeError("store must be a ProviderFabricStore")
        if not isinstance(credential_vault, ProviderCredentialVault):
            raise TypeError("credential_vault must be a ProviderCredentialVault")
        if not isinstance(plan_signing_key, SecretValue):
            raise TypeError("plan_signing_key must be a SecretValue")
        if len(plan_signing_key.reveal().encode("utf-8")) < 32:
            raise ValueError("plan signing key must contain at least 32 bytes")
        if not isinstance(plan_ttl_seconds, int) or isinstance(plan_ttl_seconds, bool):
            raise ValueError("plan_ttl_seconds must be an integer")
        if not 30 <= plan_ttl_seconds <= 300:
            raise ValueError("plan_ttl_seconds must be between 30 and 300")
        self._store = store
        self._vault = credential_vault
        self._plan_signing_key = plan_signing_key
        self._offerings_source = (
            None
            if offerings is None
            else offerings if callable(offerings) else lambda: tuple(offerings)
        )
        self._quote_source = quote_source
        self._option_validator = option_validator
        if capability_materializer is not None and not callable(capability_materializer):
            raise TypeError("capability_materializer must be callable")
        self._capability_materializer = capability_materializer
        if canonical_resource_resolver is not None and not callable(
            canonical_resource_resolver
        ):
            raise TypeError("canonical_resource_resolver must be callable")
        self._canonical_resource_resolver = canonical_resource_resolver
        if autoip_readiness_source is not None and not callable(autoip_readiness_source):
            raise TypeError("autoip_readiness_source must be callable")
        self._autoip_readiness_source = autoip_readiness_source
        self._provider_accounts = {
            _stable_id(provider, "provider_id"): _stable_id(account, "account_id")
            for provider, account in dict(provider_accounts or {}).items()
        }
        if set(self._provider_accounts) - {"local_comfy", "clipmode"}:
            raise ValueError(
                "provider_accounts may bind only shared local or import namespaces"
            )
        self._account_audits = {
            _stable_id(account, "account_id"): self._normalize_account_audit(audit)
            for account, audit in dict(account_audits or {}).items()
        }
        self._local_audit = self._normalize_local_audit(local_audit or {})
        self._h3_audit = self._normalize_h3_audit(h3_audit or {})
        if h3_audit_source is not None and not callable(h3_audit_source):
            raise TypeError("h3_audit_source must be callable")
        self._h3_audit_source = h3_audit_source
        if h3_variant_contracts is not None and not isinstance(
            h3_variant_contracts, Mapping
        ):
            raise TypeError("h3_variant_contracts must be an object")
        normalized_h3_variants: dict[str, str] = {}
        for raw_variant, raw_contract in dict(h3_variant_contracts or {}).items():
            variant_id = _stable_id(raw_variant, "H3 variant_id")
            contract_id = str(raw_contract or "").strip().lower()
            if contract_id not in H3_CONTRACT_IDS:
                raise ValueError("H3 variant contract is unknown")
            if (variant_id, contract_id) not in H3_APPROVED_VARIANT_CONTRACTS:
                raise ValueError("H3 variant contract is not an approved exact lane")
            if variant_id in normalized_h3_variants:
                raise ValueError("H3 variant contract identifiers must be unique")
            normalized_h3_variants[variant_id] = contract_id
        self._h3_variant_contracts = normalized_h3_variants
        if webhook_secret_ref is not None and not isinstance(webhook_secret_ref, SecretReference):
            raise TypeError("webhook_secret_ref must be a SecretReference")
        self._webhook_secret_ref = webhook_secret_ref
        self._known_external_task = known_external_task or (lambda _task_id: False)
        self._router = router or DeterministicRouter()
        self._clock = clock
        self._nonce_factory = nonce_factory or (lambda: secrets.token_hex(16))
        self._plan_ttl = plan_ttl_seconds

    @staticmethod
    def _normalize_account_audit(value: Mapping[str, Any]) -> dict[str, bool]:
        _strict_keys(
            value,
            frozenset({"credential_rotated", "health_verified", "pricing_verified"}),
            "account audit",
        )
        if any(not isinstance(item, bool) for item in value.values()):
            raise ValueError("account audit gates must be booleans")
        return {
            "credential_rotated": value.get("credential_rotated") is True,
            "health_verified": value.get("health_verified") is True,
            "pricing_verified": value.get("pricing_verified") is True,
        }

    @staticmethod
    def _normalize_local_audit(value: Mapping[str, Any]) -> dict[str, Any]:
        _strict_keys(
            value,
            frozenset({"ready", "version", "endpoint_scope", "node_count"}),
            "local audit",
        )
        ready = value.get("ready", False)
        if not isinstance(ready, bool):
            raise ValueError("local audit ready must be boolean")
        node_count = value.get("node_count")
        if node_count is not None and (
            not isinstance(node_count, int) or isinstance(node_count, bool) or node_count < 0
        ):
            raise ValueError("local audit node_count is invalid")
        version = value.get("version")
        if version is not None and (not isinstance(version, str) or len(version) > 80):
            raise ValueError("local audit version is invalid")
        endpoint_scope = value.get("endpoint_scope", "loopback")
        if endpoint_scope != "loopback":
            raise ValueError("local provider endpoint must remain loopback-only")
        return {
            "ready": ready,
            "version": version,
            "endpoint_scope": "loopback",
            "node_count": node_count,
        }

    @staticmethod
    def _normalize_h3_audit(value: Mapping[str, Any]) -> dict[str, Any]:
        _strict_keys(
            value,
            frozenset(
                {
                    "license_blocked",
                    "authorization_recorded",
                    "runtime_ready",
                    "models_ready",
                    "contracts",
                }
            ),
            "H3 audit",
        )
        scalar_values = {
            key: item for key, item in value.items() if key != "contracts"
        }
        if any(not isinstance(item, bool) for item in scalar_values.values()):
            raise ValueError("H3 audit gates must be booleans")
        raw_contracts = value.get("contracts", {})
        if not isinstance(raw_contracts, Mapping):
            raise ValueError("H3 contract audit must be an object")
        if set(raw_contracts) - H3_CONTRACT_IDS:
            raise ValueError("H3 contract audit contains an unknown contract")
        if any(not isinstance(item, bool) for item in raw_contracts.values()):
            raise ValueError("H3 contract audit gates must be booleans")
        authorized = value.get("authorization_recorded") is True
        license_blocked = value.get("license_blocked", not authorized) is not False
        if not authorized:
            license_blocked = True
        return {
            "license_blocked": license_blocked,
            "authorization_recorded": authorized,
            "runtime_ready": value.get("runtime_ready") is True,
            "models_ready": value.get("models_ready") is True,
            "contracts": {
                contract_id: raw_contracts.get(contract_id) is True
                for contract_id in sorted(H3_CONTRACT_IDS)
            },
        }

    @classmethod
    def _closed_h3_audit(cls) -> dict[str, Any]:
        return cls._normalize_h3_audit({})

    def _current_h3_audit(self) -> dict[str, Any]:
        if self._h3_audit_source is None:
            return {
                **self._h3_audit,
                "contracts": dict(self._h3_audit["contracts"]),
            }
        try:
            supplied = self._h3_audit_source()
            if not isinstance(supplied, Mapping):
                raise ValueError("H3 audit source did not return an object")
            # A dynamic source is authoritative only when it names the complete
            # locked contract set.  Partial snapshots must not open one lane
            # while silently omitting another.
            contracts = supplied.get("contracts")
            if not isinstance(contracts, Mapping) or set(contracts) != H3_CONTRACT_IDS:
                raise ValueError("H3 audit source is incomplete")
            return self._normalize_h3_audit(supplied)
        except Exception:
            return self._closed_h3_audit()

    @staticmethod
    def _require_actor(actor: ActorContext, *, elevated: bool = False) -> ActorContext:
        if not isinstance(actor, ActorContext):
            raise ProviderServiceError(
                "authentication_required", "An authenticated Castle identity is required.", status=401
            )
        if elevated and actor.role not in _ELEVATED_ROLES:
            raise ProviderServiceError(
                "administrator_required",
                "This provider operation requires an administrator or operator.",
                status=403,
            )
        return actor

    def _accessible_accounts(
        self,
        actor: ActorContext,
        provider_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return only accounts the authenticated actor may actually use."""

        try:
            return tuple(
                self._store.list_accessible_provider_accounts(
                    actor.user_id,
                    provider_id=provider_id,
                    include_disabled=True,
                )
            )
        except (ValueError, ProviderFabricStoreError) as error:
            raise ProviderServiceError(
                "provider_inventory_unavailable",
                "Provider inventory is temporarily unavailable.",
                status=503,
            ) from error

    def _owned_manual_import_accounts(
        self,
        actor: ActorContext,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return actor-owned ClipMode namespaces without treating them as executable."""

        try:
            return tuple(
                self._store.list_owned_manual_import_accounts(actor.user_id)
            )
        except (ValueError, ProviderFabricStoreError) as error:
            raise ProviderServiceError(
                "provider_inventory_unavailable",
                "Provider inventory is temporarily unavailable.",
                status=503,
            ) from error

    def _account(
        self,
        actor: ActorContext,
        provider_id: str,
    ) -> tuple[str | None, Mapping[str, Any] | None]:
        """Select one account without ever crossing the actor ACL boundary."""

        account_id = self._provider_accounts.get(provider_id)
        if account_id:
            try:
                account = self._store.get_accessible_provider_account(
                    actor.user_id,
                    account_id,
                )
            except KeyError:
                # An inaccessible configured identifier must not be reflected
                # into another user's inventory or plan token.
                return None, None
            except (ValueError, ProviderFabricStoreError) as error:
                raise ProviderServiceError(
                    "provider_inventory_unavailable",
                    "Provider inventory is temporarily unavailable.",
                    status=503,
                ) from error
            if account.get("provider_id") != provider_id:
                return None, None
            return account_id, account

        discovered = self._accessible_accounts(actor, provider_id)
        # Multiple accounts need an explicit actor-visible binding.  Guessing
        # could spend against the wrong private balance.
        if len(discovered) != 1:
            return None, None
        account = discovered[0]
        return str(account["account_id"]), account

    def _stored_offerings(self, actor: ActorContext) -> tuple[CapabilityOffering, ...]:
        """Rehydrate actor-accessible immutable account snapshots only."""

        offerings: list[CapabilityOffering] = []
        try:
            all_accounts = self._store.list_accessible_provider_accounts(
                actor.user_id,
                include_disabled=True,
            )
        except (ValueError, ProviderFabricStoreError) as error:
            raise ProviderServiceError(
                "capability_catalog_unavailable",
                "Capability catalog is unavailable.",
                status=503,
            ) from error
        selected_accounts: list[Mapping[str, Any]] = []
        provider_ids = sorted({str(item["provider_id"]) for item in all_accounts})
        for provider_id in provider_ids:
            candidates = [
                item for item in all_accounts if item["provider_id"] == provider_id
            ]
            explicit_id = self._provider_accounts.get(provider_id)
            if explicit_id:
                selected_accounts.extend(
                    item for item in candidates if item["account_id"] == explicit_id
                )
            elif len(candidates) == 1:
                selected_accounts.append(candidates[0])
            # Multiple unbound accounts stay unavailable until an operator
            # chooses one; their snapshots must not be merged or deduplicated.
        for account in selected_accounts:
            try:
                snapshot = self._store.latest_accessible_capability_snapshot(
                    actor.user_id,
                    str(account["account_id"]),
                )
            except (KeyError, ValueError, ProviderFabricStoreError) as error:
                raise ProviderServiceError(
                    "capability_catalog_unavailable",
                    "Capability catalog is unavailable.",
                    status=503,
                ) from error
            if snapshot is None:
                continue
            for item in snapshot["offerings"]:
                try:
                    offerings.append(CapabilityOffering(
                        provider_id=str(account["provider_id"]),
                        capability=CapabilityKind(str(item["capability"])),
                        variant_id=str(item["variant_id"]),
                        visual_classes=frozenset(
                            VisualClass(str(value)) for value in item["visual_classes"]
                        ),
                        evidence=EvidenceState(str(item["evidence"])),
                        evidence_id=item.get("evidence_id"),
                        enabled=item.get("enabled") is True,
                        priority=item.get("priority", 100),
                        option_keys=frozenset(str(value) for value in item["option_keys"]),
                    ))
                except (KeyError, TypeError, ValueError) as error:
                    raise ProviderServiceError(
                        "capability_catalog_invalid",
                        "A persisted capability snapshot is invalid.",
                        status=503,
                    ) from error
        return tuple(offerings)

    def _current_autoip_readiness(
        self,
        actor: ActorContext,
        account_id: str,
    ) -> Mapping[str, Any] | None:
        """Return current owner/account technical evidence without provider I/O."""

        if self._autoip_readiness_source is None:
            return None
        try:
            receipt = self._autoip_readiness_source(actor, account_id)
        except Exception:
            return None
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("current") is not True
            or receipt.get("provider_id") not in {None, AUTOIP_PROVIDER_ID}
            or not isinstance(receipt.get("receipt_id"), str)
        ):
            return None
        return receipt

    def _provider_gate(self, actor: ActorContext, provider_id: str) -> tuple[bool, str]:
        if provider_id == "clipmode":
            return False, "needs_supported_contract"
        account_id, account = self._account(actor, provider_id)
        if account is None:
            account_count = len(self._accessible_accounts(actor, provider_id))
            if account_count > 1:
                return False, "account_selection_required"
            return False, "provider_account_not_configured"
        if account.get("enabled") is not True:
            return False, "provider_account_disabled"
        if provider_id == "local_comfy" and not self._local_audit["ready"]:
            return False, "local_runtime_not_ready"
        if provider_id == AUTOIP_PROVIDER_ID:
            try:
                self._store.credential_reference_for_actor(actor.user_id, account_id or "")
            except (KeyError, ValueError, ProviderFabricStoreError):
                return False, "credential_unavailable"
            if self._current_autoip_readiness(actor, account_id or "") is None:
                return False, "provider_compatibility_check_required"
        return True, "ready"

    def _current_offerings(
        self,
        actor: ActorContext,
        provider_constraint: str | None = None,
    ) -> tuple[CapabilityOffering, ...]:
        try:
            supplied = tuple(
                self._stored_offerings(actor)
                if self._offerings_source is None
                else self._offerings_source()
            )
        except Exception:
            raise ProviderServiceError(
                "capability_catalog_unavailable", "Capability catalog is unavailable.", status=503
            ) from None
        if any(not isinstance(item, CapabilityOffering) for item in supplied):
            raise ProviderServiceError(
                "capability_catalog_invalid", "Capability catalog is invalid.", status=503
            )
        if len({item.key for item in supplied}) != len(supplied):
            raise ProviderServiceError(
                "capability_catalog_invalid", "Capability catalog has duplicate variants.", status=503
            )
        # MiniMAX H3 is a local, pinned contract. Its exact offerings come
        # directly from the current model/node readiness snapshot rather than
        # waiting for a quality-golden catalog publication ceremony.
        if self._h3_variant_contracts:
            try:
                from .h3_profiles import h3_character_profiles

                current_h3 = self._current_h3_audit()
                ready_contracts = {
                    contract_id
                    for contract_id, ready in current_h3["contracts"].items()
                    if ready is True
                }
                dynamic_h3 = tuple(
                    CapabilityOffering(
                        provider_id=profile.provider_id,
                        capability=profile.capability,
                        variant_id=profile.variant_id,
                        visual_classes=frozenset({profile.visual_class}),
                        evidence=EvidenceState.VERIFIED,
                        evidence_id=profile.execution_evidence_id,
                        enabled=profile.execution_ready,
                        priority=20,
                        option_keys=profile.required_option_keys,
                    )
                    for profile in h3_character_profiles(ready_contracts)
                    if profile.variant_id in self._h3_variant_contracts
                )
            except Exception:
                dynamic_h3 = ()
            existing_keys = {item.key for item in supplied}
            supplied = supplied + tuple(
                item for item in dynamic_h3 if item.key not in existing_keys
            )
        effective: list[CapabilityOffering] = []
        autoip_receipt_id: str | None = None
        if provider_constraint in {None, AUTOIP_PROVIDER_ID}:
            autoip_account_id, _autoip_account = self._account(actor, AUTOIP_PROVIDER_ID)
            if autoip_account_id is not None:
                readiness = self._current_autoip_readiness(actor, autoip_account_id)
                if readiness is not None:
                    autoip_receipt_id = str(readiness["receipt_id"])
        h3_audit = self._current_h3_audit()
        h3_global_ready = (
            not h3_audit["license_blocked"]
            and h3_audit["authorization_recorded"]
            and h3_audit["models_ready"]
            and h3_audit["runtime_ready"]
        )
        for offering in supplied:
            if provider_constraint and offering.provider_id != provider_constraint:
                continue
            allowed, _reason = self._provider_gate(actor, offering.provider_id)
            if offering.provider_id == AUTOIP_PROVIDER_ID and (
                autoip_receipt_id is None
                or offering.evidence_id != autoip_receipt_id
            ):
                allowed = False
            if offering.provider_id == AUTOIP_PROVIDER_ID:
                # Older persisted readiness snapshots contain only required
                # option keys.  Enrich them from the exact pinned profile so a
                # newly selected bounded optional seed/timing value does not
                # become an artificial routing failure.
                profile = AUTOIP_PROFILE_BY_KEY.get(offering.key)
                if (
                    profile is not None
                    and profile.capability is offering.capability
                    and profile.visual_class in offering.visual_classes
                ):
                    offering = replace(
                        offering, option_keys=frozenset(profile.option_values)
                    )
            if offering.provider_id == "local_comfy" and (
                offering.variant_id in self._h3_variant_contracts
                or _H3_VARIANT_RE.search(offering.variant_id) is not None
            ):
                contract_id = self._h3_variant_contracts.get(offering.variant_id)
                if (
                    not h3_global_ready
                    or contract_id is None
                    or h3_audit["contracts"].get(contract_id) is not True
                ):
                    allowed = False
            effective.append(replace(offering, enabled=offering.enabled and allowed))
        return tuple(effective)

    def inventory(self, actor: ActorContext) -> dict[str, Any]:
        """Return stable provider readiness metadata without health network calls."""

        actor = self._require_actor(actor)
        local_accounts = self._accessible_accounts(actor, "local_comfy")
        autoip_accounts = self._accessible_accounts(actor, AUTOIP_PROVIDER_ID)
        clipmode_accounts = self._owned_manual_import_accounts(actor)
        local_account_id, local_account = self._account(actor, "local_comfy")
        autoip_account_id, autoip_account = self._account(actor, AUTOIP_PROVIDER_ID)
        configured_clipmode_id = self._provider_accounts.get("clipmode")
        if configured_clipmode_id:
            selected_clipmode = next(
                (
                    account
                    for account in clipmode_accounts
                    if account["account_id"] == configured_clipmode_id
                ),
                None,
            )
        else:
            selected_clipmode = clipmode_accounts[0] if len(clipmode_accounts) == 1 else None
        clipmode_account_id = (
            str(selected_clipmode["account_id"])
            if selected_clipmode is not None
            else None
        )
        if not local_accounts:
            local_status = "account_not_configured"
        elif local_account is None:
            local_status = "account_selection_required"
        elif local_account.get("enabled") is not True:
            local_status = "configured_disabled"
        elif not self._local_audit["ready"]:
            local_status = "runtime_not_ready"
        else:
            local_status = "ready"
        autoip_configured = bool(autoip_accounts)
        autoip_enabled = bool(autoip_account and autoip_account.get("enabled") is True)
        autoip_credential_accessible = False
        if autoip_account_id and autoip_account is not None:
            try:
                self._store.credential_reference_for_actor(
                    actor.user_id,
                    autoip_account_id,
                )
                autoip_credential_accessible = True
            except (KeyError, ValueError, ProviderFabricStoreError):
                autoip_credential_accessible = False
        if not autoip_configured:
            autoip_status = "needs_credential"
        elif autoip_account is None:
            autoip_status = "account_selection_required"
        elif not autoip_enabled:
            autoip_status = "configured_disabled"
        elif not autoip_credential_accessible:
            autoip_status = "credential_unavailable"
        elif self._current_autoip_readiness(actor, autoip_account_id or "") is None:
            autoip_status = "compatibility_check_required"
        else:
            autoip_status = "ready"
        h3_audit = self._current_h3_audit()
        h3_ready = (
            not h3_audit["license_blocked"]
            and h3_audit["authorization_recorded"]
            and h3_audit["runtime_ready"]
            and h3_audit["models_ready"]
            and all(h3_audit["contracts"].values())
        )
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "providers": [
                {
                    "provider_id": "local_comfy",
                    "status": local_status,
                    "account_id": local_account_id,
                    "account_count": len(local_accounts),
                    "configured": bool(local_accounts),
                    "execution_enabled": local_status == "ready",
                    "runtime": dict(self._local_audit),
                },
                {
                    "provider_id": AUTOIP_PROVIDER_ID,
                    "status": autoip_status,
                    "account_id": autoip_account_id,
                    "account_count": len(autoip_accounts),
                    "configured": autoip_configured,
                    "execution_enabled": autoip_status == "ready",
                    "credential_readback": False,
                    "live_probe_performed": False,
                },
                {
                    "provider_id": "clipmode",
                    "status": (
                        "offline_import_ready" if clipmode_accounts else "offline_export_required"
                    ),
                    "account_id": clipmode_account_id,
                    "account_count": len(clipmode_accounts),
                    "configured": bool(clipmode_accounts),
                    "execution_enabled": False,
                    "generation": "disabled",
                    "sync": "disabled",
                    "import_mode": "manual_import_only",
                    "import_contract": "castle.clipmode-export.v1",
                    "import_capabilities": [
                        "characters", "profile_photos", "media_versions", "lineage",
                    ],
                },
            ],
            "features": {
                "minimax_h3": {
                    "status": "ready" if h3_ready else (
                        "license_blocked"
                        if h3_audit["license_blocked"]
                        else "models_not_ready"
                        if not h3_audit["models_ready"]
                        else "runtime_not_ready"
                        if not h3_audit["runtime_ready"]
                        else "contract_not_ready"
                    ),
                    "execution_enabled": h3_ready,
                    **h3_audit,
                }
            },
            "transport_dispatched": False,
        }

    def provision_credential(self, actor: ActorContext, value: Mapping[str, Any]) -> dict[str, Any]:
        """Provision an AutoIP key and register its account disabled."""

        self._require_actor(actor, elevated=True)
        try:
            request = dict(_safe_mapping(value, "credential request"))
        finally:
            value = {}
        credential = request.pop("credential_value", None)
        try:
            _strict_keys(
                request,
                frozenset(
                    {
                        "schema_version",
                        "provider_id",
                        "account_id",
                        "display_name",
                        "api_base",
                    }
                ),
                "credential request",
            )
            _schema_version(request)
            provider_id = str(request.get("provider_id", "")).strip().lower()
            if provider_id != AUTOIP_PROVIDER_ID:
                code = (
                    "clipmode_generation_disabled"
                    if provider_id == "clipmode"
                    else "provider_not_supported"
                )
                raise ProviderServiceError(
                    code, "This provider has no supported credential contract."
                )
            try:
                account_id = _stable_id(request.get("account_id"), "account_id")
            except ValueError as error:
                raise ProviderServiceError("schema_invalid", "account_id is invalid.") from error
            display_name = request.get("display_name")
            if (
                not isinstance(display_name, str)
                or not display_name.strip()
                or len(display_name) > 160
            ):
                raise ProviderServiceError("schema_invalid", "display_name is invalid.")
            api_base = request.get("api_base", AUTOIP_API_BASE)
            if api_base != AUTOIP_API_BASE:
                raise ProviderServiceError(
                    "provider_contract_invalid", "AutoIP must use its canonical HTTPS API base."
                )
            if not isinstance(credential, (str, bytes)):
                raise ProviderServiceError("schema_invalid", "credential_value is invalid.")
            try:
                planned_reference = self._vault.reference_for(provider_id, account_id)
                # Reserve ownership and the exact final locator before the
                # first credential byte reaches the vault.  Pending accounts
                # are disabled and cannot resolve credentials or route work.
                self._store.reserve_owned_provider_account_credential(
                    actor.user_id,
                    account_id,
                    provider_id,
                    display_name.strip(),
                    planned_reference,
                    api_base=AUTOIP_API_BASE,
                )
            except StoreConflictError:
                # Cross-owner, global collisions, configured accounts, and
                # incompatible reservations intentionally share one result.
                # Crucially, this is returned before any vault I/O.
                raise ProviderServiceError(
                    "account_unavailable",
                    "The provider account is unavailable for credential provisioning.",
                    status=409,
                ) from None
            except (ProviderFabricStoreError, KeyError, TypeError, ValueError):
                raise ProviderServiceError(
                    "credential_registration_failed",
                    "Credential account reservation failed safely.",
                    status=503,
                ) from None
            try:
                result = self._vault.provision(provider_id, account_id, credential)
            except CredentialAlreadyConfigured:
                # A locator alone cannot prove that existing bytes came from
                # this reservation or match this request.  Keep the pending
                # account fail-closed until an explicit recovery flow can use
                # a durable vault-write receipt without reading the secret.
                raise ProviderServiceError(
                    "account_unavailable",
                    "The provider account is unavailable for credential provisioning.",
                    status=409,
                ) from None
            except (CredentialVaultError, TypeError, ValueError):
                raise ProviderServiceError(
                    "credential_rejected", "Credential could not be stored safely."
                ) from None
            if result.reference != planned_reference:
                raise ProviderServiceError(
                    "credential_registration_failed",
                    "Credential reference finalization failed safely.",
                    status=503,
                )
            try:
                account = self._store.finalize_owned_provider_account_credential(
                    actor.user_id,
                    account_id,
                    result.reference,
                )
            except (StoreConflictError, KeyError):
                # Missing and cross-owner finalization are deliberately
                # indistinguishable.  Never delete here: another request may
                # have completed the durable reservation while this one ran.
                raise ProviderServiceError(
                    "credential_registration_failed",
                    "Credential account finalization failed safely.",
                    status=503,
                ) from None
            except (ProviderFabricStoreError, TypeError, ValueError):
                raise ProviderServiceError(
                    "credential_registration_failed",
                    "Credential account finalization failed safely.",
                    status=503,
                ) from None
        finally:
            credential = b""
            request = {}
        self._account_audits[account_id] = self._normalize_account_audit({})
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "provider_id": provider_id,
            "account": account,
            "credential": result.to_public_dict(),
            "status": "configured_disabled",
            "performed_by": actor.user_id,
        }

    def rotate_credential(self, actor: ActorContext, value: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically rotate a key, then disable execution pending fresh probes."""

        self._require_actor(actor, elevated=True)
        try:
            request = dict(_safe_mapping(value, "rotation request"))
        finally:
            value = {}
        credential = request.pop("credential_value", None)
        try:
            _strict_keys(
                request,
                frozenset({"schema_version", "provider_id", "account_id"}),
                "rotation request",
            )
            _schema_version(request)
            provider_id = str(request.get("provider_id", "")).strip().lower()
            if provider_id != AUTOIP_PROVIDER_ID:
                raise ProviderServiceError(
                    "provider_not_supported", "This provider has no supported rotation contract."
                )
            try:
                account_id = _stable_id(request.get("account_id"), "account_id")
                account = self._store.get_accessible_provider_account(
                    actor.user_id,
                    account_id,
                )
            except (ValueError, KeyError) as error:
                raise ProviderServiceError(
                    "provider_account_not_configured", "Provider account is missing."
                ) from error
            if account.get("provider_id") != provider_id:
                raise ProviderServiceError(
                    "provider_account_mismatch", "Provider account does not match."
                )
            try:
                existing_reference = self._store.credential_reference_for_actor(
                    actor.user_id, account_id
                )
            except (KeyError, ValueError, ProviderFabricStoreError) as error:
                raise ProviderServiceError(
                    "credential_not_configured",
                    "Credential is not configured.",
                    status=409,
                ) from error
            if not isinstance(credential, (str, bytes)):
                raise ProviderServiceError("schema_invalid", "credential_value is invalid.")
            # Disable first.  If vault rotation or the final metadata update
            # fails, no worker can use an untested credential version.
            try:
                self._store.set_owned_provider_account_enabled(
                    actor.user_id,
                    account_id,
                    False,
                )
            except Exception:
                raise ProviderServiceError(
                    "provider_disable_failed",
                    "Credential rotation did not start because the account could not be disabled.",
                    status=503,
                ) from None
            try:
                result = self._vault.rotate(provider_id, account_id, credential)
            except CredentialNotConfigured:
                raise ProviderServiceError(
                    "credential_not_configured",
                    "Credential is not configured.",
                    status=409,
                ) from None
            except (CredentialVaultError, TypeError, ValueError):
                raise ProviderServiceError(
                    "credential_rejected", "Credential could not be rotated safely."
                ) from None
        finally:
            credential = b""
            request = {}
        if result.reference != existing_reference:
            raise ProviderServiceError(
                "credential_metadata_update_failed",
                "Credential was rotated but its durable account binding did not match.",
                status=503,
            )
        try:
            updated = self._store.update_owned_provider_account(
                actor.user_id,
                account_id,
                api_base=account.get("api_base") or AUTOIP_API_BASE,
                enabled=False,
            )
        except Exception:
            raise ProviderServiceError(
                "credential_metadata_update_failed",
                "Credential was rotated but remains disabled because account metadata could not be updated.",
                status=503,
            ) from None
        self._account_audits[account_id] = self._normalize_account_audit({})
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "provider_id": provider_id,
            "account": updated,
            "credential": result.to_public_dict(),
            "status": "rotated_disabled_pending_health_check",
            "performed_by": actor.user_id,
        }

    def disable_account(self, actor: ActorContext, value: Mapping[str, Any]) -> dict[str, Any]:
        """Disable new routing while retaining the write-only credential."""

        self._require_actor(actor, elevated=True)
        request = _safe_mapping(value, "disable request")
        _strict_keys(
            request,
            frozenset({"schema_version", "provider_id", "account_id", "confirmation"}),
            "disable request",
        )
        _schema_version(request)
        if request.get("confirmation") != "disable":
            raise ProviderServiceError(
                "explicit_confirmation_required", "Account disable requires explicit confirmation."
            )
        try:
            provider_id = _stable_id(request.get("provider_id"), "provider_id")
            account_id = _stable_id(request.get("account_id"), "account_id")
            if provider_id != AUTOIP_PROVIDER_ID:
                raise ProviderServiceError(
                    "provider_not_supported",
                    "This provider has no supported browser disable contract.",
                )
            account = self._store.get_accessible_provider_account(
                actor.user_id,
                account_id,
            )
        except ProviderServiceError:
            raise
        except (ValueError, KeyError) as error:
            raise ProviderServiceError("provider_account_not_configured", "Provider account is missing.") from error
        if account.get("provider_id") != provider_id:
            raise ProviderServiceError("provider_account_mismatch", "Provider account does not match.")
        try:
            updated = self._store.set_owned_provider_account_enabled(
                actor.user_id,
                account_id,
                False,
            )
        except (ProviderFabricStoreError, StoreConflictError, ValueError) as error:
            raise ProviderServiceError(
                "provider_disable_failed",
                "Provider account could not be disabled safely.",
                status=503,
            ) from error
        self._account_audits[account_id] = self._normalize_account_audit({})
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "provider_id": provider_id,
            "account": updated,
            "status": "disabled",
            "credential_deleted": False,
            "performed_by": actor.user_id,
        }

    def _resolve_quote(
        self,
        actor: ActorContext,
        account: Mapping[str, Any],
        parsed: _ParsedPlan,
        decision: RouteDecision,
    ) -> CostQuote:
        provider_id = decision.selected_provider_id
        if not provider_id:
            raise ProviderServiceError("no_provider_eligible", "No eligible provider route exists.")
        if account.get("provider_id") != provider_id or account.get("enabled") is not True:
            raise ProviderServiceError(
                "provider_account_disabled",
                "Selected provider account is not execution-ready.",
            )
        if provider_id == AUTOIP_PROVIDER_ID:
            try:
                self._store.credential_reference_for_actor(
                    actor.user_id,
                    str(account["account_id"]),
                )
            except (KeyError, ValueError, ProviderFabricStoreError) as error:
                raise ProviderServiceError(
                    "credential_unavailable",
                    "The selected provider credential is unavailable.",
                ) from error
        quote: CostQuote | None = None
        if self._quote_source is not None:
            try:
                quote = self._quote_source(
                    actor,
                    account,
                    parsed.intent,
                    decision,
                    parsed.options,
                )
            except Exception:
                raise ProviderServiceError(
                    "quote_unavailable", "A current provider quote is unavailable.", status=503
                ) from None
        if quote is None and provider_id == "local_comfy":
            quote = CostQuote(
                provider_id="local_comfy",
                capability=parsed.intent.capability,
                credits=0,
                remaining_credits=None,
                authoritative=True,
                quote_id="local-zero-v1",
                detail="Local execution does not consume provider credits.",
                account_id=str(account["account_id"]),
            )
        if quote is None or not isinstance(quote, CostQuote):
            code = "pricing_unavailable" if provider_id == AUTOIP_PROVIDER_ID else "quote_unavailable"
            raise ProviderServiceError(code, "A current authoritative quote is required.")
        if (
            quote.provider_id != provider_id
            or quote.capability is not parsed.intent.capability
            or quote.account_id != account.get("account_id")
        ):
            raise ProviderServiceError("quote_scope_mismatch", "Quote scope does not match the route.")
        if not quote.authoritative or quote.credits is None:
            code = "pricing_unavailable" if provider_id == AUTOIP_PROVIDER_ID else "authoritative_quote_required"
            raise ProviderServiceError(code, "A current authoritative quote is required.")
        if provider_id != AUTOIP_PROVIDER_ID:
            if parsed.max_credits is not None and quote.credits > parsed.max_credits:
                raise ProviderServiceError(
                    "spend_limit_exceeded", "The current quote exceeds the confirmed spending limit."
                )
            if quote.remaining_credits is not None and quote.credits > quote.remaining_credits:
                raise ProviderServiceError("insufficient_credits", "Provider balance is insufficient.")
        # Provider response text is never a public diagnostic surface.  Keep a
        # fixed explanation while binding the actual typed cost fields.
        return replace(
            quote,
            detail=(
                "Local execution does not consume provider credits."
                if provider_id == "local_comfy"
                else "Authoritative provider credit quote."
            ),
        )

    def _resolve_actor_resources(
        self,
        actor: ActorContext,
        intent: RouteIntent,
    ) -> RouteIntent:
        """Replace browser resource projections with owner-scoped store data.

        Only stable IDs cross the trust boundary.  Character names, external
        references, media types, content hashes, lineage, and visual classes
        all come back from the canonical store under the exact actor owner.
        Missing and cross-owner IDs intentionally share one public result.
        """

        characters_by_id: dict[str, CharacterRef] = {}
        assets_by_id: dict[str, MediaAsset] = {}
        missing_character_ids: list[str] = []
        missing_asset_ids: list[str] = []
        try:
            for supplied in intent.characters:
                try:
                    stored = self._store.get_owned_character(
                        actor.user_id,
                        supplied.character_id,
                    )
                except KeyError:
                    missing_character_ids.append(supplied.character_id)
                    continue
                external_refs: list[ExternalReference] = []
                seen_refs: set[tuple[str, str]] = set()
                for link in stored.get("provider_links", ()):
                    if not isinstance(link, Mapping):
                        raise ValueError("canonical character link is invalid")
                    try:
                        account = self._store.get_accessible_provider_account(
                            actor.user_id,
                            str(link["account_id"]),
                        )
                    except KeyError:
                        # Historical links outside the actor's account ACL are
                        # not identity material this actor may observe or use.
                        continue
                    pair = (
                        str(account["provider_id"]),
                        str(link["external_character_id"]),
                    )
                    if pair in seen_refs:
                        continue
                    seen_refs.add(pair)
                    external_refs.append(ExternalReference(*pair))
                characters_by_id[supplied.character_id] = CharacterRef(
                    character_id=str(stored["character_id"]),
                    visual_class=VisualClass(str(stored["visual_class"])),
                    display_name=stored.get("display_name"),
                    external_refs=tuple(external_refs),
                )

            for supplied in intent.assets:
                try:
                    stored = self._store.get_owned_remote_asset(
                        actor.user_id,
                        supplied.asset_id,
                    )
                except KeyError:
                    missing_asset_ids.append(supplied.asset_id)
                    continue
                character_ids = tuple(str(item) for item in stored.get("character_ids", ()))
                # Asset-character lineage is also an authorization boundary.
                # Reject a corrupt cross-owner edge rather than serializing or
                # executing it under the asset owner's authority.
                for character_id in character_ids:
                    self._store.get_owned_character(actor.user_id, character_id)
                versions = stored.get("versions", ())
                if not isinstance(versions, (list, tuple)):
                    raise ValueError("canonical asset versions are invalid")
                latest = versions[-1] if versions else {}
                if not isinstance(latest, Mapping):
                    raise ValueError("canonical asset version is invalid")
                raw_visual = stored.get("visual_class")
                metadata = stored.get("metadata")
                metadata = metadata if isinstance(metadata, Mapping) else {}
                is_autoip_history = bool(
                    str(stored.get("projection_id", "")).startswith(
                        "autoip-history:"
                    )
                    or metadata.get("source") == "autoip_history"
                    or metadata.get("classification_required") is True
                )
                if is_autoip_history:
                    classification = (
                        self._store.get_owned_remote_asset_classification(
                            actor.user_id,
                            str(stored["projection_id"]),
                        )
                    )
                    if classification is None:
                        raise ProviderServiceError(
                            "asset_classification_required",
                            "An adopted history asset must be explicitly classified before use.",
                            status=409,
                        )
                    if (
                        len(versions) != 1
                        or classification.get("projection_id")
                        != stored.get("projection_id")
                        or classification.get("asset_version_id")
                        != latest.get("version_id")
                        or classification.get("visual_class") != raw_visual
                        or tuple(classification.get("character_ids") or ())
                        != character_ids
                    ):
                        raise StoreConflictError(
                            "AutoIP history classification does not match the selected asset"
                        )
                assets_by_id[supplied.asset_id] = MediaAsset(
                    asset_id=str(stored["projection_id"]),
                    kind=MediaKind(str(stored["media_kind"])),
                    uri=f"castle://assets/{stored['projection_id']}",
                    visual_class=(
                        VisualClass(str(raw_visual))
                        if raw_visual is not None
                        else None
                    ),
                    character_ids=character_ids,
                    mime_type=latest.get("mime_type"),
                    byte_size=latest.get("byte_size"),
                    sha256=latest.get("sha256"),
                    duration_seconds=latest.get("duration_seconds"),
                )
        except ProviderFabricStoreError as error:
            raise ProviderServiceError(
                "resource_lookup_unavailable",
                "Selected Castle resources could not be verified.",
                status=503,
            ) from error
        except (TypeError, ValueError) as error:
            raise ProviderServiceError(
                "canonical_resource_invalid",
                "A selected Castle resource has invalid canonical metadata.",
                status=503,
            ) from error

        if missing_character_ids or missing_asset_ids:
            if self._canonical_resource_resolver is None:
                raise ProviderServiceError(
                    "resource_not_found",
                    "A selected Castle resource is unavailable.",
                    status=404,
                )
            try:
                resolved_characters, resolved_assets = self._canonical_resource_resolver(
                    actor,
                    tuple(missing_character_ids),
                    tuple(missing_asset_ids),
                )
                if not isinstance(resolved_characters, Mapping) or not isinstance(
                    resolved_assets, Mapping
                ):
                    raise TypeError("canonical resolver result must contain mappings")
                if set(resolved_characters) != set(missing_character_ids) or set(
                    resolved_assets
                ) != set(missing_asset_ids):
                    raise ValueError("canonical resolver changed requested identifiers")
                for resource_id, character in resolved_characters.items():
                    if not isinstance(character, CharacterRef) or character.character_id != resource_id:
                        raise ValueError("canonical resolver changed a character identifier")
                    characters_by_id[resource_id] = character
                for resource_id, asset in resolved_assets.items():
                    if not isinstance(asset, MediaAsset) or asset.asset_id != resource_id:
                        raise ValueError("canonical resolver changed an asset identifier")
                    if resource_id.startswith("autoip-history:"):
                        # AutoIP history is owned by the provider store's
                        # append-only adoption/classification graph.  A
                        # fallback resolver cannot synthesize that evidence.
                        raise ProviderServiceError(
                            "asset_classification_required",
                            "An adopted history asset must be explicitly classified before use.",
                            status=409,
                        )
                    assets_by_id[resource_id] = asset
            except KeyError as error:
                raise ProviderServiceError(
                    "resource_not_found",
                    "A selected Castle resource is unavailable.",
                    status=404,
                ) from error
            except ProviderServiceError:
                raise
            except (TypeError, ValueError) as error:
                raise ProviderServiceError(
                    "canonical_resource_invalid",
                    "A selected Castle resource has invalid canonical metadata.",
                    status=503,
                ) from error
            except Exception as error:
                raise ProviderServiceError(
                    "resource_lookup_unavailable",
                    "Selected Castle resources could not be verified.",
                    status=503,
                ) from error

        characters = [characters_by_id[item.character_id] for item in intent.characters]
        assets = [assets_by_id[item.asset_id] for item in intent.assets]

        if any(character.visual_class is not intent.visual_class for character in characters):
            raise ProviderServiceError(
                "visual_class_mismatch",
                "A selected character belongs to a different visual class.",
            )
        if any(
            asset.visual_class is not None and asset.visual_class is not intent.visual_class
            for asset in assets
        ):
            raise ProviderServiceError(
                "visual_class_mismatch",
                "An identity-bearing asset belongs to a different visual class.",
            )
        try:
            return RouteIntent(
                capability=intent.capability,
                visual_class=intent.visual_class,
                characters=tuple(characters),
                assets=tuple(assets),
                required_options=intent.required_options,
                preferred_providers=intent.preferred_providers,
            )
        except (TypeError, ValueError) as error:
            raise ProviderServiceError(
                "canonical_resource_invalid",
                "A selected Castle resource has invalid canonical metadata.",
                status=503,
            ) from error

    def _validate_options(
        self,
        parsed: _ParsedPlan,
        decision: RouteDecision,
    ) -> _ParsedPlan:
        """Apply the selected variant's exact value profile without inference."""

        # Character requests already passed the exact server grant/profile
        # materializer.  A legacy validator is a weaker, variant-only hook and
        # must not rewrite or reinterpret those authoritative option values.
        if parsed.materialized is not None:
            return parsed
        if decision.selected_provider_id == AUTOIP_PROVIDER_ID:
            profile = AUTOIP_PROFILE_BY_KEY.get(
                f"{AUTOIP_PROVIDER_ID}:{decision.selected_variant_id}"
            )
            if (
                profile is None
                or profile.capability is not parsed.intent.capability
                or profile.visual_class is not parsed.intent.visual_class
                or not set(profile.required_option_keys).issubset(parsed.options)
                or not set(parsed.options).issubset(profile.option_values)
                or any(
                    value not in profile.option_values[key]
                    for key, value in parsed.options.items()
                )
            ):
                raise ProviderServiceError(
                    "option_value_invalid",
                    "One or more option values are invalid for the selected AutoIP variant.",
                )
            return parsed
        if self._option_validator is None:
            if parsed.options:
                raise ProviderServiceError(
                    "option_profile_unavailable",
                    "The selected variant has no verified option-value profile.",
                )
            return parsed
        try:
            validated = self._option_validator(parsed.intent, decision, parsed.options)
        except ProviderServiceError:
            raise
        except Exception:
            raise ProviderServiceError(
                "option_value_invalid",
                "One or more option values are invalid for the selected variant.",
            ) from None
        normalized = _parse_options(validated)
        if set(normalized) != set(parsed.options):
            raise ProviderServiceError(
                "option_profile_invalid",
                "The option validator changed the selected option keys.",
                status=503,
            )
        return _ParsedPlan(
            intent=parsed.intent,
            options=normalized,
            max_credits=parsed.max_credits,
            provider_constraint=parsed.provider_constraint,
            creative_instruction=parsed.creative_instruction,
            action_id=parsed.action_id,
            feature_id=parsed.feature_id,
            profile_key=parsed.profile_key,
            materialized=parsed.materialized,
        )

    @staticmethod
    def _validate_autoip_instruction(
        parsed: _ParsedPlan,
        decision: RouteDecision,
    ) -> None:
        """Reject optional-field drift before quote, confirmation, or queueing."""

        instruction = parsed.creative_instruction or {}
        optional_keys = {
            key for key in (
                "subject_descriptions", "dialogue_text", "negative_prompt", "seed",
            )
            if key in instruction
        }
        if decision.selected_provider_id != AUTOIP_PROVIDER_ID:
            if optional_keys:
                raise ProviderServiceError(
                    "schema_invalid",
                    "AutoIP creative instruction fields require an AutoIP route.",
                )
            return
        allowed_by_capability = {
            CapabilityKind.TEXT_TO_IMAGE: {"negative_prompt"},
            CapabilityKind.IMAGE_TO_IMAGE: set(),
            CapabilityKind.IMAGE_TO_VIDEO: {"seed"},
            CapabilityKind.TEXT_TO_VIDEO: {"seed"},
            CapabilityKind.LIP_SYNC: set(),
            CapabilityKind.DANCE: set(),
            CapabilityKind.MULTI_SUBJECT_VIDEO: {
                "subject_descriptions", "dialogue_text", "negative_prompt", "seed",
            },
        }
        if not optional_keys.issubset(allowed_by_capability[parsed.intent.capability]):
            raise ProviderServiceError(
                "schema_invalid",
                "Creative instruction fields do not match the selected AutoIP operation.",
            )
        if "seed" in instruction and instruction["seed"] != parsed.options.get("seed"):
            raise ProviderServiceError(
                "option_value_invalid",
                "Creative instruction seed differs from the selected AutoIP option.",
            )
        if parsed.intent.capability is not CapabilityKind.MULTI_SUBJECT_VIDEO:
            return
        image_count = sum(
            asset.kind is MediaKind.IMAGE for asset in parsed.intent.assets
        )
        descriptions = instruction.get("subject_descriptions")
        if isinstance(descriptions, list) and len(descriptions) > image_count:
            raise ProviderServiceError(
                "schema_invalid",
                "Subject descriptions exceed the selected AutoIP images.",
            )
        has_voice_sample = any(
            asset.kind is MediaKind.AUDIO for asset in parsed.intent.assets
        )
        if has_voice_sample and not str(instruction.get("dialogue_text") or "").strip():
            raise ProviderServiceError(
                "dialogue_required",
                "An AutoIP voice sample requires authored dialogue before queueing.",
            )

    @staticmethod
    def _needs_capability_grant(intent: RouteIntent) -> bool:
        # A verified owner asset may carry a visual-class annotation without
        # claiming any character identity.  Only an explicit character or an
        # asset-to-character link enters the character rights/grant path.
        return bool(intent.characters) or any(asset.character_ids for asset in intent.assets)

    def _materialize_character_capability(
        self,
        actor: ActorContext,
        parsed: _ParsedPlan,
    ) -> _ParsedPlan:
        """Compile identity-bearing work from current server authority only."""

        if not self._needs_capability_grant(parsed.intent):
            if any((parsed.action_id, parsed.feature_id, parsed.profile_key)):
                raise ProviderServiceError(
                    "capability_binding_not_applicable",
                    "Character capability identifiers require an exact character selection.",
                )
            return parsed
        if not parsed.intent.characters:
            raise ProviderServiceError(
                "capability_character_required",
                "An identity-bearing asset requires an exact Castle character selection.",
            )
        if not all((parsed.action_id, parsed.feature_id, parsed.profile_key)):
            raise ProviderServiceError(
                "capability_grant_required",
                "This character action requires an exact server-authorized capability grant.",
            )
        if self._capability_materializer is None:
            raise ProviderServiceError(
                "capability_grant_unavailable",
                "Character capability authorization is temporarily unavailable.",
                status=503,
            )
        try:
            materialized = self._capability_materializer(
                actor,
                parsed.intent,
                str(parsed.action_id),
                str(parsed.feature_id),
                str(parsed.profile_key),
                parsed.options,
            )
        except VariantProfileError as error:
            reason = str(error.code or "capability_not_granted")
            try:
                _stable_id(reason, "capability denial reason")
            except ValueError:
                reason = "capability_not_granted"
            raise ProviderServiceError(
                "capability_grant_denied",
                "The selected character is not authorized for this exact action and module.",
                status=403,
                reason_codes=(reason,),
            ) from error
        except KeyError as error:
            raise ProviderServiceError(
                "capability_grant_denied",
                "The selected character is not authorized for this exact action and module.",
                status=403,
            ) from error
        except Exception:
            raise ProviderServiceError(
                "capability_grant_unavailable",
                "Character capability authorization is temporarily unavailable.",
                status=503,
            ) from None
        if not isinstance(materialized, MaterializedVariantRequest):
            raise ProviderServiceError(
                "capability_grant_invalid",
                "Character capability authorization returned an invalid contract.",
                status=503,
            )
        if set(materialized.authorized_character_ids) != {
            item.character_id for item in parsed.intent.characters
        }:
            raise ProviderServiceError(
                "multi_character_grant_required",
                "Every selected character requires a current exact capability grant.",
                status=403,
            )
        profile = materialized.profile
        if (
            profile.action_id != parsed.action_id
            or profile.feature_id != parsed.feature_id
            or profile.key != parsed.profile_key
            or (
                parsed.provider_constraint is not None
                and profile.provider_id != parsed.provider_constraint
            )
        ):
            raise ProviderServiceError(
                "capability_grant_denied",
                "The selected character is not authorized for this exact action and module.",
                status=403,
                reason_codes=("capability_binding_mismatch",),
            )
        if not self._same_public(
            materialized.intent.to_dict(), parsed.intent.to_dict()
        ) or not self._same_public(dict(materialized.options), dict(parsed.options)):
            # The resolver may validate the submitted canonical IDs, but it may
            # never silently replace a character, asset, option, class, or
            # provider preference in a paid plan.
            raise ProviderServiceError(
                "capability_grant_invalid",
                "Character capability authorization changed the requested resources.",
                status=503,
            )
        return _ParsedPlan(
            intent=materialized.intent,
            options=materialized.options,
            max_credits=parsed.max_credits,
            provider_constraint=parsed.provider_constraint,
            creative_instruction=parsed.creative_instruction,
            action_id=profile.action_id,
            feature_id=profile.feature_id,
            profile_key=profile.key,
            materialized=materialized,
        )

    def _route(self, actor: ActorContext, parsed: _ParsedPlan) -> RouteDecision:
        if parsed.provider_constraint == "clipmode":
            raise ProviderServiceError(
                "clipmode_generation_disabled",
                "ClipMode generation requires a supported vendor contract; manual import only is available.",
            )
        if parsed.provider_constraint:
            allowed, reason = self._provider_gate(actor, parsed.provider_constraint)
            if not allowed:
                raise ProviderServiceError(reason, "The selected provider is not ready for execution.")
        offerings = self._current_offerings(actor, parsed.provider_constraint)
        if parsed.materialized is not None:
            profile = parsed.materialized.profile
            evidence_id = getattr(profile, "execution_evidence_id", profile.golden_id)
            offerings = tuple(
                offering
                for offering in offerings
                if offering.provider_id == profile.provider_id
                and offering.variant_id == profile.variant_id
                and offering.evidence_id == evidence_id
            )
        decision = self._router.route(parsed.intent, offerings)
        if not decision.routable:
            reasons = tuple(dict.fromkeys(item.reason_code for item in decision.rejections))
            raise ProviderServiceError(
                "no_provider_eligible",
                "No provider is eligible for this exact capability, visual class, and option set.",
                reason_codes=reasons,
            )
        return decision

    def _generation_plan_nonce(self, user_id: str, request_id: str) -> str:
        """Derive the one-click plan nonce without mutable or random state."""

        return hmac.new(
            self._plan_signing_key.reveal().encode("utf-8"),
            b"castle-generation-request-v1\0"
            + user_id.encode("utf-8")
            + b"\0"
            + request_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]

    def _sign_plan(self, payload: Mapping[str, Any]) -> str:
        encoded = _canonical_json(payload).encode("utf-8")
        signature = hmac.new(
            self._plan_signing_key.reveal().encode("utf-8"),
            encoded,
            hashlib.sha256,
        ).digest()
        return f"{_b64encode(encoded)}.{_b64encode(signature)}"

    def _verify_plan(self, token: Any) -> Mapping[str, Any]:
        if not isinstance(token, str) or not 64 <= len(token) <= 131_072 or token.count(".") != 1:
            raise ProviderServiceError("plan_token_invalid", "Plan token is invalid.")
        encoded, supplied_text = token.split(".", 1)
        payload_bytes = _b64decode(encoded)
        supplied = _b64decode(supplied_text)
        expected = hmac.new(
            self._plan_signing_key.reveal().encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).digest()
        if len(supplied) != len(expected) or not hmac.compare_digest(expected, supplied):
            raise ProviderServiceError("plan_token_invalid", "Plan token signature is invalid.")
        payload = _strict_json_object(payload_bytes)
        _strict_keys(
            payload,
            frozenset(
                {
                    "type",
                    "schema_version",
                    "plan_id",
                    "user_id",
                    "issued_at",
                    "expires_at",
                    "provider_constraint",
                    "intent",
                    "options",
                    "decision",
                    "selected_account_id",
                    "quote",
                    "spend_guard",
                    "capability_grant",
                    "creative_instruction",
                    "generation_request",
                }
            ),
            "plan token",
        )
        _assert_public_tree(payload, "plan token")
        if payload.get("type") != PLAN_TOKEN_TYPE or payload.get("schema_version") != SERVICE_SCHEMA_VERSION:
            raise ProviderServiceError("plan_token_invalid", "Plan token type is invalid.")
        issued_at = payload.get("issued_at")
        expires_at = payload.get("expires_at")
        if any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in (issued_at, expires_at)
        ):
            raise ProviderServiceError("plan_token_invalid", "Plan token time fields are invalid.")
        if expires_at - issued_at != self._plan_ttl:
            raise ProviderServiceError("plan_token_invalid", "Plan token lifetime is invalid.")
        generation_request = payload.get("generation_request")
        if generation_request is not None:
            generation_request = _safe_mapping(
                generation_request, "generation request binding"
            )
            _strict_keys(
                generation_request,
                frozenset({"request_id", "plan_hash"}),
                "generation request binding",
            )
            try:
                request_id = _stable_id(
                    generation_request.get("request_id"), "request_id"
                )
                user_id = _stable_id(payload.get("user_id"), "user_id")
            except ValueError as error:
                raise ProviderServiceError(
                    "plan_token_invalid", "Generation request identity is invalid."
                ) from error
            plan_hash = generation_request.get("plan_hash")
            if (
                generation_request.get("request_id") != request_id
                or payload.get("user_id") != user_id
                or not isinstance(plan_hash, str)
                or _SHA256_RE.fullmatch(plan_hash) is None
                or payload.get("plan_id")
                != f"plan:{self._generation_plan_nonce(user_id, request_id)}"
            ):
                raise ProviderServiceError(
                    "plan_token_invalid", "Generation request binding is invalid."
                )
        return payload

    def _assert_plan_fresh(self, payload: Mapping[str, Any]) -> None:
        """Reject a valid signed plan that can no longer create new durable work."""

        issued_at = int(payload["issued_at"])
        expires_at = int(payload["expires_at"])
        now = int(self._clock())
        if issued_at > now + 5:
            raise ProviderServiceError("plan_token_invalid", "Plan token was issued in the future.")
        if expires_at <= now:
            raise ProviderServiceError("plan_token_expired", "Plan token has expired.", status=409)

    def plan_intent(self, actor: ActorContext, value: Mapping[str, Any]) -> dict[str, Any]:
        """Plan an exact route and quote without persistence or provider I/O."""

        return self._plan_intent(actor, value)

    def _plan_intent(
        self,
        actor: ActorContext,
        value: Mapping[str, Any],
        *,
        plan_nonce: str | None = None,
        generation_request: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Internal planner with an optional server-derived generation binding."""

        actor = self._require_actor(actor)
        parsed = _parse_plan_request(value)
        parsed = _ParsedPlan(
            intent=self._resolve_actor_resources(actor, parsed.intent),
            options=parsed.options,
            max_credits=parsed.max_credits,
            provider_constraint=parsed.provider_constraint,
            creative_instruction=parsed.creative_instruction,
            action_id=parsed.action_id,
            feature_id=parsed.feature_id,
            profile_key=parsed.profile_key,
        )
        parsed = self._materialize_character_capability(actor, parsed)
        decision = self._route(actor, parsed)
        parsed = self._validate_options(parsed, decision)
        self._validate_autoip_instruction(parsed, decision)
        account_id, account = self._account(actor, str(decision.selected_provider_id))
        if account_id is None or account is None or account.get("enabled") is not True:
            raise ProviderServiceError(
                "provider_account_disabled", "Selected provider account is not execution-ready."
            )
        quote = self._resolve_quote(actor, account, parsed, decision)
        now = int(self._clock())
        raw_nonce = str(
            self._nonce_factory() if plan_nonce is None else plan_nonce
        ).lower()
        if not _PLAN_NONCE_RE.fullmatch(raw_nonce):
            raise ProviderServiceError(
                "plan_id_unavailable", "A secure plan identifier could not be allocated.", status=503
            )
        plan_id = f"plan:{raw_nonce}"
        spend_guard = {
            # AutoIP account policy is provider-owned.  Castle records the
            # authoritative quote but does not impose a local usage ceiling.
            "max_credits": (
                None
                if decision.selected_provider_id == AUTOIP_PROVIDER_ID
                else parsed.max_credits
            ),
            "quoted_credits": quote.credits,
            "currency": "provider_credits",
        }
        capability_grant = (
            parsed.materialized.to_capability_binding()
            if parsed.materialized is not None
            else None
        )
        payload = {
            "type": PLAN_TOKEN_TYPE,
            "schema_version": SERVICE_SCHEMA_VERSION,
            "plan_id": plan_id,
            "user_id": actor.user_id,
            "issued_at": now,
            "expires_at": now + self._plan_ttl,
            "provider_constraint": parsed.provider_constraint,
            "intent": parsed.intent.to_dict(),
            "options": dict(parsed.options),
            "decision": decision.to_dict(),
            "selected_account_id": account_id,
            "quote": quote.to_dict(),
            "spend_guard": spend_guard,
            "capability_grant": capability_grant,
            "creative_instruction": parsed.creative_instruction,
        }
        if generation_request is not None:
            payload["generation_request"] = dict(generation_request)
        token = self._sign_plan(payload)
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "plan_id": plan_id,
            "expires_at": payload["expires_at"],
            "intent": payload["intent"],
            "options": payload["options"],
            "decision": payload["decision"],
            "selected_account_id": account_id,
            "quote": payload["quote"],
            "spend_guard": spend_guard,
            "capability_grant": capability_grant,
            "creative_instruction": parsed.creative_instruction,
            "plan_token": token,
            "confirmation_required": True,
            "provider_write_performed": False,
            "credits_spent": 0,
        }

    @staticmethod
    def _same_public(left: Any, right: Any) -> bool:
        return _canonical_json(left) == _canonical_json(right)

    @classmethod
    def _same_executable_quote(cls, left: CostQuote, right: CostQuote) -> bool:
        if left.provider_id != AUTOIP_PROVIDER_ID or right.provider_id != AUTOIP_PROVIDER_ID:
            return cls._same_public(left.to_dict(), right.to_dict())
        # Remaining balance is telemetry and may legitimately lag/change on an
        # active or unlimited account.  Pricing identity and charge remain
        # immutable for the signed request.
        return (
            left.provider_id == right.provider_id
            and left.capability is right.capability
            and left.credits == right.credits
            and left.authoritative is True
            and right.authoritative is True
            and left.quote_id == right.quote_id
            and left.account_id == right.account_id
        )

    @staticmethod
    def _confirmation_storage_parts(payload: Mapping[str, Any]) -> dict[str, Any]:
        suffix = hashlib.sha256(str(payload["plan_id"]).encode("utf-8")).hexdigest()[:32]
        persistent_payload = {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "plan_id": payload["plan_id"],
            "owner_user_id": payload["user_id"],
            "options": payload["options"],
            "spend_guard": payload["spend_guard"],
            "provider_constraint": payload["provider_constraint"],
            "capability_grant": payload.get("capability_grant"),
            "creative_instruction": payload.get("creative_instruction"),
        }
        if payload.get("generation_request") is not None:
            persistent_payload["generation_request"] = payload["generation_request"]
        return {
            "intent_id": f"intent:{suffix}",
            "decision_id": f"route:{suffix}",
            "outbox_id": f"outbox:{suffix}",
            "client_request_id": f"request:{suffix}",
            "request_key": f"plan:{suffix}",
            "persistent_payload": persistent_payload,
        }

    @staticmethod
    def _confirmation_result(
        payload: Mapping[str, Any],
        parts: Mapping[str, Any],
        bundle: Mapping[str, Any],
        accepted_credits: int,
    ) -> dict[str, Any]:
        stored_outbox = bundle["outbox"]
        created_at = stored_outbox["created_at"]
        # Confirmation acknowledges durable creation; it is not a live status
        # read.  Workers may claim or submit immediately after the transaction,
        # so returning mutable outbox fields would make an exact retry differ
        # from the response the caller originally received.  Status polling is
        # the sole contract for observing later execution state.
        confirmation_outbox = {
            "outbox_id": parts["outbox_id"],
            "intent_id": parts["intent_id"],
            "decision_id": parts["decision_id"],
            "account_id": str(payload["selected_account_id"]),
            "client_request_id": parts["client_request_id"],
            "state": "pending",
            "attempt_count": 0,
            "leased": False,
            "created_at": created_at,
            "updated_at": created_at,
        }
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "plan_id": payload["plan_id"],
            "intent_id": parts["intent_id"],
            "decision_id": parts["decision_id"],
            "outbox": confirmation_outbox,
            "client_request_id": parts["client_request_id"],
            "state": "pending",
            "credits_authorized": accepted_credits,
            "credits_spent": 0,
            "transport_dispatched": False,
            "next_action": "provider_worker_claim_outbox",
        }

    def _find_confirmation_replay(
        self,
        actor: ActorContext,
        payload: Mapping[str, Any],
        intent: RouteIntent,
        decision: RouteDecision,
        quote: CostQuote,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        parts = self._confirmation_storage_parts(payload)
        try:
            bundle = self._store.find_exact_owned_confirmation(
                actor.user_id,
                parts["intent_id"],
                parts["request_key"],
                intent,
                parts["decision_id"],
                decision,
                str(payload["selected_account_id"]),
                parts["outbox_id"],
                parts["client_request_id"],
                payload=parts["persistent_payload"],
                quote=quote.to_dict(),
                request_payload=parts["persistent_payload"],
            )
        except StoreConflictError as error:
            raise ProviderServiceError(
                "confirmation_conflict",
                "A prior confirmation has different immutable route data.",
                status=409,
            ) from error
        except (ProviderFabricStoreError, KeyError, ValueError) as error:
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior confirmation could not be read safely.",
                status=503,
            ) from error
        if bundle is None:
            return None
        return parts, bundle

    def _persist_confirmation(
        self,
        actor: ActorContext,
        payload: Mapping[str, Any],
        intent: RouteIntent,
        decision: RouteDecision,
        quote: CostQuote,
    ) -> dict[str, Any]:
        parts = self._confirmation_storage_parts(payload)
        try:
            bundle = self._store.persist_owned_confirmation(
                actor.user_id,
                parts["intent_id"],
                parts["request_key"],
                intent,
                parts["decision_id"],
                decision,
                str(payload["selected_account_id"]),
                parts["outbox_id"],
                parts["client_request_id"],
                payload=parts["persistent_payload"],
                quote=quote.to_dict(),
                request_payload=parts["persistent_payload"],
            )
        except ProviderServiceError:
            raise
        except StoreConflictError as error:
            raise ProviderServiceError(
                "confirmation_conflict",
                "A prior confirmation has different immutable route data.",
                status=409,
            ) from error
        except (ProviderFabricStoreError, KeyError, ValueError) as error:
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The confirmed request was not dispatched because durable persistence failed.",
                status=503,
            ) from error
        return {
            "intent_id": parts["intent_id"],
            "decision_id": parts["decision_id"],
            "outbox": bundle["outbox"],
            "client_request_id": parts["client_request_id"],
        }

    def confirm_intent(self, actor: ActorContext, value: Mapping[str, Any]) -> dict[str, Any]:
        """Revalidate a signed plan and durably queue it without transport I/O."""

        actor = self._require_actor(actor)
        request = _safe_mapping(value, "confirmation request")
        _strict_keys(
            request,
            frozenset({"schema_version", "plan_token", "confirmed", "accepted_credits"}),
            "confirmation request",
        )
        _schema_version(request)
        if request.get("confirmed") is not True:
            raise ProviderServiceError(
                "explicit_confirmation_required", "Generation requires explicit confirmation."
            )
        accepted_credits = request.get("accepted_credits")
        if not isinstance(accepted_credits, int) or isinstance(accepted_credits, bool) or accepted_credits < 0:
            raise ProviderServiceError(
                "schema_invalid", "accepted_credits must be a non-negative integer."
            )
        payload = self._verify_plan(request.get("plan_token"))
        if payload.get("user_id") != actor.user_id:
            raise ProviderServiceError(
                "plan_user_mismatch", "Plan token belongs to another Castle user.", status=403
            )
        try:
            _stable_id(payload.get("plan_id"), "plan_id")
            _stable_id(payload.get("selected_account_id"), "selected_account_id")
        except ValueError as error:
            raise ProviderServiceError("plan_token_invalid", "Plan identity fields are invalid.") from error
        intent = _intent_from_mapping(payload.get("intent"))
        creative_instruction = _parse_creative_instruction(
            payload.get("creative_instruction"), signed=True
        )
        options = _parse_options(payload.get("options"))
        if frozenset(options) != intent.required_options:
            raise ProviderServiceError("plan_token_invalid", "Plan options do not match the intent.")
        signed_capability_grant = _capability_binding_from_token(
            payload.get("capability_grant"),
            required=self._needs_capability_grant(intent),
        )
        spend_guard = _safe_mapping(payload.get("spend_guard"), "spend guard")
        _strict_keys(
            spend_guard,
            frozenset({"max_credits", "quoted_credits", "currency"}),
            "spend guard",
        )
        if spend_guard.get("currency") != "provider_credits":
            raise ProviderServiceError("plan_token_invalid", "Plan spend currency is invalid.")
        maximum = spend_guard.get("max_credits")
        if maximum is not None and (
            not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0
        ):
            raise ProviderServiceError("plan_token_invalid", "Plan spend limit is invalid.")
        constraint = payload.get("provider_constraint")
        if constraint is not None:
            try:
                constraint = _stable_id(constraint, "provider_constraint")
            except ValueError as error:
                raise ProviderServiceError("plan_token_invalid", "Plan provider constraint is invalid.") from error
        signed_decision = _decision_from_mapping(payload.get("decision"))
        if not signed_decision.routable:
            raise ProviderServiceError("plan_token_invalid", "Plan route is not executable.")
        signed_quote = _quote_from_mapping(payload.get("quote"))
        if (
            signed_quote.account_id != payload.get("selected_account_id")
            or signed_quote.provider_id != signed_decision.selected_provider_id
            or signed_quote.capability is not intent.capability
            or signed_quote.credits is None
        ):
            raise ProviderServiceError(
                "plan_token_invalid",
                "Plan quote scope is inconsistent.",
            )
        if spend_guard.get("quoted_credits") != signed_quote.credits:
            raise ProviderServiceError("plan_token_invalid", "Plan spend guard is inconsistent.")
        if accepted_credits != signed_quote.credits:
            raise ProviderServiceError(
                "credit_confirmation_mismatch", "Accepted credits do not match the signed quote.", status=409
            )
        if (
            signed_quote.provider_id != AUTOIP_PROVIDER_ID
            and maximum is not None
            and accepted_credits > maximum
        ):
            raise ProviderServiceError(
                "spend_limit_exceeded", "Accepted credits exceed the signed spending limit.", status=409
            )

        # A confirmed plan is an idempotency key.  Once its exact immutable
        # bundle exists, mutable catalog, routing, account, and pricing state
        # must not turn a lost-response retry into a new confirmation attempt.
        replay = self._find_confirmation_replay(
            actor,
            payload,
            intent,
            signed_decision,
            signed_quote,
        )
        if replay is not None:
            parts, bundle = replay
            return self._confirmation_result(
                payload,
                parts,
                bundle,
                accepted_credits,
            )

        # Freshness protects creation of new work, not an exact read of work
        # already persisted under this signed plan's idempotency key.  Keeping
        # this check after replay makes a lost-response retry durable across
        # the token TTL without allowing an expired token to enqueue anything.
        self._assert_plan_fresh(payload)

        try:
            current_intent = self._resolve_actor_resources(actor, intent)
        except ProviderServiceError as error:
            raise ProviderServiceError(
                "resource_changed",
                "Selected Castle resources changed after this plan was issued.",
                status=409,
            ) from error
        if not self._same_public(current_intent.to_dict(), intent.to_dict()):
            raise ProviderServiceError(
                "resource_changed",
                "Selected Castle resources changed after this plan was issued.",
                status=409,
            )
        intent = current_intent
        parsed = _ParsedPlan(
            intent=intent,
            options=options,
            max_credits=maximum,
            provider_constraint=constraint,
            creative_instruction=creative_instruction,
            action_id=(
                signed_capability_grant["action_id"]
                if signed_capability_grant is not None
                else None
            ),
            feature_id=(
                signed_capability_grant["feature_id"]
                if signed_capability_grant is not None
                else None
            ),
            profile_key=(
                signed_capability_grant["profile_key"]
                if signed_capability_grant is not None
                else None
            ),
        )
        try:
            parsed = self._materialize_character_capability(actor, parsed)
        except ProviderServiceError as error:
            raise ProviderServiceError(
                "capability_grant_changed",
                "Character capability authorization changed after this plan was issued.",
                status=409,
            ) from error
        current_capability_grant = (
            parsed.materialized.to_capability_binding()
            if parsed.materialized is not None
            else None
        )
        if not self._same_public(current_capability_grant, signed_capability_grant):
            raise ProviderServiceError(
                "capability_grant_changed",
                "Character capability authorization changed after this plan was issued.",
                status=409,
            )
        try:
            current_decision = self._route(actor, parsed)
        except ProviderServiceError as error:
            raise ProviderServiceError(
                "route_changed", "Provider eligibility changed after this plan was issued.", status=409
            ) from error
        if not self._same_public(current_decision.to_dict(), signed_decision.to_dict()):
            raise ProviderServiceError(
                "route_changed", "Provider routing changed after this plan was issued.", status=409
            )
        try:
            validated = self._validate_options(parsed, current_decision)
            self._validate_autoip_instruction(validated, current_decision)
        except ProviderServiceError as error:
            raise ProviderServiceError(
                "option_profile_changed",
                "Variant option or generation validation changed after this plan was issued.",
                status=409,
            ) from error
        if not self._same_public(dict(validated.options), dict(parsed.options)):
            raise ProviderServiceError(
                "option_profile_changed",
                "Variant options changed after this plan was issued.",
                status=409,
            )
        parsed = validated
        current_account_id, current_account = self._account(
            actor,
            str(current_decision.selected_provider_id),
        )
        if (
            current_account_id != payload.get("selected_account_id")
            or current_account is None
            or current_account.get("enabled") is not True
        ):
            raise ProviderServiceError(
                "route_changed", "Provider account readiness changed after planning.", status=409
            )
        try:
            current_quote = self._resolve_quote(
                actor,
                current_account,
                parsed,
                current_decision,
            )
        except ProviderServiceError as error:
            raise ProviderServiceError(
                "quote_changed", "Provider pricing is no longer valid for this plan.", status=409
            ) from error
        if not self._same_executable_quote(current_quote, signed_quote):
            raise ProviderServiceError(
                "quote_changed", "Provider quote changed after this plan was issued.", status=409
            )
        persistent = self._persist_confirmation(
            actor,
            payload,
            intent,
            current_decision,
            current_quote,
        )
        return self._confirmation_result(
            payload,
            self._confirmation_storage_parts(payload),
            persistent,
            accepted_credits,
        )

    @staticmethod
    def _generation_storage_ids(plan_id: str) -> dict[str, str]:
        suffix = hashlib.sha256(plan_id.encode("utf-8")).hexdigest()[:32]
        return {
            "intent_id": f"intent:{suffix}",
            "decision_id": f"route:{suffix}",
            "outbox_id": f"outbox:{suffix}",
            "client_request_id": f"request:{suffix}",
        }

    @staticmethod
    def _generation_result(
        request_id: str,
        confirmation: Mapping[str, Any],
        decision: Mapping[str, Any],
        quote: Mapping[str, Any],
    ) -> dict[str, Any]:
        intent_id = str(confirmation["intent_id"])
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "request_id": request_id,
            "plan_id": confirmation["plan_id"],
            "intent_id": intent_id,
            "decision_id": confirmation["decision_id"],
            "outbox": confirmation["outbox"],
            "client_request_id": confirmation["client_request_id"],
            "state": confirmation["state"],
            "credits_authorized": confirmation["credits_authorized"],
            "credits_spent": confirmation["credits_spent"],
            "transport_dispatched": confirmation["transport_dispatched"],
            "next_action": confirmation["next_action"],
            "decision": dict(decision),
            "quote": dict(quote),
            "confirmation_required": False,
            "status_path": f"/api/intents/{intent_id}/status",
        }

    def _find_generation_replay(
        self,
        actor: ActorContext,
        plan_id: str,
        binding: Mapping[str, str],
    ) -> dict[str, Any] | None:
        """Replay one exact one-click request from its owner-scoped durable bundle."""

        ids = self._generation_storage_ids(plan_id)
        try:
            self._store.get_intent_status(ids["intent_id"], actor.user_id)
        except KeyError:
            return None
        except ProviderFabricStoreError as error:
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior generation request could not be read safely.",
                status=503,
            ) from error
        try:
            stored_intent = self._store.get_intent(ids["intent_id"])
            stored_decision = self._store.get_route_decision(ids["decision_id"])
            stored_outbox = self._store.get_outbox(ids["outbox_id"])
        except (KeyError, ProviderFabricStoreError, ValueError) as error:
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior generation request could not be read safely.",
                status=503,
            ) from error

        persistent_payload = stored_intent.get("payload")
        stored_binding = (
            persistent_payload.get("generation_request")
            if isinstance(persistent_payload, Mapping)
            else None
        )
        if (
            not isinstance(persistent_payload, Mapping)
            or persistent_payload.get("plan_id") != plan_id
            or persistent_payload.get("owner_user_id") != actor.user_id
            or not self._same_public(stored_binding, binding)
        ):
            raise ProviderServiceError(
                "confirmation_conflict",
                "request_id already identifies different immutable plan data.",
                status=409,
            )
        if (
            stored_decision.get("intent_id") != ids["intent_id"]
            or stored_outbox.get("intent_id") != ids["intent_id"]
            or stored_outbox.get("decision_id") != ids["decision_id"]
            or stored_outbox.get("outbox_id") != ids["outbox_id"]
            or stored_outbox.get("account_id")
            != stored_decision.get("selected_account_id")
            or stored_outbox.get("client_request_id")
            != ids["client_request_id"]
        ):
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior generation request has an incomplete durable bundle.",
                status=503,
            )
        quote = stored_decision.get("quote")
        if not isinstance(quote, Mapping):
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior generation request quote is unavailable.",
                status=503,
            )
        try:
            parsed_quote = _quote_from_mapping(quote)
            decision = _decision_from_mapping(
                {
                    "routable": bool(
                        stored_decision.get("selected_provider_id")
                        and stored_decision.get("selected_variant_id")
                    ),
                    "selected_provider_id": stored_decision.get(
                        "selected_provider_id"
                    ),
                    "selected_variant_id": stored_decision.get(
                        "selected_variant_id"
                    ),
                    "candidate_keys": stored_decision.get("candidate_keys"),
                    "rejections": stored_decision.get("rejections"),
                    "policy_version": stored_decision.get("policy_version"),
                }
            ).to_dict()
        except ProviderServiceError as error:
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior generation request route data is invalid.",
                status=503,
            ) from error
        if (
            parsed_quote.credits is None
            or parsed_quote.account_id
            != stored_decision.get("selected_account_id")
            or parsed_quote.provider_id
            != stored_decision.get("selected_provider_id")
            or parsed_quote.capability.value != stored_intent.get("capability")
        ):
            raise ProviderServiceError(
                "durable_outbox_failed",
                "The prior generation request quote is unavailable.",
                status=503,
            )
        confirmation = self._confirmation_result(
            {
                "plan_id": plan_id,
                "selected_account_id": stored_decision["selected_account_id"],
            },
            ids,
            {"outbox": stored_outbox},
            parsed_quote.credits,
        )
        return self._generation_result(
            str(binding["request_id"]),
            confirmation,
            decision,
            parsed_quote.to_dict(),
        )

    def submit_generation(
        self,
        actor: ActorContext,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Plan and durably confirm one generation without provider transport."""

        actor = self._require_actor(actor)
        request = _safe_mapping(value, "generation request")
        required_fields = frozenset({"schema_version", "request_id", "plan"})
        _strict_keys(request, required_fields, "generation request")
        _schema_version(request)
        if set(request) != required_fields:
            raise ProviderServiceError(
                "schema_invalid",
                "generation request requires schema_version, request_id, and plan.",
            )
        _assert_public_tree(request)
        try:
            request_id = _stable_id(request.get("request_id"), "request_id")
        except ValueError as error:
            raise ProviderServiceError(
                "schema_invalid", "request_id must be a stable lowercase identifier."
            ) from error
        if request.get("request_id") != request_id:
            raise ProviderServiceError(
                "schema_invalid", "request_id must use its exact canonical form."
            )
        plan_request = _safe_mapping(request.get("plan"), "generation plan")
        plan_hash = hashlib.sha256(
            _canonical_json(plan_request).encode("utf-8")
        ).hexdigest()
        binding = {"request_id": request_id, "plan_hash": plan_hash}
        plan_nonce = self._generation_plan_nonce(actor.user_id, request_id)
        plan_id = f"plan:{plan_nonce}"

        replay = self._find_generation_replay(actor, plan_id, binding)
        if replay is not None:
            return replay

        planned = self._plan_intent(
            actor,
            plan_request,
            plan_nonce=plan_nonce,
            generation_request=binding,
        )
        confirmation_request = {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "plan_token": planned["plan_token"],
            "confirmed": True,
            "accepted_credits": planned["quote"]["credits"],
        }
        try:
            confirmation = self.confirm_intent(actor, confirmation_request)
        except ProviderServiceError as error:
            if error.code != "confirmation_conflict":
                raise
            replay = self._find_generation_replay(actor, plan_id, binding)
            if replay is None:
                raise
            return replay
        return self._generation_result(
            request_id,
            confirmation,
            planned["decision"],
            planned["quote"],
        )

    def get_intent_status(self, actor: ActorContext, intent_id: str) -> dict[str, Any]:
        """Read one confirmed intent owned by the authenticated Castle actor.

        This path is a local database snapshot only.  It never polls a provider,
        resolves a credential, retries a submission, or verifies an output.
        """

        actor = self._require_actor(actor)
        try:
            normalized_intent_id = _stable_id(intent_id, "intent_id")
        except ValueError as error:
            raise ProviderServiceError(
                "intent_not_found",
                "Intent was not found.",
                status=404,
            ) from error
        if (
            normalized_intent_id != intent_id
            or not _PUBLIC_INTENT_ID_RE.fullmatch(normalized_intent_id)
        ):
            raise ProviderServiceError(
                "intent_not_found",
                "Intent was not found.",
                status=404,
            )
        try:
            status = self._store.get_intent_status(normalized_intent_id, actor.user_id)
        except KeyError as error:
            # Missing, unowned legacy, and cross-user records are deliberately
            # indistinguishable.  Elevated roles receive no implicit bypass.
            raise ProviderServiceError(
                "intent_not_found",
                "Intent was not found.",
                status=404,
            ) from error
        except ProviderFabricStoreError as error:
            raise ProviderServiceError(
                "intent_status_unavailable",
                "Intent status is temporarily unavailable.",
                status=503,
            ) from error
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            **status,
            "status_check_transport_performed": False,
            "status_check_credits_spent": 0,
        }

    def verify_autoip_webhook(self, raw_body: bytes, signature_header: str) -> dict[str, Any]:
        """Return only a durable, replay-protected reconciliation signal."""

        if self._webhook_secret_ref is None:
            raise ProviderServiceError(
                "webhook_not_configured", "AutoIP webhook verification is not configured.", status=503
            )
        try:
            secret = self._webhook_secret_ref.resolve()
            # First pass authenticates and validates without a durable claim so
            # unknown task IDs cannot fill the replay table.
            verifier = AutoIPWebhookVerifier(
                MemoryReplayGuard(clock=self._clock),
                clock=self._clock,
            )
            signal = verifier.verify(raw_body, signature_header, secret)
        except WebhookReplayError as error:
            raise ProviderServiceError(
                "webhook_replay", "Webhook delivery was already accepted.", status=409
            ) from error
        except (WebhookVerificationError, OSError, KeyError, PermissionError, ValueError) as error:
            raise ProviderServiceError(
                "webhook_invalid", "Webhook verification failed.", status=400
            ) from error
        try:
            known = self._known_external_task(signal.task_uuid) is True
        except Exception as error:
            raise ProviderServiceError(
                "task_lookup_unavailable", "Webhook task lookup is unavailable.", status=503
            ) from error
        if not known:
            raise ProviderServiceError(
                "webhook_unknown_task", "Webhook refers to an unknown provider task.", status=404
            )
        # Claim the replay key only after the task is known.  Unknown but
        # otherwise valid deliveries must not consume durable replay rows.
        try:
            guard = PersistentReplayGuard(self._store)
            verifier = AutoIPWebhookVerifier(guard, clock=self._clock)
            signal = verifier.verify(raw_body, signature_header, secret)
        except WebhookReplayError as error:
            raise ProviderServiceError(
                "webhook_replay", "Webhook delivery was already accepted.", status=409
            ) from error
        except (WebhookVerificationError, ValueError) as error:
            raise ProviderServiceError(
                "webhook_invalid", "Webhook verification failed.", status=400
            ) from error
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "accepted": True,
            "signal": signal.to_dict(),
            "provider_write_performed": False,
        }


__all__ = [
    "ActorContext",
    "AutoIPReadinessSource",
    "CanonicalResourceResolver",
    "CapabilityMaterializer",
    "DEFAULT_PLAN_TTL_SECONDS",
    "H3AuditSource",
    "H3_CONTRACT_IDS",
    "PLAN_TOKEN_TYPE",
    "ProviderFabricService",
    "ProviderServiceError",
    "SERVICE_SCHEMA_VERSION",
]
