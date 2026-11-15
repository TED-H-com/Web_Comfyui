"""P1m3 failure boundaries: mocks and disk-only fixtures; never call a provider."""
import json,subprocess,uuid
from pathlib import Path
import pytest
from review_r37_fixture import rig,reserve,submit,session
from colab_service import BridgeError,ColabError
from scripts.colab_transport_diagnostics import capture,sanitize

@pytest.mark.parametrize('written',[False,True])
@pytest.mark.parametrize('kind',['TimeoutError','ConnectionResetError'])
def test_ambiguous_heartbeat_survives_once_then_proves_identity(rig,written,kind):
 r=rig;receipt=submit(r);st=r.s.sessions.get(r.s.job(r.owner,receipt['request_id'])['session_id'])
 r.s.sessions.tick_all();st=r.s.sessions.get(st['id']);last=st['last_tick'];hits=[];writes=[]
 def fault(op,kw):
  if op=='remote_exec' and "'heartbeat':True" in kw['code'] and not hits:
   hits.append(written)
   if written:writes.append(last)
   raise BridgeError('execution_uncertain',True,{'error_type':kind,'transport_phase':'wait'})
  if op=='remote_exec' and "'heartbeat':True" in kw['code']:writes.append(last)
 r.b.hook=fault;r.t[0]+=10;r.s.sessions.tick_all();st=r.s.sessions.get(st['id'])
 assert st['phase']=='uploading' and st['last_tick']==last and st['fault']['error_type']==kind
 assert writes==([last] if written else [])
 r.t[0]+=11;r.s.sessions.tick_all();st=r.s.sessions.get(st['id'])
 assert st['phase']=='uploading' and st['fault_streak']==0 and st['last_tick']==r.t[0]
 assert r.s.job(r.owner,receipt['request_id'])['phase']=='queued'
 assert writes==[last]*(2 if written else 1)
 codes=[kw['code'] for op,kw in r.b.calls if op=='remote_exec' and "'heartbeat':True" in kw['code']]
 assert len(codes)==2 and all('assert actual==expected' in c and "proc[19]==actual['start_ticks']" in c for c in codes)

@pytest.mark.parametrize('stage',['heartbeat','bootstrap_probe'])
def test_second_ambiguous_probe_fault_closes_without_renewal(rig,monkeypatch,stage):
 r=rig;st=session(r);last=st['last_tick']
 def fail(state):r.s.sessions.operation(state,stage);raise BridgeError('execution_uncertain',True)
 monkeypatch.setattr(r.s.sessions,'tick',fail)
 for n in (1,2):
  r.t[0]+=20;r.s.sessions.tick_all();st=r.s.sessions.get(st['id'])
  assert st['last_tick']==last and st['fault_streak']==n
  assert (st['phase']=='closing')==(n==2)

@pytest.mark.parametrize('stage',['bootstrap','attach','upload','arm','release'])
def test_ambiguous_mutation_is_never_retried(rig,monkeypatch,stage):
 r=rig;st=session(r);calls=[]
 def fail(state):calls.append(stage);r.s.sessions.operation(state,stage);raise BridgeError('execution_uncertain',True)
 monkeypatch.setattr(r.s.sessions,'tick',fail);r.s.sessions.tick_all()
 assert calls==[stage] and r.s.sessions.get(st['id'])['phase']=='closing'

def test_fault_receipt_preserves_system_cause(rig):
 r=rig;receipt=submit(r);job=r.s.job(r.owner,receipt['request_id']);r.s.sessions.tick_all();st=r.s.sessions.get(job['session_id'])
 fault={'stage':'heartbeat','code':'execution_uncertain','error_type':'ReadTimeout','http_status':504,'transport_phase':'read','timeout_kind':'read','ambiguous':True,'private':'never emitted'}
 st=r.s.sessions.save(st['id'],phase='closing',reason='watchdog_state_lost',close_requested=True,fault=fault)
 r.s.sessions.release(st);got=r.a.poll(r.owner,job['id'])
 assert got['state']=='failed' and got['diagnostics']=={'stage':'session_fault','substage':'heartbeat','error_code':'execution_uncertain','error_type':'ReadTimeout','http_status':504,'transport_phase':'read','timeout_kind':'read','ambiguous':True}
 from compute_parity.runtime import Runtime
 from compute_routing import _public_receipt
 assert _public_receipt(Runtime.receipt(got))['diagnostics']==got['diagnostics']
 assert 'private' not in json.dumps(got)

def test_transport_diagnostics_never_include_exception_text():
 e=TimeoutError('secret URL and credential');e.status_code=504
 got=capture(e);assert got=={'error_type':'TimeoutError','transport_phase':'wait','timeout_kind':'execution','http_status':504}
 assert sanitize({**got,'url':'secret','error_type':'private-name','http_status':True})=={'transport_phase':'wait','timeout_kind':'execution'}

def test_archive_durable_timestamps(rig):
 r=rig;st=r.s.sessions.open(r.owner,r.account,15)
 for _ in range(4):r.s.sessions.tick_all();r.t[0]+=10
 st=r.s.sessions.get(st['id'])
 assert st['upload_started_at']<=st['upload_completed_at']<=st['digest_verified_at']
 assert len([1 for op,_ in r.b.calls if op=='upload'])==1

@pytest.mark.parametrize('failure',['account_authorization','invalid_input'])
def test_confirm_foreign_owner_can_claim_legacy_orphan_before_account_validation(rig,failure):
 r=rig;rid=reserve(r);body={'request_id':rid,'account_id':r.account,'gpu':'A100','max_units':20,'max_minutes':60,'job':{'type':'image','prompt':'offline'},'files':[]}
 if failure=='invalid_input':
  body['account_id']=r.s.create_account('another-owner','offline')['id'];body['files']=[{'name':'invalid.exe','data':''}]
 with pytest.raises(ColabError):r.s.submit('another-owner',body)
 with r.s.db() as db:assert db.execute('SELECT owner FROM request_claims WHERE id=?',(rid,)).fetchone() is None
 for fn in [r.a.poll,r.a.cancel,r.a.recover]:assert fn(r.owner,rid)['state']=='closed_no_execution_evidence'
 assert r.j.ledger(r.owner)['colab']['unsettled_reserved']==0
 assert not r.s.find_job(r.owner,rid) and not any(op=='runtime_allocate' for op,_ in r.b.calls)

def test_session_controls():
 subprocess.run(['node',str(Path(__file__).with_name('test_session_controls_r38.js'))],check=True)

def test_real_heartbeat_code_checks_lock_process_identity(tmp_path,capsys):
 import os,fcntl
 from scripts.colab_session_watchdog import heartbeat
 sid=str(uuid.uuid4());root=tmp_path/sid;root.mkdir()
 lock=os.open(root/'watchdog.lock',os.O_CREAT|os.O_RDWR,0o600)
 try:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);st=os.fstat(lock)
  identity={'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],'deadline':12345,'lock_dev':st.st_dev,'lock_ino':st.st_ino}
  (root/'watchdog.json').write_text(json.dumps({k:v for k,v in identity.items() if not k.startswith('lock_')}))
  code=heartbeat(sid,identity,1000).replace('/content/castle-sessions/'+sid,str(root))
  exec(compile(code,'offline-heartbeat','exec'),{})
  assert (root/'heartbeat').read_text()=='1000'
  identity['start_ticks']='wrong'
  with pytest.raises(AssertionError):exec(compile(heartbeat(sid,identity,2000).replace('/content/castle-sessions/'+sid,str(root)),'offline-heartbeat','exec'),{})
  assert (root/'heartbeat').read_text()=='1000'
 finally:os.close(lock)

def test_retry_budget_does_not_outlive_fault_window(rig):
 r=rig;st=session(r);st=r.s.sessions.save(st['id'],fault_since=r.t[0]-80,fault_streak=1)
 r.s._exec(st,"print('WEB_COMFYUI_JSON={}')")
 op,kw=r.b.calls[-1];assert kw['_timeout_seconds']==10 and kw['timeout_seconds']==10
 r.t[0]+=11
 with pytest.raises(BridgeError):r.s._exec(st,"print('WEB_COMFYUI_JSON={}')")


def test_job_progress_probe_ambiguous_is_bounded(rig):
 r=rig;receipt=submit(r);job=r.s.job(r.owner,receipt['request_id']);st=r.s.sessions.get(job['session_id'])
 r.s.sessions.tick_all();st=r.s.sessions.get(st['id']);last=st['last_tick']
 job=r.s.update(job['id'],phase='running',runtime_id=st['runtime_id'],deadline_epoch=r.t[0]+1000,manifest_sha256='0'*64,launch_requested_at=r.t[0])
 def fail(op,kw):
  if op=='remote_exec':raise BridgeError('execution_uncertain',True,{'error_type':'ConnectionResetError','transport_phase':'read'})
 r.b.hook=fail
 for n in (1,2):
  r.s.tick(r.s.job(r.owner,job['id']));st=r.s.sessions.get(st['id'])
  assert st['last_tick']==last and (st['phase']=='closing')==(n==2)
 assert st['fault']['stage']=='job_probe'

@pytest.mark.parametrize('point',['execute','transport'])
def test_real_bridge_wraps_only_safe_exception_metadata(tmp_path,point):
 from types import SimpleNamespace
 from contextlib import contextmanager
 from scripts.colab_bridge import Bridge as RemoteBridge,BridgeError as RemoteError
 class ReadTimeout(Exception):pass
 error=ReadTimeout('private content must never appear');error.response=SimpleNamespace(status_code=503)
 class Runtime:
  def __init__(self,*a,**kw):pass
  def execute_code(self,*a,**kw):raise error
  def stop(self,**kw):pass
 @contextmanager
 def transport():
  if point=='transport':raise error
  yield
 bridge=object.__new__(RemoteBridge);bridge.sdk=SimpleNamespace(Runtime=Runtime,execution_transport=transport)
 bridge.runtime=lambda rid:{'url':'offline','token':'offline'}
 with pytest.raises(RemoteError) as caught:bridge.remote_exec({'runtime_id':str(uuid.uuid4()),'code':'pass'})
 assert caught.value.ambiguous and caught.value.code=='execution_uncertain'
 assert caught.value.diagnostics=={'error_type':'ReadTimeout','transport_phase':'read','timeout_kind':'read','http_status':503}
 assert 'private content' not in json.dumps(caught.value.public())

def test_wrong_watchdog_runtime_cannot_renew(rig):
 r=rig;st=session(r);last=st['last_tick'];r.s.sessions.save(st['id'],runtime_id='replacement')
 r.t[0]+=10;r.s.sessions.tick_all();st=r.s.sessions.get(st['id'])
 assert st['last_tick']==last and st['phase']=='closing'
 assert not any(op=='remote_exec' for op,_ in r.b.calls)

def test_ambiguous_bootstrap_launch_is_dispatched_once(rig):
 r=rig;job=submit(r);sid=r.s.job(r.owner,job['request_id'])['session_id'];attempts=[]
 def hook(op,kw):
  if op=='remote_exec' and "bootstrap-launch.tmp" in kw['code']:
   attempts.append(kw);raise BridgeError('execution_uncertain',True)
 r.b.hook=hook
 for _ in range(8):r.s.sessions.tick_all();r.t[0]+=10
 assert len(attempts)==1 and r.s.sessions.get(sid)['phase']=='closed'

def test_sweeper_preserves_terminal_fault_in_receipt(rig):
 r=rig;receipt=submit(r);job=r.s.job(r.owner,receipt['request_id']);r.s.sessions.tick_all();st=r.s.sessions.get(job['session_id'])
 fault={'stage':'heartbeat','code':'execution_uncertain','error_type':'TimeoutError','ambiguous':True}
 r.s.sessions.save(st['id'],phase='closing',close_requested=True,terminal_fault=fault)
 r.s.sessions.sweep_leases();got=r.a.poll(r.owner,job['id'])
 assert got['state']=='failed' and got['diagnostics']['stage']=='session_fault' and got['diagnostics']['error_type']=='TimeoutError'
