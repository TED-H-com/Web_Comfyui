"""Reproduce original synthetic voice examples with an explicitly selected local model.

Requires the upstream VoxCPM2 Python environment. No model downloads, credential
lookup, user assets, or real-person reference recordings are used by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
    p.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--only", choices=[row[0] for row in CASES] + ["synthetic-clone"])
    p.add_argument("--clone", action="store_true", help="Also clone the generated warm-welcome voice")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not 1 <= args.threads <= 32:
        raise ValueError("threads must be between 1 and 32")
    for name in ("model.safetensors", "audiovae.pth", "config.json"):
        if not (args.model / name).is_file():
            raise ValueError("The explicit model directory is missing a required base-model file")
    receipt_path = args.output / "receipt.json"
    previous = None
    targets = ([args.only] if args.only else [row[0] for row in CASES])
    if args.clone and "synthetic-clone" not in targets:
        targets.append("synthetic-clone")
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
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
        path = args.output / (slug + ".wav")
        sf.write(path, wav, sr, subtype="PCM_16")
        sample = {"id": slug, "label": label, "file": path.name, "text": text,
                  "full_prompt": prompt, "seed": seed, "sample_rate": sr,
                  "duration_seconds": len(wav) / sr, "render_seconds": round(time.monotonic() - start, 3),
                  "sha256": sha256(path), "peak": float(np.max(np.abs(wav))),
                  "rms": float(np.sqrt(np.mean(wav ** 2))),
                  "review_status": "generated; automated review recorded separately; listening quality not assessed", **(extra or {})}
        receipt["samples"].append(sample)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(sample, ensure_ascii=False), flush=True)

    for slug, label, description, text, seed in CASES:
        if args.only and args.only != slug:
            continue
        save(slug, label, text, f"({description}){text}", seed,
             {"voice_description": description, "mode": "text-only voice design"})
    if args.clone or args.only == "synthetic-clone":
        reference = args.output / "warm-welcome.wav"
        original = next((s for s in receipt["samples"] if s["id"] == "warm-welcome"), None)
        if not original or not reference.is_file() or sha256(reference) != original["sha256"]:
            raise ValueError("Clone requires this script's generated warm-welcome and matching receipt")
        if original["text"] != CASES[0][3]:
            raise ValueError("Reference transcript does not match the original demo")
        save("synthetic-clone", "合成聲音延續", CLONE_TEXT, CLONE_TEXT, 622,
             {"mode": "synthetic-reference cloning", "reference_file": reference.name,
              "reference_sha256": original["sha256"], "reference_transcript": original["text"]},
             prompt_wav_path=str(reference), prompt_text=original["text"], reference_wav_path=str(reference))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
