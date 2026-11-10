"""Pinned-source integration checks; optional local checkouts, no network/GPU.

Set COMMUNITY_DSEDITOR_SOURCE_DIR and COMMUNITY_ECJOJO_SOURCE_DIR to read-only
checkouts at the catalog revisions. Missing checkouts skip only source tests;
the catalog/download/native-import route tests always run on public fixtures.
No upstream Python, scripts, custom nodes or provider code is executed.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import urllib.error

import pytest

import community_workflows as cw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('source_test_library', ROOT / 'webui/workflow_library.py')
library = importlib.util.module_from_spec(spec)
spec.loader.exec_module(library)
CATALOG = json.loads((ROOT / cw.CATALOG).read_text())


def inventory(graph, *, v3=False):
    """Synthetic schema: it proves parsing/transport, not installed resources."""
    result = {kind: {} for kind in library.graph_details(graph)['node_types']}
    choices = {}
    for _, kind, field, name in library._model_selections(graph):
        choices.setdefault((kind, field), set())
        if name is not None:
            choices[kind, field].add(name)
    for (kind, field), names in choices.items():
        descriptor = ['COMBO', {'options': sorted(names)}] if v3 else [sorted(names)]
        result[kind].setdefault('input', {}).setdefault('required', {})[field] = descriptor
    return result


@pytest.fixture(scope='module')
def entries():
    return {entry['id']: entry for entry in library.catalog()}


@pytest.mark.parametrize('entry', CATALOG['workflows'], ids=lambda entry: entry['id'])
def test_pinned_source_catalog_inventory_and_import_policy(entry, entries, tmp_path, monkeypatch):
    source = CATALOG['sources'][entry['source']]
    setting = f"COMMUNITY_{entry['source'].upper()}_SOURCE_DIR"
    if not os.environ.get(setting):
        pytest.skip(f'{setting} is needed for pinned-source integration')
    source_dir = Path(os.environ[setting])
    monkeypatch.setattr(cw.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('network is forbidden'))
    # fetch verifies every exact catalog SHA256 before JSON parsing.
    graph = json.loads(cw.fetch(source, entry['upstream_path'], entry['upstream_sha256'], source_dir))
    details = library.graph_details(graph)
    listed = entries[entry['id']]
    review = listed['source_review']
    assert review['source_commit'] == source['commit']
    assert review['source_sha256'] == entry['upstream_sha256']
    assert review['gpu_tested'] is False
    active = set(details['node_types'])
    assert review['catalog_missing_active_node_types'] == sorted(active - set(entry['node_types']))
    assert review['catalog_inactive_node_types'] == sorted(set(entry['node_types']) - active)
    if not listed['available']:
        assert set(listed['node_types']) == active
    expected_missing = sorted(active - library.NON_EXECUTABLE)
    assert library.dependency_checks(entry, graph, {})['missing_nodes'] == expected_missing
    old = library.dependency_checks(entry, graph, inventory(graph))
    modern = library.dependency_checks(entry, graph, inventory(graph, v3=True))
    assert old == modern
    assert old['missing_nodes'] == [] and old['missing_models'] == []
    assert old['gpu_tested'] is False
    # Use the same strict current validator, keeping format refusals separate
    # from historical topology findings and from license/selection policy.
    if not review['import_format_supported']:
        with pytest.raises(ValueError, match='group nodes|UI-format'):
            cw.validate_graph(graph)
    elif review['topology_status'] == 'fail':
        with pytest.raises(ValueError, match='link'):
            cw.validate_graph(graph)
    else:
        cw.validate_graph(graph)
    if entry['distribution'] == 'vendored':
        output = cw.import_entry(tmp_path, entry, source, source_dir)
        assert cw.digest(output.read_bytes()) == entry['local_sha256']
        assert output.read_bytes() == (ROOT / entry['local_path']).read_bytes()
        assert cw.import_entry(tmp_path, entry, source, source_dir) == output
        assert cw.validate_entry(tmp_path, entry, source) == []
    else:
        monkeypatch.setattr(cw, 'fetch', lambda *a, **k: pytest.fail('reference import must fail before fetch'))
        expected = 'redistribution not authorized' if entry['source'] == 'ecjojo' else 'not selected for local import'
        with pytest.raises(ValueError, match=expected):
            cw.import_entry(tmp_path, entry, source, source_dir)
        assert not list(tmp_path.iterdir())


def handler_for(monkeypatch, request, entry_id=''):
    module = types.ModuleType('community_source_test_server')
    module.request_json = request
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Handler:
        def require_auth(self): return True
        def require_operator(self): return True
        def read_json(self): return {'id': entry_id, 'graph': {'untrusted': True}}
        def send_json(self, body, status=200, **kwargs): self.body, self.status = body, status
    Handler.__module__ = module.__name__
    return Handler()


def test_checked_catalog_route_surfaces_source_issues_and_only_checks_local_graphs(monkeypatch):
    calls = []
    def request(path):
        calls.append(path)
        return {}  # Connected synthetic ComfyUI with no installed nodes/models.
    handler = handler_for(monkeypatch, request)
    assert library.handle_get(handler, '/api/workflow-library', 'check=1')
    assert handler.status == 200 and calls == ['/object_info']
    assert handler.body['connection'] == {'checked': True, 'connected': True}
    rows = handler.body['items']
    assert len(rows) == 248
    assert sum(bool(row.get('source_review')) for row in rows) == 233
    assert sum(row.get('source_review', {}).get('topology_status') == 'fail' for row in rows) == 22
    for row in rows:
        if row['available']:
            assert row['gpu_tested'] is False
            assert 'dependency_status' in row
        else:
            assert 'dependency_status' not in row


@pytest.mark.parametrize('entry_id', [entry['id'] for entry in library.catalog()])
def test_every_library_item_downloads_and_native_imports_or_refuses_reference(entry_id, entries, monkeypatch):
    entry = entries[entry_id]
    calls, saved = [], {}
    graph = library.read_json(library.safe_path(entry['local_path'])) if entry['available'] else None
    info = inventory(graph, v3=True) if graph is not None else {}
    def request(path, payload=None, method=None):
        calls.append((path, method))
        if path == '/object_info':
            return info
        assert path.startswith('/userdata/workflows%2Fwebui-library%2F')
        target = path.split('?', 1)[0]
        if method == 'POST':
            assert path.endswith('?overwrite=false')
            if target in saved:
                raise urllib.error.HTTPError('http://example.invalid', 409, 'exists', {}, None)
            saved[target] = payload
            return {}
        return saved[target]
    # The real catalog is separately exercised above. Reuse its immutable
    # result to avoid reparsing all 46 local graphs for each selection.
    monkeypatch.setattr(library, 'catalog', lambda *args, **kwargs: list(entries.values()))
    handler = handler_for(monkeypatch, request, entry_id)
    library.handle_get(handler, '/api/workflow-library/file', 'id=' + entry_id)
    assert handler.status == (200 if entry['available'] else 400)
    if entry['available']:
        assert handler.body == graph
    library.handle_post(handler, '/api/workflow-library/open')
    if not entry['available']:
        assert handler.status == 400 and handler.body['ok'] is False
        assert calls == [] and saved == {}
        return
    assert handler.status == 200 and handler.body['ok'] is True
    assert handler.body['gpu_tested'] is False
    assert handler.body['missing_nodes'] == [] and handler.body['missing_models'] == []
    assert handler.body['already_imported'] is False
    assert list(saved.values()) == [graph]
    library.handle_post(handler, '/api/workflow-library/open')
    assert handler.status == 200 and handler.body['already_imported'] is True
    assert len(saved) == 1
    assert all('/prompt' not in path and 'overwrite=true' not in path for path, _ in calls)
