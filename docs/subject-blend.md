# Subject/background compositing

The `image-blend` feature uses `subject_blend_prompt` in `webui/server.py`.
Upload the original subject as `image1` and the target background as `image2`.
The subject label (`positive`) defaults to `person`; detection confidence
(`threshold`) defaults to 0.5. This label selects what to segment, rather than
describing a new generated composition. Multiple matching people are combined
in one mask.

`person` can miss handheld props, legs, hands and hair on illustrated/anime
characters. Try a label that includes the prop (for example `person holding a
bag`), then inspect the result; a longer label does not guarantee inclusion.
Check holes where hands overlap props and background-colored hair fragments.
The optional `feather` control (0–16 pixels, default 0/off) adds
`MaskToImage → ImageBlur → ImageToMask` after the binary mask. A square
`GrowMask` erosion identifies the interior beyond the blur radius, and a second
`ImageCompositeMasked` restores its exact loaded source pixels. This prevents
floating-point blur normalization (for example 0.99999988 instead of 1) from
darkening interior pixels by one level when saved as 8-bit PNG. It softens the
subject contour; it cannot recover missing regions. Feathered transition
pixels mix source and background, so the binary-mask preservation claim applies
only with feathering off. The source image itself is never blurred or resized.

The nine-node graph loads both images, resizes and center-crops only the
background to the subject image dimensions, loads SAM3, encodes the label,
detects the subject with two refinement iterations, thresholds the mask at
0.5, composites the untouched source pixels at (0, 0), and saves the image.
No sampler, VAE, IPAdapter, face replacement, foreground resizing, relighting,
or shadow generation is involved. Pixels inside the binary mask come directly
from the original image. This does not guarantee perfect segmentation: missed
face/hair regions and hard edges require visual inspection. Output dimensions
match the original subject image. The legacy `ip_blend_prompt` callable remains
available for existing integrations; it is not the default feature builder.

## Source and dependencies

This application-owned composition uses native ComfyUI nodes. The node schema
was checked against ComfyUI revision
`c194dd00cd42aa18d9dbf27d977bf6b85d9ea565`:

- `LoadImage`, `CheckpointLoaderSimple`, `CLIPTextEncode`, `SaveImage`.
- [`SAM3_Detect`](https://github.com/Comfy-Org/ComfyUI/blob/c194dd00cd42aa18d9dbf27d977bf6b85d9ea565/comfy_extras/nodes_sam3.py).
- `ResizeImageMaskNode`, `ThresholdMask`, `ImageCompositeMasked`.
- Optional feathering: `MaskToImage`, `ImageBlur`, `ImageToMask`, `GrowMask`
  and a second `ImageCompositeMasked` (native nodes; default CPU intermediate tensors).

For a transparent foreground export, connect the foreground mask through
`InvertMask` before `JoinImageWithAlpha(image=source, alpha=inverted_mask)`.
`JoinImageWithAlpha` computes `alpha = 1 - mask`, just as `LoadImage` exposes
the inverse of the file's alpha as its mask output. `ImageCompositeMasked`
instead uses white to select source pixels: do **not** invert the SAM3 mask
on the existing composition path. The current feature saves a composite, not
a separate transparent layer.

No additional custom-node repository is required. Older ComfyUI installations
without the native SAM3 and dynamic resize nodes need an update.

The only checkpoint is `models/checkpoints/sam3.1_multiplex_fp16.safetensors`.
The upstream converted model is
[Comfy-Org/sam3.1](https://huggingface.co/Comfy-Org/sam3.1), based on
[facebook/sam3.1](https://huggingface.co/facebook/sam3.1).
The reviewed upstream revision is
`7bb8374780a725b4353ed31f3a9395c9742b5621`; the
[pinned checkpoint](https://huggingface.co/Comfy-Org/sam3.1/blob/7bb8374780a725b4353ed31f3a9395c9742b5621/checkpoints/sam3.1_multiplex_fp16.safetensors)
is distributed under the
[SAM License](https://huggingface.co/Comfy-Org/sam3.1/blob/7bb8374780a725b4353ed31f3a9395c9742b5621/LICENSE).
Weights are not redistributed in this repository. Installers must review and
comply with that license; the application source license does not replace it.

## Validation

`tests/test_subject_blend.py` verifies required inputs, the registered default,
segmentation settings, direct unresized source-pixel routing, binary mask,
absence of diffusion, and the documentation graph's actual builder linkage.
Live node schemas were checked separately. These checks do not prove GPU image
quality. Public demo GPU evidence and hashes are recorded independently in
`webui/docs/examples/provenance.json`; inspect that evidence for the validated
inputs and actual result rather than treating a static graph as a GPU run.

The historical example `cheer-subject-blend-v4` ran on RTX 5090 at 1024 × 1536
(runtime-reported ComfyUI 0.37.0). The 2/255 measurement compares decoded RGB
from **`cheer-subject-reference.webp` and `cheer-subject-blend.webp`**, restricted
to the 315,611 white pixels of `cheer-subject-mask.webp`. The face rectangle
differs by at most 1/255. Full SHA-256 values are in that run's
`pixel_verification.file_sha256` in `provenance.json`.

The 2026-10-24 trace found that the actual ComfyUI input has exactly the
published reference's SHA-256. Both the original output PNG and saved mask PNG
have exactly the same RGB pixels as their published lossless WebP derivatives:
the publication conversion introduces **0/255** difference. On the same live
runtime, `LoadImage → SaveImage` of the WebP reference already reproduces the
2/255 difference. Loading an RGB PNG of that reference gives **0/255**, as does
a PNG ramp containing every 8-bit value from 0 through 255. Comparing the
binary-mask composite to the separately saved, loaded WebP reference gives
**0/255 within the mask**. The source is decoded as `argb` by PyAV 17.1.0. A standalone conversion to
`gbrpf32le`, followed by the same multiply-by-255 and uint8 truncation used by
SaveImage, exactly reproduces the live output pixels; conversion to `rgb24`
matches the reference at 0/255. ComfyUI’s floating-point RGB/RGBA decode branch
is therefore the measured source of this discrepancy. It is not an additional
error from compositing, nor evidence that ordinary PNG decoding requires a
tolerance. Use PNG inputs on this runtime when exact 8-bit preservation matters.

The saved mask and `scripts/test_demo_catalog.py` reproduce the historical
published-pair measurement. It is not a universal 2/255 guarantee. Two auxiliary
nodes in the recorded graph save the diagnostic mask; the default application
graph remains nine nodes, with five additional nodes only when feathering is on.

The 2026-10-24 follow-up also executed the complete graph in a separate CPU
ComfyUI process using PNG inputs and the SAM3.1 checkpoint. Detection ran once;
the cached detection was reused for feather radii 1 and 16 after the default
radius-0 run. All three outputs preserved source pixels inside the unaffected
interior and resized-background pixels outside the blur support at **0/255**.
The transparent diagnostic output preserved RGB and matched the foreground
mask exactly after `InvertMask`. These are CPU correctness checks, not GPU
performance measurements or a native Windows qualification.

An opt-in regression replays the application's emitted mask/composite graph
through real ComfyUI nodes for 102 cases: radii 0–16, rectangles, holes, thin
regions, canvas-edge subjects, and empty/full masks. It checks PNG pixels,
including all 256 source levels, rather than approximate floating-point equality.
Run it with an existing ComfyUI Python environment (no models or downloads are
needed; the worker disables GPU access):

```bash
COMFY_HOME=/absolute/path/to/ComfyUI \
COMFYUI_TEST_PYTHON=/absolute/path/to/ComfyUI/.venv/bin/python \
pytest-disktmp uv run --with pytest pytest -q tests/test_subject_blend_native.py
```

## 繁體中文操作

上傳第一張「人物原圖」與第二張「背景參考圖」，保留的主體先使用
`person`。SAM3 辨識人物並建立遮罩，背景會縮放、置中裁切成原圖尺寸，
再直接合成人物原像素。這個入口適合保留同一張臉、服裝與姿勢並換背景；
換裝請使用「AI 指令改圖」。此流程不會自動補光或生成陰影，完成後請檢查
髮絲及去背邊緣。動漫角色的書包、手腳可能漏切，可把道具加入主體描述，
但仍需逐張檢查。選用羽化會混合邊緣與背景，不能補回缺失部位；預設為 0。
請先安裝上方連結中的 SAM3.1 模型並閱讀其授權。

公開範例已在 RTX 5090 實跑；人物遮罩內 315,611 個像素的 RGB 最大差異
為 2/255，臉部檢查區為 1/255；比較的是上方明列的兩張公開 WebP。
隔離實測在這台服務的 WebP 載入／儲存路徑就重現該差異，PNG 與 0–255
逐值往返都是 0/255，合成相對已載入來源亦為 0/255。發布用 PNG→lossless
WebP 轉換沒有引入像素差；已在 PyAV 17.1.0 的 `argb`→浮點 RGB→8-bit
路徑逐像素重現問題，`rgb24` 路徑為 0/255。此環境需精確保留像素時請用 PNG。
GitHub Pages 只播放
這份固定成果，不會依輸入即時執行。
