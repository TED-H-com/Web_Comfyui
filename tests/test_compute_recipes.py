"""Public catalog-to-DAG contracts, without importing deployment configuration."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_recipes import build_plan, feature_registry
from compute_routing import ComputeRoutingError


@pytest.fixture
def features():
    return {
        'text2img': {'id': 'text2img', 'title': 'Draw', 'studio': 'image', 'builder': 'private_builder_name',
            'fields': [{'id': 'positive', 'type': 'textarea', 'required': True},
                       {'id': 'aspect', 'type': 'aspect', 'default': '1088x1920', 'options': [
                           {'label': 'Portrait', 'width': 1088, 'height': 1920},
                           {'label': 'Square', 'width': 1024, 'height': 1024}]},
                       {'id': 'steps', 'type': 'slider', 'min': 1, 'max': 40, 'step': 1, 'default': 8},
                       {'id': 'useDopsd', 'type': 'checkbox', 'default': False}]},
        'voice-speak': {'id': 'voice-speak', 'title': 'Speak', 'studio': 'voice', 'builder': 'voice',
            'fields': [{'id': 'text', 'type': 'textarea', 'required': True}]},
        'lipsync': {'id': 'lipsync', 'title': 'Lip sync', 'studio': 'video', 'builder': 'lipsync',
            'uploads': [{'id': 'video', 'accept': 'video/*', 'required': True},
                        {'id': 'voice', 'accept': '.wav,.mp3,audio/flac'}],
            'minimumUploads': 1, 'fields': [{'id': 'mode', 'type': 'select', 'default': 'sequential',
                                            'options': ['sequential', 'repetitive']}]},
        'disabled': {'id': 'disabled', 'builder': 'disabled', 'apiEnabled': False},
        'browser-tool': {'id': 'browser-tool', 'title': 'Browser only'},
    }


def custom(*steps):
    return {'plan': {'version': 1, 'steps': list(steps)}}


def test_registry_exports_authored_forms_and_typed_uploads_without_builder_code(features):
    registry = feature_registry(features)
    assert {item['id'] for item in registry} == {'text2img', 'voice-speak', 'lipsync'}
    assert all('builder' not in item and 'workflow' not in item for item in registry)
    lipsync = next(item for item in registry if item['id'] == 'lipsync')
    assert lipsync['minimumUploads'] == 1
    assert lipsync['uploads'][1]['media_kinds'] == ['audio']
    assert next(item for item in registry if item['id'] == 'voice-speak')['output_kind'] == 'audio'
    registry[0]['fields'][0]['label'] = 'changed'
    assert 'label' not in features['text2img']['fields'][0]


def test_custom_speech_to_lipsync_uses_common_features_and_typed_dependency(features):
    result = build_plan(custom(
        {'id': 'speech', 'request': {'featureId': 'voice-speak', 'settings': {'text': 'Synthetic words'}}, 'placement': 'local'},
        {'id': 'sync', 'request': {'featureId': 'lipsync', 'settings': {'mode': 'sequential'}, 'files': {
            'video': {'serverFile': 'owned-synthetic.mp4'}}}, 'placement': 'runninghub',
         'bindings': {'voice': {'step_id': 'speech', 'artifact_index': 0}}}), features=features)
    assert result['steps'][1]['bindings']['voice']['expected_kind'] == 'audio'
    assert result['steps'][1]['depends_on'] == ['speech']
    assert result['steps'][1]['request']['settings']['mode'] == 'sequential'


def test_custom_aspect_has_exact_pixels_and_does_not_mutate_caller(features):
    body = custom({'id': 'image', 'request': {'featureId': 'text2img', 'settings': {
        'positive': 'Synthetic sky', 'aspect': '1024x1024'}}})
    original = copy.deepcopy(body)
    result = build_plan(body, features=features)['steps'][0]['request']['settings']
    assert result == {'positive': 'Synthetic sky', 'width': 1024, 'height': 1024}
    assert body == original


@pytest.mark.parametrize('invalid', [
    {'aspect': '1024x1024', 'width': 1088, 'height': 1920},
    {'width': '1024', 'height': 1024}, {'steps': '8'}, {'steps': True},
    {'steps': 3.5}, {'steps': 41}, {'steps': float('nan')}, {'useDopsd': 'false'},
    {'aspect': '1000x1000'}, {'workflow': {}}, {'_characterId': 'forged'},
])
def test_custom_form_controls_reject_silent_coercion_and_unpublished_settings(features, invalid):
    with pytest.raises(ComputeRoutingError):
        build_plan(custom({'id': 'image', 'request': {'featureId': 'text2img', 'settings': {
            'positive': 'Synthetic sky', **invalid}}}), features=features)


@pytest.mark.parametrize('alter', ['unknown', 'disabled', 'raw_graph', 'url', 'wrong_kind', 'cycle'])
def test_custom_plan_rejects_unreviewed_code_paths_and_wrong_dependency_types(features, alter):
    body = custom(
        {'id': 'speech', 'request': {'featureId': 'voice-speak', 'settings': {'text': 'Synthetic words'}}},
        {'id': 'sync', 'request': {'featureId': 'lipsync'},
         'bindings': {'voice': {'step_id': 'speech', 'artifact_index': 0}}})
    first, second = body['plan']['steps']
    if alter in {'unknown', 'disabled'}:
        first['request']['featureId'] = alter
    elif alter == 'raw_graph':
        first['request']['workflow'] = {'node': 'unreviewed'}
    elif alter == 'url':
        second['request']['files'] = {'video': {'url': 'https://example.invalid/media.mp4'}}
    elif alter == 'wrong_kind':
        second['bindings'] = {'video': {'step_id': 'speech', 'artifact_index': 0, 'expected_kind': 'video'}}
    else:
        first['depends_on'] = ['sync']
    with pytest.raises(ComputeRoutingError):
        build_plan(body, features=features)
