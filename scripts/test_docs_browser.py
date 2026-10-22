"""Exercise the built guide with Chromium under the real Pages project subpath."""
import json
from pathlib import Path
import shutil
import subprocess
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
 const until = async fn => {for (let i = 0; i < 300; i++) {if(fn())return; await wait(20);}throw Error('timeout');};
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
  assert(document.documentElement.scrollWidth <= innerWidth + 1, 'horizontal overflow');
  assert(document.querySelector('.gen-form textarea'), 'actual app generation form');
  assert(document.querySelector('.topbar .brand').textContent === 'Web ComfyUI', 'actual app brand');
  assert(visible('[data-view="characters"]') && visible('[data-view="studio"]') && visible('[data-view="workflows"]'), 'same app navigation');
  assert(document.getElementById('servicePill').textContent==='互動說明','no fake live service');
  if(new URLSearchParams(location.search).has('screenshot')) {result.textContent = 'SCREENSHOT READY'; return;}
  if (!new URLSearchParams(location.search).has('slowguide')) document.querySelector('.app-guide-button button').click();
  await until(()=>document.querySelector('.app-feature-guide'));
  let detail = document.querySelector('.app-feature-guide');
  for(const text of ['z_image_turbo_bf16.safetensors','qwen_3_4b.safetensors','ae.safetensors','zimage_prompt','會怎麼跑','使用什麼技巧']) assert(detail.textContent.includes(text),'actual text2img source '+text);
  assert(!detail.textContent.includes('sd_xl_base_1.0'),'unrelated checkpoint not attributed');
  await close();
  document.querySelector('.generate-btn').click();await until(()=>document.querySelector('.app-feature-guide'));await close();
  await feature('AI 指令改圖');
  detail=document.querySelector('.app-feature-guide');assert(detail.textContent.includes('flux1-dev-kontext_fp8_scaled.safetensors'),'actual editing checkpoint');await close();
  assert(location.hash.includes('img2img'), 'feature route matches selected button');
  const resourceTab=visible('[data-view="resources"]');
  if(resourceTab) resourceTab.click();
  else {document.getElementById('moreMenuBtn').click();await until(()=>document.querySelector('.shell-menu-row'));[...document.querySelectorAll('.shell-menu-row')].find(row=>row.textContent==='角色創作資源中心').click();}
  await until(()=>document.querySelector('.provider-fabric-host'));
  assert([...document.querySelectorAll('input[type="password"]')].every(node=>node.disabled),'credentials disabled');
  visible('[data-view="characters"]').click();await until(()=>document.body.dataset.view==='characters');
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
  const blocked=await fetch('/api/generate',{method:'POST',body:'PRIVATE_TEST_INPUT'});assert(blocked.status===405,'writes blocked in memory');
  const external=await fetch('https://example.invalid/api');assert(external.status===405,'external calls blocked');
  assert((await fetch('/api/unlisted')).status===404,'unknown endpoint fails closed');
  assert(document.documentElement.scrollWidth<=innerWidth+1,'final overflow');
  assert(window.__docsErrors.length===0,window.__docsErrors.join(';'));
  result.textContent='PASS';
 }catch(error){result.textContent='FAIL '+error.message;}
})();
'''


@pytest.mark.parametrize('direct_guide', [False, True])
def test_static_docs_browser(tmp_path, direct_guide):
    browser = shutil.which('chromium')
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
                relative = path.removeprefix('/Web_Comfyui/') or 'index.html'
                file = output / relative
                if not file.resolve().is_relative_to(output.resolve()) or not file.is_file(): self.send_error(404); return
                data = file.read_bytes()
                if direct_guide and file.name == 'app.js': time.sleep(0.5)
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json'}.get(file.suffix, 'text/plain')
                if file.name == 'index.html': data = data.replace(b'<head>', b'<head><script src="./monitor.js"></script>').replace(b'</body>', b'<script src="./assert.js"></script></body>')
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            requests.append('POST ' + self.path); self.send_error(405)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for width in ((390,) if direct_guide else (1440, 768, 390)):
            route = '?slowguide#/studio/text2img?guide=1' if direct_guide else ''
            run = subprocess.run([browser, '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', f'--user-data-dir={tmp_path / str(width)}', f'--window-size={width},900', '--virtual-time-budget=15000', '--dump-dom', f'http://127.0.0.1:{server.server_port}/Web_Comfyui/{route}'], capture_output=True, text=True, timeout=45)
            marker = run.stdout.split('id="docs-test-result"')[-1].split('</pre>')[0]
            assert '>PASS' in marker, (width, marker[-1000:], run.stderr[-500:])
        assert not any('/api/' in path or path.startswith('POST ') for path in requests)
        assert any('/downloads/' in path for path in requests)
    finally:
        server.shutdown(); server.server_close()
