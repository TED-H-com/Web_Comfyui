"""Castle's local Qwen-Image-2.1 tools, based on the pinned official templates."""
from __future__ import annotations

import math

MODEL = "qwen_image_2.1_int8_convrot.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
MODEL_BYTES = {MODEL: 7_256_783_064, CLIP: 9_350_798_360, VAE: 675_509_688}
ASPECTS = {
    "1:1": "1:1 (Square)", "16:9": "16:9 (Widescreen)",
    "9:16": "9:16 (Portrait Widescreen)", "3:2": "3:2 (Photo)",
    "2:3": "2:3 (Portrait Photo)", "4:3": "4:3 (Standard)",
    "3:4": "3:4 (Portrait Standard)", "21:9": "21:9 (Ultrawide)",
}


def feature_definitions() -> list[dict]:
    features = []
    for edit in (False, True):
        feature_id = "qwen21-edit" if edit else "qwen21-text2img"
        fields = [{
            "id": "instruction" if edit else "positive", "type": "textarea",
            "label": "修改指令" if edit else "想生成什麼？", "default": "",
            "required": True, "rows": 4,
            "placeholder": (
                "保留 <image1> 的構圖，把背景改成黃昏的海邊；可用 <image2> 指定參考風格。"
                if edit else "例如：一座雲海中的城堡，晨光照亮塔樓，海報標題為「雲上之城」。"
            ),
            "hint": "支援中文與英文；要呈現的文字請用引號標明。",
        }]
        if edit:
            fields.append({
                "id": "resolution", "type": "select", "label": "輸出大小",
                "default": "1024", "options": [
                    {"value": "1024", "label": "約 1 MP · 依原圖比例"},
                    {"value": "1536", "label": "約 2.25 MP · 依原圖比例"},
                    {"value": "2048", "label": "約 4 MP · 依原圖比例"},
                ],
            })
        else:
            fields.extend([
                {"id": "aspectRatio", "type": "select", "label": "畫面比例",
                 "default": "1:1", "options": list(ASPECTS)},
                {"id": "megapixels", "type": "select", "label": "輸出大小",
                 "default": "1", "options": [
                     {"value": "1", "label": "1 MP · 正方形 1024 × 1024"},
                     {"value": "2", "label": "2 MP · 更多細節"},
                     {"value": "4", "label": "4 MP · 正方形 2048 × 2048"},
                 ]},
            ])
        fields.extend([
            {"id": "negative", "type": "textarea", "label": "負向提示詞",
             "default": "", "rows": 2, "advanced": True},
            {"id": "steps", "type": "slider", "label": "步數",
             "default": 25, "min": 1, "max": 50, "step": 1},
            {"id": "cfg", "type": "slider", "label": "CFG",
             "default": 1.0, "min": 1, "max": 5, "step": 0.1},
            {"id": "seed", "type": "number", "label": "種子（-1 隨機）",
             "default": -1, "min": -1, "max": 2**53 - 1, "step": 1},
        ])
        nodes = ["UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21",
                 "KSampler", "VAEDecode", "SaveImage"]
        nodes += ["LoadImage", "QwenImage21Cache"] if edit else ["ResolutionSelector", "EmptyLatentImage"]
        features.append({
            "id": feature_id, "studio": "image", "kind": "generate",
            "title": "Qwen 2.1 指令改圖" if edit else "Qwen 2.1 文生圖",
            "subtitle": "Qwen-Image-2.1 · 多圖參考" if edit else "Qwen-Image-2.1 · 中文排版與原生 2K",
            "builder": "qwen21_edit" if edit else "qwen21_t2i",
            "toolGroup": "img2img" if edit else "text2img",
            "versionLabel": "Qwen-Image 2.1",
            "uploads": [
                {"id": "image" if i == 1 else f"image{i}",
                 "label": f"圖片 {i} · <image{i}>" + ("（原圖）" if i == 1 else "（可選參考）"),
                 "accept": "image/*", "required": i == 1}
                for i in range(1, 5)
            ] if edit else [],
            "fields": fields, "requiredModels": [MODEL, CLIP, VAE], "requiredNodes": nodes,
            "note": (
                "依圖片 1 的比例輸出；參考圖請按順序加入，指令以 <image1> 至 <image4> 指定。"
                if edit else "可直接描述中文海報與排版；PNG 輸出支援透明背景。"
            ),
        })
    return features


def _number(settings: dict, name: str, default: float, low: float, high: float) -> float:
    value = float(settings.get(name, default))
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Qwen 2.1 的 {name} 必須介於 {low:g} 與 {high:g}")
    return value


def compile_prompt(settings: dict, uploads: dict, *, edit: bool, seed: int, prefix: str) -> dict:
    text = str(settings.get("instruction" if edit else "positive") or "").strip()
    if not text:
        raise ValueError("請輸入 Qwen 2.1 的修改指令" if edit else "請輸入 Qwen 2.1 的提示詞")
    steps = _number(settings, "steps", 25, 1, 50)
    if not steps.is_integer():
        raise ValueError("步數必須是整數")
    cfg = _number(settings, "cfg", 1, 1, 5)
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": text,
            "negative_prompt": str(settings.get("negative") or ""), "resolution": 1024,
        }},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["4", 1],
            "latent_image": ["6", 0], "seed": seed, "steps": int(steps), "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }
    if edit:
        if not uploads.get("image"):
            raise ValueError("Qwen 2.1 改圖需要圖片 1")
        resolution = str(settings.get("resolution", "1024"))
        if resolution not in {"1024", "1536", "2048"}:
            raise ValueError("不支援的 Qwen 2.1 編輯解析度")
        graph["4"]["inputs"]["resolution"] = int(resolution)
        gap = False
        for i in range(1, 5):
            name = uploads.get("image" if i == 1 else f"image{i}")
            if not name:
                gap = True
                continue
            if gap:
                raise ValueError("參考圖片請按圖片 1、2、3、4 的順序加入")
            node_id = str(10 + i)
            graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            graph["4"]["inputs"][f"images.image_{i}"] = [node_id, 0]
        graph["5"] = {"class_type": "QwenImage21Cache", "inputs": {
            "model": ["1", 0], "device": "auto", "dtype": "default",
        }}
        graph["7"]["inputs"].update(model=["5", 0], latent_image=["4", 2])
    else:
        ratio = str(settings.get("aspectRatio") or "1:1")
        megapixels = str(settings.get("megapixels", "1"))
        if ratio not in ASPECTS or megapixels not in {"1", "2", "4"}:
            raise ValueError("不支援的 Qwen 2.1 畫面比例或輸出大小")
        graph["5"] = {"class_type": "ResolutionSelector", "inputs": {
            "aspect_ratio": ASPECTS[ratio], "megapixels": float(megapixels), "multiple": 32,
        }}
        graph["6"] = {"class_type": "EmptyLatentImage", "inputs": {
            "width": ["5", 0], "height": ["5", 1], "batch_size": 1,
        }}
    return graph
