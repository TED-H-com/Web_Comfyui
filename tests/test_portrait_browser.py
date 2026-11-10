"""Real public app + synthetic catalog: no external media or generation."""
import io
import os
from pathlib import Path
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
    image=buffer.getvalue()
    queries=[];media_requests=[]
    def media(route):
        media_requests.append(urlsplit(route.request.url).path)
        route.fulfill(body=image,content_type='image/png')
    def catalog(route):
        params=parse_qs(urlsplit(route.request.url).query)
        queries.append(params)
        kind=params['kind'][0];style=params.get('visualStyle',[''])[0]
        categories=params['categories'][0].split(',')
        category=params.get('category',[''])[0]
        assert not category or category in categories
        title='站姿' if kind=='pose' else ('動畫外套' if style=='anime' else '寫實外套')
        items=[]
        for index in range(30):
            ident=f'{kind}-{style}-{category or "all"}-{index}'
            variants=[{'id':angle,'label':label,'angle':angle,
                       'thumbnail':f'{base}/__synthetic/thumb/{ident}-{angle}.png',
                       'image':f'{base}/__synthetic/full/{ident}-{angle}.png'} for angle,label in [('front','正面'),('side','側面')]]
            items.append({'id':ident,'kind':kind,'title':f'{title} {index+1}','category':category or categories[index%len(categories)],
                          'categoryLabel':category,'visualStyle':style,'source':'custom','variants':variants})
        number=int(params['page'][0]);start=(number-1)*24
        route.fulfill(json={'ok':True,'items':items[start:start+24],'randomItem':items[0] if params.get('random')==['1'] else None,
                           'total':len(items),'page':number,'pageSize':24,'sources':['custom'],'categories':[{'id':c,'label':c} for c in categories]})
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=browser_path,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(viewport={'width':width,'height':844})
        name,value=cookie.split('=',1);context.add_cookies([{'name':name,'value':value,'url':base}])
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/reference-sources?*',catalog)
        page.route('**/__synthetic/**',media)
        page.goto(base+'/#/studio/character-photoshoot')
        page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
        page.locator('.photo-presets').wait_for()
        assert page.get_by_label('穿著大分類',exact=True).locator('option').count()==6
        assert page.get_by_label('姿勢大分類',exact=True).locator('option').count()==7
        page.get_by_label('穿著大分類',exact=True).select_option('daily')
        page.get_by_text('隨機選用：寫實外套 1',exact=True).wait_for()
        page.wait_for_function("[...document.querySelectorAll('.photo-reference-row img')].length===24 && [...document.querySelectorAll('.photo-reference-row img')].every(i=>i.complete && i.naturalWidth>0)")
        assert len(media_requests)==24 and all('/thumb/' in url and '-front.png' in url for url in media_requests)
        assert page.locator('.photo-reference-row img[loading=eager]').count()==24
        assert page.get_by_label('穿著細分類',exact=True).locator('option').count()==6
        assert queries[-1]['categories']==['street,spring,summer,autumn,winter']
        page.get_by_role('button',name='下一頁',exact=True).click()
        page.get_by_role('button',name='預覽寫實外套 25',exact=True).wait_for()
        assert page.locator('.photo-reference-row').count()==6
        page.get_by_label('穿著細分類',exact=True).select_option('summer')
        page.get_by_text('隨機選用：寫實外套 1',exact=True).wait_for()
        assert queries[-1]['category']==['summer'] and queries[-1]['page']==['1']
        page.get_by_role('button',name='重新隨機挑選穿著',exact=True).click()
        page.wait_for_function("document.querySelector('.photo-reference-row') !== null")
        assert queries[-1]['category']==['summer'] and queries[-1]['random']==['1']
        page.get_by_label('畫風',exact=True).select_option('anime')
        page.get_by_text('隨機選用：動畫外套 1',exact=True).wait_for()
        assert queries[-1]['visualStyle']==['anime'] and queries[-1]['category']==['summer']
        assert not any('/full/' in url for url in media_requests)
        row=page.locator('.photo-reference-row').first.bounding_box()
        assert row['height']<=60
        thumb=page.locator('.photo-reference-row img').first.bounding_box()
        assert thumb['height']<=48 and thumb['y']>=row['y'] and thumb['y']+thumb['height']<=row['y']+row['height']
        field=page.locator('textarea[data-field-id="instruction"]');before=field.input_value()
        page.get_by_role('button',name='預覽動畫外套 1',exact=True).click()
        dialog=page.get_by_role('dialog');dialog.wait_for()
        bounds=dialog.bounding_box();assert bounds['width']<=400 and bounds['height']<=520 and bounds['x']>0 and bounds['y']>0
        assert field.input_value()==before
        page.wait_for_function("document.querySelector('.reference-lightbox img')?.naturalWidth>0")
        assert sum('/full/' in url for url in media_requests)==1
        page.get_by_label('預覽視角',exact=True).select_option('side')
        page.get_by_role('button',name='套用這套穿著',exact=True).click();dialog.wait_for(state='hidden')
        assert '側面' in field.input_value()
        page.get_by_label('姿勢大分類',exact=True).select_option('standing')
        page.get_by_text('隨機選用：站姿 1',exact=True).wait_for()
        assert queries[-1]['categories']==['standing,movement,leaning'] and 'visualStyle' not in queries[-1]
        page.get_by_label('姿勢細分類',exact=True).select_option('movement')
        page.get_by_text('隨機選用：站姿 1',exact=True).wait_for()
        assert queries[-1]['category']==['movement']
        page.get_by_role('button',name='預覽站姿 1',exact=True).click();dialog.wait_for()
        page.wait_for_function("document.querySelector('[role=dialog]')?.contains(document.activeElement)")
        page.keyboard.press('Escape');dialog.wait_for(state='hidden')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if directory:=os.environ.get('PORTRAIT_SCREENSHOT_DIR'):
            path=Path(directory);path.mkdir(parents=True,exist_ok=True)
            page.locator('.photo-presets').evaluate('element=>window.scrollTo(0,window.scrollY+element.getBoundingClientRect().top-72)')
            page.screenshot(path=str(path/f'after-{width}.png'))
            page.locator('.photo-presets').screenshot(path=str(path/f'composer-{width}.png'))
            page.locator('.photo-choice').first.screenshot(path=str(path/f'clothing-{width}.png'))
        page.get_by_role('button',name='咖啡廳日常',exact=True).click()
        assert page.get_by_label('穿著大分類',exact=True).input_value()=='daily'
        assert page.get_by_label('穿著細分類',exact=True).input_value()=='autumn'
        assert page.get_by_label('姿勢大分類',exact=True).input_value()=='gesture'
        assert page.get_by_label('姿勢細分類',exact=True).input_value()=='gestures'
        assert not errors,errors
        context.close();browser.close()
