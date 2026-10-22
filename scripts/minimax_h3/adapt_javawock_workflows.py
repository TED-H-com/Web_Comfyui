#!/usr/bin/env python3
"""Build Castle's Linux-ready copies of the pinned Javawock H3 workflows.

The files under ``workflows/minimax_h3/community/javawock`` stay byte-for-byte
upstream evidence.  This script verifies those bytes against PROVENANCE.json,
then applies only the explicit model-path substitutions below.  It never
silently aliases a missing model on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "workflows/minimax_h3/community/javawock"
LARRY_ROOT = REPO_ROOT / "workflows/minimax_h3/community/larry"
MOTION_CONTEXT_ROOT = REPO_ROOT / "workflows/minimax_h3/community/motion_context"
NATIVE_AUDIO_ROOT = REPO_ROOT / "workflows/minimax_h3/community/native_audio"
OUTPUT_ROOT = REPO_ROOT / "workflows/minimax_h3/castle"
BROWSER_ROOT = REPO_ROOT / "workflows/MiniMAX-H3"
PROVENANCE_PATH = SOURCE_ROOT / "PROVENANCE.json"
LARRY_PROVENANCE_PATH = LARRY_ROOT / "PROVENANCE.json"
MOTION_CONTEXT_PROVENANCE_PATH = MOTION_CONTEXT_ROOT / "PROVENANCE.json"
NATIVE_AUDIO_PROVENANCE_PATH = NATIVE_AUDIO_ROOT / "PROVENANCE.json"
OFFICIAL_T2V_PATH = (
    REPO_ROOT / "workflows/minimax_h3/official/video_minimax_h3_t2v.json"
)
OFFICIAL_WORKFLOW_MANIFEST_PATH = (
    REPO_ROOT / "manifests/minimax_h3/workflows.lock.json"
)

# Original three-field prompt following the Servasyy prompt-writing contract.
# The same text is pinned across every cache lane so the comparison changes
# only the model wrapper, never the conditioning.
SERVASYY_STRUCTURED_PROMPT = """integrated_multimodal_description: [Shot 1] A wide locked-off view of a rain-washed rooftop observatory at blue hour. An on-screen astronomer (S1), a calm woman in her thirties with a low clear timbre, measured pace, and neutral American accent, opens the dome while wind moves loose papers; distant city lights reflect in shallow puddles. [Shot 2] At 00:03.000, the camera cuts to a medium view, then slowly pushes in as (S1) looks through the telescope and quietly says <d>[English] There it is.</d> Preserve realistic body motion, coherent hands, and continuous lighting between shots.
overall_soundscape: Steady rooftop wind, light rain on metal, paper rustle, and the observatory motor.
non_diegetic_music: A low analog synth pad at 60 BPM sustains sparse notes, entering softly at 00:03.000 and fading at the end."""

NATIVE_AUDIO_CHAINS = {
    "chain/05_Chained.json": "h3-native-audio-05-chained-spectrum-off.json",
    "chain/06_Chained_AudioLock.json":
        "h3-native-audio-06-chained-audio-lock-spectrum-off.json",
    "chain/07_Chained_RefImage.json":
        "h3-native-audio-07-chained-ref-image-spectrum-off.json",
    "chain/08_Chained_RefImage_AudioLock.json":
        "h3-native-audio-08-chained-ref-image-audio-lock-spectrum-off.json",
}

WORKFLOWS = {
    "MiniMax_int8_FR-javano2608.1.json": "h3-face-refine-javawock-2608.1.json",
    "MiniMax_int8_I2V-javano2608.9.json": "h3-i2v-javawock-2608.9.json",
    "MiniMax_int8_R2V-javano2608.16.json": "h3-r2v-javawock-2608.16.json",
    "MiniMax_int8_TTS-javano2608.1.json": "h3-tts-javawock-2608.1.json",
}

SUBSTITUTIONS = {
    r"H3\minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors":
        "H3/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
    r"H3\minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors":
        "H3/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    r"H3\minimax_h3_ref_lora_rank_256_bf16.safetensors":
        "H3/minimax_h3_ref_lora_rank_256_bf16.safetensors",
    r"segm\Anzhc_Face_seg_640_v4_y11n.pt":
        "segm/Anzhc_Face_seg_640_v4_y11n.pt",
    # The current upstream TTS graph names a removed LightX2V v0.1 file.  Its
    # current, pinned 4-step ComfyUI successor is substituted in the adapted
    # graph itself; no deceptive filesystem alias is created.
    r"H3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors":
        "H3/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
}

EXPECTED_REPLACEMENTS = {
    # Each graph contains serialized subgraph definitions as well as the
    # visible node instances, so a selected filename can legitimately occur
    # more than once.  Pinning the exact total makes upstream graph drift fail.
    "MiniMax_int8_FR-javano2608.1.json": 4,
    "MiniMax_int8_I2V-javano2608.9.json": 2,
    "MiniMax_int8_R2V-javano2608.16.json": 6,
    "MiniMax_int8_TTS-javano2608.1.json": 2,
}

JAVA_WOCK_SOL_SOURCES = {
    "MiniMax_int8_I2V-javano2608.9.json",
    "MiniMax_int8_R2V-javano2608.16.json",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_using(
    value: Any,
    substitutions: dict[str, str],
) -> tuple[Any, int]:
    if isinstance(value, str):
        replacement = substitutions.get(value)
        return (replacement, 1) if replacement is not None else (value, 0)
    if isinstance(value, list):
        result: list[Any] = []
        count = 0
        for item in value:
            adapted, replacements = _replace_using(item, substitutions)
            result.append(adapted)
            count += replacements
        return result, count
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        count = 0
        for key, item in value.items():
            adapted, replacements = _replace_using(item, substitutions)
            result[key] = adapted
            count += replacements
        return result, count
    return value, 0


def _replace(value: Any) -> tuple[Any, int]:
    return _replace_using(value, SUBSTITUTIONS)


def _drop_serialized_preview_paths(value: Any) -> int:
    """Remove author-machine absolute preview paths from adapted copies."""

    removed = 0
    if isinstance(value, dict):
        if "fullpath" in value:
            value.pop("fullpath")
            removed += 1
        for item in value.values():
            removed += _drop_serialized_preview_paths(item)
    elif isinstance(value, list):
        for item in value:
            removed += _drop_serialized_preview_paths(item)
    return removed


def _sanitize_graph_links(workflow: dict[str, Any], source_name: str) -> int:
    """Prune serialized links whose endpoint node no longer exists.

    ComfyUI stores every edge twice: once in the graph's link table and again
    on the origin/target node slots.  Removing the optional Extend nodes must
    therefore also clean stale upstream references that already pointed at a
    missing node.  Subgraphs use ``-10``/``-20`` as their virtual input/output
    endpoints, so those two ids remain valid inside subgraph link tables.
    """

    removed = 0

    def sanitize(graph: dict[str, Any], *, subgraph: bool) -> None:
        nonlocal removed
        nodes = graph.get("nodes")
        links = graph.get("links")
        if not isinstance(nodes, list) or not isinstance(links, list):
            raise SystemExit(f"Javawock graph link table missing: {source_name}")
        valid_nodes = {
            node.get("id") for node in nodes if isinstance(node, dict)
        }
        if subgraph:
            valid_nodes.update({-10, -20})

        def link_parts(link: Any) -> tuple[Any, Any, Any]:
            if subgraph and isinstance(link, dict):
                return link.get("id"), link.get("origin_id"), link.get("target_id")
            if not subgraph and isinstance(link, list) and len(link) >= 4:
                return link[0], link[1], link[3]
            raise SystemExit(f"unsupported Javawock link shape: {source_name}")

        retained = []
        for link in links:
            _, origin, target = link_parts(link)
            if origin in valid_nodes and target in valid_nodes:
                retained.append(link)
            else:
                removed += 1
        graph["links"] = retained
        valid_links = {link_parts(link)[0] for link in retained}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            for item in node.get("inputs", []):
                if isinstance(item, dict) and item.get("link") not in (
                    None,
                    *valid_links,
                ):
                    item["link"] = None
                    removed += 1
            for item in node.get("outputs", []):
                if not isinstance(item, dict):
                    continue
                output_links = item.get("links")
                if isinstance(output_links, list):
                    filtered = [link for link in output_links if link in valid_links]
                    removed += len(output_links) - len(filtered)
                    item["links"] = filtered

    sanitize(workflow, subgraph=False)
    definitions = workflow.get("definitions", {})
    subgraphs = definitions.get("subgraphs", []) if isinstance(definitions, dict) else []
    if not isinstance(subgraphs, list):
        raise SystemExit(f"Javawock subgraphs invalid: {source_name}")
    for subgraph in subgraphs:
        if isinstance(subgraph, dict):
            sanitize(subgraph, subgraph=True)
    return removed


def _disable_redundant_loader_sage(
    workflow: dict[str, Any], source_name: str
) -> int:
    """Keep one H3-specific Sage patch instead of stacking loader + patch."""

    loaders: list[dict[str, Any]] = []
    patches = 0

    def visit(value: Any) -> None:
        nonlocal patches
        if isinstance(value, dict):
            if value.get("type") == "DiffusionModelLoaderKJ":
                loaders.append(value)
            elif value.get("type") == "MiniMaxH3MemoryEfficientSageAttentionPatch":
                patches += 1
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(workflow)
    if len(loaders) != 1 or patches != 1:
        raise SystemExit(f"Javawock Sage chain changed: {source_name}")
    widgets = loaders[0].get("widgets_values")
    if not isinstance(widgets, list) or len(widgets) < 5 or widgets[4] != "auto":
        raise SystemExit(f"Javawock loader Sage setting changed: {source_name}")
    widgets[4] = "disabled"
    return 1


def _prune_unavailable_javawock_sol_lane(
    workflow: dict[str, Any], source_name: str
) -> tuple[int, int, int]:
    """Make the verified Sage path authoritative on the pinned runtime.

    Javawock's exact I2V/R2V sources contain a lazy Sage/Sol switch.  The
    upstream Sol node still registers when its Triton kernel import fails, so
    an ``object_info``-only audit incorrectly makes that option look usable.
    Castle's pinned Triton 3.3.1 has no ``triton.tools.tensor_descriptor`` and
    ``SolAttnPatch.execute`` raises before sampling if the switch is enabled.

    Preserve the exact upstream files and remove only the unavailable Sol
    branch from generated Castle copies.  The existing Sage output is rewired
    directly into ``MiniMaxH3SigmaShift`` and the exposed Sol toggle is removed
    from both the subgraph boundary and its host node.
    """

    if source_name not in JAVA_WOCK_SOL_SOURCES:
        return 0, 0, 0
    definitions = workflow.get("definitions")
    subgraphs = definitions.get("subgraphs") if isinstance(definitions, dict) else None
    if not isinstance(subgraphs, list):
        raise SystemExit(f"Javawock definitions missing: {source_name}")
    candidates = [
        graph
        for graph in subgraphs
        if isinstance(graph, dict)
        and any(
            isinstance(node, dict) and node.get("type") == "SolAttnPatch"
            for node in graph.get("nodes", [])
        )
    ]
    if len(candidates) != 1:
        raise SystemExit(f"unexpected Javawock Sol subgraph count: {source_name}")
    subgraph = candidates[0]
    nodes = subgraph.get("nodes")
    links = subgraph.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise SystemExit(f"Javawock Sol graph invalid: {source_name}")

    def one(node_type: str) -> dict[str, Any]:
        matches = [
            node
            for node in nodes
            if isinstance(node, dict) and node.get("type") == node_type
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"unexpected Javawock {node_type} count: {source_name}"
            )
        return matches[0]

    sol = one("SolAttnPatch")
    probe = one("SolAttnBlockProbe")
    switch = one("ComfySwitchNode")
    sage = one("MiniMaxH3MemoryEfficientSageAttentionPatch")
    switch_inputs = {
        item.get("name"): item
        for item in switch.get("inputs", [])
        if isinstance(item, dict)
    }
    if set(switch_inputs) != {"on_false", "on_true", "switch"}:
        raise SystemExit(f"Javawock Sol switch schema changed: {source_name}")
    false_link_id = switch_inputs["on_false"].get("link")
    true_link_id = switch_inputs["on_true"].get("link")
    control_link_id = switch_inputs["switch"].get("link")
    switch_outputs = switch.get("outputs")
    if (
        not isinstance(switch_outputs, list)
        or len(switch_outputs) != 1
        or not isinstance(switch_outputs[0].get("links"), list)
        or len(switch_outputs[0]["links"]) != 1
    ):
        raise SystemExit(f"Javawock Sol switch output changed: {source_name}")
    downstream_link_id = switch_outputs[0]["links"][0]
    links_by_id = {
        link.get("id"): link for link in links if isinstance(link, dict)
    }
    false_link = links_by_id.get(false_link_id)
    true_link = links_by_id.get(true_link_id)
    control_link = links_by_id.get(control_link_id)
    downstream_link = links_by_id.get(downstream_link_id)
    if (
        not all(
            isinstance(link, dict)
            for link in (false_link, true_link, control_link, downstream_link)
        )
        or false_link.get("origin_id") != sage.get("id")
        or false_link.get("target_id") != switch.get("id")
        or true_link.get("origin_id") != probe.get("id")
        or true_link.get("target_id") != switch.get("id")
        or control_link.get("origin_id") != -10
        or control_link.get("target_id") != switch.get("id")
        or downstream_link.get("origin_id") != switch.get("id")
    ):
        raise SystemExit(f"Javawock Sol/Sage links changed: {source_name}")

    downstream_id = downstream_link["target_id"]
    downstream_slot = downstream_link["target_slot"]
    downstream = next(
        (
            node
            for node in nodes
            if isinstance(node, dict) and node.get("id") == downstream_id
        ),
        None,
    )
    if (
        not isinstance(downstream, dict)
        or downstream.get("type") != "MiniMaxH3SigmaShift"
        or not isinstance(downstream.get("inputs"), list)
        or downstream_slot >= len(downstream["inputs"])
        or downstream["inputs"][downstream_slot].get("link") != downstream_link_id
    ):
        raise SystemExit(f"Javawock Sage downstream changed: {source_name}")

    # Retain the Sage edge id and point it directly at SigmaShift.  Link
    # sanitation below removes every edge touching the deleted Sol nodes.
    false_link["target_id"] = downstream_id
    false_link["target_slot"] = downstream_slot
    downstream["inputs"][downstream_slot]["link"] = false_link_id
    removed_ids = {sol["id"], probe["id"], switch["id"]}
    subgraph["nodes"] = [
        node for node in nodes if node.get("id") not in removed_ids
    ]

    boundary_inputs = subgraph.get("inputs")
    if not isinstance(boundary_inputs, list):
        raise SystemExit(f"Javawock Sol boundary missing: {source_name}")
    control_indexes = [
        index
        for index, item in enumerate(boundary_inputs)
        if isinstance(item, dict)
        and item.get("name") == "switch"
        and item.get("label") == "enabled sol-attn"
    ]
    if len(control_indexes) != 1:
        raise SystemExit(f"Javawock Sol boundary changed: {source_name}")
    control_index = control_indexes[0]
    boundary_inputs.pop(control_index)

    hosts = [
        node
        for node in workflow.get("nodes", [])
        if isinstance(node, dict) and node.get("type") == subgraph.get("id")
    ]
    if len(hosts) != 1:
        raise SystemExit(f"Javawock Sol host changed: {source_name}")
    host = hosts[0]
    host_inputs = host.get("inputs")
    host_widgets = host.get("widgets_values")
    host_named = host.get("widgets_values_named")
    if (
        not isinstance(host_inputs, list)
        or not isinstance(host_widgets, list)
        or len(host_inputs) != len(host_widgets)
        or control_index >= len(host_inputs)
        or host_inputs[control_index].get("name") != "switch"
        or host_widgets[control_index] is not False
        or not isinstance(host_named, dict)
        or host_named.get("switch") is not False
    ):
        raise SystemExit(f"Javawock Sol host boundary changed: {source_name}")
    host_inputs.pop(control_index)
    host_widgets.pop(control_index)
    host_named.pop("switch")

    note_updates = 0
    for node in workflow.get("nodes", []):
        values = node.get("widgets_values") if isinstance(node, dict) else None
        if (
            not isinstance(values, list)
            or len(values) != 1
            or not isinstance(values[0], str)
            or "Sol-Attn\nhttps://github.com/kijai/ComfyUI-SolAttn_triton" not in values[0]
        ):
            continue
        values[0] = values[0].replace(
            "Sol-Attn\nhttps://github.com/kijai/ComfyUI-SolAttn_triton",
            "Sol-Attn (source-only upstream option)\n"
            "Castle removes this control from the runnable derivative because "
            "the pinned Triton runtime lacks triton.tools.tensor_descriptor. "
            "The verified SageAttention path is authoritative.",
        )
        note_updates += 1
    if note_updates != 1:
        raise SystemExit(f"Javawock Sol annotation changed: {source_name}")

    extra = workflow.setdefault("extra", {})
    if not isinstance(extra, dict):
        raise SystemExit(f"Javawock workflow metadata invalid: {source_name}")
    extra["castle_javawock"] = {
        "kind": "stable-core",
        "attention_path": "sage-only",
        "sol_attn": "removed-unavailable-on-pinned-triton",
        "exact_source_preserved": f"community/javawock/{source_name}",
    }
    return len(removed_ids), 1, note_updates


def _prune_javawock_extend_lane(workflow: dict[str, Any], source_name: str) -> int:
    """Remove the optional unreleased AddGuide continuation lane.

    The upstream snapshots stay untouched.  The published Castle I2V/R2V
    copies target stable ComfyUI 0.32.0, where ``MiniMaxH3AddGuide`` is not a
    registered core node.  The ordinary I2V/R2V sampler and its own video
    output are independent of this continuation lane.
    """

    if source_name not in {
        "MiniMax_int8_I2V-javano2608.9.json",
        "MiniMax_int8_R2V-javano2608.16.json",
    }:
        return 0
    definitions = workflow.get("definitions")
    if not isinstance(definitions, dict):
        raise SystemExit(f"Javawock definitions missing: {source_name}")
    subgraphs = definitions.get("subgraphs")
    if not isinstance(subgraphs, list):
        raise SystemExit(f"Javawock subgraphs missing: {source_name}")
    extend = [
        item
        for item in subgraphs
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].endswith(" Extend Sampler")
    ]
    if len(extend) != 1:
        raise SystemExit(f"unexpected Javawock Extend subgraph count: {source_name}")
    extend_id = extend[0].get("id")
    if not isinstance(extend_id, str):
        raise SystemExit(f"Javawock Extend subgraph id missing: {source_name}")
    if sum(
        node.get("type") == "MiniMaxH3AddGuide"
        for node in extend[0].get("nodes", [])
        if isinstance(node, dict)
    ) != 1:
        raise SystemExit(f"Javawock Extend AddGuide contract changed: {source_name}")

    nodes = workflow.get("nodes")
    links = workflow.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise SystemExit(f"Javawock top-level graph invalid: {source_name}")
    extend_nodes = [node for node in nodes if node.get("type") == extend_id]
    if len(extend_nodes) != 1:
        raise SystemExit(f"unexpected Javawock Extend instance count: {source_name}")
    extend_node_id = extend_nodes[0].get("id")

    def endpoints(link: Any) -> tuple[Any, Any, Any]:
        if isinstance(link, list) and len(link) >= 4:
            return link[0], link[1], link[3]
        if isinstance(link, dict):
            return link.get("id"), link.get("origin_id"), link.get("target_id")
        raise SystemExit(f"unsupported Javawock link shape: {source_name}")

    direct_output_ids = {
        target
        for link in links
        for _, origin, target in [endpoints(link)]
        if origin == extend_node_id
        and any(
            node.get("id") == target and node.get("type") == "VHS_VideoCombine"
            for node in nodes
        )
    }
    if len(direct_output_ids) != 2:
        raise SystemExit(f"unexpected Javawock Extend output count: {source_name}")
    removed_node_ids = {extend_node_id, *direct_output_ids}
    removed_link_ids = {
        link_id
        for link in links
        for link_id, origin, target in [endpoints(link)]
        if origin in removed_node_ids or target in removed_node_ids
    }
    workflow["nodes"] = [
        node for node in nodes if node.get("id") not in removed_node_ids
    ]
    workflow["links"] = [
        link for link in links if endpoints(link)[0] not in removed_link_ids
    ]
    for node in workflow["nodes"]:
        for item in node.get("inputs", []):
            if item.get("link") in removed_link_ids:
                item["link"] = None
        for item in node.get("outputs", []):
            output_links = item.get("links")
            if isinstance(output_links, list):
                item["links"] = [
                    link for link in output_links if link not in removed_link_ids
                ]
    definitions["subgraphs"] = [item for item in subgraphs if item is not extend[0]]

    main_outputs = [
        node
        for node in workflow["nodes"]
        if node.get("type") == "VHS_VideoCombine" and node.get("id") == 381
    ]
    if len(main_outputs) != 1 or not isinstance(
        main_outputs[0].get("widgets_values"), dict
    ):
        raise SystemExit(f"Javawock main output changed: {source_name}")
    main_values = main_outputs[0]["widgets_values"]
    main_values["save_output"] = True
    profile = "i2v" if "I2V" in source_name else "r2v"
    main_values["filename_prefix"] = f"Castle/minimax-h3/javawock-{profile}"
    return len(removed_node_ids)


def _adapt_larry_workflow(manifest: dict[str, Any]) -> None:
    provenance = _load_json(LARRY_PROVENANCE_PATH)
    record = provenance["workflow"]
    source = LARRY_ROOT / "minimax_h3_t2v_turbo.json"
    if source.stat().st_size != record["bytes"] or _sha256(source) != record["sha256"]:
        raise SystemExit("pinned Larry workflow verification failed")
    workflow = _load_json(source)
    changes = 0
    for node in workflow.get("nodes", []):
        node_type = node.get("type")
        values = node.get("widgets_values")
        if node_type == "UNETLoader" and isinstance(values, list):
            values[0] = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
            changes += 1
        elif node_type == "CLIPLoader" and isinstance(values, list):
            values[0] = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
            changes += 1
        elif node_type == "MiniMaxH3TurboLoRA" and isinstance(values, list):
            node["widgets_values"] = [
                "H3/minimax_h3_turbo_v4_step600_ema.safetensors",
                1.0,
                False,
            ]
            changes += 1
        elif node_type == "ResolutionSelector" and isinstance(values, list):
            # Larry and the official H3 guide recommend a 768px short edge.
            values[0:3] = ["16:9 (Widescreen)", 0.98, 32]
            changes += 1
        elif node_type == "SaveVideo" and isinstance(values, list):
            values[0] = "Castle/minimax-h3/turbo-600ema-6step"
            changes += 1
    if changes != 5:
        raise SystemExit(f"unexpected Larry workflow adaptation count: {changes}")

    # Follow the H3 deployment guide's per-model Sage path.  Keeping this in
    # the graph avoids globally replacing attention for unrelated workflows.
    nodes = workflow.get("nodes", [])
    links = workflow.get("links", [])
    by_id = {node.get("id"): node for node in nodes}
    turbo = by_id.get(134)
    guider = by_id.get(126)
    scheduler = by_id.get(124)
    if not all(isinstance(node, dict) for node in (turbo, guider, scheduler)):
        raise SystemExit("Larry workflow model chain changed upstream")
    for node in nodes:
        order = node.get("order")
        if isinstance(order, int) and order >= 12:
            node["order"] = order + 1
    for link in links:
        if link[0] == 250:
            link[3:5] = [136, 0]
        elif link[0] == 251:
            link[1:3] = [136, 0]
    links.append([253, 136, 0, 126, 0, "MODEL"])
    turbo["outputs"][0]["links"] = [250]
    guider["inputs"][0]["link"] = 253
    scheduler["inputs"][0]["link"] = 251
    nodes.append(
        {
            "id": 136,
            "type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
            "pos": [-1240, 4547],
            "size": [410, 30],
            "flags": {},
            "order": 12,
            "mode": 0,
            "inputs": [{"name": "model", "type": "MODEL", "link": 250}],
            "outputs": [{"name": "model", "type": "MODEL", "links": [251, 253]}],
            "properties": {
                "cnr_id": "comfyui-kjnodes",
                "ver": "6ab7e8130e449ed2c0037589bcf84146ceb7fc9c",
                "Node name for S&R": "MiniMaxH3MemoryEfficientSageAttentionPatch",
            },
            "widgets_values": [],
        }
    )
    workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), 136)
    workflow["last_link_id"] = max(int(workflow.get("last_link_id", 0)), 253)
    _drop_serialized_preview_paths(workflow)

    output_name = "h3-t2v-larry-600ema-6step.json"
    output = OUTPUT_ROOT / output_name
    output.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest["workflows"].append(
        {
            "source": "community/larry/minimax_h3_t2v_turbo.json",
            "source_sha256": record["sha256"],
            "source_revision": provenance["revision"],
            "output": output_name,
            "output_sha256": _sha256(output),
            "adaptations": [
                "pinned pruned int8 base and NVFP4 text encoder",
                "Larry v4 step600 EMA at strength 1.0",
                "6-step scheduler retained",
                "1344x768 native-quality canvas",
                "low_vram disabled for sharpness",
                "per-model KJ MiniMax H3 SageAttention patch",
            ],
        }
    )


def _adapt_servasyy_cache_workflows(manifest: dict[str, Any]) -> None:
    """Build the fixed-seed Servasyy Sage/cache matrix from the pinned template."""

    lock = _load_json(OFFICIAL_WORKFLOW_MANIFEST_PATH)
    records = {
        item.get("destination"): item
        for item in lock.get("files", [])
        if isinstance(item, dict)
    }
    record = records.get(OFFICIAL_T2V_PATH.name)
    if (
        not isinstance(record, dict)
        or OFFICIAL_T2V_PATH.stat().st_size != record.get("bytes")
        or _sha256(OFFICIAL_T2V_PATH) != record.get("sha256")
    ):
        raise SystemExit("pinned official T2V workflow verification failed")

    profiles = (
        (
            "h3-t2v-servasyy-sage-only.json",
            "none",
            "Castle/minimax-h3/servasyy/sage-only",
        ),
        (
            "h3-t2v-servasyy-sage-easycache-0.1.json",
            "easycache",
            "Castle/minimax-h3/servasyy/sage-easycache-0.1",
        ),
        (
            "h3-t2v-servasyy-sage-teacache-0.15.json",
            "teacache",
            "Castle/minimax-h3/servasyy/sage-teacache-0.15",
        ),
    )
    for output_name, cache_kind, output_prefix in profiles:
        if cache_kind not in {"none", "easycache", "teacache"}:
            raise SystemExit("invalid Servasyy cache profile")
        with_cache = cache_kind != "none"
        with_easycache = cache_kind == "easycache"
        with_teacache = cache_kind == "teacache"
        workflow = _load_json(OFFICIAL_T2V_PATH)
        subgraphs = workflow.get("definitions", {}).get("subgraphs", [])
        matching = [
            graph
            for graph in subgraphs
            if isinstance(graph, dict)
            and graph.get("name") == "Image to Video (MiniMax H3)"
        ]
        if len(matching) != 1:
            raise SystemExit("official T2V subgraph contract changed")
        subgraph = matching[0]
        nodes = subgraph.get("nodes")
        links = subgraph.get("links")
        if not isinstance(nodes, list) or not isinstance(links, list):
            raise SystemExit("official T2V subgraph is invalid")
        by_id = {node.get("id"): node for node in nodes}
        loader = by_id.get(6)
        scheduler = by_id.get(9)
        guider = by_id.get(16)
        if not all(isinstance(node, dict) for node in (loader, scheduler, guider)):
            raise SystemExit("official T2V model chain changed")
        if (
            loader.get("type") != "UNETLoader"
            or scheduler.get("type") != "BasicScheduler"
            or guider.get("type") != "BasicGuider"
        ):
            raise SystemExit("official T2V model node types changed")

        extra_nodes = 2 if with_cache else 1
        for node in nodes:
            order = node.get("order")
            if isinstance(order, int) and order >= 2:
                node["order"] = order + extra_nodes

        downstream_id = 120 if with_cache else 119
        for link in links:
            if link.get("id") in {5, 193}:
                link["origin_id"] = downstream_id
                link["origin_slot"] = 0
        if loader.get("outputs", [{}])[0].get("links") != [5, 193]:
            raise SystemExit("official T2V loader links changed")
        loader["outputs"][0]["links"] = [228]

        sage = {
            "id": 119,
            "type": "PathchSageAttentionKJ",
            "pos": [-1340, 4620],
            "size": [310, 106],
            "flags": {},
            "order": 2,
            "mode": 0,
            "inputs": [
                {"name": "model", "type": "MODEL", "link": 228},
                {
                    "name": "sage_attention",
                    "type": "COMBO",
                    "widget": {"name": "sage_attention"},
                    "link": None,
                },
                {
                    "name": "allow_compile",
                    "shape": 7,
                    "type": "BOOLEAN",
                    "widget": {"name": "allow_compile"},
                    "link": None,
                },
            ],
            "outputs": [
                {
                    "name": "MODEL",
                    "type": "MODEL",
                    "links": [229] if with_cache else [5, 193],
                }
            ],
            "properties": {
                "cnr_id": "comfyui-kjnodes",
                "ver": "6ab7e8130e449ed2c0037589bcf84146ceb7fc9c",
                "Node name for S&R": "PathchSageAttentionKJ",
            },
            "widgets_values": ["auto", False],
            "title": "Servasyy A/B — Patch Sage Attention KJ (auto)",
        }
        nodes.append(sage)
        links.append(
            {
                "id": 228,
                "origin_id": 6,
                "origin_slot": 0,
                "target_id": 119,
                "target_slot": 0,
                "type": "MODEL",
            }
        )
        if with_easycache:
            nodes.append(
                {
                    "id": 120,
                    "type": "EasyCache",
                    "pos": [-1010, 4620],
                    "size": [330, 178],
                    "flags": {},
                    "order": 3,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": 229},
                        {
                            "name": "reuse_threshold",
                            "type": "FLOAT",
                            "widget": {"name": "reuse_threshold"},
                            "link": None,
                        },
                        {
                            "name": "start_percent",
                            "type": "FLOAT",
                            "widget": {"name": "start_percent"},
                            "link": None,
                        },
                        {
                            "name": "end_percent",
                            "type": "FLOAT",
                            "widget": {"name": "end_percent"},
                            "link": None,
                        },
                        {
                            "name": "verbose",
                            "type": "BOOLEAN",
                            "widget": {"name": "verbose"},
                            "link": None,
                        },
                    ],
                    "outputs": [
                        {"name": "MODEL", "type": "MODEL", "links": [5, 193]}
                    ],
                    "properties": {
                        "cnr_id": "comfy-core",
                        "ver": "0.32.0",
                        "Node name for S&R": "EasyCache",
                    },
                    # Servasyy's measured conservative threshold. Core defaults
                    # for the start/end window are retained exactly.
                    "widgets_values": [0.1, 0.15, 0.95, False],
                    "title": "Servasyy A/B — native EasyCache (conservative 0.1)",
                }
            )
            links.append(
                {
                    "id": 229,
                    "origin_id": 119,
                    "origin_slot": 0,
                    "target_id": 120,
                    "target_slot": 0,
                    "type": "MODEL",
                }
            )
        elif with_teacache:
            nodes.append(
                {
                    "id": 120,
                    "type": "MiniMaxH3TeaCache",
                    "pos": [-1010, 4620],
                    "size": [330, 178],
                    "flags": {},
                    "order": 3,
                    "mode": 0,
                    "inputs": [
                        {"name": "model", "type": "MODEL", "link": 229},
                        {
                            "name": "rel_l1_thresh",
                            "type": "FLOAT",
                            "widget": {"name": "rel_l1_thresh"},
                            "link": None,
                        },
                        {
                            "name": "start_step",
                            "type": "INT",
                            "widget": {"name": "start_step"},
                            "link": None,
                        },
                        {
                            "name": "end_step",
                            "type": "INT",
                            "widget": {"name": "end_step"},
                            "link": None,
                        },
                        {
                            "name": "total_steps",
                            "type": "INT",
                            "widget": {"name": "total_steps"},
                            "link": None,
                        },
                    ],
                    "outputs": [
                        {"name": "MODEL", "type": "MODEL", "links": [5, 193]}
                    ],
                    "properties": {
                        "cnr_id": "ComfyUI-MiniMaxH3-TeaCache",
                        "ver": "4cbb50d69c73a19a5d6ec42c5aec1989d5a04b6f",
                        "Node name for S&R": "MiniMaxH3TeaCache",
                    },
                    "widgets_values": [0.15, 2, -2, 20],
                    "title": "Servasyy A/B — H3 TeaCache 0.15 (20 steps)",
                }
            )
            links.append(
                {
                    "id": 229,
                    "origin_id": 119,
                    "origin_slot": 0,
                    "target_id": 120,
                    "target_slot": 0,
                    "type": "MODEL",
                }
            )

        state = subgraph.get("state")
        if not isinstance(state, dict):
            raise SystemExit("official T2V subgraph state missing")
        state["lastNodeId"] = max(int(state.get("lastNodeId", 0)), downstream_id)
        state["lastLinkId"] = max(
            int(state.get("lastLinkId", 0)), 229 if with_cache else 228
        )
        workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), downstream_id)
        workflow["last_link_id"] = max(
            int(workflow.get("last_link_id", 0)), 229 if with_cache else 228
        )

        top_nodes = {node.get("id"): node for node in workflow.get("nodes", [])}
        selector = top_nodes.get(115)
        host = top_nodes.get(105)
        saver = top_nodes.get(92)
        note = top_nodes.get(116)
        if not all(isinstance(node, dict) for node in (selector, host, saver, note)):
            raise SystemExit("official T2V top-level contract changed")
        selector["widgets_values"] = ["16:9 (Widescreen)", 0.7, 32]
        host["widgets_values"][0] = SERVASYY_STRUCTURED_PROMPT
        host["widgets_values"][4] = 42
        saver["widgets_values"][0] = output_prefix
        note["widgets_values"][0] += (
            "\n\n## Castle Servasyy fixed-seed A/B\n\n"
            "This short qualification copy uses 1152x640, 124 frames, 20 steps, "
            "and seed 42. Use 362 frames to reproduce the article's 15-second "
            "cache timing benchmark. "
            + (
                "Native EasyCache is isolated at reuse_threshold=0.1; changing or "
                "removing it changes the generated result."
                if with_easycache
                else (
                    "Icyoung MiniMaxH3TeaCache is isolated at rel_l1_thresh=0.15 "
                    "with a fresh-state lifecycle overlay; changing or removing it "
                    "changes the generated result."
                    if with_teacache
                    else "This is the Sage-only control with no cache node."
                )
            )
        )
        _drop_serialized_preview_paths(workflow)
        output = OUTPUT_ROOT / output_name
        output.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        manifest["workflows"].append(
            {
                "source": "official/video_minimax_h3_t2v.json",
                "source_sha256": record["sha256"],
                "source_revision": lock["source"]["revision"],
                "output": output_name,
                "output_sha256": _sha256(output),
                "adaptations": [
                    "Servasyy-derived short fixed-seed 1152x640/124-frame/20-step A/B",
                    "KJ Patch Sage Attention auto mode",
                    (
                        "native EasyCache threshold=0.1, start=0.15, end=0.95"
                        if with_easycache
                        else (
                            "Icyoung H3 TeaCache threshold=0.15, steps=2..-2/20"
                            if with_teacache
                            else "Sage-only control with no cache"
                        )
                    ),
                    "separate namespaced output prefix",
                    "same Servasyy three-field structured prompt across all lanes",
                ],
            }
        )


def _adapt_motion_context_workflow(manifest: dict[str, Any]) -> None:
    provenance = _load_json(MOTION_CONTEXT_PROVENANCE_PATH)
    record = provenance["workflow"]
    source = MOTION_CONTEXT_ROOT / "minimax_h3_fl_ref_motion_context.json"
    if source.stat().st_size != record["bytes"] or _sha256(source) != record["sha256"]:
        raise SystemExit("pinned Motion Context workflow verification failed")
    substitutions = {
        "MiniMax_H3_ref2va_pruned_int8_convrot.safetensors":
            "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "MiniMax_H3_fl2va_pruned_int8_convrot.safetensors":
            "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "Qwen3vl-32b-minimax-h3_int8_convrot.safetensors":
            "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    }
    workflow, replacements = _replace_using(_load_json(source), substitutions)
    if replacements != 8:
        raise SystemExit(
            f"unexpected Motion Context model substitution count: {replacements}"
        )
    turbo_nodes = 0
    resolution_nodes = 0
    for node in workflow.get("nodes", []):
        values = node.get("widgets_values")
        if node.get("type") == "LoraLoaderModelOnly":
            if not isinstance(values, list) or values[:1] != [
                r"MiniMax H3\Turbo 4-step.safetensors"
            ]:
                continue
            # The generic loader cannot re-inject pruned H3 AdaLN curves.
            # Keep these optional branches muted, but make them correct if
            # explicitly enabled after the scheduler is set to 6–8 steps.
            node["type"] = "MiniMaxH3TurboLoRA"
            node["properties"] = {"Node name for S&R": "MiniMaxH3TurboLoRA"}
            node["widgets_values"] = [
                "H3/minimax_h3_turbo_v4_step600_ema.safetensors",
                1.0,
                False,
            ]
            turbo_nodes += 1
        elif node.get("type") == "ResolutionSelector":
            if not isinstance(values, list) or values != [
                "9:16 (Portrait Widescreen)", 0.4, 8
            ]:
                raise SystemExit("Motion Context resolution selector changed upstream")
            # H3 requires both dimensions to be divisible by 32.  The source
            # graph's multiple=8 computes 488x864; multiple=32 computes the
            # documented 480x864 canvas without latent truncation.
            values[2] = 32
            resolution_nodes += 1
    if turbo_nodes != 2:
        raise SystemExit(f"unexpected Motion Context Turbo node count: {turbo_nodes}")
    if resolution_nodes != 2:
        raise SystemExit(
            f"unexpected Motion Context resolution node count: {resolution_nodes}"
        )

    # The pinned upstream graph was exported while the core R2V node's prompt
    # widget was converted to an input, but the serialized input itself is
    # absent.  Link 382 consequently collides with the width slot and leaves
    # the R2V lane with a blank prompt.  Restore that converted widget and move
    # the three numeric target slots one position to their canonical schema.
    by_id = {node.get("id"): node for node in workflow.get("nodes", [])}
    r2v = by_id.get(191)
    prompt_source = by_id.get(220)
    if (
        not isinstance(r2v, dict)
        or r2v.get("type") != "MiniMaxH3ReferenceToVideo"
        or not isinstance(prompt_source, dict)
        or prompt_source.get("type") != "Text Multiline"
    ):
        raise SystemExit("Motion Context R2V prompt contract changed upstream")
    r2v_inputs = r2v.get("inputs")
    if (
        not isinstance(r2v_inputs, list)
        or len(r2v_inputs) != 15
        or r2v_inputs[12].get("name") != "width"
        or r2v_inputs[12].get("link") != 378
        or prompt_source.get("outputs", [{}])[0].get("links") != [382]
    ):
        raise SystemExit("Motion Context converted prompt input changed upstream")
    r2v_inputs.insert(
        12,
        {
            "name": "prompt",
            "type": "STRING",
            "widget": {"name": "prompt"},
            "link": 382,
        },
    )
    target_slots = {382: 12, 378: 13, 379: 14, 376: 15}
    repaired_prompt_links = 0
    for link in workflow.get("links", []):
        if not isinstance(link, list) or len(link) < 5 or link[0] not in target_slots:
            continue
        if link[3] != 191:
            raise SystemExit("Motion Context R2V target changed upstream")
        link[4] = target_slots[link[0]]
        repaired_prompt_links += 1
    if repaired_prompt_links != 4:
        raise SystemExit("Motion Context R2V prompt links incomplete")
    _drop_serialized_preview_paths(workflow)

    output_name = "h3-motion-context-fl-ref.json"
    output = OUTPUT_ROOT / output_name
    output.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest["workflows"].append(
        {
            "source": "community/motion_context/minimax_h3_fl_ref_motion_context.json",
            "source_sha256": record["sha256"],
            "source_revision": provenance["revision"],
            "output": output_name,
            "output_sha256": _sha256(output),
            "adaptations": [
                "exact installed official model filenames",
                "context_length 22 and audio_context_length 24 retained",
                "portrait selectors corrected to 480x864 with multiple=32",
                "R2V converted prompt input and canonical target slots restored",
                "Spectrum branches remain muted",
                "optional generic LoRA nodes replaced by muted Larry 600EMA nodes",
            ],
        }
    )


def _adapt_native_audio_chains(manifest: dict[str, Any]) -> None:
    """Publish explicit Spectrum-off variants of the pinned chain graphs."""

    provenance = _load_json(NATIVE_AUDIO_PROVENANCE_PATH)
    records = {
        item["path"]: item
        for item in provenance.get("workflow_assets", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for source_name, output_name in NATIVE_AUDIO_CHAINS.items():
        source = NATIVE_AUDIO_ROOT / source_name
        record = records.get(source_name)
        if (
            record is None
            or source.stat().st_size != record.get("bytes")
            or _sha256(source) != record.get("sha256")
        ):
            raise SystemExit(f"pinned native-audio workflow verification failed: {source_name}")
        workflow = _load_json(source)
        spectrum_nodes = 0
        for node in workflow.get("nodes", []):
            if node.get("type") != "SpectrumApplyMiniMaxH3":
                continue
            values = node.get("widgets_values")
            if not isinstance(values, list) or not values or values[0] is not True:
                raise SystemExit(f"native-audio Spectrum default changed: {source_name}")
            values[0] = False
            spectrum_nodes += 1
        if spectrum_nodes != 1:
            raise SystemExit(f"unexpected native-audio Spectrum count: {source_name}")

        # The upstream browser graphs carry stale sampler slot metadata: the
        # sampler's model input still references the pre-Spectrum edge, and
        # AudioLock/reference-image target slots are shifted.  Preserve every
        # branch while making the edge table and node slots agree exactly.
        samplers = [
            node
            for node in workflow.get("nodes", [])
            if node.get("type") == "H3MultishotSampler"
        ]
        spectra = [
            node
            for node in workflow.get("nodes", [])
            if node.get("type") == "SpectrumApplyMiniMaxH3"
        ]
        if len(samplers) != 1 or len(spectra) != 1:
            raise SystemExit(f"native-audio model chain changed: {source_name}")
        sampler = samplers[0]
        spectrum = spectra[0]
        spectrum_links = spectrum.get("outputs", [{}])[0].get("links")
        if not isinstance(spectrum_links, list) or len(spectrum_links) != 1:
            raise SystemExit(f"native-audio Spectrum output changed: {source_name}")
        sampler_inputs = sampler.get("inputs")
        if not isinstance(sampler_inputs, list) or not sampler_inputs:
            raise SystemExit(f"native-audio sampler inputs missing: {source_name}")
        sampler_inputs[0]["link"] = spectrum_links[0]
        links_by_id = {
            link[0]: link
            for link in workflow.get("links", [])
            if isinstance(link, list) and len(link) >= 5
        }
        schema_slots = {
            "model": 0,
            "clip": 1,
            "video_vae": 2,
            "audio_vae": 3,
            "start_image": 4,
            "exact_audio": 5,
            "reference_image": 6,
        }
        checked = 0
        for item in sampler_inputs:
            link_id = item.get("link")
            if link_id is None:
                continue
            link = links_by_id.get(link_id)
            if link is None:
                raise SystemExit(
                    f"native-audio sampler link missing: {source_name}:{link_id}"
                )
            expected_slot = schema_slots.get(item.get("name"))
            if expected_slot is None or link[3:5] != [sampler["id"], expected_slot]:
                raise SystemExit(
                    f"native-audio sampler target mismatch: {source_name}:{link_id}"
                )
            checked += 1
        _drop_serialized_preview_paths(workflow)
        output = OUTPUT_ROOT / output_name
        output.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        manifest["workflows"].append(
            {
                "source": f"community/native_audio/{source_name}",
                "source_sha256": record["sha256"],
                "source_revision": provenance["revision"],
                "output": output_name,
                "output_sha256": _sha256(output),
                "adaptations": [
                    "Spectrum explicitly disabled by default",
                    f"corrected sampler model edge and verified {checked} schema targets",
                    "author-machine absolute preview paths removed",
                    "all native H3 and AudioLock chain settings otherwise retained",
                ],
            }
        )


def _adapt_native_audio_music_video(manifest: dict[str, Any]) -> None:
    """Derive exact-audio duration from the snapped target frame count."""

    provenance = _load_json(NATIVE_AUDIO_PROVENANCE_PATH)
    source_name = "MiniMaxH3_NativeAudio_MusicVideo_TEMPLATE.json"
    records = {
        item.get("path"): item
        for item in provenance.get("workflow_assets", [])
        if isinstance(item, dict)
    }
    record = records.get(source_name)
    source = NATIVE_AUDIO_ROOT / source_name
    if (
        not isinstance(record, dict)
        or source.stat().st_size != record.get("bytes")
        or _sha256(source) != record.get("sha256")
    ):
        raise SystemExit(
            "pinned NativeAudio music-video workflow verification failed"
        )

    workflow = _load_json(source)
    by_id = {node.get("id"): node for node in workflow.get("nodes", [])}
    duration = by_id.get(132)
    frame_math = by_id.get(131)
    trim = by_id.get(144)
    note = by_id.get(116)
    if (
        not isinstance(duration, dict)
        or duration.get("type") != "PrimitiveFloat"
        or duration.get("widgets_values") != [5]
        or not isinstance(frame_math, dict)
        or frame_math.get("type") != "ComfyMathExpression"
        or frame_math.get("widgets_values")
        != [
            "max(5, round(a * 24)) + "
            "(5 - (max(5, round(a * 24)) % 17)) % 17"
        ]
        or not isinstance(trim, dict)
        or trim.get("type") != "TrimAudioDuration"
        or trim.get("widgets_values") != [0.0, 10.125]
        or not isinstance(note, dict)
    ):
        raise SystemExit(
            "NativeAudio music-video duration contract changed upstream"
        )
    # Keep the proven 124-frame target. Derive exact audio as length / 24 so
    # changing the duration control cannot make reference, target, and mux
    # intervals drift apart again. The raw 10.125-second source remains intact.
    for node in workflow["nodes"]:
        order = node.get("order")
        if isinstance(order, int) and order >= 16:
            node["order"] = order + 2
    frame_math["outputs"][1]["links"].append(292)
    duration_math = {
        "id": 146,
        "type": "ComfyMathExpression",
        "pos": [-1190, 5940],
        "size": [225, 0],
        "flags": {"collapsed": True},
        "order": 16,
        "mode": 0,
        "inputs": [
            {
                "label": "a",
                "name": "values.a",
                "type": "FLOAT,INT,BOOLEAN",
                "link": 292,
            },
            {
                "label": "b",
                "name": "values.b",
                "shape": 7,
                "type": "FLOAT,INT,BOOLEAN",
                "link": None,
            },
        ],
        "outputs": [
            {"name": "FLOAT", "type": "FLOAT", "links": [293]},
            {"name": "INT", "type": "INT", "links": []},
            {"name": "BOOL", "type": "BOOLEAN", "links": []},
        ],
        "properties": {"Node name for S&R": "ComfyMathExpression"},
        "widgets_values": ["a / 24.0"],
        "title": "Snapped frames / 24 = exact audio seconds",
    }
    workflow["nodes"].append(duration_math)
    workflow["links"].extend(
        [
            [292, 131, 1, 146, 0, "INT"],
            [293, 146, 0, 144, 2, "FLOAT"],
        ]
    )
    trim["inputs"][2]["link"] = 293
    trim["order"] = 17
    trim["widgets_values"][1] = 124 / 24
    workflow["last_node_id"] = 146
    workflow["last_link_id"] = 293
    note["widgets_values"][0] += (
        "\n\n## Castle synchronized-duration adaptation\n\n"
        "The trimmed master-audio duration is derived from the snapped target "
        "frame count divided by 24 fps. The default remains the proven "
        "124-frame target (5.1667 seconds), so reference conditioning, "
        "NativeAudioLock, and the final mux cover the same interval."
    )
    _drop_serialized_preview_paths(workflow)

    output_name = "h3-native-audio-music-video-synced.json"
    output = OUTPUT_ROOT / output_name
    output.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest["workflows"].append(
        {
            "source": f"community/native_audio/{source_name}",
            "source_sha256": record["sha256"],
            "source_revision": provenance["revision"],
            "output": output_name,
            "output_sha256": _sha256(output),
            "adaptations": [
                "proven 124-frame 17k+5 target retained at 24 fps",
                "trim duration derived from snapped target frames divided by fps",
                "reference, exact-audio latent, and final mux share one interval",
            ],
        }
    )


def _adapt_precision_tts_workflow(manifest: dict[str, Any]) -> None:
    """Create the tutorial's quality-first 20-step speech profile."""

    source_name = "h3-tts-javawock-2608.1.json"
    source = OUTPUT_ROOT / source_name
    workflow = _load_json(source)
    changes = {
        "lora_bypassed": 0,
        "scheduler": 0,
        "host_steps": 0,
        "output": 0,
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            node_type = value.get("type")
            widgets = value.get("widgets_values")
            if node_type == "LoraLoaderModelOnly" and isinstance(widgets, list):
                if widgets[:1] == [
                    "H3/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
                ]:
                    # Bypass the fast LoRA completely; setting strength zero
                    # would still make the quality profile load the checkpoint.
                    value["mode"] = 4
                    changes["lora_bypassed"] += 1
            elif node_type == "BasicScheduler" and isinstance(widgets, list):
                if widgets[:3] == ["simple", 8, 1]:
                    widgets[1] = 20
                    changes["scheduler"] += 1
            elif (
                node_type == "466b2449-e267-4ec5-9751-96c224d14021"
                and isinstance(widgets, list)
            ):
                # The subgraph exposes `steps` as boundary input #8.  Its host
                # widget overrides the scheduler's serialized default, so both
                # values must be changed for a real 20-step execution.
                if len(widgets) == 8 and widgets[-2:] == [8, 5]:
                    widgets[-2] = 20
                    changes["host_steps"] += 1
            elif node_type == "SaveAudioAdvanced" and isinstance(widgets, list):
                if widgets[:1] == ["audio/H3-TTS"]:
                    widgets[0] = "audio/H3-TTS-precision-20step"
                    changes["output"] += 1
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(workflow)
    if changes != {
        "lora_bypassed": 1,
        "scheduler": 1,
        "host_steps": 1,
        "output": 1,
    }:
        raise SystemExit(f"unexpected precision TTS adaptation: {changes}")
    output_name = "h3-tts-javawock-precision-20step.json"
    output = OUTPUT_ROOT / output_name
    output.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest["workflows"].append(
        {
            "source": f"castle/{source_name}",
            "source_sha256": _sha256(source),
            "output": output_name,
            "output_sha256": _sha256(output),
            "adaptations": [
                "LightX2V fast LoRA node bypassed",
                "stock simple scheduler raised from 8 to 20 steps",
                "Spectrum remains absent/disabled",
                "separate output prefix for an explicit A/B comparison",
            ],
        }
    )


def _publish_workflow_browser(manifest: dict[str, Any]) -> None:
    """Publish only the validated profiles into ComfyUI's workflow browser."""

    copies = {
        REPO_ROOT / "workflows/minimax_h3/official/video_minimax_h3_t2v.json":
            BROWSER_ROOT / "Official/video_minimax_h3_t2v.json",
        REPO_ROOT / "workflows/minimax_h3/official/video_minimax_h3_i2v.json":
            BROWSER_ROOT / "Official/video_minimax_h3_i2v.json",
        REPO_ROOT / "workflows/minimax_h3/official/video_minimax_h3_r2v.json":
            BROWSER_ROOT / "Official/video_minimax_h3_r2v.json",
        OUTPUT_ROOT / "h3-t2v-larry-600ema-6step.json":
            BROWSER_ROOT / "Castle/h3-t2v-larry-600ema-6step.json",
        OUTPUT_ROOT / "h3-t2v-servasyy-sage-only.json":
            BROWSER_ROOT / "Cache/h3-t2v-servasyy-sage-only.json",
        OUTPUT_ROOT / "h3-t2v-servasyy-sage-easycache-0.1.json":
            BROWSER_ROOT / "Cache/h3-t2v-servasyy-sage-easycache-0.1.json",
        OUTPUT_ROOT / "h3-t2v-servasyy-sage-teacache-0.15.json":
            BROWSER_ROOT / "Cache/h3-t2v-servasyy-sage-teacache-0.15.json",
        OUTPUT_ROOT / "h3-i2v-javawock-2608.9.json":
            BROWSER_ROOT / "Castle/h3-i2v-javawock-2608.9.json",
        OUTPUT_ROOT / "h3-r2v-javawock-2608.16.json":
            BROWSER_ROOT / "Castle/h3-r2v-javawock-2608.16.json",
        OUTPUT_ROOT / "h3-tts-javawock-2608.1.json":
            BROWSER_ROOT / "Castle/h3-tts-javawock-2608.1.json",
        OUTPUT_ROOT / "h3-tts-javawock-precision-20step.json":
            BROWSER_ROOT / "Castle/h3-tts-javawock-precision-20step.json",
        OUTPUT_ROOT / "h3-face-refine-javawock-2608.1.json":
            BROWSER_ROOT / "Castle/h3-face-refine-javawock-2608.1.json",
        OUTPUT_ROOT / "h3-motion-context-fl-ref.json":
            BROWSER_ROOT / "Chain/h3-motion-context-fl-ref.json",
        OUTPUT_ROOT / "h3-native-audio-music-video-synced.json":
            BROWSER_ROOT / "Audio/MiniMaxH3_NativeAudio_MusicVideo_TEMPLATE.json",
        REPO_ROOT / "workflows/minimax_h3/community/native_audio/RIFE_WAN_Method_Interpolation_TEMPLATE.json":
            BROWSER_ROOT / "Audio/RIFE_WAN_Method_Interpolation_TEMPLATE.json",
        OUTPUT_ROOT / "h3-native-audio-05-chained-spectrum-off.json":
            BROWSER_ROOT / "Chain/05_Chained.json",
        OUTPUT_ROOT / "h3-native-audio-06-chained-audio-lock-spectrum-off.json":
            BROWSER_ROOT / "Chain/06_Chained_AudioLock.json",
        OUTPUT_ROOT / "h3-native-audio-07-chained-ref-image-spectrum-off.json":
            BROWSER_ROOT / "Chain/07_Chained_RefImage.json",
        OUTPUT_ROOT / "h3-native-audio-08-chained-ref-image-audio-lock-spectrum-off.json":
            BROWSER_ROOT / "Chain/08_Chained_RefImage_AudioLock.json",
        SOURCE_ROOT / "llm_system_prompt_for_minimax-h3_fl2va-2608.1.txt":
            BROWSER_ROOT / "Guides/llm_system_prompt_for_minimax-h3_fl2va-2608.1.txt",
        SOURCE_ROOT / "llm_system_prompt_for_minimax-h3_ref2va-2608.1.txt":
            BROWSER_ROOT / "Guides/llm_system_prompt_for_minimax-h3_ref2va-2608.1.txt",
    }
    published: list[dict[str, Any]] = []
    for source, destination in copies.items():
        if not source.is_file():
            raise SystemExit(f"workflow-browser source missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if _sha256(source) != _sha256(destination):
            raise SystemExit(f"workflow-browser copy mismatch: {destination}")
        published.append(
            {
                "source": str(source.relative_to(REPO_ROOT)),
                "destination": str(destination.relative_to(REPO_ROOT)),
                "sha256": _sha256(destination),
            }
        )
    manifest["workflow_browser"] = published


def main() -> int:
    provenance = _load_json(PROVENANCE_PATH)
    hashes = {
        item["path"]: item["sha256"]
        for item in provenance.get("workflow_assets", [])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str)
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source_revision": provenance["sources"]["javawock_workflows"]["revision"],
        "source_directory": "workflows/minimax_h3/community/javawock",
        "adaptation": (
            "explicit Linux model paths; current LightX2V 4-step filename for TTS; "
            "stable-core Sage-only main I2V/R2V lanes; "
            "Spectrum-off native-audio defaults"
        ),
        "workflows": [],
    }

    for source_name, output_name in WORKFLOWS.items():
        source = SOURCE_ROOT / source_name
        expected_hash = hashes.get(source_name)
        if expected_hash is None or _sha256(source) != expected_hash:
            raise SystemExit(f"pinned source verification failed: {source_name}")
        adapted, replacements = _replace(_load_json(source))
        if replacements != EXPECTED_REPLACEMENTS[source_name]:
            raise SystemExit(
                f"unexpected substitution count for {source_name}: {replacements}"
            )
        pruned_nodes = _prune_javawock_extend_lane(adapted, source_name)
        (
            sol_nodes_pruned,
            sol_controls_pruned,
            sol_annotations_updated,
        ) = _prune_unavailable_javawock_sol_lane(adapted, source_name)
        loader_sage_disabled = _disable_redundant_loader_sage(
            adapted, source_name
        )
        stale_links = _sanitize_graph_links(adapted, source_name)
        preview_paths = _drop_serialized_preview_paths(adapted)
        output = OUTPUT_ROOT / output_name
        rendered = json.dumps(
            adapted,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
        output.write_text(rendered, encoding="utf-8")
        manifest["workflows"].append(
            {
                "source": source_name,
                "source_sha256": expected_hash,
                "output": output_name,
                "output_sha256": _sha256(output),
                "substitutions": replacements,
                "unreleased_extend_nodes_pruned": pruned_nodes,
                "unavailable_sol_nodes_pruned": sol_nodes_pruned,
                "unavailable_sol_controls_pruned": sol_controls_pruned,
                "sol_annotations_updated": sol_annotations_updated,
                "redundant_loader_sage_disabled": loader_sage_disabled,
                "stale_link_references_pruned": stale_links,
                "serialized_preview_paths_removed": preview_paths,
            }
        )

    _adapt_larry_workflow(manifest)
    _adapt_servasyy_cache_workflows(manifest)
    _adapt_motion_context_workflow(manifest)
    _adapt_precision_tts_workflow(manifest)
    _adapt_native_audio_chains(manifest)
    _adapt_native_audio_music_video(manifest)
    _publish_workflow_browser(manifest)

    (OUTPUT_ROOT / "ADAPTATION.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    for item in manifest["workflows"]:
        print(f"{item['output']} {item['output_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
