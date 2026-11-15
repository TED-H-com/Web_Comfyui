"""P1 offline contracts and real adapter lifecycle tests; no provider credentials."""
import copy
import hashlib
import json
import logging
from pathlib import Path
import socket
import sys
import uuid
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'webui')]
from compute_parity.contracts import *
from compute_parity.adapters import RunningHubNative,AutoIPPublic,CloudManifestProbe,call
from compute_parity.colab import ColabNative,submit_native
from compute_parity.journal import Journal
from compute_parity.transport import Transport
from scripts.colab_remote.native_schema import validate_native_manifest
from scripts.colab_remote.native_runner import install_model

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a,**kw):raise AssertionError('Offline test attempted network')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket,'create_connection',deny)

@pytest.fixture
def bundle():
    graph={'1':{'class_type':'LoadImage','inputs':{'image':'old.png'}},
        '2':{'class_type':'VHS_LoadVideoPath','inputs':{'video':'/old/private.mp4','force_rate':16,'frame_load_cap':205}},
        '3':{'class_type':'UNETLoader','inputs':{'unet_name':'scail.safetensors','weight_dtype':'default'}},
        '4':{'class_type':'Output','inputs':{'images':['1',0],'video':['2',0],'model':['3',0],'filename_prefix':'old'}}}
    b={'schema_version':1,'case_id':'dance.anime','feature':'dance','graph':graph,'graph_sha256':digest(graph),
       'private_persona':False,'material_settings':{'positive':'Full body dance'},'models':[
           {'name':'scail.safetensors','target':'models/diffusion_models/scail.safetensors','sha256':'b'*64,'bytes':12,'private':False}],
       'inputs':[],'core':{'commit':'a'*40},'node_packs':{},'output_contract':{'kind':'video'}}
    for node,slot,kind,ext in [('1','reference','image','.png'),('2','video','video','.mp4')]:
        data=slot.encode();b['inputs'].append({'node_id':node,'slot':slot,'kind':kind,'field':'image' if slot=='reference' else 'video','name':'input/'+node+ext,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return seal(b)

def seal(b):
    b['graph_sha256']=digest(b['graph']);b.pop('bundle_sha256',None);b['bundle_sha256']=digest(b);return b

def synthetic_material_bundle(tmp_path):
    # Lifecycle tests exercise byte signatures and media probing using generated inputs.
    import io
    import subprocess
    import tempfile
    from PIL import Image
    if not hasattr(synthetic_material_bundle, '_media'):
        image = io.BytesIO()
        Image.new('RGB', (32, 32), 'black').save(image, format='PNG')
        with tempfile.TemporaryDirectory(prefix='synthetic-video-') as directory:
            video = Path(directory) / 'input.mp4'
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i',
                            'color=c=black:s=32x32:r=2', '-t', '1', '-c:v', 'mpeg4',
                            '-y', str(video)], check=True)
            synthetic_material_bundle._media = {'reference': image.getvalue(), 'video': video.read_bytes()}
    b = bundle.__wrapped__()
    b['case_id'] = 'dance.anime.chatgpt'
    for item in b['inputs']:
        raw = synthetic_material_bundle._media[item['slot']]
        path = tmp_path / ('fixture-' + Path(item['name']).name)
        path.write_bytes(raw)
        item.update(local_path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    seal(b)
    catalog = tmp_path / 'compute-parity' / 'bundles-r29'
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / (b['case_id'] + '.json')).write_text(json.dumps(b))
    return b

def voice(b,private=False):
    b=copy.deepcopy(b);b.update(feature='voice-speak',case_id='voice-speak.shared' if private else 'voice-speak.library',private_persona=private)
    if private:
        b['graph']={'1':{'class_type':'CastleBreezyVoiceTTS','inputs':{'persona':'custom','text':'hello'}}}
        b['inputs']=[{'node_id':None,'slot':'audio','kind':'audio','field':None,'name':'input/1.wav','bytes':5,'sha256':hashlib.sha256(b'audio').hexdigest()}]
        b['voice_export']={'persona':'custom','reference_name':'input/1.wav','reference_text':'reference','text':'hello','weight_target':None}

    else:
        data=b'audio';b['graph']={'1':{'class_type':'LoadAudio','inputs':{'audio':'original.wav'}},'2':{'class_type':'CastleBreezyVoiceClone','inputs':{'ref_audio':['1',0],'ref_text':'reference','text':'hello'}}}
        b['inputs']=[{'node_id':'1','slot':'audio','kind':'audio','field':'audio','name':'input/1.wav','bytes':5,'sha256':hashlib.sha256(data).hexdigest()}]
    return seal(b)

def files(b):return {i['slot']:i['slot'].encode() for i in b['inputs']}
def oid():return str(uuid.uuid4())
def schema(graph):
    result={}
    for n in graph.values():
        required={k:([v] if k in MODEL_FIELDS else 'STRING',{}) for k,v in n['inputs'].items()}
        result[n['class_type']]={'input':{'required':required}}
    return result

class FakeTransport:
    account_type="NORMAL"
    fingerprint='account-A'
    def __init__(self,b):
        self.calls=[];self.graph=compile_request(b,'runninghub',request_id=oid(),voice_consent=True)['json']['prompt'];self.info=schema(self.graph)
        self.timeout=False;self.rid='remote-1';self.history={};self.queue={'queue_running':[],'queue_pending':[]};self.point=12
        self.pricing={'data':{'character_dance':100,'voice_clone':20}};self.balance={'data':{'balance':1000}};self.status={'status':'queued'}
    def request(self,method,path,**kw):
        self.calls.append((method,path,kw))
        if path=='/object_info' or path=='/api/object_info':return self.info
        if path=='/upload/image':return {'name':kw['files']['image'][0],'subfolder':'owned','type':'input'}
        if path=='/prompt':
            if self.timeout:raise TimeoutError('SECRET https://www.runninghub.ai/proxy-plus/SECRET/prompt')
            self.prompt=kw['body'];return {'prompt_id':self.rid}
        if path=='/history':return self.history
        if path=='/queue' and method=='GET':return self.queue
        if path.startswith('/history/'):return self.history
        if path in {'/interrupt','/queue'}:return {}
        if path.startswith('/view?'):return b'video-result'
        if path=='/openapi/v2/query':return {'usage':{'chargedPoint':self.point,'consumeCoins':9999}}
        if path=='/api/v1/pricing':return self.pricing
        if path=='/api/v1/credits/balance':return self.balance
        if path in {'/api/v1/videos/character-dance','/api/v1/voice/clone'}:
            if self.timeout:raise TimeoutError('SECRET')
            return {'task_uuid':self.rid}
        if path.endswith('/outputs/0'):return b'audio-or-video'
        if path.startswith('/api/v1/tasks/'):return self.status
        raise AssertionError((method,path))
    def download_runninghub_output(self,artifact):return b'video-result'
    def finish(self):self.history={self.rid:{'status':{'completed':True},'outputs':{'25':{'gifs':[{'filename':'movie.mp4','type':'output','subfolder':'parity'}]}}}}

@pytest.mark.parametrize('lane',['local','colab','runninghub','autoip','comfy_cloud'])
def test_compilers(bundle,lane):
    p=compile_request(bundle,lane,request_id=oid())
    assert p['lane']==lane
    if lane=='runninghub':
        assert p['json']['prompt']['2']['class_type']=='VHS_LoadVideo'
        assert p['json']['prompt']['2']['inputs']['frame_load_cap']==205
        assert p['capacity_gb']==48
    if lane=='colab':assert p['gpu']=='A100' and p['cache'].endswith(bundle['bundle_sha256'])
    if lane=='autoip':assert p['fields']['mode']=='animation' and 'seed' in p['differences']
    assert bundle['graph']['2']['class_type']=='VHS_LoadVideoPath'

@pytest.mark.parametrize('lane',['colab','runninghub','autoip','comfy_cloud'])
@pytest.mark.parametrize('consent',[False,True,'true',1])
def test_private_voice_needs_consent(bundle,lane,consent):
    b=voice(bundle,True)
    if consent is not True:
        with pytest.raises(ParityError,match='明確同意'):compile_request(b,lane,request_id=oid(),voice_consent=consent)
    else:
        assert compile_request(b,lane,request_id=oid(),voice_consent=True)['lane']==lane

@pytest.mark.parametrize('lane',['colab','runninghub','autoip'])
@pytest.mark.parametrize('consent',[False,'true',1,None])
def test_voice_requires_literal_per_run_true(bundle,lane,consent):
    with pytest.raises(ParityError,match='明確同意'):compile_request(voice(bundle),lane,request_id=oid(),voice_consent=consent)

@pytest.mark.parametrize('lane',['colab','runninghub','autoip'])
def test_portable_voice_compiles(bundle,lane):
    p=compile_request(voice(bundle),lane,request_id=oid(),voice_consent=True)
    if lane=='autoip':assert p['fields']['dialect_rewrite']=='0' and 'reference_voice_id' not in p['fields']

def test_unknown_node_and_model(bundle):
    p=compile_request(bundle,'runninghub',request_id=oid());info=schema(p['json']['prompt'])
    info.pop('LoadImage');info['UNETLoader']['input']['required']['unet_name']=(['different'],{})
    r=dependency_probe(p['json']['prompt'],info)
    assert not r['ready'] and r['missing_nodes']==['LoadImage'] and r['missing_models']==['scail.safetensors']
    assert '收藏' in r['reason'] and not r['hashes_verified']

def test_rh_lifecycle_and_correct_unit(bundle,tmp_path):
    t=FakeTransport(bundle);a=RunningHubNative(t,Journal(tmp_path/'jobs.db'));rid=oid()
    assert a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})['state']=='queued'
    assert a.poll('a',rid)['state']=='queued'
    t.finish();result=a.poll('a',rid)
    assert result['state']=='completed' and result['actual_cost']=={'unit':'POINT','amount':12}
    assert a.download('a',rid)==b'video-result'
    assert a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})['state']=='completed'
    assert sum(p=='/prompt' for _,p,_ in t.calls)==1
    assert all('workflowId' not in str(kw) for _,_,kw in t.calls)

def test_rh_unknown_recovery_no_resubmit(bundle,tmp_path):
    t=FakeTransport(bundle);t.timeout=True;j=Journal(tmp_path/'jobs.db');a=RunningHubNative(t,j);rid=oid()
    assert a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})['state']=='submission_uncertain'
    a=RunningHubNative(t,Journal(tmp_path/'jobs.db'))
    assert a.recover('a',rid)['state']=='submission_uncertain'
    a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})
    t.queue['queue_pending']=[[0,t.rid,{}, {'castle_request_id':rid}]]
    assert a.recover('a',rid)['remote_id']==t.rid
    assert sum(p=='/prompt' for _,p,_ in t.calls)==1
    assert 'SECRET' not in (tmp_path/'jobs.db').read_bytes().decode('utf8','ignore')

@pytest.mark.parametrize('where',['queue_running','queue_pending'])
def test_rh_cancel_scoped(bundle,tmp_path,where):
    t=FakeTransport(bundle);a=RunningHubNative(t,Journal(tmp_path/'j.db'));rid=oid()
    a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})
    t.queue[where]=[[0,t.rid,{},{}]]
    assert a.cancel('a',rid)['state']=='cancelling'
    assert t.calls[-1][1]==('/interrupt' if where=='queue_running' else '/queue')

def test_rh_cancel_does_not_interrupt_other_work(bundle,tmp_path):
    t=FakeTransport(bundle);a=RunningHubNative(t,Journal(tmp_path/'j.db'));rid=oid()
    a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})
    t.queue['queue_running']=[[0,t.rid],[1,'other']]
    with pytest.raises(ParityError):a.cancel('a',rid)
    assert not any(p=='/interrupt' for _,p,_ in t.calls)

@pytest.mark.parametrize('q,cap',[({'unit':'coins','amount':1},2000),({'unit':'POINT','amount':float('nan')},2000)])
def test_rh_budget_refused_before_io(bundle,tmp_path,q,cap):
    t=FakeTransport(bundle);a=RunningHubNative(t,Journal(tmp_path/'j.db'))
    with pytest.raises(ParityError):a.submit('a',oid(),bundle,files(bundle),quote=q,cap=cap)
    assert not t.calls

def test_missing_dependencies_before_upload(bundle,tmp_path):
    t=FakeTransport(bundle);t.info={};a=RunningHubNative(t,Journal(tmp_path/'j.db'))
    result=a.submit('a',oid(),bundle,files(bundle),quote={'unit':'POINT','amount':100})
    assert result['state']=='rejected' and '收藏' in result['message']
    assert [p for _,p,_ in t.calls]==['/object_info']

def test_journal_no_fixed_cap_three_attempts_and_owner_scope(tmp_path):
    j=Journal(tmp_path/'j.db');q={'unit':'POINT','amount':50000}
    for n in range(3):
        rid=oid();j.reserve('a',rid,'runninghub','acct','case',rid,q)
        with pytest.raises(ParityError,match='帳號已有'):j.reserve('a',oid(),'runninghub','acct','other','id',q)
        j.update('a',rid,state='completed',actual_cost={'unit':'POINT','amount':n+1})
    with pytest.raises(ParityError,match='最多三次'):j.reserve('a',oid(),'runninghub','new-account','case','new',q)
    j.reserve('b',oid(),'runninghub','other-account','case','new',q)
    assert j.ledger('a')['runninghub']=={'unit':'POINT','actual':6,'unsettled_reserved':0,'attempts':3}

def test_stop_lane_two_consecutive_root_causes(tmp_path):
    j=Journal(tmp_path/'j.db');q={'unit':'credits','amount':3}
    for case in ('a','b'):
        rid=oid();j.reserve('a',rid,'autoip','acct',case,rid,q);j.update('a',rid,state='failed',root_cause='missing_model')
    with pytest.raises(ParityError,match='相同原因'):j.reserve('a',oid(),'autoip','acct','c','new',q)
    j.reserve('other-owner',oid(),'autoip','other','c','new',q)
    j.reserve('a',oid(),'runninghub','rh','c','new',{'unit':'POINT','amount':3})

def test_account_key_rotation_rejects_old_job(bundle,tmp_path):
    t=FakeTransport(bundle);a=RunningHubNative(t,Journal(tmp_path/'j.db'));rid=oid()
    a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})
    t.fingerprint='account-B'
    with pytest.raises(ParityError):a.poll('a',rid)
    with pytest.raises(ParityError):a.submit('a',rid,bundle,files(bundle),quote={'unit':'POINT','amount':100})
    with pytest.raises(ParityError):a.poll('another-owner',rid)

@pytest.mark.parametrize('feature',['dance','video-replace','voice-speak'])
def test_autoip_lifecycle_quote_and_files(bundle,tmp_path,feature,monkeypatch):
    from compute_parity import autoip_duration
    bundle['output_contract']['frames']=391;seal(bundle)
    monkeypatch.setattr(autoip_duration,'prepare',lambda raw,n:(raw,{'frames':n}))
    monkeypatch.setattr(autoip_duration,'trim',lambda raw,c:raw)
    b=voice(bundle) if feature=='voice-speak' else copy.deepcopy(bundle);b['feature']=feature;seal(b)
    t=FakeTransport(b);a=AutoIPPublic(t,Journal(tmp_path/'j.db'));rid=oid()
    result=a.submit('a',rid,b,files(b),voice_consent=True)
    assert result['state']=='queued' and result['cost']['unit']=='credits'
    assert [p for _,p,_ in t.calls[:2]]==['/api/v1/pricing','/api/v1/credits/balance']
    if feature=='video-replace':assert t.calls[-1][2]['fields']['mode']=='replacement'
    assert a.poll('a',rid)['state']=='queued'
    assert '未公開取消' in a.cancel('a',rid)['message']
    t.status={'status':'completed','outputs':[{'url':'ignored-untrusted-url'}]}
    assert a.poll('a',rid)['state']=='completed'
    assert a.download('a',rid)==b'audio-or-video'

def test_autoip_unknown_never_resubmits(bundle,tmp_path,monkeypatch):
    from compute_parity import autoip_duration
    bundle['output_contract']['frames']=391;seal(bundle)
    monkeypatch.setattr(autoip_duration,'prepare',lambda raw,n:(raw,{'frames':n}))
    t=FakeTransport(bundle);t.timeout=True;a=AutoIPPublic(t,Journal(tmp_path/'j.db'));rid=oid()
    a.submit('a',rid,bundle,files(bundle));a.submit('a',rid,bundle,files(bundle));a.recover('a',rid)
    assert sum(p=='/api/v1/videos/character-dance' for _,p,_ in t.calls)==1

@pytest.mark.parametrize('price,cap',[({'unit':'coins','credits':1},1000),(None,1000),(-2,1000),(True,1000)])
def test_autoip_quote_refusal(bundle,tmp_path,price,cap):
    t=FakeTransport(bundle);t.pricing={'data':{'character_dance':price}};a=AutoIPPublic(t,Journal(tmp_path/'j.db'))
    with pytest.raises(ParityError):a.submit('a',oid(),bundle,files(bundle),cap=cap)
    assert all(m=='GET' for m,_,_ in t.calls)

def test_all_adapter_voice_gates_before_io(bundle,tmp_path):
    b=voice(bundle);t=FakeTransport(b)
    for cls in [RunningHubNative,AutoIPPublic]:
        a=cls(t,Journal(tmp_path/(cls.__name__+'.db')))
        with pytest.raises(ParityError):a.submit('a',oid(),b,files(b),quote={'unit':'POINT','amount':1})
    assert not t.calls

def test_cloud_probe_only_never_sends_voice(bundle):
    b=voice(bundle);t=FakeTransport(b);result=CloudManifestProbe(t).probe(b)
    assert not result['submit_allowed']
    assert t.calls==[('GET','/api/object_info',{})]

def test_transport_disabled_and_no_key_in_errors_or_logs(caplog):
    secret='MY_SECRET_KEY_123';t=Transport('runninghub',secret)
    assert secret not in repr(t)
    assert secret not in t.scrub('https://www.runninghub.ai/proxy-plus/'+secret+'/prompt')
    with pytest.raises(ParityError):t.request('GET','/object_info')
    class Bad:
        def request(self,*a,**kw):raise RuntimeError('https://www.runninghub.ai/proxy-plus/'+secret+'/prompt')
    with pytest.raises(ParityError) as e:call(Bad(),'GET','/object_info')
    assert secret not in str(e.value) and e.value.__context__ is None and secret not in caplog.text
    t.enabled=True
    with pytest.raises(ParityError) as e:t.request('GET','/object_info')
    assert e.value.__context__ is None and secret not in str(e.value)

def test_native_schema_hashes_and_manifest(bundle,tmp_path):
    manifest={'schema_version':1,'job_id':oid(),'deadline_epoch':1,'jobs':[{'engine':'castle_native_p1','type':'video','bundle':bundle}],
      'input_files':[{'path':i['name'],'size':i['bytes'],'sha256':i['sha256']} for i in bundle['inputs']]}
    for i in bundle['inputs']:
        p=tmp_path/i['name'];p.parent.mkdir(exist_ok=True);p.write_bytes(i['slot'].encode())
    assert validate_native_manifest(manifest,tmp_path)==manifest
    (tmp_path/bundle['inputs'][0]['name']).write_bytes(b'bad')
    with pytest.raises(ValueError):validate_native_manifest(manifest,tmp_path)

def test_model_cache_hash_validation_and_repair(tmp_path):
    item={'target':'models/a','sha256':hashlib.sha256(b'abc').hexdigest(),'bytes':3,'url':'https://huggingface.co/a/resolve/rev/file'}
    calls=[]
    def fetch(url,p):calls.append(url);p.write_bytes(b'abc')
    p=install_model(item,tmp_path,fetch);assert p.read_bytes()==b'abc'
    install_model(item,tmp_path,fetch);assert len(calls)==1
    p.write_bytes(b'bad');install_model(item,tmp_path,fetch);assert len(calls)==2
    p.write_bytes(b'bad')
    with pytest.raises(ValueError):install_model(item,tmp_path,lambda u,p:p.write_bytes(b'xxx'))
    assert not list(tmp_path.rglob('*.part'))

def test_bundle_tamper_and_graph_cycle(bundle):
    bundle['graph']['3']['inputs']['unet_name']='different'
    with pytest.raises(ParityError):validate_bundle(bundle)
    graph={'1':{'class_type':'X','inputs':{'input':['1',0]}}}
    with pytest.raises(ParityError):validate_graph(graph)

def test_colab_server_hook_uses_original_lifecycle(bundle,tmp_path,monkeypatch):
    import colab_service
    class Bridge:
        installed=True
        def __call__(self,op,directory,**kw):
            if op=='account_identity':return {'bound':True,'subject_key':'a'*64}
            if op=='account_status':return {'authenticated':True,'balance_compute_units':100}
            raise AssertionError(op)
    service=colab_service.ColabService(tmp_path/'colab',bridge=Bridge())
    account=service.create_account('a','fake')
    monkeypatch.setattr(service,'start',lambda:None)
    monkeypatch.setattr(colab_service,'validate_media',lambda p,**kw:{'kind':'video'})
    import colab_inputs
    monkeypatch.setattr(colab_inputs,'input_name',lambda name,raw,kind:name)
    import base64
    body={'request_id':oid(),'account_id':account['id'],'gpu':'A100','max_units':20,'max_minutes':60,
        'files':[{'name':Path(i['name']).name,'data':base64.b64encode(i['slot'].encode()).decode()} for i in bundle['inputs']]}
    result=submit_native(service,'a',body,bundle)
    assert result['phase']=='queued'
    state=service.job('a',body['request_id'])
    assert state['watchdog_sha256'] and state['uploads'] and state['allocation_id']
    assert any(u['path'].endswith('native_runner.py') for u in state['uploads'])
    assert submit_native(service,'a',body,bundle)['id']==result['id']
    service.cancel('a',result['id']);service.tick(service.job('a',result['id']))
    assert service.job('a',result['id'])['phase']=='cancelled'
    assert service.job('a',result['id'])['released'] is True

def test_colab_consent_before_auth(bundle):
    class NoService:
        def __getattr__(self,n):raise AssertionError('Must not use auth before gate')
    with pytest.raises(ParityError):submit_native(NoService(),'a',{},voice(bundle,True),voice_consent=False)


def test_owner_consent_is_not_role_or_client_truthiness(tmp_path):
    from compute_parity.consent import VoiceConsent
    consent=VoiceConsent(tmp_path/'consent.db');consent.record('owner',True,'explicit owner decision')
    assert consent.permits('owner')
    assert not consent.permits('other') and not consent.permits('other','true')
    assert consent.permits('other',True)
    consent.record('owner',False,'revoked');assert not consent.permits('owner')

def test_private_model_frozen_and_changed_rejected(tmp_path):
    from compute_parity.private_assets import freeze_private_models
    p=tmp_path/'model.pt';p.write_bytes(b'private-weight')
    b={'models':[{'private':True,'path':str(p),'target':'private/persona.pt','bytes':14,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}
    uploads=freeze_private_models(b,tmp_path/'job')
    assert uploads[0]['path']=='private-models/private/persona.pt.parts/0'
    assert (tmp_path/'job'/uploads[0]['path']).stat().st_mode & 0o777 == 0o600
    from scripts.colab_remote.native_runner import assemble_private_model
    recovered=assemble_private_model(tmp_path/'job',tmp_path/'cache',b['models'][0])
    assert recovered.read_bytes()==p.read_bytes()
    p.write_bytes(b'changed')
    with pytest.raises(ParityError):freeze_private_models(b,tmp_path/'changed')

def test_cloud_lifecycle_and_unknown_no_retry(bundle,tmp_path):
    from compute_parity.adapters import CloudNative
    class Cloud(FakeTransport):
        def download_output(self,link,**kw):return b'video'
        def request(self,method,path,**kw):
            self.calls.append((method,path,kw))
            if path=='/api/object_info':return schema(compile_request(bundle,'comfy_cloud',request_id=oid())['probe_graph'])
            if path=='/api/v2/assets':return {'id':'asset-owned'}
            if path=='/api/v2/jobs':
                if self.timeout:raise TimeoutError('secret')
                self.submitted=kw;return {'id':'00000000-0000-4000-8000-00000000c001','urls':{'self':'/api/v2/jobs/00000000-0000-4000-8000-00000000c001','cancel':'/api/v2/jobs/00000000-0000-4000-8000-00000000c001/cancel'}}
            if path=='/api/v2/jobs/00000000-0000-4000-8000-00000000c001':return self.status
            if path.endswith('/cancel'):return {}
            if path=='/api/v2/assets/output/content':return b'video'
            if path=='/api/jobs?limit=100':return {'jobs':[]}
            raise AssertionError(path)
    t=Cloud(bundle);a=CloudNative(t,Journal(tmp_path/'cloud.db'));rid=oid()
    assert a.submit('a',rid,bundle,files(bundle),quote={'unit':'credits','amount':3000})['state']=='queued'
    assert t.submitted['headers']['Idempotency-Key']==rid
    assert t.submitted['body']['workflow']['1']['inputs']['image']['__type']=='core/ASSET'
    assert a.poll('a',rid)['state']=='queued'
    assert a.cancel('a',rid)['state']=='cancelling'
    t.status={'status':'succeeded','outputs':[{'url':'/api/v2/assets/output/content','size_bytes':5}]}
    assert a.poll('a',rid)['state']=='completed';assert a.download('a',rid)==b'video'
    a.submit('a',rid,bundle,files(bundle),quote={'unit':'credits','amount':3000})
    assert sum(p=='/api/v2/jobs' for _,p,_ in t.calls)==1
    t.timeout=True;rid=oid();a.submit('a',rid,bundle,files(bundle),quote={'unit':'credits','amount':2})
    a.recover('a',rid);a.submit('a',rid,bundle,files(bundle),quote={'unit':'credits','amount':2})
    assert sum(p=='/api/v2/jobs' for _,p,_ in t.calls)==2
    with pytest.raises(ParityError):a.poll('other',rid)

def test_cloud_voice_blocked_before_io(bundle,tmp_path):
    from compute_parity.adapters import CloudNative
    b=voice(bundle);t=FakeTransport(b);a=CloudNative(t,Journal(tmp_path/'cloud.db'))
    with pytest.raises(ParityError,match='Build'):a.submit('a',oid(),b,files(b),quote={'unit':'credits','amount':2},voice_consent=True)
    assert not t.calls

def test_colab_adapter_full_lifecycle_and_uncertain_recovery(bundle,tmp_path):
    from contextlib import nullcontext
    class Service:
        def __init__(self):self.calls=[];self.jobs={};self.timeout=False
        def account(self,owner,account):
            assert owner=='a' and account=='owned'
        def account_guard(self,account,**kw):return nullcontext()
        def native_submission_guard(self,owner,rid):return nullcontext()
        def claim_request(self,owner,rid):pass  # fake store; durable SQL covered by test_review_r37
        def find_job(self,owner,rid):return self.jobs.get(rid)
        def _submit(self,owner,body,_native):
            self.calls.append(body['request_id']);rid=body['request_id']
            self.jobs[rid]={'id':rid,'phase':'queued','artifacts':[]}
            if self.timeout:raise TimeoutError('private-url')
            return self.jobs[rid]
        def job(self,owner,rid):
            assert owner=='a';return self.jobs[rid]
        def public_job(self,job):return job
        def cancel(self,owner,rid):self.jobs[rid].update(phase='cancelled',released=True,units_observed=2)
        def artifact(self,owner,rid,name):return b'output'
    service=Service();a=ColabNative(service,Journal(tmp_path/'colab.db'));rid=oid()
    q={'unit':'CU','amount':800}
    assert a.submit('a',rid,bundle,files(bundle),account_id='owned',quote=q)['state']=='running'
    assert a.poll('a',rid)['state']=='running'
    service.jobs[rid].update(phase='completed',released=True,units_observed=3,artifacts=[{'name':'a.mp4'}])
    assert a.poll('a',rid)['actual_cost']=={'unit':'CU','amount':3}
    assert a.download('a',rid,'a.mp4')==b'output'
    service.timeout=True;rid2=oid()
    assert a.submit('a',rid2,bundle,files(bundle),account_id='owned',quote=q)['state']=='running'
    a.submit('a',rid2,bundle,files(bundle),account_id='owned',quote=q)
    assert service.calls.count(rid2)==1
    assert a.cancel('a',rid2)['state']=='cancelled'
    assert a.journal.ledger('a')['colab']['actual']==5

def test_registration_local_delegates_original_lock_and_remote_off(bundle):
    from compute_routing import ComputeAdapter,ComputeRejected
    from compute_parity.registration import register
    calls=[]
    def submit(*args):calls.append(args);return {'state':'queued','downstream_id':'original-lock'}
    old=ComputeAdapter(lambda *a:{'eligible':True},submit,None,None,None)
    web=SimpleNamespace(s=SimpleNamespace(),router=SimpleNamespace(adapters={'local':old,'runninghub':old}))
    register(web);request={'featureId':'dance'}
    assert web.router.adapters['local'].submit('a','id',request,{})['downstream_id']=='original-lock'
    with pytest.raises(ComputeRejected):web.router.adapters['runninghub'].submit('a','id',request,{})
    assert len(calls)==1

def test_owner_credentials_only_use_console_reference(tmp_path):
    from contextlib import nullcontext
    from compute_parity.owner_credentials import reference_for
    from provider_fabric.config import SecretReference,SecretKind
    root=tmp_path/'vault';ref=SecretReference(kind=SecretKind.FILE,locator=str(root/'runninghub/acct/api-key.secret'))
    console=SimpleNamespace(owner='a',actor=SimpleNamespace(user_id='actor-a'),prefs=SimpleNamespace(db=lambda:nullcontext(None)),runtime=SimpleNamespace(config=SimpleNamespace(credential_root=root)))
    data={'actor_id':'actor-a','credential_present':True,'account_id':'acct','credential_ref':{'kind':'file','locator':ref.locator}}
    console._raw=lambda lane,db:data
    assert reference_for(console,'a','runninghub')==ref
    with pytest.raises(ParityError):reference_for(console,'b','runninghub')
    data['actor_id']='other'
    with pytest.raises(ParityError):reference_for(console,'a','runninghub')
    data['actor_id']='actor-a';data['credential_ref']['locator']=str(tmp_path/'secret')
    with pytest.raises(ParityError):reference_for(console,'a','runninghub')

def test_cloud_foreign_links_rejected():
    from compute_parity.adapters import CloudNative
    for value in ['https://other.example/api/v2/jobs/a','//other.example/api/v2/jobs/a','/prompt']:
        with pytest.raises(ParityError):CloudNative._link(value)


def test_v3_dynamic_fields_not_false_missing():
    info={'Resize':{'input':{'required':{'resize':['COMFY_DYNAMICCOMBO_V3',{'options':[{'key':'pixels','inputs':{'required':{'megapixels':['FLOAT',{}]}}}]}]}}},
          'Batch':{'input':{'required':{'images':['COMFY_AUTOGROW_V3',{'template':{'prefix':'image','min':1,'max':2}}]}}}}
    graph={'1':{'class_type':'Resize','inputs':{'resize':'pixels','resize.megapixels':1}},
           '2':{'class_type':'Batch','inputs':{'images.image0':['1',0],'images.image1':['1',0]}}}
    assert dependency_probe(graph,info)['ready']
    graph['1']['inputs']['resize']='other';graph['2']['inputs']['images.image2']=['1',0]
    assert dependency_probe(graph,info)['missing_inputs']==['Batch.images','Batch.images.image2','Resize.resize','Resize.resize.megapixels']


def test_runninghub_voice_requires_model_execution_contract(bundle,tmp_path):
    b=voice(bundle);t=FakeTransport(b);a=RunningHubNative(t,Journal(tmp_path/'rh.db'))
    with pytest.raises(ParityError,match='綁定契約'):a.submit('a',oid(),b,files(b),quote={'unit':'POINT','amount':1},voice_consent=True)
    assert not t.calls


def test_local_campaign_preserves_product_admission_and_three_attempts(bundle,tmp_path):
    from compute_parity.local import LocalBaseline
    calls=[];state={'state':'queued','downstream_id':'locked-local'}
    def submit(*args):calls.append(args);return dict(state)
    delegate=SimpleNamespace(submit=submit,status=lambda *a:dict(state),recover=lambda *a:dict(state),cancel=lambda *a:{'state':'cancelled'})
    a=LocalBaseline(delegate,Journal(tmp_path/'local.db'),lambda *a:b'local-output')
    rid=oid();assert a.submit('a',rid,bundle,files(bundle),request={'featureId':'dance'})['state']=='queued'
    a.submit('a',rid,bundle,files(bundle),request={'featureId':'dance'});assert len(calls)==1
    assert a.cancel('a',rid)['state']=='cancelled'
    state['state']='completed';rid=oid();a.submit('a',rid,bundle,files(bundle),request={'featureId':'dance'})
    assert a.download('a',rid)==b'local-output'
    assert a.journal.ledger('a')['local']['actual']==0
    with pytest.raises(ParityError):a.download('b',rid)


def test_persona_missing_export_refused_before_remote_preparation(bundle):
    b=voice(bundle,True);b.pop('voice_export');seal(b)
    for lane in ('colab','autoip','runninghub','comfy_cloud'):
        with pytest.raises(ParityError,match='尚未封裝'):compile_request(b,lane,request_id=oid(),voice_consent=True)


def test_cloud_cdn_no_auth_and_signed_url_not_public(bundle,tmp_path):
    from compute_parity.adapters import CloudNative,public
    t=FakeTransport(bundle);a=CloudNative(t,Journal(tmp_path/'cdn.db'));rid=oid()
    j,_=a.journal.reserve('a',rid,'comfy_cloud',t.fingerprint,'case','identity',{'unit':'credits','amount':1})
    url='https://storage.googleapis.com/owned/output.mp4?signature=synthetic-signature'
    a.journal.update('a',rid,state='completed',artifacts=[{'index':0}],output_links=[url],output_metadata=[{'size_bytes':10}])
    t.download_output=lambda link,**kw:b'cdn-output'
    assert a.download('a',rid)==b'cdn-output'
    assert 'synthetic-signature' not in str(public(a.journal.get('a',rid)))
    for bad in ['https://127.0.0.1/api/v2/data','https://evil.test/a','https://evil@storage.googleapis.com/a']:
        with pytest.raises(ParityError):a._output_link(bad)
    transport=Transport('comfy_cloud','synthetic-provider-key',enabled=True)
    class Response:
        headers={}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return b'download'
    class Opener:
        def open(self,request,**kw):
            assert not request.get_header('Authorization') and not request.get_header('X-api-key')
            return Response()
    transport._opener=Opener();assert transport.download_output(url)==b'download'


def test_native_runner_source_pin_is_enforced(bundle,tmp_path):
    bundle['runner_sources']={'native_runner.py':{'bytes':4,'sha256':hashlib.sha256(b'good').hexdigest()}};seal(bundle)
    m={'schema_version':1,'job_id':oid(),'deadline_epoch':1,'jobs':[{'engine':'castle_native_p1','type':'video','bundle':bundle}],
       'input_files':[{'path':i['name'],'size':i['bytes'],'sha256':i['sha256']} for i in bundle['inputs']]}
    for i in bundle['inputs']:
        p=tmp_path/i['name'];p.parent.mkdir(exist_ok=True);p.write_bytes(i['slot'].encode())
    (tmp_path/'runner').mkdir();(tmp_path/'runner/native_runner.py').write_bytes(b'good')
    validate_native_manifest(m,tmp_path)
    (tmp_path/'runner/native_runner.py').write_bytes(b'evil')
    with pytest.raises(ValueError,match='Runner differs'):validate_native_manifest(m,tmp_path)
