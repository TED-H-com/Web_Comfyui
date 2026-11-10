"""Offline contract tests; no dependency install, GPU or remote account access."""
import copy
import base64
import hashlib
import json
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
import time
import urllib.error
import uuid
import wave
import zlib
import zipfile

import pytest

from scripts.colab_remote import remote_job as R


def manifest(kind='video'):
    job = {'id': 'output', 'type': kind, 'prompt': 'A synthetic paper kite in a blue sky.'}
    if kind == 'video':
        job['mode'] = 'text'
    return {'schema_version': 1, 'job_id': str(uuid.uuid4()), 'deadline_epoch': time.time() + 1200,
            'jobs': [job]}


def make_runner(tmp_path, value=None, transport=None):
    value = value or manifest()
    root = tmp_path / value['job_id']
    root.mkdir(exist_ok=True)
    (root / 'manifest.json').write_text(json.dumps(value))
    runner = R.Runner(root, transport=transport)
    runner.current = 'output'
    runner.state['jobs']['output'] = {'type': value['jobs'][0]['type'], 'status': 'running'}
    return runner


def graph():
    return {'1': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'synthetic'}}}


def test_normalized_manifest_is_deterministic_and_does_not_mutate(tmp_path):
    value = manifest()
    before = copy.deepcopy(value)
    first = R.validate_manifest(value, tmp_path)
    second = R.validate_manifest(value, tmp_path)
    assert first == second
    assert value == before
    assert type(first['jobs'][0]['seed']) is int
    assert first['jobs'][0]['resolution'] == '480P'


def test_inputs_are_bound_to_bytes_and_prior_output_types(tmp_path):
    value = manifest('image')
    target = tmp_path / 'input' / 'source.png'
    target.parent.mkdir()
    target.write_bytes(b'synthetic bytes; parser does not claim media decode')
    value['input_files'] = [{'path': 'input/source.png', 'size': target.stat().st_size, 'sha256': R.file_digest(target)}]
    value['jobs'][0]['inputs'] = ['input/source.png']
    value['jobs'].append({'id': 'video', 'type': 'video', 'mode': 'frames', 'first': '@output', 'prompt': 'A kite moves.'})
    R.validate_manifest(value, tmp_path)
    target.write_bytes(b'tampered')
    with pytest.raises(R.ValidationError, match='immutable'):
        R.validate_manifest(value, tmp_path)
    R.validate_manifest(value, tmp_path, check_files=False)
    value['jobs'][1]['first'] = '@future'
    with pytest.raises(R.ValidationError, match='earlier'):
        R.validate_manifest(value, tmp_path, check_files=False)


@pytest.mark.parametrize('path', ['../source.png', '/tmp/source.png', 'input/../source.png', 'input/a\\b.png', 'input/a..png'])
def test_invalid_input_paths_are_rejected_even_without_files(tmp_path, path):
    value = manifest('image')
    value['input_files'] = [{'path': path, 'size': 1, 'sha256': '0' * 64}]
    value['jobs'][0]['inputs'] = [path]
    with pytest.raises(R.ValidationError):
        R.validate_manifest(value, tmp_path, check_files=False)


def test_symlink_input_and_missing_inventory_rejected(tmp_path):
    value = manifest('image')
    (tmp_path / 'input').mkdir()
    other = tmp_path / 'other.png'
    other.write_bytes(b'fake')
    (tmp_path / 'input' / 'x.png').symlink_to(other)
    value['jobs'][0]['inputs'] = ['input/x.png']
    with pytest.raises(R.ValidationError, match='inventory'):
        R.validate_manifest(value, tmp_path)
    value['input_files'] = [{'path': 'input/x.png', 'size': 4, 'sha256': R.file_digest(other)}]
    with pytest.raises(R.ValidationError, match='Symlink'):
        R.validate_manifest(value, tmp_path)


@pytest.mark.parametrize('field,value', [('seconds', float('nan')), ('seconds', True), ('seconds', 99),
                                         ('resolution', '4096P'), ('seed', True), ('mode', 'unknown'),
                                         ('fast', 'yes'), ('images', [])])
def test_invalid_or_silently_ignored_settings_rejected(tmp_path, field, value):
    data = manifest()
    data['jobs'][0][field] = value
    with pytest.raises(R.ValidationError):
        R.validate_manifest(data, tmp_path)


def test_ref_mode_requires_used_inputs_and_single_tag_notation(tmp_path):
    value = manifest()
    value['input_files'] = [{'path': 'input/x.png', 'size': 1, 'sha256': '0' * 64}]
    job = value['jobs'][0]
    job.update(mode='ref', inputs=['input/x.png'])
    with pytest.raises(R.ValidationError, match='mention'):
        R.validate_manifest(value, tmp_path, check_files=False)
    job['prompt'] = '@Image1 is a kite.'
    R.validate_manifest(value, tmp_path, check_files=False)
    job['prompt'] += ' <Picture 1>'
    with pytest.raises(R.ValidationError, match='one reference notation'):
        R.validate_manifest(value, tmp_path, check_files=False)


def test_submission_intent_then_prompt_id_are_durable_before_polling(tmp_path):
    seen = []
    runner = make_runner(tmp_path)
    def transport(route, body=None):
        seen.append(route)
        if route == '/object_info':
            return {'SaveImage': {}}
        assert route == '/prompt'
        saved = json.loads(runner.state_path.read_text())
        submission = saved['jobs']['output']['submission']
        assert submission['identity'] == body['extra_data']['web_comfyui']
        assert 'prompt_id' not in submission
        return {'prompt_id': 'owned-prompt'}
    runner.transport = transport
    assert runner.queue(graph()) == 'owned-prompt'
    saved = json.loads(runner.state_path.read_text())
    assert saved['jobs']['output']['submission']['prompt_id'] == 'owned-prompt'
    resumed = R.Runner(runner.root, transport=lambda *a: pytest.fail('must reuse prompt ID'))
    resumed.current = 'output'
    assert resumed.queue(graph()) == 'owned-prompt'
    assert seen.count('/prompt') == 1


def test_ambiguous_post_is_never_retried_when_queue_history_are_empty(tmp_path):
    calls = []
    def transport(route, body=None):
        calls.append(route)
        if route == '/object_info':
            return {'SaveImage': {}}
        if route == '/prompt':
            raise TimeoutError('synthetic lost response')
        return {'queue_running': [], 'queue_pending': []} if route == '/queue' else {}
    runner = make_runner(tmp_path, transport=transport)
    with pytest.raises(R.SubmissionUncertain):
        runner.queue(graph())
    resumed = R.Runner(runner.root, transport=transport)
    resumed.current = 'output'
    with pytest.raises(R.SubmissionUncertain):
        resumed.queue(graph())
    assert calls.count('/prompt') == 1
    assert resumed.state['phase'] == 'submission_uncertain'


def test_uncertain_submission_reconciles_owned_history_without_post(tmp_path):
    runner = make_runner(tmp_path)
    def initial(route, body=None):
        if route == '/object_info':
            return {'SaveImage': {}}
        raise TimeoutError()
    runner.transport = initial
    with pytest.raises(R.SubmissionUncertain):
        runner.queue(graph())
    identity = runner.state['jobs']['output']['submission']['identity']
    def reconcile(route, body=None):
        assert route != '/prompt'
        if route.startswith('/history'):
            return {'found': {'prompt': [0, 'found', graph(), {'web_comfyui': identity}, ['1']]}}
        return {'queue_running': [], 'queue_pending': []}
    resumed = R.Runner(runner.root, transport=reconcile)
    resumed.current = 'output'
    assert resumed.queue(graph()) == 'found'
    assert json.loads(resumed.state_path.read_text())['jobs']['output']['submission']['prompt_id'] == 'found'


def test_changed_manifest_or_graph_cannot_reuse_job_identity(tmp_path):
    runner = make_runner(tmp_path, transport=lambda route, body=None: {'SaveImage': {}} if route == '/object_info' else {'prompt_id': 'p'})
    runner.queue(graph())
    changed = graph()
    changed['1']['inputs']['filename_prefix'] = 'changed'
    with pytest.raises(R.SubmissionUncertain, match='differs'):
        runner.queue(changed)
    manifest_path = runner.root / 'manifest.json'
    value = json.loads(manifest_path.read_text())
    value['jobs'][0]['prompt'] = 'Different request'
    manifest_path.write_text(json.dumps(value))
    with pytest.raises(R.ValidationError, match='identity differs'):
        R.Runner(runner.root)


def test_false_completed_history_is_not_success(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    calls = []
    def transport(route, body=None):
        calls.append(route)
        return {'pid': {'status': {'completed': len(calls) > 1, 'status_str': 'success'}}}
    runner.transport = transport
    monkeypatch.setattr(R.time, 'sleep', lambda seconds: None)
    assert runner.wait_history('pid')['status']['completed'] is True
    assert len(calls) == 2


def test_monitoring_loss_keeps_accepted_identity_and_blocks_new_submission(tmp_path):
    runner = make_runner(tmp_path)
    runner.transport = lambda route, body=None: {'SaveImage': {}} if route == '/object_info' else {'prompt_id': 'p'}
    runner.queue(graph())
    def lost(*args):
        raise TimeoutError('synthetic disconnected monitor')
    runner.transport = lost
    with pytest.raises(R.SubmissionUncertain, match='Monitoring unavailable'):
        runner.wait_history('p')
    assert runner.state['jobs']['output']['submission']['prompt_id'] == 'p'


def test_offline_batch_collects_all_types_and_completed_rerun_does_not_execute(tmp_path, monkeypatch):
    value = manifest('image')
    value['jobs'].extend([{'id': 'video', 'type': 'video', 'mode': 'text', 'prompt': 'Kite motion'},
                          {'id': 'audio', 'type': 'tts', 'prompt': 'Hello synthetic world'}])
    runner = make_runner(tmp_path, value)
    monkeypatch.setattr(runner, 'provision', lambda: None)
    monkeypatch.setattr(runner, 'start_comfy', lambda: None)
    monkeypatch.setattr(runner, 'configure_helpers', lambda: None)
    calls = []
    def fake_run(job):
        calls.append(job['id'])
        source = runner.root / 'work' / (job['id'] + {'image': '.png', 'video': '.mp4', 'tts': '.flac'}[job['type']])
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(('synthetic ' + job['id']).encode())
        runner.state['jobs'][job['id']]['submission'] = {'prompt_id': 'prompt-' + job['id']}
        return source, {'seed': job['seed']}
    for kind in ['image', 'video', 'tts']:
        monkeypatch.setitem(R.U.RUNNERS, kind, fake_run)
    runner.execute()
    assert runner.state['phase'] == 'completed'
    artifacts = json.loads((runner.root / 'artifacts.json').read_text())
    assert len(artifacts['artifacts']) == 3
    assert {x['kind'] for x in artifacts['artifacts']} == {'image', 'video', 'audio'}
    assert artifacts['manifest_sha256'] == runner.digest
    runner.execute()
    assert calls == ['output', 'video', 'audio']


def test_unknown_submission_stops_batch_even_when_stop_on_error_is_false(tmp_path, monkeypatch):
    value = manifest('image')
    value['stop_on_error'] = False
    value['jobs'].append({'id': 'second', 'type': 'image', 'prompt': 'Second synthetic kite'})
    runner = make_runner(tmp_path, value)
    monkeypatch.setattr(runner, 'provision', lambda: None)
    monkeypatch.setattr(runner, 'start_comfy', lambda: None)
    monkeypatch.setattr(runner, 'configure_helpers', lambda: None)
    calls = []
    def uncertain(job):
        calls.append(job['id'])
        raise R.SubmissionUncertain('Synthetic unknown acceptance')
    monkeypatch.setitem(R.U.RUNNERS, 'image', uncertain)
    with pytest.raises(R.SubmissionUncertain):
        runner.execute()
    assert calls == ['output']
    assert not (runner.root / 'artifacts.json').exists()


def test_artifact_manifest_hashes_owned_files_and_rejects_escape(tmp_path):
    runner = make_runner(tmp_path, manifest('image'))
    runner.state['jobs']['output']['submission'] = {'prompt_id': 'p'}
    source = runner.root / 'work' / 'synthetic.png'
    source.parent.mkdir()
    source.write_bytes(b'synthetic output bytes')
    artifact = runner.collect('output', source, 'image', 12)
    assert artifact == {'path': 'out/output.png', 'size': len(b'synthetic output bytes'),
                        'sha256': hashlib.sha256(b'synthetic output bytes').hexdigest(), 'kind': 'image', 'seed': 12, 'prompt_id': 'p'}
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'outside')
    with pytest.raises(R.ValidationError, match='outside'):
        runner.collect('output', outside, 'image', 12)
    with pytest.raises(R.ValidationError):
        runner.comfy_file({'filename': '../../outside.png', 'type': 'output'})


def test_cancel_and_deadline_are_checked_before_submission(tmp_path):
    runner = make_runner(tmp_path, transport=lambda *args: pytest.fail('no network after cancel'))
    (runner.root / 'cancel.requested').touch()
    with pytest.raises(R.Cancelled):
        runner.queue(graph())
    (runner.root / 'cancel.requested').unlink()
    runner.deadline = time.time() - 1
    with pytest.raises(R.Cancelled):
        runner.queue(graph())


@pytest.mark.parametrize('mode', ['text', 'ref', 'frames'])
def test_real_pinned_video_templates_compile_offline_for_all_modes(mode):
    image = {'kind': 'image', 'name': 'synthetic.png', 'width': 512, 'height': 512}
    job = {'mode': mode, 'prompt': '@Image1 moves.' if mode == 'ref' else 'A kite moves.', 'seed': 7}
    kw = {'inputs': [image]} if mode == 'ref' else {'first': image} if mode == 'frames' else {}
    plan = R.G.plan_video(job, **kw)
    result = R.G.video_graph(R.G.load_template(R.G.VIDEO_TEMPLATE), plan)
    assert R.G.check_graph(result) == []
    assert R.G.check_video(result, plan) == []


def test_picture_and_tts_templates_are_bound_to_reviewed_hashes():
    lock = json.loads((R.HERE / 'runtime.lock.json').read_text())
    assert len(lock['models']) == 15
    assert all(len(model['revision']) == 40 and len(model['sha256']) == 64 and model['size'] > 0 for model in lock['models'])
    for item in lock['templates']:
        assert R.file_digest(R.HERE / item['path']) == item['sha256']
    plan = R.G.plan_picture({'prompt': 'Synthetic kite', 'seed': 9})
    picture = R.G.picture_graph(R.G.load_template(R.G.PICTURE_TEMPLATE), plan)
    assert R.G.check_graph(picture) == []
    assert R.G.check_picture(picture, plan) == []
    assert R.G.check_graph(R.G.load_template('h3_tts32_api.json')) == []


def test_only_the_admitted_graph_models_are_download_candidates():
    lock = json.loads((R.HERE / 'runtime.lock.json').read_text())
    assert len(R.required_models(manifest('image'), lock)) == 4
    assert len(R.required_models(manifest('tts'), lock)) == 5
    assert len(R.required_models(manifest('video'), lock)) == 9
    action = manifest('video')
    action['jobs'][0]['intensity'] = 'high'
    assert len(R.required_models(action, lock)) == 12
    del lock['models'][0]
    with pytest.raises(R.ValidationError, match='absent'):
        R.required_models(action, lock)


@pytest.mark.parametrize('damage', ['missing_helper', 'invalid_helper', 'invalid_manifest', 'missing_lock', 'invalid_state'])
def test_real_subprocess_bootstrap_failure_publishes_bound_terminal_state(tmp_path, damage):
    value = manifest('image')
    root = tmp_path / value['job_id']
    bundle = root / 'runner'
    shutil.copytree(R.HERE, bundle, ignore=shutil.ignore_patterns('__pycache__'))
    manifest_path = root / 'manifest.json'
    manifest_path.write_text(json.dumps(value))
    expected = R.file_digest(manifest_path)
    if damage == 'missing_helper':
        (bundle / 'h3graph.py').unlink()
    elif damage == 'invalid_helper':
        (bundle / 'h3graph.py').write_text('this is invalid Python (')
    elif damage == 'invalid_manifest':
        manifest_path.write_text('{broken upload')
    elif damage == 'missing_lock':
        (bundle / 'runtime.lock.json').unlink()
    else:
        (root / 'status.json').write_text('["broken status"]')
    # Invoke the actual worker entry under a disk temp path. Public CLI path
    # restrictions remain unchanged; this needs no /content access or watchdog.
    result = subprocess.run([sys.executable, '-I', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import remote_job; '
        'raise SystemExit(remote_job.run_job(sys.argv[2], sys.argv[3]))',
        str(bundle), str(root), expected], capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    saved = json.loads((root / 'status.json').read_text())
    assert saved['job_id'] == value['job_id']
    assert saved['manifest_sha256'] == expected
    assert saved['phase'] == 'failed' and saved['done'] is True
    assert not (root / 'runtime').exists()
    assert not (root / 'artifacts.json').exists()
    assert 'WEB_COMFYUI_JOB ' in result.stdout
    assert value['jobs'][0]['prompt'] not in saved['error']
    assert saved['diagnostics']['stage'] == 'bootstrap'
    assert saved['diagnostics']['error_type'] in R.DIAGNOSTIC_ERRORS
    if damage == 'missing_helper':
        assert saved['diagnostics']['cause_type'] == 'ModuleNotFoundError'
    elif damage == 'invalid_helper':
        assert saved['diagnostics']['cause_type'] == 'SyntaxError'


@pytest.mark.parametrize('stage', ['source_checkout', 'venv', 'torch_install',
                                  'requirements_install', 'cuda_check', 'model_download'])
def test_worker_persists_safe_stage_and_subprocess_status(tmp_path, monkeypatch, stage):
    value = manifest('image')
    root = tmp_path / value['job_id']
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps(value))
    def provision(self):
        self.diagnostic_stage = stage
        raise subprocess.CalledProcessError(23, ['private-command', 'private-argument'],
                                            output=b'private provider response')
    monkeypatch.setattr(R.Runner, 'provision', provision)
    monkeypatch.setattr(R.Runner, 'cancel_owned', lambda self: None)
    monkeypatch.setattr(R.subprocess, 'Popen', lambda *a, **k: None)
    R.run_job(root)
    state = json.loads((root / 'status.json').read_text())
    assert state['phase'] == 'failed'
    assert state['diagnostics'] == {'stage': stage, 'error_type': 'CalledProcessError', 'exit_code': 23}
    assert 'private-' not in json.dumps(state)
    assert 'private provider response' not in json.dumps(state)


def test_failure_diagnostics_never_serializes_exception_text_or_unknown_fields():
    error = urllib.error.HTTPError('https://private.invalid/?credential=hidden', 403,
                                   'private provider response', {}, None)
    assert R.failure_diagnostics(error, 'model_download') == {
        'stage': 'model_download', 'error_type': 'HTTPError', 'http_status': 403}
    unsafe = {'stage': 'private prompt', 'error_type': ['RuntimeError'], 'cause_type': 'private message',
              'exit_code': True, 'http_status': 999, 'url': 'https://private.invalid', 'stderr': 'private'}
    assert R.sanitize_diagnostics(unsafe) == {}


def test_generation_failure_retains_controlled_diagnostics(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, manifest('image'))
    monkeypatch.setattr(runner, 'provision', lambda: None)
    monkeypatch.setattr(runner, 'start_comfy', lambda: None)
    monkeypatch.setattr(runner, 'configure_helpers', lambda: None)
    def fail(_job):
        raise RuntimeError('private authored prompt')
    monkeypatch.setitem(R.U.RUNNERS, 'image', fail)
    state = runner.execute()
    assert state['phase'] == 'failed'
    assert state['diagnostics'] == {'stage': 'generation', 'error_type': 'RuntimeError'}
    assert 'private authored prompt' not in json.dumps(state)


def test_corrupt_template_fails_before_any_install_or_download(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, manifest('image'))
    runner.lock['templates'][0]['sha256'] = '0' * 64
    monkeypatch.setattr(runner, 'command', lambda *a, **k: pytest.fail('Corrupt bundle must not install'))
    with pytest.raises(R.ValidationError, match='template integrity'):
        runner.provision()
    assert not (runner.root / 'runtime').exists()


def test_colab_bootstrap_without_ensurepip_installs_only_into_job_environment(tmp_path, monkeypatch):
    pytest.importorskip('pip')
    runner = make_runner(tmp_path, manifest('image'))
    runner.lock['git'] = []
    monkeypatch.setattr(R, 'required_models', lambda *_: [])
    support = tmp_path / 'missing-ensurepip'
    support.mkdir()
    (support / 'sitecustomize.py').write_text(
        "import venv\n"
        "def unavailable(*args, **kwargs):\n"
        "    raise RuntimeError('synthetic ensurepip unavailable')\n"
        "venv.EnvBuilder._setup_pip = unavailable\n")
    environment = dict(os.environ, PYTHONPATH=str(support))
    # Establish the same failure as the Colab image before exercising the fix.
    broken = subprocess.run([sys.executable, '-m', 'venv', str(tmp_path / 'control')],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert broken.returncode != 0 and 'synthetic ensurepip unavailable' in broken.stderr
    original = runner.command
    commands = []
    def command(args, *, stage=None, **kwargs):
        commands.append((stage, args))
        if stage == 'venv':
            return original(args, stage=stage, env=environment, **kwargs)
        # These are the production commands, but never install torch or use a GPU.
        assert stage in {'torch_install', 'requirements_install', 'cuda_check'}
    monkeypatch.setattr(runner, 'command', command)
    runner.provision()
    prefix = [sys.executable, '-m', 'pip', '--python', str(runner.python)]
    packages = runner.lock['python_packages']
    assert commands[0] == ('venv', [sys.executable, '-m', 'venv', '--without-pip', str(runner.python.parents[1])])
    assert commands[1] == ('torch_install', prefix + ['install', '--index-url', runner.lock['torch_index'],
        *[name + '==' + packages[name] for name in ('torch', 'torchvision', 'torchaudio')]])
    assert commands[2] == ('requirements_install', prefix + [
        'install', '-c', str(runner.root / 'runtime' / 'constraints.txt'),
        '-r', str(runner.comfy / 'requirements.txt'), 'huggingface_hub==' + packages['huggingface_hub'],
        'hf_xet==' + packages['hf_xet']])
    assert commands[3][0] == 'cuda_check' and commands[3][1][:2] == [str(runner.python), '-c']
    probe = subprocess.run([str(runner.python), '-I', '-c',
        'import importlib.util,sys; assert sys.prefix != sys.base_prefix; '
        'assert importlib.util.find_spec("pip") is None'], capture_output=True, timeout=30)
    assert probe.returncode == 0
    # A local synthetic wheel tests the real pip --python mechanism without
    # package downloads, system installs, or a GPU. The target still has no pip.
    name = 'webcomfy_bootstrap_fixture'
    assert importlib.util.find_spec(name) is None
    wheel = tmp_path / (name + '-1.0-py3-none-any.whl')
    info = name + '-1.0.dist-info/'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(name + '/__init__.py', 'MARKER = "isolated"\n')
        archive.writestr(info + 'METADATA', 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: 1.0\n')
        archive.writestr(info + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(info + 'RECORD', '')
    installed = subprocess.run(prefix + ['install', '--no-index', '--no-deps', '--disable-pip-version-check', str(wheel)],
                               capture_output=True, timeout=30)
    assert installed.returncode == 0
    checked = subprocess.run([str(runner.python), '-I', '-c',
        'import webcomfy_bootstrap_fixture as f; assert f.MARKER == "isolated"'], capture_output=True, timeout=30)
    assert checked.returncode == 0
    assert importlib.util.find_spec(name) is None


@pytest.fixture
def synthetic_media(tmp_path):
    """Tiny generated fixtures only, in pytest's disk-backed directory."""
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is needed to exercise the real source-media adapters')
    def chunk(name, payload):
        return struct.pack('!I', len(payload)) + name + payload + struct.pack('!I', zlib.crc32(name + payload))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress((b'\0' + bytes([0, 80, 220]) * 32) * 32)) + chunk(b'IEND', b'')
    picture = tmp_path / 'synthetic.png'
    picture.write_bytes(png)
    audio = tmp_path / 'synthetic.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\0\0' * 8000)
    video = tmp_path / 'synthetic.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=32x32:d=2:r=24',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-shortest', '-c:v', 'libx264',
                    '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], check=True, timeout=30)
    return {'image': picture, 'video': video, 'audio': audio}


@pytest.mark.parametrize('form_kind', ['image', 'image_edit', 'video_text', 'video_ref', 'video_first',
                                      'video_first_last', 'video_extend', 'tts', 'tts_voice'])
def test_frontend_service_manifest_runs_real_source_adapters_offline(tmp_path, monkeypatch, synthetic_media, form_kind):
    """Real admission/manifest/graph/media code, synthetic ComfyUI HTTP boundary.

    This exercises every form kind without installing upstream runtime code or
    executing a model. FFmpeg only creates/conforms the tiny synthetic fixtures.
    """
    from webui.colab_service import ColabService
    class FakeAccount:
        installed = True
        def __call__(self, operation, directory, **options):
            if operation == 'account_identity':
                return {'bound': True, 'subject_key': 'a' * 64}
            assert operation == 'account_status'
            return {'authenticated': True, 'balance_compute_units': 100, 'consumption_rate_per_hour': 4}
    service = ColabService(tmp_path / 'controller', FakeAccount())
    monkeypatch.setattr(service, 'start', lambda: None)
    account = service.create_account('synthetic-owner', 'Synthetic account; no credentials')
    job = {'type': 'image' if form_kind.startswith('image') else 'tts' if form_kind.startswith('tts') else 'video',
           'prompt': 'A synthetic blue paper kite.', 'seed': 123}
    media_kinds = []
    if job['type'] == 'image':
        job.update(size='1K', aspect='1:1')
        if form_kind == 'image_edit':
            media_kinds = ['image']
    elif job['type'] == 'tts':
        job.update(seconds=2)
        if form_kind == 'tts_voice':
            media_kinds = ['audio']
            job['prompt'] = '<Audio 1> is the voice reference. Say hello.'
    else:
        mode = {'video_text': 'text', 'video_ref': 'ref', 'video_first': 'frames',
                'video_first_last': 'frames', 'video_extend': 'extend'}[form_kind]
        job.update(seconds=2, mode=mode, resolution='480P', aspect='16:9', intensity='off', fast=True)
        if form_kind == 'video_ref':
            media_kinds = ['image', 'video', 'audio']
            job['prompt'] = '@Image1 appears in the scene from @Video1 with the voice of @Audio1.'
        elif form_kind in {'video_first', 'video_first_last'}:
            media_kinds = ['image'] * (2 if form_kind.endswith('_last') else 1)
        elif form_kind == 'video_extend':
            media_kinds = ['video']
    files = [{'name': synthetic_media[kind].name, 'data': base64.b64encode(synthetic_media[kind].read_bytes()).decode()} for kind in media_kinds]
    body = {'account_id': account['id'], 'request_id': str(uuid.uuid4()), 'gpu': 'L4',
            'max_units': 20, 'max_minutes': 60, 'job': job, 'files': files}
    admitted = service.submit('synthetic-owner', body)
    state = service.job('synthetic-owner', admitted['id'])
    root = service.root / 'jobs' / admitted['id']
    runner = R.Runner(root, state['manifest_sha256'])
    runner.current = 'output'
    runner.state['jobs']['output'] = {'type': job['type'], 'status': 'running'}
    for name in ('JOB', 'OUT', 'WORK', 'COMFY', 'STATUS', 'STATE', 'STAGED', 'LOCAL_TEST', 'COMFY_URL',
                 'TEMPLATE_DIRS', 'save', 'log', 'queue', 'wait_history', 'comfy_input', 'source_path',
                 'extend_source', 'comfy_file', 'call', 'run_tts'):
        monkeypatch.setattr(R.U, name, getattr(R.U, name))
    monkeypatch.setitem(R.U.RUNNERS, 'tts', R.U.RUNNERS['tts'])
    runner.configure_helpers()
    (runner.comfy / 'input').mkdir(parents=True)
    output = runner.comfy / 'output'
    output.mkdir()
    graph_types = set()
    for path in (R.HERE / 'workflows').glob('*.json'):
        graph_types.update(n['class_type'] for n in json.loads(path.read_text()).values())
    graph_types.update(re.findall(r'["\']class_type["\']:\s*["\']([^"\']+)', (R.HERE / 'h3graph.py').read_text()))
    graph_types.add('LoadAudio')
    submitted = []
    def transport(route, body=None):
        if route == '/object_info':
            return {kind: {} for kind in graph_types}
        if route == '/prompt':
            submitted.append(body['prompt'])
            return {'prompt_id': 'synthetic-owned-prompt'}
        assert route == '/history/synthetic-owned-prompt'
        source_kind = 'audio' if job['type'] == 'tts' else job['type']
        fixture = synthetic_media[source_kind]
        if source_kind == 'video':
            conditioning = submitted[0][R.G.N_COND]['inputs']
            R.U.conform(fixture, output / fixture.name, conditioning['width'], conditioning['height'], True)
        else:
            shutil.copyfile(fixture, output / fixture.name)
        sink, field = (R.G.N_IMG_SAVE, 'images') if source_kind == 'image' else (R.G.N_SAVE, 'videos') if source_kind == 'video' else ('62', 'audio')
        return {'synthetic-owned-prompt': {'status': {'completed': True, 'status_str': 'success'},
                'prompt': [0, 'synthetic-owned-prompt', submitted[0], {}, []],
                'outputs': {sink: {field: [{'filename': fixture.name, 'subfolder': '', 'type': 'output'}]}}}}
    runner.transport = transport
    normalized = runner.manifest['jobs'][0]
    source, info = R.U.RUNNERS[normalized['type']](normalized)
    assert source.is_file()
    assert not info.get('warning')
    assert len(submitted) == 1
    assert R.G.check_graph(submitted[0]) == []
    saved = json.loads(runner.state_path.read_text())
    assert saved['manifest_sha256'] == state['manifest_sha256']
    assert saved['jobs']['output']['submission']['prompt_id'] == 'synthetic-owned-prompt'
    artifact = runner.collect('output', source, normalized['type'], normalized['seed'])
    assert artifact['path'].startswith('out/output.') and artifact['seed'] == 123
