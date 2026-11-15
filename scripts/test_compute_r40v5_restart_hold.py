"""Deterministic regression coverage for the yielded worker/sweeper handoff."""
import fcntl
import json
import queue
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest
import colab_service as cs
from scripts import colab_lease_sweeper as sweeper


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def setup_service(tmp_path, monkeypatch):
    monkeypatch.setenv('CASTLE_COLAB_DB_NESTING', 'strict')
    clock = Clock()
    return cs.ColabService(tmp_path / 'colab', bridge=lambda *a, **k: {}, clock=clock), clock


def uid():
    return str(uuid.uuid4())


def lock_available(service):
    with (service.root / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(lock, fcntl.LOCK_UN)
        return True


def assigned_session(service):
    session = service.sessions.new_state('owner', uid(), 'a' * 64,
                                         {'idle_minutes': 20, 'hard_hours': 6, 'max_units': 120})
    session.update(phase='arming', runtime_id='fixture-runtime', watchdog_armed=False, deadline_epoch=22000)
    with service.db() as db:
        db.execute('INSERT INTO work_sessions VALUES (?,?,?)', (session['id'], 'owner', cs.encoded(session)))
    return session


class Driver:
    """Advance the real worker only at an explicitly permitted virtual sleep."""
    def __init__(self, monkeypatch):
        self.events = queue.Queue()
        self.permits = threading.Semaphore(0)
        self.stopping = False
        self.errors = []
        monkeypatch.setattr(cs.time, 'sleep', self.sleep)

    def sleep(self, seconds):
        self.events.put(('sleep', seconds))
        if not self.permits.acquire(timeout=5) or self.stopping:
            raise SystemExit

    def instrument(self, service, monkeypatch):
        loop = service._loop
        def run():
            try:
                loop()
            except SystemExit:
                pass
            except BaseException as error:
                self.errors.append(error)
            finally:
                self.events.put(('exit', None))
        monkeypatch.setattr(service, '_loop', run)

    def event(self, expected, value=None):
        event = self.events.get(timeout=5)
        assert event == (expected, value)

    def advance(self):
        self.permits.release()

    def finish(self, thread):
        thread.join(timeout=5)
        assert not thread.is_alive() and not self.errors

    def stop(self, thread):
        if thread and thread.is_alive():
            self.stopping = True
            self.advance()
            self.event('exit')
            self.finish(thread)
        assert not self.errors


def yield_worker(service, monkeypatch):
    def fail():
        raise sqlite3.OperationalError('fixture')
    monkeypatch.setattr(service.sessions, 'tick_all', fail)
    driver = Driver(monkeypatch)
    driver.instrument(service, monkeypatch)
    service.start()
    thread = service._thread
    driver.event('sleep', 10); driver.advance()
    driver.event('sleep', 10); driver.advance()
    driver.event('exit'); driver.finish(thread)
    return driver, thread


def test_pipeline_assigned_runtime_handoff_deterministic(setup_service, tmp_path, monkeypatch):
    """T-H1: real execution -> pipeline -> router -> web cancellation, 100:1 virtual time."""
    from compute_pipeline import ComputePipeline
    from compute_routing import ComputeRouter, ComputeAdapter
    from compute_execution import PipelineExecution
    from compute_web import ComputeWeb

    s, clock = setup_service
    owner = 'owner'; pipeline_id = uid(); account = uid()
    web = object.__new__(ComputeWeb); web.colab = s
    adapter = ComputeAdapter(lambda *a: None, lambda *a: None, web.status_colab, web.cancel_colab)
    router = ComputeRouter(tmp_path / 'router.sqlite3', {'colab': adapter}, clock=clock)
    pipe = ComputePipeline(tmp_path / 'pipeline.sqlite3', router_factory=lambda _: router,
                           artifact_resolver=lambda *a: None, clock=clock)
    state = pipe.create(owner, pipeline_id,
                        {'version': 1, 'steps': [{'id': 'one', 'request': {'featureId': 'fixture'}, 'placement': 'colab'}]})
    job_id = state['steps'][0]['request_id']
    session = assigned_session(s)
    with s.db() as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job_id, owner, account, 'fixture', cs.encoded(
            {'id': job_id, 'phase': 'queued', 'account_id': account, 'session_id': session['id'], 'session_job': True})))
    receipt = {'state': 'queued', 'downstream_id': job_id, 'cancel_supported': True}
    with router._db() as db:
        db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (owner, job_id, 'fixture', 'colab', 'colab', 'queued', '{}', '{}', json.dumps(receipt), 1, 1))
    with pipe._db() as db:
        db.execute('UPDATE compute_pipelines SET cancel_requested=1 WHERE owner=? AND pipeline_id=?', (owner, pipeline_id))
        db.execute("UPDATE compute_pipeline_attempts SET state='queued',receipt_json=? WHERE owner=? AND pipeline_id=?",
                   (json.dumps(receipt), owner, pipeline_id))

    attempts = []; operations = []; samples = []; sweep_results = []; trace = []
    def fail():
        attempts.append(clock.now)
        raise RuntimeError('fixture supervision failure')
    def bridge(op, _directory, **kwargs):
        operations.append(op)
        if op == 'runtime_stop': return {'stopped': True}
        if op == 'account_status': return {}  # Keep the cancelling pipeline active after release.
        raise AssertionError(op)
    monkeypatch.setattr(s.sessions, 'tick_all', fail)
    driver = Driver(monkeypatch); driver.instrument(s, monkeypatch)
    state_machine = {'due': None, 'pending_yield': False, 'premature': 0, 'starts': 0}
    original_start = s.start
    def capture_start():
        before = s._thread
        original_start()
        if s._thread is not before:
            if state_machine['pending_yield']:
                state_machine['premature'] += 1
            state_machine['starts'] += 1
            trace.append(('start', clock.now))
            driver.event('sleep', 10)
            state_machine['due'] = clock.now + 10
    monkeypatch.setattr(s, 'start', capture_start)

    def advance_worker():
        if state_machine['due'] is not None and clock.now >= state_machine['due']:
            driver.advance()
            kind, value = driver.events.get(timeout=5)
            if kind == 'sleep':
                assert value == 10
                state_machine['due'] = clock.now + 10
            else:
                assert (kind, value) == ('exit', None)
                driver.finish(s._thread)
                trace.append(('yield', clock.now))
                state_machine['pending_yield'] = True
                state_machine['due'] = None

    def attempt_sweep():
        nonlocal session
        if s.sessions.get(session['id'])['released']:
            session = assigned_session(s)
        before_stops = operations.count('runtime_stop')
        assert not s.sessions.get(session['id'])['watchdog_armed']
        acquired = sweeper.sweep(s.root, bridge=bridge, clock=clock)
        released = s.sessions.get(session['id'])['released']
        stopped = operations.count('runtime_stop') == before_stops + 1
        sweep_results.append((acquired, released, stopped))
        trace.append(('sweep', clock.now))
        if acquired:
            state_machine['pending_yield'] = False

    class StopProbe(BaseException):
        pass
    class Wake:
        count = 0
        def wait(self, seconds):
            assert seconds == 5
            self.count += 1
            for index in range(10):
                clock.now += 0.5  # 10 samples per five simulated seconds, as in the review.
                if self.count in (60, 120) and index == 9:
                    attempt_sweep()  # Attempt before an iteration due at this exact instant.
                advance_worker()
                samples.append(not lock_available(s))
            if self.count == 125:
                raise StopProbe
        def clear(self):
            pass

    execution = object.__new__(PipelineExecution)
    execution.engine = pipe; execution._wake = Wake()
    try:
        with pytest.raises(StopProbe):
            execution._loop()
    finally:
        driver.stop(s._thread)
    held = sum(samples)
    print('H1 held=%d/%d reviewer=1008/1250 starts=%d premature=%d sweeps=%s' %
          (held, len(samples), state_machine['starts'], state_machine['premature'], sweep_results))
    assert s.job(owner, job_id)['cancel_requested'] is True
    assert len(samples) == 1250 and len(attempts) >= 3
    assert state_machine['premature'] == 0, 'worker restarted before a sweeper finished after yield'
    assert len(sweep_results) == 2 and all(all(result) for result in sweep_results)
    assert any(kind == 'yield' for kind, _ in trace)


def test_fifty_starts_wait_for_real_sweeper(setup_service, monkeypatch, caplog):
    """T-H2: the actual sweeper releases an assigned unarmed runtime before restart."""
    s, clock = setup_service
    session = assigned_session(s)
    driver, first = yield_worker(s, monkeypatch)
    marker = s.root / 'worker-yield.json'
    assert json.loads(marker.read_text()) == {'yielded_at': clock.now, 'sweep_run_id': None}
    assert marker.stat().st_mode & 0o777 == 0o600
    assert not list(s.root.glob('worker-yield.json.*.tmp'))
    for _ in range(50): s.start()
    assert s._thread is first and not first.is_alive() and lock_available(s)
    assert sum('colab_worker_start_held' in r.message for r in caplog.records) == 1
    operations = []
    def bridge(op, _directory, **kwargs):
        operations.append(op)
        return {'stopped': True} if op == 'runtime_stop' else {}
    clock.now += 300
    assert sweeper.sweep(s.root, bridge=bridge, clock=clock)
    assert 'runtime_stop' in operations and s.sessions.get(session['id'])['released']
    finished = s.root / 'sweeper-last-run.json'
    finished_data = json.loads(finished.read_text())
    assert set(finished_data) == {'finished_at', 'run_id'} and finished_data['finished_at'] == clock.now
    assert s._valid_sweeper_run_id(finished_data['run_id'])
    assert finished.stat().st_mode & 0o777 == 0o600
    assert not list(s.root.glob('sweeper-last-run.json.*.tmp'))
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: None)
    try:
        s.start(); second = s._thread
        driver.event('sleep', 10)
        assert second is not first and second.is_alive() and not lock_available(s) and not marker.exists()
    finally:
        driver.stop(s._thread)


def test_restart_hold_expires_at_900_seconds(setup_service, monkeypatch):
    """T-H3: 899 seconds remains held, 900 seconds permits a new worker."""
    s, clock = setup_service
    driver, first = yield_worker(s, monkeypatch)
    yielded = clock.now
    clock.now = yielded + 899
    s.start(); assert s._thread is first and not first.is_alive()
    clock.now = yielded + 900
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: None)
    try:
        s.start(); driver.event('sleep', 10)
        assert s._thread is not first and s._thread.is_alive() and not lock_available(s)
        assert not (s.root / 'worker-yield.json').exists()
    finally:
        driver.stop(s._thread)


def test_clock_reversal_and_malformed_yield_markers(setup_service, caplog):
    """T-H4: reject malformed data and log unreadable only once per instance."""
    s, clock = setup_service
    marker = s.root / 'worker-yield.json'
    marker.write_text(json.dumps({'yielded_at': clock.now + 1, 'sweep_run_id': None}))
    assert not s._restart_held() and not marker.exists()
    for data in ('{', '[]', '{"yielded_at":"1000"}', '{"yielded_at":NaN}', '{"yielded_at":true}'):
        marker.write_text(data)
        assert not s._restart_held() and not marker.exists()
    assert sum('colab_worker_yield_marker_unreadable' in r.message for r in caplog.records) == 1


def test_marker_oserrors_are_not_held_and_log_once(setup_service, monkeypatch, caplog):
    s, clock = setup_service
    marker = s.root / 'worker-yield.json'
    marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': None}))
    read = Path.read_text
    def fail_read(path, *args, **kwargs):
        if path == marker: raise PermissionError('fixture')
        return read(path, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_text', fail_read)
        assert not s._restart_held()
        assert not marker.exists()
    marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': None}))
    sweeper_marker = s.root / 'sweeper-last-run.json'
    def fail_sweeper_read(path, *args, **kwargs):
        if path == sweeper_marker: raise PermissionError('fixture')
        return read(path, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_text', fail_sweeper_read)
        assert s._restart_held() and marker.exists()
    clock.now += 900
    unlink = Path.unlink
    def fail_unlink(path, *args, **kwargs):
        if path == marker: raise PermissionError('fixture')
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path, 'unlink', fail_unlink)
        assert not s._restart_held()
    assert sum('colab_worker_yield_marker_unreadable' in r.message for r in caplog.records) == 1


def test_sweeper_marker_validation_and_hold_logging(setup_service, caplog):
    s, clock = setup_service
    marker = s.root / 'worker-yield.json'
    finished = s.root / 'sweeper-last-run.json'
    marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': None}))
    for data in ('{', '[]', '{"run_id":"short"}', '{"run_id":true}',
                 '{"run_id":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}', '{"finished_at":999}'):
        finished.write_text(data)
        assert s._restart_held() and marker.exists()
    assert sum('colab_worker_start_held' in r.message for r in caplog.records) == 1
    assert not any('colab_worker_yield_marker_unreadable' in r.message for r in caplog.records)
    clock.now += 1
    marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': None}))
    assert s._restart_held()
    assert sum('colab_worker_start_held' in r.message for r in caplog.records) == 2
    finished.write_text(json.dumps({'finished_at': clock.now, 'run_id': 'a' * 32}))
    assert not s._restart_held() and not marker.exists()


def test_sweeper_records_exception_but_not_busy_or_absent(setup_service, tmp_path, monkeypatch):
    """T-H5: the marker is written in finally, only after the real lock is acquired."""
    s, clock = setup_service
    marker = s.root / 'sweeper-last-run.json'
    with (s.root / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not sweeper.sweep(s.root, bridge=object(), clock=clock)
        assert not marker.exists()
    assert not sweeper.sweep(tmp_path / 'missing', bridge=object(), clock=clock)
    assert not (tmp_path / 'missing' / 'sweeper-last-run.json').exists()
    def fail(*args, **kwargs): raise RuntimeError('fixture')
    monkeypatch.setattr(sweeper, 'ColabService', fail)
    with pytest.raises(RuntimeError, match='fixture'):
        sweeper.sweep(s.root, bridge=object(), clock=clock)
    finished_data = json.loads(marker.read_text())
    assert set(finished_data) == {'finished_at', 'run_id'} and finished_data['finished_at'] == clock.now
    assert s._valid_sweeper_run_id(finished_data['run_id'])
    assert marker.stat().st_mode & 0o777 == 0o600 and lock_available(s)


def test_sweeper_swallows_only_marker_oserrors(setup_service, monkeypatch):
    s, clock = setup_service
    def fail_os(*args, **kwargs):
        assert not lock_available(s)
        raise OSError('fixture')
    monkeypatch.setattr(sweeper, 'private_write', fail_os)
    assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    assert lock_available(s) and not (s.root / 'sweeper-last-run.json').exists()
    def fail_other(*args, **kwargs): raise ValueError('fixture')
    monkeypatch.setattr(sweeper, 'private_write', fail_other)
    with pytest.raises(ValueError, match='fixture'):
        sweeper.sweep(s.root, bridge=object(), clock=clock)
    assert lock_available(s)


def test_no_yield_preserves_start_and_success_order(setup_service, monkeypatch):
    """T-H6: one live thread, original success order, and no marker writes."""
    s, clock = setup_service
    order = []
    monkeypatch.setattr(s, '_job_states', lambda: order.append('rows') or [{'id': 'fixture', 'phase': 'running'}])
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: order.append('sessions'))
    monkeypatch.setattr(s, 'tick', lambda state: order.append('job'))
    driver = Driver(monkeypatch); driver.instrument(s, monkeypatch)
    try:
        s.start(); first = s._thread; driver.event('sleep', 10)
        for _ in range(50): s.start()
        assert s._thread is first and first.is_alive() and not lock_available(s)
        assert order == ['rows', 'sessions', 'job']
        assert not (s.root / 'worker-yield.json').exists()
        assert not (s.root / 'sweeper-last-run.json').exists()
    finally:
        driver.stop(s._thread)
    assert lock_available(s)
    assert not (s.root / 'worker-yield.json').exists()
    assert not (s.root / 'sweeper-last-run.json').exists()


def test_yield_marker_write_failure_releases_lock(setup_service, monkeypatch, caplog):
    """T-H7: even an atomic-write exception cannot retain the lock."""
    s, clock = setup_service
    def fail(*args, **kwargs):
        assert not lock_available(s)
        raise PermissionError('private detail')
    monkeypatch.setattr(cs, 'private_write', fail)
    driver, thread = yield_worker(s, monkeypatch)
    assert not thread.is_alive() and lock_available(s)
    assert not (s.root / 'worker-yield.json').exists()
    assert sum('colab_worker_yield_marker_failed exception=PermissionError' in r.message for r in caplog.records) == 1
    assert all('private detail' not in r.message for r in caplog.records)


def test_pipeline_fixed_clock_rearm_waits_for_new_sweep(setup_service, tmp_path, monkeypatch):
    """T-H8: two real cancellations at one timestamp need two distinct sweeps."""
    from compute_pipeline import ComputePipeline
    from compute_routing import ComputeRouter, ComputeAdapter
    from compute_execution import PipelineExecution
    from compute_web import ComputeWeb

    s, clock = setup_service
    owner, pipeline_id, account = 'owner', uid(), uid()
    web = object.__new__(ComputeWeb); web.colab = s
    adapter = ComputeAdapter(lambda *a: None, lambda *a: None, web.status_colab, web.cancel_colab)
    router = ComputeRouter(tmp_path / 'router.sqlite3', {'colab': adapter}, clock=clock)
    pipe = ComputePipeline(tmp_path / 'pipeline.sqlite3', router_factory=lambda _: router,
                           artifact_resolver=lambda *a: None, clock=clock)
    state = pipe.create(owner, pipeline_id,
                        {'version': 1, 'steps': [{'id': 'one', 'request': {'featureId': 'fixture'}, 'placement': 'colab'}]})
    job_id = state['steps'][0]['request_id']
    first_session = assigned_session(s)
    with s.db() as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job_id, owner, account, 'fixture', cs.encoded(
            {'id': job_id, 'phase': 'queued', 'account_id': account, 'session_id': first_session['id'], 'session_job': True})))
    receipt = {'state': 'queued', 'downstream_id': job_id, 'cancel_supported': True}
    with router._db() as db:
        db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (owner, job_id, 'fixture', 'colab', 'colab', 'queued', '{}', '{}', json.dumps(receipt), 1, 1))
    with pipe._db() as db:
        db.execute('UPDATE compute_pipelines SET cancel_requested=1 WHERE owner=? AND pipeline_id=?', (owner, pipeline_id))
        db.execute("UPDATE compute_pipeline_attempts SET state='queued',receipt_json=? WHERE owner=? AND pipeline_id=?",
                   (json.dumps(receipt), owner, pipeline_id))

    def fail(): raise sqlite3.OperationalError('fixture')
    monkeypatch.setattr(s.sessions, 'tick_all', fail)
    driver = Driver(monkeypatch); driver.instrument(s, monkeypatch)
    operations = []
    def bridge(op, _directory, **kwargs):
        operations.append(op)
        if op == 'runtime_stop': return {'stopped': True}
        if op == 'account_status': return {}
        raise AssertionError(op)
    outcome = {'premature': 0, 'sweeps': [], 'last': None, 'session': first_session}
    def finish_yield():
        driver.event('sleep', 10); driver.advance()
        driver.event('sleep', 10); driver.advance()
        driver.event('exit'); driver.finish(s._thread)
        assert lock_available(s)
    def attempt_sweep():
        session = outcome['session']; before = operations.count('runtime_stop')
        assert not s.sessions.get(session['id'])['watchdog_armed']
        acquired = sweeper.sweep(s.root, bridge=bridge, clock=clock)
        released = s.sessions.get(session['id'])['released']
        stopped = operations.count('runtime_stop') == before + 1
        outcome['sweeps'].append((acquired, released, stopped))

    class StopProbe(BaseException): pass
    class Wake:
        count = 0
        def wait(self, seconds):
            assert seconds == 5
            self.count += 1
            if self.count == 1:
                finish_yield(); attempt_sweep()
                outcome['session'] = assigned_session(s)
            elif self.count == 2:
                finish_yield(); outcome['last'] = s._thread
            else:
                if s._thread is not outcome['last']:
                    outcome['premature'] += 1
                    outcome['last'] = s._thread
                    driver.event('sleep', 10)
                    if self.count < 7:
                        driver.advance(); driver.event('sleep', 10); driver.advance()
                        driver.event('exit'); driver.finish(s._thread)
                if self.count == 7:
                    attempt_sweep()
                    raise StopProbe
        def clear(self): pass

    execution = object.__new__(PipelineExecution)
    execution.engine = pipe; execution._wake = Wake()
    try:
        with pytest.raises(StopProbe): execution._loop()
    finally:
        driver.stop(s._thread)
    print('H8 premature=%d sweeps=%s fixed_clock=%s' % (outcome['premature'], outcome['sweeps'], clock.now))
    assert s.job(owner, job_id)['cancel_requested'] is True
    assert outcome['premature'] == 0
    assert outcome['sweeps'] == [(True, True, True), (True, True, True)]


def test_zero_time_without_prior_sweep_is_held(setup_service, monkeypatch):
    """T-H9: zero is a real yield time, not evidence of a previous sweep."""
    s, clock = setup_service; clock.now = 0.0
    session = assigned_session(s)
    driver, first = yield_worker(s, monkeypatch)
    marker = s.root / 'worker-yield.json'
    assert json.loads(marker.read_text()) == {'yielded_at': 0.0, 'sweep_run_id': None}
    assert not (s.root / 'sweeper-last-run.json').exists()
    s.start(); assert s._thread is first and not first.is_alive() and lock_available(s)
    operations = []
    def bridge(op, _directory, **kwargs):
        operations.append(op)
        return {'stopped': True} if op == 'runtime_stop' else {}
    assert sweeper.sweep(s.root, bridge=bridge, clock=clock)
    assert 'runtime_stop' in operations and s.sessions.get(session['id'])['released']
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: None)
    try:
        s.start(); driver.event('sleep', 10)
        assert s._thread is not first and s._thread.is_alive() and not marker.exists()
    finally:
        driver.stop(s._thread)


def test_mode000_yield_marker_is_deleted_on_start(setup_service, monkeypatch, caplog):
    """T-H10: a read error must attempt deletion before starting a worker."""
    s, clock = setup_service
    marker = s.root / 'worker-yield.json'
    marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': None}))
    marker.chmod(0)
    class DummyThread:
        def __init__(self, **kwargs): self.started = False
        def start(self): self.started = True
        def is_alive(self): return self.started
    monkeypatch.setattr(cs.threading, 'Thread', DummyThread)
    try:
        s.start()
        assert s._thread is not None and s._thread.started and not marker.exists()
        assert sum('colab_worker_yield_marker_unreadable' in r.message for r in caplog.records) == 1
    finally:
        if marker.exists(): marker.chmod(0o600)


def test_malformed_sweeper_at_yield_then_valid_run_releases(setup_service, monkeypatch):
    """T-H11a: a later valid sweep is distinct from an unreadable baseline."""
    s, clock = setup_service
    finished = s.root / 'sweeper-last-run.json'; finished.write_text('{')
    driver, first = yield_worker(s, monkeypatch)
    marker = s.root / 'worker-yield.json'
    assert json.loads(marker.read_text())['sweep_run_id'] is None
    s.start(); assert s._thread is first and not first.is_alive()
    assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: None)
    try:
        s.start(); driver.event('sleep', 10)
        assert s._thread is not first and s._thread.is_alive() and not marker.exists()
    finally:
        driver.stop(s._thread)


def test_valid_sweeper_at_yield_then_malformed_holds_to_timeout(setup_service, monkeypatch):
    """T-H11b: an invalid later record cannot prove another sweep occurred."""
    s, clock = setup_service
    assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    previous = s._sweeper_run_id(); assert previous is not None
    driver, first = yield_worker(s, monkeypatch)
    marker = s.root / 'worker-yield.json'
    assert json.loads(marker.read_text())['sweep_run_id'] == previous
    (s.root / 'sweeper-last-run.json').write_text('{')
    s.start(); assert s._thread is first and not first.is_alive()
    clock.now += 899; s.start(); assert s._thread is first
    clock.now += 1
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: None)
    try:
        s.start(); driver.event('sleep', 10)
        assert s._thread is not first and s._thread.is_alive() and not marker.exists()
    finally:
        driver.stop(s._thread)


def test_failed_sweeper_marker_write_holds_until_timeout(setup_service, monkeypatch):
    """T-H12: a successful sweep whose marker fails cannot release the hold."""
    s, clock = setup_service
    assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    previous = s._sweeper_run_id(); assert previous is not None
    driver, first = yield_worker(s, monkeypatch)
    def fail_write(*args, **kwargs): raise OSError('fixture')
    with monkeypatch.context() as m:
        m.setattr(sweeper, 'private_write', fail_write)
        assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    assert s._sweeper_run_id() == previous
    s.start(); assert s._thread is first and not first.is_alive()
    clock.now += 899; s.start(); assert s._thread is first
    clock.now += 1
    monkeypatch.setattr(s.sessions, 'tick_all', lambda: None)
    try:
        s.start(); driver.event('sleep', 10)
        assert s._thread is not first and s._thread.is_alive()
    finally:
        driver.stop(s._thread)


def test_invalid_yield_run_ids_are_deleted_and_warn_once(setup_service, caplog):
    s, clock = setup_service
    marker = s.root / 'worker-yield.json'
    values = ('short', 'A' * 32, 'g' * 32, 'a' * 31, 'a' * 33, True, 42, [], {})
    for value in values:
        marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': value}))
        assert not s._restart_held() and not marker.exists()
    marker.write_text(json.dumps({'yielded_at': clock.now}))
    assert not s._restart_held() and not marker.exists()
    assert sum('colab_worker_yield_marker_unreadable' in r.message for r in caplog.records) == 1


def test_hold_log_is_once_per_yield_time_and_sweep_id(setup_service, caplog):
    s, clock = setup_service
    marker = s.root / 'worker-yield.json'; finished = s.root / 'sweeper-last-run.json'
    for run_id in ('a' * 32, 'b' * 32, 'a' * 32):
        marker.write_text(json.dumps({'yielded_at': clock.now, 'sweep_run_id': run_id}))
        finished.write_text(json.dumps({'finished_at': -1, 'run_id': run_id}))
        assert s._restart_held() and s._restart_held()
    assert sum('colab_worker_start_held' in r.message for r in caplog.records) == 2


def test_each_sweeper_run_gets_a_new_id_even_at_equal_time(setup_service):
    s, clock = setup_service
    assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    first = s._sweeper_run_id(); assert first is not None
    assert sweeper.sweep(s.root, bridge=object(), clock=clock)
    second = s._sweeper_run_id(); assert second is not None and second != first
