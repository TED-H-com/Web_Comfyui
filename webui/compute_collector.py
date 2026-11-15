"""Collect existing routed receipts without a browser or dispatch authority."""
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time

from compute_execution import ExecutionContext
from compute_refresh import request_lock
from compute_routing import TERMINAL


LOG = logging.getLogger(__name__)
ENV = 'CASTLE_COMPUTE_RECEIPT_COLLECTOR'
HOSTED = frozenset({'colab', 'autoip', 'comfy_cloud', 'runninghub'})
SETTLEMENT_WINDOW = 6 * 60 * 60
SETTLEMENT_INTERVAL = 5 * 60
SETTLEMENT_LIMIT = 12


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in {'0', 'false', 'no', 'off'}


class ReceiptCollector:
    interval = 15
    batch_size = 20

    def __init__(self, server, path, *, clock=time.monotonic, wall_clock=time.time):
        self.s, self.path, self.clock = server, Path(path), clock
        self.wall_clock = wall_clock
        self._cursor = None
        self._schedule = {}
        # Never prune/reset this budget when updated changes or backoff expires.
        # It lasts for this web process, independently of owner-driven GETs.
        self._settlement_attempts = {}
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def router(self, owner):
        # Exactly the web adapter assembly, including parity registration and
        # current role resolution. No session, synthetic admin, or stored key.
        from compute_web import ComputeWeb
        return ComputeWeb(ExecutionContext(self.s, owner), self.s).router

    def _rows(self):
        if not self.path.exists():
            return []  # Start even before the first routed request is created.
        terminal = tuple(sorted(TERMINAL))
        hosted = tuple(sorted(HOSTED))
        sql = f'''SELECT owner, request_id, lane, state FROM compute_requests
            WHERE (state NOT IN ({','.join('?' for _ in terminal)})
                OR (state='completed' AND lane IN ({','.join('?' for _ in hosted)})
                    AND json_extract(receipt_json, '$.actual_cost') IS NULL
                    AND updated > ?))'''
        args = (*terminal, *hosted, self.wall_clock() - SETTLEMENT_WINDOW)
        # Keyset rotation is independent of updated timestamps: a stuck or
        # backoff row cannot starve requests beyond the first batch.
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            def page(cursor):
                suffix = ' AND (owner, request_id) > (?, ?)' if cursor else ''
                return db.execute(sql + suffix + ' ORDER BY owner, request_id LIMIT ?',
                                  (*args, *(cursor or ()), self.batch_size)).fetchall()
            rows = page(self._cursor)
            if not rows and self._cursor is not None:
                rows = page(None)
        self._cursor = (rows[-1]['owner'], rows[-1]['request_id']) if rows else None
        return rows

    def tick(self):
        counts = dict(scanned=0, refreshed=0, deferred=0, busy=0, unknown_owner=0, errors=0)
        if not enabled():
            return counts
        now = self.clock()
        # Forget inactive bookkeeping, including rows settled by an HTTP GET.
        self._schedule = {key: value for key, value in self._schedule.items() if value[0] + 600 >= now}
        for row in self._rows():
            counts['scanned'] += 1
            owner, rid = row['owner'], row['request_id']
            key = (owner, rid)
            settlement = row['state'] == 'completed'
            if settlement and self._settlement_attempts.get(key, 0) >= SETTLEMENT_LIMIT:
                counts['deferred'] += 1
                continue
            due, failures = self._schedule.get(key, (0, 0))
            if self.clock() < due:
                counts['deferred'] += 1
                continue
            lock = request_lock(self.path, owner, rid)
            if not lock.acquire(blocking=False):
                counts['busy'] += 1
                continue
            failed = False
            try:
                try:
                    # Resolve first, so an unknown owner never initializes any
                    # adapters or provider runtime. Rechecked by ComputeWeb.
                    ExecutionContext(self.s, owner)
                except PermissionError:
                    counts['unknown_owner'] += 1
                    failed = True
                    continue
                router = self.router(owner)
                # GET may complete/settle a row after our scan. Recheck under
                # the same lock before spending any settlement poll budget.
                current = router.status(owner, rid, refresh=False)
                settlement = (current['state'] == 'completed' and current['lane'] in HOSTED
                              and current.get('actual_cost') is None)
                if current['state'] in TERMINAL and not settlement:
                    continue
                if settlement:
                    if (current['updated_at'] <= self.wall_clock() - SETTLEMENT_WINDOW
                            or self._settlement_attempts.get(key, 0) >= SETTLEMENT_LIMIT):
                        counts['deferred'] += 1
                        continue
                    self._settlement_attempts[key] = self._settlement_attempts.get(key, 0) + 1
                errors = router.refresh_errors
                result = router.status(owner, rid, refresh=True)
                counts['refreshed'] += 1
                failed = router.refresh_errors != errors
                if failed:
                    counts['errors'] += 1
                # A completion discovered by ordinary status polling starts
                # the slower settlement cadence immediately.
                settlement = settlement or (result['state'] == 'completed'
                    and result['lane'] in HOSTED and result.get('actual_cost') is None)
            except Exception:
                failed = True
                counts['errors'] += 1
            finally:
                cadence = SETTLEMENT_INTERVAL if settlement else (60 if row['lane'] in HOSTED else self.interval)
                failures = min(failures + 1, 7) if failed else 0
                delay = min(600, cadence * 2 ** failures) if failed else cadence
                self._schedule[key] = (self.clock() + delay, failures)
                lock.release()
        return counts

    def start(self):
        if not enabled():
            return
        with self._start_lock:
            if not self._thread or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name='compute-receipts', daemon=True)
                self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                counts = self.tick()
                if counts['scanned']:
                    LOG.info('receipt_collector scanned=%d refreshed=%d deferred=%d busy=%d unknown_owner=%d errors=%d',
                             *(counts[key] for key in ('scanned', 'refreshed', 'deferred', 'busy', 'unknown_owner', 'errors')))
            except Exception:
                # Never print exception text/tracebacks: adapters can include
                # credential-bearing URLs. A failed scan cannot kill the loop.
                LOG.warning('receipt_collector tick_errors=%d', 1)
            self._stop.wait(self.interval)
