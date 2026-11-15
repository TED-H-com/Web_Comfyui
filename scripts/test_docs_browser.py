"""Exercise the built guide with Chromium under the real Pages project subpath."""
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_docs import build, ROOT

ASSERT = r'''(async () => {
 const result = document.createElement('pre'); result.id = 'docs-test-result'; document.body.append(result);
 const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
 const until = async fn => {for (let i = 0; i < 300; i++) {if(fn())return; await wait(20);}throw Error('timeout: '+fn.toString());};
 const assert = (value, message) => {if(!value)throw Error(message);};
 const visible = selector => [...document.querySelectorAll(selector)].find(node => node !== document.body && node.getBoundingClientRect().width > 0);
 let createObjectUrlCalls = 0, fileReaderCalls = 0;
 const origCreateObjectUrl = URL.createObjectURL.bind(URL);
 URL.createObjectURL = (...args) => { createObjectUrlCalls++; return origCreateObjectUrl(...args); };
 for (const m of ['readAsDataURL', 'readAsArrayBuffer', 'readAsText', 'readAsBinaryString']) {
  const orig = FileReader.prototype[m];
  FileReader.prototype[m] = function (...args) { fileReaderCalls++; return orig.apply(this, args); };
 }
 const close = async () => {document.querySelector('.sheet-close').click(); await until(()=>!document.querySelector('[role="dialog"]'));};
 const feature = async name => {
  document.getElementById('studioToolTrigger').click();
  await until(()=>document.querySelector('.studio-tool-sheet-row'));
  const row=[...document.querySelectorAll('.studio-tool-sheet-row')].find(node=>node.textContent.includes(name));
  assert(row && row.getBoundingClientRect().height >= 44, 'real visible tool button '+name);row.click();
  await until(()=>document.querySelector('.app-feature-guide'));
 };
 try {
  await until(() => document.getElementById('studioToolTrigger'));
  await until(() => document.querySelector('[data-demo-id="text2img"]'));
  assert(document.documentElement.scrollWidth <= innerWidth + 1, 'horizontal overflow');
  assert(document.querySelector('.gen-form textarea'), 'actual app generation form');
  assert(document.querySelector('.topbar .brand').textContent === 'Web ComfyUI', 'actual app brand');
  assert(visible('[data-view="characters"]') && visible('[data-view="studio"]') && visible('[data-view="workflows"]'), 'same app navigation');
  assert(document.getElementById('servicePill').textContent==='互動說明','no fake live service');
  assert(document.querySelectorAll('.app-guide-button button').length===2,'two guide actions, no separate example button');
  assert(document.querySelector('[data-field-id="positive"] textarea')?.value || document.querySelector('textarea[data-field-id="positive"]').value, 'real form prefilled');
  assert(document.querySelector('.app-demo').hidden && !document.querySelector('.demo-output img,.demo-output video'), 'no result before Generate');
  if (new URLSearchParams(location.search).has('helptest')) {
    await until(()=>document.querySelector('[role="dialog"]'));
    assert(document.querySelector('[role="dialog"]').textContent.includes('互動說明與安裝'),'legacy help link');
    await close();
  }
  if(new URLSearchParams(location.search).has('screenshot')) {result.textContent = 'SCREENSHOT READY'; return;}
  if (!new URLSearchParams(location.search).has('slowguide')) document.querySelector('.app-guide-button button').click();
  await until(()=>document.querySelector('.app-feature-guide'));
  let detail = document.querySelector('.app-feature-guide');
  for(const text of ['z_image_turbo_bf16.safetensors','qwen_3_4b.safetensors','ae.safetensors','zimage_prompt','會怎麼跑','使用什麼技巧']) assert(detail.textContent.includes(text),'actual text2img source '+text);
  assert(!detail.textContent.includes('sd_xl_base_1.0'),'unrelated checkpoint not attributed');
  await close();
  document.querySelector('[data-action="view-graph"]').click();await until(()=>document.querySelector('.app-node-graph'));
  assert(document.querySelectorAll('.graph-node').length===9,'actual Z-Image builder graph');
  document.querySelector('.graph-node').click();assert(document.querySelector('.graph-inspector').textContent.includes('z_image_turbo_bf16.safetensors'),'source model on node');await close();
  document.querySelector('.generate-btn').click();
  assert(document.querySelector('.app-demo')?.dataset.state==='running' && document.querySelector('[role=progressbar]'),'visible simulated progress after submit');
  await until(()=>document.querySelector('.app-demo')?.dataset.state==='complete');
  assert(!document.querySelector('.demo-output').hidden && !document.querySelector('[role="dialog"]'),'result status appears in original form');
  if(WebComfyTour.demoFor('text2img').output.kind==='unavailable') assert(!document.querySelector('.demo-output img'),'untested text2img has no borrowed image');
  else await until(()=>document.querySelector('.demo-output img')?.naturalWidth>0);
  await feature('AI 指令改圖');
  detail=document.querySelector('.app-feature-guide');assert(detail.textContent.includes('qwen_image_2.1_int8_convrot.safetensors')&&!detail.textContent.includes('flux1-dev-kontext_fp8_scaled.safetensors'),'actual editing checkpoint');await close();
  assert(location.hash.includes('qwen21-edit'), 'feature route matches selected button');
  await until(()=>document.querySelector('[data-demo-id="qwen21-edit"]'));
  location.hash='#/studio/img2img';await until(()=>document.querySelector('[data-demo-id="img2img"]'));
  document.querySelector('.app-guide-button button').click();await until(()=>document.querySelector('.app-feature-guide'));
  detail=document.querySelector('.app-feature-guide');assert(detail.textContent.includes('flux1-dev-kontext_fp8_scaled.safetensors')&&detail.textContent.includes('目前一般工具選單隱藏'),'hidden Flux Kontext form stays documented at its direct route');await close();
  const editingDemo=WebComfyTour.demoFor('img2img');
  if(editingDemo.inputs?.length) {
    const original=document.querySelector('.demo-input-preview img');
    assert(original&&original.src===editingDemo.inputs[0].src,'reference visible before playback under Pages subpath');
    await until(()=>original.naturalWidth>0);
    assert(!editingDemo.output.images?.some(image=>image.src===original.src),'original not recycled as result');
  }
  assert(document.querySelector('.app-demo').hidden && !document.querySelector('.demo-references'), 'only upload reference visible before editing');
  document.querySelector('.generate-btn').click();await until(()=>document.querySelector('.app-demo')?.dataset.state==='complete');
  const comparison=document.querySelector('.demo-comparison');
  assert(comparison?.querySelectorAll('img').length===2,'before and after shown only after Generate');
  assert(comparison.querySelector('[data-comparison-before] img').src===editingDemo.inputs[0].src,'comparison shows exact original');
  assert(comparison.querySelector('figure:not([data-comparison-before]) img').src===editingDemo.output.images[0].src,'comparison shows actual recolored uniform');
  assert(comparison.textContent.includes('紅色上衣、深藍短褲'),'visible description of changed clothing');
  document.querySelector('.demo-prefill-bar button').click();
  assert(document.querySelector('.app-demo').hidden && !document.querySelector('.demo-output img'),'reset hides and removes result');
  const upload=document.querySelector('input[type=file][data-demo-upload]');
  const transfer=new DataTransfer();transfer.items.add(new File(['synthetic local input'],'local-example.png',{type:'image/png'}));
  const drop=new Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:transfer});
  upload.closest('label').dispatchEvent(drop);
  await until(()=>document.querySelector('.demo-toast')?.textContent.includes('不接受拖放'));
  upload.files=transfer.files;upload.dispatchEvent(new Event('change',{bubbles:true}));
  assert(!upload.value && createObjectUrlCalls===0 && fileReaderCalls===0,'local file selection never reads local file or creates blob');
  upload.click();await until(()=>document.querySelector('.demo-disk-modal'));
  document.querySelector('.demo-disk-modal [data-material-id="ph-cheer"]').click();
  await until(()=>!document.querySelector('.demo-disk-modal') && document.querySelector('.demo-slot-preview[data-slot="image"] img'));
  assert(document.querySelector('.demo-slot-preview[data-slot="image"] img').src.includes('cheer-subject-reference.webp'),'picked demo disk material fills preview without blob');
  assert(createObjectUrlCalls===0,'no blob URLs created after disk pick');
  const resourceTab=visible('[data-view="resources"]');
  if(resourceTab) resourceTab.click();
  else {document.getElementById('moreMenuBtn').click();await until(()=>document.querySelector('.shell-menu-row'));[...document.querySelectorAll('.shell-menu-row')].find(row=>row.textContent==='角色創作資源中心').click();}
  await until(()=>document.querySelector('.provider-fabric-host'));
  assert([...document.querySelectorAll('input[type="password"]')].every(node=>node.disabled),'credentials disabled');
  visible('[data-view="characters"]').click();await until(()=>document.querySelector('[data-demo-id="character-create"]'));
  assert(document.querySelector('.experimental-create-form input[name=name]').value,'character name prefilled');
  assert(document.querySelector('.experimental-create-form textarea[name=description]').value,'character description prefilled');
  assert(document.querySelector('.demo-input-preview img').src.includes('cheer-reference.webp'),'character reference matches photos slot');
  assert(document.querySelector('.app-demo').hidden&&!document.querySelector('.demo-character-card'),'character not created before submit');
  document.querySelector('.experimental-create-form button[type=submit]').click();
  await until(()=>document.querySelector('[data-session-character]'));
  assert(!document.querySelector('.experimental-create-form'),'creation closes form');
  assert(document.querySelector('.docs-character-header h1').textContent==='晴晴','created character name');
  assert(document.querySelectorAll('.docs-character-example').length===3,'prepared portrait outfit video examples');
  const firstId=document.querySelector('[data-session-character]').dataset.sessionCharacter;
  const reference=document.querySelector('.docs-character-reference').src;
  for(const media of document.querySelectorAll('.docs-character-example img,.docs-character-example video')) assert(media.src!==reference,'prepared results differ from reference');
  visible('[data-view="characters"]').click();await until(()=>document.querySelector('[data-character-id="'+firstId+'"]'));
  assert(!document.querySelector('.experimental-create-form'),'return list keeps form closed');
  document.querySelector('[data-character-id="'+firstId+'"]').click();await until(()=>document.querySelector('[data-session-character]'));
  visible('[data-view="characters"]').click();await until(()=>document.querySelector('.new-character-card'));
  document.querySelector('.new-character-card').click();await until(()=>document.querySelector('.experimental-create-form'));
  const characterForm=document.querySelector('.experimental-create-form');
  const personalName='文生圖';
  characterForm.elements.name.value=personalName;
  characterForm.elements.description.value='<img src=x onerror=alert(1)> 文生圖';
  characterForm.elements.photos.click();await until(()=>document.querySelector('.demo-disk-modal'));
  document.querySelector('.demo-disk-modal [data-material-id="ph-cheer"]').click();
  await until(()=>!document.querySelector('.demo-disk-modal') && characterForm.querySelector('.demo-slot-preview[data-slot="photos"] img'));
  characterForm.querySelector('button[type=submit]').click();await until(()=>document.querySelector('[data-session-character]'));
  assert(document.querySelector('.docs-character-header h1').textContent===personalName&&!document.querySelector('.docs-character-header h1 img'),'name is safe literal text');
  assert(document.querySelector('.docs-character-description').textContent==='<img src=x onerror=alert(1)> 文生圖' && !document.querySelector('.docs-character-description img'),'custom description retained');
  const cardPhoto=document.querySelector('.docs-character-reference'),cardURL=cardPhoto.src;
  assert(cardURL.includes('cheer-subject-reference.webp')&&!cardURL.startsWith('blob:'),'session owns picked published photo URL without blob');
  await until(()=>cardPhoto.naturalWidth>0);
  assert((await (await fetch('/api/characters')).json()).characters.length===2,'both characters in memory list');
  const lang=document.getElementById('languageSelect');lang.value='en';lang.dispatchEvent(new Event('change'));
  await until(()=>document.documentElement.lang==='en' && visible('[data-view="characters"]').textContent.includes('Characters'));
  assert(document.querySelector('.docs-character-header h1').textContent===personalName,'language preserves personal name');
  [...document.querySelectorAll('.docs-character-workspace button')].find(button=>button.textContent==='Delete character').click();
  await until(()=>!document.querySelector('[data-session-character]') && document.querySelector('.new-character-card,.experimental-create-form'));
  await until(()=>document.querySelector('#workspace h1')?.textContent==='Character gallery');
  assert(document.querySelector('#workspace').textContent.includes('Choose a character to start creating.'),'English character list guidance');
  assert(document.querySelector('#workspace').textContent.includes('+ New character'),'English new character action');
  assert(document.querySelector('.works-badge').textContent==='0 media items','English character media count');
  assert((await (await fetch('/api/characters')).json()).characters.length===1,'deleting one preserves other character');
  document.querySelector('[data-character-id="'+firstId+'"]').click();await until(()=>document.querySelector('[data-session-character]'));
  [...document.querySelectorAll('.docs-character-workspace button')].find(button=>button.textContent==='Clear all session characters').click();
  await until(()=>!document.querySelector('[data-session-character]') && document.querySelector('.new-character-card,.experimental-create-form'));
  assert((await (await fetch('/api/characters')).json()).characters.length===0,'clear session removes all characters');
  assert(document.documentElement.scrollWidth<=innerWidth+1,'English hardware header overflow');
  assert(document.querySelector('.topbar .brand').getBoundingClientRect().width>70,'English brand remains visible');
  location.hash='#/studio/img2img';await until(()=>document.querySelector('form[data-demo-id="img2img"]'));
  const englishForm=document.querySelector('form[data-demo-id="img2img"]');
  const edited=englishForm.querySelector('textarea');edited.value='My unchanged prompt 文生圖';edited.dispatchEvent(new Event('input',{bubbles:true}));
  const keptFile=englishForm.querySelector('input[type=file]');
  keptFile.click();await until(()=>document.querySelector('.demo-disk-modal'));
  document.querySelector('.demo-disk-modal [data-material-id="ph-cheer"]').click();
  await until(()=>!document.querySelector('.demo-disk-modal') && englishForm.querySelector('.demo-slot-preview[data-slot="image"]'));
  lang.value='zh-Hant';lang.dispatchEvent(new Event('change'));await until(()=>document.documentElement.lang==='zh-Hant');
  await until(()=>/[\u4e00-\u9fff]/.test(englishForm.querySelector('.app-demo h3').textContent));
  assert(edited.value==='My unchanged prompt 文生圖'&&englishForm.querySelector('.demo-slot-preview[data-slot="image"]').dataset.materialId==='ph-cheer','language preserves user prompt and picked material');
  englishForm.querySelector('.demo-prefill-bar button').click();
  assert(!keptFile.dataset.demoMaterial&&!englishForm.querySelector('.demo-slot-preview'),'reset inputs clears picked slot preview');
  visible('[data-view="workflows"]').click();await until(()=>document.querySelectorAll('.workflow-card').length);
  const mode = document.querySelector('[aria-label="收錄狀態"]'); mode.value = 'local'; mode.dispatchEvent(new Event('change'));
  document.querySelector('.workflow-card').click();await until(()=>document.querySelector('.workflow-details'));
  detail=document.querySelector('.workflow-details');
  assert(!detail.textContent.includes('載入 ComfyUI') && !detail.textContent.includes('開啟 ComfyUI'), 'no runtime actions');
  const download=[...detail.querySelectorAll('a')].find(node=>node.textContent==='下載 JSON');assert(download.pathname.startsWith('/Web_Comfyui/downloads/'),'subpath download');
  const response=await fetch(download.href);assert(response.ok,'approved download works');
  const graph=await response.json();assert(graph.nodes || Object.values(graph).some(v=>v?.class_type),'actual graph');
  await close();
  const search=document.querySelector('[aria-label="搜尋工作流"]');search.value='no-such-workflow-xyz';search.dispatchEvent(new Event('input'));
  assert(document.querySelectorAll('.workflow-card').length===0,'empty search');search.value='';search.dispatchEvent(new Event('input'));
  const features=(await(await fetch('/api/features')).json()).features;
  const originalMatchMedia=window.matchMedia;
  window.matchMedia=query=>query.includes('prefers-reduced-motion')?{matches:true,addEventListener(){},removeEventListener(){}}:originalMatchMedia(query);
  for(const entry of features) {
    window.__currentFeature=entry.id;
    location.hash='#/studio/'+entry.id;
    await until(()=>document.querySelector('[data-demo-id="'+entry.id+'"]'));
    const form=document.querySelector('[data-demo-id="'+entry.id+'"]');
    assert(form.querySelector('.app-demo').hidden&&!form.querySelector('.demo-output img,.demo-output video'),'every tool waits for Generate '+entry.id);
    const generate=form.querySelector('.generate-btn,button[type=submit]');assert(generate&&!generate.disabled,'demo action ready '+entry.id);
    generate.click();await until(()=>form.querySelector('.app-demo')?.dataset.state==='complete');
    assert(!form.querySelector('.demo-output').hidden,'all options produce a prepared result '+entry.id);
    const example=WebComfyTour.demoFor(entry.id);
    if(entry.id==='image-blend') {
      const blend=form.querySelector('.demo-comparison');
      assert(blend?.querySelectorAll('img').length===2,'subject blend shows original and composite');
      assert(blend.textContent.includes('保留人物、更換背景')&&!blend.textContent.includes('紅色上衣'),'subject blend captions describe background replacement');
      assert(blend.querySelector('figure:not([data-comparison-before]) img').src!==blend.querySelector('[data-comparison-before] img').src,'subject composite differs from original');
    }
    if(example.output.kind==='unavailable') assert(!form.querySelector('.demo-output img,.demo-output video'),'untested tool has no unrelated media '+entry.id);
    if (example.output.audio_tracks?.length) {
      const clips=[...form.querySelectorAll('.demo-output audio')];
      assert(clips.length===example.output.audio_tracks.length,'all prepared audio clips shown '+entry.id);
      for(const clip of clips) {clip.load();await until(()=>clip.readyState>=1);assert(clip.duration>0,'audio metadata '+entry.id);assert(!clip.autoplay,'audio requires user playback');}
      clips[0].muted=true;await clips[0].play();await until(()=>clips[0].currentTime>0);clips[0].pause();
      assert(!form.querySelector('.voice-lib,.voice-batch-page'),'no competing native upload/batch flow');
    }
    if(example.evidence?.kind==='local-comfyui') {
      for(const source of example.inputs || []) if(source.slot) {
        const preview=form.querySelector('[data-upload-id="'+source.slot+'"]')?.closest('.upload-slot')?.querySelector('.demo-input-preview img,.demo-input-preview video,.demo-input-preview audio') || form.querySelector('[data-upload-id="'+source.slot+'"] .demo-input-preview img,[data-upload-id="'+source.slot+'"] .demo-input-preview video,[data-upload-id="'+source.slot+'"] .demo-input-preview audio');
        assert(preview?.src===source.src,'actual upload slot uses recorded reference '+entry.id+' '+source.slot);
      }
      assert(form.querySelector('.demo-run-record').textContent.includes(example.evidence.prompt),'exact tested prompt '+entry.id);
      for(const field of entry.fields || []) if(field.type==='aspect' && example.prefill?.[field.id]) assert(form.querySelector('[data-field-id="'+field.id+'"] button.active')?.dataset.value===example.prefill[field.id],'recorded dimensions selected '+entry.id);
      assert(form.querySelector('.demo-output h4').textContent.includes('GPU'),'recorded GPU result label '+entry.id);
      for(const image of form.querySelectorAll('.demo-output img')) await until(()=>image.naturalWidth>0);
      const video=form.querySelector('.demo-output video');
      if(video) {video.preload='metadata';video.load();await until(()=>video.readyState>=1);assert(video.duration>0,'recorded video metadata '+entry.id);video.muted=true;await video.play();await until(()=>video.currentTime>0.1&&video.readyState>=2);video.pause();}
    }
    if(entry.id==='voice-library' && example.output.audio_tracks?.length>1) {
      form.querySelectorAll('[data-voice-actions] button')[3].click();
      await until(()=>document.querySelector('form[data-demo-id="voice-clone"] [data-demo-voice-selector]'));
      const selector=document.querySelector('[data-demo-voice-selector]');
      const transcript=document.querySelector('textarea[data-field-id="sampleText"]');
      assert(selector.value==='1' && transcript.value===example.output.audio_tracks[1].transcript,'library selection carries exact reference transcript to clone');
      selector.value='0';selector.dispatchEvent(new Event('change'));
      assert(transcript.value===example.output.audio_tracks[0].transcript,'changing selected reference updates actual textarea');
      assert(document.querySelector('.demo-voice-selection audio').src===example.output.audio_tracks[0].src,'reference audition uses selected audio');
    }
  }
  window.matchMedia=originalMatchMedia;
  const hardware=document.getElementById('hardwareSelect');assert(hardware,'hardware selector');
  const chooseHardware=async id=>{hardware.value=id;hardware.dispatchEvent(new Event('change'));await wait(60);};
  location.hash='#/studio/text2img';await until(()=>document.querySelector('form[data-demo-id="text2img"]'));
  const hardwareForm=document.querySelector('form[data-demo-id="text2img"]');
  const hardwarePrompt=hardwareForm.querySelector('textarea');hardwarePrompt.value='Preserve this hardware-switch input';
  const bannerLinks=[...document.querySelectorAll('#docsBanner a[target="_blank"]')];
  assert(bannerLinks.length>=2&&bannerLinks.every(a=>a.rel.includes('sponsored')&&a.target==='_blank'),'banner referrals');
  assert(bannerLinks.some(a=>a.href.includes('ref=TEDH2FND'))&&bannerLinks.some(a=>a.href.includes('inviteCode=qqgzxufp')),'banner hrefs');
  await chooseHardware('none');
  const cloud=hardwareForm.querySelector('.hardware-cloud-option');
  const cloudLinks=[...cloud.querySelectorAll('a')];
  assert(cloud&&!cloud.hidden&&cloudLinks.length===2&&cloud.textContent.includes('TEDH2FND')&&cloud.textContent.includes('qqgzxufp'),'hardware referrals when blocked');
  assert(cloudLinks.every(a=>a.rel.includes('sponsored')&&a.target==='_blank'),'hardware referral rel');
  assert(!WebComfyHardware.canUse('text2img')&&hardwareForm.querySelector('.generate-btn').disabled,'no GPU disables generation');
  assert(await WebComfyTour.start(hardwareForm)===false,'direct generation also blocked');
  if(innerWidth>=1280)assert(document.getElementById('studio-side-tool-text2img').getAttribute('aria-disabled')==='true','sidebar indicates unavailable workflow');
  if(innerWidth>=1280)for(const card of document.querySelectorAll('.side-tool'))for(const child of card.children)assert(!child.getBoundingClientRect().width||child.getBoundingClientRect().right<=card.getBoundingClientRect().right+1,'hardware status stays inside sidebar');
  await chooseHardware('vram-24');assert(WebComfyHardware.canUse('text2img')&&!WebComfyHardware.canUse('dance'),'24 GB planning threshold');
  assert(hardwareForm.querySelector('.hardware-cloud-option').hidden,'hardware referrals hidden when allowed');
  await chooseHardware('dual-24');assert(!WebComfyHardware.canUse('dance'),'dual GPUs do not pool VRAM');
  await chooseHardware('autoip');assert(features.every(entry=>WebComfyHardware.canUse(entry.id)),'AutoIP opens every demonstration');
  assert(!hardwareForm.querySelector('.generate-btn').disabled&&hardwarePrompt.value==='Preserve this hardware-switch input','mode switch restores action and preserves inputs');
  await chooseHardware('vram-32');
  const blocked=await fetch('/api/generate',{method:'POST',body:'PRIVATE_TEST_INPUT'});assert(blocked.status===405,'writes blocked in memory');
  const external=await fetch('https://example.invalid/api');assert(external.status===405,'external calls blocked');
  assert((await fetch('/api/unlisted')).status===404,'unknown endpoint fails closed');
  assert(createObjectUrlCalls===0 && fileReaderCalls===0,'zero local file reads or blob URLs');
  assert(document.documentElement.scrollWidth<=innerWidth+1,'final overflow');
  assert(window.__docsErrors.length===0,window.__docsErrors.join(';'));
  result.textContent='PASS';
 }catch(error){result.textContent='FAIL '+error.message+' feature: '+(window.__currentFeature||'')+' route: '+location.hash+' errors: '+window.__docsErrors.join(';');}
})();
'''


@pytest.mark.parametrize('initial_page', ['app', 'feature', 'help'])
def test_static_docs_browser(tmp_path, initial_page):
    playwright = pytest.importorskip('playwright.sync_api')
    browser = shutil.which('chromium')
    if not browser:
        with playwright.sync_playwright() as driver:
            candidate = driver.chromium.executable_path
            if Path(candidate).is_file():
                browser = candidate
    if not browser:
        pytest.skip('Chromium not installed')
    output = tmp_path / 'site'; build(ROOT, output)
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            path = unquote(urlsplit(self.path).path)
            if path == '/Web_Comfyui/monitor.js':
                data = b"window.__docsErrors=[];addEventListener('error',e=>__docsErrors.push(e.message));addEventListener('unhandledrejection',e=>__docsErrors.push(String(e.reason)));"; mime='text/javascript'
            elif path == '/Web_Comfyui/assert.js':
                data = ASSERT.encode(); mime = 'text/javascript'
            elif path.startswith('/Web_Comfyui/'):
                relative = path.removeprefix('/Web_Comfyui/')
                if not relative or relative.endswith('/'): relative += 'index.html'
                file = output / relative
                if not file.resolve().is_relative_to(output.resolve()) or not file.is_file(): self.send_error(404); return
                data = file.read_bytes()
                if initial_page != 'app' and file.name == 'app.js': time.sleep(0.5)
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.webp': 'image/webp', '.mp4': 'video/mp4', '.wav': 'audio/wav'}.get(file.suffix, 'text/plain')
                if relative == 'demo/index.html': data = data.replace(b'<head>', b'<head><script src="../monitor.js"></script>').replace(b'</body>', b'<script src="../assert.js"></script></body>')
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            requests.append('POST ' + self.path); self.send_error(405)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                for width in ((1440, 768, 390) if initial_page == 'app' else (390,)):
                    route = {'app': '', 'feature': '?slowguide#/studio/text2img?guide=1', 'help': '?helptest#/start'}[initial_page]
                    with instance.new_context(viewport={'width': width, 'height': 900}) as context:
                        page = context.new_page()
                        page.goto(f'http://127.0.0.1:{server.server_port}/Web_Comfyui/demo/{route}')
                        assert page.evaluate('innerWidth') == width
                        for _ in range(1800):
                            if page.locator('#docs-test-result').count() and page.locator('#docs-test-result').inner_text().startswith(('PASS', 'FAIL')):
                                break
                            page.wait_for_timeout(100)
                        marker = page.locator('#docs-test-result').inner_text()
                        assert marker == 'PASS', (width, marker)
                        page.evaluate("WebComfyCharacterSession.create({name:'Refresh check'})")
                        assert page.evaluate("WebComfyCharacterSession.list().length") == 1
                        page.reload()
                        page.wait_for_function("window.WebComfyCharacterSession !== undefined")
                        assert page.evaluate("WebComfyCharacterSession.list().length") == 0
        assert not any('/api/' in path or path.startswith('POST ') for path in requests)
        assert any('/downloads/' in path for path in requests)
    finally:
        server.shutdown(); server.server_close()


def test_u1a_closed_loop_browser(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    browser = shutil.which('chromium')
    if not browser:
        with playwright.sync_playwright() as driver:
            candidate = driver.chromium.executable_path
            if Path(candidate).is_file():
                browser = candidate
    if not browser:
        pytest.skip('Chromium not installed')

    from functools import partial
    from http.server import SimpleHTTPRequestHandler

    output = tmp_path / 'Web_Comfyui'
    build(ROOT, output)
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()
        def do_POST(self):
            requests.append('POST ' + self.path)
            self.send_error(405)

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page = instance.new_page(viewport={'width': 1280, 'height': 900})
                errors, external, filechoosers = [], [], []
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.on('filechooser', lambda fc: filechoosers.append(fc))
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else (external.append(route.request.url), route.abort()))
                page.add_init_script("""(() => {
                  window.__u1aCounts = {createObjectURL: 0, fileReader: 0};
                  const origCreate = URL.createObjectURL.bind(URL);
                  URL.createObjectURL = (...a) => { window.__u1aCounts.createObjectURL++; return origCreate(...a); };
                  for (const m of ['readAsDataURL', 'readAsArrayBuffer', 'readAsText', 'readAsBinaryString']) {
                    const orig = FileReader.prototype[m];
                    FileReader.prototype[m] = function (...a) { window.__u1aCounts.fileReader++; return orig.apply(this, a); };
                  }
                })();""")

                page.goto(f'{origin}/Web_Comfyui/demo/#/studio/image-blend', wait_until='domcontentloaded', timeout=12000)
                page.locator('form[data-demo-id="image-blend"] label.dropzone[data-upload-id="image2"]').wait_for(timeout=12000)

                # Keyboard activation on dropzone opens Demo Disk, Esc returns focus to opener
                drop2 = page.locator('form[data-demo-id="image-blend"] label.dropzone[data-upload-id="image2"]')
                drop2.focus()
                page.keyboard.press('Enter')
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                assert [' '.join(t.inner_text().split()) for t in page.locator('.demo-folder-btn').all()] == [
                    '人物照片 (1)', '場景 (2)', '影片 (1)', '聲音 (2)'
                ]
                assert page.locator('.demo-folder-btn[data-folder="people"]').get_attribute('aria-selected') == 'true'
                page.locator('.demo-folder-btn[data-folder="videos"]').click()
                vid_tile = page.locator('.demo-disk-modal .demo-tile[data-material-id="ph-wave"]')
                assert vid_tile.get_attribute('aria-disabled') == 'true'
                assert '這個欄位只接受圖片' in vid_tile.inner_text()
                vid_tile.focus()
                assert page.evaluate("() => document.activeElement?.dataset?.materialId") == 'ph-wave'
                vid_tile.click(force=True, timeout=5000)
                assert page.locator('.demo-disk-modal').is_visible()
                page.keyboard.press('Escape')
                page.locator('.demo-disk-modal').wait_for(state='detached', timeout=5000)
                assert page.evaluate("() => document.activeElement === document.querySelector('label.dropzone[data-upload-id=\"image2\"]')")

                # Case badge + default folder from dataset.demoCase + upload progress + event + live i18n
                page.evaluate("""() => {
                  const form = document.querySelector('form[data-demo-id="image-blend"]');
                  form.dataset.demoCase = 'ph-blend-case';
                  window.__pickedEvents = [];
                  form.addEventListener('demo:material-picked', e => window.__pickedEvents.push(e.detail));
                }""")
                drop2.click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                assert page.locator('.demo-folder-btn[data-folder="scenes"]').get_attribute('aria-selected') == 'true'
                stadium_tile = page.locator('.demo-disk-modal .demo-tile[data-material-id="ph-stadium"]')
                assert '本案例使用的素材' in stadium_tile.inner_text()
                assert '有預跑結果' in stadium_tile.inner_text()
                page.evaluate("() => window.WebComfyI18n.setLanguage('en')")
                page.wait_for_function("() => document.documentElement.lang === 'en'", timeout=5000)
                assert 'Used by this case' in stadium_tile.inner_text()
                assert page.locator('.sheet-head h2').inner_text() == 'Demo disk (simulated upload)'
                page.evaluate("() => window.WebComfyI18n.setLanguage('zh-Hant')")
                page.wait_for_function("() => document.documentElement.lang === 'zh-Hant'", timeout=5000)
                assert page.locator('.sheet-head h2').inner_text() == '示範硬碟（模擬上傳）'
                stadium_tile.click()
                page.locator('.demo-disk-modal [role="progressbar"]').wait_for(timeout=3000)
                page.locator('.demo-slot-preview[data-slot="image2"]').wait_for(timeout=5000)
                assert '已上傳到素材庫' in page.locator('.demo-toast').inner_text()
                assert page.evaluate("() => window.__pickedEvents.at(-1)") == {'slot': 'image2', 'materialId': 'ph-stadium', 'source': 'upload'}

                # Reduced motion completes without waiting, external input & drop refused
                page.emulate_media(reduced_motion='reduce')
                page.locator('form[data-demo-id="image-blend"] [data-material-picker="image1"]').click()
                page.locator('.demo-library-modal').wait_for(timeout=5000)
                group_titles = [g.inner_text() for g in page.locator('.demo-library-group-title').all()]
                assert group_titles[0] == '你剛剛加入的'
                assert '人物照片' in group_titles and '預跑成果' in group_titles
                page.locator('.demo-library-modal .demo-tile[data-material-id="ph-cheer"]').click()
                page.locator('.demo-slot-preview[data-slot="image1"]').wait_for(timeout=5000)
                assert page.locator('.demo-disk-modal [role="progressbar"]').count() == 0
                page.emulate_media(reduced_motion='no-preference')

                page.evaluate("""() => {
                  const ext = document.createElement('input');
                  ext.type = 'file';
                  ext.id = 'ext-outside-input';
                  document.body.append(ext);
                }""")
                page.locator('#ext-outside-input').click()
                page.wait_for_function("() => document.querySelector('.demo-toast')?.textContent.includes('不接受拖放')", timeout=5000)

                # Lipsync URL alias, crawl & Drive stubs
                page.evaluate("() => { location.hash = '#/studio/lipsync'; }")
                page.locator('form[data-demo-id="lipsync"] [data-url-fetch="video"]').wait_for(timeout=12000)
                video_slot = page.locator('form[data-demo-id="lipsync"] .upload-slot:has([data-upload-id="video"])')
                url_input = video_slot.locator('.url-fetch .url-input').first
                list_id = url_input.get_attribute('list')
                assert list_id and 'https://video.example.com/cheer-wave.mp4' in [
                    o.get_attribute('value') for o in page.locator(f'#{list_id} option').all()
                ]
                video_slot.locator('[data-url-fetch="video"]').click()
                video_slot.locator('.demo-url-refuse').wait_for(timeout=5000)
                video_slot.locator('.demo-url-alias-btn').first.click()
                video_slot.locator('.demo-slot-preview[data-slot="video"]').wait_for(timeout=5000)
                assert '已從示範網址下載' in page.locator('.demo-toast').inner_text()
                assert video_slot.locator('[data-crawl-picker] option').count() <= 1
                assert 'fb_crawl' in video_slot.locator('[data-crawl-picker] .demo-picker-note').inner_text()
                assert '尚未登入 Google Drive' in video_slot.locator('[data-drive-picker]').inner_text()
                video_slot.locator('[data-drive-picker] button', has_text='登入 Google Drive').click()
                page.wait_for_function("() => document.querySelector('.demo-toast')?.textContent.includes('不連線 Google')", timeout=5000)

                # #/manage route, filters, session order, sorted results, live language switch & subject routes
                page.evaluate("() => { location.hash = '#/manage'; }")
                page.locator('.demo-manage-panel').wait_for(timeout=12000)
                assert page.locator('.demo-filter-btn[data-filter-kind="all"]').get_attribute('aria-pressed') == 'true'
                session_ids = [t.get_attribute('data-material-id') for t in page.locator('.demo-manage-section').nth(0).locator('.demo-tile').all()]
                assert session_ids == ['ph-wave', 'ph-cheer', 'ph-stadium']
                result_hrefs = [t.get_attribute('href') for t in page.locator('.demo-manage-section').nth(1).locator('.demo-tile').all()]
                assert len(result_hrefs) == 2 and any('#/studio/image-blend?case=ph-blend-case' in h for h in result_hrefs)
                page.locator('.demo-filter-btn[data-filter-kind="image"]').click()
                assert page.locator('.demo-filter-btn[data-filter-kind="image"]').get_attribute('aria-pressed') == 'true'
                assert [t.get_attribute('data-material-id') for t in page.locator('.demo-manage-section').nth(0).locator('.demo-tile').all()] == ['ph-cheer', 'ph-stadium']
                page.locator('.demo-filter-btn[data-filter-kind="all"]').click()
                page.locator('#languageSelect').select_option('en')
                page.wait_for_function("() => document.documentElement.lang === 'en' && document.querySelector('.demo-manage-title')?.textContent === 'Library'", timeout=5000)
                page.locator('#languageSelect').select_option('zh-Hant')
                page.wait_for_function("() => document.documentElement.lang === 'zh-Hant' && document.querySelector('.demo-manage-title')?.textContent === '素材庫'", timeout=5000)

                page.locator('.demo-manage-section').nth(1).locator('a.demo-tile[href*="ph-blend-case"]').click()
                page.locator('form[data-demo-id="image-blend"]').wait_for(timeout=12000)
                assert not page.locator('.missing-box').count()
                assert page.evaluate("() => window.WebComfyDemoDrive?.pendingRoute()?.caseId") == 'ph-blend-case'

                page.evaluate("() => { location.hash = '#/manage/docs-character-cheer'; }")
                page.locator('.demo-manage-panel').wait_for(timeout=12000)
                assert [t.get_attribute('data-material-id') for t in page.locator('.demo-manage-section').nth(0).locator('.demo-tile').all()] == ['ph-wave', 'ph-cheer']
                page.evaluate("() => { location.hash = '#/manage/other'; }")
                page.locator('.demo-manage-empty').wait_for(timeout=12000)
                assert page.locator('.demo-manage-section').nth(1).locator('.demo-tile').count() == 0

                # 390px mobile layout check
                page.set_viewport_size({'width': 390, 'height': 844})
                page.evaluate("() => { location.hash = '#/studio/img2img'; }")
                page.locator('form[data-demo-id="img2img"] label.dropzone').wait_for(timeout=12000)
                page.locator('form[data-demo-id="img2img"] label.dropzone').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                assert page.evaluate("() => getComputedStyle(document.querySelector('.demo-drive-folders')).flexDirection") == 'row'
                page.keyboard.press('Escape')

                assert page.evaluate("() => window.__u1aCounts") == {'createObjectURL': 0, 'fileReader': 0}
                assert not errors, errors
                assert not external, external
                assert not filechoosers, filechoosers
                assert not any('/api/' in path or path.startswith('POST ') for path in requests), requests
    finally:
        server.shutdown(); server.server_close()


@pytest.mark.parametrize('language', ['zh-Hant', 'en'])
@pytest.mark.parametrize('width', [390, 1440])
def test_compute_screens_are_canned_and_read_only(tmp_path, language, width):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler
    playwright = pytest.importorskip('playwright.sync_api')
    browser = shutil.which('chromium')
    if not browser:
        pytest.skip('Chromium not installed')
    output = tmp_path / 'Web_Comfyui'
    build(ROOT, output)
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()
        def do_POST(self):
            requests.append('POST ' + self.path)
            self.send_error(405)

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page = instance.new_page(viewport={'width': width, 'height': 1000})
                errors, external = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                def local_only(route):
                    if route.request.url.startswith(origin + '/'):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()
                page.route('**/*', local_only)
                page.goto(f'{origin}/Web_Comfyui/demo/?lang={language}')
                try:
                    page.locator('form[data-demo-id="text2img"] .compute-placement').wait_for()
                except playwright.TimeoutError:
                    pytest.fail(f'Compute chooser did not mount: {errors}; {page.locator("body").inner_text()[:2500]}')
                page.wait_for_function("document.documentElement.lang === " + json.dumps(language))
                page.locator('.compute-placement > .docs-compute-action').wait_for()
                page.evaluate("""() => {
                  window.computeReads = [];
                  const original = window.fetch;
                  window.fetch = (input, options) => {
                    computeReads.push(typeof input === 'string' ? input : input.url);
                    return original(input, options);
                  };
                }""")
                notice = ('Documentation site: screens only. Nothing connects, no keys are accepted and nothing is saved.'
                          if language == 'en' else '說明站只展示畫面：不會連線、不接受金鑰，也不會保存設定。')
                placeholder = ('Keys are not accepted on the documentation site' if language == 'en' else '說明站不接受金鑰')
                def check_screen(selector, name):
                    screen = page.locator(selector)
                    assert screen.locator(':scope > .docs-compute-notice').inner_text() == notice
                    for credential in screen.locator('input[type=password]').all():
                        assert credential.is_disabled()
                        assert credential.input_value() == ''
                        assert credential.get_attribute('placeholder') == placeholder
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    shots = os.environ.get('WEB_COMFYUI_TEST_SCREENSHOTS')
                    if shots:
                        target = Path(shots); target.mkdir(parents=True, exist_ok=True)
                        in_sheet = screen.evaluate("screen => {const sheet = screen.closest('[role=dialog]'); if (!sheet) return false; sheet.querySelector('.sheet-body').scrollTop = 0; return true;}")
                        page.screenshot(path=str(target / f'{name}-{language}-{width}.png'), full_page=not in_sheet)

                def menu(label):
                    # The ported desktop app keeps platforms/resources in Account,
                    # and Colab accounts in Management; mobile combines both.
                    selector = '#moreMenuBtn' if width < 768 else ('#managementMenuBtn' if label == 'Google Colab 帳號' else '#identityChip')
                    page.locator(selector).click()
                    translated = page.evaluate('(text) => WebComfyI18n.t(text)', label)
                    page.locator('.shell-menu-row').filter(has_text=translated).first.click()

                check_screen('.compute-placement', 'studio')
                menu('我的運算平台')
                cards = page.locator('.compute-settings [data-provider]')
                cards.nth(4).wait_for()
                for lane, label in [('local', '本機'), ('colab', 'Google Colab'), ('autoip', 'AutoIP'),
                                    ('comfy_cloud', 'Comfy Cloud'), ('runninghub', 'RunningHub')]:
                    heading = page.locator(f'.compute-settings [data-provider="{lane}"] h3')
                    heading.scroll_into_view_if_needed()
                    assert heading.is_visible()
                    assert heading.inner_text() == page.evaluate('(text) => WebComfyI18n.t(text)', label)
                page.locator('.console-exceptions summary').click()
                page.locator('.console-exceptions select').first.wait_for()
                assert page.locator('.console-exceptions select').first.locator('option').count() > 0
                assert '/api/compute/capabilities' in page.evaluate('computeReads')
                shots = os.environ.get('WEB_COMFYUI_TEST_SCREENSHOTS')
                if shots:
                    page.locator('.console-exceptions').screenshot(path=str(Path(shots) / f'platform-exceptions-{language}-{width}.png'))
                check_screen('.compute-settings', 'platforms')
                tabs = page.locator('.compute-settings > .console-actions button')
                if tabs.count() == 2:
                    tabs.nth(1).click()
                    page.locator('.compute-settings > div:last-child > h3').wait_for()
                    assert page.locator('.console-admin-row').count() == 0
                    assert '/api/compute/platform-admin' in page.evaluate('computeReads')
                    check_screen('.compute-settings', 'platform-admin')
                    tabs.nth(0).click()
                # A real mutation reaches only the 405 documentation adapter.
                page.locator('.compute-settings .console-test button').filter(has_text=page.evaluate("WebComfyI18n.t('儲存金鑰')")).first.click()
                page.wait_for_function("document.querySelector('.compute-settings .docs-compute-action')?.textContent.length > 0")
                assert page.evaluate("fetch('/api/compute/connections').then(r=>r.json()).then(d=>d.connections.every(c=>!c.credential_present))")
                page.locator('.sheet-close').click()
                page.locator('.compute-production-entry').click()
                page.locator('.compute-production form').wait_for()
                check_screen('.compute-production', 'production')
                menu('Google Colab 帳號')
                page.locator('.colab-content select').first.wait_for()
                assert page.locator('.colab-content select').first.locator('option').count() == 1
                assert page.locator('.colab-content select').first.input_value() == ''
                check_screen('.colab-content', 'colab')
                page.locator('.sheet-close').click()
                menu('角色創作資源中心')
                page.locator('castle-provider-fabric .provider-fabric__inner').wait_for()
                check_screen('castle-provider-fabric', 'resources')
                assert page.evaluate('WebComfyDocs.unknownRequests') == []
                assert errors == []
                assert external == []
                assert not any('/api/' in request or request.startswith('POST ') for request in requests)
    finally:
        server.shutdown(); server.server_close()
