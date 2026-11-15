import json,uuid
from pathlib import Path
import pytest
from review_r38_fixture import rig, session,submit
from colab_service import BridgeError

def running(r):
    st=session(r);got=submit(r);job=r.s.job(r.owner,got['request_id'])
    assert job['session_id']==st['id']
    r.s.sessions.attach(st,job)
    job=r.s.update(job['id'],phase='running',launch_requested_at=r.t[0])
    return st,job

def probe_kind(op,kw):
    if op!='remote_exec':return op
    code=kw['code']
    if "'heartbeat':True" in code:return 'heartbeat'
    if "'worker_dead':dead" in code:return 'job_probe'
    if "'bootstrap-status.json'" in code:return 'bootstrap_probe'
    if 'subprocess.Popen' in code:return 'launch'
    if 'archive_verified' in code:return 'extract'
    return 'other'

def running_status(job):return {'stdout':'WEB_COMFYUI_JSON='+json.dumps({'status':{'job_id':job['id'],'manifest_sha256':job['manifest_sha256'],'phase':'running'},'worker_dead':False})}

def test_confirm_two_faults_in_one_worker_cycle_are_not_terminal(rig):
    r=rig;st,job=running(r);sid=st['id'];seen=[]
    def fault(op,kw):
        if probe_kind(op,kw) in {'heartbeat','job_probe'}:
            seen.append(probe_kind(op,kw));raise BridgeError('execution_uncertain',True)
    r.b.hook=fault;r.t[0]+=10
    # The exact service._loop ordering, using both real tick implementations.
    r.s.sessions.tick_all();r.s.tick(job)
    s=r.s.sessions.get(sid);j=r.s.job(r.owner,job['id'])
    assert seen==['heartbeat','job_probe'] and s['phase']=='closing'
    assert s['fault_streak']==2 and s['last_tick']==st['last_tick']
    r.b.hook=None;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['released']
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1

def test_confirm_alternating_faulted_worker_cycles_renew_lease(rig):
    r=rig;st,job=running(r);sid=st['id'];first=r.t[0];bad=['heartbeat'];faults=[]
    def hook(op,kw):
        kind=probe_kind(op,kw)
        if kind==bad[0]:faults.append(kind);raise BridgeError('execution_uncertain',True)
        if kind=='job_probe':return running_status(job)
    r.b.hook=hook
    for i in range(16):
        bad[0]='heartbeat' if i%2==0 else 'job_probe';r.t[0]+=10
        before=r.s.sessions.get(sid)['last_tick']
        r.s.sessions.tick_all();r.s.tick(job)
        current=r.s.sessions.get(sid)
        assert current['last_tick']==st['last_tick']
        if i==0:assert current['phase']=='ready' and current['fault_streak']==1
        elif i==1:assert current['phase']=='closing' and current['fault_streak']==2
        else:assert current['phase']=='closed'
    assert len(faults)==2 and r.t[0]-first>90
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1

def test_confirm_slow_upload_after_heartbeat_retry_exceeds_fault_span(rig):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st)
    def first(op,kw):
        if probe_kind(op,kw)=='heartbeat':raise BridgeError('execution_uncertain',True)
    r.b.hook=first;r.s.sessions.tick_all();since=r.s.sessions.get(sid)['fault_since']
    def slow_upload(op,kw):
        if op=='upload':r.t[0]+=100;return {} # within the bridge's existing 150s upload timeout
    r.b.hook=slow_upload;r.t[0]+=10;r.s.sessions.tick_all();state=r.s.sessions.get(sid)
    assert r.t[0]-since==110 and state['phase']=='closing'
    assert not state.get('archive_uploaded') and not state.get('upload_completed_at')
    assert state['fault_streak']>=1 and state['fault_since']==since and state['terminal_fault']
    assert state['last_tick']==st['last_tick']
    assert next(kw['_timeout_seconds'] for op,kw in r.b.calls if op=='upload')==80
    r.b.hook=None;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['released']

@pytest.mark.parametrize('kind',['heartbeat','bootstrap_probe','job_probe'])
def test_second_same_probe_fault_closes_and_does_not_relaunch(rig,kind):
    r=rig
    if kind=='job_probe':st,job=running(r)
    else:
        st=session(r,'bootstrap' if kind=='bootstrap_probe' else 'ready',launch_at=r.t[0]);job=None
        if kind=='bootstrap_probe':r.s.sessions.freeze(st)
    sid=st['id'];last=st['last_tick']
    def fault(op,kw):
        if probe_kind(op,kw)==kind:raise BridgeError('execution_uncertain',True,{'error_type':'ReadTimeout','http_status':503,'transport_phase':'read','timeout_kind':'read'})
    r.b.hook=fault
    for i in range(2):
        r.t[0]+=10
        if job:r.s.tick(job)
        else:r.s.sessions.tick_all()
        s=r.s.sessions.get(sid)
        assert s['last_tick']==last
        assert s['phase']=='closing' if i else s['phase']!='closing'
    r.b.hook=None;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['released']
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1
    if job:
        receipt=r.a.poll(r.owner,job['id']);assert receipt['state']=='failed'
        assert receipt['diagnostics']['stage']=='session_fault' and receipt['diagnostics']['substage']==kind

@pytest.mark.parametrize('stage',['heartbeat','bootstrap_probe'])
def test_one_fault_then_complete_success_keeps_session(rig,stage):
    r=rig;st=session(r,'bootstrap' if stage=='bootstrap_probe' else 'ready',launch_at=r.t[0]);sid=st['id']
    if stage=='bootstrap_probe':r.s.sessions.freeze(st)
    def fault(op,kw):
        if probe_kind(op,kw)==stage:raise BridgeError('execution_uncertain',True)
    r.b.hook=fault;r.t[0]+=10;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['last_tick']==st['last_tick']
    r.b.hook=None;r.t[0]+=10;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['phase']=='ready'
    assert r.s.sessions.get(sid)['fault_streak']==0

def test_span_expired_before_retry_closes(rig):
    r=rig;st=session(r);sid=st['id']
    def fault(op,kw):
        if probe_kind(op,kw)=='heartbeat':raise BridgeError('execution_uncertain',True)
    r.b.hook=fault;r.s.sessions.tick_all();count=len(r.b.calls);r.t[0]+=90;r.b.hook=None;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['released']
    assert not any(probe_kind(op,kw)=='heartbeat' for op,kw in r.b.calls[count:])

@pytest.mark.parametrize('kind',['launch','extract','upload'])
def test_session_mutation_ambiguity_closes_without_retry(rig,kind):
    r=rig;st=session(r,'uploading');sid=st['id'];st=r.s.sessions.freeze(st)
    if kind in {'extract','launch'}:st=r.s.sessions.save(sid,archive_uploaded=True)
    if kind=='launch':st=r.s.sessions.save(sid,archive_verified=True,bootstrap_plan_id=next(iter(st['plan_catalog'])))
    hits=[]
    def fault(op,kw):
        if probe_kind(op,kw)==kind:hits.append(kind);raise BridgeError('execution_uncertain',True)
    r.b.hook=fault;r.s.sessions.tick_all();assert r.s.sessions.get(sid)['phase']=='closing'
    r.s.sessions.tick_all();assert r.s.sessions.get(sid)['released'] and len(hits)==1

def test_ambiguous_job_launch_is_not_reposted(rig):
    r=rig;st,job=running(r);r.s.update(job['id'],phase='preparing',upload_index=len(job['uploads']))
    hits=[]
    def hook(op,kw):
        if probe_kind(op,kw)=='launch':hits.append(kw['code']);raise BridgeError('execution_uncertain',True)
        if probe_kind(op,kw)=='job_probe':return running_status(job)
    r.b.hook=hook;r.s.tick(job);assert r.s.job(r.owner,job['id'])['phase']=='launch_uncertain'
    for _ in range(3):r.t[0]+=10;r.s.sessions.tick_all();r.s.tick(job)
    assert len(hits)==1
    assert r.s.sessions.get(st['id'])['phase']=='ready'
    assert r.s.job(r.owner,job['id'])['phase']=='running' or r.s.job(r.owner,job['id'])['phase']=='launch_uncertain'

@pytest.mark.parametrize('failed_stage',['heartbeat','job_probe'])
def test_only_complete_cycle_clears_shared_budget_and_collector_cannot(rig,failed_stage):
    r=rig;st,job=running(r);sid=st['id'];last=st['last_tick']
    def fail(op,kw):
        kind=probe_kind(op,kw)
        if kind==failed_stage:raise BridgeError('execution_uncertain',True)
        if kind=='job_probe':return running_status(job)
    r.b.hook=fail;r.t[0]+=10;r.s.sessions.tick_all();r.s.tick(job)
    faulted=r.s.sessions.get(sid)
    assert faulted['fault_streak']==1 and faulted['last_tick']==last
    r.a.poll(r.owner,job['id']);r.a.poll(r.owner,job['id'])
    assert r.s.sessions.get(sid)==faulted
    r.b.hook=lambda op,kw:running_status(job) if probe_kind(op,kw)=='job_probe' else None
    r.t[0]+=10;r.s.sessions.tick_all()
    half=r.s.sessions.get(sid)
    assert half['fault_streak']==1 and half['last_tick']==last
    r.s.tick(job);complete=r.s.sessions.get(sid)
    assert complete['phase']=='ready' and complete['fault_streak']==0 and complete['fault_since'] is None
    assert complete['last_tick']==r.t[0] and r.s.job(r.owner,job['id'])['phase']=='running'
    heartbeat=[kw['code'] for op,kw in r.b.calls if probe_kind(op,kw)=='heartbeat'][-1]
    assert str(last) in heartbeat


def test_slow_account_observation_is_bounded_and_cannot_commit_meter(rig):
    r=rig;st=session(r);sid=st['id']
    r.b.hook=lambda op,kw:(_ for _ in ()).throw(BridgeError('execution_uncertain',True)) if probe_kind(op,kw)=='heartbeat' else None
    r.s.sessions.tick_all();r.t[0]+=60
    def slow(op,kw):
        if op=='account_status':
            assert kw['_timeout_seconds']==30
            r.t[0]+=31
            return {'authenticated':True,'balance_compute_units':999}
    r.b.hook=slow;r.s.sessions.tick_all();state=r.s.sessions.get(sid)
    assert state['phase']=='closing' and state['last_tick']==st['last_tick']
    assert state['units_observed']==0 and state['usage_checked_at']==st['usage_checked_at']
    assert state['terminal_fault']['code']=='execution_uncertain'
    r.b.hook=None;r.s.sessions.tick_all();assert r.s.sessions.get(sid)['released']


@pytest.mark.parametrize('op',['upload','download','account_status','runtime_lookup','remote_exec'])
def test_every_rpc_uses_fresh_session_budget_even_for_stale_job(rig,op):
    r=rig;st,job=running(r);sid=st['id']
    r.s.sessions.save(sid,fault_streak=1,fault_since=r.t[0]-89,fault={'stage':'heartbeat','code':'execution_uncertain'})
    seen=[]
    def rpc(actual,kw):
        if actual==op:seen.append(kw);return {}
    r.b.hook=rpc;r.s._call(job,op)
    assert seen[0]['_timeout_seconds']==1
    if op=='remote_exec':assert seen[0]['timeout_seconds']==1
    assert r.s.sessions.get(sid)['fault_streak']==1


@pytest.mark.parametrize('field',['archive_uploaded','archive_verified','upload_completed_at','digest_verified_at'])
def test_success_commit_rechecks_expired_budget(rig,field):
    r=rig;st=session(r,'uploading');sid=st['id']
    fault={'stage':'heartbeat','code':'execution_uncertain'}
    r.s.sessions.save(sid,fault_streak=1,fault_since=r.t[0],fault=fault)
    r.t[0]+=90
    with pytest.raises(BridgeError):r.s.sessions.save(sid,**{field:True})
    state=r.s.sessions.get(sid)
    assert not state.get(field) and state['phase']=='closing' and state['terminal_fault']==fault
    assert state['last_tick']==st['last_tick']


def test_cycle_finalize_rechecks_expired_budget(rig):
    r=rig;st,job=running(r);sid=st['id']
    fault={'stage':'heartbeat','code':'execution_uncertain'}
    r.s.sessions.save(sid,fault_streak=1,fault_since=r.t[0],fault=fault,
                      cycle_session_ok=True,cycle_job=job['id'],cycle_faulted=False)
    r.t[0]+=90
    with pytest.raises(BridgeError):r.s.sessions.finish_cycle(sid,job['id'])
    state=r.s.sessions.get(sid)
    assert state['phase']=='closing' and state['fault_streak']==1 and state['last_tick']==st['last_tick']

@pytest.mark.parametrize('op',['upload','download','account_status','remote_exec'])
def test_bridge_subprocess_obeys_remaining_budget(rig,monkeypatch,tmp_path,op):
    from colab_service import Bridge
    from types import SimpleNamespace
    import colab_service
    def invoke(*args,**kwargs):
        assert kwargs['timeout']==0.5
        assert '_timeout_seconds' not in json.loads(kwargs['input'])
        return SimpleNamespace(stdout=json.dumps({'ok':True,'result':{}}),returncode=0)
    monkeypatch.setattr(colab_service.subprocess,'run',invoke)
    # The fixture forbids real bridge calls; restore this method only under a
    # subprocess mock, with an inert existing interpreter path.
    module=colab_service
    source=Path(module.__file__).read_text()
    import ast
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='Bridge')
    scope=dict(module.__dict__);exec(compile(ast.Module(body=[node],type_ignores=[]),'bridge-budget','exec'),scope)
    scope['Bridge']('/usr/bin/python3')(op,tmp_path,_timeout_seconds=0.5)


def test_closing_during_late_reply_cannot_erase_rpc_deadline(rig):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st)
    r.s.sessions.save(sid,fault_streak=1,fault_since=r.t[0],fault={'stage':'heartbeat','code':'execution_uncertain'})
    def slow(op,kw):
        if op=='upload':
            r.s.sessions.save(sid,phase='closing',close_requested=True)
            r.t[0]+=91;return {}
    r.b.hook=slow;r.s.sessions.tick_all();current=r.s.sessions.get(sid)
    assert current['phase']=='closing' and not current.get('archive_uploaded')
    assert not current.get('upload_completed_at') and current['last_tick']==st['last_tick']

@pytest.mark.parametrize('next_cycle',[False,True])
def test_shared_budget_survives_controller_restart(rig,next_cycle):
    from colab_service import ColabService
    r=rig;st,job=running(r);sid=st['id']
    r.b.hook=lambda op,kw:(_ for _ in ()).throw(BridgeError('execution_uncertain',True)) if probe_kind(op,kw)=='heartbeat' else running_status(job) if probe_kind(op,kw)=='job_probe' else None
    r.t[0]+=10;r.s.sessions.tick_all()
    if next_cycle:r.s.tick(job)
    # Real SQLite state, fresh controller object; no in-memory recovery state.
    r.s=ColabService(r.s.root,r.b,lambda:r.t[0])
    r.b.hook=lambda op,kw:(_ for _ in ()).throw(BridgeError('execution_uncertain',True)) if probe_kind(op,kw)=='job_probe' else None
    if next_cycle:r.t[0]+=10;r.s.sessions.tick_all()
    r.s.tick(job);state=r.s.sessions.get(sid)
    assert state['phase']=='closing' and state['fault_streak']==2
    assert state['last_tick']==st['last_tick']

def test_r40_combined_probe_heartbeat_one_rpc_and_fault6_fault7(rig):
    r=rig;st=session(r,'bootstrap',launch_at=r.t[0]);sid=st['id'];r.s.sessions.freeze(st)
    seen=[]
    def fault(op,kw):
        if op=='remote_exec':
            seen.append(kw['code']);raise BridgeError('execution_uncertain',True)
    r.b.hook=fault;r.t[0]+=10;r.s.sessions.tick_all()
    first=r.s.sessions.get(sid)
    assert first['fault_streak']==1 and first['last_tick']==st['last_tick']
    # Restart cannot clear the durable budget. A later probe+heartbeat failure
    # (the live fault-6/fault-7 pattern) cannot publish a partial success.
    from colab_service import ColabService
    r.s=ColabService(r.s.root,r.b,lambda:r.t[0]);r.t[0]+=24;r.s.sessions.tick_all()
    final=r.s.sessions.get(sid)
    assert len(seen)==2 and all('bootstrap-status.json' in code and "watchdog.lock" in code for code in seen)
    assert final['phase']=='closing' and final['fault_streak']==2 and final['last_tick']==st['last_tick']

def test_r40_complete_combined_probe_keeps_last_complete_heartbeat(rig):
    r=rig;st=session(r,'bootstrap',launch_at=r.t[0]);sid=st['id'];r.s.sessions.freeze(st)
    r.b.calls.clear();r.t[0]+=10;r.s.sessions.tick_all()
    calls=[kw['code'] for op,kw in r.b.calls if op=='remote_exec']
    assert len(calls)==1 and 'bootstrap-status.json' in calls[0] and 'watchdog.lock' in calls[0]
    assert f"write_text(str({st['last_tick']!r}))" in calls[0]
    assert r.s.sessions.get(sid)['last_tick']==r.t[0]

def test_r40_liveness_only_probe_does_not_renew(rig):
    r=rig;st=session(r,'bootstrap',launch_at=r.t[0]);sid=st['id'];r.s.sessions.freeze(st)
    def live(op,kw):
        if op=='remote_exec':return {'stdout':'WEB_COMFYUI_JSON='+json.dumps({'phase':'running','alive':True,'progress_at':r.t[0]-121})}
    r.b.hook=live;r.t[0]+=10;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['last_tick']==st['last_tick']
    assert r.s.sessions.get(sid)['phase']=='bootstrap'
    assert r.s.sessions.get(sid)['fault_streak']==1
    r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['last_tick']==st['last_tick']
    assert r.s.sessions.get(sid)['phase']=='closing'
