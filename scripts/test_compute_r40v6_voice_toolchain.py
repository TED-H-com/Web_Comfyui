"""Voice build toolchain: gcc overrides the interpreter sysconfig, pins, and PATH."""
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from scripts import colab_session_voice as voice
from scripts.colab_session_diagnostics import failure_diagnostics

FIVE = 'cython==3.2.5\nnumpy==1.26.4\npackaging==26.3\nsetuptools==70.2.0\nwheel==0.48.0\n'
VOICE_ID = 'ee91ed788074a2a51bfe0c9ccdbef26979a3791c0b614a27f7739bb4be80d4c0'
COMPILER = {'CC': 'gcc -pthread', 'CXX': 'g++ -pthread', 'AR': 'ar'}
R40V5_REGISTRY_SHA256 = '7f4a9ddec9bcc769edd3f4032d0b511e074034339d4864dffa4c408ead914f0c'
R40V5_VOICE_BUILD_CONSTRAINT = 'setuptools==70.2.0\n'


def canonical_sha256(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def arm_tools(tmp_path, monkeypatch):
    registry = json.loads(voice.REGISTRY.read_text())
    selected = tmp_path / 'selected-python'
    selected.write_bytes(b'interpreter')
    real_sha = voice.sha

    def sha(path, _selected=selected, _hash=registry['interpreter']['sha256'], _real=real_sha):
        if Path(path) == _selected:
            return _hash
        return _real(path)

    monkeypatch.setattr(voice, 'sha', sha)
    monkeypatch.setattr(voice, 'provision_python', lambda *args: str(selected))
    monkeypatch.setattr(voice, 'verify_python', lambda *args: None)
    tool_python = tmp_path / 'voice-tools' / 'venv' / 'bin' / 'python'
    tool_python.parent.mkdir(parents=True, exist_ok=True)
    tool_python.write_bytes(b'tool')
    (tmp_path / 'voice-tools' / 'tools-ready').touch()
    return registry


def install_args(tmp_path, plan, label):
    lock = tmp_path / (label + '-constraints.txt')
    lock.write_text('\n'.join(plan['runtime_lock'][label]) + '\n')
    python = tmp_path / (label + '-venv') / 'bin' / 'python'
    return [str(python), '-m', 'pip', 'install', '-r', str(lock)]


def pip_installs(calls):
    return [(args, kw) for args, kw in calls if 'pip' in args and 'install' in args]


def stubs(directory):
    directory.mkdir()
    for name in ('gcc', 'g++', 'ar'):
        path = directory / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    return directory


def test_compiler_env_overrides_inherited_clang(tmp_path, monkeypatch):
    registry = arm_tools(tmp_path, monkeypatch)
    monkeypatch.setenv('CC', 'clang')
    monkeypatch.setenv('CXX', 'clang++')
    monkeypatch.setenv('AR', 'llvm-ar')
    inherited = {'CC': 'clang', 'CXX': 'clang++', 'AR': 'llvm-ar'}
    _selected, _tool, options = voice.tools(tmp_path, registry, lambda *args, **kw: pytest.fail('no run'), {'env': inherited})
    for key, value in COMPILER.items():
        assert options['env'][key] == value




def test_registry_pin_matches_runtime_dists_and_base_differs_only_there():
    work = json.loads(voice.REGISTRY.read_text())
    voice_env = work['environments'][VOICE_ID]
    assert voice_env['provenance']['label'] == 'local voice venv'
    assert voice_env['build_constraint'] == FIVE
    pins = dict(line.split('==') for line in FIVE.splitlines())
    for name in ('cython', 'numpy', 'setuptools'):
        assert voice_env['dists'][name] == pins[name]
    vox = next(row for row in work['environments'].values() if row['provenance']['label'] == 'local voxcpm venv')
    assert vox['build_constraint'] == 'setuptools==70.2.0\n'
    restored = copy.deepcopy(work)
    restored['environments'][VOICE_ID]['build_constraint'] = R40V5_VOICE_BUILD_CONSTRAINT
    assert canonical_sha256(restored) == R40V5_REGISTRY_SHA256




def test_public_diagnostics_report_build_toolchain():
    error = voice.VoiceEnvironmentMismatch('voice_env_mismatch')
    error.substage = 'build_toolchain'
    error.command_label = 'pip_voice'
    result = failure_diagnostics(error, 'pip_voice')
    assert result['substage'] == 'build_toolchain' and result['command_label'] == 'pip_voice'


def toolchain_case(tmp_path, monkeypatch, bin_dir):
    registry = json.loads(voice.REGISTRY.read_text())
    pins = ['unit-test==1']
    plan = {'feature': 'voxcpm-tts', 'runtime_lock': {'voxcpm': pins}}
    (tmp_path / 'voxcpm-constraints.txt').write_text('\n'.join(pins) + '\n')
    expected = {'last': [], 'build_constraint': ''}
    monkeypatch.setattr(voice, 'entry', lambda *args: (registry, expected, '\n'.join(pins) + '\n'))
    monkeypatch.setattr(voice, 'tools', lambda *args: ('test-python', 'test-tool', {'env': {'PATH': str(bin_dir)}}))
    monkeypatch.setattr(voice, 'verify_python', lambda *args: None)
    calls = []
    args = [str(tmp_path / 'voxcpm-venv/bin/python'), '-m', 'pip', 'install', '-r', str(tmp_path / 'voxcpm-constraints.txt')]
    return plan, args, calls


@pytest.mark.parametrize('present', [(), ('gcc',), ('gcc', 'g++'), ('g++', 'ar'), ('gcc', 'ar')])
def test_missing_compiler_tool_blocks_before_pip(tmp_path, monkeypatch, present):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name in present:
        tool = bin_dir / name
        tool.write_text('#!/bin/sh\nexit 0\n')
        tool.chmod(0o755)
    plan, args, calls = toolchain_case(tmp_path, monkeypatch, bin_dir)
    with pytest.raises(voice.VoiceEnvironmentMismatch, match='voice_env_mismatch') as error:
        voice.run_command(plan, tmp_path, args, lambda args, **kw: calls.append(args), {})
    assert error.value.substage == 'build_toolchain' and error.value.command_label == 'pip_voxcpm'
    assert calls == []


def test_complete_compiler_toolchain_reaches_pip(tmp_path, monkeypatch):
    plan, args, calls = toolchain_case(tmp_path, monkeypatch, stubs(tmp_path / 'bin'))
    voice.run_command(plan, tmp_path, args, lambda args, **kw: calls.append(args), {})
    assert any('pip' in call and 'install' in call for call in calls)




