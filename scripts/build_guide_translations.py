"""Keep local-app and Pages workflow-guide translations identical."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(root=ROOT):
    mapping = json.loads((root / 'manifests/app_guides/en.json').read_text(encoding='utf-8'))
    return ('/* Generated from manifests/app_guides/en.json; run scripts/build_guide_translations.py. */\n'
            'window.WebComfyI18n?.register(' + json.dumps(mapping, ensure_ascii=False, indent=2) + ');\n')


if __name__ == '__main__':
    (ROOT / 'webui/static/guide-translations-en.js').write_text(render(), encoding='utf-8')
