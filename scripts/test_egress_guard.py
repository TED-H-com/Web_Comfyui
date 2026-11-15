"""Synthetic loopback tests for downloader egress enforcement."""
import http.server
import io
import re
import select
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import egress_guard
import hosted_compute_http
import material_collector
import server


def address(ip, port):
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, '', (ip, port))]


def direct_connect(target, timeout):
    sock = socket.socket(socket.AF_INET6 if ':' in target[0] else socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(target)
    return sock


def raw_request(proxy, message):
    port = urllib.parse.urlsplit(proxy.url).port
    with socket.socket() as client:
        client.settimeout(3)
        client.connect(('127.0.0.1', port))
        client.sendall(message)
        data = b''
        while b'\r\n\r\n' not in data:
            part = client.recv(4096)
            if not part:
                break
            data += part
        return data


@pytest.fixture
def origin():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.requests.append((self.path, dict(self.headers)))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', 'http://' + 'internal.test:' + str(self.server.redirect_port) + '/x')
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'fixture body')
        def log_message(self, *args):
            pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    srv.requests = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(2)


def opener(proxy):
    return urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy.url}))


def test_address_allowed_checks_mapped_multicast_and_reserved():
    for ip in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '0.0.0.0',
               '224.0.0.1', '::1', '::ffff:127.0.0.1', 'fc00::1',
               '64:ff9b::7f00:1', '64:ff9b::a00:1', '64:ff9b::a9fe:a9fe',
               '::127.0.0.1', '::10.0.0.1',
               '::ffff:0:7f00:1', '::ffff:0:808:808', 'ff02::1'):
        assert not egress_guard.address_allowed(ip), ip
    assert egress_guard.address_allowed('8.8.8.8')
    assert egress_guard.address_allowed('64:ff9b::808:808')


def test_other_fetchers_refuse_ipv4_carried_in_ipv6(monkeypatch):
    for answer in ('64:ff9b::a00:1', '::127.0.0.1'):
        resolved = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', (answer, 443, 0, 0))]
        monkeypatch.setattr(material_collector.socket, 'getaddrinfo', lambda *args, rows=resolved, **kwargs: rows)
        with pytest.raises(ValueError):
            material_collector.public_target('https://media.example/a.png')
        monkeypatch.setattr(hosted_compute_http.socket, 'getaddrinfo', lambda *args, rows=resolved, **kwargs: rows)
        with pytest.raises(hosted_compute_http.TransportError):
            hosted_compute_http._resolve('media.example', 2)


def test_proxy_forwards_absolute_http_and_removes_proxy_headers(origin, monkeypatch):
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    port = origin.server_address[1]
    resolver = lambda host, port, **kw: address('127.0.0.1', port)
    with egress_guard.GlobalOnlyProxy(resolver=resolver, allowed=lambda ip: ip == '127.0.0.1',
                                      connect=direct_connect, ports=(port,)) as proxy:
        req = urllib.request.Request('http://' + f'public.test:{port}/hello?x=1',
                                     headers={'Proxy-Connection': 'keep-alive',
                                              'Proxy-Authorization': 'fixture'})
        assert opener(proxy).open(req, timeout=3).read() == b'fixture body'
        assert proxy.refusals == []
    path, headers = origin.requests[0]
    assert path == '/hello?x=1'
    assert not any(k.lower() in ('proxy-connection', 'proxy-authorization') for k in headers)
    assert headers['Connection'] == 'close'


def test_proxy_rejects_redirect_before_internal_connection(origin, monkeypatch):
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    with socket.socket() as internal:
        internal.bind(('127.0.0.2', 0))
        internal.listen()
        origin.redirect_port = internal.getsockname()[1]
        port = origin.server_address[1]
        def resolver(host, target_port, **kwargs):
            return address('127.0.0.2' if host == 'internal.test' else '127.0.0.1', target_port)
        with egress_guard.GlobalOnlyProxy(resolver=resolver, allowed=lambda ip: ip == '127.0.0.1',
                                          connect=direct_connect, ports=(port, origin.redirect_port)) as proxy:
            with pytest.raises(urllib.error.HTTPError) as caught:
                opener(proxy).open('http://' + f'public.test:{port}/redirect', timeout=3)
            assert caught.value.code == 403
            assert proxy.refusals == ['non_global']
        assert not select.select([internal], [], [], 0.2)[0]


def test_proxy_resolves_once_and_connects_to_checked_address():
    queries, targets = [], []
    def resolver(host, port, **kwargs):
        queries.append((host, port))
        return address('8.8.8.8' if len(queries) == 1 else '127.0.0.1', port)
    def connect(target, timeout):
        targets.append(target)
        raise ConnectionRefusedError()
    with egress_guard.GlobalOnlyProxy(resolver=resolver, connect=connect, ports=(8765,)) as proxy:
        response = raw_request(proxy, b'GET http://' + b'public.test:8765/x HTTP/1.1\r\nHost: public.test\r\n\r\n')
        assert response.startswith(b'HTTP/1.1 502')
    assert len(queries) == 1
    assert targets == [('8.8.8.8', 8765)]


def test_proxy_rejects_disallowed_port_malformed_and_excess_connections():
    with egress_guard.GlobalOnlyProxy(max_connections=1) as proxy:
        def wait_for_active(expected):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with proxy._lock:
                    if proxy._active == expected:
                        return
                time.sleep(0.01)
            pytest.fail('proxy connection count did not settle')

        assert raw_request(proxy, b'CONNECT public.test:22 HTTP/1.1\r\nHost: public.test\r\n\r\n').startswith(b'HTTP/1.1 403')
        wait_for_active(0)
        assert raw_request(proxy, b'GET / HTTP/1.1\r\nHost: public.test\r\n\r\n').startswith(b'HTTP/1.1 400')
        wait_for_active(0)
        port = urllib.parse.urlsplit(proxy.url).port
        with socket.socket() as first:
            first.connect(('127.0.0.1', port))
            wait_for_active(1)
            assert raw_request(proxy, b'GET / HTTP/1.1\r\n\r\n').startswith(b'HTTP/1.1 503')
            assert proxy.refusals == ['port', 'malformed']


def download_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'COMFY_INPUT', tmp_path / 'input')
    monkeypatch.setattr(server, 'CRAWL_DIR', tmp_path)
    monkeypatch.setattr(server, '_resolve_yt_dlp', lambda: '/fixture/yt-dlp')
    monkeypatch.setattr(server, '_crawl_cookie_file', lambda url: None)
    monkeypatch.setattr(server, '_video_codec', lambda path: 'h264')
    monkeypatch.setattr(server, 'socket', SimpleNamespace(
        getaddrinfo=lambda host, port, **kwargs: address('8.8.8.8', port), SOCK_STREAM=socket.SOCK_STREAM))


def test_fetch_proxy_argv_environment_and_operator_path(tmp_path, monkeypatch):
    download_setup(tmp_path, monkeypatch)
    monkeypatch.setenv('NO_PROXY', 'fixture')
    monkeypatch.setenv('no_proxy', 'fixture')
    calls = []
    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        filename = argv[argv.index('-o') + 1].replace('%(title).40s', 'clip').replace('%(ext).16s', 'mp4')
        Path(filename).write_bytes(b'video')
        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')
    monkeypatch.setattr(server.subprocess, 'run', run)
    for shared in (False, True):
        result = server.fetch_video_url('http://' + 'public.test/x', 'v' * 100, shared_cookies=shared)
        assert re.match(r'^castle_url_\d{10}_[0-9a-f]{32}_', result['filename'])
        assert len(result['filename'].encode()) <= 200
    argv, kwargs = calls[0]
    pos = argv.index('--proxy')
    assert pos < argv.index('--')
    proxy_url = argv[pos + 1]
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        assert kwargs['env'][key] == proxy_url
    assert 'NO_PROXY' not in kwargs['env'] and 'no_proxy' not in kwargs['env']
    assert '--proxy' not in calls[1][0] and 'env' not in calls[1][1]


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'no_file'])
@pytest.mark.parametrize('refusal', ['non_global', 'port'])
def test_fetch_refusal_overrides_failure_message(tmp_path, monkeypatch, failure, refusal):
    download_setup(tmp_path, monkeypatch)
    proxy_class = egress_guard.GlobalOnlyProxy
    monkeypatch.setattr(server.egress_guard, 'GlobalOnlyProxy', lambda: proxy_class(
        resolver=lambda host, port, **kwargs: address('127.0.0.2', port)))
    def run(argv, **kwargs):
        url = argv[argv.index('--proxy') + 1]
        port = urllib.parse.urlsplit(url).port
        with socket.socket() as client:
            client.settimeout(3)
            client.connect(('127.0.0.1', port))
            if refusal == 'port':
                client.sendall(b'CONNECT public.test:22 HTTP/1.1\r\nHost: public.test\r\n\r\n')
            else:
                client.sendall(b'GET http://' + b'internal.test/ HTTP/1.1\r\nHost: internal.test\r\n\r\n')
            assert client.recv(1024).startswith(b'HTTP/1.1 403')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0 if failure == 'no_file' else 1, stdout='', stderr='failure')
    monkeypatch.setattr(server.subprocess, 'run', run)
    with pytest.raises(ValueError, match='這個連結會轉到非公開或不支援的位址，無法下載；請改為上傳影片檔'):
        server.fetch_video_url('http://' + 'public.test/x', 'video', shared_cookies=False)


def test_connect_tunnels_buffered_bytes_and_relay():
    with socket.socket() as origin:
        origin.bind(('127.0.0.1', 0))
        origin.listen()
        port = origin.getsockname()[1]
        received = []
        def echo():
            upstream, _ = origin.accept()
            with upstream:
                upstream.settimeout(3)
                received.append(upstream.recv(128))
                upstream.sendall(b'echo:' + received[0])
        worker = threading.Thread(target=echo, daemon=True)
        worker.start()
        resolver = lambda host, target_port, **kwargs: address('127.0.0.1', target_port)
        with egress_guard.GlobalOnlyProxy(resolver=resolver, allowed=lambda ip: ip == '127.0.0.1',
                                          connect=direct_connect, ports=(port,)) as proxy:
            proxy_port = urllib.parse.urlsplit(proxy.url).port
            with socket.socket() as client:
                client.settimeout(3)
                client.connect(('127.0.0.1', proxy_port))
                client.sendall(f'CONNECT public.test:{port} HTTP/1.1\r\nHost: public.test\r\n\r\nhello'.encode())
                response = b''
                while b'echo:hello' not in response:
                    part = client.recv(1024)
                    assert part
                    response += part
                assert response.startswith(b'HTTP/1.1 200 Connection established\r\n\r\n')
        worker.join(2)
        assert received == [b'hello']


def test_mixed_dns_answers_are_rejected_and_never_connected():
    targets = []
    def resolver(host, port, **kwargs):
        return address('8.8.8.8', port) + address('127.0.0.1', port)
    def connect(target, timeout):
        targets.append(target)
        raise AssertionError('must not connect')
    with egress_guard.GlobalOnlyProxy(resolver=resolver, connect=connect) as proxy:
        response = raw_request(proxy, b'GET http://' + b'public.test/ HTTP/1.1\r\nHost: public.test\r\n\r\n')
        assert response.startswith(b'HTTP/1.1 403')
        assert proxy.refusals == ['non_global']
    assert not targets


@pytest.mark.parametrize('raw_data', [
    b'GET https://' + b'public.test/ HTTP/1.1\r\nHost: public.test\r\n\r\n',
    b'PUT http://' + b'public.test/ HTTP/1.1\r\nHost: public.test\r\n\r\n',
    b'GET http://' + b'public.test/ HTTP/1.1\r\nX-Fill: ' + b'a' * 16384 + b'\r\n\r\n',
], ids=['https', 'method', 'oversized'])
def test_proxy_rejects_invalid_forms_and_oversized_heads(raw_data):
    with egress_guard.GlobalOnlyProxy() as proxy:
        assert raw_request(proxy, raw_data).startswith(b'HTTP/1.1 400')
        assert proxy.refusals == ['malformed']


def test_fetch_conversion_suffix_stays_within_name_limit(tmp_path, monkeypatch):
    download_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(server, '_video_codec', lambda path: 'av1')
    monkeypatch.setattr(server, 'ensure_cv2_compatible_video', lambda source, target: target.write_bytes(b'converted'))
    def run(argv, **kwargs):
        filename = argv[argv.index('-o') + 1].replace('%(title).40s', 't' * 40).replace('%(ext).16s', 'mp4')
        Path(filename).write_bytes(b'video')
        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')
    monkeypatch.setattr(server.subprocess, 'run', run)
    result = server.fetch_video_url('http://' + 'public.test/x', 'v' * 100, shared_cookies=True)
    assert result['filename'].endswith('_cv2.mp4')
    assert len(result['filename'].encode()) <= 200
