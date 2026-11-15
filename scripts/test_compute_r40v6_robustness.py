"""r40v6 robustness checks. The runner sets PYTHONPATH; this module does not."""
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

os.environ.setdefault('CASTLE_COLAB_DB_NESTING', 'strict')

import colab_service as cs
from colab_service import ColabError, ColabService
from compute_parity.journal import Journal
from compute_parity.provisioning import states
import compute_parity.journal as journal_mod
import compute_parity.private_assets as private_assets
import hosted_compute
from scripts.colab_rpc_transport import Budget, scoped

R40V5_SUBMIT_PROBE_SHA256 = '6c1447f4f4092412d6f6ef7fdf0c46466bca0174330c71657648a56039c9bc5e'
R40V5_RESERVE_JOB = '{"account":"acct","artifacts":[],"case_id":"voice.demo","cost":{"amount":1,"unit":"CU"},"created_at":1700000000.0,"epoch":"legacy","identity":"ident","lane":"colab","owner":"owner","request_id":"88888888-8888-4888-8888-888888888888","reserved_amount":1,"state":"reserved"}'
SUBJECT = 'ab' * 32
CLOCK = 1_000_000.0


class Bridge:
    installed = True

    def __call__(self, *args, **kwargs):
        raise AssertionError('provider access forbidden')


def service(tmp_path):
    clock = [CLOCK]
    root = tmp_path / 'colab'
    svc = ColabService(root, Bridge(), clock=lambda: clock[0])
    svc.start = lambda: None
    owner = 'owner-a'
    account = '11111111-1111-4111-8111-111111111111'
    with svc.db() as db:
        db.execute('INSERT INTO accounts VALUES (?,?,?,?)', (account, owner, 'lab', '{}'))
    svc.admission_account = lambda *args, **kwargs: (
        {'authenticated': True, 'balance_compute_units': 500.0, 'checked_at': CLOCK},
        {'bound': True, 'subject_key': SUBJECT, 'verified_at': CLOCK},
    )
    return svc, clock, owner, account


def image_body(account, request_id=None):
    return {
        'request_id': request_id or str(uuid.uuid4()),
        'account_id': account,
        'gpu': 'L4',
        'max_units': 20,
        'max_minutes': 60,
        'job': {'type': 'image', 'prompt': 'red square'},
    }


def native_spec():
    return {'type': 'image', 'bundle': {'models': [{'private': True, 'target': 'weights.safetensors'}]}}


def names(root):
    jobs = root / 'jobs'
    if not jobs.exists():
        return []
    return sorted(p.name for p in jobs.iterdir())


def staging(root):
    return [name for name in names(root) if name.startswith('.staging-')]


def job_row(svc, job_id):
    with svc.db() as db:
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
    return None if row is None else json.loads(row[0])


def patch_native_manifest(monkeypatch):
    import scripts.colab_session_inputs as inputs
    monkeypatch.setattr(inputs, 'validate_native_manifest', lambda manifest, directory, check_files=False: manifest)


def test_rb1_second_writer_during_freeze(tmp_path, monkeypatch):
    svc, _clock, owner, account = service(tmp_path)
    patch_native_manifest(monkeypatch)
    body = image_body(account)
    job_id = body['request_id']
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    errors, obs = [], {}

    def freeze(bundle, directory):
        obs['staging'] = bool(staging(svc.root))
        obs['directory'] = (svc.root / 'jobs' / job_id).exists()
        entered.set()
        assert release.wait(30)
        return []

    monkeypatch.setattr(private_assets, 'freeze_private_models', freeze)

    def writer():
        assert entered.wait(3)
        connection = sqlite3.connect(svc.root / 'state.sqlite3', timeout=15)
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(
                'INSERT INTO accounts VALUES (?,?,?,?)',
                ('22222222-2222-4222-8222-222222222222', 'other', 'x', '{}'),
            )
            connection.commit()
            committed.set()
        except sqlite3.OperationalError as error:
            errors.append(str(error))
        finally:
            connection.close()
            release.set()

    def submit():
        svc._submit(owner, body, _native=native_spec())

    worker = threading.Thread(target=writer, daemon=True)
    submitter = threading.Thread(target=submit, daemon=True)
    submitter.start()
    try:
        assert entered.wait(120)
        worker.start()
        worker.join(30)
    finally:
        release.set()
        submitter.join(120)
    assert not submitter.is_alive()
    assert committed.is_set(), errors
    assert obs.get('staging') and not obs.get('directory')
    assert staging(svc.root) == []
    assert job_row(svc, job_id) is not None


def test_rb1_upload_and_state_match_base(tmp_path):
    probe = r'''
import json, uuid
from pathlib import Path
import colab_service as cs
from colab_service import ColabService
class Bridge:
    installed = True
    def __call__(self, *a, **k):
        raise AssertionError('provider')
clock = 1000000.0
root = Path(sys_root)
svc = ColabService(root, Bridge(), clock=lambda: clock)
svc.start = lambda: None
owner, account = 'owner-a', '11111111-1111-4111-8111-111111111111'
rid = '33333333-3333-4333-8333-333333333333'
with svc.db() as db:
    db.execute('INSERT INTO accounts VALUES (?,?,?,?)', (account, owner, 'lab', '{}'))
svc.admission_account = lambda *a, **k: (
    {'authenticated': True, 'balance_compute_units': 500.0, 'checked_at': clock},
    {'bound': True, 'subject_key': 'ab'*32, 'verified_at': clock})
n = {'i': 0}
def fake():
    n['i'] += 1
    return uuid.UUID(int=n['i'])
cs.uuid.uuid4 = fake
body = {'request_id': rid, 'account_id': account, 'gpu': 'L4', 'max_units': 20,
        'max_minutes': 60, 'job': {'type': 'image', 'prompt': 'red square'}}
svc._submit(owner, body)
with svc.db() as db:
    state = json.loads(db.execute('SELECT state FROM jobs WHERE id=?', (rid,)).fetchone()[0])
files = []
for path in sorted((root/'jobs'/rid).rglob('*')):
    if path.is_file():
        files.append({'path': path.relative_to(root/'jobs'/rid).as_posix(), 'size': path.stat().st_size,
                      'sha256': __import__('hashlib').sha256(path.read_bytes()).hexdigest()})
print(json.dumps({'state': state, 'files': files}, sort_keys=True, separators=(',', ':')))
'''
    script = tmp_path / 'probe.py'
    script.write_text('import sys\nsys_root = sys.argv[1]\n' + probe)
    outputs = []
    for tree, dest in ((Path(cs.ROOT), tmp_path / 'work-out'),):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(tree / 'webui') + os.pathsep + str(tree)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['CASTLE_COLAB_DB_NESTING'] = 'strict'
        result = subprocess.run(
            [sys.executable, '-B', str(script), str(dest)],
            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180,
        )
        assert result.returncode == 0, result.stderr[-500:]
        outputs.append(result.stdout.strip())
    assert [hashlib.sha256(text.encode('utf-8')).hexdigest() for text in outputs] == [R40V5_SUBMIT_PROBE_SHA256]


def test_rb1_duplicate_removes_staging(tmp_path, monkeypatch):
    svc, _clock, owner, account = service(tmp_path)
    body = image_body(account)
    first = svc._submit(owner, body)
    assert staging(svc.root) == []
    second = svc._submit(owner, body)
    assert second['id'] == first['id']
    assert staging(svc.root) == []
    with svc.db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1


def test_rb1_inner_duplicate_removes_staging(tmp_path, monkeypatch):
    svc, _clock, owner, account = service(tmp_path)
    body = image_body(account)
    job_id = body['request_id']
    request_hash = cs.digest(cs.encoded(body).encode())
    real = cs.private_write
    once = []

    def wrapped(path, data):
        if '.staging-' in str(path) and not once:
            once.append(1)
            with svc.db() as db:
                db.execute(
                    'INSERT INTO jobs VALUES (?,?,?,?,?)',
                    (job_id, owner, account, request_hash, cs.encoded({'id': job_id, 'phase': 'queued', 'account_id': account, 'gpu': 'L4'})),
                )
        return real(path, data)

    monkeypatch.setattr(cs, 'private_write', wrapped)
    result = svc._submit(owner, body)
    assert result['id'] == job_id
    assert staging(svc.root) == []
    assert not (svc.root / 'jobs' / job_id).exists()
    with svc.db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['staging', 'duplicate', 'claim', 'active', 'session'])
def test_rb1_failed_paths_leave_no_directory(tmp_path, monkeypatch, kind):
    svc, _clock, owner, account = service(tmp_path)
    body = image_body(account)
    job_id = body['request_id']
    real = cs.private_write
    once = []

    def wrapped(path, data):
        if '.staging-' in str(path) and not once:
            once.append(1)
            if kind == 'staging':
                raise RuntimeError('staging-boom')
            with svc.db() as db:
                if kind == 'duplicate':
                    db.execute(
                        'INSERT INTO jobs VALUES (?,?,?,?,?)',
                        (job_id, owner, account, 'different-hash', cs.encoded({'id': job_id, 'phase': 'queued', 'account_id': account, 'gpu': 'L4'})),
                    )
                elif kind == 'claim':
                    db.execute("UPDATE request_claims SET state='terminal' WHERE id=?", (job_id,))
                elif kind == 'active':
                    other = '44444444-4444-4444-8444-444444444444'
                    db.execute(
                        'INSERT INTO jobs VALUES (?,?,?,?,?)',
                        (other, owner, account, 'h', cs.encoded({'id': other, 'phase': 'queued', 'account_id': account, 'subject_key': SUBJECT, 'gpu': 'L4'})),
                    )
                else:
                    state = svc.sessions.new_state(owner, account, SUBJECT, {'idle_minutes': 20, 'hard_hours': 6, 'max_units': 120})
                    state.update(phase='ready', deadline_epoch=CLOCK + 1000)
                    db.execute('INSERT INTO work_sessions VALUES (?,?,?)', (state['id'], owner, cs.encoded(state)))
        return real(path, data)

    monkeypatch.setattr(cs, 'private_write', wrapped)
    with pytest.raises((RuntimeError, ColabError)):
        svc._submit(owner, body)
    assert staging(svc.root) == []
    assert not (svc.root / 'jobs' / job_id).exists()
    if kind == 'duplicate':
        assert job_row(svc, job_id) is not None
    else:
        assert job_row(svc, job_id) is None


def test_rb1_insert_failure_renames_directory(tmp_path):
    svc, _clock, owner, account = service(tmp_path)
    body = image_body(account)
    job_id = body['request_id']
    with svc.db() as db:
        db.execute("CREATE TRIGGER rb1_fail BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'rb1'); END")
    with pytest.raises(sqlite3.IntegrityError):
        svc._submit(owner, body)
    assert staging(svc.root) == []
    assert not (svc.root / 'jobs' / job_id).exists()
    assert job_row(svc, job_id) is None
    stale = [svc.root / 'jobs' / name for name in names(svc.root) if name.startswith('.stale-' + job_id)]
    assert len(stale) == 1 and (stale[0] / 'manifest.json').is_file()


def test_rb1_leftover_directory_is_renamed(tmp_path):
    svc, _clock, owner, account = service(tmp_path)
    body = image_body(account)
    job_id = body['request_id']
    leftover = svc.root / 'jobs' / job_id
    leftover.mkdir(parents=True)
    (leftover / 'marker.txt').write_text('keep')
    result = svc._submit(owner, body)
    assert result['id'] == job_id
    assert (svc.root / 'jobs' / job_id / 'manifest.json').is_file()
    assert not (svc.root / 'jobs' / job_id / 'marker.txt').exists()
    stale = list((svc.root / 'jobs').glob('.stale-' + job_id + '-*'))
    assert len(stale) == 1 and (stale[0] / 'marker.txt').read_text() == 'keep'
    assert staging(svc.root) == []
    assert job_row(svc, job_id)['phase'] == 'queued'


def test_rb1_concurrent_same_request(tmp_path):
    svc, _clock, owner, account = service(tmp_path)
    body = image_body(account)
    job_id = body['request_id']
    barrier = threading.Barrier(2)
    results, errors = [], []

    def run():
        try:
            barrier.wait(5)
            results.append(svc._submit(owner, dict(body)))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert sorted(item['id'] for item in results) == [job_id, job_id]
    assert staging(svc.root) == []
    assert (svc.root / 'jobs' / job_id).is_dir()
    assert not list((svc.root / 'jobs').glob('.stale-*'))
    with svc.db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1


def _parity(tmp_path):
    svc, _clock, _owner, _account = service(tmp_path)
    journal = Journal(tmp_path / 'parity' / 'journal.sqlite3', colab_state_path=svc.root / 'state.sqlite3')
    return svc, journal


def _watch_parity(path, stop, waits):
    while not stop.is_set():
        started = time.monotonic()
        connection = sqlite3.connect(path, timeout=8)
        try:
            connection.execute('BEGIN IMMEDIATE')
            waited = time.monotonic() - started
            connection.rollback()
            if waited >= 0.05:
                waits.append(waited)
        except sqlite3.OperationalError:
            waits.append(time.monotonic() - started)
        finally:
            connection.close()


def _record_sleep(bucket):
    def fake(seconds):
        bucket.append(seconds)
        threading.Event().wait(0.05)
    return fake


def _hold_colab(path, started, release, seconds):
    connection = sqlite3.connect(path, timeout=30)
    connection.execute('BEGIN EXCLUSIVE')
    started.set()
    release.wait(seconds)
    connection.rollback()
    connection.close()


def test_rb2_one_second_hold_succeeds_and_bounds_parity_lock(tmp_path, monkeypatch):
    _svc, journal = _parity(tmp_path)
    slept = []
    monkeypatch.setattr(journal_mod.time, 'sleep', lambda seconds: slept.append(seconds))
    started, release, stop = threading.Event(), threading.Event(), threading.Event()
    waits = []
    holder = threading.Thread(target=_hold_colab, args=(journal.colab_state_path, started, release, 1.0))
    watcher = threading.Thread(target=_watch_parity, args=(journal.path, stop, waits))
    holder.start()
    assert started.wait(2)
    watcher.start()
    begin = time.monotonic()
    try:
        job, created = journal.reserve('owner', '55555555-5555-4555-8555-555555555555', 'colab', 'acct', 'voice.demo', 'ident', {'unit': 'CU', 'amount': 1})
    finally:
        release.set()
        stop.set()
        holder.join(3)
        watcher.join(3)
    elapsed = time.monotonic() - begin
    assert created and job['state'] == 'reserved' and slept == []
    assert 0.7 <= elapsed <= 2.5
    assert waits and max(waits) <= 2.5


def test_rb2_hold_past_one_read_retries(tmp_path, monkeypatch):
    _svc, journal = _parity(tmp_path)
    slept = []
    monkeypatch.setattr(journal_mod.time, 'sleep', _record_sleep(slept))
    started, release, stop = threading.Event(), threading.Event(), threading.Event()
    waits = []
    holder = threading.Thread(target=_hold_colab, args=(journal.colab_state_path, started, release, 3.2))
    watcher = threading.Thread(target=_watch_parity, args=(journal.path, stop, waits))
    holder.start()
    assert started.wait(2)
    watcher.start()
    try:
        job, created = journal.reserve('owner', '66666666-6666-4666-8666-666666666666', 'colab', 'acct', 'voice.demo', 'ident', {'unit': 'CU', 'amount': 1})
    finally:
        release.set()
        stop.set()
        holder.join(5)
        watcher.join(4)
    assert created and job['state'] == 'reserved'
    assert slept == [0.5]
    assert waits and max(waits) <= 4.5


def test_rb2_ten_second_hold_is_busy(tmp_path, monkeypatch):
    from scripts.colab_remote.native_contracts import ParityError
    _svc, journal = _parity(tmp_path)
    slept = []
    monkeypatch.setattr(journal_mod.time, 'sleep', _record_sleep(slept))
    started, release, stop = threading.Event(), threading.Event(), threading.Event()
    waits = []
    holder = threading.Thread(target=_hold_colab, args=(journal.colab_state_path, started, release, 10))
    watcher = threading.Thread(target=_watch_parity, args=(journal.path, stop, waits))
    holder.start()
    assert started.wait(2)
    watcher.start()
    rid = '77777777-7777-4777-8777-777777777777'
    begin = time.monotonic()
    try:
        with pytest.raises(ParityError) as caught:
            journal.reserve('owner', rid, 'colab', 'acct', 'voice.demo', 'ident', {'unit': 'CU', 'amount': 1})
        elapsed = time.monotonic() - begin
        reader = sqlite3.connect(journal.colab_state_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.3)
        try:
            with pytest.raises(sqlite3.OperationalError):
                reader.execute('SELECT 1 FROM jobs')
        finally:
            reader.close()
    finally:
        release.set()
        stop.set()
        holder.join(3)
        watcher.join(4)
    assert caught.value.code == 'colab_state_busy'
    assert str(caught.value) == 'Colab 狀態正在更新，請稍後再試一次'
    assert slept == [0.5, 0.5]
    assert 5.0 <= elapsed <= 8.5
    assert journal.get('owner', rid) is None
    assert waits and max(waits) <= 4.5


def test_rb2_without_contention_matches_base_and_one_attempt(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(journal_mod.time, 'sleep', lambda seconds: slept.append(seconds))
    _svc, journal = _parity(tmp_path)
    monkeypatch.setattr(journal_mod.time, 'time', lambda: 1700000000.0)
    job, created = journal.reserve('owner', '88888888-8888-4888-8888-888888888888', 'colab', 'acct', 'voice.demo', 'ident', {'unit': 'CU', 'amount': 1})
    assert created and slept == []
    assert json.dumps(job, sort_keys=True, separators=(',', ':')) == R40V5_RESERVE_JOB


def test_rb2_states_outside_reserve_keep_defaults(tmp_path):
    svc, journal = _parity(tmp_path)
    assert states.__defaults__ is None or True
    import inspect
    assert inspect.signature(states).parameters['timeout'].default == 30
    assert inspect.signature(journal._session_capable).parameters['timeout'].default == 5.0
    started, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=_hold_colab, args=(svc.root / 'state.sqlite3', started, release, 0.4))
    holder.start()
    assert started.wait(2)
    begin = time.monotonic()
    try:
        assert states(svc.root / 'state.sqlite3', 'owner') == {}
        assert journal._session_capable() is True
    finally:
        release.set()
        holder.join(2)
    assert time.monotonic() - begin < 5


def _session_row(svc, owner, account, **changes):
    state = svc.sessions.new_state(owner, account, SUBJECT, {'idle_minutes': 20, 'hard_hours': 6, 'max_units': 120})
    fields = {'phase': 'ready', 'last_balance': 100, 'deadline_epoch': CLOCK + 10000, 'last_tick': CLOCK}
    fields.update(changes)
    state.update(fields)
    with svc.db() as db:
        db.execute('INSERT INTO work_sessions VALUES (?,?,?)', (state['id'], owner, cs.encoded(state)))
    return state


def test_rb3_rebind_during_observe_does_not_meter(tmp_path, caplog):
    svc, _clock, owner, account = service(tmp_path)
    state = _session_row(svc, owner, account)
    old = {'owner': owner, 'account_id': account, 'subject_key': SUBJECT, 'identity_at': 900, 'balance_at': 900, 'balance': 100}
    svc.sessions.save(state['id'], admission_verification=old)
    second = '99999999-9999-4999-8999-999999999999'

    def observed(current, op, **kwargs):
        svc.sessions.save(state['id'], account_id=second)
        return {'authenticated': True, 'subject_key': SUBJECT, 'balance_compute_units': 40}

    svc._call = observed
    with caplog.at_level(logging.WARNING, logger='colab_sessions'):
        returned = svc.sessions.observe(svc.sessions.get(state['id']))
    current = svc.sessions.get(state['id'])
    assert returned['account_id'] == second == current['account_id']
    assert current['admission_verification'] == old
    assert current['last_balance'] == 100 and current['units_observed'] == 0
    assert 'source' not in current['admission_verification']
    assert 'colab_observe_binding_changed session=%s' % state['id'] in caplog.text


class Client:
    def start_channels(self, *args, **kwargs):
        return None

    def execute(self, *args, **kwargs):
        return None

    def _on_open(self, *args, **kwargs):
        return None

    def _on_close(self, *args, **kwargs):
        return None


def test_rb4_fired_exception_becomes_timeout():
    budget = Budget(30)
    with pytest.raises(TimeoutError) as caught:
        with scoped(budget, Client):
            try:
                import signal
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
            except TimeoutError:
                raise ValueError('replaced')
    assert isinstance(caught.value.__cause__, ValueError)


def test_rb4_exception_before_alarm_is_unchanged():
    budget = Budget(30)
    with pytest.raises(ValueError, match='plain'):
        with scoped(budget, Client):
            raise ValueError('plain')


def test_rb4_timeout_itself_stays_timeout():
    import signal
    budget = Budget(30)
    with pytest.raises(TimeoutError) as caught:
        with scoped(budget, Client):
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    assert caught.value.__cause__ is None


def _busy(message):
    return sqlite3.OperationalError(message)


def test_rb5_one_busy_tick_stays(tmp_path, monkeypatch):
    svc, _clock, owner, account = service(tmp_path)
    state = _session_row(svc, owner, account)
    monkeypatch.setattr(svc.sessions, 'tick', lambda current: (_ for _ in ()).throw(_busy('database is locked')))
    svc.sessions.tick_all()
    current = svc.sessions.get(state['id'])
    assert current['phase'] == 'ready'
    assert current['fault']['code'] == 'store_busy'
    assert current['last_tick'] == state['last_tick']
    assert current['fault_streak'] == 1
    assert state['id'] in svc.sessions._recovery_anchors


@pytest.mark.parametrize('message', ['database is locked', 'database is busy'])
def test_rb5_second_busy_closes_on_streak(tmp_path, message):
    svc, _clock, owner, account = service(tmp_path)
    state = _session_row(svc, owner, account)
    svc.sessions.operation(state, 'observe')
    svc.sessions.record_fault(state, _busy(message))
    assert svc.sessions.get(state['id'])['phase'] == 'ready'
    svc.sessions.operation(state, 'observe')
    svc.sessions.record_fault(svc.sessions.get(state['id']), _busy(message))
    current = svc.sessions.get(state['id'])
    assert current['phase'] == 'closing' and current['reason'] == 'watchdog_state_lost'
    assert current['last_tick'] == state['last_tick']


def test_rb5_busy_past_span_heartbeat_and_deadline(tmp_path):
    svc, clock, owner, account = service(tmp_path)
    span = _session_row(svc, owner, account, fault_since=CLOCK - 90, fault_streak=0)
    svc.sessions.record_fault(span, _busy('database is locked'))
    assert svc.sessions.get(span['id'])['phase'] == 'closing'

    heartbeat = _session_row(svc, owner, account, last_tick=CLOCK - 300)
    svc.sessions.record_fault(heartbeat, _busy('database is locked'))
    assert svc.sessions.get(heartbeat['id'])['phase'] == 'closing'
    assert svc.sessions.get(heartbeat['id'])['reason'] == 'watchdog_state_lost'

    deadline = _session_row(svc, owner, account, deadline_epoch=CLOCK - 1)
    svc.sessions.record_fault(deadline, _busy('database is busy'))
    current = svc.sessions.get(deadline['id'])
    assert current['phase'] == 'closing' and current['reason'] == 'watchdog_state_lost'
    assert current['last_tick'] == deadline['last_tick']


def test_rb5_non_busy_operational_error_closes(tmp_path):
    svc, _clock, owner, account = service(tmp_path)
    state = _session_row(svc, owner, account)
    svc.sessions.operation(state, 'heartbeat')
    svc.sessions.record_fault(state, sqlite3.OperationalError('unable to open database file'))
    current = svc.sessions.get(state['id'])
    assert current['phase'] == 'closing' and current['reason'] == 'watchdog_state_lost'
    assert current['fault']['code'] == 'OperationalError'
    assert current['last_tick'] == state['last_tick']


def _job(root, name, text):
    directory = root / 'owner' / 'comfy_cloud' / 'jobs' / name
    directory.mkdir(parents=True)
    (directory / 'job.json').write_text(text)
    return directory


def test_rb6_bad_jobs_do_not_stop_the_poll(tmp_path, caplog):
    hosted_compute._HOSTED_TICK_LOGGED.clear()
    calls = []
    svc = hosted_compute.HostedComputeService(tmp_path / 'hosted', transport=object())
    _job(svc.root, 'badjson', '{')
    _job(svc.root, 'badkey', json.dumps({'state': 'queued'}))
    _job(svc.root, 'good', json.dumps({'state': 'queued'}))

    def poll(folder, path, job):
        calls.append(path.name)
        if path.name == 'badkey':
            raise KeyError('missing')

    svc._poll = poll
    with caplog.at_level(logging.WARNING, logger='hosted_compute'):
        svc.tick()
        svc.tick()
    assert calls.count('good') == 2 and calls.count('badkey') == 2
    assert 'badjson' not in calls
    assert svc.poll_failures == 2
    assert caplog.text.count('hosted_job_tick_failed job=badkey exception=KeyError') == 1


def test_rb6_tick_exception_keeps_the_thread(tmp_path, caplog, monkeypatch):
    hosted_compute._HOSTED_TICK_LOGGED.clear()
    svc = hosted_compute.HostedComputeService(tmp_path / 'hosted', transport=object())
    calls = []

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('iter')

    monkeypatch.setattr(svc, 'tick', tick)
    with caplog.at_level(logging.WARNING, logger='hosted_compute'):
        svc.start(interval=0.05)
        deadline = time.monotonic() + 3
        while len(calls) < 2 and time.monotonic() < deadline:
            svc._stop.wait(0.02)
        alive = svc._thread.is_alive()
        svc.stop()
        svc._thread.join(2)
    assert alive and len(calls) >= 2 and svc.poll_failures >= 1
    assert 'hosted_poller_iteration_failed exception=RuntimeError' in caplog.text


def test_rb6_safe_log_swallows_logger_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('log')

    monkeypatch.setattr(hosted_compute.logger, 'warning', boom)
    hosted_compute._safe_log('hosted_poller_iteration_failed exception=%s', 'RuntimeError')


def _vox():
    import orchestrator.tasks as tasks
    return tasks


def test_rb7_unusable_manifest(tmp_path, monkeypatch):
    tasks = _vox()
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(tasks, '_trusted_existing_path', lambda *args, **kwargs: Path('/bin/true'))
    monkeypatch.setattr(tasks, '_safe_output_dir', lambda *args, **kwargs: out)
    monkeypatch.setattr(tasks, 'gpu_queue_cmd', lambda inner, payload: ['true'])
    monkeypatch.setattr(tasks, '_cancelled', lambda path: False)
    payload = {'segments': [{'index': 1, 'text': 'hi'}], 'output_dir': str(out), 'manifest_name': 'manifest.json'}

    def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(tasks.subprocess, 'check_call', missing)
    with pytest.raises(RuntimeError, match='VoxCPM batch produced no usable manifest'):
        tasks.run_castle_voxcpm_tts_batch(payload, tmp_path / 'work-missing', tmp_path / 'cancel')

    def invalid(*args, **kwargs):
        (out / 'manifest.json').write_text('{', encoding='utf-8')

    monkeypatch.setattr(tasks.subprocess, 'check_call', invalid)
    with pytest.raises(RuntimeError, match='VoxCPM batch produced no usable manifest'):
        tasks.run_castle_voxcpm_tts_batch(payload, tmp_path / 'work-invalid', tmp_path / 'cancel')

    def empty(*args, **kwargs):
        (out / 'manifest.json').write_text('{"segments": []}', encoding='utf-8')

    monkeypatch.setattr(tasks.subprocess, 'check_call', empty)
    with pytest.raises(RuntimeError, match='VoxCPM batch produced no usable manifest'):
        tasks.run_castle_voxcpm_tts_batch(payload, tmp_path / 'work-empty', tmp_path / 'cancel')


def test_rb7_one_segment_matches_previous_artifacts(tmp_path, monkeypatch):
    tasks = _vox()
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(tasks, '_trusted_existing_path', lambda *args, **kwargs: Path('/bin/true'))
    monkeypatch.setattr(tasks, '_safe_output_dir', lambda *args, **kwargs: out)
    monkeypatch.setattr(tasks, 'gpu_queue_cmd', lambda inner, payload: ['true'])
    monkeypatch.setattr(tasks, '_cancelled', lambda path: False)

    def write(*args, **kwargs):
        (out / 'manifest.json').write_text(
            json.dumps({'segments': [{'index': 1, 'file': 'seg_01.wav', 'passed': True}]}),
            encoding='utf-8',
        )

    monkeypatch.setattr(tasks.subprocess, 'check_call', write)
    artifacts = tasks.run_castle_voxcpm_tts_batch(
        {'segments': [{'index': 1, 'text': 'hi'}], 'output_dir': str(out)},
        tmp_path / 'work',
        tmp_path / 'cancel',
    )
    assert artifacts == [
        {
            'path': str(out / 'seg_01.wav'),
            'name': 'seg_01.wav',
            'artifact_type': 'audio',
            'output_name': 'seg_01',
            'metadata': {'workflow': 'castle.voxcpm_tts_batch', 'file': 'seg_01.wav', 'index': 1, 'passed': True},
        },
        {
            'path': str(out / 'manifest.json'),
            'name': 'manifest.json',
            'artifact_type': 'json',
            'output_name': 'manifest',
            'metadata': {
                'workflow': 'castle.voxcpm_tts_batch',
                'manifest': 'manifest.json',
                'count': 1,
                'files': ['seg_01.wav'],
            },
        },
    ]


def test_point_monitor_pause_ignores_a_patched_shared_sleep(monkeypatch):
    """The monitor thread outlives the test that started it; its pause must not call time.sleep."""
    import types
    import compute_parity.lifecycle as lifecycle
    calls, ticked = [], threading.Event()
    monkeypatch.setattr(lifecycle.time, 'sleep', _record_sleep(calls))
    monkeypatch.setattr(lifecycle, 'tick', lambda console, journal: ticked.set())
    console = types.SimpleNamespace(test=lambda *args, **kwargs: None, owner='monitor-pause-owner')
    lifecycle.start_monitor(console, types.SimpleNamespace(path='monitor-pause-journal'))
    assert ticked.wait(2)
    threading.Event().wait(0.3)
    assert calls == []


def test_native_input_check_proves_containment_before_reading(tmp_path, monkeypatch):
    """Session inputs are read only after their resolved path is proven to stay inside the job directory."""
    import types
    import scripts.colab_session_inputs as inputs
    monkeypatch.setitem(sys.modules, 'native_schema', types.SimpleNamespace(validate_native_manifest=lambda manifest, job_dir, check_files=True: manifest))
    job = tmp_path / 'job'
    (job / 'input').mkdir(parents=True)
    data = b'\x89PNG\r\n\x1a\n' + b'0' * 24

    def manifest(name, payload=data):
        entry = {'path': name, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        return {'jobs': [{'bundle': {'inputs': [{'name': name, 'bytes': entry['size'], 'sha256': entry['sha256']}]}}], 'input_files': [entry]}

    (job / 'input' / 'a.png').write_bytes(data)
    assert inputs.validate_native_manifest(manifest('input/a.png'), job)['input_files'][0]['path'] == 'input/a.png'
    (tmp_path / 'outside.png').write_bytes(data)
    (job / 'input' / 'link.png').symlink_to(job / 'input' / 'a.png')
    (job / 'linked').symlink_to(job / 'input', target_is_directory=True)
    (job / 'loop.png').symlink_to(job / 'loop.png')
    (tmp_path / 'outdir').mkdir()
    (job / 'linked_out').symlink_to(tmp_path / 'outdir', target_is_directory=True)
    for name in ('../outside.png', str(tmp_path / 'outside.png'), 'input/link.png', 'linked/a.png', 'loop.png', 'linked_out/../input/a.png'):
        with pytest.raises(ValueError, match='Native input integrity failure'):
            inputs.validate_native_manifest(manifest(name), job)
    with pytest.raises(ValueError, match='Native input integrity failure'):
        inputs.validate_native_manifest(manifest('input/a.png', data[:-1] + b'1'), job)
    with pytest.raises(FileNotFoundError):
        inputs.validate_native_manifest(manifest('input/missing.png'), job)
