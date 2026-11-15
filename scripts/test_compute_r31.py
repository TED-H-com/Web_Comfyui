"""Offline r31 regressions: recorded boundary shapes, admission and owner closure."""
import copy,io,json,ssl,time,uuid,urllib.error
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from test_compute_parity_p1 import bundle,files,oid,FakeTransport,no_network,seal
from compute_parity.adapters import RunningHubNative,AutoIPPublic
from compute_parity.journal import Journal
from compute_parity.contracts import ParityError,lane_description
from compute_parity.transport import Transport
from compute_parity.diagnostics import sanitize,attempt_diagnostics

@pytest.mark.parametrize('error,kind',[(TimeoutError('PRIVATE'),'timeout'),(ConnectionError('PRIVATE'),'connection'),(ssl.SSLError('PRIVATE'),'tls'),(urllib.error.URLError(TimeoutError('PRIVATE')),'timeout')])
def test_transport_types_are_safe(error,kind):
 t=Transport('runninghub','synthetic-test-key',enabled=True)
 t._opener=NS(open=lambda *a,**k:(_ for _ in ()).throw(error))
 with pytest.raises(ParityError) as caught:t.request('POST','/prompt',body={})
 d=caught.value.diagnostics
 assert d['stage']=='prompt_transport' and d['transport_error_type']==kind
 assert 'PRIVATE' not in str(d) and d['prompt_id_present'] is False

@pytest.mark.parametrize('raw,kind',[(b'not json','json_decode'),(b'x'*(16*1024*1024+1),'response_size')])
def test_decode_size_and_prompt_timeout(raw,kind):
 from test_compute_r30 import Response
 calls=[];response=Response(raw);response.status=200
 def open(*a,**kw):calls.append(kw['timeout']);return response
 t=Transport('runninghub','synthetic-test-key',enabled=True);t._opener=NS(open=open)
 with pytest.raises(ParityError) as caught:t.request('POST','/prompt',body={})
 assert calls==[300] and caught.value.diagnostics['http_status']==200
 assert caught.value.diagnostics['transport_error_type']==kind

@pytest.mark.parametrize('body,rid',[({'data':{'taskId':'nested-task','PRIVATE':'secret'},'msg':'secret'},'nested-task'),({'prompt_id':'native','data':{'taskId':'nested'},'error':[]},'native'),({'prompt_id':'invalid id','data':{'prompt_id':'valid'}},'valid'),(['private'],None),({'data':None},None)])
def test_rh_envelopes_preserve_id_first(bundle,tmp_path,body,rid):
 t=FakeTransport(bundle);original=t.request
 t.request=lambda m,p,**kw:body if p=='/prompt' else original(m,p,**kw)
 a=RunningHubNative(t,Journal(tmp_path/'j.db'));result=a.submit('owner',oid(),bundle,files(bundle),quote={'unit':'POINT','amount':10})
 assert result.get('remote_id')==rid
 assert result['diagnostics']['stage']==('prompt_response' if rid else 'id_parse')
 assert result['diagnostics']['prompt_id_present']==bool(rid)
 assert 'secret' not in str(result['diagnostics']) and 'secret' not in json.dumps(result['diagnostics'].get('unknown_keys',{}))  # r32 allows safe key names, never values
 assert result['state']==('queued' if rid else 'submission_uncertain')

@pytest.mark.parametrize('path,stage,state',[('/upload/image','upload','rejected'),('/prompt','prompt_transport','submission_uncertain')])
def test_upload_distinct_from_ambiguous_prompt(bundle,tmp_path,path,stage,state):
 t=FakeTransport(bundle);original=t.request
 def request(m,p,**kw):
  if p==path:raise TimeoutError('private')
  return original(m,p,**kw)
 t.request=request;a=RunningHubNative(t,Journal(tmp_path/'j.db'))
 result=a.submit('owner',oid(),bundle,files(bundle),quote={'unit':'POINT','amount':10})
 assert result['state']==state and result['diagnostics']['stage']==stage
 assert result['diagnostics']['transport_error_type']=='timeout'

def failed(j,case='voice-speak.anime.chatgpt',kind='execution',root='native_runtime_failed',lane='colab'):
 rid=oid();j.reserve('owner',rid,lane,'account',case,rid,{'unit':'CU' if lane=='colab' else 'credits','amount':10})
 return j.update('owner',rid,state='failed',root_cause=root,failure_kind=kind)

def test_epoch_preserves_attempt_cap_and_history(tmp_path):
 j=Journal(tmp_path/'j.db');failed(j);failed(j)
 with pytest.raises(ParityError,match='重新驗證'):j.check_admission('owner','colab','voice-speak.anime.claude')
 with pytest.raises(ParityError):j.revalidate('owner','colab','20261111-r31')
 first=j.revalidate('owner','colab','20261111-r31',authorized=True)
 assert j.revalidate('owner','colab','20261111-r31',authorized=True)==first
 j.check_admission('owner','colab','voice-speak.anime.chatgpt');failed(j)
 with pytest.raises(ParityError) as e:j.check_admission('owner','colab','voice-speak.anime.chatgpt')
 assert e.value.code=='attempt_cap' and j.ledger('owner')['colab']['attempts']==3
 assert j.ledger('someone-else')=={}

def test_contract_scope_and_quality_never_stop(tmp_path):
 j=Journal(tmp_path/'j.db')
 failed(j,'dance.anime.chatgpt','contract','duration_short','autoip');failed(j,'video-replace.anime.claude','contract','duration_short','autoip')
 j.check_admission('owner','autoip','voice-speak.anime.chatgpt')
 with pytest.raises(ParityError) as e:j.check_admission('owner','autoip','dance.realistic.example-a')
 assert e.value.code=='lane_stopped'
 failed(j,'voice-speak.anime.chatgpt','quality','speaker_gate','autoip');failed(j,'voice-speak.anime.claude','quality','speaker_gate','autoip')
 j.check_admission('owner','autoip','voice-speak.realistic.example-a')

def test_autoip_upper_bound_real_25_to_50(tmp_path):
 # P1g four voice tasks each quoted 25, actually charged 50. Exact safe pricing fields.
 observed=json.loads((Path(__file__).parent/'fixtures/r31/autoip-voice-25-to-50.json').read_text())
 evidence={'pricing':observed['pricing'],'balance':{'data':{'credits':1000}}}
 q=AutoIPPublic.normalize_quote(evidence,'voice_clone')
 assert q['amount']==q['base_amount']==50 and q['off_peak_amount']==25 and q['upper_bound']
 j=Journal(tmp_path/'j.db');rid=oid()
 j.reserve('owner',rid,'autoip','account','voice-speak.anime.chatgpt','id',{'unit':'credits','amount':25})
 result=j.update('owner',rid,state='completed',actual_cost={'unit':'credits','amount':50})
 assert result['actual_minus_quote']==25 and result['cost']['amount']==25

def test_autoip_voxcpm_rejected_before_quote(bundle,tmp_path):
 t=FakeTransport(bundle);bundle['feature']='voxcpm-tts';a=AutoIPPublic(t,Journal(tmp_path/'j.db'))
 assert lane_description('voxcpm-tts','autoip')['recipe_mode']=='impossible'
 with pytest.raises(ParityError) as e:a.quote(bundle,voice_consent=True)
 assert e.value.code=='voice_node_contract_required' and not t.calls

def test_quality_survives_router_and_local(tmp_path):
 from quality_rejection import QualityRejected,from_error
 from compute_parity.local import LocalBaseline
 from compute_routing import _public_receipt
 diagnostic={'stage':'speaker_gate','best_similarity':0.819,'threshold':0.820,'attempts':2}
 assert from_error(str(QualityRejected({**diagnostic,'private':'never'})))==diagnostic
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'local','account','voxcpm-tts.shared.custom-english','identity',{'unit':'local','amount':0})
 receipt={'state':'quality_rejected','downstream_id':'run_test','diagnostics':diagnostic}
 result=LocalBaseline(None,j,None)._record('owner',rid,_public_receipt(receipt))
 assert result['state']=='quality_rejected' and result['diagnostics']==diagnostic
 assert j.get('owner',rid)['failure_kind']=='quality'

def test_mapping_rejects_changed_graph_and_inputs(bundle):
 from compute_parity.measurement_mapping import link
 target=copy.deepcopy(bundle);target['runner_sources']={};seal(target)
 assert link(bundle,target)['source_bundle']==bundle['bundle_sha256']
 target['graph']['1']['inputs']['changed']='value';seal(target)
 with pytest.raises(ValueError):link(bundle,target)

def product(tmp_path,bundle):
 from compute_parity.product import ProductRuntime
 p=object.__new__(ProductRuntime);p.journal=Journal(tmp_path/'j.db')
 class Materials:
  def bundle(self,*args):return bundle
  def __call__(self,*args):return bundle,files(bundle)
 p.materials=Materials();p.voice_consent=lambda *a:True
 p.evidence=lambda *a:{'quote':{'unit':'CU','amount':15},'account_id':'account'}
 p.web=NS(owner='owner');return p

def test_product_preview_and_submit_known_lock_no_io(tmp_path,bundle):
 from compute_routing import ComputeRejected
 p=product(tmp_path,bundle);failed(p.journal);failed(p.journal)
 p.evidence=lambda *a:pytest.fail('Locked lane must not obtain provider evidence')
 p.adapter=lambda *a:pytest.fail('Locked lane must not acquire adapter')
 assert p.inspect('owner','colab',{}, {})['reasons']==['lane_stopped']
 with pytest.raises(ComputeRejected) as e:p.submit('owner','colab',oid(),{}, {})
 assert e.value.code=='lane_stopped'
 assert p.journal.ledger('owner')['colab']['attempts']==2

@pytest.mark.parametrize('reserved',[False,True])
def test_product_reserve_race_rejected_only_before_dispatch(tmp_path,bundle,reserved):
 from compute_routing import ComputeRejected
 p=product(tmp_path,bundle);rid=oid()
 def submit(*a,**kw):
  if reserved:p.journal.reserve('owner',rid,'colab','account',bundle['case_id'],'i',{'unit':'CU','amount':15})
  raise ParityError('lane_stopped','safe')
 p.adapter=lambda *a:NS(submit=submit)
 with pytest.raises(ParityError if reserved else ComputeRejected):p.submit('owner','colab',rid,{}, {})

@pytest.fixture
def legacy(tmp_path,monkeypatch):
 from compute_routing import ComputeRouter,ComputeAdapter
 from compute_parity import local_closure
 clock=[10000.];monkeypatch.setattr(local_closure.time,'time',lambda:clock[0])
 rid=oid();did=oid();owner='owner'
 router=ComputeRouter(tmp_path/'routing.db',{'local':ComputeAdapter(lambda *a:{'eligible':True},lambda *a:{'state':'submission_uncertain','downstream_id':did})},clock=lambda:0)
 router.submit(owner,rid,{'featureId':'text2img'},'local')
 states={'queue':{'queue_running':[],'queue_pending':[]},'history':{},'durable':None,'direct':None,'indexed_owner':owner}
 server=NS(DEFAULT_COMFY='http://127.0.0.1:8188',load_job_index=lambda:{did:{'email':states['indexed_owner']}},user_can_access_job=lambda *a:True,
           local_job_snapshot=lambda:[],request_json=lambda path:states['queue'] if path=='/queue' else states['history'])
 web=NS(router=router,owner=owner,user={},s=server,_durable_local_result=lambda *a:states['durable'],_direct_local_result=lambda *a:states['direct'])
 return web,rid,did,clock,states

def test_local_unknown_closure_two_observations_idempotent_and_late(legacy):
 from compute_parity.local_closure import close,reconcile,STATE
 from compute_routing import ComputeRoutingError
 web,rid,did,clock,s=legacy;body={'authorize':True,'downstream_id':did}
 first=close(web,rid,body);assert first['state']=='submission_uncertain'
 clock[0]+=59;assert close(web,rid,body)['state']=='submission_uncertain'
 clock[0]+=1;closed=close(web,rid,body);assert closed['state']==STATE and closed['execution_outcome']=='unknown'
 assert close(web,rid,body)==closed and len(closed['closure_evidence']['observations'])==2
 web.status_local=lambda *a:{'state':'completed','downstream_id':did,'artifacts':[{'url':'/api/output/owned'}]}
 result=reconcile(web,rid,body)
 assert result['state']==STATE and result['late_reconciliation']['receipt']['downstream_id']==did
 with pytest.raises(ComputeRoutingError):reconcile(web,rid,{**body,'downstream_id':oid()})
 assert web.router.submit('owner',rid,{'featureId':'text2img'},'local')['state']==STATE

@pytest.mark.parametrize('damage',['owner','downstream','queue','history','durable','sidecar','malformed_queue','young','unauthorized'])
def test_local_unknown_refuses_incomplete_or_conflicting_evidence(legacy,damage):
 from compute_parity.local_closure import close
 from compute_routing import ComputeRoutingError
 web,rid,did,clock,s=legacy;body={'authorize':True,'downstream_id':did}
 if damage=='owner':web.owner='intruder'
 if damage=='downstream':body['downstream_id']=oid()
 if damage=='queue':s['queue']['queue_running']=[[0,did]]
 if damage=='history':s['history']={did:{}}
 if damage=='durable':s['durable']={'state':'completed'}
 if damage=='sidecar':s['direct']={'state':'completed'}
 if damage=='malformed_queue':s['queue']={}
 if damage=='young':clock[0]=30
 if damage=='unauthorized':body['authorize']=False
 with pytest.raises((ComputeRoutingError,PermissionError)):close(web,rid,body)
 assert web.router.status('owner',rid)['state']=='submission_uncertain'

def test_late_measurement_via_verified_runner_mapping(tmp_path,bundle,monkeypatch):
 from compute_parity import measurement_mapping as mapping
 from compute_parity.hosted_estimates import measurement_from_journal
 old=copy.deepcopy(bundle);new=copy.deepcopy(bundle);new['runner_sources']={'worker.py':{'bytes':1,'sha256':'a'*64}};seal(new)
 entry=mapping.link(old,new);monkeypatch.setattr(mapping,'sources',lambda b:{old['bundle_sha256']:entry} if b==new else {})
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'local','account',bundle['case_id'],'i',{'unit':'local','amount':0})
 assert measurement_from_journal(j.path,'owner',new) is None
 j.update('owner',rid,state='completed',bundle_sha256=old['bundle_sha256'],completed_at=time.time(),wall_seconds=100.0)
 m=measurement_from_journal(j.path,'owner',new)
 assert m['request_id']==rid and m['mapping_version']==mapping.VERSION and m['source_bundle']==old['bundle_sha256']
 assert measurement_from_journal(j.path,'intruder',new) is None


def test_http_nested_id_and_shape_no_context():
 t=Transport('runninghub','synthetic-test-key',enabled=True)
 body={'data':{'taskId':'known'},'private':'never','msg':'secret'}
 def open(*a,**k):raise urllib.error.HTTPError('https://private.invalid/key',502,'private',{},io.BytesIO(json.dumps(body).encode()))
 t._opener=NS(open=open)
 with pytest.raises(ParityError) as e:t.request('POST','/prompt',body={})
 assert e.value.prompt_id=='known' and e.value.__context__ is None
 assert e.value.diagnostics['http_status']==502 and e.value.diagnostics['transport_error_type']=='http'
 assert e.value.diagnostics['response_keys']==['data','msg']

def test_list_api_never_recovers_historical_receipts(monkeypatch):
 import server,compute_web
 saved=[];rid=oid()
 router=NS(list_jobs=lambda *a,**k:[{'request_id':rid,'lane':'runninghub','state':'submission_uncertain'}],
           status=lambda *a,**k:pytest.fail('Listing must not recover'))
 monkeypatch.setattr(compute_web,'ComputeWeb',lambda *a:NS(owner='owner',user={},router=router))
 handler=NS(path='/api/compute/jobs',send_json=lambda data,*a,**kw:saved.append(data))
 server.Handler.api_compute(handler,'/api/compute/jobs',False)
 assert saved[0]['jobs'][0]['request_id']==rid

def test_quality_flows_from_owned_orchestrator(monkeypatch):
 from compute_web import ComputeWeb
 from quality_rejection import QualityRejected
 diagnostic={'stage':'speaker_gate','best_similarity':.819,'threshold':.82,'attempts':2}
 w=object.__new__(ComputeWeb)
 w._owned_orchestrator_run=lambda task:{'status':'failed','tasks':[{'error':str(QualityRejected(diagnostic))}]}
 result=w._orchestrator_local_result('run_test')
 assert result['state']=='quality_rejected' and result['diagnostics']==diagnostic

def test_completed_output_contract_failure_stops_only_video(tmp_path,bundle):
 p=product(tmp_path,bundle)
 for case in ('dance.anime.chatgpt','video-replace.anime.claude'):
  rid=oid();p.journal.reserve('owner',rid,'autoip','account',case,rid,{'amount':50,'unit':'credits'})
  job=p.journal.update('owner',rid,state='completed',artifacts=[{'index':0}])
  p.artifact=lambda *a:(_ for _ in ()).throw(ParityError('duration_short','safe'))
  assert p.receipt(job)['state']=='failed'
 p.journal.check_admission('owner','autoip','voice-speak.anime.chatgpt')
 with pytest.raises(ParityError) as e:p.journal.check_admission('owner','autoip','dance.realistic.example-b')
 assert e.value.code=='lane_stopped'

def test_revalidation_http_requires_explicit_owner_authorization():
 from compute_parity.http import handle
 from compute_routing import ComputeRoutingError
 for body in ({'lane':'colab'},{'lane':'colab','authorize':False},{'lane':'colab','authorize':True,'owner':'other'}):
  with pytest.raises(ComputeRoutingError):handle(NS(),NS(),['parity','revalidate'],True,body)


def test_two_unknown_execution_closures_stop_until_reviewed_fix(tmp_path):
 j=Journal(tmp_path/'j.db')
 for _ in range(2):
  rid=oid();j.reserve('owner',rid,'runninghub','account','dance.anime.chatgpt',rid,{'unit':'POINT','amount':50})
  j.update('owner',rid,state='closed_no_execution_evidence',root_cause='no_execution_evidence')
 with pytest.raises(ParityError) as e:j.check_admission('owner','runninghub','dance.anime.claude')
 assert e.value.code=='lane_stopped'
 j.revalidate('owner','runninghub','20261111-r31',authorized=True)
 j.check_admission('owner','runninghub','dance.anime.chatgpt')
 assert j.ledger('owner')['runninghub']['attempts']==2

def test_voxcpm_child_quality_diagnostic_keeps_threshold(tmp_path,monkeypatch):
 from orchestrator import tasks
 from quality_rejection import QualityRejected
 work=tmp_path/'work';(work/'tmp').mkdir(parents=True)
 diagnostic={'stage':'speaker_gate','best_similarity':.819,'threshold':.82,'attempts':2}
 monkeypatch.setattr(tasks,'_trusted_existing_path',lambda *a,**k:tmp_path)
 monkeypatch.setattr(tasks,'_safe_output_path',lambda p,*a:Path(p))
 monkeypatch.setattr(tasks,'gpu_queue_cmd',lambda args,payload:args)
 seen=[]
 def child(args,**kw):
  seen.extend(args)
  side=Path(args[args.index('--sidecar')+1]);Path(str(side)+'.quality.json').write_text(str(QualityRejected(diagnostic)))
  raise tasks.subprocess.CalledProcessError(1,args)
 monkeypatch.setattr(tasks.subprocess,'check_call',child)
 with pytest.raises(QualityRejected) as e:
  tasks.run_castle_voxcpm_tts({'output_path':str(tmp_path/'result.wav'),'sidecar_path':str(tmp_path/'result.json'),'speaker_gate':True,'speaker_threshold':.82,'speaker_max_attempts':2},work,work/'cancel')
 assert e.value.diagnostics==diagnostic
 assert seen[seen.index('--speaker-threshold')+1]=='0.82' and seen[seen.index('--speaker-max-attempts')+1]=='2'
 assert not list(work.rglob('*.quality.json'))

def test_task_wrapper_saves_only_structured_quality(tmp_path,monkeypatch):
 from orchestrator import task_wrapper as wrapper
 from quality_rejection import QualityRejected,from_error
 diagnostic={'stage':'speaker_gate','best_similarity':.819,'threshold':.82,'attempts':2}
 work=tmp_path/'work';work.mkdir();payload=tmp_path/'input.json';payload.write_text('{}');sentinel=work/'done.json'
 monkeypatch.setattr(wrapper,'run_task',lambda *a:(_ for _ in ()).throw(QualityRejected(diagnostic)))
 monkeypatch.setattr(wrapper,'install_signal_handlers',lambda *a:None)
 monkeypatch.setattr(wrapper.sys,'argv',['wrapper','--task-id','task_test','--task-type','castle.voxcpm_tts','--input-json',str(payload),'--work-dir',str(work),'--marker','test','--sentinel',str(sentinel)])
 assert wrapper.main()==1
 result=json.loads(sentinel.read_text())
 assert result['diagnostics']==diagnostic and from_error(result['error'])==diagnostic
 assert 'traceback' not in result and result['outputs']==[]



def test_public_predispatch_record_uses_generic_journal(tmp_path):
 from compute_parity.revalidation import revalidate
 calls=[]
 expected={'epoch':'synthetic'}
 journal=NS(revalidate=lambda *args,**kwargs: calls.append((args,kwargs)) or expected)
 web=NS(owner='owner@example.invalid',parity=NS(journal=journal))
 assert revalidate(web,'colab') == expected
 assert calls == [(('owner@example.invalid','colab','20261111-r31'),{'authorized':True})]
