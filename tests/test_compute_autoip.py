from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_autoip import autoip_generation_request, autoip_text_compatibility, build_autoip_plan, build_autoip_text_plan, normalise_autoip_intent, normalise_autoip_video_intent
from compute_routing import ComputeValidationError
from provider_fabric.autoip import AutoIPOperation
from provider_fabric.autoip_worker import _prompt
from provider_fabric.service import ActorContext, ProviderFabricService, _parse_plan_request


@pytest.mark.parametrize('feature,settings,visual', [
    ('text2img', {'positive': 'A ceramic cup on a table.', 'aspect': '1:1'}, 'realistic'),
    ('qwen21-text2img', {'prompt': 'A small illustrated boat.', 'size': '960x540', 'negative': 'text'}, 'anime'),
    ('h3-text-video', {'prompt': 'A leaf moves in the wind.', 'seconds': 5, 'resolution': '480p'}, 'realistic'),
])
def test_generic_plan_passes_actual_fabric_parser_and_worker_prompt_without_character_grant(feature, settings, visual):
    request_id = str(uuid.uuid4())
    plan = build_autoip_text_plan(feature, settings, request_id=request_id, visual_class=visual)
    parsed = _parse_plan_request(plan)
    # This path must not call a character materializer or invent a character.
    service = object.__new__(ProviderFabricService)
    assert service._materialize_character_capability(ActorContext('owner', 'operator'), parsed) is parsed
    prompt = _prompt(parsed.creative_instruction, AutoIPOperation(plan['capability']))
    assert settings.get('prompt', settings.get('positive')) in prompt
    assert plan['characters'] == [] and plan['assets'] == []
    assert not {'action_id', 'feature_id', 'profile_key'} & set(plan)
    envelope = autoip_generation_request(request_id, plan)
    assert envelope == autoip_generation_request(request_id, plan)
    assert envelope['request_id'] == 'compute:' + uuid.UUID(request_id).hex


@pytest.mark.parametrize('settings', [
    {'positive': 'scene', 'steps': 9},
    {'positive': 'scene', 'aspect': '1088x1920'},
    {'positive': 'scene', 'seed': 123},
    {'positive': 'scene', 'batchSize': 2},
    {'positive': 'scene', 'useDopsd': True},
    {'positive': 'scene', 'prompt': 'a different scene'},
    {'positive': 'scene', '_characterId': 'must-not-strip'},
])
def test_nonportable_controls_rejected_without_silent_loss(settings):
    result = autoip_text_compatibility('text2img', settings)
    assert result['supported'] is False


@pytest.mark.parametrize('binding', [{'character_id': 'owned-character'}, {'file_slots': ['image']}])
def test_identity_and_uploads_require_canonical_existing_adapters(binding):
    with pytest.raises(ComputeValidationError) as error:
        build_autoip_text_plan('text2img', {'positive': 'scene'}, request_id=str(uuid.uuid4()), **binding)
    assert error.value.code == 'canonical_binding_required'


def test_unknown_feature_and_visual_class_do_not_claim_support():
    assert autoip_text_compatibility('dance', {'prompt': 'scene'})['supported'] is False
    assert autoip_text_compatibility('text2img', {'prompt': 'scene'}, visual_class='unknown')['supported'] is False


def test_legacy_entrypoint_has_stable_plan_and_explicit_visual_style():
    settings = {'positive': 'A ceramic cup.', 'visualClass': 'anime', 'aspect': '1:1'}
    first = build_autoip_plan('text2img', settings, max_credits=5)
    assert first == build_autoip_plan('text2img', settings, max_credits=5)
    assert first['visual_class'] == 'anime' and first['options']['mode'] == 'anime'
    assert first['max_credits'] == 5
    assert settings['visualClass'] == 'anime'


def test_known_local_defaults_normalize_with_warning_and_keep_geometry_and_negative():
    settings = {'positive': 'A ceramic cup.', 'negative': 'text', 'aspect': '1024x1024',
                'steps': 8, 'cfg': 1, 'batchSize': 1, 'useDopsd': False, 'seed': -1}
    normalized, warnings = normalise_autoip_intent('text2img', settings)
    assert warnings and 'steps' in warnings[0] and 'cfg' in warnings[0]
    assert normalized['aspect'] == settings['aspect'] and normalized['negative'] == 'text'
    assert settings['steps'] == 8
    plan = build_autoip_plan('text2img', settings)
    assert plan['options']['size'] == '1024x1024'
    assert plan['creative_instruction']['negative_prompt'] == 'text'
    assert autoip_text_compatibility('text2img', settings)['warnings'] == warnings


def test_custom_controls_and_unmatched_geometry_remain_unavailable_after_normalization():
    settings = {'positive': 'A cup.', 'steps': 9, 'cfg': 1, 'aspect': '1088x1920'}
    normalized, warnings = normalise_autoip_intent('text2img', settings)
    assert normalized['steps'] == 9 and normalized['aspect'] == '1088x1920'
    assert warnings
    assert autoip_text_compatibility('text2img', settings)['supported'] is False
    assert autoip_text_compatibility('text2img', {'positive': 'A cup.', 'negative': 'x' * 501})['supported'] is False


def test_actual_qwen_form_defaults_match_exact_square_pixels():
    from qwen_image21 import feature_definitions
    feature = next(item for item in feature_definitions() if item['id'] == 'qwen21-text2img')
    settings = {field['id']: field['default'] for field in feature['fields']}
    settings['positive'] = 'A ceramic cup.'
    plan = build_autoip_plan(feature['id'], settings)
    assert plan['options']['size'] == '1024x1024'
    assert settings['aspectRatio'] == '1:1' and settings['megapixels'] == '1'
    normalized, warnings = normalise_autoip_intent(feature['id'], settings)
    assert normalized['size'] == '1024x1024' and any('1024x1024' in warning for warning in warnings)
    for changes in ({'megapixels': '2'}, {'aspectRatio': '16:9'}, {'seed': 23}, {'size': '960x540'}, {'steps': 24}):
        with pytest.raises(ComputeValidationError):
            build_autoip_plan(feature['id'], {**settings, **changes})


def test_actual_aspect_widget_width_height_payload_preserves_exact_pixels():
    settings = {'positive': 'A cup.', 'negative': '', 'width': 1024, 'height': 1024,
                'steps': 8, 'cfg': 1, 'batchSize': 1, 'useDopsd': False, 'seed': -1}
    assert build_autoip_plan('text2img', settings)['options']['size'] == '1024x1024'
    for changes in ({'width': 1088, 'height': 1920}, {'height': None}, {'width': True}, {'aspect': '16:9'}):
        with pytest.raises(ComputeValidationError):
            build_autoip_plan('text2img', {**settings, **changes})


def actual_h3_settings(feature_id='h3-text-video'):
    from minimax_h3_product import feature_definitions
    feature = next(item for item in feature_definitions() if item['id'] == feature_id)
    settings = {field['id']: field.get('default', '') for field in feature['fields']}
    settings.update(integrated_multimodal_description='A ceramic cup slowly turns.',
                    overall_soundscape='Wind softly whistles.', non_diegetic_music='No music.')
    return settings


def test_actual_h3_form_requires_explicit_remote_geometry_and_preserves_every_prose_section():
    settings = actual_h3_settings()
    with pytest.raises(ComputeValidationError) as error:
        normalise_autoip_video_intent('h3-text-video', settings)
    assert error.value.code == 'duration_invalid'
    remote = {'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'}
    normalized, warnings = normalise_autoip_video_intent('h3-text-video', settings, remote)
    plan = build_autoip_plan('h3-text-video', normalized)
    parsed = _parse_plan_request(plan)
    prompt = _prompt(parsed.creative_instruction, AutoIPOperation.TEXT_TO_VIDEO)
    for name in ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'):
        assert settings[name] in prompt
    assert plan['options'] == {**remote, 'seed': 'random'}
    assert settings['frames'] == '124' and settings['aspect'] == 'landscape'
    assert any('5 秒、480p、16:9' in warning for warning in warnings)


@pytest.mark.parametrize('change,override', [
    ({'acceleration': 'teacache'}, {'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'}),
    ({'seed': 42}, {'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'}),
    ({}, {'duration_seconds': 5.1667, 'resolution': '480p', 'aspect_ratio': '16:9'}),
    ({}, {'duration_seconds': 5, 'resolution': '1080p', 'aspect_ratio': '16:9'}),
    ({'overall_soundscape': ''}, {'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'}),
    ({'integrated_multimodal_description': 'x' * 1001}, {'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'}),
])
def test_explicit_h3_override_never_discards_custom_controls_or_truncates_prose(change, override):
    with pytest.raises(ComputeValidationError):
        settings, _ = normalise_autoip_video_intent('h3-text-video', {**actual_h3_settings(), **change}, override)
        build_autoip_plan('h3-text-video', settings)
