"""Independent STAGED4 crash, fairness and deployment-helper boundary probes."""
import json
import runpy
import signal
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_review2_original_r35 import CANDIDATE, ColabService, rig, uid, Crash, encoded, BridgeError
from scripts import colab_lease_sweeper as sw


def activate(s, bridge):
    try:sw.sweep(s.root,bridge,s.clock)
    except RuntimeError as e:assert str(e)=='Lease cleanup pending; retry on next timer'


@pytest.mark.parametrize('n',[1,5,17])
@pytest.mark.parametrize('priority',['none','unarmed','overdue'])
def test_fair_attempts_with_real_rpc_timeouts(rig,monkeypatch,n,priority):
    s,b,clock,account,state=rig
    monkeypatch.setattr(sw,'RPC_BUDGET_SECONDS',0.005)
    # Allow synchronous disk commits for the new legacy grace record; the
    # actual provider timeout probe still uses a real 5ms SIGALRM.
    monkeypatch.setattr(sw,'LEASE_BUDGET_SECONDS',2)
    ids=[]
    for i in range(n):
        st=state if i==0 else s.sessions.new_state('other'+str(i),account,'a'*64,state['settings'])
        if i:
            with s.db() as db:db.execute('INSERT INTO work_sessions VALUES (?,?,?)',(st['id'],st['owner'],encoded(st)))
        aid=uid();ids.append(aid)
        s.sessions.save(st['id'],phase='uncertain',allocation_id=aid,allocation_at=clock[0],gpu='A100',
                        deadline_epoch=clock[0]+21600,last_balance=1000)
    if priority!='none':
        st=s.sessions.new_state('priority',account,'a'*64,state['settings'])
        with s.db() as db:db.execute('INSERT INTO work_sessions VALUES (?,?,?)',(st['id'],st['owner'],encoded(st)))
        s.sessions.save(st['id'],phase='ready',runtime_id='priority-runtime',gpu='A100',last_balance=1000,
                        watchdog_armed=priority=='overdue',deadline_epoch=clock[0]-1 if priority=='overdue' else clock[0]+21600)
    calls=[];activation=[0];first={}
    def bridge(op,account_dir,**kw):
        calls.append((activation[0],op,kw))
        if op=='runtime_lookup':
            aid=kw['allocation_id'];first.setdefault(aid,activation[0])
            if aid!=ids[-1]:signal.pause()  # real SIGALRM, no real provider
            return {'found':True,'runtime_id':aid,'accelerator':'A100'}
        return b(op,account_dir,**kw)
    for a in range(1,n+1):
        activation[0]=a;activate(s,bridge)
    assert set(first)==set(ids) and max(first.values())<=n
    assert any(op=='runtime_stop' and kw['runtime_id']==ids[-1] for _,op,kw in calls)
    if priority!='none':
        stop=next(i for i,(_,op,kw) in enumerate(calls) if op=='runtime_stop' and kw['runtime_id']=='priority-runtime')
        lookup=next(i for i,(_,op,_) in enumerate(calls) if op=='runtime_lookup')
        assert stop<lookup
    assert {op for _,op,_ in calls}<={'runtime_lookup','runtime_stop','account_status'}
    assert signal.getitimer(signal.ITIMER_REAL)[0]==0


def test_cursor_commit_survives_process_death(rig,monkeypatch):
    s,b,clock,account,state=rig
    others=[]
    for i in range(3):
        st=state if i==0 else s.sessions.new_state('other'+str(i),account,'a'*64,state['settings'])
        if i:
            with s.db() as db:db.execute('INSERT INTO work_sessions VALUES (?,?,?)',(st['id'],st['owner'],encoded(st)))
        others.append(s.sessions.save(st['id'],phase='uncertain',allocation_id=uid(),allocation_at=clock[0],gpu='A100',deadline_epoch=clock[0]+21600))
    attempts=[]
    def crash(op,account_dir,**kw):
        assert op=='runtime_lookup';attempts.append(kw['allocation_id']);raise Crash()
    for _ in range(3):
        with pytest.raises(Crash):sw.sweep(s.root,crash,s.clock)
    assert len(set(attempts))==3


@pytest.mark.parametrize('accepted',[False,True])
@pytest.mark.parametrize('cancel',[False,True])
def test_intent_crash_recovery_keeps_uuid_and_grace(rig,monkeypatch,accepted,cancel):
    s,b,clock,account,state=rig;real=s._call
    def crash(st,op,**kw):
        if op=='runtime_allocate':
            persisted=s.sessions.get(state['id'])
            assert persisted['allocation_id']==kw['allocation_id']
            assert persisted['dispatch_intent_at']==clock[0]
            if accepted:real(st,op,**kw)
            raise Crash()
        return real(st,op,**kw)
    monkeypatch.setattr(s,'_call',crash)
    with pytest.raises(Crash):s.sessions.tick_all()
    before=s.sessions.get(state['id']);aid=before['allocation_id']
    if cancel:s.sessions.close('owner')
    restarted=ColabService(s.root,b,s.clock)
    for elapsed in [0,149]:
        clock[0]=before['allocation_at']+elapsed
        restarted.sessions.tick_all()
        current=restarted.sessions.get(state['id'])
        assert current['allocation_id']==aid and not current['released']
        assert current['allocation_history']==before['allocation_history']
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==int(accepted)
    assert {kw['allocation_id'] for op,kw in b.calls if op=='runtime_lookup'}=={aid}
    clock[0]=before['allocation_at']+150
    b.found=accepted
    restarted.sessions.tick_all()
    current=restarted.sessions.get(state['id'])
    if accepted:
        assert current['runtime_id']==aid
        assert current['phase']==('closed' if cancel else 'uploading')
    elif cancel:assert current['phase']=='closed' and current['released']
    else:assert current['phase']=='requesting' and current['gpu_index']==1
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==int(accepted)
    assert current['allocation_history']==before['allocation_history']
    if cancel:assert not any(op=='remote_exec' for op,_ in b.calls)


def test_lease_budget_interrupts_and_restores_alarm():
    handler=signal.getsignal(signal.SIGALRM)
    with pytest.raises(sw.LeaseBudgetExpired):
        with sw.budget(0.01,sw.LeaseBudgetExpired()):signal.pause()
    assert signal.getsignal(signal.SIGALRM)==handler
    assert signal.getitimer(signal.ITIMER_REAL)[0]==0


@pytest.mark.parametrize('cancel',[False,True])
def test_slow_preflight_consumes_absence_grace(rig,monkeypatch,cancel):
    """Safe regression: preflight cannot consume this UUID's dispatch grace."""
    s,b,clock,account,state=rig;original=s._call;posts=[]
    def provider(st,op,**kw):
        if op=='account_status' and st['phase']=='requesting':clock[0]+=70
        if op=='runtime_allocate':
            posts.append((clock[0],kw['allocation_id']))
            raise BridgeError('allocation_uncertain',True)
        return original(st,op,**kw)
    monkeypatch.setattr(s,'_call',provider)
    s.sessions.tick_all()
    dispatched=s.sessions.get(state['id'])
    assert dispatched['dispatch_intent_at']==posts[0][0]
    assert dispatched['dispatch_intent_at']-dispatched['allocation_at']==70
    if cancel:s.sessions.close('owner')
    # Restart and receive a negative lookup only 80s after the real POST.
    restarted=ColabService(s.root,b,s.clock)
    clock[0]=dispatched['allocation_at']+150
    assert clock[0]-posts[0][0]==80
    restarted.sessions.tick_all()
    pending=restarted.sessions.get(state['id'])
    assert not pending['released'] and pending['phase']!='closed'
    assert pending['allocation_id']==posts[0][1] and pending['gpu_index']==0
    assert pending['dispatch_intent_id']==posts[0][1]
    assert pending['deadline_epoch']==dispatched['deadline_epoch']
    if cancel:
        # Assignment becomes visible 100s after POST, inside the grace.
        # Neither a negative lookup nor a restart can make it unreachable.
        sw.sweep(s.root,b,s.clock)
        assert not s.sessions.get(state['id'])['released']
        clock[0]=posts[0][0]+100;b.found=True
        restarted.sessions.tick_all();sw.sweep(s.root,b,s.clock)
        final=restarted.sessions.get(state['id'])
        assert final['phase']=='closed' and final['released']
        assert len([op for op,_ in b.calls if op=='runtime_stop'])==1
        assert not any(op in {'remote_exec','upload'} for op,_ in b.calls)
    else:
        clock[0]=posts[0][0]+149
        restarted.sessions.tick_all()
        pending=restarted.sessions.get(state['id'])
        assert pending['allocation_id']==posts[0][1] and pending['gpu_index']==0
        assert not any(op=='runtime_allocate' for op,_ in b.calls)
        clock[0]=posts[0][0]+150
        restarted.sessions.tick_all()
        final=restarted.sessions.get(state['id'])
        assert final['phase']=='requesting' and final['gpu_index']==1
        assert final['allocation_id'] is None
    assert final['allocation_history'][0]['allocation_id']==posts[0][1]
    assert final['deadline_epoch']==dispatched['deadline_epoch']


