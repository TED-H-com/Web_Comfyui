"""Replaying needs explicit opt-in and cannot send public graph data off-host."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import replay_recorded_demo as replay


@pytest.mark.parametrize('url', [
    'https://127.0.0.1:8188', 'http://example.com', 'http://192.168.1.2:8188',
    'http://user:password@127.0.0.1:8188', 'http://127.0.0.1:8188?token=abc',
    'file:///tmp/example', 'http://localhost.evil.example',
])
def test_nonlocal_or_credential_urls_rejected(url):
    with pytest.raises(ValueError):
        replay.local_url(url)


def test_default_only_describes_without_network_or_disk_changes(monkeypatch, capsys):
    record = {'id': 'example', 'feature_id': 'text2img', 'device': 'test GPU', 'inputs': []}
    monkeypatch.setattr(replay, 'load_record', lambda _: (record, {'1': {}}))
    monkeypatch.setattr(replay, 'submit', lambda *_: pytest.fail('Unexpected network request'))
    assert replay.main(['example']) == 0
    assert 'Validated only' in capsys.readouterr().out


@pytest.mark.parametrize('node_type,field', [('LoadImage', 'image'), ('LoadAudio', 'audio'), ('VHS_LoadVideo', 'video')])
def test_rewrites_only_recorded_inputs_and_refuses_overwrite(tmp_path, monkeypatch, node_type, field):
    examples = tmp_path / 'examples'
    examples.mkdir()
    payload = b'public example image bytes'
    (examples / 'reference.webp').write_bytes(payload)
    monkeypatch.setattr(replay, 'PUBLIC_DEMO_ASSETS', {'reference.webp': hashlib.sha256(payload).hexdigest()})
    inputs = tmp_path / 'input'
    inputs.mkdir()
    run = {'inputs': [{'graph_name': 'original.webp', 'file': 'reference.webp'}]}
    graph = {'1': {'class_type': node_type, 'inputs': {field: 'original.webp'}}}
    rewritten = replay.prepare_inputs(run, graph, inputs, examples)
    assert rewritten['1']['inputs'][field] == 'web-demo-replay/reference.webp'
    target = inputs / 'web-demo-replay/reference.webp'
    assert target.read_bytes() == payload
    target.write_bytes(b'personal bytes')
    graph['1']['inputs'][field] = 'original.webp'
    with pytest.raises(ValueError, match='unrelated'):
        replay.prepare_inputs(run, graph, inputs, examples)
    assert target.read_bytes() == b'personal bytes'


def test_unrecorded_input_rejected_before_copying(tmp_path):
    graph = {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'private.webp'}}}
    with pytest.raises(ValueError, match='Unrecorded'):
        replay.prepare_inputs({'inputs': []}, graph, tmp_path)
    assert not (tmp_path / 'web-demo-replay').exists()


def test_submit_local_graph_and_refuse_redirect():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if len(requests) == 1:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"prompt_id":"test-prompt"}')
            else:
                self.send_response(302)
                self.send_header('Location', 'http://example.com/prompt')
                self.end_headers()
    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        assert replay.submit(url, {'1': {'class_type': 'Example'}})['prompt_id'] == 'test-prompt'
        assert requests[0] == {'prompt': {'1': {'class_type': 'Example'}}}
        with pytest.raises(ValueError, match='redirects'):
            replay.submit(url, {})
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_every_committed_record_validates_without_submission(monkeypatch):
    monkeypatch.setattr(replay, 'submit', lambda *_: pytest.fail('Unexpected GPU submission'))
    ledger = json.loads((replay.ROOT / 'webui/docs/examples/provenance.json').read_text())
    assert ledger['runs']
    for run in ledger['runs']:
        assert replay.main([run['id']]) == 0


def test_recorded_lipsync_can_prepare_both_video_and_synthetic_audio(tmp_path):
    run, graph = replay.load_record('cheer-lipsync-v1')
    inputs = tmp_path / 'input'
    inputs.mkdir()
    prepared = replay.prepare_inputs(run, graph, inputs)
    assert prepared['1']['inputs']['video'] == 'web-demo-replay/cheer-wave.mp4'
    assert prepared['2']['inputs']['audio'] == 'web-demo-replay/warm-welcome.wav'
    assert len(list((inputs / 'web-demo-replay').iterdir())) == 2


def test_rehashed_graph_with_unreviewed_node_is_rejected(tmp_path):
    graph = {'1': {'class_type': 'ArbitraryPythonExecution', 'inputs': {}}}
    checksum = replay.digest(json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
    provenance = {'runs': [{'id': 'unsafe', 'status': 'success',
                           'workflow': {'graph': graph, 'sha256': checksum}}]}
    (tmp_path / 'provenance.json').write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match='unreviewed node'):
        replay.load_record('unsafe', tmp_path)
