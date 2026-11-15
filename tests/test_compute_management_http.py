"""Real HTTP boundaries with isolated storage and an empty synthetic Comfy."""
import json
import uuid
import time
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
    with pytest.raises(urllib.error.HTTPError) as legacy_error:
        call('/api/compute/providers/comfy_cloud', {'enabled': False, 'endpoint': 'https://cloud.comfy.org', 'recipes': []})
    assert legacy_error.value.code == 409
    assert json.load(legacy_error.value)['error']['code'] == 'legacy_write_disabled'
    request = {'featureId': 'text2img', 'settings': {'positive': 'Synthetic blue landscape', 'aspect': '1024x1024'}, 'files': {}}
    preview = call('/api/compute/preview', {'request': request, 'placement': 'auto', 'limits': {}})
    assert {row['lane'] for row in preview['candidates']} == {'local', 'colab', 'autoip', 'comfy_cloud', 'runninghub'}
    assert all(not row['eligible'] for row in preview['candidates'] if row['lane'] in {'comfy_cloud', 'runninghub'})
    assert {row['id'] for row in call('/api/compute/pipeline-recipes')['recipes']} >= {
        'text-image-video', 'edit-image-video', 'voice-design-lipsync'}
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


def test_guided_catalog_preferences_and_custom_dag_http(test_server):
    base, opener, cookie = test_server
    def call(path, body=None):
        req = urllib.request.Request(base + '/api/compute/' + path,
            headers={'Cookie': cookie, 'Content-Type': 'application/json', 'Origin': base},
            data=json.dumps(body).encode() if body is not None else None)
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    catalog = call('recipe-catalog')['templates']
    assert catalog and any(item['feature_id'] == 'text2img' for item in catalog)
    assert all(item['validation'] == 'contract_tested' for item in catalog)
    registry = call('pipeline-features')['features']
    assert any(item['id'] == 'text2img' for item in registry)
    assert all('builder' not in item for item in registry)
    coverage = call('capabilities')
    assert coverage['readiness_checked'] is False
    assert any(row['id'] == 'text2img' for row in coverage['features'])
    # Follow test_provider_console.py::write and its authenticated inventory test.
    from types import SimpleNamespace
    from colab_service import ColabService
    from provider_console import ProviderConsole
    from provider_fabric.runtime import actor_context_from_castle, load_or_create_identity_key
    from provider_fabric.store import ProviderFabricStore
    from compute_parity.readiness import save
    root = test_server.root
    with opener.open(urllib.request.Request(base + '/api/auth/status', headers={'Cookie': cookie})) as response:
        user = json.load(response)['user']
    identity = load_or_create_identity_key(root / 'actor-identity.key')
    runtime = SimpleNamespace(config=SimpleNamespace(credential_root=root / 'provider-secrets'),
        store=ProviderFabricStore(root / 'provider-fabric.sqlite3'),
        actor_context=lambda email, role: actor_context_from_castle(email, role, identity_key=identity))
    console = ProviderConsole(root, runtime, user, ColabService(root / 'colab'),
        probes={'runninghub': lambda _: {'connected': True, 'account_type': 'NORMAL', 'balances': []}})
    def rh_card():
        return next(c for c in console.snapshot()['connections'] if c['provider'] == 'runninghub')
    console.write_credential('runninghub', {'credential': 'synthetic-test-key-1', 'revision': rh_card()['revision'], 'operation_id': str(uuid.uuid4())})
    console.test('runninghub', {'revision': rh_card()['revision']})
    catalog_path = root / 'compute-parity/bundles-r29'
    catalog_path.mkdir(parents=True, exist_ok=True)
    (catalog_path / 'catalog.json').write_text(json.dumps({'cases': [{'owner': console.owner, 'ready': True, 'bundle_sha256': 'synthetic-bundle'}]}))
    save(root / 'compute-parity', {'owner': console.owner, 'lane': 'runninghub', 'credential_version': rh_card()['credential_version'],
        'bundle_sha256': 'synthetic-bundle', 'observed_at': time.time(), 'expires_at': time.time() + 300,
        'connection_verified': True, 'dependencies_ready': True, 'hashes_verified': True, 'lifecycle_verified': True,
        'alias_map_version': '20261111-r29b', 'alias_evidence_verified': True})
    console.toggle('runninghub', {'revision': rh_card()['revision'], 'use_enabled': True})
    prefs = {'default_placement': 'auto', 'feature_placements': {'text2img': 'runninghub'},
             'auto_order': console.snapshot()['preferences']['auto_order']}
    assert call('preferences', prefs)['feature_placements'] == prefs['feature_placements']
    assert call('preferences')['default_placement'] == 'auto'
    request = {'featureId': 'text2img', 'settings': {'positive': 'Synthetic blue sky', 'width': 1024, 'height': 1024}, 'files': {}}
    assert call('preview', {'request': request})['placement'] == 'runninghub'
    assert call('preview', {'request': request, 'placement': 'local'})['placement'] == 'local'
    with pytest.raises(urllib.error.HTTPError) as error:
        call('preferences', {**prefs, 'feature_placements': {'missing-tool': 'local'}})
    assert error.value.code == 400
    pid = str(uuid.uuid4())
    body = {'pipeline_id': pid, 'plan': {'version': 1, 'steps': [
        {'id': 'image', 'request': request, 'placement': 'comfy_cloud'},
        {'id': 'edit', 'request': {'featureId': 'img2img', 'settings': {'instruction': 'Make the sky red'}, 'files': {}},
         'placement': 'runninghub', 'bindings': {'image': {'step_id': 'image', 'artifact_index': 0, 'expected_kind': 'image'}}},
    ]}}
    first = call('pipelines', body)
    assert first['pipeline_id'] == pid
    assert call('pipelines', body)['plan_hash'] == first['plan_hash']
    deadline = time.monotonic() + 10
    while True:
        status = call('pipelines/' + pid)
        if status['steps'][0]['state'] in ('rejected', 'failed'):
            break
        assert time.monotonic() < deadline, status
        time.sleep(.05)
    old_id = status['steps'][0]['request_id']
    retried = call('pipelines/' + pid + '/retry', {'step_id': 'image', 'placement': 'runninghub'})
    step = retried['steps'][0]
    assert step['placement'] == 'runninghub' and step['request_id'] != old_id
    call('pipelines/' + pid + '/cancel', {})
    with pytest.raises(urllib.error.HTTPError) as error:
        call('pipelines', {'pipeline_id': str(uuid.uuid4()), 'plan': {'version': 1, 'steps': [
            {'id': 'invalid', 'request': {'featureId': 'not-a-feature', 'settings': {}, 'files': {}}, 'placement': 'local'}]}})
    assert error.value.code in (400, 422)
    assert json.load(error.value)['submission_state'] == 'not_submitted'
    call('preferences', {'default_placement': 'local', 'feature_placements': {}, 'auto_order': console.snapshot()['preferences']['auto_order']})


def test_pipeline_initial_media_is_frozen_and_kind_checked(test_server):
    import base64
    import io
    import wave
    from PIL import Image
    base, opener, cookie = test_server
    def post(body):
        req = urllib.request.Request(base + '/api/compute/pipelines',
            headers={'Cookie': cookie, 'Content-Type': 'application/json', 'Origin': base},
            data=json.dumps(body).encode())
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(output, format='PNG')
    body = {'pipeline_id': str(uuid.uuid4()), 'recipe_id': 'edit-image-video',
        'inputs': {'image': {'name': 'synthetic.png', 'dataUrl': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()},
                   'instruction': 'Make it red', 'motion_prompt': 'Slow camera'},
        'placements': {'image': 'comfy_cloud', 'video': 'runninghub'}}
    original = post(body)
    assert post(body)['plan_hash'] == original['plan_hash']
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000); stream.writeframes(b'\0' * 1600)
    wrong = {**body, 'pipeline_id': str(uuid.uuid4()), 'inputs': {**body['inputs'],
        'image': {'name': 'synthetic.wav', 'dataUrl': 'data:audio/wav;base64,' + base64.b64encode(audio.getvalue()).decode()}}}
    with pytest.raises(urllib.error.HTTPError) as error:
        post(wrong)
    assert error.value.code in (400, 422)
    failure = json.load(error.value)
    assert failure['submission_state'] == 'not_submitted'
    assert failure['error']['code'] == 'artifact_kind_mismatch'
    # Reject an invalid later voice step before accepting any paid predecessor.
    for request in (
        {'featureId': 'voice-speak', 'settings': {'voiceSource': 'persona'}, 'files': {}},
        {'featureId': 'lipsync', 'settings': {}, 'files': {'video': {'name': 'synthetic.mp4', 'dataUrl': 'invalid-on-purpose'}}},
    ):
        with pytest.raises(urllib.error.HTTPError) as error:
            post({'pipeline_id': str(uuid.uuid4()), 'plan': {'version': 1, 'steps': [
                {'id': 'first', 'placement': 'comfy_cloud', 'request': {'featureId': 'text2img', 'settings': {'positive': 'Synthetic sky'}, 'files': {}}},
                {'id': 'voice', 'placement': 'local', 'request': request, 'depends_on': ['first']},
            ]}})
        assert error.value.code in (400, 422)
        assert json.load(error.value)['submission_state'] == 'not_submitted'
