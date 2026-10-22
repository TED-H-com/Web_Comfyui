#!/usr/bin/env python3
"""Execute one server-compiled H3 graph under Castle's shared GPU lock.

Exit 10 is reserved for failures proven to happen before ComfyUI submission.
Every failure after the POST boundary is uncertain and exits 1.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path
from typing import Mapping


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEBUI = ROOT / "webui"
for item in (str(HERE), str(WEBUI)):
    if item not in sys.path:
        sys.path.insert(0, item)

import run_native as native_runner  # noqa: E402


SPEC_KEYS = {
    "schema_version", "task_id", "client_request_id", "variant_id", "graph",
    "graph_sha256", "source_path", "source_sha256", "upload_basename",
    "prompt_id", "receipt_path", "generation_deadline_epoch", "cancel_file",
    "state_root", "worker_hmac_sha256",
}
SAFE_UPLOAD = re.compile(r"castle_h3_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)")
SAFE_OUTPUT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.mp4")
SAFE_SPEC = re.compile(r"spec-([0-9a-f]{16})\.json")
SAFE_SHA256 = re.compile(r"[0-9a-f]{64}")
SAFE_TOKEN = re.compile(r"[0-9a-f]{64}")
WORKER_TOKEN_ENV = "CASTLE_H3_PROVIDER_WORKER_TOKEN"
WORKER_STATE_ENV = "CASTLE_H3_PROVIDER_STATE_ROOT"


class PreSubmitError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def worker_spec_hmac(payload: Mapping[str, object], token: str) -> str:
    if SAFE_TOKEN.fullmatch(token) is None:
        raise PreSubmitError("private specification token is invalid")
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "worker_hmac_sha256"
    }
    return hmac.new(
        token.encode("ascii"), canonical(unsigned), hashlib.sha256
    ).hexdigest()


def owner_file(path: Path, *, max_bytes: int) -> bytes:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1 or info.st_size <= 0 or info.st_size > max_bytes
    ):
        raise PreSubmitError("unsafe private specification")
    return path.read_bytes()


def stable_sha(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PreSubmitError("source is not a stable regular file")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(fd, 8 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise PreSubmitError("source changed while hashing")
        return size, digest.hexdigest()
    finally:
        os.close(fd)


def cancellation_requested(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise PreSubmitError("cancellation state is unreadable") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise PreSubmitError("cancellation state is unsafe")
    return True


def write_receipt(path: Path, payload: Mapping[str, object]) -> None:
    if path.parent.lstat().st_uid != os.geteuid():
        raise RuntimeError("receipt directory ownership changed")
    raw = canonical(payload)
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


def load_spec(path: Path, *, expected_state_root: Path, token: str) -> dict:
    try:
        state_info = expected_state_root.lstat()
        state_root = expected_state_root.resolve(strict=True)
        lexical = Path(os.path.abspath(os.fspath(path)))
    except OSError as error:
        raise PreSubmitError("private specification path is unreadable") from error
    if (
        stat.S_ISLNK(state_info.st_mode)
        or not stat.S_ISDIR(state_info.st_mode)
        or state_info.st_uid != os.geteuid()
        or stat.S_IMODE(state_info.st_mode) != 0o700
        or Path(os.path.abspath(os.fspath(expected_state_root))) != state_root
    ):
        raise PreSubmitError("private specification state root is unsafe")
    match = SAFE_SPEC.fullmatch(lexical.name)
    if lexical.parent != state_root or match is None:
        raise PreSubmitError("private specification path escaped private state")
    nonce = match.group(1)
    try:
        value = json.loads(owner_file(lexical, max_bytes=2 * 1024 * 1024).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreSubmitError("private specification is unreadable") from error
    if not isinstance(value, dict) or set(value) != SPEC_KEYS:
        raise PreSubmitError("private specification schema changed")
    if value.get("schema_version") != "castle.h3-gpu-worker.v1":
        raise PreSubmitError("private specification version changed")
    signature = value.get("worker_hmac_sha256")
    if (
        not isinstance(signature, str)
        or SAFE_SHA256.fullmatch(signature) is None
        or not secrets.compare_digest(signature, worker_spec_hmac(value, token))
    ):
        raise PreSubmitError("private specification is not authentic")
    if value.get("state_root") != str(state_root):
        raise PreSubmitError("private specification state root changed")
    if value.get("receipt_path") != str(state_root / f"receipt-{nonce}.json"):
        raise PreSubmitError("receipt path escaped private state")
    if value.get("cancel_file") != str(state_root / f"cancel-{nonce}.json"):
        raise PreSubmitError("cancellation path escaped private state")
    graph = value.get("graph")
    if not isinstance(graph, dict) or hashlib.sha256(canonical(graph)).hexdigest() != value.get("graph_sha256"):
        raise PreSubmitError("compiled graph identity changed")
    upload = value.get("upload_basename")
    if not isinstance(upload, str) or SAFE_UPLOAD.fullmatch(upload) is None:
        raise PreSubmitError("upload basename is invalid")
    try:
        prompt = str(uuid.UUID(str(value.get("prompt_id"))))
    except ValueError as error:
        raise PreSubmitError("prompt identifier is invalid") from error
    if prompt != value.get("prompt_id"):
        raise PreSubmitError("prompt identifier is not canonical")
    deadline = value.get("generation_deadline_epoch")
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not time.time() < float(deadline) <= time.time() + 86_400
    ):
        raise PreSubmitError("generation deadline is invalid")
    receipt = Path(str(value.get("receipt_path"))).resolve(strict=False)
    if receipt.parent != state_root or receipt.exists():
        raise PreSubmitError("receipt path escaped private state")
    return value


def install_source(spec: Mapping[str, object], input_root: Path) -> None:
    source = Path(str(spec["source_path"])).resolve(strict=True)
    size, digest = stable_sha(source)
    if size <= 0 or digest != spec["source_sha256"]:
        raise PreSubmitError("source bytes changed")
    target = input_root / str(spec["upload_basename"])
    if target.exists():
        existing_size, existing_digest = stable_sha(target)
        if (existing_size, existing_digest) != (size, digest):
            raise PreSubmitError("immutable ComfyUI upload name conflicts")
        return
    temporary = input_root / f".{target.name}.{os.getpid()}.tmp"
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            os.chmod(temporary, 0o600)
            shutil.copyfileobj(incoming, outgoing, 8 * 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        copied_size, copied_digest = stable_sha(temporary)
        if (copied_size, copied_digest) != (size, digest):
            raise PreSubmitError("source copy verification failed")
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def bind_output(history: Mapping[str, object], graph: Mapping[str, object], output_root: Path) -> Path:
    try:
        prefix = graph["14"]["inputs"]["filename_prefix"]  # type: ignore[index]
        metadata = history["outputs"]["14"]["images"]  # type: ignore[index]
    except (KeyError, TypeError) as error:
        raise RuntimeError("H3 output metadata is missing") from error
    if not isinstance(prefix, str) or not isinstance(metadata, list) or len(metadata) != 1:
        raise RuntimeError("H3 output cardinality changed")
    item = metadata[0]
    expected_dir, token = prefix.rsplit("/", 1)
    if (
        not isinstance(item, dict) or item.get("type") != "output"
        or item.get("subfolder") != expected_dir
        or not isinstance(item.get("filename"), str)
        or SAFE_OUTPUT.fullmatch(item["filename"]) is None
        or re.fullmatch(re.escape(token) + r"_[0-9]{5}_\.mp4", item["filename"]) is None
    ):
        raise RuntimeError("H3 output escaped its server namespace")
    root = output_root.resolve(strict=True)
    result = (root / expected_dir / item["filename"]).resolve(strict=True)
    if os.path.commonpath((str(root), str(result))) != str(root):
        raise RuntimeError("H3 output escaped ComfyUI output root")
    return result


def run(spec_path: Path, *, token: str, state_root: Path) -> None:
    spec = load_spec(spec_path, expected_state_root=state_root, token=token)
    try:
        spec_path.unlink()
    except OSError as error:
        raise PreSubmitError("private specification could not be consumed") from error
    cancel_file = Path(str(spec["cancel_file"]))
    if cancellation_requested(cancel_file):
        raise PreSubmitError("H3 provider task was cancelled before submission")
    graph = spec["graph"]
    comfy = Path.home() / "comfyui"
    input_root = (comfy / "input").resolve(strict=True)
    output_root = (comfy / "output").resolve(strict=True)
    install_source(spec, input_root)
    base = native_runner.validate_loopback_url(native_runner.DEFAULT_COMFY_URL)
    native_runner.verify_loopback_listener(base)
    native_runner.wait_for_memory_headroom()
    native_runner.require_gpu_headroom(native_runner.read_gpu_snapshot())
    native_runner.verify_object_info(native_runner.request_json(base, "/object_info"), graph)
    native_runner.require_queue_idle(native_runner.request_json(base, "/queue"))

    submission_attempted = False
    memory_cleanup = native_runner.memory_cleanup_not_attempted()
    receipt_payload: dict[str, object] | None = None
    try:
        if (
            cancellation_requested(cancel_file)
            or time.time() >= float(spec["generation_deadline_epoch"])
        ):
            raise PreSubmitError(
                "H3 provider task was cancelled before submission"
            )
        # Recheck live host RAM at the irreversible POST boundary.  The
        # blocking gate above may have succeeded before object-info and queue
        # preflights, while another local process consumed the headroom.
        native_runner.require_memory_headroom(
            native_runner.read_memory_snapshot()
        )
        # From this assignment onward a transport error may hide an accepted POST.
        submission_attempted = True
        prompt_id = native_runner.submit_prompt(
            base, graph, client_id="castle-provider-h3", prompt_id=str(spec["prompt_id"])
        )
        history = native_runner.wait_for_history(
            base,
            prompt_id,
            deadline_epoch=float(spec["generation_deadline_epoch"]),
            cancel_file=str(cancel_file),
        )
        output = bind_output(history, graph, output_root)
        try:
            generator = graph["5"]  # type: ignore[index]
            width = int(generator["inputs"]["width"])  # type: ignore[index]
            height = int(generator["inputs"]["height"])  # type: ignore[index]
            frames = int(generator["inputs"]["length"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("H3 output profile is not bound to its graph") from error
        native_runner.probe_output(output, resolution=(width, height), frames=frames)
        byte_size, digest = stable_sha(output)
        if byte_size <= 0:
            raise RuntimeError("H3 output is empty")
        receipt_payload = {
            "schema_version": "castle.h3-gpu-receipt.v1",
            "task_id": spec["task_id"],
            "client_request_id": spec["client_request_id"],
            "variant_id": spec["variant_id"],
            "prompt_id": prompt_id,
            "graph_sha256": spec["graph_sha256"],
            "output_path": str(output),
            "output_sha256": digest,
            "byte_size": byte_size,
            "mime_type": "video/mp4",
        }
    except BaseException as error:
        if submission_attempted:
            native_runner.cancel_owned_prompt(base, str(spec["prompt_id"]))
            raise
        raise PreSubmitError("ComfyUI submission was definitely not accepted") from error
    finally:
        if submission_attempted:
            native_runner.wait_for_comfy_queue_idle(
                base, timeout_seconds=None
            )
            memory_cleanup = native_runner.release_comfy_memory(base)
    if receipt_payload is None:
        raise RuntimeError("H3 GPU receipt payload is unavailable")
    receipt_payload["memory_cleanup"] = memory_cleanup
    write_receipt(Path(str(spec["receipt_path"])), receipt_payload)


def main() -> int:
    token = os.environ.pop(WORKER_TOKEN_ENV, "")
    state_value = os.environ.pop(WORKER_STATE_ENV, "")
    if SAFE_TOKEN.fullmatch(token) is None or not state_value:
        return 10
    if len(sys.argv) != 2:
        return 10
    try:
        run(Path(sys.argv[1]), token=token, state_root=Path(state_value))
        return 0
    except PreSubmitError:
        return 10
    except BaseException:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
