"""Cross-process lock tests run on Linux and in the Windows primitive-only CI job."""
import os
from pathlib import Path
import subprocess
import sys
import queue
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webui import file_lock as locks


def read_ready(process):
    lines = queue.Queue()
    thread = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
    thread.start()
    try:
        return lines.get(timeout=10).strip()
    except queue.Empty:
        pytest.fail('Lock subprocess did not become ready within 10 seconds')

PROBE = '''
import os, sys
from webui import file_lock as locks
fd = os.open(sys.argv[1], os.O_RDWR)
os.lseek(fd, 7, os.SEEK_SET)
try:
    locks.flock(fd, int(sys.argv[2]) | locks.LOCK_NB)
except BlockingIOError:
    print('busy')
else:
    print('acquired')
    locks.flock(fd, locks.LOCK_UN)
finally:
    os.close(fd)
'''


def probe(path, operation):
    result = subprocess.run([sys.executable, '-c', PROBE, str(path), str(operation)],
                            cwd=ROOT, capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip()


@pytest.mark.parametrize('held', [locks.LOCK_SH, locks.LOCK_EX])
def test_shared_exclusive_contention_and_release(tmp_path, held):
    path = tmp_path / 'gpu.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        locks.flock(fd, held | locks.LOCK_NB)
        assert probe(path, locks.LOCK_EX) == 'busy'
        assert probe(path, locks.LOCK_SH) == ('acquired' if held == locks.LOCK_SH else 'busy')
        locks.flock(fd, locks.LOCK_UN)
        assert probe(path, locks.LOCK_EX) == 'acquired'
    finally:
        os.close(fd)


def test_process_exit_releases_lock(tmp_path):
    path = tmp_path / 'exit.lock'
    script = '''
import os, sys
from webui import file_lock as locks
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
locks.flock(fd, locks.LOCK_EX)
print('ready', flush=True)
sys.stdin.readline()
os._exit(0)
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(path)], cwd=ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert read_ready(process) == 'ready'
        assert probe(path, locks.LOCK_EX) == 'busy'
        process.communicate('\n', timeout=15)
        assert probe(path, locks.LOCK_EX) == 'acquired'
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=15)


def test_invalid_descriptor_is_not_reported_as_contention():
    with pytest.raises((OSError, ValueError)) as caught:
        locks.flock(-1, locks.LOCK_EX | locks.LOCK_NB)
    assert not isinstance(caught.value, BlockingIOError)


def test_blocking_waiter_acquires_only_after_release(tmp_path):
    path = tmp_path / 'blocking.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    script = '''
import os,sys
from webui import file_lock as locks
fd=os.open(sys.argv[1],os.O_RDWR)
print('waiting',flush=True)
locks.flock(fd,locks.LOCK_EX)
print('acquired',flush=True)
locks.flock(fd,locks.LOCK_UN)
os.close(fd)
'''
    process = None
    try:
        locks.flock(fd, locks.LOCK_EX)
        process = subprocess.Popen([sys.executable, '-c', script, str(path)], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert read_ready(process) == 'waiting'
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.15)
        locks.flock(fd, locks.LOCK_UN)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err
        assert out.strip() == 'acquired'
    finally:
        os.close(fd)
        if process is not None and process.poll() is None:
            process.kill(); process.wait(timeout=10)


def test_contended_updates_are_not_lost(tmp_path):
    lock = tmp_path / 'counter.lock'
    counter = tmp_path / 'counter.txt'
    lock.touch(); counter.write_text('0')
    script = '''
import os,sys
from pathlib import Path
if sys.argv[3]=='native':
    import fcntl as locks
else:
    from webui import file_lock as locks
counter=Path(sys.argv[2]); fd=os.open(sys.argv[1],os.O_RDWR)
try:
    for _ in range(40):
        locks.flock(fd,locks.LOCK_EX)
        try: counter.write_text(str(int(counter.read_text())+1))
        finally: locks.flock(fd,locks.LOCK_UN)
finally: os.close(fd)
'''
    processes = []
    try:
        for n in range(4):
            mode = 'native' if os.name != 'nt' and n % 2 else 'wrapper'
            processes.append(subprocess.Popen([sys.executable, '-c', script, str(lock), str(counter), mode],
                                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for process in processes:
            _, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
        assert counter.read_text() == '160'
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)


@pytest.mark.skipif(os.name == 'nt', reason='The protected GPU path layer still requires POSIX')
def test_gpu_queue_lock_interoperates_with_legacy_processes(tmp_path, monkeypatch):
    from webui.config_env import default_gpu_lock
    from webui.novel_scene_art.gpu_lock import SharedGpuLock
    # Bound the ancestry check to this test's private simulated home.
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    tmp_path.chmod(0o700)
    (tmp_path / '.cache').mkdir(mode=0o700)
    path = default_gpu_lock(tmp_path)
    lock = SharedGpuLock(path)
    try:
        assert lock.try_acquire()
        assert lock.try_acquire()  # Same object is idempotent.
        assert default_gpu_lock(tmp_path) == path
        assert probe(path, locks.LOCK_EX) == 'busy'
    finally:
        lock.release()
    assert probe(path, locks.LOCK_EX) == 'acquired'
