#!/usr/bin/env python3
"""VoxCPM2 worker for Castle web.

Runs as a background GPU-queued process, not inside the web server. Supports the
main upstream modes Castle exposes: voice design, reference cloning, ultimate
cloning, and local LoRA profiles such as Custom English.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


PROGRESS_PREFIX = "CASTLE_PROGRESS "
BATCH_CHUNK_MAX_WORDS = 30
BATCH_CHUNK_MIN_WORDS = 8
BATCH_INTERNAL_START_TRIM_SECONDS = 0.25
BATCH_INTERNAL_FADE_SECONDS = 0.02
BATCH_INTERNAL_LEAD_SECONDS = 0.06
BATCH_INTERNAL_RELEASE_SECONDS = 0.24
BATCH_CONTINUATION_GAP_SECONDS = 0.45
BATCH_SENTENCE_GAP_SECONDS = 0.90
BATCH_FINAL_TAIL_SECONDS = 0.60
BATCH_CONTENT_AUDIT_TAIL_SECONDS = 0.35
BATCH_VAD_FRAME_SECONDS = 0.01
BATCH_VAD_ABSOLUTE_RMS = 0.0015
BATCH_VAD_RELATIVE_RMS = 0.025
BATCH_PERIOD_SENTINEL = "\ue000"


class ContentGateError(RuntimeError):
    """Raised when content cannot be verified completely and safely."""


class ContentGateClient:
    """Persistent JSON-lines client for Whisper in Castle's voice-lab venv."""

    def __init__(self, args):
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.request_id = 0

    def start(self) -> None:
        if self.process is not None:
            return
        script = Path(self.args.content_script).resolve()
        if not script.is_file():
            raise ContentGateError(f"content gate script not found: {script}")
        command = [
            self.args.content_python,
            str(script),
            "--serve",
            "--model",
            self.args.content_model,
            "--model-cache",
            str(Path(self.args.content_model_cache).expanduser()),
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        ready = self._read_response(self.args.content_startup_timeout)
        if not ready.get("ready"):
            self.close()
            raise ContentGateError(f"content gate failed to start: {ready}")

    def _read_response(self, timeout: float) -> dict:
        if self.process is None or self.process.stdout is None:
            raise ContentGateError("content gate is not running")
        selector = selectors.DefaultSelector()
        try:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(max(0.1, float(timeout))):
                raise ContentGateError(f"content gate timed out after {timeout:.0f}s")
            line = self.process.stdout.readline()
        finally:
            selector.close()
        if not line:
            code = self.process.poll()
            raise ContentGateError(f"content gate stopped unexpectedly (exit {code})")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContentGateError(f"content gate returned invalid JSON: {line[:300]!r}") from error
        if response.get("error"):
            raise ContentGateError(str(response["error"]))
        return response

    def check(self, audio_path: Path, expected: str) -> dict:
        self.start()
        if self.process is None or self.process.stdin is None:
            raise ContentGateError("content gate is not running")
        self.request_id += 1
        payload = {
            "id": self.request_id,
            "audioPath": str(audio_path.resolve()),
            "expected": expected,
        }
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ContentGateError("content gate stopped while accepting a request") from error
        response = self._read_response(self.args.content_timeout)
        if response.get("id") != self.request_id:
            raise ContentGateError("content gate response id mismatch")
        return response

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin is not None and process.poll() is None:
                process.stdin.write('{"op":"shutdown"}\n')
                process.stdin.flush()
            process.wait(timeout=5)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()


def emit(stage: str, label: str, pct: int, **extra) -> None:
    payload = {"stage": stage, "label": label, "pct": max(0, min(100, int(pct))), **extra}
    print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def maybe_cancel(cancel_file: Path | None) -> None:
    if cancel_file and cancel_file.exists():
        raise KeyboardInterrupt("cancel requested")


def clean_instruction(*parts: str) -> str:
    text = ", ".join(part.strip() for part in parts if part and part.strip())
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text


def tts_pronunciation_text(text: str) -> str:
    """Apply presenter-only spoken forms without changing the source script."""
    text = re.sub(r"\bPICs\b", "P I C's", text)
    text = re.sub(r"\bPIC-", "P I C ", text)
    text = re.sub(r"\bPIC\b", "P I C", text)
    # The contraction is semantically identical, but spelling it out keeps
    # VoxCPM from dropping the quiet negation in phrases such as "can't
    # download". The approved contraction remains unchanged for the gate.
    text = re.sub(r"\bcan['’]t\b", "cannot", text, flags=re.IGNORECASE)
    # Quotation marks around these three UI menu labels are presentational,
    # not spoken content. Remove only complete quoted instances of the exact
    # labels; unrelated and unmatched quotation marks remain untouched.
    text = re.sub(
        r"['\"‘“]\s*(New|Upload|More)\s*['\"’”]",
        r"\1",
        text,
    )
    # VoxCPM may skip this exact parenthetical UI label as an aside. Keep both
    # approved words and remove only their synthesis-time delimiters.
    text = re.sub(r"\(\s*(More options)\s*\)", r"\1", text)
    # VoxCPM occasionally drops quiet terminal inflections even though the
    # source word is correct. These spelling-only hints make the final sound
    # explicit; the strict content gate still audits against the untouched
    # approved chunk, so a missing S remains a hard failure.
    inflection_hints = {
        "Posts": "Post's",
        "Teams": "Teems",
        "Shared": "Shaird",
        "Combined": "Combine'd",
        "settings": "setting's",
        "divisions": "division's",
        "workspaces": "work spaces",
    }
    for source, target in inflection_hints.items():
        text = re.sub(rf"\b{source}\b", target, text, flags=re.IGNORECASE)
    # VoxCPM may treat square-bracket stage directions as metadata and skip
    # them. Keep every approved word while removing only the synthesis-time
    # delimiters so the strict gate can still require the full phrase.
    text = re.sub(r"\[\s*([^\[\]]+?)\s*\]", r"\1", text)
    # VoxCPM can treat an acronym in parentheses as an aside and skip it,
    # especially at the end of a sentence. Preserve the words but remove only
    # those synthesis-time parentheses so "Team P I C" is spoken aloud.
    text = re.sub(
        r"\(\s*((?:Team\s+)?P\s+I\s+C)\s*\)",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    # At a sentence boundary, phonetic letter names keep VoxCPM from treating
    # the acronym as one word or ending before the final C. This remains a
    # synthesis-only hint; the approved/displayed script is never changed.
    return re.sub(
        r"\bP\s+I\s+C(?=\s*[.!?])",
        "Pee Eye See",
        text,
        flags=re.IGNORECASE,
    )


def final_text(text: str, control: str, mode: str, has_prompt_text: bool = False) -> str:
    text = tts_pronunciation_text(re.sub(r"\s+", " ", (text or "").strip()))
    control = clean_instruction(control)
    if not text:
        raise ValueError("text is required")
    # Upstream VoxCPM uses "(control instruction)target text". Do not use it with
    # prompt-text cloning, where the reference transcript controls continuation.
    if control and mode != "ultimate" and not has_prompt_text:
        return f"({control}){text}"
    return text


def split_batch_text(text: str, max_words: int = BATCH_CHUNK_MAX_WORDS) -> list[str]:
    """Split a batch item into reliable sentence-sized VoxCPM calls.

    PowerPoint notes sometimes arrive with a period immediately followed by the
    next line (``Teams.Next sentence``), so the boundary whitespace is optional.
    Complete sentences stay intact up to the cap. Longer sentences prefer a
    clause boundary and never leave a tiny orphan fragment for the next call.
    """
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return []
    protected = re.sub(
        r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|No|vs)\.",
        lambda match: match.group(0)[:-1] + BATCH_PERIOD_SENTINEL,
        normalized,
        flags=re.IGNORECASE,
    )
    protected = re.sub(
        r"\b(?:[A-Za-z]\.){2,}",
        lambda match: match.group(0)[:-1].replace(".", BATCH_PERIOD_SENTINEL) + ".",
        protected,
    )
    sentence_groups = re.split(
        r"(?<=[.!?])\s*(?=(?:[\"'“‘(\[])?[A-Z0-9])",
        protected,
    )
    def close_chunk(value: str, *, sentence_end: bool) -> str:
        value = value.strip()
        if not value or re.search(r'[.!?,;:—]["\'”’)]*$', value):
            return value
        return value + ("." if sentence_end else ",")

    chunks: list[str] = []
    minimum_words = max(1, min(BATCH_CHUNK_MIN_WORDS, max_words // 2))
    for group in sentence_groups:
        words = group.replace(BATCH_PERIOD_SENTINEL, ".").strip().split()
        while len(words) > max_words:
            # Reserve a useful remainder instead of producing fragments such as
            # "be sent separately." as their own terminal TTS request.
            largest_cut = min(max_words, len(words) - minimum_words)
            cut = largest_cut
            for index in range(largest_cut, minimum_words - 1, -1):
                token = words[index - 1].rstrip('\"\'”’)]}')
                if token.endswith((",", ";", ":", "—")):
                    cut = index
                    break
            chunks.append(close_chunk(" ".join(words[:cut]), sentence_end=False))
            words = words[cut:]
        if words:
            chunks.append(close_chunk(" ".join(words), sentence_end=True))
    return [chunk for chunk in chunks if chunk]


def _rms_dbfs(value: float) -> float:
    return round(20.0 * np.log10(max(float(value), 1e-12)), 2)


def _speech_bounds(audio: np.ndarray, sample_rate: int) -> tuple[int, int, float]:
    """Return conservative frame-level speech bounds without cutting consonants."""
    if not len(audio):
        return 0, 0, BATCH_VAD_ABSOLUTE_RMS
    frame = max(1, round(sample_rate * BATCH_VAD_FRAME_SECONDS))
    rms = np.asarray([
        np.sqrt(np.mean(np.square(audio[start:start + frame], dtype=np.float64)))
        for start in range(0, len(audio), frame)
    ])
    peak_rms = float(np.max(rms)) if len(rms) else 0.0
    threshold = max(BATCH_VAD_ABSOLUTE_RMS, peak_rms * BATCH_VAD_RELATIVE_RMS)
    active = np.flatnonzero(rms >= threshold)
    if not len(active):
        return 0, len(audio), threshold
    start = int(active[0]) * frame
    end = min(len(audio), (int(active[-1]) + 1) * frame)
    return start, end, threshold


def normalize_generated_chunk(audio: np.ndarray, sample_rate: int, index: int) -> tuple[np.ndarray, dict]:
    """Keep one measured lead/release around speech and remove variable silence."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
    if index:
        trim_samples = round(sample_rate * BATCH_INTERNAL_START_TRIM_SECONDS)
        if len(audio) <= trim_samples:
            raise ValueError(f"chunk {index + 1:02d} is shorter than the startup trim")
        audio = audio[trim_samples:].copy()
    speech_start, speech_end, threshold = _speech_bounds(audio, sample_rate)
    lead_samples = 0 if not index else round(sample_rate * BATCH_INTERNAL_LEAD_SECONDS)
    release_samples = round(sample_rate * BATCH_INTERNAL_RELEASE_SECONDS)
    crop_start = 0 if not index else max(0, speech_start - lead_samples)
    crop_end = min(len(audio), speech_end + release_samples)
    normalized = audio[crop_start:crop_end].copy()
    if index:
        fade_samples = min(len(normalized), round(sample_rate * BATCH_INTERNAL_FADE_SECONDS))
        if fade_samples:
            curve = np.sin(np.linspace(0.0, np.pi / 2.0, fade_samples, dtype=np.float32))
            normalized[:fade_samples] *= curve
    actual_lead = max(0.0, (speech_start - crop_start) / sample_rate)
    actual_release = max(0.0, (crop_end - speech_end) / sample_rate)
    tail_window = audio[-max(1, round(sample_rate * 0.08)):]
    tail_rms = float(np.sqrt(np.mean(np.square(tail_window, dtype=np.float64))))
    return normalized, {
        "lead_s": actual_lead,
        "release_s": actual_release,
        "tail_rms_dbfs": _rms_dbfs(tail_rms),
        "tail_at_edge": bool(tail_rms >= threshold),
    }


def chunk_gap_seconds(chunk: str) -> float:
    return BATCH_CONTINUATION_GAP_SECONDS if re.search(r'[,;:—][\"\'”’)]*$', chunk) else BATCH_SENTENCE_GAP_SECONDS


def generate_batch_audio(
    args,
    model,
    text: str,
    prompt_wav_path: str | None,
    reference_wav_path: str | None,
    seed: int | None,
    content_gate: ContentGateClient | None = None,
    scratch_prefix: str = "segment",
) -> tuple[np.ndarray, list[str], list[dict]]:
    """Generate complete chunks, then join them with the existing adaptive timing."""
    chunks = split_batch_text(text)
    if not chunks:
        raise ValueError("batch text is required")
    sample_rate = int(model.tts_model.sample_rate)
    generated: list[np.ndarray] = []
    metrics: list[dict] = []
    for index, chunk in enumerate(chunks):
        # Keep the approved source chunk untouched for the content gate.  The
        # presenter pronunciation spelling and optional batch control are
        # synthesis-only, and must be applied exactly once at the model edge.
        render_chunk = final_text(
            chunk,
            getattr(args, "_batch_render_control", "") if index == 0 else "",
            getattr(args, "mode", "design"),
            bool(str(getattr(args, "prompt_text", "") or "").strip()),
        )
        attempts: list[dict] = []
        accepted_audio: np.ndarray | None = None
        accepted_metrics: dict | None = None
        accepted_attempt: int | None = None
        maximum = max(1, min(6, int(args.content_max_attempts))) if content_gate else 1
        for content_attempt in range(1, maximum + 1):
            maybe_cancel(args.cancel_file)
            chunk_seed = None if seed is None else (
                seed + index + (content_attempt - 1) * 104729
            ) % 2_147_483_647
            wav = model.generate(
                text=render_chunk,
                prompt_wav_path=prompt_wav_path,
                prompt_text=args.prompt_text.strip() if prompt_wav_path else None,
                reference_wav_path=reference_wav_path,
                cfg_value=args.cfg,
                inference_timesteps=args.steps,
                normalize=True,
                denoise=False,
                seed=chunk_seed,
            )
            audio, chunk_metrics = normalize_generated_chunk(wav, sample_rate, index)
            attempt_record = {
                "attempt": content_attempt,
                "seed": chunk_seed,
                "passed": True,
            }
            if content_gate is not None:
                emit(
                    "content_gate",
                    f"Checking text chunk {index + 1:02d} ({content_attempt}/{maximum})",
                    72,
                    chunk=index + 1,
                    attempt=content_attempt,
                )
                gate_path = Path(args.output) / (
                    f".{scratch_prefix}.chunk{index + 1:02d}.content{content_attempt}.wav"
                )
                audit_tail = np.zeros(
                    round(sample_rate * BATCH_CONTENT_AUDIT_TAIL_SECONDS), dtype=np.float32
                )
                sf.write(str(gate_path), np.concatenate([audio, audit_tail]), sample_rate)
                try:
                    result = content_gate.check(gate_path, chunk)
                finally:
                    try:
                        gate_path.unlink()
                    except FileNotFoundError:
                        pass
                attempt_record.update({
                    "passed": bool(result.get("passed")),
                    "heard": str(result.get("heard") or ""),
                    "expectedCoverage": result.get("expectedCoverage"),
                    "characterSimilarity": result.get("characterSimilarity"),
                    "lengthRatio": result.get("lengthRatio"),
                    "prefixCoverage": result.get("prefixCoverage"),
                    "suffixCoverage": result.get("suffixCoverage"),
                    "firstTokenMatched": result.get("firstTokenMatched"),
                    "finalTokenMatched": result.get("finalTokenMatched"),
                    "missingExpectedWords": result.get("missingExpectedWords") or [],
                    "alteredExpectedWords": result.get("alteredExpectedWords") or [],
                    "unexpectedHeardWords": result.get("unexpectedHeardWords") or [],
                    "missingContentWords": result.get("missingContentWords") or [],
                    "missingStopwords": result.get("missingStopwords") or [],
                    "failures": result.get("failures") or [],
                    "avgLogprob": result.get("avgLogprob"),
                    "finalWordProbability": result.get("finalWordProbability"),
                })
            attempts.append(attempt_record)
            if attempt_record["passed"]:
                accepted_audio = audio
                accepted_metrics = chunk_metrics
                accepted_attempt = content_attempt
                break
            emit(
                "content_gate",
                f"Incomplete chunk {index + 1:02d}; regenerating only this chunk",
                74,
                chunk=index + 1,
                attempt=content_attempt,
                failures=attempt_record.get("failures") or [],
            )

        if accepted_audio is None or accepted_metrics is None:
            last_failures = attempts[-1].get("failures") if attempts else []
            raise ContentGateError(
                f"chunk {index + 1:02d} failed content verification after {maximum} attempts: "
                + "; ".join(last_failures or ["unknown content mismatch"])
            )
        accepted_metrics.update({
            "render_text": render_chunk,
            "content_gate_enabled": content_gate is not None,
            "content_gate_passed": True,
            "content_gate_accepted_attempt": accepted_attempt,
            "content_gate_attempts": attempts,
        })
        generated.append(accepted_audio)
        metrics.append(accepted_metrics)

    pieces: list[np.ndarray] = []
    for index, audio in enumerate(generated):
        pieces.append(audio)
        if index + 1 >= len(generated):
            continue
        target_gap = chunk_gap_seconds(chunks[index])
        native_gap = metrics[index]["release_s"] + metrics[index + 1]["lead_s"]
        inserted_gap = max(0.0, target_gap - native_gap)
        metrics[index]["gap_after_s"] = round(target_gap, 3)
        metrics[index]["inserted_silence_s"] = round(inserted_gap, 3)
        pieces.append(np.zeros(round(sample_rate * inserted_gap), dtype=np.float32))
    final_silence = max(0.0, BATCH_FINAL_TAIL_SECONDS - metrics[-1]["release_s"])
    metrics[-1]["final_tail_s"] = round(BATCH_FINAL_TAIL_SECONDS, 3)
    pieces.append(np.zeros(round(sample_rate * final_silence), dtype=np.float32))
    return np.concatenate(pieces), chunks, metrics


def attempt_seed(base_seed: int | None, attempt: int) -> int | None:
    if base_seed is None:
        return random.SystemRandom().randint(0, 2_147_483_647)
    return (base_seed + attempt) % 2_147_483_647


def speaker_similarity(args, reference_audio: str, candidate: Path) -> float:
    cmd = [
        args.speaker_python,
        args.speaker_script,
        "--ref",
        reference_audio,
        "--candidate",
        str(candidate),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=180, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"speaker gate failed: {detail[:500]}")
    data = json.loads(proc.stdout)
    return float(data["similarity"])


def cleanup_attempts(paths: list[Path], keep: Path | None = None) -> None:
    for path in paths:
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def load_segments(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("segments json must be a non-empty list")
    segments = []
    for expected, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError("segment item must be an object")
        index = int(item.get("index"))
        text = str(item.get("text") or "")
        if index != expected:
            raise ValueError("segment indexes must be 1-based and consecutive")
        if not text.strip():
            raise ValueError(f"segment {expected:02d} text is required")
        segments.append({"index": index, "text": text})
    return segments


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_batch_manifest(path: Path, manifest: dict, status: str | None = None) -> None:
    if status is not None:
        manifest["status"] = status
    manifest["total_audio_s"] = round(
        sum(float(item.get("duration_s") or 0) for item in manifest.get("segments") or []),
        2,
    )
    atomic_write_json(path, manifest)


def generate_batch_segment(
    args,
    model,
    segment: dict,
    text: str,
    reference_audio: str | None,
    prompt_wav_path: str | None,
    reference_wav_path: str | None,
    gate_enabled: bool,
    content_gate: ContentGateClient | None,
) -> dict:
    index = int(segment["index"])
    output = args.output / f"seg_{index:02d}.wav"
    max_attempts = max(1, min(5, int(args.speaker_max_attempts or 1)))
    candidates = max(0, int(args.candidates or 0))
    total = candidates if candidates > 0 else max_attempts
    fixed_candidates = candidates > 0
    attempt_paths = []
    gate_attempts = []
    accepted_path = None
    best_path = None
    best_record = None
    best_similarity = -1.0
    attempt_records: list[tuple[Path, dict]] = []
    started = time.time()

    for attempt in range(1, total + 1):
        maybe_cancel(args.cancel_file)
        seed = attempt_seed(args.seed, (index - 1) * 1000 + attempt - 1)
        emit(
            "generate",
            f"Generating segment {index:02d} ({attempt}/{total})",
            8 + min(80, int((index - 1) * 80 / max(1, args._segment_count)) + attempt),
            mode=args.mode,
            segment=index,
            attempt=attempt,
            seed=seed,
        )
        try:
            wav, chunks, chunk_metrics = generate_batch_audio(
                args,
                model,
                text,
                prompt_wav_path,
                reference_wav_path,
                seed,
                content_gate=content_gate,
                scratch_prefix=f"seg{index:02d}.speaker{attempt}.{args.job_id}",
            )
        except ContentGateError as error:
            # A fully exhausted chunk produces no candidate file.  Try the
            # next independent speaker seed before failing the segment, while
            # keeping the gate fail-closed for every promoted output.
            gate_attempts.append({
                "attempt": attempt,
                "seed": seed,
                "accepted": False,
                "contentGatePassed": False,
                "error": str(error),
            })
            if attempt < total:
                emit(
                    "content_gate",
                    f"Segment {index:02d} content incomplete; trying a new speaker seed",
                    75,
                    segment=index,
                    attempt=attempt,
                    error=str(error),
                )
                continue
            # A fixed-candidate run may already have valid, content-verified
            # candidates. If only the final independent candidate exhausts
            # its gate retries, keep those earlier valid candidates eligible
            # for the normal best-speaker selection below. Still fail closed
            # when no candidate has passed content verification.
            if fixed_candidates and attempt_records:
                break
            cleanup_attempts(attempt_paths)
            raise
        except Exception:
            cleanup_attempts(attempt_paths)
            raise

        content_passed = bool(
            content_gate is None
            or all(item.get("content_gate_passed") for item in chunk_metrics)
        )
        if not content_passed:
            cleanup_attempts(attempt_paths)
            raise ContentGateError(
                f"segment {index:02d} contained a chunk that did not pass content verification"
            )

        maybe_cancel(args.cancel_file)
        candidate = output.with_name(f".{output.stem}.{args.job_id}.attempt{attempt}{output.suffix}")
        sf.write(str(candidate), wav, model.tts_model.sample_rate)
        attempt_paths.append(candidate)
        duration = len(wav) / float(model.tts_model.sample_rate)
        record = {
            "attempt": attempt,
            "seed": seed,
            "duration": round(duration, 2),
            "chunk_count": len(chunks),
            "edge_tail_chunks": sum(1 for item in chunk_metrics if item.get("tail_at_edge")),
            "contentGatePassed": content_passed,
            "contentGate": [
                {
                    "chunk": chunk_index + 1,
                    "text": chunks[chunk_index],
                    "renderText": metric.get("render_text"),
                    "passed": bool(metric.get("content_gate_passed")),
                    "acceptedAttempt": metric.get("content_gate_accepted_attempt"),
                    "attempts": metric.get("content_gate_attempts") or [],
                }
                for chunk_index, metric in enumerate(chunk_metrics)
            ],
            "timing": [
                {
                    key: metric[key]
                    for key in (
                        "lead_s",
                        "release_s",
                        "tail_rms_dbfs",
                        "tail_at_edge",
                        "gap_after_s",
                        "inserted_silence_s",
                        "final_tail_s",
                    )
                    if key in metric
                }
                for metric in chunk_metrics
            ],
            "accepted": False,
        }
        attempt_records.append((candidate, record))
        if gate_enabled:
            emit("speaker_gate", f"Checking segment {index:02d} ({attempt}/{total})", 82, segment=index, attempt=attempt)
            similarity = speaker_similarity(args, reference_audio, candidate)
            record["similarity"] = round(similarity, 4)
            if similarity > best_similarity:
                best_similarity = similarity
                best_path = candidate
                best_record = record.copy()
            if not fixed_candidates and similarity >= args.speaker_threshold:
                record["accepted"] = True
                accepted_path = candidate
                best_record = record.copy()
                gate_attempts.append(record)
                break
        else:
            record["accepted"] = True
            accepted_path = candidate
            best_record = record.copy()
            gate_attempts.append(record)
            break
        gate_attempts.append(record)

    if fixed_candidates and attempt_records:
        if gate_enabled:
            accepted_path, selected_record = max(
                attempt_records,
                key=lambda item: float(item[1].get("similarity") or -1.0),
            )
        else:
            accepted_path, selected_record = attempt_records[0]
        best_record = selected_record.copy()
    elif accepted_path is None:
        accepted_path = best_path or (attempt_paths[-1] if attempt_paths else None)
    if accepted_path is None or best_record is None:
        cleanup_attempts(attempt_paths)
        raise ValueError(f"segment {index:02d} generation failed")
    speaker_passed = bool(
        not gate_enabled
        or (best_record.get("similarity") is not None and best_record["similarity"] >= args.speaker_threshold)
    )
    content_passed = bool(best_record.get("contentGatePassed"))
    passed = content_passed and speaker_passed
    best_record["accepted"] = passed
    for candidate_path, record in attempt_records:
        record["accepted"] = bool(passed and candidate_path == accepted_path)
    os.replace(accepted_path, output)
    cleanup_attempts(attempt_paths, keep=accepted_path)
    return {
        "index": index,
        "file": output.name,
        "seed": best_record.get("seed"),
        "similarity": best_record.get("similarity"),
        "passed": passed,
        "duration_s": round(float(best_record.get("duration") or 0), 2),
        "attempts": gate_attempts,
        "wall_s": round(time.time() - started, 2),
        "text_chars": len(segment["text"]),
        "chunk_count": int(best_record.get("chunk_count") or 1),
        "contentGatePassed": content_passed,
        "contentGate": best_record.get("contentGate") or [],
        "timing": best_record.get("timing") or [],
    }


def run_batch(args) -> int:
    segments = load_segments(args.segments_json)
    args._segment_count = len(segments)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / args.manifest_name
    if args.manifest_name != Path(args.manifest_name).name or not args.manifest_name.endswith(".json"):
        raise ValueError("manifest name must be a json basename")

    has_prompt_text = bool(args.prompt_text.strip())
    control = clean_instruction(args.control, args.language, args.accent)
    args._batch_render_control = control
    reference_audio = str(Path(args.reference_audio).resolve()) if args.reference_audio else None
    if args.mode in ("reference", "ultimate") and not reference_audio:
        raise ValueError("reference audio is required for this VoxCPM mode")
    if has_prompt_text and not reference_audio:
        raise ValueError("prompt transcript requires reference audio")
    if args.mode == "ultimate" and not has_prompt_text:
        raise ValueError("prompt transcript is required for ultimate cloning")
    if args.mode == "lora" and not args.lora_path:
        raise ValueError("LoRA path is required for LoRA profile mode")
    gate_enabled = bool(args.speaker_gate and reference_audio)
    content_gate_enabled = args.content_gate is not False
    manifest = {
        "batch_id": args.output.name,
        "created": int(time.time()),
        "profile": args.profile,
        "threshold": args.speaker_threshold if gate_enabled else None,
        "contentGate": {
            "enabled": content_gate_enabled,
            "model": args.content_model if content_gate_enabled else None,
            "maxAttemptsPerChunk": args.content_max_attempts if content_gate_enabled else None,
            "persistent": content_gate_enabled,
            "failClosed": True,
        },
        "timing": {
            "chunkMaxWords": BATCH_CHUNK_MAX_WORDS,
            "chunkMinWords": BATCH_CHUNK_MIN_WORDS,
            "continuationGapSeconds": BATCH_CONTINUATION_GAP_SECONDS,
            "sentenceGapSeconds": BATCH_SENTENCE_GAP_SECONDS,
            "finalTailSeconds": BATCH_FINAL_TAIL_SECONDS,
            "adaptiveVadBoundaries": True,
        },
        "status": "running",
        "segments": [],
        "total_audio_s": 0,
    }
    write_batch_manifest(manifest_path, manifest)

    content_gate: ContentGateClient | None = None
    try:
        maybe_cancel(args.cancel_file)
        emit("load_model", "Loading VoxCPM2", 4, mode=args.mode, profile=args.profile)
        from voxcpm import VoxCPM

        model = VoxCPM.from_pretrained(
            args.model_path,
            load_denoiser=False,
            local_files_only=True,
            optimize=False,
            device=args.device,
            lora_weights_path=args.lora_path or None,
        )
        if content_gate_enabled:
            emit("content_gate", f"Loading Whisper {args.content_model} content gate", 6)
            content_gate = ContentGateClient(args)
            content_gate.start()
        prompt_wav_path = reference_audio if args.mode == "ultimate" or (args.mode == "lora" and has_prompt_text) else None
        reference_wav_path = reference_audio if args.mode in ("reference", "ultimate") or (args.mode == "lora" and reference_audio) else None
        for segment in segments:
            maybe_cancel(args.cancel_file)
            # Batch chunking and content verification use the approved source
            # script.  Rendering-only pronunciation hints are applied later,
            # one time per chunk, inside generate_batch_audio().
            text = re.sub(r"\s+", " ", segment["text"].strip())
            record = generate_batch_segment(
                args,
                model,
                segment,
                text,
                reference_audio,
                prompt_wav_path,
                reference_wav_path,
                gate_enabled,
                content_gate,
            )
            manifest["segments"] = [item for item in manifest["segments"] if item.get("index") != record["index"]]
            manifest["segments"].append(record)
            manifest["segments"].sort(key=lambda item: int(item.get("index") or 0))
            write_batch_manifest(manifest_path, manifest, "running")
        write_batch_manifest(manifest_path, manifest, "done")
        emit("done", "completed", 100, output=args.output.name)
        return 0
    except KeyboardInterrupt:
        write_batch_manifest(manifest_path, manifest, "cancelled")
        raise
    except Exception as error:
        manifest["error"] = f"{type(error).__name__}: {error}"
        write_batch_manifest(manifest_path, manifest, "failed")
        raise
    finally:
        if content_gate is not None:
            content_gate.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["design", "reference", "ultimate", "lora"], required=True)
    parser.add_argument("--text")
    parser.add_argument("--segments-json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--workflow", default="castle-voxcpm-tts-web")
    parser.add_argument("--manifest-name", default="manifest.json")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--reference-audio")
    parser.add_argument("--prompt-text", default="")
    parser.add_argument("--control", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--accent", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--lora-path")
    parser.add_argument("--cfg", type=float, default=2.0)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("--speaker-gate", action="store_true")
    parser.add_argument("--speaker-threshold", type=float, default=0.82)
    parser.add_argument("--speaker-max-attempts", type=int, default=2)
    parser.add_argument("--speaker-python", default=str(Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python"))
    parser.add_argument("--speaker-script", default=str(Path(__file__).with_name("speaker_similarity.py")))
    parser.add_argument("--candidates", type=int, default=0)
    parser.add_argument(
        "--content-gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="verify every batch chunk with Whisper (enabled by default for --segments-json)",
    )
    parser.add_argument("--content-max-attempts", type=int, default=3)
    parser.add_argument("--content-model", default="small")
    parser.add_argument("--content-python", default=str(Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python"))
    parser.add_argument("--content-script", default=str(Path(__file__).with_name("content_completeness.py")))
    parser.add_argument("--content-model-cache", default=str(Path.home() / ".cache/whisper"))
    parser.add_argument("--content-startup-timeout", type=float, default=300.0)
    parser.add_argument("--content-timeout", type=float, default=180.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.segments_json:
        if args.text:
            parser.error("--text and --segments-json are mutually exclusive")
        return run_batch(args)
    missing = []
    if not args.text:
        missing.append("--text")
    if not args.sidecar:
        missing.append("--sidecar")
    if missing:
        parser.error("the following arguments are required: " + ", ".join(missing))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.sidecar.parent.mkdir(parents=True, exist_ok=True)

    has_prompt_text = bool(args.prompt_text.strip())
    control = clean_instruction(args.control, args.language, args.accent)
    text = final_text(args.text, control, args.mode, has_prompt_text)
    reference_audio = str(Path(args.reference_audio).resolve()) if args.reference_audio else None
    if args.mode in ("reference", "ultimate") and not reference_audio:
        raise ValueError("reference audio is required for this VoxCPM mode")
    if has_prompt_text and not reference_audio:
        raise ValueError("prompt transcript requires reference audio")
    if args.mode == "ultimate" and not has_prompt_text:
        raise ValueError("prompt transcript is required for ultimate cloning")
    if args.mode == "lora" and not args.lora_path:
        raise ValueError("LoRA path is required for LoRA profile mode")
    gate_enabled = bool(args.speaker_gate and reference_audio)
    max_attempts = max(1, min(5, int(args.speaker_max_attempts or 1)))

    if args.dry_run:
        emit("dry_run", f"ready: {args.mode}", 100, mode=args.mode)
        return 0

    maybe_cancel(args.cancel_file)
    emit("load_model", "Loading VoxCPM2", 4, mode=args.mode, profile=args.profile)

    from voxcpm import VoxCPM

    model = VoxCPM.from_pretrained(
        args.model_path,
        load_denoiser=False,
        local_files_only=True,
        optimize=False,
        device=args.device,
        lora_weights_path=args.lora_path or None,
    )

    prompt_wav_path = reference_audio if args.mode == "ultimate" or (args.mode == "lora" and has_prompt_text) else None
    reference_wav_path = reference_audio if args.mode in ("reference", "ultimate") or (args.mode == "lora" and reference_audio) else None

    gate_attempts = []
    attempt_paths = []
    accepted_path = None
    best_attempt = None
    best_similarity = -1.0
    for attempt in range(1, max_attempts + 1):
        maybe_cancel(args.cancel_file)
        seed = attempt_seed(args.seed, attempt - 1)
        emit(
            "generate",
            f"Generating speech ({attempt}/{max_attempts})" if gate_enabled else "Generating speech",
            25 + min(45, attempt * 12),
            mode=args.mode,
            attempt=attempt,
            seed=seed,
        )
        wav = model.generate(
            text=text,
            prompt_wav_path=prompt_wav_path,
            prompt_text=args.prompt_text.strip() if prompt_wav_path else None,
            reference_wav_path=reference_wav_path,
            cfg_value=args.cfg,
            inference_timesteps=args.steps,
            normalize=True,
            denoise=False,
            seed=seed,
        )

        maybe_cancel(args.cancel_file)
        candidate = args.output.with_name(f".{args.output.stem}.{args.job_id}.attempt{attempt}{args.output.suffix}")
        sf.write(str(candidate), wav, model.tts_model.sample_rate)
        attempt_paths.append(candidate)
        duration = len(wav) / float(model.tts_model.sample_rate)
        record = {
            "attempt": attempt,
            "seed": seed,
            "duration": round(duration, 2),
            "accepted": False,
        }
        if gate_enabled:
            emit("speaker_gate", f"Checking speaker match ({attempt}/{max_attempts})", 78, attempt=attempt)
            similarity = speaker_similarity(args, reference_audio, candidate)
            record["similarity"] = round(similarity, 4)
            if similarity >= args.speaker_threshold:
                record["accepted"] = True
            if similarity > best_similarity:
                best_similarity = similarity
                best_attempt = record.copy()
            if record["accepted"]:
                accepted_path = candidate
                gate_attempts.append(record)
                break
            gate_attempts.append(record)
            emit(
                "speaker_gate",
                f"Speaker match {similarity:.3f} below {args.speaker_threshold:.3f}",
                82,
                attempt=attempt,
                similarity=round(similarity, 4),
            )
        else:
            record["accepted"] = True
            accepted_path = candidate
            gate_attempts.append(record)
            break

    if accepted_path is None:
        cleanup_attempts(attempt_paths)
        best_label = f"{best_similarity:.3f}" if best_similarity >= 0 else "n/a"
        raise ValueError(
            f"speaker gate failed after {max_attempts} attempts; best similarity {best_label}, "
            f"threshold {args.speaker_threshold:.3f}"
        )

    os.replace(accepted_path, args.output)
    cleanup_attempts(attempt_paths, keep=accepted_path)
    accepted_record = next((item for item in gate_attempts if item.get("accepted")), gate_attempts[-1])
    duration = float(accepted_record["duration"])
    sidecar = {
        "promptId": args.job_id,
        "workflow": args.workflow,
        "feature": "VoxCPM 多語音色",
        "created": int(time.time()),
        "status": "completed",
        "settings": {
            "mode": args.mode,
            "language": args.language,
            "accent": args.accent,
            "profile": args.profile,
            "control": args.control,
            "cfg": args.cfg,
            "steps": args.steps,
            "seed": args.seed,
            "modelPath": args.model_path,
            "loraPath": args.lora_path or "",
            "hasReferenceAudio": bool(reference_audio),
            "hasPromptText": has_prompt_text,
            "speakerGate": gate_enabled,
        },
        "summary": {
            "duration": round(duration, 2),
            "sampleRate": int(model.tts_model.sample_rate),
            "speakerSimilarity": accepted_record.get("similarity"),
        },
        "speakerGate": {
            "enabled": gate_enabled,
            "threshold": args.speaker_threshold if gate_enabled else None,
            "passed": True,
            "attempts": gate_attempts,
            "best": best_attempt or accepted_record,
        },
        "detailNodes": [
            {
                "id": "voxcpm",
                "type": "VoxCPM2",
                "inputs": [
                    {"name": "mode", "value": args.mode},
                    {"name": "language", "value": args.language},
                    {"name": "accent", "value": args.accent},
                    {"name": "profile", "value": args.profile},
                    {"name": "control", "value": args.control},
                    {"name": "has_reference_audio", "value": bool(reference_audio)},
                    {"name": "has_prompt_text", "value": has_prompt_text},
                    {"name": "speaker_gate", "value": gate_enabled},
                    {"name": "speaker_threshold", "value": args.speaker_threshold if gate_enabled else ""},
                    {"name": "speaker_similarity", "value": accepted_record.get("similarity", "")},
                    {"name": "cfg", "value": args.cfg},
                    {"name": "steps", "value": args.steps},
                    {"name": "text", "value": args.text[:4000]},
                ],
            }
        ],
        "outputs": [
            {
                "kind": "audio",
                "filename": args.output.name,
                "subfolder": args.output.parent.relative_to(args.output.parents[1]).as_posix()
                if len(args.output.parents) > 1 and args.output.parent.name != "output" else "",
                "type": "output",
            }
        ],
    }
    args.sidecar.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    done_extra = {"duration": round(duration, 2), "output": args.output.name}
    if accepted_record.get("similarity") is not None:
        done_extra["speakerSimilarity"] = accepted_record["similarity"]
    emit("done", "completed", 100, **done_extra)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        emit("cancelled", "cancelled", 100)
        raise SystemExit(130)
    except Exception as error:
        emit("error", str(error), 100)
        print(f"[voxcpm-worker] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
