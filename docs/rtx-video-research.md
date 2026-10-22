# Local prompt generation and RTX video recipes

The public recipe catalog is [recipes.json](../manifests/community_recipes/recipes.json). It contains editable settings, source references and explicit prerequisites. Recipe visibility is not runtime qualification: no local LLM, renderer, CUDA install or GPU benchmark was run for this research.

## RTX Spark video experiment

[DSLab's October 14 video article](https://dslab.tw/rtx-spark-video) concerns an RTX Spark N1X Windows ARM64 laptop, not a SparkVSR upscaler. Its pipeline alternates a local multimodal LLM and H3: 736×416 first pass, a 1.3× latent upscale to 960×544, second pass, then RTX super resolution to 1920×1088. It reports 260 frames at 24 fps and 480–502 seconds over three runs. The beta schedule has eight steps split four/four. Those measurements apply to that configuration; the separate 5090 comparison uses a different, single-pass test.

Its Windows-specific settings include the factory GPU reservation, a 96 GB pagefile, disabled pinned memory and preventing laptop standby. These are observations for that machine, not Linux defaults. Its HDR-treated output was still 8-bit, so it did not establish an HDR delivery workflow. The useful portable concepts are serial model residency, staging resolution and measuring complete tasks. [Source](https://dslab.tw/rtx-spark-video).

The `h3-two-pass-1080p` recipe records the stage settings as a starting point. Preserve the scheduler's continuous sigma sequence between passes and use an H3-compatible latent upscaler; resizing decoded frames between the samplers is a different operation. Verify the selected H3 variant's current frame grid and sampler inputs rather than silently rounding the article's settings. Keep original audio, duration and frame count through super resolution. For strict 1920×1080 delivery, choose an explicit crop/pad policy after the 1920×1088 intermediate; do not label 1088 pixels as exact 1080.

## Local LLM and user-selected APIs

[DSLab's October 13 LLM article](https://dslab.tw/rtx-spark-llm) compares task success and end-to-end time as well as decode/prefill rates. It recommends stable prompt prefixes for cache reuse and measures power-state effects. Its illustrated server uses a local OpenAI-compatible endpoint, a 64K context, quantized KV caches and MTP. The exact model/build and speculative settings need requalification on another system; they are not universal tuning values.

The catalog's director recipe accepts a configurable base URL, model ID and runtime credential reference. It does not force a cloud provider or embed a token. A local example is `http://127.0.0.1:8080/v1`; remote APIs remain a user choice. Vision requests require a matching projector/model and a server that accepts images. Treat model-generated prompt text as data, validate required H3 fields before enqueueing, and retain the user's opportunity to edit the result.

Separate prompt generation, model release, video generation and postprocessing as queue stages. Automatic LLM release must be confirmed by the server's supported idle/unload mechanism; a fixed sleep alone cannot establish that VRAM is free. Record cold/warm latency, full task duration, prompt-validation failures, rerolls, output quality and cost per accepted clip. The catalog disables forced unload/API calls by default: it does not disrupt a model serving other users.

## NVIDIA VFX: pin comparison and migration boundary

The repository currently pins `Nvidia_RTX_Nodes_ComfyUI` at `892515e3eb9a4920a131a502a047e47adca9eb0d`. The renamed upstream `ComfyUI_Nvidia_VFX_Nodes` was reviewed at `38d790ded00650681fa3645362efe28c8560cf40`. This is the same evolving extension; installing both copies risks duplicate node registrations.

| Capability | Existing pin | Reviewed newer pin |
| --- | --- | --- |
| Node IDs | `RTXVideoSuperResolution` | Same SR ID plus `RTXTrueHDR`, `RTXVideoFrameGeneration` |
| SR options | LOW/MEDIUM/HIGH/ULTRA | Additional cleanup/high-bitrate/streaming modes, strength, internal encoding |
| Runtime requirement file | Unbounded `nvidia-vfx` | `nvidia-vfx>=0.2` |
| CUDA guidance | No such constraint in its short README | README requires PyTorch cu130 or newer |

The baseline installation's cu128 wheels do not satisfy the new README requirement. Keep the existing node pin until a separate compatible environment has passed dependency, import and image tests; do not automatically replace CUDA libraries or restart the running renderer. An unbounded dependency at the older pin is also not a reproducible runtime lock. [Existing source](https://github.com/Comfy-Org/ComfyUI_Nvidia_VFX_Nodes/blob/892515e3eb9a4920a131a502a047e47adca9eb0d/__init__.py), [new source](https://github.com/Comfy-Org/ComfyUI_Nvidia_VFX_Nodes/blob/38d790ded00650681fa3645362efe28c8560cf40/__init__.py), [new requirements](https://github.com/Comfy-Org/ComfyUI_Nvidia_VFX_Nodes/blob/38d790ded00650681fa3645362efe28c8560cf40/requirements.txt).

The newer extension's source is Apache-2.0; NVIDIA's `nvidia-vfx` runtime has its own proprietary license. Preserve both boundaries. SR operates on decoded image batches without changing frame rate. HDR produces color-space-specific pixel values requiring downstream encoding metadata; ordinary SDR saving is insufficient. Frame generation requires RTX 40-series or later. Our recipes keep HDR and interpolation optional, with separate validation of audio timing and output encoding. [Official Comfy VFX overview](https://comfy.org/nvidia-vfx/), [pinned README](https://github.com/Comfy-Org/ComfyUI_Nvidia_VFX_Nodes/blob/38d790ded00650681fa3645362efe28c8560cf40/README.md).

Qualification should use a short user-owned clip: verify nonblack frames, input/output dimensions, frame count, fps, audio duration and visible detail. Test the new pack in isolation, then migrate one extension directory in a maintenance window. Native node availability and an importable example do not imply SDK/runtime compatibility on a particular OS.

## H3 courses and adapters

[DSLab's H3 course](https://dslab.tw/lesson-h3) separates basic official-node workflows from advanced community-node examples. It distinguishes image/reference roles and exact-audio copying from voice-reference prompting. Preserve these as explicit recipe fields and allow either local or user-configured API prompt assistance. The article's cloud compatibility observations are not guarantees for every custom graph.

[The advanced adapter survey](https://dslab.tw/lesson-h3-adv3) organizes camera, transformation, character, action, style and postprocessing adapters. Its key integration constraint is mode compatibility: a Ref2VA adapter, contact-sheet adapter and postprocessing adapter are not interchangeable. Use model-only loading where supported, preserve each author's trigger/sampler instructions, and qualify one adapter before stacking. A downloadable model card is not a blanket license for redistributing weights, sample media or a course graph.

The catalog links the surveyed author repositories for model-specific details. Gated files stay gated. Unknown modes and inconsistent recommendations remain unresolved rather than becoming silent defaults. Before a recipe becomes executable, record the model revision/hash, node pin, adapter key-format match and a successful minimal local render. The catalog does not claim that MOSS image LoRAs can be applied to H3 video models.
