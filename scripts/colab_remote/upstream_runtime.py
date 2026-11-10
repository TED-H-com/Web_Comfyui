# -*- coding: utf-8 -*-
"""Runs ON the Colab VM. Started in the background by start.py; progress goes to /content/h3job/status.json.

One batch = /content/h3job/manifest.json (written by the local h3run.py). Steps:
  1 install (skipped when the marker exists): ComfyUI + 3 node packs
  2 models: H3 PRISM int8 + Qwen3-VL text encoder + video/audio VAE + LoRAs; the P-H3 picture set only when
    the batch has an image job
  3 start ComfyUI (127.0.0.1:8188), private to the VM
  4 every job in order -> /content/h3job/out/<id>.<ext>
Only ComfyUI is needed. Videos, extensions and pictures are built by h3graph.py from the clean templates in
workflows/ (h3_video_api.json, h3_picture_api.json) and posted straight to ComfyUI's /prompt; an extension's tail
and the final join are made here with ffmpeg. TTS posts the 32x32 H3 graph (sound only, audio VAE decode).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # h3graph.py sits next to this file (also on Colab)
try:
    from . import h3graph as G
except ImportError:
    import h3graph as G

# H3JOB_LOCAL_TEST=1 runs the job part against a ComfyUI already running on a desktop (no install, no downloads);
# scripts/h3local.py uses it for the local mode (ComfyUI Desktop), together with H3JOB_FETCH and H3JOB_COMFY_INPUT
LOCAL_TEST = os.environ.get("H3JOB_LOCAL_TEST") == "1"
JOB = Path(os.environ.get("H3JOB_DIR", "/content/h3job"))
OUT = JOB / "out"
WORK = JOB / "work"
STATUS = JOB / "status.json"
COMFY = Path(os.environ.get("H3JOB_COMFY", "/content/ComfyUI"))
MARK = Path("/content/.h3skill_setup_v1")
COMFY_URL = os.environ.get("H3JOB_COMFY_URL", "http://127.0.0.1:8188")
STATE = {"stage": "starting", "log": [], "jobs": {}, "done": False, "error": None, "started": time.time()}
TEMPLATE_DIRS = [JOB, Path(__file__).resolve().parent.parent / "workflows"]  # uploaded copies first

ASSETS = "dseditor/minimax-h3-studio-assets"
BASE_MODELS = [  # (repo, file in repo, models/ subfolder)
    ("OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM", "Minimax-h3_Singularity_x_10Eros-Max_PRISM_int8.safetensors", "diffusion_models"),
    ("Comfy-Org/MiniMax-H3", "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "text_encoders"),
    ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_int8_convrot.safetensors", "vae"),
    ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors", "vae"),
    ("Comfy-Org/MiniMax-H3", "loras/pika_h3_flex_ref2va_v1_bf16.safetensors", "loras"),  # video + TTS
] + [(ASSETS, f, str(Path(f).parent)) for f in [
    "loras/minimax_h3_turbo_v4_step600_ema_DasiwaREF2VAHybridV1_0_curveproj1025_compat_v001.safetensors",
    "loras/MysticXXX_MMH3-V2.safetensors",
    "loras/H3_Combat_V2.safetensors",          # motion LoRAs: only wired in when intensity is not "off"
    "loras/Motion_Repair.safetensors",
    "loras/H3_SPEED_slider.safetensors",
    "semantic_bridge/t8_compat/BUNNY_H3_ActionLogic_Bridge_V1_T8_Compat.safetensors",
    "latent_upscale_models/minimax_h3_latent_upscaler_3d_fp16_V2.safetensors",
]]
PICTURE_MODELS = [  # P-H3-Image (PrunaAI): a LoRA on the same H3 model + its own encoder and image VAE (~10 GB)
    ("PrunaAI/P-H3-Image-Comfy", "loras/p-h3-image_lora_bf16.safetensors", "loras"),
    ("PrunaAI/P-H3-Image-Comfy", "text_encoders/p-h3-image_text_encoder_int8_convrot.safetensors", "text_encoders"),
    ("PrunaAI/P-H3-Image-Comfy", "vae/p-h3-image_vae_int8_convrot.safetensors", "vae"),
]
NODES = {
    "minimax-h3-audio-t8": "https://github.com/T8mars/comfyui-minimax-h3-audio-T8",
    "comfyui-SelfLift": "https://github.com/facok/comfyui-SelfLift",
    "ComfyUI-H3-Motion-Context": "https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}


# ---------------------------------------------------------------- status
def save():
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATUS)


def log(msg, stage=None):
    if stage:
        STATE["stage"] = stage
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    STATE["log"] = (STATE["log"] + [line])[-60:]
    save()


def sh(args, **kw):
    subprocess.run(args, check=True, **kw)


# ---------------------------------------------------------------- ComfyUI client
def call(url, body=None, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, headers=headers or {"Content-Type": "application/json"},
                                 data=data if data is not None else (None if body is None else json.dumps(body).encode()))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} -> {e.code}: {e.read().decode('utf-8', 'replace')[:800]}")
    return json.loads(raw) if raw.strip() else {}


def queue(graph):
    problems = G.check_graph(graph)
    if problems:  # never post a graph with a dangling link or a preview/VHS node
        raise RuntimeError("graph check failed before posting: " + "; ".join(problems[:6]))
    return call(COMFY_URL + "/prompt", {"prompt": graph, "client_id": "h3skill"})["prompt_id"]


def wait_history(pid, timeout, what):
    """The /history entry once the prompt has finished; a failed run raises with ComfyUI's own message."""
    t0, last = time.time(), time.time()
    while time.time() - t0 < timeout:
        h = call(COMFY_URL + f"/history/{pid}").get(pid)
        if h and h.get("status", {}).get("completed") is not None:
            if h["status"].get("status_str") == "error":
                msgs = [m[1] for m in h["status"].get("messages", []) if m[0] == "execution_error"]
                m = msgs[0] if msgs else {}
                raise RuntimeError(f"ComfyUI: {m.get('node_type', '')} {m.get('exception_message', 'error')[:400]}".strip())
            return h
        if time.time() - last > 120:
            last = time.time()
            log(f"{what}: generating ({time.time() - t0:.0f} s)")
        time.sleep(2)
    raise RuntimeError(f"no result after {timeout} s")


def ran_graph(entry):
    """The graph ComfyUI really ran (history keeps [number, prompt_id, graph, extra, outputs])."""
    p = entry.get("prompt") or []
    return p[2] if len(p) > 2 and isinstance(p[2], dict) else {}


def comfy_file(info):
    if os.environ.get("H3JOB_FETCH") == "1":  # local ComfyUI Desktop: its output folder can be anywhere, so ask it
        q = urllib.parse.urlencode({"filename": info["filename"], "subfolder": info.get("subfolder", ""),
                                    "type": info.get("type", "output")})
        dst = JOB / "fetch" / info["filename"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{COMFY_URL}/view?{q}", timeout=300) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
        return dst
    return COMFY / "output" / info.get("subfolder", "") / info["filename"]


def comfy_input():
    return Path(os.environ.get("H3JOB_COMFY_INPUT") or COMFY / "input")


def saved_video(entry):
    """The file our SaveVideo ("sv") wrote. LoadVideo also reports its input as a UI output (type "input"),
    so only files of type "output" count."""
    outputs = entry.get("outputs", {})
    ordered = [outputs[k] for k in (G.N_SAVE,) if k in outputs] + list(outputs.values())
    for node_out in ordered:
        for key in ("images", "gifs", "videos", "video"):
            for f in node_out.get(key, []) or []:
                if (isinstance(f, dict) and f.get("type", "output") == "output"
                        and str(f.get("filename", "")).lower().endswith((".mp4", ".webm", ".mkv", ".mov"))):
                    return f
    return None


def saved_image(entry):
    for f in entry.get("outputs", {}).get(G.N_IMG_SAVE, {}).get("images", []) or []:
        if f.get("type", "output") == "output":
            return f
    return None


# ---------------------------------------------------------------- media (ffmpeg, PyAV / Pillow when present)
def ffmpeg_exe(tool="ffmpeg"):
    exe = shutil.which(tool)
    if exe:
        return exe
    if tool == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass
    raise RuntimeError(f"找不到 {tool}：延伸影片與參考影片需要 ffmpeg")


def image_size(path):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            return ImageOps.exif_transpose(im).size
    except ImportError:
        pass
    out = subprocess.run([ffmpeg_exe("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def probe_video(path):
    """(width, height, has_audio)."""
    try:
        import av
        with av.open(str(path)) as c:
            v = c.streams.video[0]
            return v.codec_context.width, v.codec_context.height, len(c.streams.audio) > 0
    except ImportError:
        pass
    out = subprocess.run([ffmpeg_exe("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    streams = json.loads(out.stdout).get("streams", [])
    v = next(s for s in streams if s.get("codec_type") == "video")
    return int(v["width"]), int(v["height"]), any(s.get("codec_type") == "audio" for s in streams)


def conform(src_path, dest, width, height, has_audio, tail=None):
    """Re-encode (the tail of) a video at 24 fps and the generation size, always with a stereo track."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    if tail:
        cmd += ["-sseof", f"-{tail}"]
    cmd += ["-i", str(src_path)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-vf", f"fps={G.FPS},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
            "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-shortest",
            "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def join_clips(first, second, dest):
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(first), "-i", str(second), "-filter_complex",
           "[0:a]aresample=48000,aformat=channel_layouts=stereo[a0];[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];"
           "[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[v][a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def last_frame(src, dest):
    """The final decodable frame of a video (each frame of the last 1.5 s overwrites the same file)."""
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-sseof", "-1.5", "-i", str(src), "-update", "1",
                    "-q:v", "2", str(dest)], check=True, capture_output=True)
    if not Path(dest).is_file():
        raise RuntimeError("讀不到延伸影片的最後一格")


# ---------------------------------------------------------------- inputs -> ComfyUI input folder
STAGED = {}  # source file -> media item (each file is copied into ComfyUI's input folder once)


def source_path(rel):
    """A manifest file field: a path relative to the job folder, or "@<id>" = an earlier job's output."""
    rel = str(rel)
    if rel.startswith("@"):
        st = STATE["jobs"].get(rel[1:], {})
        if st.get("status") != "done":
            raise RuntimeError(f"{rel}: that job has not produced a file (it must come earlier and succeed)")
        path = OUT / st["file"]
    else:
        path = JOB / "inputs" / rel
    if not path.is_file():
        raise RuntimeError(f"input not found: {rel}")
    return path


def stage(path, prefix="h3skill"):
    """Copy one file into ComfyUI's input folder and describe it (kind, size, audio track)."""
    path = Path(path)
    key = str(path.resolve())
    if key not in STAGED:
        ext = path.suffix.lower()
        kind = "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else None
        if kind is None:
            raise RuntimeError(f"不支援的檔案格式：{path.name}")
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
        name = f"{prefix}_{digest}{ext}"
        dst = comfy_input() / name
        if not dst.is_file():
            shutil.copy(str(path), str(dst))
        item = {"kind": kind, "name": name, "path": str(path), "width": None, "height": None, "has_audio": False}
        if kind == "image":
            item["width"], item["height"] = image_size(path)
        elif kind == "video":
            item["width"], item["height"], item["has_audio"] = probe_video(path)
        STAGED[key] = item
    return STAGED[key]


def media(rel):
    return stage(source_path(rel))


def extend_source(j):
    """The video to continue: an earlier job of this batch ("shot01" or "@shot01"; an extension's own output is
    already the joined, full-length video) or a video file the user gave."""
    ref = str(j["extend"])
    key = ref.lstrip("@")
    if key in STATE["jobs"] and key != j["id"]:
        st = STATE["jobs"][key]
        if st.get("status") != "done":
            raise RuntimeError(f"extend {ref}: that job has not produced a video (it must come earlier and succeed)")
        path = OUT / st["file"]
    else:
        path = source_path(ref)
    item = stage(path)
    if item["kind"] != "video":
        raise RuntimeError("只有影片可以延伸")
    return item


# ---------------------------------------------------------------- 4 jobs
def run_image(j):
    if not LOCAL_TEST and not all((COMFY / "models" / folder / Path(f).name).is_file() for _, f, folder in PICTURE_MODELS):
        raise RuntimeError("picture models are not installed on this runtime")
    plan = G.plan_picture(j, [media(r) for r in j.get("inputs", [])])
    graph = G.picture_graph(G.load_template(G.PICTURE_TEMPLATE, TEMPLATE_DIRS), plan)
    pid = queue(graph)
    entry = wait_history(pid, int(j.get("timeout", 900)), j["id"])
    miswired = G.check_picture(ran_graph(entry), plan)  # read back the graph ComfyUI really ran
    if miswired:
        raise RuntimeError("; ".join(miswired[:4]))
    f = saved_image(entry)
    if not f:
        raise RuntimeError("ComfyUI finished but saved no picture")
    return comfy_file(f), {"final_prompt": plan["final_prompt"], "seed": plan["seed"]}


def run_video(j):
    mode = G.VIDEO_MODES.get(j.get("mode", "ref"))
    kw = {}
    src = None
    if mode == "ref":
        kw["inputs"] = [media(r) for r in j.get("inputs", [])]
        if j.get("extend"):
            src = extend_source(j)
            WORK.mkdir(parents=True, exist_ok=True)
            frame = WORK / f"last_{Path(src['name']).stem}.jpg"
            last_frame(src["path"], frame)
            kw["extend"] = {"width": src["width"], "height": src["height"], "has_audio": src["has_audio"],
                            "last_frame": stage(frame, "h3skill_last")}
    elif mode == "frames":
        kw["first"] = media(j["first"]) if j.get("first") else None
        kw["last"] = media(j["last"]) if j.get("last") else None
    plan = G.plan_video(j, **kw)
    if plan["unused_inputs"]:
        log(f"{j['id']}: inputs not mentioned with @圖片N/@影片N/@音訊N are not used: {', '.join(plan['unused_inputs'])}")

    tail = None
    if src:  # the source's last seconds, at the new clip's size and 24 fps, are pinned into the head of the clip
        tail = f"h3skill_tail_{j['id']}.mp4"
        conform(src["path"], comfy_input() / tail, plan["width"], plan["height"], src["has_audio"],
                tail=G.EXTEND["tail_seconds"])
    graph = G.video_graph(G.load_template(G.VIDEO_TEMPLATE, TEMPLATE_DIRS), plan, tail)
    problems = G.check_video(graph, plan, tail)
    if problems:
        raise RuntimeError("graph check failed before posting: " + "; ".join(problems[:6]))
    pid = queue(graph)
    log(f"{j['id']}: {plan['task']} {plan['width']}x{plan['height']} {plan['seconds']:g}s queued")
    entry = wait_history(pid, int(j.get("timeout", 2400)), j["id"])
    problems = G.check_video(ran_graph(entry), plan, tail)  # read back the graph ComfyUI really ran
    if problems:
        raise RuntimeError("the graph ComfyUI ran is not the planned one: " + "; ".join(problems[:6]))
    f = saved_video(entry)
    if not f:
        raise RuntimeError("ComfyUI finished but saved no video")
    clip = comfy_file(f)
    info = {"final_prompt": plan["final_prompt"], "seed": plan["seed"]}
    if src:  # extension output = the source (conformed to the same size) joined with the new clip
        WORK.mkdir(parents=True, exist_ok=True)
        full_src, full = WORK / f"{j['id']}_src.mp4", WORK / f"{j['id']}_full.mp4"
        try:
            conform(src["path"], full_src, plan["width"], plan["height"], src["has_audio"])
            join_clips(full_src, clip, full)
            clip = full
        except (subprocess.CalledProcessError, RuntimeError) as e:
            err = e.stderr.decode("utf-8", "replace")[-400:] if getattr(e, "stderr", None) else str(e)
            info["warning"] = f"接回原片失敗，只交出新的那一段：{err}"
            log(f"{j['id']}: {info['warning']}")
        finally:
            (comfy_input() / tail).unlink(missing_ok=True)
            full_src.unlink(missing_ok=True)
    return clip, info


def run_tts(j):
    """32x32 H3: the picture is thrown away, only the audio latent is decoded (h3_tts32_api.json)."""
    wf = json.loads((Path(__file__).resolve().parent / "workflows" / "h3_tts32_api.json").read_text(encoding="utf-8"))
    if os.environ.get("H3JOB_TTS_LORA"):
        wf["63"]["inputs"]["lora_name"] = os.environ["H3JOB_TTS_LORA"]
    wf["7"]["inputs"]["prompt"] = j["prompt"]
    wf["56"]["inputs"]["value"] = int(j.get("seconds", 10))
    wf["54"]["inputs"]["seed"] = int(j["seed"]) if j.get("seed") is not None else int.from_bytes(os.urandom(6), "big")
    for k, rel in enumerate(j.get("voices", [])[:3]):  # <Audio N>: voice-timbre references, in order
        src = OUT / STATE["jobs"][rel[1:]]["file"] if rel.startswith("@") else JOB / "inputs" / rel
        name = f"h3skill_{j['id']}_{k}{Path(src).suffix}"
        shutil.copy(str(src), str(comfy_input() / name))
        wf[f"ra{k}"] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        wf["7"]["inputs"][f"ref_audios.ref_audio_{k}"] = [f"ra{k}", 0]
    pid = call(COMFY_URL + "/prompt", {"prompt": wf, "client_id": "h3skill"})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < 900:
        h = call(COMFY_URL + f"/history/{pid}").get(pid)
        if h and h.get("status", {}).get("completed") is not None:
            if h["status"].get("status_str") == "error":
                msgs = [m[1] for m in h["status"].get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(f"ComfyUI: {msgs[0].get('exception_message', '')[:400] if msgs else 'error'}")
            a = next(o["audio"][0] for o in h["outputs"].values() if o.get("audio"))
            flac = comfy_file(a)
            wav = flac.with_suffix(".wav")
            if not shutil.which("ffmpeg"):  # a desktop without ffmpeg: hand over the FLAC as it is
                return flac, {"seconds": j.get("seconds", 10)}
            sh(["ffmpeg", "-v", "error", "-y", "-i", str(flac), str(wav)])
            return wav, {"seconds": j.get("seconds", 10)}
        time.sleep(2)
    raise RuntimeError("TTS: no result after 900 s")


RUNNERS = {"image": run_image, "video": run_video, "tts": run_tts}
