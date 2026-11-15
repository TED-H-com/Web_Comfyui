"""Membership-native contracts, using observed shapes and real frozen video inputs."""
import copy,io,json,traceback,urllib.error,uuid
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace as NS
import pytest
from compute_parity.adapters import RunningHubNative
from compute_parity.contracts import ParityError,compile_request
from compute_parity.journal import Journal
from compute_parity.transport import Transport
from compute_parity.runninghub_native import require_membership,no_execution_attempt,exempt_attempts
from test_compute_parity_p1 import FakeTransport
ROOT=Path(__file__).resolve().parents[1]
RID='2120000000000000001'
SUB='0123456789abcdef0123456789abcdef/'
NAME='castle_probe_00001_abcde_1794400000.png'
@pytest.fixture
def frozen(tmp_path):
    from test_compute_parity_p1 import synthetic_material_bundle
    return synthetic_material_bundle(tmp_path)
def setup(tmp,b):
 t=FakeTransport(b);t.info['ResizeImageMaskNode']=json.loads((Path(__file__).parent/'fixtures/r34/runninghub-resize-node.json').read_text());t.rid=RID;j=Journal(tmp/'j.sqlite3');a=RunningHubNative(t,j)
 files={i['slot']:Path(i['local_path']).read_bytes() for i in b['inputs']};rid=str(uuid.uuid4())
 return a,t,j,files,rid
def start(tmp,b):
 a,t,j,files,rid=setup(tmp,b);a.submit('owner',rid,b,files,quote={'unit':'POINT','amount':100})
 return a,t,j,rid
def entry(kind='images',name=NAME,sub=SUB,status=None):
 return {'outputs':{'2':{kind:[{'filename':name,'subfolder':sub,'type':'output'}]}},'status':{'status_str':'success'} if status is None else status}
@pytest.mark.parametrize('kind,name',[('images',NAME),('gifs','castle_video_00001_abcde_1794400000.mp4')])
def test_exact_observed_success_without_completed_or_prompt(frozen,tmp_path,kind,name):
 a,t,j,rid=start(tmp_path,frozen);t.history={RID:entry(kind,name)}
 result=a.poll('owner',rid)
 assert result['state']=='completed' and result['artifacts']==[{'filename':name,'subfolder':SUB,'type':'output'}]
 assert j.get('owner',rid)['remote_id']==RID
 assert len([1 for _,p,_ in t.calls if p=='/prompt'])==1
@pytest.mark.parametrize('status,expected',[({'status_str':'error'},'failed'),({'completed':True},'completed'),({'status_str':'running'},'running'),({},'running'),({'completed':'true'},'running')])
def test_completion_variants(frozen,tmp_path,status,expected):
 a,t,j,rid=start(tmp_path,frozen);t.history={RID:entry(status=status)}
 assert a.poll('owner',rid)['state']==expected

def test_success_requires_outputs(frozen,tmp_path):
 a,t,j,rid=start(tmp_path,frozen);t.history={RID:{'outputs':{},'status':{'status_str':'success'}}}
 with pytest.raises(ParityError,match='沒有成果'):a.poll('owner',rid)
 assert j.get('owner',rid)['state']!='completed'
@pytest.mark.parametrize('sub',['../','/root/','a//','a//b/','a/../','a\\b/','/',None])
def test_trailing_slash_does_not_weaken_path_validation(frozen,tmp_path,sub):
 a,t,j,rid=start(tmp_path,frozen);t.history={RID:entry(sub=sub)}
 with pytest.raises(ParityError,match='路徑'):a.poll('owner',rid)

@pytest.mark.parametrize('kind',['SHARED','NORMAL',None,'OTHER'])
def test_key_gate_before_quote_and_any_submit_post(frozen,tmp_path,monkeypatch,kind):
 from compute_parity.hosted_readiness import refresh, PUBLIC_MD5
 from test_compute_parity_p1 import seal
 frozen['hosted_model_proof']={name:{'page_id':page,'md5':md5} for name,(page,md5) in PUBLIC_MD5.items()}
 seal(frozen)
 a,t,j,files,rid=setup(tmp_path,frozen);t.account_type=kind
 @contextmanager
 def db():yield None
 c=NS(root=tmp_path,owner='owner',prefs=NS(db=db),_raw=lambda *a:{'credential_version':1,'account_type':kind})
 monkeypatch.setattr(Transport,'for_owner',lambda *a,**kw:t)
 if kind=='NORMAL':
  r=refresh(c,frozen,'runninghub',estimates={'runninghub_points':100},persist=False);assert r['ready'] and r['quote']['unit']=='POINT'
  assert a.submit('owner',rid,frozen,files,quote=r['quote'])['state']=='queued'
 else:
  message='企業共享金鑰不接受原生工作' if kind=='SHARED' else '尚未確認 RunningHub 金鑰類型'
  with pytest.raises(ParityError,match=message):refresh(c,frozen,'runninghub',persist=False)
  with pytest.raises(ParityError,match=message):a.submit('owner',rid,frozen,files,quote={'unit':'POINT','amount':100})
  assert t.calls==[] and j.get('owner',rid) is None

def test_v2_rejection_retains_point_observations_and_unknown_bill(frozen,tmp_path):
 a,t,j,files,rid=setup(tmp_path,frozen);values=iter((1000,999.94));a.observe_point=lambda:next(values)
 original=t.request
 def request(m,p,**kw):return {'code':404} if p=='/openapi/v2/query' else original(m,p,**kw)
 t.request=request;a.submit('owner',rid,frozen,files,quote={'unit':'POINT','amount':100});t.history={RID:entry()}
 result=a.poll('owner',rid);job=j.get('owner',rid)
 assert result['state']=='completed' and result.get('actual_cost') is None
 assert job['cost']['unit']=='POINT' and job['pre_submission_point']['value']==1000
 assert job['balance_observation']['value']==999.94 and job['point_balance_delta']==pytest.approx(.06)
 assert job['reserved_amount']==100

class Response(io.BytesIO):
 status=200
 def __init__(self,raw=b'png',declared='3'):super().__init__(raw);self.headers={} if declared is None else {'Content-Length':declared}
def transport():return Transport('runninghub','placeholder-membership-secret',enabled=True)
def artifact():return {'filename':NAME,'subfolder':SUB,'type':'output'}
def test_exact_two_hop_redirect_chain_no_auth_or_persisted_urls():
 t=transport();calls=[]
 locations=['https://www.runninghub.ai/view?Rh-Identify=placeholder-identity&filename='+NAME+'&token=placeholder-token&type=output','https://rh-hk-images.xiaoyaoyou.com/'+SUB+NAME]
 def open(req,**kw):
  calls.append(req);assert req.get_header('Authorization') is None
  if len(calls)<=2:raise urllib.error.HTTPError(req.full_url,302,'private',{'Location':locations[len(calls)-1]},io.BytesIO())
  return Response()
 t._opener=NS(open=open)
 assert t.download_runninghub_output(artifact())==b'png' and len(calls)==3
 assert '/proxy-plus/' in calls[0].full_url and 'subfolder='+SUB.replace('/','%2F') in calls[0].full_url
 assert all('placeholder-token' not in repr(v) for k,v in vars(t).items() if k!='_opener')
 assert 'placeholder-membership-secret' not in repr(t)
@pytest.mark.parametrize('location',['http://www.runninghub.ai/view','https://evil.invalid/x','https://www.runninghub.ai.evil.invalid/x','https://www.runninghub.ai:444/view','https://user:password@www.runninghub.ai/view','https://www.runninghub.ai/view#fragment','https://www.runninghub.ai/\nview'])
def test_redirect_allowlist_rejects_before_next_request(location):
 t=transport();calls=[]
 def open(req,**kw):
  calls.append(req);raise urllib.error.HTTPError(req.full_url,302,'private',{'Location':location},io.BytesIO())
 t._opener=NS(open=open)
 with pytest.raises(ParityError):t.download_runninghub_output(artifact())
 assert len(calls)==1

def test_redirect_limit_and_safe_error_traceback():
 t=transport();calls=[]
 def open(req,**kw):
  calls.append(req);raise urllib.error.HTTPError(req.full_url,302,'placeholder-sensitive-text',{'Location':'https://www.runninghub.ai/view?token=placeholder-sensitive-token'},io.BytesIO())
 t._opener=NS(open=open)
 with pytest.raises(ParityError) as e:t.download_runninghub_output(artifact())
 assert len(calls)==4
 rendered=''.join(traceback.format_exception(e.type,e.value,e.tb))
 assert 'placeholder-sensitive' not in rendered and e.value.__context__ is None
@pytest.mark.parametrize('length',['2','4',None,'-1','536870913','nonnumeric','0'])
def test_download_length_required_and_bounded(length):
 t=transport();t._opener=NS(open=lambda *a,**kw:Response(declared=length))
 with pytest.raises(ParityError,match='大小或長度不符'):t.download_runninghub_output(artifact())

def historical(rid=None):
 return {'owner':'owner','request_id':rid or str(uuid.uuid4()),'lane':'runninghub','case_id':'dance.anime.chatgpt','state':'closed_no_execution_evidence','account':'account-A','root_cause':'no_execution_evidence','artifacts':[],
 'diagnostics':{'http_status':200,'stage':'id_parse','error_code':'empty_response_object','prompt_id_present':False,'transport_error_type':None},
 'closure_evidence':{'queue_absent':True,'history_absent':True,'unexplained_drop':False,'new_submissions':0,'account':'account-A'}}
@pytest.mark.parametrize('shape',['empty_response_object','missing_prompt_id','legacy'])
def test_exemption_from_durable_receipt_shapes(shape):
 j=historical()
 if shape=='legacy':j['diagnostics'].pop('error_code');j['diagnostics'].update(response_keys=[],data_keys=[])
 else:j['diagnostics']['error_code']=shape
 assert no_execution_attempt(j)
@pytest.mark.parametrize('damage',['remote_id','prompt_id','taskId','artifacts','actual_cost','state','http_status','stage','prompt_id_present','error_code','transport_error_type','queue_absent','history_absent','unexplained_drop','new_submissions','account','no_diagnostics'])
def test_missing_or_conflicting_evidence_is_not_exempt(damage):
 j=historical()
 if damage in ('remote_id','prompt_id','taskId'):j[damage]=RID
 elif damage=='artifacts':j[damage]=[artifact()]
 elif damage=='actual_cost':j[damage]={'unit':'POINT','amount':0}
 elif damage=='state':j[damage]='submission_uncertain'
 elif damage=='no_diagnostics':j['diagnostics']={}
 elif damage in ('http_status','stage','prompt_id_present','error_code','transport_error_type'):j['diagnostics'][damage]={'http_status':500,'stage':'upload','prompt_id_present':True,'error_code':'different','transport_error_type':'timeout'}[damage]
 else:j['closure_evidence'][damage]={'queue_absent':False,'history_absent':False,'unexplained_drop':True,'new_submissions':1,'account':'different-account'}[damage]
 assert not no_execution_attempt(j)

def test_cap_and_lane_stop_derive_exemptions_without_rewrite(tmp_path):
 j=Journal(tmp_path/'j.db');ids=[]
 for _ in range(3):
  rid=str(uuid.uuid4());j.reserve('owner',rid,'runninghub','account-A','dance.anime.chatgpt',rid,{'unit':'POINT','amount':10})
  row=historical(rid);row.pop('owner');row.pop('request_id');j.update('owner',rid,**row);ids.append(rid)
 before=[j.get('owner',rid) for rid in ids]
 assert exempt_attempts(before)==set(ids)
 j.check_admission('owner','runninghub','dance.anime.chatgpt')
 assert [j.get('owner',rid) for rid in ids]==before
 assert j.ledger('owner')['runninghub']['unsettled_reserved']==30
