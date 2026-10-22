"""Compile exact-class confirmed MiniMAX H3 work for the shared GPU queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from minimax_h3_product import compile_product_prompt

from .h3_profiles import h3_character_profiles
from .models import MediaAsset, TaskReceipt
from .provider_worker import DefiniteLocalFailure
from .service import _decision_from_mapping, _intent_from_mapping
from .variant_profiles import VariantProfile


@dataclass(frozen=True, slots=True)
class H3QueueRequest:
    owner_user_id: str
    task_id: str
    client_request_id: str
    variant_id: str
    graph: Mapping[str, Mapping[str, object]]
    graph_sha256: str
    source_path: Path
    source_sha256: str
    upload_basename: str


class H3QueueExecutor(Protocol):
    def __call__(self, request: H3QueueRequest) -> TaskReceipt: ...


AssetLoader = Callable[[str, MediaAsset], Path]
ProfileResolver = Callable[[str], VariantProfile]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DefiniteLocalFailure(f"{label} is invalid")
    return value


def profile_binding(profile: VariantProfile) -> dict[str, str]:
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
        raise DefiniteLocalFailure("H3 profile has no execution evidence")
    return binding


def _instruction(value: object) -> Mapping[str, object]:
    item = _mapping(value, "creative instruction")
    expected = {
        "schema_version", "shot_id", "frame_intent", "observable_action",
        "camera", "audio_intent", "language", "content_hash",
    }
    if "production_binding" in item:
        expected.add("production_binding")
    if set(item) != expected or item.get("schema_version") != "castle.creative-instruction.v1":
        raise DefiniteLocalFailure("creative instruction contract is incomplete")
    material = {key: item[key] for key in expected - {"content_hash"}}
    raw = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if item.get("content_hash") != hashlib.sha256(raw).hexdigest():
        raise DefiniteLocalFailure("creative instruction hash changed")
    return item


def _description(item: Mapping[str, object]) -> str:
    parts = []
    for key, label in (
        ("frame_intent", "Frame"),
        ("observable_action", "Action"),
        ("camera", "Camera"),
        ("audio_intent", "Audio"),
        ("language", "Language"),
    ):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DefiniteLocalFailure("creative instruction text is invalid")
        parts.append(f"{label}: {value.strip()}")
    result = ". ".join(parts)
    if len(result) > 4000:
        raise DefiniteLocalFailure("H3 creative instruction is too long")
    return result


def _verified_source(loader: AssetLoader, owner: str, asset: MediaAsset) -> Path:
    if asset.sha256 is None or asset.byte_size is None or asset.mime_type is None:
        raise DefiniteLocalFailure("H3 input lacks immutable byte identity")
    path = Path(loader(owner, asset))
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise DefiniteLocalFailure("H3 input is unavailable") from error
    if total != asset.byte_size or digest.hexdigest() != asset.sha256:
        raise DefiniteLocalFailure("H3 input bytes changed")
    return path


class H3SubmissionProcessor:
    """Rebuild an H3 graph; the injected executor owns shared-GPU dispatch."""

    def __init__(
        self,
        asset_loader: AssetLoader,
        executor: H3QueueExecutor,
        *,
        profile_resolver: ProfileResolver,
    ) -> None:
        if not all(callable(item) for item in (asset_loader, executor, profile_resolver)):
            raise TypeError("asset_loader, executor, and profile_resolver are required")
        self._asset_loader = asset_loader
        self._executor = executor
        self._profile_resolver = profile_resolver

    def __call__(self, bundle: Mapping[str, object]) -> TaskReceipt:
        try:
            owner = str(bundle["owner_user_id"])
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
            if decision.selected_provider_id != "local_comfy":
                raise DefiniteLocalFailure("claimed task is not local ComfyUI work")
            profile_key = f"local_comfy:{decision.selected_variant_id}"
            profile = self._profile_resolver(profile_key)
            if (
                profile.key != profile_key
                or profile.capability is not intent.capability
                or profile.visual_class is not intent.visual_class
                or not profile.execution_ready
            ):
                raise DefiniteLocalFailure("H3 profile is not technically execution-ready")
            signed_binding = _mapping(
                payload.get("capability_grant"), "H3 capability binding"
            )
            if dict(signed_binding) != profile_binding(profile):
                raise DefiniteLocalFailure("H3 technical readiness evidence changed")
            if len(intent.assets) != 1 or len(intent.characters) != 1:
                raise DefiniteLocalFailure("H3 character work requires one character avatar")
            asset = intent.assets[0]
            if asset.character_ids != (intent.characters[0].character_id,):
                raise DefiniteLocalFailure("H3 avatar lineage differs from its character")
            options = _mapping(payload.get("options"), "H3 options")
            if set(options) != set(profile.required_option_keys):
                raise DefiniteLocalFailure("H3 option signature changed")
            for key, value in options.items():
                if value not in profile.option_values[key]:
                    raise DefiniteLocalFailure("H3 option is not allowed for this class")
            instruction = _instruction(payload.get("creative_instruction"))
            source = _verified_source(self._asset_loader, owner, asset)
            reference = str(decision.selected_variant_id).startswith("reference-video.")
            feature_id = "h3-reference-video" if reference else "h3-image-video"
            slot = "reference" if reference else "image"
            request_id = str(task["client_request_id"])
            seed = int(str(instruction["content_hash"])[:8], 16)
            output_token = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
            suffix = source.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise DefiniteLocalFailure("H3 input filename has an unsupported extension")
            upload_basename = f"castle_h3_{asset.sha256[:32]}{suffix}"
            graph = compile_product_prompt(
                feature_id=feature_id,
                visual_class=intent.visual_class.value,
                settings={
                    "description": _description(instruction),
                    "motionProfile": options["motion_profile"],
                    "aspect": options["aspect"],
                    "quality": options["quality"],
                    "seed": seed,
                },
                uploads={slot: upload_basename},
                output_token=output_token,
            )
            graph_hash = hashlib.sha256(json.dumps(
                graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            return self._executor(H3QueueRequest(
                owner_user_id=owner,
                task_id=str(task["task_id"]),
                client_request_id=request_id,
                variant_id=str(decision.selected_variant_id),
                graph=graph,
                graph_sha256=graph_hash,
                source_path=source,
                source_sha256=str(asset.sha256),
                upload_basename=upload_basename,
            ))
        except DefiniteLocalFailure:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise DefiniteLocalFailure("confirmed H3 bundle is invalid") from error


H3_VARIANT_IDS = tuple(profile.variant_id for profile in h3_character_profiles())

__all__ = [
    "AssetLoader", "H3QueueExecutor", "H3QueueRequest", "H3SubmissionProcessor",
    "H3_VARIANT_IDS", "ProfileResolver", "profile_binding",
]
