#!/usr/bin/env python3
"""Guarded BreezyVoice TTS worker for Castle web.

This runs outside the web server process. It calls the warm BreezyVoice daemon for
each short chunk, checks the result with Whisper, re-rolls bad takes, then writes a
single wav plus a local sidecar report for the gallery detail view.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf


PROGRESS_PREFIX = "CASTLE_PROGRESS "
PUNCT_RE = re.compile(r"[\s，。、！？；：「」『』（）()\[\]\.,!?;:\-—…·~～\"']")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LAUGH_RE = re.compile(r"哈哈|呵呵|嘿嘿|嘻嘻|哼哼|ㄏㄏ|呼呼|嗯哼")
MOAN_CHARS = "啊嗯哦喔噢呃唔嗚呀哼哎唉噫"


def emit(stage: str, label: str, pct: int, **extra) -> None:
    payload = {"stage": stage, "label": label, "pct": max(0, min(100, int(pct))), **extra}
    print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def maybe_cancel(cancel_file: Path | None) -> None:
    if cancel_file and cancel_file.exists():
        raise KeyboardInterrupt("cancel requested")


def load_chunks(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    chunks = data.get("chunks") if isinstance(data, dict) else data
    if not isinstance(chunks, list) or not all(isinstance(item, str) and item.strip() for item in chunks):
        raise ValueError("chunks file must contain a non-empty string list")
    return [item.strip() for item in chunks]


def normalize_for_compare(text: str) -> str:
    text = PUNCT_RE.sub("", text or "")
    if CJK_RE.search(text):
        text = re.sub(r"[^\u3400-\u9fff]", "", text)
        try:
            import opencc

            text = opencc.OpenCC("t2s").convert(text)
        except Exception:
            pass
        return text
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def cer(ref: str, hyp: str) -> float:
    a, b = normalize_for_compare(ref), normalize_for_compare(hyp)
    if not a:
        return 0.0 if not b else 1.0
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def max_adjacent_repeat(text: str) -> int:
    text = normalize_for_compare(text)
    n = len(text)
    best = 0
    for size in range(4, n // 2 + 1):
        for index in range(n - 2 * size + 1):
            if text[index:index + size] == text[index + size:index + 2 * size]:
                best = max(best, size)
    return best


def moan_excess(ref: str, heard: str) -> int:
    ref_norm = normalize_for_compare(ref)
    heard_norm = normalize_for_compare(heard)
    return sum(max(0, heard_norm.count(ch) - ref_norm.count(ch)) for ch in set(MOAN_CHARS))


def held_vowel_secs(result: dict) -> float:
    total = 0.0
    for segment in (result or {}).get("segments", []):
        for word in segment.get("words", []) or []:
            token = re.sub(r"\s+", "", str(word.get("word") or ""))
            if token and all(ch in MOAN_CHARS for ch in token):
                total += max(0.0, float(word.get("end", 0.0)) - float(word.get("start", 0.0)))
    return total


def length_ratio(ref: str, heard: str) -> float:
    ref_norm = normalize_for_compare(ref)
    heard_norm = normalize_for_compare(heard)
    if not ref_norm:
        return 1.0
    return len(heard_norm) / len(ref_norm)


def synth_once(breezy_url: str, persona: str, text: str, out_path: Path) -> None:
    url = breezy_url.rstrip("/") + "/tts"
    body = json.dumps({"persona": persona, "text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=360) as response:
            ctype = response.headers.get("Content-Type", "")
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"BreezyVoice HTTP {error.code}: {detail[:240]}") from error
    if "audio" not in ctype:
        raise RuntimeError(f"BreezyVoice returned non-audio content: {raw[:120]!r}")
    out_path.write_bytes(raw)


def transcribe(asr, wav_path: Path, language: str) -> tuple[str, float, dict]:
    kwargs = {"condition_on_previous_text": False, "verbose": False, "word_timestamps": True}
    if language and language != "auto":
        kwargs["language"] = language
    result = asr.transcribe(str(wav_path), **kwargs)
    heard = "".join(str(segment.get("text") or "") for segment in result.get("segments", [])).strip()
    try:
        audio, sr = sf.read(str(wav_path))
        duration = len(audio) / float(sr)
    except Exception:
        duration = 0.0
    return heard, duration, result


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), int(sr)


def resample_linear(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or len(audio) == 0:
        return audio
    target_len = max(1, round(len(audio) * dst_sr / src_sr))
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def trim_loudnorm(path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".norm.wav")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                "-af",
                "silenceremove=start_periods=1:start_silence=0.08:start_threshold=-45dB,"
                "areverse,"
                "silenceremove=start_periods=1:start_silence=0.08:start_threshold=-45dB,"
                "areverse,loudnorm=I=-18:TP=-2:LRA=11",
                "-ar", "22050", "-ac", "1", str(tmp),
            ],
            check=True,
        )
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--workflow", default="castle-voice-speak-guarded-web")
    parser.add_argument("--breezy-url", default="http://127.0.0.1:8231")
    parser.add_argument("--whisper-model", default=os.environ.get("CASTLE_GUARD_WHISPER_MODEL", "large-v3"))
    parser.add_argument("--language", default="zh")
    parser.add_argument("--tries", type=int, default=int(os.environ.get("CASTLE_GUARD_TRIES", "4")))
    parser.add_argument("--cer", type=float, default=float(os.environ.get("CASTLE_GUARD_CER", "0.22")))
    parser.add_argument("--rep", type=int, default=int(os.environ.get("CASTLE_GUARD_REP", "4")))
    parser.add_argument("--held", type=float, default=float(os.environ.get("CASTLE_GUARD_HELD", "0.45")))
    parser.add_argument("--max-length-ratio", type=float, default=float(os.environ.get("CASTLE_GUARD_MAX_LEN_RATIO", "1.45")))
    parser.add_argument("--min-length-ratio", type=float, default=float(os.environ.get("CASTLE_GUARD_MIN_LEN_RATIO", "0.55")))
    parser.add_argument("--pause", type=float, default=0.14)
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chunks = load_chunks(args.chunks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.sidecar.parent.mkdir(parents=True, exist_ok=True)
    workdir = args.output.parent / f".{args.output.stem}.guard"
    workdir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        emit("dry_run", f"{len(chunks)} chunks ready", 100, total=len(chunks))
        return 0

    import torch
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    emit("load_asr", f"Loading Whisper {args.whisper_model}", 3, total=len(chunks), device=device)
    asr = whisper.load_model(args.whisper_model, device=device)

    pieces: list[np.ndarray] = []
    sample_rate: int | None = None
    report_chunks = []
    rerolls = 0
    total = len(chunks)

    for index, chunk in enumerate(chunks):
        maybe_cancel(args.cancel_file)
        best = None
        attempts = []
        for attempt in range(max(1, args.tries)):
            maybe_cancel(args.cancel_file)
            wav_path = workdir / f"chunk_{index:03d}_try_{attempt:02d}.wav"
            base_pct = 5 + round((index / total) * 88)
            emit(
                "synth",
                f"chunk {index + 1}/{total} try {attempt + 1}",
                base_pct,
                current=index + 1,
                total=total,
                attempt=attempt + 1,
            )
            synth_once(args.breezy_url, args.persona, chunk, wav_path)
            heard, duration, asr_result = transcribe(asr, wav_path, args.language)
            c = cer(chunk, heard)
            rep = max_adjacent_repeat(heard)
            held = held_vowel_secs(asr_result)
            moan = moan_excess(chunk, heard)
            ratio = length_ratio(chunk, heard)
            laughter = bool(LAUGH_RE.search(heard) and not LAUGH_RE.search(chunk))
            bad = (
                c > args.cer
                or rep >= args.rep
                or held >= args.held
                or moan > 0
                or ratio > args.max_length_ratio
                or ratio < args.min_length_ratio
                or laughter
            )
            score = c + (0.45 * rep) + (1.2 * held) + (2.0 * moan) + (1.2 if laughter else 0.0)
            if ratio > args.max_length_ratio:
                score += ratio - args.max_length_ratio
            if ratio < args.min_length_ratio:
                score += args.min_length_ratio - ratio
            attempt_report = {
                "attempt": attempt + 1,
                "cer": round(c, 4),
                "repeat": rep,
                "held": round(held, 3),
                "moan": moan,
                "lengthRatio": round(ratio, 3),
                "duration": round(duration, 2),
                "accepted": not bad,
                "heard": heard[:240],
            }
            attempts.append(attempt_report)
            emit(
                "gate",
                f"chunk {index + 1}/{total} CER {c:.2f} rep {rep}",
                5 + round(((index + 0.75) / total) * 88),
                current=index + 1,
                total=total,
                accepted=not bad,
                cer=round(c, 3),
                repeat=rep,
            )
            if best is None or score < best["score"]:
                best = {"score": score, "path": wav_path, "attempt": attempt_report, "heard": heard}
            if not bad:
                break
            rerolls += 1

        if best is None:
            raise RuntimeError("no TTS attempt was produced")
        audio, sr = read_mono(best["path"])
        if sample_rate is None:
            sample_rate = sr
        elif sr != sample_rate:
            audio = resample_linear(audio, sr, sample_rate)
        pieces.append(audio)
        report_chunks.append(
            {
                "index": index,
                "text": chunk,
                "selectedAttempt": best["attempt"]["attempt"],
                "accepted": bool(best["attempt"]["accepted"]),
                "heard": best["heard"][:400],
                "attempts": attempts,
            }
        )

    if sample_rate is None or not pieces:
        raise RuntimeError("no audio pieces were produced")
    pause = np.zeros(max(0, int(args.pause * sample_rate)), dtype=np.float32)
    joined = pieces[0]
    for piece in pieces[1:]:
        joined = np.concatenate([joined, pause, piece])
    sf.write(str(args.output), joined, sample_rate)
    trim_loudnorm(args.output)

    sidecar = {
        "promptId": args.job_id,
        "workflow": args.workflow,
        "feature": "打字即說（高品質）",
        "created": int(time.time()),
        "status": "completed",
        "settings": {
            "persona": args.persona,
            "qualityGate": True,
            "asrModel": args.whisper_model,
            "language": args.language,
            "tries": args.tries,
            "cerThreshold": args.cer,
            "pause": args.pause,
        },
        "summary": {
            "chunks": len(chunks),
            "rerolls": rerolls,
            "duration": round(len(joined) / sample_rate, 2),
        },
        "chunks": report_chunks,
        "outputs": [
            {
                "kind": "audio",
                "filename": args.output.name,
                "subfolder": args.output.parent.name if args.output.parent.name != "output" else "",
                "type": "output",
            }
        ],
    }
    args.sidecar.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("done", "completed", 100, total=total, rerolls=rerolls, output=args.output.name)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        emit("cancelled", "cancelled", 100)
        raise SystemExit(130)
