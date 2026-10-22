"""Parent-side shared-GPU execution boundary for prepared H3 graphs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Mapping

from .h3_worker import H3QueueRequest
from .models import TaskReceipt, TaskState
from .provider_worker import DefiniteLocalFailure


ReceiptAdopter = Callable[[H3QueueRequest, Mapping[str, object]], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_LEGACY_MEMORY_CLEANUP_KEYS = {
    "attempted", "status", "error_code", "unload_models", "free_memory",
}
_RECYCLE_MEMORY_CLEANUP_KEYS = _LEGACY_MEMORY_CLEANUP_KEYS | {
    "method", "service", "queue_idle_before", "previous_main_pid",
    "previous_invocation_id", "current_main_pid", "current_invocation_id",
    "loopback_ready_after", "queue_idle_after", "memory_available_bytes_after",
    "minimum_memory_available_bytes",
}
_MIN_MEMORY_AVAILABLE_BYTES = 32 * 1024**3
_SYSTEMD_INVOCATION_ID = re.compile(r"[0-9a-f]{32}")
_LIFECYCLE_GRACE_SECONDS = 420
_WORKER_TOKEN_ENV = "CASTLE_H3_PROVIDER_WORKER_TOKEN"
_WORKER_STATE_ENV = "CASTLE_H3_PROVIDER_STATE_ROOT"
_SAFE_WORKER_TOKEN = re.compile(r"[0-9a-f]{64}")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", "strict")


def _worker_spec_hmac(payload: Mapping[str, object], token: str) -> str:
    if _SAFE_WORKER_TOKEN.fullmatch(token) is None:
        raise ValueError("provider worker token is invalid")
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "worker_hmac_sha256"
    }
    return hmac.new(
        token.encode("ascii"), _canonical(unsigned), hashlib.sha256
    ).hexdigest()


def _worker_environment(*, token: str, state_root: Path) -> dict[str, str]:
    """Build the provider worker's deliberately small environment."""

    if _SAFE_WORKER_TOKEN.fullmatch(token) is None:
        raise DefiniteLocalFailure("H3 provider worker token is invalid")
    allowed = (
        "HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER",
        "LOGNAME", "TMPDIR",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    runtime_dir = Path("/run/user") / str(os.geteuid())
    try:
        info = runtime_dir.stat()
    except OSError as error:
        raise DefiniteLocalFailure(
            "H3 provider systemd user bus is unavailable"
        ) from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise DefiniteLocalFailure("H3 provider systemd user bus is unsafe")
    environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
    environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    environment[_WORKER_TOKEN_ENV] = token
    environment[_WORKER_STATE_ENV] = str(state_root)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _write_cancel_sentinel(path: Path) -> None:
    """Create the signed worker's cancellation sentinel without replacing it."""

    parent = path.parent
    info = parent.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise DefiniteLocalFailure("H3 executor cancellation state is unsafe")
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        existing = path.lstat()
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_ISLNK(existing.st_mode)
            or existing.st_uid != os.geteuid()
            or stat.S_IMODE(existing.st_mode) != 0o600
            or existing.st_nlink != 1
        ):
            raise DefiniteLocalFailure(
                "H3 executor cancellation sentinel is unsafe"
            )
        return
    try:
        os.write(fd, b'{"cancelled":true}')
        os.fsync(fd)
    finally:
        os.close(fd)


def _run_process_group(
    command: list[str],
    *,
    cancel_path: Path,
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    """Run the queue wrapper without unlocking over uncertain GPU work.

    The signed child owns its generation deadline, cancellation, queue-idle
    proof, and controlled service recycle.  The parent must not hard-kill the
    process group that currently owns the shared GPU flock.
    """

    kwargs.pop("timeout", None)
    check = bool(kwargs.pop("check", False))
    interrupted_signal: int | None = None
    previous_handlers: dict[int, object] = {}

    def relay_interrupt(signum: int, _frame: object) -> None:
        nonlocal interrupted_signal
        interrupted_signal = interrupted_signal or signum
        try:
            _write_cancel_sentinel(cancel_path)
        except Exception:
            # Never let a failed relay unlock over an uncertain child.  Its
            # signed generation deadline remains the safe fallback.
            pass

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, relay_interrupt)
        except ValueError:
            for installed, handler in previous_handlers.items():
                try:
                    signal.signal(installed, handler)
                except ValueError:
                    pass
            previous_handlers.clear()
            break
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            start_new_session=True,
            close_fds=True,
            **kwargs,
        )
        while True:
            try:
                stdout, stderr = process.communicate()
                break
            except KeyboardInterrupt:
                interrupted_signal = interrupted_signal or signal.SIGINT
                try:
                    _write_cancel_sentinel(cancel_path)
                except Exception:
                    pass
                # Continue waiting.  The wrapper still owns the shared flock
                # while its worker cancels, proves queue idle, and recycles.
                continue
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass
    if process is None:
        raise RuntimeError("provider queue process did not start")
    completed = subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr
    )
    if interrupted_signal is not None:
        previous = previous_handlers.get(interrupted_signal, signal.SIG_DFL)
        if callable(previous):
            # Replay the parent's original policy only after the guarded child
            # has finished its complete post-submit lifecycle.
            previous(interrupted_signal, None)
        elif previous != signal.SIG_IGN:
            if interrupted_signal == signal.SIGINT:
                raise KeyboardInterrupt
            raise SystemExit(128 + interrupted_signal)
    if check and completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )
    return completed


def _valid_memory_cleanup(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    keys = frozenset(value)
    if keys not in {
        frozenset(_LEGACY_MEMORY_CLEANUP_KEYS),
        frozenset(_RECYCLE_MEMORY_CLEANUP_KEYS),
    }:
        return False
    if (
        value.get("attempted") is not True
        or value.get("unload_models") is not True
        or value.get("free_memory") is not True
    ):
        return False
    status = value.get("status")
    error_code = value.get("error_code")
    if keys == _LEGACY_MEMORY_CLEANUP_KEYS:
        if status == "accepted":
            return error_code is None
        return (
            status == "failed"
            and isinstance(error_code, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is not None
        )

    if (
        value.get("method") != "systemd_service_restart"
        or value.get("service") != "comfyui.service"
        or value.get("minimum_memory_available_bytes")
        != _MIN_MEMORY_AVAILABLE_BYTES
    ):
        return False
    if status == "failed":
        return (
            isinstance(error_code, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is not None
        )
    previous_pid = value.get("previous_main_pid")
    current_pid = value.get("current_main_pid")
    previous_invocation = value.get("previous_invocation_id")
    current_invocation = value.get("current_invocation_id")
    memory_after = value.get("memory_available_bytes_after")
    minimum_memory = value.get("minimum_memory_available_bytes")
    return all((
        status == "service_recycled",
        error_code is None,
        type(previous_pid) is int and previous_pid > 1,
        type(current_pid) is int and current_pid > 1,
        previous_pid != current_pid,
        isinstance(previous_invocation, str)
        and _SYSTEMD_INVOCATION_ID.fullmatch(previous_invocation) is not None,
        isinstance(current_invocation, str)
        and _SYSTEMD_INVOCATION_ID.fullmatch(current_invocation) is not None,
        previous_invocation != current_invocation,
        value.get("queue_idle_before") is True,
        value.get("loopback_ready_after") is True,
        value.get("queue_idle_after") is True,
        type(minimum_memory) is int
        and minimum_memory == _MIN_MEMORY_AVAILABLE_BYTES,
        type(memory_after) is int
        and type(minimum_memory) is int
        and memory_after >= minimum_memory,
    ))


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise DefiniteLocalFailure("H3 executor state directory is unsafe")
    return path.resolve(strict=True)


def _write_spec(path: Path, value: Mapping[str, object]) -> None:
    raw = _canonical(value)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


class H3GpuQueueExecutor:
    """Run one graph under gpu_queue and adopt output before returning completed."""

    def __init__(
        self,
        state_root: Path,
        queue_script: Path,
        worker_script: Path,
        adopter: ReceiptAdopter,
        *,
        runner: CommandRunner | None = None,
        timeout_seconds: int = 10_800,
    ) -> None:
        if not callable(adopter) or (runner is not None and not callable(runner)):
            raise TypeError("adopter and runner are required")
        if not isinstance(timeout_seconds, int) or not 600 <= timeout_seconds <= 86_400:
            raise ValueError("timeout_seconds must be between 600 and 86400")
        self._state_root = _private_directory(Path(state_root))
        self._queue_script = Path(queue_script).resolve(strict=True)
        self._worker_script = Path(worker_script).resolve(strict=True)
        self._adopter = adopter
        self._runner = runner
        self._timeout = timeout_seconds

    def __call__(self, request: H3QueueRequest) -> TaskReceipt:
        if not isinstance(request, H3QueueRequest):
            raise DefiniteLocalFailure("H3 queue request is invalid")
        if not _SAFE_NAME.fullmatch(request.upload_basename):
            raise DefiniteLocalFailure("H3 upload basename is invalid")
        nonce = secrets.token_hex(8)
        spec_path = self._state_root / f"spec-{nonce}.json"
        receipt_path = self._state_root / f"receipt-{nonce}.json"
        cancel_path = self._state_root / f"cancel-{nonce}.json"
        prompt_uuid = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"castle://minimax-h3/{request.client_request_id}",
        ))
        spec = {
            "schema_version": "castle.h3-gpu-worker.v1",
            "task_id": request.task_id,
            "client_request_id": request.client_request_id,
            "variant_id": request.variant_id,
            "graph": request.graph,
            "graph_sha256": request.graph_sha256,
            "source_path": str(request.source_path),
            "source_sha256": request.source_sha256,
            "upload_basename": request.upload_basename,
            "prompt_id": prompt_uuid,
            "receipt_path": str(receipt_path),
            "cancel_file": str(cancel_path),
            "state_root": str(self._state_root),
            "generation_deadline_epoch": (
                time.time() + self._timeout - _LIFECYCLE_GRACE_SECONDS
            ),
        }
        token = secrets.token_hex(32)
        spec["worker_hmac_sha256"] = _worker_spec_hmac(spec, token)
        _write_spec(spec_path, spec)
        command = [
            sys.executable,
            str(self._queue_script),
            "--min-free-mb", "26000",
            "--max-util", "20",
            "--interval", "20",
            "--comfy-url", "",
            "--",
            sys.executable,
            str(self._worker_script),
            str(spec_path),
        ]
        try:
            run_kwargs: dict[str, object] = {
                "cwd": str(self._worker_script.parents[1]),
                "env": _worker_environment(
                    token=token,
                    state_root=self._state_root,
                ),
                "text": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "check": False,
            }
            if self._runner is None:
                completed = _run_process_group(
                    command,
                    cancel_path=cancel_path,
                    **run_kwargs,
                )
            else:
                completed = self._runner(command, **run_kwargs)
            if completed.returncode != 0:
                # The child exits 10 only for a proven pre-submit failure.
                if completed.returncode == 10:
                    raise DefiniteLocalFailure("H3 GPU preflight rejected the task")
                raise RuntimeError("H3 GPU worker outcome is uncertain")
            try:
                raw = receipt_path.read_bytes()
                receipt = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError("H3 GPU receipt is unavailable") from error
            expected = {
                "schema_version", "task_id", "client_request_id", "variant_id",
                "prompt_id", "graph_sha256", "output_path", "output_sha256",
                "byte_size", "mime_type", "memory_cleanup",
            }
            if (
                not isinstance(receipt, dict)
                or set(receipt) != expected
                or receipt.get("schema_version") != "castle.h3-gpu-receipt.v1"
                or receipt.get("task_id") != request.task_id
                or receipt.get("client_request_id") != request.client_request_id
                or receipt.get("variant_id") != request.variant_id
                or receipt.get("prompt_id") != prompt_uuid
                or receipt.get("graph_sha256") != request.graph_sha256
                or receipt.get("mime_type") != "video/mp4"
                or not isinstance(receipt.get("byte_size"), int)
                or receipt.get("byte_size", 0) <= 0
                or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("output_sha256") or "")) is None
                or not _valid_memory_cleanup(receipt.get("memory_cleanup"))
            ):
                raise RuntimeError("H3 GPU receipt does not match its durable task")
            self._adopter(request, receipt)
            return TaskReceipt(
                provider_id="local_comfy",
                client_request_id=request.client_request_id,
                state=TaskState.COMPLETED,
                external_task_id=prompt_uuid,
                detail="MiniMAX H3 output was verified and adopted.",
                outputs=({
                    "sha256": receipt["output_sha256"],
                    "byte_size": receipt["byte_size"],
                    "mime_type": receipt["mime_type"],
                },),
            )
        finally:
            for path in (spec_path, receipt_path, cancel_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


__all__ = ["CommandRunner", "H3GpuQueueExecutor", "ReceiptAdopter"]
