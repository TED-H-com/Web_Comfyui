import base64
import hashlib
import json
import io
from pathlib import Path
import urllib.error
import urllib.request
import uuid

import pytest

from webui.colab_service import BridgeError, ColabError, ColabService


class FakeBridge:
    installed = True

    def __init__(self):
        self.calls = []
        self.balance = 100
        self.allocate_error = False
        self.stop_error = False
        self.usage_error = False
        self.found = True
        self.bound = True
        from PIL import Image
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "gray").save(image, "PNG")
        self.data = image.getvalue()

    def __call__(self, op, directory, **values):
        self.calls.append((op, values))
        if op == "account_identity":
            return {"bound": self.bound, "subject_key": "a" * 64 if self.bound else None}
        if op == "account_status":
            if self.usage_error:
                raise BridgeError("timeout")
            return {"authenticated": True, "balance_compute_units": self.balance, "consumption_rate_per_hour": 4, "active_assignments": 0}
        if op == "runtime_allocate" and self.allocate_error:
            raise BridgeError("timeout", True)
        if op in {"runtime_lookup", "runtime_allocate"}:
            assert values["gpu"] == "L4"
            return {"found": self.found, "runtime_id": values["allocation_id"]}
        if op == "runtime_stop":
            if self.stop_error:
                raise BridgeError("timeout", True)
            return {"stopped": True}
        if op == "download":
            Path(values["local_path"]).write_bytes(self.data)
            return {"bytes": len(self.data)}
        return {}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    bridge = FakeBridge()
    service = ColabService(tmp_path / "colab", bridge, clock=lambda: 1000)
    monkeypatch.setattr(service, "start", lambda: None)
    account = service.create_account("alice", "My Pro")
    body = {"account_id": account["id"], "request_id": str(uuid.uuid4()), "job": {"type": "video", "mode": "text", "prompt": "A blue square", "seconds": 5}, "gpu": "L4", "max_units": 20, "max_minutes": 60}
    return service, bridge, body


def test_admission_owner_budget_and_idempotence(lane):
    service, bridge, body = lane
    with pytest.raises(ColabError):
        service.submit("bob", body)
    bridge.balance = 10
    with pytest.raises(ColabError):
        service.submit("alice", body)
    bridge.balance = 100
    first = service.submit("alice", body)
    assert service.submit("alice", body)["id"] == first["id"]
    assert not [c for c in bridge.calls if c[0] == "runtime_allocate"]
    with pytest.raises(ColabError):
        service.submit("alice", {**body, "max_minutes": 30})
    with pytest.raises(ColabError):
        service.submit("alice", {**body, "request_id": str(uuid.uuid4())})
    assert service.snapshot("bob")["jobs"] == []
    with pytest.raises(ColabError):
        service.job("bob", first["id"])
    with pytest.raises(ColabError):
        service.cancel("bob", first["id"])


def test_ambiguous_allocate_survives_restart_without_second_allocation(lane):
    service, bridge, body = lane
    job = service.submit("alice", body)
    bridge.allocate_error = True
    service.tick(service.job("alice", job["id"]))
    assert service.job("alice", job["id"])["phase"] == "allocation_uncertain"
    recovered = ColabService(service.root, bridge, clock=lambda: 1005)
    recovered.tick(recovered.job("alice", job["id"]))
    state = recovered.job("alice", job["id"])
    assert state["phase"] == "preparing"
    assert state["runtime_id"] == state["allocation_id"]
    assert len([c for c in bridge.calls if c[0] == "runtime_allocate"]) == 1


def test_unbound_legacy_reconnect_blocked_until_release(lane):
    service, bridge, body = lane
    job = service.submit("alice", body)
    bridge.bound = False
    for action in ("login", "finish"):
        with pytest.raises(ColabError):
            service.account_action("alice", body["account_id"], action, {})
    assert not [c for c in bridge.calls if c[0].startswith("oauth")]


def test_bound_account_can_reauthenticate_during_cleanup(lane):
    service, bridge, body = lane
    job = service.submit('alice', body)
    service.update(job['id'], phase='cleanup_pending', outcome='failed')
    service.account_action('alice', body['account_id'], 'login', {})
    service.account_action('alice', body['account_id'], 'finish', {'flow_id': 'synthetic', 'code': 'synthetic'})
    assert [op for op, _ in bridge.calls if op.startswith('oauth')] == ['oauth_begin', 'oauth_finish']


def test_duplicate_profiles_cannot_bypass_google_account_lease(lane):
    service, bridge, body = lane
    service.submit('alice', body)
    other = service.create_account('bob', 'Another label, same Google subject')
    with pytest.raises(ColabError, match='已有'):
        service.submit('bob', {**body, 'request_id': str(uuid.uuid4()), 'account_id': other['id']})
    assert service.snapshot('bob')['jobs'] == []


def test_corrupt_input_and_missing_probe_fail_before_paid_admission(lane, monkeypatch):
    service, bridge, body = lane
    with pytest.raises(ColabError, match='圖片'):
        service.submit('alice', {**body, 'job': {'type': 'image', 'prompt': 'square'}, 'files': [{'name': 'bad.png', 'data': base64.b64encode(b'not an image').decode()}]})
    from webui import colab_service
    monkeypatch.setattr(colab_service.shutil, 'which', lambda name: None)
    with pytest.raises(ColabError, match='ffprobe'):
        service.submit('alice', body)
    assert service.snapshot('alice')['jobs'] == []
    assert not [op for op, _ in bridge.calls if op == 'runtime_allocate']


def test_empty_lookup_during_cancel_does_not_drop_ambiguous_allocation(lane):
    service, bridge, body = lane
    job = service.submit('alice', body)
    bridge.allocate_error = True
    service.tick(service.job('alice', job['id']))
    bridge.found = False
    service.cancel('alice', job['id'])
    service.tick(service.job('alice', job['id']))
    assert service.job('alice', job['id'])['phase'] == 'cleanup_pending'
    # The original provider request becomes visible later; release that same ID.
    bridge.found = True
    service.tick(service.job('alice', job['id']))
    assert service.job('alice', job['id'])['phase'] == 'cancelled'


def test_delayed_worker_snapshot_observes_cancel_before_allocating(lane):
    service, bridge, body = lane
    job = service.submit('alice', body)
    stale = service.job('alice', job['id'])
    service.cancel('alice', job['id'])
    service.tick(stale)
    assert service.job('alice', job['id'])['phase'] == 'cancelled'
    assert not [op for op, _ in bridge.calls if op == 'runtime_allocate']


def test_stop_failure_never_claims_completion_and_is_recovered(lane):
    service, bridge, body = lane
    job = service.submit("alice", body)
    service.tick(service.job("alice", job["id"]))
    service.cancel("alice", job["id"])
    bridge.stop_error = True
    with pytest.raises(BridgeError):
        service.tick(service.job("alice", job["id"]))
    state = service.job("alice", job["id"])
    assert state["phase"] == "cleanup_pending" and not state["released"]
    bridge.stop_error = False
    service.tick(state)
    state = service.job("alice", job["id"])
    assert state["phase"] == "cancelled" and state["released"]


@pytest.mark.parametrize("condition", ["missing_balance", "budget", "deadline"])
def test_limits_release_owned_runtime(lane, condition):
    service, bridge, body = lane
    job = service.submit("alice", body)
    service.tick(service.job("alice", job["id"]))
    if condition == "missing_balance": bridge.usage_error = True
    if condition == "budget": bridge.balance = 75
    if condition == "deadline": service.clock = lambda: 5000
    service.tick(service.job("alice", job["id"]))
    state = service.job("alice", job["id"])
    assert state["phase"] == "failed" and state["released"]


def test_download_identity_hash_and_traversal(lane):
    service, bridge, body = lane
    body["job"] = {"type": "image", "prompt": "A blue square"}
    job = service.submit("alice", body)
    state = service.update(job["id"], runtime_id=str(uuid.uuid4()))
    artifact = {"path": "out/output.png", "size": len(bridge.data), "sha256": hashlib.sha256(bridge.data).hexdigest()}
    report = {"job_id": state["id"], "manifest_sha256": state["manifest_sha256"], "artifacts": [artifact]}
    outputs = service._download(state, report, "/content/web-comfyui/" + state["id"])
    assert outputs[0]["sha256"] == artifact["sha256"]
    service.update(state["id"], artifacts=outputs)
    with pytest.raises(ColabError):
        service.artifact("bob", state["id"], "1.png")
    assert service.artifact("alice", state["id"], "1.png").read_bytes() == bridge.data
    for changes in ({"path": "../token.json"}, {"path": "/content/out/a.png"}, {"sha256": "0" * 64}):
        with pytest.raises(ColabError):
            service._download(state, {**report, "artifacts": [{**artifact, **changes}]}, "/content/web-comfyui/" + state["id"])
    with pytest.raises(ColabError):
        service._download(state, {**report, "manifest_sha256": "0" * 64}, "/content/web-comfyui/" + state["id"])


def test_invalid_mode_and_external_paths_never_allocate(lane):
    service, bridge, body = lane
    for job in ({"type": "image", "prompt": "square", "inputs": ["/private/file.png"]}, {"type": "video", "mode": "text", "prompt": "square", "seconds": 99}):
        with pytest.raises(ColabError):
            service.submit("alice", {**body, "job": job})
    assert service.snapshot("alice")["jobs"] == []
    assert not [c for c in bridge.calls if c[0] == "runtime_allocate"]


def test_permanently_invalid_output_releases_without_repeated_download(lane, monkeypatch):
    service, bridge, body = lane
    job = service.submit('alice', body)
    state = service.update(job['id'], phase='running', runtime_id=str(uuid.uuid4()))
    identity = {'job_id': job['id'], 'manifest_sha256': state['manifest_sha256']}
    monkeypatch.setattr(service, '_exec', lambda *args: {'status': {**identity, 'phase': 'completed'}, 'artifacts': {**identity, 'artifacts': [{'path': 'out/video.mp4', 'size': 70*1024*1024, 'sha256': 'a'*64}]}})
    service.tick(state)
    state = service.job('alice', job['id'])
    assert state['phase'] == 'failed' and state['released']
    assert not [op for op, _ in bridge.calls if op == 'download']


def test_actual_detached_worker_crash_is_observed_by_controller(lane, monkeypatch, tmp_path):
    import contextlib
    import time
    service, bridge, body = lane
    job = service.submit('alice', body)
    state = service.update(job['id'], phase='preparing', runtime_id=str(uuid.uuid4()), watchdog_armed=True,
                           upload_index=len(service.job('alice', job['id'])['uploads']))
    remote = tmp_path / 'synthetic-remote'
    (remote / 'runner').mkdir(parents=True)
    # A syntax error occurs before the remote worker can publish any status.
    (remote / 'runner/remote_job.py').write_text('invalid python !')
    namespace = {}
    def execute(_state, code):
        stdout = io.StringIO()
        code = code.replace('/content/web-comfyui/' + job['id'], str(remote))
        with contextlib.redirect_stdout(stdout):
            exec(compile(code, '<reviewed-controller-code>', 'exec'), namespace)
        return json.loads(stdout.getvalue().split('WEB_COMFYUI_JSON=')[-1])
    monkeypatch.setattr(service, '_exec', execute)
    service.tick(state)
    namespace['p'].wait(timeout=5)
    service.tick(service.job('alice', job['id']))
    state = service.job('alice', job['id'])
    assert state['phase'] == 'failed' and state['released']
    assert '啟動' in state['message']
    assert state['remote_diagnostics'] == {'stage': 'worker_status', 'error_type': 'WorkerExited'}


def test_remote_failure_diagnostics_are_sanitized_durable_before_cleanup(lane, monkeypatch):
    service, bridge, body = lane
    job = service.submit('alice', body)
    state = service.update(job['id'], phase='running', runtime_id=str(uuid.uuid4()))
    expected = {'stage': 'venv', 'error_type': 'CalledProcessError', 'exit_code': 1}
    report = {'status': {'job_id': job['id'], 'manifest_sha256': state['manifest_sha256'],
                        'phase': 'failed', 'error': 'private provider text',
                        'diagnostics': dict(expected, stderr='private log', command='private command',
                                            http_status=True, cause_type='private prompt')}}
    monkeypatch.setattr(service, '_exec', lambda *a: report)
    bridge.stop_error = True
    with pytest.raises(BridgeError):
        service.tick(state)
    saved = service.job('alice', job['id'])
    assert saved['phase'] == 'cleanup_pending'
    assert saved['remote_diagnostics'] == expected
    assert 'private ' not in json.dumps(saved)
    restarted = ColabService(service.root, bridge, clock=lambda: 1000)
    bridge.stop_error = False
    restarted.tick(restarted.job('alice', job['id']))
    saved = restarted.job('alice', job['id'])
    assert saved['phase'] == 'failed' and saved['released']
    assert restarted.public_job(saved)['diagnostics'] == expected
    assert not any(op == 'runtime_allocate' for op, _ in bridge.calls)


def test_foreign_remote_status_cannot_inject_failure_diagnostics(lane, monkeypatch):
    service, _, body = lane
    job = service.submit('alice', body)
    state = service.update(job['id'], phase='running', runtime_id=str(uuid.uuid4()), launch_requested_at=1000)
    monkeypatch.setattr(service, '_exec', lambda *a: {'status': {
        'job_id': str(uuid.uuid4()), 'manifest_sha256': state['manifest_sha256'], 'phase': 'failed',
        'diagnostics': {'stage': 'venv', 'error_type': 'CalledProcessError', 'exit_code': 1}}})
    service.tick(state)
    saved = service.job('alice', job['id'])
    assert saved['phase'] == 'running' and 'remote_diagnostics' not in saved


@pytest.mark.parametrize("cancel_during_download", [False, True])
def test_complete_lifecycle_uses_frozen_bundle_and_respects_late_cancel(lane, monkeypatch, tmp_path, cancel_during_download):
    service, bridge, body = lane
    body["job"] = {"type": "image", "prompt": "A blue square"}
    job = service.submit("alice", body)
    real_call = bridge.__class__.__call__
    def transport(op, directory, **values):
        state = service.job("alice", job["id"])
        if op == "remote_exec":
            code = values["code"]
            if "read('status.json')" in code:
                entry = {"path": "out/output.png", "size": len(bridge.data), "sha256": hashlib.sha256(bridge.data).hexdigest()}
                identity = {"job_id": job["id"], "manifest_sha256": state["manifest_sha256"]}
                result = {"status": {**identity, "phase": "completed"}, "artifacts": {**identity, "artifacts": [entry]}}
            elif "armed" in code:
                result = {"armed": True, "job_id": job["id"], "manifest_sha256": state["manifest_sha256"], "deadline_epoch": state["deadline_epoch"]}
            else:
                result = {"launched": True}
            return {"stdout": "WEB_COMFYUI_JSON=" + json.dumps(result)}
        if op == "upload":
            assert Path(values["local_path"]).is_relative_to(service.root / "jobs" / job["id"])
        result = real_call(bridge, op, directory, **values)
        if op == "download" and cancel_during_download:
            service.cancel("alice", job["id"])
        return result
    class LifecycleBridge:
        installed = True
        __call__ = staticmethod(transport)
    service.bridge = LifecycleBridge()
    # Simulate a deployment replacing source files after admission.
    from webui import colab_service
    monkeypatch.setattr(colab_service, "ROOT", tmp_path / "new-unrelated-release")
    for _ in range(50):
        state = service.job("alice", job["id"])
        if state["phase"] in {"completed", "failed", "cancelled"}:
            break
        service.tick(state)
    state = service.job("alice", job["id"])
    assert state["phase"] == ("cancelled" if cancel_during_download else "completed")
    assert state["released"]
    assert len([call for call in bridge.calls if call[0] == "runtime_allocate"]) == 1
    if not cancel_during_download:
        assert state["artifacts"][0]["width"] == 8


def test_colab_http_auth_and_disabled_submission(test_server):
    base, opener, cookie = test_server
    for path in ("/api/colab", "/api/colab/jobs/" + str(uuid.uuid4()) + "/files/1.png"):
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(base + path)
        assert error.value.code == 401
    request = urllib.request.Request(base + "/api/colab", headers={"Cookie": cookie})
    state = json.load(opener.open(request))
    assert not state["installed"] and state["accounts"] == []
    request = urllib.request.Request(base + "/api/colab/accounts", data=json.dumps({"label": "Synthetic Pro"}).encode(), headers={"Cookie": cookie, "Origin": base, "Content-Type": "application/json"})
    account = json.load(opener.open(request))
    request = urllib.request.Request(base + "/api/colab/accounts/" + account["id"] + "/login", data=b"{}", headers={"Cookie": cookie, "Origin": base, "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request)
    assert error.value.code == 400
    assert b"WEB_COMFYUI_COLAB_PYTHON" in error.value.read()


def test_watchdog_reconciles_cold_kernel_without_reallocation_or_upload(lane, monkeypatch):
    service, bridge, body = lane
    job = service.submit('alice', body)
    service.tick(service.job('alice', job['id']))
    original_deadline = service.job('alice', job['id'])['deadline_epoch']
    outcomes = [BridgeError('execution_uncertain', True), None]
    def execute(state, code):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return {'armed': True, 'job_id': state['id'], 'manifest_sha256': state['manifest_sha256'],
                'deadline_epoch': state['deadline_epoch']}
    monkeypatch.setattr(service, '_exec', execute)
    service.tick(service.job('alice', job['id']))
    waiting = service.job('alice', job['id'])
    assert waiting['phase'] == 'preparing' and waiting['watchdog_attempts'] == 1
    assert not waiting.get('watchdog_armed')
    assert not [op for op, _ in bridge.calls if op in {'upload', 'runtime_stop'}]
    service.tick(waiting)
    ready = service.job('alice', job['id'])
    assert ready['watchdog_armed'] and ready['deadline_epoch'] == original_deadline
    assert len([op for op, _ in bridge.calls if op == 'runtime_allocate']) == 1


def test_watchdog_uncertainty_is_bounded_and_releases_same_runtime(lane, monkeypatch):
    service, bridge, body = lane
    job = service.submit('alice', body)
    service.tick(service.job('alice', job['id']))
    def execute(*args):
        raise BridgeError('execution_uncertain', True)
    monkeypatch.setattr(service, '_exec', execute)
    for _ in range(3):
        service.tick(service.job('alice', job['id']))
    failed = service.job('alice', job['id'])
    assert failed['phase'] == 'failed' and failed['released']
    assert failed['watchdog_attempts'] == 3
    assert failed['preparation_error_code'] == 'execution_uncertain'
    assert len([op for op, _ in bridge.calls if op == 'runtime_allocate']) == 1
    assert not [op for op, _ in bridge.calls if op == 'upload']


def test_watchdog_bad_ack_fails_immediately_without_leaking_error(lane, monkeypatch):
    service, bridge, body = lane
    job = service.submit('alice', body)
    service.tick(service.job('alice', job['id']))
    def execute(*args):
        raise RuntimeError('PRIVATE_CREDENTIAL_AND_PROMPT')
    monkeypatch.setattr(service, '_exec', execute)
    service.tick(service.job('alice', job['id']))
    failed = service.job('alice', job['id'])
    assert failed['phase'] == 'failed' and failed['released']
    assert failed['watchdog_attempts'] == 1
    assert failed['preparation_error_code'] == 'watchdog_ack_invalid'
    assert 'PRIVATE_CREDENTIAL_AND_PROMPT' not in json.dumps(failed)
