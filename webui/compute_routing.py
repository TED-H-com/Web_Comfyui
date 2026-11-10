"""Durable placement across existing execution lanes, without provider I/O.

Adapters own authorization, feature semantics, resource binding and execution.
This coordinator owns one immutable owner/request identity and one chosen lane.
It never retries an ambiguous dispatch or moves it to another provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping
import uuid

LANES = ('local', 'colab', 'autoip', 'comfy_cloud', 'runninghub')
TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'rejected'})
STATES = TERMINAL | {'reserved', 'dispatching', 'queued', 'running', 'collecting', 'cancelling', 'submission_uncertain'}
LIMIT_KEYS = frozenset({'max_credits', 'max_units', 'max_comfy_credits', 'max_runninghub_coins',
                        'max_minutes', 'max_concurrent', 'account_id', 'gpu'})
CURRENCIES = {'local': 'local', 'colab': 'compute_units', 'autoip': 'credits',
              'comfy_cloud': 'comfy_credits', 'runninghub': 'runninghub_coins'}
BUDGET_KEYS = {'colab': 'max_units', 'autoip': 'max_credits',
               'comfy_cloud': 'max_comfy_credits', 'runninghub': 'max_runninghub_coins'}


class ComputeRoutingError(ValueError):
    def __init__(self, message, *, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


class ComputeRejected(ComputeRoutingError):
    """An adapter proves rejection happened before any work was submitted.

    Only deliberately authored, public-safe messages belong in this exception.
    Ordinary exceptions after entering submit are always treated as uncertain.
    """
    def __init__(self, message='執行前檢查未通過', *, code='adapter_rejected', status=422):
        super().__init__(message, code=code, status=status)


class ComputeValidationError(ComputeRejected):
    pass


class ComputeUncertain(RuntimeError):
    """The adapter cannot prove whether execution was accepted."""


@dataclass(frozen=True)
class ComputeAdapter:
    # inspect must never allocate compute, submit work or spend credits. It may
    # consult existing readiness/quote snapshots and validate canonical inputs.
    inspect: Callable
    submit: Callable
    status: Callable | None = None
    cancel: Callable | None = None
    # Optional read-only lookup by the original request UUID after the caller
    # lost its submission receipt. None means unknown, never permission to retry.
    recover: Callable | None = None


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ComputeRoutingError('請求必須使用有效的 JSON 值') from error


def _owner(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 320:
        raise ComputeRoutingError('缺少登入身分', code='owner_required', status=401)
    return value


def _request_id(value):
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ComputeRoutingError('請求識別碼必須是 UUID') from error
    if value != normalized:
        raise ComputeRoutingError('請求識別碼必須使用標準 UUID 格式')
    return value


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _limits(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - LIMIT_KEYS:
        raise ComputeRoutingError('不支援的執行上限')
    result = dict(value)
    for key, amount in result.items():
        if key == 'account_id':
            if amount != '':
                _request_id(amount)
            continue
        if key == 'gpu':
            if not isinstance(amount, str) or amount not in {'L4', 'G4', 'A100', 'H100'}:
                raise ComputeRoutingError('不支援的 Colab GPU')
            continue
        if not _number(amount):
            raise ComputeRoutingError('執行上限必須是非負有限數值')
        if key in {'max_concurrent', 'max_minutes'} and (not isinstance(amount, int) or amount < 1):
            raise ComputeRoutingError('時間與同時執行上限必須是正整數')
    return result


def _public_receipt(value):
    """Return only the adapter contract, never arbitrary provider responses."""
    if not isinstance(value, Mapping) or value.get('state') not in STATES - {'reserved', 'dispatching'}:
        raise ComputeUncertain('invalid adapter receipt')
    result = {'state': value['state']}
    downstream = value.get('downstream_id')
    if downstream is not None:
        if not isinstance(downstream, str) or not downstream or len(downstream) > 256:
            raise ComputeUncertain('invalid downstream identity')
        result['downstream_id'] = downstream
    if value['state'] not in {'rejected', 'failed', 'submission_uncertain'} and not downstream:
        raise ComputeUncertain('accepted work has no durable downstream identity')
    for key in ('status_url', 'result_url'):
        url = value.get(key)
        if url is not None:
            if not isinstance(url, str) or not url.startswith('/api/') or len(url) > 1024 or '\\' in url or any(ord(c) < 32 for c in url):
                raise ComputeUncertain('invalid owner-scoped result URL')
            result[key] = url
    if isinstance(value.get('cancel_supported'), bool):
        result['cancel_supported'] = value['cancel_supported']
    if isinstance(value.get('result_ids'), list) and all(isinstance(x, str) and len(x) <= 256 for x in value['result_ids']):
        result['result_ids'] = list(value['result_ids'])[:100]
    for key in ('recipe_id', 'recipe_label', 'provider_id', 'output_kind'):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 160 and not any(ord(c) < 32 for c in item):
            result[key] = item
    if 'artifacts' in value:
        items = value['artifacts']
        if not isinstance(items, list) or len(items) > 100:
            raise ComputeUncertain('invalid artifacts')
        artifacts = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ComputeUncertain('invalid artifact')
            url = item.get('url')
            if not isinstance(url, str) or not url.startswith('/api/') or len(url) > 1024 or '\\' in url or any(ord(c) < 32 for c in url):
                raise ComputeUncertain('invalid artifact URL')
            artifact = {'url': url}
            for key in ('id', 'name', 'mime_type', 'sha256'):
                field = item.get(key)
                if isinstance(field, str) and len(field) <= 256 and not any(ord(c) < 32 for c in field):
                    if key == 'name' and ('/' in field or '\\' in field):
                        raise ComputeUncertain('artifact name contains path')
                    artifact[key] = field
            if _number(item.get('size')):
                artifact['size'] = item['size']
            artifacts.append(artifact)
        result['artifacts'] = artifacts
    return result


class ComputeRouter:
    def __init__(self, db_path, adapters: Mapping[str, ComputeAdapter], *, clock=time.time):
        if not isinstance(adapters, Mapping) or set(adapters) - set(LANES):
            raise ValueError('adapters must use a supported compute lane ID')
        if any(not isinstance(a, ComputeAdapter) or not callable(a.inspect) or not callable(a.submit) for a in adapters.values()):
            raise TypeError('each lane requires a ComputeAdapter')
        self.path, self.adapters, self.clock = Path(db_path), dict(adapters), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError('routing database cannot be a symlink')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS compute_requests (
                owner TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                placement TEXT NOT NULL, lane TEXT NOT NULL, state TEXT NOT NULL,
                request_json TEXT NOT NULL, limits_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY (owner, request_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS compute_requests_owner_history '
                       'ON compute_requests(owner, created DESC, request_id DESC)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _view(row):
        receipt = json.loads(row['receipt_json'])
        state = row['state']
        # A process may have died after claiming dispatch and before recording
        # its receipt. Reading it never makes that claim retryable.
        if state == 'dispatching':
            state = 'submission_uncertain'
        return {
            'request_id': row['request_id'], 'request_hash': row['request_hash'],
            'placement': row['placement'], 'lane': row['lane'],
            **receipt, 'state': state,
            'created_at': row['created'], 'updated_at': row['updated'],
            'retry_submission_allowed': False,
        }

    def _get(self, owner, request_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
        if row is None:
            raise ComputeRoutingError('找不到這個執行請求', code='not_found', status=404)
        return row

    @staticmethod
    def _input(owner, request, placement, limits):
        _owner(owner)
        if placement not in ('auto', *LANES):
            raise ComputeRoutingError('不支援的運算平台')
        if not isinstance(request, Mapping):
            raise ComputeRoutingError('缺少生成設定')
        request = json.loads(_json(dict(request)))
        return request, _limits(limits)

    def preview(self, owner, request, placement='auto', limits=None):
        request, limits = self._input(owner, request, placement, limits)
        with self._db() as db:
            active = db.execute('SELECT lane,state FROM compute_requests WHERE owner=?', (owner,)).fetchall()
        active = [row for row in active if row['state'] not in TERMINAL]
        rows = []
        for lane in LANES:
            reasons, snapshot = [], {}
            adapter = self.adapters.get(lane)
            if adapter is None:
                reasons.append('adapter_unavailable')
            else:
                try:
                    supplied = adapter.inspect(owner, json.loads(_json(request)), dict(limits))
                    if not isinstance(supplied, Mapping) or not isinstance(supplied.get('eligible'), bool):
                        raise ValueError('invalid capability snapshot')
                    snapshot = dict(supplied)
                    supplied_reasons = snapshot.get('reasons', [])
                    if not isinstance(supplied_reasons, (list, tuple)) or not all(isinstance(x, str) and x and len(x) <= 160 for x in supplied_reasons):
                        raise ValueError('invalid capability reasons')
                    reasons.extend(supplied_reasons)
                    if not snapshot['eligible'] and not reasons:
                        reasons.append('operation_unavailable')
                except Exception:
                    # Never echo account URLs, errors, payloads or credentials.
                    snapshot = {}
                    reasons.append('inspection_unavailable')
            queue = snapshot.get('queue_depth', 0)
            if not isinstance(queue, int) or isinstance(queue, bool) or queue < 0:
                queue = 0
                reasons.append('capacity_unknown')
            slots = snapshot.get('available_slots')
            if slots is not None and (not isinstance(slots, int) or isinstance(slots, bool) or slots < 1):
                reasons.append('capacity_exhausted')
            if limits.get('max_concurrent') is not None and len(active) >= limits['max_concurrent']:
                reasons.append('concurrency_limit')
            currency, amount = snapshot.get('currency'), snapshot.get('estimated_cost')
            budget_key = BUDGET_KEYS.get(lane)
            expected_currency = CURRENCIES[lane]
            if amount is not None and (not _number(amount) or currency != expected_currency):
                amount = None
                reasons.append('quote_invalid')
            if budget_key in limits:
                if amount is None:
                    reasons.append('quote_unavailable')
                elif amount > limits[budget_key]:
                    reasons.append('budget_exceeded')
            warnings = snapshot.get('warnings', [])
            if not isinstance(warnings, (list, tuple)) or len(warnings) > 10 or not all(isinstance(item, str) and len(item) <= 500 for item in warnings):
                warnings = []
                reasons.append('inspection_invalid')
            row = {
                'lane': lane, 'eligible': not reasons,
                'reasons': list(dict.fromkeys(reasons)), 'queue_depth': queue,
                'warnings': list(warnings),
                'available_slots': slots,
                'active_requests': sum(row['lane'] == lane for row in active),
                'estimated_cost': amount, 'currency': expected_currency,
                'auto_eligible': snapshot.get('auto_eligible', True) is True,
            }
            basis = snapshot.get('budget_basis')
            if basis in {'configured_estimate', 'provider_quote', 'usage_stop_threshold'}:
                row['budget_basis'] = basis
            rows.append(row)
        eligible = [row for row in rows if row['eligible'] and placement in ('auto', row['lane'])
                    and (placement != 'auto' or row['auto_eligible'])]
        # Compare queue pressure, then a stable platform preference. Monetary
        # values in unrelated provider currencies are never compared.
        eligible.sort(key=lambda row: (row['queue_depth'] + row['active_requests'], LANES.index(row['lane'])))
        return {'placement': placement, 'selected_lane': eligible[0]['lane'] if eligible else None,
                'candidates': rows, 'execution_started': False}

    def submit(self, owner, request_id, request, placement='auto', limits=None):
        request_id = _request_id(request_id)
        request, limits = self._input(owner, request, placement, limits)
        payload = _json({'request': request, 'placement': placement, 'limits': limits})
        request_hash = hashlib.sha256(payload.encode()).hexdigest()
        with self._db() as db:
            prior = db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
        if prior is not None:
            if prior['request_hash'] != request_hash:
                raise ComputeRoutingError('同一請求識別碼不能改用其他設定或平台', code='request_conflict', status=409)
            return self._view(prior)
        planned = self.preview(owner, request, placement, limits)
        lane = planned['selected_lane']
        if lane is None:
            raise ComputeRejected('沒有符合設定、容量與預算的運算平台', code='no_eligible_lane', status=409)
        now = self.clock()
        # BEGIN IMMEDIATE makes duplicate admission and global concurrency
        # checks atomic across threads/processes using this routing database.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if prior is not None:
                if prior['request_hash'] != request_hash:
                    raise ComputeRoutingError('同一請求識別碼不能改用其他設定或平台', code='request_conflict', status=409)
                return self._view(prior)
            if 'max_concurrent' in limits:
                states = db.execute('SELECT state FROM compute_requests WHERE owner=?', (owner,)).fetchall()
                if sum(row['state'] not in TERMINAL for row in states) >= limits['max_concurrent']:
                    raise ComputeRejected('已達同時執行上限', code='concurrency_limit', status=409)
            db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (owner, request_id, request_hash, placement, lane, 'dispatching',
                        _json(request), _json(limits), '{}', now, now))
        try:
            receipt = _public_receipt(self.adapters[lane].submit(owner, request_id, request, dict(limits)))
        except ComputeRejected as error:
            receipt = {'state': 'rejected', 'error': {'code': error.code, 'message': str(error)}}
        except BaseException as error:
            receipt = {'state': 'submission_uncertain', 'error': {
                'code': 'submission_uncertain', 'message': '送出結果尚未確認；請查詢原任務，勿改用其他平台重送。'}}
            self._save(owner, request_id, receipt)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            return self._view(self._get(owner, request_id))
        self._save(owner, request_id, receipt)
        return self._view(self._get(owner, request_id))

    def _save(self, owner, request_id, receipt, *, expected_state=None):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if row is None:
                raise ComputeRoutingError('找不到這個執行請求', code='not_found', status=404)
            # A stale status response must not erase a concurrently completed
            # or cancelled result, or replace an already bound task identity.
            if row['state'] in TERMINAL or (expected_state is not None and row['state'] != expected_state):
                return
            old = json.loads(row['receipt_json'])
            if old.get('downstream_id') and receipt.get('downstream_id') != old['downstream_id']:
                raise ComputeUncertain('downstream identity changed')
            if row['state'] in {'running', 'collecting', 'cancelling'} and receipt['state'] == 'queued':
                return
            merged = {**old, **receipt}
            if receipt['state'] != 'submission_uncertain' and 'error' not in receipt:
                merged.pop('error', None)
            db.execute('UPDATE compute_requests SET state=?,receipt_json=?,updated=? WHERE owner=? AND request_id=?',
                       (receipt['state'], _json(merged), self.clock(), owner, request_id))

    def status(self, owner, request_id, refresh=False):
        row = self._get(_owner(owner), _request_id(request_id))
        if refresh and row['state'] not in TERMINAL:
            receipt = json.loads(row['receipt_json'])
            adapter = self.adapters.get(row['lane'])
            if adapter and adapter.status and receipt.get('downstream_id'):
                try:
                    updated = _public_receipt(adapter.status(owner, receipt['downstream_id']))
                    self._save(owner, request_id, updated, expected_state=row['state'])
                except Exception:
                    pass  # Retain the last durable result; never re-submit.
            elif adapter and adapter.recover and not receipt.get('downstream_id'):
                try:
                    recovered = adapter.recover(owner, request_id)
                    if recovered is not None:
                        self._save(owner, request_id, _public_receipt(recovered), expected_state=row['state'])
                except Exception:
                    pass
        return self._view(self._get(owner, request_id))

    def list_jobs(self, owner, limit=50, before=None):
        _owner(owner)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ComputeRoutingError('工作清單筆數必須是 1–100')
        with self._db() as db:
            if before is None:
                rows = db.execute('SELECT * FROM compute_requests WHERE owner=? ORDER BY created DESC,request_id DESC LIMIT ?', (owner, limit)).fetchall()
            else:
                before = _request_id(before)
                anchor = db.execute('SELECT created FROM compute_requests WHERE owner=? AND request_id=?', (owner, before)).fetchone()
                if anchor is None:
                    raise ComputeRoutingError('找不到這個執行請求', code='not_found', status=404)
                rows = db.execute('''SELECT * FROM compute_requests WHERE owner=?
                    AND (created<? OR (created=? AND request_id<?))
                    ORDER BY created DESC,request_id DESC LIMIT ?''',
                    (owner, anchor['created'], anchor['created'], before, limit)).fetchall()
        return [self._view(row) for row in rows]

    def cancel(self, owner, request_id):
        row = self._get(_owner(owner), _request_id(request_id))
        if row['state'] in TERMINAL:
            return self._view(row)
        receipt = json.loads(row['receipt_json'])
        adapter = self.adapters.get(row['lane'])
        if not adapter or not adapter.cancel or not receipt.get('downstream_id'):
            raise ComputeRoutingError('目前無法確認可取消的下游任務；請保留原任務並查詢狀態', code='cancel_unavailable', status=409)
        try:
            updated = _public_receipt(adapter.cancel(owner, receipt['downstream_id']))
            self._save(owner, request_id, updated, expected_state=row['state'])
        except ComputeRejected:
            raise
        except Exception:
            raise ComputeRoutingError('取消結果尚未確認；原任務仍會保留', code='cancel_uncertain', status=503) from None
        return self._view(self._get(owner, request_id))
