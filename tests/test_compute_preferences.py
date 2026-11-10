from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_preferences import ComputePreferences
from compute_routing import ComputeRoutingError, ComputeAdapter, ComputeRouter
from test_compute_web import web_fixture


def test_defaults_owner_isolation_and_strict_options(tmp_path):
    store = ComputePreferences(tmp_path / 'prefs.sqlite3')
    assert store.get('alice')['default_placement'] == 'local'
    body = {'default_placement': 'auto', 'feature_placements': {'text2img': 'colab'}}
    assert store.save('alice', body, {'text2img'}) == body
    assert store.placement('alice', 'text2img') == 'colab'
    assert store.placement('alice', 'img2img') == 'auto'
    assert store.get('bob')['default_placement'] == 'local'
    assert ComputePreferences(store.path).get('alice') == body
    assert store.path.stat().st_mode & 0o077 == 0
    for invalid in ({**body, 'owner': 'bob'}, {**body, 'default_placement': 'unknown'},
                    {**body, 'feature_placements': {'hidden-admin-feature': 'local'}}):
        with pytest.raises(ComputeRoutingError):
            store.save('alice', invalid, {'text2img'})


def test_replay_keeps_original_platform_after_preferences_change(web_fixture, tmp_path):
    f = web_fixture
    calls = []
    def accepted(lane):
        def submit(owner, rid, request, limits):
            calls.append(lane)
            return {'state': 'queued', 'downstream_id': rid}
        return submit
    f.web.router = ComputeRouter(tmp_path / 'router.sqlite3', {
        'local': ComputeAdapter(lambda *a: {'eligible': True, 'queue_depth': 0, 'currency': 'local'}, accepted('local')),
        'colab': ComputeAdapter(lambda *a: {'eligible': True, 'queue_depth': 0, 'currency': 'compute_units'}, accepted('colab')),
    })
    body = {'request_id': str(uuid.uuid4()), 'request': f.request}
    first = f.web.submit(body)
    assert first['lane'] == 'local'
    f.web.preferences.save(f.web.owner, {'default_placement': 'colab', 'feature_placements': {}}, {'qwen21-text2img'})
    assert f.web.submit(body)['lane'] == 'local'
    assert calls == ['local']
    fresh = {**body, 'request_id': str(uuid.uuid4())}
    assert f.web.submit(fresh)['lane'] == 'colab'
    assert f.web.submit({**fresh, 'request_id': str(uuid.uuid4()), 'placement': 'local'})['lane'] == 'local'
    with pytest.raises(ComputeRoutingError):
        f.web.submit({**body, 'placement': 'colab'})


def test_inventory_is_not_a_readiness_or_gpu_verification_claim():
    from compute_capabilities import capabilities
    features = [{'id': 'text2img', 'title': '文生圖', 'builder': 'zimage'},
                {'id': 'custom-tool', 'title': '其他工具', 'builder': 'custom'},
                {'id': 'planned', 'builder': None}]
    hosted = {'providers': [{'provider': 'comfy_cloud', 'enabled': True, 'credential_configured': True,
                'recipes': [{'feature_id': 'text2img', 'recipe_id': 'native.v1', 'verified': False}]}]}
    result = capabilities(features, hosted, {'templates': [{'feature_id': 'text2img'}]})
    assert result['readiness_checked'] is False
    rows = {row['id']: row['providers'] for row in result['features']}
    assert rows['text2img']['comfy_cloud']['status'] == 'configured'
    assert rows['text2img']['comfy_cloud']['verified'] is False
    assert rows['text2img']['runninghub']['status'] == 'unconfigured'
    assert rows['custom-tool']['colab']['status'] == 'unsupported'
    assert all(row['status'] == 'unsupported' for row in rows['planned'].values())
