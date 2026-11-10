"""Owned image inputs for generic AutoIP work; no network or provider submit.

The HTTP layer must authenticate the owner and authorize each source path. This
adapter copies only those explicitly supplied images, then creates immutable
owner/version bindings consumed by the existing canonical resource resolver.
The execution worker must allow the configured staging root in its file-source
resolver. Character work deliberately remains in the existing capability lane.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

try:
    from compute_autoip import _choice, _reject, normalise_autoip_intent
    from provider_fabric.autoip_profiles import candidate_autoip_profiles
    from provider_fabric.autoip_input_geometry import FIT_POLICY, KNOWN_CANVASES
    from provider_fabric.media_signatures import classify_declared_media, SIGNATURE_PROBE_BYTES
    from provider_fabric.models import CostQuote, MediaKind, RouteDecision, RouteIntent, VisualClass
    from provider_fabric.owned_media_source import owned_provider_asset_uri
    from provider_fabric.store import ProviderFabricStore, StoreConflictError
except ModuleNotFoundError:
    from webui.compute_autoip import _choice, _reject, normalise_autoip_intent
    from webui.provider_fabric.autoip_profiles import candidate_autoip_profiles
    from webui.provider_fabric.autoip_input_geometry import FIT_POLICY, KNOWN_CANVASES
    from webui.provider_fabric.media_signatures import classify_declared_media, SIGNATURE_PROBE_BYTES
    from webui.provider_fabric.models import CostQuote, MediaKind, RouteDecision, RouteIntent, VisualClass
    from webui.provider_fabric.owned_media_source import owned_provider_asset_uri
    from webui.provider_fabric.store import ProviderFabricStore, StoreConflictError

MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
_OPERATIONS = {'img2img': 'image_to_image', 'qwen21-edit': 'image_to_image',
               'img2video': 'image_to_video', 'h3-image-video': 'image_to_video',
               'first-last-frame': 'image_to_video'}


def _hash(*parts):
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()


def _real_path(path, roots):
    original = Path(path).absolute()
    if any(part.is_symlink() for part in (original, *original.parents)):
        _reject('素材來源不能使用符號連結', 'source_path_invalid')
    try:
        resolved = original.resolve(strict=True)
        contained = roots and any(resolved.is_relative_to(Path(root).resolve(strict=True)) for root in roots)
    except OSError:
        _reject('素材來源已不存在或無法讀取', 'source_path_invalid')
    if not contained:
        _reject('素材不在核准的來源目錄內', 'source_path_invalid')
    return resolved


def _image_check(path, mime):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'} or Image.MIME.get(image.format) != mime:
                _reject('圖片格式與檔名不一致', 'image_invalid')
            if image.width < 1 or image.height < 1 or image.width * image.height > 64_000_000:
                _reject('圖片尺寸超過允許範圍', 'image_invalid')
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as error:
        _reject('素材不是完整可讀的圖片', 'image_invalid')


def ingest_owned_image(store, owner_user_id, account_id, source_path, *, source_identity,
                       visual_class, allowed_source_roots, staging_root,
                       authorize_source: Callable, character_ids=(), max_bytes=MAX_IMAGE_BYTES):
    """Freeze one authorized generic image and return a path-free planner asset.

    authorize_source(owner_user_id, absolute_path) must verify the existing
    upload ownership receipt and return exactly True. A shared filesystem root
    alone is not ownership. Only local namespace metadata is written; no remote
    upload, account provisioning or character creation occurs here.
    """
    if not isinstance(store, ProviderFabricStore) or not callable(authorize_source):
        raise TypeError('provider store and source authorization callback are required')
    if not isinstance(owner_user_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._:-]{0,127}', owner_user_id):
        _reject('缺少有效的擁有者身分', 'owner_required')
    if character_ids:
        _reject('角色素材必須使用既有的角色權限流程', 'canonical_binding_required')
    if visual_class not in ('anime', 'realistic'):
        _reject('圖片必須明確選擇動漫或寫實類型', 'visual_class_required')
    if not isinstance(source_identity, str) or not source_identity or len(source_identity) > 1024:
        _reject('素材識別碼不正確', 'source_identity_invalid')
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_IMAGE_BYTES:
        raise ValueError('image size limit is invalid')
    original = Path(source_path).absolute()
    if authorize_source(owner_user_id, original) is not True:
        _reject('無權使用這份素材', 'source_not_owned')
    source = _real_path(original, allowed_source_roots)
    mime = _MIME.get(source.suffix.lower())
    if mime is None:
        _reject('只支援 PNG、JPEG、WebP 圖片', 'image_invalid')
    account = store.get_provider_account(account_id)
    if account.get('provider_id') != 'local_comfy':
        _reject('素材暫存必須使用本機命名空間', 'namespace_invalid')
    root = Path(staging_root).absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        _reject('素材暫存目錄不能使用符號連結', 'staging_invalid')
    owner_dir = root / _hash(owner_user_id)
    owner_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Reject a pre-existing swapped owner directory as well as a symlink root.
    if owner_dir.is_symlink() or not owner_dir.resolve().is_relative_to(root.resolve()):
        _reject('素材暫存目錄不正確', 'staging_invalid')
    temporary = None
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= max_bytes:
            _reject('素材必須是大小允許的單一一般檔案', 'image_invalid')
        digest, total, probe = hashlib.sha256(), 0, bytearray()
        with tempfile.NamedTemporaryFile(prefix='input-', dir=owner_dir, delete=False) as output:
            temporary = Path(output.name)
            while chunk := os.read(fd, 1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    _reject('圖片超過允許大小', 'image_too_large')
                output.write(chunk)
                digest.update(chunk)
                if len(probe) < SIGNATURE_PROBE_BYTES:
                    probe.extend(chunk[:SIGNATURE_PROBE_BYTES - len(probe)])
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if total != before.st_size or identity(before) != identity(after):
            _reject('素材在讀取時改變，請重新選擇', 'source_changed')
        if classify_declared_media(mime, bytes(probe)) is None:
            _reject('圖片簽章與宣告格式不一致', 'image_invalid')
        _image_check(temporary, mime)
        sha = digest.hexdigest()
        destination = owner_dir / (sha + ('.jpg' if mime == 'image/jpeg' else '.png' if mime == 'image/png' else '.webp'))
        try:
            os.link(temporary, destination)  # Atomic exclusive publication.
        except FileExistsError:
            if destination.is_symlink() or destination.stat().st_size != total or hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                _reject('既有暫存素材與內容雜湊不符', 'staging_conflict')
        temporary.unlink()
        temporary = None
    finally:
        os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    projection_id = 'compute-upload:' + _hash(owner_user_id, source_identity, sha, visual_class)[:32]
    version_id = 'assetver:' + _hash(projection_id, sha)[:32]
    store.project_owned_remote_asset(owner_user_id, projection_id, account_id, projection_id,
                                    MediaKind.IMAGE, visual_class=VisualClass(visual_class),
                                    metadata={'source': 'compute_upload', 'input_binding': 'anonymous-owned-image-v1'})
    asset = store.get_owned_remote_asset(owner_user_id, projection_id)
    versions = [v for v in asset['versions'] if v['version_id'] == version_id]
    if not versions:
        try:
            store.add_owned_remote_asset_version(owner_user_id, projection_id, destination.as_uri(),
                                                version_id=version_id, version_number=1, provider_version_id=sha,
                                                mime_type=mime, byte_size=total, sha256=sha,
                                                metadata={'verified_owned_upload': True})
        except StoreConflictError:
            pass
    asset = store.get_owned_remote_asset(owner_user_id, projection_id)
    versions = [v for v in asset['versions'] if v['version_id'] == version_id]
    if (len(versions) != 1 or asset.get('character_ids')
            or any(versions[0].get(key) != value for key, value in {'sha256': sha, 'byte_size': total, 'mime_type': mime}.items())):
        _reject('素材版本或角色綁定與本次請求不一致', 'asset_binding_conflict')
    return {
        'projection_id': projection_id, 'asset_version_id': version_id,
        'asset': {'asset_id': 'provider-version:' + version_id, 'kind': 'image',
                  'uri': owned_provider_asset_uri(projection_id, version_id),
                  'visual_class': visual_class, 'character_ids': [],
                  'mime_type': mime, 'byte_size': total, 'sha256': sha, 'duration_seconds': None},
    }


def _media_plan_spec(feature_id, settings, file_slots, *, request_id,
                     visual_class, character_id='', max_credits=None, fit_video_frame=True):
    """Validate metadata only; the returned skeleton must never be submitted."""
    operation = _OPERATIONS.get(feature_id) if isinstance(feature_id, str) else None
    if operation is None or not isinstance(settings, Mapping):
        _reject('這個 AutoIP 圖片轉接尚未支援此功能或設定', 'autoip_feature_unsupported')
    if character_id or visual_class not in ('anime', 'realistic'):
        _reject('角色請求不可轉為匿名素材；圖片類型必須明確', 'canonical_binding_required')
    if set(file_slots) == {'image'}:
        slots = ['image']
    elif operation == 'image_to_video' and set(file_slots) in ({'first_frame'}, {'first_frame', 'last_frame'}, {'image', 'last_frame'}, {'start', 'end'}):
        slots = ['start', 'end'] if 'start' in file_slots else ['first_frame' if 'first_frame' in file_slots else 'image'] + (['last_frame'] if 'last_frame' in file_slots else [])
    else:
        _reject('圖片欄位不符合單圖或首尾幀契約', 'input_slots_unsupported')
    try:
        uid = uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        _reject('請求識別碼必須是 UUID', 'request_id_invalid')
    if str(uid) != request_id:
        _reject('請求識別碼必須使用標準格式', 'request_id_invalid')
    values, warnings = normalise_autoip_intent(feature_id, settings)
    for key in ('visual_class', 'visualClass', 'visualStyle'):
        if key in values:
            if values.pop(key) != visual_class:
                _reject('素材類型與請求設定不一致', 'visual_class_mismatch')
    prompt = _choice(values, ('instruction', 'prompt', 'positive'))
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 1000:
        _reject('生成描述需為 1–1000 字文字', 'prompt_invalid')
    common = {'instruction', 'prompt', 'positive', 'language', 'seed', 'negative'}
    allowed = common | ({'mode', 'size', 'strength'} if operation == 'image_to_image' else
                        {'seconds', 'duration', 'duration_seconds', 'resolution', 'aspect', 'aspect_ratio', 'audio_intent'})
    if set(values) - allowed or values.get('negative', '') != '' or values.get('seed', -1) not in (-1, '-1', 'random'):
        _reject('此圖片轉接無法保留指定的模型、採樣、負向描述或固定種子', 'autoip_options_unsupported')
    language = values.get('language', 'zh-TW')
    if not isinstance(language, str) or not language.strip() or len(language) > 32:
        _reject('語言設定不正確')
    if max_credits is not None and (type(max_credits) is not int or max_credits < 0):
        _reject('AutoIP 點數上限必須是非負整數')
    profile = next(p for p in candidate_autoip_profiles()
                   if p.capability.value == operation and p.visual_class.value == visual_class)
    options = ({'mode': values.get('mode', 'standard'), 'size': values.get('size', 'original'),
                'strength': values.get('strength', 0.75)} if operation == 'image_to_image' else
               {'duration_seconds': _choice(values, ('seconds', 'duration', 'duration_seconds'), 5),
                'resolution': values.get('resolution', '480p'),
                'aspect_ratio': _choice(values, ('aspect', 'aspect_ratio'), '16:9'), 'seed': 'random'})
    if any(isinstance(value, bool) or value not in profile.option_values[key] for key, value in options.items()):
        _reject('選項不在目前 AutoIP 圖片契約內')
    if operation == 'image_to_video' and fit_video_frame:
        if (options['resolution'], options['aspect_ratio']) in KNOWN_CANVASES:
            options['frame_fit'] = FIT_POLICY
            warnings.append('AutoIP 480p 橫式影片會保留完整圖片，等比例縮放至 864×480 並補黑邊。')
        else:
            warnings.append('此尺寸目前沿用原圖，尚未套用等比例補邊。')
    instruction = {'schema_version': 'castle.creative-instruction.v1', 'shot_id': 'compute-' + uid.hex,
                   'frame_intent': prompt.strip(), 'observable_action': 'Follow the user description using the supplied image.',
                   'camera': 'Use the composition requested by the user.',
                   'audio_intent': 'No audio output.' if operation == 'image_to_image' else 'Follow the user description for audio.',
                   'language': language.strip()}
    if operation == 'image_to_video':
        instruction['seed'] = 'random'
        if 'audio_intent' in values:
            if not isinstance(values['audio_intent'], str) or not values['audio_intent'].strip() or len(values['audio_intent']) > 1000:
                _reject('聲音描述需為 1–1000 字文字', 'prompt_invalid')
            instruction['audio_intent'] = values['audio_intent'].strip()
    return ({'schema_version': 1, 'capability': operation, 'visual_class': visual_class,
             'characters': [], 'assets': [], 'options': options, 'provider_id': 'autoip',
             'preferred_providers': ['autoip'], 'max_credits': max_credits, 'creative_instruction': instruction}, slots, warnings)


def build_autoip_media_plan(feature_id, settings, assets_by_slot, *, request_id,
                           visual_class, character_id='', max_credits=None):
    """Compile existing immutable generic image references, never raw paths."""
    if not isinstance(assets_by_slot, Mapping):
        _reject('圖片素材欄位必須是物件', 'input_slots_unsupported')
    plan, slots, _ = _media_plan_spec(feature_id, settings, assets_by_slot,
                                    request_id=request_id, visual_class=visual_class,
                                    character_id=character_id, max_credits=max_credits)
    for slot in slots:
        item = assets_by_slot[slot]
        item = item.get('asset', item) if isinstance(item, Mapping) else item
        if (not isinstance(item, Mapping) or item.get('kind') != 'image'
                or item.get('visual_class') != visual_class or item.get('character_ids') != []
                or not str(item.get('asset_id', '')).startswith('provider-version:assetver:')):
            _reject('只能使用類型相符且沒有角色綁定的不可變素材版本', 'canonical_binding_required')
        plan['assets'].append(dict(item))
    return plan


def inspect_autoip_media(runtime, actor, feature_id, settings, file_slots, *,
                         visual_class, character_id='', max_credits=100):
    """Metadata-only admission preview using exact account readiness/live price.

    No input files, assets or execution intents are created. The quote source
    accepts an asset-free intent solely to look up operation/options pricing;
    this object is never sent to the planner or worker. Actual submission must
    repeat canonical asset validation and the budget gate in submit_generation.
    Inspection failures intentionally disclose stable codes only.
    """
    try:
        from compute_routing import ComputeRoutingError
    except ModuleNotFoundError:
        from webui.compute_routing import ComputeRoutingError
    result = {'eligible': False, 'reasons': [], 'queue_depth': 0, 'currency': 'credits'}
    try:
        plan, _, warnings = _media_plan_spec(feature_id, settings, file_slots,
            request_id=str(uuid.UUID(int=0)), visual_class=visual_class,
            character_id=character_id, max_credits=max_credits)
        accounts = [item for item in runtime.inventory(actor).get('providers', ())
                    if item.get('provider_id') == 'autoip'
                    and isinstance(item.get('account_id'), str)
                    and item.get('compatibility_receipt_current') is True]
        if len(accounts) != 1:
            _reject('沒有唯一且已通過相容性檢查的 AutoIP 帳號', 'account_unavailable')
        account = runtime.store.get_owned_provider_account(actor.user_id, accounts[0]['account_id'])
        if account.get('provider_id') != 'autoip' or account.get('enabled') is not True:
            _reject('AutoIP 帳號目前不可用', 'account_unavailable')
        profiles = [profile for profile in runtime.autoip_profiles_for_account(actor.user_id, account['account_id'])
                    if profile.capability.value == plan['capability']
                    and profile.visual_class.value == visual_class and profile.execution_ready]
        if len(profiles) != 1:
            _reject('這個帳號尚未驗證所選圖片操作', 'autoip_operation_unavailable')
        profile = profiles[0]
        if any(key not in profile.option_values or value not in profile.option_values[key]
               for key, value in plan['options'].items()):
            _reject('帳號目前不支援所選圖片設定', 'autoip_options_unsupported')
        intent = RouteIntent(profile.capability, profile.visual_class,
                             required_options=frozenset(plan['options']), preferred_providers=('autoip',))
        decision = RouteDecision('autoip', profile.variant_id, (profile.key,), (), 'compute-media-preview-v1')
        quote = runtime.autoip_quote_source(actor, account, intent, decision, plan['options'])
        if (not isinstance(quote, CostQuote) or not quote.authoritative
                or quote.provider_id != 'autoip' or quote.account_id != account['account_id']
                or quote.capability != profile.capability or quote.credits is None):
            _reject('無法確認這個圖片操作的即時用量', 'quote_unavailable')
        if max_credits is not None and quote.credits > max_credits:
            _reject('這個圖片操作的即時報價超過上限', 'budget_exceeded')
        return {**result, 'eligible': True, 'estimated_cost': quote.credits,
                'warnings': warnings + ['送出時會驗證圖片內容與擁有權，並依當時報價重新檢查點數上限。']}
    except ComputeRoutingError as error:
        return {**result, 'reasons': [error.code]}
    except Exception:
        return {**result, 'reasons': ['inspection_unavailable']}


def prepare_autoip_media(runtime, actor, feature_id, settings, owned_paths_by_slot, *,
                         request_id, visual_class, allowed_source_roots, staging_root,
                         authorize_source, character_id='', max_credits=100):
    """Prepare a submit-ready plan from authenticated, already staged uploads.

    The web adapter must reject character/action/variant requests and authorize
    reused server files, or stage fresh bytes from this authenticated request.
    Its per-request authorize_source(owner,path) must verify that exact set;
    containment alone cannot authorize a shared input. Paths never leave this
    call. The caller delegates once to the existing durable fabric service.
    """
    if not isinstance(owned_paths_by_slot, Mapping):
        _reject('圖片素材欄位必須是物件', 'input_slots_unsupported')
    _, slots, _ = _media_plan_spec(feature_id, settings, owned_paths_by_slot,
                                 request_id=request_id, visual_class=visual_class,
                                 character_id=character_id, max_credits=max_credits)
    bindings = {}
    for slot in slots:
        bindings[slot] = ingest_owned_image(runtime.store, actor.user_id, runtime.config.local_account_id,
            owned_paths_by_slot[slot], source_identity=request_id + ':' + slot, visual_class=visual_class,
            allowed_source_roots=allowed_source_roots, staging_root=staging_root, authorize_source=authorize_source)
    return build_autoip_media_plan(feature_id, settings, bindings, request_id=request_id,
                                  visual_class=visual_class, character_id=character_id, max_credits=max_credits)
