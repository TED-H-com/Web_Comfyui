"""Synthetic, disk-only regression tests for the S1 security patch."""
import base64
import contextlib
import io
import json
import mimetypes
import re
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import server
import library_service


def identity(name, role='user'):
    return {'email': name + '@' + 'example.invalid', 'role': role, 'displayName': name}


A = identity('alpha')
B = identity('bravo')
ADMIN = identity('administrator', 'admin')
OPERATOR = identity('operator', 'admin')


@pytest.fixture
def state(tmp_path, monkeypatch):
    input_dir, output_dir, temp_dir = (tmp_path / name for name in ('input', 'output', 'temp'))
    for directory in (input_dir, output_dir, temp_dir):
        directory.mkdir()
    monkeypatch.setattr(server, 'COMFY_INPUT', input_dir)
    monkeypatch.setattr(server, 'COMFY_OUTPUT', output_dir)
    monkeypatch.setattr(server, 'COMFY_TEMP', temp_dir)
    monkeypatch.setattr(server, 'VIEW_DIRS', {'input': input_dir, 'output': output_dir, 'temp': temp_dir})
    monkeypatch.setattr(server, 'JOB_INDEX_PATH', tmp_path / 'jobs' / 'index.json')
    monkeypatch.setattr(server, 'VOICE_LIBRARY', tmp_path / 'voices')
    monkeypatch.setattr(server, 'OPERATOR_EMAIL', OPERATOR['email'])
    return SimpleNamespace(root=tmp_path, input=input_dir, output=output_dir, temp=temp_dir)


def handler(user=B, body=None, path='/', headers=None):
    h = object.__new__(server.Handler)
    h.path = path
    h.headers = headers or {}
    h.current_user = lambda: user
    h.read_json = lambda *args: body or {}
    h.read_provider_json = lambda *args: body or {}
    h.origin_allowed = lambda: True
    h.is_orchestrator_service_authenticated = lambda: False
    h._handle_provider_console = lambda *args: False
    h.handle_legacy_entrypoint = lambda: False
    h.status = None
    h.response_headers = {}
    h.wfile = io.BytesIO()
    h.send_response = lambda status: setattr(h, 'status', status)
    h.send_header = lambda name, value: h.response_headers.__setitem__(name, str(value))
    h.end_headers = lambda: None
    h.send_redirect = lambda location: (setattr(h, 'status', 302), h.response_headers.__setitem__('Location', location))
    return h


def response_json(h):
    return json.loads(h.wfile.getvalue())


def fake_upload(monkeypatch, state):
    def upload(req, timeout):
        match = re.search(rb'filename="([^"]+)"', req.data)
        assert match
        name = match.group(1).decode()
        (state.input / name).write_bytes(b'fixture image')
        return contextlib.closing(io.BytesIO(json.dumps({'name': name}).encode()))
    monkeypatch.setattr(server.urllib.request, 'urlopen', upload)


def test_a1_randomized_upload_names_and_extension(state, monkeypatch):
    fake_upload(monkeypatch, state)
    names = [server.save_uploaded_bytes(b'image', 'x' * 300 + '.png', 'image/png', 'image'),
             server.save_uploaded_bytes(b'video', 'x' * 300 + '.mp4', 'video/mp4', 'video')]
    for name, ext in zip(names, ('.png', '.mp4')):
        assert re.match(r'^castle_web_\d+_[0-9a-f]{32}_[a-z]+_', name)
        assert len(name.encode()) <= 200
        assert name.endswith(ext)
        assert (state.input / name).is_file()
    assert names[0] != server.save_uploaded_bytes(b'image', 'x' * 300 + '.png', 'image/png', 'image')


def test_a2_a3_input_view_receipts_and_missing_match(state):
    (state.input / 'plain.png').write_bytes(b'private')
    (state.input / 'castle_shared.png').write_bytes(b'staged')
    server.record_input_upload_owner('plain.png', A)
    def get(user, name, folder=''):
        h = handler(user)
        h.api_view('type=input&filename=' + name + ('&subfolder=' + folder if folder else ''))
        return h
    forbidden, missing = get(B, 'plain.png'), get(B, 'missing.png')
    assert forbidden.status == missing.status == 404
    assert forbidden.wfile.getvalue() == missing.wfile.getvalue()
    assert get(A, 'plain.png').wfile.getvalue() == b'private'
    assert get(ADMIN, 'plain.png').wfile.getvalue() == b'private'
    assert get(B, 'castle_shared.png').wfile.getvalue() == b'staged'
    assert not server.user_can_read_input_file(None, 'castle_shared.png')
    assert not server.user_can_read_input_file(A, 'plain.png', 'nested')
    assert server.user_can_read_input_file(B, 'reference_opc_fixture.png', 'nested')


def test_a4_assist_checks_every_reference_before_model(state, monkeypatch):
    for name, owner in [('a.png', A), ('b.png', B)]:
        (state.input / name).write_bytes(b'image')
        server.record_input_upload_owner(name, owner)
    model = Mock(return_value={'text': 'ok'})
    monkeypatch.setattr(server, 'generate_assist', model)
    h = handler(B, {'mode': 'brainstorm', 'references': [{'serverFile': 'b.png'}, {'serverFile': 'a.png'}]})
    h.api_assist()
    assert h.status == 403
    assert response_json(h)['error'] == '無權使用這個伺服器素材；請重新上傳自己的檔案'
    model.assert_not_called()
    h = handler(B, {'mode': 'brainstorm', 'references': [{'serverFile': 'b.png'}]})
    h.api_assist()
    assert h.status == 200
    model.assert_called_once()


def library_fixture(state, monkeypatch):
    source = state.root / 'source.png'
    source.write_bytes(b'fixture library image')
    item = {'id': 'f' * 32, 'signature': 'fixture-signature', 'kind': 'image', 'root': 'output',
            'path': source, 'relative': 'source.png', 'metadata': {'filename': 'source.png', 'title': 'fixture'}}
    store = SimpleNamespace(get=lambda *a, **k: item)
    context = SimpleNamespace(COMFY_INPUT=state.input, import_crawl_video=server.import_crawl_video)
    monkeypatch.setattr(library_service, 'catalog', lambda context: store)
    monkeypatch.setattr(library_service, 'file_signature', lambda path: item['signature'])
    monkeypatch.setattr(server, 'library_server_context', lambda: context)
    return item


def test_a5_library_receipts_are_owner_specific(state, monkeypatch):
    library_fixture(state, monkeypatch)
    names = []
    for user in (A, B):
        h = handler(user, {'id': 'fixture', 'slot': 'image'})
        h.api_library_mutate('/api/library/import')
        assert h.status == 200
        name = response_json(h)['serverFile']
        server.validate_client_generation_files({'image': {'serverFile': name}}, user)
        names.append(name)
    assert names[0] != names[1]
    assert server.user_can_use_uploaded_input(A, names[0])
    assert server.user_can_use_uploaded_input(B, names[1])
    assert not server.user_can_use_uploaded_input(B, names[0])


def test_a5_library_crawler_branch_records_returned_filename(state, monkeypatch):
    item = library_fixture(state, monkeypatch)
    item.update(kind='video', root='crawl')
    def stage(relative, slot):
        (state.input / 'crawl-private.mp4').write_bytes(b'video')
        return {'filename': 'crawl-private.mp4'}
    context = SimpleNamespace(COMFY_INPUT=state.input, import_crawl_video=stage)
    monkeypatch.setattr(server, 'library_server_context', lambda: context)
    h = handler(B, {'id': 'fixture', 'slot': 'video'})
    h.api_library_mutate('/api/library/import')
    assert h.status == 200
    name = response_json(h)['filename']
    server.validate_client_generation_files({'video': {'serverFile': name}}, B)


def test_a5_reference_source_receipt(state, monkeypatch):
    def reference(context, user, body):
        (state.input / 'reference-plain.png').write_bytes(b'reference')
        return {'ok': True, 'filename': 'reference-plain.png'}
    monkeypatch.setattr(server.reference_sources, 'import_image', reference)
    h = handler(B, {'fixture': True})
    h.api_reference_sources(None)
    assert h.status == 200
    server.validate_client_generation_files({'image': {'serverFile': response_json(h)['filename']}}, B)


def generation_stubs(state, monkeypatch, lane):
    import comfy_checkpoint_context
    import compute_adapters
    feature = {'id': 's1-test', 'builder': 's1-test'}
    monkeypatch.setitem(server.FEATURE_BY_ID, 's1-test', feature)
    monkeypatch.setitem(server.BUILDERS, 's1-test', lambda settings, uploads: {'uploaded': uploads})
    monkeypatch.setattr(server, 'character_entry_for_generation', lambda *a: None)
    monkeypatch.setattr(server, 'character_generation_context', lambda *a: None)
    monkeypatch.setattr(server, 'apply_character_visual_class_hints', lambda settings, *a: settings)
    monkeypatch.setattr(server, 'resolve_generation_presets', lambda feature_id, settings: settings)
    monkeypatch.setattr(server, 'validate_feature_option_contract', lambda feature, settings: settings)
    monkeypatch.setattr(server, 'enforce_p0_generation_options', lambda feature_id, settings: settings)
    monkeypatch.setattr(server, 'validate_voice_generation_contract', lambda feature_id, settings, *a: settings)
    monkeypatch.setattr(server, 'validate_generation_file_contract', lambda feature, files: files)
    monkeypatch.setattr(server, 'feature_status', lambda: [])
    monkeypatch.setattr(server, 'request_json', lambda *a, **k: {'prompt_id': 'fixture-job'})
    monkeypatch.setattr(server, 'record_job_attribution', lambda *a, **k: None)
    monkeypatch.setattr(comfy_checkpoint_context, 'prompt_admission', contextlib.nullcontext)
    monkeypatch.setattr(compute_adapters, 'normalise_intent', lambda *a, **k: ({}, []))
    monkeypatch.setattr(compute_adapters, 'colab_compatibility', lambda *a, **k: {'supported': True})
    monkeypatch.setattr(compute_adapters, 'build_colab_request', lambda *a, **k: {})
    monkeypatch.setattr(server, 'get_colab_service', lambda: SimpleNamespace(submit=lambda *a: {'ok': True}))
    h = handler(B)
    h._provider_console = lambda: SimpleNamespace(gate=lambda lane: None)
    return h


@pytest.mark.parametrize('lane', ['local', 'colab'])
def test_a5_inline_upload_receipt_and_failure_is_nonfatal(state, monkeypatch, lane, capsys):
    h = generation_stubs(state, monkeypatch, lane)
    file = {'name': 'fixture.mp4', 'dataUrl': 'data:video/mp4;base64,' + base64.b64encode(b'video').decode()}
    body = {'featureId': 's1-test', 'settings': {}, 'files': {'video': file}}
    assert h._generate_result(body, compute_lane=lane)['ok']
    name = next(state.input.iterdir()).name
    server.validate_client_generation_files({'video': {'serverFile': name}}, B)
    monkeypatch.setattr(server, 'record_input_upload_owner', Mock(side_effect=OSError('fixture')))
    assert h._generate_result(body, compute_lane=lane)['ok']
    assert 'input_receipt_failed exception=OSError' in capsys.readouterr().out


def test_generation_refuses_borrowed_voice_selection(state, monkeypatch):
    h = generation_stubs(state, monkeypatch, 'local')
    body = {'featureId': 's1-test', 'settings': {}, 'files': {}, 'parityVoiceSelection': {'voice': 'other'}}
    with pytest.raises(ValueError, match='此版本不支援借用聲音'):
        h._generate_result(body, compute_lane='local')


def test_b_history_item_and_prompt_authorization(state, monkeypatch):
    for root, name, owner in [(state.input, 'a.wav', A), (state.input, 'b.wav', B)]:
        (root / name).write_bytes(b'audio')
        server.record_input_upload_owner(name, owner)
    (state.output / 'a-out.wav').write_bytes(b'audio')
    (state.output / 'b-out.wav').write_bytes(b'audio')
    monkeypatch.setattr(server, 'user_can_access_view_item', lambda user, name, sub, typ, prompt: name.startswith('a-') == (user == A))
    monkeypatch.setattr(server, 'user_can_access_job', lambda user, prompt: prompt == ('job-a' if user == A else 'job-b'))
    history = Mock(side_effect=lambda path: {'job-a': {'fixture': True}, 'job-b': {'fixture': True}})
    monkeypatch.setattr(server, 'request_json', history)
    monkeypatch.setattr(server, 'history_prompt_detail', lambda prompt, item: {'workflow': 'owned workflow', 'nodes': [{'id': prompt}]})
    voices = []
    monkeypatch.setattr(server, 'load_voice_index', lambda: list(voices))
    monkeypatch.setattr(server, 'save_voice_index', lambda entries: voices.__setitem__(slice(None), entries))
    def run(user, name, typ, prompt, subfolder=''):
        h = handler(user, {'filename': name, 'type': typ, 'promptId': prompt, 'subfolder': subfolder})
        h.api_voices_import_history()
        return h
    assert run(B, 'a-out.wav', 'output', 'job-a').status == 403
    assert run(B, 'a.wav', 'input', 'job-a').status == 403
    assert run(A, 'a.wav', 'input', 'job-a', 'nested').status == 403
    history.reset_mock()
    mixed = run(B, 'b.wav', 'input', 'job-a')
    assert mixed.status == 200
    assert response_json(mixed)['voice']['settings']['promptId'] == ''
    assert 'nodes' not in response_json(mixed)['voice']['settings']
    history.assert_not_called()
    for name, typ in [('a.wav', 'input'), ('a-out.wav', 'output')]:
        own = run(A, name, typ, 'job-a')
        assert own.status == 200
        assert response_json(own)['voice']['settings']['nodes'][0]['id'] == 'job-a'


def dispatch_stubs(monkeypatch):
    monkeypatch.setattr(server.workflow_library, 'handle_get', lambda *a: False)
    monkeypatch.setattr(server.workflow_library, 'handle_post', lambda *a: False)


@pytest.mark.parametrize('method,path,endpoint', [
    ('GET', '/api/google/status', 'api_google_status'),
    ('GET', '/api/google/login', 'api_google_login'),
    ('GET', '/api/google/drive/files', 'api_google_drive_files'),
    ('POST', '/api/google/logout', 'api_google_logout'),
    ('POST', '/api/google/drive/import', 'api_google_drive_import'),
])
def test_c_drive_dispatch_is_operator_only(state, monkeypatch, method, path, endpoint):
    dispatch_stubs(monkeypatch)
    for user, expected in [(B, 403), (ADMIN, 403), (OPERATOR, 200)]:
        h = handler(user, path=path)
        setattr(h, endpoint, lambda *args: h.send_json({'ok': True}))
        getattr(h, 'do_' + method)()
        assert h.status == expected


def test_c_callback_requires_matching_operator_session(state, monkeypatch):
    exchanges = Mock(return_value={'access_token': 'synthetic', 'expires_in': 3600})
    saved = Mock()
    monkeypatch.setattr(server, 'request_form_json', exchanges)
    monkeypatch.setattr(server, 'load_google_token', lambda: {})
    monkeypatch.setattr(server, 'save_google_token', saved)
    for label, user, cookie, recorded, expected in [
        ('absent', OPERATOR, '', 'same', 'session_error'),
        ('other', OPERATOR, 'different', 'same', 'session_error'),
        ('nonoperator', B, 'same', 'same', 'session_error'),
        ('empty-record', OPERATOR, 'same', '', 'session_error'),
        ('valid', OPERATOR, 'same', 'same', 'connected'),
    ]:
        server.GOOGLE_AUTH_STATES[label] = {'session': recorded, 'expires': time.time() + 60, 'redirectUri': 'fixture'}
        headers = {'Cookie': server.SESSION_COOKIE + '=' + cookie} if cookie else {}
        h = handler(user, headers=headers)
        h.api_google_callback('state=' + label + '&code=fixture')
        assert h.response_headers['Location'] == '/?google_drive=' + expected
    assert exchanges.call_count == saved.call_count == 1


def downloader_setup(state, monkeypatch, returncode=1):
    cookies = state.root / 'cookies'
    cookies.mkdir(exist_ok=True)
    monkeypatch.setattr(server, 'CRAWL_COOKIES', cookies)
    monkeypatch.setattr(server, 'CRAWL_DIR', state.root)
    monkeypatch.setattr(server, '_resolve_yt_dlp', lambda: '/fixture/yt-dlp')
    monkeypatch.setattr(server, '_video_codec', lambda path: 'h264')
    (cookies / 'facebook.txt').touch()
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if returncode == 0:
            name = argv[argv.index('-o') + 1].replace('%(title).40s', 'clip').replace('%(ext).16s', 'mp4')
            Path(name).write_bytes(b'video')
        return subprocess.CompletedProcess(argv, returncode, stdout='', stderr='synthetic failure')
    monkeypatch.setattr(server.subprocess, 'run', run)
    return calls, cookies


def addr(value):
    family = socket.AF_INET6 if ':' in value else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, '', (value, 443))]


def test_d_cookie_boundary_and_downloader_arguments(state, monkeypatch):
    calls, cookies = downloader_setup(state, monkeypatch, 0)
    monkeypatch.setattr(server.socket, 'getaddrinfo', lambda *a, **k: addr('8.8.8.8'))
    assert server._crawl_cookie_file('https://' + 'notfacebook.com.example.org' + '/x') is None
    assert server._crawl_cookie_file('https://' + 'evilfacebook.com' + '/x') is None
    assert server._crawl_cookie_file('https://' + 'm.facebook.com' + '/x') == cookies / 'facebook.txt'
    server.fetch_video_url('https://' + 'm.facebook.com' + '/x', 'video', shared_cookies=False)
    server.fetch_video_url('https://' + 'm.facebook.com' + '/x', 'video', shared_cookies=True)
    assert '--cookies' not in calls[0]
    assert '--cookies' in calls[1]
    assert calls[1][calls[1].index('--cookies') + 1] == str(cookies / 'facebook.txt')


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.5', '100.64.0.1', '::1'])
def test_d_non_global_resolution_is_refused_for_user_only(state, monkeypatch, address):
    calls, _ = downloader_setup(state, monkeypatch)
    monkeypatch.setattr(server.socket, 'getaddrinfo', lambda *a, **k: addr(address))
    with pytest.raises(ValueError, match='不是公開網址'):
        server.fetch_video_url('https://' + 'fixture.invalid' + '/x', 'video', shared_cookies=False)
    assert not calls
    with pytest.raises(ValueError, match='下載失敗'):
        server.fetch_video_url('https://' + 'fixture.invalid' + '/x', 'video', shared_cookies=True)
    assert len(calls) == 1


def test_d_hint_only_for_unshared_known_cookie_and_dns_failure_allowed(state, monkeypatch):
    calls, _ = downloader_setup(state, monkeypatch)
    monkeypatch.setattr(server.socket, 'getaddrinfo', lambda *a, **k: (_ for _ in ()).throw(socket.gaierror()))
    for host, shared, hint in [('m.facebook.com', False, True), ('m.facebook.com', True, False), ('fixture.invalid', False, False)]:
        with pytest.raises(ValueError) as error:
            server.fetch_video_url('https://' + host + '/x', 'video', shared_cookies=shared)
        assert ('請改為上傳影片檔' in str(error.value)) is hint
    assert len(calls) == 3


@pytest.mark.parametrize('method,path,endpoint', [
    ('GET', '/api/crawl-videos', 'api_crawl_videos'),
    ('GET', '/api/crawl-videos/preview', 'api_crawl_video_preview'),
    ('POST', '/api/crawl-videos/import', 'api_crawl_video_import'),
])
def test_e_crawler_dispatch_admin_only(state, monkeypatch, method, path, endpoint):
    dispatch_stubs(monkeypatch)
    for user, expected in [(B, 403), (ADMIN, 200)]:
        h = handler(user, path=path)
        setattr(h, endpoint, lambda *args: h.send_json({'ok': True}))
        getattr(h, 'do_' + method)()
        assert h.status == expected


def test_f_voice_ids_are_filtered_and_unowned_is_operator_only(state, monkeypatch):
    entries = [{'id': 'private-alpha', 'filename': 'a.wav', 'ownerEmail': A['email']},
               {'id': 'private-operator', 'filename': 'op.wav', 'ownerEmail': OPERATOR['email']},
               {'id': 'unowned', 'filename': 'u.wav'}]
    monkeypatch.setattr(server, 'load_voice_index', lambda: entries)
    with pytest.raises(ValueError) as error:
        server.resolve_voice_reference('missing', B)
    assert 'private-alpha' not in str(error.value)
    assert 'private-operator' not in str(error.value)
    with pytest.raises(ValueError) as inaccessible:
        server.resolve_voice_reference('private-alpha', B)
    assert '找不到聲音庫項目' in str(inaccessible.value)
    assert not server.user_can_access_voice_entry(B, entries[2])
    assert server.user_can_access_voice_entry(OPERATOR, entries[2])
    assert server.user_can_access_voice_entry(None, entries[2], True)
    state.root.joinpath('voices').mkdir()
    state.root.joinpath('voices', 'u.wav').write_bytes(b'voice')
    with pytest.raises(ValueError):
        server.resolve_voice_reference('unowned', B)
    assert (state.input / server.resolve_voice_reference('unowned', OPERATOR)).is_file()
    assert (state.input / server.resolve_voice_reference('unowned', None, True)).is_file()


def test_g_local_grant_does_not_reveal_account(state, monkeypatch):
    load = Mock(return_value={A['email']: A})
    monkeypatch.setattr(server, 'load_users', load)
    result = []
    for email in (A['email'], identity('missing')['email']):
        h = handler(B, {'email': email, 'allowed': True}, path='/api/compute/platform-admin/local-grant')
        h._provider_console = lambda: SimpleNamespace(user=B)
        server.Handler._handle_provider_console(h, 'PATCH')
        result.append((h.status, h.wfile.getvalue()))
    assert result[0] == result[1]
    assert result[0][0] == 403
    load.assert_not_called()


def test_h_active_uploads_are_attachments_and_static_is_nosniff(state, monkeypatch):
    fake_upload(monkeypatch, state)
    for ext, content_type in [('.html', 'text/html'), ('.svg', 'image/svg+xml')]:
        name = server.save_uploaded_bytes(b'<active/>', 'fixture' + ext, content_type, 'file')
        server.record_input_upload_owner(name, B)
        h = handler(B)
        h.api_view('type=input&filename=' + name)
        assert h.status == 200
        assert h.response_headers['Content-Type'] == 'application/octet-stream'
        assert h.response_headers['Content-Disposition'].startswith('attachment')
        assert h.response_headers['X-Content-Type-Options'] == 'nosniff'
    for name in ('fixture.mp4', 'fixture.png'):
        p = state.input / name
        p.write_bytes(b'media')
        h = handler(B)
        h.send_local_file(p)
        assert h.response_headers['X-Content-Type-Options'] == 'nosniff'
    h = handler(B)
    h.serve_static('app.js')
    assert h.status == 200
    assert 'javascript' in h.response_headers['Content-Type']
    assert h.response_headers['X-Content-Type-Options'] == 'nosniff'
    h = handler(B)
    h.send_local_file(state.input / 'missing.html')
    assert h.status == 404
    assert h.response_headers['X-Content-Type-Options'] == 'nosniff'
    h = handler(B, headers={'Range': 'bytes=999-1000'})
    h.send_local_file(state.input / 'fixture.png')
    assert h.status == 416
    assert h.response_headers['X-Content-Type-Options'] == 'nosniff'


def test_i_error_path_masking_is_role_dependent(state):
    path = '/home/' + 'synthetic/private/file.txt'
    user, admin = handler(B), handler(ADMIN)
    user.send_error_json(ValueError('failure: ' + path), 400)
    admin.send_error_json(ValueError('failure: ' + path), 400)
    assert '<path>' in response_json(user)['error']
    assert path not in response_json(user)['error']
    assert path in response_json(admin)['error']


def test_i_upstream_error_details_are_masked_for_non_admin(state, monkeypatch):
    path = '/var/' + 'synthetic/private/model.bin'
    monkeypatch.setattr(server, 'comfy_error_payload', lambda body: {
        'message': 'missing ' + path,
        'error_code': 'fixture',
        'submission_state': 'failed',
        'technical_detail': 'detail ' + path,
    })
    upstream = server.urllib.error.HTTPError('fixture', 500, 'synthetic', {}, io.BytesIO(b'{}'))
    h = handler(B)
    h.send_error_json(upstream, 400)
    assert '<path>' in response_json(h)['error']
    assert '<path>' in response_json(h)['technical_detail']
    assert path not in json.dumps(response_json(h))


def test_c_client_drive_controls_and_callback_toasts(state):
    js = r'''
const assert = require('assert');
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
let begin = src.indexOf('function renderDriveFetch(');
let end = src.indexOf('\nfunction ', begin + 1);
assert(begin >= 0 && end > begin);
let operator = false, created = [], timers = [], requests = [];
function isOperator() { return operator; }
function h(tag, attrs, children=[]) {
  created.push({tag, attrs});
  return {tag, attrs, children, value: '', textContent: '', className: '', disabled: false,
    classList: {add(){}, toggle(){}}, addEventListener(){}, appendChild(){}};
}
function setTimeout(fn) { timers.push(fn); }
async function api(path) { requests.push(path); return {configured:false}; }
function useServerVideo() {}
function formatBytes() { return ''; }
eval(src.slice(begin, end));
renderDriveFetch({id:'video'}, {}, {});
assert(created.some(x => x.attrs.placeholder === '貼上公開分享的 Google Drive 連結'));
assert(!created.some(x => x.attrs.text === '登入 Google Drive'));
assert.strictEqual(timers.length, 0);
operator = true; created = []; timers = [];
renderDriveFetch({id:'video'}, {}, {});
assert(created.some(x => x.attrs.text === '登入 Google Drive'));
assert.strictEqual(timers.length, 1);
timers[0]();
assert.deepStrictEqual(requests, ['/api/google/status']);
begin = src.indexOf('  const callbackUrl =', src.indexOf('function boot()'));
end = src.indexOf('\n  primeRouteBeforePaint();', begin);
assert(begin >= 0 && end > begin);
const callback = new Function('window', 'history', 'showToast', src.slice(begin,end));
for (const [value,expected] of [
  ['connected','Google Drive 已連結'],
  ['session_error','請在登入本站的同一個瀏覽器與網址完成 Google Drive 連結'],
  ['denied','Google Drive 連結未完成，請再試一次']]) {
  let messages=[], replaced=[];
  callback({location:{href:'https://fixture.invalid/?keep=yes&google_drive='+value+'#route'}},
    {state:{}, replaceState:(...a)=>replaced.push(a[2])}, x=>messages.push(x));
  assert.deepStrictEqual(messages,[expected]);
  const parsed = new URL(replaced[0]);
  assert.strictEqual(parsed.searchParams.has('google_drive'),false);
  assert.strictEqual(parsed.searchParams.get('keep'),'yes');
  assert.strictEqual(parsed.hash,'#route');
}
'''
    app_js = Path(__file__).resolve().parents[1] / 'webui' / 'static' / 'app.js'
    proc = subprocess.run(['node', '-e', js, str(app_js)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_d_timeout_includes_login_hint_only_for_unshared_cookie(state, monkeypatch):
    calls, _ = downloader_setup(state, monkeypatch)
    monkeypatch.setattr(server.socket, 'getaddrinfo', lambda *a, **k: addr('8.8.8.8'))
    monkeypatch.setattr(server.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('fixture', 1)))
    for shared, expected in [(False, True), (True, False)]:
        with pytest.raises(ValueError) as error:
            server.fetch_video_url('https://' + 'm.facebook.com' + '/x', 'video', shared_cookies=shared)
        assert ('請改為上傳影片檔' in str(error.value)) is expected


@pytest.mark.parametrize('endpoint,function', [
    ('api_fetch_url', 'fetch_video_url'), ('api_fetch_drive', 'fetch_google_drive_video')
])
def test_d_routes_pass_operator_cookie_decision(state, monkeypatch, endpoint, function):
    fetch = Mock(return_value={'filename': 'fixture.mp4'})
    monkeypatch.setattr(server, function, fetch)
    monkeypatch.setattr(server, 'record_input_upload_owner', lambda *args: None)
    for user, allowed in [(B, False), (ADMIN, False), (OPERATOR, True)]:
        h = handler(user, {'url': 'https://' + 'fixture.invalid' + '/x', 'slot': 'video'})
        getattr(h, endpoint)()
        assert h.status == 200
        assert fetch.call_args.kwargs == {'shared_cookies': allowed}


def test_d_mixed_public_and_private_dns_answers_are_refused(state, monkeypatch):
    calls, _ = downloader_setup(state, monkeypatch)
    monkeypatch.setattr(server.socket, 'getaddrinfo', lambda *a, **k: addr('8.8.8.8') + addr('10.0.0.5'))
    with pytest.raises(ValueError, match='不是公開網址'):
        server.fetch_video_url('https://' + 'fixture.invalid' + '/x', 'video', shared_cookies=False)
    assert not calls


def test_a1b_long_suffix_and_normal_extensions(state, monkeypatch):
    fake_upload(monkeypatch, state)
    long_name = server.save_uploaded_bytes(b'image', 'photo.' + 'z' * 150, 'image/png', 'image')
    assert len(long_name.encode()) <= 200
    assert long_name.endswith('.png')
    assert 'photo.' + 'z' * 30 in long_name
    ordinary = server.save_uploaded_bytes(b'video', 'ordinary.MP4', 'video/mp4', 'clip')
    assert ordinary.endswith('.MP4')
    assert server.staged_input_name('web', '!', 'plain', 'image/unknown').endswith('.png')
    with pytest.raises(ValueError):
        server.staged_input_name('unknown', 'slot', 'fixture.png')


def test_a1b_mime_fallback_extensions_are_validated(monkeypatch):
    original_guess = mimetypes.guess_extension

    def controlled_guess(content_type, strict=True):
        guesses = {
            'text/x-c++src': '.c++',
            'application/sarif-external-properties+json': '.' + 'a' * 20,
            'image/x-unknown-test': None,
        }
        if content_type in guesses:
            return guesses[content_type]
        return original_guess(content_type, strict=strict)

    monkeypatch.setattr(mimetypes, 'guess_extension', controlled_guess)
    original = 'sample.' + 'z' * 150
    for content_type, extension in (
        ('text/x-c++src', ''),
        ('application/sarif-external-properties+json', ''),
        ('image/x-unknown-test', '.png'),
        ('VIDEO/MP4; codecs=fixture', '.mp4'),
    ):
        name = server.staged_input_name('web', 'file', original, content_type)
        assert re.fullmatch(r'[A-Za-z0-9._-]{1,200}', name)
        if extension:
            assert name.endswith(extension)
        else:
            assert name.endswith('z') and '+' not in name


def test_a1b_registered_mime_types_produce_safe_names():
    mimetypes.init()
    original = 'base.' + 'z' * 295
    for content_type in [*mimetypes.types_map.values(), *mimetypes.common_types.values()]:
        name = server.staged_input_name('web', 'file', original, content_type)
        assert re.fullmatch(r'[A-Za-z0-9._-]{1,200}', name), content_type


def test_k_all_eight_input_writers_generate_random_bounded_names(state, monkeypatch):
    import shutil

    source = state.root / 'fixture.mp4'
    source.write_bytes(b'video')
    voice = state.root / 'voices' / 'fixture.wav'
    voice.parent.mkdir()
    voice.write_bytes(b'audio')
    monkeypatch.setattr(server, 'experimental_user_asset_path', lambda *a: source)
    monkeypatch.setattr(server, 'load_voice_index', lambda: [{'id': 'fixture', 'filename': voice.name}])
    monkeypatch.setattr(server, 'user_can_access_voice_entry', lambda *a: True)
    monkeypatch.setattr(server, 'google_drive_file_meta', lambda *a: {'name': 'x' * 300 + '.mp4', 'mimeType': 'video/mp4'})
    monkeypatch.setattr(server, 'google_access_token', lambda: 'fixture')
    monkeypatch.setattr(server.urllib.request, 'urlopen', lambda *a, **k: contextlib.closing(io.BytesIO(b'video')))
    monkeypatch.setattr(server, 'resolve_crawl_video_path', lambda *a: source)
    monkeypatch.setattr(server, '_video_codec', lambda *a: 'h264')
    monkeypatch.setattr(server, 'ensure_cv2_compatible_video', lambda a, b: shutil.copyfile(a, b))
    monkeypatch.setattr(server, 'resolve_dance_library_template', lambda *a: ({}, {'id': 'fixture'}))
    monkeypatch.setattr(server, 'prepare_dance_library_derivative', lambda *a: source)
    monkeypatch.setattr(server, '_load_dance_library_catalog_unlocked', lambda: {})
    monkeypatch.setattr(server, 'resolve_view_path', lambda *a: source)
    monkeypatch.setattr(server, 'gallery_media_kind', lambda *a: 'video')
    downloader_setup(state, monkeypatch, 0)
    monkeypatch.setattr(server.socket, 'getaddrinfo', lambda *a, **k: addr('8.8.8.8'))
    writers = {
        'web': lambda: server.save_uploaded_bytes(b'video', 'x' * 300 + '.mp4', 'video/mp4', 'VIDEO'),
        'user_photo': lambda: server.import_user_photo_to_input({}, 'fixture.mp4', 'video')['filename'],
        'voice': lambda: server.resolve_voice_reference('fixture', A),
        'drive': lambda: server.import_google_drive_file('fixture', 'video')['filename'],
        'crawl': lambda: server.import_crawl_video('fixture', 'video')['filename'],
        'dance_library': lambda: server.import_dance_library_template('fixture', 'video')['filename'],
        'chain': lambda: server.import_view_item_to_input('fixture.mp4', '', 'output', 'video')['filename'],
        'url': lambda: server.fetch_video_url('http://' + 'fixture.invalid/x', 'video', shared_cookies=True)['filename'],
    }
    for kind, writer in writers.items():
        names = [writer(), writer()]
        assert names[0] != names[1], kind
        for name in names:
            assert re.match(r'^castle_' + kind + r'_\d{10}_[0-9a-f]{32}_', name), kind
            assert re.fullmatch(r'[A-Za-z0-9._-]{1,200}', name), kind
            assert len(name.encode()) <= 200, kind
            assert (state.input / name).is_file(), kind
        if kind in {'web', 'drive'}:
            assert all(name.endswith('.mp4') for name in names)
    max_slot = server.staged_input_name('dance_library', 'a' * 100, 'x' * 300 + '.mp4')
    assert len(max_slot.encode()) <= 200 and max_slot.endswith('.mp4')


def test_l_source_sidecars_are_hidden_before_receipt_or_prefix(state):
    for name in ('castle_fixture.source.json', 'plain.SOURCE.JSON'):
        (state.input / name).write_bytes(b'metadata')
        server.record_input_upload_owner(name, B)
        ordinary, administrator, missing = handler(B), handler(ADMIN), handler(B)
        ordinary.api_view('type=input&filename=' + name)
        administrator.api_view('type=input&filename=' + name)
        missing.api_view('type=input&filename=missing.json')
        assert ordinary.status == missing.status == 404
        assert ordinary.wfile.getvalue() == missing.wfile.getvalue()
        assert administrator.status == 200 and administrator.wfile.getvalue() == b'metadata'


