"""Static publishing must use reviewed source inputs, never runtime discovery."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_docs

MANIFESTS = (
    'manifests/community_workflows/catalog.json',
    'manifests/workflow_coverage/imported_sources.json',
    'manifests/community_recipes/recipes.json',
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def source(tmp_path):
    """Copy only the reviewed build inputs into the disk-based pytest fixture."""
    root = tmp_path / 'source'
    paths = set(build_docs.ASSETS) | set(MANIFESTS)
    community = read_json(ROOT / MANIFESTS[0])
    coverage = read_json(ROOT / MANIFESTS[1])
    for row in community['workflows']:
        if row.get('local_path'):
            paths.add(row['local_path'])
            paths.add(community['sources'][row['source']]['license_local_path'])
    for row in coverage['records']:
        if row.get('destination'):
            paths.update((row['destination'], row['license_file']))
    for relative in paths:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return root


def first_download(root):
    return next(row for row in read_json(root / MANIFESTS[0])['workflows'] if row.get('local_path'))


def artifact_files(output):
    return {path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()}


def test_artifact_contains_only_reviewed_graphs_assets_and_licenses(source, tmp_path, monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError('A documentation build must not access the network')
    monkeypatch.setattr(socket, 'create_connection', no_network)
    output = tmp_path / 'site'
    result = build_docs.build(source, output)
    payload = read_json(output / 'catalog.json')
    records = read_json(output / 'sources.json')['records']
    downloads = [row for row in payload['items'] if row.get('available')]
    assert result['downloads'] == len(downloads) == len(records) == 40
    assert payload['mode'] == 'documentation'
    assert payload['gpu_tested'] is False
    expected = set(build_docs.ASSETS.values()) | {
        'catalog.json', 'sources.json', 'NOTICE.txt', '.nojekyll', build_docs.MARKER,
    }
    for record in records:
        graph = record['download_url'].removeprefix('./')
        license_path = record['license_url'].removeprefix('./')
        expected.update((graph, license_path))
        assert hashlib.sha256((output / graph).read_bytes()).hexdigest() == record['sha256']
        assert (output / license_path).read_bytes() == (source / record['license_file']).read_bytes()
        assert record['source_revision'] and record['license']
        assert 'modifications' in record
        assert 'path' not in record
    assert artifact_files(output) == expected
    for original, destination in build_docs.ASSETS.items():
        assert (output / destination).read_bytes() == (source / original).read_bytes()
    for row in payload['items']:
        assert 'local_path' not in row
        assert set(row) <= build_docs.FIELDS | {'download_url'}
        if row.get('source') == 'ecjojo':
            assert not row['available'] and 'download_url' not in row
    assert 'sources.json' in (output / 'NOTICE.txt').read_text()


def test_untracked_workflows_runtime_data_and_extra_metadata_are_excluded(source, tmp_path):
    sentinel = 'UNREVIEWED_PRIVATE_FIXTURE_CONTENT'
    for relative in ('.env', 'users.json', 'characters/personal.json',
                     'workflows/user-saved/personal.json', 'workflows/untracked.json'):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'nodes': [], 'links': [], 'prompt': sentinel}))
    manifest = source / MANIFESTS[0]
    data = read_json(manifest)
    data['workflows'][0]['private_metadata'] = sentinel
    manifest.write_text(json.dumps(data))
    output = tmp_path / 'site'
    build_docs.build(source, output)
    assert all(sentinel.encode() not in (output / relative).read_bytes() for relative in artifact_files(output))
    assert not any('untracked' in row['title'] for row in read_json(output / 'catalog.json')['items'])


def test_rebuild_removes_stale_runtime_files(source, tmp_path):
    output = tmp_path / 'site'
    build_docs.build(source, output)
    (output / 'users.json').write_text('{"private":"stale runtime data"}')
    (output / '.env').write_text('PRIVATE_FIXTURE=value')
    build_docs.build(source, output)
    assert not (output / 'users.json').exists()
    assert not (output / '.env').exists()


@pytest.mark.parametrize('kind', ['graph', 'graph-parent', 'asset', 'license'])
def test_symlink_inputs_are_rejected(source, tmp_path, kind):
    row = first_download(source)
    if kind == 'graph':
        path = source / row['local_path']
    elif kind == 'graph-parent':
        path = (source / row['local_path']).parent
    elif kind == 'asset':
        path = source / 'webui/docs/index.html'
    else:
        community = read_json(source / MANIFESTS[0])
        path = source / community['sources'][row['source']]['license_local_path']
    outside = tmp_path / 'outside-input'
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        build_docs.build(source, tmp_path / 'site')
    assert not (tmp_path / 'site').exists()


def test_graph_hash_mismatch_is_rejected(source, tmp_path):
    path = source / first_download(source)['local_path']
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='Workflow hash mismatch'):
        build_docs.build(source, tmp_path / 'site')


def test_credential_shaped_source_is_rejected_without_echoing_value(source, tmp_path):
    synthetic = 'gh' + 'p_' + '0' * 36
    path = source / 'webui/docs/docs.js'
    path.write_text(path.read_text() + '\n// fixture: ' + synthetic + '\n')
    with pytest.raises(ValueError, match='Source failed release scan') as caught:
        build_docs.build(source, tmp_path / 'site')
    assert synthetic not in str(caught.value)
    assert not (tmp_path / 'site').exists()


def test_license_hash_mismatch_is_rejected(source, tmp_path):
    community = read_json(source / MANIFESTS[0])
    row = first_download(source)
    license_source = community['sources'][row['source']]
    assert license_source['license_sha256']
    path = source / license_source['license_local_path']
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='License hash mismatch'):
        build_docs.build(source, tmp_path / 'site')


def test_download_without_redistribution_license_is_rejected(source, tmp_path):
    manifest = source / MANIFESTS[0]
    data = read_json(manifest)
    data['sources'][first_download(source)['source']]['license'] = None
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='redistribution license'):
        build_docs.build(source, tmp_path / 'site')


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://example.com/source', 'https://fixture:password@example.com/source'])
def test_unsafe_source_urls_are_rejected(source, tmp_path, url):
    manifest = source / MANIFESTS[0]
    data = read_json(manifest)
    next(row for row in data['workflows'] if row.get('local_path'))['upstream_url'] = url
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='HTTPS source URLs'):
        build_docs.build(source, tmp_path / 'site')


@pytest.mark.parametrize('url', ['https://localhost/source', 'https://machine.local/source',
                               'https://127.0.0.1/source', 'https://10.1.2.3/source',
                               'https://[::1]/source'])
def test_local_source_endpoints_are_rejected(url):
    with pytest.raises(ValueError, match='source URL is not publishable'):
        build_docs.public_url(url)


def test_output_guard_preserves_unmarked_directory(source, tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    retained = output / 'valuable.txt'
    retained.write_text('keep this directory')
    with pytest.raises(ValueError, match='marker'):
        build_docs.build(source, output)
    assert retained.read_text() == 'keep this directory'
    with pytest.raises(ValueError):
        build_docs.build(source, source)


def test_symlink_output_parent_is_rejected(source, tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        build_docs.build(source, linked / 'site')
    assert not list(actual.iterdir())


def test_static_shell_uses_project_relative_assets_and_has_no_credentials_form(source, tmp_path):
    output = tmp_path / 'site'
    build_docs.build(source, output)
    class References(HTMLParser):
        def __init__(self):
            super().__init__()
            self.references = []
            self.tags = []
        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            for key, value in attrs:
                if key in ('src', 'href') and value:
                    self.references.append(value)
    html = (output / 'index.html').read_text()
    parsed = References()
    parsed.feed(html)
    assert 'form' not in parsed.tags and 'input' not in parsed.tags
    for reference in parsed.references:
        if reference.startswith('#') or urlsplit(reference).scheme:
            continue
        assert not reference.startswith('/')
        assert (output / reference).is_file(), reference
    assert "connect-src 'self'" in html
    assert 'docsMode: true' in (output / 'assets/docs.js').read_text()
    assert not {'assets/app.js', 'assets/login-return.js', 'assets/provider-fabric.js'} & artifact_files(output)
