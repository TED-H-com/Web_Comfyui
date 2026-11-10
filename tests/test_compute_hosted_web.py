"""Real authenticated form bridge with synthetic hosted boundaries, never GPUs.

The shared fixture compiles the actual server helper bodies without importing
deployment configuration. Hosted recipe compilation is real; upload/create are
recorded substitutes. Provider wire transport has its own service tests.
"""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_compute_web import web_fixture, character_request, request_id


@pytest.fixture(params=['comfy_cloud', 'runninghub'])
def hosted_web(web_fixture, request):
    from hosted_compute import HostedComputeError, _compile, _recipe
    f, provider = web_fixture, request.param
    calls = SimpleNamespace(inspections=[], submissions=[], starts=0, compiled=[])

    def recipe(feature):
        settings = {
            'qwen21-text2img': {'aspectRatio': '1:1', 'megapixels': '1', 'steps': 25,
                              'cfg': 1, 'negative': '', 'seed': 4},
            'qwen21-edit': {'resolution': '1024'},
            'character-photoshoot': {'resolution': '1024', 'steps': 25, 'cfg': 1, 'negative': ''},
        }[feature]
        bindings = [{'source': 'prompt', 'node_id': '1', 'input': 'text'}]
        if feature != 'qwen21-text2img':
            bindings.append({'source': 'files.image', 'node_id': '2', 'input': 'image'})
        value = {'feature_id': feature, 'recipe_id': 'synthetic-' + feature,
                 'label': 'Synthetic bridge recipe', 'output_kind': 'image',
                 'estimated_cost': 1, 'fixed_settings': settings, 'bindings': bindings}
        if provider == 'comfy_cloud':
            value['workflow'] = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': ''}},
                                 '2': {'class_type': 'LoadImage', 'inputs': {'image': ''}}}
        else:
            value['workflow_id'] = 'synthetic-workflow'
        return _recipe(provider, value)

    class Hosted:
        def inspect(self, owner, lane, effective, limits):
            assert owner == f.user['email'] and lane == provider
            assert set(effective) == {'featureId', 'settings', 'files'}
            assert all(value == {} for value in effective['files'].values())
            calls.inspections.append(copy.deepcopy(effective))
            try:
                _compile(recipe(effective['featureId']), effective)
            except HostedComputeError:
                return {'eligible': False, 'reasons': ['hosted_settings_unsupported']}
            return {'eligible': True, 'auto_eligible': False, 'estimated_cost': 1,
                    'currency': 'comfy_credits' if provider == 'comfy_cloud' else 'runninghub_coins'}

        def submit(self, owner, lane, ident, effective, limits, *, resolved_files):
            assert owner == f.user['email'] and lane == provider
            assert set(effective) == {'featureId', 'settings', 'files'}
            assert set(effective['files']) == set(resolved_files)
            assert all(Path(path).is_relative_to(f.input_root) for path in resolved_files.values())
            compiled = _compile(recipe(effective['featureId']), effective,
                                {slot: 'synthetic-provider-image' for slot in resolved_files})
            calls.compiled.append(compiled)
            calls.submissions.append((owner, lane, ident, copy.deepcopy(effective),
                                      {slot: Path(path).read_bytes() for slot, path in resolved_files.items()}))
            return {'state': 'queued', 'downstream_id': ident, 'provider_id': provider,
                    'recipe_id': 'synthetic-' + effective['featureId'], 'artifacts': []}

        def start(self):
            calls.starts += 1

    hosted = Hosted()
    f.web.hosted = hosted
    f.server.get_hosted_compute_service = lambda: hosted
    f.globals['get_hosted_compute_service'] = lambda: hosted
    f.hosted, f.hosted_calls, f.provider = hosted, calls, provider
    f.hosted_limits = {'max_minutes': 5, 'max_comfy_credits': 5, 'max_runninghub_coins': 5}
    return f


def run(f, body, ident=None):
    return f.web._run(body, f.provider, ident or request_id(), f.hosted_limits)


def upload(f):
    return {'featureId': 'qwen21-edit', 'settings': {'instruction': 'Make the square red', 'resolution': '1024'},
            'files': {'image': {'name': 'synthetic.png',
                      'dataUrl': 'data:image/png;base64,' + base64.b64encode(f.media).decode()}}}


def test_hosted_text_preserves_explicit_visual_class_and_strict_request(hosted_web):
    f = hosted_web
    body = {**f.request, 'computeVisualClass': 'realistic',
            'submittedBy': {'email': 'forged@example.invalid'}}
    assert f.web.inspect_hosted(f.provider, f.web.owner, body, f.hosted_limits)['eligible']
    ident = request_id()
    result = run(f, body, ident)
    assert result['state'] == 'queued' and result['downstream_id'] == ident
    owner, provider, rid, effective, files = f.hosted_calls.submissions[0]
    assert (owner, provider, rid) == (f.user['email'], f.provider, ident)
    assert effective['settings']['visualStyle'] == 'realistic'
    assert effective['settings']['positive'] == f.request['settings']['positive']
    assert 'realistic photographic style' in f.hosted_calls.compiled[0][0][1]
    assert not files and f.stages == [] and f.submits == []
    assert 'forged@example.invalid' not in json.dumps(effective)
    assert f.hosted_calls.starts == 1


def test_hosted_owned_character_preserves_verified_identity_and_bound_asset(hosted_web):
    f = hosted_web
    body = character_request()
    body['computeVisualClass'] = 'anime'
    body['settings'].update({'_characterId': 'forged-character', '_characterVisualClass': 'realistic',
                             '_characterReference': {'path': '/forged/private/path'}, 'prefix': 'forged/path'})
    assert f.web.inspect_hosted(f.provider, f.web.owner, body, f.hosted_limits)['eligible']
    assert list(f.input_root.iterdir()) == []
    result = run(f, body)
    assert result['state'] == 'queued'
    owner, _, _, effective, files = f.hosted_calls.submissions[0]
    assert owner == f.user['email']
    assert effective['settings']['_characterId'] == f.character['id']
    assert effective['settings']['_characterVisualClass'] == 'anime'
    assert effective['settings']['visualStyle'] == 'anime'
    assert effective['settings']['_characterActionId'] == 'photoshoot'
    assert effective['settings']['_characterVariantId'] == body['variantId']
    assert files == {'image': f.media}
    assert '全畫面採用一致的動漫畫風' in f.hosted_calls.compiled[0][0][1]
    assert 'forged' not in json.dumps(effective)
    assert f.stages == [] and f.submits == []


def test_hosted_raw_upload_skips_local_readiness_builder_and_upload(hosted_web):
    f = hosted_web
    assert run(f, upload(f))['state'] == 'queued'
    assert f.hosted_calls.submissions[0][4] == {'image': f.media}
    assert f.stages == [] and f.submits == []
    staged = list(f.input_root.glob('castle_colab_*.png'))
    assert len(staged) == 1 and staged[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('denial', ['foreign_character', 'foreign_input', 'internal_path', 'unknown_slot'])
def test_hosted_denied_sources_never_reach_upload_or_provider(hosted_web, denial):
    from compute_routing import ComputeRejected
    f = hosted_web
    body = upload(f)
    if denial == 'foreign_character':
        f.character['ownerEmail'] = 'other@example.invalid'
        body = character_request()
    elif denial == 'foreign_input':
        (f.input_root / 'other-user.png').write_bytes(f.media)
        body['files'] = {'image': {'serverFile': 'other-user.png'}}
    elif denial == 'internal_path':
        body['files'] = {'image': {'_characterAssetPath': str(f.avatar)}}
    else:
        body['files']['unexpected'] = body['files']['image']
    before = sorted(path.name for path in f.input_root.iterdir())
    assert not f.web.inspect_hosted(f.provider, f.web.owner, body, f.hosted_limits)['eligible']
    with pytest.raises(ComputeRejected):
        run(f, body)
    assert sorted(path.name for path in f.input_root.iterdir()) == before
    assert not f.hosted_calls.inspections and not f.hosted_calls.submissions
    assert f.stages == [] and f.submits == []


@pytest.mark.parametrize('character', [False, True])
def test_hosted_contradicting_style_rejected_before_staging(hosted_web, character):
    from compute_routing import ComputeRejected
    f = hosted_web
    body = character_request() if character else upload(f)
    body['computeVisualClass'] = 'realistic'
    body['settings']['visualStyle'] = 'anime'
    assert not f.web.inspect_hosted(f.provider, f.web.owner, body, f.hosted_limits)['eligible']
    with pytest.raises(ComputeRejected):
        run(f, body)
    assert not f.hosted_calls.submissions and not f.hosted_calls.inspections
    assert list(f.input_root.iterdir()) == []


def test_hosted_lost_submit_reply_keeps_uuid_and_never_replays(hosted_web, monkeypatch):
    f = hosted_web
    attempted = []
    def uncertain(owner, lane, ident, *args, **kwargs):
        attempted.append((owner, lane, ident))
        raise TimeoutError('synthetic lost accepted reply')
    monkeypatch.setattr(f.hosted, 'submit', uncertain)
    ident = request_id()
    first = f.web.router.submit(f.web.owner, ident, f.request, f.provider, f.hosted_limits)
    second = f.web.router.submit(f.web.owner, ident, f.request, f.provider, f.hosted_limits)
    assert first['state'] == second['state'] == 'submission_uncertain'
    assert first['lane'] == second['lane'] == f.provider
    assert attempted == [(f.web.owner, f.provider, ident)]
