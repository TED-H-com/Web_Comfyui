"""Real HTTP boundaries with isolated storage and an empty synthetic Comfy."""
import json
import uuid
import urllib.error
import urllib.request

import pytest


def test_five_routes_management_and_durable_pipeline_http(test_server):
    base, opener, cookie = test_server
    def call(path, body=None, *, authenticated=True):
        headers = {'Content-Type': 'application/json', 'Origin': base}
        if authenticated:
            headers['Cookie'] = cookie
        request = urllib.request.Request(base + path, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        with opener.open(request, timeout=20) as response:
            return json.load(response)
    for path in ('/api/compute/providers', '/api/compute/pipeline-recipes', '/api/compute/pipelines'):
        with pytest.raises(urllib.error.HTTPError) as error:
            call(path, authenticated=False)
        assert error.value.code == 401
    config = call('/api/compute/providers')
    assert {row['provider'] for row in config['providers']} == {'comfy_cloud', 'runninghub'}
    assert 'api_key' not in json.dumps(config)
    call('/api/compute/providers/comfy_cloud', {'enabled': False, 'endpoint': 'https://cloud.comfy.org', 'recipes': []})
    request = {'featureId': 'text2img', 'settings': {'positive': 'Synthetic blue landscape', 'aspect': '1024x1024'}, 'files': {}}
    preview = call('/api/compute/preview', {'request': request, 'placement': 'auto', 'limits': {}})
    assert {row['lane'] for row in preview['candidates']} == {'local', 'colab', 'autoip', 'comfy_cloud', 'runninghub'}
    assert all(not row['eligible'] for row in preview['candidates'] if row['lane'] in {'comfy_cloud', 'runninghub'})
    assert len(call('/api/compute/pipeline-recipes')['recipes']) == 2
    pid = str(uuid.uuid4())
    body = {'pipeline_id': pid, 'recipe_id': 'text-image-video',
            'inputs': {'image_prompt': 'Synthetic blue sky', 'motion_prompt': 'Slow synthetic clouds'},
            'placements': {'image': 'comfy_cloud', 'video': 'runninghub'}}
    created = call('/api/compute/pipelines', body)
    assert created['pipeline_id'] == pid
    replay = call('/api/compute/pipelines', body)
    assert replay['plan_hash'] == created['plan_hash']
    with pytest.raises(urllib.error.HTTPError) as error:
        call('/api/compute/pipelines', {**body, 'inputs': {**body['inputs'], 'image_prompt': 'Different intent'}})
    assert error.value.code == 409
    listed = call('/api/compute/pipelines')['pipelines']
    assert len(listed) == 1
    assert 'Synthetic blue' not in json.dumps(listed)
    assert call('/api/compute/pipelines/' + pid)['pipeline_id'] == pid
    with pytest.raises(urllib.error.HTTPError) as error:
        call('/api/compute/pipelines/' + str(uuid.uuid4()))
    assert error.value.code == 404
    assert json.load(error.value)['error']['code'] == 'pipeline_not_found'
    cancelled = call('/api/compute/pipelines/' + pid + '/cancel', {})
    assert cancelled['cancel_requested'] is True
    assert len(call('/api/compute/pipelines')['pipelines']) == 1
