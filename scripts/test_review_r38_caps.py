import json,sys,time,subprocess,uuid
from pathlib import Path
import pytest
from review_r38_fixture import rig, session
from colab_service import BridgeError
from scripts.colab_session_watchdog import WORKER
from test_review_r38_faults import probe_kind

@pytest.mark.parametrize('cap',['hard','cu','idle','stale'])
def test_caps_still_release_after_one_tolerated_fault(rig,cap):
    r=rig;st=session(r);sid=st['id']
    def fail(op,kw):
        if probe_kind(op,kw)=='heartbeat':raise BridgeError('execution_uncertain',True)
    r.b.hook=fail;r.s.sessions.tick_all();r.b.hook=None
    assert r.s.sessions.get(sid)['fault_streak']==1
    if cap=='hard':r.s.sessions.save(sid,deadline_epoch=r.t[0])
    elif cap=='cu':r.s.sessions.save(sid,units_observed=120)
    elif cap=='idle':r.s.sessions.save(sid,idle_since=r.t[0]-1200)
    else:r.s.sessions.save(sid,last_tick=r.t[0]-301)
    r.s.sessions.tick_all()
    assert r.s.sessions.get(sid)['released']
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1

@pytest.mark.parametrize('expired',['hard','stale','unarmed','closing'])
def test_sweeper_cannot_exec_upload_or_allocate(rig,expired):
    r=rig;st=session(r);sid=st['id']
    if expired=='hard':r.s.sessions.save(sid,deadline_epoch=r.t[0])
    elif expired=='stale':r.s.sessions.save(sid,last_tick=r.t[0]-301)
    elif expired=='unarmed':r.s.sessions.save(sid,watchdog_armed=False)
    else:r.s.sessions.save(sid,phase='closing')
    r.b.calls.clear();r.s.sessions.sweep_leases()
    assert r.s.sessions.get(sid)['released']
    assert all(op in {'account_status','runtime_stop','runtime_lookup'} for op,_ in r.b.calls)
    assert sum(op=='runtime_stop' for op,_ in r.b.calls)==1

@pytest.mark.parametrize('limit',['heartbeat','hard','close'])
def test_actual_remote_watchdog_requests_release(tmp_path,monkeypatch,limit):
    p=tmp_path/'remote';p.mkdir();clock=[1000.0];release=[]
    (p/'heartbeat').write_text('1000')
    if limit=='close':(p/'close').touch()
    deadline=1020 if limit=='hard' else 10000
    class StopProbe(Exception):pass
    def run(args,**kwargs):
        assert args[2]=='from google.colab import runtime; runtime.unassign()'
        release.append(clock[0]);raise StopProbe()
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setattr(sys,'argv',['offline-watchdog',str(p),str(deadline)])
    monkeypatch.setattr(time,'time',lambda:clock[0])
    monkeypatch.setattr(time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    namespace={}
    try:
        with pytest.raises(StopProbe):exec(compile(WORKER,'offline-watchdog','exec'),namespace)
        assert release==[{'heartbeat':1300,'hard':1020,'close':1000}[limit]]
    finally:
        import os
        if 'fd' in namespace:os.close(namespace['fd'])
