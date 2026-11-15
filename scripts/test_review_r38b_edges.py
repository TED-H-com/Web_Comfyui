import ast,json,subprocess
from types import SimpleNamespace as NS
import pytest
import colab_service
from colab_service import ColabService,Bridge,BridgeError,TERMINAL
from review_r38_fixture import rig,session,submit
from test_review_r38_faults import running,probe_kind,running_status

REAL_BRIDGE_CALL=Bridge.__call__

def cycle(r):
    with r.s.db() as db:jobs=[json.loads(row[0]) for row in db.execute('SELECT state FROM jobs')]
    r.s.sessions.tick_all()
    for job in jobs:
        if job['phase'] not in TERMINAL:r.s.tick(job)

def fail_kind(kind):
    def hook(op,kw):
        if probe_kind(op,kw)==kind:raise BridgeError('execution_uncertain',True)
    return hook

def test_real_loop_two_faults_and_release(rig,monkeypatch):
    r=rig;st,job=running(r);seen=[];ticks=[]
    def hook(op,kw):
        kind=probe_kind(op,kw)
        if kind in {'heartbeat','job_probe'}:
            seen.append(kind);raise BridgeError('execution_uncertain',True)
    class StopLoop(BaseException):pass
    def sleep(seconds):
        ticks.append(r.s.sessions.get(st['id']))
        if len(ticks)==2:raise StopLoop()
        r.t[0]+=seconds
    monkeypatch.setattr(colab_service.time,'sleep',sleep);r.b.hook=hook
    with pytest.raises(StopLoop):r.s._loop()
    assert seen==['heartbeat','job_probe']
    assert ticks[0]['phase']=='closing' and ticks[1]['phase']=='closed'
    assert all(s['last_tick']==st['last_tick'] for s in ticks)
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1

@pytest.mark.parametrize('fault_stage',['heartbeat','job_probe'])
@pytest.mark.parametrize('restart',[False,True])
def test_reads_and_restart_cannot_clear_open_cycle(rig,fault_stage,restart):
    r=rig;st,job=running(r);sid=st['id'];r.t[0]+=10
    def hook(op,kw):
        if probe_kind(op,kw)==fault_stage:raise BridgeError('execution_uncertain',True)
        if probe_kind(op,kw)=='job_probe':return running_status(job)
    r.b.hook=hook;cycle(r);faulted=r.s.sessions.get(sid)
    assert faulted['fault_streak']==1 and faulted['last_tick']==st['last_tick']
    for _ in range(3):r.a.poll(r.owner,job['id']);r.s.sessions.snapshot(r.owner);r.s.public_job(r.s.job(r.owner,job['id']))
    assert r.s.sessions.get(sid)==faulted
    if restart:r.s=ColabService(r.s.root,r.b,lambda:r.t[0])
    r.b.hook=lambda op,kw:running_status(job) if probe_kind(op,kw)=='job_probe' else None
    r.t[0]+=10;r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['last_tick']==st['last_tick']
    assert r.s.sessions.get(sid)['fault_streak']==1
    code=[kw['code'] for op,kw in r.b.calls if probe_kind(op,kw)=='heartbeat'][-1]
    assert f"write_text(str({st['last_tick']!r}))" in code
    r.s.tick(job);good=r.s.sessions.get(sid)
    assert good['last_tick']==r.t[0] and good['fault_streak']==0

@pytest.mark.parametrize('restart',[False,True])
def test_no_active_job_recovery_budget_survives_restart(rig,restart):
    r=rig;st=session(r);r.b.hook=fail_kind('heartbeat');r.t[0]+=10;cycle(r)
    if restart:r.s=ColabService(r.s.root,r.b,lambda:r.t[0])
    r.t[0]+=90;r.b.hook=None;r.b.calls.clear();cycle(r)
    assert r.s.sessions.get(st['id'])['released']
    assert not any(op=='remote_exec' for op,_ in r.b.calls)

@pytest.mark.parametrize('faulted',[False,True])
def test_finished_job_midcycle_and_next_job_budget(rig,faulted):
    r=rig;st,job=running(r);sid=st['id'];nextid=submit(r)['request_id']
    # Finish the real cleanup path; only remote cleanup evidence is mocked.
    r.s.update(job['id'],phase='cleanup_pending',outcome='completed')
    def hook(op,kw):
        if faulted and probe_kind(op,kw)=='heartbeat':raise BridgeError('execution_uncertain',True)
        if op=='remote_exec' and 'reusable-clean.json' in kw['code']:
            return {'stdout':'WEB_COMFYUI_JSON='+json.dumps({'clean':True,'job_id':job['id'],'manifest_sha256':job['manifest_sha256']})}
    r.b.hook=hook;r.t[0]+=10;cycle(r);state=r.s.sessions.get(sid)
    assert r.s.job(r.owner,job['id'])['released'] and state['active_job'] is None
    assert state['last_tick']==(st['last_tick'] if faulted else r.t[0])
    assert state['fault_streak']==(1 if faulted else 0)
    # New job is attached next cycle; an earlier fault cannot disappear at attach.
    r.b.hook=None;r.t[0]+=10;r.s.sessions.tick_all();state=r.s.sessions.get(sid)
    assert state['active_job']==nextid and state['fault_streak']==(1 if faulted else 0)
    r.b.hook=lambda op,kw:(_ for _ in ()).throw(BridgeError('upload_transport_failed')) if op=='upload' else None
    r.s.tick(r.s.job(r.owner,nextid));state=r.s.sessions.get(sid)
    assert state['phase']==('closing' if faulted else 'ready')

@pytest.mark.parametrize('op',['upload','download','account_status','runtime_lookup','remote_exec'])
def test_every_rpc_uses_durable_remaining_and_rejects_late_reply(rig,op):
    r=rig;st,job=running(r);sid=st['id']
    r.b.hook=fail_kind('job_probe');cycle(r)
    r.t[0]+=89.5;seen=[]
    def hook(actual,kw):
        if actual==op:
            seen.append(kw);r.t[0]+=.6;return {}
    r.b.hook=hook
    with pytest.raises(BridgeError):r.s._call(job,op)
    assert len(seen)==1 and seen[0]['_timeout_seconds']==.5
    assert r.s.sessions.get(sid)['phase']=='closing'
    assert r.s.sessions.get(sid)['last_tick']==st['last_tick']

@pytest.mark.parametrize('op',['upload','download','account_status','runtime_lookup','remote_exec'])
def test_actual_bridge_parent_timeout_is_bounded(rig,monkeypatch,tmp_path,op):
    calls=[]
    def run(*a,**kw):
        calls.append(kw);return NS(stdout=json.dumps({'ok':True,'result':{}}),returncode=0)
    monkeypatch.setattr(colab_service.subprocess,'run',run)
    assert REAL_BRIDGE_CALL(Bridge('/usr/bin/python3'),op,tmp_path,_timeout_seconds=.25)=={}
    assert calls[0]['timeout']==.25
    assert '_timeout_seconds' not in json.loads(calls[0]['input'])

@pytest.mark.parametrize('phase',['queued','running'])
def test_actual_cycle_cancellation_keeps_fault(rig,phase):
    r=rig
    if phase=='queued':st=session(r);rid=submit(r)['request_id']
    else:st,job=running(r);rid=job['id']
    r.b.hook=fail_kind('heartbeat');r.s.sessions.tick_all();r.s.cancel(r.owner,rid)
    r.t[0]+=10;cycle(r);r.b.hook=None;cycle(r)
    receipt=r.a.poll(r.owner,rid)
    assert receipt['state']=='cancelled' and receipt['diagnostics']['stage']=='session_fault'
    assert r.s.sessions.get(st['id'])['released']

def test_plain_system_close_does_not_mean_cancel(rig):
    r=rig;st=session(r);rid=submit(r)['request_id']
    r.s.sessions.save(st['id'],phase='closing',close_requested=True,reason='watchdog_or_hard_cap')
    cycle(r);assert r.a.poll(r.owner,rid)['state']=='failed'

def test_clock_rewind_can_extend_single_fault_budget(rig):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st)
    r.b.hook=fail_kind('heartbeat');cycle(r);since=r.s.sessions.get(sid)['fault_since']
    r.t[0]-=120 # host wall clock steps backwards after the durable fault
    seen=[]
    def upload(op,kw):
        if op=='upload':seen.append(kw);r.t[0]+=140;return {}
    r.b.hook=upload;cycle(r);state=r.s.sessions.get(sid)
    assert not seen and not state.get('archive_uploaded') and not state.get('upload_completed_at')
    assert state['last_tick']==st['last_tick'] and state['phase']=='closed'
    assert state['terminal_fault']['code']=='clock_inconsistent'
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1
    assert not any(op in {'upload','runtime_allocate'} for op,_ in r.b.calls)

def test_missing_session_breaks_preexisting_orphan_release(rig):
    r=rig;st,job=running(r)
    with r.s.db() as db:db.execute('DELETE FROM work_sessions WHERE id=?',(st['id'],))
    r.s.tick(job);r.s.tick(job)
    assert r.s.job(r.owner,job['id'])['orphan_session']
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1
    assert r.s.job(r.owner,job['id'])['released']

@pytest.mark.parametrize('mutation',['job_launch','bootstrap_launch','archive_extract'])
def test_expired_mutation_reply_never_relaunches(rig,mutation):
    r=rig
    if mutation=='job_launch':
        st,job=running(r);r.s.update(job['id'],phase='preparing',upload_index=len(job['uploads']))
    else:
        st=session(r,'uploading');st=r.s.sessions.freeze(st)
        st=r.s.sessions.save(st['id'],archive_uploaded=True)
        if mutation=='bootstrap_launch':
            rid=submit(r)['request_id'];pid=next(iter(st['plan_catalog']))
            r.s.update(rid,bootstrap_plan_id=pid)
            st=r.s.sessions.save(st['id'],archive_verified=True,bootstrap_plan_id=pid)
    r.b.hook=fail_kind('heartbeat');r.s.sessions.tick_all()
    r.t[0]+=10;hits=[]
    def late(op,kw):
        if probe_kind(op,kw)==('extract' if mutation=='archive_extract' else 'launch'):
            hits.append(kw);assert kw['_timeout_seconds']==75
            r.t[0]+=81
            return {'stdout':'WEB_COMFYUI_JSON='+json.dumps({'archive_verified':True,'launched':True,'pid':1,'start_ticks':'42'})}
    r.b.hook=late;cycle(r);s=r.s.sessions.get(st['id'])
    assert hits and s['phase'] in {'closing','closed'} and s['last_tick']==st['last_tick']
    if mutation=='archive_extract':assert not s['archive_verified']
    r.b.hook=None;cycle(r)
    assert r.s.sessions.get(st['id'])['released'] and len(hits)==1

def test_slow_account_reply_never_commits_meter(rig):
    r=rig;st=session(r);r.b.hook=fail_kind('heartbeat');cycle(r)
    r.t[0]+=60
    def slow(op,kw):
        if op=='account_status':
            assert kw['_timeout_seconds']==30;r.t[0]+=31
            return {'authenticated':True,'balance_compute_units':900}
    r.b.hook=slow;cycle(r);s=r.s.sessions.get(st['id'])
    assert s['phase']=='closing' and s['units_observed']==0 and s['usage_checked_at']==st['usage_checked_at']
    assert s['last_tick']==st['last_tick']

@pytest.mark.parametrize('restart',[False,True])
@pytest.mark.parametrize('field',['fault_since','fault_at','last_tick'])
@pytest.mark.parametrize('bad',['future','nan','inf'])
def test_inconsistent_durable_time_closes_without_work_rpc(rig,restart,field,bad):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st)
    r.b.hook=fail_kind('heartbeat');cycle(r)
    value=r.t[0]+120 if bad=='future' else float(bad)
    corrupt=r.s.sessions.get(sid)
    if field=='fault_at':corrupt['fault']['at']=value
    else:corrupt[field]=value
    with r.s.db() as db:db.execute('UPDATE work_sessions SET state=? WHERE id=?',(json.dumps(corrupt),sid))
    if restart:r.s=ColabService(r.s.root,r.b,lambda:r.t[0])
    r.b.hook=None;r.b.calls.clear();cycle(r);state=r.s.sessions.get(sid)
    assert state['phase']=='closed' and state['released']
    assert state['terminal_fault']['code']=='clock_inconsistent'
    assert not any(op in {'remote_exec','upload','download','runtime_allocate'} for op,_ in r.b.calls)
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1

@pytest.mark.parametrize('faulted',[False,True])
def test_forward_wall_step_obeys_existing_caps_without_false_clock_fault(rig,faulted):
    r=rig;st=session(r);sid=st['id']
    if faulted:r.b.hook=fail_kind('heartbeat');cycle(r)
    r.b.hook=None;r.t[0]+=30;cycle(r)
    assert r.s.sessions.get(sid)['phase']=='ready'
    assert r.s.sessions.get(sid)['last_tick']==r.t[0]
    r.t[0]=st['deadline_epoch']+1;cycle(r)
    assert r.s.sessions.get(sid)['released']
    assert (r.s.sessions.get(sid).get('terminal_fault') or {}).get('code')!='clock_inconsistent'

@pytest.mark.parametrize('op',['upload','account_status'])
def test_monotonic_elapsed_deadline_rejects_late_reply_despite_small_wall_delta(rig,op):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st);mono=[100.0]
    r.s.sessions._monotonic=lambda:mono[0]
    r.b.hook=fail_kind('heartbeat');cycle(r);r.t[0]+=10;mono[0]+=10
    def late(actual,kw):
        if actual==op:
            assert 0<kw['_timeout_seconds']<=80
            mono[0]+=81;r.t[0]+=1
            return {'authenticated':True,'balance_compute_units':999} if op=='account_status' else {}
    r.b.hook=late
    with pytest.raises(BridgeError):r.s._call(r.s.sessions.get(sid),op)
    state=r.s.sessions.get(sid)
    assert state['phase']=='closing' and state['last_tick']==st['last_tick']
    assert not state.get('archive_uploaded') and not state.get('upload_completed_at')
    assert state['terminal_fault']['code']=='execution_uncertain'

@pytest.mark.parametrize('action',['archive_uploaded','upload_completed_at','finish_cycle'])
def test_monotonic_deadline_checked_at_success_publication(rig,action):
    r=rig;st=session(r,'uploading');sid=st['id'];mono=[100.0]
    r.s.sessions._monotonic=lambda:mono[0]
    r.b.hook=fail_kind('heartbeat');cycle(r);mono[0]+=91
    if action=='finish_cycle':
        r.s.sessions.save(sid,cycle_session_ok=True,cycle_faulted=False,cycle_job=None)
        with pytest.raises(BridgeError):r.s.sessions.finish_cycle(sid)
    else:
        with pytest.raises(BridgeError):r.s.sessions.save(sid,**{action:True})
    state=r.s.sessions.get(sid)
    assert state['phase']=='closing' and state['last_tick']==st['last_tick'] and state['fault_streak']==1
    assert not state.get('archive_uploaded') and not state.get('upload_completed_at')


def test_restart_uses_only_remaining_durable_window(rig):
    r=rig;st=session(r);r.b.hook=fail_kind('heartbeat');cycle(r)
    r.t[0]+=60;r.s=ColabService(r.s.root,r.b,lambda:r.t[0]);r.b.hook=None
    assert 0<r.s.sessions.recovery_remaining(st)<=30
    r.t[0]+=30;cycle(r)
    assert r.s.sessions.get(st['id'])['released']

@pytest.mark.parametrize('op',['upload','download','runtime_allocate','remote_exec'])
def test_orphan_budget_exception_never_authorizes_work(rig,op):
    r=rig;st,job=running(r)
    with r.s.db() as db:db.execute('DELETE FROM work_sessions WHERE id=?',(st['id'],))
    job=r.s.update(job['id'],phase='cleanup_pending',orphan_session=True)
    r.b.calls.clear()
    with pytest.raises(BridgeError):r.s._call(job,op,runtime_id=job['runtime_id'])
    assert not r.b.calls


def test_orphan_stop_requires_durable_runtime_identity(rig):
    r=rig;st,job=running(r)
    with r.s.db() as db:db.execute('DELETE FROM work_sessions WHERE id=?',(st['id'],))
    job=r.s.update(job['id'],phase='cleanup_pending',orphan_session=True);r.b.calls.clear()
    with pytest.raises(BridgeError):r.s._call(job,'runtime_stop',runtime_id='different-runtime')
    assert not r.b.calls
    r.s.tick(job);r.s.tick(job)
    assert r.s.job(r.owner,job['id'])['released'] and sum(op=='runtime_stop' for op,_ in r.b.calls)==1


def test_clock_fault_is_safe_in_receipt(rig):
    r=rig;st=session(r,'uploading');rid=submit(r)['request_id']
    r.b.hook=fail_kind('heartbeat');cycle(r);r.t[0]-=1;r.b.hook=None;cycle(r)
    receipt=r.a.poll(r.owner,rid)
    assert receipt['state']=='failed' and receipt['diagnostics']['error_code']=='clock_inconsistent'
    assert receipt['diagnostics']['stage']=='session_fault'


def test_orphan_lookup_then_stop_uses_persisted_identity(rig):
    r=rig;st,job=running(r)
    with r.s.db() as db:db.execute('DELETE FROM work_sessions WHERE id=?',(st['id'],))
    job={**job,'runtime_id':None,'allocation_id':job['id'],'gpu':'A100'}
    with r.s.db() as db:db.execute('UPDATE jobs SET state=? WHERE id=?',(json.dumps(job),job['id']))
    r.b.found=True;r.b.calls.clear();r.s.tick(job);r.s.tick(job)
    assert [op for op,_ in r.b.calls]==['runtime_lookup','runtime_stop']
    assert r.s.job(r.owner,job['id'])['released']


def test_orphan_stop_failure_preserves_cleanup_for_next_pass(rig):
    r=rig;st,job=running(r)
    with r.s.db() as db:db.execute('DELETE FROM work_sessions WHERE id=?',(st['id'],))
    r.b.calls.clear();r.b.hook=lambda op,kw:(_ for _ in ()).throw(BridgeError('stop_uncertain',True)) if op=='runtime_stop' else None
    with pytest.raises(BridgeError):r.s.tick(job)
    state=r.s.job(r.owner,job['id']);assert state['phase']=='cleanup_pending' and not state.get('released')
    r.b.hook=None;r.s.tick(state);assert r.s.job(r.owner,job['id'])['released']
    assert not r.s.sessions.rows()


def test_orphan_flag_in_caller_is_not_cleanup_authorization(rig):
    r=rig;st,job=running(r)
    with r.s.db() as db:db.execute('DELETE FROM work_sessions WHERE id=?',(st['id'],))
    fake={**job,'phase':'cleanup_pending','orphan_session':True};r.b.calls.clear()
    with pytest.raises(BridgeError):r.s._call(fake,'runtime_stop',runtime_id=job['runtime_id'])
    assert not r.b.calls


def test_clock_step_at_upload_intent_cannot_send_work_after_closing(rig,monkeypatch):
    r=rig;st=session(r,'uploading');sid=st['id'];r.s.sessions.freeze(st)
    save=r.s.sessions.save
    def rewind(sid,**changes):
        if 'upload_started_at' in changes:r.t[0]-=120
        return save(sid,**changes)
    monkeypatch.setattr(r.s.sessions,'save',rewind)
    cycle(r);state=r.s.sessions.get(sid)
    assert state['phase']=='closing' and state['terminal_fault']['code']=='clock_inconsistent'
    assert not any(op=='upload' for op,_ in r.b.calls)
    cycle(r);assert r.s.sessions.get(sid)['released']

@pytest.mark.parametrize('bad',['future','nan','inf'])
def test_inconsistent_timestamp_is_not_ignored_when_streak_is_zero(rig,bad):
    r=rig;st=session(r);sid=st['id']
    st['fault_streak']=0;st['fault_since']=r.t[0]+120 if bad=='future' else float(bad)
    with r.s.db() as db:db.execute('UPDATE work_sessions SET state=? WHERE id=?',(json.dumps(st),sid))
    r.b.calls.clear();cycle(r);current=r.s.sessions.get(sid)
    assert current['released'] and current['terminal_fault']['code']=='clock_inconsistent'
    assert not any(op in {'remote_exec','upload','runtime_allocate'} for op,_ in r.b.calls)
