"""Exercise the built guide with Chromium under the real Pages project subpath."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_docs import build, ROOT

ASSERT = r'''
(async () => {
 const result = document.createElement('pre'); result.id = 'docs-test-result'; document.body.append(result);
 const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
 const until = async fn => {for (let i = 0; i < 300; i++) {if(fn())return; await wait(20);}throw Error('timeout');};
 const assert = (value, message) => {if(!value)throw Error(message);};
 try {
  await until(() => document.querySelectorAll('.workflow-card').length);
  assert(document.documentElement.scrollWidth <= innerWidth + 1, 'horizontal overflow');
  for(const nav of document.querySelectorAll('[data-page]')) {
   const bounds = nav.getBoundingClientRect();
   assert(bounds.height >= 44 && bounds.width > 0, 'navigation visible and touch sized');
  }
  if(new URLSearchParams(location.search).has('screenshot')) {result.textContent = 'SCREENSHOT READY'; return;}
  const mode = document.querySelector('[aria-label="收錄狀態"]'); mode.value = 'local'; mode.dispatchEvent(new Event('change'));
  const cards = [...document.querySelectorAll('.workflow-card')]; assert(cards.length > 0, 'downloadable workflows');
  cards[0].click(); await until(() => document.querySelector('dialog[open]'));
  assert(location.hash.startsWith('#/workflow/'), 'shareable workflow link');
  const idHash = location.hash;
  const detail = document.querySelector('.workflow-details');
  for(const heading of ['先準備什麼','會怎麼跑','使用什麼技巧']) assert(detail.textContent.includes(heading), 'guide section '+heading);
  assert(!detail.textContent.includes('載入 ComfyUI') && !detail.textContent.includes('開啟 ComfyUI'), 'no runtime actions');
  assert(document.activeElement.classList.contains('sheet-close'), 'focus enters dialog');
  const download = [...detail.querySelectorAll('a')].find(a=>a.textContent==='下載 JSON');
  assert(download.pathname.startsWith('/Web_Comfyui/downloads/'), 'subpath download');
  const response = await fetch(download.href); assert(response.ok, 'download exists');
  const graph = await response.json(); assert(graph.nodes || Object.values(graph).some(v=>v?.class_type), 'actual workflow graph');
  document.querySelector('.sheet-close').click(); assert(!document.querySelector('dialog[open]'), 'close dialog');
  assert(document.activeElement.classList.contains('workflow-card'), 'focus restored');
  location.hash = idHash; await until(() => document.querySelector('dialog[open]'));
  document.querySelector('dialog').dispatchEvent(new Event('cancel', {cancelable:true}));
  assert(!document.querySelector('dialog[open]'), 'escape/cancel closes dialog');
  document.querySelector('[data-page="start"]').click(); await until(() => !document.getElementById('startPage').hidden);
  assert(document.getElementById('workflowPage').hidden, 'start route hides library');
  document.querySelector('[data-page="about"]').click(); await until(() => !document.getElementById('aboutPage').hidden);
  document.querySelector('[data-page="workflows"]').click(); await until(() => !document.getElementById('workflowPage').hidden);
  const search = document.querySelector('[aria-label="搜尋工作流"]');search.value='no-such-workflow-xyz';search.dispatchEvent(new Event('input'));
  assert(document.querySelectorAll('.workflow-card').length === 0, 'empty search');
  search.value='';search.dispatchEvent(new Event('input'));
  document.querySelector('.workflow-heading button').click();await until(()=>!document.querySelector('.workflow-heading button').disabled);
  assert(!document.querySelector('input[type="password"]'), 'no credential input');
  assert(document.documentElement.scrollWidth <= innerWidth + 1, 'final overflow');
  result.textContent = 'PASS';
 } catch(error) {result.textContent='FAIL '+error.message;}
})();
'''


def test_static_docs_browser(tmp_path):
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
            if path == '/Web_Comfyui/assert.js':
                data = ASSERT.encode(); mime = 'text/javascript'
            elif path.startswith('/Web_Comfyui/'):
                relative = path.removeprefix('/Web_Comfyui/') or 'index.html'
                file = output / relative
                if not file.resolve().is_relative_to(output.resolve()) or not file.is_file(): self.send_error(404); return
                data = file.read_bytes()
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json'}.get(file.suffix, 'text/plain')
                if file.name == 'index.html': data = data.replace(b'</body>', b'<script src="./assert.js"></script></body>')
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            requests.append('POST ' + self.path); self.send_error(405)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for width in (1440, 768, 390):
            run = subprocess.run([browser, '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', f'--user-data-dir={tmp_path / str(width)}', f'--window-size={width},900', '--virtual-time-budget=15000', '--dump-dom', f'http://127.0.0.1:{server.server_port}/Web_Comfyui/'], capture_output=True, text=True, timeout=45)
            marker = run.stdout.split('id="docs-test-result"')[-1].split('</pre>')[0]
            assert '>PASS' in marker, (width, marker[-1000:], run.stderr[-500:])
        assert not any('/api/' in path or path.startswith('POST ') for path in requests)
        assert any('/downloads/' in path for path in requests)
    finally:
        server.shutdown(); server.server_close()
