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
            html = '<div id="workspace"><section class="feature-panel"><button data-guide-feature="voice-library"></button><div class="voice-lib"><button>Upload</button></div></section></div><div id="sheetLayer"></div>'
            page.route('http://127.0.0.1/**', lambda route: route.fulfill(status=200, content_type='text/html', body=html) if route.request.url == 'http://127.0.0.1/demo/' else route.fulfill(status=200, content_type='application/octet-stream', body=b''))
            page.goto('http://127.0.0.1/demo/')
            page.evaluate('''() => {
              window.WebComfyI18n={language:'zh',translate(){}};
              window.WebComfyExplorers={demoContent(){const node=document.createElement('div');node.demo={reset(){},dispose(){},start(){return Promise.resolve(true);}};return node;}};
            }''')
            page.add_script_tag(path=str(ROOT / 'webui/docs/demo-drive.js'))
            page.add_script_tag(path=str(ROOT / 'webui/docs/demo-cases.js'))
            page.add_script_tag(path=str(ROOT / 'webui/docs/tour.js'))
            page.evaluate('''() => {
              const demoSet = {
                subjects: [{id:'sub1', character_id:'sub1', name_zh:'小晴', name_en:'Qing', listed:true, reference_images:[], voice:'v1'}],
                materials: [
                  {id:'v1', kind:'audio', file:'examples/v1.wav', label_zh:'溫柔女聲', label_en:'Soft voice', gender:'female', subject:'sub1', library:true, folder:'voices', transcript:'第一段原始旁白', transcript_en:'First sample'},
                  {id:'v2', kind:'audio', file:'examples/v2.wav', label_zh:'明亮男聲', label_en:'Bright voice', gender:'male', subject:null, library:true, folder:'voices', transcript:'第二段原始旁白', transcript_en:'Second sample'}
                ],
                cases: [
                  {id:'vc1', feature_id:'voice-clone', order:1, display:true, kind:'prepared', label_zh:'溫柔', label_en:'Soft', inputs:{voice:'v1'}, settings:{speech:'第一段重說'}, result:{kind:'audio', files:['examples/out1.wav']}},
                  {id:'vc2', feature_id:'voice-clone', order:2, display:true, kind:'prepared', label_zh:'明亮', label_en:'Bright', inputs:{voice:'v2'}, settings:{speech:'第二段重說'}, result:{kind:'audio', files:['examples/out2.wav']}},
                  {id:'vs1', feature_id:'voice-speak', order:1, display:true, kind:'prepared', label_zh:'說話', label_en:'Speak', inputs:{}, settings:{voiceId:'v1', sampleText:'你好'}, result:{kind:'audio', files:['examples/out3.wav']}}
                ]
              };
              const demo={prompt:'prepared example',evidence:{kind:'prepared-example'},output:{kind:'audio',audio_tracks:[]}};
              const app={demo_set:demoSet, demo_runs:{}, features:[{id:'voice-library'},{id:'voice-batch'},{id:'voice-clone',fields:[{id:'speech',type:'textarea'}]},{id:'voice-speak'}], demos:{'voice-library':demo,'voice-batch':{...demo,prefill:{text:'Part one\\n\\nPart two\\n\\nPart three'}},'voice-clone':demo}};
              WebComfyDemoDrive.install(app);
              WebComfyDemoCases.install(app);
              WebComfyTour.install(app);
            }''')
            assert page.locator('.voice-lib').count() == 0
            assert page.locator('form[data-docs-voice-form="voice-library"]').count() == 1
            assert page.locator('form label[data-upload-slot="voice"]').count() == 1
            assert page.locator('form .generate-btn').count() == 0
            assert page.locator('[data-voice-actions]').count() == 0
            assert page.locator('.demo-voice-library .demo-voice-card').count() == 2
            assert '第二段原始旁白' in page.locator('.demo-voice-library').inner_text()
            link = page.locator('.demo-voice-card').nth(1).locator('a[href^="#/studio/voice-clone"]')
            assert link.get_attribute('href') == '#/studio/voice-clone?case=vc2'
            link.click()
            # demo-drive.js moves ?case= out of the hash on hashchange; the form below must still open case vc2.
            page.wait_for_function("() => location.hash === '#/studio/voice-clone'", timeout=5000)
            page.evaluate('''() => {
              document.getElementById('workspace').innerHTML='<section class="feature-panel"><button data-guide-feature="voice-clone"></button><form><label data-upload-slot="voice"><input type="file" name="voice"></label><label data-field-id="speech"><textarea data-field-id="speech"></textarea></label><button type="submit">Generate</button></form></section>';
            }''')
            page.wait_for_selector('.demo-case-strip button', timeout=5000)
            assert page.locator('[data-demo-voice-selector]').count() == 0
            assert page.locator('form').get_attribute('data-demo-case') == 'vc2'
            assert page.locator('textarea[data-field-id=speech]').input_value() == '第二段重說'
            page.evaluate('''() => {document.getElementById('workspace').innerHTML='<section class="feature-panel"><button data-guide-feature="voice-batch"></button><div class="voice-batch-page">Native batch</div></section>';}''')
            page.wait_for_selector('[data-docs-voice-form=voice-batch]')
            assert page.locator('.voice-batch-page').count() == 0
            assert page.locator('form').count() == 1
            assert 'Part three' in page.locator('[name=text]').input_value()
            assert page.evaluate("WebComfyTour.start(document.querySelector('form'))") is True
