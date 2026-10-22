"""Compile only reviewed static templates and pinned local artifacts."""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import RenderClaim
from .errors import fail
from .manifests import IdentityPack, RenderRelease, SceneTemplate, canonical_json


PROMPT_NAMESPACE = uuid.UUID("35de7548-5031-52f0-9118-f7af4adcb89c")
MARKER_KEY = "$novel_scene_art"
SAFE_MARKER = re.compile(r"^[a-z][a-z0-9_.]{1,127}$")
SAFE_PREFIX = re.compile(r"^NovelSceneArt/[a-f0-9]{32}/[a-f0-9-]{36}$")
FIXED_SOURCES = frozenset(
    {
        "template.positive_prompt",
        "template.negative_prompt",
        "render.width",
        "render.height",
        "render.seed",
        "derived.filename_prefix",
    }
)

# Deliberately narrow. Expanding this set is a renderer release change and
# requires a new qualified compiler manifest. No download, script, preview,
# video, or arbitrary save node is admitted.
SAFE_NODE_CLASSES = frozenset(
    {
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "SaveImage",
        "LoadImage",
        "LoraLoader",
        "CheckpointLoaderSimple",
        "VAEDecode",
        "VAEEncode",
        "CLIPSetLastLayer",
        "IPAdapterModelLoader",
        "IPAdapterAdvanced",
        "PrepImageForClipVision",
        "CLIPVisionLoader",
        "VAELoader",
        "ControlNetLoader",
        "ControlNetApplyAdvanced",
    }
)
FILE_INPUT_RULES: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("CheckpointLoaderSimple", "ckpt_name"): ("model.checkpoint",),
    ("VAELoader", "vae_name"): ("model.vae",),
    ("CLIPVisionLoader", "clip_name"): ("model.clip_vision",),
    ("IPAdapterModelLoader", "ipadapter_file"): ("model.ipadapter",),
    ("ControlNetLoader", "control_net_name"): ("model.controlnet",),
    ("LoadImage", "image"): ("identity.",),
    ("LoraLoader", "lora_name"): ("identity.", "model.lora"),
}


@dataclass(frozen=True, slots=True)
class CompiledGraph:
    graph: Mapping[str, object]
    graph_sha256: str
    compiled_intent_sha256: str
    positive_prompt_sha256: str
    negative_prompt_sha256: str
    prompt_id: str
    template_id: str
    template_sha256: str
    output_node_id: str
    output_prefix: str
    seed: int


def deterministic_prompt_id_fields(
    *,
    job_id: str,
    request_id: str,
    generation_key: str,
    render_plan_hash: str,
    renderer_release_digest: str,
) -> str:
    """Return the one prompt identity used by journals, graphs, and Comfy."""

    name = "\x00".join(
        (
            job_id,
            request_id,
            generation_key,
            render_plan_hash,
            renderer_release_digest,
        )
    )
    return str(uuid.uuid5(PROMPT_NAMESPACE, name))


def deterministic_prompt_id(claim: RenderClaim) -> str:
    return deterministic_prompt_id_fields(
        job_id=claim.job_id,
        request_id=claim.request_id,
        generation_key=claim.generation_key,
        render_plan_hash=claim.render_plan_hash,
        renderer_release_digest=claim.renderer_release_digest,
    )


def _select_template(scene_family: object, slot_count: int, release: RenderRelease) -> SceneTemplate:
    if not isinstance(scene_family, str):
        fail("scene_template_unavailable", "ScenePack has no allowlisted scene family")
    matches = [
        template
        for template in release.templates
        if scene_family in template.scene_families and slot_count in template.identity_slot_counts
    ]
    if len(matches) != 1:
        fail("scene_template_unavailable", "Scene family has no unique reviewed template")
    return matches[0]


def _resolve_identities(claim: RenderClaim, release: RenderRelease) -> tuple[IdentityPack, ...]:
    result: list[IdentityPack] = []
    slots = claim.scene_pack["identity_slots"]
    assert isinstance(slots, list)
    for slot in slots:
        assert isinstance(slot, dict)
        identity = release.identities.get(
            (str(slot["persona_id"]), str(slot["identity_pack_revision"]))
        )
        if identity is None:
            fail("identity_pack_unavailable", "An exact reviewed identity pack is unavailable")
        result.append(identity)
    return tuple(result)


def _source_values(
    template: SceneTemplate,
    identities: tuple[IdentityPack, ...],
    claim: RenderClaim,
    release: RenderRelease,
    prompt_id: str,
) -> dict[str, object]:
    run_key = hashlib.sha256(
        (claim.job_id + "\x00" + claim.request_id).encode("utf-8", "strict")
    ).hexdigest()[:32]
    prefix = f"NovelSceneArt/{run_key}/{prompt_id}"
    if SAFE_PREFIX.fullmatch(prefix) is None:
        fail("compile_failed", "Output namespace is invalid")
    output = claim.render_plan["output"]
    assert isinstance(output, dict)
    values: dict[str, object] = {
        "template.positive_prompt": template.positive_prompt,
        "template.negative_prompt": template.negative_prompt,
        "render.width": output["width"],
        "render.height": output["height"],
        "render.seed": claim.seed,
        "derived.filename_prefix": prefix,
    }
    for role, artifact in release.models:
        if artifact.comfy_name is None:
            fail("compile_failed", "Model has no pinned Comfy catalog name")
        values[f"model.{role}"] = artifact.comfy_name
    for index, identity in enumerate(identities):
        artifacts = {artifact.artifact_id: artifact for artifact in identity.artifacts}
        for binding, artifact_id in identity.bindings.items():
            source = f"identity.{index}.{binding}"
            if SAFE_MARKER.fullmatch(source) is None:
                fail("compile_failed", "Identity binding name is invalid")
            artifact = artifacts[artifact_id]
            if artifact.comfy_name is None:
                fail("compile_failed", "Identity artifact has no pinned Comfy catalog name")
            values[source] = artifact.comfy_name
    return values


def _source_for_marker(marker_value: object, bindings: Mapping[str, object]) -> str:
    if not isinstance(marker_value, dict) or set(marker_value) != {MARKER_KEY}:
        fail("compile_failed", "A renderer file input is not a reviewed marker")
    marker = marker_value[MARKER_KEY]
    if not isinstance(marker, str) or SAFE_MARKER.fullmatch(marker) is None:
        fail("compile_failed", "Workflow marker is invalid")
    source = bindings.get(marker)
    if not isinstance(source, str):
        fail("compile_failed", "Workflow marker is not bound by the template")
    return source


def _audit_graph(
    graph: Mapping[str, object],
    template: SceneTemplate,
    release: RenderRelease,
    identities: tuple[IdentityPack, ...],
) -> None:
    used_models: set[str] = set()
    used_identities: set[tuple[int, str]] = set()
    save_nodes: list[str] = []
    for node_id, raw_node in graph.items():
        if not isinstance(node_id, str) or not isinstance(raw_node, dict):
            fail("compile_failed", "Workflow node shape is invalid")
        if not set(raw_node).issubset({"class_type", "inputs", "_meta"}):
            fail("compile_failed", "Workflow node contains unreviewed fields")
        class_type = raw_node.get("class_type")
        inputs = raw_node.get("inputs")
        if class_type not in SAFE_NODE_CLASSES or not isinstance(inputs, dict):
            fail("compile_failed", "Workflow contains an unapproved node class")
        if class_type == "SaveImage":
            save_nodes.append(node_id)
        for (node_class, input_name), prefixes in FILE_INPUT_RULES.items():
            if class_type != node_class:
                continue
            if input_name not in inputs:
                fail("compile_failed", "A renderer file input is missing")
            source = _source_for_marker(inputs[input_name], template.graph_bindings)
            if not any(
                source == prefix or (prefix == "identity." and source.startswith(prefix))
                for prefix in prefixes
            ):
                fail("compile_failed", "A renderer file input has the wrong artifact class")
            if source.startswith("model."):
                used_models.add(source.removeprefix("model."))
            elif source.startswith("identity."):
                parts = source.split(".")
                if len(parts) != 3 or not parts[1].isdigit():
                    fail("compile_failed", "Identity file source is malformed")
                used_identities.add((int(parts[1]), parts[2]))
    if save_nodes != [template.output_node_id]:
        fail("compile_failed", "Workflow must have one exact reviewed output node")
    expected_models = {role for role, _artifact in release.models}
    if used_models != expected_models:
        fail("compile_failed", "Every pinned model must bind an exact workflow file input")
    expected_identities = {
        (index, binding)
        for index, identity in enumerate(identities)
        for binding in identity.bindings
    }
    if used_identities != expected_identities:
        fail("compile_failed", "Every identity artifact binding must be consumed exactly")


def _replace_markers(value: object, bindings: Mapping[str, object], values: Mapping[str, object]) -> object:
    if isinstance(value, list):
        return [_replace_markers(item, bindings, values) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {MARKER_KEY}:
        marker = value[MARKER_KEY]
        if not isinstance(marker, str) or SAFE_MARKER.fullmatch(marker) is None:
            fail("compile_failed", "Workflow marker is invalid")
        source = bindings.get(marker)
        if not isinstance(source, str) or (
            source not in FIXED_SOURCES
            and not source.startswith("identity.")
            and not source.startswith("model.")
        ):
            fail("compile_failed", "Workflow marker source is not allowlisted")
        if source not in values:
            fail("compile_failed", "Workflow marker source is unavailable")
        return values[source]
    if MARKER_KEY in value:
        fail("compile_failed", "Workflow marker cannot be mixed with graph data")
    return {key: _replace_markers(item, bindings, values) for key, item in value.items()}


def compile_graph(claim: RenderClaim, release: RenderRelease) -> CompiledGraph:
    presentation = claim.scene_pack["presentation"]
    assert isinstance(presentation, dict)
    identities = _resolve_identities(claim, release)
    template = _select_template(presentation.get("scene_family"), len(identities), release)
    _audit_graph(release.workflow_graph, template, release, identities)
    prompt_id = deterministic_prompt_id(claim)
    values = _source_values(template, identities, claim, release, prompt_id)
    graph = _replace_markers(copy.deepcopy(release.workflow_graph), template.graph_bindings, values)
    if not isinstance(graph, dict) or not graph:
        fail("compile_failed", "Compiled workflow is invalid")

    def contains_marker(item: object) -> bool:
        if isinstance(item, list):
            return any(contains_marker(child) for child in item)
        if isinstance(item, dict):
            return MARKER_KEY in item or any(contains_marker(child) for child in item.values())
        return False

    if contains_marker(graph):
        fail("compile_failed", "Compiled workflow still contains unresolved markers")
    output_prefix = str(values["derived.filename_prefix"])
    output_node = graph.get(template.output_node_id)
    if not isinstance(output_node, dict):
        fail("compile_failed", "Reviewed output node is unavailable")
    output_inputs = output_node.get("inputs")
    if not isinstance(output_inputs, dict) or output_inputs.get("filename_prefix") != output_prefix:
        fail("compile_failed", "Reviewed output node is not bound to the exact namespace")
    graph_sha256 = hashlib.sha256(canonical_json(graph)).hexdigest()
    compiled_intent = {
        "schema_version": "castle.novel-scene-art-compiled-intent.v1",
        "renderer_release_digest": claim.renderer_release_digest,
        "render_plan_hash": claim.render_plan_hash,
        "scene_pack_hash": claim.scene_pack_hash,
        "template_id": template.template_id,
        "template_sha256": template.digest,
        "prompt_id": prompt_id,
        "output_prefix": output_prefix,
        "seed": claim.seed,
        "compiled_graph_sha256": graph_sha256,
    }
    return CompiledGraph(
        graph,
        graph_sha256,
        hashlib.sha256(canonical_json(compiled_intent)).hexdigest(),
        hashlib.sha256(template.positive_prompt.encode("utf-8", "strict")).hexdigest(),
        hashlib.sha256(template.negative_prompt.encode("utf-8", "strict")).hexdigest(),
        prompt_id,
        template.template_id,
        template.digest,
        template.output_node_id,
        output_prefix,
        claim.seed,
    )
