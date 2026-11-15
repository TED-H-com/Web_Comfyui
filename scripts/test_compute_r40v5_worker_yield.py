"""Worker supervision yields its real flock without weakening job isolation."""
import fcntl
import json
import os
import queue
import sqlite3
import threading
import uuid
from pathlib import Path
import pytest
import colab_service as cs
from scripts import colab_lease_sweeper


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('CASTLE_COLAB_DB_NESTING', 'strict')
    return cs.ColabService(tmp_path / 'colab', bridge=object(), clock=lambda: 1000.0)


def ident():
    return str(uuid.uuid4())


def add_job(s, raw, job_id=None):
    job_id = job_id or ident()
    with s.db() as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',
                   (job_id, 'owner', 'account', 'fixture', raw))
    return job_id


class Driver:
    def __init__(self, monkeypatch):
        self.events = queue.Queue()
        self.permits = threading.Semaphore(0)
        self.stopping = False
        self.errors = []
        self.sleeps = []
        monkeypatch.setattr(cs.time, 'sleep', self.sleep)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.events.put(seconds)
        if not self.permits.acquire(timeout=5) or self.stopping:
            raise SystemExit

    def wait(self, seconds=10):
        assert self.events.get(timeout=3) == seconds

    def advance(self):
        self.permits.release()

    def stop(self, thread):
        self.stopping = True
        self.permits.release()
        thread.join(3)
        assert not thread.is_alive()
        assert not self.errors

    def run(self, service):
        def invoke():
            try:
                service._loop()
            except SystemExit:
                pass
            except BaseException as error:
                self.errors.append(error)
        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        return thread


def lock_available(s):
    with (s.root / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(lock, fcntl.LOCK_UN)
        return True


def test_persistent_failure_yields_lock_and_real_sweeper_releases(service, monkeypatch):
    """T-B1-1; this same test must fail on the revision-1 loop."""
    s = service
    session = s.sessions.new_state('owner', ident(), 'a'*64,
                                   {'idle_minutes':20, 'hard_hours':6, 'max_units':120})
    session.update(phase='arming', runtime_id='fixture-runtime', watchdog_armed=False, deadline_epoch=22600)
    with s.db() as db:
        db.execute('INSERT INTO work_sessions VALUES (?,?,?)', (session['id'], 'owner', cs.encoded(session)))
    calls = []
    def fail():
        calls.append('sessions')
        raise sqlite3.OperationalError('private')
    monkeypatch.setattr(s.sessions, 'tick_all', fail)
    d = Driver(monkeypatch); thread = d.run(s)
    try:
        d.wait(); assert not lock_available(s); d.advance()
        d.wait(); d.advance(); thread.join(2)
        assert not thread.is_alive(), 'worker retained lock after three unsupervised cycles'
        assert calls == ['sessions']*3 and d.sleeps == [10, 10]
        assert s.worker_iteration_failures == 3 and lock_available(s)
    finally:
        d.stop(thread)
    bridge_calls = []
    def mock_call(self, state, op, **kwargs):
        bridge_calls.append(op)
        if op == 'runtime_stop': return {'stopped':True}
        raise ValueError('mock status unavailable')
    monkeypatch.setattr(cs.ColabService, '_call', mock_call)
    assert colab_lease_sweeper.sweep(s.root, bridge=object(), clock=lambda:1000.0)
    assert bridge_calls.count('runtime_stop') == 1
    assert s.sessions.get(session['id'])['released'] is True


def test_unreadable_rows_do_not_block_supervision(service, monkeypatch, caplog):
    s=service
    ids=[add_job(s, '{'), add_job(s, '[]'), add_job(s, '{}')]
    good=ident(); add_job(s, json.dumps({'id':good,'phase':'running'}), good)
    supervised=[]; ticked=[]
    monkeypatch.setattr(s.sessions,'tick_all',lambda:supervised.append(1))
    monkeypatch.setattr(s,'tick',lambda state:ticked.append(state['id']))
    d=Driver(monkeypatch); thread=d.run(s)
    try:
        d.wait();d.advance();d.wait()
        assert thread.is_alive() and not lock_available(s)
        assert supervised == [1,1] and ticked == [good,good]
        assert s.worker_iteration_failures == 0 and s.unreadable_job_rows == 6
        for bad in ids: assert caplog.text.count('colab_job_row_unreadable id='+bad)==1
    finally:d.stop(thread)


def test_bad_row_logging_is_process_wide_bounded_and_sanitized(service, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cs,'_unreadable_job_ids',set())
    other=cs.ColabService(tmp_path/'other',bridge=object(),clock=lambda:1000.0)
    bad='not-a-canonical-uuid-private';ids=[bad]+[ident() for _ in range(20)]
    for job_id in ids:add_job(service,'{',job_id)
    add_job(other,'{',ids[1]);add_job(other,'{',ident())
    assert service._job_states()==[] and other._job_states()==[]
    assert service.unreadable_job_rows==21 and other.unreadable_job_rows==2
    lines=[line for line in caplog.text.splitlines() if 'colab_job_row_unreadable' in line]
    assert len(lines)==20 and sum('id=invalid' in line for line in lines)==1
    assert bad not in caplog.text and sum('id='+ids[1] in line for line in lines)==1


def test_failed_scan_does_not_block_session_supervision(service, monkeypatch):
    s=service;calls=[]
    monkeypatch.setattr(s,'_job_states',lambda:(_ for _ in ()).throw(RuntimeError('private')))
    monkeypatch.setattr(s.sessions,'tick_all',lambda:calls.append('sessions'))
    monkeypatch.setattr(s,'tick',lambda _:calls.append('job'))
    d=Driver(monkeypatch);thread=d.run(s)
    try:
        for _ in range(4): d.wait();d.advance()
        d.wait()
        assert calls == ['sessions']*5 and thread.is_alive() and not lock_available(s)
        assert s.worker_iteration_failures == 5
    finally:d.stop(thread)


def test_success_resets_consecutive_failures(service, monkeypatch):
    s=service;calls=[]
    def tick_all():
        calls.append(1)
        if len(calls)!=3:raise RuntimeError('private')
    monkeypatch.setattr(s.sessions,'tick_all',tick_all)
    d=Driver(monkeypatch);thread=d.run(s)
    try:
        for _ in range(5):
            d.wait();assert thread.is_alive() and not lock_available(s);d.advance()
        thread.join(2)
        assert not thread.is_alive() and len(calls)==6 and lock_available(s)
        assert s.worker_iteration_failures==5 and d.sleeps == [10]*5
    finally:d.stop(thread)


def test_start_after_yield_waits_for_other_lock_holder(service, monkeypatch):
    s=service;d=Driver(monkeypatch); calls=[]
    def failing():calls.append('failed');raise RuntimeError('private')
    monkeypatch.setattr(s.sessions,'tick_all',failing)
    thread=d.run(s)
    try:
        d.wait();d.advance();d.wait();d.advance();thread.join(2)
        assert not thread.is_alive() and lock_available(s)
        s.start(); assert s._thread is None
        assert colab_lease_sweeper.sweep(s.root, bridge=lambda *a, **k: {}, clock=s.clock)
        other=(s.root/'worker.lock').open('a');fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        monkeypatch.setattr(s.sessions,'tick_all',lambda:calls.append('supervised'))
        s.start(); restarted=s._thread
        d.wait(1);assert restarted.is_alive() and calls==['failed']*3
        fcntl.flock(other,fcntl.LOCK_UN);other.close();d.advance()
        d.wait();assert calls[-1]=='supervised' and not lock_available(s)
        monkeypatch.setattr(s.sessions,'tick_all',failing)
        d.advance();d.wait();d.advance();d.wait();d.advance();restarted.join(2)
        assert not restarted.is_alive() and lock_available(s)
    finally:
        if thread.is_alive():d.stop(thread)


def test_failed_update_does_not_skip_next_job(service, monkeypatch):
    s=service;first,second=ident(),ident();calls=[]
    for item in (first,second):add_job(s,json.dumps({'id':item,'phase':'running'}),item)
    monkeypatch.setattr(s.sessions,'tick_all',lambda:None)
    def tick(state):
        calls.append(state['id'])
        if state['id']==first:raise RuntimeError('private')
    monkeypatch.setattr(s,'tick',tick)
    monkeypatch.setattr(s,'update',lambda *a,**k:(_ for _ in ()).throw(ValueError('private')))
    d=Driver(monkeypatch);thread=d.run(s)
    try:
        d.wait();assert calls==[first,second] and s.worker_iteration_failures==2
    finally:d.stop(thread)


def test_broken_logging_does_not_affect_worker_or_nested_guard(service, monkeypatch):
    s=service;s._db_strict=False; add_job(s,'{')
    monkeypatch.setattr(cs.logger,'warning',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('private')))
    with s.db():
        with s.db() as db:assert db.execute('SELECT 1').fetchone()[0]==1
    assert s.nested_db_events == 1
    monkeypatch.setattr(s.sessions,'tick_all',lambda:(_ for _ in ()).throw(RuntimeError('private')))
    d=Driver(monkeypatch);thread=d.run(s)
    try:
        d.wait();d.advance();d.wait();d.advance();thread.join(2)
        assert not thread.is_alive() and lock_available(s)
        assert s.unreadable_job_rows==3 and s.worker_iteration_failures==3
    finally:d.stop(thread)


def test_connection_close_exception_restores_depth(service, monkeypatch):
    original=cs.sqlite3.connect
    class FailClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise RuntimeError('close failed')
    monkeypatch.setattr(cs.sqlite3,'connect',lambda *a,**k:original(*a,**k,factory=FailClose))
    before=getattr(service._db_local,'depth',0)
    with pytest.raises(RuntimeError,match='close failed'):
        with service.db() as db:assert db.execute('SELECT 1').fetchone()[0]==1
    assert service._db_local.depth==before
