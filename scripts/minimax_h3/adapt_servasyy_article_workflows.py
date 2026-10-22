#!/usr/bin/env python3
"""Build runnable Castle derivatives from immutable Servasyy article exports.

The RunningHub JSON snapshots are never edited.  This generator validates their
reviewed hashes, applies only documented current-runtime substitutions, embeds
derivative metadata, and publishes browser copies under a separate directory.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "workflows/minimax_h3/community/servasyy_article"
DERIVATIVE_ROOT = REPO_ROOT / "workflows/minimax_h3/castle/servasyy_article"
BROWSER_SOURCE_ROOT = REPO_ROOT / "workflows/MiniMAX-H3/Article/Source-Exact"
BROWSER_DERIVATIVE_ROOT = REPO_ROOT / "workflows/MiniMAX-H3/Article/Adapted-Current"

SOURCES = {
    "r2v": {
        "filename": "h3-r2v-runninghub-2084127823018946561.json",
        "sha256": "8ac489422009fab42c720c402c3f4c859aa8a8ec5b28556f2162fd49eae8ef3f",
        "workflow_id": "2084127823018946561",
    },
    "i2v-flf": {
        "filename": "h3-i2v-flf-runninghub-2084620086811410433.json",
        "sha256": "a49039b0ebaaa4f19cd6509b7b5ba129ac220b4dafe35df8b9c7499a225c7222",
        "workflow_id": "2084620086811410433",
    },
    "t2v": {
        "filename": "h3-t2v-runninghub-2084626786919878657.json",
        "sha256": "8b190846243edfc58ab45b82dc73a73d9163e98cf200168a602555b847059d82",
        "workflow_id": "2084626786919878657",
    },
}

OUTPUTS = {
    "r2v": "h3-r2v-servasyy-article-adapted-current.json",
    "i2v-flf": "h3-i2v-flf-servasyy-article-adapted-current.json",
    "t2v": "h3-t2v-servasyy-article-adapted-current.json",
}

I2V_V01_LORA = (
    "H3/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"
)
I2V_V1_FALLBACK_LORA = (
    "H3/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
)
I2V_V1_FALLBACK_OUTPUT = (
    "h3-i2v-flf-servasyy-article-fallback-v1.json"
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def load_source(task: str) -> tuple[dict[str, Any], bytes]:
    record = SOURCES[task]
    path = SOURCE_ROOT / record["filename"]
    payload = path.read_bytes()
    actual_hash = sha256(payload)
    if actual_hash != record["sha256"]:
        raise RuntimeError(
            f"{task}: immutable source hash changed: "
            f"expected {record['sha256']}, got {actual_hash}"
        )
    workflow = json.loads(payload)
    if not isinstance(workflow.get("nodes"), list) or not isinstance(
        workflow.get("links"), list
    ):
        raise RuntimeError(f"{task}: source is not a browser workflow")
    return workflow, payload


def node_map(workflow: dict[str, Any]) -> dict[int, dict[str, Any]]:
    nodes = {int(node["id"]): node for node in workflow["nodes"]}
    if len(nodes) != len(workflow["nodes"]):
        raise RuntimeError("duplicate workflow node id")
    return nodes


def expect_node(
    nodes: dict[int, dict[str, Any]],
    node_id: int,
    node_type: str,
    widgets: list[Any] | None = None,
) -> dict[str, Any]:
    node = nodes.get(node_id)
    if node is None or node.get("type") != node_type:
        raise RuntimeError(f"expected {node_type} node {node_id}")
    if widgets is not None and node.get("widgets_values") != widgets:
        raise RuntimeError(
            f"{node_type}#{node_id} source widgets changed: "
            f"{node.get('widgets_values')!r}"
        )
    return node


def expect_link(
    workflow: dict[str, Any],
    link_id: int,
    expected: list[Any],
) -> list[Any]:
    links = {int(link[0]): link for link in workflow["links"]}
    actual = links.get(link_id)
    if actual != expected:
        raise RuntimeError(f"link {link_id} changed: expected {expected}, got {actual}")
    return actual


def add_note(workflow: dict[str, Any], text: str) -> None:
    notes = [node for node in workflow["nodes"] if node.get("type") == "MarkdownNote"]
    if not notes:
        raise RuntimeError("source workflow has no MarkdownNote template")
    note = deepcopy(notes[0])
    new_id = max(int(node["id"]) for node in workflow["nodes"]) + 1
    note["id"] = new_id
    note["pos"] = [-760, -220]
    note["size"] = [620, 360]
    note["order"] = max(int(node.get("order", 0)) for node in workflow["nodes"]) + 1
    note["widgets_values"] = [text]
    note.setdefault("properties", {})["Node name for S&R"] = "MarkdownNote"
    workflow["nodes"].append(note)
    workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), new_id)


def set_metadata(
    workflow: dict[str, Any],
    task: str,
    substitutions: list[dict[str, str]],
    *,
    kind: str = "adapted-current",
    variant: str | None = None,
) -> None:
    extra = workflow.setdefault("extra", {})
    extra["castle_servasyy_article"] = {
        "kind": kind,
        "source_workflow_id": SOURCES[task]["workflow_id"],
        "source_sha256": SOURCES[task]["sha256"],
        "generated_by": "scripts/minimax_h3/adapt_servasyy_article_workflows.py",
        "substitutions": substitutions,
        "exact_source_unchanged": True,
    }
    if variant is not None:
        extra["castle_servasyy_article"]["variant"] = variant


def add_steps_widget(node: dict[str, Any]) -> None:
    inputs = node.setdefault("inputs", [])
    if any(item.get("name") == "steps" for item in inputs):
        return
    inputs.append(
        {
            "widget": {"name": "steps"},
            "name": "steps",
            "label": "steps",
            "type": "INT",
            "localized_name": "steps",
        }
    )


def rebuild_link_metadata(workflow: dict[str, Any]) -> None:
    incoming: dict[tuple[int, int], int] = {}
    outgoing: dict[tuple[int, int], list[int]] = {}
    for link in workflow["links"]:
        link_id, source_id, source_slot, target_id, target_slot, _ = link
        incoming[(int(target_id), int(target_slot))] = int(link_id)
        outgoing.setdefault((int(source_id), int(source_slot)), []).append(int(link_id))
    for node in workflow["nodes"]:
        node_id = int(node["id"])
        for slot, item in enumerate(node.get("inputs", [])):
            link_id = incoming.get((node_id, slot))
            if link_id is None:
                item.pop("link", None)
            else:
                item["link"] = link_id
        for slot, item in enumerate(node.get("outputs", [])):
            item["links"] = outgoing.get((node_id, slot), [])


def validate_links(workflow: dict[str, Any]) -> None:
    node_ids = {int(node["id"]) for node in workflow["nodes"]}
    link_ids: set[int] = set()
    for link in workflow["links"]:
        link_id, source_id, _, target_id, _, _ = link
        if int(link_id) in link_ids:
            raise RuntimeError(f"duplicate link id {link_id}")
        link_ids.add(int(link_id))
        if int(source_id) not in node_ids or int(target_id) not in node_ids:
            raise RuntimeError(f"link {link_id} references missing node")


def adapt_t2v() -> dict[str, Any]:
    workflow, _ = load_source("t2v")
    workflow = deepcopy(workflow)
    nodes = node_map(workflow)
    expect_node(nodes, 127, "UNETLoader", ["minimax_h3_fl2va_pruned_int8_convrot.safetensors", "default"])
    expect_node(nodes, 124, "BasicScheduler", ["simple", 20, 1])
    expect_node(nodes, 115, "ResolutionSelector", ["16:9 (Widescreen)", 0.8, 32])
    expect_node(nodes, 133, "PrimitiveFloat", [5])
    spectrum = expect_node(nodes, 134, "SpectrumSDXL", [0.3, 3, 0.1, 2, 0.25, 6, 16])
    expect_node(nodes, 135, "PathchSageAttentionKJ", ["auto", False])
    spectrum["widgets_values"] = [0.3, 3, 0.1, 2, 0.25, 6, 16, 20]
    add_steps_widget(spectrum)
    expect_node(nodes, 92, "SaveVideo")["widgets_values"][0] = (
        "video/MiniMax_H3/Article/T2V"
    )
    substitutions = [
        {
            "field": "SpectrumSDXL.steps",
            "source": "implicit/default widget absent in export",
            "adapted": "20, matching BasicScheduler#124",
            "reason": "pinned Spectrum revision exposes an explicit steps widget",
        },
        {
            "field": "SaveVideo.filename_prefix",
            "source": "video/MiniMax_H3",
            "adapted": "video/MiniMax_H3/Article/T2V",
            "reason": "keep article qualification outputs separate",
        },
    ]
    set_metadata(workflow, "t2v", substitutions)
    add_note(
        workflow,
        "# Castle adapted-current — Servasyy article T2V\n\n"
        "This is a derivative, not the immutable RunningHub export. It preserves "
        "the official FL2VA INT8 model, NVFP4 AWQ text encoder, 20-step simple "
        "scheduler, res_multistep sampler, 0.8 MP 16:9 canvas, 5-second duration, "
        "SageAttention, and Spectrum parameters `[0.3, 3, 0.1, 2, 0.25, 6, 16]`. "
        "The only numerical compatibility addition is Spectrum `steps=20`.\n\n"
        f"Exact source SHA-256: `{SOURCES['t2v']['sha256']}`",
    )
    rebuild_link_metadata(workflow)
    validate_links(workflow)
    return workflow


def adapt_r2v() -> dict[str, Any]:
    workflow, _ = load_source("r2v")
    workflow = deepcopy(workflow)
    nodes = node_map(workflow)
    expect_node(nodes, 127, "UNETLoader", ["minimax_h3_ref2va_pruned_int8_convrot.safetensors", "default"])
    expect_node(nodes, 124, "BasicScheduler", ["simple", 25, 1])
    expect_node(nodes, 115, "ResolutionSelector", ["16:9 (Widescreen)", 0.4, 32])
    expect_node(nodes, 132, "PrimitiveFloat", [10])
    expect_node(nodes, 146, "ModelPatchTorchSettings", [True])
    expect_node(nodes, 147, "PathchSageAttentionKJ", ["auto", True])
    spectrum = expect_node(nodes, 148, "SpectrumSDXLBatch", [0.30000000000000004, 3, 0.1, 2, 0.25000000000000006, 8, 20])
    expect_link(workflow, 284, [284, 143, 0, 136, 9, "AUDIO"])
    expect_link(workflow, 285, [285, 144, 0, 136, 10, "AUDIO"])
    spectrum["widgets_values"] = [0.3, 3, 0.1, 2, 0.25, 8, 20, 25]
    add_steps_widget(spectrum)

    input_changes = {
        137: "reference.png",
        139: "reference_person_b.jpg",
        142: "dance.png",
        143: "reference_voice_a.wav",
        144: "reference_voice_b.wav",
    }
    for node_id, filename in input_changes.items():
        values = expect_node(
            nodes, node_id, "LoadAudio" if node_id in {143, 144} else "LoadImage"
        )["widgets_values"]
        values[0] = filename
    expect_node(nodes, 92, "SaveVideo")["widgets_values"][0] = (
        "video/MiniMax_H3/Article/R2V"
    )
    substitutions = [
        {
            "field": "SpectrumSDXLBatch implementation",
            "source": "RunningHub-only class name",
            "adapted": "alias to the pinned upstream batch-safe SpectrumSDXL class",
            "reason": "upstream commit does not export the historical Batch name",
        },
        {
            "field": "SpectrumSDXLBatch.steps",
            "source": "implicit/default widget absent in export",
            "adapted": "25, matching BasicScheduler#124",
            "reason": "pinned Spectrum revision exposes an explicit steps widget",
        },
        {
            "field": "LoadImage/LoadAudio filenames",
            "source": "RunningHub-private sample filenames",
            "adapted": "existing Castle ComfyUI input files",
            "reason": "public export does not provide the original input bytes",
        },
        {
            "field": "SaveVideo.filename_prefix",
            "source": "video/MiniMax_H3",
            "adapted": "video/MiniMax_H3/Article/R2V",
            "reason": "keep article qualification outputs separate",
        },
    ]
    set_metadata(workflow, "r2v", substitutions)
    add_note(
        workflow,
        "# Castle adapted-current — Servasyy article R2V\n\n"
        "This derivative preserves Ref2VA INT8 + NVFP4 AWQ, ModelPatch fp16 "
        "accumulation, Sage `[auto, true]`, SpectrumBatch "
        "`[0.3, 3, 0.1, 2, 0.25, 8, 20]`, 25 simple steps, res_multistep, "
        "0.4 MP 16:9, 10 seconds, three image links and two standalone audio "
        "links. The five private RunningHub inputs are replaced by existing local "
        "Comfy inputs; replace them and update the prompt tags for production.\n\n"
        f"Exact source SHA-256: `{SOURCES['r2v']['sha256']}`",
    )
    rebuild_link_metadata(workflow)
    validate_links(workflow)
    return workflow


def adapt_i2v_flf(*, use_v1_fallback: bool = False) -> dict[str, Any]:
    workflow, _ = load_source("i2v-flf")
    workflow = deepcopy(workflow)
    nodes = node_map(workflow)
    expect_node(nodes, 129, "UNETLoader", ["minimax_h3_fl2va_pruned_int8_convrot.safetensors", "default"])
    lora = expect_node(
        nodes,
        140,
        "LoraLoaderModelOnly",
        ["minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors", 0.7000000000000002],
    )
    expect_node(nodes, 115, "ResolutionSelector", ["9:16 (Portrait Widescreen)", 1, 32])
    expect_node(nodes, 135, "PrimitiveFloat", [5])
    scheduler = expect_node(nodes, 126, "BasicScheduler", ["simple", 10, 1])
    sampler = expect_node(nodes, 125, "KSamplerSelect", ["res_multistep"])
    t8_sampler = expect_node(nodes, 143, "MiniMaxH3MultiRateSamplerEXPT8", [4, 12, 12, 3])
    expect_node(nodes, 145, "MiniMaxH3BlockCacheT8")
    spectrum = expect_node(nodes, 147, "SpectrumSDXL", [0.30000000000000004, 3, 0.15000000000000002, 2, 0.25, 5, 12])
    expect_link(workflow, 244, [244, 114, 0, 133, 2, "IMAGE"])

    lora_name = I2V_V1_FALLBACK_LORA if use_v1_fallback else I2V_V01_LORA
    lora["widgets_values"] = [lora_name, 0.7]
    scheduler["mode"] = 0
    scheduler["widgets_values"] = ["simple", 12, 1]
    sampler["mode"] = 0
    t8_sampler["mode"] = 4
    spectrum["widgets_values"] = [0.3, 3, 0.15, 2, 0.25, 5, 12, 12]
    add_steps_widget(spectrum)
    expect_node(nodes, 114, "LoadImage")["widgets_values"][0] = "reference.png"
    output_prefix = (
        "video/MiniMax_H3/Article/I2V-FLF-v1-fallback"
        if use_v1_fallback
        else "video/MiniMax_H3/Article/I2V-FLF"
    )
    expect_node(nodes, 92, "SaveVideo")["widgets_values"][0] = output_prefix

    rewrites = {
        263: [263, 147, 0, 128, 0, "MODEL"],
        264: [264, 125, 0, 127, 2, "SAMPLER"],
        265: [265, 126, 0, 127, 3, "SIGMAS"],
    }
    removed = {266, 279, 281}
    rewritten_links = []
    for link in workflow["links"]:
        link_id = int(link[0])
        if link_id in removed:
            continue
        rewritten_links.append(rewrites.get(link_id, link))
    workflow["links"] = rewritten_links
    workflow["nodes"] = [
        node for node in workflow["nodes"] if int(node["id"]) not in {143, 145}
    ]

    lora_substitution = {
        "field": "LoraLoaderModelOnly#140",
        "source": (
            "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors "
            "@ 0.7"
        ),
        "adapted": f"{lora_name} @ 0.7",
        "reason": (
            "explicit v1.0 comparison fallback; the primary derivative uses the "
            "public byte-verified v0.1 Comfy conversion"
            if use_v1_fallback
            else "same public Kijai v0.1 Comfy artifact, namespaced under loras/H3"
        ),
    }
    substitutions = [
        lora_substitution,
        {
            "field": "MiniMaxH3MultiRateSamplerEXPT8#143",
            "source": "active [4, 12, 12, 3] experimental T8 sampler",
            "adapted": "removed; native BasicScheduler simple/12 + res_multistep enabled",
            "reason": "the unpublished experimental T8 node is unavailable on ComfyUI 0.32",
        },
        {
            "field": "MiniMaxH3BlockCacheT8#145",
            "source": "bypassed experimental node",
            "adapted": "removed from the derivative's disconnected branch",
            "reason": "the unpublished T8 node is unavailable on ComfyUI 0.32",
        },
        {
            "field": "SpectrumSDXL.steps",
            "source": "implicit/default widget absent in export",
            "adapted": "12, matching the native scheduler substitution",
            "reason": "pinned Spectrum revision exposes an explicit steps widget",
        },
        {
            "field": "LoadImage#114",
            "source": "RunningHub-private sample filename",
            "adapted": "reference.png",
            "reason": "public export does not provide the original input bytes",
        },
        {
            "field": "SaveVideo.filename_prefix",
            "source": "video/MiniMax_H3",
            "adapted": output_prefix,
            "reason": "keep article qualification outputs separate",
        },
    ]
    metadata_kind = (
        "adapted-current-fallback-v1" if use_v1_fallback else "adapted-current"
    )
    set_metadata(
        workflow,
        "i2v-flf",
        substitutions,
        kind=metadata_kind,
        variant="lightx2v-v1-fallback" if use_v1_fallback else "lightx2v-v0.1-exact",
    )
    if use_v1_fallback:
        note_body = (
            "This A/B fallback replaces only the v0.1 LoRA with the pinned "
            "LightX2V v1.0 4-step Comfy artifact at the same 0.7 strength. "
            "The primary adapted-current workflow uses the exact-name, "
            "byte-verified Kijai v0.1 Comfy conversion. Both derivatives replace "
            "the unavailable T8 sampler with native `simple/12 + res_multistep`."
        )
        note_title = "# Castle v1 fallback — Servasyy article I2V/FLF"
    else:
        note_body = (
            "The LoRA is the public, byte-verified Kijai artifact named exactly "
            "as in the article export, kept at the original 0.7 strength. Only "
            "its local `H3/` directory prefix is added. The unpublished "
            "MiniMaxH3MultiRateSamplerEXPT8 is replaced by native "
            "`simple/12 + res_multistep`; Spectrum remains "
            "`[0.3, 3, 0.15, 2, 0.25, 5, 12]` with `steps=12`."
        )
        note_title = "# Castle adapted-current — Servasyy article I2V/FLF"
    add_note(
        workflow,
        f"{note_title}\n\n"
        f"{note_body} "
        "The 1 MP portrait canvas, 5-second duration, first-frame link, Sage "
        "chain, and available bypassed EasyCache/Sol nodes are preserved. The "
        "unavailable experimental T8 sampler and disconnected T8 cache node are "
        "retained only in the immutable exact-source snapshot.\n\n"
        f"Exact source SHA-256: `{SOURCES['i2v-flf']['sha256']}`",
    )
    rebuild_link_metadata(workflow)
    validate_links(workflow)
    return workflow


def serialize(workflow: dict[str, Any]) -> bytes:
    return (json.dumps(workflow, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> int:
    builders = {
        "r2v": adapt_r2v,
        "i2v-flf": adapt_i2v_flf,
        "t2v": adapt_t2v,
    }
    for task, record in SOURCES.items():
        source_path = SOURCE_ROOT / record["filename"]
        source_payload = source_path.read_bytes()
        if sha256(source_payload) != record["sha256"]:
            raise RuntimeError(f"{task}: source hash changed before publish")
        atomic_write(BROWSER_SOURCE_ROOT / record["filename"], source_payload)

        workflow = builders[task]()
        payload = serialize(workflow)
        output_name = OUTPUTS[task]
        atomic_write(DERIVATIVE_ROOT / output_name, payload)
        atomic_write(BROWSER_DERIVATIVE_ROOT / output_name, payload)
        print(
            f"{task}: {output_name} {len(payload)} bytes sha256={sha256(payload)}"
        )

    fallback = adapt_i2v_flf(use_v1_fallback=True)
    fallback_payload = serialize(fallback)
    atomic_write(DERIVATIVE_ROOT / I2V_V1_FALLBACK_OUTPUT, fallback_payload)
    atomic_write(
        BROWSER_DERIVATIVE_ROOT / I2V_V1_FALLBACK_OUTPUT, fallback_payload
    )
    print(
        "i2v-flf-v1-fallback: "
        f"{I2V_V1_FALLBACK_OUTPUT} {len(fallback_payload)} bytes "
        f"sha256={sha256(fallback_payload)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
