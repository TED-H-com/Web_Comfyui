"""Fixed interface examples, kept separate from verified workflow recordings.

Media provenance identifies the engine that actually produced each asset. A
prepared example never establishes that the tool named by its form was run.
"""
from copy import deepcopy

from scripts.voice_demo_catalog import VOICE_TRACKS


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
}
