"""Runtime-side session fail-closed watchdog; no provider work on import."""
import math
import uuid

WORKER = r'''
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]);deadline=float(sys.argv[2])
fd=os.open(root/'watchdog.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:sys.exit(0)
(root/'watchdog.json').write_text(json.dumps({'deadline':deadline,'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}))
while time.time()<deadline:
    try:
        alive=time.time()-float((root/'heartbeat').read_text())<300
        intact=(root/'watchdog.json').is_file()
    except (OSError,ValueError):alive=intact=False
    if not alive or not intact or (root/'close').exists():break
    time.sleep(5)
# Keep requesting release until the runtime disappears, even if an RPC fails.
while True:
    try:subprocess.run([sys.executable,'-c','from google.colab import runtime; runtime.unassign()'],timeout=20,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except (OSError,subprocess.TimeoutExpired):pass
    time.sleep(10)
'''


def build(sid, deadline):
    assert str(uuid.UUID(sid)) == sid
    assert type(deadline) in (int,float) and math.isfinite(deadline) and deadline > 0
    return f'''
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
p=Path('/content/castle-sessions/{sid}')
assert not any(q.is_symlink() for q in (p,*p.parents))
p.mkdir(parents=True,exist_ok=True,mode=0o700)
def alive():
    f=os.open(p/'watchdog.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
        except BlockingIOError:return True
    finally:os.close(f)
if (p/'watchdog.json').exists():
    assert json.loads((p/'watchdog.json').read_text())['deadline']=={deadline!r}
(p/'heartbeat').write_text(str(time.time()))
if not alive():
    subprocess.Popen([sys.executable,'-c',{WORKER!r},str(p),str({deadline!r})],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
limit=time.monotonic()+5
while not alive() or not (p/'watchdog.json').exists():
    assert time.monotonic()<limit
    time.sleep(.05)
identity=json.loads((p/'watchdog.json').read_text());info=(p/'watchdog.lock').stat()
identity.update(lock_dev=info.st_dev,lock_ino=info.st_ino)
print('WEB_COMFYUI_JSON='+json.dumps({{'armed':True,'session_id':{sid!r},'watchdog_identity':identity}}))
'''


def heartbeat(sid, identity, at):
    """Identity-bound idempotent write: never starts or replaces a watchdog."""
    import json
    assert str(uuid.UUID(sid)) == sid
    assert isinstance(identity,dict) and set(identity)=={'pid','start_ticks','deadline','lock_dev','lock_ino'}
    return f'''
import json,os,fcntl
from pathlib import Path
p=Path('/content/castle-sessions/{sid}')
expected=json.loads({json.dumps(identity)!r})
assert not any(q.is_symlink() for q in (p,*p.parents))
actual=json.loads((p/'watchdog.json').read_text())
fd=os.open(p/'watchdog.lock',os.O_RDWR|os.O_NOFOLLOW)
try:
    info=os.fstat(fd);actual.update(lock_dev=info.st_dev,lock_ino=info.st_ino)
    assert actual==expected
    proc=Path('/proc/'+str(int(actual['pid']))+'/stat').read_text().rsplit(')',1)[1].split()
    assert proc[0]!='Z' and proc[19]==actual['start_ticks']
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);raise RuntimeError('watchdog_dead')
    except BlockingIOError:pass
    (p/'heartbeat').write_text(str({at!r}))
finally:os.close(fd)
print('WEB_COMFYUI_JSON='+json.dumps({{'heartbeat':True,'watchdog_identity':actual}}))
'''
