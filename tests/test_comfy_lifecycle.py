"""Synthetic lifecycle transport/concurrency tests; never touch a real service."""
import ast
from contextlib import contextmanager
import importlib.util
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'webui'))
from scripts import comfy_lifecycle as life
from scripts.minimax_h3 import run_native as native
from scripts.minimax_h3 import run_web_graph as worker


def _hold_lock(path, ready, finish, exclusive):
    lock = life.recycle_lock(path=path) if exclusive else life.admission_lock(path=path)
    with lock:
        ready.set()
        finish.wait(5)


@pytest.mark.parametrize('exclusive', [True, False])
def test_cross_process_admission_and_maintenance(tmp_path, exclusive):
    path = tmp_path / 'gate.lock'
    ctx = multiprocessing.get_context('spawn')
    ready, finish = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_lock, args=(path, ready, finish, exclusive))
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(life.AdmissionBusy):
            with life.recycle_lock(path=path, timeout_seconds=0.05):
                pytest.fail('exclusive maintenance entered a held gate')
        if exclusive:
            with pytest.raises(life.AdmissionBusy):
                with life.admission_lock(path=path):
                    pytest.fail('POST admission entered maintenance')
        else:
            with life.admission_lock(path=path):
                pass
    finally:
        finish.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0
    with life.recycle_lock(path=path, timeout_seconds=0):
        pass
    assert path.stat().st_mode & 0o777 == 0o600


def test_unsafe_lock_is_rejected(tmp_path):
    target = tmp_path / 'data'
    target.write_text('preserve')
    link = tmp_path / 'gate'
    link.symlink_to(target)
    with pytest.raises(OSError):
        with life.admission_lock(path=link):
            pass
    assert target.read_text() == 'preserve'


@pytest.mark.parametrize('body,status', [
    (b'{"ok":true,"saved":2}', 0),
    (b'{"ok":false}', 0),
    (b'{"ok":true,"prompt":"private"}', 0),
    (b'{"ok":true,"saved":-1}', 0),
    (b'garbage', 0),
    (b'{"ok":true}', 1),
    (b' ' * 65537, 0),
])
def test_checkpoint_requires_bounded_success_counts(tmp_path, monkeypatch, body, status):
    module = tmp_path / 'comfy_lifecycle.py'
    (tmp_path / 'checkpoint_comfy_history.py').write_text('# fake')
    monkeypatch.setattr(life, '__file__', str(module))
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert kwargs['timeout'] == 120
        kwargs['stdout'].write(body)
        return subprocess.CompletedProcess(command, status)
    if body == b'{"ok":true,"saved":2}' and status == 0:
        assert life.checkpoint_history('http://127.0.0.1:8188', run_command=run)['saved'] == 2
    else:
        with pytest.raises(life.LifecycleError):
            life.checkpoint_history('http://127.0.0.1:8188', run_command=run)
    assert commands[0][-2:] == ['--comfy-url', 'http://127.0.0.1:8188']


def test_failed_checkpoint_never_restarts_and_gate_releases(tmp_path, monkeypatch):
    monkeypatch.setenv('CASTLE_COMFY_ADMISSION_LOCK', str(tmp_path / 'gate'))
    commands = []
    def fail(_base):
        with pytest.raises(life.AdmissionBusy):
            with life.admission_lock():
                pass
        raise life.LifecycleError('fake persistence failure')
    with pytest.raises(native.RunnerError) as error:
        native.recycle_comfy_service(
            'http://127.0.0.1:8188',
            http=lambda *a, **kw: {'queue_running': [], 'queue_pending': []},
            service_reader=lambda: native.ComfyServiceState('loaded', 'active', 'running', 42, 'a'*32, 'control-group'),
            sleeper=lambda seconds: None,
            history_checkpoint=fail,
            run_command=lambda *a, **kw: commands.append(a),
        )
    assert error.value.code == 'comfy_history_checkpoint_failed'
    assert commands == []
    with life.admission_lock():
        pass


def test_checkpoint_precedes_restart_and_gate_covers_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv('CASTLE_COMFY_ADMISSION_LOCK', str(tmp_path / 'gate'))
    events, now = [], [0.0]
    states = iter([native.ComfyServiceState('loaded','active','running',i,c*32,'control-group')
                   for i,c in [(42,'a'),(43,'b'),(43,'b')]])
    def assert_closed():
        with pytest.raises(life.AdmissionBusy):
            with life.admission_lock():
                pass
    def checkpoint(base):
        assert_closed()
        events.append('checkpoint')
    def command(argv, **kwargs):
        assert_closed()
        events.append('restart' if 'restart' in argv else 'reset')
        return subprocess.CompletedProcess(argv, 0)
    def http(base, path, **kwargs):
        assert_closed()
        if path == '/system_stats':
            events.append('ready')
            return {'system': {'comfyui_version':'0.32.0'}}
        return {'queue_running':[], 'queue_pending':[]}
    native.recycle_comfy_service(
        'http://127.0.0.1:8188', http=http, service_reader=lambda: next(states),
        listener_verifier=lambda base: (), history_checkpoint=checkpoint, run_command=command,
        memory_reader=lambda: native.MemorySnapshot(40*1024**3,0,0),
        clock=lambda: now[0], sleeper=lambda seconds: now.__setitem__(0,now[0]+seconds),
        stability_seconds=1, poll_seconds=1,
    )
    assert events.index('checkpoint') < events.index('restart') < events.index('ready')
    with life.admission_lock():
        pass


def _fake_worker(tmp_path, monkeypatch, fail_checkpoint=False):
    events = []
    output = tmp_path/'video.mp4'
    output.write_bytes(b'verified synthetic output')
    spec_path = tmp_path/'spec.json'; spec_path.write_text('{}')
    spec = {'cancel_file':str(tmp_path/'cancel'), 'deadline_epoch':1e20,
            'comfy_dir':str(tmp_path), 'prompt_id':'owned-prompt',
            'graph_sha256':'a'*64,'receipt_path':str(tmp_path/'receipt.json')}
    contract = SimpleNamespace(mode='t2v',width=864,height=480,frames=124,
                               output_prefix='Castle/run')
    monkeypatch.setattr(worker,'_load_spec',lambda *a,**kw:(spec,{},contract,tmp_path,tmp_path))
    monkeypatch.setattr(worker,'_check_stop',lambda *a,**kw:None)
    monkeypatch.setattr(worker,'_bind_history_output',lambda *a,**kw:output)
    monkeypatch.setattr(worker.web_executor,'_validate_graph_contract',lambda *a,**kw:contract)
    monkeypatch.setattr(worker.web_executor,'_contract_payload',lambda *a,**kw:{})
    for name in ('require_no_incomplete_model_downloads','verify_loopback_listener',
                 'verify_comfy_runtime_contract','wait_for_memory_headroom','require_gpu_headroom',
                 'verify_object_info','require_queue_idle','require_memory_headroom'):
        monkeypatch.setattr(native,name,lambda *a,**kw:None)
    monkeypatch.setattr(native,'load_manifest',lambda *a:SimpleNamespace(artifact_set='test',revision='test'))
    monkeypatch.setattr(native,'verify_pinned_models',lambda *a,**kw:(1,2,3,4))
    monkeypatch.setattr(native,'read_gpu_snapshot',lambda:None)
    monkeypatch.setattr(native,'read_memory_snapshot',lambda:None)
    monkeypatch.setattr(native,'request_json',lambda *a,**kw:{})
    monkeypatch.setattr(native,'submit_prompt',lambda *a,**kw:'owned-prompt')
    monkeypatch.setattr(native,'wait_for_history',lambda *a,**kw:{})
    monkeypatch.setattr(native,'probe_output',lambda *a,**kw:{'synthetic':True})
    monkeypatch.setattr(native,'manifest_scope_sha256',lambda *a:'b'*64)
    monkeypatch.setattr(native,'cancel_owned_prompt',lambda *a:None)
    monkeypatch.setattr(native,'wait_for_comfy_queue_idle',lambda *a,**kw:events.append('idle'))
    checkpoint=tmp_path/'receipt.output-checkpoint.json'
    def cleanup(*a):
        assert checkpoint.is_file()
        data=json.loads(checkpoint.read_text())
        assert data['cleanup_pending'] is True
        assert data['receipt']['prompt_id']=='owned-prompt'
        assert data['receipt']['output']['bytes']==output.stat().st_size
        events.append('cleanup')
        return {'attempted':True,'status':'accepted'}
    monkeypatch.setattr(native,'release_comfy_memory',cleanup)
    if fail_checkpoint:
        monkeypatch.setattr(native,'_write_exclusive_json',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    return lambda:worker.run(spec_path,token='fake',state_root=tmp_path), events, checkpoint


def test_h3_durable_output_exists_before_cleanup(tmp_path, monkeypatch):
    run,events,checkpoint=_fake_worker(tmp_path,monkeypatch)
    run()
    assert events==['idle','cleanup']
    assert checkpoint.is_file()
    assert json.loads((tmp_path/'receipt.json').read_text())['memory_cleanup']['status']=='accepted'


def test_h3_checkpoint_failure_preserves_volatile_history(tmp_path, monkeypatch):
    run,events,checkpoint=_fake_worker(tmp_path,monkeypatch,True)
    with pytest.raises(OSError):
        run()
    assert events==[]
    assert not checkpoint.exists()


def test_relative_deployment_gate_is_rejected(monkeypatch):
    monkeypatch.setenv('CASTLE_COMFY_ADMISSION_LOCK', 'different-per-working-directory.lock')
    with pytest.raises(life.LifecycleError):
        life.lock_path()
