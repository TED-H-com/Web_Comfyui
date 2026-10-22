"""Persistent, owner-scoped media catalogue. Classification never grants generation provenance.

Files stay in their source roots. SQLite contains metadata and verified scene
associations, not image bytes. Only the local importer may register scene proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MEDIA = {**dict.fromkeys(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif'), 'image'),
         **dict.fromkeys(('.mp4', '.mov', '.webm', '.mkv'), 'video'),
         **dict.fromkeys(('.wav', '.mp3', '.flac', '.ogg', '.m4a'), 'audio')}
MEDIA.update({'.oga': 'audio', '.aac': 'audio'})
FACETS = ('source', 'genre', 'scene', 'pose', 'level', 'outfit', 'purpose', 'review', 'collection')
EDITABLE = ('title', 'characterIds', 'tags', 'genre', 'scene', 'pose', 'level', 'outfit', 'purpose', 'collection')


def file_signature(path: Path) -> str:
    s = path.stat()
    return f'{s.st_dev}:{s.st_ino}:{s.st_size}:{s.st_mtime_ns}:{s.st_ctime_ns}'


def digest_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def item_id(root: str, relative: str) -> str:
    return hashlib.sha256(f'{root}\0{relative}'.encode()).hexdigest()


def merge_scene_evidence(previous: dict, current: dict) -> dict:
    result = dict(current)
    generation = previous.get('generation')
    if previous.get('source') == 'scene-runner' and previous.get('promptId'):
        generation = {key: previous[key] for key in ('promptId', 'sourceArtifactId', 'sourceSha256') if key in previous}
    if generation and current.get('source') != 'scene-runner':
        result['generation'] = generation
    return result


class LibraryCatalog:
    def __init__(self, db: Path, roots: dict[str, Path]):
        self.db = Path(db)
        self.roots = {key: Path(value).resolve() for key, value in roots.items()}
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self._query_cache = {}
        self._facet_cache = {}
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS files (
                  id TEXT PRIMARY KEY, root TEXT NOT NULL, relative TEXT NOT NULL,
                  signature TEXT NOT NULL, modified REAL NOT NULL, size INTEGER NOT NULL,
                  kind TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                  digest TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}',
                  present INTEGER NOT NULL DEFAULT 1, UNIQUE(root, relative));
                CREATE INDEX IF NOT EXISTS files_owner ON files(owner,present,kind,modified DESC);
                CREATE INDEX IF NOT EXISTS files_digest ON files(owner,digest);
                CREATE INDEX IF NOT EXISTS files_pending_digest ON files(modified DESC)
                  WHERE present=1 AND digest='';
                CREATE TABLE IF NOT EXISTS characters (
                  file_id TEXT NOT NULL, character_id TEXT NOT NULL,
                  PRIMARY KEY(file_id,character_id));
                CREATE INDEX IF NOT EXISTS character_files ON characters(character_id,file_id);
                CREATE TABLE IF NOT EXISTS associations (
                  file_id TEXT PRIMARY KEY, signature TEXT NOT NULL, digest TEXT NOT NULL,
                  owner TEXT NOT NULL, character_id TEXT NOT NULL, evidence TEXT NOT NULL,
                  run_id TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS edits (
                  file_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY, file_id TEXT NOT NULL, actor TEXT NOT NULL,
                  before_json TEXT NOT NULL, after_json TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revision (id INTEGER PRIMARY KEY,value INTEGER NOT NULL);
                INSERT OR IGNORE INTO revision VALUES(1,0);
                CREATE TRIGGER IF NOT EXISTS files_insert_revision AFTER INSERT ON files BEGIN
                  UPDATE revision SET value=value+1 WHERE id=1; END;
                CREATE TRIGGER IF NOT EXISTS files_update_revision AFTER UPDATE ON files BEGIN
                  UPDATE revision SET value=value+1 WHERE id=1; END;
                CREATE TRIGGER IF NOT EXISTS files_delete_revision AFTER DELETE ON files BEGIN
                  UPDATE revision SET value=value+1 WHERE id=1; END;
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA synchronous=NORMAL')
        try:
            with c:
                yield c
        finally:
            c.close()

    def path(self, root: str, relative: str) -> Path:
        base = self.roots[root]
        path = (base / relative).resolve()
        if path == base or not path.is_relative_to(base):
            raise ValueError('Invalid media path')
        return path

    def locate(self, path: Path) -> tuple[str, str]:
        path = path.resolve()
        for key, root in self.roots.items():
            if path.is_relative_to(root):
                return key, path.relative_to(root).as_posix()
        raise ValueError('File is outside the configured media roots')

    def get(self, fid: str, owner: str | None = None) -> dict | None:
        with self.connect() as c:
            row = c.execute('SELECT * FROM files WHERE id=? AND present=1', (fid,)).fetchone()
        if row is None or (owner is not None and (not owner or row['owner'] != owner)):
            return None
        result = dict(row)
        path = self.path(row['root'], row['relative'])
        if not path.is_file() or file_signature(path) != row['signature']:
            return None
        result['metadata'] = json.loads(row['metadata'])
        result['path'] = path
        return result

    def association(self, root: str, relative: str) -> dict | None:
        with self.connect() as c:
            row = c.execute('SELECT * FROM associations WHERE file_id=?', (item_id(root, relative),)).fetchone()
        if row:
            try:
                if file_signature(self.path(root, relative)) == row['signature']:
                    return {**dict(row), 'evidence': json.loads(row['evidence'])}
            except OSError:
                pass
        return None

    def upsert(self, root: str, relative: str, metadata: dict, owner: str = '', *, connection=None):
        path = self.path(root, relative)
        kind = MEDIA.get(path.suffix.lower())
        if not kind:
            return
        signature = file_signature(path)
        st = path.stat()
        declared_kind = (metadata.get('history') or {}).get('kind')
        if declared_kind in ('image', 'video', 'audio'):
            kind = declared_kind
        fid = item_id(root, relative)
        def write(c):
            previous = c.execute('SELECT metadata,signature,digest FROM files WHERE id=?', (fid,)).fetchone()
            # Reconciliation adds classification without erasing an existing
            # authentic generation receipt. Replaced bytes inherit no receipt.
            meta = json.loads(previous['metadata']) if previous and previous['signature'] == signature else {}
            meta.update(metadata)
            if previous and 'hidden' not in meta:
                meta['hidden'] = bool(json.loads(previous[0]).get('hidden'))
            digest = previous['digest'] if previous and previous['signature'] == signature else ''
            association = c.execute('SELECT * FROM associations WHERE file_id=? AND signature=?',
                                    (fid, signature)).fetchone()
            if association:
                # Existing generation ownership is never reassigned by a scene import.
                if owner and owner != association['owner']:
                    raise ValueError('Conflicting generation and catalogue owners')
                owner_value = association['owner']
                digest = association['digest']
                proof = json.loads(association['evidence'])
                meta.update(proof.get('classification', {}))
                meta.update(characterIds=[association['character_id']], source='scene',
                            association='scene-record', evidence=proof)
            else:
                owner_value = owner
            edit = c.execute('SELECT metadata FROM edits WHERE file_id=?', (fid,)).fetchone()
            if edit:
                meta.update(json.loads(edit[0]))
            meta.setdefault('source', 'castle' if root == 'output' else root)
            meta.setdefault('title', path.stem)
            c.execute('''INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET signature=excluded.signature,modified=excluded.modified,
                size=excluded.size,kind=excluded.kind,owner=excluded.owner,digest=excluded.digest,
                metadata=excluded.metadata,present=1''',
                (fid, root, relative, signature, st.st_mtime, st.st_size, kind, owner_value,
                 digest, json.dumps(meta, ensure_ascii=False)))
            c.execute('DELETE FROM characters WHERE file_id=?', (fid,))
            c.executemany('INSERT OR IGNORE INTO characters VALUES(?,?)',
                          [(fid, cid) for cid in meta.get('characterIds', []) if cid])
        if connection is not None:
            write(connection)
        else:
            with self.connect() as c:
                write(c)

    def register_scene(self, path: Path, digest: str, character_id: str, owner: str,
                       evidence: dict, run_id: str, *, connection=None, verified_signature='') -> bool:
        if not owner or not character_id or not evidence.get('sourceId'):
            raise ValueError('Scene association needs an owner, character and source record')
        root, relative = self.locate(path)
        if root != 'output':
            raise ValueError('Scene output must be in the ComfyUI output root')
        fid = item_id(root, relative)
        sidecar = path.with_name(path.name + '.castle.json')
        if sidecar.is_file():
            data = json.loads(sidecar.read_text())
            existing_owner = (data.get('submittedBy') or {}).get('email', '')
            if existing_owner and existing_owner.strip().lower() != owner:
                raise ValueError('File sidecar belongs to another owner')
        signature = file_signature(path)
        # The local importer may hash on a bounded I/O pool, then pass that exact
        # file identity here. This internal optimization is never exposed by HTTP.
        if (verified_signature != signature and digest_file(path) != digest) or file_signature(path) != signature:
            raise ValueError('File hash changed or does not match the scene record')
        with (self.connect() if connection is None else nullcontext(connection)) as c:
            previous = c.execute('SELECT * FROM associations WHERE file_id=?', (fid,)).fetchone()
            existing = c.execute('SELECT owner FROM files WHERE id=?', (fid,)).fetchone()
            if existing and existing['owner'] and existing['owner'] != owner:
                raise ValueError('File already belongs to another owner')
            if previous and (previous['digest'], previous['character_id'], previous['owner']) != (digest, character_id, owner):
                raise ValueError('Conflicting scene association; explicit review required')
            evidence = merge_scene_evidence(json.loads(previous['evidence']) if previous else {}, evidence)
            encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            if previous and previous['signature'] == signature and previous['evidence'] == encoded:
                return False
            c.execute('INSERT OR REPLACE INTO associations VALUES(?,?,?,?,?,?,?,?)',
                      (fid, signature, digest, owner, character_id, encoded,
                       previous['run_id'] if previous else run_id, time.time()))
            self.upsert(root, relative, {}, owner, connection=c)
        return True

    def edit(self, fid: str, patch: dict, actor: str, owner: str | None):
        item = self.get(fid, owner)
        if not item:
            raise FileNotFoundError('Material not found')
        clean = {}
        for key, value in patch.items():
            if key not in EDITABLE:
                raise ValueError('Unsupported classification field')
            if key in ('tags', 'characterIds'):
                if not isinstance(value, list) or len(value) > 32 or any(not isinstance(v, str) or len(v) > 128 for v in value):
                    raise ValueError('Invalid classification list')
                clean[key] = list(dict.fromkeys(v.strip() for v in value if v.strip()))
            elif not isinstance(value, str) or len(value) > 240:
                raise ValueError('Invalid classification value')
            else:
                clean[key] = value.strip()
        with self.connect() as c:
            previous = c.execute('SELECT metadata FROM edits WHERE file_id=?', (fid,)).fetchone()
            edit = json.loads(previous[0]) if previous else {}
            before = dict(edit)
            edit.update(clean)
            c.execute('INSERT OR REPLACE INTO edits VALUES(?,?,?)', (fid, json.dumps(edit), time.time()))
            c.execute('INSERT INTO audit(file_id,actor,before_json,after_json,created) VALUES(?,?,?,?,?)',
                      (fid, actor, json.dumps(before), json.dumps(edit), time.time()))
            self.upsert(item['root'], item['relative'], item['metadata'], item['owner'], connection=c)

    def set_hidden(self, root: str, relative: str, hidden: bool):
        with self.connect() as c:
            c.execute("UPDATE files SET metadata=json_set(metadata,'$.hidden',?) WHERE id=?",
                      (int(hidden), item_id(root, relative)))

    def query(self, *, owner: str | None, character: str = '', kind: str = '', filters=None,
              search: str = '', offset: int = 0, limit: int = 48, order: str = 'newest', include_hidden=False) -> dict:
        filters = filters or {}
        clauses, args = ['f.present=1'], []
        if not include_hidden:
            clauses.append("coalesce(json_extract(f.metadata,'$.hidden'),0)=0")
        if owner is not None:
            clauses.append('f.owner=? AND f.owner<>\'\'')
            args.append(owner)
        if character == '_unassigned':
            clauses.append('NOT EXISTS(SELECT 1 FROM characters c WHERE c.file_id=f.id)')
        elif character:
            clauses.append('f.id IN (SELECT file_id FROM characters WHERE character_id=?)')
            args.append(character)
        if kind:
            clauses.append('f.kind=?')
            args.append(kind)
        base_where, base_args = ' AND '.join(clauses), list(args)
        for key in FACETS:
            if filters.get(key):
                clauses.append(f"json_extract(f.metadata,'$.{key}')=?")
                args.append(filters[key])
        if search:
            clauses.append('(instr(lower(f.metadata),lower(?))>0 OR instr(lower(f.relative),lower(?))>0)')
            args.extend([search[:240], search[:240]])
        where = ' AND '.join(clauses)
        # Byte-identical files collapse within their existing owner scope only.
        # Hashing grants neither character attribution nor generation authority.
        group = "CASE WHEN f.digest<>'' THEN f.owner||':'||f.digest ELSE f.id END"
        sort_value = "json_extract(f.metadata,'$.title') COLLATE NOCASE" if order == 'title' else 'f.modified'
        ranked = f'''WITH ranked AS (SELECT f.id,{sort_value} AS sort_value,
            count(*) OVER(PARTITION BY {group}) AS duplicate_count,
            row_number() OVER(PARTITION BY {group}
            ORDER BY coalesce(json_extract(f.metadata,'$.history.provenanceTrusted'),0) DESC,
            (instr(f.relative,'ziyu-scene-art/')=1) DESC,f.id) AS rank
            FROM files f WHERE {where})'''
        direction = 'ASC' if order in ('oldest', 'title') else 'DESC'
        sort = f'sort_value {direction},id'
        outer_sort = f'{sort_value} {direction},f.id'
        with self.connect() as c:
            c.execute('BEGIN')
            versions = c.execute('SELECT value FROM revision WHERE id=1').fetchone()[0]
            cache_key = (versions, owner, character, kind, tuple(sorted(filters.items())), search, include_hidden)
            cached = self._query_cache.get(cache_key)
            total = cached['total'] if cached else c.execute(f'SELECT count(DISTINCT {group}) FROM files f WHERE {where}', args).fetchone()[0]
            rows = c.execute(ranked+f''', page AS (
                SELECT id,duplicate_count FROM ranked WHERE rank=1 ORDER BY {sort} LIMIT ? OFFSET ?)
                SELECT f.*,page.duplicate_count FROM page JOIN files f ON f.id=page.id ORDER BY {outer_sort}''',
                             [*args, min(96, max(1, limit)), max(0, offset)]).fetchall()
            facet_key = (versions, owner, character, kind, include_hidden)
            facets = self._facet_cache.get(facet_key)
            if facets is None:
                facets = {key: [] for key in FACETS}
                names = ','.join('?' for _ in FACETS)
                for row in c.execute(f'''SELECT j.key,j.value,count(DISTINCT {group}) AS count
                    FROM files f,json_each(f.metadata) j WHERE {base_where}
                    AND j.key IN ({names}) AND j.type='text' AND j.value<>''
                    GROUP BY j.key,j.value ORDER BY j.key,count DESC,j.value''', [*base_args, *FACETS]):
                    if len(facets[row['key']]) < 100:
                        facets[row['key']].append({'value': row['value'], 'count': row['count']})
                if len(self._facet_cache) > 64:
                    self._facet_cache.clear()
                self._facet_cache[facet_key] = facets
            status = dict(c.execute('SELECT key,value FROM state'))
        if len(self._query_cache) > 64:
            self._query_cache.clear()
        self._query_cache[cache_key] = {'total': total, 'facets': facets}
        items = [{**dict(row), 'metadata': json.loads(row['metadata'])} for row in rows]
        return {'items': items, 'total': total, 'offset': offset, 'limit': limit,
                'hasMore': offset + len(items) < total, 'facets': facets, 'index': status}

    def index_content_hashes(self, limit: int = 256) -> dict:
        """Hash off the request thread; commit only if the indexed file is unchanged."""
        with self.connect() as c:
            rows = [dict(row) for row in c.execute('''SELECT id,root,relative,signature FROM files
                WHERE present=1 AND digest='' ORDER BY modified DESC LIMIT ?''', (max(1, limit),))]
        def verify(row):
            try:
                path = self.path(row['root'], row['relative'])
                if file_signature(path) != row['signature']:
                    return row, None
                digest = digest_file(path)
                if file_signature(path) == row['signature']:
                    return row, digest
            except (OSError, ValueError):
                pass
            return row, None
        indexed = skipped = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for start in range(0, len(rows), 128):
                results = list(pool.map(verify, rows[start:start+128]))
                with self.connect() as c:
                    for row, digest in results:
                        if digest is None:
                            skipped += 1
                            continue
                        indexed += c.execute('''UPDATE files SET digest=? WHERE id=? AND signature=?
                            AND present=1 AND digest='' ''', (digest, row['id'], row['signature'])).rowcount
        return {'checked': len(rows), 'indexed': indexed, 'skipped': skipped}

    def scan(self, metadata_reader=None, crawl_owner: str = '') -> dict:
        """Stat each media file once; resolve paths/decode metadata only on change.

        No watcher resources are required. In-place edits, renames and deletes
        remain visible on the normal minute cadence, even on unwatched mounts.
        """
        started = time.time()
        with self.connect() as c:
            known = {r['id']: (r['signature'], r['present']) for r in c.execute('SELECT id,signature,present FROM files')}
        total = changed = 0
        for root_key, root in self.roots.items():
            if not root.is_dir():
                continue
            directories = ['']
            while directories:
                relative_dir = directories.pop()
                prefix = relative_dir + '/' if relative_dir else ''
                batch = []
                try:
                    directory = self.path(root_key, relative_dir) if relative_dir else root
                    entries = os.scandir(directory)
                except (OSError, ValueError):
                    continue
                with entries:
                    for entry in entries:
                        name = entry.name
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if not name.startswith('.') and name not in ('_clipmode_identity_staging', '__pycache__'):
                                    directories.append(prefix + name)
                                continue
                            if '.' not in name or '.' + name.rsplit('.', 1)[-1].lower() not in MEDIA:
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            value = entry.stat(follow_symlinks=False)
                            signature = f'{value.st_dev}:{value.st_ino}:{value.st_size}:{value.st_mtime_ns}:{value.st_ctime_ns}'
                            relative = prefix + name
                            fid = item_id(root_key, relative)
                            total += 1
                            previous = known.pop(fid, None)
                            if previous and previous[0] == signature and previous[1]:
                                continue
                            # The directory walk never follows symlinks; before
                            # reading changed bytes still enforce root containment.
                            path = self.path(root_key, relative)
                            metadata, owner = ({}, '' if root_key == 'output' else crawl_owner)
                            if metadata_reader:
                                metadata, owner = metadata_reader(root_key, path, relative)
                            batch.append((root_key, relative, metadata, owner))
                        except (OSError, ValueError):
                            continue
                if batch:
                    with self.connect() as c:
                        for item in batch:
                            self.upsert(*item, connection=c)
                    changed += len(batch)
        with self.connect() as c:
            c.executemany('UPDATE files SET present=0 WHERE id=?',
                          [(fid,) for fid, (_signature, present) in known.items() if present])
            for key, value in {'updated': str(time.time()), 'files': str(total), 'duration': str(time.time()-started)}.items():
                c.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, value))
        return {'files': total, 'changed': changed, 'seconds': round(time.time()-started, 2)}
