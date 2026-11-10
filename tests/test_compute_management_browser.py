"""Synthetic hosted configuration and multi-step UI; never contacts providers."""
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from test_compute_browser import compute_page, fake_router


def screenshot(page,name):
    directory=os.environ.get('WEB_COMFYUI_TEST_SCREENSHOTS')
    if directory:
        Path(directory).mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(Path(directory)/name),full_page=True)


@pytest.mark.parametrize('width',[390,768,1440])
def test_management_write_only_credentials_and_recipe_import(compute_page,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    providers={name:{'provider':name,'enabled':False,'credential_configured':False,'connection_state':'not_configured',
                     'endpoint':url,'recipes':[]} for name,url in [('comfy_cloud','https://cloud.comfy.org'),('runninghub','https://www.runninghub.ai')]}
    writes=[]
    def respond(route):
        if route.request.method=='GET':route.fulfill(json={'ok':True,'providers':list(providers.values())});return
        name=route.request.url.rsplit('/',1)[-1];body=route.request.post_data_json;writes.append((name,body))
        providers[name].update({key:value for key,value in body.items() if key not in ['api_key','clear_api_key']})
        if body.get('api_key'):providers[name]['credential_configured']=True
        if body.get('clear_api_key'):providers[name]['credential_configured']=False
        route.fulfill(json={'ok':True,'provider':providers[name]})
    page.route('**/api/compute/providers**',respond)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/admin')
    page.get_by_role('button',name='運算平台',exact=True).wait_for()
    screenshot(page,f'compute-management-before-{width}.png')
    page.get_by_role('button',name='運算平台',exact=True).click()
    dialog=page.get_by_role('dialog',name='運算平台');dialog.wait_for()
    page.get_by_text('尚未設定 API 金鑰。',exact=True).wait_for()
    page.get_by_label('啟用此平台',exact=True).check()
    page.get_by_label('API 金鑰',exact=True).fill('synthetic-key-only-for-testing')
    page.get_by_text('進階：功能配方',exact=True).click()
    recipe={'feature_id':'text2img','recipe_id':'synthetic-v1','label':'合成生圖配方','output_kind':'image',
            'workflow':{'6':{'class_type':'SyntheticNode','inputs':{'text':''}}},
            'bindings':[{'source':'prompt','node_id':'6','input':'text'}],'fixed_settings':{},'estimated_cost':4}
    page.get_by_label('配方檔案',exact=True).set_input_files({'name':'synthetic-recipe.json','mimeType':'application/json','buffer':json.dumps({'recipes':[{**recipe,'verified':True}]}).encode()})
    page.get_by_text('已讀取 1 個配方；按儲存後取代原清單。',exact=True).wait_for()
    page.get_by_role('button',name='儲存平台設定',exact=True).click()
    page.get_by_text('平台設定已儲存；尚未啟動生成。',exact=True).wait_for()
    assert writes==[('comfy_cloud',{'enabled':True,'endpoint':'https://cloud.comfy.org','api_key':'synthetic-key-only-for-testing','recipes':[recipe]})]
    assert page.get_by_label('API 金鑰',exact=True).input_value()==''
    assert 'synthetic-key-only-for-testing' not in dialog.inner_text()
    assert page.evaluate("![JSON.stringify(localStorage),JSON.stringify(sessionStorage)].some(value=>/synthetic-key|synthetic-unsaved|paper kite|pipeline_id|data:image/.test(value))")
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert dialog.bounding_box()['width']<=400
    dialog.locator('.sheet-body').evaluate('element=>element.scrollTop=0')
    screenshot(page,f'compute-management-after-{width}.png')
    page.get_by_label('平台',exact=True).select_option('runninghub')
    page.get_by_text('尚未設定 API 金鑰。',exact=True).wait_for()
    assert page.get_by_label('平台網址',exact=True).input_value()=='https://www.runninghub.ai'
    assert page.get_by_label('API 金鑰',exact=True).input_value()==''
    page.get_by_label('平台',exact=True).select_option('comfy_cloud')
    page.get_by_text('金鑰已設定；連線與配方仍會在使用時檢查。',exact=True).wait_for()
    page.get_by_label('移除已儲存的金鑰',exact=True).check()
    page.get_by_role('button',name='儲存平台設定',exact=True).click()
    page.get_by_text('平台設定已儲存；尚未啟動生成。',exact=True).wait_for()
    assert writes[-1][1]['clear_api_key'] is True and 'api_key' not in writes[-1][1]
    page.get_by_label('API 金鑰',exact=True).fill('synthetic-unsaved-key')
    page.keyboard.press('Escape');dialog.wait_for(state='hidden')
    page.get_by_role('button',name='運算平台',exact=True).click()
    page.get_by_text('尚未設定 API 金鑰。',exact=True).wait_for()
    assert page.get_by_label('API 金鑰',exact=True).input_value()==''
    assert not errors,errors


@pytest.mark.parametrize('recipe_id',['text-image-video','edit-image-video'])
def test_actual_recipe_schema_compiles_browser_inputs_without_execution(compute_page,monkeypatch,recipe_id):
    page,errors=compute_page
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'webui'))
    from compute_recipes import build_plan
    compiled=[]
    def pipelines(route):
        if route.request.method=='GET':route.fulfill(json={'ok':True,'pipelines':[]});return
        body=route.request.post_data_json;compiled.append(build_plan(body))
        route.fulfill(json={'ok':True,'pipeline_id':body['pipeline_id'],'state':'queued','steps':[
            {'id':'image','state':'pending','placement':body['placements']['image'],'depends_on':[]},
            {'id':'video','state':'pending','placement':body['placements']['video'],'depends_on':['image']}]})
    page.route('**/api/compute/pipelines',pipelines)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/compute-production')
    page.get_by_label('製作方式',exact=True).select_option(recipe_id)
    if recipe_id=='text-image-video':page.get_by_label('圖片描述',exact=True).fill('A synthetic blue paper kite.')
    else:
        page.get_by_label('修改指令',exact=True).fill('Make the synthetic kite blue.')
        output=io.BytesIO();Image.new('RGB',(32,32),'gray').save(output,'PNG')
        page.get_by_label('參考圖片',exact=True).set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':output.getvalue()})
    page.get_by_label('影片動作與場景',exact=True).fill('The synthetic kite rises slowly.')
    page.get_by_label('工作流影片長度',exact=True).select_option('226')
    page.get_by_label('圖片製作影片執行位置',exact=True).select_option('autoip')
    page.get_by_label('AutoIP 影片秒數',exact=True).fill('6')
    page.get_by_role('button',name='開始製作',exact=True).click()
    page.get_by_text('製作已建立，執行情況顯示於下方。',exact=True).wait_for()
    assert len(compiled)==1
    assert compiled[0]['steps'][1]['request']['settings']['frames']=='226'
    assert compiled[0]['steps'][1]['request']['computeAutoipVideo']['duration_seconds']==6
    assert compiled[0]['steps'][1]['depends_on']==['image']
    assert not errors,errors


def test_management_does_not_echo_provider_error_or_retain_failed_key(compute_page):
    page,errors=compute_page
    def respond(route):
        if route.request.method=='POST':route.fulfill(status=422,json={'ok':False,'error':{'code':'provider_error','message':'synthetic-secret-reflected-by-error'}})
        else:route.fulfill(json={'ok':True,'providers':[]})
    page.route('**/api/compute/providers**',respond)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/admin')
    page.get_by_role('button',name='運算平台',exact=True).click()
    page.get_by_text('尚未設定 API 金鑰。',exact=True).wait_for()
    page.get_by_label('API 金鑰',exact=True).fill('synthetic-secret-reflected-by-error')
    page.get_by_role('button',name='儲存平台設定',exact=True).click()
    page.get_by_text('設定尚未儲存。請檢查連線與配方格式後再試；新金鑰需重新輸入。',exact=True).wait_for()
    assert 'synthetic-secret-reflected-by-error' not in page.locator('body').inner_text()
    assert page.get_by_label('API 金鑰',exact=True).input_value()==''
    assert not errors


def test_operator_can_open_platform_management_from_account_menu(compute_page):
    page,errors=compute_page
    page.route('**/api/compute/providers',lambda route:route.fulfill(json={'ok':True,'providers':[]}))
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/studio/qwen21-text2img')
    page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
    page.evaluate('''() => {currentUser={...currentUser,role:'user',operator:true};openAccountMenu(document.querySelector('#identityChip'));}''')
    page.get_by_role('button',name='運算平台',exact=True).click()
    page.get_by_role('dialog',name='運算平台').wait_for()
    page.get_by_text('尚未設定 API 金鑰。',exact=True).wait_for()
    assert not errors,errors


def fake_pipelines(page,*,lost=False):
    recipes=[{'id':'edit-image-video','label':'改圖後製作影片','description':'先依指令改圖，再用成果製作影片。',
              'fields':[{'id':'instruction','label':'改圖描述','type':'textarea','required':True},
                        {'id':'image','label':'參考圖片','type':'file','required':True},
                        {'id':'motion_prompt','label':'影片動作','type':'textarea','required':True},
                        {'id':'visual_class','label':'圖片類型','type':'select','options':[{'value':'anime','label':'動漫'},{'value':'realistic','label':'寫實'}],'default':'realistic'},
                        {'id':'native_frames','label':'本機與 Colab 長度','type':'select','options':[{'value':124,'label':'約 5.2 秒'},{'value':226,'label':'約 9.4 秒'}],'default':124},
                        {'id':'autoip_seconds','label':'AutoIP 影片秒數','type':'number','min':2,'max':15,'default':8,'show_when':{'step_id':'video','placements':['auto','autoip']}}],
              'steps':[{'id':'image','label':'指令改圖','feature_id':'qwen21-edit','depends_on':[]},
                       {'id':'video','label':'圖片生影片','feature_id':'h3-image-video','depends_on':['image']}]}]
    jobs={};creates=[];actions=[];lookups=[];unavailable=[False]
    def respond(route):
        path=route.request.url.split('/api/compute/',1)[1]
        if path=='pipeline-recipes':route.fulfill(json={'ok':True,'recipes':recipes});return
        if path=='pipelines' and route.request.method=='GET':route.fulfill(json={'ok':True,'pipelines':list(jobs.values())[::-1]});return
        if path=='pipelines':
            body=route.request.post_data_json;creates.append(body);ident=body['pipeline_id']
            if lost and len(creates)==1:route.abort('failed');return
            jobs[ident]={'pipeline_id':ident,'state':'running','steps':[
                {'id':'image','index':0,'attempt':1,'placement':body['placements']['image'],'state':'running','depends_on':[],'artifacts':[]},
                {'id':'video','index':1,'attempt':1,'placement':body['placements']['video'],'state':'pending','depends_on':['image'],'artifacts':[]}]}
            route.fulfill(json={'ok':True,**jobs[ident]});return
        ident=path.split('/')[1]
        if route.request.method=='POST':
            action=path.split('/')[2];actions.append((action,route.request.post_data_json))
            if action=='retry':jobs[ident]['steps'][1].update(attempt=2,state='running');jobs[ident]['state']='running'
            if action=='cancel':jobs[ident]['state']='cancelling'
            route.fulfill(json={'ok':True,**jobs[ident]});return
        lookups.append(ident)
        if unavailable[0]:route.fulfill(status=503,json={'ok':False,'error':{'code':'unavailable','message':'synthetic unavailability'}})
        elif ident not in jobs:route.fulfill(status=404,json={'ok':False,'error':{'code':'pipeline_not_found','message':'absent'}})
        else:route.fulfill(json={'ok':True,**jobs[ident]})
    page.route('**/api/compute/pipeline**',respond)
    return creates,actions,jobs,lookups,unavailable


def fill_pipeline(page):
    page.get_by_label('改圖描述',exact=True).fill('Turn the synthetic paper kite blue.')
    page.get_by_label('影片動作',exact=True).fill('The paper kite rises slowly.')
    image=io.BytesIO();Image.new('RGB',(32,32),'gray').save(image,'PNG')
    page.get_by_label('參考圖片',exact=True).set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':image.getvalue()})


@pytest.mark.parametrize('width',[390,768,1440])
def test_multi_step_production_preserves_results_and_explicit_retry(compute_page,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    fake_router(page)
    creates,actions,jobs,_,_=fake_pipelines(page)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/studio/qwen21-text2img')
    page.locator('.compute-production-entry').wait_for()
    screenshot(page,f'compute-pipeline-before-{width}.png')
    page.locator('.compute-production-entry').click()
    page.get_by_label('改圖描述',exact=True).wait_for();fill_pipeline(page)
    assert page.url.endswith('#/compute-production')
    assert page.get_by_label('AutoIP 影片秒數',exact=True).is_hidden()
    page.get_by_label('指令改圖執行位置',exact=True).select_option('comfy_cloud')
    page.get_by_label('圖片生影片執行位置',exact=True).select_option('autoip')
    page.get_by_label('AutoIP 影片秒數',exact=True).fill('10')
    assert page.get_by_label('圖片生影片執行位置',exact=True).locator('option').count()==6
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    screenshot(page,f'compute-pipeline-after-{width}.png')
    page.get_by_role('button',name='開始製作',exact=True).click()
    page.get_by_text('製作已建立，執行情況顯示於下方。',exact=True).wait_for()
    assert len(creates)==1
    body=creates[0];assert body['placements']=={'image':'comfy_cloud','video':'autoip'}
    assert body['inputs']['native_frames']==124 and body['inputs']['autoip_seconds']==10
    assert body['inputs']['image']['dataUrl'].startswith('data:image/png;base64,')
    assert body['limits']['max_comfy_credits']==100 and body['limits']['max_runninghub_coins']==100
    job=jobs[body['pipeline_id']];job['state']='blocked'
    job['steps'][0].update(state='completed',artifacts=[{'name':'合成圖片成果','url':'/api/assets/synthetic-image/source'}])
    job['steps'][1]['state']='failed'
    page.get_by_role('button',name='更新紀錄',exact=True).click()
    page.get_by_role('link',name='合成圖片成果',exact=True).wait_for()
    page.get_by_role('button',name='繼續',exact=True).click()
    page.get_by_text('製作狀態已更新。',exact=True).wait_for()
    assert actions[-1]==('resume',{}) and len(creates)==1
    page.get_by_role('button',name='重試失敗步驟',exact=True).click()
    page.locator('[data-pipeline-step=video]').get_by_text('製作中',exact=True).wait_for()
    assert actions[-1]==('retry',{'step_id':'video'})
    assert page.get_by_role('link',name='合成圖片成果',exact=True).count()==1
    assert job['steps'][0]['attempt']==1 and job['steps'][1]['attempt']==2
    page.get_by_role('button',name='停止',exact=True).click()
    page.get_by_text('正在停止',exact=True).wait_for()
    assert page.get_by_role('link',name='合成圖片成果',exact=True).count()==1
    assert page.get_by_text('已停止',exact=True).count()==0
    assert not errors,errors


def test_pipeline_lost_create_response_preserves_original_identity(compute_page):
    page,errors=compute_page;creates,_,jobs,lookups,unavailable=fake_pipelines(page,lost=True)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/compute-production')
    page.get_by_label('改圖描述',exact=True).wait_for();fill_pipeline(page)
    page.get_by_role('button',name='開始製作',exact=True).click()
    retry=page.get_by_role('button',name='確認原提交',exact=True);retry.wait_for()
    assert len(creates)==1 and page.get_by_label('改圖描述',exact=True).is_disabled()
    page.evaluate('renderWorkspace()')
    assert page.get_by_label('改圖描述',exact=True).input_value()=='Turn the synthetic paper kite blue.'
    original=json.loads(json.dumps(creates[0]));unavailable[0]=True
    retry.click();page.get_by_text('提交回應尚未確認；請確認原提交，避免重複製作。',exact=True).wait_for()
    assert creates==[original] and lookups==[original['pipeline_id']]
    unavailable[0]=False;retry.click()
    page.get_by_text('製作已建立，執行情況顯示於下方。',exact=True).wait_for()
    assert creates==[original,original] and len(jobs)==1
    assert page.evaluate("![JSON.stringify(localStorage),JSON.stringify(sessionStorage)].some(value=>/synthetic-key|synthetic-unsaved|paper kite|pipeline_id|data:image/.test(value))")
    assert not errors,errors
