"""R29 regressions with fake actors, providers, media and clocks; no network."""
from pathlib import Path
from types import SimpleNamespace as NS
import contextlib,copy,json,sys,uuid
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'webui')]
from test_compute_parity_p1 import bundle,files,FakeTransport,oid,no_network
from compute_parity.contracts import ParityError,digest,compile_request
from compute_parity.journal import Journal
from compute_parity.materials import Materials
from compute_parity.local import LocalBaseline
from compute_parity.adapters import AutoIPPublic
from compute_parity.readiness import usable,dependencies_usable
from compute_routing import ComputeRejected
import server


def test_clipmode_catalog_owner_split(tmp_path):
    calls=[]
    def owned(actor,cid):
        calls.append((actor,cid))
        if actor!='owner-actor':raise KeyError(cid)
        return {'id':cid}
    w=NS(owner='owner',user={},_feature=lambda r:None,
        s=NS(character_entry_for_generation=lambda *a:{'id':a[0]},provider_character_studio_entry=lambda x:x),
        h=NS(_provider_runtime_actor=lambda:(NS(store=NS(get_owned_character=owned)),NS(user_id='owner-actor'))))
    rows=[{'case_id':str(i),'owner':'owner','feature':'dance','character_id':('clipmode.character:' if i<6 else 'native:')+str(i),
        'label':str(i),'ready':i>=2,'reason':''} for i in range(13)]
    (tmp_path/'catalog.json').write_text(json.dumps({'cases':rows}))
    m=Materials(w,tmp_path);choices=m.choices('dance')
    assert len(choices)==13 and sum(c['ready'] for c in choices)==11
    assert len(calls)==6 and all(c[0]=='owner-actor' for c in calls)
    w.owner='other';assert m.choices('dance')==[]
    with pytest.raises(ParityError):m.bundle('other',{'featureId':'dance','parity_case_id':'0'})






@pytest.mark.parametrize('charge,expected',[(100,100),('75.5',75.5),(None,None),(0,None),(-1,None),('NaN',None)])
def test_autoip_explicit_charge_or_pending(tmp_path,bundle,charge,expected):
    j=Journal(tmp_path/'j.sqlite3');t=FakeTransport(bundle);rid=oid()
    j.reserve('owner',rid,'autoip',t.fingerprint,bundle['case_id'],'identity',{'unit':'credits','amount':100})
    j.update('owner',rid,state='running',remote_id='remote1')
    def request(method,path,**kw):
        if path.endswith('/balance'):return {'data':{'available_credits':17000}}
        return {'status':'completed','outputs':['artifact'],'credits_charged':charge,'quote':100,'cost':0}
    t.request=request;a=AutoIPPublic(t,j);out=a.poll('owner',rid)
    if expected is None:
        assert 'actual_cost' not in out and out['message']=='已完成、費用待對帳'
        assert out['balance_observation']['value']==17000 and out['balance_observation']['at']>0
        assert j.ledger('owner')['autoip']['unsettled_reserved']==100
    else:
        assert out['actual_cost']=={'unit':'credits','amount':expected}
        assert j.ledger('owner')['autoip']['unsettled_reserved']==0


def test_local_pre_dispatch_rejected_releases_reservation(tmp_path,bundle):
    j=Journal(tmp_path/'j.sqlite3');calls=[]
    def submit(*a):calls.append(1);raise ComputeRejected('No dispatch',code='persona_hash_mismatch')
    adapter=LocalBaseline(NS(submit=submit,recover=None),j,None);rid=oid()
    out=adapter.submit('owner',rid,bundle,files(bundle),request={})
    assert out['state']=='rejected' and out['root_cause']=='persona_hash_mismatch'
    assert adapter.poll('owner',rid)['state']=='rejected' and len(calls)==1
    assert j.get('owner',rid)['reservation_released'] is True
    assert not j.ledger('owner')['local']['unsettled_reserved']


def test_local_unknown_still_uncertain(tmp_path,bundle):
    j=Journal(tmp_path/'j.sqlite3')
    def submit(*a):raise OSError('connection lost')
    adapter=LocalBaseline(NS(submit=submit,recover=None),j,None);rid=oid()
    assert adapter.submit('owner',rid,bundle,files(bundle),request={})['state']=='submission_uncertain'
    assert adapter.poll('owner',rid)['state']=='submission_uncertain'


def test_local_request_binds_server_tuple_and_verifies_persona(monkeypatch,tmp_path):
    checks=[];character={'id':'trusted'}
    def resolve(c,a,f,v):
        assert c==character and (a,f,v)==('voice-speak','voice-speak','voice-speak.persona.v1')
        return {'actionId':a,'variantId':v}
    w=NS(user={},s=NS(character_entry_for_generation=lambda *a:character,resolve_character_capability=resolve,
        apply_character_capability_settings=lambda c,v,s:{**s,'persona':'server-persona'}))
    m=Materials(w,tmp_path);monkeypatch.setattr(m,'read',lambda item:checks.append('original'))
    monkeypatch.setattr('compute_parity.persona.verify_persona',lambda b,p:checks.append(p))
    b={'feature':'voice-speak','request_context':{'material_character':'trusted'},'material_settings':{'persona':'forged'},'inputs':[],'character_original_image':{}}
    req=m.local_request(b,{})
    assert req['characterId']=='trusted' and req['variantId']=='voice-speak.persona.v1'
    assert req['settings']['persona']=='server-persona' and checks==['server-persona','original']


def test_local_request_has_no_fixed_voxcpm_binding(monkeypatch,tmp_path):
    reads=[];character={'id':'trusted'}
    def resolve(*a):raise AssertionError('voxcpm-tts must not resolve a fixed variant')
    w=NS(user={},s=NS(character_entry_for_generation=lambda *a:character,resolve_character_capability=resolve,
        character_capabilities=lambda c:[]))
    m=Materials(w,tmp_path);monkeypatch.setattr(m,'read',lambda item:reads.append(item))
    b={'feature':'voxcpm-tts','request_context':{'material_character':'trusted'},'material_settings':{},'inputs':[],'character_original_image':{}}
    with pytest.raises(ParityError,match='角色功能未啟用'):m.local_request(b,{})
    assert reads==[]


def test_local_request_refuses_voice_selection(monkeypatch,tmp_path):
    reads=[];character={'id':'trusted'}
    def resolve(*a):raise AssertionError('a borrowed voice must be refused before capability lookup')
    w=NS(user={},s=NS(character_entry_for_generation=lambda *a:character,resolve_character_capability=resolve))
    m=Materials(w,tmp_path);monkeypatch.setattr(m,'read',lambda item:reads.append(item))
    b={'feature':'voice-speak','voice_selection':{'character':'other'},'request_context':{'material_character':'trusted'},'material_settings':{},'inputs':[],'character_original_image':{}}
    with pytest.raises(ParityError) as error:m.local_request(b,{})
    assert error.value.code=='voice_selection_unsupported' and reads==[]


def test_persona_changed_bytes_rejected(tmp_path):
    from compute_parity.persona import verify_persona,fingerprint
    (tmp_path/'ref.wav').write_bytes(b'audio');(tmp_path/'breezy_server.py').write_bytes(b'code')
    (tmp_path/'refs.json').write_text(json.dumps({'refs':{'fake':{'wav':'ref.wav','text':'synthetic test'}}}))
    import hashlib
    selected={'variant':'stock','persona':'fake','reference':fingerprint(tmp_path/'ref.wav'),
        'reference_text_sha256':hashlib.sha256(b'synthetic test').hexdigest(),'models':[],'daemon_source':fingerprint(tmp_path/'breezy_server.py')}
    b={'material_settings':{'persona':'fake'},'voice_export':{'persona':'fake'},'models':[],'voice_identity_sha256':digest(selected),
       'persona_verification':{'variant':'stock','service_sources':[]}}
    verify_persona(b,'fake',root=tmp_path)
    (tmp_path/'ref.wav').write_bytes(b'changed')
    with pytest.raises(ParityError,match='聲音已變更'):verify_persona(b,'fake',root=tmp_path)


@pytest.mark.parametrize('reason,status,code',[('GPU_UNAVAILABLE',503,'allocation_unavailable'),('QUOTA_EXCEEDED',403,'allocation_rejected'),('secret',503,'allocation_uncertain'),(None,504,'allocation_uncertain')])
def test_colab_diagnostics_allowlist(reason,status,code):
    from scripts.colab_allocation_diagnostics import classify
    e=Exception('secret-token');e.response=NS(status_code=status,json=lambda:{'error':{'reason':reason,'message':'secret-body','token':'secret'}})
    result,diag=classify(e);assert result==code
    assert 'secret' not in json.dumps(diag) and set(diag)<={'http_status','reason_code'}


def test_colab_one_retry_after_300_same_gpu(tmp_path):
    from colab_service import ColabService,BridgeError
    now=[1000];service=ColabService(tmp_path,bridge=lambda *a:None,clock=lambda:now[0]);rid=oid()
    state={'id':rid,'owner':'owner','account_id':oid(),'phase':'queued','gpu':'A100','deadline_epoch':5000,
        'uploads':['pinned'],'watchdog_sha256':'pinned','allocation_id':oid()}
    with service.db() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(rid,'owner',state['account_id'],'queued',json.dumps(state)))
    calls=[]
    def call(state,op,**kw):
        calls.append((op,kw));raise BridgeError('allocation_unavailable',False,{'reason_code':'GPU_UNAVAILABLE','http_status':503,'token':'private'})
    service._call=call
    service.tick(state);first=service.job('owner',rid);assert first['phase']=='allocation_retry_wait'
    now[0]=1299;service.tick(first);assert len(calls)==1
    now[0]=1300;service.tick(first);last=service.job('owner',rid)
    assert last['phase']=='failed' and last['released'] and len(calls)==2
    assert calls[0][1]['allocation_id']!=calls[1][1]['allocation_id'] and all(c[1]['gpu']=='A100' for c in calls)
    service.tick(last);assert len(calls)==2
    assert 'token' not in json.dumps(last)


def test_hosted_policy_and_official_alias(bundle):
    e={'dependencies_ready':True,'hashes_verified':True,'lifecycle_mode':'provisional_monitored','alias_map_version':'20261111-r29b','alias_evidence_verified':True}
    assert dependencies_usable(e,'runninghub') and not usable(e,'runninghub')
    e['quote']={'amount':10,'unit':'POINT','basis':'provider'};assert usable(e,'runninghub')
    e['lifecycle_mode']='stopped_drain';assert not usable(e,'runninghub')
    assert dependencies_usable({'dependencies_ready':True,'hash_mode':'name_size_provisional','lora_sha_verified':True,'alias_map_version':'20261111-r29b','alias_evidence_verified':True},'comfy_cloud')
    b=copy.deepcopy(bundle);b['graph']['5']={'class_type':'CLIPLoader','inputs':{'clip_name':'text.safetensors'}};b['graph']['3']['class_type']='LoraLoaderModelOnly';b['graph']['3']['inputs']['lora_name']='Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors'
    from test_compute_parity_p1 import seal
    from compute_parity.model_aliases import reviewed_map, SOURCE
    alias=reviewed_map()['runninghub'][SOURCE]
    b['models'].append({'name':SOURCE,'target':'models/loras/'+SOURCE,'sha256':alias['source_sha256'],'bytes':alias['bytes']})
    b['hosted_model_proof']={SOURCE:{'md5':alias['evidence']['md5'],'page_id':alias['evidence']['page_id']}}
    b=seal(b);plan=compile_request(b,'runninghub',request_id=oid())
    assert plan['json']['prompt']['3']['inputs']['lora_name'].endswith('_official.safetensors')
    assert plan['json']['prompt']['3']['class_type']=='LoraLoader' and plan['json']['prompt']['3']['inputs']['strength_clip']==0


def test_monitor_console_test_schedule_and_drain(tmp_path):
    from compute_parity.lifecycle import ensure_monitor,tick,pending_monitor
    from compute_parity.readiness import save,entries
    j=Journal(tmp_path/'j.sqlite3');rid=oid();j.reserve('owner',rid,'runninghub','account','case','id',{'unit':'POINT','amount':10});j.update('owner',rid,state='completed')
    @contextlib.contextmanager
    def db():yield None
    balance=[100];calls=[]
    def test(lane,body):calls.append((lane,body));return {'connections':[{'provider':'runninghub','connection_state':'connected','balances':[{'unit':'POINT','value':balance[0]}]}]}
    c=NS(root=tmp_path,owner='owner',prefs=NS(db=db),_raw=lambda *a:{'credential_version':1,'revision':3},test=test)
    save(tmp_path/'compute-parity',{'owner':'owner','lane':'runninghub','credential_version':1,'bundle_sha256':'b','lifecycle_mode':'provisional_monitored'})
    ensure_monitor(c,j,'owner',rid,now=1000);tick(c,j,now=1000);tick(c,j,now=1299);assert len(calls)==1
    tick(c,j,now=1300);tick(c,j,now=1900);tick(c,j,now=2800);tick(c,j,now=4600)
    assert len(calls)==5 and not pending_monitor(j,'owner')
    assert [o['offset_seconds'] for o in j.get('owner',rid)['lifecycle_monitor']['observations']]==[0,300,900,1800,3600]
    m=j.get('owner',rid)['lifecycle_monitor'];m['state']='pending';m['observations']=m['observations'][:1];j.update('owner',rid,lifecycle_monitor=m)
    balance[0]=99;tick(c,j,now=1300)
    assert entries(tmp_path/'compute-parity')[0]['lifecycle_mode']=='stopped_drain' and pending_monitor(j,'owner')


def test_source_relative_framing_and_one_frame_duration():
    from compute_parity.quality import source_framing,source_duration
    source=[[2,2,0] for _ in range(33)]
    for i in (0,11,12,23,24):source[i]=[.5,.5,1]
    assert source_framing(source,copy.deepcopy(source)) is True  # source mid-thigh crop
    cropped=copy.deepcopy(source);cropped[23]=[.5,1.1,1]
    assert source_framing(source,cropped) is False
    assert source_framing([],source) is None
    assert source_duration(13.033333,13.033333+1/30,30)
    assert not source_duration(13.033333,12.966667,30)


def test_local_durable_receipt_recovery(tmp_path):
    from compute_web import ComputeWeb
    w=object.__new__(ComputeWeb);w.colab=NS(root=tmp_path/'colab')
    calls=[];w._run=lambda *a:(calls.append(a) or {'prompt_id':'product-queue-id'})
    rid=oid();receipt=w.submit_local('owner',rid,{'featureId':'voice-speak'}, {})
    assert w.recover_local('owner',rid)==receipt and len(calls)==1
    assert w.recover_local('another',rid) is None
    assert receipt['downstream_id']=='product-queue-id'


def test_colab_network_loss_keeps_original_id(tmp_path):
    from colab_service import ColabService,BridgeError
    service=ColabService(tmp_path,bridge=lambda *a:None,clock=lambda:1000);rid=oid();allocation=oid()
    state={'id':rid,'owner':'owner','account_id':oid(),'phase':'queued','gpu':'A100','deadline_epoch':5000,
        'uploads':['pinned'],'watchdog_sha256':'pinned','allocation_id':allocation}
    with service.db() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(rid,'owner',state['account_id'],'queued',json.dumps(state)))
    calls=[]
    def call(state,op,**kw):
        calls.append((op,kw))
        if op=='runtime_allocate':raise BridgeError('allocation_uncertain',True,{'http_status':504})
        return {'found':False}
    service._call=call;service.tick(state);state=service.job('owner',rid)
    assert state['phase']=='allocation_uncertain'
    service.tick(state);assert [c[0] for c in calls]==['runtime_allocate','runtime_lookup']
    assert all(c[1]['allocation_id']==allocation for c in calls)


def test_live_inventory_refresh_can_enable_after_favourite(tmp_path,monkeypatch,bundle):
    from compute_parity.hosted_readiness import refresh,PUBLIC_MD5
    from test_compute_parity_p1 import schema,seal
    from compute_parity.transport import Transport
    @contextlib.contextmanager
    def db():yield None
    c=NS(root=tmp_path,owner='owner',prefs=NS(db=db),_raw=lambda *a:{'credential_version':1,'account_type':'NORMAL'})
    bundle['hosted_model_proof']={name:{'md5':md5,'page_id':page} for name,(page,md5) in PUBLIC_MD5.items()};seal(bundle)
    info=schema(compile_request(bundle,'runninghub',request_id=oid())['json']['prompt'])
    t=NS(fingerprint='fake',request=lambda *a,**kw:info)
    monkeypatch.setattr(Transport,'for_owner',lambda *a,**kw:t)
    ready=refresh(c,bundle,'runninghub',{'runninghub_points':10})
    assert ready['can_enable'] and ready['ready']
    bundle['hosted_model_proof'][next(iter(PUBLIC_MD5))]['md5']='0'*32;seal(bundle)
    assert not refresh(c,bundle,'runninghub')['can_enable']


@pytest.mark.parametrize('valid',[True,False])
def test_colab_sdk_post_assignment_response_is_not_no_assignment(tmp_path,valid):
    from scripts.colab_bridge import Bridge,BridgeError
    class Assignment:pass
    class PostAssignmentResponse:
        endpoint='fake-endpoint';runtime_proxy_info=object()
    sdk=NS(Assignment=Assignment,Variant=NS(GPU='GPU'),Accelerator=lambda gpu:gpu)
    bridge=Bridge(tmp_path,sdk);bridge._client=NS(assign=lambda *a,**kw:PostAssignmentResponse() if valid else None)
    saved=[];bridge.save_assignment=lambda *a:saved.append(a)
    request={'allocation_id':oid(),'gpu':'A100'}
    if valid:
        assert bridge.runtime_allocate(request)['found'] and len(saved)==1
    else:
        with pytest.raises(BridgeError) as error:bridge.runtime_allocate(request)
        assert error.value.code=='allocation_uncertain' and error.value.ambiguous and not saved


def test_colab_xssi_error_whitelists_only_reason():
    from scripts.colab_allocation_diagnostics import classify
    def bad():raise ValueError('not plain JSON')
    error=Exception('private');error.response=NS(status_code=503,json=bad)
    error.response_body=")]}'\n"+json.dumps({'error':{'reason':'GPU_UNAVAILABLE','token':'private'}})
    code,diag=classify(error)
    assert code=='allocation_unavailable' and diag=={'http_status':503,'reason_code':'GPU_UNAVAILABLE'}


def test_cloud_import_changes_readiness_but_keeps_provisional_label(tmp_path,monkeypatch,bundle):
    from compute_parity.hosted_readiness import refresh,LORA_SHA
    from test_compute_parity_p1 import schema,seal
    from compute_parity.transport import Transport
    @contextlib.contextmanager
    def db():yield None
    c=NS(root=tmp_path,owner='owner',prefs=NS(db=db),_raw=lambda *a:{'credential_version':1,'account_type':'NORMAL'})
    b=copy.deepcopy(bundle);b['models'].append({'name':'Wan21_I2V_fixture.safetensors','target':'models/loras/test.safetensors','bytes':10,'sha256':LORA_SHA});seal(b)
    info=schema(compile_request(b,'comfy_cloud',request_id=oid())['probe_graph']);imported=[False]
    def request(method,path,**kw):
        if 'object_info' in path:return info
        from urllib.parse import parse_qs,urlsplit
        name=parse_qs(urlsplit(path).query)['name_contains'][0]
        m=next(m for m in b['models'] if m['name']==name)
        return {'assets':[] if name.startswith('Wan21') and not imported[0] else [{'name':name,'size':m['bytes']}]}
    monkeypatch.setattr(Transport,'for_owner',lambda *a,**kw:NS(fingerprint='fake',request=request))
    assert not refresh(c,b,'comfy_cloud')['can_enable']
    imported[0]=True;result=refresh(c,b,'comfy_cloud',{'comfy_cloud_credits':10})
    assert result['can_enable'] and result['ready'] and 'hash 未驗' in result['verification_note']
