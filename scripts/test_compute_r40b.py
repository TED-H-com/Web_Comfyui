"""Admission and transport boundaries; real locks, fake provider, no network."""
import copy,json,threading,time,uuid
from pathlib import Path
import pytest
from review_r38_fixture import rig,session,submit
from colab_service import BridgeError
from compute_parity.provisioning import admission_only,cap_exemptions,states
from scripts.colab_rpc_transport import Budget,NotReady,scoped

def verified(r,**changes):
    v={'owner':r.owner,'account_id':r.account,'subject_key':'b'*64,'identity_at':r.t[0],'balance_at':r.t[0],'balance':1000}
    v.update(changes);return session(r,phase='allocating',admission_verification=v)

def test_account_busy_admits_from_verified_owner_session(rig):
    r=rig;verified(r)
    def hook(op,kw):
        if op in {'account_status','account_identity'}:raise BridgeError('account_busy')
    r.b.hook=hook;result=submit(r)
    assert r.s.job(r.owner,result['request_id'])['phase']=='queued'
    assert not any(op=='runtime_allocate' for op,_ in r.b.calls)

@pytest.mark.parametrize('changes',[{'identity_at':999399},{'balance_at':999399},{'owner':'other'},{'account_id':'other'},{'subject_key':'c'*64},{'balance':None}])
def test_busy_bad_snapshot_is_distinct_exempt_rejection(rig,changes):
    r=rig;verified(r,**changes)
    def hook(op,kw):
        if op=='account_status':raise BridgeError('account_busy')
    r.b.hook=hook;result=submit(r)
    assert result['state']=='rejected' and result['error']['code']=='account_busy'
    row=r.j.get(r.owner,result['request_id'])
    assert cap_exemptions([row],r.s.root/'state.sqlite3',r.owner)=={row['request_id']:'admission_only'}
    assert 'private_admission_error' not in result

@pytest.mark.parametrize('field,value',[('settlement_status','unknown'),('artifacts',[{}]),('actual_cost',{'unit':'CU','amount':1}),('failure_kind','execution'),('lane','autoip'),('execution_started_at',1)])
def test_admission_unknown_or_contradiction_counted(rig,field,value):
    r=rig
    def hook(op,kw):
        if op=='account_status':raise BridgeError('account_busy')
    r.b.hook=hook;result=submit(r);row=r.j.get(r.owner,result['request_id']);record=states(r.s.root/'state.sqlite3',r.owner)[row['request_id']]
    assert admission_only(row,record)
    row[field]=value;assert not admission_only(row,record)


def test_private_diagnostic_never_public(rig):
    from compute_parity.adapters import public
    from compute_parity.runtime import Runtime
    from scripts.colab_private_diagnostics import capture
    r=rig
    def hook(op,kw):
        if op=='account_status':raise BridgeError('transport_failed',private_diagnostics=capture(RuntimeError('https://secret.invalid/token user:pass@host /home/user/file '+('X'*64)),'account_status'))
    r.b.hook=hook;result=submit(r);row=r.j.get(r.owner,result['request_id'])
    assert row['private_admission_error']['bridge']['operation']=='account_status'
    serialized=json.dumps(row['private_admission_error']);assert 'secret.invalid' not in serialized and '/home/' not in serialized and 'pass@host' not in serialized
    from compute_routing import _public_receipt
    for projection in [result,public(row),Runtime.receipt(row),_public_receipt(Runtime.receipt(row))]:assert 'private_admission_error' not in json.dumps(projection)

class Event:
    def __init__(self,on=False):self.on=on
    def is_set(self):return self.on
    def wait(self,timeout=None):return self.on
    def set(self):self.on=True
    def clear(self):self.on=False
class Client:
    def __init__(self,ready=True):self.connection_ready=Event(ready);self._message_received=Event();self.sent=0
    def start_channels(self):pass
    def execute(self):self.sent+=1;return 'ack'
    def _on_open(self,*args):self.connection_ready.set()
    def _on_close(self,*args):self.connection_ready.clear()

@pytest.mark.parametrize('ready',[False,True])
def test_transport_never_open_or_close_before_send(ready):
    b=Budget(2);c=Client(ready)
    with scoped(b,Client):
        if not ready:
            with pytest.raises(NotReady):c.start_channels()
        else:
            c.start_channels();c._on_close(None,1006,'PRIVATE reason')
            with pytest.raises(NotReady):c.execute()
    assert c.sent==0 and b.sent is False and 'PRIVATE' not in json.dumps(b.evidence())

def test_transport_close_after_send_stays_ambiguous():
    b=Budget(2);c=Client()
    with scoped(b,Client):
        c.start_channels();c.execute();c._on_close(None,1006,'private')
    assert b.sent and b.evidence()['phase']=='close' and b.evidence()['close_code']==1006

@pytest.mark.parametrize('wall,mono',[(0,91),(91,0),(-1,1),(91,91)])
def test_transport_dual_clock_deadline_freeze_jump_and_late_success(wall,mono):
    w=[100.];m=[100.];b=Budget(90,lambda:w[0],lambda:m[0]);w[0]+=wall;m[0]+=mono
    with pytest.raises(TimeoutError):b.remaining()

def test_ready_wait_and_silent_open_are_deadline_bounded():
    class Silent(Event):
        def wait(self,timeout=None):time.sleep(timeout);return False
    b=Budget(.03);c=Client();c._message_received=Silent()
    with scoped(b,Client):
        c.start_channels();c.execute()
        with pytest.raises(TimeoutError):c._message_received.wait()
    assert b.sent

@pytest.mark.parametrize('modes,expected_calls,ambiguous',[
    (['notready','ok'],2,None),(['notready','notready'],2,False),
    (['sentfail','ok'],1,True),(['closed','ok'],2,None),(['lateclose'],1,True)])
def test_scoped_bridge_reconnects_only_before_send(monkeypatch,modes,expected_calls,ambiguous):
    import contextlib
    from scripts import colab_bridge as cb,colab_rpc_transport as rpc
    real_scoped=rpc.scoped
    monkeypatch.setattr(rpc,'scoped',lambda b:real_scoped(b,Client))
    calls=[]
    clock=[time.time(),time.monotonic()]
    if modes==['lateclose']:
        monkeypatch.setattr(rpc,'Budget',lambda seconds:Budget(seconds,lambda:clock[0],lambda:clock[1]))
    class Runtime:
        def __init__(self,*args,**kwargs):self.mode=modes[len(calls)];calls.append(self.mode)
        def execute_code(self,*args,**kwargs):
            c=Client(self.mode!='notready');c.start_channels()
            if self.mode=='closed':c._on_close(None,1006,'private')
            c.execute()
            if self.mode=='sentfail':raise RuntimeError('Connection was lost.')
            return []
        def stop(self,**kwargs):
            if self.mode=='lateclose':clock[0]+=100;clock[1]+=100
    sdk=cb.GoogleSDK.__new__(cb.GoogleSDK);sdk.Runtime=Runtime;sdk.execution_transport=contextlib.nullcontext
    b=cb.Bridge.__new__(cb.Bridge);b.sdk=sdk;b.runtime=lambda rid:{'url':'https://example.invalid','token':'fixture'}
    args={'runtime_id':str(uuid.uuid4()),'code':'pass','timeout_seconds':3}
    if ambiguous is None:
        result=b.remote_exec(args);assert result['private_rpc']['attempts']==expected_calls
    else:
        with pytest.raises(cb.BridgeError) as caught:b.remote_exec(args)
        assert caught.value.ambiguous is ambiguous
        assert caught.value.code==('execution_uncertain' if ambiguous else 'transport_not_ready')
    assert len(calls)==expected_calls

def test_sdk_cause_captured_before_transport_failed(monkeypatch):
    from scripts import colab_bridge as cb
    def fail(*args,**kwargs):raise ConnectionError('file:secret user:pass@host /home/private '+('x'*64))
    monkeypatch.setattr(cb,'dispatch',fail)
    result=cb.handle({'op':'account_status'})['error']
    assert result['code']=='transport_failed' and result['private_diagnostics']['class']=='ConnectionError'
    assert result['private_diagnostics']['operation']=='account_status'
    assert 'user:pass' not in json.dumps(result) and '/home/' not in json.dumps(result)

@pytest.mark.parametrize('field,value',[('state','pending'),('outcome','failed'),('owner','other')])
def test_terminal_claim_binding_is_required(rig,field,value):
    r=rig
    def hook(op,kw):
        if op=='account_status':raise BridgeError('account_busy')
    r.b.hook=hook;result=submit(r);row=r.j.get(r.owner,result['request_id']);record=states(r.s.root/'state.sqlite3',r.owner)[row['request_id']]
    assert admission_only(row,record)
    record[1]['_claim_evidence'][field]=value
    assert not admission_only(row,record)
