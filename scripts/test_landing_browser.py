"""Verify the project homepage, nested demo and legacy entry links in a browser."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import threading
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_docs import build


def test_project_homepage_and_demo_entry(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    output = tmp_path / 'Web_Comfyui'
    build(ROOT, output)
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/Web_Comfyui/'
    try:
        with playwright.sync_playwright() as driver:
            executable = shutil.which('chromium') or driver.chromium.executable_path
            if not Path(executable).is_file(): pytest.skip('Chromium unavailable')
            with driver.chromium.launch(executable_path=executable, args=['--no-sandbox', '--disable-dev-shm-usage']) as browser:
                for width in (390, 768, 1440):
                    with browser.new_context(viewport={'width': width, 'height': 900}) as context:
                        page = context.new_page(); errors = []; requests = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.on('request', lambda request: requests.append(request.url))
                        page.goto(base); page.wait_for_selector('h1')
                        assert page.evaluate('innerWidth') == width
                        assert not page.locator('#workspace').count(), 'Homepage must introduce the project'
                        assert not any(url.endswith('/app.js') or '/app-guide.json' in url for url in requests)
                        assert page.evaluate('document.documentElement.scrollWidth') <= width + 1
                        page.select_option('#languageSelect', 'en')
                        page.wait_for_timeout(80)
                        assert page.locator('html').get_attribute('lang') == 'en'
                        assert page.evaluate('document.documentElement.scrollWidth') <= width + 1
                        referral = page.locator('a[href*="ref=TEDH2FND"]').first
                        assert 'utm_campaign=TEDH2FND' in referral.get_attribute('href')
                        assert 'sponsored' in referral.get_attribute('rel')
                        assert page.locator('img[src$="autoip-referral.svg"]').count()
                        qr = page.locator('img[src$="autoip-referral.svg"]')
                        qr.scroll_into_view_if_needed()
                        for _ in range(100):
                            if qr.evaluate('(image) => image.complete && image.naturalWidth > 0'): break
                            page.wait_for_timeout(20)
                        assert qr.evaluate('(image) => image.complete && image.naturalWidth > 0'), 'Referral QR renders'
                        page.locator('a[href^="./demo/"]').first.click()
                        page.wait_for_selector('form[data-demo-id="text2img"]')
                        assert urlsplit(page.url).path == '/Web_Comfyui/demo/'
                        assert page.locator('html').get_attribute('lang') == 'en'
                        assert 'Not a live service' in page.locator('#docsBanner').inner_text()
                        assert page.locator('#hardwareSelect').count()
                        assert not any('/api/' in url or urlsplit(url).netloc != urlsplit(base).netloc for url in requests)
                        assert not errors, errors
                with browser.new_context(viewport={'width': 390, 'height': 900}) as context:
                    page = context.new_page()
                    page.goto(base + '#/studio/text2img?guide=1')
                    page.wait_for_selector('.app-feature-guide')
                    assert urlsplit(page.url).path == '/Web_Comfyui/demo/'
    finally:
        server.shutdown(); server.server_close()
