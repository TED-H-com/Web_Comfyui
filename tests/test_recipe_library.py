"""End-to-end integration tests verifying recipe search filtering, model checkpoint schema validation, and workflow export."""
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="module")
def test_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("test_server")
    class Comfy(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            payload = {'system': {'comfyui_version': 'test'}, 'devices': []} if self.path == '/system_stats' else {}
            if self.path == '/queue': payload = {'queue_running': [], 'queue_pending': []}
            if self.path == '/object_info':
                # Return object_info for schema validation checks
                payload = {
                    'UNETLoader': {'input': {'required': {'unet_name': ['COMBO', {'options': ['test_model.safetensors']}]}}},
                    'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': ['COMBO', {'options': ['test_ckpt.safetensors']}]}}},
                    'LoraLoader': {'input': {'required': {'lora_name': ['COMBO', {'options': ['test_lora.safetensors']}]}}}
                }
            raw = json.dumps(payload).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            if self.path.startswith('/userdata/workflows/webui-library/') or self.path.startswith('/userdata/'):
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')
                return
            self.send_error(405)
    comfy = ThreadingHTTPServer(('127.0.0.1', 0), Comfy)
    threading.Thread(target=comfy.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]

    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG') if key in os.environ}
    env.update({
        'COMFY_HOME': str(tmp_path / 'comfy'),
        'COMFYUI_URL': f'http://127.0.0.1:{comfy.server_port}',
        'WEB_COMFYUI_DATA_DIR': str(tmp_path / 'data'),
        'WEB_COMFYUI_CACHE_DIR': str(tmp_path / 'cache'),
        'WEB_COMFYUI_PORT': str(port),
        'WEB_COMFYUI_ALLOWED_ORIGIN_HOSTS': 'localhost',
        'PYTHONDONTWRITEBYTECODE': '1',
    })

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    process = None
    try:
        with (tmp_path / 'server.log').open('wb') as log:
            # When pytest is run with `uv run --with pillow`, the environment will have PIL
            # but standard `subprocess.Popen([sys.executable...])` might lose `PYTHONPATH` or `uv` context
            # if run.py spawns a raw python executable. To be safe, use Python from the current env context.
            # (which inherently has pillow).
            process = subprocess.Popen([sys.executable, str(ROOT / 'run.py'), '--no-env'], cwd=ROOT, env=env, stdout=log, stderr=log)
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 12
            while True:
                try:
                    with opener.open(base + '/api/auth/status', timeout=1) as response:
                        status = json.load(response)
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError((tmp_path / 'server.log').read_text())
                    time.sleep(0.05)
            assert not status['configured']
            body = json.dumps({'password': 'synthetic-boot-password'}).encode()
            with opener.open(urllib.request.Request(base + '/api/auth/setup', data=body, headers={'Content-Type': 'application/json', 'Origin': base}), timeout=5) as response:
                assert response.status == 200
            yield base, opener
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        comfy.shutdown(); comfy.server_close()

def test_recipe_library_filtering(test_server):
    """Test that recipe library entries are available and correctly categorized."""
    base, opener = test_server
    with opener.open(base + '/api/workflow-library', timeout=5) as response:
        catalog = json.load(response)

    recipes = [item for item in catalog['items'] if item.get('kind') == 'recipe']
    assert len(recipes) > 0, "Should have recipes in the catalog"

    assert all(r.get('id', '').startswith('recipe-') for r in recipes)
    assert all('guide' in r for r in recipes)
    assert all('title' in r for r in recipes)
    assert all('description' in r for r in recipes)

def test_parameter_schema_validation(test_server):
    """Test model checkpoint schema validation against object_info."""
    base, opener = test_server

    with opener.open(base + '/api/workflow-library?check=1', timeout=5) as response:
        catalog = json.load(response)

    assert catalog['connection'] == {'checked': True, 'connected': True}
    workflows = [item for item in catalog['items'] if item.get('kind') == 'workflow' and item.get('available')]
    assert len(workflows) > 0, "Should have workflows"
    has_checked = False
    for wf in workflows:
        if 'models_checked' in wf:
            has_checked = True
            break
    assert has_checked, "Dependency checking via schema validation should populate models_checked"

def test_workflow_export(test_server):
    """Test exporting a workflow to the ComfyUI workspace."""
    base, opener = test_server

    with opener.open(base + '/api/workflow-library', timeout=5) as response:
        catalog = json.load(response)

    workflow = next((item for item in catalog['items'] if item.get('kind') == 'workflow' and item.get('available') and item.get('format') == 'visual'), None)

    if workflow:
        body = json.dumps({'id': workflow['id']}).encode()
        try:
            with opener.open(urllib.request.Request(base + '/api/workflow-library/open', data=body, headers={'Content-Type': 'application/json', 'Origin': base}), timeout=5) as response:
                assert response.status == 200
                result = json.load(response)
                assert result['ok'] is True
                assert 'filename' in result
                assert result['filename'].startswith('workflows/webui-library/')
        except urllib.error.HTTPError as e:
            error_content = e.read().decode('utf-8')
            pytest.fail(f"HTTPError: {e.code} {e.reason} - {error_content}")
