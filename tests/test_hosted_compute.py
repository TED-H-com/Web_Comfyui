"""Offline provider HTTP fixtures. No account, GPU, external HTTP or user files."""
import copy
from email.parser import BytesParser
from email.policy import default
import io
import json
import os
from pathlib import Path
import threading
import time
import uuid

from PIL import Image
import pytest

from webui import hosted_compute as H
from webui.compute_routing import ComputeRejected
from webui.hosted_compute_http import Response, TransportError, public_url

OWNER = 'synthetic@example.test'
KEY = 'synthetic-provider-key'
RID = '00000000-0000-4000-8000-000000000001'
REMOTE = '11111111-1111-4111-8111-111111111111'
LIMITS = {'max_comfy_credits': 20, 'max_runninghub_coins': 30, 'max_minutes': 15}
REQUEST = {'featureId': 'text2img', 'settings': {'positive': 'A blue square'}, 'files': {}}


def png():
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), '#123456').save(stream, format='PNG')
    return stream.getvalue()


def recipe(provider='comfy_cloud', *, image=False):
    value = {'feature_id': 'img2img' if image else 'text2img', 'recipe_id': 'synthetic-v1',
        'label': 'Synthetic transport fixture', 'output_kind': 'image', 'estimated_cost': 2,
        'bindings': [{'source': 'prompt', 'node_id': '6', 'input': 'text'}], 'fixed_settings': {}}
    if provider == 'comfy_cloud':
        value['workflow'] = {'6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': ''}},
                             '7': {'class_type': 'LoadImage', 'inputs': {'image': ''}}}
    else:
        value['workflow_id'] = '1910246754753896450'
    if image:
        value['bindings'].append({'source': 'files.image', 'node_id': '7', 'input': 'image'})
    return value


def response(body, status=200, headers=None):
    return Response(status, headers or {}, body if isinstance(body, bytes) else json.dumps(body).encode())


class FakeHTTP:
    def __init__(self, replies=()):
        self.replies, self.calls = list(replies), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert kwargs['timeout'] in (30, 60)
        assert 0 < kwargs['max_bytes'] <= H.MAX_OUTPUT
        if not self.replies:
            raise AssertionError('unexpected HTTP call: ' + method + ' ' + url)
        item = self.replies.pop(0)
        if callable(item):
            return item(method, url, kwargs)
        if isinstance(item, Exception):
            raise item
        return item


def configured(tmp_path, provider='comfy_cloud', *, image=False, http=None, endpoint=None, clock=None):
    http = http or FakeHTTP()
    service = H.HostedComputeService(tmp_path / 'state', transport=http, **({'clock': clock} if clock else {}))
    body = {'enabled': True, 'api_key': KEY, 'recipes': [recipe(provider, image=image)]}
    if endpoint:
        body['endpoint'] = endpoint
    service.configure(OWNER, provider, body)
    return service, http


def accepted(provider='comfy_cloud', **extra):
    return response({'id': REMOTE, 'status': 'queued', **extra}, 201) if provider == 'comfy_cloud' else response({'code': 0, 'data': {'taskId': REMOTE, 'taskStatus': 'QUEUED'}})


def completed(data=None, **extra):
    data = png() if data is None else data
    return response({'id': REMOTE, 'status': 'succeeded', 'outputs': [
        {'name': 'result.png', 'type': 'image', 'size_bytes': len(data), 'url': '/api/v2/assets/asset/content'}], **extra})


def input_request(tmp_path):
    file = tmp_path / 'synthetic.png'
    file.write_bytes(png())
    return {'featureId': 'img2img', 'settings': {'instruction': 'Make the square red'}, 'files': {'image': {}}}, {'image': file}


def test_config_is_owner_scoped_write_only_private_and_offline(tmp_path):
    service, http = configured(tmp_path)
    public = service.public_config(OWNER)
    assert KEY not in json.dumps(public)
    assert public['providers'][0]['credential_configured']
    assert public['providers'][0]['recipes'][0]['verified'] is False
    assert not service.public_config('another@example.test')['providers'][0]['credential_configured']
    assert http.calls == []
    for path in (tmp_path / 'state').rglob('*'):
        assert path.stat().st_mode & 0o077 == 0
    service.configure(OWNER, 'comfy_cloud', {'api_key': ''})
    assert service.public_config(OWNER)['providers'][0]['credential_configured']
    service.configure(OWNER, 'comfy_cloud', {'clear_api_key': True})
    assert not service.public_config(OWNER)['providers'][0]['credential_configured']


@pytest.mark.parametrize('url', ['http://cloud.comfy.org', 'https://127.0.0.1', 'https://cloud.comfy.org.evil.test',
    'https://user@cloud.comfy.org', 'https://cloud.comfy.org/?key=x', 'https://cloud.comfy.org:443',
    'https://a.b.run.comfy.app', 'https://cloud.comfy.org/path'])
def test_configuration_rejects_noncanonical_endpoints(tmp_path, url):
    service = H.HostedComputeService(tmp_path)
    with pytest.raises(H.HostedComputeError):
        service.configure(OWNER, 'comfy_cloud', {'endpoint': url})


@pytest.mark.parametrize('key,value', [('negative', 'custom negative'), ('width', 1088), ('sampler', 'custom'), ('_forged', 'private')])
def test_unmapped_settings_fail_before_any_network(tmp_path, key, value):
    service, http = configured(tmp_path)
    request = copy.deepcopy(REQUEST)
    request['settings'][key] = value
    candidate = service.inspect(OWNER, 'comfy_cloud', request, LIMITS)
    assert candidate['eligible'] is False
    assert 'hosted_settings_unsupported' in candidate['reasons']
    with pytest.raises(ComputeRejected):
        service.submit(OWNER, 'comfy_cloud', RID, request, LIMITS)
    assert not http.calls
    assert service.recover(OWNER, 'comfy_cloud', RID) is None


def test_fixed_values_exact_and_mapped_numeric_inputs_keep_types(tmp_path):
    service, http = configured(tmp_path)
    item = recipe()
    item['workflow']['6']['inputs']['width'] = 1
    item['bindings'].append({'source': 'settings.width', 'node_id': '6', 'input': 'width'})
    item['fixed_settings'] = {'negative': '', 'steps': 8}
    service.configure(OWNER, 'comfy_cloud', {'recipes': [item]})
    req = copy.deepcopy(REQUEST)
    req['settings'].update(width=1088, negative='', steps=8)
    assert service.inspect(OWNER, 'comfy_cloud', req, LIMITS)['eligible']
    http.replies.append(accepted())
    service.submit(OWNER, 'comfy_cloud', RID, req, LIMITS)
    payload = json.loads(http.calls[0][2]['body'])
    assert payload['workflow']['6']['inputs']['width'] == 1088
    assert type(payload['workflow']['6']['inputs']['width']) is int
    req['settings']['negative'] = 'custom'
    assert not service.inspect(OWNER, 'comfy_cloud', req, LIMITS)['eligible']


@pytest.mark.parametrize('limits,reason', [({}, 'hosted_budget_exceeded'),
    ({**LIMITS, 'max_comfy_credits': 1}, 'hosted_budget_exceeded'),
    ({**LIMITS, 'max_minutes': 0}, 'hosted_deadline_required'),
    ({**LIMITS, 'max_concurrent': True}, 'hosted_concurrency_invalid')])
def test_finite_budget_and_deadline_required(tmp_path, limits, reason):
    service, http = configured(tmp_path)
    assert reason in service.inspect(OWNER, 'comfy_cloud', REQUEST, limits)['reasons']
    assert not http.calls


def test_cloud_create_poll_download_restart_and_tamper(tmp_path):
    http = FakeHTTP([accepted(urls={'self': '/api/v2/jobs/custom-path'}), completed(), response(png())])
    service, _ = configured(tmp_path, http=http)
    first = service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    assert first['state'] == 'queued' and first['downstream_id'] == RID
    create = json.loads(http.calls[0][2]['body'])
    assert create == {'workflow': recipe()['workflow'] | {'6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'A blue square'}}}}
    assert 'metadata' not in create
    assert http.calls[0][2]['headers']['Idempotency-Key'] == RID
    assert not service.inspect(OWNER, 'comfy_cloud', REQUEST, LIMITS)['auto_eligible']
    restarted = H.HostedComputeService(tmp_path / 'state', http)
    done = restarted.status(OWNER, 'comfy_cloud', RID)
    assert done['state'] == 'completed'
    assert http.calls[1][1].endswith('/api/v2/jobs/custom-path')
    assert restarted.inspect(OWNER, 'comfy_cloud', REQUEST, LIMITS)['auto_eligible']
    assert REMOTE not in json.dumps(done) and KEY not in json.dumps(done)
    item = done['artifacts'][0]
    assert item['url'] == f'/api/compute/hosted/comfy_cloud/{RID}/files/output-1.png'
    file, metadata = restarted.artifact(OWNER, 'comfy_cloud', RID, item['name'])
    assert file.read_bytes() == png() and metadata['sha256'] == item['sha256']
    with pytest.raises(H.HostedComputeError):
        restarted.artifact('other@example.test', 'comfy_cloud', RID, item['name'])
    file.write_bytes(b'tampered')
    with pytest.raises(H.HostedComputeError):
        restarted.artifact(OWNER, 'comfy_cloud', RID, item['name'])
    restarted.configure(OWNER, 'comfy_cloud', {'api_key': 'rotated-test-key'})
    assert not restarted.inspect(OWNER, 'comfy_cloud', REQUEST, LIMITS)['auto_eligible']


def test_comfy_upload_last_part_and_typed_asset_reference(tmp_path):
    http = FakeHTTP([response({'id': 'asset-123'}, 201), accepted()])
    service, _ = configured(tmp_path, image=True, http=http)
    req, files = input_request(tmp_path)
    service.submit(OWNER, 'comfy_cloud', RID, req, LIMITS, resolved_files=files)
    call = http.calls[0][2]
    message = BytesParser(policy=default).parsebytes(('Content-Type: ' + call['headers']['Content-Type'] + '\r\n\r\n').encode() + call['body'])
    parts = list(message.iter_parts())
    assert [p.get_param('name', header='content-disposition') for p in parts] == ['content_type', 'file_path', 'file']
    assert parts[-1].get_payload(decode=True) == png()
    graph = json.loads(http.calls[1][2]['body'])['workflow']
    assert graph['7']['inputs']['image'] == {'__type': 'core/ASSET', 'info': {'id': 'asset-123'}}
    # Same UUID with changed bytes may not reuse the old request or create again.
    Image.new('RGB', (8, 8), '#ffffff').save(files['image'])
    with pytest.raises(ComputeRejected) as error:
        service.submit(OWNER, 'comfy_cloud', RID, req, LIMITS, resolved_files=files)
    assert error.value.code == 'request_conflict'
    assert len(http.calls) == 2


def test_invalid_input_and_extra_slot_rejected_without_allocation(tmp_path):
    service, http = configured(tmp_path, image=True)
    req, files = input_request(tmp_path)
    files['image'].write_bytes(b'not a picture')
    with pytest.raises(ComputeRejected) as error:
        service.submit(OWNER, 'comfy_cloud', RID, req, LIMITS, resolved_files=files)
    assert error.value.code == 'inputs_invalid'
    assert not http.calls
    req['files']['image2'] = {}
    assert not service.inspect(OWNER, 'comfy_cloud', req, LIMITS)['eligible']


def test_partner_key_requires_explicit_recipe_flag(tmp_path):
    service, http = configured(tmp_path, http=FakeHTTP([accepted()]))
    item = recipe()
    item['partner_nodes'] = True
    service.configure(OWNER, 'comfy_cloud', {'recipes': [item]})
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    assert json.loads(http.calls[0][2]['body'])['extra_data'] == {'api_key_comfy_org': KEY}
    job = next((tmp_path / 'state').rglob('job.json')).read_text()
    assert KEY not in job


@pytest.mark.parametrize('provider', ['comfy_cloud', 'runninghub'])
def test_uncertain_submit_is_durable_and_never_retried(tmp_path, provider):
    service, http = configured(tmp_path, provider, http=FakeHTTP([TransportError('synthetic lost response with secret ' + KEY)]))
    receipt = service.submit(OWNER, provider, RID, REQUEST, LIMITS)
    assert receipt['state'] == 'submission_uncertain'
    assert KEY not in json.dumps(receipt)
    restarted = H.HostedComputeService(tmp_path / 'state', http)
    assert restarted.recover(OWNER, provider, RID)['state'] == 'submission_uncertain'
    assert restarted.submit(OWNER, provider, RID, REQUEST, LIMITS)['state'] == 'submission_uncertain'
    assert len(http.calls) == 1
    with pytest.raises(ComputeRejected):
        restarted.submit(OWNER, provider, str(uuid.uuid4()), REQUEST, LIMITS)
    assert len(http.calls) == 1  # uncertain tasks consume the concurrency slot


def test_dedicated_lookup_recovers_lost_create_without_create_retry(tmp_path):
    http = FakeHTTP([TransportError('lost'), response({'jobs': [{'id': REMOTE, 'metadata': {'webcomfy_request': RID}}]}),
                     response({'id': REMOTE, 'status': 'running'})])
    service, _ = configured(tmp_path, http=http, endpoint='https://dep-test.run.comfy.app')
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    assert json.loads(http.calls[0][2]['body'])['metadata'] == {'webcomfy_request': RID}
    assert service.recover(OWNER, 'comfy_cloud', RID)['state'] == 'running'
    assert [call[0] for call in http.calls] == ['POST', 'GET', 'GET']
    assert 'metadata%5Bwebcomfy_request%5D=' + RID in http.calls[1][1]


def test_runninghub_upload_create_outputs_real_http_shapes(tmp_path):
    http = FakeHTTP([response({'code': 0, 'data': {'fileName': 'api/synthetic.png'}}), accepted('runninghub'),
        response({'code': 813, 'data': None}), response({'code': 804, 'data': None}),
        response({'code': 0, 'data': [{'fileUrl': 'https://rh-images.example.com/result.png', 'fileType': 'png', 'consumeCoins': '2'}]}), response(png())])
    service, _ = configured(tmp_path, 'runninghub', image=True, http=http)
    req, files = input_request(tmp_path)
    service.submit(OWNER, 'runninghub', RID, req, LIMITS, resolved_files=files)
    payload = json.loads(http.calls[1][2]['body'])
    assert payload['apiKey'] == KEY and payload['workflowId'] == recipe('runninghub')['workflow_id']
    assert payload['addMetadata'] is False
    assert payload['nodeInfoList'] == [{'nodeId': '6', 'fieldName': 'text', 'fieldValue': req['settings']['instruction']},
                                       {'nodeId': '7', 'fieldName': 'image', 'fieldValue': 'api/synthetic.png'}]
    assert service.status(OWNER, 'runninghub', RID)['state'] == 'queued'
    assert service.status(OWNER, 'runninghub', RID)['state'] == 'running'
    assert service.status(OWNER, 'runninghub', RID)['state'] == 'completed'
    assert all('/status' not in url for _, url, _ in http.calls)
    assert http.calls[-1][2]['headers'] == {}  # no provider key to signed asset CDN


def test_cancel_ack_is_not_terminal_and_late_completion_can_be_collected(tmp_path):
    http = FakeHTTP([accepted('runninghub'), response({'code': 0, 'data': None}), response({'code': 804}),
                     response({'code': 805}), response({'code': 0, 'data': [{'fileUrl': 'https://rh-images.example.com/a.png', 'fileType': 'png'}]}), response(png())])
    service, _ = configured(tmp_path, 'runninghub', http=http)
    service.submit(OWNER, 'runninghub', RID, REQUEST, LIMITS)
    assert service.cancel(OWNER, 'runninghub', RID)['state'] == 'cancelling'
    # A subsequent cancel error may mean already finished. Still poll outputs.
    assert service.status(OWNER, 'runninghub', RID)['state'] == 'completed'


def test_deadline_tick_restores_after_restart_and_waits_for_terminal(tmp_path):
    now = [100.0]
    http = FakeHTTP([accepted(), response({'id': REMOTE, 'status': 'canceling'}),
                     response({'id': REMOTE, 'status': 'canceled'})])
    service, _ = configured(tmp_path, http=http, clock=lambda: now[0])
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    now[0] = 100 + 16 * 60
    restored = H.HostedComputeService(tmp_path / 'state', http, clock=lambda: now[0])
    restored.tick()
    assert restored.status(OWNER, 'comfy_cloud', RID)['state'] == 'cancelled'
    assert http.calls[1][1].endswith('/cancel')


def test_cancel_arriving_during_upload_prevents_create(tmp_path):
    started, release = threading.Event(), threading.Event()
    def upload(*_):
        started.set()
        assert release.wait(5)
        return response({'id': 'asset-123'}, 201)
    service, http = configured(tmp_path, image=True, http=FakeHTTP([upload]))
    req, files = input_request(tmp_path)
    results = []
    thread = threading.Thread(target=lambda: results.append(service.submit(OWNER, 'comfy_cloud', RID, req, LIMITS, resolved_files=files)))
    thread.start()
    assert started.wait(5)
    cancel = threading.Thread(target=lambda: results.append(service.cancel(OWNER, 'comfy_cloud', RID)))
    cancel.start()
    for _ in range(100):
        if list((tmp_path / 'state').rglob('cancel.json')):
            break
        time.sleep(.01)
    assert list((tmp_path / 'state').rglob('cancel.json'))
    release.set()
    thread.join(5)
    cancel.join(5)
    assert len(results) == 2 and all(r['state'] == 'cancelled' for r in results)
    assert len(http.calls) == 1


def test_redirect_drops_auth_and_blocks_local_destination(tmp_path):
    http = FakeHTTP([accepted(), completed(), Response(302, {'location': 'https://storage.example.com/signed'}, b''), response(png())])
    service, _ = configured(tmp_path, http=http)
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    assert service.status(OWNER, 'comfy_cloud', RID)['state'] == 'completed'
    assert http.calls[2][2]['headers']['Authorization'] == 'Bearer ' + KEY
    assert http.calls[3][2]['headers'] == {}
    with pytest.raises(TransportError):
        service._download({'endpoint': H.PROVIDERS['comfy_cloud'], 'provider_id': 'comfy_cloud'}, 'https://127.0.0.1/private', KEY, 1024)
    assert len(http.calls) == 4


@pytest.mark.parametrize('data', [b'invalid picture', png() + b'corrupt-length'])
def test_outputs_are_not_published_before_size_and_media_validation(tmp_path, data):
    http = FakeHTTP([accepted(), completed(), response(data)])
    service, _ = configured(tmp_path, http=http)
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    result = service.status(OWNER, 'comfy_cloud', RID)
    assert result['state'] == 'collecting' and result['artifacts'] == []
    assert not service.inspect(OWNER, 'comfy_cloud', REQUEST, LIMITS)['auto_eligible']


def test_character_prompt_binding_preserves_identity_prose(tmp_path):
    service, _ = configured(tmp_path, image=True)
    item = recipe(image=True)
    item['feature_id'] = 'character-photoshoot'
    service.configure(OWNER, 'comfy_cloud', {'recipes': [item]})
    req = {'featureId': 'character-photoshoot', 'settings': {'instruction': 'Wear a red coat', 'visualStyle': 'anime',
        '_characterId': 'private-character-id', '_characterReference': {'privatePath': '/never/forward'}}, 'files': {'image': {}}}
    values = H._compile(item, req, {'image': {'__type': 'core/ASSET', 'info': {'id': 'asset'}}})
    prompt = values[0][1]
    assert 'red coat' in prompt and '動漫畫風' in prompt
    assert '唯一角色身分參考' in prompt
    assert 'private-character-id' not in prompt and '/never/forward' not in prompt
    item['bindings'][0]['source'] = 'settings.instruction'
    with pytest.raises(H.HostedComputeError):
        H._compile(item, req)


def test_random_seed_is_materialized_once_as_integer(tmp_path):
    service, http = configured(tmp_path, http=FakeHTTP([accepted()]))
    item = recipe()
    item['workflow']['6']['inputs']['seed'] = 0
    item['bindings'].append({'source': 'settings.seed', 'node_id': '6', 'input': 'seed'})
    service.configure(OWNER, 'comfy_cloud', {'recipes': [item]})
    request = copy.deepcopy(REQUEST)
    request['settings']['seed'] = '-1'
    service.submit(OWNER, 'comfy_cloud', RID, request, LIMITS)
    seed = json.loads(http.calls[0][2]['body'])['workflow']['6']['inputs']['seed']
    assert type(seed) is int and 0 <= seed < 2**64
    service.submit(OWNER, 'comfy_cloud', RID, request, LIMITS)
    assert len(http.calls) == 1
    assert H._compile(item, request, run_seed=seed)[1][1] == seed
    request['settings']['seed'] = True
    assert not service.inspect(OWNER, 'comfy_cloud', request, LIMITS)['eligible']


def test_character_cannot_omit_required_reference_slot(tmp_path):
    item = recipe(image=True)
    item['feature_id'] = 'character-photoshoot'
    request = {'featureId': 'character-photoshoot', 'settings': {'instruction': 'A red coat', 'visualStyle': 'anime'}, 'files': {}}
    with pytest.raises(H.HostedComputeError):
        H._compile(item, request)


def test_h3_structured_sections_and_exact_geometry_binding(tmp_path):
    from webui.minimax_h3_product import compile_product_prompt
    item = recipe()
    item.update(feature_id='h3-text-video', output_kind='video')
    item['fixed_settings'] = {'aspect': 'landscape-preview', 'quality': 'quality', 'acceleration': 'stock'}
    request = {'featureId': 'h3-text-video', 'settings': {
        'integrated_multimodal_description': 'A blue balloon rises.',
        'overall_soundscape': 'Wind.', 'non_diegetic_music': 'None.',
        'aspect': 'landscape-preview', 'quality': 'quality', 'acceleration': 'stock'}, 'files': {}}
    values = H._compile(item, request)
    expected = compile_product_prompt(feature_id='h3-text-video', visual_class=None,
        settings={**request['settings'], 'seed': 0}, uploads={}, output_token='hosted')['5']['inputs']['prompt']
    assert values[0][1] == expected
    request['settings']['aspect'] = 'portrait-preview'
    with pytest.raises(H.HostedComputeError):
        H._compile(item, request)


def test_definitive_provider_rejection_does_not_become_uncertain(tmp_path):
    for provider, reply in [('comfy_cloud', response({'error': {'code': 'invalid_workflow'}}, 422)),
                             ('runninghub', response({'code': 433, 'msg': 'synthetic validator failure'}))]:
        service, http = configured(tmp_path / provider, provider, http=FakeHTTP([reply]))
        assert service.submit(OWNER, provider, RID, REQUEST, LIMITS)['state'] == 'rejected'
        assert service.recover(OWNER, provider, RID)['state'] == 'rejected'
        assert len(http.calls) == 1


def test_config_and_recipe_changes_invalidate_verified_auto(tmp_path):
    service, _ = configured(tmp_path, http=FakeHTTP([accepted(), completed(), response(png())]))
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    service.status(OWNER, 'comfy_cloud', RID)
    item = recipe()
    item['workflow']['6']['class_type'] = 'DifferentNode'
    service.configure(OWNER, 'comfy_cloud', {'recipes': [item]})
    assert not service.inspect(OWNER, 'comfy_cloud', REQUEST, LIMITS)['auto_eligible']


def test_credential_removal_does_not_lose_existing_binding(tmp_path):
    service, http = configured(tmp_path, http=FakeHTTP([accepted(), response({'id': REMOTE, 'status': 'running'})]))
    service.submit(OWNER, 'comfy_cloud', RID, REQUEST, LIMITS)
    service.configure(OWNER, 'comfy_cloud', {'clear_api_key': True})
    blocked = service.status(OWNER, 'comfy_cloud', RID)
    assert blocked['state'] == 'queued' and blocked['error_code'] == 'credential_required_for_recovery'
    assert len(http.calls) == 1
    service.configure(OWNER, 'comfy_cloud', {'api_key': KEY})
    assert service.recover(OWNER, 'comfy_cloud', RID)['state'] == 'running'
    assert len(http.calls) == 2


def test_documented_sdxl_recipe_imports_and_maps_real_form_defaults():
    import re
    doc = (Path(__file__).resolve().parents[1] / 'docs/hosted-compute.md').read_text()
    sample = json.loads(re.search(r'```json\n(.*?)\n```', doc, re.S).group(1))[0]
    item = H._recipe('comfy_cloud', sample)
    request = {'featureId': 'text2img', 'settings': {'positive': 'Synthetic blue cup', 'negative': 'synthetic negative',
        'width': 1088, 'height': 1920, 'steps': 8, 'cfg': 1.0, 'batchSize': 1, 'useDopsd': False, 'seed': -1}, 'files': {}}
    values = H._compile(item, request, run_seed=123)
    mapping = {(binding['node_id'], binding['input']): value for binding, value in values}
    assert mapping[('5', 'width')] == 1088 and mapping[('5', 'height')] == 1920
    assert mapping[('3', 'cfg')] == 1.0 and mapping[('7', 'text')] == 'synthetic negative'
    assert mapping[('3', 'seed')] == 123
