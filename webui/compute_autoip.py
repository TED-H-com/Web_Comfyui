"""Conservative bridges from generic text forms to existing AutoIP plans.

This module does not submit, fetch prices, resolve credentials or read media.
Character and uploaded-media jobs continue through the existing canonical
resource/grant APIs; they cannot become anonymous jobs through this adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import math
import uuid

try:
    from compute_routing import ComputeValidationError
    from provider_fabric.autoip_profiles import candidate_autoip_profiles
except ModuleNotFoundError:
    from webui.compute_routing import ComputeValidationError
    from webui.provider_fabric.autoip_profiles import candidate_autoip_profiles

TEXT_FEATURES = {
    'text2img': 'text_to_image',
    'qwen21-text2img': 'text_to_image',
    'h3-text-video': 'text_to_video',
}
IMAGE_ASPECTS = {'1:1': '1024x1024', '4:3': '1024x768', '3:4': '768x1024',
                 '16:9': '960x540', '9:16': '540x960'}


def normalise_autoip_intent(feature_id, settings):
    """Remove exact local defaults only; callers must display returned warnings.

    Geometry, authored negative prompts, nondefault sampler controls and
    character bindings are preserved for strict validation, never discarded.
    Normalizing does not imply a provider account or operation is ready.
    """
    if not isinstance(settings, Mapping) or any(not isinstance(key, str) for key in settings):
        _reject('生成設定必須是物件')
    result = copy.deepcopy(dict(settings))
    defaults = {}
    if feature_id == 'text2img':
        defaults = {'steps': 8, 'cfg': 1.0, 'batchSize': 1, 'useDopsd': False}
    elif feature_id in {'qwen21-text2img', 'qwen21-edit'}:
        defaults = {'steps': 25, 'cfg': 1.0}
    elif feature_id == 'img2img':
        defaults = {'steps': 20, 'guidance': 2.5, 'denoise': 1.0}
    elif feature_id in {'h3-text-video', 'h3-image-video'}:
        defaults = {'acceleration': 'stock'}
    removed = []
    for key, default in defaults.items():
        if key not in result:
            continue
        value = result[key]
        equal = type(value) is type(default) and value == default
        if type(value) in (int, float) and type(default) in (int, float):
            equal = value == default
        if equal:
            del result[key]
            removed.append(key)
    warnings = []
    if removed:
        warnings.append('改用 AutoIP 服務配方；已轉換本機預設控制：' + ', '.join(removed) + '。自訂控制與尺寸不會自動捨棄。')
    if feature_id in TEXT_FEATURES and TEXT_FEATURES[feature_id] == 'text_to_image' and {'width', 'height'} & result.keys():
        if any(type(result.get(key)) is not int or result[key] < 1 for key in ('width', 'height')):
            _reject('輸出寬高必須完整提供正整數像素', 'geometry_unsupported')
        size = f"{result['width']}x{result['height']}"
        if size not in IMAGE_ASPECTS.values():
            _reject('AutoIP 沒有與指定寬高完全相同的輸出尺寸', 'geometry_unsupported')
        if any(not isinstance(result[key], str) or IMAGE_ASPECTS.get(result[key], result[key]) != size
               for key in ('size', 'aspect') if key in result):
            _reject('輸出尺寸欄位互相衝突', 'geometry_conflict')
        del result['width'], result['height']
        result['size'] = size
    if feature_id == 'qwen21-text2img' and {'aspectRatio', 'megapixels'} & result.keys():
        # Match Qwen's ResolutionSelector (1024² pixels per MP, multiples of
        # 32), rather than treating its aspect label as a smaller AutoIP size.
        ratio = result.get('aspectRatio', '1:1')
        megapixels = result.get('megapixels', '1')
        if not isinstance(ratio, str) or ratio not in {'1:1', '16:9', '9:16', '3:2', '2:3', '4:3', '3:4', '21:9'} or str(megapixels) not in {'1', '2', '4'}:
            _reject('Qwen 畫面比例或像素數不正確', 'geometry_unsupported')
        width_ratio, height_ratio = map(int, ratio.split(':'))
        scale = math.sqrt(float(megapixels) * 1024 * 1024 / (width_ratio * height_ratio))
        size = f'{round(width_ratio * scale / 32) * 32}x{round(height_ratio * scale / 32) * 32}'
        if size not in IMAGE_ASPECTS.values():
            _reject('AutoIP 沒有與這個 Qwen 輸出完全相同的像素尺寸', 'geometry_unsupported')
        if any(not isinstance(result[key], str) or IMAGE_ASPECTS.get(result[key], result[key]) != size for key in ('size', 'aspect') if key in result):
            _reject('輸出尺寸欄位互相衝突', 'geometry_conflict')
        result.pop('aspectRatio', None)
        result.pop('megapixels', None)
        result['size'] = size
        warnings.append('Qwen 畫面設定已換算為相同的 ' + size + ' 像素；執行模型改為 AutoIP 服務配方。')
    return result, warnings


def normalise_autoip_video_intent(feature_id, settings, override=None):
    """Preserve H3 prose and apply only an explicit remote video specification.

    The caller binds the original top-level computeAutoipVideo object in the
    durable request hash and displays these warnings before submission. Local
    native canvas/frame counts are never implicitly rounded or relabeled.
    """
    result, warnings = normalise_autoip_intent(feature_id, settings)
    if feature_id not in {'h3-text-video', 'h3-image-video'}:
        if override is not None:
            _reject('此功能沒有 AutoIP 影片設定轉接', 'autoip_feature_unsupported')
        return result, warnings
    sections = ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music')
    if any(key in result for key in sections):
        if any(not isinstance(result.get(key), str) or not result[key].strip() for key in sections):
            _reject('H3 的畫面、環境聲與配樂描述都必須保留', 'prompt_invalid')
        prompt = result[sections[0]].strip()
        audio = '\n\n'.join(key + ':\n' + result[key].strip() for key in sections[1:])
        if len(prompt) > 1000 or len(audio) > 1000:
            _reject('AutoIP 畫面及聲音描述各限 1000 字；不會自動截斷', 'prompt_invalid')
        if any(result[key] != prompt for key in ('prompt', 'positive') if key in result):
            _reject('H3 與通用畫面描述互相衝突', 'prompt_conflict')
        if 'audio_intent' in result and result['audio_intent'] != audio:
            _reject('H3 與通用聲音描述互相衝突', 'prompt_conflict')
        for key in sections:
            del result[key]
        result.update(prompt=prompt, audio_intent=audio)
        warnings.append('保留 H3 的畫面、環境聲與配樂描述，改以 AutoIP 影片配方執行。')
    native_aspects = {'landscape', 'portrait', 'square', 'landscape-preview', 'portrait-preview', 'square-preview'}
    if override is None:
        if 'frames' in result or 'quality' in result:
            _reject('請明確選擇 AutoIP 影片秒數；本機幀數不會自動取整', 'duration_invalid')
        if result.get('aspect') in native_aspects:
            _reject('請明確選擇 AutoIP 影片比例與解析度；本機畫布不會自動縮放', 'geometry_unsupported')
        return result, warnings
    keys = {'duration_seconds', 'resolution', 'aspect_ratio'}
    if not isinstance(override, Mapping) or set(override) != keys:
        _reject('AutoIP 影片設定必須明確提供秒數、解析度與比例', 'settings_invalid')
    if type(override['duration_seconds']) is not int or override['duration_seconds'] not in range(2, 16):
        _reject('AutoIP 影片秒數必須是 2–15 的整數', 'duration_invalid')
    if override['resolution'] not in ('480p', '768p', '2k') or override['aspect_ratio'] not in ('16:9', '9:16'):
        _reject('AutoIP 影片解析度或比例不受支援', 'geometry_unsupported')
    if 'frames' in result and (isinstance(result['frames'], bool) or str(result['frames']) not in {str(n) for n in range(124, 363, 17)}):
        _reject('本機 H3 幀數設定不正確', 'duration_invalid')
    if 'quality' in result and (result['quality'] != 'quality' or 'frames' in result):
        _reject('本機 H3 長度欄位互相衝突', 'duration_invalid')
    if 'aspect' in result and result['aspect'] not in native_aspects | {'16:9', '9:16'}:
        _reject('本機 H3 畫布設定不正確', 'geometry_unsupported')
    for key in ('frames', 'quality', 'aspect', 'seconds', 'duration', 'duration_seconds', 'resolution', 'aspect_ratio'):
        result.pop(key, None)
    result.update(override)
    warnings.append(f"已明確選用 AutoIP 輸出：{override['duration_seconds']} 秒、{override['resolution']}、{override['aspect_ratio']}；取代本機幀數與畫布設定。")
    return result, warnings


def _reject(message, code='autoip_options_unsupported'):
    raise ComputeValidationError(message, code=code)


def _choice(settings, names, default=None):
    supplied = [settings[name] for name in names if name in settings]
    if supplied and any(value != supplied[0] for value in supplied):
        _reject('同一項設定的不同欄位值不一致')
    return supplied[0] if supplied else default


def build_autoip_text_plan(feature_id, settings, *, request_id, visual_class='realistic',
                          character_id='', file_slots=(), max_credits=None):
    """Build an exact anonymous text plan; authorization/readiness stays server-side.

    Call service.plan_intent for a current quote, enforce the chosen budget,
    then confirm that exact signed plan (or use submit_generation only where
    the caller also enforces any requested spending ceiling).
    """
    operation = TEXT_FEATURES.get(feature_id) if isinstance(feature_id, str) else None
    if operation is None:
        _reject('這個功能尚未提供通用 AutoIP 轉接', 'autoip_feature_unsupported')
    if character_id or tuple(file_slots):
        _reject('角色與素材必須使用既有的權限及不可變素材綁定，不能轉成匿名生成', 'canonical_binding_required')
    try:
        canonical_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        _reject('請求識別碼必須是 UUID', 'request_id_invalid')
    if canonical_id != request_id:
        _reject('請求識別碼必須使用標準格式', 'request_id_invalid')
    if not isinstance(visual_class, str) or visual_class not in {'anime', 'realistic'}:
        _reject('請選擇動漫或寫實視覺類型', 'visual_class_required')
    if not isinstance(settings, Mapping):
        _reject('缺少生成設定')
    common = {'prompt', 'positive', 'language', 'seed', 'batchSize', 'useDopsd'}
    allowed = common | ({'negative', 'negative_prompt', 'size', 'aspect', 'mode'} if operation == 'text_to_image' else
                        {'seconds', 'duration', 'duration_seconds', 'resolution', 'aspect', 'aspect_ratio', 'audio_intent'})
    if set(settings) - allowed:
        _reject('目前設定包含 AutoIP 無法保留的模型或採樣控制；請改用此平台支援的設定')
    if type(settings.get('batchSize', 1)) is not int or settings.get('batchSize', 1) != 1 or settings.get('useDopsd', False) is not False:
        _reject('此 AutoIP 轉接只支援單一輸出，且不支援本機 DOPSD LoRA')
    prompt = _choice(settings, ('prompt', 'positive'))
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 1000:
        _reject('生成描述需為 1–1000 字文字', 'prompt_invalid')
    language = settings.get('language', 'zh-TW')
    if not isinstance(language, str) or not language.strip() or len(language) > 32:
        _reject('語言設定不正確')
    if max_credits is not None and (not isinstance(max_credits, int) or isinstance(max_credits, bool) or max_credits < 0):
        _reject('AutoIP 點數上限必須是非負整數')
    profile = next(p for p in candidate_autoip_profiles()
                   if p.capability.value == operation and p.visual_class.value == visual_class)
    instruction = {
        'schema_version': 'castle.creative-instruction.v1',
        'shot_id': 'compute-' + uuid.UUID(request_id).hex,
        'frame_intent': prompt.strip(),
        'observable_action': 'Follow the user description without adding a new subject.',
        'camera': 'Use the composition described by the user.',
        'audio_intent': 'No audio output.' if operation == 'text_to_image' else 'Follow the user description for audio.',
        'language': language.strip(),
    }
    if 'audio_intent' in settings:
        if not isinstance(settings['audio_intent'], str) or not settings['audio_intent'].strip() or len(settings['audio_intent']) > 1000:
            _reject('聲音描述需為 1–1000 字文字', 'prompt_invalid')
        instruction['audio_intent'] = settings['audio_intent'].strip()
    if operation == 'text_to_image':
        size = _choice(settings, ('size', 'aspect'), '1024x1024')
        size = IMAGE_ASPECTS.get(size, size) if isinstance(size, str) else size
        mode = settings.get('mode', 'anime' if visual_class == 'anime' else 'standard')
        options = {'mode': mode, 'size': size}
        if settings.get('seed', -1) not in (-1, '-1', 'random'):
            _reject('此 AutoIP 文生圖契約不支援固定種子')
        negative = _choice(settings, ('negative', 'negative_prompt'), '')
        if not isinstance(negative, str) or len(negative.strip()) > 500:
            _reject('負向描述長度不正確')
        if negative.strip():
            instruction['negative_prompt'] = negative.strip()
    else:
        seconds = _choice(settings, ('seconds', 'duration', 'duration_seconds'), 5)
        aspect = _choice(settings, ('aspect', 'aspect_ratio'), '16:9')
        options = {'duration_seconds': seconds, 'resolution': settings.get('resolution', '480p'),
                   'aspect_ratio': aspect}
        seed = settings.get('seed', 'random')
        if seed in (-1, '-1'):
            seed = 'random'
        if seed != 'random':
            # The frozen candidate profile currently advertises only random.
            # Do not widen the live service contract from this UI adapter.
            _reject('此轉接目前只支援隨機影片種子')
        options['seed'] = seed
        instruction['seed'] = seed
    for key, value in options.items():
        if value not in profile.option_values[key] or isinstance(value, bool):
            _reject('尺寸、模式或片長不在目前 AutoIP 契約內')
    return {
        'schema_version': 1, 'capability': operation, 'visual_class': visual_class,
        'characters': [], 'assets': [], 'options': options,
        'provider_id': 'autoip', 'preferred_providers': ['autoip'],
        'max_credits': max_credits, 'creative_instruction': instruction,
    }


def autoip_text_compatibility(feature_id, settings, *, visual_class='realistic', character_id='', file_slots=()):
    try:
        normalized, warnings = normalise_autoip_intent(feature_id, settings)
        plan = build_autoip_text_plan(feature_id, normalized, request_id=str(uuid.UUID(int=0)),
                                     visual_class=visual_class, character_id=character_id, file_slots=file_slots)
    except ComputeValidationError as error:
        return {'supported': False, 'reason': error.code, 'message': str(error)}
    return {'supported': True, 'reason': '', 'operation': plan['capability'],
            'recipe_id': 'autoip-' + plan['capability'],
            'recipe_label': 'AutoIP 文生圖' if plan['capability'] == 'text_to_image' else 'AutoIP 文生影片',
            'options': plan['options'], 'warnings': warnings}


def autoip_generation_request(request_id, plan):
    """Stable one-click fabric envelope; the caller supplies the authorized plan."""
    try:
        value = uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        _reject('請求識別碼必須是 UUID', 'request_id_invalid')
    return {'schema_version': 1, 'request_id': 'compute:' + value.hex, 'plan': dict(plan)}


def build_autoip_plan(feature_id, settings, *, max_credits=100):
    """Legacy-form convenience entry point; callers reject character/file work.

    The instruction label is deterministic so repeated inspection and admission
    produce identical plans. The fabric generation request ID remains the
    independent owner-scoped identity supplied by the server coordinator.
    """
    if not isinstance(settings, Mapping):
        _reject('缺少生成設定')
    values, _warnings = normalise_autoip_intent(feature_id, settings)
    visual = _choice(values, ('visual_class', 'visualClass', 'visualStyle'), 'realistic')
    for name in ('visual_class', 'visualClass', 'visualStyle'):
        values.pop(name, None)
    try:
        material = json.dumps({'feature_id': feature_id, 'settings': values, 'visual_class': visual},
                              sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        _reject('生成設定不是有效 JSON')
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'castle-compute-autoip:' + material))
    return build_autoip_text_plan(feature_id, values, request_id=request_id,
                                  visual_class=visual, max_credits=max_credits)
