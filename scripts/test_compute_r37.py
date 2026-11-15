"""P1m2 admission, absent-job closure, frozen archive and bounded tick faults."""
import base64,copy,hashlib,io,json,tarfile,uuid
from pathlib import Path
import pytest
from scripts.test_compute_r35 import rig,OWNER,oid,native
from colab_service import ColabError,BridgeError,encoded
from compute_parity.colab import ColabNative
from compute_parity.journal import Journal
from compute_parity.runtime import Runtime
from colab_inputs import input_name,preflight
from scripts.colab_session_archive import pack,extract

def adapter(rig):
 s,b,c,a,bundle=rig
 return ColabNative(s,Journal(s.root.parent/'compute-parity/journal.sqlite3',colab_state_path=s.root/'state.sqlite3'))
def inputs(bundle):return {i['slot']:Path(i['local_path']).read_bytes() for i in bundle['inputs']}
def reserve(a,rig,rid=None):
 s,b,c,account,bundle=rig;rid=rid or oid()
 a.journal.reserve(OWNER,rid,'colab',account,bundle['case_id'],'identity',{'unit':'CU','amount':15})
 a.journal.update(OWNER,rid,state='submission_uncertain')
 return rid

@pytest.mark.parametrize('code',['unsupported_input_format','input_too_large','invalid_manifest','invalid_media_image','account_unavailable','insufficient_balance','lane_busy','unexpected'])
def test_live_predurable_failure_is_rejected_and_reserved_cu_released(rig,monkeypatch,code):
 from colab_inputs import ADMISSION_MESSAGES
 s,b,c,account,bundle=rig;a=adapter(rig);rid=oid()
 def reject(*args,**kw):
  if code=='unexpected':raise RuntimeError('private diagnostic must not escape')
  raise ColabError(ADMISSION_MESSAGES[code])
 monkeypatch.setattr(s,'_submit',reject)
 result=a.submit(OWNER,rid,bundle,inputs(bundle),account_id=account,quote={'unit':'CU','amount':15})
 receipt=Runtime.receipt(result)
 assert receipt['state']=='rejected' and receipt['actual_cost']=={'unit':'CU','amount':0} and receipt['cancel_supported'] is False
 assert receipt['diagnostics']['stage']=='admission' and receipt['error']['code']==('admission_unavailable' if code=='unexpected' else code)
 assert a.journal.ledger(OWNER)['colab']['unsettled_reserved']==0
 assert s.find_job(OWNER,rid) is None and not any(op=='runtime_allocate' for op,kw in b.calls)
 assert 'private diagnostic' not in json.dumps(receipt)
 if code=='unexpected':assert receipt['diagnostics']['error_type']=='RuntimeError'

def test_predurable_bridge_exception_has_safe_http_diagnostic(rig,monkeypatch):
 s,b,c,account,bundle=rig;a=adapter(rig)
 def reject(*args,**kw):raise BridgeError('timeout',diagnostics={'http_status':503,'message':'private URL'})
 monkeypatch.setattr(s,'_submit',reject)
 result=a.submit(OWNER,oid(),bundle,inputs(bundle),account_id=account,quote={'unit':'CU','amount':15})
 assert result['state']=='rejected' and result['error']['code']=='admission_unavailable'
 assert result['diagnostics']['http_status']==503 and 'private URL' not in json.dumps(result)

@pytest.mark.parametrize('operation',['poll','recover','cancel'])
def test_p1m2_uncertain_without_job_closes_by_owner_product_read(rig,operation):
 s,b,c,account,bundle=rig;a=adapter(rig);rid=reserve(a,rig,'00000000-0000-4000-8000-00000000d002')
 assert Runtime.receipt(a.journal.get(OWNER,rid))['downstream_id']==rid
 assert not (s.root/'jobs'/rid).exists()
 receipt=Runtime.receipt(getattr(a,operation)(OWNER,rid))
 assert receipt['state']=='closed_no_execution_evidence' and receipt['actual_cost']['amount']==0 and not receipt['cancel_supported']
 assert a.poll(OWNER,rid)==getattr(a,operation)(OWNER,rid)
 a.journal.check_admission(OWNER,'colab',bundle['case_id'])
 assert not any(op in {'runtime_allocate','runtime_stop','upload','remote_exec'} for op,kw in b.calls)

@pytest.mark.parametrize('lock',['request','account'])
def test_recovery_does_not_close_in_flight_submit(rig,lock):
 s,b,c,account,bundle=rig;a=adapter(rig);rid=reserve(a,rig)
 with (s.native_submission_guard(OWNER,rid) if lock=='request' else s.account_guard(account)):
  result=a.poll(OWNER,rid)
 assert result['state']=='submission_uncertain' and result['diagnostics']['error_code']=='admission_in_flight'
 assert a.poll(OWNER,rid)['state']=='closed_no_execution_evidence'

def test_lookup_failure_remains_uncertain(rig,monkeypatch):
 s,b,c,account,bundle=rig;a=adapter(rig);rid=reserve(a,rig)
 def broken(*a):raise OSError('private database path')
 monkeypatch.setattr(s,'find_job',broken)
 result=a.poll(OWNER,rid)
 assert result['state']=='submission_uncertain' and result['diagnostics']['error_code']=='admission_lookup_failed'

def test_request_input_rejections_do_not_stop_other_cases(rig):
 a=adapter(rig)
 for _ in range(2):
  rid=reserve(a,rig);a.journal.update(OWNER,rid,state='rejected',failure_kind='admission',root_cause='invalid_input',actual_cost={'unit':'CU','amount':0})
 a.journal.check_admission(OWNER,'colab','video-replace.realistic.example-b')


def test_signature_mismatch_and_invalid_preview_are_rejected(rig):
 s,b,c,account,bundle=rig;raw=inputs(bundle)['reference']
 with pytest.raises(ColabError,match='副檔名'):input_name('wrong.mp4',raw,'image')
 with pytest.raises(ColabError):input_name('unknown',b'garbage','image')
 broken=inputs(bundle);broken['reference']=b'garbage'
 with pytest.raises(ColabError):preflight(bundle,broken,s.root)

def test_archive_is_deterministic_and_verified_before_publication(tmp_path):
 pairs=[('runner/x.py',b'code'),('bootstrap.json',b'{}')];raw=pack(pairs);assert raw==pack(list(reversed(pairs)))
 expected=[{'path':n,'size':len(v),'sha256':hashlib.sha256(v).hexdigest()} for n,v in pairs]
 p=tmp_path/'archive';p.write_bytes(raw);root=tmp_path/'remote';root.mkdir()
 assert extract(p,root,expected,hashlib.sha256(raw).hexdigest())
 assert extract(p,root,expected,hashlib.sha256(raw).hexdigest())
 expected[-1]['sha256']='0'*64;(root/'runner/x.py').write_bytes(b'existing')
 with pytest.raises(ValueError):extract(p,root,expected,hashlib.sha256(raw).hexdigest())
 assert (root/'runner/x.py').read_bytes()==b'existing'

@pytest.mark.parametrize('kind',['symlink','hardlink','absolute','parent','duplicate','extra','wrong_size','wrong_hash','directory'])
def test_archive_rejects_unsafe_members(tmp_path,kind):
 out=io.BytesIO();name={'absolute':'/x','parent':'../x'}.get(kind,'x');entry=tarfile.TarInfo(name);entry.size=1
 if kind in {'symlink','hardlink','directory'}:entry.type={'symlink':tarfile.SYMTYPE,'hardlink':tarfile.LNKTYPE,'directory':tarfile.DIRTYPE}[kind];entry.linkname='/outside';entry.size=0
 with tarfile.open(fileobj=out,mode='w') as tar:
  tar.addfile(entry,io.BytesIO(b'x'))
  if kind=='duplicate':tar.addfile(entry,io.BytesIO(b'x'))
 raw=out.getvalue();p=tmp_path/'archive';p.write_bytes(raw);root=tmp_path/'remote';root.mkdir()
 expected=[{'path':name if kind in {'absolute','parent'} else 'y' if kind=='extra' else 'x','size':2 if kind=='wrong_size' else 1,'sha256':'0'*64 if kind=='wrong_hash' else hashlib.sha256(b'x').hexdigest()}]
 with pytest.raises(ValueError):extract(p,root,expected,hashlib.sha256(raw).hexdigest())
 assert not list(root.iterdir())

@pytest.mark.parametrize('code',['timeout','transport_failed'])
@pytest.mark.parametrize('stage',['observe','heartbeat','upload'])
def test_transient_tick_fault_retries_bounded_without_renewing_heartbeat(rig,monkeypatch,stage,code):
 s,b,c,account,bundle=rig;st=s.sessions.open(OWNER,account,15);s.sessions.tick_all();st=s.sessions.get(st['id']);last=st['last_tick']
 def tick(state):s.sessions.operation(state,stage);raise BridgeError(code)
 monkeypatch.setattr(s.sessions,'tick',tick)
 for n in range(2):
  c[0]+=30;s.sessions.tick_all();st=s.sessions.get(st['id'])
  assert st['last_tick']==last and st['fault']['count']==n+1 and st['fault']['code']==code
  assert (st['phase']=='closing')==(n==1)
 assert s.sessions.public(st)['fault']==st['fault']

@pytest.mark.parametrize('stage,ambiguous',[('upload',True),('attach',False),('bootstrap',False),('allocation',False),('release',False)])
def test_nonidempotent_or_ambiguous_tick_fault_closes(rig,monkeypatch,stage,ambiguous):
 s,b,c,account,bundle=rig;st=s.sessions.open(OWNER,account,15);s.sessions.tick_all()
 def tick(state):s.sessions.operation(state,stage);raise BridgeError('timeout',ambiguous)
 monkeypatch.setattr(s.sessions,'tick',tick);s.sessions.tick_all()
 assert s.sessions.get(st['id'])['phase']=='closing'

def test_session_frozen_set_uses_one_upload(rig):
 s,b,c,account,bundle=rig;st=s.sessions.open(OWNER,account,15);s.sessions.tick_all();s.sessions.tick_all()
 st=s.sessions.get(st['id']);assert st['archive_uploaded'] and st['upload_index']==len(st['uploads'])
 uploads=[kw for op,kw in b.calls if op=='upload'];assert len(uploads)==1 and uploads[0]['remote_path'].endswith('/bootstrap.tar')

def test_faulted_upload_cannot_extend_remote_heartbeat_beyond_last_success(rig,monkeypatch):
 s,b,c,account,bundle=rig;st=s.sessions.open(OWNER,account,15);s.sessions.tick_all()
 st=s.sessions.get(st['id']);last=st['last_tick'];codes=[];original=s._exec
 def execute(state,code,*a,**kw):
  if "(p/'heartbeat').write_text" in code:codes.append(code)
  return original(state,code,*a,**kw)
 original_call=s._call
 def call(state,operation,*a,**kw):
  if operation=='upload':raise BridgeError('timeout')
  return original_call(state,operation,*a,**kw)
 monkeypatch.setattr(s,'_exec',execute);monkeypatch.setattr(s,'_call',call)
 for _ in range(2):c[0]+=30;s.sessions.tick_all()
 assert len(codes)==2 and all("(p/'heartbeat').write_text(str("+repr(last)+"))" in code for code in codes)
 assert s.sessions.get(st['id'])['last_tick']==last

def test_owner_router_get_closes_original_receipt_and_preview_capacity(rig):
 from compute_routing import ComputeRouter,ComputeAdapter
 s,b,c,account,bundle=rig;a=adapter(rig);rid=reserve(a,rig)
 def forbidden(*a,**kw):raise AssertionError('No submission/provider call during receipt recovery')
 inspect=lambda *a,**kw:{'eligible':True,'estimated_cost':15,'currency':'compute_units','reasons':[]}
 router=ComputeRouter(s.root.parent/'compute-routing.sqlite3',{'colab':ComputeAdapter(inspect,forbidden,lambda o,r:Runtime.receipt(a.poll(o,r)),forbidden)},clock=s.clock)
 with router._db() as db:
  db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',(OWNER,rid,'hash','colab','colab','submission_uncertain','{}','{}',json.dumps({'state':'submission_uncertain','downstream_id':rid,'cancel_supported':True}),c[0],c[0]))
 receipt=router.status(OWNER,rid,refresh=True)
 assert receipt['state']=='closed_no_execution_evidence' and receipt['actual_cost']['amount']==0 and receipt['cancel_supported'] is False
 assert receipt['retry_submission_allowed'] is False
 assert router._get(OWNER,rid)['state']=='closed_no_execution_evidence'
 with router._db() as db:
  assert db.execute("SELECT count(*) FROM compute_requests WHERE lane='colab' AND state NOT IN ('closed_no_execution_evidence','rejected','completed','failed','cancelled')").fetchone()[0]==0
 assert not (s.root/'jobs'/rid).exists()

def test_fault_streak_time_limit_checked_before_renewal(rig):
 s,b,c,account,bundle=rig;st=s.sessions.open(OWNER,account,15);s.sessions.tick_all()
 st=s.sessions.save(st['id'],fault_streak=1,fault_since=c[0]-91)
 b.calls.clear();s.sessions.tick_all();st=s.sessions.get(st['id'])
 assert st['phase']=='closed' and st['reason']=='watchdog_state_lost'
 assert not any(op=='remote_exec' and "'heartbeat':True" in kw.get('code','') for op,kw in b.calls)

def test_unknown_fault_never_leaks_exception_message(rig,monkeypatch):
 s,b,c,account,bundle=rig;st=s.sessions.open(OWNER,account,15);s.sessions.tick_all()
 def broken(state):s.sessions.operation(state,'upload');raise OSError('private://owner/path?secret=value')
 monkeypatch.setattr(s.sessions,'tick',broken);s.sessions.tick_all();state=s.sessions.get(st['id'])
 assert state['fault']['code']=='OSError' and state['phase']=='closing'
 assert 'secret' not in json.dumps(s.sessions.public(state))



@pytest.mark.parametrize('feature,label,expected',[('voice-speak','voice','voice_imports'),('voxcpm-tts','voxcpm','voxcpm_imports'),('voxcpm-tts','voice','speaker_imports')])
def test_real_import_failures_are_fixed_substages_no_ready(tmp_path,monkeypatch,feature,label,expected):
 from scripts import colab_session_voice as voice
 import builtins
 real=builtins.__import__
 def missing(name,*a,**kw):
  if name=='pkg_resources':raise ImportError('private path must not survive')
  return real(name,*a,**kw)
 monkeypatch.setattr(builtins,'__import__',missing)
 with pytest.raises(voice.VoiceEnvironmentMismatch) as e:voice.import_smoke({'dists':{}},tmp_path,label,feature)
 assert e.value.substage==expected and str(e.value)=='voice_env_mismatch'
 assert not list(tmp_path.glob('*ready.json'))

def test_voice_heartbeat_telemetry_redacts_unrecognized_values():
 from scripts.colab_session_diagnostics import telemetry
 value={'voice_environments':{'voice':{'python_build':'main Jun 2 2026','python_version':'3.11.15','torch_version':'2.8.0+cu128','onnx_providers':['CPUExecutionProvider'],'imports_ready':True},'private-path':{'token':'secret'}}}
 assert telemetry(value)=={'voice_environments':{'voice':value['voice_environments']['voice']}}
 value['voice_environments']['voice'].update(python_build='/home/private',onnx_providers=['private-provider'])
 safe=telemetry(value)['voice_environments']['voice'];assert 'python_build' not in safe and 'onnx_providers' not in safe
