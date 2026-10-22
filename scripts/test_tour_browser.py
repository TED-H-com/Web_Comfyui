"""Verify recorded-run form prefills and reference selection without a GPU."""
from pathlib import Path
import shutil
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width', [390, 768, 1440])
def test_recorded_inputs(tmp_path, width):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as driver:
        executable = shutil.which('chromium') or shutil.which('google-chrome')
        with driver.chromium.launch(executable_path=executable,
                                    args=['--no-sandbox', '--disable-dev-shm-usage']) as browser:
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.set_content('''<div id="workspace"><section class="feature-panel">
              <button data-guide-feature="sample"></button><form>
              <textarea data-field-id="prompt"></textarea>
              <input type="number" data-field-id="steps">
              <input type="range" data-field-id="strength" min="0" max="1" step="0.1">
              <input type="checkbox" data-field-id="enabled">
              <select data-field-id="model"><option value="old">Old</option><option value="tested">Tested</option></select>
              <div data-field-id="aspect"><button type="button" data-value="512x512" class="active">Square</button><button type="button" data-value="512x768">Portrait</button></div>
              <label data-upload-id="voice"><input type="file" accept="audio/wav"></label>
              <label data-upload-id="motion"><input type="file" accept="video/mp4"></label>
              <label><input type="file" data-field-id="face" accept="image/webp"></label>
              <button type="submit">Generate</button></form></section></div><div id="sheetLayer"></div>''')
            page.evaluate('''() => {
              window.WebComfyI18n={language:'zh',translate(){}};
              window.WebComfyExplorers={demoContent(){const node=document.createElement('div');node.demo={reset(){},dispose(){}};return node;}};
              document.querySelectorAll('[data-field-id="aspect"] button').forEach(button=>button.onclick=()=>{button.parentElement.querySelectorAll('button').forEach(other=>other.classList.toggle('active',other===button));});
            }''')
            page.add_script_tag(path=str(ROOT / 'webui/docs/tour.js'))
            page.evaluate('''() => {
              const fields=[{id:'prompt',type:'textarea'}, {id:'steps',type:'number',default:20},{id:'strength',type:'slider',default:0.2},{id:'enabled',type:'checkbox',default:false},{id:'model',type:'select',default:'old'},{id:'aspect',type:'aspect',default:'512x512'}];
              WebComfyTour.install({features:[{id:'sample',fields}],demos:{sample:{prompt:'explanation',prefill:{prompt:'exact model prompt',steps:8,strength:0.7,enabled:true,model:'tested',aspect:'512x768'},inputs:[{slot:'voice',kind:'audio',src:'/voice.wav',alt:'Voice'},{slot:'face',src:'/face.webp',alt:'Face'},{slot:'motion',kind:'video',src:'/motion.mp4',poster:'/poster.webp',alt:'Motion'}],output:{kind:'unavailable'}}}});
            }''')
            assert page.locator('[data-field-id="prompt"]').input_value() == 'exact model prompt'
            assert page.locator('[data-field-id="steps"]').input_value() == '8'
            assert page.locator('[data-field-id="strength"]').input_value() == '0.7'
            assert page.locator('[data-field-id="enabled"]').is_checked()
            assert page.locator('[data-field-id="model"]').input_value() == 'tested'
            assert page.locator('[data-field-id="aspect"] .active').get_attribute('data-value') == '512x768'
            assert page.locator('label[data-upload-id="motion"] video').get_attribute('src') == '/motion.mp4'
            assert page.locator('label:has([data-field-id="face"]) img').get_attribute('src') == '/face.webp'
            assert not page.locator('video').get_attribute('autoplay')
            assert page.locator('label[data-upload-id=voice] audio').get_attribute('src') == '/voice.wav'
            assert page.locator('label[data-upload-id=voice] audio').get_attribute('controls') is not None
            assert not page.locator('audio').get_attribute('autoplay')
            page.locator('[data-field-id="prompt"]').fill('')
            page.locator('[data-field-id="steps"]').fill('13')
            page.locator('[data-field-id="enabled"]').uncheck()
            page.locator('[data-field-id="model"]').select_option('old')
            page.locator('[data-value="512x512"]').click()
            page.evaluate("WebComfyI18n.language='en';window.dispatchEvent(new Event('webcomfy:language'))")
            assert page.locator('[data-field-id="prompt"]').input_value() == ''
            assert page.locator('[data-field-id="steps"]').input_value() == '13'
            assert not page.locator('[data-field-id="enabled"]').is_checked()
            assert page.locator('[data-field-id="model"]').input_value() == 'old'
            assert page.locator('[data-field-id="aspect"] .active').get_attribute('data-value') == '512x512'
            page.locator('.demo-prefill-bar button').click()
            assert page.locator('[data-field-id="steps"]').input_value() == '8'
            assert page.locator('[data-field-id="model"]').input_value() == 'tested'
            assert page.locator('[data-field-id="enabled"]').is_checked()


def test_prepared_voice_forms_and_selection(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as driver:
        executable = shutil.which('chromium') or shutil.which('google-chrome')
        with driver.chromium.launch(executable_path=executable, args=['--no-sandbox', '--disable-dev-shm-usage']) as browser:
            page = browser.new_page()
            page.set_content('<div id="workspace"><section class="feature-panel"><button data-guide-feature="voice-library"></button><div class="voice-lib"><button>Upload</button></div></section></div><div id="sheetLayer"></div>')
            page.evaluate('''() => {
              window.WebComfyI18n={language:'zh',translate(){}};
              window.WebComfyExplorers={demoContent(){const node=document.createElement('div');node.demo={reset(){},dispose(){},start(){return Promise.resolve(true);}};return node;}};
            }''')
            page.add_script_tag(path=str(ROOT / 'webui/docs/tour.js'))
            page.evaluate('''() => {
              const tracks=[{src:'/soft.wav',title:'溫柔女聲',title_en:'Soft female voice',transcript:'第一段原始旁白',transcript_en:'First sample'}, {src:'/bright.wav',title:'明亮女聲',title_en:'Bright female voice',transcript:'第二段原始旁白',transcript_en:'Second sample'}];
              const demo={prompt:'prepared example',evidence:{kind:'prepared-example'},output:{kind:'audio',audio_tracks:tracks}};
              WebComfyTour.install({features:[{id:'voice-library'},{id:'voice-batch'},{id:'voice-clone',fields:[{id:'sampleText',type:'textarea'}]}],demos:{'voice-library':{...demo,prefill:{name:'Demo voice',source:'Prepared',transcript:'Sample transcript'}},'voice-batch':{...demo,prefill:{text:'Part one\\n\\nPart two\\n\\nPart three'}},'voice-clone':demo}});
            }''')
            assert page.locator('.voice-lib').count() == 0
            assert page.locator('form').count() == 1
            assert page.locator('[name=name]').input_value() == 'Demo voice'
            page.evaluate("WebComfyTour.start(document.querySelector('form'))")
            page.locator('[data-voice-actions] button').nth(3).click()
            assert page.evaluate('location.hash') == '#/studio/voice-clone'
            page.evaluate('''() => {document.getElementById('workspace').innerHTML='<section class="feature-panel"><button data-guide-feature="voice-clone"></button><form><label data-field-id="sampleText"><textarea data-field-id="sampleText"></textarea></label><button type="submit">Generate</button></form></section>';}''')
            page.wait_for_selector('[data-demo-voice-selector]')
            assert page.locator('[data-demo-voice-selector]').input_value() == '1'
            assert page.locator('textarea[data-field-id=sampleText]').input_value() == '第二段原始旁白'
            assert page.locator('audio').get_attribute('src') == '/bright.wav'
            page.locator('[data-demo-voice-selector]').select_option('0')
            assert page.locator('textarea[data-field-id=sampleText]').input_value() == '第一段原始旁白'
            page.evaluate('''() => {document.getElementById('workspace').innerHTML='<section class="feature-panel"><button data-guide-feature="voice-batch"></button><div class="voice-batch-page">Native batch</div></section>';}''')
            page.wait_for_selector('[data-docs-voice-form=voice-batch]')
            assert page.locator('.voice-batch-page').count() == 0
            assert page.locator('form').count() == 1
            assert 'Part three' in page.locator('[name=text]').input_value()
            assert page.evaluate("WebComfyTour.start(document.querySelector('form'))") is True
