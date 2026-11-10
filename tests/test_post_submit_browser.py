"""Exercise the actual post callback through browser form events; no providers."""
from pathlib import Path
import shutil

import pytest


@pytest.fixture
def post_page(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:
        pytest.skip('Chromium unavailable')
    source = (Path(__file__).resolve().parents[1] / 'webui/static/app.js').read_text()
    start = source.index('async function onGeneratePost(')
    callback = source[start:source.index('\nfunction renderPostResult(', start)]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium,
            downloads_path=str(tmp_path / 'downloads'),
            args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: pytest.fail('Synthetic callback must not use the network'))
        page.set_content('<form id="post"><button type="submit">生成貼文</button></form><div id="postResult"></div>')
        page.add_script_tag(content='''
            const activeCharacterId = 'synthetic-post-character';
            const characterPostsCache = new Map([[activeCharacterId, 'stale']]);
            let libraryPostsLoaded = true, libraryPostsError = 'stale';
            window.requests = []; window.alerts = []; window.rendered = [];
            window.capabilityCurrent = true;
            function activeCharacter() { return {id:activeCharacterId}; }
            function enabledFeatureCapability() { return {actionId:'post',variantId:'post.synthetic.v1'}; }
            function captureSessionContext() { return 'synthetic-owner-session'; }
            function applyCharacterDefaults(feature, settings) { return settings; }
            function showAlert(message) { window.alerts.push(message); }
            function capabilityStillEnabled() { return window.capabilityCurrent; }
            function assertSessionContext(value) { if (value !== 'synthetic-owner-session') throw Error('session mismatch'); }
            function sessionContextIsCurrent() { return true; }
            function isStaleSessionError() { return false; }
            function $(id) { return document.getElementById(id); }
            function renderPostResult(box, result) { window.rendered.push(result); box.textContent=result.text; }
            function api(path, options) {
                window.requests.push({path,method:options.method,body:JSON.parse(options.body)});
                return new Promise((resolve,reject)=>{window.finishPost=resolve;window.failPost=reject;});
            }
        ''')
        page.add_script_tag(content=callback)
        page.add_script_tag(content='''
            document.getElementById('post').addEventListener('submit', event =>
                onGeneratePost(event, {id:'post-generator'}, () => ({mode:'quick',topic:'合成草稿測試'})));
        ''')
        yield page, errors
        browser.close()


@pytest.mark.parametrize('succeeded', [True, False])
def test_post_uses_submitting_form_and_restores_button(post_page, succeeded):
    page, errors = post_page
    button = page.get_by_role('button', name='生成貼文', exact=True)
    button.click()
    page.wait_for_function('window.requests.length === 1')
    assert page.get_by_role('button', name='生成中…', exact=True).is_disabled()
    assert page.evaluate('window.requests[0]') == {
        'path': '/api/post', 'method': 'POST', 'body': {
            'featureId': 'post-generator', 'characterId': 'synthetic-post-character',
            'action': 'post', 'variantId': 'post.synthetic.v1',
            'settings': {'mode': 'quick', 'topic': '合成草稿測試'}}}
    if succeeded:
        page.evaluate("window.finishPost({ok:true,text:'合成完成草稿'})")
        page.wait_for_function("document.getElementById('postResult').textContent === '合成完成草稿'")
        assert page.evaluate('characterPostsCache.has(activeCharacterId)') is False
    else:
        page.evaluate("window.failPost(new Error('synthetic failure'))")
        page.wait_for_function("window.alerts.includes('生成失敗：synthetic failure')")
        assert page.evaluate('window.rendered.length') == 0
    assert page.get_by_role('button', name='生成貼文', exact=True).is_enabled()
    assert page.evaluate('window.requests.length') == 1
    assert errors == []


def test_post_rechecks_character_capability_before_dispatch(post_page):
    page, errors = post_page
    page.evaluate('window.capabilityCurrent = false')
    page.get_by_role('button', name='生成貼文', exact=True).click()
    page.wait_for_function("window.alerts.includes('生成失敗：這個角色的功能設定已變更，請重新選擇')")
    assert page.evaluate('window.requests') == []
    assert page.get_by_role('button', name='生成貼文', exact=True).is_enabled()
    assert errors == []
