"""Voice library selection stays owner-authorized before hosted staging."""
from types import SimpleNamespace
import copy
import uuid
import pytest
from test_compute_web import web_fixture


def test_hosted_library_voice_resolves_to_an_owned_slot(web_fixture):
    f = web_fixture
    feature = {'id': 'lipsync', 'builder': 'synthetic_lipsync', 'uploads': [
        {'id': 'video', 'required': True}, {'id': 'voice'}], 'fields': []}
    f.globals['FEATURE_BY_ID']['lipsync'] = feature
    f.globals['BUILDERS']['synthetic_lipsync'] = lambda *a: pytest.fail('local builder must not run')
    (f.input_root / 'owned-upload-video.mp4').write_bytes(b'synthetic-provider-boundary-video')
    (f.input_root / 'owned-library-voice.wav').write_bytes(b'synthetic-provider-boundary-audio')
    calls, inspected, submitted = [], [], []
    def resolve(voice_id, user, service):
        assert voice_id == 'owned-voice' and user == f.user and not service
        calls.append(voice_id)
        return 'owned-library-voice.wav'
    f.globals['resolve_voice_reference'] = resolve
    def inspect(owner, provider, request, limits):
        assert 'voiceId' not in request['settings']
        assert request['files'] == {'video': {}, 'voice': {}}
        inspected.append(copy.deepcopy(request))
        return {'eligible': True}
    def submit(owner, provider, ident, request, limits, *, resolved_files):
        assert resolved_files['voice'] == f.input_root / 'owned-library-voice.wav'
        assert 'voiceId' not in request['settings']
        submitted.append(ident)
        return {'state': 'queued', 'downstream_id': ident}
    hosted = SimpleNamespace(inspect=inspect, submit=submit, start=lambda: None)
    f.web.hosted = hosted
    f.globals['get_hosted_compute_service'] = lambda: hosted
    request = {'featureId': 'lipsync', 'settings': {'voiceId': 'owned-voice', 'mode': 'sequential'},
               'files': {'video': {'serverFile': 'owned-upload-video.mp4'}}}
    assert f.web.inspect_hosted('comfy_cloud', f.web.owner, request, {})['eligible']
    assert set(request['files']) == {'video'}
    ident = str(uuid.uuid4())
    assert f.web._run(request, 'comfy_cloud', ident, {})['downstream_id'] == ident
    assert calls == ['owned-voice', 'owned-voice'] and len(inspected) == 2 and submitted == [ident]
    def forbidden(*args):
        raise PermissionError('foreign voice')
    f.globals['resolve_voice_reference'] = forbidden
    assert not f.web.inspect_hosted('comfy_cloud', f.web.owner, request, {})['eligible']
    with pytest.raises(ValueError):
        f.web._run(request, 'comfy_cloud', str(uuid.uuid4()), {})
    assert submitted == [ident]
