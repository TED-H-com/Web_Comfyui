# Community workflows

The collection includes **24 editable, GPL-3.0 licensed workflows** and a searchable
index of **233 upstream workflows**: 139 from dseditor and 94 from ecjojo. Each
catalog entry records its source revision, file hash, category, node types and
explicit model filenames. Style dictionaries and configuration JSON are excluded.
The WebUI can consume `manifests/community_workflows/catalog.json`; only entries
with `distribution: vendored` have local files. References retain their source
links, including workflows with configurable external API integrations.

## Sources and redistribution

| Source | Pinned commit | Treatment |
| --- | --- | --- |
| [dseditor/ComfyuiWorkflows](https://github.com/dseditor/ComfyuiWorkflows/tree/b2ea26fa602d6eebadb1de1e4ccf1ecc91875806) | `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806` | 24 selected GPL-3.0 derivatives; remaining 115 indexed |
| [ecjojo/ComfyUI-Templates](https://github.com/ecjojo/ComfyUI-Templates/tree/adc9ef7659db315053fd1b9863ca285fa3fcccb1) | `adc9ef7659db315053fd1b9863ca285fa3fcccb1` | 94 source references; no JSON redistribution license found |

Keep [the full GPL license](../third_party/community/dseditor/LICENSE) and
[attribution/modification notice](../third_party/community/dseditor/NOTICE.md)
with the dseditor files in public copies. Model weights and custom nodes have
their own licenses. ecjojo's invitation to study techniques is respected through
source references and dependency descriptions; it is not treated as a blanket
redistribution grant. The importer refuses links-only entries.

## Selected workflows

All paths below are under `workflows/community/dseditor/`. **Static validation is
not GPU qualification.** Models and custom nodes are not installed by this import.

| File | Purpose | Principal dependencies and setup |
| --- | --- | --- |
| `flux-schnell-upscale.json` | Flux Schnell generation with SDXL upscale | Flux Schnell, T5/CLIP-L, AE, RealVisXL Lightning and UltraSharp; efficiency nodes, FL slices, prompt styler, UltimateSDUpscale |
| `flux-dev-fp8-upscale.json` | Flux Dev FP8 generation with upscale | Flux Dev FP8 checkpoint, RealVisXL Lightning, UltraSharp; efficiency nodes, FL slices, UltimateSDUpscale |
| `flux-fill-outpaint.json` | Expand an image using Flux Fill | ComfyUI-GGUF, Flux Fill Q8, T5 Q8, CLIP-L, AE; upload an image and set padding |
| `flux-canny-depth.json` | Switch between line and depth guidance | GGUF, ControlNet preprocessors, essentials; Flux Dev Q8 and depth/canny/8-step LoRAs |
| `flux-redux-advanced.json` | Reference image composition | GGUF, ReduxAdvanced, DetailDaemon, FL nodes, LayerStyle; Redux and SigLIP vision models |
| `flux-redux-inpaint.json` | Reference-guided inpainting | GGUF, Redux/vision models, Flux Fill; upload reference and masked source images |
| `qwen-image-2512-gguf.json` | Qwen image generation | GGUF, Qwen Image 2512 Q8, Qwen2.5 VL Q8, Qwen VAE, Lightning LoRA |
| `qwen-image-layered.json` | Decompose an image into layers | GGUF and KJNodes; Qwen Image Layered, layered VAE, Qwen VL, edit Lightning LoRA; attention backend requires testing |
| `zimage-tiled-text-to-image.json` | Generate and tile-upscale | KJNodes and TTP toolset; Z-image Turbo FP8, Qwen3 4B, AE, RealWebPhoto upscaler |
| `zimage-tiled-image-to-image.json` | Tile-refine an uploaded image | Same stack as text-to-image; upload source image |
| `sdxl-golden-noise.json` | SDXL noise refinement experiment | NPNetGoldenNoise plus its own weights; DreamShaper XL Lightning checkpoint |
| `cosxl-style-edit.json` | Instruction-based style editing | CosXL Edit checkpoint, essentials, wolfden styler; upload source image |
| `sdxl-hand-fix.json` | Detect and repair hands | Impact/Inspire/efficiency packs, MeshGraphormer preprocessor; HelloWorld XL and depth Control-LoRA |
| `material-transfer.json` | Transfer reference materials to regions | Impact, IPAdapter Plus, LayerStyle, use-everywhere, JW resize, ZeST; Hyper SDXL and IPAdapter/SAM models |
| `hunyuan-fast-text-to-video.json` | Local FastVideo generation | HunyuanVideoWrapper, VideoHelperSuite, FastVideo 720 FP8, Hunyuan VAE, LLaVA/CLIP encoders; attention backend and memory need testing |
| `sdxl-background-differential.json` | Replace image backgrounds | Efficiency nodes, JW resize, LayerStyle/BiRefNet and prompt styler; DreamShaper XL |
| `sd15-background-brushnet.json` | Masked background replacement | BrushNet, LayerStyle/BiRefNet and JW resize; SD 1.5 checkpoint and BrushNet weights |
| `sdxl-object-removal.json` | Segment and remove objects | Florence2, SAM2, essentials, inpaint preprocessors; SDXL and Union ControlNet |

| `sd15-iclight-background.json` | Relight a subject to match a background | Two stages: DreamShaper XL/MistoLine then SD 1.5 RealisticVision/IC-Light; IPAdapter, SAM/GroundingDINO, LayerStyle, Inspire, efficiency and WD14. Input 20 = subject, 153 = background reference; inspect masks; preview-only output |
| `sd15-pose-clothes-face-detail.json` | Pose and clothing references with face refinement | MajicMix SD 1.5, OpenPose, DWpose, IPAdapter FaceID, YOLO and Impact; input 48 = pose, 132 = face, 173 = garment. Second garment branch is bypassed upstream; inspect it before enabling. Generative reference does not guarantee exact clothing or identity |
| `sdxl-regional-three-subjects.json` | Assign references to three separate people | RealVisXL Lightning, regional IPAdapter, MistoLine/Advanced ControlNet, YOLO, LayerStyle, Inspire and use-everywhere; input 259 = layout, 77/78/422 = subjects, 183 = background. Verify mask order 0/1/2 and implicit CLIP/VAE routing; preview-only output |
| `sdxl-lineart-pvc-style.json` | Convert a reference to a PVC figure style while guiding outlines | PVCStyleModelMovable beta27, AnyLine/MistoLine, IPAdapter, Advanced ControlNet, DetailDaemon, AlignYourSteps, PAG and WD14; input 88 drives structure and appearance. Review automatic tags before generation |
| `flux-fill-subject-detail.json` | Fill a masked edit, then refine cropped subject detail | Flux Fill and Dev Q8, T5 Q8, improved CLIP-L, AE, 8-step LoRA and Nomos upscaler; GGUF, LayerStyle, YOLO/matting, essentials. Upload a masked image, inspect person-region crop and restored edge; not a face-specific detector |
| `sdxl-prompt-list-comparison.json` | Compare prompt variants with visible image notes | RealVisXL V5 Lightning, Comfyroll prompt list, WAS text/random nodes, FL ImageNotes and custom-scripts; replace prompt list, fix seed and start index for fair comparison. Preview-only output |

These six additions retain their existing catalog IDs, so source-reference links
continue to resolve after import. Their pinned hashes and `distribution` fields
are the repeatable selection; no unpinned discovery is used by the importer.
The line-art coloring source was left reference-only after inspection found a
stale output link and an opaque hosted checkpoint ID. No graph was repaired by
silently guessing its checkpoint or removing broken connections.

Exact node IDs and model selections are in each manifest entry. Some older graphs
do not declare their custom-node package IDs. Their complete `node_types` list is
authoritative for identifying missing nodes; package names above are setup hints,
not a promise of compatibility with today's package versions. `models` records
explicit filenames, not implicit weights downloaded internally by a node.
Model placement hints are relative to ComfyUI's model directories; no machine
paths or model files are included. Existing `custom_nodes.txt` is not changed,
because this collection does not install or activate new dependencies.

## Browse, reproduce and check

Run these commands from the repository root with Python 3.10 or newer:

```sh
python3 scripts/community_workflows.py list
python3 scripts/community_workflows.py show dseditor-flux-fill-outpaint
python3 scripts/community_workflows.py validate
python3 scripts/community_workflows.py import
```

`import` fetches only the 24 pinned source JSON files and upstream LICENSE. It
checks source SHA-256, sanitizes deterministically, verifies the expected output
hash, and refuses to overwrite a locally edited file. For a local pinned source
checkout, add `--source-dir /path/to/ComfyuiWorkflows`. It never fetches models,
images, video, or ecjojo source files. `--root` selects another destination that
already contains the catalog, allowing the same collection in private/public
repositories. Existing files with identical bytes are left intact.

For a node-availability check against a running local ComfyUI instance:

```sh
mkdir -p "$HOME/agent-work/tmp/community-check"
curl --fail http://127.0.0.1:8188/object_info -o "$HOME/agent-work/tmp/community-check/object_info.json"
python3 scripts/community_workflows.py validate --object-info "$HOME/agent-work/tmp/community-check/object_info.json"
rm "$HOME/agent-work/tmp/community-check/object_info.json"
```

This checks registered node names only; it does not validate every widget schema,
model availability, attention kernel, VRAM requirement, or generation quality.
Frontend-only routing/note nodes are excluded from the server-registration test.
Selected graphs are UI-format workflows: open them in ComfyUI, choose installed
model filenames, upload your own input images (`input_<node-id>.png` placeholders),
and inspect settings before queuing. Download-and-load nodes may fetch weights
when the workflow is executed, even though the importer never does so.

The sanitizer removes unused cached subgraphs, media previews and private paths,
sets portable output prefixes, and keeps graph links, prompts, model choices and
sampling parameters. Per-file attribution records these changes. No generation
has been run as part of collection validation.

Tests: `pytest-disktmp uv run pytest -q scripts/test_community_workflows.py`.
