"""Source-attributed workflow library; no model downloads or automatic execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse

try:
    from workflow_guides import active_nodes, enrich_entry
    from comfy_schema import combo_options
except ModuleNotFoundError:
    from webui.workflow_guides import active_nodes, enrich_entry
    from webui.comfy_schema import combo_options

ROOT = Path(__file__).resolve().parents[1]
MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
NON_EXECUTABLE = {'Note', 'MarkdownNote', 'Reroute', 'PrimitiveNode'}
# Only reviewed widget layouts are interpreted. The indices are widget indices,
# not socket indices (for example CheckpointLoader also has a config widget).
MODEL_LOADERS = {
    'CheckpointLoaderSimple': ('checkpoints', {'ckpt_name': 0}),
    'CheckpointLoader': ('checkpoints', {'ckpt_name': 1}),
    'UNETLoader': ('diffusion_models', {'unet_name': 0}),
    'UnetLoaderGGUF': ('diffusion_models', {'unet_name': 0}),
    'VAELoader': ('vae', {'vae_name': 0}),
    'LoraLoader': ('loras', {'lora_name': 0}),
    'LoraLoaderModelOnly': ('loras', {'lora_name': 0}),
    'CLIPLoader': ('text_encoders', {'clip_name': 0}),
    'CLIPLoaderGGUF': ('text_encoders', {'clip_name': 0}),
    'DualCLIPLoader': ('text_encoders', {'clip_name1': 0, 'clip_name2': 1}),
    'DualCLIPLoaderGGUF': ('text_encoders', {'clip_name1': 0, 'clip_name2': 1}),
    'CLIPVisionLoader': ('clip_vision', {'clip_name': 0}),
    'ControlNetLoader': ('controlnet', {'control_net_name': 0}),
    'DiffControlNetLoader': ('controlnet', {'control_net_name': 0}),
    'UpscaleModelLoader': ('upscale_models', {'model_name': 0}),
}
DIRECTORY_ALIASES = {'diffusion_models': {'diffusion_models', 'unet'},
                     'text_encoders': {'text_encoders', 'clip'}}
VAE_BUILTINS = {'pixel_space', 'taesd', 'taesdxl', 'taesd3', 'taef1', 'taef2'}


class LibraryRequestError(ValueError):
    """A deliberate, public-facing validation message, never an upstream error."""


def read_json(path):
    if path.stat().st_size > MAX_WORKFLOW_BYTES:
        raise ValueError('Workflow exceeds 8 MiB')
    return json.loads(path.read_text(encoding='utf-8'))


def graph_details(graph):
    if not isinstance(graph, dict):
        raise ValueError('工作流必須是 JSON 物件')
    if isinstance(graph.get('nodes'), list):
        nodes = active_nodes(graph)
        types = sorted({str(n.get('type')) for n in nodes if isinstance(n, dict) and n.get('type')})
        return {'format': 'visual', 'node_types': types, 'node_count': len(nodes)}
    nodes = [v for v in graph.values() if isinstance(v, dict) and 'class_type' in v]
    if nodes and len(nodes) == len(graph):
        return {'format': 'api', 'node_types': sorted({str(n['class_type']) for n in nodes}), 'node_count': len(nodes)}
    raise ValueError('不是 ComfyUI visual / API 工作流')


def _model_name(value, kind):
    if not isinstance(value, str) or len(value) > 240:
        return None
    name = value.replace('\\', '/')
    if kind == 'VAELoader' and name in VAE_BUILTINS:
        return name
    if (not re.fullmatch(r'[\w .()/+-]+\.(?:safetensors|ckpt|gguf|pth|pt|bin|sft)', name, re.I)
        or name.startswith(('/', '~')) or '..' in name.split('/')
        or re.search(r'(?:sk-|aip_|gh[pousr]_|hf_)[A-Za-z0-9_-]{16,}', name)):
        return None
    return name


def _model_selections(graph):
    """Yield known fields, marking connected or unsafe selections as unknown."""
    for node in active_nodes(graph):
        kind = node['type']
        if kind not in MODEL_LOADERS:
            continue
        directory, fields = MODEL_LOADERS[kind]
        raw = node.get('inputs', {}) if node.get('class_type') else node.get('widgets_values', [])
        inputs = node.get('inputs', [])
        connected = {item.get('name') for item in inputs
                     if isinstance(item, dict) and item.get('link') is not None} if isinstance(inputs, list) else set()
        for field, index in fields.items():
            value = raw.get(field) if isinstance(raw, dict) else raw[index] if isinstance(raw, list) and index < len(raw) else None
            yield directory, kind, field, None if field in connected else _model_name(value, kind)


def graph_models(graph):
    """Describe known loader fields, without publishing absolute or opaque paths."""
    result, seen = [], set()
    for directory, kind, _, name in _model_selections(graph):
        if name is not None and (directory, name) not in seen:
            result.append({'name': name, 'node_type': kind, 'directory_hint': directory})
            seen.add((directory, name))
    return result


def _model_key(name, directory):
    """Normalize documented directory aliases, never arbitrary basename matches."""
    parts = name.replace('\\', '/').split('/')
    aliases = DIRECTORY_ALIASES.get(directory, {directory})
    if len(parts) > 2 and parts[0] == 'models' and parts[1] in aliases:
        parts = parts[1:]
    if len(parts) > 1 and parts[0] in aliases:
        parts = parts[1:]
    return '/'.join(parts)


def dependency_checks(entry, graph, object_info):
    """Check the current node/model inventory; this never certifies execution."""
    if not isinstance(object_info, dict):
        raise ValueError('Invalid ComfyUI object inventory')
    missing, checked, unknown = [], 0, 0
    seen_missing = set()
    for directory, kind, field, name in _model_selections(graph):
        schema = object_info.get(kind, {})
        inputs = schema.get('input', {}) if isinstance(schema, dict) else {}
        descriptor = None
        if isinstance(inputs, dict):
            for section in ('required', 'optional'):
                fields = inputs.get(section, {})
                if isinstance(fields, dict) and field in fields:
                    descriptor = fields[field]
                    break
        choices = combo_options(descriptor)
        if name is None or choices is None:
            unknown += 1
            continue
        checked += 1
        available = {_model_key(choice, directory) for choice in choices}
        if _model_key(name, directory) not in available and (kind, directory, name) not in seen_missing:
            missing.append({'name': name, 'node_type': kind, 'directory_hint': directory})
            seen_missing.add((kind, directory, name))
    # Unknown custom loader layouts must not be represented as fully checked.
    # Their arbitrary widget strings and object_info choices stay private.
    for node in active_nodes(graph):
        kind = node['type']
        if kind not in MODEL_LOADERS and 'loader' in kind.lower():
            unknown += 1
    absent_nodes = missing_nodes(entry, object_info)
    return {'missing_nodes': absent_nodes, 'missing_models': missing,
            'models_checked': checked, 'model_checks_unknown': unknown,
            'dependency_status': 'missing' if absent_nodes or missing else 'partial' if unknown else 'present',
            'gpu_tested': False}


def safe_path(relative, root=ROOT):
    candidate = root / str(relative)
    if candidate.is_symlink() or not candidate.resolve().is_relative_to((root / 'workflows').resolve()):
        raise ValueError('工作流路徑無效')
    if any(p.startswith('.') or p == 'user-saved' for p in candidate.relative_to(root).parts):
        raise ValueError('這個路徑不是共用工作流')
    if candidate.suffix.lower() != '.json' or not candidate.is_file():
        raise ValueError('工作流不存在')
    return candidate


def catalog(root=ROOT, *, include_discovered=True, object_info=None):
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
            entry['license'] = entry.get('license') or source.get('license') or '未宣告再散布授權'
            imported = by_url.get(entry.get('upstream_url'), {})
            if imported and not entry.get('local_path'):
                entry['local_path'] = imported['destination']
                entry['license'] = imported['license']
            relative = entry.get('local_path')
            graph = None
            entry['available'] = False
            if relative:
                try:
                    path = safe_path(relative, root)
                    graph = read_json(path)
                    entry.update(graph_details(graph))
                    if not entry.get('models'):
                        entry['models'] = graph_models(graph)
                    entry['available'] = True
                    covered.add(path.resolve())
                except (ValueError, OSError):
                    entry['status'] = 'file-unavailable'
            entry['kind'] = 'workflow'
            entry['guide'] = enrich_entry(entry, graph)
            if entry['available'] and object_info is not None:
                entry.update(dependency_checks(entry, graph, object_info))
            entries.append(entry)
    paths = (root / 'workflows').rglob('*.json') if include_discovered else (root / path for path in by_path)
    for path in sorted(paths):
        if path.resolve() in covered:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            safe_path(relative, root)
            graph = read_json(path)
            details = graph_details(graph)
        except (ValueError, OSError):
            continue
        entry = {
            'id': 'local-' + hashlib.sha256(relative.encode()).hexdigest()[:20],
            'title': path.stem, 'category': path.parent.name if path.parent.name != 'workflows' else 'local',
            'source': 'workspace', 'kind': 'workflow', 'available': True,
            'local_path': relative, 'tags': [], 'status': 'not-gpu-qualified', **details,
            'models': graph_models(graph),
            **({'upstream_url': by_path[relative].get('source_url'),
                'license': by_path[relative].get('license'),
                'source': urllib.parse.urlsplit(by_path[relative].get('source_url', '')).path.split('/')[1],
                'notes': by_path[relative].get('modifications', [])} if relative in by_path else {}),
        }
        entry['guide'] = enrich_entry(entry, graph)
        if object_info is not None:
            entry.update(dependency_checks(entry, graph, object_info))
        entries.append(entry)
    recipes_path = root / 'manifests/community_recipes/recipes.json'
    if recipes_path.is_file():
        data = read_json(recipes_path)
        recipes = data if isinstance(data, list) else data.get('recipes', [])
        for raw in recipes:
            entry = {**raw, 'id': 'recipe-' + str(raw['id']), 'kind': 'recipe', 'available': False}
            entry['guide'] = enrich_entry(entry)
            entries.append(entry)
    return entries


def selected(entry_id, root=ROOT):
    entry = next((e for e in catalog(root) if e['id'] == entry_id and e.get('available')), None)
    if entry is None:
        raise LibraryRequestError('這個項目是參考資料，沒有可載入的本機 JSON')
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
            info = None
            connection = {'checked': False}
            if (params.get('check') or [''])[0] == '1':
                try:
                    info = server.request_json('/object_info')
                    if not isinstance(info, dict):
                        raise ValueError('Invalid ComfyUI object inventory')
                    connection = {'checked': True, 'connected': True}
                except Exception:
                    info = None
                    connection = {'checked': True, 'connected': False, 'error': '無法連線 ComfyUI，請先啟動服務。'}
            entries = catalog(object_info=info) if info is not None else catalog()
            browser_url = os.environ.get('COMFYUI_BROWSER_URL', 'http://127.0.0.1:8188')
            parsed = urllib.parse.urlsplit(browser_url)
            if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
                browser_url = 'http://127.0.0.1:8188'
            handler.send_json({'items': entries, 'connection': connection, 'comfy_url': browser_url}, cache_control='no-store')
    except (ValueError, OSError) as error:
        message = str(error) if isinstance(error, LibraryRequestError) else '無法讀取工作流資料；請確認來源檔案與格式後重試。'
        handler.send_json({'ok': False, 'error': message}, 400)
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
            raise LibraryRequestError('API 格式請下載後在 ComfyUI 匯入；原生編輯器支援視覺工作流。')
        server = sys.modules[handler.__class__.__module__]
        digest = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()[:12]
        filename = 'workflows/webui-library/' + entry['id'] + '-' + digest + '.json'
        # Entry IDs are catalog-controlled, but keep the destination safe for
        # manifests supplied by repository contributors as well.
        if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in entry['id']):
            raise LibraryRequestError('工作流 ID 無效')
        info = server.request_json('/object_info')
        checks = dependency_checks(entry, graph, info)
        target = '/userdata/' + urllib.parse.quote(filename, safe='')
        already_imported = False
        try:
            server.request_json(target + '?overwrite=false', graph, method='POST')
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise
            existing = server.request_json(target)
            if json.dumps(existing, sort_keys=True) != json.dumps(graph, sort_keys=True):
                handler.send_json({'ok': False, 'error': '同名工作流已存在且內容不同；請在 ComfyUI 保留或重新命名既有檔案，再重試。'}, 409)
                return True
            already_imported = True
        handler.send_json({'ok': True, 'filename': filename, **checks,
                           'already_imported': already_imported,
                           'message': '相同工作流已在 ComfyUI 的 webui-library 資料夾；請從側欄開啟。' if already_imported else
                                      '已加入 ComfyUI 的 webui-library 資料夾；請在工作流側欄開啟並檢查模型後執行。'})
    except Exception as error:
        # Upstream failures may include private endpoint details. Return a small
        # deliberate error, never raw credentials, URLs, or HTTP response bodies.
        message = str(error) if isinstance(error, LibraryRequestError) else 'ComfyUI 載入失敗；請確認服務與工作流仍可用後重試。'
        handler.send_json({'ok': False, 'error': message}, 400)
    return True
