"""Build a bounded, idempotent Colab watchdog bootstrap; no Google work on import.

Call build_watchdog_code through the private bridge before uploading anything.
The acknowledgement confirms a live watchdog, never provider-side release.
The detached child inherits Colab's runtime environment, keeps one per-job flock,
and attempts runtime.unassign at the absolute deadline or cancellation marker.
"""
from __future__ import annotations

import math
import re
import uuid


_WORKER = r'''
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1])
deadline=float(sys.argv[2])
manifest_sha256=sys.argv[3]
identity={'schema_version':1,'job_id':root.name,'manifest_sha256':manifest_sha256,'deadline_epoch':deadline}
for path in (root,*root.parents):
    if path.is_symlink():
        raise RuntimeError('Watchdog directory is invalid')
lock=os.open(root/'.watchdog.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
try:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
marker=root/'watchdog.json'
if marker.exists():
    if marker.is_symlink() or marker.stat().st_size>4096:
        raise RuntimeError('Watchdog identity is invalid')
    old=json.loads(marker.read_text())
    if any(old.get(k)!=v for k,v in identity.items()):
        raise RuntimeError('Watchdog identity differs')
def save(phase,attempt=0):
    temporary=root/('.watchdog.'+str(os.getpid())+'.tmp')
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(dict(identity,phase=phase,pid=os.getpid(),attempt=attempt),stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,marker)
    finally:
        temporary.unlink(missing_ok=True)
save('armed')
while time.time()<deadline and not (root/'cancel.requested').exists():
    time.sleep(min(5,max(.05,deadline-time.time())))
(root/'cancel.requested').touch(mode=0o600,exist_ok=True)
for attempt in range(1,4):
    save('shutdown_requested',attempt)
    try:
        result=subprocess.run([sys.executable,'-c','from google.colab import runtime; runtime.unassign()'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        if result.returncode==0:
            break
    except (OSError,subprocess.TimeoutExpired):
        pass
    if attempt<3:
        time.sleep(2)
os.close(lock)
'''


def build_watchdog_code(job_id: str, deadline_epoch: float, manifest_sha256: str) -> str:
    """Return reviewed Jupyter code with a WEB_COMFYUI_JSON acknowledgement.

    Repeating the bootstrap reconciles a matching live watchdog and can replace
    a dead watchdog. It never extends the original deadline, allocates a runtime,
    or launches the generation worker. All interpolated arguments are validated.
    """
    try:
        valid_id = isinstance(job_id, str) and str(uuid.UUID(job_id)) == job_id
    except (ValueError, AttributeError):
        valid_id = False
    if (not valid_id or not isinstance(manifest_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
            or isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float))
            or not math.isfinite(deadline_epoch) or not 0 < deadline_epoch < 2 ** 53):
        raise ValueError("Invalid watchdog identity or deadline")
    identity = {"schema_version": 1, "job_id": job_id, "manifest_sha256": manifest_sha256,
                "deadline_epoch": float(deadline_epoch)}
    return f'''
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
_identity={identity!r}
_root=Path('/content/web-comfyui')/_identity['job_id']
for _path in (_root,*_root.parents):
    if _path.is_symlink():
        raise RuntimeError('Watchdog directory is invalid')
_root.mkdir(parents=True,mode=0o700,exist_ok=True)
os.chmod(_root,0o700)
def _watchdog_ready():
    _marker=_root/'watchdog.json'
    if not _marker.exists():
        return False
    if _marker.is_symlink() or _marker.stat().st_size>4096:
        raise RuntimeError('Watchdog identity is invalid')
    _state=json.loads(_marker.read_text())
    if any(_state.get(k)!=v for k,v in _identity.items()):
        raise RuntimeError('Watchdog identity differs')
    if _state.get('phase')!='armed':
        raise RuntimeError('Watchdog already requested shutdown')
    _fd=os.open(_root/'.watchdog.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:
            fcntl.flock(_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(_fd)
if not _watchdog_ready():
    if time.time()>=_identity['deadline_epoch']:
        raise RuntimeError('Watchdog deadline already passed')
    _child=subprocess.Popen([sys.executable,'-c',{_WORKER!r},str(_root),str(_identity['deadline_epoch']),_identity['manifest_sha256']],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    _wait_until=time.monotonic()+5
    while not _watchdog_ready():
        if time.monotonic()>=_wait_until:
            raise RuntimeError('Watchdog readiness was not confirmed')
        time.sleep(.05)
print('WEB_COMFYUI_JSON='+json.dumps(dict(_identity,armed=True)))
'''
