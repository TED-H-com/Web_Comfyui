from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webui.config_env import apply_environment_aliases, default_gpu_lock


def test_public_names_win_and_empty_values_are_explicit():
    env = {'CASTLE_COMFY_WEB_PORT': '8000', 'WEB_COMFYUI_PORT': '8191',
           'CASTLE_OPERATOR_EMAIL': 'old@example.invalid', 'WEB_COMFYUI_OPERATOR_EMAIL': '',
           'WEB_COMFYUI_H3_MAX_STEPS': '4', 'WEB_COMFYUI_DATA_DIR': 'local-data'}
    apply_environment_aliases(env)
    assert env['CASTLE_COMFY_WEB_PORT'] == '8191'
    assert env['CASTLE_OPERATOR_EMAIL'] == ''
    assert env['CASTLE_H3_MAX_STEPS'] == '4'
    assert 'CASTLE_DATA_DIR' not in env


def test_legacy_only_configuration_is_unchanged():
    env = {'CASTLE_COMFY_WEB_PORT': '8192'}
    apply_environment_aliases(env)
    assert env == {'CASTLE_COMFY_WEB_PORT': '8192'}


def test_gpu_lock_keeps_existing_queue_path(tmp_path):
    assert default_gpu_lock(tmp_path).name == 'rtx5090.lock'
    legacy = tmp_path / '.cache/web-comfyui-gpu-queue/rtx5090.lock'
    legacy.parent.mkdir(parents=True)
    legacy.touch()
    assert default_gpu_lock(tmp_path) == legacy


def test_default_gpu_lock_does_not_switch_when_legacy_worker_starts(tmp_path):
    first = default_gpu_lock(tmp_path)
    first.parent.mkdir(parents=True)
    first.touch()
    legacy = first.with_name('rtx5090.lock')
    legacy.touch()
    assert default_gpu_lock(tmp_path) == first
