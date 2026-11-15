import contextlib,fcntl,json,os,subprocess,uuid
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from review_r38_fixture import rig, session,submit
from colab_service import BridgeError,Bridge
from scripts import colab_transport_diagnostics as td
from scripts import colab_bridge as cb
from scripts.colab_session_watchdog import heartbeat
from test_review_r38_faults import probe_kind,running

SECRET='PRIVATE_SENTINEL_秘密'
TEXT='https://user:'+SECRET+'@example.invalid/x?token='+SECRET+' /home/private/'+SECRET+' owner@example.invalid Authorization: Bearer '+SECRET

@pytest.mark.parametrize('name',['ReadTimeout','ConnectTimeout','HTTPStatusError','SDK秘密Error','ConnectionResetError'])
@pytest.mark.parametrize('where',['transport','execute','stop'])
def test_actual_bridge_metadata_never_uses_exception_text(rig,name,where,capsys):
    r=rig;error=type(name,(Exception,),{})(TEXT);error.response=NS(status_code=503,headers={'Authorization':TEXT});error.__cause__=ValueError(TEXT)
    bridge=cb.Bridge.__new__(cb.Bridge)
    bridge.runtime=lambda rid:{'url':'https://example.invalid','token':'synthetic-token'}
    class Runtime:
        def __init__(self,*a,**kw):pass
        def execute_code(self,*a,**kw):
            if where=='execute':raise error
            return []
        def stop(self,**kw):
            if where=='stop':raise error
    @contextlib.contextmanager
    def transport():
        if where=='transport':raise error
        yield
    bridge.sdk=NS(Runtime=Runtime,execution_transport=transport)
    with pytest.raises(cb.BridgeError) as caught:bridge.remote_exec({'runtime_id':str(uuid.uuid4()),'code':'pass','timeout_seconds':45})
    public=caught.value.public();assert SECRET not in json.dumps(public) and TEXT not in json.dumps(public)
    assert public['diagnostics']['error_type']==(name if name in td.CLASSES else 'Exception')
    assert public['diagnostics']['http_status']==503
    st=session(r);got=submit(r);rid=got['request_id']
    def fail(op,kw):
        if probe_kind(op,kw)=='heartbeat':raise BridgeError(public['code'],public['ambiguous'],{**public['diagnostics'],'url':TEXT,'headers':{'Authorization':TEXT}})
    r.b.hook=fail
    for _ in range(2):r.t[0]+=10;r.s.sessions.tick_all()
    r.b.hook=None;r.s.sessions.tick_all()
    receipt=r.a.poll(r.owner,rid);snapshot=r.s.sessions.snapshot(r.owner)
    assert receipt['state']=='failed' and receipt['diagnostics']['stage']=='session_fault'
    assert receipt['diagnostics']['error_type']==public['diagnostics']['error_type']
    assert SECRET not in json.dumps([receipt,snapshot],ensure_ascii=False)
    assert not r.s.sessions.snapshot('foreign')['session'].get('fault')
    captured=capsys.readouterr();assert SECRET not in captured.out+captured.err

def test_transport_sanitize_rejects_adversarial_values():
    value={k:TEXT for k in ['error_type','transport_phase','timeout_kind','http_status','url','headers']}
    assert td.sanitize(value)=={}
    assert td.sanitize({'http_status':True})=={}
    assert td.capture(type('秘密'+SECRET,(Exception,),{})(TEXT))=={'error_type':'Exception','transport_phase':'wait'}

@pytest.mark.parametrize('change',['none','pid','start','deadline','inode','unlocked'])
def test_real_generated_heartbeat_verifies_identity_and_lock(tmp_path,change):
    sid=str(uuid.uuid4());p=tmp_path/'session';p.mkdir()
    f=os.open(p/'watchdog.lock',os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(f,fcntl.LOCK_EX)
    identity={'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],'deadline':1000000}
    (p/'watchdog.json').write_text(json.dumps(identity));st=os.fstat(f);expected={**identity,'lock_dev':st.st_dev,'lock_ino':st.st_ino}
    (p/'heartbeat').write_text('old')
    if change=='pid':identity['pid']+=10000000
    elif change=='start':identity['start_ticks']='0'
    elif change=='deadline':identity['deadline']+=1
    elif change=='inode':(p/'watchdog.lock').rename(p/'old-lock');(p/'watchdog.lock').touch()
    elif change=='unlocked':fcntl.flock(f,fcntl.LOCK_UN)
    (p/'watchdog.json').write_text(json.dumps(identity))
    code=heartbeat(sid,expected,1234.0).replace('/content/castle-sessions/'+sid,str(p))
    try:
        if change=='none':exec(compile(code,'offline-heartbeat','exec'),{});assert (p/'heartbeat').read_text()=='1234.0'
        else:
            with pytest.raises((AssertionError,RuntimeError,OSError)):exec(compile(code,'offline-heartbeat','exec'),{})
            assert (p/'heartbeat').read_text()=='old'
    finally:os.close(f)

def test_local_runtime_binding_mismatch_closes_before_exec(rig):
    r=rig;st=session(r);r.s.sessions.save(st['id'],watchdog_runtime_id='different-runtime')
    r.b.calls.clear();r.s.sessions.tick_all()
    assert r.s.sessions.get(st['id'])['phase']=='closing'
    assert not any(op=='remote_exec' for op,_ in r.b.calls)

@pytest.mark.parametrize('faulted',[False,True])
def test_cancel_and_system_fault_interleaving(rig,faulted):
    r=rig;st=session(r);got=submit(r);rid=got['request_id'];sid=st['id']
    if faulted:
        def fail(op,kw):
            if probe_kind(op,kw)=='heartbeat':raise BridgeError('execution_uncertain',True)
        r.b.hook=fail;r.s.sessions.tick_all()
    r.s.cancel(r.owner,rid)
    if faulted:
        r.t[0]+=10;r.s.sessions.tick_all();r.b.hook=None;r.s.sessions.tick_all()
        # Confirm defect: explicit queued-job cancellation is overwritten by
        # a subsequent system fault rather than preserved alongside diagnostics.
        receipt=r.a.poll(r.owner,rid);assert receipt['state']=='cancelled'
        assert receipt['diagnostics']['stage']=='session_fault'
        assert r.s.job(r.owner,rid)['cancel_requested'] is True
    else:
        r.s.tick(r.s.job(r.owner,rid));assert r.a.poll(r.owner,rid)['state']=='cancelled'

def test_archive_upload_timestamps_are_acknowledged_and_ordered(rig):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st);first=r.t[0]
    def fail(op,kw):
        if op=='upload':raise BridgeError('upload_transport_failed')
    r.b.hook=fail;r.s.sessions.tick_all();s=r.s.sessions.get(sid)
    assert s['upload_started_at']==first and not s.get('upload_completed_at') and not s.get('digest_verified_at')
    r.b.hook=None;r.t[0]+=10;r.s.sessions.tick_all();s=r.s.sessions.get(sid)
    assert s['upload_started_at']==first and s['upload_completed_at']==r.t[0] and not s.get('digest_verified_at')
    r.t[0]+=10;r.s.sessions.tick_all();s=r.s.sessions.get(sid)
    assert s['digest_verified_at']==r.t[0] and s['archive_verified']

@pytest.mark.parametrize('active_job',[False,True])
def test_cancel_before_fault_settles_through_actual_worker_order(rig,active_job):
    r=rig
    if active_job:st,job=running(r);rid=job['id']
    else:st=session(r,'uploading');rid=submit(r)['request_id'];job=r.s.job(r.owner,rid)
    def fail(op,kw):
        if probe_kind(op,kw)=='heartbeat':raise BridgeError('execution_uncertain',True)
        if probe_kind(op,kw)=='job_probe':
            from test_review_r38_faults import running_status
            return running_status(job)
    r.b.hook=fail;r.s.sessions.tick_all();r.s.tick(job)
    r.s.cancel(r.owner,rid);r.t[0]+=10
    r.s.sessions.tick_all();r.s.tick(job)
    receipt=r.a.poll(r.owner,rid)
    assert receipt['state']=='cancelled' and receipt['diagnostics']['stage']=='session_fault'
    assert r.s.sessions.get(st['id'])['released']


def test_system_close_flag_is_not_user_cancel(rig):
    r=rig;st=session(r,'uploading');rid=submit(r)['request_id']
    r.s.sessions.save(st['id'],phase='closing',close_requested=True,reason='watchdog_or_hard_cap')
    r.s.sessions.tick_all()
    assert r.a.poll(r.owner,rid)['state']=='failed'


def test_plan_failure_does_not_infer_cancel_from_system_close_flag(rig):
    r=rig;st=session(r,'bootstrap');sid=st['id'];rid=submit(r)['request_id']
    st=r.s.sessions.freeze(st);pid=next(iter(st['plan_catalog']))
    r.s.update(rid,bootstrap_plan_id=pid)
    st=r.s.sessions.save(sid,bootstrap_plan_id=pid,close_requested=True,reason='watchdog_state_lost')
    r.s.sessions.fail_plan(st,{'stage':'session_bootstrap','error_code':'stage_failed'},'bootstrap_failed')
    assert r.s.job(r.owner,rid)['phase']=='failed'
