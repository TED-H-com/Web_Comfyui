"""Small reviewed production chains; UI supplies intent, never workflow code."""
from copy import deepcopy

from compute_routing import ComputeRoutingError, LANES, _limits


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
    ]


def build_plan(body):
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
    placements = body.get('placements', {})
    if not isinstance(placements, dict) or set(placements) - {'image', 'video'} or any(v not in ('auto', *LANES) for v in placements.values()):
        raise ComputeRoutingError('不支援的運算平台')
    limits = _limits(body.get('limits'))
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
