"""N1-N3 controls: disk SQLite, synthetic leases, no provider or GPU work."""
import json
import signal
import sqlite3
import time
from contextlib import contextmanager
import pytest
from test_review2_original_r35 import rig, allocated, job, uid, encoded, BridgeError
from scripts import colab_lease_sweeper as sweeper


def leases(rig, count, assigned=False):
    s,b,clock,account,first=rig
    result=[]
    for n in range(count):
        state=first if n==0 else s.sessions.new_state('owner'+str(n),uid(),str(n)*64,first['settings'])
        if n:
            with s.db() as db:db.execute('INSERT INTO work_sessions VALUES (?,?,?)',(state['id'],state['owner'],encoded(state)))
        result.append(s.sessions.save(state['id'],phase='uncertain',allocation_id=uid(),allocation_at=clock[0],gpu='A100',deadline_epoch=clock[0]+21600,last_balance=1000,
                                      **({'runtime_id':uid()} if assigned else {})))
    return result


@pytest.mark.parametrize('count',[1,5,17])
@pytest.mark.parametrize('assigned',[False,True])
def test_n_lease_fairness_across_process_activations(rig,monkeypatch,count,assigned):
    s,b,clock,account,state=rig
    states=leases(rig,count,assigned)
    attempted=[]
    def fail(self,only=None):
        # Independent DB read proves the cursor commits before the attempt.
        with self.s.db() as db:
            assert db.execute('SELECT last_id FROM session_sweep_cursor WHERE kind=?',('assigned' if assigned else 'lookup',)).fetchone()[0]==only
        attempted.append(only)
        raise sweeper.LeaseBudgetExpired()
    monkeypatch.setattr(type(s.sessions),'sweep_leases',fail)
    for _ in range((count+sweeper.MAX_ATTEMPTS-1)//sweeper.MAX_ATTEMPTS):
        with pytest.raises(RuntimeError):sweeper.sweep(s.root,b,s.clock)
    assert set(attempted)=={row['id'] for row in states}
    assert len(set(attempted[:count]))==count


def test_assigned_priority_and_lookup_progress(rig,monkeypatch):
    s,b,clock,account,state=rig
    states=leases(rig,13)
    paid={row['id'] for row in states[:8]}
    for row in states[:8]:s.sessions.save(row['id'],runtime_id=uid())
    activations=[]
    def fail(self,only=None):
        activations[-1].append(only)
        raise BridgeError('timeout')
    monkeypatch.setattr(type(s.sessions),'sweep_leases',fail)
    for _ in range(8):
        activations.append([])
        with pytest.raises(RuntimeError):sweeper.sweep(s.root,b,s.clock)
    for activation in activations:
        assert len(activation)==2
        assert all(sid in paid for sid in activation[:1])
        assert all(sid not in paid for sid in activation[1:])
    assert {sid for a in activations for sid in a}=={row['id'] for row in states}


def test_cursor_survives_abrupt_activation_death(rig,monkeypatch):
    s,b,clock,account,state=rig
    states=leases(rig,5);attempted=[]
    class Kill(BaseException):pass
    def die(self,only=None):attempted.append(only);raise Kill()
    monkeypatch.setattr(type(s.sessions),'sweep_leases',die)
    for _ in states:
        with pytest.raises(Kill):sweeper.sweep(s.root,b,s.clock)
    assert len(set(attempted))==len(states)


def test_budget_interrupts_blocking_attempt_and_restores_alarm():
    handler=signal.getsignal(signal.SIGALRM)
    started=time.monotonic()
    with pytest.raises(sweeper.LeaseBudgetExpired):
        with sweeper.budget(.02,sweeper.LeaseBudgetExpired()):time.sleep(1)
    assert time.monotonic()-started<.5
    assert signal.getsignal(signal.SIGALRM)==handler
    assert signal.getitimer(signal.ITIMER_REAL)==(0,0)
    assert sweeper.LEASE_BUDGET_SECONDS*sweeper.MAX_ATTEMPTS<=180


def test_stuck_balance_cannot_prevent_stop(rig,monkeypatch):
    s,b,clock,account,state=rig
    allocated(rig);clock[0]+=301
    monkeypatch.setattr(sweeper,'RPC_BUDGET_SECONDS',.02)
    def bridge(op,*args,**kw):
        if op=='account_status':time.sleep(1)
        return b(op,*args,**kw)
    assert sweeper.sweep(s.root,bridge,s.clock)
    assert any(op=='runtime_stop' for op,_ in b.calls)
    assert s.sessions.get(state['id'])['phase']=='released_unsettled'


def test_reads_do_not_require_sqlite_writer(rig):
    s,b,clock,account,state=rig
    with sqlite3.connect(s.root/'state.sqlite3',timeout=.01) as db:
        db.execute('BEGIN IMMEDIATE')
        assert s.sessions.settings('owner')['idle_minutes']==20
        assert s.sessions.snapshot('owner')['session']['id']==state['id']


def test_dispatch_committed_before_rpc_and_cancel_job_during_rpc(rig,monkeypatch):
    s,b,clock,account,state=rig
    j=job(rig,state['id']);s.sessions.save(state['id'],admission_created=True)
    original=s._call
    def call(st,op,**kw):
        if op=='runtime_allocate':
            with sqlite3.connect(s.root/'state.sqlite3',timeout=.01) as db:
                db.execute('BEGIN IMMEDIATE')
                fresh=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
                assert fresh['allocation_id']==kw['allocation_id']
                assert fresh['deadline_epoch']==clock[0]+21600
                assert fresh['dispatch_intent_at']==clock[0]
                cancelled=json.loads(db.execute('SELECT state FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]);cancelled['cancel_requested']=True
                db.execute('UPDATE jobs SET state=? WHERE id=?',(encoded(cancelled),j['id']))
        return original(st,op,**kw)
    monkeypatch.setattr(s,'_call',call)
    s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert s.job('owner',j['id'])['phase']=='cancelled'
    assert not any(op in {'upload','remote_exec'} for op,_ in b.calls)


def test_lost_dispatch_response_preserves_close_and_settings(rig,monkeypatch):
    s,b,clock,account,state=rig;original=s._call
    def call(st,op,**kw):
        if op=='runtime_allocate':
            s.sessions.close('owner');s.sessions.settings('owner',{'max_units':77})
            b.found=True
            raise BridgeError('timeout',True)
        return original(st,op,**kw)
    monkeypatch.setattr(s,'_call',call)
    s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['released'] and final['close_requested']
    assert final['settings']['max_units']==77
    assert not any(op in {'upload','remote_exec'} for op,_ in b.calls)
