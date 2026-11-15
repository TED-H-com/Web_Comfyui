"""Hosted alias/estimate regressions with fake provider schemas and owned bytes."""
import contextlib
import copy
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlsplit
import pytest
from test_compute_parity_p1 import bundle, no_network, seal, oid, schema, files, FakeTransport
from compute_parity.contracts import compile_request, dependency_probe, ParityError
from compute_parity.hosted_readiness import refresh, PUBLIC_MD5
from compute_parity.readiness import dependencies_usable, entries
from compute_parity.hosted_estimates import estimate, MEASUREMENTS
from compute_parity.transport import Transport
from compute_parity.adapters import RunningHubNative, CloudNative
from compute_parity.runtime import Runtime
from compute_parity.journal import Journal
from compute_parity import model_aliases as aliases

@pytest.fixture
def alias_bundle(bundle):
    b=copy.deepcopy(bundle)
    a=aliases.reviewed_map()['runninghub'][aliases.SOURCE]
    b['models'].append({'name':aliases.SOURCE,'target':'models/loras/'+aliases.SOURCE,
                        'bytes':a['bytes'],'sha256':a['source_sha256']})
    b['hosted_model_proof']={name:{'md5':md5,'page_id':page} for name,(page,md5) in PUBLIC_MD5.items()}
    b['graph']['5']={'class_type':'CLIPLoader','inputs':{'clip_name':'text.safetensors'}}
    b['graph']['6']={'class_type':'LoraLoaderModelOnly','inputs':{'lora_name':aliases.SOURCE,'model':['3',0],'strength_model':1.0}}
    return seal(b)

def console(root):
    @contextlib.contextmanager
    def db(): yield None
    return NS(root=root,owner='owner',prefs=NS(db=db),_raw=lambda *a:{'credential_version':1,'account_type':'NORMAL'})

@pytest.mark.parametrize('lane',['runninghub','comfy_cloud'])
def test_compile_probe_and_inventory_use_exact_alias(alias_bundle,lane,tmp_path,monkeypatch):
    original=copy.deepcopy(alias_bundle)
    plan=compile_request(alias_bundle,lane,request_id=oid())
    graph=plan.get('probe_graph') or plan['json']['prompt']
    target=aliases.reviewed_map()[lane][aliases.SOURCE]['target_name']
    assert graph['6']['inputs']['lora_name']==target and alias_bundle==original
    info=schema(graph)
    assert dependency_probe(graph,info)['ready']
    info[graph['6']['class_type']]['input']['required']['lora_name']=([aliases.SOURCE],{})
    assert dependency_probe(graph,info)['missing_models']==[target]
    info=schema(graph)
    if lane=='comfy_cloud':info[graph['6']['class_type']]['input']['required']['lora_name']=(['public-only.safetensors'],{})
    calls=[];available=[True]
    def request(method,path,**kw):
        assert method=='GET';calls.append(path)
        if 'object_info' in path:return info
        q=parse_qs(urlsplit(path).query);name=q['name_contains'][0]
        if name==target:assert q['include_public']==['false']
        model=next(m for m in alias_bundle['models'] if m['name']==name or (name==target and m['name']==aliases.SOURCE))
        a=aliases.reviewed_map()[lane][aliases.SOURCE]
        return {'assets':[{'name':name,'size':model['bytes'],'tags':['models','loras'],'loader_path':name,
            'metadata':{'revision':a['evidence'].get('revision'),'resolve_url':a['evidence'].get('url')}}] if available[0] else []}
    monkeypatch.setattr(Transport,'for_owner',lambda *a,**kw:NS(fingerprint='fake',request=request))
    r=refresh(console(tmp_path),alias_bundle,lane,{'runninghub_points':10,'comfy_cloud_credits':10},persist=False)
    assert r['can_enable'] and r['ready'] and not entries(tmp_path/'compute-parity')
    record=r['model_aliases'][0]
    assert record['source_name']==aliases.SOURCE and record['target_name']==target and record['evidence']
    if lane=='comfy_cloud':
        available[0]=False
        assert not refresh(console(tmp_path),alias_bundle,lane,persist=False)['can_enable']

@pytest.mark.parametrize('lane',['runninghub','comfy_cloud'])
@pytest.mark.parametrize('damage',['missing_map','missing_evidence','wrong_hash','wrong_size'])
def test_missing_or_mismatched_alias_evidence_not_ready(alias_bundle,lane,tmp_path,monkeypatch,damage):
    if damage in {'missing_map','missing_evidence'}:
        data=aliases.reviewed_map()
        if damage=='missing_map':data.pop(lane)
        else:data[lane][aliases.SOURCE]['evidence']={}
        monkeypatch.setattr(aliases,'reviewed_map',lambda:data)
    else:
        model=next(m for m in alias_bundle['models'] if m['name']==aliases.SOURCE)
        if damage=='wrong_hash':model['sha256']='0'*64
        else:model['bytes']+=1
        seal(alias_bundle)
    monkeypatch.setattr(Transport,'for_owner',lambda *a,**kw:pytest.fail('must reject before provider access'))
    r=refresh(console(tmp_path),alias_bundle,lane)
    assert not r['ready'] and not r['can_enable']
    assert not dependencies_usable(entries(tmp_path/'compute-parity')[0],lane)

@pytest.mark.parametrize('lane',['local','colab','autoip'])
def test_other_lanes_do_not_read_alias_map(alias_bundle,lane,monkeypatch):
    monkeypatch.setattr(aliases,'reviewed_map',lambda:pytest.fail('non-hosted lane read alias map'))
    plan=compile_request(alias_bundle,lane,request_id=oid())
    assert plan.get('model_aliases',[])==[]
    if lane!='autoip':assert plan['json']['prompt']['6']['inputs']['lora_name']==aliases.SOURCE

@pytest.mark.parametrize('lane',['runninghub','comfy_cloud'])
def test_stale_readiness_without_alias_verification_rejected(lane):
    e={'dependencies_ready':True,'hashes_verified':True,'lifecycle_verified':True}
    assert not dependencies_usable(e,lane)

@pytest.mark.parametrize('lane',['runninghub','comfy_cloud'])
def test_receipts_persist_both_names(alias_bundle,lane,tmp_path):
    plan=compile_request(alias_bundle,lane,request_id=oid())
    graph=plan.get('probe_graph') or plan['json']['prompt']
    class Fake(FakeTransport):
        def request(self,method,path,**kw):
            if path=='/api/object_info':return schema(graph)
            if path.startswith('/api/assets?'):
                a=aliases.reviewed_map()['comfy_cloud'][aliases.SOURCE]
                return {'assets':[{'name':a['target_name'],'size':a['bytes'],'tags':['models','loras'],'loader_path':a['target_name'],
                    'metadata':{'revision':a['evidence']['revision'],'resolve_url':a['evidence']['url']}}]}
            if path=='/api/v2/assets':return {'id':'asset-fake'}
            if path=='/api/v2/jobs':return {'id':'00000000-0000-4000-8000-00000000c001','urls':{'self':'/api/v2/jobs/00000000-0000-4000-8000-00000000c001','cancel':'/api/v2/jobs/00000000-0000-4000-8000-00000000c001/cancel'}}
            return super().request(method,path,**kw)
    t=Fake(alias_bundle);j=Journal(tmp_path/'journal.sqlite3');rid=oid()
    adapter=(RunningHubNative if lane=='runninghub' else CloudNative)(t,j)
    job=adapter.submit('owner',rid,alias_bundle,files(alias_bundle),quote={'unit':'POINT' if lane=='runninghub' else 'credits','amount':10})
    assert job['state']=='queued'
    receipt=Runtime.receipt(job)
    assert receipt['model_aliases']==j.get('owner',rid)['model_aliases']==plan['model_aliases']
    assert receipt['model_aliases'][0]['source_name']==aliases.SOURCE
    assert receipt['model_aliases'][0]['target_name']!=aliases.SOURCE
    from compute_routing import _public_receipt
    routed=_public_receipt(receipt)
    assert routed['model_aliases']==receipt['model_aliases']
    assert routed['estimated_cost']['is_estimate'] and routed['actual_cost'] is None

@pytest.mark.parametrize('lane,rate',[('runninghub',0.25),('comfy_cloud',0.266)])
def test_reviewed_case_estimate_and_unknown_case(lane,rate,monkeypatch):
    monkeypatch.setitem(MEASUREMENTS, 'synthetic-bundle', {'case_id':'dance.realistic.example-a','wall_seconds':500})
    key,m=next(iter(MEASUREMENTS.items()))
    b={'bundle_sha256':key,'case_id':m['case_id']}
    q=estimate(b,lane)
    assert q['amount']==round(m['wall_seconds']*1.5*rate,6)
    assert q['actual_cost'] is None and q['is_estimate'] and q['rate_source']
    if lane=='runninghub':assert '估計，非官方報價' in q['label']
    assert estimate({**b,'bundle_sha256':'changed'},lane)=={}
    assert estimate({**b,'case_id':'different'},lane)=={}

def test_cloud_runtime_cap(monkeypatch):
    monkeypatch.setitem(MEASUREMENTS, 'synthetic-bundle', {'case_id':'dance.realistic.example-a','wall_seconds':500})
    key,m=next(iter(MEASUREMENTS.items()));b={'bundle_sha256':key,'case_id':m['case_id']}
    q=estimate(b,'comfy_cloud',cloud_limit_seconds=600)
    assert q['expected_seconds']==600 and q['amount']==159.6
    assert estimate(b,'comfy_cloud',cloud_limit_seconds=0)=={}

@pytest.mark.parametrize('damage',['size','tags','loader_path','revision','resolve_url','name'])
def test_cloud_private_inventory_rejects_wrong_category_or_provenance(alias_bundle,damage):
    from compute_parity.cloud_inventory import private_aliases
    plan=compile_request(alias_bundle,'comfy_cloud',request_id=oid())
    a=aliases.reviewed_map()['comfy_cloud'][aliases.SOURCE]
    row={'name':a['target_name'],'size':a['bytes'],'tags':['models','loras'],'loader_path':a['target_name'],
         'metadata':{'revision':a['evidence']['revision'],'resolve_url':a['evidence']['url']}}
    if damage in {'revision','resolve_url'}:row['metadata'][damage]='wrong'
    else:row[damage]=[] if damage=='tags' else 1 if damage=='size' else 'wrong'
    allowed,records=private_aliases(alias_bundle,plan['probe_graph'],NS(request=lambda *a,**kw:{'assets':[row]}))
    assert not allowed and not records[0]['present']

def test_cloud_usage_units_and_no_false_settlement():
    from compute_parity.cloud_usage import observe
    t=NS(request=lambda *a:{'summary':{'balance':{'currency':'USD','amount_micros':25000},'spend_micros':1000000}})
    o=observe(t)
    assert o['balance_usd_cents']==25000 and o['workspace_spend_usd_micros']==1000000
    assert 'actual_cost' not in o and o['scope']=='workspace' and o['reporting_may_lag']

@pytest.mark.parametrize('lane',['runninghub','comfy_cloud'])
def test_changed_release_map_invalidates_readiness(alias_bundle,lane,tmp_path,monkeypatch):
    monkeypatch.setattr(aliases,'MAP_SHA256','0'*64)
    r=refresh(console(tmp_path),alias_bundle,lane,persist=False)
    assert not r['can_enable'] and r['reason']=='model_alias_evidence_missing'
    assert not dependencies_usable({'dependencies_ready':True,'hashes_verified':True,'lifecycle_verified':True,
        'alias_map_version':aliases.VERSION,'alias_evidence_verified':True},lane)

def test_cloud_completion_observes_usage_without_claiming_estimate_as_actual(tmp_path,bundle):
    j=Journal(tmp_path/'journal.sqlite3');rid=oid()
    j.reserve('owner',rid,'comfy_cloud','fake','case','identity',{'unit':'credits','amount':200.5})
    j.update('owner',rid,state='queued',remote_id='00000000-0000-4000-8000-00000000c001',links={'self':'/api/v2/jobs/00000000-0000-4000-8000-00000000c001'})
    calls=[]
    def request(method,path,**kw):
        assert method=='GET';calls.append(path)
        if path=='/api/v2/jobs/00000000-0000-4000-8000-00000000c001':return {'status':'succeeded','outputs':[{'url':'/api/v2/assets/result/content','size_bytes':5}]}
        if path=='/api/billing/usage/timeseries':return {'summary':{'balance':{'currency':'USD','amount_cents':100},'spend_micros':2000000}}
        pytest.fail('unexpected provider call')
    a=CloudNative(NS(fingerprint='fake',request=request),j)
    result=a.poll('owner',rid)
    assert result['state']=='completed' and result['settlement_status']=='pending'
    assert result.get('actual_cost') is None and result['cost']['amount']==200.5
    assert result['balance_observation']['balance_usd_cents']==100
    a.poll('owner',rid)
    assert calls==['/api/v2/jobs/00000000-0000-4000-8000-00000000c001','/api/billing/usage/timeseries']

@pytest.mark.parametrize('lane',['local','colab','autoip'])
def test_nonhosted_receipt_unchanged(lane):
    job={'lane':lane,'request_id':'fake','state':'queued','cost':{'unit':'fake','amount':1}}
    r=Runtime.receipt(job)
    assert all(r[k]==v for k,v in {'state':'queued','downstream_id':'fake','cancel_supported':lane!='autoip','result_ids':[],'cost':job['cost']}.items())
    if lane in {'autoip','colab'}:assert r['estimated_cost']==job['cost'] and r['actual_cost'] is None

def test_router_never_echoes_unreviewed_alias_fields():
    from compute_routing import _public_receipt, ComputeUncertain
    a=copy.deepcopy(aliases.reviewed_map()['comfy_cloud'][aliases.SOURCE])
    a.update(map_version=aliases.VERSION,private_provider_field='not-public')
    value={'state':'queued','downstream_id':'fake','model_aliases':[a],
           'estimated_cost':{'unit':'credits','amount':10,'private_provider_field':'not-public'}}
    r=_public_receipt(value)
    assert 'private_provider_field' not in str(r)
    value['model_aliases'][0]['target_name']='not-reviewed'
    with pytest.raises(ComputeUncertain):_public_receipt(value)
