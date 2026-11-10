"""Pinned external reference catalogue; selected images go directly to generation.

No bulk image download, gallery ingestion, upstream scripts, or generation calls.
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import secrets
import threading
import tempfile
import urllib.parse
from pathlib import Path

from material_collector import fetch_public, atomic_json

MANIFEST = Path(__file__).resolve().parents[1] / 'manifests/open_pose_costume/source.json'
CACHE = Path(os.environ.get('WEB_COMFYUI_REFERENCE_CACHE') or os.environ.get('CASTLE_REFERENCE_CACHE') or Path(os.environ.get('WEB_COMFYUI_CACHE_DIR', Path.home()/'.cache/web-comfyui')) / 'reference-sources')
REPOSITORY = 'https://github.com/hongye007/open-pose-costume'
TERMS = REPOSITORY + '/blob/main/SOURCES.md'
HOSTS = {'ai-pose-gallery.pages.dev', 'ai-pose-depth-gallery.pages.dev',
         'ai-costume-gallery.pages.dev', 'ai-costume-anime-gallery.pages.dev',
         'ai-portrait-gallery.pages.dev', 'omilic.com'}
_lock = threading.Lock()
_downloads = threading.BoundedSemaphore(2)
ZH_TW = json.loads(MANIFEST.with_name('zh_tw.json').read_text())


def image_url(value):
    url = urllib.parse.urljoin('https://omilic.com/', str(value))
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError('參考圖片來源不受支援')
    if not p.path.lower().endswith(('.png','.jpg','.jpeg','.webp')) or p.query or p.fragment:
        raise ValueError('參考圖片網址無效')
    if p.hostname == 'omilic.com' and not p.path.startswith('/custom/'):
        raise ValueError('自建參考圖片網址無效')
    return url


def label(value):
    text = str(value.get('zh') or value.get('en') or '') if isinstance(value,dict) else str(value or '')
    return ZH_TW.get(text, text)


def costume_style(item):
    if item.get('kind') != 'costume':
        return ''
    host = urllib.parse.urlsplit(item.get('source', '')).hostname
    if host == 'ai-costume-anime-gallery.pages.dev':
        return 'anime'
    if host == 'ai-costume-gallery.pages.dev':
        return 'realistic'
    tags = {tag.get('id') for tag in item.get('tags', []) if isinstance(tag, dict)}
    if 'anime' in tags and 'photo' not in tags:
        return 'anime'
    if 'photo' in tags and 'anime' not in tags:
        return 'realistic'
    return ''


def normalize(data):
    result=[]
    for item in data['items']:
        if item.get('kind') not in ('pose','costume','character'):
            continue
        custom=item.get('source')=='custom'
        variants=[]
        for v in item.get('variants',[]):
            variant={'id':v['id'],'angle':v.get('angle','front'),'label':label(v.get('label')),
                     **{k:image_url(v[k]) for k in ('original','image','thumbnail')}}
            if v.get('depth'):
                variant['depth']={k:image_url(v['depth'][k]) for k in ('original','image','thumbnail')}
            elif item['source']=='https://ai-pose-gallery.pages.dev/':
                variant['depth']={k:variant[k].replace('ai-pose-gallery.','ai-pose-depth-gallery.') for k in ('original','image','thumbnail')}
            variants.append(variant)
        if not variants:
            continue
        result.append({'id':item['id'],'kind':item['kind'],'code':item.get('code',item['id']),
                       'visualStyle':costume_style(item),
                       'title':label(item.get('title')),'englishTitle':item.get('title',{}).get('en',''),
                       'category':item['category']['id'],'categoryLabel':label(item['category']),
                       'source':'custom' if custom else urllib.parse.urlsplit(item['source']).hostname,
                       'sourceUrl':REPOSITORY if custom else item['source'],
                       'termsUrl':REPOSITORY+'/blob/main/public/custom/LICENSE.txt' if custom else TERMS,
                       'usage':'自建素材：限個人學習、非商業使用；其他用途需另行授權。' if custom else '依原站條款作單張創作參考；圖片本身不可再分發。',
                       'keywords':label(item.get('keywords'))+' '+item.get('keywords',{}).get('en',''),
                       'tags':[label(t) for t in item.get('tags',[])], 'variants':variants})
    return result


@functools.lru_cache(maxsize=1)
def catalog():
    with _lock:
        manifest=json.loads(MANIFEST.read_text())
        path=CACHE/(manifest['revision']+'.json')
        raw=path.read_bytes() if path.is_file() else b''
        if hashlib.sha256(raw).hexdigest()!=manifest['catalogSha256']:
            raw, _, _ = fetch_public(manifest['catalogUrl'],limit=8*1024*1024)
            if hashlib.sha256(raw).hexdigest()!=manifest['catalogSha256']:
                raise ValueError('參考來源版本校驗失敗')
            CACHE.mkdir(parents=True,exist_ok=True)
            # Atomic replace; a partial download never becomes the next index.
            fd, temp = tempfile.mkstemp(dir=CACHE, suffix='.tmp')
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                os.replace(temp, path)
            finally:
                Path(temp).unlink(missing_ok=True)
        return normalize(json.loads(raw))


def listing(params):
    kind=str(params.get('kind') or 'pose')
    if kind not in ('pose','costume','character'):
        raise ValueError('不支援的參考類型')
    page=max(1,min(1000,int(params.get('page') or 1)))
    q=str(params.get('q') or '').casefold()[:160]
    source=str(params.get('source') or '')
    category=str(params.get('category') or '')
    category_filter=str(params.get('categories') or '')
    categories_filter={value for value in category_filter.split(',') if value}
    if len(category_filter)>1024 or len(categories_filter)>32:
        raise ValueError('參考分類篩選過多')
    visual_style=str(params.get('visualStyle') or '')
    if visual_style not in ('', 'anime', 'realistic'):
        raise ValueError('不支援的參考畫風')
    pool=[i for i in catalog() if i['kind']==kind and
          (kind!='costume' or not visual_style or i.get('visualStyle')==visual_style)]
    categories=sorted({(i['category'],i['categoryLabel']) for i in pool})
    sources=sorted({i['source'] for i in pool})
    rows=[i for i in pool if (not source or i['source']==source) and (not category or i['category']==category)
          and (not categories_filter or i['category'] in categories_filter)
          and (not q or all(t in (' '.join((i['title'],i['englishTitle'],i['code'],i['keywords'],*i['tags']))).casefold() for t in q.split()))]
    return {'ok':True,'items':rows[(page-1)*24:page*24],'total':len(rows),'page':page,'pageSize':24,
            'randomItem':secrets.choice(rows) if rows and str(params.get('random') or '')=='1' else None,
            'categories':[{'id':k,'label':v} for k,v in categories],'sources':sources,'repository':REPOSITORY}


def import_image(server, user, body):
    if not isinstance(body,dict) or set(body)-{'id','variantId','depth','slot'}:
        raise ValueError('請使用參考目錄中的圖片')
    if type(body.get('depth',False)) is not bool:
        raise ValueError('深度圖選項無效')
    slot=str(body.get('slot') or '')
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}',slot):
        raise ValueError('輸入欄位無效')
    item=next((i for i in catalog() if i['id']==body.get('id')),None)
    variant=next((v for v in (item or {}).get('variants',[]) if v['id']==body.get('variantId')),None)
    if not item or not variant:
        raise ValueError('找不到參考圖片或視角')
    selected=variant.get('depth') if body.get('depth') else variant
    if not selected:
        raise ValueError('此視角沒有深度圖')
    owner=str((user or {}).get('email') or '').strip().lower()
    if not owner:
        raise PermissionError('請先登入')
    if not _downloads.acquire(blocking=False):
        raise ValueError('參考圖片載入中，請稍後再試')
    try:
        raw, _, final=fetch_public(selected['original'],limit=25*1024*1024)
        image_url(final)
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}.get(im.format)
            if not ext or im.width*im.height>40_000_000:
                raise ValueError('參考圖片格式或尺寸不支援')
            im.verify()
        digest=hashlib.sha256(raw).hexdigest()
        name='reference_opc_'+hashlib.sha256(owner.encode()).hexdigest()[:12]+'_'+digest[:24]+ext
        filename=server.save_uploaded_bytes(raw,name,'image/'+('jpeg' if ext=='.jpg' else ext[1:]),slot)
        receipt={'source':'open-pose-costume','sourceUrl':item['sourceUrl'],'imageUrl':selected['original'],
                 'referenceId':item['id'],'variantId':variant['id'],'depth':bool(body.get('depth')),
                 'title':item['title'],'kind':item['kind'],'ownerEmail':owner,'sha256':digest,
                 'termsUrl':item['termsUrl'],'usage':item['usage']}
        target=(server.COMFY_INPUT/filename).resolve()
        if not target.is_relative_to(server.COMFY_INPUT.resolve()):
            raise ValueError('參考圖片儲存路徑無效')
        atomic_json(target.with_name(target.name+'.source.json'),receipt)
        return {'ok':True,'serverFile':filename,'filename':filename,'slot':slot,
                'label':f"{item['title']} · {variant['label']}"+(' · 深度圖' if body.get('depth') else ''),'reference':receipt}
    finally:
        _downloads.release()
