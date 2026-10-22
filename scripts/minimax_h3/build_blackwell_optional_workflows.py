#!/usr/bin/env python3
"""Build pinned MiniMax H3 checkpoint-comparison workflows.

The Servasyy article's official INT8 stack remains the default.  These copies
make the community Blackwell and 10Eros checkpoints testable without silently
changing the prompt, seed, resolution, frame count, sampler, or cache
configuration.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
CASTLE_ROOT = REPO_ROOT / "workflows/minimax_h3/castle"
BROWSER_ROOT = REPO_ROOT / "workflows/MiniMAX-H3/Comparison"

FL_SOURCE = CASTLE_ROOT / "h3-t2v-servasyy-sage-only.json"
R2V_SOURCE = REPO_ROOT / "workflows/minimax_h3/official/video_minimax_h3_r2v.json"

FL_SOURCE_SHA256 = (
    "b712226b68f2f6882b82636cc55f7ef727fbd9702e2fa1ddd1fbda5d86a24e00"
)
R2V_SOURCE_SHA256 = (
    "099d24eda6263854818975c7209db6f29ebfd0339936c928f12293d5ab029ffb"
)

BLACKWELL_REPOSITORY = "lilcheaty/MiniMax-H3-NVFP4"
BLACKWELL_REVISION = "8c5abfed61e1b6a170240792b65253fba1a65b7b"
BLACKWELL_BYTES = 12_528_636_800

QRUSHER_REPOSITORY = "QrusherZA/10Eros-Max-int8-convrot"
QRUSHER_REVISION = "658072dd8f6481664dc9027def160cfaf85ecbe0"
QRUSHER_MODEL = "H3/10Eros_Max_h3_fl2va_int8_convrot_test4_pruned.safetensors"
QRUSHER_BASENAME = Path(QRUSHER_MODEL).name
QRUSHER_BYTES = 20_970_365_904
QRUSHER_SHA256 = (
    "68babdec8c39afae1218ba6808fdd97ebfed0e233a6b31189c40494bbffe8814"
)

FL_INT8 = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
FL_NVFP4 = "H3/minimax_h3_fl2va_pruned_nvfp4.safetensors"
FL_NVFP4_BASENAME = Path(FL_NVFP4).name
FL_NVFP4_SHA256 = (
    "72fa9269ce551fb63ff42a32d9b46d0c122e84b4b2c511e22fa698287b088f70"
)

R2V_INT8 = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
R2V_NVFP4 = "H3/minimax_h3_ref2va_pruned_nvfp4.safetensors"
R2V_NVFP4_BASENAME = Path(R2V_NVFP4).name
R2V_NVFP4_SHA256 = (
    "c813c5eabd85e275daccbf45e6f8ac4d9d14a1827d425e5be5070c92c60b78ac"
)

FL_CONTROL = CASTLE_ROOT / "h3-t2v-article-blackwell-official-int8-control.json"
FL_NVFP4_OUTPUT = CASTLE_ROOT / "h3-t2v-article-blackwell-nvfp4-sage-only.json"
QRUSHER_OUTPUT = CASTLE_ROOT / "h3-t2v-qrusher-10eros-test4-int8-sage-only.json"
R2V_INT8_OUTPUT = CASTLE_ROOT / "h3-r2v-article-blackwell-official-int8-control.json"
R2V_NVFP4_OUTPUT = CASTLE_ROOT / "h3-r2v-article-blackwell-nvfp4.json"

FL_CONTROL_BROWSER = BROWSER_ROOT / "h3-t2v-article-blackwell-official-int8-control.json"
FL_NVFP4_BROWSER = BROWSER_ROOT / FL_NVFP4_OUTPUT.name
QRUSHER_BROWSER = BROWSER_ROOT / QRUSHER_OUTPUT.name
R2V_INT8_BROWSER = BROWSER_ROOT / R2V_INT8_OUTPUT.name
R2V_NVFP4_BROWSER = BROWSER_ROOT / R2V_NVFP4_OUTPUT.name

REFERENCE_AUDIO = "reference_voice_a.wav"
REFERENCE_AUDIO_NODE_ID = 141
REFERENCE_AUDIO_LINK_ID = 283

_BLACKWELL_WARNING = """

## Castle optional Blackwell A/B

The official INT8 ConvRot checkpoint remains Castle's default and release
baseline.  This NVFP4 checkpoint is an optional, non-release-blocking profile
for NVIDIA Blackwell GPUs.  Do not assume it has better motion or image
quality: compare it against the matching control with the same prompt, seed,
resolution, frame count, sampler, steps, and cache configuration.
""".strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_pinned(path: Path, expected_sha256: str) -> dict[str, Any]:
    actual = _sha256(path)
    if actual != expected_sha256:
        raise SystemExit(
            f"pinned workflow drift: {path}: expected {expected_sha256}, got {actual}"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"workflow root is not an object: {path}")
    return value


def _walk_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_objects(item)


def _replace_text(value: Any, replacements: list[tuple[str, str]]) -> int:
    """Apply ordered string substitutions recursively and return occurrence count."""

    count = 0
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                updated = item
                for old, new in replacements:
                    occurrences = updated.count(old)
                    updated = updated.replace(old, new)
                    count += occurrences
                value[key] = updated
            else:
                count += _replace_text(item, replacements)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                updated = item
                for old, new in replacements:
                    occurrences = updated.count(old)
                    updated = updated.replace(old, new)
                    count += occurrences
                value[index] = updated
            else:
                count += _replace_text(item, replacements)
    return count


def _nodes(workflow: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [item for item in _walk_objects(workflow) if item.get("type") == node_type]


def _one_top_node(workflow: dict[str, Any], node_type: str) -> dict[str, Any]:
    matches = [node for node in workflow.get("nodes", []) if node.get("type") == node_type]
    if len(matches) != 1:
        raise SystemExit(f"expected one top-level {node_type}, found {len(matches)}")
    return matches[0]


def _one_node(workflow: dict[str, Any], node_type: str) -> dict[str, Any]:
    matches = _nodes(workflow, node_type)
    if len(matches) != 1:
        raise SystemExit(f"expected one {node_type}, found {len(matches)}")
    return matches[0]


def _append_note(workflow: dict[str, Any], title: str, text: str) -> None:
    matches = [
        node
        for node in workflow.get("nodes", [])
        if node.get("type") == "MarkdownNote" and node.get("title") == title
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one note {title!r}, found {len(matches)}")
    widgets = matches[0].get("widgets_values")
    if not isinstance(widgets, list) or len(widgets) != 1 or not isinstance(widgets[0], str):
        raise SystemExit(f"invalid note widget: {title}")
    widgets[0] = f"{widgets[0].rstrip()}\n\n{text.strip()}"


def _blackwell_url(filename: str) -> str:
    return (
        f"https://huggingface.co/{BLACKWELL_REPOSITORY}/resolve/"
        f"{BLACKWELL_REVISION}/{filename}"
    )


def _qrusher_url(filename: str) -> str:
    return (
        f"https://huggingface.co/{QRUSHER_REPOSITORY}/resolve/"
        f"{QRUSHER_REVISION}/{filename}"
    )


def build_fl_control(source: dict[str, Any]) -> dict[str, Any]:
    """Freeze the shared Servasyy source into a comparison-only INT8 control."""

    workflow = copy.deepcopy(source)
    noise = _one_node(workflow, "RandomNoise")
    if noise.get("id") != 15 or noise.get("widgets_values") != [1, "randomize"]:
        raise SystemExit("upstream FL seed controls changed")
    noise["widgets_values"] = [1, "fixed"]
    return workflow


def build_fl_nvfp4(control: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(control)
    official_url = (
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/"
        f"diffusion_models/{FL_INT8}"
    )
    replacements = [
        (official_url, _blackwell_url(FL_NVFP4_BASENAME)),
        (FL_INT8, FL_NVFP4),
        (
            "This is the Sage-only control with no cache node.",
            "This is the Sage-only Blackwell NVFP4 candidate with no cache node.",
        ),
    ]
    replaced = _replace_text(workflow, replacements)
    if replaced != 8:
        raise SystemExit(f"FL workflow replacement count changed: {replaced}")

    loaders = _nodes(workflow, "UNETLoader")
    if len(loaders) != 1 or loaders[0].get("widgets_values", [None])[0] != FL_NVFP4:
        raise SystemExit("FL NVFP4 UNET selector was not pinned exactly once")
    if len(_nodes(workflow, "PathchSageAttentionKJ")) != 1:
        raise SystemExit("FL NVFP4 A/B must retain exactly one Sage patch")

    save = _one_top_node(workflow, "SaveVideo")
    save["widgets_values"][0] = "Castle/minimax-h3/article-blackwell/fl-nvfp4-sage-only"
    _append_note(
        workflow,
        "Note: Model Links",
        (
            f"**Pinned optional checkpoint**: `{FL_NVFP4}`  \n"
            f"Source: `{BLACKWELL_REPOSITORY}@{BLACKWELL_REVISION}`  \n"
            f"Bytes: `{BLACKWELL_BYTES}`  \nSHA-256: `{FL_NVFP4_SHA256}`"
        ),
    )
    _append_note(workflow, "Note: MiniMax H3", _BLACKWELL_WARNING)
    return workflow


def build_qrusher_10eros(control: dict[str, Any]) -> dict[str, Any]:
    """Build the fixed-seed 10Eros candidate from the exact INT8 control."""

    workflow = copy.deepcopy(control)
    official_url = (
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/"
        f"diffusion_models/{FL_INT8}"
    )
    replaced = _replace_text(
        workflow,
        [
            (official_url, _qrusher_url(QRUSHER_BASENAME)),
            (FL_INT8, QRUSHER_MODEL),
        ],
    )
    if replaced != 7:
        raise SystemExit(f"10Eros workflow replacement count changed: {replaced}")

    loaders = _nodes(workflow, "UNETLoader")
    if len(loaders) != 1 or loaders[0].get("widgets_values", [None])[0] != QRUSHER_MODEL:
        raise SystemExit("10Eros UNET selector was not pinned exactly once")
    sage = _nodes(workflow, "PathchSageAttentionKJ")
    if len(sage) != 1 or sage[0].get("widgets_values") != ["auto", False]:
        raise SystemExit("10Eros comparison must retain the verified Sage-only path")

    save = _one_top_node(workflow, "SaveVideo")
    save["widgets_values"][0] = "Castle/minimax-h3/comparison/qrusher-10eros-test4-int8-sage-only"
    _append_note(
        workflow,
        "Note: Model Links",
        (
            f"**Pinned 10Eros comparison checkpoint**: `{QRUSHER_MODEL}`  \n"
            f"Source: `{QRUSHER_REPOSITORY}@{QRUSHER_REVISION}`  \n"
            f"Bytes: `{QRUSHER_BYTES}`  \nSHA-256: `{QRUSHER_SHA256}`"
        ),
    )
    _append_note(
        workflow,
        "Note: MiniMax H3",
        (
            "## Castle fixed 10Eros comparison\n\n"
            "This candidate changes only the checkpoint, output prefix, and "
            "annotations from the official INT8 control. It keeps the exposed "
            "group seed 42 fixed "
            "and retains the verified Sage-only attention path."
        ),
    )
    return workflow


def _prepare_r2v_common(source: dict[str, Any], output_prefix: str) -> dict[str, Any]:
    workflow = copy.deepcopy(source)
    noise = _one_top_node(workflow, "RandomNoise")
    if noise.get("widgets_values") != [157368968253448, "randomize"]:
        raise SystemExit("upstream R2V seed controls changed")
    noise["widgets_values"] = [42, "fixed"]

    resolution = _one_top_node(workflow, "ResolutionSelector")
    if resolution.get("widgets_values") != ["16:9 (Widescreen)", 0.4, 32]:
        raise SystemExit("upstream R2V resolution controls changed")
    resolution["widgets_values"] = ["16:9 (Widescreen)", 0.98, 32]

    scheduler = _one_top_node(workflow, "BasicScheduler")
    if scheduler.get("widgets_values") != ["simple", 20, 1]:
        raise SystemExit("upstream R2V scheduler changed")
    generation = _one_top_node(workflow, "MiniMaxH3ReferenceToVideo")
    if generation.get("widgets_values", [None, None, None, None])[1:4] != [1344, 768, 124]:
        raise SystemExit("upstream R2V generation dimensions changed")

    if workflow.get("last_node_id") != 140 or workflow.get("last_link_id") != 282:
        raise SystemExit("upstream R2V graph IDs changed")
    if any(node.get("id") == REFERENCE_AUDIO_NODE_ID for node in workflow.get("nodes", [])):
        raise SystemExit("R2V reference-audio node ID is already occupied")
    if any(link[0] == REFERENCE_AUDIO_LINK_ID for link in workflow.get("links", [])):
        raise SystemExit("R2V reference-audio link ID is already occupied")

    audio_input = next(
        (
            item
            for item in generation.get("inputs", [])
            if item.get("name") == "ref_audios.ref_audio_0"
        ),
        None,
    )
    if audio_input is None or audio_input.get("link") is not None:
        raise SystemExit("upstream R2V standalone-audio input changed")
    audio_input["link"] = REFERENCE_AUDIO_LINK_ID

    audio_order = 13
    for node in workflow.get("nodes", []):
        if node.get("order", -1) >= audio_order:
            node["order"] += 1
    workflow["nodes"].append(
        {
            "id": REFERENCE_AUDIO_NODE_ID,
            "type": "LoadAudio",
            "pos": [-1280, 5630],
            "size": [320, 140],
            "flags": {},
            "order": audio_order,
            "mode": 0,
            "inputs": [],
            "outputs": [
                {
                    "name": "AUDIO",
                    "type": "AUDIO",
                    "links": [REFERENCE_AUDIO_LINK_ID],
                }
            ],
            "title": "Reference Audio <Audio 1> — 可在此重新選擇",
            "properties": {
                "Node name for S&R": "LoadAudio",
            },
            "widgets_values": [REFERENCE_AUDIO],
        }
    )
    workflow["links"].append(
        [
            REFERENCE_AUDIO_LINK_ID,
            REFERENCE_AUDIO_NODE_ID,
            0,
            generation["id"],
            8,
            "AUDIO",
        ]
    )
    workflow["last_node_id"] = REFERENCE_AUDIO_NODE_ID
    workflow["last_link_id"] = REFERENCE_AUDIO_LINK_ID

    save = _one_top_node(workflow, "SaveVideo")
    save["widgets_values"][0] = output_prefix
    _append_note(
        workflow,
        "Note: MiniMax H3",
        (
            "## Castle fixed R2V A/B\n\n"
            "This comparison is pinned to seed 42, 1344x768, 124 frames, "
            "the `simple` scheduler, and 20 steps."
        ),
    )
    _append_note(
        workflow,
        "Note: MiniMax H3",
        (
            "## Castle reference audio\n\n"
            f"`<Audio 1>` is connected through core `LoadAudio` node "
            f"{REFERENCE_AUDIO_NODE_ID}, defaulting to `{REFERENCE_AUDIO}`. "
            "Select or upload a different reference in that node whenever needed."
        ),
    )
    return workflow


def build_r2v_int8(source: dict[str, Any]) -> dict[str, Any]:
    workflow = _prepare_r2v_common(
        source, "Castle/minimax-h3/article-blackwell/r2v-official-int8-control"
    )
    loaders = _nodes(workflow, "UNETLoader")
    if len(loaders) != 1 or loaders[0].get("widgets_values", [None])[0] != R2V_INT8:
        raise SystemExit("official R2V control selector changed")
    _append_note(
        workflow,
        "Note: MiniMax H3",
        "This file is the official INT8 ConvRot control and remains the default.",
    )
    return workflow


def build_r2v_nvfp4(source: dict[str, Any]) -> dict[str, Any]:
    workflow = _prepare_r2v_common(
        source, "Castle/minimax-h3/article-blackwell/r2v-nvfp4-candidate"
    )
    official_url = (
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/"
        f"diffusion_models/{R2V_INT8}"
    )
    replacements = [
        (official_url, _blackwell_url(R2V_NVFP4_BASENAME)),
        (R2V_INT8, R2V_NVFP4),
    ]
    replaced = _replace_text(workflow, replacements)
    if replaced != 7:
        raise SystemExit(f"R2V workflow replacement count changed: {replaced}")
    loaders = _nodes(workflow, "UNETLoader")
    if len(loaders) != 1 or loaders[0].get("widgets_values", [None])[0] != R2V_NVFP4:
        raise SystemExit("R2V NVFP4 UNET selector was not pinned exactly once")
    _append_note(
        workflow,
        "Note: Model Links",
        (
            f"**Pinned optional checkpoint**: `{R2V_NVFP4}`  \n"
            f"Source: `{BLACKWELL_REPOSITORY}@{BLACKWELL_REVISION}`  \n"
            f"Bytes: `{BLACKWELL_BYTES}`  \nSHA-256: `{R2V_NVFP4_SHA256}`"
        ),
    )
    _append_note(workflow, "Note: MiniMax H3", _BLACKWELL_WARNING)
    return workflow


def build_documents() -> dict[Path, dict[str, Any]]:
    fl_source = _load_pinned(FL_SOURCE, FL_SOURCE_SHA256)
    fl_control = build_fl_control(fl_source)
    r2v_source = _load_pinned(R2V_SOURCE, R2V_SOURCE_SHA256)
    return {
        FL_CONTROL: fl_control,
        FL_NVFP4_OUTPUT: build_fl_nvfp4(fl_control),
        QRUSHER_OUTPUT: build_qrusher_10eros(fl_control),
        R2V_INT8_OUTPUT: build_r2v_int8(r2v_source),
        R2V_NVFP4_OUTPUT: build_r2v_nvfp4(r2v_source),
    }


def _serialized(workflow: dict[str, Any]) -> bytes:
    return (json.dumps(workflow, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> int:
    documents = build_documents()
    CASTLE_ROOT.mkdir(parents=True, exist_ok=True)
    for destination, workflow in documents.items():
        destination.write_bytes(_serialized(workflow))

    BROWSER_ROOT.mkdir(parents=True, exist_ok=True)
    for source, destination in (
        (FL_CONTROL, FL_CONTROL_BROWSER),
        (FL_NVFP4_OUTPUT, FL_NVFP4_BROWSER),
        (QRUSHER_OUTPUT, QRUSHER_BROWSER),
        (R2V_INT8_OUTPUT, R2V_INT8_BROWSER),
        (R2V_NVFP4_OUTPUT, R2V_NVFP4_BROWSER),
    ):
        shutil.copy2(source, destination)

    for path in (
        FL_CONTROL_BROWSER,
        FL_NVFP4_OUTPUT,
        QRUSHER_OUTPUT,
        R2V_INT8_OUTPUT,
        R2V_NVFP4_OUTPUT,
    ):
        print(f"{_sha256(path)}  {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
