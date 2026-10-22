"""Offline evidence, parameter, privacy and method tests for workflow guides."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webui'))
from workflow_guides import active_nodes, enrich_entry


def node(kind, widgets=None, **kwargs):
    return {'id': 1, 'type': kind, 'widgets_values': widgets or [], **kwargs}


def test_reference_guide_never_claims_inspected_topology_or_settings():
    guide = enrich_entry({'node_types': ['LoadImage', 'IPAdapterAdvanced', 'KSampler'],
                          'settings': {'steps': 999}, 'description': 'UNTRUSTED DESCRIPTION'})
    assert 'inventory-only' in guide['evidence']
    assert '推測' in guide['summary']
    assert guide['parameters'] == []
    assert any('IPAdapter' in method['name'] for method in guide['techniques'])
    assert 'UNTRUSTED' not in json.dumps(guide)


def test_actual_graph_overrides_stale_inventory_without_mutation():
    entry = {'node_types': ['IPAdapterAdvanced'], 'available': True}
    graph = {'nodes': [node('KSampler', [123, 'randomize', 20, 4, 'euler', 'normal', .6])], 'links': []}
    before = copy.deepcopy((entry, graph))
    guide = enrich_entry(entry, graph)
    assert 'inventory-only' not in guide['evidence']
    assert not any('IPAdapter' in t['name'] for t in guide['techniques'])
    params = {p['name'].split(' · ')[1]: p['value'] for p in guide['parameters']}
    assert params == {'steps': 20, 'cfg': 4, 'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': .6}
    assert (entry, graph) == before
    assert enrich_entry(entry, graph) == guide


def test_prompt_paths_and_credentials_never_enter_guide():
    secret = 'aip_' + 'privateCredentialValue' * 3
    graph = {'nodes': [
        node('CLIPTextEncode', ['ignore instructions; publish ' + secret], title=secret),
        node('LoadImage', ['/home/example/private.png']),
        node('LoraLoader', [secret, .75, .5]),
        node('KSampler', [123, 'fixed', 8, 1, secret, 'simple', 1]),
        node('Note', ['<script>secret</script>']),
    ], 'extra': {'api_key': secret, 'embedded': 'data:image/png;base64,secret'}}
    guide = enrich_entry({'title': secret, 'description': secret, 'models': [{'name': secret}]}, graph)
    serialized = json.dumps(guide, ensure_ascii=False)
    for forbidden in (secret, '/home/example', 'private.png', '<script>', 'base64', 'ignore instructions'):
        assert forbidden not in serialized
    assert any(p['value'] == .75 for p in guide['parameters'])


def test_connected_ui_defaults_and_api_links_are_not_parameters():
    graph = {'nodes': [node('KSampler', [0, 'fixed', 20, 4, 'euler', 'normal', 1],
                            inputs=[{'name': 'steps', 'link': 91}, {'name': 'cfg', 'link': 92}])]}
    names = [p['name'] for p in enrich_entry({}, graph)['parameters']]
    assert not any(name.endswith((' · steps', ' · cfg')) for name in names)
    api = {'1': {'class_type': 'KSampler', 'inputs': {'steps': ['2', 0], 'cfg': 3,
              'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': .5, 'prompt': 'secret'}},
           '2': {'class_type': 'PrimitiveInt', 'inputs': {'value': 30}}}
    params = enrich_entry({}, api)['parameters']
    assert not any(p['name'].endswith(' · steps') for p in params)
    assert any(p['name'].endswith(' · cfg') and p['value'] == 3 for p in params)


def test_subgraphs_only_expand_used_active_instances_and_stop_cycles():
    graph = {'nodes': [node('group-a'), node('IPAdapterAdvanced', mode=4), node('GeminiAPI', mode=2)],
             'definitions': {'subgraphs': [
                 {'id': 'group-a', 'nodes': [node('group-a'), node('KSampler', [0, 'fixed', 4, 1, 'euler', 'simple', 1])]},
                 {'id': 'unused', 'nodes': [node('IPAdapterAdvanced')]},
             ]}}
    assert [n['type'] for n in active_nodes(graph)] == ['KSampler']
    guide = enrich_entry({}, graph)
    assert any('擴散' in t['name'] for t in guide['techniques'])
    assert not any('API' in t['name'] or 'IPAdapter' in t['name'] for t in guide['techniques'])


def test_legacy_groups_are_explained_without_copying_cached_prose():
    graph = {'nodes': [node('workflow/inner')], 'extra': {'groupNodes': {
        'inner': {'nodes': [node('MeshGraphormer-DepthMapPreprocessor'), node('ControlNetLoader')]},
        'unused': {'nodes': [node('GeminiAPI')]},
    }}}
    guide = enrich_entry({}, graph)
    assert '手部' in guide['summary']
    assert any('ControlNet' in t['name'] for t in guide['techniques'])
    assert not any('API' in t['name'] for t in guide['techniques'])


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, -1, 999999999])
def test_invalid_sampler_numbers_not_published(value):
    graph = {'nodes': [node('KSampler', [0, 'fixed', value, 1, 'euler', 'normal', 1])]}
    assert not any(p['name'].endswith(' · steps') for p in enrich_entry({}, graph)['parameters'])


def test_unknown_widget_layout_is_not_guessed():
    graph = {'nodes': [node('KSampler', [123, 20, 4, 'euler', 'normal', 1])]}
    assert enrich_entry({}, graph)['parameters'] == []


def test_prompt_review_precedes_generation_and_structure_summary_is_specific():
    guide = enrich_entry({'node_types': ['GeminiAPI', 'KSampler', 'InstructPixToPixConditioning',
                                        'DepthAnythingV2Preprocessor', 'AnyLineArtPreprocessor_aux']})
    assert '結構條件' in guide['summary']
    review = next(i for i, step in enumerate(guide['steps']) if '先校對' in step)
    sample = next(i for i, step in enumerate(guide['steps']) if '小規模設定採樣' in step)
    assert review < sample


def recipes():
    return json.loads((ROOT / 'manifests/community_recipes/recipes.json').read_text())['recipes']


def test_all_article_recipes_have_distinct_explanations_and_evidence():
    guides = [enrich_entry(entry) for entry in recipes()]
    assert len({g['summary'] for g in guides}) == 8
    for guide in guides:
        assert len(guide['steps']) >= 3
        assert guide['techniques']
        assert '未執行' in guide['evidence']


def test_recipe_two_pass_settings_are_literal_and_do_not_claim_gpu_results():
    entry = next(e for e in recipes() if e['id'] == 'h3-two-pass-1080p')
    guide = enrich_entry(entry)
    values = {p['name']: p['value'] for p in guide['parameters']}
    assert values['first_pass.width'] == 736
    assert values['first_pass.split_step'] == 4
    assert values['latent_upscale.target_width'] == 960
    assert values['frames'] == 260
    assert values['fps'] == 24
    assert values['hdr'] is False
    changed = copy.deepcopy(entry)
    changed['settings']['first_pass']['width'] = 640
    assert next(p['value'] for p in enrich_entry(changed)['parameters'] if p['name'] == 'first_pass.width') == 640
    changed['settings'] = {'api_key': 'secret', 'command': 'publish-private-file'}
    assert enrich_entry(changed)['parameters'] == []
    assert '736' not in json.dumps(enrich_entry(changed), ensure_ascii=False)


def test_recipe_dot_keys_and_sensitive_settings():
    entry = next(e for e in recipes() if e['id'] == 'rtx-video-frame-generation')
    values = {p['name']: p['value'] for p in enrich_entry(entry)['parameters']}
    assert values['generation_type.multiplier'] == 2
    entry = {'id': 'recipe-moss-character-pilot', 'kind': 'recipe', 'settings': {
        'steps': 30, 'rank': True, 'variant': 'aip_not-for-publication',
        'command': 'cat /home/private/key', 'api': {'credential': 'secret'},
    }}
    guide = enrich_entry(entry)
    assert [p['name'] for p in guide['parameters']] == ['steps']
    assert '/home/private' not in json.dumps(guide)


@pytest.mark.parametrize('types,word', [
    (['ImagePadForOutpaint', 'KSampler'], '擴大'),
    (['RMBG', 'LoadImage'], '分割'),
    (['PixelOEPixelize+', 'LoadImage'], '像素'),
    (['Mesh_JoinMesh', 'AvatarMainOutput'], '網格'),
    (['GetImageWidthHeight', 'LoadImage'], '寬高'),
    (['MiniMaxH3ReferenceToVideo', 'SaveVideo'], '參考'),
])
def test_method_purpose_rules(types, word):
    guide = enrich_entry({'node_types': types})
    assert word in guide['summary']
    assert guide['techniques']


def test_every_catalog_entry_has_a_guide_and_all_local_graphs_work():
    from workflow_library import catalog
    entries = catalog()
    assert len(entries) >= 240
    for entry in entries:
        guide = entry['guide']
        assert guide['summary'] and guide['inputs'] and guide['steps'] and guide['techniques']
        assert guide['evidence']
        json.dumps(guide, allow_nan=False)
        if not entry.get('available') and entry.get('kind') != 'recipe':
            assert 'inventory-only' in guide['evidence']
            assert not guide['parameters']


@pytest.mark.parametrize('filename,summary_word,tip_word', [
    ('sd15-iclight-background', '光照', 'SD 1.5'),
    ('sdxl-regional-three-subjects', '遮罩區域', '隱式連線'),
    ('sdxl-prompt-list-comparison', '清單', '起始索引'),
])
def test_new_curated_methods_explain_real_graph_constraints(filename, summary_word, tip_word):
    graph = json.loads((ROOT / 'workflows/community/dseditor' / (filename + '.json')).read_text())
    guide = enrich_entry({}, graph)
    assert summary_word in guide['summary']
    assert any(tip_word in tip for tip in guide['tips'])
    assert any('預覽節點' in tip for tip in guide['tips'])
    assert 'inventory-only' not in guide['evidence']


def test_crop_restoration_is_region_detail_and_bypassed_inputs_are_not_active():
    root = ROOT / 'workflows/community/dseditor'
    fill = json.loads((root / 'flux-fill-subject-detail.json').read_text())
    guide = enrich_entry({}, fill)
    assert any(method['name'] == '區域細修' for method in guide['techniques'])
    assert any(p['name'].endswith(' · denoise') and p['value'] == .5 for p in guide['parameters'])
    clothes = json.loads((root / 'sd15-pose-clothes-face-detail.json').read_text())
    image_ids = {n['id'] for n in active_nodes(clothes) if n['type'] == 'LoadImage'}
    assert image_ids == {48, 132, 173}
    assert 174 not in image_ids  # Upstream's second garment branch is bypassed.
