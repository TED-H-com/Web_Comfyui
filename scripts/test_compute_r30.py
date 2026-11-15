"""r30 regression: trimmed authenticated provider GET fixtures plus adversarial cases."""
import copy,io,json,time,uuid,urllib.error
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from test_compute_parity_p1 import bundle,files,oid,FakeTransport,no_network,seal
from compute_parity.adapters import RunningHubNative,CloudNative,AutoIPPublic,call
from compute_parity.journal import Journal
from compute_parity.contracts import compile_request,dependency_probe,ParityError
from compute_parity.transport import Transport
F=Path(__file__).parent/'fixtures/r30'
def fixture(name):return json.loads((F/name).read_text())
CLOUD=fixture('cloud-job.json')['id']
REQUEST='00000000-0000-4000-8000-00000000d001'

def reserved(tmp_path,lane='runninghub',rid=None):
 j=Journal(tmp_path/'journal.db');rid=rid or oid()
 j.reserve('owner',rid,lane,'account','dance.anime.chatgpt','identity',{'unit':'POINT' if lane=='runninghub' else 'credits','amount':100})
 j.update('owner',rid,state='submission_uncertain',created_at=0,submitted_at=0)
 return j,rid

@pytest.mark.parametrize('inventory',[None,{('unused','unused','unused')}])
def test_real_required_schema_and_v3_groups(inventory):
 info=fixture('runninghub-object-info.json')
 values={'video':'owned.mp4','force_rate':16,'custom_width':432,'custom_height':768,'frame_load_cap':213,'skip_first_frames':0,'select_every_nth':1}
 g={'15':{'class_type':'VHS_LoadVideo','inputs':values},'28':{'class_type':'BatchImagesNode','inputs':{'images.image0':['15',0],'images.image1':['15',0]}}}
 assert dependency_probe(g,info,model_inventory=inventory)['missing_inputs']==['VHS_LoadVideo.force_size']
 values['force_size']='Custom'
 assert dependency_probe(g,info,model_inventory=inventory)['ready']
 g['28']['inputs']={}
 assert dependency_probe(g,info)['missing_inputs']==['BatchImagesNode.images']
 g['28']['inputs']={'images.image51':['15',0]}
 assert not dependency_probe(g,info)['ready']

def test_compiled_loader_custom_preserves_approved_dimensions(bundle):
 bundle['graph']['2']['inputs'].update(custom_width=432,custom_height=768);seal(bundle)
 v=compile_request(bundle,'runninghub',request_id=oid())['json']['prompt']['2']['inputs']
 assert (v['force_size'],v['custom_width'],v['custom_height'])==('Custom',432,768)

class CloudCaptured(FakeTransport):
 def __init__(self,b):super().__init__(b);self.fingerprint='account'
 def request(self,method,path,**kw):
  self.calls.append((method,path,kw))
  if path=='/api/v2/assets':return {'id':oid()}
  if path=='/api/v2/jobs':return fixture('cloud-job.json')
  if path=='/api/jobs?limit=100':return fixture('cloud-jobs.json')
  if path.endswith('/workflow'):return fixture('cloud-workflow.json')
  if path=='/api/v2/jobs/'+CLOUD:return fixture('cloud-job.json')
  if path=='/api/billing/usage/timeseries':return {}
  pytest.fail('Unexpected provider operation')

def test_cloud_persists_id_despite_http_links(bundle,tmp_path,monkeypatch):
 t=CloudCaptured(bundle);a=CloudNative(t,Journal(tmp_path/'j.db'));monkeypatch.setattr(a,'probe',lambda b:{'ready':True})
 result=a.submit('owner',REQUEST,bundle,files(bundle),quote={'unit':'credits','amount':10})
 assert result['remote_id']==CLOUD and result['state']=='queued'
 j=a.journal.get('owner',REQUEST);assert j['links']['self']=='/api/v2/jobs/'+CLOUD
 a.poll('owner',REQUEST)
 assert a.journal.get('owner',REQUEST)['output_links'][0].startswith('/api/v2/assets/')
 assert all(not p.startswith('http:') for _,p,_ in t.calls)

def test_cloud_id_survives_link_processing_exception(bundle,tmp_path,monkeypatch):
 t=CloudCaptured(bundle);a=CloudNative(t,Journal(tmp_path/'j.db'));monkeypatch.setattr(a,'probe',lambda b:{'ready':True})
 monkeypatch.setattr(a,'_job_links',lambda rid:(_ for _ in ()).throw(ParityError('cloud_link_invalid','safe')))
 result=a.submit('owner',REQUEST,bundle,files(bundle),quote={'unit':'credits','amount':10})
 assert result['remote_id']==CLOUD

def test_cloud_exact_executed_graph_recovery_never_posts(bundle,tmp_path):
 j,rid=reserved(tmp_path,'comfy_cloud',REQUEST);t=CloudCaptured(bundle);a=CloudNative(t,j)
 result=a.recover('owner',rid)
 assert result['state']=='completed' and result['remote_id']==CLOUD
 assert all(m=='GET' for m,_,_ in t.calls)
 assert j.get('owner',rid)['recovery_evidence']['new_submissions']==0

@pytest.mark.parametrize('prefix',['parity/different','parity/'+REQUEST+'extra'])
def test_cloud_nonmatching_prefix_not_recovered(bundle,tmp_path,prefix):
 j,rid=reserved(tmp_path,'comfy_cloud',REQUEST);t=CloudCaptured(bundle);original=t.request
 def req(m,p,**kw):
  if p.endswith('/workflow'):return {'workflow':{'1':{'inputs':{'filename_prefix':prefix}}}}
  return original(m,p,**kw)
 t.request=req
 assert CloudNative(t,j).recover('owner',rid)['state']=='submission_uncertain'
 assert not j.get('owner',rid).get('remote_id')

class Response:
 def __init__(self,raw=b'video'):self.raw=raw;self.headers={'Content-Length':str(len(raw))}
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self,*a):return self.raw

def redirect(url):return urllib.error.HTTPError('https://cloud.comfy.org',302,'Found',{'Location':url},io.BytesIO())

def test_cloud_real_302_shape_drops_key_and_checks_size():
 t=Transport('comfy_cloud','synthetic-test-key',enabled=True);calls=[]
 loc=fixture('cloud-redirect.json')['location']
 def open(req,**kw):
  calls.append(req)
  if len(calls)==1:raise redirect(loc)
  assert not req.get_header('Authorization') and not req.get_header('X-api-key')
  return Response()
 t._opener=NS(open=open)
 assert t.download_output('/api/v2/assets/00000000-0000-4000-8000-00000000c002/content',expected_size=5)==b'video'
 assert calls[0].get_header('Authorization')
 with pytest.raises(ParityError):t.download_output(loc,expected_size=6)

@pytest.mark.parametrize('url',['http://storage.googleapis.com/a','https://evil.test/a','https://user@storage.googleapis.com/a','https://storage.googleapis.com:444/a','https://127.0.0.1/a'])
def test_cloud_malicious_redirect_rejected_before_next_request(url):
 t=Transport('comfy_cloud','synthetic-test-key',enabled=True);calls=[]
 def open(req,**kw):calls.append(req);raise redirect(url)
 t._opener=NS(open=open)
 with pytest.raises(ParityError):t.download_output('/api/v2/assets/00000000-0000-4000-8000-00000000c002/content')
 assert len(calls)==1

def test_cloud_redirect_bound_and_no_key_when_redirecting_back():
 t=Transport('comfy_cloud','synthetic-test-key',enabled=True);calls=[]
 origin='https://cloud.comfy.org/api/v2/assets/00000000-0000-4000-8000-00000000c002/content'
 def open(req,**kw):
  calls.append(req)
  if len(calls)>1:assert not req.get_header('Authorization')
  raise redirect('https://storage.googleapis.com/a' if len(calls)%2 else origin)
 t._opener=NS(open=open)
 with pytest.raises(ParityError):t.download_output(origin)
 assert len(calls)==4

def validation_body(prompt=None):
 # Synthetic error case: original failed POST was NOT captured. Real required schema above establishes B8.
 return {'prompt_id':prompt,'error':{'type':'prompt_outputs_failed_validation','message':'https://private.invalid/token'},
         'node_errors':{'15':{'class_type':'VHS_LoadVideo','errors':[{'type':'required_input_missing','message':'PRIVATE'}]}}}

@pytest.mark.parametrize('accepted',[False,True])
def test_runninghub_diagnostics_and_prompt_id_precede_node_errors(bundle,tmp_path,accepted):
 t=FakeTransport(bundle);old=t.request
 t.request=lambda m,p,**kw:validation_body('native-id' if accepted else None) if p=='/prompt' else old(m,p,**kw)
 a=RunningHubNative(t,Journal(tmp_path/'j.db'));rid=oid()
 r=a.submit('owner',rid,bundle,files(bundle),quote={'unit':'POINT','amount':10})
 assert r['state']==('queued' if accepted else 'rejected')
 assert bool(r.get('remote_id'))==accepted
 assert r['diagnostics']['nodes']==[{'node_id':'15','node_class':'VHS_LoadVideo','error_type':'required_input_missing'}]
 assert 'PRIVATE' not in str(r) and 'https:' not in str(r)

def test_transport_keeps_safe_http_reason_without_raw_error():
 t=Transport('runninghub','synthetic-test-key',enabled=True)
 body=json.dumps(validation_body()).encode()
 t._opener=NS(open=lambda *a,**kw:(_ for _ in ()).throw(urllib.error.HTTPError('https://private',400,'PRIVATE',{},io.BytesIO(body))))
 with pytest.raises(ParityError) as error:call(t,'POST','/prompt',body={})
 assert error.value.diagnostics['http_status']==400
 assert error.value.diagnostics['reason_code']=='prompt_outputs_failed_validation'
 assert 'PRIVATE' not in str(error.value)

@pytest.mark.parametrize('before,age,span,drop,closed',[(True,3599,0,0,False),(True,3600,0,0,True),(True,7200,0,1,False),(False,3600,1799,0,False),(False,3600,1800,0,True),(False,7200,1800,1,False)])
def test_uncertain_closure_all_conditions(tmp_path,monkeypatch,before,age,span,drop,closed):
 j,rid=reserved(tmp_path);now=[age-span];monkeypatch.setattr(time,'time',lambda:now[0]);value=[100]
 captured=fixture('runninghub-absence.json');calls=[]
 def request(m,p,**kw):calls.append((m,p));assert m=='GET';return copy.deepcopy(captured[p[1:]])
 a=RunningHubNative(NS(fingerprint='account',request=request),j);a.observe_point=lambda:value[0]
 if before:j.update('owner',rid,pre_submission_point={'at':-1,'account':'account','unit':'POINT','value':100})
 else:a.recover('owner',rid)
 now[0]=age;value[0]-=drop;r=a.recover('owner',rid)
 assert (r['state']=='closed_no_execution_evidence')==closed
 assert j.get('owner',rid)['closure_evidence']['history_absent']
 if closed:
  original=j.get('owner',rid);a.recover('owner',rid);assert j.get('owner',rid)==original
  # New request, counted separately; original never re-POSTed.
  for n in (1,2):
   new=oid();j.reserve('owner',new,'runninghub','account','dance.anime.chatgpt',new,{'unit':'POINT','amount':1});j.update('owner',new,state='rejected')
  with pytest.raises(ParityError) as error:j.reserve('owner',oid(),'runninghub','account','dance.anime.chatgpt','next',{'unit':'POINT','amount':1})
  assert error.value.code=='attempt_cap'

@pytest.mark.parametrize('damage',['queue','history','malformed','balance','account'])
def test_uncertain_no_false_closure(tmp_path,monkeypatch,damage):
 j,rid=reserved(tmp_path);monkeypatch.setattr(time,'time',lambda:7200)
 captured=fixture('runninghub-absence.json')
 if damage=='queue':captured['queue']['queue_running']=[[0,'remote',{}, {'castle_request_id':rid}]]
 if damage=='history':captured['history']={'remote':{'prompt':[0,'remote',{}, {'castle_request_id':rid}]}}
 if damage=='malformed':captured['queue']={}
 a=RunningHubNative(NS(fingerprint='account',request=lambda m,p,**kw:captured[p[1:]]),j)
 a.observe_point=lambda:None if damage=='balance' else 100
 j.update('owner',rid,pre_submission_point={'at':-1,'unit':'POINT','account':'other' if damage=='account' else 'account','value':100})
 try:a.recover('owner',rid)
 except ParityError:pass
 assert j.get('owner',rid)['state']!='closed_no_execution_evidence'

def test_uncertain_never_starts_completed_monitor(tmp_path):
 from compute_parity.lifecycle import ensure_monitor
 j,rid=reserved(tmp_path);ensure_monitor(NS(),j,'owner',rid)
 assert 'lifecycle_monitor' not in j.get('owner',rid)

def test_autoip_terminal_settlement_monotonic_and_outer_id(tmp_path):
 from compute_routing import ComputeRouter,ComputeAdapter
 from compute_parity.runtime import Runtime
 j,rid=reserved(tmp_path,'autoip');response=fixture('autoip-settlement.json')
 j.update('owner',rid,state='completed',remote_id=response['task_uuid'],artifacts=[{'index':0}])
 calls=[]
 def get(m,p,**kw):calls.append((m,p));assert m=='GET';return response
 a=AutoIPPublic(NS(fingerprint='account',request=get),j)
 router=ComputeRouter(tmp_path/'router.db',{'autoip':ComputeAdapter(inspect=lambda *a:{'eligible':True},submit=lambda *a:Runtime.receipt(j.get('owner',rid)),status=lambda o,r:Runtime.receipt(a.poll(o,r)))})
 # Use ordinary public router submission, with only the adapter dispatch mocked.
 result=router.submit('owner',rid,{'featureId':'dance','settings':{}},placement='autoip')
 first=router.status('owner',rid,refresh=True)
 assert first['state']=='completed'
 assert first['actual_cost']=={'unit':'credits','amount':100}
 snapshot=router.status('owner',rid,refresh=True);assert snapshot==first
 assert calls==[('GET','/api/v1/tasks/'+response['task_uuid'])]
 j.update('owner',rid,state='failed',artifacts=[],actual_cost=None,settlement_status='pending')
 assert j.get('owner',rid)['state']=='completed' and j.get('owner',rid)['artifacts']==[{'index':0}]
 assert j.get('owner',rid)['actual_cost']['amount']==100

def test_colab_whitelisted_stage_diagnostics_and_cu(tmp_path):
 from scripts.colab_remote.remote_job import sanitize_diagnostics
 from compute_parity.colab import ColabNative
 from compute_parity.runtime import Runtime
 from compute_routing import _public_receipt
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'colab','account','case','id',{'unit':'CU','amount':15})
 diagnostic={'stage':'prompt_validation','error_type':'ValueError','error_code':'node_validation_failed','node_class':'VHS_LoadVideo','node_id':'15','http_status':400,'stdout':'SECRET','url':'https://private','token':'SECRET'}
 r=ColabNative(NS(),j)._record('owner',rid,{'id':rid,'phase':'failed','released':True,'units_observed':.1,'diagnostics':diagnostic})
 assert 'SECRET' not in str(r) and 'A100 unavailable' not in str(r)
 assert r['diagnostics']==sanitize_diagnostics(diagnostic)
 assert _public_receipt(Runtime.receipt(r))['actual_cost']=={'unit':'CU','amount':.1}

@pytest.mark.parametrize('stage',['bootstrap','model_download','node_start','prompt_validation','execute','ffprobe'])
def test_colab_stage_whitelist(stage):
 from scripts.colab_remote.remote_job import failure_diagnostics
 assert failure_diagnostics(ValueError('URL token stdout'),stage)=={'stage':stage,'error_type':'ValueError'}

def test_every_measured_local_has_versioned_hosted_estimate():
 from compute_parity.hosted_estimates import MEASUREMENTS,estimate
 for sha,m in MEASUREMENTS.items():
  for lane,rate in [('runninghub',.25),('comfy_cloud',.266)]:
   q=estimate({'bundle_sha256':sha,'case_id':m['case_id']},lane)
   assert q['amount']==round((min(m['wall_seconds']*1.5,1800) if lane=='comfy_cloud' else m['wall_seconds']*1.5)*rate,6)
   assert q['estimate_version']=='20261111-r30' and q['measured_at'] and '估計，非官方報價' in q['label']


def test_runninghub_http_error_with_prompt_id_is_not_rejected(bundle,tmp_path):
 t=FakeTransport(bundle);old=t.request
 def request(m,p,**kw):
  if p=='/prompt':
   error=ParityError('provider_http','safe');error.prompt_id='accepted-native'
   error.diagnostics={'http_status':400,'reason_code':'prompt_outputs_failed_validation'};raise error
  return old(m,p,**kw)
 t.request=request;a=RunningHubNative(t,Journal(tmp_path/'j.db'));rid=oid()
 r=a.submit('owner',rid,bundle,files(bundle),quote={'unit':'POINT','amount':1})
 assert r['state']=='queued' and r['remote_id']=='accepted-native'

def test_unrelated_identified_history_does_not_deadlock_uncertain(tmp_path,monkeypatch):
 j,rid=reserved(tmp_path);monkeypatch.setattr(time,'time',lambda:3600)
 response={'/history':{'different':{'prompt':[0,'different',{}, {'castle_request_id':oid()}]}},'/queue':{'queue_running':[],'queue_pending':[]}}
 a=RunningHubNative(NS(fingerprint='account',request=lambda m,p,**kw:response[p]),j);a.observe_point=lambda:100
 j.update('owner',rid,pre_submission_point={'at':-1,'unit':'POINT','value':100,'account':'account'})
 assert a.recover('owner',rid)['state']=='closed_no_execution_evidence'
 assert j.get('owner',rid)['closure_evidence']['history_count']==1

def test_new_local_measurement_is_owner_and_bundle_bound(tmp_path,monkeypatch,bundle):
 from compute_parity.local import LocalBaseline
 from compute_parity.hosted_estimates import measurement_from_journal,estimate
 j=Journal(tmp_path/'j.db');clock=[100];monkeypatch.setattr(time,'time',lambda:clock[0])
 a=LocalBaseline(NS(submit=lambda *args:{'state':'queued','downstream_id':'local-id'},status=lambda *args:{'state':'completed','downstream_id':'local-id'}),j,None)
 rid=oid();a.submit('owner',rid,bundle,files(bundle),request={});clock[0]=200;a.poll('owner',rid)
 m=measurement_from_journal(j.path,'owner',bundle);assert m['wall_seconds']==100
 assert estimate(bundle,'runninghub',measurement=m)['amount']==37.5
 assert measurement_from_journal(j.path,'other',bundle) is None
 assert measurement_from_journal(j.path,'owner',{**bundle,'bundle_sha256':'wrong'}) is None

def test_colab_fetches_sanitized_status_before_cleanup(tmp_path):
 from colab_service import ColabService
 service=object.__new__(ColabService);events=[];service.clock=lambda:100
 state={'id':oid(),'runtime_id':'lease','native_parity':True,'manifest_sha256':'a'*64,'phase':'cleanup_pending'}
 diagnostic={'stage':'execute','error_type':'RuntimeError','stdout':'SECRET','url':'https://private'}
 def execute(s,code):
  events.append('diagnostic_get');assert 'runner.log' not in code
  return {'status':{'job_id':state['id'],'manifest_sha256':state['manifest_sha256'],'diagnostics':diagnostic}}
 def update(rid,**changes):state.update(changes);return dict(state)
 def call(s,operation,**kw):
  events.append(operation);assert s['remote_diagnostics']=={'stage':'execute','error_type':'RuntimeError'}
  # Stop at the release call; no real provider, no lease exists in this test.
  raise RuntimeError('test ends at cleanup boundary')
 service._exec=execute;service.update=update;service._call=call
 with pytest.raises(RuntimeError):service._cleanup(state)
 assert events==['diagnostic_get','runtime_stop'] and 'SECRET' not in str(state)

def test_colab_real_old_failure_does_not_claim_a100_shortage():
 from compute_parity.colab import ColabNative
 data=fixture('colab-failure.json')
 assert data['released'] and data['remote_phase']=='failed' and data['remote_diagnostics']=={}
 assert 'allocation_unavailable' not in str(data)



def test_uncertain_keeps_first_observation_and_never_forgets_drain(tmp_path,monkeypatch):
 j,rid=reserved(tmp_path);now=[3600];monkeypatch.setattr(time,'time',lambda:now[0]);value=[100]
 response=fixture('runninghub-absence.json')
 a=RunningHubNative(NS(fingerprint='account',request=lambda m,p,**kw:response[p[1:]]),j);a.observe_point=lambda:value[0]
 a.recover('owner',rid);value[0]=99;now[0]+=60;a.recover('owner',rid);value[0]=100
 # An unexplained drop cannot be erased by long polling or recovery to the same balance.
 for i in range(140):now[0]+=60;a.recover('owner',rid)
 evidence=j.get('owner',rid)['closure_evidence']
 assert evidence['observations'][0]['at']==3600 and evidence['unexplained_drop']
 assert len(evidence['observations'])==128 and j.get('owner',rid)['state']=='submission_uncertain'

def test_cost_allowlist_is_independent_of_model_aliases():
 from compute_routing import _public_receipt
 r=_public_receipt({'state':'completed','downstream_id':'original','actual_cost':{'unit':'credits','amount':100},'settlement_status':'settled','balance_observation':{'at':1,'unit':'credits','value':1000,'status':'observed','token':'SECRET'}})
 assert r['actual_cost']=={'unit':'credits','amount':100} and r['balance_observation']['unit']=='credits'
 assert 'model_aliases' not in r and 'SECRET' not in str(r)

def test_diagnostic_hostile_types_never_leak():
 from compute_parity.diagnostics import sanitize,response_diagnostics
 from scripts.colab_remote.remote_job import sanitize_diagnostics
 assert sanitize({'nodes':[{'node_id':{},'node_class':{},'error_type':[]}],'reason_code':{'token':'secret'}})=={}
 assert response_diagnostics({'node_errors':{'15':{'errors':None}}})=={}
 assert sanitize_diagnostics({'node_class':{},'error_code':[]})=={}

def test_pipeline_closure_blocks_dependents_and_retry_has_new_identity(tmp_path):
 from compute_pipeline import ComputePipeline
 from compute_routing import ComputeRouter,ComputeAdapter
 submitted=[]
 def submit(owner,rid,*args):submitted.append(rid);return {'state':'submission_uncertain','downstream_id':rid}
 router=ComputeRouter(tmp_path/'routing.db',{'runninghub':ComputeAdapter(inspect=lambda *a:{'eligible':True},submit=submit,status=lambda owner,rid:{'state':'closed_no_execution_evidence','downstream_id':rid})})
 pipeline=ComputePipeline(tmp_path/'pipeline.db',router_factory=lambda owner:router,artifact_resolver=lambda *a:None)
 pid=oid();pipeline.create('owner',pid,{'version':1,'steps':[{'id':'run','request':{'featureId':'dance'},'placement':'runninghub'},{'id':'next','request':{'featureId':'video-replace'},'placement':'runninghub','depends_on':['run']}]})
 pipeline.tick('owner',pid);pipeline.tick('owner',pid)
 result=pipeline.status('owner',pid);assert result['state']=='blocked'
 assert result['steps'][0]['retry_allowed'] and result['steps'][1]['state']=='blocked'
 original=result['steps'][0]['request_id'];new=pipeline.retry('owner',pid,'run')
 assert new['steps'][0]['request_id']!=original and submitted==[original]
 assert new['steps'][0]['attempt']==2
