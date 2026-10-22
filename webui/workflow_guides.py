"""Deterministic, offline workflow explanations shared by the app and static docs.

Only recognized node types and bounded scalar controls are interpreted. Prompts,
node titles, paths, URLs, credentials and arbitrary metadata are never echoed.
These guides explain techniques; they are not an execution or GPU certification.
"""
from __future__ import annotations

import math
from collections.abc import Mapping


SAMPLERS = frozenset({
    'euler', 'euler_cfg_pp', 'euler_ancestral', 'euler_ancestral_cfg_pp', 'heun',
    'heunpp2', 'dpm_2', 'dpm_2_ancestral', 'lms', 'dpm_fast', 'dpm_adaptive',
    'dpmpp_2s_ancestral', 'dpmpp_sde', 'dpmpp_sde_gpu', 'dpmpp_2m',
    'dpmpp_2m_sde', 'dpmpp_2m_sde_gpu', 'dpmpp_3m_sde', 'dpmpp_3m_sde_gpu',
    'ddpm', 'ddim', 'uni_pc', 'uni_pc_bh2', 'lcm', 'ipndm', 'ipndm_v', 'deis',
    'res_multistep', 'res_multistep_cfg_pp', 'gradient_estimation', 'er_sde',
})
SCHEDULERS = frozenset({'normal', 'karras', 'exponential', 'sgm_uniform', 'simple',
                        'ddim_uniform', 'beta', 'linear_quadratic', 'kl_optimal'})
# UI widget indices are used only for these reviewed, stable layouts. API-format
# graphs use explicit field names. Seed values are deliberately not published.
WIDGETS = {
    'KSampler': {'steps': 2, 'cfg': 3, 'sampler_name': 4, 'scheduler': 5, 'denoise': 6},
    'KSampler (Efficient)': {'steps': 2, 'cfg': 3, 'sampler_name': 4, 'scheduler': 5, 'denoise': 6},
    'KSamplerSelect': {'sampler_name': 0},
    'BasicScheduler': {'scheduler': 0, 'steps': 1, 'denoise': 2},
    'EmptyLatentImage': {'width': 0, 'height': 1, 'batch_size': 2},
    'EmptySD3LatentImage': {'width': 0, 'height': 1, 'batch_size': 2},
    'ImagePadForOutpaint': {'left': 0, 'top': 1, 'right': 2, 'bottom': 3, 'feathering': 4},
    'LoraLoader': {'strength_model': 1, 'strength_clip': 2},
    'LoraLoaderModelOnly': {'strength_model': 1},
    'FluxGuidance': {'guidance': 0},
    'CreateVideo': {'fps': 0},
    'VHS_VideoCombine': {},
}
CONTROLS = {
    'steps': (1, 2000, '採樣迭代數；增加步數不保證改善蒸餾模型的輸出。'),
    'cfg': (0, 100, '文字條件引導強度；合適範圍取決於模型。'),
    'guidance': (0, 100, '模型專用的引導設定，與一般 CFG 不一定相同。'),
    'denoise': (0, 1, '去噪範圍；圖生圖通常以較低值保留更多原圖。'),
    'width': (1, 32768, '此潛在畫布的寬度，單位為像素；後續仍可能縮放或裁切。'),
    'height': (1, 32768, '此潛在畫布的高度，單位為像素；後續仍可能縮放或裁切。'),
    'batch_size': (1, 4096, '批次大小；增加批次通常提高記憶體需求。'),
    'left': (0, 32768, '向左擴充的像素數。'),
    'top': (0, 32768, '向上擴充的像素數。'),
    'right': (0, 32768, '向右擴充的像素數。'),
    'bottom': (0, 32768, '向下擴充的像素數。'),
    'feathering': (0, 32768, '遮罩邊緣過渡寬度，單位為像素。'),
    'strength_model': (-10, 10, 'LoRA 對模型權重的作用強度；只代表此載入節點。'),
    'strength_clip': (-10, 10, 'LoRA 對文字編碼器的作用強度。'),
    'fps': (0.01, 1000, '封裝影格率，不代表來源影格數或實際影片時長。'),
    'frame_rate': (0.01, 1000, '輸出封裝影格率；請另外確認音訊同步。'),
}


def _nodes(graph):
    """Walk active graph instances, including only referenced UI subgraphs."""
    result = []
    def walk(document, inherited, stack=()):
        if not isinstance(document, Mapping) or len(stack) > 8 or len(result) >= 5000:
            return
        definitions = dict(inherited)
        section = document.get('definitions', {})
        if isinstance(section, Mapping):
            subgraphs = section.get('subgraphs', [])
            if isinstance(subgraphs, list):
                for sub in subgraphs:
                    if isinstance(sub, Mapping) and isinstance(sub.get('id'), str):
                        definitions[sub['id']] = sub
        extra = document.get('extra', {})
        groups = extra.get('groupNodes', {}) if isinstance(extra, Mapping) else {}
        if isinstance(groups, Mapping):
            for name, sub in groups.items():
                if isinstance(name, str) and isinstance(sub, Mapping):
                    definitions['workflow/' + name] = sub
        nodes = document.get('nodes')
        if isinstance(nodes, list):
            for node in nodes[:5000]:
                if not isinstance(node, Mapping) or node.get('mode', 0) in (2, 4):
                    continue
                kind = node.get('type')
                if not isinstance(kind, str):
                    continue
                if kind in definitions:
                    if kind not in stack:
                        walk(definitions[kind], definitions, (*stack, kind))
                else:
                    result.append((kind, node, False))
        else:
            for node in list(document.values())[:5000]:
                if isinstance(node, Mapping) and isinstance(node.get('class_type'), str):
                    result.append((node['class_type'], node, True))
    walk(graph, {})
    return result


def _number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high and math.isfinite(value)


def active_nodes(graph):
    """Return fresh active node dictionaries with canonical ``type`` fields.

    Expand referenced subgraph instances for dependency inspection. UUID wrapper
    IDs and unused/muted subgraphs are excluded. No graph objects are modified.
    Consumers must not publish arbitrary widget or input strings from this list.
    """
    return [dict(node, type=kind) for kind, node, _ in _nodes(graph)]


def _parameters(nodes):
    output = []
    counts = {}
    for kind, node, api in nodes:
        if kind not in WIDGETS:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        names = WIDGETS[kind]
        raw = node.get('inputs', {}) if api else node.get('widgets_values', [])
        if kind == 'VHS_VideoCombine':
            names = {'frame_rate': 0}
            if not isinstance(raw, Mapping):
                continue  # Legacy positional VHS layouts vary by node version.
        if api and not isinstance(raw, Mapping):
            continue
        input_slots = node.get('inputs', [])
        input_slots = input_slots if isinstance(input_slots, list) else []
        connected = {i.get('name') for i in input_slots
                     if isinstance(i, Mapping) and i.get('link') is not None} if not api else set()
        # An unknown KSampler positional layout must not relabel a seed as steps.
        if not api and kind.startswith('KSampler') and kind != 'KSamplerSelect':
            if not isinstance(raw, list) or len(raw) < 7 or raw[1] not in (None, 'fixed', 'increment', 'decrement', 'randomize'):
                continue
        for name, index in names.items():
            if name in connected:
                continue
            if isinstance(raw, Mapping):
                value = raw.get(name)
            elif isinstance(raw, list) and len(raw) > index:
                value = raw[index]
            else:
                continue
            if name == 'sampler_name':
                valid = isinstance(value, str) and value in SAMPLERS
                note = '採樣演算法；名稱直接取自此節點的已知欄位。'
            elif name == 'scheduler':
                valid = isinstance(value, str) and value in SCHEDULERS
                note = '噪聲時間表；需搭配模型及採樣器檢查。'
            else:
                low, high, note = CONTROLS[name]
                valid = _number(value, low, high)
            if valid:
                output.append({'name': f'{kind} #{counts[kind]} · {name}', 'value': value,
                               'note': note + ' 這是已存值，未解析連線運算或條件分支。'})
        if len(output) >= 48:
            return output[:48]
    return output


# Keys are internal method identifiers; all user-visible explanations are fixed
# prose, so malicious labels or instruction-like prompts cannot enter a guide.
METHODS = {
    'diffusion': ('擴散模型採樣', '在潛在表示上逐步去噪，將文字或影像條件轉成輸出；採樣器、噪聲時間表與模型需要搭配。'),
    'text': ('文字條件編碼', '先用相容文字編碼器將描述轉成模型條件；不同模型家族需要對應的編碼器。'),
    'vae': ('VAE 潛在表示', 'VAE 負責影像與壓縮潛在表示之間的轉換；模型與 VAE 不匹配可能造成色彩或解碼問題。'),
    'hand': ('手部深度修復', 'MeshGraphormer 估算手部結構，再透過深度控制與局部重繪修補；需檢查偵測遮罩是否涵蓋正確手部。'),
    'material': ('材質參考轉移', 'ZeST 與參考條件將材質外觀帶入指定物件；遮罩決定修改區域，光照與原有形狀仍需檢查。'),
    'outpaint': ('向外擴圖', '先擴大畫布並建立新增區域遮罩，再由模型補出邊界外的內容。'),
    'inpaint': ('遮罩局部重繪', '將遮罩與原圖組成修補條件，只針對選定範圍重繪；遮罩反相會改變被修改的區域。'),
    'segment': ('主體分割與遮罩', 'SAM、BiRefNet 或偵測器建立主體區域，後續可用於換背景、合成或局部細修。'),
    'control': ('ControlNet 結構引導', '將線稿、姿態或深度條件送入控制網路，約束生成構圖；控制模型必須與底模家族相容。'),
    'depth': ('深度預處理', '先由影像估算遠近關係，提供比文字更明確的幾何或空間條件。'),
    'pose': ('姿態預處理', '擷取人體或手部關鍵點來描述動作；姿態控制本身不保證人物身分一致。'),
    'line': ('線稿與邊緣條件', '將輪廓或邊緣轉為生成條件，可用於線稿上色與保留原有構圖。'),
    'ipadapter': ('IPAdapter 影像參考', '透過影像編碼器把參考圖轉為條件，影響外觀、風格或人物特徵；參考權重與遮罩會影響保留程度。'),
    'redux': ('Flux Redux 參考條件', '將參考圖的視覺特徵轉入 Flux 條件；它提供外觀參考，並非逐像素複製。'),
    'identity': ('人臉身分參考', 'FaceID、InstantID、PuLID 或換臉節點使用臉部特徵協助身分一致；仍需檢查角度、遮擋與融合邊界。'),
    'detail': ('區域細修', '偵測或遮罩切出局部區域，放大處理後再貼回原圖，可改善小臉、手部或其他細節。'),
    'tile': ('分塊處理', '把大圖切成區塊以分段處理，再合回完整影像；重疊與邊界融合會影響接縫。'),
    'upscale': ('超解析度與放大', '利用放大模型或重採樣提高尺寸；若再經擴散細化，也可能改變原有細節。'),
    'lora': ('LoRA 模型適配', '在底模上載入小型權重增量，調整身分、風格、控制或加速行為；不同底模的適配器不可直接混用。'),
    'gguf': ('GGUF 量化載入', '使用 GGUF 載入器讀取量化模型；可降低權重記憶體，但節點版本、速度與品質仍需實測。'),
    'differential': ('Differential Diffusion', '依遮罩的強弱控制各區域參與去噪的程度，使局部修改與保留區域之間更平順。'),
    'layer': ('圖層分離與合成', '利用圖層條件或分層模型處理前景、背景與透明度；需檢查 alpha 邊緣及每層的實際內容。'),
    'caption': ('影像理解與標籤', '以視覺語言模型或標籤器將圖片轉成文字條件；自動描述可能有誤，應在生成前校對。'),
    'llm': ('語言模型提示詞輔助', '先以語言模型整理描述或生成文字條件，再交給下游流程；輸出的敘述不等於已執行的設定。'),
    'remote': ('可設定的外部 API', '部分節點呼叫遠端服務；端點、帳戶與金鑰由使用者自行設定，是否計費依服務而定。'),
    'video': ('時間序列生成', '影片模型同時處理多個影格的時間關係；影格數、動作與鏡頭設定會影響記憶體與一致性。'),
    'audio': ('音訊條件或解碼', '圖中包含音訊處理；請區分參考音色、原音保留與新合成音訊，並檢查輸出同步。'),
    'interpolate': ('影格插補', '在既有影格之間合成中間畫面；提高封裝 FPS 時應維持片長，並檢查快速運動與切鏡處的偽影。'),
    'edit': ('指令式影像編輯', '結合原圖與文字修改指令，讓模型依語意調整畫面；原圖保留程度仍取決於模型與採樣設定。'),
    'cascade': ('Stable Cascade 分階段生成', '以不同階段的模型逐步建立潛在表示與細節；各階段模型和條件需要成套匹配。'),
    'realtime': ('即時影像輸入', '由螢幕或即時影像來源取得畫面，再進行重繪或控制；更新頻率與採樣成本會影響延遲。'),
    'regional': ('區域條件配置', '用區域、色塊或遮罩分配不同文字條件，減少多個主體的描述互相干擾。'),
    'noise': ('噪聲與採樣調整', '使用專用噪聲修正或細節採樣節點改變去噪過程；先與相同種子的基本流程比較效果。'),
    'pixel': ('像素化與輪廓整理', '將輸入圖轉成較粗的像素網格並整理色塊或輪廓；像素化不等於重新生成細節。'),
    'geometry': ('網格與材質組合', '建立或修改網格形狀、圖層與 UV 材質，再送入模型或角色輸出；需另行確認相容的三維節點環境。'),
    'dimensions': ('影像尺寸讀取', '讀取輸入圖片的寬高並轉為數值或文字，供其他節點設定尺寸，不會自行生成新圖。'),
    'code': ('自訂程式節點', '以程式碼串接特殊邏輯並回傳文字；執行前需閱讀原始碼，節點名稱無法證明它會操作哪些服務或檔案。'),
}


def _methods(types, entry):
    text = ' '.join(types).lower()
    context = ' '.join(str(entry.get(k, ''))[:500] for k in ('id', 'category', 'title')).lower()
    def has(*parts): return any(p in text for p in parts)
    flags = {
        'diffusion': has('ksampler', 'samplercustom', 'hyvideosampler', 'brushnetpipeline'),
        'text': has('textencode', 'efficient loader'),
        'vae': has('vaeencode', 'vaedecode', 'vaeloader'),
        'hand': has('meshgraphormer', 'handrefiner'),
        'material': has('zest:'),
        'outpaint': has('imagepadforoutpaint', 'outpaint'),
        'inpaint': has('inpaint', 'brushnet', 'powerpaint', 'fooo') or has('vaeencodeforinpaint'),
        'segment': has('segmentanything', 'sam2', 'samloader', 'birefnet', 'segmdetector', 'rembg', 'rmbg', 'ultralytics', 'sam multilayer') or 'Segment' in types,
        'control': has('controlnet', 'control-lora'),
        'depth': has('depthanything', 'depthmap', 'midas'),
        'pose': has('dwpreprocessor', 'openpose', 'posepreprocessor'),
        'line': has('lineart', 'canny', 'softedge'),
        'ipadapter': has('ipadapter'),
        'redux': has('redux', 'stylemodelapply'),
        'identity': has('faceid', 'instantid', 'pulid', 'reactor', 'faceswap'),
        'detail': has('detailer', 'segspaste'),
        'tile': has('tile', 'ttp_image'),
        'upscale': has('upscale', 'superresolution', 'apisr'),
        'lora': has('lora'),
        'gguf': has('gguf'),
        'differential': has('differentialdiffusion'),
        'layer': has('layereddiffusion') or ('layered' in context and has('latentcuttobatch', 'referenceLatent'.lower())),
        'caption': has('florence', 'blip', 'wd14', 'llava', 'joycaption'),
        'llm': has('llm', 'ollama', 'gemini', 'openai', 'llava'),
        'remote': has('gemini', 'openai', 'api_zho', 'replicate', 'comfydeploy'),
        'video': has('svd_', 'animatediff', 'ade_', 'hyvideo', 'hunyuanvideo', 'wanvideo', 'minimaxh3', 'ltxv', 'easyanimate', 'hotshot', 'vhs_videocombine', 'createvideo'),
        'audio': has('loadaudio', 'audioencode', 'audiovae', 'vaedecodeaudio', 'audioldm', 'musetalk', 'multitalk'),
        'interpolate': has('rife', 'film', 'frameinterpol', 'framegeneration'),
        'edit': has('instructpixtopix', 'textencodeqwenimageedit', 'kontext'),
        'cascade': has('stablecascade'),
        'realtime': has('screenshare', 'floatingvideo', 'webcam'),
        'regional': has('regionalconditioning', 'regionalprompt', 'conditioningcombine'),
        'noise': has('npnet', 'detaildaemon', 'injectnoise'),
        'pixel': has('pixelize', 'pixelart'),
        'geometry': has('mesh_', 'object_createmesh', 'avatarmainoutput'),
        'dimensions': has('getimagewidthheight'),
        'code': has('codenode', 'pythonexecute'),
    }
    if flags['layer'] and 'qwen' in context and not has('createvideo', 'vhs_videocombine'):
        flags['video'] = False
    return flags


def _recipe_value(settings, path):
    value = settings
    for key in path.split('.'):
        if not isinstance(value, Mapping):
            return None
        # RTX node option names can themselves contain dots.
        value = value.get(key)
    return value


RECIPE_GUIDES = {
    'moss-character-pilot': {
        'summary': '先建立小規模 MOSS-Image 角色 LoRA 試訓，再驗證匯出權重能否在 ComfyUI 正確載入；試訓是流程驗證，不是成品品質保證。',
        'inputs': ['已獲使用權的角色訓練圖片與對應文字描述。', '分離的 KohakuMossTrainer 環境，以及同一變體的 MOSS 模型組。'],
        'steps': ['先執行離線 plan，確認固定來源與下載需求。', '產生設定檔並以 preflight 檢查資料集、模型和訓練器版本。', '在獨立訓練環境執行小規模試訓，檢查 loss、輸出檔與載入鍵。', '將 comfy 格式適配器載入相容的 MOSS 模型，與不載入 LoRA 的相同測試條件比較。'],
        'techniques': [('LoRA 試訓', '用低秩適配器測試角色資料與訓練管線；先確認權重確實作用，再擴大訓練。')],
        'tips': ['base 與 SFT 權重不可混搭；不要把試訓步數當成正式訓練建議。', '此工具不是 ComfyUI 訓練節點；模型下載與訓練需另行明確執行。'],
        'params': [('steps', 'steps', '試訓迭代數。'), ('rank', 'rank', 'LoRA 的低秩維度。'), ('learning_rate', 'learning_rate', '最佳化學習率。'), ('variant', 'variant', '選定的模型變體。'), ('export_format', 'export_format', '權重匯出格式。'), ('periodic_samples', 'periodic_samples', '是否在訓練途中產生樣本。')],
    },
    'local-llm-h3-director': {
        'summary': '把創作意圖整理為 H3 可閱讀的畫面、聲音與音樂描述，再由人確認後交給影片生成。',
        'inputs': ['文字創作需求；需要視覺理解時另提供參考圖。', '使用者選定的相容 API 與模型；影像請求還需要對應多模態支援。'],
        'steps': ['選擇本機或遠端相容模型，確認它是否支援提供的素材。', '分別整理整合式畫面敘述、整體聲景和非劇中音樂。', '校對角色、時間、鏡頭與聲音的因果關係，再將提示詞帶入選定 H3 工作流。', '以成功率、冷暖啟動延遲、重抽次數與可用片段成本比較設定。'],
        'techniques': [('結構化多模態提示', '將畫面、場景音與配樂分開規劃，降低互相矛盾的描述。')],
        'tips': ['本機 LLM 與影片共用 GPU 時，先安排階段順序；不自動卸載別人的模型。', '這是提示詞方法，並不會自動選模型、排隊或花費 API 點數。'],
        'params': [('review_before_queue', 'review_before_queue', '生成前需確認描述。'), ('automatic_unload', 'automatic_unload', '不強制卸載既有模型。'), ('reference_images_optional', 'reference_images_optional', '是否允許不提供參考圖。')],
    },
    'h3-two-pass-1080p': {
        'summary': '先用較低解析度完成部分 H3 去噪，再放大潛在表示續跑，最後以 RTX 放大與裁切製作 1080p 交付版本。',
        'inputs': ['已選定的相容 H3 模型、模式匹配的 turbo 適配器與原始生成條件。', 'H3 潛在放大與 RTX 超解析度節點；需要保留時另提供來源音訊。'],
        'steps': ['第一階段依已列設定建立較低解析度潛在畫布，從同一去噪時間表的前半段開始。', '在已列切分步數放大潛在表示，對齊目標尺寸與後半段噪聲時間表。', '接續剩餘步數去噪，再解碼影片影格。', '以已列倍率做 RTX 放大；使用者確認中間尺寸，再選擇交付裁切與音訊保留方式。'],
        'techniques': [('分段去噪與潛在放大', '在去噪途中增加空間解析度，讓後半段在較大潛在畫布上補充細節；不能把它當成兩次互不相干的完整採樣。')],
        'tips': ['來源影格數需符合所選模型的影格格點；不要自動把它改成另一個數字。', '來源倍率與目標尺寸可能包含模型格點取整；以已列目標值逐一核對。', '這是來源報告的配方，尚未在本機驗證；注意力核心與模型版本需要另外確認。'],
        'params': [('first_pass.width', 'first_pass.width', '第一階段寬度。'), ('first_pass.height', 'first_pass.height', '第一階段高度。'), ('first_pass.total_steps', 'first_pass.total_steps', '整個去噪序列的步數。'), ('first_pass.split_step', 'first_pass.split_step', '切換解析度的步數。'), ('first_pass.scheduler', 'first_pass.scheduler', '來源時間表。'), ('latent_upscale.scale', 'latent_upscale.scale', '來源放大倍率。'), ('latent_upscale.target_width', 'latent_upscale.target_width', '取整後的目標寬度。'), ('latent_upscale.target_height', 'latent_upscale.target_height', '取整後的目標高度。'), ('second_pass.remaining_steps', 'second_pass.remaining_steps', '續跑步數。'), ('second_pass.continue_sigma_sequence', 'second_pass.continue_sigma_sequence', '續用同一去噪時間表。'), ('frames', 'frames', '來源影格數，需核對模型限制。'), ('fps', 'fps', '來源影格率。'), ('rtx_scale', 'rtx_scale', '解碼後的影像放大倍率。'), ('preserve_source_audio', 'preserve_source_audio', '是否保留來源音訊。'), ('hdr', 'hdr', '此配方不啟用 HDR。')],
    },
    'rtx-video-super-resolution': {
        'summary': '對已解碼的影片影格做 RTX 超解析度放大，再以原影格率和音訊重新封裝。',
        'inputs': ['已解碼的影片影格、來源影格率與需保留的音訊。', '相容 NVIDIA RTX GPU 與已核對版本的 VFX 節點/runtime。'],
        'steps': ['解碼影片並記錄來源影格率與音訊。', '以設定的倍率與品質處理影格。', '用相同時間軸重新封裝，檢查細節、閃爍、音畫同步及輸出尺寸。'],
        'techniques': [('RTX 影格超解析度', '對既有影格增加可呈現的細節與尺寸，不會自動修正剪輯或生成錯誤。')],
        'tips': ['舊版固定節點僅支援基本縮放與品質；新欄位不能直接送進舊 schema。', '較新 VFX runtime 需要不同 CUDA/PyTorch 條件，應在隔離環境驗證。'],
        'params': [('resize_type.scale', 'resize_type.scale', '影像放大倍率。'), ('quality', 'quality', '來源品質選項。'), ('strength', 'strength', '增強強度。'), ('image_encoding', 'image_encoding', '輸入像素編碼。'), ('preserve_audio', 'preserve_audio', '保留來源音訊。'), ('preserve_fps', 'preserve_fps', '保留來源影格率。')],
    },
    'rtx-video-hdr': {
        'summary': '以 RTX TrueHDR 轉換亮度與色彩表現，並透過真正支援 HDR 的編碼器保留 PQ/BT.2020 交付資訊。',
        'inputs': ['要轉為 HDR 的影片影格。', '支援 RTXTrueHDR 的節點版本、runtime 和 10-bit HDR 編碼器。'],
        'steps': ['依來源設定調整對比、飽和度、中灰與亮度，執行 HDR 轉換。', '依已列位元深度、色彩原色與傳遞函數，使用支援 HDR 的編碼器封裝。', '檢查檔案色彩 metadata，並在相容播放器與顯示器上驗證。'],
        'techniques': [('HDR 轉換與色彩管理', 'HDR 像素必須搭配正確編碼與色彩標記；只提升亮度或使用 SDR 封裝不等於正確 HDR。')],
        'tips': ['舊版固定節點組沒有 RTXTrueHDR；先確認安裝版本。', '不要把 HDR 像素交給一般 SDR 輸出流程。'],
        'params': [('contrast', 'contrast', '來源對比設定。'), ('saturation', 'saturation', '來源飽和度設定。'), ('middle_gray', 'middle_gray', '來源中灰設定。'), ('luminance', 'luminance', '來源亮度設定。'), ('debanding', 'debanding', '是否進行色階平滑。'), ('output_colorspace', 'output_colorspace', '目標像素色彩空間。'), ('delivery.bit_depth', 'delivery.bit_depth', '輸出位元深度。'), ('delivery.primaries', 'delivery.primaries', '色彩原色標記。'), ('delivery.transfer', 'delivery.transfer', '傳遞函數標記。')],
    },
    'rtx-video-frame-generation': {
        'summary': '在既有影片影格之間插入中間畫面，提升交付流暢度，同時維持原本片長與音訊同步。',
        'inputs': ['來源影格、來源 FPS 與音訊。', '支援 RTXVideoFrameGeneration 的 GPU、節點和 runtime。'],
        'steps': ['按來源時間順序解碼影格，啟用來源記錄的切鏡偵測。', '以設定的倍率生成中間影格，檢查快動作、遮擋與切鏡。', '以對應倍數的交付 FPS 重新封裝，確認最後一格時長與音畫同步。'],
        'techniques': [('影格插補與時間軸保留', '增加影格數時同步提高 FPS 才能維持片長；不要誤把插補輸出當成慢動作。')],
        'tips': ['切鏡處不應混成跨鏡頭的過渡影格。', '來源指定 RTX 40 系列或更新 GPU；仍需確認實際 runtime 相容性。'],
        'params': [('generation_type.multiplier', 'generation_type.multiplier', '插補影格倍率。'), ('mode', 'mode', '來源品質/速度選項。'), ('automatic_shot_change_detection', 'automatic_shot_change_detection', '自動偵測切鏡。'), ('shot_change', 'shot_change', '來源手動切鏡旗標。'), ('delivery_fps_multiplier', 'delivery_fps_multiplier', '輸出 FPS 的倍率。'), ('preserve_audio', 'preserve_audio', '保留來源音訊。')],
    },
    'h3-course-basics': {
        'summary': '學習選擇首尾幀或多參考 H3 條件，並區分參考音色與精確保留原音的不同需求。',
        'inputs': ['使用者提供的首尾幀或身分、服裝、場景參考圖。', '有聲音需求時提供音色參考或需保留的原始音訊。'],
        'steps': ['先依素材選擇 FL2VA 或 Ref2VA，明確分配每張圖的角色。', '決定要參考音色重新合成，還是保留既有音軌，不把兩者混為一談。', '先使用模式相容的官方節點流程驗證，再逐一加入適配器或提示詞輔助。'],
        'techniques': [('條件角色分工', '首尾幀限定時間端點，多參考圖描述身分與外觀；音色參考不等於逐字逐拍複製原音。')],
        'tips': ['雲端範例不代表本機節點與模型已備齊。', '課程未指定這個索引項目的固定採樣步數；請以選定的實際圖為準。'],
        'params': [],
    },
    'h3-lora-catalog': {
        'summary': '依鏡頭、風格、動作、換角或修復目的挑選 H3 適配器，先確認模式相容性再逐個測試。',
        'inputs': ['已確定的 H3 模型與生成模式。', '從作者卡片取得且授權相容的單一適配器，以及固定的比較素材。'],
        'steps': ['依需求篩選運鏡、動畫風格、動作或人物處理的作者卡片。', '核對底模、FL2VA/Ref2VA 模式、檔案格式與授權。', '先載入一個適配器，確認有模型鍵匹配，並以相同種子做開關 A/B 比較。', '確認單個有效後再測組合，記錄互相干擾與失效情況。'],
        'techniques': [('模式相容的 LoRA 驗證', '檔案能下載或能被選到，不代表它實際作用於底模；需同時檢查載入鍵與輸出差異。')],
        'tips': ['未轉換格式可能匹配零個權重鍵；不要僅以沒有報錯判定成功。', '作者要求的受限下載與模型授權仍適用；此索引不附權重。'],
        'params': [],
    },
}

RECIPE_ENUMS = {
    'variant': {'base', 'sft'}, 'export_format': {'comfy'}, 'first_pass.scheduler': SCHEDULERS,
    'quality': {'ULTRA', 'HIGH', 'MEDIUM', 'LOW'}, 'mode': {'MEDIUM', 'HIGH', 'LOW'},
    'image_encoding': {'8-bit RGB'}, 'output_colorspace': {'HDR PQ'},
    'delivery.primaries': {'bt2020'}, 'delivery.transfer': {'smpte2084'},
}
RECIPE_BOOLEANS = frozenset({'periodic_samples', 'review_before_queue', 'automatic_unload',
    'reference_images_optional', 'second_pass.continue_sigma_sequence', 'preserve_source_audio',
    'hdr', 'preserve_audio', 'preserve_fps', 'debanding', 'automatic_shot_change_detection', 'shot_change'})


def _recipe(entry):
    recipe_id = entry.get('id', '')
    if isinstance(recipe_id, str) and recipe_id.startswith('recipe-'):
        recipe_id = recipe_id[7:]
    source = RECIPE_GUIDES.get(recipe_id) if isinstance(recipe_id, str) else None
    if source is None:
        return {'summary': '這是來源方法與設定索引；請先選擇相容的實際工作流，再逐項驗證。',
                'inputs': ['依來源方法準備自己的素材與模型。'],
                'steps': ['閱讀來源與需求。', '將設定逐項對照相容工作流。', '以小規模測試確認輸出。'],
                'techniques': [], 'tips': ['配方不等於可直接執行的 ComfyUI 圖。'],
                'parameters': [], 'evidence': '方法索引；未解析可執行圖，也未進行 GPU 驗證。'}
    settings = entry.get('settings', {})
    settings = settings if isinstance(settings, Mapping) else {}
    parameters = []
    for path, name, note in source['params']:
        value = settings.get(path) if path in settings else _recipe_value(settings, path)
        if path in RECIPE_BOOLEANS:
            valid = isinstance(value, bool)
        elif path in RECIPE_ENUMS:
            valid = isinstance(value, str) and value in RECIPE_ENUMS[path]
        else:
            valid = _number(value, 0, 1_000_000)
        if valid:
            parameters.append({'name': name, 'value': value, 'note': note + ' 取自已收錄配方；不是本機實測推薦值。'})
    return {key: list(source[key]) if isinstance(source[key], list) else source[key]
            for key in ('summary', 'inputs', 'steps', 'tips')} | {
        'techniques': [{'name': name, 'explanation': explanation} for name, explanation in source['techniques']],
        'parameters': parameters,
        'evidence': '依已收錄的來源配方整理；數值僅來自 settings 白名單欄位，未執行訓練或 GPU 生成。',
    }


def enrich_entry(entry, graph=None):
    """Return a fresh guide dictionary; never mutate entry or execute the graph."""
    entry = entry if isinstance(entry, Mapping) else {}
    if entry.get('kind') == 'recipe' or entry.get('status') == 'recipe_only':
        return _recipe(entry)
    nodes = _nodes(graph) if isinstance(graph, Mapping) else []
    types = sorted({kind for kind, _, _ in nodes}) if nodes else sorted({
        value for value in entry.get('node_types', []) if isinstance(value, str)
    }) if isinstance(entry.get('node_types', []), list) else []
    flags = _methods(types, entry)
    text = ' '.join(types).lower()
    has_image = any(s in text for s in ('loadimage', 'load image', 'loadimageoutput', 'screenshare'))
    has_video_input = any(s in text for s in ('loadvideo', 'load video'))
    has_audio_input = any(s in text for s in ('loadaudio', 'load audio', 'vhs_loadaudio'))
    sampling = any(s in text for s in ('ksampler', 'samplercustom', 'hyvideosampler', 'brushnetpipeline'))
    image_edit = flags['edit'] or ('textencodeqwenimage21' in text and has_image)
    reference = flags['ipadapter'] or flags['redux'] or flags['identity'] or 'referencetovideo' in text
    if flags['hand']:
        summary = '偵測或選定手部，利用深度結構引導局部重繪，改善手指形狀與銜接。'
    elif flags['material']:
        summary = '把參考材質帶入指定物件區域，保留構圖並調整局部外觀。'
    elif flags['outpaint']:
        summary = '擴大圖片畫布，生成原圖邊界外的延伸內容。'
    elif flags['layer']:
        summary = '處理前景、背景或透明圖層，方便分離主體與重新合成。'
    elif flags['video']:
        summary = ('以參考圖約束人物或場景，生成時間連續的影片。' if reference else
                   '將既有影片影格處理後重新封裝，改善尺寸或流暢度。' if has_video_input and not sampling else
                   '以輸入影像作為起始或條件，生成連續動態影格。' if has_image else
                   '從文字與模型條件建立動態影格，並輸出影片。')
    elif flags['inpaint']:
        summary = '以遮罩選定修改範圍，進行物件修補、移除或局部內容替換。'
    elif flags['realtime']:
        summary = '將即時畫面帶入影像生成或重繪，觀察輸入變化對結果的影響。'
    elif flags['geometry']:
        summary = '將圖像與形狀資料整理為網格、材質或角色圖層，供三維角色處理使用。'
    elif flags['pixel']:
        summary = '將輸入圖片像素化，整理色塊與輪廓，製作像素風格素材。'
    elif flags['segment'] and not sampling:
        summary = '分割主體或服裝區域，產生去背圖片、圖層或可供後續處理的遮罩。'
    elif flags['dimensions'] and not sampling:
        summary = '讀取圖片寬高並轉為數值或文字，作為其他工作流的尺寸資訊。'
    elif flags['code'] and not sampling:
        summary = '以自訂程式節點執行特定整合邏輯並顯示文字結果；實際作用需閱讀原圖中的程式碼。'
    elif flags['depth'] or flags['pose'] or flags['line']:
        summary = '以線稿、深度或姿態圖提供結構條件，引導生成並保留指定輪廓與構圖。'
    elif image_edit:
        summary = '以原圖與文字指令修改畫面內容、外觀或風格。'
    elif not sampling and flags['caption']:
        summary = '分析輸入影像並產生文字描述，供提示詞整理或後續多模態處理使用。'
    elif not sampling and flags['llm']:
        summary = '用語言模型整理或生成文字條件，供後續創作流程使用。'
    elif reference:
        summary = '以影像參考提供人物、構圖或風格條件，再生成符合文字描述的圖片。'
    elif flags['control'] or flags['pose'] or flags['line']:
        summary = '使用姿態、線稿或結構條件引導生成，讓結果更接近指定構圖。'
    elif flags['upscale'] and not sampling:
        summary = '放大或增強既有圖片的細節與尺寸。'
    elif has_image and sampling:
        summary = '以輸入圖片為條件進行重繪，透過文字與採樣強度調整結果。'
    elif sampling:
        summary = '將文字條件與初始潛在畫布送入模型採樣，生成圖片。'
    else:
        summary = '組合已列出的節點處理素材；具體輸入、分支與輸出需在原始工作流中確認。'
    inputs = []
    if has_image:
        inputs.append('自己的參考圖片；依節點用途選擇原圖、人物或風格參考。')
    if has_video_input:
        inputs.append('來源影片與需要保留的時間軸資訊。')
    if has_audio_input:
        inputs.append('自己的音訊檔；先確認它是音色參考還是需保留的音軌。')
    if flags['inpaint'] or flags['outpaint']:
        inputs.append('修改區域遮罩，可由圖片遮罩、分割或擴圖節點提供。')
    if sampling or any(s in text for s in ('textencode', 'llm', 'gemini', 'ollama')):
        inputs.append('文字描述與生成條件；不要把檔案內示例提示詞視為必需素材。')
    if sampling or any(s in text for s in ('loader', 'checkpoint')):
        inputs.append('相容的模型、編碼器與 VAE；實際檔名及自訂節點需求見此項目的需求清單。')
    if not inputs:
        inputs.append('依原始工作流的輸入節點準備素材；目前資料不足以確認必填欄位。')
    steps = ['在 ComfyUI 開啟原始圖，先確認缺少的節點與模型，並檢查啟用的分支。']
    if has_image or has_video_input or has_audio_input:
        steps.append('載入自己的素材，核對尺寸、裁切與素材在圖中的角色。')
    if flags['segment'] or flags['hand'] or flags['pose'] or flags['depth'] or flags['line']:
        steps.append('先預覽分割、姿態、深度或線稿的中間結果，確認它們對應到要處理的主體。')
    if flags['llm'] or flags['caption']:
        steps.append('若自動文字用於提示詞，先校對再送入生成條件。')
    if reference or flags['control'] or image_edit:
        steps.append('將參考、控制或原圖條件與文字條件結合；逐一核對權重及連線。')
    if flags['inpaint'] or flags['outpaint']:
        steps.append('預覽遮罩與擴充範圍，確認重繪區域及邊緣過渡沒有反向。')
    if sampling:
        steps.append('先以小規模設定採樣；需要解碼時用相容 VAE 將潛在表示轉回影像。')
    if flags['detail'] or flags['tile'] or flags['upscale'] or flags['interpolate']:
        steps.append('檢查後段細修、分塊、放大或插補結果，特別注意接縫與細節漂移。')
    steps.append('檢查影片影格率、最後影格時長與音畫同步後再封裝。' if flags['video'] else '比較原始素材與預覽結果，確認構圖及需要保留的細節後再儲存。')
    tips = ['先建立不加額外修改的基準，再一次調整一個設定；節點檢查通過不等於 GPU 已驗證。']
    if flags['lora']:
        tips.append('LoRA 必須匹配底模與模式；載入後檢查權重鍵和實際 A/B 差異。')
    if flags['remote']:
        tips.append('請自行設定服務端點與帳戶；此說明不包含金鑰，也不會觸發遠端請求。')
    if any('download' in t.lower() for t in types):
        tips.append('部分節點執行時可能自行下載權重；本指南與工作流索引不會下載模型。')
    if flags['video'] or flags['gguf']:
        tips.append('先核對模型版本、量化載入器與注意力核心；不要由檔名推定顯示卡相容性。')
    inspected = bool(nodes)
    if not inspected:
        summary = '依節點清單推測用途：' + summary
        tips.append('目前只有來源索引，無法確認節點連線、停用分支或採樣參數；請閱讀並檢查上游原圖。')
    evidence = ('已靜態讀取提供的圖及被引用的子圖；步驟是技術階段說明，不是逐條連線的執行追蹤。未做 GPU 生成驗證。'
                if inspected else '僅依節點清單作 inventory-only 推測；未讀取可執行圖，無法確認連線順序或採樣設定。')
    return {'summary': summary, 'inputs': inputs, 'steps': steps,
            'techniques': [{'name': METHODS[key][0], 'explanation': METHODS[key][1]} for key in METHODS if flags[key]],
            'tips': tips, 'evidence': evidence, 'parameters': _parameters(nodes),
            'output': '影片影格或封裝影片；實際輸出節點為準。' if flags['video'] else
                      '文字或多模態分析結果；實際輸出節點為準。' if not sampling and flags['llm'] else
                      '處理後的影像或中間素材；實際輸出節點為準。'}
