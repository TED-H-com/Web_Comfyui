"""Prepared, bilingual examples for the documentation UI, never model execution.

The files are fictional illustrations created with the built-in image tool.
Video files only animate those stills. Audio examples deliberately contain no
audio: there is no approved speech sample, voice clone, or synthesized waveform.
"""
from __future__ import annotations

from copy import deepcopy

NOTE = '模擬展示：預先準備的插圖／文案，不會依輸入即時生成，也不是所列模型的實測或品質保證。'
NOTE_EN = 'Simulation: prepared illustrations or copy, not generated from your input and not a benchmark or quality claim for the listed models.'
VIDEO_NOTE = '示範動畫，非模型實際輸出：只對插圖做鏡頭縮放與淡入淡出，沒有物件動作、口型同步或音軌。'
VIDEO_NOTE_EN = 'Prepared demonstration animation, not model output: only camera zoom and fades over a still illustration, with no object motion, lip sync, or soundtrack.'
AUDIO_NOTE = '此範例只有預先撰寫的逐字稿，不含聲音、聲紋或克隆結果；未呼叫任何語音服務。'
AUDIO_NOTE_EN = 'This example contains a prepared transcript only, with no audio, voiceprint, or cloned voice. No speech service is called.'

IMAGES = {
    'tea': {'src': './assets/examples/mountain-teahouse.webp',
            'alt': '預先製作的虛構高山茶屋插圖，湖面、晨霧與前景茶杯。',
            'alt_en': 'Prepared illustration of a fictional mountain tea house, lake, morning mist, and a foreground tea cup.'},
    'explorer': {'src': './assets/examples/explorer-portrait.webp',
                 'alt': '原創虛構成年探險家肖像，青綠外套與橘色圍巾，海岸背景。',
                 'alt_en': 'Original fictional adult explorer in a teal jacket and orange scarf, with coastal cliffs behind them.'},
    'robot': {'src': './assets/examples/ceramic-robot.webp',
              'alt': '預先製作的原創陶瓷桌面機器人插圖，青綠關節、暖黃眼睛與一片葉子。',
              'alt_en': 'Prepared illustration of an original ceramic desk robot with teal joints, amber eyes, and a leaf.'},
    'coast': {'src': './assets/examples/coastal-observatory.webp',
              'alt': '預先製作的虛構海岸天文台插圖，石造圓頂、觀景平台與暮色海面。',
              'alt_en': 'Prepared illustration of a fictional coastal observatory with a stone dome, viewing deck, and twilight sea.'},
}

# Prompts describe each prepared example. They are editable demonstration input,
# not a claim that these exact prompts produced results with a listed workflow.
CONTENT = {
    'text2img': ('高山茶屋的清晨', 'Morning at a mountain tea house',
        '高山湖畔的木造茶屋，清晨薄霧、暖金色陽光，前景一杯冒著熱氣的茶，電影感構圖。',
        'A timber tea house beside an alpine lake, morning mist, warm golden sunlight, a steaming tea cup in the foreground, cinematic composition.', 'image', 'tea'),
    'img2img': ('把場景改成海岸天文台', 'Imagine a coastal observatory',
        '把建築改成石造圓頂天文台，保留海岸與草地的自然氣氛，加入藍紫色暮光。',
        'Reimagine the building as a stone-domed observatory, keeping the natural coastal landscape and adding lavender twilight.', 'image', 'coast'),
    'anime-scene-card': ('角色與場景的素材卡', 'Character and scene references',
        '準備一位穿青綠外套的虛構探險家與海岸天文台場景，確認角色與場景素材後再製作場景卡。',
        'Prepare a fictional explorer in a teal jacket and a coastal observatory scene; review both references before building a scene card.', 'gallery', 'explorer'),
    'image-blend': ('陶瓷與青綠配色', 'Ceramic materials and teal accents',
        '融合奶油色陶瓷質感與青綠金屬細節，設計一台拿著葉子的原創桌面機器人。',
        'Combine cream ceramic materials and teal metal details into an original desk robot holding a leaf.', 'image', 'robot'),
    'character-angles': ('虛構探險家的肖像', 'Portrait of a fictional explorer',
        '原創成年探險家，青綠外套、橘色圍巾，三分之二側面肖像，柔和海岸夕陽。',
        'An original adult explorer in a teal jacket and orange scarf, three-quarter portrait in soft coastal sunset light.', 'image', 'explorer'),
    'clipmode-character-photoshoot': ('海岸探險家寫真', 'Coastal explorer portrait session',
        '讓虛構角色穿青綠外套站在海岸，保留橘色圍巾、短髮與親切神情，溫暖傍晚光線。',
        'A fictional character in a teal jacket by the coast, with an orange scarf, short hair, a friendly expression, and warm evening light.', 'image', 'explorer'),
    'img2video': ('天文台鏡頭示意', 'Observatory camera preview',
        '從海岸天文台圖片開始，鏡頭緩慢推近，保持安靜的暮色氣氛。',
        'Begin with the coastal observatory image and slowly push the camera in, keeping the quiet twilight mood.', 'video', 'coast'),
    'first-last-frame': ('首尾構圖示意', 'Opening and closing composition preview',
        '首格為海岸天文台全景，尾格為稍近的構圖；示範從全景推向細節的鏡頭安排。',
        'Start with a wide observatory view and finish slightly closer; preview a camera move from the overall scene toward its details.', 'video', 'coast'),
    'dance': ('動作影片的預覽位置', 'Motion-video preview placement',
        '以虛構探險家作為角色參考，規劃輕鬆的舞步；本站只展示預先製作的角色鏡頭動畫。',
        'Use the fictional explorer as a character reference for relaxed dance steps; this site shows only a prepared camera animation of the portrait.', 'video', 'explorer'),
    'lipsync': ('對嘴工具的影片預覽', 'Lip-sync tool video preview',
        '台詞：今天的旅程從海邊開始。此示範只介紹人物影片與語音的操作位置，不合成口型或音訊。',
        'Line: Today our journey begins by the sea. This example shows the placement of portrait-video and speech controls; it does not synthesize lip movement or audio.', 'video', 'explorer'),
    'face-swap': ('人物結果的示意肖像', 'Illustrative portrait result',
        '使用原創虛構角色理解換人流程的結果檢視位置；本範例沒有替換任何真人臉部。',
        'Use an original fictional character to explore where a face-swap result would appear; this example does not replace any real person’s face.', 'image', 'explorer'),
    'video-loop': ('短片循環播放示意', 'Short-clip playback preview',
        '選擇海岸天文台短片，觀察重複播放的預覽位置；實際循環次數由本機工具處理。',
        'Choose the coastal observatory clip to explore the playback preview; the installed tool handles actual repeat counts.', 'video', 'coast'),
    'voice-speak': ('一段旅程旁白', 'A short journey narration',
        '今天的旅程從海邊開始。沿著石階走上天文台，等第一顆星慢慢出現。',
        'Today our journey begins by the sea. Follow the stone path to the observatory and wait for the first star to appear.', 'audio', None),
    'voice-batch': ('三頁簡報旁白', 'Three-slide narration draft',
        '第一頁：介紹海岸天文台。第二頁：說明觀景路線。第三頁：邀請觀眾留下自己的旅行計畫。',
        'Slide one: Introduce the coastal observatory. Slide two: Explain the viewing route. Slide three: Invite viewers to share their travel plans.', 'audio', None),
    'voice-clone': ('參考聲音操作示意', 'Reference-voice workflow example',
        '請先確認參考音訊的使用權與逐字稿。示範台詞：帶著好奇心出發，讓風景慢慢展開。',
        'First confirm permission to use the reference audio and verify its transcript. Example line: Set out with curiosity and let the landscape unfold.', 'audio', None),
    'voxcpm-tts': ('溫暖清楚的旁白稿', 'A warm, clear narration script',
        '用溫暖、清楚、稍慢的節奏朗讀：歡迎來到山湖茶屋，今天讓我們留一點時間給自己。',
        'Read warmly, clearly, and a little slowly: Welcome to the mountain tea house. Today, let us leave a little time for ourselves.', 'audio', None),
    'voice-library': ('聲音庫的逐字稿範例', 'Voice-library transcript example',
        '參考逐字稿：沿著海岸的小路散步，遠處的天文台亮起一盞燈。',
        'Reference transcript: We walk along the coastal path as a light comes on in the distant observatory.', 'audio', None),
    'post-generator': ('山湖茶屋的貼文草稿', 'Mountain tea house social post',
        '為山湖茶屋寫一則溫暖、簡短的旅行貼文，描述晨霧、茶香與放慢腳步的感受。',
        'Write a warm, brief travel post about a mountain tea house: morning mist, the aroma of tea, and slowing down.', 'text', 'tea'),
    'h3-text-video': ('海岸天文台的分鏡示意', 'Coastal observatory shot preview',
        '畫面：暮色中的海岸天文台，鏡頭緩慢推近。聲景：規劃輕柔海浪。配樂：規劃低調鋼琴；本站範例沒有音軌。',
        'Visual: Slowly push toward a coastal observatory at dusk. Soundscape plan: gentle waves. Music plan: understated piano. This prepared example has no audio track.', 'video', 'coast'),
    'h3-image-video': ('從參考圖開始規劃鏡頭', 'Plan a shot from a reference image',
        '以海岸天文台為首格，鏡頭緩慢接近石造圓頂；保持構圖與安靜的暮色。',
        'Use the coastal observatory as the opening frame and slowly approach its stone dome while preserving the quiet twilight composition.', 'video', 'coast'),
    'h3-reference-video': ('多參考角色鏡頭示意', 'Multiple-reference character shot preview',
        'Picture 1 定義虛構探險家的外觀，Picture 2 定義海岸場景；規劃平穩的角色介紹鏡頭。',
        'Picture 1 defines the fictional explorer and Picture 2 defines the coastal setting; plan a steady character-introduction shot.', 'video', 'explorer'),
    'qwen21-text2img': ('原創陶瓷機器人', 'Original ceramic robot',
        '原創奶油色陶瓷桌面機器人，青綠關節、暖黃色眼睛，手拿一片葉子，柔和產品攝影，無文字或商標。',
        'An original cream ceramic desk robot with teal joints and warm amber eyes, holding a leaf, soft product photography, no text or logos.', 'image', 'robot'),
    'qwen21-edit': ('把建築改成天文台', 'Reimagine a building as an observatory',
        '將 <image1> 的建築改成石造圓頂天文台，保留海岸地形，加入溫暖窗光與藍紫色天空。',
        'Reimagine the building in <image1> as a stone-domed observatory, preserving the coastline and adding warm window lights and a lavender sky.', 'image', 'coast'),
}

# Translations follow the three source-guide stages. The Chinese stages below
# are copied from the actual guide catalog passed by the caller.
STEPS_EN = {
    'text2img': ['Enter a scene and aspect ratio; defaults are 1088×1920, 8 steps, CFG 1.', 'Load Z-Image Turbo, the Qwen 3 4B lumina2 text encoder, and AE.', 'Sample with res_multistep / simple and save; DOPSD adds the white-backdrop LoRA only when selected.'],
    'img2img': ['Upload an image and enter an editing instruction.', 'Scale with FluxKontextImageScale, encode with AE, and supply ReferenceLatent.', 'Apply FluxGuidance, sample, decode, and save.'],
    'anime-scene-card': ['Choose an approved character outfit and scene from the character page.', 'Read the character alpha mask and invert it for compositing.', 'Composite with ImageCompositeMasked and save.'],
    'image-blend': ['Upload at least two references and choose a description and aspect ratio.', 'Load the SDXL checkpoint, IPAdapter Plus, and CLIP Vision.', 'Add each reference through IPAdapterAdvanced, sample, and decode with the checkpoint VAE.'],
    'character-angles': ['Prepare a clear, near-frontal reference face with permission.', 'Analyze it with InsightFace and condition SDXL with InstantID and ControlNet.', 'Sample the requested scene or angle and decode with the checkpoint VAE.'],
    'clipmode-character-photoshoot': ['Choose a character, scene, aspect ratio, and seed on the character page.', 'The installed server binds identity references and models, using InstantID / ReActor or a configured character LoRA.', 'Generate locally, check identity and person consistency, and keep accepted results.'],
    'img2video': ['Upload an opening image and describe motion and aspect ratio.', 'Encode with CLIP Vision, create WanImageToVideo conditioning, and load LightX2V LoRA.', 'Defaults are 81 frames, 16 FPS, and 6 steps; decode and assemble MP4.'],
    'first-last-frame': ['The graph builder accepts an opening and closing frame.', 'Create video latents from both image conditions and LightX2V LoRA.', 'Sample and decode video frames.'],
    'dance': ['Provide a character reference and motion video for the exposed animate mode.', 'Track with SAM3 and construct SCAIL masks, references, and motion frames.', 'Sample with Wan SCAIL, LightX2V, and context windows; retain source motion-video audio.'],
    'lipsync': ['Provide a clearly visible face video and authorized speech audio.', 'Wav2Lip processes frames and audio in sequential or repetitive mode.', 'Assemble a 30 FPS MP4 with the driving audio.'],
    'face-swap': ['The graph builder takes a target video and a source-face image.', 'ReActor uses inswapper_128 with optional GFPGAN or CodeFormer restoration.', 'The configured graph exports animated WebP.'],
    'video-loop': ['Upload a clip and select repeats or ping-pong playback.', 'Read the original frame rate and frames.', 'Assemble repeated MP4 at the original speed without audio.'],
    'voice-speak': ['Select a configured character voice or an authorized library sample and transcript.', 'The installed voice configuration selects CastleBreezyVoiceTTS, BreezyVoice, or F5.', 'Generate segments locally, add pauses, merge, and optionally check transcription.'],
    'voice-batch': ['Prepare a long script or PPTX speaker notes.', 'Configure Custom English voice and batch helpers in the installed environment.', 'Use the administrator workflow to generate, check, and merge narration segments.'],
    'voice-clone': ['Choose authorized reference audio and verify its transcript.', 'Select the F5 language model or the configured BreezyVoice service.', 'Synthesize text segments in the installed environment and join the output.'],
    'voxcpm-tts': ['Configure the voice backend, LoRA profile, and reference audio.', 'Choose Voice Design, Reference, Ultimate, or local LoRA mode.', 'The installed worker synthesizes and performs configured speaker or ASR checks.'],
    'voice-library': ['Upload reference audio you have permission to use to the installed app.', 'Check its transcript and display name.', 'Select the sample in speech or lip-sync forms.'],
    'post-generator': ['Enter a topic and choose platform, tone, length, and backend.', 'The installed app uses configured Claude CLI or provider APIs.', 'Review and reuse the text yourself; the documentation site sends no provider requests.'],
    'h3-text-video': ['Write separate visual, soundscape, and music descriptions.', 'Choose canvas, supported frame count, and acceleration mode.', 'Stock defaults are 20 steps with res_multistep / simple; decode video and audio and save at 24 FPS.'],
    'h3-image-video': ['Write separate visual, soundscape, and music descriptions.', 'Choose canvas, supported frame count, and acceleration; provide an opening or closing frame.', 'Stock defaults are 20 steps with res_multistep / simple; decode video and audio and save at 24 FPS.'],
    'h3-reference-video': ['Write six prompt sections and match Picture, Video, and Audio tags to references.', 'Provide at least one reference, up to 9 images, 3 videos, and 3 audio clips.', 'Stock defaults are 20 steps with res_multistep / simple; decode video and audio and save at 24 FPS.'],
    'qwen21-text2img': ['Describe the image and quote any exact text.', 'ResolutionSelector sets the canvas and TextEncodeQwenImage21 encodes conditions.', 'Sample with euler / simple, default 25 steps and CFG 1, then decode to PNG.'],
    'qwen21-edit': ['Upload images in order and refer to <image1> through <image4>.', 'TextEncodeQwenImage21 builds image conditions and latents; QwenImage21Cache wraps the model.', 'Sample at the selected size, decode, and save; do not leave gaps between image slots.'],
}


def demos(app_catalog):
    """Return fresh examples for exactly the current app registry plus creation."""
    features = {row['id']: row for row in app_catalog['features']}
    guides = {row['id']: row for row in app_catalog['guides']}
    if set(features) != set(CONTENT) or set(guides) != set(CONTENT) or set(STEPS_EN) != set(CONTENT):
        raise ValueError('Demo content must match every app feature and source guide')
    result = {}
    for feature_id, spec in CONTENT.items():
        title, title_en, prompt, prompt_en, kind, asset = spec
        output = {'kind': kind, 'images': [deepcopy(IMAGES[asset])] if asset else []}
        note, note_en = NOTE, NOTE_EN
        if kind == 'video':
            file = 'explorer-pan.mp4' if asset == 'explorer' else 'coastal-pan.mp4'
            output['video'] = {'src': './assets/examples/' + file, 'poster': IMAGES[asset]['src']}
            note += ' ' + VIDEO_NOTE
            note_en += ' ' + VIDEO_NOTE_EN
        elif kind == 'audio':
            output.update(text=prompt, text_en=prompt_en)
            note += ' ' + AUDIO_NOTE
            note_en += ' ' + AUDIO_NOTE_EN
        if feature_id == 'anime-scene-card':
            output['images'].append(deepcopy(IMAGES['coast']))
            output.update(text='兩張獨立參考插圖，未執行角色場景合成。',
                          text_en='Two separate reference illustrations; no character-scene compositing has been performed.')
        elif feature_id == 'post-generator':
            output.update(text='晨霧還沒散，茶香已經醒了。\n在山湖邊留一個早晨，不趕路，只看光線慢慢移動。\n你想把下一杯茶，留給哪一片風景？',
                          text_en='The mist is still resting, but the tea is awake.\nKeep one morning by the mountain lake: no hurry, just light moving across the water.\nWhich view would you choose for your next cup?')
        result[feature_id] = {'id': feature_id, 'title': title, 'title_en': title_en,
            'prompt': prompt, 'prompt_en': prompt_en, 'steps': list(guides[feature_id]['steps']),
            'steps_en': list(STEPS_EN[feature_id]), 'output': output, 'note': note, 'note_en': note_en}
    result['character-create'] = {
        'id': 'character-create', 'title': '建立虛構角色「沐野」', 'title_en': 'Create the fictional character “Mori”',
        'prompt': '沐野是一位原創成年探險家，短髮、青綠外套與橘色圍巾，喜歡觀星與海岸旅行。',
        'prompt_en': 'Mori is an original fictional adult explorer with short hair, a teal jacket, and an orange scarf, who enjoys stargazing and coastal journeys.',
        'steps': ['在建立角色表單填入名字與外觀描述。', '選擇角色參考圖；本機版本會把角色與素材存入自己的資料庫。', '本站只顯示預先準備的角色卡，不建立帳戶或保存上傳素材。'],
        'steps_en': ['Enter a name and appearance in the character form.', 'Choose a character reference; the installed app stores characters and assets in your own database.', 'This site shows a prepared character card without creating accounts or retaining uploaded files.'],
        'output': {'kind': 'character', 'images': [deepcopy(IMAGES['explorer'])],
                   'text': '沐野 · 虛構成年探險家\n青綠外套、橘色圍巾、海岸與星空。',
                   'text_en': 'Mori · Fictional adult explorer\nTeal jacket, orange scarf, coastlines, and starlight.'},
        'note': NOTE, 'note_en': NOTE_EN,
    }
    return result
