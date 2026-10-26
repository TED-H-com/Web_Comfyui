"""Public environment names, translated at process startup for legacy modules."""
from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

_PUBLIC_ONLY = {'WEB_COMFYUI_DATA_DIR', 'WEB_COMFYUI_CACHE_DIR', 'WEB_COMFYUI_TMP_DIR'}
_SPECIAL = {'WEB_COMFYUI_HOST': 'CASTLE_COMFY_WEB_HOST', 'WEB_COMFYUI_PORT': 'CASTLE_COMFY_WEB_PORT'}


def legacy_name(name: str) -> str:
    if name in _SPECIAL:
        return _SPECIAL[name]
    if name.startswith('WEB_COMFYUI_') and name not in _PUBLIC_ONLY:
        return 'CASTLE_' + name.removeprefix('WEB_COMFYUI_')
    return name


def public_name(name: str) -> str:
    reverse = {value: key for key, value in _SPECIAL.items()}
    return reverse.get(name, 'WEB_COMFYUI_' + name.removeprefix('CASTLE_') if name.startswith('CASTLE_') else name)


def apply_environment_aliases(environ: MutableMapping[str, str] | None = None) -> None:
    """Public names win, including empty values; do not log configuration values."""
    env = os.environ if environ is None else environ
    for key, value in list(env.items()):
        if key.startswith('WEB_COMFYUI_') and key not in _PUBLIC_ONLY:
            env[legacy_name(key)] = value


def default_gpu_lock(home: Path | None = None) -> Path:
    """Keep one stable default, even before an older worker creates its lock file.

    A different name requires an explicit shared configuration for all workers;
    file existence cannot safely distinguish a new installation from an idle one.
    """
    base = (Path.home() if home is None else home) / '.cache/web-comfyui-gpu-queue'
    return base / 'rtx5090.lock'
