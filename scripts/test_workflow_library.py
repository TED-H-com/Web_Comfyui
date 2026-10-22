from pathlib import Path
import importlib.util
import json
import sys
import types
import urllib.error

import pytest

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
    assert handler.body['already_imported'] is False
    assert handler.body['dependency_status'] == 'present'
    assert handler.body['gpu_tested'] is False


def object_loader(field, choices):
    return {'input': {'required': {field: [choices]}}}


def test_loader_widget_positions_connections_and_private_values():
    graph = {'nodes': [
        {'type': 'CheckpointLoader', 'widgets_values': ['config.yaml', 'base.safetensors']},
        {'type': 'DualCLIPLoader', 'widgets_values': ['stale.safetensors', 'actual.safetensors'],
         'inputs': [{'name': 'clip_name1', 'link': 14}]},
        {'type': 'CLIPVisionLoader', 'widgets_values': ['vision.safetensors']},
        {'type': 'UpscaleModelLoader', 'widgets_values': ['upscaler.pth']},
        {'type': 'VAELoader', 'widgets_values': ['/home/example/private.safetensors']},
    ]}
    models = library.graph_models(graph)
    assert [row['name'] for row in models] == ['base.safetensors', 'actual.safetensors', 'vision.safetensors', 'upscaler.pth']
    assert all('input' not in row and 'widgets_values' not in row for row in models)
    api = {'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': ['2', 0]}},
           '2': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'pixel_space'}}}
    assert library.graph_models(api) == [{'name': 'pixel_space', 'node_type': 'VAELoader', 'directory_hint': 'vae'}]


def test_dependency_inventory_distinguishes_missing_known_and_unknown_models():
    graph = {'nodes': [
        {'type': 'UNETLoader', 'widgets_values': ['unet/family/model.safetensors']},
        {'type': 'DualCLIPLoader', 'widgets_values': ['clip\\encoder.safetensors', 'missing.safetensors']},
        {'type': 'VAELoader', 'widgets_values': ['stale.safetensors'], 'inputs': [{'name': 'vae_name', 'link': 4}]},
        {'type': 'CustomModelLoader', 'widgets_values': ['never-echo-this.safetensors']},
    ]}
    info = {
        'UNETLoader': object_loader('unet_name', ['diffusion_models/family/model.safetensors']),
        'DualCLIPLoader': {'input': {'required': {'clip_name1': [['encoder.safetensors']],
                                                 'clip_name2': [['other.safetensors']]}}},
        'VAELoader': object_loader('vae_name', ['stale.safetensors']),
        'CustomModelLoader': {},
    }
    entry = {**library.graph_details(graph), 'models': [{'name': 'untrusted_manifest_note.safetensors'}]}
    result = library.dependency_checks(entry, graph, info)
    assert result == {
        'missing_nodes': [],
        'missing_models': [{'name': 'missing.safetensors', 'node_type': 'DualCLIPLoader', 'directory_hint': 'text_encoders'}],
        'models_checked': 3, 'model_checks_unknown': 2,
        'dependency_status': 'missing', 'gpu_tested': False,
    }
    assert 'never-echo-this' not in json.dumps(result)
    assert 'untrusted_manifest_note' not in json.dumps(result)


def test_model_matching_keeps_subdirectories_distinct_and_supports_optional_fields():
    graph = {'nodes': [{'type': 'LoraLoader', 'widgets_values': ['portraits/style.safetensors']}]}
    info = {'LoraLoader': {'input': {'optional': {'lora_name': [['landscapes/style.safetensors']]}}}}
    result = library.dependency_checks(library.graph_details(graph), graph, info)
    assert result['models_checked'] == 1
    assert result['missing_models'][0]['name'] == 'portraits/style.safetensors'
    info['LoraLoader']['input']['optional']['lora_name'] = [['models/loras/portraits/style.safetensors']]
    assert library.dependency_checks(library.graph_details(graph), graph, info)['dependency_status'] == 'present'


@pytest.mark.parametrize('choices, expected', [([], 'missing'), ('STRING', 'partial'), ([1, 2], 'partial')])
def test_empty_enumeration_is_missing_but_unsupported_schema_is_unknown(choices, expected):
    graph = {'1': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'example.safetensors'}}}
    result = library.dependency_checks(library.graph_details(graph), graph, {'VAELoader': object_loader('vae_name', choices)})
    assert result['dependency_status'] == expected
    assert result['models_checked'] == (1 if expected == 'missing' else 0)
    assert result['model_checks_unknown'] == (0 if expected == 'missing' else 1)


def test_missing_loader_counts_node_and_unchecked_model_without_exposing_paths():
    graph = {'nodes': [{'type': 'UNETLoader', 'widgets_values': ['/private/model.safetensors']}]}
    result = library.dependency_checks(library.graph_details(graph), graph, {})
    assert result['missing_nodes'] == ['UNETLoader']
    assert result['missing_models'] == []
    assert result['model_checks_unknown'] == 1
    assert result['dependency_status'] == 'missing'
    assert '/private/' not in json.dumps(result)


def test_checked_catalog_uses_one_inventory_response_and_actual_graph_selections(tmp_path, monkeypatch):
    workflows = tmp_path / 'workflows'
    workflows.mkdir()
    for name in ('one', 'two'):
        (workflows / (name + '.json')).write_text(json.dumps({'nodes': [
            {'type': 'CheckpointLoaderSimple', 'widgets_values': ['base.safetensors']},
        ]}))
    original_catalog = library.catalog
    monkeypatch.setattr(library, 'catalog', lambda **kwargs: original_catalog(tmp_path, **kwargs))
    calls = []
    def request(path):
        calls.append(path)
        return {'CheckpointLoaderSimple': object_loader('ckpt_name', ['base.safetensors'])}
    module = types.ModuleType('workflow_inventory_server')
    module.request_json = request
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Handler:
        def require_auth(self): return True
        def send_json(self, body, status=200, **kwargs): self.body, self.status = body, status
    Handler.__module__ = module.__name__
    handler = Handler()
    library.handle_get(handler, '/api/workflow-library', 'check=1')
    assert calls == ['/object_info']
    assert handler.body['connection'] == {'checked': True, 'connected': True}
    assert len(handler.body['items']) == 2
    assert all(row['models_checked'] == 1 and row['dependency_status'] == 'present' for row in handler.body['items'])
    calls.clear()
    library.handle_get(handler, '/api/workflow-library', '')
    assert not calls
    assert all('models_checked' not in row for row in handler.body['items'])


def open_handler(monkeypatch, request, graph=None):
    module = types.ModuleType('workflow_repeat_server')
    module.request_json = request
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Handler:
        def require_auth(self): return True
        def require_operator(self): return True
        def read_json(self): return {'id': 'safe'}
        def send_json(self, body, status=200): self.body, self.status = body, status
    Handler.__module__ = module.__name__
    graph = graph or {'nodes': [{'id': 1, 'type': 'KSampler'}], 'links': []}
    monkeypatch.setattr(library, 'selected', lambda _: ({'id': 'safe', **library.graph_details(graph)}, graph))
    return Handler(), graph


@pytest.mark.parametrize('same', [True, False])
def test_repeat_import_compares_saved_graph_and_never_overwrites(monkeypatch, same):
    calls = []
    graph = {'nodes': [{'id': 1, 'type': 'KSampler'}], 'links': []}
    def request(path, payload=None, method=None):
        calls.append((path, payload, method))
        if path == '/object_info':
            return {'KSampler': {}}
        if method == 'POST':
            raise urllib.error.HTTPError('https://private.invalid/ignored', 409, 'private detail', {}, None)
        return dict(reversed(list(graph.items()))) if same else {'nodes': [], 'links': []}
    handler, _ = open_handler(monkeypatch, request, graph)
    library.handle_post(handler, '/api/workflow-library/open')
    assert handler.status == (200 if same else 409)
    if same:
        assert handler.body['already_imported'] is True
    else:
        assert handler.body['ok'] is False
    assert len(calls) == 3
    assert calls[1][0].endswith('?overwrite=false')
    assert calls[2][0] == calls[1][0].split('?')[0]
    assert calls[2][1:] == (None, None)
    assert sum(method == 'POST' for _, _, method in calls) == 1
    assert all('/prompt' not in path and 'overwrite=true' not in path for path, _, _ in calls)
    assert 'private detail' not in json.dumps(handler.body)


@pytest.mark.parametrize('error', [ValueError('private upstream token'),
                                 urllib.error.HTTPError('https://private.invalid', 500, 'private upstream token', {}, None)])
def test_open_sanitizes_all_upstream_errors(monkeypatch, error):
    def request(*args, **kwargs): raise error
    handler, _ = open_handler(monkeypatch, request)
    library.handle_post(handler, '/api/workflow-library/open')
    assert handler.status == 400 and handler.body['ok'] is False
    assert 'private' not in json.dumps(handler.body)


def test_non_operator_cannot_read_import_body_or_contact_comfy(monkeypatch):
    class Handler:
        def require_auth(self): return True
        def require_operator(self): return False
        def read_json(self): raise AssertionError('Unauthorized body read')
    monkeypatch.setattr(library, 'selected', lambda *_: pytest.fail('Unauthorized catalog lookup'))
    assert library.handle_post(Handler(), '/api/workflow-library/open')


@pytest.mark.parametrize('path', ['/api/workflow-library', '/api/workflow-library/file'])
@pytest.mark.parametrize('error', [OSError('private-token at /private/runtime/file.json'),
                                 ValueError('private-token in malformed graph')])
def test_get_sanitizes_filesystem_and_parser_errors(monkeypatch, path, error):
    def fail(*args, **kwargs): raise error
    monkeypatch.setattr(library, 'catalog', fail)
    monkeypatch.setattr(library, 'selected', fail)
    class Handler:
        def require_auth(self): return True
        def send_json(self, body, status=200, **kwargs): self.body, self.status = body, status
    handler = Handler()
    library.handle_get(handler, path, 'id=example')
    assert handler.status == 400 and handler.body['ok'] is False
    assert 'private-token' not in json.dumps(handler.body)
    assert '/private/' not in json.dumps(handler.body)


def test_get_preserves_deliberate_reference_only_message(monkeypatch):
    def fail(*args): raise library.LibraryRequestError('參考項目沒有可下載檔案')
    monkeypatch.setattr(library, 'selected', fail)
    class Handler:
        def require_auth(self): return True
        def send_json(self, body, status=200, **kwargs): self.body, self.status = body, status
    handler = Handler()
    library.handle_get(handler, '/api/workflow-library/file', 'id=reference')
    assert handler.body['error'] == '參考項目沒有可下載檔案'
