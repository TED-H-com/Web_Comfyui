from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_comfy_gpu_queue as queue


def test_idle_cached_models_are_recycled_before_admission():
    events = []
    memories = iter([(8000, 0), (29000, 0)])
    def http(_base, path, **_kwargs):
        events.append(path)
        return ({'queue_running': [], 'queue_pending': []} if path == '/queue'
                else {'system': {}, 'devices': [{'torch_vram_total': 16 * 1024**3}]})
    def recycle(_base):
        events.append('recycle')
        return {'status': 'recycled'}
    queue.wait_until_ready('http://127.0.0.1:8188', 26000, 20, 1,
                           http=http, gpu=lambda: next(memories), recycle=recycle,
                           sleeper=lambda _: None)
    assert events.count('recycle') == 1
    assert events[events.index('recycle') + 1:] == ['/queue', '/system_stats', '/queue']


def test_busy_queue_is_never_recycled_even_with_free_gpu():
    samples = iter([{'queue_running': [[1, 'busy']], 'queue_pending': []},
                    {'queue_running': [], 'queue_pending': []},
                    {'queue_running': [], 'queue_pending': []}])
    sleeps = []
    def http(_base, path, **_kwargs):
        return next(samples) if path == '/queue' else {'system': {}, 'devices': []}
    def forbidden(_):
        raise AssertionError('busy queue must not be restarted')
    queue.wait_until_ready('http://127.0.0.1:8188', 26000, 20, 1, http=http,
                           gpu=lambda: (29000, 0), recycle=forbidden, sleeper=sleeps.append)
    assert sleeps == [1]


def test_free_vram_during_restart_does_not_admit_worker():
    attempts = 0
    sleeps = []
    def http(_base, path, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise queue.guarded.RunnerError('unavailable', 'restart in progress')
        return {'queue_running': [], 'queue_pending': []} if path == '/queue' else {'system': {}}
    queue.wait_until_ready('http://127.0.0.1:8188', 26000, 20, 1, http=http,
                           gpu=lambda: (29000, 0), sleeper=sleeps.append)
    assert sleeps == [1]


def test_busy_gpu_of_another_process_does_not_trigger_recycle():
    samples = iter([(8000, 99), (29000, 0)])
    sleeps = []
    def http(_base, path, **_kwargs):
        return ({'queue_running': [], 'queue_pending': []} if path == '/queue'
                else {'system': {}, 'devices': [{'torch_vram_total': 16 * 1024**3}]})
    def forbidden(_):
        raise AssertionError('active GPU must not be restarted')
    queue.wait_until_ready('http://127.0.0.1:8188', 26000, 20, 1, http=http,
                           gpu=lambda: next(samples), recycle=forbidden, sleeper=sleeps.append)
    assert sleeps == [1]


def test_voice_wrapper_flags_preserve_child_argv_and_fleet_lock(tmp_path, monkeypatch, capsys):
    import fcntl
    import pytest
    lock = tmp_path / 'fleet.lock'
    child = ['python3', 'tts.py', '--text', 'private test text']
    monkeypatch.setattr(sys, 'argv', ['queue', '--lock', str(lock), '--min-free-mb', '21000',
                                   '--max-util', '15', '--interval', '3', '--', *child])
    observed = []
    def ready(base, minimum, maximum, interval):
        assert (minimum, maximum, interval) == (21000,15,3)
        observed.append('ready')
    def call(argv):
        assert argv == child
        with lock.open('r+') as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed.append('child')
        return 7
    monkeypatch.setattr(queue, 'wait_until_ready', ready)
    monkeypatch.setattr(queue.subprocess, 'call', call)
    assert queue.main() == 7
    assert observed == ['ready','child']
    output = capsys.readouterr().out
    assert '[gpu-queue] running:' in output
    assert 'private test text' not in output


def test_failed_checkpoint_never_admits_child_on_freed_vram():
    # A failed maintenance attempt must loop through fresh readiness checks;
    # an idle-looking GPU alone is not a successful admission proof.
    calls = []
    class StopWaiting(Exception):
        pass
    def http(_base, path, **kwargs):
        return ({'queue_running': [], 'queue_pending': []} if path == '/queue'
                else {'system': {}, 'devices':[{'torch_vram_total':16*1024**3}]})
    def fail(base):
        calls.append('checkpoint_failed')
        raise queue.guarded.RunnerError('comfy_history_checkpoint_failed','fake')
    def stop(seconds):
        calls.append('wait')
        raise StopWaiting
    import pytest
    with pytest.raises(StopWaiting):
        queue.wait_until_ready('http://127.0.0.1:8188',26000,20,1,http=http,
                              gpu=lambda:(8000,0),recycle=fail,sleeper=stop)
    assert calls == ['checkpoint_failed','wait']
