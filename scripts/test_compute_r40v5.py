import math
import pytest
from review_r38_fixture import rig,session,submit
from colab_service import BridgeError


def snapshot(r,st,age=4300):
 t=r.t[0]-age
 v={'owner':r.owner,'account_id':r.account,'subject_key':'b'*64,'identity_at':t,'balance_at':t,'balance':1000}
 return r.s.sessions.save(st['id'],admission_verification=v)

def status(r,**kw):
 def hook(op,args):
  if op=='account_status':return {'authenticated':True,'subject_key':'b'*64,'balance_compute_units':1000,**kw}
 r.b.hook=hook

def test_observe_refreshes_busy_admission(rig):
 r=rig;st=snapshot(rig,session(rig));status(r);r.s.sessions.observe(st)
 def busy(op,args):
  if op in {'account_status','account_identity'}:raise BridgeError('account_busy')
 r.b.hook=busy;job=submit(r)
 j=r.s.job(r.owner,job['request_id']);assert j['phase']=='queued'
 assert j['admission_verification']['identity_at']==r.t[0] and j['admission_verification']['balance']==1000
 assert not any(op=='runtime_allocate' for op,_ in r.b.calls)

@pytest.mark.parametrize('values',[{'subject_key':'c'*64},{'subject_key':None},{'authenticated':1},{'balance_compute_units':0},{'balance_compute_units':-1},{'balance_compute_units':float('nan')}])
def test_invalid_observation_never_refreshes(rig,values):
 r=rig;st=snapshot(r,session(r));old=st['admission_verification'];status(r,**values)
 try:r.s.sessions.observe(st)
 except ValueError:pass
 assert r.s.sessions.get(st['id'])['admission_verification']==old

@pytest.mark.parametrize('changes',[{'close_requested':True},{'released':True},{'phase':'closing'}])
def test_closed_and_unusable_session_not_refreshed(rig,changes):
 r=rig;st=snapshot(r,session(r));st=r.s.sessions.save(st['id'],**changes);status(r);r.s.sessions.observe(st)
 assert r.s.sessions.get(st['id'])['admission_verification']==st['admission_verification']

def test_observation_time_before_rpc(rig):
 r=rig;st=snapshot(r,session(r));before=r.t[0]
 def hook(op,args):
  if op=='account_status':r.t[0]+=7;return {'authenticated':True,'subject_key':'b'*64,'balance_compute_units':1000}
 r.b.hook=hook;r.s.sessions.observe(st);v=r.s.sessions.get(st['id'])['admission_verification']
 assert v['identity_at']==v['balance_at']==before

def test_rewind_and_anchors(rig):
 r=rig;st=snapshot(r,session(r));status(r)
 for i in range(50):r.s.sessions.observe(st);r.t[0]+=1
 assert len([k for k in r.s.sessions._admission_anchors if k[0]==st['id']])==1
 assert r.s.sessions.admission_snapshot(r.owner,r.account)
 assert r.s.sessions.admission_snapshot('other',r.account) is None
 assert r.s.sessions.admission_snapshot(r.owner,'other') is None
 r.t[0]-=30;old=r.s.sessions.get(st['id'])['admission_verification'];assert r.s.sessions.admission_snapshot(r.owner,r.account) is None
 r.s.sessions.observe(st);assert r.s.sessions.get(st['id'])['admission_verification']==old

def test_meter_exception_rolls_back(rig,monkeypatch):
 r=rig;st=snapshot(r,session(r));status(r)
 def fail(*a,**kw):raise RuntimeError('meter')
 monkeypatch.setattr(r.s.sessions,'_meter',fail)
 with pytest.raises(RuntimeError):r.s.sessions.observe(st)
 assert r.s.sessions.get(st['id'])['admission_verification']==st['admission_verification']

def test_one_busy_heartbeat_is_transient(rig):
 r=rig;st=session(r)
 def hook(op,kw):
  if op=='remote_exec':raise BridgeError('account_busy')
 r.b.hook=hook;r.t[0]+=10;r.s.sessions.tick_all();cur=r.s.sessions.get(st['id'])
 assert cur['phase']=='ready' and cur['fault_streak']==1 and cur['last_tick']==st['last_tick']
 r.b.hook=None;r.t[0]+=10;r.s.sessions.tick_all();assert r.s.sessions.get(st['id'])['fault_streak']==0

def test_two_busy_heartbeats_close(rig):
 r=rig;st=session(r)
 def hook(op,kw):
  if op=='remote_exec':raise BridgeError('account_busy')
 r.b.hook=hook
 for _ in range(2):r.t[0]+=10;r.s.sessions.tick_all()
 assert r.s.sessions.get(st['id'])['phase']=='closing'

def test_stale_snapshot_busy_rejected(rig):
 r=rig;st=snapshot(r,session(r))
 def busy(op,args):
  if op in {'account_status','account_identity'}:raise BridgeError('account_busy')
 r.b.hook=busy;result=submit(r)
 assert result['state']=='rejected' and result['error']['code']=='account_busy'
 from compute_parity.provisioning import cap_exemptions
 row=r.j.get(r.owner,result['request_id']);assert row['actual_cost']['amount']==0 and row['settlement_status']=='settled'
 assert cap_exemptions([row],r.s.root/'state.sqlite3',r.owner)=={row['request_id']:'admission_only'}
 assert r.s.sessions.get(st['id'])['admission_verification']==st['admission_verification']

def test_monotonic_expiry(rig):
 r=rig;st=snapshot(r,session(r));mono=[100.];r.s.sessions._monotonic=lambda:mono[0];status(r)
 r.s.sessions.observe(st);assert r.s.sessions.admission_snapshot(r.owner,r.account)
 mono[0]+=601;assert r.s.sessions.admission_snapshot(r.owner,r.account) is None


@pytest.mark.parametrize('stage',['observe','heartbeat','upload','bootstrap_probe','job_probe'])
def test_busy_allowed_only_for_existing_stages(rig,stage):
 r=rig;st=session(r);sid=st['id'];r.s.sessions.operation(st,stage)
 r.s.sessions.record_fault(st,BridgeError('account_busy'));cur=r.s.sessions.get(sid)
 assert cur['phase']=='ready' and cur['fault_streak']==1 and cur['last_tick']==st['last_tick']
 r.s.sessions.operation(st,stage);r.s.sessions.record_fault(st,BridgeError('account_busy'))
 assert r.s.sessions.get(sid)['phase']=='closing'

def test_busy_outside_stages_closes(rig):
 r=rig;st=session(r);r.s.sessions.operation(st,'attach');r.s.sessions.record_fault(st,BridgeError('account_busy'))
 assert r.s.sessions.get(st['id'])['phase']=='closing'

def test_busy_expired_lease_closes(rig):
 r=rig;st=session(r);r.t[0]+=300;r.s.sessions.operation(st,'heartbeat');r.s.sessions.record_fault(st,BridgeError('account_busy'))
 assert r.s.sessions.get(st['id'])['phase']=='closing'

def test_real_bridge_lock_busy_admission_fallback(rig):
 import threading,time
 from scripts.colab_bridge import account_lock
 r=rig;st=snapshot(r,session(r));status(r);r.s.sessions.observe(st)
 locked=threading.Event();done=threading.Event();path=r.s.account_dir(r.account)
 def holder():
  with account_lock(path):locked.set();time.sleep(9)
 t=threading.Thread(target=holder);t.start();assert locked.wait(3)
 from scripts.colab_bridge import BridgeError as RemoteError
 def bridge(op,account,**kw):
  try:
   with account_lock(account):raise AssertionError('provider must not be reached')
  except RemoteError as e:raise BridgeError(e.code)
 bridge.installed=True;r.s.bridge=bridge;before=time.monotonic()
 try:
  result=submit(r);elapsed=time.monotonic()-before
  assert elapsed<8 and r.s.job(r.owner,result['request_id'])['phase']=='queued'
 finally:done.set();t.join()


@pytest.mark.parametrize('stage',['observe','heartbeat','upload','bootstrap_probe','job_probe'])
def test_real_busy_stage_survives_complete_next_cycle(rig,stage):
 from test_review_r38_faults import running,probe_kind,running_status
 r=rig;job=None
 if stage=='job_probe':st,job=running(r)
 else:
  st=session(r,'uploading' if stage=='upload' else 'bootstrap' if stage=='bootstrap_probe' else 'ready',**({'launch_at':r.t[0]} if stage=='bootstrap_probe' else {}))
  if stage in {'upload','bootstrap_probe'}:st=r.s.sessions.freeze(st)
 if stage=='observe':r.t[0]+=60
 else:r.t[0]+=10
 def hook(op,kw):
  if probe_kind(op,kw)==({'observe':'account_status'}.get(stage,stage)):raise BridgeError('account_busy')
  if job and probe_kind(op,kw)=='job_probe':return running_status(job)
 r.b.hook=hook
 if job:r.s.tick(job)
 else:r.s.sessions.tick_all()
 current=r.s.sessions.get(st['id']);assert current['phase'] not in {'closing','closed'} and current['fault_streak']==1
 assert current['last_tick']==st['last_tick']
 r.b.hook=None;r.t[0]+=10
 if job:
  r.s.sessions.tick_all()
  r.b.hook=lambda op,kw:running_status(job) if probe_kind(op,kw)=='job_probe' else None
  r.s.tick(job)
 else:r.s.sessions.tick_all()
 assert r.s.sessions.get(st['id'])['fault_streak']==0
