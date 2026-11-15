"""Unsent transport faults keep a wider, still bounded recovery window."""
import contextlib
import sqlite3
import uuid

import pytest
from colab_service import BridgeError
from colab_sessions import (
    FAULT_SPAN, HEARTBEAT, UNSENT_FAULT_LIMIT, UNSENT_FAULT_SPAN, UNSENT_STAGES, WorkSessions,
)
from review_r38_fixture import rig, session


def _fault(r, st, error, stage='heartbeat', reconciling_launch=False):
    r.s.sessions.operation(st, stage)
    r.s.sessions.record_fault(st, error, reconciling_launch=reconciling_launch)
    return r.s.sessions.get(st['id'])


def _unsent():
    return BridgeError('transport_not_ready')


def test_six_unsent_heartbeat_faults(rig):
    r = rig
    st = session(r)
    base = r.t[0]
    phase = st['phase']
    for index, offset in enumerate((0, 20, 40, 60, 80), start=1):
        r.t[0] = base + offset
        cur = _fault(r, st, _unsent())
        assert cur['phase'] == phase
        assert cur['fault_streak'] == index
        assert cur['fault_streak_unsent'] is True
        assert cur['fault']['code'] == 'transport_not_ready'
    r.t[0] = base + 100
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'
    assert cur['terminal_fault']['count'] == 6


def test_wall_span_closes_past_180(rig):
    r = rig
    st = session(r)
    base = r.t[0]
    phase = st['phase']
    for offset in (0, 179):
        r.t[0] = base + offset
        cur = _fault(r, st, _unsent())
        assert cur['phase'] == phase and cur['fault_streak_unsent'] is True
    r.t[0] = base + 181
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_monotonic_span_times_out_while_wall_is_frozen(rig):
    r = rig
    mono = [500.0]
    r.s.sessions._monotonic = lambda: mono[0]
    st = session(r)
    _fault(r, st, _unsent())
    mono[0] += 181
    remaining, code = r.s.sessions.budget_status(r.s.sessions.get(st['id']))
    assert code == 'timeout' and remaining <= 0
    with pytest.raises(BridgeError):
        r.s.sessions.recovery_remaining(r.s.sessions.get(st['id']))
    cur = r.s.sessions.get(st['id'])
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_restart_does_not_open_a_new_unsent_window(rig):
    r = rig
    mono = [1000.0]
    st = session(r, fault_streak=2, fault_streak_unsent=True, fault_since=r.t[0] - 150,
                 fault={'stage': 'heartbeat', 'code': 'transport_not_ready', 'at': r.t[0] - 150,
                        'count': 2, 'ambiguous': False})
    r.s.sessions = WorkSessions(r.s)
    r.s.sessions._monotonic = lambda: mono[0]
    remaining, code = r.s.sessions.budget_status(r.s.sessions.get(st['id']))
    assert code is None and 0 < remaining <= 30
    anchor = r.s.sessions._recovery_anchors[st['id']]
    assert anchor[1] - mono[0] <= 30
    mono[0] += 31
    remaining, code = r.s.sessions.budget_status(r.s.sessions.get(st['id']))
    assert code == 'timeout' and remaining <= 0


def test_heartbeat_still_bounds_an_unsent_streak(rig):
    r = rig
    st = session(r, last_tick=r.t[0] - 290)
    phase = st['phase']
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == phase and cur['fault_streak'] == 1 and cur['fault_streak_unsent'] is True
    r.t[0] += 11
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_mixed_unsent_then_ambiguous_execution_closes(rig):
    r = rig
    st = session(r)
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == st['phase'] and cur['fault_streak_unsent'] is True
    cur = _fault(r, st, BridgeError('execution_uncertain', True))
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_mixed_execution_then_unsent_closes(rig):
    r = rig
    st = session(r)
    cur = _fault(r, st, BridgeError('execution_uncertain', True))
    assert cur['phase'] == st['phase'] and cur['fault_streak_unsent'] is False
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_mixed_unsent_then_busy_store_closes(rig):
    r = rig
    st = session(r)
    cur = _fault(r, st, _unsent())
    assert cur['phase'] == st['phase']
    cur = _fault(r, st, sqlite3.OperationalError('database is locked'))
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'
    assert cur['fault']['code'] == 'store_busy'


def test_unsent_upload_uses_the_short_window(rig):
    r = rig
    st = session(r)
    cur = _fault(r, st, _unsent(), stage='upload')
    assert cur['phase'] == st['phase'] and cur['fault_streak'] == 1 and cur['fault_streak_unsent'] is False
    cur = _fault(r, st, _unsent(), stage='upload')
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_unsent_while_reconciling_launch_uses_the_short_window(rig):
    r = rig
    st = session(r)
    cur = _fault(r, st, _unsent(), reconciling_launch=True)
    assert cur['phase'] == st['phase'] and cur['fault_streak'] == 1 and cur['fault_streak_unsent'] is False
    cur = _fault(r, st, _unsent(), reconciling_launch=True)
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_unsent_attach_closes_immediately(rig):
    r = rig
    st = session(r)
    cur = _fault(r, st, _unsent(), stage='attach')
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'
    assert cur['fault_streak'] == 1


def test_ambiguous_transport_not_ready_uses_the_short_window(rig):
    r = rig
    st = session(r)
    error = BridgeError('transport_not_ready', True)
    cur = _fault(r, st, error)
    assert cur['phase'] == st['phase'] and cur['fault_streak'] == 1 and cur['fault_streak_unsent'] is False
    cur = _fault(r, st, error)
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_successful_cycle_resets_unsent_streak(rig):
    r = rig
    st = session(r)
    sid = st['id']
    for _ in range(3):
        r.t[0] += 10
        cur = _fault(r, st, _unsent())
    assert cur['fault_streak'] == 3 and cur['fault_streak_unsent'] is True
    r.s.sessions.save(sid, cycle_session_ok=True, cycle_faulted=False, cycle_job=None)
    r.t[0] += 10
    r.s.sessions.finish_cycle(sid)
    cur = r.s.sessions.get(sid)
    assert cur['fault_streak'] == 0 and cur['fault_streak_unsent'] is False
    cur = _fault(r, st, BridgeError('execution_uncertain', True))
    assert cur['phase'] == st['phase'] and cur['fault_streak'] == 1
    cur = _fault(r, st, BridgeError('execution_uncertain', True))
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'


def test_tick_all_four_unsent_heartbeats_then_a_complete_cycle(rig):
    from test_review_r38_faults import probe_kind
    r = rig
    st = session(r)
    seen = []

    def hook(op, kw):
        if probe_kind(op, kw) == 'heartbeat':
            seen.append(1)
            if len(seen) <= 4:
                raise BridgeError('transport_not_ready')
        return None

    r.b.hook = hook
    for _ in range(4):
        r.t[0] += 5
        r.s.sessions.tick_all()
        cur = r.s.sessions.get(st['id'])
        assert cur['phase'] == 'ready'
    assert cur['fault_streak'] == 4 and cur['fault_streak_unsent'] is True
    previous = cur['last_tick']
    r.t[0] += 5
    r.s.sessions.tick_all()
    cur = r.s.sessions.get(st['id'])
    assert cur['phase'] == 'ready' and cur['fault_streak'] == 0 and cur['fault_streak_unsent'] is False
    assert cur['last_tick'] == r.t[0] and cur['last_tick'] != previous


def test_clock_inconsistent_closes_at_that_fault(rig):
    r = rig
    st = session(r)
    r.t[0] -= 5
    cur = _fault(r, st, _unsent())
    assert cur['fault']['code'] == 'clock_inconsistent'
    assert cur['phase'] == 'closing' and cur['reason'] == 'watchdog_state_lost'
    assert cur.get('fault_streak_unsent') is not True


def test_unsent_constants_hold():
    assert UNSENT_FAULT_LIMIT == 6 and UNSENT_FAULT_SPAN == 180
    assert UNSENT_STAGES == frozenset({'heartbeat', 'observe', 'bootstrap_probe', 'job_probe'})
    assert FAULT_SPAN <= UNSENT_FAULT_SPAN < HEARTBEAT


def _lookup_account(tmp_path):
    from scripts import colab_bridge as cb
    account = cb.private_directory(str(tmp_path / 'account'))
    cb.atomic_json(account / 'identity.json', {
        'issuer': cb.GOOGLE_ISSUER, 'subject': 'subject-1', 'verified_at': 1})
    runtime_id = str(uuid.uuid4())
    runtime_dir = cb.private_directory(str(account / 'runtimes'))
    cb.atomic_json(runtime_dir / (runtime_id + '.json'), {
        'runtime_id': runtime_id, 'accelerator': 'A100', 'endpoint': 'ep-1', 'state': 'allocated'})
    return cb, account, runtime_id


def _sdk(cb, assignments, runtime):
    sdk = cb.GoogleSDK.__new__(cb.GoogleSDK)

    class Client:
        def list_assignments(self):
            return assignments()

    sdk.credentials = lambda path: object()
    sdk.client = lambda credentials: Client()
    sdk.Runtime = runtime
    sdk.execution_transport = contextlib.nullcontext
    return sdk


class _Proxy:
    url = 'https://abc.googleusercontent.com/runtime'
    token = 'fixture-token'


class _Match:
    endpoint = 'ep-1'
    runtime_proxy_info = _Proxy()


class _Other:
    endpoint = 'other-endpoint'


@pytest.mark.parametrize('kind', [ConnectionError, OSError])
def test_lookup_exception_is_unsent_and_does_not_construct_runtime(tmp_path, kind):
    cb, account, runtime_id = _lookup_account(tmp_path)
    constructed = []

    class Runtime:
        def __init__(self, *args, **kwargs):
            constructed.append(1)

    sdk = _sdk(cb, lambda: (_ for _ in ()).throw(kind('lookup failed')), Runtime)
    result = cb.handle({
        'op': 'remote_exec', 'account_dir': str(account), 'runtime_id': runtime_id,
        'code': 'pass', 'timeout_seconds': 3,
    }, sdk_factory=lambda: sdk)
    assert result['ok'] is False
    assert result['error']['code'] == 'transport_not_ready'
    assert result['error']['ambiguous'] is False
    assert constructed == []


def test_lookup_without_endpoint_stays_runtime_missing(tmp_path):
    cb, account, runtime_id = _lookup_account(tmp_path)
    constructed = []

    class Runtime:
        def __init__(self, *args, **kwargs):
            constructed.append(1)

    sdk = _sdk(cb, lambda: [_Other()], Runtime)
    result = cb.handle({
        'op': 'remote_exec', 'account_dir': str(account), 'runtime_id': runtime_id,
        'code': 'pass', 'timeout_seconds': 3,
    }, sdk_factory=lambda: sdk)
    assert result['ok'] is False
    assert result['error']['code'] == 'runtime_missing'
    assert result['error']['ambiguous'] is False
    assert constructed == []


def test_failure_after_send_stays_execution_uncertain(tmp_path, monkeypatch):
    from scripts import colab_rpc_transport as rpc
    from test_compute_r40b import Client
    cb, account, runtime_id = _lookup_account(tmp_path)
    real_scoped = rpc.scoped
    monkeypatch.setattr(rpc, 'scoped', lambda budget: real_scoped(budget, Client))

    class Runtime:
        def __init__(self, *args, **kwargs):
            return None

        def execute_code(self, *args, **kwargs):
            client = Client(True)
            client.start_channels()
            client.execute()
            raise RuntimeError('Connection was lost.')

        def stop(self, **kwargs):
            return None

    sdk = _sdk(cb, lambda: [_Match()], Runtime)
    result = cb.handle({
        'op': 'remote_exec', 'account_dir': str(account), 'runtime_id': runtime_id,
        'code': 'pass', 'timeout_seconds': 3,
    }, sdk_factory=lambda: sdk)
    assert result['ok'] is False
    assert result['error']['code'] == 'execution_uncertain'
    assert result['error']['ambiguous'] is True


def test_lookup_failure_counts_as_unsent_heartbeat(rig, tmp_path):
    cb, account, runtime_id = _lookup_account(tmp_path)

    class Runtime:
        def __init__(self, *args, **kwargs):
            raise AssertionError('Runtime constructed')

    sdk = _sdk(cb, lambda: (_ for _ in ()).throw(ConnectionError('lookup failed')), Runtime)
    result = cb.handle({
        'op': 'remote_exec', 'account_dir': str(account), 'runtime_id': runtime_id,
        'code': 'pass', 'timeout_seconds': 3,
    }, sdk_factory=lambda: sdk)
    error = result['error']
    service_error = BridgeError(str(error['code']), bool(error['ambiguous']),
                                 error.get('diagnostics'), error.get('private_diagnostics'))
    r = rig
    mono = [100.0]
    r.s.sessions._monotonic = lambda: mono[0]
    st = session(r)
    cur = _fault(r, st, service_error)
    assert cur['phase'] == st['phase'] and cur['fault_streak'] == 1
    assert cur['fault']['code'] == 'transport_not_ready' and cur['fault_streak_unsent'] is True
    remaining, code = r.s.sessions.budget_status(cur)
    assert code is None and 90 < remaining <= 180
    anchor = r.s.sessions._recovery_anchors[st['id']]
    assert anchor[1] - mono[0] == pytest.approx(UNSENT_FAULT_SPAN)
    cur = _fault(r, st, service_error)
    cur = _fault(r, st, service_error)
    assert cur['phase'] == st['phase'] and cur['fault_streak'] == 3 and cur['fault_streak_unsent'] is True
