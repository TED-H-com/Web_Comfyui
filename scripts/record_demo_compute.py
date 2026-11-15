#!/usr/bin/env python3
"""Record empty compute screens from a fresh, loopback-only server.

Run with the Web UI Python dependencies installed. No .env is read, no existing
state is reused, and the child server is forbidden to open outgoing sockets.
These receipts show empty screens; they are not evidence of GPU generation.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'webui/docs/demo-compute.json'
# Initial reads and reads on opening a panel/details, enumerated from compute-*.js,
# colab.js and provider-fabric.js. Empty state has no account/job/asset detail IDs.
ENDPOINTS = (
    '/api/compute/connections', '/api/colab/session',
    '/api/compute/platform-admin', '/api/compute/capabilities',
    '/api/compute/preferences', '/api/compute/jobs',
    '/api/compute/parity/cases', '/api/compute/pipeline-recipes',
    '/api/compute/pipeline-features', '/api/compute/pipelines', '/api/colab',
    '/api/providers', '/api/provider-characters',
    '/api/assets?kind=image', '/api/history?max_items=500&scope=own',
)
EMAIL = re.compile(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
SECRET = re.compile(r'(?i)(?:\b(?:sk|rk|pk)[-_][a-z0-9_-]{16,}|\b(?:gh[pousr]_|github_pat_|AKIA)[a-z0-9_]{16,}|\bBearer\s+\S+|\beyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+|\b[a-f0-9]{32,}\b|-----BEGIN .*PRIVATE KEY)')
SECRET_FIELDS = {'password', 'api_key', 'apikey', 'credential', 'credential_value',
                 'token', 'access_token', 'refresh_token', 'client_secret',
                 'authorization', 'cookie', 'session_cookie', 'private_key'}
STAMP = re.compile(r'^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d')
UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)


def validate(value, trail='$'):
    """Fail closed without echoing a potentially sensitive value in an error."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SECRET_FIELDS and child not in (None, '', False):
                raise ValueError(f'Credential field at {trail}.{key}')
            validate(key, trail + '.<key>')
            validate(child, trail + '.' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate(child, f'{trail}[{index}]')
    elif isinstance(value, str):
        if any(not email.endswith('@example.invalid') for email in EMAIL.findall(value)):
            raise ValueError(f'Non-example e-mail at {trail}')
        # HTTP request paths are relative web references, not filesystem paths.
        if re.search(r'(?:^|[\s"\'])/(?!api/)[\w.~/-]+|[A-Za-z]:[\\/]|\\\\[^\\]+\\', value):
            raise ValueError(f'Absolute filesystem path at {trail}')
        if SECRET.search(value):
            raise ValueError(f'Token/key-shaped value at {trail}')


def normalize(value, identity, key='', ids=None):
    ids = {} if ids is None else ids
    if isinstance(value, dict):
        return {normalize(k, identity, ids=ids): normalize(v, identity, k, ids)
                for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, identity, key, ids) for v in value]
    if isinstance(value, str):
        value = EMAIL.sub(lambda match: 'demo@example.invalid'
                          if match.group() == identity['email'] else match.group(), value)
        if value == identity.get('displayName'):
            value = 'Demo'
        if STAMP.match(value):
            return '2026-11-14T00:00:00Z'
        if UUID.fullmatch(value):
            return ids.setdefault(value, f'demo-id-{len(ids) + 1}')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and (
        key.endswith(('_at', '_timestamp')) or key in {'created', 'updated', 'now'}
    ):
        return 0
    return value


def serve(port, identity_file):
    # Import only after the same launcher configuration used by --no-env.
    sys.path.insert(0, str(ROOT))
    import run
    run.prepare_environment(port=port)
    def offline(event, args):
        if event == 'socket.connect':
            raise PermissionError('Demo recorder forbids outgoing connections')
    sys.addaudithook(offline)
    sys.path.insert(0, str(ROOT / 'webui'))
    import server
    identity_file.write_text(json.dumps(server.LOCAL_SESSION_USER), encoding='utf-8')
    sys.argv = [str(ROOT / 'webui/server.py'), '--host', '127.0.0.1', '--port', str(port)]
    server.main()


def record():
    with tempfile.TemporaryDirectory(prefix='demo-compute-') as directory:
        state = Path(directory)
        (state / 'home').mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
        env.update({
            'HOME': str(state / 'home'),
            'USERPROFILE': str(state / 'home'),
            'PYTHONPATH': os.pathsep.join(path for path in sys.path if path),
            'COMFY_HOME': str(state / 'absent-comfy'),
            'WEB_COMFYUI_DATA_DIR': str(state / 'data'),
            'WEB_COMFYUI_CACHE_DIR': str(state / 'cache'),
            'WEB_COMFYUI_TMP_DIR': str(state / 'tmp'),
            'XDG_DATA_HOME': str(state / 'xdg-data'),
            'XDG_CONFIG_HOME': str(state / 'xdg-config'),
            'XDG_CACHE_HOME': str(state / 'xdg-cache'),
            'CASTLE_COMFY_ADMISSION_LOCK': str(state / 'gpu.lock'),
            'PYTHONDONTWRITEBYTECODE': '1',
        })
        identity_file = state / 'identity.json'
        base = f'http://127.0.0.1:{port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def call(path, data=None):
            request = urllib.request.Request(base + path,
                data=None if data is None else json.dumps(data).encode(),
                headers={'Content-Type': 'application/json', 'Origin': base})
            try:
                with opener.open(request, timeout=30) as response:
                    return {'status': response.status, 'body': json.load(response)}
            except urllib.error.HTTPError as error:
                return {'status': error.code, 'body': json.load(error)}
        with (state / 'server.log').open('w') as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                '--serve', str(port), str(identity_file)], cwd=ROOT, env=env,
                stdout=log, stderr=log)
            try:
                for _ in range(300):
                    try:
                        status = call('/api/auth/status')['body']
                        break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError('Temporary server failed to start') from None
                        time.sleep(.05)
                else:
                    raise RuntimeError('Temporary server readiness timed out')
                if status['configured']:
                    raise RuntimeError('Recorder requires unconfigured first-run state')
                password = {'password': 'synthetic-demo-password-1'}
                for path in ('/api/auth/setup', '/api/auth/login'):
                    if call(path, password)['status'] != 200:
                        raise RuntimeError('Temporary first-run authentication failed')
                identity = json.loads(identity_file.read_text(encoding='utf-8'))
                ids = {}
                result = {path: normalize(call(path), identity, ids=ids) for path in ENDPOINTS}
                validate(result)
                OUTPUT.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                             indent=2) + '\n', encoding='utf-8')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    print(f'Recorded {len(result)} empty-state responses.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', nargs=2, metavar=('PORT', 'IDENTITY_FILE'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(int(args.serve[0]), Path(args.serve[1]))
    else:
        record()
