"""Real public app + synthetic catalog: no external media or generation."""
import base64
import io
import json
import shutil
from urllib.parse import parse_qs, urlsplit
import pytest
from PIL import Image


@pytest.mark.parametrize('width',[390,768,1440])
def test_portrait_picker_styles_and_small_lightbox(test_server,width):
    playwright=pytest.importorskip('playwright.sync_api')
    browser_path=shutil.which('chromium') or shutil.which('chromium-browser')
    if not browser_path:pytest.skip('Chromium unavailable')
    base,_,cookie=test_server
    buffer=io.BytesIO();Image.new('RGB',(80,100),'gray').save(buffer,'PNG')
    image='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
    def catalog(route):
        params=parse_qs(urlsplit(route.request.url).query)
        kind=params['kind'][0];style=params.get('visualStyle',[''])[0];category=params['category'][0]
        title='站姿' if kind=='pose' else ('動畫外套' if style=='anime' else '寫實外套')
        item={'id':kind+style,'kind':kind,'title':title,'category':category,'categoryLabel':category,'visualStyle':style,'source':'custom','variants':[{'id':'front','label':'正面','angle':'front','thumbnail':image,'image':image},{'id':'side','label':'側面','angle':'side','thumbnail':image,'image':image}]}
        route.fulfill(json={'ok':True,'items':[item],'randomItem':item,'total':1,'page':1,'pageSize':24,'sources':['custom'],'categories':[{'id':category,'label':category}]})
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=browser_path,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(viewport={'width':width,'height':844})
        name,value=cookie.split('=',1);context.add_cookies([{'name':name,'value':value,'url':base}])
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/reference-sources?*',catalog)
        page.goto(base+'/#/studio/character-photoshoot')
        page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
        page.locator('.photo-presets').wait_for()
        page.get_by_label('穿著大分類',exact=True).select_option('street')
        page.get_by_text('隨機選用：寫實外套',exact=True).wait_for()
        page.get_by_label('畫風',exact=True).select_option('anime')
        page.get_by_text('隨機選用：動畫外套',exact=True).wait_for()
        field=page.locator('textarea[data-field-id="instruction"]');before=field.input_value()
        page.get_by_role('button',name='預覽動畫外套',exact=True).click()
        dialog=page.get_by_role('dialog');dialog.wait_for()
        bounds=dialog.bounding_box();assert bounds['width']<=400 and bounds['height']<=520 and bounds['x']>0 and bounds['y']>0
        assert field.input_value()==before
        page.get_by_label('預覽視角',exact=True).select_option('side')
        page.get_by_role('button',name='套用這套穿著',exact=True).click();dialog.wait_for(state='hidden')
        assert '側面' in field.input_value()
        page.get_by_label('姿勢大分類',exact=True).select_option('standing')
        page.get_by_role('button',name='預覽站姿',exact=True).click();dialog.wait_for()
        page.wait_for_function("document.querySelector('[role=dialog]')?.contains(document.activeElement)")
        page.keyboard.press('Escape');dialog.wait_for(state='hidden')
        assert not errors,errors
        context.close();browser.close()
