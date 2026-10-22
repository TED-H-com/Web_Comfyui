"""Boot the packaged app against a fake ComfyUI, with empty disk-backed state."""
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

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_first_run_with_fake_comfy(tmp_path):
    requests = []
    class Comfy(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            requests.append(self.path)
            payload = {'system': {'comfyui_version': 'test'}, 'devices': []} if self.path == '/system_stats' else {}
            if self.path == '/queue': payload = {'queue_running': [], 'queue_pending': []}
            raw = json.dumps(payload).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            requests.append('POST ' + self.path)
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
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    process = None
    try:
        with (tmp_path / 'server.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, str(ROOT / 'run.py'), '--no-env', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
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
            with opener.open(base + '/api/workflow-library', timeout=5) as response:
                catalog = json.load(response)
            assert len(catalog['items']) >= 240
            assert sum(bool(item.get('available')) for item in catalog['items']) == 46
            with opener.open(base + '/api/characters', timeout=5) as response:
                assert json.load(response)['characters'] == []
            assert not any(path.startswith('POST ') for path in requests), 'Startup must not execute a workflow'
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        comfy.shutdown(); comfy.server_close()
