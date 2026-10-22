#!/usr/bin/env python3
"""Build, qualify, and optionally run Castle's pinned native MiniMax H3 T2V.

The public command is dry-run by default.  ``--execute`` never submits a graph
directly: it writes an owner-only transient specification outside the
repository and starts an internal worker through Castle's shared GPU queue.
The worker holds that queue lock for its complete submit/wait/probe lifecycle.

Only the first qualification lane (stock FL2VA text-to-video) is accepted.
There are deliberately no image, video, audio, character, LoRA, attention, or
arbitrary model-path inputs in this runner.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
WEBUI_ROOT = REPO_ROOT / "webui"
if str(WEBUI_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBUI_ROOT))

if __package__ in {None, ""}:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from artifacts import (  # type: ignore[import-not-found]
        ArtifactError,
        ArtifactManifest,
        ArtifactSpec,
        artifact_destination,
        load_manifest,
        partial_path_for,
        select_family,
        verify_file,
    )
    from authorization import (  # type: ignore[import-not-found]
        default_authorization_record_path,
        evaluate_local_inference_authorization,
        manifest_scope_sha256,
    )
else:
    from .artifacts import (
        ArtifactError,
        ArtifactManifest,
        ArtifactSpec,
        artifact_destination,
        load_manifest,
        partial_path_for,
        select_family,
        verify_file,
    )
    from .authorization import (
        default_authorization_record_path,
        evaluate_local_inference_authorization,
        manifest_scope_sha256,
    )

import minimax_h3_native as native


DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/models.lock.json"
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_COMFY_DIR = Path(os.environ.get("CASTLE_H3_COMFY_DIR", os.environ.get("COMFY_HOME", Path.home() / "comfyui"))).expanduser()
DEFAULT_STATE_ROOT = (
    Path.home() / ".local/share/web-comfyui/minimax-h3-native-runs"
)
DEFAULT_GPU_QUEUE_SCRIPT = Path(
    os.environ.get("CASTLE_GPU_QUEUE_SCRIPT", Path.home() / ".local/share/web-comfyui/integrations/voice/gpu_queue.py")
)
DEFAULT_AUTHORIZATION_RECORD = default_authorization_record_path()

SCHEMA_VERSION = 1
WORKER_SPEC_SCHEMA_VERSION = 2
MAX_SPEC_BYTES = 64 * 1024
MAX_HTTP_BYTES = 32 * 1024 * 1024
COMFY_SYSTEMD_SERVICE = "comfyui.service"
SYSTEMCTL_BIN = Path("/usr/bin/systemctl")
MAX_PROMPT_FILE_BYTES = native.MAX_PROMPT_CHARS * 4 + 4
MAX_COMFY_VERSION_BYTES = 16 * 1024
MIN_COMFY_VERSION = (0, 30, 0)
MIN_MEM_AVAILABLE_BYTES = 32 * 1024**3
MEMORY_HEADROOM_WAIT_SECONDS = 8.0
MEMORY_HEADROOM_POLL_SECONDS = 1.0
PRE_SUBMIT_SERVICE_RECYCLE_TIMEOUT_SECONDS = 45.0
GPU_MIN_FREE_MIB = 26_000
GPU_MAX_UTIL_PERCENT = 20
GPU_QUEUE_INTERVAL_SECONDS = 20
DEFAULT_TIMEOUT_SECONDS = 7_200
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 2.0
TERMINATION_GRACE_SECONDS = 15.0
SERVICE_RECYCLE_TIMEOUT_SECONDS = 3 * 60.0
SERVICE_RECYCLE_POLL_SECONDS = 2.0
SERVICE_RECYCLE_STABILITY_SECONDS = 2.0
SERVICE_RECYCLE_PRECHECK_SECONDS = 1.0
WORKER_CANCEL_GRACE_SECONDS = 2 * SERVICE_RECYCLE_TIMEOUT_SECONDS + 60.0

COMFY_HISTORY_NONTERMINAL_STATUSES = frozenset(
    {"pending", "queued", "running", "processing"}
)
COMFY_HISTORY_FAILURE_MESSAGES = frozenset(
    {
        "execution_error",
        "execution_interrupted",
        "execution_cancelled",
        "execution_canceled",
    }
)

ORIENTATIONS: Mapping[str, tuple[int, int]] = {
    "landscape": (864, 480),
    "portrait": (480, 864),
    "square": (640, 640),
}
SAFE_RUN_ID = re.compile(r"h3-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
SAFE_PROMPT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
OUTPUT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.mp4")
COMFY_VERSION_ASSIGNMENT = re.compile(
    r"^\s*__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$",
    re.MULTILINE,
)
COMFY_VERSION_VALUE = re.compile(
    r"([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})(?:[-+][0-9A-Za-z.-]+)?"
)


class RunnerError(RuntimeError):
    """A stable error whose code and message are safe to print or receipt."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise RunnerError(code, message)


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    mem_available_bytes: int
    swap_total_bytes: int
    swap_free_bytes: int

    @property
    def swap_used_ratio(self) -> float:
        if self.swap_total_bytes == 0:
            return 0.0
        return (self.swap_total_bytes - self.swap_free_bytes) / self.swap_total_bytes


@dataclass(frozen=True, slots=True)
class GpuSnapshot:
    free_mib: int
    utilization_percent: int


@dataclass(frozen=True, slots=True)
class ComfyServiceState:
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int
    invocation_id: str
    kill_mode: str


@dataclass(frozen=True, slots=True)
class OutputBinding:
    path: Path
    filename: str
    subfolder: str
    output_type: str


_INTERRUPTED = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_epoch(value: object) -> float | None:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ) is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return None


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise RunnerError("serialization_failed", "Runner data cannot be serialized.") from error


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()


def _worker_spec_hmac(payload: Mapping[str, object], token: str) -> str:
    if not isinstance(token, str) or not token.isascii() or len(token) != 64:
        _fail("worker_token_invalid", "Internal GPU-queue worker token is invalid.")
    unsigned = {key: value for key, value in payload.items() if key != "worker_hmac_sha256"}
    return hmac.new(
        token.encode("ascii", "strict"),
        _canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()


def _safe_json_loads(raw: bytes, *, code: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RunnerError(code, "Runner received invalid JSON.") from error


def _normalize_outside_repository(value: str | os.PathLike[str], *, code: str) -> Path:
    raw = os.fspath(Path(value).expanduser())
    if not raw or "\x00" in raw:
        _fail(code, "Path is invalid.")
    lexical = Path(os.path.abspath(raw))
    try:
        # ``strict=False`` still resolves every existing symlink component.
        # This prevents an apparently external state path from reaching back
        # into the checkout through (for example) /tmp/castle-link.
        path = lexical.resolve(strict=False)
        repository = REPO_ROOT.resolve(strict=True)
    except OSError as error:
        raise RunnerError(code, "Path is invalid.") from error
    try:
        inside = os.path.commonpath((str(path), str(repository))) == str(repository)
    except ValueError:
        inside = False
    if inside:
        _fail(code, "Runtime state must remain outside the repository.")
    return path


def _ensure_private_directory(path_value: str | os.PathLike[str]) -> Path:
    path = _normalize_outside_repository(path_value, code="state_path_invalid")
    parts = path.parts
    if not parts or parts[0] != os.path.sep or len(parts) == 1:
        _fail("state_path_invalid", "Runtime state directory is invalid.")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current_fd = os.open(os.path.sep, directory_flags)
    except OSError as error:
        raise RunnerError("state_directory_unavailable", "Runtime state directory is unavailable.") from error
    try:
        for index, name in enumerate(parts[1:], start=1):
            if name in {"", ".", ".."}:
                _fail("state_path_invalid", "Runtime state directory is invalid.")
            final = index == len(parts) - 1
            try:
                next_fd = os.open(name, directory_flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=current_fd)
                    next_fd = os.open(name, directory_flags, dir_fd=current_fd)
                except OSError as error:
                    raise RunnerError(
                        "state_directory_unavailable",
                        "Runtime state directory is unavailable.",
                    ) from error
            except OSError as error:
                raise RunnerError(
                    "state_directory_unsafe",
                    "Runtime state directory contains an unsafe component.",
                ) from error
            os.close(current_fd)
            current_fd = next_fd
            if final:
                info = os.fstat(current_fd)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                    _fail("state_directory_unsafe", "Runtime state directory is unsafe.")
                try:
                    os.fchmod(current_fd, 0o700)
                    info = os.fstat(current_fd)
                except OSError as error:
                    raise RunnerError(
                        "state_directory_unsafe",
                        "Runtime state directory is unsafe.",
                    ) from error
                if stat.S_IMODE(info.st_mode) != 0o700:
                    _fail("state_directory_unsafe", "Runtime state directory is not owner-only.")
    finally:
        os.close(current_fd)
    try:
        if path.resolve(strict=True) != path:
            _fail("state_directory_unsafe", "Runtime state directory changed during creation.")
    except OSError as error:
        raise RunnerError("state_directory_unsafe", "Runtime state directory is unsafe.") from error
    return path


def _write_exclusive_json(path: Path, payload: object) -> None:
    raw = _canonical_json(payload) + b"\n"
    if len(raw) > MAX_SPEC_BYTES:
        _fail("state_payload_too_large", "Runtime state payload is too large.")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise RunnerError("state_collision", "Runtime state already exists.") from error
    except OSError as error:
        raise RunnerError("state_write_failed", "Runtime state could not be created.") from error
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(raw):
            offset += os.write(fd, raw[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_owner_file(path: Path, *, max_bytes: int, code: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise RunnerError(code, "Owner-only state could not be opened.") from error
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            _fail(code, "Owner-only state is unsafe.")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            raise RunnerError(code, "Owner-only state is currently locked.") from error
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        raw = b"".join(chunks)
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size == len(raw)
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
        )
        if not stable:
            _fail(code, "Owner-only state changed while being read.")
        return raw
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _read_prompt_file(path_value: str) -> str:
    path = Path(path_value).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise RunnerError("prompt_file_unreadable", "Prompt file could not be opened.") from error
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PROMPT_FILE_BYTES:
            _fail("prompt_file_invalid", "Prompt file is not a small regular file.")
        chunks: list[bytes] = []
        remaining = MAX_PROMPT_FILE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        raw = b"".join(chunks)
        if (
            len(raw) != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            _fail("prompt_file_changed", "Prompt file changed while being read.")
        try:
            return raw.decode("utf-8", "strict")
        except UnicodeError as error:
            raise RunnerError("prompt_file_invalid", "Prompt file must be UTF-8 text.") from error
    finally:
        os.close(fd)


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"h3-{stamp}-{secrets.token_hex(6)}"


def build_t2v_request(
    *,
    prompt: str,
    orientation: str,
    profile: str,
    seed: int,
    run_id: str,
) -> tuple[dict[str, object], native.PromptGraph]:
    if orientation not in ORIENTATIONS:
        _fail("orientation_invalid", "Orientation is not supported.")
    if not SAFE_RUN_ID.fullmatch(run_id):
        _fail("run_id_invalid", "Run identifier is invalid.")
    request: dict[str, object] = {
        "mode": "t2v",
        "prompt": prompt,
        "resolution": ORIENTATIONS[orientation],
        "profile": profile,
        "seed": seed,
        "output_prefix": f"Castle/minimax-h3/t2v/{run_id}",
    }
    try:
        graph = native.build_prompt(request)
    except native.MiniMaxH3PromptError as error:
        raise RunnerError(error.code, str(error)) from error
    forbidden = {"LoadImage", "LoadVideo", "LoadAudio", "MiniMaxH3ReferenceToVideo"}
    classes = {
        str(node.get("class_type") or "")
        for node in graph.values()
        if isinstance(node, dict)
    }
    if classes & forbidden or graph.get("1", {}).get("inputs", {}).get("unet_name") != native.FL2VA_MODEL:
        _fail("graph_lane_violation", "Runner graph left the qualified T2V lane.")
    # Hash and persist the exact text embedded in the graph, not an equivalent
    # caller spelling with leading/trailing whitespace.
    graph_prompt = graph.get("5", {}).get("inputs", {}).get("prompt")
    if not isinstance(graph_prompt, str):
        _fail("graph_lane_violation", "Runner graph has no canonical T2V prompt.")
    request["prompt"] = graph_prompt
    return request, graph


def sanitized_graph_summary(
    *,
    request: Mapping[str, object],
    graph: native.PromptGraph,
    run_id: str,
    action: str = "dry_run",
) -> dict[str, object]:
    prompt = str(request["prompt"])
    width, height = request["resolution"]  # type: ignore[misc]
    frames = native.DURATION_PROFILES[str(request["profile"])]
    classes: dict[str, int] = {}
    for node in graph.values():
        class_type = str(node.get("class_type") or "")
        classes[class_type] = classes.get(class_type, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "run_id": run_id,
        "mode": "t2v",
        "profile": request["profile"],
        "resolution": {"width": width, "height": height},
        "frames": frames,
        "fps": native.STOCK_FPS,
        "expected_duration_seconds": round(frames / native.STOCK_FPS, 3),
        "seed": request["seed"],
        "prompt": {"sha256": _sha256_text(prompt), "characters": len(prompt)},
        "graph": {
            "sha256": _sha256_json(graph),
            "node_count": len(graph),
            "class_counts": dict(sorted(classes.items())),
        },
        "models": [
            native.FL2VA_MODEL,
            native.TEXT_ENCODER,
            native.VIDEO_VAE,
            native.AUDIO_VAE,
        ],
        "output": {
            "namespace": "Castle/minimax-h3/t2v",
            "container": "mp4",
            "video_codec": "h264",
            "audio_required": True,
        },
    }


def validate_loopback_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise RunnerError("comfy_url_invalid", "ComfyUI URL is invalid.") from error
    if (
        parsed.scheme != "http"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        _fail("comfy_url_invalid", "ComfyUI must be an uncredentialed loopback HTTP origin.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise RunnerError("comfy_not_loopback", "ComfyUI host must be a numeric loopback address.") from error
    if address != ipaddress.ip_address("127.0.0.1"):
        _fail("comfy_not_loopback", "ComfyUI must use Castle's fixed IPv4 loopback address.")
    if port is None:
        port = 80
    if port != 8188:
        _fail("comfy_url_invalid", "ComfyUI must use Castle's fixed loopback port.")
    return DEFAULT_COMFY_URL


def _proc_socket_address(encoded: str, *, ipv6: bool) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        if ipv6:
            raw = bytes.fromhex(encoded)
            if len(raw) != 16:
                raise ValueError("invalid IPv6 width")
            # Linux /proc renders every native-endian 32-bit word separately.
            network_order = b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
            return ipaddress.ip_address(network_order)
        return ipaddress.ip_address(struct.pack("<I", int(encoded, 16)))
    except (ValueError, struct.error) as error:
        raise RunnerError("listener_status_invalid", "Local socket status is invalid.") from error


def _listening_addresses(path: Path, *, port: int, ipv6: bool) -> list[ipaddress._BaseAddress]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise RunnerError("listener_status_unavailable", "Local socket status is unavailable.") from error
    addresses: list[ipaddress._BaseAddress] = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[3] != "0A":
            continue
        encoded, separator, encoded_port = fields[1].rpartition(":")
        if not separator:
            _fail("listener_status_invalid", "Local socket status is invalid.")
        try:
            local_port = int(encoded_port, 16)
        except ValueError as error:
            raise RunnerError("listener_status_invalid", "Local socket status is invalid.") from error
        if local_port == port:
            addresses.append(_proc_socket_address(encoded, ipv6=ipv6))
    return addresses


def verify_loopback_listener(
    base_url: str,
    *,
    tcp_path: Path = Path("/proc/net/tcp"),
    tcp6_path: Path = Path("/proc/net/tcp6"),
) -> tuple[str, ...]:
    """Prove that every listener on the selected ComfyUI port is loopback."""

    normalized = validate_loopback_url(base_url)
    parsed = urllib.parse.urlsplit(normalized)
    assert parsed.port is not None
    addresses = _listening_addresses(tcp_path, port=parsed.port, ipv6=False)
    addresses.extend(_listening_addresses(tcp6_path, port=parsed.port, ipv6=True))
    if not addresses:
        _fail("comfy_listener_missing", "No local ComfyUI listener was found.")
    if any(not address.is_loopback for address in addresses):
        _fail("comfy_listener_exposed", "ComfyUI is listening beyond loopback.")
    expected = ipaddress.ip_address(parsed.hostname)
    if expected not in addresses:
        _fail("comfy_listener_mismatch", "ComfyUI is not listening on the selected loopback address.")
    return tuple(sorted(str(address) for address in addresses))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _new_http_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout: float = 10.0,
    opener: urllib.request.OpenerDirector | None = None,
) -> Mapping[str, Any]:
    base = validate_loopback_url(base_url)
    if not path.startswith("/") or "//" in path or "\x00" in path:
        _fail("comfy_path_invalid", "ComfyUI request path is invalid.")
    body = None if payload is None else _canonical_json(payload)
    headers = {
        "Accept": "application/json",
        "User-Agent": "castle-minimax-h3-native-runner/1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    client = opener or _new_http_opener()
    try:
        with client.open(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_HTTP_BYTES:
                _fail("comfy_response_too_large", "ComfyUI response is too large.")
            raw = response.read(MAX_HTTP_BYTES + 1)
    except RunnerError:
        raise
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as error:
        raise RunnerError("comfy_request_failed", "ComfyUI request failed.") from error
    if len(raw) > MAX_HTTP_BYTES:
        _fail("comfy_response_too_large", "ComfyUI response is too large.")
    # ComfyUI's loopback-only ``POST /free`` endpoint intentionally returns an
    # empty 200 response.  Keep that exception narrowly bound to the exact
    # request; every other endpoint must still return a JSON object so
    # malformed responses fail closed.
    if (
        not raw
        and method == "POST"
        and path == "/free"
        and payload == {"unload_models": True, "free_memory": True}
    ):
        return {}
    parsed = _safe_json_loads(raw, code="comfy_response_invalid")
    if not isinstance(parsed, dict):
        _fail("comfy_response_invalid", "ComfyUI response must be an object.")
    return parsed


def memory_cleanup_not_attempted() -> dict[str, object]:
    return {
        "attempted": False,
        "status": "not_attempted",
        "error_code": None,
        "unload_models": True,
        "free_memory": True,
    }


def read_comfy_service_state(
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ComfyServiceState:
    """Read the exact systemd user-unit state used by Castle's ComfyUI."""

    command = [
        str(SYSTEMCTL_BIN),
        "--user",
        "show",
        COMFY_SYSTEMD_SERVICE,
        "--property=LoadState",
        "--property=ActiveState",
        "--property=SubState",
        "--property=MainPID",
        "--property=InvocationID",
        "--property=KillMode",
        "--no-pager",
    ]
    try:
        completed = run_command(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerError(
            "comfy_service_state_unavailable",
            "ComfyUI service state is unavailable.",
        ) from error
    if completed.returncode != 0:
        _fail(
            "comfy_service_state_unavailable",
            "ComfyUI service state is unavailable.",
        )
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            _fail(
                "comfy_service_state_invalid",
                "ComfyUI service state is invalid.",
            )
        values[key] = value
    if set(values) != {
        "LoadState",
        "ActiveState",
        "SubState",
        "MainPID",
        "InvocationID",
        "KillMode",
    }:
        _fail(
            "comfy_service_state_invalid",
            "ComfyUI service state is invalid.",
        )
    try:
        main_pid = int(values["MainPID"])
    except ValueError as error:
        raise RunnerError(
            "comfy_service_state_invalid",
            "ComfyUI service state is invalid.",
        ) from error
    if main_pid < 0:
        _fail(
            "comfy_service_state_invalid",
            "ComfyUI service state is invalid.",
        )
    return ComfyServiceState(
        load_state=values["LoadState"],
        active_state=values["ActiveState"],
        sub_state=values["SubState"],
        main_pid=main_pid,
        invocation_id=values["InvocationID"],
        kill_mode=values["KillMode"],
    )


def _require_running_comfy_service(state: ComfyServiceState) -> None:
    if (
        state.load_state != "loaded"
        or state.active_state != "active"
        or state.sub_state != "running"
        or state.main_pid <= 1
        or re.fullmatch(r"[0-9a-f]{32}", state.invocation_id) is None
        or state.kill_mode != "control-group"
    ):
        _fail(
            "comfy_service_not_running",
            "ComfyUI service is not in its expected running state.",
        )


def recycle_comfy_service(
    base_url: str,
    *,
    timeout_seconds: float = SERVICE_RECYCLE_TIMEOUT_SECONDS,
    poll_seconds: float = SERVICE_RECYCLE_POLL_SECONDS,
    stability_seconds: float = SERVICE_RECYCLE_STABILITY_SECONDS,
    precheck_seconds: float = SERVICE_RECYCLE_PRECHECK_SECONDS,
    http: Callable[..., Mapping[str, Any]] = request_json,
    listener_verifier: Callable[[str], tuple[str, ...]] = verify_loopback_listener,
    memory_reader: Callable[[], MemorySnapshot] | None = None,
    service_reader: Callable[[], ComfyServiceState] = read_comfy_service_state,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Safely free H3 memory by replacing the idle ComfyUI process.

    ComfyUI 0.32 acknowledges ``POST /free`` before it offloads models.  On
    this host, offloading the H3 diffusion model and text encoder into
    swap-exhausted RAM deterministically invokes the kernel OOM killer.  A
    systemd restart terminates the process instead, releasing its address
    space without first materializing every model on the CPU.
    """

    validate_loopback_url(base_url)
    if (
        timeout_seconds <= 0
        or poll_seconds <= 0
        or stability_seconds < 0
        or precheck_seconds < 0
    ):
        _fail(
            "comfy_service_recycle_invalid",
            "ComfyUI service recycle settings are invalid.",
        )
    read_memory = memory_reader or read_memory_snapshot

    # Never terminate a prompt owned by another caller.  The shared GPU flock
    # is still held by the worker while this check and restart take place.
    require_queue_idle(http(base_url, "/queue", timeout=5.0))
    sleeper(precheck_seconds)
    require_queue_idle(http(base_url, "/queue", timeout=5.0))
    before = service_reader()
    _require_running_comfy_service(before)

    try:
        completed = run_command(
            [
                str(SYSTEMCTL_BIN),
                "--user",
                "--no-block",
                "restart",
                COMFY_SYSTEMD_SERVICE,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerError(
            "comfy_service_recycle_failed",
            "ComfyUI service recycle failed.",
        ) from error
    if completed.returncode != 0:
        _fail(
            "comfy_service_recycle_failed",
            "ComfyUI service recycle failed.",
        )

    deadline = clock() + timeout_seconds
    stable_identity: tuple[int, str] | None = None
    stable_since: float | None = None
    last_available: int | None = None
    last_ready_state: ComfyServiceState | None = None
    replacement_ready_seen = False
    replacement_memory_low_seen = False
    while True:
        now = clock()
        try:
            after = service_reader()
            _require_running_comfy_service(after)
            if (
                after.main_pid == before.main_pid
                or after.invocation_id == before.invocation_id
            ):
                raise RunnerError(
                    "comfy_service_recycle_unsettled",
                    "ComfyUI service identity did not change.",
                )
            listener_verifier(base_url)
            stats = http(base_url, "/system_stats", timeout=5.0)
            system = stats.get("system")
            if not isinstance(system, Mapping):
                _fail(
                    "comfy_service_recycle_unsettled",
                    "ComfyUI replacement service is not ready.",
                )
            _parse_comfy_version(system.get("comfyui_version"))
            require_queue_idle(http(base_url, "/queue", timeout=5.0))
            replacement_ready_seen = True
            snapshot = read_memory()
            last_ready_state = after
            last_available = snapshot.mem_available_bytes
            if snapshot.mem_available_bytes < MIN_MEM_AVAILABLE_BYTES:
                replacement_memory_low_seen = True
                _fail(
                    "comfy_service_recycle_memory_unsettled",
                    "ComfyUI restarted but available RAM did not recover.",
                )
        except (RunnerError, OSError, ValueError):
            stable_identity = None
            stable_since = None
        else:
            last_available = snapshot.mem_available_bytes
            identity = (after.main_pid, after.invocation_id)
            if stable_identity != identity:
                stable_identity = identity
                stable_since = now
            elif stable_since is not None and now - stable_since >= stability_seconds:
                return {
                    "attempted": True,
                    "status": "service_recycled",
                    "error_code": None,
                    "unload_models": True,
                    "free_memory": True,
                    "method": "systemd_service_restart",
                    "service": COMFY_SYSTEMD_SERVICE,
                    "queue_idle_before": True,
                    "previous_main_pid": before.main_pid,
                    "previous_invocation_id": before.invocation_id,
                    "current_main_pid": after.main_pid,
                    "current_invocation_id": after.invocation_id,
                    "loopback_ready_after": True,
                    "queue_idle_after": True,
                    "memory_available_bytes_after": last_available,
                    "minimum_memory_available_bytes": MIN_MEM_AVAILABLE_BYTES,
                }
        remaining = deadline - clock()
        if remaining <= 0:
            if (
                replacement_ready_seen
                and replacement_memory_low_seen
                and last_ready_state is not None
                and last_available is not None
                and last_available < MIN_MEM_AVAILABLE_BYTES
            ):
                return {
                    "attempted": True,
                    "status": "failed",
                    "error_code": "comfy_service_recycle_memory_unsettled",
                    "unload_models": True,
                    "free_memory": True,
                    "method": "systemd_service_restart",
                    "service": COMFY_SYSTEMD_SERVICE,
                    "queue_idle_before": True,
                    "previous_main_pid": before.main_pid,
                    "previous_invocation_id": before.invocation_id,
                    "current_main_pid": last_ready_state.main_pid,
                    "current_invocation_id": last_ready_state.invocation_id,
                    "loopback_ready_after": True,
                    "queue_idle_after": True,
                    "memory_available_bytes_after": last_available,
                    "minimum_memory_available_bytes": MIN_MEM_AVAILABLE_BYTES,
                }
            _fail(
                "comfy_service_recycle_unsettled",
                "ComfyUI service recycle did not become stably ready.",
            )
        sleeper(min(poll_seconds, remaining))


def _failed_service_recycle_evidence(error_code: str) -> dict[str, object]:
    return {
        "attempted": True,
        "status": "failed",
        "error_code": error_code,
        "unload_models": True,
        "free_memory": True,
        "method": "systemd_service_restart",
        "service": COMFY_SYSTEMD_SERVICE,
        "queue_idle_before": False,
        "previous_main_pid": None,
        "previous_invocation_id": None,
        "current_main_pid": None,
        "current_invocation_id": None,
        "loopback_ready_after": False,
        "queue_idle_after": False,
        "memory_available_bytes_after": None,
        "minimum_memory_available_bytes": MIN_MEM_AVAILABLE_BYTES,
    }


def release_comfy_memory(
    base_url: str,
    *,
    requester: Callable[[str], None] | None = None,
    recovery_waiter: Callable[[str], None] | None = None,
    service_recycler: Callable[[str], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Return receipt-safe cleanup evidence without masking a run outcome.

    The production path performs a controlled systemd recycle.  ``requester``
    remains only as a backward-compatible, side-effect-free injection seam for
    tests; production must never call ComfyUI 0.32's asynchronous ``/free``.
    """

    if requester is None:
        recycle = service_recycler or recycle_comfy_service
        try:
            evidence = dict(recycle(base_url))
        except RunnerError as error:
            return _failed_service_recycle_evidence(error.code)
        except BaseException:
            return _failed_service_recycle_evidence(
                "comfy_service_recycle_failed"
            )
        if not valid_memory_cleanup_evidence(evidence, attempted_required=True):
            return _failed_service_recycle_evidence(
                "comfy_service_recycle_evidence_invalid"
            )
        return evidence

    try:
        requester(base_url)
        if recovery_waiter is not None:
            recovery_waiter(base_url)
    except RunnerError as error:
        return {
            "attempted": True,
            "status": "failed",
            "error_code": error.code,
            "unload_models": True,
            "free_memory": True,
        }
    except BaseException:
        return {
            "attempted": True,
            "status": "failed",
            "error_code": "comfy_memory_cleanup_failed",
            "unload_models": True,
            "free_memory": True,
        }
    return {
        "attempted": True,
        "status": "accepted",
        "error_code": None,
        "unload_models": True,
        "free_memory": True,
    }


def valid_memory_cleanup_evidence(
    value: object,
    *,
    attempted_required: bool = False,
) -> bool:
    """Validate the exact non-secret cleanup receipt fragment."""

    if not isinstance(value, dict):
        return False
    legacy_keys = {
        "attempted",
        "status",
        "error_code",
        "unload_models",
        "free_memory",
    }
    recycle_keys = legacy_keys | {
        "method",
        "service",
        "queue_idle_before",
        "previous_main_pid",
        "previous_invocation_id",
        "current_main_pid",
        "current_invocation_id",
        "loopback_ready_after",
        "queue_idle_after",
        "memory_available_bytes_after",
        "minimum_memory_available_bytes",
    }
    keys = set(value)
    if keys != legacy_keys and keys != recycle_keys:
        return False
    if value.get("unload_models") is not True or value.get("free_memory") is not True:
        return False
    attempted = value.get("attempted")
    status = value.get("status")
    error_code = value.get("error_code")
    if keys == recycle_keys:
        if (
            attempted is not True
            or value.get("method") != "systemd_service_restart"
            or value.get("service") != COMFY_SYSTEMD_SERVICE
            or value.get("minimum_memory_available_bytes")
            != MIN_MEM_AVAILABLE_BYTES
        ):
            return False
        if status == "service_recycled":
            previous_pid = value.get("previous_main_pid")
            current_pid = value.get("current_main_pid")
            previous_invocation = value.get("previous_invocation_id")
            current_invocation = value.get("current_invocation_id")
            available = value.get("memory_available_bytes_after")
            return all(
                (
                    error_code is None,
                    type(previous_pid) is int and previous_pid > 1,
                    type(current_pid) is int and current_pid > 1,
                    previous_pid != current_pid,
                    isinstance(previous_invocation, str)
                    and re.fullmatch(r"[0-9a-f]{32}", previous_invocation)
                    is not None,
                    isinstance(current_invocation, str)
                    and re.fullmatch(r"[0-9a-f]{32}", current_invocation)
                    is not None,
                    previous_invocation != current_invocation,
                    value.get("queue_idle_before") is True,
                    value.get("loopback_ready_after") is True,
                    value.get("queue_idle_after") is True,
                    type(available) is int
                    and available >= MIN_MEM_AVAILABLE_BYTES,
                )
            )
        return (
            status == "failed"
            and isinstance(error_code, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code)
            is not None
        )
    if attempted is False:
        return (
            not attempted_required
            and status == "not_attempted"
            and error_code is None
        )
    if attempted is not True:
        return False
    if status == "accepted":
        return error_code is None
    return (
        status == "failed"
        and isinstance(error_code, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is not None
    )


def _artifact_snapshot(path: Path) -> tuple[int, int, int, int, int, int, int, int]:
    info = path.lstat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def require_no_incomplete_model_downloads(
    models_dir: Path,
    manifest: ArtifactManifest,
) -> None:
    """Reject any known H3 ``.partial`` across the complete pinned manifest.

    Qualification deliberately checks every locked family, not only the FL
    files used by the current graph.  This keeps a concurrent/incomplete Ref2VA
    transfer from competing with the qualification run for disk, RAM, or I/O.
    Unknown filenames are ignored; only exact manifest-adjacent paths count.
    """

    for artifact in manifest.artifacts:
        try:
            _root, destination = artifact_destination(models_dir, artifact.destination)
            partial_path_for(destination).lstat()
        except FileNotFoundError:
            continue
        except (ArtifactError, OSError) as error:
            raise RunnerError(
                "model_download_state_unavailable",
                "Pinned MiniMax H3 download state is unavailable.",
            ) from error
        _fail(
            "model_download_incomplete",
            "A pinned MiniMax H3 model download is active or incomplete.",
        )


def verify_pinned_models(
    models_dir: Path,
    manifest: ArtifactManifest,
    *,
    family: str = "fl",
    verify: Callable[[Path, ArtifactSpec], bool] = verify_file,
) -> tuple[ArtifactSpec, ...]:
    try:
        selected = select_family(manifest.artifacts, family)
    except ArtifactError as error:
        raise RunnerError(
            "models_not_verified", "Pinned MiniMax H3 model family is invalid."
        ) from error
    for artifact in selected:
        _root, destination = artifact_destination(models_dir, artifact.destination)
        try:
            before = _artifact_snapshot(destination)
        except OSError as error:
            raise RunnerError("models_not_verified", "A pinned MiniMax H3 model is missing.") from error
        if (
            stat.S_ISLNK(before[2])
            or not stat.S_ISREG(before[2])
            or before[3] != os.geteuid()
            or before[4] != 1
            or before[2] & 0o022
        ):
            _fail("models_not_verified", "A pinned MiniMax H3 model is unsafe.")
        try:
            if partial_path_for(destination).exists():
                _fail("models_not_verified", "A pinned MiniMax H3 model is still partial.")
        except OSError as error:
            raise RunnerError("models_not_verified", "Pinned model state is unavailable.") from error
        if not verify(destination, artifact):
            _fail("models_not_verified", "A pinned MiniMax H3 model failed size or SHA-256 verification.")
        try:
            after = _artifact_snapshot(destination)
        except OSError as error:
            raise RunnerError("models_not_verified", "A pinned MiniMax H3 model disappeared.") from error
        if before != after:
            _fail("models_changed", "A pinned MiniMax H3 model changed during verification.")
    return selected


def _parse_comfy_version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str):
        _fail("comfy_runtime_invalid", "ComfyUI runtime version is invalid.")
    match = COMFY_VERSION_VALUE.fullmatch(value)
    if match is None:
        _fail("comfy_runtime_invalid", "ComfyUI runtime version is invalid.")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _read_local_comfy_version(comfy_dir: Path) -> str:
    try:
        root = comfy_dir.resolve(strict=True)
        root_info = root.stat()
    except OSError as error:
        raise RunnerError(
            "comfy_runtime_unavailable", "Local ComfyUI runtime is unavailable."
        ) from error
    if not stat.S_ISDIR(root_info.st_mode):
        _fail("comfy_runtime_unavailable", "Local ComfyUI runtime is unavailable.")

    version_path = root / "comfyui_version.py"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(version_path, flags)
    except OSError as error:
        raise RunnerError(
            "comfy_runtime_unavailable", "Local ComfyUI version is unavailable."
        ) from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_COMFY_VERSION_BYTES
        ):
            _fail("comfy_runtime_invalid", "Local ComfyUI version file is invalid.")
        raw = b""
        while len(raw) <= MAX_COMFY_VERSION_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_COMFY_VERSION_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            _fail("comfy_runtime_changed", "Local ComfyUI version changed during preflight.")
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError as error:
        raise RunnerError(
            "comfy_runtime_invalid", "Local ComfyUI version file is invalid."
        ) from error
    matches = COMFY_VERSION_ASSIGNMENT.findall(text)
    if len(matches) != 1:
        _fail("comfy_runtime_invalid", "Local ComfyUI version file is invalid.")
    return matches[0]


def verify_comfy_runtime_contract(
    comfy_dir: Path,
    system_stats: Mapping[str, Any],
) -> str:
    """Require the documented local venv and the same supported live version."""

    local_version = _read_local_comfy_version(comfy_dir)
    local_tuple = _parse_comfy_version(local_version)
    if local_tuple < MIN_COMFY_VERSION:
        _fail("comfy_version_unsupported", "Local ComfyUI version is below the H3 minimum.")

    python_path = comfy_dir / ".venv/bin/python"
    try:
        resolved_python = python_path.resolve(strict=True)
        python_info = resolved_python.stat()
    except OSError as error:
        raise RunnerError(
            "comfy_runtime_unavailable", "ComfyUI virtual environment is unavailable."
        ) from error
    if not stat.S_ISREG(python_info.st_mode) or not os.access(resolved_python, os.X_OK):
        _fail("comfy_runtime_invalid", "ComfyUI virtual environment is invalid.")

    system = system_stats.get("system")
    live_version = system.get("comfyui_version") if isinstance(system, dict) else None
    live_tuple = _parse_comfy_version(live_version)
    if live_tuple < MIN_COMFY_VERSION:
        _fail("comfy_version_unsupported", "Live ComfyUI version is below the H3 minimum.")
    if live_version != local_version:
        _fail("comfy_runtime_mismatch", "Live ComfyUI does not match the audited local runtime.")
    return local_version


def verify_object_info(object_info: Mapping[str, Any], graph: native.PromptGraph) -> None:
    required_classes = {
        str(node.get("class_type") or "")
        for node in graph.values()
        if isinstance(node, dict)
    }
    missing = sorted(name for name in required_classes if name not in object_info)
    if missing:
        _fail("comfy_nodes_missing", "ComfyUI is missing a required native H3 node.")

    loader_fields = {
        "UNETLoader": "unet_name",
        "CLIPLoader": "clip_name",
        "VAELoader": "vae_name",
    }
    model_fields: set[tuple[str, str, str]] = set()
    for node in graph.values():
        if not isinstance(node, dict):
            _fail("comfy_graph_invalid", "Native H3 graph is invalid.")
        class_name = str(node.get("class_type") or "")
        field = loader_fields.get(class_name)
        if field is None:
            continue
        inputs = node.get("inputs")
        expected = inputs.get(field) if isinstance(inputs, dict) else None
        if not isinstance(expected, str) or not expected:
            _fail("comfy_graph_invalid", "Native H3 model binding is invalid.")
        model_fields.add((class_name, field, expected))
    if not model_fields:
        _fail("comfy_graph_invalid", "Native H3 graph has no model bindings.")
    for class_name, field, expected in sorted(model_fields):
        node = object_info.get(class_name)
        try:
            options = node["input"]["required"][field][0]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            _fail("comfy_object_info_invalid", "ComfyUI object metadata is invalid.")
        if not isinstance(options, list) or expected not in options:
            _fail("comfy_model_not_indexed", "ComfyUI has not indexed a pinned H3 model.")


def require_queue_idle(queue: Mapping[str, Any]) -> None:
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    if not isinstance(running, list) or not isinstance(pending, list):
        _fail("comfy_queue_invalid", "ComfyUI queue response is invalid.")
    if running or pending:
        _fail("comfy_queue_busy", "ComfyUI queue must be idle before H3 submission.")


def read_memory_snapshot(path: Path = Path("/proc/meminfo")) -> MemorySnapshot:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as error:
        raise RunnerError("memory_status_unavailable", "System memory status is unavailable.") from error
    values: dict[str, int] = {}
    for line in lines:
        name, separator, remainder = line.partition(":")
        if not separator:
            continue
        parts = remainder.strip().split()
        if not parts or not parts[0].isdigit() or (len(parts) > 1 and parts[1] != "kB"):
            continue
        values[name] = int(parts[0]) * 1024
    required = {"MemAvailable", "SwapTotal", "SwapFree"}
    if not required.issubset(values) or values["SwapFree"] > values["SwapTotal"]:
        _fail("memory_status_invalid", "System memory status is invalid.")
    return MemorySnapshot(
        mem_available_bytes=values["MemAvailable"],
        swap_total_bytes=values["SwapTotal"],
        swap_free_bytes=values["SwapFree"],
    )


def require_memory_headroom(snapshot: MemorySnapshot) -> None:
    if snapshot.mem_available_bytes < MIN_MEM_AVAILABLE_BYTES:
        _fail("memory_headroom_low", "Insufficient available RAM for pinned H3 qualification.")
    # Swap occupancy on this private workstation is historical state, not
    # usable H3 headroom: the kernel can leave cold pages in swap even after
    # their owning workload exits.  The shared GPU flock serializes large
    # producers, MemAvailable is rechecked immediately before submission, and
    # every H3 path now recycles the idle ComfyUI service after its output is
    # bound, probed, and hashed.  A full-but-stale swap therefore must not permanently
    # disable local generation when the live RAM threshold is satisfied.


def request_comfy_memory_release(
    base_url: str,
    *,
    http: Callable[..., Mapping[str, Any]] = request_json,
) -> None:
    """Ask an idle ComfyUI worker to unload models before H3 submission."""

    # This helper is called only by a worker that already owns Castle's shared
    # GPU flock.  Still prove the Comfy queue is empty immediately before the
    # global memory-release flag is set, so a direct Comfy caller cannot have
    # slipped work into the queue between preflight checks.
    require_queue_idle(http(base_url, "/queue", timeout=5.0))
    http(
        base_url,
        "/free",
        method="POST",
        payload={"unload_models": True, "free_memory": True},
        timeout=5.0,
    )


def wait_for_memory_headroom(
    *,
    timeout_seconds: float = MEMORY_HEADROOM_WAIT_SECONDS,
    base_url: str = DEFAULT_COMFY_URL,
    http: Callable[..., Mapping[str, Any]] = request_json,
    memory_reader: Callable[[], MemorySnapshot] = read_memory_snapshot,
    listener_verifier: Callable[[str], tuple[str, ...]] = verify_loopback_listener,
    service_recycler: Callable[..., Mapping[str, object]] | None = (
        recycle_comfy_service
    ),
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> MemorySnapshot:
    """Recover H3 RAM headroom under the shared GPU flock, or fail quickly.

    A healthy host returns immediately.  On low RAM, an idle ComfyUI receives
    its documented ``/free`` request and gets a short bounded window to unload
    cached models.  If that is insufficient, the already-established worker-
    side service recycler may replace the idle process.  This function is not
    called by the HTTP request thread and never waits for the old fixed
    five-minute timeout.
    """

    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        _fail("memory_headroom_wait_invalid", "RAM headroom wait is invalid.")
    normalized_base = validate_loopback_url(base_url)
    snapshot = memory_reader()
    try:
        require_memory_headroom(snapshot)
    except RunnerError as error:
        if error.code != "memory_headroom_low":
            raise
    else:
        return snapshot

    try:
        request_comfy_memory_release(normalized_base, http=http)
    except RunnerError as error:
        # A busy or malformed queue is an independent safety gate.  Preserve
        # that exact result and never recycle a process that may own work.
        if error.code in {"comfy_queue_busy", "comfy_queue_invalid"}:
            raise
        # A disappearing listener can be the OOM/restart edge of /free.  The
        # controlled recycler below is the only permitted recovery fallback.

    deadline = clock() + float(timeout_seconds)
    maximum_polls = max(
        1,
        int(math.ceil(float(timeout_seconds) / MEMORY_HEADROOM_POLL_SECONDS)),
    )
    for _attempt in range(maximum_polls):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleeper(min(MEMORY_HEADROOM_POLL_SECONDS, remaining))
        snapshot = memory_reader()
        try:
            require_memory_headroom(snapshot)
        except RunnerError as error:
            if error.code != "memory_headroom_low":
                raise
        else:
            return snapshot

    if service_recycler is not None:
        try:
            evidence = dict(
                service_recycler(
                    normalized_base,
                    timeout_seconds=PRE_SUBMIT_SERVICE_RECYCLE_TIMEOUT_SECONDS,
                    http=http,
                    listener_verifier=listener_verifier,
                    memory_reader=memory_reader,
                )
            )
        except RunnerError as error:
            if error.code in {"comfy_queue_busy", "comfy_queue_invalid"}:
                raise
        except Exception:
            pass
        else:
            if (
                valid_memory_cleanup_evidence(evidence, attempted_required=True)
                and evidence.get("status") == "service_recycled"
            ):
                snapshot = memory_reader()
                try:
                    require_memory_headroom(snapshot)
                except RunnerError as error:
                    if error.code != "memory_headroom_low":
                        raise
                else:
                    return snapshot

    _fail(
        "memory_recovery_failed",
        "本機可用記憶體仍不足；已嘗試安全釋放 ComfyUI 模型，但仍未達 H3 "
        "所需的 32 GiB。任務尚未送出，不會重複生成；請稍後再試。",
    )


def read_gpu_snapshot(
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> GpuSnapshot:
    try:
        completed = run_command(
            [
                "nvidia-smi",
                "--query-gpu=memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerError("gpu_status_unavailable", "GPU status is unavailable.") from error
    if completed.returncode != 0:
        _fail("gpu_status_unavailable", "GPU status is unavailable.")
    rows = [row.strip() for row in completed.stdout.splitlines() if row.strip()]
    if len(rows) != 1:
        _fail("gpu_status_invalid", "GPU status is invalid.")
    parts = [part.strip() for part in rows[0].split(",")]
    try:
        free_mib, utilization = (int(value) for value in parts)
    except (TypeError, ValueError) as error:
        raise RunnerError("gpu_status_invalid", "GPU status is invalid.") from error
    if free_mib < 0 or not 0 <= utilization <= 100:
        _fail("gpu_status_invalid", "GPU status is invalid.")
    return GpuSnapshot(free_mib=free_mib, utilization_percent=utilization)


def require_gpu_headroom(snapshot: GpuSnapshot) -> None:
    if snapshot.free_mib < GPU_MIN_FREE_MIB or snapshot.utilization_percent > GPU_MAX_UTIL_PERCENT:
        _fail("gpu_headroom_low", "RTX 5090 is not idle enough for pinned H3 qualification.")


def _is_cancelled(cancel_file: str | None) -> bool:
    global _INTERRUPTED
    if _INTERRUPTED:
        return True
    if not cancel_file:
        return False
    try:
        Path(cancel_file).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _check_deadline_or_cancel(*, deadline_epoch: float, cancel_file: str | None) -> None:
    if _is_cancelled(cancel_file):
        _fail("run_cancelled", "MiniMax H3 run was cancelled.")
    if time.time() >= deadline_epoch:
        _fail("run_timeout", "MiniMax H3 run reached its timeout.")


def _queue_prompt_ids(entries: object) -> set[str] | None:
    if not isinstance(entries, list):
        return None
    result: set[str] = set()
    for entry in entries:
        if not isinstance(entry, list) or len(entry) < 2:
            return None
        prompt_id = entry[1]
        if not isinstance(prompt_id, str) or SAFE_PROMPT_ID.fullmatch(prompt_id) is None:
            return None
        if prompt_id in result:
            return None
        result.add(prompt_id)
    return result


def wait_for_comfy_queue_idle(
    base_url: str,
    *,
    timeout_seconds: float | None = SERVICE_RECYCLE_TIMEOUT_SECONDS,
    http: Callable[..., Mapping[str, Any]] = request_json,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until ComfyUI proves that no running or pending prompt remains."""

    validate_loopback_url(base_url)
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        _fail(
            "comfy_queue_settle_invalid",
            "ComfyUI queue settle timeout is invalid.",
        )
    deadline = None if timeout_seconds is None else clock() + float(timeout_seconds)
    while True:
        try:
            queue = http(base_url, "/queue", timeout=5.0)
        except RunnerError as error:
            # After an owned prompt has been submitted, a transient loopback
            # outage is not proof that the prompt stopped.  The unbounded
            # lifecycle mode keeps the shared GPU flock until ComfyUI comes
            # back and proves its queue is empty.
            if deadline is None:
                sleeper(POLL_SECONDS)
                continue
            raise
        if not isinstance(queue, Mapping):
            _fail("comfy_queue_invalid", "ComfyUI queue response is invalid.")
        running = _queue_prompt_ids(queue.get("queue_running"))
        pending = _queue_prompt_ids(queue.get("queue_pending"))
        if running is None or pending is None or running.intersection(pending):
            if deadline is None:
                sleeper(POLL_SECONDS)
                continue
            _fail("comfy_queue_invalid", "ComfyUI queue response is invalid.")
        if not running and not pending:
            return
        if deadline is None:
            sleeper(POLL_SECONDS)
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            _fail(
                "comfy_queue_settle_timeout",
                "ComfyUI queue did not become idle after cancellation.",
            )
        sleeper(min(POLL_SECONDS, remaining))


def cancel_owned_prompt(
    base_url: str,
    prompt_id: str,
    *,
    http: Callable[..., Mapping[str, Any]] = request_json,
) -> str:
    """Cancel only when the queue proves this exact prompt is pending/running."""

    if SAFE_PROMPT_ID.fullmatch(prompt_id) is None:
        return "not_owned"
    try:
        queue = http(base_url, "/queue", timeout=5.0)
        running = _queue_prompt_ids(queue.get("queue_running"))
        pending = _queue_prompt_ids(queue.get("queue_pending"))
        if running is None or pending is None or (prompt_id in running and prompt_id in pending):
            return "queue_unverified"
        if prompt_id in pending:
            http(
                base_url,
                "/queue",
                method="POST",
                payload={"delete": [prompt_id]},
                timeout=5.0,
            )
            return "deleted"
        if prompt_id in running:
            http(
                base_url,
                "/interrupt",
                method="POST",
                payload={"prompt_id": prompt_id},
                timeout=5.0,
            )
            return "interrupted"
        return "not_found"
    except RunnerError:
        return "cancel_failed"


def submit_prompt(
    base_url: str,
    graph: native.PromptGraph,
    *,
    client_id: str,
    prompt_id: str,
    http: Callable[..., Mapping[str, Any]] = request_json,
) -> str:
    try:
        allocated = str(uuid.UUID(prompt_id))
    except (AttributeError, TypeError, ValueError) as error:
        raise RunnerError("prompt_id_invalid", "Allocated prompt identifier is invalid.") from error
    if allocated != prompt_id:
        _fail("prompt_id_invalid", "Allocated prompt identifier is invalid.")
    response = http(
        base_url,
        "/prompt",
        method="POST",
        payload={"prompt": graph, "client_id": client_id, "prompt_id": allocated},
        timeout=30.0,
    )
    returned_prompt_id = response.get("prompt_id")
    node_errors = response.get("node_errors", {})
    if (
        returned_prompt_id != allocated
        or not isinstance(node_errors, dict)
        or node_errors
    ):
        _fail("prompt_rejected", "ComfyUI rejected the native H3 graph.")
    return allocated


def wait_for_history(
    base_url: str,
    prompt_id: str,
    *,
    deadline_epoch: float,
    cancel_file: str | None,
    http: Callable[..., Mapping[str, Any]] = request_json,
    sleeper: Callable[[float], None] = time.sleep,
) -> Mapping[str, Any]:
    path = "/history/" + urllib.parse.quote(prompt_id, safe="")
    while True:
        _check_deadline_or_cancel(deadline_epoch=deadline_epoch, cancel_file=cancel_file)
        history = http(base_url, path, timeout=10.0)
        item = history.get(prompt_id)
        if item is not None:
            if not isinstance(item, dict):
                _fail("history_invalid", "ComfyUI history is invalid.")
            status_value = item.get("status")
            if not isinstance(status_value, dict):
                _fail("history_invalid", "ComfyUI history status is invalid.")
            completed = status_value.get("completed")
            status_str = status_value.get("status_str")
            messages = status_value.get("messages")
            if (
                not isinstance(completed, bool)
                or not isinstance(status_str, str)
                or not isinstance(messages, list)
            ):
                _fail("history_invalid", "ComfyUI history status is invalid.")
            message_types: set[str] = set()
            for message in messages:
                if (
                    not isinstance(message, (list, tuple))
                    or len(message) != 2
                    or not isinstance(message[0], str)
                    or not isinstance(message[1], dict)
                ):
                    _fail("history_invalid", "ComfyUI history messages are invalid.")
                message_types.add(message[0])
            if status_str == "error" or message_types & COMFY_HISTORY_FAILURE_MESSAGES:
                _fail("generation_failed", "ComfyUI reported an H3 execution failure.")
            if status_str == "success":
                if completed is not True:
                    _fail("history_invalid", "ComfyUI history completion state is invalid.")
                return item
            if status_str not in COMFY_HISTORY_NONTERMINAL_STATUSES or completed is not False:
                _fail("history_invalid", "ComfyUI history completion state is invalid.")
        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            _fail("run_timeout", "MiniMax H3 run reached its timeout.")
        sleeper(min(POLL_SECONDS, remaining))


def bind_history_output(
    history_item: Mapping[str, Any],
    *,
    run_id: str,
    output_root: Path,
) -> OutputBinding:
    outputs = history_item.get("outputs")
    if not isinstance(outputs, dict):
        _fail("output_metadata_invalid", "ComfyUI did not return output metadata.")
    save_node = outputs.get("14")
    if not isinstance(save_node, dict):
        _fail("output_metadata_invalid", "ComfyUI did not return the H3 SaveVideo output.")
    candidates = save_node.get("images")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
        _fail("output_metadata_invalid", "H3 must produce exactly one saved MP4.")
    metadata = candidates[0]
    filename = metadata.get("filename")
    subfolder = metadata.get("subfolder")
    output_type = metadata.get("type")
    expected_name = re.compile(re.escape(run_id) + r"_[0-9]{5}_\.mp4")
    if (
        not isinstance(filename, str)
        or OUTPUT_FILENAME.fullmatch(filename) is None
        or expected_name.fullmatch(filename) is None
        or subfolder != "Castle/minimax-h3/t2v"
        or output_type != "output"
    ):
        _fail("output_binding_failed", "H3 output metadata left its server-owned namespace.")
    try:
        root = output_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise RunnerError("output_root_unavailable", "ComfyUI output root is unavailable.") from error
    if not root.is_dir():
        _fail("output_root_unavailable", "ComfyUI output root is unavailable.")
    candidate = root.joinpath("Castle", "minimax-h3", "t2v", filename)
    try:
        info = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise RunnerError("output_missing", "H3 output file is missing.") from error
    try:
        contained = os.path.commonpath((str(resolved), str(root))) == str(root)
    except ValueError:
        contained = False
    if (
        not contained
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_size <= 0
    ):
        _fail("output_file_unsafe", "H3 output file is unsafe.")
    return OutputBinding(resolved, filename, subfolder, output_type)


def _fraction_rate(value: object) -> float:
    if not isinstance(value, str) or not value or value == "0/0":
        _fail("video_probe_invalid", "Video frame rate is invalid.")
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError) as error:
        raise RunnerError("video_probe_invalid", "Video frame rate is invalid.") from error
    if not math.isfinite(rate) or rate <= 0:
        _fail("video_probe_invalid", "Video frame rate is invalid.")
    return rate


def _stable_file_sha256(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise RunnerError("output_hash_failed", "H3 output could not be hashed.") from error
    try:
        before = os.fstat(fd)
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, 8 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            size != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            _fail("output_changed", "H3 output changed during hashing.")
        return size, digest.hexdigest()
    finally:
        os.close(fd)


def probe_output(
    path: Path,
    *,
    resolution: tuple[int, int],
    frames: int,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        _fail("ffprobe_unavailable", "ffprobe is required for H3 qualification.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise RunnerError("video_probe_failed", "H3 output could not be opened for probing.") from error
    try:
        before = os.fstat(fd)
        try:
            completed = run_command(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration",
                    "-of",
                    "json",
                    f"/proc/self/fd/{fd}",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=60,
                pass_fds=(fd,),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RunnerError("video_probe_failed", "ffprobe could not inspect H3 output.") from error
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if completed.returncode != 0 or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        _fail("video_probe_failed", "ffprobe rejected an unstable H3 output.")
    try:
        raw = json.loads(completed.stdout)
        streams = raw.get("streams")
        format_value = raw.get("format")
        duration = float(format_value.get("duration"))
        format_names = str(format_value.get("format_name") or "").split(",")
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RunnerError("video_probe_invalid", "ffprobe returned invalid metadata.") from error
    if not isinstance(streams, list) or not math.isfinite(duration) or duration <= 0:
        _fail("video_probe_invalid", "ffprobe returned invalid metadata.")
    video_streams = [row for row in streams if isinstance(row, dict) and row.get("codec_type") == "video"]
    audio_streams = [row for row in streams if isinstance(row, dict) and row.get("codec_type") == "audio"]
    if len(video_streams) != 1 or not audio_streams:
        _fail("video_streams_invalid", "H3 output must contain one video stream and audio.")
    video = video_streams[0]
    if video.get("codec_name") != "h264" or "mp4" not in format_names:
        _fail("video_codec_invalid", "H3 output must be H.264 in an MP4 container.")
    try:
        width = int(video.get("width"))
        height = int(video.get("height"))
    except (TypeError, ValueError) as error:
        raise RunnerError("video_dimensions_invalid", "H3 output dimensions are invalid.") from error
    if (width, height) != resolution:
        _fail("video_dimensions_invalid", "H3 output dimensions do not match the requested profile.")
    fps = _fraction_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if abs(fps - native.STOCK_FPS) > 0.02:
        _fail("video_fps_invalid", "H3 output frame rate is not the stock 24 fps.")
    expected_duration = frames / native.STOCK_FPS
    tolerance = max(0.35, expected_duration * 0.08)
    if abs(duration - expected_duration) > tolerance:
        _fail("video_duration_invalid", "H3 output duration does not match its frame profile.")
    audio_codec = str(audio_streams[0].get("codec_name") or "")
    if not audio_codec:
        _fail("video_audio_invalid", "H3 output audio codec is missing.")
    return {
        "container": "mp4",
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "video_codec": "h264",
        "audio_codec": audio_codec,
        "video_streams": 1,
        "audio_streams": len(audio_streams),
    }


def _receipt_path(state_root: Path, run_id: str) -> Path:
    receipts = _ensure_private_directory(state_root / "receipts")
    return receipts / f"{run_id}.json"


def write_receipt(state_root: Path, run_id: str, payload: Mapping[str, object]) -> Path:
    if SAFE_RUN_ID.fullmatch(run_id) is None:
        _fail("run_id_invalid", "Run identifier is invalid.")
    path = _receipt_path(state_root, run_id)
    exact = dict(payload)
    exact["schema_version"] = SCHEMA_VERSION
    exact["run_id"] = run_id
    _write_exclusive_json(path, exact)
    return path


def read_completed_receipt(state_root: Path, spec: Mapping[str, Any]) -> Mapping[str, Any]:
    run_id = str(spec.get("run_id") or "")
    if SAFE_RUN_ID.fullmatch(run_id) is None:
        _fail("receipt_invalid", "Runner receipt identifier is invalid.")
    path = _receipt_path(state_root, run_id)
    raw = _read_owner_file(path, max_bytes=MAX_SPEC_BYTES, code="receipt_invalid")
    payload = _safe_json_loads(raw, code="receipt_invalid")
    required = {
        "schema_version", "run_id", "mode", "status", "error_code", "created_at",
        "finished_at", "profile", "resolution", "frames", "fps", "seed",
        "prompt_sha256", "graph_sha256", "prompt_id", "authorization",
        "models_verified", "model_manifest", "output", "memory_cleanup",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        _fail("receipt_invalid", "Runner receipt schema is invalid.")
    request = spec.get("request") if isinstance(spec.get("request"), dict) else {}
    orientation = request.get("orientation")
    profile = request.get("profile")
    if orientation not in ORIENTATIONS or profile not in native.DURATION_PROFILES:
        _fail("receipt_invalid", "Runner receipt request contract is invalid.")
    resolution = ORIENTATIONS[str(orientation)]
    frames = native.DURATION_PROFILES[str(profile)]
    created_epoch = _utc_epoch(spec.get("created_at"))
    finished_epoch = _utc_epoch(payload.get("finished_at"))
    deadline = spec.get("deadline_epoch")
    if (
        created_epoch is None
        or finished_epoch is None
        or not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
        or finished_epoch < created_epoch
        or finished_epoch > float(deadline)
    ):
        _fail("receipt_invalid", "Runner receipt timestamps are invalid.")
    if not all(
        (
            payload.get("schema_version") == SCHEMA_VERSION,
            payload.get("run_id") == run_id,
            payload.get("mode") == "t2v",
            payload.get("status") == "completed",
            payload.get("error_code") is None,
            payload.get("created_at") == spec.get("created_at"),
            payload.get("profile") == request.get("profile"),
            payload.get("resolution") == {
                "width": resolution[0], "height": resolution[1]
            },
            payload.get("frames") == frames,
            payload.get("fps") == native.STOCK_FPS,
            payload.get("seed") == request.get("seed"),
            payload.get("prompt_sha256") == _sha256_text(str(request.get("prompt") or "")),
            payload.get("graph_sha256") == spec.get("graph_sha256"),
            payload.get("prompt_id") == spec.get("prompt_id"),
            payload.get("authorization") == "authorized",
            payload.get("models_verified") == 4,
            valid_memory_cleanup_evidence(
                payload.get("memory_cleanup"), attempted_required=True
            ),
        )
    ):
        _fail("receipt_invalid", "Runner receipt does not match its executed contract.")
    model_manifest = payload.get("model_manifest")
    try:
        pinned_manifest = load_manifest(DEFAULT_MANIFEST)
        expected_manifest = {
            "artifact_set": pinned_manifest.artifact_set,
            "revision": pinned_manifest.revision,
            "sha256": manifest_scope_sha256(pinned_manifest),
        }
    except (ArtifactError, OSError) as error:
        raise RunnerError("receipt_invalid", "Pinned model receipt scope is unavailable.") from error
    if (
        not isinstance(model_manifest, dict)
        or set(model_manifest) != {"artifact_set", "revision", "sha256"}
        or model_manifest != expected_manifest
    ):
        _fail("receipt_invalid", "Runner receipt model evidence is invalid.")
    output = payload.get("output")
    expected_filename = re.compile(re.escape(run_id) + r"_[0-9]{5}_\.mp4")
    if (
        not isinstance(output, dict)
        or set(output) != {"filename", "subfolder", "type", "bytes", "sha256", "probe"}
        or not isinstance(output.get("bytes"), int)
        or isinstance(output.get("bytes"), bool)
        or output.get("bytes", 0) <= 0
        or re.fullmatch(r"[0-9a-f]{64}", str(output.get("sha256") or "")) is None
        or not isinstance(output.get("filename"), str)
        or expected_filename.fullmatch(str(output.get("filename"))) is None
        or output.get("subfolder") != "Castle/minimax-h3/t2v"
        or output.get("type") != "output"
        or not isinstance(output.get("probe"), dict)
    ):
        _fail("receipt_invalid", "Runner receipt output evidence is invalid.")
    probe = output["probe"]
    probe_keys = {
        "container", "duration_seconds", "width", "height", "fps",
        "video_codec", "audio_codec", "video_streams", "audio_streams",
    }
    expected_duration = frames / native.STOCK_FPS
    tolerance = max(0.35, expected_duration * 0.08)
    duration = probe.get("duration_seconds")
    if (
        set(probe) != probe_keys
        or probe.get("container") != "mp4"
        or probe.get("video_codec") != "h264"
        or probe.get("width") != resolution[0]
        or probe.get("height") != resolution[1]
        or not isinstance(probe.get("fps"), (int, float))
        or isinstance(probe.get("fps"), bool)
        or abs(float(probe["fps"]) - native.STOCK_FPS) > 0.02
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or abs(float(duration) - expected_duration) > tolerance
        or probe.get("video_streams") != 1
        or not isinstance(probe.get("audio_streams"), int)
        or isinstance(probe.get("audio_streams"), bool)
        or probe.get("audio_streams", 0) < 1
        or not isinstance(probe.get("audio_codec"), str)
        or not probe.get("audio_codec")
    ):
        _fail("receipt_invalid", "Runner receipt probe evidence is invalid.")
    binding = bind_history_output(
        {
            "outputs": {
                "14": {
                    "images": [{
                        "filename": output["filename"],
                        "subfolder": output["subfolder"],
                        "type": output["type"],
                    }]
                }
            }
        },
        run_id=run_id,
        output_root=Path(str(spec.get("output_dir") or "")),
    )
    actual_size, actual_sha256 = _stable_file_sha256(binding.path)
    if actual_size != output["bytes"] or actual_sha256 != output["sha256"]:
        _fail("receipt_invalid", "Runner receipt output bytes do not match the saved file.")
    return payload


def _base_receipt(spec: Mapping[str, Any]) -> dict[str, object]:
    request = spec["request"]
    orientation = request["orientation"]
    resolution = ORIENTATIONS[orientation]
    frames = native.DURATION_PROFILES[request["profile"]]
    return {
        "mode": "t2v",
        "status": "failed",
        "error_code": None,
        "created_at": spec["created_at"],
        "finished_at": _utc_now(),
        "profile": request["profile"],
        "resolution": {"width": resolution[0], "height": resolution[1]},
        "frames": frames,
        "fps": native.STOCK_FPS,
        "seed": request["seed"],
        "prompt_sha256": _sha256_text(request["prompt"]),
        "graph_sha256": spec["graph_sha256"],
        "prompt_id": None,
        "authorization": "not_checked",
        "models_verified": 0,
        "model_manifest": None,
        "output": None,
        "memory_cleanup": memory_cleanup_not_attempted(),
    }


def _validate_worker_spec(payload: object, *, expected_state_root: Path, token: str) -> dict[str, Any]:
    required = {
        "schema_version",
        "run_id",
        "created_at",
        "deadline_epoch",
        "request",
        "graph_sha256",
        "prompt_id",
        "comfy_url",
        "comfy_dir",
        "models_dir",
        "output_dir",
        "authorization_record",
        "state_root",
        "cancel_file",
        "worker_hmac_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema_version") != WORKER_SPEC_SCHEMA_VERSION:
        _fail("worker_spec_invalid", "Worker specification is invalid.")
    if payload.get("state_root") != str(expected_state_root):
        _fail("worker_spec_invalid", "Worker state root does not match its owner directory.")
    signature = payload.get("worker_hmac_sha256")
    if (
        not isinstance(signature, str)
        or re.fullmatch(r"[0-9a-f]{64}", signature) is None
        or not hmac.compare_digest(signature, _worker_spec_hmac(payload, token))
    ):
        _fail("worker_spec_authentication_failed", "Internal worker specification is not authentic.")
    run_id = payload.get("run_id")
    request = payload.get("request")
    if not isinstance(run_id, str) or SAFE_RUN_ID.fullmatch(run_id) is None:
        _fail("worker_spec_invalid", "Worker run identifier is invalid.")
    if not isinstance(request, dict) or set(request) != {
        "mode",
        "prompt",
        "orientation",
        "profile",
        "seed",
        "output_prefix",
    }:
        _fail("worker_spec_invalid", "Worker request is invalid.")
    if request.get("mode") != "t2v" or request.get("orientation") not in ORIENTATIONS:
        _fail("worker_spec_invalid", "Worker request left the T2V lane.")
    if request.get("profile") not in native.DURATION_PROFILES:
        _fail("worker_spec_invalid", "Worker profile is invalid.")
    if request.get("output_prefix") != f"Castle/minimax-h3/t2v/{run_id}":
        _fail("worker_spec_invalid", "Worker output namespace is invalid.")
    deadline = payload.get("deadline_epoch")
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
    ):
        _fail("worker_spec_invalid", "Worker deadline is invalid.")
    if not isinstance(payload.get("graph_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", payload["graph_sha256"]) is None:
        _fail("worker_spec_invalid", "Worker graph digest is invalid.")
    try:
        if str(uuid.UUID(str(payload.get("prompt_id")))) != payload.get("prompt_id"):
            raise ValueError("non-canonical UUID")
    except (AttributeError, TypeError, ValueError) as error:
        raise RunnerError("worker_spec_invalid", "Worker prompt identifier is invalid.") from error
    cancel_file = payload.get("cancel_file")
    if cancel_file is not None and not isinstance(cancel_file, str):
        _fail("worker_spec_invalid", "Worker cancellation path is invalid.")
    for key in ("comfy_dir", "models_dir", "output_dir", "authorization_record"):
        if not isinstance(payload.get(key), str) or not os.path.isabs(payload[key]):
            _fail("worker_spec_invalid", "Worker path is invalid.")
    validate_loopback_url(payload.get("comfy_url"))
    built_request, graph = build_t2v_request(
        prompt=request.get("prompt"),
        orientation=request.get("orientation"),
        profile=request.get("profile"),
        seed=request.get("seed"),
        run_id=run_id,
    )
    if built_request["output_prefix"] != request["output_prefix"] or _sha256_json(graph) != payload["graph_sha256"]:
        _fail("worker_graph_mismatch", "Worker graph does not match its submitted dry-run contract.")
    return payload


def execute_worker_spec(
    spec: Mapping[str, Any],
    *,
    http: Callable[..., Mapping[str, Any]] = request_json,
    model_verifier: Callable[[Path, ArtifactManifest], tuple[ArtifactSpec, ...]] = verify_pinned_models,
    download_state_verifier: Callable[[Path, ArtifactManifest], None] = require_no_incomplete_model_downloads,
    runtime_verifier: Callable[[Path, Mapping[str, Any]], str] = verify_comfy_runtime_contract,
    memory_reader: Callable[[], MemorySnapshot] = read_memory_snapshot,
    gpu_reader: Callable[[], GpuSnapshot] = read_gpu_snapshot,
    listener_verifier: Callable[[str], tuple[str, ...]] = verify_loopback_listener,
    output_probe: Callable[..., dict[str, object]] = probe_output,
    cleanup_requester: Callable[[str], None] | None = None,
) -> tuple[dict[str, object], int]:
    receipt = _base_receipt(spec)
    request = spec["request"]
    _built_request, graph = build_t2v_request(
        prompt=request["prompt"],
        orientation=request["orientation"],
        profile=request["profile"],
        seed=request["seed"],
        run_id=spec["run_id"],
    )
    prompt_id: str | None = None
    allocated_prompt_id = str(spec["prompt_id"])
    submission_attempted = False
    base_url = validate_loopback_url(spec["comfy_url"])
    deadline = float(spec["deadline_epoch"])
    cancel_file = spec["cancel_file"]
    runner_error: RunnerError | None = None
    try:
        _check_deadline_or_cancel(deadline_epoch=deadline, cancel_file=cancel_file)
        listener_verifier(base_url)
        manifest = load_manifest(DEFAULT_MANIFEST)
        decision = evaluate_local_inference_authorization(
            spec["authorization_record"],
            manifest=manifest,
            family="fl",
        )
        if not decision.authorized:
            _fail("authorization_blocked", "Local MiniMax H3 inference is not authorized.")
        receipt["authorization"] = "authorized"
        receipt["model_manifest"] = {
            "artifact_set": manifest.artifact_set,
            "revision": manifest.revision,
            "sha256": manifest_scope_sha256(manifest),
        }

        models_dir = Path(spec["models_dir"])
        comfy_dir = Path(spec["comfy_dir"])
        download_state_verifier(models_dir, manifest)
        selected = model_verifier(models_dir, manifest)
        receipt["models_verified"] = len(selected)
        _check_deadline_or_cancel(deadline_epoch=deadline, cancel_file=cancel_file)

        runtime_verifier(
            comfy_dir,
            http(base_url, "/system_stats", timeout=10.0),
        )
        object_info = http(base_url, "/object_info", timeout=20.0)
        verify_object_info(object_info, graph)
        require_queue_idle(http(base_url, "/queue", timeout=10.0))
        wait_for_memory_headroom(
            base_url=base_url,
            http=http,
            memory_reader=memory_reader,
            listener_verifier=listener_verifier,
        )

        # Hashing and node discovery can take time. Re-evaluate authorization,
        # cancellation, memory pressure, and queue idleness immediately before
        # the only side effect in this worker.
        decision = evaluate_local_inference_authorization(
            spec["authorization_record"],
            manifest=manifest,
            family="fl",
        )
        if not decision.authorized:
            _fail("authorization_blocked", "Local MiniMax H3 inference authorization expired.")
        _check_deadline_or_cancel(deadline_epoch=deadline, cancel_file=cancel_file)
        verified_again = model_verifier(models_dir, manifest)
        if len(verified_again) != len(selected):
            _fail("models_changed", "Pinned H3 model scope changed before submission.")
        require_memory_headroom(memory_reader())
        require_gpu_headroom(gpu_reader())
        listener_verifier(base_url)
        runtime_verifier(
            comfy_dir,
            http(base_url, "/system_stats", timeout=10.0),
        )
        verify_object_info(http(base_url, "/object_info", timeout=20.0), graph)
        require_queue_idle(http(base_url, "/queue", timeout=10.0))
        download_state_verifier(models_dir, manifest)

        submission_attempted = True
        prompt_id = submit_prompt(
            base_url,
            graph,
            client_id=f"castle-h3-{secrets.token_hex(12)}",
            prompt_id=allocated_prompt_id,
            http=http,
        )
        receipt["prompt_id"] = prompt_id
        history = wait_for_history(
            base_url,
            prompt_id,
            deadline_epoch=deadline,
            cancel_file=cancel_file,
            http=http,
        )
        binding = bind_history_output(
            history,
            run_id=spec["run_id"],
            output_root=Path(spec["output_dir"]),
        )
        resolution = ORIENTATIONS[request["orientation"]]
        frames = native.DURATION_PROFILES[request["profile"]]
        output_identity = _artifact_snapshot(binding.path)
        probe = output_probe(binding.path, resolution=resolution, frames=frames)
        size, digest = _stable_file_sha256(binding.path)
        try:
            if _artifact_snapshot(binding.path) != output_identity:
                _fail("output_changed", "H3 output changed between probe and hashing.")
        except OSError as error:
            raise RunnerError("output_changed", "H3 output changed during qualification.") from error
        receipt["output"] = {
            "filename": binding.filename,
            "subfolder": binding.subfolder,
            "type": binding.output_type,
            "bytes": size,
            "sha256": digest,
            "probe": probe,
        }
        receipt["status"] = "completed"
        receipt["error_code"] = None
    except (ArtifactError, OSError) as error:
        runner_error = RunnerError("preflight_failed", "MiniMax H3 preflight failed.")
        runner_error.__cause__ = error
    except RunnerError as error:
        runner_error = error
    except Exception as error:  # fail closed without leaking remote or prompt details
        runner_error = RunnerError("runner_failed", "MiniMax H3 runner failed safely.")
        runner_error.__cause__ = error

    if runner_error is not None and submission_attempted:
        receipt["cancel_action"] = cancel_owned_prompt(
            base_url,
            prompt_id or allocated_prompt_id,
            http=http,
        )
    if submission_attempted:
        # Success history can precede queue removal by a short interval.  The
        # worker must prove terminal queue state on every post-submit path
        # before recycling and before gpu_queue is allowed to release flock.
        wait_for_comfy_queue_idle(base_url, timeout_seconds=None, http=http)
        receipt["memory_cleanup"] = release_comfy_memory(
            base_url,
            requester=cleanup_requester,
        )
    if runner_error is None:
        return receipt, 0
    receipt["status"] = "cancelled" if runner_error.code == "run_cancelled" else "failed"
    receipt["error_code"] = runner_error.code
    return receipt, 2


def _install_worker_signal_handlers() -> None:
    def interrupted(_signum: int, _frame: object) -> None:
        global _INTERRUPTED
        _INTERRUPTED = True

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)


def _worker_entry(spec_path_value: str) -> int:
    token = os.environ.pop("CASTLE_H3_WORKER_TOKEN", "")
    state_value = os.environ.pop("CASTLE_H3_WORKER_STATE_ROOT", "")
    if not token or not state_value or not token.isascii():
        print("[error] worker_context_missing", file=sys.stderr)
        return 2
    try:
        state_root = _ensure_private_directory(state_value)
        pending = _ensure_private_directory(state_root / "pending")
        spec_path = Path(os.path.abspath(spec_path_value))
        if spec_path.parent != pending or not spec_path.name.endswith(".json"):
            _fail("worker_spec_path_invalid", "Worker specification path is invalid.")
        raw = _read_owner_file(spec_path, max_bytes=MAX_SPEC_BYTES, code="worker_spec_invalid")
        payload = _safe_json_loads(raw, code="worker_spec_invalid")
        spec = _validate_worker_spec(payload, expected_state_root=state_root, token=token)
        try:
            spec_path.unlink()
        except OSError as error:
            raise RunnerError("worker_spec_cleanup_failed", "Worker specification could not be consumed.") from error
        _install_worker_signal_handlers()
        receipt, code = execute_worker_spec(spec)
        receipt["finished_at"] = _utc_now()
        path = write_receipt(state_root, spec["run_id"], receipt)
        print(json.dumps({"status": receipt["status"], "run_id": spec["run_id"], "receipt": str(path)}))
        return code
    except RunnerError as error:
        print(f"[error] {error.code}: {error}", file=sys.stderr)
        return 2


def _safe_queue_script(path_value: str | os.PathLike[str]) -> Path:
    path = Path(path_value).expanduser()
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RunnerError("gpu_queue_unavailable", "Castle GPU queue wrapper is unavailable.") from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        _fail("gpu_queue_unsafe", "Castle GPU queue wrapper is unsafe.")
    try:
        parent_info = resolved.parent.stat()
    except OSError as error:
        raise RunnerError("gpu_queue_unsafe", "Castle GPU queue wrapper is unsafe.") from error
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        _fail("gpu_queue_unsafe", "Castle GPU queue wrapper parent is writable by another user.")
    return resolved


def gpu_queue_command(queue_script: Path, spec_path: Path) -> list[str]:
    return [
        sys.executable,
        str(queue_script),
        "--min-free-mb",
        str(GPU_MIN_FREE_MIB),
        "--max-util",
        str(GPU_MAX_UTIL_PERCENT),
        "--interval",
        str(GPU_QUEUE_INTERVAL_SECONDS),
        "--comfy-url",
        "",
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        "--_worker-spec",
        str(spec_path),
    ]


def _worker_environment(*, token: str, state_root: Path) -> dict[str, str]:
    allowed = (
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "USER",
        "LOGNAME",
        "TMPDIR",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    runtime_dir = Path("/run/user") / str(os.geteuid())
    try:
        runtime_info = runtime_dir.stat()
    except OSError as error:
        raise RunnerError(
            "systemd_user_bus_unavailable",
            "The local systemd user bus is unavailable.",
        ) from error
    if (
        not stat.S_ISDIR(runtime_info.st_mode)
        or runtime_info.st_uid != os.geteuid()
        or stat.S_IMODE(runtime_info.st_mode) & 0o077
    ):
        _fail(
            "systemd_user_bus_unsafe",
            "The local systemd user bus directory is unsafe.",
        )
    environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
    environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    environment["CASTLE_H3_WORKER_TOKEN"] = token
    environment["CASTLE_H3_WORKER_STATE_ROOT"] = str(state_root)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _outer_failure_receipt(spec: Mapping[str, Any], *, status: str, error_code: str) -> dict[str, object]:
    receipt = _base_receipt(spec)
    receipt["status"] = status
    receipt["error_code"] = error_code
    receipt["finished_at"] = _utc_now()
    return receipt


def execute_via_gpu_queue(
    *,
    spec: Mapping[str, Any],
    state_root: Path,
    queue_script: Path,
    cancel_file: str | None,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    prompt_canceller: Callable[[str, str], str] = cancel_owned_prompt,
    wall_clock: Callable[[], float] = time.time,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    process_terminator: Callable[[subprocess.Popen[Any]], None] = _terminate_process_group,
) -> int:
    pending = _ensure_private_directory(state_root / "pending")
    run_id = spec["run_id"]
    spec_path = pending / f"{run_id}.json"
    internal_cancel_path = pending / f"{run_id}.cancel"
    token = secrets.token_hex(32)
    stored = dict(spec)
    # Never pass a caller-controlled cancellation path to the privileged
    # worker.  The outer owner watches that path and relays cancellation
    # through this owner-only file inside the already-private state tree.
    stored["cancel_file"] = str(internal_cancel_path)
    stored["worker_hmac_sha256"] = _worker_spec_hmac(stored, token)
    _write_exclusive_json(spec_path, stored)
    deadline_epoch = float(spec["deadline_epoch"])
    queue_process: subprocess.Popen[Any] | None = None
    terminal_code = "gpu_queue_failed"
    terminal_status = "failed"
    terminal_cancel_action: str | None = None
    cancellation_requested = False
    previous_handlers: dict[int, Any] = {}

    def request_worker_cancellation(*, error_code: str, status: str) -> None:
        nonlocal cancellation_requested, terminal_code, terminal_status
        if cancellation_requested:
            return
        terminal_code = error_code
        terminal_status = status
        cancellation_requested = True
        try:
            _write_exclusive_json(
                internal_cancel_path,
                {"run_id": run_id, "error_code": error_code},
            )
        except RunnerError:
            # Still honor the hard grace period: the worker also owns the
            # original generation deadline and may already be settling it.
            terminal_code = "worker_cancel_signal_failed"

    def relay_signal(_signum: int, _frame: object) -> None:
        request_worker_cancellation(
            error_code="run_cancelled",
            status="cancelled",
        )

    try:
        if _is_cancelled(cancel_file):
            terminal_code = "run_cancelled"
            terminal_status = "cancelled"
            return 2
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, relay_signal)
            except ValueError:
                previous_handlers.clear()
                break
        queue_process = popen(
            gpu_queue_command(queue_script, spec_path),
            env=_worker_environment(token=token, state_root=state_root),
            cwd=str(REPO_ROOT),
            start_new_session=True,
            close_fds=True,
        )
        while True:
            return_code = queue_process.poll()
            if return_code is not None:
                if int(return_code) != 0:
                    if not cancellation_requested:
                        terminal_code = "worker_failed"
                        terminal_cancel_action = prompt_canceller(
                            str(spec["comfy_url"]), str(spec["prompt_id"])
                        )
                    return 2
                try:
                    read_completed_receipt(state_root, stored)
                except RunnerError:
                    terminal_code = "receipt_invalid"
                    terminal_cancel_action = prompt_canceller(
                        str(spec["comfy_url"]), str(spec["prompt_id"])
                    )
                    return 2
                return 0
            if _is_cancelled(cancel_file):
                request_worker_cancellation(
                    error_code="run_cancelled",
                    status="cancelled",
                )
            elif wall_clock() >= deadline_epoch:
                request_worker_cancellation(
                    error_code="run_timeout",
                    status="failed",
                )
            # Never hard-kill the process group that owns gpu_queue's flock.
            # The signed worker has the generation deadline and cancellation
            # sentinel, then keeps the flock until queue-idle proof and the
            # controlled ComfyUI recycle finish.  A wedged worker therefore
            # remains visibly wedged instead of unlocking over active work.
            try:
                sleeper(0.25)
            except KeyboardInterrupt:
                # Non-main-thread callers cannot install scoped handlers.
                # Relay cancellation and keep waiting so the child retains
                # flock through queue settlement and service recycling.
                relay_signal(signal.SIGINT, None)
    except (OSError, ValueError) as error:
        raise RunnerError("gpu_queue_failed", "Castle GPU queue could not start.") from error
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass
        try:
            spec_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            terminal_code = "worker_spec_cleanup_failed"
        try:
            internal_cancel_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            terminal_code = "worker_cancel_cleanup_failed"
        receipt_path = _receipt_path(state_root, run_id)
        if not receipt_path.exists():
            try:
                failure = _outer_failure_receipt(
                    stored,
                    status=terminal_status,
                    error_code=terminal_code,
                )
                if terminal_cancel_action is not None:
                    failure["cancel_action"] = terminal_cancel_action
                write_receipt(
                    state_root,
                    run_id,
                    failure,
                )
            except RunnerError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="print a sanitized graph summary (default)")
    action.add_argument("--execute", action="store_true", help="run only after every fail-closed preflight")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt", help="UTF-8 H3 prompt text")
    prompt.add_argument("--prompt-file", help="path to a small UTF-8 prompt file")
    parser.add_argument("--orientation", choices=tuple(ORIENTATIONS), default="landscape")
    parser.add_argument("--profile", choices=tuple(native.DURATION_PROFILES), default="smoke")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--cancel-file", help="outside-repository cancellation sentinel")
    parser.add_argument("--comfy-url", default=DEFAULT_COMFY_URL)
    parser.add_argument("--comfy-dir", default=str(DEFAULT_COMFY_DIR))
    parser.add_argument("--models-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_ROOT))
    parser.add_argument("--authorization-record", default=str(DEFAULT_AUTHORIZATION_RECORD))
    parser.add_argument("--gpu-queue-script", default=str(DEFAULT_GPU_QUEUE_SCRIPT))
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if len(raw_argv) == 2 and raw_argv[0] == "--_worker-spec":
        return _worker_entry(raw_argv[1])
    args = build_parser().parse_args(raw_argv)
    try:
        if not MIN_TIMEOUT_SECONDS <= args.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            _fail("timeout_invalid", "Timeout is outside the allowed range.")
        validate_loopback_url(args.comfy_url)
        prompt = args.prompt if args.prompt is not None else _read_prompt_file(args.prompt_file)
        seed = args.seed if args.seed is not None else secrets.randbits(64)
        run_id = _new_run_id()
        request, graph = build_t2v_request(
            prompt=prompt,
            orientation=args.orientation,
            profile=args.profile,
            seed=seed,
            run_id=run_id,
        )
        summary = sanitized_graph_summary(request=request, graph=graph, run_id=run_id)
        if not args.execute:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        state_root = _ensure_private_directory(args.state_dir)
        comfy_dir = Path(args.comfy_dir).expanduser().resolve(strict=False)
        models_dir = Path(args.models_dir).expanduser() if args.models_dir else comfy_dir / "models"
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else comfy_dir / "output"
        cancel_file = None
        if args.cancel_file:
            cancel_file = str(
                _normalize_outside_repository(args.cancel_file, code="cancel_path_invalid")
            )
        authorization_record = Path(args.authorization_record).expanduser()
        if not authorization_record.is_absolute():
            _fail("authorization_path_invalid", "Authorization record path must be absolute.")
        queue_script = _safe_queue_script(args.gpu_queue_script)
        created_at = _utc_now()
        deadline_epoch = time.time() + args.timeout_seconds
        prompt_id = str(uuid.uuid4())
        spec: dict[str, Any] = {
            "schema_version": WORKER_SPEC_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": created_at,
            "deadline_epoch": deadline_epoch,
            "request": {
                "mode": "t2v",
                "prompt": request["prompt"],
                "orientation": args.orientation,
                "profile": request["profile"],
                "seed": request["seed"],
                "output_prefix": request["output_prefix"],
            },
            "graph_sha256": summary["graph"]["sha256"],
            "prompt_id": prompt_id,
            "comfy_url": validate_loopback_url(args.comfy_url),
            "comfy_dir": str(comfy_dir),
            "models_dir": str(models_dir.resolve(strict=False)),
            "output_dir": str(output_dir.resolve(strict=False)),
            "authorization_record": str(authorization_record),
            "state_root": str(state_root),
            "cancel_file": cancel_file,
        }
        print(json.dumps({
            "status": "queued",
            "run_id": run_id,
            "graph_sha256": summary["graph"]["sha256"],
            "receipt": str(_receipt_path(state_root, run_id)),
        }))
        return execute_via_gpu_queue(
            spec=spec,
            state_root=state_root,
            queue_script=queue_script,
            cancel_file=cancel_file,
        )
    except RunnerError as error:
        print(f"[error] {error.code}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
