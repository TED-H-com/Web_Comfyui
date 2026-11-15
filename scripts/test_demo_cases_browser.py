"""End-to-end Playwright browser tests for U1b demo cases, matching, gates, timing, characters, and voice library."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import shutil
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_docs import ROOT, build


def _launch_site(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    browser_path = shutil.which('chromium') or shutil.which('google-chrome')
    if not browser_path:
        with playwright.sync_playwright() as driver:
            candidate = driver.chromium.executable_path
            if Path(candidate).is_file():
                browser_path = candidate
    if not browser_path:
        pytest.skip('Chromium not installed')

    output = tmp_path / 'Web_Comfyui'
    build(ROOT, output)
    app_guide = json.loads((output / 'app-guide.json').read_text(encoding='utf-8'))
    demo_set = app_guide['demo_set']

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    return playwright, browser_path, server, origin, app_guide, demo_set


def _new_monitored_page(instance, origin, viewport=None):
    page = instance.new_page(viewport=viewport or {'width': 1280, 'height': 900})
    errors, external, filechoosers = [], [], []
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    page.on('filechooser', lambda fc: filechoosers.append(fc))
    page.route(
        '**/*',
        lambda route: route.continue_()
        if route.request.url.startswith(origin + '/')
        else (external.append(route.request.url), route.abort()),
    )
    return page, errors, external, filechoosers


def _assert_clean_page(page, errors, external, filechoosers):
    assert errors == [], errors
    assert external == [], external
    assert filechoosers == [], filechoosers
    unknown = page.evaluate('() => window.WebComfyDocs?.unknownRequests || []')
    assert unknown == [], unknown


def test_case_strips_order_unfilled_and_url_routing(tmp_path):
    playwright, browser_path, server, origin, app_guide, demo_set = _launch_site(tmp_path)
    assert demo_set.get('status') == 'final'

    studio_ids = {f['id'] for f in app_guide['features']}
    by_feature = {}
    for case in sorted(
        (c for c in demo_set.get('cases', []) if c.get('display') and c.get('kind') in ('recorded', 'prepared') and c.get('feature_id') in studio_ids),
        key=lambda c: c.get('order', 0),
    ):
        by_feature.setdefault(case['feature_id'], []).append(case)

    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)
                first_feat = next(iter(by_feature))
                page.goto(f'{origin}/Web_Comfyui/demo/#/studio/{first_feat}', wait_until='domcontentloaded', timeout=12000)
                page.locator(f'form[data-demo-id="{first_feat}"]').wait_for(timeout=12000)

                for feature_id, expected_cases in by_feature.items():
                    page.evaluate('(f) => { location.hash = "#/studio/" + f; }', feature_id)
                    form = page.locator(f'form[data-demo-id="{feature_id}"]')
                    form.wait_for(timeout=12000)

                    btns = form.locator('.demo-case-strip .demo-case-btn')
                    assert btns.count() == len(expected_cases), (feature_id, btns.count(), len(expected_cases))
                    actual_ids = [btns.nth(i).get_attribute('data-case-id') for i in range(btns.count())]
                    assert actual_ids == [c['id'] for c in expected_cases], (feature_id, actual_ids)

                    # Default first case selected cleanly without edit state
                    assert form.get_attribute('data-demo-case') == expected_cases[0]['id']
                    assert form.get_attribute('data-demo-edited') == 'false'
                    assert form.get_attribute('data-demo-gate') == 'ready'
                    assert btns.nth(0).get_attribute('aria-pressed') == 'true'

                    # Check every case on this feature for demoUnfilled and pre-generate hiding
                    for idx, case in enumerate(expected_cases):
                        if idx > 0:
                            btns.nth(idx).click()
                        assert form.get_attribute('data-demo-case') == case['id']
                        assert form.get_attribute('data-demo-edited') == 'false'
                        assert form.locator('.app-demo').is_hidden()

                        unfilled_raw = form.get_attribute('data-demo-unfilled') or ''
                        unfilled_keys = {k for k in unfilled_raw.split(',') if k}
                        assert unfilled_keys == set(), (feature_id, case['id'], unfilled_keys)

                        if feature_id == 'voice-speak':
                            options = [o.get_attribute('value') for o in form.locator('select option').all()]
                            assert 'documentation-example' not in options
                            assert form.locator('[data-persona-tabs], [data-demo-voice-selector]').count() == 0

                # Simulation pages have ZERO .demo-case-strip
                page.evaluate('() => { location.hash = "#/studio/voice-library"; }')
                page.locator('form[data-docs-voice-form="voice-library"]').wait_for(timeout=12000)
                assert page.locator('.demo-case-strip').count() == 0

                page.evaluate('() => { location.hash = "#/characters"; }')
                page.locator('.new-character-card').wait_for(timeout=12000)
                page.locator('.new-character-card').click()
                page.locator('form[data-demo-id="character-create"]').wait_for(timeout=12000)
                assert page.locator('form[data-demo-id="character-create"] .demo-case-strip').count() == 0

                # ?case= known (non-default) vs unknown graceful fallback
                multi_case_feat = next((f for f, cs in by_feature.items() if len(cs) >= 2), None)
                assert multi_case_feat
                second_case = by_feature[multi_case_feat][1]
                page.evaluate(
                    '(arg) => { location.hash = `#/studio/${arg.f}?case=${encodeURIComponent(arg.c)}`; }',
                    {'f': multi_case_feat, 'c': second_case['id']},
                )
                target_form = page.locator(f'form[data-demo-id="{multi_case_feat}"]')
                target_form.wait_for(timeout=12000)
                page.wait_for_function(
                    '(arg) => document.querySelector(`form[data-demo-id="${arg.f}"]`)?.dataset.demoCase === arg.c',
                    arg={'f': multi_case_feat, 'c': second_case['id']},
                    timeout=5000,
                )

                page.evaluate(
                    '(f) => { location.hash = `#/studio/${f}?case=nonexistent-case-999`; }',
                    multi_case_feat,
                )
                page.wait_for_function(
                    '(arg) => document.querySelector(`form[data-demo-id="${arg.f}"]`)?.dataset.demoCase === arg.c',
                    arg={'f': multi_case_feat, 'c': by_feature[multi_case_feat][0]['id']},
                    timeout=5000,
                )

                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()


def test_matching_rules_gate_assist_timing_and_results(tmp_path):
    playwright, browser_path, server, origin, app_guide, demo_set = _launch_site(tmp_path)
    blend_image1 = {c['inputs'].get('image1') for c in demo_set['cases']
                    if c['feature_id'] == 'image-blend' and c.get('display')}
    anime_mat = next((m for m in demo_set['materials'] if m.get('domain') == 'anime' and m.get('kind') == 'image'
                      and m['id'] not in blend_image1), None)
    assert anime_mat, 'Expected an anime image material that no image-blend case uses in image1'

    blend_cases = [c for c in demo_set['cases'] if c['feature_id'] == 'image-blend' and c.get('display')]
    assert len(blend_cases) >= 2

    img2img_case = next(c for c in demo_set['cases'] if c['feature_id'] == 'img2img' and c.get('display'))
    text2img_case = next(c for c in demo_set['cases'] if c['feature_id'] == 'text2img' and c.get('display'))
    angles_case = next(c for c in demo_set['cases'] if c['feature_id'] == 'character-angles' and c.get('display'))
    assert len(angles_case['outputs']) >= 2
    run_info = app_guide['demo_runs'][img2img_case['run_id']]
    expected_ms = round(min(4.0, max(1.5, math.sqrt(run_info['duration_seconds']) * 0.45)) * 1000)

    speak_cases = [c for c in demo_set['cases'] if c['feature_id'] == 'voice-speak' and c.get('display')]
    assert len(speak_cases) >= 2

    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)

                # 1. image-blend: disk upload match -> domain mismatch -> language toggle -> remove -> empty.slot -> match recovery
                page.goto(f'{origin}/Web_Comfyui/demo/#/studio/image-blend', wait_until='domcontentloaded', timeout=12000)
                form = page.locator('form[data-demo-id="image-blend"]')
                form.wait_for(timeout=12000)
                assert form.get_attribute('data-demo-case') == blend_cases[0]['id']

                second_bg = blend_cases[1]['inputs']['image2']
                form.locator('label.dropzone[data-upload-id="image2"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                page.locator('.demo-folder-btn[data-folder="scenes"]').click()
                page.locator(f'.demo-disk-modal .demo-tile[data-material-id="{second_bg}"]').click()
                page.wait_for_function(
                    '(cid) => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoCase === cid',
                    arg=blend_cases[1]['id'],
                    timeout=5000,
                )
                assert form.get_attribute('data-demo-gate') == 'ready'
                assert not form.locator('.generate-btn').is_disabled()

                # Library pick of anime domain portrait against real scene -> nomatch.domain & disabled button
                form.locator('[data-material-picker="image1"]').click()
                page.locator('.demo-library-modal').wait_for(timeout=5000)
                page.locator(f'.demo-library-modal .demo-tile[data-material-id="{anime_mat["id"]}"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "nomatch.domain"',
                    timeout=5000,
                )
                assert form.get_attribute('data-demo-case') == ''
                assert form.locator('.demo-gate-reason').is_visible()
                assert '真人要配真實場景' in form.locator('.demo-gate-reason').inner_text()
                assert form.locator('.generate-btn').is_disabled()

                # Live language switch updates gate reason & case strip labels cleanly
                page.locator('#languageSelect').select_option('en')
                page.wait_for_function("() => document.documentElement.lang === 'en'", timeout=5000)
                assert 'Realistic people go with real scenes' in form.locator('.demo-gate-reason').inner_text()
                page.locator('#languageSelect').select_option('zh-Hant')
                page.wait_for_function("() => document.documentElement.lang === 'zh-Hant'", timeout=5000)

                # Remove slot -> clears preview; pick valid matching portrait -> recovers ready + slotHasFile
                form.locator('.demo-slot-remove[data-slot="image1"]').click()
                assert form.locator('.demo-slot-preview[data-slot="image1"]').count() == 0
                assert form.get_attribute('data-demo-gate') == 'empty.slot'
                assert form.locator('.generate-btn').is_disabled()

                good_portrait = blend_cases[0]['inputs']['image1']
                form.locator('[data-material-picker="image1"]').click()
                page.locator('.demo-library-modal').wait_for(timeout=5000)
                page.locator(f'.demo-library-modal .demo-tile[data-material-id="{good_portrait}"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "ready"',
                    timeout=5000,
                )
                assert not form.locator('.generate-btn').is_disabled()

                # URL alias selection + domain exemption check on lipsync (voice slot exempt from domain check)
                page.evaluate('() => { location.hash = "#/studio/lipsync"; }')
                lip_form = page.locator('form[data-demo-id="lipsync"]')
                lip_form.wait_for(timeout=12000)
                page.emulate_media(reduced_motion='reduce')
                lip_form.locator('[data-url-fetch="video"]').click()
                lip_form.locator('.demo-url-alias-btn', has_text='qingqing-cafe.mp4').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="lipsync"]\')?.dataset.demoGate === "ready"',
                    timeout=5000,
                )
                page.emulate_media(reduced_motion='no-preference')

                # Non-matching upload in domain-exempt slot (h3-reference-video.audio1 / lipsync.voice) gives nomatch.other, never nomatch.domain
                lip_form.locator('[data-material-picker="voice"]').click()
                page.locator('.demo-library-modal').wait_for(timeout=5000)
                page.locator('.demo-library-modal .demo-tile[data-material-id="xingyu-hello"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="lipsync"]\')?.dataset.demoGate === "nomatch.other"',
                    timeout=5000,
                )
                assert lip_form.locator('.generate-btn').is_disabled()

                page.evaluate('() => { location.hash = "#/studio/h3-reference-video"; }')
                h3_form = page.locator('form[data-demo-id="h3-reference-video"]')
                h3_form.wait_for(timeout=12000)
                assert h3_form.get_attribute('data-demo-gate') == 'ready'

                # Select change matches another case (voice-speak) + demoEdited flag on manual edit
                page.evaluate('() => { location.hash = "#/studio/voice-speak"; }')
                speak_form = page.locator('form[data-demo-id="voice-speak"]')
                speak_form.wait_for(timeout=12000)
                second_voice_id = speak_cases[1]['settings']['voiceId']
                speak_form.locator('.post-mode-panel:not(.hidden) select').first.select_option(second_voice_id)
                page.wait_for_function(
                    '(cid) => document.querySelector(\'form[data-demo-id="voice-speak"]\')?.dataset.demoCase === cid',
                    arg=speak_cases[1]['id'],
                    timeout=5000,
                )
                assert speak_form.get_attribute('data-demo-edited') == 'false'
                speak_ta = speak_form.locator('textarea').first
                speak_ta.fill('Custom manual text')
                speak_ta.dispatch_event('input')
                assert speak_form.get_attribute('data-demo-edited') == 'true'

                # .assist-btn on text2img refills case positive prompt cleanly without opening dialog
                page.evaluate('() => { location.hash = "#/studio/text2img"; }')
                t2i_form = page.locator('form[data-demo-id="text2img"]')
                t2i_form.wait_for(timeout=12000)
                assert t2i_form.locator('.assist-btn').count() >= 1
                pos_ta = t2i_form.locator('textarea[data-field-id="positive"]')
                pos_ta.fill('temporary text')
                t2i_form.locator('.assist-btn').first.click()
                page.wait_for_function(
                    "() => document.querySelector('.demo-toast')?.textContent.includes('已填入這個案例實際使用的提示詞')",
                    timeout=5000,
                )
                assert pos_ta.input_value() == text2img_case['settings']['positive']
                assert page.locator('.sheet-layer [role="dialog"]').count() == 0

                # Timing within +/-450ms (and reduced motion == 0s) + rich result cards
                page.evaluate('() => { location.hash = "#/studio/img2img"; }')
                img_form = page.locator('form[data-demo-id="img2img"]')
                img_form.wait_for(timeout=12000)

                # Measure real replay timing against expected durationMs (+/-450ms margin)
                elapsed_ms = page.evaluate("""async () => {
                  const form = document.querySelector('form[data-demo-id="img2img"]');
                  const demoBox = form.querySelector('.app-demo');
                  const btn = form.querySelector('.generate-btn');
                  const t0 = performance.now();
                  btn.click();
                  while (demoBox.dataset.state !== 'complete') {
                    await new Promise(r => setTimeout(r, 20));
                  }
                  return performance.now() - t0;
                }""")
                assert abs(elapsed_ms - expected_ms) <= 450, (elapsed_ms, expected_ms)

                # Rich recorded result parts
                assert 'RTX 5090' in img_form.locator('.demo-disclosure.demo-result-provenance').inner_text()
                assert img_form.locator('.demo-comparison img').count() == 2
                img_form.locator('.demo-run-record summary').click()
                assert img2img_case['id'] in img_form.locator('.demo-run-record pre').inner_text()
                assert img_form.locator('.demo-what-next a').count() >= 1

                # Reduced motion executes immediately (~0s)
                page.emulate_media(reduced_motion='reduce')
                page.evaluate('() => { location.hash = "#/studio/character-angles"; }')
                ang_form = page.locator('form[data-demo-id="character-angles"]')
                ang_form.wait_for(timeout=12000)
                fast_ms = page.evaluate("""async () => {
                  const form = document.querySelector('form[data-demo-id="character-angles"]');
                  const demoBox = form.querySelector('.app-demo');
                  const t0 = performance.now();
                  form.querySelector('.generate-btn').click();
                  while (demoBox.dataset.state !== 'complete') {
                    await new Promise(r => setTimeout(r, 15));
                  }
                  return performance.now() - t0;
                }""")
                assert fast_ms < 350, fast_ms
                assert ang_form.locator('.demo-angles-grid img').count() == len(angles_case['outputs'])
                page.wait_for_function(
                    "() => [...document.querySelectorAll('form[data-demo-id=\"character-angles\"] .demo-angles-grid img')].every(i => i.naturalWidth > 0)",
                    timeout=5000,
                )
                grid_cols = page.evaluate("() => getComputedStyle(document.querySelector('form[data-demo-id=\"character-angles\"] .demo-angles-grid')).gridTemplateColumns.split(' ').length")
                assert grid_cols == 2

                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()


def test_characters_voice_library_and_mobile_overflow(tmp_path):
    playwright, browser_path, server, origin, app_guide, demo_set = _launch_site(tmp_path)
    listed_subs = [s for s in demo_set['subjects'] if s.get('listed')]
    assert len(listed_subs) >= 2
    feature_order = [f['id'] for f in app_guide['features']]

    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)

                # Character gallery lists both demo subjects with badge; open first listed subject
                page.goto(f'{origin}/Web_Comfyui/demo/#/characters', wait_until='domcontentloaded', timeout=12000)
                page.locator('.new-character-card').wait_for(timeout=12000)
                for sub in listed_subs:
                    cid = sub['character_id']
                    card = page.locator(f'[data-character-id="{cid}"]')
                    assert card.count() == 1
                    assert '示範' in card.inner_text()

                first_cid = listed_subs[0]['character_id']
                page.locator(f'[data-character-id="{first_cid}"]').click()
                page.locator('.docs-character-voice audio').wait_for(timeout=5000)
                assert page.locator('.docs-character-case-card').count() >= 4

                # Check groups & tool ordering follows app_guide['features']
                group_titles = [el.inner_text() for el in page.locator('.docs-character-group-title').all()]
                assert {'圖片', '影片', '聲音'} <= set(group_titles), group_titles
                card_hrefs = [a.get_attribute('href') for a in page.locator('.docs-character-case-card a[href^="#/studio/"]').all()]
                assert all(f'&from={first_cid}' in h or f'?from={first_cid}' in h for h in card_hrefs)

                # Navigate via ?case=...&from=... and verify .demo-char-back roundtrip
                page.locator('.docs-character-case-card a[href^="#/studio/"]').first.click()
                page.locator('.demo-char-back').wait_for(timeout=5000)
                back_href = page.locator('.demo-char-back').get_attribute('href')
                assert back_href == f'#/c/{first_cid}'
                page.locator('.demo-char-back').click()
                page.locator('.docs-character-voice audio').wait_for(timeout=5000)

                # Verify all cases referencing the listed subject are in feature_order sequence within groups
                assert feature_order[0] == 'text2img'

                # character-create unbound vs bound
                page.evaluate('() => { location.hash = "#/characters"; }')
                page.locator('.new-character-card').wait_for(timeout=5000)
                page.locator('.new-character-card').click()
                cc_form = page.locator('form[data-demo-id="character-create"]')
                cc_form.wait_for(timeout=5000)
                assert cc_form.locator('.demo-case-strip').count() == 0
                cc_form.locator('button[type="submit"]').click()
                page.locator('.demo-cc-nophoto').wait_for(timeout=5000)
                assert page.locator('.docs-character-case-card').count() == 0

                # Now create bound character: Disk modal allows only 'people' folder + cc.only-demo
                page.evaluate('() => { location.hash = "#/characters"; }')
                page.locator('.new-character-card').wait_for(timeout=5000)
                page.locator('.new-character-card').click()
                cc_form.wait_for(timeout=5000)
                cc_form.locator('input[name="photos"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                assert page.locator('.demo-folder-btn[data-folder="people"]').get_attribute('aria-selected') == 'true'
                page.locator('.demo-folder-btn[data-folder="scenes"]').click()
                assert '示範站只能用示範人物照片' in page.locator('.demo-disk-modal').inner_text()
                page.locator('.demo-folder-btn[data-folder="people"]').click()
                first_mat = next(m['id'] for m in demo_set['materials'] if m.get('subject') == listed_subs[0]['id'] and m.get('kind') == 'image')
                page.locator(f'.demo-disk-modal .demo-tile[data-material-id="{first_mat}"]').click()
                page.wait_for_function("() => !document.querySelector('.demo-disk-modal')", timeout=5000)
                cc_form.locator('button[type="submit"]').click()
                page.locator('.demo-cc-bound').wait_for(timeout=5000)
                assert page.locator('.docs-character-case-card').count() >= 4

                # voice-library: groups, no .generate-btn, add voice from disk, handoff to clone & speak
                page.evaluate('() => { location.hash = "#/studio/voice-library"; }')
                vl_form = page.locator('form[data-docs-voice-form="voice-library"]')
                vl_form.wait_for(timeout=12000)
                assert vl_form.locator('.generate-btn').count() == 0
                assert page.locator('.demo-case-strip').count() == 0
                assert page.locator('[data-demo-voice-selector]').count() == 0
                assert page.locator('[data-voice-actions]').count() == 0
                assert page.locator('.demo-voice-library .demo-voice-group').count() == 1
                initial_cards = page.locator('.demo-voice-library .demo-voice-card').count()
                assert initial_cards >= 3
                expected_ref_count = len({
                    c.get('inputs', {}).get('voice')
                    for c in demo_set['cases']
                    if c['feature_id'] == 'voice-clone' and c.get('display') and c.get('inputs', {}).get('voice')
                })
                assert page.locator('.demo-voice-library a[href^="#/studio/voice-clone?case="]').count() == expected_ref_count

                disk_voice_mat = next(m for m in demo_set['materials'] if m.get('kind') == 'audio' and m.get('folder') == 'voices')
                vl_form.locator('label[data-upload-slot="voice"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                assert page.locator('.demo-folder-btn[data-folder="voices"]').get_attribute('aria-selected') == 'true'
                page.locator(f'.demo-disk-modal .demo-tile[data-material-id="{disk_voice_mat["id"]}"]').click()
                page.wait_for_function(
                    "() => document.querySelector('.demo-toast')?.textContent.includes('已加入聲音庫（示範）')",
                    timeout=5000,
                )
                added = page.evaluate('() => window.WebComfyDemoCases?.addedVoiceIds?.() || []')
                assert disk_voice_mat['id'] in added
                expected_cards = initial_cards if disk_voice_mat.get('library') else initial_cards + 1
                assert page.locator('.demo-voice-library .demo-voice-card').count() == expected_cards

                # Handoff to voice-clone (?case=...)
                page.locator('.demo-voice-library a[href^="#/studio/voice-clone?case="]').first.click()
                clone_form = page.locator('form[data-demo-id="voice-clone"]')
                clone_form.wait_for(timeout=12000)
                assert page.locator('[data-demo-voice-selector]').count() == 0
                assert clone_form.get_attribute('data-demo-case')

                # Handoff to voice-speak from voice-library
                page.evaluate('() => { location.hash = "#/studio/voice-library"; }')
                page.locator('.demo-voice-library a[href^="#/studio/voice-speak"]').first.click()
                speak_form = page.locator('form[data-demo-id="voice-speak"]')
                speak_form.wait_for(timeout=12000)
                assert page.locator('[data-demo-voice-selector]').count() == 0
                assert speak_form.get_attribute('data-demo-case')

                # 390px mobile zero horizontal overflow across key views
                page.set_viewport_size({'width': 390, 'height': 844})
                for route in (
                    '#/characters',
                    f'#/c/{first_cid}',
                    '#/studio/image-blend',
                    '#/studio/character-angles',
                    '#/studio/voice-library',
                    '#/manage',
                ):
                    page.evaluate('(r) => { location.hash = r; }', route)
                    page.wait_for_timeout(150)
                    overflow = page.evaluate('() => document.documentElement.scrollWidth - innerWidth')
                    assert overflow <= 1, (route, overflow)

                # Selecting the last case in an overflowing 390px strip scrolls strip scrollLeft without scrolling the page
                vs_cases = [c for c in demo_set['cases'] if c['feature_id'] == 'voice-speak' and c.get('display')]
                assert len(vs_cases) >= 2
                page.evaluate('() => window.scrollTo(0, 0)')
                page.evaluate('(cid) => { location.hash = "#/studio/voice-speak?case=" + encodeURIComponent(cid); }', vs_cases[-1]['id'])
                page.locator('form[data-demo-id="voice-speak"] .demo-case-strip').wait_for(timeout=8000)
                page.wait_for_timeout(120)
                strip_metrics = page.evaluate("""() => {
                  const row = document.querySelector('form[data-demo-id="voice-speak"] .demo-case-strip-buttons');
                  return {
                    scrollWidth: row?.scrollWidth || 0,
                    clientWidth: row?.clientWidth || 0,
                    scrollLeft: row?.scrollLeft || 0,
                    pageY: window.scrollY || 0,
                  };
                }""")
                assert strip_metrics['scrollWidth'] > strip_metrics['clientWidth']
                assert strip_metrics['scrollLeft'] > 0, strip_metrics
                assert strip_metrics['pageY'] == 0, strip_metrics

                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()


def test_u1c_final_data_e8(tmp_path):
    playwright, browser_path, server, origin, app_guide, demo_set = _launch_site(tmp_path)
    studio_ids = {f['id'] for f in app_guide['features']}
    expected_chars = [
        'docs-character-qingqing',
        'docs-character-zhiyuan',
        'docs-character-xingyu',
        'docs-character-chenyi',
    ]
    expected_voices = [
        'qingqing-hello',
        'zhiyuan-hello',
        'xingyu-hello',
        'chenyi-hello',
        'qingqing-english',
        'zhiyuan-english',
    ]
    recorded_by_feature = {}
    for case in sorted(
        (c for c in demo_set.get('cases', []) if c.get('display') and c.get('kind') == 'recorded' and c.get('feature_id') in studio_ids),
        key=lambda c: c.get('order', 0),
    ):
        recorded_by_feature.setdefault(case['feature_id'], []).append(case)

    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)
                page.emulate_media(reduced_motion='reduce')

                # Rule 3 with case.switched: image-blend bl-qingqing-old-street + scene-snow-ridge -> bl-qingqing-snow-ridge
                page.goto(f'{origin}/Web_Comfyui/demo/#/studio/image-blend?case=bl-qingqing-old-street', wait_until='domcontentloaded', timeout=12000)
                ib_form = page.locator('form[data-demo-id="image-blend"]')
                ib_form.wait_for(timeout=12000)
                assert ib_form.get_attribute('data-demo-case') == 'bl-qingqing-old-street'
                ib_form.locator('label.dropzone[data-upload-id="image2"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                page.locator('.demo-folder-btn[data-folder="scenes"]').click()
                page.locator('.demo-disk-modal .demo-tile[data-material-id="scene-snow-ridge"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoCase === "bl-qingqing-snow-ridge"',
                    timeout=5000,
                )
                assert ib_form.locator('.demo-case-switch-note').is_visible()
                assert '已切換到使用這個素材的示範案例' in ib_form.locator('.demo-case-switch-note').inner_text()

                # Rule 4 nomatch.domain & nomatch.other from bl-qingqing-old-street; clicking a strip case clears both
                ib_form.locator('.demo-case-btn[data-case-id="bl-qingqing-old-street"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoCase === "bl-qingqing-old-street"',
                    timeout=5000,
                )
                ib_form.locator('label.dropzone[data-upload-id="image1"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                page.locator('.demo-folder-btn[data-folder="scenes"]').click()
                page.locator('.demo-disk-modal .demo-tile[data-material-id="scene-sakura-shrine"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "nomatch.domain"',
                    timeout=5000,
                )
                assert ib_form.locator('.generate-btn').is_disabled()
                assert ib_form.locator('.demo-gate-reason').is_visible()

                ib_form.locator('.demo-case-btn[data-case-id="bl-qingqing-old-street"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "ready"',
                    timeout=5000,
                )
                assert not ib_form.locator('.demo-gate-reason').is_visible()

                ib_form.locator('label.dropzone[data-upload-id="image1"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                page.locator('.demo-folder-btn[data-folder="people"]').click()
                page.locator('.demo-disk-modal .demo-tile[data-material-id="portrait-qingqing"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "nomatch.other"',
                    timeout=5000,
                )
                assert ib_form.locator('.generate-btn').is_disabled()
                assert ib_form.locator('.demo-gate-reason').is_visible()
                ib_form.locator('.demo-case-btn[data-case-id="bl-qingqing-old-street"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "ready"',
                    timeout=5000,
                )
                assert not ib_form.locator('.demo-gate-reason').is_visible()

                # Rules 7 and 8: voice-speak zhiyuan-english -> vs-zhiyuan-english-clone; voice-clone library voice switch
                page.evaluate('() => { location.hash = "#/studio/voice-speak"; }')
                vs_form = page.locator('form[data-demo-id="voice-speak"]')
                vs_form.wait_for(timeout=12000)
                vs_form.locator('.post-mode-panel:not(.hidden) select').first.select_option('zhiyuan-english')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="voice-speak"]\')?.dataset.demoCase === "vs-zhiyuan-english-clone"',
                    timeout=5000,
                )

                page.evaluate('() => { location.hash = "#/studio/voice-clone"; }')
                vc_form = page.locator('form[data-demo-id="voice-clone"]')
                vc_form.wait_for(timeout=12000)
                vc_form.locator('.seg-btn').first.click()
                page.wait_for_function(
                    '() => [...document.querySelectorAll(\'form[data-demo-id="voice-clone"] .post-mode-panel:not(.hidden) select option\')].some(o => o.value === "zhiyuan-hello")',
                    timeout=5000,
                )
                vc_sel = vc_form.locator('.post-mode-panel:not(.hidden) select').first
                vc_sel.select_option('zhiyuan-hello')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="voice-clone"]\')?.dataset.demoCase === "vc-zhiyuan"',
                    timeout=5000,
                )
                vc_sel.select_option('qingqing-hello')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="voice-clone"]\')?.dataset.demoCase === "vc-qingqing"',
                    timeout=5000,
                )

                # 2x2 grid: ca-qingqing outputs 4 images in .demo-angles-grid
                page.evaluate('() => { location.hash = "#/studio/character-angles?case=ca-qingqing"; }')
                ang_form = page.locator('form[data-demo-id="character-angles"]')
                ang_form.wait_for(timeout=12000)
                ang_form.locator('.generate-btn').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="character-angles"] .app-demo\')?.dataset.state === "complete"',
                    timeout=5000,
                )
                assert ang_form.locator('.demo-angles-grid img').count() == 4

                # /api/characters returns exact 4 listed subjects and each character page + grid links roundtrip via .demo-char-back
                page.evaluate('() => { location.hash = "#/characters"; }')
                page.locator('.new-character-card').wait_for(timeout=12000)
                api_chars = page.evaluate('() => fetch("/api/characters").then(r => r.json())')
                api_char_ids = [c.get('id') for c in (api_chars.get('characters') if isinstance(api_chars, dict) else api_chars)]
                assert api_char_ids == expected_chars, api_char_ids

                for cid in expected_chars:
                    sub_id = cid.removeprefix('docs-character-')
                    page.evaluate('(c) => { location.hash = "#/c/" + c; }', cid)
                    page.locator('.docs-character-voice audio').wait_for(timeout=8000)
                    expected_case_ids = {
                        c['id']
                        for c in demo_set['cases']
                        if c.get('display') and c.get('kind') in ('recorded', 'prepared') and c.get('feature_id') != 'character-voice' and sub_id in (c.get('subjects') or [])
                    }
                    card_ids = {el.get_attribute('data-case-id') for el in page.locator('.docs-character-case-card').all()}
                    assert card_ids == expected_case_ids, (cid, card_ids, expected_case_ids)
                    first_href = page.locator('.docs-character-case-card a[href^="#/studio/"]').first.get_attribute('href')
                    page.locator('.docs-character-case-card a[href^="#/studio/"]').first.click()
                    page.locator('.demo-char-back').wait_for(timeout=8000)
                    assert page.locator('.demo-char-back').get_attribute('href') == f'#/c/{cid}', (cid, first_href)
                    page.locator('.demo-char-back').click()
                    page.locator('.docs-character-voice audio').wait_for(timeout=8000)

                # character-create: 6 listed people photos pickable; face-meiling disabled with cc.only-demo
                page.evaluate('() => { location.hash = "#/characters"; }')
                page.locator('.new-character-card').wait_for(timeout=8000)
                page.locator('.new-character-card').click()
                cc_form = page.locator('form[data-demo-id="character-create"]')
                cc_form.wait_for(timeout=8000)
                cc_form.locator('input[name="photos"]').click()
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                for mid in ('subject-qingqing', 'portrait-qingqing', 'subject-zhiyuan', 'portrait-zhiyuan', 'subject-xingyu', 'subject-chenyi'):
                    tile = page.locator(f'.demo-disk-modal .demo-tile[data-material-id="{mid}"]')
                    assert tile.count() == 1, mid
                    assert tile.get_attribute('aria-disabled') != 'true', mid
                meiling_tile = page.locator('.demo-disk-modal .demo-tile[data-material-id="face-meiling"]')
                assert meiling_tile.count() == 1
                assert meiling_tile.get_attribute('aria-disabled') == 'true'
                assert '示範站只能用示範人物照片建立角色' in meiling_tile.inner_text()
                page.locator('.sheet-close').click()
                page.wait_for_function("() => !document.querySelector('.demo-disk-modal')", timeout=5000)

                # /api/voices returns exact 6 library voices; no voice.group.other before joining; links & buttons exact
                api_voices = page.evaluate('() => fetch("/api/voices").then(r => r.json())')
                api_voice_ids = [v.get('id') for v in (api_voices.get('voices') if isinstance(api_voices, dict) else api_voices)]
                assert api_voice_ids == expected_voices, api_voice_ids

                page.evaluate('() => { location.hash = "#/studio/voice-library"; }')
                page.locator('form[data-docs-voice-form="voice-library"]').wait_for(timeout=12000)
                assert '其他示範聲音' not in page.locator('.demo-voice-library').inner_text()
                assert page.locator('.demo-voice-library .demo-voice-group').count() == 1
                assert page.locator('.demo-voice-library .demo-voice-card').count() == len(expected_voices)
                ref_hrefs = [a.get_attribute('href') for a in page.locator('.demo-voice-library a[href^="#/studio/voice-clone?case="]').all()]
                assert ref_hrefs == ['#/studio/voice-clone?case=vc-qingqing', '#/studio/voice-clone?case=vc-zhiyuan'], ref_hrefs
                speak_hrefs = [a.get_attribute('href') for a in page.locator('.demo-voice-library a[href^="#/studio/voice-speak?case="]').all()]
                assert len(speak_hrefs) == len(expected_voices), speak_hrefs
                for href in speak_hrefs:
                    target_case = href.split('case=')[-1]
                    page.evaluate('(h) => { location.hash = h; }', href)
                    page.wait_for_function(
                        '(cid) => document.querySelector(\'form[data-demo-id="voice-speak"]\')?.dataset.demoCase === cid',
                        arg=target_case,
                        timeout=5000,
                    )

                # Every recorded displayed case of every Studio case form: select, Generate, outputs + run record shown
                for feature_id, cases in recorded_by_feature.items():
                    for case in cases:
                        page.evaluate('(arg) => { location.hash = `#/studio/${arg.f}?case=${encodeURIComponent(arg.c)}`; }', {'f': feature_id, 'c': case['id']})
                        form = page.locator(f'form[data-demo-id="{feature_id}"]')
                        form.wait_for(timeout=12000)
                        page.wait_for_function(
                            '(arg) => document.querySelector(`form[data-demo-id="${arg.f}"]`)?.dataset.demoCase === arg.c && document.querySelector(`form[data-demo-id="${arg.f}"]`)?.dataset.demoGate === "ready"',
                            arg={'f': feature_id, 'c': case['id']},
                            timeout=5000,
                        )
                        form.locator('.generate-btn').click()
                        page.wait_for_function(
                            '(f) => document.querySelector(`form[data-demo-id="${f}"] .app-demo`)?.dataset.state === "complete"',
                            arg=feature_id,
                            timeout=5000,
                        )
                        rendered_outputs = form.locator('.demo-output [data-comparison-after] img, .demo-output .demo-gallery:not(.demo-comparison) img, .demo-output .demo-video-result video, .demo-output .demo-audio-track audio').count()
                        assert rendered_outputs == len(case['outputs']), (feature_id, case['id'], rendered_outputs, len(case['outputs']))
                        assert form.locator('.demo-output .demo-run-record').count() == 1, (feature_id, case['id'])
                        form.locator('.demo-output .demo-run-record summary').click()
                        rec_text = form.locator('.demo-output .demo-run-record pre').inner_text()
                        assert case['id'] in rec_text or case['run_id'] in rec_text, (feature_id, case['id'])

                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()


def test_u1c_dance_url_alias_rule3(tmp_path):
    playwright, browser_path, server, origin, _app_guide, _demo_set = _launch_site(tmp_path)
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)
                page.emulate_media(reduced_motion='reduce')
                page.goto(f'{origin}/Web_Comfyui/demo/#/studio/dance?case=dn-xingyu-energetic', wait_until='domcontentloaded', timeout=12000)
                form = page.locator('form[data-demo-id="dance"]')
                form.wait_for(timeout=12000)
                assert form.get_attribute('data-demo-case') == 'dn-xingyu-energetic'
                form.locator('[data-url-fetch="video"]').click()
                form.locator('.demo-url-alias-btn', has_text='tai-chi.mp4').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="dance"]\')?.dataset.demoCase === "dn-chenyi-taichi"',
                    timeout=5000,
                )
                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()


def test_u1c2_rule3_rule7_matching(tmp_path):
    playwright, browser_path, server, origin, _app_guide, _demo_set = _launch_site(tmp_path)
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)
                page.emulate_media(reduced_motion='reduce')

                def open_case(feature_id, case_id):
                    page.goto(
                        f'{origin}/Web_Comfyui/demo/#/studio/{feature_id}?case={case_id}',
                        wait_until='domcontentloaded',
                        timeout=12000,
                    )
                    form = page.locator(f'form[data-demo-id="{feature_id}"]')
                    form.wait_for(timeout=12000)
                    page.wait_for_function(
                        '(arg) => document.querySelector(`form[data-demo-id="${arg.f}"]`)?.dataset.demoCase === arg.c',
                        arg={'f': feature_id, 'c': case_id},
                        timeout=5000,
                    )
                    assert form.get_attribute('data-demo-case') == case_id
                    return form

                def pick_library(form, slot_id, material_id):
                    form.locator(f'[data-material-picker="{slot_id}"]').click()
                    page.locator('.demo-library-modal').wait_for(timeout=5000)
                    page.locator(f'.demo-library-modal .demo-tile[data-material-id="{material_id}"]').click()

                def assert_ready(form):
                    assert form.get_attribute('data-demo-gate') == 'ready'
                    assert not form.locator('.generate-btn').is_disabled()

                # (a) image-blend: pick subject-zhiyuan into image1 on snow-ridge -> bl-zhiyuan-snow-ridge
                form = open_case('image-blend', 'bl-qingqing-snow-ridge')
                pick_library(form, 'image1', 'subject-zhiyuan')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoCase === "bl-zhiyuan-snow-ridge"',
                    timeout=5000,
                )
                assert form.locator('.demo-case-switch-note').is_visible()
                assert_ready(form)

                # (b) image-blend: pick subject-xingyu into image1 on old-street -> bl-xingyu-sakura-shrine & image2 replaced
                form = open_case('image-blend', 'bl-qingqing-old-street')
                pick_library(form, 'image1', 'subject-xingyu')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoCase === "bl-xingyu-sakura-shrine"',
                    timeout=5000,
                )
                assert form.locator('[data-upload-id="image2"] input[type=file]').get_attribute('data-demo-material') == 'scene-sakura-shrine'
                assert form.locator('.demo-case-switch-note').is_visible()
                assert_ready(form)

                # (c) image-blend: pick subject-qingqing into image1 on old-street -> stays bl-qingqing-old-street, note hidden
                form = open_case('image-blend', 'bl-qingqing-old-street')
                pick_library(form, 'image1', 'subject-qingqing')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoCase === "bl-qingqing-old-street" '
                    '&& document.querySelector(\'form[data-demo-id="image-blend"]\')?.dataset.demoGate === "ready"',
                    timeout=5000,
                )
                assert form.locator('.demo-case-switch-note').is_hidden()
                assert_ready(form)

                # (d) face-swap: select detection -> YOLOv5l is an edit (rule 5: non-differing key)
                form = open_case('face-swap', 'fs-qingqing-meiling')
                form.locator('details.advanced > summary').click()
                form.locator('select[data-field-id="detection"]').select_option('YOLOv5l')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="face-swap"]\')?.dataset.demoEdited === "true"',
                    timeout=5000,
                )
                assert form.get_attribute('data-demo-case') == 'fs-qingqing-meiling'
                assert form.locator('.demo-case-switch-note').is_hidden()
                assert_ready(form)

                # (e) h3-text-video: select aspect -> square is an edit (rule 5: non-differing key)
                form = open_case('h3-text-video', 'h3t-xingyu-sky')
                form.locator('select[data-field-id="aspect"]').select_option('square')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="h3-text-video"]\')?.dataset.demoEdited === "true"',
                    timeout=5000,
                )
                assert form.get_attribute('data-demo-case') == 'h3t-xingyu-sky'
                assert_ready(form)

                # (f) voxcpm-tts: language English -> vt-english-welcome, then Chinese Mandarin -> vt-warm-welcome
                form = open_case('voxcpm-tts', 'vt-warm-welcome')
                form.locator('select[data-field-id="language"]').select_option('English')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="voxcpm-tts"]\')?.dataset.demoCase === "vt-english-welcome"',
                    timeout=5000,
                )
                assert form.locator('.demo-case-switch-note').is_visible()
                assert_ready(form)
                form.locator('select[data-field-id="language"]').select_option('Chinese Mandarin')
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="voxcpm-tts"]\')?.dataset.demoCase === "vt-warm-welcome"',
                    timeout=5000,
                )
                assert_ready(form)

                # (g) face-swap: card group click is an edit (rule 5)
                form = open_case('face-swap', 'fs-qingqing-meiling')
                form.locator('[data-field-id="restore"] .mode-card[data-value="GFPGANv1.4.pth"]').click()
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-demo-id="face-swap"]\')?.dataset.demoEdited === "true"',
                    timeout=5000,
                )
                assert form.get_attribute('data-demo-case') == 'fs-qingqing-meiling'
                assert_ready(form)

                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()


def test_u1c2_voice_library_dropzone(tmp_path):
    playwright, browser_path, server, origin, _app_guide, demo_set = _launch_site(tmp_path)
    disk_voice_mat = next(m for m in demo_set['materials'] if m.get('kind') == 'audio' and m.get('folder') == 'voices')
    try:
        with playwright.sync_playwright() as driver:
            with driver.chromium.launch(executable_path=browser_path, args=['--no-sandbox', '--disable-dev-shm-usage']) as instance:
                page, errors, external, filechoosers = _new_monitored_page(instance, origin)
                page.emulate_media(reduced_motion='reduce')
                route = f'{origin}/Web_Comfyui/demo/#/studio/voice-library'
                page.goto(route, wait_until='domcontentloaded', timeout=12000)
                vl_form = page.locator('form[data-docs-voice-form="voice-library"]')
                vl_form.wait_for(timeout=12000)

                dropzone = vl_form.locator('label.dropzone.upload-slot[data-upload-id="voice"]')
                assert dropzone.count() == 1
                page.wait_for_function(
                    '() => document.querySelector(\'form[data-docs-voice-form="voice-library"] label.dropzone[data-upload-id="voice"]\')?.getAttribute("tabindex") === "0"',
                    timeout=5000,
                )
                assert dropzone.get_attribute('tabindex') == '0'
                file_input = dropzone.locator('input[type=file]')
                assert file_input.count() == 1
                assert not file_input.is_visible()
                assert dropzone.locator('.drop-text').inner_text() == '點一下，從示範硬碟選聲音'

                dropzone.focus()
                page.keyboard.press('Enter')
                page.locator('.demo-disk-modal').wait_for(timeout=5000)
                page.locator(f'.demo-disk-modal .demo-tile[data-material-id="{disk_voice_mat["id"]}"]').click()
                page.wait_for_function(
                    '() => Boolean(document.querySelector(\'form[data-docs-voice-form="voice-library"] label.dropzone[data-upload-id="voice"] .demo-slot-preview audio\'))',
                    timeout=5000,
                )
                assert dropzone.locator('.demo-slot-preview audio').count() == 1

                # Fresh visit to verify English drop-text translation before preview replaces the hint
                page.goto('about:blank', wait_until='domcontentloaded', timeout=12000)
                page.goto(route, wait_until='domcontentloaded', timeout=12000)
                vl_form = page.locator('form[data-docs-voice-form="voice-library"]')
                vl_form.wait_for(timeout=12000)
                page.locator('#languageSelect').select_option('en')
                page.wait_for_function("() => document.documentElement.lang === 'en'", timeout=5000)
                assert vl_form.locator('label.dropzone[data-upload-id="voice"] .drop-text').inner_text() == 'Click to pick a voice from the demo disk'

                page.set_viewport_size({'width': 390, 'height': 844})
                page.reload(wait_until='domcontentloaded', timeout=12000)
                vl_form = page.locator('form[data-docs-voice-form="voice-library"]')
                vl_form.wait_for(timeout=12000)
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')

                _assert_clean_page(page, errors, external, filechoosers)
    finally:
        server.shutdown()
        server.server_close()
