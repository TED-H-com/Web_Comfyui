"""Bounded webpage image collector and castle_fb_crawl bridge.

Inspired by crawler-pic's img/data-original extraction, with selectable sources,
atomic content-addressed files and provenance sidecars instead of title filenames.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import uuid
from html.parser import HTMLParser
from pathlib import Path

import library_service

MAX_IMAGE = 25 * 1024 * 1024
MAX_PAGE = 4 * 1024 * 1024
_slots = threading.BoundedSemaphore(2)
VIDEO_HOSTS = ('facebook.com', 'fb.watch', 'youtube.com', 'youtu.be', 'instagram.com',
               'tiktok.com', 'threads.com', 'threads.net', 'vimeo.com', 'x.com', 'twitter.com', 'bilibili.com')


def public_target(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('請使用公開網站的 HTTP 或 HTTPS 網址')
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('採集來源必須是公開網站')
    return p, addresses[0]


def fetch_public(url, limit=MAX_IMAGE):
    for _ in range(6):
        parsed, address = public_target(url)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=25)
        # Pin the already-checked DNS answer, including across redirects.
        raw = socket.create_connection((address, port), timeout=25)
        connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) if parsed.scheme == 'https' else raw
        try:
            connection.request('GET', urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, '')),
                               headers={'User-Agent': 'CastleMediaCollector/1.0', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('來源轉址無效')
                url = urllib.parse.urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f'來源回應 HTTP {response.status}')
            content_type = response.getheader('Content-Type', '').lower()
            bound = min(limit, MAX_PAGE) if 'html' in content_type else limit
            body = response.read(bound + 1)
            if len(body) > bound:
                raise ValueError('來源檔案超過採集大小限制')
            return body, content_type, url
        finally:
            connection.close()
    raise ValueError('來源轉址次數過多')


class Images(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.items = base, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'img':
            source = a.get('data-original') or a.get('data-src') or a.get('src')
            if not source and a.get('srcset'):
                source = a['srcset'].split(',')[-1].strip().split(' ')[0]
            if source:
                self.items.append((urllib.parse.urljoin(self.base, source), a.get('alt', '')))
        elif tag == 'meta' and a.get('property') == 'og:image' and a.get('content'):
            self.items.append((urllib.parse.urljoin(self.base, a['content']), ''))


def atomic_json(path, data):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False))
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def collect_pictures(server, url, owner, purpose):
    from PIL import Image
    raw, content_type, final = fetch_public(url)
    candidates = [(final, '', raw)]
    if 'html' in content_type:
        parser = Images(final)
        encoding = 'utf-8'
        if 'charset=' in content_type:
            encoding = content_type.split('charset=', 1)[1].split(';')[0].strip()
        try:
            parser.feed(raw.decode(encoding, errors='replace'))
        except LookupError:
            parser.feed(raw.decode('utf-8', errors='replace'))
        candidates = [(link, title, None) for link, title in dict(parser.items).items()][:24]
    store = library_service.catalog(server)
    folder = store.roots['pictures'] / hashlib.sha256(owner.encode()).hexdigest()[:24]
    folder.mkdir(parents=True, exist_ok=True)
    imported, failures = [], 0
    for link, title, body in candidates:
        try:
            if body is None:
                body, _type, link = fetch_public(link)
            with Image.open(io.BytesIO(body)) as image:
                extension = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif'}.get(image.format)
                if not extension:
                    raise ValueError('Unsupported image')
                image.verify()
            digest = hashlib.sha256(body).hexdigest()
            path = folder / (digest + extension)
            if not path.exists():
                temp = folder / (digest + '.' + uuid.uuid4().hex + '.tmp')
                temp.write_bytes(body)
                os.replace(temp, path)
            sidecar = path.with_name(path.name + '.source.json')
            if not sidecar.exists():
                atomic_json(sidecar, {'title': title[:240] or urllib.parse.unquote(Path(urllib.parse.urlsplit(link).path).name),
                                     'sourceUrl': final, 'imageUrl': link, 'ownerEmail': owner,
                                     'purpose': purpose, 'collector': 'crawler-pic-adapter', 'sha256': digest})
            key, relative = store.locate(path)
            meta, source_owner = library_service.source_metadata(server, key, path, relative)
            store.upsert(key, relative, meta, source_owner)
            imported.append(path.name)
        except (ValueError, OSError, http.client.HTTPException):
            failures += 1
    if not imported:
        raise ValueError('沒有可下載的圖片；此頁可能需要登入或由 JavaScript 動態載入。')
    return len(set(imported)), failures


def collect_video(server, url, owner, purpose):
    parsed, _address = public_target(url)
    if not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in VIDEO_HOSTS):
        raise ValueError('目前影片採集支援 FB、YouTube、Instagram、TikTok、Threads、Vimeo、X 與 Bilibili')
    store = library_service.catalog(server)
    folder = store.roots['crawl'] / hashlib.sha256(owner.encode()).hexdigest()[:24] / hashlib.sha256(url.encode()).hexdigest()[:24]
    folder.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'FB_CRAWL_DEST': str(folder), 'FB_CRAWL_MAX_BYTES': str(512 * 1024 * 1024)}
    env['PATH'] = str(Path(server._resolve_yt_dlp()).parent) + os.pathsep + env.get('PATH', '')
    completed = subprocess.run([str(server.CRAWL_DIR / 'grab.sh'), url], cwd=server.CRAWL_DIR, env=env,
                               timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if completed.returncode:
        raise ValueError('影片採集失敗；若來源需要登入，請更新 castle_fb_crawl 的網站 cookies。')
    count = 0
    for path in folder.iterdir():
        if path.suffix.lower() not in server.CRAWL_VIDEO_EXTS:
            continue
        atomic_json(path.with_name(path.name + '.source.json'), {'sourceUrl': url, 'ownerEmail': owner, 'purpose': purpose, 'title': path.stem})
        key, relative = store.locate(path)
        meta, source_owner = library_service.source_metadata(server, key, path, relative)
        store.upsert(key, relative, meta, source_owner)
        count += 1
    if not count:
        raise ValueError('來源沒有產生可用的影片檔案')
    return count, 0


def job_path(server, job_id):
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('Invalid collection ID')
    root = library_service.catalog(server).db.parent / 'collection-jobs'
    root.mkdir(parents=True, exist_ok=True)
    return root / f'{job_id}.json'


def job_status(server, job_id, user):
    path = job_path(server, job_id)
    if not path.is_file():
        raise FileNotFoundError('找不到採集任務')
    data = json.loads(path.read_text())
    if data['owner'] != user.get('email') and user.get('role') != 'admin':
        raise PermissionError('沒有權限')
    if data['state'] == 'running' and time.time() - data['started'] > 900:
        data.update(state='error', message='採集中斷或逾時；已下載素材仍保留，可以重試。')
    return {k: data[k] for k in ('id', 'state', 'message')}


def start(server, user, payload):
    if user.get('role') != 'admin':
        raise PermissionError('需要管理員權限')
    url = str(payload.get('url') or '').strip()
    mode = payload.get('mode')
    if len(url) > 2048 or mode not in ('pictures', 'video'):
        raise ValueError('無效的採集來源')
    public_target(url)
    if not _slots.acquire(blocking=False):
        raise ValueError('已有兩項採集工作進行中，請稍後重試')
    job_id = uuid.uuid4().hex
    path = job_path(server, job_id)
    job = {'id': job_id, 'owner': user['email'], 'state': 'running', 'message': '採集中…', 'started': time.time()}
    atomic_json(path, job)
    def run():
        try:
            operation = collect_pictures if mode == 'pictures' else collect_video
            count, skipped = operation(server, url, user['email'], str(payload.get('purpose') or '')[:240])
            job.update(state='completed', message=f'已加入 {count} 件素材' + (f'，{skipped} 件無法下載。' if skipped else '。'))
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            job.update(state='error', message=str(error) if isinstance(error, ValueError) else '來源暫時無法下載，請稍後重試。')
        except Exception:
            job.update(state='error', message='素材採集失敗，請稍後重試。')
        finally:
            atomic_json(path, job)
            _slots.release()
    threading.Thread(target=run, name=f'collect-{job_id[:8]}', daemon=True).start()
    return {'ok': True, 'id': job_id, 'state': 'running'}
