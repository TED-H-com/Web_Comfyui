"""N4 UUID-bound grace, legacy/pre-intent crash and terminal-row recovery."""
import pytest
from test_review2_original_r35 import rig, uid, Crash, ColabService, BridgeError
from scripts.colab_lease_sweeper import sweep


@pytest.mark.parametrize('kind',['legacy','preintent','stale_uuid'])
@pytest.mark.parametrize('cancel',[False,True])
def test_missing_bound_intent_uses_durable_recovery_time(rig,kind,cancel):
    s,b,clock,account,state=rig
    aid=uid();old=clock[0]-70
    fields={'allocation_id':aid,'allocation_at':old,'deadline_epoch':old+21600,'gpu':'A100','phase':'uncertain','round_at':old}
    if kind=='preintent':fields.update(dispatch_intent_id=None,dispatch_intent_at=None)
    if kind=='stale_uuid':fields.update(dispatch_intent_id=uid(),dispatch_intent_at=old-100,absence_grace_id=uid(),absence_grace_at=old-100)
    s.sessions.save(state['id'],**fields)
    if cancel:s.sessions.close('owner')
    first_recovery=clock[0]
    for elapsed in (0,80,149):
        clock[0]=first_recovery+elapsed
        restarted=ColabService(s.root,b,s.clock)
        restarted.sessions.tick_all()
        current=restarted.sessions.get(state['id'])
        assert current['allocation_id']==aid and not current['released']
        assert current['absence_grace_id']==aid and current['absence_grace_at']==first_recovery
        assert current['deadline_epoch']==old+21600
    clock[0]=first_recovery+150
    restarted.sessions.tick_all();current=s.sessions.get(state['id'])
    assert current['phase']==('closed' if cancel else 'requesting')
    assert not any(op=='runtime_allocate' for op,_ in b.calls)
    if cancel:
        before=len(b.calls);restarted.sessions.tick_all();sweep(s.root,b,s.clock)
        assert len(b.calls)==before  # Confirmed absence is durable, not reopened forever.


@pytest.mark.parametrize('via',['controller','sweeper'])
@pytest.mark.parametrize('closed',[False,True])
def test_cancelled_or_prematurely_closed_uuid_stays_reconcilable(rig,via,closed):
    s,b,clock,account,state=rig
    b.uncertain=True;s.sessions.tick_all();dispatched=s.sessions.get(state['id'])
    s.sessions.close('owner')
    if closed:s.sessions.save(state['id'],phase='closed',released=True)
    def recover():
        restarted=ColabService(s.root,b,s.clock)
        if via=='controller':restarted.sessions.tick_all()
        else:sweep(s.root,b,s.clock)
    clock[0]+=149;recover()
    current=s.sessions.get(state['id'])
    assert current['phase']=='closing' and not current['released']
    assert current['allocation_id']==dispatched['allocation_id']
    b.found=True;recover();recover()
    assert s.sessions.get(state['id'])['released']
    assert len([op for op,_ in b.calls if op=='runtime_stop'])==1
    assert not any(op in {'remote_exec','upload'} for op,_ in b.calls)


def test_next_uuid_resets_intent_before_preintent_crash(rig,monkeypatch):
    s,b,clock,account,state=rig
    b.uncertain=True;s.sessions.tick_all();first=s.sessions.get(state['id'])
    clock[0]+=150;s.sessions.tick_all()
    original=s.sessions.allocation_allowed
    def crash(db,st,phase):
        if phase=='allocating':
            assert st['allocation_id']!=first['allocation_id']
            assert st['dispatch_intent_id'] is None and st['dispatch_intent_at'] is None
            assert st['absence_grace_id'] is None and st['absence_grace_at'] is None
            raise Crash()
        return original(db,st,phase)
    monkeypatch.setattr(s.sessions,'allocation_allowed',crash)
    with pytest.raises(Crash):s.sessions.tick_all()
    second=s.sessions.get(state['id']);clock[0]+=70
    restarted=ColabService(s.root,b,s.clock);restarted.sessions.tick_all()
    at=clock[0];clock[0]+=149;restarted.sessions.tick_all()
    current=s.sessions.get(state['id'])
    assert current['allocation_id']==second['allocation_id']
    assert current['absence_grace_at']==at and current['absence_grace_id']==second['allocation_id']
    assert current['gpu']=='H100' and current['deadline_epoch']==second['deadline_epoch']
    assert len([op for op,_ in b.calls if op=='runtime_allocate'])==1


def test_each_dispatched_uuid_has_fresh_bound_intent(rig,monkeypatch):
    s,b,clock,account,state=rig;b.uncertain=True
    s.sessions.tick_all();first=s.sessions.get(state['id']);clock[0]+=150;s.sessions.tick_all()
    original=s._call
    def slow(st,op,**kw):
        if op=='account_status':clock[0]+=70
        return original(st,op,**kw)
    monkeypatch.setattr(s,'_call',slow)
    s.sessions.tick_all();second=s.sessions.get(state['id'])
    assert second['allocation_id']!=first['allocation_id']
    assert second['dispatch_intent_id']==second['allocation_id']
    assert second['dispatch_intent_at']==clock[0]
    assert second['dispatch_intent_at']==second['allocation_at']+70
    clock[0]+=80;s.sessions.tick_all()
    assert s.sessions.get(state['id'])['allocation_id']==second['allocation_id']
    assert len([op for op,_ in b.calls if op=='runtime_allocate'])==2
