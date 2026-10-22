"""Real browser coverage of library search, detail, load and narrow screens."""
import json
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'webui/static'
MOCK = r'''<script>
window.__wf={errors:[],loaded:null};
addEventListener('error',e=>__wf.errors.push(e.message));
addEventListener('unhandledrejection',e=>__wf.errors.push(String(e.reason)));
window.EventSource=class {close(){}};
window.fetch=async(input,options={})=>{
 const p=new URL(input,location.origin).pathname;
 const reply=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
 if(p==='/api/auth/status')return reply({configured:true,authenticated:true,lane:'local',user:{email:'owner@example.test',role:'admin',displayName:'Owner',operator:true}});
 if(p==='/api/features')return reply({studios:[],features:[]});
 if(p==='/api/characters')return reply({characters:[],actions:[]});
 if(p==='/api/queue')return reply({jobs:[],summary:{running:0,pending:0,total:0},recent_errors:[]});
 if(p==='/api/workflow-library')return reply(window.__catalogFixture);
 if(p==='/api/workflow-library/open'){__wf.loaded=JSON.parse(options.body);return reply({ok:true,message:'已加入 ComfyUI',missing_nodes:[]});}
 return reply({ok:true,items:[],characters:[],providers:[],assets:[],runs:[],posts:[]});
};
</script>'''
ASSERT = r'''<script>
(async()=>{
 const result=document.createElement('pre'); result.id='workflow-test-result';document.body.append(result);
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 const until=async(fn)=>{for(let i=0;i<300;i++){if(fn())return;await wait(20);}throw Error('timeout');};
 const assert=(x,msg)=>{if(!x)throw Error(msg);};
 try{
  await until(()=>document.querySelectorAll('.workflow-card').length>0);
  assert(document.documentElement.scrollWidth<=innerWidth+1,'horizontal overflow');
  if(new URLSearchParams(location.search).has('screenshot')){result.textContent='SCREENSHOT READY';return;}
  const search=document.querySelector('input[aria-label="搜尋工作流"]'); search.value='Local workflow';search.dispatchEvent(new Event('input'));
  assert(document.querySelectorAll('.workflow-card').length===1,'search narrows results');
  document.querySelector('.workflow-card').click();await until(()=>document.querySelector('.workflow-details'));
  const load=[...document.querySelectorAll('.workflow-details button')].find(b=>b.textContent==='載入 ComfyUI');load.click();
  await until(()=>__wf.loaded); assert(__wf.loaded.id==='local-test','load selected catalog item');
  assert(__wf.errors.length===0,__wf.errors.join('\n'));result.textContent='PASS';
 }catch(e){result.textContent='FAIL '+e.message;}
})();</script>'''


def fixture():
    entries = [{'id':'local-test','title':'Local workflow','category':'image','source':'workspace','kind':'workflow','format':'visual','available':True,'node_types':['KSampler'],'status':'not-gpu-qualified'}]
    entries += [{'id':f'ref-{n}','title':f'參考工作流 {n}','category':'video','source':'community','kind':'workflow','available':False} for n in range(45)]
    return {'items':entries,'comfy_url':'http://127.0.0.1:8188','connection':{'checked':False}}


def test_workflow_library_browser(tmp_path):
    browser = shutil.which('chromium')
    if not browser: pytest.skip('Chromium not installed')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/':
                injection='<script>window.__catalogFixture='+json.dumps(fixture())+'</script>'+MOCK
                data=(STATIC/'index.html').read_text().replace('</head>',injection+'</head>').replace('</body>',ASSERT+'</body>').encode()
                mime='text/html'
            elif path.startswith('/static/') and Path(path).name == path.removeprefix('/static/'):
                file=STATIC/Path(path).name
                if not file.is_file():self.send_error(404);return
                data=file.read_bytes();mime='text/css' if file.suffix=='.css' else 'text/javascript'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        for width in (1440,768,390):
            run=subprocess.run([browser,'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',f'--user-data-dir={tmp_path / str(width)}',f'--window-size={width},900','--virtual-time-budget=12000','--dump-dom',f'http://127.0.0.1:{server.server_port}/#/workflows'],capture_output=True,text=True,timeout=45)
            marker=run.stdout.split('id="workflow-test-result"')[-1].split('</pre>')[0]
            assert '>PASS' in marker, (width,marker[-1000:],run.stderr[-500:])
    finally:
        server.shutdown();server.server_close()
