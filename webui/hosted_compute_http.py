"""Bounded HTTPS transport for explicitly configured hosted compute providers.

No proxy discovery, redirects, ambient credentials, or HTTP retries. Connections
pin a public DNS result while TLS still verifies the original hostname. Signed
artifact redirects are followed by the service, dropping authentication whenever
the origin changes. Tests inject this narrow byte-level transport.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit


class TransportError(Exception):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


def public_url(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443) or parts.fragment
                or any(ord(c) < 33 or ord(c) > 126 for c in url)
                or '\\' in url or len(url) > 8192):
            raise ValueError()
        host = parts.hostname
        if not all(c.isalnum() or c in '.-' for c in host):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host or host.endswith('.'):
                raise ValueError()
        else:
            if not ip.is_global:
                raise ValueError()
        return parts
    except (ValueError, TypeError, AttributeError) as error:
        raise TransportError('invalid HTTPS destination') from error


def _resolve(host, timeout):
    result, done = [], threading.Event()
    def lookup():
        try:
            result.extend(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            pass
        finally:
            done.set()
    threading.Thread(target=lookup, daemon=True).start()
    if not done.wait(timeout) or not result:
        raise TransportError('DNS unavailable')
    # Reject mixed public/private DNS answers as well as direct local addresses.
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in result):
        raise TransportError('non-public destination')
    return result[0]


class HTTPS:
    def request(self, method, url, *, headers=None, body=None, timeout=30, max_bytes=2 * 1024 * 1024):
        parts = public_url(url)
        deadline = time.monotonic() + timeout
        family, socktype, proto, _, address = _resolve(parts.hostname, min(timeout, 10))
        conn = http.client.HTTPSConnection(parts.hostname, timeout=max(.1, deadline-time.monotonic()))
        raw = socket.socket(family, socktype, proto)
        try:
            raw.settimeout(max(.1, deadline-time.monotonic()))
            raw.connect(address)
            conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
            conn.sock.settimeout(max(.1, deadline-time.monotonic()))
            conn.request(method, parts.path + ('?' + parts.query if parts.query else ''), body=body, headers=headers or {})
            response = conn.getresponse()
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > max_bytes):
                raise TransportError('response exceeds limit')
            chunks, size = [], 0
            while True:
                left = deadline-time.monotonic()
                if left <= 0:
                    raise TransportError('request timed out')
                if conn.sock is not None:
                    conn.sock.settimeout(left)
                chunk = response.read1(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise TransportError('response exceeds limit')
                chunks.append(chunk)
            if length is not None and size != int(length):
                raise TransportError('incomplete response')
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, b''.join(chunks))
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise TransportError('HTTPS request failed') from error
        finally:
            conn.close()
            raw.close()
