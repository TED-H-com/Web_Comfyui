# -*- coding: utf-8 -*-
"""Pure functions that turn one manifest job into a ComfyUI API prompt (no network, no files except the templates).

Shared by remote/remote_job.py (on Colab, and in local mode through scripts/h3local.py) and scripts/selftest_graph.py.
The templates in workflows/ are already clean API graphs (no VHS, no preview nodes): they are posted as they are,
after the parameters below are written into them. Nothing is stripped at run time.

Ported from H3 Toybox (dseditor/minimax-h3-colab, toybox/server.py): DEFAULT_CONFIG steps / loras / intensity /
extend, dims(), round32(), resolve_mentions(), alignment_line(), raw_prompt(), bypass(), build_generate(),
image_dims(), image_refs() (P-H3 branch), ph3_graph(), check_image_wiring().

A "media item" is a dict describing one file already placed in ComfyUI's input folder:
    {"kind": "image"|"video"|"audio", "name": <file name in ComfyUI input>,
     "width": int|None, "height": int|None, "has_audio": bool (videos)}
"""
import copy
import json
import random
import re
from pathlib import Path

FPS = 24
WORKFLOWS = Path(__file__).resolve().parent / "workflows"
VIDEO_TEMPLATE = "h3_video_api.json"
PICTURE_TEMPLATE = "h3_picture_api.json"

# fast mode (default on): fewer SelfLift steps, [total, low-resolution steps]
STEPS = {"normal": (12, 6), "fast": (8, 4)}
# LoRA node ids in h3_video_api.json. "drama" always applies; "action" only when intensity is not off,
# with strength = template strength x multiplier
LORAS_DRAMA = ("53", "400", "404")
LORAS_ACTION = ("342", "344", "343")
INTENSITY = {"low": 0.6, "mid": 1.0, "high": 1.3}
# extension (H3 Motion Context): frames of the source tail pinned into the head of the new clip
EXTEND = {"context_length": "22", "audio_context_length": 24, "tail_seconds": 3.0}
# long side of a picture per size key (P-H3 is best at 2K+)
IMAGE_SIZES = {"1K": 1024, "1.3K": 1344, "1.6K": 1664, "2K": 2048, "2.7K": 2720}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "21:9": (21, 9)}

# node ids in h3_video_api.json
N_COND, N_SAMPLER, N_SCHED, N_BRIDGE = "265", "397", "261", "402"
N_VAE, N_AVAE, N_CLIP, N_SECONDS = "36", "37", "38", "259"
N_DECODE, N_ADECODE, N_CREATE, N_SAVE = "253", "254", "cv", "sv"
# node ids in h3_picture_api.json
N_IMG_ENC, N_IMG_NOISE, N_IMG_SAVE = "ienc", "in", "isave"

# never allowed in a graph this skill posts: they need extra node packs or only feed a UI preview
FORBIDDEN_CLASS = ("VHS_", "Preview", "ChunkFeedForward", "ResolutionSelector")

LABEL = {"image": "Picture", "video": "Video", "audio": "Audio"}
# @圖片1 / @影片1 / @音訊1, plus @Image1 / @Video1 / @Audio1
LITERAL_RE = re.compile(r"<(Picture|Video|Audio) (\d+)>")
MENTION_RE = re.compile(r"@(圖片|图片|Image|影片|视频|Video|音訊|音频|Audio)\s?(\d+)", re.IGNORECASE)
MENTION_KIND = {"圖片": "image", "图片": "image", "image": "image", "影片": "video", "视频": "video",
                "video": "video", "音訊": "audio", "音频": "audio", "audio": "audio"}
VIDEO_MODES = {"ref": "ref", "frames": "frames", "first_last": "frames", "text": "text"}


def load_template(name, dirs=()):
    """The first `name` found in dirs, then in the skill's workflows/ folder."""
    for d in list(dirs) + [WORKFLOWS]:
        p = Path(d) / name
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"workflow template not found: {name}")


def is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)


# ---------------------------------------------------------------- sizes, seeds
def round32(x):
    return max(32, int(round(x / 32)) * 32)


def dims(aspect, resolution, auto_size=None):
    """Video size. aspect "auto" follows auto_size (first frame / extension source) and falls back to 16:9."""
    short = int(str(resolution).rstrip("pP"))
    if aspect == "auto" and auto_size and auto_size[0] and auto_size[1]:
        a, b = auto_size
    else:
        a, b = ASPECTS.get(aspect, (16, 9))
    w, h = (short * a / b, short) if a >= b else (short, short * b / a)
    return round32(w), round32(h)


def image_dims(aspect, size_key, refs):
    """Picture size from the long side; aspect "auto" follows the first reference (1:1 without one)."""
    long_side = IMAGE_SIZES.get(size_key, 1344)
    if aspect == "auto":
        a, b = (refs[0]["width"], refs[0]["height"]) if refs and refs[0].get("width") else (1, 1)
    else:
        a, b = ASPECTS.get(aspect, (1, 1))
    w, h = (long_side, long_side * b / a) if a >= b else (long_side * a / b, long_side)
    return round32(w), round32(h)


def pick_seed(seed, rng=None):
    if seed not in (None, "", -1):
        return int(seed)
    return (rng or random).randint(0, 2**48)


# ---------------------------------------------------------------- prompt text
def resolve_mentions(text, items):
    """Map @圖片N mentions (N = position among the items of that kind) to compact H3 tags.

    Only mentioned items are used, numbered by first mention. Returns (text_with_tags, chosen by kind).
    """
    if not MENTION_RE.search(text):
        # Prompt writers that follow the official H3 guide write <Picture N> / <Video N> / <Audio N> directly.
        # Read those as @-mentions in input order; otherwise every reference would be silently dropped.
        text = LITERAL_RE.sub(lambda m: "@" + {"Picture": "Image"}.get(m.group(1), m.group(1)) + m.group(2), text)
    by_kind = {"image": [], "video": [], "audio": []}
    for item in items:
        by_kind[item["kind"]].append(item)
    chosen = {"image": [], "video": [], "audio": []}
    assigned = {}

    def repl(m):
        kind = MENTION_KIND[m.group(1).lower() if m.group(1).isascii() else m.group(1)]
        n = int(m.group(2))
        pool = by_kind[kind]
        if not 1 <= n <= len(pool):
            raise ValueError(f"找不到參考素材 {m.group(0)}（inputs 裡只有 {len(pool)} 個{kind}）")
        key = (kind, n)
        if key not in assigned:
            chosen[kind].append(pool[n - 1])
            assigned[key] = len(chosen[kind])
        return f"<{LABEL[kind]} {assigned[key]}>"

    return MENTION_RE.sub(repl, text), chosen


def alignment_line(task, duration, last_shot=1):
    """The fixed first line the base guide requires for keyframe tasks."""
    end = f"{duration:.2f}"
    if task == "I2VA":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if task == "FL2VA":
        return ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                f"0.00-second mark of the target video; Picture 2 (from Shot {last_shot}) aligns with the "
                f"{end}-second mark of the target video.")
    if task == "L2VA":
        return ("How the reference pictures align with the target video — <Picture 1> (from "
                f"[Shot {last_shot}]) aligns with the {end}-second mark of the target video.")
    return ""


def raw_prompt(text, task, duration, chosen, extending):
    """The prompt sent to H3 (Toybox's path with the rewriter off, which is what this skill always used)."""
    if task == "Ref2VA":
        if extending:
            k = len(chosen["image"])
            text += f"\n<Picture {k}> is the last frame of the previous clip; the video continues directly from it."
        return text
    head = alignment_line(task, duration)
    if not head:
        return text
    # Writers following the base guide often write this line themselves; keep only ours (its seconds are exact).
    first, _, rest = text.lstrip().partition("\n")
    if first.startswith(head[:30]):
        text = rest.lstrip("\n")
    return head + "\n\n" + text


# ---------------------------------------------------------------- video
def bypass(wf, node_id, port="model"):
    """Remove a pass-through node and wire its consumers to its input."""
    upstream = wf[node_id]["inputs"][port]
    for other in wf.values():
        for k, v in other["inputs"].items():
            if v == [node_id, 0]:
                other["inputs"][k] = upstream
    del wf[node_id]


def plan_video(job, inputs=(), first=None, last=None, extend=None, rng=None):
    """Everything about a video job that does not depend on the template.

    inputs: media items of `inputs` in manifest order (ref mode). first / last: media items (frames mode).
    extend: None, or {"width", "height", "has_audio", "last_frame": <image media item>} of the source video.
    """
    mode = VIDEO_MODES.get(job.get("mode", "ref"))
    if mode is None:
        raise ValueError(f"未知的 mode：{job.get('mode')}")
    duration = float(job.get("seconds", 10))
    text = job["prompt"]
    chosen = {"image": [], "video": [], "audio": []}
    if mode == "ref":
        tagged, chosen = resolve_mentions(text, list(inputs))
        if extend:
            chosen["image"].append(extend["last_frame"])
        task = "Ref2VA"
    elif mode == "frames":
        if not first and not last:
            raise ValueError("頭尾格模式至少要放一張首格或尾格")
        for item in (first, last):
            if item:
                if item["kind"] != "image":
                    raise ValueError("首格與尾格必須是圖片")
                chosen["image"].append(item)
        tagged, task = text, "FL2VA" if first and last else "I2VA" if first else "L2VA"
    else:
        tagged, task = text, "T2VA"
    if extend and mode != "ref":
        raise ValueError("延伸只能用 ref 模式")

    auto_size = None
    if extend:
        auto_size = (extend.get("width"), extend.get("height"))
    elif task in ("I2VA", "FL2VA", "L2VA"):
        f0 = chosen["image"][0]
        auto_size = (f0.get("width"), f0.get("height")) if f0.get("width") else None
    width, height = dims(job.get("aspect", "auto"), job.get("resolution", "480P"), auto_size)

    total, low = STEPS["fast" if job.get("fast", True) else "normal"]
    intensity = job.get("intensity", "off")
    unused = [i for i in inputs if i not in chosen[i["kind"]]] if mode == "ref" else []
    return {"task": task, "mode": mode, "final_prompt": raw_prompt(tagged, task, duration, chosen, bool(extend)),
            "chosen": chosen, "seconds": duration, "width": width, "height": height,
            "steps": int(total), "transition_step": int(low), "seed": pick_seed(job.get("seed"), rng),
            "intensity": intensity, "multiplier": INTENSITY.get(intensity), "extend": bool(extend),
            "unused_inputs": [i["name"] for i in unused]}


def video_graph(template, plan, tail_name=None):
    """The template with the plan written in. tail_name: the conformed source tail in ComfyUI input (extend)."""
    wf = copy.deepcopy(template)
    width, height = plan["width"], plan["height"]
    chosen = plan["chosen"]
    if plan["task"] == "Ref2VA":
        ref = wf[N_COND]["inputs"]
        for k in [k for k in ref if k.startswith("ref_") and "." in k]:  # autogrow slots only
            del ref[k]
        for i, item in enumerate(chosen["image"]):
            wf[f"ri{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["name"]}}
            ref[f"ref_images.ref_image_{i}"] = [f"ri{i}", 0]
        for i, item in enumerate(chosen["video"]):
            wf[f"rv{i}"] = {"class_type": "LoadVideo", "inputs": {"file": item["name"]}}
            wf[f"rc{i}"] = {"class_type": "GetVideoComponents", "inputs": {"video": [f"rv{i}", 0]}}
            ref[f"ref_videos.ref_video_{i}"] = [f"rc{i}", 0]
            if item.get("has_audio") and item.get("use_audio", True):
                ref[f"ref_video_audios.ref_video_audio_{i}"] = [f"rc{i}", 1]
        for i, item in enumerate(chosen["audio"]):
            wf[f"ra{i}"] = {"class_type": "LoadAudio", "inputs": {"audio": item["name"]}}
            ref[f"ref_audios.ref_audio_{i}"] = [f"ra{i}", 0]
        ref["prompt"] = plan["final_prompt"]
        ref["width"], ref["height"] = width, height
    else:  # T2VA / I2VA / FL2VA / L2VA share the stock image-to-video conditioning
        old = wf[N_COND]["inputs"]
        node = {"clip": [N_CLIP, 0], "vae": [N_VAE, 0], "prompt": plan["final_prompt"], "width": width,
                "height": height, "length": old["length"]}
        slots = {"I2VA": ["first_frame"], "FL2VA": ["first_frame", "last_frame"], "L2VA": ["last_frame"]}.get(plan["task"], [])
        for i, (slot, item) in enumerate(zip(slots, chosen["image"])):
            wf[f"kf{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["name"]}}
            wf[f"ks{i}"] = {"class_type": "ImageScale", "inputs": {"image": [f"kf{i}", 0], "upscale_method": "lanczos",
                                                                    "width": width, "height": height, "crop": "center"}}
            node[slot] = [f"ks{i}", 0]
        wf[N_COND] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": node}

    wf[N_SECONDS]["inputs"]["value"] = float(plan["seconds"])
    wf[N_SCHED]["inputs"]["steps"] = plan["steps"]
    wf[N_SAMPLER]["inputs"]["transition_step"] = plan["transition_step"]
    wf[N_SAMPLER]["inputs"]["seed"] = plan["seed"]

    if plan["extend"]:  # pin the source tail into the head of the clip, then cut the pinned frames off again
        if not tail_name:
            raise ValueError("extend needs the conformed tail file")
        wf["ev"] = {"class_type": "LoadVideo", "inputs": {"file": tail_name}}
        wf["ec"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["ev", 0]}}
        wf["mc"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {
            "conditioning": [N_BRIDGE, 0], "vae": [N_VAE, 0], "latent": [N_COND, 1],
            "context_length": str(EXTEND["context_length"]), "audio_context_length": int(EXTEND["audio_context_length"]),
            "context_frames": ["ec", 0], "audio_vae": [N_AVAE, 0], "context_audio": ["ec", 1]}}
        wf[N_SAMPLER]["inputs"]["positive"] = ["mc", 0]
        wf["mt"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {
            "images": [N_DECODE, 0], "trim_frames": ["mc", 1], "audio": [N_ADECODE, 0], "fps": float(FPS),
            "match_tail": True}}
        wf[N_CREATE]["inputs"]["images"], wf[N_CREATE]["inputs"]["audio"] = ["mt", 0], ["mt", 1]

    mult = plan["multiplier"]
    for node_id in LORAS_ACTION:
        node = wf.get(node_id)
        if node is None:
            continue
        if mult is None:  # intensity off: bypass by rewiring consumers to this LoRA's input model
            bypass(wf, node_id)
        else:
            node["inputs"]["strength_model"] = round(node["inputs"]["strength_model"] * mult, 4)
    return wf


# ---------------------------------------------------------------- picture (P-H3-Image on the H3 model)
def plan_picture(job, inputs=(), rng=None):
    """P-H3 reads <Picture N> itself. With @ mentions only the mentioned pictures are used (in mention order);
    without any, every input picture is used in order. Picture 1 is the one edited."""
    text = job["prompt"]
    pictures = [i for i in inputs if i["kind"] == "image"]
    if MENTION_RE.search(text):
        tagged, chosen = resolve_mentions(text, pictures)
        refs = chosen["image"]
    else:
        tagged, refs = text, pictures
    if len(refs) > 8:
        raise ValueError("P-H3 編輯最多 8 張圖（第 1 張是要改的那張）")
    size = job.get("size", "2K")
    w, h = image_dims(job.get("aspect", "auto"), size, refs)
    return {"task": "Edit" if refs else "T2I", "final_prompt": tagged, "refs": refs, "width": w, "height": h,
            "seed": pick_seed(job.get("seed"), rng), "size": size}


def picture_graph(template, plan):
    g = copy.deepcopy(template)
    enc = g[N_IMG_ENC]["inputs"]
    for k in [k for k in enc if k.startswith("images.")]:
        del enc[k]
    enc["prompt"], enc["width"], enc["height"] = plan["final_prompt"], plan["width"], plan["height"]
    g[N_IMG_NOISE]["inputs"]["noise_seed"] = plan["seed"]
    for i, item in enumerate(plan["refs"], 1):  # image_1 is the picture edited, the rest are references, in order
        g[f"ir{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["name"]}}
        enc[f"images.image_{i}"] = [f"ir{i}", 0]
    return g


# ---------------------------------------------------------------- checks (before posting, and on what really ran)
def check_graph(g):
    """Structural problems of an API graph: forbidden classes, missing class_type, dangling links."""
    problems = []
    for nid, node in g.items():
        if not isinstance(node, dict) or "class_type" not in node or not isinstance(node.get("inputs"), dict):
            problems.append(f"node {nid}: not an API node (class_type + inputs)")
            continue
        ct = node["class_type"]
        if any(f in ct for f in FORBIDDEN_CLASS):
            problems.append(f"node {nid}: forbidden class {ct}")
        for port, v in node["inputs"].items():
            if is_link(v) and v[0] not in g:
                problems.append(f"node {nid}.{port} -> missing node {v[0]}")
    return problems


def _source(g, link, cls):
    node = g.get(link[0], {}) if is_link(link) else {}
    return node if node.get("class_type") == cls else None


def check_video(g, plan, tail_name=None):
    """Does this graph (built, or read back from /history) really carry the plan?"""
    problems = check_graph(g)
    if problems:
        return problems
    cond = g.get(N_COND, {})
    ci = cond.get("inputs", {})
    want_class = "MiniMaxH3ReferenceToVideo" if plan["task"] == "Ref2VA" else "MiniMaxH3ImageToVideo"
    if cond.get("class_type") != want_class:
        problems.append(f"{N_COND} is {cond.get('class_type')}, expected {want_class}")
    if ci.get("prompt") != plan["final_prompt"]:
        problems.append("prompt in the graph differs from the planned prompt")
    if (ci.get("width"), ci.get("height")) != (plan["width"], plan["height"]):
        problems.append(f"size {ci.get('width')}x{ci.get('height')} != {plan['width']}x{plan['height']}")
    if plan["task"] == "Ref2VA":
        expect = {"image": ("ref_images.ref_image_", "LoadImage", "image"),
                  "video": ("ref_videos.ref_video_", "GetVideoComponents", None),
                  "audio": ("ref_audios.ref_audio_", "LoadAudio", "audio")}
        for kind, (prefix, cls, field) in expect.items():
            slots = sorted(k for k in ci if k.startswith(prefix))
            if len(slots) != len(plan["chosen"][kind]):
                problems.append(f"{kind} ref slots {len(slots)} != {len(plan['chosen'][kind])} chosen")
                continue
            for i, item in enumerate(plan["chosen"][kind]):
                src = _source(g, ci.get(f"{prefix}{i}"), cls)
                if src is None:
                    problems.append(f"{prefix}{i} is not wired to a {cls}")
                    continue
                if kind == "video":
                    src = _source(g, src["inputs"].get("video"), "LoadVideo")
                    field = "file"
                if src is None or src["inputs"].get(field) != item["name"]:
                    problems.append(f"{prefix}{i} does not load {item['name']}")
    else:
        slots = {"I2VA": ["first_frame"], "FL2VA": ["first_frame", "last_frame"], "L2VA": ["last_frame"]}.get(plan["task"], [])
        for slot in ("first_frame", "last_frame"):
            if (slot in ci) != (slot in slots):
                problems.append(f"{plan['task']}: {slot} {'missing' if slot in slots else 'should not be set'}")
        for slot, item in zip(slots, plan["chosen"]["image"]):
            scale = _source(g, ci.get(slot), "ImageScale")
            load = _source(g, scale["inputs"].get("image"), "LoadImage") if scale else None
            if not load or load["inputs"].get("image") != item["name"]:
                problems.append(f"{slot} does not load {item['name']}")
            elif (scale["inputs"].get("width"), scale["inputs"].get("height")) != (plan["width"], plan["height"]):
                problems.append(f"{slot} is not scaled to the video size")
    if g.get(N_SECONDS, {}).get("inputs", {}).get("value") != float(plan["seconds"]):
        problems.append(f"seconds node {N_SECONDS} != {plan['seconds']}")
    si = g.get(N_SAMPLER, {}).get("inputs", {})
    if g.get(N_SCHED, {}).get("inputs", {}).get("steps") != plan["steps"] or si.get("transition_step") != plan["transition_step"]:
        problems.append(f"steps != {plan['steps']}/{plan['transition_step']}")
    if si.get("seed") != plan["seed"]:
        problems.append("seed differs")
    for nid in LORAS_DRAMA:
        if nid not in g:
            problems.append(f"drama LoRA {nid} missing")
    for nid in LORAS_ACTION:
        if (nid in g) != (plan["multiplier"] is not None):
            problems.append(f"action LoRA {nid} {'missing' if plan['multiplier'] is not None else 'still in the graph with intensity off'}")
    # the model chain must still reach the UNet from the sampler
    seen, link = set(), si.get("model")
    while is_link(link) and link[0] not in seen:
        seen.add(link[0])
        node = g.get(link[0], {})
        if node.get("class_type") == "UNETLoader":
            break
        link = node.get("inputs", {}).get("model")
    else:
        problems.append("sampler model chain does not reach the UNETLoader")
    mc = g.get("mc")
    if plan["extend"]:
        if not mc or si.get("positive") != ["mc", 0]:
            problems.append("extend: sampler positive is not the MotionContext")
        ev = _source(g, (mc or {}).get("inputs", {}).get("context_frames"), "GetVideoComponents")
        ev = _source(g, ev["inputs"].get("video"), "LoadVideo") if ev else None
        if tail_name and (not ev or ev["inputs"].get("file") != tail_name):
            problems.append("extend: MotionContext does not load the source tail")
        if g.get(N_CREATE, {}).get("inputs", {}).get("images") != ["mt", 0]:
            problems.append("extend: CreateVideo is not fed by MotionContextTrim")
    elif mc:
        problems.append("MotionContext present without extend")
    save = g.get(N_SAVE, {})
    if save.get("class_type") != "SaveVideo" or save.get("inputs", {}).get("video") != [N_CREATE, 0]:
        problems.append("SaveVideo is not wired to CreateVideo")
    return problems


def check_picture(g, plan):
    """Read back the graph: a LoadImage that exists but is not wired into the encoder's dynamic `images` input
    passes every other check and silently turns an edit into plain text-to-image."""
    problems = check_graph(g)
    if problems:
        return problems
    enc = g.get(N_IMG_ENC, {})
    ei = enc.get("inputs", {})
    if ei.get("prompt") != plan["final_prompt"]:
        problems.append("prompt in the graph differs from the planned prompt")
    if (ei.get("width"), ei.get("height")) != (plan["width"], plan["height"]):
        problems.append("picture size differs")
    slots = [k for k in ei if k.startswith("images.")]
    if len(slots) != len(plan["refs"]):
        problems.append(f"picture slots {len(slots)} != {len(plan['refs'])} references")
    for i, item in enumerate(plan["refs"], 1):
        src = _source(g, ei.get(f"images.image_{i}"), "LoadImage")
        if not src or src["inputs"].get("image") != item["name"]:
            problems.append(f"參考圖第 {i} 張沒有接進 {enc.get('class_type', '編碼節點')} 的 images.image_{i}")
    if g.get(N_IMG_NOISE, {}).get("inputs", {}).get("noise_seed") != plan["seed"]:
        problems.append("seed differs")
    return problems


def required_classes(pictures=True, dirs=()):
    """Every node class a run can post (templates + nodes added at build time), for `h3local.py check`."""
    item = lambda kind, n: {"kind": kind, "name": n, "width": 640, "height": 360, "has_audio": True}
    t = load_template(VIDEO_TEMPLATE, dirs)
    graphs = []
    for job, kw, tail in (
            ({"mode": "ref", "prompt": "@圖片1 @影片1 @音訊1", "intensity": "mid"},
             {"inputs": [item("image", "a"), item("video", "b"), item("audio", "c")],
              "extend": {"width": 640, "height": 360, "last_frame": item("image", "l")}}, "t"),
            ({"mode": "frames", "prompt": "x"}, {"first": item("image", "a"), "last": item("image", "b")}, None)):
        graphs.append(video_graph(t, plan_video(job, **kw), tail))
    if pictures:
        p = plan_picture({"prompt": "x"}, [item("image", "a")])
        graphs.append(picture_graph(load_template(PICTURE_TEMPLATE, dirs), p))
    return sorted({n["class_type"] for g in graphs for n in g.values()})
