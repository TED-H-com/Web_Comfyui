"""Nonblocking exclusion compatible with Castle's existing RTX 5090 queue."""

from __future__ import annotations

import errno
if '.' in (__package__ or ''):
    from .. import file_lock as fcntl
else:  # top-level webui package layout
    import file_lock as fcntl
import os
import stat
from pathlib import Path
from types import TracebackType

from .errors import SceneArtError, fail
from .secure_path import close_descriptors, open_absolute_directory
try:
    from ..config_env import default_gpu_lock
except ImportError:  # server.py also supports top-level webui modules
    from config_env import default_gpu_lock


PRODUCTION_GPU_LOCK = Path(os.environ.get('WEB_COMFYUI_GPU_LOCK_PATH',
                           os.environ.get('CASTLE_GPU_LOCK_PATH', str(default_gpu_lock())))).expanduser()


class SharedGpuLock:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute() or "\x00" in os.fspath(path):
            fail("gpu_lock_invalid", "Shared GPU lock path is invalid")
        self.path = path
        self._descriptor: int | None = None

    def try_acquire(self) -> bool:
        if self._descriptor is not None:
            return True
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent = self.path.parent.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or stat.S_ISLNK(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077
            ):
                fail("gpu_lock_unsafe", "Shared GPU lock directory must be owner-private")
            if self.path.is_relative_to(Path.home()):
                current = self.path.parent
                while True:
                    ancestor = current.lstat()
                    if (
                        not stat.S_ISDIR(ancestor.st_mode)
                        or stat.S_ISLNK(ancestor.st_mode)
                        or ancestor.st_uid != os.geteuid()
                        or ancestor.st_mode & 0o022
                    ):
                        fail("gpu_lock_unsafe", "Shared GPU lock ancestry is unsafe")
                    if current == Path.home():
                        break
                    current = current.parent
            parent_descriptors = open_absolute_directory(
                self.path.parent,
                code="gpu_lock_unavailable",
                message="Shared GPU lock directory is unavailable",
            )
            parent_descriptor = parent_descriptors[-1]
            try:
                bound_parent = os.fstat(parent_descriptor)
                if (
                    bound_parent.st_uid != os.geteuid()
                    or bound_parent.st_mode & 0o077
                ):
                    fail("gpu_lock_unsafe", "Shared GPU lock directory must be owner-private")
                descriptor = os.open(
                    self.path.name,
                    os.O_RDWR
                    | os.O_CREAT
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    0o600,
                    dir_fd=parent_descriptor,
                )
            finally:
                close_descriptors(parent_descriptors)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                os.close(descriptor)
                fail("gpu_lock_unsafe", "Shared GPU lock file must be owner-private")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(descriptor)
                if error.errno in {errno.EACCES, errno.EAGAIN}:
                    return False
                raise
            self._descriptor = descriptor
            return True
        except SceneArtError:
            raise
        except OSError as error:
            raise SceneArtError("gpu_lock_unavailable", "Shared GPU lock is unavailable") from error

    def release(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "SharedGpuLock":
        if not self.try_acquire():
            fail("gpu_lock_busy", "Shared GPU lock is busy")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
