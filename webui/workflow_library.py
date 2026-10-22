"""Source-attributed workflow library; no model downloads or automatic execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
NON_EXECUTABLE = {'Note', 'MarkdownNote', 'Reroute', 'PrimitiveNode'}


def read_json(path):
    if path.stat().st_size > MAX_WORKFLOW_BYTES:
        raise ValueError('Workflow exceeds 8 MiB')
    return json.loads(path.read_text(encoding='utf-8'))


def graph_details(graph):
    if not isinstance(graph, dict):
        raise ValueError('工作流必須是 JSON 物件')
    if isinstance(graph.get('nodes'), list):
        nodes = graph['nodes']
        types = sorted({str(n.get('type')) for n in nodes if isinstance(n, dict) and n.get('type')})
        return {'format': 'visual', 'node_types': types, 'node_count': len(nodes)}
    nodes = [v for v in graph.values() if isinstance(v, dict) and 'class_type' in v]
    if nodes and len(nodes) == len(graph):
        return {'format': 'api', 'node_types': sorted({str(n['class_type']) for n in nodes}), 'node_count': len(nodes)}
    raise ValueError('不是 ComfyUI visual / API 工作流')


def safe_path(relative, root=ROOT):
    candidate = root / str(relative)
    if candidate.is_symlink() or not candidate.resolve().is_relative_to((root / 'workflows').resolve()):
        raise ValueError('工作流路徑無效')
    if any(p.startswith('.') or p == 'user-saved' for p in candidate.relative_to(root).parts):
        raise ValueError('這個路徑不是共用工作流')
    if candidate.suffix.lower() != '.json' or not candidate.is_file():
        raise ValueError('工作流不存在')
    return candidate


def catalog(root=ROOT):
    entries = []
    covered = set()
    provenance_path = root / 'manifests/workflow_coverage/imported_sources.json'
    provenance = read_json(provenance_path).get('records', []) if provenance_path.is_file() else []
    by_url = {row.get('source_url'): row for row in provenance if row.get('destination')}
    by_path = {row['destination']: row for row in provenance if row.get('destination')}
    catalog_path = root / 'manifests/community_workflows/catalog.json'
    if catalog_path.is_file():
        data = read_json(catalog_path)
        for raw in data.get('workflows', []):
            entry = dict(raw)
            source = data.get('sources', {}).get(entry.get('source'), {})
            entry['license'] = source.get('license') or '未宣告再散布授權'
            imported = by_url.get(entry.get('upstream_url'), {})
            if imported:
                entry['local_path'] = imported['destination']
                entry['license'] = imported['license']
            relative = entry.get('local_path')
            entry['available'] = False
            if relative:
                try:
                    path = safe_path(relative, root)
                    graph = read_json(path)
                    entry.update(graph_details(graph))
                    entry['available'] = True
                    covered.add(path.resolve())
                except (ValueError, OSError):
                    entry['status'] = 'file-unavailable'
            entry['kind'] = 'workflow'
            entries.append(entry)
    for path in sorted((root / 'workflows').rglob('*.json')):
        if path.resolve() in covered:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            safe_path(relative, root)
            details = graph_details(read_json(path))
        except (ValueError, OSError):
            continue
        entries.append({
            'id': 'local-' + hashlib.sha256(relative.encode()).hexdigest()[:20],
            'title': path.stem, 'category': path.parent.name if path.parent.name != 'workflows' else 'local',
            'source': 'workspace', 'kind': 'workflow', 'available': True,
            'local_path': relative, 'tags': [], 'status': 'not-gpu-qualified', **details,
            **({'upstream_url': by_path[relative].get('source_url'),
                'license': by_path[relative].get('license'),
                'source': urllib.parse.urlsplit(by_path[relative].get('source_url', '')).path.split('/')[1],
                'notes': by_path[relative].get('modifications', [])} if relative in by_path else {}),
        })
    recipes_path = root / 'manifests/community_recipes/recipes.json'
    if recipes_path.is_file():
        data = read_json(recipes_path)
        recipes = data if isinstance(data, list) else data.get('recipes', [])
        for raw in recipes:
            entries.append({**raw, 'id': 'recipe-' + str(raw['id']), 'kind': 'recipe', 'available': False})
    return entries


def selected(entry_id, root=ROOT):
    entry = next((e for e in catalog(root) if e['id'] == entry_id and e.get('available')), None)
    if entry is None:
        raise ValueError('這個項目是參考資料，沒有可載入的本機 JSON')
    return entry, read_json(safe_path(entry['local_path'], root))


def missing_nodes(entry, object_info):
    return sorted(set(entry.get('node_types', [])) - set(object_info) - NON_EXECUTABLE)


def handle_get(handler, path, query):
    if path not in {'/api/workflow-library', '/api/workflow-library/file'}:
        return False
    if not handler.require_auth():
        return True
    params = urllib.parse.parse_qs(query)
    try:
        if path.endswith('/file'):
            _, graph = selected((params.get('id') or [''])[0])
            handler.send_json(graph, cache_control='no-store')
        else:
            server = sys.modules[handler.__class__.__module__]
            entries = catalog()
            connection = {'checked': False}
            if (params.get('check') or [''])[0] == '1':
                try:
                    info = server.request_json('/object_info')
                    for entry in entries:
                        if entry.get('available'):
                            entry['missing_nodes'] = missing_nodes(entry, info)
                    connection = {'checked': True, 'connected': True}
                except Exception:
                    connection = {'checked': True, 'connected': False, 'error': '無法連線 ComfyUI，請先啟動服務。'}
            browser_url = os.environ.get('COMFYUI_BROWSER_URL', 'http://127.0.0.1:8188')
            parsed = urllib.parse.urlsplit(browser_url)
            if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
                browser_url = 'http://127.0.0.1:8188'
            handler.send_json({'items': entries, 'connection': connection, 'comfy_url': browser_url}, cache_control='no-store')
    except (ValueError, OSError) as error:
        handler.send_json({'ok': False, 'error': str(error)}, 400)
    return True


def handle_post(handler, path):
    if path != '/api/workflow-library/open':
        return False
    if not handler.require_auth() or not handler.require_operator():
        return True
    try:
        body = handler.read_json()
        entry, graph = selected(str(body.get('id', '')))
        if entry.get('format') != 'visual':
            raise ValueError('API 格式請下載後在 ComfyUI 匯入；原生編輯器支援視覺工作流。')
        server = sys.modules[handler.__class__.__module__]
        digest = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()[:12]
        filename = 'workflows/webui-library/' + entry['id'] + '-' + digest + '.json'
        # Entry IDs are catalog-controlled, but keep the destination safe for
        # manifests supplied by repository contributors as well.
        if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in entry['id']):
            raise ValueError('工作流 ID 無效')
        info = server.request_json('/object_info')
        missing = missing_nodes(entry, info)
        server.request_json('/userdata/' + urllib.parse.quote(filename, safe='') + '?overwrite=false', graph, method='POST')
        handler.send_json({'ok': True, 'filename': filename, 'missing_nodes': missing,
                           'message': '已加入 ComfyUI 的 webui-library 資料夾；請在工作流側欄開啟並檢查模型後執行。'})
    except Exception as error:
        # Upstream failures may include private endpoint details. Return a small
        # deliberate error, never raw credentials, URLs, or HTTP response bodies.
        message = str(error) if isinstance(error, ValueError) else 'ComfyUI 載入失敗；若已載入，請在 webui-library 資料夾開啟。'
        handler.send_json({'ok': False, 'error': message}, 400)
    return True
