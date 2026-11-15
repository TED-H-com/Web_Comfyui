"""A loopback HTTP proxy that connects only to validated global addresses."""

from __future__ import annotations

import ipaddress
import re
import select
import socket
import threading
import time
import urllib.parse


_NAT64_WELL_KNOWN = ipaddress.IPv6Network('64:ff9b::/96')
_IPV4_COMPATIBLE = ipaddress.IPv6Network('::/96')
_IPV4_TRANSLATED = ipaddress.IPv6Network('::ffff:0:0:0/96')


def address_allowed(ip: str) -> bool:
    """True only for a global unicast address; IPv4 carried inside IPv6 is checked as IPv4."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address in _IPV4_COMPATIBLE or address in _IPV4_TRANSLATED:
            return False
        elif address in _NAT64_WELL_KNOWN:
            address = ipaddress.IPv4Address(address.packed[-4:])
    return address.is_global and not address.is_multicast


class GlobalOnlyProxy:
    def __init__(self, *, resolver=socket.getaddrinfo, allowed=address_allowed,
                 connect=socket.create_connection, ports=(80, 443),
                 max_connections=32, idle_timeout=60.0):
        self.resolver = resolver
        self.allowed = allowed
        self.connect = connect
        self.ports = ports
        self.max_connections = max_connections
        self.idle_timeout = idle_timeout
        self.refusals = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sockets = set()
        self._threads = set()
        self._active = 0
        self._listener = None
        self._accept_thread = None
        self.url = ""

    def __enter__(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.25)
        self._listener = listener
        self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self._accept_thread = threading.Thread(target=self._accept, daemon=True)
        self._accept_thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        with self._lock:
            sockets = list(self._sockets)
            threads = list(self._threads)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._accept_thread is not None:
            self._accept_thread.join(2)
        for thread in threads:
            thread.join(2)
        return False

    @staticmethod
    def _response(client, status):
        try:
            client.sendall(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
        except OSError:
            pass

    def _refuse(self, client, status, reason):
        with self._lock:
            self.refusals.append(reason)
        self._response(client, status)

    def _accept(self):
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if self._stop.is_set():
                    client.close()
                    break
                if self._active >= self.max_connections:
                    busy = True
                else:
                    busy = False
                    self._active += 1
                    self._sockets.add(client)
                    thread = threading.Thread(target=self._handle, args=(client,), daemon=True)
                    self._threads.add(thread)
                    thread.start()
            if busy:
                self._response(client, "503 Service Unavailable")
                client.close()

    def _head(self, client):
        client.settimeout(15)
        data = b""
        while b"\r\n\r\n" not in data and len(data) <= 16384:
            chunk = client.recv(16385 - len(data))
            if not chunk:
                break
            data += chunk
        end = data.find(b"\r\n\r\n")
        if end < 0 or end + 4 > 16384:
            raise ValueError("malformed")
        return data[:end].decode("iso-8859-1"), data[end + 4:]

    @staticmethod
    def _parse(head):
        lines = head.split("\r\n")
        match = re.fullmatch(r"(\S+) (\S+) HTTP/1\.[0-9]", lines[0])
        if not match:
            raise ValueError("malformed")
        method, target = match.groups()
        tunnel = method == "CONNECT"
        if tunnel:
            parsed = urllib.parse.urlsplit("//" + target)
            if parsed.path or parsed.query or parsed.fragment or parsed.port is None:
                raise ValueError("malformed")
            port = parsed.port
            path = None
        else:
            if method not in {"GET", "HEAD", "POST", "OPTIONS"}:
                raise ValueError("malformed")
            parsed = urllib.parse.urlsplit(target)
            if parsed.scheme != "http" or not parsed.netloc or parsed.fragment:
                raise ValueError("malformed")
            port = parsed.port if parsed.port is not None else 80
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("malformed")
        headers = []
        for line in lines[1:]:
            if ":" not in line or not line.split(":", 1)[0].strip():
                raise ValueError("malformed")
            name = line.split(":", 1)[0].strip().lower()
            if name not in {"proxy-connection", "proxy-authorization", "connection"}:
                headers.append(line)
        return method, parsed.hostname, port, path, headers

    def _address(self, client, host, port):
        if port not in self.ports:
            self._refuse(client, "403 Forbidden", "port")
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            try:
                answers = self.resolver(host, port, type=socket.SOCK_STREAM)
            except OSError:
                answers = []
            if not answers:
                self._refuse(client, "502 Bad Gateway", "resolve_failed")
                return None
            addresses = [answer[4][0] for answer in answers]
        else:
            addresses = [host]
        if any(not self.allowed(ip) for ip in addresses):
            self._refuse(client, "403 Forbidden", "non_global")
            return None
        return addresses[0]

    def _relay(self, client, upstream):
        sockets = (client, upstream)
        last_data = time.monotonic()
        while not self._stop.is_set():
            remaining = self.idle_timeout - (time.monotonic() - last_data)
            if remaining <= 0:
                return
            readable, _, _ = select.select(sockets, (), (), remaining)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (upstream if source is client else client).sendall(data)
                last_data = time.monotonic()

    def _handle(self, client):
        upstream = None
        try:
            try:
                head, body = self._head(client)
                method, host, port, path, headers = self._parse(head)
            except (OSError, ValueError, UnicodeError):
                self._refuse(client, "400 Bad Request", "malformed")
                return
            address = self._address(client, host, port)
            if address is None:
                return
            try:
                upstream = self.connect((address, port), 15)
            except OSError:
                self._response(client, "502 Bad Gateway")
                return
            with self._lock:
                if self._stop.is_set():
                    upstream.close()
                    return
                self._sockets.add(upstream)
            upstream.settimeout(15)
            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                request = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers + ["Connection: close", "", ""])
                upstream.sendall(request.encode("iso-8859-1"))
            if body:
                upstream.sendall(body)
            self._relay(client, upstream)
        except (OSError, ValueError):
            pass
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    with self._lock:
                        self._sockets.discard(sock)
                    sock.close()
            with self._lock:
                self._active -= 1
                self._threads.discard(threading.current_thread())
