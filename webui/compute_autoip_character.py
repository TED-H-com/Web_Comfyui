"""Explicit ordinary-character forms -> exact AutoIP capability grants.

The original owner/character/action/feature/variant remains in the durable
ComputeRouter request. Its source binding hash also enters the signed creative
instruction. The provider tuple is deliberately separate: the existing fabric
canonical resolver, rights materializer, quote and confirmation checks remain
authoritative. No anonymous ingestion, character shadow records or provider I/O.

Canonical avatars are hash-pinned at admission, not retained copies. Fabric
confirmation and the worker reject changed bytes instead of using replacements.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import uuid

try:
    from compute_routing import ComputeRejected
    from compute_autoip import normalise_autoip_video_intent
    from compute_autoip_media import _media_plan_spec
    from provider_fabric.autoip_profiles import candidate_autoip_profiles
    from qwen_image21 import compile_character_prompt
    import minimax_h3_product as H3
except ModuleNotFoundError:
    from webui.compute_routing import ComputeRejected
    from webui.compute_autoip import normalise_autoip_video_intent
    from webui.compute_autoip_media import _media_plan_spec
    from webui.provider_fabric.autoip_profiles import candidate_autoip_profiles
    from webui.qwen_image21 import compile_character_prompt
    from webui import minimax_h3_product as H3

FEATURES = frozenset({'character-photoshoot', 'h3-image-video', 'h3-reference-video'})
MAX_IMAGE = 32 * 1024 * 1024


def _reject(message, code='canonical_binding_required'):
    raise ComputeRejected(message, code=code)


def _square_source(server, character, asset):
    """Authorize before this call; verify the exact canonical image before sizing."""
    from PIL import Image
    source = server.character_asset_path(character, 'avatar')
    if source is None:
        _reject('角色原圖目前無法使用', 'source_changed')
    source = Path(source)
    if source.is_symlink():
        _reject('角色原圖目前無法使用', 'source_changed')
    with source.open('rb') as handle:
        data = handle.read(MAX_IMAGE + 1)
    if (not 0 < len(data) <= MAX_IMAGE or len(data) != asset.byte_size
            or hashlib.sha256(data).hexdigest() != asset.sha256):
        _reject('角色原圖已改變，請重新預覽', 'source_changed')
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if (image.format not in {'PNG', 'JPEG', 'WEBP'} or Image.MIME.get(image.format) != asset.mime_type
                or width != height or width * height > 64_000_000):
            _reject('AutoIP 角色寫真目前只接受可精確對應的正方形原圖與 1024 解析度', 'geometry_unsupported')
        image.verify()


def build_character_plan(server, user, runtime, actor, request, *, request_id,
                         service_token=False, max_credits=100, preview=False):
    """Return plan, separate source origin and warnings, with current authorization.

Only call under the handler's freshly established provider actor context. The
caller must pass this plan through the ordinary fabric service; this function
cannot grant capability/rights, accept a quote or schedule a provider task.
"""
    try:
        uid = uuid.UUID(request_id)
        if str(uid) != request_id:
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        _reject('請求識別碼必須是標準 UUID', 'request_id_invalid')
    feature = request.get('featureId')
    if feature not in FEATURES or not request.get('characterId'):
        _reject('這個角色功能尚未提供 AutoIP 語意轉接', 'autoip_feature_unsupported')
    submitted_settings = request.get('settings') or {}
    decoration_keys = {'_characterId', '_characterName'}
    if not isinstance(submitted_settings, dict) or any(not isinstance(key, str) or (key.startswith('_') and key not in decoration_keys) for key in submitted_settings):
        _reject('角色內部綁定只能由伺服器建立', 'input_validation_failed')
    decorations = {key: submitted_settings[key] for key in decoration_keys if key in submitted_settings}
    submitted_settings = {key: value for key, value in submitted_settings.items() if key not in decoration_keys}
    # These two ordinary-form labels are not authoritative identity input.
    # Validate them against the authorized record below, then use only the
    # canonical planner's recomputed context. Never strip authored controls.
    request = {**request, 'settings': submitted_settings}
    if request.get('files'):
        slot = 'reference' if feature == 'h3-reference-video' else 'image'
        if not preview or request['files'] != {slot: {'name': slot}}:
            _reject('此角色轉接只接受伺服器綁定的原圖，不接受替換或額外素材', 'input_slots_unsupported')
        # computeUploadMetadata emits this exact byte-free marker. It is only
        # accepted for inspection; submission must still provide no files.
        request = {**request, 'files': {}}
    # Reuse the original form's exact ownership, capability, policy, options and
    # server-bound slot checks. This does not test local model availability.
    effective = server.plan_colab_generation_request(request, user, service_token, provider='autoip')
    character = server.character_entry_for_generation(request['characterId'], user, service_token)
    if not character:
        _reject('角色目前無法使用')
    canonical_decorations = {'_characterId': character['id'], '_characterName': str(character.get('name') or '')}
    if any(not isinstance(value, str) or value != canonical_decorations[key] for key, value in decorations.items()):
        _reject('角色顯示資料與已授權角色不一致', 'capability_binding_mismatch')
    if character.get('kind') == 'provider_character':
        _reject('這個匯入角色需要不可變角色卡版本轉接，不能改用一般大頭貼', 'source_contract_unsupported')
    character_id = character['id']
    visual = server.normalized_visual_class(character.get('visualClass'))
    expected = ({'action': 'photoshoot', 'variant': f'photoshoot.{visual}.character-photoshoot.v1', 'slot': 'image', 'operation': 'image_to_image'}
        if feature == 'character-photoshoot' else
        {'action': 'h3-image-video', 'variant': f'image-video.{visual}.h3-fl2va.v1', 'slot': 'image', 'operation': 'image_to_video'}
        if feature == 'h3-image-video' else
        {'action': 'h3-reference-video', 'variant': f'reference-video.{visual}.h3-ref2va-image.v1', 'slot': 'reference', 'operation': 'multi_subject_video'})
    variants = {expected['variant']}
    if feature == 'character-photoshoot':
        # These separately versioned Studio contracts also bind the legacy
        # avatar. The server must still grant the exact submitted tuple; this
        # list never makes a missing/private capability public or executable.
        variants.add(f'photoshoot.{visual}.character-photoshoot.reference.v1')
        if visual == 'anime':
            variants.add('photoshoot.anime.character-photoshoot.v3')
    if (visual not in {'anime', 'realistic'} or request.get('action') != expected['action']
            or request.get('variantId') not in variants
            or effective.get('feature_id') != feature or effective.get('file_slots') != [expected['slot']]):
        _reject('角色、原功能與 AutoIP 轉接版本不一致', 'capability_binding_mismatch')
    values = dict(effective['settings'])
    if (values.get('_characterVisualClass') != visual
            or request.get('computeVisualClass') not in (None, '', visual)
            or submitted_settings.get('visualStyle') not in (None, '', visual)
            or values.get('visualStyle') not in (None, '', visual)):
        _reject('畫風與已授權角色不一致', 'visual_class_mismatch')
    values.pop('_characterVisualClass')
    values.pop('visualStyle', None)
    # Never construct an unlinked image. Resolve the same canonical avatar used
    # by the existing production planner and keep its exact identity and bytes.
    asset_id = f'castle-character:{character_id}:avatar'
    characters, assets = server.provider_fabric_canonical_resource_resolver(actor, (character_id,), (asset_id,))
    selected, asset = characters[character_id], assets[asset_id]
    if (selected.character_id != character_id or selected.visual_class.value != visual
            or asset.asset_id != asset_id or asset.kind.value != 'image'
            or getattr(asset.visual_class, 'value', None) != visual or tuple(asset.character_ids) != (character_id,)
            or not isinstance(asset.sha256, str) or not re.fullmatch('[0-9a-f]{64}', asset.sha256)
            or type(asset.byte_size) is not int or not 0 < asset.byte_size <= MAX_IMAGE):
        _reject('角色原圖的不可變身分證據不完整', 'source_changed')
    origin = {'owner_user_id': actor.user_id, 'character_id': character_id,
              'action_id': request['action'], 'feature_id': feature, 'variant_id': request['variantId'],
              'asset_id': asset_id, 'sha256': asset.sha256, 'byte_size': asset.byte_size}
    binding_hash = hashlib.sha256(json.dumps(origin, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    warnings = ['保留原角色、動作與版本綁定；改用獨立的 AutoIP 服務配方，並非原本的 Qwen／H3 推論圖。']
    if feature == 'character-photoshoot':
        if request.get('computeAutoipVideo') is not None:
            _reject('角色寫真不能套用影片輸出設定', 'settings_invalid')
        if str(values.get('resolution', '1024')) != '1024' or 'size' in values:
            _reject('AutoIP 角色寫真目前只接受可精確對應的 1024 正方形輸出', 'geometry_unsupported')
        _square_source(server, character, asset)
        values.pop('resolution', None)
        if not isinstance(values.get('instruction'), str) or not values['instruction'].strip():
            _reject('請保留完整的角色寫真描述', 'prompt_invalid')
        prompt = compile_character_prompt({'instruction': values.get('instruction'), 'visualStyle': visual},
                                         {'image': 'reference.png'}, seed=0, prefix='autoip-adapter')['4']['inputs']['prompt']
        # The singular AutoIP image slot is the same identity source. Preserve
        # all character prose, with an explicit provider-neutral reference name.
        values['instruction'] = prompt.replace('<image1>', '所提供的角色原圖')
        values['size'] = '1024x1024'
        plan, _, translated = _media_plan_spec('qwen21-edit', values, ['image'],
            request_id=request_id, visual_class=visual, max_credits=max_credits)
        warnings += translated
    else:
        allowed = {'description', 'motionProfile', 'aspect', 'frames', 'quality', 'acceleration', 'seed'}
        if set(values) - allowed or values.get('seed', -1) not in (-1, '-1', 'random'):
            _reject('AutoIP 角色影片不能保留其中的自訂推論設定或固定種子', 'autoip_options_unsupported')
        if request.get('computeAutoipVideo') is None:
            _reject('請明確選擇 AutoIP 影片的秒數、解析度與比例', 'geometry_unsupported')
        # Compile the original character motion/identity prose exactly; never
        # replace it with the raw description or an anonymous scene request.
        canonical = {**values, 'seed': 0, 'acceleration': 'stock', 'frames': values.get('frames', 124)}
        canonical.pop('quality', None)
        graph = H3.compile_product_prompt(feature_id=feature, visual_class=visual, settings=canonical,
            uploads={expected['slot']: 'reference.png'}, output_token='autoip-adapter')
        prompt = graph['5']['inputs']['prompt'].replace('<Picture 1>', 'the supplied character identity image')
        converted, translated = normalise_autoip_video_intent('h3-image-video', values, request['computeAutoipVideo'])
        # description/motion have been compiled, with scene and audio intent
        # intact; the explicit override alone replaces duration and geometry.
        converted.pop('description', None)
        converted.pop('motionProfile', None)
        converted['prompt'] = prompt
        converted['audio_intent'] = 'Preserve the complete authored scene/audio instructions in the prompt; do not add dialogue or music.'
        plan, _, media_warnings = _media_plan_spec('h3-image-video', converted, ['image'],
            request_id=request_id, visual_class=visual, max_credits=max_credits,
            fit_video_frame=expected['operation'] == 'image_to_video')
        warnings += translated + media_warnings
    profile = next(p for p in candidate_autoip_profiles()
                   if p.capability.value == expected['operation'] and p.visual_class.value == visual)
    if any(key not in profile.option_values or value not in profile.option_values[key]
           for key, value in plan['options'].items()):
        _reject('這個角色配方不支援所選輸出設定', 'autoip_options_unsupported')
    plan.update(capability=profile.capability.value, characters=[selected.to_dict()], assets=[asset.to_dict()],
                action_id=profile.action_id, feature_id=profile.feature_id, profile_key=profile.key)
    plan['creative_instruction']['shot_id'] = 'compute-' + uid.hex + '-' + binding_hash
    return {'plan': plan, 'origin': origin, 'warnings': warnings}
