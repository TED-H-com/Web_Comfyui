"""Router-facing status and adopted-output checks, with synthetic metadata only."""
import contextlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_web import ComputeWeb
from compute_routing import ComputeAdapter, ComputeRouter
from provider_fabric.store import ProviderFabricStore


@pytest.mark.parametrize(('source','expected'), [
    ('pending','queued'), ('preparing','queued'), ('submitted','queued'),
    ('queued','queued'), ('running','running'), ('failed','failed'),
    ('cancelled','cancelled'), ('abandoned','rejected'),
    ('timeout','submission_uncertain'), ('submission_uncertain','submission_uncertain'),
])
def test_autoip_status_preserves_terminal_and_ambiguous_states(source, expected):
    web = object.__new__(ComputeWeb)
    runtime = SimpleNamespace(service=SimpleNamespace(get_intent_status=lambda *args: {'state':source}))
    web.h = SimpleNamespace(_provider_runtime_actor=lambda:(runtime, SimpleNamespace(user_id='owner')))
    assert web.status_autoip('owner','intent:test')['state'] == expected


def test_completion_waits_for_owned_download_then_survives_router_poll(tmp_path):
    verified = False
    owner_calls = []
    def outputs(owner, iid):
        owner_calls.append((owner,iid))
        return ['output:synthetic']
    runtime = SimpleNamespace(service=SimpleNamespace(get_intent_status=lambda *args: {
        'state':'completed','output_verified':verified}),
        store=SimpleNamespace(list_owned_intent_output_ids=outputs))
    web = object.__new__(ComputeWeb)
    web.h = SimpleNamespace(_provider_runtime_actor=lambda:(runtime, SimpleNamespace(user_id='owner')))
    router = ComputeRouter(tmp_path/'routing.sqlite3', {'autoip':ComputeAdapter(
        lambda *args:{'eligible':True}, lambda *args:{'state':'queued','downstream_id':'intent:test'},
        web.status_autoip)})
    rid='bbbbbbbb-bbbb-4bbb-abbb-bbbbbbbbbbbb'
    router.submit('owner',rid,{'featureId':'text2img'},'autoip')
    assert router.status('owner',rid,refresh=True)['state']=='collecting'
    assert owner_calls==[]
    verified=True
    job=router.status('owner',rid,refresh=True)
    assert job['state']=='completed'
    assert job['artifacts']==[{'id':'output:synthetic','name':'成果 1','url':'/api/assets/output:synthetic/source'}]
    assert owner_calls==[('owner','intent:test')]


def test_output_query_is_bound_to_exact_owner_account_task_and_validated_download(tmp_path):
    # Execute the production SQL against synthetic metadata on disk.
    db=sqlite3.connect(tmp_path/'metadata.sqlite3');db.row_factory=sqlite3.Row
    db.executescript('''
      CREATE TABLE generation_intent_owners(intent_id TEXT,owner_user_id TEXT);
      CREATE TABLE submission_outbox(intent_id TEXT,outbox_id TEXT,account_id TEXT);
      CREATE TABLE external_tasks(outbox_id TEXT,task_id TEXT,state TEXT);
      CREATE TABLE remote_asset_projections(projection_id TEXT,account_id TEXT,metadata_json TEXT,media_kind TEXT);
      CREATE TABLE remote_asset_owners(projection_id TEXT,owner_user_id TEXT);
      CREATE TABLE remote_asset_versions(projection_id TEXT,metadata_json TEXT,source_uri TEXT,byte_size INT,sha256 TEXT,mime_type TEXT);
      INSERT INTO generation_intent_owners VALUES('intent:test','owner');
      INSERT INTO submission_outbox VALUES('intent:test','outbox:test','account:test');
      INSERT INTO external_tasks VALUES('outbox:test','task:test','completed');
    ''')
    def add(pid,owner='owner',account='account:test',task='task:test',verified=True,source='autoip_generation'):
        db.execute('INSERT INTO remote_asset_projections VALUES(?,?,?,?)',(pid,account,json.dumps({
            'source':source,'intent_id':'intent:test','task_id':task}),'image'))
        db.execute('INSERT INTO remote_asset_owners VALUES(?,?)',(pid,owner))
        db.execute('INSERT INTO remote_asset_versions VALUES(?,?,?,?,?,?)',(pid,json.dumps({'validated_download':verified}),'file:/synthetic',1,'a'*64,'image/png'))
    add('good');add('wrong-owner',owner='other');add('wrong-account',account='other')
    add('wrong-task',task='other');add('not-validated',verified=False);add('wrong-source',source='compute_upload')
    store=object.__new__(ProviderFabricStore)
    store._read_connection=lambda:contextlib.nullcontext(db)
    calls=[]
    store.get_intent_status=lambda iid,owner:(calls.append((iid,owner)) or {'state':'completed','output_verified':True})
    assert store.list_owned_intent_output_ids('owner','intent:test')==['good']
    assert calls==[('intent:test','owner')]
    assert store.list_owned_intent_output_ids('other','intent:test')==[]
    db.close()


def test_local_cancellation_waits_for_running_worker_acknowledgement():
    web=object.__new__(ComputeWeb)
    web.h=SimpleNamespace(_cancel_job_result=lambda task:{'action':'cancel_requested'})
    assert web.cancel_local('owner','task')=={'state':'cancelling','downstream_id':'task'}


def test_local_worker_outputs_survive_comfy_offline_and_use_owned_durable_sidecar():
    web=object.__new__(ComputeWeb);web.user={'email':'owner@example.invalid'}
    checks=[]
    def access(user, task, metadata=None, index=None):
        checks.append((metadata,index))
        return metadata=={'submittedBy':{'email':user['email']}} and index=={}
    web.s=SimpleNamespace(
        local_job_snapshot=lambda:[],
        request_json=lambda path:pytest.fail('local worker status must not require Comfy'),
        direct_sidecar_for_prompt=lambda task:('/synthetic/metadata',{'status':'completed'}),
        direct_sidecar_owner_email=lambda *args:'owner@example.invalid',
        user_can_access_job=access,
        direct_history_detail=lambda *args:{'completed':True,'outputs':[
            {'filename':'result.wav','subfolder':'Synthetic','type':'output'},
            {'filename':'not-owned.wav','subfolder':'Other','type':'output'}]},
        user_can_access_view_item=lambda user,filename,*args:filename=='result.wav')
    result=web.status_local('owner','local-synthetic')
    assert result['state']=='completed'
    assert result['artifacts']==[{'name':'result.wav','url':'/api/view?filename=result.wav&subfolder=Synthetic&type=output&promptId=local-synthetic'}]
    assert checks==[({'submittedBy':{'email':'owner@example.invalid'}},{})]
    web.s.direct_sidecar_owner_email=lambda *args:'other@example.invalid'
    with pytest.raises(PermissionError):web.status_local('owner','local-synthetic')


def test_local_capacity_counts_direct_and_gpu_orchestrator_jobs_once():
    web=object.__new__(ComputeWeb);web.user={}
    web.s=SimpleNamespace(FEATURE_BY_ID={'text2img':{'id':'text2img','builder':'text'}},
        feature_status=lambda:[{'id':'text2img','ready':True}],
        request_json=lambda path:{'queue_running':[[0,'shared-wrapper'],[1,'native']], 'queue_pending':[]},
        local_job_snapshot=lambda:[{'promptId':'shared-wrapper','state':'running'},{'promptId':'preparing-h3','state':'running'}],
        orchestrator_job_snapshot=lambda:[{'promptId':'voice-run','state':'pending','lane':'gpu'},
            {'promptId':'voice-run','state':'running','lane':'gpu'}, {'promptId':'cpu-task','state':'running','lane':'cpu'}])
    assert web.inspect_local('owner',{'featureId':'text2img'}, {})['queue_depth']==4
