"""Additional offline boundaries: sweeper, shutdown meter, actual voice paths."""
import ast
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import pytest
from test_review_r35 import rig, allocated, job, Crash, CANDIDATE
from test_compute_r35 import load_worker
from scripts.colab_lease_sweeper import sweep
from colab_service import BridgeError


def test_deadline_committed_before_post_and_immediate_watchdog(rig,monkeypatch):
    s,b,clock,account,state=rig
    original=s._call
    def checked(state,op,**kw):
        if op=='runtime_allocate':
            persisted=s.sessions.get(state['id'])
            assert persisted['deadline_epoch']==clock[0]+21600
            assert persisted['allocation_id']==kw['allocation_id']
        return original(state,op,**kw)
    monkeypatch.setattr(s,'_call',checked)
    s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['watchdog_armed'] and final['phase']=='uploading'
    assert [op for op,_ in b.calls if op in {'runtime_allocate','remote_exec'}]==['runtime_allocate','remote_exec']


@pytest.mark.parametrize('age',[1,7*3600])
def test_independent_sweeper_recovers_pre_arm_gap(rig,monkeypatch,age):
    s,b,clock,account,state=rig
    monkeypatch.setattr(s.sessions,'assigned',lambda *a:(_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):s.sessions.tick_all()
    b.found=True;clock[0]+=age;before=len(b.calls)
    assert sweep(s.root,b,s.clock)
    final=s.sessions.get(state['id'])
    assert final['released'] and final['phase']=='closed'
    assert final['deadline_epoch']==state['created_at']+21600
    assert {op for op,_ in b.calls[before:]}<={'runtime_lookup','runtime_stop','account_status'}


def test_sweeper_obeys_same_worker_flock(rig):
    s,b,clock,account,state=rig
    allocated(rig);clock[0]+=301
    before=len(b.calls)
    with (s.root/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert sweep(s.root,b,s.clock) is False
    assert len(b.calls)==before
    assert sweep(s.root,b,s.clock)
    assert s.sessions.get(state['id'])['released']


def test_stop_confirmed_balance_unavailable_remains_reserved(rig,monkeypatch):
    s,b,clock,account,state=rig;state=allocated(rig)
    j=job(rig,state['id'],'cleanup_pending',runtime_id=state['runtime_id'],outcome='failed')
    state=s.sessions.save(state['id'],active_job=j['id'],phase='closing')
    b.balance-=2;b.stop_cost=1
    original=s._call;stopped=[False];missing=[True]
    def bridge(state,op,**kw):
        if op=='account_status' and stopped[0] and missing[0]:raise BridgeError()
        result=original(state,op,**kw)
        if op=='runtime_stop':stopped[0]=True
        return result
    monkeypatch.setattr(s,'_call',bridge)
    s.sessions.release(state)
    line=s.sessions.snapshot('owner')['session_ledger'][0]
    assert line['amount'] is None and not line['settled']
    assert s.sessions.get(state['id'])['phase']=='released_unsettled'
    assert not s.job('owner',j['id'])['released']
    s.sessions.tick_all();assert len([op for op,_ in b.calls if op=='runtime_stop'])==1
    missing[0]=False;s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['phase']=='closed' and final['job_units']==2 and final['session_units']==1
    assert final['units_observed']==3 and s.job('owner',j['id'])['units_observed']==2


def test_live_but_overlong_bootstrap_never_renews_heartbeat(rig,monkeypatch):
    s,b,clock,account,state=rig;state=allocated(rig,'bootstrap')
    clock[0]+=1801;s.sessions.save(state['id'],last_tick=clock[0])
    original=s._exec;codes=[]
    def remote(state,code):
        codes.append(code)
        if 'bootstrap-status.json' in code:return {'phase':'running','alive':True,'progress_at':clock[0],'identity':{'pid':1,'start_ticks':'123'}}
        return original(state,code)
    monkeypatch.setattr(s,'_exec',remote)
    s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert s.sessions.get(state['id'])['reason']=='bootstrap_timeout'
    assert not any("'heartbeat':True" in code for code in codes)


def test_cancel_during_balance_check_blocks_post(rig,monkeypatch):
    s,b,clock,account,state=rig
    original=s._call
    def call(state,op,**kw):
        if op=='account_status':
            s.sessions.close('owner')
            s.sessions.settings('owner',{'idle_minutes':37})
        return original(state,op,**kw)
    monkeypatch.setattr(s,'_call',call)
    s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['close_requested'] and final['settings']['idle_minutes']==37
    assert not any(op=='runtime_allocate' for op,_ in b.calls)


@pytest.mark.parametrize('marker',[{}, {'clean':True}, {'clean':True,'job_id':'wrong','manifest_sha256':'wrong'}])
def test_missing_or_mismatched_cleanup_proof_closes_lease(rig,monkeypatch,marker):
    s,b,clock,account,state=rig;state=allocated(rig)
    j=job(rig,state['id'],'cleanup_pending',runtime_id=state['runtime_id'],outcome='completed',manifest_sha256='a'*64)
    s.sessions.save(state['id'],active_job=j['id'])
    monkeypatch.setattr(s,'_exec',lambda *a:marker)
    s.sessions.finish_job(j)
    assert s.sessions.get(state['id'])['released']
    assert s.sessions.get(state['id'])['reason']=='isolation_unproven'
    assert not s.job('owner',j['id']).get('session_runtime_retained')


def test_success_meter_and_handoff_roll_back_together(rig,monkeypatch):
    s,b,clock,account,state=rig;state=allocated(rig)
    j=job(rig,state['id'],'cleanup_pending',runtime_id=state['runtime_id'],outcome='completed',manifest_sha256='a'*64)
    s.sessions.save(state['id'],active_job=j['id']);b.balance-=2
    monkeypatch.setattr(s,'_exec',lambda *a:{'clean':True,'job_id':j['id'],'manifest_sha256':'a'*64})
    real_db=s.db
    class DB:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            result=self.db.execute(sql,args)
            if sql.startswith('UPDATE work_sessions') and json.loads(args[0]).get('active_job') is None:raise Crash()
            return result
    @contextmanager
    def crashing():
        with real_db() as db:yield DB(db)
    with monkeypatch.context() as m:
        m.setattr(s,'db',crashing)
        with pytest.raises(Crash):s.sessions.finish_job(j)
    assert not s.job('owner',j['id'])['released'] and s.job('owner',j['id'])['units_observed']==0
    assert s.sessions.get(state['id'])['active_job']==j['id']
    s.sessions.finish_job(j)
    assert s.job('owner',j['id'])['released'] and s.job('owner',j['id'])['units_observed']==2
    assert s.sessions.get(state['id'])['active_job'] is None


def test_actual_persona_consumer_uses_job_cache_view(tmp_path,monkeypatch):
    worker=load_worker(monkeypatch)
    root=tmp_path/'job';root.mkdir();session=tmp_path/'session';(session/'cache').mkdir(parents=True)
    cache=worker.cache_view(root,session)
    (root/'comfy-input/reference.wav').write_bytes(b'offline fixture')
    (cache/'voice-export.json').write_text(json.dumps({'persona':'test','reference_name':'reference.wav','reference_text':''}))
    p=CANDIDATE/'scripts/colab_remote/native_voice_node.py'
    tree=ast.parse(p.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CastleBreezyVoiceTTS')
    class Captured(Exception):pass
    def capture(args,**kw):
        request=json.loads(Path(args[-1]).read_text())
        assert Path(request['reference']).resolve()==root/'comfy-input/reference.wav'
        assert Path(request['reference']).is_file()
        raise Captured()
    scope={'Path':Path,'os':os,'json':json,'tempfile':tempfile,'__file__':str(p),
           'subprocess':SimpleNamespace(run=capture,DEVNULL=-3)}
    exec(compile(ast.Module(body=[cls],type_ignores=[]),str(p),'exec'),scope)
    monkeypatch.setenv('CASTLE_PARITY_CACHE',str(cache))
    with pytest.raises(Captured):scope['CastleBreezyVoiceTTS']().synth('test','')


def test_real_cpu_process_group_is_gone_before_cleanup(tmp_path,monkeypatch):
    worker=load_worker(monkeypatch)
    proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],start_new_session=True)
    try:
        worker.stop_group(proc)
        assert proc.poll() is not None
        with pytest.raises(ProcessLookupError):os.killpg(proc.pid,0)
    finally:
        if proc.poll() is None:proc.kill();proc.wait()


def test_private_view_cleanup_failure_is_not_proof(tmp_path,monkeypatch):
    worker=load_worker(monkeypatch);root=tmp_path/'job';root.mkdir()
    private=root/'cache';private.mkdir();(private/'voice-export.json').write_text('{}')
    monkeypatch.setattr(worker.shutil,'rmtree',lambda path:None)
    with pytest.raises(RuntimeError):worker.clean_job(root,None)
    assert private.exists() and not (root/'reusable-clean.json').exists()


def test_cancel_auto_session_job_inside_dispatch_transaction(rig,monkeypatch):
    s,b,clock,account,state=rig
    j=job(rig,state['id']);s.sessions.save(state['id'],admission_created=True)
    original=s._call
    def call(state,op,**kw):
        if op=='account_status':s.cancel('owner',j['id'])
        return original(state,op,**kw)
    monkeypatch.setattr(s,'_call',call)
    s.sessions.tick_all();s.sessions.tick_all()
    assert s.sessions.get(state['id'])['phase']=='closed'
    assert not any(op=='runtime_allocate' for op,_ in b.calls)




def test_web_reconciler_retries_startup_sweeper_lock(rig,monkeypatch):
    import colab_service
    s,*_=rig;attempts=[];sleeps=[]
    def flock(*a):
        attempts.append(True)
        if len(attempts)==1:raise BlockingIOError()
        raise Crash()  # Stop before any reconciliation/provider work.
    monkeypatch.setattr(colab_service.file_lock,'flock',flock)
    monkeypatch.setattr(colab_service.time,'sleep',sleeps.append)
    with pytest.raises(Crash):s._loop()
    assert len(attempts)==2 and sleeps==[1]


def test_recovered_unarmed_runtime_after_heartbeat_gap_is_released(rig):
    s,b,clock,account,state=rig
    b.uncertain=True;s.sessions.tick_all()
    before=s.sessions.get(state['id'])
    clock[0]+=301;b.found=True;s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['phase']=='closed' and final['deadline_epoch']==before['deadline_epoch']
    assert not any(op=='remote_exec' for op,_ in b.calls)
    assert len([op for op,_ in b.calls if op=='runtime_allocate'])==1
