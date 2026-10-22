"""Small retained-dirfd helpers for security-sensitive local paths."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import SceneArtError, fail


DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_DIRECTORY", 0)
)
READ_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def normalized_absolute(path: Path, *, code: str, message: str) -> Path:
    if not path.is_absolute() or "\x00" in os.fspath(path):
        fail(code, message)
    return Path(os.path.abspath(os.fspath(path)))


def open_absolute_directory(
    path: Path,
    *,
    code: str,
    message: str,
    definite_pre_submit: bool = True,
) -> list[int]:
    """Walk from `/`, retaining every no-follow directory descriptor."""

    path = normalized_absolute(path, code=code, message=message)
    descriptors: list[int] = []
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
        descriptors.append(descriptor)
        for component in path.parts[1:]:
            if component in {"", ".", ".."} or "/" in component or "\x00" in component:
                fail(code, message, definite_pre_submit=definite_pre_submit)
            descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            descriptors.append(descriptor)
        return descriptors
    except SceneArtError:
        for opened in reversed(descriptors):
            os.close(opened)
        raise
    except OSError as error:
        for opened in reversed(descriptors):
            os.close(opened)
        raise SceneArtError(code, message, definite_pre_submit=definite_pre_submit) from error


def close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)
