# Subject/background compositing

The `image-blend` feature uses `subject_blend_prompt` in `webui/server.py`.
Upload the original subject as `image1` and the target background as `image2`.
The subject label (`positive`) defaults to `person`; detection confidence
(`threshold`) defaults to 0.5. This label selects what to segment, rather than
describing a new generated composition. Multiple matching people are combined
in one mask.

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

The public example `cheer-subject-blend-v4` was executed on an RTX 5090 at
1024 × 1536. The published lossless reference and result were compared over
all 315,611 subject-mask pixels: the maximum RGB channel difference was 2/255;
the recorded central face rectangle differed by at most 1/255. This measures
the small decode/save quantization of the image path, not bit-identical files.
The background changes substantially. The saved binary mask and a regression
check in `scripts/test_demo_catalog.py` make those measurements reproducible.
Two auxiliary output nodes in the recorded graph save that diagnostic mask;
the application composition remains the same nine-node graph.

## 繁體中文操作

上傳第一張「人物原圖」與第二張「背景參考圖」，保留的主體先使用
`person`。SAM3 辨識人物並建立遮罩，背景會縮放、置中裁切成原圖尺寸，
再直接合成人物原像素。這個入口適合保留同一張臉、服裝與姿勢並換背景；
換裝請使用「AI 指令改圖」。此流程不會自動補光或生成陰影，完成後請檢查
髮絲及去背邊緣。請先安裝上方連結中的 SAM3.1 模型並閱讀其授權。

公開範例已在 RTX 5090 實跑；人物遮罩內 315,611 個像素的 RGB 最大差異
為 2/255，臉部檢查區為 1/255。這是影像讀取、儲存路徑中的小幅量化差異，
不是逐位元相同的檔案。GitHub Pages 只播放這份固定成果，不會依輸入即時執行。
