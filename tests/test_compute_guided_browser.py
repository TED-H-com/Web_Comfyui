"""Guided configuration and typed custom chains use synthetic media only."""
import json
from pathlib import Path

import pytest

from test_compute_browser import compute_page, fake_router
from test_compute_management_browser import screenshot


def guided_api(page):
    recipe={'feature_id':'text2img','recipe_id':'native-synthetic','label':'合成範本','output_kind':'image','estimated_cost':1,
            'workflow':{'6':{'class_type':'SyntheticNode','inputs':{'text':''}}},'partner_nodes':False,
            'bindings':[{'source':'prompt','node_id':'6','input':'text','default':'','value_map':[{'from':'a','to':'b'}]}], 'fixed_settings':{}}
    existing={'feature_id':'img2img','recipe_id':'keep-this','label':'保留的其他配方','output_kind':'image','workflow_id':'123',
              'bindings':[{'source':'prompt','node_id':'1','input':'text'}],'fixed_settings':{},'estimated_cost':8,'verified':True}
    providers={name:{'provider':name,'enabled':True,'endpoint':url,'credential_configured':True,'recipes':[existing.copy()]} for name,url in [('comfy_cloud','https://cloud.comfy.org'),('runninghub','https://www.runninghub.ai')]}
    prefs={'default_placement':'local','feature_placements':{'img2img':'colab'}}
    features=[{'id':key,'title':title,'providers':{lane:{'status':'unconfigured' if lane=='runninghub' else 'implemented','reason':'尚未設定此功能配方' if lane=='runninghub' else '已提供此功能契約'} for lane in ['local','colab','autoip','comfy_cloud','runninghub']}} for key,title in [('text2img','文字生圖'),('img2img','指令改圖')]]
    writes=[]
    def respond(route):
        path=route.request.url.split('/api/compute/',1)[1]
        if path=='recipe-catalog':route.fulfill(json={'templates':[{'id':'synthetic-template','label':'合成生圖範本','feature_id':'text2img','description':'沿用原生表單語意。','recipe':recipe,'requirements':['需要合成測試模型與節點'],'input_slots':[],'validation':'contract_tested','cost_requires_input':True}]});return
        if path=='capabilities':route.fulfill(json={'features':features});return
        if path=='preferences':
            if route.request.method=='POST':prefs.update(route.request.post_data_json);writes.append(('preferences',route.request.post_data_json))
            route.fulfill(json={'ok':True,**prefs});return
        if path=='providers':route.fulfill(json={'providers':list(providers.values())});return
        if path.startswith('providers/'):
            name=path.split('/')[1];body=route.request.post_data_json;writes.append((name,body));providers[name].update(body);route.fulfill(json={'provider':providers[name]});return
        route.fallback()
    page.route('**/api/compute/**',respond)
    return recipe,existing,providers,prefs,writes


@pytest.mark.parametrize('width',[390,768,1440])
def test_guided_template_setup_merges_recipes_and_requires_real_estimate(compute_page,tmp_path,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    recipe,existing,providers,_,writes=guided_api(page)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/admin')
    page.get_by_role('button',name='運算平台',exact=True).click()
    page.get_by_label('設定項目',exact=True).select_option('templates')
    page.get_by_label('功能範本',exact=True).select_option('synthetic-template')
    assert page.get_by_label('每次預估用量',exact=True).input_value()==''
    page.get_by_label('已確認平台具備列出的模型與節點',exact=True).check()
    page.get_by_role('button',name='儲存功能配方',exact=True).click()
    assert not writes
    page.get_by_label('每次預估用量',exact=True).fill('12.5')
    page.get_by_role('button',name='儲存功能配方',exact=True).click()
    page.get_by_text('功能配方已儲存；其他功能配方已保留，尚未啟動生成。',exact=True).wait_for()
    assert len(writes)==1 and writes[0][0]=='comfy_cloud'
    saved=writes[0][1]['recipes'];assert len(saved)==2
    assert saved[0]=={key:value for key,value in existing.items() if key!='verified'}
    assert saved[1]=={**recipe,'estimated_cost':12.5}
    assert page.get_by_label('替換此功能現有配方',exact=True).is_visible()
    page.get_by_label('每次預估用量',exact=True).fill('15')
    page.get_by_label('已確認平台具備列出的模型與節點',exact=True).check()
    page.get_by_role('button',name='儲存功能配方',exact=True).click();assert len(writes)==1
    page.get_by_label('替換此功能現有配方',exact=True).check()
    page.get_by_role('button',name='儲存功能配方',exact=True).click()
    page.get_by_text('功能配方已儲存；其他功能配方已保留，尚未啟動生成。',exact=True).wait_for()
    assert len(writes)==2 and len(writes[-1][1]['recipes'])==2
    page.get_by_label('平台',exact=True).select_option('runninghub')
    page.get_by_label('RunningHub 已發布工作流 ID',exact=True).wait_for()
    with page.expect_download() as download_info:page.get_by_role('button',name='下載工作流供發布',exact=True).click()
    download_path=tmp_path/'downloaded-graph.json';download_info.value.save_as(str(download_path))
    assert json.loads(download_path.read_text())==recipe['workflow']
    page.get_by_label('RunningHub 已發布工作流 ID',exact=True).fill('998877')
    page.get_by_label('每次預估用量',exact=True).fill('9')
    page.get_by_label('已確認平台具備列出的模型與節點',exact=True).check()
    assert '尚未在你的平台或 GPU 實跑' in page.get_by_role('dialog').inner_text()
    screenshot(page,f'guided-template-{width}.png')
    page.get_by_role('button',name='儲存功能配方',exact=True).click()
    page.get_by_text('功能配方已儲存；其他功能配方已保留，尚未啟動生成。',exact=True).wait_for()
    rh=writes[-1][1]['recipes'][-1]
    assert writes[-1][0]=='runninghub' and rh['workflow_id']=='998877'
    assert 'workflow' not in rh and 'partner_nodes' not in rh
    assert rh['bindings']==recipe['bindings'] and rh['estimated_cost']==9
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors


@pytest.mark.parametrize('width',[390,768,1440])
def test_capability_overview_and_private_placement_preferences(compute_page,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    _,_,_,prefs,writes=guided_api(page)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/admin')
    page.get_by_role('button',name='運算平台',exact=True).click()
    page.get_by_label('設定項目',exact=True).select_option('capabilities')
    page.get_by_label('搜尋可用功能',exact=True).fill('文字')
    page.get_by_label('篩選平台',exact=True).select_option('runninghub')
    page.get_by_text('尚未設定此功能配方',exact=True).wait_for()
    assert page.locator('.compute-capability-row').count()==1
    assert page.locator('[data-capability-lane]').count()==1
    screenshot(page,f'guided-capabilities-{width}.png')
    page.get_by_label('設定項目',exact=True).select_option('preferences')
    page.get_by_label('一般預設位置',exact=True).select_option('auto')
    page.get_by_label('指定功能',exact=True).select_option('text2img')
    page.get_by_label('此功能預設位置',exact=True).select_option('runninghub')
    page.get_by_role('button',name='儲存預設安排',exact=True).click()
    page.get_by_text('預設安排已儲存；現有表單與任務維持原選擇。',exact=True).wait_for()
    assert writes==[('preferences',{'default_placement':'auto','feature_placements':{'img2img':'colab','text2img':'runninghub'}})]
    assert prefs['default_placement']=='auto'
    screenshot(page,f'guided-preferences-{width}.png')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors


def test_saved_default_and_manual_choice_win_over_delayed_preferences(compute_page):
    page,errors=compute_page;previews,submissions,_,_,_,_=fake_router(page,hosted_ready=True)
    pending=[]
    page.route('**/api/compute/preferences',lambda route:pending.append(route))
    page.evaluate('window.mountCompute()')
    page.get_by_label('執行位置',exact=True).select_option('runninghub')
    page.wait_for_timeout(50);assert pending and not previews
    pending[0].fulfill(json={'default_placement':'auto','feature_placements':{'text-to-image':'comfy_cloud'}})
    page.wait_for_function('window.ComputePlacement.getState(document.querySelector("form")).eligible')
    assert previews[-1]['placement']=='runninghub'
    page.evaluate('window.submitCompute()');page.get_by_text('生成中',exact=True).wait_for()
    assert submissions[0]['placement']=='runninghub' and not errors


def test_saved_feature_default_initializes_before_first_preview(compute_page):
    page,errors=compute_page;previews,_,_,_,_,_=fake_router(page,hosted_ready=True)
    page.route('**/api/compute/preferences',lambda route:route.fulfill(json={'default_placement':'auto','feature_placements':{'text-to-image':'comfy_cloud'}}))
    page.evaluate('window.mountCompute()')
    page.wait_for_function('window.ComputePlacement.getState(document.querySelector("form")).eligible')
    assert previews and all(item['placement']=='comfy_cloud' for item in previews)
    assert page.get_by_label('執行位置',exact=True).input_value()=='comfy_cloud'
    assert not errors


def custom_registry():
    return [
        {'id':'synthetic-image','label':'合成生圖','studio':'image','output_kind':'image','fields':[
            {'id':'positive','label':'描述','type':'textarea','required':True},
            {'id':'steps','label':'步數','type':'slider','min':1,'max':30,'default':8,'step':1},
            {'id':'enabled','label':'效果','type':'checkbox','default':False},
            {'id':'aspect','label':'比例','type':'aspect','default':'64x64','options':[{'label':'正方形','width':64,'height':64}]}],'uploads':[]},
        {'id':'synthetic-video','label':'合成影片','studio':'video','output_kind':'video','fields':[{'id':'positive','label':'描述','type':'textarea','required':True}],
         'uploads':[{'id':'image','label':'首幀','accept':'image/*','required':True,'media_kinds':['image']}]},
        {'id':'synthetic-unknown','label':'類型待確認功能','output_kind':None,'fields':[],'uploads':[]},
        {'id':'synthetic-character','label':'角色專用功能','characterOnly':True,'output_kind':'image','fields':[],'uploads':[]},
    ]


@pytest.mark.parametrize('width',[390,768,1440])
def test_custom_steps_compile_typed_fields_and_previous_artifact_binding(compute_page,monkeypatch,width):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900})
    features=custom_registry();plans=[]
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'webui'))
    from compute_recipes import build_plan
    authored={item['id']:{**item,'builder':'synthetic','title':item['label']} for item in features}
    def respond(route):
        path=route.request.url.split('/api/compute/',1)[1]
        if path=='pipeline-features':route.fulfill(json={'features':features});return
        if path=='pipeline-recipes':route.fulfill(json={'recipes':[]});return
        if path=='preferences':route.fulfill(json={'default_placement':'local','feature_placements':{'synthetic-video':'runninghub'}});return
        if path=='pipelines' and route.request.method=='GET':route.fulfill(json={'pipelines':[]});return
        if path=='pipelines':
            body=route.request.post_data_json;plans.append(build_plan(body,features=authored))
            route.fulfill(json={'pipeline_id':body['pipeline_id'],'state':'queued','steps':[{'id':step['id'],'state':'pending','depends_on':step.get('depends_on',[]),'placement':step['placement']} for step in plans[-1]['steps']]});return
        route.fallback()
    page.route('**/api/compute/**',respond)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/compute-production')
    page.get_by_label('製作方式',exact=True).select_option('custom')
    page.get_by_label('步驟 1 執行位置',exact=True).select_option('comfy_cloud')
    page.get_by_label('步驟 1 功能',exact=True).select_option('synthetic-image')
    assert page.get_by_label('步驟 1 執行位置',exact=True).input_value()=='comfy_cloud'
    assert '角色專用功能' not in page.get_by_label('步驟 1 功能',exact=True).inner_text()
    page.get_by_label('合成生圖 描述',exact=True).fill('A synthetic paper kite.')
    page.locator('.compute-custom-step summary').click()
    page.get_by_label('合成生圖 步數',exact=True).fill('12')
    page.get_by_label('合成生圖 效果',exact=True).check()
    page.get_by_role('button',name='新增步驟',exact=True).click()
    page.get_by_label('步驟 2 功能',exact=True).select_option('synthetic-video')
    assert page.get_by_label('步驟 2 執行位置',exact=True).input_value()=='runninghub'
    page.get_by_label('合成影片 描述',exact=True).fill('The kite rises slowly.')
    page.get_by_label('合成影片 首幀來源',exact=True).select_option('step-1')
    screenshot(page,f'guided-custom-steps-{width}.png')
    page.get_by_role('button',name='開始製作',exact=True).click()
    page.get_by_text('製作已建立，執行情況顯示於下方。',exact=True).wait_for()
    assert len(plans)==1
    first,second=plans[0]['steps']
    assert first['request']['settings']=={'positive':'A synthetic paper kite.','steps':12,'enabled':True,'width':64,'height':64}
    assert second['bindings']=={'image':{'step_id':'step-1','artifact_index':0,'expected_kind':'image'}}
    assert second['depends_on']==['step-1'] and second['request']['files']=={}
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors


def test_unknown_output_requires_explicit_type_and_removed_source_invalidates_binding(compute_page):
    page,errors=compute_page;features=custom_registry();posts=[]
    def respond(route):
        path=route.request.url.split('/api/compute/',1)[1]
        if path=='pipeline-features':route.fulfill(json={'features':features})
        elif path=='pipeline-recipes':route.fulfill(json={'recipes':[]})
        elif path=='preferences':route.fulfill(json={'default_placement':'local','feature_placements':{}})
        elif path=='pipelines':
            if route.request.method=='POST':posts.append(route.request.post_data_json)
            route.fulfill(json={'pipelines':[]})
        else:route.fallback()
    page.route('**/api/compute/**',respond)
    page.goto(page.url.split('/compute-fixture')[0]+'/#/compute-production')
    page.get_by_label('步驟 1 功能',exact=True).select_option('synthetic-unknown')
    page.get_by_role('button',name='新增步驟',exact=True).click()
    page.get_by_label('步驟 2 功能',exact=True).select_option('synthetic-video')
    page.get_by_label('合成影片 描述',exact=True).fill('Synthetic motion.')
    source=page.get_by_label('合成影片 首幀來源',exact=True);source.select_option('step-1')
    expected=page.get_by_label('合成影片 首幀成果類型',exact=True)
    assert expected.is_visible() and expected.input_value()==''
    page.get_by_role('button',name='開始製作',exact=True).click();assert not posts
    expected.select_option('image')
    page.locator('.compute-custom-step').first.get_by_role('button',name='移除此步驟',exact=True).click()
    assert source.input_value()=='' and expected.is_hidden()
    page.get_by_role('button',name='開始製作',exact=True).click();assert not posts
    assert not errors


def test_member_preferences_excludes_credential_management(compute_page):
    page,errors=compute_page;guided_api(page)
    base=page.url.split('/compute-fixture')[0];page.goto(base+'/#/studio/qwen21-text2img')
    page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
    page.evaluate("() => {currentUser={...currentUser,role:'user',operator:false};openComputeSettings(document.querySelector('#identityChip'),'preferences');}")
    choices=page.get_by_label('設定項目',exact=True)
    assert choices.locator('option').all_text_contents()==['可用功能','預設安排']
    assert page.get_by_label('API 金鑰',exact=True).is_hidden()
    assert page.get_by_label('一般預設位置',exact=True).is_visible()
    assert not errors


def test_voice_quick_recipe_compiles_video_input_and_dynamic_step_ids(compute_page,monkeypatch):
    page,errors=compute_page;compiled=[]
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'webui'))
    from compute_recipes import build_plan
    def respond(route):
        if route.request.method=='GET':route.fulfill(json={'pipelines':[]});return
        body=route.request.post_data_json;plan=build_plan(body);compiled.append((body,plan))
        route.fulfill(json={'pipeline_id':body['pipeline_id'],'state':'queued','steps':[{'id':step['id'],'state':'pending','placement':step['placement']} for step in plan['steps']]})
    page.route('**/api/compute/pipelines',respond)
    page.goto(page.url.split('/compute-fixture')[0]+'/#/compute-production')
    page.get_by_label('製作方式',exact=True).select_option('voice-design-lipsync')
    video=page.get_by_label('可清楚辨識臉部的真人影片',exact=True)
    assert video.get_attribute('accept')=='video/*'
    video.set_input_files({'name':'synthetic-contract.mp4','mimeType':'video/mp4','buffer':b'synthetic-contract-only-no-decode'})
    page.get_by_label('要說的話',exact=True).fill('This is a synthetic voice contract.')
    page.get_by_label('聲音描述',exact=True).fill('A calm and clear synthetic voice.')
    page.get_by_label('設計合成聲音執行位置',exact=True).select_option('comfy_cloud')
    page.get_by_label('影片對嘴執行位置',exact=True).select_option('runninghub')
    page.get_by_role('button',name='開始製作',exact=True).click()
    page.get_by_text('製作已建立，執行情況顯示於下方。',exact=True).wait_for()
    body,plan=compiled[0]
    assert body['placements']=={'voice':'comfy_cloud','lipsync':'runninghub'}
    assert body['inputs']['video']['dataUrl'].startswith('data:video/mp4;base64,')
    assert plan['steps'][1]['bindings']['voice']=={'step_id':'voice','artifact_index':0,'expected_kind':'audio'}
    assert not errors
