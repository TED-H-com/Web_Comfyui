"""Castle integration for the persistent catalogue and material input handoff."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

from library_catalog import FACETS, LibraryCatalog, file_signature

_stores = {}
_lock = threading.Lock()
_thumb_slots = threading.Semaphore(3)
_worker_started = False


def catalog_path():
    state = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return Path(os.environ.get('CASTLE_MEDIA_CATALOG', state / 'web-comfyui-media/library.sqlite3'))


def catalog(server):
    roots = {'output': server.COMFY_OUTPUT, 'crawl': server.CRAWL_VIDEO_ROOT,
             'pictures': Path(os.environ.get('CASTLE_PICTURE_ROOT', catalog_path().parent / 'pictures'))}
    key = (str(catalog_path()), tuple((k, str(v)) for k, v in roots.items()))
    with _lock:
        if key not in _stores:
            _stores[key] = LibraryCatalog(catalog_path(), roots)
        return _stores[key]


def owner_scope(user):
    if not user:
        raise PermissionError('請先登入')
    return None if user.get('role') == 'admin' else str(user.get('email') or '').strip().lower()


def source_metadata(server, root, path, relative):
    owner = ''
    metadata = {'source': 'castle' if root == 'output' else root}
    if root == 'output' and server.direct_sidecar_path(path).is_file():
        item = server.gallery_file_item(path, 'output', server.load_hidden())
        if item:
            metadata['history'] = item
            metadata['hidden'] = item.get('hidden', False)
            metadata['title'] = item.get('feature') or path.stem
            owner = server.view_item_owner_email(path.name, '' if Path(relative).parent == Path('.') else Path(relative).parent.as_posix(), 'output', item.get('promptId', ''))
            if item.get('provenanceTrusted') and item.get('characterAttribution') == 'server':
                metadata['characterIds'] = [item['character']['id']]
    elif root != 'output':
        owner = os.environ.get('CASTLE_CRAWL_OWNER', '').strip().lower()
        for sidecar in (path.with_suffix('.info.json'), path.with_name(path.name + '.source.json')):
            if sidecar.is_file():
                try:
                    source = json.loads(sidecar.read_text())
                    metadata.update(title=str(source.get('title') or path.stem)[:240],
                                    sourceUrl=str(source.get('webpage_url') or source.get('sourceUrl') or '')[:2048],
                                    creator=str(source.get('uploader') or '')[:160])
                    for field in ('scene', 'pose', 'outfit', 'purpose', 'collection', 'tags'):
                        if source.get(field):
                            metadata[field] = source[field]
                    owner = str(source.get('ownerEmail') or owner).strip().lower()
                except (OSError, ValueError):
                    pass
    return metadata, owner


def project(server, row, store):
    meta = row['metadata']
    item = dict(meta.get('history') or {})
    relative = Path(row['relative'])
    cid = (meta.get('characterIds') or [''])[0]
    item.update(libraryId=row['id'], title=meta.get('title') or relative.stem,
                duplicateCount=row.get('duplicate_count', 1),
                kind=row['kind'], filename=relative.name, size=row['size'], modified=row['modified'],
                classification={key: meta.get(key, '') for key in (*FACETS, 'tags')},
                libraryCharacterIds=meta.get('characterIds', []), libraryAttribution='catalog',
                sourceUrl=meta.get('sourceUrl', ''), librarySource=meta.get('source', ''),
                thumbUrl=f'/api/library/file?id={row["id"]}&thumb=1&v={hashlib.sha256(row["signature"].encode()).hexdigest()[:16]}')
    if row['root'] == 'output':
        subfolder = '' if relative.parent == Path('.') else relative.parent.as_posix()
        params = urllib.parse.urlencode({'filename': relative.name, 'subfolder': subfolder, 'type': 'output'})
        item.update(type='output', subfolder=subfolder, url=f'/api/view?{params}', downloadUrl=f'/api/view?{params}&dl=1')
        item.setdefault('provenanceTrusted', False)
        item.setdefault('characterAttribution', '')
    else:
        item.update(type='material', subfolder=row['root'], url=f'/api/library/file?id={row["id"]}',
                    downloadUrl=f'/api/library/file?id={row["id"]}&dl=1', provenanceTrusted=False,
                    characterAttribution='')
    if cid and not item.get('provenanceTrusted'):
        item['character'] = server.character_summary_by_id(cid)
    return item


def list_items(server, user, params):
    store = catalog(server)
    pick = lambda k, default='': (params.get(k) or [default])[0]
    result = store.query(owner=owner_scope(user), character=pick('character'), kind=pick('kind'),
                         filters={key: pick(key) for key in FACETS}, search=pick('q'),
                         offset=max(0, int(pick('offset', '0'))), limit=min(96, max(1, int(pick('limit', '48')))),
                         order=pick('order'), include_hidden=pick('hidden') == '1')
    hidden = server.load_hidden()
    public = []
    for row in result['items']:
        # Recheck file identity on delivery. An overwritten path is never allowed
        # to inherit old classification, private ownership or generation proof.
        if store.get(row['id'], owner_scope(user)) is None:
            continue
        item = project(server, row, store)
        item['hidden'] = (item.get('type'), item.get('subfolder'), item.get('filename')) in hidden
        if item['hidden'] and pick('hidden') != '1':
            continue
        public.append(item)
    result['items'] = public
    return {'ok': True, **result}


def decorate_history(server, items):
    if not catalog_path().is_file():
        return items
    store = catalog(server)
    for item in items:
        if item.get('type', 'output') != 'output':
            continue
        relative = '/'.join(filter(None, (item.get('subfolder'), item.get('filename'))))
        proof = store.association('output', relative)
        if proof:
            from library_catalog import item_id
            indexed = store.get(item_id('output', relative))
            ids = indexed['metadata'].get('characterIds', []) if indexed else [proof['character_id']]
            item.update(libraryCharacterIds=ids, libraryAttribution='catalog',
                        classification={**proof['evidence'].get('classification', {}), 'source': 'scene'})
            if ids and not item.get('provenanceTrusted'):
                item['character'] = server.character_summary_by_id(ids[0])
    return items


def thumbnail(store, item):
    path = item['path']
    key = hashlib.sha256((str(path) + item['signature']).encode()).hexdigest()
    target = store.db.parent / 'thumbs' / f'{key}.jpg'
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with _thumb_slots:
        if target.is_file():
            return target
        fd, temp = tempfile.mkstemp(suffix='.jpg', dir=target.parent)
        os.close(fd)
        try:
            if item['kind'] == 'image':
                from PIL import Image, ImageOps
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((480, 600))
                    im.convert('RGB').save(temp, 'JPEG', quality=82)
            elif item['kind'] == 'video':
                subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                '-threads', '1', '-i', str(path), '-frames:v', '1',
                                '-vf', 'scale=480:480:force_original_aspect_ratio=decrease', '-threads', '1', temp],
                               check=True, timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                raise ValueError('No thumbnail for this media type')
            if file_signature(path) != item['signature']:
                raise ValueError('Media changed')
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return target


def import_input(server, user, fid, slot):
    store = catalog(server)
    item = store.get(fid, owner_scope(user))
    if not item:
        raise FileNotFoundError('找不到素材')
    import re
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}', slot):
        raise ValueError('不支援的輸入欄位')
    source = item['path']
    if item['kind'] == 'video':
        # Existing Castle compatibility conversion handles AV1/social clips.
        if item['root'] == 'crawl':
            return server.import_crawl_video(item['relative'], slot)
    suffix = source.suffix.lower()
    filename = f'library_{item["id"][:24]}_{hashlib.sha256(item["signature"].encode()).hexdigest()[:12]}{suffix}'
    target = server.COMFY_INPUT / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        fd, temp = tempfile.mkstemp(dir=target.parent, suffix=suffix)
        os.close(fd)
        try:
            shutil.copyfile(source, temp)
            if file_signature(source) != item['signature']:
                raise ValueError('素材已變更，請重新整理')
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return {'ok': True, 'serverFile': filename, 'slot': slot, 'label': item['metadata'].get('title') or source.name,
            'url': '/api/view?' + urllib.parse.urlencode({'filename': filename, 'type': 'input'}),
            'libraryId': fid}


def start_indexer(server):
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    def work():
        receipts = {}
        while True:
            try:
                store = catalog(server)
                try:
                    # Persist real generation receipts while jobs are still in ComfyUI
                    # history, independently of which page the user has open.
                    items = server.output_images(server.request_json('/history?max_items=500'), server.load_hidden(), server.load_job_index())
                    for item in items:
                        if item.get('type') != 'output' or not item.get('provenanceTrusted'):
                            continue
                        relative = '/'.join(filter(None, (item.get('subfolder'), item.get('filename'))))
                        version = json.dumps(item, sort_keys=True)
                        if receipts.get(relative) == version:
                            continue
                        path = store.path('output', relative)
                        metadata, owner = source_metadata(server, 'output', path, relative)
                        if owner:
                            store.upsert('output', relative, metadata, owner)
                            receipts[relative] = version
                    if len(receipts) > 2000:
                        receipts.clear()
                except Exception:
                    pass
                result = store.scan(lambda *args: source_metadata(server, *args))
                result['hashes'] = store.index_content_hashes()
                print(f'Library index: {result}', flush=True)
            except Exception as error:
                print(f'Library index failed: {type(error).__name__}', flush=True)
            time.sleep(60)
    threading.Thread(target=work, name='library-index', daemon=True).start()
