"""Offline, conservative demo planning profiles; not hardware detection or benchmarks.

Only accepts the already source-derived app catalog. No server imports, network,
GPU probes, environment access, model loading or runtime files. VRAM allowances below
are editorial estimates for the named default builders with sequential CPU
model offload and sufficient system RAM. Smaller quantized/tiled variants may
work below them; large inputs or different nodes can exceed them.
"""
from copy import deepcopy

GPU_SOURCES = {
    '4060': 'https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4060-4060ti/',
    '5060': 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5060-family/',
    '5070': 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/',
    '5080': 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5080/',
    '4090': 'https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/',
    '5090': 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/',
}
PROFILES = [
    {'id': 'none', 'label': '無 NVIDIA GPU', 'label_en': 'No NVIDIA GPU', 'cards_gb': [], 'examples': [], 'source_urls': []},
    {'id': 'vram-8', 'label': '8 GB · RTX 4060 / 5060', 'label_en': '8 GB · RTX 4060 / 5060', 'cards_gb': [8], 'examples': ['RTX 4060', 'RTX 5060', 'RTX 5060 Ti 8 GB'], 'source_urls': [GPU_SOURCES['4060'], GPU_SOURCES['5060']]},
    {'id': 'vram-12', 'label': '12 GB · RTX 5070', 'label_en': '12 GB · RTX 5070', 'cards_gb': [12], 'examples': ['RTX 5070'], 'source_urls': [GPU_SOURCES['5070']]},
    {'id': 'vram-16', 'label': '16 GB · RTX 5060 Ti / 5080', 'label_en': '16 GB · RTX 5060 Ti / 5080', 'cards_gb': [16], 'examples': ['RTX 5060 Ti 16 GB', 'RTX 5080'], 'source_urls': [GPU_SOURCES['5060'], GPU_SOURCES['5080']]},
    {'id': 'vram-24', 'label': '24 GB · RTX 4090', 'label_en': '24 GB · RTX 4090', 'cards_gb': [24], 'examples': ['RTX 4090'], 'source_urls': [GPU_SOURCES['4090']]},
    {'id': 'vram-32', 'label': '32 GB · RTX 5090', 'label_en': '32 GB · RTX 5090', 'cards_gb': [32], 'examples': ['RTX 5090'], 'source_urls': [GPU_SOURCES['5090']]},
    {'id': 'dual-24', 'label': '2 × 24 GB · 不合併顯存', 'label_en': '2 × 24 GB · no pooling', 'cards_gb': [24, 24], 'examples': ['2 × RTX 4090'], 'source_urls': [GPU_SOURCES['4090']]},
    {'id': 'autoip', 'label': 'AutoIP · 雲端示範', 'label_en': 'AutoIP · cloud demo', 'cards_gb': [], 'examples': [], 'source_urls': [], 'cloud_simulation': True},
]
# (planning VRAM GB, rationale in Chinese, rationale in English)
COMMUNITY_MEASUREMENTS = {
    'text2img': [{
        'source': 'User review supplied 2026-10-24', 'validation': 'user-reported-not-independently-reproduced',
        'device': 'RTX 5070 Laptop 8 GB', 'model': 'FLUX 3 Klein', 'quantization': 'int8',
        'width': 832, 'height': 1216, 'steps': 16, 'seconds_per_image_approx': 7,
        'reported_peak_gb': [7, 7], 'memory_metric': 'whole-machine peak as reported; measurement method unspecified',
        'same_as_default_builder': False,
        'summary': '使用者實測回報：RTX 5070 Laptop 8 GB，FLUX 3 Klein int8，832×1216、16 步，約 7 秒／張，整機峰值約 7 GB。測量方法未提供，本機未重現；這是另一個模型，並非此頁預設的 Z-Image。',
        'summary_en': 'User-reported measurement: RTX 5070 Laptop 8 GB, FLUX 3 Klein int8, 832×1216, 16 steps, about 7 s/image and about 7 GB whole-machine peak. Measurement method unspecified; not independently reproduced. This is an alternative model, not this form’s default Z-Image.',
    }],
    'image-blend': [{
        'source': 'User review supplied 2026-10-24', 'validation': 'user-reported-not-independently-reproduced',
        'device': 'RTX 5070 Laptop 8 GB', 'model': 'SAM3.1', 'quantization': 'not reported',
        'seconds_per_image_approx': 2, 'warm_run': True, 'reported_peak_gb': [2.3, 4.2],
        'memory_metric': 'peak as reported; measurement method unspecified',
        'summary': '使用者實測回報：RTX 5070 Laptop 8 GB，SAM3.1 熱跑約 2 秒／張，峰值 2.3–4.2 GB。量化、輸入尺寸與測量方法未提供，本機未重現；不是冷啟動時間。',
        'summary_en': 'User-reported measurement: RTX 5070 Laptop 8 GB, SAM3.1 warm runs about 2 s/image, peak 2.3–4.2 GB. Quantization, input dimensions and measurement method unspecified; not independently reproduced. Not a cold-start time.',
    }],
}

ESTIMATES = {
    'text2img': (24, 'Z-Image BF16 與 Qwen 4B 編碼器；預設大圖需留採樣／解碼空間。', 'Z-Image BF16 plus the Qwen 4B encoder; the large default image needs sampling and decoding headroom.'),
    'img2img': (24, 'Flux Kontext FP8、T5 編碼器及原圖 latent；預留編輯採樣空間。', 'Flux Kontext FP8, T5 and an input-image latent; allow headroom for editing and sampling.'),
    'anime-scene-card': (0, '已核准圖片的 alpha 合成，沒有擴散 checkpoint；仍需 ComfyUI 與素材。', 'Alpha compositing of approved images has no diffusion checkpoint; ComfyUI and assets are still required.'),
    'image-blend': (8, 'SAM3 主體分割與原圖合成的規劃值；大圖仍需額外記憶體，沒有擴散採樣。', 'Planning allowance for SAM3 segmentation and original-pixel compositing; large images need additional memory. No diffusion sampling.'),
    'character-angles': (16, 'SDXL、InstantID、ControlNet 與臉部分析模型。', 'SDXL, InstantID, ControlNet and face-analysis models.'),
    'clipmode-character-photoshoot': (24, 'SDXL 身份條件及可選換臉／修復；角色路線與私人模型另行設定。', 'SDXL identity conditioning with optional face swap/restoration; character routes and models need separate setup.'),
    'img2video': (24, 'Wan 14B FP8、文字／視覺編碼器與逐幀 latent；長片可能超過估計。', 'Wan 14B FP8, text/vision encoders and frame latents; longer clips can exceed this estimate.'),
    'first-last-frame': (24, 'Wan 14B 首尾幀規劃；原生生成入口停用，示範不會解除此限制。', 'Wan 14B first/last-frame planning; the native generation endpoint is disabled and this demo does not enable it.'),
    'dance': (32, 'Wan 14B、SCAIL、SAM3 追蹤及影片批次；各階段需釋放或卸載模型。', 'Wan 14B, SCAIL, SAM3 tracking and video batches; stages need model release or offload.'),
    'lipsync': (8, 'Wav2Lip 外掛的保守規劃值；節點版本、批次與修復選項另驗證。', 'A conservative planning allowance for Wav2Lip; verify node version, batch size and restoration options.'),
    'face-swap': (8, 'ReActor／ONNX 臉部模型規劃；原生生成入口仍停用。', 'Planning for ReActor/ONNX face models; the native generation endpoint remains disabled.'),
    'video-loop': (0, 'FFmpeg／影片節點處理，不使用擴散模型；高解析影片仍需要系統記憶體。', 'FFmpeg/video-node processing without a diffusion model; high-resolution video still needs system RAM.'),
    'voice-speak': (8, '本機 BreezyVoice／F5 等服務的規劃值；模型不在 registry 中固定，須另驗證。', 'Planning allowance for local BreezyVoice/F5 services; models are not fixed in the registry and need separate validation.'),
    'voice-batch': (8, '逐段使用語音後端；多工可能提高需求，不代表已配置服務。', 'Runs speech segments through a backend; concurrency can increase requirements and service setup is separate.'),
    'voice-clone': (8, 'F5／BreezyVoice 參考音克隆；實際後端與音訊長度需另驗證。', 'F5/BreezyVoice reference cloning; validate the actual backend and audio duration separately.'),
    'voxcpm-tts': (8, '獨立 VoxCPM 服務規劃值；不是 ComfyUI checkpoint，也不保證已安裝。', 'Planning allowance for a separate VoxCPM service; not a ComfyUI checkpoint or an installation guarantee.'),
    'voice-library': (0, '管理音訊素材，不執行語音合成。', 'Manages audio assets without speech synthesis.'),
    'post-generator': (0, '配置的文字 CLI／API 服務；本機大型語言模型的額外需求未包含。', 'Uses a configured text CLI/API; additional requirements for a self-hosted language model are not included.'),
    'h3-text-video': (32, 'H3 INT8 主模型約 20.97 GB、NVFP4 編碼器約 15.69 GB，另有 VAE；32 GB 仍須分階段卸載。', 'The H3 INT8 diffusion file is about 20.97 GB and its NVFP4 encoder about 15.69 GB, plus VAEs; even 32 GB needs staged offload.'),
    'h3-image-video': (32, 'H3 與首尾格條件；主模型／編碼器須分階段卸載，解析度與長度另驗證。', 'H3 with first/last-frame conditioning; stage diffusion/encoder residency and validate resolution and duration.'),
    'h3-reference-video': (32, 'H3 多參考與音訊／影片條件；參考數量會增加記憶體，不是 32 GB 可任意運行的保證。', 'H3 multi-reference audio/video conditioning; more references increase memory, so 32 GB is not an unrestricted-run guarantee.'),
    'qwen21-text2img': (24, 'Qwen 2.1 INT8 主模型約 7.26 GB、編碼器約 9.35 GB，加上 VAE／採樣空間。', 'Qwen 2.1 INT8 diffusion is about 7.26 GB and its encoder about 9.35 GB, plus VAE and sampling headroom.'),
    'qwen21-edit': (24, 'Qwen 2.1 INT8 編輯及參考圖片；多圖或大圖可能超過規劃值。', 'Qwen 2.1 INT8 editing with reference images; multiple or large inputs can exceed this allowance.'),
}


def catalog(app_catalog):
    features = app_catalog['features']
    ids = [feature['id'] for feature in features]
    if len(ids) != len(set(ids)) or set(ids) != set(ESTIMATES):
        raise ValueError('Hardware estimates must match every actual feature exactly')
    guides = {guide['id']: guide for guide in app_catalog['guides']}
    requirements = {}
    for feature in features:
        fid = feature['id']; gb, reason, reason_en = ESTIMATES[fid]
        guide = guides[fid]
        requirements[fid] = {
            'estimated_vram_gb': gb, 'basis': reason, 'basis_en': reason_en,
            'validation': 'editorial-estimate-not-benchmark',
            'measurements': deepcopy(COMMUNITY_MEASUREMENTS.get(fid, [])),
            'model_names': [model['name'] for model in guide.get('models', [])],
            'source_files': list(guide.get('source_files', [])),
            'native_api_disabled': feature.get('apiEnabled') is False,
            'native_ui_hidden': feature.get('uiHidden') is True,
            'provider_coverage': 'not-established-by-this-demo',
        }
    requirements['character-create'] = {
        'estimated_vram_gb': 0, 'basis': '建立角色資料與照片預覽，不訓練模型。',
        'basis_en': 'Creates a character record and photo preview; no model training.',
        'validation': 'source-reviewed-no-model-operation', 'model_names': [],
        'source_files': ['webui/static/app.js'], 'provider_coverage': 'not-established-by-this-demo',
    }
    profiles = deepcopy(PROFILES)
    for profile in profiles:
        profile['effective_vram_gb'] = max(profile['cards_gb'], default=0)
    return {
        'schema_version': 1, 'scope': 'static-demo-planning-only', 'default_profile': 'vram-32',
        'profiles': profiles, 'features': requirements,
        'assumptions': [
            'Estimates concern the default source builders, not measured minimum VRAM or speed.',
            'Assumes one job at a time, sequential model offload, sufficient system RAM and compatible nodes.',
            'Model file size is not peak GPU memory; resolution, frames, references and precision change requirements.',
            'Smaller quantized/tiled variants may run below the planning allowance; large inputs can exceed it.',
            'Two 24 GB cards are treated as one 24 GB device: these builders do not implement model sharding.',
            'AutoIP enables every prepared demo only. Real provider model coverage, configuration and credit pricing must be checked separately.',
        ],
        'model_size_sources': [
            'manifests/minimax_h3/models.lock.json', 'manifests/qwen_image21/models.lock.json',
            'https://huggingface.co/Comfy-Org/MiniMax-H3/blob/014cd40f7e177756c6b2473c0d93b1c89a790dd2/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors',
            'https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/ace0edeb3791a594ddfa36ed5f41a178a394e921',
        ],
    }


def can_use(data, feature_id, profile_id):
    """Demo availability only; never grants native/provider execution permission."""
    profile = next((p for p in data['profiles'] if p['id'] == profile_id), None)
    feature = data['features'].get(feature_id)
    if not profile or not feature:
        return False
    return bool(profile.get('cloud_simulation') or max(profile['cards_gb'], default=0) >= feature['estimated_vram_gb'])
