from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from .reel_proxy_contract import canonical_proxy_ffmpeg_command


# This module is the complete, deliberately narrow output-producing surface
# fingerprinted by ``canonical_proxy_implementation_hash``.  Bump
# ``PROXY_EXECUTOR_SURFACE_VERSION`` in the contract when a shared callback's
# semantics change in a way that can alter proxy bytes.


def execute_canonical_proxy(
    *,
    source: Path,
    output: Path,
    work_dir: Path,
    cancel_file: Path,
    is_video: bool,
    ffmpeg_bin: str | None,
    run_cpu_child: Callable[..., str],
) -> None:
    """Produce one canonical proxy after the caller has validated lineage."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if cancel_file.exists():
        raise KeyboardInterrupt()

    if is_video:
        command = canonical_proxy_ffmpeg_command(
            source, output, ffmpeg_bin=ffmpeg_bin)
        run_cpu_child(
            command,
            work_dir,
            cancel_file,
            error_label="ffmpeg proxy generation",
        )
        return

    shutil.copy2(source, output)
