"""Native graph/form parity and deterministic mapping tests; no runtime imports."""
import ast
import copy
import json
from pathlib import Path

import pytest

from webui import compute_recipe_catalog as C
from webui import hosted_compute as H
from webui import minimax_h3_product as P
from webui import qwen_image21 as Q
from scripts.app_guide_catalog import registry

ROOT = Path(__file__).resolve().parents[1]
CATALOG = C.recipe_catalog()
TEMPLATES = {item['id']: item for item in CATALOG['templates']}
FEATURES = {item['id']: item for item in registry(ROOT)[1]}
FEATURES['character-photoshoot'] = Q.character_feature_definition()


def defaults(feature):
    settings = {}
    for field in FEATURES[feature]['fields']:
        name = field['id']
        if field['type'] == 'aspect':
            width, height = field['default'].split('x')
            settings.update(width=int(width), height=int(height))
        elif 'default' in field:
            settings[name] = field['default']
        elif field.get('required'):
            settings[name] = 'Synthetic scene, subject and sound.'
    for field in ('positive', 'instruction'):
        if field in settings:
            settings[field] = 'Synthetic person waves beside a blue wall.'
    return settings


def request(template, settings=None):
    return {'featureId': template['feature_id'], 'settings': defaults(template['feature_id']) if settings is None else settings,
            'files': {slot: {} for slot in template['input_slots']}}


def compile_graph(template, submitted, seed=73):
    graph = copy.deepcopy(template['recipe']['workflow'])
    uploaded = {slot: slot + ('.mp4' if slot.startswith('video') else '.wav' if slot.startswith('audio') or slot == 'voice' else '.png') for slot in submitted['files']}
    for binding, value in H._compile(template['recipe'], submitted, uploaded, run_seed=seed):
        graph[binding['node_id']]['inputs'][binding['input']] = value
    return graph


def test_catalog_is_fresh_json_and_all_features_have_reason_or_recipe():
    catalog = C.recipe_catalog()
    assert len(catalog['templates']) == 31
    assert len({t['id'] for t in catalog['templates']}) == 31
    supported = {t['feature_id'] for t in catalog['templates']}
    unsupported = {u['feature_id'] for u in catalog['unsupported']}
    assert set(FEATURES) == supported | unsupported
    assert not supported & unsupported
    json.dumps(catalog, allow_nan=False)
    catalog['templates'][0]['recipe']['workflow'].clear()
    assert C.recipe_catalog()['templates'][0]['recipe']['workflow']
    assert all(t['validation'] == 'contract_tested' and t['cost_requires_input'] and t['requirements'] for t in CATALOG['templates'])


@pytest.mark.parametrize('template', CATALOG['templates'], ids=lambda t: t['id'])
def test_every_template_matches_actual_form_defaults_and_file_arity(template):
    H._recipe('comfy_cloud', template['recipe'])
    graph = compile_graph(template, request(template))
    assert all(node['class_type'] not in {'LoadImagePath', 'VHS_LoadVideoPath'} for node in graph.values())
    for node in graph.values():
        for value in node['inputs'].values():
            if isinstance(value, list):
                assert len(value) == 2 and value[0] in graph and type(value[1]) is int
    source_slots = [binding['source'][6:] for binding in template['recipe']['bindings'] if binding['source'].startswith('files.')]
    assert source_slots == template['input_slots']
    assert len(source_slots) <= 8
    seed_nodes = [node['inputs'].get('seed', node['inputs'].get('noise_seed')) for node in graph.values()
                  if 'seed' in node['inputs'] or 'noise_seed' in node['inputs']]
    assert seed_nodes == ([] if template['feature_id'] in {'lipsync', 'video-loop'} else [73])
    rh_recipe = copy.deepcopy(template['recipe'])
    rh_recipe.pop('workflow')
    rh_recipe['workflow_id'] = '1910246754753896450'
    H._recipe('runninghub', rh_recipe)
    # Same mapping values apply to RunningHub nodeInfoList after graph publish.
    assert H._compile(rh_recipe, request(template), {slot: slot for slot in source_slots}, run_seed=73)


def isolated_builder(name, input_root=None):
    """Run only a reviewed pure builder AST, never import/execute server.py."""
    tree = ast.parse((ROOT / 'webui/server.py').read_text())
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {'clamp_int': lambda v, d, low, high: max(low, min(high, int(d if v is None else v))),
                 'clamp_float': lambda v, d, low, high: max(low, min(high, float(d if v is None else v))),
                 'normalize_seed': lambda _v: 73, 'output_prefix': lambda *_a: 'test-output',
                 'ZIMAGE_DEFAULT_PROMPT': 'unused', 'ZIMAGE_DEFAULT_NEGATIVE': C.Z_NEGATIVE,
                 'WAN_DEFAULT_NEGATIVE': C.WAN_NEGATIVE, 'COMFY_INPUT': input_root}
    module = ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[]))
    exec(compile(module, '<isolated native builder>', 'exec'), namespace)
    return namespace[name]


def without_prefix(graph):
    graph = copy.deepcopy(graph)
    for node in graph.values():
        if 'filename_prefix' in node['inputs']:
            node['inputs']['filename_prefix'] = 'normalized'
    return graph


@pytest.mark.parametrize('tid,builder', [('native-zimage-text2img-v1', 'zimage_prompt'),
    ('native-flux-kontext-v1', 'flux_kontext_prompt'), ('native-wan21-image-video-v1', 'wan_i2v_prompt')])
def test_existing_native_builder_exact_graph_parity(tid, builder):
    template = TEMPLATES[tid]
    submitted = request(template)
    uploads = {slot: slot + '.png' for slot in submitted['files']}
    expected = isolated_builder(builder)(submitted['settings'], uploads)
    assert without_prefix(compile_graph(template, submitted)) == without_prefix(expected)


@pytest.mark.parametrize('count', [1, 2, 3, 4])
def test_qwen_multireference_order_and_custom_negative_are_native(count):
    template = TEMPLATES[f'native-qwen21-edit-{count}-v1']
    submitted = request(template)
    submitted['settings'].update(instruction='Use <image1> as subject and preserve the listed references.',
                                 negative='custom watermark exclusion', resolution='2048', steps=31, cfg=2.4)
    expected = Q.compile_prompt(submitted['settings'], {slot: slot+'.png' for slot in submitted['files']}, edit=True, seed=73, prefix='test')
    assert without_prefix(compile_graph(template, submitted)) == without_prefix(expected)
    graph = compile_graph(template, submitted)
    assert [graph['4']['inputs'][f'images.image_{i}'] for i in range(1, count+1)] == [[str(10+i), 0] for i in range(1, count+1)]
    missing = copy.deepcopy(submitted)
    missing['files'].pop(next(iter(missing['files'])))
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, missing)
    extra = copy.deepcopy(submitted)
    extra['files']['image5'] = {}
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, extra)


@pytest.mark.parametrize('aspect', list(Q.ASPECTS))
@pytest.mark.parametrize('megapixels', ['1', '2', '4'])
def test_qwen_canvas_exact_native_options(aspect, megapixels):
    template = TEMPLATES['native-qwen21-text2img-v1']
    submitted = request(template)
    submitted['settings'].update(aspectRatio=aspect, megapixels=megapixels, negative='custom negative')
    expected = Q.compile_prompt(submitted['settings'], {}, edit=False, seed=73, prefix='test')
    assert without_prefix(compile_graph(template, submitted)) == without_prefix(expected)


@pytest.mark.parametrize('style', ['anime', 'realistic'])
def test_character_identity_and_custom_negative_preserved(style):
    template = TEMPLATES['native-qwen21-character-v1']
    submitted = request(template)
    submitted['settings'].update(visualStyle=style, _characterVisualClass=style, _characterId='synthetic-owned',
                                 _characterReference={'private_receipt': 'not-a-prompt'}, negative='custom negative')
    graph = compile_graph(template, submitted)
    expected = Q.compile_character_prompt(submitted['settings'], {'image': 'image.png'}, seed=73, prefix='test')
    assert without_prefix(graph) == without_prefix(expected)
    assert 'not-a-prompt' not in graph['4']['inputs']['prompt']
    submitted['settings']['visualStyle'] = 'realistic' if style == 'anime' else 'anime'
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, submitted)


@pytest.mark.parametrize('tid', [t['id'] for t in CATALOG['templates'] if t['feature_id'].startswith('h3-')])
def test_h3_graph_matches_pure_product_compiler_for_native_sections(tid):
    template = TEMPLATES[tid]
    submitted = request(template)
    graph = compile_graph(template, submitted)
    uploads = {slot: slot + ('.mp4' if slot.startswith('video') else '.wav' if slot.startswith('audio') or slot == 'voice' else '.png') for slot in submitted['files']}
    expected = P.compile_product_prompt(feature_id=template['feature_id'], visual_class=None,
        settings={**submitted['settings'], 'seed': 73}, uploads=uploads, output_token='test')
    assert without_prefix(graph) == without_prefix(expected)


@pytest.mark.parametrize('aspect,dimensions', list(C.ASPECTS.items()))
@pytest.mark.parametrize('frames', [124, 226, 362])
def test_h3_exact_geometry_and_legal_frame_lattice(aspect, dimensions, frames):
    template = TEMPLATES['native-h3-first-last-v1']
    submitted = request(template)
    submitted['settings'].update(aspect=aspect, frames=str(frames))
    graph = compile_graph(template, submitted)
    node = graph['5']['inputs']
    assert (node['width'], node['height'], node['length']) == (*dimensions, frames)
    expected = P.compile_product_prompt(feature_id='h3-image-video', visual_class=None,
        settings={**submitted['settings'], 'seed': 73}, uploads={'image': 'image.png', 'last_frame': 'last_frame.png'}, output_token='test')
    assert node['prompt'] == expected['5']['inputs']['prompt']
    for invalid in ('123', 123, True, 124.0, None, ''):
        submitted['settings']['frames'] = invalid
        with pytest.raises(H.HostedComputeError):
            compile_graph(template, submitted)


@pytest.mark.parametrize('style', ['anime', 'realistic'])
def test_generic_h3_visual_style_does_not_become_a_character_request(style):
    template = TEMPLATES['native-h3-first-v1']
    submitted = request(template)
    submitted['settings']['visualStyle'] = style
    graph = compile_graph(template, submitted)
    prompt = graph['5']['inputs']['prompt']
    assert ('2D anime' if style == 'anime' else 'realistic photographic') in prompt
    assert 'Synthetic scene' in prompt and 'overall_soundscape:' in prompt
    assert P.ANIME_GUARDRAIL not in prompt and P.REALISTIC_GUARDRAIL not in prompt


@pytest.mark.parametrize('style,motion', [('anime', 'gentle'), ('realistic', 'natural')])
def test_bound_h3_character_keeps_native_guardrails(style, motion):
    template = TEMPLATES['native-h3-first-v1']
    submitted = request(template, {'description': 'A synthetic adult presenter waves.', 'motionProfile': motion,
        'aspect': 'portrait', 'frames': '124', 'seed': '-1', 'acceleration': 'stock',
        'visualStyle': style, '_characterVisualClass': style, '_characterId': 'synthetic-owned'})
    graph = compile_graph(template, submitted)
    expected = P.compile_product_prompt(feature_id='h3-image-video', visual_class=style,
        settings={**submitted['settings'], 'seed': 73}, uploads={'image': 'image.png'}, output_token='test')
    assert without_prefix(graph) == without_prefix(expected)
    submitted['settings']['visualStyle'] = 'realistic' if style == 'anime' else 'anime'
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, submitted)


def test_defaults_only_when_absent_and_never_hide_user_values():
    template = TEMPLATES['native-zimage-text2img-v1']
    minimal = request(template, {'positive': 'Synthetic blue cup'})
    graph = compile_graph(template, minimal)
    assert graph['5']['inputs']['text'] == C.Z_NEGATIVE
    assert graph['6']['inputs']['width'] == 1088 and graph['7']['inputs']['steps'] == 8
    minimal['settings'].update(negative='', width=1024, height=1024, steps=11, cfg=2.7)
    graph = compile_graph(template, minimal)
    assert graph['5']['inputs']['text'] == ''
    assert graph['6']['inputs']['width'] == 1024 and graph['7']['inputs']['steps'] == 11
    assert graph['7']['inputs']['cfg'] == 2.7
    minimal['settings']['useDopsd'] = True
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, minimal)


@pytest.mark.parametrize('value', [None, '', True, 1024, '4096'])
def test_qwen_resolution_never_silently_falls_back(value):
    template = TEMPLATES['native-qwen21-edit-1-v1']
    submitted = request(template)
    submitted['settings']['resolution'] = value
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, submitted)


@pytest.mark.parametrize('binding_change', [
    {'default': {}}, {'default': float('nan')}, {'value_map': {}}, {'value_map': []},
    {'value_map': [{'from': '1', 'to': []}]}, {'value_map': [{'from': '1', 'to': float('inf')}]},
    {'value_map': [{'from': '1', 'to': 1}, {'from': '1', 'to': 2}]},
    {'value_map': [{'from': '1', 'to': 1, 'fallback': 2}]},
    {'value_map': [{'from': '1', 'to': 1}], 'default': '2'},
])
def test_malformed_finite_maps_and_defaults_fail_configuration(binding_change):
    recipe = copy.deepcopy(TEMPLATES['native-qwen21-text2img-v1']['recipe'])
    recipe['bindings'][-1].update(binding_change)
    with pytest.raises(H.HostedComputeError):
        H._recipe('comfy_cloud', recipe)


def test_defaults_maps_not_allowed_for_prompt_or_files_and_conflicting_defaults_rejected():
    recipe = copy.deepcopy(TEMPLATES['native-h3-first-v1']['recipe'])
    for index in (0, len(recipe['bindings'])-1):
        broken = copy.deepcopy(recipe)
        broken['bindings'][index]['default'] = 'do-not-replace'
        with pytest.raises(H.HostedComputeError):
            H._recipe('comfy_cloud', broken)
    recipe['bindings'][2]['default'] = 'portrait'
    with pytest.raises(H.HostedComputeError):
        H._recipe('comfy_cloud', recipe)


def test_seed_uint64_precision_and_default_minus_one():
    template = TEMPLATES['native-h3-text-v1']
    submitted = request(template)
    submitted['settings']['seed'] = '18446744073709551615'
    assert compile_graph(template, submitted)['6']['inputs']['noise_seed'] == 2**64-1
    submitted['settings'].pop('seed')
    assert compile_graph(template, submitted, seed=993)['6']['inputs']['noise_seed'] == 993


def test_catalog_does_not_import_server_or_read_runtime_files():
    # This subprocess has only a guarded import: neither source construction nor
    # pure Qwen/H3 builders may open files or import the production HTTP module.
    import subprocess, sys
    code = '''
import builtins
from webui.compute_recipe_catalog import recipe_catalog
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert name not in ('server', 'webui.server')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
builtins.open = lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected file read'))
assert len(recipe_catalog()['templates']) == 31
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('tid,builder', [('adapted-wav2lip-upload-v1', 'lipsync_prompt'),
    ('adapted-vhs-video-loop-v1', 'video_loop_prompt'), ('adapted-scail-animate-upload-v1', 'scail_dance_prompt')])
def test_adapted_video_graph_only_changes_loader_and_prefix(tmp_path, tid, builder):
    template = TEMPLATES[tid]
    submitted = request(template)
    uploads = {slot: slot + ('.mp4' if slot == 'video' else '.wav' if slot == 'voice' else '.png') for slot in submitted['files']}
    expected = isolated_builder(builder, tmp_path)(submitted['settings'], uploads)
    for node in expected.values():
        if node['class_type'] == 'VHS_LoadVideoPath':
            node['class_type'] = 'VHS_LoadVideo'
            node['inputs']['video'] = 'video.mp4'
    assert template['adaptation'] == 'upload_backed_video_loader'
    assert without_prefix(compile_graph(template, submitted)) == without_prefix(expected)
    assert not any(node['class_type'].endswith('Path') for node in compile_graph(template, submitted).values())


def test_lipsync_uploaded_voice_is_mapped_and_library_id_is_not_discarded():
    template = TEMPLATES['adapted-wav2lip-upload-v1']
    submitted = request(template)
    submitted['settings'].update(mode='repetitive', maxFrames=440, faceBatch=4, voiceId='')
    graph = compile_graph(template, submitted)
    assert graph['1']['inputs']['frame_load_cap'] == 440
    assert graph['2']['inputs']['audio'] == 'voice.wav'
    assert graph['3']['inputs']['mode'] == 'repetitive' and graph['3']['inputs']['face_detect_batch'] == 4
    assert graph['4']['inputs']['frame_rate'] == 30.0 and graph['4']['inputs']['audio'] == ['3', 1]
    submitted['settings']['voiceId'] = 'unresolved-library-id'
    with pytest.raises(H.HostedComputeError):
        compile_graph(template, submitted)


def test_dance_form_all_lengths_preserve_context_and_soundtrack():
    template = TEMPLATES['adapted-scail-animate-upload-v1']
    for frames in range(81, 162, 4):
        submitted = request(template)
        submitted['settings'].update(length=frames, fps=24, poseStrength=1.3)
        graph = compile_graph(template, submitted)
        assert graph['15']['inputs']['frame_load_cap'] == frames
        assert graph['22']['inputs']['length'] == frames and graph['22']['inputs']['pose_strength'] == 1.3
        assert graph['4']['inputs']['context_length'] <= frames
        assert graph['25']['inputs']['audio'] == ['15', 2] and graph['25']['inputs']['frame_rate'] == 24
    for changes in ({'length': 9}, {'mode': 'replace'}, {'contextLength': 133}):
        submitted = request(template)
        submitted['settings'].update(changes)
        with pytest.raises(H.HostedComputeError):
            compile_graph(template, submitted)


def test_video_loop_preserves_source_fps_and_intentional_no_audio():
    template = TEMPLATES['adapted-vhs-video-loop-v1']
    submitted = request(template)
    submitted['settings'].update(pingpong=True, repeat=7, maxFrames=900)
    graph = compile_graph(template, submitted)
    assert graph['1']['inputs']['frame_load_cap'] == 900
    assert graph['2']['inputs']['video_info'] == ['1', 3]
    assert graph['3']['inputs']['frame_rate'] == ['2', 5]
    assert graph['3']['inputs']['loop_count'] == 7 and graph['3']['inputs']['pingpong'] is True
    assert 'audio' not in graph['3']['inputs']
