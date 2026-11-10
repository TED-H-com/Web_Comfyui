"""Exercise actual HTTP framing over a synthetic loopback TLS server only."""
import http.server
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

from webui import hosted_compute_http as T


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    if not shutil.which('openssl'):
        pytest.skip('openssl needed only to create an ephemeral loopback TLS certificate')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=cloud.comfy.org', '-addext', 'subjectAltName=DNS:cloud.comfy.org',
        '-keyout', str(key), '-out', str(cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    received = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.command, self.path, dict(self.headers), self.rfile.read(int(self.headers.get('Content-Length', 0)))))
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self.wfile.write(b'3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n')
        def do_GET(self):
            if self.path == '/slow':
                time.sleep(.15)
            self.send_response(200)
            self.send_header('Content-Length', '999' if self.path == '/large' else '10')
            self.end_headers()
            try:
                self.wfile.write(b'abc')  # intentionally truncated
            except OSError:
                pass
        def log_message(self, *_):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    client = ssl.create_default_context(cafile=str(cert))
    # Only this fixture's DNS is injected: actual connect/TLS/HTTP/read execute.
    monkeypatch.setattr(T, '_resolve', lambda *_: (socket.AF_INET, socket.SOCK_STREAM, 0, '', server.server_address))
    monkeypatch.setattr(T.ssl, 'create_default_context', lambda: client)
    try:
        yield received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_real_tls_http_chunked_body_and_auth(tls_server):
    result = T.HTTPS().request('POST', 'https://cloud.comfy.org/api/v2/jobs?test=1',
        headers={'Authorization': 'Bearer synthetic', 'Content-Type': 'application/json'}, body=b'{"x":1}', max_bytes=5)
    assert result.status == 200 and result.body == b'abcde'
    method, path, headers, body = tls_server[0]
    assert method == 'POST' and path == '/api/v2/jobs?test=1'
    assert headers['Host'] == 'cloud.comfy.org' and headers['Authorization'] == 'Bearer synthetic'
    assert body == b'{"x":1}'


@pytest.mark.parametrize('path,timeout,limit', [('/truncated', 1, 20), ('/large', 1, 20), ('/slow', .03, 20)])
def test_real_tls_bounds_truncation_and_timeout(tls_server, path, timeout, limit):
    started = time.monotonic()
    with pytest.raises(T.TransportError):
        T.HTTPS().request('GET', 'https://cloud.comfy.org' + path, timeout=timeout, max_bytes=limit)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['93.184.216.34', '10.0.0.1'], ['169.254.169.254'], ['::1']])
def test_public_dns_guard_blocks_private_and_mixed_answers(monkeypatch, addresses):
    monkeypatch.setattr(T.socket, 'getaddrinfo', lambda *_args, **_kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, '', (ip, 443)) for ip in addresses])
    with pytest.raises(T.TransportError):
        T._resolve('synthetic.example', .2)


@pytest.mark.parametrize('url', ['https://127.0.0.1/private', 'http://example.com', 'https://u:p@example.com',
    'https://example.com:8443', 'https://example.com/#secret', 'https://example.com/\r\nheader', 'https://localhost/',
    'https://example.com\\@127.0.0.1/'])
def test_url_validation_rejects_unsafe_destinations(url):
    with pytest.raises(T.TransportError):
        T.public_url(url)
