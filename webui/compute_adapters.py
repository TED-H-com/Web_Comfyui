"""Strict, offline conversion of validated Studio intents to the PRISM recipe.

This module never resolves upload IDs, establishes character ownership, probes a
provider, or submits work. The server must do those checks first, and retain its
character/provenance metadata alongside the returned provider request. Different
engines are explicitly different recipes; an unsupported creative option must
not disappear during routing. ``normalise_intent`` is an explicit, reportable
translation of known local engine defaults, not part of strict compilation.
"""
from __future__ import annotations

import base64
import copy
import math
import os
from pathlib import Path
import re
import stat
from typing import Mapping, Sequence
import uuid

try:
    from . import minimax_h3_product as H3
    from .qwen_image21 import compile_character_prompt
except ImportError:  # server.py also supports script startup
    import minimax_h3_product as H3
    from qwen_image21 import compile_character_prompt


MAX_INPUT_BYTES = 32 * 1024 * 1024
RECIPE_ID = "h3-prism-ph3.v1"
RECIPE_LABEL = "PRISM / P-H3（不同於本機原工作流程）"
SUPPORTED_FEATURES = frozenset({
    "text2img", "qwen21-text2img", "img2img", "qwen21-edit",
    "character-photoshoot", "h3-text-video", "h3-image-video", "h3-reference-video",
})
_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO = {".mp4", ".mov", ".webm"}
_AUDIO = {".wav", ".mp3", ".flac", ".m4a"}
_ASPECTS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}
_SIZES = {"1K": 1024, "1.3K": 1344, "1.6K": 1664, "2K": 2048, "2.7K": 2720}
_CONTEXT = {"_characterId", "_characterName", "_characterActionId", "_characterVariantId",
            "_characterVisualClass", "_characterReference", "_characterGender", "_characterAppearance"}
_COMMON = {"seed", "visualStyle", *_CONTEXT}
_IMAGE_FIELDS = {
    "text2img": {"positive", "negative", "aspect", "steps", "cfg", "batchSize", "useDopsd"},
    "qwen21-text2img": {"positive", "negative", "aspectRatio", "megapixels", "steps", "cfg"},
    "img2img": {"instruction", "steps", "guidance", "denoise"},
    "qwen21-edit": {"instruction", "resolution", "negative", "steps", "cfg"},
    "character-photoshoot": {"instruction", "resolution", "negative", "steps", "cfg"},
}
_H3_FIELDS = {"aspect", "frames", "quality", "acceleration", "prompt", "seconds",
              "resolution", "fast", "intensity"}


class AdapterError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str):
    raise AdapterError(code, message)


def _settings(feature_id, settings):
    if type(feature_id) is not str or feature_id not in SUPPORTED_FEATURES:
        _fail("feature_unsupported", "這個功能尚未提供 PRISM / P-H3 執行方式。")
    if not isinstance(settings, Mapping) or any(type(k) is not str for k in settings):
        _fail("settings_invalid", "生成設定必須是物件。")
    if feature_id.startswith("h3-"):
        headings = H3.REF2VA_HEADINGS if feature_id == "h3-reference-video" else H3.T2V_HEADINGS
        fields = _H3_FIELDS | set(headings)
        if feature_id != "h3-text-video":
            fields |= {"description", "motionProfile"}
    else:
        fields = _IMAGE_FIELDS[feature_id] | {"size", "aspect", "width", "height"}
    allowed = _COMMON | fields
    if set(settings) - allowed:
        _fail("setting_unsupported", "PRISM / P-H3 無法保留其中的設定；請改用適用的工作流程。")
    return dict(settings)


def normalise_intent(feature_id: str, settings: Mapping) -> tuple[dict, list[str]]:
    """Translate only known engine defaults; callers must display the warnings.

    Geometry, nonempty negative prompts, and custom sampler options are never
    removed. The returned settings still need ``colab_compatibility``. This is
    intentionally separate from strict compilation so automatic routing cannot
    silently reinterpret an exact legacy request.
    """
    result = copy.deepcopy(_settings(feature_id, settings))
    defaults = {"negative": "", "batchSize": 1, "useDopsd": False, "cfg": 1.0}
    if feature_id in {"qwen21-text2img", "qwen21-edit", "character-photoshoot"}:
        defaults["steps"] = 25
    elif feature_id == "text2img":
        defaults["steps"] = 8
    elif feature_id == "img2img":
        defaults.update(steps=20, guidance=2.5, denoise=1.0)
    if feature_id.startswith("h3-"):
        defaults["acceleration"] = "stock"
    removed = []
    for key, default in defaults.items():
        if key not in result:
            continue
        value = result[key]
        equal = value == default and type(value) is type(default)
        if type(default) in (int, float) and type(value) in (int, float):
            equal = value == default
        if equal:
            removed.append(key)
            del result[key]
    if result.get("quality") == "quality" and "frames" not in result and "seconds" not in result:
        result.pop("quality")
        result["frames"] = 124
        removed.append("quality → 124 frames")
    warnings = []
    if removed:
        warnings.append("改用 PRISM / P-H3 的固定推論配方；本機預設選項已轉換：" + ", ".join(removed))
    return result, warnings


def _seed(value):
    if type(value) is str and re.fullmatch(r"-1|0|[1-9][0-9]{0,19}", value):
        value = int(value)
    if type(value) is not int or not -1 <= value < 2**64:
        _fail("seed_invalid", "種子必須是 -1 或 uint64 十進位整數。")
    return value


def _text(value):
    if type(value) is not str or not value.strip() or "\0" in value or len(value) > 16000:
        _fail("prompt_invalid", "描述須為 1–16000 字，且不能包含空字元。")
    return value.strip()


def _style(settings):
    bound = settings.get("_characterVisualClass", "")
    chosen = settings.get("visualStyle", "")
    if bound not in ("", "anime", "realistic") or chosen not in ("", "anime", "realistic"):
        _fail("style_invalid", "畫風只支援動畫或寫實。")
    if bound and chosen and bound != chosen:
        _fail("style_conflict", "畫風與已綁定角色不一致。")
    return bound or chosen


def _slot_order(feature_id, slots):
    if isinstance(slots, (str, bytes)) or not isinstance(slots, (Sequence, Mapping)):
        _fail("inputs_invalid", "參考素材欄位必須是有序清單或物件。")
    slots = list(slots)
    if any(type(slot) is not str for slot in slots) or len(set(slots)) != len(slots) or len(slots) > 8:
        _fail("inputs_invalid", "最多提供 8 個不重複的參考素材欄位。")
    keys = set(slots)
    if feature_id in {"text2img", "qwen21-text2img", "h3-text-video"}:
        if keys:
            _fail("inputs_unsupported", "這個文字生成模式不能附帶參考素材。")
        return []
    if feature_id in {"img2img", "character-photoshoot"}:
        if keys != {"image"}:
            _fail("inputs_invalid", "這個模式需要且只接受一張角色或原始圖片。")
        return ["image"]
    if feature_id == "qwen21-edit":
        order = ["image", "image2", "image3", "image4"]
        if not keys or keys != set(order[:len(keys)]):
            _fail("inputs_invalid", "參考圖片須從圖片 1 起連續提供，最多 4 張。")
        return order[:len(keys)]
    if feature_id == "h3-image-video":
        if keys - {"image", "first_frame", "last_frame"} or {"image", "first_frame"} <= keys:
            _fail("inputs_invalid", "首尾格素材欄位不正確。")
        first = "first_frame" if "first_frame" in keys else "image"
        if first not in keys:
            _fail("last_frame_only_unsupported", "目前 Colab 介面不能保留僅尾格的條件；請使用本機或提供首格。")
        return [first] + (["last_frame"] if "last_frame" in keys else [])
    order = []
    for group in (["reference", *[f"reference{i}" for i in range(2, 10)]],
                  [f"video{i}" for i in range(1, 4)], [f"audio{i}" for i in range(1, 4)]):
        selected = [slot for slot in group if slot in keys]
        if selected != group[:len(selected)]:
            _fail("inputs_invalid", "每類參考素材的編號須從 1 起連續提供。")
        order.extend(selected)
    if not order or set(order) != keys:
        _fail("inputs_invalid", "多參考模式需要合法的圖片、影片或音訊欄位。")
    return order


def _kind(slot):
    return "video" if slot.startswith("video") else "audio" if slot.startswith("audio") else "image"


def _image_job(feature_id, settings, slots):
    for key in ("negative",):
        if key in settings and settings[key] != "":
            _fail("negative_unsupported", "P-H3 不支援獨立負向提示詞，不能自動刪除。")
    fixed = {"steps": 8, "cfg": 1, "batchSize": 1, "useDopsd": False, "denoise": 1}
    for key, expected in fixed.items():
        if key in settings and (settings[key] != expected or (key != "useDopsd" and type(settings[key]) not in (int, float)) or (key == "useDopsd" and type(settings[key]) is not bool)):
            _fail("engine_option_unsupported", f"P-H3 無法保留 {key}；請使用相容的配方設定。")
    if "guidance" in settings:
        _fail("engine_option_unsupported", "Flux Guidance 無法對應 P-H3。")
    prompt = _text(settings.get("instruction" if slots else "positive"))
    visual_style = _style(settings)
    if feature_id == "character-photoshoot":
        # Reuse the original identity/style prose, not the Qwen graph or sampler.
        graph = compile_character_prompt({"instruction": prompt, "visualStyle": visual_style},
                                         {"image": "reference.png"}, seed=0, prefix="adapter")
        prompt = graph["4"]["inputs"]["prompt"]
    elif visual_style:
        style = ("Use a consistent 2D anime illustration style across characters, clothing and scenery."
                 if visual_style == "anime" else
                 "Use a consistent realistic photographic style across people, clothing and scenery.")
        prompt = style + "\n\n" + prompt
    if slots:
        prompt = re.sub(r"<image([1-4])>", lambda m: f"<Picture {m[1]}>", prompt, flags=re.IGNORECASE)
        if re.search(r"<image\b|<image[0-9]+>", prompt, re.IGNORECASE):
            _fail("reference_invalid", "圖片引用格式或編號不受支援。")
        # P-H3's @-mention path may filter/reorder images. Keep this adapter on
        # canonical literal tags and explicitly bind the edit source and order.
        inventory = ", ".join(f"<Picture {i}>" for i in range(1, len(slots) + 1))
        prompt = "Reference images in supplied order (the first is the edit source): " + inventory + ".\n\n" + prompt
    aspect = settings.get("aspect", settings.get("aspectRatio", "auto" if slots else "1:1"))
    if "width" in settings or "height" in settings:
        if any(key in settings for key in ("aspect", "aspectRatio", "size", "megapixels", "resolution")):
            _fail("geometry_conflict", "精確寬高不可與其他畫布大小選項混用。")
        width, height = settings.get("width"), settings.get("height")
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            _fail("geometry_unsupported", "精確寬高必須是正整數。")
        aspect = f"{width}x{height}"
    if "aspect" in settings and "aspectRatio" in settings:
        _fail("geometry_conflict", "不能同時指定兩個畫面比例設定。")
    size = settings.get("size")
    if type(aspect) is str and re.fullmatch(r"[0-9]+x[0-9]+", aspect):
        if size is not None:
            _fail("geometry_conflict", "精確尺寸不能與 P-H3 尺寸預設同時使用。")
        from scripts.colab_remote.h3graph import image_dims
        width, height = (int(x) for x in aspect.split("x"))
        matches = [(a, s) for a in _ASPECTS for s in _SIZES if image_dims(a, s, []) == (width, height)]
        if not matches:
            _fail("geometry_unsupported", "P-H3 無法保持此精確像素尺寸；請明確選擇 P-H3 輸出大小。")
        aspect, size = sorted(matches)[0]
    if type(aspect) is not str or aspect not in _ASPECTS | {"auto"} or (aspect == "auto" and not slots):
        _fail("geometry_unsupported", "P-H3 不支援此畫面比例。")
    if "megapixels" in settings:
        mp = str(settings["megapixels"])
        if size is not None or aspect != "1:1" or mp not in {"1", "4"}:
            _fail("geometry_unsupported", "此百萬像素設定無法精確對應 P-H3；請明確選擇輸出大小。")
        size = {"1": "1K", "4": "2K"}[mp]
    if "resolution" in settings:
        if size is not None or str(settings["resolution"]) not in {"1024", "2048"}:
            _fail("geometry_unsupported", "此編輯解析度無法對應 P-H3 長邊尺寸。")
        size = {"1024": "1K", "2048": "2K"}[str(settings["resolution"])]
    size = "1K" if size is None else size
    if type(size) is not str or size not in _SIZES:
        _fail("geometry_unsupported", "不支援的 P-H3 輸出大小。")
    return {"type": "image", "prompt": _text(prompt), "aspect": aspect, "size": size}


def _video_job(feature_id, settings, slots):
    if "acceleration" in settings:
        _fail("engine_option_unsupported", "本機 Stock / Sage / Cache 模式與 PRISM 不同；請先選擇 PRISM 配方。")
    visual_style = _style(settings)
    frames = settings.get("frames", 124 if settings.get("quality") == "quality" else None)
    if "quality" in settings and (settings["quality"] != "quality" or "frames" in settings or "seconds" in settings):
        _fail("duration_invalid", "不能混用或轉換這個品質／時長選項。")
    if frames is not None:
        if type(frames) is str and re.fullmatch(r"[0-9]+", frames):
            frames = int(frames)
        if type(frames) is not int or frames not in H3.LEGAL_FRAMES or "seconds" in settings:
            _fail("duration_invalid", "幀數須在 124–362 的 17k+5 格點，且不可同時指定秒數。")
        seconds = min(15, frames / 24)
    else:
        seconds = settings.get("seconds", 124 / 24)
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 2 <= seconds <= 15:
        _fail("duration_invalid", "PRISM 影片長度須為 2–15 秒。")
    if frames is None:
        target = max(5, round(seconds * 24))
        frames = target + (5 - target % 17) % 17
    aspect = settings.get("aspect", "landscape-preview")
    if type(aspect) is not str:
        _fail("geometry_unsupported", "畫面比例須為有效的文字選項。")
    native = {"landscape": "16:9", "portrait": "9:16", "square": "1:1",
              "landscape-preview": "16:9", "portrait-preview": "9:16", "square-preview": "1:1"}
    resolution = settings.get("resolution")
    if resolution is None:
        if aspect not in {"landscape-preview", "portrait-preview"}:
            _fail("geometry_unsupported", "原本 H3 畫布與 PRISM 不同；請明確選擇 PRISM 解析度，或使用 480P 橫／直預覽。")
        resolution = "480P"
    if type(resolution) is not str or resolution not in {"480P", "720P", "1080P"}:
        _fail("geometry_unsupported", "PRISM 只支援 480P、720P、1080P。")
    ratio = native.get(aspect, aspect)
    if ratio not in _ASPECTS | {"auto"}:
        _fail("geometry_unsupported", "不支援的 PRISM 畫面比例。")
    mode = {"h3-text-video": "text", "h3-image-video": "frames", "h3-reference-video": "ref"}[feature_id]
    if "prompt" in settings:
        if any(k in settings for k in (*H3.T2V_HEADINGS, *H3.REF2VA_HEADINGS, "description", "motionProfile")):
            _fail("prompt_conflict", "不能混用單段與結構化描述。")
        if visual_style or any(k in settings for k in _CONTEXT):
            _fail("character_prompt_invalid", "角色影片須保留角色描述與動作規則。")
        prompt = _text(settings["prompt"])
    else:
        headings = H3.REF2VA_HEADINGS if mode == "ref" else H3.T2V_HEADINGS
        if visual_style and any(key in settings for key in headings):
            _fail("prompt_conflict", "角色描述不能混用通用影片的結構化欄位。")
        if not visual_style and any(key in settings for key in ("description", "motionProfile")):
            _fail("character_prompt_invalid", "角色描述與動作必須綁定角色畫風。")
        # Use the existing product compiler for its exact character guardrails,
        # motion prose, section headings and reference validation. Its graph is
        # discarded. Only prompt text is passed to the distinct PRISM recipe.
        canonical = {**settings, "seed": 0, "acceleration": "stock", "frames": max(124, frames),
                     "aspect": aspect if aspect in native else "landscape-preview"}
        canonical.pop("quality", None)
        uploads = {slot: slot + {"image": ".png", "video": ".mp4", "audio": ".wav"}[_kind(slot)] for slot in slots}
        try:
            graph = H3.compile_product_prompt(feature_id=feature_id, visual_class=visual_style or None,
                settings=canonical, uploads=uploads, output_token="adapter")
        except ValueError as error:
            _fail("prompt_invalid", str(error))
        prompt = graph["5"]["inputs"]["prompt"]
        if any(_kind(slot) == "video" for slot in slots) and re.search(r"<\s*Audio\s+\d+\s*>", prompt, re.IGNORECASE):
            _fail("video_audio_reference_unsupported", "本機影片內音訊編號與 PRISM 不同，不能自動轉換 Audio 引用。")
    if mode != "ref" and "prompt" in settings:
        # The frame compiler inserts its own alignment tags. Free-form prompt
        # tags cannot introduce additional media or bypass that ordering.
        if re.search(r"@(?:Image|Video|Audio|圖片|影片|音訊)\d+|<\s*(?:Picture|Video|Audio)\s+\d+\s*>", prompt, re.IGNORECASE):
            _fail("references_invalid", "文字／首尾格模式的自由描述不可另外指定參考編號。")
    fast = settings.get("fast", True)
    intensity = settings.get("intensity", "off")
    if type(fast) is not bool or type(intensity) is not str or intensity not in {"off", "low", "mid", "high"}:
        _fail("engine_option_unsupported", "無效的 PRISM 推論速度或動作強度。")
    return {"type": "video", "mode": mode, "prompt": _text(prompt), "seconds": seconds,
            "aspect": ratio, "resolution": resolution, "fast": fast, "intensity": intensity}


def _compile(feature_id, settings, file_slots):
    settings = _settings(feature_id, settings)
    slots = _slot_order(feature_id, file_slots)
    job = (_video_job if feature_id.startswith("h3-") else _image_job)(feature_id, settings, slots)
    job["seed"] = _seed(settings.get("seed", -1))
    # Exercise the actual worker schema offline, including reference semantics.
    from scripts.colab_remote.remote_job import validate_manifest
    from scripts.colab_remote.h3graph import resolve_mentions, MENTION_RE, LITERAL_RE
    inventory = []
    items = []
    names = []
    for index, slot in enumerate(slots):
        kind = _kind(slot)
        name = f"input/{index + 1}" + {"image": ".png", "video": ".mp4", "audio": ".wav"}[kind]
        inventory.append({"path": name, "size": 1, "sha256": "0" * 64})
        items.append({"kind": kind, "name": name})
        names.append(name)
    spec = {**job, "id": "output"}
    if job.get("mode") == "frames":
        spec["first"] = names[0]
        if len(names) == 2:
            spec["last"] = names[1]
    else:
        spec["inputs"] = names
    try:
        if job["type"] == "image" and (MENTION_RE.search(job["prompt"]) or LITERAL_RE.search(job["prompt"])):
            _, chosen = resolve_mentions(job["prompt"], items)
            if sum(map(len, chosen.values())) != len(items):
                raise ValueError("Reference inventory mismatch")
        validate_manifest({"schema_version": 1, "job_id": "00000000-0000-4000-8000-000000000001",
                           "deadline_epoch": 1, "stop_on_error": True, "jobs": [spec],
                           "input_files": inventory}, Path("/adapter-validation"), check_files=False)
    except ValueError:
        _fail("references_invalid", "描述中的參考編號或素材種類不相容；所有參考素材都須明確使用。")
    return job, slots


def colab_compatibility(feature_id: str, settings: Mapping, file_slots=()) -> dict:
    """Pure, no-I/O route eligibility; supplied slots are names, never paths."""
    result = {"recipe_id": RECIPE_ID, "recipe_label": RECIPE_LABEL, "supported": False}
    try:
        job, slots = _compile(feature_id, settings, file_slots)
    except (AdapterError, TypeError, ValueError) as error:
        return {**result, "code": getattr(error, "code", "settings_invalid"), "reason": str(error)}
    return {**result, "supported": True, "code": "supported", "reason": "",
            "output_kind": job["type"], "file_slots": slots,
            "output_settings": {k: v for k, v in job.items() if k not in {"prompt", "seed"}}}


def _read_owned(path, remaining):
    if not isinstance(path, (str, os.PathLike)) or not Path(path).is_absolute():
        _fail("file_invalid", "素材必須由伺服器解析為已驗證的絕對路徑。")
    fd = None
    try:
        # NOFOLLOW prevents a last-component swap; ownership and containment of
        # ancestors are the server resolver's responsibility.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or (hasattr(os, "geteuid") and before.st_uid != os.geteuid()):
            _fail("file_invalid", "素材不是目前服務擁有的普通檔案。")
        if not 0 < before.st_size <= remaining:
            _fail("input_too_large", "參考素材不可為空，且合計上限為 32 MiB。")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(remaining + 1)
            after = os.fstat(stream.fileno())
        if len(data) != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            _fail("file_changed", "素材讀取期間有變動，請重新提交。")
        return data
    except OSError:
        _fail("file_invalid", "無法安全讀取已選擇的素材。")
    finally:
        if fd is not None:
            os.close(fd)


def build_colab_request(feature_id: str, settings: Mapping, resolved_files: Mapping, *,
                        request_id: str, account_id: str, gpu: str = "L4",
                        max_units: float = 20, max_minutes: int = 60) -> dict:
    """Build exactly ColabService.submit's body, reading only explicit files.

    ``resolved_files`` maps upload slot names to caller-validated, owned paths.
    Ordering is reconstructed from the feature's numbered slots, never dict
    insertion order. At most 32 MiB of source bytes are read. ColabService still
    performs actual media decoding/probing before accepting a paid assignment.
    """
    if not isinstance(resolved_files, Mapping):
        _fail("inputs_invalid", "素材必須依欄位提供。")
    job, slots = _compile(feature_id, settings, resolved_files)
    for value in (request_id, account_id):
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, AttributeError):
            _fail("identity_invalid", "請求與帳戶識別碼須為標準 UUID。")
    if type(gpu) is not str or gpu not in {"L4", "G4", "A100", "H100"}:
        _fail("gpu_unsupported", "不支援的 Colab GPU 選項。")
    if type(max_units) not in (int, float) or not math.isfinite(max_units) or not 5 <= max_units <= 2000 or type(max_minutes) is not int or not 5 <= max_minutes <= 180:
        _fail("budget_invalid", "點數上限須為 5–2000，時間上限須為 5–180 分鐘。")
    files, total = [], 0
    for index, slot in enumerate(slots):
        path = resolved_files[slot]
        if not isinstance(path, (str, os.PathLike)):
            _fail("file_invalid", "素材路徑格式不正確。")
        suffix = Path(path).suffix.lower()
        if suffix not in {"image": _IMAGE, "video": _VIDEO, "audio": _AUDIO}[_kind(slot)]:
            _fail("media_kind_invalid", "素材副檔名與選擇的欄位種類不符。")
        data = _read_owned(path, MAX_INPUT_BYTES - total)
        total += len(data)
        files.append({"name": f"reference-{index + 1}{suffix}", "data": base64.b64encode(data).decode("ascii")})
    return {"request_id": request_id, "account_id": account_id, "gpu": gpu,
            "max_units": max_units, "max_minutes": max_minutes, "job": job, "files": files}
