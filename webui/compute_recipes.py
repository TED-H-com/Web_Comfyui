"""Small reviewed production chains; UI supplies intent, never workflow code."""
from copy import deepcopy
from collections.abc import Mapping
import math

from compute_routing import ComputeRoutingError, LANES, _limits

_REQUEST_FIELDS = {'featureId', 'settings', 'files', 'characterId', 'action', 'variantId',
                   'computeVisualClass', 'computeAutoipVideo'}
_FIELD_METADATA = {'id', 'label', 'type', 'default', 'options', 'min', 'max', 'step', 'required',
                   'description', 'placeholder', 'rows', 'maxLength', 'display', 'showWhen',
                   'hideWhen', 'group', 'hint'}
_EXTENSION_KINDS = {'.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
                    '.mp4': 'video', '.mov': 'video', '.webm': 'video',
                    '.wav': 'audio', '.mp3': 'audio', '.flac': 'audio', '.m4a': 'audio'}


def media_kinds(accept):
    result = set()
    for item in str(accept or '').lower().split(','):
        item = item.strip()
        if item.split('/')[0] in {'image', 'audio', 'video'}:
            result.add(item.split('/')[0])
        elif item in _EXTENSION_KINDS:
            result.add(_EXTENSION_KINDS[item])
    return sorted(result)


def feature_registry(features):
    """Export only catalog-authored forms; this is not an execution permission.

    The HTTP host filters operator/character visibility and repeats common
    authorization at admission and dispatch. No workflow graph or builder name
    is returned to the browser.
    """
    values = features.values() if isinstance(features, Mapping) else features
    result = []
    for feature in values or []:
        if not isinstance(feature, Mapping) or feature.get('apiEnabled') is False or not feature.get('builder'):
            continue
        kind = feature.get('output_kind', feature.get('outputKind'))
        if kind not in {'image', 'audio', 'video'}:
            kind = feature.get('studio') if feature.get('studio') in {'image', 'video'} else (
                'audio' if feature.get('id') in {'voice-speak', 'voxcpm-tts'} else None)
        result.append({'id': feature['id'], 'label': feature.get('title', feature['id']),
            'description': feature.get('subtitle', ''), 'studio': feature.get('studio', ''),
            'operatorOnly': bool(feature.get('operatorOnly')), 'characterOnly': bool(feature.get('characterOnly')),
            **({'minimumUploads': feature['minimumUploads']} if type(feature.get('minimumUploads')) is int else {}),
            'output_kind': kind,
            'fields': [{key: deepcopy(value) for key, value in field.items() if key in _FIELD_METADATA}
                       for field in feature.get('fields', []) if isinstance(field, Mapping) and field.get('id')],
            'uploads': [{**{key: deepcopy(value) for key, value in upload.items()
                           if key in {'id', 'label', 'accept', 'required', 'description'}},
                         'media_kinds': media_kinds(upload.get('accept'))}
                        for upload in feature.get('uploads', []) if isinstance(upload, Mapping) and upload.get('id')]})
    return result


def _form_settings(settings, feature):
    """Match the ordinary form's types and aspect-to-pixel serialization."""
    result = deepcopy(settings)
    fields = {field['id']: field for field in feature['fields']}
    allowed = set(fields)
    if any(field.get('type') == 'aspect' for field in fields.values()):
        allowed.update({'width', 'height'})
    if set(result) - allowed:
        raise ComputeRoutingError('流程包含未公開的表單設定')
    for key, field in fields.items():
        kind = field.get('type')
        if kind == 'aspect':
            dimensions = {(item['width'], item['height']) for item in field.get('options', [])
                          if isinstance(item, dict) and type(item.get('width')) is int and type(item.get('height')) is int}
            explicit = 'width' in result or 'height' in result
            selected = result.pop(key, None if explicit else field.get('default'))
            if selected is not None:
                choices = {f'{width}x{height}': (width, height) for width, height in dimensions}
                if not isinstance(selected, str) or selected not in choices:
                    raise ComputeRoutingError('畫面比例不在已公開的精確尺寸選項中')
                width, height = choices[selected]
                if explicit and (result.get('width'), result.get('height')) != (width, height):
                    raise ComputeRoutingError('畫面比例與寬高設定互相衝突')
                result.update(width=width, height=height)
            if (any(type(result.get(name)) is not int for name in ('width', 'height'))
                    or (result['width'], result['height']) not in dimensions):
                raise ComputeRoutingError('請提供完整且受支援的整數寬高')
            continue
        if key not in result:
            if field.get('required'):
                raise ComputeRoutingError('缺少必要的製作欄位：' + key)
            continue
        value = result[key]
        if kind == 'checkbox':
            if type(value) is not bool:
                raise ComputeRoutingError('勾選設定必須是布林值：' + key)
        elif kind in {'number', 'slider'}:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ComputeRoutingError('數值設定必須是有限數字：' + key)
            if (('min' in field and value < field['min']) or ('max' in field and value > field['max'])
                    or type(field.get('step')) is int and field['step'] >= 1 and value != int(value)):
                raise ComputeRoutingError('數值設定超出表單範圍：' + key)
        elif kind == 'select':
            options = [item.get('value') if isinstance(item, dict) else item for item in field.get('options', [])]
            if not any(type(value) is type(option) and value == option for option in options):
                raise ComputeRoutingError('設定不在已公開的選項中：' + key)
        elif (not isinstance(value, str) or '\x00' in value
              or len(value) > field.get('maxLength', 24000) or field.get('required') and not value.strip()):
            raise ComputeRoutingError('文字設定不正確：' + key)
    return result


def _custom_plan(plan, features):
    from compute_pipeline import validate_plan
    if features is None:
        raise ComputeRoutingError('自訂流程需要伺服器功能目錄')
    known = {feature['id']: feature for feature in feature_registry(features)}
    result = validate_plan(plan)
    by_id = {step['id']: step for step in result['steps']}
    for step in result['steps']:
        request = step['request']
        feature = known.get(request['featureId'])
        if not feature or set(request) - _REQUEST_FIELDS:
            raise ComputeRoutingError('自訂流程只能使用已開放的製作功能與表單設定')
        settings = request.get('settings', {})
        if not isinstance(settings, dict) or any(str(key).startswith('_') for key in settings):
            raise ComputeRoutingError('自訂流程不能指定伺服器內部設定')
        request['settings'] = _form_settings(settings, feature)
        uploads = {item['id']: item for item in feature['uploads']}
        files = request.get('files') or {}
        if set(files) - uploads.keys() or set(step['bindings']) - uploads.keys():
            raise ComputeRoutingError('流程包含此功能不支援的素材欄位')
        for value in files.values():
            if (not isinstance(value, dict) or set(value) - {'dataUrl', 'name', 'type', 'mimeType', 'size',
                                                               'serverFile', 'promptId', 'prompt_id'}):
                raise ComputeRoutingError('初始素材只能使用自己的上傳；不能指定路徑或網址')
        for slot, binding in step['bindings'].items():
            kinds = uploads[slot]['media_kinds']
            if 'expected_kind' not in binding and len(kinds) == 1:
                binding['expected_kind'] = kinds[0]
            if not binding.get('expected_kind') or binding['expected_kind'] not in kinds:
                raise ComputeRoutingError('請指定與輸入欄位相容的成果類型')
            upstream = known.get(by_id[binding['step_id']]['request']['featureId'])
            if upstream and upstream['output_kind'] and upstream['output_kind'] != binding['expected_kind']:
                raise ComputeRoutingError('前一步的成果類型不符合此素材欄位')
    return result


def recipes():
    common = [
        {'id': 'visual_class', 'label': '畫面風格', 'type': 'select', 'required': True,
         'default': 'anime', 'options': [{'value': 'anime', 'label': '動畫'}, {'value': 'realistic', 'label': '寫實'}]},
        {'id': 'motion_prompt', 'label': '影片動作與場景', 'type': 'textarea', 'required': True},
        {'id': 'native_frames', 'label': '工作流影片長度', 'type': 'select', 'default': '124',
         'description': '本機、Colab 與依此工作流部署的平台使用此長度。',
         'options': [{'value': '124', 'label': '約 5.2 秒'}, {'value': '226', 'label': '約 9.4 秒'}, {'value': '362', 'label': '約 15.1 秒'}]},
        {'id': 'autoip_seconds', 'label': 'AutoIP 影片秒數', 'type': 'number', 'default': 8, 'min': 2, 'max': 15,
         'description': '僅選到 AutoIP 時，改用此服務支援的影片規格。',
         'show_when': {'step_id': 'video', 'placements': ['auto', 'autoip']}},
        {'id': 'autoip_resolution', 'label': 'AutoIP 影片解析度', 'type': 'select', 'default': '480p',
         'options': [{'value': '480p', 'label': '480p'}, {'value': '768p', 'label': '768p'}, {'value': '2k', 'label': '2K'}],
         'show_when': {'step_id': 'video', 'placements': ['auto', 'autoip']}},
    ]
    return [
        {'id': 'text-image-video', 'label': '文字生圖 → 影片',
         'description': '先生成一張圖片，驗證完成後交給下一步製作影片。',
         'fields': [{'id': 'image_prompt', 'label': '圖片描述', 'type': 'textarea', 'required': True}, *deepcopy(common)],
         'steps': [{'id': 'image', 'label': '文字生圖', 'feature_id': 'text2img', 'depends_on': []},
                   {'id': 'video', 'label': '圖片製作影片', 'feature_id': 'h3-image-video', 'depends_on': ['image']}]},
        {'id': 'edit-image-video', 'label': '指令改圖 → 影片',
         'description': '先依指令修改圖片，再將確認完成的成果交給影片步驟。',
         'fields': [{'id': 'image', 'label': '參考圖片', 'type': 'file', 'required': True, 'accept': 'image/*'},
                    {'id': 'instruction', 'label': '修改指令', 'type': 'textarea', 'required': True}, *deepcopy(common)],
         'steps': [{'id': 'image', 'label': '指令改圖', 'feature_id': 'img2img', 'depends_on': []},
                   {'id': 'video', 'label': '圖片製作影片', 'feature_id': 'h3-image-video', 'depends_on': ['image']}]},
        {'id': 'voice-design-lipsync', 'label': '設計聲音 → 影片對嘴',
         'description': '依描述生成合成聲音，驗證音訊後交給真人影片對嘴；不綁定或驗證任何角色身份。',
         'fields': [
             {'id': 'video', 'label': '可清楚辨識臉部的真人影片', 'type': 'file', 'required': True, 'accept': 'video/*'},
             {'id': 'speech_text', 'label': '要說的話', 'type': 'textarea', 'required': True, 'maxLength': 2400},
             {'id': 'voice_style', 'label': '聲音描述', 'type': 'textarea', 'required': True,
              'description': '例如：溫和、清晰的中性聲線；依文字描述設計聲音，不使用真人聲音參考。'},
             {'id': 'sync_mode', 'label': '影片影格對應', 'type': 'select', 'default': 'sequential',
              'options': [{'value': 'sequential', 'label': '依語音長度伸縮'},
                          {'value': 'repetitive', 'label': '循環播放原影片'}]},
         ],
         'steps': [{'id': 'voice', 'label': '設計合成聲音', 'feature_id': 'voxcpm-tts', 'depends_on': []},
                   {'id': 'lipsync', 'label': '影片對嘴', 'feature_id': 'lipsync', 'depends_on': ['voice']}]},
    ]


def build_plan(body, *, features=None):
    if isinstance(body, dict) and 'plan' in body:
        if set(body) - {'pipeline_id', 'plan'}:
            raise ComputeRoutingError('自訂流程不能同時指定預設流程欄位')
        return _custom_plan(body['plan'], features)
    if not isinstance(body, dict) or set(body) - {'pipeline_id', 'recipe_id', 'inputs', 'placements', 'limits'}:
        raise ComputeRoutingError('多步製作請求格式不正確')
    recipe = next((item for item in recipes() if item['id'] == body.get('recipe_id')), None)
    if not recipe:
        raise ComputeRoutingError('找不到這個製作流程')
    inputs = body.get('inputs', {})
    allowed = {item['id'] for item in recipe['fields']}
    if not isinstance(inputs, dict) or set(inputs) - allowed:
        raise ComputeRoutingError('不支援的製作欄位')
    def prose(key):
        value = inputs.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 12000 or '\x00' in value:
            raise ComputeRoutingError('請填寫完整的畫面與動作描述')
        return value.strip()
    placements = body.get('placements', {})
    step_ids = {step['id'] for step in recipe['steps']}
    if not isinstance(placements, dict) or set(placements) - step_ids or any(v not in ('auto', *LANES) for v in placements.values()):
        raise ComputeRoutingError('不支援的運算平台')
    limits = _limits(body.get('limits'))
    if recipe['id'] == 'voice-design-lipsync':
        video = inputs.get('video')
        mode = inputs.get('sync_mode', 'sequential')
        speech = prose('speech_text')
        if len(speech) > 2400:
            raise ComputeRoutingError('要說的話單次最多 2400 字')
        if not isinstance(video, dict) or not video:
            raise ComputeRoutingError('請選擇自己可使用的真人影片')
        if mode not in {'sequential', 'repetitive'}:
            raise ComputeRoutingError('不支援的影片影格對應方式')
        return {'version': 1, 'steps': [
            {'id': 'voice', 'request': {'featureId': 'voxcpm-tts', 'settings': {
                'voiceEngine': 'voxcpm', 'mode': 'design', 'text': speech,
                'style': prose('voice_style'), 'language': '', 'accent': '',
                'cfg': 2.0, 'steps': 10, 'seed': -1, 'speakerGate': False}, 'files': {}},
             'placement': placements.get('voice', 'auto'), 'limits': limits},
            {'id': 'lipsync', 'request': {'featureId': 'lipsync',
                'settings': {'mode': mode, 'maxFrames': 300, 'faceBatch': 8}, 'files': {'video': video}},
             'placement': placements.get('lipsync', 'auto'), 'limits': limits, 'depends_on': ['voice'],
             'bindings': {'voice': {'step_id': 'voice', 'artifact_index': 0, 'expected_kind': 'audio'}}},
        ]}
    visual = inputs.get('visual_class', 'anime')
    if visual not in {'anime', 'realistic'}:
        raise ComputeRoutingError('請選擇動畫或寫實風格')
    frames = inputs.get('native_frames', '124')
    if frames not in {'124', '226', '362'}:
        raise ComputeRoutingError('不支援的影片長度')
    seconds = inputs.get('autoip_seconds', 8)
    try:
        seconds_int = int(seconds)
    except (ValueError, TypeError):
        raise ComputeRoutingError('AutoIP 影片長度需為 2–15 秒') from None
    if isinstance(seconds, bool) or str(seconds_int) != str(seconds) or not 2 <= seconds_int <= 15:
        raise ComputeRoutingError('AutoIP 影片長度需為 2–15 秒')
    resolution = inputs.get('autoip_resolution', '480p')
    if resolution not in {'480p', '768p', '2k'}:
        raise ComputeRoutingError('不支援的 AutoIP 解析度')
    if recipe['id'] == 'text-image-video':
        style = 'Anime illustration. ' if visual == 'anime' else 'Photorealistic photography. '
        first = {'featureId': 'text2img', 'settings': {'positive': style + prose('image_prompt'),
                 'width': 1024, 'height': 1024, 'steps': 8, 'cfg': 1, 'batchSize': 1, 'useDopsd': False, 'seed': -1}, 'files': {}}
    else:
        image = inputs.get('image')
        if not isinstance(image, dict) or not image:
            raise ComputeRoutingError('請選擇參考圖片')
        first = {'featureId': 'img2img', 'settings': {'instruction': prose('instruction'),
                 'steps': 20, 'guidance': 2.5, 'denoise': 1, 'seed': -1}, 'files': {'image': image}}
    first['computeVisualClass'] = visual
    video = {'featureId': 'h3-image-video', 'settings': {
        'integrated_multimodal_description': prose('motion_prompt'),
        'overall_soundscape': 'N/A', 'non_diegetic_music': 'None.',
        'aspect': 'landscape-preview', 'frames': frames, 'acceleration': 'stock', 'seed': -1},
        'files': {}, 'computeVisualClass': visual,
        'computeAutoipVideo': {'duration_seconds': seconds_int, 'resolution': resolution, 'aspect_ratio': '16:9'}}
    return {'version': 1, 'steps': [
        {'id': 'image', 'request': first, 'placement': placements.get('image', 'auto'), 'limits': limits},
        {'id': 'video', 'request': video, 'placement': placements.get('video', 'auto'), 'limits': limits,
         'depends_on': ['image'], 'bindings': {'image': {'step_id': 'image', 'artifact_index': 0}}},
    ]}
