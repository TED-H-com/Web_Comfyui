"""Guided configuration and typed custom chains use synthetic media only."""
import json
from pathlib import Path

import pytest

from test_compute_browser import compute_page, fake_router
from test_provider_console import env
from test_provider_console_http import http_console
from test_compute_management_browser import screenshot








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


@pytest.mark.parametrize(('width','seed'),[(390,'9007199254740993'),(768,'18446744073709551615'),(1440,'-1')])
def test_custom_h3_uses_authored_fields_and_preserves_uint64_seed_text(compute_page,monkeypatch,width,seed):
    page,errors=compute_page;page.set_viewport_size({'width':width,'height':900});submitted=[];plans=[]
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'webui'))
    from minimax_h3_product import feature_definitions
    from compute_recipes import build_plan
    authored={item['id']:item for item in feature_definitions()}
    def pipelines(route):
        if route.request.method=='GET':route.fulfill(json={'pipelines':[]});return
        body=route.request.post_data_json;submitted.append(body);plans.append(build_plan(body,features=authored))
        route.fulfill(json={'pipeline_id':body['pipeline_id'],'state':'queued','steps':[{'id':step['id'],'state':'pending','placement':step['placement']} for step in plans[-1]['steps']]})
    page.route('**/api/compute/pipelines',pipelines)
    page.goto(page.url.split('/compute-fixture')[0]+'/#/compute-production')
    page.get_by_label('製作方式',exact=True).select_option('custom')
    feature=page.get_by_label('步驟 1 功能',exact=True)
    for feature_id in authored:assert feature.locator('option[value="'+feature_id+'"]').count()==1
    feature.select_option('h3-text-video')
    step=page.locator('.compute-custom-step').first
    for text in step.locator('textarea').all():text.fill('Synthetic paper kite, calm ambient sound, no music.')
    step.get_by_text('進階設定',exact=True).click()
    seed_input=page.get_by_label('文字生成有聲影片 種子（-1 隨機）',exact=True)
    assert seed_input.get_attribute('type')=='text' and seed_input.get_attribute('inputmode')=='numeric'
    seed_input.fill('1e18');page.get_by_role('button',name='開始製作',exact=True).click();assert not submitted
    seed_input.fill(seed)
    assert page.locator('.compute-production-form').evaluate('form=>form.checkValidity()'),page.locator('.compute-production-form :invalid').evaluate_all('nodes=>nodes.map(n=>({label:n.getAttribute("aria-label"),value:n.value,message:n.validationMessage}))')
    screenshot(page,f'custom-h3-seed-{width}.png')
    page.get_by_role('button',name='開始製作',exact=True).click()
    page.get_by_text('製作已建立，執行情況顯示於下方。',exact=True).wait_for()
    assert submitted[0]['plan']['steps'][0]['request']['settings']['seed']==seed
    assert plans[0]['steps'][0]['request']['settings']['seed']==seed
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors




def test_switching_back_to_builtin_ignores_hidden_custom_required_fields(compute_page):
    page,errors=compute_page
    page.route('**/api/compute/pipelines',lambda route:route.fulfill(json={'pipelines':[]}))
    page.goto(page.url.split('/compute-fixture')[0]+'/#/compute-production')
    page.get_by_label('製作方式',exact=True).select_option('custom')
    page.get_by_label('步驟 1 功能',exact=True).select_option('h3-text-video')
    assert not page.locator('.compute-production-form').evaluate('form=>form.checkValidity()')
    page.get_by_label('製作方式',exact=True).select_option('text-image-video')
    page.get_by_label('圖片描述',exact=True).fill('Synthetic kite.')
    page.get_by_label('影片動作與場景',exact=True).fill('Synthetic kite rises.')
    assert page.locator('.compute-production-form').evaluate('form=>form.checkValidity()')
    page.get_by_label('圖片製作影片執行位置',exact=True).select_option('autoip')
    assert page.get_by_label('AutoIP 影片秒數',exact=True).is_enabled()
    assert not errors,errors


def test_member_console_shows_only_own_connection_state(http_console):
    import shutil
    from test_provider_console import write
    h = http_console
    write(h.env.console('owner@example.invalid', 'admin'), p='comfy_cloud', secret='synthetic-' + 'test-key-1')
    write(h.env.console('bob@example.test'), p='comfy_cloud', secret='synthetic-' + 'test-key-2')
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context()
        context.add_cookies([{'name': 'castle_comfy_session', 'value': h.sessions['other@example.invalid'], 'url': h.origin}])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(h.origin, wait_until='domcontentloaded')
        page.wait_for_function("currentUser?.email === 'other@example.invalid'")
        page.evaluate("openComputeSettings(document.getElementById('accountMenuBtn'))")
        page.locator('.console-card').first.wait_for()
        assert page.get_by_role('button', name='平台管理', exact=True).count() == 0
        cloud = page.locator('[data-provider=comfy_cloud]')
        assert cloud.get_by_role('button', name='儲存金鑰', exact=True).count() == 1
        assert cloud.get_by_role('button', name='更換金鑰', exact=True).count() == 0
        write(h.env.console('other@example.invalid'), secret='synthetic-' + 'test-key-3')
        page.keyboard.press('Escape')
        page.evaluate("openComputeSettings(document.getElementById('accountMenuBtn'))")
        page.locator('[data-provider=runninghub]').get_by_role('button', name='更換金鑰', exact=True).wait_for()
        assert page.locator('[data-provider=comfy_cloud]').get_by_role('button', name='更換金鑰', exact=True).count() == 0
        text = page.locator('.compute-settings').inner_text()
        for value in ('owner@example.invalid', 'bob@example.test', 'synthetic-test-key-1', 'synthetic-test-key-2', 'synthetic-test-key-3'):
            assert value not in text
        assert page.locator('input[type=password]').evaluate_all('(els)=>els.every(el=>!el.value)')
        assert not h.calls
        assert not errors, errors
        browser.close()
