"""Offline watchdog bootstraps with synthetic state; no Google processes run."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "colab_watchdog.py"
SPEC = importlib.util.spec_from_file_location("colab_watchdog_tested", SOURCE)
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)
SHA = "a" * 64


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """Map only the simulated remote /content tree to disk-backed pytest temp."""
    job_id = str(uuid.uuid4())
    root = tmp_path / "content" / "web-comfyui" / job_id
    root.mkdir(parents=True)
    clock = SimpleNamespace(now=100.0)
    fake_time = ModuleType("time")
    fake_time.time = fake_time.monotonic = lambda: clock.now
    fake_time.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
    fake_pathlib = ModuleType("pathlib")

    def path(value):
        text = str(value)
        return tmp_path / text.lstrip("/") if text.startswith("/content/") else Path(value)

    fake_pathlib.Path = path
    fake_subprocess = ModuleType("subprocess")
    fake_subprocess.DEVNULL = subprocess.DEVNULL
    fake_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    fake_subprocess.Popen = lambda *a, **kw: pytest.fail("Unexpected detached process")
    fake_subprocess.run = lambda *a, **kw: pytest.fail("Unexpected Google command")
    monkeypatch.setitem(sys.modules, "time", fake_time)
    monkeypatch.setitem(sys.modules, "pathlib", fake_pathlib)
    monkeypatch.setitem(sys.modules, "subprocess", fake_subprocess)
    return root, job_id, clock, fake_subprocess


@pytest.mark.parametrize("changes", [
    {"job_id": "../escape"}, {"job_id": None}, {"manifest_sha256": "x" * 64},
    {"manifest_sha256": "'\nprint('unsafe')"}, {"deadline_epoch": True},
    {"deadline_epoch": float("nan")}, {"deadline_epoch": float("inf")}, {"deadline_epoch": -1},
])
def test_builder_rejects_untrusted_identity(changes):
    args = {"job_id": str(uuid.uuid4()), "deadline_epoch": 200, "manifest_sha256": SHA}
    with pytest.raises(ValueError):
        watchdog.build_watchdog_code(**dict(args, **changes))


def test_bootstrap_acknowledges_identity_and_reuses_live_watchdog(remote, capsys):
    root, job_id, _, process = remote
    launched, locks = [], []

    def spawn(argv, **kwargs):
        launched.append((argv, kwargs))
        compile(argv[2], "watchdog-child", "exec")
        fd = os.open(root / ".watchdog.lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(fd)
        (root / "watchdog.json").write_text(json.dumps({"schema_version": 1, "job_id": job_id,
            "manifest_sha256": SHA, "deadline_epoch": 200.0, "phase": "armed"}))
        return SimpleNamespace(pid=123)

    process.Popen = spawn
    try:
        code = watchdog.build_watchdog_code(job_id, 200, SHA)
        exec(code, {})
        exec(code, {})
        assert len(launched) == 1
        assert launched[0][1]["start_new_session"] is True
        assert launched[0][1]["stdin"] == subprocess.DEVNULL
        responses = [json.loads(line.split("=", 1)[1]) for line in capsys.readouterr().out.splitlines()]
        assert len(responses) == 2
        assert all(r["armed"] and r["job_id"] == job_id and r["manifest_sha256"] == SHA
                   and r["deadline_epoch"] == 200 for r in responses)
    finally:
        for fd in locks:
            os.close(fd)


def test_bootstrap_does_not_accept_stale_marker_as_live(remote):
    root, job_id, clock, process = remote
    (root / "watchdog.json").write_text(json.dumps({"schema_version": 1, "job_id": job_id,
        "manifest_sha256": SHA, "deadline_epoch": 200.0, "phase": "armed"}))
    process.Popen = lambda *a, **kw: SimpleNamespace(pid=123)
    with pytest.raises(RuntimeError, match="readiness"):
        exec(watchdog.build_watchdog_code(job_id, 200, SHA), {})
    assert 105 <= clock.now < 106


def test_bootstrap_rejects_changed_manifest_before_spawning(remote):
    root, job_id, _, _ = remote
    (root / "watchdog.json").write_text(json.dumps({"schema_version": 1, "job_id": job_id,
        "manifest_sha256": "b" * 64, "deadline_epoch": 200.0, "phase": "armed"}))
    with pytest.raises(RuntimeError, match="identity differs"):
        exec(watchdog.build_watchdog_code(job_id, 200, SHA), {})


def test_expired_bootstrap_does_not_start_process(remote):
    _, job_id, _, _ = remote
    with pytest.raises(RuntimeError, match="deadline already passed"):
        exec(watchdog.build_watchdog_code(job_id, 99, SHA), {})


@pytest.mark.parametrize("cancelled", [False, True])
def test_worker_attempts_bounded_shutdown_on_deadline_or_cancel(remote, monkeypatch, cancelled):
    root, job_id, clock, process = remote
    if cancelled:
        (root / "cancel.requested").touch()
    calls = []
    process.run = lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(returncode=0)
    monkeypatch.setattr(sys, "argv", ["watchdog", str(root), "110", SHA])
    exec(watchdog._WORKER, {})
    assert clock.now == (100 if cancelled else 110)
    assert len(calls) == 1 and calls[0][1]["timeout"] == 20
    assert calls[0][0][0][-1] == "from google.colab import runtime; runtime.unassign()"
    marker = json.loads((root / "watchdog.json").read_text())
    assert marker["phase"] == "shutdown_requested" and marker["job_id"] == job_id
    assert "released" not in marker
    assert (root / "cancel.requested").exists()
    assert (root / "watchdog.json").stat().st_mode & 0o777 == 0o600


def test_worker_bounds_unassign_timeout_retries(remote, monkeypatch):
    root, _, clock, process = remote
    (root / "cancel.requested").touch()
    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired("redacted", 20)

    process.run = timeout
    monkeypatch.setattr(sys, "argv", ["watchdog", str(root), "110", SHA])
    exec(watchdog._WORKER, {})
    assert len(calls) == 3 and clock.now == 104
    assert json.loads((root / "watchdog.json").read_text())["attempt"] == 3
