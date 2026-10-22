"""Prepare exact visual work without submitting providers or queueing a GPU."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from provider_fabric.variant_profiles import VariantProfile

from .autoip_adapter import build_autoip_request
from .compiler import compile_project, materialize_provider_task
from .contracts import ProductionPack
from .h3_adapter import build_h3_graph
from .prompt_contract import compile_shot_prompt
from .provider_prompt import bind_provider_prompt


PREPARED_VISUAL_VERSION = "castle.prepared-visual-execution.v1"


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("prepared execution must contain JSON-compatible values")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PreparedVisualExecution:
    project_id: str
    shot_id: str
    route_variant_id: str
    provider_id: str
    provider_variant_id: str
    task_input_hash: str
    payload: Mapping[str, Any]
    preparation_hash: str
    schema_version: str = PREPARED_VISUAL_VERSION

    def __post_init__(self) -> None:
        payload = _freeze(self.payload)
        material = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "shot_id": self.shot_id,
            "route_variant_id": self.route_variant_id,
            "provider_id": self.provider_id,
            "provider_variant_id": self.provider_variant_id,
            "task_input_hash": self.task_input_hash,
            "payload": _thaw(payload),
        }
        if self.preparation_hash != _hash(material):
            raise ValueError("prepared execution hash does not match its contents")
        object.__setattr__(self, "payload", payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "shot_id": self.shot_id,
            "route_variant_id": self.route_variant_id,
            "provider_id": self.provider_id,
            "provider_variant_id": self.provider_variant_id,
            "task_input_hash": self.task_input_hash,
            "payload": _thaw(self.payload),
            "preparation_hash": self.preparation_hash,
            "authority": {
                "provider_submission_authorized": False,
                "gpu_queue_authorized": False,
                "credits_authorized": 0,
            },
        }


def prepare_visual_execution(
    pack: ProductionPack,
    *,
    shot_id: str,
    route_variant_id: str,
    profile: VariantProfile,
    selected_options: Mapping[str, Any],
    canonical_inputs: Sequence[Mapping[str, Any]],
    seed: int | None = None,
    output_token: str | None = None,
) -> PreparedVisualExecution:
    """Materialize one route into an inert, hash-bound adapter payload."""

    if not isinstance(pack, ProductionPack) or not isinstance(profile, VariantProfile):
        raise TypeError("pack and profile must use Castle contract types")
    project = compile_project(pack)
    shot = next((item for item in pack.shots if item.shot_id == shot_id), None)
    run = next((item for item in project.shot_runs if item.shot_id == shot_id), None)
    route = next((item for item in pack.route_variants if item.route_variant_id == route_variant_id), None)
    if shot is None or run is None or route is None:
        raise ValueError("shot or route is not part of the production pack")
    if (
        route.provider_id != profile.provider_id
        or route.variant_id != profile.variant_id
        or route.capability is not profile.capability
        or profile.visual_class not in route.visual_classes
    ):
        raise ValueError("provider profile does not match the selected route")
    selected_keys = set(selected_options)
    if (
        not set(profile.required_option_keys).issubset(selected_keys)
        or not selected_keys.issubset(profile.option_values)
    ):
        raise ValueError("selected options do not match the exact provider profile")
    for key, value in selected_options.items():
        if value not in profile.option_values[key]:
            raise ValueError("selected option is not allowed by the provider profile")

    task = materialize_provider_task(run, "visual_generate", route_variant_id)
    contract = compile_shot_prompt(pack, shot)
    envelope = bind_provider_prompt(
        contract, profile, route_variant_id=route_variant_id,
    )
    records = [dict(item) for item in canonical_inputs]
    source_ids = [item.pop("source_asset_id", None) for item in records]
    if (
        any(not isinstance(item, str) for item in source_ids)
        or len(source_ids) != len(set(source_ids))
        or not set(source_ids).issubset(contract.immutable_resource_ids)
    ):
        raise ValueError("canonical inputs are not bound to immutable shot resources")

    if profile.provider_id == "autoip":
        if seed is not None or output_token is not None:
            raise ValueError("local execution fields cannot be sent to AutoIP")
        for item in records:
            item.pop("basename", None)
        normalized = build_autoip_request(
            envelope, selected_options=selected_options, inputs=records,
        )
        payload: dict[str, Any] = {
            "kind": "autoip_request",
            "request": normalized.to_dict(),
        }
    elif profile.provider_id == "local_comfy" and ".h3-" in profile.variant_id:
        if seed is None or output_token is None:
            raise ValueError("native H3 requires a server seed and output token")
        uploads: dict[str, str] = {}
        for item in records:
            if set(item) != {
                "field_name", "asset_version_id", "sha256", "mime_type", "byte_size",
                "signature_family", "visual_class", "basename",
            }:
                raise ValueError("native H3 input record is not canonical")
            if item["visual_class"] != profile.visual_class.value:
                raise ValueError("native H3 input class differs from the route")
            uploads[item["field_name"]] = item["basename"]
        payload = {
            "kind": "comfy_prompt_graph",
            "graph": build_h3_graph(
                envelope,
                selected_options=selected_options,
                server_uploads=uploads,
                seed=seed,
                output_token=output_token,
            ),
        }
    else:
        raise ValueError("selected provider profile has no production adapter")

    payload.update({
        "prompt_envelope": envelope.to_dict(),
        "source_asset_version_ids": [item.get("asset_version_id") for item in records],
    })
    material = {
        "schema_version": PREPARED_VISUAL_VERSION,
        "project_id": pack.project.project_id,
        "shot_id": shot_id,
        "route_variant_id": route_variant_id,
        "provider_id": profile.provider_id,
        "provider_variant_id": profile.variant_id,
        "task_input_hash": task.input_hash,
        "payload": payload,
    }
    return PreparedVisualExecution(
        project_id=pack.project.project_id,
        shot_id=shot_id,
        route_variant_id=route_variant_id,
        provider_id=profile.provider_id,
        provider_variant_id=profile.variant_id,
        task_input_hash=task.input_hash,
        payload=payload,
        preparation_hash=_hash(material),
    )


__all__ = ["PREPARED_VISUAL_VERSION", "PreparedVisualExecution", "prepare_visual_execution"]
