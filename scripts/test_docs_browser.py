"""Exercise the built guide with Chromium under the real Pages project subpath."""
import json
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
 const visible = selector => [...document.querySelectorAll(selector)].find(node => node.getBoundingClientRect().width > 0);
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
  document.querySelector('.generate-btn').click();await until(()=>document.querySelector('.app-demo')?.dataset.state==='complete');
  assert(!document.querySelector('.demo-output').hidden && !document.querySelector('[role="dialog"]'),'result status appears in original form');
  if(WebComfyTour.demoFor('text2img').output.kind==='unavailable') assert(!document.querySelector('.demo-output img'),'untested text2img has no borrowed image');
  else await until(()=>document.querySelector('.demo-output img')?.naturalWidth>0);
  await feature('AI 指令改圖');
  detail=document.querySelector('.app-feature-guide');assert(detail.textContent.includes('flux1-dev-kontext_fp8_scaled.safetensors'),'actual editing checkpoint');await close();
  assert(location.hash.includes('img2img'), 'feature route matches selected button');
  await until(()=>document.querySelector('[data-demo-id="img2img"]'));
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
  const transfer=new DataTransfer();transfer.items.add(new File(['synthetic local input'],'local-example.png',{type:'image/png'}));upload.files=transfer.files;upload.dispatchEvent(new Event('change',{bubbles:true}));
  assert(document.querySelector('.demo-input-preview img').src.startsWith('blob:'),'selected file preview remains local');
  const revoked=[],originalRevoke=URL.revokeObjectURL;URL.revokeObjectURL=url=>{revoked.push(url);originalRevoke.call(URL,url);};
  upload.dispatchEvent(new Event('change',{bubbles:true}));assert(revoked.length===1,'old preview URL released');
  const drop=new Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:transfer});upload.closest('label').dispatchEvent(drop);assert(revoked.length===2,'drag and drop replaces local preview');
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
  document.querySelector('.experimental-create-form button[type=submit]').click();await until(()=>document.querySelector('.app-demo')?.dataset.state==='complete');
  assert(document.querySelector('.demo-character img'),'successful character card');
  await until(()=>document.querySelector('.demo-character img').naturalWidth>0);
  assert(document.querySelector('.demo-output h4').textContent==='示範角色建立完成','character success label');
  assert(document.querySelector('.demo-character-card h5').textContent==='晴晴','prefilled name on card');
  assert(document.querySelectorAll('.demo-character-card a[href^="#/studio/"]').length===3,'next workflow demo links');
  const characterForm=document.querySelector('.experimental-create-form');
  characterForm.elements.name.value='<img src=x onerror=alert(1)> 文生圖';
  characterForm.elements.description.value='我的角色描述，換成紅色隊服。';
  const canvas=document.createElement('canvas');canvas.width=32;canvas.height=48;canvas.getContext('2d').fillRect(0,0,32,48);
  const photoFile=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
  const photos=new DataTransfer();photos.items.add(new File([photoFile],'my-character.png',{type:'image/png'}));
  characterForm.elements.photos.files=photos.files;characterForm.elements.photos.dispatchEvent(new Event('change',{bubbles:true}));
  assert(document.querySelector('.app-demo').hidden,'photo replacement clears previous card');
  characterForm.querySelector('button[type=submit]').click();await until(()=>document.querySelector('.app-demo')?.dataset.state==='complete');
  assert(document.querySelector('.demo-character-card h5').textContent===characterForm.elements.name.value&&!document.querySelector('.demo-character-card h5 img'),'name is safe literal text');
  assert(document.querySelector('.demo-character-card .demo-result-text').textContent===characterForm.elements.description.value,'custom description on card');
  const cardPhoto=document.querySelector('.demo-character-card img');
  assert(cardPhoto.src.startsWith('blob:')&&cardPhoto.src!==document.querySelector('.demo-input-preview img').src,'card owns a separate local photo URL');
  await until(()=>cardPhoto.naturalWidth>0);
  const lang=document.getElementById('languageSelect');lang.value='en';lang.dispatchEvent(new Event('change'));
  await until(()=>document.documentElement.lang==='en' && visible('[data-view="characters"]').textContent.includes('Characters'));
  assert(document.querySelector('.experimental-create-form button[type=submit]').textContent.includes('demo'),'English native demo action');
  assert(document.querySelector('.demo-output h4').textContent==='Demo character created','English character completion');
  assert(document.querySelector('.demo-character-card h5').textContent===characterForm.elements.name.value,'language preserves personal name');
  const cardBounds=document.querySelector('.demo-character-card').getBoundingClientRect();
  assert(cardBounds.width>characterForm.getBoundingClientRect().width*0.7,'character result spans form columns');
  for(const link of document.querySelectorAll('.demo-character-card a')) assert(link.getBoundingClientRect().right<=cardBounds.right,'next action fits inside character card');
  const cardURL=cardPhoto.src;
  characterForm.querySelector('.demo-prefill-bar button').click();
  assert(revoked.includes(cardURL)&&document.querySelector('.app-demo').hidden&&!document.querySelector('.demo-character-card'),'reset clears card and releases its photo');
  assert(document.documentElement.scrollWidth<=innerWidth+1,'English hardware header overflow');
  assert(document.querySelector('.topbar .brand').getBoundingClientRect().width>70,'English brand remains visible');
  location.hash='#/studio/img2img';await until(()=>document.querySelector('form[data-demo-id="img2img"]'));
  const englishForm=document.querySelector('form[data-demo-id="img2img"]');
  const edited=englishForm.querySelector('textarea');edited.value='My unchanged prompt 文生圖';edited.dispatchEvent(new Event('input',{bubbles:true}));
  const keptFile=englishForm.querySelector('input[type=file]');keptFile.files=transfer.files;keptFile.dispatchEvent(new Event('change',{bubbles:true}));
  lang.value='zh-Hant';lang.dispatchEvent(new Event('change'));await until(()=>document.documentElement.lang==='zh-Hant');
  await until(()=>/[\u4e00-\u9fff]/.test(englishForm.querySelector('.app-demo h3').textContent));
  assert(edited.value==='My unchanged prompt 文生圖'&&keptFile.files[0].name==='local-example.png','language preserves user prompt and file');
  const revokedBeforeReset=revoked.length;englishForm.querySelector('.demo-prefill-bar button').click();
  assert(!keptFile.value&&revoked.length===revokedBeforeReset+1,'reset inputs clears file and revokes preview');
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
    if(example.output.kind==='unavailable') assert(!form.querySelector('.demo-output img,.demo-output video'),'untested tool has no unrelated media '+entry.id);
    if(example.evidence?.kind==='local-comfyui') {
      for(const source of example.inputs || []) if(source.slot) {
        const preview=form.querySelector('[data-upload-id="'+source.slot+'"]')?.closest('.upload-slot')?.querySelector('.demo-input-preview img,.demo-input-preview video') || form.querySelector('[data-upload-id="'+source.slot+'"] .demo-input-preview img,[data-upload-id="'+source.slot+'"] .demo-input-preview video');
        assert(preview?.src===source.src,'actual upload slot uses recorded reference '+entry.id+' '+source.slot);
      }
      assert(form.querySelector('.demo-run-record').textContent.includes(example.evidence.prompt),'exact tested prompt '+entry.id);
      for(const field of entry.fields || []) if(field.type==='aspect' && example.prefill?.[field.id]) assert(form.querySelector('[data-field-id="'+field.id+'"] button.active')?.dataset.value===example.prefill[field.id],'recorded dimensions selected '+entry.id);
      assert(form.querySelector('.demo-output h4').textContent.includes('GPU'),'recorded GPU result label '+entry.id);
      for(const image of form.querySelectorAll('.demo-output img')) await until(()=>image.naturalWidth>0);
      const video=form.querySelector('.demo-output video');
      if(video) {video.preload='metadata';video.load();await until(()=>video.readyState>=1);assert(video.duration>0,'recorded video metadata '+entry.id);video.muted=true;await video.play();await until(()=>video.currentTime>0.1&&video.readyState>=2);video.pause();}
    }
  }
  window.matchMedia=originalMatchMedia;
  const hardware=document.getElementById('hardwareSelect');assert(hardware,'hardware selector');
  const chooseHardware=async id=>{hardware.value=id;hardware.dispatchEvent(new Event('change'));await wait(60);};
  location.hash='#/studio/text2img';await until(()=>document.querySelector('form[data-demo-id="text2img"]'));
  const hardwareForm=document.querySelector('form[data-demo-id="text2img"]');
  const hardwarePrompt=hardwareForm.querySelector('textarea');hardwarePrompt.value='Preserve this hardware-switch input';
  await chooseHardware('none');
  assert(!WebComfyHardware.canUse('text2img')&&hardwareForm.querySelector('.generate-btn').disabled,'no GPU disables generation');
  assert(await WebComfyTour.start(hardwareForm)===false,'direct generation also blocked');
  if(innerWidth>=1280)assert(document.getElementById('studio-side-tool-text2img').getAttribute('aria-disabled')==='true','sidebar indicates unavailable workflow');
  if(innerWidth>=1280)for(const card of document.querySelectorAll('.side-tool'))for(const child of card.children)assert(!child.getBoundingClientRect().width||child.getBoundingClientRect().right<=card.getBoundingClientRect().right+1,'hardware status stays inside sidebar');
  await chooseHardware('vram-24');assert(WebComfyHardware.canUse('text2img')&&!WebComfyHardware.canUse('dance'),'24 GB planning threshold');
  await chooseHardware('dual-24');assert(!WebComfyHardware.canUse('dance'),'dual GPUs do not pool VRAM');
  await chooseHardware('autoip');assert(features.every(entry=>WebComfyHardware.canUse(entry.id)),'AutoIP opens every demonstration');
  assert(!hardwareForm.querySelector('.generate-btn').disabled&&hardwarePrompt.value==='Preserve this hardware-switch input','mode switch restores action and preserves inputs');
  await chooseHardware('vram-32');
  const blocked=await fetch('/api/generate',{method:'POST',body:'PRIVATE_TEST_INPUT'});assert(blocked.status===405,'writes blocked in memory');
  const external=await fetch('https://example.invalid/api');assert(external.status===405,'external calls blocked');
  assert((await fetch('/api/unlisted')).status===404,'unknown endpoint fails closed');
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
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.webp': 'image/webp', '.mp4': 'video/mp4'}.get(file.suffix, 'text/plain')
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
                        for _ in range(600):
                            if page.locator('#docs-test-result').count() and page.locator('#docs-test-result').inner_text().startswith(('PASS', 'FAIL')):
                                break
                            page.wait_for_timeout(100)
                        marker = page.locator('#docs-test-result').inner_text()
                        assert marker == 'PASS', (width, marker)
        assert not any('/api/' in path or path.startswith('POST ') for path in requests)
        assert any('/downloads/' in path for path in requests)
    finally:
        server.shutdown(); server.server_close()
