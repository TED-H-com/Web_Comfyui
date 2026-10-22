from __future__ import annotations

import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path


WEBUI_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
DEFAULT_DATA_ROOT = Path(os.environ.get(
    "CASTLE_ORCH_DATA_ROOT",
    Path.home() / ".local/share/web-comfyui/orchestrator",
))
DEFAULT_CACHE_ROOT = Path(os.environ.get(
    "CASTLE_ORCH_CACHE_ROOT",
    Path.home() / ".cache/web-comfyui/orchestrator",
))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    artifact_root: Path
    work_root: Path
    lock_path: Path
    scheduler_pause_path: Path
    resource_reservations_path: Path
    python: str
    worker_interval: float
    gpu_queue_python: str
    gpu_queue_script: Path
    gpu_min_free_mb: int
    gpu_max_util: int
    gpu_queue_interval: int


def settings_from_env() -> Settings:
    data_root = DEFAULT_DATA_ROOT
    cache_root = DEFAULT_CACHE_ROOT
    db_path = Path(os.environ.get("CASTLE_ORCH_DB", data_root / "orchestrator.sqlite3"))
    artifact_root = Path(os.environ.get("CASTLE_ORCH_ARTIFACT_ROOT", data_root / "artifacts"))
    work_root = Path(os.environ.get("CASTLE_ORCH_WORK_ROOT", cache_root / "work"))
    lock_path = Path(os.environ.get("CASTLE_ORCH_LOCK", cache_root / "worker.lock"))
    scheduler_pause_path = Path(os.environ.get("CASTLE_ORCH_PAUSE_PATH", cache_root / "scheduler.paused"))
    resource_reservations_path = Path(os.environ.get("CASTLE_ORCH_RESOURCE_RESERVATIONS", cache_root / "resource_reservations.json"))
    try:
        interval = float(os.environ.get("CASTLE_ORCH_WORKER_INTERVAL", "1.0"))
    except ValueError:
        interval = 1.0
    try:
        gpu_min_free_mb = int(os.environ.get("CASTLE_GUARDED_MIN_FREE_MB", os.environ.get("CASTLE_GPU_MIN_FREE_MB", "26000")))
    except ValueError:
        gpu_min_free_mb = 26000
    try:
        gpu_max_util = int(os.environ.get("CASTLE_GUARDED_MAX_UTIL", os.environ.get("CASTLE_GPU_MAX_UTIL", "20")))
    except ValueError:
        gpu_max_util = 20
    try:
        gpu_queue_interval = int(os.environ.get("CASTLE_GUARDED_QUEUE_INTERVAL", os.environ.get("CASTLE_GPU_QUEUE_INTERVAL", "20")))
    except ValueError:
        gpu_queue_interval = 20
    return Settings(
        db_path=db_path,
        artifact_root=artifact_root,
        work_root=work_root,
        lock_path=lock_path,
        scheduler_pause_path=scheduler_pause_path,
        resource_reservations_path=resource_reservations_path,
        python=os.environ.get("CASTLE_ORCH_PYTHON", sys.executable),
        worker_interval=max(0.2, interval),
        gpu_queue_python=os.environ.get("CASTLE_GPU_QUEUE_PYTHON", "/usr/bin/python3"),
        gpu_queue_script=Path(os.environ.get("CASTLE_GPU_QUEUE_SCRIPT", Path.home() / ".local/share/web-comfyui/integrations/voice/gpu_queue.py")),
        gpu_min_free_mb=gpu_min_free_mb,
        gpu_max_util=gpu_max_util,
        gpu_queue_interval=gpu_queue_interval,
    )


def ensure_private_dir(path: Path) -> Path:
    """Create or harden one exact orchestrator-owned directory.

    ``chmod`` is intentionally applied through a descriptor opened with
    ``O_NOFOLLOW``.  That keeps a stale or hostile leaf symlink from making
    startup alter a directory outside the configured orchestrator roots.
    Parent directories created by ``parents=True`` are not recursively
    chmodded; only the exact path passed here is owned by this helper.
    """

    path = Path(path)
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise NotADirectoryError(str(path))
        if details.st_uid != os.geteuid():
            raise PermissionError(f"orchestrator directory is not owner-controlled: {path}")
        os.fchmod(descriptor, PRIVATE_DIR_MODE)
    finally:
        os.close(descriptor)
    return path


def harden_private_file(path: Path) -> Path:
    """Set an existing regular orchestrator file to owner-only access."""

    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise OSError(f"orchestrator private path is not a regular file: {path}")
        if details.st_uid != os.geteuid():
            raise PermissionError(f"orchestrator file is not owner-controlled: {path}")
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)
    return path


def atomic_write_private_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace one text file with owner-only permissions."""

    path = Path(path)
    ensure_private_dir(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        harden_private_file(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def open_private_text_append(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: str | None = None,
    buffering: int = -1,
) -> TextIOWrapper:
    """Open an owner-only regular text file for append without following links."""

    path = Path(path)
    ensure_private_dir(path.parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise OSError(f"orchestrator log path is not a regular file: {path}")
        if details.st_uid != os.geteuid():
            raise PermissionError(f"orchestrator log is not owner-controlled: {path}")
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        handle = os.fdopen(
            descriptor,
            "a",
            encoding=encoding,
            errors=errors,
            buffering=buffering,
        )
        descriptor = -1
        return handle
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def ensure_dirs(settings: Settings | None = None) -> Settings:
    settings = settings or settings_from_env()
    private_dirs = {
        settings.db_path.parent,
        settings.artifact_root,
        settings.work_root,
        settings.lock_path.parent,
        settings.scheduler_pause_path.parent,
        settings.resource_reservations_path.parent,
    }
    for path in sorted(private_dirs, key=lambda item: (len(item.parts), str(item))):
        ensure_private_dir(path)
    return settings
