"""Camera-conditioned Qwen Edit 2511; independent views from one original."""
MODEL = 'qwen_image_edit_2511_fp8mixed.safetensors'
CLIP = 'qwen_2.5_vl_7b_fp8_scaled.safetensors'
VAE = 'qwen_image_vae.safetensors'
LORA = 'qwen-image-edit-2511-multiple-angles-lora.safetensors'
VIEWS = ('left side view', 'front view', 'right side view',
         'front-left quarter view', 'front-right quarter view', 'front view elevated shot')


def compile_angles(settings, uploads, *, seed, prefix, camera_lora=True):
    image = uploads.get('face')
    if not image:
        raise ValueError('請先提供角色原圖')
    count = int(settings.get('batchSize', 3))
    if not 1 <= count <= 6:
        raise ValueError('多角度張數必須介於 1 與 6')
    steps = int(settings.get('steps', 25))
    if not 1 <= steps <= 50:
        raise ValueError('多角度步數必須介於 1 與 50')
    graph = {
        'model': {'class_type': 'UNETLoader', 'inputs': {'unet_name': MODEL, 'weight_dtype': 'default'}},
        'clip': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': CLIP, 'type': 'qwen_image', 'device': 'default'}},
        'vae': {'class_type': 'VAELoader', 'inputs': {'vae_name': VAE}},
        'original': {'class_type': 'LoadImage', 'inputs': {'image': image}},
        'scale': {'class_type': 'ImageScaleToTotalPixels', 'inputs': {'image': ['original', 0], 'upscale_method': 'lanczos', 'megapixels': 1.0, 'resolution_steps': 32}},
        'latent': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['scale', 0], 'vae': ['vae', 0]}},
        'shift': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['camera' if camera_lora else 'model', 0], 'shift': 3.1}},
        'norm': {'class_type': 'CFGNorm', 'inputs': {'model': ['shift', 0], 'strength': 1.0}},
        'negative': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['clip', 0], 'vae': ['vae', 0], 'image1': ['scale', 0], 'prompt': str(settings.get('negative') or '')}},
    }
    if camera_lora:
        graph['camera'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['model', 0], 'lora_name': LORA, 'strength_model': 1.0}}
    for i, view in enumerate(VIEWS[:count]):
        prompt = f'<sks> {view} eye-level shot medium shot' if i < 5 else '<sks> front view elevated shot medium shot'
        if camera_lora and i in (0, 2):
            direction = "right" if i == 0 else "left"
            prompt += (f". Show the same character's full head in side profile, the nose pointing to the {direction} edge of the image. "
                       "Turn the head with the body, only one eye visible, no eye contact with the camera. "
                       "Keep the entire head and both shoulders inside the frame, the original identity, hair, outfit and colors. Do not mirror the original.")
        # Camera trigger followed by an explicit head instruction prevents a body-only orbit.
        if not camera_lora:
            prompt = 'Show the same character from a new camera angle: ' + view + '. Keep the same face, hair, clothing and full color.'
        graph[f'text{i}'] = {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['clip', 0], 'vae': ['vae', 0], 'image1': ['scale', 0], 'prompt': prompt}}
        graph[f'sample{i}'] = {'class_type': 'KSampler', 'inputs': {'model': ['norm', 0], 'positive': [f'text{i}', 0], 'negative': ['negative', 0], 'latent_image': ['latent', 0], 'seed': (seed+i) % (2**53), 'steps': steps, 'cfg': float(settings.get('cfg', 4)), 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}}
        graph[f'decode{i}'] = {'class_type': 'VAEDecode', 'inputs': {'samples': [f'sample{i}', 0], 'vae': ['vae', 0]}}
        graph[f'save{i}'] = {'class_type': 'SaveImage', 'inputs': {'images': [f'decode{i}', 0], 'filename_prefix': f'{prefix}/{i}-{view.replace(" ", "-" )}'}}
    return graph
