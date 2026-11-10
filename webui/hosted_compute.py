"""Owner-scoped Comfy API v2 and RunningHub workflow execution.

Only explicitly configured recipes execute. Every creative setting and file is
bound or checked against an explicit fixed value. Provider create is attempted
once after a durable dispatch marker; recovery never repeats it. Job completion
means bounded media bytes were downloaded, decoded/probed, and hashed locally.
See docs/hosted-compute.md for the HTTP evidence and billing limitations.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit
import uuid

try:
    from .hosted_compute_http import HTTPS, TransportError, public_url
    from .colab_service import validate_media
    from . import minimax_h3_product as H3
    from .qwen_image21 import compile_character_prompt
except ImportError:
    from hosted_compute_http import HTTPS, TransportError, public_url
    from colab_service import validate_media
    import minimax_h3_product as H3
    from qwen_image21 import compile_character_prompt

PROVIDERS = {'comfy_cloud': 'https://cloud.comfy.org', 'runninghub': 'https://www.runninghub.ai'}
CURRENCY = {'comfy_cloud': 'comfy_credits', 'runninghub': 'runninghub_coins'}
CAP = {'comfy_cloud': 'max_comfy_credits', 'runninghub': 'max_runninghub_coins'}
TERMINAL = {'completed', 'failed', 'cancelled', 'rejected'}
MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
CONTEXT = {'_characterId', '_characterName', '_characterActionId', '_characterVariantId',
           '_characterVisualClass', '_characterReference', '_characterGender', '_characterAppearance'}
SUFFIX = {'.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
          '.mp4': 'video', '.mov': 'video', '.webm': 'video',
          '.wav': 'audio', '.mp3': 'audio', '.flac': 'audio', '.m4a': 'audio'}
WARNING = '費用依設定的估計值檢查；供應商未提供已驗證的單項帳單硬上限，取消前的運算仍可能收費。'


class HostedComputeError(ValueError):
    def __init__(self, message, *, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def fail(message, code='invalid_request', status=400):
    raise HostedComputeError(message, code=code, status=status)


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError):
        fail('設定必須是有效的 JSON 值')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def identifier(value, label='識別碼'):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        fail(label + '格式不正確')
    return value


def request_uuid(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        fail('請求識別碼必須是標準 UUID')
    return value


def endpoint(provider, value):
    if provider not in PROVIDERS:
        fail('不支援的託管供應商')
    try:
        parts = public_url(value)
        host = parts.hostname
        valid = (host == 'www.runninghub.ai' if provider == 'runninghub' else
                 host == 'cloud.comfy.org' or re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.run\.comfy\.app', host))
        if not valid or parts.path not in ('', '/') or parts.query or parts.port:
            raise ValueError()
    except (TransportError, ValueError):
        fail('只接受供應商官方 HTTPS 網址；Comfy 可指定專用部署網址')
    return 'https://' + host


def _directory(path):
    if path.is_symlink():
        fail('本機資料目錄不安全', status=500)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _write(path, value):
    data = value if isinstance(value, bytes) else encoded(value)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    if path.is_symlink() or path.stat().st_size > 8 * MAX_JSON:
        fail('本機狀態檔不安全', status=500)
    try:
        return json.loads(path.read_bytes())
    except (ValueError, OSError):
        fail('無法讀取本機工作狀態', status=500)


@contextmanager
def _lock(path, *, blocking=True):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def _recipe(provider, recipe):
    allowed = {'feature_id', 'recipe_id', 'label', 'output_kind', 'workflow', 'workflow_id',
               'bindings', 'fixed_settings', 'estimated_cost', 'instance_type', 'partner_nodes'}
    if type(recipe) is not dict or set(recipe) - allowed:
        fail('配方含有不支援的欄位')
    result = copy.deepcopy(recipe)
    for key in ('feature_id', 'recipe_id'):
        identifier(result.get(key), key)
    if type(result.get('label')) is not str or not 1 <= len(result['label']) <= 160 or any(ord(c) < 32 for c in result['label']):
        fail('配方需要 1–160 字的名稱')
    if result.get('output_kind') not in {'image', 'video', 'audio'} or not number(result.get('estimated_cost')):
        fail('配方需要成果類型與正數費用估計')
    if type(result.get('fixed_settings', {})) is not dict:
        fail('固定設定必須是物件')
    for key in result.get('fixed_settings', {}):
        identifier(key)
        if key.startswith('_'):
            fail('配方不能覆寫角色授權資料')
    if provider == 'comfy_cloud':
        if set(result) & {'workflow_id', 'instance_type'}:
            fail('Comfy 配方不接受 RunningHub 欄位')
        graph = result.get('workflow')
        if type(graph) is not dict or not 1 <= len(graph) <= 512 or 'nodes' in graph:
            fail('需要 Comfy API 格式工作流程，最多 512 個節點')
        for node_id, node in graph.items():
            identifier(node_id, '節點識別碼')
            if type(node) is not dict or type(node.get('class_type')) is not str or type(node.get('inputs')) is not dict:
                fail('工作流程節點格式不正確')
        if type(result.get('partner_nodes', False)) is not bool:
            fail('partner_nodes 必須是布林值')
    else:
        if set(result) & {'workflow', 'partner_nodes'}:
            fail('RunningHub 配方不接受 Comfy 欄位')
        identifier(result.get('workflow_id'), 'RunningHub workflow_id')
        if result.get('instance_type', 'default') not in {'default', 'plus', 'ultra'}:
            fail('RunningHub instance_type 不支援')
    bindings = result.get('bindings')
    if type(bindings) is not list or not 1 <= len(bindings) <= 128:
        fail('配方需要 1–128 個輸入映射')
    targets, source_defaults = set(), {}
    for binding in bindings:
        if type(binding) is not dict or not {'source', 'node_id', 'input'} <= set(binding) or set(binding) - {'source', 'node_id', 'input', 'default', 'value_map'}:
            fail('輸入映射需要 source、node_id、input；只支援 default 與 value_map 選項')
        source = binding['source']
        if type(source) is not str or not re.fullmatch(r'prompt|(?:settings|files)\.[A-Za-z][A-Za-z0-9_-]{0,127}', source):
            fail('輸入映射來源不支援')
        if ('default' in binding or 'value_map' in binding) and not source.startswith('settings.'):
            fail('只有設定欄位可使用預設值與有限值映射')
        def scalar(value):
            return value is None or type(value) in (str, bool, int) or (type(value) is float and math.isfinite(value))
        if 'default' in binding:
            if not scalar(binding['default']):
                fail('映射預設值必須是有限的純量')
            if source in source_defaults and encoded(source_defaults[source]) != encoded(binding['default']):
                fail('同一個設定來源不能指定不同預設值')
            source_defaults[source] = binding['default']
        if 'value_map' in binding:
            mapping = binding['value_map']
            if type(mapping) is not list or not 1 <= len(mapping) <= 128:
                fail('value_map 必須是 1–128 組 from/to 清單')
            seen = set()
            for pair in mapping:
                if type(pair) is not dict or set(pair) != {'from', 'to'} or not all(scalar(v) for v in pair.values()):
                    fail('value_map 只接受有限純量的 from/to')
                token = encoded(pair['from'])
                if token in seen:
                    fail('value_map 來源值不可重複')
                seen.add(token)
            if 'default' in binding and encoded(binding['default']) not in seen:
                fail('映射預設值不在 value_map 接受範圍內')
        target = (identifier(binding['node_id']), identifier(binding['input']))
        if target in targets:
            fail('同一個節點輸入不能映射兩次')
        targets.add(target)
        if provider == 'comfy_cloud' and (target[0] not in graph or target[1] not in graph[target[0]]['inputs']):
            fail('輸入映射找不到對應的工作流程節點欄位')
    if len(encoded(result)) > MAX_JSON:
        fail('配方超過 4 MiB')
    return result


def _prompt(feature, settings, slots):
    """Reuse product character/section prose; do not borrow another engine's graph."""
    style = settings.get('_characterVisualClass') or settings.get('visualStyle', '')
    if style not in ('', 'anime', 'realistic') or (settings.get('_characterVisualClass') and settings.get('visualStyle') and settings['_characterVisualClass'] != settings['visualStyle']):
        fail('角色畫風與生成設定不一致')
    used = {'visualStyle'} if 'visualStyle' in settings else set()
    if feature.startswith('h3-'):
        character = bool(settings.get('_characterId') or settings.get('_characterVisualClass') or
                         any(key in settings for key in ('description', 'motionProfile')))
        if character and not style:
            fail('角色影片需要已驗證的角色畫風')
        style_prose = ('Use a consistent 2D anime illustration style across characters, clothing and scenery.' if style == 'anime'
                       else 'Use a consistent realistic photographic style across people, clothing and scenery.') if style else ''
        if 'prompt' in settings:
            if character or any(key in settings for key in (*H3.T2V_HEADINGS, *H3.REF2VA_HEADINGS, 'description', 'motionProfile')):
                fail('影片角色與結構化描述不可被單段提示取代')
            text = settings['prompt']
            if style_prose:
                text = style_prose + '\n\n' + str(text)
            used.add('prompt')
        else:
            uploads = {slot: slot + ('.mp4' if slot.startswith('video') else '.wav' if slot.startswith('audio') else '.png') for slot in slots}
            canonical = {**settings, 'seed': 0, 'acceleration': 'stock'}
            if style_prose and not character:
                heading = 'detailed_description' if feature == 'h3-reference-video' else 'integrated_multimodal_description'
                if type(canonical.get(heading)) is not str or not canonical[heading].strip():
                    fail('請保留完整的影片結構化描述')
                canonical[heading] = style_prose + '\n\n' + canonical[heading]
            try:
                graph = H3.compile_product_prompt(feature_id=feature, visual_class=style if character else None,
                    settings=canonical, uploads=uploads, output_token='hosted')
            except ValueError:
                fail('工作流程不能保留完整的影片或角色描述')
            text = graph['5']['inputs']['prompt']
            used.update(key for key in (*H3.T2V_HEADINGS, *H3.REF2VA_HEADINGS, 'description', 'motionProfile') if key in settings)
    else:
        present = [key for key in ('positive', 'instruction', 'prompt') if key in settings]
        if len(present) != 1:
            fail('配方需要單一明確的文字描述來源')
        used.update(present)
        text = settings[present[0]]
        if feature == 'character-photoshoot':
            try:
                graph = compile_character_prompt({'instruction': text, 'visualStyle': style}, {'image': 'reference.png'}, seed=0, prefix='hosted')
                text = graph['4']['inputs']['prompt']
            except ValueError:
                fail('角色寫真描述不完整')
        elif style:
            text = ('Use a consistent 2D anime illustration style across characters, clothing and scenery.' if style == 'anime'
                    else 'Use a consistent realistic photographic style across people, clothing and scenery.') + '\n\n' + str(text)
    if type(text) is not str or not text.strip() or len(text) > 24000 or '\0' in text:
        fail('描述必須為 1–24000 字')
    return text, used


def _compile(recipe, request, uploaded=None, *, run_seed=0):
    if type(request) is not dict or set(request) - {'featureId', 'settings', 'files'} or request.get('featureId') != recipe['feature_id']:
        fail('生成請求與配方不一致')
    settings, files = request.get('settings', {}), request.get('files', {})
    if type(settings) is not dict or type(files) is not dict or len(files) > 8 or len(encoded(request)) > MAX_JSON:
        fail('生成設定或素材格式不正確')
    if any(type(key) is not str or (key.startswith('_') and key not in CONTEXT) for key in settings):
        fail('生成請求含有未授權的內部欄位')
    for key, value in settings.items():
        if key not in CONTEXT:
            identifier(key, '設定名稱')
        if key not in CONTEXT and not (type(value) in (str, int, float, bool) or value is None):
            fail('配方只接受明確的純量生成設定')
    for slot in files:
        identifier(slot, '素材欄位')
    settings = dict(settings)
    # Defaults are explicit recipe values, applied before prompt compilation so
    # H3 alignment prose and latent frame counts use the same effective inputs.
    # Presence (including null/empty/false/zero) always wins over a default.
    for binding in recipe['bindings']:
        if 'default' in binding and binding['source'].startswith('settings.'):
            settings.setdefault(binding['source'][9:], copy.deepcopy(binding['default']))
    mapped, file_keys, values, consumed = set(), set(), [], set()
    text = None
    if any(b['source'] == 'prompt' for b in recipe['bindings']):
        text, consumed = _prompt(recipe['feature_id'], settings, files)
    if (settings.get('_characterId') or settings.get('_characterVisualClass') or settings.get('visualStyle')) and text is None:
        fail('角色與畫風配方必須映射完整 prompt，不能只取原始文字')
    for binding in recipe['bindings']:
        source = binding['source']
        if source == 'prompt':
            value = text
        elif source.startswith('settings.'):
            key = source[9:]
            if key not in settings:
                fail('缺少配方要求的設定：' + key)
            value = settings[key]
            if 'value_map' in binding:
                candidates = [pair['to'] for pair in binding['value_map'] if encoded(pair['from']) == encoded(value)]
                if len(candidates) != 1:
                    fail('配方沒有對應這個設定值：' + key, 'setting_unsupported', 422)
                value = copy.deepcopy(candidates[0])
            if key == 'seed':
                if type(value) is str and re.fullmatch(r'-1|[0-9]{1,20}', value):
                    value = int(value)
                if type(value) is not int or not -1 <= value < 2**64:
                    fail('種子必須是 -1 或 uint64 整數')
                if value == -1:
                    value = run_seed
            mapped.add(key)
        else:
            key = source[6:]
            if key not in files:
                fail('缺少配方要求的素材：' + key)
            value = uploaded[key] if uploaded is not None else None
            file_keys.add(key)
        values.append((binding, value))
    if file_keys != set(files):
        fail('配方沒有映射全部素材，不能忽略參考順序或附件')
    for key, expected in recipe.get('fixed_settings', {}).items():
        if key in settings and encoded(settings[key]) != encoded(expected):
            fail('這個配方無法保留自訂設定：' + key)
    unknown = set(settings) - CONTEXT - mapped - consumed - recipe.get('fixed_settings', {}).keys()
    if unknown:
        fail('配方沒有映射設定：' + ', '.join(sorted(unknown)), 'setting_unsupported', 422)
    return values


class HostedComputeService:
    def __init__(self, root, transport=None, *, clock=time.time, media_validator=validate_media):
        self.root = _directory(Path(root))
        self.transport = transport or HTTPS()
        self.clock, self.media_validator = clock, media_validator
        self._stop, self._thread = threading.Event(), None

    def _owner(self, owner):
        if type(owner) is not str or not owner.strip() or len(owner) > 320:
            fail('缺少登入身分', 'owner_required', 401)
        return _directory(self.root / hashlib.sha256(owner.encode()).hexdigest())

    def _provider(self, owner, provider):
        if provider not in PROVIDERS:
            fail('不支援的託管供應商')
        return _directory(self._owner(owner) / provider)

    def _config(self, folder):
        provider = folder.name
        return _read(folder / 'config.json', {'enabled': False, 'endpoint': PROVIDERS[provider], 'api_key': '', 'recipes': [], 'credential_epoch': uuid.uuid4().hex})

    def _fingerprint(self, config, recipe):
        return digest({'endpoint': config['endpoint'], 'credential_epoch': config['credential_epoch'], 'recipe': recipe})

    def _public_config(self, folder, config):
        verified = _read(folder / 'verified.json', {})
        recipes = [{**recipe, 'verified': self._fingerprint(config, recipe) in verified} for recipe in config['recipes']]
        configured = bool(config['api_key'])
        return {'provider': folder.name, 'enabled': config['enabled'], 'endpoint': config['endpoint'],
                'credential_configured': configured, 'connection_state': 'configured_unverified' if configured else 'not_configured',
                'recipes': recipes, 'currency': CURRENCY[folder.name], 'warnings': [WARNING]}

    def public_config(self, owner):
        return {'providers': [self._public_config(folder, self._config(folder)) for provider in PROVIDERS
                              for folder in [self._provider(owner, provider)]]}

    def configure(self, owner, provider, body):
        if type(body) is not dict or set(body) - {'enabled', 'endpoint', 'api_key', 'clear_api_key', 'recipes'} or len(encoded(body)) > MAX_JSON:
            fail('供應商設定格式不正確')
        folder = self._provider(owner, provider)
        with _lock(folder / '.config.lock'):
            config = self._config(folder)
            if 'enabled' in body:
                if type(body['enabled']) is not bool:
                    fail('enabled 必須是布林值')
                config['enabled'] = body['enabled']
            if 'endpoint' in body:
                config['endpoint'] = endpoint(provider, body['endpoint'])
            if type(body.get('clear_api_key', False)) is not bool:
                fail('clear_api_key 必須是布林值')
            key = body.get('api_key', '')
            if type(key) is not str or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                fail('API key 格式不正確')
            if key and body.get('clear_api_key'):
                fail('不能同時設定與清除 API key')
            if key or body.get('clear_api_key'):
                next_key = '' if body.get('clear_api_key') else key
                if next_key != config['api_key']:
                    config['credential_epoch'] = uuid.uuid4().hex
                config['api_key'] = next_key
            if 'recipes' in body:
                if type(body['recipes']) is not list or len(body['recipes']) > 64:
                    fail('最多可設定 64 個配方')
                config['recipes'] = [_recipe(provider, item) for item in body['recipes']]
                if len({r['feature_id'] for r in config['recipes']}) != len(config['recipes']) or len({r['recipe_id'] for r in config['recipes']}) != len(config['recipes']):
                    fail('每個功能與配方識別碼只能設定一次')
            _write(folder / 'config.json', config)
            return self._public_config(folder, config)

    def _inspection(self, folder, config, request, limits):
        provider = folder.name
        result = {'eligible': False, 'auto_eligible': False, 'reasons': [], 'currency': CURRENCY[provider],
                  'budget_basis': 'configured_estimate', 'warnings': [WARNING], 'cancel_supported': True}
        recipe = next((r for r in config['recipes'] if r['feature_id'] == request.get('featureId')), None) if type(request) is dict else None
        if not config['enabled'] or not config['api_key']:
            result['reasons'].append('hosted_not_configured')
        if recipe is None:
            result['reasons'].append('recipe_not_configured')
            return result, None
        result.update(recipe_id=recipe['recipe_id'], recipe_label=recipe['label'], output_kind=recipe['output_kind'], estimated_cost=recipe['estimated_cost'])
        try:
            _compile(recipe, request)
            if type(limits) is not dict or not number(limits.get(CAP[provider])) or limits[CAP[provider]] < recipe['estimated_cost']:
                fail('請設定足以涵蓋配方估計值的供應商費用上限', 'hosted_budget_exceeded')
            if type(limits.get('max_minutes')) is not int or not 1 <= limits['max_minutes'] <= 1440:
                fail('請設定 1–1440 分鐘的執行期限', 'hosted_deadline_required')
            if 'max_concurrent' in limits and (type(limits['max_concurrent']) is not int or not 1 <= limits['max_concurrent'] <= 32):
                fail('同時執行上限必須為 1–32', 'hosted_concurrency_invalid')
        except HostedComputeError as error:
            result['reasons'].append(error.code if error.code.startswith('hosted_') else 'hosted_settings_unsupported')
            result['warnings'].append(str(error))
        result['eligible'] = not result['reasons']
        result['auto_eligible'] = result['eligible'] and self._fingerprint(config, recipe) in _read(folder / 'verified.json', {})
        if result['eligible'] and not result['auto_eligible']:
            result['warnings'].append('此配方尚未有已下載驗證的成功成果；請先手動執行，才可納入自動選擇。')
        return result, recipe

    def inspect(self, owner, provider, request, limits):
        folder = self._provider(owner, provider)
        return self._inspection(folder, self._config(folder), request, limits)[0]

    def _job_path(self, folder, request_id):
        return _directory(folder / 'jobs' / request_uuid(request_id))

    def _receipt(self, job):
        result = {key: job[key] for key in ('state', 'provider_id', 'recipe_id', 'recipe_label', 'output_kind')}
        result.update(downstream_id=job['request_id'], cancel_supported=bool(job.get('remote_id')),
                      artifacts=job.get('artifacts', []), budget_basis='configured_estimate')
        if job.get('error_code'):
            result['error_code'] = job['error_code']
        return result

    def _save(self, path, job):
        job['updated_at'] = self.clock()
        _write(path / 'job.json', job)

    def _json(self, method, url, key, *, body=None, headers=None):
        response = self.transport.request(method, url, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', **(headers or {})},
            body=None if body is None else encoded(body), timeout=30, max_bytes=MAX_JSON)
        try:
            payload = json.loads(response.body)
        except (ValueError, UnicodeError):
            raise TransportError('invalid provider response')
        if type(payload) is not dict:
            raise TransportError('invalid provider response')
        return response.status, payload

    def _rh(self, job, key, operation):
        status, body = self._json('POST', job['endpoint'] + '/task/openapi/' + operation, key,
            body={'apiKey': key, 'taskId': job['remote_id']})
        if status != 200 or body.get('code') != 0:
            raise TransportError('provider operation unavailable')
        return body.get('data')

    def _upload(self, provider, endpoint_url, key, path, filename):
        boundary = 'webcomfy-' + uuid.uuid4().hex
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        fields = {'content_type': mime, 'file_path': filename} if provider == 'comfy_cloud' else {'apiKey': key, 'fileType': 'input'}
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        # Comfy requires file LAST: fields after it are not processed.
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode(), path.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()])
        response = self.transport.request('POST', endpoint_url + ('/api/v2/assets' if provider == 'comfy_cloud' else '/task/openapi/upload'),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'multipart/form-data; boundary=' + boundary},
            body=b''.join(parts), timeout=60, max_bytes=MAX_JSON)
        try:
            data = json.loads(response.body)
            if response.status not in (200, 201) or type(data) is not dict:
                raise ValueError()
            if provider == 'comfy_cloud':
                asset_id = identifier(data.get('id'), '素材識別碼')
                return {'__type': 'core/ASSET', 'info': {'id': asset_id}}
            if data.get('code') != 0:
                raise ValueError()
            filename = data['data']['fileName']
            if type(filename) is not str or not re.fullmatch(r'[A-Za-z0-9_/-]+\.[A-Za-z0-9]+', filename) or '..' in filename or filename.startswith('/'):
                raise ValueError()
            return filename
        except (ValueError, KeyError, TypeError):
            raise TransportError('upload failed')

    def _prepare(self, path, request, resolved_files, provider):
        if type(resolved_files) is not dict or set(resolved_files) != set(request.get('files', {})):
            fail('素材必須先通過擁有者授權與路徑解析', 'inputs_invalid', 422)
        inputs, total = {}, 0
        for slot, source in resolved_files.items():
            source = Path(source)
            suffix = source.suffix.lower()
            if suffix not in SUFFIX:
                fail('素材格式不支援', 'inputs_invalid', 422)
            fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= (30_000_000 if provider == 'runninghub' else MAX_INPUT):
                    fail('素材大小不支援', 'inputs_invalid', 422)
                data = stream.read(MAX_INPUT + 1)
            total += len(data)
            if total > MAX_INPUT or len(data) != info.st_size:
                fail('素材總量超過 32 MiB 或在讀取期間變更', 'inputs_invalid', 422)
            sha = hashlib.sha256(data).hexdigest()
            name = slot + '-' + sha[:16] + suffix
            target = path / name
            _write(target, data)
            try:
                media = self.media_validator(target)
            except Exception:
                fail('素材無法解碼或檢查', 'inputs_invalid', 422)
            if media.get('kind') != SUFFIX[suffix]:
                fail('素材類型不符', 'inputs_invalid', 422)
            inputs[slot] = {'name': name, 'size': len(data), 'sha256': sha}
        return inputs

    def submit(self, owner, provider, request_id, request, limits, *, resolved_files=None):
        try:
            return self._submit(owner, provider, request_id, request, limits, resolved_files=resolved_files)
        except HostedComputeError as error:
            if error.status >= 500:
                raise
            # These authored exceptions represent validation before create. I/O
            # errors are deliberately not caught or reclassified as rejection.
            try:
                from .compute_routing import ComputeRejected
            except ImportError:
                from compute_routing import ComputeRejected
            raise ComputeRejected(str(error), code=error.code, status=error.status) from error

    def _submit(self, owner, provider, request_id, request, limits, *, resolved_files=None):
        folder = self._provider(owner, provider)
        path = self._job_path(folder, request_id)
        with _lock(path / '.lock'):
            existing = _read(path / 'job.json')
            # Same request UUID is permanently bound, including uncertain outcomes.
            request_hash = digest({'request': request, 'limits': limits})
            if existing:
                if existing['request_hash'] != request_hash:
                    fail('這個請求識別碼已綁定不同內容', 'request_conflict', 409)
                if type(resolved_files or {}) is not dict or set(resolved_files or {}) != set(existing['inputs']):
                    fail('重送的素材欄位不同', 'request_conflict', 409)
                for slot, source in (resolved_files or {}).items():
                    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    with os.fdopen(fd, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size != existing['inputs'][slot]['size']:
                            fail('重送的素材內容不同', 'request_conflict', 409)
                        data = stream.read(MAX_INPUT + 1)
                    if hashlib.sha256(data).hexdigest() != existing['inputs'][slot]['sha256']:
                        fail('重送的素材內容不同', 'request_conflict', 409)
                return self._receipt(existing)
            with _lock(folder / '.config.lock'):
                config = self._config(folder)
                inspection, recipe = self._inspection(folder, config, request, limits)
                if not inspection['eligible']:
                    fail('；'.join(inspection['reasons']), 'provider_incompatible', 422)
                active = sum(1 for f in (folder / 'jobs').glob('*/job.json') if _read(f, {}).get('state') not in TERMINAL)
                if active >= limits.get('max_concurrent', 1):
                    fail('此供應商已達同時執行上限', 'concurrency_limit', 409)
                inputs = self._prepare(path, request, resolved_files or {}, provider)
                now = self.clock()
                job = {'state': 'queued', 'phase': 'preparing', 'provider_id': provider, 'request_id': request_id,
                       'request_hash': request_hash, 'recipe_id': recipe['recipe_id'], 'recipe_label': recipe['label'],
                       'recipe': recipe, 'output_kind': recipe['output_kind'], 'endpoint': config['endpoint'],
                       'fingerprint': self._fingerprint(config, recipe), 'inputs': inputs, 'request': request,
                       'content_hash': digest({'request': request, 'inputs': inputs}),
                       'created_at': now, 'deadline': now + limits['max_minutes'] * 60, 'artifacts': [], 'cancel_requested': False}
                self._save(path, job)
            try:
                uploaded = {}
                for slot, value in inputs.items():
                    if self.clock() >= job['deadline'] or (path / 'cancel.json').exists():
                        job.update(state='cancelled', phase='done', error_code='deadline_before_submit')
                        self._save(path, job)
                        return self._receipt(job)
                    uploaded[slot] = self._upload(provider, job['endpoint'], config['api_key'], path / value['name'], value['name'])
                values = _compile(recipe, request, uploaded, run_seed=int(hashlib.sha256(request_id.encode()).hexdigest()[:16], 16))
                if provider == 'comfy_cloud':
                    graph = copy.deepcopy(recipe['workflow'])
                    for binding, value in values:
                        graph[binding['node_id']]['inputs'][binding['input']] = value
                    body = {'workflow': graph}
                    if recipe.get('partner_nodes'):
                        body['extra_data'] = {'api_key_comfy_org': config['api_key']}
                    if job['endpoint'] != PROVIDERS['comfy_cloud']:
                        body['metadata'] = {'webcomfy_request': request_id}
                    url, headers = job['endpoint'] + '/api/v2/jobs', {'Idempotency-Key': request_id}
                else:
                    body = {'apiKey': config['api_key'], 'workflowId': recipe['workflow_id'], 'addMetadata': False,
                            'instanceType': recipe.get('instance_type', 'default'),
                            'nodeInfoList': [{'nodeId': b['node_id'], 'fieldName': b['input'], 'fieldValue': v} for b, v in values]}
                    url, headers = job['endpoint'] + '/task/openapi/create', {}
                if self.clock() >= job['deadline'] or (path / 'cancel.json').exists():
                    job.update(state='cancelled', phase='done', error_code='deadline_before_submit')
                    self._save(path, job)
                    return self._receipt(job)
                # Commit before crossing the only non-idempotent paid boundary.
                job.update(phase='dispatching', state='submission_uncertain')
                self._save(path, job)
                status, response = self._json('POST', url, config['api_key'], body=body, headers=headers)
                if provider == 'comfy_cloud':
                    if status == 201:
                        job['remote_id'] = identifier(response.get('id'), '遠端工作識別碼')
                    elif status in (400, 401, 402, 403, 404, 422, 429) and response.get('error', {}).get('code') != 'idempotency_key_reuse':
                        job.update(state='rejected', phase='done', error_code='provider_rejected')
                    else:
                        raise TransportError('submission outcome unknown')
                else:
                    if status == 200 and type(response.get('code')) is int and response['code'] == 0:
                        job['remote_id'] = identifier(response.get('data', {}).get('taskId'), '遠端工作識別碼')
                    elif status == 200 and response.get('code') in {301, 380, 412, 415, 416, 421, 433, 435, 436, 801, 802, 803, 806, 810, 811, 812, 901}:
                        job.update(state='rejected', phase='done', error_code='provider_rejected')
                    else:
                        # Unknown RunningHub codes may describe accepted/queued tasks.
                        # Do not infer no-side-effect rejection from undocumented codes.
                        raise TransportError('submission outcome unknown')
                if job.get('remote_id'):
                    job.update(state='queued', phase='accepted')
                self._save(path, job)
                if provider == 'comfy_cloud' and job.get('remote_id'):
                    self._capture_links(job, response)
                    self._save(path, job)
                return self._receipt(job)
            except Exception:
                if job['phase'] == 'preparing':
                    job.update(state='failed', phase='done', error_code='input_upload_failed')
                elif not job.get('remote_id'):
                    job.update(state='submission_uncertain', error_code='submission_outcome_unknown')
                self._save(path, job)
                return self._receipt(job)

    def _same_origin(self, job, url):
        resolved = urljoin(job['endpoint'] + '/', url)
        public_url(resolved)
        if urlsplit(resolved).netloc != urlsplit(job['endpoint']).netloc:
            raise TransportError('provider link changed origin')
        return resolved

    def _capture_links(self, job, response):
        links = response.get('urls', {})
        for key in ('self', 'cancel'):
            if key in links:
                job[key + '_url'] = self._same_origin(job, links[key])

    def _download(self, job, url, key, max_bytes):
        url = urljoin(job['endpoint'] + '/', url)
        original = urlsplit(job['endpoint']).netloc
        for _ in range(4):
            public_url(url)
            auth = {'Authorization': 'Bearer ' + key} if job['provider_id'] == 'comfy_cloud' and urlsplit(url).netloc == original else {}
            response = self.transport.request('GET', url, headers=auth, timeout=60, max_bytes=max_bytes)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get('location')
                if type(location) is not str:
                    raise TransportError('redirect without location')
                url = urljoin(url, location)
                continue
            if response.status != 200 or not 0 < len(response.body) <= max_bytes:
                raise TransportError('output download unavailable')
            return response.body
        raise TransportError('too many redirects')

    def _collect(self, folder, path, job, outputs, key):
        if type(outputs) is not list or not 1 <= len(outputs) <= 16:
            raise TransportError('output count unsupported')
        artifacts, total = [], 0
        for index, output in enumerate(outputs):
            if type(output) is not dict:
                raise TransportError('invalid output')
            if job['provider_id'] == 'comfy_cloud':
                url, filename, size = output.get('url'), output.get('name'), output.get('size_bytes')
                if output.get('type') != job['output_kind'] or type(size) is not int or size <= 0:
                    raise TransportError('output kind unsupported')
            else:
                url, size = output.get('fileUrl'), None
                filename = 'output.' + str(output.get('fileType', ''))
            suffix = Path(filename).suffix.lower() if type(filename) is str else ''
            if SUFFIX.get(suffix) != job['output_kind'] or type(url) is not str:
                raise TransportError('output format unsupported')
            name = f'output-{index + 1}' + suffix
            target = path / name
            data = self._download(job, url, key, MAX_OUTPUT - total)
            if size is not None and len(data) != size:
                raise TransportError('output length mismatch')
            total += len(data)
            _write(target, data)
            try:
                media = self.media_validator(target)
                if media.get('kind') != job['output_kind']:
                    raise ValueError()
            except Exception:
                target.unlink(missing_ok=True)
                raise TransportError('output media invalid')
            artifacts.append({'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'mime_type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
                'url': f"/api/compute/hosted/{job['provider_id']}/{job['request_id']}/files/{name}"})
        job.update(artifacts=artifacts, state='completed', phase='done')
        self._save(path, job)
        with _lock(folder / '.config.lock'):
            verified = _read(folder / 'verified.json', {})
            verified[job['fingerprint']] = self.clock()
            # Old config generations are not useful forever.
            _write(folder / 'verified.json', dict(sorted(verified.items(), key=lambda item: item[1])[-256:]))

    def _recover_remote(self, job, key):
        if job['provider_id'] != 'comfy_cloud' or job['endpoint'] == PROVIDERS['comfy_cloud']:
            return
        url = job['endpoint'] + '/api/v2/jobs?' + urlencode({'metadata[webcomfy_request]': job['request_id'], 'limit': 2})
        status, data = self._json('GET', url, key)
        if status != 200:
            raise TransportError('lookup unavailable')
        jobs = data.get('jobs')
        if type(jobs) is not list:
            raise TransportError('invalid lookup')
        matches = [item for item in jobs if type(item) is dict and item.get('metadata', {}).get('webcomfy_request') == job['request_id']]
        if len(matches) == 1 and not data.get('next_cursor'):
            job.update(remote_id=identifier(matches[0].get('id')), phase='accepted', state='queued')

    def _poll(self, folder, path, job):
        if job['state'] in TERMINAL:
            return self._receipt(job)
        config = self._config(folder)
        key = config['api_key']
        if not key:
            job['error_code'] = 'credential_required_for_recovery'
            self._save(path, job)
            return self._receipt(job)
        try:
            if not job.get('remote_id'):
                if job['phase'] == 'preparing':
                    job.update(state='failed', phase='done', error_code='interrupted_before_submit')
                else:
                    self._recover_remote(job, key)
                self._save(path, job)
                if not job.get('remote_id'):
                    return self._receipt(job)
            if self.clock() >= job['deadline'] or (path / 'cancel.json').exists():
                job['cancel_requested'] = True
            self._save(path, job)
            if job['provider_id'] == 'comfy_cloud':
                self_url = job.get('self_url', job['endpoint'] + '/api/v2/jobs/' + quote(job['remote_id'], safe=''))
                if job['cancel_requested']:
                    cancel_url = job.get('cancel_url', self_url + '/cancel')
                    try:
                        self._json('POST', cancel_url, key)
                    except TransportError:
                        pass  # Poll remains authoritative after an uncertain cancel.
                status, data = self._json('GET', self_url, key)
                if status != 200 or data.get('id') != job['remote_id']:
                    raise TransportError('status unavailable')
                self._capture_links(job, data)
                state = {'queued': 'queued', 'running': 'running', 'canceling': 'cancelling',
                         'canceled': 'cancelled', 'failed': 'failed', 'expired': 'failed', 'succeeded': 'collecting'}.get(data.get('status'))
                outputs = data.get('outputs')
            else:
                if job['cancel_requested']:
                    try:
                        self._rh(job, key, 'cancel')
                    except TransportError:
                        pass  # A completed task can reject cancel; still reconcile it.
                # Official integration guide deprecates /status. /outputs is
                # authoritative: 804 running, 813 queued, 805 interrupted/error.
                status, data = self._json('POST', job['endpoint'] + '/task/openapi/outputs', key,
                    body={'apiKey': key, 'taskId': job['remote_id']})
                if status != 200 or type(data.get('code')) is not int:
                    raise TransportError('status unavailable')
                state = {0: 'collecting', 804: 'running', 813: 'queued', 805: 'failed'}.get(data['code'])
                outputs = data.get('data') if state == 'collecting' else None
            if state is None:
                raise TransportError('unknown provider state')
            job['state'] = 'cancelling' if job['cancel_requested'] and state in {'queued', 'running'} else state
            job.pop('error_code', None)
            self._save(path, job)
            if state == 'collecting':
                self._collect(folder, path, job, outputs, key)
            elif state in TERMINAL:
                job['phase'] = 'done'
                self._save(path, job)
        except Exception:
            job['error_code'] = 'provider_recovery_pending'
            self._save(path, job)
        return self._receipt(job)

    def status(self, owner, provider, request_id):
        folder = self._provider(owner, provider)
        path = self._job_path(folder, request_id)
        with _lock(path / '.lock'):
            job = _read(path / 'job.json')
            if job is None:
                fail('找不到這個擁有者的工作', 'not_found', 404)
            return self._poll(folder, path, job)

    def cancel(self, owner, provider, request_id):
        folder = self._provider(owner, provider)
        path = self._job_path(folder, request_id)
        if not (path / 'job.json').exists():
            fail('找不到這個擁有者的工作', 'not_found', 404)
        # Durable intent must not wait for a long upload to release its lock.
        _write(path / 'cancel.json', {'requested_at': self.clock()})
        with _lock(path / '.lock'):
            job = _read(path / 'job.json')
            if job is None:
                fail('找不到這個擁有者的工作', 'not_found', 404)
            if job['state'] not in TERMINAL:
                job['cancel_requested'] = True
                self._save(path, job)
            return self._poll(folder, path, job)

    def recover(self, owner, provider, request_id):
        folder = self._provider(owner, provider)
        path = self._job_path(folder, request_id)
        if not (path / 'job.json').exists():
            return None
        return self.status(owner, provider, request_id)

    def artifact(self, owner, provider, request_id, name):
        folder = self._provider(owner, provider)
        path = self._job_path(folder, request_id)
        job = _read(path / 'job.json', {})
        item = next((a for a in job.get('artifacts', []) if a['name'] == name), None)
        if job.get('state') != 'completed' or item is None:
            fail('找不到這個擁有者的成果', 'not_found', 404)
        target = path / item['name']
        if target.is_symlink() or not target.is_file() or target.stat().st_size != item['size'] or hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
            fail('本機成果驗證失敗', 'artifact_invalid', 409)
        return target, dict(item)

    def tick(self):
        """Resume existing jobs only; safe with concurrent processes via job locks."""
        for jobfile in self.root.glob('*/*/jobs/*/job.json'):
            path, folder = jobfile.parent, jobfile.parent.parent.parent
            try:
                with _lock(path / '.lock', blocking=False):
                    job = _read(jobfile)
                    if job['state'] not in TERMINAL:
                        self._poll(folder, path, job)
            except (BlockingIOError, HostedComputeError, OSError):
                continue

    def start(self, interval=10):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        def run():
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(interval)
        self._thread = threading.Thread(target=run, name='hosted-compute', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
