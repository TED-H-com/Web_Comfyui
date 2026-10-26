"""Static publishing must use reviewed source inputs, never runtime discovery."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import app_guide_catalog, build_docs

MANIFESTS = (
    'manifests/community_workflows/catalog.json',
    'manifests/workflow_coverage/imported_sources.json',
    'manifests/community_recipes/recipes.json',
    'manifests/app_guides/en.json',
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def source(tmp_path):
    """Copy only the reviewed build inputs into the disk-based pytest fixture."""
    root = tmp_path / 'source'
    paths = set(build_docs.ASSETS) | set(MANIFESTS) | set(app_guide_catalog.SOURCE_FILES) | {build_docs.HTML_SOURCE, build_docs.LANDING_SOURCE}
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


def test_execution_counts_derive_from_receipts_and_do_not_count_failed_or_prepared_examples():
    records = {'runs': [{'id': 'gpu', 'status': 'success', 'device': 'test GPU'},
                        {'id': 'failed', 'status': 'error', 'device': 'test GPU'}],
               'audio_runs': [{'id': 'speech', 'status': 'success', 'device': 'CPU'}],
               'prepared_examples': [{'id': 'prepared', 'status': 'success'}]}
    summary = build_docs.provenance_summary(records)
    assert summary['records'] == {'runs': 2, 'audio_runs': 1}
    assert summary['successful_runs_by_device'] == {'CPU': 1, 'test GPU': 1}
    records['runs'].append({'id': 'second', 'status': 'success', 'device': 'test GPU'})
    assert build_docs.provenance_summary(records)['successful_runs'] == 3
    records['audio_runs'].append({'id': 'gpu', 'status': 'success', 'device': 'CPU'})
    with pytest.raises(ValueError, match='Duplicate'):
        build_docs.provenance_summary(records)


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
    approved = {row['local_path'] for row in read_json(source / MANIFESTS[0])['workflows'] if row.get('local_path')}
    approved.update(row['destination'] for row in read_json(source / MANIFESTS[1])['records'] if row.get('destination'))
    assert result['downloads'] == len(downloads) == len(records) == len(approved)
    assert payload['mode'] == 'documentation'
    assert payload['gpu_tested'] is False
    expected = set(build_docs.ASSETS.values()) | {
        'index.html', 'demo/index.html', 'app-guide.json', 'execution-summary.json', 'catalog.json', 'sources.json', 'NOTICE.txt', '.nojekyll', build_docs.MARKER,
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
    summary = read_json(output / 'execution-summary.json')
    assert summary == read_json(output / 'app-guide.json')['execution_summary']
    provenance = read_json(source / 'webui/docs/examples/provenance.json')
    assert summary['records']['runs'] == len(provenance['runs'])
    assert summary['records']['audio_runs'] == len(provenance['audio_runs'])
    for original, destination in build_docs.ASSETS.items():
        assert (output / destination).read_bytes() == (source / original).read_bytes()
    for row in payload['items']:
        assert 'local_path' not in row
        assert set(row) <= build_docs.FIELDS | {'download_url'}
        if row.get('source') == 'ecjojo':
            assert not row['available'] and 'download_url' not in row
    assert 'sources.json' in (output / 'NOTICE.txt').read_text()
    app = read_json(output / 'app-guide.json')
    assert len(app['features']) == len(app['guides']) == 23
    assert set(app['graphs']) == set(app['demos']) == {row['id'] for row in app['features']} | {'character-create'}
    assert {row['id'] for row in app['features']} == {row['id'] for row in app['guides']}
    assert not any(relative.endswith('.py') for relative in artifact_files(output))


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


@pytest.mark.parametrize('kind', ['graph', 'graph-parent', 'asset', 'license', 'app-source', 'app-manifest'])
def test_symlink_inputs_are_rejected(source, tmp_path, kind):
    row = first_download(source)
    if kind == 'graph':
        path = source / row['local_path']
    elif kind == 'graph-parent':
        path = (source / row['local_path']).parent
    elif kind == 'asset':
        path = source / build_docs.HTML_SOURCE
    elif kind == 'app-source':
        path = source / 'webui/server.py'
    elif kind == 'app-manifest':
        path = source / 'manifests/app_guides/catalog.json'
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


@pytest.mark.parametrize('relative', ['webui/docs/docs.js', 'webui/server.py'])
def test_credential_shaped_source_is_rejected_without_echoing_value(source, tmp_path, relative):
    synthetic = 'gh' + 'p_' + '0' * 36
    path = source / relative
    path.write_text(path.read_text() + '\n' + json.dumps(synthetic) + '\n')
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


def test_static_shell_preserves_app_navigation_and_loads_adapter_first(source, tmp_path):
    output = tmp_path / 'site'
    build_docs.build(source, output)
    class References(HTMLParser):
        def __init__(self):
            super().__init__()
            self.references = []
            self.tags = []
            self.views = []
            self.ids = []
            self.scripts = []
        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            values = dict(attrs)
            if 'data-view' in values:
                self.views.append(values['data-view'])
            if 'id' in values:
                self.ids.append(values['id'])
            if tag == 'script':
                self.scripts.append(values.get('src'))
            for key, value in attrs:
                if key in ('src', 'href') and value:
                    self.references.append(value)
    html = (output / 'demo/index.html').read_text()
    parsed = References()
    parsed.feed(html)
    original = References()
    original.feed((source / build_docs.HTML_SOURCE).read_text())
    assert parsed.views == original.views
    assert set(original.ids) <= set(parsed.ids)
    assert 'form' in parsed.tags and 'input' in parsed.tags
    assert 'id="authGate" class="auth-gate hidden"' in html
    for reference in parsed.references:
        if reference.startswith('#') or urlsplit(reference).scheme:
            continue
        assert not reference.startswith('/')
        path = output / 'demo' / urlsplit(reference).path
        assert path.is_file() or (path / 'index.html').is_file(), reference
    assert "connect-src 'self'" in html
    assert "form-action 'none'" in html
    script_paths = [urlsplit(url).path for url in parsed.scripts]
    assert script_paths[0] == '../assets/docs.js'
    assert script_paths[1:5] == ['../assets/explorers.js', '../assets/hardware.js', '../assets/character-session.js', '../assets/tour.js']
    assert script_paths[5:] == [urlsplit(url).path.replace('/static/', '../assets/') for url in original.scripts]
    for url in parsed.scripts:
        asset = output / 'demo' / urlsplit(url).path
        assert urlsplit(url).query == 'v=' + hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
    assert {'assets/app.js', 'assets/login-return.js', 'assets/provider-fabric.js', 'assets/project-preview.js'} <= artifact_files(output)


def test_project_homepage_is_separate_from_simulated_app(source, tmp_path):
    output = tmp_path / 'site'; build_docs.build(source, output)
    home = (output / 'index.html').read_text()
    demo = (output / 'demo/index.html').read_text()
    assert 'href="./demo/"' in home
    assert 'id="workspace"' not in home
    assert 'assets/app.js' not in home and 'assets/docs.js' not in home
    assert "connect-src 'none'" in home
    assert 'TEDH2FND' in home and 'rel="sponsored noopener noreferrer"' in home
    assert 'autoip-referral.svg' in home
    assert '介面示範 · 非實際服務' in demo and 'Not a live service' in demo
    assert 'href="../"' in demo
    hardware = read_json(output / 'app-guide.json')['hardware']
    assert hardware and 'none' in {profile['id'] for profile in hardware['profiles']}


@pytest.mark.parametrize('field', ['source', 'templates', 'source_files'])
def test_app_guide_manifest_cannot_select_undeclared_source_files(source, tmp_path, field):
    outside = tmp_path / 'outside.py'
    outside.write_text('raise AssertionError("Must not read or execute external source")')
    manifest = source / 'manifests/app_guides/catalog.json'
    data = read_json(manifest)
    spec = next(iter(data['features'].values()))
    spec[field] = str(outside) if field == 'source' else [{'path': str(outside), 'role': 'external'}] if field == 'templates' else [str(outside)]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        build_docs.build(source, tmp_path / 'site')
    assert not (tmp_path / 'site').exists()


def test_server_source_is_parsed_without_execution_or_environment_reads(source, tmp_path, monkeypatch):
    server = source / 'webui/server.py'
    server.write_text(server.read_text() + '\nraise AssertionError("SERVER_MUST_NOT_EXECUTE")\n')
    monkeypatch.setenv('VOICE_SPEAK_MAX_CHARS', 'PRIVATE_ENV_MUST_NOT_BE_READ')
    monkeypatch.setenv('CASTLE_VOICE_LAB_ENV', str(tmp_path / 'no-such-private-env'))
    output = tmp_path / 'site'
    build_docs.build(source, output)
    assert 'SERVER_MUST_NOT_EXECUTE' not in (output / 'app-guide.json').read_text()
    assert 'PRIVATE_ENV_MUST_NOT_BE_READ' not in (output / 'app-guide.json').read_text()


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for adapter isolation checks')
def test_docs_adapter_rejects_mutations_and_never_fetches_unknown_or_local_urls(source):
    # A small DOM substitute tests the network boundary itself. Full navigation,
    # accessibility and event behavior are covered by the browser smoke test.
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const calls = [], listeners = {};
const password = {value: 'synthetic-old-value', disabled: false};
const app = {studios: [], features: [], guides: []};
const library = {items: []};
const window = {addEventListener() {}, fetch: async (url, options) => {
  calls.push({url: String(url), options});
  return new Response(JSON.stringify(String(url).endsWith('app-guide.json') ? app : library));
}};
for (const key of ['localStorage', 'sessionStorage']) Object.defineProperty(window, key, {
  configurable: true, get() {throw Error('Persistent storage must not be read');}
});
const context = {window, URL, Request, Response, console, setTimeout,
  location: {href: 'https://example.test/Web_Comfyui/demo/', origin: 'https://example.test', hash: '#/studio/text2img'},
  document: {documentElement: {}, querySelectorAll: () => [password], getElementById: () => null,
    addEventListener: (name, callback) => {listeners[name] = callback;}},
  history: {replaceState() {}}, navigator: {sendBeacon() {throw Error('Native beacon must not run');}},
  MutationObserver: class {observe() {}}
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  await window.WebComfyDocs.ready;
  assert.equal(calls.length, 2);
  assert.deepEqual(calls.map(call => call.url).sort(), [
    'https://example.test/Web_Comfyui/app-guide.json', 'https://example.test/Web_Comfyui/catalog.json'
  ]);
  assert.ok(calls.every(call => call.options.credentials === 'omit'));
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    assert.equal((await window.fetch('/api/generate', {method, body: 'synthetic-data'})).status, 405);
  }
  assert.equal((await window.fetch(new Request('https://example.test/api/generate', {method: 'POST'}))).status, 405);
  assert.equal((await window.fetch('/api/unrecognized-private-api')).status, 404);
  assert.equal((await window.fetch('http://127.0.0.1:8188/object_info')).status, 405);
  assert.equal((await window.fetch('https://external.example/api')).status, 405);
  assert.equal((await window.fetch('/api/characters')).status, 200);
  assert.equal(calls.length, 2);
  assert.equal(context.navigator.sendBeacon('/api/write', 'data'), false);
  assert.throws(() => new window.WebSocket('ws://127.0.0.1:8188'));
  assert.throws(() => new window.XMLHttpRequest().open('POST', '/api/write'));
  new window.EventSource('/api/events').close();
  window.localStorage.setItem('draft', 'temporary');
  assert.equal(window.localStorage.getItem('draft'), 'temporary');
  assert.equal(window.sessionStorage.getItem('draft'), null);
  assert.equal(password.disabled, true);
  assert.equal(password.value, '');
  let prevented = false;
  listeners.beforeinput({target: {matches: () => true}, preventDefault() {prevented = true;}, stopImmediatePropagation() {}});
  assert.equal(prevented, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run(['node', '-e', script, str(source / 'webui/docs/docs.js')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
