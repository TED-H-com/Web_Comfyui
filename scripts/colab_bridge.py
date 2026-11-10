#!/usr/bin/env python3
"""Private, optional Google Colab transport. One JSON request on stdin per process.

Install requirements-colab.txt in a dedicated Python >=3.12 environment. There is
no ADC, HOME override, global CLI state, implicit login, or GPU work on import.
The parent owns authorization, media ownership, durable jobs, and budget policy.

Common request: {"op": ..., "account_dir": "/absolute/private/account"}.
Operations (all responses are {"ok": bool, "result": ...} or {"error": ...}):
  oauth_begin: -> authorization_url, flow_id, expires_at
  oauth_finish: flow_id, code -> authenticated, subject_key
  account_identity: -> bound, subject_key (local only; no SDK or Google request)
  account_status: -> authenticated, balance_compute_units,
                  consumption_rate_per_hour, active_assignments
  runtime_allocate/runtime_lookup: allocation_id (UUID), gpu -> found,
                  runtime_id (same UUID), accelerator, state
  runtime_stop: runtime_id -> stopped, runtime_id
  remote_exec: runtime_id, code, timeout_seconds (1..300) -> stdout, outputs
  upload/download: runtime_id, local_path, remote_path (/content/...),
                  max_bytes (1..67108864) -> bytes, sha256

runtime_lookup only performs GETs. An allocation UUID attempted before cannot
be allocated again: reconcile with lookup. Proxy tokens/URLs stay in the private
account directory. Remote code must be parent-reviewed; this is not a public
arbitrary-code API. Stdout is bounded/redacted transport data, not a UI log.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import stat
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit
import uuid

VERSION = "0.7.4"
FLOW_TTL = 600
MAX_FILE = 64 * 1024 * 1024
MAX_REQUEST = 2 * 1024 * 1024
MAX_OUTPUT = 512 * 1024
GPUS = frozenset({"L4", "G4", "A100", "H100"})
OPS = frozenset({"oauth_begin", "oauth_finish", "account_identity", "account_status", "runtime_allocate",
                 "runtime_lookup", "runtime_stop", "remote_exec", "upload", "download"})
MUTATING_REMOTE = frozenset({"runtime_allocate", "runtime_stop", "remote_exec", "upload"})
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.profile",
          "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/cloud-platform",
          "https://www.googleapis.com/auth/colaboratory",
          "https://www.googleapis.com/auth/drive.file"]
REDIRECT = "https://sdk.cloud.google.com/applicationdefaultauthcode.html"
# Google's OIDC discovery document advertises this endpoint and immutable sub:
# https://accounts.google.com/.well-known/openid-configuration
# https://developers.google.com/identity/openid-connect/openid-connect
GOOGLE_ISSUER = "https://accounts.google.com"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class BridgeError(Exception):
    def __init__(self, code: str, message: str, *, retryable=False, ambiguous=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.ambiguous = retryable, ambiguous

    def public(self):
        return {"code": self.code, "message": self.message,
                "retryable": self.retryable, "ambiguous": self.ambiguous}


def invalid():
    return BridgeError("invalid_request", "The Colab request is invalid.")


def absolute_path(value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise invalid()
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise invalid()
    # Explicit filesystem scope only; never follow symlinks to another account.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise invalid()
    return path


def private_directory(value: object) -> Path:
    path = absolute_path(value)
    if path == Path(path.anchor):
        raise invalid()
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
        raise invalid()
    os.chmod(path, 0o700)
    return path


def atomic_json(path: Path, data: object):
    if path.is_symlink():
        raise invalid()
    fd, name = tempfile.mkstemp(prefix=".colab-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def private_json(path: Path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST:
            raise invalid()
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise invalid()
        os.fchmod(stream.fileno(), 0o600)
        return json.load(stream)


@contextlib.contextmanager
def account_lock(account: Path):
    import fcntl  # The upstream CLI supports Linux and macOS.
    fd = os.open(account / ".bridge.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BridgeError("account_busy", "Another Colab operation is active.", retryable=True)
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def identifier(value: object) -> str:
    if not isinstance(value, str):
        raise invalid()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise invalid() from None
    if str(parsed) != value.lower():
        raise invalid()
    return str(parsed)


def bounded_int(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise invalid()
    return value


def verified_subject(value: object) -> str:
    # Google documents sub as case-sensitive ASCII, <=255 characters. Never
    # substitute email, an unverified ID-token payload, or a browser-supplied id.
    if not isinstance(value, str) or re.fullmatch(r"[\x21-\x7e]{1,255}", value) is None:
        raise BridgeError("identity_unverified", "Google did not return a verified account identity; reconnect and try again.")
    return value


def subject_key(subject: str) -> str:
    return hashlib.sha256((GOOGLE_ISSUER + "\0" + subject).encode("ascii")).hexdigest()


def remote_path(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048 or "\\" in value or "\x00" in value:
        raise invalid()
    p = PurePosixPath(value)
    if not value.startswith("/content/") or ".." in p.parts or str(p) != value or len(p.parts) > 14:
        raise invalid()
    return value


def proxy_url(value: object) -> str:
    if not isinstance(value, str):
        raise invalid()
    p = urlsplit(value)
    host = p.hostname or ""
    if (p.scheme != "https" or p.username or p.password or p.query or p.fragment
            or p.port not in (None, 443)
            or not (host == "colab.research.google.com" or host.endswith(".googleusercontent.com")
                    or host.endswith(".r.colab.dev"))):
        raise BridgeError("invalid_runtime", "Google returned an unsupported runtime address.")
    return value.rstrip("/")


class GoogleSDK:
    """Pinned SDK compatibility; constructed only for an explicit request."""
    def __init__(self):
        try:
            if importlib.metadata.version("google-colab-cli") != VERSION:
                raise BridgeError("dependency_version", "Install the pinned Colab controller requirements.")
            import requests
            from colab_cli.client import Client, Prod, Accelerator, Variant, Assignment
            from colab_cli.runtime import ColabRuntime
            from google.auth.transport.requests import AuthorizedSession, Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from importlib import resources
        except (ImportError, importlib.metadata.PackageNotFoundError):
            raise BridgeError("dependency_missing", "Install the optional Colab controller requirements.") from None
        self.requests, self.Client, self.Prod = requests, Client, Prod
        self.Accelerator, self.Variant, self.Assignment = Accelerator, Variant, Assignment
        self.Runtime, self.Credentials = ColabRuntime, Credentials
        self.AuthorizedSession, self.Request, self.Flow = AuthorizedSession, Request, InstalledAppFlow
        self.client_config = json.loads(resources.files("colab_cli").joinpath("oauth_config.json").read_text())

    def flow(self, *, verifier=None, state=None):
        flow = self.Flow.from_client_config(self.client_config, SCOPES,
                                           autogenerate_code_verifier=verifier is None,
                                           code_verifier=verifier, state=state)
        flow.redirect_uri = REDIRECT
        flow.oauth2session.trust_env = False
        original_request = flow.oauth2session.request

        def request(method, url, **kwargs):
            # requests-oauthlib treats unknown fetch_token kwargs as OAuth form
            # fields, so redirect control belongs on the actual HTTP session.
            kwargs["timeout"] = (10, 30)
            kwargs["allow_redirects"] = False
            response = original_request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                response.close()
                raise BridgeError("redirect_rejected", "Google returned an unexpected authentication redirect.")
            return response

        flow.oauth2session.request = request
        return flow

    def http(self, credentials=None):
        # AuthorizedSession otherwise creates its own ambient requests.Session
        # for automatic refresh after a 401, bypassing our explicit transport.
        refresh = self.http() if credentials is not None else None
        session = (self.AuthorizedSession(credentials, auth_request=self.Request(session=refresh),
                                          refresh_timeout=60)
                   if credentials is not None else self.requests.Session())
        session.trust_env = False  # No netrc, proxy, or ambient credential discovery.
        original = session.request

        def request(method, url, **kwargs):
            kwargs["timeout"] = (10, 60)
            kwargs["allow_redirects"] = False
            # v0.7.4 needs an explicit body for POSTs behind some HTTP proxies.
            if method.upper() == "POST" and "json" not in kwargs and "data" not in kwargs:
                kwargs["json"] = {}
            response = original(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                response.close()
                raise BridgeError("redirect_rejected", "The Colab service returned an unexpected redirect.")
            return response

        session.request = request
        if refresh is not None:
            original_close = session.close

            def close():
                try:
                    original_close()
                finally:
                    refresh.close()

            session.close = close
        return session

    @contextlib.contextmanager
    def execution_transport(self):
        # The pinned Jupyter dependency calls requests.get/post directly. Replace
        # only its fetch adapter in this isolated subprocess; never touch SDK
        # source files or the parent application's global requests behavior.
        import jupyter_kernel_client.manager as manager
        import websocket

        original_fetch = manager.fetch
        original_run = websocket.WebSocketApp.run_forever
        original_timeout = websocket.getdefaulttimeout()
        with self.http() as transport:
            def fetch(url, token=None, **kwargs):
                headers = {"Accept": "application/json", "Content-Type": "application/json"}
                headers.update(kwargs.pop("headers", {}))
                if token:
                    headers["Authorization"] = "Bearer " + token
                response = transport.request(kwargs.pop("method", "GET"), url, headers=headers, **kwargs)
                response.raise_for_status()
                return response

            def run(socket, *args, **kwargs):
                kwargs.update(http_no_proxy=["*"], http_proxy_host=None,
                              http_proxy_auth=None, reconnect=0)
                return original_run(socket, *args, **kwargs)

            manager.fetch = fetch
            websocket.WebSocketApp.run_forever = run
            websocket.setdefaulttimeout(10)
            try:
                yield
            finally:
                manager.fetch = original_fetch
                websocket.WebSocketApp.run_forever = original_run
                websocket.setdefaulttimeout(original_timeout)

    def credentials(self, token_path):
        try:
            data = private_json(token_path)
        except FileNotFoundError:
            raise BridgeError("authentication_required", "Connect a Google account first.") from None
        try:
            credentials = self.Credentials.from_authorized_user_info(data, SCOPES)
            if not credentials.valid:
                if not credentials.refresh_token:
                    raise ValueError()
                with self.http() as transport:
                    credentials.refresh(self.Request(session=transport))
                atomic_json(token_path, json.loads(credentials.to_json()))
            if not credentials.valid:
                raise ValueError()
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("authentication_required", "Reconnect the selected Google account.") from None
        return credentials

    def subject(self, credentials):
        """Verify identity over Google's HTTPS userinfo endpoint, not JWT decoding."""
        try:
            with self.http(credentials) as transport:
                response = transport.request("GET", USERINFO_URL, stream=True)
                try:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(4096):
                        size += len(chunk)
                        if size > 32768:
                            raise ValueError()
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
                    return verified_subject(data.get("sub"))
                finally:
                    response.close()
        except Exception:
            raise BridgeError("identity_unverified", "Google did not return a verified account identity; reconnect and try again.") from None

    def client(self, credentials):
        return self.Client(self.Prod(), self.http(credentials))


class Bridge:
    def __init__(self, account: Path, sdk, *, clock=time.time):
        self.account, self.sdk, self.clock = account, sdk, clock
        self.token_path = account / "token.json"
        self.identity_path = account / "identity.json"
        self.pending_path = account / "pending_login.json"
        self.runtime_dir = private_directory(str(account / "runtimes"))
        self.secrets = []
        self._client = None

    @property
    def client(self):
        if self._client is None:
            credentials = self.sdk.credentials(self.token_path)
            self.ensure_identity(credentials)
            for key in ("token", "refresh_token", "id_token"):
                value = getattr(credentials, key, None)
                if value:
                    self.secrets.append(value)
            self._client = self.sdk.client(credentials)
        return self._client

    def identity(self):
        try:
            data = private_json(self.identity_path)
        except FileNotFoundError:
            return None
        except Exception:
            raise BridgeError("identity_unverified", "The saved Google account identity is invalid; keep the existing runtime records.") from None
        if not isinstance(data, dict) or data.get("issuer") != GOOGLE_ISSUER:
            raise BridgeError("identity_unverified", "The saved Google account identity is invalid; keep the existing runtime records.")
        return verified_subject(data.get("subject"))

    def bind_identity(self, subject):
        subject = verified_subject(subject)
        previous = self.identity()
        if previous is not None and not secrets.compare_digest(previous, subject):
            raise BridgeError("account_identity_mismatch", "Choose the same Google account originally connected to this profile; its existing credentials were kept.")
        if previous is None:
            # Binding first makes a crash before token replacement fail closed.
            atomic_json(self.identity_path, {"issuer": GOOGLE_ISSUER, "subject": subject,
                                            "verified_at": self.clock()})
        return subject_key(subject)

    def ensure_identity(self, credentials=None):
        subject = self.identity()
        if subject is not None:
            return subject_key(subject)
        if credentials is None and not self.token_path.exists():
            if any(self.runtime_dir.glob("*.json")):
                raise BridgeError("legacy_identity_unverified", "This older profile has no verified identity. Confirm its runtimes are released in Google Colab before creating a new profile.")
            return None
        try:
            credentials = credentials or self.sdk.credentials(self.token_path)
            return self.bind_identity(self.sdk.subject(credentials))
        except Exception:
            raise BridgeError("legacy_identity_unverified", "This older profile's original Google identity could not be verified. Confirm its runtimes are released in Google Colab before creating a new profile.") from None

    def account_identity(self, request):
        subject = self.identity()
        return {"bound": subject is not None, "subject_key": subject_key(subject) if subject is not None else None}

    def redact(self, value: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[redacted]")
        # Runtime proxy query tokens must not reach public/persistent logs.
        return re.sub(r"(?i)(colab-runtime-proxy-token=)[^\s&\"']+", r"\1[redacted]", value)

    def record_path(self, runtime_id):
        return self.runtime_dir / (identifier(runtime_id) + ".json")

    def record(self, runtime_id):
        try:
            data = private_json(self.record_path(runtime_id))
        except FileNotFoundError:
            raise BridgeError("runtime_unknown", "This account has no record of that runtime.") from None
        if data.get("runtime_id") != runtime_id:
            raise BridgeError("invalid_runtime", "The private runtime record is invalid.")
        return data

    def save_assignment(self, runtime_id, gpu, assignment, previous=None):
        proxy = assignment.runtime_proxy_info
        endpoint = assignment.endpoint
        if not isinstance(endpoint, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", endpoint):
            raise BridgeError("invalid_runtime", "Google returned an invalid runtime identity.", ambiguous=True)
        data = dict(previous or {}, runtime_id=runtime_id, accelerator=gpu,
                    endpoint=endpoint, url=proxy_url(proxy.url), token=proxy.token,
                    state="allocated", updated_at=self.clock())
        if not isinstance(proxy.token, str) or not proxy.token:
            raise BridgeError("invalid_runtime", "Google returned invalid runtime credentials.", ambiguous=True)
        atomic_json(self.record_path(runtime_id), data)
        self.secrets.extend([proxy.token, proxy.url])
        return data

    def runtime(self, runtime_id):
        data = self.record(runtime_id)
        for assignment in self.client.list_assignments():
            if assignment.endpoint == data.get("endpoint"):
                return self.save_assignment(runtime_id, data["accelerator"], assignment, data)
        raise BridgeError("runtime_missing", "The Colab runtime is no longer available.")

    def oauth_begin(self, request):
        # Existing unbound credentials must establish their original identity
        # before any replacement OAuth flow, including flows begun pre-upgrade.
        self.ensure_identity()
        flow = self.sdk.flow()
        url, state = flow.authorization_url(prompt="consent", token_usage="remote", access_type="offline")
        flow_id = secrets.token_urlsafe(32)
        expires = self.clock() + FLOW_TTL
        atomic_json(self.pending_path, {"flow_id": flow_id, "state": state,
                    "verifier": flow.code_verifier, "expires_at": expires})
        return {"authorization_url": url, "flow_id": flow_id, "expires_at": expires}

    def oauth_finish(self, request):
        code, flow_id = request.get("code"), request.get("flow_id")
        if not isinstance(code, str) or not 1 <= len(code.strip()) <= 8192 or not isinstance(flow_id, str):
            raise invalid()
        try:
            pending = private_json(self.pending_path)
        except FileNotFoundError:
            raise BridgeError("oauth_flow_missing", "Start a new Google connection.") from None
        if not secrets.compare_digest(pending.get("flow_id", ""), flow_id):
            raise BridgeError("oauth_flow_mismatch", "This Google connection belongs to another login attempt.")
        # Consume once before exchange; no replay even after an uncertain response.
        self.pending_path.unlink()
        if self.clock() >= pending["expires_at"]:
            raise BridgeError("oauth_flow_expired", "The Google connection expired; start again.")
        self.ensure_identity()
        flow = self.sdk.flow(verifier=pending["verifier"], state=pending["state"])
        try:
            flow.fetch_token(code=code.strip(), timeout=30)
            credentials = flow.credentials
            if not credentials.refresh_token:
                raise ValueError()
        except Exception:
            raise BridgeError("oauth_exchange_failed", "Google did not complete the connection; start again.") from None
        key = self.bind_identity(self.sdk.subject(credentials))
        atomic_json(self.token_path, json.loads(credentials.to_json()))
        return {"authenticated": True, "subject_key": key}

    def account_status(self, request):
        info = self.client.get_consumption_user_info()
        balance, rate = info.paid_compute_units_balance, info.consumption_rate_hourly
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
                   for v in (balance, rate)):
            raise BridgeError("invalid_account_status", "Google did not return usable compute-unit information.")
        count = bounded_int(info.assignments_count, 0, 10000)
        return {"authenticated": True, "balance_compute_units": balance,
                "consumption_rate_per_hour": rate, "active_assignments": count,
                "subject_key": self.account_identity({})["subject_key"]}

    def allocation(self, request, *, lookup):
        runtime_id = identifier(request.get("allocation_id"))
        gpu = request.get("gpu")
        if gpu not in GPUS:
            raise invalid()
        path = self.record_path(runtime_id)
        previous = private_json(path) if path.exists() else None
        if previous and previous.get("accelerator") != gpu:
            raise BridgeError("allocation_conflict", "The runtime GPU differs from its saved allocation.")
        if not lookup and previous:
            raise BridgeError("allocation_already_attempted", "Reconcile the existing allocation before proceeding.", ambiguous=True)
        kwargs = {"variant": self.sdk.Variant.GPU, "accelerator": self.sdk.Accelerator(gpu)}
        if lookup:
            # A GET may return a challenge token, but must never consume it in a POST.
            assignment = self.client._get_assignment(uuid.UUID(runtime_id), **kwargs)
            if not isinstance(assignment, self.sdk.Assignment):
                return {"found": False, "runtime_id": runtime_id, "accelerator": gpu, "state": "absent"}
        else:
            # Initialize authentication before marking a provider request attempted.
            client = self.client
            atomic_json(path, {"runtime_id": runtime_id, "accelerator": gpu,
                               "state": "allocating", "created_at": self.clock()})
            try:
                assignment = client.assign(uuid.UUID(runtime_id), **kwargs)
            except Exception:
                raise BridgeError("allocation_uncertain", "Allocation was not confirmed; reconcile this runtime.", ambiguous=True) from None
        self.save_assignment(runtime_id, gpu, assignment, previous)
        return {"found": True, "runtime_id": runtime_id, "accelerator": gpu, "state": "allocated"}

    def runtime_allocate(self, request):
        return self.allocation(request, lookup=False)

    def runtime_lookup(self, request):
        return self.allocation(request, lookup=True)

    def runtime_stop(self, request):
        runtime_id = identifier(request.get("runtime_id"))
        data = self.record(runtime_id)
        endpoint = data.get("endpoint")
        if not endpoint:
            raise BridgeError("runtime_unreconciled", "Reconcile the allocation before stopping it.", ambiguous=True)
        assignments = self.client.list_assignments()
        if any(a.endpoint == endpoint for a in assignments):
            atomic_json(self.record_path(runtime_id), dict(data, state="stop_pending"))
            try:
                self.client.unassign(endpoint)
            except Exception:
                raise BridgeError("stop_uncertain", "Runtime shutdown is not yet confirmed.", ambiguous=True) from None
            if any(a.endpoint == endpoint for a in self.client.list_assignments()):
                raise BridgeError("stop_pending", "Runtime shutdown is still pending.", retryable=True, ambiguous=True)
        # Preserve identity for recovery, remove transient proxy credentials.
        atomic_json(self.record_path(runtime_id), {"runtime_id": runtime_id, "accelerator": data["accelerator"],
                                                  "endpoint": endpoint, "state": "stopped", "updated_at": self.clock()})
        return {"stopped": True, "runtime_id": runtime_id}

    def remote_exec(self, request):
        runtime_id = identifier(request.get("runtime_id"))
        code = request.get("code")
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 1024 * 1024:
            raise invalid()
        timeout = bounded_int(request.get("timeout_seconds", 60), 1, 300)
        data = self.runtime(runtime_id)

        def remember(field, value):
            data[field] = value
            atomic_json(self.record_path(runtime_id), data)

        runtime = self.sdk.Runtime(data["url"], data["token"], kernel_id=data.get("kernel_id"),
            session_id=data.get("session_id"), on_kernel_started=lambda v: remember("kernel_id", v),
            on_session_started=lambda v: remember("session_id", v))
        with self.sdk.execution_transport():
            try:
                outputs = runtime.execute_code(code, allow_stdin=False, timeout=timeout)
            except Exception:
                raise BridgeError("execution_uncertain", "Remote execution was not confirmed; inspect the job state.", ambiguous=True) from None
            finally:
                try:
                    # v0.7.4 only fires its callbacks when the supplied IDs were
                    # empty. If Jupyter reports a saved kernel as gone, it starts
                    # a replacement but omits those callbacks; without capturing
                    # the actual IDs every subsequent poll starts another kernel.
                    client = getattr(runtime, "_kernel_client", None)
                    if client is not None and client.id:
                        if client.id != data.get("kernel_id"):
                            remember("kernel_id", client.id)
                        session_id = client._manager.client.session.session
                        if session_id and session_id != data.get("session_id"):
                            remember("session_id", session_id)
                finally:
                    runtime.stop(shutdown_kernel=False)
        clean, stdout, size = [], [], 0
        for output in outputs:
            kind = output.get("output_type")
            if kind == "error":
                # Tracebacks can contain user media, code, and service credentials.
                raise BridgeError("remote_execution_failed", "The remote code reported an error; inspect the private job status.")
            if kind == "stream":
                value = output.get("text", "")
                if isinstance(value, list):
                    value = "".join(value)
                if not isinstance(value, str):
                    raise BridgeError("invalid_remote_output", "Remote output was malformed.")
                value = self.redact(value)
                size += len(value.encode())
                if size > MAX_OUTPUT:
                    raise BridgeError("output_too_large", "Remote output exceeded the transport limit.")
                clean.append({"output_type": "stream", "name": output.get("name", "stdout"), "text": value})
                if output.get("name", "stdout") == "stdout":
                    stdout.append(value)
        return {"stdout": "".join(stdout), "outputs": clean}

    def transfer(self, request, *, download):
        runtime_id = identifier(request.get("runtime_id"))
        local = absolute_path(request.get("local_path"))
        remote = remote_path(request.get("remote_path"))
        maximum = bounded_int(request.get("max_bytes", MAX_FILE), 1, MAX_FILE)
        if local == self.account or self.account in local.parents:
            raise BridgeError("private_path", "Account credentials cannot be transferred as media.")
        if download:
            if not local.parent.is_dir() or (local.exists() and not local.is_file()):
                raise invalid()
        elif not local.is_file() or local.stat().st_size > maximum:
            raise BridgeError("input_too_large", "The input is missing or exceeds the transfer limit.")
        data = self.runtime(runtime_id)
        self.secrets.append(data["token"])
        url = data["url"] + "/api/contents/" + quote(remote.strip("/"), safe="/")
        params = {"authuser": "0", "colab-runtime-proxy-token": data["token"]}
        with self.sdk.http() as transport:
            if not download:
                with open(local, "rb") as stream:
                    content = stream.read(maximum + 1)
                if len(content) > maximum:
                    raise BridgeError("input_too_large", "The input exceeds the transfer limit.")
                # Jupyter PUT directory is idempotent for existing directories;
                # create ancestors before the first upload in a fresh runtime.
                # The fixed /content root must already exist in Colab.
                for parent in reversed(PurePosixPath(remote).parents):
                    if str(parent) in {"/", "/content"}:
                        continue
                    directory_url = data["url"] + "/api/contents/" + quote(str(parent).strip("/"), safe="/")
                    response = transport.request("PUT", directory_url, params=params,
                                                 json={"type": "directory"})
                    try:
                        response.raise_for_status()
                        if response.json().get("type") != "directory":
                            raise BridgeError("invalid_remote_directory", "The runtime upload directory is unavailable.")
                    finally:
                        response.close()
                payload = {"name": PurePosixPath(remote).name, "path": remote, "type": "file", "format": "base64",
                           "content": base64.b64encode(content).decode("ascii"), "chunk": 1}
                response = transport.request("PUT", url, params=params, json=payload)
                response.raise_for_status()
                response.close()
            else:
                # Explicit base64 also covers valid UTF-8 binary data (e.g. a
                # short silent WAV), which Jupyter otherwise returns as text.
                response = transport.request("GET", url, params=dict(params, content="1", format="base64"), stream=True)
                try:
                    response.raise_for_status()
                    encoded_bound = 4 * ((maximum + 2) // 3)
                    # Jupyter uses MIME base64 with 76-column line wrapping;
                    # JSON escapes LF (or CRLF) and adds wire bytes per line.
                    bound = encoded_bound + 4 * ((encoded_bound + 75) // 76) + 1024 * 1024
                    chunks, total = [], 0
                    for chunk in response.iter_content(64 * 1024):
                        total += len(chunk)
                        if total > bound:
                            raise BridgeError("download_too_large", "The output exceeds the transfer limit.")
                        chunks.append(chunk)
                    payload = json.loads(b"".join(chunks))
                    if payload.get("type") != "file" or payload.get("format") != "base64":
                        raise BridgeError("invalid_download", "The runtime did not return a binary file.")
                    encoded = payload.get("content")
                    if not isinstance(encoded, str):
                        raise BridgeError("invalid_download", "The runtime returned malformed file content.")
                    encoded = encoded.translate(str.maketrans("", "", " \t\r\n"))
                    if len(encoded) > encoded_bound:
                        raise BridgeError("download_too_large", "The output exceeds the transfer limit.")
                    # Strip only permitted MIME whitespace; retain strict
                    # validation of every base64 character and its padding.
                    content = base64.b64decode(encoded, validate=True)
                    if len(content) > maximum:
                        raise BridgeError("download_too_large", "The output exceeds the transfer limit.")
                finally:
                    response.close()
                fd, part = tempfile.mkstemp(prefix=".colab-download-", dir=local.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(part, local)
                finally:
                    Path(part).unlink(missing_ok=True)
        return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def upload(self, request):
        return self.transfer(request, download=False)

    def download(self, request):
        return self.transfer(request, download=True)


def dispatch(request: object, *, sdk_factory=GoogleSDK):
    if not isinstance(request, dict) or request.get("op") not in OPS:
        raise invalid()
    account = private_directory(request.get("account_dir"))
    with account_lock(account):
        bridge = Bridge(account, None if request["op"] == "account_identity" else sdk_factory())
        return getattr(bridge, request["op"])(request)


def handle(request: object, *, sdk_factory=GoogleSDK):
    try:
        return {"ok": True, "result": dispatch(request, sdk_factory=sdk_factory)}
    except BridgeError as error:
        return {"ok": False, "error": error.public()}
    except Exception:
        op = request.get("op") if isinstance(request, dict) else None
        error = BridgeError("transport_failed", "The Colab operation failed; no private details were returned.",
                            retryable=op not in MUTATING_REMOTE, ambiguous=op in MUTATING_REMOTE)
        return {"ok": False, "error": error.public()}


def main():
    # SDK loggers can include tokens in URLs and HTTP headers. The bridge never
    # emits their text. No secrets are accepted through command-line arguments.
    logging.disable(logging.CRITICAL)
    if len(sys.argv) != 1:
        response = {"ok": False, "error": invalid().public()}
    else:
        try:
            raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST:
                raise invalid()
            request = json.loads(raw)
            # A bounded parent process timeout remains required on every platform.
            # This guard also covers websocket connection setup in the pinned SDK.
            if hasattr(signal, "SIGALRM"):
                def expired(_signum, _frame):
                    raise BridgeError("operation_timeout", "The Colab operation timed out; reconcile remote state.",
                                      ambiguous=request.get("op") in MUTATING_REMOTE)
                signal.signal(signal.SIGALRM, expired)
                signal.alarm(420)
            response = handle(request)
        except BridgeError as error:
            response = {"ok": False, "error": error.public()}
        except Exception:
            response = {"ok": False, "error": invalid().public()}
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
    print(json.dumps(response, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
