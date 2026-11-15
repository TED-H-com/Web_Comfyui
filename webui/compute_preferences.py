"""Owner defaults and console metadata share one SQLite transaction boundary."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from compute_routing import ComputeRoutingError, LANES, _owner


class ComputePreferences:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('invalid settings store')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences (owner TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS connections (owner TEXT, provider TEXT, value TEXT NOT NULL, PRIMARY KEY(owner,provider))')
            db.execute('CREATE TABLE IF NOT EXISTS local_grants (owner TEXT PRIMARY KEY, allowed INTEGER NOT NULL)')

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    def get(self, owner, *, db=None, local_allowed=True):
        _owner(owner)
        if db is None:
            with self.db() as connection:
                return self.get(owner, db=connection, local_allowed=local_allowed)
        row = db.execute('SELECT value FROM preferences WHERE owner=?', (owner,)).fetchone()
        value = json.loads(row[0]) if row else {'default_placement': 'local' if local_allowed else None, 'feature_placements': {}}
        # A read never writes a migration and never pretends r24 used a fixed order.
        return {'auto_policy': 'legacy_load' if row else 'user_order', 'auto_order': [], 'revision': 0, **value}

    @staticmethod
    def put(db, owner, value):
        db.execute('INSERT INTO preferences VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET value=excluded.value',
                   (owner, json.dumps(value, sort_keys=True)))

    def save(self, owner, body, feature_ids, *, available=None, enabled=None, local_allowed=True):
        _owner(owner)
        if not isinstance(body, dict) or set(body) - {'default_placement', 'feature_placements', 'auto_order', 'revision'} or not {'default_placement','feature_placements'} <= set(body):
            raise ComputeRoutingError('請完整設定預設平台與功能安排')
        choices = (None, 'auto', *LANES)
        overrides = body['feature_placements']
        if body['default_placement'] not in choices or not isinstance(overrides, dict) or len(overrides) > 256:
            raise ComputeRoutingError('預設運算平台格式不正確')
        if any(key not in feature_ids or value not in ('auto', *LANES) for key, value in overrides.items()):
            raise ComputeRoutingError('預設安排包含不可用的功能或平台')
        with self.db(write=True) as db:
            current = self.get(owner, db=db, local_allowed=local_allowed)
            if 'revision' in body and (type(body['revision']) is not int or body['revision'] != current['revision']):
                raise ComputeRoutingError('設定已更新，請重新整理', code='revision_conflict', status=409)
            available_now = available(db) if callable(available) else available
            enabled_now = enabled(db) if callable(enabled) else enabled
            changed = ([body['default_placement']] if body['default_placement'] != current['default_placement'] else []) + [v for k,v in overrides.items() if current['feature_placements'].get(k) != v]
            if available_now is not None and any(v not in (None, 'auto') and v not in available_now for v in changed):
                raise ComputeRoutingError('請先開啟可用的平台', code='provider_unavailable', status=409)
            value = {**current, 'default_placement': body['default_placement'], 'feature_placements': dict(overrides), 'revision': current['revision'] + 1}
            if 'auto_order' in body:
                order = body['auto_order']
                if not isinstance(order, list) or any(not isinstance(x,str) for x in order) or len(order) != len(set(order)) or set(order) - set(LANES):
                    raise ComputeRoutingError('自動順序不可重複或包含未知平台')
                if enabled_now is not None and set(order) != set(enabled_now):
                    raise ComputeRoutingError('請按目前已開啟的平台重新儲存順序', code='revision_conflict', status=409)
                value.update(auto_order=order, auto_policy='user_order')
            elif body['default_placement'] == 'auto' and current['default_placement'] != 'auto':
                raise ComputeRoutingError('首次選擇自動安排，請確認並儲存順序')
            self.put(db, owner, value)
        return value

    def placement(self, owner, feature_id):
        prefs = self.get(owner)
        return prefs['feature_placements'].get(feature_id, prefs['default_placement'])
