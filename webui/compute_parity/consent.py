"""Owner consent is server state, never inferred from role or browser settings."""
import sqlite3
import time
from pathlib import Path

class VoiceConsent:
    def __init__(self, path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS consent(owner TEXT PRIMARY KEY, granted INTEGER, source TEXT, at REAL)')
        self.path.chmod(0o600)
    def record(self, owner, granted, source):
        if not owner or type(granted) is not bool or not source:raise ValueError('Invalid owner consent')
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO consent VALUES (?,?,?,?)',(owner,int(granted),source,time.time()))
    def permits(self, owner, per_run=False):
        with sqlite3.connect(self.path) as db:row=db.execute('SELECT granted FROM consent WHERE owner=?',(owner,)).fetchone()
        return per_run is True or bool(row and row[0]==1)
