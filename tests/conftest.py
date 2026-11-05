import json
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import socket
import urllib.request
import os
import subprocess
import sys
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

@pytest.fixture(scope="module")
def test_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("test_server")
    class Comfy(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            payload = {'system': {'comfyui_version': 'test'}, 'devices': []} if self.path == '/system_stats' else {}
            if self.path == '/queue': payload = {'queue_running': [], 'queue_pending': []}
            if self.path == '/object_info':
                payload = {}
            raw = json.dumps(payload).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            if self.path == '/prompt':
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', '25'); self.end_headers(); self.wfile.write(b'{"prompt_id": "test-123"}')
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

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    process = None
    try:
        with (tmp_path / 'server.log').open('wb') as log:
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
            opener.open(urllib.request.Request(base + '/api/auth/setup', data=body, headers={'Content-Type': 'application/json', 'Origin': base}))

            req = urllib.request.Request(base + '/api/auth/login', data=json.dumps({'password': 'synthetic-boot-password'}).encode(), headers={'Content-Type': 'application/json', 'Origin': base})
            resp = opener.open(req)
            cookies = resp.headers.get_all('Set-Cookie')
            assert cookies

            cookie_header = cookies[0].split(';')[0]

            yield base, opener, cookie_header
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        comfy.shutdown(); comfy.server_close()
