import os
from pathlib import Path
import shutil
import pytest


@pytest.mark.parametrize('width', [390, 768, 1440])
def test_source_only_broken_graph_warning_is_visible(test_server, width):
    pw = pytest.importorskip('playwright.sync_api')
    binary = shutil.which('chromium') or shutil.which('chromium-browser')
    if not binary:
        pytest.skip('Chromium unavailable')
    base, _, cookie = test_server
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=binary, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': width, 'height': 900})
        name, value = cookie.split('=', 1)
        context.add_cookies([{'name': name, 'value': value, 'url': base}])
        page = context.new_page(); errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base + '/#/workflows')
        page.get_by_label('搜尋工作流', exact=True).fill('simplesvd_1024')
        page.locator('.workflow-card').first.click()
        dialog = page.get_by_role('dialog')
        dialog.wait_for()
        evidence = os.environ.get('WEB_COMFYUI_TEST_SCREENSHOTS')
        before = os.environ.get('REVIEW_CAPTURE_BEFORE') == '1'
        if evidence:
            target = Path(evidence); target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f'workflow-review-{"before" if before else "after"}-{width}.png'))
        if not before:
            assert dialog.get_by_text('固定版本的上游圖未通過靜態連線檢查；需在 ComfyUI 檢查並修復。', exact=True).is_visible()
            assert dialog.get_by_role('button', name='載入 ComfyUI', exact=True).count() == 0
        assert dialog.evaluate('e => e.scrollWidth <= e.clientWidth')
        assert not errors
        context.close(); browser.close()
