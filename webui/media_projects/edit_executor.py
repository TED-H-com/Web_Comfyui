"""Private deterministic ffmpeg assembly and mechanical QA for edit timelines."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .edit_timeline import EditTimeline


EDIT_RECEIPT_VERSION = "castle.edit-receipt.v1"
SourceResolver = Callable[[str], Path]
_SUBTITLE_PROFILES = frozenset({"language-burnin", "zh-tw-burnin"})
_AUDIO_MIX_PROFILES = frozenset({"social-standard"})
_BRANDING_PROFILES = frozenset({"castle-default"})
_DELIVERABLE_PROFILES = frozenset({"mp4-h264-browser"})


def _stable_sha(path: Path, expected: str | None = None) -> tuple[int, str]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or before.st_nlink != 1 or before.st_size <= 0
        ):
            raise ValueError("edit source is not a stable regular file")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(fd, 8 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("edit source changed while hashing")
        result = digest.hexdigest()
        if expected is not None and result != expected:
            raise ValueError("edit source digest differs from accepted version")
        return size, result
    finally:
        os.close(fd)


def _srt_time(value: float) -> str:
    millis = int(round(value * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _subtitle_text(timeline: EditTimeline) -> str:
    blocks = []
    for index, cue in enumerate(timeline.captions, start=1):
        text = cue.text.replace("\r", " ").replace("\x00", "").strip()
        blocks.append(
            f"{index}\n{_srt_time(cue.start_seconds)} --> {_srt_time(cue.end_seconds)}\n{text}\n"
        )
    return "\n".join(blocks)


def _write_exclusive_text(path: Path, value: str) -> None:
    raw = value.encode("utf-8")
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class EditReceipt:
    timeline_hash: str
    output_path: Path
    output_sha256: str
    byte_size: int
    subtitle_path: Path | None
    subtitle_burned_in: bool
    contact_sheet_path: Path
    contact_sheet_sha256: str
    contact_sheet_byte_size: int
    contact_sheet_width: int
    contact_sheet_height: int
    soundtrack_mixed: bool
    soundtrack_ducking_applied: bool
    sound_effect_count: int
    duration_seconds: float
    width: int
    height: int
    fps: float
    schema_version: str = EDIT_RECEIPT_VERSION


def _probe(path: Path, timeline: EditTimeline) -> tuple[float, float]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is required for edit QA")
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries",
            "format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
            "-of", "json", str(path),
        ],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError("ffprobe rejected the assembled edit")
    try:
        value = json.loads(completed.stdout)
        streams = value["streams"]
        duration = float(value["format"]["duration"])
        video = [item for item in streams if item.get("codec_type") == "video"]
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        numerator, denominator = video[0]["avg_frame_rate"].split("/", 1)
        fps = float(numerator) / float(denominator)
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as error:
        raise RuntimeError("assembled edit metadata is invalid") from error
    if (
        len(video) != 1 or len(audio) != 1
        or video[0].get("codec_name") != "h264" or audio[0].get("codec_name") != "aac"
        or int(video[0].get("width", 0)) != timeline.width
        or int(video[0].get("height", 0)) != timeline.height
        or abs(fps - timeline.fps) > 0.02
        or abs(duration - timeline.total_duration_seconds) > max(0.15, timeline.total_duration_seconds * 0.02)
        or "mp4" not in str(value["format"].get("format_name") or "").split(",")
    ):
        raise RuntimeError("assembled edit failed its delivery profile")
    return duration, fps


def _create_contact_sheet(ffmpeg: str, source: Path, destination: Path, duration: float) -> None:
    """Create one deterministic six-frame review sheet from the assembled picture."""

    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-an", "-vf",
            f"fps=6/{duration:.6f},scale=320:-2:flags=lanczos,tile=3x2:nb_frames=6",
            "-frames:v", "1", "-map_metadata", "-1", "-threads", "1",
            "-c:v", "mjpeg", "-q:v", "2", str(destination),
        ],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        timeout=max(120, int(duration * 10)),
    )
    if completed.returncode != 0:
        raise RuntimeError("ffmpeg could not create the delivery contact sheet")


def _probe_contact_sheet(path: Path) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is required for contact-sheet QA")
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path),
        ],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
    )
    try:
        stream = json.loads(completed.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("delivery contact sheet metadata is invalid") from error
    if completed.returncode != 0 or stream.get("codec_name") != "mjpeg" or width != 960 or height <= 0:
        raise RuntimeError("delivery contact sheet failed its qualified profile")
    return width, height


def execute_edit_timeline(
    timeline: EditTimeline,
    resolver: SourceResolver,
    output_root: Path,
) -> EditReceipt:
    """Transcode exact accepted clips, concatenate, emit SRT, and verify output."""

    if not isinstance(timeline, EditTimeline) or not callable(resolver):
        raise TypeError("timeline and source resolver are required")
    if timeline.subtitle_profile not in _SUBTITLE_PROFILES:
        raise ValueError("subtitle profile has no qualified edit implementation")
    if timeline.audio_mix_profile not in _AUDIO_MIX_PROFILES:
        raise ValueError("audio mix profile has no qualified edit implementation")
    if timeline.branding_profile not in _BRANDING_PROFILES:
        raise ValueError("branding profile has no qualified edit implementation")
    if timeline.deliverable_profile not in _DELIVERABLE_PROFILES:
        raise ValueError("deliverable profile has no qualified edit implementation")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for edit assembly")
    root = Path(output_root).resolve(strict=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError("edit output root is unsafe")
    sources = []
    for clip in timeline.clips:
        path = Path(resolver(clip.asset_version_id)).resolve(strict=True)
        _stable_sha(path, clip.asset_sha256)
        sources.append(path)
    soundtrack = None
    if timeline.soundtrack_asset_version_id is not None:
        soundtrack = Path(resolver(timeline.soundtrack_asset_version_id)).resolve(strict=True)
        _stable_sha(soundtrack, timeline.soundtrack_asset_sha256)
    effects = []
    for effect in timeline.sound_effects:
        path = Path(resolver(effect.asset_version_id)).resolve(strict=True)
        _stable_sha(path, effect.asset_sha256)
        effects.append((effect, path))
    output = root / f"castle_edit_{timeline.output_token}.mp4"
    subtitle = root / f"castle_edit_{timeline.output_token}.srt" if timeline.captions else None
    contact_sheet = root / f"castle_edit_{timeline.output_token}.contact.jpg"
    if output.exists() or contact_sheet.exists() or (subtitle is not None and subtitle.exists()):
        raise FileExistsError("edit output token already exists and requires reconciliation")

    with tempfile.TemporaryDirectory(prefix="castle-edit-", dir=root) as temporary_name:
        temporary = Path(temporary_name)
        os.chmod(temporary, 0o700)
        staged_output = temporary / "assembled.mp4"
        staged_contact_sheet = temporary / "contact-sheet.jpg"
        staged_subtitle = temporary / "captions.srt" if subtitle is not None else None
        if staged_subtitle is not None:
            _write_exclusive_text(staged_subtitle, _subtitle_text(timeline))
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        for source in sources:
            command.extend(["-i", str(source)])
        if soundtrack is not None:
            command.extend(["-stream_loop", "-1", "-i", str(soundtrack)])
        for _effect, path in effects:
            command.extend(["-i", str(path)])
        filters = []
        concat_inputs = []
        for index, clip in enumerate(timeline.clips):
            duration = f"{clip.source_duration_seconds:.6f}"
            filters.append(
                f"[{index}:v]trim=duration={duration},setpts=PTS-STARTPTS,"
                f"scale={timeline.width}:{timeline.height}:force_original_aspect_ratio=decrease,"
                f"pad={timeline.width}:{timeline.height}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={timeline.fps},format=yuv420p[v{index}]"
            )
            filters.append(
                f"[{index}:a]atrim=duration={duration},asetpts=PTS-STARTPTS,"
                f"aresample=48000[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        filters.append(
            "".join(concat_inputs)
            + f"concat=n={len(timeline.clips)}:v=1:a=1[vcat][acat]"
        )
        video_output = "[vcat]"
        if staged_subtitle is not None:
            filters.append(
                "[vcat]subtitles=filename=captions.srt:"
                "force_style='FontName=Noto Sans CJK TC,FontSize=18,"
                "Outline=2,Shadow=0,MarginV=42'[vout]"
            )
            video_output = "[vout]"
        audio_input = "[acat]"
        if soundtrack is not None:
            soundtrack_index = len(sources)
            filters.append(
                f"[{soundtrack_index}:a]atrim=duration={timeline.total_duration_seconds:.6f},"
                "asetpts=PTS-STARTPTS,aresample=48000,"
                f"volume={timeline.soundtrack_gain_db:.2f}dB[bgm]"
            )
            filters.append("[acat]asplit=2[dialoguemix][duckkey]")
            filters.append(
                "[bgm][duckkey]sidechaincompress=threshold=0.03:ratio=8:"
                "attack=20:release=250:knee=4[bgmduck]"
            )
            filters.append(
                "[dialoguemix][bgmduck]amix=inputs=2:duration=first:"
                "dropout_transition=0:normalize=0[amix]"
            )
            audio_input = "[amix]"
        effect_outputs = []
        effect_input_start = len(sources) + (1 if soundtrack is not None else 0)
        for offset, (effect, _path) in enumerate(effects):
            label = f"fx{offset}"
            delay_ms = int(round(effect.timeline_start_seconds * 1000))
            filters.append(
                f"[{effect_input_start + offset}:a]"
                f"atrim=start={effect.source_start_seconds:.6f}:duration={effect.duration_seconds:.6f},"
                f"asetpts=PTS-STARTPTS,aresample=48000,volume={effect.gain_db:.2f}dB,"
                f"adelay={delay_ms}:all=1,apad=whole_dur={timeline.total_duration_seconds:.6f},"
                f"atrim=duration={timeline.total_duration_seconds:.6f}[{label}]"
            )
            effect_outputs.append(f"[{label}]")
        if effect_outputs:
            filters.append(
                audio_input + "".join(effect_outputs)
                + f"amix=inputs={len(effect_outputs) + 1}:duration=first:"
                "dropout_transition=0:normalize=0[withfx]"
            )
            audio_input = "[withfx]"
        filters.append(f"{audio_input}loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        command.extend([
            "-filter_complex", ";".join(filters), "-map", video_output, "-map", "[aout]",
            "-map_metadata", "-1", "-threads", "1", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-flags:a", "+bitexact", str(staged_output),
        ])
        completed = subprocess.run(
            command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=max(300, int(timeline.total_duration_seconds * 30)),
            cwd=temporary,
        )
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg could not assemble the reviewed timeline")
        duration, fps = _probe(staged_output, timeline)
        _create_contact_sheet(ffmpeg, staged_output, staged_contact_sheet, duration)
        contact_width, contact_height = _probe_contact_sheet(staged_contact_sheet)
        byte_size, digest = _stable_sha(staged_output)
        contact_size, contact_digest = _stable_sha(staged_contact_sheet)
        os.link(staged_output, output)
        try:
            os.link(staged_contact_sheet, contact_sheet)
            if subtitle is not None:
                os.link(staged_subtitle, subtitle)
        except BaseException:
            output.unlink(missing_ok=True)
            contact_sheet.unlink(missing_ok=True)
            if subtitle is not None:
                subtitle.unlink(missing_ok=True)
            raise
    return EditReceipt(
        timeline.timeline_hash, output, digest, byte_size, subtitle,
        subtitle is not None, contact_sheet, contact_digest, contact_size,
        contact_width, contact_height, soundtrack is not None, soundtrack is not None,
        len(effects),
        round(duration, 6), timeline.width, timeline.height, round(fps, 6),
    )


__all__ = [
    "EDIT_RECEIPT_VERSION", "EditReceipt", "SourceResolver", "execute_edit_timeline",
]
