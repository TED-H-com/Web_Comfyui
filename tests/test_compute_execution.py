"""Synthetic image handoff and fresh authorization; no deployed data or GPU."""
import ast
import copy
import hashlib
import hmac
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlencode
import uuid
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_execution import ExecutionContext, PipelineExecution
from compute_recipes import build_plan, recipes
from compute_routing import ComputeAdapter, ComputeRejected, ComputeRouter, ComputeRoutingError


def server_functions(names, namespace):
    """Compile pure helpers without importing deployed config or user data."""
    path = Path(__file__).resolve().parents[1] / 'webui/server.py'
    source = ast.parse(path.read_text())
    nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


@pytest.fixture
def execution(tmp_path):
    from PIL import Image
    user = {'email': 'synthetic@example.invalid', 'role': 'operator'}
    output = tmp_path / 'output'
    output.mkdir()
    Image.new('RGB', (16, 16), 'blue').save(output / 'result.png')
    registered = []
    server = SimpleNamespace(COMFY_INPUT=tmp_path / 'input', COMFY_OUTPUT=output,
        user_for_email=lambda owner: copy.deepcopy(user) if owner == user['email'] else None,
        get_colab_service=lambda: SimpleNamespace(root=tmp_path / 'colab'),
        user_can_access_view_item=lambda actor, name, subfolder, kind, task: actor['email'] == user['email'] and task == 'image-task',
        record_input_upload_owner=lambda name, actor: registered.append((name, actor['email'])))
    return PipelineExecution(server), user, registered


def image_receipt():
    return {'state': 'completed', 'downstream_id': 'image-task', 'artifacts': [
        {'name': 'result.png', 'url': '/api/view?' + urlencode({'filename': 'result.png', 'subfolder': '', 'type': 'output', 'promptId': 'image-task'})}]}


def test_handoff_freezes_owned_image_and_checks_corruption(execution):
    service, user, registrations = execution
    receipt = image_receipt()
    result = service.resolve_artifact(user['email'], receipt, 0)
    target = service.s.COMFY_INPUT / result['file']['serverFile']
    assert target.read_bytes() == (service.s.COMFY_OUTPUT / 'result.png').read_bytes()
    assert registrations == [(target.name, user['email'])]
    assert service.resolve_artifact(user['email'], receipt, 0) == result
    target.write_bytes(b'changed')
    with pytest.raises(ComputeRejected, match='完整性'):
        service.resolve_artifact(user['email'], receipt, 0)
    assert not list(service.s.COMFY_INPUT.glob('.pipeline-*'))


def test_handoff_rejects_foreign_owner_wrong_task_and_external_url(execution):
    service, user, _ = execution
    with pytest.raises(PermissionError):
        service.resolve_artifact('other@example.invalid', image_receipt(), 0)
    user['disabled'] = True
    with pytest.raises(PermissionError):
        service.resolve_artifact(user['email'], image_receipt(), 0)
    user['disabled'] = False
    receipt = image_receipt()
    receipt['downstream_id'] = 'different-task'
    with pytest.raises(PermissionError):
        service.resolve_artifact(user['email'], receipt, 0)
    receipt['artifacts'][0]['url'] = 'https://example.invalid/result.png'
    with pytest.raises(ComputeRejected):
        service.resolve_artifact(user['email'], receipt, 0)


def test_cross_provider_chain_retains_verified_dependency_after_restart(execution, tmp_path):
    service, user, _ = execution
    submitted = []
    def submit_image(owner, rid, request, limits):
        submitted.append(('image', rid))
        return image_receipt()
    def submit_video(owner, rid, request, limits):
        submitted.append(('video', rid))
        assert (service.s.COMFY_INPUT / request['files']['image']['serverFile']).is_file()
        return {'state': 'completed', 'downstream_id': 'video-task', 'artifacts': [{'name': 'movie.mp4', 'url': '/api/synthetic/result'}]}
    adapters = {
        'local': ComputeAdapter(lambda *a: {'eligible': True, 'queue_depth': 0, 'currency': 'local'}, submit_image),
        'colab': ComputeAdapter(lambda *a: {'eligible': True, 'queue_depth': 0, 'currency': 'compute_units', 'estimated_cost': 1}, submit_video),
    }
    service.engine.router_factory = lambda owner: ComputeRouter(tmp_path / 'router.sqlite3', adapters)
    body = {'pipeline_id': str(uuid.uuid4()), 'recipe_id': 'text-image-video',
            'inputs': {'image_prompt': 'Synthetic landscape', 'motion_prompt': 'Slow camera motion'},
            'placements': {'image': 'local', 'video': 'colab'}}
    service.engine.create(user['email'], body['pipeline_id'], build_plan(body))
    for _ in range(3):
        result = service.engine.tick(user['email'], body['pipeline_id'])
    assert result['state'] == 'completed'
    assert result['steps'][1]['dependency_hashes']['image']
    fresh = PipelineExecution(service.s)
    fresh.engine.router_factory = service.engine.router_factory
    assert fresh.engine.resume(user['email'], body['pipeline_id'])['state'] == 'completed'
    assert [row[0] for row in submitted] == ['image', 'video']


def test_execution_context_refreshes_user_permissions_each_call(execution):
    service, user, _ = execution
    context = ExecutionContext(service.s, user['email'])
    assert context.current_user()['role'] == 'operator'
    user['role'] = 'member'
    assert context.current_user()['role'] == 'member'
    user['disabled'] = True
    with pytest.raises(PermissionError):
        context.current_user()
    user['disabled'] = False
    user['mustSetPassword'] = True
    with pytest.raises(PermissionError):
        context.current_user()


@pytest.mark.parametrize('identity', ['LOCAL_SESSION_USER', 'LEGACY_SESSION_USER'])
def test_existing_local_identity_requires_current_local_credentials(execution, identity):
    service, _, _ = execution
    service.s.LOCAL_SESSION_USER = {'email': 'local@example.invalid', 'role': 'admin'}
    service.s.LEGACY_SESSION_USER = {'email': 'legacy@example.invalid', 'role': 'admin'}
    service.s.local_credential_version = lambda: 'synthetic-version'
    owner = getattr(service.s, identity)['email']
    context = ExecutionContext(service.s, owner)
    result = context.current_user()
    result['role'] = 'member'
    assert context.current_user()['role'] == 'admin'
    with pytest.raises(PermissionError):
        ExecutionContext(service.s, 'unknown@example.invalid')
    service.s.local_credential_version = lambda: None
    with pytest.raises(PermissionError):
        context.current_user()


def test_handoff_uses_real_owner_receipts_and_rejects_other_owner(execution, tmp_path):
    service, user, _ = execution
    namespace = server_functions({'resolve_server_file', 'sha256_file', 'record_input_upload_owner',
                                  'user_can_use_uploaded_input'}, {
        'Path': Path, 'os': os, 'time': time, 'hashlib': hashlib, 'hmac': hmac,
        'COMFY_INPUT': service.s.COMFY_INPUT, 'JOB_INDEX_PATH': tmp_path / 'state/jobs.json',
        'normalize_email': lambda value: value.strip().lower(),
    })
    service.s.record_input_upload_owner = namespace['record_input_upload_owner']
    receipt = service.resolve_artifact(user['email'], image_receipt(), 0)
    name = receipt['file']['serverFile']
    assert namespace['user_can_use_uploaded_input'](user, name)
    assert not namespace['user_can_use_uploaded_input']({'email': 'other@example.invalid'}, name)
    assert (service.s.COMFY_INPUT / name).stat().st_nlink == 1


def test_revoked_user_cannot_register_handoff_after_image_validation(execution, monkeypatch):
    from PIL import Image
    service, user, registrations = execution
    original = Image.open
    def revoke_then_open(*args, **kwargs):
        user['disabled'] = True
        return original(*args, **kwargs)
    monkeypatch.setattr(Image, 'open', revoke_then_open)
    with pytest.raises(ComputeRejected):
        service.resolve_artifact(user['email'], image_receipt(), 0)
    assert registrations == []
    assert not list(service.s.COMFY_INPUT.iterdir())


def test_recipes_explicit_style_geometry_and_duration_contracts():
    from compute_adapters import normalise_intent, colab_compatibility
    from compute_autoip import normalise_autoip_video_intent
    assert {r['id'] for r in recipes()} == {'text-image-video', 'edit-image-video', 'voice-design-lipsync'}
    body = {'recipe_id': 'text-image-video', 'inputs': {'image_prompt': 'Blue sky', 'motion_prompt': 'Slow clouds',
            'visual_class': 'realistic', 'native_frames': '226', 'autoip_seconds': 12}}
    plan = build_plan(body)
    for step in plan['steps']:
        req = step['request']
        settings, _ = normalise_intent(req['featureId'], req['settings'])
        compatible = colab_compatibility(req['featureId'], settings, ['image'] if step['id'] == 'video' else [])
        assert compatible['supported'], compatible
    assert plan['steps'][0]['request']['computeVisualClass'] == 'realistic'
    namespace = server_functions({'zimage_prompt', 'clamp_int', 'clamp_float', 'normalize_seed'}, {
        'math': math, 'random': random, 'ZIMAGE_DEFAULT_NEGATIVE': 'synthetic negative',
        'output_prefix': lambda *args: 'synthetic/output',
    })
    prompt = namespace['zimage_prompt'](plan['steps'][0]['request']['settings'])
    geometry = next(node['inputs'] for node in prompt.values() if node['class_type'] == 'EmptySD3LatentImage')
    assert (geometry['width'], geometry['height']) == (1024, 1024)
    video = plan['steps'][1]['request']
    normalized, _ = normalise_autoip_video_intent(video['featureId'], video['settings'], video['computeAutoipVideo'])
    assert normalized['duration_seconds'] == 12
    assert video['settings']['frames'] == '226'
    for bad in ({'image_prompt': 'x'}, {'image_prompt': 'x', 'motion_prompt': 'y', 'autoip_seconds': True},
                {'image_prompt': 'x', 'motion_prompt': 'y', 'visual_class': 'anything'}):
        with pytest.raises(ComputeRoutingError):
            build_plan({**body, 'inputs': bad})


@pytest.mark.parametrize('kind', ['audio', 'video'])
def test_handoff_validates_real_synthetic_audio_video_streams(execution, kind):
    if not shutil.which('ffprobe') or kind == 'video' and not shutil.which('ffmpeg'):
        pytest.skip('synthetic media validation requires ffprobe/ffmpeg')
    service, user, registrations = execution
    name = 'result.wav' if kind == 'audio' else 'result.mp4'
    source = service.s.COMFY_OUTPUT / name
    if kind == 'audio':
        with wave.open(str(source), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\x00\x00' * 2000)
    else:
        subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=16x16:r=4:d=0.5', '-c:v', 'mpeg4', '-threads', '1', str(source)],
                       check=True, capture_output=True, timeout=15)
    receipt = image_receipt()
    receipt['output_kind'] = kind
    receipt['artifacts'][0] = {'name': name, 'url': '/api/view?' + urlencode({
        'filename': name, 'subfolder': '', 'type': 'output', 'promptId': 'image-task'})}
    proof = service.resolve_artifact(user['email'], receipt, 0)
    assert proof['media']['kind'] == kind and 0 < proof['media']['duration'] < 1
    assert proof['artifact_id'].startswith(kind + ':')
    assert (service.s.COMFY_INPUT / proof['file']['serverFile']).read_bytes() == source.read_bytes()
    assert registrations == [(proof['file']['serverFile'], user['email'])]
    receipt['output_kind'] = 'image'
    with pytest.raises(ComputeRejected) as error:
        service.resolve_artifact(user['email'], receipt, 0)
    assert error.value.code == 'artifact_kind_mismatch'


def test_media_decoder_failure_never_records_an_owned_input(execution):
    service, user, registrations = execution
    source = service.s.COMFY_OUTPUT / 'invalid.wav'
    source.write_bytes(b'not a real audio stream')
    with pytest.raises(ComputeRejected):
        service.freeze_media(user['email'], user, source, expected_kind='audio')
    assert registrations == [] and not list(service.s.COMFY_INPUT.iterdir())


def test_media_byte_and_duration_limits_are_checked_before_publication(execution, monkeypatch):
    import colab_service
    service, user, registrations = execution
    source = service.s.COMFY_OUTPUT / 'oversized.wav'
    with source.open('wb') as output:
        output.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(ComputeRejected):
        service.freeze_media(user['email'], user, source)
    source.write_bytes(b'synthetic audio placeholder')
    monkeypatch.setattr(colab_service, 'validate_media', lambda path: {'kind': 'audio', 'duration': 3601})
    with pytest.raises(ComputeRejected):
        service.freeze_media(user['email'], user, source)
    assert registrations == [] and not list(service.s.COMFY_INPUT.iterdir())


def test_voice_design_recipe_matches_real_voxcpm_admission_without_identity_reference(tmp_path):
    body = {'recipe_id': 'voice-design-lipsync', 'inputs': {
        'speech_text': 'A short synthetic greeting.', 'voice_style': 'Calm, clear synthetic voice.',
        'video': {'serverFile': 'owned-synthetic.mp4'}, 'sync_mode': 'repetitive'},
        'placements': {'voice': 'local', 'lipsync': 'runninghub'}}
    plan = build_plan(body)
    first, second = plan['steps']
    settings = first['request']['settings']
    assert first['placement'] == 'local' and second['placement'] == 'runninghub'
    assert second['bindings']['voice']['expected_kind'] == 'audio'
    assert not {'characterId', 'action', 'variantId'} & first['request'].keys()
    assert not {'voiceProfile', 'persona', 'voiceId'} & settings.keys()
    assert first['request']['files'] == {}
    runtime_file = tmp_path / 'synthetic-runtime-file'
    runtime_file.write_bytes(b'synthetic')
    model = tmp_path / 'synthetic-model'
    model.mkdir()
    for name in ('config.json', 'model.safetensors'):
        (model / name).write_bytes(b'synthetic fixture, never a model')
    admitted = []
    def admit(value):
        admitted.append(value)
        return {'run': {'id': 'run_synthetic_voice'}}
    namespace = server_functions({'start_voxcpm_voice_job', 'validate_voice_generation_contract',
                                  'clamp_int', 'clamp_float', 'normalize_seed'}, {
        'math': math, 'random': random, 'time': time, 'secrets': SimpleNamespace(token_hex=lambda size: 'synthetic'),
        'VOICE_SPEAK_MAX_CHARS': 2400, 'VOXCPM_PROFILES': [], 'VOXCPM_MODEL_PATH': model,
        **{key: runtime_file for key in ('VOXCPM_PYTHON', 'VOXCPM_SCRIPT', 'GPU_QUEUE_SCRIPT',
                                        'VOICE_LAB_PYTHON', 'VOXCPM_SPEAKER_SCRIPT')},
        'reject_voice_hallucination_risks': lambda *args: None, 'normalize_voice_script': lambda text: text,
        'basic_voice_normalize': lambda text: text, 'output_prefix': lambda *args: 'synthetic',
        'output_path_from_prefix': lambda *args: tmp_path / 'voice.wav',
        'direct_sidecar_path': lambda *args: tmp_path / 'voice.json',
        'castle_orchestrator': SimpleNamespace(create_run_api=admit),
        'VOXCPM_SPEAKER_THRESHOLD': .8, 'VOXCPM_SPEAKER_ATTEMPTS': 2,
        'GPU_QUEUE_PYTHON': 'synthetic', 'GUARDED_MIN_FREE_MB': 1, 'GUARDED_MAX_UTIL': 1, 'GUARDED_QUEUE_INTERVAL': 1,
    })
    assert namespace['validate_voice_generation_contract']('voxcpm-tts', settings, {'role': 'member'}) is settings
    result = namespace['start_voxcpm_voice_job'](settings, {})
    assert result['run_id'] == 'run_synthetic_voice'
    payload = admitted[0]['input']
    assert payload['mode'] == 'design' and payload['text'] == body['inputs']['speech_text']
    assert payload['control'] == body['inputs']['voice_style']
    assert payload['reference_audio'] == payload['lora_path'] == payload['profile'] == ''
    assert payload['speaker_gate'] is False
