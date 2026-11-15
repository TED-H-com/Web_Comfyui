"""Independent, bounded recovery for public installations under the controller's flock.

Two durable round-robin cursors give assigned leases priority without starving
unresolved allocation lookups. No allocations or job POSTs are permitted.
"""
from contextlib import contextmanager
import fcntl
import os
import secrets
from pathlib import Path
import signal
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'webui'))
from colab_service import Bridge, BridgeError, ColabService, encoded, private_write
from colab_sessions import HEARTBEAT

LEASE_BUDGET_SECONDS = 45
RPC_BUDGET_SECONDS = 10
MAX_ATTEMPTS = 2  # <=90 seconds, leaving headroom under systemd's 240s limit.


class LeaseBudgetExpired(BaseException):
    pass


@contextmanager
def budget(seconds, error):
    """Interrupt blocking SQLite/bridge I/O in this oneshot's main thread.

    subprocess.run kills and waits for its bridge child when interrupted. Nested
    RPC limits preserve the enclosing lease's remaining absolute time budget.
    """
    started=time.monotonic()
    handler=signal.getsignal(signal.SIGALRM)
    previous=signal.getitimer(signal.ITIMER_REAL)
    def expired(*_):raise error
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,min(seconds,previous[0]) if previous[0] else seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,handler)
        if previous[0]:signal.setitimer(signal.ITIMER_REAL,max(.000001,previous[0]-(time.monotonic()-started)),previous[1])


def sweep(root, bridge=None, clock=None):
    root=Path(root)
    if not (root/'state.sqlite3').is_file():return False
    with (root/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        try:
            raw=bridge or Bridge(os.environ.get('WEB_COMFYUI_COLAB_PYTHON',''))
            def release_only(op,account_dir,**kw):
                if op not in {'runtime_lookup','runtime_stop','account_status'}:
                    raise ValueError('Lease sweeper cannot allocate or execute')
                with budget(RPC_BUDGET_SECONDS,BridgeError('timeout',op=='runtime_stop')):
                    return raw(op,account_dir,**kw)
            options={'clock':clock} if clock is not None else {}
            service=ColabService(root,release_only,**options)
            groups={'assigned':[], 'lookup':[]}
            now=service.clock()
            for row in service.sessions.rows():
                unresolved_closed=service.sessions.closed_needs_reconciliation(row)
                if row['phase']=='closed' and not unresolved_closed:continue
                if (row.get('runtime_id') or row.get('allocation_id')) and (
                        unresolved_closed or not row.get('watchdog_armed') or now>=row.get('deadline_epoch',0)
                        or now-row['last_tick']>HEARTBEAT
                        or row['phase'] in {'closing','stopping','released_unsettled'}):
                    groups['assigned' if row.get('runtime_id') else 'lookup'].append(row['id'])
            with service.db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS session_sweep_cursor (kind TEXT PRIMARY KEY, last_id TEXT NOT NULL)')
                cursors=dict(db.execute('SELECT kind,last_id FROM session_sweep_cursor'))
            # Assigned runtimes get the first slots; reserve half the slots for
            # unresolved lookups when both groups exist. For a stable N-lease group,
            # every lease is attempted in <=N successful timer activations.
            limit=MAX_ATTEMPTS//2 if all(groups.values()) else MAX_ATTEMPTS
            failed=False
            for kind,ids in groups.items():
                cursor=cursors.get(kind,'')
                ordered=sorted(ids)
                ordered=[sid for sid in ordered if sid>cursor]+[sid for sid in ordered if sid<=cursor]
                for sid in ordered[:limit]:
                    try:
                        with budget(LEASE_BUDGET_SECONDS,LeaseBudgetExpired()):
                            # Commit BEFORE provider work. Even SIGKILL/timeout after
                            # this point cannot repeatedly favour the same lease.
                            with service.db() as db:
                                db.execute('INSERT OR REPLACE INTO session_sweep_cursor VALUES (?,?)',(kind,sid))
                            service.sessions.sweep_leases(only=sid)
                    except (Exception,LeaseBudgetExpired):failed=True
            if failed:raise RuntimeError('Lease cleanup pending; retry on next timer')
            return True
        finally:
            try:
                private_write(root/'sweeper-last-run.json', encoded({'finished_at': (clock or time.time)(), 'run_id': secrets.token_hex(16)}).encode())
            except OSError:
                pass


if __name__=='__main__':
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share').expanduser()
    try:sweep(Path(os.environ.get('CASTLE_COLAB_ROOT', data_home / 'web-comfyui/colab')))
    except Exception:
        print('Colab lease cleanup pending',file=sys.stderr)
        raise SystemExit(1) from None
