# dseditor ComfyuiWorkflows：完整來源與公開應用程式稽核

> **歷史基準報告：** 本報告比較的是公開版本 `95e2079ed3ae47f76ff67948f80673f410abd407`，不是後續修正後的即時狀態。新修正、測試與尚未完成的執行驗證請見 [稽核索引](README.md)。上游工作流實際 GPU 驗證數仍為 **0**。

Inspected on 2026-11-10. Source commit `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`; public checkout baseline `95e2079ed3ae47f76ff67948f80673f410abd407`. Reports describe the public app before the separately authorized fixes.

The audit read every plain JSON, all 35 Markdown files and supporting scripts, all 58 PNG workflow metadata payloads, and six readable archive graphs. Six encrypted archive members are listed but could not be studied. No image pixels, user media, credentials, model files or runtime databases were inspected; no upstream code or generation was executed.

| Evidence category | Count |
| --- | ---: |
| Plain JSON files | 143 |
| Plain workflow graphs | 139 (138 visual, 1 API) |
| Non-workflow JSON | 4 |
| Additional readable PNG / ZIP graphs | 58 / 6 |
| Encrypted ZIP JSON members, uninspected | 6 |
| Readable graph representations / unique byte payloads | 203 / 202 |
| Plain graphs passing / failing top-level checker | 118 / 21 |
| All readable graphs passing / failing checker | 166 / 37 |
| Physical public copies / distinct upstream paths | 40 / 39 |
| Public dseditor library entries | 140 |
| Audited graph GPU runs established | 0 |

A static pass is not runtime qualification. A static failure is an exact incompatibility with the public strict validator; some legacy frontend routing may reconstruct nonreciprocal links, so repair requires understanding the graph rather than deleting links blindly. All 143 plain JSONs parse successfully.

## 主要發現

- **WF-01** Plain JSON catalog is complete for the 139 graph files but excludes 58 PNG workflow metadata graphs and 6 readable ZIP graphs. Six additional ZIP JSONs are encrypted. Counts must label these denominators.
- **WF-02** 40 physical public dseditor files represent 39 source paths: sdxlhandfix is copied in both baseline and curated locations. The root/nested Kontext sources are byte-identical but only root has a public copy. Runtime library has 140 dseditor entries due the extra baseline hand-fix file.
- **WF-03** 21 plain graphs and 16 embedded/archive graphs fail the public strict topology checker. All source JSON parses; these are graph/link issues, not malformed JSON syntax. Specialized frontend routing may affect interpretation.
- **WF-04** Eleven catalog inventories omit active nested dependencies. Sanitizer drops modern definitions; only workflow/ wrappers are explicitly rejected. Modern UUID and workflow> group forms need support or explicit rejection.
- **WF-05** NPNet curated copy contains an unresolved model-path placeholder. Hosted checkpoint IDs in ArtCompare and the two line-art coloring graphs cannot be silently converted to filenames.
- **WF-06** Source-reference import refusal wrongly says no redistribution license for GPL dseditor entries. Catalog selection and license authorization are separate causes.
- **WF-07** Public library has download/save-to-native-editor integration, not per-graph app execution. It only preflights registered nodes and recognized model-loader layouts; node presence cannot verify custom loaders, provider credentials, input media, masks, kernels, VRAM or output quality.
- **WF-08** Upstream Gradio try-on uses fixed clothes/people filenames and selects latest output globally instead of prompt-specific outputs; concurrent jobs can mix inputs/results. Its Windows launcher downloads models and globally kills other Python processes on exit. These scripts must not be reused as the public runner.
- **WF-09** YTUploader is executable publishing code requiring OAuth and an explicit user-requested upload. Treating every JSON as a generation workflow would hide external side effects.
- **WF-10** 89 public static tests passed. No audited graph was queued, no GPU run was performed, no runtime node/model inventory was queried, and public demo recordings for other builders are not proof for these exact source graphs.

## 公開版本整合與證據

`manifests/community_workflows/catalog.json` indexes all 139 plain dseditor graphs; 24 are curated copies and 115 are catalogued as source references. `imported_sources.json` adds 16 physical baseline files, 15 from otherwise reference-only paths and one additional copy of sdxlhandfix. The library merges most baseline provenance into the original IDs and discovers the extra hand-fix copy as another entry. The root and nested FLUX Kontext rendering sources have identical bytes, but separate catalog entries.

A local library entry supports guide/details, JSON download and save into the native ComfyUI workflow sidebar. `handle_post` reads `/object_info` and writes `/userdata/...`; it does not call `/prompt`. There is no exact source-graph app runner mapping. Existing portrait, H3 or Qwen builders may use related methods/models, but that is not provenance or execution proof for these graphs.

Static tests run: `pytest-disktmp uv run --with pytest pytest -q scripts/test_community_workflows.py scripts/test_workflow_library.py scripts/test_workflow_guides.py` — **89 passed in 0.11s**. The wrapper used disk storage under `~/agent-work/tmp/pytest/` and cleaned it. No live node/model probe, provider request, model download or GPU generation was performed.

## 逐項審閱

Each item below includes its own pipeline review, source hash, model selections, active inputs/output sinks, dependencies and exact public linkage. The JSON companion additionally includes full node inventories and top-level links. Model filenames are observed strings, not an installation manifest; inactive branches are explicitly retained.

### 000 — `Animate/4StepAndMusetalk.json`

Generate AnimateDiff Lightning frames from a reference and prompt schedule, interpolate with RIFE, then drive MuseTalk from uploaded audio. Requires motion/SparseCtrl weights and MuseTalk audio/video plumbing; the saved graph has a stale output link.

- 容器／格式： `json_file` / `visual`. SHA-256: `463203916d744c924d59999d6ee5fbb1dbcdd59233577cffd7b5fd89190450f4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 34 top-level nodes; 34 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `ACN_AdvancedControlNetApply`, `ACN_SparseCtrlLoaderAdvanced`, `ACN_SparseCtrlRGBPreprocessor`, `ACN_SparseCtrlSpreadMethodNode`, `ADE_AnimateDiffLoaderWithContext`, `ADE_AnimateDiffUniformContextOptions`, `ADE_EmptyLatentImageLarge`, `BatchPromptSchedule`, `CheckpointLoaderSimple`, `Fast Groups Bypasser (rgthree)`, `FreeU_V2`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImpactFloat`, `ImpactSwitch`, `KSamplerAdvanced //Inspire`, `LayerUtility: PurgeVRAM`, `LoadImage`, `MuseTalkCupAudio`, `MuseTalkRun`, `RIFE VFI`, `ScaledSoftControlNetWeights`, `ShowText|pysssss`, `Simple String`, `SimpleMath+`, `String Literal`, `VAEDecode`, `VHS_FILENAMES_STRING_MuseTalk`, `VHS_LoadAudio`, `VHS_VideoCombine`, `WD14Tagger|pysssss`.
- 明確模型檔名： `animatediff_lightning_4step_comfyui.safetensors`, `controlnetanimediff/v3_sd15_sparsectrl_rgb.ckpt`, `dreamshaper_8.safetensors`, `rife47.pth`.
- 輸入： 167: String Literal, 166: BatchPromptSchedule, 219: LoadImage, 245: String Literal, 237: VHS_LoadAudio.
- 輸出： 236: VHS_VideoCombine, 134: VHS_VideoCombine, 250: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved; ComfyUI_essentials; rgthree-comfy; ComfyUI-Custom-Scripts; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-4stepandmusetalk-f7a630e1`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 001 — `Animate/SVD3mix.json`

Animate one image through several AnimateLCM-SVD branches with depth ControlNet, sharpening, upscaling, optional ReActor face replacement and RIFE. This is a multi-branch comparison, not three independent qualified runners; reciprocal links need review.

- 容器／格式： `json_file` / `visual`. SHA-256: `56b3dde80b6e9f54d8703e66115379ac685b64756dfaac47613cb218a40ed184`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 54 top-level nodes; 54 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `ACN_AdvancedControlNetApply`, `Anything Everywhere3`, `ControlNetLoaderAdvanced`, `DepthAnythingPreprocessor`, `Fast Groups Bypasser (rgthree)`, `GetImageSize+`, `ImageCASharpening+`, `ImageOnlyCheckpointLoader`, `ImageUpscaleWithModel`, `ImpactMakeImageBatch`, `Int Literal`, `JWImageResizeByFactor`, `JWImageResizeByLongerSide`, `KSampler`, `LatentFromBatch`, `LayerColor: ColorAdapter`, `LoadImage`, `Note`, `PreviewImage`, `RIFE VFI`, `ReActorFaceSwap`, `SVD_img2vid_Conditioning`, `UpscaleModelLoader`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.
- 明確模型檔名： `4x-UltraSharp.pth`, `AnimateLCM-SVD-xt-1-1_fp16_comfy.safetensors`, `controlnet-depth-svd-v1/diffusion_pytorch_model.safetensors`, `depth_anything_vitl14.pth`, `inswapper_128.onnx`, `rife47.pth`.
- 輸入： 57: LoadImage.
- 輸出： 177: PreviewImage, 175: PreviewImage, 179: PreviewImage, 110: VHS_VideoCombine, 109: VHS_VideoCombine.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI_essentials; rgthree-comfy; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-svd3mix-c77dae57`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 002 — `Animate/【AnimateDiff】DifferentialDiffusionSimple.json`

Restyle an input video with AnimateDiff, depth/line controls and Differential Diffusion; RMBG masks and Florence captions condition/composite the frames. Florence and background removal can require additional downloaded weights.

- 容器／格式： `json_file` / `visual`. SHA-256: `04f70fa6a7a3a1d64f140fd3571585b7560990d67530ddf2c94971ead72956e9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 39 top-level nodes; 39 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply_v2`, `ADE_AnimateDiffUniformContextOptions`, `ADE_ApplyAnimateDiffModelSimple`, `ADE_LoadAnimateDiffModel`, `ADE_UseEvolvedSampling`, `AnyLineArtPreprocessor_aux`, `CLIPTextEncode`, `CheckpointLoaderSimpleWithNoiseSelect`, `ControlNetLoader`, `DepthAnythingPreprocessor`, `DifferentialDiffusion`, `DownloadAndLoadFlorence2Model`, `Florence2Run`, `FreeU_V2`, `ImageBlur`, `ImageCompositeMasked`, `ImageFromBatch`, `ImageResize+`, `ImageToMask`, `KSampler`, `MaskToImage`, `RMBG`, `RemapImageRange`, `SetLatentNoiseMask`, `ShowText|pysssss`, `SimpleCondition+`, `SimpleMathBoolean+`, `SimpleMathDual+`, `SimpleMathInt+`, `Text Multiline`, `VAEDecode`, `VAEEncode`, `VAELoader`, `VHS_LoadVideo`, `VHS_VideoCombine`.
- 明確模型檔名： `animatediff_lightning_8step_comfyui.safetensors`, `control_v11f1p_sd15_depth.pth`, `control_v11p_sd15_lineart_fp16.safetensors`, `darkSushiMixMix_225D.safetensors`, `depth_anything_vitl14.pth`, `vae-ft-mse-840000-ema-pruned.safetensors`.
- 輸入： 25: CLIPTextEncode, 26: CLIPTextEncode, 111: Text Multiline, 11: VHS_LoadVideo.
- 輸出： 101: VHS_VideoCombine, 112: ShowText|pysssss.
- 相依套件： comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-animatediff-differentialdiffusionsimple-b2aed83c`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 003 — `Animate/【Animatediff】InstanceDiffusion.json`

Animate reference subjects along SplineEditor coordinates using InstanceDiffusion tracking, IPAdapter and AnimateDiff. Requires downloaded InstanceDiffusion models, a tracking editor frontend and functioning Set/Get routing.

- 容器／格式： `json_file` / `visual`. SHA-256: `8f2199388e6af0a99066ce6bea939a11c02bd091797bba97ad5f6bc647aafce5`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 47 top-level nodes; 40 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ADE_AnimateDiffLoaderGen1`, `ADE_StandardUniformContextOptions`, `AppendInstanceDiffusionTracking`, `ApplyScaleUModelNode`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `CreateInstanceDiffusionTracking`, `DownloadInstanceDiffusionModels`, `EmptyLatentImage`, `GetMaskSizeAndCount`, `GetNode`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImageConcanate`, `InstanceDiffusionTrackingPrompt`, `KSampler //Inspire`, `LoadImage`, `PlotCoordinates`, `Reroute`, `SetNode`, `SplineEditor`, `VAEDecode`, `VHS_VideoCombine`.
- 明確模型檔名： `animatediff_lightning_4step_comfyui.safetensors`, `dreamshaper_8.safetensors`.
- 輸入： 1: InstanceDiffusionTrackingPrompt, 17: CLIPTextEncode, 18: CLIPTextEncode, 133: LoadImage.
- 輸出： 23: VHS_VideoCombine [mode 4], 116: VHS_VideoCombine.
- 相依套件： ComfyUI-Inspire-Pack; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-AnimateDiff-Evolved; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-animatediff-instancediffusion-85947329`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 004 — `Animate/【EasyAnimate】AllinOne.json`

Compare EasyAnimate text-to-video and image-to-video branches, with Florence/Superprompt assistance, image batching, upscaling and RIFE. Several image branches are muted; loader model selectors rather than explicit weight filenames determine core video dependencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `8a4ccaf0a5ab0d57a3ee64c077c9deab29e02bd7774b3c3c723dfacc4627a24e`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 40 top-level nodes; 21 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CR Text Concatenate`, `DownloadAndLoadFlorence2Model`, `EasyAnimateI2VSampler`, `EasyAnimateT2VSampler`, `Fast Groups Muter (rgthree)`, `Florence2Run`, `ImageCASharpening+`, `ImageListToImageBatch`, `ImageUpscaleWithModel`, `ImpactMakeImageList`, `ImpactSwitch`, `JWImageResizeByFactor`, `LoadEasyAnimateModel`, `LoadImage`, `Note`, `RIFE VFI`, `SDXLPromptStyler`, `ShowText|pysssss`, `String Literal`, `Superprompt`, `TextBox`, `UpscaleModelLoader`, `VHS_VideoCombine`.
- 明確模型檔名： `4x-UltraSharp.pth`, `rife47.pth`.
- 輸入： 84: LoadImage [mode 2], 99: LoadImage [mode 2], 88: String Literal [mode 2], 120: SDXLPromptStyler, 119: Superprompt, 87: String Literal, 112: String Literal [mode 2], 111: LoadImage [mode 2], 117: String Literal, 124: LoadImage, 123: LoadImage, 107: String Literal.
- 輸出： 17: VHS_VideoCombine [mode 2], 98: VHS_VideoCombine [mode 2], 108: VHS_VideoCombine, 121: ShowText|pysssss, 113: VHS_VideoCombine [mode 2], 122: VHS_VideoCombine.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; rgthree-comfy; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-easyanimate-allinone-6f209dd2`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 005 — `Animate/【EasyAnimate】ConceptVideo.json`

Use Ollama and translation to build text/concept panels, generate EasyAnimate video, and combine repeated text/image frames. Dangling graph links and Ollama/translation services must be resolved before use.

- 容器／格式： `json_file` / `visual`. SHA-256: `6df67d7bc586a0583f92fdd09150016298cc413e31d727ea234af4038d5b0cfe`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 26 top-level nodes; 26 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `CR Text Concatenate`, `EasyAnimateT2VSampler`, `EmptyImage`, `GoogleTranslateTextNode`, `ImageListToImageBatch`, `ImpactMakeImageBatch`, `LayerStyle: DropShadow`, `LayerUtility: SimpleTextImage`, `LoadEasyAnimateModel`, `OllamaGenerateAdvance`, `PreviewImage`, `RepeatImageBatch`, `ShowText|pysssss`, `String Literal`, `Text Find and Replace`, `TextBox`, `TextSplitByDelimiter`, `VHS_VideoCombine`.
- 明確模型檔名： none observed; inspect model selectors, implicit downloads and provider models.
- 輸入： 110: String Literal, 4: String Literal, 124: LayerUtility: SimpleTextImage, 114: LayerUtility: SimpleTextImage, 5: String Literal.
- 輸出： 2: ShowText|pysssss, 8: ShowText|pysssss, 111: VHS_VideoCombine, 125: PreviewImage, 115: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Google translation service/custom-node backend; network behavior and provider configuration must be checked; Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-animate-easyanimate-conceptvideo-dbd4a62e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 006 — `Animate/【Lightinng】IPAdapter.json`

Animate a scheduled set of reference images through IPAdapterBatch weights, AnimateDiff Lightning and SparseCtrl, then interpolate with RIFE. Input schedules and weight strategy must align; stale links remain.

- 容器／格式： `json_file` / `visual`. SHA-256: `f956dc73d9065df4282a2c41125ffa319eeffdd13ff7b130ce2d10899770dae2`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 31 top-level nodes; 31 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `ACN_AdvancedControlNetApply`, `ACN_SparseCtrlLoaderAdvanced`, `ACN_SparseCtrlRGBPreprocessor`, `ACN_SparseCtrlSpreadMethodNode`, `ADE_AnimateDiffLoaderWithContext`, `ADE_AnimateDiffUniformContextOptions`, `ADE_EmptyLatentImageLarge`, `BatchPromptSchedule`, `CheckpointLoaderSimple`, `FreeU_V2`, `IPAdapterBatch`, `IPAdapterNoise`, `IPAdapterPromptScheduleFromWeightsStrategy`, `IPAdapterUnifiedLoader`, `IPAdapterWeights`, `ImageBatchMultiple+`, `JWImageResizeByLongerSide`, `KSamplerAdvanced //Inspire`, `LoadImage`, `Note`, `RIFE VFI`, `SetNode`, `String Literal`, `VAEDecode`, `VHS_VideoCombine`.
- 明確模型檔名： `animatediff_lightning_4step_comfyui.safetensors`, `controlnetanimediff/v3_sd15_sparsectrl_rgb.ckpt`, `dreamshaper_8.safetensors`, `rife47.pth`.
- 輸入： 235: IPAdapterPromptScheduleFromWeightsStrategy, 246: LoadImage, 245: LoadImage, 239: LoadImage, 241: String Literal, 234: BatchPromptSchedule, 238: LoadImage.
- 輸出： 134: VHS_VideoCombine.
- 相依套件： ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_essentials; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-lightinng-ipadapter-97360c9b`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 007 — `Animate/【SVD】SUPIR.json`

Animate an image with AnimateLCM-SVD and enhance frames through SUPIR, UltraSharp and RIFE. Two different diffusion stacks and a large SUPIR checkpoint are required; reciprocal links fail the static check.

- 容器／格式： `json_file` / `visual`. SHA-256: `12816eae15745d2392c4fd838305a6ed69b1ea3cd7b3e438cc77b26e47db3114`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 53 top-level nodes; 53 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `Anything Everywhere3`, `CheckpointLoaderSimple`, `ColorMatch`, `Fast Groups Bypasser (rgthree)`, `GetImageSize+`, `ImageCASharpening+`, `ImageFromBatch`, `ImageOnlyCheckpointLoader`, `ImageUpscaleWithModel`, `ImpactMakeImageBatch`, `Int Literal`, `JWImageResizeByFactor`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `Note`, `PreviewImage`, `RIFE VFI`, `SUPIR_conditioner`, `SUPIR_decode`, `SUPIR_encode`, `SUPIR_first_stage`, `SUPIR_model_loader_v2`, `SUPIR_sample`, `SVD_img2vid_Conditioning`, `Simple String`, `UpscaleModelLoader`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.
- 明確模型檔名： `4x-UltraSharp.pth`, `AnimateLCM-SVD-xt-1-1_fp16_comfy.safetensors`, `SUPIR/SUPIR-v0Q.ckpt`, `dreamshaperXL_lightningDPMSDE.safetensors`, `rife47.pth`.
- 輸入： 57: LoadImage.
- 輸出： 109: VHS_VideoCombine, 110: VHS_VideoCombine, 175: PreviewImage, 227: PreviewImage, 179: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; rgthree-comfy; ComfyUI-SUPIR; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-svd-supir-c02cc885`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 008 — `Animate/【SVD】svdbatchandcaption.json`

Caption a batch of images with Florence/Gemini, animate them with AnimateLCM-SVD, and create captioned/interpolated output. Requires explicit Gemini configuration plus video weights; dangling links remain.

- 容器／格式： `json_file` / `visual`. SHA-256: `04290b475d4206c0a5efd48ce826ceb362a3f2554132df19e9bba91162b87766`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 34 top-level nodes; 34 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `DownloadAndLoadFlorence2Model`, `Fast Groups Bypasser (rgthree)`, `Florence2Run`, `Gemini_API_Zho`, `GetImageSizeAndCount`, `ImageListToImageBatch`, `ImageOnlyCheckpointLoader`, `ImageUpscaleWithModel`, `ImpactImageBatchToImageList`, `ImpactMakeImageBatch`, `ImpactSwitch`, `JWImageResizeByFactor`, `JWImageResizeByLongerSide`, `KSampler`, `LayerStyle: DropShadow`, `LayerUtility: SimpleTextImage`, `LoadImage`, `RIFE VFI`, `RepeatImageBatch`, `SVD_img2vid_Conditioning`, `SaveImage`, `ShowText|pysssss`, `StringFunction|pysssss`, `Upscale Model Loader`, `VAEDecode`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.
- 明確模型檔名： `4x-UltraSharp.pth`, `AnimateLCM-SVD-xt-1-1_fp16_comfy.safetensors`, `rife47.pth`.
- 輸入： 84: LayerUtility: SimpleTextImage, 57: LoadImage, 75: LoadImage, 113: LoadImage, 100: LayerUtility: SimpleTextImage, 112: LoadImage.
- 輸出： 93: SaveImage, 81: ShowText|pysssss, 110: VHS_VideoCombine, 24: VHS_VideoCombine, 96: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-VideoHelperSuite plus compatible FFmpeg; rgthree-comfy; ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-animate-svd-svdbatchandcaption-f5e33292`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 009 — `Animate/【Toon】Toondenoise.json`

Create toon animation by gradually increasing denoise, using depth/AnimateDiff and ToonCrafter interpolation, with segmented face-detail branches. Multiple animation and inpainting models coexist; inspect active branches and mask routing.

- 容器／格式： `json_file` / `visual`. SHA-256: `c13ac1d835a81609382212dabe910cb8d2e644e9fd7c58e99e4cdd29f395e0eb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 50 top-level nodes; 50 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `ADE_AnimateDiffLoaderGen1`, `ADE_LoopedUniformContextOptions`, `Anything Everywhere3`, `CLIPTextEncode`, `ControlNetLoader`, `DetailerForEachPipeForAnimateDiff`, `DifferentialDiffusion`, `DownloadAndLoadCLIPModel`, `DownloadAndLoadCLIPVisionModel`, `DownloadAndLoadDynamiCrafterModel`, `Efficient Loader`, `Fast Groups Bypasser (rgthree)`, `FreeU_V2`, `ImageBatchSplitter //Inspire`, `ImageFromBatch`, `ImageUpscaleWithModel`, `ImpactMakeImageBatch`, `ImpactSimpleDetectorSEGS_for_AD`, `ImpactSwitch`, `InpaintModelConditioning`, `JWImageResizeByFactor`, `JWImageResizeByLongerSide`, `KSampler Gradually Adding More Denoise (efficient)`, `LayerMask: SegmentAnythingUltra`, `LoadImage`, `LoraLoader`, `MiDaS-DepthMapPreprocessor`, `Note`, `PreviewImage`, `ScaledSoftControlNetWeights`, `String Literal`, `ToBasicPipe`, `ToonCrafterInterpolation`, `UltralyticsDetectorProvider`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`, `VHS_VideoCombine`.
- 明確模型檔名： `CLIP-ViT-H-fp16.safetensors`, `RealESRGAN_x4plus_anime_6B.pth`, `add_detail.safetensors`, `bbox/face_yolov8m.pt`, `control_v11f1p_sd15_depth.pth`, `hypersd/Hyper-SD15-4steps-lora.safetensors`, `imp_v10.safetensors`, `stable-diffusion-2-1-clip-fp16.safetensors`, `tooncrafter_512_interp-fp16.safetensors`, `v3_sd15_mm.ckpt`, `vae-ft-mse-840000-ema-pruned.safetensors`.
- 輸入： 49: CLIPTextEncode, 50: CLIPTextEncode, 70: String Literal, 174: CLIPTextEncode, 97: LoadImage.
- 輸出： 172: VHS_VideoCombine, 89: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-toon-toondenoise-2e4cbc79`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 010 — `Animate/【Toon】inpaintandtoon.json`

Create revised toon keyframes by masked inpainting, choose frames, then interpolate with ToonCrafter. Preview Chooser requires interactive selection; SDXL generation and separate ToonCrafter encoders/weights need version-compatible setup.

- 容器／格式： `json_file` / `visual`. SHA-256: `557e22c7f8326f0c3713be626921429c65e0fcd58c8ab0baf3291fb1d38d663c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 32 top-level nodes; 32 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `Anything Everywhere3`, `CLIPTextEncode`, `ControlNetLoader`, `DifferentialDiffusion`, `DownloadAndLoadCLIPModel`, `DownloadAndLoadCLIPVisionModel`, `DownloadAndLoadDynamiCrafterModel`, `Efficient Loader`, `GetImageSize+`, `ImageBatchSplitter //Inspire`, `ImpactMakeImageBatch`, `InpaintModelConditioning`, `InpaintPreprocessor`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: SegmentAnythingUltra`, `LoadImage`, `Preview Chooser`, `Reroute`, `ShowText|pysssss`, `TextSplitByDelimiter`, `ToonCrafterInterpolation`, `VAEDecode`, `VAELoader`, `VHS_VideoCombine`.
- 明確模型檔名： `CLIP-ViT-H-fp16.safetensors`, `Kataragi_inpaintXL-lora128.safetensors`, `animagineXLV31_v31.safetensors`, `stable-diffusion-2-1-clip-fp16.safetensors`, `tooncrafter_512_interp-fp16.safetensors`, `vae-ft-mse-840000-ema-pruned.safetensors`.
- 輸入： 32: CLIPTextEncode, 33: CLIPTextEncode, 5: LoadImage.
- 輸出： 20: ShowText|pysssss, 31: VHS_VideoCombine, 53: Preview Chooser.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-toon-inpaintandtoon-925cd4bc`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 011 — `Animate/【hotshot】hotshotxl1step.json`

Convert video with HotshotXL and Hyper-SDXL one-step sampling, depth/line controls and IPAdapter. The one-step scheduler/model must match; reciprocal links fail the public check.

- 容器／格式： `json_file` / `visual`. SHA-256: `7ce1568678241834d62de88e74c317382ae12ba5ecafc4cd42f87dcdbedbb549`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `ADE_AnimateDiffLoaderGen1`, `ADE_StandardUniformContextOptions`, `AIO_Preprocessor`, `Anything Everywhere3`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoaderAdvanced`, `HyperSDXL1StepUnetScheduler`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `JWImageResizeByLongerSide`, `KSamplerSelect`, `LoadImage`, `SamplerCustom`, `VAEDecode`, `VAEEncode`, `VHS_BatchManager`, `VHS_LoadVideo`, `VHS_VideoCombine`.
- 明確模型檔名： `Hyper-SDXL-1step-Unet-Comfyui.fp16.safetensors`, `control-lora-depth-rank256.safetensors`, `hotshotxl_mm_v1.pth`, `t2i-adapter-lineart-sdxl-1.0.safetensors`.
- 輸入： 7: CLIPTextEncode, 25: LoadImage, 6: CLIPTextEncode, 119: VHS_LoadVideo.
- 輸出： 34: VHS_VideoCombine.
- 相依套件： comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-hotshot-hotshotxl1step-46b40c5a`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 012 — `Animate/【hotshot】hotshotxlvid2vid.json`

Convert an uploaded video through HotshotXL with SDXL, depth/line controls and an image-reference IPAdapter. VideoHelperSuite batch management and implicit CLIP/VAE routing are dependencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `abdb55476d4dcec0b04e1f0b48ca4fbefc337ddeb63c65c3e2cdc61ad8ec62b0`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 22 top-level nodes; 22 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ADE_AnimateDiffLoaderGen1`, `ADE_StandardUniformContextOptions`, `AIO_Preprocessor`, `Anything Everywhere3`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoaderAdvanced`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `VAEDecode`, `VAEEncode`, `VHS_BatchManager`, `VHS_LoadVideo`, `VHS_VideoCombine`.
- 明確模型檔名： `control-lora-depth-rank256.safetensors`, `dreamshaperXL_lightningDPMSDE.safetensors`, `hotshotxl_mm_v1.pth`, `t2i-adapter-lineart-sdxl-1.0.safetensors`.
- 輸入： 122: VHS_LoadVideo, 25: LoadImage, 7: CLIPTextEncode, 6: CLIPTextEncode.
- 輸出： 105: VHS_VideoCombine.
- 相依套件： comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-hotshot-hotshotxlvid2vid-ba374d99`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 013 — `Animate/【lightning】AnimateLightning4step.json`

Generate AnimateDiff Lightning motion with SparseCtrl, IPAdapter, prompt schedule and RIFE; an OpenPose video path is bypassed. Stale links prevent the public importer accepting the saved graph.

- 容器／格式： `json_file` / `visual`. SHA-256: `ee5ddf8689eaeab268ad9c928aabc9eccc4f126ea962f12638ee1db886fb0ebe`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 24 top-level nodes; 19 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `ACN_AdvancedControlNetApply`, `ACN_SparseCtrlLoaderAdvanced`, `ACN_SparseCtrlRGBPreprocessor`, `ACN_SparseCtrlSpreadMethodNode`, `ADE_AnimateDiffLoRALoader`, `ADE_AnimateDiffLoaderWithContext`, `ADE_AnimateDiffUniformContextOptions`, `ADE_EmptyLatentImageLarge`, `BatchPromptSchedule`, `CheckpointLoaderSimple`, `ControlNetLoader`, `DWPreprocessor`, `FreeU_V2`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `KSamplerAdvanced`, `LoadImage`, `RIFE VFI`, `String Literal`, `VAEDecode`, `VHS_LoadVideo`, `VHS_VideoCombine`, `WD14Tagger|pysssss`.
- 明確模型檔名： `animatediff_lightning_4step_comfyui.safetensors`, `control_v11p_sd15_openpose.pth`, `controlnetanimediff/v3_sd15_sparsectrl_rgb.ckpt`, `dreamshaper_8.safetensors`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `rife47.pth`, `v2_lora_ZoomIn.ckpt`, `yolox_l.onnx`.
- 輸入： 186: VHS_LoadVideo [mode 4], 166: BatchPromptSchedule, 167: String Literal, 175: LoadImage.
- 輸出： 134: VHS_VideoCombine.
- 相依套件： comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved; ComfyUI-Custom-Scripts; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animate-lightning-animatelightning4step-23c75c02`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 014 — `AnimeArt.json`

Convert anime art to a more realistic SD1.5 rendering using grouped checkpoint/control/resize/face-detail stages. The README describes soft-edge cosplay; referenced legacy group definitions contain critical nodes omitted by the catalog.

- 容器／格式： `json_file` / `visual`. SHA-256: `873b2bdfdc478e9f5bfcf0d282775ece0e44937dd1670120cfb482c139150ae1`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 9 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AIO_Preprocessor`, `CR Multi-ControlNet Stack`, `Efficient Loader`, `FaceDetailer`, `HighRes-Fix Script`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `PreviewImage`, `SelfAttentionGuidance`, `String Literal`, `Text List`, `Text List to Text`, `UltralyticsDetectorProvider`, `VAEEncode`, `WD14Tagger|pysssss`, `workflow/ControlnetMulti`, `workflow/ResizetoLATENT`, `workflow/loadcheckpoint`, `workflow/臉部細化`.
- 明確模型檔名： `RealESRGAN_x4.pth`, `add_detail.safetensors`, `bbox/face_yolov8m.pt`, `control_lora_rank128_v11f1e_sd15_tile_fp16.safetensors`, `control_v11p_sd15_lineart_fp16.safetensors`, `control_v11p_sd15_openpose.pth`, `control_v11p_sd15_softedge_fp16.safetensors`, `majicmixRealistic_v7.safetensors`.
- 輸入： root/141: String Literal, 101: LoadImage.
- 輸出： root/108: PreviewImage, root/108: PreviewImage, 132: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-animeart-2871ce3a`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: AIO_Preprocessor, CR Multi-ControlNet Stack, FaceDetailer, JWImageResizeByLongerSide, SelfAttentionGuidance, String Literal, Text List, Text List to Text, UltralyticsDetectorProvider, VAEEncode, WD14Tagger|pysssss No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 015 — `BackgroundChanger/LamaIClight.json`

Replace a background using LaMa/segmentation and SDXL guidance, then relight/composite with SD1.5 IC-Light. Two source images, distinct model families and masks feed final SaveImage outputs.

- 容器／格式： `json_file` / `visual`. SHA-256: `ec81d50b73f24134a1dcd714579fecd2d2205468528f72fa02e97f6b38ce363a`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 48 top-level nodes; 48 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `ACN_ControlNet++LoaderSingle`, `AnyLineArtPreprocessor_aux`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetLoader`, `DepthAnythingV2Preprocessor`, `DisplayAny`, `DownloadAndLoadFlorence2Model`, `EmptyLatentImage`, `Fast Groups Bypasser (rgthree)`, `Florence2Run`, `ICLightApplyMaskGrey`, `ICLightAppply`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImageBatch`, `ImageRemoveBackground+`, `ImageResize+`, `InvertMask`, `JoinImageWithAlpha`, `KSampler`, `KSamplerAdvanced //Inspire`, `LayerMask: MaskEdgeUltraDetail V2`, `LayerUtility: ImageBlendAdvance V2`, `LayerUtility: LaMa`, `LoadImage`, `Note`, `PixelPerfectResolution`, `PreviewImage`, `Reroute`, `SaveImage`, `ScaledSoftControlNetWeights`, `SolidMask`, `TransparentBGSession+`, `UNETLoader`, `VAEDecode`, `VAEEncodeArgMax`.
- 明確模型檔名： `SDXL/EcomXL_controlnet_inpaint.safetensors`, `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `aiartistHyperrayXLXL_hypersdxl.safetensors`, `depth_anything_v2_vitl.pth`, `iclight_sd15_fbc_unet_ldm.safetensors`, `mistoLine_rank256.safetensors`, `realisticVisionV60B1_v51VAE.safetensors`.
- 輸入： 263: CLIPTextEncode, 264: CLIPTextEncode, 267: CLIPTextEncode, 268: CLIPTextEncode, 20: LoadImage, 153: LoadImage.
- 輸出： 261: DisplayAny, 208: PreviewImage, 291: SaveImage, 292: SaveImage.
- 相依套件： ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Advanced-ControlNet; ComfyUI_essentials; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-backgroundchanger-lamaiclight-1c1e04a9`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 016 — `BackgroundChanger/【Backgorund】brushinpaintAddsupir.json`

Replace an image background with inverted BiRefNet masking and BrushNet, then refine using SUPIR. The graph needs separately configured BrushNet and SUPIR loaders; previews are its output sinks.

- 容器／格式： `json_file` / `visual`. SHA-256: `5f8a0b87508257c002d9bef07d9186a6a50712efa14fc90ff33d9d347f478649`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BrushNetInpaint`, `BrushNetLoader`, `BrushNetPipeline`, `CheckpointLoaderSimple`, `InvertMask`, `JWImageResizeByLongerSide`, `LayerMask: BiRefNetUltra`, `LoadImage`, `MaskToImage`, `PreviewImage`, `SUPIR_conditioner`, `SUPIR_decode`, `SUPIR_encode`, `SUPIR_first_stage`, `SUPIR_model_loader_v2`, `SUPIR_sample`, `SetNode`.
- 明確模型檔名： `SUPIR/SUPIR-v0Q.ckpt`, `ayumu_I.safetensors`, `juggernautXL_v9Rdphoto2Lightning.safetensors`.
- 輸入： 1: LoadImage.
- 輸出： 32: PreviewImage, 12: PreviewImage, 59: PreviewImage.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-KJNodes / related version-specific extensions; ComfyUI-SUPIR. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-backgroundchanger-backgorund-brushinpaintaddsupir-72e1d2a0`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 017 — `BackgroundChanger/【ICLight】BlendAndDetailer.json`

Blend foreground/background references with segmentation and SDXL/IPAdapter, relight through IC-Light, then run a mask detailer. The graph includes Florence captions and preview sinks, not a saved-file contract.

- 容器／格式： `json_file` / `visual`. SHA-256: `e95371f2dcbad538362ec9969561f944e06db0100240df25c969a387f2aa8a47`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 37 top-level nodes; 37 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `AnyLinePreprocessor`, `ControlNetLoader`, `DownloadAndLoadFlorence2Model`, `Efficient Loader`, `Fast Groups Bypasser (rgthree)`, `Florence2Run`, `ICLightApplyMaskGrey`, `ICLightAppply`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImageBatch`, `InvertMask`, `JWImageResizeByFactor`, `JoinImageWithAlpha`, `KSamplerAdvanced //Inspire`, `LayerFilter: HDREffects`, `LayerMask: SegmentAnythingUltra`, `LayerUtility: GetImageSize`, `LayerUtility: ImageBlendAdvance`, `LoadImage`, `MaskDetailerPipe`, `PreviewImage`, `PrimitiveNode`, `SDXLPromptStyler`, `ScaledSoftControlNetWeights`, `SolidMask`, `ToBasicPipe`, `UNETLoader`, `VAEDecode`, `VAEEncodeArgMax`, `ZeST: Grayout Subject`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`, `iclight_sd15_fbc_unet_ldm.safetensors`, `mistoLine_rank256.safetensors`, `realisticVisionV60B1_v51VAE.safetensors`.
- 輸入： 153: LoadImage, 121: SDXLPromptStyler, 20: LoadImage.
- 輸出： 208: PreviewImage, 67: PreviewImage, 178: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Advanced-ControlNet; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-backgroundchanger-iclight-blendanddetailer-7ecfc9fb`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 018 — `BackgroundChanger/【SD15】ICLightv1.json`

Use subject and background images to generate a guided SDXL background and relight it with SD1.5 IC-Light. Upstream inputs 20/153 are distinct roles; outputs are previews, so native execution must explicitly save desired results.

- 容器／格式： `json_file` / `visual`. SHA-256: `d6e05a040f423d40cadf9293c00f5225d8a01525d89098d7fcb862febb9d8892`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 38 top-level nodes; 38 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ControlNetApply`, `ControlNetLoader`, `Efficient Loader`, `Fast Groups Bypasser (rgthree)`, `FloatStaticPrimitive`, `ICLightApplyMaskGrey`, `ICLightAppply`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImageBatch`, `ImageResize+`, `ImageUpscaleWithModel`, `JWImageResizeByFactor`, `JoinImageWithAlpha`, `KSamplerAdvanced //Inspire`, `LayerFilter: HDREffects`, `LayerMask: SegmentAnythingUltra`, `LayerUtility: GetImageSize`, `LayerUtility: ImageBlendAdvance V2`, `LineArtPreprocessor`, `LoadImage`, `PreviewImage`, `SDXLPromptStyler`, `SolidMask`, `UNETLoader`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncodeArgMax`, `WD14Tagger|pysssss`.
- 明確模型檔名： `4x-UltraSharp.pth`, `dreamshaperXL_lightningDPMSDE.safetensors`, `iclight_sd15_fbc_unet_ldm.safetensors`, `mistoLine_rank256.safetensors`, `realisticVisionV60B1_v51VAE.safetensors`.
- 輸入： 121: SDXLPromptStyler, 20: LoadImage, 153: LoadImage.
- 輸出： 67: PreviewImage, 165: PreviewImage, 178: PreviewImage, 185: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials; rgthree-comfy; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-backgroundchanger-sd15-iclightv1-73367f0d`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sd15-iclight-background.json`; SHA-256 `2446d8604d047b0fec1fec44562b4424c863e7c3c670f6b638b8971c4490e491`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 019 — `BackgroundChanger/【background】brushnet.json`

Replace the background of an uploaded subject through inverted BiRefNet masks and SD1.5 BrushNet. The source requires a specific BrushNet checkpoint and the historical BrushNet/Diffusers compatibility described by its README.

- 容器／格式： `json_file` / `visual`. SHA-256: `b3023c25cea871ca25b78ded36ce29e96536ef4050fbf26df267281736d6ed23`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BrushNetInpaint`, `BrushNetLoader`, `BrushNetPipeline`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `InvertMask`, `JWImageResizeByLongerSide`, `LayerMask: BiRefNetUltra`, `LoadImage`, `MaskToImage`, `PreviewImage`.
- 明確模型檔名： `ayumu_I.safetensors`, `brushnet/random_mask_brushnet_ckpt/diffusion_pytorch_model.safetensors`.
- 輸入： 1: LoadImage, 54: CLIPTextEncode, 53: CLIPTextEncode.
- 輸出： 32: PreviewImage, 12: PreviewImage.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sd15-background-brushnet`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sd15-background-brushnet.json`; SHA-256 `78ab6c6664aa8c0ce1facef77701cc827ed046527ce18cb5cfb35c7efb3b29cf`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 020 — `BackgroundChanger/【background】cosxl.json`

Apply instruction-guided CosXL background editing through a grouped DualCFG Pix2Pix sampler. The runtime depends on the legacy IP2PSampler definition, which the catalog does not expand.

- 容器／格式： `json_file` / `visual`. SHA-256: `27bd422d3d53571aeb285fd35585130d9f3125b92c3f601bb77629d6c5dcd879`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 8 top-level nodes; 16 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `DualCFGGuider`, `InstructPixToPixConditioning`, `JWImageResizeByLongerSide`, `KSamplerSelect`, `LoadImage`, `RandomNoise`, `Reroute`, `SamplerCustomAdvanced`, `SaveImage`, `VAEDecode`, `workflow/IP2PSampler`.
- 明確模型檔名： `CosXL/cosxl_edit.safetensors`.
- 輸入： 13: LoadImage, 7: CLIPTextEncode, 6: CLIPTextEncode.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-backgroundchanger-background-cosxl-74c7ab6e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: BasicScheduler, DualCFGGuider, InstructPixToPixConditioning, KSamplerSelect, RandomNoise, Reroute, SamplerCustomAdvanced No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 021 — `BackgroundChanger/【background】differdiffdream.json`

Replace the background using BiRefNet, inverted mask conditioning and DreamShaper XL Differential Diffusion. The supplied local copy retains the graph but requires a user image and installed custom nodes/models.

- 容器／格式： `json_file` / `visual`. SHA-256: `7612cf50262fa6e8b2370683603b96a3892f38d4888ec6b2825b7836aa3d00ce`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 11 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `DifferentialDiffusion`, `Efficient Loader`, `GetImageSize`, `InpaintModelConditioning`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: BiRefNetUltra`, `LoadImage`, `PreviewImage`, `SDXLPromptStyler`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`.
- 輸入： 20: LoadImage, 121: SDXLPromptStyler.
- 輸出： 243: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-background-differential`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-background-differential.json`; SHA-256 `2b042abd883cbd5ceacb3a5dc3e1ec4350192267c236e73ff3a635bfc16d4266`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 022 — `BackgroundChanger/【background】differdiffplay.json`

Replace the background with Playground v2.5, SegmentAnything masks and Differential Diffusion/continuous EDM sampling. The checkpoint/sampling family differs from the DreamShaper variant.

- 容器／格式： `json_file` / `visual`. SHA-256: `9a0405897a02f1be8767b5de191f3fe7be04506d2589444155462901945932ea`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 12 top-level nodes; 12 active expanded nodes. Top-level check: **pass**.
- 節點類型： `DifferentialDiffusion`, `Efficient Loader`, `GetImageSize`, `InpaintModelConditioning`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: SegmentAnythingUltra`, `LoadImage`, `ModelSamplingContinuousEDM`, `PreviewImage`, `SDXLPromptStyler`.
- 明確模型檔名： `playground-v2.5-1024px-aesthetic.fp16.safetensors`.
- 輸入： 20: LoadImage, 121: SDXLPromptStyler.
- 輸出： 243: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-backgroundchanger-background-differdiffplay-3777099c`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 023 — `Clothes/twoclothes.json`

Combine pose, face and clothing references with SD1.5 IPAdapter/FaceID and face detection/detailing. The second garment input is bypassed; the active inputs are 48 pose, 132 face and 173 garment.

- 容器／格式： `json_file` / `visual`. SHA-256: `6bbb48b799b7c2bd48b378a317f6ab27d848ce9dd08a76cdae84534a2cb14e8f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 34 top-level nodes; 31 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `DWPreprocessor`, `DiffControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `GetImageSize`, `IPAdapter`, `IPAdapterCombineEmbeds`, `IPAdapterEmbeds`, `IPAdapterEncoder`, `IPAdapterUnifiedLoader`, `IPAdapterUnifiedLoaderFaceID`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `PreviewImage`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`, `Text List`, `Text List to Text`, `UltralyticsDetectorProvider`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `majicmixRealistic_v7.safetensors`, `yolox_l.torchscript.pt`.
- 輸入： 132: LoadImage, 174: LoadImage [mode 4], 48: LoadImage, 173: LoadImage, 189: String Literal, 6: CLIPTextEncode, 7: CLIPTextEncode.
- 輸出： 130: PreviewImage, 30: PreviewImage, 186: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-clothes-twoclothes-e9e6badf`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sd15-pose-clothes-face-detail.json`; SHA-256 `f03afbba1d5dbb727bd4689ec6cec44a4784fcc1a19587438dd87a8781047fc2`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 024 — `Clothes/【Depthmap】Withgoodhand.json`

Extend clothing/identity synthesis with depth guidance, RAUNet, FreeU and PAG while preserving a face-reference branch. Multiple references and detector weights are needed; the graph has reciprocal-link inconsistencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `02727d15791c6b9217ae557224cca2b5b4c830fac1fe238aa872604715d9bb99`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 46 top-level nodes; 42 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `AIO_Preprocessor`, `Anything Everywhere3`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `DiffControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `FreeU_V2`, `GetImageSize+`, `IPAdapter`, `IPAdapterCombineEmbeds`, `IPAdapterEmbeds`, `IPAdapterEncoder`, `IPAdapterNoise`, `IPAdapterUnifiedLoader`, `IPAdapterUnifiedLoaderFaceID`, `JWImageResizeByLongerSide`, `KSampler //Inspire`, `LoadImage`, `PerturbedAttentionGuidance`, `PreviewImage`, `RAUNet`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`, `Text List`, `Text List to Text`, `UltralyticsDetectorProvider`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `bbox/face_yolov8m.pt`, `control_v11f1p_sd15_depth.pth`, `majicmixRealistic_v7.safetensors`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 174: LoadImage [mode 4], 209: CLIPTextEncode, 189: String Literal, 194: LoadImage, 132: LoadImage, 48: LoadImage, 173: LoadImage.
- 輸出： 186: ShowText|pysssss, 30: PreviewImage, 202: ShowText|pysssss, 130: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-clothes-depthmap-withgoodhand-88ead69f`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 025 — `Clothes/【openpose】forlikely.json`

Extend the clothing/face workflow with DWpose/OpenPose, RAUNet, FreeU and PAG. The graph differs from the depth variant in structural control, and has reciprocal-link inconsistencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `c1c4a7848c063ad7be245f4da79f5d546f754d4660866ff30029bbca55780235`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 46 top-level nodes; 42 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `Anything Everywhere3`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `DWPreprocessor`, `DiffControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `FreeU_V2`, `GetImageSize+`, `IPAdapter`, `IPAdapterCombineEmbeds`, `IPAdapterEmbeds`, `IPAdapterEncoder`, `IPAdapterNoise`, `IPAdapterUnifiedLoader`, `IPAdapterUnifiedLoaderFaceID`, `JWImageResizeByLongerSide`, `KSampler //Inspire`, `LoadImage`, `PerturbedAttentionGuidance`, `PreviewImage`, `RAUNet`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`, `Text List`, `Text List to Text`, `UltralyticsDetectorProvider`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `majicmixRealistic_v7.safetensors`, `yolox_l.torchscript.pt`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 174: LoadImage [mode 4], 132: LoadImage, 209: CLIPTextEncode, 48: LoadImage, 189: String Literal, 194: LoadImage, 173: LoadImage.
- 輸出： 186: ShowText|pysssss, 30: PreviewImage, 202: ShowText|pysssss, 130: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-clothes-openpose-forlikely-5fab3cb3`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 026 — `CodeExample/Youtube/YTUploader.json`

Upload an existing video to YouTube and add it to a playlist using embedded FL_CodeNode Python. This is an external publishing operation, not media generation; it requires Google OAuth credentials/token, YouTube Data API and a playlist, and must never be autoqueued as a harmless image graph.

- 容器／格式： `json_file` / `visual`. SHA-256: `c52d01a7bd8c5b0ba048118fca3f5d52026cf01889c5f105f23799f0e6fb014c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 5 top-level nodes; 5 active expanded nodes. Top-level check: **pass**.
- 節點類型： `FL_CodeNode`, `ShowText|pysssss`, `Text Multiline`.
- 明確模型檔名： none observed; inspect model selectors, implicit downloads and provider models.
- 輸入： 5: Text Multiline, 3: Text Multiline, 6: Text Multiline.
- 輸出： 2: ShowText|pysssss.
- 相依套件： ComfyUI_Fill-Nodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfyui-custom-scripts`, `comfyui_fill-nodes`, `pr-was-node-suite-comfyui-47064894`.
- 供應商／橋接需求： Custom code node; review embedded code and file/network side effects before execution.
- 公開目錄／工作流庫 ID： `dseditor-codeexample-youtube-ytuploader-5cd47a91`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed. Embedded code uploads videos and modifies YouTube playlists; it requires explicit publish authorization and is not an image generation runner.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 027 — `Couple/3girls.json`

Generate a three-person composition with ordered person masks, regional IPAdapter references, SDXL and line control. Inputs 259, 77/78/422 and 183 serve layout, subjects and background; verify mask order and implicit CLIP/VAE routes.

- 容器／格式： `json_file` / `visual`. SHA-256: `4898682103b146129c449f3f6b6427b892dc931cba93725bc136665b77b02115`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 46 top-level nodes; 46 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `AnyLinePreprocessor`, `Anything Everywhere3`, `CLIPTextEncode`, `ConditioningCombineMultiple+`, `ControlNetLoader`, `Efficient Loader`, `Fast Groups Bypasser (rgthree)`, `GetImageSize+`, `IPAdapterCombineParams`, `IPAdapterFromParams`, `IPAdapterRegionalConditioning`, `IPAdapterUnifiedLoader`, `InvertMask`, `JWImageResizeByLongerSide`, `KSamplerAdvanced //Inspire`, `LayerMask: YoloV8Detect`, `LoadImage`, `MaskPreview+`, `PreviewImage`, `ScaledSoftControlNetWeights`, `SelectNthMask //Inspire`, `StringFunction|pysssss`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `mistoLine_rank256.safetensors`, `person_yolov8m-seg.pt`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： 259: LoadImage, 388: CLIPTextEncode, 394: CLIPTextEncode, 419: CLIPTextEncode, 393: CLIPTextEncode, 77: LoadImage, 183: LoadImage, 422: LoadImage, 78: LoadImage.
- 輸出： 412: MaskPreview+, 416: MaskPreview+, 415: MaskPreview+, 431: PreviewImage, 417: MaskPreview+.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Advanced-ControlNet; ComfyUI_essentials; rgthree-comfy; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-couple-3girls-dfdd6bc8`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-regional-three-subjects.json`; SHA-256 `141c55a74286fc0e98da4714e6a185862126c6c6dd5f31a841d956048b555e56`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 028 — `Couple/couplefaceid.json`

Create a two-person image using region masks, OpenPose, FaceID and IPAdapter. InsightFace and FaceID adapters/LoRA are required; legacy groups contain control and per-person conditioning.

- 容器／格式： `json_file` / `visual`. SHA-256: `5b6f46444c015346630d4b5761451c5f9915610492e059dfa6bce5a71b50fcbb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 45 top-level nodes; 48 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionLoader`, `ColorToMask`, `ConditioningCombine`, `ConditioningSetMask`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `Efficient Loader`, `EmptyImage`, `GetImageSize`, `IPAdapterApply`, `IPAdapterApplyFaceID`, `IPAdapterModelLoader`, `ImageCompositeMasked`, `ImpactSEGSOrderedFilter`, `InsightFaceLoader`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `MaskToImage`, `PrepImageForInsightFace`, `PreviewImage`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `UltralyticsDetectorProvider`, `workflow/controlnet`, `workflow/紅色區域條件`, `workflow/綠色區域條件`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `ghostmix_v20Bakedvae.safetensors`, `ip-adapter-faceid-plusv2_sd15.bin`, `ip-adapter-faceid-plusv2_sd15_lora.safetensors`, `ip-adapter-plus_sd15.safetensors`, `segm/yolov8m-seg.pt`, `yolox_l.onnx`.
- 輸入： 183: LoadImage, 77: LoadImage, 78: LoadImage, 259: LoadImage, root/311: CLIPTextEncode, root/305: CLIPTextEncode, 292: CLIPTextEncode.
- 輸出： 309: PreviewImage, 258: PreviewImage, 250: PreviewImage, 248: PreviewImage, 272: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-couple-couplefaceid-d5c39b63`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: ConditioningSetMask, ControlNetApply, ControlNetLoader No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 029 — `CoupleFull.json`

Create a two-person composition with independent references, face/person segmentation and differential inpainting. It contains legacy regional/control groups and separate clothing/identity branches; these are not flattened by the public importer.

- 容器／格式： `json_file` / `visual`. SHA-256: `cf8710b01fe322e3022539594084bb6333dc0a206bf1b5e40634af09715124cd`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 48 top-level nodes; 51 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BboxDetectorSEGS`, `CLIPTextEncode`, `ConditioningCombine`, `ConditioningSetMask`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `DifferentialDiffusion`, `Efficient Loader`, `GetImageSize`, `IPAdapter`, `IPAdapterUnifiedLoader`, `IPAdapterUnifiedLoaderFaceID`, `ImpactSEGSOrderedFilter`, `InpaintModelConditioning`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: PersonMaskUltra`, `LoadImage`, `MaskToImage`, `Note`, `PreviewImage`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `UltralyticsDetectorProvider`, `workflow/controlnet`, `workflow/紅色區域條件`, `workflow/綠色區域條件`.
- 明確模型檔名： `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `henmixReal_v5c.safetensors`, `segm/yolov8m-seg.pt`, `yolox_l.onnx`.
- 輸入： 259: LoadImage, 183: LoadImage, 338: LoadImage, 339: LoadImage, 292: CLIPTextEncode, root/311: CLIPTextEncode, root/305: CLIPTextEncode, 78: LoadImage, 77: LoadImage.
- 輸出： 309: PreviewImage, 321: PreviewImage, 368: PreviewImage, 248: PreviewImage, 250: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-couplefull-43853ca3`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: ConditioningSetMask, ControlNetApply, ControlNetLoader No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 030 — `Design/【Material】ChangeMaterial.json`

Transfer reference material appearance into selected regions using ZeST grayout, IPAdapter and per-region detailing/paste. Several reference images are present; exact material preservation is not guaranteed by the graph.

- 容器／格式： `json_file` / `visual`. SHA-256: `707a70d988edc086d6895f6186129cdfef89af98d757fc9c316957dd08e161b0`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 29 top-level nodes; 29 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Anything Everywhere3`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `DetailerForEachPipe`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImpactImageBatchToImageList`, `ImpactMakeImageBatch`, `JWImageResizeByLongerSide`, `LayerMask: SegmentAnythingUltra`, `LoadImage`, `MaskToSEGS`, `PreviewImage`, `SEGSPaste`, `ToBasicPipe`, `ZeST: Grayout Subject`.
- 明確模型檔名： `aiartistHyperrayXLXL_hypersdxl.safetensors`.
- 輸入： 3: LoadImage, 18: LoadImage, 69: CLIPTextEncode, 70: CLIPTextEncode, 81: CLIPTextEncode, 78: LoadImage, 79: LoadImage.
- 輸出： 61: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-material-transfer`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/material-transfer.json`; SHA-256 `69ca967200b9df6bd1eceb60330d64770666913ec277789207477a094a3c5bb8`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 031 — `Experiment/ClothesChangerV2.json`

Segment garments with DeepFashion YOLO, cut/paste them onto another image and inpaint around them under pose control. The upstream README explicitly says fit quality is poor; this is an experiment, not a verified clothing-transfer feature.

- 容器／格式： `json_file` / `visual`. SHA-256: `412f91ddb331b82e903fc35ba95867a72f1f11aeb4331647e42ca4605c20d68c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 38 top-level nodes; 38 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ControlNetApply`, `ControlNetLoader`, `Cut By Mask`, `DWPreprocessor`, `Efficient Loader`, `EmptyImage`, `GetImageSize`, `InvertMask`, `KSampler`, `LoadImage`, `MaskToImage`, `Mix Images By Mask`, `Paste By Mask`, `PreviewImage`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `SplitImageWithAlpha`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncodeForInpaint`, `WD14Tagger|pysssss`.
- 明確模型檔名： `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `majicmixRealistic_v7-inpainting.safetensors`, `segm/deepfashion2_yolov8s-seg.pt`, `yolox_l.onnx`.
- 輸入： 21: LoadImage, 27: LoadImage, 86: CLIPTextEncode.
- 輸出： 69: PreviewImage, 20: PreviewImage, 56: PreviewImage, 46: PreviewImage, 103: PreviewImage, 110: PreviewImage, 80: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-experiment-clotheschangerv2-62f91006`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 032 — `Experiment/StyleAsligned+IPadapter.json`

Compare/reference-match styles using StyleAligned latent/reference samplers, SD1.5 and a grouped IPAdapter branch. The README limits its positive observations mainly to drawn/model subjects, and the group requires expansion.

- 容器／格式： `json_file` / `visual`. SHA-256: `49f52a6ffcb2f244810101a7c7fdebf400f60efdc57a906eeec7c2bce8a12959`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPTextEncode`, `CLIPVisionLoader`, `Efficient Loader`, `IPAdapterApply`, `IPAdapterModelLoader`, `JWImageResizeByLongerSide`, `KSamplerSelect`, `LoadImage`, `PrepImageForClipVision`, `PreviewImage`, `ShowText|pysssss`, `StyleAlignedReferenceSampler`, `StyleAlignedSampleReferenceLatents`, `Text to Conditioning`, `VAEDecode`, `VAEEncode`, `WD14Tagger|pysssss`, `workflow/iPadapter`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `ghostmix_v20Bakedvae.safetensors`, `ip-adapter-plus_sd15.safetensors`.
- 輸入： 82: LoadImage, 107: CLIPTextEncode.
- 輸出： 77: ShowText|pysssss, 71: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-experiment-styleasligned-ipadapter-e9e9c074`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: CLIPVisionLoader, IPAdapterApply, IPAdapterModelLoader No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 033 — `Experiment/config.json`

ComfyUI UI/application configuration object with 24 settings, not a workflow; excluded from graph counts and runtime integration.

- 容器／格式： `json_file` / `not_workflow`. SHA-256: `cf28d1976046bfdfb96c659156046206ce0be6c7a0c3021c89c775717c4ad84f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 狀態： Not executable workflow JSON.

### 034 — `FLUXKontextBasicLLM(Rendering).json`

Render an input image/line drawing through FLUX Kontext, with Gemini-assisted edit prompts and an output stitch for comparison. The source is byte-identical to its nested Kontext copy; the public local copy still needs a LoadImageOutput choice and explicit Gemini configuration.

- 容器／格式： `json_file` / `visual`. SHA-256: `b6d5984b46a1d3bdf5fd0eb2a192ab475fa006d416566c0a89e36b7778b9f722`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ConditioningZeroOut`, `DualCLIPLoader`, `FluxGuidance`, `FluxKontextImageScale`, `GeminiAPI`, `ImageStitch`, `KSampler`, `LoadImageOutput`, `PreviewImage`, `ReferenceLatent`, `SaveImage`, `ShowText|pysssss`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `ae.safetensors`, `clip_l.safetensors`, `flux1-dev-kontext_fp8_scaled.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 6: CLIPTextEncode, 142: LoadImageOutput.
- 輸出： 173: PreviewImage, 195: ShowText|pysssss, 136: SaveImage.
- 相依套件： ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `al-swaiti/ComfyUI-OllamaGemini`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-ollamagemini`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-fluxkontextbasicllm-rendering-406bacb1`; catalog distribution: links-only.
- 公開副本： `workflows/FLUXKontextBasicLLM(Rendering).json`; SHA-256 `b7d72769582658dcee286d7ccc18d5d950018cb53fdfc2a0011e0f22bd5163cd`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.
- 完全相同位元組的重複來源： `Flux/Kontext/FLUXKontextBasicLLM(Rendering).json`.

### 035 — `Flux/ACELora/【Portrait】FaceChangerBasic.json`

Replace/refine a portrait region using Flux Fill, ACE portrait LoRA, Redux reference conditioning and person-mask expansion. The saved graph has dangling nodes; it is not ready to promote as a working portrait feature.

- 容器／格式： `json_file` / `visual`. SHA-256: `0bf008662d71d6d32f80e1e11c91f677d1ba304cdce1630b3102ecc29bcdfa0a`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 29 top-level nodes; 25 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `AddMaskForICLora`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `GetImageSize+`, `INPAINT_ExpandMask`, `ImageCrop+`, `ImageResize+`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LoadImage`, `LoraLoaderModelOnly`, `PreviewImage`, `SimpleComparison+`, `SimpleCondition+`, `StyleModelApplySimple`, `StyleModelLoader`, `TeaCacheForImgGen`, `ToBinaryMask`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 188: LoadImage, 149: LoadImage, 148: CLIPTextEncode.
- 輸出： 193: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-acelora-portrait-facechangerbasic-d2f13e86`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 036 — `Flux/ACELora/【Portrait】PortraitLoraWithRedux.json`

Use ACE portrait LoRA plus Redux, Florence captions, face detection and crop/mask handling to guide Flux Fill. Dangling references must be repaired; weights include explicit face YOLO and portrait LoRA.

- 容器／格式： `json_file` / `visual`. SHA-256: `5f39ca70046cfeeba66782e286e95f932c5d026a848be15baaacf1723c583fa7`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 34 top-level nodes; 34 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `AddMaskForICLora`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoaderGGUF`, `EmptyImage`, `Florence2Run`, `FluxGuidance`, `GetImageSize+`, `ImageCrop+`, `ImageResize+`, `ImageToMask`, `InpaintModelConditioning`, `KSampler`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask V2`, `LoadImage`, `LoraLoaderModelOnly`, `PreviewImage`, `SaveImage`, `SimpleComparison+`, `SimpleCondition+`, `StringFunction|pysssss`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `Flux/ali-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `face_yolov8m.pt`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 149: LoadImage, 212: LoadImage, 148: CLIPTextEncode.
- 輸出： 202: PreviewImage, 204: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-acelora-portrait-portraitlorawithredux-ee80cbfa`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 037 — `Flux/ACELora/【Portrait】ReduxWithHires.json`

Perform portrait Redux inpainting followed by a Flux Dev high-resolution pass with cached sampling, face crop restoration and upscaling. Dangling links remain and both Fill/Dev stacks are required.

- 容器／格式： `json_file` / `visual`. SHA-256: `229621491a17f3df0ee1910eb30a880315db5cfee88ef81e73ca13445d2bbda5`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 37 top-level nodes; 33 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `ApplyFBCacheOnModel`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoaderGGUF`, `EmptyImage`, `Florence2Run`, `FluxGuidance`, `FluxSamplerParams+`, `ImageToMask`, `ImageUpscaleWithModel`, `InpaintModelConditioning`, `JWImageResizeByFactor`, `KSampler`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LoadImage`, `LorasForFluxParams+`, `Power Lora Loader (rgthree)`, `SaveImage`, `StringFunction|pysssss`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/flux1-dev-Q8_0.gguf`, `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `Flux/FLUXHand.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `face_yolov8m.pt`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 148: CLIPTextEncode, 149: LoadImage, 251: CLIPTextEncode, 204: LoadImage.
- 輸出： 269: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; rgthree-comfy; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-acelora-portrait-reduxwithhires-7c5748b0`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 038 — `Flux/FluxDev.json`

Generate Flux Dev images via a legacy grouped sampler, then upscale/refine with SDXL and UltraSharp. The group contains core UNET/encoders/VAE/sampling nodes missing from the shallow catalog.

- 容器／格式： `json_file` / `visual`. SHA-256: `68400bf9c8f24f3a1ea07b1c2c60bff6e80b73905767e905122cb051257aca9c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 8 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `DualCLIPLoader`, `Efficient Loader`, `EmptyLatentImage`, `FL_SDUltimate_Slices`, `KSamplerSelect`, `RandomNoise`, `SDXLPromptStyler`, `SD_4XUpscale_Conditioning`, `SamplerCustomAdvanced`, `SaveImage`, `UNETLoader`, `UltimateSDUpscale`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`, `workflow/flux`.
- 明確模型檔名： `4x-UltraSharp.pth`, `FLUX1/ae.sft`, `Flux/clip_l.safetensors`, `Flux/t5xxl_fp8_e4m3fn.safetensors`, `SD3/clip_l.safetensors`, `SD3/t5xxl_fp8_e4m3fn.safetensors`, `flux1-dev-fp8.safetensors`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： root/29: CLIPTextEncode, 26: SDXLPromptStyler.
- 輸出： 9: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_Fill-Nodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxdev-7895b096`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: BasicGuider, BasicScheduler, CLIPTextEncode, DualCLIPLoader, EmptyLatentImage, KSamplerSelect, RandomNoise, SamplerCustomAdvanced, UNETLoader, VAEDecode, VAELoader No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 039 — `Flux/FluxSchnell.json`

Generate Flux Schnell images, then slice/upscale and refine with SDXL Lightning. The Flux and SDXL checkpoints, separate text encoders/AE and UltraSharp all need installation.

- 容器／格式： `json_file` / `visual`. SHA-256: `f0d6719f6930564169927772b1ca1b6604e0a411db0570b4c6c4c8f393bed989`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 18 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `DualCLIPLoader`, `Efficient Loader`, `FL_SDUltimate_Slices`, `KSamplerSelect`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SDXLPromptStyler`, `SD_4XUpscale_Conditioning`, `SamplerCustomAdvanced`, `SaveImage`, `UNETLoader`, `UltimateSDUpscale`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `4x-UltraSharp.pth`, `FLUX1/ae.sft`, `FLUX1/flux1-schnell.sft`, `SD3/clip_l.safetensors`, `SD3/t5xxl_fp8_e4m3fn.safetensors`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： 6: CLIPTextEncode, 26: SDXLPromptStyler.
- 輸出： 9: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_essentials; ComfyUI_Fill-Nodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-schnell-upscale`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-schnell-upscale.json`; SHA-256 `33e792ed8f3fa2d737e5be516d136b7c39d6285b7b4737e7e887f4bcb3383c27`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 040 — `Flux/FluxTools/【FLUXTool】BGChanger.json`

Replace backgrounds using Flux Fill plus Redux, Florence captions and background removal, then apply a Flux Dev/upscale pass. Two image inputs and both Fill/Dev models are required.

- 容器／格式： `json_file` / `visual`. SHA-256: `e0ef84ef56ec26eb2696c9af794cba8a3a78f96f7072e4db24c98aa2f311b2db`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 39 top-level nodes; 39 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `Florence2ModelLoader`, `Florence2Run`, `FluxGuidance`, `FluxSamplerParams+`, `ImageRemoveBackground+`, `ImageResize+`, `ImageResizeKJ`, `ImageUpscaleWithModel`, `InpaintModelConditioning`, `InvertMask`, `KSampler`, `LayerUtility: ImageBlendAdvance V2`, `LayerUtility: PurgeVRAM`, `LoadImage`, `LoraLoaderModelOnly`, `LorasForFluxParams+`, `PreviewImage`, `RemBGSession+`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/flux1-dev-Q8_0.gguf`, `FLUX1/flux1-fill-dev-Q8_0.gguf`, `Flux/ali-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 83: LoadImage, 162: LoadImage, 169: CLIPTextEncode, 188: CLIPTextEncode.
- 輸出： 193: PreviewImage, 194: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxtools-fluxtool-bgchanger-8b1f6fbb`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 041 — `Flux/FluxTools/【FluxTools】CannyDepthSwitch.json`

Choose line or depth guidance with Flux canny/depth LoRAs and an accelerated Flux sampler. The actual selected base is RealFlux_1.0b_Dev-Q8_0.gguf; switching guidance also changes its compatible control LoRA.

- 容器／格式： `json_file` / `visual`. SHA-256: `54f54bdfa7bdf625c789dace55b542d4d457ab4c2dba3acf99ad8a340a417bbc`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AnyLineArtPreprocessor_aux`, `CLIPTextEncode`, `ConditioningZeroOut`, `DepthAnythingV2Preprocessor`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `ImageResize+`, `InstructPixToPixConditioning`, `KSampler`, `LoadImage`, `LoraLoaderModelOnly`, `ModelSamplingFlux`, `PixelPerfectResolution`, `PreviewImage`, `SaveImage`, `SimpleCondition+`, `SimpleMathBoolean+`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/RealFlux_1.0b_Dev-Q8_0.gguf`, `Flux/ali-flux-8step.safetensors`, `Flux/flux1-canny-dev-lora.safetensors`, `Flux/flux1-depth-dev-lora.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `depth_anything_v2_vitl.pth`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 23: CLIPTextEncode, 17: LoadImage.
- 輸出： 42: PreviewImage, 56: PreviewImage, 66: SaveImage.
- 相依套件： ComfyUI-GGUF; comfyui_controlnet_aux; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-canny-depth`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-canny-depth.json`; SHA-256 `6535597580a6ca164a8907db9d26f4a06be93e063c4aae49973d62c7f46521c4`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 042 — `Flux/FluxTools/【FluxTools】SimpleTryon.json`

Use person/clothing images with Flux Fill, expanded person masks and Redux style/reference conditioning for simple try-on. This variant lacks the LoRA/upscale additions found in the separate frontend pair.

- 容器／格式： `json_file` / `visual`. SHA-256: `68f4b846df2421793a6139f827e179b97961a70fa79f8584bda57394f57bc566`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AddMaskForICLora`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `INPAINT_ExpandMask`, `ImageCrop+`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LoadImage`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `ae.safetensors`, `clip_l.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 59: LoadImage, 50: LoadImage, 23: CLIPTextEncode.
- 輸出： 143: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxtools-fluxtools-simpletryon-49506e26`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 043 — `Flux/FluxTools/【FluxTool】InpaintAndDetailer.json`

Inpaint a masked image with Flux Fill, detect a person region, crop/resize and refine with Flux Dev, then restore the crop. The detector is person segmentation, not a face-specific detector.

- 容器／格式： `json_file` / `visual`. SHA-256: `b5ee9fe28cf678a697b0ebf79e2feb7941f1f07f2619ad40522069ae669a1529`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 29 top-level nodes; 29 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `ImageResize+`, `ImageUpscaleWithModel`, `InpaintModelConditioning`, `KSampler`, `LayerMask: MaskEdgeUltraDetail`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask V2`, `LayerUtility: ImageScaleRestore V2`, `LayerUtility: RestoreCropBox`, `LoadImage`, `LoraLoaderModelOnly`, `PreviewImage`, `SaveImage`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/flux1-dev-Q8_0.gguf`, `FLUX1/flux1-fill-dev-Q8_0.gguf`, `Flux/ali-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `person_yolov8m-seg.pt`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 7: CLIPTextEncode, 23: CLIPTextEncode, 17: LoadImage.
- 輸出： 9: SaveImage, 62: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxtools-fluxtool-inpaintanddetailer-f237fa28`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-fill-subject-detail.json`; SHA-256 `4621997f97717f40f304e17847266f4e09b6915740f432cba4df496afb6c5e3d`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 044 — `Flux/FluxTools/【FluxTool】Outpaint.json`

Expand an image canvas and fill new regions with Flux Fill GGUF plus Differential Diffusion. Requires padding/mask review and the specified T5, improved CLIP-L and AE files.

- 容器／格式： `json_file` / `visual`. SHA-256: `06b01c03dfb875485e58c3c60b4ca6d5421f791c13af4fb03201492f6ad60bc6`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `ImagePadForOutpaint`, `InpaintModelConditioning`, `KSampler`, `LoadImage`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 7: CLIPTextEncode, 23: CLIPTextEncode, 17: LoadImage.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI-GGUF. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fill-outpaint`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-fill-outpaint.json`; SHA-256 `ed615320077325ef8eb36b30bc78c2de125fdbfc874462b4e118a0e3e99bec64`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 045 — `Flux/FluxTools/【FluxTool】ReduxAdvanced.json`

Generate from image reference conditioning via ReduxAdvanced and DetailDaemon sampling, then compare/annotate outputs. Redux/SigLIP weights and custom sampler nodes are needed in addition to Flux.

- 容器／格式： `json_file` / `visual`. SHA-256: `1b9906dfe5036103b754a2a8a5e9fadcbc0bf4099ed6f456709e09cf31de7875`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 25 top-level nodes; 21 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `CLIPVisionLoader`, `DetailDaemonSamplerNode`, `DualCLIPLoaderGGUF`, `EmptySD3LatentImage`, `FL_ImageNotes`, `FluxGuidance`, `ImageConcatMulti`, `ImageResize+`, `KSamplerSelect`, `LayerUtility: SaveImagePlus`, `LoadImage`, `ModelSamplingFlux`, `PreviewImage`, `RandomNoise`, `ReduxAdvanced`, `SamplerCustomAdvanced`, `SaveImage`, `StyleModelLoader`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-dev-Q8_0.gguf`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 40: LoadImage, 6: CLIPTextEncode.
- 輸出： 60: LayerUtility: SaveImagePlus [mode 4], 62: PreviewImage, 69: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI_Fill-Nodes; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-redux-advanced`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-redux-advanced.json`; SHA-256 `49aa8f5341686b9191a93c47bbacc0383eda1bfa3ace8b5d1836edb6467ac0d0`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 046 — `Flux/FluxTools/【FluxTool】ReduxStoryBoard.json`

Generate a prompt-list storyboard with Redux reference conditioning, Flux sampler-parameter helpers and image-batch concatenation. Reference/latent routing and list iteration require inspection; output is a storyboard image.

- 容器／格式： `json_file` / `visual`. SHA-256: `fa7ddc40e59fb7ea3b04258881819b1ef336545387acdd97e02c2d453772713a`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 21 top-level nodes; 21 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionLoader`, `CR Prompt List`, `DualCLIPLoaderGGUF`, `FluxSamplerParams+`, `ImageConcatFromBatch`, `ImageListToImageBatch`, `LoraLoaderModelOnly`, `LorasForFluxParams+`, `Note`, `PrimitiveNode`, `ReduxAdvanced`, `SDXLEmptyLatentSizePicker+`, `SaveImage`, `StyleModelLoader`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-dev-Q8_0.gguf`, `Flux/ali-flux-8step.safetensors`, `IClora/portrait-photography.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 288: CLIPTextEncode, 274: CLIPTextEncode, 287: CR Prompt List.
- 輸出： 293: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials; ComfyUI_Comfyroll_CustomNodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxtools-fluxtool-reduxstoryboard-7c28324e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 047 — `Flux/FluxTools/【FluxTool】TryOn.json`

Try on clothing using Flux Fill, CatVTON LoRA, Redux, Florence and expanded masks, then refine/upscale with Flux Dev. Requires person/garment inputs and several distinct model families/custom-node packages.

- 容器／格式： `json_file` / `visual`. SHA-256: `8e9ae4577c4ab4c94dea2f04d0e4dea0b6345d95c24b211817043980de16b310`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 43 top-level nodes; 38 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AddMaskForICLora`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoaderGGUF`, `FL_ImageNotes`, `Florence2Run`, `FluxGuidance`, `FluxSamplerParams+`, `INPAINT_ExpandMask`, `ImageConcatMulti`, `ImageCrop+`, `ImageResize+`, `ImageUpscaleWithModel`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LayerUtility: ImageBlendAdvance V2`, `LoadImage`, `LoraLoaderModelOnly`, `LorasForFluxParams+`, `MaskPreview+`, `Note`, `PreviewImage`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/flux1-dev-Q8_0.gguf`, `FLUX1/flux1-fill-dev-Q8_0.gguf`, `Flux/Catviton.safetensors`, `Flux/ali-flux-8step.safetensors`, `SD3/clip_l.safetensors`, `ae.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 23: CLIPTextEncode, 98: CLIPTextEncode, 59: LoadImage, 50: LoadImage.
- 輸出： 70: PreviewImage, 110: PreviewImage [mode 4], 83: MaskPreview+, 114: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI_Fill-Nodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxtools-fluxtool-tryon-16d985bf`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 048 — `Flux/FluxTools/【Fluxtool】ReduxInpainting.json`

Inpaint an image mask while applying Redux from a separate reference image. Flux Fill/Redux/SigLIP and acceleration LoRA are explicit dependencies; source and reference are separate inputs.

- 容器／格式： `json_file` / `visual`. SHA-256: `7091bb8872f210f12b8753d3de7eb14603408f298a376656b9e6cacde6cac631`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `GetImageSize+`, `InpaintModelConditioning`, `KSampler`, `LoadImage`, `LoraLoaderModelOnly`, `ModelSamplingFlux`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `Flux/ali-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 40: LoadImage, 62: CLIPTextEncode, 6: CLIPTextEncode, 63: LoadImage.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-redux-inpaint`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-redux-inpaint.json`; SHA-256 `fa1c125ffb0bdb184e796168dcb545e93629755f8be8b1dbd34dfba7ff08d8b3`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 049 — `Flux/FluxWallpaper.json`

Generate a wallpaper with Flux Schnell, optionally assisted by Gemini or Ollama, then tile-upscale. Provider selection and prompt routing require explicit configuration; source remains a reference in the public app.

- 容器／格式： `json_file` / `visual`. SHA-256: `668b4d0f0e2f4bab8d8f1d48cfaf88dbd443ea918ca6df0ecdeeb4c379cc89c1`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 16 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConcatText_Zho`, `EmptyLatentImage`, `FL_SDUltimate_Slices`, `FluxGuidance`, `Gemini_API_Zho`, `ImpactSwitch`, `KSampler (Efficient)`, `Note`, `OllamaGenerate`, `SDXLPromptStyler`, `SaveImage`, `String Literal`, `UltimateSDUpscale`, `UpscaleModelLoader`.
- 明確模型檔名： `4x-UltraSharp.pth`, `Flux/flux1-schnell-fp8.safetensors`.
- 輸入： 26: SDXLPromptStyler, 47: CLIPTextEncode, 48: CLIPTextEncode, 75: String Literal.
- 輸出： 69: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_Fill-Nodes; Ollama custom nodes plus explicitly configured Ollama service; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly; Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-flux-fluxwallpaper-f4f1ad96`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 050 — `Flux/Fluxdevfp8Checkpoint.json`

Generate with a Flux Dev FP8 checkpoint and refine/upscale using RealVisXL Lightning and UltraSharp. AlignYourSteps and UltimateSDUpscaleCustomSample are required custom sampling components.

- 容器／格式： `json_file` / `visual`. SHA-256: `a02dea9e17def651d5347f30981d337025c480f864bcfddf40c20de7d96d3fdb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 15 top-level nodes; 15 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AlignYourStepsScheduler`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `Efficient Loader`, `EmptyLatentImage`, `FL_SDUltimate_Slices`, `FluxGuidance`, `KSampler (Efficient)`, `KSamplerSelect`, `SDXLPromptStyler`, `SD_4XUpscale_Conditioning`, `SaveImage`, `UltimateSDUpscaleCustomSample`, `UpscaleModelLoader`.
- 明確模型檔名： `4x-UltraSharp.pth`, `Flux/flux1-dev-fp8.safetensors`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： 47: CLIPTextEncode, 48: CLIPTextEncode, 26: SDXLPromptStyler.
- 輸出： 9: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_Fill-Nodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-dev-fp8-upscale`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/flux-dev-fp8-upscale.json`; SHA-256 `466094a190e68f1f4de62ecdf489db1c2cb895325d37e1ca22d108fd3ed6021d`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 051 — `Flux/FrontEnd/TryOnsimpleAPI.json`

API-format version of the local two-upload Flux Fill try-on frontend. Person and clothing filenames are fixed by gradiotw.py; it saves an image but the public app has no runner mapping and the visual-only importer cannot sanitize this format.

- 容器／格式： `json_file` / `api`. SHA-256: `144124eed40ea86bf75ed1c539143986806ff0f965dbc0d942574043c08f8ce9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 25 top-level nodes; 25 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AddMaskForICLora`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `INPAINT_ExpandMask`, `ImageCrop+`, `ImageResize+`, `ImageUpscaleWithModel`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LoadImage`, `LoraLoaderModelOnly`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `Catviton.safetensors`, `ae.safetensors`, `ali-flux-8step.safetensors`, `clip_l.safetensors`, `flux1-fill-dev-Q8_0.gguf`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 23: CLIPTextEncode, 50: LoadImage, 59: LoadImage.
- 輸出： 143: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-frontend-tryonsimpleapi-d596606f`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Source API graph is not handled by the visual-only community sanitizer/importer; upstream Gradio runner is a separate implementation.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 052 — `Flux/FrontEnd/【FluxTool】TryOnsimple.json`

Editable visual counterpart of the try-on API graph. It combines person and garment images with Flux Fill, CatVTON and acceleration LoRAs, Redux conditioning and Nomos upscaling. The separate upstream Gradio frontend is neither bundled nor invoked by Web_Comfyui.

- 容器／格式： `json_file` / `visual`. SHA-256: `5eb521f965c0a6566818bb5d7a076dd98f1d10af1cf164973dc8558c558d9588`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 25 top-level nodes; 25 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AddMaskForICLora`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `INPAINT_ExpandMask`, `ImageCrop+`, `ImageResize+`, `ImageUpscaleWithModel`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LoadImage`, `LoraLoaderModelOnly`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `Catviton.safetensors`, `ae.safetensors`, `ali-flux-8step.safetensors`, `clip_l.safetensors`, `flux1-fill-dev-Q8_0.gguf`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 23: CLIPTextEncode, 59: LoadImage, 50: LoadImage.
- 輸出： 143: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-frontend-fluxtool-tryonsimple-505b37cd`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 053 — `Flux/Kontext/FLUXKontextBasicLLM(Rendering).json`

Byte-identical nested copy of the root FLUX Kontext rendering graph, with Gemini prompts and stitched comparison output. The public catalog treats this path separately as a reference although the identical root source is locally copied.

- 容器／格式： `json_file` / `visual`. SHA-256: `b6d5984b46a1d3bdf5fd0eb2a192ab475fa006d416566c0a89e36b7778b9f722`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ConditioningZeroOut`, `DualCLIPLoader`, `FluxGuidance`, `FluxKontextImageScale`, `GeminiAPI`, `ImageStitch`, `KSampler`, `LoadImageOutput`, `PreviewImage`, `ReferenceLatent`, `SaveImage`, `ShowText|pysssss`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `ae.safetensors`, `clip_l.safetensors`, `flux1-dev-kontext_fp8_scaled.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 6: CLIPTextEncode, 142: LoadImageOutput.
- 輸出： 173: PreviewImage, 195: ShowText|pysssss, 136: SaveImage.
- 相依套件： ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `al-swaiti/ComfyUI-OllamaGemini`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-ollamagemini`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-flux-kontext-fluxkontextbasicllm-rendering-960a9f98`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.
- 完全相同位元組的重複來源： `FLUXKontextBasicLLM(Rendering).json`.

### 054 — `Flux/Kontext/FLUXKontextBasicLLM.json`

FLUX Kontext image editing with Gemini prompt preparation and stitched comparison. A second image branch is bypassed; inspect active input count before claiming multiple-reference editing.

- 容器／格式： `json_file` / `visual`. SHA-256: `af0bf8a11f42bc17b4abbcfe6ae7ff0eaeec57beba05cee27bb917c2cfdd794a`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 19 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ConditioningZeroOut`, `DualCLIPLoader`, `FluxGuidance`, `FluxKontextImageScale`, `GeminiAPI`, `ImageStitch`, `KSampler`, `LoadImageOutput`, `PreviewImage`, `ReferenceLatent`, `SaveImage`, `ShowText|pysssss`, `StringFunction|pysssss`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `ae.safetensors`, `clip_l.safetensors`, `flux1-dev-kontext_fp8_scaled.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 147: LoadImageOutput [mode 4], 6: CLIPTextEncode, 142: LoadImageOutput.
- 輸出： 195: ShowText|pysssss, 136: SaveImage, 173: PreviewImage.
- 相依套件： ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-custom-scripts`, `comfyui-ollamagemini`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-flux-kontext-fluxkontextbasicllm-8078776f`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 055 — `Flux/Kontext/FLUXKontextOutpaint.json`

Expand a canvas and edit it through FLUX Kontext reference-latent conditioning with Gemini-assisted instructions. This is a Kontext edit path rather than the separate Flux Fill outpaint graph.

- 容器／格式： `json_file` / `visual`. SHA-256: `d0fd872eab3b175ca102bc8523517453e9ea4ded177f3bc1deb56884e8e1f0dc`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 16 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ConditioningZeroOut`, `DualCLIPLoader`, `FluxGuidance`, `FluxKontextImageScale`, `GeminiAPI`, `ImagePadForOutpaint`, `KSampler`, `LoadImage`, `ReferenceLatent`, `SaveImage`, `ShowText|pysssss`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `ae.safetensors`, `clip_l.safetensors`, `flux1-dev-kontext_fp8_scaled.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 6: CLIPTextEncode, 199: LoadImage.
- 輸出： 206: ShowText|pysssss, 136: SaveImage.
- 相依套件： ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-custom-scripts`, `comfyui-ollamagemini`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-flux-kontext-fluxkontextoutpaint-4c72d230`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 056 — `Flux/Kontext/KontextOllamaLocal.json`

Iterate local Ollama-enhanced Kontext prompts from several lists using SageAttention/Torch patches. Its graph has dangling references, and requires a configured Ollama service plus compatible attention kernels.

- 容器／格式： `json_file` / `visual`. SHA-256: `f95b8a2a9c6bed3e30710836f6ccca74ef63f7a58cc6b7803f2b6688e6e285d4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 26 top-level nodes; 26 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `CLIPTextEncode`, `CR Prompt List`, `ConditioningZeroOut`, `DualCLIPLoader`, `FluxGuidance`, `FluxKontextImageScale`, `ImpactSwitch`, `KSampler`, `LayerUtility: PurgeVRAM V2`, `LoadImage`, `ModelPatchTorchSettings`, `Note`, `OllamaFluxKontextEnhancerV2`, `PathchSageAttentionKJ`, `PrimitiveString`, `ReferenceLatent`, `SaveImage`, `ShowText|pysssss`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `ae.safetensors`, `clip_l.safetensors`, `flux1-dev-kontext_fp8_scaled.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 227: CR Prompt List, 213: CR Prompt List, 218: CR Prompt List, 221: CR Prompt List, 6: CLIPTextEncode, 229: CR Prompt List, 204: LoadImage, 237: PrimitiveString.
- 輸出： 136: SaveImage, 234: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyroll`, `comfyui-custom-scripts`, `comfyui-impact-pack`, `comfyui-kjnodes`, `comfyui_layerstyle`, `kontext-super-prompt`.
- 供應商／橋接需求： Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-flux-kontext-kontextollamalocal-3fb25182`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 057 — `Flux/【Flux】ControlnetBasic.json`

Generate with a Flux NF4 checkpoint under depth ControlNet, with Gemini-assisted captions/prompts and image guidance. Bitsandbytes/NF4 support and custom provider settings require independent qualification.

- 容器／格式： `json_file` / `visual`. SHA-256: `5b64508d7d12bb86627069dc2ae289c87fd86e30f0b941816dd110db4f732204`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 15 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AIO_Preprocessor`, `CLIPTextEncode`, `CheckpointLoaderNF4`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `EmptySD3LatentImage`, `FluxGuidance`, `Gemini_API_Zho`, `KSampler`, `LoadImage`, `PreviewImage`, `SaveImage`, `ShowText|pysssss`, `VAEDecode`.
- 明確模型檔名： `Flux/flux-depth-controlnet.safetensors`, `flux1-dev-bnb-nf4.safetensors`.
- 輸入： 11: LoadImage, 6: CLIPTextEncode, 7: CLIPTextEncode.
- 輸出： 19: PreviewImage, 9: SaveImage, 37: ShowText|pysssss [mode 4].
- 相依套件： comfyui_controlnet_aux; ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-flux-flux-controlnetbasic-596d6eb9`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 058 — `Flux/【Flux】Realismlora.json`

Simple Flux Dev FP8 text-to-image with a Boreal realism LoRA. It has a straightforward SaveImage sink but no local public copy or generation evidence.

- 容器／格式： `json_file` / `visual`. SHA-256: `6275ad573ce76ca00ece12275fd30a3cb1ed23aa2dcc741feb9a1a38e1db5284`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 9 top-level nodes; 9 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FluxGuidance`, `KSampler`, `LoraLoaderModelOnly`, `SaveImage`, `VAEDecode`.
- 明確模型檔名： `Flux/boreal-flux-dev-lora-v04_1000_steps.safetensors`, `Flux/flux1-dev-fp8.safetensors`.
- 輸入： 31: CLIPTextEncode, 6: CLIPTextEncode.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-flux-realismlora-cf05aa20`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 059 — `Flux/【Test】Devcompare.json`

Compare Flux Dev sampling/conditioning branches using AdaptiveGuidance, dynamic thresholding and a shared seed. This is a sampler experiment; matching inputs and seeds matter when interpreting side-by-side outputs.

- 容器／格式： `json_file` / `visual`. SHA-256: `00f9926bab63ff447bcef9b8ef03440b65121a690481857c1cfff6104814994e`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AdaptiveGuidance`, `BasicScheduler`, `CLIPTextEncode`, `CLIPTextEncodeFlux`, `CheckpointLoaderSimple`, `DynamicThresholdingFull`, `EmptyLatentImage`, `KSampler`, `KSamplerSelect`, `PrimitiveNode`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveImage`, `Seed Everywhere`, `String Literal`, `VAEDecode`.
- 明確模型檔名： `Flux/flux1-dev-fp8.safetensors`.
- 輸入： 33: CLIPTextEncodeFlux, 34: CLIPTextEncodeFlux, 92: String Literal, 86: CLIPTextEncode, 87: CLIPTextEncode.
- 輸出： 9: SaveImage, 91: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-flux-test-devcompare-4e1f55bf`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 060 — `HandFix.json`

Repair SD1.5 hands using detection/SEGS detailers, OpenPose or MeshGraphormer depth controls and a manual inpaint branch. Several preview sinks expose alternative repair paths; detector/SAM/control weights are required.

- 容器／格式： `json_file` / `visual`. SHA-256: `ed1978b0b1dec657b462b39a63de98fa1cbf9faf3b1d7245567ae01a6270c11d`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 28 top-level nodes; 28 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DWPreprocessor_Provider_for_SEGS //Inspire`, `DetailerForEachPipe`, `DiffControlNetLoader`, `ImpactControlNetApplySEGS`, `ImpactSimpleDetectorSEGS`, `KSampler`, `LoadImage`, `MeshGraphormer-DepthMapPreprocessor`, `MeshGraphormerDepthMapPreprocessorProvider_for_SEGS //Inspire`, `PreviewImage`, `SAMLoader`, `ToBasicPipe`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncodeForInpaint`.
- 明確模型檔名： `bbox/hand_yolov8s.pt`, `control_sd15_inpaint_depth_hand_fp16.safetensors`, `control_v11p_sd15_openpose.pth`, `majicmixRealistic_v7.safetensors`, `sam_vit_b_01ec64.pth`.
- 輸入： 114: CLIPTextEncode, 115: CLIPTextEncode, 102: LoadImage.
- 輸出： 93: PreviewImage, 123: PreviewImage, 128: PreviewImage, 121: PreviewImage, 120: PreviewImage, 94: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-handfix-b4327d1f`; catalog distribution: links-only.
- 公開副本： `workflows/HandFix.json`; SHA-256 `f375d8311d4b0eee48eec53f6adcf1e43893e62dba84b1f59cb074eb82e4e14f`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 061 — `Handfix2.json`

Alternative SD1.5 hand repair with an additional inpaint ControlNet and explicit resizing, using Henmix rather than MajicMix. Preview outputs compare different structural-repair branches.

- 容器／格式： `json_file` / `visual`. SHA-256: `c2179037830c7de9dac8a32ced2550dd852711846796af36738bbaa36b0f7534`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 27 top-level nodes; 27 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DWPreprocessor_Provider_for_SEGS //Inspire`, `DetailerForEachPipe`, `DiffControlNetLoader`, `ImpactControlNetApplySEGS`, `ImpactSimpleDetectorSEGS`, `InpaintPreprocessor`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `MeshGraphormer-DepthMapPreprocessor`, `PreviewImage`, `SAMLoader`, `ToBasicPipe`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncodeForInpaint`.
- 明確模型檔名： `bbox/hand_yolov8s.pt`, `control_sd15_inpaint_depth_hand_fp16.safetensors`, `control_v11p_sd15_inpaint.pth`, `control_v11p_sd15_openpose.pth`, `henmixReal_v5c.safetensors`, `sam_vit_b_01ec64.pth`.
- 輸入： 168: CLIPTextEncode, 169: CLIPTextEncode, 102: LoadImage.
- 輸出： 128: PreviewImage, 153: PreviewImage, 154: PreviewImage, 171: PreviewImage, 170: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-handfix2-e2f1bd24`; catalog distribution: links-only.
- 公開副本： `workflows/Handfix2.json`; SHA-256 `4d777dce4fb434bfbc180bcd82493fc464db3ce9259073aeb612a461d5c0c806`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 062 — `Kolors/【Kolors】MixwithmklanRealistic.json`

Blend Kolors UNET with an SDXL checkpoint, use ChatGLM prompt conditioning and DetailDaemon/tiled refinement. Loader semantics and mixed-model compatibility need native validation.

- 容器／格式： `json_file` / `visual`. SHA-256: `ab12e6c1e76b3d2a7d9d66acf07dc6b33f89e1d9e0b26c635bba6b0786dcf4fb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 27 top-level nodes; 27 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AlignYourStepsScheduler`, `CFGGuider`, `CheckpointLoaderSimple`, `DetailDaemonSamplerNode`, `ImageResize+`, `ImageUpscaleWithModel`, `KSampler`, `KSamplerSelect`, `KolorsAwesomePrompts`, `MZ_ChatGLM3Loader`, `MZ_ChatGLM3_V2`, `MZ_KolorsUNETLoaderV2`, `ModelMergeSimple`, `PreviewImage`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SamplerCustomAdvanced`, `SimpleMath+`, `TiledDiffusion`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `SDXL/mklanRealistic_mklan230realHyperSD.safetensors`, `chatglm3-4bit.safetensors`, `kcolors/diffusion_pytorch_model.fp16.safetensors`, `sdxl_vae.safetensors`.
- 輸入： 138: KolorsAwesomePrompts.
- 輸出： 137: PreviewImage, 160: PreviewImage.
- 相依套件： ComfyUI_essentials; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-kolors-kolors-mixwithmklanrealistic-73bd7f6a`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 063 — `Kolors/【Kolors】Render.json`

Render image references using mixed Kolors/SDXL, depth/line ControlNet patches and a Kolors IPAdapter, then upscale. Requires Kolors-specific vision/text encoders and model merge/control patch support.

- 容器／格式： `json_file` / `visual`. SHA-256: `090ee3081cf4685c49be48401a2dee596f3dffc6bade685c880fe4062ce67e6c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 40 top-level nodes; 40 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `ACN_ScaledSoftControlNetWeights`, `AnyLineArtPreprocessor_aux`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `ControlNetLoader`, `DepthAnythingV2Preprocessor`, `EmptyLatentImage`, `IPAdapterAdvanced`, `IPAdapterModelLoader`, `IPAdapterNoise`, `ImageResize+`, `ImageUpscaleWithModel`, `KSampler`, `KolorsAwesomePrompts`, `LoadImage`, `MZ_ChatGLM3Loader`, `MZ_ChatGLM3_V2`, `MZ_KolorsControlNetPatch`, `MZ_KolorsUNETLoaderV2`, `ModelMergeSimple`, `PixelPerfectResolution`, `Reroute`, `SaveImage`, `SetUnionControlNetType`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`.
- 明確模型檔名： `4x-UltraSharp.pth`, `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `aiartistHyperrayXLXL_hypersdxl.safetensors`, `chatglm3-4bit.safetensors`, `depth_anything_v2_vitl.pth`, `kcolors/diffusion_pytorch_model.fp16.safetensors`, `kolors/ip_adapter_plus_genernal.bin`, `kolors/pytorch_model.bin`.
- 輸入： 42: LoadImage, 43: LoadImage, 144: KolorsAwesomePrompts.
- 輸出： 111: SaveImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Advanced-ControlNet; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-kolors-kolors-render-83878fc6`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 064 — `Krita/【Krita】AllModelsNoStyle.json`

Krita Graph text/image generation with canvas input, user-exposed ETN parameters and a checkpoint sampler. Parameters can override saved model selectors; ETN_KritaOutput returns to Krita, not a standard SaveImage sink.

- 容器／格式： `json_file` / `visual`. SHA-256: `55c58cac0f78fe7a239709a9a94f96e3caf496dfc16e19fcc333bec70e7c241c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 15 top-level nodes; 15 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ETN_KritaCanvas`, `ETN_KritaOutput`, `ETN_Parameter`, `EmptyLatentImage`, `KSampler`, `VAEDecode`.
- 明確模型檔名： `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`, `hunyuan_dit_comfyui/hunyuan_dit_1.2.safetensors`.
- 輸入： 2: CLIPTextEncode, 9: ETN_KritaCanvas, 3: CLIPTextEncode, 8: ETN_Parameter, 14: ETN_Parameter, 7: ETN_Parameter, 15: ETN_Parameter, 16: ETN_Parameter, 6: ETN_Parameter, 17: ETN_Parameter.
- 輸出： 12: ETN_KritaOutput.
- 相依套件： Krita AI Diffusion graph bridge / ComfyUI tooling nodes. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Krita graph plugin bridge; matching Krita and ComfyUI extension.
- 公開目錄／工作流庫 ID： `dseditor-krita-krita-allmodelsnostyle-9882666d`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 065 — `Krita/【Krita】Allmodel.json`

Krita Graph generation plus SDXL style selection and ETN parameter binding. Needs the Krita/Comfy bridge and matching frontend schema; it is not an independent Web_Comfyui form.

- 容器／格式： `json_file` / `visual`. SHA-256: `870ebba95284f9dfba01c6122b67a2b89b56b9ef39aac288f2b81d3ea8a22b1b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ETN_KritaCanvas`, `ETN_KritaOutput`, `ETN_Parameter`, `EmptyLatentImage`, `KSampler`, `SDXLPromptStylerAll`, `VAEDecode`.
- 明確模型檔名： `hunyuan_dit_comfyui/hunyuan_dit_1.2.safetensors`.
- 輸入： 6: ETN_Parameter, 2: CLIPTextEncode, 9: ETN_KritaCanvas, 14: ETN_Parameter, 15: ETN_Parameter, 7: ETN_Parameter, 16: ETN_Parameter, 3: CLIPTextEncode, 17: ETN_Parameter, 8: ETN_Parameter, 19: ETN_Parameter, 18: SDXLPromptStylerAll.
- 輸出： 12: ETN_KritaOutput.
- 相依套件： Krita AI Diffusion graph bridge / ComfyUI tooling nodes. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Krita graph plugin bridge; matching Krita and ComfyUI extension.
- 公開目錄／工作流庫 ID： `dseditor-krita-krita-allmodel-779b712c`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 066 — `Krita/【Krita】COSXL.json`

Krita canvas instruction editing using CosXL, DualCFG and Pix2Pix conditioning. The image and parameter bridge must be supplied by Krita; output is ETN_KritaOutput.

- 容器／格式： `json_file` / `visual`. SHA-256: `48d36d5a1d40a22f19c94320e32f7be7f74911571aef9c1a5c7bc3c94cb321e0`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 14 top-level nodes; 14 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `DualCFGGuider`, `ETN_KritaCanvas`, `ETN_KritaOutput`, `ETN_Parameter`, `InstructPixToPixConditioning`, `KSamplerSelect`, `RandomNoise`, `SamplerCustomAdvanced`, `VAEDecode`.
- 明確模型檔名： `robmixCosxlEdit_cosxlZenithV20.safetensors`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 97: ETN_Parameter, 81: ETN_Parameter, 79: ETN_KritaCanvas.
- 輸出： 80: ETN_KritaOutput.
- 相依套件： Krita AI Diffusion graph bridge / ComfyUI tooling nodes. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Krita graph plugin bridge; matching Krita and ComfyUI extension.
- 公開目錄／工作流庫 ID： `dseditor-krita-krita-cosxl-c08fa061`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 067 — `Krita/【Krita】RoomShow.json`

Krita room rendering from depth/line controls, translation and SDXL Union ControlNet. A stale link exists, and Krita parameter/output bridge support is required.

- 容器／格式： `json_file` / `visual`. SHA-256: `3a74bfcce512d1c6b93d2896849cc0d5a06dc135ee0dd2f036ed8c9ba4720b68`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 21 top-level nodes; 21 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `AnyLineArtPreprocessor_aux`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DepthAnythingV2Preprocessor`, `ETN_KritaCanvas`, `ETN_KritaOutput`, `ETN_Parameter`, `ETN_Translate`, `EmptyLatentImage`, `KSampler`, `PixelPerfectResolution`, `SetUnionControlNetType`, `VAEDecode`.
- 明確模型檔名： `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `aiartistHyperrayXLXL_hypersdxl.safetensors`, `depth_anything_v2_vitl.pth`.
- 輸入： 163: CLIPTextEncode, 164: CLIPTextEncode, 201: ETN_KritaCanvas, 213: ETN_Parameter, 215: ETN_Parameter, 208: ETN_Parameter, 214: ETN_Parameter.
- 輸出： 202: ETN_KritaOutput.
- 相依套件： comfyui_controlnet_aux; Krita AI Diffusion graph bridge / ComfyUI tooling nodes. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Krita graph plugin bridge; matching Krita and ComfyUI extension.
- 公開目錄／工作流庫 ID： `dseditor-krita-krita-roomshow-3a807df2`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 068 — `LTXVideo2/LTXAudioToVideo0218.json`

Generate LTX-2 video driven by uploaded audio and an input image, keeping audio/video latents separate through encode/decode. Requires a large custom LTX checkpoint, Gemma/connector and both VAEs; source audio is a concrete input.

- 容器／格式： `json_file` / `visual`. SHA-256: `565a4f5707f60a7a0ba67c82cf3ba17350ab0ad8a3acfc6aac9b2ab0846643f6`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 44 top-level nodes; 44 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AudioToFrameCount`, `CFGGuider`, `CLIPTextEncode`, `ConditioningZeroOut`, `CreateVideo`, `DualCLIPLoader`, `EmptyLTXVLatentVideo`, `GetImageSizeAndCount`, `GetNode`, `ImageResizeKJv2`, `KSamplerSelect`, `LTXVAudioVAEEncode`, `LTXVConcatAVLatent`, `LTXVConditioning`, `LTXVImgToVideoInplace`, `LTXVPreprocess`, `LTXVSeparateAVLatent`, `LoadImage`, `ManualSigmas`, `Note`, `PrimitiveFloat`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveVideo`, `SetLatentNoiseMask`, `SetNode`, `SolidMask`, `UNETLoader`, `VAEDecodeTiled`, `VAELoaderKJ`, `VHS_LoadAudioUpload`.
- 明確模型檔名： `LTX2_audio_vae_bf16.safetensors`, `LTX2_video_vae_bf16.safetensors`, `gemma_3_12B_it_fp8_scaled.safetensors`, `ltx-2-19b-embeddings_connector_distill_bf16.safetensors`, `ltx2-phr00tmerge-nsfw-v62.safetensors`.
- 輸入： 122: CLIPTextEncode, 169: VHS_LoadAudioUpload, 98: LoadImage.
- 輸出： 154: CreateVideo, 159: SaveVideo.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `Listhelper`, `comfy-core`, `comfyui-kjnodes`, `comfyui-videohelpersuite`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-ltxvideo2-ltxaudiotovideo0218-0d20c14b`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 069 — `LTXVideo2/LTXVideoSleepy0218.json`

Create chained LTX-2 audiovisual sequences with first/continuation subgraphs, guide cropping, attention tuning and frame interpolation. Three modern subgraph definitions carry core runtime nodes; the current sanitizer would drop them if this entry were promoted.

- 容器／格式： `json_file` / `visual`. SHA-256: `9fc278d916a9b72f44f28a8cf68df462dc5945cb7018b68a6ecacb65b8e62ade`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 36 active expanded nodes. Top-level check: **pass**.
- 節點類型： `8d9c4e3a-cb41-4dec-818e-124716d16086`, `AudioConcat`, `BasicGuider`, `CLIPTextEncode`, `ConditioningZeroOut`, `DualCLIPLoader`, `EmptyLTXVLatentVideo`, `FL_IntToFloat`, `FL_RIFE`, `ImageResizeKJv2`, `KSamplerSelect`, `LTX2AttentionTunerPatch`, `LTXVAddGuideMulti`, `LTXVAudioVAEDecode`, `LTXVChunkFeedForward`, `LTXVConcatAVLatent`, `LTXVConditioning`, `LTXVCropGuides`, `LTXVEmptyLatentAudio`, `LTXVNormalizingSampler`, `LTXVPreprocess`, `LTXVSeparateAVLatent`, `LoadImage`, `ManualSigmas`, `ModelDownloader`, `PrimitiveFloat`, `PrimitiveInt`, `RandomNoise`, `UNETLoader`, `VAEDecodeTiled`, `VAELoaderKJ`, `VHS_SelectImages`, `VHS_VideoCombine`, `cf483555-e6d9-4b76-b9c0-55ca33bcfefd`, `fcecfd9d-c7ba-414c-9903-b3e372adc08f`.
- 明確模型檔名： `LTX2_audio_vae_bf16.safetensors`, `LTX2_video_vae_bf16.safetensors`, `gemma_3_12B_it_fp8_scaled.safetensors`, `ltx-2-19b-embeddings_connector_distill_bf16.safetensors`, `ltx2-phr00tmerge-nsfw-v62.safetensors`.
- 輸入： 106: LoadImage, 303: LoadImage, 6: CLIPTextEncode.
- 輸出： 17: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_Fill-Nodes; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-LTXVideo`, `Listhelper`, `comfy-core`, `comfyui-kjnodes`, `comfyui-videohelpersuite`, `comfyui_fill-nodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-ltxvideo2-ltxvideosleepy0218-8eff659a`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Modern subgraph definitions must survive sanitation; baseline sanitizer drops definitions and catalog inventory is shallow. Static catalog omits active nested node types: AudioConcat, BasicGuider, CLIPTextEncode, ConditioningZeroOut, EmptyLTXVLatentVideo, FL_IntToFloat, FL_RIFE, KSamplerSelect, LTX2AttentionTunerPatch, LTXVAudioVAEDecode, LTXVChunkFeedForward, LTXVConcatAVLatent, LTXVConditioning, LTXVCropGuides, LTXVEmptyLatentAudio, LTXVNormalizingSampler, LTXVSeparateAVLatent, ManualSigmas, PrimitiveFloat, PrimitiveInt, RandomNoise, VHS_SelectImages No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 070 — `LocalVideoModel/HunyuanVideo/【FastT2V】HunyuanVideo.json`

Generate FastVideo Hunyuan text-to-video using HunyuanVideoWrapper, downloadable text encoders, dedicated VAE and VideoHelperSuite. Attention implementation and VRAM are unverified; the public copy is static-only.

- 容器／格式： `json_file` / `visual`. SHA-256: `23736abee1f0394c5ed01a9e918d071fb2864160cfc7d5594e2243c5b408266d`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 8 top-level nodes; 8 active expanded nodes. Top-level check: **pass**.
- 節點類型： `DownloadAndLoadHyVideoTextEncoder`, `HyVideoCustomPromptTemplate`, `HyVideoDecode`, `HyVideoModelLoader`, `HyVideoSampler`, `HyVideoTextEncode`, `HyVideoVAELoader`, `VHS_VideoCombine`.
- 明確模型檔名： `Kvae/hunyuan_video_vae_bf16.safetensors`, `hunyuan_video_FastVideo_720_fp8_e4m3fn.safetensors`.
- 輸入： 59: HyVideoCustomPromptTemplate, 16: DownloadAndLoadHyVideoTextEncoder, 65: HyVideoTextEncode.
- 輸出： 66: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-hunyuan-fast-text-to-video`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/hunyuan-fast-text-to-video.json`; SHA-256 `692da2364c1f660e717ebd4b8d0d8a589a4275b52f9a1f05f08a84401e19ba24`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 071 — `LocalVideoModel/HunyuanVideo/【HYVideo】ImageMotion.json`

Generate Hunyuan video motion from an image using ImageMotionGuider, Florence captioning and tiled decode. This uses native-style Hunyuan nodes rather than the FastVideo wrapper graph.

- 容器／格式： `json_file` / `visual`. SHA-256: `135ab1ec7676a932c7e4f293fffeaf196c6c3d1782d5e3b672b353c0df4bb917`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 23 top-level nodes; 23 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoader`, `Florence2Run`, `FluxGuidance`, `ImageConcatMulti`, `ImageMotionGuider`, `ImageResize+`, `KSamplerSelect`, `LoadImage`, `ModelSamplingSD3`, `RandomNoise`, `SamplerCustomAdvanced`, `ShowText|pysssss`, `StringFunction|pysssss`, `UNETLoader`, `VAEDecodeTiled`, `VAEEncode`, `VAELoader`, `VHS_VideoCombine`.
- 明確模型檔名： `clip_l.safetensors`, `hunyuan_video_720_cfgdistill_fp8_e4m3fn.safetensors`, `hunyuan_video_vae_bf16.safetensors`, `llava_llama3_fp8_scaled.safetensors`.
- 輸入： 91: LoadImage, 44: CLIPTextEncode.
- 輸出： 83: VHS_VideoCombine, 126: ShowText|pysssss.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-localvideomodel-hunyuanvideo-hyvideo-imagemotion-d3b35cf3`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 072 — `MiniMAXH3/FL2VA頭尾格6STEP.json`

Generate MiniMax H3 first/last-frame video plus audio using local FL2VA weights, Turbo LoRA/sampler and memory-efficient attention patches. Despite the provider brand, this is a local model graph, not proof of a MiniMax hosted API integration.

- 容器／格式： `json_file` / `visual`. SHA-256: `65652c8d761878ab0ad7416f1ac2ead76d8e0df3677a40151a5d8c18ceac9b8e`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPLoader`, `CreateVideo`, `LoadImage`, `MiniMaxH3ImageToVideo`, `MiniMaxH3MemoryEfficientSageAttentionPatch`, `MiniMaxH3MemoryEfficientSolAttentionPatch`, `MiniMaxH3TurboLoRA`, `MiniMaxH3TurboSampler`, `PathchSageAttentionKJ`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveVideo`, `UNETLoader`, `VAEDecode`, `VAEDecodeAudio`, `VAELoader`.
- 明確模型檔名： `H3/minimax_h3_turbo_v4_step600_ema.safetensors`, `minimax_h3_audio_vae_fp32.safetensors`, `minimax_h3_fl2va_pruned_int8_convrot.safetensors`, `minimax_h3_video_vae_fp16.safetensors`, `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.
- 輸入： 5b: LoadImage, 5: LoadImage.
- 輸出： 18: SaveVideo, 17: CreateVideo.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `Saganaki22/ComfyUI-sol-attn`, `comfy-core`, `comfyui-kjnodes`, `larryvrh/ComfyUI-MiniMax-H3-Turbo`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-minimaxh3-fl2va-6step-ea0e2293`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 073 — `MiniMAXMusic/StarCandyMusic.json`

Generate music locally with MiniMax Music3 diffusion/text/audio-VAE weights and save MP3. It does not contain a remote MiniMax API node; lyrics/music conditioning is supplied to MiniMaxMusic3TextEncode.

- 容器／格式： `json_file` / `visual`. SHA-256: `6b8dd5ee4c599ae4a0c26a6cfc311508f8545eb6542d5398650b566e58325994`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 9 top-level nodes; 9 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `ConditioningZeroOut`, `EmptyMiniMaxMusic3LatentAudio`, `KSampler`, `MiniMaxMusic3TextEncode`, `SaveAudioMP3`, `UNETLoader`, `VAEDecodeAudio`, `VAELoader`.
- 明確模型檔名： `minimax_music3_dav.safetensors`, `minimax_music3_dit_fp16.safetensors`, `minimax_music3_text_encoder_pruned_int8_convrot.safetensors`.
- 輸入： 4: MiniMaxMusic3TextEncode.
- 輸出： 9: SaveAudioMP3.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-minimaxmusic-starcandymusic-f0078bd7`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 074 — `PID/ZImagePID.json`

Generate Z-image output and enlarge/refine with a PiD model and RTX Video Super Resolution. Requires PiD/Gemma and RTX-specific runtime support in addition to Z-image/Qwen weights; model compatibility and device support remain unverified.

- 容器／格式： `json_file` / `visual`. SHA-256: `b3e5747e6dbc92cd3d8241aca8e6dfb610259166f11079e63d6291ee6e6c5ac9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 27 top-level nodes; 27 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPLoader`, `CLIPTextEncode`, `ComfyMathExpression`, `ConditioningZeroOut`, `ContextWindowsManual`, `EmptyChromaRadianceLatentImage`, `EmptySD3LatentImage`, `KSampler`, `KSamplerSelect`, `MarkdownNote`, `ModelSamplingAuraFlow`, `PiDConditioning`, `PrimitiveInt`, `PrimitiveStringMultiline`, `RTXVideoSuperResolution`, `SamplerCustom`, `SaveImage`, `UNETLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `gemma_2_2b_it_elm_bf16.safetensors`, `pid_flux1_1024_to_4096_4step_mxfp8.safetensors`, `qwen_3_4b.safetensors`, `z_image_turbo_bf16.safetensors`.
- 輸入： 67: CLIPTextEncode, 85: CLIPTextEncode, 92: PrimitiveStringMultiline.
- 輸出： 93: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui_nvidia_rtx_nodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-pid-zimagepid-b6a35a7e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 075 — `QuickreplaceBG.json`

Remove background with Rembg/LaMa and regenerate it with Layer Diffusion plus DreamShaper XL. Output is preview-only and includes specialized layer-conditioning nodes.

- 容器／格式： `json_file` / `visual`. SHA-256: `55bdc988bd953d807c26b68add80a981dbb8558bb39be31056e943634e6b2cda`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 12 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Efficient Loader`, `GetImageSize`, `Image Rembg (Remove Background)`, `ImageToMask`, `JWImageResizeByFactor`, `KSampler (Efficient)`, `LamaRemover`, `LayeredDiffusionCondApply`, `LayeredDiffusionDiffApply`, `LoadImage`, `PreviewImage`, `SDXLPromptStyler`, `VAEEncode`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`.
- 輸入： 121: SDXLPromptStyler, 20: LoadImage.
- 輸出： 160: PreviewImage [mode 4].
- 相依套件： efficiency-nodes-comfyui. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-quickreplacebg-65aea4e1`; catalog distribution: links-only.
- 公開副本： `workflows/QuickreplaceBG.json`; SHA-256 `a97a081bc42469e4bb222cc0bc42c6685b9a5d8cbb68c91715707d19215dbd51`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 076 — `Qwen/ImageEditQwen.json`

Decompose/edit an input image through Qwen Image Layered using EmptyQwenImageLayeredLatentImage and batch extraction. The filename ImageEditQwen does not make it a generic image-edit runner; it is explicitly a layered-model graph.

- 容器／格式： `json_file` / `visual`. SHA-256: `c8ba7b0dbe8e2d95f181fe8baaa84abce3717a57913a9c771bf532048bf01dd9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 23 top-level nodes; 21 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoaderGGUF`, `CLIPTextEncode`, `ConditioningZeroOut`, `EmptyQwenImageLayeredLatentImage`, `GetImageSize`, `ImageScaleToMaxDimension`, `KSampler`, `LatentCutToBatch`, `LoadImage`, `LoraLoaderModelOnly`, `ModelPatchTorchSettings`, `ModelSamplingAuraFlow`, `Note`, `PathchSageAttentionKJ`, `ReferenceLatent`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `Qwen/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors`, `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`, `Qwen_Image_Layered-Q8_0.gguf`, `qwen_image_layered_vae.safetensors`.
- 輸入： 74: LoadImage, 127: CLIPTextEncode.
- 輸出： 60: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GGUF`, `comfy-core`, `comfyui-kjnodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-qwen-imageeditqwen-aa006ed4`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 077 — `Qwen/ListhelperQwen2511.json`

Use a local GGUF LLM helper to prepare/list edit instructions, then edit an image with Qwen 2511 Lightning. ModelDownloader and GGUFInference imply extra runtime weights that the explicit filename list alone does not enumerate.

- 容器／格式： `json_file` / `visual`. SHA-256: `08980e7319be9a03ed213b0a0ccc41de45573e94f803a943903eb92b456fc039`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 18 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CFGNorm`, `CLIPLoaderGGUF`, `ConditioningZeroOut`, `EmptyLatentImage`, `FluxKontextMultiReferenceLatentMethod`, `GGUFInference`, `KSampler`, `LoadImage`, `ModelDownloader`, `ModelSamplingAuraFlow`, `PathchSageAttentionKJ`, `PreviewAny`, `SaveImage`, `ShowText|pysssss`, `TextEncodeQwenImageEditPlus`, `UNETLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`, `Qwen_Image-VAE.safetensors`, `qwen_image_edit_2511_fp8_e4m3fn_scaled_lightning_comfyui.safetensors`.
- 輸入： 42: TextEncodeQwenImageEditPlus, 10: LoadImage.
- 輸出： 59: ShowText|pysssss, 48: SaveImage, 61: PreviewAny.
- 相依套件： ComfyUI-GGUF; ComfyUI-KJNodes / related version-specific extensions; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GGUF`, `Listhelper`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-kjnodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-qwen-listhelperqwen2511-4e82d905`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 078 — `Qwen/QwenCompare20251225.json`

Compare Qwen Edit 2511 FP8 and Qwen Edit 2509 Nunchaku INT4 variants using shared reference inputs. Quantization runtimes, model versions and steps differ; it is a comparison workflow, not interchangeable adapters.

- 容器／格式： `json_file` / `visual`. SHA-256: `4896ac1011b5d1273e0387fc619273618c429de54e9fa4046e807835aa1eaae9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 25 top-level nodes; 25 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CFGNorm`, `CLIPLoaderGGUF`, `ConditioningZeroOut`, `FluxKontextMultiReferenceLatentMethod`, `ImageScaleToTotalPixels`, `KSampler`, `LoadImage`, `ModelSamplingAuraFlow`, `NunchakuQwenImageDiTLoader`, `PathchSageAttentionKJ`, `SaveImage`, `TextEncodeQwenImageEditPlus`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`, `Qwen_Image-VAE.safetensors`, `qwen_image_edit_2511_fp8_e4m3fn_scaled_lightning_comfyui.safetensors`, `svdq-int4_r128-qwen-image-edit-2509-lightning-8steps-251115.safetensors`.
- 輸入： 42: TextEncodeQwenImageEditPlus, 10: LoadImage, 24: TextEncodeQwenImageEditPlus.
- 輸出： 9: SaveImage, 48: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GGUF`, `ComfyUI-nunchaku`, `comfy-core`, `comfyui-kjnodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-qwen-qwencompare20251225-11805b3e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 079 — `Qwen/QwenEdit2509PoseClothes.json`

Guide Qwen Edit with person/clothing/pose images and DWpose, using Nunchaku and a custom tiled/upscale VAE path. Three input images and the unusual Wan-derived image VAE require specific qualification.

- 容器／格式： `json_file` / `visual`. SHA-256: `c9802e35855c9936b5c57faf7410784950dae1773bdcb6c973d745f74d86b86b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CFGNorm`, `CLIPLoaderGGUF`, `ConditioningZeroOut`, `DWPreprocessor`, `ImageScaleToTotalPixels`, `KSampler`, `LoadImage`, `ModelSamplingAuraFlow`, `NunchakuQwenImageDiTLoader`, `PathchSageAttentionKJ`, `SaveImage`, `TextEncodeQwenImageEditPlus`, `VAEEncode`, `VAEUtils_CustomVAELoader`, `VAEUtils_VAEDecodeTiled`.
- 明確模型檔名： `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`, `Wan2.1_VAE_upscale2x_imageonly_real_v1_bf16.safetensors`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `svdq-int4_r128-qwen-image-edit-2509-lightning-8steps-251115.safetensors`, `yolox_l.onnx`.
- 輸入： 32: LoadImage, 33: LoadImage, 10: LoadImage, 24: TextEncodeQwenImageEditPlus.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI-GGUF; comfyui_controlnet_aux; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GGUF`, `ComfyUI-nunchaku`, `comfy-core`, `comfyui-kjnodes`, `comfyui_controlnet_aux`, `spacepxl/ComfyUI-VAE-Utils`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-qwen-qwenedit2509poseclothes-7308c8a1`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 080 — `Qwen/QwenImageLayered.json`

Decompose an image into Qwen layers using a multi-frame latent, reference conditioning and LatentCutToBatch. The public copy retains EmptyHunyuanLatentVideo as its shape mechanism; layer count and VAE semantics must be checked.

- 容器／格式： `json_file` / `visual`. SHA-256: `df6d8c6bdd4508e0e4d495be9d0a80765a3cd89976a16e96486d46898e19597e`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 22 top-level nodes; 22 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoaderGGUF`, `CLIPTextEncode`, `ConditioningZeroOut`, `EmptyHunyuanLatentVideo`, `ImageResizeKJv2`, `KSampler`, `LatentCutToBatch`, `LoadImage`, `LoraLoaderModelOnly`, `ModelPatchTorchSettings`, `ModelSamplingAuraFlow`, `Note`, `PathchSageAttentionKJ`, `ReferenceLatent`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `Qwen/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors`, `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`, `Qwen_Image_Layered-Q8_0.gguf`, `qwen_image_layered_vae.safetensors`.
- 輸入： 6: CLIPTextEncode, 74: LoadImage.
- 輸出： 60: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GGUF`, `comfy-core`, `comfyui-kjnodes`, `kijai/ComfyUI-KJNodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-qwen-image-layered`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/qwen-image-layered.json`; SHA-256 `a785ca07cbc2af1c709a7c9d659dd6248cc2f743fdfb9189db346e7846254c8d`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 081 — `Qwen/Qwen_Image_2512_GGUF.json`

Generate Qwen Image 2512 with GGUF UNET/text encoder and a four-step Lightning LoRA. Requires all model files and GGUF/CFGNorm compatibility; static checks do not verify image quality.

- 容器／格式： `json_file` / `visual`. SHA-256: `b6e7b89fbf1215d503e28af24b15000d9d9374ff28c5664fe63a9dc4f466d0f0`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CFGNorm`, `CLIPLoaderGGUF`, `CLIPTextEncode`, `ConditioningZeroOut`, `EmptySD3LatentImage`, `KSampler`, `LoraLoaderModelOnly`, `ModelSamplingAuraFlow`, `Note`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors`, `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`, `Qwen_Image-VAE.safetensors`, `qwen-image-2512-Q8_0.gguf`.
- 輸入： 6: CLIPTextEncode.
- 輸出： 60: SaveImage.
- 相依套件： ComfyUI-GGUF. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GGUF`, `comfy-core`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-qwen-image-2512-gguf`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/qwen-image-2512-gguf.json`; SHA-256 `23ff8c9386bc0ce8ff8607fadc97936520351f2744a9f12635b109ad8755c1a9`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 082 — `SCAIL/SCAILAnimate.json`

Animate a reference person with Wan SCAIL2, driven by an input video and SAM3 tracked masks. Uses an NVFP4 SCAIL checkpoint, context windows and SageAttention; this depends on compatible GPU kernels and node versions.

- 容器／格式： `json_file` / `visual`. SHA-256: `1c307acc4feda0f838e3fe4361d84bdc845af314fa833b2a977bb238e93e7a74`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 33 top-level nodes; 33 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `GetImageSize`, `ImageFromBatch`, `KSampler`, `LoadImage`, `LoraLoaderModelOnly`, `MarkdownNote`, `ModelSamplingSD3`, `PathchSageAttentionKJ`, `Reroute`, `ResizeImageMaskNode`, `SAM3_VideoTrack`, `SCAIL2ColoredMask`, `UNETLoader`, `VAEDecode`, `VAELoader`, `VHS_LoadVideo`, `VHS_VideoCombine`, `VHS_VideoInfo`, `WanContextWindowsManual`, `WanSCAILToVideo`.
- 明確模型檔名： `Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors`, `clip_vision_h.safetensors`, `sam3.1_multiplex_fp16.safetensors`, `umt5_xxl_fp16.safetensors`, `wan2.1_14B_SCAIL_2_nvfp4_comfy_V2.safetensors`, `wan_2.1_vae.safetensors`.
- 輸入： 109: CLIPTextEncode, 7: CLIPTextEncode, 115: CLIPTextEncode, 6: CLIPTextEncode, 58: LoadImage, 113: VHS_LoadVideo.
- 輸出： 49: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-kjnodes`, `comfyui-videohelpersuite`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-scail-scailanimate-88064521`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 083 — `SCAIL/SCAILReplace.json`

Replace/animate a tracked video subject using Wan SCAIL2 with an FP8 checkpoint and SAM3 masks. It is a distinct saved model variant from SCAILAnimate, not a duplicate NVFP4-qualified path.

- 容器／格式： `json_file` / `visual`. SHA-256: `4da275905c7f9e45792b77e368ed57250a81ea26b8f506742161526f99606c73`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 33 top-level nodes; 33 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `CheckpointLoaderSimple`, `GetImageSize`, `ImageFromBatch`, `KSampler`, `LoadImage`, `LoraLoaderModelOnly`, `MarkdownNote`, `ModelSamplingSD3`, `PathchSageAttentionKJ`, `Reroute`, `ResizeImageMaskNode`, `SAM3_VideoTrack`, `SCAIL2ColoredMask`, `UNETLoader`, `VAEDecode`, `VAELoader`, `VHS_LoadVideo`, `VHS_VideoCombine`, `VHS_VideoInfo`, `WanContextWindowsManual`, `WanSCAILToVideo`.
- 明確模型檔名： `Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors`, `clip_vision_h.safetensors`, `sam3.1_multiplex_fp16.safetensors`, `umt5_xxl_fp16.safetensors`, `wan2.1_14B_SCAIL_2_fp8_scaled.safetensors`, `wan_2.1_vae.safetensors`.
- 輸入： 7: CLIPTextEncode, 6: CLIPTextEncode, 109: CLIPTextEncode, 115: CLIPTextEncode, 58: LoadImage, 113: VHS_LoadVideo.
- 輸出： 49: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-kjnodes`, `comfyui-videohelpersuite`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-scail-scailreplace-17f55c71`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 084 — `SDXL/ArtCompare.json`

Compare SDXL style-list prompts in a labeled image montage. The checkpoint is a hosted numeric ID with a display label, not a portable local model filename; it requires deliberate model resolution before running locally.

- 容器／格式： `json_file` / `visual`. SHA-256: `3fb1d26ff455c3e6e8d25e5faec52e763a715ed8851b3dd1e405e822ee418779`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 19 top-level nodes; 19 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `ImageConcatFromBatch`, `ImageListToImageBatch`, `KSampler`, `LayerStyle: DropShadow`, `LayerUtility: SimpleTextImage`, `SDXLPromptStylerAll`, `SaveImage`, `StringFunction|pysssss`, `Text Multiline`, `VAEDecode`, `easy promptList`.
- 明確模型檔名： none observed; inspect model selectors, implicit downloads and provider models.
- 輸入： 34: LayerUtility: SimpleTextImage, 36: SDXLPromptStylerAll, 37: SDXLPromptStylerAll, 38: SDXLPromptStylerAll, 39: SDXLPromptStylerAll, 32: Text Multiline, 22: CLIPTextEncode, 21: CLIPTextEncode, 13: easy promptList.
- 輸出： 31: SaveImage.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-Easy-Use; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-custom-scripts`, `comfyui-easy-use`, `comfyui-impact-pack`, `comfyui-kjnodes`, `pr-was-node-suite-comfyui-47064894`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-artcompare-d170c423`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. CheckpointLoaderSimple uses an opaque hosted numeric ID/display name; resolve an explicit licensed local checkpoint, without guessing.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 085 — `SDXL/COSXLEditStyleChanger.json`

Apply instruction/style edits to an uploaded image using CosXL Edit and DualCFG Pix2Pix conditioning. The public copy needs the specific CosXL checkpoint and style/resize custom nodes.

- 容器／格式： `json_file` / `visual`. SHA-256: `a7062ce4849f0d76c4ea7e888b4da15162061de2e11620b57f05674289aec1eb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 14 top-level nodes; 14 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `DualCFGGuider`, `ImageResize+`, `InstructPixToPixConditioning`, `KSamplerSelect`, `LoadImage`, `RandomNoise`, `SDXLPromptStylerAll`, `SamplerCustomAdvanced`, `SaveImage`, `VAEDecode`.
- 明確模型檔名： `CosXL/cosxl_edit.safetensors`.
- 輸入： 29: LoadImage, 90: SDXLPromptStylerAll, 46: CLIPTextEncode, 8: CLIPTextEncode.
- 輸出： 74: SaveImage.
- 相依套件： ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui_essentials`, `wolfden-styler`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-cosxl-style-edit`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/cosxl-style-edit.json`; SHA-256 `ab355699869cc36ae2e1821174fdea6a9b54a4da3815deb44bc2166a10df203d`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 086 — `SDXL/LineArtColor.json`

Color/render a line drawing using AnyLine/MistoLine, auto-tags and styled prompts. A stale output link and hosted numeric checkpoint selector block straightforward local use; no checkpoint should be guessed.

- 容器／格式： `json_file` / `visual`. SHA-256: `80b259446aeb767028da6d4ec43d905cbe3206b700a973a1eb626ea69bdbe54f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 16 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `AnyLineArtPreprocessor_aux`, `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `EmptyLatentImage`, `ImageResize+`, `KSampler`, `LoadImage`, `PreviewImage`, `Prompts`, `SDXLPromptStylerAll`, `StringFunction|pysssss`, `Text Concatenate`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `MistoLine/mistoLine_fp16.safetensors`.
- 輸入： 19: SDXLPromptStylerAll, 43: LoadImage, 17: Prompts.
- 輸出： 132: PreviewImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-lineartcolor-4e56ddfe`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed. CheckpointLoaderSimple uses an opaque hosted numeric ID/display name; resolve an explicit licensed local checkpoint, without guessing.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 087 — `SDXL/LineArtColorArtists.json`

Artist-style variant of line-art coloring with the same AnyLine/MistoLine architecture. It separately retains a stale output link and hosted checkpoint ID; title similarity does not make it a validated duplicate.

- 容器／格式： `json_file` / `visual`. SHA-256: `05d056f169cdbf71bb315d5d4819594d37fd14093ce0278f3b3bd55ff39e53f3`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 16 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `AnyLineArtPreprocessor_aux`, `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `EmptyLatentImage`, `ImageResize+`, `KSampler`, `LoadImage`, `PreviewImage`, `Prompts`, `SDXLPromptStylerAll`, `StringFunction|pysssss`, `Text Concatenate`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `MistoLine/mistoLine_fp16.safetensors`.
- 輸入： 17: Prompts, 43: LoadImage, 19: SDXLPromptStylerAll.
- 輸出： 132: PreviewImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-lineartcolorartists-486792fe`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed. CheckpointLoaderSimple uses an opaque hosted numeric ID/display name; resolve an explicit licensed local checkpoint, without guessing.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 088 — `SDXL/【3D】Realtime.json`

Render a 3D viewport image through SDXL Union ControlNet using Load3D. A compatible 3D asset/viewer frontend and its render outputs are needed; this is not a text-only workflow.

- 容器／格式： `json_file` / `visual`. SHA-256: `9175d41bebce402c6a056697a1fb25f8a3299f856935c15abdc97a3a62f868a9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 11 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `EmptyLatentImage`, `KSampler`, `Load3D`, `PreviewImage`, `SetUnionControlNetType`, `VAEDecode`.
- 明確模型檔名： `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`.
- 輸入： 11: CLIPTextEncode, 1: Load3D, 6: CLIPTextEncode.
- 輸出： 13: PreviewImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-3d-realtime-02fe6213`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 089 — `SDXL/【SDXL】GhostmixDetailDaemon.json`

Create GhostXL illustrations with DetailDaemon, a modified text encoder and optional Florence reference captioning. Model/encoder compatibility and SaveImagePlus output behavior need local review.

- 容器／格式： `json_file` / `visual`. SHA-256: `deb0b6fcafbef947eb267169be0732c34717492e87f8289e341e9fb7f552eec4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CFGGuider`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `DetailDaemonSamplerNode`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoader`, `EmptyLatentImage`, `Florence2Run`, `KSamplerSelect`, `LayerUtility: SaveImagePlus`, `LoadImage`, `RandomNoise`, `SDXLPromptStylerbyOriginal`, `SamplerCustomAdvanced`, `VAEDecode`.
- 明確模型檔名： `SD3/clip_g.safetensors`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `ghostxl_v10BakedVAE.safetensors`.
- 輸入： 104: CLIPTextEncode, 105: CLIPTextEncode, 115: SDXLPromptStylerbyOriginal, 123: LoadImage.
- 輸出： 116: LayerUtility: SaveImagePlus.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-sdxl-ghostmixdetaildaemon-ef8920bd`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 090 — `SDXL/【SDXL】MVAdapterPVC.json`

Generate multi-view PVC-style images using a Diffusers MV-Adapter pipeline, Florence/BiRefNet and RIFE video assembly. Requires the MV-Adapter stack and Diffusers model makeup, not merely an SDXL checkpoint.

- 容器／格式： `json_file` / `visual`. SHA-256: `0549fa816f7d0a42bd0f1bff1b1e8b30fd5e0ee4ab69578c1097b327e7c90b55`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BiRefNet`, `DiffusersMVSampler`, `DiffusersModelMakeup`, `DiffusersSchedulerLoader`, `EmptyImage`, `Florence2ModelLoader`, `Florence2Run`, `ImagePreprocessor`, `ImageResize+`, `LayerUtility: ImageBlendAdvance`, `LdmPipelineLoader`, `LdmVaeLoader`, `LoadImage`, `PreviewImage`, `RIFE VFI`, `VHS_VideoCombine`.
- 明確模型檔名： `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`, `fp16sdxl/sdxl_vae.safetensors`, `mvadapter_i2mv_sdxl.safetensors`, `rife47.pth`.
- 輸入： 7: LoadImage.
- 輸出： 10: PreviewImage, 19: VHS_VideoCombine, 21: PreviewImage.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-sdxl-mvadapterpvc-28ac2436`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 091 — `SDXL/【SDXL】NPNNoiseSimple.json`

Compare SDXL Golden Noise/NPNet-adjusted sampling against ordinary noise. Upstream embeds an absolute NPNet weight path; the public sanitizer replaces it with a placeholder, so the shipped copy needs explicit weight-path repair before execution.

- 容器／格式： `json_file` / `visual`. SHA-256: `92e9718819667252e7e6510e7814502b48c52cac726ef03302da2eeef020383b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 15 top-level nodes; 15 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CFGGuider`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSamplerSelect`, `NPNetGoldenNoise`, `PreviewImage`, `RandomNoise`, `SamplerCustomAdvanced`, `VAEDecode`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`, `sdxl.pth`.
- 輸入： 13: CLIPTextEncode, 14: CLIPTextEncode.
- 輸出： 15: PreviewImage, 16: PreviewImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-golden-noise`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-golden-noise.json`; SHA-256 `f206790f3eec6f208a133f57ce93edca7ee300787a92253f923c35139d167abd`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed. Shipped NPNet weight selector is [local path removed]; supply a valid NPNet model path before queueing.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 092 — `SDXL/【SDXL】PVCWorkflowNew.json`

Render a reference in PVC-figure style using AnyLine/MistoLine, IPAdapter, DetailDaemon, PAG and AlignYourSteps. Input 88 supplies structure/reference; WD14 tags need review before generation.

- 容器／格式： `json_file` / `visual`. SHA-256: `d3b9983f5ca6dd8ac1d3a66ae73d1a4c527bca49bef8a4ab4f177aee89c614d9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 26 top-level nodes; 26 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `AlignYourStepsScheduler`, `AnyLineArtPreprocessor_aux`, `CFGGuider`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetLoader`, `DetailDaemonSamplerNode`, `EmptyLatentImage`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImageResize+`, `KSamplerSelect`, `LoadImage`, `PerturbedAttentionGuidance`, `RandomNoise`, `SDXLPromptStyler`, `SamplerCustomAdvanced`, `SaveImage`, `ScaledSoftControlNetWeights`, `String Literal`, `Text List`, `Text List to Text`, `VAEDecode`, `WD14Tagger|pysssss`.
- 明確模型檔名： `PVCStyleModelMovable_beta27Realistic.safetensors`, `mistoLine_rank256.safetensors`.
- 輸入： 75: SDXLPromptStyler, 112: CLIPTextEncode, 113: CLIPTextEncode, 84: String Literal, 88: LoadImage.
- 輸出： 95: SaveImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Advanced-ControlNet; ComfyUI_essentials; ComfyUI-Custom-Scripts; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-sdxl-pvcworkflownew-adc477eb`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-lineart-pvc-style.json`; SHA-256 `2b48b158e0c7e5c000366b1e00245c8655209fce69aa84ef1f5338e6fb972b38`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 093 — `SDXL/【SDXL】PromptlistExample.json`

Generate and label a list of SDXL prompt alternatives with Comfyroll/WAS/FL nodes. Fix random seed and list position for comparisons; output remains preview-only.

- 容器／格式： `json_file` / `visual`. SHA-256: `7114d733136cd3faba5a837d2bba7ff66debe01aef592ac8354c4d1ef55dbf71`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 12 top-level nodes; 12 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CR Prompt List`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `FL_ImageNotes`, `KSampler`, `PreviewImage`, `Random Number`, `ShowText|pysssss`, `Text Multiline`, `VAEDecode`.
- 明確模型檔名： `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 31: CR Prompt List, 27: Text Multiline.
- 輸出： 37: PreviewImage, 34: ShowText|pysssss.
- 相依套件： ComfyUI_Fill-Nodes; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-sdxl-promptlistexample-52ccdad0`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-prompt-list-comparison.json`; SHA-256 `fe888a846a82958e7bdf5a515e4691f77c208817f9468c8a784cea9a735afab9`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 094 — `SDXLrealtime.json`

Redraw live screen/video input with SDXL using depth and line controls, efficient sampling and implicit routing. Capture frontend and permissions are separate dependencies; the public graph does not implement a browser camera form.

- 容器／格式： `json_file` / `visual`. SHA-256: `033e5528b19d8d817762796c17b1fdf66ce2ce3ddb861f22c308302ca0cd0bf4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Anything Everywhere`, `Anything Everywhere3`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `FloatingVideo`, `ImageScale`, `KSampler (Efficient)`, `LineArtPreprocessor`, `MiDaS-DepthMapPreprocessor`, `PreviewImage`, `PrimitiveNode`, `SDXLPromptStyler`, `ScreenShare`, `String Literal`, `Text List`, `Text List to Text`, `VAEEncode`.
- 明確模型檔名： `control-lora-depth-rank256.safetensors`, `dreamshaperXL_lightningDPMSDE.safetensors`, `t2i-adapter-lineart-sdxl-1.0.safetensors`.
- 輸入： 100: ScreenShare, 101: FloatingVideo, 4: CLIPTextEncode, 5: CLIPTextEncode, 134: String Literal, 120: SDXLPromptStyler.
- 輸出： 105: PreviewImage, 118: PreviewImage, 112: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; comfyui_controlnet_aux. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Live screen/camera frontend and matching capture extension.
- 公開目錄／工作流庫 ID： `dseditor-sdxlrealtime-0d7dafd4`; catalog distribution: links-only.
- 公開副本： `workflows/SDXLrealtime.json`; SHA-256 `dad2c1cf6e66b4e68a808c90a8789afacd407404ee39a448182e1e555b516031`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 095 — `SpecialUsage/8BWorld.json`

Compare three model families: AuraFlow, HunyuanDiT and SD3.5. Each has separate conditioning/sampling branches and SaveImage output; shared wording does not certify comparable conditioning or quality.

- 容器／格式： `json_file` / `visual`. SHA-256: `eaedd1a5c76759b712d719cf3c8facd4de44f4ede059ea52950c72b1d2729a38`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 26 top-level nodes; 26 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ConditioningSetTimestepRange`, `ConditioningZeroOut`, `EmptyLatentImage`, `EmptySD3LatentImage`, `KSampler`, `ModelSamplingAuraFlow`, `ModelSamplingSD3`, `SaveImage`, `VAEDecode`.
- 明確模型檔名： `auraflow_0.3_8x8-marduk191.safetensors`, `hunyuan_dit_1.2.safetensors`, `sd3.5_large_fp8_scaled.safetensors`.
- 輸入： 38: CLIPTextEncode, 39: CLIPTextEncode, 6: CLIPTextEncode, 7: CLIPTextEncode, 31: CLIPTextEncode, 32: CLIPTextEncode.
- 輸出： 54: SaveImage, 56: SaveImage, 55: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-8bworld-58f792bd`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 096 — `SpecialUsage/AniPortrait.json`

Animate a portrait from audio or video-extracted audio via AniPortrait. The loader contains machine-specific paths to several AniPortrait weights and requires substantial manual configuration.

- 容器／格式： `json_file` / `visual`. SHA-256: `c9af8bebda9a03be895d98b816e7d8a43157bcc642ca07d309ce51a348d633e4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 8 top-level nodes; 8 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AniPortraitLoader`, `AniPortraitRun`, `FloatConstant`, `LoadImage`, `VHS_LoadAudio`, `VHS_LoadVideo`, `VHS_VideoCombine`, `video2audio`.
- 明確模型檔名： `audio2mesh.pt`, `denoising_unet.pth`, `motion_module.pth`, `pose_guider.pth`, `reference_unet.pth`.
- 輸入： 21: VHS_LoadVideo, 8: VHS_LoadAudio, 4: LoadImage.
- 輸出： 7: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-aniportrait-38ff5d3e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 097 — `SpecialUsage/LLMComparetools.json`

Compare multiple Ollama-generated texts by rendering them into labeled image panels. This is a local LLM/compositing tool without an image diffusion checkpoint; configured Ollama models are required.

- 容器／格式： `json_file` / `visual`. SHA-256: `bc66ac10f6c20abc955d9d195d32a3bf5016a067f734e550c747ac1a6a8bd112`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `EmptyImage`, `FL_ImageNotes`, `ImageConcatMulti`, `LayerStyle: DropShadow`, `LayerUtility: SimpleTextImage`, `OllamaGenerate`, `PreviewImage`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`, `WidgetToString`.
- 明確模型檔名： none observed; inspect model selectors, implicit downloads and provider models.
- 輸入： 11: LayerUtility: SimpleTextImage, 9: LayerUtility: SimpleTextImage, 30: LayerUtility: SimpleTextImage, 21: String Literal.
- 輸出： 3: ShowText|pysssss, 12: PreviewImage, 4: ShowText|pysssss, 6: ShowText|pysssss, 10: PreviewImage, 8: ShowText|pysssss, 18: PreviewImage, 14: PreviewImage, 36: PreviewImage.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_Fill-Nodes; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-llmcomparetools-c40dad4c`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 098 — `SpecialUsage/LLMsummary.json`

Summarize a document with Gemini/Ollama and illustrate/layout its pieces using SDXL and comic/text panels. Requires document input, explicit LLM/provider setup and review of generated text.

- 容器／格式： `json_file` / `visual`. SHA-256: `3ea484ed3172edb8410f39133800ed1eaf18d2fe375da721f5b0da534d40a5d4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 26 top-level nodes; 26 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CR Comic Panel Templates`, `CR Text Concatenate`, `CheckpointLoaderSimple`, `DocumentLoader`, `EmptyLatentImage`, `Fast Groups Bypasser (rgthree)`, `Gemini_API_Zho`, `ImageConcatMulti`, `ImageListToImageBatch`, `KSampler (Efficient)`, `LayerStyle: DropShadow`, `LayerUtility: ColorImage V2`, `LayerUtility: SimpleTextImage`, `OllamaGenerate`, `PreviewImage`, `SaveImage`, `String Literal`, `Text List`, `Text List to Text`, `TextSplitByDelimiter`.
- 明確模型檔名： `SDXL/mklanRealistic_mklan230realHyperSD.safetensors`.
- 輸入： 74: CLIPTextEncode, 116: String Literal, 104: String Literal, 120: DocumentLoader, 43: String Literal, 90: LayerUtility: SimpleTextImage, 75: CLIPTextEncode.
- 輸出： 82: PreviewImage, 86: PreviewImage, 127: PreviewImage, 108: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_LayerStyle and applicable advanced extensions; rgthree-comfy; ComfyUI_Comfyroll_CustomNodes; Ollama custom nodes plus explicitly configured Ollama service; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly; Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-llmsummary-8d11c27e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 099 — `SpecialUsage/Oldphotofix.json`

Restore old portraits using line controls, PuLID identity, face segmentation and mask detailing. Requires PuLID EVA/InsightFace and detector assets; image resemblance still requires visual verification.

- 容器／格式： `json_file` / `visual`. SHA-256: `d9328614daf36ee2a0a94018f4506789a5f01e1bb7a77bb87909e7dbc22ed4f5`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 28 top-level nodes; 28 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `AnyLinePreprocessor`, `ApplyPulid`, `CLIPTextEncode`, `CR Text Concatenate`, `ControlNetLoader`, `Efficient Loader`, `GetImageSize+`, `InvertMask`, `JWImageResizeByLongerSide`, `KSamplerAdvanced`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask`, `LoadImage`, `MaskDetailerPipe`, `PreviewImage`, `PulidEvaClipLoader`, `PulidInsightFaceLoader`, `PulidModelLoader`, `SDXLPromptStyler`, `SelectNthMask //Inspire`, `ToBasicPipe`, `VAEDecode`.
- 明確模型檔名： `SDXL/CN-anytest_v4-marged.safetensors`, `face_yolov8m.pt`, `ip-adapter_pulid_sdxl_fp16.safetensors`, `mistoLine_rank256.safetensors`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： 21: CLIPTextEncode, 35: SDXLPromptStyler, 8: LoadImage.
- 輸出： 64: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI-Advanced-ControlNet; ComfyUI_essentials; ComfyUI_Comfyroll_CustomNodes; PuLID/PuLID-Flux and matching InsightFace/EVA dependencies. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-oldphotofix-e4bf9cf2`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 100 — `SpecialUsage/animevideoupscale.json`

Upscale an uploaded anime video with APISR in batches and reassemble it using VideoHelperSuite. No diffusion sampler is involved; APISR weights and video encoding support are essential.

- 容器／格式： `json_file` / `visual`. SHA-256: `da0e9e1c88109060f33cc78278794f9710d3af5e2c0db4d2d76365e9b5402250`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 6 top-level nodes; 6 active expanded nodes. Top-level check: **pass**.
- 節點類型： `APISR_Lterative_Zho`, `APISR_ModelLoader_Zho`, `SplitImageWithAlpha`, `VHS_BatchManager`, `VHS_LoadVideo`, `VHS_VideoCombine`.
- 明確模型檔名： `4x_APISR_GRL_GAN_generator.pth`.
- 輸入： 14: VHS_LoadVideo.
- 輸出： 12: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-animevideoupscale-f6c22f9e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 101 — `SpecialUsage/bnktiledver.json`

Refine images with BNK tiled SD1.5 sampling under multiple controls, then enhance through SUPIR. Requires mixed model families and the specific SUPIR model, with preview output comparisons.

- 容器／格式： `json_file` / `visual`. SHA-256: `369206bcdb88589f194f33653214e96840438658d27fb90c6515c68cf003edd3`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 27 top-level nodes; 27 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BNK_TiledKSampler`, `CLIPTextEncode`, `CR Apply Multi-ControlNet`, `CR Multi-ControlNet Stack`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `GetImageSize`, `HEDPreprocessor`, `JWImageResizeByLongerSide`, `LoadImage`, `PreviewImage`, `SUPIR_conditioner`, `SUPIR_decode`, `SUPIR_encode`, `SUPIR_first_stage`, `SUPIR_model_loader_v2`, `SUPIR_sample`, `SetNode`, `ShowText|pysssss`, `String Literal`, `Text Find and Replace`, `Text Random Line`, `VAEDecode`.
- 明確模型檔名： `9527DetailRealistic_v40Lighting4S.safetensors`, `SUPIR/SUPIR-v0Q.ckpt`, `control_v11e_sd15_ip2p_fp16.safetensors`, `control_v11f1e_sd15_tile.pth`, `control_v11p_sd15_softedge_fp16.safetensors`, `dreamshaper_8.safetensors`.
- 輸入： 20: String Literal, 18: String Literal, 6: CLIPTextEncode, 7: CLIPTextEncode, 12: LoadImage.
- 輸出： 21: ShowText|pysssss, 49: PreviewImage, 60: PreviewImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI-KJNodes / related version-specific extensions; ComfyUI-SUPIR; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-bnktiledver-b56b54e7`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 102 — `SpecialUsage/characterdifferentpose.json`

Generate a character in several poses via IPAdapterBatch, OpenPose, StyleAligned, prompt maker and face detailing. A dangling reference exists; IF_PromptMkr backend and interactive Preview Chooser add runtime dependencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `76e069c389bb00f7c6f02cebe6c00b7e5894804522b8f90e8e1cc597538c50d2`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 36 top-level nodes; 36 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `Anything Everywhere3`, `CLIPTextEncode`, `CR Text Concatenate`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `DiffControlNetLoader`, `Efficient Loader`, `EmptyLatentImage`, `FaceDetailer`, `Fast Groups Bypasser (rgthree)`, `GetImageSizeAndCount`, `HighRes-Fix Script`, `IF_PromptMkr`, `IPAdapterBatch`, `IPAdapterUnifiedLoader`, `ImageFromBatch`, `ImpactMakeImageBatch`, `ImpactSwitch`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `Note`, `Preview Chooser`, `PreviewImage`, `SEGSOrderedFilterDetailerHookProvider`, `Seed Everywhere`, `Simple String`, `StyleAlignedBatchAlign_`, `UltralyticsDetectorProvider`.
- 明確模型檔名： `4x-UltraSharp.pth`, `bbox/face_yolov8m.pt`, `control_v11f1e_sd15_tile_fp16.safetensors`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `henmixReal_v6b.safetensors`, `hypersd/Hyper-SD15-12steps-CFG-lora.safetensors`, `vae-ft-mse-840000-ema-pruned.safetensors`, `yolox_l.onnx`.
- 輸入： 238: CLIPTextEncode, 48: LoadImage, 223: LoadImage, 225: LoadImage, 224: LoadImage, 281: IF_PromptMkr.
- 輸出： 245: PreviewImage, 255: Preview Chooser.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; rgthree-comfy; ComfyUI_Comfyroll_CustomNodes. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： IF prompt maker service/model configuration; inspect selected backend.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-characterdifferentpose-1676e7a9`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 103 — `SpecialUsage/kcolors.json`

Generate using merged Kolors/SDXL models, ChatGLM, automatic CFG and translation. Explicit ChatGLM/Kolors weights and the translation backend are required.

- 容器／格式： `json_file` / `visual`. SHA-256: `e70c5b1eb17a8d7a568aae189fa8fae2a697da546c8c3bbdcb81d107e6d591b3`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 14 top-level nodes; 14 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Automatic CFG - Preset Loader`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `GoogleTranslateTextNode`, `KSampler //Inspire`, `MZ_ChatGLM3`, `MZ_ChatGLM3Loader`, `MZ_KolorsUNETLoader`, `ModelMergeSimple`, `SaveImage`, `ShowText|pysssss`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `SDXL/mklanRealistic_mklan230realHyperSD.safetensors`, `chatglm3-4bit.safetensors`, `kcolors/diffusion_pytorch_model.fp16.safetensors`, `sdxl_vae.safetensors`.
- 輸入： no recognized sink/input; inspect custom-node contract.
- 輸出： 130: SaveImage, 136: ShowText|pysssss.
- 相依套件： ComfyUI-Inspire-Pack; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Google translation service/custom-node backend; network behavior and provider configuration must be checked.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-kcolors-6aefeee5`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 104 — `SpecialUsage/oldmanpic.json`

Generate a DreamShaper XL portrait and compose it with text and drop shadow. This small poster/card pipeline ends in a preview, not a saved image contract.

- 容器／格式： `json_file` / `visual`. SHA-256: `6663646ee3e3951470a7a32819fbacc49fca2303eab7f33b67b4ad3aa220e0bb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 6 top-level nodes; 6 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Efficient Loader`, `KSampler Adv. (Efficient)`, `LayerStyle: DropShadow`, `LayerUtility: TextImage`, `PreviewImage`, `SDXLPromptStyler`.
- 明確模型檔名： `dreamshaperXL_v2TurboDpmppSDE.safetensors`.
- 輸入： 75: SDXLPromptStyler.
- 輸出： 94: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-oldmanpic-13435813`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 105 — `SpecialUsage/poster.json`

Generate a poster with SD3, styled/translated prompts and optional Ollama/Gemini assistance. Model and service configuration are distinct; generated poster typography is not verified.

- 容器／格式： `json_file` / `visual`. SHA-256: `60de1a6ce273609c7b4e341f2ebe2496a3b6efcb9973571c195120f582af61b2`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 16 top-level nodes; 15 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptySD3LatentImage`, `Gemini_API_Zho`, `GoogleTranslateTextNode`, `ImpactSwitch`, `KSampler (Efficient)`, `Note`, `OllamaGenerate`, `SDXLPromptStyler`, `SaveImage`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`.
- 明確模型檔名： `SD3/sd3_medium_incl_clips_t5xxlfp8.safetensors`.
- 輸入： 7: CLIPTextEncode, 14: CLIPTextEncode, 19: String Literal, 45: String Literal, 15: SDXLPromptStyler.
- 輸出： 16: SaveImage, 11: ShowText|pysssss.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly; Google translation service/custom-node backend; network behavior and provider configuration must be checked; Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-poster-5f050ad1`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 106 — `SpecialUsage/sampler.json`

Compare SDXL sampling with shared seed and PerturbedAttentionGuidance branches. This is a parameter/sampler experiment with preview outputs, not a distinct app generation feature.

- 容器／格式： `json_file` / `visual`. SHA-256: `7e3515764bb7993e48a70f2c4815d9d0c7b3f5003af92594abff6eba909940ba`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `Fast Groups Bypasser (rgthree)`, `GlobalSeed //Inspire`, `KSampler //Inspire`, `PerturbedAttentionGuidance`, `PreviewImage`, `VAEDecode`.
- 明確模型檔名： `xxmix9realisticsdxl_v10.safetensors`.
- 輸入： 7: CLIPTextEncode, 6: CLIPTextEncode.
- 輸出： 24: PreviewImage, 11: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-sampler-391ec559`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 107 — `SpecialUsage/stylephoto.json`

Transfer style/lighting between subject and background using IPAdapter precise style transfer, SDXL, IC-Light and mask detailing. The graph has reciprocal-link inconsistencies and several mask/composite dependencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `93cc7857bab8997d4d3ad85d350af7e973c4035fefe475653026eb0b28727406`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 45 top-level nodes; 45 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `ACN_AdvancedControlNetApply`, `CLIPTextEncode`, `ConditioningCombine`, `ControlNetLoader`, `DownloadAndLoadFlorence2Model`, `Efficient Loader`, `Fast Groups Bypasser (rgthree)`, `Florence2Run`, `ICLightAppply`, `IPAdapterPreciseStyleTransfer`, `IPAdapterUnifiedLoader`, `ImageBatch`, `ImpactSwitch`, `InvertMask`, `JWImageResizeByFactor`, `JWMaskResize`, `JoinImageWithAlpha`, `KSamplerAdvanced //Inspire`, `LayerFilter: HDREffects`, `LayerMask: MaskEdgeUltraDetail`, `LayerMask: PersonMaskUltra V2`, `LayerUtility: GetImageSize`, `LayerUtility: ImageBlendAdvance`, `LoadImage`, `MaskDetailerPipe`, `Note`, `PreviewImage`, `PreviewMask_`, `SDXLPromptStyler`, `SetNode`, `SolidMask`, `ToBasicPipe`, `UNETLoader`, `VAEDecode`, `VAEEncodeArgMax`, `ZeST: Grayout Subject`.
- 明確模型檔名： `SDXL/CN-anytest_v4-marged.safetensors`, `iclight_sd15_fbc_unet_ldm.safetensors`, `realisticVisionV60B1_v51VAE.safetensors`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： 259: CLIPTextEncode, 121: SDXLPromptStyler, 258: CLIPTextEncode, 20: LoadImage, 153: LoadImage.
- 輸出： 178: PreviewImage, 208: PreviewImage, 238: PreviewMask_.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Advanced-ControlNet; ComfyUI-KJNodes / related version-specific extensions; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-specialusage-stylephoto-922d278e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 108 — `Style/sdxl_styles_gpt.json`

419-entry prompt-style dictionary, not a ComfyUI graph or executable workflow.

- 容器／格式： `json_file` / `not_workflow`. SHA-256: `5ee3fa8ec7d92e3cb5e288b8dc34af366e75892966d26e53afb2ac19c7480d99`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 狀態： Not executable workflow JSON.

### 109 — `Style/sdxl_styles_sai_tw.json`

17-entry translated SAI style dictionary, not a ComfyUI graph or executable workflow.

- 容器／格式： `json_file` / `not_workflow`. SHA-256: `4722f5caaa3af44120665b8e2076abdaef6be801aa545e136ee05f222417201e`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 狀態： Not executable workflow JSON.

### 110 — `Style/sdxl_styles_twri_tw.json`

89-entry translated TWRI style dictionary, not a ComfyUI graph or executable workflow.

- 容器／格式： `json_file` / `not_workflow`. SHA-256: `82e720b557c0ce5b541dcac7cbd062baf4b80d1dff92df092494af46fd1a9a29`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 狀態： Not executable workflow JSON.

### 111 — `Style/sdxlbasewlittleinies.json`

Generate stylized SDXL images with Little Tinies LoRA, Superprompt and PAG/shared seed. Requires the LoRA and prompt expansion node; output is preview-only.

- 容器／格式： `json_file` / `visual`. SHA-256: `535dfd390f1bdb3853cc00c0387c6787f4ff03ec77d8b86e5319d751d19e10b6`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 14 top-level nodes; 14 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `Fast Groups Bypasser (rgthree)`, `GlobalSeed //Inspire`, `KSampler //Inspire`, `LoraLoaderModelOnly`, `PerturbedAttentionGuidance`, `PreviewImage`, `SDXLPromptStyler`, `String Literal`, `Superprompt`, `VAEDecode`.
- 明確模型檔名： `SDXL/Little_Tinies.safetensors`, `sd_xl_base_1.0.safetensors`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 27: Superprompt, 29: SDXLPromptStyler, 28: String Literal.
- 輸出： 11: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-style-sdxlbasewlittleinies-1d07a38b`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 112 — `Tiled/【tiled】SEGtiledupscaler.json`

Upscale a source with UltraSharp and refine it tile-by-tile through Impact SEGS using SDXL Lightning, with optional face/identity handling. README timing on an RTX4070 is an upstream claim, not an audited run or host benchmark.

- 容器／格式： `json_file` / `visual`. SHA-256: `d2ce4898f8082d592d1f669c39d9e1acbd740cac9897d6439d9ef6c8a1918509`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 29 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `CoreMLDetailerHookProvider`, `DetailerForEachPipe`, `IPAdapter`, `IPAdapterModelHelper //Inspire`, `IPAdapterUnifiedLoaderFaceID`, `ImageUpscaleWithModel`, `ImpactIPAdapterApplySEGS`, `ImpactMakeTileSEGS`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByLongerSide`, `LayerColor: ColorAdapter`, `LoadImage`, `PreviewImage`, `SAMLoader`, `SDXLPromptStyler`, `SegsToCombinedMask`, `SetNode`, `Text List`, `Text List to Text`, `ToBasicPipe`, `UltralyticsDetectorProvider`, `Upscale Model Loader`, `WD14Tagger|pysssss`.
- 明確模型檔名： `4x-UltraSharp.pth`, `bbox/face_yolov8m.pt`, `dreamshaperXL_lightningDPMSDE.safetensors`, `sam_vit_b_01ec64.pth`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 11: SDXLPromptStyler, 41: LoadImage.
- 輸出： 36: PreviewImage [mode 4], 82: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-KJNodes / related version-specific extensions; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-tiled-tiled-segtiledupscaler-2dee52fb`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 113 — `VLM.json`

Caption/interpret an image with local LLaVA/GGUF and synthesize audio through AudioLDM2 with a PlayMusic sink. Vision projector and LLM weights are explicit; audio model assets may be implicit.

- 容器／格式： `json_file` / `visual`. SHA-256: `d0494630db4080cb806aea6aa7bb63a0c47a9abd63b067e6952810c4072317d9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 12 top-level nodes; 12 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AudioLDM2Node`, `LLMSampler`, `LLava Loader Simple`, `LLavaSamplerSimple`, `LlavaClipLoader`, `LoadImage`, `PlayMusic`, `ShowText|pysssss`, `SimpleText`.
- 明確模型檔名： `llava-1.6-mistral-7b-gguf/llava-v1.6-mistral-7b.Q4_K_M.gguf`, `llava-1.6-mistral-7b-gguf/mmproj-model-f16.gguf`.
- 輸入： 32: SimpleText, 38: SimpleText, 33: LoadImage, 35: SimpleText.
- 輸出： 43: PlayMusic, 50: ShowText|pysssss, 51: ShowText|pysssss.
- 相依套件： ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-vlm-359a10bd`; catalog distribution: links-only.
- 公開副本： `workflows/VLM.json`; SHA-256 `f7b69e1ed808a8f2e214dbb3d6252cb8d353b19a3d842937e2760cda62c7f6ea`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 114 — `Video/WAN/ShortMV.json`

Create a short music video by generating Flux key images, ACE-Step audio and Wan VACE video, with Gemini text assistance. This is a multi-model multimodal pipeline with explicit provider setup and no public app runner mapping.

- 容器／格式： `json_file` / `visual`. SHA-256: `0f726eda64c842aa40b9a56b2777b14a4ca5212c547202f8ceb2ea3266398993`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 40 top-level nodes; 40 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConditioningZeroOut`, `DualCLIPLoader`, `EmptyAceStepLatentAudio`, `GeminiAPI`, `ImageListToImageBatch`, `ImageResize+`, `KSampler`, `LayerUtility: PurgeVRAM`, `LoadWanVideoT5TextEncoder`, `LoraLoaderModelOnly`, `ModelSamplingSD3`, `Note`, `SDXLEmptyLatentSizePicker+`, `StringFunction|pysssss`, `Text Multiline`, `TextEncodeAceStepAudio`, `TextSplit`, `TextSplitByDelimiter`, `UNETLoader`, `VAEDecode`, `VAEDecodeAudio`, `VAELoader`, `VHS_VideoCombine`, `WanVideoDecode`, `WanVideoLoraSelect`, `WanVideoModelLoader`, `WanVideoSampler`, `WanVideoTextEncode`, `WanVideoVACEEncode`, `WanVideoVACEModelSelect`, `WanVideoVACEStartToEndFrame`, `WanVideoVAELoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1DevEdgeOfReality_v30.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `Wan/Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank32.safetensors`, `Wan2_1-T2V-1_3B_fp8_e4m3fn.safetensors`, `Wan2_1-VACE_module_1_3B_bf16.safetensors`, `ace_step_v1_3.5b.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`, `umt5-xxl-enc-fp8_e4m3fn.safetensors`, `wan_2.1_vae.safetensors`.
- 輸入： 19: WanVideoTextEncode, 8: CLIPTextEncode, 20: LoadWanVideoT5TextEncoder, 5: Text Multiline, 58: TextEncodeAceStepAudio.
- 輸出： 30: VHS_VideoCombine.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; ComfyUI-WanVideoWrapper or native Wan variant named in node inventory; ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-WanVideoWrapper`, `chibi`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-impact-pack`, `comfyui-ollamagemini`, `comfyui-videohelpersuite`, `comfyui_essentials`, `comfyui_layerstyle`, `pr-was-node-suite-comfyui-47064894`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-video-wan-shortmv-3f9f44ae`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 115 — `Video/WAN/【Wan】FantasyTalkingEnglish.json`

Create English talking video with FantasyTalking/Wav2Vec, IndexTTS, Wan, optional Uni3C control, looped segments and subtitles. Needs voice/audio/image inputs, many model stacks, and explicit Gemini configuration.

- 容器／格式： `json_file` / `visual`. SHA-256: `7814e80e620bdc0f3fa05442a402b54411bfef112ba4140a33494593e13cfe39`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 50 top-level nodes; 50 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AddSubtitlesToVideo`, `AudioListGenerator`, `CLIPVisionLoader`, `CreateCFGScheduleFloatList`, `DisplayAny`, `DownloadAndLoadWav2VecModel`, `FantasyTalkingModelLoader`, `FantasyTalkingWav2VecEmbeds`, `Fast Groups Bypasser (rgthree)`, `GeminiAPI`, `GetImageSize+`, `INTConstant`, `ImageFromBatch`, `ImageResizeKJv2`, `ImageUpscaleWithModel`, `IndexTTSRun`, `LayerUtility: PurgeVRAM`, `LoadAudio`, `LoadImage`, `LoadWanVideoT5TextEncoder`, `RepeatImageBatch`, `SaveAudio`, `SetNode`, `ShowText|pysssss`, `StringFunction|pysssss`, `TextBox1`, `UpscaleModelLoader`, `VHS_LoadVideoPath`, `VHS_SelectFilename`, `VHS_VideoCombine`, `VHS_VideoInfoSource`, `WanVideoBlockSwap`, `WanVideoClipVisionEncode`, `WanVideoDecode`, `WanVideoEncode`, `WanVideoImageToVideoEncode`, `WanVideoLoraSelect`, `WanVideoModelLoader`, `WanVideoSampler`, `WanVideoTextEncode`, `WanVideoUni3C_ControlnetLoader`, `WanVideoUni3C_embeds`, `WanVideoVAELoader`, `easy batchAnything`, `easy float`, `easy forLoopEnd`, `easy forLoopStart`, `easy indexAnything`.
- 明確模型檔名： `RealESRGAN_x2.pth`, `Wan/Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors`, `Wan21_Uni3C_controlnet_fp16.safetensors`, `Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors`, `Wan2_1_VAE_bf16.safetensors`, `clip_vision_h.safetensors`, `fantasytalking_fp16.safetensors`, `umt5-xxl-enc-fp8_e4m3fn.safetensors`.
- 輸入： 11: LoadWanVideoT5TextEncoder, 16: WanVideoTextEncode, 58: LoadImage, 147: VHS_LoadVideoPath, 138: LoadAudio.
- 輸出： 131: DisplayAny, 30: VHS_VideoCombine, 140: SaveAudio, 151: VHS_VideoCombine, 162: ShowText|pysssss.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_essentials; rgthree-comfy; ComfyUI-WanVideoWrapper or native Wan variant named in node inventory; ComfyUI-Easy-Use; ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-WanVideoWrapper`, `Listhelper`, `RES4LYF`, `audiotools`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-easy-use`, `comfyui-kjnodes`, `comfyui-ollamagemini`, `comfyui-videohelpersuite`, `comfyui_essentials`, `comfyui_layerstyle`, `indextts-mw`, `kijai/ComfyUI-WanVideoWrapper`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： `dseditor-video-wan-wan-fantasytalkingenglish-1d470e19`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 116 — `WanVIdeo/SVI2WanT820260101.json`

Extend Wan2.2 image-to-video over many SVI Pro segments using high/low-noise models, prompt lists and overlapping frame batches. The 139-node graph includes model downloads and repeated saves; context/state continuity needs execution-level validation.

- 容器／格式： `json_file` / `visual`. SHA-256: `3aced07321cfd8dcd21ce32c1d6206ce18df4e39d4f1a312b574c21866bcb8f9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 139 top-level nodes; 128 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `CR Prompt Text`, `DiffusionModelLoaderKJ`, `GetNode`, `INTConstant`, `ImageBatchExtendWithOverlap`, `ImageResizeKJv2`, `KSamplerAdvanced`, `LoadImage`, `LoraLoaderModelOnly`, `ModelDownloader`, `Note`, `PreviewAny`, `PreviewImage`, `SetNode`, `VAEDecode`, `VAEEncode`, `VAELoader`, `VHS_VideoCombine`, `WanImageToVideoSVIPro`.
- 明確模型檔名： `Lightx2v wan 2.2 high_noise_seko v1.1.safetensors`, `SVI_v2_PRO_Wan2.2-I2V-A14B_HIGH_lora_rank_128_fp16.safetensors`, `SVI_v2_PRO_Wan2.2-I2V-A14B_LOW_lora_rank_128_fp16.safetensors`, `Wan/lightx2v_I2V_14B_480p_cfg_step_distill_rank256_bf16.safetensors`, `umt5_xxl_fp16.safetensors`, `wan2.2_i2v_A14b_high_noise_scaled_fp8_e4m3_lightx2v_4step_comfyui_1030.safetensors`, `wan2.2_i2v_A14b_low_noise_scaled_fp8_e4m3_lightx2v_4step_comfyui.safetensors`, `wan_2.1_vae.safetensors`.
- 輸入： 157: CLIPTextEncode, 279: CLIPTextEncode, 306: CLIPTextEncode, 293: CLIPTextEncode, 324: CLIPTextEncode, 89: CLIPTextEncode, 93: CLIPTextEncode, 152: CLIPTextEncode, 284: CLIPTextEncode, 297: CLIPTextEncode, 310: CLIPTextEncode, 333: CLIPTextEncode, 97: LoadImage, 366: CR Prompt Text, 367: CR Prompt Text, 368: CR Prompt Text, 369: CR Prompt Text, 370: CR Prompt Text, 371: CR Prompt Text.
- 輸出： 335: VHS_VideoCombine, 167: VHS_VideoCombine, 320: VHS_VideoCombine, 334: VHS_VideoCombine, 319: VHS_VideoCombine, 309: VHS_VideoCombine, 322: VHS_VideoCombine, 296: VHS_VideoCombine, 321: VHS_VideoCombine, 354: PreviewImage [mode 4], 356: PreviewImage [mode 4], 357: PreviewImage [mode 4], 363: PreviewImage [mode 4], 361: PreviewImage [mode 4], 360: PreviewImage [mode 4], 359: PreviewImage [mode 4], 358: PreviewImage [mode 4], 355: PreviewImage [mode 4], 362: PreviewImage [mode 4], 126: VHS_VideoCombine, 283: VHS_VideoCombine, 382: PreviewAny.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_Comfyroll_CustomNodes. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `Listhelper`, `comfy-core`, `comfyroll`, `comfyui-kjnodes`, `comfyui-videohelpersuite`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-wanvideo-svi2want820260101-57a4e79e`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 117 — `WanVIdeo/SimpleMultiTalk.json`

Create multi-speaker talking video from image and separate audio inputs using MultiTalk/Wav2Vec/IndexTTS and WanVideo. Speaker masks, audio duration/frame counts and clip merging are part of the runtime contract.

- 容器／格式： `json_file` / `visual`. SHA-256: `25f06c3a4e53c28a225a5f840569388cb8039044904c6328c7da361657b5f295`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 36 top-level nodes; 36 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AudioToFrameCount`, `CLIPVisionLoader`, `DisplayAny`, `DownloadAndLoadWav2VecModel`, `GetImageSize+`, `ImageResizeKJv2`, `IndexTTSRun`, `LoadAudioMW`, `LoadImage`, `LoadWanVideoT5TextEncoder`, `MergeVideoFilename`, `MultiTalkModelLoader`, `MultiTalkWav2VecEmbeds`, `Note`, `PromptListGenerator`, `SeparateMasks`, `ShowText|pysssss`, `SimpleMath+`, `SimpleMathDual+`, `TextBox1`, `VHS_VideoCombine`, `WanVideoBlockSwap`, `WanVideoClipVisionEncode`, `WanVideoDecode`, `WanVideoImageToVideoMultiTalk`, `WanVideoLoraSelect`, `WanVideoModelLoader`, `WanVideoSampler`, `WanVideoTextEncode`, `WanVideoVAELoader`.
- 明確模型檔名： `Wan/Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors`, `Wan2_1-I2V-14B-480P_fp8_e4m3fn.safetensors`, `Wan2_1_VAE_bf16.safetensors`, `WanVideo_2_1_Multitalk_14B_fp8_e4m3fn.safetensors`, `clip_vision_h.safetensors`, `umt5-xxl-enc-bf16.safetensors`.
- 輸入： 209: LoadAudioMW, 210: PromptListGenerator, 212: PromptListGenerator, 208: LoadAudioMW, 136: LoadWanVideoT5TextEncoder, 135: WanVideoTextEncode, 133: LoadImage.
- 輸出： 131: VHS_VideoCombine, 221: DisplayAny, 238: ShowText|pysssss.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_essentials; ComfyUI-WanVideoWrapper or native Wan variant named in node inventory; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-WanVideoWrapper`, `Listhelper`, `RES4LYF`, `audiotools`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-kjnodes`, `comfyui-videohelpersuite`, `comfyui_essentials`, `indextts-mw`, `kijai/ComfyUI-KJNodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-wanvideo-simplemultitalk-3eada222`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 118 — `Zimage/【Zimage】QwenLLM.json`

Expand a prompt using local Qwen GPU inference, then generate with Z-image Turbo. QwenGPUInference may need its own LLM assets beyond the Qwen text encoder filename.

- 容器／格式： `json_file` / `visual`. SHA-256: `48346553e4abff93f470979112415fb1ca298404bde513273070314f79bb7ed9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 10 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `EmptySD3LatentImage`, `KSampler`, `QwenGPUInference`, `SaveImage`, `UNETLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `ae.safetensors`, `qwen_3_4b.safetensors`, `z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `Listhelper`, `comfy-core`, `dseditor/ComfyUI-ListHelper`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-zimage-zimage-qwenllm-e7f90bd7`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 119 — `Zimage/【Zimage】SimpleTTPI2I.json`

Refine an uploaded image using Z-image tiled sampling and assembly after model upscaling. TTP tile overlap/seams, SageAttention and Qwen/AE compatibility remain untested.

- 容器／格式： `json_file` / `visual`. SHA-256: `781c4c0cecb4f0723b05c2f7205cfd5e61c21728fa29ed8e6c8005136f6b6f97`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 19 top-level nodes; 15 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `ConditioningZeroOut`, `ImageResizeKJv2`, `ImageUpscaleWithModel`, `KSampler`, `LoadImage`, `ModelPatchTorchSettings`, `PathchSageAttentionKJ`, `SaveImage`, `TTP_Image_Assy`, `TTP_Image_Tile_Batch`, `TTP_Tile_image_size`, `UNETLoader`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xRealWebPhoto_v4_dat2.pth`, `ae.safetensors`, `qwen_3_4b.safetensors`, `z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors`.
- 輸入： 21: CLIPTextEncode, 90: LoadImage.
- 輸出： 86: SaveImage.
- 相依套件： ComfyUI-KJNodes / related version-specific extensions; ComfyUI TTP Toolset. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-kjnodes`, `comfyui_ttp_toolset`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-zimage-tiled-image-to-image`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/zimage-tiled-image-to-image.json`; SHA-256 `2df66af53208acae4ba980768e14cca91034880dee378b9bb900b1c0c38821e2`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 120 — `Zimage/【Zimage】SimpleTTPT2I.json`

Generate with Z-image Turbo, then run model upscale and tile refinement/assembly. The initial text-to-image branch and tile branch use the same family but distinct sampling stages.

- 容器／格式： `json_file` / `visual`. SHA-256: `580a08c0be2d2d26ac021c2c2574bdf22e887ff01bc7b7c0cdb35bd35f8b28c4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 22 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `ConditioningZeroOut`, `EmptySD3LatentImage`, `ImageResizeKJv2`, `ImageUpscaleWithModel`, `KSampler`, `ModelPatchTorchSettings`, `PathchSageAttentionKJ`, `SaveImage`, `TTP_Image_Assy`, `TTP_Image_Tile_Batch`, `TTP_Tile_image_size`, `UNETLoader`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xRealWebPhoto_v4_dat2.pth`, `ae.safetensors`, `qwen_3_4b.safetensors`, `z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors`.
- 輸入： 21: CLIPTextEncode, 80: CLIPTextEncode.
- 輸出： 86: SaveImage.
- 相依套件： ComfyUI-KJNodes / related version-specific extensions; ComfyUI TTP Toolset. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-kjnodes`, `comfyui_ttp_toolset`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-zimage-tiled-text-to-image`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/zimage-tiled-text-to-image.json`; SHA-256 `5349f435c3756e877f9a7dc19efe8a265a122ad2b757b3b3a8d9b283c474def7`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 121 — `charmake.json`

Build character images with face references, tiled IPAdapter, pose controls, StyleAligned and background blending. Load Image Batch is a directory-style input requiring manual setup; README is openly critical of consistency.

- 容器／格式： `json_file` / `visual`. SHA-256: `65639306800b2483b37d0b5c89aaaa940096578f04b731eae2eaf1a317ed56ed`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 30 top-level nodes; 30 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CR Multi-ControlNet Stack`, `DWPreprocessor`, `Efficient Loader`, `GetImageSize`, `IPAdapterApplyFaceID`, `IPAdapterModelHelper //Inspire`, `IPAdapterTilesMasked`, `ImageCrop`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: RemBgUltra`, `LayerUtility: ImageBlend`, `Load Image Batch`, `LoadImage`, `Note`, `PrepImageForInsightFace`, `PreviewImage`, `RescaleCFG`, `SaveImage`, `StyleAlignedBatchAlign`, `Text List`, `Text List to Text`, `WD14Tagger|pysssss`.
- 明確模型檔名： `add_detail.safetensors`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `ghostmix_v20Bakedvae.safetensors`, `yolox_l.onnx`.
- 輸入： 75: LoadImage, 10: LoadImage, 83: LoadImage, 65: LoadImage, 82: Load Image Batch.
- 輸出： 72: PreviewImage, 41: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-charmake-f8e6a50e`; catalog distribution: links-only.
- 公開副本： `workflows/charmake.json`; SHA-256 `4d681667a1b562ea721232b2df3bd1bb8fc7e18cae7676ab51e3d7b7578effe0`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 122 — `clotheschangertiled.json`

Transfer clothing with tiled masked IPAdapter and OpenPose, then apply a grouped FaceID face detailer and high-resolution fix. Legacy face-detail group requires migration; InsightFace/SAM/YOLO weights are dependencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `3a64fc1edd3e044f7027fe628a1daf9d5f2310e668366fd0476953b54099e587`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 23 top-level nodes; 25 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPVisionLoader`, `CR Multi-ControlNet Stack`, `DWPreprocessor`, `Efficient Loader`, `FaceDetailer`, `FreeU_V2`, `GetImageSize`, `HighRes-Fix Script`, `IPAdapterApplyFaceID`, `IPAdapterModelLoader`, `IPAdapterTilesMasked`, `InsightFaceLoader`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `LoraLoader`, `PrepImageForInsightFace`, `PreviewImage`, `RescaleCFG`, `SAMLoader`, `SaveImage`, `UltralyticsDetectorProvider`, `workflow/FaceDetailerEX`.
- 明確模型檔名： `4x-UltraSharp.pth`, `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `add_detail.safetensors`, `bbox/face_yolov8m.pt`, `control_lora_rank128_v11f1e_sd15_tile_fp16.safetensors`, `control_v11p_sd15_openpose.pth`, `dreamshaper_8.safetensors`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `ip-adapter-faceid-plusv2_sd15.bin`, `ip-adapter-faceid-plusv2_sd15_lora.safetensors`, `ip-adapter-plus_sd15.safetensors`, `sam_vit_b_01ec64.pth`, `yolox_l.onnx`.
- 輸入： 111: LoadImage, 148: LoadImage, 106: LoadImage.
- 輸出： 138: PreviewImage, 101: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_Comfyroll_CustomNodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-clotheschangertiled-cbb48e88`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: FaceDetailer, SAMLoader, UltralyticsDetectorProvider No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 123 — `ducklineart.json`

Drive SVD image-to-video using lineart extracted from an input video and an image reference. This is a video/line-control graph despite its root filename; VideoHelperSuite output is required.

- 容器／格式： `json_file` / `visual`. SHA-256: `70a5c8332f72eb3ad386f968effe2e1aa83d790430fda7f86a094455c64fd1af`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ACN_AdvancedControlNetApply`, `ControlNetLoaderAdvanced`, `GetImageSize`, `ImageOnlyCheckpointLoader`, `JWImageResizeByLongerSide`, `KSampler`, `LineartStandardPreprocessor`, `LoadImage`, `SVD_img2vid_Conditioning`, `VAEDecode`, `VHS_LoadVideo`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.
- 明確模型檔名： `controlnet-lineart-svd-v1/diffusion_pytorch_model.safetensors`, `svd_xt.safetensors`.
- 輸入： 81: VHS_LoadVideo, 57: LoadImage.
- 輸出： 24: VHS_VideoCombine.
- 相依套件： comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-ducklineart-2cf96dc2`; catalog distribution: links-only.
- 公開副本： `workflows/ducklineart.json`; SHA-256 `bdf2342c20179b27bd56e66f58bfdd209284c9f41942ab44e227f58a172c528d`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 124 — `hunyuan_dit/ditwithupscale.json`

Generate HunyuanDiT images and upscale/refine them with RealVisXL Lightning, FL slices and UltimateSDUpscale. Translation and model-family-specific encoders are additional dependencies.

- 容器／格式： `json_file` / `visual`. SHA-256: `ceddcfeb711f5aadace5eb8a18aa77be2229afd623f4e31d30f4d0e9db21a414`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AlignYourStepsScheduler`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `Efficient Loader`, `EmptyLatentImage`, `FL_PromptSelector`, `FL_SDUltimate_Slices`, `GoogleTranslateTextNode`, `KSampler //Inspire`, `KSamplerSelect`, `Note`, `SD_4XUpscale_Conditioning`, `SaveImage`, `UltimateSDUpscaleCustomSample`, `UpscaleModelLoader`, `VAEDecode`.
- 明確模型檔名： `4x-UltraSharp.pth`, `hunyuan_dit_comfyui/hunyuan_dit_1.2.safetensors`, `realvisxlV40_v40LightningBakedvae.safetensors`.
- 輸入： 7: CLIPTextEncode, 6: CLIPTextEncode, 55: FL_PromptSelector.
- 輸出： 29: SaveImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_Fill-Nodes. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Google translation service/custom-node backend; network behavior and provider configuration must be checked.
- 公開目錄／工作流庫 ID： `dseditor-hunyuan-dit-ditwithupscale-fbd67ce1`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 125 — `inpaintoutpaint/【Inpaint】SDXLRemove.json`

Remove selected objects by Florence coordinate grounding and SAM2 masks, then use SDXL Union inpaint controls. Inspect the actual mask before generation; Florence/SAM2 nodes may download auxiliary models.

- 容器／格式： `json_file` / `visual`. SHA-256: `30dd1b2a5a44bd808da9161853d485834f3161bddc604b022e363c118b525b3e`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 25 top-level nodes; 25 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DownloadAndLoadFlorence2Model`, `DownloadAndLoadSAM2Model`, `EmptyLatentImage`, `Florence2Run`, `Florence2toCoordinates`, `INPAINT_ExpandMask`, `ImageResize+`, `InpaintPreprocessor`, `KSampler`, `LoadImage`, `MaskPreview+`, `Note`, `PreviewImage`, `ResizeMask`, `Sam2Segmentation`, `SetUnionControlNetType`, `SimpleCondition+`, `SimpleMathBoolean+`, `ToBinaryMask`, `VAEDecode`.
- 明確模型檔名： `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`, `sam2_hiera_small.safetensors`.
- 輸入： 2: CLIPTextEncode, 3: CLIPTextEncode, 17: LoadImage.
- 輸出： 80: MaskPreview+, 13: PreviewImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-object-removal`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-object-removal.json`; SHA-256 `a4035075c3d3431f25b771da534d5efb0e0ebde49a8d14354bbcb5622582cd96`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 126 — `inpaintoutpaint/【powerpaint】outpaint.json`

Outpaint a source image with PowerPaint, pad masks, SD1.5 and RAUNet. Requires both PowerPaint diffusion and CLIP assets; output is preview-only.

- 容器／格式： `json_file` / `visual`. SHA-256: `47999f9c9841233f872d1a293aecf1ac3bf41e841484af3bea089e8b142d0853`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BrushNetLoader`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ImagePadForOutpaint`, `JWImageResizeByLongerSide`, `KSampler //Inspire`, `LoadImage`, `PowerPaint`, `PowerPaintCLIPLoader`, `PreviewImage`, `RAUNet`, `VAEDecode`.
- 明確模型檔名： `Powerpaint/diffusion_pytorch_model.safetensors`, `Powerpaint/pytorch_model.bin`, `SD15/model.fp16.safetensors`, `realisticVisionV60B1_v51VAE.safetensors`.
- 輸入： 50: CLIPTextEncode, 49: CLIPTextEncode, 58: LoadImage.
- 輸出： 12: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-inpaintoutpaint-powerpaint-outpaint-e55a3a34`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 127 — `inpaintoutpaint/【powerpaint】removepeole.json`

Remove selected people using YOLO segmentation order, combined masks and PowerPaint. Mask ordering determines whom to remove; do not infer that detection identifies the intended subject automatically.

- 容器／格式： `json_file` / `visual`. SHA-256: `edd4eb6d6141a3c46f373083a4d21e34851213b90f3020bfdeabba394c8967c8`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BrushNetLoader`, `Efficient Loader`, `ImpactSEGSOrderedFilter`, `Int Literal`, `JWImageResizeByLongerSide`, `KSampler //Inspire`, `LoadImage`, `MaskToImage`, `PowerPaint`, `PowerPaintCLIPLoader`, `PreviewImage`, `RAUNet`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `UltralyticsDetectorProvider`, `VAEDecode`.
- 明確模型檔名： `Powerpaint/diffusion_pytorch_model.safetensors`, `Powerpaint/pytorch_model.bin`, `SD15/model.fp16.safetensors`, `realisticVisionV60B1_v51VAE.safetensors`, `segm/yolov8m-seg.pt`.
- 輸入： 20: LoadImage.
- 輸出： 111: PreviewImage, 113: PreviewImage, 77: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-inpaintoutpaint-powerpaint-removepeole-56781d05`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 128 — `inpaintoutpaint.json`

Inpaint/outpaint an image using grouped SDXL checkpoint/text loading and grouped background removal/resizing. A separately loaded inpainting UNET participates; legacy groups block the public importer.

- 容器／格式： `json_file` / `visual`. SHA-256: `4530a689014cdf75172862d31f9887ebee060f385af757d3cc80a637fd6def3b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 12 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `Image Rembg (Remove Background)`, `ImagePadForOutpaint`, `ImageToMask`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `PreviewImage`, `UNETLoader`, `VAEDecode`, `VAEEncodeForInpaint`, `workflow/CheckpointXL`, `workflow/resizeandremovebg`.
- 明確模型檔名： `diffusion_pytorch_model.fp16.safetensors`, `sdxlYamersRealisticNSFW_v5TX.safetensors`.
- 輸入： 20: LoadImage, root/63: CLIPTextEncode, root/63: CLIPTextEncode.
- 輸出： 57: PreviewImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-inpaintoutpaint-caa42f82`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: CLIPTextEncode, CheckpointLoaderSimple, Image Rembg (Remove Background), ImageToMask, InvertMask, JWImageResizeByLongerSide No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 129 — `ipaclothes.json`

Combine pose, face and clothing images with SD1.5 IPAdapter and a grouped face detailer. Upstream README explicitly treats clothing as a generative reference, and the grouped structure requires review/migration.

- 容器／格式： `json_file` / `visual`. SHA-256: `96394a7d912993e74ad8626f9dd571f6fa4354d2f095f5297277e26c7527fe0b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 21 top-level nodes; 23 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApply`, `DWPreprocessor`, `DiffControlNetLoader`, `EmptyLatentImage`, `FaceDetailer`, `GetImageSize`, `IPAdapter`, `IPAdapterUnifiedLoader`, `IPAdapterUnifiedLoaderFaceID`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `PreviewImage`, `UltralyticsDetectorProvider`, `VAEDecode`, `workflow/controlnet`, `workflow/facedetailer`.
- 明確模型檔名： `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `majicmixRealistic_v7.safetensors`, `yolox_l.torchscript.pt`.
- 輸入： 48: LoadImage, 6: CLIPTextEncode, 7: CLIPTextEncode, 114: LoadImage, 132: LoadImage.
- 輸出： 130: PreviewImage, 30: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-ipaclothes-9a34e732`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: ControlNetApply, DiffControlNetLoader, FaceDetailer, UltralyticsDetectorProvider No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 130 — `layerdiffmix.json`

Compare layered foreground/background synthesis and composites with DreamShaper XL, SegmentAnything and LaMa. Several preview branches exist; no verified output or mask quality is supplied by static validation.

- 容器／格式： `json_file` / `visual`. SHA-256: `e21cfbb2ac62a31e990e289e14b06a4f1695cb57e43d036fac06ae61f4a731d1`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 32 top-level nodes; 32 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CheckpointLoaderSimple`, `ConditionText`, `EmptyLatentImage`, `GetImageSize`, `JWImageResizeByFactor`, `KSampler`, `LayerMask: SegmentAnythingUltra`, `LayerUtility: GetImageSize`, `LayerUtility: LaMa`, `LayeredDiffusionCondApply`, `LayeredDiffusionDiffApply`, `LoadImage`, `PreviewImage`, `SDXLPromptStyler`, `VAEDecode`, `VAEEncode`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`.
- 輸入： 20: LoadImage, 121: SDXLPromptStyler.
- 輸出： 167: PreviewImage, 67: PreviewImage, 158: PreviewImage, 183: PreviewImage, 160: PreviewImage.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-layerdiffmix-ad04f590`; catalog distribution: links-only.
- 公開副本： `workflows/layerdiffmix.json`; SHA-256 `26a5fb75aac912efff57a996b2ebfabc2d83518210cf233a223f078240f44844`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 131 — `layerdiffusionsubject.json`

Generate a subject/background composition with Layer Diffusion and depth ControlNet from an uploaded image. Layer masks, depth model and preview output need inspection.

- 容器／格式： `json_file` / `visual`. SHA-256: `72cedad5dc4c884954402e60c093ca114cc000c0379a9f6f1a5e380abe819402`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 14 top-level nodes; 14 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ControlNetApply`, `ControlNetLoader`, `DepthAnythingPreprocessor`, `Efficient Loader`, `GetImageSize`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: SegmentAnythingUltra`, `LayerUtility: ImageRemoveAlpha`, `LayeredDiffusionCondApply`, `LoadImage`, `PreviewImage`, `SDXLPromptStyler`, `VAEEncode`.
- 明確模型檔名： `controlnetT2IAdapter_t2iAdapterDepth.safetensors`, `depth_anything_vitl14.pth`, `dreamshaperXL_lightningDPMSDE.safetensors`.
- 輸入： 20: LoadImage, 121: SDXLPromptStyler.
- 輸出： 207: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-layerdiffusionsubject-6ce254c9`; catalog distribution: links-only.
- 公開副本： `workflows/layerdiffusionsubject.json`; SHA-256 `dbf4f58be7ec322a3308a077b92de32eb162c1238d1dfdac4e24deb6906b7208`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 132 — `llm.json`

Use local LLaVA/GGUF text generation as prompt input for DreamShaper XL generation with a styled prompt. Separate LLM/projector and image-model assets are required; outputs are text and image previews.

- 容器／格式： `json_file` / `visual`. SHA-256: `29dab2bff628d11069ea0b55901d74c138de539957bfe6934d376629e942b991`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 10 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Efficient Loader`, `KSampler (Efficient)`, `LLMSampler`, `LLava Loader Simple`, `LlavaClipLoader`, `PreviewImage`, `SDXL Empty Latent Image`, `SDXLPromptStyler`, `ShowText|pysssss`, `SimpleText`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`, `llava-1.6-mistral-7b-gguf/llava-v1.6-mistral-7b.Q4_K_M.gguf`, `llava-1.6-mistral-7b-gguf/mmproj-model-f16.gguf`.
- 輸入： 72: SDXLPromptStyler, 32: SimpleText.
- 輸出： 78: PreviewImage, 67: ShowText|pysssss.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-llm-63f97950`; catalog distribution: links-only.
- 公開副本： `workflows/llm.json`; SHA-256 `c1c36550c7eb60fd0732c0d5b22b06437351f90a4972887f653a263f09454117`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 133 — `ollama/SD15haircut.json`

Create an SD1.5 hairstyle comparison book using person masks, FaceID and Ollama descriptions/prompt lists. Dangling links remain; generated panels depend on mask selection and local LLM services.

- 容器／格式： `json_file` / `visual`. SHA-256: `afda85d1d60c98b6dcf596f286f15210c63fb826aa71f0239b15f8bbd86cb3c4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 38 top-level nodes; 38 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `CR Comic Panel Templates`, `CR Prompt List`, `CR Text Concatenate`, `DetailerForEachPipe`, `Efficient Loader`, `EmptyImage`, `Fast Groups Bypasser (rgthree)`, `IPAdapterFaceID`, `IPAdapterUnifiedLoaderFaceID`, `ImageListToBatch+`, `ImpactSwitch`, `JWImageResizeByLongerSide`, `LayerMask: PersonMaskUltra`, `LayerStyle: DropShadow`, `LayerUtility: ImageBlendAdvance`, `LayerUtility: SimpleTextImage`, `LoadImage`, `MaskToSEGS`, `OllamaGenerateAdvance`, `OllamaVision`, `PatchModelAddDownscale`, `PerturbedAttentionGuidance`, `PreviewImage`, `SDXLPromptStyler`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`, `ToBasicPipe`.
- 明確模型檔名： `hypersd/Hyper-SD15-8steps-lora.safetensors`, `majicmixRealistic_v7.safetensors`.
- 輸入： 200: CR Prompt List, 148: SDXLPromptStyler, 189: CR Prompt List, 207: String Literal, 111: LayerUtility: SimpleTextImage, 138: LoadImage.
- 輸出： 120: ShowText|pysssss, 122: ShowText|pysssss, 106: ShowText|pysssss, 157: PreviewImage, 188: PreviewImage, 110: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials; rgthree-comfy; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-ollama-sd15haircut-fe0393ec`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 134 — `ollama/SDXLhaircut.json`

Create an SDXL hairstyle book using PuLID identity, canny controls, person masks and Ollama assistance. Dangling links remain; this has a different identity stack from the SD1.5 variant.

- 容器／格式： `json_file` / `visual`. SHA-256: `4417f876aef46933455460daa9ff785402cefa81867c038160177c559c9618ec`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 41 top-level nodes; 41 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `ApplyPulid`, `CR Comic Panel Templates`, `CR Prompt List`, `CR Text Concatenate`, `CannyEdgePreprocessor`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DetailerForEachPipe`, `Efficient Loader`, `EmptyImage`, `Fast Groups Bypasser (rgthree)`, `ImageListToBatch+`, `ImpactSwitch`, `InstantIDFaceAnalysis`, `JWImageResizeByLongerSide`, `LayerMask: PersonMaskUltra`, `LayerStyle: DropShadow`, `LayerUtility: ImageBlendAdvance`, `LayerUtility: SimpleTextImage`, `LoadImage`, `MaskToSEGS`, `OllamaGenerateAdvance`, `OllamaVision`, `PreviewImage`, `PulidEvaClipLoader`, `PulidModelLoader`, `SDXLPromptStyler`, `ShowText|pysssss`, `String Literal`, `StringFunction|pysssss`, `ToBasicPipe`.
- 明確模型檔名： `control-lora-canny-rank256.safetensors`, `dreamshaperXL_lightningDPMSDE.safetensors`, `ip-adapter_pulid_sdxl_fp16.safetensors`.
- 輸入： 111: LayerUtility: SimpleTextImage, 200: CR Prompt List, 148: SDXLPromptStyler, 207: String Literal, 189: CR Prompt List, 138: LoadImage.
- 輸出： 188: PreviewImage, 120: ShowText|pysssss, 110: PreviewImage, 122: ShowText|pysssss, 106: ShowText|pysssss, 157: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_essentials; rgthree-comfy; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service; PuLID/PuLID-Flux and matching InsightFace/EVA dependencies. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-ollama-sdxlhaircut-10b253d6`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 135 — `ollama/geminidesign.json`

Describe/design from a reference image using OllamaVision and Gemini, then render with an SDXL checkpoint. Both configured LLM paths and prompt parsing must be checked.

- 容器／格式： `json_file` / `visual`. SHA-256: `cf922f0bcb6733346f50815cc4c08acd8e49b733d8bcda93faf96e537634939b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 10 top-level nodes; 9 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Efficient Loader`, `Gemini_API_Zho`, `KSampler (Efficient)`, `LoadImage`, `OllamaVision`, `PreviewImage`, `SDXLPromptStyler`, `ShowText|pysssss`, `TextSplitByDelimiter`.
- 明確模型檔名： `aiartistHyperrayXLXL_hypersdxl.safetensors`.
- 輸入： 7: SDXLPromptStyler, 2: LoadImage.
- 輸出： 3: ShowText|pysssss, 11: PreviewImage, 6: ShowText|pysssss.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly; Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-ollama-geminidesign-c350b3de`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 136 — `ollama/ollamaclotheschanger.json`

Generate clothing/pose/face combinations with Ollama-derived descriptions, SD1.5 IPAdapter/FaceID, OpenPose and face detailer. Inputs supply distinct roles; local Ollama services and implicit routing are additional requirements.

- 容器／格式： `json_file` / `visual`. SHA-256: `2796d5cb2d233d1346f5f1d23f5e92c9f0e10bb4296002e8fb453e9a41149823`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 45 top-level nodes; 45 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Anything Everywhere3`, `CLIPSetLastLayer`, `CLIPTextEncode`, `CR Text Concatenate`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DWPreprocessor`, `EmptyLatentImage`, `FaceDetailer`, `FreeU_V2`, `GetImageSize`, `IPAdapter`, `IPAdapterCombineEmbeds`, `IPAdapterEmbeds`, `IPAdapterEncoder`, `IPAdapterUnifiedLoader`, `IPAdapterUnifiedLoaderFaceID`, `JWImageResizeByLongerSide`, `KSampler //Inspire`, `LoadImage`, `OllamaGenerate`, `OllamaVision`, `PerturbedAttentionGuidance`, `PreviewImage`, `ShowText|pysssss`, `String Literal`, `Text List`, `Text List to Text`, `UltralyticsDetectorProvider`, `VAEDecode`.
- 明確模型檔名： `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `majicmixRealistic_v7.safetensors`, `yolox_l.torchscript.pt`.
- 輸入： 200: String Literal, 132: LoadImage, 217: String Literal, 7: CLIPTextEncode, 6: CLIPTextEncode, 213: String Literal, 174: LoadImage, 197: String Literal, 173: LoadImage, 48: LoadImage.
- 輸出： 30: PreviewImage, 216: ShowText|pysssss, 130: PreviewImage, 199: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; Ollama custom nodes plus explicitly configured Ollama service. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Ollama service plus installed LLM; endpoint/model configuration required.
- 公開目錄／工作流庫 ID： `dseditor-ollama-ollamaclotheschanger-50107208`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 137 — `playgroundchanger.json`

Playground v2.5 background replacement using SegmentAnything, inverted mask, Differential Diffusion and continuous EDM sampling. This is a local baseline copy of a graph also represented by a separate background-folder variant.

- 容器／格式： `json_file` / `visual`. SHA-256: `d43f6b26587fc2b038138e559309f2095ec820aac60b18176152f8d562decfec`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 12 top-level nodes; 12 active expanded nodes. Top-level check: **pass**.
- 節點類型： `DifferentialDiffusion`, `Efficient Loader`, `GetImageSize`, `InpaintModelConditioning`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LayerMask: SegmentAnythingUltra`, `LoadImage`, `ModelSamplingContinuousEDM`, `PreviewImage`, `SDXLPromptStyler`.
- 明確模型檔名： `playground-v2.5-1024px-aesthetic.fp16.safetensors`.
- 輸入： 20: LoadImage, 121: SDXLPromptStyler.
- 輸出： 243: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-playgroundchanger-e8a3baa9`; catalog distribution: links-only.
- 公開副本： `workflows/playgroundchanger.json`; SHA-256 `6cc068251baf8cbd671ee0fdd3e5d55c017a98b17e731372515184894dc11570`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 138 — `removeothers.json`

Remove selected subjects with ordered YOLO masks and Fooocus inpaint patches on SDXL. Requires Fooocus head/patch assets and choosing the intended segmentation mask; previews are the output.

- 容器／格式： `json_file` / `visual`. SHA-256: `964f5a45dda8581c01608ed93615aaf4491573c349aa4034bb7e09d7ed19911c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 21 top-level nodes; 21 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Efficient Loader`, `INPAINT_ApplyFooocusInpaint`, `INPAINT_LoadFooocusInpaint`, `ImpactSEGSOrderedFilter`, `InjectNoiseToLatent`, `Int Literal`, `JWImageResizeByLongerSide`, `KSampler`, `LoadImage`, `MaskToImage`, `PreviewImage`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncode`, `VAEEncodeForInpaint`.
- 明確模型檔名： `dreamshaperXL_v2TurboDpmppSDE.safetensors`, `fooocus_inpaint_head.pth`, `segm/yolov8m-seg.pt`.
- 輸入： 20: LoadImage.
- 輸出： 77: PreviewImage, 111: PreviewImage, 113: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-removeothers-6b974cbe`; catalog distribution: links-only.
- 公開副本： `workflows/removeothers.json`; SHA-256 `c653aed830cc75acf9be7ce45c54d54fe8348edc674a206d272b6599b35f5e4f`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 139 — `sdxlhandfix.json`

Repair SDXL hands with MeshGraphormer depth, mask-to-SEGS and per-region detailing. This single upstream path has two different sanitized public copies (curated and baseline), with separate library IDs.

- 容器／格式： `json_file` / `visual`. SHA-256: `fefe8a3473163356ba3aceda3c89880910a7f0e0eaf97ee82a95c8a3472cd058`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 11 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ControlNetLoader`, `DetailerForEachPipe`, `Efficient Loader`, `ImpactControlNetApplySEGS`, `LoadImage`, `MaskToSEGS`, `MeshGraphormer-DepthMapPreprocessor`, `MeshGraphormerDepthMapPreprocessorProvider_for_SEGS //Inspire`, `PreviewImage`, `ToBasicPipe`.
- 明確模型檔名： `control-lora-depth-rank256.safetensors`, `leosamsHelloworldXL_hw50EulerALightning.safetensors`.
- 輸入： 1: LoadImage.
- 輸出： 34: PreviewImage, 27: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-sdxl-hand-fix`; catalog distribution: vendored.
- 公開副本： `workflows/community/dseditor/sdxl-hand-fix.json`; SHA-256 `0267e777fe1f4029abb7372bcbf5a836bc416a734cc9e42923faa5ee5587952d`; recorded hash match: True.
- 公開副本： `workflows/sdxlhandfix.json`; SHA-256 `0935534f93327f772855ac602a78f2e4b92a71fe734a33310407278f5ad8c9ae`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 140 — `stablecascade-quickmix.json`

Use Stable Cascade stages C/B and reference vision conditioning, then mix/refine through an SD1.5 checkpoint. Models from different families are deliberately staged; output quality and compatibility are untested.

- 容器／格式： `json_file` / `visual`. SHA-256: `16d2a2187f4f2cb6eed13356d27c998dc6c88f563e3883a71f15a94a32aa423a`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 28 top-level nodes; 28 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionEncode`, `CheckpointLoaderSimple`, `Efficient Loader`, `FreeU_V2`, `GetImageSize`, `KSampler`, `KSampler (Efficient)`, `LoadImage`, `PreviewImage`, `SDXLPromptStyler`, `StableCascade_EmptyLatentImage`, `StableCascade_StageB_Conditioning`, `Text List`, `Text List to Text`, `VAEDecode`, `VAEEncode`, `WD14Tagger|pysssss`, `unCLIPCheckpointLoader`, `unCLIPConditioning`.
- 明確模型檔名： `Stable-Cascade/stable_cascade_stage_b.safetensors`, `Stable-Cascade/stable_cascade_stage_c.safetensors`, `add_detail.safetensors`, `ghostmix_v20Bakedvae.safetensors`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 48: LoadImage, 61: SDXLPromptStyler, 57: LoadImage.
- 輸出： 83: PreviewImage, 86: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-stablecascade-quickmix-421e1d9e`; catalog distribution: links-only.
- 公開副本： `workflows/stablecascade-quickmix.json`; SHA-256 `c828fa4b9a125890bac4334e4cd975a591b8e4c986afe1e5f9c6dd1e24e942d1`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 141 — `stablecascadecontrolnet.json`

Generate/inpaint using Stable Cascade stages C/B, canny/inpaint controls and grouped mask/composite logic. The legacy group contains essential controls and masks; currently source-reference only.

- 容器／格式： `json_file` / `visual`. SHA-256: `b5ebc7d631a08fd4e6500501cd25d9733e151025b6a376c6432b5b4339a3151f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 24 top-level nodes; 29 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AIO_Preprocessor`, `CLIPTextEncode`, `CLIPVisionEncode`, `CR Apply Multi-ControlNet`, `CR Multi-ControlNet Stack`, `EmptyImage`, `GetImageSize`, `ImageCompositeMasked`, `InvertMask`, `JoinImageWithAlpha`, `KSampler`, `LayerMask: PersonMaskUltra`, `LayerUtility: ImageBlendAdvance`, `LoadImage`, `PreviewImage`, `Reroute`, `SDXLPromptStyler`, `SaveImage`, `StableCascade_CheckpointLoader //Inspire`, `StableCascade_EmptyLatentImage`, `StableCascade_StageB_Conditioning`, `ThresholdMask`, `VAEDecode`, `WD14Tagger|pysssss`, `unCLIPConditioning`, `workflow/StableCascadeInpaintCnet`.
- 明確模型檔名： `Stable-Cascade/stable_cascade_stage_b.safetensors`, `Stable-Cascade/stable_cascade_stage_c.safetensors`, `stablecascade/canny.safetensors`, `stablecascade/inpainting.safetensors`.
- 輸入： 7: CLIPTextEncode, 6: CLIPTextEncode, 50: LoadImage, 100: LoadImage, 85: SDXLPromptStyler.
- 輸出： 90: PreviewImage, 9: SaveImage, 76: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-stablecascadecontrolnet-b268b833`; catalog distribution: links-only.
- 公開副本： none.
- 介面／執行器： workflow-library source reference only; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. Static catalog omits active nested node types: EmptyImage, ImageCompositeMasked, InvertMask, JoinImageWithAlpha, Reroute, ThresholdMask No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 142 — `workflowmasked.json`

Generate a region-conditioned Stable Cascade image using color masks, multiple prompts and staged C/B sampling. Mask colors correspond to regions; user must inspect region/prompt assignment before execution.

- 容器／格式： `json_file` / `visual`. SHA-256: `2d3b1c7014bf99d45db377696acd6ec915af303275f9936a5264f25873f2de96`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 圖結構： 25 top-level nodes; 25 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConditioningCombine`, `EmptyImage`, `ImageCompositeMasked`, `KSampler`, `PreviewImage`, `RegionalConditioningColorMask //Inspire`, `SDXLPromptStyler`, `SaveImage`, `SimpleMath+`, `StableCascade_EmptyLatentImage`, `StableCascade_StageB_Conditioning`, `VAEDecode`, `Width/Height Literal`.
- 明確模型檔名： `Stable-Cascade/stable_cascade_stage_b.safetensors`, `Stable-Cascade/stable_cascade_stage_c.safetensors`.
- 輸入： 60: SDXLPromptStyler, 6: CLIPTextEncode, 7: CLIPTextEncode.
- 輸出： 9: SaveImage, 74: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： `dseditor-workflowmasked-d0bb978a`; catalog distribution: links-only.
- 公開副本： `workflows/workflowmasked.json`; SHA-256 `c8ca3398efe0efcd2528762eddb6ee3106689e122bcf9aaa6ec5008bb4a9c2be`; recorded hash match: True.
- 介面／執行器： workflow-library details; download JSON and save visual JSON to native ComfyUI userdata; No app runner mapping or automatic queuing for this source graph; native ComfyUI execution after user review.
- 缺口： Public load action saves JSON under ComfyUI userdata; no direct app runner/queue or verified generation exists for this graph.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 143 — `Couple.zip!CoupleWorkflow(background).json`

Archived two-person/background composition using regional IPAdapters, pose control and legacy groups. It fails reciprocal-link validation and has no public catalog entry; archive packaging is not integration evidence.

- 容器／格式： `zip_member` / `visual`. SHA-256: `7cf594f5611a0ba4764968d73ae8b61a213e9b5dd600acbd7ffbf0dced649c1d`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `8bcab05c7a886d23c9dbb0878e6a9040d6b145c23d40cf4e38a296f0bbaf79d6`.
- 圖結構： 27 top-level nodes; 33 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `ApplyRegionalIPAdapters //Inspire`, `CLIPVisionLoader`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `Efficient Loader`, `GetImageSize`, `IPAdapterModelLoader`, `KSampler (Efficient)`, `LoadImage`, `MaskPreview+`, `Note`, `PrepImageForClipVision`, `PreviewImage`, `RegionalConditioningColorMask //Inspire`, `RegionalIPAdapterColorMask //Inspire`, `ToIPAdapterPipe //Inspire`, `workflow/GreenAreaIPA`, `workflow/RedAreaIPA`, `workflow/controlnet`, `workflow/pipeloader`, `workflow/人物loader`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `add_detail.safetensors`, `control_v11p_sd15_openpose.pth`, `henmixReal_v5c.safetensors`, `ip-adapter-plus-face_sd15.safetensors`, `ip-adapter-plus_sd15.safetensors`.
- 輸入： 175: LoadImage, 139: LoadImage, 78: LoadImage, 77: LoadImage, 183: LoadImage.
- 輸出： 25: PreviewImage, 116: MaskPreview+, 118: MaskPreview+, 119: MaskPreview+.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 144 — `Couple.zip!CoupleWorkflow(mix).json`

Archived two-person mixing variant with regional IPAdapters and groups. The CLIP vision filename includes a trailing space before its extension, and reciprocal links fail; no automatic normalization should silently guess the asset.

- 容器／格式： `zip_member` / `visual`. SHA-256: `a36491ea928a83b0b3983a6d9f11acf38808f666a1698ca9e2b899430d2c15ef`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `8bcab05c7a886d23c9dbb0878e6a9040d6b145c23d40cf4e38a296f0bbaf79d6`.
- 圖結構： 24 top-level nodes; 29 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `ApplyRegionalIPAdapters //Inspire`, `CLIPVisionLoader`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `Efficient Loader`, `GetImageSize`, `IPAdapterModelLoader`, `KSampler (Efficient)`, `LoadImage`, `MaskPreview+`, `Note`, `PrepImageForClipVision`, `PreviewImage`, `RegionalConditioningColorMask //Inspire`, `RegionalIPAdapterColorMask //Inspire`, `ToIPAdapterPipe //Inspire`, `workflow/GreenAreaIPA`, `workflow/IPadapterpipeloader`, `workflow/RedAreaIPA`, `workflow/controlnet`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K .safetensors`, `add_detail.safetensors`, `control_v11p_sd15_openpose.pth`, `henmixReal_v5c.safetensors`, `ip-adapter-plus-face_sd15.safetensors`, `ip-adapter-plus_sd15.safetensors`.
- 輸入： 175: LoadImage, 139: LoadImage, 78: LoadImage, 183: LoadImage, 77: LoadImage.
- 輸出： 119: MaskPreview+, 25: PreviewImage, 116: MaskPreview+, 118: MaskPreview+.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 145 — `Lesson/Lesson2.zip!Lesson2.json`

Archived three-reference Qwen image editing lesson using a bundled all-in-one checkpoint. Referenced lesson images are source examples, not public app fixtures; the archive graph is absent from its catalog.

- 容器／格式： `zip_member` / `visual`. SHA-256: `39f09b04491f1a77c074e4b19c2be8fb19c15e6d99302723495ac72c82dcc8cc`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `adfc6940b1e8fda36165283a850e11004916b06dbafabf2f06b096c63905ae47`.
- 圖結構： 11 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CheckpointLoaderSimple`, `ConditioningZeroOut`, `ImageScaleToTotalPixels`, `KSampler`, `LoadImage`, `SaveImage`, `TextEncodeQwenImageEditPlus`, `VAEDecode`, `VAEEncode`.
- 明確模型檔名： `Qwen-Rapid-AIO-NSFW-v14.1.safetensors`.
- 輸入： 32: LoadImage, 33: LoadImage, 10: LoadImage, 24: TextEncodeQwenImageEditPlus.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 146 — `MiniMAXH3/0811H3MotionContextLoop.zip!MotionContext.json`

Archived local MiniMax H3 continuation loop using motion-context carry/trim, image and audio concatenation, Turbo LoRA and multiple video saves. The app has related H3 functionality but no provenance or runner linkage to this exact source graph.

- 容器／格式： `zip_member` / `visual`. SHA-256: `af4483fb79044e706c460b6317290ca7661c28cf8872139d9a3127e7b6365f60`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `e90e9190eeddb8564712590cc1b326a8e4de784a0cce194f2220827b562115d2`.
- 圖結構： 40 top-level nodes; 40 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AudioConcat`, `BasicGuider`, `BasicScheduler`, `BatchImagesNode`, `CLIPLoader`, `ComfyMathExpression`, `CreateVideo`, `GetImageSize`, `ImageFromBatch`, `ImageScaleToTotalPixels`, `KSamplerSelect`, `LoadImage`, `LoraLoaderModelOnly`, `MiniMaxH3ImageToVideo`, `MiniMaxH3MotionContext`, `MiniMaxH3MotionContextTrim`, `PrimitiveInt`, `PrimitiveStringMultiline`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveVideo`, `UNETLoader`, `VAEDecode`, `VAEDecodeAudio`, `VAELoader`, `easy forLoopEnd`, `easy forLoopStart`, `easy textIndexSwitch`.
- 明確模型檔名： `H3/minimax_h3_fl2v_turbo_4step_v0.1_comfyui_alpha8-T8-convert.safetensors`, `minimax_h3_audio_vae_fp32.safetensors`, `minimax_h3_fl2va_pruned_int8_convrot.safetensors`, `minimax_h3_video_vae_fp16.safetensors`, `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.
- 輸入： 5: LoadImage, 10004: PrimitiveStringMultiline, 10005: PrimitiveStringMultiline, 10008: PrimitiveStringMultiline.
- 輸出： 14: CreateVideo, 15: SaveVideo, 38: SaveVideo, 28: CreateVideo, 37: CreateVideo, 29: SaveVideo.
- 相依套件： ComfyUI-Easy-Use. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `NikoDemon80/ComfyUI-H3-Motion-Context`, `comfy-core`, `comfyui-easy-use`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 147 — `MiniMAXH3/0813FirstEndFrameT8Audio.zip!Workflow.json`

Archived local MiniMax H3 first/last-frame generation with uploaded audio and T8 audio conditioning. It requires its specific Turbo LoRA and conditioning extension; no exact public copy exists.

- 容器／格式： `zip_member` / `visual`. SHA-256: `efc2e42b5953535b3e8369d5c527125ba52f3be3c6cb4d56a145e2519b5a1b24`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `f487f070f8ee33e1bfbbeee3ef4d89c02557d3779acdc0265ef74c8fb3248d6c`.
- 圖結構： 19 top-level nodes; 19 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `ComfyMathExpression`, `ConditioningZeroOut`, `CreateVideo`, `GetImageSize`, `ImageScaleToTotalPixels`, `KSampler`, `LoadAudio`, `LoadImage`, `LoraLoaderModelOnly`, `MiniMaxH3AudioConditioningT8`, `PrimitiveInt`, `SaveVideo`, `UNETLoader`, `VAEDecode`, `VAEDecodeAudio`, `VAELoader`.
- 明確模型檔名： `H3/minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`, `minimax_h3_audio_vae_fp32.safetensors`, `minimax_h3_fl2va_pruned_int8_convrot.safetensors`, `minimax_h3_video_vae_fp16.safetensors`, `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.
- 輸入： 5: LoadImage, 6: LoadImage, 7: LoadAudio.
- 輸出： 16: CreateVideo, 17: SaveVideo.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `T8mars/comfyui-minimax-h3-audio-T8`, `comfy-core`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 148 — `MiniMAXH3/Example.zip!xxt_mv2_S3_00002_.json`

Archived local MiniMax H3 reference-to-video example with two reference images and audio plus SolAttention/VRAM debugging. The exact graph/LoRA combination is not an app-integrated verified run.

- 容器／格式： `zip_member` / `visual`. SHA-256: `cf06efc8ac032be7e6a87d6f7b5da6fd051734c0243938ce8d9a8e25709c1dd4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `c6d1fb755c489d49aa5f701de15c1d69fd611293635e387b1a90177433523ae4`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPLoader`, `CreateVideo`, `KSamplerSelect`, `LoadAudio`, `LoadImage`, `LoraLoaderModelOnly`, `MiniMaxH3MemoryEfficientSolAttentionPatch`, `MiniMaxH3ReferenceToVideo`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveVideo`, `UNETLoader`, `VAEDecode`, `VAEDecodeAudio`, `VAELoader`, `VRAM_Debug`.
- 明確模型檔名： `H3/minimax_h3_fl2v_turbo_4step_v0.1_comfyui_alpha8-T8-convert.safetensors`, `minimax_h3_audio_vae_fp32.safetensors`, `minimax_h3_fl2va_pruned_int8_convrot.safetensors`, `minimax_h3_video_vae_fp16.safetensors`, `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`.
- 輸入： 5: LoadImage, 50: LoadImage, 6: LoadAudio.
- 輸出： 15: CreateVideo, 16: SaveVideo.
- 相依套件： efficiency-nodes-comfyui. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `Saganaki22/ComfyUI-sol-attn`, `comfy-core`, `comfyui-kjnodes`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 149 — `SpecialUsage/workflowsummit.zip!【SP】Anything(KritaGraph).json`

Encrypted ZIP member: filename suggests a Krita graph, but actual JSON, nodes, model selectors and behavior could not be inspected without the archive password.

- 容器／格式： `encrypted_zip_member` / `uninspected_encrypted`. SHA-256: `unavailable: encrypted member`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2af7883a56115a2a5b2cf39fb2d35c145a37826118a4a2fc5a734ee61c56591f`.
- 狀態： Encrypted source content unavailable; no node/model/behavior claims justified.

### 150 — `SpecialUsage/workflowsummit.zip!【SP】Anything.json`

Encrypted ZIP member: actual JSON, nodes, model selectors and behavior could not be inspected without the archive password.

- 容器／格式： `encrypted_zip_member` / `uninspected_encrypted`. SHA-256: `unavailable: encrypted member`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2af7883a56115a2a5b2cf39fb2d35c145a37826118a4a2fc5a734ee61c56591f`.
- 狀態： Encrypted source content unavailable; no node/model/behavior claims justified.

### 151 — `SpecialUsage/workflowsummit.zip!【SP】AnythingFaceAndClothes.json`

Encrypted ZIP member: filename mentions face/clothes, but actual JSON, dependencies and behavior remain uninspected without the archive password.

- 容器／格式： `encrypted_zip_member` / `uninspected_encrypted`. SHA-256: `unavailable: encrypted member`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2af7883a56115a2a5b2cf39fb2d35c145a37826118a4a2fc5a734ee61c56591f`.
- 狀態： Encrypted source content unavailable; no node/model/behavior claims justified.

### 152 — `SpecialUsage/workflowsummit.zip!【SP】FlowEdit.json`

Encrypted ZIP member: filename mentions FlowEdit, but actual JSON, dependencies and behavior remain uninspected without the archive password.

- 容器／格式： `encrypted_zip_member` / `uninspected_encrypted`. SHA-256: `unavailable: encrypted member`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2af7883a56115a2a5b2cf39fb2d35c145a37826118a4a2fc5a734ee61c56591f`.
- 狀態： Encrypted source content unavailable; no node/model/behavior claims justified.

### 153 — `SpecialUsage/workflowsummit.zip!【SP】FLUXBasic.json`

Encrypted ZIP member: filename mentions Flux, but actual JSON, dependencies and behavior remain uninspected without the archive password.

- 容器／格式： `encrypted_zip_member` / `uninspected_encrypted`. SHA-256: `unavailable: encrypted member`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2af7883a56115a2a5b2cf39fb2d35c145a37826118a4a2fc5a734ee61c56591f`.
- 狀態： Encrypted source content unavailable; no node/model/behavior claims justified.

### 154 — `SpecialUsage/workflowsummit.zip!【SP】LineArt.json`

Encrypted ZIP member: filename mentions line art, but actual JSON, dependencies and behavior remain uninspected without the archive password.

- 容器／格式： `encrypted_zip_member` / `uninspected_encrypted`. SHA-256: `unavailable: encrypted member`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2af7883a56115a2a5b2cf39fb2d35c145a37826118a4a2fc5a734ee61c56591f`.
- 狀態： Encrypted source content unavailable; no node/model/behavior claims justified.

### 155 — `Animate/【Depthflow】DepthFlowExample.png#workflow`

Embedded depth-parallax animation using DepthAnything and Depthflow orbital/linear/exponential motion plus depth-of-field. It has dangling links; no diffusion generation or public catalog integration is demonstrated.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `f378e3200d80ff3290d9da7611d077ce4e9e22765ebf1089a989328477049cbe`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `a6c35eac9b3a020ecf5807f62aa230ab8bbf6e81ca3dbf5c25891d91cdff9fe7`.
- 圖結構： 19 top-level nodes; 19 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `DepthAnythingV2Preprocessor`, `Depthflow`, `DepthflowEffectDOF`, `DepthflowMotionExponential`, `DepthflowMotionLinear`, `DepthflowMotionPresetOrbital`, `ImpactMakeImageBatch`, `Int Literal`, `JWImageResizeByLongerSide`, `LoadImage`, `VHS_VideoCombine`.
- 明確模型檔名： `depth_anything_v2_vitl.pth`.
- 輸入： 18: LoadImage, 33: LoadImage, 5: LoadImage.
- 輸出： 20: VHS_VideoCombine.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI-VideoHelperSuite plus compatible FFmpeg. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 156 — `Animate/【SVD】AudioMix.png#workflow`

Embedded SDXL image generation followed by SVD animation, RIFE and Stable Audio synthesis/muxing. Multiple independent models are needed; PNG metadata is source configuration, not verified runtime evidence.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `f0624b1b01d4f0f7c3e6e385ba2ec18459c103342b1ab5c0e505a09d827ac212`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `89db0dd0f095ff94bda1de23c2ed7dac97bec8c89856399488872ab22e53d7ca`.
- 圖結構： 30 top-level nodes; 30 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConditioningConcat`, `EmptyLatentAudio`, `EmptyLatentImage`, `GetImageSize+`, `ImageOnlyCheckpointLoader`, `JWImageResizeByLongerSide`, `KSampler`, `Note`, `RIFE VFI`, `SVD_img2vid_Conditioning`, `String Literal`, `Text to Conditioning`, `VAEDecode`, `VAEDecodeAudio`, `VHS_VideoCombine`, `VideoLinearCFGGuidance`.
- 明確模型檔名： `AnimateLCM-SVD-xt-1-1_fp16_comfy.safetensors`, `AudioClipmodel.safetensors`, `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`, `StableAudio/model.safetensors`, `rife47.pth`.
- 輸入： 72: CLIPTextEncode, 28: CLIPTextEncode, 37: CLIPTextEncode, 11: CLIPTextEncode, 2: String Literal, 3: String Literal.
- 輸出： 59: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 157 — `Clothes/OutfitToOutfit.png#workflow`

Embedded OutfitToOutfit inpainting with clothing masks, OpenPose, DetailDaemon and labeled comparison panels. It uses an outfit-specific checkpoint and original reference input; not equivalent to the existing public pose/clothing graph.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `d7990418d670d670b5de324fbf7d39dfebccc6b648f853bbecd421daddfcc761`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `01db62f2a4685af48323f2b8a42621f8e0807572b61a93ffbda249daf265c07c`.
- 圖結構： 39 top-level nodes; 39 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CFGGuider`, `CLIPLoader`, `CLIPTextEncode`, `CR Comic Panel Templates`, `CR Text Concatenate`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DWPreprocessor`, `DetailDaemonSamplerNode`, `DifferentialDiffusion`, `EmptyImage`, `GoogleTranslateTextNode`, `ImageListToBatch+`, `ImpactSwitch`, `JWImageResizeByLongerSide`, `KSamplerSelect`, `LayerMask: PersonMaskUltra`, `LayerStyle: DropShadow`, `LayerUtility: ImageBlendAdvance`, `LayerUtility: SimpleTextImage`, `LayerUtility: TextBox`, `LoadImage`, `MaskPreview+`, `Note`, `PreviewBridge`, `PreviewImage`, `RandomNoise`, `RandomPrompt`, `SamplerCustomAdvanced`, `SaveImage`, `ShowText|pysssss`, `VAEDecode`, `VAEEncodeForInpaint`.
- 明確模型檔名： `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `epicphotogasm_ultimateFidelity.safetensors`, `outfitToOutfit_v20.safetensors`, `yolox_l.onnx`.
- 輸入： 280: CLIPTextEncode, 281: CLIPTextEncode, 111: LayerUtility: SimpleTextImage, 219: RandomPrompt, 138: LoadImage.
- 輸出： 259: PreviewBridge, 284: PreviewImage, 239: MaskPreview+, 257: SaveImage, 220: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_essentials; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Google translation service/custom-node backend; network behavior and provider configuration must be checked.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 158 — `Clothes/【SDXL】DressingandOutpaint.png#workflow`

Embedded SDXL clothing edit/outpaint with Segformer garment masks, IPAdapter and Union/EcomXL controls. It provides editable mask previews and separate save branches; reference roles and mask inversions matter.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `3a07f2e9d43bb77fc566ef0daca1bf17a53db50d370fbde1beb946cef6d888fb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `72ea2ba9beb5f5200866b81fd8a866ab7e076bd1981621574fff113fbe752a04`.
- 圖結構： 41 top-level nodes; 37 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ConditioningCombine`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DWPreprocessor`, `EmptyLatentImage`, `GetImageSize+`, `IPAdapterAdvanced`, `IPAdapterNoise`, `IPAdapterUnifiedLoader`, `ImagePadForOutpaint`, `ImageResize+`, `ImpactSwitch`, `InpaintPreprocessor`, `InvertMask`, `JoinImageWithAlpha`, `KSampler`, `LayerMask: SegformerB2ClothesUltra`, `LoadImage`, `MaskPreview+`, `Note`, `PreviewBridge`, `SaveImage`, `SetUnionControlNetType`, `SimpleCondition+`, `SimpleMathBoolean+`, `SplitImageWithAlpha`, `VAEDecode`, `VAEEncodeForInpaint`, `WD14Tagger|pysssss`.
- 明確模型檔名： `SDXL/EcomXL_controlnet_inpaint.safetensors`, `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `yolox_l.onnx`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 53: CLIPTextEncode, 97: LoadImage, 12: LoadImage.
- 輸出： 75: MaskPreview+ [mode 4], 71: PreviewBridge [mode 4], 58: MaskPreview+, 90: SaveImage, 91: SaveImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 159 — `Design/【CosXL_edit】LinearttoPic.png#workflow`

Embedded CosXL lineart-to-picture editing with IPAdapter reference/style, using a grouped Pix2Pix sampler. Both source and appearance reference images must be configured; legacy group expansion is required.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `67da1f1b7059c3d9015e14f702ded59f2024a23f268f120c0f44c5e2c6731bb1`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `56e38d529531303223bce4260dace7aaf022ddbfa8770a73b192847a44c46423`.
- 圖結構： 11 top-level nodes; 19 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CheckpointLoaderSimple`, `DualCFGGuider`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImageScaleToTotalPixels`, `InstructPixToPixConditioning`, `KSamplerSelect`, `LoadImage`, `PreviewImage`, `Prompts`, `RandomNoise`, `Reroute`, `SDXLPromptStylerAll`, `SamplerCustomAdvanced`, `VAEDecode`, `workflow/IP2PSampler`.
- 明確模型檔名： `CosXL/robmixCosxlEdit_cosxlZenithV20.safetensors`.
- 輸入： 97: Prompts, 96: SDXLPromptStylerAll, 81: LoadImage, 78: LoadImage.
- 輸出： 98: PreviewImage.
- 相依套件： ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 160 — `Experiment/Manual/Alisgned.png#workflow`

Embedded example of StyleAligned plus grouped IPAdapter reference matching. It resembles the standalone experiment but is retained as its own metadata representation; no image pixels were reviewed.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `645ccfd2540083b95014ea368ad2179233ff5baf5f0fb81f70abdecea976ee81`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `c2fa699c4c7880f1c48bc708ca08587acb28403df4b926a5483a93d467a6f4d3`.
- 圖結構： 16 top-level nodes; 18 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPTextEncode`, `CLIPVisionLoader`, `Efficient Loader`, `IPAdapterApply`, `IPAdapterModelLoader`, `JWImageResizeByLongerSide`, `KSamplerSelect`, `LoadImage`, `PrepImageForClipVision`, `PreviewImage`, `ShowText|pysssss`, `StyleAlignedReferenceSampler`, `StyleAlignedSampleReferenceLatents`, `Text to Conditioning`, `VAEDecode`, `VAEEncode`, `WD14Tagger|pysssss`, `workflow/iPadapter`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `ghostmix_v20Bakedvae.safetensors`, `ip-adapter-plus_sd15.safetensors`.
- 輸入： 82: LoadImage, 107: CLIPTextEncode.
- 輸出： 77: ShowText|pysssss, 71: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 161 — `Experiment/Manual/ClothesChanger2.png#workflow`

Embedded example of garment segmentation/cut-paste/inpainting, corresponding to the ClothesChangerV2 experiment. Treat upstream fit/quality caveats as applicable rather than labeling its PNG as a verified app run.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `252c8eb8f79f11014ca1565829498d19ce3d988a2211a938755c450b4581afa5`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `b9033f393353725221549bdd9456631155f70e6b35262fd25b226e60e4b87d8f`.
- 圖結構： 38 top-level nodes; 38 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ControlNetApply`, `ControlNetLoader`, `Cut By Mask`, `DWPreprocessor`, `Efficient Loader`, `EmptyImage`, `GetImageSize`, `InvertMask`, `KSampler`, `LoadImage`, `MaskToImage`, `Mix Images By Mask`, `Paste By Mask`, `PreviewImage`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `SplitImageWithAlpha`, `UltralyticsDetectorProvider`, `VAEDecode`, `VAEEncodeForInpaint`, `WD14Tagger|pysssss`.
- 明確模型檔名： `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `majicmixRealistic_v7-inpainting.safetensors`, `segm/deepfashion2_yolov8s-seg.pt`, `yolox_l.onnx`.
- 輸入： 21: LoadImage, 27: LoadImage, 86: CLIPTextEncode.
- 輸出： 69: PreviewImage, 20: PreviewImage, 56: PreviewImage, 46: PreviewImage, 103: PreviewImage, 110: PreviewImage, 80: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 162 — `Experiment/Manual/NoAlisigned.png#workflow`

Embedded StyleAligned comparison including an ordinary efficient sampler branch. The additional branch makes it a comparison variant, not an identical source file.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `a248b6d7513d032fd2516d7b0ad4c0d8c396b52e12d692f53e7e4a9cc2bb5857`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `f79a0c640dca9c1d55f056a0eee88f0136c8df0d4baf0ffd5bbd3e880826e5f2`.
- 圖結構： 17 top-level nodes; 19 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `CLIPTextEncode`, `CLIPVisionLoader`, `Efficient Loader`, `IPAdapterApply`, `IPAdapterModelLoader`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `KSamplerSelect`, `LoadImage`, `PreviewImage`, `ShowText|pysssss`, `StyleAlignedReferenceSampler`, `StyleAlignedSampleReferenceLatents`, `Text to Conditioning`, `VAEDecode`, `VAEEncode`, `WD14Tagger|pysssss`, `workflow/iPadapter`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `ghostmix_v20Bakedvae.safetensors`, `ip-adapter-plus_sd15.safetensors`.
- 輸入： 82: LoadImage, 107: CLIPTextEncode.
- 輸出： 77: ShowText|pysssss, 123: PreviewImage, 71: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 163 — `Flux/ACELora/ACELoraPipe.png#workflow`

Embedded ACE LoRA pipeline mixing local/subject/portrait edits, Redux, face crops, Florence and Flux high-resolution refinement. Multiple dangling references remain; easy pipe routing and three ACE LoRAs add dependencies.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `c28d061d3a04664b784176a8a67d70f2342c96e394bd227205c9d0d68b3728c3`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `df190c5204354d29a59db7f62924590c01bb8c290c2e490581b33f2ff94a3c65`.
- 圖結構： 59 top-level nodes; 59 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `ApplyFBCacheOnModel`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoaderGGUF`, `EmptyImage`, `Florence2Run`, `FluxGuidance`, `FluxSamplerParams+`, `ImageResize+`, `ImageToMask`, `ImageUpscaleWithModel`, `ImpactSwitch`, `InpaintModelConditioning`, `IntStaticPrimitive`, `JWImageResizeByFactor`, `JWMaskResize`, `KSampler`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LoadImage`, `LoraLoaderModelOnly`, `LorasForFluxParams+`, `SaveImage`, `StringFunction|pysssss`, `StyleModelApplySimple`, `StyleModelLoader`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`, `easy pipeIn`, `easy pipeOut`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/flux1-dev-Q8_0.gguf`, `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_local_lora16.safetensors`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `FLUXACE/comfyui_subject_lora16.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `face_yolov8m.pt`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 251: CLIPTextEncode, 148: CLIPTextEncode, 204: LoadImage, 288: CLIPTextEncode, 287: CLIPTextEncode, 319: LoadImage, 149: LoadImage.
- 輸出： 269: SaveImage, 306: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI-Easy-Use; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 164 — `Flux/ACELora/【ACE】LoraLoopClothesAndFace.png#workflow`

Embedded sequential clothing/face edits with ACE subject/portrait LoRAs and loop/data-bus nodes. Dangling links remain; loop iteration and restored crop state require explicit validation.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `4bcb5b517e32fd846c46e76a988b76f2ebb3ef315a031734ce03d96cf3045bf4`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `ff2e6fa28bb9d2c28f455719cbbc5102abba4ad16cd8197f61b619ccc7a83583`.
- 圖結構： 34 top-level nodes; 34 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `CR Data Bus In`, `CR Data Bus Out`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `INPAINT_ExpandMask`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LoadImage`, `LoraLoaderModelOnly`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `ToBinaryMask`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `easy anythingIndexSwitch`, `easy forLoopEnd`, `easy forLoopStart`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `FLUXACE/comfyui_subject_lora16.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 59: LoadImage, 148: CLIPTextEncode, 149: LoadImage, 50: LoadImage, 23: CLIPTextEncode.
- 輸出： 197: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-Easy-Use; ComfyUI_Comfyroll_CustomNodes. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `advancedRefluxControl`, `comfy-core`, `comfyroll`, `comfyui-easy-use`, `comfyui-gguf`, `comfyui-impact-pack`, `comfyui-inpaint-nodes`, `comfyui_layerstyle`, `comfyui_layerstyle_advance`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 165 — `Flux/ACELora/【Mixed】FaceAndClothes.png#workflow`

Embedded two-stage face/clothing editing with Flux Fill, ACE portrait/subject LoRAs, Redux and mask crop-back. It passes top-level topology checks but has no public app copy or verified result.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `1783eb560265528693fb1667a0deaf542c59c668a5f1fe8575430b1c66c2aa6b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `5090ac183e6046be35be03727ff6ad417df543766076f498b891d8cd186ae003`.
- 圖結構： 38 top-level nodes; 38 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `INPAINT_ExpandMask`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LoadImage`, `LoraLoaderModelOnly`, `Note`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `ToBinaryMask`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `FLUXACE/comfyui_subject_lora16.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 148: CLIPTextEncode, 23: CLIPTextEncode, 59: LoadImage, 50: LoadImage, 149: LoadImage.
- 輸出： 197: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 166 — `Flux/ACELora/【Mixed】FaceAndClothesLoop.png#workflow`

Embedded looped face/clothing editing variant with index switches and easy loops. The strict validator finds an invalid link slot; loop schemas and negative/special slots need native review before repair.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `a629aa5911e18125ee3d569a9b7a4498839f4558cca9b16c9666b376c8b1d212`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `805d9b986d16bd21de36359007331c36b5faf600041d072c029d74a94fa2f54a`.
- 圖結構： 37 top-level nodes; 37 active expanded nodes. Top-level check: **fail** — invalid link slot.
- 節點類型： `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `INPAINT_ExpandMask`, `ImpactSwitch`, `InpaintModelConditioning`, `KSampler`, `LayerMask: PersonMaskUltra V2`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LoadImage`, `LoraLoaderModelOnly`, `SaveImage`, `StyleModelApplySimple`, `StyleModelLoader`, `ToBinaryMask`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `easy anythingIndexSwitch`, `easy forLoopEnd`, `easy forLoopStart`, `easy int`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_portrait_lora64.safetensors`, `FLUXACE/comfyui_subject_lora16.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 149: LoadImage, 23: CLIPTextEncode, 59: LoadImage, 50: LoadImage, 148: CLIPTextEncode.
- 輸出： 197: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-Easy-Use. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: invalid link slot. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 167 — `Flux/ACELora/【Subject】2PassRedux.png#workflow`

Embedded two-pass subject/clothing editing using ACE LoRA, Redux, depth control and Segformer fashion masks. Requires explicit model and crop restoration checks; output includes mask previews and saved image.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `b291eb4f95e7f25b87ef104788a9afdc4e42b37cbb11d34fcd47d63227093aad`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `c1cc66ea45f03a5a73d140636af0fff9b94c02f32335ab43d81df6ca8dd2ea01`.
- 圖結構： 41 top-level nodes; 41 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DepthAnythingV2Preprocessor`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `GetImageSize+`, `INPAINT_ExpandMask`, `InpaintModelConditioning`, `KSampler`, `LayerMask: MaskEdgeUltraDetail V2`, `LayerMask: SegformerFashionPipelineLoader`, `LayerMask: SegformerUltraV2`, `LayerUtility: CropByMask V2`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LayerUtility: ImageScaleRestore V2`, `LayerUtility: RestoreCropBox`, `LoadImage`, `LoraLoaderModelOnly`, `MaskPreview+`, `PixelPerfectResolution`, `PreviewImage`, `ReduxAdvanced`, `SaveImage`, `StyleModelLoader`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_subject_lora16.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `Flux/flux-depth-controlnet-v3.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `depth_anything_v2_vitl.pth`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 23: CLIPTextEncode, 59: LoadImage, 50: LoadImage.
- 輸出： 195: PreviewImage, 171: PreviewImage, 210: SaveImage, 178: MaskPreview+.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `advancedRefluxControl`, `comfy-core`, `comfyui-gguf`, `comfyui-inpaint-nodes`, `comfyui_controlnet_aux`, `comfyui_essentials`, `comfyui_layerstyle`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 168 — `Flux/ACELora/【Subject】RoomRefImg.png#workflow`

Embedded room/reference object editing using ACE subject LoRA, Florence-grounded SAM2 masks, Flux Fill and Redux. Dangling references remain and the selected segmentation target must be inspected.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `6f2acb1dd2b69dd03efba947c5e793c78808bdb0b03fe1ea5045e545bbc02abe`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `7229e4e899989ebf661e733547ecda37b4a31708d90c0b657ab300ad874a3baf`.
- 圖結構： 41 top-level nodes; 41 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `AddMask`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `ConditioningZeroOut`, `DifferentialDiffusion`, `DownloadAndLoadFlorence2Model`, `DownloadAndLoadSAM2Model`, `DualCLIPLoaderGGUF`, `Florence2Run`, `Florence2toCoordinates`, `FluxGuidance`, `ImageResize+`, `InpaintModelConditioning`, `InvertMask`, `JWMaskResize`, `KSampler`, `LayerUtility: ICMask`, `LayerUtility: ICMaskCropBack`, `LoadImage`, `LoraLoaderModelOnly`, `MaskPreview+`, `Note`, `Sam2Segmentation`, `SaveImage`, `SimpleCondition+`, `SimpleMathBoolean+`, `StringFunction|pysssss`, `StyleModelApplySimple`, `StyleModelLoader`, `TeaCacheForImgGen`, `ToBinaryMask`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-fill-dev-Q8_0.gguf`, `FLUXACE/comfyui_subject_lora16.safetensors`, `Flux/Turbo-flux-8step.safetensors`, `ae.safetensors`, `clip_l.safetensors`, `flux1-redux-dev.safetensors`, `sam2.1_hiera_small.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 148: CLIPTextEncode, 149: LoadImage, 225: LoadImage.
- 輸出： 248: MaskPreview+, 218: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 169 — `Flux/CompareLora.png#workflow`

Embedded Flux LoRA/parameter comparison with PlotParameters and legacy workflow> wrapper syntax. Public group inspection only recognizes workflow/; these definitions require migration or alias-aware expansion.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `3908b200625641544cc25d2308c1c3a2aa4c97727c87283bd8f54e35c97166a0`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `1904a617c7cad4dbfa518446c3a6f76c00ea9dc1e6bd71a7499afecb1479bfad`.
- 圖結構： 7 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `DualCLIPLoaderGGUF`, `FluxSamplerParams+`, `LorasForFluxParams+`, `PlotParameters+`, `PreviewImage`, `SDXLEmptyLatentSizePicker+`, `TextEncodeForSamplerParams+`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `workflow>FluxSampler`, `workflow>GGUFLoader`.
- 明確模型檔名： `FLUX1/Asian_Realistic-Q4_K_S.gguf`, `FLUX1/ae.sft`, `Flux/Asian beauty_flux_V1.safetensors`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 18: TextEncodeForSamplerParams+.
- 輸出： 10: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 170 — `Flux/DetailDaemon/CompareFLUX.png#workflow`

Embedded comparison of Flux DetailDaemon/sigma variants with annotated images and comparator UI. Reciprocal links fail; sigma graph and sampler extensions are part of the experiment.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `f1e5ba925d18f024a4a96fc19f5ad1bb8da81c6e8bdd0bfbb1ff1072e12929a8`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `0186f53cd1d2d455c54453cb758c6afa7a19a1f948405296f4e2b45b750dfbeb`.
- 圖結構： 37 top-level nodes; 31 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncodeFlux`, `DetailDaemonGraphSigmasNode`, `DetailDaemonSamplerNode`, `DualCLIPLoaderGGUF`, `EmptyLatentImage`, `FL_ImageNotes`, `Image Comparer (rgthree)`, `ImageConcatMulti`, `KSamplerSelect`, `LyingSigmaSampler`, `MultiplySigmas`, `PreviewImage`, `RandomNoise`, `SamplerCustomAdvanced`, `Text List`, `Text List to Text`, `Text Multiline`, `Text Random Line`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-dev-Q8_0.gguf`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 112: Text Multiline, 114: Text Multiline, 38: CLIPTextEncodeFlux.
- 輸出： 67: PreviewImage, 37: PreviewImage, 79: PreviewImage [mode 4], 82: PreviewImage, 105: PreviewImage.
- 相依套件： ComfyUI-GGUF; rgthree-comfy; ComfyUI_Fill-Nodes; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 171 — `Flux/DetailDaemon/SDXLFLUX.png#workflow`

Embedded side-by-side SDXL and Flux DetailDaemon sampling. Different encoders/models are intentionally used, so comparison controls must be fixed before quality interpretation.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `ddc1664188f78c7ffb1f1b3e869c8af0c7a28f4f56b5c49deba8515048ed9374`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `8739d5f7c09c6c07265910e0119a11a5bff9181b3f8a049bbcfe17e726acdd5c`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CFGGuider`, `CLIPTextEncode`, `CLIPTextEncodeFlux`, `CheckpointLoaderSimple`, `DetailDaemonSamplerNode`, `DualCLIPLoader`, `EmptyLatentImage`, `KSamplerSelect`, `ModelSamplingFlux`, `PreviewImage`, `PrimitiveNode`, `RandomNoise`, `SamplerCustomAdvanced`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-dev-Q8_0.gguf`, `SD3/clip_g.safetensors`, `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `ae.safetensors`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 38: CLIPTextEncodeFlux, 104: CLIPTextEncode, 105: CLIPTextEncode.
- 輸出： 37: PreviewImage, 112: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 172 — `Flux/DetailDaemon/SimpleFLUX.png#workflow`

Embedded minimal Flux DetailDaemon sampler with GGUF models. A reciprocal-link inconsistency remains; no public app copy is present.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `5bdddf2535939d9772c82f2f0a377001db93ce3d2d5212b49e2f7425de126574`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `b011f10c6bed43fb7f6c9d06614dc827dbfc8d7f60135af56b9d84dae45958dd`.
- 圖結構： 15 top-level nodes; 15 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncodeFlux`, `DetailDaemonSamplerNode`, `DualCLIPLoaderGGUF`, `EmptyLatentImage`, `KSamplerSelect`, `ModelSamplingFlux`, `PrimitiveNode`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-dev-Q8_0.gguf`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 38: CLIPTextEncodeFlux.
- 輸出： 118: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 173 — `Flux/FLUXreduxCustomnode.png#workflow`

Embedded two-reference Flux Redux generation with Florence captioning and prompt lists. Requires Redux/SigLIP and GGUF stacks; reference combination behavior is not established by its saved output metadata.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `0e75d30fb8731ece6eff0d84520365bde21ebccafb6f8ba20ae2dfb58d4268a9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `8ee5e2bd5dd86f44040cb2fb63cd82a4bed651a9707044d450a6102efa3acf25`.
- 圖結構： 27 top-level nodes; 27 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `CLIPVisionEncode`, `CLIPVisionLoader`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoaderGGUF`, `Florence2Run`, `FluxGuidance`, `KSamplerSelect`, `LoadImage`, `ModelSamplingFlux`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SamplerCustomAdvanced`, `SaveImage`, `StyleModelApply`, `StyleModelLoader`, `Text List`, `Text List to Text`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/flux1-dev-Q8_0.gguf`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `flux1-redux-dev.safetensors`, `sigclip_vision_patch14_384.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 6: CLIPTextEncode, 40: LoadImage, 54: LoadImage.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 174 — `Flux/【FLUX】Majicflus.png#workflow`

Embedded MajicFlux generation with style LoRAs, DetailDaemon and high-resolution refinement. Requires the custom checkpoint/LoRAs plus upscaler; no public library integration exists.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `7cd19c3d7ef411ea109eeed65d1f06fc85bdbec08878c534827bf43ece471e66`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `3e9774d22c9c660e9117e3ce7288a8caa007c111f59d8f3c8044e1c27532190b`.
- 圖結構： 22 top-level nodes; 22 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `DetailDaemonSamplerNode`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `FluxSamplerParams+`, `ImageResize+`, `ImageUpscaleWithModel`, `KSamplerSelect`, `ModelSamplingFlux`, `Power Lora Loader (rgthree)`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SamplerCustomAdvanced`, `SaveImage`, `UNETLoader`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `Flux/FLUXMaji/al.24.safetensors`, `Flux/midjourney_whisper_flux_lora_v01.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `ae.safetensors`, `majicflus_v10.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 45: CLIPTextEncode.
- 輸出： 2: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials; rgthree-comfy; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 175 — `Flux/【FLUX】OpenArtPeguinAIGirlFix.png#workflow`

Embedded 121-node Flux portrait pipeline using PuLID, depth, noise/latent refinement, face detection, TTP tiling and color/film treatments. Multiple previews and a chooser support manual review; it is not a turnkey identity-guaranteed portrait feature.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `188fdc3496d431492ca0a3edf4ba69ada9b816f71bbfff275bbc82f07851c4b7`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `991b251ba634c42583c379951aca4a7b083ccb0137dd5f2dcb487e775b2342c9`.
- 圖結構： 121 top-level nodes; 114 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Any Switch (rgthree)`, `ApplyPulidFlux`, `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `ConditioningZeroOut`, `DepthAnythingPreprocessor`, `DisableNoise`, `DualCLIPLoader`, `EmptyLatentImage`, `FaceDetailer`, `FluxGuidance`, `GetNode`, `ImageApplyLUT+`, `ImageScaleToTotalPixels`, `ImageUpscaleWithModel`, `ImpactImageBatchToImageList`, `InjectLatentNoise+`, `KSamplerSelect`, `LatentInterpolate`, `LayerFilter: FilmV2`, `LayerUtility: TextBox`, `LoadImage`, `ModelSamplingFlux`, `Preview Chooser`, `PreviewImage`, `PrimitiveNode`, `PulidFluxEvaClipLoader`, `PulidFluxInsightFaceLoader`, `PulidFluxModelLoader`, `RandomNoise`, `SAMLoader`, `SamplerCustomAdvanced`, `SaveImage`, `Seed Everywhere`, `SetNode`, `SplitSigmasDenoise`, `TTP_Image_Assy`, `TTP_Image_Tile_Batch`, `TTP_Tile_image_size`, `UNETLoader`, `UltralyticsDetectorProvider`, `UpscaleModelLoader`, `VAEDecode`, `VAEDecodeTiled`, `VAEEncode`, `VAELoader`, `easy cleanGpuUsed`, `easy clearCacheAll`, `easy int`.
- 明確模型檔名： `4x_NMKD-Siax_200k.pth`, `FLUX1/ae.sft`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `bbox/face_yolov8m.pt`, `depth_anything_vitl14.pth`, `majicflus_v10.safetensors`, `pulid_flux_v0.9.1.safetensors`, `sam_vit_b_01ec64.pth`, `t5xxl_fp8_e4m3fn_scaled.safetensors`.
- 輸入： 1: CLIPTextEncode, 71: LoadImage.
- 輸出： 122: PreviewImage, 126: PreviewImage, 127: PreviewImage, 121: PreviewImage, 136: SaveImage, 137: Preview Chooser.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI-KJNodes / related version-specific extensions; ComfyUI_essentials; rgthree-comfy; ComfyUI TTP Toolset; ComfyUI-Easy-Use; PuLID/PuLID-Flux and matching InsightFace/EVA dependencies. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `cg-image-picker`, `cg-use-everywhere`, `comfy-core`, `comfyui-easy-use`, `comfyui-impact-pack`, `comfyui-impact-subpack`, `comfyui_controlnet_aux`, `comfyui_essentials`, `comfyui_layerstyle`, `comfyui_ttp_toolset`, `rgthree-comfy`, `zhangp365/ComfyUI-PuLID-Flux`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 176 — `Flux/【FLUX】Res2MFLUX.png#workflow`

Embedded Flux sampling experiment with DetailDaemon and an amateur-photo LoRA. The sampler is selected through KSamplerSelect; compatibility must be checked against the installed extension.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `28c839c753fe7581e343fb56600e96d4368d5fc1dd8f63a44d5729462fcc7e1f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `00d5572f072d0ff0d997e355d2ffde4668ae92618bd1a40e5d5a10458d1a4d05`.
- 圖結構： 16 top-level nodes; 16 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncodeFlux`, `DetailDaemonSamplerNode`, `DualCLIPLoaderGGUF`, `KSamplerSelect`, `LoraLoaderModelOnly`, `ModelSamplingFlux`, `PrimitiveNode`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SamplerCustomAdvanced`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q8_0.gguf`, `Flux/amateurphoto-v6-forcu.safetensors`, `SD3/clip_l.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 11: CLIPTextEncodeFlux.
- 輸出： 2: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials; ComfyUI-Detail-Daemon. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 177 — `Flux/【FLUX】SimpleTTP.png#workflow`

Embedded Flux image tile refinement using Florence captions, TTP assembly, TeaCache and Turbo LoRA. Auxiliary caption/cache behavior and tile boundaries need runtime validation.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `8260ae4161e02111019e5b6074859cd26220f4310a219b2ec1cfb0f76190ea17`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `0fed2ab5471b87f6841d0a6a720b8950233fd63f1af97c8b99f80812efad9a9d`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CLIPTextEncode`, `DownloadAndLoadFlorence2Model`, `DualCLIPLoaderGGUF`, `Florence2Run`, `ImageScaleBy`, `ImageUpscaleWithModel`, `KSamplerSelect`, `LoadImage`, `LoraLoaderModelOnly`, `Note`, `RandomNoise`, `SamplerCustomAdvanced`, `SaveImage`, `TTP_Image_Assy`, `TTP_Image_Tile_Batch`, `TTP_Tile_image_size`, `TeaCache`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecodeTiled`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4x_NMKD-Siax_200k.pth`, `FLUX1/ae.sft`, `Flux/Turbo-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `atomixFluxGGUF_q8.gguf`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 21: CLIPTextEncode, 15: LoadImage.
- 輸出： 50: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI TTP Toolset. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-florence2`, `comfyui-gguf`, `comfyui_ttp_toolset`, `teacache`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 178 — `Flux/【FLUX】TileSEGS.png#workflow`

Embedded Flux tile/face detailing using Impact SEGS, SAM/YOLO and color matching after upscaling. Source image is the main input; multiple region refinements can alter identity/detail.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `ba65328473d41f0ec0ea6dabb9e49e73540dee342c5de729aa1b1d82e4c2bcc1`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `29eb46967264475838fab40aa738ddf6f7d132f02a5c92ccec32db9076a2d02d`.
- 圖結構： 21 top-level nodes; 21 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ColorMatch`, `ConditioningZeroOut`, `DetailerForEachPipe`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `ImageResize+`, `ImageUpscaleWithModel`, `ImpactMakeTileSEGS`, `ImpactSimpleDetectorSEGS`, `LoadImage`, `LoraLoaderModelOnly`, `SAMLoader`, `SEGSPreview`, `SaveImage`, `TeaCache`, `ToBasicPipe`, `UltralyticsDetectorProvider`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/ae.sft`, `Flux/Turbo-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `atomixFluxGGUF_q8.gguf`, `bbox/face_yolov8m.pt`, `sam_vit_b_01ec64.pth`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 8: CLIPTextEncode, 17: LoadImage.
- 輸出： 49: SaveImage, 50: SEGSPreview.
- 相依套件： ComfyUI-GGUF; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-gguf`, `comfyui-impact-pack`, `comfyui-impact-subpack`, `comfyui-kjnodes`, `comfyui_essentials`, `teacache`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 179 — `Flux/【FLUX】TiledSEGS2X.png#workflow`

Embedded two-times Flux tile-detail variant with explicit resize/scale controls and optional face group. Inspect bypass state and tile coverage before claiming a fixed output size.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `581aaa646adab8bd414fc2e8ef40746fde965ed9eb70c67c0f4b40e737f437eb`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `b5c1d4e15b5a0f00fff0236aca4de080a1e57a3afdc0c7e008a3838413608cbf`.
- 圖結構： 26 top-level nodes; 23 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `ColorMatch`, `ConditioningZeroOut`, `DetailerForEachPipe`, `DualCLIPLoaderGGUF`, `Fast Groups Bypasser (rgthree)`, `FluxGuidance`, `GetImageSize+`, `ImageResize+`, `ImageScaleBy`, `ImageUpscaleWithModel`, `ImpactMakeTileSEGS`, `ImpactSimpleDetectorSEGS`, `LoadImage`, `LoraLoaderModelOnly`, `Note`, `SAMLoader`, `SEGSPreview`, `SaveImage`, `TeaCache`, `ToBasicPipe`, `UltralyticsDetectorProvider`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/ae.sft`, `Flux/Turbo-flux-8step.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `atomixFluxGGUF_q8.gguf`, `bbox/face_yolov8m.pt`, `sam_vit_b_01ec64.pth`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 8: CLIPTextEncode, 17: LoadImage.
- 輸出： 50: SEGSPreview, 49: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_essentials; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-gguf`, `comfyui-impact-pack`, `comfyui-impact-subpack`, `comfyui-kjnodes`, `comfyui_essentials`, `teacache`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 180 — `Flux/【Flux】360Workflow.png#workflow`

Embedded panoramic Flux/SDXL pipeline with seamless tiling, circular VAE, outpaint/inpaint and person-region refinement. Multiple model families and 360-specific LoRAs are required; seam quality is unverified.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `157f457fdb86953176bd002274222179a8b91e1a3d5604eed2a5446addb1bb3f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `12fd93efe262660c40a1c1c1040b206606b6837bf9a8264ccb28c3d6c192bfcb`.
- 圖結構： 59 top-level nodes; 46 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AdaptiveGuidance`, `BasicGuider`, `BasicScheduler`, `CLIPTextEncodeFlux`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `DynamicThresholdingFull`, `Fast Groups Bypasser (rgthree)`, `ImagePadForOutpaint`, `ImageResize+`, `InpaintModelConditioning`, `InpaintPreprocessor`, `KSampler`, `KSampler //Inspire`, `KSamplerSelect`, `LayerMask: MaskEdgeUltraDetail`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask V2`, `LayerUtility: ImageScaleRestore V2`, `LayerUtility: PurgeVRAM`, `LayerUtility: RestoreCropBox`, `LayerUtility: SaveImagePlus`, `LoraLoaderModelOnly`, `MakeCircularVAE`, `ModelSamplingFlux`, `Note`, `PreviewImage`, `Prompts`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SDXLPromptStylerAll`, `SamplerCustomAdvanced`, `SaveImage`, `SeamlessTile`, `SelectNthMask //Inspire`, `SetUnionControlNetType`, `SimpleCondition+`, `SimpleMathBoolean+`, `UltimateSDUpscale`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `4xNomosWebPhoto_RealPLKSR.pth`, `FLUX1/Asian_Realistic-Q4_K_S.gguf`, `FLUX1/ae.sft`, `Flux/360HDR.safetensors`, `Flux/Hyper-FLUX.1-dev-8steps-lora.safetensors`, `Flux/midjourney_whisper_flux_lora_v01.safetensors`, `SDXL/360RedmondResized.safetensors`, `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `aiartistHyperrayXLXL_hypersdxl.safetensors`, `person_yolov8m-seg.pt`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 70: Prompts, 88: CLIPTextEncodeFlux, 87: CLIPTextEncodeFlux, 2: Prompts, 96: SDXLPromptStylerAll.
- 輸出： 27: PreviewImage, 86: SaveImage, 83: LayerUtility: SaveImagePlus.
- 相依套件： ComfyUI-GGUF; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_essentials; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 181 — `Flux/【Flux】AdaptiveGuidance.png#workflow`

Embedded Flux AdaptiveGuidance/dynamic-thresholding comparison with styled prompts. Dangling nodes prevent strict acceptance of the saved graph.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `5b10ceb4748deb1cffbf688dbc123c2fa0e6bd06be5bae04fa7dc7e31d610367`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `14a956d38d1a40dcab1ce887aef0d9ffce4afb4970f92888decfaf7046fffd17`.
- 圖結構： 19 top-level nodes; 19 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `AdaptiveGuidance`, `BasicScheduler`, `CFGGuider`, `CLIPTextEncodeFlux`, `DualCLIPLoaderGGUF`, `DynamicThresholdingFull`, `EmptyLatentImage`, `KSamplerSelect`, `RandomNoise`, `SDXLPromptStylerAll`, `SamplerCustomAdvanced`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q8_0.gguf`, `SD3/clip_l.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 33: CLIPTextEncodeFlux, 34: CLIPTextEncodeFlux, 88: SDXLPromptStylerAll.
- 輸出： 96: SaveImage, 9: SaveImage.
- 相依套件： ComfyUI-GGUF. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 182 — `Flux/【Flux】AreaInpainting.png#workflow`

Embedded masked-area Flux inpainting with crop/scale restoration, custom VAEDecode and a grouped GGUF loader. Group definitions and mask boundaries need native migration/review.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `c60e8d66608afffc6f709fbd722c793f06bc0e47c2dc4883877835af347043c7`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `d13af09e304706ae9ebcf3d7532917f21082547868ee051ef83b67b6a5497d5f`.
- 圖結構： 17 top-level nodes; 19 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Anything Everywhere3`, `CLIPTextEncode`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `InpaintModelConditioning`, `KSampler`, `LayerMask: MaskEdgeUltraDetail`, `LayerUtility: CropByMask V2`, `LayerUtility: ImageScaleRestore V2`, `LayerUtility: RestoreCropBox`, `LoadImage`, `PreviewImage`, `UnetLoaderGGUF`, `VAEDecode_party`, `VAELoader`, `workflow/GGUFLoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q4_K_S.gguf`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 70: CLIPTextEncode, 69: CLIPTextEncode, 1: LoadImage.
- 輸出： 68: PreviewImage, 71: PreviewImage, 88: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 183 — `Flux/【Flux】CompareByparms.png#workflow`

Embedded Flux parameter grid using PlotParameters and grouped sampler/GGUF loader. It is a comparative sweep, not a single stable parameter recipe.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `b021999d2f5146c44bbac3984079046ba91143891b06d389848844c470eb9a9a`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `a96863ec68409062b0ee6d37361d5f1f9eb8b47dfcbe297175a1b926509da724`.
- 圖結構： 7 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `DualCLIPLoaderGGUF`, `FluxSamplerParams+`, `LorasForFluxParams+`, `PlotParameters+`, `PreviewImage`, `SDXLEmptyLatentSizePicker+`, `TextEncodeForSamplerParams+`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `workflow/FluxSampler`, `workflow/GGUFLoader`.
- 明確模型檔名： `FLUX1/Asian_Realistic-Q4_K_S.gguf`, `FLUX1/ae.sft`, `Flux/Hyper-FLUX.1-dev-8steps-lora_rank1.safetensors`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 18: TextEncodeForSamplerParams+.
- 輸出： 10: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 184 — `Flux/【Flux】DetailerCFGBasic.png#workflow`

Embedded Flux detail/CFG experiment using latent upscaling, injected noise, split sigmas and Skimmed CFG. The legacy GGUF group and custom sampler helpers are required.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `426d1912434a1599549a00673d95ec4b9b76c39980dacbd83f79d56d2e3caf07`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `dd0aafa1e05e651c2e3cac71d360c0f43d7afe484d1f3721318f2478c857873a`.
- 圖結構： 17 top-level nodes; 19 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicScheduler`, `DualCLIPLoaderGGUF`, `FluxGuidance`, `FluxSamplerParams+`, `InjectLatentNoise+`, `KSamplerSelect`, `LatentUpscaleBy`, `ModelSamplingFlux`, `PreviewImage`, `Prompts`, `SDXLEmptyLatentSizePicker+`, `SamplerCustom`, `Skimmed CFG`, `SplitSigmasDenoise`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `workflow/GGUFLoader`.
- 明確模型檔名： `FLUX1/Asian_Realistic-Q4_K_S.gguf`, `FLUX1/ae.sft`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 33: Prompts.
- 輸出： 10: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 185 — `Flux/【Flux】Dev8Step.png#workflow`

Embedded accelerated Flux Dev eight-step generation with a Hyper-FLUX LoRA and sampler-parameter helpers. Exact base/LoRA/step compatibility has not been executed.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `e9ed18b672fb104ae2a314205879f4e54730646ba7adfcb3caa99ac1357505b3`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `652163a08c30a9eda59e6578ce8a6d6c677472b864bea24a2ab5e4a3abb18fdd`.
- 圖結構： 10 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `DualCLIPLoaderGGUF`, `EmptyLatentImage`, `FluxSamplerParams+`, `LorasForFluxParams+`, `Note`, `PreviewImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q4_K_S.gguf`, `Flux/Hyper-FLUX.1-dev-8steps-lora.safetensors`, `clip_l.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 10: CLIPTextEncode.
- 輸出： 12: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 186 — `Flux/【Flux】LineArtRender.png#workflow`

Embedded Flux line/depth-controlled rendering followed by upscaling/refinement. A stale output link remains; distinct eight/sixteen-step LoRA branches must be reviewed.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `bd175ad8041a130a0ef8ab8a940d09edd17a2a5e6db935b66fa26b87cce93dbd`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `a7016a07511ae1fc0b2b1cac9f55647e78400c53b67f9849ea432ab61bb758ee`.
- 圖結構： 31 top-level nodes; 31 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `AnyLineArtPreprocessor_aux`, `CLIPTextEncode`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DepthAnythingV2Preprocessor`, `DualCLIPLoaderGGUF`, `EmptyLatentImage`, `FluxSamplerParams+`, `GetImageSize+`, `ImageResize+`, `ImageUpscaleWithModel`, `JWImageResizeByLongerSide`, `LoadImage`, `LorasForFluxParams+`, `Note`, `PixelPerfectResolution`, `PreviewImage`, `Reroute`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`.
- 明確模型檔名： `4xRealWebPhoto_v4_dat2.pth`, `FLUX1/RealFlux_1.0b_Dev-Q8_0.gguf`, `FLUX1/ae.sft`, `Flux/Hyper-FLUX.1-dev-16steps-lora.safetensors`, `Flux/Hyper-FLUX.1-dev-8steps-lora.safetensors`, `Flux/flux-canny-controlnet-v3.safetensors`, `Flux/flux-depth-controlnet-v3.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `depth_anything_v2_vitl.pth`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 164: CLIPTextEncode, 163: CLIPTextEncode, 43: LoadImage.
- 輸出： 134: PreviewImage, 151: PreviewImage.
- 相依套件： ComfyUI-GGUF; comfyui_controlnet_aux; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 187 — `Flux/【Flux】LoraCompare.png#workflow`

Embedded Flux LoRA comparison grid with shared seed and comic/image-note layout. A dangling reference remains, plus implicit routing and grouped loader/size logic.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `c69dfe512acef98f2cf69de82fb292fef9fa9923b11bcd9595b88d33d6db2934`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `a21317fae3b23324fff236695228f40b575ed044f45563d5de389ab0650fda8f`.
- 圖結構： 31 top-level nodes; 36 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `Anything Everywhere`, `Anything Everywhere3`, `CR Comic Panel Templates`, `DualCLIPLoaderGGUF`, `FL_ImageNotes`, `FluxGuidance`, `ImageListToBatch_`, `ImpactMakeImageBatch`, `ImpactSwitch`, `JWStringToInteger`, `KSampler`, `LoraLoaderModelOnly`, `ModelSamplingFlux`, `PreviewImage`, `Prompts`, `SDXLEmptyLatentSizePicker+`, `Seed Everywhere`, `SimpleMath+`, `TextSplitByDelimiter`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `WidgetToString`, `workflow/GGUFLoader`, `workflow/SizeSeedLatent`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q8_0.gguf`, `Flux/Asian beauty_flux_V1.safetensors`, `Flux/Hyper-FLUX.1-dev-8steps-lora_rank1.safetensors`, `Flux/Neon_Cyberpunk_Splash_Art_FLUX.safetensors`, `Flux/ZX_SpectrumFlux.safetensors`, `Flux/boreal-flux-dev-lora-v04_1000_steps.safetensors`, `Flux/scg-anatomy-female-5000.safetensors`, `SD3/clip_l.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 148: Prompts.
- 輸出： 176: PreviewImage, 175: PreviewImage, 115: PreviewImage.
- 相依套件： ComfyUI-GGUF; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_essentials; ComfyUI_Fill-Nodes; ComfyUI_Comfyroll_CustomNodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 188 — `Flux/【Flux】Schnell8StepHiresfix.png#workflow`

Embedded Flux Schnell/Dev high-resolution comparison using latent upscale/noise injection, Skimmed CFG and annotations. Two model families/quantizations are present; branch-specific settings matter.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `87c04883d7f9c981213421a1feca8842824240790e5d35329ad6d0b681d6dca6`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2138ee13f0beb5135380dd0d207c09eb2175721354076ba08fc0357a42c875cc`.
- 圖結構： 42 top-level nodes; 37 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BasicGuider`, `BasicScheduler`, `CFGGuider`, `CLIPTextEncodeFlux`, `DisableNoise`, `DualCLIPLoaderGGUF`, `FL_ImageNotes`, `Fast Groups Bypasser (rgthree)`, `ImageConcanate`, `InjectLatentNoise+`, `KSampler`, `KSamplerSelect`, `LatentUpscaleBy`, `LayerUtility: SaveImagePlus`, `LoraLoaderModelOnly`, `ModelSamplingFlux`, `PreviewImage`, `PrimitiveNode`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SamplerCustomAdvanced`, `SaveImage`, `SimpleMath+`, `Skimmed CFG`, `SplitSigmasDenoise`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`, `workflow/GGUFLoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q8_0.gguf`, `FLUX1/flux1-schnell-Q4_K_S.gguf`, `Flux/hinaFluxAsianMixLora-schnell_v1.safetensors`, `SD3/clip_l.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 16: CLIPTextEncodeFlux, 11: CLIPTextEncodeFlux.
- 輸出： 33: SaveImage, 73: LayerUtility: SaveImagePlus [mode 4], 69: PreviewImage [mode 4].
- 相依套件： ComfyUI-GGUF; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; rgthree-comfy; ComfyUI_Fill-Nodes. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 189 — `Flux/【Flux】Upscaler.png#workflow`

Embedded Flux upscaling through an upscaler ControlNet and Hyper-FLUX LoRA, with Florence captioning. It is image-conditioned generation rather than a pure deterministic resize.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `46aacad9b4093800f32aa9a47727b20699d66381f772f4fac6ce29f5f9b1009b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `1cb313df9b8a178b939a249b6f5c86f4dbfeb34d09af3e200f3ebe712ea0b42a`.
- 圖結構： 20 top-level nodes; 20 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPTextEncodeFlux`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DualCLIPLoaderGGUF`, `Florence2ModelLoader`, `Florence2Run`, `GetImageSize+`, `ImageCASharpening+`, `ImageScaleBy`, `KSampler`, `LoadImage`, `LoraLoaderModelOnly`, `ModelSamplingFlux`, `Reroute`, `SDXLEmptyLatentSizePicker+`, `SaveImage`, `UnetLoaderGGUF`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `FLUX1/ae.sft`, `FLUX1/flux1-dev-Q4_K_S.gguf`, `Flux/Hyper-FLUX.1-dev-8steps-lora.safetensors`, `Flux/upscaler.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 43: CLIPTextEncodeFlux, 44: CLIPTextEncode, 15: LoadImage.
- 輸出： 40: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 190 — `SD3/【SD35】BasicWorkflow.png#workflow`

Embedded SD3.5 text-to-image with TripleCLIP GGUF loading and SD3-specific negative conditioning. Requires three compatible text encoders and the correct SD3.5 checkpoint.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `54d20c206e6918014dfd6b36bef36753753cd90ddd094878a6f11f5a21714141`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `ac734277b3c15a29be830e57ddc14aab70f5af1f229c84756f0cbae59f3102a6`.
- 圖結構： 10 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptySD3LatentImage`, `KSampler`, `ModelSamplingSD3`, `SD3NegativeConditioning+`, `SaveImage`, `TripleCLIPLoaderGGUF`, `VAEDecode`.
- 明確模型檔名： `SD3/clip_g.safetensors`, `SD3/sd3.5_large_fp8_scaled.safetensors`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `t5/t5-v1_1-xxl-encoder-Q8_0.gguf`.
- 輸入： 40: CLIPTextEncode, 16: CLIPTextEncode.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI-GGUF; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 191 — `SDXL/【SDXL】StyleLoop.png#workflow`

Embedded SDXL style loop with PuLID identity and labeled/composited image sheets. Loop boundaries, style list and identity weights need review; no public graph copy exists.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `1467d5f7aaaf6e7527c7ef9e082cb4e822f55c41f85608d21de3205f8c5fc797`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `05c508968860d9f0a4cab2b4733e6a95e129b20360847bc388e923d61493db65`.
- 圖結構： 27 top-level nodes; 27 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ApplyPulidAdvanced`, `CLIPTextEncode`, `CR Draw Shape`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `ImageConcatFromBatch`, `ImageToMask`, `KSampler`, `LayerStyle: DropShadow`, `LayerUtility: ColorImage V2`, `LayerUtility: ImageBlendAdvance`, `LayerUtility: SimpleTextImage`, `LoadImage`, `PulidEvaClipLoader`, `PulidInsightFaceLoader`, `PulidModelLoader`, `RandomGeneratorForList //Inspire`, `SDXLPromptStylerAll`, `SaveImage`, `String Literal`, `StringFunction|pysssss`, `VAEDecode`, `easy batchAnything`, `easy forLoopEnd`, `easy forLoopStart`.
- 明確模型檔名： `dreamshaperXL_lightningDPMSDE.safetensors`, `ip-adapter_pulid_sdxl_fp16.safetensors`.
- 輸入： 36: LoadImage, 74: LayerUtility: SimpleTextImage, 3: SDXLPromptStylerAll, 8: CLIPTextEncode, 46: CLIPTextEncode, 86: String Literal.
- 輸出： 81: SaveImage.
- 相依套件： ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-Easy-Use; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts; PuLID/PuLID-Flux and matching InsightFace/EVA dependencies. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 192 — `SpecialUsage/ACEAuto.png#workflow`

Embedded ACE-Step music generation with a Griptape Google-provider agent preparing text. Dangling graph references remain, and explicit provider credentials are required for the agent branch.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `9f9ed51d2ae07fa2d209f64336ec16d86b996190b2ff9aa19aefeb1bb3d6dc03`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `4c5d5831d0783bed15bf7d80a170afb210255ffc3efb974e4fad357e73966b7d`.
- 圖結構： 15 top-level nodes; 15 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `CheckpointLoaderSimple`, `ConditioningZeroOut`, `EmptyAceStepLatentAudio`, `Griptape Agent Config: Google Drivers`, `Griptape Create: Agent from Config`, `Griptape Run: Agent`, `KSampler`, `ModelSamplingSD3`, `SaveAudio`, `StringFunction|pysssss`, `Text Multiline`, `TextEncodeAceStepAudio`, `TextSplit`, `VAEDecodeAudio`.
- 明確模型檔名： `ace_step_v1_3.5b.safetensors`.
- 輸入： 14: TextEncodeAceStepAudio, 80: Text Multiline.
- 輸出： 19: SaveAudio.
- 相依套件： ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `chibi`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-griptape`, `pr-was-node-suite-comfyui-47064894`.
- 供應商／橋接需求： Griptape agent/provider configuration; Google driver node requires explicitly configured provider credentials.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 193 — `SpecialUsage/CLIPLAndControlnet.png#workflow`

Embedded SD1.5 rendering with a custom long CLIP-L encoder, canny control and latent upscaling. The altered encoder/checkpoint pairing needs qualification.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `c471193393487e51f735a687e919d3330d3e2dc861d60995362f035ade009221`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `d384f5ae8acce07ef5b2fa9ffa5fcf27376987b4e6dad99a2ecb57b76601a020`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `CannyEdgePreprocessor`, `CheckpointLoaderSimple`, `ControlNetApply`, `ControlNetLoader`, `EmptyLatentImage`, `GetImageSizeAndCount`, `JWImageResizeByFactor`, `KSampler`, `LatentUpscaleBy`, `LoadImage`, `PreviewImage`, `SaveImage`, `VAEDecode`.
- 明確模型檔名： `Long-ViT-L-14-BEST-GmP-smooth-ft-state_dict.pt`, `SD15/11_v10.safetensors`, `control_v11p_sd15_canny.pth`.
- 輸入： 6: CLIPTextEncode, 7: CLIPTextEncode, 11: LoadImage.
- 輸出： 20: PreviewImage, 9: SaveImage.
- 相依套件： comfyui_controlnet_aux. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 194 — `SpecialUsage/CLIPLAndDeep.png#workflow`

Embedded SD1.5 generation with long CLIP-L and model downscale patching. It demonstrates encoder/sampling experimentation, not a generic model-independent pipeline.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `9a81d80115770eed47351b7b22cd09e5c38df443de3295fc024b88a8f10c25c9`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `085fb3c413de08fc870c3642e858a7b723f9e9180fe33628b92a0db4bdcbf31e`.
- 圖結構： 10 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPSetLastLayer`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler`, `PatchModelAddDownscale`, `SaveImage`, `VAEDecode`.
- 明確模型檔名： `Long-ViT-L-14-BEST-GmP-smooth-ft-state_dict.pt`, `SD15/11_v10.safetensors`.
- 輸入： 3: CLIPTextEncode, 4: CLIPTextEncode.
- 輸出： 9: SaveImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 195 — `SpecialUsage/EasyUSeOpenpose.png#workflow`

Embedded multi-pose character generation via EasyUse OpenPose/IPAdapter, StyleAligned and detailer/hires wrappers. Several images are batched as references; wrapper schemas hide dependencies that standard-loader checks miss.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `b09eb616700ab217c6d6e9314ece082925b50f576baca14c3cef4537603b363b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `fde446b523f65ff3858a2caaa39bdd4ce7acc39421837fef7903b706f689b61d`.
- 圖結構： 29 top-level nodes; 29 active expanded nodes. Top-level check: **pass**.
- 節點類型： `FreeU_V2`, `ImageConcatFromBatch`, `ImpactMakeImageBatch`, `LoadImage`, `SaveImage`, `StringFunction|pysssss`, `WD14Tagger|pysssss`, `easy comfyLoader`, `easy controlnetLoader`, `easy detailerFix`, `easy fullkSampler`, `easy hiresFix`, `easy imageCount`, `easy imageListToImageBatch`, `easy ipadapterApplyADV`, `easy preDetailerFix`, `easy samLoaderPipe`, `easy styleAlignedBatchAlign`, `easy ultralyticsDetectorPipe`.
- 明確模型檔名： `4x-UltraSharp.pth`, `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `henmixReal_v6b.safetensors`, `hypersd/Hyper-SD15-12steps-CFG-lora.safetensors`, `sam_vit_b_01ec64.pth`, `vae-ft-mse-840000-ema-pruned.safetensors`.
- 輸入： 19: LoadImage, 21: LoadImage, 22: LoadImage, 34: LoadImage, 7: LoadImage, 20: LoadImage, 33: LoadImage.
- 輸出： 47: SaveImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Easy-Use; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-custom-scripts`, `comfyui-easy-use`, `comfyui-impact-pack`, `comfyui-kjnodes`, `comfyui-wd14-tagger`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 196 — `SpecialUsage/HairStyleBook/BrushNetHairStyle.png#workflow`

Embedded hairstyle comparison using SDXL BrushNet, person masks and random text prompts. Hair-mask correctness and compatible SDXL BrushNet assets are required; output panels are previews.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `1ad30ade73530147ac69ba3bd92feb66dc3795862e1ff4e3801a43975e44aa67`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `40f064a10ce97e975f659bdf5aaa9c37664ecb1aedace9f477fb9e29f4953081`.
- 圖結構： 24 top-level nodes; 24 active expanded nodes. Top-level check: **pass**.
- 節點類型： `BrushNet`, `BrushNetLoader`, `CLIPTextEncode`, `CR Comic Panel Templates`, `CR Text Concatenate`, `CheckpointLoaderSimple`, `EmptyImage`, `ImageListToBatch+`, `JWImageResizeByLongerSide`, `KSampler`, `LayerMask: PersonMaskUltra`, `LayerStyle: DropShadow`, `LayerUtility: ImageBlendAdvance`, `LayerUtility: SimpleTextImage`, `LayerUtility: TextBox`, `LoadImage`, `MaskPreview+`, `PreviewImage`, `RandomNoise`, `RandomPrompt`, `ShowText|pysssss`, `VAEDecode`.
- 明確模型檔名： `Brushnet_XL/random_mask_brushnet_ckpt_sdxl_v0/diffusion_pytorch_model.safetensors`, `SDXL/realvisxlV50_v50LightningBakedvae.safetensors`.
- 輸入： 243: CLIPTextEncode, 111: LayerUtility: SimpleTextImage, 138: LoadImage, 219: RandomPrompt, 242: CLIPTextEncode.
- 輸出： 188: PreviewImage, 220: ShowText|pysssss, 236: PreviewImage, 239: MaskPreview+.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 197 — `SpecialUsage/HairStyleBook/KolorHairStyle.png#workflow`

Embedded Kolors hairstyle inpainting with ChatGLM, translation and person-mask comparison panels. Requires an inpainting Kolors model and selected mask/caption review.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `aec3740c42be849f4d6b055865deefc80e3e70644718a421299cb7f5be3c4f64`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `fe094cefcbc05fb78c648f221fbe4c66f2412899f0ce115ccb0544a5bed6c5c1`.
- 圖結構： 29 top-level nodes; 28 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CR Comic Panel Templates`, `CR Text Concatenate`, `EmptyImage`, `GoogleTranslateTextNode`, `ImageListToBatch+`, `ImpactSwitch`, `JWImageResizeByLongerSide`, `KSampler`, `LayerMask: PersonMaskUltra`, `LayerStyle: DropShadow`, `LayerUtility: ImageBlendAdvance`, `LayerUtility: SimpleTextImage`, `LayerUtility: TextBox`, `LoadImage`, `MZ_ChatGLM3Loader`, `MZ_ChatGLM3_V2`, `MZ_KolorsUNETLoaderV2`, `MaskPreview+`, `Note`, `PreviewBridge`, `PreviewImage`, `RandomNoise`, `RandomPrompt`, `SaveImage`, `ShowText|pysssss`, `VAEDecode`, `VAEEncodeForInpaint`, `VAELoader`.
- 明確模型檔名： `chatglm3-4bit.safetensors`, `kcolors/inpainting/diffusion_pytorch_model.safetensors`, `sdxl_vae.safetensors`.
- 輸入： 219: RandomPrompt, 111: LayerUtility: SimpleTextImage, 138: LoadImage.
- 輸出： 259: PreviewBridge, 239: MaskPreview+, 236: PreviewImage, 220: ShowText|pysssss, 257: SaveImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; ComfyUI_Comfyroll_CustomNodes; ComfyUI-Custom-Scripts. Package hints are inferred, not installed/version-verified.
- 供應商／橋接需求： Google translation service/custom-node backend; network behavior and provider configuration must be checked.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 198 — `SpecialUsage/Simple360.png#workflow`

Embedded SDXL panorama outpainting with circular VAE, seamless tile behavior and 360 LoRA. Seam/wrap correctness requires actual image inspection; no app integration exists.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `2fb7149ac8bed7749b06185d7d5145b3942da7ded56a0e78ed03a482f8fe4d77`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `f449f24fb43a98cef19393ceffb60e0bc4e3cf3212c5384cea61ec52a24eae35`.
- 圖結構： 17 top-level nodes; 17 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `EmptyLatentImage`, `ImagePadForOutpaint`, `ImageResize+`, `InpaintPreprocessor`, `KSampler`, `LoadImage`, `LoraLoaderModelOnly`, `MakeCircularVAE`, `PreviewImage`, `SeamlessTile`, `SetUnionControlNetType`, `VAEDecode`.
- 明確模型檔名： `SDXL/360RedmondResized.safetensors`, `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `aiartistHyperrayXLXL_hypersdxl.safetensors`.
- 輸入： 3: CLIPTextEncode, 2: CLIPTextEncode, 17: LoadImage.
- 輸出： 13: PreviewImage.
- 相依套件： comfyui_controlnet_aux; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 199 — `SpecialUsage/【360】FullScaleBeta.png#workflow`

Embedded full panorama pipeline combining Flux/SDXL generation, subject cleanup, IPAdapter, 360 LoRAs and upscale. Many branches/masks are involved; no exact runtime link exists in Web_Comfyui.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `2f398ca80cd35ce0002cd5184d061b8ad47e0e63cbd03baf10ec126b06562ec0`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `8ddf76f42fbdd23b6682af8d5a61eb7461afcfc8563cf0e727099418a40c77bd`.
- 圖結構： 58 top-level nodes; 58 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AdaptiveGuidance`, `BasicScheduler`, `CLIPTextEncodeFlux`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `DifferentialDiffusion`, `DualCLIPLoaderGGUF`, `DynamicThresholdingFull`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImagePadForOutpaint`, `ImageRemoveBackground+`, `ImageResize+`, `InpaintModelConditioning`, `InpaintPreprocessor`, `KSampler`, `KSampler //Inspire`, `KSamplerSelect`, `LayerMask: MaskEdgeUltraDetail`, `LayerMask: YoloV8Detect`, `LayerUtility: CropByMask V2`, `LayerUtility: ImageScaleRestore V2`, `LayerUtility: LaMa`, `LayerUtility: PurgeVRAM`, `LayerUtility: RestoreCropBox`, `LayerUtility: SaveImagePlus`, `LoraLoaderModelOnly`, `MakeCircularVAE`, `PreviewImage`, `Prompts`, `RandomNoise`, `SDXLEmptyLatentSizePicker+`, `SDXLPromptStylerAll`, `SamplerCustomAdvanced`, `SaveImage`, `SeamlessTile`, `SelectNthMask //Inspire`, `SetUnionControlNetType`, `SimpleCondition+`, `SimpleMathBoolean+`, `TransparentBGSession+`, `UltimateSDUpscale`, `UnetLoaderGGUF`, `UpscaleModelLoader`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `4x-UltraSharp.pth`, `FLUX1/Asian_Realistic-Q4_K_S.gguf`, `FLUX1/ae.sft`, `Flux/360HDR.safetensors`, `Flux/Hyper-FLUX.1-dev-8steps-lora.safetensors`, `Flux/MizuotoFlux_000001250.safetensors`, `SDXL/360RedmondResized.safetensors`, `SDXL/controlnet-union-sdxl-1.0/diffusion_pytorch_model_promax.safetensors`, `ViT-L-14-TEXT-detail-improved-hiT-GmP-TE-only-HF.safetensors`, `aiartistHyperrayXLXL_hypersdxl.safetensors`, `person_yolov8m-seg.pt`, `t5/t5-v1_1-xxl-encoder-Q5_K_S.gguf`.
- 輸入： 70: Prompts, 87: CLIPTextEncodeFlux, 88: CLIPTextEncodeFlux, 2: Prompts, 96: SDXLPromptStylerAll.
- 輸出： 86: SaveImage, 27: PreviewImage, 83: LayerUtility: SaveImagePlus.
- 相依套件： ComfyUI-GGUF; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 200 — `SpecialUsage/【CatVitOn】CatVitonBasic.png#workflow`

Embedded native CatVTON try-on pipeline with automatic masks and person/garment images. Loader selections imply model downloads even without explicit filenames; this differs from Flux CatVTON-LoRA try-on.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `4fee26df880206e52f4ea4141fde0e301ba28197c89e6665f7c61b6903c6c224`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `62798c62a5a19c56b8c1a2ed68d4259487f5e43c7ccc6641eea64f03c3e17220`.
- 圖結構： 9 top-level nodes; 9 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AutoMasker`, `CatVTON`, `LoadAutoMasker`, `LoadCatVTONPipeline`, `LoadImage`, `PreviewImage`.
- 明確模型檔名： none observed; inspect model selectors, implicit downloads and provider models.
- 輸入： 10: LoadImage, 11: LoadImage.
- 輸出： 14: PreviewImage, 18: PreviewImage, 15: PreviewImage.
- 相依套件： ComfyUI plus node implementations in inventory. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 201 — `Style/PhotoToPainting.png#workflow`

Embedded photo-to-painting with line controls, SD1.5 stylization, SEGS tile/detail passes and a workflow> sampler group. Dangling links and alternate group syntax both need review.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `2d25c7991e735bc7fd062be670fcb00fc81d965f64b0c1dd8baea2d113df04e8`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `0decc5fcdbf55924cf28c33bcc4603f631d070b797fe60250b28a8779eddf889`.
- 圖結構： 39 top-level nodes; 40 active expanded nodes. Top-level check: **fail** — dangling link node.
- 節點類型： `AnimeLineArt_Preprocessor_Provider_for_SEGS //Inspire`, `AnyLineArtPreprocessor_aux`, `CLIPLoader`, `CLIPSetLastLayer`, `CheckpointLoaderSimple`, `ControlNetApplyAdvanced`, `ControlNetLoader`, `CoreMLDetailerHookProvider`, `DetailerForEachPipe`, `Fast Groups Bypasser (rgthree)`, `FreeU_V2`, `GetImageSize+`, `ImageUpscaleWithModel`, `ImpactControlNetApplyAdvancedSEGS`, `ImpactMakeTileSEGS`, `ImpactSimpleDetectorSEGS`, `JWImageResizeByFactor`, `JWImageResizeByLongerSide`, `KSampler //Inspire`, `LoadImage`, `LoraLoaderModelOnly`, `Note`, `PixelPerfectResolution`, `PreviewImage`, `Prompts`, `SAMLoader`, `String Literal`, `ToBasicPipe`, `UltralyticsDetectorProvider`, `UpscaleModelLoader`, `VAEDecode`, `VAEEncode`, `workflow>Sampler`.
- 明確模型檔名： `RealESRGAN_x4plus_anime_6B.pth`, `SDXLrender_v2.0.safetensors`, `ViT-L-14-BEST-smooth-GmP-TE-only-HF-format.safetensors`, `control_v11p_sd15_lineart_fp16.safetensors`, `hypersd/Hyper-SD15-8steps-CFG-lora.safetensors`, `imp_v10.safetensors`, `sam_vit_b_01ec64.pth`, `segm/person_yolov8m-seg.pt`.
- 輸入： 183: Prompts, 173: LoadImage, 107: String Literal.
- 輸出： 126: PreviewImage, 136: PreviewImage, 108: PreviewImage, 160: PreviewImage.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_essentials; rgthree-comfy. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: dangling link node. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 202 — `Video/WAN/TalkingVideo.png#workflow`

Embedded talking-video pipeline combining FLOAT, IndexTTS, Wan VACE, frame interpolation and subtitles with Gemini assistance. It requires voice/image/audio inputs and several model stacks; none is mapped to this graph by the public app.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `ad3811648240bb484acc3439459e11868d39ddd47494ef9e7597ae4c83e74efc`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `ca94261024bf02875593514cd9694d58689edf10fa03e03fc4a9ac94868dfd5e`.
- 圖結構： 41 top-level nodes; 41 active expanded nodes. Top-level check: **pass**.
- 節點類型： `AddSubtitlesToVideo`, `DownloadAndLoadGIMMVFIModel`, `Fast Groups Bypasser (rgthree)`, `FloatProcessOpt`, `GIMMVFI_interpolate`, `GeminiAPI`, `GetImageRangeFromBatch`, `GetImageSizeAndCount`, `ImagePadForOutpaint`, `ImageResize+`, `IndexTTSRun`, `LayerUtility: PurgeVRAM`, `LoadAudio`, `LoadFloatModelsOpt`, `LoadImage`, `LoadWanVideoT5TextEncoder`, `MathExpression|pysssss`, `RebatchImages`, `ShowText|pysssss`, `TextBox1`, `VHS_DuplicateMasks`, `VHS_GetImageCount`, `VHS_LoadVideoPath`, `VHS_SelectFilename`, `VHS_VideoCombine`, `WanVideoDecode`, `WanVideoLoraSelect`, `WanVideoModelLoader`, `WanVideoSampler`, `WanVideoTextEncode`, `WanVideoVACEEncode`, `WanVideoVAELoader`, `easy batchAnything`, `easy forLoopEnd`, `easy forLoopStart`, `easy indexAnything`, `easy int`.
- 明確模型檔名： `Wan/Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank32.safetensors`, `Wan2.1-T2V-1.3B-Self-Forcing-DMD-VACE-FP8_e4m3fn.safetensors`, `float.pth`, `gimmvfi_r_arb_lpips_fp32.safetensors`, `umt5-xxl-enc-fp8_e4m3fn.safetensors`, `wan_2.1_vae.safetensors`.
- 輸入： 73: VHS_LoadVideoPath, 48: LoadAudio, 8: LoadWanVideoT5TextEncoder, 46: LoadImage, 11: WanVideoTextEncode.
- 輸出： 55: VHS_VideoCombine, 62: ShowText|pysssss, 19: VHS_VideoCombine, 79: VHS_VideoCombine.
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI_essentials; rgthree-comfy; ComfyUI-WanVideoWrapper or native Wan variant named in node inventory; ComfyUI-Easy-Use; ComfyUI-Custom-Scripts; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `ComfyUI-GIMM-VFI`, `ComfyUI-WanVideoWrapper`, `RES4LYF`, `audiotools`, `comfy-core`, `comfyui-custom-scripts`, `comfyui-easy-use`, `comfyui-kjnodes`, `comfyui-ollamagemini`, `comfyui-videohelpersuite`, `comfyui_essentials`, `comfyui_layerstyle`, `float-optimized`, `indextts-mw`, `set-soft/ComfyUI-FLOAT_Optimized`, `yolain/ComfyUI-Easy-Use`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 203 — `Video/WAN/WANT2V.png#workflow`

Embedded Wan/MOVIIGen text-to-video with SageAttention and VideoHelperSuite output. Model/encoder/VAE and attention support must be qualified together.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `8f99617b5934011ce2c10665349ce5776da1d4f3703b57fb3a0172eb59abe07c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `17d6237e5864df47b8dc70dc4e9d2fc2f3fcebc6c76cd00ffa9531e4b0224769`.
- 圖結構： 11 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPLoader`, `CLIPTextEncode`, `EmptyHunyuanLatentVideo`, `KSampler`, `ModelSamplingSD3`, `PathchSageAttentionKJ`, `UNETLoader`, `VAEDecode`, `VAELoader`, `VHS_VideoCombine`.
- 明確模型檔名： `Wan2_1-MoviiGen1_1_fp8_e4m3fn.safetensors`, `umt5_xxl_fp8_e4m3fn_scaled.safetensors`, `wan_2.1_vae.safetensors`.
- 輸入： 7: CLIPTextEncode, 6: CLIPTextEncode.
- 輸出： 50: VHS_VideoCombine.
- 相依套件： ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-KJNodes / related version-specific extensions. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `comfy-core`, `comfyui-kjnodes`, `comfyui-videohelpersuite`.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 204 — `inpaintoutpaint/GeminiInpainting.png#workflow`

Embedded Gemini-powered masked image editing with crop/scale restoration and edge-detail handling. This explicitly requires a configured external Gemini service and exposes no local diffusion checkpoint.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `64d58a391d3a9bd43321f84288cb69615f4ba92bbf6e86a3dbcf1b26e5940d56`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2d7cbe097f8f8cb33da6686283e0ba9e9e73d2e249db189be229e1417be05ff1`.
- 圖結構： 13 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `Google-Gemini`, `ImageConcanate`, `ImageResize+`, `LayerMask: MaskEdgeUltraDetail V2`, `LayerUtility: CropByMask V2`, `LayerUtility: ImageScaleRestore V2`, `LayerUtility: RestoreCropBox`, `LoadImage`, `PreviewImage`.
- 明確模型檔名： none observed; inspect model selectors, implicit downloads and provider models.
- 輸入： 10: LoadImage.
- 輸出： 23: PreviewImage, 26: PreviewImage, 14: PreviewImage, 29: PreviewImage [mode 4].
- 相依套件： ComfyUI_LayerStyle and applicable advanced extensions; ComfyUI_essentials; Gemini API custom nodes named in inventory plus explicit provider configuration. Package hints are inferred, not installed/version-verified.
- 來源宣告的套件 ID： `CY-CHENYUE/ComfyUI-Gemini-API`, `comfy-core`, `comfyui-kjnodes`, `comfyui_essentials`, `comfyui_layerstyle`.
- 供應商／橋接需求： Gemini-capable API/local hybrid node; inspect selected provider and configure explicitly.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. Provider/bridge/code requirements must be configured explicitly and cannot be inferred from local node registration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 205 — `manual/CoupleFlowpic.png#workflow`

Embedded older couple mixing graph with legacy regional IPAdapter groups. Reciprocal-link errors and the CLIP vision filename-space issue match the archived variant, but raw metadata bytes are not identical.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `f480c12f647e14aee6a2073f74cfef2faa03291e2751677a271397dbb37434dc`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `da5a043fa5663c518fee05c1323a1836afdd6ed1bfa1725e84f392adc2574b0f`.
- 圖結構： 24 top-level nodes; 29 active expanded nodes. Top-level check: **fail** — nonreciprocal link.
- 節點類型： `ApplyRegionalIPAdapters //Inspire`, `CLIPVisionLoader`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `Efficient Loader`, `GetImageSize`, `IPAdapterModelLoader`, `KSampler (Efficient)`, `LoadImage`, `MaskPreview+`, `Note`, `PrepImageForClipVision`, `PreviewImage`, `RegionalConditioningColorMask //Inspire`, `RegionalIPAdapterColorMask //Inspire`, `ToIPAdapterPipe //Inspire`, `workflow/GreenAreaIPA`, `workflow/IPadapterpipeloader`, `workflow/RedAreaIPA`, `workflow/controlnet`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K .safetensors`, `add_detail.safetensors`, `control_v11p_sd15_openpose.pth`, `henmixReal_v5c.safetensors`, `ip-adapter-plus-face_sd15.safetensors`, `ip-adapter-plus_sd15.safetensors`.
- 輸入： 175: LoadImage, 139: LoadImage, 78: LoadImage, 183: LoadImage, 77: LoadImage.
- 輸出： 119: MaskPreview+, 25: PreviewImage, 116: MaskPreview+, 118: MaskPreview+.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Inspire-Pack; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI_essentials. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: nonreciprocal link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 206 — `manual/CoupleFull.png#workflow`

Embedded expanded 71-node couple composition with pose/segmentation/regional IPAdapter and face masks. It is a distinct older graph from the grouped root CoupleFull.json, not an automatic duplicate.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `a2a465802bada50e7b8470ab25c0d0c7c4bfa4b9996602fb3d6bc6caecac8a98`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `46e0acb1bd96b70ab2ba4b50dcc6ac00b62b79154e54bd4cd2819338c8db4158`.
- 圖結構： 71 top-level nodes; 71 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ApplyRegionalIPAdapters //Inspire`, `BboxDetectorSEGS`, `CLIPVisionLoader`, `ConditioningCombine`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `Efficient Loader`, `EmptyImage`, `GetImageSize`, `IPAdapterModelLoader`, `ImageCompositeMasked`, `ImpactSEGSOrderedFilter`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `MaskToImage`, `Note`, `PrepImageForClipVision`, `PreviewImage`, `RegionalConditioningSimple //Inspire`, `RegionalIPAdapterColorMask //Inspire`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `SubtractMask`, `ToIPAdapterPipe //Inspire`, `UltralyticsDetectorProvider`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `add_detail.safetensors`, `bbox/face_yolov8m.pt`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `ghostmix_v20Bakedvae.safetensors`, `ip-adapter-plus-face_sd15.safetensors`, `ip-adapter-plus_sd15.safetensors`, `segm/yolov8m-seg.pt`, `yolox_l.onnx`.
- 輸入： 108: LoadImage, 109: LoadImage, 99: LoadImage, 98: LoadImage, 97: LoadImage, 33: LoadImage.
- 輸出： 7: PreviewImage, 5: PreviewImage, 28: PreviewImage, 30: PreviewImage, 46: PreviewImage, 50: PreviewImage, 110: PreviewImage, 82: PreviewImage, 83: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 207 — `manual/Couplefaceid.png#workflow`

Embedded couple FaceID workflow with mask regions and legacy group definitions. InsightFace, FaceID adapters and exact mask ordering are prerequisites.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `a8639da2a797bf570e6bf1cd3ed955f65449916fc70b5d7d0d7a72593cdb9a6f`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `2c475c6be4591f0df71f6ff82f6079c2eedcbc7f35329bd5fe6c542eb3c18c94`.
- 圖結構： 45 top-level nodes; 48 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CLIPVisionLoader`, `ColorToMask`, `ConditioningCombine`, `ConditioningSetMask`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `Efficient Loader`, `EmptyImage`, `GetImageSize`, `IPAdapterApply`, `IPAdapterApplyFaceID`, `IPAdapterModelLoader`, `ImageCompositeMasked`, `ImpactSEGSOrderedFilter`, `InsightFaceLoader`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `MaskToImage`, `PrepImageForInsightFace`, `PreviewImage`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `UltralyticsDetectorProvider`, `workflow/controlnet`, `workflow/紅色區域條件`, `workflow/綠色區域條件`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `ghostmix_v20Bakedvae.safetensors`, `ip-adapter-faceid-plusv2_sd15.bin`, `ip-adapter-faceid-plusv2_sd15_lora.safetensors`, `ip-adapter-plus_sd15.safetensors`, `segm/yolov8m-seg.pt`, `yolox_l.onnx`.
- 輸入： 183: LoadImage, 77: LoadImage, 78: LoadImage, 259: LoadImage, root/311: CLIPTextEncode, root/305: CLIPTextEncode, 292: CLIPTextEncode.
- 輸出： 309: PreviewImage, 258: PreviewImage, 250: PreviewImage, 248: PreviewImage, 272: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 208 — `manual/Couplenofaceid.png#workflow`

Embedded couple workflow without FaceID, using regional IPAdapter image embeddings and pose. Its identity mechanism differs from the FaceID variant despite similar outputs.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `f3148a335bec9d7ee9e92996ecf49a8af47bfc72fec169f8d9a2ffc445e78600`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `8fe703b6d9300c747883704c975398e5aa9572a47e8f26f4a94ceb18d58f8ca2`.
- 圖結構： 48 top-level nodes; 50 active expanded nodes. Top-level check: **pass**.
- 節點類型： `ApplyRegionalIPAdapters //Inspire`, `CLIPTextEncode`, `CLIPVisionLoader`, `ColorToMask`, `ConditioningCombine`, `ConditioningSetMask`, `ControlNetApply`, `ControlNetLoader`, `DWPreprocessor`, `Efficient Loader`, `EmptyImage`, `GetImageSize`, `IPAdapterModelLoader`, `ImageCompositeMasked`, `ImpactSEGSOrderedFilter`, `InvertMask`, `JWImageResizeByLongerSide`, `KSampler (Efficient)`, `LoadImage`, `MaskToImage`, `PrepImageForClipVision`, `PreviewImage`, `RegionalConditioningSimple //Inspire`, `RegionalIPAdapterMask //Inspire`, `SegmDetectorSEGS`, `SegsToCombinedMask`, `ToIPAdapterPipe //Inspire`, `UltralyticsDetectorProvider`, `workflow/controlnet`, `workflow/綠色區域條件`.
- 明確模型檔名： `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`, `add_detail.safetensors`, `control_v11p_sd15_openpose.pth`, `dw-ll_ucoco_384_bs5.torchscript.pt`, `henmixReal_v5c.safetensors`, `ip-adapter-plus-face_sd15.safetensors`, `ip-adapter-plus_sd15.safetensors`, `segm/yolov8m-seg.pt`, `yolox_l.onnx`.
- 輸入： 292: CLIPTextEncode, 77: LoadImage, 78: LoadImage, 183: LoadImage, root/305: CLIPTextEncode, 259: LoadImage.
- 輸出： 309: PreviewImage, 258: PreviewImage, 250: PreviewImage, 248: PreviewImage, 272: PreviewImage.
- 相依套件： efficiency-nodes-comfyui; ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Referenced legacy groups carry runtime dependencies; current sanitizer rejects workflow/ and does not handle workflow> aliases. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 209 — `manual/Musetalk.png#workflow`

Embedded MuseTalk/AnimateDiff example corresponding to the audio-driven animation architecture. A stale output link remains; PNG presence does not demonstrate current executable integration.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `9c9e51d829819ffcbb50058881076c92b77df0d8e0cd5e65a82a11d5293a8c8c`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `de5d893b3c1e7c9e570a8d32750265928545ded3c919c961c99e2288e95e7fd5`.
- 圖結構： 34 top-level nodes; 34 active expanded nodes. Top-level check: **fail** — stale output link.
- 節點類型： `ACN_AdvancedControlNetApply`, `ACN_SparseCtrlLoaderAdvanced`, `ACN_SparseCtrlRGBPreprocessor`, `ACN_SparseCtrlSpreadMethodNode`, `ADE_AnimateDiffLoaderWithContext`, `ADE_AnimateDiffUniformContextOptions`, `ADE_EmptyLatentImageLarge`, `BatchPromptSchedule`, `CheckpointLoaderSimple`, `Fast Groups Bypasser (rgthree)`, `FreeU_V2`, `IPAdapterAdvanced`, `IPAdapterUnifiedLoader`, `ImpactFloat`, `ImpactSwitch`, `KSamplerAdvanced //Inspire`, `LayerUtility: PurgeVRAM`, `LoadImage`, `MuseTalkCupAudio`, `MuseTalkRun`, `RIFE VFI`, `ScaledSoftControlNetWeights`, `ShowText|pysssss`, `Simple String`, `SimpleMath+`, `String Literal`, `VAEDecode`, `VHS_FILENAMES_STRING_MuseTalk`, `VHS_LoadAudio`, `VHS_VideoCombine`, `WD14Tagger|pysssss`.
- 明確模型檔名： `animatediff_lightning_4step_comfyui.safetensors`, `controlnetanimediff/v3_sd15_sparsectrl_rgb.ckpt`, `dreamshaper_8.safetensors`, `rife47.pth`.
- 輸入： 167: String Literal, 166: BatchPromptSchedule, 219: LoadImage, 245: String Literal, 237: VHS_LoadAudio.
- 輸出： 236: VHS_VideoCombine, 134: VHS_VideoCombine, 250: ShowText|pysssss.
- 相依套件： ComfyUI-Impact-Pack / Impact-Subpack; ComfyUI-Inspire-Pack; ComfyUI_LayerStyle and applicable advanced extensions; comfyui_controlnet_aux; ComfyUI_IPAdapter_plus and version-compatible FaceID assets; ComfyUI-VideoHelperSuite plus compatible FFmpeg; ComfyUI-Advanced-ControlNet; ComfyUI-AnimateDiff-Evolved; ComfyUI_essentials; rgthree-comfy; ComfyUI-Custom-Scripts; frame interpolation custom nodes and RIFE weights. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： Source fails public strict top-level topology check: stale output link. This is an importer incompatibility; frontend reconstruction or specialized routing may explain some records and must be inspected before calling the graph intrinsically non-executable. No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 210 — `manual/SD15.png#workflow`

Embedded SD1.5 portrait generation with translated PortraitMaster/PromptBuilder controls and model downscale patching. UI prompt-building nodes and checkpoint/VAE assets are required.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `415cb4788372c62a3a14c3d36571d64f9603684524858798483df6ea5c5c1490`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `c04510f7e127c31e378b495ee3e79658c4ac9b01fae441910c52e811740ca023`.
- 圖結構： 13 top-level nodes; 13 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPSetLastLayer`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler //Inspire`, `PatchModelAddDownscale`, `PortraitMaster_繁體中文版`, `PreviewImage`, `PromptBuilder //Inspire`, `SDXLPromptStyler`, `VAEDecode`, `VAELoader`.
- 明確模型檔名： `magmix_v10.safetensors`, `vae-ft-mse-840000-ema-pruned.safetensors`.
- 輸入： 8: CLIPTextEncode, 16: PromptBuilder //Inspire, 7: CLIPTextEncode, 9: SDXLPromptStyler.
- 輸出： 15: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 211 — `manual/SD3.png#workflow`

Embedded SD3 portrait generation using PortraitMaster/PromptBuilder and a checkpoint with bundled encoders. It remains a source tutorial graph, not an app portrait runner.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `a08731166f7c7e349c4863e7db21c9b720e6139e8c95a6a30e8517edec37df8b`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `1e06a025845bf6f368966e7577e52d7ede4b63c475c290a231a4ad929133f646`.
- 圖結構： 10 top-level nodes; 10 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptySD3LatentImage`, `KSampler //Inspire`, `PortraitMaster_繁體中文版`, `PreviewImage`, `PromptBuilder //Inspire`, `SDXLPromptStyler`, `VAEDecode`.
- 明確模型檔名： `SD3/sd3_medium_incl_clips_t5xxlfp8.safetensors`.
- 輸入： 7: CLIPTextEncode, 8: CLIPTextEncode, 16: PromptBuilder //Inspire, 9: SDXLPromptStyler.
- 輸出： 15: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

### 212 — `manual/SDXL9527.png#workflow`

Embedded SDXL portrait generation using the 9527 checkpoint and translated portrait/style controls. A preview sink is present, but no public copy or qualified generation is linked.

- 容器／格式： `png_metadata` / `visual`. SHA-256: `b144cc9196b6226a26ebbad2e6bdf9aefac0dea73fa636c78be807bcfbd38bbf`. COMMIT: `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.
- 容器 SHA-256： `938b6304db055c2e128115064a696f90115f39b0f8b897ed31ad9e3d5de1543c`.
- 圖結構： 11 top-level nodes; 11 active expanded nodes. Top-level check: **pass**.
- 節點類型： `CLIPSetLastLayer`, `CLIPTextEncode`, `CheckpointLoaderSimple`, `EmptyLatentImage`, `KSampler //Inspire`, `PortraitMaster_繁體中文版`, `PreviewImage`, `PromptBuilder //Inspire`, `SDXLPromptStyler`, `VAEDecode`.
- 明確模型檔名： `9527DetailRealistic_v55MixBakedvae.safetensors`.
- 輸入： 8: CLIPTextEncode, 16: PromptBuilder //Inspire, 7: CLIPTextEncode, 9: SDXLPromptStyler.
- 輸出： 15: PreviewImage.
- 相依套件： ComfyUI-Inspire-Pack. Package hints are inferred, not installed/version-verified.
- 供應商需求： no explicit remote-provider/bridge node detected; local node/model environment still required. MiniMax H3/Music3 model graphs are local.
- 公開目錄／工作流庫 ID： none; catalog distribution: absent.
- 公開副本： none.
- 介面／執行器： absent from public workflow library; none.
- 缺口： No public local copy or exact workflow runner mapping; source-reference metadata is not execution integration. No recognized active durable-output sink; inspect preview/text/custom sinks and add an intentional export contract if needed.
- 授權／證據： repository GPL-3.0; model/node licenses separate. Static source review only; no verified generation of this exact graph.

## 輔助檔案與排除項目

- `Animate/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `df5c5b484a7466e882dedacc295e560bcc9917e76cf6a047c80ec8f9ad38a73b`.
- `BackgroundChanger/readme.md` — Complete file read; Explains BrushNet/CosXL/Differential Diffusion alternatives, including historical Diffusers compatibility and model setup caveats. SHA-256 `74bf879a3c65b1bb793dbdbd9fcb8915335e6cb344c64e7c7df6aec530857714`.
- `Clothes/clothes.md` — Complete file read; Explains generative clothing/reference intent and depth/OpenPose alternatives; explicitly does not promise exact garment appearance. SHA-256 `8ba838abd49b22247bf43feae4b6daf9b6c941154782c2f560d6c7ca87c4639a`.
- `CodeExample/Youtube/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `482ff4a08228bc23217c41379e66472e1fd5e347453798dc820760b7a8c6233f`.
- `CodeExample/Youtube/yt.py` — Complete file read; Desktop Google OAuth bootstrap; binds a local callback on 8080 and writes token.json with upload/playlist scopes. Not executed or incorporated. SHA-256 `057035666d4653a18f4a3d76720a9814ab4b5fa04178c6ffaa605832b3e78b7d`.
- `CodeExample/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `be0ebefea5b4f6b9dc6bc7c74d996bc35be6c9686ee6823958faf63b2744ef39`.
- `Couple/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `109475d94308f4b542e554e32ee2e82073db5b19053ff3cd1867c28b3709e023`.
- `Design/GEM.md` — Complete file read; Poster-design prompt/template prose, not executable instructions for the audit or a graph integration. SHA-256 `8655ffe803b6f855d924de1fe9dd43f64f17de4b0e9e934b2232a232a151005c`.
- `Design/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `186fd238974e3969f75268163bfaf779daff7d9034796d8188ac41351f1746fa`.
- `Experiment/DesignPack/Readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `20c8f8973fcfdd607e4398bc5d7ef9637f7f786a871db08ec05338ac302684b4`.
- `Experiment/Manual/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `6682b8b94ca1200993cd6fd7cf9de751ed4d88a586d0bf78712f7b661b8a52dd`.
- `Experiment/extra_models_config.yaml` — Complete file read; Machine-specific Windows model search paths and aliases. Configuration example, not a portable model manifest. SHA-256 `c8c1ff0de430a8fdee9c0de9716f4bd199ecc21199de3ca7214d12ac2b0b4e87`.
- `Experiment/readme.md` — Complete file read; Describes StyleAligned reference experiment and explicitly poor clothing-fit results in ClothesChangerV2. SHA-256 `22b5078e41a6a1c92ca11920ed6d45f01503cbe8767134cf3ca48b247f30a0d9`.
- `Experiment/test.py` — Complete file read; Triton CUDA vector-add smoke script. It is not a workflow or generation regression test, and was read without execution. SHA-256 `4c75c47792cc337cb9f1b6de212f4abcfe05d8d779c27938c76ee1d6165564b3`.
- `Flux/ACELora/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `77af01b7978ee9af300b53d3f9a0189537a4636682b685ceb8ccbf8e7fc858ed`.
- `Flux/DetailDaemon/Readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `051db51f055cf0777a3ea1fb73b9b313503a9849f9853228db1781a88a204500`.
- `Flux/FluxTools/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `b46a17f36936753126fd33452dbe7e3dd2cb9055e868267a48d47cbde0e57456`.
- `Flux/FrontEnd/comfyui-launcher.py` — Complete file read; Windows-only process/job launcher; auto-downloads Flux Fill/T5 weights, starts ComfyUI and Gradio, and cleanup kills unrelated Python processes by process name. Not portable or safe as the app lifecycle manager. SHA-256 `4fa287d175622f17643af17fb3208f76c7ac7da1d93d2bd9b0f8642bff422bac`.
- `Flux/FrontEnd/frontend.bat` — Complete file read; Starts launcher with the Windows embedded Python; no public application linkage. SHA-256 `8bc6aa6f7fc435fc7ce49beb964881d74e08253d8b5fe1510aae3a127391145f`.
- `Flux/FrontEnd/gradiotw.py` — Complete file read; Gradio person/garment frontend; writes fixed shared filenames, posts API JSON, polls history, then returns globally newest image. No prompt-specific output binding or concurrency isolation. SHA-256 `ccd2ad475d410247023dea66793d1bb72677728d8e26166c3d9a4c5775e09906`.
- `Flux/FrontEnd/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `3e5d96602d39c17529bb3bd7490e6b864bea28eb11cde76cd7ed9f2a5b15da09`.
- `Flux/Kontext/readme.md` — Complete file read; Explains Gemini/Ollama node dependency and historical manual update requirement for Kontext prompt templates. SHA-256 `016ef86280327b299db38ce1d9f8ddb6218a0ffad055b7fc43419e0a57076b09`.
- `Flux/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `7a832983db2888b313a688e6626983e002ebca565f2ae2dcb42480803632f23e`.
- `Kolors/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `c0b8693ba0bcc7200b94724c297e0ec2c6ba3e2583a10fe424641b0fb73099e9`.
- `Krita/readme.md` — Complete file read; Explains Krita Graph loading. Its broad model-install wording does not override explicit model selections/parameters in the JSON. SHA-256 `81a12133428a86dc31f0cdfa97a7ea1d0fca7e492dbf1eead01cee58e5e71951`.
- `LocalVideoModel/HunyuanVideo/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `b47c3a66f1d0b36e41a3525fba6ae6430a93cd60b7560822f3e4ebdedf8fdc76`.
- `MiniMAXH3/Readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `03e3dad5991bc8f7f5eadb724860817e7600a265b2caa48b3cd661c51b97c4aa`.
- `MiniMAXMusic/Readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `add42067f1c5857bbc29a03addab7dd5bfe7cb3acc7780b044f81f8a1780bd61`.
- `Qwen/Readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `4c429a66c948a46121af5562f08b4582c6d9c61af6766d07059499a4feb9da08`.
- `README.md` — Complete file read; Explains hand repair, clothing/pose/face inputs, inpainting, anime conversion, couple FaceID, character experiments and VLM text fixes. SHA-256 `60e6819bafff8a789fb6e1cb1496d1241ae7238bf5dff2edcf0e26b89cca0f17`.
- `SD3/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `2a9d61a3c98e24a7914028c2375d72d03bbb66860e9e34858d580b729d6e4d39`.
- `SDXL/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `1e651bbd33f6a0d2590083b206fc572334223ade47e6dd367e76eff1dc45817b`.
- `SpecialUsage/HairStyleBook/hairstyle.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `b150abc13de53a332a46fb8e70fdbc3bb78c7acaa9b935ad67cddf25bd432f2e`.
- `SpecialUsage/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `857b8779fa0ff0e8bbf7903b7815e71e874b89ef3f99665b9dc9a91f1b3a6566`.
- `Style/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `5a9953833fc7bc36cbbe8655a1e6a7f2205729437452ce3c739f8292c141e685`.
- `Tiled/readme.md` — Complete file read; Explains UltraSharp plus SEGS/SDXL Lightning tiling and claims roughly two minutes on RTX4070; historical author claim only. SHA-256 `54c23ceaa0ce053bcd9c3ef0302c54a931d86f64b731ca843692c9293ad903dd`.
- `Video/WAN/readme` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `e681c937c4ed147e1c88bbdc1257f21ed414f475de1229466cc13dd6476b6833`.
- `WanVIdeo/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `f78e15d60b8f7ac547c5c645ddcdbecb6d040bb0796995972218b7ed1a1e8f6f`.
- `hunyuan_dit/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `7047c46a6f4a08e2c9e00ebbacff739425aea5a4ec0c525e0e8403397f945915`.
- `inpaintoutpaint/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `16a93c7012ec4b9a9198f1ba970e065fa79176501c4221f4d5473bec1cd03fee`.
- `manual/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `5ed34f1e94c03eaa6ba3837e1d57e60a2130aad8db60c00e74bfe7e8ea3b11be`.
- `ollama/readme.md` — Complete file read; Brief source-specific description/index or workflow-family notes; no install execution, runtime evidence or public runner linkage supplied. SHA-256 `362a4f8d6722b76f9cdc7b255d506c2d0d29be2cc0d46216e3ed24216c709374`.
- `Flux/convertpack.zip` — convert.py, read_tensors.py and three batch scripts read as source. Uses torch/safetensors/llama.cpp GGUF conversion and bundled Windows quantization binaries; no code/binary executed. convert.py declares Apache-2.0. Quantization(TW).bat has suspect parenthesis structure; no Windows execution attempted.
- `manual/MusetalkLib.zip` — Dependency archive directory inspected; bundled Windows CPython 3.11 MMCV/mmpose/aliyun dependencies and native binaries are not authored workflow JSON, were not extracted or executed. Not a supported Linux/Blackwell install recipe.
- `CodeExample/Youtube/PPT.pptx` — Binary presentation was not rendered; accompanying README and embedded FL_CodeNode source supply OAuth/upload behavior. No media was inspected.

The complete PNG and ZIP container inventory, source hashes, per-node model selections, package IDs and exact manifest provenance are in [workflows-audit-20261110.json](workflows-audit-20261110.json). `manual/AnimeArt.png` is the one PNG without workflow metadata. No media asset is accepted as GPU evidence without a run manifest binding graph, inputs, engine, device and output hashes.

## 後續實作優先順序

1. Preserve and validate referenced modern definitions, expand nested dependency inventories, and reject unsupported group wrappers explicitly. Retain source IDs and pinned hashes.
2. Distinguish GPL source-reference selection policy from missing redistribution license.
3. Resolve NPNet/custom loader requirements and opaque hosted checkpoint selectors before promoting entries to executable application forms.
4. For each desired runtime feature, define input roles/masks, exact API graph or native bridge, model/node versions, output artifact binding, cancellation/error handling and provider compatibility/budget policy.
5. Add representative CPU/static fixture checks first, then separately authorized native generation qualification with synthetic inputs and graph/model/node/device/output hashes.
6. Review encrypted archives only if a legitimate password is supplied; until then keep those six entries uninspected.
