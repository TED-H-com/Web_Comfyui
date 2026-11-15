"""Production JS/CSS and real session APIs on a disposable loopback server."""
import json
import os
import shutil
from pathlib import Path

import pytest
from test_provider_console import env,write,SECRET
from test_provider_console_http import http_console


def test_console_browser_sizes_and_write_only(http_console,tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    h=http_console
    admin=h.env.console('owner@example.invalid','admin');write(admin)
    with admin.prefs.db(write=True) as db:admin.prefs.put(db,admin.owner,{'default_placement':'local','feature_placements':{}})
    output=Path(os.environ.get('CONSOLE_SCREENSHOTS',str(tmp_path/'screenshots')));output.mkdir(parents=True,exist_ok=True)
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox', '--disable-dev-shm-usage'])
        context=browser.new_context()
        context.add_cookies([{'name':'castle_comfy_session','value':h.sessions['owner@example.invalid'],'url':h.origin}])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(h.origin,wait_until='domcontentloaded')
        page.wait_for_function("typeof openComputeSettings === 'function' && currentUser?.email === 'owner@example.invalid'")
        page.evaluate("openComputeSettings(document.getElementById('accountMenuBtn'))")
        page.locator('.console-card').first.wait_for()
        for width,height in [(390,844),(768,1024),(1440,1000)]:
            page.set_viewport_size({'width':width,'height':height})
            page.locator('.compute-settings-sheet .sheet-body').evaluate('(el)=>el.scrollTop=0')
            page.screenshot(path=str(output/f'console-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            assert page.locator('.compute-settings').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
            page.locator('[data-provider=runninghub]').scroll_into_view_if_needed()
            page.screenshot(path=str(output/f'console-{width}-saved.png'))
        assert page.locator('[data-provider=runninghub]').get_by_role('button',name='更換金鑰',exact=True).count()==1
        assert page.locator('[data-provider=runninghub] input[type=password]').count()==0
        assert SECRET not in page.content()
        assert page.locator('[data-provider=runninghub] [role=switch]').is_disabled()
        assert not h.calls  # GET and rendering never tested providers.
        page.get_by_role('button',name='平台管理',exact=True).click()
        page.get_by_text('本機使用權與連線').wait_for()
        page.screenshot(path=str(output/'admin-1440.png'))
        assert page.get_by_text('Alice',exact=True).count()==0  # Projection is Castle email, no provider identity.
        page.close();context.close()
        context=browser.new_context(viewport={'width':390,'height':844})
        context.add_cookies([{'name':'castle_comfy_session','value':h.sessions['other@example.invalid'],'url':h.origin}])
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(h.origin,wait_until='domcontentloaded')
        page.wait_for_function("currentUser?.email === 'other@example.invalid'")
        # Account-menu entry must exist for an ordinary authenticated user.
        assert page.evaluate("renderAccountMenuContent().textContent.includes('我的運算平台')")
        page.evaluate("openComputeSettings(document.getElementById('accountMenuBtn'))")
        page.locator('.console-card').first.wait_for()
        assert page.get_by_role('button',name='平台管理',exact=True).count()==0
        assert page.get_by_label('新任務預設',exact=True).input_value()==''
        rh=page.locator('[data-provider=runninghub]');key=rh.get_by_label('RunningHub API 金鑰',exact=True)
        assert rh.get_by_role('button',name='儲存金鑰',exact=True).is_disabled()
        key.fill(SECRET)
        assert rh.get_by_role('button',name='測試連線',exact=True).is_disabled()
        rh.get_by_role('button',name='儲存金鑰',exact=True).click()
        page.get_by_text('金鑰已儲存，請測試連線。',exact=True).wait_for()
        assert page.locator('[data-provider=runninghub] input[type=password]').count()==0
        assert SECRET not in page.content()
        assert SECRET not in page.evaluate('JSON.stringify(localStorage) + JSON.stringify(sessionStorage)')
        rh=page.locator('[data-provider=runninghub]')
        rh.get_by_role('button',name='測試連線',exact=True).click()
        page.get_by_text('連線檢查完成；請查看平台狀態。').wait_for()
        assert h.calls==['runninghub']
        # Closing and reopening keeps the saved indicator without filling a password field.
        page.keyboard.press('Escape');page.evaluate("openComputeSettings(document.getElementById('accountMenuBtn'))")
        page.locator('[data-provider=runninghub]').wait_for()
        assert page.locator('[data-provider=runninghub] input[type=password]').count()==0
        page.locator('.compute-settings-sheet .sheet-body').evaluate('(el)=>el.scrollTop=0')
        page.screenshot(path=str(output/'member-390.png'))
        # Keyboard focus stays visible and every interactive hit target is at least 44px high.
        page.keyboard.press('Tab')
        assert page.evaluate("document.activeElement !== document.body")
        sizes=page.locator('.compute-settings button:visible').evaluate_all('(els)=>els.map(e=>e.getBoundingClientRect().height)')
        assert min(sizes)>=44
        assert not errors,errors
        browser.close()
    assert SECRET not in '\n'.join(h.logs)


