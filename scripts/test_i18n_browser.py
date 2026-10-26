"""Offline DOM language switching preserves forms, listeners and user content."""
import json
from pathlib import Path
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'webui/static'


def registry_strings():
    from app_guide_catalog import catalog
    data=catalog(ROOT)
    strings=set()
    keys={'title','label','subtitle','tagline','hint','minLabel','maxLabel','placeholder','note','versionLabel','sub'}
    def walk(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in keys and isinstance(v,str) and any('\u4e00'<=c<='\u9fff' for c in v):strings.add(v)
                elif isinstance(v,(dict,list)):walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(data['features']);walk(data['studios'])
    return sorted(strings)


HTML='''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><link rel="stylesheet" href="/static/i18n.css"></head><body>
<select id="authLanguageSelect"><option value="zh-Hant">繁體中文</option><option value="en">English</option></select><header><select id="languageSelect"><option value="zh-Hant">繁體中文</option><option value="en">English</option></select></header>
<nav><button id="nav">工作庫</button><button id="action"><span>生成</span><i id="icon">+</i></button></nav>
<form><label id="label">步數</label><input id="prompt" value="文生圖" placeholder="想生成什麼？"><input id="steps" type="number" value="8"><textarea id="textarea" placeholder="修改指令">工作庫</textarea><select id="aspect"><option value="portrait">直 9:16</option><option value="landscape">橫 16:9</option></select><select id="implicit"><option>工作庫</option></select></form>
<div id="dynamic"></div><p id="attr" title="模型需求" aria-label="開啟任務，2 個進行中">模型需求</p>
<p id="explicit" data-i18n-zh="節點圖說明" data-i18n-en="Node graph explanation">節點圖說明</p>
<div data-i18n-ignore><p id="userdata">文生圖</p></div><code id="code">工作庫</code><pre id="pre">角色</pre><p id="person" class="wall-card-name">文生圖</p><p id="echo" class="demo-input-label">工作庫</p><p id="output" class="post-text">生成</p><p id="filename" class="file-name">工作庫</p>
<script>
window.__network=[];window.fetch=(url)=>{__network.push(String(url));throw Error('unexpected network');};
window.__clicks=0;document.getElementById('action').onclick=()=>__clicks++;
const query=new URLSearchParams(location.search);
if(query.get('mode')==='docs'){
 const memory=new Map([['webcomfy.language','en']]);
 Object.defineProperty(window,'localStorage',{value:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,String(v)),removeItem:k=>memory.delete(k)}});
}else localStorage.setItem('webcomfy.language',query.get('initial')||'zh-Hant');
</script>
<script src="/static/translations-en.js"></script><script src="/static/i18n.js"></script>
<script>window.__registry=REGISTRY;</script>
<script>
(async()=>{
 const result=document.createElement('pre');result.id='i18n-test-result';document.body.append(result);
 const wait=()=>new Promise(r=>setTimeout(r,30));const assert=(value,msg)=>{if(!value)throw Error(msg);};
 const $=id=>document.getElementById(id);
 try {
  await wait();const i18n=window.WebComfyI18n;const initial=query.get('mode')==='docs'?'en':query.get('initial')||'zh-Hant';
  assert(innerWidth===390,'fixture viewport must be exactly 390 CSS pixels');assert(getComputedStyle($('languageSelect')).fontSize==='16px','mobile select text size');
  assert(i18n.language===initial,'initial language');assert(document.documentElement.lang===initial,'document lang');
  assert($('languageSelect').value===initial&&$('authLanguageSelect').value===initial,'selector sync');
  let events=0;addEventListener('webcomfy:language',()=>events++);
  i18n.setLanguage('en');await wait();
  assert($('nav').textContent==='Workflows','initial DOM translated');
  assert($('action').firstElementChild.textContent==='Generate','nested button label');
  const actionText=$('action').firstElementChild;const savedLabel=actionText.textContent;
  actionText.textContent='生成中…';await wait();actionText.textContent=savedLabel;await wait();
  $('action').click();assert(__clicks===1&&$('icon').textContent==='+','listeners and nested DOM preserved');
  assert($('prompt').value==='文生圖'&&$('textarea').value==='工作庫'&&$('steps').value==='8','form values untouched');
  assert($('prompt').placeholder==='What would you like to generate?','input placeholder');
  assert($('textarea').placeholder==='Edit instruction','textarea placeholder');
  assert($('aspect').value==='portrait'&&$('implicit').value==='工作庫','option values preserved');
  assert($('attr').getAttribute('aria-label')==='Open tasks, 2 active','number pattern');
  assert($('explicit').textContent==='Node graph explanation','explicit bilingual data');
  for(const [id,value] of [['userdata','文生圖'],['code','工作庫'],['pre','角色'],['person','文生圖'],['echo','工作庫'],['output','生成'],['filename','工作庫']])assert($(id).textContent===value,'user content changed: '+id);
  $('dynamic').innerHTML='<button id="late">建立實驗角色</button>';await wait();assert($('late').textContent==='Create an experimental character','late rendering');
  $('nav').textContent='資源';$('attr').setAttribute('title','節點需求');await wait();assert($('nav').textContent==='Resources','external text update');assert($('attr').title==='Required nodes','external attribute update');
  i18n.register({'新的介面標籤':'New interface label'});$('late').textContent='新的介面標籤';await wait();assert($('late').textContent==='New interface label','late dictionary');
  const missing=__registry.filter(text=>i18n.t(text)===text);assert(!missing.length,'registry untranslated: '+missing.join(' / '));
  assert(i18n.t('參考圖片 *')==='Reference image *','required field label');
  assert(i18n.t('圖片 1 · <image1>（原圖） *')==='Image 1 · <image1> (original) *','required numbered label');
  assert(i18n.t('還沒有「文生圖」的輸出。').startsWith('No output from'),'known feature empty state');
  assert(i18n.t('還沒有「自訂使用者名字」的輸出。')==='還沒有「自訂使用者名字」的輸出。','unknown composed content preserved');
  assert(i18n.t('自訂使用者名字 *')==='自訂使用者名字 *','unknown suffix preserved');
  assert(i18n.t('段落 12')==='Beat 12','dynamic project beat');
  assert(i18n.t('這是使用者自己寫的內容')==='這是使用者自己寫的內容','unknown prose must stay unchanged');
  $('authLanguageSelect').value='zh-Hant';$('authLanguageSelect').dispatchEvent(new Event('change'));await wait();
  assert($('languageSelect').value==='zh-Hant','both language selectors synchronized');
  assert(i18n.language==='zh-Hant'&&document.documentElement.lang==='zh-Hant','language event');
  assert($('nav').textContent==='資源'&&$('attr').title==='節點需求','new originals restored');
  assert(actionText.textContent==='生成','async restored UI label remains reversible');
  assert($('explicit').textContent==='節點圖說明'&&$('prompt').placeholder==='想生成什麼？','original text restored');
  assert(localStorage.getItem('webcomfy.language')==='zh-Hant','preference persisted');
  assert(events>0,'language event dispatched');assert(__network.length===0,'offline only');
  for(let n=0;n<5;n++){i18n.setLanguage('en');i18n.setLanguage('zh-Hant');}await wait();
  assert($('nav').textContent==='資源'&&$('prompt').value==='文生圖','repeated switch stable');
  result.textContent='PASS';
 }catch(e){result.textContent='FAIL '+e.message;}
 parent.postMessage({i18nResult:result.textContent},location.origin);
})();
</script></body></html>'''


@pytest.mark.parametrize('mode,initial',[('local','en'),('local','zh-Hant'),('docs','en')])
def test_language_browser(tmp_path,mode,initial):
    playwright = pytest.importorskip('playwright.sync_api')
    registry=registry_strings()
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            path=urlsplit(self.path).path;requests.append(path)
            if path=='/':
                # Headless Chromium may clamp its outer viewport to 500px. An
                # explicit same-origin frame gives a real 390px CSS viewport.
                query=urlsplit(self.path).query
                data=('<!doctype html><html><body><pre id="i18n-test-result">WAIT</pre>'
                    '<iframe style="width:390px;height:900px;border:0" src="/case?'+query+'"></iframe>'
                    '<script>addEventListener("message",e=>{if(e.origin===location.origin&&e.data.i18nResult)'
                    'document.getElementById("i18n-test-result").textContent=e.data.i18nResult})</script>'
                    '</body></html>').encode();mime='text/html'
            elif path=='/case':data=HTML.replace('REGISTRY',json.dumps(registry,ensure_ascii=False)).encode();mime='text/html'
            elif path in ['/static/i18n.js','/static/translations-en.js','/static/i18n.css']:
                data=(STATIC/Path(path).name).read_bytes();mime='text/css' if path.endswith('.css') else 'text/javascript'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch_persistent_context(
                str(tmp_path / 'browser'),
                executable_path=shutil.which('google-chrome') or shutil.which('chromium'),
                viewport={'width': 390, 'height': 900},
                args=['--no-sandbox', '--disable-dev-shm-usage'],
            ) as context:
                page = context.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}/?mode={mode}&initial={initial}')
                page.wait_for_function("document.getElementById('i18n-test-result').textContent !== 'WAIT'", timeout=15000)
                assert page.locator('#i18n-test-result').inner_text() == 'PASS'
        assert all(path in {'/','/case','/favicon.ico','/static/i18n.js','/static/translations-en.js','/static/i18n.css'} for path in requests)
    finally:server.shutdown();server.server_close()
