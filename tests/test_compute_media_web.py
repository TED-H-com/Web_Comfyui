"""Authenticated shared routing freezes a media input before AutoIP dispatch."""
import base64
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from test_compute_web import web_fixture
from test_compute_autoip_media import preview_runtime
from provider_fabric.models import MediaAsset
from provider_fabric.owned_media_source import resolve_owned_media_source
from provider_fabric.service import ActorContext, _parse_plan_request
from provider_fabric.store import ProviderFabricStore


def runtime_for(f, tmp_path):
    runtime, quotes = preview_runtime()
    store = ProviderFabricStore(tmp_path/'provider'/'fabric.sqlite3')
    store.register_system_local_provider_account('local-main','Local',enabled=True)
    # Account readiness is synthetic; actual owned image ingestion and canonical
    # version storage are real and never invoke a network upload.
    store.get_owned_provider_account = runtime.store.get_owned_provider_account
    runtime.store=store
    runtime.config=SimpleNamespace(local_account_id='local-main',database_path=tmp_path/'provider'/'fabric.sqlite3')
    submitted=[]
    def submit(actor, body):
        parsed=_parse_plan_request(body['plan'])
        assert parsed.intent.characters==()
        assert body['plan']['max_credits']==20
        for asset in parsed.intent.assets:
            path=resolve_owned_media_source(store,None,actor.user_id,asset,
                allowed_file_roots=(runtime.config.database_path.parent/'autoip-outputs',))
            assert path.read_bytes()==f.media
        submitted.append(body)
        return {'intent_id':'intent:synthetic','status_path':'/api/intents/intent:synthetic/status'}
    runtime.service=SimpleNamespace(submit_generation=submit)
    f.handler._provider_runtime_actor=lambda:(runtime,ActorContext('owner','operator'))
    f.server.FEATURE_BY_ID['img2img']={'id':'img2img','builder':'flux_kontext',
        'uploads':[{'id':'image','required':True,'accept':'image/*'}]}
    return runtime,quotes,submitted


def body(f):
    return {'featureId':'img2img','computeVisualClass':'anime',
        'settings':{'instruction':'Make the synthetic cup blue.','steps':20,'guidance':2.5,'denoise':1.0,'seed':-1},
        'files':{'image':{'name':'synthetic.png','type':'image/png','dataUrl':'data:image/png;base64,'+base64.b64encode(f.media).decode()}}}


def test_shared_media_request_quotes_then_dispatches_one_immutable_owned_version(web_fixture,tmp_path):
    f=web_fixture
    runtime,quotes,submitted=runtime_for(f,tmp_path)
    request=body(f);rid=str(uuid.uuid4());limits={'max_credits':20}
    preview=f.web.inspect_autoip(f.web.owner,request,limits)
    assert preview['eligible'] and len(quotes)==1
    assert not list(f.input_root.iterdir()) and not submitted
    result=f.web.router.submit(f.web.owner,rid,request,'autoip',limits)
    assert result['state']=='queued' and result['downstream_id']=='intent:synthetic',result
    assert len(submitted)==1 and submitted[0]['plan']['visual_class']=='anime'
    assert len(submitted[0]['plan']['assets'])==1
    assert f.web.router.submit(f.web.owner,rid,request,'autoip',limits)['downstream_id']=='intent:synthetic'
    assert len(submitted)==1
    assert not list(f.input_root.iterdir()) # Ephemeral pre-admission copy removed.
    assert len(list((tmp_path/'provider'/'autoip-outputs'/'compute-inputs').rglob('*.png')))==1


@pytest.mark.parametrize('change',[{'characterId':'owned-character'},{'action':'photoshoot'}, {'variantId':'a'}, {'computeVisualClass':''}])
def test_generic_media_cannot_anonymize_character_or_infer_image_class(web_fixture,tmp_path,change):
    f=web_fixture;_,_,submitted=runtime_for(f,tmp_path)
    result=f.web.inspect_autoip(f.web.owner,{**body(f),**change},{'max_credits':20})
    assert result['eligible'] is False
    assert not submitted and not list(f.input_root.iterdir())


def test_unowned_reused_input_is_rejected_before_dispatch(web_fixture,tmp_path):
    f=web_fixture;_,_,submitted=runtime_for(f,tmp_path)
    request={**body(f),'files':{'image':{'serverFile':'someone-elses.png'}}}
    (f.input_root/'someone-elses.png').write_bytes(f.media)
    result=f.web.router.submit(f.web.owner,str(uuid.uuid4()),request,'autoip',{'max_credits':20})
    assert result['state']=='rejected' and not submitted
