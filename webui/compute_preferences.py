"""Owner-scoped defaults only; an accepted task's placement never changes."""
from contextlib import closing
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
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences (owner TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def get(self, owner):
        _owner(owner)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = db.execute('SELECT value FROM preferences WHERE owner=?', (owner,)).fetchone()
        return json.loads(row[0]) if row else {'default_placement': 'local', 'feature_placements': {}}

    def save(self, owner, body, feature_ids):
        _owner(owner)
        if not isinstance(body, dict) or set(body) != {'default_placement', 'feature_placements'}:
            raise ComputeRoutingError('請完整設定預設平台與功能安排')
        choices = ('auto', *LANES)
        overrides = body['feature_placements']
        if body['default_placement'] not in choices or not isinstance(overrides, dict) or len(overrides) > 256:
            raise ComputeRoutingError('預設運算平台格式不正確')
        if any(key not in feature_ids or value not in choices for key, value in overrides.items()):
            raise ComputeRoutingError('預設安排包含不可用的功能或平台')
        value = {'default_placement': body['default_placement'], 'feature_placements': dict(overrides)}
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('INSERT INTO preferences VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET value=excluded.value',
                       (owner, json.dumps(value, sort_keys=True)))
        return value

    def placement(self, owner, feature_id):
        prefs = self.get(owner)
        return prefs['feature_placements'].get(feature_id, prefs['default_placement'])
