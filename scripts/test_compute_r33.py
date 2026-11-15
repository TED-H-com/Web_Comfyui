"""Offline boundary and lifecycle regressions; every temp is disk-backed."""
import contextlib,hashlib,json,sqlite3,uuid
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from test_compute_parity_p1 import bundle,files,oid,FakeTransport
from compute_parity.diagnostics import attempt_diagnostics
from compute_parity.journal import Journal
from compute_parity.runtime import Runtime
from compute_routing import _public_receipt
from colab_service import ColabService,BridgeError
from scripts.colab_upload_diagnostics import classify,sanitize

@pytest.mark.parametrize('body,code',[({},'empty_response_object'),([], 'missing_prompt_id'),(None,'missing_prompt_id'),({'id':'not-a-contract'},'missing_prompt_id'),({'clientId':'not-a-job'},'missing_prompt_id')])
def test_runninghub_diagnostics_and_single_post(bundle,tmp_path,body,code):
 from compute_parity.adapters import RunningHubNative
 t=FakeTransport(bundle);original=t.request;posts=[]
 def request(m,p,**kw):
  if p=='/prompt':posts.append(kw['body']);return body
  return original(m,p,**kw)
 t.request=request;t.last_diagnostics={'http_status':200}
 j=Journal(tmp_path/'journal.db');a=RunningHubNative(t,j);rid=oid()
 result=a.submit('owner',rid,bundle,files(bundle),quote={'unit':'POINT','amount':10})
 d=_public_receipt(Runtime.receipt(result))['diagnostics']
 assert d['stage']=='id_parse' and d['error_code']==code and d['http_status']==200
 assert result['state']=='submission_uncertain' and not result.get('remote_id')
 a.submit('owner',rid,bundle,files(bundle),quote={'unit':'POINT','amount':10})
 assert len(posts)==1 and posts[0]['client_id']==rid

@pytest.mark.parametrize('rid_body',[{'prompt_id':'accepted','node_errors':{'1':{'errors':[{'type':'invalid_prompt'}]}}},{'data':{'taskId':'accepted'}}])
def test_id_wins_and_no_empty_code(rid_body):
 d=attempt_diagnostics('id_parse',rid_body,status=200)
 assert d['prompt_id_present'] and 'error_code' not in d

@pytest.mark.parametrize('status',[100,401,403,413,429,500,599])
@pytest.mark.parametrize('substage',['runtime_lookup','directory_put','file_put'])
def test_upload_http_safe_status(status,substage):
 error=Exception('SECRET URL voice text');error.response=NS(status_code=status)
 d=classify(error,substage)
 assert d=={'stage':'upload','error_code':'input_upload_failed','bridge_error_code':'upload_http_error','http_status':status,'upload_substage':substage}
 assert 'SECRET' not in str(d)

@pytest.mark.parametrize('status',[True,False,99,600,'503',None])
def test_reject_bad_status(status):
 assert sanitize({'http_status':status,'upload_substage':'SECRET','bridge_error_code':[]})=={}

@pytest.fixture
def upload_job(tmp_path):
 root=tmp_path/'controller';root.mkdir();rid=oid();now=[1000.0]
 service=object.__new__(ColabService);service.root=root;service.clock=lambda:now[0]
 service._db_local=__import__('threading').local();service._db_strict=True;service.nested_db_events=0;service.nested_db_sites=set()
 directory=root/'jobs'/rid;directory.mkdir(parents=True)
 uploads=[]
 for i in range(3):
  p=directory/str(i);p.write_bytes(bytes([i])*5)
  uploads.append({'path':str(i),'size':5,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 state={'id':rid,'account_id':oid(),'phase':'preparing','runtime_id':oid(),'native_parity':True,'deadline_epoch':2000,'watchdog_armed':True,'usage_checked_at':1000,'baseline_units':100,'units_observed':0,'uploads':uploads,'upload_index':1,'released':False,'created_at':1000}
 with sqlite3.connect(root/'state.sqlite3') as db:
  db.execute('CREATE TABLE jobs(id TEXT,owner TEXT,account_id TEXT,state TEXT)')
  db.execute('INSERT INTO jobs VALUES (?,?,?,?)',(rid,'owner',state['account_id'],json.dumps(state)))
 return service,state,now

def test_bounded_retry_resume_and_cleanup_pending(upload_job):
 s,state,now=upload_job;calls=[];release=[False]
 def call(state,op,**kw):
  calls.append((op,kw))
  if op=='upload':raise BridgeError('upload_http_error',diagnostics={'http_status':429,'upload_substage':'file_put','token':'SECRET'})
  if op=='runtime_stop':return {'stopped':release[0]}
  raise AssertionError(op)
 s._call=call
 for delta in [0,9,1,29,1]:
  now[0]+=delta;s.tick(state)
 saved=s.job('owner',state['id'])
 assert saved['phase']=='cleanup_pending' and not saved['released'] and saved['outcome']=='failed'
 assert saved['upload_index']==1 and saved['upload_attempts']==3
 assert len([c for c in calls if c[0]=='upload'])==3
 assert all(Path(c[1]['local_path']).name=='1' for c in calls if c[0]=='upload')
 assert saved['remote_diagnostics']['http_status']==429
 # A new controller instance resumes the durable cleanup, never the upload.
 other=object.__new__(ColabService);other.root=s.root;other.clock=s.clock;other._call=call
 other._db_local=__import__('threading').local();other._db_strict=True;other.nested_db_events=0;other.nested_db_sites=set()
 other.tick(state);assert other.job('owner',state['id'])['phase']=='cleanup_pending'
 release[0]=True;other.tick(state);final=other.job('owner',state['id'])
 assert final['phase']=='failed' and final['released'] and final['runtime_id']==state['runtime_id']
 assert final['remote_diagnostics']==saved['remote_diagnostics']
 assert len([c for c in calls if c[0]=='upload'])==3 and not any(c[0]=='remote_exec' for c in calls)
 j=Journal(s.root/'parity.db');j.reserve('owner',state['id'],'colab','account','case',state['id'],{'unit':'CU','amount':10})
 from compute_parity.colab import ColabNative
 a=ColabNative(None,j)
 pending=a._record('owner',state['id'],s.public_job(saved))
 assert pending['state']=='cancelling'
 result=a._record('owner',state['id'],s.public_job(final))
 assert _public_receipt(Runtime.receipt(result))['diagnostics']==saved['remote_diagnostics']


def test_restart_resumes_index_then_resets_retry(upload_job):
 s,state,now=upload_job;calls=[]
 def failed(*a,**kw):raise BridgeError('timeout')
 s._call=failed;s.tick(state)
 now[0]+=10
 other=object.__new__(ColabService);other.root=s.root;other.clock=s.clock
 other._db_local=__import__('threading').local();other._db_strict=True;other.nested_db_events=0;other.nested_db_sites=set()
 other._call=lambda state,op,**kw:calls.append((op,Path(kw['local_path']).name)) or {}
 other.tick(state);other.tick(state)
 saved=other.job('owner',state['id'])
 assert calls==[('upload','1'),('upload','2')] and saved['upload_index']==3
 assert saved['upload_attempts']==0 and saved['upload_retry_at']==0 and saved['remote_diagnostics']=={}

@pytest.mark.parametrize('cancel,expire',[(True,False),(False,True)])
def test_cancel_deadline_during_backoff_release_without_upload(upload_job,cancel,expire):
 s,state,now=upload_job;calls=[]
 s.update(state['id'],upload_retry_at=1100,cancel_requested=cancel,deadline_epoch=999 if expire else 2000,
          remote_diagnostics={'stage':'upload','error_code':'input_upload_failed'})
 s._call=lambda state,op,**kw:calls.append(op) or {'stopped':True}
 s.tick(state)
 assert calls==['runtime_stop'] and s.job('owner',state['id'])['phase']==('cancelled' if cancel else 'failed')


def test_crash_after_last_attempt_does_not_grant_extra_upload(upload_job):
 s,state,now=upload_job;calls=[]
 s.update(state['id'],upload_attempt_index=1,upload_attempts=3)
 s._call=lambda state,op,**kw:calls.append(op) or {'stopped':True}
 s.tick(state)
 assert calls==['runtime_stop'] and s.job('owner',state['id'])['phase']=='failed'


def test_local_corruption_stops_without_network_upload(upload_job):
 s,state,now=upload_job;calls=[]
 (s.root/'jobs'/state['id']/'1').write_bytes(b'changed')
 s._call=lambda state,op,**kw:calls.append(op) or {'stopped':True}
 s.tick(state);saved=s.job('owner',state['id'])
 assert calls==['runtime_stop'] and saved['remote_diagnostics']['bridge_error_code']=='upload_local_invalid'

@pytest.mark.parametrize('step',['runtime_lookup','directory_put','file_put'])
def test_real_bridge_upload_step_and_response_close(tmp_path,step):
 from scripts.colab_bridge import Bridge as Worker,BridgeError as WorkerError
 local=tmp_path/'file';local.write_bytes(b'abc');closed=[]
 class Response:
  def __init__(self,fail):self.fail=fail
  def raise_for_status(self):
   if self.fail:
    error=Exception('SECRET response');error.response=NS(status_code=413);raise error
  def json(self):return {'type':'directory'}
  def close(self):closed.append(True)
 class HTTP:
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def request(self,method,url,**kw):return Response(step==('directory_put' if kw['json']['type']=='directory' else 'file_put'))
 worker=Worker(tmp_path/'account',NS(http=HTTP))
 def runtime(rid):
  if step=='runtime_lookup':
   error=Exception('SECRET URL');error.response=NS(status_code=413);raise error
  return {'url':'https://synthetic.invalid','token':('test-' + 'key-not-' + 'secret-0001')}
 worker.runtime=runtime
 with pytest.raises(WorkerError) as caught:worker.upload({'runtime_id':oid(),'local_path':str(local),'remote_path':'/content/test/file','max_bytes':32})
 d=caught.value.public()['diagnostics']
 assert d['upload_substage']==step and d['http_status']==413 and d['bridge_error_code']=='upload_http_error'
 assert 'SECRET' not in str(caught.value.public())
 assert len(closed)==(0 if step=='runtime_lookup' else 1 if step=='directory_put' else 2)

@pytest.mark.parametrize('error',[TimeoutError('SECRET'),BridgeError('timeout'),BridgeError('operation_timeout')])
def test_timeout_classification(error):
 assert classify(error)['bridge_error_code']=='upload_timeout'


def test_cleanup_diagnostic_fetch_cannot_overwrite_upload(upload_job):
 s,state,now=upload_job;saved=s.update(state['id'],phase='cleanup_pending',outcome='failed',remote_diagnostics={'stage':'upload','error_code':'input_upload_failed','http_status':503})
 s._exec=lambda *a:(_ for _ in ()).throw(AssertionError('Must not fetch unlaunched worker status'))
 s._call=lambda *a,**k:{'stopped':True};s.tick(saved)
 assert s.job('owner',state['id'])['remote_diagnostics']==saved['remote_diagnostics']

@pytest.mark.parametrize('code',['upload_http_error','upload_timeout','upload_transport_failed','upload_local_invalid'])
def test_parent_bridge_and_receipt_sanitizers_keep_fixed_upload_diagnostic(tmp_path,monkeypatch,code):
 from colab_service import Bridge
 from compute_parity.colab import ColabNative
 d={'stage':'upload','error_code':'input_upload_failed','bridge_error_code':code,'upload_substage':'file_put','http_status':413,'secret':'NEVER'}
 monkeypatch.setattr('colab_service.subprocess.run',lambda *a,**kw:NS(returncode=0,stdout=json.dumps({'ok':False,'error':{'code':code,'diagnostics':d}})))
 with pytest.raises(BridgeError) as caught:Bridge('/usr/bin/python3')('upload',tmp_path)
 safe=classify(caught.value);rid=oid();j=Journal(tmp_path/'journal.db');j.reserve('owner',rid,'colab','account','case',rid,{'unit':'CU','amount':1})
 state={'id':rid,'phase':'cleanup_pending','runtime_id':'runtime','remote_diagnostics':safe}
 recorded=ColabNative(None,j)._record('owner',rid,ColabService.public_job(None,state))
 assert _public_receipt(Runtime.receipt(recorded))['diagnostics']=={k:v for k,v in d.items() if k!='secret'}


def test_upload_sanitizer_does_not_widen_allocation_status():
 assert BridgeError('allocation_uncertain',True,{'http_status':413,'upload_substage':'file_put'}).diagnostics=={}


def test_legacy_null_diagnostic_does_not_block_release(upload_job):
 s,state,now=upload_job;calls=[]
 saved=s.update(state['id'],phase='cleanup_pending',outcome='failed',remote_diagnostics=None)
 s._exec=lambda *a:{'status':{}}
 s._call=lambda state,op,**kw:calls.append(op) or {'stopped':True}
 s.tick(saved)
 assert calls==['runtime_stop'] and s.job('owner',state['id'])['released']
