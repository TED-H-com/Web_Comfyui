from pathlib import Path
import importlib.util
import json
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('workflow_library', ROOT / 'webui/workflow_library.py')
library = importlib.util.module_from_spec(spec)
spec.loader.exec_module(library)


def test_catalog_keeps_private_saved_files_out(tmp_path):
    workflows = tmp_path / 'workflows'
    workflows.mkdir()
    graph = {'nodes': [{'id': 1, 'type': 'KSampler'}], 'links': []}
    (workflows / 'public.json').write_text(json.dumps(graph))
    saved = workflows / 'user-saved'; saved.mkdir()
    (saved / 'personal.json').write_text(json.dumps(graph))
    (workflows / 'metadata.json').write_text('{"notes":"not a graph"}')
    entries = library.catalog(tmp_path)
    assert len(entries) == 1
    assert entries[0]['title'] == 'public'
    assert entries[0]['node_types'] == ['KSampler']


def test_catalog_does_not_follow_symlinks(tmp_path):
    workflows = tmp_path / 'workflows'; workflows.mkdir()
    outside = tmp_path / 'private.json'; outside.write_text('{"nodes":[],"links":[]}')
    (workflows / 'symlink.json').symlink_to(outside)
    assert library.catalog(tmp_path) == []


def test_reference_only_cannot_be_downloaded(tmp_path):
    path = tmp_path / 'manifests/community_workflows/catalog.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'workflows': [{'id': 'reference', 'title': 'Reference', 'distribution': 'links-only'}]}))
    assert library.catalog(tmp_path)[0]['available'] is False
    import pytest
    with pytest.raises(ValueError):
        library.selected('reference', tmp_path)


def test_missing_nodes_excludes_frontend_notes():
    assert library.missing_nodes({'node_types': ['Note', 'Reroute', 'KSampler', 'Unknown']}, {'KSampler': {}}) == ['Unknown']


def test_subgraph_dependencies_expand_and_models_ignore_private_paths():
    graph = {'nodes': [{'id': 1, 'type': 'subgraph-id'}], 'definitions': {'subgraphs': [
        {'id': 'subgraph-id', 'nodes': [
            {'type': 'KSampler'},
            {'type': 'UNETLoader', 'widgets_values': ['example/model.safetensors']},
            {'type': 'VAELoader', 'widgets_values': ['/home/example/private.safetensors']},
            {'type': 'LoraLoader', 'widgets_values': ['../outside.safetensors']},
        ]},
        {'id': 'unused', 'nodes': [{'type': 'UnusedCustomNode'}]},
    ]}}
    details = library.graph_details(graph)
    assert 'subgraph-id' not in details['node_types']
    assert 'UnusedCustomNode' not in details['node_types']
    assert library.missing_nodes(details, {'KSampler': {}, 'UNETLoader': {}, 'VAELoader': {}, 'LoraLoader': {}}) == []
    assert library.graph_models(graph) == [{'name': 'example/model.safetensors', 'node_type': 'UNETLoader', 'directory_hint': 'diffusion_models'}]


def test_routes_authenticate_before_reading(monkeypatch):
    class Handler:
        def require_auth(self): return False
        def read_json(self): raise AssertionError('body must not be read')
    monkeypatch.setattr(library, 'catalog', lambda: (_ for _ in ()).throw(AssertionError('must not list')))
    assert library.handle_get(Handler(), '/api/workflow-library', '')
    assert library.handle_post(Handler(), '/api/workflow-library/open')


def test_open_only_saves_catalog_graph_never_queues(monkeypatch):
    calls = []
    def request(path, payload=None, method=None):
        calls.append((path, payload, method))
        return {'KSampler': {}} if path == '/object_info' else {}
    module = types.ModuleType('workflow_test_server'); module.request_json = request
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Handler:
        def require_auth(self): return True
        def require_operator(self): return True
        def read_json(self): return {'id': 'safe', 'graph': {'do-not-trust': True}}
        def send_json(self, body, status=200): self.body, self.status = body, status
    Handler.__module__ = module.__name__
    graph = {'nodes': [{'id': 1, 'type': 'KSampler'}], 'links': []}
    monkeypatch.setattr(library, 'selected', lambda _: ({'id': 'safe', 'format': 'visual', 'node_types': ['KSampler']}, graph))
    handler = Handler(); library.handle_post(handler, '/api/workflow-library/open')
    assert handler.status == 200
    assert calls[1][0].startswith('/userdata/workflows%2Fwebui-library%2Fsafe-')
    assert calls[1][1] == graph
    assert calls[1][2] == 'POST'
    assert all(path != '/prompt' for path, _, _ in calls)
