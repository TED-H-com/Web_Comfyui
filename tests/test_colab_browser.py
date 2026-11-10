"""Real app, synthetic Colab transport, no Google account or user media access."""
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest


@pytest.mark.parametrize("width", [390, 768, 1440])
def test_colab_form_and_owned_job_controls(test_server, width):
    pw = pytest.importorskip("playwright.sync_api")
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        pytest.skip("Chromium unavailable")
    base, _, cookie = test_server
    account_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    submitted = []
    jobs = []
    account = {"id": account_id, "label": "測試 Ultra", "authenticated": True, "balance_compute_units": 1000}
    def route(request):
        path = request.request.url.split("/api/colab", 1)[1]
        if path == "":
            result = {"installed": True, "accounts": [account], "jobs": jobs}
        elif path == "/jobs":
            data = request.request.post_data_json
            submitted.append(data)
            jobs.append({"id": job_id, "account_id": account_id, "gpu": "L4", "created_at": 1794270000, "phase": "running", "status_label": "雲端執行中", "artifacts": []})
            result = {"id": job_id}
        elif path == f"/jobs/{job_id}/cancel":
            jobs[0].update(phase="cancelled", status_label="已取消，GPU 已釋放", released=True)
            result = jobs[0]
        else:
            result = account
        request.fulfill(json={"ok": True, **result})
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": width, "height": 900})
        name, value = cookie.split("=", 1)
        context.add_cookies([{"name": name, "value": value, "url": base}])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/colab**", route)
        page.goto(base + "/#/resources")
        page.wait_for_function("window.castleTimers && foregroundRefreshes.size === 0")
        page.get_by_role("button", name="開啟 Colab", exact=True).click()
        dialog = page.get_by_role("dialog")
        page.wait_for_timeout(250)
        assert page.locator(".colab-form").count(), (errors, dialog.inner_text())
        page.get_by_label("生成內容", exact=True).select_option("video")
        page.get_by_label("影片方式", exact=True).select_option("frames")
        page.get_by_label("參考檔案（合計最多 32 MB）", exact=True).set_input_files({"name": "synthetic.png", "mimeType": "image/png", "buffer": b"synthetic-ui-fixture"})
        page.get_by_label("內容描述", exact=True).fill("測試方塊平緩移動")
        page.get_by_label("生成秒數", exact=True).fill("8")
        assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        evidence = os.environ.get("WEB_COMFYUI_TEST_SCREENSHOTS")
        if evidence:
            directory = Path(evidence); directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"colab-{width}.png"))
        page.get_by_role("button", name="在 Colab 生成", exact=True).click()
        page.get_by_text("雲端執行中", exact=True).wait_for()
        assert len(submitted) == 1 and submitted[0]["job"]["seconds"] == 8
        assert submitted[0]["job"]["mode"] == "frames" and submitted[0]["account_id"] == account_id
        assert submitted[0]["files"][0]["name"] == "synthetic.png"
        page.get_by_role("button", name="停止並釋放 GPU", exact=True).click()
        page.get_by_text("已取消，GPU 已釋放", exact=True).wait_for()
        page.get_by_label("生成內容", exact=True).select_option("tts")
        assert not page.get_by_label("影片方式", exact=True).is_visible()
        page.get_by_label("內容描述", exact=True).fill("測試語音")
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        assert not errors, errors
        context.close(); browser.close()


@pytest.mark.parametrize('test_server', ['colab_transport'], indirect=True)
def test_colab_real_http_login_admission_and_cancel(test_server):
    """No Colab API routes mocked: browser -> HTTP -> SQLite -> subprocess."""
    pw = pytest.importorskip('playwright.sync_api')
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:
        pytest.skip('Chromium unavailable')
    base, _, cookie = test_server
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        name, value = cookie.split('=', 1)
        context.add_cookies([{'name': name, 'value': value, 'url': base}])
        page = context.new_page()
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/#/resources')
        page.wait_for_function('window.castleTimers && foregroundRefreshes.size === 0')
        page.get_by_role('button', name='開啟 Colab', exact=True).click()
        page.get_by_label('帳號名稱', exact=True).fill('合成測試帳號')
        page.get_by_role('button', name='新增並連接', exact=True).click()
        page.get_by_label('授權碼', exact=True).fill('synthetic-code')
        page.get_by_role('button', name='完成連線', exact=True).click()
        page.get_by_text('可用 100.0 點', exact=True).wait_for()
        page.get_by_label('內容描述', exact=True).fill('A synthetic blue square')
        page.get_by_role('button', name='在 Colab 生成', exact=True).click()
        page.get_by_role('button', name='停止並釋放 GPU', exact=True).click()
        page.get_by_text('已取消，GPU 已釋放', exact=True).wait_for(timeout=35000)
        assert not errors
        state = context.request.get(base + '/api/colab').json()
        assert len(state['jobs']) == 1 and state['jobs'][0]['released']
        assert 'subject_key' not in json.dumps(state)
        context.close(); browser.close()
