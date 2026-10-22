"""Blocking, fail-closed GPU-queue boundary for compiled MiniMax H3 Web graphs.

The HTTP layer is expected to compile and materialize a graph before calling
``execute_web_graph`` from a background thread.  This module never submits a
graph directly: it writes an authenticated owner-only worker specification and
runs the worker through Castle's shared ``gpu_queue.py`` flock.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping
import uuid

# Production launches ``webui/server.py`` directly, making ``webui`` rather
# than the release root ``sys.path[0]``.  Resolve the sibling ``scripts``
# package from this module's immutable location before importing the guarded
# worker primitives; never depend on a service-level PYTHONPATH.
_RELEASE_ROOT = Path(__file__).resolve().parents[1]
if str(_RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_RELEASE_ROOT))

from scripts.minimax_h3 import run_native as guarded

try:  # Support both ``webui.*`` package imports and the server's flat imports.
    from . import minimax_h3_native as native
except ImportError:  # pragma: no cover - exercised by the production flat import.
    import minimax_h3_native as native  # type: ignore[no-redef]


WORKER_SCHEMA_VERSION = "castle.minimax-h3-web-worker.v1"
RECEIPT_SCHEMA_VERSION = "castle.minimax-h3-web-receipt.v1"
WORKER_TOKEN_ENV = "CASTLE_H3_WEB_WORKER_TOKEN"
WORKER_STATE_ENV = "CASTLE_H3_WEB_STATE_ROOT"
MAX_GRAPH_BYTES = 48 * 1024
DEFAULT_TIMEOUT_SECONDS = 10_800
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 86_400

_SAFE_TOKEN = re.compile(r"[0-9a-f]{64}")
_SAFE_NONCE = re.compile(r"[0-9a-f]{16}")
_SAFE_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_SAFE_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_OUTPUT_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?")
_SAFE_OUTPUT_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.mp4")
_SAFE_SHA256 = re.compile(r"[0-9a-f]{64}")

_ALLOWED_NODE_CLASSES = frozenset(
    {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3ReferenceToVideo",
        "RandomNoise",
        "BasicGuider",
        "KSamplerSelect",
        "BasicScheduler",
        "SamplerCustomAdvanced",
        "VAEDecode",
        "VAEDecodeAudio",
        "CreateVideo",
        "SaveVideo",
        "LoadImage",
        "VHS_LoadVideo",
        "LoadAudio",
        "PathchSageAttentionKJ",
        "EasyCache",
        "MiniMaxH3TeaCache",
    }
)
_MEDIA_LOADERS: Mapping[str, tuple[str, frozenset[str], str]] = {
    "LoadImage": ("image", frozenset({"png", "jpg", "jpeg", "webp"}), "image"),
    "VHS_LoadVideo": ("video", frozenset({"mp4", "mov", "webm", "mkv"}), "video"),
    "LoadAudio": ("audio", frozenset({"wav", "mp3", "flac", "m4a", "ogg", "aac"}), "audio"),
}

WORKER_SPEC_KEYS = frozenset(
    {
        "schema_version",
        "prompt_id",
        "graph",
        "graph_sha256",
        "mode",
        "save_node_id",
        "output_prefix",
        "width",
        "height",
        "frames",
        "media_evidence",
        "comfy_dir",
        "state_root",
        "receipt_path",
        "cancel_file",
        "deadline_epoch",
        "worker_hmac_sha256",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "prompt_id",
        "graph_sha256",
        "mode",
        "resolution",
        "frames",
        "fps",
        "models_verified",
        "model_manifest",
        "output",
        "memory_cleanup",
    }
)


class WebGraphExecutionError(RuntimeError):
    """Stable, sanitized failure from the Web graph execution boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise WebGraphExecutionError(code, message)


@dataclass(frozen=True, slots=True)
class WebGraphContract:
    mode: str
    save_node_id: str
    output_prefix: str
    width: int
    height: int
    frames: int
    media_evidence: tuple[dict[str, object], ...]


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise WebGraphExecutionError(
            "graph_invalid", "MiniMax H3 graph is not canonical JSON."
        ) from error


def _snapshot_graph(graph: object) -> dict[str, dict[str, object]]:
    if type(graph) is not dict:
        _fail("graph_invalid", "MiniMax H3 graph must be an object.")
    raw = _canonical(graph)
    if not raw or len(raw) > MAX_GRAPH_BYTES:
        _fail("graph_invalid", "MiniMax H3 graph exceeds the safe size limit.")
    value = json.loads(raw.decode("utf-8"))
    if type(value) is not dict or not value:
        _fail("graph_invalid", "MiniMax H3 graph must not be empty.")
    return value


def _graph_sha256(graph: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(graph)).hexdigest()


def _comfy_roots(comfy_dir_value: str | os.PathLike[str]) -> tuple[Path, Path, Path]:
    try:
        comfy_dir = Path(comfy_dir_value).expanduser().resolve(strict=True)
        input_root = (comfy_dir / "input").resolve(strict=True)
        output_root = (comfy_dir / "output").resolve(strict=True)
    except OSError as error:
        raise WebGraphExecutionError(
            "comfy_storage_unavailable", "ComfyUI storage is unavailable."
        ) from error
    for path in (comfy_dir, input_root, output_root):
        try:
            info = path.stat()
        except OSError as error:
            raise WebGraphExecutionError(
                "comfy_storage_unavailable", "ComfyUI storage is unavailable."
            ) from error
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o002
        ):
            _fail("comfy_storage_unsafe", "ComfyUI storage is unsafe.")
    return comfy_dir, input_root, output_root


def _media_evidence(
    input_root: Path,
    *,
    basename: object,
    extensions: frozenset[str],
    media_type: str,
) -> dict[str, object]:
    if (
        type(basename) is not str
        or not basename
        or ".." in basename
        or "/" in basename
        or "\\" in basename
        or "://" in basename
        or _SAFE_BASENAME.fullmatch(basename) is None
    ):
        _fail(
            "media_name_invalid",
            "MiniMax H3 media must be a materialized ComfyUI input basename.",
        )
    extension = basename.rpartition(".")[2].casefold()
    if extension not in extensions:
        _fail("media_name_invalid", "MiniMax H3 media extension is not allowed.")
    candidate = input_root / basename
    try:
        lexical = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise WebGraphExecutionError(
            "media_not_materialized", "MiniMax H3 media is not materialized."
        ) from error
    if (
        resolved.parent != input_root
        or stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or lexical.st_uid != os.geteuid()
        or lexical.st_nlink != 1
        or lexical.st_size <= 0
        or stat.S_IMODE(lexical.st_mode) & 0o002
    ):
        _fail("media_not_materialized", "MiniMax H3 media is not a trusted input.")
    try:
        size, digest = guarded._stable_file_sha256(resolved)
    except guarded.RunnerError as error:
        raise WebGraphExecutionError(
            "media_not_materialized", "MiniMax H3 media could not be bound."
        ) from error
    return {
        "basename": basename,
        "kind": media_type,
        "bytes": size,
        "sha256": digest,
    }


def _node_inputs(node: object) -> tuple[str, dict[str, object]]:
    if type(node) is not dict or not {"class_type", "inputs"} <= set(node):
        _fail("graph_invalid", "MiniMax H3 graph node is invalid.")
    if not set(node) <= {"class_type", "inputs", "_meta"}:
        _fail("graph_invalid", "MiniMax H3 graph node has unsupported fields.")
    class_type = node.get("class_type")
    inputs = node.get("inputs")
    if type(class_type) is not str or class_type not in _ALLOWED_NODE_CLASSES:
        _fail("graph_node_forbidden", "MiniMax H3 graph contains an untrusted node.")
    if type(inputs) is not dict:
        _fail("graph_invalid", "MiniMax H3 graph node inputs are invalid.")
    return class_type, inputs


def _validate_graph_contract(
    graph: Mapping[str, object], *, input_root: Path
) -> WebGraphContract:
    save_nodes: list[tuple[str, dict[str, object]]] = []
    generators: list[tuple[str, dict[str, object]]] = []
    model_loaders: list[tuple[str, dict[str, object]]] = []
    clip_loaders: list[dict[str, object]] = []
    vae_loaders: list[dict[str, object]] = []
    media: list[dict[str, object]] = []
    media_counts = {"image": 0, "video": 0, "audio": 0}

    for node_id, node in graph.items():
        if type(node_id) is not str or _SAFE_NODE_ID.fullmatch(node_id) is None:
            _fail("graph_invalid", "MiniMax H3 graph node identifier is invalid.")
        class_type, inputs = _node_inputs(node)
        if class_type == "SaveVideo":
            save_nodes.append((node_id, inputs))
        elif class_type in {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}:
            generators.append((class_type, inputs))
        elif class_type == "UNETLoader":
            model_loaders.append((class_type, inputs))
        elif class_type == "CLIPLoader":
            clip_loaders.append(inputs)
        elif class_type == "VAELoader":
            vae_loaders.append(inputs)
        loader = _MEDIA_LOADERS.get(class_type)
        if loader is not None:
            field, extensions, media_type = loader
            media_counts[media_type] += 1
            media.append(
                _media_evidence(
                    input_root,
                    basename=inputs.get(field),
                    extensions=extensions,
                    media_type=media_type,
                )
            )

    if len(save_nodes) != 1 or len(generators) != 1:
        _fail("graph_invalid", "MiniMax H3 graph must have one generator and one saver.")
    save_node_id, save_inputs = save_nodes[0]
    prefix = save_inputs.get("filename_prefix")
    if type(prefix) is not str:
        _fail("output_namespace_invalid", "MiniMax H3 output namespace is invalid.")
    match = re.fullmatch(
        r"Castle/minimax-h3/(t2v|i2v|ref2va_image)/(.+)", prefix
    )
    if match is None or _SAFE_OUTPUT_TOKEN.fullmatch(match.group(2)) is None:
        _fail("output_namespace_invalid", "MiniMax H3 output namespace is invalid.")
    mode = match.group(1)
    if (
        save_inputs.get("format") != "mp4"
        or save_inputs.get("codec") != "h264"
        or type(save_inputs.get("video")) is not list
        or len(save_inputs["video"]) != 2
    ):
        _fail("output_contract_invalid", "MiniMax H3 saver contract is invalid.")

    generator_type, generator_inputs = generators[0]
    expected_generator = (
        "MiniMaxH3ReferenceToVideo"
        if mode == "ref2va_image"
        else "MiniMaxH3ImageToVideo"
    )
    if generator_type != expected_generator:
        _fail("graph_mode_mismatch", "MiniMax H3 graph mode is inconsistent.")
    width = generator_inputs.get("width")
    height = generator_inputs.get("height")
    frames = generator_inputs.get("length")
    if (
        type(width) is not int
        or type(height) is not int
        or (width, height) not in native.ALLOWED_RESOLUTIONS
        or type(frames) is not int
        or frames not in native.LEGAL_FRAME_LENGTHS
    ):
        _fail("graph_profile_invalid", "MiniMax H3 output profile is invalid.")

    if mode == "t2v" and any(media_counts.values()):
        _fail("graph_mode_mismatch", "MiniMax H3 T2V graph cannot load media.")
    if mode == "i2v" and not (
        1 <= media_counts["image"] <= 2
        and media_counts["video"] == 0
        and media_counts["audio"] == 0
    ):
        _fail("graph_mode_mismatch", "MiniMax H3 I2V media inputs are invalid.")
    if mode == "ref2va_image" and not (
        1 <= sum(media_counts.values())
        and media_counts["image"] <= 9
        and media_counts["video"] <= 3
        and media_counts["audio"] <= 3
    ):
        _fail("graph_mode_mismatch", "MiniMax H3 reference inputs are invalid.")

    expected_unet = native.REF2VA_MODEL if mode == "ref2va_image" else native.FL2VA_MODEL
    if (
        len(model_loaders) != 1
        or model_loaders[0][1].get("unet_name") != expected_unet
        or len(clip_loaders) != 1
        or clip_loaders[0].get("clip_name") != native.TEXT_ENCODER
        or len(vae_loaders) != 2
        or {item.get("vae_name") for item in vae_loaders}
        != {native.VIDEO_VAE, native.AUDIO_VAE}
    ):
        _fail("graph_model_invalid", "MiniMax H3 graph model bindings are invalid.")

    unique_media = {
        (str(item["kind"]), str(item["basename"])): item for item in media
    }
    evidence = tuple(
        dict(unique_media[key]) for key in sorted(unique_media)
    )
    return WebGraphContract(
        mode=mode,
        save_node_id=save_node_id,
        output_prefix=prefix,
        width=width,
        height=height,
        frames=frames,
        media_evidence=evidence,
    )


def _contract_payload(contract: WebGraphContract) -> dict[str, object]:
    return {
        "mode": contract.mode,
        "save_node_id": contract.save_node_id,
        "output_prefix": contract.output_prefix,
        "width": contract.width,
        "height": contract.height,
        "frames": contract.frames,
        "media_evidence": [dict(item) for item in contract.media_evidence],
    }


def _safe_worker_script(path_value: str | os.PathLike[str]) -> Path:
    path = Path(path_value).expanduser()
    try:
        lexical = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise WebGraphExecutionError(
            "worker_unavailable", "MiniMax H3 Web worker is unavailable."
        ) from error
    if (
        stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or lexical.st_uid != os.geteuid()
        or lexical.st_nlink != 1
        or stat.S_IMODE(lexical.st_mode) & 0o022
    ):
        _fail("worker_unsafe", "MiniMax H3 Web worker is unsafe.")
    return resolved


def _worker_environment(*, token: str, state_root: Path) -> dict[str, str]:
    if _SAFE_TOKEN.fullmatch(token) is None:
        _fail("worker_context_invalid", "MiniMax H3 worker context is invalid.")
    environment = guarded._worker_environment(token=token, state_root=state_root)
    environment[WORKER_TOKEN_ENV] = environment.pop("CASTLE_H3_WORKER_TOKEN")
    environment[WORKER_STATE_ENV] = environment.pop("CASTLE_H3_WORKER_STATE_ROOT")
    return environment


def _write_cancel_sentinel(path: Path, *, prompt_id: str, reason: str) -> None:
    try:
        guarded._write_exclusive_json(
            path, {"prompt_id": prompt_id, "reason": reason}
        )
        return
    except guarded.RunnerError as error:
        if error.code != "state_collision":
            raise WebGraphExecutionError(
                "cancel_signal_failed", "MiniMax H3 cancellation could not be relayed."
            ) from error
    try:
        info = path.lstat()
    except OSError as error:
        raise WebGraphExecutionError(
            "cancel_signal_failed", "MiniMax H3 cancellation state is unavailable."
        ) from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        _fail("cancel_signal_failed", "MiniMax H3 cancellation state is unsafe.")


def _external_cancelled(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _run_queue_process(
    command: list[str],
    *,
    environment: Mapping[str, str],
    cancel_path: Path,
    external_cancel: Path | None,
    prompt_id: str,
    deadline_epoch: float,
    popen: Callable[..., subprocess.Popen[Any]],
    wall_clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> int:
    previous_handlers: dict[int, Any] = {}
    interrupted = False

    def relay(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        try:
            _write_cancel_sentinel(
                cancel_path, prompt_id=prompt_id, reason=f"signal_{signum}"
            )
        except WebGraphExecutionError:
            pass

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, relay)
            except ValueError:
                previous_handlers.clear()
                break
        process = popen(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=dict(environment),
            start_new_session=True,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cancellation_relayed = False
        while True:
            return_code = process.poll()
            if return_code is not None:
                if interrupted:
                    raise KeyboardInterrupt
                return int(return_code)
            if not cancellation_relayed and _external_cancelled(external_cancel):
                _write_cancel_sentinel(
                    cancel_path, prompt_id=prompt_id, reason="external_cancel"
                )
                cancellation_relayed = True
            elif not cancellation_relayed and wall_clock() >= deadline_epoch:
                _write_cancel_sentinel(
                    cancel_path, prompt_id=prompt_id, reason="deadline"
                )
                cancellation_relayed = True
            try:
                sleeper(0.25)
            except KeyboardInterrupt:
                relay(signal.SIGINT, None)
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass


def _output_file(
    output: Mapping[str, object], *, contract: WebGraphContract, output_root: Path
) -> Path:
    expected_subfolder, token = contract.output_prefix.rsplit("/", 1)
    filename = output.get("filename")
    if (
        type(filename) is not str
        or _SAFE_OUTPUT_FILE.fullmatch(filename) is None
        or re.fullmatch(re.escape(token) + r"_[0-9]{5}_\.mp4", filename) is None
        or output.get("subfolder") != expected_subfolder
        or output.get("type") != "output"
    ):
        _fail("receipt_invalid", "MiniMax H3 receipt output namespace is invalid.")
    candidate = output_root.joinpath(*expected_subfolder.split("/"), filename)
    try:
        lexical = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise WebGraphExecutionError(
            "receipt_invalid", "MiniMax H3 receipt output is unavailable."
        ) from error
    try:
        contained = os.path.commonpath((str(output_root), str(resolved))) == str(output_root)
    except ValueError:
        contained = False
    if (
        not contained
        or stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or lexical.st_uid != os.geteuid()
        or lexical.st_nlink != 1
        or lexical.st_size <= 0
        or output.get("path") != str(resolved)
    ):
        _fail("receipt_invalid", "MiniMax H3 receipt output is unsafe.")
    return resolved


def _valid_probe(value: object, *, contract: WebGraphContract) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "container",
        "duration_seconds",
        "width",
        "height",
        "fps",
        "video_codec",
        "audio_codec",
        "video_streams",
        "audio_streams",
    }:
        return False
    duration = value.get("duration_seconds")
    fps = value.get("fps")
    expected_duration = contract.frames / native.STOCK_FPS
    tolerance = max(0.35, expected_duration * 0.08)
    return all(
        (
            value.get("container") == "mp4",
            value.get("video_codec") == "h264",
            value.get("width") == contract.width,
            value.get("height") == contract.height,
            isinstance(fps, (int, float)) and not isinstance(fps, bool),
            isinstance(fps, (int, float))
            and math.isfinite(float(fps))
            and abs(float(fps) - native.STOCK_FPS) <= 0.02,
            isinstance(duration, (int, float)) and not isinstance(duration, bool),
            isinstance(duration, (int, float))
            and math.isfinite(float(duration))
            and abs(float(duration) - expected_duration) <= tolerance,
            value.get("video_streams") == 1,
            type(value.get("audio_streams")) is int
            and int(value["audio_streams"]) >= 1,
            isinstance(value.get("audio_codec"), str)
            and bool(value.get("audio_codec")),
        )
    )


def _validate_receipt(
    payload: object,
    *,
    prompt_id: str,
    graph_sha256: str,
    contract: WebGraphContract,
    output_root: Path,
) -> dict[str, object]:
    if type(payload) is not dict or set(payload) != RECEIPT_KEYS:
        _fail("receipt_invalid", "MiniMax H3 worker receipt schema is invalid.")
    resolution = payload.get("resolution")
    output = payload.get("output")
    model_manifest = payload.get("model_manifest")
    try:
        pinned_manifest = guarded.load_manifest(guarded.DEFAULT_MANIFEST)
        expected_manifest = {
            "artifact_set": pinned_manifest.artifact_set,
            "revision": pinned_manifest.revision,
            "sha256": guarded.manifest_scope_sha256(pinned_manifest),
        }
    except Exception as error:
        raise WebGraphExecutionError(
            "receipt_invalid", "MiniMax H3 pinned model scope is unavailable."
        ) from error
    if (
        payload.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or payload.get("prompt_id") != prompt_id
        or payload.get("graph_sha256") != graph_sha256
        or payload.get("mode") != contract.mode
        or resolution != {"width": contract.width, "height": contract.height}
        or payload.get("frames") != contract.frames
        or payload.get("fps") != native.STOCK_FPS
        or payload.get("models_verified") != 4
        or model_manifest != expected_manifest
        or type(output) is not dict
        or not guarded.valid_memory_cleanup_evidence(
            payload.get("memory_cleanup"), attempted_required=True
        )
    ):
        _fail("receipt_invalid", "MiniMax H3 worker receipt does not match its graph.")
    if set(output) != {
        "path",
        "filename",
        "subfolder",
        "type",
        "bytes",
        "sha256",
        "probe",
    }:
        _fail("receipt_invalid", "MiniMax H3 worker output receipt is invalid.")
    if (
        type(output.get("bytes")) is not int
        or int(output["bytes"]) <= 0
        or type(output.get("sha256")) is not str
        or _SAFE_SHA256.fullmatch(str(output["sha256"])) is None
        or not _valid_probe(output.get("probe"), contract=contract)
    ):
        _fail("receipt_invalid", "MiniMax H3 worker output evidence is invalid.")
    path = _output_file(output, contract=contract, output_root=output_root)
    try:
        size, digest = guarded._stable_file_sha256(path)
    except guarded.RunnerError as error:
        raise WebGraphExecutionError(
            "receipt_invalid", "MiniMax H3 output could not be revalidated."
        ) from error
    if size != output["bytes"] or not secrets.compare_digest(digest, str(output["sha256"])):
        _fail("receipt_invalid", "MiniMax H3 output bytes changed after execution.")
    return dict(payload)


def _deadline(
    *, deadline_epoch: float | None, timeout_seconds: int, now: float
) -> float:
    if deadline_epoch is None:
        if (
            type(timeout_seconds) is not int
            or not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            _fail("deadline_invalid", "MiniMax H3 execution timeout is invalid.")
        return now + timeout_seconds
    if (
        not isinstance(deadline_epoch, (int, float))
        or isinstance(deadline_epoch, bool)
        or not math.isfinite(float(deadline_epoch))
        or not now < float(deadline_epoch) <= now + MAX_TIMEOUT_SECONDS
    ):
        _fail("deadline_invalid", "MiniMax H3 execution deadline is invalid.")
    return float(deadline_epoch)


def execute_web_graph(
    graph: Mapping[str, Mapping[str, object]],
    prompt_id: str,
    state_root: Path,
    queue_script: Path,
    worker_script: Path,
    *,
    comfy_dir: Path | None = None,
    cancel_file: Path | None = None,
    deadline_epoch: float | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    wall_clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Execute one compiled graph under the shared flock and return its receipt.

    The function is intentionally blocking.  Cancellation and deadline expiry
    create an owner-only sentinel; they never terminate the queue process that
    owns the flock.  The worker therefore retains serialization until ComfyUI
    proves global queue-idle and the controlled service recycle finishes.
    """

    try:
        canonical_prompt_id = str(uuid.UUID(prompt_id))
    except (AttributeError, TypeError, ValueError) as error:
        raise WebGraphExecutionError(
            "prompt_id_invalid", "MiniMax H3 prompt identifier is invalid."
        ) from error
    if canonical_prompt_id != prompt_id:
        _fail("prompt_id_invalid", "MiniMax H3 prompt identifier is invalid.")
    if not callable(popen) or not callable(wall_clock) or not callable(sleeper):
        _fail("executor_invalid", "MiniMax H3 executor hooks are invalid.")

    now = float(wall_clock())
    generation_deadline = _deadline(
        deadline_epoch=deadline_epoch, timeout_seconds=timeout_seconds, now=now
    )
    private_state = guarded._ensure_private_directory(state_root)
    try:
        safe_queue = guarded._safe_queue_script(queue_script)
    except guarded.RunnerError as error:
        raise WebGraphExecutionError(
            "queue_unsafe", "Castle GPU queue wrapper is unavailable or unsafe."
        ) from error
    safe_worker = _safe_worker_script(worker_script)
    selected_comfy = comfy_dir or guarded.DEFAULT_COMFY_DIR
    resolved_comfy, input_root, output_root = _comfy_roots(selected_comfy)
    graph_snapshot = _snapshot_graph(graph)
    contract = _validate_graph_contract(graph_snapshot, input_root=input_root)
    graph_digest = _graph_sha256(graph_snapshot)

    nonce = secrets.token_hex(8)
    spec_path = private_state / f"spec-{nonce}.json"
    receipt_path = private_state / f"receipt-{nonce}.json"
    internal_cancel = private_state / f"cancel-{nonce}.json"
    token = secrets.token_hex(32)
    spec: dict[str, object] = {
        "schema_version": WORKER_SCHEMA_VERSION,
        "prompt_id": prompt_id,
        "graph": graph_snapshot,
        "graph_sha256": graph_digest,
        **_contract_payload(contract),
        "comfy_dir": str(resolved_comfy),
        "state_root": str(private_state),
        "receipt_path": str(receipt_path),
        "cancel_file": str(internal_cancel),
        "deadline_epoch": generation_deadline,
    }
    spec["worker_hmac_sha256"] = guarded._worker_spec_hmac(spec, token)
    guarded._write_exclusive_json(spec_path, spec)
    command = [
        sys.executable,
        str(safe_queue),
        "--min-free-mb",
        str(guarded.GPU_MIN_FREE_MIB),
        "--max-util",
        str(guarded.GPU_MAX_UTIL_PERCENT),
        "--interval",
        str(guarded.GPU_QUEUE_INTERVAL_SECONDS),
        "--comfy-url",
        "",
        "--",
        sys.executable,
        str(safe_worker),
        str(spec_path),
    ]
    try:
        return_code = _run_queue_process(
            command,
            environment=_worker_environment(token=token, state_root=private_state),
            cancel_path=internal_cancel,
            external_cancel=Path(cancel_file) if cancel_file is not None else None,
            prompt_id=prompt_id,
            deadline_epoch=generation_deadline,
            popen=popen,
            wall_clock=wall_clock,
            sleeper=sleeper,
        )
        if return_code == 11:
            _fail(
                "memory_recovery_failed",
                "本機可用記憶體仍不足；系統已先釋放 ComfyUI 模型，H3 任務尚未送出，"
                "不會重複生成。請稍後再試。",
            )
        if return_code == 10:
            _fail("pre_submit_rejected", "MiniMax H3 Web graph failed before submission.")
        if return_code != 0:
            _fail("outcome_uncertain", "MiniMax H3 Web graph outcome is uncertain.")
        try:
            raw = guarded._read_owner_file(
                receipt_path,
                max_bytes=guarded.MAX_SPEC_BYTES,
                code="receipt_invalid",
            )
            payload = guarded._safe_json_loads(raw, code="receipt_invalid")
        except guarded.RunnerError as error:
            raise WebGraphExecutionError(
                "receipt_invalid", "MiniMax H3 worker receipt is unavailable."
            ) from error
        return _validate_receipt(
            payload,
            prompt_id=prompt_id,
            graph_sha256=graph_digest,
            contract=contract,
            output_root=output_root,
        )
    finally:
        for path in (spec_path, receipt_path, internal_cancel):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "WebGraphExecutionError",
    "execute_web_graph",
]
