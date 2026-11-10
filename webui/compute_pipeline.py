"""Durable owner-scoped DAGs over ComputeRouter; no provider or filesystem I/O.

The host supplies a fresh owner-authorized router for each worker tick and an
artifact resolver which verifies/copies an owned output into an owned input.
Neither callback may retain a mutable HTTP handler across users. This module
never accepts dependency URLs/paths, executes workflows, or retries ambiguity.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Mapping
import uuid

try:
    from compute_routing import (ComputeRejected, ComputeRouter, ComputeRoutingError,
        LANES, TERMINAL, _json, _limits, _owner, _public_receipt, _request_id)
except ModuleNotFoundError:
    from webui.compute_routing import (ComputeRejected, ComputeRouter, ComputeRoutingError,
        LANES, TERMINAL, _json, _limits, _owner, _public_receipt, _request_id)

_ID = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')
_ACTIVE = {'dispatching', 'queued', 'running', 'collecting', 'cancelling', 'submission_uncertain'}


class PipelineError(ComputeRoutingError):
    pass


def _fail(message, code='pipeline_invalid', status=400):
    raise PipelineError(message, code=code, status=status)


def _id(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        _fail('步驟識別碼格式不正確')
    return value


def step_request_id(pipeline_id, step_id, attempt=1):
    _request_id(pipeline_id)
    _id(step_id)
    if type(attempt) is not int or attempt < 1:
        _fail('步驟重試次數不正確')
    return str(uuid.uuid5(uuid.UUID(pipeline_id), f'compute-step:{step_id}:attempt:{attempt}'))


def validate_plan(plan):
    if not isinstance(plan, Mapping) or set(plan) != {'version', 'steps'} or type(plan.get('version')) is not int or plan['version'] != 1:
        _fail('生產流程版本或欄位不正確')
    steps = plan['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
        _fail('生產流程需包含 1–64 個步驟')
    result, ids = [], set()
    for item in steps:
        if not isinstance(item, Mapping) or set(item) - {'id', 'request', 'placement', 'limits', 'depends_on', 'bindings'}:
            _fail('生產步驟欄位不正確')
        ident = _id(item.get('id'))
        if ident in ids:
            _fail('步驟識別碼不可重複')
        ids.add(ident)
        request = item.get('request')
        if not isinstance(request, Mapping) or not isinstance(request.get('featureId'), str) or not request['featureId']:
            _fail('步驟缺少生成工具')
        request = json.loads(_json(dict(request)))
        files = request.get('files') or {}
        if not isinstance(files, dict):
            _fail('步驟素材欄位必須是物件')
        placement = item.get('placement', 'auto')
        if placement not in ('auto', *LANES):
            _fail('步驟使用不支援的運算平台')
        dependencies = item.get('depends_on', [])
        if not isinstance(dependencies, list) or len(dependencies) != len(set(map(str, dependencies))):
            _fail('步驟依賴清單不正確')
        dependencies = [_id(value) for value in dependencies]
        bindings = item.get('bindings', {})
        if not isinstance(bindings, Mapping) or len(bindings) > 16:
            _fail('步驟素材綁定不正確')
        clean_bindings = {}
        for slot, binding in bindings.items():
            _id(slot)
            if slot in files:
                _fail('同一素材欄位不能同時指定上傳與前一步成果')
            if not isinstance(binding, Mapping) or set(binding) != {'step_id', 'artifact_index'}:
                _fail('成果依賴只能指定步驟與成果索引；不能提供網址或路徑')
            source = _id(binding['step_id'])
            index = binding['artifact_index']
            if type(index) is not int or not 0 <= index < 100:
                _fail('成果索引需為 0–99')
            clean_bindings[slot] = {'step_id': source, 'artifact_index': index}
            if source not in dependencies:
                dependencies.append(source)
        result.append({'id': ident, 'request': request, 'placement': placement,
                       'limits': _limits(item.get('limits')), 'depends_on': dependencies,
                       'bindings': clean_bindings})
    by_id = {step['id']: step for step in result}
    visited, pending = set(), set()
    def visit(ident):
        if ident in pending:
            _fail('生產流程包含循環依賴', 'pipeline_cycle')
        if ident in visited:
            return
        pending.add(ident)
        for source in by_id[ident]['depends_on']:
            if source not in by_id:
                _fail('步驟依賴不存在', 'pipeline_dependency_missing')
            visit(source)
        pending.remove(ident)
        visited.add(ident)
    for ident in by_id:
        visit(ident)
    return {'version': 1, 'steps': result}


def _binding(value):
    """Only the trusted resolver can produce an owned input receipt."""
    if not isinstance(value, Mapping) or set(value) != {'file', 'sha256', 'artifact_id'}:
        _fail('成果解析器未提供完整的素材證明', 'artifact_unverified')
    file = value['file']
    if not isinstance(file, Mapping) or set(file) - {'serverFile', 'promptId', 'prompt_id', 'name', 'type', 'mimeType', 'size'}:
        _fail('成果解析器不能回傳任意網址或路徑', 'artifact_unverified')
    name = file.get('serverFile')
    if (not isinstance(name, str) or not name or name in {'.', '..'} or len(name) > 255
            or '/' in name or '\\' in name or ':' in name or any(ord(c) < 32 for c in name)):
        _fail('成果必須轉成已授權的輸入檔案', 'artifact_unverified')
    if not isinstance(value['sha256'], str) or not _SHA.fullmatch(value['sha256']):
        _fail('成果缺少有效的內容雜湊', 'artifact_unverified')
    artifact_id = value['artifact_id']
    if not isinstance(artifact_id, str) or not artifact_id or len(artifact_id) > 256 or any(ord(c) < 32 for c in artifact_id):
        _fail('成果缺少有效的素材識別碼', 'artifact_unverified')
    return json.loads(_json(dict(value)))


class ComputePipeline:
    def __init__(self, db_path, *, router_factory, artifact_resolver, clock=time.time, lease_seconds=120):
        if not callable(router_factory) or not callable(artifact_resolver):
            raise TypeError('owner router factory and verified artifact resolver are required')
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 600:
            raise ValueError('lease_seconds must be 5–600')
        self.path, self.router_factory, self.artifact_resolver = Path(db_path), router_factory, artifact_resolver
        self.clock, self.lease_seconds = clock, lease_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS compute_pipelines (
                  owner TEXT NOT NULL, pipeline_id TEXT NOT NULL, plan_hash TEXT NOT NULL, plan_json TEXT NOT NULL,
                  cancel_requested INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
                  lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0,
                  PRIMARY KEY(owner,pipeline_id));
                CREATE TABLE IF NOT EXISTS compute_pipeline_attempts (
                  owner TEXT NOT NULL, pipeline_id TEXT NOT NULL, step_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                  request_id TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT, dependencies_json TEXT NOT NULL,
                  receipt_json TEXT NOT NULL, error_code TEXT, updated REAL NOT NULL,
                  PRIMARY KEY(owner,pipeline_id,step_id,attempt));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _rows(self, db, owner, ident):
        parent = db.execute('SELECT * FROM compute_pipelines WHERE owner=? AND pipeline_id=?', (owner, ident)).fetchone()
        if parent is None:
            _fail('找不到這個生產流程', 'pipeline_not_found', 404)
        attempts = db.execute('SELECT * FROM compute_pipeline_attempts WHERE owner=? AND pipeline_id=? ORDER BY attempt', (owner, ident)).fetchall()
        latest = {row['step_id']: row for row in attempts}
        return parent, latest, attempts

    def create(self, owner, pipeline_id, plan):
        _owner(owner); _request_id(pipeline_id)
        plan = validate_plan(plan)
        encoded = _json(plan)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT plan_hash FROM compute_pipelines WHERE owner=? AND pipeline_id=?', (owner, pipeline_id)).fetchone()
            if existing:
                if existing['plan_hash'] != digest:
                    _fail('同一生產流程識別碼不能更改內容', 'pipeline_conflict', 409)
            else:
                db.execute('INSERT INTO compute_pipelines VALUES(?,?,?,?,0,?,?,NULL,0)', (owner, pipeline_id, digest, encoded, now, now))
                for step in plan['steps']:
                    db.execute('INSERT INTO compute_pipeline_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (owner, pipeline_id, step['id'], 1, step_request_id(pipeline_id, step['id']), 'pending', None, '{}', '{}', None, now))
        return self.status(owner, pipeline_id)

    @staticmethod
    def _view(parent, latest, attempts):
        plan = json.loads(parent['plan_json'])
        rows, artifacts = [], []
        for index, spec in enumerate(plan['steps']):
            row = latest[spec['id']]
            state = row['state']
            if state == 'dispatching':
                state = 'submission_uncertain'
            if state == 'pending' and any(latest[dep]['state'] in {'failed', 'rejected', 'cancelled'} for dep in spec['depends_on']):
                state = 'blocked'
            receipt = json.loads(row['receipt_json'])
            dependencies = json.loads(row['dependencies_json'])
            item = {'id': spec['id'], 'index': index, 'attempt': row['attempt'], 'request_id': row['request_id'],
                    'placement': spec['placement'], 'state': state, 'depends_on': spec['depends_on'],
                    'dependency_hashes': {slot: value['sha256'] for slot, value in dependencies.items()},
                    'artifacts': receipt.get('artifacts', []), 'updated_at': row['updated'],
                    'retry_allowed': row['state'] in {'failed', 'rejected'}}
            if receipt:
                item['job'] = receipt
            if row['error_code']:
                item['error_code'] = row['error_code']
            item['attempts'] = [{'attempt': old['attempt'], 'request_id': old['request_id'], 'state': old['state']}
                                for old in attempts if old['step_id'] == spec['id']]
            rows.append(item)
            if state == 'completed':
                artifacts.extend({'step_id': spec['id'], **artifact} for artifact in item['artifacts'])
        states = {row['state'] for row in rows}
        if states == {'completed'}:
            state = 'completed'
        elif parent['cancel_requested']:
            state = 'cancelled' if states <= TERMINAL | {'blocked'} else 'cancelling'
        elif 'submission_uncertain' in states:
            state = 'submission_uncertain'
        elif states & {'preparing', 'queued', 'running', 'collecting', 'cancelling'}:
            state = 'running'
        elif states & {'failed', 'rejected', 'cancelled', 'blocked'}:
            state = 'blocked'
        else:
            state = 'queued'
        return {'pipeline_id': parent['pipeline_id'], 'plan_hash': parent['plan_hash'], 'state': state,
                'created_at': parent['created'], 'updated_at': parent['updated'],
                'cancel_requested': bool(parent['cancel_requested']), 'steps': rows, 'artifacts': artifacts}

    def status(self, owner, pipeline_id):
        _owner(owner); _request_id(pipeline_id)
        with self._db() as db:
            return self._view(*self._rows(db, owner, pipeline_id))

    def list_jobs(self, owner, limit=50):
        _owner(owner)
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail('流程清單筆數需為 1–100')
        with self._db() as db:
            ids = [row[0] for row in db.execute('SELECT pipeline_id FROM compute_pipelines WHERE owner=? ORDER BY created DESC LIMIT ?', (owner, limit))]
        return [self.status(owner, ident) for ident in ids]

    def work_items(self, limit=50):
        """Internal worker inventory; do not expose owner IDs as a public API."""
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail('工作清單筆數需為 1–100')
        with self._db() as db:
            rows = db.execute('SELECT owner,pipeline_id FROM compute_pipelines ORDER BY updated ASC').fetchall()
        result = []
        for row in rows:
            status = self.status(row['owner'], row['pipeline_id'])
            if status['state'] not in {'completed', 'cancelled', 'blocked'}:
                result.append(dict(row))
                if len(result) >= limit:
                    break
        return result

    def _lease(self, owner, ident):
        token, now = str(uuid.uuid4()), self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            parent, _, _ = self._rows(db, owner, ident)
            if parent['lease_token'] and parent['lease_until'] > now:
                return None
            db.execute('UPDATE compute_pipelines SET lease_token=?,lease_until=? WHERE owner=? AND pipeline_id=?',
                       (token, now + self.lease_seconds, owner, ident))
        return token

    def _write(self, owner, ident, token, step_id, attempt, *, state=None, request=None, dependencies=None, receipt=None, error=None, before_dispatch=False):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            parent, latest, _ = self._rows(db, owner, ident)
            if parent['lease_token'] != token or latest[step_id]['attempt'] != attempt:
                return False
            if before_dispatch and parent['cancel_requested']:
                return False
            row = latest[step_id]
            if row['state'] in TERMINAL:
                return False
            db.execute('''UPDATE compute_pipeline_attempts SET state=?,request_json=?,dependencies_json=?,receipt_json=?,error_code=?,updated=?
                          WHERE owner=? AND pipeline_id=? AND step_id=? AND attempt=?''',
                       (state or row['state'], _json(request) if request is not None else row['request_json'],
                        _json(dependencies) if dependencies is not None else row['dependencies_json'],
                        _json(receipt) if receipt is not None else row['receipt_json'], error, self.clock(),
                        owner, ident, step_id, attempt))
            db.execute('UPDATE compute_pipelines SET updated=?,lease_until=? WHERE owner=? AND pipeline_id=?',
                       (self.clock(), self.clock() + self.lease_seconds, owner, ident))
        return True

    @staticmethod
    def _router_receipt(receipt, request_id):
        if not isinstance(receipt, Mapping) or receipt.get('request_id') != request_id or receipt.get('lane') not in LANES:
            _fail('步驟回應不符合原請求身分', 'step_receipt_invalid')
        result = {**_public_receipt(receipt), 'request_id': request_id, 'lane': receipt['lane']}
        # A compound production promises tangible output at every stage. A
        # completed transport with no verified artifact cannot finish a parent.
        if result['state'] == 'completed' and not result.get('artifacts'):
            result['state'] = 'collecting'
        return result

    def tick(self, owner, pipeline_id, max_steps=4):
        """Bounded worker progress; no retries after a dispatch claim.

        Expired preparation claims can repeat owned-input validation. Once a
        step is dispatching, recovery only queries its stable router UUID. A
        crash before router admission remains uncertain until proven otherwise.
        """
        _owner(owner); _request_id(pipeline_id)
        if type(max_steps) is not int or not 1 <= max_steps <= 64:
            _fail('單次處理步驟數需為 1–64')
        token = self._lease(owner, pipeline_id)
        if token is None:
            return self.status(owner, pipeline_id)
        try:
            router = self.router_factory(owner)
            if not isinstance(router, ComputeRouter):
                raise TypeError('router factory must return ComputeRouter')
            visited = set()
            for _ in range(max_steps):
                with self._db() as db:
                    parent, latest, _ = self._rows(db, owner, pipeline_id)
                specs = {step['id']: step for step in json.loads(parent['plan_json'])['steps']}
                candidates = [row for row in latest.values() if row['step_id'] not in visited and (
                    row['state'] in _ACTIVE or row['state'] == 'preparing' or
                    row['state'] == 'pending' and (parent['cancel_requested'] or all(latest[dep]['state'] == 'completed' for dep in specs[row['step_id']]['depends_on'])))]
                if not candidates:
                    break
                row = min(candidates, key=lambda value: (value['updated'], list(specs).index(value['step_id'])))
                step_id, attempt, rid = row['step_id'], row['attempt'], row['request_id']
                visited.add(step_id)
                if parent['cancel_requested'] and row['state'] in {'pending', 'preparing'}:
                    self._write(owner, pipeline_id, token, step_id, attempt, state='cancelled')
                    continue
                if row['state'] in _ACTIVE:
                    try:
                        receipt = router.status(owner, rid, refresh=True)
                        if parent['cancel_requested'] and receipt['state'] not in TERMINAL:
                            try:
                                receipt = router.cancel(owner, rid)
                            except ComputeRoutingError:
                                pass
                        receipt = self._router_receipt(receipt, rid)
                        self._write(owner, pipeline_id, token, step_id, attempt, state=receipt['state'], receipt=receipt)
                    except Exception:
                        self._write(owner, pipeline_id, token, step_id, attempt,
                                    state='submission_uncertain' if row['state'] == 'dispatching' else row['state'], error='step_status_unavailable')
                    continue
                spec = specs[step_id]
                if not self._write(owner, pipeline_id, token, step_id, attempt, state='preparing', before_dispatch=True):
                    continue
                try:
                    request = json.loads(_json(spec['request']))
                    request['files'] = dict(request.get('files') or {})
                    dependencies = {}
                    for slot, binding in spec['bindings'].items():
                        upstream = latest[binding['step_id']]
                        upstream_receipt = json.loads(upstream['receipt_json'])
                        artifacts = upstream_receipt.get('artifacts') or []
                        if upstream['state'] != 'completed' or binding['artifact_index'] >= len(artifacts):
                            _fail('前一步沒有可驗證的指定成果', 'artifact_missing')
                        resolved = _binding(self.artifact_resolver(owner, upstream_receipt, binding['artifact_index']))
                        request.setdefault('files', {})[slot] = resolved['file']
                        dependencies[slot] = {key: resolved[key] for key in ('sha256', 'artifact_id')}
                    expected_dependencies = json.loads(row['dependencies_json'])
                    if expected_dependencies and dependencies != expected_dependencies:
                        _fail('重試素材與原先綁定的成果不同', 'dependency_changed')
                    if not self._write(owner, pipeline_id, token, step_id, attempt, state='dispatching',
                                       request=request, dependencies=dependencies, before_dispatch=True):
                        continue
                except Exception as error:
                    code = error.code if isinstance(error, ComputeRoutingError) else 'artifact_resolution_failed'
                    self._write(owner, pipeline_id, token, step_id, attempt, state='rejected', error=code)
                    continue
                try:
                    receipt = router.submit(owner, rid, request, spec['placement'], spec['limits'])
                    receipt = self._router_receipt(receipt, rid)
                    self._write(owner, pipeline_id, token, step_id, attempt, state=receipt['state'], receipt=receipt)
                except ComputeRejected as error:
                    # Router proves this failure happened before dispatch.
                    self._write(owner, pipeline_id, token, step_id, attempt, state='rejected', error=error.code)
                except Exception:
                    self._write(owner, pipeline_id, token, step_id, attempt, state='submission_uncertain', error='step_submission_uncertain')
        finally:
            with self._db() as db:
                db.execute('UPDATE compute_pipelines SET lease_token=NULL,lease_until=0 WHERE owner=? AND pipeline_id=? AND lease_token=?',
                           (owner, pipeline_id, token))
        return self.status(owner, pipeline_id)

    def resume(self, owner, pipeline_id):
        # Resume polls existing identities and dispatches only never-submitted
        # pending work. Failed/rejected attempts require an explicit retry.
        if self.status(owner, pipeline_id)['cancel_requested']:
            _fail('已取消的流程不能恢復提交', 'pipeline_cancelled', 409)
        return self.tick(owner, pipeline_id)

    def retry(self, owner, pipeline_id, step_id):
        _owner(owner); _request_id(pipeline_id); _id(step_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            parent, latest, _ = self._rows(db, owner, pipeline_id)
            row = latest.get(step_id)
            if parent['cancel_requested'] or row is None or row['state'] not in {'failed', 'rejected'}:
                _fail('只有已確認失敗或拒絕的步驟可以重試', 'step_retry_unsafe', 409)
            if parent['lease_token'] and parent['lease_until'] > self.clock():
                _fail('流程仍在更新中，請稍後重試', 'pipeline_busy', 409)
            attempt = row['attempt'] + 1
            now = self.clock()
            db.execute('INSERT INTO compute_pipeline_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (owner, pipeline_id, step_id, attempt, step_request_id(pipeline_id, step_id, attempt), 'pending', None, row['dependencies_json'], '{}', None, now))
            db.execute('UPDATE compute_pipelines SET updated=? WHERE owner=? AND pipeline_id=?', (now, owner, pipeline_id))
        return self.status(owner, pipeline_id)

    def cancel(self, owner, pipeline_id):
        _owner(owner); _request_id(pipeline_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._rows(db, owner, pipeline_id)
            db.execute('UPDATE compute_pipelines SET cancel_requested=1,updated=? WHERE owner=? AND pipeline_id=?', (self.clock(), owner, pipeline_id))
            db.execute("UPDATE compute_pipeline_attempts SET state='cancelled',updated=? WHERE owner=? AND pipeline_id=? AND state IN ('pending','preparing')",
                       (self.clock(), owner, pipeline_id))
        return self.tick(owner, pipeline_id)
