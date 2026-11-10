"""First-party hosted recipes from reviewed native builders, without runtime I/O.

These API graphs describe the required deployment. They do not assert that a
provider has those nodes/models, that a GPU fits them, or that a paid job passed.
Binding defaults mirror authored form defaults. Finite value maps preserve
native aspect/resolution/frame semantics. Explicit file arities avoid silently
losing optional references. No server import, credential discovery or file read.
"""
from __future__ import annotations

import hashlib
import json

try:
    from . import qwen_image21 as Q
    from . import minimax_h3_native as N
except ImportError:
    import qwen_image21 as Q
    import minimax_h3_native as N

SOURCE_REVISION = '01850c166af1356e2d13348a6bdfb628eeedc0ab'
Z_NEGATIVE = '文字、水印、logo、过度磨皮、塑料皮肤、畸形手指、多余手指、断指、严重解剖错误、脸部崩坏'
WAN_NEGATIVE = ('色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，'
                '整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，'
                '画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，'
                '杂乱的背景，三条腿，背景人很多，倒着走')
ASPECTS = {'portrait': (768, 1344), 'landscape': (1344, 768), 'square': (992, 992),
           'portrait-preview': (480, 864), 'landscape-preview': (864, 480), 'square-preview': (640, 640)}
MODEL_KEYS = {'unet_name', 'ckpt_name', 'clip_name', 'clip_name1', 'clip_name2', 'vae_name', 'lora_name'}
_NO_DEFAULT = object()


def _node(kind, **inputs):
    return {'class_type': kind, 'inputs': inputs}


def _bind(source, node, field, default=_NO_DEFAULT, values=None):
    result = {'source': source, 'node_id': str(node), 'input': field}
    if default is not _NO_DEFAULT:
        result['default'] = default
    if values is not None:
        result['value_map'] = [{'from': source_value, 'to': target} for source_value, target in values]
    return result


def _setting(key, node, field=None, default=_NO_DEFAULT, values=None):
    return _bind('settings.' + key, node, field or key, default, values)


def _template(id_, feature, label, description, graph, bindings, *, output='image', fixed=None, sources=(), requirements=()):
    models = sorted({value for node in graph.values() for key, value in node['inputs'].items()
                     if key in MODEL_KEYS and isinstance(value, str)})
    nodes = sorted({node['class_type'] for node in graph.values()})
    input_slots = [b['source'][6:] for b in bindings if b['source'].startswith('files.')]
    recipe = {'feature_id': feature, 'recipe_id': id_, 'label': label, 'output_kind': output,
              'workflow': graph, 'bindings': bindings, 'fixed_settings': fixed or {},
              # Structural placeholder only: guided setup requires explicit cost.
              'estimated_cost': 1}
    return {'id': id_, 'label': label, 'feature_id': feature, 'description': description,
            'recipe': recipe, 'input_slots': input_slots, 'cost_requires_input': True,
            'requirements': [
                '先在自己的供應商環境安裝並驗證以下模型與節點；尚未驗證遠端 GPU、記憶體或付費權益。',
                '模型：' + ('、'.join(models) if models else '無圖內模型載入器；自訂節點的內部依賴仍需另外確認。'), '節點：' + '、'.join(nodes),
                '費用 1 是結構占位值，不是報價；儲存前必須輸入自己的單次估計值。', *requirements],
            'models': models, 'node_types': nodes, 'validation': 'contract_tested',
            'source_revision': SOURCE_REVISION, 'source_files': list(sources),
            'workflow_sha256': hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}


def _zimage():
    graph = {
        '1': _node('UNETLoader', unet_name='z_image_turbo_bf16.safetensors', weight_dtype='default'),
        '2': _node('CLIPLoader', clip_name='qwen_3_4b.safetensors', type='lumina2', device='default'),
        '3': _node('VAELoader', vae_name='ae.safetensors'),
        '4': _node('CLIPTextEncode', text='Template prompt', clip=['2', 0]),
        '5': _node('CLIPTextEncode', text=Z_NEGATIVE, clip=['2', 0]),
        '6': _node('EmptySD3LatentImage', width=1088, height=1920, batch_size=1),
        '7': _node('KSampler', model=['1', 0], seed=0, steps=8, cfg=1.0, sampler_name='res_multistep',
                   scheduler='simple', positive=['4', 0], negative=['5', 0], latent_image=['6', 0], denoise=1.0),
        '9': _node('VAEDecode', samples=['7', 0], vae=['3', 0]),
        '12': _node('SaveImage', images=['9', 0], filename_prefix='WebComfy/Native/ZImage'),
    }
    bindings = [_bind('prompt', 4, 'text'), _setting('negative', 5, 'text', Z_NEGATIVE),
                _setting('width', 6, default=1088), _setting('height', 6, default=1920),
                _setting('batchSize', 6, 'batch_size', 1), _setting('seed', 7, default=-1),
                _setting('steps', 7, default=8), _setting('cfg', 7, default=1.0),
                _setting('denoise', 7, default=1.0), _setting('sampler', 7, 'sampler_name', 'res_multistep'),
                _setting('scheduler', 7, default='simple')]
    return _template('native-zimage-text2img-v1', 'text2img', 'Z-Image Turbo 原生文生圖',
        '沿用原生 Z-Image Turbo 圖；保留精確寬高、負向提示、步數、CFG、張數與種子。此版不含 DOPSD LoRA。',
        graph, bindings, fixed={'useDopsd': False}, sources=['webui/server.py#zimage_prompt'])


def _flux():
    graph = {
        '1': _node('UNETLoader', unet_name='flux1-dev-kontext_fp8_scaled.safetensors', weight_dtype='default'),
        '2': _node('DualCLIPLoader', clip_name1='clip_l.safetensors', clip_name2='t5xxl_fp8_e4m3fn_scaled.safetensors', type='flux', device='default'),
        '3': _node('VAELoader', vae_name='ae.safetensors'),
        '4': _node('LoadImage', image='input.png'),
        '5': _node('FluxKontextImageScale', image=['4', 0]),
        '6': _node('VAEEncode', pixels=['5', 0], vae=['3', 0]),
        '7': _node('CLIPTextEncode', text='Template instruction', clip=['2', 0]),
        '8': _node('ReferenceLatent', conditioning=['7', 0], latent=['6', 0]),
        '9': _node('FluxGuidance', conditioning=['8', 0], guidance=2.5),
        '10': _node('ConditioningZeroOut', conditioning=['7', 0]),
        '11': _node('KSampler', model=['1', 0], seed=0, steps=20, cfg=1.0, sampler_name='euler', scheduler='simple',
                    positive=['9', 0], negative=['10', 0], latent_image=['6', 0], denoise=1.0),
        '12': _node('VAEDecode', samples=['11', 0], vae=['3', 0]),
        '13': _node('SaveImage', images=['12', 0], filename_prefix='WebComfy/Native/FluxKontext'),
    }
    bindings = [_bind('prompt', 7, 'text'), _bind('files.image', 4, 'image'),
                _setting('guidance', 9, default=2.5), _setting('seed', 11, default=-1),
                _setting('steps', 11, default=20), _setting('denoise', 11, default=1.0),
                _setting('sampler', 11, 'sampler_name', 'euler'), _setting('scheduler', 11, default='simple')]
    return _template('native-flux-kontext-v1', 'img2img', 'FLUX.1 Kontext 原生指令改圖',
        '單張原圖 → Kontext 縮放與參考 latent → 指令重新生成。保留 Guidance、Denoise 與步數；沒有獨立負向提示介面。',
        graph, bindings, sources=['webui/server.py#flux_kontext_prompt'],
        requirements=['FLUX 模型另有使用條款；此目錄不代替遠端部署或模型授權。'])


def _qwen(edit=False, count=1, character=False):
    slots = ['image' if i == 1 else f'image{i}' for i in range(1, count + 1)] if edit else []
    settings = {'instruction' if edit else 'positive': 'Template instruction', 'visualStyle': 'realistic'}
    uploads = {slot: slot + '.png' for slot in slots}
    graph = (Q.compile_character_prompt(settings, uploads, seed=0, prefix='WebComfy/Native/QwenCharacter') if character
             else Q.compile_prompt(settings, uploads, edit=edit, seed=0, prefix='WebComfy/Native/Qwen21'))
    # The pure compiler's edit branch leaves latent_image pointing at node4;
    # only generation has an EmptyLatentImage node6. No dangling node6 is added.
    bindings = [_bind('prompt', 4, 'prompt'), _setting('negative', 4, 'negative_prompt', ''),
                _setting('seed', 7, default=-1), _setting('steps', 7, default=25), _setting('cfg', 7, default=1.0)]
    if edit:
        bindings.append(_setting('resolution', 4, default='1024', values=[(str(n), n) for n in (1024, 1536, 2048)]))
        bindings.extend(_bind('files.' + slot, 11 + index, 'image') for index, slot in enumerate(slots))
        id_ = 'native-qwen21-character-v1' if character else f'native-qwen21-edit-{count}-v1'
        feature = 'character-photoshoot' if character else 'qwen21-edit'
        label = 'Qwen 2.1 角色搭配寫真' if character else f'Qwen 2.1 原生改圖 · {count} 張參考'
        description = ('完整沿用角色身分與畫風提示，角色原圖仍由伺服器授權綁定。' if character else
                       f'按 image 至 image{count if count > 1 else ""} 的順序提供恰好 {count} 張圖，保留 <image1> 等原生引用語法。')
    else:
        bindings += [_setting('aspectRatio', 5, 'aspect_ratio', '1:1', values=list(Q.ASPECTS.items())),
                     _setting('megapixels', 5, default='1', values=[(str(n), float(n)) for n in (1, 2, 4)])]
        id_, feature, label = 'native-qwen21-text2img-v1', 'qwen21-text2img', 'Qwen 2.1 原生文生圖'
        description = '原生 ResolutionSelector 比例與 1／2／4 MP；保留負向提示、步數、CFG 與種子。'
    return _template(id_, feature, label, description, graph, bindings,
        sources=['webui/qwen_image21.py#compile_character_prompt' if character else 'webui/qwen_image21.py#compile_prompt',
                 'manifests/qwen_image21/models.lock.json'],
        requirements=['模型鎖定：Comfy-Org/Qwen-Image-2.1 @ ace0edeb3791a594ddfa36ed5f41a178a394e921。',
                      '節點基準：ComfyUI @ c194dd00cd42aa18d9dbf27d977bf6b85d9ea565；須包含 TextEncodeQwenImage21 與所列節點。'])


def _wan():
    graph = {
        '1': _node('UNETLoader', unet_name='wan2.1_i2v_480p_14B_fp8_scaled.safetensors', weight_dtype='default'),
        '2': _node('LoraLoaderModelOnly', model=['1', 0], lora_name='Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors', strength_model=1.0),
        '3': _node('ModelSamplingSD3', model=['2', 0], shift=8.0),
        '4': _node('CLIPLoader', clip_name='umt5_xxl_fp8_e4m3fn_scaled.safetensors', type='wan', device='default'),
        '5': _node('VAELoader', vae_name='wan_2.1_vae.safetensors'),
        '6': _node('CLIPVisionLoader', clip_name='clip_vision_h.safetensors'),
        '7': _node('LoadImage', image='input.png'),
        '8': _node('CLIPVisionEncode', clip_vision=['6', 0], image=['7', 0], crop='center'),
        '9': _node('CLIPTextEncode', text='Template motion', clip=['4', 0]),
        '10': _node('CLIPTextEncode', text=WAN_NEGATIVE, clip=['4', 0]),
        '11': _node('WanImageToVideo', positive=['9', 0], negative=['10', 0], vae=['5', 0], width=480, height=832,
                    length=81, batch_size=1, clip_vision_output=['8', 0], start_image=['7', 0]),
        '12': _node('KSampler', model=['3', 0], seed=0, steps=6, cfg=1.0, sampler_name='euler', scheduler='simple',
                    positive=['11', 0], negative=['11', 1], latent_image=['11', 2], denoise=1.0),
        '13': _node('VAEDecode', samples=['12', 0], vae=['5', 0]),
        '14': _node('VHS_VideoCombine', images=['13', 0], frame_rate=16.0, loop_count=0,
                    filename_prefix='WebComfy/Native/Wan21', format='video/h264-mp4', pingpong=False, save_output=True),
    }
    bindings = [_bind('prompt', 9, 'text'), _bind('files.image', 7, 'image'), _setting('negative', 10, 'text', WAN_NEGATIVE),
                _setting('width', 11, default=480), _setting('height', 11, default=832), _setting('length', 11, default=81),
                _setting('fps', 14, 'frame_rate', 16), _setting('shift', 3, default=8.0),
                _setting('loraStrength', 2, 'strength_model', 1.0), _setting('seed', 12, default=-1),
                _setting('steps', 12, default=6), _setting('cfg', 12, default=1.0),
                _setting('sampler', 12, 'sampler_name', 'euler'), _setting('scheduler', 12, default='simple')]
    return _template('native-wan21-image-video-v1', 'img2video', 'Wan 2.1 原生圖生影片',
        '沿用 480p I2V 模型與 LightX2V LoRA，映射實際畫布、幀數、FPS、Shift 與負向提示。',
        graph, bindings, output='video', sources=['webui/server.py#wan_i2v_prompt'],
        requirements=['需要 ComfyUI-VideoHelperSuite 的 VHS_VideoCombine 與 ffmpeg；尚未驗證供應商的節點版本。'])


def _h3(kind, *, pictures=0, videos=0, audios=0):
    common = dict(prompt='Template scene', resolution=(1344, 768), frames=124, seed=0, acceleration='stock')
    slots, bindings = [], []
    if kind == 'text':
        feature, label = 'h3-text-video', 'MiniMax H3 原生文字有聲影片'
        graph = N.build_t2v_prompt(**common, output_prefix='Castle/minimax-h3/t2v/catalog')
    elif kind in ('first', 'last', 'first-last'):
        feature = 'h3-image-video'
        label = {'first': 'MiniMax H3 原生首格影片', 'last': 'MiniMax H3 原生尾格影片', 'first-last': 'MiniMax H3 原生首尾格影片'}[kind]
        slots = (['image'] if kind != 'last' else []) + (['last_frame'] if kind != 'first' else [])
        graph = N.build_i2v_prompt(**common, first_frame='image.png' if 'image' in slots else None,
            last_frame='last_frame.png' if 'last_frame' in slots else None, output_prefix='Castle/minimax-h3/i2v/catalog')
        bindings = [_bind('files.' + slot, 15 + index, 'image') for index, slot in enumerate(slots)]
        for index in range(len(slots)):
            bindings.extend([
                _setting('aspect', 17 + index, 'target_width', 'landscape', values=[(k, v[0]) for k, v in ASPECTS.items()]),
                _setting('aspect', 17 + index, 'target_height', 'landscape', values=[(k, v[1]) for k, v in ASPECTS.items()]),
            ])
    else:
        feature = 'h3-reference-video'
        slots = ['reference' if i == 1 else f'reference{i}' for i in range(1, pictures + 1)]
        image_names = tuple(slot + '.png' for slot in slots)
        slots += [f'video{i}' for i in range(1, videos + 1)] + [f'audio{i}' for i in range(1, audios + 1)]
        graph = N.build_ref2va_prompt(**common, reference_images=image_names,
            reference_videos=tuple(f'video{i}.mp4' for i in range(1, videos + 1)),
            reference_audios=tuple(f'audio{i}.wav' for i in range(1, audios + 1)), output_prefix='Castle/minimax-h3/ref2va_image/catalog')
        bindings = [_bind('files.' + ('reference' if i == 0 else f'reference{i+1}'), 15 + i, 'image') for i in range(pictures)]
        bindings += [_bind(f'files.video{i+1}', 30 + i, 'video') for i in range(videos)]
        bindings += [_bind(f'files.audio{i+1}', 40 + i, 'audio') for i in range(audios)]
        label = f'MiniMax H3 原生多參考 · 圖{pictures}／影{videos}／音{audios}'
    graph['14']['inputs']['filename_prefix'] = 'WebComfy/Native/H3'
    bindings = [_bind('prompt', 5, 'prompt'), _setting('aspect', 5, 'width', 'landscape', values=[(k, v[0]) for k, v in ASPECTS.items()]),
                _setting('aspect', 5, 'height', 'landscape', values=[(k, v[1]) for k, v in ASPECTS.items()]),
                _setting('frames', 5, 'length', '124', values=[(value, frames) for frames in N.LEGAL_FRAME_LENGTHS for value in (str(frames), frames)]),
                _setting('seed', 6, 'noise_seed', -1), *bindings]
    suffix = kind if kind != 'reference' else f'reference-{pictures}' if not videos and not audios else f'reference-i{pictures}-v{videos}-a{audios}'
    is_keyframe = kind in ('first', 'last', 'first-last')
    template = _template(f'native-h3-{suffix}-v{2 if is_keyframe else 1}', feature, label,
        '官方 FL2VA／Ref2VA 模型；保留六種原生／預覽畫布、124–362 合法幀數與有聲 MP4。Stock 20 步；此配方不使用 PRISM 或快取加速。',
        graph, bindings, output='video', fixed={'acceleration': 'stock'},
        sources=['webui/minimax_h3_native.py#build_t2v_prompt' if kind == 'text' else
                 'webui/minimax_h3_native.py#build_i2v_prompt' if kind in ('first', 'last', 'first-last') else
                 'webui/minimax_h3_native.py#build_ref2va_prompt', 'webui/minimax_h3_product.py#compile_product_prompt',
                 'manifests/minimax_h3/models.lock.json'],
        requirements=['模型鎖定：Comfy-Org/MiniMax-H3 @ 014cd40f7e177756c6b2473c0d93b1c89a790dd2；模型授權另見 H3 文件。',
                      '節點基準：ComfyUI @ c2bcbecd82ec5ae66594340b395c24ef0217b238；需原生 MiniMax H3 與音訊 VAE 節點。',
                      '恰好提供此範本列出的素材；目前託管服務最多 8 個檔案，不能忽略其餘參考。',
                      *(['影片參考需要 VideoHelperSuite 的上傳型 VHS_LoadVideo；音軌引用順序沿用原生 H3。'] if videos else [])])
    if is_keyframe:
        template['adaptation'] = 'preserve_aspect_black_pad'
        template['description'] += ' 首尾格等比例縮放、完整保留並置中補黑邊；不裁切，也不生成延伸背景。'
        template['requirements'].append('需要內建 ResizeAndPadImage（已檢視 ComfyUI @ c194dd00cd42aa18d9dbf27d977bf6b85d9ea565）；黑邊會進入影片條件。新幾何配方須重新驗證，未宣稱供應商執行通過。')
    return template


VHS_REVISION = '4ee72c065db22c9d96c2427954dc69e7b908444b'

def _video_upload(cap):
    return _node('VHS_LoadVideo', video='video.mp4', force_rate=0.0, custom_width=0, custom_height=0,
                 frame_load_cap=cap, skip_first_frames=0, select_every_nth=1)

def _adapted(template):
    template['adaptation'] = 'upload_backed_video_loader'
    template['requirements'].append('已將本機路徑載入器替換為上傳型 VHS_LoadVideo；此是明示改編，尚未驗證供應商執行。')
    template['requirements'].append('VideoHelperSuite @ ' + VHS_REVISION + '；上傳／路徑型共用解碼器及四個輸出插槽。')
    template['source_files'].append('custom_nodes.txt#ComfyUI-VideoHelperSuite')
    return template

def _lipsync():
    graph = {
        '1': _video_upload(300),
        '2': _node('LoadAudio', audio='voice.wav'),
        '3': _node('Wav2Lip', images=['1',0], mode='sequential', face_detect_batch=8, audio=['2',0]),
        '4': _node('VHS_VideoCombine', images=['3',0], audio=['3',1], frame_rate=30.0, loop_count=0,
                    filename_prefix='WebComfy/Adapted/Lipsync', format='video/h264-mp4', pingpong=False, save_output=True),
    }
    bindings = [_bind('files.video',1,'video'), _bind('files.voice',2,'audio'),
                _setting('maxFrames',1,'frame_load_cap',300), _setting('faceBatch',3,'face_detect_batch',8),
                _setting('mode',3,default='sequential',values=[(v,v) for v in ('sequential','repetitive')])]
    return _adapted(_template('adapted-wav2lip-upload-v1','lipsync','Wav2Lip 上傳影片與語音對嘴',
        '使用已授權上傳的 video＋voice；保留影格模式、讀取上限與臉部批次，沿用 30 FPS 有聲 MP4。',
        graph,bindings,output='video',fixed={'voiceId':''},sources=['webui/server.py#lipsync_prompt'],
        requirements=['需要部署相容 Wav2Lip 自訂節點、其 checkpoint 與人臉偵測依賴；registry 未固定這些內部權重。',
                      '支援上傳語音或 WebUI 完成擁有者授權解析的聲音庫音檔；未解析的非空 voiceId 會拒絕。']))

def _video_loop():
    graph = {
        '1': _video_upload(600),
        '2': _node('VHS_VideoInfo',video_info=['1',3]),
        '3': _node('VHS_VideoCombine',images=['1',0],frame_rate=['2',5],loop_count=4,
                    filename_prefix='WebComfy/Adapted/VideoLoop',format='video/h264-mp4',pingpong=False,save_output=True),
    }
    bindings = [_bind('files.video',1,'video'), _setting('maxFrames',1,'frame_load_cap',600),
                _setting('repeat',3,'loop_count',4), _setting('pingpong',3,default=False)]
    return _adapted(_template('adapted-vhs-video-loop-v1','video-loop','VHS 上傳影片循環',
        '沿用來源影片影格率，保留重複次數、正反播放與讀取上限；與原工具相同，輸出不含音訊。',
        graph,bindings,output='video',sources=['webui/server.py#video_loop_prompt'],
        requirements=['不需擴散模型；需要 VideoHelperSuite、ffmpeg 及足夠的解碼記憶體。']))

def _dance():
    # Native animate-mode literal, with only upload loading and output prefix adapted.
    graph = {'1': {'class_type': 'UNETLoader',
           'inputs': {'unet_name': 'wan2.1_14B_SCAIL_2_fp8_scaled.safetensors', 'weight_dtype': 'default'}},
     '2': {'class_type': 'LoraLoaderModelOnly',
           'inputs': {'model': ['1', 0],
                      'lora_name': 'Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors',
                      'strength_model': 1.0}},
     '3': {'class_type': 'ModelSamplingSD3', 'inputs': {'model': ['2', 0], 'shift': 5.0}},
     '4': {'class_type': 'WanContextWindowsManual',
           'inputs': {'model': ['3', 0],
                      'context_length': 81,
                      'context_overlap': 29,
                      'context_schedule': 'standard_static',
                      'context_stride': 1,
                      'closed_loop': False,
                      'fuse_method': 'pyramid',
                      'freenoise': True,
                      'retain_first_frame': False,
                      'split_conds_to_windows': False}},
     '5': {'class_type': 'CLIPLoader',
           'inputs': {'clip_name': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'type': 'wan', 'device': 'default'}},
     '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'Template dance', 'clip': ['5', 0]}},
     '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'TEMP_NEGATIVE', 'clip': ['5', 0]}},
     '8': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'wan_2.1_vae.safetensors'}},
     '9': {'class_type': 'CLIPVisionLoader', 'inputs': {'clip_name': 'clip_vision_h.safetensors'}},
     '10': {'class_type': 'LoadImage', 'inputs': {'image': 'reference.png'}},
     '11': {'class_type': 'ResizeImageMaskNode',
            'inputs': {'input': ['10', 0],
                       'resize_type': 'scale total pixels',
                       'resize_type.megapixels': 0.5,
                       'scale_method': 'nearest-exact'}},
     '12': {'class_type': 'ResizeImageMaskNode',
            'inputs': {'input': ['11', 0],
                       'resize_type': 'scale to multiple',
                       'resize_type.multiple': 32,
                       'scale_method': 'nearest-exact'}},
     '13': {'class_type': 'CLIPVisionEncode', 'inputs': {'clip_vision': ['9', 0], 'image': ['12', 0], 'crop': 'none'}},
     '14': {'class_type': 'GetImageSize', 'inputs': {'image': ['12', 0]}},
     '15': {'class_type': 'VHS_LoadVideo',
            'inputs': {'video': 'video.mp4',
                       'force_rate': 16.0,
                       'custom_width': 0,
                       'custom_height': 0,
                       'frame_load_cap': 133,
                       'skip_first_frames': 0,
                       'select_every_nth': 1}},
     '16': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'sam3.1_multiplex_fp16.safetensors'}},
     '17': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'human', 'clip': ['16', 1]}},
     '18': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'human', 'clip': ['16', 1]}},
     '19': {'class_type': 'SAM3_VideoTrack',
            'inputs': {'images': ['15', 0],
                       'model': ['16', 0],
                       'detection_threshold': 0.5,
                       'max_objects': 0,
                       'detect_interval': 1,
                       'conditioning': ['17', 0]}},
     '20': {'class_type': 'SAM3_VideoTrack',
            'inputs': {'images': ['12', 0],
                       'model': ['16', 0],
                       'detection_threshold': 0.5,
                       'max_objects': 0,
                       'detect_interval': 1,
                       'conditioning': ['18', 0]}},
     '21': {'class_type': 'SCAIL2ColoredMask',
            'inputs': {'driving_track_data': ['19', 0],
                       'ref_track_data': ['20', 0],
                       'object_indices': '',
                       'sort_by': 'area',
                       'replacement_mode': False}},
     '22': {'class_type': 'WanSCAILToVideo',
            'inputs': {'positive': ['6', 0],
                       'negative': ['7', 0],
                       'vae': ['8', 0],
                       'width': ['14', 0],
                       'height': ['14', 1],
                       'length': 133,
                       'batch_size': 1,
                       'pose_strength': 1.0,
                       'pose_start': 0.0,
                       'pose_end': 1.0,
                       'video_frame_offset': 0,
                       'previous_frame_count': 5,
                       'pose_video': ['15', 0],
                       'pose_video_mask': ['21', 0],
                       'reference_image': ['12', 0],
                       'reference_image_mask': ['21', 1],
                       'clip_vision_output': ['13', 0],
                       'replacement_mode': False}},
     '23': {'class_type': 'KSampler',
            'inputs': {'model': ['4', 0],
                       'seed': 0,
                       'steps': 6,
                       'cfg': 1.0,
                       'sampler_name': 'euler',
                       'scheduler': 'simple',
                       'positive': ['22', 0],
                       'negative': ['22', 1],
                       'latent_image': ['22', 2],
                       'denoise': 1.0}},
     '24': {'class_type': 'VAEDecode', 'inputs': {'samples': ['23', 0], 'vae': ['8', 0]}},
     '25': {'class_type': 'VHS_VideoCombine',
            'inputs': {'images': ['24', 0],
                       'audio': ['15', 2],
                       'frame_rate': 16.0,
                       'loop_count': 0,
                       'filename_prefix': 'WebComfy/Adapted/SCAIL',
                       'format': 'video/h264-mp4',
                       'pingpong': False,
                       'save_output': True}}}
    graph['7']['inputs']['text'] = WAN_NEGATIVE
    bindings = [_bind('prompt',6,'text'), _bind('files.reference',10,'image'), _bind('files.video',15,'video'),
                _setting('negative',7,'text',WAN_NEGATIVE), _setting('seed',23,default=-1),
                _setting('steps',23,default=6), _setting('cfg',23,default=1.0),
                _setting('shift',3,default=5.0), _setting('loraStrength',2,'strength_model',1.0),
                _setting('poseStrength',22,'pose_strength',1.0),
                _setting('length',15,'frame_load_cap',133,values=[(n,n) for n in range(81,162,4)]),
                _setting('length',22,default=133,values=[(n,n) for n in range(81,162,4)]),
                _setting('fps',15,'force_rate',16), _setting('fps',25,'frame_rate',16),
                _setting('sampler',23,'sampler_name','euler'), _setting('scheduler',23,default='simple')]
    return _adapted(_template('adapted-scail-animate-upload-v1','dance','SCAIL 上傳動作影片 · Animate',
        '角色參考圖＋動作影片，保留原生姿態、遮罩、來源音訊與採樣圖；只接受公開表單 Animate 模式和 81–161 幀。',
        graph,bindings,output='video',fixed={'mode':'animate','contextLength':81},
        sources=['webui/server.py#scail_dance_prompt'],
        requirements=['需要 SAM3、SCAIL2ColoredMask、WanContextWindowsManual、ResizeImageMaskNode 等列出的實際節點。',
                      'Context 固定 81 幀；使用者另填 contextLength 或 replace 分段模式時拒絕，不會自動轉換。']))


UNSUPPORTED = [
    {'feature_id': 'anime-scene-card', 'reason': '需要伺服器綁定的透明角色與核准場景版位；尚未提供完整的版位／遮罩語意映射。'},
    {'feature_id': 'image-blend', 'reason': '去背模型、遮罩與合成版位有專屬處理；尚未建立逐項保留其參數的託管配方。'},
    {'feature_id': 'character-angles', 'reason': '需要 InsightFace 模型目錄與多角度身分處理；不能只用一般 prompt 映射替代。'},
    {'feature_id': 'face-swap', 'reason': '目前公開 API 已停用此工具，且現有圖輸出動畫 WebP；不能將它標成已支援的 MP4 影片配方。'},
    {'feature_id': 'voice-batch', 'reason': '批次語音包含聲音庫授權及服務編排，不是單一 Comfy API 圖。'},
    {'feature_id': 'voice-library', 'reason': '這是聲音資產管理功能，不是可提交的生成圖。'},
    {'feature_id': 'post-generator', 'reason': '文字企劃使用外部文字服務與產品流程，沒有對應的 Comfy 模型圖。'},
    {'feature_id': 'clipmode-character-photoshoot', 'reason': '需要私有角色 LoRA／InstantID 身分檔案、觸發詞與生成後身分檢查；不能僅搬移圖或上傳任意臉圖。'},
    {'feature_id': 'voice-speak', 'reason': '聲音庫與角色人格授權、語音生成後處理仍由原本的服務流程管理；沒有已審查的遠端節點契約。'},
    {'feature_id': 'voxcpm-tts', 'reason': '依賴本機語音模型、角色聲音設定與服務程序；沒有可直接移植的純 Comfy API 圖。'},
    {'feature_id': 'voice-clone', 'reason': '聲音擁有權與模型載入流程尚未建立託管契約，不能自動傳送聲音資料。'},
    {'feature_id': 'first-last-frame', 'reason': '現有 Wan 首尾格圖輸出動畫 WebP；目前託管影片成果契約要求可探測的影片容器，尚未轉接。'},
]


def recipe_catalog():
    """Return fresh JSON values; every graph and input map is side-effect free."""
    templates = [_zimage(), _flux(), _qwen(), *[_qwen(edit=True, count=n) for n in range(1, 5)],
                 _qwen(edit=True, character=True), _wan(), _lipsync(), _video_loop(), _dance(), _h3('text'), _h3('first'), _h3('last'), _h3('first-last')]
    templates += [_h3('reference', pictures=n) for n in range(1, 9)]
    templates += [_h3('reference', videos=n) for n in range(1, 4)]
    templates += [_h3('reference', audios=n) for n in range(1, 4)]
    templates += [_h3('reference', pictures=1, videos=1, audios=1)]
    return {'templates': templates, 'unsupported': [dict(row) for row in UNSUPPORTED],
            'source_revision': SOURCE_REVISION, 'validation': 'contract_tested'}
