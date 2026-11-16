"""Reproduce original synthetic voice examples with an explicitly selected local model.

Requires the upstream VoxCPM2 Python environment. No model downloads, credential
lookup, user assets, or real-person reference recordings are used by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

MODEL_ID = "openbmb/VoxCPM2"
MODEL_REVISION = "32279effe8c19989596f05d353d1447f51d9e915"
CODE_REVISION = "30118a33522ef2d8698fca9bebac7224af242395"
CASES = [('warm-welcome',
  '溫暖迎賓',
  'A warm adult woman in her late twenties, speaking natural Taiwan Mandarin with a friendly smile and '
  'clear soft mid-range voice.',
  '嗨，歡迎回來！今天也辛苦了。準備好一起開始新的小冒險了嗎？',
  241),
 ('game-cheer',
  '元氣遊戲',
  'An energetic adult female game host in her twenties, bright and lively Taiwan Mandarin, cheerful '
  'crisp articulation and playful confidence.',
  '各位玩家，準備好了嗎？倒數三、二、一！一起出發，把今天的精彩任務全部完成吧！',
  763),
 ('gentle-story',
  '溫柔故事',
  'An adult woman in her thirties with a soft airy low-volume voice, gentle Taiwan Mandarin '
  'storytelling, unhurried and soothing.',
  '溫暖的陽光輕輕落在窗邊。她捧起一杯熱茶，翻開書頁，讓忙碌的心慢慢安靜下來。',
  182),
 ('mature-announcement',
  '成熟播報',
  'A mature adult female announcer in her forties, rich low-pitched voice, composed professional Taiwan '
  'Mandarin, measured pacing and precise diction.',
  '各位來賓您好，今天的展覽將在十分鐘後開始。請依照現場指示入座，謝謝您的配合。',
  943),
 ('slide-one',
  '簡報第一頁',
  'An adult female presenter in her thirties, confident clear mid-low voice, natural Taiwan Mandarin, '
  'conversational professional presentation.',
  '第一頁，我們先看整體流程。從文字構想開始，選擇圖片與聲音，再把素材整理成一段完整的作品。',
  552),
 ('slide-two',
  '簡報第二頁',
  'An adult female presenter in her thirties, confident clear mid-low voice, natural Taiwan Mandarin, '
  'conversational professional presentation.',
  '第二頁，重點是保留人物原貌。先辨識主體，再替換背景，最後檢查頭髮與臉部邊緣是否自然。',
  553),
 ('slide-three',
  '簡報第三頁',
  'An adult female presenter in her thirties, confident clear mid-low voice, natural Taiwan Mandarin, '
  'conversational professional presentation.',
  '第三頁，完成前請先播放一次。看看畫面、旁白與字幕是否互相配合，再分享你的創作。',
  552),
 ('english-welcome',
  '英文迎賓',
  'An adult woman in her thirties, warm expressive American English, clear mid-range voice and relaxed '
  'friendly delivery.',
  'Hello, and welcome! Let us turn a simple idea into a story you can see and hear. Your next creation '
  'starts here.',
  427)]
CLONE_TEXT = "大家好，我是晴晴。今天也要帶著笑容，一起為喜歡的隊伍加油！"
DEMO2_CASES = [('qingqing-hello',
  '晴晴自我介紹',
  'A cheerful adult woman in her mid-twenties, bright warm Taiwan Mandarin, lively and friendly, clear '
  'mid-high voice.',
  '嗨，我是晴晴！今天陽光超好，我們一起去拍一組新的照片吧！',
  311),
 ('zhiyuan-hello',
  '志遠自我介紹',
  'A calm adult man in his early thirties, warm low-mid baritone, relaxed natural Taiwan Mandarin, steady '
  'unhurried pacing.',
  '我是志遠，平常在山上拍照。好的畫面，要耐心等光線剛剛好的那一刻。',
  412),
 ('xingyu-hello',
  '星羽登場',
  'An energetic adult woman in her early twenties, sparkling playful Taiwan Mandarin like an animation '
  'voice actress, bright crisp high voice.',
  '大家好，我是星羽！準備好了嗎？跟我一起飛到雲朵上面去冒險吧！',
  1523),
 ('chenyi-hello',
  '辰翼登場',
  'A confident adult man in his late twenties, deep resonant cool voice like an animation hero, clear '
  'Taiwan Mandarin, slow dramatic delivery.',
  '我是辰翼。這座城市的夜晚，由我來守護。',
  634),
 ('qingqing-english',
  '晴晴英文',
  'A cheerful adult woman in her mid-twenties, bright friendly American English with a light playful tone.',
  "Hi, I'm Qingqing! The sun is out, so let's go shoot some new photos together!",
  745),
 ('zhiyuan-english',
  '志遠英文',
  'A calm adult man in his early thirties, warm baritone, relaxed natural American English, steady pacing.',
  "Hi, I'm Zhiyuan. I photograph mountains, and the best shots come to those who wait for the light.",
  856),
 ('male-announcement',
  '男聲廣播',
  'A mature adult male announcer in his forties, rich low voice, composed clear Taiwan Mandarin, measured '
  'pacing and precise diction.',
  '各位旅客您好，本班列車即將抵達山城車站，請帶好隨身物品，準備下車。',
  967),
 ('mountain-slide-one',
  '山景簡報第一頁',
  'A mature adult male narrator in his forties, rich low voice, composed documentary-style Taiwan '
  'Mandarin, measured pacing.',
  '第一頁：清晨的山谷還在沉睡，雲海靜靜地鋪在群山之間。',
  1981)]
DEMO2_CLONES = [('mountain-slide-two',
  '山景簡報第二頁',
  'mountain-slide-one',
  '第二頁：第一道陽光越過山頭，把整片雲海染成金色。',
  982),
 ('mountain-slide-three',
  '山景簡報第三頁',
  'mountain-slide-one',
  '第三頁：山谷慢慢醒來，山路上的旅人準備出發，新的一天開始了。',
  3983),
 ('qingqing-clone',
  '晴晴聲音克隆',
  'qingqing-hello',
  '這段話是用晴晴的聲音樣本克隆出來的：換上全新的台詞，聽起來還是同一個人。',
  979),
 ('zhiyuan-clone',
  '志遠聲音克隆',
  'zhiyuan-hello',
  '這句話是用志遠的聲音樣本克隆出來的：同一個聲音，說出全新的內容。',
  978),
 ('xingyu-clone',
  '星羽聲音克隆',
  'xingyu-hello',
  '這段話是用星羽的聲音樣本克隆的：同樣的聲音，喊出全新的冒險口號！',
  977),
 ('chenyi-clone',
  '辰翼聲音克隆',
  'chenyi-hello',
  '這句話也是用辰翼的聲音樣本克隆出來的：今晚的巡邏，換我用新台詞報告。',
  976),
 ('qingqing-english-clone',
  '晴晴英文克隆',
  'qingqing-english',
  "This line was cloned from Qingqing's English voice sample: same voice, brand-new words.",
  975),
 ('zhiyuan-english-clone',
  '志遠英文克隆',
  'zhiyuan-english',
  "This line was cloned from Zhiyuan's English voice sample: the same calm voice, saying something new.",
  974)]
DEMO2_POSTPROCESS = {
    "frame_ms": 10,
    "threshold_db_below_loudest_frame": 40.0,
    "floor_rms": 0.0001,
    "edge_keep_ms": 120,
    "max_pause_ms": 700,
    "crossfade_ms": 10,
    "edge_fade_ms": 5,
}


def frame_rms(samples, frame):
    if frame <= 0:
        raise ValueError("frame must be positive")
    return [
        math.sqrt(sum(float(x) * float(x) for x in samples[i:i + frame]) / len(samples[i:i + frame]))
        for i in range(0, len(samples), frame)
    ]


def plan_keep_segments(rms, n_samples, sample_rate, params=DEMO2_POSTPROCESS):
    if not rms or n_samples <= 0:
        raise ValueError("silent waveform")
    frame = round(sample_rate * params["frame_ms"] / 1000)
    threshold = max(max(rms) * 10 ** (-params["threshold_db_below_loudest_frame"] / 20), params["floor_rms"])
    voiced = [idx for idx, val in enumerate(rms) if val >= threshold]
    if not voiced:
        raise ValueError("silent waveform")
    first_voiced = voiced[0]
    last_voiced = voiced[-1]
    edge = round(sample_rate * params["edge_keep_ms"] / 1000)
    start = max(0, first_voiced * frame - edge)
    end = min(n_samples, (last_voiced + 1) * frame + edge)
    pause = round(sample_rate * params["max_pause_ms"] / 1000)
    segments = []
    cur = start
    for v_prev, v_next in zip(voiced, voiced[1:]):
        a = v_prev + 1
        b = v_next
        if (b - a) * frame > pause:
            cut_start = a * frame + pause // 2
            cut_end = b * frame - (pause - pause // 2)
            segments.append((cur, cut_start))
            cur = cut_end
    segments.append((cur, end))
    return segments


def join_segments(samples, segments, crossfade, edge_fade):
    if not segments or crossfade < 0 or edge_fade < 0:
        raise ValueError("invalid segments or fade parameters")
    if any(s < 0 or e > len(samples) or e - s < crossfade for s, e in segments):
        raise ValueError("segment shorter than crossfade")
    parts = [[float(x) for x in samples[s:e]] for s, e in segments]
    out = list(parts[0])
    for right in parts[1:]:
        if crossfade > 0:
            base = len(out) - crossfade
            for i in range(crossfade):
                w_i = (i + 1) / (crossfade + 1)
                out[base + i] = (1.0 - w_i) * out[base + i] + w_i * right[i]
            out.extend(right[crossfade:])
        else:
            out.extend(right)
    fade_len = min(edge_fade, len(out))
    for i in range(fade_len):
        w_i = (i + 1) / (edge_fade + 1)
        out[i] *= w_i
        out[len(out) - 1 - i] *= w_i
    return out


def postprocess_demo2(samples, sample_rate):
    params = DEMO2_POSTPROCESS
    frame = round(sample_rate * params["frame_ms"] / 1000)
    crossfade = round(sample_rate * params["crossfade_ms"] / 1000)
    edge_fade = round(sample_rate * params["edge_fade_ms"] / 1000)
    n_samples = len(samples)
    rms = frame_rms(samples, frame)
    segments = plan_keep_segments(rms, n_samples, sample_rate, params)
    processed = join_segments(samples, segments, crossfade, edge_fade)
    record = {
        **params,
        "name": "demo2-silence-trim-v1",
        "raw_duration_seconds": round(n_samples / sample_rate, 3),
        "removed_seconds": round((n_samples - len(processed)) / sample_rate, 3),
        "internal_cuts": len(segments) - 1,
    }
    return processed, record


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="Explicit local base VoxCPM2 directory")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--set", choices=("original", "demo2"), default="original")
    p.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--only", choices=[row[0] for row in CASES] + ["synthetic-clone"]
                   + [row[0] for row in DEMO2_CASES] + [row[0] for row in DEMO2_CLONES])
    p.add_argument("--clone", action="store_true", help="Also clone the generated reference voice")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not 1 <= args.threads <= 32:
        raise ValueError("threads must be between 1 and 32")
    for name in ("model.safetensors", "audiovae.pth", "config.json"):
        if not (args.model / name).is_file():
            raise ValueError("The explicit model directory is missing a required base-model file")
    if args.set == "original":
        cases = CASES
        clones = [("synthetic-clone", "合成聲音延續", "warm-welcome", CLONE_TEXT, 622)]
    else:
        cases = DEMO2_CASES
        clones = DEMO2_CLONES
    valid_only = {row[0] for row in cases} | {row[0] for row in clones}
    if args.only and args.only not in valid_only:
        raise ValueError("--only is not part of the selected set")
    design_rows = [row for row in cases if not args.only or args.only == row[0]]
    if args.set == "original":
        clone_rows = list(clones) if (args.clone or args.only == "synthetic-clone") else []
    elif not args.only:
        clone_rows = list(clones) if args.clone else []
    elif args.only in {row[0] for row in clones}:
        clone_rows = [row for row in clones if row[0] == args.only]
    elif args.clone:
        clone_rows = [row for row in clones if row[2] == args.only]
    else:
        clone_rows = []
    targets = [row[0] for row in design_rows] + [row[0] for row in clone_rows]
    receipt_path = args.output / "receipt.json"
    previous = None
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        if previous.get("set", "original") != args.set:
            raise ValueError("Existing output receipt belongs to another set")
        if previous.get("engine") != "VoxCPM2" or not args.only:
            raise ValueError("Existing output receipt: use an explicit --only rerun for an owned sample")
        for sample in previous.get("samples", []):
            if sample["id"] in targets:
                existing = args.output / (sample["id"] + ".wav")
                if sample.get("file") != existing.name or not existing.is_file() or sha256(existing) != sample["sha256"]:
                    raise ValueError("Existing output does not match its owned receipt")
    owned = {sample["id"] for sample in (previous or {}).get("samples", [])}
    for slug in targets:
        if (args.output / (slug + ".wav")).exists() and slug not in owned:
            raise ValueError("Refusing to overwrite an unrelated audio file")
    case_texts = {row[0]: row[3] for row in cases}

    def resolve_reference(samples, reference_slug):
        reference = args.output / f"{reference_slug}.wav"
        original = next((s for s in samples if s.get("id") == reference_slug), None)
        if (not original or original.get("file") != reference.name
                or not reference.is_file() or sha256(reference) != original.get("sha256")):
            raise ValueError(f"Clone requires this script's generated {reference_slug} and matching receipt")
        if original.get("text") != case_texts[reference_slug]:
            if args.set == "original":
                raise ValueError("Reference transcript does not match the original demo")
            raise ValueError(f"Clone requires this script's generated {reference_slug} and matching receipt")
        return reference, original

    for _, _, reference_slug, _, _ in clone_rows:
        if reference_slug not in targets:
            resolve_reference((previous or {}).get("samples", []), reference_slug)
    import numpy as np
    import soundfile as sf
    import torch
    from voxcpm import VoxCPM

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(2)
    args.output.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output / "receipt.json"
    settings = dict(cfg_value=2.0, inference_timesteps=10, normalize=False,
                    denoise=False, retry_badcase=False, max_len=768)
    receipt = {
        "engine": "VoxCPM2", "mode": "text-only voice design",
        **({"set": "demo2"} if args.set == "demo2" else {}),
        "model_id": MODEL_ID, "reviewed_model_revision": MODEL_REVISION,
        "reviewed_code_revision": CODE_REVISION,
        "model_sha256": {name: sha256(args.model / name) for name in
                         ("model.safetensors", "audiovae.pth", "config.json")},
        "license": "Apache-2.0", "license_source": "https://huggingface.co/openbmb/VoxCPM2",
        "input_rights": "Original scripts and generic adult voice descriptions; no private or real-person reference audio, presets, or LoRA.",
        "settings": {**settings, "device": args.device, "cpu_threads": args.threads, "optimize": False},
        "samples": [],
    }
    if args.only and receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        if previous.get("model_sha256") != receipt["model_sha256"]:
            raise ValueError("Existing receipt uses different model weights")
        receipt["samples"] = [s for s in previous["samples"] if s["id"] not in targets]
    model = VoxCPM(str(args.model), enable_denoiser=False, zipenhancer_model_path=None,
                   optimize=False, device=args.device)

    def save(slug, label, text, prompt, seed, extra=None, **reference):
        start = time.monotonic()
        wav = np.asarray(model.generate(text=prompt, seed=seed, **settings, **reference), dtype=np.float32).reshape(-1)
        if not len(wav) or not np.all(np.isfinite(wav)):
            raise ValueError("Invalid generated waveform")
        sr = model.tts_model.sample_rate
        postprocess = None
        if args.set == "demo2":
            processed, postprocess = postprocess_demo2(wav.tolist(), sr)
            wav = np.asarray(processed, dtype=np.float32)
        path = args.output / (slug + ".wav")
        sf.write(path, wav, sr, subtype="PCM_16")
        sample = {"id": slug, "label": label, "file": path.name, "text": text,
                  "full_prompt": prompt, "seed": seed, "sample_rate": sr,
                  "duration_seconds": len(wav) / sr, "render_seconds": round(time.monotonic() - start, 3),
                  "sha256": sha256(path), "peak": float(np.max(np.abs(wav))),
                  "rms": float(np.sqrt(np.mean(wav ** 2))),
                  "review_status": "generated; automated review recorded separately; listening quality not assessed",
                  **(extra or {}), **({"postprocess": postprocess} if postprocess is not None else {})}
        receipt["samples"].append(sample)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(sample, ensure_ascii=False), flush=True)

    for slug, label, description, text, seed in design_rows:
        save(slug, label, text, f"({description}){text}", seed,
             {"voice_description": description, "mode": "text-only voice design"})
    for slug, label, reference_slug, text, seed in clone_rows:
        reference, original = resolve_reference(receipt["samples"], reference_slug)
        save(slug, label, text, text, seed,
             {"mode": "synthetic-reference cloning", "reference_file": reference.name,
              "reference_sha256": original["sha256"], "reference_transcript": original["text"]},
             prompt_wav_path=str(reference), prompt_text=original["text"], reference_wav_path=str(reference))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
