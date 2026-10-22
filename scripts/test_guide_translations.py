"""Ensure the English guide dictionary covers actual, public-facing guide text."""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webui'))
CJK = re.compile(r'[\u4e00-\u9fff]')


def strings(value):
    if isinstance(value, str):
        if CJK.search(value):
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def translations():
    return json.loads((ROOT / 'manifests/app_guides/en.json').read_text())


def test_native_guide_dictionary_matches_source():
    from scripts.build_guide_translations import render
    assert (ROOT / 'webui/static/guide-translations-en.js').read_text() == render(ROOT)


def test_all_application_guide_prose_has_english():
    source = json.loads((ROOT / 'manifests/app_guides/catalog.json').read_text())
    mapping = translations()
    assert len(source['features']) == 23
    assert set(strings(source)) <= mapping.keys()
    assert all(isinstance(key, str) and isinstance(value, str) and value.strip() for key, value in mapping.items())
    assert not [value for value in mapping.values() if CJK.search(value)]


def test_character_creation_guide_has_english():
    js = (ROOT / 'webui/docs/docs.js').read_text()
    block = js.split('const characterGuide = {', 1)[1].split('\n  };', 1)[0]
    phrases = set(re.findall(r"'([^'\n]*[\u4e00-\u9fff][^'\n]*)'", block))
    assert len(phrases) >= 9
    assert phrases <= translations().keys()


def test_all_current_library_guides_including_composed_notes_have_english():
    from workflow_library import catalog
    entries = catalog()
    assert len(entries) >= 248
    phrases = {phrase for entry in entries for phrase in strings(entry['guide'])}
    assert phrases <= translations().keys()


def test_model_identifiers_and_evidence_are_not_replaced_with_runtime_claims():
    mapping = translations()
    assert 'Z-Image Turbo' in mapping['文生圖按鈕使用 Z-Image Turbo 程式圖；文字編碼後從空白 latent 採樣，再用 AE 解碼為圖片。預設不套用 LoRA。']
    assert 'inventory-only' in mapping['僅依節點清單作 inventory-only 推測；未讀取可執行圖，無法確認連線順序或採樣設定。'].lower()
    assert 'No GPU' in mapping['已靜態讀取提供的圖及被引用的子圖；步驟是技術階段說明，不是逐條連線的執行追蹤。未做 GPU 生成驗證。']
    assert mapping['官方參考模板；應用程式使用原生 Python builder'] == 'Official reference template; the application uses a native Python builder'
