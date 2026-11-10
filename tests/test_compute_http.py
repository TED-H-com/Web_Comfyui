"""Unified routing HTTP tests with synthetic transport, no cloud allocation."""
import json
import uuid
import urllib.request
import urllib.error
import pytest


@pytest.mark.parametrize('test_server', ['colab_transport'], indirect=True)
def test_shared_form_can_run_colab_without_local_models_and_replays_once(test_server):
    base, opener, cookie = test_server
    def call(path, body=None, authenticated=True):
        headers = {'Content-Type':'application/json', 'Origin':base}
        if authenticated: headers['Cookie'] = cookie
        req = urllib.request.Request(base+path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with opener.open(req, timeout=15) as response: return json.load(response)
    with pytest.raises(urllib.error.HTTPError) as error:
        call('/api/compute/jobs', authenticated=False)
    assert error.value.code == 401
    account = call('/api/colab/accounts', {'label':'合成 Ultra'})
    flow = call('/api/colab/accounts/'+account['id']+'/login', {})
    call('/api/colab/accounts/'+account['id']+'/finish', {'flow_id':flow['flow_id'],'code':'synthetic-code'})
    call('/api/colab/accounts/'+account['id']+'/refresh', {})
    feature = next(item for item in call('/api/features')['features'] if item['id']=='text2img')
    sizes = next(field['options'] for field in feature['fields'] if field['id']=='aspect')
    assert any(size['width']==1024 and size['height']==1024 for size in sizes)
    request = {'featureId':'text2img','settings':{'positive':'A synthetic blue square','width':1024,'height':1024,'steps':8,'cfg':1,'seed':1},'files':{}}
    limits = {'account_id':account['id'],'gpu':'L4','max_units':20,'max_minutes':5,'max_credits':100}
    body = {'request_id':str(uuid.uuid4()),'request':request,'placement':'colab','limits':limits}
    preview = call('/api/compute/preview', body)
    assert preview['execution_started'] is False
    assert preview['selected_lane'] == 'colab', json.dumps(preview, ensure_ascii=False)
    assert not next(c for c in preview['candidates'] if c['lane']=='local')['eligible']
    result = call('/api/compute/jobs', body)
    assert result['lane'] == 'colab' and result['downstream_id'] == body['request_id'], result
    replay = call('/api/compute/jobs', body)
    assert replay['downstream_id'] == result['downstream_id']
    with pytest.raises(urllib.error.HTTPError) as error:
        call('/api/compute/jobs', {**body,'placement':'local'})
    assert error.value.code == 409
    jobs = call('/api/compute/jobs')['jobs']
    assert len(jobs) == 1
    assert 'request_json' not in json.dumps(jobs) and 'A synthetic blue' not in json.dumps(jobs)
    assert len(call('/api/colab')['jobs']) == 1
    cancelled = call('/api/compute/jobs/'+body['request_id']+'/cancel', {})
    assert cancelled['state'] in {'cancelled','cancelling'}
    assert call('/api/compute/jobs/'+body['request_id'])['request_id'] == body['request_id']
    with pytest.raises(urllib.error.HTTPError) as error:
        call('/api/compute/jobs/'+str(uuid.uuid4()))
    assert error.value.code == 404
    assert json.load(error.value)['error']['code'] == 'compute_request_not_found'
