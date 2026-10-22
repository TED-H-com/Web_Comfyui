"""Fixed interface examples, kept separate from verified workflow recordings.

Media provenance identifies the engine that actually produced each asset. A
prepared example never establishes that the tool named by its form was run.
"""
from copy import deepcopy

from scripts.voice_demo_catalog import VOICE_TRACKS


def image(name, zh, en, slot=None):
    row = {'src': './assets/examples/' + name, 'kind': 'image',
           'alt': zh, 'alt_en': en, 'label': zh, 'label_en': en}
    if slot:
        row['slot'] = slot
    return row


def video(name, poster, zh, en, slot=None):
    row = image(name, zh, en, slot)
    row.update(kind='video', poster='./assets/examples/' + poster)
    return row


def voice(slug, slot=None):
    row = deepcopy(VOICE_TRACKS[slug])
    row.update(kind='audio', alt=row['title'], alt_en=row['title_en'],
               label=row['title'], label_en=row['title_en'])
    if slot:
        row['slot'] = slot
    return row


def example(zh, en, prompt, prompt_en, output, *, inputs=(), prefill=None,
            source_zh='', source_en=''):
    return {'title': zh, 'title_en': en, 'prompt': prompt, 'prompt_en': prompt_en,
            'inputs': list(inputs), 'prefill': prefill or {}, 'prefill_en': prefill or {},
            'evidence': {'kind': 'prepared-example'}, 'output': output,
            'note': source_zh + ' 這是固定的預製介面範例，不代表此工作流已實測；修改輸入不會重新生成。',
            'note_en': source_en + ' This is a prepared interface example, not generated from your input and not a verified run of this workflow.'}


PERSON = image('cheer-reference.webp', '晴晴：原創成年角色參考', 'Qingqing: original adult character reference')
NIGHT = image('stadium-ground-v2-reference.webp', '夜間球場背景', 'Evening stadium background')
WAVE = video('cheer-wave.mp4', 'cheer-wave-poster.webp', '晴晴揮手影片', 'Qingqing waving clip')
STYLE_VOICES = ['warm-welcome', 'game-cheer', 'gentle-story', 'mature-announcement', 'english-welcome']
VOICE_SOURCE_ZH = '音檔以 VoxCPM2 在 CPU 實際合成，使用原創成年女聲描述；沒有真人參考錄音。'
VOICE_SOURCE_EN = 'Audio was actually synthesized by VoxCPM2 on CPU from original adult female voice descriptions, without real-person reference recordings.'
welcome = VOICE_TRACKS['warm-welcome']['transcript']
slide_text = '\n\n'.join(VOICE_TRACKS[slug]['transcript'] for slug in ['slide-one', 'slide-two', 'slide-three'])

PREPARED_DEMOS = {
    'voice-speak': example('女聲打字即說：迎賓、元氣與故事', 'Female speech: greeting, energy and storytelling',
        welcome, welcome, {'kind': 'audio', 'audio_tracks': [voice(slug) for slug in STYLE_VOICES[:3]]},
        prefill={'text': welcome, 'sampleText': welcome, 'voiceSource': 'library', 'cloneEngine': 'taiwan'},
        source_zh=VOICE_SOURCE_ZH + ' 此處示範語音操作，不把 VoxCPM2 音檔當作 BreezyVoice 或 F5 成果。',
        source_en=VOICE_SOURCE_EN + ' These illustrate speech controls, not BreezyVoice or F5 quality.'),
    'voice-batch': example('三頁簡報：逐頁播放女聲旁白', 'Three-slide presentation: play each narration',
        slide_text, slide_text, {'kind': 'audio', 'audio_tracks': [voice(slug) for slug in ['slide-one', 'slide-two', 'slide-three']]},
        prefill={'text': slide_text}, source_zh=VOICE_SOURCE_ZH + ' 三段分別生成，沒有上傳或解析你的 PPTX。',
        source_en=VOICE_SOURCE_EN + ' Three separate clips; your PPTX is not uploaded or parsed.'),
    'voice-clone': example('合成聲音延續：讓同一份參考說新台詞', 'Synthetic voice continuation: a new line from a reference',
        VOICE_TRACKS['synthetic-clone']['transcript'], VOICE_TRACKS['synthetic-clone']['transcript'],
        {'kind': 'audio', 'audio_tracks': [voice('synthetic-clone')]}, inputs=[voice('warm-welcome', 'voice')],
        prefill={'speech': VOICE_TRACKS['synthetic-clone']['transcript'], 'sampleText': welcome, 'engine': 'taiwan'},
        source_zh='實際使用 VoxCPM2 與本專案生成的迎賓聲音作為參考，另合成新台詞；這不是 F5／BreezyVoice 的測試，也沒有複製真人聲音。',
        source_en='VoxCPM2 actually used this project’s synthetic welcome clip as a reference for a new line. This is not an F5/BreezyVoice test or a real-person voice clone.'),
    'voxcpm-tts': example('設計女聲：五種風格試聽', 'Design a female voice: five style auditions',
        welcome, welcome, {'kind': 'audio', 'audio_tracks': [voice(slug) for slug in STYLE_VOICES]},
        prefill={'voiceEngine': 'voxcpm', 'mode': 'design', 'language': 'zh',
                 'style': VOICE_TRACKS['warm-welcome']['style_en'], 'text': welcome, 'cfg': 2, 'steps': 10, 'seed': 241},
        source_zh=VOICE_SOURCE_ZH + ' 口音與自然度可直接試聽，沒有載入私人 LoRA。',
        source_en=VOICE_SOURCE_EN + ' Audition the accent and delivery yourself; no private LoRA was used.'),
    'voice-library': example('女聲試聽庫：選聲音再開始創作', 'Female voice library: audition, select and create',
        '選擇一種聲音，試聽後帶入語音合成或聲音參考示範。',
        'Audition a voice and carry it into the speech or reference-voice demonstration.',
        {'kind': 'audio', 'audio_tracks': [voice(slug) for slug in STYLE_VOICES]},
        inputs=[voice('warm-welcome', 'voice')], prefill={'name': '溫暖迎賓', 'source': 'VoxCPM2 原創合成聲音', 'transcript': welcome},
        source_zh=VOICE_SOURCE_ZH + ' 選擇只保留在本頁，自己的錄音僅在瀏覽器預覽。',
        source_en=VOICE_SOURCE_EN + ' Selection stays on this page; your own recordings are only previewed in the browser.'),
    'post-generator': example('晴晴的球場日常貼文', 'Qingqing’s stadium-day post',
        '幫原創角色晴晴寫一則輕鬆活潑的球場應援貼文，約一百字，加上三個 hashtag。',
        'Write a friendly stadium-day post for the fictional character Qingqing, with three hashtags.',
        {'kind': 'text', 'text': '今天把笑容準備好，也把滿滿的元氣帶到球場！\n\n換上最喜歡的隊服，和大家一起拍手、一起加油。最開心的不是比分，而是身邊每一個認真投入的瞬間。\n\n你最喜歡哪一種應援動作？留言告訴晴晴吧！\n\n#晴晴的球場日常 #原創虛構角色 #一起加油',
         'text_en': 'A smile, a favorite jersey, and a little extra energy for stadium day!\n\nThe best part is cheering together and sharing those moments with everyone around us. What is your favorite cheer? Tell Qingqing below!\n\n#StadiumDay #OriginalFictionalCharacter #CheerTogether'},
        prefill={'platform': 'instagram', 'tone': '活潑', 'length': 'short'},
        source_zh='這份貼文為預先撰寫的文案範例，展示語氣、段落與標籤。',
        source_en='This prewritten copy illustrates tone, paragraphs and hashtags.'),
    'anime-scene-card': example('應援角色的動漫素材卡', 'An anime cheer-character concept card',
        '將成年虛構應援角色整理成青綠白配色的動漫風格素材卡。',
        'Create a teal-and-white anime concept card for an original adult cheer character.',
        {'kind': 'image', 'images': [image('cheer-anime.webp', 'Z-Image 生成的成年動漫角色概念圖', 'Adult anime character concept generated with Z-Image')]},
        inputs=[{**PERSON, 'slot': 'image'}, {**NIGHT, 'slot': 'scene'}],
        source_zh='概念圖來自 Z-Image，示意素材卡的構圖與風格；不是本機 alpha 合成流程的輸出。',
        source_en='The concept image came from Z-Image and illustrates a card composition; it is not output from the local alpha-compositing workflow.'),
    'clipmode-character-photoshoot': example('角色寫真企劃：換裝與夜間場景', 'Character portrait planning: outfit and evening scene',
        '為晴晴規劃紅色隊服與夜間球場兩套視覺，保持角色參考清楚。',
        'Plan a red uniform and evening stadium look for Qingqing, with clear character references.',
        {'kind': 'gallery', 'images': [image('cheer-kontext.webp', '換裝企劃：Flux Kontext 紅色隊服案例', 'Outfit concept: a Flux Kontext red-uniform example'),
                                     image('cheer-subject-blend.webp', '場景企劃：SAM3 保留人物換背景案例', 'Scene concept: SAM3 subject-preserving background replacement')]},
        inputs=[PERSON], prefill={'prompt': 'Original adult cheer performer, red baseball jersey, navy shorts, full-body stadium portrait.'},
        source_zh='以既有 Flux Kontext 與 SAM3 成果示意兩套企劃，不宣稱 Clipmode／SDXL 曾執行這組設定。',
        source_en='Existing Flux Kontext and SAM3 results illustrate two concepts; these settings were not executed with Clipmode/SDXL.'),
    'video-loop': example('來回揮手：短片循環', 'A waving boomerang loop',
        '讓揮手短片正播、倒播，做成循環預覽。', 'Play the waving clip forward and backward as a loop.',
        {'kind': 'video', 'video': video('cheer-loop.mp4', 'cheer-loop-poster.webp', 'ComfyUI VHS 實際循環處理', 'Actual ComfyUI VHS loop processing')},
        inputs=[{**WAVE, 'slot': 'video'}], prefill={'repeat': 2, 'pingpong': True, 'maxFrames': 81},
        source_zh='這支影片實際經過 ComfyUI VHS 正反向與循環處理，沒有生成新影格或使用擴散模型。',
        source_en='The clip was actually processed with ComfyUI VHS for forward/reverse looping; no new frames or diffusion model were generated.'),
    'h3-text-video': example('應援短片的文字分鏡', 'A text storyboard for a cheer clip',
        '成年虛構應援角色站在球場，微笑並舉手打招呼，固定全身鏡頭。',
        'An original adult cheer performer smiles and waves at a stadium, in a fixed full-body shot.',
        {'kind': 'video', 'video': video('cheer-flf.mp4', 'cheer-flf-poster.webp', 'Wan 預製分鏡範例，非 H3 輸出', 'Prepared Wan storyboard, not H3 output')},
        prefill={'integrated_multimodal_description': 'An original adult cheer performer smiles and waves at a stadium. Static camera, full body, natural motion.',
                 'overall_soundscape': 'Quiet baseball stadium ambience', 'non_diegetic_music': 'None'},
        source_zh='播放以 Wan 首尾幀流程完成的短片，示範文字分鏡的呈現方式；沒有執行 H3。',
        source_en='A completed Wan first/last-frame clip illustrates the storyboard presentation; H3 was not run.'),
    'h3-image-video': example('照片變成揮手招呼的分鏡', 'Storyboard: a photo becomes a greeting',
        '讓照片裡的成年角色向前走兩小步，舉手微笑打招呼。',
        'The adult character takes two small steps forward and raises a hand to wave.',
        {'kind': 'video', 'video': WAVE}, inputs=[{**PERSON, 'slot': 'image'}],
        prefill={'integrated_multimodal_description': 'The adult character takes two small steps toward the camera and waves hello, keeping the same outfit.', 'non_diegetic_music': 'None'},
        source_zh='這支預製影片由 Wan I2V 完成，提供圖生影片的操作範例；不是 H3 的品質或效能展示。',
        source_en='This prepared clip was made with Wan I2V; it illustrates image-to-video controls, not H3 quality or performance.'),
    'h3-reference-video': example('人物、動作與聲音的參考組合', 'Combine character, motion and voice references',
        'Picture 1 是角色；Video 1 是原創動作參考；Audio 1 是原創合成女聲。製作友善的應援短片。',
        'Picture 1 defines the character, Video 1 provides original motion, and Audio 1 is an original synthetic female voice.',
        {'kind': 'video', 'video': video('cheer-scail.mp4', 'cheer-scail-poster.webp', 'SCAIL 動作參考示範影片', 'SCAIL motion-reference example'), 'audio_tracks': [voice('game-cheer')]},
        inputs=[{**PERSON, 'slot': 'reference'}, {**video('cheer-motion-source.mp4', 'cheer-motion-source-poster.webp', '原創動作影片', 'Original motion clip'), 'slot': 'video1'}, voice('game-cheer', 'audio1')],
        prefill={'subject_definitions': 'Picture 1: original adult cheer performer.', 'summary': 'A cheerful original cheer movement.',
                 'retention_analysis': 'Keep the original character reference and athletic clothing.', 'detailed_description': 'Follow the simple side-step rhythm from Video 1. Audio 1 is a separate voice reference.'},
        source_zh='動作成果來自 SCAIL，音訊來自 VoxCPM2，分別播放以介紹多模態素材；沒有執行 H3 或自動混音。',
        source_en='SCAIL motion and VoxCPM2 audio play separately to illustrate multimodal references; no H3 run or automatic audio mix is claimed.'),
    'qwen21-text2img': example('角色海報構圖的預填範例', 'A prefilled character-poster composition',
        '成年台灣應援角色，青綠白隊服，全身構圖，空曠棒球場，溫暖日光，沒有文字或標誌。',
        'An original adult Taiwanese cheer performer, teal-and-white uniform, full-body composition, empty stadium, warm daylight, no text or logos.',
        {'kind': 'image', 'images': [image('cheer-text2img.webp', 'Z-Image 預製角色構圖，非 Qwen 輸出', 'Prepared Z-Image character composition, not Qwen output')]},
        prefill={'positive': 'An original adult Taiwanese cheer performer, teal-and-white uniform, full-body composition, empty stadium, warm daylight, no text or logos.'},
        source_zh='以 Z-Image 已生成圖片說明構圖與結果檢視；沒有執行 Qwen，也不作為 Qwen 文字生成能力的證據。',
        source_en='An existing Z-Image result illustrates composition and result viewing; it is not evidence of Qwen generation or text rendering.'),
    'qwen21-edit': example('換裝指令：紅色球衣與深藍短褲', 'An outfit-edit instruction: red jersey and navy shorts',
        '把 <image1> 的青綠球衣改成紅色，白短褲改成深藍色，保留人物與背景。',
        'Change the teal jersey in <image1> to red and the white shorts to navy; keep the person and background.',
        {'kind': 'gallery', 'images': [image('cheer-reference.webp', '編輯前參考', 'Before: reference'), image('cheer-kontext.webp', '編輯概念：Flux Kontext 的換裝成果', 'Edit concept: a Flux Kontext outfit result')]},
        inputs=[{**PERSON, 'slot': 'image'}], prefill={'instruction': 'Change the teal jersey in <image1> to red and the white shorts to navy; keep the person and background.'},
        source_zh='前後對照取自 Flux Kontext 實跑，展示編輯指令與結果位置；不是 Qwen 編輯成果。',
        source_en='The comparison comes from a completed Flux Kontext run and illustrates edit controls; it is not a Qwen editing result.'),
}
