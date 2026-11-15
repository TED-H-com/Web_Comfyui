"""Offline r32 regressions: no provider, GPU or live state writes."""
import copy,json,sqlite3,subprocess,time
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from test_compute_parity_p1 import bundle,files,oid,FakeTransport
from compute_parity.contracts import ParityError
from compute_parity.journal import Journal
from compute_parity.diagnostics import attempt_diagnostics,sanitize,prompt_identity
from compute_parity.provisioning import provisioning_only
from compute_routing import _public_receipt

@pytest.mark.parametrize('body,kind,empty',[(None,'null',True),({},'object',True),([],'array',True),('','string',True),('private','string',False),(0,'number',False),(False,'bool',False),([1],'array',False),({'data':[]},'object',False)])
def test_envelope_types_through_router(body,kind,empty):
 d=attempt_diagnostics('id_parse',body,status=200)
 result=_public_receipt({'state':'submission_uncertain','diagnostics':d})['diagnostics']
 assert result==sanitize(d)
 assert result['response_kind']==kind and result['response_empty'] is empty
 assert result['data_kind']==('array' if isinstance(body,dict) and 'data' in body else 'null')
 assert 'private' not in str(result)

@pytest.mark.parametrize('length,bucket',[(0,'0'),(1,'1-64'),(64,'1-64'),(65,'65-1024'),(1024,'65-1024'),(1025,'1025-16384'),(16384,'1025-16384'),(16385,'16385+')])
def test_string_length_buckets_only(length,bucket):
 d=attempt_diagnostics('id_parse','!'*length,status=200)
 assert d['response_length_bucket']==bucket and '!' not in str(d)

def test_unknown_names_are_sorted_bounded_and_values_never_stored():
 top={f'field_{i:02}':'SECRET-VALUE' for i in range(20)}
 top.update({'with space':'SECRET-VALUE','a'*42:'SECRET-VALUE','中文字':'SECRET-VALUE','data':{'newField':{'nestedSecret':'SECRET-VALUE'},'9bad':'SECRET-VALUE'},'code':0})
 d=sanitize(attempt_diagnostics('id_parse',top,status=200))
 assert d['unknown_keys']=={'top_level':[f'field_{i:02}' for i in range(16)],'data_level':['newField']}
 assert d['unknown_key_count']=={'top_level':23,'data_level':2}
 assert not any(s in json.dumps(d,ensure_ascii=False) for s in ['SECRET-VALUE','with space','中文字','nestedSecret','9bad','a'*42])
 assert prompt_identity({'id':'unproven','data':{'unknownId':'unproven'}}) is None
 assert sanitize({'unknown_keys':{'top_level':[{},'bad-name','ok','ok']},'response_length_bucket':[]})=={'unknown_keys':{'top_level':['ok']}}

@pytest.mark.parametrize('body',[None,[],False,0,'opaque',{'newEnvelope':{'id':'unproven'}}])
def test_unknown_envelope_never_reposts(bundle,tmp_path,body):
 from compute_parity.adapters import RunningHubNative
 t=FakeTransport(bundle);original=t.request;posts=[]
 def request(method,path,**kw):
  if path=='/prompt':posts.append(path);return body
  return original(method,path,**kw)
 t.request=request;t.last_diagnostics={'http_status':200}
 j=Journal(tmp_path/'j.db');a=RunningHubNative(t,j);rid=oid()
 first=a.submit('owner',rid,bundle,files(bundle),quote={'unit':'POINT','amount':10})
 assert first['state']=='submission_uncertain' and first['diagnostics']['response_kind']
 a.submit('owner',rid,bundle,files(bundle),quote={'unit':'POINT','amount':10})
 assert posts==['/prompt'] and j.get('owner',rid)['state']=='submission_uncertain'

@pytest.mark.parametrize('code',['allocation_unavailable','allocation_rejected','allocation_uncertain'])
@pytest.mark.parametrize('released',[False,True])
def test_provisioning_survives_every_sanitizer(tmp_path,code,released):
 from compute_parity.colab import ColabNative
 from scripts.colab_remote.remote_job import sanitize_diagnostics
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'colab','account','dance.anime.chatgpt',rid,{'unit':'CU','amount':10})
 remote={'id':rid,'phase':'cancelled' if released else 'allocation_uncertain','allocation_code':code,
         'allocation_diagnostics':{'http_status':503,'private':'NEVER'},'runtime_id':None,'released':released,'units_observed':0}
 result=ColabNative(None,j)._record('owner',rid,remote)
 expected={'stage':'provisioning','error_code':code,'http_status':503}
 assert result['diagnostics']==expected==sanitize_diagnostics(result['diagnostics'])
 from compute_parity.runtime import Runtime
 assert _public_receipt(Runtime.receipt(result))['diagnostics']==expected
 assert result['state']==('cancelled' if released else 'submission_uncertain')

def test_uncertain_stays_uncertain_during_cleanup(tmp_path):
 from compute_parity.colab import ColabNative
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'colab','account','case',rid,{'unit':'CU','amount':10})
 remote={'id':rid,'phase':'stopping','allocation_code':'allocation_uncertain','runtime_id':None,'released':False}
 assert ColabNative(None,j)._record('owner',rid,remote)['state']=='submission_uncertain'

def test_assigned_runtime_retains_execution_diagnostic(tmp_path):
 from compute_parity.colab import ColabNative
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'colab','account','case',rid,{'unit':'CU','amount':10})
 remote={'id':rid,'phase':'running','allocation_code':'allocation_uncertain','runtime_id':'assigned','diagnostics':{'stage':'execute','error_type':'RuntimeError'}}
 result=ColabNative(None,j)._record('owner',rid,remote)
 assert result['state']=='running' and result['diagnostics']['stage']=='execute'

def test_503_is_still_ambiguous():
 from scripts.colab_allocation_diagnostics import classify
 assert classify(NS(status_code=503))[0]=='allocation_uncertain'
 assert classify(NS(status_code=503,reason_code='NO_ASSIGNMENT'))[0]=='allocation_unavailable'

@pytest.fixture
def evidence(tmp_path):
 path=tmp_path/'colab/state.sqlite3';path.parent.mkdir()
 with sqlite3.connect(path) as db:db.execute('CREATE TABLE jobs(id TEXT,owner TEXT,account_id TEXT,state TEXT)')
 return Journal(tmp_path/'compute-parity/journal.sqlite3'),path

def provision(evidence,at,case='dance.anime.chatgpt',**changes):
 j,path=evidence;rid=oid()
 # Seed durable historical evidence: exercise admission separately, never edit live rows.
 job={'owner':'owner','request_id':rid,'lane':'colab','account':'account','case_id':case,'identity':rid,
      'state':'failed','cost':{'unit':'CU','amount':10},'reserved_amount':10,'actual_cost':{'unit':'CU','amount':0},
      'created_at':at,'artifacts':[],'remote_id':rid,'root_cause':'native_runtime_failed'}
 state={'id':rid,'account_id':'account','phase':'failed','released':True,'units_observed':0,'allocation_attempted_at':at}
 state.update(changes)
 with sqlite3.connect(path) as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?)',(rid,'owner','account',json.dumps(state)))
 with j.db() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',('owner',rid,'colab','account',case,0,'failed',json.dumps(job)))
 return job,state

@pytest.mark.parametrize('damage',[{'runtime_id':'ever-assigned'},{'runtime_ever_assigned':True},{'released':False},{'units_observed':1},{'units_observed':False},{'remote_phase':'execute'},{'upload_index':1},{'artifacts':[{}]},{'phase':'allocation_uncertain'}])
def test_exemption_requires_complete_evidence(evidence,damage):
 job,state=provision(evidence,time.time()-1000,**damage)
 assert not provisioning_only(job,('account',state))

def test_owner_account_cost_and_request_binding(evidence):
 job,state=provision(evidence,time.time()-1000)
 assert provisioning_only(job,('account',state))
 for key,value in [('actual_cost',None),('actual_cost',{'unit':'CU','amount':False}),('remote_id','other'),('lane','runninghub'),('account','other')]:
  assert not provisioning_only({**job,key:value},('account',state))
 j,path=evidence
 with sqlite3.connect(path) as db:db.execute('UPDATE jobs SET owner="other"')
 from compute_parity.provisioning import classify
 assert not classify([job],path,'owner')

def test_legacy_exemption_cap_stop_and_no_rewrite(evidence):
 j,path=evidence
 for i in range(3):provision(evidence,time.time()-3000-i*700)
 before=j.path.read_bytes();colab_before=path.read_bytes()
 j.check_admission('owner','colab','dance.anime.chatgpt')
 assert j.path.read_bytes()==before and path.read_bytes()==colab_before
 assert j.ledger('owner')['colab']['attempts']==3
 # Execution failures in another case still stop the entire lane.
 for i in range(2):provision(evidence,time.time()-1500-i*700,case='dance.anime.claude',runtime_id='assigned')
 with pytest.raises(ParityError) as e:j.check_admission('owner','colab','voice-speak.anime.chatgpt')
 assert e.value.code=='lane_stopped'

@pytest.mark.parametrize('seconds,blocked',[(599,True),(600,False),(601,False)])
def test_ten_minute_boundary_across_cases_and_epochs(evidence,monkeypatch,seconds,blocked):
 j,path=evidence;monkeypatch.setattr('compute_parity.journal.time.time',lambda:100000)
 provision(evidence,100000-seconds)
 if blocked:
  with pytest.raises(ParityError) as e:j.check_admission('owner','colab','voice-speak.anime.chatgpt')
  assert e.value.code=='provisioning_retry_wait'
 else:j.check_admission('owner','colab','voice-speak.anime.chatgpt')

@pytest.mark.parametrize('oldest,blocked',[(86399,True),(86400,False),(86401,False)])
def test_rolling_six_across_cases(evidence,monkeypatch,oldest,blocked):
 j,path=evidence;monkeypatch.setattr('compute_parity.journal.time.time',lambda:100000)
 for i in range(5):provision(evidence,100000-1000-i*700,case=f'case.{i}')
 provision(evidence,100000-oldest,case='old')
 if blocked:
  with pytest.raises(ParityError) as e:j.reserve('owner',oid(),'colab','rotated','new','new',{'unit':'CU','amount':10})
  assert e.value.code=='provisioning_retry_cap'
 else:j.reserve('owner',oid(),'colab','rotated','new','new',{'unit':'CU','amount':10})

def test_unreleased_and_missing_evidence_count_toward_cap(evidence):
 j,path=evidence
 for i in range(3):provision(evidence,time.time()-3000-i*700,released=False)
 with pytest.raises(ParityError) as e:j.check_admission('owner','colab','dance.anime.chatgpt')
 assert e.value.code=='attempt_cap'
 path.unlink()
 with pytest.raises(ParityError) as e:j.check_admission('owner','colab','dance.anime.chatgpt')
 assert e.value.code=='attempt_cap'

def test_runninghub_closure_counts_cap(evidence):
 j,path=evidence
 for i in range(3):
  rid=oid();j.reserve('owner',rid,'runninghub','account','case',rid,{'unit':'POINT','amount':10})
  j.update('owner',rid,state='closed_no_execution_evidence',root_cause=f'cause{i}')
 with pytest.raises(ParityError) as e:j.check_admission('owner','runninghub','case')
 assert e.value.code=='attempt_cap'

def test_poll_scope_node_regressions():
 subprocess.run(['node',str(Path(__file__).with_name('test_compute_r32_poll.cjs'))],check=True)

def test_allocation_time_not_earlier_reservation_controls_spacing(evidence,monkeypatch):
 j,path=evidence;monkeypatch.setattr('compute_parity.journal.time.time',lambda:100000)
 provision(evidence,98000,allocation_attempted_at=99500)
 with pytest.raises(ParityError) as e:j.check_admission('owner','colab','new')
 assert e.value.code=='provisioning_retry_wait'

def test_native_allocation_cannot_auto_retry_outside_admission(evidence):
 from colab_service import ColabService,BridgeError,ColabError
 j,path=evidence;job,state=provision(evidence,time.time()-1000)
 service=object.__new__(ColabService);service.root=path.parent;service.clock=lambda:100000
 service._db_local=__import__('threading').local();service._db_strict=True;service.nested_db_events=0;service.nested_db_sites=set()
 state=service.update(job['request_id'],phase='queued',native_parity=True,deadline_epoch=101000,
                      gpu='A100',allocation_id=oid(),uploads=['pinned'],watchdog_sha256='pinned')
 calls=[]
 def call(state,op,**kw):
  calls.append(op);raise BridgeError('allocation_unavailable',False,{'http_status':503,'reason_code':'NO_ASSIGNMENT'})
 service._call=call;service.tick(state);service.tick(state)
 saved=service.job('owner',job['request_id'])
 assert calls==['runtime_allocate'] and saved['phase']=='failed' and saved['released'] is True
 service.update(job['request_id'],runtime_id='assigned')
 with pytest.raises(ColabError):service.update(job['request_id'],runtime_id=None)
 assert service.job('owner',job['request_id'])['runtime_ever_assigned'] is True

def test_admission_reservation_serializes_sixth_slot(evidence,monkeypatch):
 from concurrent.futures import ThreadPoolExecutor
 j,path=evidence;monkeypatch.setattr('compute_parity.journal.time.time',lambda:100000)
 for i in range(5):provision(evidence,98000-i*700,case=f'case.{i}')
 def reserve(i):
  try:j.reserve('owner',oid(),'colab',f'account{i}',f'new{i}',str(i),{'unit':'CU','amount':10});return 'accepted'
  except ParityError as e:return e.code
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
 assert sorted(results)==['accepted','provisioning_retry_cap']


def test_legacy_multiple_allocations_consume_each_retry_slot(evidence,monkeypatch):
 j,path=evidence;monkeypatch.setattr('compute_parity.journal.time.time',lambda:100000)
 for i in range(3):provision(evidence,98000-i*700,case=f'case.{i}',allocation_attempts=2)
 with pytest.raises(ParityError) as e:j.check_admission('owner','colab','new')
 assert e.value.code=='provisioning_retry_cap'


def test_cancelled_before_assignment_still_qualifies(evidence):
 job,state=provision(evidence,time.time()-1000,allocation_attempted_at=None)
 assert provisioning_only(job,('account',state))

def test_missing_allocation_code_preserves_ambiguity_through_cleanup(tmp_path):
 from compute_parity.colab import ColabNative
 j=Journal(tmp_path/'j.db');rid=oid();j.reserve('owner',rid,'colab','account','case',rid,{'unit':'CU','amount':10})
 adapter=ColabNative(None,j)
 for phase in ('allocation_uncertain','cleanup_pending'):
  result=adapter._record('owner',rid,{'id':rid,'phase':phase,'released':False,'runtime_id':None})
  assert result['state']=='submission_uncertain'
  assert result['diagnostics']=={'stage':'provisioning','error_code':'allocation_uncertain'}
