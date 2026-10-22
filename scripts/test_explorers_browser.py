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
  const recorded=WebComfyExplorers.demoContent({...fixture.demo,inputs:[{src:'/input.webp',label:'原圖',label_en:'Original'},{kind:'video',src:'/motion.mp4',poster:'/poster.webp',label:'動作影片',label_en:'Motion video'}],evidence:{kind:'local-comfyui',run_id:'run-42',prompt:'Exact GPU prompt',models:['model.safetensors'],seed:42,steps:8,width:512,height:512},output:{kind:'image',images:[{src:'/result.webp',alt:'Generated result'}]}});document.body.append(recorded);
  check(recorded.querySelector('.demo-references img').src.endsWith('/input.webp'),'original visible before run');
  check(recorded.querySelector('.demo-output').hidden,'output separate and initially hidden');
  check(recorded.querySelector('.demo-references video').controls&&!recorded.querySelector('.demo-references video').autoplay,'video reference controls without autoplay');
  check(recorded.querySelector('.demo-run-record').textContent.includes('Exact GPU prompt'),'exact tested prompt visible');
  let completed=0;recorded.addEventListener('demo-complete',()=>completed++);
  const playback=recorded.demo.start('changed user prompt');
  check(recorded.demo.start('duplicate')===playback,'double start returns the current run');
  check(recorded.querySelector('.demo-output').hidden,'recorded output hidden while running');
  check(!recorded.querySelector('.demo-progress').hidden,'recorded progress visible');
  check(recorded.querySelector('.demo-progress-label').textContent.includes('Simulated progress'),'simulation explicitly labeled');
  await pause(550);
  const percent=Number(recorded.querySelector('.demo-progress').getAttribute('aria-valuenow'));
  check(percent>0&&percent<100&&recorded.querySelector('.demo-output').hidden,'recorded progress advances before output');
  check(recorded.querySelector('.demo-form button').disabled,'run disabled during playback');
  check(!recorded.querySelector('.demo-stages .done,.demo-stages .active'),'no pretend GPU stage completion');
  check(await playback,'recorded replay completes');
  check(completed===1&&!recorded.querySelector('.demo-form button').disabled,'single completion restores button');
  check(getComputedStyle(recorded.querySelector('.demo-progress span')).transitionDuration==='0s','completed result does not precede visible full progress');
  check(recorded.querySelector('.demo-output h4').textContent.includes('Recorded GPU result'),'verified result labeled');
  check(recorded.querySelector('.demo-output img').src.endsWith('/result.webp'),'result distinct from original');
  check(!recorded.querySelector('.demo-stages .done,.demo-stages .active')&&recorded.querySelector('.demo-progress').getAttribute('aria-valuenow')==='100','recorded workflow is a reference, not simulated execution');
  check(recorded.querySelector('.demo-run-record').textContent.includes('Exact GPU prompt'),'user edits do not rewrite run evidence');
  WebComfyI18n.setLanguage('zh-Hant');await pause(20);
  check(recorded.querySelector('.demo-output h4').textContent.includes('GPU 實測成果'),'verified result Chinese');
  const missing=WebComfyExplorers.demoContent({...fixture.demo,output:{kind:'unavailable',reason:'尚待測試',reason_en:'Awaiting testing',images:[{src:'/unrelated.webp'}]}});document.body.append(missing);await missing.demo.start('anything');
  check(!missing.querySelector('.demo-output img, .demo-output video'),'unavailable never shows unrelated media');
  check(missing.querySelector('.demo-output').textContent.includes('尚待測試'),'unavailable reason');
  const resetRun=recorded.demo.start('reset during replay');await pause(250);recorded.demo.reset();
  check(!(await resetRun),'reset resolves cancelled run');await pause(2600);
  check(recorded.dataset.state==='idle'&&recorded.querySelector('.demo-output').hidden&&completed===1,'reset prevents late result');
  check(recorded.querySelector('.demo-progress').getAttribute('aria-valuenow')==='0'&&!recorded.querySelector('.demo-form button').disabled,'reset restores progress and controls');
  window.matchMedia=()=>({matches:true});
  const fast=recorded.demo.start('accessible replay');
  check(recorded.dataset.state==='running'&&recorded.querySelector('.demo-output').hidden,'reduced motion retains running state');
  check(getComputedStyle(recorded.querySelector('.demo-progress span')).transitionDuration==='0s','reduced motion has no bar animation');
  check(await fast,'reduced motion completes');window.matchMedia=reduced;
  check(recorded.querySelector('.demo-progress').getAttribute('aria-valuenow')==='100','reduced motion final progress');
  const suppressed=WebComfyExplorers.demoContent(fixture.demo,{suppressOutput:true});document.body.append(suppressed);
  window.matchMedia=()=>({matches:true});check(await suppressed.demo.start('session'),'suppressed output still resolves');window.matchMedia=reduced;
  check(suppressed.querySelector('.demo-output').hidden,'session caller owns output');suppressed.demo.dispose();suppressed.remove();
  recorded.demo.dispose();recorded.remove();missing.demo.dispose();missing.remove();
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
            elif path == '/voice.wav':
                import io, math, struct, wave
                buffer=io.BytesIO()
                with wave.open(buffer,'wb') as wav:
                    wav.setparams((1,2,16000,0,'NONE','not compressed'))
                    wav.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(i*440*2*math.pi/16000))) for i in range(16000*4)))
                data=buffer.getvalue();mime='audio/wav'
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


@pytest.mark.parametrize('width', [390, 768, 1440])
def test_prepared_audio_browser(explorer_server, width):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as driver:
        with driver.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox', '--disable-dev-shm-usage', '--autoplay-policy=no-user-gesture-required']) as browser:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(explorer_server+'/preview')
            page.evaluate("""() => {
              window.voice=WebComfyExplorers.demoContent({...fixture.demo,evidence:{kind:'prepared-example'},output:{kind:'audio',audio_tracks:[
                {src:'/voice.wav',title:'溫暖女聲',title_en:'Warm female voice',style:'輕柔旁白',style_en:'Soft narration',transcript:'歡迎來到海邊。',transcript_en:'Welcome to the coast.'},
                {src:'/voice.wav?second',title:'清晰女聲',title_en:'Clear female voice'},
                {src:'https://external.invalid/voice.wav',title:'Rejected remote'},
                {src:'data:audio/wav;base64,AAAA',title:'Rejected data URL'}]}});
              document.querySelector('main').replaceChildren(voice);
            }""")
            assert page.locator('audio').count()==0
            page.evaluate("() => {window.voicePending=voice.demo.start('different input');}")
            page.wait_for_timeout(350)
            assert page.locator('.demo-progress').is_visible()
            assert page.locator('audio').count()==0
            assert page.evaluate('() => window.voicePending')
            assert page.locator('audio').count()==2
            assert '非此工作流實測' in page.locator('.demo-disclosure').inner_text()
            assert page.locator('.demo-run-record').count()==0
            assert page.locator('.demo-output h4').inner_text()=='預製介面範例 · 非此工作流實測'
            assert page.locator('audio').first.get_attribute('preload')=='metadata'
            assert page.locator('audio').first.get_attribute('autoplay') is None
            page.evaluate("() => WebComfyI18n.setLanguage('en')")
            assert page.locator('.demo-audio-track h5').first.inner_text()=='Warm female voice'
            assert page.locator('.demo-audio-style').inner_text()=='Soft narration'
            page.locator('.demo-audio-transcript summary').click()
            assert page.locator('.demo-audio-transcript p').inner_text()=='Welcome to the coast.'
            assert page.evaluate('() => document.documentElement.scrollWidth<=innerWidth+1')
            page.evaluate("async () => {window.firstAudio=voice.querySelector('audio');await firstAudio.play();}")
            assert page.evaluate('() => !firstAudio.paused')
            page.evaluate("async () => {window.secondAudio=voice.querySelectorAll('audio')[1];await secondAudio.play();}")
            assert page.evaluate('() => firstAudio.paused&&!secondAudio.paused')
            page.evaluate('() => voice.demo.reset()')
            assert page.evaluate('() => secondAudio.paused')
            assert page.locator('audio').count()==0
            assert page.locator('.demo-output').is_hidden()
            page.emulate_media(reduced_motion='reduce')
            assert page.evaluate("() => voice.demo.start('replay')")
            page.evaluate("async () => {window.removedAudio=voice.querySelector('audio');await removedAudio.play();voice.remove();}")
            page.wait_for_function('() => removedAudio.paused')
            page.evaluate("""() => {
              window.single=WebComfyExplorers.demoContent({...fixture.demo,evidence:{kind:'prepared-example'},output:{kind:'audio',audio:{src:'/voice.wav',title:'Single clip'}}});
              document.querySelector('main').append(single);
            }""")
            assert page.evaluate("() => single.demo.start('single')")
            assert page.locator('audio').count()==1
            assert 'not a verified run' in page.locator('.demo-output h4').inner_text()
            page.evaluate('() => single.demo.dispose()')
