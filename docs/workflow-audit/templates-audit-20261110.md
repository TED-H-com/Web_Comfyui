# ComfyUI-Templates：完整整合稽核 — 2026-11-10

> **歷史基準報告：** 本報告比較的是公開版本 `95e2079ed3ae47f76ff67948f80673f410abd407`，不是後續修正後的即時狀態。新修正、測試與尚未完成的執行驗證請見 [稽核索引](README.md)。上游工作流實際 GPU 驗證數仍為 **0**。

Upstream: [`adc9ef7659db315053fd1b9863ca285fa3fcccb1`](https://github.com/ecjojo/ComfyUI-Templates/tree/adc9ef7659db315053fd1b9863ca285fa3fcccb1). Public app inspected at `95e2079ed3ae47f76ff67948f80673f410abd407`; relevant file hashes are recorded in the JSON audit.

**94 graph files are indexed, 0 copied, 0 native-editor loadable through this library, 0 form-wired, 0 runner-wired, and 0 GPU-qualified.** All source hashes and stored node-type inventories match the existing pinned catalog. The absence of a redistribution license is the current import policy barrier. Source visibility does not establish a license grant.

Every JSON graph was parsed and its nodes, models, input/output links, disabled branches and explicit output paths inspected. No upstream code or workflow was executed. No model, credential or user media was accessed. The report includes all 94 paths individually below, plus README/wiki recipes and non-executable assets in the JSON companion.

## 影響使用的發現

- 93 graphs pass reciprocal-link validation; `Outdated/simplesvd_1024.json` fails with eight links referring to missing node IDs. Exact links appear in its entry.
- Three catalog entries include node types that occur only in bypassed branches: `Outdated/Any2Fullbody(2025).json`, `Outdated/SDXL_withSDUpscale.json`, and the clothing case. Stored dependencies and active dependencies need distinct fields.
- Every entry is `links-only`; `workflow_library.catalog()` enriches it without a graph, `selected()` rejects it, and the browser offers source links. The native open route uploads local JSON to ComfyUI userdata; it never queues a prompt.
- The importer skips links-only entries during bulk validate/import, so a successful bulk validation says nothing about the 94 reference graphs. A specific import is refused before fetching.
- `AIStudioShot` has a disconnected LoadImage node and generates its own subject. `talkingheadanime` creates a still image. Avatar graphs export GLB through Blender. HumanDetector tests `no_humans`; true is not a human-present flag.
- 19 graphs contain model/preset selectors that are absent from the filename-only catalog inventory. Graph-specific entries identify them; package/download licensing is separate.
- Several SVD graphs connect an SDXL-labelled LCM LoRA to SVD and obtain CLIP from an SD 1.5 checkpoint. Family/weight compatibility remains unproven despite valid JSON links.
- 55 PNG assets contain no embedded workflow/prompt graph; one has only Adobe XMP metadata. Preview media is source material, not recorded execution evidence.
- Both README collection-directory links are stale. Two per-directory READMEs are empty. The wiki file named 14Days actually contains ten showcase entries.

## 證據與限制

`pytest-disktmp uv run --with pytest pytest -q scripts/test_community_workflows.py tests/test_workflow_library_unit.py tests/test_workflow_guides_unit.py` completed with **45 passed**. These tests exercise static importer/library/guide behavior; they do not execute ecjojo graphs. Live ComfyUI node availability, installed models, schemas, GPU memory and output quality were not checked.

Runtime package names below are mapping hints inferred from graph nodes or declared metadata. They are not package installation assertions. Every graph needs its exact model/node dependency setup, input contract and runtime qualification before use. Existing guide prose for reference-only entries is metadata-derived, not a persisted detailed analysis of that graph.

## 目錄清單

| Directory | Graphs | App availability |
|---|---:|---|
| 10 Workflows for Beginner | 10 | Reference only |
| Civitai_Workflow | 8 | Reference only |
| Module | 5 | Reference only |
| Outdated | 52 | Reference only |
| Patreon_Case Examples | 2 | Reference only |
| Patreon_Workflow | 17 | Reference only |

## 匯入器與工作流庫改善項目

1. Represent separate states: source indexed, license authorized, local graph present, graph structurally validated, dependency checked, native-editor loadable, form wired, runner wired, execution qualified. A library listing is only source indexing.
2. Persist audit diagnostics for reference-only graphs without redistributing their graph bytes: broken links, active/muted node inventory, implicit models, effective input mapping and real output artifact types.
3. Replace automatic filename-to-feature assumptions with reviewed input/output contracts. AIStudioShot ignores its disconnected upload; talkingheadanime is a still image; AvatarGraphSimple exports GLB; HumanDetector tests no_humans.
4. Model records need an origin, hash/version, family, loader/preset and license. Pin custom-node package revisions; explicit filenames alone miss internal RMBG, SAM, HF classifier, WD14 and BLIP dependencies.
5. Use stable IDs for aliases/duplicate graph content. Preserve distinct variants when saved settings or topology differ; 94 paths do not imply 94 unique techniques.
6. Add explicit user-owned image/mask/video fields and a reviewed visual-to-API mapping per admitted graph. Existing /api/workflow-library/open only copies licensed local JSON to ComfyUI userdata; it does not queue a generation.
7. Gate provider routing on actual model/node/input/output capability and a durable task binding. No ecjojo graph is currently an AutoIP-ready or hosted-ready task; a ComfyDeploy input node is insufficient evidence.
8. Keep upstream previews separate from execution evidence. Record synthetic-input hash, graph hash, node/model versions, backend/device, prompt/task ID, output hash and inspected result for every execution qualification.
9. Repair stale README collection links and label empty README files and wiki showcases honestly. Preserve the no-redistribution policy until a usable license grant exists.

## README 與教學配方涵蓋範圍

The wiki was cloned separately at `713c238dcc823cbd304336bacf2c2ad17fc4b3fe`. Its two Markdown pages were read. No notebooks or executable source scripts exist in the main tree.

- [`10 Workflows for Beginner/README.md`](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/README.md): empty file; no runnable recipe.
- [`8GB VRAM available/README.md`](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/8GB%20VRAM%20available/README.md): read and inventoried; links/hashes in JSON.
- [`Patreon_Workflow/README.md`](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/README.md): empty file; no runnable recipe.
- [`README.md`](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/README.md): read and inventoried; links/hashes in JSON.
- **low-vram-startup**: Launch-flag recipe and rough model-family VRAM table, not a workflow graph. Command as documented: `python main.py --lowvram --fp16 --cpu-vae`; flags and estimates are unverified.
- **Day001 Professional Portrait**: Use a pose/background composition reference and a face reference to generate a portrait, then perform face swapping. Graph: `Patreon_Workflow/ProfessionalPortrait.json`.
- **Day002 Change Image Style**: Resize and redraw a room image while preserving Canny edges, with an SD 1.5 LCM LoRA. Graph: `Patreon_Workflow/ChangeRoomStyle.json`.
- **Day003 Anime2Realitic**: Redraw an anime image using SD 1.5, segmentation/tile controls and IPAdapter with LCM acceleration. Graph: `Patreon_Workflow/Anime2Realistic.json`.
- **Day004 Image2FullBodyAnimeCharacter**: Turn an image reference into a full-body anime character using a body-pose LoRA, adapter and face refinement. Graph: `Patreon_Workflow/FullBodyAnimeCharacter.json`.
- **Day005 Image2HalfBodyAnimeCharacter**: Generate a still half-body anime avatar with pose/reference conditioning and face detail; no speech or video animation node is present. Graph: `Patreon_Workflow/talkingheadanime.json`.
- **Day006 ChangePose**: Use a pose reference and appearance reference to generate a new pose, then apply ReActor face swapping. Graph: `Patreon_Workflow/ChangePose.json`.
- **Day007 LunarNewYearCard**: Create a reference-conditioned portrait/card using Canny control, face swap, model upscaling and a redraw stage. Graph: `Patreon_Workflow/LunarNewYearCard.json`.
- **Day008 1StepGen SDXLTurbo**: One-step SDXL Turbo text-to-image generation. Graph: `Patreon_Workflow/1StepGen_SDXLTurbo.json`.
- **Day009 2ImageCombin**: Combine two supplied reference images through sequential IPAdapter applications. Graph: `Patreon_Workflow/2IPAdapter.json`.
- **Day010 Palgirls**: Use multiple creature/image references with adapter and concatenated text-conditioning branches to generate humanoid variants. Graph: `Patreon_Workflow/Palworld2girl.json`.

External member/profile links (Patreon, Buymeacoffee, Medium, Civitai, OpenArt) are inventoried as discovery links. They are not a finite reviewed file collection and are not included in the 94-graph execution coverage count.

## Per-graph audit

Common integration result for every graph below: **indexed source reference only; not copied, not wired to a WebUI form/runner, not execution-tested**. Workflow redistribution license is undeclared. Every entry repeats its exact path/hash, purpose, graph dependencies, inputs, outputs, checks and actionable gaps. Full node IDs/sockets/model selectors and declared revisions are in the JSON companion.

### 01. 10 Workflows for Beginner/01_txt2img.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/01_txt2img.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `6f9395edafdf6f202aa94df210532f0663352e2f62eee76edaa1889f237b000a` · Git blob `b31e3239ffc1a76889ac67a245550ede2c851e2b`.

Basic SD 1.5 text-to-image generation with DreamShaper 8 at a 512px latent canvas.

Catalog ID: `ecjojo-10-workflows-for-beginner-01-txt2img-785f78dc`. Graph: 8 stored / 8 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `Note`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 02. 10 Workflows for Beginner/02_txt2img(addLora).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/02_txt2img%28addLora%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `551a70a707d6e1b580ad6b788b757955346b46f68471380edb2f8bdcd795e355` · Git blob `b08484328611461db2e36d60e36bbdf815e8d83c`.

Basic SD 1.5 text-to-image generation with an EC character/style LoRA.

Catalog ID: `ecjojo-10-workflows-for-beginner-02-txt2img-addlora-5529f51e`. Graph: 9 stored / 9 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4); `EC.safetensors` (LoraLoader #15)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 0.7000000000000001; LoraLoader #1 · strength_clip = 0.7000000000000001. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 03. 10 Workflows for Beginner/03_txt2img(SDXL Turbo).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/03_txt2img%28SDXL%20Turbo%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `59fdc8b31f7152dd37617ff8d8fd2e5039623aaa552a783ab2a0def1ffb5dbac` · Git blob `6a3c2dfb21a3ec3340908d17157cc4fc9d624d15`.

SDXL Turbo-family text-to-image generation using separate SDXL text-conditioning nodes.

Catalog ID: `ecjojo-10-workflows-for-beginner-03-txt2img-sdxl-turbo-adcaa128`. Graph: 11 stored / 11 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #28

**節點類型：** `CLIPTextEncodeSDXL`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `Note`, `PrimitiveNode`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `dreamshaperXL_turboDpmppSDE.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 1024; EmptyLatentImage #1 · height = 1024; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 8; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = dpmpp_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 04. 10 Workflows for Beginner/04_Img2Img.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/04_Img2Img.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `c3fa1edded417adbfa79f26f68197716c87489f9247e9a0f36cbbcff4f06cb0c` · Git blob `6d16eb8fd7fc4020d2a098f63200e23f2ad8f885`.

Resize and VAE-encode an uploaded image, then redraw it with partial denoising.

Catalog ID: `ecjojo-10-workflows-for-beginner-04-img2img-446265d4`. Graph: 10 stored / 10 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `Note`, `SaveImage`, `VAEDecode`, `VAEEncode`.

**明確模型選擇：** `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. JWNodes

**已存純量參數：** KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 05. 10 Workflows for Beginner/05_Img2Img(Inpaint).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/05_Img2Img%28Inpaint%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `8e08a1293c167b11e86c3c55b18adda8ebc6ee2ebf3c25481d71d703d203b253` · Git blob `a878775b3e354ab967c17b174ad1a7243fd1e02d`.

Redraw a supplied masked region using DreamShaper inpainting and VAEEncodeForInpaint.

Catalog ID: `ecjojo-10-workflows-for-beginner-05-img2img-inpaint-c36aa3f6`. Graph: 10 stored / 10 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `Note`, `SaveImage`, `VAEDecode`, `VAEEncodeForInpaint`.

**明確模型選擇：** `dreamshaper_8Inpainting.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. JWNodes

**已存純量參數：** KSampler #1 · steps = 30; KSampler #1 · cfg = 6; KSampler #1 · sampler_name = dpmpp_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 06. 10 Workflows for Beginner/06_Img2Img(Outpaint).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/06_Img2Img%28Outpaint%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `cd6d305e854fb3c9e60221d9fa6b22988a709da891743ae9798fe0828f4fdfb5` · Git blob `3c448ef046634690ccd1ae3ea2eac2abce6ed907`.

Pad an uploaded image, derive the new-area mask and generate content into the added border.

Catalog ID: `ecjojo-10-workflows-for-beginner-06-img2img-outpaint-69651949`. Graph: 11 stored / 11 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ImagePadForOutpaint`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `Note`, `SaveImage`, `VAEDecode`, `VAEEncodeForInpaint`.

**明確模型選擇：** `dreamshaper_8Inpainting.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. JWNodes

**已存純量參數：** KSampler #1 · steps = 20; KSampler #1 · cfg = 6; KSampler #1 · sampler_name = dpmpp_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; ImagePadForOutpaint #1 · left = 24; ImagePadForOutpaint #1 · top = 24; ImagePadForOutpaint #1 · right = 24; ImagePadForOutpaint #1 · bottom = 200; ImagePadForOutpaint #1 · feathering = 0. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 07. 10 Workflows for Beginner/07_Img2Img(IPA).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/07_Img2Img%28IPA%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `e0d0aeb8823b819ccefa59e6b90bd2e019d417bd8c06f160449e0067f726c920` · Git blob `c27edd5f27dabb4522cd2a9bee395859fce14f44`.

Generate from an empty latent with IPAdapter reference conditioning; the reference is not the initial image latent.

Catalog ID: `ecjojo-10-workflows-for-beginner-07-img2img-ipa-ce25a36a`. Graph: 13 stored / 13 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `Note`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4); `model.safetensors` (CLIPVisionLoader #32); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #30)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated JWNodes

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 25; KSampler #1 · cfg = 6; KSampler #1 · sampler_name = dpmpp_2m; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 08. 10 Workflows for Beginner/08_Img2Img(FaceSwap).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/08_Img2Img%28FaceSwap%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `436f6229bff798c7d79f47c1becbe10385f1c366b68933b92b935645e535a719` · Git blob `13609f1e318113e3381d8cae685c3ea8172af400`.

Swap a source face into a resized target image using ReActor/InsightFace, with CodeFormer restoration.

Catalog ID: `ecjojo-10-workflows-for-beginner-08-img2img-faceswap-aad4773f`. Graph: 6 stored / 6 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path); LoadImage #31 (effective explicit path) No diffusion text prompt inferred.

**輸出：** saved image via SaveImage #32

**節點類型：** `JWImageResizeByLongerSide`, `LoadImage`, `Note`, `ReActorFaceSwap`, `SaveImage`.

**明確模型選擇：** `inswapper_128.onnx` (ReActorFaceSwap #30); `codeformer.pth` (ReActorFaceSwap #30)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. ReActor + InsightFace/ONNXRuntime + face restore dependencies JWNodes

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 09. 10 Workflows for Beginner/09_Img2Img(Upscale).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/09_Img2Img%28Upscale%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `4b6b016139fc26505ccc511122d5219fb142ccb4e25441701d37102d0e1ca7c4` · Git blob `97c4b67bf984ccf25126211c479417558fae26cb`.

Resize an input and perform tiled diffusion upscaling with UltimateSDUpscale and UltraSharp.

Catalog ID: `ecjojo-10-workflows-for-beginner-09-img2img-upscale-3713c5fb`. Graph: 9 stored / 9 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `JWImageResizeByLongerSide`, `LoadImage`, `Note`, `SaveImage`, `UltimateSDUpscale`, `UpscaleModelLoader`.

**明確模型選擇：** `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4); `4x-UltraSharp.pth` (UpscaleModelLoader #25)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. JWNodes UltimateSDUpscale

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 10. 10 Workflows for Beginner/10_SimpleSVD.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/10%20Workflows%20for%20Beginner/10_SimpleSVD.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `db6ed67b1287d8dc1fcb88a3a16a14dcdc522432e2ea55306219e121b48993a5` · Git blob `7ff2661c9580f66824702c119834f7c625e5a601`.

Animate one image using SVD-XT, FreeU and video CFG, then interpolate frames with RIFE and encode MP4.

Catalog ID: `ecjojo-10-workflows-for-beginner-10-simplesvd-bc47fc9b`. Graph: 14 stored / 14 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) No diffusion text prompt inferred.

**輸出：** MP4/video frames via VHS_VideoCombine #26

**節點類型：** `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `FreeU_V2`, `Get image size`, `ImageOnlyCheckpointLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `LoraLoader`, `RIFE VFI`, `SVD_img2vid_Conditioning`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.

**明確模型選擇：** `rife40.pth` (RIFE VFI #30); `SVD/svd_xt.safetensors` (ImageOnlyCheckpointLoader #15); `lcm/SDXL/pytorch_lora_weights.safetensors` (LoraLoader #44); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #45)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. VideoHelperSuite and FFmpeg frame-interpolation package and RIFE checkpoint JWNodes Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** KSampler #1 · steps = 30; KSampler #1 · cfg = 1; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = sgm_uniform; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; VHS_VideoCombine #1 · frame_rate = 15. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Graph attaches an SDXL-labelled LCM LoRA to the SVD model and supplies CLIP from a separate SD 1.5 checkpoint. Weight-shape/model-family compatibility is unproven; do not assume this accelerates SVD successfully.

### 11. Civitai_Workflow/AIStudioShot.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/AIStudioShot.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `ab762413ffd679d2a6ea0c8157c9e24777a50adfa910c80ef5eee6a01510682c` · Git blob `e0badc97a99fff0bd8f939b1ef652a856f5b06ee`.

Generate a subject and background separately, segment the generated subject, composite, redraw and refine the face.

Catalog ID: `ecjojo-civitai-workflow-aistudioshot-d9dc73fd`. Graph: 38 stored / 38 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #362 (no explicit output path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #360; image preview via PreviewImage #354; saved image via SaveImage #366

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `GetImageSize+`, `ImageCompositeMasked`, `ImpactSEGSToMaskList`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByLongerSide`, `JWInteger`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SAMLoader`, `SaveImage`, `SimpleMath+`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `segm/person_yolov8m-seg.pt` (UltralyticsDetectorProvider #328); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #364); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #273); `vaeFtMse840000Ema_v100.safetensors` (VAELoader #331); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #262); `sam_vit_b_01ec64.pth` (SAMLoader #329)

**Exact graph duplicates:** `Patreon_Case Examples/BackgroundRegen/CaseExamples02_BackgroundGen.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ComfyUI Essentials JWNodes

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 0.5; KSampler #3 · steps = 10; KSampler #3 · cfg = 1.5; KSampler #3 · sampler_name = lcm; KSampler #3 · scheduler = exponential; KSampler #3 · denoise = 1; EmptyLatentImage #2 · width = 680; EmptyLatentImage #2 · height = 720; EmptyLatentImage #2 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Uploaded/source input nodes 362 do not reach any explicit output; do not advertise them as effective controls without repairing wiring.

### 12. Civitai_Workflow/AvatarGraphSimple.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/AvatarGraphSimple.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `18d66e8b8ac348b47157966f77e7f3404afa23ed37e3aa9894a04bd36ba2ac7d` · Git blob `373c4bf3498e62977a57844ed38d79a5baa6a576`.

Segment eye/mouth layers, build a textured Blender mesh and shape keys, and export a GLB avatar.

Catalog ID: `ecjojo-civitai-workflow-avatargraphsimple-5e6c199e`. Graph: 15 stored / 15 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #320 (effective explicit path) No diffusion text prompt inferred.

**輸出：** GLB avatar mesh/shape keys via AvatarMainOutput #332

**節點類型：** `AvatarMainOutput`, `CreateShapeFlow`, `LoadImage`, `Mesh_Delete`, `Mesh_JoinMesh`, `Mesh_ModifyShapeKey`, `Mesh_PrimitivePlaneAdd`, `Object_CreateMeshLayer`, `Object_MatchTextureAspectRatio`, `Object_PlaneTextureUnwrap`, `SAM MultiLayer`.

**明確模型選擇：** `sam_vit_h_4b8939.pth` (SAM MultiLayer #319)

**Exact graph duplicates:** `Outdated/AvatarGraphSimple.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. AvatarGraph/Blender bpy mesh and shape-flow environment; confirm package/version Blender/bpy environment and a mesh artifact consumer are required; GPU diffusion is not the principal operation.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- This produces a Blender/GLB artifact; image-only gallery and runner handling are insufficient. Map mesh export and shape-flow outputs explicitly.

### 13. Civitai_Workflow/Image2fullbody.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/Image2fullbody.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `23ef63d008604d6ca245b3f06d7f5e8fac4093d63c5fbd5c8686afca31fc24b6` · Git blob `72895660e76187e9f032f0f5da7a2d70133a9c81`.

Use an uploaded reference with IPAdapter, a body-pose LoRA and FaceDetailer to generate a full-body character.

Catalog ID: `ecjojo-civitai-workflow-image2fullbody-0b35bead`. Graph: 15 stored / 15 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #42

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `vtuber-poses-05.safetensors` (LoraLoader #35); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72)

**Exact graph duplicates:** `Outdated/Image2fullbody.json`, `Outdated/Image2fullbody_withupscale.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 0.6; LoraLoader #1 · strength_clip = 0.6; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 1280; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 14. Civitai_Workflow/RandomCharacter(v2).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/RandomCharacter%28v2%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `b3af162037e1c9dbf93a46ad3b0134b32ce5bd3fec2975200906544257cae343` · Git blob `f75984ccf0ff316f84568dc5e0e8e690c3868642`.

Generate randomized character prompts, mix model branches and refine faces; saves character/background outputs.

Catalog ID: `ecjojo-civitai-workflow-randomcharacter-v2-66b21cc3`. Graph: 23 stored / 23 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #17; saved image via SaveImage #85; saved image via SaveImage #53

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoraLoader`, `MMDetDetectorProvider`, `ModelMergeSimple`, `OneButtonPrompt`, `PreviewImage`, `SaveImage`, `Text to Conditioning`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `EC.safetensors` (LoraLoader #1); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #16); `yabalMixSemireal_v1.safetensors` (CheckpointLoaderSimple #2); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #52); `model.safetensors` (CLIPVisionLoader #66); `bbox/mmdet_anime-face_yolov3.pth` (MMDetDetectorProvider #45)

**Exact graph duplicates:** `Outdated/kana_randomcharacter.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies One Button Prompt

**已存純量參數：** LoraLoader #1 · strength_model = 0.6; LoraLoader #1 · strength_clip = 0.6; KSampler #1 · steps = 15; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = uni_pc; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; EmptyLatentImage #2 · width = 768; EmptyLatentImage #2 · height = 1024; EmptyLatentImage #2 · batch_size = 1; KSampler #2 · steps = 25; KSampler #2 · cfg = 9; KSampler #2 · sampler_name = dpmpp_2m; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Legacy MMDetection provider requires its own compatible detection stack; modern YOLO presence does not satisfy this node contract.

### 15. Civitai_Workflow/RoomGen(v2).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/RoomGen%28v2%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `30fc2033747d94123f523d51bdd98872ac0b0974a9c5f327cd5c676b251be248` · Git blob `b3ff79af3f145ee5a8c74b83e99c328f6ebb4171`.

Generate a base room, derive LeReS depth and combine it with a tiled style reference through IPAdapter.

Catalog ID: `ecjojo-civitai-workflow-roomgen-v2-42919007`. Graph: 26 stored / 26 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #49; image preview via PreviewImage #65; saved image via SaveImage #55; saved image via SaveImage #57

**節點類型：** `AIO_Preprocessor`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LeReS-DepthMapPreprocessor`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `control_v11f1p_sd15_depth.pth` (ControlNetLoader #47); `room.safetensors` (LoraLoader #5); `Anime/landscapeAnimePro_v20Inspiration.safetensors` (CheckpointLoaderSimple #4); `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #3); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #13); `model.safetensors` (CLIPVisionLoader #6); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #7)

**Exact graph duplicates:** `Outdated/RoomGen(with_LCMLora)_v2(Removed_BLIP).json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; LoraLoader #2 · strength_model = 0.5; LoraLoader #2 · strength_clip = 0.5; EmptyLatentImage #1 · width = 1080; EmptyLatentImage #1 · height = 608; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = euler; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 16. Civitai_Workflow/ToonGen(v2).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/ToonGen%28v2%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `7d7f7cf0cb7333328ce96aa00e0e3cd3491c28723b275377dee5980b2b4d92a6` · Git blob `e48605bd7fcfeab8a0498252b6a8ab06c3afab8f`.

Convert an image reference to a toon avatar with pose control, dual IPAdapter stages, face refinement and background removal.

Catalog ID: `ecjojo-civitai-workflow-toongen-v2-f2e2e03c`. Graph: 29 stored / 29 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path); LoadImage #18 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #125; image preview via PreviewImage #93; image preview via PreviewImage #101; saved image via SaveImage #117

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SAMLoader`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #89); `sam_vit_h_4b8939.pth` (SAMLoader #106); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #39); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #84); `toonAme_version10.safetensors` (CheckpointLoaderSimple #4); `ip-adapter-plus-face_sd15.bin` (IPAdapterModelLoader #9); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #28); `model.safetensors` (CLIPVisionLoader #8); `mouth_slider_v1.safetensors` (LoraLoader #91); `control_v11p_sd15_openpose.pth` (ControlNetLoader #17)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #98); `u2net` (Image Rembg (Remove Background) #127).

**Exact graph duplicates:** `Outdated/ToonStyleImageGen_v2(Removed_BLIP).json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; LoraLoader #2 · strength_model = 4; LoraLoader #2 · strength_clip = 4; EmptyLatentImage #1 · width = 720; EmptyLatentImage #1 · height = 720; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: u2net. Pin artifacts and licenses separately.

### 17. Civitai_Workflow/drawing2img.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/drawing2img.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `44d0c9da6cce674d1621a8577a2c102fae3d3308a6ccc29b67e69f9c707f566a` · Git blob `32c573b11b9c6f05b55c4dd888fff4bac11dcb59`.

Turn a Painter sketch into a toon image with scribble ControlNet, LCM LoRA and concatenated prompts.

Catalog ID: `ecjojo-civitai-workflow-drawing2img-e9385638`. Graph: 18 stored / 18 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** PainterNode #33 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #90

**節點類型：** `CLIPTextEncode`, `CR Prompt Text`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `PainterNode`, `SaveImage`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #20); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `control_sd15_scribble.pth` (ControlNetLoader #45)

**Exact graph duplicates:** `Outdated/drawing2img_wLCM.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. custom-scripts; WD14 tagger additionally when referenced Painter frontend extension; manually supplied sketch canvas Comfyroll nodes

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 8; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Painter depends on an interactive frontend canvas; a server-only form needs a reviewed drawing/upload adapter.

### 18. Civitai_Workflow/image2exp(v2).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Civitai_Workflow/image2exp%28v2%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d1c83402fb396b73906f03e1fc9f0a4df50275128628e34c48753af68604df9d` · Git blob `2b10a56079808f6c67a6bc41688093c419beb66e`.

Create six expression variants from a source portrait using pose conditioning, IPAdapter and separate FaceDetailer branches.

Catalog ID: `ecjojo-civitai-workflow-image2exp-v2-8ca1c6d4`. Graph: 45 stored / 45 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #64 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #42; saved image via SaveImage #46; saved image via SaveImage #43; saved image via SaveImage #44; saved image via SaveImage #45; image preview via PreviewImage #73; saved image via SaveImage #80

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SaveImage`, `UltralyticsDetectorProvider`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `control_v11p_sd15_openpose.pth` (ControlNetLoader #89); `yolox_l.onnx` (DWPreprocessor #90); `dw-ll_ucoco_384.onnx` (DWPreprocessor #90); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #86); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #99)

**Exact graph duplicates:** `Outdated/image2exp(v2).json`, `Outdated/image2exp(v2)_wLCM.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 19. Module/64x64PixelArtAddOutline_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Module/64x64PixelArtAddOutline_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `965c0f80df170f849d15f6f9502bf1d0c0cab3c7a8d4a028d6ad92cce3c89809` · Git blob `258d108229a1a2f3eeffd5120d9a0f114135e356`.

Pixelize a source image at a 64-pixel target and remove background with RMBG; a parallel pixelization branch is previewed.

Catalog ID: `ecjojo-module-64x64pixelartaddoutline-v1-6086a8b2`. Graph: 6 stored / 6 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) No diffusion text prompt inferred.

**輸出：** saved image via SaveImage #33; image preview via PreviewImage #35

**節點類型：** `LoadImage`, `PixelOEPixelize+`, `PreviewImage`, `RMBG`, `SaveImage`.

**明確模型選擇：** No filename-based model selections; check the implicit selectors below.

**隱含模型選擇：** `RMBG-2.0` (RMBG #5).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. ComfyUI-RMBG with internally selected segmentation/background-removal weights ComfyUI Essentials

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Catalog explicit-filename inventory omits internal model presets/downloads: RMBG-2.0. Pin artifacts and licenses separately.

### 20. Module/ClothingSegmentation(RMBG)_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Module/ClothingSegmentation%28RMBG%29_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `481c8a56f47f3c29563f5f8bd90cfbb7d870a1c46aa9303691beac5021522790` · Git blob `45d2db5a49a9ac38f1debbe2f88a9dbda06ecb5e`.

Segment clothing using text-guided SAM/GroundingDINO and save the isolated garment image.

Catalog ID: `ecjojo-module-clothingsegmentation-rmbg-v1-73f54851`. Graph: 4 stored / 4 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #13 (effective explicit path) No diffusion text prompt inferred.

**輸出：** saved image via SaveImage #14

**節點類型：** `LoadImage`, `Note _O`, `SaveImage`, `Segment`.

**明確模型選擇：** No filename-based model selections; check the implicit selectors below.

**隱含模型選擇：** `sam_vit_l (1.25GB)` (Segment #12); `GroundingDINO_SwinT_OGC (694MB)` (Segment #12).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. ComfyUI-RMBG with internally selected segmentation/background-removal weights

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Catalog explicit-filename inventory omits internal model presets/downloads: GroundingDINO_SwinT_OGC (694MB), sam_vit_l (1.25GB). Pin artifacts and licenses separately.

### 21. Module/FastGenderClassifier_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Module/FastGenderClassifier_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `8d33ae774eedb8903ccf3b4d435b6f4354eafd083af726466657624926e828dc` · Git blob `b2afc86ff1d2693bb7e856f8481d24a06ec21db3`.

Classify detected face crops and select the string 1girl or 1boy according to the filtered-SEGS test.

Catalog ID: `ecjojo-module-fastgenderclassifier-v1-2256eb85`. Graph: 11 stored / 11 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #2 (effective explicit path) No diffusion text prompt inferred.

**輸出：** text/boolean display via ShowText|pysssss #9

**節點類型：** `ImpactHFTransformersClassifierProvider`, `ImpactIsNotEmptySEGS`, `ImpactSEGSClassify`, `ImpactSimpleDetectorSEGS`, `LoadImage`, `Note _O`, `ShowText|pysssss`, `Text Input Switch`, `UltralyticsDetectorProvider`, `easy string`.

**明確模型選擇：** `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #4)

**隱含模型選擇：** `rizvandwiki/gender-classification-2` (ImpactHFTransformersClassifierProvider #5).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced ComfyUI Easy Use

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Catalog explicit-filename inventory omits internal model presets/downloads: rizvandwiki/gender-classification-2. Pin artifacts and licenses separately.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- No-face/empty filtered detections take the same false branch as a non-female classification; add an explicit unknown/empty outcome before using this as a prompt control.

### 22. Module/HumanDetector_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Module/HumanDetector_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `63cf80efc57431d9ad5a66c39d2f3d03ab8104cdc3ed15b3844401949e918648` · Git blob `c43fc1c870abf4b0c172f25c76244871c1a36990`.

Tag an image with WD14 and display whether its tags contain no_humans.

Catalog ID: `ecjojo-module-humandetector-v1-7710b99d`. Graph: 5 stored / 5 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #2 (effective explicit path) No diffusion text prompt inferred.

**輸出：** text/boolean display via easy showAnything #14

**節點類型：** `LoadImage`, `Note _O`, `Text Contains`, `WD14Tagger|pysssss`, `easy showAnything`.

**明確模型選擇：** No filename-based model selections; check the implicit selectors below.

**隱含模型選擇：** `wd-v1-4-vit-tagger-v2` (WD14Tagger|pysssss #13).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced ComfyUI Easy Use

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Catalog explicit-filename inventory omits internal model presets/downloads: wd-v1-4-vit-tagger-v2. Pin artifacts and licenses separately.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- Output is the no_humans tag predicate: true indicates that tag is present, not that a human was detected. Threshold-based tags are not a calibrated human detector.

### 23. Module/RemovevBackground(RMBG)_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Module/RemovevBackground%28RMBG%29_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `644784f526dfb4726bcdf59038d4880687bb7d1aeb8481d64b9fd081bb8dc23a` · Git blob `780de93a00fe145062878e75eb572d28bb497c3a`.

Remove an uploaded image background with RMBG-2.0 and save an alpha image.

Catalog ID: `ecjojo-module-removevbackground-rmbg-v1-b5540dca`. Graph: 3 stored / 3 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) No diffusion text prompt inferred.

**輸出：** saved image via SaveImage #3

**節點類型：** `LoadImage`, `RMBG`, `SaveImage`.

**明確模型選擇：** No filename-based model selections; check the implicit selectors below.

**隱含模型選擇：** `RMBG-2.0` (RMBG #5).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. ComfyUI-RMBG with internally selected segmentation/background-removal weights

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Catalog explicit-filename inventory omits internal model presets/downloads: RMBG-2.0. Pin artifacts and licenses separately.

### 24. Outdated/1.5lcm.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/1.5lcm.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `cfb375f721f96b18b591028f5a42d3f4fcdc5dda958af5832ad1d3cf1c36a257` · Git blob `5516813a04580ad26034d71d54d5d8403ae816c8`.

SD 1.5 text-to-image with an LCM LoRA followed by face detection and detail refinement.

Catalog ID: `ecjojo-outdated-1-5lcm-3dc5f5a1`. Graph: 14 stored / 14 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9; image preview via PreviewImage #10

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #13); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #15); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #6)

**Exact graph duplicates:** `Outdated/txt2img.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 25. Outdated/2IPA2fullbodypose.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/2IPA2fullbodypose.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `5f36895f77545804ff6aa972245274913e5fef43ce36c3ff77318d38ac2f2f63` · Git blob `da833f3b51b9a546dbb3f3b451e3525f367d3844`.

Combine two image-reference adapters on a tall latent canvas, then refine the generated face.

Catalog ID: `ecjojo-outdated-2ipa2fullbodypose-c145f09a`. Graph: 20 stored / 20 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #4 (effective explicit path); LoadImage #3 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #20; saved image via SaveImage #32

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SAMLoader`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #9); `ip-adapter-plus-face_sd15.bin` (IPAdapterModelLoader #8); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #21); `sam_vit_b_01ec64.pth` (SAMLoader #37); `model_full_body_v1.safetensors` (LoraLoader #24); `model.safetensors` (CLIPVisionLoader #10); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #6)

**Exact graph duplicates:** `Outdated/Img2Fullbody.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** LoraLoader #1 · strength_model = 0.8; LoraLoader #1 · strength_clip = 0.8; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 1280; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 20; KSampler #1 · cfg = 6.5; KSampler #1 · sampler_name = dpmpp_2m; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 26. Outdated/2loramix.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/2loramix.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `2265f3649847374a3bec8fbadf6b9e3007969969cc8fc17cf6e120f949f37193` · Git blob `b2a3244cc571916be00f59602a65539d8cdf698d`.

Apply separate LoRA-conditioned sampling branches across a mask using TwoSamplersForMask and pose ControlNet.

Catalog ID: `ecjojo-outdated-2loramix-e5bd7337`. Graph: 22 stored / 22 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #135 (effective explicit path); LoadImage #116 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #134; saved image via SaveImage #125

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `EmptyLatentImage`, `KSamplerProvider`, `LoadImage`, `LoraLoader`, `PreviewImage`, `SaveImage`, `ToBasicPipe`, `TwoSamplersForMask`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `chilloutmix_NiPrunedFp32Fix.safetensors` (CheckpointLoaderSimple #117); `jeff_chilloutmix_v1.safetensors` (LoraLoader #130); `benny_chilloutmix_v1.safetensors` (LoraLoader #119); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #121); `control_v11p_sd15_openpose.pth` (ControlNetLoader #127)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; LoraLoader #2 · strength_model = 1; LoraLoader #2 · strength_clip = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 27. Outdated/4KWallpaper_XL.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/4KWallpaper_XL.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `0b1d93368df3966e308510e61b7458231bf07ad9fe7414ae088097d787e3d730` · Git blob `ac5382029f55803f79d077a56512d9d2c516bf77`.

Generate/refine SDXL imagery and pass it through tiled model upscaling for large wallpapers.

Catalog ID: `ecjojo-outdated-4kwallpaper-xl-aa1ebcec`. Graph: 24 stored / 24 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #246; saved image via SaveImage #184

**節點類型：** `CLIPTextEncodeSDXL`, `CLIPTextEncodeSDXLRefiner`, `CM_SDXLResolution`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSamplerAdvanced`, `LoraLoader`, `PrimitiveNode`, `SaveImage`, `Seed Generator`, `UltimateSDUpscale`, `UpscaleModelLoader`, `VAEDecode`.

**明確模型選擇：** `sd_xl_refiner_1.0_0.9vae.safetensors` (CheckpointLoaderSimple #4); `XL/wegg.safetensors` (LoraLoader #252); `sd_xl_base_1.0_0.9vae.safetensors` (CheckpointLoaderSimple #10); `4x-UltraSharp.pth` (UpscaleModelLoader #253); `4x_Valar_v1.pth` (UpscaleModelLoader #187)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. UltimateSDUpscale

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 28. Outdated/AIPhoto(AIBGMerge).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/AIPhoto%28AIBGMerge%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `a00414c4ee86877da5fa739583cee3f4d62c93ec68c49030a29c10d8de21f2cc` · Git blob `6175ed8700f0f5a6371bc72ac2f93a037ede030d`.

Generate subject/background layers, segment and composite them, apply line control, then swap a reference face.

Catalog ID: `ecjojo-outdated-aiphoto-aibgmerge-274f2d84`. Graph: 39 stored / 39 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #362 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #354; image preview via PreviewImage #379; saved image via SaveImage #366

**節點類型：** `CLIPTextEncode`, `CR Prompt Text`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `Display Int (rgthree)`, `EmptyLatentImage`, `GetImageSize+`, `ImageCompositeMasked`, `ImpactSEGSToMaskList`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByLongerSide`, `JWInteger`, `KSampler`, `LineArtPreprocessor`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `ReActorFaceSwap`, `Reroute`, `SAMLoader`, `SaveImage`, `SimpleMath+`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #262); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #273); `vaeFtMse840000Ema_v100.safetensors` (VAELoader #331); `sam_vit_b_01ec64.pth` (SAMLoader #329); `segm/person_yolov8m-seg.pt` (UltralyticsDetectorProvider #328); `control_v11p_sd15_lineart.pth` (ControlNetLoader #384); `inswapper_128.onnx` (ReActorFaceSwap #375); `codeformer.pth` (ReActorFaceSwap #375)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used ReActor + InsightFace/ONNXRuntime + face restore dependencies ComfyUI Essentials JWNodes Comfyroll nodes rgthree nodes

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 1.2; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 0.75. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 29. Outdated/AIPhotograph(Random).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/AIPhotograph%28Random%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `a344201d01cb8e1b376f4ae72d5df5e57c8700be08e0d1dfe67ad4d83e5ab52c` · Git blob `22bf892a8ec23f45b2ed0e84054f4f00bfeda306`.

Random-prompt portrait generation with model merging, reference-adapter machinery and face detail refinement.

Catalog ID: `ecjojo-outdated-aiphotograph-random-ab56429f`. Graph: 26 stored / 26 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #53; image preview via PreviewImage #89; image preview via PreviewImage #17

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CR Prompt Text`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoraLoader`, `ModelMergeSimple`, `Note`, `OneButtonPrompt`, `PreviewImage`, `SaveImage`, `Text to Conditioning`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #86); `yabalMixSemireal_v1.safetensors` (CheckpointLoaderSimple #2); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #16); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #52); `model.safetensors` (CLIPVisionLoader #66); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #87); `EC.safetensors` (LoraLoader #1)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies Comfyroll nodes One Button Prompt

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; EmptyLatentImage #2 · width = 768; EmptyLatentImage #2 · height = 1024; EmptyLatentImage #2 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 1.5; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; LoraLoader #2 · strength_model = 0.6; LoraLoader #2 · strength_clip = 0.6. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 30. Outdated/AIStudioShot.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/AIStudioShot.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `3042f05c51d5225f287fc3a8cfa1c1cd905bb3b20799b948b0106905e2926054` · Git blob `5c1021e64113b1d1fd2b250b74e783e1644fb293`.

Generate a subject and background separately, segment the generated subject, composite, redraw and refine the face.

Catalog ID: `ecjojo-outdated-aistudioshot-9bf69cfc`. Graph: 38 stored / 38 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #362 (no explicit output path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #360; image preview via PreviewImage #354; saved image via SaveImage #366

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `GetImageSize+`, `ImageCompositeMasked`, `ImpactSEGSToMaskList`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByLongerSide`, `JWInteger`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SAMLoader`, `SaveImage`, `SimpleMath+`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `segm/person_yolov8m-seg.pt` (UltralyticsDetectorProvider #328); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #364); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #273); `vaeFtMse840000Ema_v100.safetensors` (VAELoader #331); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #262); `sam_vit_b_01ec64.pth` (SAMLoader #329)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ComfyUI Essentials JWNodes

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 0.5; KSampler #3 · steps = 10; KSampler #3 · cfg = 1.5; KSampler #3 · sampler_name = lcm; KSampler #3 · scheduler = exponential; KSampler #3 · denoise = 1; EmptyLatentImage #2 · width = 680; EmptyLatentImage #2 · height = 720; EmptyLatentImage #2 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Uploaded/source input nodes 362 do not reach any explicit output; do not advertise them as effective controls without repairing wiring.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 31. Outdated/Any2Fullbody(2025).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Any2Fullbody%282025%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `b6afe652a3b769d5307717661c93dda7cd53f2732877a81372355e7fcfd0334b` · Git blob `de3401e66cb3765aec333c6b2f8244f6baf31548`.

Expand image-reference conditioning into a full-body composition using regional prompts, IPAdapter, upscaling and FaceDetailer.

Catalog ID: `ecjojo-outdated-any2fullbody-2025-bef72458`. Graph: 30 stored / 28 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #115; image preview via PreviewImage #127; saved image via SaveImage #42

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ConditioningSetAreaPercentage`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapter`, `IPAdapterUnifiedLoader`, `ImagePadForOutpaint`, `KSampler`, `LatentUpscaleBy`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`, `easy imageRemBg`.

**明確模型選擇：** `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72); `realcartoonAnime_v11.safetensors` (CheckpointLoaderSimple #2); `virtual_youtuber_v2.safetensors` (LoraLoader #35)

**隱含模型選擇：** `PLUS (high strength)` (IPAdapterUnifiedLoader #75); `RMBG-2.0` (easy imageRemBg #114).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ComfyUI Easy Use

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 0.6; KSampler #2 · steps = 25; KSampler #2 · cfg = 7; KSampler #2 · sampler_name = dpmpp_2m_sde; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 1; LoraLoader #1 · strength_model = 0.7000000000000001; LoraLoader #1 · strength_clip = 0.7000000000000001; ImagePadForOutpaint #1 · left = 24; ImagePadForOutpaint #1 · top = 24; ImagePadForOutpaint #1 · right = 24; ImagePadForOutpaint #1 · bottom = 24; ImagePadForOutpaint #1 · feathering = 0. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Saved graph contains muted/bypassed nodes: easy imageRemBg #114 (mode 4), SaveImage #115 (mode 4). Resolve intended branch state before creating form controls.
- Catalog explicit-filename inventory omits internal model presets/downloads: PLUS (high strength), RMBG-2.0. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 32. Outdated/Anyopenmouth.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Anyopenmouth.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `5e8d2461188c0feb0466e9a2aa08b776932a4151d6e11b1b3bde06e4ba1a0cd1` · Git blob `34d685209669792eff9d46812855a97ef0c6f6b8`.

Segment/pose-condition a source face and apply mouth/face detail transformations with IPAdapter and background removal.

Catalog ID: `ecjojo-outdated-anyopenmouth-566d0c06`. Graph: 23 stored / 23 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #382 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #501

**節點類型：** `CLIPSeg`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `LoadImage`, `LoraLoader`, `SAMLoader`, `SaveImage`, `StringFunction|pysssss`, `UltralyticsDetectorProvider`, `VAELoader`.

**明確模型選擇：** `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #403); `sam_vit_b_01ec64.pth` (SAMLoader #518); `control_v11p_sd15_openpose.pth` (ControlNetLoader #380); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #386); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #383); `model.safetensors` (CLIPVisionLoader #377); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #517); `yolox_l.onnx` (DWPreprocessor #500); `dw-ll_ucoco_384.onnx` (DWPreprocessor #500); `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #404); `sam_vit_b_01ec64.pth` (SAMLoader #524); `mouth_slider_v1.safetensors` (LoraLoader #405)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #489).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** EmptyLatentImage #1 · width = 1024; EmptyLatentImage #1 · height = 1024; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; LoraLoader #2 · strength_model = 5; LoraLoader #2 · strength_clip = 5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: u2net. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 33. Outdated/AvatarGraphSimple.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/AvatarGraphSimple.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `18d66e8b8ac348b47157966f77e7f3404afa23ed37e3aa9894a04bd36ba2ac7d` · Git blob `373c4bf3498e62977a57844ed38d79a5baa6a576`.

Segment eye/mouth layers, build a textured Blender mesh and shape keys, and export a GLB avatar.

Catalog ID: `ecjojo-outdated-avatargraphsimple-788b5708`. Graph: 15 stored / 15 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #320 (effective explicit path) No diffusion text prompt inferred.

**輸出：** GLB avatar mesh/shape keys via AvatarMainOutput #332

**節點類型：** `AvatarMainOutput`, `CreateShapeFlow`, `LoadImage`, `Mesh_Delete`, `Mesh_JoinMesh`, `Mesh_ModifyShapeKey`, `Mesh_PrimitivePlaneAdd`, `Object_CreateMeshLayer`, `Object_MatchTextureAspectRatio`, `Object_PlaneTextureUnwrap`, `SAM MultiLayer`.

**明確模型選擇：** `sam_vit_h_4b8939.pth` (SAM MultiLayer #319)

**Exact graph duplicates:** `Civitai_Workflow/AvatarGraphSimple.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. AvatarGraph/Blender bpy mesh and shape-flow environment; confirm package/version Blender/bpy environment and a mesh artifact consumer are required; GPU diffusion is not the principal operation.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- This produces a Blender/GLB artifact; image-only gallery and runner handling are insufficient. Map mesh export and shape-flow outputs explicitly.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 34. Outdated/Catgirl.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Catgirl.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d31ef8ffcc6c2976be5940daa4ef2e43cc6495527f3e006a7fee1255eca0a6ca` · Git blob `bcdd80cd2f015fef6fe37d59fb68fd94521d42f8`.

Text-to-image with an anime checkpoint, LoRA and explicitly loaded SD 1.5 VAE.

Catalog ID: `ecjojo-outdated-catgirl-1d803877`. Graph: 10 stored / 10 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #107

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `SaveImage`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #20)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 824; EmptyLatentImage #1 · height = 384; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 35. Outdated/CharacterCreator(ComfyDeploy).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/CharacterCreator%28ComfyDeploy%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `8613767e458429280a33cd2aca21b1e788065609e15bc59a54dce7f82c5bc8af` · Git blob `a7c960488247241ec65a22a2299f9521d8a2981b`.

Use an external image or local default, automatic tags and reference conditioning to create full-body, cutout and face-crop assets.

Catalog ID: `ecjojo-outdated-charactercreator-comfydeploy-a5ba52a5`. Graph: 37 stored / 36 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** ComfyUIDeployExternalImage #132 (effective explicit path); LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** text/boolean display via ShowText|pysssss #136; image preview via PreviewImage #127; saved image via SaveImage #42; image preview via PreviewImage #139; saved image via SaveImage #115; saved image via SaveImage #146

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ComfyUIDeployExternalImage`, `ConditioningCombine`, `ConditioningSetAreaPercentage`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapter`, `IPAdapterUnifiedLoader`, `Image Crop Face`, `ImagePadForOutpaint`, `KSampler`, `LatentUpscaleBy`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `ShowText|pysssss`, `String Literal`, `UltralyticsDetectorProvider`, `VAEDecode`, `WD14Tagger|pysssss`, `easy imageRemBg`, `easy promptConcat`, `easy seed`.

**明確模型選擇：** `realcartoonAnime_v11.safetensors` (CheckpointLoaderSimple #2); `virtual_youtuber_v2.safetensors` (LoraLoader #35); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72)

**隱含模型選擇：** `PLUS (high strength)` (IPAdapterUnifiedLoader #75); `RMBG-2.0` (easy imageRemBg #114); `wd-v1-4-convnextv2-tagger-v2` (WD14Tagger|pysssss #128).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced ComfyUI Easy Use ComfyUI-Deploy external-input node; hosted execution is optional and not configured

**已存純量參數：** LoraLoader #1 · strength_model = 0.6; LoraLoader #1 · strength_clip = 0.6; ImagePadForOutpaint #1 · left = 24; ImagePadForOutpaint #1 · top = 24; ImagePadForOutpaint #1 · right = 24; ImagePadForOutpaint #1 · bottom = 24; ImagePadForOutpaint #1 · feathering = 0; KSampler #1 · steps = 20; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; KSampler #2 · steps = 20; KSampler #2 · cfg = 7; KSampler #2 · sampler_name = dpmpp_sde; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 0.5; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Saved graph contains muted/bypassed nodes: SaveImage #115 (mode 4). Resolve intended branch state before creating form controls.
- Catalog explicit-filename inventory omits internal model presets/downloads: PLUS (high strength), RMBG-2.0, wd-v1-4-convnextv2-tagger-v2. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.
- ComfyDeploy external input is an integration contract, not evidence of a configured hosted provider or AutoIP compatibility; validate fallback and request mapping explicitly.

### 36. Outdated/CharacterRoom.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/CharacterRoom.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `677c4ad07a1a54ee63a3ba7eb7d1ca88b6c2c95135a5ddd9c83ee8f672a23787` · Git blob `9edada4318ed412996e8d8b7805c7901e9af6cda`.

Caption a reference with BLIP and generate room/background imagery using IPAdapter, depth and image preprocessors.

Catalog ID: `ecjojo-outdated-characterroom-27cd036d`. Graph: 35 stored / 35 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** text/boolean display via ShowText|pysssss #24; image preview via PreviewImage #20; image preview via PreviewImage #18; text/boolean display via ShowText|pysssss #44; saved image via SaveImage #55; image preview via PreviewImage #49; image preview via PreviewImage #54

**節點類型：** `BLIP Analyze Image`, `BLIP Model Loader`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ColorPreprocessor`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LeReS-DepthMapPreprocessor`, `LoadImage`, `LoraLoader`, `PreviewImage`, `SaveImage`, `ShowText|pysssss`, `ShufflePreprocessor`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `Anime/abyssorangemix3AOM3_aom3a3.safetensors` (CheckpointLoaderSimple #4); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #3); `room.safetensors` (LoraLoader #5); `model.safetensors` (CLIPVisionLoader #6); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #7); `control_v11f1p_sd15_depth.pth` (ControlNetLoader #47); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #13)

**隱含模型選擇：** `interrogate` (BLIP Model Loader #23).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; LoraLoader #2 · strength_model = 0.5; LoraLoader #2 · strength_clip = 0.5; KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = euler; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 1; EmptyLatentImage #1 · width = 1080; EmptyLatentImage #1 · height = 608; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: interrogate. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 37. Outdated/ComfyUI_Level1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/ComfyUI_Level1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d81853ef977fbff9302732976101816782873366ab308348139e8327908f31aa` · Git blob `bde59de71f50324c04ced04b126e67878bbd6d7b`.

Basic text-to-image teaching graph with clip-skip and staged sampling.

Catalog ID: `ecjojo-outdated-comfyui-level1-da609d08`. Graph: 15 stored / 15 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #8

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `Note`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #3)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 38. Outdated/ComfyUI_Level2.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/ComfyUI_Level2.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `269dc99c44bbc10687b5fe020cd657b7d060d2c5fe534645b7aea7567cc0a737` · Git blob `4f7a3d38c7472d4a17c550e14847d327a0328ea6`.

Extend the basic clip-skip text-to-image teaching graph with LoRA loading.

Catalog ID: `ecjojo-outdated-comfyui-level2-e1683359`. Graph: 17 stored / 17 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #8

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `character/EC.safetensors` (LoraLoader #18); `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #3)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 39. Outdated/DollBase.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/DollBase.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `78671fb596d22380442f133370a9c91acd13f1e6b7a60ab242aaa481b85515f8` · Git blob `08d89ab1bfa5d525778a420900ecc74588d1b417`.

Create a doll-like portrait using reference/line conditioning, face swapping, subject masking, compositing and model upscaling.

Catalog ID: `ecjojo-outdated-dollbase-2a458da0`. Graph: 52 stored / 52 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #381 (effective explicit path); LoadImage #634 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #501; image preview via PreviewImage #395; image preview via PreviewImage #679; image preview via PreviewImage #706; saved image via SaveImage #693

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Blank`, `Image Rembg (Remove Background)`, `ImageCompositeMasked`, `ImageResize+`, `ImageUpscaleWithModel`, `ImpactSEGSToMaskList`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByLongerSide`, `KSampler`, `LineArtPreprocessor`, `LoadImage`, `LoraLoader`, `PreviewImage`, `ReActorFaceSwap`, `SAMLoader`, `SaveImage`, `StringFunction|pysssss`, `UltralyticsDetectorProvider`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #389); `control_v11p_sd15_openpose.pth` (ControlNetLoader #380); `Realistic/realisticVisionV51_v51VAE.safetensors` (CheckpointLoaderSimple #376); `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #403); `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #404); `control_v11p_sd15_lineart.pth` (ControlNetLoader #690); `mouth_slider_v1.safetensors` (LoraLoader #405); `mouth_slider_v1.safetensors` (LoraLoader #390); `model.safetensors` (CLIPVisionLoader #377); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #383); `ip-adapter-plus-face_sd15.bin` (IPAdapterModelLoader #452); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #386); `segm/person_yolov8m-seg.pt` (UltralyticsDetectorProvider #703); `sam_vit_b_01ec64.pth` (SAMLoader #704); `RealESRGAN_x2.pth` (UpscaleModelLoader #664); `inswapper_128.onnx` (ReActorFaceSwap #669); `codeformer.pth` (ReActorFaceSwap #669)

**隱含模型選擇：** `u2net_human_seg` (Image Rembg (Remove Background) #499); `u2net_human_seg` (Image Rembg (Remove Background) #662).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used ReActor + InsightFace/ONNXRuntime + face restore dependencies WAS Node Suite and node-specific ML dependencies ComfyUI Essentials JWNodes custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 1024; EmptyLatentImage #1 · height = 1024; EmptyLatentImage #1 · batch_size = 1; LoraLoader #2 · strength_model = 0.5; LoraLoader #2 · strength_clip = 0.5; LoraLoader #3 · strength_model = 2; LoraLoader #3 · strength_clip = 2; LoraLoader #4 · strength_model = 3; LoraLoader #4 · strength_clip = 3; KSampler #1 · steps = 10; KSampler #1 · cfg = 1; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 1; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 0.4. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: u2net_human_seg. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 40. Outdated/GetImageWidthHeight.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/GetImageWidthHeight.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `3123a334fa9235257cb29f40e7ed480ffdbc85fd370dbd32ecf8232534a897bc` · Git blob `791191d45691a7af4e691d8ad0b3fd8be72bd735`.

Read an uploaded image width/height, convert the dimensions to text and display them; no generation.

Catalog ID: `ecjojo-outdated-getimagewidthheight-7d430fda`. Graph: 8 stored / 8 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) No diffusion text prompt inferred.

**輸出：** text/boolean display via ShowText|pysssss #31; text/boolean display via ShowText|pysssss #38

**節點類型：** `Constant Number`, `GetImageWidthHeight`, `LoadImage`, `Number to Text`, `ShowText|pysssss`.

**明確模型選擇：** No filename-based model selections; check the implicit selectors below.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Node-specific image/ML/mesh processing; device and memory requirements have not been measured. WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 41. Outdated/HDImage2fullbody.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/HDImage2fullbody.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `b6557cbabafa06d41bd39a1582793042264e4ab704b4e75e444fc9b6d2e5eaaa` · Git blob `40c2fc20e93171ce4c1c6aad4e2e6ba18e9e492c`.

Create a reference-conditioned full-body image with background removal, resizing and face detail refinement.

Catalog ID: `ecjojo-outdated-hdimage2fullbody-7fb28715`. Graph: 19 stored / 19 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #74; saved image via SaveImage #42; image preview via PreviewImage #86

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `Image scale by ratio`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72); `vtuber-poses-05.safetensors` (LoraLoader #35)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #73).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies

**已存純量參數：** EmptyLatentImage #1 · width = 720; EmptyLatentImage #1 · height = 1280; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.75; LoraLoader #1 · strength_clip = 1; KSampler #1 · steps = 30; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: u2net. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 42. Outdated/HDImageGen.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/HDImageGen.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `a0aed0773b6a7c435a5ccf3440bc2d0a3ebb167d175dad0e65fb221d9d563034` · Git blob `b89e9a78da019801f554969386f5f46e3c512179`.

Text-to-image followed by MMDetection-based face refinement.

Catalog ID: `ecjojo-outdated-hdimagegen-298a20a4`. Graph: 10 stored / 10 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #10; saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `MMDetDetectorProvider`, `PreviewImage`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `bbox/mmdet_anime-face_yolov3.pth` (MMDetDetectorProvider #11); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #6)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.
- Legacy MMDetection provider requires its own compatible detection stack; modern YOLO presence does not satisfy this node contract.

### 43. Outdated/HDImageGen_with2Lora.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/HDImageGen_with2Lora.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `28ead222886c62c96f77b2ac70f543408a68a699520e1a7f02b2208004509630` · Git blob `e7e45e0ac9f5c4bf3c03a93a2c0a3efda58cc551`.

Text-to-image with two LoRA stages and MMDetection-based face refinement.

Catalog ID: `ecjojo-outdated-hdimagegen-with2lora-fd3299f2`. Graph: 12 stored / 12 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #10; saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `LoraLoader`, `MMDetDetectorProvider`, `PreviewImage`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `bbox/mmdet_anime-face_yolov3.pth` (MMDetDetectorProvider #11); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #6); `EC.safetensors` (LoraLoader #13); `frieren-10.safetensors` (LoraLoader #12)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.8; LoraLoader #1 · strength_clip = 0.8; LoraLoader #2 · strength_model = 0.8; LoraLoader #2 · strength_clip = 0.8. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.
- Legacy MMDetection provider requires its own compatible detection stack; modern YOLO presence does not satisfy this node contract.

### 44. Outdated/HDImageGen_withLora.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/HDImageGen_withLora.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `efbc616287881ab40412573043aa6dec447a328d8c37fc4b6bfa2b2489ede7d6` · Git blob `719885026c8b99820b509df951a1bf1d79454786`.

Text-to-image with one LoRA stage and MMDetection-based face refinement.

Catalog ID: `ecjojo-outdated-hdimagegen-withlora-20fd9fa4`. Graph: 11 stored / 11 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #10; saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `LoraLoader`, `MMDetDetectorProvider`, `PreviewImage`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `bbox/mmdet_anime-face_yolov3.pth` (MMDetDetectorProvider #11); `yabalMixSemireal_v1.safetensors` (CheckpointLoaderSimple #6); `Kanna_Hashimoto.safetensors` (LoraLoader #12)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.
- Legacy MMDetection provider requires its own compatible detection stack; modern YOLO presence does not satisfy this node contract.

### 45. Outdated/HDcharacterLora_withop.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/HDcharacterLora_withop.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `fd3eafbd33e39f794c39fb0fcdec9b1c4b11afc948abfb8fee0c7ac01b76bfa8` · Git blob `859622035e3bbda9908503de1304ba2be4f3a732`.

Generate a LoRA character under supplied OpenPose ControlNet guidance, then refine the face.

Catalog ID: `ecjojo-outdated-hdcharacterlora-withop-04b7a805`. Graph: 14 stored / 14 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #15 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #10; saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `DiffControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `LoadImage`, `LoraLoader`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `Char-Genshin-Furina.safetensors` (LoraLoader #12); `control_v11p_sd15_openpose.pth` (DiffControlNetLoader #14); `revMix_revI.safetensors` (CheckpointLoaderSimple #6); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #17)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 46. Outdated/Image2fullbody.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Image2fullbody.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `23ef63d008604d6ca245b3f06d7f5e8fac4093d63c5fbd5c8686afca31fc24b6` · Git blob `72895660e76187e9f032f0f5da7a2d70133a9c81`.

Use an uploaded reference with IPAdapter, a body-pose LoRA and FaceDetailer to generate a full-body character.

Catalog ID: `ecjojo-outdated-image2fullbody-e592e6cb`. Graph: 15 stored / 15 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #42

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `vtuber-poses-05.safetensors` (LoraLoader #35); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72)

**Exact graph duplicates:** `Civitai_Workflow/Image2fullbody.json`, `Outdated/Image2fullbody_withupscale.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 0.6; LoraLoader #1 · strength_clip = 0.6; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 1280; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 47. Outdated/Image2fullbody_withupscale.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Image2fullbody_withupscale.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `23ef63d008604d6ca245b3f06d7f5e8fac4093d63c5fbd5c8686afca31fc24b6` · Git blob `72895660e76187e9f032f0f5da7a2d70133a9c81`.

Reference-conditioned full-body generation with face-detail enlargement; no separate general-image upscale node exists.

Catalog ID: `ecjojo-outdated-image2fullbody-withupscale-dfbe2d9a`. Graph: 15 stored / 15 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #42

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `vtuber-poses-05.safetensors` (LoraLoader #35); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72)

**Exact graph duplicates:** `Civitai_Workflow/Image2fullbody.json`, `Outdated/Image2fullbody.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 0.6; LoraLoader #1 · strength_clip = 0.6; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 1280; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- No general-image upscaler node is present; distinguish FaceDetailer crop enlargement from final-image upscaling.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 48. Outdated/Img2Exp.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Img2Exp.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `aa97bfd524a25b113211af84f5b1c52298493a6cc4c2e8e730c592838e304140` · Git blob `80a5e914d4124fd610c7a0d5a022a3a43973bd0d`.

Create idle, smile, shy, sadness, crying and anger images using a reference adapter and six local face-redraw branches.

Catalog ID: `ecjojo-outdated-img2exp-ca113d9e`. Graph: 46 stored / 46 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #64 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #80; saved image via SaveImage #42; saved image via SaveImage #46; saved image via SaveImage #44; saved image via SaveImage #45; image preview via PreviewImage #73; saved image via SaveImage #43

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CannyEdgePreprocessor`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SaveImage`, `UltralyticsDetectorProvider`.

**明確模型選擇：** `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #99); `model.safetensors` (CLIPVisionLoader #5); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `control_v11p_sd15_canny.pth` (ControlNetLoader #89); `ip-adapter-full-face_sd15.bin` (IPAdapterModelLoader #3); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #117)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 49. Outdated/Img2Fullbody.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Img2Fullbody.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `5f36895f77545804ff6aa972245274913e5fef43ce36c3ff77318d38ac2f2f63` · Git blob `da833f3b51b9a546dbb3f3b451e3525f367d3844`.

Combine two image-reference adapters and a full-body latent layout, then refine the face.

Catalog ID: `ecjojo-outdated-img2fullbody-f916222d`. Graph: 20 stored / 20 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #4 (effective explicit path); LoadImage #3 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #20; saved image via SaveImage #32

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SAMLoader`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #9); `ip-adapter-plus-face_sd15.bin` (IPAdapterModelLoader #8); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #21); `sam_vit_b_01ec64.pth` (SAMLoader #37); `model_full_body_v1.safetensors` (LoraLoader #24); `model.safetensors` (CLIPVisionLoader #10); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #6)

**Exact graph duplicates:** `Outdated/2IPA2fullbodypose.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** LoraLoader #1 · strength_model = 0.8; LoraLoader #1 · strength_clip = 0.8; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 1280; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 20; KSampler #1 · cfg = 6.5; KSampler #1 · sampler_name = dpmpp_2m; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 50. Outdated/MangaDraw_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/MangaDraw_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `9326efa56bfc4a80289fb05d5fe4be673badcde39c6105807b91e89c18ce6d02` · Git blob `72933a7e27f44cdc4b6b3ae61fad61693a71d4f3`.

Turn a Painter drawing into manga imagery using scribble/pose controls, dynamic prompts, LCM and face detailing.

Catalog ID: `ecjojo-outdated-mangadraw-v1-4ee977d2`. Graph: 33 stored / 33 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** PainterNode #33 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** text/boolean display via ShowText|pysssss #55; saved image via SaveImage #90; text/boolean display via ShowText|pysssss #42; saved image via SaveImage #98; image preview via PreviewImage #92; image preview via PreviewImage #104

**節點類型：** `CLIPTextEncode`, `CR Prompt Text`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DPFeelingLucky`, `DPMagicPrompt`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `LoraLoader`, `Note`, `OpenposePreprocessor`, `PainterNode`, `PreviewImage`, `SaveImage`, `Seed Generator`, `ShowText|pysssss`, `StringFunction|pysssss`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `Anime/meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #20); `control_v11p_sd15_openpose.pth` (ControlNetLoader #45); `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `control_sd15_scribble.pth` (ControlNetLoader #37); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #99)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used custom-scripts; WD14 tagger additionally when referenced Painter frontend extension; manually supplied sketch canvas Comfyroll nodes Dynamic Prompts and any implicit prompt-model dependency

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 1.5; KSampler #2 · sampler_name = euler; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 1; EmptyLatentImage #1 · width = 712; EmptyLatentImage #1 · height = 712; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Painter depends on an interactive frontend canvas; a server-only form needs a reviewed drawing/upload adapter.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 51. Outdated/MouthOpenPro(lcm).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/MouthOpenPro%28lcm%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d51182c3fb078027bc8036a307876e35b8699635566de4269c5b8635d22404bd` · Git blob `fffeec225c7cac05f6579e59e3c81b1eed55890f`.

Inpaint an uploaded masked image using pose guidance, IPAdapter, a mouth slider and LCM LoRA.

Catalog ID: `ecjojo-outdated-mouthopenpro-lcm-2a577034`. Graph: 17 stored / 17 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #315 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #324; image preview via PreviewImage #316

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `PreviewImage`, `VAEDecode`, `VAEEncodeForInpaint`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #318); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #319); `control_v11p_sd15_openpose.pth` (ControlNetLoader #322); `yolox_l.onnx` (DWPreprocessor #323); `dw-ll_ucoco_384.onnx` (DWPreprocessor #323); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #326); `Anime/comicBabes_v1.safetensors` (CheckpointLoaderSimple #181); `mouth_slider_v1.safetensors` (LoraLoader #325)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; LoraLoader #2 · strength_model = 4.5; LoraLoader #2 · strength_clip = 4.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 52. Outdated/MouthOpenPro.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/MouthOpenPro.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `859ac3f6851d7bb95c75be74c2fd327b0f558455e34967c626474a4fe104e7a3` · Git blob `544173f050ce5d3cbb725663c4bfe401508c31b8`.

Derive a mouth mask with CLIPSeg and inpaint with pose control and IPAdapter; includes saved and preview output.

Catalog ID: `ecjojo-outdated-mouthopenpro-f0285073`. Graph: 19 stored / 19 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #9; image preview via PreviewImage #4; image preview via PreviewImage #5; saved image via SaveImage #16

**節點類型：** `CLIPSeg`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `DWPreprocessor`, `DiffControlNetLoader`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `PreviewImage`, `SaveImage`, `VAEDecode`, `VAEEncodeForInpaint`, `VAELoader`.

**明確模型選擇：** `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #22); `model.safetensors` (CLIPVisionLoader #23); `epicphotogasm_x.safetensors` (CheckpointLoaderSimple #14); `control_v11p_sd15_openpose.pth` (DiffControlNetLoader #12); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #7)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** KSampler #1 · steps = 20; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 53. Outdated/Openpose2Vdo(Animatediff_lcm).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Openpose2Vdo%28Animatediff_lcm%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `c018f48d79670e8a15ea9572b694859d24e45aa57bdfd700b3c098bbc5d774b9` · Git blob `376054d865d8a9687241910180bb2156bcf69cfa`.

Load pose-video frames and an appearance image, apply AnimateDiff/context plus ControlNet and IPAdapter, then encode a video.

Catalog ID: `ecjojo-outdated-openpose2vdo-animatediff-lcm-0ca9f3c9`. Graph: 32 stored / 32 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #6 (effective explicit path); VHS_LoadVideo #13 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #118; saved image via SaveImage #643; image preview via PreviewImage #639; MP4/video frames via VHS_VideoCombine #63

**節點類型：** `ADE_AnimateDiffLoaderWithContext`, `ADE_AnimateDiffUniformContextOptions`, `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CR Apply LoRA Stack`, `CR LoRA Stack`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoaderAdvanced`, `Display Int (rgthree)`, `EmptyLatentImage`, `FreeU_V2`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `KSampler`, `LoadImage`, `ModelSamplingDiscrete`, `PrepImageForClipVision`, `PreviewImage`, `SaveImage`, `VAEDecode`, `VHS_LoadVideo`, `VHS_VideoCombine`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #586); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #590); `lcm/LCM_LoRA_Weights_SD15.safetensors` (CR LoRA Stack #601); `control_v11p_sd15_openpose.pth` (ControlNetLoaderAdvanced #516); `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #593); `mm_sd_v15_v2.ckpt` (ADE_AnimateDiffLoaderWithContext #37)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #645).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used VideoHelperSuite and FFmpeg AnimateDiff-Evolved and compatible motion model WAS Node Suite and node-specific ML dependencies Comfyroll nodes rgthree nodes Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** EmptyLatentImage #1 · width = 768; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 16; KSampler #1 · cfg = 1.04; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; KSampler #2 · steps = 16; KSampler #2 · cfg = 1.04; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 1; VHS_VideoCombine #1 · frame_rate = 16; EmptyLatentImage #2 · width = 1024; EmptyLatentImage #2 · height = 1024. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: u2net. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 54. Outdated/RoomGen(with_LCMLora).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/RoomGen%28with_LCMLora%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `4ba464d463234be6c19ba4f8244d29f5c59ce6608e3bb712fc457d681acb2339` · Git blob `9d1ed7682380de56751ab27f53b5c3008a9e33e9`.

Caption a reference, generate a room base and use depth/reference conditioning for an LCM room variation.

Catalog ID: `ecjojo-outdated-roomgen-with-lcmlora-d625e962`. Graph: 33 stored / 33 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #20; saved image via SaveImage #57; saved image via SaveImage #55; text/boolean display via ShowText|pysssss #24; text/boolean display via ShowText|pysssss #44; image preview via PreviewImage #18; image preview via PreviewImage #49

**節點類型：** `BLIP Analyze Image`, `BLIP Model Loader`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ColorPreprocessor`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LeReS-DepthMapPreprocessor`, `LoadImage`, `LoraLoader`, `PreviewImage`, `SaveImage`, `ShowText|pysssss`, `ShufflePreprocessor`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #7); `model.safetensors` (CLIPVisionLoader #6); `control_v11f1p_sd15_depth.pth` (ControlNetLoader #47); `room.safetensors` (LoraLoader #5); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #3); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #13); `Anime/landscapeAnimePro_v20Inspiration.safetensors` (CheckpointLoaderSimple #4)

**隱含模型選擇：** `interrogate` (BLIP Model Loader #23).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 1080; EmptyLatentImage #1 · height = 608; EmptyLatentImage #1 · batch_size = 1; LoraLoader #2 · strength_model = 0.5; LoraLoader #2 · strength_clip = 0.5; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = euler; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: interrogate. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 55. Outdated/RoomGen(with_LCMLora)_v2(Removed_BLIP).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/RoomGen%28with_LCMLora%29_v2%28Removed_BLIP%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `30fc2033747d94123f523d51bdd98872ac0b0974a9c5f327cd5c676b251be248` · Git blob `b3ff79af3f145ee5a8c74b83e99c328f6ebb4171`.

Generate a room base and use depth/reference conditioning for an LCM room variation without BLIP captioning.

Catalog ID: `ecjojo-outdated-roomgen-with-lcmlora-v2-removed-blip-6e16bb6b`. Graph: 26 stored / 26 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #1 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #49; image preview via PreviewImage #65; saved image via SaveImage #55; saved image via SaveImage #57

**節點類型：** `AIO_Preprocessor`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LeReS-DepthMapPreprocessor`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `control_v11f1p_sd15_depth.pth` (ControlNetLoader #47); `room.safetensors` (LoraLoader #5); `Anime/landscapeAnimePro_v20Inspiration.safetensors` (CheckpointLoaderSimple #4); `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #3); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #13); `model.safetensors` (CLIPVisionLoader #6); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #7)

**Exact graph duplicates:** `Civitai_Workflow/RoomGen(v2).json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; LoraLoader #2 · strength_model = 0.5; LoraLoader #2 · strength_clip = 0.5; EmptyLatentImage #1 · width = 1080; EmptyLatentImage #1 · height = 608; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = euler; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 56. Outdated/SDXLTurbo_testing.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/SDXLTurbo_testing.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `6aa86cf5ce884817e54f510084ecce6f25d2282a7195ff38088785cc8552a351` · Git blob `3edbd23b73dd50ebc9f69d7521c2b44e89e5bd63`.

Minimal SDXL Turbo text-to-image experiment.

Catalog ID: `ecjojo-outdated-sdxlturbo-testing-1ed1b85c`. Graph: 7 stored / 7 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #534

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `sd_xl_turbo_1.0_fp16.safetensors` (CheckpointLoaderSimple #527)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** KSampler #1 · steps = 3; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 768; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 57. Outdated/SDXL_lcm.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/SDXL_lcm.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `e72251ba3277bd0a7d1e6471523db9b2e05232a7563d3ba40a92d802e8150c4d` · Git blob `02c2a472314594cbe49f3c1d6b5b2f2bcc46a88a`.

SDXL text-to-image with an LCM LoRA, explicit LCM sampling configuration and IPAdapter reference conditioning.

Catalog ID: `ecjojo-outdated-sdxl-lcm-82c655f7`. Graph: 13 stored / 13 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #14 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `ModelSamplingDiscrete`, `SDXL Empty Latent Image (rgthree)`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `dreamshaperXL_turboDpmppSDE.safetensors` (CheckpointLoaderSimple #4); `SD1.5/pytorch_model.bin` (CLIPVisionLoader #13); `ip-adapter-plus_sdxl_vit-h.safetensors` (IPAdapterModelLoader #12); `lcm/SDXL/pytorch_lora_weights.safetensors` (LoraLoader #18)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated rgthree nodes

**已存純量參數：** KSampler #1 · steps = 7; KSampler #1 · cfg = 1.3; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = sgm_uniform; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 58. Outdated/SDXL_withSDUpscale.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/SDXL_withSDUpscale.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `dd05068c3f7aaf9b0b2863be46658a97ca481585eca6dfa36f0f07c0f6fdc896` · Git blob `1c9e86b2044d1a8cbf190ef34f65eaf03d44f42d`.

SDXL base/refiner workflow containing a currently bypassed UltimateSDUpscale branch.

Catalog ID: `ecjojo-outdated-sdxl-withsdupscale-752bfc25`. Graph: 22 stored / 20 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #246; saved image via SaveImage #184

**節點類型：** `CLIPTextEncodeSDXL`, `CLIPTextEncodeSDXLRefiner`, `CheckpointLoaderSimple`, `KSamplerAdvanced`, `PrimitiveNode`, `SDXL Empty Latent Image (rgthree)`, `SaveImage`, `Seed (rgthree)`, `UltimateSDUpscale`, `UpscaleModelLoader`, `VAEDecode`.

**明確模型選擇：** `4x-UltraSharp.pth` (UpscaleModelLoader #253); `4x_Valar_v1.pth` (UpscaleModelLoader #187); `sd_xl_base_1.0_0.9vae.safetensors` (CheckpointLoaderSimple #10); `sd_xl_refiner_1.0_0.9vae.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. UltimateSDUpscale rgthree nodes

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Saved graph contains muted/bypassed nodes: UltimateSDUpscale #245 (mode 4), UltimateSDUpscale #249 (mode 4). Resolve intended branch state before creating form controls.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 59. Outdated/SimpleDraw2Img.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/SimpleDraw2Img.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `3a63c93028f897a4be93fad6d085a709a8d271850cfa58304af4de0ed01176b8` · Git blob `b40658d05ead4b50df8e1742355e423dec4a47b5`.

Convert an interactive Painter sketch to a toon image with scribble ControlNet and LCM LoRA.

Catalog ID: `ecjojo-outdated-simpledraw2img-3ba52fd2`. Graph: 17 stored / 17 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** PainterNode #33 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #107

**節點類型：** `CLIPTextEncode`, `CR Prompt Text`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `PainterNode`, `SaveImage`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `control_v11p_sd15_scribble.pth` (ControlNetLoader #37); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `toonAme_version10.safetensors` (CheckpointLoaderSimple #20)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Painter frontend extension; manually supplied sketch canvas Comfyroll nodes

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 680; EmptyLatentImage #1 · height = 680; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Painter depends on an interactive frontend canvas; a server-only form needs a reviewed drawing/upload adapter.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 60. Outdated/SimpleSVD.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/SimpleSVD.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `c6cbf4ad23e0abfc62f84f654861a8d24b90fdd1a587db812c728cf89e4e8a7f` · Git blob `b1fef0de125365ce610c11d78e502653cc250470`.

SVD image-to-video with FreeU/video-CFG controls and RIFE interpolation.

Catalog ID: `ecjojo-outdated-simplesvd-48932cfc`. Graph: 14 stored / 14 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) No diffusion text prompt inferred.

**輸出：** MP4/video frames via VHS_VideoCombine #26

**節點類型：** `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `FreeU_V2`, `ImageOnlyCheckpointLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `RIFE VFI`, `SDXLResolutionPresets`, `SVD_img2vid_Conditioning`, `Seed (rgthree)`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.

**明確模型選擇：** `Realistic/dreamshaper_8.safetensors` (CheckpointLoaderSimple #45); `lcm/SDXL/pytorch_lora_weights.safetensors` (LoraLoader #44); `SVD/svd_xt.safetensors` (ImageOnlyCheckpointLoader #15); `sudo_rife4_269.662_testV1_scale1.pth` (RIFE VFI #30)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. VideoHelperSuite and FFmpeg frame-interpolation package and RIFE checkpoint rgthree nodes Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 1; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = sgm_uniform; KSampler #1 · denoise = 1; VHS_VideoCombine #1 · frame_rate = 20. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Graph attaches an SDXL-labelled LCM LoRA to the SVD model and supplies CLIP from a separate SD 1.5 checkpoint. Weight-shape/model-family compatibility is unproven; do not assume this accelerates SVD successfully.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 61. Outdated/SimpleSVD_ver6.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/SimpleSVD_ver6.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `b68c13558d2e2ca7350e3945fcf1a856275e45205d7f2b2f47bdb3f73758e230` · Git blob `44bb7331e1f8296068d771c0182141a8c95f4460`.

Resize-derived SVD image-to-video with FreeU/video-CFG controls and RIFE interpolation.

Catalog ID: `ecjojo-outdated-simplesvd-ver6-ceb82dd5`. Graph: 16 stored / 16 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) No diffusion text prompt inferred.

**輸出：** MP4/video frames via VHS_VideoCombine #26

**節點類型：** `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `FreeU_V2`, `Get image size`, `ImageOnlyCheckpointLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `RIFE VFI`, `SVD_img2vid_Conditioning`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.

**明確模型選擇：** `rife40.pth` (RIFE VFI #30); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #45); `lcm/SDXL/pytorch_lora_weights.safetensors` (LoraLoader #44); `SVD/svd_xt.safetensors` (ImageOnlyCheckpointLoader #15)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. VideoHelperSuite and FFmpeg frame-interpolation package and RIFE checkpoint JWNodes Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; VHS_VideoCombine #1 · frame_rate = 10; KSampler #1 · steps = 25; KSampler #1 · cfg = 1; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = sgm_uniform; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Graph attaches an SDXL-labelled LCM LoRA to the SVD model and supplies CLIP from a separate SD 1.5 checkpoint. Weight-shape/model-family compatibility is unproven; do not assume this accelerates SVD successfully.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 62. Outdated/Template10_Img2Vdo(SVD).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/Template10_Img2Vdo%28SVD%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `e6d4caf338bea78ec3bac33e97597081ca5290fc61f23256f2b0606b61e1ac65` · Git blob `64e1f275478ebaef6eb708712135b87de6eaf642`.

SVD image-to-video with selectable resolution and MP4 encoding; includes an auxiliary VAE loader.

Catalog ID: `ecjojo-outdated-template10-img2vdo-svd-24361f05`. Graph: 12 stored / 12 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path) No diffusion text prompt inferred.

**輸出：** MP4/video frames via VHS_VideoCombine #31

**節點類型：** `ImageOnlyCheckpointLoader`, `KSampler`, `LoadImage`, `Note`, `PrimitiveNode`, `SDXLResolutionPresets`, `SVD_img2vid_Conditioning`, `VAEDecode`, `VAELoader`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.

**明確模型選擇：** `SVD/svd_xt.safetensors` (ImageOnlyCheckpointLoader #29); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #41)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. VideoHelperSuite and FFmpeg Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** KSampler #1 · steps = 20; KSampler #1 · cfg = 5; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 63. Outdated/ToonStyleImageGen_v1.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/ToonStyleImageGen_v1.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `5aa1832996af3d580525afdbce46a6916cee06945f6ab766c29ce5955a948f83` · Git blob `51b5942aa2076335646cd180555e7c72bd70a72a`.

Caption and transform a photo to a toon avatar with dual adapters, pose control, mouth LoRA and face/background refinement.

Catalog ID: `ecjojo-outdated-toonstyleimagegen-v1-a8dfc548`. Graph: 47 stored / 47 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #18 (effective explicit path); LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #125; image preview via PreviewImage #93; text/boolean display via ShowText|pysssss #65; image preview via PreviewImage #101; saved image via SaveImage #117

**節點類型：** `BLIP Analyze Image`, `BLIP Model Loader`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SAMLoader`, `SaveImage`, `ShowText|pysssss`, `StringFunction|pysssss`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #8); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #89); `sam_vit_h_4b8939.pth` (SAMLoader #106); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #39); `ip-adapter-plus-face_sd15.bin` (IPAdapterModelLoader #9); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #28); `control_v11p_sd15_openpose.pth` (ControlNetLoader #17); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #84); `control_sd15_scribble.pth` (ControlNetLoader #129); `toonAme_version10.safetensors` (CheckpointLoaderSimple #4); `mouth_slider_v1.safetensors` (LoraLoader #91)

**隱含模型選擇：** `interrogate` (BLIP Model Loader #24); `caption` (BLIP Model Loader #62); `u2net` (Image Rembg (Remove Background) #98); `u2net` (Image Rembg (Remove Background) #127).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced

**已存純量參數：** EmptyLatentImage #1 · width = 720; EmptyLatentImage #1 · height = 720; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; LoraLoader #2 · strength_model = 4; LoraLoader #2 · strength_clip = 4. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: caption, interrogate, u2net. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 64. Outdated/ToonStyleImageGen_v2(Removed_BLIP).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/ToonStyleImageGen_v2%28Removed_BLIP%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `7d7f7cf0cb7333328ce96aa00e0e3cd3491c28723b275377dee5980b2b4d92a6` · Git blob `e48605bd7fcfeab8a0498252b6a8ab06c3afab8f`.

Transform a photo to a toon avatar with dual adapters, pose control and face/background refinement without BLIP.

Catalog ID: `ecjojo-outdated-toonstyleimagegen-v2-removed-blip-3e6c10c3`. Graph: 29 stored / 29 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path); LoadImage #18 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #125; image preview via PreviewImage #93; image preview via PreviewImage #101; saved image via SaveImage #117

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SAMLoader`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #89); `sam_vit_h_4b8939.pth` (SAMLoader #106); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #39); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #84); `toonAme_version10.safetensors` (CheckpointLoaderSimple #4); `ip-adapter-plus-face_sd15.bin` (IPAdapterModelLoader #9); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #28); `model.safetensors` (CLIPVisionLoader #8); `mouth_slider_v1.safetensors` (LoraLoader #91); `control_v11p_sd15_openpose.pth` (ControlNetLoader #17)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #98); `u2net` (Image Rembg (Remove Background) #127).

**Exact graph duplicates:** `Civitai_Workflow/ToonGen(v2).json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; LoraLoader #2 · strength_model = 4; LoraLoader #2 · strength_clip = 4; EmptyLatentImage #1 · width = 720; EmptyLatentImage #1 · height = 720; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: u2net. Pin artifacts and licenses separately.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 65. Outdated/drawing2img(random)_wLCM.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/drawing2img%28random%29_wLCM.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `a8ba35ef7451cea6347394b5b67dac7a9dfa17d039fb2e081ca16d90fa78b95d` · Git blob `f61a49bfe76129c0adffc9d8d5c1c1c9757b3f6b`.

Combine a Painter sketch with randomized prompt expansion, scribble control and LCM toon generation.

Catalog ID: `ecjojo-outdated-drawing2img-random-wlcm-624ddca5`. Graph: 21 stored / 21 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** PainterNode #33 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #90; text/boolean display via ShowText|pysssss #55

**節點類型：** `CLIPTextEncode`, `CR Prompt Text`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DPFeelingLucky`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `PainterNode`, `SaveImage`, `ShowText|pysssss`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #20); `control_sd15_scribble.pth` (ControlNetLoader #45)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. custom-scripts; WD14 tagger additionally when referenced Painter frontend extension; manually supplied sketch canvas Comfyroll nodes Dynamic Prompts and any implicit prompt-model dependency

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 712; EmptyLatentImage #1 · height = 712; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Painter depends on an interactive frontend canvas; a server-only form needs a reviewed drawing/upload adapter.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 66. Outdated/drawing2img_wLCM.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/drawing2img_wLCM.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `44d0c9da6cce674d1621a8577a2c102fae3d3308a6ccc29b67e69f9c707f566a` · Git blob `32c573b11b9c6f05b55c4dd888fff4bac11dcb59`.

Combine a Painter sketch with user prompts, scribble control and LCM toon generation.

Catalog ID: `ecjojo-outdated-drawing2img-wlcm-26f5c84e`. Graph: 18 stored / 18 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** PainterNode #33 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #90

**節點類型：** `CLIPTextEncode`, `CR Prompt Text`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `PainterNode`, `SaveImage`, `StringFunction|pysssss`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `lcm/LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `Toon/toonAme_version10.safetensors` (CheckpointLoaderSimple #20); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `control_sd15_scribble.pth` (ControlNetLoader #45)

**Exact graph duplicates:** `Civitai_Workflow/drawing2img.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. custom-scripts; WD14 tagger additionally when referenced Painter frontend extension; manually supplied sketch canvas Comfyroll nodes

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 8; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Painter depends on an interactive frontend canvas; a server-only form needs a reviewed drawing/upload adapter.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 67. Outdated/easyopenmouth_(inpaint).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/easyopenmouth_%28inpaint%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `e7cded6d2ccc5c172422b6d2152b0d59af809f1b279bcf52431db73c55ed6a16` · Git blob `2bb19e034f648bfbc96eb53b3d564d060049b5e8`.

Simple source-image mask inpainting for a mouth expression; the result is preview-only.

Catalog ID: `ecjojo-outdated-easyopenmouth-inpaint-482c5f61`. Graph: 8 stored / 8 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #315 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #316

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `KSampler`, `LoadImage`, `PreviewImage`, `VAEDecode`, `VAEEncodeForInpaint`.

**明確模型選擇：** `meinamix_meinaV10.safetensors` (CheckpointLoaderSimple #181)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 68. Outdated/heighttesting.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/heighttesting.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `158eb50539e1bdb35ac8317671ac5ab442a50d05476bfd184d4207729d1aaafc` · Git blob `c3cd97a51c8b58c802cd73a1170c7da1f0f8c5d1`.

Generate multiple pose-conditioned anime variants on different canvases to compare apparent body height.

Catalog ID: `ecjojo-outdated-heighttesting-1d9daff0`. Graph: 20 stored / 20 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #121 (effective explicit path); LoadImage #122 (effective explicit path); LoadImage #88 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #66; image preview via PreviewImage #127; image preview via PreviewImage #128

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `LoadImage`, `PreviewImage`, `VAEDecode`.

**明確模型選擇：** `control_v11p_sd15_openpose_fp16.safetensors` (ControlNetLoader #87); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 0.99; KSampler #2 · steps = 25; KSampler #2 · cfg = 7; KSampler #2 · sampler_name = dpmpp_2m_sde; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 0.99; KSampler #3 · steps = 25; KSampler #3 · cfg = 7; KSampler #3 · sampler_name = dpmpp_2m_sde; KSampler #3 · scheduler = karras; KSampler #3 · denoise = 0.99. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 69. Outdated/image2exp(v2).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/image2exp%28v2%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d1c83402fb396b73906f03e1fc9f0a4df50275128628e34c48753af68604df9d` · Git blob `2b10a56079808f6c67a6bc41688093c419beb66e`.

Create six expression variants from a source portrait using pose conditioning, IPAdapter and separate FaceDetailer branches.

Catalog ID: `ecjojo-outdated-image2exp-v2-65f25281`. Graph: 45 stored / 45 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #64 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #42; saved image via SaveImage #46; saved image via SaveImage #43; saved image via SaveImage #44; saved image via SaveImage #45; image preview via PreviewImage #73; saved image via SaveImage #80

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SaveImage`, `UltralyticsDetectorProvider`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `control_v11p_sd15_openpose.pth` (ControlNetLoader #89); `yolox_l.onnx` (DWPreprocessor #90); `dw-ll_ucoco_384.onnx` (DWPreprocessor #90); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #86); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #99)

**Exact graph duplicates:** `Civitai_Workflow/image2exp(v2).json`, `Outdated/image2exp(v2)_wLCM.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 70. Outdated/image2exp(v2)_wLCM.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/image2exp%28v2%29_wLCM.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d1c83402fb396b73906f03e1fc9f0a4df50275128628e34c48753af68604df9d` · Git blob `2b10a56079808f6c67a6bc41688093c419beb66e`.

Create six image-expression variants with LCM, pose ControlNet, IPAdapter and FaceDetailer.

Catalog ID: `ecjojo-outdated-image2exp-v2-wlcm-9ae21fe3`. Graph: 45 stored / 45 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #64 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #42; saved image via SaveImage #46; saved image via SaveImage #43; saved image via SaveImage #44; saved image via SaveImage #45; image preview via PreviewImage #73; saved image via SaveImage #80

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SaveImage`, `UltralyticsDetectorProvider`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `control_v11p_sd15_openpose.pth` (ControlNetLoader #89); `yolox_l.onnx` (DWPreprocessor #90); `dw-ll_ucoco_384.onnx` (DWPreprocessor #90); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #86); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #99)

**Exact graph duplicates:** `Civitai_Workflow/image2exp(v2).json`, `Outdated/image2exp(v2).json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 71. Outdated/image2exp.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/image2exp.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `dc8018e26aeb024709d83f5b3f2bdbef95eddf0e9a2499038abf8b8e91b98280` · Git blob `3fcc291619ec6a271c1c7f0aab610e037a740d35`.

Early expression-variant graph using IPAdapter face reference, MMDetection/SAM and several FaceDetailer outputs.

Catalog ID: `ecjojo-outdated-image2exp-55893507`. Graph: 36 stored / 36 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #64 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #80; saved image via SaveImage #42; saved image via SaveImage #46; saved image via SaveImage #43; saved image via SaveImage #44; saved image via SaveImage #45; image preview via PreviewImage #73

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterCLIPVisionEncode`, `IPAdapterModelLoader`, `LoadImage`, `MMDetDetectorProvider`, `Note`, `PreviewImage`, `Reroute`, `SAMLoader`, `SaveImage`.

**明確模型選擇：** `meinamix_meinaV10.safetensors` (CheckpointLoaderSimple #2); `pytorch_model_P.bin` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #3); `bbox/mmdet_anime-face_yolov3.pth` (MMDetDetectorProvider #49); `sam_vit_b_01ec64.pth` (SAMLoader #82)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.
- Legacy MMDetection provider requires its own compatible detection stack; modern YOLO presence does not satisfy this node contract.

### 72. Outdated/kana_randomcharacter.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/kana_randomcharacter.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `b3af162037e1c9dbf93a46ad3b0134b32ce5bd3fec2975200906544257cae343` · Git blob `f75984ccf0ff316f84568dc5e0e8e690c3868642`.

Random-prompt character generation with model merging, EC LoRA and MMDetection face refinement.

Catalog ID: `ecjojo-outdated-kana-randomcharacter-f054a07d`. Graph: 23 stored / 23 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #17; saved image via SaveImage #85; saved image via SaveImage #53

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoraLoader`, `MMDetDetectorProvider`, `ModelMergeSimple`, `OneButtonPrompt`, `PreviewImage`, `SaveImage`, `Text to Conditioning`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `EC.safetensors` (LoraLoader #1); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #16); `yabalMixSemireal_v1.safetensors` (CheckpointLoaderSimple #2); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #52); `model.safetensors` (CLIPVisionLoader #66); `bbox/mmdet_anime-face_yolov3.pth` (MMDetDetectorProvider #45)

**Exact graph duplicates:** `Civitai_Workflow/RandomCharacter(v2).json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies One Button Prompt

**已存純量參數：** LoraLoader #1 · strength_model = 0.6; LoraLoader #1 · strength_clip = 0.6; KSampler #1 · steps = 15; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = uni_pc; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; EmptyLatentImage #2 · width = 768; EmptyLatentImage #2 · height = 1024; EmptyLatentImage #2 · batch_size = 1; KSampler #2 · steps = 25; KSampler #2 · cfg = 9; KSampler #2 · sampler_name = dpmpp_2m; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.
- Legacy MMDetection provider requires its own compatible detection stack; modern YOLO presence does not satisfy this node contract.

### 73. Outdated/mannequin.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/mannequin.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `a9b5d52c3519fc184b09d2d4874382e3aff1206ccb784d134e3f28112743697c` · Git blob `3a119f9c43296d1e57ff70069d79261c675f71ad`.

Generate a pose-conditioned anime body/mannequin from an uploaded pose map; preview-only output.

Catalog ID: `ecjojo-outdated-mannequin-35ef26e1`. Graph: 10 stored / 10 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #88 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #66

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `LoadImage`, `PreviewImage`, `VAEDecode`.

**明確模型選擇：** `control_v11p_sd15_openpose_fp16.safetensors` (ControlNetLoader #87); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 768; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 25; KSampler #1 · cfg = 7; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- No persistent image/video/mesh save sink exists; define whether display-only output or a new save/output adapter is intended.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 74. Outdated/simplesvd_1024.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/simplesvd_1024.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `3d31c0ce0baeaec05fa798a2d19ea639a323f0e8ae1f456c475ac9c2d6a63c4f` · Git blob `b8dde1763d92e979080e9b35fce68529d0ff21bd`.

SVD video generation at a configured resolution with continuous-EDM sampling, FreeU, RIFE and implicit image routing.

Catalog ID: `ecjojo-outdated-simplesvd-1024-b6d4d2bd`. Graph: 11 stored / 11 active nodes. Structural check: **dangling link node**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #50 (effective explicit path) No diffusion text prompt inferred.

**輸出：** MP4/video frames via VHS_VideoCombine #67

**節點類型：** `Anything Everywhere`, `FreeU_V2`, `ImageOnlyCheckpointLoader`, `KSamplerAdvanced`, `LoadImage`, `ModelSamplingContinuousEDM`, `RIFE VFI`, `SVD_img2vid_Conditioning`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.

**明確模型選擇：** `SVD/svd_xt.safetensors` (ImageOnlyCheckpointLoader #64); `rife40.pth` (RIFE VFI #66)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. VideoHelperSuite and FFmpeg frame-interpolation package and RIFE checkpoint Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** VHS_VideoCombine #1 · frame_rate = 8. Connected/computed values and unknown widget layouts are not resolved.

**Structural diagnostics:** `[{"kind": "dangling_link_node", "link_id": 139, "source_node": 38, "target_node": 41}, {"kind": "dangling_link_node", "link_id": 140, "source_node": 38, "target_node": 67}, {"kind": "dangling_link_node", "link_id": 141, "source_node": 38, "target_node": 41}, {"kind": "dangling_link_node", "link_id": 142, "source_node": 38, "target_node": 67}, {"kind": "dangling_link_node", "link_id": 157, "source_node": 38, "target_node": 41}, {"kind": "dangling_link_node", "link_id": 158, "source_node": 38, "target_node": 67}, {"kind": "dangling_link_node", "link_id": 162, "source_node": 38, "target_node": 67}, {"kind": "dangling_link_node", "link_id": 173, "source_node": 38, "target_node": 67}, {"kind": "nonreciprocal_link", "link_id": 175}]`

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Repair reviewed graph topology: dangling_link_node, nonreciprocal_link. Do not drop stale links without checking intended routing.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 75. Outdated/txt2img.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Outdated/txt2img.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `cfb375f721f96b18b591028f5a42d3f4fcdc5dda958af5832ad1d3cf1c36a257` · Git blob `5516813a04580ad26034d71d54d5d8403ae816c8`.

LCM text-to-image and YOLO-based face detailing.

Catalog ID: `ecjojo-outdated-txt2img-e18a3152`. Graph: 14 stored / 14 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9; image preview via PreviewImage #10

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `KSampler`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #13); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #15); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #6)

**Exact graph duplicates:** `Outdated/1.5lcm.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Author places this graph in Outdated; its presence is historical, not a current compatibility claim.

### 76. Patreon_Case Examples/AddModel2Cloth/CaseExamples01_clothing.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Case%20Examples/AddModel2Cloth/CaseExamples01_clothing.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `6f338697377d7367a5165f84284301a196207f706ebaf650a6ed6724aadf1d91` · Git blob `7e2696766cc3f4860d50d839a0fcfbc4d6456a73`.

Segment clothing, pad the canvas, apply pose/depth conditioning and inpaint a model around the garment, then refine the face.

Catalog ID: `ecjojo-patreon-case-examples-addmodel2cloth-caseexamples01-clothing-609c86f1`. Graph: 40 stored / 39 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #388 (effective explicit path); LoadImage #592 (effective explicit path) No diffusion text prompt inferred.

**輸出：** image preview via PreviewImage #506; saved image via SaveImage #469; saved image via SaveImage #411; saved image via SaveImage #575; image preview via PreviewImage #574; image preview via PreviewImage #555

**節點類型：** `ControlNetApply`, `ControlNetLoader`, `Display Int (rgthree)`, `Efficient Loader`, `FaceDetailer`, `Get Image Size`, `GroundingDinoModelLoader (segment anything)`, `GroundingDinoSAMSegment (segment anything)`, `Image Rembg (Remove Background)`, `ImagePadForOutpaint`, `ImpactInt`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `MiDaS-DepthMapPreprocessor`, `Note`, `PreviewImage`, `SAMLoader`, `SAMModelLoader (segment anything)`, `SaveImage`, `UltimateSDUpscale`, `UltralyticsDetectorProvider`, `UpscaleModelLoader`, `VAEEncodeForInpaint`.

**明確模型選擇：** `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #408); `control_v11f1p_sd15_depth_fp16.safetensors` (ControlNetLoader #558); `sam_vit_b_01ec64.pth` (SAMLoader #329); `国外大模型/dreamshaper_8.safetensors` (Efficient Loader #584); `vae-ft-mse-840000-ema-pruned.safetensors` (Efficient Loader #584); `control_v11p_sd15_openpose_fp16.safetensors` (ControlNetLoader #461); `4x-UltraSharp.pth` (UpscaleModelLoader #598); `dreamshaper_8.safetensors` (Efficient Loader #586); `vae-ft-mse-840000-ema-pruned.safetensors` (Efficient Loader #586); `LCM_LoRA_Weights_SD15.safetensors` (Efficient Loader #586)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #600); `sam_vit_h (2.56GB)` (SAMModelLoader (segment anything) #561); `GroundingDINO_SwinT_OGC (694MB)` (GroundingDinoModelLoader (segment anything) #553).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used WAS Node Suite and node-specific ML dependencies JWNodes Efficiency Nodes UltimateSDUpscale rgthree nodes

**已存純量參數：** ImagePadForOutpaint #1 · feathering = 50; KSampler (Efficient) #1 · steps = 10; KSampler (Efficient) #1 · cfg = 1.5; KSampler (Efficient) #1 · sampler_name = lcm; KSampler (Efficient) #1 · scheduler = exponential; KSampler (Efficient) #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Saved graph contains muted/bypassed nodes: UltimateSDUpscale #597 (mode 4). Resolve intended branch state before creating form controls.
- Catalog explicit-filename inventory omits internal model presets/downloads: GroundingDINO_SwinT_OGC (694MB), sam_vit_h (2.56GB), u2net. Pin artifacts and licenses separately.

### 77. Patreon_Case Examples/BackgroundRegen/CaseExamples02_BackgroundGen.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Case%20Examples/BackgroundRegen/CaseExamples02_BackgroundGen.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `ab762413ffd679d2a6ea0c8157c9e24777a50adfa910c80ef5eee6a01510682c` · Git blob `e0badc97a99fff0bd8f939b1ef652a856f5b06ee`.

Generate a subject and replacement background, segment/composite/redraw and refine the face; byte-identical to Civitai AIStudioShot, with its uploaded-image node disconnected.

Catalog ID: `ecjojo-patreon-case-examples-backgroundregen-caseexamples02-backgroundgen-311fa286`. Graph: 38 stored / 38 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #362 (no explicit output path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #360; image preview via PreviewImage #354; saved image via SaveImage #366

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `GetImageSize+`, `ImageCompositeMasked`, `ImpactSEGSToMaskList`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByLongerSide`, `JWInteger`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SAMLoader`, `SaveImage`, `SimpleMath+`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `segm/person_yolov8m-seg.pt` (UltralyticsDetectorProvider #328); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #364); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #273); `vaeFtMse840000Ema_v100.safetensors` (VAELoader #331); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #262); `sam_vit_b_01ec64.pth` (SAMLoader #329)

**Exact graph duplicates:** `Civitai_Workflow/AIStudioShot.json`.

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required ComfyUI Essentials JWNodes

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 0.5; KSampler #3 · steps = 10; KSampler #3 · cfg = 1.5; KSampler #3 · sampler_name = lcm; KSampler #3 · scheduler = exponential; KSampler #3 · denoise = 1; EmptyLatentImage #2 · width = 680; EmptyLatentImage #2 · height = 720; EmptyLatentImage #2 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Uploaded/source input nodes 362 do not reach any explicit output; do not advertise them as effective controls without repairing wiring.

### 78. Patreon_Workflow/1StepGen_SDXLTurbo.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/1StepGen_SDXLTurbo.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `ce7ce5d7c11b24d2dc0cc64629df6fd5f0d3ea80c30b97e73e87498377cdcd72` · Git blob `2d06379c2c77270974cd972e9c62aabe686621b7`.

One-step SDXL Turbo text-to-image generation.

Catalog ID: `ecjojo-patreon-workflow-1stepgen-sdxlturbo-2ea01f9a`. Graph: 7 stored / 7 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `SDXL-TURBO/sd_xl_turbo_1.0_fp16.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured.

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 1; KSampler #1 · cfg = 1; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 79. Patreon_Workflow/2IPAdapter.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/2IPAdapter.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `4fe5baf988063056031246a7939f7a3be1baf9eb980865672fddc331d50c8b44` · Git blob `b872b8e33d15a6d8ae3923d107268e922f306618`.

Combine two supplied reference images through sequential IPAdapter applications.

Catalog ID: `ecjojo-patreon-workflow-2ipadapter-e1a9fb01`. Graph: 13 stored / 13 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #72 (effective explicit path); LoadImage #85 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #88

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #68); `ip-adapter-plus_sd15.safetensors` (IPAdapterModelLoader #67); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated

**已存純量參數：** KSampler #1 · steps = 30; KSampler #1 · cfg = 6; KSampler #1 · sampler_name = euler; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 80. Patreon_Workflow/Anime2Realistic.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/Anime2Realistic.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `d9c126422d2940a122b8bb08d9c1003c0a171655957c16c5e4720af253c0998a` · Git blob `4bbc4747805d2731ab00accc0691c1f4ff94b738`.

Redraw an anime image using SD 1.5, segmentation/tile controls and IPAdapter with LCM acceleration.

Catalog ID: `ecjojo-patreon-workflow-anime2realistic-7bcc59f5`. Graph: 25 stored / 25 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #65; image preview via PreviewImage #75; image preview via PreviewImage #90

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `IPAdapterApply`, `IPAdapterModelLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SAMPreprocessor`, `SaveImage`, `TilePreprocessor`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `lcm/SD1.5/pytorch_lora_weights.safetensors` (LoraLoader #73); `SD1.5/pytorch_model.bin` (CLIPVisionLoader #86); `ip-adapter-plus_sd15.safetensors` (IPAdapterModelLoader #87); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #45); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #71); `control_v11u_sd15_tile_fp16.safetensors` (ControlNetLoader #66); `control_v11p_sd15_seg_fp16.safetensors` (ControlNetLoader #80)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used JWNodes

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; KSampler #1 · steps = 15; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 81. Patreon_Workflow/ChangePose.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/ChangePose.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `dcceccf9b258064aca57c28e1e85d2f96c22468949e703bd49446fe318c949c3` · Git blob `5c6b743dbcf3bb0aaca4764cb37db33542a6c43c`.

Use a pose reference and appearance reference to generate a new pose, then apply ReActor face swapping.

Catalog ID: `ecjojo-patreon-workflow-changepose-49c29a5a`. Graph: 25 stored / 25 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #125; image preview via PreviewImage #137; image preview via PreviewImage #142

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `ReActorFaceSwap`, `SaveImage`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `lcm/SD1.5/pytorch_lora_weights.safetensors` (LoraLoader #120); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #71); `model.safetensors` (CLIPVisionLoader #86); `control_v11p_sd15_openpose_fp16.safetensors` (ControlNetLoader #139); `ip-adapter-plus_sd15.safetensors` (IPAdapterModelLoader #87); `inswapper_128.onnx` (ReActorFaceSwap #141); `codeformer.pth` (ReActorFaceSwap #141); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #45); `yolox_l.onnx` (DWPreprocessor #138); `dw-ll_ucoco_384_bs5.torchscript.pt` (DWPreprocessor #138)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used ReActor + InsightFace/ONNXRuntime + face restore dependencies

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #2 · width = 512; EmptyLatentImage #2 · height = 512; EmptyLatentImage #2 · batch_size = 1; KSampler #1 · steps = 12; KSampler #1 · cfg = 1.56; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 1.5; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 82. Patreon_Workflow/ChangeRoomStyle.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/ChangeRoomStyle.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `2057c26ed417b9b25c89caf77477927394a4f3daa6d95c494c56d06b55d86fea` · Git blob `7cc4cef77b41ecdb6840afa540356ecafce56db2`.

Resize and redraw a room image while preserving Canny edges, with an SD 1.5 LCM LoRA.

Catalog ID: `ecjojo-patreon-workflow-changeroomstyle-e319b645`. Graph: 18 stored / 18 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #65; image preview via PreviewImage #69

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CannyEdgePreprocessor`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #71); `control_v11p_sd15_canny_fp16.safetensors` (ControlNetLoader #66); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #45); `lcm/SD1.5/pytorch_lora_weights.safetensors` (LoraLoader #73)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ControlNet auxiliary preprocessors; Advanced-ControlNet where used JWNodes

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; KSampler #1 · steps = 12; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 83. Patreon_Workflow/CharacterLora.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/CharacterLora.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `c9d6ff10046836c3eb4890701dc14d208468aeba0b690d0c9f8864cf894d5b42` · Git blob `7af325c05830dc6bc18a96e894ee2aaed5dec1e5`.

Randomized character generation using a character LoRA, model/reference conditioning and a second high-resolution/detail stage.

Catalog ID: `ecjojo-patreon-workflow-characterlora-59ea9bdf`. Graph: 30 stored / 30 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #53; image preview via PreviewImage #17; image preview via PreviewImage #89; text/boolean display via ShowText|pysssss #95; text/boolean display via ShowText|pysssss #96

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CR Prompt Text`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `KSampler (Efficient)`, `LatentUpscaleBy`, `LoraLoader`, `Note`, `OneButtonPrompt`, `PreviewImage`, `SaveImage`, `ShowText|pysssss`, `Text to Conditioning`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `yabalMixSemireal_v1.safetensors` (CheckpointLoaderSimple #2); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #87); `ip-adapter-plus_sd15.bin` (IPAdapterModelLoader #52); `model.safetensors` (CLIPVisionLoader #66); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #86); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #16); `EC.safetensors` (LoraLoader #1)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies custom-scripts; WD14 tagger additionally when referenced Efficiency Nodes Comfyroll nodes One Button Prompt

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1; EmptyLatentImage #2 · width = 1024; EmptyLatentImage #2 · height = 1024; EmptyLatentImage #2 · batch_size = 1; KSampler #2 · steps = 10; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = exponential; KSampler #2 · denoise = 1; KSampler (Efficient) #1 · steps = 10; KSampler (Efficient) #1 · cfg = 1.5; KSampler (Efficient) #1 · sampler_name = lcm; KSampler (Efficient) #1 · scheduler = exponential; KSampler (Efficient) #1 · denoise = 0.7000000000000001; LoraLoader #2 · strength_model = 0.6; LoraLoader #2 · strength_clip = 0.6. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 84. Patreon_Workflow/FullBodyAnimeCharacter.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/FullBodyAnimeCharacter.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `ca7e88d873ec83c78b3d7a196127c9ca196f5d89547b609e923373103706e7e2` · Git blob `492e9081cf5be25828e88be4774150bc21993c23`.

Turn an image reference into a full-body anime character using a body-pose LoRA, adapter and face refinement.

Catalog ID: `ecjojo-patreon-workflow-fullbodyanimecharacter-2e78200c`. Graph: 20 stored / 20 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #76; saved image via SaveImage #74; saved image via SaveImage #42

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `ip-adapter-full-face_sd15.safetensors` (IPAdapterModelLoader #3); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72); `vtuber-poses-05.safetensors` (LoraLoader #35)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #75); `isnet-anime` (Image Rembg (Remove Background) #73).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 1024; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 25; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: isnet-anime, u2net. Pin artifacts and licenses separately.

### 85. Patreon_Workflow/IPAdapter_WeightTesting.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/IPAdapter_WeightTesting.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `69380a492a03e5482bdf0238f68688996d27f2a8b7f7059107628a6719704b15` · Git blob `695ce66c36144b68b4f93eadd2849a9faf6b0d1c`.

Run parallel adapter-conditioning branches at different strengths, with latent-noise injection for comparison.

Catalog ID: `ecjojo-patreon-workflow-ipadapter-weighttesting-263a76de`. Graph: 33 stored / 33 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #109 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #173; saved image via SaveImage #163; saved image via SaveImage #166; saved image via SaveImage #169; saved image via SaveImage #172

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `Latent Noise Injection`, `LoadImage`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #68); `ip-adapter-plus_sd15.safetensors` (IPAdapterModelLoader #67); `realisian_v50.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 30; KSampler #1 · cfg = 8; KSampler #1 · sampler_name = dpmpp_2m; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; KSampler #2 · steps = 30; KSampler #2 · cfg = 8; KSampler #2 · sampler_name = dpmpp_2m; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 1; KSampler #3 · steps = 30; KSampler #3 · cfg = 8; KSampler #3 · sampler_name = dpmpp_2m; KSampler #3 · scheduler = karras; KSampler #3 · denoise = 1; KSampler #4 · steps = 30; KSampler #4 · cfg = 8; KSampler #4 · sampler_name = dpmpp_2m; KSampler #4 · scheduler = karras; KSampler #4 · denoise = 1; KSampler #5 · steps = 30; KSampler #5 · cfg = 8; KSampler #5 · sampler_name = dpmpp_2m; KSampler #5 · scheduler = karras; KSampler #5 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 86. Patreon_Workflow/Img2Exp.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/Img2Exp.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `015038a14dad5f9515f0be09c5ce4480c94d99859b3baa276af2019723c630ed` · Git blob `24b6b2a0778a9eb456ff667aa3750aebc8cf7b7c`.

Create idle, smile, shy, sadness, crying and anger images using a reference adapter and six local face-redraw branches.

Catalog ID: `ecjojo-patreon-workflow-img2exp-336cb1f9`. Graph: 47 stored / 47 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #64 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #80; saved image via SaveImage #42; saved image via SaveImage #46; saved image via SaveImage #44; saved image via SaveImage #45; saved image via SaveImage #43; image preview via PreviewImage #73

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CannyEdgePreprocessor`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `LoadImage`, `LoraLoader`, `Note`, `PreviewImage`, `Reroute`, `SaveImage`, `UltralyticsDetectorProvider`.

**明確模型選擇：** `ip-adapter-full-face_sd15.bin` (IPAdapterModelLoader #3); `model.safetensors` (CLIPVisionLoader #5); `control_v11p_sd15_canny.pth` (ControlNetLoader #89); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #99); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #117)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required ControlNet auxiliary preprocessors; Advanced-ControlNet where used

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 87. Patreon_Workflow/LoraTesting(LCM_Lightning_TCD).json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/LoraTesting%28LCM_Lightning_TCD%29.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `fa2f93f5988dfaca0001923a35a322afeea6b082d9b699f51a37759b37599017` · Git blob `fbfb83fa65b3d84ce71e7f8ad5cd600fbf54f87a`.

Compare SDXL LCM, Lightning and TCD LoRA sampling branches at shared canvas/seed inputs.

Catalog ID: `ecjojo-patreon-workflow-loratesting-lcm-lightning-tcd-b244d3e8`. Graph: 27 stored / 27 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** No uploaded media input; configure graph prompts/controls. Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #34; saved image via SaveImage #35; saved image via SaveImage #18

**節點類型：** `CLIPTextEncode`, `CM_SDXLResolution`, `CR Seed`, `CR Text`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `SaveImage`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `sdxl_vae.safetensors` (VAELoader #41); `SDXL/juggernautXL_v8Rundiffusion.safetensors` (CheckpointLoaderSimple #1); `SDXL-Lightning/sdxl_lightning_4step_lora.safetensors` (LoraLoader #3); `TCD-SDXL-LoRA.safetensors` (LoraLoader #4); `lcm/SDXL/pytorch_lora_weights.safetensors` (LoraLoader #2)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Comfyroll nodes

**已存純量參數：** KSampler #1 · steps = 8; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = sgm_uniform; KSampler #1 · denoise = 1; EmptyLatentImage #1 · batch_size = 1; KSampler #2 · steps = 8; KSampler #2 · cfg = 2; KSampler #2 · sampler_name = dpmpp_2m; KSampler #2 · scheduler = sgm_uniform; KSampler #2 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; LoraLoader #2 · strength_model = 1; LoraLoader #2 · strength_clip = 1; LoraLoader #3 · strength_model = 1; LoraLoader #3 · strength_clip = 1; KSampler #3 · steps = 8; KSampler #3 · cfg = 2; KSampler #3 · sampler_name = euler_ancestral; KSampler #3 · scheduler = sgm_uniform; KSampler #3 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 88. Patreon_Workflow/LunarNewYearCard.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/LunarNewYearCard.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `21fe1fd09a046cdc105b454e753615615827c95ebe8d053dfd7c074c8268ac55` · Git blob `b3a8b3e1cc3fe8dcc32a4fb9e9d3ddc0a5ea7d68`.

Create a reference-conditioned portrait/card using Canny control, face swap, model upscaling and a redraw stage.

Catalog ID: `ecjojo-patreon-workflow-lunarnewyearcard-33a41480`. Graph: 25 stored / 25 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #72 (effective explicit path); LoadImage #16 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #9; saved image via SaveImage #50; saved image via SaveImage #74; saved image via SaveImage #77

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `ImageUpscaleWithModel`, `KSampler`, `LoadImage`, `LoraLoader`, `ReActorFaceSwap`, `SaveImage`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #68); `ip-adapter-full-face_sd15.safetensors` (IPAdapterModelLoader #67); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4); `control_v11p_sd15_canny_fp16.safetensors` (ControlNetLoader #44); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #54); `inswapper_128.onnx` (ReActorFaceSwap #73); `codeformer.pth` (ReActorFaceSwap #73); `4x_NMKD-Siax_200k.pth` (UpscaleModelLoader #76)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ReActor + InsightFace/ONNXRuntime + face restore dependencies

**已存純量參數：** LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 13; KSampler #1 · cfg = 1.7; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 1; KSampler #2 · steps = 12; KSampler #2 · cfg = 1; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 0.25. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 89. Patreon_Workflow/Palworld2girl.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/Palworld2girl.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `5a4b721e2c75ab2aaca726d16385011c3fd2762af921098d2dac5a42ad1819bc` · Git blob `3ff033006321d4ca84870e36e93b9a4bffbe73c6`.

Use multiple creature/image references with adapter and concatenated text-conditioning branches to generate humanoid variants.

Catalog ID: `ecjojo-patreon-workflow-palworld2girl-8cad1982`. Graph: 42 stored / 42 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #108 (effective explicit path); LoadImage #109 (effective explicit path); LoadImage #107 (effective explicit path); LoadImage #106 (effective explicit path); LoadImage #72 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #169; saved image via SaveImage #166; saved image via SaveImage #172; saved image via SaveImage #163; saved image via SaveImage #173

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ConditioningConcat`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `KSampler`, `LoadImage`, `SaveImage`, `VAEDecode`.

**明確模型選擇：** `ip-adapter-plus_sd15.safetensors` (IPAdapterModelLoader #67); `model.safetensors` (CLIPVisionLoader #68); `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 30; KSampler #1 · cfg = 5; KSampler #1 · sampler_name = dpmpp_2m; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1; KSampler #2 · steps = 30; KSampler #2 · cfg = 5; KSampler #2 · sampler_name = dpmpp_2m; KSampler #2 · scheduler = karras; KSampler #2 · denoise = 1; KSampler #3 · steps = 30; KSampler #3 · cfg = 5; KSampler #3 · sampler_name = dpmpp_2m; KSampler #3 · scheduler = karras; KSampler #3 · denoise = 1; KSampler #4 · steps = 30; KSampler #4 · cfg = 5; KSampler #4 · sampler_name = dpmpp_2m; KSampler #4 · scheduler = karras; KSampler #4 · denoise = 1; KSampler #5 · steps = 30; KSampler #5 · cfg = 5; KSampler #5 · sampler_name = dpmpp_2m; KSampler #5 · scheduler = karras; KSampler #5 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 90. Patreon_Workflow/ProfessionalPortrait.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/ProfessionalPortrait.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `5d335d9fa15e32e82e5073aabb5be4a9d2e5558537c145143c41ec5347bbc331` · Git blob `a0731c6293bf0e8ac498cee82c67ddbbb2a48e55`.

Use a pose/background composition reference and a face reference to generate a portrait, then perform face swapping.

Catalog ID: `ecjojo-patreon-workflow-professionalportrait-371f5ee8`. Graph: 22 stored / 22 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #16 (effective explicit path); LoadImage #31 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #32

**節點類型：** `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `EmptyLatentImage`, `IPAdapterApply`, `IPAdapterModelLoader`, `ImageSize(FaceParsing)`, `JWImageResizeByLongerSide`, `KSamplerAdvanced`, `LoadImage`, `Note`, `ReActorFaceSwap`, `SaveImage`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `ip-adapter-full-face_sd15.safetensors` (IPAdapterModelLoader #47); `control_v11p_sd15_openpose_fp16.safetensors` (ControlNetLoader #51); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #40); `model.safetensors` (CLIPVisionLoader #45); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #41); `yolox_l.onnx` (DWPreprocessor #36); `dw-ll_ucoco_384_bs5.torchscript.pt` (DWPreprocessor #36); `inswapper_128.onnx` (ReActorFaceSwap #30); `codeformer.pth` (ReActorFaceSwap #30)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated ControlNet auxiliary preprocessors; Advanced-ControlNet where used ReActor + InsightFace/ONNXRuntime + face restore dependencies JWNodes

**已存純量參數：** EmptyLatentImage #1 · batch_size = 4. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.

### 91. Patreon_Workflow/SVD.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/SVD.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `bfa8f04ef56ef8539e76e5e2261c0df42ce77c304add7c7af645b3127e1101fd` · Git blob `f69bd692b548423ad71156beb83007fa0d480946`.

Resize-derived SVD image-to-video with FreeU, an attached LCM LoRA, RIFE interpolation and MP4 encoding.

Catalog ID: `ecjojo-patreon-workflow-svd-17e36365`. Graph: 17 stored / 17 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #23 (effective explicit path) No diffusion text prompt inferred.

**輸出：** MP4/video frames via VHS_VideoCombine #26

**節點類型：** `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `FreeU_V2`, `Get image size`, `ImageOnlyCheckpointLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `LoraLoader`, `Note`, `RIFE VFI`, `SVD_img2vid_Conditioning`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.

**明確模型選擇：** `rife40.pth` (RIFE VFI #30); `SVD/svd_xt.safetensors` (ImageOnlyCheckpointLoader #15); `lcm/SDXL/pytorch_lora_weights.safetensors` (LoraLoader #44); `majicmixRealistic_v7.safetensors` (CheckpointLoaderSimple #45)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. VideoHelperSuite and FFmpeg frame-interpolation package and RIFE checkpoint JWNodes Video decode/encode and frame-batch memory; no audio-driven motion unless separately added.

**已存純量參數：** KSampler #1 · steps = 25; KSampler #1 · cfg = 1; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = sgm_uniform; KSampler #1 · denoise = 1; LoraLoader #1 · strength_model = 1; LoraLoader #1 · strength_clip = 1; VHS_VideoCombine #1 · frame_rate = 15. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Graph attaches an SDXL-labelled LCM LoRA to the SVD model and supplies CLIP from a separate SD 1.5 checkpoint. Weight-shape/model-family compatibility is unproven; do not assume this accelerates SVD successfully.

### 92. Patreon_Workflow/SimpleDraw2Img.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/SimpleDraw2Img.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `0e528bc3729b09c93a03742b101eed1070e899b36b3b93f6a8d57e98a618ffa4` · Git blob `b24acc12e3b30f4b0090ea2287101b0bfdc89df3`.

Convert an interactive Painter sketch to a toon image with scribble ControlNet and LCM LoRA.

Catalog ID: `ecjojo-patreon-workflow-simpledraw2img-39d50b65`. Graph: 14 stored / 14 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** PainterNode #33 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #107

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `LoraLoader`, `Note`, `PainterNode`, `SaveImage`, `VAEDecode`, `VAELoader`.

**明確模型選擇：** `toonAme_version10.safetensors` (CheckpointLoaderSimple #20); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #35); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `control_v11p_sd15_scribble.pth` (ControlNetLoader #37)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Painter frontend extension; manually supplied sketch canvas

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 10; KSampler #1 · cfg = 2; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = exponential; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Painter depends on an interactive frontend canvas; a server-only form needs a reviewed drawing/upload adapter.

### 93. Patreon_Workflow/img2img_facedeailer.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/img2img_facedeailer.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `afec8b7b5f879ed35b17bd04b68f1129a9870a62e747fe2d2e18c80d6695a9e4` · Git blob `e8d27ce13ee0b7f3c4985bf596793f5b1c7e1db5`.

Compare redraws from an uploaded image across multiple checkpoints with LCM and face-detail stages.

Catalog ID: `ecjojo-patreon-workflow-img2img-facedeailer-6e5f218f`. Graph: 31 stored / 31 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #12 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** saved image via SaveImage #24; saved image via SaveImage #36; saved image via SaveImage #37

**節點類型：** `CLIPTextEncode`, `CheckpointLoaderSimple`, `FaceDetailer`, `JWImageResizeByShorterSide`, `KSampler`, `LoadImage`, `LoraLoader`, `SaveImage`, `Text box`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncode`, `VAELoader`.

**明確模型選擇：** `dreamshaper_8.safetensors` (CheckpointLoaderSimple #4); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #16); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #41); `helloflatart_V12a.safetensors` (CheckpointLoaderSimple #39); `vae-ft-mse-840000-ema-pruned.safetensors` (VAELoader #38); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #23); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #40); `LCM_LoRA_Weights_SD15.safetensors` (LoraLoader #42)

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. Impact Pack / Impact Subpack; detector dependencies and compatible versions required JWNodes

**已存純量參數：** LoraLoader #1 · strength_model = 0.5; LoraLoader #1 · strength_clip = 0.5; LoraLoader #2 · strength_model = 0.5; LoraLoader #2 · strength_clip = 0.5; KSampler #1 · steps = 10; KSampler #1 · cfg = 1.5; KSampler #1 · sampler_name = lcm; KSampler #1 · scheduler = normal; KSampler #1 · denoise = 0.6; KSampler #2 · steps = 10; KSampler #2 · cfg = 1.5; KSampler #2 · sampler_name = lcm; KSampler #2 · scheduler = normal; KSampler #2 · denoise = 0.6; KSampler #3 · steps = 10; KSampler #3 · cfg = 1.5; KSampler #3 · sampler_name = lcm; KSampler #3 · scheduler = normal; KSampler #3 · denoise = 0.6; LoraLoader #3 · strength_model = 0.5; LoraLoader #3 · strength_clip = 0.5. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.

### 94. Patreon_Workflow/talkingheadanime.json

[Pinned graph](https://github.com/ecjojo/ComfyUI-Templates/blob/adc9ef7659db315053fd1b9863ca285fa3fcccb1/Patreon_Workflow/talkingheadanime.json) · commit `adc9ef7659db315053fd1b9863ca285fa3fcccb1` · SHA-256 `7403c48b8b4dc80be8bbe4dd8d67cc1fbb10ee1c1067079d2279c845f6f26c1a` · Git blob `34fdab5cf9bb8c63fd1514abe51e8b7b239ad083`.

Generate a still half-body anime avatar with pose/reference conditioning and face detail; no speech or video animation node is present.

Catalog ID: `ecjojo-patreon-workflow-talkingheadanime-e703aa71`. Graph: 22 stored / 22 active nodes. Structural check: **pass**. GPU/provider/CPU graph execution: **not performed**.

**輸入：** LoadImage #22 (effective explicit path); LoadImage #79 (effective explicit path) Prompts exist in text-conditioning/utility nodes; prompt text deliberately omitted.

**輸出：** image preview via PreviewImage #76; saved image via SaveImage #74; saved image via SaveImage #42

**節點類型：** `CLIPSetLastLayer`, `CLIPTextEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `IPAdapterApply`, `IPAdapterModelLoader`, `Image Rembg (Remove Background)`, `KSampler`, `LoadImage`, `Note`, `PreviewImage`, `SaveImage`, `UltralyticsDetectorProvider`, `VAEDecode`.

**明確模型選擇：** `model.safetensors` (CLIPVisionLoader #5); `ip-adapter-plus_sd15.safetensors` (IPAdapterModelLoader #3); `control_v11p_sd15_openpose_fp16.safetensors` (ControlNetLoader #80); `meinamix_meinaV11.safetensors` (CheckpointLoaderSimple #2); `bbox/face_yolov8m.pt` (UltralyticsDetectorProvider #72)

**隱含模型選擇：** `u2net` (Image Rembg (Remove Background) #75); `isnet-anime` (Image Rembg (Remove Background) #73).

**執行環境：** Separate ComfyUI installation; exact graph/node schema compatibility is unverified. Local diffusion/model inference; GPU memory and installed model coverage have not been measured. ComfyUI_IPAdapter_plus; legacy IPAdapterApply layout must be pinned or migrated Impact Pack / Impact Subpack; detector dependencies and compatible versions required WAS Node Suite and node-specific ML dependencies

**已存純量參數：** EmptyLatentImage #1 · width = 512; EmptyLatentImage #1 · height = 512; EmptyLatentImage #1 · batch_size = 1; KSampler #1 · steps = 25; KSampler #1 · cfg = 8.5; KSampler #1 · sampler_name = dpmpp_2m_sde; KSampler #1 · scheduler = karras; KSampler #1 · denoise = 1. Connected/computed values and unknown widget layouts are not resolved.

**缺口與後續動作：**

- No upstream redistribution license is declared; retain source-reference treatment until a grant is recorded.
- No local graph, WebUI parameter mapping, API-prompt conversion, runner binding or ecjojo execution fixture is present.
- Qualify the exact node versions, model files, supplied inputs and output handling in native ComfyUI before claiming readiness.
- Legacy IPAdapterApply widget/socket layouts require a compatible package revision or an explicit graph migration.
- Catalog explicit-filename inventory omits internal model presets/downloads: isnet-anime, u2net. Pin artifacts and licenses separately.
- Despite its filename, this is a still-image avatar workflow; audio-driven animation and lip-sync are absent.

## Artifact contents

`templates-audit-20261110.json` is the machine-readable companion. It contains every node and socket inventory, model selector, source/graph/app hash, package declarations, input consumers, output sinks, structural diagnostics, all tracked non-JSON asset hashes, README URLs, and recipe-to-graph mappings. No raw workflow prompt text or credentials are copied into the report.
