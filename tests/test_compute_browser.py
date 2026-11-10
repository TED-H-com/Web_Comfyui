"""Synthetic router responses and actual browser UI; no provider access or media."""
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest


HTML = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/compute-placement.css">
<script src="/static/compute-placement.js"></script></head><body>
<main style="max-width:880px;margin:24px auto;padding:16px;background:var(--panel)">
<h1 style="font-size:var(--text-xl)">合成測試：共用生成表單</h1>
<form id="generation"><label class="field">內容描述<textarea id="prompt">A synthetic blue paper kite.</textarea></label>
<button id="generate" type="button" class="primary small" disabled>開始生成</button></form>
<script>
window.receipts=[];window.failures=[];window.availability=[];
window.transport=async(path,options={})=>{
 const response=await fetch(path,{headers:{'Content-Type':'application/json'},...options});
 const body=await response.json();if(!response.ok||body.ok===false){const error=new Error(body.error?.message||'HTTP failure');error.errorCode=body.error?.code;error.submissionState=body.submission_state;throw error;}return body;
};
const form=document.querySelector('#generation');
window.request=()=>({featureId:'text-to-image',settings:{positive:document.querySelector('#prompt').value},
 files:{image:{name:'synthetic.png',size:3,type:'image/png',data:'synthetic-private-pixels'}}});
window.mountCompute=()=>window.ComputePlacement.mount(form,{id:'text-to-image'},{api:window.transport,getRequest:window.request,
 onAvailability:value=>{window.availability.push(value);document.querySelector('#generate').disabled=!value.eligible;}});
window.submitCompute=()=>window.ComputePlacement.submit(form,window.request(),window.transport).then(value=>{window.receipts.push(value);return value;}).catch(error=>{window.failures.push(error.message);return null;});
document.querySelector('#generate').onclick=window.submitCompute;
</script></main></body></html>'''


@pytest.fixture
def compute_page(test_server):
    pw=pytest.importorskip('playwright.sync_api')
    chromium=shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:pytest.skip('Chromium unavailable')
    base,_,cookie=test_server
    with pw.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(viewport={'width':1440,'height':900})
        name,value=cookie.split('=',1);context.add_cookies([{'name':name,'value':value,'url':base}])
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        static_root=os.environ.get('WEB_COMFYUI_TEST_STATIC_ROOT')
        if static_root:
            from urllib.parse import urlsplit
            directory=Path(static_root).resolve()
            def static_asset(route):
                relative=urlsplit(route.request.url).path.removeprefix('/static/')
                path=(directory/relative).resolve()
                if path.is_relative_to(directory) and path.is_file() and path.suffix in ('.js','.css'):
                    route.fulfill(path=str(path),content_type='text/javascript' if path.suffix=='.js' else 'text/css')
                else:route.continue_()
            page.route('**/static/**',static_asset)
        page.route('**/compute-fixture',lambda route:route.fulfill(body=HTML,content_type='text/html'))
        page.goto(base+'/compute-fixture')
        yield page,errors
        context.close();browser.close()


def fake_router(page, *, drop_first=None, local_ready=True, autoip_ready=False):
    previews=[];submissions=[];lookups=[];jobs={};unknown_lookup=[False]
    account_id=str(uuid.uuid4())
    def respond(route):
        path=route.request.url.split('/api/compute',1)[1]
        if path=='/preview':
            body=route.request.post_data_json;previews.append(body)
            selected='colab' if body['placement']=='auto' else body['placement']
            route.fulfill(json={'ok':True,'selected_lane':None if selected=='autoip' and not autoip_ready or selected=='local' and not local_ready else selected,
                'candidates':[{'lane':lane,'eligible':(lane!='autoip' or autoip_ready) and (lane!='local' or local_ready),'queue_depth':2 if lane=='local' else 0,
                               'reasons':['internal-token-do-not-display'] if lane=='autoip' else ['local_not_ready'] if lane=='local' and not local_ready else []} for lane in ['local','colab','autoip']],
                'accounts':[{'id':account_id,'label':'合成 Ultra 帳號','authenticated':True,'balance_compute_units':80}],
                'recipes':{'local':'Qwen Image','colab':'PRISM / P-H3','autoip':'AutoIP'}})
        elif path=='/jobs' and route.request.method=='POST':
            body=route.request.post_data_json;submissions.append(body)
            job={'ok':True,'request_id':body['request_id'],'lane':'colab' if body['placement']=='auto' else body['placement'],
                 'state':'running','downstream_id':'synthetic-owned-'+body['request_id']}
            if len(submissions)==1 and drop_first:
                if drop_first=='rejected':
                    route.fulfill(status=422,json={'ok':False,'submission_state':'not_submitted','error':{'code':'invalid_request','message':'Synthetic input mismatch'}});return
                if drop_first=='accepted':jobs[job['request_id']]=job
                route.abort('failed');return
            jobs[job['request_id']]=job;route.fulfill(json=job)
        elif path=='/jobs':route.fulfill(json={'ok':True,'jobs':list(jobs.values())[::-1]})
        elif path.endswith('/cancel'):
            ident=path.split('/')[2];jobs[ident]['state']='cancelling';route.fulfill(json=jobs[ident])
        else:
            ident=path.split('/')[2];lookups.append(ident)
            if unknown_lookup[0]:route.fulfill(status=503,json={'ok':False,'error':{'code':'unavailable','message':'synthetic failure'}})
            elif ident in jobs:route.fulfill(json=jobs[ident])
            else:route.fulfill(status=404,json={'ok':False,'error':{'code':'compute_request_not_found','message':'Not found'}})
    page.route('**/api/compute/**',respond)
    page.route('**/api/generate',lambda route:pytest.fail('Unified UI must not call legacy generation'))
    return previews,submissions,lookups,jobs,unknown_lookup,account_id


@pytest.mark.parametrize('width',[390,768,1440])
def test_unified_placement_limits_jobs_and_small_layout(compute_page,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    previews,submissions,lookups,jobs,_,account_id=fake_router(page)
    directory=os.environ.get('WEB_COMFYUI_TEST_SCREENSHOTS')
    if directory:
        Path(directory).mkdir(parents=True,exist_ok=True);page.screenshot(path=str(Path(directory)/f'compute-before-{width}.png'))
    page.evaluate('window.mountCompute()')
    page.get_by_text('工作流：Qwen Image',exact=True).wait_for()
    assert page.get_by_label('執行位置',exact=True).input_value()=='local'
    assert page.locator('#generate').is_enabled()
    assert all('data' not in request['request']['files']['image'] for request in previews)
    assert not submissions
    page.get_by_label('執行位置',exact=True).select_option('autoip')
    page.get_by_text('目前無法用所選方式執行；請查看支援條件或調整設定。',exact=True).wait_for()
    assert page.locator('#generate').is_disabled()
    assert 'internal-token-do-not-display' not in page.locator('body').inner_text()
    page.get_by_text('目前不符合此方式的執行條件',exact=True).wait_for()
    page.get_by_label('執行位置',exact=True).select_option('auto')
    page.get_by_text('工作流：PRISM / P-H3',exact=True).wait_for()
    page.get_by_text('執行上限與 Colab 帳號',exact=True).click()
    page.get_by_label('最長執行分鐘',exact=True).fill('45')
    page.get_by_label('Colab 點數停止門檻',exact=True).fill('15')
    page.get_by_label('AutoIP 點數上限',exact=True).fill('60')
    page.get_by_label('Colab 執行帳號',exact=True).select_option(account_id)
    page.get_by_label('Colab GPU',exact=True).select_option('A100')
    page.wait_for_function('window.ComputePlacement.getState(document.querySelector("form")).eligible')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('.compute-placement').evaluate('element=>element.scrollWidth<=element.clientWidth')
    # Two simultaneous host callbacks share a single in-flight request/identity.
    page.evaluate('Promise.all([window.submitCompute(),window.submitCompute()])')
    page.get_by_text('生成中',exact=True).wait_for()
    assert len(submissions)==1
    submitted=submissions[0]
    assert submitted['placement']=='auto' and submitted['limits']=={'max_minutes':45,'max_units':15,'max_credits':60,'account_id':account_id,'gpu':'A100'}
    assert submitted['request']['files']['image']['data']=='synthetic-private-pixels'
    assert str(uuid.UUID(submitted['request_id']))==submitted['request_id']
    actual=page.evaluate('window.receipts[0]');assert actual['lane']=='colab' and actual['downstream_id']==jobs[actual['request_id']]['downstream_id']
    page.get_by_role('button',name='停止任務',exact=True).click()
    page.get_by_text('正在停止',exact=True).wait_for()
    assert '已取消' not in page.locator('.compute-tasks').inner_text()
    jobs[actual['request_id']].update(state='completed',artifacts=[{'name':'合成成果','url':'/api/media/synthetic.png'},{'name':'invalid','url':'javascript:alert(1)'}])
    page.get_by_role('button',name='更新任務',exact=True).click()
    page.get_by_text('已完成',exact=True).wait_for()
    assert page.get_by_role('link',name='合成成果',exact=True).get_attribute('href')=='/api/media/synthetic.png'
    assert page.get_by_role('link',name='invalid',exact=True).count()==0
    if directory:page.screenshot(path=str(Path(directory)/f'compute-after-{width}.png'),full_page=True)
    assert page.evaluate('localStorage.length===0 && sessionStorage.length===0')
    page.evaluate('window.ComputePlacement.reset()')
    assert page.locator('.compute-placement,.compute-tasks').count()==0
    assert not errors,errors


@pytest.mark.parametrize('drop_first',['accepted','absent'])
def test_ambiguous_submission_retains_identity_and_never_switches_provider(compute_page,drop_first):
    page,errors=compute_page
    _,submissions,lookups,jobs,unknown_lookup,_=fake_router(page,drop_first=drop_first)
    page.evaluate('window.mountCompute()');page.get_by_text('工作流：Qwen Image',exact=True).wait_for()
    page.get_by_label('執行位置',exact=True).select_option('colab')
    page.get_by_text('工作流：PRISM / P-H3',exact=True).wait_for()
    page.evaluate('window.submitCompute()')
    retry=page.get_by_role('button',name='確認並重試原請求',exact=True);retry.wait_for()
    assert len(submissions)==1 and page.get_by_label('執行位置',exact=True).is_disabled()
    original=json.loads(json.dumps(submissions[0]))
    unknown_lookup[0]=True
    retry.click();page.wait_for_function('!window.ComputePlacement.getState(document.querySelector("form")).submitting')
    assert len(submissions)==1 and lookups==[original['request_id']]
    # Even edited form text cannot silently replace an ambiguous request body.
    page.locator('#prompt').fill('Edited after a lost acknowledgement.')
    unknown_lookup[0]=False
    retry.click();page.get_by_text('生成中',exact=True).wait_for()
    assert all(body==original for body in submissions)
    assert len(submissions)==(1 if drop_first=='accepted' else 2)
    assert len(jobs)==1 and next(iter(jobs.values()))['lane']=='colab'
    assert retry.is_hidden()
    assert page.evaluate('localStorage.length===0 && sessionStorage.length===0')
    assert not errors,errors


def test_definite_prevalidation_rejection_allows_correcting_request(compute_page):
    page,errors=compute_page
    _,submissions,_,jobs,_,_=fake_router(page,drop_first='rejected')
    page.evaluate('window.mountCompute()');page.get_by_text('工作流：Qwen Image',exact=True).wait_for()
    page.evaluate('window.submitCompute()')
    page.get_by_text('提交檢查未通過，請調整設定後再試。',exact=True).wait_for()
    assert not jobs and page.get_by_label('執行位置',exact=True).is_enabled()
    assert page.get_by_role('button',name='確認並重試原請求',exact=True).is_hidden()
    page.locator('#prompt').fill('Corrected synthetic request.')
    page.wait_for_function('window.ComputePlacement.getState(document.querySelector("form")).eligible')
    page.evaluate('window.submitCompute()');page.get_by_text('生成中',exact=True).wait_for()
    assert len(submissions)==2 and submissions[0]['request_id']!=submissions[1]['request_id']
    assert submissions[1]['request']['settings']['positive']=='Corrected synthetic request.'
    assert len(jobs)==1 and not errors


@pytest.mark.parametrize('feature_id',['qwen21-text2img','character-photoshoot'])
def test_real_studio_routes_remote_when_local_missing_preserving_upload_requirement(compute_page,feature_id):
    page,errors=compute_page
    previews,submissions,_,_,_,_=fake_router(page,local_ready=False)
    base=page.url.split('/compute-fixture')[0]
    page.goto(base+'/#/studio/'+feature_id)
    page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
    page.get_by_label('執行位置',exact=True).wait_for()
    submit=page.locator('.gen-form button[type=submit]')
    assert submit.is_disabled()
    page.get_by_label('執行位置',exact=True).select_option('colab')
    page.get_by_text('工作流：PRISM / P-H3',exact=True).wait_for()
    field=page.locator('textarea[data-field-id="instruction"],textarea[data-field-id="positive"]').first
    field.fill('A synthetic blue paper kite on a white background.')
    if feature_id=='character-photoshoot':
        import io
        from PIL import Image
        assert submit.is_disabled()
        output=io.BytesIO();Image.new('RGB',(32,40),'gray').save(output,'PNG')
        page.locator('.dropzone input[type=file]').first.set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':output.getvalue()})
    page.wait_for_function('!document.querySelector(".gen-form button[type=submit]").disabled')
    submit.click()
    try:page.get_by_text('生成中',exact=True).wait_for(timeout=5000)
    except Exception:
        raise AssertionError({'errors':errors,'submissions':submissions,'alert':page.locator('#alert').inner_text(),
                              'invalid':page.locator('.gen-form :invalid').evaluate_all('(nodes)=>nodes.map(n=>({tag:n.tagName,type:n.type,value:n.value,message:n.validationMessage}))'),
                              'state':page.evaluate('window.ComputePlacement.getState(document.querySelector(".gen-form"))')})
    assert len(submissions)==1 and submissions[0]['placement']=='colab'
    assert submissions[0]['request']['featureId']==feature_id
    if feature_id=='character-photoshoot':assert set(submissions[0]['request']['files'])=={'image'}
    assert all('data' not in str(item['request'].get('files',{})) for item in previews)
    assert not errors,errors


def test_character_minimal_form_keeps_identity_and_does_not_track_remote_as_comfy(compute_page):
    page,errors=compute_page
    previews,submissions,_,_,_,_=fake_router(page,local_ready=False)
    base=page.url.split('/compute-fixture')[0]
    page.goto(base+'/#/studio/character-photoshoot')
    page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
    page.evaluate('''() => {
      window.ComputePlacement.reset();
      const capability={actionId:'photoshoot',featureId:'character-photoshoot',variantId:'synthetic.photoshoot.v1',visualClass:'realistic',enabled:true,runtimeReady:true,serverBoundUploads:['image']};
      const character={id:'synthetic-character',name:'合成角色',kind:'custom',visualClass:'realistic',capabilities:[capability]};
      characters=[character];activeCharacterId=character.id;selectedCharacterId=character.id;
      currentRoute={view:'character-workspace',characterId:character.id,actionId:'photoshoot'};
      const feature=features.find(item=>item.id==='character-photoshoot');feature.ready=false;
      document.querySelector('#workspace').replaceChildren(renderMinimalActionPanel(character,{id:'photoshoot',label:'寫真'}));
    }''')
    page.get_by_label('執行位置',exact=True).select_option('colab')
    page.get_by_text('工作流：PRISM / P-H3',exact=True).wait_for()
    page.locator('.minimal-form textarea').first.fill('A synthetic kite portrait.')
    page.wait_for_function('generationFsm.state==="ready"')
    page.locator('.sticky-generate-bar .generation-primary').click()
    try:page.get_by_text('生成中',exact=True).wait_for(timeout=5000)
    except Exception:
        raise AssertionError({'errors':errors,'submissions':submissions,'alert':page.locator('#alert').inner_text(),
                              'fsm':page.evaluate('generationFsm'),'state':page.evaluate('window.ComputePlacement.getState(document.querySelector("form.minimal-form"))'),
                              'invalid':page.locator('.minimal-form :invalid').evaluate_all('(nodes)=>nodes.map(n=>({value:n.value,message:n.validationMessage}))')})
    assert len(submissions)==1
    request=submissions[0]['request']
    assert request['characterId']=='synthetic-character' and request['action']=='photoshoot' and request['variantId']=='synthetic.photoshoot.v1'
    assert request['files']=={} and request['settings']['instruction']=='A synthetic kite portrait.'
    assert previews[-1]['request']['files']['image']['name']
    assert page.evaluate('trackedPromptId')==''
    assert not errors,errors


def test_explicit_uploaded_image_style_and_owned_file_metadata(compute_page):
    page,errors=compute_page
    previews,submissions,_,_,_,_=fake_router(page,autoip_ready=True)
    page.evaluate('''() => {window.request=()=>({featureId:'img2img',settings:{instruction:'A synthetic coat'},files:{image:{serverFile:'owned/synthetic.png'}}});window.mountCompute();}''')
    page.get_by_text('工作流：Qwen Image',exact=True).wait_for()
    assert page.get_by_label('圖片類型',exact=True).is_hidden()
    page.get_by_label('執行位置',exact=True).select_option('autoip')
    page.get_by_label('圖片類型',exact=True).wait_for()
    page.get_by_label('圖片類型',exact=True).select_option('anime')
    page.wait_for_function('window.ComputePlacement.getState(document.querySelector("form")).eligible')
    assert previews[-1]['request']['computeVisualClass']=='anime'
    assert previews[-1]['request']['files']=={'image':{'name':'synthetic.png'}}
    page.evaluate('window.submitCompute()');page.get_by_text('生成中',exact=True).wait_for()
    assert submissions[0]['request']['computeVisualClass']=='anime'
    assert 'visualStyle' not in submissions[0]['request']['settings']
    assert submissions[0]['request']['files']['image']['serverFile']=='owned/synthetic.png'
    assert not errors


@pytest.mark.parametrize('width',[390,768,1440])
def test_explicit_autoip_video_spec_preserves_native_h3_settings(compute_page,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    previews,submissions,_,jobs,_,_=fake_router(page,autoip_ready=True)
    page.evaluate("""() => {window.request=()=>({featureId:'h3-image-video',settings:{positive:'A synthetic moving kite',frames:124,aspect:'landscape',visualStyle:'anime'}});window.mountCompute();}""")
    page.get_by_text('工作流：Qwen Image',exact=True).wait_for()
    assert page.get_by_label('AutoIP 影片秒數',exact=True).is_hidden()
    page.get_by_label('執行位置',exact=True).select_option('autoip')
    page.get_by_label('AutoIP 影片秒數',exact=True).select_option('10')
    page.get_by_label('AutoIP 解析度',exact=True).select_option('768p')
    page.get_by_label('AutoIP 畫面比例',exact=True).select_option('9:16')
    page.wait_for_function('window.ComputePlacement.getState(document.querySelector("form")).eligible')
    assert previews[-1]['request']['computeAutoipVideo']=={'duration_seconds':10,'resolution':'768p','aspect_ratio':'9:16'}
    assert previews[-1]['request']['settings']['frames']==124
    assert previews[-1]['request']['settings']['aspect']=='landscape'
    assert page.get_by_label('圖片類型',exact=True).is_hidden()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('.compute-placement').evaluate('element=>element.scrollWidth<=element.clientWidth')
    directory=os.environ.get('WEB_COMFYUI_TEST_SCREENSHOTS')
    if directory:
        Path(directory).mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(Path(directory)/f'compute-autoip-video-{width}.png'),full_page=True)
    page.evaluate('window.submitCompute()');page.get_by_text('生成中',exact=True).wait_for()
    assert submissions[0]['request']==previews[-1]['request']
    ident=submissions[0]['request_id'];jobs[ident].update(state='collecting',cancel_supported=False)
    page.get_by_role('button',name='更新任務',exact=True).click()
    page.get_by_text('整理成果中',exact=True).wait_for()
    assert page.get_by_role('button',name='停止任務',exact=True).count()==0
    assert not errors
