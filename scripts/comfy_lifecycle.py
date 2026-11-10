"""Cross-process Comfy prompt admission and maintenance gate (stdlib only).

Every cooperating POST /prompt holds a shared lock through the HTTP result.
Maintenance takes an exclusive lock through idle proof, durable history
checkpoint, restart and readiness. This does not authorize a restart by itself.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


class LifecycleError(RuntimeError):
    code = "comfy_lifecycle_failed"


class AdmissionBusy(LifecycleError):
    code = "comfy_admission_busy"


def lock_path() -> Path:
    path = Path(os.environ.get("CASTLE_COMFY_ADMISSION_LOCK") or
                Path.home() / ".local/share/castle-comfy-web/comfy-admission.lock")
    if not path.is_absolute():
        raise LifecycleError("Comfy admission lock must use an absolute path")
    return path


@contextmanager
def _lock(exclusive: bool, *, timeout_seconds: float, path: Path | None = None):
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise LifecycleError("Invalid Comfy maintenance timeout")
    target = path or lock_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise LifecycleError("Unsafe Comfy admission lock")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdmissionBusy("ComfyUI 正在維護或處理送出要求，請稍後再試。") from None
                time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def admission_lock(*, path: Path | None = None):
    """Fail before POST when maintenance has the exclusive gate."""
    return _lock(False, timeout_seconds=0.0, path=path)


def recycle_lock(*, timeout_seconds: float = 30.0, path: Path | None = None):
    return _lock(True, timeout_seconds=timeout_seconds, path=path)


def checkpoint_history(base_url: str, *, run_command=None) -> dict:
    """Fail closed unless the same checkout persists owned completion history."""
    parts = urlsplit(base_url)
    try:
        valid = (parts.scheme == "http" and parts.hostname is not None
                 and ipaddress.ip_address(parts.hostname).is_loopback
                 and parts.username is None and parts.password is None
                 and parts.path in {"", "/"} and not parts.query and not parts.fragment
                 and parts.port is not None)
    except ValueError:
        valid = False
    if not valid:
        raise LifecycleError("Invalid loopback Comfy checkpoint URL")
    script = Path(__file__).resolve().parent / "checkpoint_comfy_history.py"
    if not script.is_file():
        raise LifecycleError("Owned history checkpoint helper is unavailable")
    interpreter = os.environ.get("CASTLE_WEB_PYTHON") or sys.executable
    command = [interpreter, str(script), "--comfy-url", base_url.rstrip("/")]
    execute = run_command or subprocess.run
    # Disk-backed stdout prevents an unexpected helper response filling RAM.
    with tempfile.TemporaryFile() as output:
        try:
            result = execute(command, stdout=output, stderr=subprocess.DEVNULL,
                             check=False, timeout=120)
            output.seek(0)
            raw = output.read(65537)
            if result.returncode != 0 or len(raw) > 65536:
                raise LifecycleError("Owned history checkpoint failed")
            data = json.loads(raw)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise LifecycleError("Owned history checkpoint failed") from error
    if (type(data) is not dict or data.get("ok") is not True
            or any(key != "ok" and (type(value) is not int or value < 0)
                   for key, value in data.items())):
        raise LifecycleError("Owned history checkpoint response is invalid")
    return data
