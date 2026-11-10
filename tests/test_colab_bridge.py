"""Offline contract tests: these fakes never authenticate or allocate a GPU."""
from __future__ import annotations

import base64
from contextlib import nullcontext
from enum import Enum
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "colab_bridge.py"
SPEC = importlib.util.spec_from_file_location("web_comfyui_colab_bridge", SOURCE)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class Accelerator(str, Enum):
    L4 = "L4"
    G4 = "G4"
    A100 = "A100"
    H100 = "H100"


class Assignment:
    def __init__(self, endpoint="owned-endpoint"):
        self.endpoint = endpoint
        self.runtime_proxy_info = SimpleNamespace(token="private-proxy-token",
            url="https://runtime.colab.googleusercontent.com")


class Credentials:
    token = "private-access-token"
    refresh_token = "private-refresh-token"
    id_token = "private-id-token"

    def to_json(self):
        return json.dumps({"token": self.token, "refresh_token": self.refresh_token})


class Response:
    def __init__(self, payload=None, *, raw=None):
        self.content = raw if raw is not None else json.dumps(payload or {}).encode()
        self.status_code = 200
        self.closed = False

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self.content)

    def iter_content(self, chunk_size):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeSDK:
    Accelerator = Accelerator
    Assignment = Assignment
    Variant = SimpleNamespace(GPU="GPU")

    def __init__(self):
        self.calls = []
        self.assignments = []
        self.lookup_response = SimpleNamespace(token="unused-challenge-token")
        self.allocate_error = None
        self.stop_error = None
        self.stop_leaves_assignment = False
        self.outputs = [{"output_type": "stream", "name": "stdout", "text": "WORKER {}\n"}]
        self.response = Response()
        self.pending_flow = None
        self.balance = 200.5

    def credentials(self, token_path):
        self.calls.append(("credentials", str(token_path)))
        if not token_path.exists():
            raise bridge.BridgeError("authentication_required", "Connect a Google account first.")
        return Credentials()

    def client(self, credentials):
        return self

    def get_consumption_user_info(self):
        return SimpleNamespace(paid_compute_units_balance=self.balance,
                               consumption_rate_hourly=1.5, assignments_count=len(self.assignments))

    def list_assignments(self):
        self.calls.append(("list",))
        return list(self.assignments)

    def assign(self, allocation_id, **kwargs):
        self.calls.append(("assign", str(allocation_id), kwargs))
        if self.allocate_error:
            raise self.allocate_error
        result = Assignment()
        self.assignments.append(result)
        self.lookup_response = result
        return result

    def _get_assignment(self, allocation_id, **kwargs):
        self.calls.append(("lookup", str(allocation_id), kwargs))
        return self.lookup_response

    def unassign(self, endpoint):
        self.calls.append(("unassign", endpoint))
        if self.stop_error:
            raise self.stop_error
        if not self.stop_leaves_assignment:
            self.assignments = [a for a in self.assignments if a.endpoint != endpoint]

    def flow(self, *, verifier=None, state=None):
        parent = self

        class Flow:
            code_verifier = verifier or "private-pkce-verifier"
            credentials = Credentials()

            def authorization_url(self, **kwargs):
                return "https://accounts.google.com/o/oauth2/auth?state=public-state", "public-state"

            def fetch_token(self, **kwargs):
                parent.calls.append(("fetch_token", kwargs, verifier, state))

        return Flow()

    def Runtime(self, url, token, **kwargs):
        parent = self

        class Runtime:
            def execute_code(self, code, **options):
                parent.calls.append(("exec", code, options))
                kwargs["on_kernel_started"]("kernel-1")
                kwargs["on_session_started"]("session-1")
                return parent.outputs

            def stop(self, **options):
                parent.calls.append(("close", options))

        return Runtime()

    def http(self):
        return nullcontext(self)

    def execution_transport(self):
        return nullcontext()

    def request(self, method, url, **kwargs):
        self.calls.append(("http", method, url, kwargs))
        if kwargs.get("json", {}).get("type") == "directory":
            return Response({"type": "directory"})
        return self.response


@pytest.fixture
def setup(tmp_path):
    account = bridge.private_directory(str(tmp_path / "account-a"))
    bridge.atomic_json(account / "token.json", {"token": "token"})
    sdk = FakeSDK()

    def call(op, **kwargs):
        return bridge.handle({"op": op, "account_dir": str(account), **kwargs}, sdk_factory=lambda: sdk)

    return account, sdk, call


def allocate(call):
    runtime_id = str(uuid.uuid4())
    result = call("runtime_allocate", allocation_id=runtime_id, gpu="L4")
    assert result["ok"]
    return runtime_id


def test_account_status_is_read_only_and_account_scoped(setup, tmp_path):
    account, sdk, call = setup
    result = call("account_status")
    assert result["result"] == {"authenticated": True, "balance_compute_units": 200.5,
                                "consumption_rate_per_hour": 1.5, "active_assignments": 0}
    assert sdk.calls == [("credentials", str(account / "token.json"))]
    other = bridge.handle({"op": "account_status", "account_dir": str(tmp_path / "account-b")},
                          sdk_factory=lambda: sdk)
    assert other["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("balance", [None, -1, float("nan"), float("inf"), True])
def test_unreadable_balance_does_not_become_ready(setup, balance):
    _, sdk, call = setup
    sdk.balance = balance
    assert call("account_status")["error"]["code"] == "invalid_account_status"


def test_oauth_pkce_expiry_and_replay(setup):
    account, sdk, call = setup
    begun = call("oauth_begin")["result"]
    pending = bridge.private_json(account / "pending_login.json")
    assert pending["verifier"] == "private-pkce-verifier"
    assert "verifier" not in json.dumps(begun)
    assert stat.S_IMODE((account / "pending_login.json").stat().st_mode) == 0o600
    wrong = call("oauth_finish", flow_id="wrong", code="private-code")
    assert wrong["error"]["code"] == "oauth_flow_mismatch"
    assert (account / "pending_login.json").exists()
    done = call("oauth_finish", flow_id=begun["flow_id"], code="private-code")
    assert done == {"ok": True, "result": {"authenticated": True}}
    assert sdk.calls[-1][2:] == ("private-pkce-verifier", "public-state")
    assert call("oauth_finish", flow_id=begun["flow_id"], code="private-code")["error"]["code"] == "oauth_flow_missing"
    begun = call("oauth_begin")["result"]
    pending = bridge.private_json(account / "pending_login.json")
    bridge.atomic_json(account / "pending_login.json", dict(pending, expires_at=0))
    assert call("oauth_finish", flow_id=begun["flow_id"], code="private-code")["error"]["code"] == "oauth_flow_expired"
    assert not (account / "pending_login.json").exists()


def test_allocation_records_identity_before_transport_and_never_retries(setup):
    account, sdk, call = setup
    runtime_id = str(uuid.uuid4())
    sdk.allocate_error = TimeoutError("SECRET-REFRESH-TOKEN")
    error = call("runtime_allocate", allocation_id=runtime_id, gpu="G4")
    assert error["error"]["ambiguous"] is True
    assert "SECRET" not in json.dumps(error)
    saved = bridge.private_json(account / "runtimes" / (runtime_id + ".json"))
    assert saved["state"] == "allocating"
    assert saved["accelerator"] == "G4"
    error = call("runtime_allocate", allocation_id=runtime_id, gpu="G4")
    assert error["error"]["code"] == "allocation_already_attempted"
    assert len([c for c in sdk.calls if c[0] == "assign"]) == 1


def test_lookup_is_only_get_even_without_private_record(setup):
    _, sdk, call = setup
    runtime_id = str(uuid.uuid4())
    absent = call("runtime_lookup", allocation_id=runtime_id, gpu="L4")
    assert absent["result"]["found"] is False
    sdk.lookup_response = Assignment()
    found = call("runtime_lookup", allocation_id=runtime_id, gpu="L4")
    assert found["result"]["runtime_id"] == runtime_id
    assert not any(c[0] == "assign" for c in sdk.calls)
    assert "private-proxy-token" not in json.dumps(found)


@pytest.mark.parametrize("gpu", ["l4", "L40", "", "T4", None])
def test_unknown_gpu_never_falls_back_to_a100(setup, gpu):
    _, sdk, call = setup
    assert not call("runtime_allocate", allocation_id=str(uuid.uuid4()), gpu=gpu)["ok"]
    assert not sdk.calls


def test_stop_is_confirmed_and_only_owned_endpoint_is_removed(setup):
    account, sdk, call = setup
    runtime_id = allocate(call)
    sdk.assignments.append(Assignment("unrelated-endpoint"))
    result = call("runtime_stop", runtime_id=runtime_id)
    assert result["result"]["stopped"] is True
    assert [a.endpoint for a in sdk.assignments] == ["unrelated-endpoint"]
    saved = bridge.private_json(account / "runtimes" / (runtime_id + ".json"))
    assert saved["state"] == "stopped"
    assert "token" not in saved and "url" not in saved
    assert call("runtime_stop", runtime_id=runtime_id)["result"]["stopped"]
    assert len([c for c in sdk.calls if c[0] == "unassign"]) == 1


@pytest.mark.parametrize("leave,error", [(True, None), (False, TimeoutError("secret"))])
def test_unconfirmed_stop_keeps_private_recovery_state(setup, leave, error):
    account, sdk, call = setup
    runtime_id = allocate(call)
    sdk.stop_leaves_assignment, sdk.stop_error = leave, error
    result = call("runtime_stop", runtime_id=runtime_id)
    assert not result["ok"] and result["error"]["ambiguous"]
    assert bridge.private_json(account / "runtimes" / (runtime_id + ".json"))["state"] == "stop_pending"


def test_remote_exec_persists_kernel_and_redacts_transport_secrets(setup):
    account, sdk, call = setup
    runtime_id = allocate(call)
    sdk.outputs = [{"output_type": "stream", "name": "stdout",
                    "text": "result private-proxy-token private-access-token private-refresh-token"}]
    result = call("remote_exec", runtime_id=runtime_id, code="print('status')", timeout_seconds=30)
    assert result["result"]["stdout"] == "result [redacted] [redacted] [redacted]"
    assert sdk.calls[-2:] == [("exec", "print('status')", {"allow_stdin": False, "timeout": 30}),
                              ("close", {"shutdown_kernel": False})]
    saved = bridge.private_json(account / "runtimes" / (runtime_id + ".json"))
    assert (saved["kernel_id"], saved["session_id"]) == ("kernel-1", "session-1")


def test_remote_traceback_is_never_returned(setup):
    _, sdk, call = setup
    runtime_id = allocate(call)
    sdk.outputs = [{"output_type": "error", "traceback": ["PRIVATE FILE OR TOKEN"]}]
    result = call("remote_exec", runtime_id=runtime_id, code="raise Exception()")
    assert result["error"]["code"] == "remote_execution_failed"
    assert "PRIVATE" not in json.dumps(result)


def test_download_is_bounded_atomic_and_hashed(setup, tmp_path):
    _, sdk, call = setup
    runtime_id = allocate(call)
    target = tmp_path / "result.mp4"
    target.write_bytes(b"old")
    content = b"synthetic fixture"
    sdk.response = Response({"type": "file", "format": "base64", "content": base64.b64encode(content).decode()})
    result = call("download", runtime_id=runtime_id, local_path=str(target), remote_path="/content/job/out/result.mp4", max_bytes=32)
    assert result["result"] == {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    assert target.read_bytes() == content
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    sdk.response = Response({"type": "file", "format": "base64", "content": base64.b64encode(b"too large").decode()})
    bad = call("download", runtime_id=runtime_id, local_path=str(target), remote_path="/content/job/out/result.mp4", max_bytes=2)
    assert bad["error"]["code"] == "download_too_large"
    assert target.read_bytes() == content
    assert sdk.response.closed


def test_upload_passes_bytes_through_contents_protocol(setup, tmp_path):
    _, sdk, call = setup
    runtime_id = allocate(call)
    source = tmp_path / "input.png"
    source.write_bytes(b"fixture")
    result = call("upload", runtime_id=runtime_id, local_path=str(source), remote_path="/content/job/input.png", max_bytes=100)
    assert result["result"]["sha256"] == hashlib.sha256(b"fixture").hexdigest()
    method = [c for c in sdk.calls if c[0] == "http"][-1]
    assert method[1] == "PUT"
    assert base64.b64decode(method[3]["json"]["content"]) == b"fixture"


def test_upload_creates_only_contained_ancestors_in_order_and_can_repeat(setup, tmp_path):
    _, sdk, call = setup
    runtime_id = allocate(call)
    source = tmp_path / "runner.py"
    source.write_bytes(b"fixture")
    for _ in range(2):
        result = call("upload", runtime_id=runtime_id, local_path=str(source),
                      remote_path="/content/web-comfyui/job/runner/worker.py")
        assert result["ok"]
    http = [item for item in sdk.calls if item[0] == "http"]
    paths = [item[2].split("/api/contents/")[1] for item in http]
    assert paths == ["content/web-comfyui", "content/web-comfyui/job",
                     "content/web-comfyui/job/runner", "content/web-comfyui/job/runner/worker.py"] * 2
    assert all(item[1] == "PUT" for item in http)


def test_directory_conflict_stops_upload_before_media_is_sent(setup, tmp_path):
    _, sdk, call = setup
    runtime_id = allocate(call)
    source = tmp_path / "input.png"
    source.write_bytes(b"fixture")
    calls = []

    def conflict(method, url, **kwargs):
        calls.append(kwargs)
        return Response({"type": "file"})

    sdk.request = conflict
    result = call("upload", runtime_id=runtime_id, local_path=str(source),
                  remote_path="/content/web-comfyui/job/input.png")
    assert result["error"]["code"] == "invalid_remote_directory"
    assert len(calls) == 1 and calls[0]["json"] == {"type": "directory"}


@pytest.mark.parametrize("remote", ["/etc/passwd", "/content/../etc/passwd", "/content/a/./b", "/content//x", "/content/a\\b"])
def test_remote_paths_are_contained(setup, tmp_path, remote):
    _, sdk, call = setup
    source = tmp_path / "input.png"
    source.write_bytes(b"x")
    result = call("upload", runtime_id=str(uuid.uuid4()), local_path=str(source), remote_path=remote)
    assert result["error"]["code"] == "invalid_request"
    assert not sdk.calls


def test_credentials_and_symlinks_cannot_be_uploaded(setup, tmp_path):
    account, sdk, call = setup
    runtime_id = str(uuid.uuid4())
    result = call("upload", runtime_id=runtime_id, local_path=str(account / "token.json"), remote_path="/content/token.json")
    assert result["error"]["code"] == "private_path"
    link = tmp_path / "input.png"
    link.symlink_to(account / "token.json")
    assert call("upload", runtime_id=runtime_id, local_path=str(link), remote_path="/content/input.png")["error"]["code"] == "invalid_request"
    assert not sdk.calls


def test_account_directory_symlink_is_rejected_without_sdk(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(private, target_is_directory=True)
    result = bridge.handle({"op": "account_status", "account_dir": str(alias)},
                           sdk_factory=lambda: pytest.fail("must not initialize SDK"))
    assert result["error"]["code"] == "invalid_request"


def test_http_refresh_uses_explicit_bounded_transport_and_rejects_redirects():
    sessions = []

    class Session:
        def __init__(self, credentials=None, **kwargs):
            self.credentials, self.options = credentials, kwargs
            self.trust_env, self.closed = True, False
            self.calls = []
            self.response = Response()
            sessions.append(self)

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.response

        def close(self):
            self.closed = True

    sdk = bridge.GoogleSDK.__new__(bridge.GoogleSDK)
    sdk.requests = SimpleNamespace(Session=Session)
    sdk.AuthorizedSession = Session
    sdk.Request = lambda **kwargs: SimpleNamespace(**kwargs)
    session = sdk.http(Credentials())
    refresh = session.options["auth_request"].session
    assert refresh is sessions[0] and refresh is not session
    assert not session.trust_env and not refresh.trust_env
    refresh.request("POST", "https://oauth2.googleapis.com/token", data={"refresh_token": "secret"})
    session.request("POST", "https://colab.research.google.com/tun/m/assign")
    for transport in (refresh, session):
        options = transport.calls[-1][2]
        assert options["timeout"] == (10, 60) and options["allow_redirects"] is False
    assert session.calls[-1][2]["json"] == {}
    session.response.status_code = 302
    with pytest.raises(bridge.BridgeError, match="unexpected redirect"):
        session.request("GET", "https://colab.research.google.com")
    assert session.response.closed
    session.close()
    assert session.closed and refresh.closed


def test_jupyter_adapter_uses_explicit_transport_and_restores_sdk(monkeypatch):
    package = ModuleType("jupyter_kernel_client")
    manager = ModuleType("jupyter_kernel_client.manager")
    previous = lambda *args, **kwargs: pytest.fail("ambient fetch must not run")
    manager.fetch = previous
    package.manager = manager
    calls = []

    class WebSocketApp:
        def run_forever(self, **kwargs):
            calls.append(kwargs)

    websocket = ModuleType("websocket")
    websocket.WebSocketApp = WebSocketApp
    websocket.timeout = None
    websocket.getdefaulttimeout = lambda: websocket.timeout
    websocket.setdefaulttimeout = lambda value: setattr(websocket, "timeout", value)
    monkeypatch.setitem(sys.modules, "jupyter_kernel_client", package)
    monkeypatch.setitem(sys.modules, "jupyter_kernel_client.manager", manager)
    monkeypatch.setitem(sys.modules, "websocket", websocket)
    sdk = bridge.GoogleSDK.__new__(bridge.GoogleSDK)
    transport = FakeSDK()
    sdk.http = lambda: nullcontext(transport)
    with sdk.execution_transport():
        manager.fetch("https://runtime.colab.googleusercontent.com/api/kernels", token="explicit", method="POST", json={})
        WebSocketApp().run_forever(ping_interval=60)
        assert websocket.timeout == 10
        assert calls[-1]["http_no_proxy"] == ["*"]
        assert calls[-1]["reconnect"] == 0
    assert manager.fetch is previous and websocket.timeout is None
    request = transport.calls[-1]
    assert request[1] == "POST" and request[3]["headers"]["Authorization"] == "Bearer explicit"


def test_entrypoint_accepts_only_stdin_and_has_no_import_side_effects(tmp_path):
    result = subprocess.run([sys.executable, str(SOURCE), "private-auth-code"], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["error"]["code"] == "invalid_request"
    assert "private-auth-code" not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())
