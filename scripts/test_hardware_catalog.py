"""Planning estimates are separate from real GPU or provider execution."""
import copy
import json
from pathlib import Path
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from app_guide_catalog import catalog as app_catalog
from hardware_catalog import catalog, can_use

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def hardware():
    return catalog(app_catalog(ROOT))


def test_all_actual_features_and_profiles(hardware):
    assert len(hardware['features']) == 24  # 23 source forms plus character creation
    assert {p['id'] for p in hardware['profiles']} == {'none','vram-8','vram-12','vram-16','vram-24','vram-32','dual-24','autoip'}
    assert all(p['effective_vram_gb'] == max(p['cards_gb'],default=0) for p in hardware['profiles'])
    assert hardware['features']['text2img']['model_names'][0] == 'z_image_turbo_bf16.safetensors'
    assert hardware['features']['face-swap']['native_api_disabled']
    assert hardware['scope'] == 'static-demo-planning-only'


def test_thresholds_do_not_pool_cards(hardware):
    assert not can_use(hardware,'text2img','none')
    assert can_use(hardware,'voice-library','none')
    assert can_use(hardware,'character-create','none')
    assert not can_use(hardware,'image-blend','vram-12')
    assert can_use(hardware,'image-blend','vram-16')
    assert can_use(hardware,'text2img','vram-24')
    assert not can_use(hardware,'dance','vram-24')
    assert not can_use(hardware,'dance','dual-24')
    assert can_use(hardware,'dance','vram-32')
    for fid in hardware['features']:
        assert can_use(hardware,fid,'dual-24') == can_use(hardware,fid,'vram-24')
        assert can_use(hardware,fid,'autoip')
    assert not can_use(hardware,'unreviewed','autoip')
    assert not can_use(hardware,'text2img','unreviewed')


def test_pure_catalog_no_network_gpu_or_source_mutation(monkeypatch):
    source=app_catalog(ROOT);before=copy.deepcopy(source)
    def forbidden(*args,**kwargs):raise AssertionError('No network or hardware process permitted')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'run',forbidden)
    data=catalog(source)
    assert source==before
    assert all(f['provider_coverage']=='not-established-by-this-demo' for f in data['features'].values())
    source['features'].pop()
    with pytest.raises(ValueError,match='every actual feature'):catalog(source)


BROWSER=r'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<link rel="stylesheet" href="/webui/static/styles.css"><link rel="stylesheet" href="/webui/static/i18n.css"><link rel="stylesheet" href="/webui/docs/hardware.css">
<header id="appTopbar" class="topbar"><div class="brand">Web ComfyUI</div><nav class="view-nav"><button class="view-tab">角色</button><button class="view-tab">創作</button><button class="view-tab">專案</button><button class="view-tab">媒體庫</button><button class="view-tab">工作庫</button><button class="view-tab">資源</button></nav><div class="top-actions"><select id="languageSelect"><option value="zh-Hant">繁中</option><option value="en">English</option></select><button id="statusPill" class="status-pill">Tasks 0</button><button class="shell-menu-btn">Manage</button><button class="identity-chip"><span class="identity-name">Guide mode</span><span class="identity-role">Administrator</span> ▾</button></div></header>
<main><aside class="sidebar" style="width:240px"><div class="side-tools"><button id="tool" class="side-tool" data-hardware-feature="dance"><span class="side-tool-ico">•</span><span class="side-tool-name">AI 跳舞影片</span><span class="tool-dot ok"></span></button></div></aside><form data-demo-id="text2img"><input id="prompt" value="my prompt 文生圖"><input type="file" id="file"><button id="generate" data-demo-submit="true" class="generate-btn" type="submit">Generate</button><div class="app-demo" data-state="idle"></div></form></main>
<script>window.fetch=()=>{throw Error('No network calls allowed')};sessionStorage.clear();localStorage.setItem('webcomfy.language','zh-Hant');</script>
<script src="/webui/static/translations-en.js"></script><script src="/webui/static/i18n.js"></script><script src="/webui/docs/hardware.js"></script>
<script>
(async()=>{const result=document.createElement('pre');result.id='result';document.body.append(result);const check=(x,m)=>{if(!x)throw Error(m)},wait=()=>new Promise(r=>setTimeout(r,35));try{
 const h=WebComfyHardware;check(h.install({hardware:DATA}),'installed');await wait();
 check(innerWidth===Number(new URLSearchParams(location.search).get('width')),'real viewport');
 const form=document.querySelector('form'),generate=document.getElementById('generate'),input=document.getElementById('prompt');
 const file=document.getElementById('file'),files=new DataTransfer();files.items.add(new File(['local'],'reference.png'));file.files=files.files;
 check(h.profile==='vram-32'&&!generate.disabled,'default enabled');
 h.setProfile('none');await wait();check(generate.disabled,'no GPU blocks generation');check(document.getElementById('tool').classList.contains('hardware-unavailable'),'dim marker');
 check(!form.querySelector('.hardware-cloud-option').hidden&&form.querySelector('.hardware-cloud-option a').rel.includes('sponsored'),'blocked contextual referral');
 WebComfyI18n.setLanguage('en');await wait();
 const tool=document.getElementById('tool'),rect=tool.getBoundingClientRect();
 check(tool.scrollWidth<=tool.clientWidth+1,'sidebar card contained');
 for(const node of tool.children){if(!node.hidden)check(node.getBoundingClientRect().right<=rect.right+1,'card child within sidebar');}
 WebComfyI18n.setLanguage('zh-Hant');await wait();
 h.setProfile('vram-24');await wait();check(!generate.disabled&&h.canUse('text2img')&&!h.canUse('dance'),'24GB threshold');
 h.setProfile('dual-24');check(!h.canUse('dance'),'dual cards do not pool');
 check(form.querySelector('.hardware-reason').textContent.includes('48 GB'),'dual reason');
 form.querySelector('.app-demo').dataset.state='running';generate.disabled=true;h.setProfile('none');h.setProfile('autoip');check(generate.disabled,'running remains disabled');
 form.querySelector('.app-demo').dataset.state='idle';h.decorate('text2img',form);check(!generate.disabled,'finished demo restores allowed generation');
 for(const id of Object.keys(DATA.features))check(h.canUse(id),'cloud simulation coverage '+id);
 check(form.querySelector('.hardware-reason').textContent.includes('不扣點'),'cloud disclosure');
 WebComfyI18n.setLanguage('en');await wait();check(form.querySelector('.hardware-reason').textContent.includes('actual supported models'),'English provider qualification');
 check(input.value==='my prompt 文生圖'&&file.files[0].name==='reference.png','profile preserves prompts and files');
 check(form.querySelectorAll('.hardware-feature-status').length===1,'idempotent notice');
 check(sessionStorage.getItem('webcomfy.demo.hardware')==='autoip','preference saved locally');
 check(document.documentElement.scrollWidth<=innerWidth+1,'page overflow');const header=document.getElementById('appTopbar');check(header.scrollWidth<=header.clientWidth+1,'header overflow');
 check(!h.canUse('unknown')&&!h.setProfile('unknown'),'unknown fails closed');
 result.textContent='PASS';
 }catch(e){result.textContent='FAIL '+e.stack;}parent.postMessage({hardwareResult:result.textContent},location.origin);})();
</script></html>'''


@pytest.mark.parametrize('width',[390,768,1440])
def test_hardware_browser(tmp_path,hardware,width):
    browser=shutil.which('chromium')
    if not browser:pytest.skip('Chromium unavailable')
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            path=urlsplit(self.path).path;requests.append(path)
            if path=='/':
                data=('<!doctype html><html><body><pre id="result">WAIT</pre>'
                    f'<iframe style="width:{width}px;height:1000px;border:0" src="/case?width={width}"></iframe>'
                    '<script>addEventListener("message",e=>{if(e.origin===location.origin&&e.data.hardwareResult)document.getElementById("result").textContent=e.data.hardwareResult})</script></body></html>').encode();mime='text/html'
            elif path=='/case':data=BROWSER.replace('DATA',json.dumps(hardware,ensure_ascii=False)).encode();mime='text/html'
            elif path in ['/webui/static/styles.css','/webui/static/i18n.css','/webui/static/i18n.js','/webui/static/translations-en.js','/webui/docs/hardware.js','/webui/docs/hardware.css']:
                data=(ROOT/path.lstrip('/')).read_bytes();mime='text/css' if path.endswith('.css') else 'text/javascript'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        run=subprocess.run([browser,'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',f'--user-data-dir={tmp_path / "browser"}',f'--window-size={width},1000','--virtual-time-budget=9000','--dump-dom',f'http://127.0.0.1:{server.server_port}/'],capture_output=True,text=True,timeout=40)
        marker=run.stdout.split('id="result"')[-1].split('</pre>')[0]
        assert '>PASS' in marker,(marker[-2000:],run.stderr[-500:])
        assert not any('/api' in request for request in requests)
    finally:server.shutdown();server.server_close()
