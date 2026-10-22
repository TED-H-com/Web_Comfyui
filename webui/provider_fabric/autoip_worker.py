"""Rebuild one confirmed AutoIP submission from Castle's immutable bundle.

The processor is intentionally transport-free until every signed field,
profile binding, input role, and local byte digest has been revalidated.  It is
designed to be passed to :func:`provider_worker.run_once`; that outer boundary
owns the durable at-most-once state transition.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .autoip import (
    AUTOIP_PROVIDER_ID,
    AutoIPApiError,
    AutoIPClient,
    AutoIPFile,
    AutoIPJob,
    AutoIPOperation,
    AutoIPPreflightError,
)
from .autoip_profiles import AUTOIP_PROFILE_BY_KEY
from .models import CostQuote, MediaAsset, MediaKind, RouteDecision, RouteIntent, TaskReceipt
from .provider_worker import DefiniteLocalFailure, DefiniteProviderRejection
from .service import _decision_from_mapping, _intent_from_mapping, _quote_from_mapping
from .variant_profiles import VariantProfile


class AutoIPClientLike(Protocol):
    def submit(
        self,
        intent: RouteIntent,
        decision: RouteDecision,
        job: AutoIPJob,
        *,
        client_request_id: str,
        quote: CostQuote,
    ) -> TaskReceipt: ...


ClientResolver = Callable[[str, str], AutoIPClientLike]
AssetLoader = Callable[[str, MediaAsset, str], AutoIPFile]
ProfileResolver = Callable[[str, str, str], VariantProfile]
QuoteVerifier = Callable[
    [str, str, RouteIntent, RouteDecision, Mapping[str, object]],
    CostQuote,
]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DefiniteLocalFailure(f"{label} is not an immutable mapping")
    return value


def _profile_binding(profile: VariantProfile) -> dict[str, str]:
    binding = {
        "action_id": profile.action_id,
        "feature_id": profile.feature_id,
        "profile_key": profile.key,
        "profile_revision": profile.profile_revision,
    }
    if profile.technical_evidence_id is not None:
        binding["technical_evidence_id"] = profile.technical_evidence_id
    elif profile.golden_id is not None:
        binding["golden_id"] = profile.golden_id
    else:
        raise DefiniteLocalFailure("AutoIP profile has no execution evidence")
    return binding


def _prompt(instruction: Mapping[str, object], operation: AutoIPOperation) -> str:
    expected = {
        "schema_version", "shot_id", "frame_intent", "observable_action",
        "camera", "audio_intent", "language", "content_hash",
    }
    if "production_binding" in instruction:
        expected.add("production_binding")
    for optional in (
        "subject_descriptions", "dialogue_text", "negative_prompt", "seed",
    ):
        if optional in instruction:
            expected.add(optional)
    if set(instruction) != expected or instruction.get("schema_version") != "castle.creative-instruction.v1":
        raise DefiniteLocalFailure("creative instruction contract is incomplete")
    material = {key: instruction[key] for key in expected - {"content_hash"}}
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if instruction.get("content_hash") != hashlib.sha256(encoded).hexdigest():
        raise DefiniteLocalFailure("creative instruction hash changed")
    values = []
    for key, label in (
        ("frame_intent", "frame"),
        ("observable_action", "action"),
        ("camera", "camera"),
        ("audio_intent", "audio"),
        ("language", "language"),
    ):
        value = instruction.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DefiniteLocalFailure("creative instruction text is invalid")
        values.append(f"{label}: {value.strip()}")
    prompt = ". ".join(values)
    maximum = 2000 if operation in {
        AutoIPOperation.TEXT_TO_IMAGE, AutoIPOperation.IMAGE_TO_IMAGE,
    } else 500 if operation is AutoIPOperation.DANCE else 4000
    if len(prompt) > maximum:
        raise DefiniteLocalFailure("creative instruction exceeds the provider prompt limit")
    return prompt


def _optional_text(
    instruction: Mapping[str, object],
    key: str,
    maximum: int,
) -> str | None:
    value = instruction.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > maximum
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
    ):
        raise DefiniteLocalFailure(f"creative instruction {key} is invalid")
    return value.strip()


def _subject_descriptions(
    instruction: Mapping[str, object],
    image_count: int,
) -> tuple[str, ...] | None:
    value = instruction.get("subject_descriptions")
    if value is None:
        return None
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) > image_count
    ):
        raise DefiniteLocalFailure("creative instruction subject descriptions are invalid")
    result = []
    for item in value:
        if (
            not isinstance(item, str)
            or len(item) > 200
            or any(ord(character) < 32 and character not in "\n\t" for character in item)
        ):
            raise DefiniteLocalFailure("creative instruction subject descriptions are invalid")
        result.append(item)
    return tuple(result)


def _assign_fields(operation: AutoIPOperation, assets: tuple[MediaAsset, ...]) -> tuple[str, ...]:
    images = [asset for asset in assets if asset.kind is MediaKind.IMAGE]
    audio = [asset for asset in assets if asset.kind is MediaKind.AUDIO]
    video = [asset for asset in assets if asset.kind is MediaKind.VIDEO]
    if operation in {AutoIPOperation.TEXT_TO_IMAGE, AutoIPOperation.TEXT_TO_VIDEO}:
        if assets:
            raise DefiniteLocalFailure("selected operation cannot consume identity assets")
        return ()
    if operation is AutoIPOperation.IMAGE_TO_IMAGE:
        if len(images) != 1 or len(assets) != 1:
            raise DefiniteLocalFailure("image-to-image requires one exact image")
        return ("image",)
    if operation is AutoIPOperation.IMAGE_TO_VIDEO:
        if len(images) not in {1, 2} or len(images) != len(assets):
            raise DefiniteLocalFailure("image-to-video requires one or two exact images")
        return ("first_frame", "last_frame")[:len(images)]
    if operation is AutoIPOperation.LIP_SYNC:
        if len(images) != 1 or len(audio) != 1 or len(assets) != 2:
            raise DefiniteLocalFailure("lip sync requires one image and one audio asset")
        return tuple("image" if asset.kind is MediaKind.IMAGE else "audio" for asset in assets)
    if operation is AutoIPOperation.DANCE:
        if len(images) != 1 or len(video) != 1 or len(assets) != 2:
            raise DefiniteLocalFailure("dance requires one image and one motion video")
        return tuple("image" if asset.kind is MediaKind.IMAGE else "dance_video" for asset in assets)
    if operation is AutoIPOperation.MULTI_SUBJECT_VIDEO:
        if not 1 <= len(images) <= 4 or len(audio) > 1 or len(assets) != len(images) + len(audio):
            raise DefiniteLocalFailure("multi-subject video inputs are invalid")
        return tuple("images[]" if asset.kind is MediaKind.IMAGE else "voice_sample" for asset in assets)
    raise DefiniteLocalFailure("AutoIP operation is unsupported")


def _verified_file(
    loader: AssetLoader,
    owner_user_id: str,
    asset: MediaAsset,
    field_name: str,
) -> AutoIPFile:
    if asset.sha256 is None or asset.byte_size is None or asset.mime_type is None:
        raise DefiniteLocalFailure("canonical asset has no immutable byte identity")
    loaded = loader(owner_user_id, asset, field_name)
    if not isinstance(loaded, AutoIPFile) or loaded.field_name != field_name:
        raise DefiniteLocalFailure("asset loader returned a mismatched input role")
    path = Path(loaded.path)
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size != asset.byte_size:
            raise DefiniteLocalFailure("canonical asset size changed")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except DefiniteLocalFailure:
        raise
    except OSError as error:
        raise DefiniteLocalFailure("canonical asset is unavailable") from error
    if digest.hexdigest() != asset.sha256:
        raise DefiniteLocalFailure("canonical asset digest changed")
    if loaded.content_type not in {None, asset.mime_type}:
        raise DefiniteLocalFailure("canonical asset MIME changed")
    if asset.visual_class is not None and loaded.visual_class is not asset.visual_class:
        raise DefiniteLocalFailure("canonical asset visual class changed")
    return AutoIPFile(
        field_name=field_name,
        path=path,
        visual_class=asset.visual_class,
        content_type=asset.mime_type,
    )


class AutoIPSubmissionProcessor:
    """Callable adapter for one already-claimed, actor-owned outbox bundle."""

    def __init__(
        self,
        client_resolver: ClientResolver,
        asset_loader: AssetLoader,
        *,
        profile_resolver: ProfileResolver | None = None,
        quote_verifier: QuoteVerifier | None = None,
    ) -> None:
        if not callable(client_resolver) or not callable(asset_loader):
            raise TypeError("client_resolver and asset_loader are required")
        self._client_resolver = client_resolver
        self._asset_loader = asset_loader
        self._profile_resolver = profile_resolver or (
            lambda _owner, _account, key: AUTOIP_PROFILE_BY_KEY[key]
        )
        if quote_verifier is not None and not callable(quote_verifier):
            raise TypeError("quote_verifier must be callable")
        self._quote_verifier = quote_verifier

    def __call__(self, bundle: Mapping[str, object]) -> TaskReceipt:
        try:
            owner_user_id = str(bundle["owner_user_id"])
            intent_record = _mapping(bundle["intent"], "intent record")
            decision_record = _mapping(bundle["decision"], "decision record")
            task = _mapping(bundle["task"], "task")
            payload = _mapping(intent_record["payload"], "intent payload")
            intent = _intent_from_mapping(intent_record["intent"])
            decision = _decision_from_mapping({
                "routable": True,
                "selected_provider_id": decision_record["selected_provider_id"],
                "selected_variant_id": decision_record["selected_variant_id"],
                "candidate_keys": decision_record["candidate_keys"],
                "rejections": decision_record["rejections"],
                "policy_version": decision_record["policy_version"],
            })
            quote = _quote_from_mapping(decision_record["quote"])
            if decision.selected_provider_id != AUTOIP_PROVIDER_ID:
                raise DefiniteLocalFailure("claimed task is not routed to AutoIP")
            profile_key = f"autoip:{decision.selected_variant_id}"
            account_id = str(decision_record["selected_account_id"])
            profile = self._profile_resolver(owner_user_id, account_id, profile_key)
            if (
                profile.key != profile_key
                or profile.capability is not intent.capability
                or profile.visual_class is not intent.visual_class
                or not profile.execution_ready
                or not profile.execution_evidence_id
            ):
                raise DefiniteLocalFailure("AutoIP profile is not execution-ready")
            identity_bound = bool(intent.characters) or any(
                asset.character_ids for asset in intent.assets
            )
            capability_binding = payload.get("capability_grant")
            if identity_bound:
                if dict(_mapping(
                    capability_binding, "AutoIP capability binding"
                )) != _profile_binding(profile):
                    raise DefiniteLocalFailure("AutoIP technical readiness evidence changed")
            elif capability_binding is not None:
                raise DefiniteLocalFailure("generic AutoIP media has a character capability binding")
            options = _mapping(payload.get("options"), "selected options")
            option_keys = set(options)
            if (
                not set(profile.required_option_keys).issubset(option_keys)
                or not option_keys.issubset(profile.option_values)
            ):
                raise DefiniteLocalFailure("selected AutoIP options changed")
            for key, value in options.items():
                if value not in profile.option_values[key]:
                    raise DefiniteLocalFailure("selected AutoIP option is no longer allowed")
            operation = AutoIPOperation(intent.capability.value)
            instruction = _mapping(
                payload.get("creative_instruction"), "creative instruction"
            )
            fields = {**options, "prompt": _prompt(instruction, operation)}
            if fields.get("seed") == "random":
                fields.pop("seed")
            fields.pop("native_audio", None)
            if "seed" in instruction and instruction.get("seed") != options.get("seed"):
                raise DefiniteLocalFailure("creative instruction seed differs from selected options")
            if operation is AutoIPOperation.LIP_SYNC:
                fields.setdefault("audio_start_time", 0)
                fields.setdefault("audio_duration", 5)
            if operation is AutoIPOperation.DANCE:
                # The mutable template catalog is intentionally not presented
                # as executable.  Castle's exact lane is one uploaded motion
                # video, and motion_source is not a provider wire field.
                if fields.pop("motion_source", None) != "dance_video":
                    raise DefiniteLocalFailure("dance motion source is not executable")
            roles = _assign_fields(operation, intent.assets)
            if operation is AutoIPOperation.MULTI_SUBJECT_VIDEO:
                image_count = sum(
                    asset.kind is MediaKind.IMAGE for asset in intent.assets
                )
                descriptions = _subject_descriptions(instruction, image_count)
                if descriptions is not None:
                    fields["descriptions[]"] = descriptions
                dialogue = _optional_text(instruction, "dialogue_text", 1000)
                negative = _optional_text(instruction, "negative_prompt", 1000)
                has_voice_sample = any(
                    asset.kind is MediaKind.AUDIO for asset in intent.assets
                )
                if has_voice_sample and dialogue is None:
                    raise DefiniteLocalFailure("AutoIP voice sample requires dialogue")
                if dialogue is not None:
                    fields["dialogue_text"] = dialogue
                if negative is not None:
                    fields["negative_prompt"] = negative
            elif operation is AutoIPOperation.TEXT_TO_IMAGE:
                negative = _optional_text(instruction, "negative_prompt", 500)
                if negative is not None:
                    fields["negative_prompt"] = negative
            elif operation is AutoIPOperation.IMAGE_TO_IMAGE:
                negative = _optional_text(instruction, "negative_prompt", 2000)
                if negative is not None:
                    if fields.get("mode", "standard") != "hd_4k":
                        raise DefiniteLocalFailure("AutoIP image negative prompt requires hd_4k mode")
                    fields["negative_prompt"] = negative
            files = tuple(
                _verified_file(self._asset_loader, owner_user_id, asset, role)
                for asset, role in zip(intent.assets, roles, strict=True)
            )
            spend_guard = _mapping(payload.get("spend_guard"), "spend guard")
            if (
                set(spend_guard) != {"max_credits", "quoted_credits", "currency"}
                or spend_guard.get("currency") != "provider_credits"
                or spend_guard.get("quoted_credits") != quote.credits
                or quote.credits is None
            ):
                raise DefiniteLocalFailure("AutoIP spend guard changed")
            job = AutoIPJob(
                operation=operation,
                visual_class=intent.visual_class,
                fields=fields,
                files=files,
                max_credits=None,
            )
            if self._quote_verifier is not None:
                try:
                    current_quote = self._quote_verifier(
                        owner_user_id,
                        account_id,
                        intent,
                        decision,
                        options,
                    )
                except Exception as error:
                    raise DefiniteLocalFailure(
                        "AutoIP live quote could not be revalidated"
                    ) from error
                if (
                    not isinstance(current_quote, CostQuote)
                    or current_quote.provider_id != quote.provider_id
                    or current_quote.capability is not quote.capability
                    or current_quote.credits != quote.credits
                    or current_quote.authoritative is not True
                    or current_quote.quote_id != quote.quote_id
                    or current_quote.account_id != quote.account_id
                ):
                    raise DefiniteLocalFailure("AutoIP live quote changed")
            client = self._client_resolver(owner_user_id, account_id)
            if not isinstance(client, AutoIPClient) and not callable(getattr(client, "submit", None)):
                raise DefiniteLocalFailure("AutoIP client is unavailable")
            try:
                return client.submit(
                    intent,
                    decision,
                    job,
                    client_request_id=str(task["client_request_id"]),
                    quote=quote,
                )
            except AutoIPApiError as error:
                if error.definite_submission_rejection:
                    raise DefiniteProviderRejection(
                        error.status,
                        error.error_code,
                        retry_after=error.retry_after,
                    ) from error
                # HTTP 408/5xx cannot prove whether an upstream accepted the
                # POST.  Preserve the outer worker's submission-uncertain path.
                raise
        except DefiniteLocalFailure:
            raise
        except AutoIPPreflightError as error:
            raise DefiniteLocalFailure("AutoIP preflight rejected the immutable request") from error
        except (KeyError, TypeError, ValueError) as error:
            raise DefiniteLocalFailure("confirmed AutoIP bundle is invalid") from error


__all__ = [
    "AssetLoader", "AutoIPSubmissionProcessor", "ClientResolver",
    "ProfileResolver", "QuoteVerifier",
]
