"""Offline receipt collection: disk fixtures and mocked adapters only."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock
import uuid

import pytest

from compute_collector import ENV, ReceiptCollector, HOSTED, SETTLEMENT_WINDOW, SETTLEMENT_INTERVAL, SETTLEMENT_LIMIT
from compute_execution import ExecutionContext
from compute_refresh import request_lock
from compute_routing import ComputeAdapter, ComputeRouter, TERMINAL

OWNER = 'owner@example.test'


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    now = [1000.0]
    wall = [100000.0]
    user = {'email': OWNER, 'role': 'member'}
    server = NS(user_for_email=lambda email: dict(user) if email == OWNER else None)
    path = tmp_path / 'compute-routing.sqlite3'
    forbidden = Mock(side_effect=AssertionError('forbidden operation'))
    status = Mock(return_value={'state': 'queued', 'downstream_id': 'task'})
    adapters = {lane: ComputeAdapter(forbidden, forbidden, status, forbidden, recover=Mock(return_value=None))
                for lane in ('local', 'colab', 'autoip', 'comfy_cloud', 'runninghub')}
    router = ComputeRouter(path, adapters, clock=lambda: wall[0])
    collector = ReceiptCollector(server, path, clock=lambda: now[0], wall_clock=lambda: wall[0])
    collector.router = lambda owner: ComputeRouter(path, adapters, clock=lambda: wall[0])
    monkeypatch.setattr(ComputeRouter, 'submit', forbidden)
    monkeypatch.setattr(ComputeRouter, 'cancel', forbidden)
    monkeypatch.setattr('compute_web.ComputeWeb.close_local_unknown', forbidden)

    def seed(lane='local', state='queued', owner=OWNER, cost=None, downstream=True, updated=None):
        rid = str(uuid.uuid4())
        receipt = {'state': state}
        if downstream:
            receipt['downstream_id'] = 'task'
        if cost is not None:
            receipt['actual_cost'] = {'unit': 'POINT', 'amount': cost}
        with router._db() as db:
            db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (owner, rid, 'hash', lane, lane, state, '{}', '{}', json.dumps(receipt), wall[0] - 86400, wall[0] if updated is None else updated))
        return rid
    yield NS(now=now, wall=wall, server=server, user=user, path=path, forbidden=forbidden,
             status=status, adapters=adapters, router=router, collector=collector, seed=seed)
    forbidden.assert_not_called()


def test_local_history_collected_without_get(rig, monkeypatch):
    from compute_web import ComputeWeb
    rid = rig.seed()
    history = {'task': {'status': {'completed': True}, 'outputs': {'1': {'images': [
        {'filename': 'output.png', 'type': 'output'}]}}}}
    rig.server.get_colab_service = lambda: NS(root=rig.path.parent / 'colab')
    rig.server.get_hosted_compute_service = lambda: NS()
    rig.server.compute_parity_runtime = NS(owns=lambda *args: False)
    rig.server.Handler = NS(_provider_console=lambda context: NS())
    rig.server.local_job_snapshot = lambda: []
    rig.server.load_job_index = lambda: {'task': {'email': OWNER}}
    rig.server.request_json = Mock(return_value=history)
    rig.server.user_can_access_job = lambda user, *args: user == rig.user
    rig.server.user_can_access_view_item = lambda user, *args: user == rig.user
    monkeypatch.setattr(ComputeWeb, '_durable_local_result', lambda *args: None)
    persist = Mock(return_value=True)
    monkeypatch.setattr(ComputeWeb, '_persist_local_history', persist)
    del rig.collector.router  # Exercise real ExecutionContext -> ComputeWeb -> adapter chain.
    assert rig.collector.tick()['refreshed'] == 1
    result = rig.router.status(OWNER, rid)  # Durable read only, no GET/refresh.
    assert result['state'] == 'completed' and result['artifacts']
    rig.server.request_json.assert_called_once_with('/history/task')
    persist.assert_called_once_with('task', history['task'], {'email': OWNER})


@pytest.mark.parametrize('lane', ['colab', 'autoip', 'comfy_cloud', 'runninghub'])
def test_hosted_completed_unsettled_until_cost_observed(rig, lane):
    rid = rig.seed(lane, 'completed')
    rig.status.side_effect = [
        {'state': 'completed', 'downstream_id': 'task'},
        {'state': 'completed', 'downstream_id': 'task', 'actual_cost': {'unit': 'POINT', 'amount': 0}, 'settlement_status': 'settled'}]
    assert rig.collector.tick()['refreshed'] == 1
    for second in (15, 30, 45, 60, 120, 299):
        rig.now[0] = 1000 + second
        assert rig.collector.tick()['deferred'] == 1
    rig.now[0] = 1300
    assert rig.collector.tick()['refreshed'] == 1
    assert rig.router.status(OWNER, rid)['actual_cost']['amount'] == 0
    rig.now[0] = 1600
    assert rig.collector.tick()['scanned'] == 0
    assert rig.status.call_count == 2


@pytest.mark.parametrize('lane,base', [('local', 15), ('colab', 60), ('autoip', 60), ('comfy_cloud', 60), ('runninghub', 60)])
def test_cadence_exponential_backoff_cap_and_reset(rig, lane, base):
    rig.seed(lane)
    rig.status.side_effect = RuntimeError('private exception')
    for failure in range(1, 10):
        assert rig.collector.tick()['errors'] == 1
        delay = min(600, base * 2 ** failure)
        rig.now[0] += delay - 1
        assert rig.collector.tick()['deferred'] == 1
        rig.now[0] += 1
    rig.status.side_effect = None
    assert rig.collector.tick()['errors'] == 0
    rig.now[0] += base - 1
    assert rig.collector.tick()['deferred'] == 1
    rig.now[0] += 1
    assert rig.collector.tick()['refreshed'] == 1


def test_get_and_collector_never_overlap(rig, monkeypatch):
    # Execute the real GET branch without importing/starting the live server.
    import compute_web
    import compute_parity.http
    source = Path(__file__).resolve().parents[1] / 'webui/server.py'
    tree = ast.parse(source.read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
    method = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == 'api_compute')
    namespace = {'sys': sys, '__name__': __name__, 'FEATURE_BY_ID': {}}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    rid = rig.seed('runninghub')
    monkeypatch.setattr(compute_web, 'ComputeWeb', lambda *args: NS(router=rig.router, owner=OWNER, user=rig.user))
    monkeypatch.setattr(compute_parity.http, 'handle', lambda *args: False)
    received = []
    h = NS(send_json=lambda value, *args: received.append(value))
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return {'state': 'completed', 'downstream_id': 'task', 'actual_cost': {'unit': 'POINT', 'amount': 1}}
    rig.status.side_effect = slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(rig.collector.tick)
        try:
            assert entered.wait(5)
            namespace['api_compute'](h, '/api/compute/jobs/' + rid, False)
            assert received[-1]['state'] == 'queued'  # bounded wait, durable fallback
            assert rig.status.call_count == 1
        finally:
            release.set()
        assert task.result(timeout=5)['refreshed'] == 1
    assert rig.router.status(OWNER, rid)['state'] == 'completed'


def test_collector_skips_busy_get(rig):
    rid = rig.seed()
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return {'state': 'queued', 'downstream_id': 'task'}
    rig.status.side_effect = slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        get = pool.submit(rig.router.status, OWNER, rid, refresh=True)
        try:
            assert entered.wait(5)
            assert rig.collector.tick()['busy'] == 1
            assert rig.status.call_count == 1
        finally:
            release.set()
        get.result(timeout=5)
    assert rig.collector.tick()['refreshed'] == 1


@pytest.mark.parametrize('damage', ['missing', 'disabled', 'mustSetPassword', 'mismatch'])
def test_unresolvable_owner_skips_before_adapters(rig, damage):
    rig.seed()
    if damage == 'missing':
        rig.server.user_for_email = lambda email: None
    elif damage == 'mismatch':
        rig.user['email'] = 'another@example.test'
    else:
        rig.user[damage] = True
    rig.collector.router = Mock(side_effect=AssertionError('must skip'))
    assert rig.collector.tick()['unknown_owner'] == 1
    rig.collector.router.assert_not_called()
    rig.status.assert_not_called()


@pytest.mark.parametrize('role', ['admin', 'operator', 'member'])
def test_existing_owner_context_preserves_current_roles(rig, monkeypatch, role):
    import compute_web
    rig.user['role'] = role
    rig.server.Handler = NS(_provider_console=lambda ctx: ctx.current_user()['role'])
    seen = []
    def web(context, server):
        seen.append((context.current_user(), context._provider_console(), server))
        return NS(router=rig.router)
    monkeypatch.setattr(compute_web, 'ComputeWeb', web)
    del rig.collector.router
    assert rig.collector.router(OWNER) is rig.router
    assert seen == [(rig.user, role, rig.server)]
    rig.user['role'] = 'member'
    assert ExecutionContext(rig.server, OWNER).current_user()['role'] == 'member'


def test_legacy_identity_uses_existing_credential_resolution(rig):
    rig.server.user_for_email = lambda email: None
    rig.server.LOCAL_SESSION_USER = dict(rig.user)
    rig.server.local_credential_version = lambda: 'synthetic-version'
    assert ExecutionContext(rig.server, OWNER).current_user() == rig.user
    rig.server.local_credential_version = lambda: None
    with pytest.raises(PermissionError):
        ExecutionContext(rig.server, OWNER)


@pytest.mark.parametrize('lane', ['local', 'colab', 'autoip', 'comfy_cloud', 'runninghub'])
def test_terminal_settled_and_closed_untouched(rig, lane):
    for state in TERMINAL:
        rig.seed(lane, state, cost=0)
    if lane == 'local':
        rig.seed(lane, 'completed')
    with rig.router._db() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM compute_requests')]
    assert rig.collector.tick()['scanned'] == 0
    with rig.router._db() as db:
        assert before == [tuple(row) for row in db.execute('SELECT * FROM compute_requests')]
    rig.status.assert_not_called()


def test_uncertain_only_recovers_original_identity(rig):
    rid = rig.seed(state='submission_uncertain', downstream=False)
    adapter = rig.adapters['local']
    adapter.recover.return_value = None
    assert rig.collector.tick()['refreshed'] == 1
    adapter.recover.assert_called_once_with(OWNER, rid)
    assert rig.router.status(OWNER, rid)['state'] == 'submission_uncertain'
    rig.status.assert_not_called()
    adapter.recover.side_effect = RuntimeError('private')
    rig.now[0] += 15
    assert rig.collector.tick()['errors'] == 1
    rig.now[0] += 15
    assert rig.collector.tick()['deferred'] == 1


def test_bounded_rotation_no_starvation(rig):
    for _ in range(45):
        rig.seed('runninghub')
    assert rig.collector.tick()['refreshed'] == 20
    assert rig.collector.tick()['refreshed'] == 20
    assert rig.collector.tick()['refreshed'] == 5
    assert rig.collector.tick()['deferred'] == 20
    assert rig.status.call_count == 45


def test_exception_in_one_owner_context_does_not_stop_batch(rig):
    for _ in range(3):
        rig.seed()
    original = rig.collector.router
    rig.collector.router = Mock(side_effect=[RuntimeError('private'), original(OWNER), original(OWNER)])
    counts = rig.collector.tick()
    assert counts['errors'] == 1 and counts['refreshed'] == 2


def test_loop_survives_scan_error_and_logs_only_counts(rig, caplog):
    caplog.set_level(logging.INFO)
    original = rig.collector.tick
    rig.seed()
    rig.collector.tick = Mock(side_effect=[RuntimeError('private@example.test https://secret.test/?key=secret'), original()])
    waits = []
    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            rig.collector._stop.set()
    rig.collector._stop.wait = wait
    rig.collector._loop()
    assert waits == [15, 15]
    assert rig.collector.tick.call_count == 2
    assert 'tick_errors=1' in caplog.text and 'refreshed=1' in caplog.text
    assert not any(value in caplog.text for value in ('private', 'https:', 'secret', OWNER))


@pytest.mark.parametrize('value', ['0', 'false', 'OFF', 'no'])
def test_kill_switch_blocks_start_and_ticks(rig, monkeypatch, value):
    rig.seed()
    monkeypatch.setenv(ENV, value)
    rig.collector.start()
    assert rig.collector._thread is None
    assert rig.collector.tick()['scanned'] == 0
    rig.status.assert_not_called()


def test_start_is_default_on_daemon_and_idempotent(rig, monkeypatch):
    threads = []
    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs, self.started = kwargs, False
            threads.append(self)
        def start(self): self.started = True
        def is_alive(self): return self.started
    monkeypatch.setattr('compute_collector.threading.Thread', FakeThread)
    rig.collector.start()
    rig.collector.start()
    assert len(threads) == 1
    assert threads[0].kwargs['daemon'] and threads[0].kwargs['name'] == 'compute-receipts'
    assert threads[0].kwargs['target'] == rig.collector._loop


def test_no_database_until_first_request(rig, tmp_path):
    path = tmp_path / 'not-created' / 'routing.sqlite3'
    collector = ReceiptCollector(rig.server, path)
    assert collector.tick()['scanned'] == 0 and not path.exists()


def test_request_locks_are_owner_and_database_scoped(rig, tmp_path):
    a = request_lock(rig.path, OWNER, 'id')
    assert a is request_lock(rig.path, OWNER, 'id')
    assert a is not request_lock(rig.path, 'different', 'id')
    assert a is not request_lock(tmp_path / 'other.db', OWNER, 'id')


def test_server_startup_starts_collector_even_without_routing_database(rig, monkeypatch):
    import argparse
    import os
    import compute_collector
    import comfy_checkpoint_context
    source = Path(__file__).resolve().parents[1] / 'webui/server.py'
    main = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    monkeypatch.setattr(sys, 'argv', ['server.py'])
    monkeypatch.setenv('CASTLE_COLAB_ROOT', str(rig.path.parent / 'fresh' / 'colab'))
    monkeypatch.setattr(comfy_checkpoint_context, 'write_checkpoint_context', Mock())
    factory = Mock(return_value=NS(start=Mock()))
    monkeypatch.setattr(compute_collector, 'ReceiptCollector', factory)
    http = NS(serve_forever=Mock())
    namespace = dict(argparse=argparse, os=os, sys=sys, Path=Path, __name__=__name__,
                     _CASTLE_DATA_HOME=rig.path.parent, DEFAULT_COMFY='unused',
                     CastleThreadingHTTPServer=Mock(return_value=http), Handler=object,
                     library_service=NS(start_indexer=Mock()), library_server_context=lambda: NS(),
                     castle_orchestrator=NS(initialize=Mock(), start_worker_once=Mock()))
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(source), 'exec'), namespace)
    namespace['main']()
    factory.assert_called_once_with(sys.modules[__name__], rig.path.parent / 'fresh' / 'compute-routing.sqlite3')
    factory.return_value.start.assert_called_once_with()
    assert namespace['_RECEIPT_COLLECTOR'] is factory.return_value
    assert not (rig.path.parent / 'fresh').exists()


def test_terminal_transition_after_scan_is_not_refreshed(rig):
    rid = rig.seed('runninghub')
    rows = rig.collector._rows()
    rig.router._save(OWNER, rid, {'state': 'completed', 'downstream_id': 'task',
                                'actual_cost': {'unit': 'POINT', 'amount': 0}})
    rig.collector._rows = lambda: rows
    rig.collector.tick()
    rig.status.assert_not_called()


@pytest.mark.parametrize('role,operator,expected', [('admin', False, 'admin'), ('user', True, 'operator'), ('user', False, 'member')])
def test_context_delegates_to_authenticated_actor_resolution(rig, role, operator, expected):
    source = Path(__file__).resolve().parents[1] / 'webui/server.py'
    handler = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
    method = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == '_provider_runtime_actor')
    namespace = dict(normalize_email=lambda email: email.lower(), is_operator_user=lambda user: operator,
                     _PROVIDER_CANONICAL_CONTEXT=threading.local())
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    rig.user['role'] = role
    runtime = NS(actor_context=Mock(return_value=NS()))
    rig.server.Handler = NS(_provider_runtime_actor=namespace['_provider_runtime_actor'],
                            _provider_runtime=lambda context: runtime)
    ExecutionContext(rig.server, OWNER)._provider_runtime_actor()
    runtime.actor_context.assert_called_once_with(OWNER, expected)


@pytest.mark.parametrize('lane', sorted(HOSTED))
@pytest.mark.parametrize('age,expected', [(0, 1), (21599.999, 1), (21600, 0), (21600.001, 0), (86400 * 7, 0)])
def test_settlement_sql_uses_updated_strict_six_hour_window(rig, lane, age, expected):
    rig.seed(lane, 'completed', updated=rig.wall[0] - age)
    rig.status.return_value = {'state': 'completed', 'downstream_id': 'task'}
    counts = rig.collector.tick()
    assert counts['scanned'] == counts['refreshed'] == expected
    assert rig.status.call_count == expected


@pytest.mark.parametrize('lane', ['local', *sorted(HOSTED)])
def test_old_nonterminal_rows_remain_eligible(rig, lane):
    rig.seed(lane, 'queued', updated=rig.wall[0] - 86400 * 7)
    assert rig.collector.tick()['refreshed'] == 1


@pytest.mark.parametrize('lane', sorted(HOSTED))
@pytest.mark.parametrize('error', [False, True])
def test_twelve_settlement_attempts_per_process_including_failures(rig, lane, error):
    rid = rig.seed(lane, 'completed')
    if error:
        rig.status.side_effect = RuntimeError('private provider error')
    else:
        rig.status.return_value = {'state': 'completed', 'downstream_id': 'task'}
    delay = 600 if error else 300
    for _ in range(12):
        assert rig.collector.tick()['refreshed'] == 1
        rig.now[0] += delay - 1
        assert rig.collector.tick()['refreshed'] == 0
        rig.now[0] += 1
    assert rig.status.call_count == 12
    assert rig.collector._settlement_attempts[(OWNER, rid)] == 12
    # Old scheduling entries expire; the lifetime budget must not expire.
    rig.now[0] += 10000
    assert rig.collector.tick()['refreshed'] == 0
    assert rig.status.call_count == 12
    # Owner demand still uses the unchanged router GET path, without a cap.
    rig.status.side_effect = None
    rig.status.return_value = {'state': 'completed', 'downstream_id': 'task'}
    rig.wall[0] += 3600
    assert rig.router.status(OWNER, rid, refresh=True)['updated_at'] == rig.wall[0]
    assert rig.status.call_count == 13
    assert rig.collector.tick()['refreshed'] == 0  # updated was refreshed by GET
    assert rig.status.call_count == 13
    # Another request has its own independent budget.
    rig.seed(lane, 'completed')
    assert rig.collector.tick()['refreshed'] == 1
    assert rig.status.call_count == 14


@pytest.mark.parametrize('lane', sorted(HOSTED))
def test_get_refreshes_expired_settlement_on_demand(rig, lane):
    rid = rig.seed(lane, 'completed', updated=rig.wall[0] - 86400)
    rig.status.return_value = {'state': 'completed', 'downstream_id': 'task',
                              'actual_cost': {'unit': 'POINT', 'amount': 2}}
    assert rig.collector.tick()['scanned'] == 0
    assert rig.router.status(OWNER, rid, refresh=True)['actual_cost']['amount'] == 2
    assert rig.status.call_count == 1


def test_expiry_between_scan_and_refresh_rechecked_under_lock(rig):
    rig.seed('autoip', 'completed', updated=rig.wall[0] - SETTLEMENT_WINDOW + 1)
    rows = rig.collector._rows()
    rig.collector._rows = lambda: rows
    rig.wall[0] += 1
    assert rig.collector.tick()['refreshed'] == 0
    assert not rig.collector._settlement_attempts
    rig.status.assert_not_called()


def test_nonterminal_completion_enters_five_minute_cadence(rig):
    rid = rig.seed('autoip')
    rig.status.return_value = {'state': 'completed', 'downstream_id': 'task'}
    assert rig.collector.tick()['refreshed'] == 1
    assert not rig.collector._settlement_attempts
    rig.now[0] += 299
    assert rig.collector.tick()['refreshed'] == 0
    rig.now[0] += 1
    assert rig.collector.tick()['refreshed'] == 1
    assert rig.collector._settlement_attempts[(OWNER, rid)] == 1


def test_completion_by_get_after_scan_cannot_bypass_settlement_cap(rig):
    rid = rig.seed('autoip')
    rows = rig.collector._rows()
    rig.collector._rows = lambda: rows
    rig.collector._settlement_attempts[(OWNER, rid)] = SETTLEMENT_LIMIT
    rig.router._save(OWNER, rid, {'state': 'completed', 'downstream_id': 'task'})
    assert rig.collector.tick()['refreshed'] == 0
    rig.status.assert_not_called()


def test_unresolved_or_busy_settlement_does_not_consume_attempt(rig):
    rid = rig.seed('autoip', 'completed')
    rig.server.user_for_email = lambda owner: None
    assert rig.collector.tick()['unknown_owner'] == 1
    assert not rig.collector._settlement_attempts
    rig.server.user_for_email = lambda owner: dict(rig.user)
    rig.now[0] += 600
    entered, release = threading.Event(), threading.Event()
    def hold():
        with request_lock(rig.path, OWNER, rid):
            entered.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(hold)
        try:
            assert entered.wait(5)
            assert rig.collector.tick()['busy'] == 1
            assert not rig.collector._settlement_attempts
        finally:
            release.set()
        task.result(timeout=5)


def test_settlement_budget_does_not_throttle_nonterminal_request(rig):
    rid = rig.seed('runninghub', 'running')
    rig.collector._settlement_attempts[(OWNER, rid)] = SETTLEMENT_LIMIT
    assert rig.collector.tick()['refreshed'] == 1
    rig.now[0] += 60
    assert rig.collector.tick()['refreshed'] == 1
