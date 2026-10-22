#!/usr/bin/env python3
"""Small local workflow launcher for this ComfyUI workspace."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import heapq
import hmac
import importlib.util
import json
import math
import mimetypes
import os
import random
import re
import secrets
import signal
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree
import zipfile
import types

import library_service
import workflow_library
import material_collector

import orchestrator as castle_orchestrator
from media_projects import (
    MAX_BRIEF_BYTES,
    MAX_PROJECT_RUN_REQUEST_BYTES,
    MAX_PREVIEW_REQUEST_BYTES,
    ProjectQualificationError,
    ProjectRunError,
    PreviewLimitError,
    PreviewPackError,
    PreviewRequestError,
    bind_canonical_brief_characters,
    create_project_run_api,
    compile_brief_request,
    compile_storyboard_route_review,
    compile_director_plan,
    compile_pack_proposal,
    compile_formal_production_pack,
    resolve_formal_pack_evidence,
    production_usage_scope,
    ProductionStateStore,
    ProductionStoreConflict,
    create_production_api,
    get_production_api,
    list_productions_api,
    resume_production_api,
    list_production_intents_api,
    list_production_output_media_api,
    resolve_production_output_media,
    mutate_production_api,
    prepare_production_shot_api,
    production_shot_preparation_options,
    quote_prepared_production_shot_api,
    prepare_rejected_shot_repair_api,
    execute_production_edit_api,
    resolve_production_contact_sheet,
    resolve_production_edit_output,
    review_production_delivery_api,
    build_delivery_package,
    CharacterRightsStore,
    get_character_rights_api,
    put_character_rights_api,
    revoke_character_rights_api,
    SeriesContinuityConflict,
    SeriesContinuityStore,
    append_series_episode_api,
    create_series_api,
    get_series_api,
    get_project_run_api,
    preview_production_pack_request,
)
from provider_fabric.runtime import ProviderRuntimeConfig, get_provider_runtime
from provider_fabric.media_materializer import MediaMaterializerError
from provider_fabric.store import default_database_path as provider_database_path
from provider_fabric.webhook import MAX_WEBHOOK_BYTES
from provider_fabric.media_source import resolve_owned_media_source
from provider_fabric.profile_rendition import (
    ProfileRenditionError,
    ProfileRenditionLimits,
    render_profile_rendition,
)
from provider_fabric.autoip_profiles import candidate_autoip_profiles
from provider_fabric.h3_profiles import h3_character_profiles
from provider_fabric.asset_handoff import prepare_asset_handoff
from provider_fabric.local_history_adoption import adopt_local_history_output
from provider_fabric.models import CharacterRef, VisualClass
from minimax_h3_product import (
    H3_SEED_MAX,
    MiniMaxH3ProductError,
    character_variant as minimax_h3_character_variant,
    compile_product_prompt as compile_minimax_h3_product_prompt,
    feature_definitions as minimax_h3_feature_definitions,
)
from minimax_h3_web_executor import (
    WebGraphExecutionError,
    execute_web_graph as execute_minimax_h3_web_graph,
)
from minimax_h3_readiness import (
    DEFAULT_GOLDEN_RECORD,
    H3_CONTRACTS,
    MiniMaxH3ReadinessConfig,
    audit_minimax_h3_readiness,
)
from qwen_image21 import (
    MODEL_BYTES as QWEN21_MODEL_BYTES,
    compile_prompt as compile_qwen21_prompt,
    feature_definitions as qwen21_feature_definitions,
)


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
AUTH_PATH = Path(os.environ.get("CASTLE_COMFY_AUTH_PATH", ROOT / ".auth.json"))
PROVENANCE_KEY_PATH = Path(os.environ.get("CASTLE_PROVENANCE_KEY_PATH", ROOT / ".provenance.key"))
USERS_PATH = Path(os.environ.get("CASTLE_COMFY_USERS_PATH", ROOT / "users.json"))
ORCH_SERVICE_TOKEN_PATH = (
    Path(os.environ["CASTLE_ORCH_SERVICE_TOKEN_PATH"]).expanduser()
    if os.environ.get("CASTLE_ORCH_SERVICE_TOKEN_PATH")
    else None
)
PRESENTER_VOICE_TOKEN_PATH = Path(os.environ.get(
    "CASTLE_PRESENTER_VOICE_TOKEN_PATH",
    Path.home() / ".config/web-comfyui/presenter_voice_token",
)).expanduser()
DEFAULT_COMFY = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
CLIENT_ID = "castle-comfy-workflow-web"
SESSION_COOKIE = "castle_comfy_session"
AUTH_ITERATIONS = 260_000
SESSION_TTL_ADMIN_SECONDS = 60 * 60 * 24 * 30
SESSION_TTL_USER_SECONDS = 60 * 60 * 24 * 7
SESSION_TTL_SECONDS = SESSION_TTL_ADMIN_SECONDS
_CASTLE_DATA_HOME = Path(
    os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
).expanduser()
SESSION_STORE_PATH = Path(os.environ.get(
    "CASTLE_COMFY_SESSION_STORE_PATH",
    _CASTLE_DATA_HOME / "web-comfyui/auth/sessions.json",
)).expanduser()
SESSION_STORE_MAX_RECORDS = min(
    4096,
    max(16, int(os.environ.get("CASTLE_COMFY_SESSION_MAX_RECORDS", "512"))),
)
SESSION_STORE_MAX_BYTES = 2 * 1024 * 1024
SESSION_STORE_VERSION = 1
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CASTLE_ACCESS_TEAM_DOMAIN = os.environ.get("CASTLE_ACCESS_TEAM_DOMAIN", "").strip()
LOCAL_SESSION_USER = {"email": "local@castle.internal", "role": "admin", "displayName": "本機管理"}
LEGACY_SESSION_USER = {"email": "legacy@castle.internal", "role": "admin", "displayName": "傳統登入"}
SYNTHETIC_SESSION_EMAILS = {LOCAL_SESSION_USER["email"], LEGACY_SESSION_USER["email"]}
CANONICAL_WEB_ORIGIN = os.environ.get("CASTLE_CANONICAL_WEB_ORIGIN", "").strip().rstrip("/")
LEGACY_WEB_HOSTS = {host.strip().lower() for host in os.environ.get("CASTLE_LEGACY_WEB_HOSTS", "").split(",") if host.strip()}
OPERATOR_EMAIL = os.environ.get("CASTLE_OPERATOR_EMAIL", "").strip().lower()
PROJECT_DAG_ADMIN_ENABLED = str(
    os.environ.get("CASTLE_PROJECT_DAG_ADMIN", "")
).strip().lower() in {"1", "true", "yes", "on"}
ALLOWED_ORIGIN_HOSTS = {"127.0.0.1:8190", "localhost:8190"} | {host.strip().lower() for host in os.environ.get("CASTLE_ALLOWED_ORIGIN_HOSTS", "").split(",") if host.strip()}
# sha256(cookie token) -> {expires, created, email, role, displayName}.
# Raw bearer tokens are deliberately never persisted or kept as dictionary keys.
SESSIONS: dict[str, dict | float] = {}
COMFY_INPUT = Path(os.environ.get("COMFYUI_INPUT_DIR", Path.home() / "comfyui/input"))
COMFY_OUTPUT = Path(os.environ.get("COMFYUI_OUTPUT_DIR", Path.home() / "comfyui/output"))
COMFY_TEMP = Path(os.environ.get("COMFYUI_TEMP_DIR", Path.home() / "comfyui/temp"))
VOICEOVER_ROOT = COMFY_OUTPUT / "Castle/voiceover"
VOICEOVER_BATCH_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{4}$")
VOICEOVER_SEGMENT_FILE_RE = re.compile(r"^seg_\d{2,3}\.wav$")
PRESENTER_VOICE_REQUEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{7,79}$")
VOICEOVER_MAX_PPTX_BYTES = 40 * 1024 * 1024
VOICEOVER_MAX_XML_MEMBER_BYTES = 5 * 1024 * 1024
_VOICEOVER_LOCK = threading.Lock()
# ComfyUI /view buckets -> the on-disk directory each one serves. Used to resolve
# a gallery item back to its file for existence checks + deletion.
VIEW_DIRS = {"output": COMFY_OUTPUT, "input": COMFY_INPUT, "temp": COMFY_TEMP}
GALLERY_MEDIA_KINDS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".mkv": "video",
    ".wav": "audio",
    ".mp3": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    ".oga": "audio",
    ".aac": "audio",
}

AUDIO_EXPORT_FORMATS = {
    "wav": {"extension": ".wav", "contentType": "audio/wav"},
    "mp3": {"extension": ".mp3", "contentType": "audio/mpeg"},
    "mp4": {"extension": ".mp4", "contentType": "video/mp4"},
}
AUDIO_EXPORT_CACHE_ROOT = Path(os.environ.get(
    "CASTLE_AUDIO_EXPORT_CACHE_DIR",
    Path.home() / ".cache/web-comfyui/audio-exports",
))
AUDIO_EXPORT_CACHE_VERSION = "v2"
AUDIO_EXPORT_TIMEOUT_SECONDS = int(os.environ.get("CASTLE_AUDIO_EXPORT_TIMEOUT_SECONDS", "300"))
AUDIO_EXPORT_MAX_CONCURRENT = max(1, int(os.environ.get("CASTLE_AUDIO_EXPORT_MAX_CONCURRENT", "2")))
AUDIO_EXPORT_MAX_ACTIVE_REQUESTS = max(
    AUDIO_EXPORT_MAX_CONCURRENT,
    int(os.environ.get("CASTLE_AUDIO_EXPORT_MAX_ACTIVE_REQUESTS", str(AUDIO_EXPORT_MAX_CONCURRENT * 2))),
)
AUDIO_EXPORT_VIDEO_THREADS = max(1, int(os.environ.get("CASTLE_AUDIO_EXPORT_VIDEO_THREADS", "2")))
AUDIO_EXPORT_MAX_SOURCE_BYTES = max(
    1,
    int(os.environ.get("CASTLE_AUDIO_EXPORT_MAX_SOURCE_BYTES", str(2 * 1024 * 1024 * 1024))),
)
AUDIO_EXPORT_MAX_FILE_BYTES = max(
    1,
    int(os.environ.get("CASTLE_AUDIO_EXPORT_MAX_FILE_BYTES", str(1024 * 1024 * 1024))),
)
AUDIO_EXPORT_MAX_DURATION_SECONDS = max(
    1,
    int(os.environ.get("CASTLE_AUDIO_EXPORT_MAX_DURATION_SECONDS", str(4 * 60 * 60))),
)
AUDIO_EXPORT_CACHE_MAX_FILES = int(os.environ.get("CASTLE_AUDIO_EXPORT_CACHE_MAX_FILES", "384"))
AUDIO_EXPORT_CACHE_MAX_BYTES = max(
    1,
    int(os.environ.get("CASTLE_AUDIO_EXPORT_CACHE_MAX_BYTES", str(4 * 1024 * 1024 * 1024))),
)
AUDIO_EXPORT_CACHE_MAX_AGE_SECONDS = int(os.environ.get("CASTLE_AUDIO_EXPORT_CACHE_MAX_AGE_SECONDS", str(7 * 24 * 60 * 60)))
AUDIO_EXPORT_TEMP_MAX_AGE_SECONDS = int(os.environ.get("CASTLE_AUDIO_EXPORT_TEMP_MAX_AGE_SECONDS", "3600"))
AUDIO_EXPORT_CACHE_FILE_RE = re.compile(r"^[0-9a-f]{64}\.(?:wav|mp3|mp4)$")
AUDIO_EXPORT_TEMP_FILE_RE = re.compile(r"^\.[0-9a-f]{64}\.[^.]+\.(?:wav|mp3|mp4)$")
_AUDIO_EXPORT_LOCK = threading.Lock()
_AUDIO_EXPORT_KEY_LOCKS: dict[str, threading.Lock] = {}
_AUDIO_EXPORT_SLOTS = threading.BoundedSemaphore(AUDIO_EXPORT_MAX_CONCURRENT)
_AUDIO_EXPORT_REQUEST_SLOTS = threading.BoundedSemaphore(AUDIO_EXPORT_MAX_ACTIVE_REQUESTS)


class AudioExportBusy(RuntimeError):
    """Raised when the bounded export pool is already occupied."""


def gallery_media_kind(path: Path) -> str | None:
    """Classify media by extension, with animation-aware WebP handling."""
    kind = GALLERY_MEDIA_KINDS.get(path.suffix.lower())
    if path.suffix.lower() != ".webp":
        return kind
    try:
        with path.open("rb") as handle:
            header = handle.read(4 * 1024 * 1024)
    except OSError:
        return kind
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP" and (b"ANIM" in header or b"ANMF" in header):
        return "video"
    return kind


def gallery_render_kind(path: Path, media_kind: str | None = None) -> str | None:
    """Return the browser element family without changing gallery filtering.

    Animated WebP belongs in the video collection, but browsers render it with
    an ``img`` element rather than ``video``.
    """
    kind = media_kind or gallery_media_kind(path)
    if path.suffix.lower() == ".webp":
        return "image"
    return kind


def audio_export_urls(base_params: dict, media_kind: str | None) -> dict[str, str]:
    """Return authenticated export URLs only for server-classified audio."""
    if media_kind != "audio" or str(base_params.get("type") or "output") != "output":
        return {}
    return {
        output_format: "/api/audio-export?" + urllib.parse.urlencode({
            **base_params,
            "format": output_format,
        })
        for output_format in AUDIO_EXPORT_FORMATS
    }


def audio_export_filename(source_name: str, output_format: str) -> str:
    spec = AUDIO_EXPORT_FORMATS.get(str(output_format or "").lower())
    if spec is None:
        raise ValueError("不支援的語音下載格式")
    stem = Path(source_name).stem.strip() or "castle-voice"
    return f"{stem}{spec['extension']}"


def audio_export_ffmpeg_args(
    ffmpeg: str,
    source: Path,
    target: Path,
    output_format: str,
    duration_seconds: float | None = None,
) -> list[str]:
    common = [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    size_limit = ["-fs", str(AUDIO_EXPORT_MAX_FILE_BYTES)]
    if output_format == "wav":
        return [
            *common,
            "-map", "0:a:0",
            "-map_metadata", "-1",
            "-vn",
            "-c:a", "pcm_s16le",
            *size_limit,
            str(target),
        ]
    if output_format == "mp3":
        return [
            *common,
            "-map", "0:a:0",
            "-map_metadata", "-1",
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            "-id3v2_version", "3",
            *size_limit,
            str(target),
        ]
    if output_format == "mp4":
        if duration_seconds is None or not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("無法確認原始語音長度")
        duration_text = f"{duration_seconds:.6f}"
        # A real video track is intentional. Many uploaders reject audio-only
        # MPEG-4 files even when their extension is .mp4; a compressible black
        # H.264 canvas keeps the file small while making it broadly usable.
        # A finite, probed duration avoids x264 read-ahead creating a long tail.
        return [
            *common,
            "-f", "lavfi",
            "-i", f"color=c=black:s=1280x720:r=25:d={duration_text}",
            "-map", "1:v:0",
            "-map", "0:a:0",
            "-map_metadata", "-1",
            "-c:v", "libx264",
            "-threads:v", str(AUDIO_EXPORT_VIDEO_THREADS),
            "-preset", "veryfast",
            "-tune", "stillimage",
            "-crf", "30",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-profile:a", "aac_low",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-shortest",
            "-t", duration_text,
            "-movflags", "+faststart",
            *size_limit,
            str(target),
        ]
    raise ValueError("不支援的語音下載格式")


def probe_audio_duration_seconds(source: Path, pass_fds: tuple[int, ...] = ()) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("伺服器尚未安裝語音檢測元件")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=duration:format=duration",
                "-of", "json",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=min(30, AUDIO_EXPORT_TIMEOUT_SECONDS),
            pass_fds=pass_fds,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("讀取語音長度逾時，請稍後再試") from error
    except OSError as error:
        raise RuntimeError("伺服器無法啟動語音檢測元件") from error
    if result.returncode != 0:
        raise ValueError("無法讀取這個語音檔的長度")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("無法讀取這個語音檔的長度") from error
    raw_values = [
        *((stream or {}).get("duration") for stream in payload.get("streams", []) if isinstance(stream, dict)),
        (payload.get("format") or {}).get("duration"),
    ]
    durations = []
    for raw in raw_values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            durations.append(value)
    if not durations:
        raise ValueError("無法讀取這個語音檔的長度")
    duration = durations[0]
    if duration > AUDIO_EXPORT_MAX_DURATION_SECONDS:
        raise ValueError("語音太長，超過可轉檔的安全上限")
    return duration


def cleanup_audio_export_cache(keep: Path | None = None) -> None:
    try:
        entries = list(AUDIO_EXPORT_CACHE_ROOT.iterdir())
    except OSError:
        return
    now = time.time()
    stamped = []
    for path in entries:
        if not path.is_file() or path.is_symlink():
            continue
        if AUDIO_EXPORT_TEMP_FILE_RE.fullmatch(path.name):
            try:
                if now - path.stat().st_mtime > AUDIO_EXPORT_TEMP_MAX_AGE_SECONDS:
                    path.unlink()
            except OSError:
                pass
            continue
        if not AUDIO_EXPORT_CACHE_FILE_RE.fullmatch(path.name):
            continue
        try:
            stat = path.stat()
            stamped.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            pass
    stamped.sort(key=lambda item: item[0], reverse=True)
    kept_files = 0
    kept_bytes = 0
    for mtime, size, path in stamped:
        preserve = keep is not None and path == keep
        try:
            too_old = now - mtime > AUDIO_EXPORT_CACHE_MAX_AGE_SECONDS
            over_count = kept_files >= max(1, AUDIO_EXPORT_CACHE_MAX_FILES)
            over_bytes = kept_bytes + size > AUDIO_EXPORT_CACHE_MAX_BYTES
            if not preserve and (too_old or over_count or over_bytes):
                path.unlink()
                continue
        except OSError:
            continue
        kept_files += 1
        kept_bytes += size


def ensure_audio_export(source: Path, output_format: str, source_handle=None) -> Path:
    output_format = str(output_format or "").strip().lower()
    spec = AUDIO_EXPORT_FORMATS.get(output_format)
    if spec is None:
        raise ValueError("下載格式只支援 WAV、MP3 或 MP4")
    if gallery_media_kind(source) != "audio":
        raise ValueError("這個作品不是可轉出的語音檔")
    owned_handle = None
    if source_handle is None:
        try:
            owned_handle = source.open("rb")
        except OSError as error:
            raise ValueError("找不到原始語音檔") from error
        source_handle = owned_handle
    try:
        return _ensure_audio_export_from_handle(source, output_format, spec, source_handle)
    finally:
        if owned_handle is not None:
            owned_handle.close()


def _ensure_audio_export_from_handle(source: Path, output_format: str, spec: dict, source_handle) -> Path:
    try:
        source_fd = source_handle.fileno()
        stat = os.fstat(source_fd)
    except OSError as error:
        raise ValueError("找不到原始語音檔") from error
    if stat.st_size > AUDIO_EXPORT_MAX_SOURCE_BYTES:
        raise ValueError("原始語音檔太大，無法安全轉檔")
    identity = "\n".join((
        AUDIO_EXPORT_CACHE_VERSION,
        str(source.resolve()),
        str(stat.st_size),
        str(stat.st_mtime_ns),
        str(stat.st_ctime_ns),
        str(stat.st_ino),
        output_format,
    ))
    cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    target = AUDIO_EXPORT_CACHE_ROOT / f"{cache_key}{spec['extension']}"
    try:
        if target.is_file() and target.stat().st_size > 0:
            try:
                os.utime(target, None)
            except OSError:
                pass
            return target
    except OSError:
        pass
    if not _AUDIO_EXPORT_REQUEST_SLOTS.acquire(blocking=False):
        raise AudioExportBusy("目前同時轉檔的人較多，請稍後再按一次")
    cache_lock = None
    cache_lock_acquired = False
    conversion_slot_acquired = False
    try:
        with _AUDIO_EXPORT_LOCK:
            cache_lock = _AUDIO_EXPORT_KEY_LOCKS.setdefault(cache_key, threading.Lock())
            cache_lock_acquired = cache_lock.acquire(blocking=False)
            lock_limit = max(64, AUDIO_EXPORT_CACHE_MAX_FILES * len(AUDIO_EXPORT_FORMATS) * 2)
            if len(_AUDIO_EXPORT_KEY_LOCKS) > lock_limit:
                for old_key, old_lock in list(_AUDIO_EXPORT_KEY_LOCKS.items()):
                    if old_key != cache_key and not old_lock.locked():
                        _AUDIO_EXPORT_KEY_LOCKS.pop(old_key, None)
                    if len(_AUDIO_EXPORT_KEY_LOCKS) <= lock_limit // 2:
                        break
        if not cache_lock_acquired:
            raise AudioExportBusy("這個語音正在準備中，請稍後再按一次")
        try:
            if target.is_file() and target.stat().st_size > 0:
                try:
                    os.utime(target, None)
                except OSError:
                    pass
                return target
        except OSError:
            pass
        conversion_slot_acquired = _AUDIO_EXPORT_SLOTS.acquire(blocking=False)
        if not conversion_slot_acquired:
            raise AudioExportBusy("轉檔服務正在忙碌，請稍後再按一次")
        AUDIO_EXPORT_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            AUDIO_EXPORT_CACHE_ROOT.chmod(0o700)
        except OSError:
            pass
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{cache_key}.",
            suffix=spec["extension"],
            dir=AUDIO_EXPORT_CACHE_ROOT,
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("伺服器尚未安裝語音轉檔元件")
            stable_source = Path(f"/proc/self/fd/{source_fd}")
            duration_seconds = probe_audio_duration_seconds(stable_source, (source_fd,))
            try:
                result = subprocess.run(
                    audio_export_ffmpeg_args(
                        ffmpeg,
                        stable_source,
                        temporary,
                        output_format,
                        duration_seconds,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=AUDIO_EXPORT_TIMEOUT_SECONDS,
                    pass_fds=(source_fd,),
                )
            except subprocess.TimeoutExpired as error:
                raise ValueError("語音轉檔逾時，請稍後再試") from error
            except OSError as error:
                raise RuntimeError("伺服器無法啟動語音轉檔元件") from error
            if result.returncode != 0:
                raise ValueError("這個語音檔目前無法轉成所選格式")
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise ValueError("語音轉檔沒有產生可用檔案")
            if temporary.stat().st_size >= AUDIO_EXPORT_MAX_FILE_BYTES:
                raise ValueError("語音太長，轉檔大小超過安全上限")
            temporary.replace(target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
        cleanup_audio_export_cache(target)
        return target
    finally:
        if conversion_slot_acquired:
            _AUDIO_EXPORT_SLOTS.release()
        if cache_lock_acquired and cache_lock is not None:
            cache_lock.release()
        _AUDIO_EXPORT_REQUEST_SLOTS.release()


# Gallery items the operator chose to hide. Persisted locally (never deployed);
# the file just stores {type,subfolder,filename} tuples, no content.
HIDDEN_PATH = Path(os.environ.get("CASTLE_HIDDEN_PATH", ROOT / ".hidden.json"))
_HIDDEN_LOCK = threading.Lock()
_GALLERY_MUTATION_LOCK = threading.RLock()

FEATURE_OUTPUT_PREFIXES = {
    "text2img": "Castle/text2img_ZImageTurbo",
    "img2img": "Castle/img2img_FluxKontext",
    "anime-scene-card": "Castle/anime-scene-card",
    "image-blend": "Castle/image-blend_IPAdapter",
    "character-angles": "Castle/character-angles_InstantID",
    "clipmode-character-photoshoot": "Castle/clipmode-character-photoshoot_SDXL",
    "img2video": "Castle/img2video_WanI2V",
    "first-last-frame": "Castle/first-last-frame_WanFLF",
    "dance": "Castle/dance_SCAIL",
    "lipsync": "Castle/lipsync_Wav2Lip",
    "face-swap": "Castle/face-swap_ReActor",
    "video-loop": "Castle/video-loop_VHS",
    "voice-speak": "Castle/voice-speak_BreezyVoice",
    "voice-clone": "Castle/voice-clone_TTS",
    "voxcpm-tts": "Castle/voxcpm_VoxCPM2",
    "h3-text-video": "Castle/minimax-h3/t2v",
    "h3-image-video": "Castle/minimax-h3/i2v",
    "h3-reference-video": "Castle/minimax-h3/ref2va_image",
}

# 方式二：從網址下載 — reuse the local castle_fb_crawl yt-dlp setup (cookies per
# site) to pull a reference clip straight into ComfyUI's input dir. LOCAL ONLY:
# yt-dlp runs as an argv list (never a shell), only http/https URLs are accepted,
# downloads are size- and time-capped, and the saved file is a bare name inside
# COMFY_INPUT — identical to an uploaded clip from there on. The endpoint is
# password-gated like everything else on this public tunnel.
CRAWL_DIR = Path(os.environ.get("CASTLE_CRAWL_DIR", Path.home() / ".local/share/web-comfyui/integrations/crawler"))
CRAWL_COOKIES = CRAWL_DIR / "cookies"
CRAWL_VIDEO_ROOT = Path(os.environ.get("CASTLE_CRAWL_VIDEO_ROOT", CRAWL_DIR / "downloads"))
CRAWL_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
MAX_CRAWL_VIDEO_ITEMS = int(os.environ.get("CASTLE_CRAWL_VIDEO_MAX_ITEMS", "400"))
DANCE_TEMPLATE_ROOT = Path(os.environ.get("CASTLE_DANCE_TEMPLATE_ROOT", ROOT / "dance_templates")).expanduser()
DANCE_TEMPLATE_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
DANCE_LIBRARY_SCHEMA_VERSION = 1
DANCE_LIBRARY_CATALOG_PATH = Path(os.environ.get(
    "CASTLE_DANCE_LIBRARY_CATALOG",
    Path.home() / ".local/share/web-comfyui/dance-library-v1/catalog.json",
)).expanduser()
DANCE_LIBRARY_CACHE_ROOT = Path(os.environ.get(
    "CASTLE_DANCE_LIBRARY_CACHE",
    Path.home() / ".cache/web-comfyui/dance-library-v1",
)).expanduser()
DANCE_LIBRARY_ID_RE = re.compile(r"^[a-f0-9]{24}$")
DANCE_LIBRARY_ASSET_ID_RE = re.compile(r"^[a-f0-9]{16}$")
DANCE_LIBRARY_STATUSES = {"approved", "pending", "hidden", "problem"}
DANCE_LIBRARY_CHARACTER_TYPES = {"anime", "realistic"}
DANCE_LIBRARY_DERIVATIVE_KINDS = {"storyboard", "preview", "runtime"}
DANCE_LIBRARY_FFMPEG_TIMEOUT_SECONDS = max(
    30,
    int(os.environ.get("CASTLE_DANCE_LIBRARY_FFMPEG_TIMEOUT_SECONDS", "600")),
)
DANCE_LIBRARY_MAX_CONCURRENT = max(
    1,
    int(os.environ.get("CASTLE_DANCE_LIBRARY_MAX_CONCURRENT", "2")),
)
DANCE_LIBRARY_PREPARE_WAIT_SECONDS = max(
    1,
    int(os.environ.get("CASTLE_DANCE_LIBRARY_PREPARE_WAIT_SECONDS", "45")),
)
DANCE_LIBRARY_CACHE_MAX_FILES = max(
    16,
    int(os.environ.get("CASTLE_DANCE_LIBRARY_CACHE_MAX_FILES", "512")),
)
DANCE_LIBRARY_CACHE_MAX_BYTES = max(
    1024 * 1024,
    int(os.environ.get("CASTLE_DANCE_LIBRARY_CACHE_MAX_BYTES", str(6 * 1024 * 1024 * 1024))),
)
DANCE_LIBRARY_CACHE_MAX_AGE_SECONDS = max(
    3600,
    int(os.environ.get("CASTLE_DANCE_LIBRARY_CACHE_MAX_AGE_SECONDS", str(30 * 24 * 60 * 60))),
)
_DANCE_LIBRARY_LOCK = threading.RLock()
_DANCE_LIBRARY_DERIVATIVE_GUARD = threading.Lock()
_DANCE_LIBRARY_DERIVATIVE_LOCKS: dict[str, threading.Lock] = {}
_DANCE_LIBRARY_DERIVATIVE_SLOTS = threading.BoundedSemaphore(DANCE_LIBRARY_MAX_CONCURRENT)
ORCH_ADOPT_ALLOWED_ROOTS = [
    COMFY_OUTPUT,
    COMFY_INPUT,
    COMFY_TEMP,
    CRAWL_VIDEO_ROOT,
    Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
]
if os.environ.get("CASTLE_ORCH_ADOPT_ALLOWED_ROOTS"):
    ORCH_ADOPT_ALLOWED_ROOTS.extend(
        Path(part).expanduser()
        for part in os.environ["CASTLE_ORCH_ADOPT_ALLOWED_ROOTS"].split(os.pathsep)
        if part.strip()
    )
GOOGLE_CLIENT_ID = os.environ.get("CASTLE_GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("CASTLE_GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get("CASTLE_GOOGLE_REDIRECT_URI", "").strip()
GOOGLE_TOKEN_PATH = Path(os.environ.get("CASTLE_GOOGLE_TOKEN_PATH", ROOT / ".google_drive_token.json"))
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_AUTH_STATES: dict[str, dict] = {}
_GOOGLE_LOCK = threading.Lock()
YT_DLP_CANDIDATES = [
    os.environ.get("CASTLE_YT_DLP", ""),
    str(Path.home() / ".local/bin/yt-dlp"),
    "/usr/local/bin/yt-dlp",
    "/usr/bin/yt-dlp",
]
MAX_FETCH_BYTES = 256 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 240

# Request body caps (bytes). Auth bodies are tiny; upload-bearing generate
# endpoints carry base64 data URLs so they need a much larger ceiling.
MAX_JSON_BODY = 4 * 1024
MAX_TEXT_BODY = 256 * 1024
MAX_UPLOAD_BODY = 96 * 1024 * 1024
MAX_CLIPMODE_PREVIEW_BODY = 64 * 1024 * 1024
ASSIST_MAX_REFERENCE_BYTES = 8 * 1024 * 1024
ASSIST_MAX_TOTAL_REFERENCE_BYTES = 14 * 1024 * 1024
ASSIST_MAX_REFERENCES = 3
PROVIDER_RUNTIME_CONFIG = ProviderRuntimeConfig(
    owner_import_enabled=True,
    # Castle is an owner-operated workstation and the only local endpoint is
    # the fixed loopback ComfyUI origin. Per-H3 technical readiness is still
    # rechecked from models and object-info before planning and execution.
    local_comfy_enabled=True,
    local_runtime_ready=True,
    autoip_webhook_account_id=os.environ.get("CASTLE_AUTOIP_WEBHOOK_ACCOUNT_ID", "local-autoip"),
    autoip_webhook_url=(
        os.environ.get("CASTLE_AUTOIP_WEBHOOK_URL", "").strip() or None
    ),
)
_PROVIDER_CANONICAL_CONTEXT = threading.local()
MAX_PROJECT_DRY_RUN_BODY = MAX_PREVIEW_REQUEST_BYTES
MAX_PROJECT_BRIEF_BODY = MAX_BRIEF_BYTES
MAX_PROJECT_RUN_BODY = MAX_PROJECT_RUN_REQUEST_BYTES
PRODUCTION_STATE_DB = Path(os.environ.get(
    "CASTLE_PRODUCTION_STATE_DB",
    provider_database_path().parent / "productions.sqlite3",
))
CHARACTER_RIGHTS_DB = Path(os.environ.get(
    "CASTLE_CHARACTER_RIGHTS_DB",
    provider_database_path().parent / "character-rights" / "rights.sqlite3",
))
SERIES_CONTINUITY_DB = Path(os.environ.get(
    "CASTLE_SERIES_CONTINUITY_DB",
    provider_database_path().parent / "series-continuity" / "series.sqlite3",
))
_PRODUCTION_STATE_STORE = None
_PRODUCTION_STATE_STORE_LOCK = threading.Lock()
_CHARACTER_RIGHTS_STORE = None
_CHARACTER_RIGHTS_STORE_LOCK = threading.Lock()
_SERIES_CONTINUITY_STORE = None
_SERIES_CONTINUITY_STORE_LOCK = threading.Lock()
MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_EMBEDDED_PROMPT_BYTES = 2 * 1024 * 1024
EMBEDDED_PROMPT_IMAGE_EXTENSIONS = {".png", ".webp"}
EMBEDDED_PROMPT_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
OUTPUT_PROVENANCE_SCHEMA = "castle.output-provenance.v1"
OUTPUT_PROVENANCE_AUTHORITY = "castle-server"
MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA = "castle.minimax-h3-output-provenance.v1"
MINIMAX_H3_WEB_JOB_KIND = "minimax_h3_web"
CLIPMODE_PHOTOSHOOT_JOB_KIND = "clipmode_character_photoshoot_web"
MAX_UPLOAD_DECODED = 64 * 1024 * 1024
MAX_VOICE_DECODED = 32 * 1024 * 1024
VOICE_TTS_CHUNK_MAX_CHARS = int(os.environ.get("CASTLE_VOICE_TTS_CHUNK_MAX_CHARS", "60"))
VOICE_TTS_CHUNK_MIN_CHARS = int(os.environ.get("CASTLE_VOICE_TTS_CHUNK_MIN_CHARS", "8"))
VOICE_TTS_CHUNK_MAX_WORDS = int(os.environ.get("CASTLE_VOICE_TTS_CHUNK_MAX_WORDS", "50"))
VOICE_TTS_CHUNK_MIN_WORDS = int(os.environ.get("CASTLE_VOICE_TTS_CHUNK_MIN_WORDS", "6"))
VOICE_EN_WORDS_PER_SECOND = float(os.environ.get("CASTLE_VOICE_EN_WORDS_PER_SECOND", "2.5"))
VOICE_SPEAK_MAX_CHARS = int(os.environ.get("CASTLE_VOICE_SPEAK_MAX_CHARS", "2400"))
VOICE_CLONE_MAX_CHARS = int(os.environ.get("CASTLE_VOICE_CLONE_MAX_CHARS", "2400"))
VOICE_SAMPLE_MAX_CHARS = int(os.environ.get("CASTLE_VOICE_SAMPLE_MAX_CHARS", "180"))
VOICE_SCRIPT_MAX_SECONDS = int(os.environ.get("CASTLE_VOICE_SCRIPT_MAX_SECONDS", "90"))
VOICE_BREATH_GROUP_MAX_CHARS = int(os.environ.get("CASTLE_VOICE_BREATH_GROUP_MAX_CHARS", "60"))
VOICE_BREATH_GROUP_MAX_COUNT = int(os.environ.get("CASTLE_VOICE_BREATH_GROUP_MAX_COUNT", "8"))
VOICE_MAX_TTS_CHUNKS = int(os.environ.get("CASTLE_VOICE_MAX_TTS_CHUNKS", "64"))
VOICE_CHUNK_PAUSE_SECONDS = float(os.environ.get("CASTLE_VOICE_CHUNK_PAUSE_SECONDS", "0.14"))
STREAM_CHUNK_SIZE = 1024 * 1024

# Warm BreezyVoice daemon (local only) — backs the 打字即說 feature. Persona
# data is provided by the installation owner and never bundled in this repo.
BREEZY_URL = os.environ.get("CASTLE_BREEZY_URL", "").strip().rstrip("/")
VOICE_LAB_PYTHON = Path(os.environ.get("CASTLE_VOICE_LAB_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python"))
GPU_QUEUE_SCRIPT = Path(os.environ.get("CASTLE_GPU_QUEUE_SCRIPT", Path.home() / ".local/share/web-comfyui/integrations/voice/gpu_queue.py"))
GPU_QUEUE_PYTHON = os.environ.get("CASTLE_GPU_QUEUE_PYTHON", "/usr/bin/python3")
H3_WEB_STATE_ROOT = Path(os.environ.get(
    "CASTLE_H3_WEB_STATE_ROOT",
    Path.home() / ".cache/web-comfyui/minimax-h3-web",
)).expanduser()
H3_WEB_WORKER_SCRIPT = ROOT.parent / "scripts/minimax_h3/run_web_graph.py"
H3_WEB_COMFY_DIR = Path(os.environ.get("CASTLE_H3_COMFY_DIR", Path.home() / "comfyui")).expanduser()
H3_WEB_TIMEOUT_SECONDS = max(
    60,
    min(86_400, int(os.environ.get("CASTLE_H3_WEB_TIMEOUT_SECONDS", "10800"))),
)
CLIPMODE_PHOTOSHOOT_STATE_ROOT = Path(os.environ.get(
    "CASTLE_CLIPMODE_PHOTOSHOOT_STATE_ROOT",
    Path.home() / ".cache/web-comfyui/clipmode-character-photoshoot",
)).expanduser()
CLIPMODE_PHOTOSHOOT_GENERATION_TIMEOUT_SECONDS = max(
    60,
    min(3_600, int(os.environ.get(
        "CASTLE_CLIPMODE_PHOTOSHOOT_GENERATION_TIMEOUT_SECONDS",
        os.environ.get("CASTLE_CLIPMODE_PHOTOSHOOT_TIMEOUT_SECONDS", "900"),
    ))),
)
CLIPMODE_PHOTOSHOOT_MIN_FREE_MB = max(
    1,
    int(os.environ.get("CASTLE_CLIPMODE_PHOTOSHOOT_MIN_FREE_MB", "26000")),
)
CLIPMODE_PHOTOSHOOT_MAX_UTIL = max(
    0,
    min(100, int(os.environ.get("CASTLE_CLIPMODE_PHOTOSHOOT_MAX_UTIL", "20"))),
)
CLIPMODE_PHOTOSHOOT_QUEUE_INTERVAL = max(
    1,
    int(os.environ.get("CASTLE_CLIPMODE_PHOTOSHOOT_QUEUE_INTERVAL", "20")),
)
CLIPMODE_PHOTOSHOOT_COMFY_URL = "http://127.0.0.1:8188"
CLIPMODE_PHOTOSHOOT_WORKER_TOKEN_ENV = "CASTLE_CLIPMODE_PHOTOSHOOT_WORKER_TOKEN"
CLIPMODE_PHOTOSHOOT_SPEC_SCHEMA = "castle.clipmode-photoshoot-worker-spec.v2"
CLIPMODE_PHOTOSHOOT_RECEIPT_SCHEMA = "castle.clipmode-photoshoot-worker-receipt.v2"
CLIPMODE_PHOTOSHOOT_QUALITY_SCHEMA = "castle.clipmode-photoshoot-quality.v1"
CLIPMODE_PHOTOSHOOT_MAX_STATE_BYTES = 128 * 1024
CLIPMODE_PHOTOSHOOT_MAX_ATTEMPTS = 3
CLIPMODE_PHOTOSHOOT_IDENTITY_THRESHOLD = 0.875
CLIPMODE_PHOTOSHOOT_QUALITY_PYTHON = Path(os.environ.get(
    "CASTLE_CLIPMODE_PHOTOSHOOT_QUALITY_PYTHON",
    Path.home() / "comfyui/.venv/bin/python",
)).expanduser()
CLIPMODE_PHOTOSHOOT_INSIGHTFACE_ROOT = Path(os.environ.get(
    "CASTLE_CLIPMODE_PHOTOSHOOT_INSIGHTFACE_ROOT",
    Path.home() / ".insightface",
)).expanduser()
CLIPMODE_PHOTOSHOOT_QUALITY_SCRIPT = (
    ROOT.parent / "scripts/evaluate_clipmode_photoshoot_quality.py"
)
CLIPMODE_PHOTOSHOOT_STAGING_FOLDER = ".quality-staging"
GUARDED_TTS_SCRIPT = ROOT / "guarded_breezy_tts.py"
GUARDED_JOB_DIR = Path(os.environ.get("CASTLE_GUARDED_JOB_DIR", Path.home() / ".cache/web-comfyui/guarded-voice"))
GUARDED_MIN_FREE_MB = int(os.environ.get("CASTLE_GUARDED_MIN_FREE_MB", "26000"))
GUARDED_MAX_UTIL = int(os.environ.get("CASTLE_GUARDED_MAX_UTIL", "20"))
GUARDED_QUEUE_INTERVAL = int(os.environ.get("CASTLE_GUARDED_QUEUE_INTERVAL", "20"))
GUARDED_TRIES = int(os.environ.get("CASTLE_GUARD_TRIES", "4"))
GUARDED_CER = float(os.environ.get("CASTLE_GUARD_CER", "0.22"))
GUARDED_WHISPER_MODEL = os.environ.get("CASTLE_GUARD_WHISPER_MODEL", "large-v3")
VOXCPM_ROOT = Path(os.environ.get("CASTLE_VOXCPM_ROOT", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/VoxCPM"))
VOXCPM_PYTHON = Path(os.environ.get("CASTLE_VOXCPM_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/.venv/bin/python"))
VOXCPM_MODEL_PATH = Path(os.environ.get("CASTLE_VOXCPM_MODEL_PATH", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/models/VoxCPM2"))
VOXCPM_SCRIPT = ROOT / "voxcpm_tts.py"
VOXCPM_SPEAKER_SCRIPT = ROOT / "speaker_similarity.py"
VOXCPM_SPEAKER_THRESHOLD = float(os.environ.get("CASTLE_VOXCPM_SPEAKER_THRESHOLD", "0.82"))
VOXCPM_SPEAKER_ATTEMPTS = int(os.environ.get("CASTLE_VOXCPM_SPEAKER_ATTEMPTS", "2"))
CUSTOM_ENGLISH_LORA = Path(os.environ.get(
    "CASTLE_CUSTOM_ENGLISH_LORA",
    Path.home() / ".local/share/web-comfyui/integrations/voice/profiles/custom-english/lora",
))
CUSTOM_ENGLISH_ULTRACLEAN_LORA = Path(os.environ.get(
    "CASTLE_CUSTOM_ENGLISH_ULTRACLEAN_LORA",
    Path.home() / ".local/share/web-comfyui/integrations/voice/profiles/custom-english/alternate-lora",
))
CUSTOM_ENGLISH_REFERENCE_AUDIO = Path(os.environ.get(
    "CASTLE_CUSTOM_ENGLISH_REFERENCE_AUDIO",
    Path.home() / ".local/share/web-comfyui/integrations/voice/profiles/custom-english/reference.wav",
))
CUSTOM_ENGLISH_REFERENCE_TEXT = os.environ.get("CASTLE_CUSTOM_ENGLISH_REFERENCE_TEXT", "")
LOCAL_JOB_DONE_TTL = int(os.environ.get("CASTLE_LOCAL_JOB_DONE_TTL", "90"))
LOCAL_JOBS: dict[str, dict] = {}
_LOCAL_JOB_LOCK = threading.Lock()

# 貼文生成器 (post-generator): the four ai-sister personas write social posts.
# API credentials come from the process environment or an explicitly configured file.
# API calls remain password-gated. Generic Studio may use the clearly labelled local-Claude style
# fallback; character capabilities fail closed unless their own provider is live.
VOICE_LAB_ENV = (Path(os.environ["CASTLE_VOICE_LAB_ENV"]).expanduser() if os.environ.get("CASTLE_VOICE_LAB_ENV") else None)
TTS_NORMALIZER_PATH = Path(os.environ.get("CASTLE_TTS_NORMALIZER", Path.home() / ".local/share/web-comfyui/integrations/voice/tts_normalize.py"))
CLAUDE_CLI = os.environ.get("CASTLE_CLAUDE_CLI", "/usr/local/bin/claude")
POST_GEMINI_MODEL = os.environ.get("CASTLE_POST_GEMINI_MODEL", "gemini-2.5-flash")
POST_OPENAI_MODEL = os.environ.get("CASTLE_POST_OPENAI_MODEL", "gpt-4o-mini")
_LAB_SECRETS: dict[str, str] | None = None
_TTS_NORMALIZER = None


def _lab_secret(name: str) -> str | None:
    """Use process credentials, or an explicitly configured local secrets file."""
    value = os.environ.get(name)
    if value:
        return value
    if VOICE_LAB_ENV is None:
        return None
    global _LAB_SECRETS
    if _LAB_SECRETS is None:
        secrets_map: dict[str, str] = {}
        try:
            for line in VOICE_LAB_ENV.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                secrets_map[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            pass
        _LAB_SECRETS = secrets_map
    return _LAB_SECRETS.get(name) or None


# Voice library: reference audio managed locally for future voice cloning.
VOICE_LIBRARY = Path(os.environ.get("CASTLE_VOICE_DIR", ROOT / "voices"))
VOICE_INDEX = VOICE_LIBRARY / "index.json"
VOICE_ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".flac", ".webm", ".aac"}
_VOICE_LOCK = threading.Lock()
_BREEZY_PERSONA_LOCK = threading.Lock()
_BREEZY_PERSONA_CACHE: dict[str, object] = {"ts": 0.0, "value": None}
BREEZY_PERSONA_CACHE_SECONDS = float(os.environ.get("CASTLE_BREEZY_PERSONA_CACHE_SECONDS", "10"))
CHARACTER_LIBRARY = Path(os.environ.get("CASTLE_CHARACTER_DIR", ROOT / "characters"))
CHARACTER_INDEX = CHARACTER_LIBRARY / "index.json"
CHARACTER_POSTS = CHARACTER_LIBRARY / "posts.json"
JOB_INDEX_PATH = CHARACTER_LIBRARY / ".job_index.json"
_CHARACTER_LOCK = threading.Lock()
_CHARACTER_POST_LOCK = threading.Lock()
_WARDROBE_LOCK = threading.Lock()
_JOB_INDEX_LOCK = threading.RLock()
_JOB_INDEX_CACHE: dict[str, object] = {"mtime": None, "jobs": None}
_PROVENANCE_KEY_LOCK = threading.RLock()
_PROVENANCE_KEY_CACHE: dict[str, object] = {"path": "", "key": None}
_PROVENANCE_HASH_LOCK = threading.Lock()
_PROVENANCE_HASH_CACHE: dict[tuple, str] = {}
_PROMPT_OUTPUTS_CACHE_LOCK = threading.Lock()
_PROMPT_OUTPUTS_CACHE: dict[str, dict] = {}
_PROMPT_OUTPUTS_CACHE_TTL = 60.0
_PROMPT_OUTPUTS_CACHE_MAX = 256
CHARACTER_LOCAL_ASSET_ROOT = CHARACTER_LIBRARY / "assets"
CHARACTER_USER_ASSET_ROOT = CHARACTER_LIBRARY / "users"
CHARACTER_AI_PUBLIC_ROOT = Path(os.environ.get("CASTLE_CHARACTER_PUBLIC_ROOT", CHARACTER_LOCAL_ASSET_ROOT / "public")).expanduser()
CHARACTER_PERSONA_REFS_ROOT = Path(os.environ.get("CASTLE_CHARACTER_PERSONA_REFS_ROOT", CHARACTER_LOCAL_ASSET_ROOT / "references")).expanduser()
CHARACTER_PORTRAIT_ROOT = Path(os.environ.get("CASTLE_CHARACTER_PORTRAIT_ROOT", CHARACTER_LOCAL_ASSET_ROOT / "portraits")).expanduser()
CHARACTER_ASSET_ALLOWED_ROOTS = [
    CHARACTER_LOCAL_ASSET_ROOT,
    CHARACTER_USER_ASSET_ROOT,
    CHARACTER_AI_PUBLIC_ROOT,
    CHARACTER_PERSONA_REFS_ROOT,
    CHARACTER_PORTRAIT_ROOT,
]
if os.environ.get("CASTLE_CHARACTER_ASSET_ROOTS"):
    CHARACTER_ASSET_ALLOWED_ROOTS.extend(
        Path(part).expanduser()
        for part in os.environ["CASTLE_CHARACTER_ASSET_ROOTS"].split(os.pathsep)
        if part.strip()
    )
CHARACTER_ASSET_KINDS = {"avatar", "card", "expressionSheet"}
CHARACTER_TYPES = {"anime", "realistic", "flux", "experimental"}
CHARACTER_VISUAL_CLASSES = {"anime", "realistic", "illustration", "unknown"}
EXPERIMENTAL_MAX_PER_USER = 5
EXPERIMENTAL_MAX_FILE_BYTES = 15 * 1024 * 1024
EXPERIMENTAL_MAX_TOTAL_BYTES = 200 * 1024 * 1024
EXPERIMENTAL_ALLOWED_EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
DANCE_TEMPLATE_THUMB_ROOT = CHARACTER_LIBRARY / ".thumbs" / "dance_templates"
WARDROBE_ROOT = Path(os.environ.get("CASTLE_WARDROBE_ROOT", Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp/tachie")).expanduser()
WARDROBE_APPROVAL_PATH = Path(
    os.environ.get("CASTLE_WARDROBE_APPROVAL", WARDROBE_ROOT.parent / "wardrobe_approval.json")
).expanduser()
WARDROBE_THUMB_ROOT = CHARACTER_LIBRARY / ".thumbs"
WARDROBE_CACHE_SECONDS = int(os.environ.get("CASTLE_WARDROBE_CACHE_SECONDS", "3600"))
WARDROBE_REFERENCE_MAX_ITEMS = 50
WARDROBE_REFERENCE_MAX_FILE_BYTES = 15 * 1024 * 1024
WARDROBE_REFERENCE_MAX_TOTAL_BYTES = 200 * 1024 * 1024
WARDROBE_THEME_LABELS = {
    "tech": "科技",
    "finance": "金融",
    "politics": "政治",
    "education": "教育",
    "health": "健康",
    "environment": "環境",
    "law": "法律",
    "relationship": "感情",
    "family": "家庭",
    "workplace": "職場",
    "science": "科學",
    "culture": "文化",
    "sports": "運動",
    "food": "美食",
    "travel": "旅遊",
    "psychology": "心理",
    "philosophy": "哲學",
    "international": "國際",
    "media": "媒體",
    "festival": "節慶",
}
WARDROBE_THEMES = tuple(WARDROBE_THEME_LABELS.keys())
WARDROBE_POSES = ("supported", "challenged", "victory")
DEFAULT_WARDROBE_ALIASES = {"llama": "venice"}
ANIME_SCENE_ROOT = Path(
    os.environ.get("CASTLE_ANIME_SCENE_ROOT", WARDROBE_ROOT / "scenes")
).expanduser()
# Start with a small, visually reviewed set. More of the 20 local scene assets
# can be promoted here after their thumbnails and composites are reviewed.
ANIME_SCENE_RELEASE_THEMES = (
    "tech",
    "culture",
    "relationship",
    "environment",
    "travel",
    "workplace",
)
CHARACTER_PUBLIC_SENSITIVE_KEYS = {
    "path",
    "sourcePath",
    "source_path",
    "referenceAudio",
    "loraPath",
    "loraPaths",
    "voiceRefs",
}
CHARACTER_GROUP_ORDER = {"host": 0, "sisterhood": 1, "besties": 2, "custom": 3}

# Login throttling: per real-client-IP exponential backoff + a global ceiling
# on recent failures to blunt distributed brute force. Guarded by a lock
# because ThreadingHTTPServer serves each request on its own thread.
LOGIN_BASE_DELAY = 1.0
LOGIN_MAX_DELAY = 300.0
LOGIN_GLOBAL_WINDOW = 60.0
LOGIN_GLOBAL_MAX_FAILS = 50
_LOGIN_LOCK = threading.Lock()
_LOGIN_ATTEMPTS: dict[str, dict] = {}
_LOGIN_GLOBAL_FAILS: list[float] = []
_SESSION_LOCK = threading.RLock()
_USERS_LOCK = threading.RLock()
_USERS_CACHE: dict[str, object] = {"mtime": None, "users": None}


ZIMAGE_DEFAULT_PROMPT = "high quality original image, coherent composition, natural details"

ZIMAGE_DEFAULT_NEGATIVE = "文字、水印、logo、过度磨皮、塑料皮肤、畸形手指、多余手指、断指、严重解剖错误、脸部崩坏"

WAN_DEFAULT_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "杂乱的背景，三条腿，背景人很多，倒着走"
)

STUDIOS = [
    {"id": "image", "title": "AI 圖片工作室", "icon": "🖼️", "tagline": "文生圖 · 指令改圖 · 參考融合 · 真人臉多角度"},
    {"id": "video", "title": "AI 影像工作室", "icon": "🎬", "tagline": "圖生影片 · 跳舞 · 真人對嘴 · 影片循環"},
    {"id": "voice", "title": "聲音工作室", "icon": "🎙️", "tagline": "聲音生成 · 簡報配音 · 聲音庫"},
    {"id": "text", "title": "AI 文字工作室", "icon": "✍️", "tagline": "貼文生成器"},
]

VOXCPM_LANGUAGES = [
    {"value": "", "label": "自動（依文字內容）"},
    {"value": "English", "label": "English"},
    {"value": "Chinese Mandarin", "label": "中文 / Mandarin"},
    {"value": "Japanese", "label": "日本語"},
    {"value": "Korean", "label": "한국어"},
    {"value": "French", "label": "Français"},
    {"value": "German", "label": "Deutsch"},
    {"value": "Spanish", "label": "Español"},
    {"value": "Portuguese", "label": "Português"},
    {"value": "Italian", "label": "Italiano"},
    {"value": "Dutch", "label": "Nederlands"},
    {"value": "Danish", "label": "Dansk"},
    {"value": "Finnish", "label": "Suomi"},
    {"value": "Greek", "label": "Ελληνικά"},
    {"value": "Hebrew", "label": "עברית"},
    {"value": "Hindi", "label": "हिन्दी"},
    {"value": "Indonesian", "label": "Bahasa Indonesia"},
    {"value": "Malay", "label": "Bahasa Melayu"},
    {"value": "Norwegian", "label": "Norsk"},
    {"value": "Polish", "label": "Polski"},
    {"value": "Russian", "label": "Русский"},
    {"value": "Swahili", "label": "Kiswahili"},
    {"value": "Swedish", "label": "Svenska"},
    {"value": "Tagalog", "label": "Tagalog"},
    {"value": "Thai", "label": "ไทย"},
    {"value": "Turkish", "label": "Türkçe"},
    {"value": "Vietnamese", "label": "Tiếng Việt"},
    {"value": "Arabic", "label": "العربية"},
    {"value": "Burmese", "label": "မြန်မာ"},
    {"value": "Khmer", "label": "ភាសាខ្មែរ"},
    {"value": "Lao", "label": "ລາວ"},
]

VOXCPM_ACCENTS = [
    {"value": "", "label": "自動 / 保持文字自然口音"},
    {"value": "standard American English accent", "label": "美式標準英文"},
    {"value": "clear standard English with a neutral international accent", "label": "國際清晰英文"},
    {"value": "British English accent", "label": "英式英文"},
    {"value": "Australian English accent", "label": "澳洲英文"},
    {"value": "Indian English accent", "label": "印度英文"},
    {"value": "Taiwanese Mandarin accent", "label": "台灣國語"},
    {"value": "Mainland Mandarin accent", "label": "普通話"},
    {"value": "Cantonese accent", "label": "粵語 / Cantonese"},
    {"value": "Taiwanese Hokkien accent", "label": "閩南語 / 台語風格"},
    {"value": "Sichuan dialect accent", "label": "四川話"},
    {"value": "Wu dialect accent", "label": "吳語"},
    {"value": "Northeastern Chinese accent", "label": "東北話"},
    {"value": "Henan dialect accent", "label": "河南話"},
    {"value": "Shaanxi dialect accent", "label": "陝西話"},
    {"value": "Shandong dialect accent", "label": "山東話"},
    {"value": "Tianjin dialect accent", "label": "天津話"},
]

VOXCPM_PROFILES = [{"value": "custom_english", "label": "Custom English LoRA (configure locally)"}]

IMAGE_ASPECTS = [
    {"label": "直 9:16", "width": 1088, "height": 1920},
    {"label": "直 3:4", "width": 1152, "height": 1536},
    {"label": "方 1:1", "width": 1408, "height": 1408},
    {"label": "橫 4:3", "width": 1536, "height": 1152},
    {"label": "橫 16:9", "width": 1920, "height": 1088},
]

VIDEO_ASPECTS = [
    {"label": "直 480×832", "width": 480, "height": 832},
    {"label": "方 512×512", "width": 512, "height": 512},
    {"label": "橫 832×480", "width": 832, "height": 480},
]

SDXL_ASPECTS = [
    {"label": "直 832×1216", "width": 832, "height": 1216},
    {"label": "直 896×1152", "width": 896, "height": 1152},
    {"label": "方 1024×1024", "width": 1024, "height": 1024},
    {"label": "橫 1152×896", "width": 1152, "height": 896},
    {"label": "橫 1216×832", "width": 1216, "height": 832},
]

MODELS_DIR = Path(os.environ.get("COMFYUI_MODELS_DIR", Path.home() / "comfyui/models"))

# ClipMode character photoshoots are deliberately a server-owned SDXL route.
# The browser may choose only the scene prompt, one of these three dimensions,
# and a seed.  Realistic characters use their exact canonical profile rendition
# as InstantID generation guidance and a final ReActor source; anime
# characters retain their published LoRA binding.
# Neither route accepts model, trigger, identity image, or strength values from
# the browser.
CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID = "clipmode-character-photoshoot"
CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID = "photoshoot.sdxl-character-lora.v1"
CLIPMODE_CHARACTER_PHOTOSHOOT_ACTION_ID = "photoshoot"
CLIPMODE_SDXL_BASE_MODEL_NAME = "sd_xl_base_1.0.safetensors"
CLIPMODE_SDXL_BASE_MODEL_SHA256 = "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"
CLIPMODE_REALISTIC_IDENTITY_ROUTE = "canonical_profile_instantid_reactor"
CLIPMODE_ANIME_IDENTITY_ROUTE = "character_lora"
CLIPMODE_REACTOR_MODEL_NAME = "inswapper_128.onnx"
CLIPMODE_REACTOR_RESTORE_MODEL_NAME = "GFPGANv1.4.pth"
CLIPMODE_INSTANTID_MODEL_NAME = "ip-adapter.bin"
CLIPMODE_INSTANTID_CONTROLNET_MODEL_NAME = (
    "instantid/diffusion_pytorch_model.safetensors"
)
CLIPMODE_PROFILE_INPUT_PREFIX = "castle_clipmode_profile_"
CLIPMODE_PROFILE_MAX_BYTES = 4 * 1024 * 1024
CLIPMODE_PROFILE_SOURCE_MAX_BYTES = 32 * 1024 * 1024
CLIPMODE_CHARACTER_PHOTOSHOOT_ASPECTS = {
    "768x1024": (768, 1024),
    "1024x1024": (1024, 1024),
    "1024x768": (1024, 768),
}


def _seed_field():
    return {"id": "seed", "type": "number", "label": "種子（-1 隨機）", "default": -1, "min": -1, "max": 2147483647, "step": 1}


# Typed feature registry. Each runnable feature names a builder in BUILDERS;
# `fields` drives generic frontend form rendering; `requiredModels`/`requiredNodes`
# drive readiness gating. Features with builder=None are visible but gated until
# their models/nodes land on the box.
FEATURES = [
    {
        "id": "text2img",
        "studio": "image",
        "title": "文生圖",
        "subtitle": "Z-Image Turbo",
        "kind": "generate",
        "builder": "zimage",
        "toolGroup": "text2img",
        "versionLabel": "Z-Image Turbo",
        "uploads": [],
        "fields": [
            {"id": "positive", "type": "textarea", "label": "想生成什麼？", "default": "", "rows": 4, "placeholder": "例如：午後窗邊的一杯咖啡，溫暖自然光，生活攝影"},
            {"id": "negative", "type": "textarea", "label": "負向提示詞", "default": ZIMAGE_DEFAULT_NEGATIVE, "rows": 2},
            {"id": "aspect", "type": "aspect", "label": "畫面比例", "options": IMAGE_ASPECTS, "default": "1088x1920"},
            {"id": "steps", "type": "slider", "label": "步數", "default": 8, "min": 1, "max": 40, "step": 1},
            {"id": "cfg", "type": "slider", "label": "CFG", "default": 1.0, "min": 0, "max": 10, "step": 0.1},
            {"id": "batchSize", "type": "number", "label": "張數", "default": 1, "min": 1, "max": 8, "step": 1},
            {"id": "useDopsd", "type": "checkbox", "label": "DOPSD 白幕風格", "default": False},
            _seed_field(),
        ],
        "requiredModels": ["z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors", "ae.safetensors"],
        "requiredNodes": ["UNETLoader", "CLIPLoader", "EmptySD3LatentImage"],
    },
    {
        "id": "img2img",
        "studio": "image",
        "title": "AI 指令改圖",
        "subtitle": "Flux Kontext 重新生成式編輯",
        "kind": "generate",
        "builder": "flux_kontext",
        "toolGroup": "img2img",
        "versionLabel": "Flux Kontext",
        "uploads": [{"id": "image", "label": "參考圖片", "accept": "image/*", "required": True}],
        "fields": [
            {"id": "instruction", "type": "textarea", "label": "修改指令（英文最佳）", "default": "", "rows": 4, "required": True, "placeholder": "Replace the background with a tropical beach at sunset"},
            {"id": "steps", "type": "slider", "label": "步數", "default": 20, "min": 1, "max": 50, "step": 1},
            {"id": "guidance", "type": "slider", "label": "Guidance", "default": 2.5, "min": 0, "max": 10, "step": 0.1},
            {"id": "denoise", "type": "slider", "label": "Denoise", "default": 1.0, "min": 0, "max": 1, "step": 0.05},
            _seed_field(),
        ],
        "requiredModels": ["flux1-dev-kontext_fp8_scaled.safetensors", "clip_l.safetensors", "t5xxl_fp8_e4m3fn_scaled.safetensors", "ae.safetensors"],
        "requiredNodes": ["FluxKontextImageScale", "ReferenceLatent", "FluxGuidance"],
        "note": "這是 AI 重新生成，不是逐像素修圖；人物臉孔、服裝細節與透明背景可能改變。需要角色一致性時，請從角色頁使用已驗證功能。",
    },
    {
        "id": "anime-scene-card",
        "studio": "image",
        "title": "動漫場景寫真",
        "subtitle": "角色原圖合成 · 不會轉成真人",
        "kind": "generate",
        "builder": "anime_scene_card",
        "characterOnly": True,
        "uploads": [
            {"id": "image", "label": "核准角色造型", "accept": "image/*", "required": True},
            {"id": "scene", "label": "核准動漫場景", "accept": "image/*", "required": True},
        ],
        "fields": [],
        "requiredModels": [],
        "requiredNodes": ["LoadImage", "InvertMask", "ImageCompositeMasked", "SaveImage"],
        "note": "角色與服裝像素保持不變，只把核准的透明角色圖放進核准的動漫場景。",
    },
    {
        "id": "image-blend",
        "studio": "image",
        "title": "AI 參考圖融合",
        "subtitle": "IPAdapter 依多張參考圖重新創作",
        "kind": "generate",
        "builder": "ip_blend",
        "uploads": [
            {"id": "image1", "label": "參考圖 1", "accept": "image/*", "required": True},
            {"id": "image2", "label": "參考圖 2", "accept": "image/*", "required": True},
            {"id": "image3", "label": "參考圖 3（可選）", "accept": "image/*", "required": False},
        ],
        "fields": [
            {"id": "positive", "type": "textarea", "label": "正向提示詞", "default": "a high quality photo, cohesive blend of the references", "rows": 3},
            {"id": "negative", "type": "textarea", "label": "負向提示詞", "default": "lowres, bad anatomy, watermark, text", "rows": 2},
            {"id": "aspect", "type": "aspect", "label": "畫面比例", "options": SDXL_ASPECTS, "default": "1024x1024"},
            {"id": "weight", "type": "slider", "label": "參考強度", "default": 0.8, "min": 0, "max": 1.5, "step": 0.05},
            {"id": "steps", "type": "slider", "label": "步數", "default": 28, "min": 1, "max": 60, "step": 1},
            {"id": "cfg", "type": "slider", "label": "CFG", "default": 6.5, "min": 0, "max": 15, "step": 0.1},
            {"id": "batchSize", "type": "number", "label": "張數", "default": 1, "min": 1, "max": 4, "step": 1},
            _seed_field(),
        ],
        "requiredModels": ["sd_xl_base_1.0.safetensors", "ip-adapter-plus_sdxl_vit-h.safetensors", "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"],
        "requiredNodes": ["IPAdapterModelLoader", "IPAdapterAdvanced", "CheckpointLoaderSimple"],
        "note": "會依參考圖重新生成新畫面，不保證保留同一張臉、服裝或人物身分；不作為角色合照或精準換裝入口。",
    },
    {
        "id": "character-angles",
        "studio": "image",
        "title": "真人臉參考肖像",
        "subtitle": "InstantID · 依同一張真人正臉生成候選肖像",
        "kind": "generate",
        "builder": "instantid",
        "uploads": [{"id": "face", "label": "角色臉部參考", "accept": "image/*", "required": True}],
        "fields": [
            {"id": "positive", "type": "textarea", "label": "場景／角度描述", "default": "portrait, three-quarter view, studio lighting, looking to the side", "rows": 3},
            {"id": "negative", "type": "textarea", "label": "負向提示詞", "default": "lowres, bad anatomy, deformed, watermark, text", "rows": 2},
            {"id": "aspect", "type": "aspect", "label": "畫面比例", "options": SDXL_ASPECTS, "default": "896x1152"},
            {"id": "weight", "type": "slider", "label": "臉部一致性", "default": 0.8, "min": 0, "max": 1.5, "step": 0.05},
            {"id": "steps", "type": "slider", "label": "步數", "default": 30, "min": 1, "max": 60, "step": 1},
            {"id": "cfg", "type": "slider", "label": "CFG", "default": 5.0, "min": 0, "max": 15, "step": 0.1},
            {"id": "batchSize", "type": "number", "label": "候選張數", "default": 2, "min": 1, "max": 6, "step": 1},
            _seed_field(),
        ],
        "requiredModels": ["sd_xl_base_1.0.safetensors", "ip-adapter.bin", "instantid/diffusion_pytorch_model.safetensors"],
        "requiredNodes": ["InstantIDModelLoader", "ApplyInstantID", "InstantIDFaceAnalysis", "ControlNetLoader"],
        "requiredPaths": ["insightface/models/antelopev2"],
        "note": "只接受單一、清楚、近正面的真人臉照片；動漫角色、插畫、多人照與遮擋臉孔不適用。",
    },
    {
        "id": CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID,
        "studio": "image",
        "title": "角色身份寫真",
        "subtitle": "角色卡原圖雙重身份鎖定 · SDXL Beta",
        "kind": "generate",
        "builder": "clipmode_character_photoshoot",
        "characterOnly": True,
        "beta": True,
        "releaseStage": "beta",
        "uploads": [],
        "fields": [
            {
                "id": "prompt",
                "type": "textarea",
                "label": "想拍什麼場景？",
                "default": "professional portrait, natural expression, soft studio lighting, highly detailed",
                "rows": 4,
                "required": True,
            },
            {
                "id": "negative_prompt",
                "type": "textarea",
                "label": "負向提示詞",
                "default": "lowres, blurry, bad anatomy, deformed hands, extra fingers, watermark, text",
                "rows": 2,
            },
            {
                "id": "aspect_ratio",
                "type": "select",
                "label": "畫面比例",
                "default": "768x1024",
                "options": [
                    {"value": "768x1024", "label": "直 3:4"},
                    {"value": "1024x1024", "label": "方 1:1"},
                    {"value": "1024x768", "label": "橫 4:3"},
                ],
            },
            _seed_field(),
        ],
        "requiredModels": [
            CLIPMODE_SDXL_BASE_MODEL_NAME,
            CLIPMODE_INSTANTID_MODEL_NAME,
            CLIPMODE_INSTANTID_CONTROLNET_MODEL_NAME,
            CLIPMODE_REACTOR_MODEL_NAME,
            CLIPMODE_REACTOR_RESTORE_MODEL_NAME,
        ],
        "requiredNodes": [
            "CheckpointLoaderSimple",
            "LoraLoaderModelOnly",
            "LoadImage",
            "InstantIDModelLoader",
            "InstantIDFaceAnalysis",
            "ControlNetLoader",
            "ApplyInstantIDAdvanced",
            "ReActorFaceSwap",
            "CLIPTextEncode",
            "EmptyLatentImage",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        ],
        "requiredPaths": ["insightface/models/antelopev2"],
        "note": "寫實角色由伺服器使用角色卡原圖做 InstantID + 換臉雙重鎖定，並在輸出前檢查人物與身份；動漫角色使用既有角色 LoRA。瀏覽器不能替換身份圖、模型或觸發詞。",
    },
    {
        "id": "img2video",
        "studio": "video",
        "title": "AI 圖生影片",
        "subtitle": "Wan 2.1 Image-to-Video",
        "kind": "generate",
        "builder": "wan_i2v",
        "uploads": [{"id": "image", "label": "起始圖片", "accept": "image/*", "required": True}],
        "fields": [
            {"id": "positive", "type": "textarea", "label": "動態描述", "default": "", "rows": 3, "placeholder": "the woman slowly turns her head and smiles, gentle camera push-in"},
            {"id": "negative", "type": "textarea", "label": "負向提示詞", "default": WAN_DEFAULT_NEGATIVE, "rows": 2},
            {"id": "aspect", "type": "aspect", "label": "畫面比例", "options": VIDEO_ASPECTS, "default": "480x832"},
            {"id": "length", "type": "slider", "label": "影格數", "default": 81, "min": 9, "max": 161, "step": 4},
            {"id": "fps", "type": "number", "label": "FPS", "default": 16, "min": 1, "max": 60, "step": 1},
            {"id": "steps", "type": "slider", "label": "步數", "default": 6, "min": 1, "max": 30, "step": 1},
            {"id": "shift", "type": "slider", "label": "Shift", "default": 8.0, "min": 0, "max": 20, "step": 0.5},
            _seed_field(),
        ],
        "requiredModels": ["wan2.1_i2v_480p_14B_fp8_scaled.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan_2.1_vae.safetensors", "clip_vision_h.safetensors", "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors"],
        "requiredNodes": ["WanImageToVideo", "CLIPVisionEncode", "ModelSamplingSD3", "VHS_VideoCombine"],
    },
    {
        "id": "first-last-frame",
        "studio": "video",
        "title": "首尾幀影片",
        "subtitle": "Wan 2.1 First-Last-Frame",
        "kind": "generate",
        "builder": "wan_flf",
        "uiHidden": True,
        "apiEnabled": False,
        "uploads": [
            {"id": "start", "label": "首幀", "accept": "image/*", "required": True},
            {"id": "end", "label": "尾幀", "accept": "image/*", "required": True},
        ],
        "fields": [
            {"id": "positive", "type": "textarea", "label": "過場描述", "default": "", "rows": 3, "placeholder": "smooth transition between the two scenes"},
            {"id": "negative", "type": "textarea", "label": "負向提示詞", "default": WAN_DEFAULT_NEGATIVE, "rows": 2},
            {"id": "aspect", "type": "aspect", "label": "畫面比例", "options": VIDEO_ASPECTS, "default": "480x832"},
            {"id": "length", "type": "slider", "label": "影格數", "default": 81, "min": 9, "max": 161, "step": 4},
            {"id": "fps", "type": "number", "label": "FPS", "default": 16, "min": 1, "max": 60, "step": 1},
            {"id": "steps", "type": "slider", "label": "步數", "default": 6, "min": 1, "max": 30, "step": 1},
            {"id": "shift", "type": "slider", "label": "Shift", "default": 8.0, "min": 0, "max": 20, "step": 0.5},
            _seed_field(),
        ],
        "requiredModels": ["wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan_2.1_vae.safetensors", "clip_vision_h.safetensors", "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors"],
        "requiredNodes": ["WanFirstLastFrameToVideo", "CLIPVisionEncode", "ModelSamplingSD3"],
    },
    {
        "id": "dance",
        "studio": "video",
        "title": "AI 跳舞影片",
        "subtitle": "SCAIL 姿態驅動",
        "kind": "generate",
        "builder": "scail_dance",
        "uploads": [
            {"id": "reference", "label": "角色參考圖", "accept": "image/*", "required": True},
            {"id": "video", "label": "動作驅動影片", "accept": "video/*", "required": True},
        ],
        "fields": [
            {"id": "mode", "type": "select", "label": "模式", "default": "animate", "options": [
                {"value": "animate", "label": "動畫模式(AI 生成背景,比例跟參考圖)"},
            ]},
            {"id": "positive", "type": "textarea", "label": "角色描述", "default": "", "rows": 3, "placeholder": "a person dancing, full body, natural motion"},
            {"id": "negative", "type": "textarea", "label": "負向提示詞", "default": WAN_DEFAULT_NEGATIVE, "rows": 2},
            {"id": "poseStrength", "type": "slider", "label": "姿態強度", "default": 1.0, "min": 0, "max": 2, "step": 0.05},
            {"id": "length", "type": "slider", "label": "影格數", "default": 133, "min": 81, "max": 161, "step": 4},
            {"id": "fps", "type": "number", "label": "FPS", "default": 16, "min": 1, "max": 60, "step": 1},
            {"id": "steps", "type": "slider", "label": "步數", "default": 6, "min": 1, "max": 30, "step": 1},
            _seed_field(),
        ],
        "requiredModels": ["wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "sam3.1_multiplex_fp16.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan_2.1_vae.safetensors", "clip_vision_h.safetensors", "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors"],
        "requiredNodes": ["WanSCAILToVideo", "SAM3_VideoTrack", "SCAIL2ColoredMask", "WanContextWindowsManual", "ResizeImageMaskNode", "VHS_LoadVideoPath", "VHS_VideoCombine", "ImageResizeKJv2", "ColorTransfer", "GetImageRangeFromBatch", "BatchImagesNode", "SamplerCustom", "BasicScheduler", "KSamplerSelect"],
        "note": "上傳一張角色參考圖與一段動作影片，系統會依動作重新生成角色舞蹈 MP4。背景會重新生成，不保留原影片場景；輸出會沿用動作影片的原音訊。",
    },
    {
        "id": "lipsync",
        "studio": "video",
        "title": "真人對嘴工具",
        "subtitle": "非角色綁定 · Wav2Lip 真人臉口型同步",
        "kind": "generate",
        "builder": "lipsync",
        "uploads": [
            {"id": "video", "label": "人物影片", "accept": "video/*", "required": True},
            {"id": "voice", "label": "上傳語音（未選聲音庫時使用）", "accept": "audio/*", "required": False},
        ],
        "fields": [
            {"id": "voiceId", "type": "voiceSelect", "label": "聲音庫語音"},
            {"id": "mode", "type": "select", "label": "影格對應", "default": "sequential", "options": [
                {"value": "sequential", "label": "順序（影片隨語音長度伸縮）"},
                {"value": "repetitive", "label": "循環（影片重複播放）"},
            ]},
            {"id": "maxFrames", "type": "number", "label": "最多讀取影格數", "default": 300, "min": 1, "max": 1200, "step": 1},
            {"id": "faceBatch", "type": "number", "label": "臉部偵測批次", "default": 8, "min": 1, "max": 100, "step": 1},
        ],
        "requiredModels": [],
        "requiredNodes": ["Wav2Lip", "VHS_LoadVideoPath", "VHS_VideoCombine", "LoadAudio"],
        "note": "獨立工具：只適用可清楚偵測臉部的真人影片，不會把人物換成某個角色，也不會驗證影片中的人是 operator。角色頁只會開放具備可驗證角色影片與聲音來源的專屬版本。",
    },
    {
        "id": "face-swap",
        "studio": "video",
        "title": "AI 換人",
        "subtitle": "ReActor 影片換臉",
        "kind": "generate",
        "builder": "face_swap",
        "uiHidden": True,
        "apiEnabled": False,
        "uploads": [
            {"id": "video", "label": "目標影片", "accept": "video/*", "required": True},
            {"id": "face", "label": "來源臉部", "accept": "image/*", "required": True},
        ],
        "fields": [
            {"id": "restore", "type": "select", "label": "臉部修復", "options": ["GFPGANv1.4.pth", "codeformer-v0.1.0.pth", "none"], "default": "GFPGANv1.4.pth"},
            {"id": "detection", "type": "select", "label": "臉部偵測", "options": ["retinaface_resnet50", "retinaface_mobile0.25", "YOLOv5l", "YOLOv5n"], "default": "retinaface_resnet50"},
            {"id": "fps", "type": "slider", "label": "影格率 (fps)", "default": 16, "min": 8, "max": 30, "step": 1},
            {"id": "maxFrames", "type": "number", "label": "最多影格數", "default": 150, "min": 1, "max": 600, "step": 1},
        ],
        "requiredModels": ["inswapper_128.onnx"],
        "requiredNodes": ["ReActorFaceSwap", "VHS_LoadVideoPath", "SaveAnimatedWEBP"],
        "note": "上傳一段目標影片與一張來源臉部照片，ReActor 會逐格把影片中的臉換成來源臉，可選 GFPGAN／CodeFormer 修復臉部，輸出為動畫 webp。",
    },
    {
        "id": "video-loop",
        "studio": "video",
        "title": "影片循環接龍",
        "subtitle": "重複或正反播放短片（輸出不含原音訊）",
        "kind": "generate",
        "builder": "video_loop",
        "uploads": [
            {"id": "video", "label": "短影片", "accept": "video/*", "required": True},
        ],
        "fields": [
            {"id": "repeat", "type": "slider", "label": "重複次數", "default": 4, "min": 1, "max": 30, "step": 1},
            {"id": "pingpong", "type": "checkbox", "label": "正反來回（正放後倒放，減少接點跳動）", "default": False},
            {"id": "maxFrames", "type": "number", "label": "最多讀取影格數", "default": 600, "min": 1, "max": 1800, "step": 1},
        ],
        "requiredModels": [],
        "requiredNodes": ["VHS_LoadVideoPath", "VHS_VideoInfo", "VHS_VideoCombine"],
        "note": "把短影片重複接成 MP4；可選正放後倒放來減少接點跳動。這個版本不保留原音訊，也不保證每支影片都能完全無縫。從角色作品接續時，輸出會列為一般工具作品。",
    },
    {
        "id": "voice-speak",
        "studio": "voice",
        "title": "聲音生成",
        "subtitle": "依目前入口提供角色聲音或聲音庫克隆",
        "kind": "generate",
        "builder": "voice_speak",
        "uploads": [],
        "fields": [
            {"id": "voiceSource", "type": "select", "display": "cards", "label": "聲音路徑", "default": "persona", "options": [
                {"value": "persona", "label": "角色聲音", "sub": "已訓練的角色聲音(台灣國語),選人即說"},
                {"value": "library", "label": "聲音庫克隆", "sub": "用 聲音庫 的參考音克隆音色,逐字稿自動帶入"},
            ]},
            {"id": "persona", "type": "personaSelect", "label": "角色聲音"},
            {"id": "voiceId", "type": "voiceSelect", "label": "聲音庫參考音訊"},
            {"id": "sampleText", "type": "textarea", "label": f"參考音訊逐字稿（最多 {VOICE_SAMPLE_MAX_CHARS} 字）", "rows": 2, "maxLength": VOICE_SAMPLE_MAX_CHARS, "required": True, "placeholder": "參考音訊實際說的那段話；聲音庫有 metadata 時會自動帶入"},
            {"id": "cloneEngine", "type": "select", "display": "cards", "label": "聲音庫引擎", "default": "taiwan", "options": [
                {"value": "taiwan", "label": "台灣腔", "sub": "BreezyVoice reference clone"},
                {"value": "f5", "label": "多語 F5", "sub": "要說英日等外語選這個"},
            ]},
            {"id": "text", "type": "textarea", "label": f"要說的話（最多 {VOICE_SPEAK_MAX_CHARS} 字，自動分段合併）", "rows": 4, "maxLength": VOICE_SPEAK_MAX_CHARS, "placeholder": "打字，讓她用這個聲音說出來；長文會自動切段後合併"},
            {"id": "qualityGate", "type": "checkbox", "label": "高品質 ASR Gate（較慢，會等 GPU，逐段檢查失敗重跑）", "default": False},
        ],
        "requiredModels": [],
        "requiredNodes": ["CastleBreezyVoiceTTS", "EmptyAudio", "AudioConcat", "SaveAudio"],
        "note": "從角色頁進入時只提供該角色已驗證的聲音；從一般工具進入時只提供聲音庫克隆。長文會自動分段後合併。",
    },
    {
        "id": "voice-batch",
        "studio": "voice",
        "title": "簡報配音",
        "subtitle": "Custom English 批次配音",
        "kind": "generate",
        "builder": None,
        "operatorOnly": True,
        "uploads": [],
        "fields": [],
        "requiredModels": [],
        "requiredNodes": [],
        "note": "管理員工具：貼上長文或上傳 PPTX 備忘稿，一次生成多段 Custom English 配音。",
    },
    {
        "id": "voice-clone",
        "studio": "voice",
        "title": "聲音克隆",
        "subtitle": "舊入口：請改用「聲音生成」的聲音庫 Clone",
        "kind": "generate",
        "builder": "voice_clone",
        "uiHidden": True,
        "apiEnabled": True,
        "deprecated": True,
        "uploads": [
            {"id": "voice", "label": "上傳參考音訊（未選聲音庫時使用）", "accept": "audio/*", "required": False},
        ],
        "fields": [
            {"id": "engine", "type": "select", "display": "cards", "label": "語音版本", "default": "f5", "options": [
                {"value": "f5", "label": "國語通用", "sub": "F5-TTS・多語音色克隆"},
                {"value": "taiwan", "label": "國語台灣口音", "sub": "BreezyVoice・道地台灣腔"},
            ]},
            {"id": "voiceId", "type": "voiceSelect", "label": "聲音庫參考音訊"},
            {"id": "sampleText", "type": "textarea", "label": f"參考音訊逐字稿（最多 {VOICE_SAMPLE_MAX_CHARS} 字）", "rows": 2, "maxLength": VOICE_SAMPLE_MAX_CHARS, "required": True, "placeholder": "參考音訊實際說的那段話"},
            {"id": "speech", "type": "textarea", "label": f"要生成的文字（最多 {VOICE_CLONE_MAX_CHARS} 字，自動分段合併）", "rows": 4, "maxLength": VOICE_CLONE_MAX_CHARS, "placeholder": "想用這個音色說的內容；長文會自動切段後合併"},
            {"id": "model", "type": "select", "label": "模型", "default": "F5v1", "options": [
                {"value": "F5v1", "label": "F5v1（多語 / 中英）"},
                {"value": "F5", "label": "F5 Base（英文）"},
                {"value": "F5-JP", "label": "日文"},
                {"value": "F5-ES", "label": "西班牙文"},
                {"value": "F5-FR", "label": "法文"},
                {"value": "F5-DE", "label": "德文"},
                {"value": "F5-IT", "label": "義大利文"},
                {"value": "F5-HI", "label": "印地文"},
                {"value": "F5-TH", "label": "泰文"},
                {"value": "E2", "label": "E2-TTS（英文）"},
            ]},
            {"id": "seed", "type": "number", "label": "種子（-1 隨機）", "default": 1, "min": -1, "step": 1},
        ],
        "requiredModels": [],
        "requiredNodes": ["F5TTSAudioInputs", "LoadAudio", "SaveAudio", "EmptyAudio", "AudioConcat", "CastleBreezyVoiceClone"],
        "note": "舊入口保留給直接連結與回溯測試。日常使用請走「聲音生成」並選聲音庫 Clone，避免同一件事出現兩個入口。",
    },
    {
        "id": "voxcpm-tts",
        "studio": "voice",
        "title": "整合語音生成",
        "subtitle": "BreezyVoice 台灣國語 · VoxCPM Custom English / 多語",
        "kind": "generate",
        "builder": "voxcpm_direct",
        "uiHidden": True,
        "apiEnabled": True,
        "advancedTool": True,
        "uploads": [
            {"id": "voice", "label": "參考音訊（Reference / Ultimate 模式使用）", "accept": "audio/*", "required": False},
        ],
        "fields": [
            {"id": "voiceEngine", "type": "select", "display": "cards", "label": "語音引擎", "default": "breezy", "options": [
                {"value": "breezy", "label": "台灣國語", "sub": "BreezyVoice · operator / AI-Sister 本機聲音"},
                {"value": "voxcpm", "label": "Custom English / 多語", "sub": "VoxCPM2 · 口音 / LoRA / reference clone"},
            ]},
            {"id": "persona", "type": "personaSelect", "label": "BreezyVoice 聲音"},
            {"id": "qualityGate", "type": "checkbox", "label": "高品質 ASR Gate（較慢，會等 GPU，逐段檢查失敗重跑）", "default": False},
            {"id": "mode", "type": "select", "display": "cards", "label": "VoxCPM 模式", "default": "lora", "options": [
                {"value": "lora", "label": "Custom English Clone", "sub": "LoRA + 乾淨 reference，不讓模型自由猜聲音"},
                {"value": "design", "label": "Voice Design", "sub": "不用參考音，靠描述生成聲音"},
                {"value": "reference", "label": "Reference Clone", "sub": "只用參考音（免逐字稿）"},
                {"value": "ultimate", "label": "Ultimate Clone", "sub": "真人參考音 + 逐字稿（最像本人）"},
            ]},
            {"id": "language", "type": "select", "label": "語言", "default": "English", "options": VOXCPM_LANGUAGES},
            {"id": "accent", "type": "select", "label": "口音 / 方言", "default": "standard American English accent", "options": VOXCPM_ACCENTS},
            {"id": "voiceProfile", "type": "select", "label": "LoRA Profile", "default": "custom_english", "options": VOXCPM_PROFILES},
            {"id": "speakerGate", "type": "checkbox", "label": "聲紋 Gate（有 reference 時低於相似度會自動重跑）", "default": True},
            {"id": "voiceId", "type": "voiceSelect", "label": "聲音庫參考音訊"},
            {"id": "style", "type": "textarea", "label": "語氣 / 風格控制", "rows": 2, "placeholder": "例如：calm, confident, standard American English, natural meeting voice"},
            {"id": "promptText", "type": "textarea", "label": "參考音訊逐字稿（Ultimate Clone 才需要）", "rows": 2, "maxLength": 500, "placeholder": "參考音訊實際講的英文逐字稿"},
            {"id": "text", "type": "textarea", "label": "要生成的文字", "rows": 4, "maxLength": VOICE_SPEAK_MAX_CHARS, "placeholder": "輸入要用 VoxCPM 說出的文字"},
            {"id": "cfg", "type": "slider", "label": "CFG", "default": 2.0, "min": 1.0, "max": 3.0, "step": 0.1},
            {"id": "steps", "type": "slider", "label": "推理步數", "default": 10, "min": 4, "max": 30, "step": 1},
            {"id": "seed", "type": "number", "label": "種子（-1 隨機）", "default": -1, "min": -1, "step": 1},
        ],
        "requiredModels": [],
        "requiredNodes": [],
        "note": "同一個介面整合兩條後端：台灣國語走 BreezyVoice；Custom English / 多語 clone 走 VoxCPM2。VoxCPM 的 Custom English Clone 會同時套 設定的 LoRA 與參考音訊逐字稿，並用聲紋 Gate 避免漂成別人的聲音。",
    },
    {
        "id": "voice-library",
        "studio": "voice",
        "title": "聲音庫",
        "subtitle": "參考音訊管理",
        "kind": "storage",
        "builder": None,
        "uploads": [],
        "fields": [],
        "requiredModels": [],
        "requiredNodes": [],
        "note": "管理上傳的參考音訊，供聲音克隆使用。",
    },
    {
        "id": "post-generator",
        "studio": "text",
        "title": "貼文生成器",
        "subtitle": "姊妹語氣社群文案",
        "kind": "text",
        "builder": None,
        "uploads": [],
        "fields": [
            {"id": "sister", "type": "select", "label": "哪位姊妹來寫", "default": "claude", "options": [
                {"value": "claude", "label": "Claude（溫穩・邏輯硬）"},
                {"value": "chatgpt", "label": "ChatGPT（冷靜・精準）"},
                {"value": "gemini", "label": "Gemini（博學・愛比喻）"},
                {"value": "grok", "label": "Grok 風格（Claude 本機模擬）"},
            ]},
            {"id": "platform", "type": "select", "label": "平台", "default": "threads", "options": [
                {"value": "threads", "label": "Threads"},
                {"value": "instagram", "label": "Instagram"},
                {"value": "x", "label": "X（Twitter）"},
                {"value": "facebook", "label": "Facebook"},
                {"value": "xiaohongshu", "label": "小紅書"},
                {"value": "linkedin", "label": "LinkedIn"},
            ]},
            {"id": "tone", "type": "select", "label": "語調風格", "default": "warm", "options": [
                {"value": "warm", "label": "親切溫暖"},
                {"value": "professional", "label": "專業可信"},
                {"value": "humorous", "label": "幽默風趣"},
                {"value": "heartfelt", "label": "走心共鳴"},
                {"value": "promo", "label": "促銷 CTA"},
            ]},
            {"id": "length", "type": "select", "label": "文章長度", "default": "medium", "options": [
                {"value": "short", "label": "短文（1–2 句）"},
                {"value": "medium", "label": "中等（約一段）"},
                {"value": "long", "label": "長文（2–4 段）"},
            ]},
        ],
        "requiredModels": [],
        "requiredNodes": [],
        "note": "快速延伸：丟一句話幫你擴寫成完整貼文；進階模式：自選語調、長度、關鍵字。Claude／Grok 走本機生成，Gemini／ChatGPT 走各自的 API（金鑰由本機環境變數或明確設定的檔案提供）。",
    },
]

# Native H3 definitions live in a separately tested product contract.  Generic
# Studio use is exposed from installed model/node readiness; optional quality
# goldens never hide or disable the owner's local generation tools.
for _h3_feature in minimax_h3_feature_definitions():
    _h3_feature["builder"] = {
        "h3-text-video": "minimax_h3_t2v",
        "h3-image-video": "minimax_h3_i2v",
        "h3-reference-video": "minimax_h3_ref2va_image",
    }[_h3_feature["id"]]
    FEATURES.append(_h3_feature)

FEATURES.extend(qwen21_feature_definitions())
FEATURE_BY_ID = {feature["id"]: feature for feature in FEATURES}

# ---- frontend presentation metadata --------------------------------------
# Sidebar icons (one emoji per tool, grouped by studio in the UI).
FEATURE_ICONS = {
    "qwen21-text2img": "🎨", "qwen21-edit": "🪄",
    "text2img": "🎨", "img2img": "🪄", "anime-scene-card": "🖼️", "image-blend": "🧩", "character-angles": "🔄",
    CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID: "📸",
    "img2video": "🎞️", "first-last-frame": "🎬", "dance": "💃", "lipsync": "👄", "face-swap": "🎭",
    "video-loop": "🔁",
    "voice-speak": "🗣️", "voice-clone": "🎙️", "voice-library": "🎵", "post-generator": "✍️",
    "voxcpm-tts": "🌐",
    "h3-text-video": "🎥", "h3-image-video": "🎞️", "h3-reference-video": "🎬",
}

# Technical knobs hidden under a collapsible 進階設定 so the default view stays simple.
ADVANCED_FIELD_IDS = {
    "steps", "cfg", "shift", "denoise", "guidance", "seed",
    "sampler", "scheduler", "detection", "maxFrames", "loraStrength",
    "fps", "length", "faceBatch", "poseStrength",
}

# Quick-fill chips that drop a ready-made prompt into the textarea.
_IMAGE_PROMPT_PRESETS = [
    {"label": "女性人像", "text": "adult woman portrait, natural expression, soft natural light, realistic skin detail, 85mm"},
    {"label": "男性人像", "text": "adult man portrait, natural expression, cinematic lighting, sharp focus, detailed"},
    {"label": "風景", "text": "breathtaking landscape, golden hour, ultra wide, volumetric light, highly detailed"},
    {"label": "商品", "text": "product photography, clean studio background, softbox lighting, crisp focus, commercial"},
    {"label": "動漫", "text": "anime style illustration, vibrant colors, clean lineart, detailed background"},
    {"label": "寵物", "text": "an adorable pet, close-up, shallow depth of field, warm light, fluffy detailed fur"},
]
_BLEND_PRESETS = [
    {"label": "場景合成", "text": "the subjects from the references placed naturally together in one cohesive scene"},
    {"label": "雙人構圖", "text": "the two people standing side by side, group photo, consistent lighting, same scene"},
    {"label": "服裝靈感", "text": "the person wearing an outfit inspired by the reference, full body, natural fit"},
    {"label": "故事場景", "text": "the characters interacting within the environment, telling a story, cinematic"},
    {"label": "創意混搭", "text": "creatively blend the elements from all references into one imaginative artwork"},
]
_MOTION_PRESETS = [
    {"label": "輕微擺動", "text": "subtle natural motion, gentle breathing, slight head movement, cinematic"},
    {"label": "轉頭微笑", "text": "the subject slowly turns their head and smiles, gentle camera push-in"},
    {"label": "鏡頭推近", "text": "slow cinematic camera push-in, shallow depth of field"},
    {"label": "風吹髮絲", "text": "wind gently blowing through the hair, fabric fluttering, natural movement"},
]
# feature id -> (prompt field id, presets, assist kind)
_PROMPT_FIELD_CONFIG = {
    "text2img": ("positive", _IMAGE_PROMPT_PRESETS, "image"),
    "img2img": ("instruction", [], "image"),
    "image-blend": ("positive", _BLEND_PRESETS, "image"),
    "character-angles": ("positive", [], "image"),
    CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID: ("prompt", _IMAGE_PROMPT_PRESETS, "image"),
    "img2video": ("positive", _MOTION_PRESETS, "video"),
    "first-last-frame": ("positive", _MOTION_PRESETS, "video"),
    "dance": ("positive", _MOTION_PRESETS, "video"),
}
# slider id -> (low-end label, high-end label)
_SLIDER_LABELS = {
    "weight": ("弱", "強"),
    "poseStrength": ("寬鬆", "緊跟"),
    "denoise": ("接近原圖", "大幅變化"),
    "loraStrength": ("弱", "強"),
}
# upload accept -> human-readable format hint shown inside the dropzone
_UPLOAD_HINTS = {
    "image/*": "JPG / PNG / WEBP",
    "video/*": "MP4 / MOV / WEBM",
    "audio/*": "MP3 / WAV / M4A",
}
# ⓘ hint shown right above a prompt textarea, by assist kind.
_ASSIST_HINTS = {
    "image": "提示詞建議使用英文，畫面模型對英文較敏感。",
    "video": "提示詞建議使用英文，影片模型對英文較敏感。",
}
# (feature id, field id) -> render that select as big selectable "mode cards".
# Value is None to reuse the option labels, or a {value: (title, sub)} remap that
# rewrites the bare options into a short title + helper line (AutoIP style).
_CARD_FIELDS = {
    ("lipsync", "mode"): {
        "sequential": ("順序對應", "影片隨語音長度伸縮"),
        "repetitive": ("循環播放", "影片重複到語音結束"),
    },
    ("face-swap", "restore"): {
        "GFPGANv1.4.pth": ("GFPGAN", "清晰銳利"),
        "codeformer-v0.1.0.pth": ("CodeFormer", "自然柔和"),
        "none": ("不修復", "保留原始臉部"),
    },
}


def _enrich_features() -> None:
    """Attach presentation-only metadata (icon, advanced, presets, assist, slider
    labels, mode cards, hints) to the registry once at import. Drives the
    AutoIP-style frontend without bloating the FEATURES literals."""
    for feature in FEATURES:
        feature["icon"] = FEATURE_ICONS.get(feature["id"], "")
        for up in feature.get("uploads", []):
            up.setdefault("hint", _UPLOAD_HINTS.get(up.get("accept", ""), "支援常見格式"))
            # video slots also support 方式二：從網址下載 (YouTube / TikTok / FB ...).
            if up.get("accept", "").startswith("video/"):
                up.setdefault("urlFetch", True)
        cfg = _PROMPT_FIELD_CONFIG.get(feature["id"])
        for field in feature.get("fields", []):
            fid = field.get("id")
            ftype = field.get("type")
            if ftype == "select":
                # Normalize bare-string options to {value,label} so the frontend
                # renders them (and so cards can attach a sub-line).
                field["options"] = [
                    opt if isinstance(opt, dict) else {"value": opt, "label": opt}
                    for opt in field.get("options", [])
                ]
                remap = _CARD_FIELDS.get((feature["id"], fid))
                if (feature["id"], fid) in _CARD_FIELDS:
                    field["display"] = "cards"
                    for opt in field["options"]:
                        friendly = (remap or {}).get(opt.get("value"))
                        if friendly:
                            opt["label"], opt["sub"] = friendly
            if fid in ADVANCED_FIELD_IDS:
                field["advanced"] = True
            if ftype == "slider" and fid in _SLIDER_LABELS:
                lo, hi = _SLIDER_LABELS[fid]
                field.setdefault("minLabel", lo)
                field.setdefault("maxLabel", hi)
            if cfg and fid == cfg[0] and ftype == "textarea":
                if cfg[1]:
                    field["presets"] = cfg[1]
                field["assist"] = cfg[2]
                field.setdefault("hint", _ASSIST_HINTS.get(cfg[2]))


_enrich_features()

MODEL_MIN_BYTES = {
    "z_image_turbo_bf16.safetensors": 11_000_000_000,
    "qwen_3_4b.safetensors": 7_000_000_000,
    "ae.safetensors": 100_000_000,
    "flux1-dev-kontext_fp8_scaled.safetensors": 10_000_000_000,
    "clip_l.safetensors": 100_000_000,
    "t5xxl_fp8_e4m3fn_scaled.safetensors": 1_500_000_000,
    "sam3.1_multiplex_fp16.safetensors": 500_000_000,
    "clip_vision_h.safetensors": 1_000_000_000,
    "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors": 10_000_000_000,
    "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors": 500_000_000,
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors": 5_000_000_000,
    "wan_2.1_vae.safetensors": 100_000_000,
}

# Exact upstream sizes are used where a plausible-looking partial download has
# already caused a false-positive readiness check.  Safetensors metadata is
# also validated below, but the exact byte count catches a truncated payload
# before the model ever reaches ComfyUI.
MODEL_EXPECTED_BYTES = {
    **QWEN21_MODEL_BYTES,
    "t5xxl_fp8_e4m3fn_scaled.safetensors": 5_157_348_688,
}
_MODEL_INTEGRITY_CACHE: dict[tuple[str, int, int], bool] = {}
_MODEL_INTEGRITY_LOCK = threading.Lock()
_SAFETENSORS_HEADER_MAX_BYTES = 256 * 1024 * 1024


class RequestTooLarge(Exception):
    pass


class RequestBodyError(ValueError):
    """The HTTP request body framing or contents are invalid."""


class ClientDisconnected(ConnectionError):
    """The peer disconnected before its declared request body arrived."""


def request_json(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    url = f"{DEFAULT_COMFY}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def safe_filename(name: str, fallback_ext: str = "") -> str:
    stem = Path(name or "upload").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    if not stem:
        stem = f"upload{fallback_ext}"
    if "." not in stem and fallback_ext:
        stem += fallback_ext
    return stem[:140]


def decode_data_url(value: str, max_bytes: int = MAX_UPLOAD_DECODED) -> tuple[bytes, str]:
    if not value.startswith("data:") or "," not in value:
        raise ValueError("Upload must be a data URL")
    header, encoded = value.split(",", 1)
    if ";base64" not in header:
        raise ValueError("Upload must be base64-encoded")
    content_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    # 4 base64 chars encode 3 bytes; reject before decoding if clearly oversized.
    if len(encoded) // 4 * 3 > max_bytes:
        raise RequestTooLarge("Upload exceeds size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("Invalid base64 upload") from error
    if len(raw) > max_bytes:
        raise RequestTooLarge("Upload exceeds size limit")
    return raw, content_type


def upload_image_to_comfy(raw: bytes, name: str, content_type: str) -> str:
    boundary = f"----CastleComfy{secrets.token_hex(16)}"
    safe_name = safe_filename(name, mimetypes.guess_extension(content_type) or ".png")
    parts = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n"
        ).encode("utf-8"),
        raw,
        (
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="type"\r\n\r\n'
            "input"
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
            "false"
            f"\r\n--{boundary}--\r\n"
        ).encode("utf-8"),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{DEFAULT_COMFY}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    subfolder = result.get("subfolder") or ""
    filename = result.get("name") or safe_name
    return f"{subfolder}/{filename}" if subfolder else filename


def save_uploaded_bytes(raw: bytes, name: str, content_type: str, slot: str) -> str:
    if len(raw) > MAX_UPLOAD_DECODED:
        raise RequestTooLarge("Upload exceeds size limit")
    content_type = str(content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    safe_slot = re.sub(r"[^a-z0-9_-]+", "", str(slot or "").lower())[:32] or "file"
    if content_type.startswith("image/"):
        return upload_image_to_comfy(raw, name, content_type)
    guessed = mimetypes.guess_extension(content_type) or Path(str(name or "")).suffix
    safe_name = safe_filename(name, guessed)
    target_name = f"castle_web_{int(time.time())}_{secrets.token_hex(4)}_{safe_slot}_{safe_name}"
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    target = (COMFY_INPUT / target_name).resolve()
    if not target.is_relative_to(COMFY_INPUT.resolve()):
        raise ValueError("Invalid upload path")
    target.write_bytes(raw)
    return target_name


def save_uploaded_file(file_info: dict, slot: str) -> str:
    raw, content_type = decode_data_url(str(file_info.get("dataUrl") or ""))
    return save_uploaded_bytes(raw, str(file_info.get("name") or ""), content_type, slot)


def sniff_experimental_image(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValueError("不支援的圖片格式")


def experimental_total_bytes_for_user(email: str) -> int:
    slug = experimental_email_slug(email)
    root = CHARACTER_USER_ASSET_ROOT / slug
    total = 0
    try:
        paths = list(root.rglob("*"))
    except OSError:
        return 0
    for path in paths:
        try:
            if path.is_file() and path.resolve().is_relative_to(CHARACTER_USER_ASSET_ROOT.resolve()):
                total += path.stat().st_size
        except OSError:
            continue
    return total


def import_user_photo_to_input(entry: dict, filename: str, slot: str) -> dict:
    source = experimental_user_asset_path(entry, filename)
    if source is None:
        raise ValueError("找不到角色照片")
    size = source.stat().st_size
    if size > MAX_UPLOAD_BODY:
        raise ValueError("素材檔太大，請先裁切後再使用")
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(source.name, source.suffix)
    slot_name = re.sub(r"[^a-z0-9_-]+", "", str(slot or "").lower())[:32] or "photo"
    target_name = f"castle_user_photo_{int(time.time())}_{secrets.token_hex(4)}_{slot_name}_{safe_name}"
    target = (COMFY_INPUT / target_name).resolve()
    if not target.is_relative_to(COMFY_INPUT.resolve()):
        raise ValueError("無效的角色照片檔名")
    shutil.copyfile(source, target)
    return {"filename": target_name, "name": source.name, "size": size, "kind": "image"}


def resolve_voice_reference(
    voice_id: str,
    user: dict | None = None,
    service_token: bool = False,
) -> str:
    """Copy a Voice Library entry into ComfyUI's input dir so LoadAudio can read
    it, returning the bare filename."""
    with _VOICE_LOCK:
        voices = load_voice_index()
        entry = next((e for e in voices if e["id"] == voice_id), None)
    if entry is None:
        available = ", ".join(str(e.get("id") or "") for e in voices[:8] if e.get("id"))
        suffix = f"；目前可用：{available}" if available else "；目前聲音庫是空的"
        raise ValueError(f"找不到聲音庫項目「{voice_id}」{suffix}")
    if not user_can_access_voice_entry(user, entry, service_token):
        raise PermissionError("沒有權限使用這個聲音")
    source = VOICE_LIBRARY / entry["filename"]
    if not source.is_file():
        raise ValueError("聲音庫檔案遺失")
    target_name = f"castle_voice_{int(time.time())}_{secrets.token_hex(4)}_{entry['filename']}"
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    (COMFY_INPUT / target_name).write_bytes(source.read_bytes())
    return target_name


def _resolve_yt_dlp() -> str:
    """Path to the yt-dlp binary. It is a uv-tool shim in ~/.local/bin and is NOT
    on the systemd service PATH, so we probe explicit candidates."""
    for candidate in YT_DLP_CANDIDATES:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("找不到 yt-dlp（請確認 castle_fb_crawl 已安裝）")


def _crawl_cookie_file(url: str) -> Path | None:
    """Mirror grab.sh's host -> cookies/<site>.txt mapping. Returns an existing
    cookie file for the URL's host, or None. Cookie *contents* are never read or
    logged here — only the path is handed to yt-dlp."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if "facebook.com" in host or "fb.watch" in host:
        name = "facebook.txt"
    elif "instagram.com" in host:
        name = "instagram.txt"
    elif "threads.com" in host or "threads.net" in host:
        name = "threads.txt"
    elif "youtube.com" in host or "youtu.be" in host:
        name = "youtube.txt"
    elif "drive.google.com" in host or "googleusercontent.com" in host:
        name = "google.txt"
    else:
        name = "cookies.txt"
    path = CRAWL_COOKIES / name
    return path if path.is_file() else None


def _google_drive_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == "drive.google.com" or host.endswith(".drive.google.com")


def fetch_video_url(url: str, slot: str) -> dict:
    """Download a single video from a public URL into COMFY_INPUT via yt-dlp,
    returning {filename, name, size}. yt-dlp runs as an argv list (never a shell),
    only http/https URLs are accepted, and the download is size- and time-capped.
    The saved file is a bare name inside COMFY_INPUT, so from here it is treated
    exactly like an uploaded clip."""
    url = (url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("請貼上有效的 http / https 影片網址")
    binary = _resolve_yt_dlp()
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    token = f"{int(time.time())}_{secrets.token_hex(4)}_{slot}"
    out_tmpl = str(COMFY_INPUT / f"castle_url_{token}_%(title).60s.%(ext)s")
    argv = [
        binary,
        "--no-playlist",
        "--restrict-filenames",
        "--no-progress",
        "--max-filesize", str(MAX_FETCH_BYTES),
        "-f", "mp4/best[ext=mp4]/best",
        "-o", out_tmpl,
    ]
    cookie = _crawl_cookie_file(url)
    if cookie is not None:
        argv += ["--cookies", str(cookie)]
    argv += ["--", url]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=FETCH_TIMEOUT_SECONDS, cwd=str(CRAWL_DIR),
        )
    except subprocess.TimeoutExpired:
        raise ValueError("下載逾時，請改用較短的影片或直接上傳檔案")
    if proc.returncode != 0:
        # Surface a short, non-sensitive reason; never echo cookies or the argv.
        lines = [ln for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
        reason = lines[-1].strip() if lines else f"yt-dlp 失敗（{proc.returncode}）"
        raise ValueError(f"下載失敗：{reason[:200]}")
    matches = [p for p in COMFY_INPUT.glob(f"castle_url_{token}_*") if p.is_file()]
    if not matches:
        raise ValueError("下載完成但找不到影片檔，請改用上傳檔案")
    target = max(matches, key=lambda p: p.stat().st_mtime)
    size = target.stat().st_size
    if size > MAX_FETCH_BYTES:
        try:
            target.unlink()
        except OSError:
            pass
        raise ValueError("影片超過大小上限")
    if _video_codec(target) not in CV2_VIDEO_CODECS:
        final_target = target.with_suffix(".mp4")
        if final_target == target:
            final_target = target.with_name(f"{target.stem}_cv2.mp4")
        ensure_cv2_compatible_video(target, final_target)
        try:
            target.unlink()
        except OSError:
            pass
        target = final_target
        size = target.stat().st_size
        if size > MAX_FETCH_BYTES:
            try:
                target.unlink()
            except OSError:
                pass
            raise ValueError("影片超過大小上限")
    display = target.name
    prefix = f"castle_url_{token}_"
    if display.startswith(prefix):
        display = display[len(prefix):]
    return {"filename": target.name, "name": display, "size": size}


def fetch_google_drive_video(url: str, slot: str) -> dict:
    url = (url or "").strip()
    if not _google_drive_url(url):
        raise ValueError("請貼上 Google Drive 檔案分享連結")
    return fetch_video_url(url, slot)


def google_drive_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def load_google_token() -> dict | None:
    try:
        data = json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_google_token(data: dict) -> None:
    GOOGLE_TOKEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    GOOGLE_TOKEN_PATH.chmod(0o600)


def google_token_connected() -> bool:
    token = load_google_token()
    return bool(token and (token.get("access_token") or token.get("refresh_token")))


def request_form_json(url: str, form: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"Google OAuth 失敗：{detail}") from error
    return json.loads(raw.decode("utf-8")) if raw else {}


def refresh_google_token(token: dict) -> dict:
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        raise ValueError("Google Drive 尚未授權或 refresh token 遺失，請重新登入")
    data = request_form_json(GOOGLE_TOKEN_URL, {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    now = int(time.time())
    token.update(data)
    token["refresh_token"] = refresh_token
    token["expires_at"] = now + int(data.get("expires_in") or 3600) - 60
    save_google_token(token)
    return token


def google_access_token() -> str:
    if not google_drive_configured():
        raise ValueError("Google OAuth 尚未設定：請設定 CASTLE_GOOGLE_CLIENT_ID / CASTLE_GOOGLE_CLIENT_SECRET")
    token = load_google_token()
    if not token:
        raise ValueError("Google Drive 尚未登入")
    if int(token.get("expires_at") or 0) <= int(time.time()):
        token = refresh_google_token(token)
    access = str(token.get("access_token") or "")
    if not access:
        raise ValueError("Google Drive access token 遺失，請重新登入")
    return access


def google_api_json(path: str, params: dict | None = None) -> dict:
    query = urllib.parse.urlencode(params or {})
    url = f"{GOOGLE_DRIVE_API}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {google_access_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"Google Drive API 失敗：{detail}") from error
    return json.loads(raw.decode("utf-8")) if raw else {}


def google_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_google_drive_videos(search: str = "", limit: int = 50) -> list[dict]:
    terms = ["trashed = false", "mimeType contains 'video/'"]
    q = (search or "").strip()
    if q:
        terms.append(f"name contains '{google_query_literal(q[:80])}'")
    data = google_api_json("/files", {
        "pageSize": max(1, min(limit, 100)),
        "orderBy": "modifiedTime desc",
        "q": " and ".join(terms),
        "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    })
    files = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        files.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or "Drive video"),
            "mimeType": str(item.get("mimeType") or ""),
            "size": int(item.get("size") or 0),
            "modifiedTime": str(item.get("modifiedTime") or ""),
            "webViewLink": str(item.get("webViewLink") or ""),
        })
    return [item for item in files if item["id"]]


def google_drive_file_meta(file_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", file_id or ""):
        raise ValueError("無效的 Google Drive file id")
    return google_api_json(f"/files/{urllib.parse.quote(file_id)}", {
        "fields": "id,name,mimeType,size,modifiedTime",
        "supportsAllDrives": "true",
    })


def import_google_drive_file(file_id: str, slot: str) -> dict:
    meta = google_drive_file_meta(file_id)
    name = str(meta.get("name") or "drive_video")
    mime_type = str(meta.get("mimeType") or "")
    suffix = Path(name).suffix.lower()
    if not mime_type.startswith("video/") and suffix not in CRAWL_VIDEO_EXTS:
        raise ValueError("目前只支援從 Google Drive 匯入影片檔")
    size = int(meta.get("size") or 0)
    if size and size > MAX_FETCH_BYTES:
        raise ValueError("Google Drive 影片超過大小上限，請先裁切後再使用")
    safe_name = safe_filename(name, suffix or ".mp4")
    target_name = f"castle_drive_{int(time.time())}_{secrets.token_hex(4)}_{slot}_{safe_name}"
    target = COMFY_INPUT / target_name
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    url = f"{GOOGLE_DRIVE_API}/files/{urllib.parse.quote(file_id)}?" + urllib.parse.urlencode({
        "alt": "media",
        "supportsAllDrives": "true",
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {google_access_token()}"})
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as response, target.open("wb") as out:
            while True:
                chunk = response.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_FETCH_BYTES:
                    raise ValueError("Google Drive 影片超過大小上限，已中止下載")
                out.write(chunk)
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return {"filename": target_name, "name": name, "size": written or size, "source": file_id}


def _crawl_video_root() -> Path:
    return CRAWL_VIDEO_ROOT.expanduser().resolve()


def _crawl_rel_path(raw: str) -> Path:
    rel = Path(str(raw or ""))
    if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError("無效的影片路徑")
    return rel


def resolve_crawl_video_path(raw: str) -> Path:
    root = _crawl_video_root()
    rel = _crawl_rel_path(raw)
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("找不到這個 fb_crawl 影片")
    if path.suffix.lower() not in CRAWL_VIDEO_EXTS:
        raise ValueError("不支援的影片格式")
    return path


CV2_VIDEO_CODECS = {"h264", "mpeg4", "mjpeg"}


def _video_codec(path: Path) -> str:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip().splitlines()[0].strip().lower() if (proc.stdout or "").strip() else ""


def ensure_cv2_compatible_video(source: Path, target: Path) -> None:
    codec = _video_codec(source)
    if codec in CV2_VIDEO_CODECS:
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return
    if target.suffix.lower() != ".mp4":
        target = target.with_suffix(".mp4")
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v", "error",
                "-i", str(source),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("影片轉檔失敗，這支影片的格式暫時無法使用") from error
    if proc.returncode != 0:
        raise ValueError("影片轉檔失敗，這支影片的格式暫時無法使用")


def orchestrator_adopt_source_allowed(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    for root in ORCH_ADOPT_ALLOWED_ROOTS:
        try:
            base = root.expanduser().resolve()
        except OSError:
            continue
        if resolved.is_relative_to(base):
            return True
    return False


def orchestrator_adopt_source_label(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("source_path") or raw.get("sourcePath") or raw.get("filename") or "")
    return ""


def normalize_orchestrator_adopt_body(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")
    body = dict(body)
    if not (body.get("source_path") or body.get("sourcePath")) and body.get("filename"):
        filename, subfolder, view_type = validate_view_parts(
            str(body.get("filename") or ""),
            str(body.get("subfolder") or ""),
            str(body.get("type") or "output"),
        )
        target = resolve_view_path(filename, subfolder, view_type)
        if target is None or not target.is_file():
            raise FileNotFoundError("Artifact source file not found")
        body["source_path"] = str(target)
        body.setdefault("metadata", {})
        if isinstance(body["metadata"], dict):
            body["metadata"].update({
                "source": "castle-gallery",
                "filename": filename,
                "subfolder": subfolder,
                "type": view_type,
                "promptId": str(body.get("promptId") or ""),
                "workflow": str(body.get("workflow") or ""),
                "feature": str(body.get("feature") or ""),
            })
        return body

    source = str(body.get("source_path") or body.get("sourcePath") or "").strip()
    if source:
        source_path = Path(source).expanduser().resolve()
        if not orchestrator_adopt_source_allowed(source_path):
            raise PermissionError("Artifact source path is outside allowed roots")
        body["source_path"] = str(source_path)
    return body


def normalize_orchestrator_adopt_batch_body(body: dict) -> tuple[dict, list[dict]]:
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")
    raw_items = body.get("items") or body.get("artifacts") or body.get("source_paths") or body.get("sourcePaths")
    if not isinstance(raw_items, list) or not raw_items:
        return dict(body), []
    if len(raw_items) > 200:
        return dict(body), []

    items = []
    errors = []
    for index, raw in enumerate(raw_items):
        try:
            if isinstance(raw, str):
                item = {"source_path": raw}
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                raise ValueError("adopt batch items must be objects or source path strings")
            item = normalize_orchestrator_adopt_body(item)
            item["_batch_index"] = index
            items.append(item)
        except (FileNotFoundError, PermissionError, ValueError, OSError, RuntimeError) as error:
            errors.append({"index": index, "source_path": orchestrator_adopt_source_label(raw), "error": str(error)})

    normalized = dict(body)
    normalized["items"] = items
    return normalized, errors


def crawl_video_public_item(path: Path, root: Path) -> dict | None:
    if path.suffix.lower() not in CRAWL_VIDEO_EXTS:
        return None
    try:
        stat = path.stat()
        rel = path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    return {
        "id": rel,
        "name": path.name,
        "folder": "" if Path(rel).parent == Path(".") else Path(rel).parent.as_posix(),
        "size": stat.st_size,
        "modified": stat.st_mtime,
    }


def list_crawl_videos(query: str = "", limit: int = MAX_CRAWL_VIDEO_ITEMS) -> list[dict]:
    root = _crawl_video_root()
    if not root.is_dir():
        return []
    q = (query or "").strip().lower()
    items = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CRAWL_VIDEO_EXTS:
            continue
        item = crawl_video_public_item(path, root)
        if not item:
            continue
        haystack = f"{item['id']} {item['name']} {item['folder']}".lower()
        if q and q not in haystack:
            continue
        items.append(item)
    items.sort(key=lambda item: (item.get("folder", ""), -float(item.get("modified") or 0), item.get("name", "")))
    return items[: max(1, min(limit, MAX_CRAWL_VIDEO_ITEMS))]


def import_crawl_video(video_id: str, slot: str) -> dict:
    source = resolve_crawl_video_path(video_id)
    size = source.stat().st_size
    if size > MAX_FETCH_BYTES:
        raise ValueError("影片超過大小上限，請先裁切後再使用")
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    codec = _video_codec(source)
    suffix = source.suffix if codec in CV2_VIDEO_CODECS else ".mp4"
    safe_name = safe_filename(source.with_suffix(suffix).name, suffix)
    target_name = f"castle_crawl_{int(time.time())}_{secrets.token_hex(4)}_{slot}_{safe_name}"
    target = COMFY_INPUT / target_name
    ensure_cv2_compatible_video(source, target)
    return {"filename": target.name, "name": source.name, "size": target.stat().st_size, "source": video_id}


class DanceLibraryBusy(RuntimeError):
    """Raised when the bounded dance-media preparation pool is occupied."""


def dance_library_source_roots() -> dict[str, Path]:
    """Return the only roots the persisted catalog is allowed to reference."""
    return {
        "curated": DANCE_TEMPLATE_ROOT.expanduser().resolve(),
        "crawl": _crawl_video_root(),
    }


def _empty_dance_library_catalog() -> dict:
    return {
        "version": DANCE_LIBRARY_SCHEMA_VERSION,
        "revision": 0,
        "scannedAt": 0.0,
        "updated": 0.0,
        "categories": [
            {"id": "dance", "title": "舞蹈", "rank": 100},
            {"id": "workout", "title": "健身", "rank": 200},
            {"id": "uncategorized", "title": "待分類", "rank": 900},
        ],
        "templates": {},
        "aliases": {},
    }


def _normalize_dance_library_catalog(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("舞步模板目錄格式不正確")
    version = int(data.get("version") or 0)
    if version != DANCE_LIBRARY_SCHEMA_VERSION:
        raise ValueError("舞步模板目錄版本不支援")
    templates = data.get("templates")
    aliases = data.get("aliases")
    categories = data.get("categories")
    if not isinstance(templates, dict) or not isinstance(aliases, dict) or not isinstance(categories, list):
        raise ValueError("舞步模板目錄缺少必要欄位")
    clean = dict(data)
    clean["revision"] = max(0, int(data.get("revision") or 0))
    clean["scannedAt"] = float(data.get("scannedAt") or 0)
    clean["updated"] = float(data.get("updated") or 0)
    clean["templates"] = {
        str(template_id): dict(record)
        for template_id, record in templates.items()
        if DANCE_LIBRARY_ID_RE.fullmatch(str(template_id)) and isinstance(record, dict)
    }
    clean["aliases"] = {
        str(old_id): str(template_id)
        for old_id, template_id in aliases.items()
        if DANCE_LIBRARY_ID_RE.fullmatch(str(old_id))
        and DANCE_LIBRARY_ID_RE.fullmatch(str(template_id))
    }
    clean["categories"] = [
        {
            "id": str(item.get("id") or "")[:32],
            "title": str(item.get("title") or item.get("id") or "")[:80],
            "rank": max(0, int(item.get("rank") or 0)),
        }
        for item in categories
        if isinstance(item, dict) and re.fullmatch(r"[a-z0-9_-]{1,32}", str(item.get("id") or ""))
    ]
    return clean


def _load_dance_library_catalog_unlocked() -> dict:
    try:
        raw = json.loads(DANCE_LIBRARY_CATALOG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("舞步模板目錄無法讀取；原檔未被修改") from error
    return _normalize_dance_library_catalog(raw)


def _save_dance_library_catalog_unlocked(catalog: dict) -> None:
    DANCE_LIBRARY_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        DANCE_LIBRARY_CATALOG_PATH.parent.chmod(0o700)
    except OSError:
        pass
    atomic_write_json(DANCE_LIBRARY_CATALOG_PATH, catalog, 0o600)


def load_dance_library_catalog(rescan_if_missing: bool = True) -> dict:
    with _DANCE_LIBRARY_LOCK:
        try:
            return _load_dance_library_catalog_unlocked()
        except FileNotFoundError:
            if not rescan_if_missing:
                return _empty_dance_library_catalog()
    return rescan_dance_library_catalog()


def _dance_library_group_stem(path: Path) -> str:
    return re.sub(r"\.source-(?:av1|vp9)$", "", path.stem, flags=re.IGNORECASE)


def _dance_library_group_key(root_name: str, relative_path: str) -> str:
    rel = Path(relative_path)
    stem = _dance_library_group_stem(rel)
    parent = "" if rel.parent == Path(".") else rel.parent.as_posix().lower()
    return f"{root_name}:{parent}:{stem.casefold()}"


def _dance_library_friendly_title(name: str) -> str:
    stem = _dance_library_group_stem(Path(name))
    stem = re.sub(r"\s*\[[^\]]{2,80}\]\s*$", "", stem)
    stem = re.sub(
        r"^(?:\d+(?:\.\d+)?[KMB]?[_ ]+views?[_ ]+)?(?:\d+(?:\.\d+)?[KMB]?[_ ]+reactions?[_ ]+)?",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    stem = re.sub(r"^Video[_ ]+by[_ ]+", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[_-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")
    return (stem or "舞步模板")[:120]


def _dance_library_default_classification(root_name: str, relative_path: str) -> tuple[str, str]:
    if root_name == "curated":
        return "approved", "dance"
    parts = Path(relative_path).parts
    first = parts[0].lower() if len(parts) > 1 else ""
    if first == "dance":
        return "approved", "dance"
    if first == "workout":
        return "pending", "workout"
    return "pending", "uncategorized"


def dance_library_probe_video(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"codec": "", "width": 0, "height": 0, "duration": 0.0}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,duration:format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"codec": "", "width": 0, "height": 0, "duration": 0.0}
        data = json.loads(result.stdout or "{}")
        stream = next(
            (item for item in (data.get("streams") or []) if isinstance(item, dict)),
            {},
        )
        format_data = data.get("format") if isinstance(data.get("format"), dict) else {}
        duration = stream.get("duration") or format_data.get("duration") or 0
        return {
            "codec": str(stream.get("codec_name") or "").lower()[:24],
            "width": max(0, int(stream.get("width") or 0)),
            "height": max(0, int(stream.get("height") or 0)),
            "duration": max(0.0, round(float(duration or 0), 3)),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {"codec": "", "width": 0, "height": 0, "duration": 0.0}


def _dance_library_resolve_source(root_name: str, relative_path: str, require_file: bool = True) -> Path:
    roots = dance_library_source_roots()
    root = roots.get(str(root_name or ""))
    if root is None:
        raise ValueError("舞步模板來源不受信任")
    rel = _crawl_rel_path(relative_path)
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise ValueError("舞步模板來源超出允許範圍")
    if require_file and (not path.is_file() or path.suffix.lower() not in DANCE_TEMPLATE_EXTS):
        raise ValueError("找不到舞步模板來源")
    return path


def _dance_library_variant_score(variant: dict) -> tuple:
    return (
        0 if str(variant.get("codec") or "").lower() == "h264" else 1,
        0 if str(variant.get("suffix") or "").lower() == ".mp4" else 1,
        1 if ".source-" in str(variant.get("path") or "").lower() else 0,
        -int(variant.get("width") or 0) * int(variant.get("height") or 0),
        str(variant.get("path") or "").casefold(),
    )


def _dance_library_preferred_variant(record: dict) -> dict:
    variants = [
        variant for variant in (record.get("variants") or [])
        if isinstance(variant, dict) and not variant.get("missing")
    ]
    if not variants:
        raise ValueError("舞步模板來源目前不存在")
    wanted = str(record.get("preferredAssetId") or "")
    selected = next((variant for variant in variants if variant.get("id") == wanted), None)
    return selected or min(variants, key=_dance_library_variant_score)


def _dance_library_media_version(variant: dict) -> str:
    return hashlib.sha256(
        (
            f"{variant.get('sha256')}:{variant.get('size')}:"
            f"{variant.get('mtimeNs')}:dance-library-v1"
        ).encode("utf-8")
    ).hexdigest()[:12]


def _dance_library_scan_variant(
    root_name: str,
    root: Path,
    path: Path,
    previous_variants: dict[tuple[str, str], dict],
) -> dict | None:
    try:
        stat = path.stat()
        relative_path = path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    previous = previous_variants.get((root_name, relative_path))
    unchanged = bool(
        previous
        and int(previous.get("size") or -1) == stat.st_size
        and int(previous.get("mtimeNs") or -1) == stat.st_mtime_ns
        and str(previous.get("sha256") or "")
    )
    if unchanged:
        digest = str(previous.get("sha256"))
        probe = {
            "codec": str(previous.get("codec") or ""),
            "width": int(previous.get("width") or 0),
            "height": int(previous.get("height") or 0),
            "duration": float(previous.get("duration") or 0),
        }
    else:
        try:
            digest = sha256_file(path)
        except OSError:
            return None
        probe = dance_library_probe_video(path)
    asset_id = (
        str(previous.get("id"))
        if previous and DANCE_LIBRARY_ASSET_ID_RE.fullmatch(str(previous.get("id") or ""))
        else secrets.token_hex(8)
    )
    return {
        "id": asset_id,
        "root": root_name,
        "path": relative_path,
        "name": path.name,
        "suffix": path.suffix.lower(),
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "mtimeNs": stat.st_mtime_ns,
        "sha256": digest,
        **probe,
        "missing": False,
    }


def rescan_dance_library_catalog() -> dict:
    """Reconcile trusted source roots into the persisted logical-template catalog."""
    with _DANCE_LIBRARY_LOCK:
        try:
            previous_catalog = _load_dance_library_catalog_unlocked()
        except FileNotFoundError:
            previous_catalog = _empty_dance_library_catalog()
        previous_templates = previous_catalog.get("templates") or {}
        previous_variants: dict[tuple[str, str], dict] = {}
        previous_by_group: dict[str, str] = {}
        previous_by_sha: dict[str, list[tuple[str, dict]]] = {}
        for template_id, record in previous_templates.items():
            group_key = str(record.get("groupKey") or "")
            if group_key:
                previous_by_group[group_key] = template_id
            for variant in record.get("variants") or []:
                if not isinstance(variant, dict):
                    continue
                path_key = (str(variant.get("root") or ""), str(variant.get("path") or ""))
                previous_variants[path_key] = variant
                digest = str(variant.get("sha256") or "")
                if digest:
                    previous_by_sha.setdefault(digest, []).append((template_id, variant))

        groups: dict[str, list[dict]] = {}
        discovered_paths: set[tuple[str, str]] = set()
        seen_resolved: set[str] = set()
        for root_name, root in dance_library_source_roots().items():
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in DANCE_TEMPLATE_EXTS:
                    continue
                try:
                    resolved_key = str(path.resolve())
                    relative_path = path.resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                if resolved_key in seen_resolved:
                    continue
                seen_resolved.add(resolved_key)
                variant = _dance_library_scan_variant(root_name, root, path, previous_variants)
                if variant is None:
                    continue
                discovered_paths.add((root_name, relative_path))
                group_key = _dance_library_group_key(root_name, relative_path)
                groups.setdefault(group_key, []).append(variant)

        max_rank = max(
            (int(record.get("rank") or 0) for record in previous_templates.values() if isinstance(record, dict)),
            default=0,
        )
        matched_template_ids: set[str] = set()
        templates: dict[str, dict] = {}
        aliases = dict(previous_catalog.get("aliases") or {})
        now = time.time()

        for group_key in sorted(groups):
            variants = groups[group_key]
            direct_candidates = {
                template_id
                for template_id, record in previous_templates.items()
                if any(
                    (str(variant.get("root") or ""), str(variant.get("path") or "")) in {
                        (str(old.get("root") or ""), str(old.get("path") or ""))
                        for old in (record.get("variants") or [])
                        if isinstance(old, dict)
                    }
                    for variant in variants
                )
            }
            template_id = next(iter(sorted(direct_candidates)), "")
            if not template_id:
                candidate = previous_by_group.get(group_key, "")
                if candidate and candidate not in matched_template_ids:
                    template_id = candidate
            if not template_id:
                for variant in variants:
                    for candidate, old_variant in previous_by_sha.get(str(variant.get("sha256") or ""), []):
                        old_key = (str(old_variant.get("root") or ""), str(old_variant.get("path") or ""))
                        if (
                            candidate not in matched_template_ids
                            and old_key not in discovered_paths
                        ):
                            template_id = candidate
                            variant["id"] = str(old_variant.get("id") or variant["id"])
                            break
                    if template_id:
                        break
            previous = (
                dict(previous_templates.get(template_id) or {})
                if template_id and template_id in previous_templates
                else {}
            )
            if not template_id:
                template_id = secrets.token_hex(12)
                max_rank += 100
            matched_template_ids.add(template_id)

            old_variants_by_path = {
                (str(item.get("root") or ""), str(item.get("path") or "")): item
                for item in (previous.get("variants") or [])
                if isinstance(item, dict)
            }
            old_variants_by_sha = {
                str(item.get("sha256") or ""): item
                for item in (previous.get("variants") or [])
                if isinstance(item, dict) and item.get("sha256")
            }
            for variant in variants:
                old_variant = old_variants_by_path.get((variant["root"], variant["path"]))
                if old_variant is None:
                    old_variant = old_variants_by_sha.get(str(variant.get("sha256") or ""))
                if old_variant and DANCE_LIBRARY_ASSET_ID_RE.fullmatch(str(old_variant.get("id") or "")):
                    variant["id"] = str(old_variant["id"])
            variants.sort(key=_dance_library_variant_score)
            preferred = next(
                (
                    variant for variant in variants
                    if variant.get("id") == previous.get("preferredAssetId")
                ),
                variants[0],
            )
            default_status, default_category = _dance_library_default_classification(
                str(preferred.get("root") or ""),
                str(preferred.get("path") or ""),
            )
            previous_status = str(previous.get("status") or default_status)
            problem = ""
            if (
                not str(preferred.get("codec") or "")
                or int(preferred.get("width") or 0) <= 0
                or int(preferred.get("height") or 0) <= 0
            ):
                status = "problem"
                problem = "probe_failed"
                previous_status_for_recovery = (
                    str(previous.get("previousStatus") or "")
                    if previous_status == "problem"
                    else previous_status
                )
            elif previous_status == "problem" and str(previous.get("problem") or "") in {
                "source_missing",
                "probe_failed",
            }:
                status = str(previous.get("previousStatus") or default_status)
                previous_status_for_recovery = ""
            else:
                status = previous_status
                previous_status_for_recovery = str(previous.get("previousStatus") or "")
            if status not in DANCE_LIBRARY_STATUSES:
                status = default_status
            compatible_types = [
                value for value in (
                    previous.get("compatibleCharacterTypes")
                    or previous.get("compatibleTypes")
                    or ["anime"]
                )
                if value in DANCE_LIBRARY_CHARACTER_TYPES
            ]
            if not compatible_types:
                compatible_types = ["anime"]
            record = {
                "id": template_id,
                "groupKey": group_key,
                "title": str(previous.get("title") or _dance_library_friendly_title(str(preferred.get("name") or "")))[:120],
                "category": str(previous.get("category") or default_category)[:32],
                "tags": [
                    str(tag)[:32]
                    for tag in (previous.get("tags") or [])
                    if str(tag).strip()
                ][:20],
                "status": status,
                "problem": problem,
                "previousStatus": previous_status_for_recovery,
                "compatibleCharacterTypes": compatible_types,
                "compatibleTypes": compatible_types,
                "featured": bool(previous.get("featured", False)),
                "favorite": bool(previous.get("favorite", False)),
                "rank": max(0, int(previous.get("rank") or max_rank)),
                "preferredAssetId": str(preferred.get("id") or ""),
                "variants": variants,
                "missing": False,
                "useCount": max(0, int(previous.get("useCount") or 0)),
                "lastUsed": float(previous.get("lastUsed") or 0),
                "created": float(previous.get("created") or now),
                "updated": now,
            }
            templates[template_id] = record
            for variant in variants:
                if variant.get("root") == "curated":
                    try:
                        legacy_id = _dance_template_id(
                            _dance_library_resolve_source("curated", str(variant.get("path") or "")),
                            dance_library_source_roots()["curated"],
                        )
                        if legacy_id != template_id:
                            aliases[legacy_id] = template_id
                    except (OSError, ValueError):
                        pass

        for template_id, previous in previous_templates.items():
            if template_id in matched_template_ids or not isinstance(previous, dict):
                continue
            missing = dict(previous)
            missing["missing"] = True
            if str(previous.get("status") or "") != "problem":
                missing["previousStatus"] = str(previous.get("status") or "pending")
            missing["status"] = "problem"
            missing["problem"] = "source_missing"
            missing["updated"] = now
            missing["variants"] = [
                {**variant, "missing": True}
                for variant in (previous.get("variants") or [])
                if isinstance(variant, dict)
            ]
            templates[template_id] = missing

        catalog = {
            "version": DANCE_LIBRARY_SCHEMA_VERSION,
            "revision": int(previous_catalog.get("revision") or 0) + 1,
            "scannedAt": now,
            "updated": now,
            "categories": previous_catalog.get("categories") or _empty_dance_library_catalog()["categories"],
            "templates": templates,
            "aliases": {
                old_id: template_id
                for old_id, template_id in aliases.items()
                if template_id in templates and old_id != template_id
            },
        }
        _save_dance_library_catalog_unlocked(catalog)
        return catalog


def resolve_dance_library_template(template_id: str, include_missing: bool = False) -> tuple[dict, dict]:
    wanted = str(template_id or "").strip().lower()
    if not DANCE_LIBRARY_ID_RE.fullmatch(wanted):
        raise ValueError("無效的舞步模板")
    catalog = load_dance_library_catalog()
    resolved_id = str((catalog.get("aliases") or {}).get(wanted) or wanted)
    record = (catalog.get("templates") or {}).get(resolved_id)
    if not isinstance(record, dict):
        raise ValueError("找不到舞步模板")
    if record.get("missing") and not include_missing:
        raise ValueError("舞步模板來源目前不存在")
    return catalog, record


def user_can_access_dance_library_template(user: dict | None, record: dict) -> bool:
    if is_operator_user(user):
        return True
    return bool(record.get("status") == "approved" and not record.get("missing"))


def _dance_library_template_public_item(record: dict, manage: bool = False) -> dict:
    variants = [
        variant for variant in (record.get("variants") or [])
        if isinstance(variant, dict)
    ]
    available = [variant for variant in variants if not variant.get("missing")]
    preferred = None
    if available:
        try:
            preferred = _dance_library_preferred_variant(record)
        except ValueError:
            preferred = available[0]
    template_id = str(record.get("id") or "")
    public_variants = []
    for variant in variants if manage else available:
        item = {
            "id": str(variant.get("id") or ""),
            "codec": str(variant.get("codec") or ""),
            "width": int(variant.get("width") or 0),
            "height": int(variant.get("height") or 0),
            "duration": float(variant.get("duration") or 0),
            "preferred": bool(preferred and variant.get("id") == preferred.get("id")),
        }
        if manage:
            item.update({
                "root": str(variant.get("root") or ""),
                "path": str(variant.get("path") or ""),
                "name": str(variant.get("name") or ""),
                "size": int(variant.get("size") or 0),
                "modified": float(variant.get("modified") or 0),
                "sha256": str(variant.get("sha256") or ""),
                "missing": bool(variant.get("missing")),
            })
        public_variants.append(item)
    runtime_ready = bool(
        preferred
        and _dance_library_cache_path(record, preferred, "runtime").is_file()
    )
    compatible_types = list(
        record.get("compatibleCharacterTypes")
        or record.get("compatibleTypes")
        or []
    )
    media_version = _dance_library_media_version(preferred) if preferred else ""
    version_query = f"?v={media_version}" if media_version else ""
    item = {
        "id": template_id,
        "title": str(record.get("title") or "舞步模板"),
        "category": str(record.get("category") or "uncategorized"),
        "tags": list(record.get("tags") or []),
        "status": str(record.get("status") or "pending"),
        "compatibleCharacterTypes": compatible_types,
        "compatibleTypes": compatible_types,
        "duration": float((preferred or {}).get("duration") or 0),
        "width": int((preferred or {}).get("width") or 0),
        "height": int((preferred or {}).get("height") or 0),
        "featured": bool(record.get("featured")),
        "favorite": bool(record.get("favorite")),
        "rank": int(record.get("rank") or 0),
        "useCount": int(record.get("useCount") or 0),
        "lastUsed": float(record.get("lastUsed") or 0),
        "storyboardUrl": f"/api/dance-library/{template_id}/storyboard{version_query}",
        "previewUrl": f"/api/dance-library/{template_id}/preview{version_query}",
        "fileUrl": f"/api/dance-library/{template_id}/file{version_query}",
        "runtimeReady": runtime_ready,
        "variants": public_variants,
    }
    if manage:
        item.update({
            "missing": bool(record.get("missing")),
            "problem": str(record.get("problem") or ""),
            "preferredAssetId": str(record.get("preferredAssetId") or ""),
            "groupKey": str(record.get("groupKey") or ""),
            "created": float(record.get("created") or 0),
            "updated": float(record.get("updated") or 0),
        })
    return item


def list_dance_library(
    character_type: str = "",
    manage: bool = False,
    user: dict | None = None,
) -> dict:
    del user  # Favorite is currently the operator-managed shared catalog state.
    catalog = load_dance_library_catalog()
    wanted_type = str(character_type or "").strip().lower()
    if wanted_type and wanted_type not in DANCE_LIBRARY_CHARACTER_TYPES:
        raise ValueError("不支援的角色類型")
    items = []
    for record in (catalog.get("templates") or {}).values():
        if not isinstance(record, dict):
            continue
        if not manage:
            if record.get("status") != "approved" or record.get("missing"):
                continue
            compatible_types = (
                record.get("compatibleCharacterTypes")
                or record.get("compatibleTypes")
                or []
            )
            if wanted_type and wanted_type not in compatible_types:
                continue
        items.append(_dance_library_template_public_item(record, manage=manage))
    status_order = {"approved": 0, "pending": 1, "hidden": 2}
    items.sort(key=lambda item: (
        status_order.get(item.get("status"), 9) if manage else 0,
        0 if item.get("featured") else 1,
        int(item.get("rank") or 0),
        str(item.get("title") or "").casefold(),
        str(item.get("id") or ""),
    ))
    counts: dict[str, int] = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    categories = [
        {
            "id": str(category.get("id") or ""),
            "title": str(category.get("title") or category.get("id") or ""),
            "rank": int(category.get("rank") or 0),
            "count": counts.get(str(category.get("id") or ""), 0),
        }
        for category in (catalog.get("categories") or [])
        if isinstance(category, dict)
    ]
    categories.sort(key=lambda item: (item["rank"], item["title"]))
    return {
        "revision": int(catalog.get("revision") or 0),
        "scannedAt": float(catalog.get("scannedAt") or 0),
        "categories": categories,
        "templates": items,
    }


def update_dance_library(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("請提供舞步模板設定")
    nested_patch = body.get("patch")
    if nested_patch is not None and not isinstance(nested_patch, dict):
        raise ValueError("patch 必須是物件")
    update_body = {
        key: value
        for key, value in body.items()
        if key not in {"id", "ids", "patch"}
    }
    if isinstance(nested_patch, dict):
        update_body.update(nested_patch)
    allowed_update_fields = {
        "title",
        "category",
        "categoryTitle",
        "tags",
        "status",
        "compatibleCharacterTypes",
        "compatibleTypes",
        "featured",
        "favorite",
        "rank",
        "preferredAssetId",
    }
    unknown_fields = sorted(set(update_body) - allowed_update_fields)
    if unknown_fields:
        raise ValueError(f"不支援的更新欄位：{', '.join(unknown_fields)}")
    raw_ids = body.get("ids")
    if raw_ids is None:
        raw_ids = [body.get("id")]
    if not isinstance(raw_ids, list):
        raise ValueError("ids 必須是陣列")
    ids = []
    for raw_id in raw_ids:
        value = str(raw_id or "").strip().lower()
        if not DANCE_LIBRARY_ID_RE.fullmatch(value):
            raise ValueError("無效的舞步模板")
        if value not in ids:
            ids.append(value)
    if not ids or len(ids) > 200:
        raise ValueError("請選擇 1 到 200 個舞步模板")

    with _DANCE_LIBRARY_LOCK:
        catalog = _load_dance_library_catalog_unlocked()
        aliases = catalog.get("aliases") or {}
        resolved_ids = [str(aliases.get(template_id) or template_id) for template_id in ids]
        records = []
        for template_id in resolved_ids:
            record = (catalog.get("templates") or {}).get(template_id)
            if not isinstance(record, dict):
                raise ValueError("找不到舞步模板")
            records.append(record)

        changes: dict[str, object] = {}
        if "title" in update_body:
            title = str(update_body.get("title") or "").strip()
            if not title or len(title) > 120:
                raise ValueError("模板名稱需為 1 到 120 個字")
            changes["title"] = title
        if "category" in update_body:
            category = str(update_body.get("category") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,32}", category):
                raise ValueError("分類代碼格式不正確")
            changes["category"] = category
        if "tags" in update_body:
            raw_tags = update_body.get("tags")
            if not isinstance(raw_tags, list) or len(raw_tags) > 20:
                raise ValueError("標籤需為最多 20 個項目的陣列")
            tags = []
            for raw_tag in raw_tags:
                tag = str(raw_tag or "").strip()
                if not tag or len(tag) > 32:
                    raise ValueError("每個標籤需為 1 到 32 個字")
                if tag not in tags:
                    tags.append(tag)
            changes["tags"] = tags
        if "status" in update_body:
            status = str(update_body.get("status") or "").strip().lower()
            if status not in DANCE_LIBRARY_STATUSES:
                raise ValueError("模板狀態不正確")
            changes["status"] = status
        compatible_key = next(
            (
                key for key in ("compatibleCharacterTypes", "compatibleTypes")
                if key in update_body
            ),
            "",
        )
        if compatible_key:
            raw_types = update_body.get(compatible_key)
            if not isinstance(raw_types, list):
                raise ValueError("compatibleCharacterTypes 必須是陣列")
            compatible_types = []
            for raw_type in raw_types:
                character_type = str(raw_type or "").strip().lower()
                if character_type not in DANCE_LIBRARY_CHARACTER_TYPES:
                    raise ValueError("角色類型不正確")
                if character_type not in compatible_types:
                    compatible_types.append(character_type)
            if not compatible_types:
                raise ValueError("至少需支援一種角色類型")
            changes["compatibleCharacterTypes"] = compatible_types
            changes["compatibleTypes"] = compatible_types
        for boolean_field in ("featured", "favorite"):
            if boolean_field in update_body:
                if not isinstance(update_body.get(boolean_field), bool):
                    raise ValueError(f"{boolean_field} 必須是布林值")
                changes[boolean_field] = bool(update_body.get(boolean_field))
        if "rank" in update_body:
            try:
                rank = int(update_body.get("rank"))
            except (TypeError, ValueError) as error:
                raise ValueError("排序值必須是整數") from error
            if rank < 0 or rank > 1_000_000_000:
                raise ValueError("排序值超出範圍")
            changes["rank"] = rank
        if "preferredAssetId" in update_body:
            preferred_asset_id = str(update_body.get("preferredAssetId") or "")
            if not DANCE_LIBRARY_ASSET_ID_RE.fullmatch(preferred_asset_id):
                raise ValueError("偏好來源格式不正確")
            for record in records:
                if not any(
                    variant.get("id") == preferred_asset_id and not variant.get("missing")
                    for variant in (record.get("variants") or [])
                    if isinstance(variant, dict)
                ):
                    raise ValueError("偏好來源不屬於所選模板")
            changes["preferredAssetId"] = preferred_asset_id
        if not changes:
            raise ValueError("沒有可更新的欄位")

        now = time.time()
        for record in records:
            record.update(changes)
            if "status" in changes and changes["status"] != "problem":
                record["problem"] = ""
                record["previousStatus"] = ""
            record["updated"] = now
        if "category" in changes:
            category_id = str(changes["category"])
            if not any(category.get("id") == category_id for category in catalog.get("categories") or []):
                category_title = str(
                    update_body.get("categoryTitle")
                    or category_id.replace("-", " ").replace("_", " ")
                ).strip()
                catalog.setdefault("categories", []).append({
                    "id": category_id,
                    "title": (category_title or category_id)[:80],
                    "rank": max(
                        (int(category.get("rank") or 0) for category in catalog.get("categories") or []),
                        default=0,
                    ) + 100,
                })
        catalog["revision"] = int(catalog.get("revision") or 0) + 1
        catalog["updated"] = now
        _save_dance_library_catalog_unlocked(catalog)
        return catalog


def _dance_library_cache_path(record: dict, variant: dict, kind: str) -> Path:
    if kind not in DANCE_LIBRARY_DERIVATIVE_KINDS:
        raise ValueError("不支援的舞步模板媒體")
    template_id = str(record.get("id") or "")
    asset_id = str(variant.get("id") or "")
    if not DANCE_LIBRARY_ID_RE.fullmatch(template_id) or not DANCE_LIBRARY_ASSET_ID_RE.fullmatch(asset_id):
        raise ValueError("舞步模板快取識別碼不正確")
    extension = {"storyboard": ".jpg", "preview": ".mp4", "runtime": ".mp4"}[kind]
    source_key = hashlib.sha256(
        (
            f"{variant.get('sha256')}:{variant.get('size')}:{variant.get('mtimeNs')}:"
            f"{kind}:dance-library-v1"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return DANCE_LIBRARY_CACHE_ROOT / template_id / asset_id / source_key / f"{kind}{extension}"


def _ensure_private_dance_library_cache_parent(target: Path) -> None:
    root = DANCE_LIBRARY_CACHE_ROOT.expanduser()
    try:
        relative_parent = target.parent.relative_to(root)
    except ValueError as error:
        raise ValueError("舞步模板快取路徑不受信任") from error
    current = root
    for part in ("", *relative_parent.parts):
        if part:
            current = current / part
        current.mkdir(mode=0o700, parents=not part, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError("舞步模板快取目錄不受信任")
        try:
            current.chmod(0o700)
        except OSError:
            pass


def cleanup_dance_library_cache(keep: Path | None = None) -> None:
    """Bound the derivative cache without ever touching catalog source files."""
    root = DANCE_LIBRARY_CACHE_ROOT
    if not root.is_dir():
        return
    now = time.time()
    completed: list[tuple[float, int, Path]] = []
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        if ".part-" in path.name:
            if now - stat.st_mtime > 3600:
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        if path.name not in {"storyboard.jpg", "preview.mp4", "runtime.mp4"}:
            continue
        if path != keep and now - stat.st_mtime > DANCE_LIBRARY_CACHE_MAX_AGE_SECONDS:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        completed.append((stat.st_mtime, stat.st_size, path))
    completed.sort(key=lambda item: item[0], reverse=True)
    total = 0
    kept_count = 0
    for _mtime, size, path in completed:
        should_keep = (
            path == keep
            or (
                kept_count < DANCE_LIBRARY_CACHE_MAX_FILES
                and total + size <= DANCE_LIBRARY_CACHE_MAX_BYTES
            )
        )
        if should_keep:
            kept_count += 1
            total += size
            continue
        try:
            path.unlink()
        except OSError:
            pass


def dance_library_derivative_path(template_id: str, kind: str, generate: bool = False) -> Path:
    _catalog, record = resolve_dance_library_template(template_id)
    variant = _dance_library_preferred_variant(record)
    target = _dance_library_cache_path(record, variant, kind)
    if generate and not target.is_file():
        return prepare_dance_library_derivative(template_id, kind)
    return target


def _dance_library_ffmpeg_args(source: Path, target: Path, kind: str, variant: dict) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("伺服器缺少 ffmpeg，無法準備舞步模板")
    common = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
    ]
    if kind == "preview":
        duration = max(0.1, float(variant.get("duration") or 0.1))
        start = max(0.0, duration * 0.2)
        clip_duration = max(0.1, min(5.0, duration - start))
        common += ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{clip_duration:.3f}"]
    else:
        common += ["-i", str(source)]
    common += [
        "-map_metadata", "-1",
        "-threads", "2",
    ]
    if kind == "storyboard":
        duration = max(1.0, float(variant.get("duration") or 1))
        sample_rate = min(4.0, max(0.01, 4.0 / duration))
        vf = (
            f"fps={sample_rate:.6f},"
            "scale=240:426:force_original_aspect_ratio=decrease,"
            "pad=240:426:(ow-iw)/2:(oh-ih)/2:black,"
            "tile=2x2"
        )
        return common + [
            "-an",
            "-vf", vf,
            "-frames:v", "1",
            "-q:v", "3",
            str(target),
        ]
    if kind == "preview":
        return common + [
            "-map", "0:v:0",
            "-an",
            "-vf", (
                "fps=8,"
                "scale=320:568:force_original_aspect_ratio=decrease,"
                "pad=320:568:(ow-iw)/2:(oh-ih)/2:black"
            ),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "30",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(target),
        ]
    return common + [
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(target),
    ]


def _mark_dance_library_problem(template_id: str, problem: str) -> None:
    try:
        with _DANCE_LIBRARY_LOCK:
            catalog = _load_dance_library_catalog_unlocked()
            record = (catalog.get("templates") or {}).get(str(template_id or ""))
            if not isinstance(record, dict):
                return
            if str(record.get("status") or "") != "problem":
                record["previousStatus"] = str(record.get("status") or "pending")
            record["status"] = "problem"
            record["problem"] = str(problem or "derivative_failed")[:80]
            record["updated"] = time.time()
            catalog["revision"] = int(catalog.get("revision") or 0) + 1
            catalog["updated"] = time.time()
            _save_dance_library_catalog_unlocked(catalog)
    except (OSError, RuntimeError, ValueError):
        pass


def prepare_dance_library_derivative(template_id: str, kind: str) -> Path:
    _catalog, record = resolve_dance_library_template(template_id)
    variant = _dance_library_preferred_variant(record)
    source = _dance_library_resolve_source(
        str(variant.get("root") or ""),
        str(variant.get("path") or ""),
    )
    if source.stat().st_size > MAX_FETCH_BYTES:
        raise ValueError("舞步模板超過大小上限，請先裁切")
    target = _dance_library_cache_path(record, variant, kind)
    _ensure_private_dance_library_cache_parent(target)
    if target.is_file() and target.stat().st_size > 0:
        return target
    lock_key = str(target)
    with _DANCE_LIBRARY_DERIVATIVE_GUARD:
        cache_lock = _DANCE_LIBRARY_DERIVATIVE_LOCKS.setdefault(lock_key, threading.Lock())
    if not cache_lock.acquire(timeout=DANCE_LIBRARY_PREPARE_WAIT_SECONDS):
        raise DanceLibraryBusy("這個舞步模板正在準備中，請稍後再試")
    slot_acquired = False
    temporary = target.with_name(
        f".{target.stem}.part-{os.getpid()}-{secrets.token_hex(4)}{target.suffix}"
    )
    try:
        if target.is_file() and target.stat().st_size > 0:
            return target
        if not _DANCE_LIBRARY_DERIVATIVE_SLOTS.acquire(timeout=DANCE_LIBRARY_PREPARE_WAIT_SECONDS):
            raise DanceLibraryBusy("目前有其他舞步預覽正在準備，請稍後再試")
        slot_acquired = True
        try:
            if (
                kind == "runtime"
                and str(variant.get("codec") or "").lower() == "h264"
                and source.suffix.lower() == ".mp4"
            ):
                shutil.copyfile(source, temporary)
            else:
                try:
                    result = subprocess.run(
                        _dance_library_ffmpeg_args(source, temporary, kind, variant),
                        capture_output=True,
                        text=True,
                        timeout=DANCE_LIBRARY_FFMPEG_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired as error:
                    raise ValueError("舞步模板準備逾時，請稍後再試") from error
                if result.returncode != 0:
                    _mark_dance_library_problem(str(record.get("id") or ""), "derivative_failed")
                    raise ValueError("這支影片目前無法建立舞步預覽")
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                _mark_dance_library_problem(str(record.get("id") or ""), "derivative_empty")
                raise ValueError("舞步模板沒有產生可用的預覽")
            os.replace(temporary, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            cleanup_dance_library_cache(target)
            return target
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
    finally:
        if slot_acquired:
            _DANCE_LIBRARY_DERIVATIVE_SLOTS.release()
        cache_lock.release()


def prepare_dance_library(template_id: str, kinds: list[str] | None = None) -> dict:
    requested = kinds if kinds is not None else ["storyboard", "preview", "runtime"]
    if not isinstance(requested, list) or not requested or len(requested) > 3:
        raise ValueError("kinds 必須是 1 到 3 個媒體類型")
    normalized = []
    for raw_kind in requested:
        kind = str(raw_kind or "").strip().lower()
        if kind == "file":
            kind = "runtime"
        if kind not in DANCE_LIBRARY_DERIVATIVE_KINDS:
            raise ValueError("不支援的舞步模板媒體")
        if kind not in normalized:
            normalized.append(kind)
    prepared = {}
    for kind in normalized:
        target = prepare_dance_library_derivative(template_id, kind)
        prepared[kind] = {"ready": True, "size": target.stat().st_size}
    _catalog, record = resolve_dance_library_template(template_id)
    return {
        "id": str(record.get("id") or ""),
        "prepared": prepared,
        "runtimeReady": dance_library_derivative_path(template_id, "runtime").is_file(),
    }


def import_dance_library_template(
    template_id: str,
    slot: str,
    user: dict | None = None,
    enforce_access: bool = False,
) -> dict:
    _catalog, record = resolve_dance_library_template(template_id)
    if enforce_access and not user_can_access_dance_library_template(user, record):
        raise PermissionError("這個舞步模板尚未上架")
    runtime = prepare_dance_library_derivative(str(record.get("id") or ""), "runtime")
    _catalog, record = resolve_dance_library_template(str(record.get("id") or ""))
    if enforce_access and not user_can_access_dance_library_template(user, record):
        raise PermissionError("這個舞步模板尚未上架")
    size = runtime.stat().st_size
    if size > MAX_FETCH_BYTES:
        raise ValueError("舞步模板超過大小上限，請先裁切")
    safe_slot = re.sub(r"[^a-z0-9_-]+", "", str(slot or "").lower())[:32] or "video"
    target_name = (
        f"castle_dance_library_{int(time.time())}_{secrets.token_hex(4)}_"
        f"{safe_slot}_{record['id']}.mp4"
    )
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    target = (COMFY_INPUT / target_name).resolve()
    if not target.is_relative_to(COMFY_INPUT.resolve()):
        raise ValueError("無效的舞步模板檔名")
    shutil.copyfile(runtime, target)

    try:
        with _DANCE_LIBRARY_LOCK:
            catalog = _load_dance_library_catalog_unlocked()
            current = (catalog.get("templates") or {}).get(str(record.get("id") or ""))
            if isinstance(current, dict):
                current["useCount"] = max(0, int(current.get("useCount") or 0)) + 1
                current["lastUsed"] = time.time()
                current["updated"] = time.time()
                catalog["revision"] = int(catalog.get("revision") or 0) + 1
                catalog["updated"] = time.time()
                _save_dance_library_catalog_unlocked(catalog)
    except (OSError, RuntimeError, ValueError):
        # A usage counter must never make an otherwise valid import fail.
        pass
    return {
        "filename": target_name,
        "name": str(record.get("title") or "舞步模板"),
        "size": target.stat().st_size,
        "source": str(record.get("id") or ""),
        "id": str(record.get("id") or ""),
        "title": str(record.get("title") or "舞步模板"),
    }


def _dance_template_root() -> Path:
    return DANCE_TEMPLATE_ROOT.expanduser().resolve()


def _dance_template_id(path: Path, root: Path) -> str:
    return hashlib.sha256(path.resolve().relative_to(root).as_posix().encode("utf-8")).hexdigest()[:24]


def _dance_template_paths() -> list[Path]:
    root = _dance_template_root()
    if not root.is_dir():
        return []
    paths = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in DANCE_TEMPLATE_EXTS:
            try:
                resolved = path.resolve()
                if resolved.is_relative_to(root):
                    paths.append(resolved)
            except OSError:
                continue
    paths.sort(key=lambda p: p.name.lower())
    return paths


def resolve_legacy_dance_template_record(template_id: str) -> tuple[dict, dict]:
    """Map a pre-catalog curated ID back to its governed catalog record."""
    wanted = str(template_id or "").strip().lower()
    if not DANCE_LIBRARY_ID_RE.fullmatch(wanted):
        raise ValueError("無效的舞蹈範本")
    catalog = load_dance_library_catalog()
    curated_root = _dance_template_root()
    for record in (catalog.get("templates") or {}).values():
        if not isinstance(record, dict) or record.get("missing"):
            continue
        for variant in record.get("variants") or []:
            if (
                not isinstance(variant, dict)
                or variant.get("missing")
                or str(variant.get("root") or "") != "curated"
            ):
                continue
            try:
                source = _dance_library_resolve_source(
                    "curated",
                    str(variant.get("path") or ""),
                )
            except ValueError:
                continue
            if _dance_template_id(source, curated_root) == wanted:
                return catalog, record
    raise ValueError("找不到舞蹈範本")


def resolve_dance_template_path(template_id: str) -> Path:
    wanted = str(template_id or "").strip().lower()
    if not DANCE_LIBRARY_ID_RE.fullmatch(wanted):
        raise ValueError("無效的舞蹈範本")
    try:
        _catalog, record = resolve_dance_library_template(wanted)
        variant = _dance_library_preferred_variant(record)
        return _dance_library_resolve_source(
            str(variant.get("root") or ""),
            str(variant.get("path") or ""),
        )
    except ValueError:
        _catalog, record = resolve_legacy_dance_template_record(wanted)
        variant = _dance_library_preferred_variant(record)
        return _dance_library_resolve_source(
            str(variant.get("root") or ""),
            str(variant.get("path") or ""),
        )


def dance_template_thumbnail_path(source: Path) -> Path | None:
    try:
        stat = source.stat()
    except OSError:
        return None
    key = hashlib.sha256(f"dance:{source.resolve()}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()
    target = DANCE_TEMPLATE_THUMB_ROOT / f"{key}.jpg"
    if target.is_file():
        return target
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        DANCE_TEMPLATE_THUMB_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-frames:v", "1", "-vf", "scale=480:-2", str(target)],
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return target if target.is_file() else None
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        return None


def list_dance_templates() -> list[dict]:
    items = []
    for item in list_dance_library().get("templates") or []:
        try:
            _catalog, record = resolve_dance_library_template(str(item.get("id") or ""))
            variant = _dance_library_preferred_variant(record)
        except ValueError:
            continue
        template_id = str(item.get("id") or "")
        items.append({
            "id": template_id,
            "label": str(item.get("title") or "舞蹈範本"),
            "url": f"/api/dance-templates/{template_id}/file",
            "thumbUrl": f"/api/dance-templates/{template_id}/thumb",
            "previewUrl": str(item.get("previewUrl") or ""),
            "size": int(variant.get("size") or 0),
            "modified": float(variant.get("modified") or 0),
        })
    return items


def import_dance_template(
    template_id: str,
    slot: str,
    user: dict | None = None,
    enforce_access: bool = False,
) -> dict:
    return import_dance_library_template(
        template_id,
        slot,
        user=user,
        enforce_access=enforce_access,
    )


def import_view_item_to_input(
    filename: str,
    subfolder: str,
    view_type: str,
    slot: str,
    expected_kind: str = "",
) -> dict:
    filename, subfolder, view_type = validate_view_parts(filename, subfolder, view_type)
    source = resolve_view_path(filename, subfolder, view_type)
    if source is None or not source.is_file():
        raise ValueError("找不到素材檔")
    kind = gallery_media_kind(source)
    if kind not in {"image", "video", "audio"}:
        raise ValueError("這個素材類型不能用來生成")
    expected = str(expected_kind or "").strip()
    if expected and expected != kind:
        raise ValueError("素材類型已變更，請重新整理後再試")
    size = source.stat().st_size
    if size > MAX_UPLOAD_BODY:
        raise ValueError("素材檔太大，請先裁切後再使用")
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(source.name, source.suffix)
    target_name = f"castle_chain_{int(time.time())}_{secrets.token_hex(4)}_{slot}_{safe_name}"
    target = (COMFY_INPUT / target_name).resolve()
    if not target.is_relative_to(COMFY_INPUT.resolve()):
        raise ValueError("無效的素材檔名")
    shutil.copyfile(source, target)
    return {"filename": target_name, "name": source.name, "size": size, "kind": kind}


def resolve_server_file(name: str) -> str:
    """Validate a bare filename that already lives in COMFY_INPUT (e.g. produced
    by fetch_video_url) and return it. Rejects any path traversal."""
    bare = str(name or "")
    if not bare or bare != Path(bare).name:
        raise ValueError("無效的伺服器檔名")
    path = (COMFY_INPUT / bare).resolve()
    if not path.is_relative_to(COMFY_INPUT.resolve()) or not path.is_file():
        raise ValueError("找不到下載的影片檔")
    return bare


def request_bytes(path: str) -> tuple[bytes, str]:
    url = f"{DEFAULT_COMFY}{path}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read(), response.headers.get("Content-Type", "application/octet-stream")


def _recv_exact(sock, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("ComfyUI websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _ws_send_frame(sock, opcode: int, payload: bytes = b"") -> None:
    header = bytearray([0x80 | (opcode & 0x0F)])
    size = len(payload)
    if size < 126:
        header.append(size)
    elif size < 65536:
        header.extend([126])
        header.extend(struct.pack("!H", size))
    else:
        header.extend([127])
        header.extend(struct.pack("!Q", size))
    sock.sendall(bytes(header) + payload)


def _ws_read_frame(sock) -> tuple[int, bytes]:
    first, second = _recv_exact(sock, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    size = second & 0x7F
    if size == 126:
        size = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, size) if size else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def comfy_ws_messages(client_id: str):
    parsed = urllib.parse.urlsplit(DEFAULT_COMFY)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = f"{parsed.path.rstrip('/')}/ws" if parsed.path else "/ws"
    path += f"?clientId={urllib.parse.quote(client_id)}"
    raw_sock = socket.create_connection((host, port), timeout=10)
    sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host) if parsed.scheme == "https" else raw_sock
    with sock:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            header += _recv_exact(sock, 1)
            if len(header) > 8192:
                raise ConnectionError("Invalid ComfyUI websocket handshake")
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise ConnectionError("ComfyUI websocket upgrade failed")
        sock.settimeout(70)
        while True:
            opcode, payload = _ws_read_frame(sock)
            if opcode == 1:
                yield payload.decode("utf-8", "replace")
            elif opcode == 8:
                break
            elif opcode == 9:
                _ws_send_frame(sock, 0xA, payload)


def parse_range_header(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single HTTP byte range. Return None when no Range was requested."""
    if not header:
        return None
    unit, _, raw_spec = header.partition("=")
    if unit.strip().lower() != "bytes" or not raw_spec.strip() or "," in raw_spec:
        raise ValueError("Invalid Range")
    start_raw, sep, end_raw = raw_spec.strip().partition("-")
    if not sep:
        raise ValueError("Invalid Range")
    if start_raw == "":
        try:
            suffix_len = int(end_raw)
        except ValueError as error:
            raise ValueError("Invalid Range") from error
        if suffix_len <= 0:
            raise ValueError("Invalid Range")
        start = max(size - suffix_len, 0)
        end = size - 1
    else:
        try:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
        except ValueError as error:
            raise ValueError("Invalid Range") from error
        if start < 0 or end < start:
            raise ValueError("Invalid Range")
        end = min(end, size - 1)
    if start >= size:
        raise ValueError("Range not satisfiable")
    return start, end


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def clamp_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_VOICE_STAGE_RE = re.compile(r"[\[【（(][^\]】）)]{0,48}[\]】）)]")
_VOICE_CUE_WORD_RE = re.compile(
    r"(音效|背景音|背景聲|配樂|笑聲|哭聲|掌聲|鼓掌|咳嗽|嘆氣|叹气|喘息|尖叫|"
    r"笑|哭|咳|喘|嘆|叹|"
    r"停頓|沉默|轉場|旁白|BGM|SFX|music|sound|laugh|laughter|pause|silence)",
    re.IGNORECASE,
)
_VOICE_CUE_LINE_RE = re.compile(
    r"(?im)^\s*(?:音效|背景音|背景聲|配樂|轉場|旁白|BGM|SFX|music|sound|voiceover)\s*[:：]"
)
_VOICE_REPEAT_RE = re.compile(r"(?:哈){5,}|(?:啊){6,}|(?:呃){5,}|[~～]{3,}|[—-]{4,}|[♪♫]")
_VOICE_TERMINAL = "。？！.!?"
_VOICE_TRAILING_PAUSE = "，、；：,;:"


def load_tts_normalizer():
    """Load ai-sister/voice-lab's zh-TW TTS normalizer when available."""
    global _TTS_NORMALIZER
    if _TTS_NORMALIZER is not None:
        return _TTS_NORMALIZER or None
    try:
        spec = importlib.util.spec_from_file_location("castle_tts_normalize", TTS_NORMALIZER_PATH)
        if spec is None or spec.loader is None:
            raise OSError("missing loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _TTS_NORMALIZER = module
    except Exception:
        _TTS_NORMALIZER = False
    return _TTS_NORMALIZER or None


def basic_voice_normalize(text: str) -> str:
    mark = "。" if _CJK_RE.search(text) else "."
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s*\n+\s*", mark, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("...", mark).replace("⋯", mark).replace("…", mark)
    text = re.sub(r"([。？！.!?])\1+", r"\1", text)
    return text.strip()


def normalize_voice_script(text: str) -> str:
    text = basic_voice_normalize(text)
    normalizer = load_tts_normalizer()
    if normalizer and _CJK_RE.search(text):
        try:
            text = normalizer.normalize(text)
        except Exception:
            text = basic_voice_normalize(text)
    return basic_voice_normalize(text)


def voice_visible_chars(text: str) -> int:
    return len(re.sub(r"[\s，。？！：；、,.!?;:「」『』（）()\[\]【】\"'`]", "", text))


def voice_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def _voice_estimated_seconds_for_language(text: str, is_cjk: bool) -> int:
    if not is_cjk:
        return max(1, round(voice_word_count(text) / VOICE_EN_WORDS_PER_SECOND))
    # zh-TW monologue tends to land around 4-5 visible chars/sec; use a
    # conservative estimate so long scripts get split before TTS can drift.
    return max(1, round(voice_visible_chars(text) / 4.2))


def voice_estimated_seconds(text: str) -> int:
    return _voice_estimated_seconds_for_language(text, bool(_CJK_RE.search(text)))


def split_english_breath_group(text: str, max_words: int) -> list[str]:
    chunks: list[str] = []
    part = text.strip()
    while voice_word_count(part) > max_words:
        matches = list(re.finditer(r"[A-Za-z0-9']+", part))
        if len(matches) <= max_words:
            break
        limit = matches[max_words].start()
        cut = -1
        for i in range(limit, 0, -1):
            if part[i - 1] in ",;" or part[i - 1].isspace():
                cut = i
                break
        if cut <= 0:
            cut = matches[max_words - 1].end()
        chunks.append(part[:cut].strip())
        part = part[cut:].strip()
    if part:
        chunks.append(part)
    return chunks


def reject_voice_hallucination_risks(text: str, label: str) -> None:
    if _VOICE_CUE_LINE_RE.search(text):
        raise ValueError(f"{label}不能包含音效/BGM/旁白這類舞台指示；請只留下要被說出口的台詞。")
    for match in _VOICE_STAGE_RE.finditer(text):
        if _VOICE_CUE_WORD_RE.search(match.group(0)):
            raise ValueError(f"{label}不能包含括號音效或情緒 cue，例如（笑）、[music]、（停頓）。請刪掉後再送。")
    if _VOICE_REPEAT_RE.search(text):
        raise ValueError(f"{label}包含容易讓 TTS 漂掉的重複聲音或符號；請改成一般句子。")


def fallback_breath_groups(text: str, max_len: int) -> list[str]:
    text = normalize_voice_script(text)
    if not _CJK_RE.search(text):
        chunks: list[str] = []
        for part in re.split(r"(?<=[。？！.!?])\s*", text):
            part = part.strip()
            if not part:
                continue
            chunks.extend(split_english_breath_group(part, VOICE_TTS_CHUNK_MAX_WORDS))
        return [c for c in chunks if c]
    parts = re.split(r"(?<=[。？！.!?])\s*", text)
    chunks: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        while voice_visible_chars(part) > max_len:
            limit = min(len(part), max_len + 18)
            cut = -1
            for i in range(limit, max(1, limit // 2), -1):
                if part[i - 1] in _VOICE_TRAILING_PAUSE or part[i - 1].isspace():
                    cut = i
                    break
            if cut <= 0:
                cut = min(len(part), max_len)
            chunks.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            chunks.append(part)
    return [c for c in chunks if c]


def voice_breath_groups(text: str) -> list[str]:
    normalizer = load_tts_normalizer()
    if normalizer and _CJK_RE.search(text):
        try:
            return list(normalizer.breath_groups(text, max_len=VOICE_BREATH_GROUP_MAX_CHARS))
        except Exception:
            pass
    return fallback_breath_groups(text, VOICE_BREATH_GROUP_MAX_CHARS)


def terminalize_voice_group(group: str, is_cjk: bool) -> str:
    if not is_cjk:
        return group.strip()
    group = group.strip().rstrip(_VOICE_TRAILING_PAUSE)
    if not group:
        return ""
    if group[-1] in _VOICE_TERMINAL:
        return group
    return group + ("。" if _CJK_RE.search(group) else ".")


def join_voice_groups(groups: list[str]) -> str:
    groups = [group for group in groups if group]
    separator = "" if _CJK_RE.search("".join(groups)) else " "
    return separator.join(groups)


def merge_tiny_voice_groups(groups: list[str], is_cjk: bool) -> list[str]:
    merged: list[str] = []
    for group in groups:
        group = group.strip()
        if not group:
            continue
        if is_cjk:
            if (
                merged
                and voice_visible_chars(group) < VOICE_TTS_CHUNK_MIN_CHARS
                and voice_visible_chars(join_voice_groups([merged[-1], group])) <= VOICE_TTS_CHUNK_MAX_CHARS
            ):
                merged[-1] = join_voice_groups([merged[-1], group])
            else:
                merged.append(group)
            continue
        if (
            merged
            and voice_word_count(group) < VOICE_TTS_CHUNK_MIN_WORDS
            and voice_word_count(join_voice_groups([merged[-1], group])) <= VOICE_TTS_CHUNK_MAX_WORDS
        ):
            merged[-1] = join_voice_groups([merged[-1], group])
        else:
            merged.append(group)
    return merged


def prepare_voice_script_chunks(raw: str, label: str, max_chars: int) -> list[str]:
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"請輸入{label}")
    if len(text) > max_chars:
        raise ValueError(f"{label}單次最多 {max_chars} 字；這段有 {len(text)} 字。請拆成多次送出。")
    reject_voice_hallucination_risks(text, label)
    text = normalize_voice_script(text)
    reject_voice_hallucination_risks(text, label)
    is_cjk = bool(_CJK_RE.search(text))
    chunks = merge_tiny_voice_groups(
        [terminalize_voice_group(group, is_cjk) for group in voice_breath_groups(text)],
        is_cjk,
    )
    if not chunks:
        raise ValueError(f"{label}清理後沒有可朗讀內容。")
    for chunk in chunks:
        if (
            voice_visible_chars(chunk) > VOICE_TTS_CHUNK_MAX_CHARS * 2
            if is_cjk
            else voice_word_count(chunk) > VOICE_TTS_CHUNK_MAX_WORDS * 2
        ):
            raise ValueError(f"{label}有一個句子太長，請加標點或換行後再送。")
        if (
            _voice_estimated_seconds_for_language(chunk, is_cjk)
            > VOICE_SCRIPT_MAX_SECONDS
        ):
            raise ValueError(f"{label}有一段估計超過 {VOICE_SCRIPT_MAX_SECONDS} 秒，請加標點或換行後再送。")
    if len(chunks) > VOICE_MAX_TTS_CHUNKS:
        raise ValueError(
            f"{label}會被切成 {len(chunks)} 個 TTS 段，超過安全上限 {VOICE_MAX_TTS_CHUNKS} 段；"
            "請拆成多次送出。"
        )
    return chunks


def prepare_voice_script(raw: str, label: str, max_chars: int) -> str:
    return join_voice_groups(prepare_voice_script_chunks(raw, label, max_chars))


def add_audio_concat_and_save(
    prompt: dict,
    audio_refs: list[list],
    prefix: str,
    start_id: int,
    pause_seconds: float = 0.0,
) -> dict:
    if not audio_refs:
        raise ValueError("沒有可儲存的音訊")
    node_id = start_id
    silence_ref = None
    if pause_seconds > 0 and len(audio_refs) > 1:
        prompt[str(node_id)] = {
            "class_type": "EmptyAudio",
            "inputs": {"duration": pause_seconds, "sample_rate": 22050, "channels": 1},
        }
        silence_ref = [str(node_id), 0]
        node_id += 1
    current = audio_refs[0]
    for ref in audio_refs[1:]:
        if silence_ref:
            prompt[str(node_id)] = {
                "class_type": "AudioConcat",
                "inputs": {"audio1": current, "audio2": silence_ref, "direction": "after"},
            }
            current = [str(node_id), 0]
            node_id += 1
        prompt[str(node_id)] = {
            "class_type": "AudioConcat",
            "inputs": {"audio1": current, "audio2": ref, "direction": "after"},
        }
        current = [str(node_id), 0]
        node_id += 1
    prompt[str(node_id)] = {"class_type": "SaveAudio", "inputs": {"audio": current, "filename_prefix": prefix}}
    return prompt


def prepare_voice_reference_text(raw: str) -> str:
    text = basic_voice_normalize((raw or "").strip())
    if not text:
        raise ValueError("請填寫參考音訊的逐字稿（音訊實際說的話）")
    if len(text) > VOICE_SAMPLE_MAX_CHARS:
        raise ValueError(f"參考音訊逐字稿最多 {VOICE_SAMPLE_MAX_CHARS} 字；請使用 5-15 秒乾淨人聲。")
    reject_voice_hallucination_risks(text, "參考音訊逐字稿")
    return text


def model_options(class_name: str, input_name: str) -> list[str]:
    try:
        info = request_json(f"/object_info/{urllib.parse.quote(class_name)}")
        field = info[class_name]["input"]["required"][input_name]
        return list(field[0]) if field and isinstance(field[0], list) else []
    except Exception:
        return []


def available_model_names() -> set[str]:
    names: set[str] = set()
    for class_name, input_name in [
        ("UNETLoader", "unet_name"),
        ("CLIPLoader", "clip_name"),
        ("DualCLIPLoader", "clip_name1"),
        ("DualCLIPLoader", "clip_name2"),
        ("VAELoader", "vae_name"),
        ("LoraLoaderModelOnly", "lora_name"),
        ("CheckpointLoaderSimple", "ckpt_name"),
        ("ControlNetLoader", "control_net_name"),
        ("ControlNetLoaderAdvanced", "control_net_name"),
        ("CLIPVisionLoader", "clip_name"),
        ("UpscaleModelLoader", "model_name"),
        ("SAMLoader", "sam_model_name"),
        ("UltralyticsDetectorProvider", "model_name"),
        ("IPAdapterModelLoader", "ipadapter_file"),
        ("InstantIDModelLoader", "instantid_file"),
    ]:
        names.update(model_options(class_name, input_name))
    return names


def model_file_path(name: str) -> Path | None:
    models_dir = MODELS_DIR
    normalized = name.replace("\\", "/")
    direct = models_dir / normalized
    candidates = [direct, models_dir / Path(normalized).name]
    basename = Path(normalized).name
    candidates.extend(
        [
            *models_dir.glob(f"*/{normalized}"),
            *models_dir.glob(f"*/*/{normalized}"),
            *models_dir.glob(f"*/{basename}"),
            *models_dir.glob(f"*/*/{basename}"),
        ]
    )
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


def model_file_size(name: str) -> int:
    path = model_file_path(name)
    if path is not None:
        try:
            return path.stat().st_size
        except OSError:
            pass
    return 0


def safetensors_file_is_complete(path: Path) -> bool:
    """Cheap structural validation without importing model tensors.

    A safetensors header declares byte ranges for every tensor.  Requiring
    those ranges to cover the data section exactly detects interrupted model
    downloads while only reading the JSON header.  Results are cached by file
    identity and automatically invalidated when a download changes size/mtime.
    """
    try:
        stat = path.stat()
    except OSError:
        return False
    cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _MODEL_INTEGRITY_LOCK:
        cached = _MODEL_INTEGRITY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    valid = False
    try:
        with path.open("rb") as handle:
            raw_header_size = handle.read(8)
            if len(raw_header_size) != 8:
                raise ValueError("missing safetensors header length")
            header_size = struct.unpack("<Q", raw_header_size)[0]
            if (
                header_size < 2
                or header_size > _SAFETENSORS_HEADER_MAX_BYTES
                or header_size > stat.st_size - 8
            ):
                raise ValueError("invalid safetensors header length")
            header = json.loads(handle.read(header_size))
        if not isinstance(header, dict):
            raise ValueError("invalid safetensors header")
        ranges: list[tuple[int, int]] = []
        for tensor_name, tensor in header.items():
            if tensor_name == "__metadata__":
                continue
            if not isinstance(tensor, dict):
                raise ValueError("invalid tensor metadata")
            offsets = tensor.get("data_offsets")
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or not all(isinstance(value, int) and not isinstance(value, bool) for value in offsets)
            ):
                raise ValueError("invalid tensor offsets")
            start, end = offsets
            if start < 0 or end < start:
                raise ValueError("invalid tensor range")
            ranges.append((start, end))
        if not ranges:
            raise ValueError("empty safetensors payload")
        cursor = 0
        for start, end in sorted(ranges):
            if start != cursor:
                raise ValueError("safetensors data is not fully covered")
            cursor = end
        valid = cursor == stat.st_size - 8 - header_size
    except (OSError, ValueError, TypeError, json.JSONDecodeError, struct.error):
        valid = False

    with _MODEL_INTEGRITY_LOCK:
        # Keep at most one cached identity per path so repeated repairs do not
        # grow the process cache indefinitely.
        for key in [key for key in _MODEL_INTEGRITY_CACHE if key[0] == str(path) and key != cache_key]:
            _MODEL_INTEGRITY_CACHE.pop(key, None)
        _MODEL_INTEGRITY_CACHE[cache_key] = valid
    return valid


def model_is_ready(name: str, available_models: set[str]) -> bool:
    normalized = name.replace("\\", "/")
    basename = Path(normalized).name
    path = model_file_path(name)
    size = 0
    if path is not None:
        try:
            size = path.stat().st_size
        except OSError:
            path = None
    minimum = MODEL_MIN_BYTES.get(name)
    if minimum is not None:
        if path is None or size < minimum:
            return False
    expected = MODEL_EXPECTED_BYTES.get(name, MODEL_EXPECTED_BYTES.get(basename))
    if expected is not None and size != expected:
        return False
    if path is not None and path.suffix.lower() == ".safetensors" and not safetensors_file_is_complete(path):
        return False
    if minimum is not None or expected is not None:
        return path is not None
    return name in available_models or normalized in available_models or basename in available_models or path is not None


def normalize_seed(value) -> int:
    if value in (None, "", -1, "-1"):
        return random.randint(0, 2**63 - 1)
    return clamp_int(value, 0, 0, 2**63 - 1)


def output_prefix(settings: dict, feature_id: str) -> str:
    custom = (settings.get("prefix") or "").strip()
    if custom:
        return custom
    character_id = safe_path_segment(str(settings.get("_characterId") or ""), "")
    if character_id:
        action_id = safe_path_segment(str(settings.get("_characterActionId") or ""), "") or safe_path_segment(feature_id, "generate")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return f"Castle/c/{character_id}/{action_id}_{timestamp}_{secrets.token_hex(2)}"
    return FEATURE_OUTPUT_PREFIXES.get(feature_id, f"Castle/{feature_id}")


FEATURE_PRESETS = {
    "text2img": {
        "quality": {
            "fast": {"steps": 5},
            "standard": {},
            "fine": {"steps": 14},
        },
    },
    "image-blend": {
        "quality": {
            "fast": {"steps": 18},
            "standard": {},
            "fine": {"steps": 40},
        },
    },
    "character-angles": {
        "quality": {
            "fast": {"steps": 20, "batchSize": 1},
            "standard": {},
            "fine": {"steps": 42},
        },
    },
    "img2video": {
        "quality": {
            "fast": {"steps": 4},
            "standard": {},
            "fine": {"steps": 10},
        },
        "motionScale": {
            "subtle": {"shift": 5.5, "cfg": 0.8},
            "natural": {},
            "bold": {"shift": 10.5, "cfg": 1.2},
        },
        "duration": {
            "2s": {"length": 33, "fps": 16},
            "3s": {"length": 49, "fps": 16},
            "4s": {"length": 65, "fps": 16},
        },
    },
    "dance": {
        "quality": {
            "fast": {"steps": 4, "length": 81},
            "standard": {},
            "fine": {"steps": 10, "length": 161},
        },
    },
    "lipsync": {
        "quality": {
            "fast": {"maxFrames": 180},
            "standard": {},
            "fine": {"maxFrames": 600},
        },
    },
    "voice-speak": {
        "quality": {
            "fast": {"qualityGate": False},
            "standard": {},
            "fine": {"qualityGate": True},
        },
    },
    "post-generator": {
        "postLength": {
            "short": {"length": "short"},
            "medium": {"length": "medium"},
            "long": {"length": "long"},
        },
    },
}


CHARACTER_VISUAL_CLASS_PRESET_HINTS = {
    "anime": {},
    "realistic": {},
    "illustration": {},
    "unknown": {},
}


def resolve_generation_presets(feature_id: str, settings: dict) -> dict:
    if not isinstance(settings, dict):
        return {}
    resolved = dict(settings)
    preset_spec = FEATURE_PRESETS.get(feature_id) or {}
    for key, choices in preset_spec.items():
        value = str(settings.get(key) or "").strip()
        if not value:
            continue
        updates = choices.get(value)
        if not isinstance(updates, dict):
            continue
        for update_key, update_value in updates.items():
            if update_key not in settings or settings.get(update_key) in (None, ""):
                resolved[update_key] = update_value
    return resolved


def apply_character_visual_class_hints(settings: dict, character_context: dict | None) -> dict:
    if not character_context:
        return settings if isinstance(settings, dict) else {}
    visual_class = normalized_visual_class(character_context.get("visualClass"))
    hints = CHARACTER_VISUAL_CLASS_PRESET_HINTS.get(visual_class) or {}
    if not hints:
        return settings if isinstance(settings, dict) else {}
    resolved = dict(settings) if isinstance(settings, dict) else {}
    for key, value in hints.items():
        if key not in resolved or resolved.get(key) in (None, "", [], {}):
            resolved[key] = value
    return resolved


def safe_path_segment(value: str, fallback: str = "output") -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return (segment or fallback)[:80]


def stable_character_identifier(value: object) -> str:
    """Normalize a Castle/provider character ID without turning ':' into '_'."""

    character_id = str(value or "").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", character_id) is None:
        return ""
    return character_id


def wardrobe_aliases() -> dict[str, str]:
    aliases = dict(DEFAULT_WARDROBE_ALIASES)
    raw = os.environ.get("CASTLE_WARDROBE_ALIASES", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for key, value in data.items():
                    k = safe_path_segment(str(key).lower(), "")
                    v = safe_path_segment(str(value).lower(), "")
                    if k and v:
                        aliases[k] = v
        except json.JSONDecodeError:
            pass
    return aliases


def wardrobe_character_id(character_id: str) -> str | None:
    cid = safe_path_segment(str(character_id).lower(), "")
    if not cid:
        return None
    aliases = wardrobe_aliases()
    if cid in aliases:
        return aliases[cid]
    for prefix in ("ai-sister-", "bestie-"):
        if cid.startswith(prefix):
            short = cid.removeprefix(prefix)
            return aliases.get(short, short)
    return cid


def wardrobe_theme_label(theme: str) -> str:
    return WARDROBE_THEME_LABELS.get(theme, theme)


def load_wardrobe_approval() -> set[str]:
    try:
        data = json.loads(WARDROBE_APPROVAL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    items = data.get("items") if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        return set()
    return {
        str(key)
        for key, value in items.items()
        if isinstance(value, dict) and value.get("status") == "approved"
    }


def wardrobe_asset_path(character_id: str, kind: str, theme: str = "", pose: str = "") -> Path | None:
    tachie_id = wardrobe_character_id(character_id)
    if not tachie_id or theme not in WARDROBE_THEMES:
        return None
    if kind == "outfit":
        path = WARDROBE_ROOT / "outfits_v2_norm" / f"doll_{tachie_id}__{theme}.png"
    elif kind == "pose" and pose in WARDROBE_POSES:
        path = WARDROBE_ROOT / "poses/react" / f"doll_{tachie_id}__{theme}__{pose}.png"
    else:
        return None
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(WARDROBE_ROOT.resolve()):
            return None
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def approved_wardrobe_themes(character_id: str) -> list[str]:
    tachie_id = wardrobe_character_id(character_id)
    if not tachie_id:
        return []
    approved = load_wardrobe_approval()
    return [
        theme
        for theme in WARDROBE_THEMES
        if f"{tachie_id}__{theme}" in approved
        and wardrobe_asset_path(character_id, "outfit", theme) is not None
    ]


def anime_scene_asset_path(theme: str) -> Path | None:
    clean_theme = str(theme or "").strip()
    if clean_theme not in ANIME_SCENE_RELEASE_THEMES:
        return None
    path = ANIME_SCENE_ROOT / f"{clean_theme}.png"
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(ANIME_SCENE_ROOT.resolve()):
            return None
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def approved_anime_scene_themes() -> list[str]:
    return [theme for theme in ANIME_SCENE_RELEASE_THEMES if anime_scene_asset_path(theme) is not None]


def png_image_dimensions(path: Path) -> tuple[int, int]:
    """Read PNG dimensions without decoding image pixels or adding a runtime dependency."""
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError as error:
        raise ValueError("讀取動漫寫真素材失敗") from error
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError("動漫寫真目前只接受已驗證的 PNG 素材")
    width, height = struct.unpack(">II", header[16:24])
    if width < 1 or height < 1 or width > 16_384 or height > 16_384:
        raise ValueError("動漫寫真素材尺寸無效")
    return width, height


def character_wardrobe_upload_dir(character_id: str) -> Path:
    cid = safe_path_segment(str(character_id).lower(), "character")
    root = CHARACTER_LOCAL_ASSET_ROOT.resolve()
    target = (root / cid / "wardrobe").resolve()
    if not target.is_relative_to(root):
        raise ValueError("Invalid wardrobe directory")
    return target


def uploaded_wardrobe_asset_path(character_id: str, filename: str) -> Path | None:
    name = Path(str(filename or "")).name
    if not name or name != filename or name.startswith("."):
        return None
    if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    base = character_wardrobe_upload_dir(character_id).resolve()
    candidate = base / name
    if candidate.is_symlink():
        return None
    target = candidate.resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target


def uploaded_wardrobe_items(character_id: str) -> list[dict]:
    base = character_wardrobe_upload_dir(character_id)
    try:
        paths = [path for path in base.iterdir() if path.is_file() and not path.is_symlink()]
    except OSError:
        return []
    items = []
    for path in sorted(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        name = path.name
        original_stem = re.sub(r"^\d+_[a-f0-9]{4,16}_", "", Path(name).stem, flags=re.IGNORECASE)
        label = original_stem.replace("_", " ").strip() or "自訂"
        params = urllib.parse.urlencode({"kind": "wardrobe", "file": name})
        items.append({
            "theme": f"custom:{safe_path_segment(Path(name).stem, 'custom')}",
            "filename": name,
            "label": label if label != "wardrobe" else "自訂",
            "outfitUrl": f"/api/characters/{urllib.parse.quote(character_id)}/asset?{params}",
            "thumbUrl": f"/api/characters/{urllib.parse.quote(character_id)}/asset?{params}&thumb=1",
            "poses": [],
            "custom": True,
            "approved": False,
        })
    return items


def wardrobe_outfits_for_character(character_id: str) -> list[dict]:
    cid = safe_path_segment(str(character_id).lower(), "")
    if not cid:
        return []
    approved = load_wardrobe_approval()
    outfits = []
    if WARDROBE_ROOT.is_dir():
        tachie_id = wardrobe_character_id(cid)
        if tachie_id:
            for index, theme in enumerate(WARDROBE_THEMES):
                outfit = wardrobe_asset_path(cid, "outfit", theme)
                if outfit is None:
                    continue
                pose_items = []
                for pose in WARDROBE_POSES:
                    if wardrobe_asset_path(cid, "pose", theme, pose) is None:
                        continue
                    pose_params = urllib.parse.urlencode({"kind": "pose", "theme": theme, "pose": pose})
                    pose_items.append({
                        "pose": pose,
                        "url": f"/api/characters/{urllib.parse.quote(cid)}/asset?{pose_params}",
                        "thumbUrl": f"/api/characters/{urllib.parse.quote(cid)}/asset?{pose_params}&thumb=1",
                    })
                params = urllib.parse.urlencode({"kind": "outfit", "theme": theme})
                outfits.append({
                    "theme": theme,
                    "label": wardrobe_theme_label(theme),
                    "outfitUrl": f"/api/characters/{urllib.parse.quote(cid)}/asset?{params}",
                    "thumbUrl": f"/api/characters/{urllib.parse.quote(cid)}/asset?{params}&thumb=1",
                    "poses": pose_items,
                    "approved": f"{tachie_id}__{theme}" in approved,
                    "_order": index,
                })
    outfits.sort(key=lambda item: (0 if item.get("approved") else 1, item.get("_order", 999)))
    for item in outfits:
        item.pop("_order", None)
    return outfits + uploaded_wardrobe_items(cid)


def wardrobe_outfits_for_user(
    character: dict,
    user: dict | None,
    service_token: bool = False,
) -> list[dict]:
    outfits = wardrobe_outfits_for_character(str(character.get("id") or ""))
    if user_can_edit_character(user, character, service_token):
        return outfits
    return [item for item in outfits if item.get("approved") is True and not item.get("custom")]


def uploaded_wardrobe_storage(character_id: str) -> tuple[int, int]:
    base = character_wardrobe_upload_dir(character_id)
    count = 0
    total = 0
    try:
        paths = list(base.iterdir())
    except OSError:
        return 0, 0
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            count += 1
            total += path.stat().st_size
        except OSError:
            continue
    return count, total


def wardrobe_thumbnail_cache_path(source: Path) -> Path | None:
    try:
        stat = source.stat()
    except OSError:
        return None
    key = hashlib.sha256(f"{source.resolve()}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()
    suffix = source.suffix.lower() if source.suffix.lower() in {".jpg", ".jpeg", ".webp"} else ".png"
    return WARDROBE_THUMB_ROOT / f"{key}{suffix}"


def wardrobe_thumbnail_path(source: Path) -> Path | None:
    target = wardrobe_thumbnail_cache_path(source)
    if target is None:
        return None
    if target.is_file():
        return target
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        WARDROBE_THUMB_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vf", "scale=480:-2", str(target)],
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return target if target.is_file() else None
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        return None


def voice_speak_output_prefix(settings: dict, persona: str) -> str:
    custom = (settings.get("prefix") or "").strip()
    if custom:
        return custom
    character_id = safe_path_segment(str(settings.get("_characterId") or ""), "")
    if character_id:
        action_id = safe_path_segment(str(settings.get("_characterActionId") or "voice-speak"), "voice-speak")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return f"Castle/c/{character_id}/{action_id}_{timestamp}_{secrets.token_hex(2)}"
    base = FEATURE_OUTPUT_PREFIXES.get("voice-speak", "Castle/voice-speak_BreezyVoice")
    return f"{base}_{safe_path_segment(persona, 'voice')}"


def output_path_from_prefix(prefix: str, suffix: str = ".wav") -> Path:
    parts = [safe_path_segment(part) for part in str(prefix or "").split("/") if part.strip()]
    if not parts:
        parts = ["Castle", "voice-speak_BreezyVoiceGuarded"]
    stem = safe_path_segment(parts[-1], "voice-speak_BreezyVoiceGuarded")
    if "Guarded" not in stem:
        stem = f"{stem}_Guarded"
    subparts = parts[:-1]
    filename = f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{suffix}"
    directory = COMFY_OUTPUT.joinpath(*subparts).resolve() if subparts else COMFY_OUTPUT.resolve()
    if not directory.is_relative_to(COMFY_OUTPUT.resolve()):
        raise ValueError("Invalid output prefix")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def direct_sidecar_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".castle.json")


def load_direct_sidecar(path: Path) -> dict | None:
    try:
        sidecar = direct_sidecar_path(path)
        if sidecar.is_symlink() or sidecar.stat().st_size > MAX_SIDECAR_BYTES:
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def sidecar_output_metadata(sidecar: dict | None, filename: str, subfolder: str, view_type: str) -> dict | None:
    outputs = sidecar.get("outputs") if isinstance(sidecar, dict) else None
    for output in outputs if isinstance(outputs, list) else []:
        if not isinstance(output, dict) or str(output.get("filename") or "") != filename:
            continue
        output_subfolder = str(output.get("subfolder") or "")
        output_type = str(output.get("type") or "output")
        if output_subfolder and output_subfolder != subfolder:
            continue
        if output.get("type") and output_type != view_type:
            continue
        return output
    return None


def direct_sidecar_for_prompt(prompt_id: str) -> tuple[Path, dict] | None:
    if not re.fullmatch(
        r"(?:(?:local-|run_)[A-Za-z0-9_-]{6,80}|[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
        prompt_id or "",
        re.IGNORECASE,
    ):
        return None
    try:
        sidecars = COMFY_OUTPUT.rglob("*.castle.json")
    except OSError:
        return None
    for sidecar in sidecars:
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("promptId") == prompt_id:
            return sidecar, data
    return None


def direct_sidecar_owner_email(sidecar_path: Path, data: dict, prompt_id: str) -> str:
    """Resolve a durable direct-output owner after the job index is evicted."""

    pid = str(prompt_id or "").strip()
    if str(data.get("promptId") or "") != pid:
        return ""
    suffix = ".castle.json"
    if not sidecar_path.name.endswith(suffix):
        return ""
    media_path = sidecar_path.with_name(sidecar_path.name[:-len(suffix)])
    raw_receipt = data.get("provenance")
    if raw_receipt is not None:
        verified = verify_output_provenance_receipt(media_path, raw_receipt)
        if verified:
            if str(verified.get("promptId") or "") != pid:
                return ""
            return normalize_email(str(verified.get("ownerEmail") or ""))
        if isinstance(raw_receipt, dict) and any(
            raw_receipt.get(key) for key in ("signature", "contentSha256", "authority")
        ):
            return ""
    # Legacy direct outputs predate signed provenance. Their owner-bound
    # server sidecar remains the same fallback used by view_item_owner_email.
    return job_owner_email(pid, data, {})


def direct_history_parameters(data: dict) -> list[dict]:
    saved = data.get("detailParameters")
    if isinstance(saved, list):
        parameters = []
        for raw in saved:
            if not isinstance(raw, dict):
                continue
            parameter_id = str(raw.get("id") or "").strip()[:80]
            label = str(raw.get("label") or "").strip()[:160]
            value = _primitive_prompt_value(raw.get("value"))
            if parameter_id and label and value not in (None, "", [], {}):
                parameters.append({"id": parameter_id, "label": label, "value": value})
        if parameters:
            return parameters

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    parameters = []
    prompt_fields = (
        ("positive", "Prompt"),
        ("instruction", "修改指令"),
        ("integrated_multimodal_description", "Prompt"),
        ("subject_definitions", "subject_definitions"),
        ("summary", "summary"),
        ("retention_analysis", "retention_analysis"),
        ("detailed_description", "detailed_description"),
        ("overall_soundscape", "overall_soundscape"),
        ("non_diegetic_music", "non_diegetic_music"),
        ("text", "文字"),
        ("speech", "文字"),
    )
    seen_labels = set()
    for key, label in prompt_fields:
        value = _primitive_prompt_value(settings.get(key))
        if value in (None, "", [], {}) or (label, value) in seen_labels:
            continue
        parameters.append({"id": key, "label": label, "value": value})
        seen_labels.add((label, value))
    if not any(parameter.get("label") == "文字" for parameter in parameters):
        detail_nodes = data.get("detailNodes") if isinstance(data.get("detailNodes"), list) else []
        for node in detail_nodes:
            if not isinstance(node, dict) or str(node.get("type") or "") not in {
                "VoxCPM2", "VoxCPMTTS", "CastleVoxCPMTTS", "GuardedBreezyVoiceTTS",
            }:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), list) else []
            text_input = next(
                (
                    item.get("value") for item in inputs
                    if isinstance(item, dict) and str(item.get("name") or "") in {"text", "speech"}
                ),
                None,
            )
            text_value = _primitive_prompt_value(text_input)
            if text_value not in (None, "", [], {}):
                parameters.append({"id": "text", "label": "文字", "value": text_value})
                break
    if not any(parameter.get("label") == "文字" for parameter in parameters):
        chunks = data.get("chunks") if isinstance(data.get("chunks"), list) else []
        chunk_text = [
            str(chunk.get("text") or "")
            for chunk in chunks[:50]
            if isinstance(chunk, dict) and str(chunk.get("text") or "")
        ]
        if chunk_text:
            parameters.append({"id": "text", "label": "文字", "value": chunk_text})
    negative = _primitive_prompt_value(settings.get("negative"))
    if negative not in (None, "", [], {}):
        parameters.append({"id": "negativePrompt", "label": "Negative prompt", "value": negative})
    seed = settings.get("seed")
    if (
        isinstance(seed, int) and not isinstance(seed, bool)
        or isinstance(seed, str) and bool(seed)
    ):
        parameters.append({"id": "seed", "label": "Seed", "value": str(seed)})
    width = summary.get("width") or settings.get("width")
    height = summary.get("height") or settings.get("height")
    if (
        isinstance(width, int) and not isinstance(width, bool) and width > 0
        and isinstance(height, int) and not isinstance(height, bool) and height > 0
    ):
        parameters.append({"id": "dimensions", "label": "尺寸", "value": f"{width} × {height}"})
    scalar_fields = (
        ("frames", "影格數", settings.get("frames") or summary.get("frames")),
        ("fps", "FPS", summary.get("fps") or settings.get("fps")),
        ("steps", "步數", settings.get("steps")),
        ("cfg", "CFG", settings.get("cfg")),
        ("acceleration", "加速模式", settings.get("acceleration")),
    )
    for parameter_id, label, value in scalar_fields:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or value in ("", 0, 0.0):
            continue
        parameters.append({"id": parameter_id, "label": label, "value": value})
    return parameters


def direct_history_detail(prompt_id: str, sidecar_data: dict | None = None) -> dict | None:
    if sidecar_data is None:
        found = direct_sidecar_for_prompt(prompt_id)
        if not found:
            return None
        _sidecar, data = found
    else:
        data = sidecar_data
    workflow = str(data.get("workflow") or "").strip()
    guarded_voice = workflow == "castle-voice-speak-guarded-web"
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    chunks = data.get("chunks") if isinstance(data.get("chunks"), list) else []
    detail_nodes = data.get("detailNodes")
    if isinstance(detail_nodes, list):
        nodes = detail_nodes
    elif guarded_voice:
        nodes = [
            {
                "id": "guard",
                "type": "GuardedBreezyVoiceTTS",
                "inputs": [
                    {"name": "persona", "value": settings.get("persona", "")},
                    {"name": "asrModel", "value": settings.get("asrModel", "")},
                    {"name": "tries", "value": settings.get("tries", "")},
                    {"name": "cerThreshold", "value": settings.get("cerThreshold", "")},
                    {"name": "chunks", "value": summary.get("chunks", len(chunks))},
                    {"name": "rerolls", "value": summary.get("rerolls", 0)},
                    {"name": "text", "value": [str(chunk.get("text", "")) for chunk in chunks[:50] if isinstance(chunk, dict)]},
                ],
            }
        ]
    else:
        nodes = []
    outputs = data.get("outputs") if isinstance(data.get("outputs"), list) else []
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    return {
        "promptId": prompt_id,
        "workflow": workflow,
        "featureId": str(
            data.get("featureId")
            or provenance.get("featureId")
            or workflow_feature_id(workflow)
            or ""
        ),
        "feature": str(data.get("feature") or ("打字即說（高品質）" if guarded_voice else workflow_label(workflow))),
        "status": str(data.get("status") or ""),
        "completed": str(data.get("status") or "") == "completed",
        "created": int(data.get("created") or 0),
        "parameters": direct_history_parameters(data),
        "nodes": nodes,
        "outputs": outputs,
        **prompt_ui_metadata(data),
    }


def update_local_job(job_id: str, **fields) -> None:
    with _LOCAL_JOB_LOCK:
        job = LOCAL_JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated"] = time.time()


def local_job_snapshot() -> list[dict]:
    now = time.time()
    jobs = []
    with _LOCAL_JOB_LOCK:
        for job_id, job in list(LOCAL_JOBS.items()):
            terminal = job.get("state") in ("completed", "error", "cancelled")
            if terminal and now - float(job.get("updated") or 0) > LOCAL_JOB_DONE_TTL:
                LOCAL_JOBS.pop(job_id, None)
                continue
            jobs.append(dict(job))
    jobs.sort(key=lambda item: (item.get("state") != "running", item.get("created", 0)))
    pending_position = 1
    for job in jobs:
        if job.get("state") == "pending":
            job["position"] = pending_position
            pending_position += 1
    return jobs


def local_active_counts(jobs: list[dict]) -> tuple[int, int, int]:
    running = sum(1 for job in jobs if job.get("state") in ("running", "cancel_requested"))
    pending = sum(1 for job in jobs if job.get("state") == "pending")
    return running, pending, running + pending


def _csv_int(value, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def nvidia_smi_snapshot() -> dict:
    try:
        gpu_raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=2,
        )
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi not found", "gpus": [], "processes": []}
    except (subprocess.SubprocessError, OSError) as error:
        return {"available": False, "error": str(error), "gpus": [], "processes": []}

    gpus = []
    for line in gpu_raw.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        total = _csv_int(parts[2])
        used = _csv_int(parts[3])
        free = _csv_int(parts[4])
        util = _csv_int(parts[5])
        gpus.append({
            "index": _csv_int(parts[0]),
            "name": parts[1],
            "memory_total_mb": total,
            "memory_used_mb": used,
            "memory_free_mb": free,
            "utilization_gpu_pct": util,
        })

    processes = []
    try:
        proc_raw = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=2,
        )
        for line in proc_raw.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue
            processes.append({
                "pid": _csv_int(parts[0]),
                "process_name": parts[1],
                "used_memory_mb": _csv_int(parts[2]),
            })
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        processes = []

    return {"available": True, "error": "", "gpus": gpus, "processes": processes}


def request_comfy_json(path: str, timeout: int = 2) -> dict:
    req = urllib.request.Request(f"{DEFAULT_COMFY}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def comfy_queue_snapshot() -> dict:
    try:
        queue = request_comfy_json("/queue")
        jobs = queue_job_items(queue)
        running, pending, total = local_active_counts(jobs)
        return {
            "ok": True,
            "state": "running" if running else ("queued" if pending else "idle"),
            "running": running,
            "pending": pending,
            "total": total,
            "external": sum(1 for job in jobs if job.get("source") == "external"),
        }
    except Exception as error:
        return {
            "ok": False,
            "state": "error",
            "running": 0,
            "pending": 0,
            "total": 0,
            "external": 0,
            "error": str(error),
        }


def orchestrator_resources_snapshot() -> dict:
    status = castle_orchestrator.status_api()
    reservation_data = castle_orchestrator.resource_reservations_api().get("reservations") or {}
    active_reservations = reservation_data.get("active") if isinstance(reservation_data, dict) else []
    active_reservations = active_reservations if isinstance(active_reservations, list) else []
    gpu_queue = status.get("gpu_queue") if isinstance(status.get("gpu_queue"), dict) else {}
    scheduler = status.get("scheduler") if isinstance(status.get("scheduler"), dict) else {}
    active = status.get("active") if isinstance(status.get("active"), list) else []
    gpu_tasks = [task for task in active if str(task.get("lane") or "") == "gpu"]
    cpu_tasks = [task for task in active if str(task.get("lane") or "cpu") != "gpu"]
    local_jobs = local_job_snapshot()
    local_running, local_pending, local_total = local_active_counts(local_jobs)
    comfy = comfy_queue_snapshot()
    nvidia = nvidia_smi_snapshot()
    gpu_observed = (nvidia.get("gpus") or [{}])[0] if nvidia.get("gpus") else {}
    free_mb = gpu_observed.get("memory_free_mb")
    util_pct = gpu_observed.get("utilization_gpu_pct")
    min_free = int(gpu_queue.get("min_free_mb") or 0)
    max_util = int(gpu_queue.get("max_util") or 0)
    blocked_by = []
    if scheduler.get("paused"):
        blocked_by.append("scheduler.paused")
    gpu_reservations = [item for item in active_reservations if item.get("resource_id") in ("gpu:rtx5090", "service:comfyui")]
    if gpu_reservations:
        blocked_by.append("resource.reserved")
    if gpu_tasks:
        blocked_by.append("orchestrator.gpu_active")
    if comfy.get("running"):
        blocked_by.append("comfyui.running")
    if isinstance(free_mb, int) and min_free and free_mb < min_free:
        blocked_by.append("gpu.memory_below_threshold")
    if isinstance(util_pct, int) and max_util and util_pct > max_util:
        blocked_by.append("gpu.utilization_above_threshold")
    if not nvidia.get("available"):
        gpu_state = "unknown"
    elif blocked_by:
        gpu_state = "busy"
    else:
        gpu_state = "available"
    resources = [
        {
            "id": "gpu:rtx5090",
            "type": "gpu",
            "label": gpu_observed.get("name") or "RTX 5090",
            "state": gpu_state,
            "blocked_by": blocked_by,
            "reservations": gpu_reservations,
            "observed": gpu_observed,
            "processes": nvidia.get("processes") or [],
            "policy": {
                "queue_script": gpu_queue.get("script") or "",
                "queue_script_exists": bool(gpu_queue.get("script_exists")),
                "min_free_mb": min_free,
                "max_util_pct": max_util,
                "interval_seconds": int(gpu_queue.get("interval") or 0),
            },
            "orchestrator": {
                "running": sum(1 for task in gpu_tasks if task.get("status") in ("running", "cancel_requested")),
                "pending": sum(1 for task in gpu_tasks if task.get("status") == "pending"),
                "tasks": gpu_tasks,
            },
        },
        {
            "id": "service:comfyui",
            "type": "service",
            "label": "ComfyUI",
            "state": comfy["state"],
            "local_url": DEFAULT_COMFY,
            "queue": comfy,
            "reservations": [item for item in active_reservations if item.get("resource_id") == "service:comfyui"],
        },
        {
            "id": "lane:cpu",
            "type": "lane",
            "label": "CPU/API lane",
            "state": "reserved" if any(item.get("resource_id") == "lane:cpu" for item in active_reservations) else ("running" if cpu_tasks else "idle"),
            "reservations": [item for item in active_reservations if item.get("resource_id") == "lane:cpu"],
            "orchestrator": {
                "running": sum(1 for task in cpu_tasks if task.get("status") in ("running", "cancel_requested")),
                "pending": sum(1 for task in cpu_tasks if task.get("status") == "pending"),
                "tasks": cpu_tasks,
            },
        },
        {
            "id": "local:web",
            "type": "local",
            "label": "Castle local jobs",
            "state": "running" if local_running else ("queued" if local_pending else "idle"),
            "reservations": [item for item in active_reservations if item.get("resource_id") == "local:web"],
            "running": local_running,
            "pending": local_pending,
            "total": local_total,
        },
    ]
    return {
        "ok": True,
        "scheduler": scheduler,
        "timestamp": time.time(),
        "summary": {
            "gpu_state": gpu_state,
            "gpu_blocked_by": blocked_by,
            "comfyui_state": comfy["state"],
            "comfyui_running": comfy["running"],
            "comfyui_pending": comfy["pending"],
            "comfyui_external": comfy["external"],
            "orchestrator_gpu_active": len(gpu_tasks),
            "local_active": local_total,
            "gpu_processes": len(nvidia.get("processes") or []),
            "reservations_active": len(active_reservations),
        },
        "reservations": active_reservations,
        "resources": resources,
    }


def guarded_language_for_chunks(chunks: list[str]) -> str:
    joined = "".join(chunks)
    return "zh" if _CJK_RE.search(joined) else "en"


def local_job_error_items(jobs: list[dict], limit: int = 4) -> list[dict]:
    errors = []
    for job in jobs:
        if job.get("state") != "error":
            continue
        errors.append(
            {
                "promptId": job.get("promptId", ""),
                "workflow": job.get("workflow", ""),
                "feature": job.get("feature", "高品質語音"),
                "node": "GuardedBreezyVoiceTTS",
                "type": "LocalJobError",
                "message": str(job.get("error") or "高品質語音失敗")[:160],
                "timestamp": int(job.get("updated") or job.get("created") or 0),
            }
        )
    errors.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return errors[:limit]


def orchestrator_job_snapshot() -> list[dict]:
    try:
        status = castle_orchestrator.status_api()
    except Exception:
        return []
    jobs = []
    active = status.get("active") if isinstance(status, dict) else []
    for index, task in enumerate(active or [], start=1):
        run_id = str(task.get("run_id") or "")
        task_id = str(task.get("id") or "")
        task_status = str(task.get("status") or "pending")
        state = "running" if task_status in ("running", "cancel_requested") else "pending"
        progress_data = task.get("progress") if isinstance(task.get("progress"), dict) else {}
        label = str(progress_data.get("label") or ("取消中" if task_status == "cancel_requested" else ("等待 GPU/CPU lane" if state == "pending" else "執行中")))
        pct = int(progress_data.get("pct") or progress_data.get("value") or (10 if state == "running" else 0) or 0)
        if progress_data.get("event") == "task.gpu_queue":
            pct = 0
        jobs.append({
            "promptId": run_id,
            "taskId": task_id,
            "state": state,
            "position": index if state == "pending" else None,
            "workflow": str(task.get("input", {}).get("_workflow_type") or task.get("task_type") or "orchestrator"),
            "feature": f"{task.get('lane', 'cpu').upper()} · {task.get('task_type', 'orchestrator')}",
            "source": "orchestrator",
            "nodeCount": 1,
            "outputCount": 0,
            "outputNodes": [str(task.get("task_type") or "orchestrator")],
            "nodeTypes": {"orchestrator": str(task.get("task_type") or "")},
            "progress": {"pct": max(0, min(100, pct)), "label": label, "node": ""},
            "created": float(task.get("created_at") or 0),
            "updated": float(task.get("updated_at") or 0),
            "lane": str(task.get("lane") or "cpu"),
        })
    return jobs


def start_guarded_voice_job(settings: dict) -> dict:
    persona = (settings.get("persona") or "").strip()
    if not persona:
        raise ValueError("請從電腦聲音庫選擇一個聲音")
    chunks = prepare_voice_script_chunks(settings.get("text") or "", "要說的文字", VOICE_SPEAK_MAX_CHARS)
    if not VOICE_LAB_PYTHON.is_file():
        raise ValueError(f"找不到 voice-lab Python：{VOICE_LAB_PYTHON}")
    if not GPU_QUEUE_SCRIPT.is_file():
        raise ValueError(f"找不到 GPU queue script：{GPU_QUEUE_SCRIPT}")
    prefix = voice_speak_output_prefix(settings, persona)
    output = output_path_from_prefix(prefix, ".wav")
    sidecar = direct_sidecar_path(output)
    workflow = "castle-voice-speak-guarded-web"
    result = castle_orchestrator.create_run_api({
        "workflow_type": "castle.voice_speak_guarded",
        "title": "打字即說（高品質）",
        "idempotency_key": f"voice-speak-{int(time.time())}-{secrets.token_hex(8)}",
        "input": {
            "persona": persona,
            "chunks": chunks,
            "output_path": str(output),
            "sidecar_path": str(sidecar),
            "workflow": workflow,
            "feature": "打字即說（高品質）",
            "voice_python": str(VOICE_LAB_PYTHON),
            "worker_script": str(GUARDED_TTS_SCRIPT),
            "breezy_url": BREEZY_URL,
            "whisper_model": GUARDED_WHISPER_MODEL,
            "language": guarded_language_for_chunks(chunks),
            "tries": GUARDED_TRIES,
            "cer": GUARDED_CER,
            "pause": VOICE_CHUNK_PAUSE_SECONDS,
            "gpu_queue_python": GPU_QUEUE_PYTHON,
            "gpu_queue_script": str(GPU_QUEUE_SCRIPT),
            "gpu_min_free_mb": GUARDED_MIN_FREE_MB,
            "gpu_max_util": GUARDED_MAX_UTIL,
            "gpu_queue_interval": GUARDED_QUEUE_INTERVAL,
            "settings": {
                "persona": persona,
                "qualityGate": settings.get("qualityGate", True),
                "prefix": settings.get("prefix") or "",
                "characterId": settings.get("_characterId") or "",
                "actionId": settings.get("_characterActionId") or "",
                "variantId": settings.get("_characterVariantId") or "",
            },
        },
    })
    run_id = result["run"]["id"]
    return {"ok": True, "prompt_id": run_id, "run_id": run_id, "local": True, "orchestrator": True}


def start_guarded_voice_job_legacy(settings: dict) -> dict:
    persona = (settings.get("persona") or "").strip()
    if not persona:
        raise ValueError("請從電腦聲音庫選擇一個聲音")
    chunks = prepare_voice_script_chunks(settings.get("text") or "", "要說的文字", VOICE_SPEAK_MAX_CHARS)
    if not VOICE_LAB_PYTHON.is_file():
        raise ValueError(f"找不到 voice-lab Python：{VOICE_LAB_PYTHON}")
    if not GPU_QUEUE_SCRIPT.is_file():
        raise ValueError(f"找不到 GPU queue script：{GPU_QUEUE_SCRIPT}")
    prefix = voice_speak_output_prefix(settings, persona)
    output = output_path_from_prefix(prefix, ".wav")
    sidecar = direct_sidecar_path(output)
    job_id = f"local-{secrets.token_hex(8)}"
    job_dir = GUARDED_JOB_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        job_dir.chmod(0o700)
    except OSError:
        pass
    chunks_path = job_dir / "chunks.json"
    chunks_path.write_text(json.dumps({"chunks": chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
    cancel_file = job_dir / "cancel"
    workflow = "castle-voice-speak-guarded-web"
    job = {
        "promptId": job_id,
        "state": "pending",
        "position": 1,
        "workflow": workflow,
        "feature": "打字即說（高品質）",
        "source": "castle",
        "nodeCount": len(chunks),
        "outputCount": 1,
        "outputNodes": ["GuardedBreezyVoiceTTS"],
        "nodeTypes": {"guard": "GuardedBreezyVoiceTTS"},
        "progress": {"pct": 0, "label": "等待 GPU 佇列"},
        "created": time.time(),
        "updated": time.time(),
        "cancelFile": str(cancel_file),
        "output": str(output),
        "workflow": workflow,
    }
    with _LOCAL_JOB_LOCK:
        LOCAL_JOBS[job_id] = job
    thread = threading.Thread(
        target=run_guarded_voice_job,
        args=(job_id, persona, chunks_path, output, sidecar, cancel_file, workflow),
        daemon=True,
    )
    thread.start()
    return {"ok": True, "prompt_id": job_id, "local": True}


def run_guarded_voice_job(
    job_id: str,
    persona: str,
    chunks_path: Path,
    output: Path,
    sidecar: Path,
    cancel_file: Path,
    workflow: str,
) -> None:
    argv = [
        GPU_QUEUE_PYTHON,
        str(GPU_QUEUE_SCRIPT),
        "--min-free-mb", str(GUARDED_MIN_FREE_MB),
        "--max-util", str(GUARDED_MAX_UTIL),
        "--interval", str(GUARDED_QUEUE_INTERVAL),
        "--",
        str(VOICE_LAB_PYTHON),
        str(GUARDED_TTS_SCRIPT),
        "--persona", persona,
        "--chunks", str(chunks_path),
        "--output", str(output),
        "--sidecar", str(sidecar),
        "--job-id", job_id,
        "--workflow", workflow,
        "--breezy-url", BREEZY_URL,
        "--whisper-model", GUARDED_WHISPER_MODEL,
        "--language", guarded_language_for_chunks(load_chunks_for_job(chunks_path)),
        "--tries", str(GUARDED_TRIES),
        "--cer", str(GUARDED_CER),
        "--pause", str(VOICE_CHUNK_PAUSE_SECONDS),
        "--cancel-file", str(cancel_file),
    ]
    update_local_job(job_id, progress={"pct": 0, "label": "等待 GPU 佇列"})
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            cwd=str(ROOT),
        )
        update_local_job(job_id, pid=proc.pid)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("CASTLE_PROGRESS "):
                try:
                    progress = json.loads(line.split(" ", 1)[1])
                except json.JSONDecodeError:
                    continue
                state = "running" if progress.get("stage") not in ("cancelled", "dry_run") else "pending"
                if progress.get("stage") == "done":
                    state = "running"
                update_local_job(
                    job_id,
                    state=state,
                    progress={"pct": int(progress.get("pct") or 0), "label": str(progress.get("label") or "")},
                )
            elif line.startswith("[gpu-queue]"):
                update_local_job(job_id, state="pending", progress={"pct": 0, "label": line[:120]})
        code = proc.wait()
        if cancel_file.exists() or code == 130:
            update_local_job(job_id, state="cancelled", progress={"pct": 100, "label": "已取消"})
            try:
                output.unlink()
            except OSError:
                pass
            return
        if code != 0:
            raise RuntimeError(f"guarded voice worker exited {code}")
        update_local_job(job_id, state="completed", progress={"pct": 100, "label": "完成"})
    except Exception as error:
        update_local_job(job_id, state="error", error=str(error), progress={"pct": 100, "label": "失敗"})
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass


def load_chunks_for_job(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        chunks = data.get("chunks") if isinstance(data, dict) else []
        return [str(chunk) for chunk in chunks if str(chunk).strip()]
    except Exception:
        return []


def voxcpm_lora_path(profile: str) -> Path | None:
    if profile == "custom_english":
        return CUSTOM_ENGLISH_LORA
    if profile == "custom_english_alt":
        return CUSTOM_ENGLISH_ULTRACLEAN_LORA
    return None


def voxcpm_profile_reference(profile: str) -> tuple[Path | None, str]:
    if profile in ("custom_english", "custom_english_alt"):
        return CUSTOM_ENGLISH_REFERENCE_AUDIO, CUSTOM_ENGLISH_REFERENCE_TEXT
    return None, ""


def voiceover_batch_id() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def validate_voiceover_batch_id(batch_id: str) -> str:
    batch_id = str(batch_id or "").strip()
    if not VOICEOVER_BATCH_ID_RE.fullmatch(batch_id):
        raise ValueError("批次 ID 格式錯誤")
    return batch_id


def voiceover_batch_dir(batch_id: str) -> Path:
    batch_id = validate_voiceover_batch_id(batch_id)
    return VOICEOVER_ROOT / batch_id


def voiceover_read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback
    except json.JSONDecodeError:
        return fallback


def voiceover_atomic_write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def voiceover_clean_pptx_text(text: str) -> str:
    text = (text or "").translate(str.maketrans({
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }))
    text = re.sub(r"#(\d)", r"number \1", text)
    text = re.sub(r"\s+", " ", text.replace("\r", "\n").replace("\n", " ")).strip()
    return text


def _pptx_xml_bytes(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError as error:
        raise ValueError("PPTX 結構不完整") from error
    if info.file_size > VOICEOVER_MAX_XML_MEMBER_BYTES:
        raise ValueError("PPTX XML 成員太大")
    raw = zf.read(info)
    if len(raw) > VOICEOVER_MAX_XML_MEMBER_BYTES:
        raise ValueError("PPTX XML 成員太大")
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("PPTX XML 不可包含 DTD 或 ENTITY")
    return raw


def _pptx_rels(zf: zipfile.ZipFile, name: str) -> dict[str, str]:
    root = ElementTree.fromstring(_pptx_xml_bytes(zf, name))
    rels = {}
    for rel in root:
        rid = str(rel.attrib.get("Id") or "")
        target = str(rel.attrib.get("Target") or "")
        if rid and target:
            rels[rid] = target
    return rels


def _pptx_join_target(base: str, target: str) -> str:
    if target.startswith("/"):
        target = target.lstrip("/")
    else:
        base_parts = base.split("/")[:-1]
        for part in target.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if base_parts:
                    base_parts.pop()
            else:
                base_parts.append(part)
        target = "/".join(base_parts)
    if not target.startswith("ppt/"):
        target = f"ppt/{target}" if not target.startswith("ppt") else target
    return target


def extract_pptx_notes_segments(raw: bytes) -> dict:
    if len(raw) > VOICEOVER_MAX_PPTX_BYTES:
        raise ValueError("PPTX 檔案太大")
    if not raw.startswith(b"PK\x03\x04"):
        raise ValueError("請上傳有效的 PPTX 檔案")
    import io
    ns_p = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    ns_r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    ns_a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    segments = []
    skipped = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        presentation = ElementTree.fromstring(_pptx_xml_bytes(zf, "ppt/presentation.xml"))
        presentation_rels = _pptx_rels(zf, "ppt/_rels/presentation.xml.rels")
        slide_ids = presentation.findall(f".//{ns_p}sldId")
        for slide_number, slide_id in enumerate(slide_ids, start=1):
            rid = slide_id.attrib.get(f"{ns_r}id")
            slide_target = presentation_rels.get(str(rid or ""))
            if not slide_target:
                skipped.append(slide_number)
                continue
            slide_path = _pptx_join_target("ppt/presentation.xml", slide_target)
            slide_name = slide_path.rsplit("/", 1)[-1]
            slide_rels_path = slide_path.rsplit("/", 1)[0] + f"/_rels/{slide_name}.rels"
            try:
                slide_rels = _pptx_rels(zf, slide_rels_path)
            except ValueError:
                skipped.append(slide_number)
                continue
            notes_path = ""
            for target in slide_rels.values():
                if "notesSlide" in target:
                    notes_path = _pptx_join_target(slide_path, target)
                    break
            if not notes_path:
                skipped.append(slide_number)
                continue
            try:
                notes_root = ElementTree.fromstring(_pptx_xml_bytes(zf, notes_path))
            except ValueError:
                raise
            paragraphs = []
            for para in notes_root.findall(f".//{ns_a}p"):
                text = "".join((node.text or "") for node in para.findall(f".//{ns_a}t"))
                text = voiceover_clean_pptx_text(text)
                if text:
                    paragraphs.append(text)
            text = voiceover_clean_pptx_text(" ".join(paragraphs))
            if text:
                segments.append({"index": len(segments) + 1, "slide": slide_number, "text": text})
            else:
                skipped.append(slide_number)
    return {"ok": True, "segments": segments, "skipped": skipped}


def validate_voiceover_segments(raw_segments) -> list[dict]:
    if not isinstance(raw_segments, list):
        raise ValueError("請提供段落")
    if not 1 <= len(raw_segments) <= 120:
        raise ValueError("段落數量必須介於 1 到 120")
    segments = []
    for expected, item in enumerate(raw_segments, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {expected} 段格式錯誤")
        index = int(item.get("index") or expected)
        if index != expected:
            raise ValueError("段落編號必須連續")
        text = str(item.get("text") or "").strip()
        if not text:
            raise ValueError(f"第 {expected} 段不能空白")
        if len(text) > VOICE_SPEAK_MAX_CHARS:
            raise ValueError(f"第 {expected} 段最多 {VOICE_SPEAK_MAX_CHARS} 字")
        reject_voice_hallucination_risks(text, f"第 {expected} 段")
        text = normalize_voice_script(text)
        reject_voice_hallucination_risks(text, f"第 {expected} 段")
        segments.append({"index": expected, "text": text})
    return segments


def voiceover_terminal_status(status: str) -> bool:
    return status in {"completed", "completed_approved", "failed", "cancelled"}


def voiceover_run_status(run_id: str) -> str:
    if not run_id:
        return ""
    try:
        data = castle_orchestrator.get_run_api(run_id)
        return str((data.get("run") or {}).get("status") or "")
    except Exception:
        return ""


def voiceover_strip_old(record: dict) -> dict:
    clean = dict(record)
    clean.pop("old", None)
    return clean


def voiceover_merge_redo_if_ready(batch_dir: Path, batch: dict, manifest: dict) -> tuple[dict, dict]:
    redo_runs = batch.get("redo_runs") if isinstance(batch.get("redo_runs"), dict) else {}
    if not redo_runs:
        return batch, manifest
    changed = False
    for key, run_id in list(redo_runs.items()):
        try:
            index = int(key)
        except ValueError:
            continue
        status = voiceover_run_status(str(run_id or ""))
        if not voiceover_terminal_status(status):
            continue
        mini_path = batch_dir / f"manifest.redo_{index:02d}.json"
        if not mini_path.exists():
            continue
        mini = voiceover_read_json(mini_path, {})
        mini_segments = mini.get("segments") if isinstance(mini.get("segments"), list) else []
        new_record = next((item for item in mini_segments if int(item.get("index") or 0) == index), None)
        if not isinstance(new_record, dict):
            continue
        main_segments = manifest.get("segments") if isinstance(manifest.get("segments"), list) else []
        old_record = next((item for item in main_segments if int(item.get("index") or 0) == index), None)
        merged = voiceover_strip_old(new_record)
        if isinstance(old_record, dict):
            merged["old"] = voiceover_strip_old(old_record)
        manifest["segments"] = [item for item in main_segments if int(item.get("index") or 0) != index] + [merged]
        manifest["segments"].sort(key=lambda item: int(item.get("index") or 0))
        manifest["total_audio_s"] = round(sum(float(item.get("duration_s") or 0) for item in manifest["segments"]), 2)
        try:
            mini_path.unlink()
        except FileNotFoundError:
            pass
        changed = True
    if changed:
        voiceover_atomic_write_json(batch_dir / "manifest.json", manifest)
        voiceover_atomic_write_json(batch_dir / "batch.json", batch)
    return batch, manifest


def voiceover_restore_segment(batch_dir: Path, index: int) -> dict:
    current = batch_dir / f"seg_{index:02d}.wav"
    old_file = batch_dir / f"seg_{index:02d}_old.wav"
    swap = batch_dir / f"seg_{index:02d}.swap.wav"
    if not current.is_file():
        raise ValueError("找不到目前版本音檔")
    if not old_file.is_file():
        raise ValueError("沒有可還原的上一版")
    manifest_path = batch_dir / "manifest.json"
    manifest = voiceover_read_json(manifest_path, {})
    segments = manifest.get("segments") if isinstance(manifest.get("segments"), list) else []
    record = next((item for item in segments if int(item.get("index") or 0) == index), None)
    if not isinstance(record, dict) or not isinstance(record.get("old"), dict):
        raise ValueError("manifest 沒有上一版記錄")
    current.rename(swap)
    old_file.rename(current)
    swap.rename(old_file)
    previous = voiceover_strip_old(record)
    restored = voiceover_strip_old(record["old"])
    restored["index"] = index
    restored["file"] = current.name
    previous["file"] = current.name
    restored["old"] = previous
    manifest["segments"] = [item for item in segments if int(item.get("index") or 0) != index] + [restored]
    manifest["segments"].sort(key=lambda item: int(item.get("index") or 0))
    voiceover_atomic_write_json(manifest_path, manifest)
    return restored


def resolve_uploaded_audio_path(name: str) -> Path:
    bare = str(name or "")
    if not bare:
        raise ValueError("請從聲音庫選擇或上傳參考音訊")
    if bare != Path(bare).name:
        raise ValueError("Invalid audio filename")
    path = (COMFY_INPUT / bare).resolve()
    if not path.is_relative_to(COMFY_INPUT.resolve()) or not path.is_file():
        raise ValueError("找不到參考音訊")
    return path


def start_voxcpm_voice_job(settings: dict, uploads: dict[str, str]) -> dict:
    if not VOXCPM_PYTHON.is_file():
        raise ValueError(f"找不到 VoxCPM Python：{VOXCPM_PYTHON}")
    if not VOXCPM_SCRIPT.is_file():
        raise ValueError(f"找不到 VoxCPM worker：{VOXCPM_SCRIPT}")
    if not GPU_QUEUE_SCRIPT.is_file():
        raise ValueError(f"找不到 GPU queue script：{GPU_QUEUE_SCRIPT}")
    if not (VOXCPM_MODEL_PATH / "config.json").is_file() or not (VOXCPM_MODEL_PATH / "model.safetensors").is_file():
        raise ValueError(f"VoxCPM2 模型尚未就緒：{VOXCPM_MODEL_PATH}")

    mode = str(settings.get("mode") or "lora").strip()
    if mode not in ("design", "reference", "ultimate", "lora"):
        raise ValueError("Unknown VoxCPM mode")
    text = (settings.get("text") or "").strip()
    if not text:
        raise ValueError("請輸入要生成的文字")
    if len(text) > VOICE_SPEAK_MAX_CHARS:
        raise ValueError(f"要生成的文字單次最多 {VOICE_SPEAK_MAX_CHARS} 字")
    reject_voice_hallucination_risks(text, "要生成的文字")
    text = normalize_voice_script(text)
    reject_voice_hallucination_risks(text, "要生成的文字")

    prompt_text = basic_voice_normalize(str(settings.get("promptText") or "").strip())

    profile = str(settings.get("voiceProfile") or "custom_english").strip()
    allowed_profiles = {str(item.get("value") or "") for item in VOXCPM_PROFILES}
    if mode == "lora" and profile not in allowed_profiles:
        raise ValueError(f"不支援的 VoxCPM LoRA Profile：{profile or '空白'}")
    lora = voxcpm_lora_path(profile) if mode == "lora" else None
    if lora is not None and not (lora / "lora_weights.safetensors").is_file():
        raise ValueError(f"找不到 LoRA checkpoint：{lora}")

    reference = None
    if mode in ("reference", "ultimate"):
        reference = resolve_uploaded_audio_path(uploads.get("voice") or "")
    elif mode == "lora":
        profile_reference, profile_prompt = voxcpm_profile_reference(profile)
        if profile_reference is not None:
            if not profile_reference.is_file():
                raise ValueError(f"找不到 Custom English reference：{profile_reference}")
            reference = profile_reference
            prompt_text = profile_prompt
    if mode == "ultimate":
        if not prompt_text:
            voice_id = str(settings.get("voiceId") or "").strip()
            if voice_id:
                with _VOICE_LOCK:
                    voice_entry = next((e for e in load_voice_index() if e.get("id") == voice_id), None)
                library_transcript = str((voice_entry or {}).get("transcript") or "").strip()
                if library_transcript:
                    prompt_text = prepare_voice_reference_text(library_transcript)
        if not prompt_text:
            raise ValueError("Ultimate Clone 需要參考音訊逐字稿:請在表單「參考音訊逐字稿」欄位填入參考音檔實際講的內容,或到 聲音庫 為該聲音補上逐字稿")
        if len(prompt_text) > 500:
            raise ValueError("參考音訊逐字稿最多 500 字")
    elif mode != "lora":
        prompt_text = ""
    speaker_gate = bool(settings.get("speakerGate", True)) and reference is not None
    if speaker_gate:
        if not VOICE_LAB_PYTHON.is_file():
            raise ValueError(f"找不到 speaker gate Python：{VOICE_LAB_PYTHON}")
        if not VOXCPM_SPEAKER_SCRIPT.is_file():
            raise ValueError(f"找不到 speaker gate script：{VOXCPM_SPEAKER_SCRIPT}")

    prefix = output_prefix(settings, "voxcpm-tts")
    output = output_path_from_prefix(prefix, ".wav")
    sidecar = direct_sidecar_path(output)
    workflow = "castle-voxcpm-tts-web"
    result = castle_orchestrator.create_run_api({
        "workflow_type": "castle.voxcpm_tts",
        "title": "VoxCPM 多語音色",
        "idempotency_key": f"voxcpm-tts-{int(time.time())}-{secrets.token_hex(8)}",
        "input": {
            "mode": mode,
            "text": text,
            "prompt_text": prompt_text,
            "reference_audio": str(reference) if reference is not None else "",
            "lora_path": str(lora) if lora is not None else "",
            "output_path": str(output),
            "sidecar_path": str(sidecar),
            "workflow": workflow,
            "feature": "VoxCPM 多語音色",
            "voxcpm_python": str(VOXCPM_PYTHON),
            "worker_script": str(VOXCPM_SCRIPT),
            "model_path": str(VOXCPM_MODEL_PATH),
            "language": str(settings.get("language") or ""),
            "accent": str(settings.get("accent") or ""),
            "profile": profile if mode == "lora" else "",
            "control": str(settings.get("style") or ""),
            "cfg": clamp_float(settings.get("cfg"), 2.0, 1.0, 3.0),
            "steps": clamp_int(settings.get("steps"), 10, 4, 30),
            "seed": "" if str(settings.get("seed")) in ("", "-1", "None", "none") else normalize_seed(settings.get("seed")),
            "speaker_gate": speaker_gate,
            "speaker_threshold": VOXCPM_SPEAKER_THRESHOLD,
            "speaker_max_attempts": VOXCPM_SPEAKER_ATTEMPTS,
            "speaker_python": str(VOICE_LAB_PYTHON),
            "speaker_script": str(VOXCPM_SPEAKER_SCRIPT),
            "gpu_queue_python": GPU_QUEUE_PYTHON,
            "gpu_queue_script": str(GPU_QUEUE_SCRIPT),
            "gpu_min_free_mb": GUARDED_MIN_FREE_MB,
            "gpu_max_util": GUARDED_MAX_UTIL,
            "gpu_queue_interval": GUARDED_QUEUE_INTERVAL,
        },
    })
    run_id = result["run"]["id"]
    return {"ok": True, "prompt_id": run_id, "run_id": run_id, "local": True, "orchestrator": True}


def start_voxcpm_voice_job_legacy(settings: dict, uploads: dict[str, str]) -> dict:
    if not VOXCPM_PYTHON.is_file():
        raise ValueError(f"找不到 VoxCPM Python：{VOXCPM_PYTHON}")
    if not VOXCPM_SCRIPT.is_file():
        raise ValueError(f"找不到 VoxCPM worker：{VOXCPM_SCRIPT}")
    if not GPU_QUEUE_SCRIPT.is_file():
        raise ValueError(f"找不到 GPU queue script：{GPU_QUEUE_SCRIPT}")
    if not (VOXCPM_MODEL_PATH / "config.json").is_file() or not (VOXCPM_MODEL_PATH / "model.safetensors").is_file():
        raise ValueError(f"VoxCPM2 模型尚未就緒：{VOXCPM_MODEL_PATH}")

    mode = str(settings.get("mode") or "lora").strip()
    if mode not in ("design", "reference", "ultimate", "lora"):
        raise ValueError("Unknown VoxCPM mode")
    text = (settings.get("text") or "").strip()
    if not text:
        raise ValueError("請輸入要生成的文字")
    if len(text) > VOICE_SPEAK_MAX_CHARS:
        raise ValueError(f"要生成的文字單次最多 {VOICE_SPEAK_MAX_CHARS} 字")
    reject_voice_hallucination_risks(text, "要生成的文字")
    text = normalize_voice_script(text)
    reject_voice_hallucination_risks(text, "要生成的文字")

    prompt_text = basic_voice_normalize(str(settings.get("promptText") or "").strip())

    profile = str(settings.get("voiceProfile") or "none").strip()
    if mode == "lora" and profile == "none":
        profile = "custom_english"
    lora = voxcpm_lora_path(profile) if mode == "lora" else None
    if lora is not None and not (lora / "lora_weights.safetensors").is_file():
        raise ValueError(f"找不到 LoRA checkpoint：{lora}")

    reference = None
    if mode in ("reference", "ultimate"):
        reference = resolve_uploaded_audio_path(uploads.get("voice") or "")
    elif mode == "lora":
        profile_reference, profile_prompt = voxcpm_profile_reference(profile)
        if profile_reference is not None:
            if not profile_reference.is_file():
                raise ValueError(f"找不到 Custom English reference：{profile_reference}")
            reference = profile_reference
            prompt_text = profile_prompt
    if mode == "ultimate":
        if not prompt_text:
            voice_id = str(settings.get("voiceId") or "").strip()
            if voice_id:
                with _VOICE_LOCK:
                    voice_entry = next((e for e in load_voice_index() if e.get("id") == voice_id), None)
                library_transcript = str((voice_entry or {}).get("transcript") or "").strip()
                if library_transcript:
                    prompt_text = prepare_voice_reference_text(library_transcript)
        if not prompt_text:
            raise ValueError("Ultimate Clone 需要參考音訊逐字稿:請在表單「參考音訊逐字稿」欄位填入參考音檔實際講的內容,或到 聲音庫 為該聲音補上逐字稿")
        if len(prompt_text) > 500:
            raise ValueError("參考音訊逐字稿最多 500 字")
    elif mode != "lora":
        prompt_text = ""
    speaker_gate = bool(settings.get("speakerGate", True)) and reference is not None
    if speaker_gate:
        if not VOICE_LAB_PYTHON.is_file():
            raise ValueError(f"找不到 speaker gate Python：{VOICE_LAB_PYTHON}")
        if not VOXCPM_SPEAKER_SCRIPT.is_file():
            raise ValueError(f"找不到 speaker gate script：{VOXCPM_SPEAKER_SCRIPT}")

    prefix = output_prefix(settings, "voxcpm-tts")
    output = output_path_from_prefix(prefix, ".wav")
    sidecar = direct_sidecar_path(output)
    job_id = f"local-{secrets.token_hex(8)}"
    job_dir = GUARDED_JOB_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        job_dir.chmod(0o700)
    except OSError:
        pass
    cancel_file = job_dir / "cancel"
    workflow = "castle-voxcpm-tts-web"
    job = {
        "promptId": job_id,
        "state": "pending",
        "position": 1,
        "workflow": workflow,
        "feature": "VoxCPM 多語音色",
        "source": "castle",
        "nodeCount": 1,
        "outputCount": 1,
        "outputNodes": ["VoxCPM2"],
        "nodeTypes": {"voxcpm": "VoxCPM2"},
        "progress": {"pct": 0, "label": "等待 GPU 佇列"},
        "created": time.time(),
        "updated": time.time(),
        "cancelFile": str(cancel_file),
        "output": str(output),
    }
    with _LOCAL_JOB_LOCK:
        LOCAL_JOBS[job_id] = job
    thread = threading.Thread(
        target=run_voxcpm_voice_job,
        args=(job_id, settings, mode, text, prompt_text, reference, lora, output, sidecar, cancel_file, workflow),
        daemon=True,
    )
    thread.start()
    return {"ok": True, "prompt_id": job_id, "local": True}


def run_voxcpm_voice_job(
    job_id: str,
    settings: dict,
    mode: str,
    text: str,
    prompt_text: str,
    reference: Path | None,
    lora: Path | None,
    output: Path,
    sidecar: Path,
    cancel_file: Path,
    workflow: str,
) -> None:
    seed = normalize_seed(settings.get("seed"))
    if str(settings.get("seed")) in ("", "-1", "None", "none"):
        seed_arg = []
    else:
        seed_arg = ["--seed", str(seed)]
    argv = [
        GPU_QUEUE_PYTHON,
        str(GPU_QUEUE_SCRIPT),
        "--min-free-mb", str(GUARDED_MIN_FREE_MB),
        "--max-util", str(GUARDED_MAX_UTIL),
        "--interval", str(GUARDED_QUEUE_INTERVAL),
        "--",
        str(VOXCPM_PYTHON),
        str(VOXCPM_SCRIPT),
        "--mode", mode,
        "--text", text,
        "--output", str(output),
        "--sidecar", str(sidecar),
        "--job-id", job_id,
        "--workflow", workflow,
        "--model-path", str(VOXCPM_MODEL_PATH),
        "--language", str(settings.get("language") or ""),
        "--accent", str(settings.get("accent") or ""),
        "--profile", str(settings.get("voiceProfile") or ""),
        "--control", str(settings.get("style") or ""),
        "--cfg", str(clamp_float(settings.get("cfg"), 2.0, 1.0, 3.0)),
        "--steps", str(clamp_int(settings.get("steps"), 10, 4, 30)),
        "--cancel-file", str(cancel_file),
        *seed_arg,
    ]
    if reference is not None:
        argv += ["--reference-audio", str(reference)]
    if prompt_text:
        argv += ["--prompt-text", prompt_text]
    if lora is not None:
        argv += ["--lora-path", str(lora)]
    if bool(settings.get("speakerGate", True)) and reference is not None:
        argv += [
            "--speaker-gate",
            "--speaker-threshold", str(VOXCPM_SPEAKER_THRESHOLD),
            "--speaker-max-attempts", str(VOXCPM_SPEAKER_ATTEMPTS),
            "--speaker-python", str(VOICE_LAB_PYTHON),
            "--speaker-script", str(VOXCPM_SPEAKER_SCRIPT),
        ]
    run_local_worker_process(job_id, argv)


def run_local_worker_process(job_id: str, argv: list[str]) -> None:
    update_local_job(job_id, progress={"pct": 0, "label": "等待 GPU 佇列"})
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            cwd=str(ROOT),
        )
        update_local_job(job_id, pid=proc.pid)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("CASTLE_PROGRESS "):
                try:
                    progress = json.loads(line.split(" ", 1)[1])
                except json.JSONDecodeError:
                    continue
                state = "running"
                if progress.get("stage") == "cancelled":
                    state = "cancelled"
                elif progress.get("stage") == "error":
                    state = "error"
                update_local_job(
                    job_id,
                    state=state,
                    progress={"pct": int(progress.get("pct") or 0), "label": str(progress.get("label") or "")},
                )
            elif line.startswith("[gpu-queue]"):
                update_local_job(job_id, state="pending", progress={"pct": 0, "label": line[:120]})
        code = proc.wait()
        with _LOCAL_JOB_LOCK:
            job = LOCAL_JOBS.get(job_id) or {}
            cancel_file = job.get("cancelFile")
        if (cancel_file and Path(cancel_file).exists()) or code == 130:
            update_local_job(job_id, state="cancelled", progress={"pct": 100, "label": "已取消"})
            return
        if code != 0:
            raise RuntimeError(f"worker exited {code}")
        update_local_job(job_id, state="completed", progress={"pct": 100, "label": "完成"})
    except Exception as error:
        update_local_job(job_id, state="error", error=str(error), progress={"pct": 100, "label": "失敗"})
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass


def voiceover_environment_readiness() -> tuple[bool, str]:
    checks = (
        (VOXCPM_PYTHON.is_file(), "VoxCPM Python"),
        (VOXCPM_SCRIPT.is_file(), "VoxCPM worker"),
        (GPU_QUEUE_SCRIPT.is_file(), "GPU queue"),
        (VOICE_LAB_PYTHON.is_file(), "聲紋檢查 Python"),
        (VOXCPM_SPEAKER_SCRIPT.is_file(), "聲紋檢查 worker"),
        ((VOXCPM_MODEL_PATH / "config.json").is_file(), "VoxCPM config"),
        ((VOXCPM_MODEL_PATH / "model.safetensors").is_file(), "VoxCPM model"),
        ((CUSTOM_ENGLISH_LORA / "lora_weights.safetensors").is_file(), "Custom English LoRA"),
        (CUSTOM_ENGLISH_REFERENCE_AUDIO.is_file(), "Custom English 參考音"),
    )
    missing = [label for ready, label in checks if not ready]
    return not missing, ("" if not missing else "尚未就緒：" + "、".join(missing))


VOICE_CLONE_ENGINE_REQUIRED_NODES = {
    "taiwan": {"CastleBreezyVoiceClone", "LoadAudio", "EmptyAudio", "AudioConcat", "SaveAudio"},
    "f5": {"F5TTSAudioInputs", "LoadAudio", "EmptyAudio", "AudioConcat", "SaveAudio"},
}
VOICE_CLONE_ENGINE_LABELS = {
    "taiwan": "台灣腔 BreezyVoice",
    "f5": "多語 F5",
}


def voice_clone_engine_runtime(available_nodes: set[str] | None) -> dict[str, dict]:
    """Resolve each visible clone engine independently; never substitute one for another."""
    nodes = set(available_nodes or set())
    return {
        engine: {
            "ready": not (missing := sorted(required - nodes)),
            "missingNodes": missing,
        }
        for engine, required in VOICE_CLONE_ENGINE_REQUIRED_NODES.items()
    }


def voice_clone_runtime_reason(runtime: dict[str, dict]) -> str:
    parts = []
    for engine, state in runtime.items():
        missing = state.get("missingNodes") if isinstance(state, dict) else []
        if missing:
            parts.append(f"{VOICE_CLONE_ENGINE_LABELS.get(engine, engine)} 缺少 " + "、".join(missing))
    return "聲音庫克隆目前沒有可用引擎" + (("（" + "；".join(parts) + "）") if parts else "")


H3_FEATURE_CONTRACTS = {
    "h3-text-video": ("h3.t2v.stock.v1",),
    "h3-image-video": (
        "h3.i2v.anime.stock.v1",
        "h3.i2v.realistic.stock.v1",
    ),
    "h3-reference-video": (
        "h3.ref2va-image.anime.stock.v1",
        "h3.ref2va-image.realistic.stock.v1",
    ),
}
H3_VARIANT_CONTRACTS = {
    ("h3-image-video", "anime"): "h3.i2v.anime.stock.v1",
    ("h3-image-video", "realistic"): "h3.i2v.realistic.stock.v1",
    ("h3-reference-video", "anime"): "h3.ref2va-image.anime.stock.v1",
    ("h3-reference-video", "realistic"): "h3.ref2va-image.realistic.stock.v1",
}
PROVIDER_FABRIC_H3_VARIANT_CONTRACTS = {
    "image-video.anime.h3-fl2va.v1": "h3.i2v.anime.stock.v1",
    "image-video.realistic.h3-fl2va.v1": "h3.i2v.realistic.stock.v1",
    "reference-video.anime.h3-ref2va-image.v1":
        "h3.ref2va-image.anime.stock.v1",
    "reference-video.realistic.h3-ref2va-image.v1":
        "h3.ref2va-image.realistic.stock.v1",
}
_H3_READINESS_LOCK = threading.Lock()
_H3_READINESS_CACHE: dict[str, object] = {
    "at": 0.0,
    "snapshot": None,
    "refreshing": False,
}
H3_READINESS_CACHE_SECONDS = 10.0


def _audit_and_cache_minimax_h3_readiness(object_info: dict | None = None):
    try:
        fetcher = (
            (lambda *_args, **_kwargs: object_info)
            if isinstance(object_info, dict)
            else None
        )
        snapshot = audit_minimax_h3_readiness(
            MiniMaxH3ReadinessConfig(),
            **({"object_info_fetcher": fetcher} if fetcher is not None else {}),
        )
    except Exception:
        snapshot = None
    with _H3_READINESS_LOCK:
        _H3_READINESS_CACHE["at"] = time.monotonic()
        _H3_READINESS_CACHE["snapshot"] = snapshot
        _H3_READINESS_CACHE["refreshing"] = False
    return snapshot


def minimax_h3_readiness_snapshot(*, force: bool = False, object_info: dict | None = None):
    """Return cached evidence immediately; refresh inventory off request threads."""
    now = time.monotonic()
    with _H3_READINESS_LOCK:
        cached = _H3_READINESS_CACHE.get("snapshot")
        cached_at = float(_H3_READINESS_CACHE.get("at") or 0.0)
        if not force and cached is not None and now - cached_at < H3_READINESS_CACHE_SECONDS:
            return cached
        if not force:
            if not bool(_H3_READINESS_CACHE.get("refreshing")):
                _H3_READINESS_CACHE["refreshing"] = True
                threading.Thread(
                    target=_audit_and_cache_minimax_h3_readiness,
                    args=(object_info,),
                    name="castle-h3-readiness",
                    daemon=True,
                ).start()
            return cached
    # Generation preflight is deliberately synchronous and outside the cache
    # mutex, so a long model digest never freezes feature/catalog requests.
    return _audit_and_cache_minimax_h3_readiness(object_info)


def provider_fabric_h3_audit_source() -> dict[str, object]:
    """Project lightweight live H3 inventory into the provider gate.

    Full model digests remain part of the queued worker's pre-submit check.
    They must not run on page/catalog requests: a cold digest of the local H3
    model set is tens of gigabytes and used to make role actions disappear (and
    the site stall) after every Web restart.
    """

    closed = {
        "license_blocked": True,
        "authorization_recorded": False,
        "runtime_ready": False,
        "models_ready": False,
        "contracts": {contract_id: False for contract_id in sorted(H3_CONTRACTS)},
    }
    try:
        authorization_path = MiniMaxH3ReadinessConfig().authorization_record_path
        authorization_info = authorization_path.lstat()
        authorized = bool(
            authorization_path.is_file()
            and not authorization_path.is_symlink()
            and authorization_info.st_uid == os.geteuid()
            and authorization_info.st_nlink == 1
            and authorization_info.st_mode & 0o077 == 0
        )
        statuses = {
            str(item.get("id") or ""): item
            for item in feature_status()
            if isinstance(item, dict)
        }
        contract_features = {
            contract_id: feature_id
            for feature_id, contract_ids in H3_FEATURE_CONTRACTS.items()
            for contract_id in contract_ids
        }
        if set(contract_features) != set(H3_CONTRACTS):
            return closed
        h3_statuses = [statuses.get(feature_id) or {} for feature_id in H3_FEATURE_CONTRACTS]
        contracts = {
            contract_id: bool(
                authorized
                and (statuses.get(feature_id) or {}).get("runnable")
                and (statuses.get(feature_id) or {}).get("ready")
            )
            for contract_id, feature_id in sorted(contract_features.items())
        }
        return {
            "license_blocked": not authorized,
            "authorization_recorded": authorized,
            "runtime_ready": all(
                not item.get("missingNodes") and not item.get("runtimeReason")
                for item in h3_statuses
            ),
            "models_ready": all(not item.get("missingModels") for item in h3_statuses),
            "contracts": contracts,
        }
    except Exception:
        return closed


def minimax_h3_contract_ready(contract_id: str, *, force: bool = False, object_info: dict | None = None) -> bool:
    if contract_id not in H3_CONTRACTS:
        return False
    snapshot = minimax_h3_readiness_snapshot(force=force, object_info=object_info)
    if snapshot is None:
        return False
    try:
        return bool(snapshot.for_contract(contract_id).execution_enabled)
    except (AttributeError, KeyError):
        return False


def minimax_h3_contract_for(feature_id: str, visual_class: str | None) -> str:
    if visual_class in (None, ""):
        contracts = H3_FEATURE_CONTRACTS.get(feature_id, ())
        return contracts[0] if contracts else ""
    return H3_VARIANT_CONTRACTS.get((feature_id, str(visual_class or "").lower()), "")


def require_minimax_h3_contract(feature_id: str, visual_class: str | None) -> str:
    if visual_class in (None, ""):
        # Generic Studio I2V/R2V uses the same installed models and native node
        # classes as the already-qualified character variants.  Accept any
        # ready contract for that feature instead of inventing a character
        # class or blocking direct testing.
        for candidate in H3_FEATURE_CONTRACTS.get(feature_id, ()):
            if minimax_h3_contract_ready(candidate, force=True):
                return candidate
        raise ValueError("MiniMax H3 這個模式尚未完成本機驗證")
    contract_id = minimax_h3_contract_for(feature_id, visual_class)
    if not contract_id or not minimax_h3_contract_ready(contract_id, force=True):
        raise ValueError("MiniMax H3 這個角色類型與模式尚未完成本機驗證")
    return contract_id


def feature_status() -> list[dict]:
    object_info = request_json("/object_info")
    available_nodes = set(object_info)
    available_models = available_model_names()
    statuses = []
    for feature in FEATURES:
        missing_nodes = [n for n in feature.get("requiredNodes", []) if n not in available_nodes]
        missing_models = [m for m in feature.get("requiredModels", []) if not model_is_ready(m, available_models)]
        missing_paths = [p for p in feature.get("requiredPaths", []) if not (MODELS_DIR / p).exists()]
        missing_models = missing_models + missing_paths
        is_voice_batch = feature.get("id") == "voice-batch"
        runnable = feature.get("builder") is not None or is_voice_batch
        runtime_ready, runtime_reason = voiceover_environment_readiness() if is_voice_batch else (True, "")
        ready = not missing_nodes and not missing_models and runtime_ready
        h3_contract_ids = H3_FEATURE_CONTRACTS.get(str(feature.get("id") or ""), ())
        h3_ready_contracts = list(h3_contract_ids) if ready and runnable else []
        status = {
            "id": feature["id"],
            "studio": feature["studio"],
            "title": feature["title"],
            "subtitle": feature.get("subtitle", ""),
            "toolGroup": feature.get("toolGroup", ""),
            "versionLabel": feature.get("versionLabel", ""),
            "icon": feature.get("icon", ""),
            "kind": feature.get("kind", "generate"),
            "uiHidden": bool(feature.get("uiHidden")),
            "deprecated": bool(feature.get("deprecated")),
            "advancedTool": bool(feature.get("advancedTool")),
            "characterOnly": bool(feature.get("characterOnly")),
            "operatorOnly": bool(feature.get("operatorOnly")),
            "runnable": runnable,
            "uploads": feature.get("uploads", []),
            "fields": feature_fields_for_client(feature, available_models, available_nodes),
            "note": feature.get("note", ""),
            "missingNodes": missing_nodes,
            "missingModels": missing_models,
            "runtimeReason": runtime_reason,
            "ready": ready if runnable else (feature.get("kind") in ("storage", "text") and ready),
        }
        minimum_uploads = feature.get("minimumUploads")
        if type(minimum_uploads) is int and minimum_uploads > 0:
            status["minimumUploads"] = minimum_uploads
        if h3_contract_ids:
            status["readyContracts"] = h3_ready_contracts
        if feature.get("id") == "voice-speak":
            clone_runtime = voice_clone_engine_runtime(available_nodes)
            clone_engines = [engine for engine, state in clone_runtime.items() if state.get("ready")]
            generic_ready = bool(clone_engines) and not missing_models
            status.update({
                "runtimeOptions": {"cloneEngine": clone_engines},
                "genericReady": generic_ready,
                "genericRuntimeReason": "" if generic_ready else voice_clone_runtime_reason(clone_runtime),
                "genericMissingNodes": sorted({
                    node
                    for state in clone_runtime.values()
                    for node in state.get("missingNodes", [])
                }) if not generic_ready else [],
            })
        statuses.append(status)
    return statuses


def anime_scene_card_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    """Pixel-preserving alpha composite for the first anime photoshoot variant."""
    uploads = uploads or {}
    character_image = uploads.get("image")
    scene_image = uploads.get("scene")
    if not character_image or not scene_image:
        raise ValueError("動漫場景寫真缺少角色造型或場景")
    x = clamp_int(settings.get("compositeX"), 0, 0, 16_384)
    y = clamp_int(settings.get("compositeY"), 0, 0, 16_384)
    prefix = output_prefix(settings, "anime-scene-card")
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": scene_image}},
        "2": {"class_type": "LoadImage", "inputs": {"image": character_image}},
        # ComfyUI LoadImage returns an inverted alpha mask (transparent=1),
        # while ImageCompositeMasked expects foreground coverage (opaque=1).
        "3": {"class_type": "InvertMask", "inputs": {"mask": ["2", 1]}},
        "4": {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": ["1", 0],
                "source": ["2", 0],
                "x": x,
                "y": y,
                "resize_source": False,
                "mask": ["3", 0],
            },
        },
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": prefix}},
    }


def flux_kontext_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    image = uploads.get("image")
    if not image:
        raise ValueError("請先上傳參考圖片")
    instruction = (settings.get("instruction") or settings.get("positive") or "").strip()
    if not instruction:
        raise ValueError("請輸入修改指令")
    steps = clamp_int(settings.get("steps"), 20, 1, 60)
    guidance = clamp_float(settings.get("guidance"), 2.5, 0.0, 10.0)
    denoise = clamp_float(settings.get("denoise"), 1.0, 0.0, 1.0)
    sampler = settings.get("sampler") or "euler"
    scheduler = settings.get("scheduler") or "simple"
    seed = normalize_seed(settings.get("seed"))
    prefix = output_prefix(settings, "img2img")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-kontext_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image}},
        "5": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": instruction, "clip": ["2", 0]}},
        "8": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["6", 0]}},
        "9": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["8", 0], "guidance": guidance}},
        "10": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
        "11": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": sampler, "scheduler": scheduler, "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["6", 0], "denoise": denoise}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}},
    }


def wan_i2v_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    image = uploads.get("image") or uploads.get("start")
    if not image:
        raise ValueError("請先上傳起始圖片")
    width = clamp_int(settings.get("width"), 480, 64, 1280)
    height = clamp_int(settings.get("height"), 832, 64, 1280)
    length = clamp_int(settings.get("length"), 81, 9, 161)
    steps = clamp_int(settings.get("steps"), 6, 1, 40)
    cfg = clamp_float(settings.get("cfg"), 1.0, 0.0, 12.0)
    shift = clamp_float(settings.get("shift"), 8.0, 0.0, 20.0)
    lora_strength = clamp_float(settings.get("loraStrength"), 1.0, 0.0, 2.0)
    fps = clamp_int(settings.get("fps"), 16, 1, 60)
    sampler = settings.get("sampler") or "euler"
    scheduler = settings.get("scheduler") or "simple"
    seed = normalize_seed(settings.get("seed"))
    positive = (settings.get("positive") or "").strip() or "cinematic motion, natural movement"
    negative = (settings.get("negative") or WAN_DEFAULT_NEGATIVE).strip()
    prefix = output_prefix(settings, "img2video")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_i2v_480p_14B_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors", "strength_model": lora_strength}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": shift}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "6": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image}},
        "8": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["6", 0], "image": ["7", 0], "crop": "center"}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 0]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 0]}},
        "11": {"class_type": "WanImageToVideo", "inputs": {"positive": ["9", 0], "negative": ["10", 0], "vae": ["5", 0], "width": width, "height": height, "length": length, "batch_size": 1, "clip_vision_output": ["8", 0], "start_image": ["7", 0]}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler, "positive": ["11", 0], "negative": ["11", 1], "latent_image": ["11", 2], "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["5", 0]}},
        "14": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["13", 0], "frame_rate": float(fps), "loop_count": 0, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }


def wan_flf_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    start = uploads.get("start") or uploads.get("image")
    end = uploads.get("end")
    if not start or not end:
        raise ValueError("請上傳首幀與尾幀圖片")
    width = clamp_int(settings.get("width"), 480, 64, 1280)
    height = clamp_int(settings.get("height"), 832, 64, 1280)
    length = clamp_int(settings.get("length"), 81, 9, 161)
    steps = clamp_int(settings.get("steps"), 6, 1, 40)
    cfg = clamp_float(settings.get("cfg"), 1.0, 0.0, 12.0)
    shift = clamp_float(settings.get("shift"), 8.0, 0.0, 20.0)
    lora_strength = clamp_float(settings.get("loraStrength"), 1.0, 0.0, 2.0)
    fps = clamp_int(settings.get("fps"), 16, 1, 60)
    sampler = settings.get("sampler") or "euler"
    scheduler = settings.get("scheduler") or "simple"
    seed = normalize_seed(settings.get("seed"))
    positive = (settings.get("positive") or "").strip() or "smooth cinematic transition"
    negative = (settings.get("negative") or WAN_DEFAULT_NEGATIVE).strip()
    prefix = output_prefix(settings, "first-last-frame")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors", "strength_model": lora_strength}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": shift}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "6": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "7": {"class_type": "LoadImage", "inputs": {"image": start}},
        "8": {"class_type": "LoadImage", "inputs": {"image": end}},
        "9": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["6", 0], "image": ["7", 0], "crop": "center"}},
        "10": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["6", 0], "image": ["8", 0], "crop": "center"}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 0]}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 0]}},
        "13": {"class_type": "WanFirstLastFrameToVideo", "inputs": {"positive": ["11", 0], "negative": ["12", 0], "vae": ["5", 0], "width": width, "height": height, "length": length, "batch_size": 1, "clip_vision_start_image": ["9", 0], "clip_vision_end_image": ["10", 0], "start_image": ["7", 0], "end_image": ["8", 0]}},
        "14": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler, "positive": ["13", 0], "negative": ["13", 1], "latent_image": ["13", 2], "denoise": 1.0}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["5", 0]}},
        "16": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["15", 0], "filename_prefix": prefix, "fps": float(fps), "lossless": False, "quality": 85, "method": "default"}},
    }


def scail_dance_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    reference = uploads.get("reference") or uploads.get("image")
    video = uploads.get("video")
    if not reference:
        raise ValueError("請上傳角色參考圖片")
    if not video:
        raise ValueError("請上傳動作驅動影片")
    length = clamp_int(settings.get("length"), 133, 9, 257)
    steps = clamp_int(settings.get("steps"), 6, 1, 40)
    fps = clamp_int(settings.get("fps"), 16, 1, 60)
    shift = clamp_float(settings.get("shift"), 5.0, 0.0, 20.0)
    cfg = clamp_float(settings.get("cfg"), 1.0, 0.0, 12.0)
    pose_strength = clamp_float(settings.get("poseStrength"), 1.0, 0.0, 10.0)
    lora_strength = clamp_float(settings.get("loraStrength"), 1.0, 0.0, 2.0)
    # Context window must not exceed the clip length, or sampling errors out.
    context_length = min(clamp_int(settings.get("contextLength"), 81, 9, 161), length)
    seed = normalize_seed(settings.get("seed"))
    sampler = settings.get("sampler") or "euler"
    scheduler = settings.get("scheduler") or "simple"
    positive = (settings.get("positive") or "").strip() or "a person dancing, full body, natural motion"
    negative = (settings.get("negative") or WAN_DEFAULT_NEGATIVE).strip()
    prefix = output_prefix(settings, "dance")
    # VHS_LoadVideoPath takes an absolute path, sidestepping the input-dir combo
    # validation that the upload-widget loader applies to API-posted names.
    video_path = str((COMFY_INPUT / video).resolve())
    if (settings.get("mode") or "animate") == "replace":
        length = clamp_int(settings.get("length"), 133, 81, 157)
        length = ((length - 1) // 4) * 4 + 1
        steps = clamp_int(settings.get("steps"), 6, 1, 40)
        fps = clamp_int(settings.get("fps"), 16, 1, 60)
        shift = clamp_float(settings.get("shift"), 5.0, 0.0, 20.0)
        cfg = clamp_float(settings.get("cfg"), 1.0, 0.0, 12.0)
        pose_strength = clamp_float(settings.get("poseStrength"), 1.0, 0.0, 10.0)
        lora_strength = clamp_float(settings.get("loraStrength"), 0.8, 0.0, 2.0)
        seed = normalize_seed(settings.get("seed"))
        chunk2_seed = (seed + 1) % (2**64)

        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors", "strength_model": lora_strength}},
            "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": shift}},
            "4": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "5": {"class_type": "BasicScheduler", "inputs": {"model": ["3", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
            "6": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["6", 0]}},
            "8": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["6", 0]}},
            "9": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
            "10": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
            "11": {"class_type": "LoadImage", "inputs": {"image": reference}},
            "12": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["11", 0], "resize_type": "scale total pixels", "resize_type.megapixels": 1.0, "scale_method": "nearest-exact"}},
            "13": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["12", 0], "resize_type": "scale to multiple", "resize_type.multiple": 32, "scale_method": "nearest-exact"}},
            "14": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["10", 0], "image": ["13", 0], "crop": "none"}},
            "15": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": video_path, "force_rate": float(fps), "custom_width": 0, "custom_height": 0, "frame_load_cap": length, "skip_first_frames": 0, "select_every_nth": 1}},
            "16": {"class_type": "ImageResizeKJv2", "inputs": {"image": ["15", 0], "width": 1280, "height": 704, "upscale_method": "lanczos", "keep_proportion": "crop", "pad_color": "0, 0, 0", "crop_position": "center", "divisible_by": 16, "device": "cpu"}},
            "17": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sam3.1_multiplex_fp16.safetensors"}},
            "18": {"class_type": "CLIPTextEncode", "inputs": {"text": "people", "clip": ["17", 1]}},
            "19": {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["16", 0], "model": ["17", 0], "detection_threshold": 0.3, "max_objects": 2, "detect_interval": 1, "conditioning": ["18", 0]}},
            "20": {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["13", 0], "model": ["17", 0], "detection_threshold": 0.3, "max_objects": 2, "detect_interval": 1, "conditioning": ["18", 0]}},
            "21": {"class_type": "SCAIL2ColoredMask", "inputs": {"driving_track_data": ["19", 0], "ref_track_data": ["20", 0], "object_indices": "", "sort_by": "left_to_right", "replacement_mode": True}},
            "22": {"class_type": "WanSCAILToVideo", "inputs": {"positive": ["7", 0], "negative": ["8", 0], "vae": ["9", 0], "width": ["16", 1], "height": ["16", 2], "length": min(length, 81), "batch_size": 1, "pose_strength": pose_strength, "pose_start": 0.0, "pose_end": 1.0, "video_frame_offset": 0, "previous_frame_count": 5, "pose_video": ["16", 0], "pose_video_mask": ["21", 0], "reference_image": ["13", 0], "reference_image_mask": ["21", 1], "clip_vision_output": ["14", 0], "replacement_mode": True}},
            "23": {"class_type": "SamplerCustom", "inputs": {"model": ["3", 0], "add_noise": True, "noise_seed": seed, "cfg": cfg, "positive": ["22", 0], "negative": ["22", 1], "sampler": ["4", 0], "sigmas": ["5", 0], "latent_image": ["22", 2]}},
            "24": {"class_type": "VAEDecode", "inputs": {"samples": ["23", 0], "vae": ["9", 0]}},
        }
        if length <= 81:
            graph["25"] = {"class_type": "VHS_VideoCombine", "inputs": {"images": ["24", 0], "audio": ["15", 2], "frame_rate": float(fps), "loop_count": 0, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True}}
            return graph

        chunk2_length = max(9, ((length - 81 + 5 - 1) // 4) * 4 + 1)
        graph.update({
            "26": {"class_type": "WanSCAILToVideo", "inputs": {"positive": ["7", 0], "negative": ["8", 0], "vae": ["9", 0], "width": ["16", 1], "height": ["16", 2], "length": chunk2_length, "batch_size": 1, "pose_strength": pose_strength, "pose_start": 0.0, "pose_end": 1.0, "video_frame_offset": ["22", 3], "previous_frame_count": 5, "pose_video": ["16", 0], "pose_video_mask": ["21", 0], "reference_image": ["13", 0], "reference_image_mask": ["21", 1], "clip_vision_output": ["14", 0], "previous_frames": ["24", 0], "replacement_mode": False}},
            "27": {"class_type": "SamplerCustom", "inputs": {"model": ["3", 0], "add_noise": True, "noise_seed": chunk2_seed, "cfg": cfg, "positive": ["26", 0], "negative": ["26", 1], "sampler": ["4", 0], "sigmas": ["5", 0], "latent_image": ["26", 2]}},
            "28": {"class_type": "VAEDecode", "inputs": {"samples": ["27", 0], "vae": ["9", 0]}},
            "29": {"class_type": "GetImageRangeFromBatch", "inputs": {"images": ["28", 0], "start_index": 5, "num_frames": 4096}},
            "30": {"class_type": "GetImageRangeFromBatch", "inputs": {"images": ["24", 0], "start_index": -1, "num_frames": 1}},
            "31": {"class_type": "ColorTransfer", "inputs": {"image_target": ["29", 0], "image_ref": ["30", 0], "method": "reinhard_lab", "source_stats": "per_frame", "strength": 1.0}},
            "32": {"class_type": "BatchImagesNode", "inputs": {"images.image0": ["24", 0], "images.image1": ["31", 0]}},
            "33": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["32", 0], "audio": ["15", 2], "frame_rate": float(fps), "loop_count": 0, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
        })
        return graph
    # ResizeImageMaskNode is a V3 dynamic-combo node: the chosen resize_type's
    # parameter is serialized as a prefixed sibling key (e.g. resize_type.megapixels).
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.1_14B_SCAIL_2_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors", "strength_model": lora_strength}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": shift}},
        "4": {"class_type": "WanContextWindowsManual", "inputs": {"model": ["3", 0], "context_length": context_length, "context_overlap": 29, "context_schedule": "standard_static", "context_stride": 1, "closed_loop": False, "fuse_method": "pyramid", "freenoise": True, "retain_first_frame": False, "split_conds_to_windows": False}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["5", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["5", 0]}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "9": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
        "10": {"class_type": "LoadImage", "inputs": {"image": reference}},
        "11": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["10", 0], "resize_type": "scale total pixels", "resize_type.megapixels": 0.5, "scale_method": "nearest-exact"}},
        "12": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["11", 0], "resize_type": "scale to multiple", "resize_type.multiple": 32, "scale_method": "nearest-exact"}},
        "13": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["9", 0], "image": ["12", 0], "crop": "none"}},
        "14": {"class_type": "GetImageSize", "inputs": {"image": ["12", 0]}},
        "15": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": video_path, "force_rate": float(fps), "custom_width": 0, "custom_height": 0, "frame_load_cap": length, "skip_first_frames": 0, "select_every_nth": 1}},
        "16": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sam3.1_multiplex_fp16.safetensors"}},
        "17": {"class_type": "CLIPTextEncode", "inputs": {"text": "human", "clip": ["16", 1]}},
        "18": {"class_type": "CLIPTextEncode", "inputs": {"text": "human", "clip": ["16", 1]}},
        "19": {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["15", 0], "model": ["16", 0], "detection_threshold": 0.5, "max_objects": 0, "detect_interval": 1, "conditioning": ["17", 0]}},
        "20": {"class_type": "SAM3_VideoTrack", "inputs": {"images": ["12", 0], "model": ["16", 0], "detection_threshold": 0.5, "max_objects": 0, "detect_interval": 1, "conditioning": ["18", 0]}},
        "21": {"class_type": "SCAIL2ColoredMask", "inputs": {"driving_track_data": ["19", 0], "ref_track_data": ["20", 0], "object_indices": "", "sort_by": "area", "replacement_mode": False}},
        "22": {"class_type": "WanSCAILToVideo", "inputs": {"positive": ["6", 0], "negative": ["7", 0], "vae": ["8", 0], "width": ["14", 0], "height": ["14", 1], "length": length, "batch_size": 1, "pose_strength": pose_strength, "pose_start": 0.0, "pose_end": 1.0, "video_frame_offset": 0, "previous_frame_count": 5, "pose_video": ["15", 0], "pose_video_mask": ["21", 0], "reference_image": ["12", 0], "reference_image_mask": ["21", 1], "clip_vision_output": ["13", 0], "replacement_mode": False}},
        "23": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler, "positive": ["22", 0], "negative": ["22", 1], "latent_image": ["22", 2], "denoise": 1.0}},
        "24": {"class_type": "VAEDecode", "inputs": {"samples": ["23", 0], "vae": ["8", 0]}},
        "25": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["24", 0], "audio": ["15", 2], "frame_rate": float(fps), "loop_count": 0, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True}},
    }


def zimage_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    use_dopsd = bool(settings.get("useDopsd"))
    width = clamp_int(settings.get("width"), 1088, 64, 4096)
    height = clamp_int(settings.get("height"), 1920, 64, 4096)
    batch_size = clamp_int(settings.get("batchSize"), 1, 1, 16)
    steps = clamp_int(settings.get("steps"), 8, 1, 80)
    cfg = clamp_float(settings.get("cfg"), 1.0, 0.0, 20.0)
    denoise = clamp_float(settings.get("denoise"), 1.0, 0.0, 1.0)
    dopsd_strength = clamp_float(settings.get("dopsdStrength"), 1.0, -5.0, 5.0)
    seed = normalize_seed(settings.get("seed"))
    sampler = settings.get("sampler") or "res_multistep"
    scheduler = settings.get("scheduler") or "simple"
    positive = (settings.get("positive") or ZIMAGE_DEFAULT_PROMPT).strip()
    negative = (settings.get("negative") or ZIMAGE_DEFAULT_NEGATIVE).strip()
    custom_prefix = (settings.get("prefix") or "").strip()
    if custom_prefix:
        prefix = custom_prefix
    elif use_dopsd and not settings.get("_characterId"):
        prefix = "Castle/text2img_ZImageTurbo_DOPSD"
    else:
        prefix = output_prefix(settings, "text2img")

    model_ref = ["8", 0] if use_dopsd else ["1", 0]
    prompt = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "6": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch_size},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": model_ref,
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "denoise": denoise,
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if use_dopsd:
        prompt["8"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["1", 0],
                "lora_name": "dopsd_white_zimage_turbo_comfy.safetensors",
                "strength_model": dopsd_strength,
            },
        }
    return prompt


def clipmode_character_photoshoot_prompt(
    settings: dict,
    uploads: dict[str, str] | None = None,
) -> dict:
    """Build the fixed SDXL identity graph from server-bound settings.

    Realistic characters use the exact canonical profile for generation-time
    InstantID guidance and a final ReActor pass.  Anime characters keep the
    existing LoRA graph. ``api_generate`` strips all browser-authored private
    keys before resolving either contract from the owner store.
    """

    if uploads:
        raise ValueError("角色身份寫真不接受瀏覽器上傳角色圖")
    verified = verify_clipmode_character_photoshoot_files(settings)
    aspect_ratio = str(settings.get("aspect_ratio") or "768x1024").strip()
    dimensions = CLIPMODE_CHARACTER_PHOTOSHOOT_ASPECTS.get(aspect_ratio)
    if dimensions is None:
        raise ValueError(f"角色身份寫真不支援 aspect_ratio={aspect_ratio}")
    width, height = dimensions

    user_prompt = str(settings.get("prompt") or "").strip()
    if not user_prompt:
        raise ValueError("請輸入角色寫真的場景描述")
    if len(user_prompt) > 4_000:
        raise ValueError("角色寫真的場景描述最多 4000 字")
    submitted_negative = str(settings.get("negative_prompt") or "").strip()
    if len(submitted_negative) > 4_000:
        raise ValueError("角色寫真的負向提示詞最多 4000 字")
    required_negative = "child, minor, underage"
    negative = (
        f"{submitted_negative}, {required_negative}"
        if submitted_negative
        else required_negative
    )

    raw_seed = settings.get("seed", -1)
    if type(raw_seed) is not int:
        raise ValueError("seed 必須是 -1 到 2147483647 的整數")
    seed = raw_seed
    if seed == -1:
        seed = random.randint(0, 2_147_483_647)
    elif not 0 <= seed <= 2_147_483_647:
        raise ValueError("seed 必須是 -1 到 2147483647 的整數")

    prefix = output_prefix(settings, CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID)
    if verified["identity_route"] == CLIPMODE_REALISTIC_IDENTITY_ROUTE:
        effective_prompt = str(settings.get("_effectivePrompt") or user_prompt).strip()
        effective_negative = str(
            settings.get("_effectiveNegativePrompt") or submitted_negative
        ).strip()
        if not effective_prompt or len(effective_prompt) > 4_000:
            raise ValueError("角色寫真的有效場景描述無效")
        if len(effective_negative) > 4_000:
            raise ValueError("角色寫真的有效負向提示詞無效")
        negative = (
            f"{effective_negative}, {required_negative}"
            if effective_negative
            else required_negative
        )
        # Put the requested scene first.  The person-presence wording prevents
        # scene-heavy prompts from producing an empty room; it deliberately
        # avoids forcing every request into one studio/waist-up composition.
        positive = (
            f"{effective_prompt}, exactly one adult person prominently present "
            "in the foreground, the person's face clearly visible and not tiny, "
            "coherent full-scene composition, respect the requested shot size and "
            "camera distance, clothing, pose and background matching the requested "
            "scene, anatomically coherent arms and hands, photorealistic, natural "
            "skin texture, highly detailed face, realistic anatomy"
        )
        negative = (
            f"{negative}, empty room, no person, scenery only, background only, "
            "multiple people, duplicate face, extreme close-up, face filling the "
            "entire frame, cropped forehead, cropped chin, blurry face, extra arms, "
            "extra hands, duplicated limbs, disembodied hands, malformed hands, "
            "fused fingers"
        )
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": CLIPMODE_SDXL_BASE_MODEL_NAME},
            },
            "2": {
                "class_type": "LoadImage",
                "inputs": {"image": verified["profile_input_name"]},
            },
            "3": {
                "class_type": "InstantIDModelLoader",
                "inputs": {"instantid_file": CLIPMODE_INSTANTID_MODEL_NAME},
            },
            "4": {
                "class_type": "InstantIDFaceAnalysis",
                "inputs": {"provider": "CPU"},
            },
            "5": {
                "class_type": "ControlNetLoader",
                "inputs": {
                    "control_net_name": CLIPMODE_INSTANTID_CONTROLNET_MODEL_NAME,
                },
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["1", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["1", 1]},
            },
            "8": {
                "class_type": "ApplyInstantIDAdvanced",
                "inputs": {
                    "instantid": ["3", 0],
                    "insightface": ["4", 0],
                    "control_net": ["5", 0],
                    "image": ["2", 0],
                    "model": ["1", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "ip_weight": 1.0,
                    "cn_strength": 0.15,
                    "start_at": 0.0,
                    "end_at": 0.75,
                    "noise": 0.0,
                    "combine_embeds": "average",
                },
            },
            "9": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "10": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["8", 0],
                    "seed": seed,
                    "steps": 30,
                    "cfg": 5.0,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "positive": ["8", 1],
                    "negative": ["8", 2],
                    "latent_image": ["9", 0],
                    "denoise": 1.0,
                },
            },
            "11": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["10", 0], "vae": ["1", 2]},
            },
            "12": {
                "class_type": "ReActorFaceSwap",
                "inputs": {
                    "enabled": True,
                    "input_image": ["11", 0],
                    "source_image": ["2", 0],
                    "swap_model": CLIPMODE_REACTOR_MODEL_NAME,
                    "facedetection": "retinaface_resnet50",
                    "face_restore_model": CLIPMODE_REACTOR_RESTORE_MODEL_NAME,
                    # The qualified InstantID + ReActor branch scored better
                    # with a lighter restoration mix; higher values rebuilt
                    # too much of the identity after the 128px swap.
                    "face_restore_visibility": 0.2,
                    "codeformer_weight": 0.5,
                    "detect_gender_input": "no",
                    "detect_gender_source": "no",
                    "input_faces_index": "0",
                    "source_faces_index": "0",
                    "console_log_level": 1,
                },
            },
            "13": {
                "class_type": "SaveImage",
                "inputs": {"images": ["12", 0], "filename_prefix": prefix},
            },
        }

    trigger = verified["trigger_token"]
    positive = f"{trigger}, adult, {user_prompt}"
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": CLIPMODE_SDXL_BASE_MODEL_NAME},
        },
        "2": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["1", 0],
                "lora_name": verified["model_name"],
                "strength_model": 1.0,
            },
        },
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["2", 0], "seed": seed, "steps": 25, "cfg": 6.5,
                "sampler_name": "dpmpp_2m", "scheduler": "karras",
                "positive": ["3", 0], "negative": ["4", 0],
                "latent_image": ["5", 0], "denoise": 1.0,
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }


def ip_blend_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    refs = [uploads[k] for k in ("image1", "image2", "image3") if uploads.get(k)]
    if len(refs) < 2:
        raise ValueError("請至少上傳兩張參考圖片")
    width = clamp_int(settings.get("width"), 1024, 512, 1536)
    height = clamp_int(settings.get("height"), 1024, 512, 1536)
    batch_size = clamp_int(settings.get("batchSize"), 1, 1, 4)
    steps = clamp_int(settings.get("steps"), 28, 1, 80)
    cfg = clamp_float(settings.get("cfg"), 6.5, 0.0, 20.0)
    weight = clamp_float(settings.get("weight"), 0.8, 0.0, 2.0)
    seed = normalize_seed(settings.get("seed"))
    sampler = settings.get("sampler") or "dpmpp_2m"
    scheduler = settings.get("scheduler") or "karras"
    positive = (settings.get("positive") or "a high quality photo").strip()
    negative = (settings.get("negative") or "lowres, bad anatomy, watermark, text").strip()
    prefix = output_prefix(settings, "image-blend")

    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "ip-adapter-plus_sdxl_vit-h.safetensors"}},
        "3": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_size}},
    }
    # Chain one IPAdapterAdvanced per reference image so sources of differing
    # sizes blend without an explicit batch/resize step.
    model_ref = ["1", 0]
    node_id = 10
    for index, ref in enumerate(refs):
        load_id = str(node_id)
        adapter_id = str(node_id + 1)
        prompt[load_id] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        prompt[adapter_id] = {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": model_ref,
                "ipadapter": ["2", 0],
                "image": [load_id, 0],
                "clip_vision": ["3", 0],
                "weight": weight,
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 1.0,
                "embeds_scaling": "V only",
            },
        }
        model_ref = [adapter_id, 0]
        node_id += 2

    prompt["7"] = {"class_type": "KSampler", "inputs": {"model": model_ref, "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler, "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "denoise": 1.0}}
    prompt["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}}
    prompt["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}}
    return prompt


def instantid_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    face = uploads.get("face") or uploads.get("image")
    if not face:
        raise ValueError("請上傳角色臉部參考圖片")
    width = clamp_int(settings.get("width"), 896, 512, 1536)
    height = clamp_int(settings.get("height"), 1152, 512, 1536)
    batch_size = clamp_int(settings.get("batchSize"), 2, 1, 6)
    steps = clamp_int(settings.get("steps"), 30, 1, 80)
    cfg = clamp_float(settings.get("cfg"), 5.0, 0.0, 20.0)
    weight = clamp_float(settings.get("weight"), 0.8, 0.0, 2.0)
    seed = normalize_seed(settings.get("seed"))
    sampler = settings.get("sampler") or "dpmpp_2m"
    scheduler = settings.get("scheduler") or "karras"
    positive = (settings.get("positive") or "portrait, studio lighting").strip()
    negative = (settings.get("negative") or "lowres, bad anatomy, deformed, watermark, text").strip()
    prefix = output_prefix(settings, "character-angles")
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "InstantIDModelLoader", "inputs": {"instantid_file": "ip-adapter.bin"}},
        "3": {"class_type": "InstantIDFaceAnalysis", "inputs": {"provider": "CUDA"}},
        "4": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "instantid/diffusion_pytorch_model.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": face}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "8": {"class_type": "ApplyInstantID", "inputs": {"instantid": ["2", 0], "insightface": ["3", 0], "control_net": ["4", 0], "image": ["5", 0], "model": ["1", 0], "positive": ["6", 0], "negative": ["7", 0], "weight": weight, "start_at": 0.0, "end_at": 1.0}},
        "9": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_size}},
        "10": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler, "positive": ["8", 1], "negative": ["8", 2], "latent_image": ["9", 0], "denoise": 1.0}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["1", 2]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix}},
    }


REACTOR_RESTORE_MODELS = ("none", "codeformer-v0.1.0.pth", "GFPGANv1.4.pth")
REACTOR_DETECTION = ("retinaface_resnet50", "retinaface_mobile0.25", "YOLOv5l", "YOLOv5n")


def face_swap_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    video = uploads.get("video")
    face = uploads.get("face")
    if not video:
        raise ValueError("請上傳要換臉的影片")
    if not face:
        raise ValueError("請上傳來源臉部圖片")
    fps = clamp_int(settings.get("fps"), 16, 1, 60)
    max_frames = clamp_int(settings.get("maxFrames"), 150, 1, 600)
    restore = settings.get("restore")
    if restore not in REACTOR_RESTORE_MODELS:
        restore = "GFPGANv1.4.pth"
    detection = settings.get("detection")
    if detection not in REACTOR_DETECTION:
        detection = "retinaface_resnet50"
    prefix = output_prefix(settings, "face-swap")
    # VHS_LoadVideoPath takes an absolute path, sidestepping the input-dir combo
    # validation that the upload-widget LoadVideo applies to API-posted names.
    video_path = str((COMFY_INPUT / video).resolve())
    return {
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": video_path, "force_rate": float(fps), "custom_width": 0, "custom_height": 0, "frame_load_cap": max_frames, "skip_first_frames": 0, "select_every_nth": 1}},
        "2": {"class_type": "LoadImage", "inputs": {"image": face}},
        "3": {"class_type": "ReActorFaceSwap", "inputs": {"enabled": True, "input_image": ["1", 0], "source_image": ["2", 0], "swap_model": "inswapper_128.onnx", "facedetection": detection, "face_restore_model": restore, "face_restore_visibility": 1.0, "codeformer_weight": 0.5, "detect_gender_input": "no", "detect_gender_source": "no", "input_faces_index": "0", "source_faces_index": "0", "console_log_level": 1}},
        "4": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["3", 0], "filename_prefix": prefix, "fps": float(fps), "lossless": False, "quality": 85, "method": "default"}},
    }


F5_VOICE_MODELS = ("F5v1", "F5", "F5-JP", "F5-ES", "F5-FR", "F5-DE", "F5-IT", "F5-HI", "F5-TH", "E2")


def voice_clone_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    ref = uploads.get("voice")
    if not ref:
        raise ValueError("請從聲音庫挑選或上傳一段參考音訊")
    sample_text = prepare_voice_reference_text(settings.get("sampleText") or "")
    chunks = prepare_voice_script_chunks(settings.get("speech") or "", "要生成的語音文字", VOICE_CLONE_MAX_CHARS)
    custom_prefix = (settings.get("prefix") or "").strip()
    prefix = custom_prefix or (
        "Castle/voice-clone_BreezyVoice" if settings.get("engine") == "taiwan" else "Castle/voice-clone_F5TTS"
    )
    if settings.get("engine") == "taiwan":
        # 國語台灣口音：BreezyVoice (CosyVoice base + Taiwan g2p) zero-shot clone of
        # the supplied reference. Runs in the warm voice-lab daemon, not ComfyUI.
        prompt = {"1": {"class_type": "LoadAudio", "inputs": {"audio": ref}}}
        audio_refs = []
        for index, chunk in enumerate(chunks, start=2):
            node_id = str(index)
            prompt[node_id] = {"class_type": "CastleBreezyVoiceClone", "inputs": {
                "ref_audio": ["1", 0],
                "ref_text": sample_text,
                "text": chunk,
            }}
            audio_refs.append([node_id, 0])
        return add_audio_concat_and_save(prompt, audio_refs, prefix, len(chunks) + 2, VOICE_CHUNK_PAUSE_SECONDS)
    model = settings.get("model")
    if model not in F5_VOICE_MODELS:
        model = "F5v1"
    seed = clamp_int(settings.get("seed"), 1, -1, 2_147_483_647)
    prompt = {"1": {"class_type": "LoadAudio", "inputs": {"audio": ref}}}
    audio_refs = []
    for index, chunk in enumerate(chunks, start=2):
        node_id = str(index)
        prompt[node_id] = {"class_type": "F5TTSAudioInputs", "inputs": {
            "sample_audio": ["1", 0],
            "sample_text": sample_text,
            "speech": chunk,
            "seed": seed,
            "model": model,
            "vocoder": "auto",
            "speed": 1.0,
            "model_type": "F5TTS_v1_Base",
        }}
        audio_refs.append([node_id, 0])
    return add_audio_concat_and_save(prompt, audio_refs, prefix, len(chunks) + 2, VOICE_CHUNK_PAUSE_SECONDS)


def lipsync_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    uploads = uploads or {}
    video = uploads.get("video")
    if not video:
        raise ValueError("請上傳要對嘴的人物影片")
    audio_ref = uploads.get("voice")
    if not audio_ref:
        raise ValueError("請從聲音庫挑選或上傳要對嘴的語音")
    mode = settings.get("mode") or "sequential"
    if mode not in ("sequential", "repetitive"):
        raise ValueError("不支援的對嘴影格模式")
    max_frames = clamp_int(settings.get("maxFrames"), 300, 1, 1200)
    face_batch = clamp_int(settings.get("faceBatch"), 8, 1, 100)
    prefix = output_prefix(settings, "lipsync")
    # VHS_LoadVideoPath takes an absolute path, sidestepping the input-dir combo
    # validation that the upload-widget loader applies to API-posted names.
    video_path = str((COMFY_INPUT / video).resolve())
    # Wav2Lip aligns mel frames assuming 30fps output, so combine at 30fps to
    # keep the regenerated mouth in sync with the driving audio.
    return {
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": video_path, "force_rate": 0.0, "custom_width": 0, "custom_height": 0, "frame_load_cap": max_frames, "skip_first_frames": 0, "select_every_nth": 1}},
        "2": {"class_type": "LoadAudio", "inputs": {"audio": audio_ref}},
        "3": {"class_type": "Wav2Lip", "inputs": {"images": ["1", 0], "mode": mode, "face_detect_batch": face_batch, "audio": ["2", 0]}},
        "4": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["3", 0], "frame_rate": 30.0, "loop_count": 0, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False, "save_output": True, "audio": ["3", 1]}},
    }


def video_loop_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    """Loop / boomerang a short clip into a longer one — no model, pure VHS.

    loop_count repeats the clip via ffmpeg's loop filter (cheap); pingpong plays
    it forward-then-reverse so the first and last frames match and the result
    tiles seamlessly ("一直接起來"). We pull the loaded fps from VHS_VideoInfo so
    the output keeps the source's real-time speed. Audio is dropped: these are
    short generated clips that are usually silent, and a reversed boomerang track
    would desync."""
    uploads = uploads or {}
    video = uploads.get("video")
    if not video:
        raise ValueError("請上傳或從網址下載要循環的影片")
    repeat = clamp_int(settings.get("repeat"), 4, 1, 60)
    pingpong = bool(settings.get("pingpong"))
    max_frames = clamp_int(settings.get("maxFrames"), 600, 1, 1800)
    prefix = output_prefix(settings, "video-loop")
    video_path = str((COMFY_INPUT / video).resolve())
    return {
        "1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": video_path, "force_rate": 0.0, "custom_width": 0, "custom_height": 0, "frame_load_cap": max_frames, "skip_first_frames": 0, "select_every_nth": 1}},
        "2": {"class_type": "VHS_VideoInfo", "inputs": {"video_info": ["1", 3]}},
        "3": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["1", 0], "frame_rate": ["2", 5], "loop_count": repeat, "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": pingpong, "save_output": True}},
    }


def voice_speak_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    persona = (settings.get("persona") or "").strip()
    if not persona:
        raise ValueError("請從電腦聲音庫選擇一個聲音")
    chunks = prepare_voice_script_chunks(settings.get("text") or "", "要說的文字", VOICE_SPEAK_MAX_CHARS)
    prefix = voice_speak_output_prefix(settings, persona)
    prompt = {}
    audio_refs = []
    for index, chunk in enumerate(chunks, start=1):
        node_id = str(index)
        prompt[node_id] = {"class_type": "CastleBreezyVoiceTTS", "inputs": {"persona": persona, "text": chunk}}
        audio_refs.append([node_id, 0])
    return add_audio_concat_and_save(prompt, audio_refs, prefix, len(chunks) + 1, VOICE_CHUNK_PAUSE_SECONDS)


def voxcpm_direct_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    raise ValueError("VoxCPM runs as a local GPU-queued worker, not a ComfyUI graph.")


# --- 貼文生成器 (post-generator) ----------------------------------------------
# Generic writing styles for the selectable language-model backends.
POST_NO_NAMEDROP = (
    "用個性去寫，不要在輸出中提到任何人設標籤：禁止出現「MBTI / INFJ / ENTP / 星座 / "
    "archetype / 人設 / 個性」等字眼，也不要寫「我作為 X 所以…」這類自我標籤化句子。"
    "直接表達想法即可，讀者自己會感受到。"
)

POST_SISTERS = {'claude': {'name': 'Claude', 'backend': 'claude_cli', 'voice': 'Write clear, considerate prose with a concrete example and a practical conclusion.'}, 'chatgpt': {'name': 'ChatGPT', 'backend': 'openai', 'voice': 'Write concise, structured prose using short sentences and specific details.'}, 'gemini': {'name': 'Gemini', 'backend': 'gemini', 'voice': 'Explain the topic with accessible examples and relevant context.'}, 'grok': {'name': 'Grok style', 'backend': 'claude_cli', 'voice': 'Use a playful, direct tone and an unexpected perspective; avoid personal insults.'}}

# Consumer role posts must use the provider that actually owns that role.
# Grok currently has only a Claude-written style simulation, so it remains a
# clearly labelled generic Studio option and is not a Grok role capability.
EXACT_CHARACTER_POST_SISTERS = frozenset({"chatgpt", "claude", "gemini"})
CHARACTER_POST_PLATFORMS = ("threads", "instagram", "x", "facebook", "xiaohongshu", "linkedin")
CHARACTER_POST_TONES = ("warm", "professional", "humorous", "heartfelt", "promo")
CHARACTER_POST_LENGTHS = ("short", "medium", "long")


def character_post_provider_id(character: dict) -> str:
    if not isinstance(character, dict) or str(character.get("kind") or "").strip().lower() != "ai_sister":
        return ""
    source = character.get("source") if isinstance(character.get("source"), dict) else {}
    voice = character.get("voice") if isinstance(character.get("voice"), dict) else {}
    candidates = [
        source.get("providerId"),
        voice.get("persona"),
        str(character.get("id") or "").removeprefix("ai-sister-"),
    ]
    return next(
        (value for value in (str(item or "").strip().lower() for item in candidates) if value in EXACT_CHARACTER_POST_SISTERS),
        "",
    )


def exact_character_post_runtime(sister_id: str) -> tuple[bool, str]:
    sister_id = str(sister_id or "").strip().lower()
    if sister_id == "chatgpt":
        ready = bool(_lab_secret("OPENAI_API_KEY"))
        return ready, "" if ready else "ChatGPT 角色貼文尚未設定 OpenAI API"
    if sister_id == "gemini":
        ready = bool(_lab_secret("GEMINI_API_KEY"))
        return ready, "" if ready else "Gemini 角色貼文尚未設定 Gemini API"
    if sister_id == "claude":
        ready = Path(CLAUDE_CLI).is_file() or bool(shutil.which(CLAUDE_CLI))
        return ready, "" if ready else "Claude 角色貼文找不到本機 Claude CLI"
    return False, "此角色尚未有同一供應商的貼文模組"

SIGN_LABELS = {
    "aries": "牡羊座",
    "taurus": "金牛座",
    "gemini": "雙子座",
    "cancer": "巨蟹座",
    "leo": "獅子座",
    "virgo": "處女座",
    "libra": "天秤座",
    "scorpio": "天蠍座",
    "sagittarius": "射手座",
    "capricorn": "摩羯座",
    "aquarius": "水瓶座",
    "pisces": "雙魚座",
}

AI_SISTER_PROFILE_SEEDS = {}

BESTIE_PROFILE_SEEDS = {}

CHARACTER_ACTION_CATALOG = [
    {"id": "dance", "label": "跳舞", "group": "video", "intent": "用角色參考圖套到一段動作參考影片。", "needs": ["角色參考圖", "動作影片"]},
    {"id": "photoshoot", "label": "場景寫真", "group": "image", "intent": "使用該角色已驗證的寫真模組。", "needs": ["角色適用的造型與場景"]},
    {"id": "group-photo", "label": "合照 / 圖片混合", "group": "image", "intent": "使用該角色已驗證的合照模組。", "needs": ["角色圖", "其他參考圖"]},
    {"id": "multi-angle-selfie", "label": "臉部參考肖像", "group": "persona", "intent": "依真人臉部參考生成同角度設定的候選肖像。", "needs": ["清楚的真人臉部參考"]},
    {"id": "image-video", "label": "生成影片", "group": "video", "intent": "使用該角色已驗證的圖生影片模組。", "needs": ["角色適用的起始圖片與動態設定"]},
    {"id": "h3-image-video", "label": "H3 首／尾格有聲影片", "group": "video", "intent": "以角色大頭貼為首格，並可選尾格，依動漫／真人各自的 H3 模組與選項生成。", "needs": ["角色大頭貼", "本機 H3 FL2VA 模型與節點"]},
    {"id": "h3-reference-video", "label": "H3 角色參考影片", "group": "video", "intent": "以角色大頭貼作為 Ref2VA 參考，依角色類型套用正確規則。", "needs": ["角色大頭貼", "本機 H3 Ref2VA 模型與節點"]},
    {"id": "voice-speak", "label": "打字即說", "group": "voice", "intent": "使用角色綁定聲音讀出文字。", "needs": ["已驗證的角色聲音 persona"]},
    {"id": "lipsync", "label": "對嘴", "group": "video", "intent": "把角色影片對到角色聲音或聲音庫音訊。", "needs": ["人物影片", "角色聲音"]},
    {"id": "post", "label": "角色貼文", "group": "text", "intent": "用角色 personality/profile 產生社群文案。", "needs": ["角色 personality", "平台", "主題"]},
]
CHARACTER_ACTION_BY_ID = {item["id"]: item for item in CHARACTER_ACTION_CATALOG}

# Explicit result-detail entry points. A feature merely being installed never creates
# a continuation button: it must be named here, survive current readiness checks, and
# (for role actions) match trusted immutable output provenance plus a live capability.
RESULT_CONTINUATION_RULES = [
    {
        "id": "generic.video-loop",
        "label": "影片循環",
        "scope": "generic",
        "sourceKinds": ["video"],
        "featureId": "video-loop",
        "slot": "video",
        "warnings": ["一般工具輸出，不保留角色歸屬", "不保留原音訊"],
    },
    {
        "id": "character.image-video",
        "label": "生成影片",
        "scope": "character",
        "sourceKinds": ["image"],
        "visualClasses": ["anime", "realistic"],
        "sourceActionIds": ["photoshoot", "group-photo", "multi-angle-selfie"],
        "sourceVariantIds": ["photoshoot.anime.scene-card.v1"],
        "actionId": "image-video",
        "featureId": "img2video",
        "slot": "image",
    },
    {
        "id": "character.group-photo",
        "label": "加入合成",
        "scope": "character",
        "sourceKinds": ["image"],
        "visualClasses": ["anime", "realistic"],
        "sourceActionIds": ["photoshoot", "group-photo", "multi-angle-selfie"],
        "sourceVariantIds": ["photoshoot.anime.scene-card.v1"],
        "actionId": "group-photo",
        "featureId": "image-blend",
        "slot": "image2",
    },
    {
        "id": "character.dance",
        "label": "製作舞蹈",
        "scope": "character",
        "sourceKinds": ["image"],
        "visualClasses": ["anime", "realistic"],
        "sourceActionIds": ["photoshoot", "group-photo"],
        "sourceVariantIds": ["photoshoot.anime.scene-card.v1"],
        "actionId": "dance",
        "featureId": "dance",
        "slot": "reference",
    },
    {
        "id": "character.lipsync-video",
        "label": "加入對嘴",
        "scope": "character",
        "sourceKinds": ["video"],
        "visualClasses": ["realistic"],
        "sourceActionIds": ["image-video"],
        "sourceVariantIds": [],
        "actionId": "lipsync",
        "featureId": "lipsync",
        "slot": "video",
    },
    {
        "id": "character.lipsync-audio",
        "label": "加入對嘴",
        "scope": "character",
        "sourceKinds": ["audio"],
        "visualClasses": ["realistic"],
        "sourceActionIds": ["voice-speak"],
        "sourceVariantIds": ["voice-speak.persona.v1"],
        "actionId": "lipsync",
        "featureId": "lipsync",
        "slot": "voice",
        "extraDraft": {"voiceSource": "upload"},
    },
]


def _character_default_actions(kind: str = "") -> list[str]:
    if kind == "human":
        return ["photoshoot", "group-photo", "multi-angle-selfie", "image-video", "h3-image-video", "h3-reference-video", "dance", "voice-speak", "lipsync", "post"]
    return ["photoshoot", "group-photo", "multi-angle-selfie", "image-video", "h3-image-video", "h3-reference-video", "dance", "voice-speak", "post"]


def _default_character_type(kind: str, raw: dict | None = None) -> str:
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("ownerEmail") or raw.get("visibility") == "private" or raw.get("group") == "experimental":
        return "experimental"
    if kind == "human":
        return "realistic"
    if kind in ("ai_sister", "bestie"):
        return "anime"
    # Preserve the legacy lifecycle/visibility default while the independent
    # visualClass below fails closed to ``unknown``.  Changing this field while
    # merely loading an ownerless legacy custom row would otherwise make it a
    # private experimental record and hide it from existing viewers.
    return "realistic"


def _clean_character_type(value: str, kind: str, raw: dict | None = None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in CHARACTER_TYPES:
        return candidate
    return _default_character_type(kind, raw)


def _clean_character_visual_class(raw: dict, kind: str) -> str:
    """Return the independent, trusted visual routing class.

    Once ``visualClass`` exists it is authoritative, including ``unknown``;
    malformed explicit values fail closed.  Records that predate the field may
    migrate only from the three exact legacy visual labels or from the two
    established character kinds.  Model-family/lifecycle values such as flux,
    experimental and custom never imply realistic output.
    """
    if "visualClass" in raw:
        return normalized_visual_class(raw.get("visualClass"))
    legacy_type = str(raw.get("type") or "").strip().lower()
    if legacy_type in {"anime", "realistic", "illustration"}:
        return legacy_type
    if kind == "human":
        return "realistic"
    if kind in {"ai_sister", "bestie"}:
        return "anime"
    return "unknown"


def _json_copy(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return None


def _asset_entry(path: Path, label: str, status: str = "ready") -> dict:
    return {"label": label, "status": status, "sourcePath": str(path)}


def _sign_profile(seed: dict) -> dict:
    return {
        "birthAt": seed.get("birthAt"),
        "birthTz": seed.get("birthTz", ""),
        "sunSign": seed.get("sunSign", ""),
        "sunSignLabel": SIGN_LABELS.get(seed.get("sunSign", ""), ""),
        "moonSign": seed.get("moonSign", ""),
        "moonSignLabel": SIGN_LABELS.get(seed.get("moonSign", ""), ""),
        "risingSign": seed.get("risingSign", ""),
        "risingSignLabel": SIGN_LABELS.get(seed.get("risingSign", ""), ""),
        "mbti": seed.get("mbti", ""),
        "archetype": seed.get("archetype", ""),
        "archetypeNote": seed.get("archetypeNote", ""),
    }


def _seed_character_items() -> list[dict]:
    """Public installations start with an empty user-managed character library."""
    return []


def _character_known_actions(actions) -> list[str]:
    if actions is None:
        return _character_default_actions()
    result = []
    for action in actions if isinstance(actions, (list, tuple, set)) else []:
        action_id = str(action or "").strip()
        if action_id in CHARACTER_ACTION_BY_ID and action_id not in result:
            result.append(action_id)
    return result


DOPSD_LORA_NAME = "dopsd_white_zimage_turbo_comfy.safetensors"
ANIME_SCENE_CARD_RELEASE_READY = True
ANIME_SCENE_CARD_RELEASE_REASON = "動漫場景寫真正在做最後合成驗證"
ANIME_I2V_MOTION_PROMPTS = {
    "idle": "subtle natural breathing, blink once, hair and clothing gently sway, static camera",
    "smile": "blink once and make a gentle smile, subtle natural breathing, hair and clothing gently sway, static camera",
}
ANIME_I2V_STYLE_PROMPT = "preserve the exact original chibi anime character design and selected outfit, clean 2D anime illustration, full character remains in frame"
ANIME_I2V_NEGATIVE = "photorealistic, live action, realistic skin, 3d render, identity change, outfit change, cropped body"
ANIME_DANCE_STYLE_PROMPT = (
    "adult full-body chibi anime character dancing, preserve the exact original 2D character design "
    "and selected outfit, fully clothed for the entire video, clothing stays securely in place, "
    "clean anime illustration, consistent identity and proportions"
)
ANIME_DANCE_NEGATIVE = (
    "nsfw, nude, nudity, topless, exposed chest, exposed genitals, clothing removal, wardrobe "
    "malfunction, lingerie, sexualized, suggestive, photorealistic, live action, realistic skin, "
    "identity change, outfit change, child, loli"
)
ANIME_DANCE_RELEASE_READY = True
ANIME_DANCE_RELEASE_REASON = "動漫跳舞正在重新驗證角色服裝與安全 profile"
REALISTIC_LIPSYNC_RELEASE_READY = False
REALISTIC_LIPSYNC_RELEASE_REASON = (
    "角色專屬對嘴正在補齊可驗證的角色影片與聲音來源；"
    "需要處理一般真人影片時，請改用 Studio 的「真人對嘴工具」"
)


def _character_capability(
    character: dict,
    action_id: str,
    variant_id: str,
    feature_id: str,
    allowed_options: dict[str, list],
) -> dict:
    stored_actions = {
        str(item or "").strip()
        for item in (character.get("actions") if isinstance(character.get("actions"), list) else [])
    }
    return {
        "actionId": action_id,
        "variantId": variant_id,
        "featureId": feature_id,
        "enabled": action_id in stored_actions,
        "allowedOptions": {
            str(key): list(values) if isinstance(values, (list, tuple)) else []
            for key, values in allowed_options.items()
        },
    }


def _append_ready_minimax_h3_capabilities(character: dict, result: list[dict]) -> None:
    visual_class = normalized_visual_class(character.get("visualClass"))
    if visual_class not in {"anime", "realistic"} or character_asset_path(character, "avatar") is None:
        return
    stored_actions = {
        str(value or "").strip()
        for value in (character.get("actions") if isinstance(character.get("actions"), list) else [])
    }
    for action_id in ("h3-image-video", "h3-reference-video"):
        product_action = "image-video" if action_id == "h3-image-video" else "reference-video"
        variant = minimax_h3_character_variant(visual_class, product_action)
        if variant is None:
            continue
        contract_id = minimax_h3_contract_for(variant.feature_id, visual_class)
        capability = variant.public_contract()
        capability["actionId"] = action_id
        # Existing official characters predate the two explicit H3 action ids.
        # Their already-approved image-video action grants the equivalent H3
        # entry. Live feature inventory controls whether the visible tile is
        # enabled; a slow integrity audit must never erase the entry itself.
        capability["enabled"] = action_id in stored_actions or "image-video" in stored_actions
        capability["hideAdvanced"] = True
        capability["releaseReady"] = True
        capability["releaseReason"] = ""
        capability["readinessContractId"] = contract_id
        capability["providerProfileKey"] = f"local_comfy:{variant.variant_id}"
        capability["providerAssetId"] = (
            f"castle-character:{str(character.get('id') or '').strip().lower()}:avatar"
        )
        capability["providerOptionAliases"] = {"motionProfile": "motion_profile"}
        result.append(capability)


def breezy_persona_runtime(force: bool = False) -> dict:
    """Return a short-lived snapshot of the local Breezy persona service."""
    if not BREEZY_URL:
        return {"breezyOnline": False, "personaIds": [], "reason": "Configure CASTLE_BREEZY_URL to enable persona voices"}
    now = time.monotonic()
    with _BREEZY_PERSONA_LOCK:
        cached = _BREEZY_PERSONA_CACHE.get("value")
        cached_at = float(_BREEZY_PERSONA_CACHE.get("ts") or 0.0)
        if not force and isinstance(cached, dict) and now - cached_at < BREEZY_PERSONA_CACHE_SECONDS:
            return _json_copy(cached) or dict(cached)
    try:
        request = urllib.request.Request(f"{BREEZY_URL}/voices", method="GET")
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read() or b"{}")
        voices = payload.get("voices") if isinstance(payload, dict) else []
        persona_ids = sorted({
            str(item.get("id") or item.get("name") or "").strip()
            for item in (voices if isinstance(voices, list) else [])
            if isinstance(item, dict) and str(item.get("id") or item.get("name") or "").strip()
        })
        value = {
            "breezyOnline": True,
            "personaIds": persona_ids,
            "reason": "" if persona_ids else "聲音服務在線，但目前沒有可用角色聲音",
        }
    except Exception:
        value = {
            "breezyOnline": False,
            "personaIds": [],
            "reason": "角色聲音服務目前離線",
        }
    with _BREEZY_PERSONA_LOCK:
        _BREEZY_PERSONA_CACHE["ts"] = now
        _BREEZY_PERSONA_CACHE["value"] = dict(value)
    return value


def apply_character_capability_runtime(character: dict, capability: dict, runtime: dict | None) -> dict:
    item = dict(capability)
    runtime_ready = item.get("releaseReady") is not False
    runtime_reason = "" if runtime_ready else str(item.get("releaseReason") or "此角色功能正在重新驗證")
    if runtime is not None and item.get("variantId") == "voice-speak.persona.v1":
        voice = character.get("voice") if isinstance(character.get("voice"), dict) else {}
        persona = str(voice.get("persona") or "").strip()
        online = bool(runtime.get("breezyOnline"))
        persona_ids = {
            str(value or "").strip()
            for value in (runtime.get("personaIds") if isinstance(runtime.get("personaIds"), list) else [])
            if str(value or "").strip()
        }
        if not online:
            runtime_ready = False
            runtime_reason = str(runtime.get("reason") or "角色聲音服務目前離線")
        elif persona not in persona_ids:
            runtime_ready = False
            runtime_reason = f"聲音服務找不到角色聲音「{persona}」"
    item["runtimeReady"] = runtime_ready
    item["runtimeReason"] = runtime_reason
    item["disabledReason"] = (
        "角色設定尚未啟用這項功能"
        if not item.get("enabled")
        else runtime_reason
    )
    return item


def require_character_capability_runtime(character: dict, capability: dict, runtime: dict | None) -> dict:
    resolved = apply_character_capability_runtime(character, capability, runtime)
    if not resolved.get("runtimeReady"):
        raise ValueError(str(resolved.get("runtimeReason") or "角色功能目前不可用"))
    return resolved


def apply_character_capability_authorization(capabilities: list[dict], allowed: bool) -> list[dict]:
    if allowed:
        return capabilities
    reason = "這個真人角色只允許角色本人操作"
    result = []
    for capability in capabilities:
        item = dict(capability)
        item["runtimeReady"] = False
        item["runtimeReason"] = reason
        item["disabledReason"] = reason
        result.append(item)
    return result


def finalize_character_capabilities(
    character: dict,
    capabilities: list[dict],
    runtime: dict | None = None,
) -> list[dict]:
    """Validate the route identity of every role capability before publishing it."""
    exact_visual_class = normalized_visual_class(character.get("visualClass"))
    if exact_visual_class == "unknown" and capabilities:
        raise RuntimeError("尚未分類角色不可發布 capability")
    seen = {"actionId": set(), "featureId": set(), "variantId": set()}
    result = []
    for raw in capabilities:
        item = apply_character_capability_runtime(character, raw, runtime)
        if "visualClass" in item and normalized_visual_class(item.get("visualClass")) != exact_visual_class:
            raise RuntimeError("角色 capability 的 visualClass 與角色不一致")
        item["visualClass"] = exact_visual_class
        for key, values in seen.items():
            value = str(item.get(key) or "").strip()
            if not value:
                raise RuntimeError(f"角色 capability 缺少 {key}")
            if value in values:
                raise RuntimeError(f"角色 capability 的 {key} 重複：{value}")
            values.add(value)
        if item["actionId"] not in CHARACTER_ACTION_BY_ID:
            raise RuntimeError(f"角色 capability 使用未知 action：{item['actionId']}")
        feature = FEATURE_BY_ID.get(item["featureId"])
        if feature is None:
            raise RuntimeError(f"角色 capability 使用未知 feature：{item['featureId']}")
        upload_ids = {
            str(upload.get("id") or "")
            for upload in feature.get("uploads", [])
            if isinstance(upload, dict)
        }
        unknown_bound = sorted(set(item.get("serverBoundUploads") or []) - upload_ids)
        if unknown_bound:
            raise RuntimeError("角色 capability 綁定未知上傳槽：" + ", ".join(unknown_bound))
        result.append(item)
    return result


def character_capabilities(character: dict, runtime: dict | None = None) -> list[dict]:
    """Return the fail-closed consumer variants supported by one character.

    Classification determines which variants exist.  The stored ``actions``
    list can only disable one of those variants; it cannot create a new one.
    Asset-bound variants (currently Breezy persona speech) are omitted until
    the required binding exists.
    """
    if not isinstance(character, dict):
        return []
    visual_class = normalized_visual_class(character.get("visualClass"))
    kind = str(character.get("kind") or "").strip().lower()
    voice = character.get("voice") if isinstance(character.get("voice"), dict) else {}
    persona = str(voice.get("persona") or "").strip()
    voice_allowed_options = {
        # This exact variant is the live, role-bound Breezy persona route.
        # Stored voice-library ids are inventory only until a separate clone
        # variant has a validated engine/file/runtime contract.
        "voiceSource": ["persona"],
        "voicePath": ["persona"],
        "persona": [persona] if persona else [],
        "voiceId": [],
        "cloneEngine": [],
    }
    result = []

    if visual_class == "anime" and kind in ("ai_sister", "bestie"):
        wardrobe_themes = approved_wardrobe_themes(str(character.get("id") or ""))
        scene_themes = approved_anime_scene_themes()
        if wardrobe_themes and scene_themes:
            photoshoot = _character_capability(
                character,
                "photoshoot",
                "photoshoot.anime.scene-card.v1",
                "anime-scene-card",
                {
                    "wardrobeTheme": wardrobe_themes,
                    "sceneTheme": scene_themes,
                    "posePreset": ["original"],
                    "quality": ["standard"],
                },
            )
            photoshoot["serverBoundUploads"] = ["image", "scene"]
            photoshoot["hideAdvanced"] = True
            photoshoot["releaseReady"] = ANIME_SCENE_CARD_RELEASE_READY
            photoshoot["releaseReason"] = (
                "" if ANIME_SCENE_CARD_RELEASE_READY else ANIME_SCENE_CARD_RELEASE_REASON
            )
            result.append(photoshoot)
        if wardrobe_themes:
            image_video = _character_capability(
                character,
                "image-video",
                "image-video.anime.wan21-i2v.v1",
                "img2video",
                {
                    "wardrobeTheme": wardrobe_themes,
                    "motionPreset": list(ANIME_I2V_MOTION_PROMPTS),
                    "motionScale": ["natural"],
                    "duration": ["3s"],
                    "quality": ["standard"],
                    "aspect": ["480x832"],
                },
            )
            image_video["serverBoundUploads"] = ["image"]
            image_video["hideAdvanced"] = True
            result.append(image_video)
        if wardrobe_themes:
            dance = _character_capability(
                character,
                "dance",
                "dance.anime.animate.v1",
                "dance",
                {
                    "wardrobeTheme": wardrobe_themes,
                    "mode": ["animate"],
                    "quality": ["standard"],
                    "length": [81],
                },
            )
            dance["serverBoundUploads"] = ["reference"]
            dance["hideAdvanced"] = True
            dance["releaseReady"] = ANIME_DANCE_RELEASE_READY
            dance["releaseReason"] = "" if ANIME_DANCE_RELEASE_READY else ANIME_DANCE_RELEASE_REASON
            result.append(dance)
        if persona:
            voice_capability = _character_capability(
                character,
                "voice-speak",
                "voice-speak.persona.v1",
                "voice-speak",
                voice_allowed_options,
            )
            voice_capability["hideAdvanced"] = True
            result.append(voice_capability)
        post_sister = character_post_provider_id(character)
        if post_sister:
            post_ready, post_reason = exact_character_post_runtime(post_sister)
            post_capability = _character_capability(
                character,
                "post",
                "post.ai-sister.provider.v1",
                "post-generator",
                {
                    "sister": [post_sister],
                    "mode": ["quick", "advanced"],
                    "platform": list(CHARACTER_POST_PLATFORMS),
                    "tone": list(CHARACTER_POST_TONES),
                    "length": list(CHARACTER_POST_LENGTHS),
                    "postLength": list(CHARACTER_POST_LENGTHS),
                },
            )
            post_capability["hideAdvanced"] = True
            post_capability["releaseReady"] = post_ready
            post_capability["releaseReason"] = post_reason
            result.append(post_capability)
        _append_ready_minimax_h3_capabilities(character, result)
        return finalize_character_capabilities(character, result, runtime)

    if visual_class == "realistic" and kind == "human":
        if persona:
            voice_capability = _character_capability(
                character,
                "voice-speak",
                "voice-speak.persona.v1",
                "voice-speak",
                voice_allowed_options,
            )
            voice_capability["hideAdvanced"] = True
            result.append(voice_capability)
        lipsync = _character_capability(
            character,
            "lipsync",
            "lipsync.realistic.wav2lip.v1",
            "lipsync",
            {"mode": ["sequential", "repetitive"], "quality": ["standard"]},
        )
        lipsync["hideAdvanced"] = True
        lipsync["releaseReady"] = REALISTIC_LIPSYNC_RELEASE_READY
        lipsync["releaseReason"] = (
            "" if REALISTIC_LIPSYNC_RELEASE_READY else REALISTIC_LIPSYNC_RELEASE_REASON
        )
        result.append(lipsync)
        _append_ready_minimax_h3_capabilities(character, result)
    return finalize_character_capabilities(character, result, runtime)


def resolve_character_capability(
    character: dict,
    action_id: str,
    feature_id: str,
    variant_id: str,
) -> dict:
    """Resolve and validate the exact character/action/feature/variant tuple."""
    action_id = str(action_id or "").strip()
    feature_id = str(feature_id or "").strip()
    variant_id = str(variant_id or "").strip()
    if not action_id:
        raise ValueError("角色生成必須提供 action")
    if not variant_id:
        raise ValueError("角色生成必須提供 variantId")

    capabilities = character_capabilities(character)
    capability = next((item for item in capabilities if item["variantId"] == variant_id), None)
    if capability is None:
        name = str(character.get("name") or character.get("id") or "此角色")
        raise ValueError(f"角色「{name}」不支援 variantId={variant_id}")
    if capability["actionId"] != action_id or capability["featureId"] != feature_id:
        raise ValueError(
            "角色 capability mismatch："
            f"{variant_id} 必須使用 action={capability['actionId']}、featureId={capability['featureId']}"
        )
    if not capability["enabled"]:
        raise ValueError(f"角色未啟用 action={action_id}")
    return capability


def apply_character_capability_settings(character: dict, capability: dict, settings: dict) -> dict:
    """Apply server-owned option values for a validated character variant."""
    resolved = dict(settings) if isinstance(settings, dict) else {}
    variant_id = str(capability.get("variantId") or "")
    if variant_id == "photoshoot.anime.scene-card.v1":
        wardrobe_theme = str(resolved.get("wardrobeTheme") or "").strip()
        if not wardrobe_theme:
            raise ValueError("請先選擇角色造型")
        scene_theme = str(resolved.get("sceneTheme") or "").strip()
        if not scene_theme:
            raise ValueError("請先選擇動漫場景")
        pose_preset = str(resolved.get("posePreset") or "original").strip()
        character_source = wardrobe_asset_path(str(character.get("id") or ""), "outfit", wardrobe_theme)
        scene_source = anime_scene_asset_path(scene_theme)
        if character_source is None or wardrobe_theme not in approved_wardrobe_themes(str(character.get("id") or "")):
            raise ValueError("所選角色造型尚未通過驗證")
        if scene_source is None:
            raise ValueError("所選動漫場景尚未通過驗證")
        character_width, character_height = png_image_dimensions(character_source)
        scene_width, scene_height = png_image_dimensions(scene_source)
        if character_width > scene_width or character_height > scene_height:
            raise ValueError("角色造型尺寸超出動漫場景")
        bottom_margin = max(32, round(scene_height * 0.035))
        resolved = {
            "wardrobeTheme": wardrobe_theme,
            "sceneTheme": scene_theme,
            "posePreset": pose_preset,
            "quality": "standard",
            "compositeX": max(0, (scene_width - character_width) // 2),
            "compositeY": max(0, scene_height - character_height - bottom_margin),
        }
    elif variant_id == "dance.anime.animate.v1":
        wardrobe_theme = str(resolved.get("wardrobeTheme") or "").strip()
        if not wardrobe_theme:
            raise ValueError("請先選擇角色造型")
        resolved["wardrobeTheme"] = wardrobe_theme
        resolved["mode"] = "animate"
        resolved["quality"] = "standard"
        resolved["length"] = 81
        resolved["fps"] = 16
        resolved["steps"] = 6
        resolved["shift"] = 5.0
        resolved["cfg"] = 1.0
        resolved["poseStrength"] = 1.0
        resolved["loraStrength"] = 1.0
        resolved["contextLength"] = 81
        resolved["sampler"] = "euler"
        resolved["scheduler"] = "simple"
        resolved["positive"] = ANIME_DANCE_STYLE_PROMPT
        resolved["negative"] = f"{WAN_DEFAULT_NEGATIVE}, {ANIME_DANCE_NEGATIVE}"
    elif variant_id == "image-video.anime.wan21-i2v.v1":
        motion_preset = str(resolved.get("motionPreset") or "idle").strip()
        wardrobe_theme = str(resolved.get("wardrobeTheme") or "").strip()
        if not wardrobe_theme:
            raise ValueError("請先選擇角色造型")
        resolved["motionPreset"] = motion_preset
        resolved["wardrobeTheme"] = wardrobe_theme
        resolved["motionScale"] = str(resolved.get("motionScale") or "natural")
        resolved["duration"] = str(resolved.get("duration") or "3s")
        resolved["quality"] = "standard"
        resolved["width"] = 480
        resolved["height"] = 832
        resolved["length"] = 49
        resolved["fps"] = 16
        resolved["steps"] = 6
        resolved["cfg"] = 1.0
        resolved["shift"] = 8.0
        resolved["loraStrength"] = 1.0
        resolved["sampler"] = "euler"
        resolved["scheduler"] = "simple"
        resolved["positive"] = f"{ANIME_I2V_MOTION_PROMPTS.get(motion_preset, '')}, {ANIME_I2V_STYLE_PROMPT}".strip(", ")
        resolved["negative"] = f"{WAN_DEFAULT_NEGATIVE}, {ANIME_I2V_NEGATIVE}"
    elif variant_id == "voice-speak.persona.v1":
        voice = character.get("voice") if isinstance(character.get("voice"), dict) else {}
        persona = str(voice.get("persona") or "").strip()
        if not persona:
            raise ValueError("此角色尚未綁定 voice.persona")
        resolved["persona"] = persona
        voice_source = str(resolved.get("voiceSource") or "persona").strip()
        if voice_source != "persona":
            raise ValueError("此角色目前只開放已驗證的角色聲音；聲音庫克隆尚未驗證")
        resolved["voiceSource"] = "persona"
        resolved.pop("voiceId", None)
        resolved.pop("cloneEngine", None)
    elif variant_id == "post.ai-sister.provider.v1":
        sister = character_post_provider_id(character)
        if not sister:
            raise ValueError("此角色尚未有同一供應商的貼文模組")
        post_ready, post_reason = exact_character_post_runtime(sister)
        if not post_ready:
            raise ValueError(post_reason or "角色貼文模組目前不可用")
        length = str(resolved.get("length") or resolved.get("postLength") or "medium").strip()
        # Keep only the human-facing role-post fields.  In particular a
        # crafted sister/extra value cannot redirect the role to another
        # provider or replace the server-owned character profile context.
        resolved = {
            "sister": sister,
            "mode": str(resolved.get("mode") or "advanced").strip(),
            "topic": str(resolved.get("topic") or "").strip(),
            "platform": str(resolved.get("platform") or "threads").strip(),
            "tone": str(resolved.get("tone") or "warm").strip(),
            "length": length,
            "postLength": length,
            "keywords": str(resolved.get("keywords") or "").strip(),
        }
    elif variant_id == "lipsync.realistic.wav2lip.v1":
        mode = str(resolved.get("mode") or "sequential").strip()
        resolved["mode"] = mode
        # The historical operator run that established the realistic-only profile
        # used these values. Keep technical tuning out of character UI and
        # server-owned until a broader acceptance matrix exists.
        resolved["maxFrames"] = 300
        resolved["faceBatch"] = 8
        resolved.pop("quality", None)

    # Only fields listed in allowedOptions are variant-constrained.  Other
    # feature settings (prompt text, quality, numeric tuning, etc.) continue to
    # use the feature builder's normal validation.
    allowed_options = capability.get("allowedOptions") if isinstance(capability.get("allowedOptions"), dict) else {}
    for option_id, allowed_values in allowed_options.items():
        if option_id not in resolved or resolved.get(option_id) in (None, ""):
            continue
        values = list(allowed_values) if isinstance(allowed_values, (list, tuple)) else []
        submitted = resolved.get(option_id)
        if not values or not any(submitted == value or str(submitted) == str(value) for value in values):
            raise ValueError(
                f"variantId={variant_id} 不支援 {option_id}={submitted}"
            )
    # Character-owned output attribution must not be redirected by a client
    # supplied Comfy filename prefix.
    resolved.pop("prefix", None)
    resolved["_characterVariantId"] = variant_id
    return resolved


def dopsd_model_ready(available_models: set[str] | None = None) -> bool:
    models = available_model_names() if available_models is None else available_models
    return model_is_ready(DOPSD_LORA_NAME, models)


def feature_fields_for_client(
    feature: dict,
    available_models: set[str],
    available_nodes: set[str] | None = None,
) -> list[dict]:
    fields = feature.get("fields") if isinstance(feature.get("fields"), list) else []
    if feature.get("id") == "text2img" and not dopsd_model_ready(available_models):
        fields = [field for field in fields if field.get("id") != "useDopsd"]
    result = []
    clone_runtime = voice_clone_engine_runtime(available_nodes) if available_nodes is not None else {}
    for field in fields:
        item = dict(field)
        if feature.get("id") == "voice-speak" and field.get("id") == "cloneEngine" and clone_runtime:
            options = field.get("options") if isinstance(field.get("options"), list) else []
            item["options"] = [
                dict(option) if isinstance(option, dict) else option
                for option in options
                if clone_runtime.get(str(option.get("value") if isinstance(option, dict) else option), {}).get("ready")
            ]
            allowed = [str(option.get("value") or "") for option in item["options"] if isinstance(option, dict)]
            if allowed and str(item.get("default") or "") not in allowed:
                item["default"] = allowed[0]
        result.append(item)
    return result


def enforce_p0_generation_options(
    feature_id: str,
    settings: dict,
    available_models: set[str] | None = None,
) -> dict:
    """Fail closed for consumer options whose variants are not yet released."""
    resolved = dict(settings) if isinstance(settings, dict) else {}
    if feature_id == "dance":
        resolved["mode"] = "animate"
    if feature_id == "text2img" and bool(resolved.get("useDopsd")):
        if not dopsd_model_ready(available_models):
            raise ValueError(f"DOPSD 尚未就緒：缺少模型 {DOPSD_LORA_NAME}")
    return resolved


def validate_feature_option_contract(feature: dict, settings: dict) -> dict:
    """Reject values that do not belong to a feature's declared UI options.

    Builders may still clamp numeric tuning for safety, but a named select/card
    option must never silently switch to a different engine or workflow mode.
    """
    resolved = settings if isinstance(settings, dict) else {}
    for field in feature.get("fields") if isinstance(feature.get("fields"), list) else []:
        field_id = str(field.get("id") or "")
        if not field_id or field_id not in resolved or resolved.get(field_id) in (None, ""):
            continue
        if field.get("type") != "select" and field.get("display") != "cards":
            continue
        options = field.get("options") if isinstance(field.get("options"), list) else []
        allowed = {
            str(option.get("value") if isinstance(option, dict) else option)
            for option in options
        }
        submitted = str(resolved.get(field_id))
        if allowed and submitted not in allowed:
            raise ValueError(
                f"{feature.get('title') or feature.get('id')} 不支援 {field_id}={submitted}"
            )
    return resolved


def validate_voice_generation_contract(
    feature_id: str,
    settings: dict,
    user: dict | None,
    service_token: bool = False,
) -> dict:
    """Validate voice mode requirements before any uploaded file is copied."""
    resolved = settings if isinstance(settings, dict) else {}
    if feature_id == "voice-clone":
        prepare_voice_reference_text(str(resolved.get("sampleText") or ""))
        return resolved
    if feature_id == "voice-speak" and str(resolved.get("voiceSource") or "persona") == "library":
        prepare_voice_reference_text(str(resolved.get("sampleText") or ""))
        return resolved
    if feature_id != "voxcpm-tts" or str(resolved.get("voiceEngine") or "voxcpm") != "voxcpm":
        return resolved

    mode = str(resolved.get("mode") or "lora")
    profile = str(resolved.get("voiceProfile") or "").strip()
    if mode != "lora" and profile:
        raise ValueError("只有 Custom English LoRA 模式可以指定 voiceProfile")
    if mode != "ultimate":
        return resolved

    prompt_text = basic_voice_normalize(str(resolved.get("promptText") or "").strip())
    if not prompt_text:
        voice_id = str(resolved.get("voiceId") or "").strip()
        if voice_id:
            with _VOICE_LOCK:
                entry = next((item for item in load_voice_index() if item.get("id") == voice_id), None)
            if entry and user_can_access_voice_entry(user, entry, service_token):
                prompt_text = basic_voice_normalize(str(entry.get("transcript") or "").strip())
    if not prompt_text:
        raise ValueError("Ultimate Clone 需要參考音訊逐字稿")
    if len(prompt_text) > 500:
        raise ValueError("參考音訊逐字稿最多 500 字")
    return resolved


def validate_generation_file_contract(feature: dict, files) -> dict:
    """Reject missing and unknown upload slots before materializing files."""
    if files is None:
        files = {}
    if not isinstance(files, dict):
        raise ValueError("files must be an object")
    upload_specs = {
        str(item.get("id") or ""): item
        for item in (feature.get("uploads") if isinstance(feature.get("uploads"), list) else [])
        if str(item.get("id") or "")
    }
    unknown = sorted(str(slot) for slot, value in files.items() if value and str(slot) not in upload_specs)
    if unknown:
        raise ValueError("不支援的上傳欄位：" + ", ".join(unknown))
    missing = [
        str(spec.get("label") or slot)
        for slot, spec in upload_specs.items()
        if spec.get("required") and not files.get(slot)
    ]
    if missing:
        raise ValueError("缺少必要上傳：" + ", ".join(missing))
    minimum_uploads = feature.get("minimumUploads")
    if type(minimum_uploads) is int and minimum_uploads > 0:
        provided = sum(bool(files.get(slot)) for slot in upload_specs)
        if provided < minimum_uploads:
            if minimum_uploads == 1:
                raise ValueError("至少選一個參考")
            raise ValueError(f"至少選 {minimum_uploads} 個參考")
    return files


def continuation_actions_for_item(
    item: dict,
    user: dict | None,
    service_token: bool = False,
    statuses: list[dict] | None = None,
) -> list[dict]:
    """Resolve result-detail actions from an explicit, server-owned contract."""
    if not isinstance(item, dict):
        return []
    source_kind = str(item.get("kind") or "").strip().lower()
    status_rows = feature_status() if statuses is None else statuses
    status_by_id = {
        str(row.get("id") or ""): row
        for row in status_rows if isinstance(row, dict) and row.get("id")
    }
    trusted_character = (
        item.get("provenanceTrusted") is True
        and str(item.get("characterAttribution") or "") == "server"
    )
    character_summary = item.get("character") if isinstance(item.get("character"), dict) else {}
    character_id = str(character_summary.get("id") or item.get("characterId") or "").strip()
    source_variant_id = str(item.get("variantId") or "").strip()
    source_visual_class = normalized_visual_class(
        item.get("visualClass") or character_summary.get("visualClass")
    )
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    source_action = item.get("action") if isinstance(item.get("action"), dict) else {}
    source_action_id = safe_path_segment(str(provenance.get("actionId") or source_action.get("id") or ""), "")
    trusted_character = trusted_character and all((
        str(provenance.get("schema") or "") == "castle.output-provenance.v1",
        str(provenance.get("characterId") or "") == character_id,
        str(provenance.get("actionId") or "") == source_action_id,
        str(provenance.get("variantId") or "") == source_variant_id,
        normalized_visual_class(provenance.get("visualClass")) == source_visual_class,
    ))
    character_entry = None
    current_capabilities: list[dict] = []

    if trusted_character and character_id and source_variant_id and source_visual_class:
        try:
            character_entry = character_entry_for_generation(character_id, user, service_token)
        except (KeyError, ValueError, PermissionError):
            character_entry = None
        if (
            character_entry
            and normalized_visual_class(character_entry.get("visualClass")) == source_visual_class
        ):
            current_capabilities = [
                apply_character_capability_runtime(character_entry, capability, None)
                for capability in character_capabilities(character_entry, None)
            ]
        else:
            character_entry = None

    actions = []
    for rule in RESULT_CONTINUATION_RULES:
        if source_kind not in rule.get("sourceKinds", []):
            continue
        feature_id = str(rule.get("featureId") or "")
        feature = FEATURE_BY_ID.get(feature_id)
        status = status_by_id.get(feature_id) or {}
        if (
            not feature
            or feature.get("apiEnabled") is False
            or feature.get("uiHidden")
            or not status.get("runnable")
            or not status.get("ready")
        ):
            continue
        slot = str(rule.get("slot") or "")
        upload = next((spec for spec in feature.get("uploads", []) if str(spec.get("id") or "") == slot), None)
        accept = str((upload or {}).get("accept") or "")
        if not upload or (accept and not accept.startswith(f"{source_kind}/")):
            continue

        action = {
            "label": str(rule.get("label") or "接著生成"),
            "hint": "・".join(str(value) for value in rule.get("warnings", []) if str(value)),
            "scope": str(rule.get("scope") or "generic"),
            "featureId": feature_id,
            "inputSlot": slot,
            "expectedKind": source_kind,
            "warnings": [str(value) for value in rule.get("warnings", []) if str(value)],
            "target": {"view": "studio", "featureId": feature_id, "characterId": ""},
        }
        if rule.get("scope") == "character":
            if (
                not character_entry
                or source_visual_class not in rule.get("visualClasses", [])
                or source_action_id not in rule.get("sourceActionIds", [])
                or source_variant_id not in rule.get("sourceVariantIds", [])
            ):
                continue
            action_id = str(rule.get("actionId") or "")
            capability = next((
                capability for capability in current_capabilities
                if capability.get("enabled") is True
                and capability.get("runtimeReady") is not False
                and str(capability.get("actionId") or "") == action_id
                and str(capability.get("featureId") or "") == feature_id
            ), None)
            if not capability or slot in (capability.get("serverBoundUploads") or []):
                continue
            capability_variant_id = str(capability.get("variantId") or "")
            action.update({
                "id": f"{rule.get('id')}.{capability_variant_id}.{source_kind}.v1",
                "characterId": character_id,
                "actionId": action_id,
                "variantId": capability_variant_id,
                "visualClass": source_visual_class,
                "draftDefaults": _json_copy(rule.get("extraDraft") or {}) or {},
                "target": {
                    "view": "character-workspace",
                    "featureId": feature_id,
                    "characterId": character_id,
                    "actionId": action_id,
                },
            })
        else:
            action["id"] = f"{rule.get('id')}.{source_kind}.v1"
        actions.append(action)
    return actions


def result_continuation_statuses() -> list[dict]:
    """Fail closed when live ComfyUI readiness cannot be inspected."""
    try:
        return feature_status()
    except Exception:
        return []


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_OUTPUT_PROVENANCE_FIELDS = (
    "schema", "authority", "promptId", "ownerEmail", "characterId",
    "characterName", "visualClass", "featureId", "actionId", "actionLabel",
    "variantId", "contentSha256", "issuedAt",
)

_MINIMAX_H3_OUTPUT_PROVENANCE_FIELDS = (
    "schema", "authority", "scope", "jobKind", "promptId", "ownerEmail",
    "featureId", "mode", "contentSha256", "contentBytes", "modelsVerified",
    "modelManifest", "issuedAt",
)


def output_provenance_key(create: bool = False) -> bytes | None:
    """Load the dedicated HMAC key, creating it only for receipt issuance."""
    path = PROVENANCE_KEY_PATH.expanduser()
    cache_path = str(path)
    with _PROVENANCE_KEY_LOCK:
        cached = _PROVENANCE_KEY_CACHE.get("key")
        if _PROVENANCE_KEY_CACHE.get("path") == cache_path and isinstance(cached, bytes):
            return cached
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            encoded = str(payload.get("key") or "") if isinstance(payload, dict) else ""
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError("invalid provenance key length")
        except FileNotFoundError:
            if not create:
                return None
            key = secrets.token_bytes(32)
            atomic_write_json(path, {
                "version": 1,
                "key": base64.b64encode(key).decode("ascii"),
            }, 0o600)
        except (OSError, ValueError, json.JSONDecodeError):
            if create:
                raise RuntimeError("作品 provenance key 無法讀取")
            return None
        _PROVENANCE_KEY_CACHE["path"] = cache_path
        _PROVENANCE_KEY_CACHE["key"] = key
        return key


def content_sha256_cached(path: Path) -> str:
    resolved = path.resolve()
    for _attempt in range(2):
        before = resolved.stat()
        cache_key = (
            str(resolved), before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        with _PROVENANCE_HASH_LOCK:
            cached = _PROVENANCE_HASH_CACHE.get(cache_key)
        if cached:
            return cached
        digest = sha256_file(resolved)
        after = resolved.stat()
        if (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            continue
        with _PROVENANCE_HASH_LOCK:
            _PROVENANCE_HASH_CACHE[cache_key] = digest
            while len(_PROVENANCE_HASH_CACHE) > 1024:
                _PROVENANCE_HASH_CACHE.pop(next(iter(_PROVENANCE_HASH_CACHE)))
        return digest
    raise OSError("作品內容在驗證期間發生變更")


def _exact_server_model_path(root: Path, relative_name: str, label: str) -> Path:
    """Resolve one already-authorized relative model name inside a fixed root."""

    if not relative_name or Path(relative_name).is_absolute() or "\\" in relative_name:
        raise ValueError(f"{label}綁定名稱無效")
    root = root.resolve()
    try:
        target = (root / relative_name).resolve()
    except OSError as error:
        raise ValueError(f"{label}目前不可用") from error
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"{label}目前不可用")
    return target


def _clipmode_profile_extension(signature_family: str, raw: bytes | None = None) -> str:
    formats = {
        "jpeg": (".jpg", lambda value: value.startswith(b"\xff\xd8\xff")),
        "png": (".png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
        "webp": (
            ".webp",
            lambda value: len(value) >= 12
            and value[:4] == b"RIFF"
            and value[8:12] == b"WEBP",
        ),
    }
    selected = formats.get(str(signature_family or ""))
    if selected is None or (raw is not None and not selected[1](raw)):
        raise ValueError("角色身份原圖格式無效")
    return selected[0]


def _clipmode_profile_stage_root() -> Path:
    """Return the non-symlinked Comfy input root used by ``LoadImage``.

    This ComfyUI build only indexes root-level inputs. Individual canonical
    profile files therefore remain owner-only (0600) and content-addressed,
    while the shared input directory keeps its existing application mode.
    """

    try:
        COMFY_INPUT.mkdir(parents=True, exist_ok=True)
        info = COMFY_INPUT.lstat()
        resolved = COMFY_INPUT.resolve(strict=True)
        if (
            COMFY_INPUT.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o002
        ):
            raise ValueError("角色身份原圖暫存目錄不安全")
        return resolved
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("角色身份原圖暫存目錄無法使用") from error


def _verify_staged_clipmode_profile_reference(
    input_name: object,
    digest: object,
    byte_size: object,
    signature_family: object,
) -> str:
    """Revalidate one content-addressed private profile cache entry."""

    digest = str(digest or "").lower()
    signature_family = str(signature_family or "")
    if (
        re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or not 0 < byte_size <= CLIPMODE_PROFILE_MAX_BYTES
    ):
        raise ValueError("角色身份原圖綁定無效")
    extension = _clipmode_profile_extension(signature_family)
    expected_name = f"{CLIPMODE_PROFILE_INPUT_PREFIX}{digest}{extension}"
    if str(input_name or "") != expected_name:
        raise ValueError("角色身份原圖暫存名稱無效")
    stage_root = _clipmode_profile_stage_root()
    target = stage_root / expected_name
    try:
        info = target.lstat()
        resolved = target.resolve(strict=True)
        if (
            target.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != byte_size
            or resolved.parent != stage_root
            or content_sha256_cached(resolved) != digest
        ):
            raise ValueError("角色身份原圖暫存驗證失敗")
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("角色身份原圖暫存驗證失敗") from error
    return expected_name


def stage_owned_clipmode_profile_reference(
    runtime,
    owner_user_id: str,
    character: dict,
) -> dict:
    """Materialize a high-resolution rendition of the exact owner profile.

    The browser preview is deliberately not used for identity generation or
    scoring: its 512px rendition can move ArcFace similarity enough to accept
    a visibly wrong face.  The provider store instead resolves the immutable
    original role for the exact selected version, then this function creates a
    bounded, metadata-free 2048px PNG.  The returned filename is private worker
    input and must never be copied into browser metadata.
    """

    profile_id = str(character.get("profile_asset_id") or "")
    if not profile_id or character.get("profile_selection_source") != "explicit":
        raise ValueError("寫實角色尚未指定可信的角色卡原圖")
    media_store = getattr(runtime, "provider_media_store", None)
    if media_store is None or not callable(getattr(media_store, "read_owned_object", None)):
        raise ValueError("角色身份原圖儲存區目前不可用")
    try:
        exact_profile = runtime.store.resolve_owned_projection_preview(
            owner_user_id, profile_id,
        )
        version_id = str(exact_profile.get("version_id") or "")
        evidence = runtime.store.get_owned_remote_asset_version_source(
            owner_user_id, profile_id, version_id,
        )
    except (KeyError, ValueError, MediaMaterializerError) as error:
        raise ValueError("角色身份原圖目前不可用") from error
    if not isinstance(exact_profile, dict) or not isinstance(evidence, dict):
        raise ValueError("角色身份原圖證據無效")
    original_digest = str(evidence.get("original_blob_sha256") or "").lower()
    original_byte_size = evidence.get("original_blob_byte_size")
    original_signature_family = str(
        evidence.get("original_signature_family") or ""
    )
    declared_mime = str(evidence.get("mime_type") or "")
    expected_mime = {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(original_signature_family)
    if (
        exact_profile.get("projection_id") != profile_id
        or exact_profile.get("selection_source") != "explicit"
        or not version_id
        or evidence.get("projection_id") != profile_id
        or evidence.get("version_id") != version_id
        or not evidence.get("original_receipt_active")
        or re.fullmatch(r"[0-9a-f]{64}", original_digest) is None
        or isinstance(original_byte_size, bool)
        or not isinstance(original_byte_size, int)
        or not 0 < original_byte_size <= CLIPMODE_PROFILE_SOURCE_MAX_BYTES
        or declared_mime != expected_mime
    ):
        raise ValueError("角色身份原圖證據無效")
    try:
        raw = media_store.read_owned_object(
            owner_user_id,
            original_digest,
            original_byte_size,
            original_signature_family,
            max_bytes=CLIPMODE_PROFILE_SOURCE_MAX_BYTES,
        )
    except (KeyError, ValueError, MediaMaterializerError) as error:
        raise ValueError("角色身份原圖目前不可用") from error
    if (
        not isinstance(raw, bytes)
        or len(raw) != original_byte_size
        or not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), original_digest,
        )
    ):
        raise ValueError("角色身份原圖內容驗證失敗")
    try:
        rendition = render_profile_rendition(
            raw,
            declared_mime,
            limits=ProfileRenditionLimits(
                max_input_bytes=CLIPMODE_PROFILE_SOURCE_MAX_BYTES,
                max_edge_pixels=2048,
                max_output_bytes=CLIPMODE_PROFILE_MAX_BYTES,
            ),
        )
    except ProfileRenditionError as error:
        raise ValueError("角色身份原圖無法建立高解析基準") from error
    raw = rendition.content
    digest = rendition.sha256
    byte_size = rendition.byte_size
    signature_family = rendition.signature_family
    extension = _clipmode_profile_extension(signature_family, raw)
    stage_root = _clipmode_profile_stage_root()
    input_name = f"{CLIPMODE_PROFILE_INPUT_PREFIX}{digest}{extension}"
    target = stage_root / input_name
    if not target.exists():
        temporary = stage_root / (
            f".{CLIPMODE_PROFILE_INPUT_PREFIX}{digest}.tmp-"
            f"{os.getpid()}-{secrets.token_hex(8)}"
        )
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            descriptor = os.open(temporary, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                pass
        except OSError as error:
            raise ValueError("角色身份原圖無法安全暫存") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    _verify_staged_clipmode_profile_reference(
        input_name, digest, byte_size, signature_family,
    )
    return {
        "input_name": input_name,
        "sha256": digest,
        "byte_size": byte_size,
        "signature_family": signature_family,
    }


def verify_clipmode_character_photoshoot_files(settings: dict) -> dict:
    """Revalidate the route's private files immediately before graph build."""

    capability_id = str(settings.get("_characterBindingCapabilityId") or "")
    base_name = str(settings.get("_characterBaseModelName") or "")
    base_sha256 = str(settings.get("_characterBaseModelSha256") or "").lower()
    visual_class = normalized_visual_class(settings.get("_characterVisualClass"))
    identity_route = str(settings.get("_characterIdentityRoute") or "")
    if capability_id != CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID:
        raise ValueError("角色身份寫真缺少伺服器綁定")
    if (
        base_name != CLIPMODE_SDXL_BASE_MODEL_NAME
        or base_sha256 != CLIPMODE_SDXL_BASE_MODEL_SHA256
    ):
        raise ValueError("角色身份寫真的 SDXL Base 綁定不相容")

    base_path = _exact_server_model_path(
        MODELS_DIR / "checkpoints", base_name, "SDXL Base 模型",
    )
    if (
        content_sha256_cached(base_path) != base_sha256
        or not safetensors_file_is_complete(base_path)
    ):
        raise ValueError("SDXL Base 模型完整性檢查失敗")

    if visual_class == "realistic":
        if identity_route != CLIPMODE_REALISTIC_IDENTITY_ROUTE:
            raise ValueError("寫實角色身份路線綁定無效")
        input_name = _verify_staged_clipmode_profile_reference(
            settings.get("_characterProfileInputName"),
            settings.get("_characterProfileSha256"),
            settings.get("_characterProfileByteSize"),
            settings.get("_characterProfileSignatureFamily"),
        )
        return {
            "identity_route": identity_route,
            "visual_class": visual_class,
            "profile_input_name": input_name,
        }

    if visual_class != "anime" or identity_route != CLIPMODE_ANIME_IDENTITY_ROUTE:
        raise ValueError("角色身份路線綁定無效")
    model_name = str(settings.get("_characterLoraModelName") or "")
    model_sha256 = str(settings.get("_characterLoraModelSha256") or "").lower()
    trigger_token = str(settings.get("_characterTriggerToken") or "").strip()
    if re.fullmatch(
        r"clipmode/[A-Za-z0-9][A-Za-z0-9._-]{0,190}\.safetensors",
        model_name,
    ) is None or re.fullmatch(r"[0-9a-f]{64}", model_sha256) is None:
        raise ValueError("角色 LoRA 模型綁定無效")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", trigger_token) is None:
        raise ValueError("角色 LoRA 觸發詞綁定無效")
    lora_path = _exact_server_model_path(
        MODELS_DIR / "loras", model_name, "角色 LoRA 模型",
    )
    if (
        content_sha256_cached(lora_path) != model_sha256
        or not safetensors_file_is_complete(lora_path)
    ):
        raise ValueError("角色 LoRA 模型完整性檢查失敗")

    return {
        "identity_route": identity_route,
        "visual_class": visual_class,
        "model_name": model_name,
        "model_sha256": model_sha256,
        "trigger_token": trigger_token,
    }


def resolve_owned_provider_character_photoshoot(
    runtime,
    actor,
    character_id: str,
) -> tuple[dict, dict, dict]:
    """Resolve one owner-scoped provider character into private builder inputs."""

    character_id = stable_character_identifier(character_id)
    if not character_id:
        raise ValueError("角色 ID 無效")
    owner_user_id = str(getattr(actor, "user_id", "") or "")
    character = runtime.store.get_owned_character(owner_user_id, character_id)
    if str(character.get("character_id") or "") != character_id:
        raise ValueError("角色綁定識別不一致")
    visual_class = normalized_visual_class(character.get("visual_class"))
    if visual_class not in {"anime", "realistic"}:
        raise ValueError("這個角色尚未完成動漫／真人分類")
    if CLIPMODE_SDXL_BASE_MODEL_NAME not in model_options("CheckpointLoaderSimple", "ckpt_name"):
        raise ValueError("ComfyUI 尚未辨識 SDXL Base 模型")
    private_settings: dict = {
        "_characterBindingCapabilityId": CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
        "_characterBaseModelName": CLIPMODE_SDXL_BASE_MODEL_NAME,
        "_characterBaseModelSha256": CLIPMODE_SDXL_BASE_MODEL_SHA256,
        "_characterVisualClass": visual_class,
    }
    context = {
        "id": character_id,
        "name": str(character.get("display_name") or character_id)[:160],
        "kind": "provider_character",
        "type": visual_class,
        "visualClass": visual_class,
        "group": "imported",
        "origin": "ClipMode owner import",
    }

    if visual_class == "realistic":
        required_model_bindings = (
            ("ReActorFaceSwap", "swap_model", CLIPMODE_REACTOR_MODEL_NAME),
            (
                "ReActorFaceSwap",
                "face_restore_model",
                CLIPMODE_REACTOR_RESTORE_MODEL_NAME,
            ),
            (
                "InstantIDModelLoader",
                "instantid_file",
                CLIPMODE_INSTANTID_MODEL_NAME,
            ),
            (
                "ControlNetLoader",
                "control_net_name",
                CLIPMODE_INSTANTID_CONTROLNET_MODEL_NAME,
            ),
        )
        missing_identity_models = [
            expected
            for class_name, field_name, expected in required_model_bindings
            if expected not in model_options(class_name, field_name)
        ]
        if missing_identity_models:
            raise ValueError(
                "ComfyUI 尚未辨識角色身份鎖定模型："
                + ", ".join(missing_identity_models)
            )
        staged = stage_owned_clipmode_profile_reference(
            runtime, owner_user_id, character,
        )
        private_settings.update({
            "_characterIdentityRoute": CLIPMODE_REALISTIC_IDENTITY_ROUTE,
            "_characterProfileInputName": staged["input_name"],
            "_characterProfileSha256": staged["sha256"],
            "_characterProfileByteSize": staged["byte_size"],
            "_characterProfileSignatureFamily": staged["signature_family"],
        })
        context["identityBinding"] = {
            "capabilityId": CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
            "mode": "canonical-profile-instantid-reactor",
        }
        receipt = {
            "kind": "server-bound-canonical-profile",
            "capabilityId": CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
            "identityMode": "canonical-profile-instantid-reactor",
        }
    else:
        binding = runtime.store.resolve_owned_character_model_binding(
            owner_user_id,
            character_id,
            capability_id=CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
        )
        required_equal = {
            "owner_user_id": owner_user_id,
            "character_id": character_id,
            "capability_id": CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
            "base_model_name": CLIPMODE_SDXL_BASE_MODEL_NAME,
            "base_model_sha256": CLIPMODE_SDXL_BASE_MODEL_SHA256,
            "training_receipt_schema": "castle.clipmode-sdxl-training-receipt.v1",
        }
        if any(str(binding.get(key) or "") != value for key, value in required_equal.items()):
            raise ValueError("角色 LoRA 綁定證據不一致")
        if float(binding.get("model_strength") or 0.0) != 1.0 or float(binding.get("clip_strength") or 0.0) != 1.0:
            raise ValueError("角色 LoRA 綁定強度不相容")
        receipt_sha256 = str(binding.get("training_receipt_sha256") or "").lower()
        try:
            revision = int(binding.get("revision"))
        except (TypeError, ValueError) as error:
            raise ValueError("角色 LoRA 綁定版本無效") from error
        if revision < 1 or re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None:
            raise ValueError("角色 LoRA 訓練收據無效")
        model_name = str(binding.get("model_name") or "")
        model_sha256 = str(binding.get("model_sha256") or "").lower()
        trigger_token = str(binding.get("trigger_token") or "").strip()
        if re.fullmatch(
            r"clipmode/[A-Za-z0-9][A-Za-z0-9._-]{0,190}\.safetensors",
            model_name,
        ) is None or re.fullmatch(r"[0-9a-f]{64}", model_sha256) is None:
            raise ValueError("角色 LoRA 模型綁定無效")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", trigger_token) is None:
            raise ValueError("角色 LoRA 觸發詞綁定無效")
        if model_name not in model_options("LoraLoaderModelOnly", "lora_name"):
            raise ValueError("ComfyUI 尚未辨識這個角色 LoRA")
        private_settings.update({
            "_characterIdentityRoute": CLIPMODE_ANIME_IDENTITY_ROUTE,
            "_characterLoraModelName": model_name,
            "_characterLoraModelSha256": model_sha256,
            "_characterTriggerToken": trigger_token,
            "_characterModelBindingRevision": revision,
            "_characterTrainingReceiptSchema": required_equal["training_receipt_schema"],
            "_characterTrainingReceiptSha256": receipt_sha256,
        })
        context["modelBinding"] = {
            "capabilityId": CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
            "revision": revision,
            "trainingReceiptSchema": required_equal["training_receipt_schema"],
        }
        receipt = {
            "kind": "server-bound-character-lora",
            "capabilityId": CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID,
            "bindingRevision": revision,
            "modelSha256": model_sha256,
            "trainingReceiptSha256": receipt_sha256,
        }

    # Hash the exact server-owned files before any graph is built.
    verify_clipmode_character_photoshoot_files(private_settings)
    return context, private_settings, receipt


def normalize_output_provenance_receipt(receipt, require_signature: bool = True) -> dict | None:
    if not isinstance(receipt, dict):
        return None
    try:
        issued_at = int(receipt.get("issuedAt") or 0)
    except (TypeError, ValueError):
        return None
    normalized = {
        "schema": str(receipt.get("schema") or ""),
        "authority": str(receipt.get("authority") or ""),
        "promptId": str(receipt.get("promptId") or "").strip(),
        "ownerEmail": normalize_email(str(receipt.get("ownerEmail") or "")),
        "characterId": stable_character_identifier(receipt.get("characterId")),
        "characterName": str(receipt.get("characterName") or "").strip()[:160],
        "visualClass": normalized_visual_class(receipt.get("visualClass")),
        "featureId": str(receipt.get("featureId") or "").strip()[:160],
        "actionId": safe_path_segment(str(receipt.get("actionId") or ""), ""),
        "actionLabel": str(receipt.get("actionLabel") or "").strip()[:160],
        "variantId": str(receipt.get("variantId") or "").strip()[:160],
        "contentSha256": str(receipt.get("contentSha256") or "").strip().lower(),
        "issuedAt": issued_at,
    }
    if (
        normalized["schema"] != OUTPUT_PROVENANCE_SCHEMA
        or normalized["authority"] != OUTPUT_PROVENANCE_AUTHORITY
        or not prompt_id_hint_valid(normalized["promptId"])
        or not normalized["ownerEmail"]
        or not normalized["characterId"]
        or normalized["visualClass"] == "unknown"
        or not normalized["featureId"]
        or not normalized["actionId"]
        or not normalized["variantId"]
        or not re.fullmatch(r"[0-9a-f]{64}", normalized["contentSha256"])
        or normalized["issuedAt"] <= 0
    ):
        return None
    if require_signature:
        signature = str(receipt.get("signature") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            return None
        normalized["signature"] = signature
    return normalized


def normalize_minimax_h3_output_provenance_receipt(
    receipt,
    require_signature: bool = True,
) -> dict | None:
    """Normalize a generic H3 receipt without inventing role attribution."""

    if not isinstance(receipt, dict):
        return None
    expected_keys = set(_MINIMAX_H3_OUTPUT_PROVENANCE_FIELDS)
    if require_signature:
        expected_keys.add("signature")
    if set(receipt) != expected_keys:
        return None
    manifest = receipt.get("modelManifest")
    if not isinstance(manifest, dict) or set(manifest) != {"artifact_set", "revision", "sha256"}:
        return None
    if any(type(receipt.get(key)) is not int for key in ("issuedAt", "contentBytes", "modelsVerified")):
        return None
    try:
        issued_at = int(receipt.get("issuedAt") or 0)
        content_bytes = int(receipt.get("contentBytes") or 0)
        models_verified = int(receipt.get("modelsVerified") or 0)
    except (TypeError, ValueError):
        return None
    normalized_manifest = {
        "artifact_set": str(manifest.get("artifact_set") or "").strip(),
        "revision": str(manifest.get("revision") or "").strip(),
        "sha256": str(manifest.get("sha256") or "").strip().lower(),
    }
    normalized = {
        "schema": str(receipt.get("schema") or ""),
        "authority": str(receipt.get("authority") or ""),
        "scope": str(receipt.get("scope") or ""),
        "jobKind": str(receipt.get("jobKind") or ""),
        "promptId": str(receipt.get("promptId") or "").strip(),
        "ownerEmail": normalize_email(str(receipt.get("ownerEmail") or "")),
        "featureId": str(receipt.get("featureId") or "").strip()[:160],
        "mode": str(receipt.get("mode") or "").strip(),
        "contentSha256": str(receipt.get("contentSha256") or "").strip().lower(),
        "contentBytes": content_bytes,
        "modelsVerified": models_verified,
        "modelManifest": normalized_manifest,
        "issuedAt": issued_at,
    }
    expected_modes = {
        "h3-text-video": "t2v",
        "h3-image-video": "i2v",
        "h3-reference-video": "ref2va_image",
    }
    if (
        normalized["schema"] != MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA
        or normalized["authority"] != OUTPUT_PROVENANCE_AUTHORITY
        or normalized["scope"] != "generic"
        or normalized["jobKind"] != MINIMAX_H3_WEB_JOB_KIND
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            normalized["promptId"],
            re.IGNORECASE,
        ) is None
        or not normalized["ownerEmail"]
        or expected_modes.get(normalized["featureId"]) != normalized["mode"]
        or not re.fullmatch(r"[0-9a-f]{64}", normalized["contentSha256"])
        or normalized["contentBytes"] <= 0
        or normalized["modelsVerified"] != 4
        or not normalized_manifest["artifact_set"]
        or not normalized_manifest["revision"]
        or not re.fullmatch(r"[0-9a-f]{64}", normalized_manifest["sha256"])
        or normalized["issuedAt"] <= 0
    ):
        return None
    if require_signature:
        signature = str(receipt.get("signature") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            return None
        normalized["signature"] = signature
    return normalized


def output_provenance_signature(receipt: dict, key: bytes) -> str:
    fields = (
        _MINIMAX_H3_OUTPUT_PROVENANCE_FIELDS
        if receipt.get("schema") == MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA
        else _OUTPUT_PROVENANCE_FIELDS
    )
    payload = {field: receipt[field] for field in fields}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def issue_output_provenance_receipt(path: Path, prompt_id: str, indexed: dict) -> dict | None:
    projection = indexed_output_provenance(prompt_id, {str(prompt_id): indexed})
    if not projection:
        return None
    provenance = projection["provenance"]
    receipt = normalize_output_provenance_receipt({
        "schema": OUTPUT_PROVENANCE_SCHEMA,
        "authority": OUTPUT_PROVENANCE_AUTHORITY,
        "promptId": prompt_id,
        "ownerEmail": indexed.get("email"),
        "characterId": provenance.get("characterId"),
        "characterName": (projection.get("character") or {}).get("name"),
        "visualClass": provenance.get("visualClass"),
        "featureId": provenance.get("featureId"),
        "actionId": provenance.get("actionId"),
        "actionLabel": (projection.get("action") or {}).get("label"),
        "variantId": provenance.get("variantId"),
        "contentSha256": content_sha256_cached(path),
        "issuedAt": int(float(indexed.get("ts") or time.time())),
    }, require_signature=False)
    if receipt is None:
        return None
    key = output_provenance_key(create=True)
    if key is None:
        return None
    receipt["signature"] = output_provenance_signature(receipt, key)
    return receipt


def issue_minimax_h3_output_provenance_receipt(
    path: Path,
    prompt_id: str,
    indexed: dict,
    feature_id: str,
    worker_receipt: dict,
) -> dict | None:
    """Issue ownership/integrity provenance for a generic H3 Web result."""

    if (
        not isinstance(indexed, dict)
        or indexed.get("scope") != "generic"
        or indexed.get("jobKind") != MINIMAX_H3_WEB_JOB_KIND
        or str(indexed.get("featureId") or "") != feature_id
    ):
        return None
    output = worker_receipt.get("output") if isinstance(worker_receipt.get("output"), dict) else {}
    receipt = normalize_minimax_h3_output_provenance_receipt({
        "schema": MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA,
        "authority": OUTPUT_PROVENANCE_AUTHORITY,
        "scope": "generic",
        "jobKind": MINIMAX_H3_WEB_JOB_KIND,
        "promptId": prompt_id,
        "ownerEmail": indexed.get("email"),
        "featureId": feature_id,
        "mode": worker_receipt.get("mode"),
        "contentSha256": content_sha256_cached(path),
        "contentBytes": path.stat().st_size,
        "modelsVerified": worker_receipt.get("models_verified"),
        "modelManifest": worker_receipt.get("model_manifest"),
        "issuedAt": int(float(indexed.get("ts") or time.time())),
    }, require_signature=False)
    if (
        receipt is None
        or receipt["contentSha256"] != str(output.get("sha256") or "")
        or receipt["contentBytes"] != int(output.get("bytes") or 0)
    ):
        return None
    key = output_provenance_key(create=True)
    if key is None:
        return None
    receipt["signature"] = output_provenance_signature(receipt, key)
    return receipt


def verify_output_provenance_receipt(path: Path, receipt) -> dict | None:
    if isinstance(receipt, dict) and receipt.get("schema") == MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA:
        normalized = normalize_minimax_h3_output_provenance_receipt(
            receipt, require_signature=True,
        )
    else:
        normalized = normalize_output_provenance_receipt(receipt, require_signature=True)
    key = output_provenance_key(create=False)
    if normalized is None or key is None:
        return None
    signature = normalized.pop("signature")
    expected = output_provenance_signature(normalized, key)
    normalized["signature"] = signature
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        actual_hash = content_sha256_cached(path)
    except (OSError, ValueError):
        return None
    if not hmac.compare_digest(normalized["contentSha256"], actual_hash):
        return None
    if (
        normalized.get("schema") == MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA
        and normalized.get("contentBytes") != path.stat().st_size
    ):
        return None
    return normalized


def bind_character_capability_files(
    character: dict,
    capability: dict,
    files,
    settings: dict,
) -> tuple[dict, dict | None]:
    """Resolve server-owned character inputs without trusting client paths."""
    variant_id = str(capability.get("variantId") or "")
    if files is not None and not isinstance(files, dict):
        raise ValueError("files must be an object")
    client_files = files if isinstance(files, dict) else {}

    h3_bindings = {
        "image-video.anime.h3-fl2va.v1": "image",
        "image-video.realistic.h3-fl2va.v1": "image",
        "reference-video.anime.h3-ref2va-image.v1": "reference",
        "reference-video.realistic.h3-ref2va-image.v1": "reference",
    }
    h3_slot = h3_bindings.get(variant_id)
    if h3_slot:
        if client_files.get(h3_slot):
            raise ValueError("MiniMax H3 角色影片只使用伺服器綁定的大頭貼，不接受替換圖片")
        source = character_asset_path(character, "avatar")
        if source is None:
            raise ValueError("此角色沒有可用的大頭貼")
        digest = sha256_file(source)
        resolved_files = dict(client_files)
        resolved_files[h3_slot] = {
            "_characterAssetPath": str(source),
            "_characterAssetHash": digest,
            "_characterAssetTheme": "profile",
        }
        return resolved_files, {
            "slot": h3_slot,
            "kind": "character-profile-avatar",
            "sha256": digest,
        }

    if variant_id == "photoshoot.anime.scene-card.v1":
        if client_files.get("image") or client_files.get("scene"):
            raise ValueError("動漫場景寫真只使用已驗證的角色造型與場景，不接受任意圖片")
        character_id = str(character.get("id") or "")
        wardrobe_theme = str(settings.get("wardrobeTheme") or "").strip()
        scene_theme = str(settings.get("sceneTheme") or "").strip()
        if wardrobe_theme not in approved_wardrobe_themes(character_id):
            raise ValueError("所選角色造型尚未通過驗證")
        if scene_theme not in approved_anime_scene_themes():
            raise ValueError("所選動漫場景尚未通過驗證")
        character_source = wardrobe_asset_path(character_id, "outfit", wardrobe_theme)
        scene_source = anime_scene_asset_path(scene_theme)
        if character_source is None or scene_source is None:
            raise ValueError("找不到動漫場景寫真素材")
        character_hash = sha256_file(character_source)
        scene_hash = sha256_file(scene_source)
        resolved_files = dict(client_files)
        resolved_files["image"] = {
            "_characterAssetPath": str(character_source),
            "_characterAssetHash": character_hash,
            "_characterAssetTheme": wardrobe_theme,
        }
        resolved_files["scene"] = {
            "_characterAssetPath": str(scene_source),
            "_characterAssetHash": scene_hash,
            "_characterAssetTheme": f"scene-{scene_theme}",
        }
        return resolved_files, {
            "kind": "approved-anime-scene-card",
            "wardrobeTheme": wardrobe_theme,
            "sceneTheme": scene_theme,
            "assets": {
                "image": {"sha256": character_hash},
                "scene": {"sha256": scene_hash},
            },
        }

    binding = {
        "image-video.anime.wan21-i2v.v1": {
            "slot": "image",
            "reject": "動漫角色短片會使用所選角色造型，不接受任意替換圖片",
        },
        "dance.anime.animate.v1": {
            "slot": "reference",
            "reject": "動漫跳舞會使用所選角色造型，不接受任意替換角色圖片",
        },
    }.get(variant_id)
    if binding is None:
        return client_files, None
    slot = str(binding["slot"])
    if client_files.get(slot):
        raise ValueError(str(binding["reject"]))
    theme = str(settings.get("wardrobeTheme") or "").strip()
    if theme not in approved_wardrobe_themes(str(character.get("id") or "")):
        raise ValueError("所選角色造型尚未通過驗證")
    source = wardrobe_asset_path(str(character.get("id") or ""), "outfit", theme)
    if source is None:
        raise ValueError("找不到所選角色造型")
    digest = sha256_file(source)
    receipt = {
        "slot": slot,
        "kind": "approved-wardrobe",
        "theme": theme,
        "sha256": digest,
    }
    resolved_files = dict(client_files)
    resolved_files[slot] = {
        "_characterAssetPath": str(source),
        "_characterAssetHash": digest,
        "_characterAssetTheme": theme,
    }
    return resolved_files, receipt


def stage_character_asset_for_comfy(file_info: dict, character_id: str) -> str:
    source = Path(str(file_info.get("_characterAssetPath") or ""))
    expected_hash = str(file_info.get("_characterAssetHash") or "").strip()
    theme = safe_path_segment(str(file_info.get("_characterAssetTheme") or ""), "outfit")
    if not source.is_file() or not expected_hash or sha256_file(source) != expected_hash:
        raise ValueError("角色造型檔案在送出前已變更，請重新選擇")
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    cid = safe_path_segment(str(character_id or "character"), "character")
    suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    filename = f"castle_character_{cid}_{theme}_{expected_hash[:12]}{suffix}"
    target = COMFY_INPUT / filename
    input_root = COMFY_INPUT.resolve()
    if target.exists():
        if not target.resolve().is_relative_to(input_root) or not target.is_file():
            raise ValueError("角色造型暫存路徑無效")
        if sha256_file(target) == expected_hash:
            return filename
    temporary = COMFY_INPUT / f".{filename}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return filename


def strip_client_character_settings(settings: dict) -> dict:
    """Remove client-authored attribution and server-owned output routing."""
    if not isinstance(settings, dict):
        return {}
    return {
        key: value
        for key, value in settings.items()
        if not str(key).startswith("_character")
        and not str(key).startswith("_variant")
        and str(key) != "prefix"
    }


def _clean_character(raw: dict, fallback_id: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    name = str(raw.get("name") or fallback_id or "New Character").strip()[:120] or "New Character"
    cid = safe_path_segment(str(raw.get("id") or fallback_id or name).lower().replace(" ", "-"), "character")
    kind = str(raw.get("kind") or "custom").strip()[:40] or "custom"
    visual_class = _clean_character_visual_class(raw, kind)
    character_type = _clean_character_type(str(raw.get("type") or ""), kind, raw)
    owner_email = normalize_email(str(raw.get("ownerEmail") or ""))
    visibility = str(raw.get("visibility") or ("private" if owner_email or character_type == "experimental" else "official")).strip().lower()
    if visibility not in ("official", "private"):
        visibility = "private" if owner_email or character_type == "experimental" else "official"
    now = time.time()
    item = {
        "id": cid,
        "name": name,
        "kind": kind,
        "type": character_type,
        "visualClass": visual_class,
        "group": str(raw.get("group") or raw.get("lane") or ("host" if kind == "human" else ("sisterhood" if kind == "ai_sister" else ("besties" if kind == "bestie" else "custom")))).strip()[:60] or "custom",
        "tagline": str(raw.get("tagline") or "")[:240],
        "profile": str(raw.get("profile") or "")[:4000],
        "personality": str(raw.get("personality") or "")[:4000],
        "origin": str(raw.get("origin") or "")[:500],
        "color": str(raw.get("color") or "")[:32],
        "style": raw.get("style") if isinstance(raw.get("style"), dict) else {},
        "languageModel": raw.get("languageModel") if isinstance(raw.get("languageModel"), dict) else {},
        "voice": raw.get("voice") if isinstance(raw.get("voice"), dict) else {},
        "assets": raw.get("assets") if isinstance(raw.get("assets"), dict) else {},
        "profileData": raw.get("profileData") if isinstance(raw.get("profileData"), dict) else {},
        "source": raw.get("source") if isinstance(raw.get("source"), dict) else {},
        "actions": _character_known_actions(raw.get("actions")),
        "ownerEmail": owner_email,
        "visibility": visibility,
        "updated": float(raw.get("updated") or now),
        "created": float(raw.get("created") or now),
    }
    if item["type"] == "experimental":
        item["kind"] = "custom" if item["kind"] not in ("custom", "human", "ai_sister", "bestie") else item["kind"]
        item["group"] = "experimental"
        item["visibility"] = "private"
    for key in ("style", "languageModel", "voice", "assets", "profileData", "source"):
        item[key] = _json_copy(item[key]) if isinstance(item[key], dict) else {}
        if item[key] is None:
            item[key] = {}
    return item


def _character_sort_key(item: dict) -> tuple:
    group = item.get("group") or "custom"
    return (
        CHARACTER_GROUP_ORDER.get(group, 9),
        0,
        str(item.get("name") or "").lower(),
    )


def _official_seed_map() -> dict[str, dict]:
    result = {}
    for seed in _seed_character_items():
        clean = _clean_character(seed)
        clean["created"] = 0.0
        clean["updated"] = 0.0
        result[clean["id"]] = clean
    return result


def is_seed_owned_character(entry: dict) -> bool:
    character_id = safe_path_segment(str((entry or {}).get("id") or "").lower(), "")
    return bool(character_id and character_id in _official_seed_map())


def _merge_dict_preserve_paths(seed_value: dict, existing_value: dict, preserve_keys: set[str]) -> dict:
    result = _json_copy(seed_value) or {}
    existing = existing_value if isinstance(existing_value, dict) else {}
    for key in preserve_keys:
        if key in existing:
            value = existing.get(key)
            result[key] = _json_copy(value) if isinstance(value, (dict, list)) else value
    return result


def _merge_official_character(existing: dict, seed: dict) -> dict:
    merged = _json_copy(seed) or dict(seed)
    existing = existing if isinstance(existing, dict) else {}
    merged["created"] = existing.get("created") or seed.get("created") or time.time()
    merged["updated"] = float(existing.get("updated") or seed.get("updated") or time.time())
    # Stored actions are an operator-controlled allowlist.  In particular an
    # explicit [] must survive seed refreshes instead of expanding back to all
    # defaults.  A missing key still receives the seed defaults.
    merged["actions"] = _character_known_actions(
        existing.get("actions") if "actions" in existing else seed.get("actions")
    )
    merged["voice"] = _merge_dict_preserve_paths(
        seed.get("voice") if isinstance(seed.get("voice"), dict) else {},
        existing.get("voice") if isinstance(existing.get("voice"), dict) else {},
        {"voiceLibraryIds", "notes"},
    )
    merged["assets"] = _merge_dict_preserve_paths(
        seed.get("assets") if isinstance(seed.get("assets"), dict) else {},
        existing.get("assets") if isinstance(existing.get("assets"), dict) else {},
        {"outfits", "rig", "characterMedia", "notes"},
    )
    return _clean_character(merged, seed.get("id"))


def _official_character_identity_matches(existing: dict, seed: dict) -> bool:
    """Only merge a seed into an entry already carrying that official identity."""
    existing_source = existing.get("source") if isinstance(existing.get("source"), dict) else {}
    seed_source = seed.get("source") if isinstance(seed.get("source"), dict) else {}
    return bool(
        existing.get("id") == seed.get("id")
        and existing.get("kind") == seed.get("kind")
        and existing.get("group") == seed.get("group")
        and not existing.get("ownerEmail")
        and existing.get("visibility") != "private"
        and existing_source.get("system") == seed_source.get("system")
        and existing_source.get("providerId") == seed_source.get("providerId")
        and existing_source.get("sourceType") == seed_source.get("sourceType")
    )


def save_character_index(entries: list[dict]) -> None:
    CHARACTER_LIBRARY.mkdir(parents=True, exist_ok=True)
    tmp = CHARACTER_INDEX.with_name(f".{CHARACTER_INDEX.name}.tmp-{os.getpid()}")
    payload = {"version": 1, "updated": time.time(), "characters": entries}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CHARACTER_INDEX)


def load_character_posts() -> list[dict]:
    try:
        data = json.loads(CHARACTER_POSTS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    raw_posts = data.get("posts") if isinstance(data, dict) else data
    posts = []
    for item in raw_posts if isinstance(raw_posts, list) else []:
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("id") or "")
        character_id = safe_path_segment(str(item.get("characterId") or "").lower(), "")
        if not re.fullmatch(r"[a-f0-9]{16}", post_id) or not character_id:
            continue
        posts.append({
            "id": post_id,
            "characterId": character_id,
            "topic": str(item.get("topic") or ""),
            "platform": str(item.get("platform") or ""),
            "length": str(item.get("length") or ""),
            "text": str(item.get("text") or ""),
            "engine": str(item.get("engine") or ""),
            "ts": float(item.get("ts") or 0),
            "ownerEmail": normalize_email(str(item.get("ownerEmail") or "")),
        })
    posts.sort(key=lambda item: item.get("ts", 0), reverse=True)
    return posts


def save_character_posts(posts: list[dict]) -> None:
    CHARACTER_LIBRARY.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "updated": time.time(), "posts": posts}
    atomic_write_json(CHARACTER_POSTS, payload, 0o600)


def cap_character_posts(posts: list[dict], per_owner_limit: int = 200) -> list[dict]:
    counts: dict[tuple[str, str], int] = {}
    capped = []
    for item in posts:
        key = (
            str(item.get("characterId") or ""),
            normalize_email(str(item.get("ownerEmail") or "")),
        )
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= per_owner_limit:
            capped.append(item)
    return capped


def character_post_public(item: dict, can_delete: bool | None = None) -> dict:
    result = {key: value for key, value in item.items() if key != "ownerEmail"}
    result["kind"] = "text"
    if can_delete is not None:
        result["canDelete"] = bool(can_delete)
    return result


def character_posts_for_user(posts: list[dict], character_id: str, user: dict | None, service_token: bool = False) -> list[dict]:
    owner_email = normalize_email(str((user or {}).get("email") or ""))
    can_read_all = service_token or bool(user and user.get("role") == "admin")
    return [
        character_post_public(item, user_can_delete_character_post(user, item))
        for item in posts
        if item.get("characterId") == character_id
        and (can_read_all or (owner_email and item.get("ownerEmail") == owner_email))
    ]


def visible_character_posts_for_user(
    posts: list[dict],
    visible_character_ids: set[str],
    user: dict | None,
    service_token: bool = False,
) -> list[dict]:
    owner_email = normalize_email(str((user or {}).get("email") or ""))
    can_read_all = service_token or bool(user and user.get("role") == "admin")
    return [
        character_post_public(item, user_can_delete_character_post(user, item))
        for item in posts
        if item.get("characterId") in visible_character_ids
        and (can_read_all or (owner_email and item.get("ownerEmail") == owner_email))
    ]


def user_can_delete_character_post(user: dict | None, item: dict) -> bool:
    owner_email = normalize_email(str((user or {}).get("email") or ""))
    return bool(user and user.get("role") == "admin") or bool(owner_email and item.get("ownerEmail") == owner_email)


def append_character_post(character_id: str, settings: dict, result: dict, owner_email: str = "") -> dict:
    cid = safe_path_segment(str(character_id or "").lower().replace(" ", "-"), "")
    if not cid:
        raise KeyError("Character not found")
    get_character_entry(cid)
    record = {
        "id": secrets.token_hex(8),
        "characterId": cid,
        "topic": str(settings.get("topic") or ""),
        "platform": str(settings.get("platform") or ""),
        "length": str(settings.get("length") or settings.get("postLength") or ""),
        "text": str(result.get("text") or ""),
        "engine": str(result.get("engine") or ""),
        "ts": time.time(),
        "ownerEmail": normalize_email(owner_email),
    }
    with _CHARACTER_POST_LOCK:
        posts = load_character_posts()
        capped = cap_character_posts([record, *posts])
        save_character_posts(capped)
    return record


def load_character_index() -> list[dict]:
    try:
        data = json.loads(CHARACTER_INDEX.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    items = data.get("characters") if isinstance(data, dict) else data
    existing = []
    changed = not bool(items if isinstance(items, list) else [])
    for item in (items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        clean = _clean_character(item)
        existing.append(clean)
        if clean != item:
            changed = True
    by_id = {item["id"]: item for item in existing}
    for seed in _official_seed_map().values():
        if seed["id"] in by_id:
            if not _official_character_identity_matches(by_id[seed["id"]], seed):
                raise RuntimeError(f"角色 ID {seed['id']} 已被非官方角色使用，已停止自動同步")
            merged = _merge_official_character(by_id[seed["id"]], seed)
            if merged != by_id[seed["id"]]:
                by_id[seed["id"]] = merged
                changed = True
        else:
            by_id[seed["id"]] = seed
            changed = True
    result = sorted(by_id.values(), key=_character_sort_key)
    if changed:
        save_character_index(result)
    return result


def _public_scrub(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in CHARACTER_PUBLIC_SENSITIVE_KEYS:
                continue
            cleaned = _public_scrub(item)
            if cleaned is not None:
                result[key] = cleaned
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            cleaned = _public_scrub(item)
            if cleaned is not None:
                result.append(cleaned)
        return result
    if isinstance(value, str) and (value.startswith("/") or value.startswith("~/")):
        return None
    return value


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def is_experimental_character(entry: dict) -> bool:
    return entry.get("type") == "experimental" or bool(entry.get("ownerEmail")) or entry.get("visibility") == "private"


def user_can_see_character(user: dict | None, entry: dict, service_token: bool = False) -> bool:
    if service_token:
        return True
    if not is_experimental_character(entry):
        return True
    if user and user.get("role") == "admin":
        return True
    email = normalize_email(str((user or {}).get("email") or ""))
    owner = normalize_email(str(entry.get("ownerEmail") or ""))
    return bool(email and owner and email == owner)


def user_can_edit_character(user: dict | None, entry: dict, service_token: bool = False) -> bool:
    if service_token:
        return True
    if user and user.get("role") == "admin":
        return True
    if not is_experimental_character(entry):
        return False
    email = normalize_email(str((user or {}).get("email") or ""))
    owner = normalize_email(str(entry.get("ownerEmail") or ""))
    return bool(email and owner and email == owner)


def is_operator_user(user: dict | None) -> bool:
    email = normalize_email(str((user or {}).get("email") or ""))
    if OPERATOR_EMAIL and email == OPERATOR_EMAIL:
        return True
    # Local/legacy sessions exist only on the operator-controlled machine.
    return bool(email in SYNTHETIC_SESSION_EMAILS and (user or {}).get("role") == "admin")


def user_can_generate_character(user: dict | None, entry: dict, service_token: bool = False) -> bool:
    """Keep public character discovery separate from generation authority."""
    if service_token:
        return True
    if not user_can_see_character(user, entry, False):
        return False
    if (
        normalized_visual_class(entry.get("visualClass")) == "realistic"
        and str(entry.get("kind") or "").strip().lower() == "human"
    ):
        return is_operator_user(user)
    return True


def experimental_email_slug(email: str) -> str:
    normalized = normalize_email(email)
    return safe_path_segment(normalized.replace("@", "-").replace(".", "-"), "user")


def experimental_character_dir(entry: dict) -> Path | None:
    owner = normalize_email(str(entry.get("ownerEmail") or ""))
    cid = safe_path_segment(str(entry.get("id") or "").lower(), "")
    if not owner or not cid:
        return None
    base = (CHARACTER_USER_ASSET_ROOT / experimental_email_slug(owner) / cid).resolve()
    try:
        if not base.is_relative_to(CHARACTER_USER_ASSET_ROOT.resolve()):
            return None
    except OSError:
        return None
    return base


def experimental_user_asset_path(entry: dict, filename: str) -> Path | None:
    if not is_experimental_character(entry):
        return None
    name = Path(str(filename or "")).name
    if not name or name != filename or name.startswith("."):
        return None
    if Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    base = experimental_character_dir(entry)
    if base is None:
        return None
    target = (base / name).resolve()
    try:
        if not target.is_relative_to(base.resolve()) or not target.is_file():
            return None
    except OSError:
        return None
    return target


def _character_media_public(entry: dict) -> list[dict]:
    assets = entry.get("assets") if isinstance(entry.get("assets"), dict) else {}
    media = assets.get("characterMedia") if isinstance(assets.get("characterMedia"), list) else []
    cid = urllib.parse.quote(str(entry.get("id") or ""), safe="")
    result = []
    for item in media:
        if not isinstance(item, dict):
            continue
        source = str(item.get("sourcePath") or item.get("path") or "")
        filename = Path(source).name
        if not filename:
            continue
        clean = _public_scrub(_json_copy(item) or dict(item)) or {}
        clean["url"] = f"/api/characters/{cid}/asset?{urllib.parse.urlencode({'kind': 'user-photo', 'file': filename})}"
        clean["filename"] = filename
        result.append(clean)
    return result


def _character_asset_source(entry: dict, kind: str):
    assets = entry.get("assets") if isinstance(entry.get("assets"), dict) else {}
    asset = assets.get(kind)
    if isinstance(asset, dict):
        return asset.get("sourcePath") or asset.get("path")
    if isinstance(asset, str):
        return asset
    return ""


def character_asset_path(entry: dict, kind: str) -> Path | None:
    if kind not in CHARACTER_ASSET_KINDS:
        return None
    source = str(_character_asset_source(entry, kind) or "").strip()
    if not source:
        return None
    path = Path(source).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if not any(_path_is_under(resolved, root) for root in CHARACTER_ASSET_ALLOWED_ROOTS):
        return None
    return resolved


def character_asset_urls(entry: dict) -> dict[str, str]:
    urls = {}
    cid = urllib.parse.quote(str(entry.get("id") or ""), safe="")
    for kind in sorted(CHARACTER_ASSET_KINDS):
        path = character_asset_path(entry, kind)
        if path is not None:
            url = f"/api/characters/{cid}/asset?kind={urllib.parse.quote(kind, safe='')}"
            if kind == "avatar":
                # Profile pictures can be regenerated in-place by AI-Sister.
                # A content revision keeps every Castle identity surface in sync
                # instead of serving the previous face from the browser cache.
                try:
                    url += f"&v={content_sha256_cached(path)[:12]}"
                except OSError:
                    pass
            urls[kind] = url
    return urls


def character_asset_readiness(entry: dict) -> dict[str, bool]:
    assets = entry.get("assets") if isinstance(entry.get("assets"), dict) else {}
    voice = entry.get("voice") if isinstance(entry.get("voice"), dict) else {}
    outfits = assets.get("outfits") if isinstance(assets.get("outfits"), dict) else {}
    rig = assets.get("rig") if isinstance(assets.get("rig"), dict) else {}
    return {
        "avatar": character_asset_path(entry, "avatar") is not None,
        "card": character_asset_path(entry, "card") is not None,
        "expressionSheet": character_asset_path(entry, "expressionSheet") is not None,
        "outfits": bool(outfits.get("count") or outfits.get("status") or outfits.get("artifactId")),
        "rig": bool(rig.get("status") or rig.get("artifactId")),
        "voice": bool(voice.get("persona") or voice.get("voiceLibraryIds") or voice.get("trainingSource")),
        "media": bool(assets.get("characterMedia")),
    }


def character_public(
    entry: dict,
    capability_runtime: dict | None = None,
    generation_allowed: bool = True,
) -> dict:
    item = _public_scrub(_json_copy(entry) or dict(entry))
    official = is_seed_owned_character(entry)
    item["official"] = official
    if official:
        item.pop("ownerEmail", None)
        item.pop("visibility", None)
    else:
        assets = item.get("assets") if isinstance(item.get("assets"), dict) else {}
        assets["characterMedia"] = _character_media_public(entry)
        item["assets"] = assets
    item["workflowActions"] = [dict(CHARACTER_ACTION_BY_ID[action_id]) for action_id in item.get("actions", []) if action_id in CHARACTER_ACTION_BY_ID]
    item["assetUrls"] = character_asset_urls(entry)
    item["assetReadiness"] = character_asset_readiness(entry)
    item["capabilities"] = apply_character_capability_authorization(
        character_capabilities(entry, capability_runtime),
        generation_allowed,
    )
    return item


def character_public_for_user(
    entry: dict,
    user: dict | None,
    service_token: bool = False,
    capability_runtime: dict | None = None,
) -> dict:
    runtime = breezy_persona_runtime() if capability_runtime is None else capability_runtime
    return character_public(
        entry,
        runtime,
        user_can_generate_character(user, entry, service_token),
    )


def get_character_entry(character_id: str) -> dict:
    cid = safe_path_segment(str(character_id or "").lower().replace(" ", "-"), "character")
    with _CHARACTER_LOCK:
        for item in load_character_index():
            if item["id"] == cid:
                return item
    raise KeyError("Character not found")


def provider_fabric_canonical_resource_resolver(
    actor,
    character_ids: tuple[str, ...],
    asset_ids: tuple[str, ...],
):
    """Resolve legacy Castle characters under the current authenticated request.

    Provider-store resources are resolved before this callback.  This bridge
    receives only missing IDs and never trusts browser labels or classes.
    """

    from provider_fabric.models import CharacterRef, MediaAsset, MediaKind, VisualClass

    bound_actor = getattr(_PROVIDER_CANONICAL_CONTEXT, "actor", None)
    user = getattr(_PROVIDER_CANONICAL_CONTEXT, "user", None)
    if bound_actor != actor or not isinstance(user, dict):
        raise PermissionError("canonical resolver has no authenticated request context")
    resolved = {}
    for requested_id in character_ids:
        lookup_id = (
            requested_id[len("castle:"):]
            if requested_id.startswith("castle:")
            else requested_id
        )
        entry = get_character_entry(lookup_id)
        if not user_can_generate_character(user, entry):
            raise KeyError(requested_id)
        visual_class = normalized_visual_class(entry.get("visualClass"))
        if visual_class not in {"anime", "realistic"}:
            raise KeyError(requested_id)
        resolved[requested_id] = CharacterRef(
            character_id=requested_id,
            visual_class=VisualClass(visual_class),
            display_name=str(entry.get("name") or requested_id)[:160],
        )
    runtime = getattr(_PROVIDER_CANONICAL_CONTEXT, "runtime", None)

    def history_classification_is_exact(
        projection_id: str,
        version_id: str,
        asset: dict,
    ) -> bool:
        metadata = asset.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if (
            not projection_id.startswith("autoip-history:")
            and metadata.get("source") != "autoip_history"
        ):
            return True
        try:
            classification = runtime.store.get_owned_remote_asset_classification(
                actor.user_id, projection_id,
            )
        except (KeyError, RuntimeError, ValueError):
            return False
        return bool(
            classification is not None
            and classification.get("asset_version_id") == version_id
            and classification.get("visual_class") == asset.get("visual_class")
            and tuple(classification.get("character_ids") or ())
                == tuple(asset.get("character_ids") or ())
        )

    resolved_assets = {}
    for requested_id in asset_ids:
        if requested_id.startswith("provider-version:"):
            if runtime is None:
                raise PermissionError("provider runtime is unavailable")
            version_id = requested_id[len("provider-version:"):]
            if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,109}", version_id) is None:
                raise KeyError(requested_id)
            evidence = runtime.store.get_owned_remote_asset_version_source_by_id(
                actor.user_id, version_id
            )
            projection_id = str(evidence["projection_id"])
            asset = runtime.store.get_owned_remote_asset(actor.user_id, projection_id)
            versions = [
                item for item in asset.get("versions", ())
                if item.get("version_id") == version_id
            ]
            if len(versions) != 1:
                raise KeyError(requested_id)
            version = versions[0]
            visual_class = asset.get("visual_class")
            character_ids = tuple(asset.get("character_ids") or ())
            for character_id in character_ids:
                runtime.store.get_owned_character(actor.user_id, character_id)
            if (
                visual_class not in {"anime", "realistic"}
                or evidence.get("media_kind") != asset.get("media_kind")
                or evidence.get("visual_class") != visual_class
                or version.get("source_available") is not True
                or not isinstance(evidence.get("sha256"), str)
                or not isinstance(evidence.get("byte_size"), int)
                or not isinstance(evidence.get("mime_type"), str)
                or not history_classification_is_exact(
                    projection_id, version_id, asset
                )
            ):
                raise KeyError(requested_id)
            from provider_fabric.owned_media_source import owned_provider_asset_uri
            resolved_assets[requested_id] = MediaAsset(
                asset_id=requested_id,
                kind=MediaKind(asset["media_kind"]),
                uri=owned_provider_asset_uri(projection_id, version_id),
                visual_class=VisualClass(visual_class),
                character_ids=character_ids,
                mime_type=evidence["mime_type"],
                byte_size=evidence["byte_size"],
                sha256=evidence["sha256"],
                duration_seconds=evidence.get("duration_seconds"),
            )
            continue
        if requested_id.startswith("production-media:"):
            if runtime is None:
                raise PermissionError("provider runtime is unavailable")
            binding = production_state_store().get_execution_asset(
                actor.user_id, requested_id
            )
            asset = runtime.store.get_owned_remote_asset(
                actor.user_id, binding["projection_id"]
            )
            versions = [
                item for item in asset.get("versions", [])
                if item.get("version_id") == binding["asset_version_id"]
            ]
            if len(versions) != 1:
                raise KeyError(requested_id)
            version = versions[0]
            visual_class = asset.get("visual_class")
            if (
                visual_class not in {"anime", "realistic"}
                or version.get("source_available") is not True
                or not isinstance(version.get("sha256"), str)
                or not isinstance(version.get("byte_size"), int)
                or not isinstance(version.get("mime_type"), str)
                or not history_classification_is_exact(
                    binding["projection_id"], binding["asset_version_id"], asset
                )
            ):
                raise KeyError(requested_id)
            from provider_fabric.owned_media_source import owned_provider_asset_uri
            resolved_assets[requested_id] = MediaAsset(
                asset_id=requested_id,
                kind=MediaKind(asset["media_kind"]),
                uri=owned_provider_asset_uri(
                    binding["projection_id"], binding["asset_version_id"]
                ),
                visual_class=VisualClass(visual_class),
                character_ids=tuple(asset.get("character_ids") or ()),
                mime_type=version["mime_type"],
                byte_size=version["byte_size"],
                sha256=version["sha256"],
                duration_seconds=version.get("duration_seconds"),
            )
            continue
        match = re.fullmatch(
            r"castle-character:([a-z0-9][a-z0-9._:-]{0,127}):avatar",
            requested_id,
        )
        if match is None:
            raise KeyError(requested_id)
        linked_character_id = match.group(1)
        lookup_id = (
            linked_character_id[len("castle:"):]
            if linked_character_id.startswith("castle:")
            else linked_character_id
        )
        entry = get_character_entry(lookup_id)
        if not user_can_generate_character(user, entry):
            raise KeyError(requested_id)
        visual_class = normalized_visual_class(entry.get("visualClass"))
        avatar = character_asset_path(entry, "avatar")
        if visual_class not in {"anime", "realistic"} or avatar is None:
            raise KeyError(requested_id)
        try:
            byte_size = avatar.stat().st_size
        except OSError as error:
            raise KeyError(requested_id) from error
        mime_type = mimetypes.guess_type(avatar.name)[0]
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise KeyError(requested_id)
        resolved_assets[requested_id] = MediaAsset(
            asset_id=requested_id,
            kind=MediaKind.IMAGE,
            uri=f"castle://character-assets/{urllib.parse.quote(lookup_id)}/avatar",
            visual_class=VisualClass(visual_class),
            character_ids=(linked_character_id,),
            mime_type=mime_type,
            byte_size=byte_size,
            sha256=content_sha256_cached(avatar),
        )
    return resolved, resolved_assets


def provider_fabric_capability_materializer(
    actor,
    intent,
    action_id: str,
    feature_id: str,
    profile_key: str,
    options: dict,
):
    """Materialize one exact, currently-ready Castle provider character grant."""

    from provider_fabric.h3_profiles import h3_character_profiles
    from provider_fabric.variant_profiles import (
        CharacterCapabilityGrant,
        VariantProfileError,
        materialize_variant_request,
    )

    bound_actor = getattr(_PROVIDER_CANONICAL_CONTEXT, "actor", None)
    user = getattr(_PROVIDER_CANONICAL_CONTEXT, "user", None)
    if bound_actor != actor or not isinstance(user, dict):
        raise PermissionError("capability materializer has no authenticated request context")
    provider_id, separator, variant_id = profile_key.partition(":")
    if not separator or profile_key != f"{provider_id}:{variant_id}":
        raise KeyError(profile_key)
    runtime = getattr(_PROVIDER_CANONICAL_CONTEXT, "runtime", None)
    if provider_id == "local_comfy":
        contract_id = PROVIDER_FABRIC_H3_VARIANT_CONTRACTS.get(variant_id)
        if not contract_id:
            raise KeyError(variant_id)
        candidates = h3_character_profiles({contract_id})
    elif provider_id == "autoip":
        if runtime is None:
            raise PermissionError("provider runtime is unavailable")
        candidates = runtime.autoip_profiles(actor)
    else:
        raise KeyError(provider_id)
    profiles = {profile.key: profile for profile in candidates}
    profile = profiles.get(profile_key)
    if profile is None:
        raise KeyError(profile_key)
    if profile.action_id != action_id or profile.feature_id != feature_id:
        raise VariantProfileError(
            "capability_binding_mismatch",
            "The selected action does not match this provider profile.",
        )

    if provider_id == "autoip":
        if (
            not profile.execution_ready
            or not 1 <= len(intent.characters) <= 16
            or len({item.character_id for item in intent.characters})
            != len(intent.characters)
        ):
            raise KeyError("AutoIP character selection is not execution-ready")

        grants = []
        for character_ref in intent.characters:
            lookup_id = (
                character_ref.character_id[len("castle:"):]
                if character_ref.character_id.startswith("castle:")
                else character_ref.character_id
            )
            try:
                entry = get_character_entry(lookup_id)
            except KeyError:
                entry = None
            if entry is not None:
                if not user_can_generate_character(user, entry):
                    raise KeyError(character_ref.character_id)
                canonical_class = normalized_visual_class(entry.get("visualClass"))
            else:
                if runtime is None:
                    raise PermissionError("provider runtime is unavailable")
                stored = runtime.store.get_owned_character(
                    actor.user_id, character_ref.character_id
                )
                canonical_class = str(stored.get("visual_class") or "")
            if (
                canonical_class != character_ref.visual_class.value
                or canonical_class != profile.visual_class.value
            ):
                raise VariantProfileError(
                    "visual_class_mismatch",
                    "An AutoIP character belongs to another visual class.",
                )
            if canonical_class == "realistic":
                rights = character_rights_store().get(
                    actor.user_id, character_ref.character_id
                )
                if (
                    rights is None
                    or rights.revoked
                    or profile.provider_id not in rights.allowed_provider_ids
                    or profile.capability.value not in rights.allowed_capabilities
                    or "private" not in rights.allowed_usage_scopes
                ):
                    raise VariantProfileError(
                        "person_rights_scope_missing",
                        "This real-person character has no current private-use authorization.",
                    )
            grants.append(CharacterCapabilityGrant(
                character=character_ref,
                action_id=action_id,
                feature_id=feature_id,
                capability=profile.capability,
                profile_keys=(profile.key,),
                allowed_options=dict(profile.option_values),
                server_bound_slots=profile.server_bound_slots,
                enabled=True,
                runtime_ready=True,
            ))

        remaining = list(intent.assets)
        bindings: dict[str, object] = {}
        slots = tuple(profile.input_slots)
        for index, slot in enumerate(slots):
            same_kind = [item for item in remaining if item.kind is slot.media_kind]
            later_minimum = sum(
                int(other.minimum_count or 0)
                for other in slots[index + 1:]
                if other.media_kind is slot.media_kind
            )
            available = max(0, len(same_kind) - later_minimum)
            take = min(slot.maximum_count, available)
            if take < int(slot.minimum_count or 0):
                raise VariantProfileError(
                    "required_input_missing",
                    "AutoIP required input cardinality is incomplete.",
                )
            if take:
                selected_assets = tuple(same_kind[:take])
                bindings[slot.slot_id] = (
                    selected_assets[0]
                    if slot.maximum_count == 1
                    else selected_assets
                )
                selected_ids = {item.asset_id for item in selected_assets}
                remaining = [
                    item for item in remaining if item.asset_id not in selected_ids
                ]
        if remaining:
            raise VariantProfileError(
                "input_cardinality_invalid",
                "AutoIP received inputs outside the selected profile.",
            )
        identity_assets = [
            asset
            for slot in slots if slot.identity_bearing and slot.slot_id in bindings
            for asset in (
                bindings[slot.slot_id]
                if isinstance(bindings[slot.slot_id], tuple)
                else (bindings[slot.slot_id],)
            )
        ]
        # Text-only AutoIP operations intentionally have no identity-bearing
        # input slot.  Require linked reference media only when this exact
        # profile declares such a slot; otherwise T2I/T2V could never pass the
        # production materializer despite a current compatibility receipt.
        identity_slots = tuple(slot for slot in slots if slot.identity_bearing)
        if identity_slots and any(
            not any(
                character.character_id in asset.character_ids
                for asset in identity_assets
            )
            for character in intent.characters
        ):
            raise VariantProfileError(
                "asset_identity_mismatch",
                "Every AutoIP character requires an exact linked identity image.",
            )
        return materialize_variant_request(
            profile,
            grants[0],
            additional_grants=tuple(grants[1:]),
            submitted_options=options,
            client_assets=bindings,
            preferred_providers=intent.preferred_providers,
        )

    if len(intent.characters) != 1 or len(intent.assets) != 1:
        raise KeyError("local H3 character plans require one character and one exact image")
    character_ref = intent.characters[0]
    lookup_id = (
        character_ref.character_id[len("castle:"):]
        if character_ref.character_id.startswith("castle:")
        else character_ref.character_id
    )
    provider_bound = intent.assets[0].asset_id.startswith("provider-version:")
    if provider_bound:
        entry = None
    else:
        try:
            entry = get_character_entry(lookup_id)
        except KeyError:
            entry = None
    if entry is not None:
        if not user_can_generate_character(user, entry):
            raise KeyError(character_ref.character_id)
        canonical_class = normalized_visual_class(entry.get("visualClass"))
        capability = resolve_character_capability(
            entry, action_id, feature_id, variant_id,
        )
        allowed_options = dict(capability.get("allowedOptions") or {})
        if "motionProfile" in allowed_options:
            allowed_options["motion_profile"] = allowed_options.pop("motionProfile")
        enabled = capability.get("enabled") is True
        runtime_ready = capability.get("runtimeReady") is True
    else:
        if runtime is None:
            raise PermissionError("provider runtime is unavailable")
        stored = runtime.store.get_owned_character(
            actor.user_id, character_ref.character_id
        )
        canonical_class = str(stored.get("visual_class") or "")
        if canonical_class != character_ref.visual_class.value:
            raise VariantProfileError(
                "visual_class_mismatch", "The provider character class has changed."
            )
        if canonical_class == "realistic":
            rights = character_rights_store().get(
                actor.user_id, character_ref.character_id
            )
            if (
                rights is None or rights.revoked
                or profile.provider_id not in rights.allowed_provider_ids
                or profile.capability.value not in rights.allowed_capabilities
                or "private" not in rights.allowed_usage_scopes
            ):
                raise VariantProfileError(
                    "person_rights_scope_missing",
                    "This real-person character has no current private-use authorization.",
                )
        allowed_options = dict(profile.option_values)
        enabled = True
        runtime_ready = profile.runtime_ready
    if profile.visual_class.value != canonical_class:
        raise VariantProfileError(
            "visual_class_mismatch", "The character class does not match this H3 profile."
        )
    grant = CharacterCapabilityGrant(
        character=character_ref,
        action_id=action_id,
        feature_id=feature_id,
        capability=profile.capability,
        profile_keys=(profile.key,),
        allowed_options=allowed_options,
        server_bound_slots=profile.server_bound_slots,
        enabled=enabled,
        runtime_ready=runtime_ready,
    )
    slot = profile.input_slots[0]
    asset = intent.assets[0]
    if asset.character_ids != (character_ref.character_id,):
        raise VariantProfileError(
            "asset_identity_mismatch", "The canonical avatar is not linked to this character."
        )
    return materialize_variant_request(
        profile,
        grant,
        submitted_options=options,
        server_assets={slot.slot_id: asset},
        preferred_providers=intent.preferred_providers,
    )


def character_entry_for_generation(
    character_id: str,
    user: dict | None = None,
    service_token: bool = False,
) -> dict | None:
    if not str(character_id or "").strip():
        return None
    try:
        item = get_character_entry(character_id)
    except KeyError as error:
        raise ValueError("找不到指定角色") from error
    if not user_can_generate_character(user, item, service_token):
        raise PermissionError("這個真人角色只允許角色本人操作")
    return item


def character_generation_context(item: dict | None) -> dict | None:
    if not item:
        return None
    return _public_scrub({
        "id": item["id"],
        "name": item["name"],
        "kind": item.get("kind", ""),
        "type": item.get("type", ""),
        "visualClass": normalized_visual_class(item.get("visualClass")),
        "group": item.get("group", ""),
        "tagline": item.get("tagline", ""),
        "profile": item.get("profile", ""),
        "personality": item.get("personality", ""),
        "origin": item.get("origin", ""),
        "color": item.get("color", ""),
        "style": item.get("style", {}),
        "languageModel": item.get("languageModel", {}),
        "voice": item.get("voice", {}),
        "profileData": item.get("profileData", {}),
    })


def character_context_for_generation(character_id: str, user: dict | None = None, service_token: bool = False) -> dict | None:
    return character_generation_context(character_entry_for_generation(character_id, user, service_token))


def character_post_extra(character: dict) -> str:
    if not character:
        return ""
    style = character.get("style") if isinstance(character.get("style"), dict) else {}
    language_model = character.get("languageModel") if isinstance(character.get("languageModel"), dict) else {}
    lines = [
        f"角色名稱：{character.get('name', '')}",
        f"角色類型：{character.get('kind', '')}",
        f"Tagline：{str(character.get('tagline') or '')[:300]}",
        f"Profile：{str(character.get('profile') or '')[:1200]}",
        f"Personality：{str(character.get('personality') or '')[:1200]}",
        f"來源：{character.get('origin', '')}",
        f"基本 style：{style.get('base', '')} {style.get('substyle', '')}".strip(),
        f"固定 prompt：{str(style.get('fixedPrompt') or '')[:800]}",
        f"語言模組：{language_model.get('provider', '')} {language_model.get('model', '')}".strip(),
    ]
    body = "\n".join(line for line in lines if line and not line.endswith("："))
    if not body:
        return ""
    return "角色設定（用來維持身份、口吻與內容一致；不要逐字列出這些欄位）：\n" + body


def normalized_visual_class(value) -> str:
    """Return the only visual classes that can participate in role routing."""
    visual_class = str(value or "").strip().lower()
    return visual_class if visual_class in CHARACTER_VISUAL_CLASSES else "unknown"


def workflow_extra_data(
    workflow_id: str,
    character: dict | None = None,
    submitted_by: dict | None = None,
    action_id: str = "",
    variant_id: str = "",
) -> dict:
    workflow = {"id": workflow_id}
    if character:
        workflow["character"] = character
    extra_data = {"extra_pnginfo": {"workflow": workflow}}
    if submitted_by:
        extra_data["submittedBy"] = {
            "email": normalize_email(str(submitted_by.get("email") or "")),
            "displayName": str(submitted_by.get("displayName") or ""),
        }
    if character:
        extra_data["characterId"] = str(character.get("id") or "")[:128]
        extra_data["characterName"] = str(character.get("name") or "")[:160]
        extra_data["visualClass"] = normalized_visual_class(character.get("visualClass"))
    clean_action_id = safe_path_segment(str(action_id or ""), "")
    if clean_action_id:
        action = CHARACTER_ACTION_BY_ID.get(clean_action_id) or {}
        extra_data["actionId"] = clean_action_id
        extra_data["actionLabel"] = str(action.get("label") or clean_action_id)[:160]
    clean_variant_id = str(variant_id or "").strip()[:160]
    if clean_variant_id:
        workflow["variantId"] = clean_variant_id
        extra_data["variantId"] = clean_variant_id
    return extra_data


def load_job_index() -> dict[str, dict]:
    with _JOB_INDEX_LOCK:
        try:
            mtime = JOB_INDEX_PATH.stat().st_mtime_ns
        except FileNotFoundError:
            _JOB_INDEX_CACHE["mtime"] = None
            _JOB_INDEX_CACHE["jobs"] = {}
            return {}
        except OSError:
            return {}
        cached_jobs = _JOB_INDEX_CACHE.get("jobs")
        if _JOB_INDEX_CACHE.get("mtime") == mtime and isinstance(cached_jobs, dict):
            return dict(cached_jobs)
        try:
            data = json.loads(JOB_INDEX_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            jobs = {}
        else:
            raw = data.get("jobs") if isinstance(data, dict) else data
            jobs = raw if isinstance(raw, dict) else {}
        _JOB_INDEX_CACHE["mtime"] = mtime
        _JOB_INDEX_CACHE["jobs"] = dict(jobs)
        return dict(jobs)


def submitted_by_email(value) -> str:
    extra_data = None
    if isinstance(value, dict):
        if isinstance(value.get("prompt"), list):
            prompt = value.get("prompt") or []
            extra_data = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
        else:
            extra_data = value
    elif isinstance(value, (list, tuple)):
        values = list(value)
        extra_data = values[3] if len(values) > 3 and isinstance(values[3], dict) else {}
    if not isinstance(extra_data, dict):
        return ""
    submitted_by = extra_data.get("submittedBy") if isinstance(extra_data.get("submittedBy"), dict) else {}
    email = normalize_email(str(submitted_by.get("email") or ""))
    if email:
        return email
    workflow = ((extra_data.get("extra_pnginfo") or {}).get("workflow") or {})
    if not isinstance(workflow, dict):
        return ""
    submitted_by = workflow.get("submittedBy") if isinstance(workflow.get("submittedBy"), dict) else {}
    return normalize_email(str(submitted_by.get("email") or ""))


def job_owner_email(prompt_id: str, extra_data_or_item=None, job_index: dict[str, dict] | None = None) -> str:
    pid = str(prompt_id or "").strip()
    jobs = job_index if isinstance(job_index, dict) else load_job_index()
    indexed = jobs.get(pid) if pid else None
    if isinstance(indexed, dict):
        email = normalize_email(str(indexed.get("email") or ""))
        if email:
            return email
    return submitted_by_email(extra_data_or_item)


def owner_matches_account(owner: str, email: str) -> bool:
    """Include the operator's pre-account work without rewriting provenance.

    This is a one-way data-ownership compatibility rule, never a login alias.
    Synthetic cookies must not authenticate as the operator.
    """
    owner = normalize_email(str(owner or ""))
    email = normalize_email(str(email or ""))
    return bool(email and owner and (
        owner == email or (email == OPERATOR_EMAIL and owner in SYNTHETIC_SESSION_EMAILS)
    ))


def user_can_access_job(user: dict | None, prompt_id: str, extra_data_or_item=None, job_index: dict[str, dict] | None = None) -> bool:
    if user and user.get("role") == "admin":
        return True
    email = normalize_email(str((user or {}).get("email") or ""))
    if not email:
        return False
    return owner_matches_account(job_owner_email(prompt_id, extra_data_or_item, job_index), email)


def prompt_id_hint_valid(prompt_id: str) -> bool:
    pid = str(prompt_id or "").strip()
    if not pid or pid == "disk":
        return False
    if pid.startswith("local-"):
        return bool(re.fullmatch(r"local-[A-Za-z0-9_-]{6,80}", pid))
    return bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", pid))


def history_output_members(outputs) -> set[tuple[str, str, str]]:
    members: set[tuple[str, str, str]] = set()

    def add_output(out) -> None:
        if not isinstance(out, dict):
            return
        filename = str(out.get("filename") or "")
        if not filename:
            return
        members.add((filename, str(out.get("subfolder") or ""), str(out.get("type") or "output")))

    if isinstance(outputs, dict):
        nodes = outputs.values()
    elif isinstance(outputs, list):
        nodes = outputs
    else:
        nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if "filename" in node:
            add_output(node)
        for key in ("images", "gifs", "audio", "video"):
            values = node.get(key)
            if isinstance(values, list):
                for out in values:
                    add_output(out)
    return members


def prompt_output_members(prompt_id: str, job_index: dict[str, dict] | None = None) -> tuple[set[tuple[str, str, str]], str]:
    pid = str(prompt_id or "").strip()
    if not prompt_id_hint_valid(pid):
        return set(), ""
    now = time.time()
    with _PROMPT_OUTPUTS_CACHE_LOCK:
        cached = _PROMPT_OUTPUTS_CACHE.get(pid)
        if cached and now - float(cached.get("ts") or 0) <= _PROMPT_OUTPUTS_CACHE_TTL:
            members = cached.get("members") if isinstance(cached.get("members"), set) else set()
            return set(members), str(cached.get("owner") or "")
    item = None
    if pid.startswith("local-"):
        item = direct_history_detail(pid)
    else:
        history = request_json(f"/history/{urllib.parse.quote(pid)}")
        item = history.get(pid) if isinstance(history, dict) else None
    if not isinstance(item, dict):
        return set(), ""
    members = history_output_members(item.get("outputs"))
    owner = job_owner_email(pid, item, job_index)
    with _PROMPT_OUTPUTS_CACHE_LOCK:
        _PROMPT_OUTPUTS_CACHE[pid] = {"ts": now, "members": set(members), "owner": owner}
        if len(_PROMPT_OUTPUTS_CACHE) > _PROMPT_OUTPUTS_CACHE_MAX:
            oldest = sorted(_PROMPT_OUTPUTS_CACHE.items(), key=lambda item: float(item[1].get("ts") or 0))
            for key, _value in oldest[: max(1, len(_PROMPT_OUTPUTS_CACHE) - _PROMPT_OUTPUTS_CACHE_MAX)]:
                _PROMPT_OUTPUTS_CACHE.pop(key, None)
    return members, owner


def view_item_owner_email(filename: str, subfolder: str, view_type: str, prompt_id: str = "", job_index: dict[str, dict] | None = None) -> str:
    try:
        target = resolve_view_path(filename, subfolder, view_type)
    except ValueError:
        return ""
    if target is None:
        return ""
    sidecar = load_direct_sidecar(target)
    if isinstance(sidecar, dict):
        raw_receipt = sidecar.get("provenance")
        if raw_receipt is not None:
            verified = verify_output_provenance_receipt(target, raw_receipt)
            if verified:
                return normalize_email(str(verified.get("ownerEmail") or ""))
            if isinstance(raw_receipt, dict) and any(
                raw_receipt.get(key) for key in ("signature", "contentSha256", "authority")
            ):
                return ""
        owner = job_owner_email(str(sidecar.get("promptId") or ""), sidecar, job_index)
        if owner:
            return owner
    if view_type == "output" and library_service.catalog_path().is_file():
        association = library_service.catalog(library_server_context()).association(
            "output", "/".join(filter(None, (subfolder, filename)))
        )
        if association:
            return association["owner"]
    if prompt_id_hint_valid(prompt_id):
        try:
            members, owner = prompt_output_members(prompt_id, job_index)
        except Exception:
            return ""
        if (filename, subfolder, view_type) in members:
            return owner
    return ""


def user_can_access_view_item(user: dict | None, filename: str, subfolder: str, view_type: str, prompt_id: str = "") -> bool:
    if user and user.get("role") == "admin":
        return True
    voiceover_parts = str(subfolder or "").split("/")
    if (
        is_operator_user(user)
        and view_type == "output"
        and len(voiceover_parts) == 3
        and voiceover_parts[:2] == ["Castle", "voiceover"]
        and VOICEOVER_BATCH_ID_RE.fullmatch(voiceover_parts[2])
        and VOICEOVER_SEGMENT_FILE_RE.fullmatch(str(filename or ""))
    ):
        return True
    email = normalize_email(str((user or {}).get("email") or ""))
    if not email:
        return False
    jobs = load_job_index()
    owner = view_item_owner_email(filename, subfolder, view_type, prompt_id, jobs)
    if owner:
        return owner_matches_account(owner, email)
    return False


def save_job_index(jobs: dict[str, dict]) -> None:
    with _JOB_INDEX_LOCK:
        ordered = sorted(jobs.items(), key=lambda item: float(item[1].get("ts") or 0), reverse=True)[:2000]
        clean = dict(ordered)
        atomic_write_json(JOB_INDEX_PATH, {"version": 1, "updated": time.time(), "jobs": clean}, 0o600)
        try:
            mtime = JOB_INDEX_PATH.stat().st_mtime_ns
        except OSError:
            mtime = None
        _JOB_INDEX_CACHE["mtime"] = mtime
        _JOB_INDEX_CACHE["jobs"] = dict(clean)


def record_job_attribution(
    prompt_id: str,
    submitted_by: dict | None,
    character_id: str = "",
    feature: str = "",
    action_id: str = "",
    variant_id: str = "",
    scope: str = "",
    job_kind: str = "",
    character_context: dict | None = None,
) -> None:
    pid = str(prompt_id or "").strip()
    if not pid or not submitted_by:
        return
    email = normalize_email(str(submitted_by.get("email") or ""))
    if not email:
        return
    entry = {
        "email": email,
        "displayName": str(submitted_by.get("displayName") or email),
        "ts": time.time(),
    }
    if character_id:
        exact_character_id = stable_character_identifier(character_id)
        if not exact_character_id:
            return
        entry["characterId"] = exact_character_id
        bound_context = (
            character_context
            if isinstance(character_context, dict)
            and stable_character_identifier(character_context.get("id")) == exact_character_id
            else None
        )
        if bound_context is not None:
            entry["characterName"] = str(bound_context.get("name") or "")[:160]
            entry["visualClass"] = normalized_visual_class(
                bound_context.get("visualClass")
            )
        else:
            try:
                character_entry = get_character_entry(character_id)
                entry["characterName"] = str(character_entry.get("name") or "")[:160]
                entry["visualClass"] = normalized_visual_class(character_entry.get("visualClass"))
            except KeyError:
                pass
    if feature:
        entry["feature"] = str(feature)
    clean_scope = str(scope or "").strip()
    clean_job_kind = str(job_kind or "").strip()
    if clean_scope:
        if clean_scope not in {"generic", "character"}:
            raise ValueError("不支援的作品歸屬範圍")
        if clean_scope == "generic" and any((character_id, action_id, variant_id)):
            raise ValueError("通用作品不能宣稱角色歸屬")
        if clean_scope == "character" and not character_id:
            raise ValueError("角色作品缺少角色歸屬")
        entry["scope"] = clean_scope
    if clean_job_kind:
        if clean_job_kind not in {
            MINIMAX_H3_WEB_JOB_KIND,
            CLIPMODE_PHOTOSHOOT_JOB_KIND,
        }:
            raise ValueError("不支援的任務歸屬類型")
        entry["jobKind"] = clean_job_kind
        entry["featureId"] = str(feature or "")
    clean_action_id = safe_path_segment(str(action_id or ""), "")
    if clean_action_id:
        action = CHARACTER_ACTION_BY_ID.get(clean_action_id) or {}
        entry["actionId"] = clean_action_id
        entry["actionLabel"] = str(action.get("label") or clean_action_id)[:160]
    clean_variant_id = str(variant_id or "").strip()[:160]
    if clean_variant_id:
        entry["variantId"] = clean_variant_id
    with _JOB_INDEX_LOCK:
        jobs = load_job_index()
        existing = jobs.get(pid)
        if isinstance(existing, dict):
            immutable_keys = (
                "email", "characterId", "feature", "actionId", "variantId", "visualClass",
                "scope", "jobKind", "featureId",
            )
            if any(str(existing.get(key) or "") != str(entry.get(key) or "") for key in immutable_keys):
                print(f"WARNING refusing conflicting attribution for prompt {pid}")
                return
            entry["ts"] = float(existing.get("ts") or entry["ts"])
        jobs[pid] = entry
        save_job_index(jobs)


def strip_submitted_by(value):
    if isinstance(value, dict):
        protected = {"submittedBy", "characterId", "characterName", "actionId", "actionLabel"}
        return {key: strip_submitted_by(item) for key, item in value.items() if key not in protected}
    if isinstance(value, list):
        return [strip_submitted_by(item) for item in value]
    return value


def upsert_character(
    body: dict,
    character_id: str | None = None,
    server_side: bool = False,
    allow_visual_class_update: bool = False,
) -> dict:
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")
    if not server_side and any(key in body for key in ("ownerEmail", "visibility")):
        raise ValueError("ownerEmail/visibility are server-managed")
    if not server_side and "visualClass" in body and not allow_visual_class_update:
        raise ValueError("visualClass 只能由管理員分類")
    now = time.time()
    with _CHARACTER_LOCK:
        entries = load_character_index()
        by_id = {item["id"]: item for item in entries}
        if character_id:
            cid = safe_path_segment(character_id.lower().replace(" ", "-"), "character")
            if cid not in by_id:
                raise KeyError("Character not found")
            existing = by_id[cid]
            merged = dict(existing)
            character_keys = {
                "name", "kind", "type", "visualClass", "group", "tagline", "profile", "personality",
                "origin", "color", "style", "languageModel", "voice", "assets",
                "profileData", "source", "actions",
            }
            if not server_side and is_seed_owned_character(existing):
                # Official identity, classification and provider bindings are
                # seed-owned because they determine capability routing.  The
                # web editor may only change the explicit operator overrides
                # that survive an official-seed refresh.
                forbidden = sorted((set(body) & character_keys) - {"actions", "voice", "assets"})
                if forbidden:
                    raise ValueError(
                        "官方角色的身份與分類由系統管理；不可修改：" + ", ".join(forbidden)
                    )
                if "actions" in body:
                    merged["actions"] = body.get("actions")
                if "voice" in body:
                    incoming_voice = body.get("voice")
                    if not isinstance(incoming_voice, dict):
                        raise ValueError("voice must be an object")
                    unknown_voice = sorted(set(incoming_voice) - {"voiceLibraryIds", "notes"})
                    if unknown_voice:
                        raise ValueError("官方角色不允許修改這些 voice 欄位：" + ", ".join(unknown_voice))
                    next_voice = _json_copy(existing.get("voice")) if isinstance(existing.get("voice"), dict) else {}
                    for key in ("voiceLibraryIds", "notes"):
                        if key in incoming_voice:
                            value = incoming_voice[key]
                            if key == "voiceLibraryIds":
                                if not isinstance(value, list):
                                    raise ValueError("voice.voiceLibraryIds must be an array")
                                value = [str(item).strip() for item in value if str(item).strip()]
                            else:
                                value = str(value or "")
                            next_voice[key] = value
                    merged["voice"] = next_voice
                if "assets" in body:
                    incoming_assets = body.get("assets")
                    if not isinstance(incoming_assets, dict):
                        raise ValueError("assets must be an object")
                    unknown_assets = sorted(set(incoming_assets) - {"notes"})
                    if unknown_assets:
                        raise ValueError("官方角色不允許修改這些 assets 欄位：" + ", ".join(unknown_assets))
                    next_assets = _json_copy(existing.get("assets")) if isinstance(existing.get("assets"), dict) else {}
                    if "notes" in incoming_assets:
                        next_assets["notes"] = str(incoming_assets.get("notes") or "")
                    merged["assets"] = next_assets
            else:
                for key in character_keys:
                    if key not in body:
                        continue
                    if key == "assets" and not server_side:
                        incoming_assets = body.get("assets")
                        if not isinstance(incoming_assets, dict):
                            raise ValueError("assets must be an object")
                        unknown_assets = sorted(set(incoming_assets) - {"notes"})
                        if unknown_assets:
                            raise ValueError(
                                "角色資產請從唯一的角色衣櫃／素材入口管理；不可在設定修改："
                                + ", ".join(unknown_assets)
                            )
                        next_assets = _json_copy(existing.get("assets")) if isinstance(existing.get("assets"), dict) else {}
                        if "notes" in incoming_assets:
                            next_assets["notes"] = str(incoming_assets.get("notes") or "")
                        merged["assets"] = next_assets
                    else:
                        merged[key] = body[key]
            if server_side:
                for key in ("ownerEmail", "visibility"):
                    if key in body:
                        merged[key] = body[key]
            merged["id"] = cid
            merged["created"] = by_id[cid].get("created") or now
            merged["updated"] = now
            by_id[cid] = _clean_character(merged, cid)
        else:
            # Legacy ``type`` is accepted only when loading old persisted rows.
            # A newly created character needs an explicit visualClass decision;
            # otherwise it enters the catalog as unknown and cannot generate.
            create_body = dict(body)
            create_body.setdefault("visualClass", "unknown")
            clean = _clean_character(create_body)
            base = clean["id"]
            cid = base
            index = 2
            while cid in by_id:
                cid = safe_path_segment(f"{base}-{index}", "character")
                index += 1
            clean["id"] = cid
            clean["created"] = now
            clean["updated"] = now
            by_id[cid] = clean
        result = sorted(by_id.values(), key=_character_sort_key)
        save_character_index(result)
        return character_public(by_id[cid])


def delete_character(character_id: str) -> bool:
    cid = safe_path_segment(str(character_id or "").lower().replace(" ", "-"), "character")
    removed = None
    with _CHARACTER_LOCK:
        entries = load_character_index()
        removed = next((item for item in entries if item["id"] == cid), None)
        next_entries = [item for item in entries if item["id"] != cid]
        if len(next_entries) == len(entries):
            raise KeyError("Character not found")
        save_character_index(next_entries)
    if removed and is_experimental_character(removed):
        base = experimental_character_dir(removed)
        if base:
            try:
                resolved = base.resolve()
                if resolved.is_relative_to(CHARACTER_USER_ASSET_ROOT.resolve()):
                    shutil.rmtree(resolved, ignore_errors=True)
            except OSError:
                pass
    return True

POST_PLATFORMS = {
    "threads": ("Threads", "短而口語，1–2 段，可用 1–2 個 emoji，結尾加 2–3 個相關 hashtag。"),
    "instagram": ("Instagram", "首句要抓眼球，3–5 段，多用 emoji，結尾放 5–8 個 hashtag。"),
    "x": ("X（Twitter）", "280 字以內，一句話打中重點，最多 2 個 hashtag。"),
    "facebook": ("Facebook", "可稍長、有故事感，2–4 段，少量 emoji，不用太多 hashtag。"),
    "xiaohongshu": ("小紅書", "標題黨開頭＋條列重點＋大量 emoji，結尾放 8–10 個 # hashtag。"),
    "linkedin": ("LinkedIn", "專業口吻、有洞見，3–5 段，避免過度 emoji，結尾 3 個 hashtag。"),
}

POST_TONES = {
    "warm": "親切溫暖",
    "professional": "專業可信",
    "humorous": "幽默風趣",
    "heartfelt": "走心、有共鳴",
    "promo": "促銷、帶明確行動呼籲（CTA）",
}

POST_LENGTHS = {
    "short": "短：1–2 句、精簡有力，一眼就能讀完。",
    "medium": "中：約一段、完整但不冗長。",
    "long": "長：2–4 段、有鋪陳與層次。",
}


def _post_persona_system(sister: dict) -> str:
    return f"你是 {sister['name']}。{sister['voice']}\n\n{POST_NO_NAMEDROP}"


def _post_task_prompt(
    platform: str,
    tone: str,
    topic: str,
    extra: str,
    mode: str = "advanced",
    length: str = "medium",
    keywords: str = "",
) -> str:
    pf = POST_PLATFORMS.get(platform) or POST_PLATFORMS["threads"]
    ln = POST_LENGTHS.get(length) or POST_LENGTHS["medium"]
    if mode == "quick":
        lines = [
            "我給你一句話，幫我把它延伸成一則完整、好看的社群貼文。",
            f"原句：{topic}",
            f"平台：{pf[0]}",
            f"平台慣例：{pf[1]}",
            f"篇幅：{ln}",
        ]
    else:
        tn = POST_TONES.get(tone) or POST_TONES["warm"]
        lines = [
            f"幫我寫一則「{pf[0]}」社群貼文。",
            f"主題：{topic}",
            f"語氣：{tn}",
            f"篇幅：{ln}",
            f"平台慣例：{pf[1]}",
        ]
        if keywords:
            lines.append(f"盡量自然帶到這些關鍵字：{keywords}")
    if extra:
        lines.append(f"額外要求：{extra}")
    lines.append("用繁體中文。直接輸出可以貼上去的貼文內容本身，不要加引號、不要任何解釋或標題說明。")
    return "\n".join(lines)


def _post_via_gemini(
    system: str,
    user: str,
    inline_images: tuple[dict, ...] = (),
    *,
    temperature: float = 1.0,
    max_output_tokens: int = 1024,
) -> str:
    key = _lab_secret("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no gemini key")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(POST_GEMINI_MODEL)}:generateContent?key={urllib.parse.quote(key)}"
    )
    parts: list[dict] = [dict(item) for item in inline_images]
    parts.append({"text": user})
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            # Gemini 2.5 Flash defaults to dynamic thinking.  For a short
            # copywriting task it can consume the output budget on hidden
            # reasoning and return half a sentence, so disable thinking.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read() or b"{}")
    cands = data.get("candidates") or []
    finish_reason = str(cands[0].get("finishReason") or "") if cands else ""
    if finish_reason and finish_reason != "STOP":
        raise RuntimeError(f"gemini response incomplete: {finish_reason}")
    parts = (cands[0].get("content", {}).get("parts") if cands else []) or []
    return "".join(p.get("text", "") for p in parts).strip()


_CLIPMODE_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _clipmode_prompt_needs_translation(*values: object) -> bool:
    return any(_CLIPMODE_CJK_RE.search(str(value or "")) for value in values)


def _clipmode_translation_json(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("中文提示詞翻譯服務回傳格式無效") from error
    if not isinstance(value, dict) or set(value) != {"positive", "negative"}:
        raise ValueError("中文提示詞翻譯服務回傳格式無效")
    translated = {
        key: str(value.get(key) or "").strip()
        for key in ("positive", "negative")
    }
    if (
        not translated["positive"]
        or any(len(translated[key]) > 4_000 for key in translated)
        or _clipmode_prompt_needs_translation(*translated.values())
    ):
        raise ValueError("中文提示詞翻譯服務未產生可用英文提示詞")
    return translated


def prepare_clipmode_photoshoot_effective_prompts(settings: dict) -> dict:
    """Bind a literal English SDXL prompt while retaining the user's text."""

    result = dict(settings)
    positive = str(result.get("prompt") or "").strip()
    negative = str(result.get("negative_prompt") or "").strip()
    if not positive:
        raise ValueError("請輸入角色寫真的場景描述")
    if not _clipmode_prompt_needs_translation(positive, negative):
        result["_effectivePrompt"] = positive
        result["_effectiveNegativePrompt"] = negative
        return result
    system = (
        "You are a literal translation component for an SDXL image prompt. "
        "Translate the supplied positive and negative prompts into concise "
        "natural English. Preserve every requested adult action, pose, place, "
        "camera framing, clothing, mood, and visual style. Do not omit, soften, "
        "censor, moralize, or invent details. If age is unspecified, describe "
        "the person as an adult, never as young. Return exactly one JSON object "
        "with string keys positive and negative and no markdown."
    )
    payload = json.dumps(
        {"positive": positive, "negative": negative},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        translated = _clipmode_translation_json(_post_via_gemini(
            system,
            payload,
            temperature=0.0,
            max_output_tokens=512,
        ))
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(
            "中文提示詞翻譯暫時失敗；這次沒有送出圖片生成，請稍後重試"
        ) from error
    result["_effectivePrompt"] = translated["positive"]
    result["_effectiveNegativePrompt"] = translated["negative"]
    return result


def _post_via_openai(system: str, user: str) -> str:
    key = _lab_secret("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("no openai key")
    body = json.dumps({
        "model": POST_OPENAI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 1.0,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read() or b"{}")
    choice = data["choices"][0]
    finish_reason = str(choice.get("finish_reason") or "")
    if finish_reason and finish_reason != "stop":
        raise RuntimeError(f"openai response incomplete: {finish_reason}")
    return (choice["message"]["content"] or "").strip()


def _post_via_claude_cli(system: str, user: str) -> str:
    proc = subprocess.run(
        [CLAUDE_CLI, "-p", f"{system}\n\n{user}"],
        capture_output=True, text=True, timeout=180, cwd="/tmp",
    )
    if proc.returncode != 0:
        raise RuntimeError("claude CLI failed")
    return proc.stdout.strip()


def generate_post(
    sister_id: str,
    platform: str,
    tone: str,
    topic: str,
    extra: str,
    mode: str = "advanced",
    length: str = "medium",
    keywords: str = "",
    *,
    exact_backend: bool = False,
) -> dict:
    sister = POST_SISTERS.get(sister_id)
    if sister is None:
        raise ValueError(f"不支援的貼文角色模組：{sister_id or '空白'}")
    system = _post_persona_system(sister)
    user = _post_task_prompt(platform, tone, topic, extra, mode, length, keywords)
    backend = sister["backend"]
    try:
        if backend == "gemini":
            if not _lab_secret("GEMINI_API_KEY"):
                raise RuntimeError("no gemini key")
            text, engine = _post_via_gemini(system, user), "Gemini API"
        elif backend == "openai":
            if not _lab_secret("OPENAI_API_KEY"):
                raise RuntimeError("no openai key")
            text, engine = _post_via_openai(system, user), "OpenAI API"
        else:
            text, engine = _post_via_claude_cli(system, user), "Claude（本機）"
    except Exception:
        # A selected module remains the selected module.  Falling back to
        # Claude would silently change the character/provider contract.
        raise
    if not text:
        raise ValueError("生成結果為空，請再試一次")
    return {"text": text, "engine": engine, "sister": sister["name"]}


ASSIST_KIND_GUIDE = {
    "image": "AI 圖片生成（SDXL／Flux／Z-Image）",
    "video": "AI 影片生成（Wan 2.1）",
    "default": "AI 生成",
}


def _assist_system(kind: str, has_references: bool = False) -> str:
    if kind == "post":
        return (
            "你是社群貼文的點子助手。幫使用者想出可以直接拿去發揮的貼文主題或一句話開頭："
            "貼近生活、具體、有記憶點，能勾起讀者興趣。"
            "用繁體中文。直接輸出主題本身，不要編號、引號或任何解釋。"
        )
    guide = ASSIST_KIND_GUIDE.get(kind, ASSIST_KIND_GUIDE["default"])
    reference_contract = (
        "\n你會同時收到實際要拿去生成的參考圖片。先辨識圖片中的主體、服裝、場景與構圖，"
        "再只描述合理的動作與鏡頭變化。除非使用者明確要求，不得替換人物、物種、服裝、"
        "背景或新增圖片裡沒有的主角；多張圖依輸入順序視為首格、尾格或額外角色參考。"
        if has_references else ""
    )
    return (
        f"你是一個 {guide} 的提示詞助手。產出可以直接貼進生成器的提示詞："
        "具體、有畫面感，包含主體／動作／場景／光線／鏡頭／風格／畫質。"
        "以英文為主。直接輸出提示詞本身，不要編號、引號或任何解釋。"
        f"{reference_contract}"
    )


def _assist_user(mode: str, text: str, kind: str = "default") -> str:
    text = (text or "").strip()
    if kind == "post":
        if mode == "random":
            return "隨機幫我想一個有意思、能引起共鳴的社群貼文主題。"
        if mode == "enhance":
            if not text:
                raise ValueError("先輸入一點想法，我再幫你擴寫")
            return f"把這個想法擴寫成更完整、更有畫面感的貼文主題，保留原意：\n{text}"
        # brainstorm
        if not text:
            return "幫我想一個好發揮的社群貼文主題，挑一個吸睛的方向。"
        return f"我大概想講：{text}\n幫我整理成一個清楚、好寫的貼文主題。"
    if mode == "random":
        return "隨機發想一個有創意、視覺豐富的提示詞。"
    if mode == "enhance":
        if not text:
            raise ValueError("先輸入一點描述，我再幫你加強")
        return (
            "把這段描述擴寫得更具體，補上光線／鏡頭／風格／畫質細節，保留原意：\n"
            f"{text}"
        )
    # brainstorm
    if not text:
        return "幫我想一個好看的提示詞，挑一個吸睛的方向。"
    return f"我大概想要：{text}\n幫我變成一段完整、好看的提示詞。"


def _llm_complete(
    system: str,
    user: str,
    inline_images: tuple[dict, ...] = (),
) -> tuple[str, str]:
    """Best-available LLM for the assist feature: cloud key if present, else local CLI."""
    if inline_images:
        if not _lab_secret("GEMINI_API_KEY"):
            raise ValueError("已選參考圖，但 Gemini 多模態尚未設定；這次沒有改寫提示詞")
        return _post_via_gemini(system, user, inline_images), "Gemini 多模態"
    try:
        if _lab_secret("GEMINI_API_KEY"):
            return _post_via_gemini(system, user), "Gemini API"
        if _lab_secret("OPENAI_API_KEY"):
            return _post_via_openai(system, user), "OpenAI API"
    except Exception:
        pass
    return _post_via_claude_cli(system, user), "Claude（本機）"


def _assist_reference_parts(references: object) -> tuple[dict, ...]:
    if references in (None, []):
        return ()
    if not isinstance(references, list) or len(references) > ASSIST_MAX_REFERENCES:
        raise ValueError(f"參考圖最多 {ASSIST_MAX_REFERENCES} 張")
    parts: list[dict] = []
    total_bytes = 0
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise ValueError("參考圖資料格式不正確")
        data_url = str(reference.get("dataUrl") or "")
        server_file = str(reference.get("serverFile") or "")
        if bool(data_url) == bool(server_file):
            raise ValueError("每張參考圖必須只指定一個來源")
        if data_url:
            raw, declared_type = decode_data_url(data_url, ASSIST_MAX_REFERENCE_BYTES)
        else:
            bare = resolve_server_file(server_file)
            path = (COMFY_INPUT / bare).resolve()
            size = path.stat().st_size
            if size > ASSIST_MAX_REFERENCE_BYTES:
                raise RequestTooLarge("參考圖檔案過大")
            raw = path.read_bytes()
            declared_type = mimetypes.guess_type(path.name)[0] or ""
        detected_type, _extension = sniff_experimental_image(raw)
        normalized_declared = "image/jpeg" if declared_type == "image/jpg" else declared_type
        if normalized_declared and normalized_declared != detected_type:
            raise ValueError(f"第 {index + 1} 張參考圖的格式與內容不符")
        total_bytes += len(raw)
        if total_bytes > ASSIST_MAX_TOTAL_REFERENCE_BYTES:
            raise RequestTooLarge("參考圖總大小過大")
        parts.append({
            "inline_data": {
                "mime_type": detected_type,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        })
    return tuple(parts)


def generate_assist(
    mode: str,
    kind: str,
    text: str,
    references: object = None,
) -> dict:
    inline_images = _assist_reference_parts(references)
    out, engine = _llm_complete(
        _assist_system(kind, bool(inline_images)),
        _assist_user(mode, text, kind),
        inline_images,
    )
    out = (out or "").strip()
    if not out:
        raise ValueError("沒有產生內容，再試一次")
    return {"text": out, "engine": engine, "referenceCount": len(inline_images)}


_MINIMAX_H3_SEED_DECIMAL_RE = re.compile(r"(?:-1|0|[1-9][0-9]{0,19})\Z")


def parse_minimax_h3_seed(value: object) -> int:
    """Parse one lossless browser seed and enforce H3's uint64 boundary."""

    if type(value) is int:
        seed = value
    elif type(value) is str and _MINIMAX_H3_SEED_DECIMAL_RE.fullmatch(value):
        seed = int(value)
    else:
        raise ValueError("MiniMax H3 種子必須是 -1 或 uint64 十進位整數")
    if seed < -1 or seed > H3_SEED_MAX:
        raise ValueError("MiniMax H3 種子超出 uint64 範圍")
    return seed


def minimax_h3_product_prompt(feature_id: str, settings: dict, uploads: dict[str, str] | None = None) -> dict:
    resolved = dict(settings) if isinstance(settings, dict) else {}
    seed = parse_minimax_h3_seed(resolved.get("seed", "-1"))
    resolved["seed"] = random.randint(0, H3_SEED_MAX) if seed == -1 else seed
    visual_class = str(resolved.pop("_characterVisualClass", "") or "").strip().lower()
    if feature_id == "h3-text-video":
        visual_class = ""
    token = "_".join((
        safe_path_segment(str(resolved.get("_characterId") or "generic"), "generic"),
        safe_path_segment(str(resolved.get("_characterActionId") or feature_id), feature_id),
        time.strftime("%Y%m%d_%H%M%S"),
        secrets.token_hex(2),
    ))
    try:
        return compile_minimax_h3_product_prompt(
            feature_id=feature_id,
            visual_class=visual_class or None,
            settings=resolved,
            uploads=uploads or {},
            output_token=token,
        )
    except MiniMaxH3ProductError as error:
        raise ValueError(str(error)) from error


def minimax_h3_t2v_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    return minimax_h3_product_prompt("h3-text-video", settings, uploads)


def minimax_h3_i2v_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    return minimax_h3_product_prompt("h3-image-video", settings, uploads)


def minimax_h3_ref2va_image_prompt(settings: dict, uploads: dict[str, str] | None = None) -> dict:
    return minimax_h3_product_prompt("h3-reference-video", settings, uploads)


def _prepare_minimax_h3_web_state_root() -> Path:
    """Create the owner-only state directory before exposing a cancellable job."""

    root = H3_WEB_STATE_ROOT
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lexical = root.lstat()
        if root.is_symlink() or not root.is_dir() or lexical.st_uid != os.geteuid():
            raise ValueError("MiniMax H3 任務狀態目錄不安全")
        root.chmod(0o700)
        return root.resolve(strict=True)
    except OSError as error:
        raise ValueError("MiniMax H3 任務狀態目錄無法使用") from error


def _signal_minimax_h3_web_cancel(path_value: str) -> None:
    root = _prepare_minimax_h3_web_state_root()
    candidate = Path(path_value)
    if (
        candidate.parent != root
        or re.fullmatch(
            r"external-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.cancel",
            candidate.name,
            re.IGNORECASE,
        ) is None
    ):
        raise ValueError("MiniMax H3 取消路徑無效")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(candidate, flags, 0o600)
    except FileExistsError:
        try:
            info = candidate.lstat()
        except OSError as error:
            raise ValueError("MiniMax H3 取消訊號無法確認") from error
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or (info.st_mode & 0o777) != 0o600
        ):
            raise ValueError("MiniMax H3 取消訊號不安全")
        return
    except OSError as error:
        raise ValueError("MiniMax H3 取消訊號無法建立") from error
    try:
        os.write(descriptor, b'{"cancel":true}\n')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _minimax_h3_sidecar(
    *,
    job_id: str,
    feature_id: str,
    receipt: dict,
    submitted_by: dict | None,
    created: float,
    settings: dict,
    graph: dict,
) -> Path:
    output = receipt.get("output") if isinstance(receipt.get("output"), dict) else {}
    output_path = Path(str(output.get("path") or ""))
    try:
        resolved_output = output_path.resolve(strict=True)
        relative = resolved_output.relative_to(COMFY_OUTPUT.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ValueError("MiniMax H3 完成影片不在作品庫內") from error
    filename = relative.name
    subfolder = "" if relative.parent == Path(".") else relative.parent.as_posix()
    if (
        output.get("filename") != filename
        or output.get("subfolder") != subfolder
        or output.get("type") != "output"
    ):
        raise ValueError("MiniMax H3 完成影片資訊不一致")
    if (
        resolved_output.stat().st_size != int(output.get("bytes") or 0)
        or content_sha256_cached(resolved_output) != str(output.get("sha256") or "")
    ):
        raise ValueError("MiniMax H3 完成影片驗證值已改變")
    if str(receipt.get("prompt_id") or "") != job_id:
        raise ValueError("MiniMax H3 完成影片任務編號不一致")
    feature = FEATURE_BY_ID.get(feature_id) or {}
    workflow = f"castle-{feature_id}-web"
    submitted_email = normalize_email(str((submitted_by or {}).get("email") or ""))
    if not submitted_email:
        raise ValueError("MiniMax H3 作品缺少有效擁有者")
    with _JOB_INDEX_LOCK:
        indexed = load_job_index().get(job_id)
    if (
        not isinstance(indexed, dict)
        or normalize_email(str(indexed.get("email") or "")) != submitted_email
        or str(indexed.get("featureId") or "") != feature_id
        or indexed.get("jobKind") != MINIMAX_H3_WEB_JOB_KIND
    ):
        raise ValueError("MiniMax H3 作品歸屬索引不一致")
    sidecar_data = {
        "version": 2,
        "promptId": job_id,
        "workflow": workflow,
        "feature": str(feature.get("title") or feature_id),
        "status": "completed",
        "created": int(created),
        "submittedBy": {
            "email": submitted_email,
            "displayName": str((submitted_by or {}).get("displayName") or submitted_email),
        },
        "settings": {
            "aspect": str(settings.get("aspect") or ""),
            "frames": int(receipt.get("frames") or 0),
            "acceleration": str(settings.get("acceleration") or "stock"),
            "mode": str(receipt.get("mode") or ""),
        },
        "summary": {
            "width": int((receipt.get("resolution") or {}).get("width") or 0),
            "height": int((receipt.get("resolution") or {}).get("height") or 0),
            "fps": float(receipt.get("fps") or 0),
            "bytes": int(output.get("bytes") or 0),
            "sha256": str(output.get("sha256") or ""),
            "memoryCleanup": receipt.get("memory_cleanup") or {},
        },
        "detailNodes": [
            {"id": "minimax-h3", "type": "MiniMax H3", "inputs": []},
        ],
        "detailParameters": history_generation_parameters(graph, workflow),
        "outputs": [{
            "kind": "video",
            "filename": filename,
            "subfolder": subfolder,
            "type": "output",
        }],
    }
    sidecar_path = direct_sidecar_path(resolved_output)
    with _GALLERY_MUTATION_LOCK:
        sidecar_exists = sidecar_path.exists()
        existing = load_direct_sidecar(resolved_output)
        if sidecar_exists and existing is None:
            raise ValueError("MiniMax H3 作品 sidecar 無法驗證")
        raw_provenance = existing.get("provenance") if isinstance(existing, dict) else None
        provenance_receipt = (
            verify_output_provenance_receipt(resolved_output, raw_provenance)
            if raw_provenance is not None
            else None
        )
        if raw_provenance is not None and provenance_receipt is None:
            raise ValueError("MiniMax H3 作品既有簽章無效")
        scope = str(indexed.get("scope") or "")
        expected_schema = (
            MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA
            if scope == "generic"
            else OUTPUT_PROVENANCE_SCHEMA
        )
        if provenance_receipt is not None and (
            provenance_receipt.get("schema") != expected_schema
            or provenance_receipt.get("promptId") != job_id
            or provenance_receipt.get("ownerEmail") != submitted_email
            or provenance_receipt.get("featureId") != feature_id
        ):
            raise ValueError("MiniMax H3 作品既有簽章歸屬不一致")
        if provenance_receipt is None:
            if scope == "generic":
                provenance_receipt = issue_minimax_h3_output_provenance_receipt(
                    resolved_output,
                    job_id,
                    indexed,
                    feature_id,
                    receipt,
                )
            elif scope == "character":
                provenance_receipt = issue_output_provenance_receipt(
                    resolved_output,
                    job_id,
                    indexed,
                )
            else:
                raise ValueError("MiniMax H3 作品歸屬範圍無效")
        verified = verify_output_provenance_receipt(resolved_output, provenance_receipt)
        if verified is None:
            raise ValueError("MiniMax H3 作品歸屬簽章無法建立")
        projection = output_provenance_projection(verified)
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(sidecar_data)
        merged["provenance"] = provenance_receipt
        if projection.get("characterAttribution") == "server":
            merged["character"] = _json_copy(projection["character"])
            merged["action"] = _json_copy(projection["action"])
            merged["variantId"] = projection["variantId"]
            merged["visualClass"] = projection["visualClass"]
        else:
            for key in ("character", "action", "variantId", "visualClass"):
                merged.pop(key, None)
        atomic_write_json(sidecar_path, merged, 0o600)
    return sidecar_path


def run_minimax_h3_web_job(
    job_id: str,
    graph: dict,
    feature_id: str,
    settings: dict,
    submitted_by: dict | None,
    state_root: Path,
    cancel_file: Path,
    created: float,
) -> None:
    try:
        with _LOCAL_JOB_LOCK:
            job = LOCAL_JOBS.get(job_id)
            if not job:
                return
            if job.get("state") in ("cancel_requested", "cancelled"):
                job["state"] = "cancelled"
                job["progress"] = {"pct": 100, "label": "MiniMax H3 已取消"}
                job["updated"] = time.time()
                return
            if job.get("state") != "pending":
                return
            job["state"] = "running"
            job["progress"] = {"pct": 5, "label": "H3 正在排隊、整理記憶體並生成"}
            job["updated"] = time.time()
        receipt = execute_minimax_h3_web_graph(
            graph,
            job_id,
            state_root,
            GPU_QUEUE_SCRIPT,
            H3_WEB_WORKER_SCRIPT,
            comfy_dir=H3_WEB_COMFY_DIR,
            cancel_file=cancel_file,
            timeout_seconds=H3_WEB_TIMEOUT_SECONDS,
        )
        sidecar = _minimax_h3_sidecar(
            job_id=job_id,
            feature_id=feature_id,
            receipt=receipt,
            submitted_by=submitted_by,
            created=created,
            settings=settings,
            graph=graph,
        )
        output = receipt.get("output") if isinstance(receipt.get("output"), dict) else {}
        with _LOCAL_JOB_LOCK:
            job = LOCAL_JOBS.get(job_id)
            if job:
                job.update({
                    "state": "completed",
                    "outputCount": 1,
                    "output": str(output.get("path") or ""),
                    "sidecar": str(sidecar),
                    "progress": {"pct": 100, "label": "MiniMax H3 已完成"},
                    "updated": time.time(),
                })
    except WebGraphExecutionError as error:
        with _LOCAL_JOB_LOCK:
            cancelled = bool((LOCAL_JOBS.get(job_id) or {}).get("state") in ("cancel_requested", "cancelled"))
        if cancelled:
            update_local_job(
                job_id,
                state="cancelled",
                progress={"pct": 100, "label": "MiniMax H3 已取消"},
            )
        else:
            update_local_job(
                job_id,
                state="error",
                error=f"{error.code}: {error}",
                progress={"pct": 100, "label": "MiniMax H3 生成失敗"},
            )
    except Exception as error:
        with _LOCAL_JOB_LOCK:
            cancelled = bool((LOCAL_JOBS.get(job_id) or {}).get("state") in ("cancel_requested", "cancelled"))
        if cancelled:
            update_local_job(
                job_id,
                state="cancelled",
                progress={"pct": 100, "label": "MiniMax H3 已取消"},
            )
        else:
            update_local_job(
                job_id,
                state="error",
                error=str(error)[:240],
                progress={"pct": 100, "label": "MiniMax H3 生成失敗"},
            )
    finally:
        try:
            cancel_file.unlink()
        except FileNotFoundError:
            pass


def start_minimax_h3_web_job(
    graph: dict,
    feature_id: str,
    settings: dict,
    submitted_by: dict | None,
    character_id: str = "",
    action_id: str = "",
    variant_id: str = "",
) -> dict:
    state_root = _prepare_minimax_h3_web_state_root()
    if not GPU_QUEUE_SCRIPT.is_file():
        raise ValueError(f"找不到 GPU queue script：{GPU_QUEUE_SCRIPT}")
    if not H3_WEB_WORKER_SCRIPT.is_file():
        raise ValueError(f"找不到 MiniMax H3 Web worker：{H3_WEB_WORKER_SCRIPT}")
    job_id = str(uuid.uuid4())
    cancel_file = state_root / f"external-{job_id}.cancel"
    created = time.time()
    feature = FEATURE_BY_ID.get(feature_id) or {}
    job = {
        "promptId": job_id,
        "state": "pending",
        "position": 1,
        "workflow": f"castle-{feature_id}-web",
        "feature": str(feature.get("title") or feature_id),
        "source": "castle",
        "jobKind": "minimax_h3_web",
        "submittedBy": {
            "email": normalize_email(str((submitted_by or {}).get("email") or "")),
            "displayName": str((submitted_by or {}).get("displayName") or ""),
        },
        "nodeCount": len(graph),
        "outputCount": 0,
        "outputNodes": ["SaveVideo"],
        "nodeTypes": {
            str(node_id): str(node.get("class_type") or "")
            for node_id, node in graph.items()
            if isinstance(node, dict)
        },
        "progress": {"pct": 0, "label": "H3 正在排隊並準備記憶體"},
        "created": created,
        "updated": created,
        "cancelFile": str(cancel_file),
    }
    record_job_attribution(
        job_id,
        submitted_by,
        character_id,
        feature_id,
        action_id,
        variant_id,
        scope="character" if character_id else "generic",
        job_kind=MINIMAX_H3_WEB_JOB_KIND,
    )
    with _LOCAL_JOB_LOCK:
        LOCAL_JOBS[job_id] = job
    thread = threading.Thread(
        target=run_minimax_h3_web_job,
        args=(
            job_id,
            graph,
            feature_id,
            dict(settings),
            dict(submitted_by) if isinstance(submitted_by, dict) else None,
            state_root,
            cancel_file,
            created,
        ),
        name=f"castle-h3-web-{job_id[:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        with _LOCAL_JOB_LOCK:
            LOCAL_JOBS.pop(job_id, None)
        raise
    return {"ok": True, "prompt_id": job_id, "local": True, "queued": True}


def _clipmode_photoshoot_canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("角色身份寫真任務資料無法序列化") from error


def _clipmode_photoshoot_json_sha256(value: object) -> str:
    return hashlib.sha256(_clipmode_photoshoot_canonical_json(value)).hexdigest()


def _clipmode_photoshoot_worker_hmac(payload: dict, token: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", str(token or "")) is None:
        raise ValueError("角色身份寫真 worker token 無效")
    unsigned = {
        key: value for key, value in payload.items()
        if key != "worker_hmac_sha256"
    }
    return hmac.new(
        token.encode("ascii"),
        _clipmode_photoshoot_canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()


def _prepare_clipmode_photoshoot_state_root() -> Path:
    """Return one owner-only, non-repository worker state directory."""

    root = CLIPMODE_PHOTOSHOOT_STATE_ROOT
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lexical = root.lstat()
        resolved = root.resolve(strict=True)
        if (
            root.is_symlink()
            or not stat.S_ISDIR(lexical.st_mode)
            or lexical.st_uid != os.geteuid()
            or resolved.is_relative_to(ROOT.parent.resolve(strict=True))
        ):
            raise ValueError("角色身份寫真任務狀態目錄不安全")
        root.chmod(0o700)
        if stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise ValueError("角色身份寫真任務狀態目錄不是 owner-only")
        return resolved
    except OSError as error:
        raise ValueError("角色身份寫真任務狀態目錄無法使用") from error


def _write_clipmode_photoshoot_state(path: Path, payload: dict) -> None:
    raw = _clipmode_photoshoot_canonical_json(payload) + b"\n"
    if not 0 < len(raw) <= CLIPMODE_PHOTOSHOOT_MAX_STATE_BYTES:
        raise ValueError("角色身份寫真任務資料過大")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ValueError("角色身份寫真任務資料無法建立") from error
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_clipmode_photoshoot_state(path: Path) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("角色身份寫真任務資料無法讀取") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 < before.st_size <= CLIPMODE_PHOTOSHOOT_MAX_STATE_BYTES
        ):
            raise ValueError("角色身份寫真任務資料不安全")
        chunks: list[bytes] = []
        remaining = CLIPMODE_PHOTOSHOOT_MAX_STATE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        raw = b"".join(chunks)
        if (
            len(raw) != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise ValueError("角色身份寫真任務資料在讀取時變更")
    finally:
        os.close(descriptor)

    def reject_duplicates(pairs):
        decoded = {}
        for key, value in pairs:
            if key in decoded:
                raise ValueError("duplicate key")
            decoded[key] = value
        return decoded

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("角色身份寫真任務資料格式無效") from error
    if not isinstance(value, dict):
        raise ValueError("角色身份寫真任務資料格式無效")
    return value


def _clipmode_photoshoot_state_path(
    value: object,
    root: Path,
    pattern: str,
    label: str,
) -> Path:
    candidate = Path(str(value or ""))
    if candidate.parent != root or re.fullmatch(pattern, candidate.name, re.IGNORECASE) is None:
        raise ValueError(f"角色身份寫真{label}路徑無效")
    return candidate


def _signal_clipmode_photoshoot_cancel(path_value: str) -> None:
    root = _prepare_clipmode_photoshoot_state_root()
    candidate = _clipmode_photoshoot_state_path(
        path_value,
        root,
        r"cancel-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.cancel",
        "取消訊號",
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(candidate, flags, 0o600)
    except FileExistsError:
        info = candidate.lstat()
        if (
            candidate.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("角色身份寫真取消訊號不安全")
        return
    except OSError as error:
        raise ValueError("角色身份寫真取消訊號無法建立") from error
    try:
        os.write(descriptor, b'{"cancel":true}\n')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _clipmode_photoshoot_native_runner():
    release_root = ROOT.parent
    if str(release_root) not in sys.path:
        sys.path.insert(0, str(release_root))
    from scripts.minimax_h3 import run_native as native_runner
    return native_runner


def _clipmode_photoshoot_graph_route(graph: dict) -> str:
    """Return the one exact fixed graph contract represented by ``graph``."""

    if not isinstance(graph, dict):
        raise ValueError("角色身份寫真 graph 格式無效")
    node_types = {
        node_id: str(node.get("class_type") or "")
        for node_id, node in graph.items()
        if isinstance(node_id, str) and isinstance(node, dict)
    }
    realistic = {
        "1": "CheckpointLoaderSimple",
        "2": "LoadImage",
        "3": "InstantIDModelLoader",
        "4": "InstantIDFaceAnalysis",
        "5": "ControlNetLoader",
        "6": "CLIPTextEncode",
        "7": "CLIPTextEncode",
        "8": "ApplyInstantIDAdvanced",
        "9": "EmptyLatentImage",
        "10": "KSampler",
        "11": "VAEDecode",
        "12": "ReActorFaceSwap",
        "13": "SaveImage",
    }
    anime = {
        "1": "CheckpointLoaderSimple",
        "2": "LoraLoaderModelOnly",
        "3": "CLIPTextEncode",
        "4": "CLIPTextEncode",
        "5": "EmptyLatentImage",
        "6": "KSampler",
        "7": "VAEDecode",
        "8": "SaveImage",
    }
    if node_types == realistic and len(graph) == len(realistic):
        return CLIPMODE_REALISTIC_IDENTITY_ROUTE
    if node_types == anime and len(graph) == len(anime):
        return CLIPMODE_ANIME_IDENTITY_ROUTE
    raise ValueError("角色身份寫真 graph 已離開固定身份路線")


def _clipmode_photoshoot_node_id(graph: dict, class_type: str) -> str:
    _clipmode_photoshoot_graph_route(graph)
    matches = [
        node_id for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]
    if len(matches) != 1:
        raise ValueError(f"角色身份寫真 graph 缺少唯一 {class_type} 節點")
    return matches[0]


def _verify_clipmode_photoshoot_object_info(object_info: dict, graph: dict) -> None:
    identity_route = _clipmode_photoshoot_graph_route(graph)
    expected_classes = {
        "CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage",
        "KSampler", "VAEDecode", "SaveImage",
    } | (
        {
            "LoadImage", "InstantIDModelLoader", "InstantIDFaceAnalysis",
            "ControlNetLoader", "ApplyInstantIDAdvanced", "ReActorFaceSwap",
        }
        if identity_route == CLIPMODE_REALISTIC_IDENTITY_ROUTE
        else {"LoraLoaderModelOnly"}
    )
    actual_classes = {
        str(node.get("class_type") or "")
        for node in graph.values()
        if isinstance(node, dict)
    }
    if actual_classes != expected_classes:
        raise ValueError("角色身份寫真 graph 已離開固定身份路線")
    missing = sorted(name for name in expected_classes if name not in object_info)
    if missing:
        raise ValueError("ComfyUI 缺少角色身份寫真節點：" + ", ".join(missing))
    bindings = [("CheckpointLoaderSimple", "ckpt_name", CLIPMODE_SDXL_BASE_MODEL_NAME)]
    if identity_route == CLIPMODE_REALISTIC_IDENTITY_ROUTE:
        bindings.extend([
            ("LoadImage", "image", graph["2"]["inputs"]["image"]),
            (
                "InstantIDModelLoader",
                "instantid_file",
                CLIPMODE_INSTANTID_MODEL_NAME,
            ),
            (
                "ControlNetLoader",
                "control_net_name",
                CLIPMODE_INSTANTID_CONTROLNET_MODEL_NAME,
            ),
            ("ReActorFaceSwap", "swap_model", CLIPMODE_REACTOR_MODEL_NAME),
            (
                "ReActorFaceSwap",
                "face_restore_model",
                CLIPMODE_REACTOR_RESTORE_MODEL_NAME,
            ),
        ])
    else:
        bindings.append(
            ("LoraLoaderModelOnly", "lora_name", graph["2"]["inputs"]["lora_name"]),
        )
    for class_name, field_name, expected in bindings:
        try:
            options = object_info[class_name]["input"]["required"][field_name][0]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("ComfyUI 模型索引格式無效") from error
        if not isinstance(options, list) or expected not in options:
            raise ValueError(f"ComfyUI 尚未索引角色身份寫真資源：{expected}")
    if identity_route == CLIPMODE_REALISTIC_IDENTITY_ROUTE:
        try:
            providers = object_info["InstantIDFaceAnalysis"]["input"]["required"]["provider"][0]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("ComfyUI InstantID provider 索引格式無效") from error
        if not isinstance(providers, list) or "CPU" not in providers:
            raise ValueError("ComfyUI InstantID 尚未提供 CPU 臉部分析")


def _wait_for_clipmode_photoshoot_comfy_idle(
    native_runner,
    base_url: str,
    cancel_file: Path,
) -> None:
    """Wait under the shared flock until non-cooperating Comfy work finishes."""

    announced = False
    while True:
        if cancel_file.exists():
            raise ValueError("角色身份寫真已取消")
        queue = native_runner.request_json(base_url, "/queue", timeout=10.0)
        try:
            native_runner.require_queue_idle(queue)
            return
        except Exception as error:
            if getattr(error, "code", "") != "comfy_queue_busy":
                raise
        if not announced:
            print(
                'CASTLE_PROGRESS {"pct":3,"label":"等待目前的 ComfyUI 任務完成"}',
                flush=True,
            )
            announced = True
        time.sleep(2.0)


def _validate_clipmode_photoshoot_output(
    history_item: dict,
    graph: dict,
) -> dict:
    save_id = _clipmode_photoshoot_node_id(graph, "SaveImage")
    try:
        image_items = history_item["outputs"][save_id]["images"]
        prefix = str(graph[save_id]["inputs"]["filename_prefix"])
    except (KeyError, TypeError) as error:
        raise ValueError("角色身份寫真完成資料缺少圖片") from error
    if not isinstance(image_items, list) or len(image_items) != 1 or not isinstance(image_items[0], dict):
        raise ValueError("角色身份寫真必須只產生一張圖片")
    item = image_items[0]
    filename = str(item.get("filename") or "")
    subfolder = str(item.get("subfolder") or "")
    output_type = str(item.get("type") or "")
    expected_subfolder, separator, expected_stem = prefix.rpartition("/")
    if not separator or output_type != "output" or subfolder != expected_subfolder:
        raise ValueError("角色身份寫真輸出位置不一致")
    if re.fullmatch(re.escape(expected_stem) + r"_[0-9]{5}_\.png", filename) is None:
        raise ValueError("角色身份寫真輸出檔名不一致")
    output_root = COMFY_OUTPUT.resolve(strict=True)
    try:
        output_path = (output_root / subfolder / filename).resolve(strict=True)
        info = output_path.lstat()
    except OSError as error:
        raise ValueError("角色身份寫真輸出檔案不存在") from error
    if (
        not output_path.is_relative_to(output_root)
        or output_path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_size <= 0
    ):
        raise ValueError("角色身份寫真輸出檔案無效")
    return {
        "path": str(output_path),
        "filename": filename,
        "subfolder": subfolder,
        "type": output_type,
        "bytes": info.st_size,
        "sha256": content_sha256_cached(output_path),
    }


def _clipmode_photoshoot_attempt_seed(base_seed: int, attempt_index: int) -> int:
    if type(base_seed) is not int or not 0 <= base_seed <= 2_147_483_647:
        raise ValueError("角色身份寫真 seed 無效")
    if type(attempt_index) is not int or not 0 <= attempt_index < CLIPMODE_PHOTOSHOOT_MAX_ATTEMPTS:
        raise ValueError("角色身份寫真嘗試次數無效")
    return (base_seed + (attempt_index * 104_729)) % 2_147_483_648


def _clipmode_photoshoot_attempt_graph(
    graph: dict,
    job_id: str,
    attempt_index: int,
) -> tuple[dict, int, str]:
    if _clipmode_photoshoot_graph_route(graph) != CLIPMODE_REALISTIC_IDENTITY_ROUTE:
        raise ValueError("角色身份寫真品質重試只適用寫實身份路線")
    try:
        logical_job_id = str(uuid.UUID(job_id, version=4))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("角色身份寫真任務編號無效") from error
    if logical_job_id != job_id:
        raise ValueError("角色身份寫真任務編號無效")
    sampler_id = _clipmode_photoshoot_node_id(graph, "KSampler")
    save_id = _clipmode_photoshoot_node_id(graph, "SaveImage")
    base_seed = graph[sampler_id]["inputs"].get("seed")
    seed = _clipmode_photoshoot_attempt_seed(base_seed, attempt_index)
    # JSON round-tripping is also the canonical graph hashing representation;
    # it avoids sharing mutable nested input dictionaries between attempts.
    attempt_graph = json.loads(json.dumps(graph, ensure_ascii=False))
    attempt_graph[sampler_id]["inputs"]["seed"] = seed
    attempt_graph[save_id]["inputs"]["filename_prefix"] = (
        f"Castle/{CLIPMODE_PHOTOSHOOT_STAGING_FOLDER}/"
        f"{job_id}/attempt-{attempt_index + 1}"
    )
    attempt_prompt_id = str(uuid.uuid5(
        uuid.UUID(job_id), f"clipmode-quality-attempt-{attempt_index + 1}",
    ))
    return attempt_graph, seed, attempt_prompt_id


def _clipmode_photoshoot_staging_path(output: dict) -> Path:
    try:
        output_root = COMFY_OUTPUT.resolve(strict=True)
        candidate = Path(str(output.get("path") or "")).resolve(strict=True)
        relative = candidate.relative_to(output_root)
        info = candidate.lstat()
    except (OSError, ValueError) as error:
        raise ValueError("角色身份寫真候選圖片路徑無效") from error
    if (
        candidate.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or len(relative.parts) < 4
        or relative.parts[0] != "Castle"
        or relative.parts[1] != CLIPMODE_PHOTOSHOOT_STAGING_FOLDER
    ):
        raise ValueError("角色身份寫真候選圖片路徑無效")
    return candidate


def _cleanup_clipmode_photoshoot_candidate(output: dict) -> None:
    candidate = _clipmode_photoshoot_staging_path(output)
    output_root = COMFY_OUTPUT.resolve(strict=True)
    staging_root = output_root / "Castle" / CLIPMODE_PHOTOSHOOT_STAGING_FOLDER
    for path in (direct_sidecar_path(candidate), candidate):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("角色身份寫真候選圖片無法清理") from error
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise ValueError("角色身份寫真候選圖片無法安全清理")
        path.unlink()
    parent = candidate.parent
    while parent != staging_root.parent and parent.is_relative_to(staging_root):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _clipmode_photoshoot_quality_error(stderr: str) -> str:
    code = ""
    try:
        data = json.loads(stderr or "{}")
        if isinstance(data, dict):
            code = str(data.get("error") or "")
    except json.JSONDecodeError:
        pass
    if code.startswith("canonical_"):
        return "角色卡基準圖無法辨識出一張清楚的人臉"
    if code.startswith("insightface_") or code in {"opencv_unavailable", "insightface_unavailable"}:
        return "角色身份品質檢查模型目前不可用"
    return "角色身份品質檢查無法執行"


def _evaluate_clipmode_photoshoot_quality(
    output: dict,
    settings: dict,
) -> dict:
    candidate = _clipmode_photoshoot_staging_path(output)
    input_name = str(settings.get("_characterProfileInputName") or "")
    if re.fullmatch(
        re.escape(CLIPMODE_PROFILE_INPUT_PREFIX) + r"[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)",
        input_name,
        re.IGNORECASE,
    ) is None:
        raise ValueError("角色卡基準圖綁定無效")
    try:
        input_root = COMFY_INPUT.resolve(strict=True)
        canonical = (input_root / input_name).resolve(strict=True)
        canonical.relative_to(input_root)
        canonical_info = canonical.lstat()
    except (OSError, ValueError) as error:
        raise ValueError("角色卡基準圖不存在") from error
    if canonical.is_symlink() or not stat.S_ISREG(canonical_info.st_mode):
        raise ValueError("角色卡基準圖無效")
    if not CLIPMODE_PHOTOSHOOT_QUALITY_PYTHON.is_file():
        raise ValueError("角色身份品質檢查 Python 不存在")
    if not CLIPMODE_PHOTOSHOOT_QUALITY_SCRIPT.is_file():
        raise ValueError("角色身份品質檢查程式不存在")
    argv = [
        str(CLIPMODE_PHOTOSHOOT_QUALITY_PYTHON),
        str(CLIPMODE_PHOTOSHOOT_QUALITY_SCRIPT),
        "--canonical-profile", str(canonical),
        "--candidate", str(candidate),
        "--insightface-root", str(CLIPMODE_PHOTOSHOOT_INSIGHTFACE_ROOT),
        "--minimum-cosine-similarity", str(CLIPMODE_PHOTOSHOOT_IDENTITY_THRESHOLD),
    ]
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONNOUSERSITE": "1",
        "CUDA_VISIBLE_DEVICES": "",
    }
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=240,
            cwd="/tmp",
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("角色身份品質檢查無法執行") from error
    if completed.returncode != 0:
        raise ValueError(_clipmode_photoshoot_quality_error(completed.stderr[:8_192]))
    if len(completed.stdout.encode("utf-8")) > 32 * 1024:
        raise ValueError("角色身份品質檢查回傳過大")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("角色身份品質檢查回傳格式無效") from error
    if (
        not isinstance(report, dict)
        or report.get("schema") != CLIPMODE_PHOTOSHOOT_QUALITY_SCHEMA
        or type(report.get("accepted")) is not bool
        or not isinstance(report.get("reason"), str)
        or not isinstance(report.get("metrics"), dict)
        or not isinstance(report.get("thresholds"), dict)
        or report.get("provider") != "CPUExecutionProvider"
        or report["thresholds"].get("minimumIdentityCosineSimilarity")
        != CLIPMODE_PHOTOSHOOT_IDENTITY_THRESHOLD
    ):
        raise ValueError("角色身份品質檢查回傳格式無效")
    return report


def _publish_clipmode_photoshoot_candidate(output: dict, graph: dict) -> dict:
    candidate = _clipmode_photoshoot_staging_path(output)
    save_id = _clipmode_photoshoot_node_id(graph, "SaveImage")
    prefix = str(graph[save_id]["inputs"].get("filename_prefix") or "")
    subfolder, separator, stem = prefix.rpartition("/")
    if not separator or not stem:
        raise ValueError("角色身份寫真正式輸出位置無效")
    try:
        output_root = COMFY_OUTPUT.resolve(strict=True)
        destination_dir = (output_root / subfolder).resolve(strict=False)
        destination_dir.relative_to(output_root)
    except (OSError, ValueError) as error:
        raise ValueError("角色身份寫真正式輸出位置無效") from error
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{stem}_00001_.png"
    if destination.exists() or destination.is_symlink():
        raise ValueError("角色身份寫真正式輸出已存在")
    move_file_no_replace(candidate, destination)
    try:
        staging_sidecar = direct_sidecar_path(candidate)
        if staging_sidecar.is_file() and not staging_sidecar.is_symlink():
            staging_sidecar.unlink()
        info = destination.lstat()
    except OSError as error:
        raise ValueError("角色身份寫真正式輸出無法驗證") from error
    if destination.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise ValueError("角色身份寫真正式輸出無效")
    relative = destination.relative_to(output_root)
    return {
        "path": str(destination),
        "filename": relative.name,
        "subfolder": "" if relative.parent == Path(".") else relative.parent.as_posix(),
        "type": "output",
        "bytes": info.st_size,
        "sha256": content_sha256_cached(destination),
    }


def run_clipmode_photoshoot_worker(spec_path_value: str) -> dict:
    """Run only as the child command held by Castle's shared GPU flock."""

    token = os.environ.pop(CLIPMODE_PHOTOSHOOT_WORKER_TOKEN_ENV, "")
    root = _prepare_clipmode_photoshoot_state_root()
    spec_path = _clipmode_photoshoot_state_path(
        spec_path_value,
        root,
        r"spec-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json",
        "worker spec",
    )
    spec = _read_clipmode_photoshoot_state(spec_path)
    required_keys = {
        "schema", "job_id", "created", "generation_timeout_seconds", "graph_sha256",
        "graph", "settings", "extra_data", "receipt_path", "cancel_file",
        "worker_hmac_sha256",
    }
    if set(spec) != required_keys or spec.get("schema") != CLIPMODE_PHOTOSHOOT_SPEC_SCHEMA:
        raise ValueError("角色身份寫真 worker spec contract 無效")
    expected_hmac = _clipmode_photoshoot_worker_hmac(spec, token)
    if not hmac.compare_digest(str(spec.get("worker_hmac_sha256") or ""), expected_hmac):
        raise ValueError("角色身份寫真 worker spec 簽章無效")
    try:
        job_id = str(uuid.UUID(str(spec.get("job_id") or ""), version=4))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("角色身份寫真任務編號無效") from error
    if job_id != spec.get("job_id"):
        raise ValueError("角色身份寫真任務編號無效")
    receipt_path = _clipmode_photoshoot_state_path(
        spec.get("receipt_path"), root, rf"receipt-{re.escape(job_id)}\.json", "收據",
    )
    cancel_file = _clipmode_photoshoot_state_path(
        spec.get("cancel_file"), root, rf"cancel-{re.escape(job_id)}\.cancel", "取消訊號",
    )
    # A worker may be relaunched after the generation already finished (for
    # example when an older coarse GPU lock kept the original launcher
    # waiting). Treat its signed, byte-verified receipt as the idempotency
    # authority instead of submitting the same Comfy graph a second time.
    if receipt_path.exists():
        receipt = _read_clipmode_photoshoot_receipt(
            receipt_path, token, job_id, str(spec.get("graph_sha256") or ""),
        )
        print('CASTLE_PROGRESS {"pct":100,"label":"角色身份寫真已完成"}', flush=True)
        return receipt
    graph = spec.get("graph")
    settings = spec.get("settings")
    extra_data = spec.get("extra_data")
    if not isinstance(graph, dict) or not isinstance(settings, dict) or not isinstance(extra_data, dict):
        raise ValueError("角色身份寫真 worker spec 內容無效")
    if _clipmode_photoshoot_json_sha256(graph) != spec.get("graph_sha256"):
        raise ValueError("角色身份寫真 graph 驗證值不一致")
    rebuilt = clipmode_character_photoshoot_prompt(settings, {})
    if rebuilt != graph:
        raise ValueError("角色身份寫真 graph 無法由伺服器綁定重建")
    generation_timeout = spec.get("generation_timeout_seconds")
    if (
        isinstance(generation_timeout, bool)
        or not isinstance(generation_timeout, int)
        or not 60 <= generation_timeout <= 3_600
    ):
        raise ValueError("角色身份寫真生成期限無效")
    if cancel_file.exists():
        raise ValueError("角色身份寫真已取消")

    native_runner = _clipmode_photoshoot_native_runner()
    base_url = native_runner.validate_loopback_url(CLIPMODE_PHOTOSHOOT_COMFY_URL)
    native_runner.verify_loopback_listener(base_url)
    object_info = dict(native_runner.request_json(base_url, "/object_info", timeout=30.0))
    _verify_clipmode_photoshoot_object_info(object_info, graph)
    identity_route = _clipmode_photoshoot_graph_route(graph)
    quality_required = identity_route == CLIPMODE_REALISTIC_IDENTITY_ROUTE
    max_attempts = CLIPMODE_PHOTOSHOOT_MAX_ATTEMPTS if quality_required else 1
    post_attempted = False
    active_prompt_id = job_id
    candidate_output: dict | None = None
    output: dict | None = None
    accepted_quality: dict = {
        "schema": CLIPMODE_PHOTOSHOOT_QUALITY_SCHEMA,
        "accepted": True,
        "reason": "not_applicable",
        "metrics": {},
        "thresholds": {},
    }
    accepted_attempt = 1
    accepted_seed = int(graph[_clipmode_photoshoot_node_id(graph, "KSampler")]["inputs"]["seed"])
    try:
        # gpu_queue.py already owns the shared RTX 5090 flock here. A manual or
        # non-cooperating Comfy prompt can still exist, so wait for it instead
        # of turning an ordinary queue condition into a failed user job.
        _wait_for_clipmode_photoshoot_comfy_idle(
            native_runner, base_url, cancel_file,
        )
        # The binding may have waited behind another job. Re-hash both exact
        # model files after that wait, then repeat GPU + queue checks directly
        # before POST to close the stale-binding/queue race.
        if clipmode_character_photoshoot_prompt(settings, {}) != graph:
            raise ValueError("角色身份寫真模型綁定在排程期間變更")
        for attempt_index in range(max_attempts):
            if cancel_file.exists():
                raise ValueError("角色身份寫真已取消")
            if quality_required:
                attempt_graph, attempt_seed, active_prompt_id = (
                    _clipmode_photoshoot_attempt_graph(graph, job_id, attempt_index)
                )
            else:
                attempt_graph = graph
                attempt_seed = accepted_seed
                active_prompt_id = job_id
            attempt_extra_data = json.loads(json.dumps(extra_data, ensure_ascii=False))
            public = attempt_extra_data.get("castlePublicSettings")
            if isinstance(public, dict):
                public["seed"] = attempt_seed
                public["quality_attempt"] = attempt_index + 1
            payload = {
                "prompt": attempt_graph,
                "client_id": CLIENT_ID,
                "prompt_id": active_prompt_id,
                "extra_data": attempt_extra_data,
            }
            # The first POST receives the normal global headroom check.  Later
            # retries reuse the already-loaded SDXL/InstantID models while this
            # worker still owns the flock; cached VRAM is not a competing job
            # and must not deadlock the same logical request.
            if attempt_index == 0:
                native_runner.require_gpu_headroom(native_runner.read_gpu_snapshot())
            native_runner.require_queue_idle(
                native_runner.request_json(base_url, "/queue", timeout=10.0)
            )
            post_attempted = True
            response = native_runner.request_json(
                base_url, "/prompt", method="POST", payload=payload, timeout=30.0,
            )
            if (
                response.get("prompt_id") != active_prompt_id
                or not isinstance(response.get("node_errors", {}), dict)
                or response.get("node_errors", {})
            ):
                raise ValueError("ComfyUI 拒絕角色身份寫真 graph")
            progress_pct = 15 + (attempt_index * 25)
            print(
                "CASTLE_PROGRESS " + json.dumps({
                    "pct": progress_pct,
                    "label": (
                        f"角色身份寫真生成中（第 {attempt_index + 1}/{max_attempts} 次）"
                        if quality_required
                        else "角色身份寫真生成中"
                    ),
                }, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
            # Queue wait time is intentionally excluded. Each accepted retry
            # receives a fresh generation deadline while the same flock stays
            # held for the whole logical job.
            deadline_epoch = time.time() + generation_timeout
            history_item = dict(native_runner.wait_for_history(
                base_url,
                active_prompt_id,
                deadline_epoch=deadline_epoch,
                cancel_file=str(cancel_file),
            ))
            candidate_output = _validate_clipmode_photoshoot_output(
                history_item, attempt_graph,
            )
            native_runner.wait_for_comfy_queue_idle(base_url, timeout_seconds=None)
            post_attempted = False
            if not quality_required:
                output = candidate_output
                break
            print(
                "CASTLE_PROGRESS " + json.dumps({
                    "pct": min(92, progress_pct + 12),
                    "label": "正在檢查人物與角色相似度",
                }, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
            accepted_quality = _evaluate_clipmode_photoshoot_quality(
                candidate_output, settings,
            )
            if accepted_quality.get("accepted") is True:
                output = _publish_clipmode_photoshoot_candidate(candidate_output, graph)
                candidate_output = None
                accepted_attempt = attempt_index + 1
                accepted_seed = attempt_seed
                break
            rejection_reason = str(accepted_quality.get("reason") or "quality_rejected")
            _cleanup_clipmode_photoshoot_candidate(candidate_output)
            candidate_output = None
            if attempt_index + 1 >= max_attempts:
                raise ValueError(
                    "角色身份寫真連續 3 次未通過人物／身份品質檢查（"
                    f"{rejection_reason}）"
                )
            print(
                "CASTLE_PROGRESS " + json.dumps({
                    "pct": min(90, progress_pct + 15),
                    "label": "候選圖未達角色品質，已自動換 seed 重試",
                }, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
        if output is None:
            raise ValueError("角色身份寫真沒有可發布的品質合格圖片")
    except BaseException:
        if post_attempted:
            native_runner.cancel_owned_prompt(base_url, active_prompt_id)
            # A POST timeout is ambiguous: keep the shared flock until ComfyUI
            # proves that no accepted instance of our UUID remains active.
            native_runner.wait_for_comfy_queue_idle(base_url, timeout_seconds=None)
        if candidate_output is not None and quality_required:
            try:
                _cleanup_clipmode_photoshoot_candidate(candidate_output)
            except Exception:
                pass
        raise

    receipt = {
        "schema": CLIPMODE_PHOTOSHOOT_RECEIPT_SCHEMA,
        "job_id": job_id,
        "graph_sha256": spec["graph_sha256"],
        "completed": int(time.time()),
        "output": output,
        "quality": accepted_quality,
        "accepted_attempt": accepted_attempt,
        "accepted_seed": accepted_seed,
    }
    receipt["worker_hmac_sha256"] = _clipmode_photoshoot_worker_hmac(receipt, token)
    _write_clipmode_photoshoot_state(receipt_path, receipt)
    print('CASTLE_PROGRESS {"pct":100,"label":"角色身份寫真已完成"}', flush=True)
    return receipt


def _read_clipmode_photoshoot_receipt(
    receipt_path: Path,
    token: str,
    job_id: str,
    graph_sha256: str,
) -> dict:
    receipt = _read_clipmode_photoshoot_state(receipt_path)
    required_keys = {
        "schema", "job_id", "graph_sha256", "completed", "output",
        "quality", "accepted_attempt", "accepted_seed",
        "worker_hmac_sha256",
    }
    if (
        set(receipt) != required_keys
        or receipt.get("schema") != CLIPMODE_PHOTOSHOOT_RECEIPT_SCHEMA
        or receipt.get("job_id") != job_id
        or receipt.get("graph_sha256") != graph_sha256
        or not hmac.compare_digest(
            str(receipt.get("worker_hmac_sha256") or ""),
            _clipmode_photoshoot_worker_hmac(receipt, token),
        )
    ):
        raise ValueError("角色身份寫真完成收據無效")
    output = receipt.get("output")
    quality = receipt.get("quality")
    accepted_attempt = receipt.get("accepted_attempt")
    accepted_seed = receipt.get("accepted_seed")
    if (
        not isinstance(output, dict)
        or not isinstance(quality, dict)
        or quality.get("schema") != CLIPMODE_PHOTOSHOOT_QUALITY_SCHEMA
        or quality.get("accepted") is not True
        or type(accepted_attempt) is not int
        or not 1 <= accepted_attempt <= CLIPMODE_PHOTOSHOOT_MAX_ATTEMPTS
        or type(accepted_seed) is not int
        or not 0 <= accepted_seed <= 2_147_483_647
    ):
        raise ValueError("角色身份寫真完成收據缺少圖片")
    try:
        output_path = Path(str(output.get("path") or "")).resolve(strict=True)
        output_root = COMFY_OUTPUT.resolve(strict=True)
        info = output_path.lstat()
    except OSError as error:
        raise ValueError("角色身份寫真完成圖片無法驗證") from error
    if (
        not output_path.is_relative_to(output_root)
        or output_path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_size != output.get("bytes")
        or content_sha256_cached(output_path) != output.get("sha256")
    ):
        raise ValueError("角色身份寫真完成圖片驗證值已改變")
    return receipt


def _clipmode_photoshoot_sidecar(
    *,
    job_id: str,
    receipt: dict,
    submitted_by: dict | None,
    character_context: dict,
    settings: dict,
    action_id: str,
    variant_id: str,
    created: float,
) -> Path:
    output = receipt.get("output") if isinstance(receipt.get("output"), dict) else {}
    output_path = Path(str(output.get("path") or ""))
    try:
        resolved_output = output_path.resolve(strict=True)
        relative = resolved_output.relative_to(COMFY_OUTPUT.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ValueError("角色身份寫真完成圖片不在作品庫內") from error
    character_id = stable_character_identifier(character_context.get("id"))
    character_name = str(character_context.get("name") or character_id)[:160]
    visual_class = normalized_visual_class(character_context.get("visualClass"))
    identity_route_label = (
        "SDXL + InstantID + ReActor 雙重身份鎖定"
        if visual_class == "realistic"
        else "SDXL + 角色 LoRA"
    )
    submitted_email = normalize_email(str((submitted_by or {}).get("email") or ""))
    if not character_id or not submitted_email:
        raise ValueError("角色身份寫真作品歸屬資料無效")
    workflow = f"castle-{CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID}-web"
    public_settings = {
        "prompt": str(settings.get("prompt") or "")[:4_000],
        "negative_prompt": str(settings.get("negative_prompt") or "")[:4_000],
        "effective_prompt": str(
            settings.get("_effectivePrompt") or settings.get("prompt") or ""
        )[:4_000],
        "effective_negative_prompt": str(
            settings.get("_effectiveNegativePrompt")
            or settings.get("negative_prompt")
            or ""
        )[:4_000],
        "aspect_ratio": str(settings.get("aspect_ratio") or ""),
        "seed": int(receipt.get("accepted_seed")),
        "quality_attempt": int(receipt.get("accepted_attempt")),
        "quality_attempt_max": (
            CLIPMODE_PHOTOSHOOT_MAX_ATTEMPTS if visual_class == "realistic" else 1
        ),
    }
    quality = receipt.get("quality") if isinstance(receipt.get("quality"), dict) else {}
    quality_metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
    quality_thresholds = quality.get("thresholds") if isinstance(quality.get("thresholds"), dict) else {}
    parameters = [
        {"id": "prompt", "label": "Prompt", "value": public_settings["prompt"]},
        {
            "id": "effectivePrompt",
            "label": "實際送給 SDXL 的英文 Prompt",
            "value": (
                public_settings["effective_prompt"]
                if public_settings["effective_prompt"] != public_settings["prompt"]
                else ""
            ),
        },
        {"id": "negativePrompt", "label": "Negative prompt", "value": public_settings["negative_prompt"]},
        {
            "id": "dimensions",
            "label": "尺寸",
            "value": public_settings["aspect_ratio"].replace("x", " × "),
        },
        {"id": "seed", "label": "Seed", "value": str(public_settings["seed"])},
        {
            "id": "qualityAttempt",
            "label": "品質通過嘗試",
            "value": (
                f"{public_settings['quality_attempt']} / "
                f"{public_settings['quality_attempt_max']}"
            ),
        },
        {
            "id": "identityCosineSimilarity",
            "label": "角色臉部相似度",
            "value": (
                str(quality_metrics.get("identityCosineSimilarity"))
                if quality_metrics.get("identityCosineSimilarity") is not None
                else ""
            ),
        },
    ]
    parameters = [item for item in parameters if item["value"] not in (None, "")]
    public_character = {
        "id": character_id,
        "name": character_name,
        "kind": str(character_context.get("kind") or "provider_character")[:80],
        "visualClass": visual_class,
    }
    filename = relative.name
    subfolder = "" if relative.parent == Path(".") else relative.parent.as_posix()
    action = CHARACTER_ACTION_BY_ID.get(action_id) or {}
    data = {
        "version": 2,
        "promptId": job_id,
        "workflow": workflow,
        "featureId": CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID,
        "feature": "角色身份寫真",
        "status": "completed",
        "created": int(created),
        "submittedBy": {
            "email": submitted_email,
            "displayName": str((submitted_by or {}).get("displayName") or submitted_email),
        },
        "characterId": character_id,
        "characterName": character_name,
        "visualClass": visual_class,
        "actionId": action_id,
        "actionLabel": str(action.get("label") or action_id)[:160],
        "variantId": variant_id,
        "executionRoute": (
            CLIPMODE_REALISTIC_IDENTITY_ROUTE
            if visual_class == "realistic"
            else CLIPMODE_ANIME_IDENTITY_ROUTE
        ),
        "extra_pnginfo": {
            "workflow": {
                "id": workflow,
                "character": public_character,
                "variantId": variant_id,
                "executionRoute": (
                    CLIPMODE_REALISTIC_IDENTITY_ROUTE
                    if visual_class == "realistic"
                    else CLIPMODE_ANIME_IDENTITY_ROUTE
                ),
            },
        },
        "settings": public_settings,
        "quality": {
            "schema": str(quality.get("schema") or ""),
            "accepted": quality.get("accepted") is True,
            "reason": str(quality.get("reason") or ""),
            "metrics": quality_metrics,
            "thresholds": quality_thresholds,
            "attempt": public_settings["quality_attempt"],
        },
        "detailParameters": parameters,
        "detailNodes": [{
            "id": "photoshoot",
            "type": f"角色身份寫真（{identity_route_label} · Beta）",
            "inputs": [
                {"name": "prompt", "value": public_settings["prompt"]},
                {"name": "effective_prompt", "value": public_settings["effective_prompt"]},
                {"name": "negative_prompt", "value": public_settings["negative_prompt"]},
                {"name": "aspect_ratio", "value": public_settings["aspect_ratio"]},
                {"name": "seed", "value": str(public_settings["seed"])},
                {"name": "quality_attempt", "value": str(public_settings["quality_attempt"])},
            ],
        }],
        "outputs": [{
            "kind": "image",
            "filename": filename,
            "subfolder": subfolder,
            "type": "output",
        }],
    }
    sidecar_path = direct_sidecar_path(resolved_output)
    with _GALLERY_MUTATION_LOCK:
        if sidecar_path.exists():
            existing = load_direct_sidecar(resolved_output)
            if (
                not isinstance(existing, dict)
                or existing.get("promptId") != job_id
                or existing.get("outputs") != data["outputs"]
            ):
                raise ValueError("角色身份寫真作品資料已存在且不一致")
            raw_provenance = existing.get("provenance")
            if (
                raw_provenance is not None
                and verify_output_provenance_receipt(
                    resolved_output, raw_provenance,
                ) is None
            ):
                raise ValueError("角色身份寫真作品資料簽章無效")
            # Gallery reconciliation can discover the Comfy output before the
            # launcher finishes. Merge the launcher-owned workflow fields into
            # that already signed sidecar instead of treating the benign race
            # as a failed generation. Keep the reconciler's graph-derived
            # detailParameters so the UI still shows the exact effective prompt.
            merged = dict(existing)
            existing_parameters = existing.get("detailParameters")
            merged.update(data)
            if isinstance(existing_parameters, list) and existing_parameters:
                merged["detailParameters"] = existing_parameters
            if merged != existing:
                atomic_write_json(sidecar_path, merged, 0o600)
        else:
            atomic_write_json(sidecar_path, data, 0o600)
    return sidecar_path


def run_clipmode_character_photoshoot_job(
    job_id: str,
    spec_path: Path,
    receipt_path: Path,
    cancel_file: Path,
    token: str,
    graph_sha256: str,
    submitted_by: dict | None,
    character_context: dict,
    public_settings: dict,
    action_id: str,
    variant_id: str,
    created: float,
) -> None:
    argv = [
        GPU_QUEUE_PYTHON,
        str(GPU_QUEUE_SCRIPT),
        "--min-free-mb", str(CLIPMODE_PHOTOSHOOT_MIN_FREE_MB),
        "--max-util", str(CLIPMODE_PHOTOSHOOT_MAX_UTIL),
        "--interval", str(CLIPMODE_PHOTOSHOOT_QUEUE_INTERVAL),
        "--comfy-url", CLIPMODE_PHOTOSHOOT_COMFY_URL,
        "--",
        sys.executable,
        str(ROOT / "server.py"),
        "--clipmode-photoshoot-worker", str(spec_path),
    ]
    environment = dict(os.environ)
    environment[CLIPMODE_PHOTOSHOOT_WORKER_TOKEN_ENV] = token
    process = None
    try:
        update_local_job(job_id, progress={"pct": 0, "label": "等待 RTX 5090 共用佇列"})
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            cwd=str(ROOT.parent),
            env=environment,
        )
        update_local_job(job_id, pid=process.pid)
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            if line.startswith("[gpu-queue] running:"):
                update_local_job(
                    job_id,
                    state="running",
                    progress={"pct": 5, "label": "GPU 已保留，正在安全檢查 ComfyUI"},
                )
            elif line.startswith("CASTLE_PROGRESS "):
                try:
                    progress = json.loads(line.removeprefix("CASTLE_PROGRESS "))
                except json.JSONDecodeError:
                    continue
                if isinstance(progress, dict):
                    update_local_job(job_id, state="running", progress=progress)
        return_code = process.wait()
        if return_code != 0:
            with _LOCAL_JOB_LOCK:
                cancelled = (LOCAL_JOBS.get(job_id) or {}).get("state") == "cancel_requested"
            if cancelled:
                update_local_job(
                    job_id,
                    state="cancelled",
                    progress={"pct": 100, "label": "角色身份寫真已取消"},
                )
                return
            raise ValueError(f"角色身份寫真 worker 失敗（exit {return_code}）")
        receipt = _read_clipmode_photoshoot_receipt(
            receipt_path, token, job_id, graph_sha256,
        )
        output = receipt["output"]
        sidecar = _clipmode_photoshoot_sidecar(
            job_id=job_id,
            receipt=receipt,
            submitted_by=submitted_by,
            character_context=character_context,
            settings=public_settings,
            action_id=action_id,
            variant_id=variant_id,
            created=created,
        )
        update_local_job(
            job_id,
            state="completed",
            outputCount=1,
            output=str(output["path"]),
            sidecar=str(sidecar),
            progress={"pct": 100, "label": "角色身份寫真已完成"},
        )
    except Exception as error:
        with _LOCAL_JOB_LOCK:
            cancelled = (LOCAL_JOBS.get(job_id) or {}).get("state") == "cancel_requested"
        if cancelled:
            update_local_job(
                job_id,
                state="cancelled",
                progress={"pct": 100, "label": "角色身份寫真已取消"},
            )
        else:
            update_local_job(
                job_id,
                state="error",
                error=str(error)[:240],
                progress={"pct": 100, "label": "角色身份寫真生成失敗"},
            )
    finally:
        for path in (spec_path, receipt_path, cancel_file):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def start_clipmode_character_photoshoot_job(
    graph: dict,
    settings: dict,
    submitted_by: dict | None,
    character_context: dict,
    action_id: str,
    variant_id: str,
) -> dict:
    state_root = _prepare_clipmode_photoshoot_state_root()
    if not GPU_QUEUE_SCRIPT.is_file():
        raise ValueError(f"找不到 GPU queue script：{GPU_QUEUE_SCRIPT}")
    if not Path(sys.executable).is_file():
        raise ValueError("找不到角色身份寫真 worker Python")
    if _clipmode_photoshoot_graph_route(graph) == CLIPMODE_REALISTIC_IDENTITY_ROUTE:
        if not CLIPMODE_PHOTOSHOOT_QUALITY_PYTHON.is_file():
            raise ValueError("找不到角色身份品質檢查 Python")
        if not CLIPMODE_PHOTOSHOOT_QUALITY_SCRIPT.is_file():
            raise ValueError("找不到角色身份品質檢查程式")
        try:
            insightface_model_root = (
                CLIPMODE_PHOTOSHOOT_INSIGHTFACE_ROOT / "models" / "buffalo_l"
            ).resolve(strict=True)
        except OSError as error:
            raise ValueError("找不到角色身份品質檢查模型") from error
        if not insightface_model_root.is_dir():
            raise ValueError("找不到角色身份品質檢查模型")
    job_id = str(uuid.uuid4())
    created = time.time()
    spec_path = state_root / f"spec-{job_id}.json"
    receipt_path = state_root / f"receipt-{job_id}.json"
    cancel_file = state_root / f"cancel-{job_id}.cancel"
    token = secrets.token_hex(32)

    # Persist only the private fields the worker must re-verify. Training
    # receipts and UI-only character records never enter ComfyUI extra_data.
    worker_settings = {
        key: settings[key]
        for key in (
            "prompt", "negative_prompt", "_effectivePrompt",
            "_effectiveNegativePrompt", "aspect_ratio",
            "_characterBindingCapabilityId", "_characterBaseModelName",
            "_characterBaseModelSha256", "_characterVisualClass",
            "_characterIdentityRoute", "_characterProfileInputName",
            "_characterProfileSha256", "_characterProfileByteSize",
            "_characterProfileSignatureFamily", "_characterLoraModelName",
            "_characterLoraModelSha256", "_characterTriggerToken",
            "_characterId", "_characterActionId",
        )
        if key in settings
    }
    sampler_id = _clipmode_photoshoot_node_id(graph, "KSampler")
    save_id = _clipmode_photoshoot_node_id(graph, "SaveImage")
    worker_settings["seed"] = graph[sampler_id]["inputs"]["seed"]
    worker_settings["prefix"] = graph[save_id]["inputs"]["filename_prefix"]
    if clipmode_character_photoshoot_prompt(worker_settings, {}) != graph:
        raise ValueError("角色身份寫真 graph 無法由固定設定重建")
    graph_sha256 = _clipmode_photoshoot_json_sha256(graph)
    public_character = {
        key: character_context[key]
        for key in ("id", "name", "kind", "type", "visualClass", "group", "origin")
        if key in character_context
    }
    extra_data = workflow_extra_data(
        f"castle-{CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID}-web",
        public_character,
        submitted_by,
        action_id,
        variant_id,
    )
    extra_data["castlePublicSettings"] = {
        "prompt": str(settings.get("prompt") or ""),
        "negative_prompt": str(settings.get("negative_prompt") or ""),
        "effective_prompt": str(settings.get("_effectivePrompt") or settings.get("prompt") or ""),
        "effective_negative_prompt": str(
            settings.get("_effectiveNegativePrompt")
            or settings.get("negative_prompt")
            or ""
        ),
        "aspect_ratio": str(settings.get("aspect_ratio") or ""),
        "seed": int(worker_settings["seed"]),
    }
    spec = {
        "schema": CLIPMODE_PHOTOSHOOT_SPEC_SCHEMA,
        "job_id": job_id,
        "created": created,
        "generation_timeout_seconds": CLIPMODE_PHOTOSHOOT_GENERATION_TIMEOUT_SECONDS,
        "graph_sha256": graph_sha256,
        "graph": graph,
        "settings": worker_settings,
        "extra_data": extra_data,
        "receipt_path": str(receipt_path),
        "cancel_file": str(cancel_file),
    }
    spec["worker_hmac_sha256"] = _clipmode_photoshoot_worker_hmac(spec, token)
    _write_clipmode_photoshoot_state(spec_path, spec)
    feature = FEATURE_BY_ID.get(CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID) or {}
    job = {
        "promptId": job_id,
        "state": "pending",
        "position": 1,
        "workflow": f"castle-{CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID}-web",
        "feature": str(feature.get("title") or "角色身份寫真"),
        "source": "castle",
        "jobKind": CLIPMODE_PHOTOSHOOT_JOB_KIND,
        "submittedBy": {
            "email": normalize_email(str((submitted_by or {}).get("email") or "")),
            "displayName": str((submitted_by or {}).get("displayName") or ""),
        },
        "nodeCount": len(graph),
        "outputCount": 0,
        "outputNodes": ["SaveImage"],
        "nodeTypes": {
            str(node_id): str(node.get("class_type") or "")
            for node_id, node in graph.items()
            if isinstance(node, dict)
        },
        "progress": {"pct": 0, "label": "等待 RTX 5090 共用佇列"},
        "created": created,
        "updated": created,
        "cancelFile": str(cancel_file),
    }
    character_id = stable_character_identifier(character_context.get("id"))
    record_job_attribution(
        job_id,
        submitted_by,
        character_id,
        CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID,
        action_id,
        variant_id,
        scope="character",
        job_kind=CLIPMODE_PHOTOSHOOT_JOB_KIND,
        character_context=character_context,
    )
    with _LOCAL_JOB_LOCK:
        LOCAL_JOBS[job_id] = job
    thread = threading.Thread(
        target=run_clipmode_character_photoshoot_job,
        args=(
            job_id,
            spec_path,
            receipt_path,
            cancel_file,
            token,
            graph_sha256,
            dict(submitted_by) if isinstance(submitted_by, dict) else None,
            dict(character_context),
            {
                "prompt": str(settings.get("prompt") or ""),
                "negative_prompt": str(settings.get("negative_prompt") or ""),
                "_effectivePrompt": str(
                    settings.get("_effectivePrompt") or settings.get("prompt") or ""
                ),
                "_effectiveNegativePrompt": str(
                    settings.get("_effectiveNegativePrompt")
                    or settings.get("negative_prompt")
                    or ""
                ),
                "aspect_ratio": str(settings.get("aspect_ratio") or ""),
                "_resolvedSeed": int(worker_settings["seed"]),
            },
            action_id,
            variant_id,
            created,
        ),
        name=f"web-comfyui-clipmode-photoshoot-{job_id[:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        with _LOCAL_JOB_LOCK:
            LOCAL_JOBS.pop(job_id, None)
        for path in (spec_path, receipt_path, cancel_file):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return {"ok": True, "prompt_id": job_id, "local": True, "queued": True}


def qwen21_t2i_prompt(settings: dict, uploads: dict | None = None) -> dict:
    return compile_qwen21_prompt(
        settings, uploads or {}, edit=False, seed=normalize_seed(settings.get("seed")),
        prefix=output_prefix(settings, "qwen21-text2img"),
    )


def qwen21_edit_prompt(settings: dict, uploads: dict | None = None) -> dict:
    return compile_qwen21_prompt(
        settings, uploads or {}, edit=True, seed=normalize_seed(settings.get("seed")),
        prefix=output_prefix(settings, "qwen21-edit"),
    )


BUILDERS = {
    "qwen21_t2i": qwen21_t2i_prompt,
    "qwen21_edit": qwen21_edit_prompt,
    "zimage": zimage_prompt,
    "clipmode_character_photoshoot": clipmode_character_photoshoot_prompt,
    "flux_kontext": flux_kontext_prompt,
    "anime_scene_card": anime_scene_card_prompt,
    "wan_i2v": wan_i2v_prompt,
    "wan_flf": wan_flf_prompt,
    "scail_dance": scail_dance_prompt,
    "ip_blend": ip_blend_prompt,
    "instantid": instantid_prompt,
    "face_swap": face_swap_prompt,
    "voice_clone": voice_clone_prompt,
    "lipsync": lipsync_prompt,
    "video_loop": video_loop_prompt,
    "voice_speak": voice_speak_prompt,
    "voxcpm_direct": voxcpm_direct_prompt,
    "minimax_h3_t2v": minimax_h3_t2v_prompt,
    "minimax_h3_i2v": minimax_h3_i2v_prompt,
    "minimax_h3_ref2va_image": minimax_h3_ref2va_image_prompt,
}


def gallery_limit(query: str, default: int = 200, cap: int = 500) -> int:
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    try:
        raw = int((params.get("max_items") or [default])[0])
    except (TypeError, ValueError):
        raw = default
    return max(1, min(raw, cap))


def library_server_context():
    """Pass the live module bindings so isolated tests can supply temporary roots."""
    return types.SimpleNamespace(**globals())


def gallery_item_key(item: dict) -> tuple[str, str, str]:
    return (
        item.get("type", "output"),
        item.get("subfolder", ""),
        item.get("filename", ""),
    )


def local_history_key(view_type: str, subfolder: str, filename: str) -> str:
    identity = "\x00".join((view_type, subfolder, filename))
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"local-history:{suffix}"


def gallery_character_summary(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    cid = str(raw.get("id") or raw.get("_characterId") or "").strip()
    name = str(raw.get("name") or raw.get("_characterName") or "").strip()
    if not cid and not name:
        return None
    return _public_scrub({
        "id": cid,
        "name": name,
        "kind": raw.get("kind", ""),
        "type": raw.get("type", ""),
        "visualClass": normalized_visual_class(
            raw.get("visualClass") or raw.get("_characterVisualClass")
        ),
        "group": raw.get("group", ""),
        "tagline": raw.get("tagline", ""),
        "color": raw.get("color", ""),
    })


def character_summary_by_id(character_id: str) -> dict | None:
    cid = stable_character_identifier(character_id)
    if not cid:
        return None
    try:
        return gallery_character_summary(get_character_entry(cid))
    except KeyError:
        return {"id": cid, "name": cid}


def indexed_output_provenance(
    prompt_id: str,
    job_index: dict[str, dict] | None = None,
) -> dict:
    """Project immutable role attribution only from Castle's server job index.

    Workflow JSON, output folders and legacy sidecars remain useful display
    metadata, but none of them may authorize a role-specific continuation.
    """
    pid = str(prompt_id or "").strip()
    if not prompt_id_hint_valid(pid):
        return {}
    jobs = job_index if isinstance(job_index, dict) else load_job_index()
    indexed = jobs.get(pid)
    if not isinstance(indexed, dict):
        return {}
    character_id = stable_character_identifier(indexed.get("characterId"))
    action_id = safe_path_segment(str(indexed.get("actionId") or ""), "")
    feature_id = str(indexed.get("feature") or "").strip()[:160]
    variant_id = str(indexed.get("variantId") or "").strip()[:160]
    visual_class = normalized_visual_class(indexed.get("visualClass"))
    if not all((character_id, action_id, feature_id, variant_id)) or visual_class == "unknown":
        return {}
    character_name = str(indexed.get("characterName") or character_id).strip()[:160]
    character = gallery_character_summary({
        "id": character_id,
        "name": character_name,
        "visualClass": visual_class,
    })
    action = CHARACTER_ACTION_BY_ID.get(action_id) or {}
    receipt = {
        "schema": OUTPUT_PROVENANCE_SCHEMA,
        "promptId": pid,
        "characterId": character_id,
        "visualClass": visual_class,
        "featureId": feature_id,
        "actionId": action_id,
        "variantId": variant_id,
    }
    return {
        "provenanceTrusted": True,
        "characterAttribution": "server",
        "featureId": feature_id,
        "character": character,
        "variantId": variant_id,
        "visualClass": visual_class,
        "action": {
            "id": action_id,
            "label": str(indexed.get("actionLabel") or action.get("label") or action_id)[:160],
            "featureId": feature_id,
        },
        "provenance": receipt,
    }


def output_provenance_projection(receipt: dict) -> dict:
    if receipt.get("schema") == MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA:
        public_receipt = {
            "schema": MINIMAX_H3_OUTPUT_PROVENANCE_SCHEMA,
            "scope": "generic",
            "jobKind": MINIMAX_H3_WEB_JOB_KIND,
            "promptId": str(receipt.get("promptId") or ""),
            "featureId": str(receipt.get("featureId") or ""),
            "mode": str(receipt.get("mode") or ""),
        }
        return {
            "promptId": public_receipt["promptId"],
            "provenanceTrusted": True,
            "characterAttribution": "generic",
            "featureId": public_receipt["featureId"],
            "provenance": public_receipt,
        }
    action = CHARACTER_ACTION_BY_ID.get(str(receipt.get("actionId") or "")) or {}
    character_id = str(receipt.get("characterId") or "")
    visual_class = normalized_visual_class(receipt.get("visualClass"))
    character = gallery_character_summary({
        "id": character_id,
        "name": str(receipt.get("characterName") or character_id),
        "visualClass": visual_class,
    })
    public_receipt = {
        "schema": OUTPUT_PROVENANCE_SCHEMA,
        "promptId": str(receipt.get("promptId") or ""),
        "characterId": character_id,
        "visualClass": visual_class,
        "featureId": str(receipt.get("featureId") or ""),
        "actionId": str(receipt.get("actionId") or ""),
        "variantId": str(receipt.get("variantId") or ""),
    }
    return {
        "promptId": public_receipt["promptId"],
        "provenanceTrusted": True,
        "characterAttribution": "server",
        "featureId": public_receipt["featureId"],
        "character": character,
        "variantId": public_receipt["variantId"],
        "visualClass": visual_class,
        "action": {
            "id": public_receipt["actionId"],
            "label": str(receipt.get("actionLabel") or action.get("label") or public_receipt["actionId"]),
            "featureId": public_receipt["featureId"],
        },
        "provenance": public_receipt,
    }


def durable_output_provenance(
    path: Path,
    prompt_id: str,
    indexed: dict | None,
    *,
    filename: str,
    subfolder: str,
    view_type: str,
    allow_issue: bool,
) -> dict:
    """Verify an existing receipt or issue the first receipt from an indexed job.

    An existing invalid receipt is never replaced automatically: changed bytes or
    a changed signature must remain fail-closed instead of being blessed again.
    """
    with _GALLERY_MUTATION_LOCK:
        sidecar_path = direct_sidecar_path(path)
        sidecar_exists = sidecar_path.exists()
        sidecar = load_direct_sidecar(path)
        if sidecar_exists and sidecar is None:
            return {}
        raw_receipt = sidecar.get("provenance") if isinstance(sidecar, dict) else None
        if raw_receipt is not None:
            verified = verify_output_provenance_receipt(path, raw_receipt)
            if verified:
                return output_provenance_projection(verified)
            signed_shape = isinstance(raw_receipt, dict) and any(
                raw_receipt.get(key) for key in ("signature", "contentSha256", "authority")
            )
            if signed_shape or not allow_issue:
                return {}
        if not allow_issue or not isinstance(indexed, dict):
            return {}
        try:
            receipt = issue_output_provenance_receipt(path, prompt_id, indexed)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"WARNING output provenance issuance failed for {prompt_id}: {error}")
            return {}
        if receipt is None:
            return {}
        data = _json_copy(sidecar) if isinstance(sidecar, dict) else {}
        try:
            sidecar_version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            sidecar_version = 0
        data["version"] = max(2, sidecar_version)
        data["promptId"] = receipt["promptId"]
        submitted_by = data.get("submittedBy") if isinstance(data.get("submittedBy"), dict) else {}
        data["submittedBy"] = {
            **submitted_by,
            "email": receipt["ownerEmail"],
        }
        projection = output_provenance_projection(receipt)
        data["character"] = _json_copy(projection["character"])
        data["action"] = _json_copy(projection["action"])
        data["variantId"] = projection["variantId"]
        data["visualClass"] = projection["visualClass"]
        data["provenance"] = receipt
        outputs = data.get("outputs") if isinstance(data.get("outputs"), list) else []
        updated_outputs = []
        found = False
        for raw_output in outputs:
            if not isinstance(raw_output, dict):
                continue
            output = dict(raw_output)
            if (
                str(output.get("filename") or "") == filename
                and str(output.get("subfolder") or "") == subfolder
                and str(output.get("type") or "output") == view_type
            ):
                output["kind"] = gallery_media_kind(path) or str(output.get("kind") or "")
                found = True
            updated_outputs.append(output)
        if not found:
            updated_outputs.append({
                "kind": gallery_media_kind(path) or "",
                "filename": filename,
                "subfolder": subfolder,
                "type": view_type,
            })
        data["outputs"] = updated_outputs
        try:
            atomic_write_json(sidecar_path, data, 0o600)
        except OSError as error:
            print(f"WARNING output provenance sidecar write failed for {prompt_id}: {error}")
            return {}
        return projection


def persist_output_history_detail(
    path: Path,
    *,
    prompt_id: str,
    indexed: dict | None,
    workflow: str,
    feature: str,
    feature_id: str,
    parameters: list[dict],
    filename: str,
    subfolder: str,
    view_type: str,
) -> bool:
    """Persist only display-safe generation facts while Comfy history exists.

    This keeps workflow/prompt/seed/size details available after ComfyUI evicts
    or loses its in-memory history.  It never stores uploads, filesystem paths,
    model paths, owner tokens, or an arbitrary prompt graph.
    """
    if not isinstance(indexed, dict) or not path.is_file():
        return False
    owner_email = normalize_email(str(indexed.get("email") or ""))
    clean_feature_id = str(feature_id or "").strip()
    if (
        not owner_email
        or not prompt_id_hint_valid(str(prompt_id or ""))
        or clean_feature_id not in FEATURE_BY_ID
        or not isinstance(parameters, list)
    ):
        return False
    clean_parameters = []
    for raw in parameters:
        if not isinstance(raw, dict):
            continue
        parameter_id = str(raw.get("id") or "").strip()[:80]
        label = str(raw.get("label") or "").strip()[:160]
        value = _primitive_prompt_value(raw.get("value"))
        if parameter_id and label and value not in (None, "", [], {}):
            clean_parameters.append({"id": parameter_id, "label": label, "value": value})
    with _GALLERY_MUTATION_LOCK:
        sidecar_path = direct_sidecar_path(path)
        sidecar_exists = sidecar_path.exists()
        existing = load_direct_sidecar(path)
        if sidecar_exists and existing is None:
            return False
        data = dict(existing) if isinstance(existing, dict) else {}
        existing_prompt = str(data.get("promptId") or "").strip()
        if existing_prompt and existing_prompt != prompt_id:
            return False
        existing_owner = submitted_by_email(data)
        if existing_owner and existing_owner != owner_email:
            return False
        raw_receipt = data.get("provenance")
        if raw_receipt is not None and verify_output_provenance_receipt(path, raw_receipt) is None:
            return False

        try:
            version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        data["version"] = max(2, version)
        data["promptId"] = prompt_id
        data["workflow"] = str(workflow or "")[:240]
        data["feature"] = str(feature or FEATURE_BY_ID[clean_feature_id].get("title") or clean_feature_id)[:240]
        data["featureId"] = clean_feature_id
        submitted_by = data.get("submittedBy") if isinstance(data.get("submittedBy"), dict) else {}
        data["submittedBy"] = {**submitted_by, "email": owner_email}
        if clean_parameters or "detailParameters" not in data:
            data["detailParameters"] = clean_parameters

        outputs = data.get("outputs") if isinstance(data.get("outputs"), list) else []
        if not any(
            isinstance(output, dict)
            and str(output.get("filename") or "") == filename
            and str(output.get("subfolder") or "") == subfolder
            and str(output.get("type") or "output") == view_type
            for output in outputs
        ):
            outputs = [*outputs, {
                "kind": gallery_media_kind(path) or "",
                "filename": filename,
                "subfolder": subfolder,
                "type": view_type,
            }]
        data["outputs"] = outputs
        if sidecar_exists and isinstance(existing, dict) and data == existing:
            return True
        try:
            atomic_write_json(sidecar_path, data, 0o600)
        except OSError:
            return False
    return True


def gallery_folder_character(subfolder: str) -> dict | None:
    parts = str(subfolder or "").replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "Castle" and parts[1] == "c":
        return character_summary_by_id(parts[2])
    return None


def gallery_folder_action(subfolder: str, filename: str) -> dict | None:
    parts = str(subfolder or "").replace("\\", "/").split("/")
    if len(parts) < 3 or parts[0] != "Castle" or parts[1] != "c":
        return None
    stem = Path(filename).stem
    match = re.match(r"([A-Za-z0-9._-]+)_\d{8}_\d{6}(?:_[A-Fa-f0-9]{4})?", stem)
    action_id = match.group(1) if match else stem.split("_", 1)[0]
    action = CHARACTER_ACTION_BY_ID.get(action_id)
    if action:
        return {"id": action_id, "label": action.get("label") or action_id, "featureId": ""}
    return {"id": safe_path_segment(action_id, "generate"), "label": action_id or "生成", "featureId": ""}


def workflow_feature_id(workflow_id: object) -> str:
    """Return an exact Castle feature id encoded by a first-party workflow id."""
    workflow = str(workflow_id or "").strip()
    if not (workflow.startswith("castle-") and workflow.endswith("-web")):
        return ""
    feature_id = workflow.removeprefix("castle-").removesuffix("-web")
    return feature_id if feature_id in FEATURE_BY_ID else ""


def feature_id_from_output_location(subfolder: str, filename: str) -> str:
    """Infer a first-party feature from Castle's own output namespace."""

    relative = "/".join(
        part for part in (str(subfolder or "").strip("/"), str(filename or ""))
        if part
    )
    for feature_id, prefix in sorted(
        FEATURE_OUTPUT_PREFIXES.items(), key=lambda item: len(item[1]), reverse=True
    ):
        if (
            relative == prefix
            or relative.startswith(f"{prefix}/")
            or relative.startswith(f"{prefix}_")
        ):
            return feature_id if feature_id in FEATURE_BY_ID else ""
    return ""


def gallery_internal_staging_path(subfolder: object) -> bool:
    try:
        parts = Path(str(subfolder or "")).parts
    except (TypeError, ValueError):
        return True
    return CLIPMODE_PHOTOSHOOT_STAGING_FOLDER in parts


def gallery_file_item(
    path: Path,
    view_type: str,
    hidden: set[tuple[str, str, str]],
    prompt_id: str = "disk",
    job_index: dict[str, dict] | None = None,
) -> dict | None:
    extension_kind = gallery_media_kind(path)
    if not extension_kind:
        return None
    base = VIEW_DIRS.get(view_type)
    if base is None:
        return None
    try:
        rel = path.resolve().relative_to(base.resolve())
        stat = path.stat()
    except (OSError, ValueError):
        return None
    filename = rel.name
    subfolder = "" if rel.parent == Path(".") else rel.parent.as_posix()
    if view_type == "output" and gallery_internal_staging_path(subfolder):
        return None
    try:
        validate_view_parts(filename, subfolder, view_type)
    except ValueError:
        return None
    sidecar = load_direct_sidecar(path)
    sidecar_output = sidecar_output_metadata(sidecar, filename, subfolder, view_type)
    # The bytes/path determine media behavior. Sidecar labels are display-only
    # and must not make one file change category after history eviction.
    kind = extension_kind
    prompt_hint = str((sidecar or {}).get("promptId") or prompt_id or "")
    base_params = {"filename": filename, "subfolder": subfolder, "type": view_type}
    if prompt_hint and prompt_hint != "disk":
        base_params["promptId"] = prompt_hint
    params = urllib.parse.urlencode(base_params)
    dl_params = urllib.parse.urlencode({**base_params, "dl": "1"})
    sidecar_settings = (sidecar or {}).get("settings") if isinstance((sidecar or {}).get("settings"), dict) else {}
    sidecar_raw_character = (sidecar or {}).get("character") if isinstance((sidecar or {}).get("character"), dict) else {}
    sidecar_character = gallery_character_summary(sidecar_raw_character) or gallery_character_summary(sidecar_settings)
    sidecar_variant_id = str(
        (sidecar or {}).get("variantId")
        or sidecar_settings.get("variantId")
        or sidecar_settings.get("_characterVariantId")
        or ""
    ).strip()[:160]
    sidecar_visual_class = str(
        (sidecar or {}).get("visualClass")
        or sidecar_raw_character.get("visualClass")
        or sidecar_settings.get("visualClass")
        or sidecar_settings.get("_characterVisualClass")
        or ""
    ).strip().lower()[:80]
    jobs = job_index if isinstance(job_index, dict) else load_job_index()
    indexed = jobs.get(prompt_hint) if isinstance(jobs.get(prompt_hint), dict) else None
    sidecar_owner = submitted_by_email(sidecar)
    direct_server_prompt = prompt_hint.startswith(("local-", "run_"))
    allow_receipt_issue = bool(
        indexed
        and sidecar
        and sidecar_output
        and (
            sidecar_owner == normalize_email(str(indexed.get("email") or ""))
            or (not sidecar_owner and direct_server_prompt)
        )
    )
    provenance = durable_output_provenance(
        path,
        prompt_hint,
        indexed,
        filename=filename,
        subfolder=subfolder,
        view_type=view_type,
        allow_issue=allow_receipt_issue,
    )
    folder_character = gallery_folder_character(subfolder)
    folder_action = gallery_folder_action(subfolder, filename)
    raw_sidecar_action = (sidecar or {}).get("action") if isinstance((sidecar or {}).get("action"), dict) else {}
    sidecar_action = None
    if raw_sidecar_action.get("id") or raw_sidecar_action.get("label"):
        sidecar_action = {
            "id": safe_path_segment(str(raw_sidecar_action.get("id") or ""), ""),
            "label": str(raw_sidecar_action.get("label") or ""),
            "featureId": str(raw_sidecar_action.get("featureId") or ""),
        }
    location_feature_id = feature_id_from_output_location(subfolder, filename)
    workflow = str(
        (sidecar or {}).get("workflow")
        or (f"castle-{location_feature_id}-web" if location_feature_id else "")
    )
    feature = (sidecar or {}).get("feature", "")
    if not feature and location_feature_id:
        feature = str(FEATURE_BY_ID[location_feature_id].get("title") or location_feature_id)
    if folder_action and not feature:
        feature = folder_action.get("label", "")
    feature_id = str(
        provenance.get("featureId")
        or (indexed or {}).get("featureId")
        or (indexed or {}).get("feature")
        or (sidecar or {}).get("featureId")
        or (sidecar_action or {}).get("featureId")
        or workflow_feature_id(workflow)
        or location_feature_id
        or ""
    ).strip()[:160]
    item = {
        "promptId": (sidecar or {}).get("promptId") or prompt_id,
        "kind": kind,
        "renderKind": gallery_render_kind(path, kind),
        "filename": filename,
        "subfolder": subfolder,
        "type": view_type,
        "localHistoryKey": local_history_key(view_type, subfolder, filename),
        "url": f"/api/view?{params}",
        "downloadUrl": f"/api/view?{dl_params}",
        "audioExportUrls": audio_export_urls(base_params, kind),
        "hidden": (view_type, subfolder, filename) in hidden,
        "modified": stat.st_mtime,
        "size": stat.st_size,
        "source": "direct" if sidecar else "disk",
        "workflow": workflow,
        "feature": feature,
        "featureId": feature_id,
        "character": sidecar_character or folder_character,
        "characterAttribution": "sidecar" if sidecar_character else ("folder" if folder_character else ""),
        "provenanceTrusted": False,
        "variantId": sidecar_variant_id,
        "visualClass": normalized_visual_class(
            sidecar_visual_class or (sidecar_character or {}).get("visualClass")
        ),
        "action": sidecar_action or folder_action,
    }
    if provenance:
        item.update(provenance)
    return item


def output_images(
    history: dict,
    hidden: set[tuple[str, str, str]] | None = None,
    job_index: dict[str, dict] | None = None,
) -> list[dict]:
    hidden = hidden or set()
    images = []
    for prompt_id, item in history.items():
        indexed = job_index.get(str(prompt_id)) if isinstance(job_index, dict) else None
        prompt = item.get("prompt") or []
        graph = prompt[2] if len(prompt) > 2 and isinstance(prompt[2], dict) else {}
        meta = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
        workflow_meta = ((meta.get("extra_pnginfo") or {}).get("workflow") or {})
        if not isinstance(workflow_meta, dict):
            workflow_meta = {}
        workflow = (workflow_meta.get("id") or "")
        feature_id = str(
            (indexed or {}).get("featureId")
            or (indexed or {}).get("feature")
            or workflow_feature_id(workflow)
            or ""
        ).strip()[:160]
        detail_parameters = history_generation_parameters(graph, workflow)
        ui_meta = prompt_ui_metadata(meta, indexed)
        character = gallery_character_summary(workflow_meta.get("character"))
        if not character and (ui_meta.get("characterId") or ui_meta.get("characterName")):
            character = gallery_character_summary({"id": ui_meta.get("characterId"), "name": ui_meta.get("characterName")})
        feature = workflow_label(workflow)
        for node in item.get("outputs", {}).values():
            for kind, key in (("image", "images"), ("audio", "audio"), ("video", "gifs"), ("video", "video")):
                for out in node.get(key, []):
                    filename = out["filename"]
                    subfolder = out.get("subfolder", "")
                    view_type = out.get("type", "output")
                    if view_type == "output" and gallery_internal_staging_path(subfolder):
                        continue
                    # Drop entries whose file is gone (deleted here or removed on
                    # disk) so they don't linger as broken thumbnails.
                    base = VIEW_DIRS.get(view_type)
                    modified = 0.0
                    size = 0
                    path = None
                    if base is not None:
                        path = (base / subfolder / filename) if subfolder else (base / filename)
                        try:
                            stat = path.stat()
                        except OSError:
                            continue
                        modified = stat.st_mtime
                        size = stat.st_size
                    provenance = durable_output_provenance(
                        path,
                        str(prompt_id),
                        indexed,
                        filename=filename,
                        subfolder=subfolder,
                        view_type=view_type,
                        allow_issue=isinstance(indexed, dict),
                    ) if path is not None else {}
                    base_params = {"filename": filename, "subfolder": subfolder, "type": view_type, "promptId": prompt_id}
                    params = urllib.parse.urlencode(base_params)
                    dl_params = urllib.parse.urlencode({**base_params, "dl": "1"})
                    folder_character = gallery_folder_character(subfolder)
                    folder_action = gallery_folder_action(subfolder, filename)
                    metadata_action = None
                    if ui_meta.get("actionId") or ui_meta.get("actionLabel"):
                        metadata_action = {"id": ui_meta.get("actionId", ""), "label": ui_meta.get("actionLabel", ""), "featureId": ""}
                    display_feature = feature
                    if folder_action and not display_feature:
                        display_feature = folder_action.get("label", "")
                    canonical_kind = gallery_media_kind(path) if path is not None else None
                    item_kind = canonical_kind or kind
                    if path is not None:
                        persist_output_history_detail(
                            path,
                            prompt_id=str(prompt_id),
                            indexed=indexed,
                            workflow=workflow,
                            feature=display_feature,
                            feature_id=feature_id,
                            parameters=detail_parameters,
                            filename=filename,
                            subfolder=subfolder,
                            view_type=view_type,
                        )
                    gallery_item = {
                        "promptId": prompt_id,
                        "kind": item_kind,
                        "renderKind": gallery_render_kind(path, item_kind) if path is not None else item_kind,
                        "workflow": workflow,
                        "feature": display_feature,
                        "featureId": feature_id,
                        "filename": filename,
                        "subfolder": subfolder,
                        "type": view_type,
                        "localHistoryKey": local_history_key(view_type, subfolder, filename),
                        "url": f"/api/view?{params}",
                        "downloadUrl": f"/api/view?{dl_params}",
                        "audioExportUrls": audio_export_urls(base_params, item_kind),
                        "hidden": (view_type, subfolder, filename) in hidden,
                        "modified": modified,
                        "size": size,
                        "source": "history",
                        "character": character or folder_character,
                        "characterAttribution": "workflow" if character else ("folder" if folder_character else ""),
                        "provenanceTrusted": False,
                        "variantId": str(ui_meta.get("variantId") or ""),
                        "visualClass": normalized_visual_class(ui_meta.get("visualClass")),
                        "action": metadata_action or folder_action,
                    }
                    if provenance:
                        gallery_item.update(provenance)
                    images.append(gallery_item)
    return images


def history_gallery_item_for_view(prompt_id: str, filename: str, subfolder: str, view_type: str) -> dict | None:
    pid = str(prompt_id or "").strip()
    if not prompt_id_hint_valid(pid) or pid.startswith("local-"):
        return None
    try:
        history = request_json(f"/history/{urllib.parse.quote(pid)}")
    except Exception:
        return None
    job_index = load_job_index()
    for item in output_images(history, set(), job_index):
        if gallery_item_key(item) == (view_type, subfolder, filename):
            return item
    return None


def gallery_item_for_continuation(
    prompt_id: str,
    filename: str,
    subfolder: str,
    view_type: str,
) -> dict | None:
    """Return trusted history metadata while keeping the on-disk media kind authoritative."""
    target = resolve_view_path(filename, subfolder, view_type)
    if target is None or not target.is_file():
        return None
    disk_item = gallery_file_item(target, view_type, set(), prompt_id or "disk")
    history_item = history_gallery_item_for_view(prompt_id, filename, subfolder, view_type)
    if history_item is None:
        return disk_item
    if disk_item:
        history_item = {
            **history_item,
            "kind": disk_item.get("kind") or history_item.get("kind"),
            "size": disk_item.get("size") or history_item.get("size"),
            "modified": disk_item.get("modified") or history_item.get("modified"),
        }
    return history_item


def renamed_gallery_sidecar_data(
    existing: dict | None,
    gallery_item: dict | None,
    *,
    prompt_id: str,
    owner_email: str,
    old_filename: str,
    new_filename: str,
    subfolder: str,
    view_type: str,
) -> dict:
    """Build durable attribution for a renamed output.

    ComfyUI history continues to reference the old filename after a filesystem
    rename.  Persist the trusted server-side metadata beside the renamed file so
    future gallery scans and permission checks do not depend on that stale
    history entry.
    """
    data = _json_copy(existing) if isinstance(existing, dict) else {}
    item = gallery_item if isinstance(gallery_item, dict) else {}
    data["version"] = data.get("version") or 1

    prompt_candidates = [data.get("promptId"), prompt_id, item.get("promptId")]
    durable_prompt_id = next((str(value or "").strip() for value in prompt_candidates if prompt_id_hint_valid(str(value or "").strip())), "")
    if durable_prompt_id:
        data["promptId"] = durable_prompt_id
    else:
        data.pop("promptId", None)

    submitted_by = dict(data.get("submittedBy")) if isinstance(data.get("submittedBy"), dict) else {}
    clean_owner = normalize_email(owner_email)
    if clean_owner:
        submitted_by["email"] = clean_owner
    if submitted_by:
        data["submittedBy"] = submitted_by

    for key in ("workflow", "feature", "featureId"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            data[key] = _json_copy(value) if isinstance(value, (dict, list)) else value
    trusted_attribution = (
        item.get("provenanceTrusted") is True
        and str(item.get("characterAttribution") or "") == "server"
    )
    if trusted_attribution:
        for key in ("character", "action", "variantId", "visualClass"):
            value = item.get(key)
            if value not in (None, "", [], {}):
                data[key] = _json_copy(value) if isinstance(value, (dict, list)) else value

    outputs = data.get("outputs") if isinstance(data.get("outputs"), list) else []
    updated_outputs = []
    replaced = False
    for raw in outputs:
        if not isinstance(raw, dict):
            continue
        output = dict(raw)
        if (
            output.get("filename") == old_filename
            and str(output.get("subfolder") or "") == subfolder
            and str(output.get("type") or "output") == view_type
        ):
            output["filename"] = new_filename
            replaced = True
        updated_outputs.append(output)
    if not replaced:
        updated_outputs.append({
            "kind": str(item.get("kind") or GALLERY_MEDIA_KINDS.get(Path(new_filename).suffix.lower()) or ""),
            "filename": new_filename,
            "subfolder": subfolder,
            "type": view_type,
        })
    data["outputs"] = updated_outputs
    return data


def move_file_no_replace(source: Path, destination: Path) -> None:
    """Move a regular file without POSIX rename's overwrite behavior."""
    os.link(source, destination)
    try:
        source.unlink()
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def gallery_snapshot_items(items: list[dict], limit: int, balanced: bool = False) -> list[dict]:
    ordered = sorted(items, key=lambda item: item.get("modified", 0), reverse=True)
    if not balanced or len(ordered) <= limit:
        return ordered[:limit]
    # Keep older video/audio visible when a large image batch fills the window.
    groups: dict[str, list[dict]] = {}
    for item in ordered:
        groups.setdefault(str(item.get("kind") or ""), []).append(item)
    selected = []
    offset = 0
    while len(selected) < limit:
        added = False
        for group in groups.values():
            if offset < len(group):
                selected.append(group[offset])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        offset += 1
    return sorted(selected, key=lambda item: item.get("modified", 0), reverse=True)


def disk_output_items(
    hidden: set[tuple[str, str, str]],
    limit: int,
    job_index: dict[str, dict] | None = None,
    item_filter=None,
    *,
    balanced: bool = False,
    owner_email: str | None = None,
) -> list[dict]:
    if item_filter is not None:
        # A disk-only file with no sidecar has no owner evidence; live history
        # outputs are already merged separately. Do not inspect every unowned
        # training image merely to discard it from an owner-only snapshot.
        paths = (
            (path.with_name(path.name.removesuffix(".castle.json"))
             for path in COMFY_OUTPUT.rglob("*.castle.json"))
            if owner_email else COMFY_OUTPUT.rglob("*")
        )
        items = []
        for path in paths:
            if not path.is_file():
                continue
            item = gallery_file_item(path, "output", hidden, job_index=job_index)
            if item and item_filter(item):
                items.append(item)
        return gallery_snapshot_items(items, limit, balanced)

    # Discover timestamps first and decode metadata only for bounded candidates.
    # Hundreds of thousands of outputs must not all be materialized every poll.
    candidates: dict[str, list[tuple[float, str, Path]]] = {}
    output_root = COMFY_OUTPUT.resolve()
    for directory, directories, filenames in os.walk(COMFY_OUTPUT):
        directories[:] = [name for name in directories if name != CLIPMODE_PHOTOSHOOT_STAGING_FOLDER]
        folder = Path(directory)
        subfolder = folder.relative_to(COMFY_OUTPUT).as_posix()
        if gallery_internal_staging_path(subfolder):
            continue
        for filename in filenames:
            if Path(filename).suffix.lower() not in GALLERY_MEDIA_KINDS:
                continue
            path = folder / filename
            try:
                file_stat = path.lstat()
                if stat.S_ISLNK(file_stat.st_mode):
                    if not path.resolve().is_relative_to(output_root):
                        continue
                    file_stat = path.stat()
                if not stat.S_ISREG(file_stat.st_mode):
                    continue
                validate_view_parts(filename, "" if subfolder == "." else subfolder, "output")
            except (OSError, ValueError):
                continue
            kind = gallery_media_kind(path)
            if not kind:
                continue
            heap = candidates.setdefault(kind if balanced else "all", [])
            candidate = (file_stat.st_mtime, str(path), path)
            if len(heap) < limit:
                heapq.heappush(heap, candidate)
            elif candidate > heap[0]:
                heapq.heapreplace(heap, candidate)
    items = []
    for heap in candidates.values():
        for _timestamp, _name, path in heap:
            item = gallery_file_item(path, "output", hidden, job_index=job_index)
            if item:
                items.append(item)
    return gallery_snapshot_items(items, limit, balanced)


def merged_gallery_items(
    history: dict,
    hidden: set[tuple[str, str, str]],
    limit: int,
    job_index: dict[str, dict] | None = None,
    item_filter=None,
    *,
    balanced: bool = False,
    owner_email: str | None = None,
) -> list[dict]:
    merged: dict[tuple[str, str, str], dict] = {}
    for item in output_images(history, hidden, job_index):
        if item_filter is None or item_filter(item):
            merged[gallery_item_key(item)] = item
    for item in disk_output_items(
        hidden, limit, job_index, item_filter=item_filter,
        balanced=balanced, owner_email=owner_email,
    ):
        merged.setdefault(gallery_item_key(item), item)
    items = list(merged.values())
    return gallery_snapshot_items(items, limit, balanced)


_COMFY_PUBLIC_FIELD_LABELS = {
    "split_conds_to_windows": "條件分段方式",
    "retain_first_frame": "保留首格",
}
_COMFY_PUBLIC_ERROR_TYPES = {
    "bad_linked_input",
    "custom_validation_failed",
    "dependency_cycle",
    "exception_during_inner_validation",
    "invalid_input_type",
    "prompt_outputs_failed_validation",
    "required_input_missing",
    "return_type_mismatch",
    "value_bigger_than_max",
    "value_not_in_list",
    "value_smaller_than_min",
}
_COMFY_SAFE_TECH_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


def _comfy_safe_technical_identifier(value: object) -> str:
    text = str(value or "").strip()
    return text if _COMFY_SAFE_TECH_IDENTIFIER.fullmatch(text) else ""


def comfy_error_payload(raw: str) -> dict | None:
    """Project a Comfy error into a useful, non-sensitive browser envelope.

    Comfy validation details may contain input values, filesystem paths, model
    names, or exception traces.  Only stable error types and identifier-shaped
    input names cross this boundary.  A validation HTTP response means Comfy
    rejected the graph before it entered the prompt queue.
    """

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    root_error = data.get("error") if isinstance(data.get("error"), dict) else {}
    node_errors = data.get("node_errors") if isinstance(data.get("node_errors"), dict) else {}
    root_type = _comfy_safe_technical_identifier(root_error.get("type"))
    root_message = str(root_error.get("message") or "").strip()
    is_validation = (
        root_type == "prompt_outputs_failed_validation"
        or root_message == "Prompt outputs failed validation"
    )
    if not is_validation and not node_errors:
        return None

    input_names: list[str] = []
    reason_types: list[str] = []
    for node in list(node_errors.values())[:24]:
        if not isinstance(node, dict):
            continue
        errors = node.get("errors") if isinstance(node.get("errors"), list) else []
        for item in errors[:24]:
            if not isinstance(item, dict):
                continue
            reason_type = _comfy_safe_technical_identifier(item.get("type"))
            if reason_type in _COMFY_PUBLIC_ERROR_TYPES and reason_type not in reason_types:
                reason_types.append(reason_type)
            extra = item.get("extra_info") if isinstance(item.get("extra_info"), dict) else {}
            input_name = _comfy_safe_technical_identifier(extra.get("input_name"))
            if not input_name:
                # Comfy's required-input error uses the bare input name as its
                # details value.  Do not accept arbitrary detail strings here.
                input_name = _comfy_safe_technical_identifier(item.get("details"))
            if input_name and input_name not in input_names:
                input_names.append(input_name)

    display_names = [
        _COMFY_PUBLIC_FIELD_LABELS.get(name, name)
        for name in input_names[:8]
    ]
    field_text = "、".join(display_names)
    if is_validation and input_names and reason_types == ["required_input_missing"]:
        message = (
            f"系統工作流缺少目前節點要求的內部設定（{field_text}）。"
            "不是你的 Prompt 寫錯，這次沒有開始生成。"
        )
    elif is_validation and input_names:
        message = (
            f"系統工作流的內部設定與目前節點版本不相容（{field_text}）。"
            "不是你的 Prompt 寫錯，這次沒有開始生成。"
        )
    elif is_validation:
        message = (
            "系統工作流與目前節點版本不相容。"
            "不是你的 Prompt 寫錯，這次沒有開始生成。"
        )
    else:
        message = "生成服務拒絕了這次工作流設定，這次沒有開始生成。"

    technical = [
        "Prompt outputs failed validation"
        if is_validation
        else (root_type or "comfy_validation_error")
    ]
    technical.extend(reason_types[:8])
    technical.extend(input_names[:8])
    return {
        "error_code": "comfy_prompt_validation" if is_validation else "comfy_validation_error",
        "message": message,
        "submission_state": "not_submitted",
        "technical_detail": " · ".join(technical)[:600],
    }


def comfy_error_message(raw: str) -> str:
    payload = comfy_error_payload(raw)
    return (
        str(payload["message"])
        if payload is not None
        else "生成服務回傳無法辨識的錯誤。"
    )


def comfy_execution_error_payload(message: object) -> dict:
    """Turn a post-submission Comfy failure into stable, human-facing copy."""

    raw = message if isinstance(message, dict) else {}
    exception_type = _comfy_safe_technical_identifier(raw.get("exception_type")) or "execution_error"
    node_type = _comfy_safe_technical_identifier(raw.get("node_type"))
    detail = str(raw.get("exception_message") or "").strip().lower()
    combined = f"{exception_type.lower()} {detail}"
    if any(token in combined for token in ("out of memory", "cuda oom", "memory_headroom_low")):
        code = "generation_memory_exhausted"
        public = "生成時記憶體不足，系統已停止這次工作並保留輸入；稍後可直接重試。"
    elif "memory_recovery_failed" in combined:
        code = "memory_recovery_failed"
        public = "系統無法騰出 H3 所需記憶體，這次尚未開始生成；不需要重新上傳素材。"
    elif any(token in combined for token in ("timed out", "timeout")):
        code = "generation_timeout"
        public = "生成服務等待過久而停止，沒有確認到完整輸出。"
    elif any(token in combined for token in ("no such file", "file not found", "cannot find input", "input file")):
        code = "generation_input_missing"
        public = "找不到這次工作使用的輸入素材；請重新選取素材後再試。"
    elif any(token in combined for token in ("model not found", "checkpoint", "lora") ):
        code = "generation_model_unavailable"
        public = "這個工作流需要的模型目前不可用；不是 Prompt 寫錯。"
    elif any(token in combined for token in ("node not found", "class_type", "cannot import")):
        code = "generation_node_unavailable"
        public = "這個工作流需要的節點目前不可用；不是 Prompt 寫錯。"
    elif any(token in combined for token in ("shape mismatch", "size mismatch", "invalid shape", "schema")):
        code = "generation_workflow_incompatible"
        public = "工作流與目前模型或節點版本不相容；不是 Prompt 寫錯。"
    else:
        code = "generation_execution_failed"
        public = "生成已開始但沒有成功完成；系統已保留任務資料供檢查。"
    technical = " · ".join(value for value in (exception_type, node_type) if value)[:200]
    return {
        "error_code": code,
        "message": public,
        "submission_state": "submitted",
        "technical_detail": technical,
    }


def workflow_label(workflow_id: str, node_type: str = "") -> str:
    if workflow_id.startswith("castle-") and workflow_id.endswith("-web"):
        feature_id = workflow_id.removeprefix("castle-").removesuffix("-web")
        feature = FEATURE_BY_ID.get(feature_id)
        if feature:
            return str(feature.get("title") or feature_id)
        return feature_id
    if node_type:
        return node_type
    return workflow_id or "ComfyUI"


def prompt_workflow_id(extra_data: dict | None) -> str:
    if not isinstance(extra_data, dict):
        return ""
    return (((extra_data.get("extra_pnginfo") or {}).get("workflow") or {}).get("id") or "")


def prompt_ui_metadata(extra_data: dict | None, indexed: dict | None = None) -> dict:
    """Return only display-safe character/action labels for consumer job surfaces."""
    extra = extra_data if isinstance(extra_data, dict) else {}
    index = indexed if isinstance(indexed, dict) else {}
    workflow_meta = ((extra.get("extra_pnginfo") or {}).get("workflow") or {})
    if not isinstance(workflow_meta, dict):
        workflow_meta = {}
    character = workflow_meta.get("character") if isinstance(workflow_meta.get("character"), dict) else {}
    # Once a prompt has a Castle job-index entry, that server snapshot is the
    # authority. Never let client-carried workflow metadata override it.
    if index:
        character_id = str(index.get("characterId") or "").strip()[:128]
        character_name = str(index.get("characterName") or "").strip()[:160]
        action_id = safe_path_segment(str(index.get("actionId") or ""), "")
        variant_id = str(index.get("variantId") or "").strip()[:160]
        visual_class = normalized_visual_class(index.get("visualClass"))
    else:
        character_id = str(extra.get("characterId") or character.get("id") or "").strip()[:128]
        character_name = str(extra.get("characterName") or character.get("name") or "").strip()[:160]
        action_id = safe_path_segment(str(extra.get("actionId") or ""), "")
        variant_id = str(extra.get("variantId") or workflow_meta.get("variantId") or "").strip()[:160]
        visual_class = normalized_visual_class(
            extra.get("visualClass") or character.get("visualClass")
        )
    action = CHARACTER_ACTION_BY_ID.get(action_id) or {}
    action_label = str((index.get("actionLabel") if index else extra.get("actionLabel")) or action.get("label") or action_id).strip()[:160]
    return {
        "characterId": character_id,
        "characterName": character_name,
        "actionId": action_id,
        "actionLabel": action_label,
        "variantId": variant_id,
        "visualClass": visual_class,
    }


def job_scope(query: str, user: dict | None) -> tuple[str, dict | None]:
    params = urllib.parse.parse_qs(query or "", keep_blank_values=True)
    scope = str((params.get("scope") or ["own"])[0] or "own").strip().lower()
    if scope not in ("own", "all"):
        raise ValueError("scope 必須是 own 或 all")
    if scope == "all" and not (user and user.get("role") == "admin"):
        raise PermissionError("全部任務僅限管理員")
    if scope == "own" and user and user.get("role") == "admin":
        return scope, {**user, "role": "user"}
    return scope, user


def decorate_job_metadata(job: dict, job_index: dict[str, dict]) -> dict:
    prompt_id = str(job.get("promptId") or "")
    indexed = job_index.get(prompt_id) if prompt_id else {}
    allowed = {
        "promptId", "number", "state", "position", "workflow", "feature", "source",
        "nodeCount", "outputCount", "outputNodes", "nodeTypes", "progress", "created",
        "updated", "lane", "taskId", "message", "type", "node", "timestamp", "error",
    }
    summary = {key: value for key, value in job.items() if key in allowed}
    trusted_feature_id = str(
        (indexed or {}).get("featureId") or (indexed or {}).get("feature") or ""
    ).strip()
    if trusted_feature_id in FEATURE_BY_ID:
        summary["featureId"] = trusted_feature_id
    return {**summary, **prompt_ui_metadata(job, indexed)}


_PROMPT_NODE_FEATURE_IDS = (
    ("MiniMaxH3ReferenceToVideo", "h3-reference-video"),
    ("MiniMaxH3ImageToVideo", "h3-image-video"),
    ("MiniMaxH3TextToVideo", "h3-text-video"),
    ("WanFirstLastFrameToVideo", "first-last-frame"),
    ("WanSCAILToVideo", "dance"),
    ("WanImageToVideo", "img2video"),
    ("Wav2Lip", "lipsync"),
    ("ReActorFaceSwap", "face-swap"),
)


def infer_prompt_feature_id(prompt: dict) -> str:
    if not isinstance(prompt, dict):
        return ""
    class_names = {
        str(node.get("class_type") or "")
        for node in prompt.values()
        if isinstance(node, dict)
    }
    for class_name, feature_id in _PROMPT_NODE_FEATURE_IDS:
        if class_name in class_names and feature_id in FEATURE_BY_ID:
            return feature_id
    return ""


def infer_prompt_label(prompt: dict, workflow_id: str = "") -> str:
    if workflow_id:
        return workflow_label(workflow_id)
    feature_id = infer_prompt_feature_id(prompt)
    if feature_id:
        return str(FEATURE_BY_ID[feature_id].get("title") or feature_id)
    class_names = [str(node.get("class_type") or "") for node in prompt.values() if isinstance(node, dict)]
    for class_name in class_names:
        if class_name == "CastleBreezyVoiceTTS":
            return "打字即說"
        if class_name == "CastleBreezyVoiceClone":
            return "聲音克隆"
        if class_name == "F5TTSAudioInputs":
            return "聲音克隆"
        if class_name == "Wav2Lip":
            return "影片對嘴"
        if class_name == "ReActorFaceSwap":
            return "影片換臉"
        if class_name in ("WanImageToVideo", "WanFunInpaintToVideo", "WanSCAILToVideo"):
            return "影片生成"
        if class_name in ("SaveImage", "VAEDecode"):
            return "圖片生成"
    return class_names[0] if class_names else "外部 ComfyUI"


def queue_entry_job(entry, state: str, position: int = 0) -> dict:
    """Convert a ComfyUI queue tuple to a UI-safe job summary.

    ComfyUI queue entries are normally:
      [queue_number, prompt_id, prompt_graph, extra_data, outputs_to_execute]
    External clients can omit Castle workflow metadata, so fall back to node
    class names instead of assuming every prompt came from this web UI.
    """
    values = list(entry) if isinstance(entry, (list, tuple)) else []
    number = values[0] if len(values) > 0 else None
    prompt_id = str(values[1] if len(values) > 1 else "")
    prompt = values[2] if len(values) > 2 and isinstance(values[2], dict) else {}
    extra_data = values[3] if len(values) > 3 and isinstance(values[3], dict) else {}
    outputs = values[4] if len(values) > 4 and isinstance(values[4], list) else []
    workflow = prompt_workflow_id(extra_data)
    node_types = {
        str(node_id): str(node.get("class_type") or "")
        for node_id, node in prompt.items()
        if isinstance(node, dict)
    }
    source = "castle" if workflow.startswith("castle-") or extra_data.get("client_id") == CLIENT_ID else "external"
    return {
        "promptId": prompt_id,
        "number": number,
        "state": state,
        "position": position,
        "workflow": workflow,
        "feature": infer_prompt_label(prompt, workflow),
        "source": source,
        "nodeCount": len(prompt),
        "outputCount": len(outputs),
        "outputNodes": [str(item) for item in outputs],
        "nodeTypes": node_types,
        **prompt_ui_metadata(extra_data),
    }


def queue_job_items(queue: dict) -> list[dict]:
    jobs = []
    for entry in queue.get("queue_running") or []:
        jobs.append(queue_entry_job(entry, "running", 0))
    for pos, entry in enumerate(queue.get("queue_pending") or [], start=1):
        jobs.append(queue_entry_job(entry, "pending", pos))
    return jobs


def history_error_items(history: dict, limit: int = 8) -> list[dict]:
    """Return recent ComfyUI failures without prompt text or workflow inputs."""
    errors = []
    for prompt_id, item in history.items():
        status = item.get("status") or {}
        if status.get("status_str") != "error":
            continue
        prompt = item.get("prompt") or []
        meta = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
        workflow = (((meta.get("extra_pnginfo") or {}).get("workflow") or {}).get("id") or "")
        message = next(
            (m[1] for m in reversed(status.get("messages") or []) if m and m[0] == "execution_error"),
            {},
        )
        if not isinstance(message, dict):
            message = {}
        presented = comfy_execution_error_payload(message)
        timestamp = int(message.get("timestamp") or (meta.get("create_time") or 0))
        errors.append(
            {
                "promptId": prompt_id,
                "workflow": workflow,
                "feature": workflow_label(workflow, str(message.get("node_type") or "")),
                "node": str(message.get("node_type") or ""),
                "type": presented["error_code"],
                "message": presented["message"],
                "errorCode": presented["error_code"],
                "submissionState": presented["submission_state"],
                "technicalDetail": presented["technical_detail"],
                "timestamp": timestamp,
                **prompt_ui_metadata(meta),
            }
        )
    errors.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return errors[:limit]


def history_job_items(history: dict, job_index: dict[str, dict], limit: int = 200) -> list[dict]:
    """Build UI-safe terminal summaries without prompt graphs or owner identity."""
    jobs = []
    for prompt_id, item in history.items():
        if not isinstance(item, dict):
            continue
        prompt = item.get("prompt") or []
        graph = prompt[2] if len(prompt) > 2 and isinstance(prompt[2], dict) else {}
        meta = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        workflow = prompt_workflow_id(meta)
        status_str = str(status.get("status_str") or "").strip().lower()
        message_types = {
            str(message[0] or "").strip().lower()
            for message in (status.get("messages") or [])
            if isinstance(message, (list, tuple)) and message
        }
        if status_str in {"cancelled", "canceled"} or message_types & {"execution_cancelled", "execution_canceled"}:
            state = "cancelled"
        elif status_str in {"error", "interrupted"} or "execution_interrupted" in message_types:
            state = "error"
        else:
            state = "completed"
        timestamp = 0
        for message in reversed(status.get("messages") or []):
            data = message[1] if isinstance(message, (list, tuple)) and len(message) > 1 and isinstance(message[1], dict) else {}
            timestamp = int(data.get("timestamp") or timestamp or 0)
            if timestamp:
                break
        indexed = job_index.get(str(prompt_id)) if str(prompt_id) in job_index else {}
        timestamp = timestamp or int(float((indexed or {}).get("ts") or 0))
        source = "castle" if workflow.startswith("castle-") else "external"
        jobs.append({
            "promptId": str(prompt_id),
            "state": state,
            "workflow": workflow,
            "feature": infer_prompt_label(graph, workflow),
            "source": source,
            "timestamp": timestamp,
            **prompt_ui_metadata(meta, indexed),
        })
    jobs.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return jobs[:limit]


def gallery_terminal_job_items(images: list[dict], job_index: dict[str, dict]) -> list[dict]:
    jobs: dict[str, dict] = {}
    for item in images:
        prompt_id = str(item.get("promptId") or "")
        if not prompt_id or prompt_id == "disk" or prompt_id in jobs:
            continue
        character = item.get("character") if isinstance(item.get("character"), dict) else {}
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        indexed = job_index.get(prompt_id) if prompt_id in job_index else {}
        jobs[prompt_id] = {
            "promptId": prompt_id,
            "state": "completed",
            "workflow": str(item.get("workflow") or ""),
            "feature": str(item.get("feature") or "生成"),
            "source": "castle" if prompt_id in job_index else "external",
            "timestamp": int(float(item.get("modified") or (indexed or {}).get("ts") or 0)),
            "characterId": str(character.get("id") or (indexed or {}).get("characterId") or "")[:128],
            "characterName": str(character.get("name") or (indexed or {}).get("characterName") or "")[:160],
            "actionId": str(action.get("id") or (indexed or {}).get("actionId") or "")[:128],
            "actionLabel": str(action.get("label") or (indexed or {}).get("actionLabel") or "")[:160],
        }
    return list(jobs.values())


def _redact_prompt_detail_path(value: str) -> str:
    if not (value.startswith("/") or value.startswith("~/")):
        return value
    basename = value.rstrip("/").rsplit("/", 1)[-1]
    return basename or "…"


def _primitive_prompt_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            value = _redact_prompt_detail_path(value)
            if len(value) > 4000:
                return value[:3997] + "..."
        return value
    if isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) or v is None for v in value):
        return [_redact_prompt_detail_path(v) if isinstance(v, str) else v for v in value[:50]]
    return None


_GENERATION_PARAMETER_LABELS = {
    "prompt": "Prompt",
    "negativePrompt": "Negative prompt",
    "seed": "Seed",
    "dimensions": "尺寸",
    "frames": "影格數",
    "fps": "FPS",
    "steps": "步數",
    "cfg": "CFG",
    "sampler": "Sampler",
    "scheduler": "Scheduler",
}


def _prompt_graph_reference(value, graph: dict) -> str:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[1], int)
    ):
        node_id = str(value[0])
        if node_id in graph:
            return node_id
    return ""


def _conditioning_text_source(
    graph: dict,
    node_id: str,
    polarity: str,
    visited: set[str] | None = None,
) -> tuple[str, str] | None:
    """Follow one conditioning branch to its exact CLIP text input.

    Positive and negative branches are followed separately.  If a graph does
    not expose an unambiguous path, no semantic prompt label is invented; the
    raw primitive node inputs remain available in the detail drawer.
    """
    seen = set(visited or ())
    if not node_id or node_id in seen:
        return None
    seen.add(node_id)
    node = graph.get(node_id)
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    if str(node.get("class_type") or "") == "CLIPTextEncode":
        text = inputs.get("text")
        if isinstance(text, str) and text:
            return node_id, _primitive_prompt_value(text)
        return None

    preferred = (
        ("positive", "conditioning", "cond", "guider")
        if polarity == "positive"
        else ("negative", "conditioning", "cond", "guider")
    )
    excluded = "negative" if polarity == "positive" else "positive"
    ordered_keys = [key for key in preferred if key in inputs]
    ordered_keys.extend(
        key for key, value in inputs.items()
        if key not in ordered_keys
        and key != excluded
        and _prompt_graph_reference(value, graph)
    )
    for key in ordered_keys:
        parent_id = _prompt_graph_reference(inputs.get(key), graph)
        if not parent_id:
            continue
        result = _conditioning_text_source(graph, parent_id, polarity, seen)
        if result is not None:
            return result
    return None


def _unique_graph_values(graph: dict, class_types: set[str], input_names: tuple[str, ...]) -> list:
    values = []
    for node in graph.values():
        if not isinstance(node, dict) or str(node.get("class_type") or "") not in class_types:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for name in input_names:
            value = inputs.get(name)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            if value not in values:
                values.append(value)
    return values


def _generation_parameter(parameter_id: str, value) -> dict | None:
    if value in (None, "", [], {}):
        return None
    return {
        "id": parameter_id,
        "label": _GENERATION_PARAMETER_LABELS.get(parameter_id, parameter_id),
        "value": value,
    }


def history_generation_parameters(graph: dict, workflow_id: object = "") -> list[dict]:
    """Extract a small, exact human-readable summary from a prompt graph."""
    if not isinstance(graph, dict):
        return []
    feature_id = workflow_feature_id(workflow_id)
    sampler_types = {"KSampler", "SamplerCustom", "SamplerCustomAdvanced"}
    prompt_source = None
    negative_source = None
    native_h3_conditioning_types = {
        "MiniMaxH3TextToVideo",
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3ReferenceToVideo",
    }
    # Native H3 conditioning carries the exact compiled user prompt directly;
    # it does not pass through CLIPTextEncode.  Only these explicit node/input
    # names are interpreted so unrelated custom-node strings are never guessed
    # to be prompts.
    for node_id, node in graph.items():
        if not isinstance(node, dict) or str(node.get("class_type") or "") not in native_h3_conditioning_types:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        prompt_value = inputs.get("prompt")
        if prompt_source is None and isinstance(prompt_value, str) and prompt_value:
            prompt_source = (str(node_id), _primitive_prompt_value(prompt_value))
        for negative_key in ("negative_prompt", "negative"):
            negative_value = inputs.get(negative_key)
            if negative_source is None and isinstance(negative_value, str) and negative_value:
                negative_source = (f"{node_id}:{negative_key}", _primitive_prompt_value(negative_value))
    for node in graph.values():
        if not isinstance(node, dict) or str(node.get("class_type") or "") not in sampler_types:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if prompt_source is None:
            prompt_source = _conditioning_text_source(
                graph, _prompt_graph_reference(inputs.get("positive"), graph), "positive",
            )
        if negative_source is None:
            negative_source = _conditioning_text_source(
                graph, _prompt_graph_reference(inputs.get("negative"), graph), "negative",
            )

    clip_texts = []
    for node_id, node in graph.items():
        if not isinstance(node, dict) or str(node.get("class_type") or "") != "CLIPTextEncode":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        value = inputs.get("text")
        if isinstance(value, str) and value:
            clip_texts.append((str(node_id), _primitive_prompt_value(value)))
    if prompt_source is None and len(clip_texts) == 1:
        prompt_source = clip_texts[0]
    elif prompt_source is None and len(clip_texts) == 2:
        prompt_source, negative_source = clip_texts
    if negative_source is not None and prompt_source is not None and negative_source[0] == prompt_source[0]:
        negative_source = None

    parameters = []
    prompt = _generation_parameter("prompt", prompt_source[1] if prompt_source else "")
    if prompt:
        if feature_id == "img2img":
            prompt["label"] = "修改指令"
        parameters.append(prompt)
    negative = _generation_parameter("negativePrompt", negative_source[1] if negative_source else "")
    if negative:
        parameters.append(negative)

    dimension_types = {
        "EmptyLatentImage", "EmptySD3LatentImage", "CreateVideo",
        "WanImageToVideo", "WanFirstLastFrameToVideo", "WanSCAILToVideo",
        "MiniMaxH3TextToVideo", "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo",
    }
    dimensions = []
    for node in graph.values():
        if not isinstance(node, dict) or str(node.get("class_type") or "") not in dimension_types:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        width, height = inputs.get("width"), inputs.get("height")
        if (
            isinstance(width, int) and not isinstance(width, bool) and width > 0
            and isinstance(height, int) and not isinstance(height, bool) and height > 0
        ):
            dimensions.append(f"{width} × {height}")
    dimensions = list(dict.fromkeys(dimensions))

    video_types = dimension_types | {"VHS_VideoCombine", "SaveAnimatedWEBP", "SaveVideo"}
    sampler_values = {
        "seed": _unique_graph_values(graph, sampler_types | {"RandomNoise"}, ("seed", "noise_seed")),
        "steps": _unique_graph_values(graph, sampler_types | {"BasicScheduler"}, ("steps",)),
        "cfg": _unique_graph_values(graph, sampler_types | {"CFGGuider"}, ("cfg",)),
        "sampler": _unique_graph_values(graph, sampler_types | {"KSamplerSelect"}, ("sampler_name",)),
        "scheduler": _unique_graph_values(graph, sampler_types | {"BasicScheduler"}, ("scheduler",)),
        "frames": _unique_graph_values(graph, video_types, ("length", "num_frames", "frames")),
        "fps": _unique_graph_values(graph, video_types, ("frame_rate", "fps")),
    }
    ordered_values = {
        "seed": [str(value) for value in sampler_values["seed"]],
        "dimensions": dimensions,
        "frames": sampler_values["frames"],
        "fps": sampler_values["fps"],
        "steps": sampler_values["steps"],
        "cfg": sampler_values["cfg"],
        "sampler": sampler_values["sampler"],
        "scheduler": sampler_values["scheduler"],
    }
    for parameter_id, values in ordered_values.items():
        if not values:
            continue
        value = values[0] if len(values) == 1 else " 、".join(str(item) for item in values)
        parameter = _generation_parameter(parameter_id, value)
        if parameter:
            parameters.append(parameter)
    return parameters


def embedded_media_prompt_graph(path: Path) -> dict:
    """Read ComfyUI's embedded prompt graph without trusting arbitrary metadata."""

    suffix = path.suffix.lower()
    raw_prompt = None
    if suffix in EMBEDDED_PROMPT_IMAGE_EXTENSIONS:
        try:
            from PIL import Image

            with Image.open(path) as image:
                raw_prompt = image.info.get("prompt")
        except (ImportError, OSError, ValueError):
            return {}
    elif suffix in EMBEDDED_PROMPT_VIDEO_EXTENSIONS:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return {}
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v", "error",
                    "-show_entries", "format_tags=prompt",
                    "-of", "json",
                    str(path),
                ],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode != 0 or len(result.stdout or b"") > MAX_EMBEDDED_PROMPT_BYTES:
            return {}
        try:
            probe = json.loads(result.stdout or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        tags = (probe.get("format") or {}).get("tags") if isinstance(probe, dict) else {}
        raw_prompt = tags.get("prompt") if isinstance(tags, dict) else None
    else:
        return {}

    if isinstance(raw_prompt, bytes):
        try:
            raw_prompt = raw_prompt.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not isinstance(raw_prompt, str):
        return {}
    if len(raw_prompt.encode("utf-8")) > MAX_EMBEDDED_PROMPT_BYTES:
        return {}
    try:
        raw_graph = json.loads(raw_prompt)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw_graph, dict) or len(raw_graph) > 4096:
        return {}

    graph = {}
    for raw_node_id, raw_node in raw_graph.items():
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node_id)[:128]
        class_type = str(raw_node.get("class_type") or "")[:240]
        inputs = raw_node.get("inputs") if isinstance(raw_node.get("inputs"), dict) else {}
        if node_id and class_type:
            graph[node_id] = {"class_type": class_type, "inputs": inputs}
    return graph


def embedded_prompt_input_names(graph: dict) -> list[str]:
    names = []
    for node in graph.values():
        if not isinstance(node, dict) or str(node.get("class_type") or "") not in {
            "LoadImage", "LoadVideo", "VHS_LoadVideo", "LoadAudio",
        }:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for key in ("image", "video", "audio"):
            value = inputs.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            basename = Path(value.replace("\\", "/")).name[:240]
            if basename and basename not in names:
                names.append(basename)
    return names[:20]


def history_prompt_detail(prompt_id: str, history_item: dict) -> dict:
    prompt = history_item.get("prompt") or []
    graph = prompt[2] if len(prompt) > 2 and isinstance(prompt[2], dict) else {}
    meta = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
    workflow = (((meta.get("extra_pnginfo") or {}).get("workflow") or {}).get("id") or "")
    nodes = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        inputs = []
        for key, value in (node.get("inputs") or {}).items():
            primitive = _primitive_prompt_value(value)
            if primitive is None:
                continue
            if key in {"seed", "noise_seed"} and isinstance(primitive, int) and not isinstance(primitive, bool):
                # Preserve uint64 seeds through JSON/JavaScript without Number
                # rounding.  The human summary uses the same decimal string.
                primitive = str(primitive)
            inputs.append({"name": key, "value": primitive})
        nodes.append(
            {
                "id": str(node_id),
                "type": str(node.get("class_type") or ""),
                "inputs": inputs,
            }
        )
    nodes.sort(key=lambda item: (not item["id"].isdigit(), int(item["id"]) if item["id"].isdigit() else item["id"]))
    outputs = []
    for node in (history_item.get("outputs") or {}).values():
        for kind, key in (("image", "images"), ("audio", "audio"), ("video", "gifs"), ("video", "video")):
            for out in node.get(key, []):
                outputs.append(
                    {
                        "kind": kind,
                        "filename": out.get("filename", ""),
                        "subfolder": out.get("subfolder", ""),
                        "type": out.get("type", "output"),
                    }
                )
    status = history_item.get("status") or {}
    feature_id = workflow_feature_id(workflow)
    parameters = history_generation_parameters(graph, workflow)
    if feature_id == CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID:
        # The Comfy graph contains either a private canonical-profile filename
        # or a server-owned anime trigger/model binding. The browser gets the
        # original four user controls and one friendly workflow node, never
        # those compiled graph internals.
        public = meta.get("castlePublicSettings")
        public = public if isinstance(public, dict) else {}
        safe_prompt = str(public.get("prompt") or "")[:4_000]
        safe_negative = str(public.get("negative_prompt") or "")[:4_000]
        safe_aspect = str(public.get("aspect_ratio") or "")
        if safe_aspect not in CLIPMODE_CHARACTER_PHOTOSHOOT_ASPECTS:
            safe_aspect = ""
        safe_seed = public.get("seed")
        if isinstance(safe_seed, bool) or not isinstance(safe_seed, int) or not 0 <= safe_seed <= 2_147_483_647:
            safe_seed = None
        parameters = []
        for parameter_id, label, value in (
            ("prompt", "Prompt", safe_prompt),
            ("negativePrompt", "Negative prompt", safe_negative),
            ("dimensions", "尺寸", safe_aspect.replace("x", " × ") if safe_aspect else ""),
            ("seed", "Seed", str(safe_seed) if safe_seed is not None else ""),
        ):
            if value:
                parameters.append({"id": parameter_id, "label": label, "value": value})
        public_visual_class = prompt_ui_metadata(meta).get("visualClass")
        identity_route_label = (
            "SDXL + 角色卡原圖身份鎖定"
            if public_visual_class == "realistic"
            else "SDXL + 角色 LoRA"
        )
        nodes = [{
            "id": "photoshoot",
            "type": f"角色身份寫真（{identity_route_label} · Beta）",
            "inputs": [
                {"name": "prompt", "value": safe_prompt},
                {"name": "negative_prompt", "value": safe_negative},
                {"name": "aspect_ratio", "value": safe_aspect},
                {"name": "seed", "value": str(safe_seed) if safe_seed is not None else ""},
            ],
        }]
    return {
        "promptId": prompt_id,
        "workflow": workflow,
        "featureId": feature_id,
        "feature": workflow_label(workflow),
        "status": status.get("status_str") or "",
        "completed": bool(status.get("completed")),
        "created": int(meta.get("create_time") or 0),
        "parameters": parameters,
        "nodes": nodes,
        "outputs": outputs,
        **prompt_ui_metadata(meta),
    }


def embedded_media_history_detail(
    path: Path,
    *,
    prompt_id: str,
    filename: str,
    subfolder: str,
    view_type: str,
) -> dict | None:
    """Build a safe detail view from metadata retained inside one media file."""

    if not path.is_file():
        return None
    graph = embedded_media_prompt_graph(path)
    if not graph:
        return None
    feature_id = (
        feature_id_from_output_location(subfolder, filename)
        or infer_prompt_feature_id(graph)
    )
    workflow = f"castle-{feature_id}-web" if feature_id else ""
    try:
        created = int(path.stat().st_mtime)
    except OSError:
        created = 0
    detail = history_prompt_detail(
        prompt_id or "disk",
        {
            "prompt": [
                0,
                prompt_id or "disk",
                graph,
                {
                    "create_time": created,
                    "extra_pnginfo": {"workflow": {"id": workflow}},
                },
            ],
            "outputs": {},
            "status": {"status_str": "embedded_metadata", "completed": True},
        },
    )
    if feature_id:
        detail["featureId"] = feature_id
        detail["workflow"] = workflow
        detail["feature"] = str(FEATURE_BY_ID[feature_id].get("title") or feature_id)
    else:
        detail["feature"] = infer_prompt_label(graph)
    input_names = embedded_prompt_input_names(graph)
    if input_names:
        input_parameter = {
            "id": "inputMedia",
            "label": "輸入素材",
            "value": input_names[0] if len(input_names) == 1 else input_names,
        }
        insert_at = 1 if detail.get("parameters") and detail["parameters"][0].get("id") == "prompt" else 0
        detail["parameters"].insert(insert_at, input_parameter)
    detail["outputs"] = [{
        "kind": gallery_media_kind(path) or "",
        "filename": filename,
        "subfolder": subfolder,
        "type": view_type,
    }]
    detail["metadataSource"] = "embedded"
    return detail


def auth_config() -> dict | None:
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def atomic_write_json(path: Path, data, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if mode is not None:
            tmp.chmod(mode)
        os.replace(tmp, path)
        if mode is not None:
            path.chmod(mode)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def password_hash(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, AUTH_ITERATIONS)
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": AUTH_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def verify_password(password: str, config: dict) -> bool:
    try:
        salt = base64.b64decode(config["salt"])
        expected = base64.b64decode(config["hash"])
        iterations = int(config.get("iterations", AUTH_ITERATIONS))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def user_password_hash(password: str) -> str:
    config = password_hash(password)
    return "pbkdf2_sha256${iterations}${salt}${hash}".format(**config)


def verify_user_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        scheme, iterations, salt, digest = str(encoded).split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        return verify_password(password, {"iterations": int(iterations), "salt": salt, "hash": digest})
    except Exception:
        return False


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def seed_users() -> dict[str, dict]:
    """No identities are bundled; first-run local setup creates the admin session."""
    return {}


def normalize_user_record(email: str, record: dict) -> dict:
    now = time.time()
    role = str(record.get("role") or "user")
    if role not in ("admin", "user"):
        role = "user"
    return {
        "displayName": str(record.get("displayName") or email),
        "role": role,
        "disabled": bool(record.get("disabled", False)),
        "passwordHash": record.get("passwordHash") if record.get("passwordHash") else None,
        "mustSetPassword": bool(record.get("mustSetPassword", False)),
        "created": float(record.get("created") or now),
        "updated": float(record.get("updated") or now),
    }


def save_users(users: dict[str, dict]) -> None:
    clean = {
        normalize_email(email): normalize_user_record(normalize_email(email), record)
        for email, record in users.items()
        if normalize_email(email) and isinstance(record, dict)
    }
    atomic_write_json(USERS_PATH, {"version": 1, "updated": time.time(), "users": clean}, 0o600)
    try:
        mtime = USERS_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    _USERS_CACHE["mtime"] = mtime
    _USERS_CACHE["users"] = clean


def load_users() -> dict[str, dict]:
    with _USERS_LOCK:
        try:
            mtime = USERS_PATH.stat().st_mtime_ns
        except FileNotFoundError:
            users = seed_users()
            save_users(users)
            return dict(users)
        cached_users = _USERS_CACHE.get("users")
        if _USERS_CACHE.get("mtime") == mtime and isinstance(cached_users, dict):
            return dict(cached_users)
        try:
            data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        raw_users = data.get("users") if isinstance(data, dict) else {}
        users = {
            normalize_email(email): normalize_user_record(normalize_email(email), record)
            for email, record in (raw_users or {}).items()
            if normalize_email(email) and isinstance(record, dict)
        }
        _USERS_CACHE["mtime"] = mtime
        _USERS_CACHE["users"] = users
        return dict(users)


def user_for_email(email: str) -> dict | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    user = load_users().get(normalized)
    if user is None:
        return None
    result = dict(user)
    result["email"] = normalized
    return result


def public_user_record(email: str, record: dict, recent_jobs: list[dict] | None = None) -> dict:
    return {
        "email": normalize_email(email),
        "displayName": str(record.get("displayName") or email),
        "role": str(record.get("role") or "user"),
        "disabled": bool(record.get("disabled", False)),
        "mustSetPassword": bool(record.get("mustSetPassword", False)),
        "hasPassword": bool(record.get("passwordHash")),
        "created": float(record.get("created") or 0),
        "updated": float(record.get("updated") or 0),
        "recentJobs": recent_jobs or [],
    }


def recent_jobs_by_email() -> dict[str, list[dict]]:
    with _JOB_INDEX_LOCK:
        jobs = load_job_index()
    grouped: dict[str, list[dict]] = {}
    for prompt_id, item in jobs.items():
        if not isinstance(item, dict):
            continue
        email = normalize_email(str(item.get("email") or ""))
        if not email:
            continue
        grouped.setdefault(email, []).append({
            "promptId": str(prompt_id),
            "feature": str(item.get("feature") or ""),
            "characterId": str(item.get("characterId") or ""),
            "ts": float(item.get("ts") or 0),
        })
    for email, rows in grouped.items():
        rows.sort(key=lambda item: item.get("ts", 0), reverse=True)
        grouped[email] = rows[:10]
    return grouped


def enabled_admin_count(users: dict[str, dict]) -> int:
    return sum(1 for user in users.values() if isinstance(user, dict) and user.get("role") == "admin" and not user.get("disabled"))


def parse_cookies(header: str | None) -> dict[str, str]:
    cookies = {}
    for item in (header or "").split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def session_ttl_for_role(role: str) -> int:
    return SESSION_TTL_ADMIN_SECONDS if role == "admin" else SESSION_TTL_USER_SECONDS


def user_credential_version(user: dict | None) -> str | None:
    password_hash_value = str((user or {}).get("passwordHash") or "")
    if not password_hash_value:
        return None
    return hashlib.sha256(password_hash_value.encode("utf-8")).hexdigest()


def local_credential_version() -> str | None:
    config = auth_config()
    if not isinstance(config, dict):
        return None
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _session_token_key(token: str) -> str | None:
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def _session_store_file_lock():
    SESSION_STORE_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent_stat = SESSION_STORE_PATH.parent.lstat()
    if (
        SESSION_STORE_PATH.parent.is_symlink()
        or not SESSION_STORE_PATH.parent.is_dir()
        or parent_stat.st_uid != os.geteuid()
    ):
        raise PermissionError("session store directory is unsafe")
    if parent_stat.st_mode & 0o077:
        SESSION_STORE_PATH.parent.chmod(0o700)
    lock_path = SESSION_STORE_PATH.with_name(f".{SESSION_STORE_PATH.name}.lock")
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _normalize_session_record(record: object, now: float) -> dict | None:
    if not isinstance(record, dict):
        return None
    try:
        expires = float(record.get("expires"))
        created = float(record.get("created"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(expires) or not math.isfinite(created) or expires <= now:
        return None
    role = str(record.get("role") or "user")
    if role not in ("admin", "user"):
        return None
    # A hand-edited store must not turn a normal cookie into a perpetual one.
    if created > now + 300 or expires > created + session_ttl_for_role(role) + 300:
        return None
    email = normalize_email(str(record.get("email") or ""))
    if not email or len(email) > 320:
        return None
    credential_version = record.get("credentialVersion")
    if credential_version is not None and not re.fullmatch(r"[0-9a-f]{64}", str(credential_version)):
        return None
    clean = {
        "expires": expires,
        "created": created,
        "email": email,
        "role": role,
        "displayName": str(record.get("displayName") or email)[:256],
    }
    if credential_version is not None:
        clean["credentialVersion"] = str(credential_version)
    return clean


def _clean_session_records(raw: object, now: float) -> tuple[dict[str, dict], bool]:
    source = raw if isinstance(raw, dict) else {}
    clean: dict[str, dict] = {}
    changed = not isinstance(raw, dict)
    for token_key, record in source.items():
        key = str(token_key)
        normalized = _normalize_session_record(record, now)
        if not re.fullmatch(r"[0-9a-f]{64}", key) or normalized is None:
            changed = True
            continue
        clean[key] = normalized
        if normalized != record:
            changed = True
    if len(clean) > SESSION_STORE_MAX_RECORDS:
        newest = sorted(
            clean.items(),
            key=lambda item: (item[1]["created"], item[1]["expires"], item[0]),
            reverse=True,
        )[:SESSION_STORE_MAX_RECORDS]
        clean = dict(newest)
        changed = True
    return clean, changed


def _read_session_records_unlocked(now: float) -> tuple[dict[str, dict], bool]:
    try:
        if SESSION_STORE_PATH.is_symlink():
            return {}, True
        file_stat = SESSION_STORE_PATH.stat()
        if file_stat.st_size > SESSION_STORE_MAX_BYTES:
            return {}, True
        if file_stat.st_mode & 0o777 != 0o600:
            SESSION_STORE_PATH.chmod(0o600)
        payload = json.loads(SESSION_STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, False
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, True
    if not isinstance(payload, dict) or payload.get("version") != SESSION_STORE_VERSION:
        return {}, True
    return _clean_session_records(payload.get("sessions"), now)


def _write_session_records_unlocked(records: dict[str, dict]) -> None:
    SESSION_STORE_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    payload = {
        "version": SESSION_STORE_VERSION,
        "updated": time.time(),
        "sessions": records,
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{SESSION_STORE_PATH.name}.tmp-",
        dir=SESSION_STORE_PATH.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SESSION_STORE_PATH)
        SESSION_STORE_PATH.chmod(0o600)
        directory_fd = os.open(SESSION_STORE_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _sync_session_memory(records: dict[str, dict]) -> None:
    SESSIONS.clear()
    SESSIONS.update(records)


def session_create(identity: dict) -> str:
    token = secrets.token_urlsafe(32)
    token_key = _session_token_key(token)
    assert token_key is not None
    role = str(identity.get("role") or "user")
    if role not in ("admin", "user"):
        role = "user"
    now = time.time()
    expires_at = now + session_ttl_for_role(role)
    session = {
        "expires": expires_at,
        "created": now,
        "email": normalize_email(str(identity.get("email") or "")),
        "role": role,
        "displayName": str(identity.get("displayName") or identity.get("email") or ""),
    }
    credential_version = (
        local_credential_version()
        if session["email"] in SYNTHETIC_SESSION_EMAILS
        else user_credential_version(user_for_email(session["email"]))
    )
    if credential_version:
        session["credentialVersion"] = credential_version
    with _SESSION_LOCK, _session_store_file_lock():
        records, _changed = _read_session_records_unlocked(now)
        records[token_key] = session
        records, _changed = _clean_session_records(records, now)
        _write_session_records_unlocked(records)
        _sync_session_memory(records)
    return token


def session_get(token: str | None) -> dict | None:
    token_key = _session_token_key(token) if token is not None else None
    if token_key is None:
        return None
    with _SESSION_LOCK, _session_store_file_lock():
        records, changed = _read_session_records_unlocked(time.time())
        session = records.get(token_key)
        if changed:
            _write_session_records_unlocked(records)
        _sync_session_memory(records)
        if session is None:
            return None
        result = dict(session)
    email = normalize_email(str(result.get("email") or ""))
    if email in SYNTHETIC_SESSION_EMAILS:
        if not hmac.compare_digest(
            str(result.get("credentialVersion") or ""),
            str(local_credential_version() or ""),
        ):
            session_invalidate(token)
            return None
    else:
        user = user_for_email(email)
        if (
            not user
            or user.get("disabled")
            or user.get("mustSetPassword")
            or not user.get("passwordHash")
            or not hmac.compare_digest(
                str(result.get("credentialVersion") or ""),
                str(user_credential_version(user) or ""),
            )
        ):
            session_invalidate(token)
            return None
        if user.get("role") != result.get("role") or user.get("displayName") != result.get("displayName"):
            result.update({"role": user.get("role"), "displayName": user.get("displayName")})
            with _SESSION_LOCK, _session_store_file_lock():
                records, changed = _read_session_records_unlocked(time.time())
                if isinstance(records.get(token_key), dict):
                    records[token_key].update({"role": result["role"], "displayName": result["displayName"]})
                    changed = True
                if changed:
                    _write_session_records_unlocked(records)
                _sync_session_memory(records)
    return result


def session_valid(token: str | None) -> bool:
    return session_get(token) is not None


def orchestrator_service_token() -> str:
    token = os.environ.get("CASTLE_ORCH_SERVICE_TOKEN", "").strip()
    if token:
        return token
    if ORCH_SERVICE_TOKEN_PATH is None:
        return ""
    try:
        return ORCH_SERVICE_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def presenter_voice_service_token() -> str:
    token = os.environ.get("CASTLE_PRESENTER_VOICE_TOKEN", "").strip()
    if token:
        return token
    try:
        return PRESENTER_VOICE_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def session_invalidate(token: str | None) -> None:
    token_key = _session_token_key(token) if token is not None else None
    if token_key is None:
        return
    with _SESSION_LOCK, _session_store_file_lock():
        records, changed = _read_session_records_unlocked(time.time())
        if records.pop(token_key, None) is not None:
            changed = True
        if changed:
            _write_session_records_unlocked(records)
        _sync_session_memory(records)


def login_retry_after(ip: str) -> float:
    """Seconds the caller must wait before another login attempt, or 0."""
    now = time.time()
    with _LOGIN_LOCK:
        recent_global = [t for t in _LOGIN_GLOBAL_FAILS if now - t < LOGIN_GLOBAL_WINDOW]
        _LOGIN_GLOBAL_FAILS[:] = recent_global
        if len(recent_global) >= LOGIN_GLOBAL_MAX_FAILS:
            return LOGIN_GLOBAL_WINDOW
        entry = _LOGIN_ATTEMPTS.get(ip)
        if entry and now < entry["next_allowed"]:
            return entry["next_allowed"] - now
    return 0.0


def login_record_failure(ip: str) -> None:
    now = time.time()
    with _LOGIN_LOCK:
        _LOGIN_GLOBAL_FAILS.append(now)
        entry = _LOGIN_ATTEMPTS.get(ip) or {"fails": 0, "next_allowed": 0.0}
        entry["fails"] += 1
        delay = min(LOGIN_MAX_DELAY, LOGIN_BASE_DELAY * (2 ** (entry["fails"] - 1)))
        entry["next_allowed"] = now + delay
        _LOGIN_ATTEMPTS[ip] = entry


def login_record_success(ip: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(ip, None)


def load_voice_index() -> list[dict]:
    try:
        data = json.loads(VOICE_INDEX.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_voice_index(entries: list[dict]) -> None:
    VOICE_LIBRARY.mkdir(parents=True, exist_ok=True)
    VOICE_INDEX.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def voice_public(entry: dict, user: dict | None = None) -> dict:
    public = {
        "id": entry["id"],
        "name": entry.get("name") or entry["id"],
        "size": entry.get("size", 0),
        "type": entry.get("type", ""),
        "created": entry.get("created", 0),
        "url": f"/api/voices/file?id={urllib.parse.quote(entry['id'])}",
    }
    for key in ("source", "transcript", "duration", "sampleRate", "engine", "tool", "kind"):
        if entry.get(key) not in (None, ""):
            public[key] = entry.get(key)
    if isinstance(entry.get("settings"), dict):
        public["settings"] = entry["settings"]
    if user is not None:
        public["canDelete"] = user_can_manage_voice_entry(user, entry)
    return public


def voice_entry_is_operator_only(entry: dict) -> bool:
    if bool(entry.get("operatorOnly")):
        return True
    return bool(OPERATOR_EMAIL and normalize_email(str(entry.get("ownerEmail") or "")) == OPERATOR_EMAIL)


def user_can_access_voice_entry(
    user: dict | None,
    entry: dict,
    service_token: bool = False,
) -> bool:
    if service_token:
        return True
    if voice_entry_is_operator_only(entry):
        return is_operator_user(user)
    owner = normalize_email(str(entry.get("ownerEmail") or ""))
    if not owner:
        return True
    email = normalize_email(str((user or {}).get("email") or ""))
    return bool(email and (email == owner or is_operator_user(user)))


def user_can_manage_voice_entry(user: dict | None, entry: dict) -> bool:
    if is_operator_user(user):
        return True
    owner = normalize_email(str(entry.get("ownerEmail") or ""))
    email = normalize_email(str((user or {}).get("email") or ""))
    return bool(owner and email and owner == email)


def validate_view_parts(filename: str, subfolder: str, view_type: str) -> tuple[str, str, str]:
    """Whitelist ComfyUI /view identity so this public proxy can't be used to read
    or delete arbitrary files. filename must be a bare basename, subfolder must be
    a relative path with no traversal, type must be a known bucket."""
    if not filename or filename != Path(filename).name:
        raise ValueError("Invalid filename")
    if subfolder:
        normalized = subfolder.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or any(part in ("..", "") for part in parts):
            raise ValueError("Invalid subfolder")
    if view_type not in ("input", "output", "temp"):
        raise ValueError("Invalid type")
    return filename, subfolder, view_type


def normalized_rename_filename(raw_name: str, old_filename: str) -> str:
    name = raw_name.strip()
    if not name:
        raise ValueError("請輸入新檔名")
    if "/" in name or "\\" in name:
        raise ValueError("檔名不能包含路徑")
    old_suffix = Path(old_filename).suffix
    candidate = Path(name).name
    if candidate != name or candidate in (".", "..") or candidate.startswith("."):
        raise ValueError("Invalid filename")
    if not Path(candidate).suffix:
        candidate = f"{candidate}{old_suffix}"
    if Path(candidate).suffix.lower() != old_suffix.lower():
        raise ValueError(f"請保留原副檔名 {old_suffix}")
    if any(ord(ch) < 32 for ch in candidate):
        raise ValueError("檔名不能包含控制字元")
    validate_view_parts(candidate, "", "output")
    return candidate


def validate_view_query(query: str) -> str:
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    filename, subfolder, view_type = validate_view_parts(
        (params.get("filename") or [""])[0],
        (params.get("subfolder") or [""])[0],
        (params.get("type") or ["output"])[0],
    )
    clean = {"filename": filename, "type": view_type}
    if subfolder:
        clean["subfolder"] = subfolder
    return urllib.parse.urlencode(clean)


def resolve_view_path(filename: str, subfolder: str, view_type: str) -> Path | None:
    """On-disk path for a validated /view item, or None if the bucket is unknown.
    Guards against escaping the bucket dir even though the parts are pre-validated."""
    base = VIEW_DIRS.get(view_type)
    if base is None:
        return None
    target = (base / subfolder / filename) if subfolder else (base / filename)
    target = target.resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError("Invalid path")
    return target


def load_hidden() -> set[tuple[str, str, str]]:
    try:
        data = json.loads(HIDDEN_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    out = set()
    for e in data.get("hidden", []):
        out.add((e.get("type", "output"), e.get("subfolder", ""), e.get("filename", "")))
    return out


def save_hidden(keys: set[tuple[str, str, str]]) -> None:
    payload = {"hidden": [
        {"type": t, "subfolder": s, "filename": f} for (t, s, f) in sorted(keys)
    ]}
    atomic_write_json(HIDDEN_PATH, payload, 0o600)


def production_state_store() -> ProductionStateStore:
    global _PRODUCTION_STATE_STORE
    with _PRODUCTION_STATE_STORE_LOCK:
        if _PRODUCTION_STATE_STORE is None:
            _PRODUCTION_STATE_STORE = ProductionStateStore(PRODUCTION_STATE_DB)
        return _PRODUCTION_STATE_STORE


def character_rights_store() -> CharacterRightsStore:
    global _CHARACTER_RIGHTS_STORE
    with _CHARACTER_RIGHTS_STORE_LOCK:
        if _CHARACTER_RIGHTS_STORE is None:
            _CHARACTER_RIGHTS_STORE = CharacterRightsStore(CHARACTER_RIGHTS_DB)
        return _CHARACTER_RIGHTS_STORE


def series_continuity_store() -> SeriesContinuityStore:
    global _SERIES_CONTINUITY_STORE
    with _SERIES_CONTINUITY_STORE_LOCK:
        if _SERIES_CONTINUITY_STORE is None:
            _SERIES_CONTINUITY_STORE = SeriesContinuityStore(SERIES_CONTINUITY_DB)
        return _SERIES_CONTINUITY_STORE


class CastleThreadingHTTPServer(ThreadingHTTPServer):
    """Thread-per-request server sized for browser and tunnel request bursts."""

    # socketserver.TCPServer defaults to five pending connections.  A browser
    # loading the app through Cloudflare Tunnel can exceed that with one page
    # load and delay the origin response during an ordinary request burst.
    request_queue_size = 128


_GENERATION_DETAIL_OPTION_KEYS = frozenset({
    "aspect", "aspect_ratio", "duration_seconds", "mode", "motion_profile",
    "quality", "resolution", "size", "strength",
})
_GENERATION_DETAIL_INSTRUCTION_KEYS = (
    "frame_intent", "observable_action", "camera", "audio_intent", "language", "shot_id",
)


def _generation_detail_text(value: object, maximum: int = 4000) -> str:
    text = str(value or "").strip()
    if not text or "\x00" in text:
        return ""
    return text[:maximum]


def owned_asset_generation_detail(store, owner_user_id: str, projection_id: str) -> dict:
    """Return owner-scoped, allowlisted generation provenance for one asset."""

    asset = store.get_owned_remote_asset(owner_user_id, projection_id)
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    intent_id = str(metadata.get("intent_id") or "")
    if (
        metadata.get("source") != "autoip_generation"
        or re.fullmatch(r"intent:[0-9a-f]{32}", intent_id) is None
    ):
        return {
            "schema_version": "castle.asset-generation-detail.response.v1",
            "ok": True,
            "asset_id": projection_id,
            "available": False,
        }
    # This SQL-backed status lookup is the ownership boundary.  get_intent()
    # follows only after the same owner is proven to own both asset and intent.
    status = store.get_intent_status(intent_id, owner_user_id)
    record = store.get_intent(intent_id)
    intent = record.get("intent") if isinstance(record.get("intent"), dict) else {}
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    instruction = (
        payload.get("creative_instruction")
        if isinstance(payload.get("creative_instruction"), dict)
        else {}
    )
    prompt = {
        key: text
        for key in _GENERATION_DETAIL_INSTRUCTION_KEYS
        if (text := _generation_detail_text(instruction.get(key)))
    }
    raw_options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    options = {}
    for key, value in raw_options.items():
        if key not in _GENERATION_DETAIL_OPTION_KEYS:
            continue
        if isinstance(value, bool) or (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        ):
            options[key] = value
        elif isinstance(value, str) and 0 < len(value) <= 100 and "\x00" not in value:
            options[key] = value
    characters = []
    for item in intent.get("characters") if isinstance(intent.get("characters"), list) else []:
        if not isinstance(item, dict) or len(characters) >= 16:
            continue
        character_id = _generation_detail_text(item.get("character_id"), 160)
        display_name = _generation_detail_text(item.get("display_name"), 160)
        if character_id:
            characters.append({"id": character_id, "name": display_name or character_id})
    grant = payload.get("capability_grant") if isinstance(payload.get("capability_grant"), dict) else {}
    module = _generation_detail_text(grant.get("profile_key"), 240)
    return {
        "schema_version": "castle.asset-generation-detail.response.v1",
        "ok": True,
        "asset_id": projection_id,
        "available": True,
        "workflow": {
            "capability": str(status["capability"]),
            "provider": str(status["provider_id"]),
            "module": module,
        },
        "characters": characters,
        "prompt": prompt,
        "options": options,
        "usage": {
            "authorized_credits": status.get("credits_authorized"),
            "charged_credits": status.get("credits_charged"),
            "remaining_credits": status.get("remaining_credits"),
        },
        "state": str(status["state"]),
        "created_at": str(status["created_at"]),
        "updated_at": str(status["updated_at"]),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CastleComfyWeb/0.1"
    MAX_CHUNK_LINE = 4096
    MAX_CHUNK_TRAILERS = 16 * 1024

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def read_chunked_body(self, max_bytes: int) -> bytes:
        chunks = []
        total = 0
        while True:
            try:
                line = self.rfile.readline(self.MAX_CHUNK_LINE + 1)
            except ConnectionError as error:
                self.close_connection = True
                raise ClientDisconnected("Client disconnected while sending request body") from error
            if not line:
                self.close_connection = True
                raise RequestBodyError("Incomplete chunked request body")
            if len(line) > self.MAX_CHUNK_LINE or not line.endswith(b"\r\n"):
                self.close_connection = True
                raise RequestBodyError("Invalid chunk header")
            size_token = line[:-2].split(b";", 1)[0].strip()
            if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", size_token):
                self.close_connection = True
                raise RequestBodyError("Invalid chunk size")
            chunk_size = int(size_token, 16)
            if chunk_size == 0:
                trailer_bytes = 0
                while True:
                    try:
                        trailer = self.rfile.readline(self.MAX_CHUNK_LINE + 1)
                    except ConnectionError as error:
                        self.close_connection = True
                        raise ClientDisconnected("Client disconnected while sending request trailers") from error
                    if not trailer:
                        self.close_connection = True
                        raise RequestBodyError("Incomplete chunked request trailers")
                    trailer_bytes += len(trailer)
                    if (
                        len(trailer) > self.MAX_CHUNK_LINE
                        or not trailer.endswith(b"\r\n")
                        or trailer_bytes > self.MAX_CHUNK_TRAILERS
                    ):
                        self.close_connection = True
                        raise RequestBodyError("Invalid chunked request trailers")
                    if trailer == b"\r\n":
                        return b"".join(chunks)
            if total + chunk_size > max_bytes:
                self.close_connection = True
                raise RequestTooLarge(f"Request body exceeds {max_bytes} bytes")
            try:
                chunk = self.rfile.read(chunk_size)
                suffix = self.rfile.read(2)
            except ConnectionError as error:
                self.close_connection = True
                raise ClientDisconnected("Client disconnected while sending request body") from error
            if len(chunk) != chunk_size:
                self.close_connection = True
                raise RequestBodyError(
                    f"Incomplete request chunk: expected {chunk_size} bytes, received {len(chunk)}"
                )
            if suffix != b"\r\n":
                self.close_connection = True
                raise RequestBodyError("Invalid chunk terminator")
            chunks.append(chunk)
            total += chunk_size

    def read_body(self, max_bytes: int) -> bytes:
        get_all = getattr(self.headers, "get_all", None)
        transfer_encodings = get_all("Transfer-Encoding") if callable(get_all) else None
        transfer_encoding = self.headers.get("Transfer-Encoding")
        content_lengths = get_all("Content-Length") if callable(get_all) else None
        content_length = self.headers.get("Content-Length")
        if transfer_encodings is None and transfer_encoding is not None:
            transfer_encodings = [transfer_encoding]
        if transfer_encodings is not None:
            if (
                len(transfer_encodings) != 1
                or str(transfer_encodings[0]).strip().lower() != "chunked"
                or content_lengths is not None
                or content_length is not None
            ):
                self.close_connection = True
                raise RequestBodyError("Unsupported Transfer-Encoding")
            return self.read_chunked_body(max_bytes)
        if transfer_encoding is not None:
            self.close_connection = True
            raise RequestBodyError("Unsupported Transfer-Encoding")
        if content_lengths is not None and len(content_lengths) > 1:
            self.close_connection = True
            raise RequestBodyError("Multiple Content-Length headers")
        try:
            length = int(content_length or "0")
        except ValueError:
            self.close_connection = True
            raise RequestBodyError("Invalid Content-Length")
        if length < 0:
            self.close_connection = True
            raise RequestBodyError("Invalid Content-Length")
        if length > max_bytes:
            self.close_connection = True
            raise RequestTooLarge(f"Request body exceeds {max_bytes} bytes")
        if length <= 0:
            return b""
        try:
            raw = self.rfile.read(length)
        except ConnectionError as error:
            self.close_connection = True
            raise ClientDisconnected("Client disconnected while sending request body") from error
        if len(raw) != length:
            self.close_connection = True
            raise RequestBodyError(f"Incomplete request body: expected {length} bytes, received {len(raw)}")
        return raw

    def read_json(self, max_bytes: int = MAX_JSON_BODY) -> dict:
        raw = self.read_body(max_bytes)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RequestBodyError("Invalid JSON request body") from error

    def read_provider_json(self, max_bytes: int = MAX_TEXT_BODY) -> dict:
        """Decode a bounded provider request while rejecting ambiguous duplicate keys."""

        raw = self.read_body(max_bytes)
        if not raw:
            return {}

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise RequestBodyError("Duplicate JSON field")
                result[key] = value
            return result

        def reject_nonfinite(_value):
            raise RequestBodyError("Non-finite JSON number")

        try:
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=reject_nonfinite,
            )
        except RequestBodyError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise RequestBodyError("Invalid JSON request body") from error
        if not isinstance(value, dict):
            raise RequestBodyError("JSON request body must be an object")
        return value

    def client_ip(self) -> str:
        # Behind the public tunnel every peer is loopback, so prefer the real
        # client IP the tunnel forwards. CF-Connecting-IP is set by Cloudflare
        # and not attacker-controllable for tunneled requests.
        cf = self.headers.get("CF-Connecting-IP")
        if cf:
            return cf.strip()
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def send_json(self, data: dict, status: int = 200, cache_control: str | None = None) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            self.close_connection = True

    def send_project_dry_run_json(self, data: dict, status: int = 200) -> None:
        """Send one private static-preview response with anti-sniff headers."""

        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            self.close_connection = True

    def send_project_dry_run_error(
        self,
        status: int,
        code: str,
        message: str,
    ) -> None:
        self.send_project_dry_run_json(
            {
                "schema_version": "castle.project-dry-run.error.v1",
                "ok": False,
                "error": {"code": code, "message": message},
            },
            status,
        )

    def send_project_brief_error(self, status: int, code: str, message: str) -> None:
        self.send_project_dry_run_json(
            {
                "schema_version": "castle.production-brief.error.v1",
                "ok": False,
                "error": {"code": code, "message": message},
            },
            status,
        )

    def send_project_run_json(self, data: dict, status: int = 200) -> None:
        """Send an operator-only qualification response without cache leakage."""

        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            self.close_connection = True

    def send_project_run_error(self, status: int, code: str, message: str) -> None:
        self.send_project_run_json(
            {
                "schema_version": "castle.project-run.error.v1",
                "ok": False,
                "error": {"code": code, "message": message},
            },
            status,
        )

    def send_production_error(self, status: int, code: str, message: str) -> None:
        self.send_project_run_json({
            "schema_version": "castle.production.error.v1",
            "ok": False,
            "error": {"code": code, "message": message},
        }, status)

    def send_provider_response(self, response) -> None:
        """Send a framework-neutral provider response without weakening its headers."""

        raw = json.dumps(dict(response.body), ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(int(response.status))
            for name, value in response.headers.items():
                if str(name).lower() not in {"content-length", "connection"}:
                    self.send_header(str(name), str(value))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            self.close_connection = True

    def send_provider_binary_response(self, response) -> None:
        """Send one bounded provider binary without path-based file serving."""

        body = bytes(response.body)
        try:
            self.send_response(int(response.status))
            for name, value in response.headers.items():
                if str(name).lower() not in {"content-length", "connection"}:
                    self.send_header(str(name), str(value))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except ConnectionError:
            self.close_connection = True

    def send_html(self, html_text: str, status: int = 200) -> None:
        raw = html_text.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            self.close_connection = True

    def send_error_json(self, error: Exception, status: int = 500) -> None:
        if isinstance(error, ClientDisconnected):
            self.close_connection = True
            print(f"CLIENT DISCONNECTED {self.path}: {error}", flush=True)
            return
        message = str(error)
        public_details: dict[str, str] = {}
        if isinstance(error, RequestTooLarge):
            status = 413
        elif isinstance(error, RequestBodyError):
            status = 400
        if isinstance(error, urllib.error.HTTPError):
            try:
                body = error.read(64 * 1024).decode("utf-8")
                comfy_error = comfy_error_payload(body) if body else None
                if comfy_error is not None:
                    message = str(comfy_error["message"])
                    public_details = {
                        "error_code": str(comfy_error["error_code"]),
                        "submission_state": str(comfy_error["submission_state"]),
                        "technical_detail": str(comfy_error["technical_detail"]),
                    }
            except Exception:
                pass
        print(f"ERROR {self.path}: {message}", flush=True)
        self.send_json({"ok": False, "error": message, **public_details}, status)

    def send_local_file(
        self,
        path: Path,
        content_type: str | None = None,
        inline_filename: str | None = None,
        download: bool = False,
        cache_control: str | None = None,
    ) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        content_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            byte_range = parse_range_header(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return
        start, end = byte_range if byte_range else (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if byte_range else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if inline_filename:
            quoted = urllib.parse.quote(inline_filename)
            disposition = "attachment" if download else "inline"
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quoted}")
        self.end_headers()
        if length <= 0:
            return
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def is_authenticated(self) -> bool:
        return self.current_user() is not None

    def current_user(self) -> dict | None:
        token = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE)
        user = session_get(token)
        if (bool(CANONICAL_WEB_ORIGIN) and self.web_hostname() == (urllib.parse.urlsplit(CANONICAL_WEB_ORIGIN).hostname or "")) and user and user.get("email") in SYNTHETIC_SESSION_EMAILS:
            return None
        return user

    def web_hostname(self) -> str:
        # cloudflared preserves Host. Do not route on a client-supplied
        # X-Forwarded-Host, which could turn the canonical site into a loop.
        try:
            return (urllib.parse.urlsplit("//" + str(self.headers.get("Host") or "")).hostname or "").lower().rstrip(".")
        except ValueError:
            return ""

    def handle_legacy_entrypoint(self) -> bool:
        if not CANONICAL_WEB_ORIGIN or self.web_hostname() not in LEGACY_WEB_HOSTS:
            return False
        parsed = urllib.parse.urlsplit(self.path)
        # Existing registered OAuth callbacks must exchange their code against
        # the original redirect_uri before their final navigation moves hosts.
        if self.command == "GET" and parsed.path == "/api/google/callback":
            return False
        # Machine integrations retain their authenticated transport. The exact
        # signed AutoIP callback is dispatched separately before this guard.
        if parsed.path.startswith("/api/") and not parsed.path.startswith("/api/auth/") and (
            self.is_orchestrator_service_authenticated()
            or self.is_presenter_voice_service_authenticated()
        ):
            return False
        if self.command in ("GET", "HEAD"):
            target = CANONICAL_WEB_ORIGIN + "/" + parsed.path.lstrip("/")
            if parsed.query:
                target += "?" + parsed.query
            self.send_response(308)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            # Do not replay a stale tab's password, upload, or generation POST
            # across origins. Reloading the page takes the user to the configured login.
            self.close_connection = True
            self.send_json({
                "ok": False,
                "code": "canonical_entrypoint_required",
                "error": "請使用管理員設定的網站入口登入。",
                "canonicalUrl": CANONICAL_WEB_ORIGIN + "/",
            }, 409, cache_control="no-store")
        return True

    def is_local_request(self) -> bool:
        # The public Cloudflare tunnel forwards to 127.0.0.1, so a loopback peer
        # alone does not prove the request originated on this box. Tunneled/proxied
        # requests always carry forwarding headers; a genuine local request has none.
        host = self.client_address[0] if self.client_address else ""
        if host not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        forwarding_headers = (
            "X-Forwarded-For",
            "X-Forwarded-Proto",
            "X-Forwarded-Host",
            "CF-Connecting-IP",
            "CF-Ray",
            "Forwarded",
        )
        return not any(self.headers.get(header) for header in forwarding_headers)

    def request_lane(self) -> str:
        if self.is_local_request():
            return "local"
        has_access = bool(
            self.headers.get("Cf-Access-Jwt-Assertion")
            or self.headers.get("Cf-Access-Authenticated-User-Email")
        )
        if has_access or (bool(CANONICAL_WEB_ORIGIN) and self.web_hostname() == (urllib.parse.urlsplit(CANONICAL_WEB_ORIGIN).hostname or "")):
            return "access"
        if self.headers.get("CF-Ray") or self.headers.get("CF-Connecting-IP"):
            return "tunnel-legacy"
        return "local"

    def access_identity(self) -> str | None:
        email = normalize_email(str(self.headers.get("Cf-Access-Authenticated-User-Email") or ""))
        cookies = parse_cookies(self.headers.get("Cookie"))
        cf_auth = cookies.get("CF_Authorization", "")
        if not CASTLE_ACCESS_TEAM_DOMAIN or not email or not cf_auth:
            self.send_json({"ok": False, "error": "無法驗證 Cloudflare 身分，請重試"}, 503)
            return None
        try:
            url = f"https://{CASTLE_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/get-identity"
            req = urllib.request.Request(url, headers={"Cookie": f"CF_Authorization={cf_auth}"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            confirmed = normalize_email(str(data.get("email") or ""))
        except Exception:
            self.send_json({"ok": False, "error": "無法驗證 Cloudflare 身分，請重試"}, 503)
            return None
        if confirmed != email:
            self.send_json({"ok": False, "error": "無法驗證 Cloudflare 身分，請重試"}, 503)
            return None
        return email

    def origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        host = (urllib.parse.urlparse(origin).netloc or "").lower()
        return host in ALLOWED_ORIGIN_HOSTS

    def require_auth(self) -> bool:
        if self.is_authenticated():
            return True
        self.send_json({"ok": False, "error": "Authentication required"}, 401)
        return False

    def require_admin(self) -> bool:
        user = self.current_user()
        if user and user.get("role") == "admin":
            return True
        self.send_json({"ok": False, "error": "需要管理員權限"}, 403)
        return False

    def require_operator(self) -> bool:
        if is_operator_user(self.current_user()):
            return True
        self.send_json({"ok": False, "error": "需要操作員權限"}, 403)
        return False

    def require_operator_or_admin(self) -> bool:
        user = self.current_user()
        if user and (user.get("role") == "admin" or is_operator_user(user)):
            return True
        self.send_json({"ok": False, "error": "需要操作員或管理員權限"}, 403)
        return False

    def require_voiceover_operator(self) -> bool:
        user = self.current_user()
        if is_operator_user(user):
            return True
        if user:
            self.send_json({"ok": False, "error": "Custom English 簡報配音只允許 operator 操作"}, 403)
            return False
        self.send_json({"ok": False, "error": "未登入"}, 401)
        return False

    def is_orchestrator_service_authenticated(self) -> bool:
        configured = orchestrator_service_token()
        if not configured:
            return False
        header = str(self.headers.get("Authorization") or "").strip()
        if not header.lower().startswith("bearer "):
            return False
        provided = header.split(" ", 1)[1].strip()
        return bool(provided) and hmac.compare_digest(provided, configured)

    def is_presenter_voice_service_authenticated(self) -> bool:
        configured = presenter_voice_service_token()
        if not configured:
            return False
        header = str(self.headers.get("Authorization") or "").strip()
        if not header.lower().startswith("bearer "):
            return False
        provided = header.split(" ", 1)[1].strip()
        return bool(provided) and hmac.compare_digest(provided, configured)

    def require_presenter_voice_service(self) -> bool:
        if self.is_presenter_voice_service_authenticated():
            return True
        self.send_json({"ok": False, "error": "Authentication required"}, 401)
        return False

    def require_orchestrator_auth(self) -> bool:
        if self.is_orchestrator_service_authenticated():
            return True
        if self.is_authenticated():
            user = self.current_user()
            # The generic orchestrator surface exposes persisted task inputs,
            # artifacts, and logs.  Project qualification packs are private,
            # so browser sessions must be the operator; the dedicated service
            # token remains available for trusted machine-to-machine workers.
            if not self.path.startswith("/api/orchestrator/") or is_operator_user(user):
                return True
            self.send_json({"ok": False, "error": "需要操作員權限"}, 403)
            return False
        self.send_json({"ok": False, "error": "Authentication required"}, 401)
        return False

    def set_session_cookie(self, token: str, max_age: int = 60 * 60 * 24 * 30) -> None:
        secure = self.headers.get("X-Forwarded-Proto") == "https"
        cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        if secure:
            cookie += "; Secure"
        self.send_header("Set-Cookie", cookie)

    def clear_session_cookie(self) -> None:
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    def request_origin(self) -> str:
        proto = self.headers.get("X-Forwarded-Proto") or "http"
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "127.0.0.1:8190"
        return f"{proto}://{host}"

    def google_redirect_uri(self) -> str:
        return GOOGLE_REDIRECT_URI or f"{self.request_origin()}/api/google/callback"

    def send_redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def login_success(self, identity: dict) -> None:
        token = session_create(identity)
        max_age = session_ttl_for_role(str(identity.get("role") or "user"))
        raw = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.set_session_cookie(token, max_age=max_age)
        self.end_headers()
        self.wfile.write(raw)

    def api_auth_status(self) -> None:
        config = auth_config()
        lane = self.request_lane()
        user = self.current_user()
        public_user = None
        if user:
            public_user = {
                "email": user.get("email", ""),
                "displayName": user.get("displayName", ""),
                "role": user.get("role", "user"),
                "operator": is_operator_user(user),
            }
        access_email = None
        needs_setup = False
        if lane == "access" and not user:
            access_email = normalize_email(str(self.headers.get("Cf-Access-Authenticated-User-Email") or ""))
            access_user = user_for_email(access_email) if access_email else None
            needs_setup = bool(access_user and not access_user.get("disabled") and (access_user.get("mustSetPassword") or not access_user.get("passwordHash")))
        self.send_json({
            "ok": True,
            "configured": config is not None,
            "authenticated": user is not None,
            "lane": lane,
            "user": public_user,
            "accessEmail": access_email,
            "needsSetup": needs_setup,
        }, cache_control="no-store, max-age=0")

    def do_HEAD(self) -> None:
        if not self.handle_legacy_entrypoint():
            self.send_error(501, "Unsupported method ('HEAD')")

    def do_GET(self) -> None:
        if self.handle_legacy_entrypoint():
            return
        parsed = urllib.parse.urlparse(self.path)
        if workflow_library.handle_get(self, parsed.path, parsed.query):
            return
        if parsed.path.startswith("/api/integrations/presenter-voice/jobs/") and parsed.path.endswith("/audio"):
            if not self.require_presenter_voice_service():
                return
            self.api_presenter_voice_audio(parsed.path)
        elif parsed.path.startswith("/api/integrations/presenter-voice/jobs/"):
            if not self.require_presenter_voice_service():
                return
            self.api_presenter_voice_status(parsed.path)
        elif parsed.path in ("/", "/admin", "/admin/"):
            self.serve_static("index.html")
        elif parsed.path.startswith("/static/"):
            self.serve_static(parsed.path.removeprefix("/static/"))
        elif parsed.path == "/api/auth/status":
            self.api_auth_status()
        elif parsed.path == "/api/productions":
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Production list queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_list()
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/delivery-package",
            parsed.path,
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Delivery package queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_delivery_package(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/edits/"
            r"[0-9a-f]{64}/contact-sheet",
            parsed.path,
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Contact sheet queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_contact_sheet(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/edits/"
            r"[0-9a-f]{64}/output",
            parsed.path,
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Edit output queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_edit_output(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/shots/"
            r"[a-z0-9][a-z0-9._:-]{0,127}/versions/[1-9][0-9]*/output",
            parsed.path,
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Production output queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_output_media(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/outputs", parsed.path
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Production output queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_outputs(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/intents", parsed.path
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Production intent queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_intents(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/resume", parsed.path
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Production resume queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_resume(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}", parsed.path
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Production queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_get(parsed.path)
        elif re.fullmatch(r"/api/series/[a-z0-9][a-z0-9._:-]{0,127}", parsed.path):
            if parsed.query:
                self.send_json({"ok": False, "error": {"code": "query_not_supported"}}, 400)
                return
            if not self.require_auth() or not self.require_operator():
                return
            self.api_series_get(parsed.path)
        elif re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/shots/"
            r"[a-z0-9][a-z0-9._:-]{0,127}/preparation-options",
            parsed.path,
        ):
            if parsed.query:
                self.send_production_error(400, "query_not_supported", "Preparation option queries are not supported.")
                return
            if not self.require_auth():
                return
            self.api_production_preparation_options(parsed.path)
        elif re.fullmatch(r"/api/projects/runs/run_[0-9a-f]{20}", parsed.path):
            if parsed.query:
                self.send_project_run_error(
                    400, "query_not_supported", "Project run queries are not supported."
                )
                return
            if not PROJECT_DAG_ADMIN_ENABLED:
                self.send_project_run_error(
                    404, "project_runs_disabled", "Project qualification is not enabled."
                )
                return
            if not self.is_authenticated():
                self.send_project_run_error(
                    401, "authentication_required", "Authentication is required."
                )
                return
            if not is_operator_user(self.current_user()):
                self.send_project_run_error(
                    403, "project_run_forbidden", "Project qualification requires operator access."
                )
                return
            self.api_project_run_get(parsed.path)
        elif parsed.path == "/api/google/callback":
            self.api_google_callback(parsed.query)
        elif parsed.path == "/api/google/status":
            if not self.require_auth():
                return
            self.api_google_status()
        elif parsed.path == "/api/google/login":
            if not self.require_auth():
                return
            self.api_google_login()
        elif parsed.path == "/api/google/drive/files":
            if not self.require_auth():
                return
            self.api_google_drive_files(parsed.query)
        elif parsed.path == "/api/status":
            if not self.require_auth():
                return
            self.api_status()
        elif parsed.path == "/api/features":
            if not self.require_auth():
                return
            self.api_features()
        elif parsed.path == "/api/providers":
            if not self.require_auth():
                return
            if parsed.query:
                self._send_provider_failure(400, "schema_invalid", "Query fields are not supported.")
                return
            self.api_provider_inventory()
        elif not parsed.query and re.fullmatch(
            r"/api/providers/autoip/accounts/[a-z0-9][a-z0-9._:-]{0,127}/golden-candidates",
            parsed.path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_autoip_golden_candidates(parsed.path)
        elif not parsed.query and re.fullmatch(
            r"/api/providers/autoip/accounts/[a-z0-9][a-z0-9._:-]{0,127}/catalog",
            parsed.path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_autoip_catalog(parsed.path)
        elif parsed.path == "/api/provider-characters":
            if not self.require_auth():
                return
            self.api_provider_characters(parsed.query)
        elif parsed.path == "/api/assets":
            if not self.require_auth():
                return
            self.api_provider_assets(parsed.query)
        elif (
            not parsed.query
            and re.fullmatch(
                r"/api/assets/[a-z0-9][a-z0-9._:-]{0,127}/generation-detail",
                parsed.path,
            )
        ):
            if not self.require_auth():
                return
            self.api_provider_asset_generation_detail(parsed.path)
        elif (
            not parsed.query
            and re.fullmatch(
                r"/api/assets/[a-z0-9][a-z0-9._:-]{0,127}/source",
                parsed.path,
            )
        ):
            if not self.require_auth():
                return
            self.api_provider_asset_source(parsed.path)
        elif (
            not parsed.query
            and re.fullmatch(
                r"/api/assets/[a-z0-9][a-z0-9._:-]{0,127}/preview",
                parsed.path,
            )
        ):
            if not self.require_auth():
                return
            self.api_provider_asset_preview(parsed.path)
        elif (
            not parsed.query
            and re.fullmatch(
                r"/api/intents/intent(?::|%3[aA])[0-9a-f]{32}/status",
                parsed.path,
            )
        ):
            if not self.require_auth():
                return
            self.api_provider_intent_status(parsed.path)
        elif (
            not parsed.query
            and re.fullmatch(
                r"/api/provider-import-receipts/receipt:[0-9a-f]{32}",
                parsed.path,
            )
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_import_receipt(parsed.path)
        elif parsed.path == "/api/voiceover/batches":
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_batches()
        elif parsed.path.startswith("/api/voiceover/batches/") and parsed.path.endswith("/zip"):
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_zip(parsed.path)
        elif parsed.path.startswith("/api/voiceover/batches/"):
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_batch(parsed.path)
        elif parsed.path == "/api/characters":
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_characters()
        elif parsed.path == "/api/posts":
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_posts()
        elif parsed.path.startswith("/api/characters/") and parsed.path.endswith("/rights"):
            if not self.require_auth():
                return
            self.api_character_rights_get(parsed.path)
        elif parsed.path.startswith("/api/characters/") and parsed.path.endswith("/capabilities"):
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_character_capabilities(parsed.path)
        elif parsed.path.startswith("/api/characters/") and parsed.path.endswith("/wardrobe"):
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_character_wardrobe(parsed.path)
        elif parsed.path.startswith("/api/characters/") and parsed.path.endswith("/posts"):
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_character_posts(parsed.path)
        elif parsed.path.startswith("/api/characters/") and parsed.path.endswith("/asset"):
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_character_asset(parsed.path, parsed.query)
        elif parsed.path.startswith("/api/characters/"):
            if not self.is_orchestrator_service_authenticated() and not self.require_auth():
                return
            self.api_character(parsed.path)
        elif parsed.path == "/api/queue":
            if not self.require_auth():
                return
            self.api_queue(parsed.query)
        elif parsed.path == "/api/orchestrator/status":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_status()
        elif parsed.path == "/api/orchestrator/resources":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_resources()
        elif parsed.path == "/api/orchestrator/runs":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_runs(parsed.query)
        elif parsed.path == "/api/orchestrator/reel-reviews":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_reel_reviews(parsed.query)
        elif parsed.path.startswith("/api/orchestrator/runs/") and parsed.path.endswith("/review-page"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_run_review_page(parsed.path, parsed.query)
        elif parsed.path.startswith("/api/orchestrator/runs/"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_run(parsed.path, parsed.query)
        elif parsed.path == "/api/orchestrator/events":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_events(parsed.query)
        elif parsed.path == "/api/orchestrator/tasks":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_tasks(parsed.query)
        elif parsed.path == "/api/orchestrator/artifacts":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_artifacts(parsed.query)
        elif parsed.path == "/api/orchestrator/tags":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_tags(parsed.query)
        elif parsed.path.startswith("/api/orchestrator/artifacts/"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_artifact(parsed.path)
        elif parsed.path.startswith("/api/orchestrator/tasks/"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_task(parsed.path)
        elif parsed.path == "/api/events":
            if not self.require_auth():
                return
            self.api_events()
        elif parsed.path == "/api/library/collect":
            if not self.require_auth():
                return
            self.api_library_collection(parsed.query)
        elif parsed.path in ("/api/library", "/api/library/file"):
            if not self.require_auth():
                return
            self.api_library(parsed.path, parsed.query)
        elif parsed.path == "/api/history":
            if not self.require_auth():
                return
            self.api_history(parsed.query)
        elif parsed.path == "/api/history/continuations":
            if not self.require_auth():
                return
            self.api_history_continuations(parsed.query)
        elif parsed.path == "/api/history/detail":
            if not self.require_auth():
                return
            self.api_history_detail(parsed.query)
        elif parsed.path == "/api/audio-export":
            if not self.require_auth():
                return
            self.api_audio_export(parsed.query)
        elif parsed.path == "/api/admin/users":
            if not self.require_auth() or not self.require_admin():
                return
            self.api_admin_users()
        elif parsed.path == "/api/view":
            if not self.require_auth():
                return
            self.api_view(parsed.query)
        elif parsed.path == "/api/voices":
            if not self.require_auth():
                return
            self.api_voices_list()
        elif parsed.path == "/api/voices/file":
            if not self.require_auth():
                return
            self.api_voices_file(parsed.query)
        elif parsed.path == "/api/personas":
            if not self.require_auth():
                return
            self.api_personas()
        elif parsed.path == "/api/crawl-videos":
            if not self.require_auth():
                return
            self.api_crawl_videos(parsed.query)
        elif parsed.path == "/api/crawl-videos/preview":
            if not self.require_auth():
                return
            self.api_crawl_video_preview(parsed.query)
        elif parsed.path == "/api/dance-library":
            if not self.require_auth():
                return
            self.api_dance_library(parsed.query)
        elif parsed.path == "/api/dance-library/manage":
            if not self.require_auth() or not self.require_operator():
                return
            self.api_dance_library_manage()
        elif parsed.path.startswith("/api/dance-library/"):
            if not self.require_auth():
                return
            self.api_dance_library_media(parsed.path)
        elif parsed.path == "/api/dance-templates":
            if not self.require_auth():
                return
            self.api_dance_templates()
        elif parsed.path.startswith("/api/dance-templates/"):
            if not self.require_auth():
                return
            self.api_dance_template_file(parsed.path)
        else:
            self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/webhooks/autoip" and not parsed.query:
            self.api_provider_autoip_webhook()
            return
        if self.handle_legacy_entrypoint():
            return
        project_brief_path = parsed.path.startswith("/api/projects/brief")
        project_dry_run_path = parsed.path.startswith("/api/projects/dry-run")
        project_run_path = parsed.path.startswith("/api/projects/runs")
        production_path = parsed.path.startswith("/api/productions")
        if not self.origin_allowed():
            if project_brief_path:
                self.send_project_brief_error(403, "forbidden_origin", "This request origin is not allowed.")
            elif project_dry_run_path:
                self.send_project_dry_run_error(
                    403,
                    "forbidden_origin",
                    "This request origin is not allowed.",
                )
            elif project_run_path:
                self.send_project_run_error(
                    403,
                    "forbidden_origin",
                    "This request origin is not allowed.",
                )
            elif production_path:
                self.send_production_error(403, "forbidden_origin", "This request origin is not allowed.")
            else:
                self.send_json({"ok": False, "error": "Forbidden origin"}, 403)
            return
        path = parsed.path
        if not parsed.query and workflow_library.handle_post(self, path):
            return
        if path == "/api/productions" and not parsed.query:
            if not self.require_auth():
                return
            self.api_production_create()
        elif not parsed.query and re.fullmatch(
            r"/api/assets/[a-z0-9][a-z0-9._:-]{0,127}/handoff", path
        ):
            if not self.require_auth():
                return
            self.api_provider_asset_handoff(path)
        elif path == "/api/series" and not parsed.query:
            if not self.require_auth() or not self.require_operator():
                return
            self.api_series_create()
        elif not parsed.query and re.fullmatch(
            r"/api/series/[a-z0-9][a-z0-9._:-]{0,127}/episodes", path
        ):
            if not self.require_auth() or not self.require_operator():
                return
            self.api_series_episode_append(path)
        elif not parsed.query and re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/shots/"
            r"[a-z0-9][a-z0-9._:-]{0,127}/prepare",
            path,
        ):
            if not self.require_auth():
                return
            self.api_production_prepare(path)
        elif not parsed.query and re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/shots/"
            r"[a-z0-9][a-z0-9._:-]{0,127}/quote",
            path,
        ):
            if not self.require_auth():
                return
            self.api_production_quote(path)
        elif not parsed.query and re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/shots/"
            r"[a-z0-9][a-z0-9._:-]{0,127}/repair",
            path,
        ):
            if not self.require_auth():
                return
            self.api_production_repair(path)
        elif not parsed.query and re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/edit", path
        ):
            if not self.require_auth():
                return
            self.api_production_edit(path)
        elif not parsed.query and re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/delivery-review", path
        ):
            if not self.require_auth():
                return
            self.api_production_delivery_review(path)
        elif not parsed.query and re.fullmatch(
            r"/api/productions/[a-z0-9][a-z0-9._:-]{0,127}/mutations", path
        ):
            if not self.require_auth():
                return
            self.api_production_mutate(path)
        elif production_path:
            self.send_production_error(404, "not_found", "The requested production route was not found.")
        elif path in {"/api/projects/brief", "/api/projects/brief/routes"} and not parsed.query:
            if not self.is_authenticated():
                self.send_project_brief_error(401, "authentication_required", "Authentication is required.")
                return
            user = self.current_user()
            if not user or (user.get("role") != "admin" and not is_operator_user(user)):
                self.send_project_brief_error(403, "brief_forbidden", "Production brief compilation requires administrator or operator access.")
                return
            if path == "/api/projects/brief/routes":
                self.api_project_brief_route_review()
            else:
                self.api_project_brief_compile()
        elif path == "/api/projects/brief/formal-pack" and not parsed.query:
            if not self.is_authenticated():
                self.send_project_brief_error(401, "authentication_required", "Authentication is required.")
                return
            user = self.current_user()
            if not user or (user.get("role") != "admin" and not is_operator_user(user)):
                self.send_project_brief_error(403, "brief_forbidden", "Formal Pack compilation requires administrator or operator access.")
                return
            self.api_project_brief_formal_pack()
        elif project_brief_path:
            self.send_project_brief_error(404, "not_found", "The requested production brief route was not found.")
        elif self.path == "/api/projects/dry-run":
            if not self.is_authenticated():
                self.send_project_dry_run_error(
                    401,
                    "authentication_required",
                    "Authentication is required.",
                )
                return
            user = self.current_user()
            if not user or (user.get("role") != "admin" and not is_operator_user(user)):
                self.send_project_dry_run_error(
                    403,
                    "preview_forbidden",
                    "Production-pack preview requires administrator or operator access.",
                )
                return
            self.api_project_dry_run()
        elif project_dry_run_path:
            self.send_project_dry_run_error(
                404,
                "not_found",
                "The requested preview route was not found.",
            )
        elif path == "/api/projects/runs":
            if parsed.query:
                self.send_project_run_error(
                    400, "query_not_supported", "Project run queries are not supported."
                )
                return
            if not PROJECT_DAG_ADMIN_ENABLED:
                self.send_project_run_error(
                    404, "project_runs_disabled", "Project qualification is not enabled."
                )
                return
            if not self.is_authenticated():
                self.send_project_run_error(
                    401, "authentication_required", "Authentication is required."
                )
                return
            if not is_operator_user(self.current_user()):
                self.send_project_run_error(
                    403, "project_run_forbidden", "Project qualification requires operator access."
                )
                return
            self.api_project_run_create()
        elif project_run_path:
            self.send_project_run_error(
                404, "not_found", "The requested project run route was not found."
            )
        elif path == "/api/integrations/presenter-voice/jobs":
            if not self.require_presenter_voice_service():
                return
            self.api_presenter_voice_create()
        elif path == "/api/auth/setup":
            self.api_auth_setup()
        elif path == "/api/auth/login":
            self.api_auth_login()
        elif path == "/api/auth/setup-password":
            self.api_auth_setup_password()
        elif path == "/api/auth/logout":
            self.api_auth_logout()
        elif path == "/api/google/logout":
            if not self.require_auth():
                return
            self.api_google_logout()
        elif path == "/api/google/drive/import":
            if not self.require_auth():
                return
            self.api_google_drive_import()
        elif path == "/api/generate":
            if not self.require_auth():
                return
            self.api_generate()
        elif path == "/api/generations" and not parsed.query:
            if not self.require_auth():
                return
            self.api_provider_generation()
        elif path == "/api/intents/plan" and not parsed.query:
            if not self.require_auth():
                return
            self.api_provider_plan()
        elif path == "/api/providers/clipmode/imports/preview" and not parsed.query:
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_clipmode_preview()
        elif path == "/api/providers/clipmode/imports/stages" and not parsed.query:
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_import_stage()
        elif not parsed.query and re.fullmatch(
            r"/api/providers/autoip/accounts/[a-z0-9][a-z0-9._:-]{0,127}/probe",
            path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_autoip_probe(path)
        elif not parsed.query and re.fullmatch(
            r"/api/providers/autoip/accounts/[a-z0-9][a-z0-9._:-]{0,127}/catalog/sync",
            path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_autoip_catalog_sync(path)
        elif not parsed.query and re.fullmatch(
            r"/api/providers/autoip/accounts/[a-z0-9][a-z0-9._:-]{0,127}/catalog/tasks/"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/adopt",
            path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_autoip_history_adopt(path)
        elif not parsed.query and re.fullmatch(
            r"/api/providers/autoip/accounts/[a-z0-9][a-z0-9._:-]{0,127}/goldens",
            path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_autoip_golden(path)
        elif not parsed.query and re.fullmatch(
            r"/api/providers/clipmode/imports/stages/stage_[0-9a-f]{32}/plan",
            path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_import_plan(path)
        elif not parsed.query and re.fullmatch(
            r"/api/providers/clipmode/imports/stages/stage_[0-9a-f]{32}/confirm",
            path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_import_confirm(path)
        elif not parsed.query and re.fullmatch(
            r"/api/intents/plan(?::|%3[aA])[0-9a-f]{32,64}/confirm",
            path,
        ):
            if not self.require_auth():
                return
            self.api_provider_confirm(path)
        elif path == "/api/post":
            if not self.require_auth():
                return
            self.api_post_generate()
        elif path == "/api/assist":
            if not self.require_auth():
                return
            self.api_assist()
        elif path == "/api/fetch-url":
            if not self.require_auth():
                return
            self.api_fetch_url()
        elif path == "/api/fetch-drive":
            if not self.require_auth():
                return
            self.api_fetch_drive()
        elif path == "/api/voiceover/extract-pptx":
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_extract_pptx()
        elif path == "/api/voiceover/batches":
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_create_batch()
        elif path.startswith("/api/voiceover/batches/") and path.endswith("/redo"):
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_redo(path)
        elif path.startswith("/api/voiceover/batches/") and path.endswith("/restore"):
            if not self.require_voiceover_operator():
                return
            self.api_voiceover_restore(path)
        elif path == "/api/files/upload-input":
            if not self.require_auth():
                return
            self.api_upload_input_file(parsed.query)
        elif path == "/api/library/collect":
            if not self.require_auth() or not self.require_admin():
                return
            self.api_library_collection(None)
        elif path in ("/api/library/classify", "/api/library/import"):
            if not self.require_auth():
                return
            self.api_library_mutate(path)
        elif path == "/api/crawl-videos/import":
            if not self.require_auth():
                return
            self.api_crawl_video_import()
        elif path == "/api/dance-library/rescan":
            if not self.require_auth() or not self.require_operator():
                return
            self.api_dance_library_rescan()
        elif path == "/api/dance-library/update":
            if not self.require_auth() or not self.require_operator():
                return
            self.api_dance_library_update()
        elif path == "/api/dance-library/prepare":
            if not self.require_auth():
                return
            self.api_dance_library_prepare()
        elif path == "/api/dance-library/import":
            if not self.require_auth():
                return
            self.api_dance_library_import()
        elif path == "/api/dance-templates/import":
            if not self.require_auth():
                return
            self.api_dance_template_import()
        elif path == "/api/jobs/cancel":
            if not self.require_auth():
                return
            self.api_job_cancel()
        elif path == "/api/voices":
            if not self.require_auth():
                return
            self.api_voices_upload()
        elif path == "/api/voices/import-history":
            if not self.require_auth():
                return
            self.api_voices_import_history()
        elif path == "/api/voices/delete":
            if not self.require_auth():
                return
            self.api_voices_delete()
        elif path == "/api/history/hide":
            if not self.require_auth():
                return
            self.api_history_hide()
        elif path == "/api/history/delete":
            if not self.require_auth():
                return
            self.api_history_delete()
        elif path == "/api/history/rename":
            if not self.require_auth():
                return
            self.api_history_rename()
        elif path == "/api/history/import-input":
            if not self.require_auth():
                return
            self.api_history_import_input()
        elif path == "/api/history/adopt":
            if not self.require_auth():
                return
            self.api_history_adopt()
        elif path == "/api/admin/users":
            if not self.require_auth() or not self.require_admin():
                return
            self.api_admin_users_create()
        elif path == "/api/admin/users/update":
            if not self.require_auth() or not self.require_admin():
                return
            self.api_admin_users_update()
        elif path == "/api/admin/users/reset-password":
            if not self.require_auth() or not self.require_admin():
                return
            self.api_admin_users_reset_password()
        elif path == "/api/characters":
            if not self.require_orchestrator_auth():
                return
            self.api_character_create()
        elif path == "/api/characters/experimental":
            if not self.require_auth():
                return
            self.api_character_experimental_create()
        elif path.startswith("/api/characters/") and "/user-photo-import/" in path:
            if not self.require_auth():
                return
            self.api_character_user_photo_import(path)
        elif path.startswith("/api/characters/") and path.endswith("/wardrobe"):
            if not self.require_auth():
                return
            self.api_character_wardrobe_upload(path)
        elif path.startswith("/api/characters/") and "/wardrobe/" in path and path.endswith("/delete"):
            if not self.require_auth():
                return
            self.api_character_wardrobe_delete(path)
        elif path.startswith("/api/characters/") and path.endswith("/delete") and "/posts/" in path:
            if not self.require_auth():
                return
            self.api_character_post_delete(path)
        elif path.startswith("/api/characters/") and path.endswith("/delete"):
            if not self.require_orchestrator_auth():
                return
            self.api_character_delete(path)
        elif path.startswith("/api/characters/") and path.endswith("/rights/revoke"):
            if not self.require_auth() or not self.require_operator():
                return
            self.api_character_rights_revoke(path)
        elif path.startswith("/api/characters/"):
            if not self.require_orchestrator_auth():
                return
            self.api_character_update(path)
        elif path == "/api/orchestrator/runs":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_create_run()
        elif path.startswith("/api/orchestrator/runs/") and path.endswith("/cancel"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_cancel_run(path)
        elif path.startswith("/api/orchestrator/runs/") and path.endswith("/rerun"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_rerun_run(path)
        elif path.startswith("/api/orchestrator/runs/") and path.endswith("/review"):
            self.send_json({
                "ok": False,
                "error": "Reel verdicts are submitted only in AI-Sister Admin",
            }, 410)
        elif path.startswith("/api/orchestrator/runs/") and path.endswith("/review-page"):
            if not self.require_orchestrator_auth():
                return
            self.send_json({
                "ok": False,
                "error": "Unbound Castle Reel review pages are retired; use AI-Sister Admin",
            }, 410)
        elif path.startswith("/api/orchestrator/tasks/") and path.endswith("/retry"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_retry_task(path)
        elif path.startswith("/api/orchestrator/tasks/") and path.endswith("/cancel"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_cancel_task(path)
        elif path.startswith("/api/orchestrator/tasks/") and path.endswith("/mark-skipped"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_mark_task_skipped(path)
        elif path.startswith("/api/orchestrator/resources/") and path.endswith("/reserve"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_reserve_resource(path)
        elif path.startswith("/api/orchestrator/resources/") and path.endswith("/release"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_release_resource(path)
        elif path == "/api/orchestrator/scheduler/pause":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_pause_scheduler()
        elif path == "/api/orchestrator/scheduler/resume":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_resume_scheduler()
        elif path == "/api/orchestrator/artifacts/adopt":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_adopt_artifact()
        elif path == "/api/orchestrator/artifacts/adopt-batch":
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_adopt_artifacts()
        elif path.startswith("/api/orchestrator/artifacts/"):
            if not self.require_orchestrator_auth():
                return
            self.api_orchestrator_update_artifact(path)
        else:
            self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_PUT(self) -> None:
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "Forbidden origin"}, 403)
            return
        parsed = urllib.parse.urlparse(self.path)
        match = re.fullmatch(
            r"/api/providers/(autoip)/accounts/([a-z0-9][a-z0-9._:-]{0,127})/credential",
            parsed.path,
        )
        if match and not parsed.query:
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_credential(match.group(1), match.group(2))
            return
        asset_catalog = re.fullmatch(
            r"/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/catalog", parsed.path
        )
        if asset_catalog and not parsed.query:
            if not self.require_auth():
                return
            self.api_provider_asset_catalog(asset_catalog.group(1))
            return
        asset_classification = re.fullmatch(
            r"/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/classification",
            parsed.path,
        )
        if asset_classification and not parsed.query:
            if not self.require_auth() or not self.require_operator_or_admin():
                return
            self.api_provider_asset_classification(asset_classification.group(1))
            return
        rights = re.fullmatch(
            r"/api/characters/([a-z0-9][a-z0-9._:-]{0,127})/rights",
            parsed.path,
        )
        if rights and not parsed.query:
            if not self.require_auth() or not self.require_operator():
                return
            self.api_character_rights_put(rights.group(1))
            return
        self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_PATCH(self) -> None:
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "Forbidden origin"}, 403)
            return
        self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_DELETE(self) -> None:
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "Forbidden origin"}, 403)
            return
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.query and re.fullmatch(
            r"/api/providers/clipmode/imports/stages/stage_[0-9a-f]{32}",
            parsed.path,
        ):
            if not self.require_auth() or not self.require_admin():
                return
            self.api_provider_import_cancel(parsed.path)
            return
        self.send_json({"ok": False, "error": "Not found"}, 404)

    def serve_static(self, name: str) -> None:
        path = (STATIC_ROOT / name).resolve()
        if not path.is_relative_to(STATIC_ROOT) or not path.is_file():
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        self.send_local_file(path, cache_control="no-cache")

    def proxy_json(self, path: str) -> None:
        try:
            self.send_json(request_json(path))
        except Exception as error:
            self.send_error_json(error, 502)

    def _provider_runtime(self):
        return get_provider_runtime(
            PROVIDER_RUNTIME_CONFIG,
            capability_materializer=provider_fabric_capability_materializer,
            canonical_resource_resolver=provider_fabric_canonical_resource_resolver,
            h3_audit_source=provider_fabric_h3_audit_source,
            h3_variant_contracts=PROVIDER_FABRIC_H3_VARIANT_CONTRACTS,
        )

    def _provider_runtime_actor(self):
        user = self.current_user()
        identity = normalize_email(str((user or {}).get("email") or ""))
        if not identity:
            raise PermissionError("Authentication required")
        if (user or {}).get("role") == "admin":
            role = "admin"
        elif is_operator_user(user):
            role = "operator"
        else:
            role = "member"
        runtime = self._provider_runtime()
        actor = runtime.actor_context(identity, role)
        _PROVIDER_CANONICAL_CONTEXT.actor = actor
        _PROVIDER_CANONICAL_CONTEXT.user = dict(user)
        _PROVIDER_CANONICAL_CONTEXT.runtime = runtime
        return runtime, actor

    def _send_provider_failure(
        self,
        status: int = 503,
        code: str = "provider_runtime_unavailable",
        message: str = "Provider services are temporarily unavailable.",
    ) -> None:
        from provider_fabric.http_api import HttpResponse

        self.send_provider_response(HttpResponse(status, {
            "error": {
                "code": code,
                "message": message,
                "reason_codes": [],
            }
        }))

    def _production_owner_id(self) -> str:
        _runtime, actor = self._provider_runtime_actor()
        return actor.user_id

    def _rights_character(self, character_id: str) -> dict:
        entry = get_character_entry(character_id)
        if (
            not user_can_generate_character(self.current_user(), entry)
            or normalized_visual_class(entry.get("visualClass")) != "realistic"
            or str(entry.get("kind") or "").strip().lower() != "human"
        ):
            raise KeyError(character_id)
        return entry

    def api_character_rights_get(self, path: str) -> None:
        try:
            match = re.fullmatch(r"/api/characters/([a-z0-9][a-z0-9._:-]{0,127})/rights", path)
            if match is None:
                raise KeyError(path)
            self._rights_character(match.group(1))
            self.send_json(get_character_rights_api(
                character_rights_store(), self._production_owner_id(), match.group(1)
            ))
        except KeyError:
            self.send_json({"ok": False, "error": "Not found"}, 404)
        except Exception:
            self.send_json({"ok": False, "error": "Rights status unavailable"}, 500)

    def api_character_rights_put(self, character_id: str) -> None:
        try:
            self._rights_character(character_id)
            if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_json({"ok": False, "error": "JSON required"}, 415)
                return
            result = put_character_rights_api(
                character_rights_store(), self._production_owner_id(), character_id,
                self.read_provider_json(32 * 1024),
            )
            self.send_json(result)
        except RequestTooLarge:
            self.send_json({"ok": False, "error": "Request too large"}, 413)
        except RequestBodyError:
            self.send_json({"ok": False, "error": "Invalid request"}, 400)
        except KeyError:
            self.send_json({"ok": False, "error": "Not found"}, 404)
        except RuntimeError:
            self.send_json({"ok": False, "error": "Rights record changed"}, 409)
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": "Invalid rights contract"}, 422)
        except Exception:
            self.send_json({"ok": False, "error": "Rights update unavailable"}, 500)

    def api_character_rights_revoke(self, path: str) -> None:
        try:
            match = re.fullmatch(r"/api/characters/([a-z0-9][a-z0-9._:-]{0,127})/rights/revoke", path)
            if match is None:
                raise KeyError(path)
            self._rights_character(match.group(1))
            if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_json({"ok": False, "error": "JSON required"}, 415)
                return
            result = revoke_character_rights_api(
                character_rights_store(), self._production_owner_id(), match.group(1),
                self.read_provider_json(8 * 1024),
            )
            self.send_json(result)
        except RequestTooLarge:
            self.send_json({"ok": False, "error": "Request too large"}, 413)
        except RequestBodyError:
            self.send_json({"ok": False, "error": "Invalid request"}, 400)
        except KeyError:
            self.send_json({"ok": False, "error": "Not found"}, 404)
        except RuntimeError:
            self.send_json({"ok": False, "error": "Rights record changed"}, 409)
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": "Invalid rights revocation"}, 422)
        except Exception:
            self.send_json({"ok": False, "error": "Rights revocation unavailable"}, 500)

    def api_production_create(self) -> None:
        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Productions accept application/json only.")
                return
            body = self.read_provider_json(MAX_PROJECT_DRY_RUN_BODY)
            result = create_production_api(
                production_state_store(), self._production_owner_id(), body
            )
            self.send_project_run_json(result, 201)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The production request is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The production request is invalid.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "This production already exists or changed.")
        except (KeyError, TypeError, ValueError):
            self.send_production_error(422, "production_invalid", "The production contract is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production state is temporarily unavailable.")

    def api_series_get(self, path: str) -> None:
        try:
            series_id = path.removeprefix("/api/series/")
            self.send_project_run_json(get_series_api(
                series_continuity_store(), self._production_owner_id(), series_id,
            ))
        except KeyError:
            self.send_json({"ok": False, "error": {"code": "series_not_found"}}, 404)
        except Exception:
            self.send_json({"ok": False, "error": {"code": "series_unavailable"}}, 500)

    def api_series_create(self) -> None:
        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_json({"ok": False, "error": {"code": "unsupported_media_type"}}, 415)
                return
            body = self.read_provider_json(256 * 1024)
            self.send_project_run_json(create_series_api(
                series_continuity_store(), self._production_owner_id(), body,
            ), 201)
        except RequestTooLarge:
            self.send_json({"ok": False, "error": {"code": "request_too_large"}}, 413)
        except RequestBodyError:
            self.send_json({"ok": False, "error": {"code": "request_invalid"}}, 400)
        except SeriesContinuityConflict:
            self.send_json({"ok": False, "error": {"code": "series_conflict"}}, 409)
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": {"code": "series_invalid"}}, 422)
        except Exception:
            self.send_json({"ok": False, "error": {"code": "series_unavailable"}}, 500)

    def api_series_episode_append(self, path: str) -> None:
        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_json({"ok": False, "error": {"code": "unsupported_media_type"}}, 415)
                return
            match = re.fullmatch(r"/api/series/([a-z0-9][a-z0-9._:-]{0,127})/episodes", path)
            if match is None:
                raise ValueError("series episode route is invalid")
            body = self.read_provider_json(256 * 1024)
            self.send_project_run_json(append_series_episode_api(
                series_continuity_store(), self._production_owner_id(), match.group(1), body,
            ), 201)
        except RequestTooLarge:
            self.send_json({"ok": False, "error": {"code": "request_too_large"}}, 413)
        except RequestBodyError:
            self.send_json({"ok": False, "error": {"code": "request_invalid"}}, 400)
        except KeyError:
            self.send_json({"ok": False, "error": {"code": "series_not_found"}}, 404)
        except SeriesContinuityConflict:
            self.send_json({"ok": False, "error": {"code": "continuity_conflict"}}, 409)
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": {"code": "episode_invalid"}}, 422)
        except Exception:
            self.send_json({"ok": False, "error": {"code": "series_unavailable"}}, 500)

    def api_production_get(self, path: str) -> None:
        try:
            project_id = path.removeprefix("/api/productions/")
            result = get_production_api(
                production_state_store(), self._production_owner_id(), project_id
            )
            self.send_project_run_json(result)
        except KeyError:
            self.send_production_error(404, "production_not_found", "Production was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production integrity validation failed.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production state is temporarily unavailable.")

    def api_production_list(self) -> None:
        try:
            self.send_project_run_json(list_productions_api(
                production_state_store(), self._production_owner_id()
            ))
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production list integrity validation failed.")
        except Exception:
            LOG.exception("production list failed")
            self.send_production_error(500, "production_unavailable", "Production list is temporarily unavailable.")

    def api_production_resume(self, path: str) -> None:
        try:
            project_id = path.removeprefix("/api/productions/").removesuffix("/resume")
            result = resume_production_api(
                production_state_store(), self._production_owner_id(), project_id
            )
            self.send_project_run_json(result)
        except KeyError:
            self.send_production_error(404, "production_not_found", "Production was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production integrity validation failed.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production resume is temporarily unavailable.")

    def api_production_intents(self, path: str) -> None:
        try:
            project_id = path.removeprefix("/api/productions/").removesuffix("/intents")
            runtime, actor = self._provider_runtime_actor()
            result = list_production_intents_api(
                production_state_store(), runtime.store, actor.user_id, project_id
            )
            self.send_project_run_json(result)
        except KeyError:
            self.send_production_error(404, "production_not_found", "Production was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production intent lineage failed validation.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production intents are temporarily unavailable.")

    def api_production_outputs(self, path: str) -> None:
        try:
            project_id = path.removeprefix("/api/productions/").removesuffix("/outputs")
            runtime, actor = self._provider_runtime_actor()
            self.send_project_run_json(list_production_output_media_api(
                production_state_store(), runtime.store, actor.user_id, project_id
            ))
        except KeyError:
            self.send_production_error(404, "production_output_not_found", "Production output was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production output lineage failed validation.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production outputs are temporarily unavailable.")

    def api_production_output_media(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/shots/"
                r"([a-z0-9][a-z0-9._:-]{0,127})/versions/([1-9][0-9]*)/output",
                path,
            )
            if match is None:
                raise KeyError(path)
            runtime, actor = self._provider_runtime_actor()
            media = resolve_production_output_media(
                production_state_store(), runtime.store, actor.user_id,
                match.group(1), match.group(2), int(match.group(3)),
            )
            self.send_local_file(
                media.path, content_type=media.mime_type,
                inline_filename=media.filename, cache_control="private, no-store",
            )
        except (KeyError, FileNotFoundError):
            self.send_production_error(404, "production_output_not_found", "Production output was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production output integrity validation failed.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production output is temporarily unavailable.")

    def api_production_mutate(self, path: str) -> None:
        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Production mutations accept application/json only.")
                return
            project_id = path.removeprefix("/api/productions/").removesuffix("/mutations")
            body = self.read_provider_json(64 * 1024)
            result = mutate_production_api(
                production_state_store(), self._production_owner_id(), project_id, body
            )
            self.send_project_run_json(result)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The production mutation is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The production mutation is invalid.")
        except KeyError:
            self.send_production_error(404, "production_not_found", "Production was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "stale_production", "Production changed; reload before continuing.")
        except (TypeError, ValueError):
            self.send_production_error(422, "mutation_invalid", "The production mutation is not allowed.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production state is temporarily unavailable.")

    def api_production_prepare(self, path: str) -> None:
        """Persist one exact, owner-resolved, zero-spend shot preparation."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Shot preparation accepts application/json only.")
                return
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/shots/"
                r"([a-z0-9][a-z0-9._:-]{0,127})/prepare",
                path,
            )
            if match is None:
                raise ValueError("production preparation route is invalid")
            runtime, actor = self._provider_runtime_actor()
            h3_gate = provider_fabric_h3_audit_source()
            contract_states = h3_gate.get("contracts") if isinstance(h3_gate, dict) else {}
            ready_contracts = {
                contract_id for contract_id, ready in (contract_states or {}).items()
                if ready is True
            }
            profiles = runtime.autoip_profiles(actor) + h3_character_profiles(ready_contracts)
            profile_catalog = {profile.key: profile for profile in profiles}
            body = self.read_provider_json(128 * 1024)
            result = prepare_production_shot_api(
                production_state_store(), runtime.store, actor.user_id,
                match.group(1), match.group(2), body,
                profile_resolver=profile_catalog.__getitem__,
            )
            self.send_project_run_json(result, 201)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The shot preparation request is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The shot preparation request is invalid.")
        except KeyError:
            self.send_production_error(404, "preparation_resource_not_found", "The production, profile, or asset version was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "stale_production", "Production changed; reload before preparing this shot.")
        except (TypeError, ValueError):
            self.send_production_error(422, "preparation_invalid", "The shot preparation contract is not currently qualified.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Shot preparation is temporarily unavailable.")

    def api_production_preparation_options(self, path: str) -> None:
        """Read exact pack/profile candidates without resolving or exposing assets."""

        try:
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/shots/"
                r"([a-z0-9][a-z0-9._:-]{0,127})/preparation-options",
                path,
            )
            if match is None:
                raise ValueError("production preparation options route is invalid")
            runtime, actor = self._provider_runtime_actor()
            h3_gate = provider_fabric_h3_audit_source()
            contract_states = h3_gate.get("contracts") if isinstance(h3_gate, dict) else {}
            ready_contracts = {
                contract_id for contract_id, ready in (contract_states or {}).items()
                if ready is True
            }
            result = production_shot_preparation_options(
                production_state_store(), actor.user_id, match.group(1), match.group(2),
                profiles=runtime.autoip_profiles(actor) + h3_character_profiles(ready_contracts),
                provider_store=runtime.store,
            )
            self.send_project_run_json(result)
        except KeyError:
            self.send_production_error(404, "production_shot_not_found", "Production shot was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "production_conflict", "Production integrity validation failed.")
        except (TypeError, ValueError):
            self.send_production_error(422, "preparation_options_invalid", "Preparation options are unavailable for this shot.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Preparation options are temporarily unavailable.")

    def api_production_quote(self, path: str) -> None:
        """Return an authoritative signed quote without queueing any work."""

        from provider_fabric.service import ProviderServiceError

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Shot quotes accept application/json only.")
                return
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/shots/"
                r"([a-z0-9][a-z0-9._:-]{0,127})/quote",
                path,
            )
            if match is None:
                raise ValueError("production quote route is invalid")
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(64 * 1024)
            result = quote_prepared_production_shot_api(
                production_state_store(), runtime.store, runtime.service, actor,
                match.group(1), match.group(2), body,
            )
            self.send_project_run_json(result)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The shot quote request is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The shot quote request is invalid.")
        except KeyError:
            self.send_production_error(404, "preparation_not_found", "The shot preparation was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "stale_preparation", "Production changed; prepare this shot again.")
        except ProviderServiceError as error:
            self.send_production_error(error.status, error.code, str(error))
        except (TypeError, ValueError):
            self.send_production_error(422, "quote_invalid", "The prepared shot is not eligible for a quote.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Shot quoting is temporarily unavailable.")

    def api_production_repair(self, path: str) -> None:
        """Prepare an exact rejected-shot retry without queueing work."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Shot repair accepts application/json only.")
                return
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/shots/"
                r"([a-z0-9][a-z0-9._:-]{0,127})/repair",
                path,
            )
            if match is None:
                raise ValueError("production repair route is invalid")
            body = self.read_provider_json(64 * 1024)
            result = prepare_rejected_shot_repair_api(
                production_state_store(), self._production_owner_id(),
                match.group(1), match.group(2), body,
            )
            self.send_project_run_json(result, 201)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The shot repair request is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The shot repair request is invalid.")
        except KeyError:
            self.send_production_error(404, "repair_source_not_found", "The rejected shot or its preparation was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "stale_repair", "Production changed; reload before retrying this shot.")
        except (TypeError, ValueError):
            self.send_production_error(422, "repair_invalid", "Only the rejected latest shot version can be retried.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Shot repair is temporarily unavailable.")

    def api_production_edit(self, path: str) -> None:
        """Run deterministic CPU editing and persist mechanical QA evidence."""

        try:
            if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Production editing accepts application/json only.")
                return
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/edit", path
            )
            if match is None:
                raise ValueError("production edit route is invalid")
            runtime, actor = self._provider_runtime_actor()
            if runtime.provider_media_store is None:
                raise RuntimeError("private provider media is unavailable")
            result = execute_production_edit_api(
                production_state_store(), runtime.store,
                runtime.provider_media_store, actor.user_id, match.group(1),
                self.read_provider_json(64 * 1024),
                output_root=provider_database_path().parent / "production-edits",
                allowed_source_roots=(
                    Path.home() / "comfyui/output",
                    provider_database_path().parent / "autoip-outputs",
                ),
            )
            self.send_project_run_json(result, 201)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The production edit request is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The production edit request is invalid.")
        except KeyError:
            self.send_production_error(404, "edit_source_not_found", "An accepted edit source was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "stale_edit", "Production or edit evidence changed.")
        except (TypeError, ValueError):
            self.send_production_error(422, "edit_invalid", "The accepted production cannot be edited with this request.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Production editing is temporarily unavailable.")

    def api_production_delivery_review(self, path: str) -> None:
        try:
            if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_production_error(415, "unsupported_media_type", "Delivery review accepts application/json only.")
                return
            project_id = path.removeprefix("/api/productions/").removesuffix("/delivery-review")
            result = review_production_delivery_api(
                production_state_store(), self._production_owner_id(), project_id,
                self.read_provider_json(64 * 1024),
            )
            self.send_project_run_json(result, 201)
        except RequestTooLarge:
            self.send_production_error(413, "request_too_large", "The delivery review request is too large.")
        except RequestBodyError:
            self.send_production_error(400, "request_invalid", "The delivery review request is invalid.")
        except KeyError:
            self.send_production_error(404, "edit_receipt_not_found", "The edit receipt was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "stale_delivery_review", "Production or delivery evidence changed.")
        except (TypeError, ValueError):
            self.send_production_error(422, "delivery_review_invalid", "The delivery review is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Delivery review is temporarily unavailable.")

    def api_production_edit_output(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/edits/"
                r"([0-9a-f]{64})/output", path,
            )
            if match is None:
                raise KeyError(path)
            output, _receipt = resolve_production_edit_output(
                production_state_store(), self._production_owner_id(),
                match.group(1), match.group(2),
            )
            self.send_local_file(
                output, content_type="video/mp4",
                inline_filename=f"castle_{match.group(1)}.mp4",
                cache_control="private, no-store",
            )
        except (KeyError, ProductionStoreConflict, ValueError):
            self.send_production_error(404, "edit_output_not_found", "The verified edit output was not found.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Edit output is temporarily unavailable.")

    def api_production_contact_sheet(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/productions/([a-z0-9][a-z0-9._:-]{0,127})/edits/"
                r"([0-9a-f]{64})/contact-sheet", path,
            )
            if match is None:
                raise KeyError(path)
            output, _receipt = resolve_production_contact_sheet(
                production_state_store(), self._production_owner_id(),
                match.group(1), match.group(2),
            )
            self.send_local_file(
                output, content_type="image/jpeg",
                inline_filename=f"castle_{match.group(1)}_contact_sheet.jpg",
                cache_control="private, no-store",
            )
        except (KeyError, ProductionStoreConflict, ValueError):
            self.send_production_error(404, "contact_sheet_not_found", "The verified contact sheet was not found.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Contact sheet is temporarily unavailable.")

    def api_production_delivery_package(self, path: str) -> None:
        try:
            project_id = path.removeprefix("/api/productions/").removesuffix("/delivery-package")
            package = build_delivery_package(
                production_state_store(), self._production_owner_id(), project_id,
            )
            self.send_local_file(
                package.path, content_type="application/zip",
                inline_filename=package.filename, download=True,
                cache_control="private, no-store",
            )
        except (KeyError, FileNotFoundError):
            self.send_production_error(404, "delivery_package_not_found", "The accepted delivery package was not found.")
        except ProductionStoreConflict:
            self.send_production_error(409, "delivery_package_changed", "Delivery package evidence changed.")
        except ValueError:
            self.send_production_error(422, "delivery_package_not_ready", "The final production must be accepted before packaging.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_production_error(500, "production_unavailable", "Delivery packaging is temporarily unavailable.")

    def api_project_dry_run(self) -> None:
        """Compile a bounded static project preview without any runtime I/O."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self.send_project_dry_run_error(
                    415,
                    "unsupported_media_type",
                    "Production-pack preview accepts application/json only.",
                )
                return
            body = self.read_provider_json(MAX_PROJECT_DRY_RUN_BODY)
            preview = preview_production_pack_request(body)
            self.send_project_dry_run_json(preview)
        except RequestTooLarge:
            self.send_project_dry_run_error(
                413,
                "request_too_large",
                "The production-pack preview request is too large.",
            )
        except RequestBodyError:
            self.send_project_dry_run_error(
                400,
                "dry_run_request_invalid",
                "The production-pack preview request is invalid.",
            )
        except PreviewRequestError:
            self.send_project_dry_run_error(
                400,
                "dry_run_request_invalid",
                "The production-pack preview request is invalid.",
            )
        except PreviewLimitError:
            self.send_project_dry_run_error(
                422,
                "preview_limit_exceeded",
                "The production pack exceeds the static preview limits.",
            )
        except PreviewPackError:
            self.send_project_dry_run_error(
                422,
                "production_pack_invalid",
                "The production pack is invalid.",
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_project_dry_run_error(
                500,
                "dry_run_unavailable",
                "Production-pack preview is temporarily unavailable.",
            )

    def api_project_brief_compile(self) -> None:
        """Compile a human brief into an unresolved, zero-side-effect storyboard."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_project_brief_error(415, "unsupported_media_type", "Production briefs accept application/json only.")
                return
            body = self.read_provider_json(MAX_PROJECT_BRIEF_BODY)
            submitted_characters = body.get("characters") if isinstance(body, dict) else None
            if not isinstance(submitted_characters, list):
                raise ValueError("brief characters are invalid")
            user = self.current_user()
            canonical_characters = {}
            for item in submitted_characters:
                character_id = item.get("character_id") if isinstance(item, dict) else None
                if not isinstance(character_id, str):
                    raise ValueError("brief character is invalid")
                entry = get_character_entry(character_id)
                if not user_can_generate_character(user, entry):
                    raise ValueError("brief character is unavailable")
                visual_class = normalized_visual_class(entry.get("visualClass"))
                avatar = character_asset_path(entry, "avatar")
                if visual_class not in {"anime", "realistic", "illustration"} or avatar is None:
                    raise ValueError("brief character is not ready")
                canonical_characters[character_id] = {
                    "display_name": str(entry.get("name") or character_id)[:160],
                    "visual_class": visual_class,
                    "profile_asset_id": f"castle-character:{character_id}:avatar",
                }
            body = bind_canonical_brief_characters(body, canonical_characters)
            draft = compile_brief_request(body)
            self.send_project_dry_run_json({
                "schema_version": "castle.production-brief.response.v1",
                "ok": True,
                "draft": draft,
                "director_plan": compile_director_plan(draft),
                "authority": {
                    "computed_by": "castle_server",
                    "execution_authorized": False,
                    "credentials_accessed": False,
                    "runtime_catalog_checked": False,
                },
                "side_effects": {
                    "records_created": 0,
                    "tasks_scheduled": 0,
                    "comfy_prompts_queued": 0,
                    "provider_transports_attempted": 0,
                    "credits_authorized": 0,
                },
            })
        except RequestTooLarge:
            self.send_project_brief_error(413, "request_too_large", "The production brief is too large.")
        except RequestBodyError:
            self.send_project_brief_error(400, "brief_request_invalid", "The production brief request is invalid.")
        except ValueError:
            self.send_project_brief_error(422, "brief_contract_invalid", "The production brief does not match the supported contract.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_project_brief_error(500, "brief_unavailable", "Production brief compilation is temporarily unavailable.")

    def api_project_brief_route_review(self) -> None:
        """Explain exact-class route choices without selecting or executing one."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_project_brief_error(415, "unsupported_media_type", "Route review accepts application/json only.")
                return
            body = self.read_provider_json(MAX_PROJECT_BRIEF_BODY)
            submitted_characters = body.get("characters") if isinstance(body, dict) else None
            if not isinstance(submitted_characters, list):
                raise ValueError("brief characters are invalid")
            user = self.current_user()
            canonical_characters = {}
            for item in submitted_characters:
                character_id = item.get("character_id") if isinstance(item, dict) else None
                if not isinstance(character_id, str):
                    raise ValueError("brief character is invalid")
                entry = get_character_entry(character_id)
                if not user_can_generate_character(user, entry):
                    raise ValueError("brief character is unavailable")
                visual_class = normalized_visual_class(entry.get("visualClass"))
                avatar = character_asset_path(entry, "avatar")
                if visual_class not in {"anime", "realistic", "illustration"} or avatar is None:
                    raise ValueError("brief character is not ready")
                canonical_characters[character_id] = {
                    "display_name": str(entry.get("name") or character_id)[:160],
                    "visual_class": visual_class,
                    "profile_asset_id": f"castle-character:{character_id}:avatar",
                }
            canonical = bind_canonical_brief_characters(body, canonical_characters)
            draft = compile_brief_request(canonical)
            h3_gate = provider_fabric_h3_audit_source()
            contract_states = h3_gate.get("contracts") if isinstance(h3_gate, dict) else {}
            ready_contracts = {
                contract_id for contract_id, ready in (contract_states or {}).items()
                if ready is True
            }
            runtime, actor = self._provider_runtime_actor()
            profiles = runtime.autoip_profiles(actor) + h3_character_profiles(ready_contracts)
            rights_by_character = {}
            owner_user_id = self._production_owner_id()
            for character in draft["characters"]:
                if character["visual_class"] != "realistic":
                    continue
                record = character_rights_store().get(owner_user_id, character["character_id"])
                if record is not None:
                    rights_by_character[character["character_id"]] = record.public_summary()
            review = compile_storyboard_route_review(
                draft,
                profiles,
                rights_by_character=rights_by_character,
                usage_scope=production_usage_scope(draft["platform"]),
            )
            director_plan = compile_director_plan(draft)
            self.send_project_dry_run_json({
                "schema_version": "castle.storyboard-route-review.response.v1",
                "ok": True,
                "draft": draft,
                "director_plan": director_plan,
                "route_review": review,
                "pack_proposal": compile_pack_proposal(
                    draft, director_plan, review,
                    rights_by_character=rights_by_character,
                ),
                "authority": {
                    "computed_by": "castle_server",
                    "runtime_catalog_checked": True,
                    "selection_authorized": False,
                    "execution_authorized": False,
                    "credentials_accessed": False,
                },
                "side_effects": {
                    "records_created": 0,
                    "tasks_scheduled": 0,
                    "comfy_prompts_queued": 0,
                    "provider_transports_attempted": 0,
                    "credits_authorized": 0,
                },
            })
        except RequestTooLarge:
            self.send_project_brief_error(413, "request_too_large", "The production brief is too large.")
        except RequestBodyError:
            self.send_project_brief_error(400, "brief_request_invalid", "The production brief request is invalid.")
        except (KeyError, ValueError):
            self.send_project_brief_error(422, "brief_contract_invalid", "The production brief does not match the supported contract.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_project_brief_error(500, "route_review_unavailable", "Storyboard route review is temporarily unavailable.")

    def api_project_brief_formal_pack(self) -> None:
        """Recompute and compile an evidence-complete strict Pack without execution."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self.send_project_brief_error(415, "unsupported_media_type", "Formal Pack compilation accepts application/json only.")
                return
            body = self.read_provider_json(MAX_PROJECT_BRIEF_BODY)
            if not isinstance(body, dict) or set(body) != {"production_brief", "continuity_state"}:
                raise ValueError("formal Pack request fields are invalid")
            brief = body["production_brief"]
            continuity_state = body["continuity_state"]
            submitted_characters = brief.get("characters") if isinstance(brief, dict) else None
            if not isinstance(submitted_characters, list) or not isinstance(continuity_state, dict):
                raise ValueError("formal Pack request is invalid")
            user = self.current_user()
            canonical_characters = {}
            for item in submitted_characters:
                character_id = item.get("character_id") if isinstance(item, dict) else None
                if not isinstance(character_id, str):
                    raise ValueError("brief character is invalid")
                entry = get_character_entry(character_id)
                if not user_can_generate_character(user, entry):
                    raise ValueError("brief character is unavailable")
                visual_class = normalized_visual_class(entry.get("visualClass"))
                avatar = character_asset_path(entry, "avatar")
                if visual_class not in {"anime", "realistic", "illustration"} or avatar is None:
                    raise ValueError("brief character is not ready")
                canonical_characters[character_id] = {
                    "display_name": str(entry.get("name") or character_id)[:160],
                    "visual_class": visual_class,
                    "profile_asset_id": f"castle-character:{character_id}:avatar",
                }
            canonical = bind_canonical_brief_characters(brief, canonical_characters)
            draft = compile_brief_request(canonical)
            h3_gate = provider_fabric_h3_audit_source()
            contract_states = h3_gate.get("contracts") if isinstance(h3_gate, dict) else {}
            ready_contracts = {
                contract_id for contract_id, ready in (contract_states or {}).items() if ready is True
            }
            runtime, actor = self._provider_runtime_actor()
            profiles = runtime.autoip_profiles(actor) + h3_character_profiles(ready_contracts)
            rights_by_character = {}
            for character in draft["characters"]:
                if character["visual_class"] != "realistic":
                    continue
                record = character_rights_store().get(actor.user_id, character["character_id"])
                if record is not None:
                    rights_by_character[character["character_id"]] = record.public_summary()
            review = compile_storyboard_route_review(
                draft, profiles, rights_by_character=rights_by_character,
                usage_scope=production_usage_scope(draft["platform"]),
            )
            director_plan = compile_director_plan(draft)
            proposal = compile_pack_proposal(
                draft, director_plan, review, rights_by_character=rights_by_character,
            )
            resolved = resolve_formal_pack_evidence(
                proposal, owner_user_id=actor.user_id, provider_store=runtime.store,
                rights_store=character_rights_store(), h3_audit=h3_gate,
                h3_golden_record=DEFAULT_GOLDEN_RECORD,
            )
            soundtrack_binding = None
            soundtrack_version = canonical.get("soundtrack_asset_version_id")
            if soundtrack_version is not None:
                source = runtime.store.get_owned_remote_asset_version_source_by_id(
                    actor.user_id, soundtrack_version
                )
                projection = runtime.store.get_owned_remote_asset(
                    actor.user_id, str(source["projection_id"])
                )
                soundtrack_versions = [
                    item for item in projection.get("versions", ())
                    if isinstance(item, dict) and item.get("version_id") == soundtrack_version
                ]
                if len(soundtrack_versions) != 1:
                    raise ValueError("soundtrack asset version is unavailable")
                soundtrack_version_record = soundtrack_versions[0]
                soundtrack_binding = {
                    "asset_version_id": soundtrack_version,
                    "sha256": soundtrack_version_record.get("sha256"),
                    "media_kind": projection.get("media_kind"),
                    "source_available": soundtrack_version_record.get("source_available") is True,
                }
            sound_effect_bindings = {}
            effect_versions = {
                effect.get("asset_version_id")
                for beat in canonical.get("beats", ()) if isinstance(beat, dict)
                for effect in beat.get("sound_effects", ()) if isinstance(effect, dict)
            }
            if None in effect_versions:
                raise ValueError("sound effect asset version is invalid")
            for effect_version in effect_versions:
                source = runtime.store.get_owned_remote_asset_version_source_by_id(
                    actor.user_id, effect_version
                )
                projection = runtime.store.get_owned_remote_asset(
                    actor.user_id, str(source["projection_id"])
                )
                versions = [
                    item for item in projection.get("versions", ())
                    if isinstance(item, dict) and item.get("version_id") == effect_version
                ]
                if len(versions) != 1:
                    raise ValueError("sound effect asset version is unavailable")
                version = versions[0]
                sound_effect_bindings[effect_version] = {
                    "asset_version_id": effect_version,
                    "sha256": version.get("sha256"),
                    "media_kind": projection.get("media_kind"),
                    "source_available": version.get("source_available") is True,
                    "duration_seconds": version.get("duration_seconds"),
                }
            result = compile_formal_production_pack(
                proposal, evidence_by_id=resolved["evidence_by_id"],
                rights_by_character=resolved["rights_by_character"],
                provider_accounts=resolved["provider_accounts"], continuity_state=continuity_state,
                soundtrack_binding=soundtrack_binding,
                sound_effect_bindings=sound_effect_bindings,
            )
            self.send_project_dry_run_json({
                **result,
                "schema_version": "castle.formal-pack.response.v1", "ok": True,
                "authority": {
                    "computed_by": "castle_server", "evidence_resolved_server_side": True,
                    "private_paths_exposed": False, "execution_authorized": False,
                    "provider_submission_authorized": False, "gpu_execution_authorized": False,
                    "credits_authorized": 0, "publishing_authorized": False,
                },
            })
        except RequestTooLarge:
            self.send_project_brief_error(413, "request_too_large", "The formal Pack request is too large.")
        except RequestBodyError:
            self.send_project_brief_error(400, "brief_request_invalid", "The formal Pack request is invalid.")
        except (KeyError, TypeError, ValueError):
            self.send_project_brief_error(422, "formal_pack_not_ready", "The formal Pack is missing current rights, evidence, routes, or continuity.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_project_brief_error(500, "formal_pack_unavailable", "Formal Pack compilation is temporarily unavailable.")

    def api_project_run_create(self) -> None:
        """Persist one operator-only, no-spend qualification DAG."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self.send_project_run_error(
                    415,
                    "unsupported_media_type",
                    "Project qualification accepts application/json only.",
                )
                return
            body = self.read_provider_json(MAX_PROJECT_RUN_BODY)
            result = create_project_run_api(
                body,
                create_run=castle_orchestrator.create_run_api,
            )
            replay = bool((result.get("run") or {}).get("idempotent_replay"))
            self.send_project_run_json(result, 200 if replay else 202)
        except RequestTooLarge:
            self.send_project_run_error(
                413, "request_too_large", "The project qualification request is too large."
            )
        except RequestBodyError:
            self.send_project_run_error(
                400, "project_run_request_invalid", "The project qualification request is invalid."
            )
        except ProjectQualificationError:
            self.send_project_run_error(
                422, "project_qualification_invalid", "The project qualification contract is invalid."
            )
        except ProjectRunError:
            self.send_project_run_error(
                500, "project_run_persistence_failed", "Project qualification could not be persisted."
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_project_run_error(
                500, "project_run_unavailable", "Project qualification is temporarily unavailable."
            )

    def api_project_run_get(self, path: str) -> None:
        """Read one qualification run without exposing its pack or artifacts."""

        try:
            run_id = path.removeprefix("/api/projects/runs/")
            result = get_project_run_api(
                run_id,
                get_run=castle_orchestrator.get_run_api,
            )
            self.send_project_run_json(result)
        except (KeyError, ProjectRunError):
            self.send_project_run_error(
                404, "project_run_not_found", "Project qualification run was not found."
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self.send_project_run_error(
                500, "project_run_unavailable", "Project qualification is temporarily unavailable."
            )

    def api_provider_inventory(self) -> None:
        try:
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(runtime.http_api.get_inventory(actor))
        except Exception:
            self._send_provider_failure()

    def api_provider_assets(self, query: str) -> None:
        try:
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(runtime.http_api.get_assets(actor, query))
        except Exception:
            self._send_provider_failure()

    def api_provider_asset_generation_detail(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/generation-detail",
                path,
            )
            if match is None:
                raise KeyError(path)
            runtime, actor = self._provider_runtime_actor()
            self.send_json(owned_asset_generation_detail(
                runtime.store, actor.user_id, match.group(1)
            ))
        except KeyError:
            self._send_provider_failure(404, "not_found", "Asset generation detail was not found.")
        except Exception:
            self._send_provider_failure()

    def api_provider_asset_preview(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/preview", path
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Asset preview was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            if runtime.media_preview_http is None:
                self._send_provider_failure(
                    404, "not_found", "Asset preview was not found."
                )
                return
            self.send_provider_binary_response(
                runtime.media_preview_http.get_preview(actor, match.group(1))
            )
        except Exception:
            self._send_provider_failure()

    def api_provider_asset_source(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/source", path
            )
            if match is None:
                raise KeyError(path)
            runtime, actor = self._provider_runtime_actor()
            if runtime.provider_media_store is None:
                raise KeyError(match.group(1))
            media = resolve_owned_media_source(
                runtime.store, runtime.provider_media_store,
                actor.user_id, match.group(1),
                allowed_file_roots=(
                    minimax_h3_output_root(),
                    provider_database_path().parent / "autoip-outputs",
                    provider_database_path().parent / "production-edits",
                ),
            )
            self.send_local_file(
                media.path, content_type=media.mime_type,
                inline_filename=media.filename, cache_control="private, no-store",
            )
        except (KeyError, FileNotFoundError):
            self._send_provider_failure(404, "not_found", "Asset source was not found.")
        except Exception:
            self._send_provider_failure(409, "asset_source_changed", "Asset source failed integrity validation.")

    def api_provider_asset_handoff(self, path: str) -> None:
        """Resolve one immutable asset into exact-class, zero-spend planner candidates."""

        try:
            match = re.fullmatch(
                r"/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/handoff", path
            )
            if match is None:
                raise KeyError(path)
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._send_provider_failure(415, "unsupported_media_type", "Media handoff accepts application/json only.")
                return
            body = self.read_provider_json(8 * 1024)
            runtime, actor = self._provider_runtime_actor()
            h3_gate = provider_fabric_h3_audit_source()
            contracts = h3_gate.get("contracts") if isinstance(h3_gate, dict) else {}
            ready_contracts = {
                contract_id for contract_id, ready in (contracts or {}).items()
                if ready is True
            }
            result = prepare_asset_handoff(
                runtime.store, actor.user_id, match.group(1), body,
                runtime.autoip_profiles(actor) + h3_character_profiles(ready_contracts),
                rights_by_character={
                    character_id: record.public_summary()
                    for character_id in runtime.store.get_owned_remote_asset(
                        actor.user_id, match.group(1)
                    ).get("character_ids", ())
                    if (record := character_rights_store().get(
                        actor.user_id, character_id
                    )) is not None
                },
            )
            self.send_json(result)
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Media handoff request is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Media handoff request is invalid.")
        except KeyError:
            self._send_provider_failure(404, "not_found", "Media asset version was not found.")
        except (TypeError, ValueError):
            self._send_provider_failure(422, "handoff_unavailable", "Media asset cannot enter a qualified route.")
        except Exception:
            self._send_provider_failure(500, "handoff_unavailable", "Media handoff is temporarily unavailable.")

    def api_provider_characters(self, query: str) -> None:
        """Return only the authenticated actor's provider-canonical roster."""

        try:
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(runtime.http_api.get_characters(actor, query))
        except Exception:
            self._send_provider_failure()

    def api_provider_credential(self, provider_id: str, account_id: str) -> None:
        try:
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(MAX_TEXT_BODY)
            self.send_provider_response(
                runtime.http_api.put_autoip_credential(
                    actor,
                    provider_id,
                    account_id,
                    body,
                )
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_asset_catalog(self, projection_id: str) -> None:
        try:
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(8 * 1024)
            self.send_provider_response(
                runtime.http_api.put_asset_catalog_annotation(actor, projection_id, body)
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Catalog request is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Catalog request is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_asset_classification(self, projection_id: str) -> None:
        """Seal one exact local AutoIP-history classification decision."""

        try:
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(8 * 1024)
            self.send_provider_response(
                runtime.http_api.put_asset_classification(
                    actor, projection_id, body
                )
            )
        except RequestTooLarge:
            self._send_provider_failure(
                413, "request_too_large", "Classification request is too large."
            )
        except RequestBodyError:
            self._send_provider_failure(
                400, "schema_invalid", "Classification request is invalid."
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_plan(self) -> None:
        try:
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(MAX_TEXT_BODY)
            self.send_provider_response(runtime.http_api.post_plan(actor, body))
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_generation(self) -> None:
        """Plan and durably queue one provider-neutral generation request."""

        try:
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(MAX_TEXT_BODY)
            self.send_provider_response(
                runtime.http_api.post_generation(actor, body)
            )
        except RequestTooLarge:
            self._send_provider_failure(
                413, "request_too_large", "Request body is too large."
            )
        except RequestBodyError:
            self._send_provider_failure(
                400, "schema_invalid", "Request body is invalid."
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_clipmode_preview(self) -> None:
        """Validate one sanitized ClipMode JSON/ZIP export without persisting it."""

        try:
            content_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "application/zip"}:
                self._send_provider_failure(
                    415,
                    "unsupported_media_type",
                    "ClipMode preview accepts only JSON or ZIP exports.",
                )
                return
            source = self.read_body(MAX_CLIPMODE_PREVIEW_BODY)
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(
                runtime.http_api.post_clipmode_import_preview(actor, source)
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Import preview is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Import preview body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def _provider_owner_import(self):
        runtime, actor = self._provider_runtime_actor()
        if runtime.owner_import_http is None:
            raise RuntimeError("Owner import service is disabled")
        return runtime.owner_import_http, actor

    def api_provider_autoip_webhook(self) -> None:
        """Verify one fixed AutoIP callback and trigger authoritative GET recovery."""

        from provider_fabric.service import ProviderServiceError

        try:
            get_all = getattr(self.headers, "get_all", None)
            content_types = get_all("Content-Type") if callable(get_all) else None
            signatures = get_all("X-AutoIP-Signature") if callable(get_all) else None
            if content_types is not None and len(content_types) != 1:
                raise RequestBodyError("ambiguous webhook content type")
            if signatures is not None and len(signatures) != 1:
                raise RequestBodyError("ambiguous webhook signature")
            content_type = (
                content_types[0]
                if content_types is not None
                else self.headers.get("Content-Type")
            )
            media_type = str(content_type or "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self._send_provider_failure(
                    415,
                    "unsupported_media_type",
                    "AutoIP webhook accepts application/json only.",
                )
                return
            raw_body = self.read_body(MAX_WEBHOOK_BYTES)
            signature = str(
                signatures[0]
                if signatures is not None
                else self.headers.get("X-AutoIP-Signature") or ""
            )
            runtime = self._provider_runtime()
            verified = runtime.verify_autoip_webhook(raw_body, signature)
            signal = verified.get("signal") if isinstance(verified, dict) else None
            task_uuid = signal.get("task_uuid") if isinstance(signal, dict) else None
            if (
                verified.get("accepted") is not True
                or not isinstance(signal, dict)
                or signal.get("action") != "poll_authoritative_task"
                or not isinstance(task_uuid, str)
            ):
                raise RuntimeError("verified webhook signal is invalid")
            try:
                # The reconciler receives only the task identity authenticated
                # above.  Payload status, URLs, prices, and output fields never
                # cross this call boundary; GET /tasks/{uuid} is authoritative.
                runtime.reconcile_autoip_webhook_task(
                    task_uuid,
                    production_store=production_state_store(),
                )
            except Exception:
                # Durable polling remains the recovery path.  The signed
                # delivery has already been replay-claimed, so asking AutoIP to
                # resend it cannot make reconciliation safer.
                pass
            self.send_json({
                "schema_version": 1,
                "ok": True,
                "accepted": True,
            }, 202)
        except RequestTooLarge:
            self._send_provider_failure(
                413,
                "request_too_large",
                "AutoIP webhook request is too large.",
            )
        except RequestBodyError:
            self._send_provider_failure(
                400,
                "webhook_invalid",
                "Webhook verification failed.",
            )
        except ProviderServiceError as error:
            self._send_provider_failure(error.status, error.code, str(error))
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure(
                503,
                "webhook_unavailable",
                "AutoIP webhook is temporarily unavailable.",
            )

    def api_provider_import_stage(self) -> None:
        """Persist one bounded owner-private ClipMode export for review."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type not in {"application/json", "application/zip"}:
                self._send_provider_failure(
                    415,
                    "unsupported_media_type",
                    "ClipMode import accepts only JSON or ZIP exports.",
                )
                return
            source = self.read_body(MAX_CLIPMODE_PREVIEW_BODY)
            api, actor = self._provider_owner_import()
            self.send_provider_response(
                api.post_stage(actor, source=source, content_type=media_type)
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Import is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Import body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_autoip_probe(self, path: str) -> None:
        """Run an explicit non-spending compatibility probe for one owned account."""

        try:
            match = re.fullmatch(
                r"/api/providers/autoip/accounts/([a-z0-9][a-z0-9._:-]{0,127})/probe",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(MAX_TEXT_BODY)
            self.send_provider_response(
                runtime.autoip_probe_http.post_probe(actor, match.group(1), body)
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_autoip_golden(self, path: str) -> None:
        """Issue one exact owner/account golden from two adopted outputs."""

        try:
            match = re.fullmatch(
                r"/api/providers/autoip/accounts/([a-z0-9][a-z0-9._:-]{0,127})/goldens",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(MAX_TEXT_BODY)
            self.send_provider_response(
                runtime.autoip_golden_http.post_issue(actor, match.group(1), body)
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_autoip_golden_candidates(self, path: str) -> None:
        """List adopted AutoIP outputs grouped by exact qualification cell."""

        try:
            match = re.fullmatch(
                r"/api/providers/autoip/accounts/([a-z0-9][a-z0-9._:-]{0,127})/golden-candidates",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(
                runtime.autoip_golden_http.get_candidates(actor, match.group(1))
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_autoip_catalog(self, path: str) -> None:
        """Return the latest owner-private, redacted AutoIP catalog snapshot."""

        try:
            match = re.fullmatch(
                r"/api/providers/autoip/accounts/([a-z0-9][a-z0-9._:-]{0,127})/catalog",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(
                runtime.autoip_catalog_http.get_snapshot(actor, match.group(1))
            )
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_autoip_catalog_sync(self, path: str) -> None:
        """Explicitly refresh zero-spend task and dance-template metadata."""

        try:
            match = re.fullmatch(
                r"/api/providers/autoip/accounts/([a-z0-9][a-z0-9._:-]{0,127})/catalog/sync",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(8 * 1024)
            self.send_provider_response(
                runtime.autoip_catalog_http.post_sync(actor, match.group(1), body)
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_autoip_history_adopt(self, path: str) -> None:
        """Privately download and adopt one explicit completed historical task."""

        try:
            match = re.fullmatch(
                r"/api/providers/autoip/accounts/([a-z0-9][a-z0-9._:-]{0,127})/catalog/tasks/"
                r"([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/adopt",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(8 * 1024)
            self.send_provider_response(
                runtime.autoip_catalog_http.post_adopt_task(
                    actor, match.group(1), match.group(2), body,
                )
            )
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_import_plan(self, path: str) -> None:
        self._api_provider_import_stage_json(path, action="plan")

    def api_provider_import_confirm(self, path: str) -> None:
        self._api_provider_import_stage_json(path, action="confirm")

    def api_provider_import_cancel(self, path: str) -> None:
        self._api_provider_import_stage_json(path, action="cancel")

    def _api_provider_import_stage_json(self, path: str, *, action: str) -> None:
        try:
            suffix = "" if action == "cancel" else f"/{action}"
            match = re.fullmatch(
                rf"/api/providers/clipmode/imports/stages/(stage_[0-9a-f]{{32}}){suffix}",
                path,
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Import stage was not found.")
                return
            body = self.read_provider_json(MAX_TEXT_BODY)
            api, actor = self._provider_owner_import()
            operation = {
                "plan": api.post_plan,
                "confirm": api.post_confirm,
                "cancel": api.delete_stage,
            }[action]
            self.send_provider_response(operation(actor, match.group(1), body))
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_import_receipt(self, path: str) -> None:
        try:
            match = re.fullmatch(
                r"/api/provider-import-receipts/(receipt:[0-9a-f]{32})", path
            )
            if match is None:
                self._send_provider_failure(404, "not_found", "Import receipt was not found.")
                return
            api, actor = self._provider_owner_import()
            self.send_provider_response(api.get_receipt(actor, match.group(1)))
        except Exception:
            self._send_provider_failure()

    def api_provider_confirm(self, path: str) -> None:
        try:
            match = re.fullmatch(r"/api/intents/([^/]+)/confirm", path)
            plan_id = urllib.parse.unquote(match.group(1)) if match is not None else ""
            if not re.fullmatch(r"plan:[0-9a-f]{32,64}", plan_id):
                self._send_provider_failure(404, "not_found", "Provider route was not found.")
                return
            runtime, actor = self._provider_runtime_actor()
            body = self.read_provider_json(MAX_TEXT_BODY)
            self.send_provider_response(runtime.http_api.post_confirm(actor, plan_id, body))
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Request body is too large.")
        except RequestBodyError:
            self._send_provider_failure(400, "schema_invalid", "Request body is invalid.")
        except ClientDisconnected:
            self.close_connection = True
        except Exception:
            self._send_provider_failure()

    def api_provider_intent_status(self, path: str) -> None:
        """Return one owner-scoped durable provider job status without transport."""

        try:
            match = re.fullmatch(r"/api/intents/([^/]+)/status", path)
            intent_id = urllib.parse.unquote(match.group(1)) if match is not None else ""
            if not re.fullmatch(r"intent:[0-9a-f]{32}", intent_id):
                self._send_provider_failure(
                    404,
                    "intent_not_found",
                    "Provider intent was not found.",
                )
                return
            runtime, actor = self._provider_runtime_actor()
            self.send_provider_response(
                runtime.http_api.get_intent_status(actor, intent_id)
            )
        except Exception:
            self._send_provider_failure()

    def api_admin_users(self) -> None:
        try:
            users = load_users()
            recent = recent_jobs_by_email()
            rows = [
                public_user_record(email, record, recent.get(email, []))
                for email, record in users.items()
            ]
            rows.sort(key=lambda item: item.get("created", 0))
            self.send_json({"ok": True, "users": rows})
        except Exception as error:
            self.send_error_json(error, 500)

    def api_admin_users_create(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            email = normalize_email(str(body.get("email") or ""))
            if not EMAIL_RE.fullmatch(email):
                self.send_json({"ok": False, "error": "Email 格式不正確"}, 400)
                return
            role = str(body.get("role") or "user")
            if role not in ("admin", "user"):
                self.send_json({"ok": False, "error": "角色不正確"}, 400)
                return
            now = time.time()
            with _USERS_LOCK:
                users = load_users()
                if email in users:
                    self.send_json({"ok": False, "error": "使用者已存在"}, 409)
                    return
                users[email] = {
                    "displayName": str(body.get("displayName") or email).strip() or email,
                    "role": role,
                    "disabled": False,
                    "passwordHash": None,
                    "mustSetPassword": True,
                    "created": now,
                    "updated": now,
                }
                save_users(users)
                user = public_user_record(email, users[email])
            self.send_json({"ok": True, "user": user})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_admin_users_update(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            email = normalize_email(str(body.get("email") or ""))
            if not EMAIL_RE.fullmatch(email):
                self.send_json({"ok": False, "error": "Email 格式不正確"}, 400)
                return
            with _USERS_LOCK:
                users = load_users()
                if email not in users:
                    self.send_json({"ok": False, "error": "找不到使用者"}, 404)
                    return
                next_user = dict(users[email])
                if "displayName" in body:
                    next_user["displayName"] = str(body.get("displayName") or email).strip() or email
                if "role" in body:
                    role = str(body.get("role") or "")
                    if role not in ("admin", "user"):
                        self.send_json({"ok": False, "error": "角色不正確"}, 400)
                        return
                    next_user["role"] = role
                if "disabled" in body:
                    next_user["disabled"] = bool(body.get("disabled"))
                next_user["updated"] = time.time()
                next_users = dict(users)
                next_users[email] = next_user
                if enabled_admin_count(next_users) < 1:
                    self.send_json({"ok": False, "error": "至少需要一個啟用的管理員"}, 400)
                    return
                save_users(next_users)
                user = public_user_record(email, next_user)
            self.send_json({"ok": True, "user": user})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_admin_users_reset_password(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            email = normalize_email(str(body.get("email") or ""))
            if not EMAIL_RE.fullmatch(email):
                self.send_json({"ok": False, "error": "Email 格式不正確"}, 400)
                return
            with _USERS_LOCK:
                users = load_users()
                if email not in users:
                    self.send_json({"ok": False, "error": "找不到使用者"}, 404)
                    return
                user = dict(users[email])
                user["passwordHash"] = None
                user["mustSetPassword"] = True
                user["updated"] = time.time()
                users[email] = user
                save_users(users)
                public = public_user_record(email, user)
            self.send_json({"ok": True, "user": public})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_queue(self, query: str = "") -> None:
        try:
            user = self.current_user()
            scope, access_user = job_scope(query, user)
            queue = request_json("/queue")
            with _JOB_INDEX_LOCK:
                job_index = load_job_index()
            if scope == "own":
                queue["queue_running"] = [
                    entry for entry in (queue.get("queue_running") or [])
                    if user_can_access_job(access_user, str((entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else "")), entry, job_index)
                ]
                queue["queue_pending"] = [
                    entry for entry in (queue.get("queue_pending") or [])
                    if user_can_access_job(access_user, str((entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else "")), entry, job_index)
                ]
            jobs = [decorate_job_metadata(job, job_index) for job in queue_job_items(queue)]
            local_jobs = local_job_snapshot()
            if scope == "own":
                local_jobs = [job for job in local_jobs if user_can_access_job(access_user, str(job.get("promptId") or ""), job, job_index)]
            local_jobs = [decorate_job_metadata(job, job_index) for job in local_jobs]
            local_prompt_ids = {
                str(job.get("promptId") or "") for job in local_jobs
                if str(job.get("promptId") or "")
            }
            # The H3 wrapper and ComfyUI deliberately share one UUID. Prefer
            # the wrapper's lifecycle state instead of showing/counting it twice.
            jobs = [
                job for job in jobs
                if str(job.get("promptId") or "") not in local_prompt_ids
            ]
            upstream_running = sum(1 for job in jobs if job.get("state") == "running")
            upstream_pending = sum(1 for job in jobs if job.get("state") == "pending")
            orchestrator_jobs = orchestrator_job_snapshot()
            if scope == "own":
                orchestrator_jobs = [
                    job for job in orchestrator_jobs
                    if user_can_access_job(access_user, str(job.get("promptId") or ""), job, job_index)
                ]
            orchestrator_jobs = [decorate_job_metadata(job, job_index) for job in orchestrator_jobs]
            jobs.extend(local_jobs)
            jobs.extend(orchestrator_jobs)
            local_running, local_pending, _local_total = local_active_counts(local_jobs)
            orch_running, orch_pending, _orch_total = local_active_counts(orchestrator_jobs)
            queue["jobs"] = jobs
            queue["summary"] = {
                "running": upstream_running + local_running + orch_running,
                "pending": upstream_pending + local_pending + orch_pending,
                "total": len(jobs),
                "external": sum(1 for job in jobs if job.get("source") == "external"),
                "orchestrator": len(orchestrator_jobs),
            }
            queue["scope"] = scope
            try:
                history = request_json("/history?max_items=30")
                errors = local_job_error_items(local_jobs) + history_error_items(history, limit=30)
                if scope == "own":
                    errors = [
                        item for item in errors
                        if user_can_access_job(access_user, str(item.get("promptId") or ""), history.get(str(item.get("promptId") or "")) or item, job_index)
                    ]
                queue["recent_errors"] = [decorate_job_metadata(item, job_index) for item in errors[:8]]
            except Exception:
                queue["recent_errors"] = [decorate_job_metadata(item, job_index) for item in local_job_error_items(local_jobs)]
            self.send_json({
                "ok": True,
                "scope": scope,
                "jobs": jobs,
                "summary": queue["summary"],
                "recent_errors": queue.get("recent_errors") or [],
            })
        except PermissionError as error:
            self.send_error_json(error, 403)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 502)

    def api_orchestrator_status(self) -> None:
        try:
            self.send_json(castle_orchestrator.status_api())
        except Exception as error:
            self.send_error_json(error, 500)

    def _presenter_voice_batch_meta(self, batch_id: str) -> tuple[Path, dict]:
        if not VOICEOVER_BATCH_ID_RE.fullmatch(str(batch_id or "")):
            raise ValueError("Invalid job id")
        batch_dir = voiceover_batch_dir(batch_id)
        if not batch_dir.is_dir():
            raise KeyError("Job not found")
        batch = voiceover_read_json(batch_dir / "batch.json", {})
        if not isinstance(batch, dict) or batch.get("integration") != "presenter-voice":
            raise KeyError("Job not found")
        return batch_dir, batch

    def _presenter_voice_existing_batch(self, request_id: str) -> str:
        try:
            candidates = sorted(
                (path for path in VOICEOVER_ROOT.iterdir() if path.is_dir() and VOICEOVER_BATCH_ID_RE.fullmatch(path.name)),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return ""
        for path in candidates[:200]:
            batch = voiceover_read_json(path / "batch.json", {})
            if (
                isinstance(batch, dict)
                and batch.get("integration") == "presenter-voice"
                and hmac.compare_digest(str(batch.get("presenter_request_id") or ""), request_id)
            ):
                return path.name
        return ""

    def api_presenter_voice_create(self) -> None:
        try:
            if not self._voiceover_environment_ready():
                raise ValueError("Custom English voice environment is unavailable")
            body = self.read_json(MAX_TEXT_BODY)
            request_id = str(body.get("requestId") or "").strip().lower()
            if not PRESENTER_VOICE_REQUEST_ID_RE.fullmatch(request_id):
                raise ValueError("Invalid request id")
            segments = validate_voiceover_segments([{"index": 1, "text": body.get("text")}])

            with _VOICEOVER_LOCK:
                VOICEOVER_ROOT.mkdir(parents=True, exist_ok=True)
                existing = self._presenter_voice_existing_batch(request_id)
                if existing:
                    batch = voiceover_read_json(voiceover_batch_dir(existing) / "batch.json", {})
                    self.send_json({
                        "ok": True,
                        "batchId": existing,
                        "runId": str((batch or {}).get("run_id") or ""),
                        "reused": True,
                    }, 200)
                    return

                batch_id = voiceover_batch_id()
                batch_dir = voiceover_batch_dir(batch_id)
                batch_dir.mkdir(parents=True, exist_ok=False)
                voiceover_atomic_write_json(batch_dir / "segments.json", segments)
                result = castle_orchestrator.create_run_api(
                    self._voiceover_payload(batch_id, segments, batch_dir, candidates=3)
                )
                run_id = str((result.get("run") or {}).get("id") or "")
                voiceover_atomic_write_json(batch_dir / "batch.json", {
                    "run_id": run_id,
                    "created": int(time.time()),
                    "count": 1,
                    "redo_runs": {},
                    "integration": "presenter-voice",
                    "presenter_request_id": request_id,
                })
            self.send_json({"ok": True, "batchId": batch_id, "runId": run_id, "reused": False}, 202)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_presenter_voice_status(self, path: str) -> None:
        try:
            batch_id = path.removeprefix("/api/integrations/presenter-voice/jobs/").strip("/")
            self._presenter_voice_batch_meta(batch_id)
            response = self._voiceover_response(batch_id)
            segment = response.get("segments", [{}])[0] if response.get("segments") else {}
            self.send_json({
                "ok": True,
                "batchId": batch_id,
                "state": str(response.get("status") or "queued"),
                "runState": str(response.get("run_status") or ""),
                "audioReady": bool(segment.get("file")),
                "duration_s": segment.get("duration_s"),
                "similarity": segment.get("similarity"),
                "passed": segment.get("passed"),
            })
        except KeyError as error:
            self.send_json({"ok": False, "error": str(error)}, 404)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_presenter_voice_audio(self, path: str) -> None:
        try:
            batch_id = path.removeprefix("/api/integrations/presenter-voice/jobs/")
            batch_id = batch_id.removesuffix("/audio").strip("/")
            batch_dir, _batch = self._presenter_voice_batch_meta(batch_id)
            manifest = voiceover_read_json(batch_dir / "manifest.json", {})
            records = manifest.get("segments") if isinstance(manifest, dict) else []
            record = records[0] if isinstance(records, list) and records else {}
            filename = Path(str(record.get("file") or "")).name
            if not VOICEOVER_SEGMENT_FILE_RE.fullmatch(filename):
                raise KeyError("Audio not found")
            audio_path = batch_dir / filename
            if not audio_path.is_file():
                raise KeyError("Audio not found")
            self.send_local_file(
                audio_path,
                content_type="audio/wav",
                inline_filename=filename,
                cache_control="private, no-store",
            )
        except KeyError as error:
            self.send_json({"ok": False, "error": str(error)}, 404)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def _voiceover_payload(self, batch_id: str, segments: list[dict], output_dir: Path, *, candidates: int = 0, redo: bool = False) -> dict:
        return {
            "workflow_type": "castle.voxcpm_tts_batch",
            "title": f"簡報配音 {len(segments)} 段",
            "idempotency_key": f"voiceover-{batch_id}-{int(time.time())}-{secrets.token_hex(4)}",
            "input": {
                "mode": "lora",
                "segments": segments,
                "output_dir": str(output_dir),
                "candidates": candidates,
                "redo": redo,
                "workflow": "castle-voxcpm-tts-batch-web",
                "feature": "簡報配音",
                "voxcpm_python": str(VOXCPM_PYTHON),
                "worker_script": str(VOXCPM_SCRIPT),
                "model_path": str(VOXCPM_MODEL_PATH),
                "language": "English",
                "accent": "standard American English accent",
                "profile": "custom_english",
                "control": "",
                "prompt_text": CUSTOM_ENGLISH_REFERENCE_TEXT,
                "reference_audio": str(CUSTOM_ENGLISH_REFERENCE_AUDIO),
                "lora_path": str(CUSTOM_ENGLISH_LORA),
                "cfg": 2.0,
                "steps": 10,
                "seed": "",
                "speaker_gate": True,
                "speaker_threshold": VOXCPM_SPEAKER_THRESHOLD,
                "speaker_max_attempts": VOXCPM_SPEAKER_ATTEMPTS,
                "speaker_python": str(VOICE_LAB_PYTHON),
                "speaker_script": str(VOXCPM_SPEAKER_SCRIPT),
                "gpu_queue_python": GPU_QUEUE_PYTHON,
                "gpu_queue_script": str(GPU_QUEUE_SCRIPT),
                "gpu_min_free_mb": GUARDED_MIN_FREE_MB,
                "gpu_max_util": GUARDED_MAX_UTIL,
                "gpu_queue_interval": GUARDED_QUEUE_INTERVAL,
            },
        }

    def _voiceover_environment_ready(self) -> bool:
        return voiceover_environment_readiness()[0]

    def _voiceover_batch_parts(self, path: str, suffix: str | None = None) -> tuple[str, int | None]:
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "voiceover" or parts[2] != "batches":
            raise ValueError("找不到批次")
        batch_id = validate_voiceover_batch_id(parts[3] if len(parts) > 3 else "")
        if suffix is None:
            return batch_id, None
        if len(parts) != 7 or parts[4] != "segments" or parts[6] != suffix:
            raise ValueError("找不到段落")
        index = int(parts[5])
        if index <= 0:
            raise ValueError("段落編號錯誤")
        return batch_id, index

    def _voiceover_response(self, batch_id: str) -> dict:
        batch_dir = voiceover_batch_dir(batch_id)
        if not batch_dir.is_dir():
            raise KeyError("找不到批次")
        with _VOICEOVER_LOCK:
            batch = voiceover_read_json(batch_dir / "batch.json", {})
            segments = voiceover_read_json(batch_dir / "segments.json", [])
            manifest = voiceover_read_json(batch_dir / "manifest.json", {})
            batch, manifest = voiceover_merge_redo_if_ready(batch_dir, batch if isinstance(batch, dict) else {}, manifest if isinstance(manifest, dict) else {})
        run_id = str((batch or {}).get("run_id") or "")
        run_status = voiceover_run_status(run_id)
        manifest_status = str((manifest or {}).get("status") or "")
        status = manifest_status or ("running" if run_status in {"running", "pending", "cancel_requested"} else "queued")
        if run_status in {"failed", "cancelled"} and status not in {"done", "cancelled"}:
            status = run_status
        elif run_status in {"completed", "completed_approved"} and status == "running":
            status = "done"
        records = {
            int(item.get("index") or 0): item
            for item in ((manifest or {}).get("segments") if isinstance((manifest or {}).get("segments"), list) else [])
            if isinstance(item, dict)
        }
        redo_runs = (batch or {}).get("redo_runs") if isinstance((batch or {}).get("redo_runs"), dict) else {}
        redo_status = {str(k): voiceover_run_status(str(v or "")) for k, v in redo_runs.items()}
        out_segments = []
        for item in segments if isinstance(segments, list) else []:
            index = int(item.get("index") or 0)
            record = records.get(index, {})
            filename = Path(str(record.get("file") or f"seg_{index:02d}.wav")).name
            wav = batch_dir / filename
            audio_url = ""
            audio_export_urls_for_segment = {}
            if filename and wav.is_file():
                audio_params = {
                    "filename": filename,
                    "subfolder": f"Castle/voiceover/{batch_id}",
                    "type": "output",
                }
                audio_url = "/api/view?" + urllib.parse.urlencode(audio_params)
                audio_export_urls_for_segment = audio_export_urls(audio_params, "audio")
            seg_status = "完成" if audio_url else ("未完成" if status in {"done", "failed", "cancelled"} else "等待中")
            out_segments.append({
                "index": index,
                "text": str(item.get("text") or ""),
                "file": filename if audio_url else "",
                "audioUrl": audio_url,
                "audioExportUrls": audio_export_urls_for_segment,
                "status": seg_status,
                "similarity": record.get("similarity"),
                "passed": record.get("passed"),
                "duration_s": record.get("duration_s"),
                "hasOld": bool(record.get("old")) and (batch_dir / f"seg_{index:02d}_old.wav").is_file(),
                "redoStatus": redo_status.get(str(index), ""),
            })
        return {
            "ok": True,
            "batch_id": batch_id,
            "created": (batch or {}).get("created", ""),
            "count": int((batch or {}).get("count") or len(out_segments)),
            "run_id": run_id,
            "run_status": run_status,
            "status": status,
            "segments": out_segments,
            "manifest": {
                "status": manifest_status,
                "count": len(records),
                "total_audio_s": (manifest or {}).get("total_audio_s", 0),
            },
        }

    def api_voiceover_extract_pptx(self) -> None:
        try:
            body = self.read_json(MAX_UPLOAD_BODY)
            file_info = body.get("file") if isinstance(body.get("file"), dict) else {}
            raw, _content_type = decode_data_url(str(file_info.get("dataUrl") or ""), VOICEOVER_MAX_PPTX_BYTES)
            self.send_json(extract_pptx_notes_segments(raw))
        except RequestTooLarge:
            self.send_json({"ok": False, "error": "PPTX 檔案太大"}, 400)
        except (ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_voiceover_create_batch(self) -> None:
        try:
            if not self._voiceover_environment_ready():
                raise ValueError("VoxCPM 批次環境尚未就緒")
            body = self.read_json(MAX_TEXT_BODY * 2)
            segments = validate_voiceover_segments(body.get("segments"))
            VOICEOVER_ROOT.mkdir(parents=True, exist_ok=True)
            batch_id = voiceover_batch_id()
            batch_dir = voiceover_batch_dir(batch_id)
            batch_dir.mkdir(parents=True, exist_ok=False)
            voiceover_atomic_write_json(batch_dir / "segments.json", segments)
            result = castle_orchestrator.create_run_api(self._voiceover_payload(batch_id, segments, batch_dir))
            run_id = str((result.get("run") or {}).get("id") or "")
            voiceover_atomic_write_json(batch_dir / "batch.json", {
                "run_id": run_id,
                "created": int(time.time()),
                "count": len(segments),
                "redo_runs": {},
            })
            self.send_json({"ok": True, "batch_id": batch_id, "run_id": run_id})
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_voiceover_batches(self) -> None:
        try:
            VOICEOVER_ROOT.mkdir(parents=True, exist_ok=True)
            items = []
            for path in sorted((p for p in VOICEOVER_ROOT.iterdir() if p.is_dir() and VOICEOVER_BATCH_ID_RE.fullmatch(p.name)), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                batch = voiceover_read_json(path / "batch.json", {})
                manifest = voiceover_read_json(path / "manifest.json", {})
                items.append({
                    "batch_id": path.name,
                    "created": (batch or {}).get("created", ""),
                    "count": int((batch or {}).get("count") or 0),
                    "status": str((manifest or {}).get("status") or "queued"),
                })
            self.send_json({"ok": True, "batches": items})
        except Exception as error:
            self.send_error_json(error, 500)

    def api_voiceover_batch(self, path: str) -> None:
        try:
            batch_id, _index = self._voiceover_batch_parts(path)
            self.send_json(self._voiceover_response(batch_id))
        except KeyError as error:
            self.send_json({"ok": False, "error": str(error)}, 404)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_voiceover_redo(self, path: str) -> None:
        try:
            batch_id, index = self._voiceover_batch_parts(path, "redo")
            batch_dir = voiceover_batch_dir(batch_id)
            if not batch_dir.is_dir():
                raise KeyError("找不到批次")
            with _VOICEOVER_LOCK:
                batch = voiceover_read_json(batch_dir / "batch.json", {})
                manifest = voiceover_read_json(batch_dir / "manifest.json", {})
                status = str((manifest or {}).get("status") or "")
                if status not in {"done", "cancelled"}:
                    self.send_json({"ok": False, "error": "批次還在進行中"}, 409)
                    return
                redo_runs = batch.get("redo_runs") if isinstance(batch.get("redo_runs"), dict) else {}
                for run_id in redo_runs.values():
                    if not voiceover_terminal_status(voiceover_run_status(str(run_id or ""))):
                        self.send_json({"ok": False, "error": "已有重生正在進行中"}, 409)
                        return
                wav = batch_dir / f"seg_{index:02d}.wav"
                if not wav.is_file():
                    raise ValueError("找不到該段音檔")
                segments = voiceover_read_json(batch_dir / "segments.json", [])
                segment = next((item for item in segments if int(item.get("index") or 0) == index), None)
                if not isinstance(segment, dict):
                    raise ValueError("找不到該段文字")
                result = castle_orchestrator.create_run_api(self._voiceover_payload(batch_id, [segment], batch_dir, candidates=3, redo=True))
                run_id = str((result.get("run") or {}).get("id") or "")
                batch["redo_runs"] = {**redo_runs, str(index): run_id}
                voiceover_atomic_write_json(batch_dir / "batch.json", batch)
            self.send_json({"ok": True, "run_id": run_id})
        except KeyError as error:
            self.send_json({"ok": False, "error": str(error)}, 404)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_voiceover_restore(self, path: str) -> None:
        try:
            batch_id, index = self._voiceover_batch_parts(path, "restore")
            batch_dir = voiceover_batch_dir(batch_id)
            if not batch_dir.is_dir():
                raise KeyError("找不到批次")
            with _VOICEOVER_LOCK:
                batch = voiceover_read_json(batch_dir / "batch.json", {})
                manifest = voiceover_read_json(batch_dir / "manifest.json", {})
                if str((manifest or {}).get("status") or "") == "running":
                    self.send_json({"ok": False, "error": "批次還在進行中"}, 409)
                    return
                redo_runs = batch.get("redo_runs") if isinstance(batch.get("redo_runs"), dict) else {}
                for run_id in redo_runs.values():
                    if not voiceover_terminal_status(voiceover_run_status(str(run_id or ""))):
                        self.send_json({"ok": False, "error": "已有重生正在進行中"}, 409)
                        return
                segment = voiceover_restore_segment(batch_dir, int(index))
            self.send_json({"ok": True, "segment": segment})
        except KeyError as error:
            self.send_json({"ok": False, "error": str(error)}, 404)
        except (ValueError, OSError) as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_voiceover_zip(self, path: str) -> None:
        tmp_name = ""
        try:
            batch_id, _index = self._voiceover_batch_parts(path.removesuffix("/zip"))
            batch_dir = voiceover_batch_dir(batch_id)
            if not batch_dir.is_dir():
                self.send_json({"ok": False, "error": "找不到批次"}, 404)
                return
            manifest = voiceover_read_json(batch_dir / "manifest.json", {})
            if str((manifest or {}).get("status") or "") != "done":
                self.send_json({"ok": False, "error": "批次還在進行中"}, 409)
                return
            with tempfile.NamedTemporaryFile(prefix=f"voiceover_{batch_id}_", suffix=".zip", delete=False) as tmp:
                tmp_name = tmp.name
            with zipfile.ZipFile(tmp_name, "w", compression=zipfile.ZIP_STORED) as zf:
                for wav in sorted(batch_dir.glob("seg_*.wav")):
                    if wav.name.endswith("_old.wav"):
                        continue
                    zf.write(wav, wav.name)
                zf.write(batch_dir / "manifest.json", "manifest.json")
            tmp_path = Path(tmp_name)
            size = tmp_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''voiceover_{batch_id}.zip")
            self.end_headers()
            with tmp_path.open("rb") as f:
                while True:
                    chunk = f.read(STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            self.send_error_json(error, 500)
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink()
                except FileNotFoundError:
                    pass

    def api_orchestrator_resources(self) -> None:
        try:
            self.send_json(orchestrator_resources_snapshot())
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_runs(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            limit = int((params.get("limit") or ["30"])[0] or 30)
            status = str((params.get("status") or [""])[0] or "").strip() or None
            self.send_json(castle_orchestrator.list_runs_api(limit=limit, status=status))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_reel_reviews(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            limit = int((params.get("limit") or ["80"])[0] or 80)
            self.send_json(castle_orchestrator.list_reel_reviews_api(limit=limit))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_run(self, path: str, _query: str = "") -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 4:
                self.send_json({"ok": False, "error": "Run not found"}, 404)
                return
            run_id = parts[3]
            self.send_json(castle_orchestrator.get_run_api(run_id))
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_events(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            after = int((params.get("after") or ["0"])[0] or 0)
            limit = int((params.get("limit") or ["100"])[0] or 100)
            run_id = str((params.get("run_id") or params.get("runId") or [""])[0] or "").strip() or None
            self.send_json(castle_orchestrator.list_events_api(after=after, limit=limit, run_id=run_id))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_create_run(self) -> None:
        try:
            self.send_json(castle_orchestrator.create_run_api(self.read_json(MAX_TEXT_BODY)))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_cancel_run(self, path: str) -> None:
        try:
            self.read_json(MAX_TEXT_BODY)
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "cancel":
                self.send_json({"ok": False, "error": "Run not found"}, 404)
                return
            self.send_json(castle_orchestrator.cancel_run_api(parts[3]))
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_rerun_run(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "rerun":
                self.send_json({"ok": False, "error": "Run not found"}, 404)
                return
            self.send_json(castle_orchestrator.rerun_run_api(parts[3], self.read_json(MAX_TEXT_BODY)))
        except (KeyError, ValueError) as error:
            self.send_error_json(error, 400 if isinstance(error, ValueError) else 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_run_review(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "review":
                self.send_json({"ok": False, "error": "Run review action not found"}, 404)
                return
            self.send_json(castle_orchestrator.review_reel_run_api(parts[3], self.read_json(MAX_TEXT_BODY)))
        except KeyError:
            self.send_json({"ok": False, "error": "Run not found"}, 404)
        except FileNotFoundError as error:
            print(f"[orchestrator] review file not found: {error}", flush=True)
            self.send_json({"ok": False, "error": "Review file not found"}, 404)
        except OSError as error:
            print(f"[orchestrator] review file error: {error}", flush=True)
            self.send_json({"ok": False, "error": "Review file error"}, 500)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            print(f"[orchestrator] review error: {error}", flush=True)
            self.send_json({"ok": False, "error": "Internal error"}, 500)

    def api_orchestrator_run_review_page(self, path: str, query: str = "") -> None:
        try:
            if not query:
                self.send_json({
                    "ok": False,
                    "error": "Unbound Castle Reel review pages are retired; use AI-Sister Admin",
                }, 410)
                return
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "review-page":
                self.send_json({"ok": False, "error": "Run review page not found"}, 404)
                return
            result = castle_orchestrator.render_bound_reel_review_page_api(parts[3], query)
            self.send_html(result["html"])
        except KeyError:
            self.send_json({"ok": False, "error": "Run not found"}, 404)
        except FileNotFoundError as error:
            print(f"[orchestrator] review file not found: {error}", flush=True)
            self.send_json({"ok": False, "error": "Review file not found"}, 404)
        except OSError as error:
            print(f"[orchestrator] review file error: {error}", flush=True)
            self.send_json({"ok": False, "error": "Review file error"}, 500)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            print(f"[orchestrator] review page error: {error}", flush=True)
            self.send_json({"ok": False, "error": "Internal error"}, 500)

    def api_orchestrator_tasks(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            limit = int((params.get("limit") or ["50"])[0] or 50)
            status = str((params.get("status") or [""])[0] or "").strip() or None
            run_id = str((params.get("run_id") or params.get("runId") or [""])[0] or "").strip() or None
            lane = str((params.get("lane") or [""])[0] or "").strip() or None
            self.send_json(castle_orchestrator.list_tasks_api(limit=limit, status=status, run_id=run_id, lane=lane))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_artifacts(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            limit = int((params.get("limit") or ["50"])[0] or 50)
            artifact_type = str((params.get("artifact_type") or params.get("artifactType") or [""])[0] or "").strip() or None
            tag = str((params.get("tag") or [""])[0] or "").strip() or None
            tag_family = str((params.get("tag_family") or params.get("tagFamily") or [""])[0] or "").strip() or None
            tag_name = str((params.get("tag_name") or params.get("tagName") or [""])[0] or "").strip() or None
            q = str((params.get("q") or [""])[0] or "").strip() or None
            run_id = str((params.get("run_id") or params.get("runId") or [""])[0] or "").strip() or None
            status = str((params.get("status") or [""])[0] or "").strip() or None
            source_type = str((params.get("source_type") or params.get("sourceType") or [""])[0] or "").strip() or None
            gate_state = str((params.get("gate_state") or params.get("gateState") or [""])[0] or "").strip() or None
            self.send_json(castle_orchestrator.list_artifacts_api(
                limit=limit,
                artifact_type=artifact_type,
                tag=tag,
                tag_family=tag_family,
                tag_name=tag_name,
                q=q,
                run_id=run_id,
                status=status,
                source_type=source_type,
                gate_state=gate_state,
            ))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_tags(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            limit = int((params.get("limit") or ["200"])[0] or 200)
            self.send_json(castle_orchestrator.list_tags_api(limit=limit))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_adopt_artifact(self) -> None:
        try:
            body = normalize_orchestrator_adopt_body(self.read_json(MAX_TEXT_BODY))
            self.send_json(castle_orchestrator.adopt_artifact_api(body))
        except FileNotFoundError as error:
            self.send_error_json(error, 404)
        except PermissionError as error:
            self.send_error_json(error, 403)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_adopt_artifacts(self) -> None:
        try:
            body, validation_errors = normalize_orchestrator_adopt_batch_body(self.read_json(MAX_TEXT_BODY))
            if validation_errors and not body.get("items"):
                self.send_json({
                    "ok": True,
                    "artifacts": [],
                    "errors": validation_errors,
                    "count": 0,
                    "error_count": len(validation_errors),
                })
                return
            result = castle_orchestrator.adopt_artifacts_api(body)
            errors = validation_errors + list(result.get("errors") or [])
            result["errors"] = errors
            result["error_count"] = len(errors)
            self.send_json(result)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_update_artifact(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5:
                self.send_json({"ok": False, "error": "Artifact action not found"}, 404)
                return
            artifact_id = parts[3]
            action = parts[4]
            body = self.read_json(MAX_TEXT_BODY)
            if action == "tags":
                self.send_json(castle_orchestrator.update_artifact_tags_api(artifact_id, body))
            elif action == "metadata":
                self.send_json(castle_orchestrator.update_artifact_metadata_api(artifact_id, body))
            elif action == "gate":
                self.send_json(castle_orchestrator.update_artifact_gate_api(artifact_id, body))
            elif action == "approve":
                self.send_json(castle_orchestrator.approve_artifact_api(artifact_id, body))
            elif action == "reject":
                self.send_json(castle_orchestrator.reject_artifact_api(artifact_id, body))
            elif action == "review":
                self.send_json(castle_orchestrator.review_artifact_api(artifact_id, body))
            elif action == "make-proxy":
                self.send_json(castle_orchestrator.make_artifact_proxy_api(artifact_id, body))
            else:
                self.send_json({"ok": False, "error": "Artifact action not found"}, 404)
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_artifact(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 4:
                self.send_json({"ok": False, "error": "Artifact not found"}, 404)
                return
            artifact_id = parts[3]
            if len(parts) >= 5 and parts[4] == "file":
                target = castle_orchestrator.artifact_file_path_api(artifact_id)
                self.send_local_file(target, inline_filename=target.name)
                return
            self.send_json(castle_orchestrator.get_artifact_api(artifact_id))
        except (KeyError, FileNotFoundError) as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_task(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) == 4:
                self.send_json(castle_orchestrator.get_task_api(parts[3]))
                return
            if len(parts) < 5 or parts[4] != "log":
                self.send_json({"ok": False, "error": "Task log not found"}, 404)
                return
            target = castle_orchestrator.task_log_path_api(parts[3])
            self.send_local_file(target, content_type="text/plain; charset=utf-8", inline_filename=target.name)
        except (KeyError, FileNotFoundError) as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_retry_task(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "retry":
                self.send_json({"ok": False, "error": "Task not found"}, 404)
                return
            self.send_json(castle_orchestrator.retry_task_api(parts[3], self.read_json(MAX_TEXT_BODY)))
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_cancel_task(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "cancel":
                self.send_json({"ok": False, "error": "Task not found"}, 404)
                return
            self.read_json(MAX_TEXT_BODY)
            self.send_json(castle_orchestrator.cancel_task_api(parts[3]))
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_mark_task_skipped(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "mark-skipped":
                self.send_json({"ok": False, "error": "Task not found"}, 404)
                return
            self.send_json(castle_orchestrator.mark_task_skipped_api(parts[3], self.read_json(MAX_TEXT_BODY)))
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_reserve_resource(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "reserve":
                self.send_json({"ok": False, "error": "Resource action not found"}, 404)
                return
            self.send_json(castle_orchestrator.reserve_resource_api(parts[3], self.read_json(MAX_TEXT_BODY)))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_release_resource(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 5 or parts[4] != "release":
                self.send_json({"ok": False, "error": "Resource action not found"}, 404)
                return
            self.send_json(castle_orchestrator.release_resource_api(parts[3], self.read_json(MAX_TEXT_BODY)))
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_pause_scheduler(self) -> None:
        try:
            self.send_json(castle_orchestrator.pause_scheduler_api(self.read_json(MAX_TEXT_BODY)))
        except Exception as error:
            self.send_error_json(error, 500)

    def api_orchestrator_resume_scheduler(self) -> None:
        try:
            self.read_json(MAX_TEXT_BODY)
            self.send_json(castle_orchestrator.resume_scheduler_api())
        except Exception as error:
            self.send_error_json(error, 500)

    def api_events(self) -> None:
        client_id = f"{CLIENT_ID}-monitor-{secrets.token_hex(6)}"
        user = self.current_user()
        is_admin = bool(user and user.get("role") == "admin")

        def event_prompt_id(message: dict) -> str:
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            for key in ("prompt_id", "promptId"):
                value = data.get(key)
                if value:
                    return str(value)
            return ""

        def event_visible(raw: dict | str) -> bool:
            if is_admin:
                return True
            try:
                message = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                return True
            if not isinstance(message, dict):
                return True
            prompt_id = event_prompt_id(message)
            if not prompt_id:
                return True
            return user_can_access_job(user, prompt_id, message, load_job_index())

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(payload: dict | str) -> None:
            raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            self.wfile.write(f"data: {raw}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            emit({"type": "castle_connected", "data": {"client_id": client_id}})
            for message in comfy_ws_messages(client_id):
                if event_visible(message):
                    emit(message)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            try:
                emit({"type": "castle_error", "data": {"error": str(error)}})
            except Exception:
                pass

    def api_job_cancel(self) -> None:
        try:
            body = self.read_json()
            prompt_id = str(body.get("promptId") or body.get("prompt_id") or "").strip()
            requested_state = str(body.get("state") or "").strip()
            if not prompt_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", prompt_id):
                self.send_json({"ok": False, "error": "Invalid prompt id"}, 400)
                return
            user = self.current_user()
            if not user_can_access_job(user, prompt_id):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            with _LOCAL_JOB_LOCK:
                local = LOCAL_JOBS.get(prompt_id)
                if local and local.get("state") in ("pending", "running", "cancel_requested"):
                    cancel_file = local.get("cancelFile")
                    safe_worker_job = local.get("jobKind") in {
                        MINIMAX_H3_WEB_JOB_KIND,
                        CLIPMODE_PHOTOSHOOT_JOB_KIND,
                    }
                    if safe_worker_job:
                        if not cancel_file:
                            raise ValueError("安全取消訊號不存在")
                        if local.get("jobKind") == CLIPMODE_PHOTOSHOOT_JOB_KIND:
                            _signal_clipmode_photoshoot_cancel(str(cancel_file))
                            cancel_label = "角色身份寫真正在安全取消"
                        else:
                            _signal_minimax_h3_web_cancel(str(cancel_file))
                            cancel_label = "MiniMax H3 正在安全取消"
                        local["state"] = "cancel_requested"
                        local["progress"] = {"pct": 95, "label": cancel_label}
                        local["updated"] = time.time()
                        pid = 0
                    else:
                        local["state"] = "cancelled"
                        local["progress"] = {"pct": 100, "label": "正在取消"}
                        local["updated"] = time.time()
                        pid = int(local.get("pid") or 0)
                else:
                    local = None
                    cancel_file = None
                    pid = 0
            if local:
                safe_worker_job = local.get("jobKind") in {
                    MINIMAX_H3_WEB_JOB_KIND,
                    CLIPMODE_PHOTOSHOOT_JOB_KIND,
                }
                if cancel_file and not safe_worker_job:
                    try:
                        Path(cancel_file).touch()
                    except OSError:
                        pass
                if pid:
                    try:
                        os.killpg(pid, signal.SIGTERM)
                    except OSError:
                        pass
                action = "cancel_requested" if safe_worker_job else "cancelled"
                self.send_json({"ok": True, "action": action, "promptId": prompt_id})
                return
            queue = request_json("/queue")
            jobs = queue_job_items(queue)
            job = next((item for item in jobs if item.get("promptId") == prompt_id), None)
            state = (job or {}).get("state") or requested_state
            if state == "pending":
                request_json("/queue", {"delete": [prompt_id]}, method="POST")
                self.send_json({"ok": True, "action": "deleted", "promptId": prompt_id})
                return
            if state == "running":
                request_json("/interrupt", {"prompt_id": prompt_id}, method="POST")
                self.send_json({"ok": True, "action": "interrupted", "promptId": prompt_id})
                return
            self.send_json({"ok": True, "action": "not_found", "promptId": prompt_id})
        except Exception as error:
            self.send_error_json(error, 502)

    def api_status(self) -> None:
        try:
            system = request_json("/system_stats")
            required = {
                "z_image_turbo_bf16.safetensors": "diffusion model",
                "qwen_3_4b.safetensors": "text encoder",
                "ae.safetensors": "VAE",
                "dopsd_white_zimage_turbo_comfy.safetensors": "DOPSD LoRA",
            }
            available = set(model_options("UNETLoader", "unet_name"))
            available.update(model_options("CLIPLoader", "clip_name"))
            available.update(model_options("VAELoader", "vae_name"))
            available.update(model_options("LoraLoaderModelOnly", "lora_name"))
            models = [
                {"name": name, "role": role, "installed": name in available}
                for name, role in required.items()
            ]
            self.send_json({"ok": True, "comfy": True, "system": system, "models": models})
        except Exception as error:
            self.send_error_json(error, 502)

    def api_features(self) -> None:
        try:
            self.send_json({"ok": True, "studios": STUDIOS, "features": feature_status()})
        except Exception as error:
            self.send_error_json(error, 502)

    def api_characters(self) -> None:
        try:
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            runtime = breezy_persona_runtime()
            with _CHARACTER_LOCK:
                characters = [
                    character_public(
                        item,
                        runtime,
                        user_can_generate_character(user, item, service_token),
                    )
                    for item in load_character_index()
                    if user_can_see_character(user, item, service_token)
                ]
            self.send_json({"ok": True, "characters": characters, "actions": CHARACTER_ACTION_CATALOG})
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_capabilities(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(part) for part in path.split("/") if part]
            if len(parts) != 4 or parts[3] != "capabilities":
                self.send_json({"ok": False, "error": "Capabilities not found"}, 404)
                return
            character = get_character_entry(parts[2])
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            if not user_can_see_character(user, character, service_token):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            runtime = breezy_persona_runtime()
            self.send_json({
                "ok": True,
                "characterId": character["id"],
                "capabilities": apply_character_capability_authorization(
                    character_capabilities(character, runtime),
                    user_can_generate_character(user, character, service_token),
                ),
                "runtime": runtime,
            })
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 3:
                self.send_json({"ok": False, "error": "Character not found"}, 404)
                return
            character = get_character_entry(parts[2])
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            if not user_can_see_character(user, character, service_token):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            self.send_json({
                "ok": True,
                "character": character_public(
                    character,
                    breezy_persona_runtime(),
                    user_can_generate_character(user, character, service_token),
                ),
                "actions": CHARACTER_ACTION_CATALOG,
            })
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_posts(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 4 or parts[3] != "posts":
                self.send_json({"ok": False, "error": "Posts not found"}, 404)
                return
            character = get_character_entry(parts[2])
            if not user_can_see_character(self.current_user(), character, self.is_orchestrator_service_authenticated()):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            cid = character["id"]
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            with _CHARACTER_POST_LOCK:
                posts = character_posts_for_user(load_character_posts(), cid, user, service_token)
            self.send_json({"ok": True, "posts": posts}, cache_control="private, no-store")
        except KeyError:
            self.send_json({"ok": False, "error": "Character not found"}, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_posts(self) -> None:
        try:
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            with _CHARACTER_LOCK:
                visible_ids = {
                    item["id"]
                    for item in load_character_index()
                    if user_can_see_character(user, item, service_token)
                }
            with _CHARACTER_POST_LOCK:
                posts = visible_character_posts_for_user(
                    load_character_posts(),
                    visible_ids,
                    user,
                    service_token,
                )
            self.send_json({"ok": True, "posts": posts}, cache_control="private, no-store")
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_post_delete(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 6 or parts[3] != "posts" or parts[5] != "delete":
                self.send_json({"ok": False, "error": "Post not found"}, 404)
                return
            character = get_character_entry(parts[2])
            user = self.current_user()
            if not user_can_see_character(user, character, False):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            post_id = str(parts[4] or "")
            if not re.fullmatch(r"[a-f0-9]{16}", post_id):
                self.send_json({"ok": False, "error": "Post not found"}, 404)
                return
            with _CHARACTER_POST_LOCK:
                posts = load_character_posts()
                target = next((item for item in posts if item.get("characterId") == character["id"] and item.get("id") == post_id), None)
                if target is None:
                    self.send_json({"ok": False, "error": "Post not found"}, 404)
                    return
                if not user_can_delete_character_post(user, target):
                    self.send_json({"ok": False, "error": "沒有權限"}, 403)
                    return
                remaining = [item for item in posts if item is not target]
                save_character_posts(remaining)
            self.send_json({"ok": True})
        except KeyError:
            self.send_json({"ok": False, "error": "Character not found"}, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_wardrobe(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 4 or parts[3] != "wardrobe":
                self.send_json({"ok": False, "error": "Wardrobe not found"}, 404)
                return
            character = get_character_entry(parts[2])
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            if not user_can_see_character(user, character, service_token):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            outfits = wardrobe_outfits_for_user(character, user, service_token)
            self.send_json(
                {"ok": True, "outfits": outfits},
                cache_control="private, no-cache",
            )
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_asset(self, path: str, query: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 4 or parts[3] != "asset":
                self.send_json({"ok": False, "error": "Character asset not found"}, 404)
                return
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            kind = str((params.get("kind") or ["avatar"])[0] or "avatar")
            character = get_character_entry(parts[2])
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            if not user_can_see_character(user, character, service_token):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            target = None
            if kind == "outfit":
                theme = str((params.get("theme") or [""])[0] or "")
                if theme not in WARDROBE_THEMES:
                    self.send_json({"ok": False, "error": "Unknown wardrobe theme"}, 400)
                    return
                if theme not in approved_wardrobe_themes(character["id"]) and not user_can_edit_character(user, character, service_token):
                    self.send_json({"ok": False, "error": "Character asset not found"}, 404)
                    return
                target = wardrobe_asset_path(character["id"], "outfit", theme)
            elif kind == "scene":
                theme = str((params.get("theme") or [""])[0] or "")
                if theme not in approved_anime_scene_themes():
                    self.send_json({"ok": False, "error": "Unknown anime scene"}, 400)
                    return
                target = anime_scene_asset_path(theme)
            elif kind == "pose":
                theme = str((params.get("theme") or [""])[0] or "")
                pose = str((params.get("pose") or [""])[0] or "")
                if theme not in WARDROBE_THEMES or pose not in WARDROBE_POSES:
                    self.send_json({"ok": False, "error": "Unknown wardrobe pose"}, 400)
                    return
                if theme not in approved_wardrobe_themes(character["id"]) and not user_can_edit_character(user, character, service_token):
                    self.send_json({"ok": False, "error": "Character asset not found"}, 404)
                    return
                target = wardrobe_asset_path(character["id"], "pose", theme, pose)
            elif kind == "wardrobe":
                if not user_can_edit_character(user, character, service_token):
                    self.send_json({"ok": False, "error": "沒有權限"}, 403)
                    return
                filename = str((params.get("file") or [""])[0] or "")
                target = uploaded_wardrobe_asset_path(character["id"], filename)
            elif kind == "user-photo":
                filename = str((params.get("file") or [""])[0] or "")
                target = experimental_user_asset_path(character, filename)
            elif kind not in CHARACTER_ASSET_KINDS:
                self.send_json({"ok": False, "error": "Unknown asset kind"}, 400)
                return
            else:
                target = character_asset_path(character, kind)
            if target is None:
                self.send_json({"ok": False, "error": "Character asset not found"}, 404)
                return
            if (params.get("thumb") or [""])[0] in ("1", "true", "yes") and kind in {"outfit", "pose", "wardrobe", "scene"}:
                target = wardrobe_thumbnail_path(target) or target
            self.send_local_file(
                target,
                inline_filename=target.name,
                cache_control=f"private, max-age={WARDROBE_CACHE_SECONDS}",
            )
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_wardrobe_upload(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 4 or parts[3] != "wardrobe":
                self.send_json({"ok": False, "error": "Wardrobe not found"}, 404)
                return
            character = get_character_entry(parts[2])
            if not user_can_edit_character(self.current_user(), character, self.is_orchestrator_service_authenticated()):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            body = self.read_json(WARDROBE_REFERENCE_MAX_FILE_BYTES * 2)
            file_info = body.get("file") if isinstance(body.get("file"), dict) else body
            raw, content_type = decode_data_url(
                str(file_info.get("dataUrl") or ""),
                WARDROBE_REFERENCE_MAX_FILE_BYTES,
            )
            if not content_type.startswith("image/"):
                raise ValueError("請上傳圖片檔")
            _sniffed_type, suffix = sniff_experimental_image(raw)
            original_stem = Path(str(file_info.get("name") or "wardrobe")).stem
            safe_stem = Path(safe_filename(original_stem or "wardrobe")).stem or "wardrobe"
            base_name = f"{safe_stem}{suffix}"
            target_name = f"{int(time.time())}_{secrets.token_hex(2)}_{base_name}"
            with _WARDROBE_LOCK:
                item_count, total_bytes = uploaded_wardrobe_storage(character["id"])
                if item_count >= WARDROBE_REFERENCE_MAX_ITEMS:
                    raise ValueError(f"參考收藏最多 {WARDROBE_REFERENCE_MAX_ITEMS} 張")
                if total_bytes + len(raw) > WARDROBE_REFERENCE_MAX_TOTAL_BYTES:
                    raise ValueError("參考收藏空間已滿，請先刪除不需要的圖片")
                target_dir = character_wardrobe_upload_dir(character["id"])
                target_dir.mkdir(parents=True, exist_ok=True)
                target = (target_dir / target_name).resolve()
                if not target.is_relative_to(target_dir.resolve()):
                    raise ValueError("Invalid wardrobe filename")
                temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(2)}")
                try:
                    temporary.write_bytes(raw)
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, target)
                finally:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
                outfits = uploaded_wardrobe_items(character["id"])
                item = next((entry for entry in outfits if entry.get("filename") == target_name), None)
                all_outfits = wardrobe_outfits_for_character(character["id"])
            self.send_json(
                {"ok": True, "item": item, "outfits": all_outfits},
                cache_control="private, no-store",
            )
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_wardrobe_delete(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 6 or parts[3] != "wardrobe" or parts[5] != "delete":
                self.send_json({"ok": False, "error": "Wardrobe reference not found"}, 404)
                return
            character = get_character_entry(parts[2])
            if not user_can_edit_character(self.current_user(), character, False):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            with _WARDROBE_LOCK:
                target = uploaded_wardrobe_asset_path(character["id"], parts[4])
                if target is None:
                    self.send_json({"ok": False, "error": "Wardrobe reference not found"}, 404)
                    return
                thumbnail = wardrobe_thumbnail_cache_path(target)
                target.unlink()
                if thumbnail is not None:
                    try:
                        thumbnail.unlink()
                    except FileNotFoundError:
                        pass
                outfits = wardrobe_outfits_for_character(character["id"])
            self.send_json({"ok": True, "outfits": outfits}, cache_control="private, no-store")
        except KeyError as error:
            self.send_error_json(error, 404)
        except OSError as error:
            self.send_error_json(error, 500)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_create(self) -> None:
        try:
            service_token = self.is_orchestrator_service_authenticated()
            user = self.current_user()
            if not service_token and not (user and user.get("role") == "admin"):
                self.send_json({"ok": False, "error": "需要管理員權限"}, 403)
                return
            body = self.read_json(MAX_TEXT_BODY)
            saved = upsert_character(
                body,
                allow_visual_class_update=bool(
                    service_token or (user and user.get("role") == "admin")
                ),
            )
            character = character_public_for_user(get_character_entry(saved["id"]), user, service_token)
            self.send_json({"ok": True, "character": character})
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_experimental_create(self) -> None:
        try:
            user = self.current_user()
            email = normalize_email(str((user or {}).get("email") or ""))
            if not email:
                self.send_json({"ok": False, "error": "Authentication required"}, 401)
                return
            body = self.read_json(EXPERIMENTAL_MAX_FILE_BYTES * 6 * 2)
            name = str(body.get("name") or "").strip()[:120]
            if not name:
                raise ValueError("請輸入角色名稱")
            photos = body.get("photos") if isinstance(body.get("photos"), list) else []
            photos = photos[:6]
            if not photos:
                raise ValueError("請至少上傳一張照片")
            with _CHARACTER_LOCK:
                current_entries = load_character_index()
                owned_count = sum(1 for item in current_entries if is_experimental_character(item) and normalize_email(str(item.get("ownerEmail") or "")) == email)
            if owned_count >= EXPERIMENTAL_MAX_PER_USER:
                self.send_json({"ok": False, "error": "超過實驗角色數量上限"}, 403)
                return
            decoded = []
            incoming_total = 0
            for item in photos:
                if not isinstance(item, dict):
                    continue
                try:
                    raw, _content_type = decode_data_url(str(item.get("data") or item.get("dataUrl") or ""), EXPERIMENTAL_MAX_FILE_BYTES)
                except RequestTooLarge:
                    self.send_json({"ok": False, "error": "檔案太大"}, 400)
                    return
                if len(raw) > EXPERIMENTAL_MAX_FILE_BYTES:
                    self.send_json({"ok": False, "error": "檔案太大"}, 400)
                    return
                try:
                    content_type, suffix = sniff_experimental_image(raw)
                except ValueError:
                    self.send_json({"ok": False, "error": "不支援的圖片格式"}, 400)
                    return
                incoming_total += len(raw)
                decoded.append({"raw": raw, "contentType": content_type, "suffix": suffix, "name": str(item.get("name") or "photo")})
            if not decoded:
                raise ValueError("請至少上傳一張照片")
            if experimental_total_bytes_for_user(email) + incoming_total > EXPERIMENTAL_MAX_TOTAL_BYTES:
                self.send_json({"ok": False, "error": "超過上傳空間上限"}, 403)
                return

            cid_base = safe_path_segment(name.lower().replace(" ", "-"), "experimental")
            with _CHARACTER_LOCK:
                existing_ids = {item["id"] for item in load_character_index()}
            cid = cid_base
            index = 2
            while cid in existing_ids:
                cid = safe_path_segment(f"{cid_base}-{index}", "experimental")
                index += 1
            target_dir = (CHARACTER_USER_ASSET_ROOT / experimental_email_slug(email) / cid).resolve()
            if not target_dir.is_relative_to(CHARACTER_USER_ASSET_ROOT.resolve()):
                raise ValueError("Invalid upload path")
            target_dir.mkdir(parents=True, exist_ok=True)
            media = []
            for item in decoded:
                target_name = f"photo_{int(time.time())}_{secrets.token_hex(4)}{item['suffix']}"
                target = (target_dir / Path(target_name).name).resolve()
                if not target.is_relative_to(CHARACTER_USER_ASSET_ROOT.resolve()):
                    raise ValueError("Invalid upload filename")
                target.write_bytes(item["raw"])
                media.append({
                    "label": Path(item["name"]).stem[:80] or "角色照片",
                    "status": "ready",
                    "sourcePath": str(target),
                    "contentType": item["contentType"],
                })
            body = {
                "id": cid,
                "name": name,
                "kind": "custom",
                "type": "experimental",
                "visualClass": "unknown",
                "group": "experimental",
                "tagline": "私人實驗角色",
                "profile": "",
                "personality": "",
                "origin": "實驗場上傳",
                "color": "#64748b",
                "ownerEmail": email,
                "visibility": "private",
                "style": {
                    "base": "selectable",
                    "substyle": "",
                    "fixedPrompt": name,
                    "negativePrompt": "lowres, bad anatomy, watermark, text",
                },
                "assets": {
                    "avatar": media[0],
                    "characterMedia": media,
                    "voiceRefs": [],
                },
                "source": {"system": "castle", "sourceType": "experimental"},
                "actions": ["photoshoot", "group-photo", "multi-angle-selfie", "image-video", "dance", "post"],
            }
            saved = upsert_character(body, server_side=True)
            character = character_public_for_user(
                get_character_entry(saved["id"]),
                user,
                self.is_orchestrator_service_authenticated(),
            )
            self.send_json({"ok": True, "character": character})
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_user_photo_import(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 5 or parts[3] != "user-photo-import":
                self.send_json({"ok": False, "error": "Character photo not found"}, 404)
                return
            character = get_character_entry(parts[2])
            if not user_can_see_character(self.current_user(), character, self.is_orchestrator_service_authenticated()):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            body = self.read_json(MAX_TEXT_BODY)
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "photo"
            result = import_user_photo_to_input(character, parts[4], slot)
            self.send_json({"ok": True, **result})
        except KeyError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 400)

    def api_character_update(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 3:
                self.send_json({"ok": False, "error": "Character not found"}, 404)
                return
            existing = get_character_entry(parts[2])
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            if not user_can_edit_character(user, existing, service_token):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            body = self.read_json(MAX_TEXT_BODY)
            saved = upsert_character(
                body,
                parts[2],
                allow_visual_class_update=bool(
                    service_token or (user and user.get("role") == "admin")
                ),
            )
            character = character_public_for_user(get_character_entry(saved["id"]), user, service_token)
            self.send_json({"ok": True, "character": character})
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_character_delete(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) < 4:
                self.send_json({"ok": False, "error": "Character not found"}, 404)
                return
            self.read_json(MAX_TEXT_BODY)
            existing = get_character_entry(parts[2])
            if not user_can_edit_character(self.current_user(), existing, self.is_orchestrator_service_authenticated()):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            delete_character(parts[2])
            self.send_json({"ok": True, "deleted": parts[2]})
        except KeyError as error:
            self.send_error_json(error, 404)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_library_collection(self, query: str | None) -> None:
        try:
            if query is None:
                result = material_collector.start(library_server_context(), self.current_user(), self.read_json(MAX_TEXT_BODY))
            else:
                params = urllib.parse.parse_qs(query)
                result = material_collector.job_status(library_server_context(), (params.get("id") or [""])[0], self.current_user())
            self.send_json(result)
        except PermissionError:
            self.send_json({"ok": False, "error": "沒有權限"}, 403)
        except FileNotFoundError:
            self.send_json({"ok": False, "error": "找不到採集任務"}, 404)
        except (ValueError, OSError) as error:
            self.send_json({"ok": False, "error": str(error) if isinstance(error, ValueError) else "來源暫時無法連線"}, 400)

    def api_library(self, path: str, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            context = library_server_context()
            user = self.current_user()
            if path == "/api/library":
                self.send_json(library_service.list_items(context, user, params))
                return
            store = library_service.catalog(context)
            item = store.get((params.get("id") or [""])[0], library_service.owner_scope(user))
            if not item:
                self.send_json({"ok": False, "error": "找不到素材"}, 404)
                return
            target = item["path"]
            if (params.get("thumb") or [""])[0] == "1":
                target = library_service.thumbnail(store, item)
            self.send_local_file(target, inline_filename=target.name,
                                 download=(params.get("dl") or [""])[0] == "1",
                                 cache_control="private, max-age=86400" if (params.get("thumb") or [""])[0] == "1" else "private, no-cache")
        except PermissionError:
            self.send_json({"ok": False, "error": "沒有權限"}, 403)
        except (ValueError, KeyError):
            self.send_json({"ok": False, "error": "無效的素材查詢"}, 400)
        except Exception as error:
            print(f"Library request failed: {type(error).__name__}", flush=True)
            self.send_json({"ok": False, "error": "素材暫時無法讀取，請重試"}, 503)

    def api_library_mutate(self, path: str) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            user = self.current_user()
            context = library_server_context()
            store = library_service.catalog(context)
            if path == "/api/library/import":
                self.send_json(library_service.import_input(context, user, str(body.get("id") or ""), str(body.get("slot") or "image")))
                return
            patch = body.get("classification")
            if not isinstance(patch, dict):
                raise ValueError("Invalid classification")
            ids = patch.get("characterIds", [])
            if not isinstance(ids, list) or len(ids) > 32 or any(not isinstance(cid, str) for cid in ids):
                raise ValueError("Invalid characters")
            for cid in ids:
                try:
                    character = get_character_entry(cid)
                except KeyError:
                    runtime, actor = self._provider_runtime_actor()
                    runtime.store.get_owned_character(actor.user_id, cid)
                else:
                    if not user_can_see_character(user, character):
                        raise PermissionError("Character not visible")
            store.edit(str(body.get("id") or ""), patch, str(user.get("email") or ""), library_service.owner_scope(user))
            self.send_json({"ok": True})
        except PermissionError:
            self.send_json({"ok": False, "error": "沒有權限"}, 403)
        except (FileNotFoundError, KeyError):
            self.send_json({"ok": False, "error": "找不到素材或角色"}, 404)
        except (ValueError, TypeError):
            self.send_json({"ok": False, "error": "無效的素材分類"}, 400)
        except Exception:
            self.send_json({"ok": False, "error": "素材暫時無法更新，請稍後重試"}, 503)

    def api_history(self, query: str) -> None:
        try:
            user = self.current_user()
            scope, access_user = job_scope(query, user)
            upstream_params = urllib.parse.parse_qs(query or "", keep_blank_values=True)
            upstream_params.pop("scope", None)
            balanced = (upstream_params.pop("balanced", None) or ["0"])[0] == "1"
            upstream_query = urllib.parse.urlencode(upstream_params, doseq=True)
            suffix = f"?{upstream_query}" if upstream_query else "?max_items=30"
            history = request_json(f"/history{suffix}")
            with _JOB_INDEX_LOCK:
                job_index = load_job_index()
            if scope == "own":
                history = {
                    prompt_id: item for prompt_id, item in history.items()
                    if user_can_access_job(access_user, str(prompt_id), item, job_index)
                }
            with _HIDDEN_LOCK:
                hidden = load_hidden()
            limit = gallery_limit(query)
            access_email = normalize_email(str((access_user or {}).get("email") or ""))

            def item_visible(item: dict) -> bool:
                return scope != "own" or item.get("source") == "history" or owner_matches_account(view_item_owner_email(
                    str(item.get("filename") or ""),
                    str(item.get("subfolder") or ""),
                    str(item.get("type") or "output"),
                    str(item.get("promptId") or ""),
                    job_index,
                ), access_email)

            images = merged_gallery_items(
                history, hidden, limit, job_index,
                item_filter=item_visible if scope == "own" else None,
                balanced=balanced,
                owner_email=access_email if scope == "own" else None,
            )
            images = library_service.decorate_history(library_server_context(), images)
            terminal_jobs = {job["promptId"]: job for job in history_job_items(history, job_index, limit=min(limit, 200))}
            for job in gallery_terminal_job_items(images, job_index):
                terminal_jobs.setdefault(job["promptId"], job)
            ordered_terminal_jobs = sorted(terminal_jobs.values(), key=lambda item: item.get("timestamp", 0), reverse=True)[:min(limit, 200)]
            self.send_json({
                "ok": True,
                "scope": scope,
                "images": images,
                "jobs": ordered_terminal_jobs,
                "recent_errors": [decorate_job_metadata(item, job_index) for item in history_error_items(history, limit=20)],
            })
        except PermissionError as error:
            self.send_error_json(error, 403)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 502)

    def api_history_continuations(self, query: str) -> None:
        """Return only currently executable, server-owned result actions."""
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            filename, subfolder, view_type = validate_view_parts(
                str((params.get("filename") or [""])[0]),
                str((params.get("subfolder") or [""])[0]),
                str((params.get("type") or ["output"])[0]),
            )
            prompt_id = str((params.get("promptId") or params.get("prompt_id") or [""])[0]).strip()
            user = self.current_user()
            if not user_can_access_view_item(user, filename, subfolder, view_type, prompt_id):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            item = gallery_item_for_continuation(prompt_id, filename, subfolder, view_type)
            if item is None:
                self.send_json({"ok": False, "error": "找不到素材檔"}, 404)
                return
            actions = continuation_actions_for_item(item, user, statuses=result_continuation_statuses())
            self.send_json({
                "ok": True,
                "schema": "castle.result-chain.v1",
                "actions": actions,
                "provenance": {
                    "variantId": str(item.get("variantId") or ""),
                    "visualClass": str(item.get("visualClass") or ""),
                    "characterAttribution": str(item.get("characterAttribution") or ""),
                    "trusted": item.get("provenanceTrusted") is True,
                },
            })
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 502)

    def api_history_detail(self, query: str) -> None:
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        prompt_id = (params.get("prompt_id") or [""])[0].strip()
        filename = (params.get("filename") or [""])[0]
        subfolder = (params.get("subfolder") or [""])[0]
        view_type = (params.get("type") or ["output"])[0]

        def send_embedded_fallback() -> bool:
            if not filename:
                return False
            try:
                clean_filename, clean_subfolder, clean_type = validate_view_parts(
                    filename, subfolder, view_type,
                )
                target = resolve_view_path(clean_filename, clean_subfolder, clean_type)
            except ValueError as error:
                self.send_error_json(error, 400)
                return True
            if target is None or not target.is_file():
                self.send_json({"ok": False, "error": "找不到這個媒體檔"}, 404)
                return True
            if not user_can_access_view_item(
                self.current_user(),
                clean_filename,
                clean_subfolder,
                clean_type,
                prompt_id,
            ):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return True
            detail = embedded_media_history_detail(
                target,
                prompt_id=prompt_id or "disk",
                filename=clean_filename,
                subfolder=clean_subfolder,
                view_type=clean_type,
            )
            if detail is None:
                return False
            self.send_json({"ok": True, "available": True, "detail": detail})
            return True

        if not prompt_id or prompt_id == "disk":
            if send_embedded_fallback():
                return
            self.send_json({
                "ok": True,
                "available": False,
                "error": "這個媒體檔本身沒有可解析的生成資料",
            })
            return
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", prompt_id):
            self.send_json({"ok": False, "error": "Invalid prompt id"}, 400)
            return

        def send_direct_fallback(direct_found) -> bool:
            direct_data = direct_found[1] if direct_found else None
            direct = direct_history_detail(prompt_id, direct_data) if direct_data is not None else None
            if direct is None:
                return False
            access_metadata = None
            access_index = None
            if direct_found and direct_data is not None:
                owner_email = direct_sidecar_owner_email(direct_found[0], direct_data, prompt_id)
                access_metadata = {"submittedBy": {"email": owner_email}} if owner_email else {}
                # The direct sidecar is the durable source after bounded job-index
                # eviction. Do not silently fall back to a stale/reused index row.
                access_index = {}
            if not user_can_access_job(
                self.current_user(), prompt_id, access_metadata, access_index
            ):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return True
            self.send_json({"ok": True, "available": True, "detail": direct})
            return True

        if prompt_id.startswith("local-"):
            if send_direct_fallback(direct_sidecar_for_prompt(prompt_id)):
                return
            self.send_json({"ok": True, "available": False, "error": "找不到這筆本機任務"})
            return

        upstream_error = None
        try:
            history = request_json(f"/history/{urllib.parse.quote(prompt_id)}")
            item = history.get(prompt_id)
            if item:
                if not user_can_access_job(self.current_user(), prompt_id, item):
                    self.send_json({"ok": False, "error": "沒有權限"}, 403)
                    return
                self.send_json({"ok": True, "available": True, "detail": history_prompt_detail(prompt_id, item)})
                return
        except Exception as error:
            upstream_error = error

        if send_direct_fallback(direct_sidecar_for_prompt(prompt_id)):
            return
        if send_embedded_fallback():
            return
        if upstream_error is not None:
            self.send_error_json(upstream_error, 502)
            return
        self.send_json({"ok": True, "available": False, "error": "ComfyUI history 已找不到這筆任務"})

    def _view_parts_from_body(self) -> tuple[tuple[str, str, str], dict]:
        body = self.read_json()
        return validate_view_parts(
            str(body.get("filename") or ""),
            str(body.get("subfolder") or ""),
            str(body.get("type") or "output"),
        ), body

    def api_history_hide(self) -> None:
        try:
            (filename, subfolder, view_type), body = self._view_parts_from_body()
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if not user_can_access_view_item(self.current_user(), filename, subfolder, view_type, str(body.get("promptId") or body.get("prompt_id") or "")):
            self.send_json({"ok": False, "error": "沒有權限"}, 403)
            return
        try:
            hide = bool(body.get("hidden", True))
            key = (view_type, subfolder, filename)
            with _GALLERY_MUTATION_LOCK:
                with _HIDDEN_LOCK:
                    keys = load_hidden()
                    if hide:
                        keys.add(key)
                    else:
                        keys.discard(key)
                    save_hidden(keys)
            if view_type == "output" and library_service.catalog_path().is_file():
                library_service.catalog(library_server_context()).set_hidden(
                    "output", "/".join(filter(None, (subfolder, filename))), hide
                )
            self.send_json({"ok": True, "hidden": hide})
        except Exception as error:
            self.send_error_json(error, 500)

    def api_history_delete(self) -> None:
        try:
            (filename, subfolder, view_type), body = self._view_parts_from_body()
            target = resolve_view_path(filename, subfolder, view_type)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if not user_can_access_view_item(self.current_user(), filename, subfolder, view_type, str(body.get("promptId") or body.get("prompt_id") or "")):
            self.send_json({"ok": False, "error": "沒有權限"}, 403)
            return
        try:
            with _GALLERY_MUTATION_LOCK:
                target = resolve_view_path(filename, subfolder, view_type)
                if target is None:
                    self.send_json({"ok": False, "error": "Invalid type"}, 400)
                    return
                if target.is_file():
                    sidecar = direct_sidecar_path(target)
                    target.unlink()
                    try:
                        sidecar.unlink()
                    except OSError:
                        pass
                warning = ""
                try:
                    with _HIDDEN_LOCK:
                        keys = load_hidden()
                        keys.discard((view_type, subfolder, filename))
                        save_hidden(keys)
                except Exception as hidden_error:
                    warning = "檔案已刪除，但隱藏清單未能同步"
                    print(f"WARNING gallery hidden-state delete failed: {hidden_error}")
            self.send_json({"ok": True, **({"warning": warning} if warning else {})})
        except Exception as error:
            self.send_error_json(error, 500)

    def api_history_rename(self) -> None:
        try:
            (filename, subfolder, view_type), body = self._view_parts_from_body()
            target = resolve_view_path(filename, subfolder, view_type)
            new_filename = normalized_rename_filename(str(body.get("name") or ""), filename)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
            return
        prompt_id = str(body.get("promptId") or body.get("prompt_id") or "").strip()
        user = self.current_user()
        if not user_can_access_view_item(user, filename, subfolder, view_type, prompt_id):
            self.send_json({"ok": False, "error": "沒有權限"}, 403)
            return
        try:
            with _GALLERY_MUTATION_LOCK:
                target = resolve_view_path(filename, subfolder, view_type)
                if target is None:
                    self.send_json({"ok": False, "error": "Invalid type"}, 400)
                    return
                if not target.is_file():
                    self.send_json({"ok": False, "error": "檔案不存在"}, 404)
                    return
                destination = target.with_name(new_filename).resolve()
                base = VIEW_DIRS[view_type].resolve()
                if not destination.is_relative_to(base):
                    self.send_json({"ok": False, "error": "Invalid path"}, 400)
                    return
                if destination.exists():
                    self.send_json({"ok": False, "error": "同名檔案已存在"}, 409)
                    return
                old_sidecar = direct_sidecar_path(target)
                new_sidecar = direct_sidecar_path(destination)
                if new_sidecar.exists():
                    self.send_json({"ok": False, "error": "同名素材資料已存在"}, 409)
                    return
                existing_sidecar = load_direct_sidecar(target)
                gallery_item = gallery_file_item(target, view_type, set(), prompt_id or "disk") if existing_sidecar else None
                if gallery_item is None:
                    gallery_item = history_gallery_item_for_view(prompt_id, filename, subfolder, view_type)
                    if existing_sidecar is None:
                        existing_sidecar = load_direct_sidecar(target)
                if gallery_item is None:
                    gallery_item = gallery_file_item(target, view_type, set(), prompt_id or "disk")
                owner_email = view_item_owner_email(filename, subfolder, view_type, prompt_id)
                sidecar_data = renamed_gallery_sidecar_data(
                    existing_sidecar,
                    gallery_item,
                    prompt_id=prompt_id,
                    owner_email=owner_email,
                    old_filename=filename,
                    new_filename=new_filename,
                    subfolder=subfolder,
                    view_type=view_type,
                )
                staging_sidecar = new_sidecar.with_name(f".{new_sidecar.name}.rename-{os.getpid()}-{secrets.token_hex(4)}")
                atomic_write_json(staging_sidecar, sidecar_data, 0o600)
                try:
                    move_file_no_replace(target, destination)
                    try:
                        move_file_no_replace(staging_sidecar, new_sidecar)
                    except BaseException:
                        try:
                            move_file_no_replace(destination, target)
                        except OSError as rollback_error:
                            print(f"WARNING gallery rename rollback failed: {rollback_error}")
                        raise
                finally:
                    try:
                        staging_sidecar.unlink()
                    except OSError:
                        pass
                try:
                    old_sidecar.unlink()
                except OSError:
                    pass
                warning = ""
                try:
                    with _HIDDEN_LOCK:
                        keys = load_hidden()
                        old_key = (view_type, subfolder, filename)
                        new_key = (view_type, subfolder, new_filename)
                        was_hidden = old_key in keys
                        keys.discard(old_key)
                        if was_hidden:
                            keys.add(new_key)
                        save_hidden(keys)
                except Exception as hidden_error:
                    keys = set()
                    warning = "檔案已改名，但隱藏狀態未能同步"
                    print(f"WARNING gallery hidden-state rename failed: {hidden_error}")
                item = gallery_file_item(destination, view_type, keys)
            self.send_json({"ok": True, "item": item, **({"warning": warning} if warning else {})})
        except FileExistsError:
            self.send_json({"ok": False, "error": "同名檔案已存在"}, 409)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_view(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            filename, subfolder, view_type = validate_view_parts(
                (params.get("filename") or [""])[0],
                (params.get("subfolder") or [""])[0],
                (params.get("type") or ["output"])[0],
            )
            target = resolve_view_path(filename, subfolder, view_type)
        except ValueError as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if target is None or not target.is_file():
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        user = self.current_user()
        if not (user and user.get("role") == "admin"):
            if view_type != "input":
                prompt_id = str((params.get("promptId") or params.get("prompt_id") or [""])[0])
                if not user_can_access_view_item(user, filename, subfolder, view_type, prompt_id):
                    self.send_json({"ok": False, "error": "沒有權限"}, 403)
                    return
            # SPEC P2 keeps shared input refs visible; private uploads get dedicated routes.
        download = (params.get("dl") or [""])[0] in ("1", "true", "yes")
        self.send_local_file(target, inline_filename=filename, download=download)

    def api_audio_export(self, query: str) -> None:
        source_handle = None
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            filename, subfolder, view_type = validate_view_parts(
                (params.get("filename") or [""])[0],
                (params.get("subfolder") or [""])[0],
                (params.get("type") or ["output"])[0],
            )
            if view_type != "output":
                raise ValueError("只支援匯出已生成的語音作品")
            output_format = str((params.get("format") or ["wav"])[0]).strip().lower()
            if output_format not in AUDIO_EXPORT_FORMATS:
                raise ValueError("下載格式只支援 WAV、MP3 或 MP4")
            prompt_id = str((params.get("promptId") or params.get("prompt_id") or [""])[0])
            with _GALLERY_MUTATION_LOCK:
                target = resolve_view_path(filename, subfolder, view_type)
                if target is None or not target.is_file():
                    self.send_json({"ok": False, "error": "找不到原始語音檔"}, 404)
                    return
                if gallery_media_kind(target) != "audio":
                    raise ValueError("這個作品不是可轉出的語音檔")
                user = self.current_user()
                if not (user and user.get("role") == "admin") and not user_can_access_view_item(
                    user,
                    filename,
                    subfolder,
                    view_type,
                    prompt_id,
                ):
                    self.send_json({"ok": False, "error": "沒有權限"}, 403)
                    return
                try:
                    source_handle = target.open("rb")
                except OSError:
                    self.send_json({"ok": False, "error": "找不到原始語音檔"}, 404)
                    return
            exported = ensure_audio_export(target, output_format, source_handle)
            source_handle.close()
            source_handle = None
            spec = AUDIO_EXPORT_FORMATS[output_format]
            prepare_only = str((params.get("prepare") or [""])[0]).lower() in {"1", "true", "yes"}
            if prepare_only:
                self.send_json(
                    {
                        "ok": True,
                        "filename": audio_export_filename(filename, output_format),
                        "format": output_format,
                    },
                    cache_control="private, no-store",
                )
                return
            self.send_local_file(
                exported,
                content_type=spec["contentType"],
                inline_filename=audio_export_filename(filename, output_format),
                download=True,
                cache_control="private, no-store",
            )
        except AudioExportBusy as error:
            self.send_error_json(error, 429)
        except RuntimeError as error:
            self.send_error_json(error, 503)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)
        finally:
            if source_handle is not None:
                source_handle.close()

    def api_auth_setup(self) -> None:
        if auth_config() is not None:
            self.send_json({"ok": False, "error": "Password is already configured"}, 409)
            return
        if self.request_lane() != "local":
            self.send_json(
                {"ok": False, "error": "First-run setup is only allowed from the local machine"},
                403,
            )
            return
        try:
            password = str(self.read_json().get("password") or "")
            if len(password) < 8:
                self.send_json({"ok": False, "error": "Password must be at least 8 characters"}, 400)
                return
            AUTH_PATH.write_text(json.dumps(password_hash(password), indent=2), encoding="utf-8")
            AUTH_PATH.chmod(0o600)
            self.login_success(LOCAL_SESSION_USER)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_auth_login(self) -> None:
        lane = self.request_lane()
        if lane == "tunnel-legacy":
            self.send_json({"ok": False, "error": "請使用管理員設定的網站入口登入。"}, 409)
            return
        config = auth_config()
        if config is None and lane != "access":
            self.send_json({"ok": False, "error": "Password is not configured yet"}, 409)
            return
        ip = self.client_ip()
        retry_after = login_retry_after(ip)
        if retry_after > 0:
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(int(retry_after) + 1))
            body = json.dumps({"ok": False, "error": "Too many attempts; slow down"}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            password = str(self.read_json().get("password") or "")
        except Exception as error:
            self.send_error_json(error, 400)
            return
        if lane == "access":
            access_email = self.access_identity()
            if not access_email:
                return
            user = user_for_email(access_email)
            if not user or user.get("disabled"):
                login_record_failure(ip)
                self.send_json({"ok": False, "error": "Wrong password"}, 401)
                return
            if user.get("mustSetPassword") or not user.get("passwordHash"):
                self.send_json({"ok": False, "error": "needs-setup", "accessEmail": access_email}, 409)
                return
            if not verify_user_password(password, user.get("passwordHash")):
                login_record_failure(ip)
                self.send_json({"ok": False, "error": "Wrong password"}, 401)
                return
            identity = {"email": access_email, "role": user["role"], "displayName": user["displayName"]}
        else:
            if not config or not verify_password(password, config):
                login_record_failure(ip)
                self.send_json({"ok": False, "error": "Wrong password"}, 401)
                return
            identity = LOCAL_SESSION_USER
        login_record_success(ip)
        self.login_success(identity)

    def api_auth_setup_password(self) -> None:
        if self.request_lane() != "access":
            self.send_json({"ok": False, "error": "需要 Cloudflare Access 身分"}, 403)
            return
        access_email = self.access_identity()
        if not access_email:
            return
        try:
            password = str(self.read_json().get("password") or "")
        except Exception as error:
            self.send_error_json(error, 400)
            return
        if len(password) < 8:
            self.send_json({"ok": False, "error": "Password must be at least 8 characters"}, 400)
            return
        with _USERS_LOCK:
            users = load_users()
            user = users.get(access_email)
            if not user or user.get("disabled") or not user.get("mustSetPassword"):
                self.send_json({"ok": False, "error": "需要有效的初次設定邀請"}, 403)
                return
            user = dict(user)
            user["passwordHash"] = user_password_hash(password)
            user["mustSetPassword"] = False
            user["updated"] = time.time()
            users[access_email] = user
            save_users(users)
        self.login_success({"email": access_email, "role": user["role"], "displayName": user["displayName"]})

    def api_auth_logout(self) -> None:
        token = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE)
        session_invalidate(token)
        raw = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.clear_session_cookie()
        self.end_headers()
        self.wfile.write(raw)

    def api_google_status(self) -> None:
        configured = google_drive_configured()
        self.send_json({
            "ok": True,
            "configured": configured,
            "connected": google_token_connected() if configured else False,
            "redirectUri": self.google_redirect_uri(),
            "scope": GOOGLE_DRIVE_SCOPE,
        })

    def api_google_login(self) -> None:
        if not google_drive_configured():
            self.send_json({
                "ok": False,
                "error": "Google OAuth 尚未設定：請設定 CASTLE_GOOGLE_CLIENT_ID / CASTLE_GOOGLE_CLIENT_SECRET",
                "redirectUri": self.google_redirect_uri(),
            }, 400)
            return
        session_token = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE) or ""
        state = secrets.token_urlsafe(32)
        redirect_uri = self.google_redirect_uri()
        now = time.time()
        with _GOOGLE_LOCK:
            for key, item in list(GOOGLE_AUTH_STATES.items()):
                if float(item.get("expires", 0)) <= now:
                    GOOGLE_AUTH_STATES.pop(key, None)
            GOOGLE_AUTH_STATES[state] = {
                "session": session_token,
                "redirectUri": redirect_uri,
                "expires": now + 600,
            }
        params = urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_DRIVE_SCOPE,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        })
        self.send_redirect(f"{GOOGLE_AUTH_URL}?{params}")

    def api_google_callback(self, query: str) -> None:
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        state = (params.get("state") or [""])[0]
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]
        with _GOOGLE_LOCK:
            record = GOOGLE_AUTH_STATES.pop(state, None)
        if not record or float(record.get("expires", 0)) <= time.time():
            self.send_redirect("/?google_drive=state_error")
            return
        current_session = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE) or ""
        if current_session and record.get("session") and current_session != record.get("session"):
            self.send_redirect("/?google_drive=session_error")
            return
        if error or not code:
            self.send_redirect("/?google_drive=denied")
            return
        try:
            data = request_form_json(GOOGLE_TOKEN_URL, {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": str(record.get("redirectUri") or self.google_redirect_uri()),
            })
            existing = load_google_token() or {}
            refresh_token = data.get("refresh_token") or existing.get("refresh_token")
            token = dict(data)
            if refresh_token:
                token["refresh_token"] = refresh_token
            token["expires_at"] = int(time.time()) + int(data.get("expires_in") or 3600) - 60
            save_google_token(token)
            self.send_redirect("/?google_drive=connected")
        except Exception:
            self.send_redirect("/?google_drive=token_error")

    def api_google_logout(self) -> None:
        try:
            GOOGLE_TOKEN_PATH.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            self.send_error_json(error, 500)
            return
        self.send_json({"ok": True, "connected": False})

    def api_google_drive_files(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            search = (params.get("q") or [""])[0]
            limit = gallery_limit(query, default=50, cap=100)
            configured = google_drive_configured()
            connected = google_token_connected() if configured else False
            self.send_json({
                "ok": True,
                "configured": configured,
                "connected": connected,
                "files": list_google_drive_videos(search, limit) if connected else [],
            })
        except Exception as error:
            self.send_error_json(error, 400)

    def api_google_drive_import(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            file_id = str(body.get("fileId") or "")
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "video"
            result = import_google_drive_file(file_id, slot)
            self.send_json({"ok": True, **result})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_generate(self) -> None:
        try:
            raw_body = self.read_json(MAX_UPLOAD_BODY)
            if not isinstance(raw_body, dict):
                raise ValueError("request body must be an object")
            requested_character_id = str(raw_body.get("characterId") or "").strip()
            body = strip_submitted_by(raw_body)
            if requested_character_id:
                body["characterId"] = requested_character_id
            submitted_by = self.current_user()
            feature_id = str(body.get("featureId") or "").strip()
            feature = FEATURE_BY_ID.get(feature_id)
            if feature is None:
                raise ValueError("Unknown feature")
            if feature.get("apiEnabled") is False:
                raise ValueError("這個工具尚未開放")
            if feature.get("operatorOnly") and not is_operator_user(submitted_by):
                raise PermissionError("這項功能只允許 operator 操作")
            builder = BUILDERS.get(feature.get("builder")) if feature.get("builder") else None
            if builder is None:
                raise ValueError("此功能尚未開放生成")

            raw_settings = body.get("settings") or {}
            if not isinstance(raw_settings, dict):
                raise ValueError("settings must be an object")
            settings = strip_client_character_settings(raw_settings)
            character_id = requested_character_id
            action_id = str(body.get("action") or "").strip()
            variant_id = str(body.get("variantId") or "").strip()
            submitted_files = body.get("files")
            reference_receipt = None
            character_entry = None
            character_context = None
            provider_character_request = (
                feature_id == CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID
            )
            if provider_character_request:
                if action_id != CLIPMODE_CHARACTER_PHOTOSHOOT_ACTION_ID:
                    raise ValueError("角色身份寫真必須使用 action=photoshoot")
                if variant_id != CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID:
                    raise ValueError(
                        "角色身份寫真必須使用 "
                        f"variantId={CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID}"
                    )
                unknown_settings = sorted(
                    str(key) for key in settings
                    if str(key) not in {
                        "prompt", "negative_prompt", "aspect_ratio", "seed",
                    }
                )
                if unknown_settings:
                    raise ValueError(
                        "角色身份寫真不支援設定：" + ", ".join(unknown_settings)
                    )
                runtime, actor = self._provider_runtime_actor()
                character_context, private_settings, reference_receipt = (
                    resolve_owned_provider_character_photoshoot(
                        runtime, actor, character_id,
                    )
                )
                settings.update(private_settings)
            else:
                character_entry = character_entry_for_generation(
                    character_id,
                    submitted_by,
                    self.is_orchestrator_service_authenticated(),
                )
                character_context = character_generation_context(character_entry)
            if character_entry:
                capability = resolve_character_capability(
                    character_entry,
                    action_id,
                    feature_id,
                    variant_id,
                )
                runtime = breezy_persona_runtime() if capability.get("variantId") == "voice-speak.persona.v1" else None
                capability = require_character_capability_runtime(character_entry, capability, runtime)
                settings = apply_character_capability_settings(character_entry, capability, settings)
                submitted_files, reference_receipt = bind_character_capability_files(
                    character_entry,
                    capability,
                    submitted_files,
                    settings,
                )
            elif not provider_character_request:
                # Generic Studio requests remain supported, but they cannot
                # attach forged role attribution or action/variant receipts.
                if feature.get("characterOnly"):
                    raise ValueError("這個工具必須先選擇適用的角色")
                if action_id or variant_id:
                    raise ValueError("未指定角色時不能提供 action 或 variantId")
                if feature_id == "voice-speak":
                    source = str(settings.get("voiceSource") or "persona").strip()
                    if source != "library":
                        raise ValueError("角色聲音必須先選擇角色；通用 Studio 只提供聲音庫克隆")
                if feature_id == "voxcpm-tts":
                    engine = str(settings.get("voiceEngine") or "voxcpm").strip()
                    mode = str(settings.get("mode") or "lora").strip()
                    if engine not in {"breezy", "voxcpm"}:
                        raise ValueError(f"不支援的語音引擎：{engine}")
                    if mode not in {"design", "reference", "ultimate", "lora"}:
                        raise ValueError(f"不支援的 VoxCPM 模式：{mode}")
                    if engine == "breezy":
                        raise ValueError("Breezy 角色聲音必須先選擇角色")
                    if mode == "lora":
                        profile = str(settings.get("voiceProfile") or "custom_english").strip()
                        allowed_profiles = {str(item.get("value") or "") for item in VOXCPM_PROFILES}
                        if profile not in allowed_profiles:
                            raise ValueError(f"不支援的 VoxCPM LoRA Profile：{profile or '空白'}")
                    if mode == "lora" and not is_operator_user(submitted_by):
                        raise PermissionError("Custom English 聲音只允許 operator 操作")
                action_id = ""
                variant_id = ""

            settings = apply_character_visual_class_hints(settings, character_context)
            settings = resolve_generation_presets(feature_id, settings)
            settings = validate_feature_option_contract(feature, settings)
            settings = enforce_p0_generation_options(feature_id, settings)
            settings = validate_voice_generation_contract(
                feature_id,
                settings,
                submitted_by,
                self.is_orchestrator_service_authenticated(),
            )
            submitted_files = validate_generation_file_contract(feature, submitted_files)
            voice_id = str(settings.get("voiceId") or "").strip()
            submitted_voice = bool(submitted_files.get("voice"))
            if voice_id and submitted_voice:
                raise ValueError("聲音庫與上傳音檔不能同時使用，請只選擇一種聲音來源")
            if feature_id == "lipsync" and not (voice_id or submitted_voice):
                raise ValueError("請從聲音庫挑選或上傳要對嘴的語音")
            resolved_voice_reference = ""
            if voice_id:
                resolved_voice_reference = resolve_voice_reference(
                    voice_id,
                    submitted_by,
                    self.is_orchestrator_service_authenticated(),
                )
            if character_context:
                settings["_characterId"] = character_context["id"]
                settings["_characterName"] = character_context["name"]
                settings["_characterActionId"] = action_id
                settings["_characterVariantId"] = variant_id
                settings["_characterVisualClass"] = normalized_visual_class(
                    character_context.get("visualClass")
                )
                if reference_receipt:
                    settings["_characterReference"] = reference_receipt
                    character_context["reference"] = reference_receipt

            # All capability and option checks happen before files are copied
            # into ComfyUI input, so rejected requests leave no upload debris.
            status = next((item for item in feature_status() if item["id"] == feature_id), None)
            status_ready = bool(status and status.get("ready"))
            status_missing_nodes = list((status or {}).get("missingNodes") or [])
            status_runtime_reason = str((status or {}).get("runtimeReason") or "")
            if status and feature_id == "voice-speak" and str(settings.get("voiceSource") or "persona") == "library":
                clone_engine = str(settings.get("cloneEngine") or "taiwan").strip()
                runtime_options = status.get("runtimeOptions") if isinstance(status.get("runtimeOptions"), dict) else None
                available_clone_engines = (
                    [str(value) for value in runtime_options.get("cloneEngine", [])]
                    if runtime_options is not None
                    else None
                )
                if available_clone_engines is not None and clone_engine not in available_clone_engines:
                    label = VOICE_CLONE_ENGINE_LABELS.get(clone_engine, clone_engine)
                    raise ValueError(f"聲音庫克隆引擎「{label}」目前尚未就緒")
                status_ready = bool(status.get("genericReady", status_ready))
                status_missing_nodes = list(status.get("genericMissingNodes") or [])
                status_runtime_reason = str(status.get("genericRuntimeReason") or "")
            if status and not status_ready:
                problems = []
                if status_runtime_reason:
                    problems.append(status_runtime_reason)
                if status_missing_nodes:
                    problems.append("缺少節點：" + ", ".join(status_missing_nodes))
                if status["missingModels"]:
                    problems.append("缺少模型：" + ", ".join(status["missingModels"]))
                raise ValueError("；".join(problems) or "尚未就緒")

            if feature_id == CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID:
                settings = prepare_clipmode_photoshoot_effective_prompts(settings)

            uploads = {}
            for slot, file_info in submitted_files.items():
                if not file_info:
                    continue
                if isinstance(file_info, dict) and file_info.get("_characterAssetPath"):
                    uploads[slot] = stage_character_asset_for_comfy(file_info, character_id)
                elif isinstance(file_info, dict) and file_info.get("serverFile"):
                    uploads[slot] = resolve_server_file(file_info["serverFile"])
                else:
                    uploads[slot] = save_uploaded_file(file_info, slot)

            if resolved_voice_reference:
                uploads["voice"] = resolved_voice_reference
            if feature_id == "voice-speak":
                source = str(settings.get("voiceSource") or "persona").strip()
                if source == "library":
                    if not voice_id:
                        raise ValueError("請從聲音庫選擇一段參考音訊")
                    clone_engine = str(settings.get("cloneEngine") or "taiwan").strip()
                    if clone_engine not in ("taiwan", "f5"):
                        raise ValueError(f"不支援的聲音庫克隆引擎：{clone_engine}")
                    clone_settings = dict(settings)
                    clone_settings["engine"] = clone_engine
                    clone_settings["speech"] = settings.get("text") or ""
                    if not str(clone_settings.get("prefix") or "").strip():
                        if character_context:
                            clone_settings["prefix"] = output_prefix(clone_settings, "voice-speak")
                        else:
                            engine_tag = "BreezyClone" if clone_engine == "taiwan" else "F5Clone"
                            clone_settings["prefix"] = f"Castle/voice-speak_{engine_tag}_{safe_path_segment(voice_id, 'voice')}"
                    prompt = voice_clone_prompt(clone_settings, uploads)
                    payload = {
                        "client_id": CLIENT_ID,
                        "prompt": prompt,
                        "extra_data": workflow_extra_data("castle-voice-speak-library-web", character_context, submitted_by, action_id, variant_id),
                    }
                    result = request_json("/prompt", payload)
                    record_job_attribution(result.get("prompt_id") or result.get("promptId"), submitted_by, character_id, "voice-speak", action_id, variant_id)
                    self.send_json({"ok": True, **result, **({"variantId": variant_id} if variant_id else {})})
                    return
                if bool(settings.get("qualityGate")):
                    result = start_guarded_voice_job(settings)
                    record_job_attribution(result.get("prompt_id") or result.get("promptId") or result.get("run_id"), submitted_by, character_id, "voice-speak", action_id, variant_id)
                    self.send_json({**result, **({"variantId": variant_id} if variant_id else {})})
                    return
            if feature_id == "voxcpm-tts":
                engine = str(settings.get("voiceEngine") or "voxcpm").strip()
                if engine not in {"breezy", "voxcpm"}:
                    raise ValueError(f"不支援的語音引擎：{engine}")
                if engine == "breezy":
                    if bool(settings.get("qualityGate")):
                        result = start_guarded_voice_job(settings)
                        record_job_attribution(result.get("prompt_id") or result.get("promptId") or result.get("run_id"), submitted_by, character_id, "voxcpm-tts", action_id, variant_id)
                        self.send_json(result)
                        return
                    prompt = voice_speak_prompt(settings, uploads)
                    payload = {
                        "client_id": CLIENT_ID,
                        "prompt": prompt,
                        "extra_data": workflow_extra_data("castle-voice-speak-web", character_context, submitted_by, action_id, variant_id),
                    }
                    result = request_json("/prompt", payload)
                    record_job_attribution(result.get("prompt_id") or result.get("promptId"), submitted_by, character_id, "voxcpm-tts", action_id, variant_id)
                    self.send_json({"ok": True, **result})
                    return
                result = start_voxcpm_voice_job(settings, uploads)
                record_job_attribution(result.get("prompt_id") or result.get("promptId") or result.get("run_id"), submitted_by, character_id, "voxcpm-tts", action_id, variant_id)
                self.send_json(result)
                return
            prompt = builder(settings, uploads)
            if feature_id == CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID:
                if not isinstance(character_context, dict):
                    raise ValueError("角色身份寫真缺少角色綁定")
                result = start_clipmode_character_photoshoot_job(
                    prompt,
                    settings,
                    submitted_by,
                    character_context,
                    action_id,
                    variant_id,
                )
                self.send_json({**result, "variantId": variant_id})
                return
            if feature_id in H3_FEATURE_CONTRACTS:
                result = start_minimax_h3_web_job(
                    prompt,
                    feature_id,
                    settings,
                    submitted_by,
                    character_id,
                    action_id,
                    variant_id,
                )
                self.send_json({
                    **result,
                    **({"variantId": variant_id} if variant_id else {}),
                })
                return
            payload = {
                "client_id": CLIENT_ID,
                "prompt": prompt,
                "extra_data": workflow_extra_data(f"castle-{feature_id}-web", character_context, submitted_by, action_id, variant_id),
            }
            result = request_json("/prompt", payload)
            record_job_attribution(
                result.get("prompt_id") or result.get("promptId"),
                submitted_by,
                character_id,
                feature_id,
                action_id,
                variant_id,
                character_context=character_context,
            )
            self.send_json({"ok": True, **result, **({"variantId": variant_id} if variant_id else {})})
        except PermissionError as error:
            self.send_error_json(error, 403)
        except Exception as error:
            self.send_error_json(error, 400)

    def api_post_generate(self) -> None:
        try:
            raw_body = self.read_json(MAX_TEXT_BODY)
            if not isinstance(raw_body, dict):
                raise ValueError("request body must be an object")
            requested_character_id = str(raw_body.get("characterId") or "").strip()
            body = strip_submitted_by(raw_body)
            if requested_character_id:
                body["characterId"] = requested_character_id
            raw_settings = body.get("settings") or {}
            if not isinstance(raw_settings, dict):
                raise ValueError("settings must be an object")
            settings = strip_client_character_settings(raw_settings)
            character_id = requested_character_id
            feature_id = str(body.get("featureId") or "post-generator").strip()
            action_id = str(body.get("action") or "").strip()
            variant_id = str(body.get("variantId") or "").strip()
            if feature_id != "post-generator":
                raise ValueError("Unknown post feature")
            character_entry = character_entry_for_generation(character_id, self.current_user(), False)
            character_context = character_generation_context(character_entry)
            if character_entry:
                capability = resolve_character_capability(
                    character_entry,
                    action_id,
                    feature_id,
                    variant_id,
                )
                capability = require_character_capability_runtime(character_entry, capability, None)
                settings = apply_character_capability_settings(character_entry, capability, settings)
            elif action_id or variant_id:
                raise ValueError("未指定角色時不能提供 action 或 variantId")
            settings = apply_character_visual_class_hints(settings, character_context)
            settings = resolve_generation_presets("post-generator", settings)
            if character_context:
                settings["_characterId"] = character_context["id"]
                settings["_characterName"] = character_context["name"]
                settings["_characterActionId"] = action_id
                settings["_characterVariantId"] = variant_id
            mode = str(settings.get("mode") or "advanced")
            if mode not in ("quick", "advanced"):
                raise ValueError(f"不支援的貼文模式：{mode}")
            sister = str(settings.get("sister") or "claude")
            platform = str(settings.get("platform") or "threads")
            tone = str(settings.get("tone") or "warm")
            length = str(settings.get("length") or "medium")
            if sister not in POST_SISTERS:
                raise ValueError(f"不支援的貼文角色模組：{sister}")
            if platform not in POST_PLATFORMS:
                raise ValueError(f"不支援的貼文平台：{platform}")
            if tone not in POST_TONES:
                raise ValueError(f"不支援的貼文語氣：{tone}")
            if length not in POST_LENGTHS:
                raise ValueError(f"不支援的貼文長度：{length}")
            topic = str(settings.get("topic") or "").strip()
            extra = str(settings.get("extra") or "").strip()
            keywords = str(settings.get("keywords") or "").strip()
            if not topic:
                raise ValueError("請輸入一句話" if mode == "quick" else "請輸入貼文主題")
            if character_context:
                char_extra = character_post_extra(character_context)
                if char_extra:
                    extra = "\n".join(part for part in (extra, char_extra) if part)
            result = generate_post(
                sister,
                platform,
                tone,
                topic,
                extra,
                mode,
                length,
                keywords,
                exact_backend=variant_id == "post.ai-sister.provider.v1",
            )
            if character_context:
                user = self.current_user()
                owner_email = normalize_email(str((user or {}).get("email") or ""))
                append_character_post(character_context["id"], settings, result, owner_email)
                result["character"] = character_context
            self.send_json({"ok": True, **result})
        except PermissionError as error:
            self.send_error_json(error, 403)
        except Exception as error:
            self.send_error_json(error, 400)

    def api_fetch_url(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            url = str(body.get("url") or "").strip()
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "video"
            result = fetch_video_url(url, slot)
            self.send_json({"ok": True, **result})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_fetch_drive(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            url = str(body.get("url") or "").strip()
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "video"
            result = fetch_google_drive_video(url, slot)
            self.send_json({"ok": True, **result})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_upload_input_file(self, query: str = "") -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            content_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            raw_upload = str((params.get("raw") or [""])[0]).lower() in {"1", "true", "yes"}
            if content_type == "application/json" and not raw_upload:
                body = self.read_json(MAX_UPLOAD_BODY)
                slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "file"
                file_info = body.get("file") if isinstance(body.get("file"), dict) else {}
                original_name = str(file_info.get("name") or "")
                filename = save_uploaded_file(file_info, slot)
            else:
                slot = re.sub(r"[^a-z0-9_-]+", "", str((params.get("slot") or [""])[0]).lower())[:32] or "file"
                encoded_name = str(self.headers.get("X-Castle-Filename") or (params.get("name") or [""])[0] or "upload")
                original_name = Path(urllib.parse.unquote(encoded_name)).name
                raw = self.read_body(MAX_UPLOAD_DECODED)
                if not raw:
                    raise RequestBodyError("Upload body is empty")
                filename = save_uploaded_bytes(raw, original_name, content_type, slot)
            self.send_json({"ok": True, "filename": filename, "name": original_name or filename})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_crawl_videos(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            search = (params.get("q") or [""])[0]
            limit = gallery_limit(query, default=MAX_CRAWL_VIDEO_ITEMS, cap=MAX_CRAWL_VIDEO_ITEMS)
            self.send_json({"ok": True, "videos": list_crawl_videos(search, limit)})
        except Exception as error:
            self.send_error_json(error, 500)

    def api_crawl_video_preview(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            video_id = str((params.get("id") or [""])[0])
            path = resolve_crawl_video_path(video_id)
            content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
            if not content_type.startswith("video/"):
                content_type = "video/mp4"
            self.send_local_file(path, content_type=content_type, inline_filename=path.name, cache_control="private, max-age=300")
        except Exception as error:
            self.send_error_json(error, 404)

    def api_crawl_video_import(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            video_id = str(body.get("id") or "")
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "video"
            result = import_crawl_video(video_id, slot)
            self.send_json({"ok": True, **result})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_history_import_input(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            filename, subfolder, view_type = validate_view_parts(
                str(body.get("filename") or ""),
                str(body.get("subfolder") or ""),
                str(body.get("type") or "output"),
            )
            if not user_can_access_view_item(self.current_user(), filename, subfolder, view_type, str(body.get("promptId") or body.get("prompt_id") or "")):
                self.send_json({"ok": False, "error": "沒有權限"}, 403)
                return
            prompt_id = str(body.get("promptId") or body.get("prompt_id") or "").strip()
            next_action_id = str(body.get("nextActionId") or "").strip()[:320]
            next_action = None
            if next_action_id:
                item = gallery_item_for_continuation(prompt_id, filename, subfolder, view_type)
                current_actions = continuation_actions_for_item(
                    item or {},
                    self.current_user(),
                    statuses=result_continuation_statuses(),
                )
                next_action = next((action for action in current_actions if action.get("id") == next_action_id), None)
                if next_action is None:
                    self.send_json({
                        "ok": False,
                        "code": "continuation_unavailable",
                        "error": "這個接續動作已變更，請重新整理後再試",
                    }, 409)
                    return
                slot = str(next_action.get("inputSlot") or "")
                expected_kind = str(next_action.get("expectedKind") or "")
            else:
                # Kept for non-result-chain callers such as the internal TTS
                # bridge. New result-detail UI always uses nextActionId.
                slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "file"
                expected_kind = str(body.get("expectedKind") or "")
            result = import_view_item_to_input(
                filename,
                subfolder,
                view_type,
                slot,
                expected_kind,
            )
            if next_action_id:
                # Re-resolve after the copy so a readiness/capability flip
                # cannot navigate the browser into a stale module. Remove the
                # just-created input file when that narrow race is detected.
                refreshed_item = gallery_item_for_continuation(prompt_id, filename, subfolder, view_type)
                refreshed_actions = continuation_actions_for_item(
                    refreshed_item or {},
                    self.current_user(),
                    statuses=result_continuation_statuses(),
                )
                refreshed_action = next((action for action in refreshed_actions if action.get("id") == next_action_id), None)
                if refreshed_action is None:
                    imported = (COMFY_INPUT / str(result.get("filename") or "")).resolve()
                    try:
                        if imported.is_relative_to(COMFY_INPUT.resolve()):
                            imported.unlink()
                    except OSError:
                        pass
                    self.send_json({
                        "ok": False,
                        "code": "continuation_unavailable",
                        "error": "這個接續動作剛剛變更，請重新整理後再試",
                    }, 409)
                    return
                next_action = refreshed_action
            self.send_json({"ok": True, **result, **({"nextAction": next_action} if next_action else {})})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_history_adopt(self) -> None:
        """Explicitly lock one accessible local result into the canonical media spine."""

        try:
            content_type = str(self.headers.get("Content-Type") or "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._send_provider_failure(415, "unsupported_media_type", "Local media adoption accepts application/json only.")
                return
            body = self.read_provider_json(8 * 1024)
            if not isinstance(body, dict) or set(body) != {
                "schema_version", "filename", "subfolder", "type", "prompt_id",
            } or body.get("schema_version") != "castle.local-history-adoption.v1":
                raise ValueError("local history adoption request is invalid")
            filename, subfolder, view_type = validate_view_parts(
                str(body.get("filename") or ""),
                str(body.get("subfolder") or ""),
                str(body.get("type") or "output"),
            )
            prompt_id = str(body.get("prompt_id") or "").strip()
            if not prompt_id_hint_valid(prompt_id) or not user_can_access_view_item(
                self.current_user(), filename, subfolder, view_type, prompt_id,
            ):
                self._send_provider_failure(404, "not_found", "Local history output was not found.")
                return
            source = resolve_view_path(filename, subfolder, view_type)
            if source is None or not source.is_file():
                raise FileNotFoundError(filename)
            media_kind = gallery_media_kind(source)
            mime_type = mimetypes.guess_type(source.name)[0] or ""
            if media_kind not in {"image", "video", "audio"}:
                raise ValueError("local history media kind is unsupported")
            item = gallery_item_for_continuation(
                prompt_id, filename, subfolder, view_type,
            )
            character = None
            if isinstance(item, dict):
                provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
                summary = item.get("character") if isinstance(item.get("character"), dict) else {}
                character_id = str(summary.get("id") or "").strip()
                visual_class = normalized_visual_class(item.get("visualClass"))
                if (
                    item.get("provenanceTrusted") is True
                    and item.get("characterAttribution") == "server"
                    and provenance.get("schema") == "castle.output-provenance.v1"
                    and provenance.get("characterId") == character_id
                    and normalized_visual_class(provenance.get("visualClass")) == visual_class
                    and character_id
                    and visual_class in {"anime", "realistic"}
                ):
                    entry = get_character_entry(character_id)
                    if (
                        user_can_generate_character(self.current_user(), entry)
                        and normalized_visual_class(entry.get("visualClass")) == visual_class
                    ):
                        character = CharacterRef(
                            character_id,
                            VisualClass(visual_class),
                            str(entry.get("name") or character_id)[:160],
                        )
            runtime, actor = self._provider_runtime_actor()
            adopted = adopt_local_history_output(
                runtime.store,
                owner_user_id=actor.user_id,
                account_id="local-main",
                source_path=source,
                source_identity="\x00".join((view_type, subfolder, filename)),
                mime_type=mime_type,
                display_name=filename[:160],
                character=character,
                audio_role=(
                    "voice"
                    if isinstance(item, dict)
                    and isinstance(item.get("provenance"), dict)
                    and item["provenance"].get("featureId") in {"voice-speak", "voxcpm-tts"}
                    else "unclassified"
                ),
            )
            self.send_json({
                "schema_version": "castle.local-history-adoption.response.v1",
                "ok": True,
                "asset": adopted,
                "authority": {
                    "owner_scoped": True,
                    "history_membership_checked": True,
                    "file_integrity_checked": True,
                    "provider_transport_performed": False,
                    "credits_spent": 0,
                },
            })
        except RequestTooLarge:
            self._send_provider_failure(413, "request_too_large", "Local history adoption request is too large.")
        except (RequestBodyError, ValueError):
            self._send_provider_failure(422, "adoption_invalid", "Local history output could not be adopted safely.")
        except (FileNotFoundError, KeyError):
            self._send_provider_failure(404, "not_found", "Local history output was not found.")
        except Exception:
            self._send_provider_failure(409, "adoption_conflict", "Local history output conflicts with canonical media state.")

    def api_dance_library(self, query: str) -> None:
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            character_type = str(
                (params.get("character_type") or params.get("characterType") or [""])[0]
            )
            payload = list_dance_library(
                character_type=character_type,
                manage=False,
                user=self.current_user(),
            )
            self.send_json(
                {"ok": True, **payload},
                cache_control="private, max-age=30",
            )
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_library_manage(self) -> None:
        try:
            payload = list_dance_library(manage=True, user=self.current_user())
            self.send_json({"ok": True, **payload}, cache_control="no-store")
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_library_rescan(self) -> None:
        try:
            catalog = rescan_dance_library_catalog()
            status_counts: dict[str, int] = {}
            for record in (catalog.get("templates") or {}).values():
                if not isinstance(record, dict):
                    continue
                status = str(record.get("status") or "pending")
                status_counts[status] = status_counts.get(status, 0) + 1
            self.send_json({
                "ok": True,
                "revision": int(catalog.get("revision") or 0),
                "templateCount": len(catalog.get("templates") or {}),
                "statusCounts": status_counts,
            })
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_library_update(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            catalog = update_dance_library(body)
            self.send_json({
                "ok": True,
                "revision": int(catalog.get("revision") or 0),
            })
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def _dance_library_request_record(self, template_id: str) -> dict | None:
        try:
            _catalog, record = resolve_dance_library_template(template_id)
        except ValueError as error:
            self.send_error_json(error, 404)
            return None
        if not user_can_access_dance_library_template(self.current_user(), record):
            self.send_json({"ok": False, "error": "這個舞步模板尚未上架"}, 403)
            return None
        return record

    def api_dance_library_media(self, path: str) -> None:
        parts = [urllib.parse.unquote(part) for part in path.split("/") if part]
        if (
            len(parts) != 4
            or parts[0] != "api"
            or parts[1] != "dance-library"
            or parts[3] not in {"storyboard", "preview", "file"}
        ):
            self.send_json({"ok": False, "error": "Dance template media not found"}, 404)
            return
        template_id = parts[2].lower()
        kind = "runtime" if parts[3] == "file" else parts[3]
        record = self._dance_library_request_record(template_id)
        if record is None:
            return
        try:
            target = prepare_dance_library_derivative(str(record.get("id") or ""), kind)
            # Re-check after a potentially long conversion so a concurrent hide
            # takes effect before any bytes are disclosed.
            _catalog, refreshed = resolve_dance_library_template(str(record.get("id") or ""))
            if not user_can_access_dance_library_template(self.current_user(), refreshed):
                self.send_json({"ok": False, "error": "這個舞步模板尚未上架"}, 403)
                return
            preferred = _dance_library_preferred_variant(refreshed)
            expected_version = _dance_library_media_version(preferred)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            supplied_version = str((query.get("v") or [""])[0])
            cache_control = (
                "private, max-age=31536000, immutable"
                if supplied_version and hmac.compare_digest(supplied_version, expected_version)
                else "private, max-age=60"
            )
            title = safe_filename(
                str(refreshed.get("title") or "dance-template"),
                ".jpg" if kind == "storyboard" else ".mp4",
            )
            if kind == "storyboard" and not title.lower().endswith(".jpg"):
                title = f"{title}.jpg"
            elif kind != "storyboard" and not title.lower().endswith(".mp4"):
                title = f"{title}.mp4"
            self.send_local_file(
                target,
                content_type="image/jpeg" if kind == "storyboard" else "video/mp4",
                inline_filename=title,
                cache_control=cache_control,
            )
        except DanceLibraryBusy as error:
            self.send_json({"ok": False, "error": str(error), "retryable": True}, 503)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_library_prepare(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            template_id = str(body.get("id") or "").strip().lower()
            record = self._dance_library_request_record(template_id)
            if record is None:
                return
            raw_kinds = body.get("kinds")
            kinds = raw_kinds if raw_kinds is not None else None
            result = prepare_dance_library(str(record.get("id") or ""), kinds)
            _catalog, refreshed = resolve_dance_library_template(str(record.get("id") or ""))
            if not user_can_access_dance_library_template(self.current_user(), refreshed):
                self.send_json({"ok": False, "error": "這個舞步模板尚未上架"}, 403)
                return
            self.send_json({"ok": True, **result})
        except DanceLibraryBusy as error:
            self.send_json({"ok": False, "error": str(error), "retryable": True}, 503)
        except ValueError as error:
            self.send_error_json(error, 400)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_library_import(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            template_id = str(body.get("id") or "").strip().lower()
            record = self._dance_library_request_record(template_id)
            if record is None:
                return
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "video"
            result = import_dance_library_template(
                str(record.get("id") or ""),
                slot,
                user=self.current_user(),
                enforce_access=True,
            )
            self.send_json({"ok": True, **result})
        except DanceLibraryBusy as error:
            self.send_json({"ok": False, "error": str(error), "retryable": True}, 503)
        except ValueError as error:
            self.send_error_json(error, 400)
        except PermissionError as error:
            self.send_error_json(error, 403)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_templates(self) -> None:
        try:
            self.send_json(
                {"ok": True, "templates": list_dance_templates()},
                cache_control=f"private, max-age={WARDROBE_CACHE_SECONDS}",
            )
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_template_file(self, path: str) -> None:
        try:
            parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
            if len(parts) != 4 or parts[0] != "api" or parts[1] != "dance-templates" or parts[3] not in {"file", "thumb"}:
                self.send_json({"ok": False, "error": "Dance template not found"}, 404)
                return
            template_id = str(parts[2] or "").lower()
            catalog_record = None
            try:
                _catalog, catalog_record = resolve_dance_library_template(template_id)
            except ValueError:
                try:
                    _catalog, catalog_record = resolve_legacy_dance_template_record(template_id)
                except ValueError:
                    catalog_record = None
            if catalog_record is not None:
                if not user_can_access_dance_library_template(self.current_user(), catalog_record):
                    self.send_json({"ok": False, "error": "這個舞步模板尚未上架"}, 403)
                    return
                if parts[3] == "thumb":
                    target = prepare_dance_library_derivative(
                        str(catalog_record.get("id") or ""),
                        "storyboard",
                    )
                else:
                    target = resolve_dance_template_path(template_id)
            else:
                target = resolve_dance_template_path(template_id)
                if parts[3] == "thumb":
                    thumb = dance_template_thumbnail_path(target)
                    if thumb is None:
                        self.send_json({"ok": False, "error": "Dance template thumbnail not available"}, 404)
                        return
                    target = thumb
            inline_filename = target.name
            if catalog_record is not None:
                extension = ".jpg" if parts[3] == "thumb" else ".mp4"
                inline_filename = safe_filename(
                    str(catalog_record.get("title") or "dance-template"),
                    extension,
                )
                if not inline_filename.lower().endswith(extension):
                    inline_filename = f"{inline_filename}{extension}"
            self.send_local_file(
                target,
                inline_filename=inline_filename,
                cache_control="private, max-age=60",
            )
        except DanceLibraryBusy as error:
            self.send_json({"ok": False, "error": str(error), "retryable": True}, 503)
        except ValueError as error:
            self.send_error_json(error, 404)
        except Exception as error:
            self.send_error_json(error, 500)

    def api_dance_template_import(self) -> None:
        try:
            body = self.read_json(MAX_TEXT_BODY)
            template_id = str(body.get("id") or "")
            try:
                _catalog, record = resolve_dance_library_template(template_id)
            except ValueError:
                record = None
            if record is not None and not user_can_access_dance_library_template(self.current_user(), record):
                self.send_json({"ok": False, "error": "這個舞步模板尚未上架"}, 403)
                return
            slot = re.sub(r"[^a-z0-9_-]+", "", str(body.get("slot") or "").lower())[:32] or "video"
            result = import_dance_template(
                template_id,
                slot,
                user=self.current_user(),
                enforce_access=True,
            )
            self.send_json({"ok": True, **result})
        except DanceLibraryBusy as error:
            self.send_json({"ok": False, "error": str(error), "retryable": True}, 503)
        except PermissionError as error:
            self.send_error_json(error, 403)
        except Exception as error:
            self.send_error_json(error, 400)

    def api_assist(self) -> None:
        try:
            body = self.read_json(MAX_UPLOAD_BODY)
            mode = str(body.get("mode") or "brainstorm").strip()
            if mode not in ("random", "brainstorm", "enhance"):
                raise ValueError("unknown assist mode")
            kind = str(body.get("kind") or "default").strip()
            text = str(body.get("text") or "").strip()
            references = body.get("references")
            result = generate_assist(mode, kind, text, references)
            self.send_json({"ok": True, **result})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_voices_list(self) -> None:
        try:
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            with _VOICE_LOCK:
                entries = load_voice_index()
            self.send_json({
                "ok": True,
                "voices": [
                    voice_public(entry, user)
                    for entry in entries
                    if user_can_access_voice_entry(user, entry, service_token)
                ],
            })
        except Exception as error:
            self.send_error_json(error, 500)

    def api_personas(self) -> None:
        # Proxy the warm BreezyVoice daemon's persona list. Returns an empty list
        # (not an error) if the daemon is down, so the panel degrades gracefully.
        if not BREEZY_URL:
            self.send_json({"ok": True, "personas": []})
            return
        try:
            req = urllib.request.Request(f"{BREEZY_URL}/voices", method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read() or b"{}")
            user = self.current_user()
            service_token = self.is_orchestrator_service_authenticated()
            with _CHARACTER_LOCK:
                allowed_personas = {
                    str((item.get("voice") or {}).get("persona") or "").strip()
                    for item in load_character_index()
                    if isinstance(item.get("voice"), dict)
                    and user_can_generate_character(user, item, service_token)
                }
            personas = [
                item
                for item in (data.get("voices") if isinstance(data.get("voices"), list) else [])
                if isinstance(item, dict)
                and str(item.get("id") or item.get("name") or "").strip() in allowed_personas
            ]
            self.send_json({"ok": True, "personas": personas})
        except Exception:
            self.send_json({"ok": True, "personas": [], "daemon": False})

    def api_voices_upload(self) -> None:
        try:
            user = self.current_user()
            body = self.read_json(MAX_UPLOAD_BODY)
            file_info = body.get("file") or {}
            raw, content_type = decode_data_url(str(file_info.get("dataUrl") or ""), MAX_VOICE_DECODED)
            original = str(file_info.get("name") or "voice")
            # The browser-supplied filename extension is the reliable signal;
            # mimetypes.guess_extension is patchy for audio (None for wav/m4a,
            # ".weba" for webm), so only use it as a fallback.
            suffix = Path(original).suffix.lower()
            guessed = (mimetypes.guess_extension(content_type) or "").lower()
            guessed = {".mpga": ".mp3", ".weba": ".webm"}.get(guessed, guessed)
            ext = suffix if suffix in VOICE_ALLOWED_EXT else guessed
            if ext not in VOICE_ALLOWED_EXT:
                raise ValueError("不支援的音訊格式（支援 mp3/wav/m4a/ogg/flac）")
            display = (str(body.get("name") or "") or Path(original).stem or "voice").strip()[:80]
            transcript_raw = str(body.get("transcript") or "").strip()
            transcript = prepare_voice_reference_text(transcript_raw) if transcript_raw else ""
            source = str(body.get("source") or "").strip()[:160]
            engine = str(body.get("engine") or "").strip()[:80]
            tool = str(body.get("tool") or "").strip()[:80]
            kind = str(body.get("kind") or "reference").strip()[:80] or "reference"
            settings_meta = body.get("settings")
            voice_id = secrets.token_hex(8)
            filename = f"{voice_id}{ext}"
            entry = {
                "id": voice_id,
                "name": display,
                "filename": filename,
                "type": content_type,
                "size": len(raw),
                "created": int(time.time()),
                "kind": kind,
                "ownerEmail": normalize_email(str((user or {}).get("email") or "")),
            }
            if transcript:
                entry["transcript"] = transcript
            if source:
                entry["source"] = source
            if engine:
                entry["engine"] = engine
            if tool:
                entry["tool"] = tool
            if isinstance(settings_meta, dict):
                entry["settings"] = settings_meta
            with _VOICE_LOCK:
                VOICE_LIBRARY.mkdir(parents=True, exist_ok=True)
                (VOICE_LIBRARY / filename).write_bytes(raw)
                entries = load_voice_index()
                entries.insert(0, entry)
                save_voice_index(entries)
            self.send_json({"ok": True, "voice": voice_public(entry, user)})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_voices_import_history(self) -> None:
        try:
            user = self.current_user()
            (filename, subfolder, view_type), body = self._view_parts_from_body()
            target = resolve_view_path(filename, subfolder, view_type)
            if target is None or not target.is_file():
                raise ValueError("找不到這個輸出檔")
            if GALLERY_MEDIA_KINDS.get(target.suffix.lower()) != "audio":
                raise ValueError("只有音訊輸出可以加入聲音庫")
            ext = target.suffix.lower()
            if ext not in VOICE_ALLOWED_EXT:
                raise ValueError("不支援的音訊格式（支援 mp3/wav/m4a/ogg/flac）")
            size = target.stat().st_size
            if size > MAX_FETCH_BYTES:
                raise ValueError("音訊檔太大，請先裁切後再加入聲音庫")

            display = (str(body.get("name") or "") or target.stem or "voice").strip()[:80]
            transcript_raw = str(body.get("transcript") or "").strip()
            transcript = prepare_voice_reference_text(transcript_raw) if transcript_raw else ""
            prompt_id = str(body.get("promptId") or "").strip()
            workflow = str(body.get("workflow") or "").strip()
            feature = str(body.get("feature") or "").strip()
            detail = None
            if prompt_id.startswith("local-"):
                detail = direct_history_detail(prompt_id)
            elif prompt_id and prompt_id != "disk" and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", prompt_id):
                try:
                    history = request_json(f"/history/{urllib.parse.quote(prompt_id)}")
                    item = history.get(prompt_id)
                    if item:
                        detail = history_prompt_detail(prompt_id, item)
                except Exception:
                    detail = None
            if detail:
                workflow = workflow or str(detail.get("workflow") or "")
                feature = feature or str(detail.get("feature") or "")

            source = str(body.get("source") or feature or workflow or "generated output").strip()[:160]
            voice_id = secrets.token_hex(8)
            library_filename = f"{voice_id}{ext}"
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            settings_meta = {
                "promptId": prompt_id,
                "workflow": workflow,
                "feature": feature,
                "original": {"filename": filename, "subfolder": subfolder, "type": view_type},
            }
            if detail:
                settings_meta["nodes"] = detail.get("nodes", [])
                settings_meta["outputs"] = detail.get("outputs", [])
                settings_meta["status"] = detail.get("status", "")
            entry = {
                "id": voice_id,
                "name": display,
                "filename": library_filename,
                "type": content_type,
                "size": size,
                "created": int(time.time()),
                "kind": "generated",
                "source": source,
                "engine": feature,
                "tool": workflow,
                "settings": settings_meta,
                "ownerEmail": normalize_email(str((user or {}).get("email") or "")),
            }
            if transcript:
                entry["transcript"] = transcript
            with _VOICE_LOCK:
                VOICE_LIBRARY.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, VOICE_LIBRARY / library_filename)
                entries = load_voice_index()
                entries.insert(0, entry)
                save_voice_index(entries)
            self.send_json({"ok": True, "voice": voice_public(entry, user)})
        except Exception as error:
            self.send_error_json(error, 400)

    def api_voices_delete(self) -> None:
        try:
            user = self.current_user()
            voice_id = str(self.read_json().get("id") or "")
            with _VOICE_LOCK:
                entries = load_voice_index()
                target = next((e for e in entries if e["id"] == voice_id), None)
                if target is None:
                    raise ValueError("找不到這個聲音")
                if not user_can_manage_voice_entry(user, target):
                    raise PermissionError("沒有權限刪除這個聲音")
                save_voice_index([e for e in entries if e["id"] != voice_id])
                try:
                    (VOICE_LIBRARY / target["filename"]).unlink()
                except OSError:
                    pass
            self.send_json({"ok": True})
        except PermissionError as error:
            self.send_error_json(error, 403)
        except Exception as error:
            self.send_error_json(error, 400)

    def api_voices_file(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        voice_id = (params.get("id") or [""])[0]
        with _VOICE_LOCK:
            entries = load_voice_index()
            entry = next((e for e in entries if e["id"] == voice_id), None)
        if entry is None:
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        if not user_can_access_voice_entry(
            self.current_user(),
            entry,
            self.is_orchestrator_service_authenticated(),
        ):
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        path = (VOICE_LIBRARY / entry["filename"]).resolve()
        if not path.is_relative_to(VOICE_LIBRARY.resolve()) or not path.is_file():
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        self.send_local_file(
            path,
            content_type=entry.get("type") or mimetypes.guess_type(path.name)[0],
            inline_filename=entry.get("filename") or path.name,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("CASTLE_COMFY_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CASTLE_COMFY_WEB_PORT", "8190")))
    parser.add_argument("--clipmode-photoshoot-worker", default="")
    args = parser.parse_args()
    if args.clipmode_photoshoot_worker:
        try:
            run_clipmode_photoshoot_worker(args.clipmode_photoshoot_worker)
        except Exception as error:
            print(f"角色身份寫真 worker 失敗：{error}", file=sys.stderr, flush=True)
            raise SystemExit(1) from error
        return
    server = CastleThreadingHTTPServer((args.host, args.port), Handler)
    library_service.start_indexer(library_server_context())
    try:
        castle_orchestrator.initialize()
        castle_orchestrator.start_worker_once()
    except Exception as error:
        print(f"Castle orchestrator startup failed: {error}", flush=True)
    print(f"Castle Comfy workflow web listening on http://{args.host}:{args.port}")
    print(f"Proxying ComfyUI at {DEFAULT_COMFY}")
    server.serve_forever()


if __name__ == "__main__":
    main()
