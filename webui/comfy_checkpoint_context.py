"""Local-only lifecycle configuration; no provider credentials or user media."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import uuid
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[1]
_ENV_KEYS = frozenset({'COMFYUI_URL', 'COMFYUI_OUTPUT_DIR', 'CASTLE_CHARACTER_DIR',
    'CASTLE_PROVENANCE_KEY_PATH', 'CASTLE_COLAB_ROOT', 'CASTLE_COMFY_USERS_PATH'})


def context_path():
    lock = Path(os.environ.get('CASTLE_COMFY_ADMISSION_LOCK') or
        Path.home() / '.local/share/castle-comfy-web/comfy-admission.lock').expanduser()
    return lock.with_name(lock.name + '.context.json')


def loopback_url(value):
    if not isinstance(value, str):
        raise ValueError('Invalid local ComfyUI address')
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}):
        raise ValueError('Invalid local ComfyUI address')
    _ = parsed.port
    return value.rstrip('/')


def _absolute(value):
    if not isinstance(value, str) or not value or '\0' in value or len(value) > 4096 or not Path(value).is_absolute():
        raise ValueError('Invalid checkpoint configuration path')
    return value


def _process_start(pid):
    if type(pid) is not int or pid < 1:
        raise ValueError('Invalid checkpoint process')
    return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]


def write_checkpoint_context(namespace, colab_root):
    """Publish this running Web instance's resolved paths before starting workers."""
    root = Path(colab_root).expanduser().resolve()
    value = {'schema_version': 1, 'server_file': str(Path(namespace['__file__']).resolve()),
        'python': str(Path(sys.executable).resolve()),
        'web_pid': os.getpid(), 'web_start': _process_start(os.getpid()),
        'receipt_root': str(root.parent / 'compute-local-results'),
        'env': {'COMFYUI_URL': loopback_url(namespace['DEFAULT_COMFY']),
            'COMFYUI_OUTPUT_DIR': str(Path(namespace['COMFY_OUTPUT']).resolve()),
            'CASTLE_CHARACTER_DIR': str(Path(namespace['CHARACTER_LIBRARY']).resolve()),
            'CASTLE_PROVENANCE_KEY_PATH': str(Path(namespace['PROVENANCE_KEY_PATH']).resolve()),
            'CASTLE_COMFY_USERS_PATH': str(Path(namespace['USERS_PATH']).resolve()),
            'CASTLE_COLAB_ROOT': str(root)}}
    path = context_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value


def read_checkpoint_context():
    path = context_path()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 16384
                or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid())):
            raise ValueError('Checkpoint configuration is not private')
        value = json.load(stream)
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'server_file', 'python', 'receipt_root', 'env', 'web_pid', 'web_start'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['env'], dict) or set(value['env']) != _ENV_KEYS):
        raise ValueError('Invalid checkpoint configuration')
    if _process_start(value['web_pid']) != value['web_start']:
        raise ValueError('Checkpoint Web process is no longer active')
    for key in ('server_file', 'python', 'receipt_root'):
        _absolute(value[key])
    if Path(value['server_file']).name != 'server.py':
        raise ValueError('Invalid checkpoint server')
    for key, item in value['env'].items():
        loopback_url(item) if key == 'COMFYUI_URL' else _absolute(item)
    return value


@contextmanager
def prompt_admission():
    """Keep a web POST and its durable owner attribution atomic against recycle."""
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    from scripts.comfy_lifecycle import admission_lock, AdmissionBusy, LifecycleError
    try:
        with admission_lock():
            yield
    except (AdmissionBusy, LifecycleError):
        from compute_routing import ComputeRejected
        raise ComputeRejected('ComfyUI 正在整理記憶體，請稍後更新狀態；尚未送出任務。',
                              code='capacity_exhausted') from None
