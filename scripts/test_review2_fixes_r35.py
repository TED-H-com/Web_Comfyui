"""Snapshot-only adversarial checks of new code; no provider or live DB access."""
import ast
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import pytest
from test_review2_original_r35 import CANDIDATE, rig, allocated, job, uid, Crash, BridgeError, encoded
from scripts.colab_lease_sweeper import sweep


def worker_module(monkeypatch):
    monkeypatch.syspath_prepend(str(CANDIDATE/'scripts/colab_remote'))
    spec=importlib.util.spec_from_file_location('second_pass_worker',CANDIDATE/'scripts/colab_session_worker.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def test_bootstrap_reporter_identity_is_overwritten(tmp_path,monkeypatch,rig):
    w=worker_module(monkeypatch)
    root=tmp_path/'remote';root.mkdir()
    (root/'bootstrap.json').write_text(json.dumps([{'models':[]}]))
    callbacks=[];errors=[];in_bootstrap=[]
    class Event:
        stopped=False
        def is_set(self):return self.stopped
        def set(self):self.stopped=True
        def wait(self,*a):self.set()
    class Thread:
        def __init__(self,target,**kw):callbacks.append(target)
        def start(self):pass
        def join(self):pass
    monkeypatch.setattr(w,'threading',SimpleNamespace(Event=Event,Thread=Thread))
    monkeypatch.setattr(w.subprocess,'check_output',lambda *a,**kw:'NVIDIA A100, 40536')
    def long_bootstrap(plan,cache,**kw):
        in_bootstrap.append(True)
        # Schedule the actual reporter callback once the first plan has begun,
        # exactly when a second 15-second heartbeat can execute in production.
        callbacks[0]()
        status=json.loads((root/'bootstrap-status.json').read_text())
        identity=json.loads((root/'bootstrap-process.json').read_text())
        assert status['phase']=='running' and status['progress_at']>0
        assert {k:status[k] for k in identity}==identity
        errors.append(False)
    monkeypatch.setattr(w,'bootstrap',long_bootstrap)
    assert w.bootstrap_session(root)==0
    assert in_bootstrap and errors == [False]
    # With the worker still alive but no new progress, controller cancels it.
    s,b,clock,account,state=rig;allocated(rig,'bootstrap')
    launched=clock[0];clock[0]+=121
    original=s._exec
    monkeypatch.setattr(s,'_exec',lambda st,code:
        {'phase':'running','alive':True,'progress_at':launched,'identity':{'pid':1,'start_ticks':'1'}}
        if 'bootstrap-status.json' in code else original(st,code))
    s.sessions.tick_all()
    assert s.sessions.get(state['id'])['fault_streak']==1
    before=s.sessions.get(state['id'])['last_tick']
    s.sessions.tick_all()
    assert s.sessions.get(state['id'])['last_tick']==before
    assert s.sessions.get(state['id'])['private_rpc_error']=={'bootstrap_probe':{'type':'progress_stale'}}
    assert s.sessions.get(state['id'])['reason']=='watchdog_state_lost'
    s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']


def test_close_during_allocation_rpc_cannot_commit(rig,monkeypatch):
    s,b,clock,account,state=rig;original=s._call;attempts=[]
    real_connect=sqlite3.connect
    # Reduce only the wait length, preserving SQLite's actual locking behavior.
    def fast_connect(*args,**kw):kw['timeout']=0.01;return real_connect(*args,**kw)
    monkeypatch.setattr(sqlite3,'connect',fast_connect)
    def provider(state,op,**kw):
        if op=='runtime_allocate':
            # Independent connection used by an HTTP close during the RPC.
            s.sessions.close('owner')
            s.sessions.settings('owner',{'idle_minutes':30})
            assert s.sessions.snapshot('owner')['session']['close_requested']
            attempts.append(True)
        return original(state,op,**kw)
    monkeypatch.setattr(s,'_call',provider)
    s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert attempts and final['phase']=='closed' and final['close_requested']
    assert final['settings']['idle_minutes']==30
    assert any(op=='runtime_stop' for op,_ in b.calls)
    assert not any(op in {'upload','remote_exec'} for op,_ in b.calls)


def test_sweeper_global_timeout_starves_later_unarmed_lease(rig):
    s,b,clock,account,state=rig
    states=[]
    for n in range(4):
        if n==0:current=state
        else:
            current=s.sessions.new_state('owner'+str(n),uid(),str(n)*64,state['settings'])
            with s.db() as db:db.execute('INSERT INTO work_sessions VALUES (?,?,?)',(current['id'],current['owner'],encoded(current)))
        current=s.sessions.save(current['id'],phase='uncertain',allocation_id=uid(),allocation_at=clock[0],
                               gpu='A100',deadline_epoch=clock[0]+21600,last_balance=1000)
        states.append(current)
    timeout=int(next(line.split('=')[1] for line in (CANDIDATE/'scripts/systemd/web-comfyui-colab-lease-sweeper.service').read_text().splitlines() if line.startswith('TimeoutStartSec=')))
    assert timeout==240
    elapsed=[0];calls=[]
    class SystemdKill(BaseException):pass
    def bridge(op,account_dir,**kw):
        calls.append((op,kw))
        if op=='runtime_lookup':
            if kw['allocation_id'] in {x['allocation_id'] for x in states[:3]}:
                elapsed[0]+=75;raise BridgeError('timeout')
            elapsed[0]+=20
            if elapsed[0]>=timeout:raise SystemdKill()
            return {'found':True,'runtime_id':kw['allocation_id'],'accelerator':'A100'}
        if op=='runtime_stop':return {'stopped':True}
        if op=='account_status':return {'authenticated':True,'balance_compute_units':1000}
        raise AssertionError(op)
    for _ in range(4):
        elapsed[0]=0;clock[0]+=300
        try:sweep(s.root,bridge,s.clock)
        except (RuntimeError,SystemdKill):pass
        if s.sessions.get(states[3]['id'])['released']:break
    victim=s.sessions.get(states[3]['id'])
    assert victim['released'] and victim['phase']=='closed'
    assert any(op=='runtime_stop' for op,_ in calls)


def test_sweeper_same_flock_and_fresh_active_lease(rig):
    s,b,clock,account,state=rig;st=allocated(rig)
    j=job(rig,st['id'],'running',runtime_id=st['runtime_id'])
    s.sessions.save(st['id'],active_job=j['id'])
    before=len(b.calls)
    assert sweep(s.root,b,s.clock)
    assert len(b.calls)==before and not s.sessions.get(st['id'])['released']
    clock[0]+=301
    with (s.root/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert sweep(s.root,b,s.clock) is False
        assert len(b.calls)==before
    assert sweep(s.root,b,s.clock)
    assert s.sessions.get(st['id'])['released']
    assert {op for op,_ in b.calls[before:]}<={'runtime_stop','account_status','runtime_lookup'}


def test_sweeper_recovers_prearm_gap_without_post(rig,monkeypatch):
    s,b,clock,account,state=rig
    def crash(*a):raise Crash()
    monkeypatch.setattr(s.sessions,'assigned',crash)
    with pytest.raises(Crash):s.sessions.tick_all()
    b.found=True;before=len(b.calls)
    assert sweep(s.root,b,s.clock)
    assert s.sessions.get(state['id'])['released']
    assert {op for op,_ in b.calls[before:]}<={'runtime_stop','account_status','runtime_lookup'}


def test_sweeper_fails_closed_on_database_lock_then_retries(rig,monkeypatch):
    s,b,clock,account,state=rig;allocated(rig);clock[0]+=301
    real_connect=sqlite3.connect
    def fast_connect(*args,**kw):kw['timeout']=0.01;return real_connect(*args,**kw)
    monkeypatch.setattr(sqlite3,'connect',fast_connect)
    before=len(b.calls)
    with real_connect(s.root/'state.sqlite3') as db:
        db.execute('BEGIN EXCLUSIVE')
        with pytest.raises(sqlite3.OperationalError):sweep(s.root,b,s.clock)
    assert len(b.calls)==before
    assert sweep(s.root,b,s.clock)
    assert s.sessions.get(state['id'])['released']


def test_sweeper_incomplete_schema_does_not_call_provider(tmp_path):
    root=tmp_path/'colab';root.mkdir()
    with sqlite3.connect(root/'state.sqlite3') as db:db.execute('CREATE TABLE work_sessions (id TEXT)')
    calls=[]
    with pytest.raises(sqlite3.OperationalError):sweep(root,lambda *a,**kw:calls.append(a))
    assert calls==[]
    # The failure also releases the worker flock.
    with (root/'worker.lock').open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_release_unknown_final_balance_preserves_reservation(rig,monkeypatch):
    s,b,clock,account,state=rig;st=allocated(rig)
    j=job(rig,st['id'],'cleanup_pending',runtime_id=st['runtime_id'],outcome='failed')
    st=s.sessions.save(st['id'],active_job=j['id'],phase='closing')
    b.balance-=2;b.stop_cost=1;original=s._call;after=[False];missing=[True]
    def call(st,op,**kw):
        if op=='account_status' and after[0] and missing[0]:raise BridgeError('timeout')
        result=original(st,op,**kw)
        if op=='runtime_stop':after[0]=True
        return result
    monkeypatch.setattr(s,'_call',call)
    s.sessions.release(st)
    assert s.sessions.get(st['id'])['phase']=='released_unsettled'
    assert not s.job('owner',j['id'])['released']
    assert not s.sessions.snapshot('owner')['session_ledger'][0]['settled']
    missing[0]=False;s.sessions.tick_all()
    assert s.sessions.get(st['id'])['session_units']==1
    assert s.job('owner',j['id'])['units_observed']==2
    assert len([1 for op,_ in b.calls if op=='runtime_stop'])==1


def test_persona_actual_consumer_resolves_private_view(tmp_path,monkeypatch):
    import tempfile
    w=worker_module(monkeypatch);root=tmp_path/'job';root.mkdir()
    session=tmp_path/'session';(session/'cache').mkdir(parents=True)
    view=w.cache_view(root,session)
    (root/'comfy-input/ref.wav').write_bytes(b'offline')
    (view/'voice-export.json').write_text(json.dumps({'persona':'offline','reference_name':'ref.wav','reference_text':''}))
    p=CANDIDATE/'scripts/colab_remote/native_voice_node.py'
    cls=next(n for n in ast.parse(p.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='CastleBreezyVoiceTTS')
    class Captured(BaseException):pass
    def run(args,**kw):
        request=json.loads(Path(args[-1]).read_text())
        assert Path(request['reference']).resolve()==root/'comfy-input/ref.wav'
        assert Path(request['reference']).read_bytes()==b'offline'
        raise Captured()
    scope={'Path':Path,'os':os,'json':json,'tempfile':tempfile,'__file__':str(p),'subprocess':SimpleNamespace(run=run,DEVNULL=-3)}
    exec(compile(ast.Module(body=[cls],type_ignores=[]),str(p),'exec'),scope)
    monkeypatch.setenv('CASTLE_PARITY_CACHE',str(view))
    with pytest.raises(Captured):scope['CastleBreezyVoiceTTS']().synth('offline','')


