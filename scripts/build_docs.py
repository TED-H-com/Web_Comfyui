#!/usr/bin/env python3
"""Build the public, offline-readable workflow guide from reviewed repository inputs.

No running server, user data, credentials or network access are used. The output
is a deployable directory, not the source checkout. Only licensed, hash-pinned
workflow files from the two source manifests are eligible for download.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webui.workflow_library import catalog
from scripts.check_public_release import inspect
from scripts.public_demo_assets import PUBLIC_DEMO_ASSETS

MARKER = '.web-comfyui-docs'
ASSETS = {
    'webui/docs/landing.css': 'assets/landing.css',
    'webui/docs/landing.js': 'assets/landing.js',
    'webui/docs/hardware.js': 'assets/hardware.js',
    'webui/docs/hardware.css': 'assets/hardware.css',
    'webui/docs/autoip-referral.svg': 'assets/autoip-referral.svg',
    'webui/docs/docs.css': 'assets/docs.css',
    'webui/docs/docs.js': 'assets/docs.js',
    'webui/docs/explorers.js': 'assets/explorers.js',
    'webui/docs/explorers.css': 'assets/explorers.css',
    'webui/docs/tour.js': 'assets/tour.js',
    'webui/static/app.js': 'assets/app.js',
    'webui/static/i18n.js': 'assets/i18n.js',
    'webui/static/i18n.css': 'assets/i18n.css',
    'webui/static/translations-en.js': 'assets/translations-en.js',
    'webui/static/guide-translations-en.js': 'assets/guide-translations-en.js',
    'webui/static/login-return.js': 'assets/login-return.js',
    'webui/static/provider-fabric.js': 'assets/provider-fabric.js',
    'webui/static/provider-fabric.css': 'assets/provider-fabric.css',
    'webui/static/project-preview.js': 'assets/project-preview.js',
    'webui/static/project-preview.css': 'assets/project-preview.css',
    'webui/static/styles.css': 'assets/styles.css',
    'webui/static/workflow-library.css': 'assets/workflow-library.css',
    'webui/static/workflow-library.js': 'assets/workflow-library.js',
    'LICENSE': 'licenses/project-GPL-3.0.txt',
    'third_party/community/dseditor/NOTICE.md': 'licenses/dseditor-NOTICE.md',
    'webui/docs/examples/provenance.json': 'assets/examples/provenance.json',
}
ASSETS.update({'webui/docs/examples/' + name: 'assets/examples/' + name for name in PUBLIC_DEMO_ASSETS})
HTML_SOURCE = 'webui/static/index.html'
LANDING_SOURCE = 'webui/docs/landing.html'
FIELDS = {'id', 'title', 'category', 'source', 'kind', 'available', 'format', 'tags',
          'node_types', 'node_count', 'models', 'license', 'upstream_url', 'source_urls',
          'status', 'requirements', 'settings', 'validation', 'guide'}


def source_bytes(root: Path, relative: str) -> bytes:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Source must be a repository-relative path')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Symlink source is not publishable: ' + relative)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Source missing or outside repository: ' + relative)
    data = path.read_bytes()
    if inspect(Path(relative), data):
        raise ValueError('Source failed release scan: ' + relative)
    return data


def source_json(root, relative):
    return json.loads(source_bytes(root, relative))


def site_html(root):
    """Use the real application markup and scripts, with a read-only adapter."""
    html = source_bytes(root, HTML_SOURCE).decode('utf-8')
    html = html.replace('/static/', '../assets/')
    html = html.replace('<title>Web ComfyUI</title>', '<title>Web ComfyUI · 介面示範</title>')
    html = html.replace('class="auth-gate"', 'class="auth-gate hidden"')
    policy = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'none'"
    html = html.replace('</head>', '<meta http-equiv="Content-Security-Policy" content="' + policy + '">\n<link rel="stylesheet" href="../assets/docs.css">\n<link rel="stylesheet" href="../assets/explorers.css">\n<link rel="stylesheet" href="../assets/hardware.css">\n</head>')
    banner = '''<aside class="app-docs-banner" id="docsBanner"><div><strong data-i18n-zh="介面示範 · 非實際服務" data-i18n-en="Interface demo · Not a live service">介面示範 · 非實際服務</strong><p data-i18n-zh="生成按鈕只展示預製成果，不執行模型、不上傳檔案。實際創作請自行安裝 WebUI，或使用 AutoIP 支援的雲端服務。" data-i18n-en="Generate shows prepared examples. No models run and no files are uploaded. Install the WebUI for real work, or use supported AutoIP cloud services.">生成按鈕只展示預製成果，不執行模型、不上傳檔案。實際創作請自行安裝 WebUI，或使用 AutoIP 支援的雲端服務。</p></div><nav aria-label="Demo information"><a class="ghost small workflow-link" href="../" data-i18n-zh="專案介紹" data-i18n-en="Project overview">專案介紹</a><button id="docsHelpButton" class="ghost small" type="button">安裝與使用說明</button><a class="ghost small workflow-link" href="https://autoip.app/register?ref=TEDH2FND&amp;utm_source=affiliate&amp;utm_medium=referral&amp;utm_campaign=TEDH2FND" target="_blank" rel="sponsored noopener noreferrer" data-i18n-zh="沒有顯卡？AutoIP 推薦連結" data-i18n-en="No GPU? AutoIP referral">沒有顯卡？AutoIP 推薦連結</a></nav></aside>'''
    html = html.replace('</header>', '</header>' + banner, 1)
    html = html.replace('<script src="../assets/login-return.js', '<script src="../assets/docs.js"></script>\n    <script src="../assets/explorers.js"></script>\n    <script src="../assets/hardware.js"></script>\n    <script src="../assets/tour.js"></script>\n    <script src="../assets/login-return.js', 1)
    return html.encode('utf-8')


def landing_html(root):
    html = source_bytes(root, LANDING_SOURCE).decode('utf-8')
    policy = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'none'; img-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'"
    html = html.replace('</head>', '<meta http-equiv="Content-Security-Policy" content="' + policy + '">\n</head>')
    return html.encode('utf-8')


def public_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only HTTPS source URLs without credentials are publishable')
    host = parsed.hostname.lower()
    if host in {'localhost', 'localhost.localdomain'} or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Local source URL is not publishable')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('Private source URL is not publishable')
    return value


def build(root: Path = ROOT, output: Path | None = None) -> dict:
    root = Path(root).absolute()
    output = Path(output or root / '.site').absolute()
    if output == root or output in root.parents or output.is_symlink():
        raise ValueError('Output must be a separate, non-symlink build directory')
    if any(parent.is_symlink() for parent in output.parents):
        raise ValueError('Output parent must not be a symlink')
    if output.exists() and (not output.is_dir() or not (output / MARKER).is_file()):
        raise ValueError('Refusing to replace a directory without the documentation build marker')
    community = source_json(root, 'manifests/community_workflows/catalog.json')
    coverage = source_json(root, 'manifests/workflow_coverage/imported_sources.json')
    source_json(root, 'manifests/community_recipes/recipes.json')
    from scripts.app_guide_catalog import SOURCE_FILES, catalog as app_catalog
    for relative in SOURCE_FILES:
        source_bytes(root, relative)
    app_guide = app_catalog(root)
    from scripts.app_graph_catalog import SOURCE_FILES as GRAPH_SOURCES, graphs
    from scripts.demo_catalog import demos
    for relative in GRAPH_SOURCES:
        source_bytes(root, relative)
    app_guide['graphs'] = graphs(root, app_guide)
    app_guide['demos'] = demos(app_guide)
    app_guide['translations'] = source_json(root, 'manifests/app_guides/en.json')
    from scripts.hardware_catalog import catalog as hardware_catalog
    app_guide['hardware'] = hardware_catalog(app_guide)
    approved = {}
    records = []
    licenses = {}
    for item in community['workflows']:
        if not item.get('local_path'):
            continue
        source = community['sources'][item['source']]
        if not source.get('license') or not source.get('license_local_path'):
            raise ValueError('Download has no redistribution license')
        record = {'path': item['local_path'], 'sha256': item['local_sha256'],
                  'source_url': public_url(item['upstream_url']), 'source_revision': source['commit'],
                  'license': source['license'], 'license_file': source['license_local_path'],
                  'modifications': item.get('notes', [])}
        approved[record['path']] = record
        licenses[record['license_file']] = source.get('license_sha256')
    for row in coverage['records']:
        if not row.get('destination'):
            continue
        if not row.get('license') or not row.get('license_file'):
            raise ValueError('Download has no redistribution license')
        record = {'path': row['destination'], 'sha256': row['distributed_sha256'],
                  'source_url': public_url(row['source_url']), 'source_revision': row['source_revision'],
                  'license': row['license'], 'license_file': row['license_file'],
                  'modifications': row.get('modifications', [])}
        approved[record['path']] = record
        licenses.setdefault(record['license_file'], None)
    graph_bytes = {}
    for relative, record in approved.items():
        if not relative.startswith('workflows/') or Path(relative).suffix != '.json':
            raise ValueError('Unexpected workflow path')
        data = source_bytes(root, relative)
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Workflow hash mismatch: ' + relative)
        graph_bytes[relative] = data
    license_bytes = {}
    for relative, digest in licenses.items():
        if not relative.startswith('third_party/') or Path(relative).name != 'LICENSE':
            raise ValueError('Unexpected license path')
        data = source_bytes(root, relative)
        if digest and hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('License hash mismatch')
        license_bytes[relative] = data
    items = []
    entries = catalog(root, include_discovered=False)
    for entry in entries:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', entry['id']):
            raise ValueError('Invalid catalog ID')
        item = {key: value for key, value in entry.items() if key in FIELDS}
        if item.get('upstream_url'):
            public_url(item['upstream_url'])
        for url in item.get('source_urls', []):
            public_url(url)
        if entry.get('available'):
            relative = entry.get('local_path')
            if relative not in approved:
                raise ValueError('Unreviewed workflow is not publishable')
            item['download_url'] = './downloads/' + item['id'] + '.json'
            record = dict(approved[relative])
            record['download_url'] = item['download_url']
            record['license_url'] = './licenses/' + record['license_file'] + '.txt'
            record.pop('path')
            records.append(record)
        items.append(item)
    if len({row['id'] for row in items}) != len(items):
        raise ValueError('Duplicate catalog IDs')
    if len(records) != len(approved):
        raise ValueError('A reviewed workflow is absent from the built catalog')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.docs-build-', dir=output.parent) as scratch:
        stage = Path(scratch) / 'site'; stage.mkdir()
        def write(relative, data):
            if inspect(Path(relative), data):
                raise ValueError('Artifact failed release scan: ' + relative)
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        for source, destination in ASSETS.items():
            write(destination, source_bytes(root, source))
        write('index.html', landing_html(root))
        write('demo/index.html', site_html(root))
        write('app-guide.json', (json.dumps(app_guide, ensure_ascii=False, indent=2) + '\n').encode())
        for relative, data in license_bytes.items():
            write('licenses/' + relative + '.txt', data)
        for entry, item in zip(entries, items):
            if item.get('download_url'):
                write(item['download_url'].removeprefix('./'), graph_bytes[entry['local_path']])
        payload = {'schema_version': 1, 'mode': 'documentation', 'gpu_tested': False, 'items': items}
        write('catalog.json', (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode())
        write('sources.json', (json.dumps({'records': records}, ensure_ascii=False, indent=2) + '\n').encode())
        notice = 'Web ComfyUI documentation and source: https://github.com/TED-H-com/Web_Comfyui\nProject: GPL-3.0 (licenses/project-GPL-3.0.txt)\nWorkflow authors, revisions, modifications and individual licenses: sources.json\nThis site has no generation service. GPU execution has not been verified.\n\n'
        notice += '\n'.join(sorted('licenses/' + relative + '.txt' for relative in licenses))
        notice += '\nlicenses/dseditor-NOTICE.md\n'
        write('NOTICE.txt', notice.encode())
        write('.nojekyll', b'')
        write(MARKER, b'Generated documentation build; safe to replace.\n')
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
    return {'items': len(items), 'downloads': len(records), 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.site')
    args = parser.parse_args()
    print(json.dumps(build(output=args.output)))


if __name__ == '__main__':
    main()
