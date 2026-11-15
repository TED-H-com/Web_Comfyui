"""r40 rev3: execute generated probes with real local PID/flock, no RPC/network."""
import ast,contextlib,fcntl,io,json,os
from pathlib import Path
import pytest
from review_r38_fixture import rig,session,submit
from colab_service import BridgeError
from colab_sessions import WorkSessions,bootstrap_progress_fault
from compute_parity.provisioning import cap_exemptions

@pytest.mark.parametrize('value,previous,kind',[(1000005,None,'progress_future'),(float('nan'),None,'progress_nonfinite'),(float('inf'),None,'progress_nonfinite'),(999879,None,'progress_stale'),(999999,1000000,'progress_regressed')])
def test_generated_invalid_progress_preserves_budget(rig,tmp_path,monkeypatch,value,previous,kind):
    run_probe(rig,tmp_path,monkeypatch,value,previous=previous,expected=kind,prior_fault=True)


def run_probe(r,tmp_path,monkeypatch,progress,previous=None,expected=None,prior_fault=False,heartbeat='good',tamper=None):
    st=session(r,'bootstrap',launch_at=r.t[0],bootstrap_progress=previous);sid=st['id'];r.s.sessions.freeze(st)
    remote=tmp_path/'remote';remote.mkdir()
    identity={'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}
    (remote/'bootstrap-process.json').write_text(json.dumps(identity))
    (remote/'bootstrap-status.json').write_text(json.dumps({'phase':'running','progress_at':progress}))
    lock=(remote/'watchdog.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stat=os.fstat(lock.fileno());wd={**identity,'deadline':r.t[0]+21600,'lock_dev':stat.st_dev,'lock_ino':stat.st_ino}
    (remote/'watchdog.json').write_text(json.dumps(wd))
    if heartbeat!='missing':(remote/'heartbeat').write_text(str(r.t[0]-(301 if heartbeat=='stale' else 0)))
    before_heartbeat=(remote/'heartbeat').read_bytes() if (remote/'heartbeat').exists() else None
    r.s.sessions.save(sid,bootstrap_identity=identity,watchdog_identity=wd)
    if prior_fault:
        r.s.sessions.operation(st,'bootstrap_probe');r.s.sessions.record_fault(st,BridgeError('execution_uncertain',True))
    before=r.s.sessions.get(sid);r.t[0]+=.1
    def execute(state,code):
        assert 'bootstrap-status.json' in code
        code=code.replace('/content/castle-sessions/'+sid,str(remote)).replace('time.time()',str(r.t[0]))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):exec(compile(code,'generated-combined-probe','exec'),{})
        result=json.loads(output.getvalue().strip().split('WEB_COMFYUI_JSON=')[1])
        if tamper:tamper(result)
        return result
    monkeypatch.setattr(r.s,'_exec',execute)
    try:r.s.sessions.tick_all()
    finally:lock.close()
    after=r.s.sessions.get(sid)
    if expected:
        assert after['last_tick']==before['last_tick']
        assert after['fault_streak']==(2 if prior_fault else 1)
        assert after['phase']==('closing' if prior_fault else 'bootstrap')
        assert after['fault']['ambiguous'] and after['fault']['stage']=='bootstrap_probe'
        assert after['private_rpc_error']=={'bootstrap_probe':{'type':expected}}
        assert 'bootstrap_probe' not in json.dumps(r.s.sessions.public(after).get('private_rpc_error'))
        if not tamper:
            assert ((remote/'heartbeat').read_bytes() if (remote/'heartbeat').exists() else None)==before_heartbeat
    else:
        assert after['last_tick']==r.t[0] and after['fault_streak']==0
        assert after['phase']=='bootstrap'
        # Remote renewal uses the preceding COMPLETE cycle, never current partial progress.
        assert float((remote/'heartbeat').read_text())==before['last_tick']
    return after

@pytest.mark.parametrize('heartbeat',['missing','stale'])
def test_generated_heartbeat_file_required(rig,tmp_path,monkeypatch,heartbeat):
    run_probe(rig,tmp_path,monkeypatch,rig.t[0],heartbeat=heartbeat,expected='heartbeat_unavailable')

@pytest.mark.parametrize('field,value,kind',[
    ('heartbeat',False,'heartbeat_ack_missing'),('watchdog_identity',{},'heartbeat_identity_mismatch'),
    ('heartbeat_cycle','old-cycle','heartbeat_cycle_mismatch'),('heartbeat_at',0,'heartbeat_timestamp_mismatch')])
def test_ack_bound_to_identity_and_current_cycle(rig,tmp_path,monkeypatch,field,value,kind):
    run_probe(rig,tmp_path,monkeypatch,rig.t[0],expected=kind,tamper=lambda result:result.update({field:value}))


def test_one_fault_then_actual_complete_probe_recovers(rig,tmp_path,monkeypatch):
    run_probe(rig,tmp_path,monkeypatch,rig.t[0],prior_fault=True)


def test_fixed_future_tolerance():
    assert bootstrap_progress_fault(100,101) is None
    assert bootstrap_progress_fault(100,101.001)=='progress_future'
    assert bootstrap_progress_fault(100,100,101)=='progress_regressed'


def verified(r,**kw):
    v={'owner':r.owner,'account_id':r.account,'subject_key':'b'*64,'balance':1000,'identity_at':r.t[0],'balance_at':r.t[0]};v.update(kw)
    return session(r,'allocating',admission_verification=v)


def busy(r):
    def hook(op,kw):
        if op in {'account_status','account_identity'}:raise BridgeError('account_busy')
    r.b.hook=hook


def rejected(r):
    busy(r);got=submit(r)
    assert got['state']=='rejected' and got['error']['code']=='account_busy'
    row=r.j.get(r.owner,got['request_id'])
    assert cap_exemptions([row],r.s.root/'state.sqlite3',r.owner)=={row['request_id']:'admission_only'}


def test_released_snapshot_is_uncounted_busy(rig):
    st=verified(rig);rig.s.sessions.save(st['id'],released=True);rejected(rig)


def test_clock_rewind_never_revives_snapshot(rig):
    r=rig;verified(r,identity_at=r.t[0]-500,balance_at=r.t[0]-500)
    r.t[0]+=150;assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
    r.t[0]-=200;assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
    rejected(r)

@pytest.mark.parametrize('restart',[False,True])
@pytest.mark.parametrize('field',['last_tick','created_at','upload_started_at','digest_verified_at','bootstrap_progress','fault','admission_verification'])
def test_snapshot_checks_all_durable_observations(rig,field,restart):
    r=rig;st=verified(r);value=r.t[0]+10
    if field=='fault':value={'at':value}
    if field=='admission_verification':value={**st[field],'balance_at':value}
    # Inject a durable observation directly: save() correctly refuses future last_tick.
    with r.s.db() as db:
        st[field]=value;db.execute('UPDATE work_sessions SET state=? WHERE id=?',(json.dumps(st),st['id']))
    if restart:r.s.sessions=WorkSessions(r.s)
    assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
    rejected(r)


def test_snapshot_monotonic_bound_and_process_rewind(rig):
    r=rig;verified(r);mono=[0.];r.s.sessions._monotonic=lambda:mono[0]
    assert r.s.sessions.admission_snapshot(r.owner,r.account)
    r.t[0]+=200;mono[0]=200
    assert r.s.sessions.admission_snapshot(r.owner,r.account)
    r.t[0]-=100;mono[0]+=1
    assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
    r.t[0]+=200;mono[0]=601
    assert r.s.sessions.admission_snapshot(r.owner,r.account) is None


def test_snapshot_restart_valid_durable_remainder(rig):
    r=rig;verified(r,identity_at=r.t[0]-500,balance_at=r.t[0]-500)
    r.s.sessions=WorkSessions(r.s);mono=[0.];r.s.sessions._monotonic=lambda:mono[0]
    assert r.s.sessions.admission_snapshot(r.owner,r.account)
    mono[0]=101
    assert r.s.sessions.admission_snapshot(r.owner,r.account) is None


def test_open_captures_monotonic_age_before_first_busy_fallback(rig):
    r=rig;mono=[0.];r.s.sessions._monotonic=lambda:mono[0]
    r.s.sessions.open(r.owner,r.account)
    mono[0]=601  # Wall clock froze; no fallback has ever read the snapshot.
    assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
    rejected(r)


@pytest.mark.parametrize('field,value',[('last_tick',None),('created_at',None),('created_at',float('inf'))])
def test_snapshot_unknown_clock_evidence_is_uncounted_busy(rig,field,value):
    r=rig;st=verified(r);st[field]=value
    with r.s.db() as db:db.execute('UPDATE work_sessions SET state=? WHERE id=?',(json.dumps(st),st['id']))
    assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
    rejected(r)
