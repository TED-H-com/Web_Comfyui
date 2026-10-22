"""Offline native-browser coverage for the docs graph and inline simulation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import subprocess
import threading
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
const g={kind:'comfyui',evidence:'依照來源節點繪製，沒有執行 GPU。',evidence_en:'Built from the actual API graph; no GPU run.',source:'webui/server.py#build_graph',notes:['可選節點尚未加入。'],notes_en:['Optional nodes are not included.'],nodes:[
{id:'1',type:'UNETLoader',inputs:{unet_name:'z_image_turbo_bf16.safetensors'}},
{id:'2',type:'CLIPTextEncode',inputs:{text:'<img src=x onerror=alert(1)>',clip:['3',0]}},
{id:'3',type:'CLIPLoader',inputs:{clip_name:'qwen_3_4b.safetensors'}},
{id:'4',type:'KSampler',inputs:{steps:8,cfg:1,sampler_name:'res_multistep'}},
{id:'5',type:'VAEDecode',inputs:{vae:['6',0]}},
{id:'6',type:'VAELoader',inputs:{vae_name:'ae.safetensors'}}],links:[
{from:'1',to:'4',from_slot:'MODEL',to_slot:'model'},
{from:'3',to:'2',from_slot:'CLIP',to_slot:'clip'},
{from:'2',to:'4',from_slot:'CONDITIONING',to_slot:'positive'},
{from:'4',to:'5',from_slot:'LATENT',to_slot:'samples'},
{from:'6',to:'5',from_slot:'VAE',to_slot:'vae'}]};
const demo={id:'example',title:'文生圖範例',title_en:'Image example',prompt:'A quiet garden',steps:['讀取範例條件','展示採樣階段','展示解碼階段','開啟準備好的結果'],steps_en:['Read example conditions','Show sampling stage','Show decoding stage','Open prepared result'],output:{kind:'text',text:'固定準備的結果',text_en:'Prepared fixed output'},note:'Offline example.'};
const graph=WebComfyExplorers.graphContent(g);document.querySelector('main').append(graph);
const inline=WebComfyExplorers.demoContent(demo,{inline:true,hidePrompt:true});document.querySelector('main').append(inline);
const process=WebComfyExplorers.graphContent({kind:'process',nodes:[{id:'create',type:'HTTP_POST',title:'建立角色資料',title_en:'Create character record',inputs:{name:'文生圖',file:'參考.png'}}],links:[]});document.querySelector('main').append(process);
window.fixture={graph,inline,demo,process};
'''
CHECK = r'''
(async()=>{
 const result=document.createElement('pre');result.id='test-result';document.body.append(result);
 const check=(value,msg)=>{if(!value)throw Error(msg);};
 const pause=ms=>new Promise(r=>setTimeout(r,ms));
 try{
  await pause(40);check(innerWidth===Number(new URLSearchParams(location.search).get('width')),'true fixture viewport');
  check(document.documentElement.scrollWidth<=innerWidth+1,'page horizontal overflow');
  check(fixture.graph.querySelectorAll('.graph-node').length===6,'all actual nodes');
  check(fixture.graph.querySelectorAll('svg path').length===5,'all actual edges');
  fixture.graph.querySelector('[data-node-id="2"]').click();
  check(fixture.graph.querySelector('.graph-inspector').textContent.includes('<img src=x'),'parameters rendered as text');
  check(!fixture.graph.querySelector('.graph-inspector img'),'no HTML injection');
  fixture.graph.querySelector('[data-node-id="4"]').click();await pause(20);check(fixture.graph.querySelector('.graph-inspector h3').textContent==='KSampler · #4','selected title survives observer');
  const viewport=fixture.graph.querySelector('.graph-viewport'), old=fixture.graph.querySelector('output').textContent;
  viewport.dispatchEvent(new KeyboardEvent('keydown',{key:'+',bubbles:true}));
  check(fixture.graph.querySelector('output').textContent!==old,'keyboard zoom');
  check(fixture.inline.dataset.docsInteractive==='true'&&!fixture.inline.querySelector('textarea'),'inline form integration');
  const pending=fixture.inline.demo.start('<'+'script>unsafe()<'+'/script>');
  check(fixture.inline.dataset.state==='running','running state');
  check(await pending,'finite completed animation');
  check(fixture.inline.dataset.state==='complete','completion state');
  check(fixture.inline.textContent.includes('固定準備的結果'),'prepared output');
  check(fixture.inline.querySelector('.demo-prompt-echo').textContent.includes('<script>unsafe()'),'safe input echo');
  check(!fixture.inline.querySelector('script'),'prompt not executable');
  check(fixture.inline.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')==='100','accessible progress');
  WebComfyI18n.setLanguage('en');await pause(20);
  check(fixture.graph.querySelector('.explorer-evidence').textContent.includes('Built from the actual API graph'),'English graph evidence');
  check(fixture.graph.querySelector('.explorer-notes').textContent==='Optional nodes are not included.','English graph notes');
  check(fixture.graph.querySelector('.graph-source').textContent==='Source: webui/server.py#build_graph','source prefix and exact file');
  check(fixture.graph.querySelector('.graph-inspector h3').textContent==='KSampler · #4','selected title never resets');
  check(fixture.graph.querySelector('.graph-viewport').getAttribute('aria-label').startsWith('Scrollable node canvas'),'English graph aria');
  check(fixture.graph.querySelector('[data-node-id="4"]').getAttribute('aria-label').includes('inspect parameters'),'English node aria');
  check(fixture.process.querySelector('.graph-node strong').textContent==='Create character record','English process title');
  check(fixture.process.querySelector('.graph-inspector h3').textContent==='Create character record · #create','English selected process title');
  check(fixture.process.querySelector('.graph-parameters').textContent.includes('文生圖')&&fixture.process.querySelector('.graph-parameters').textContent.includes('參考.png'),'process values preserved');
  WebComfyI18n.setLanguage('zh-Hant');await pause(20);
  check(fixture.graph.querySelector('.explorer-notes').textContent==='可選節點尚未加入。','graph notes restored');
  check(fixture.process.querySelector('.graph-inspector h3').textContent==='建立角色資料 · #create','selected process restored');
  check(fixture.graph.querySelector('.graph-viewport').getAttribute('aria-label').startsWith('節點畫布'),'graph aria restored');
  WebComfyI18n.setLanguage('en');await pause(20);
  check(fixture.inline.textContent.includes('Prepared fixed output'),'English result');
  check(fixture.inline.textContent.includes('Prepared examples, not generated from your input'),'English disclosure');
  fixture.inline.demo.reset();check(fixture.inline.dataset.state==='idle'&&fixture.inline.querySelector('.demo-output').hidden,'reset');
  const cancelled=fixture.inline.demo.start('cancel me');fixture.inline.remove();check(!(await cancelled),'cancel on removal resolves');
  const unsafe=WebComfyExplorers.demoContent({...fixture.demo,output:{kind:'image',images:[{src:'https://external.invalid/private.png',alt:'external'}]}});document.body.append(unsafe);
  const reduced=matchMedia;window.matchMedia=()=>({matches:true});check(await unsafe.demo.start('reduced motion'),'reduced motion completes');window.matchMedia=reduced;
  check(!unsafe.querySelector('img'),'external assets rejected');unsafe.demo.dispose();unsafe.remove();
  check(!window.errors.length,window.errors.join('\n'));result.textContent='PASS';
 }catch(e){result.textContent='FAIL '+e.stack;}
 parent.postMessage({explorerResult:result.textContent},location.origin);
})();
'''


def html(check=True):
    return ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="stylesheet" href="/webui/static/styles.css"><link rel="stylesheet" href="/webui/docs/explorers.css">'
            '<style>body{margin:0;overflow:auto;background:var(--bg)}main{max-width:1100px;margin:24px auto;background:var(--panel)}@media(max-width:767px){main{margin:0}}</style>'
            '<main></main><script>window.errors=[];addEventListener("error",e=>errors.push(e.message));window.fetch=()=>{throw Error("no fetch")};</script>'
            '<script>localStorage.setItem("webcomfy.language","zh-Hant");</script><script src="/webui/static/translations-en.js"></script><script src="/webui/static/i18n.js"></script><script src="/webui/docs/explorers.js"></script><script>'+FIXTURE+(CHECK if check else '')+'</script></html>').encode()


@pytest.fixture
def explorer_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            path=self.path.split('?')[0]
            if path=='/':
                from urllib.parse import parse_qs, urlsplit
                width=int(parse_qs(urlsplit(self.path).query).get('width',['390'])[0])
                data=('<!doctype html><html><body style="margin:0"><pre id="test-result">WAIT</pre>'
                    f'<iframe style="width:{width}px;height:1000px;border:0" src="/case?width={width}"></iframe>'
                    '<script>addEventListener("message",e=>{if(e.origin===location.origin&&e.data.explorerResult)'
                    'document.getElementById("test-result").textContent=e.data.explorerResult})</script></body></html>').encode();mime='text/html'
            elif path in ('/case','/preview'):
                data=html(path=='/case');mime='text/html'
            elif path in ('/webui/static/styles.css','/webui/docs/explorers.css','/webui/docs/explorers.js','/webui/static/i18n.js','/webui/static/translations-en.js'):
                file=ROOT/path.lstrip('/');data=file.read_bytes();mime='text/css' if file.suffix=='.css' else 'text/javascript'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close()


@pytest.mark.parametrize('width',[390,768,1440])
def test_explorers_browser(tmp_path, explorer_server, width):
    browser=shutil.which('chromium')
    if not browser:pytest.skip('Chromium unavailable')
    result=subprocess.run([browser,'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',f'--user-data-dir={tmp_path / "browser"}',f'--window-size={width},1000','--virtual-time-budget=15000','--dump-dom',explorer_server+f'/?width={width}'],capture_output=True,text=True,timeout=40)
    marker=result.stdout.split('id="test-result"')[-1].split('</pre>')[0]
    assert '>PASS' in marker,(marker[-1600:],result.stderr[-500:])
