from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


PROXY_TASK_SCHEMA_VERSION = "2"
PROXY_EXECUTOR_SURFACE_VERSION = "canonical-proxy-executor-v1"
CANONICAL_REEL_FINAL_SIZE = (1080, 1920)
CANONICAL_REVIEW_PROXY_SIZE = (720, 1280)
CANONICAL_REVIEW_AUDIO_BITRATE_BPS = 96_000
# ffmpeg's AAC encoder targets ``-b:a 96k``; the muxed stream's measured
# average is not byte-for-byte 96000 (a short fixture currently reports
# 96053). Keep a narrow, explicit media tolerance while the command and runner
# fingerprints retain the exact requested setting.
CANONICAL_REVIEW_AUDIO_BITRATE_TOLERANCE_BPS = 8_000
CANONICAL_REVIEW_PROXY_SETTINGS = {
    "vf": "scale='min(720,iw)':-2",
    "preset": "veryfast",
    "crf": "28",
    "audio_bitrate": "96k",
    "video_codec": "libx264",
    "audio_codec": "aac",
    "pix_fmt": "yuv420p",
    "movflags": "+faststart",
}


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_ffmpeg_path(ffmpeg_bin: str | None = None) -> Path:
    requested = str(ffmpeg_bin or "ffmpeg")
    found = shutil.which(requested)
    if found is None:
        raise ValueError("ffmpeg not found; cannot create canonical review proxy")
    path = Path(found).resolve(strict=True)
    if not path.is_file():
        raise ValueError("canonical ffmpeg executable is unavailable")
    return path


def canonical_proxy_implementation_hash(ffmpeg_bin: str | None = None) -> str:
    ffmpeg = canonical_ffmpeg_path(ffmpeg_bin)
    contract = Path(__file__).resolve(strict=True)
    executor = contract.with_name("reel_proxy_executor.py").resolve(strict=True)
    return _canonical_json_sha256({
        "schema": PROXY_TASK_SCHEMA_VERSION,
        "contract_sha256": _file_sha256(contract),
        "executor_surface": PROXY_EXECUTOR_SURFACE_VERSION,
        "executor": "webui/orchestrator/reel_proxy_executor.py",
        "executor_sha256": _file_sha256(executor),
        "ffmpeg_path": str(ffmpeg),
        "ffmpeg_sha256": _file_sha256(ffmpeg),
    })


def canonical_proxy_input_hash(
    final_artifact_id: str,
    final_sha256: str,
    implementation_hash: str | None = None,
) -> str:
    if implementation_hash is None:
        implementation_hash = canonical_proxy_implementation_hash()
    payload = {
        "workflow": "artifact.make_proxy",
        "role": "proxy",
        "task_schema_version": PROXY_TASK_SCHEMA_VERSION,
        "implementation_hash": str(implementation_hash),
        "source": {
            "kind": "artifact",
            "artifact_id": str(final_artifact_id),
            "content_hash": str(final_sha256),
        },
        "settings": CANONICAL_REVIEW_PROXY_SETTINGS,
    }
    return _canonical_json_sha256(payload)


def canonical_proxy_metadata_matches(
    metadata: object,
    final_artifact_id: str,
    final_sha256: str,
    *,
    implementation_hash: str | None = None,
) -> bool:
    if not isinstance(metadata, dict):
        return False
    try:
        current_implementation = (
            str(implementation_hash)
            if implementation_hash is not None
            else canonical_proxy_implementation_hash()
        )
    except (OSError, ValueError):
        return False
    return (
        metadata.get("workflow") == "artifact.make_proxy"
        and metadata.get("role") == "proxy"
        and metadata.get("task_schema_version") == PROXY_TASK_SCHEMA_VERSION
        and metadata.get("implementation_hash") == current_implementation
        and metadata.get("source_artifact_id") == final_artifact_id
        and metadata.get("source_content_hash") == final_sha256
        and metadata.get("settings") == CANONICAL_REVIEW_PROXY_SETTINGS
        and metadata.get("input_hash") == canonical_proxy_input_hash(
            final_artifact_id, final_sha256, current_implementation)
    )


def canonical_proxy_ffmpeg_command(
    source: Path,
    output: Path,
    *,
    ffmpeg_bin: str | None = None,
) -> list[str]:
    settings = CANONICAL_REVIEW_PROXY_SETTINGS
    return [
        str(canonical_ffmpeg_path(ffmpeg_bin)),
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-i", str(source),
        "-vf", settings["vf"],
        "-c:v", settings["video_codec"],
        "-preset", settings["preset"],
        "-crf", settings["crf"],
        "-pix_fmt", settings["pix_fmt"],
        "-c:a", settings["audio_codec"],
        "-b:a", settings["audio_bitrate"],
        "-movflags", settings["movflags"],
        str(output),
    ]


def canonical_proxy_stream_info(probe: object) -> dict | None:
    """Normalize ffprobe JSON only for the canonical mobile proxy shape."""
    if not isinstance(probe, dict):
        return None
    streams = probe.get("streams")
    format_row = probe.get("format")
    if not isinstance(streams, list) or not isinstance(format_row, dict):
        return None
    format_names = {
        value.strip() for value in str(format_row.get("format_name") or "").split(",")
        if value.strip()
    }
    if not ({"mp4", "mov"} & format_names):
        return None
    videos = [
        row for row in streams
        if isinstance(row, dict) and row.get("codec_type") == "video"
    ]
    audios = [
        row for row in streams
        if isinstance(row, dict) and row.get("codec_type") == "audio"
    ]
    if len(videos) != 1 or len(audios) != 1:
        return None
    video = videos[0]
    audio = audios[0]
    try:
        width = int(video.get("width"))
        height = int(video.get("height"))
        duration = float(format_row.get("duration"))
        audio_bit_rate = int(audio.get("bit_rate"))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        isinstance(video.get("width"), bool)
        or isinstance(video.get("height"), bool)
        or video.get("codec_name") != "h264"
        or video.get("pix_fmt") != CANONICAL_REVIEW_PROXY_SETTINGS["pix_fmt"]
        or audio.get("codec_name") != "aac"
        or (width, height) != CANONICAL_REVIEW_PROXY_SIZE
        or duration <= 0
        or abs(audio_bit_rate - CANONICAL_REVIEW_AUDIO_BITRATE_BPS)
        > CANONICAL_REVIEW_AUDIO_BITRATE_TOLERANCE_BPS
    ):
        return None
    return {
        "width": width,
        "height": height,
        "duration": duration,
        "orientation": "portrait" if height > width else (
            "landscape" if width > height else "square"),
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_bit_rate": audio_bit_rate,
        "pix_fmt": CANONICAL_REVIEW_PROXY_SETTINGS["pix_fmt"],
        "video_stream_count": 1,
        "audio_stream_count": 1,
    }


def canonical_proxy_derivative_matches(
    final_info: object,
    proxy_info: object,
    *,
    duration_tolerance_seconds: float = 0.25,
) -> bool:
    if not isinstance(final_info, dict) or not isinstance(proxy_info, dict):
        return False
    try:
        final_width = int(final_info["width"])
        final_height = int(final_info["height"])
        final_duration = float(final_info["duration"])
        proxy_width = int(proxy_info["width"])
        proxy_height = int(proxy_info["height"])
        proxy_duration = float(proxy_info["duration"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if min(final_width, final_height, proxy_width, proxy_height) <= 0:
        return False
    final_orientation = (
        "portrait" if final_height > final_width else
        "landscape" if final_width > final_height else "square"
    )
    proxy_orientation = (
        "portrait" if proxy_height > proxy_width else
        "landscape" if proxy_width > proxy_height else "square"
    )
    aspect_delta = abs(
        (final_width / final_height) - (proxy_width / proxy_height))
    return (
        (final_width, final_height) == CANONICAL_REEL_FINAL_SIZE
        and (proxy_width, proxy_height) == CANONICAL_REVIEW_PROXY_SIZE
        and final_orientation == "portrait"
        and proxy_orientation == "portrait"
        and aspect_delta <= 0.000001
        and abs(final_duration - proxy_duration) <= duration_tolerance_seconds
    )


def canonical_proxy_has_faststart(path: Path) -> bool:
    """Require the top-level MP4 ``moov`` atom to precede ``mdat``.

    Searching raw encoded bytes for the four-character atom names is not a
    container check: H.264 payload bytes can contain the string ``moov``.  Walk
    only validated top-level ISO-BMFF atom headers so an embedded payload marker
    cannot masquerade as faststart evidence.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            file_size = handle.tell()
            offset = 0
            moov_offset = None
            mdat_offset = None
            while offset < file_size:
                if file_size - offset < 8:
                    return False
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    return False
                atom_size = int.from_bytes(header[:4], "big")
                atom_type = header[4:8]
                header_size = 8
                if atom_size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        return False
                    atom_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif atom_size == 0:
                    atom_size = file_size - offset
                if atom_size < header_size or offset + atom_size > file_size:
                    return False
                if atom_type == b"moov" and moov_offset is None:
                    moov_offset = offset
                elif atom_type == b"mdat" and mdat_offset is None:
                    mdat_offset = offset
                if moov_offset is not None and mdat_offset is not None:
                    return moov_offset < mdat_offset
                offset += atom_size
    except OSError:
        return False
    return False
