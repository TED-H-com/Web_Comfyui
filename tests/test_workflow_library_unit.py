import pytest
import os
import json
from pathlib import Path
from webui.workflow_library import safe_path, catalog, _provenance_owner, selected, _model_name, graph_details, _model_selections, graph_models, missing_nodes, _model_key, dependency_checks, read_json, _provenance_url_parts, LibraryRequestError

def test_safe_path_basic(tmp_path):
    workflows_dir = tmp_path / 'workflows'
    workflows_dir.mkdir()
    (workflows_dir / 'test.json').touch()

    result = safe_path("workflows/test.json", tmp_path)
    assert result.name == "test.json"

def test_safe_path_invalid_relative(tmp_path):
    workflows_dir = tmp_path / 'workflows'
    workflows_dir.mkdir()

    with pytest.raises(ValueError, match="工作流路徑無效"):
        safe_path("workflows/../test.json", tmp_path)

def test_safe_path_symlink_not_allowed(tmp_path):
    workflows_dir = tmp_path / 'workflows'
    workflows_dir.mkdir()

    target = tmp_path / 'target.json'
    target.touch()
    link = workflows_dir / 'test.json'
    link.symlink_to(target)

    with pytest.raises(ValueError, match="工作流路徑無效"):
        safe_path("workflows/test.json", tmp_path)

def test_safe_path_outside_workflows(tmp_path):
    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    (other_dir / 'test.json').touch()

    with pytest.raises(ValueError, match="工作流路徑無效"):
        safe_path("other/test.json", tmp_path)

def test_safe_path_non_existent(tmp_path):
    workflows_dir = tmp_path / 'workflows'
    workflows_dir.mkdir()

    with pytest.raises(ValueError, match="工作流不存在"):
        safe_path("workflows/not_found.json", tmp_path)

def test_safe_path_not_json(tmp_path):
    workflows_dir = tmp_path / 'workflows'
    workflows_dir.mkdir()
    (workflows_dir / 'test.txt').touch()

    with pytest.raises(ValueError, match="工作流不存在"):
        safe_path("workflows/test.txt", tmp_path)

def test_safe_path_user_saved_rejected(tmp_path):
    user_saved_dir = tmp_path / 'workflows' / 'user-saved'
    user_saved_dir.mkdir(parents=True)
    (user_saved_dir / 'test.json').touch()

    with pytest.raises(ValueError, match="這個路徑不是共用工作流"):
        safe_path("workflows/user-saved/test.json", tmp_path)

def test_provenance_url_parts():
    # Valid
    assert _provenance_url_parts("http://example.com/path").scheme == "http"
    assert _provenance_url_parts("https://example.com/path").hostname == "example.com"

    # Invalid
    assert _provenance_url_parts(None) is None
    assert _provenance_url_parts("") is None
    assert _provenance_url_parts("   ") is None
    assert _provenance_url_parts("ftp://example.com") is None
    assert _provenance_url_parts("https://") is None
    assert _provenance_url_parts(123) is None

def test_provenance_owner():
    assert _provenance_owner("https://github.com/owner/repo") == "owner"
    assert _provenance_owner("https://github.com") is None
    assert _provenance_owner("https://github.com/") is None
    assert _provenance_owner("https://github.com/.") is None
    assert _provenance_owner("https://github.com/..") is None
    assert _provenance_owner(None) is None
    assert _provenance_owner("invalid") is None

def test_read_json_valid(tmp_path):
    file_path = tmp_path / "test.json"
    file_path.write_text('{"key": "value"}')
    assert read_json(file_path) == {"key": "value"}

def test_read_json_invalid(tmp_path):
    file_path = tmp_path / "test.json"
    file_path.write_text('{invalid_json')
    with pytest.raises(json.decoder.JSONDecodeError):
        read_json(file_path)

def test_read_json_non_existent(tmp_path):
    with pytest.raises(OSError):
        read_json(tmp_path / "not_found.json")

def test_read_json_too_large(tmp_path):
    file_path = tmp_path / "test.json"
    # Write slightly more than 8MB
    content = " " * (8 * 1024 * 1024 + 10)
    file_path.write_text(f'{{"key": "{content}"}}')
    with pytest.raises(ValueError, match="Workflow exceeds 8 MiB"):
        read_json(file_path)

def test_graph_details():
    graph = {
        "nodes": [
            {
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["model.safetensors"]
            }
        ],
        "extra": {
            "workspace_info": {
                "id": "test_id",
                "name": "Test Workflow"
            }
        }
    }
    details = graph_details(graph)
    assert details["format"] == "visual"

    graph_api = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "model2.safetensors"
            }
        }
    }
    details_api = graph_details(graph_api)
    assert details_api["format"] == "api"

    with pytest.raises(ValueError, match="不是 ComfyUI visual / API 工作流"):
        graph_details({})

def test_model_name():
    assert _model_name("model.safetensors", "checkpoints") == "model.safetensors"
    assert _model_name("model.pt", "diffusion_models") == "model.pt"
    assert _model_name("folder/model.safetensors", "checkpoints") == "folder/model.safetensors"
    assert _model_name("../model.safetensors", "checkpoints") is None

def test_model_key():
    assert _model_key("model.safetensors", "checkpoints") == "model.safetensors"
    assert _model_key("checkpoints/model.pt", "checkpoints") == "model.pt"
    assert _model_key("models/checkpoints/model.safetensors", "checkpoints") == "model.safetensors"

def test_model_selections():
    graph = {
        "nodes": [
            {
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["model.safetensors"]
            }
        ]
    }
    selections = list(_model_selections(graph))
    assert selections == [("checkpoints", "CheckpointLoaderSimple", "ckpt_name", "model.safetensors")]

    # With dict structure instead of list for widgets_values (sometimes happens in API format)
    graph_api = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "model2.safetensors"
            }
        }
    }
    selections_api = list(_model_selections(graph_api))
    assert selections_api == [("checkpoints", "CheckpointLoaderSimple", "ckpt_name", "model2.safetensors")]

def test_graph_models():
    graph = {
        "nodes": [
            {
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["model.safetensors"]
            }
        ]
    }
    models = graph_models(graph)
    assert models[0]["name"] == "model.safetensors"
    assert models[0]["directory_hint"] == "checkpoints"
    assert models[0]["node_type"] == "CheckpointLoaderSimple"

def test_missing_nodes():
    entry = {"node_types": ["KSampler", "Note", "CustomNode1"]}
    object_info = {"KSampler": {}}

    # Note is NON_EXECUTABLE, so only CustomNode1 is missing
    missing = missing_nodes(entry, object_info)
    assert missing == ["CustomNode1"]

def test_dependency_checks():
    entry = {
        "node_types": ["KSampler", "MissingNode"],
        "models": [{"name": "missing_model.safetensors", "directory_hint": "checkpoints"}]
    }
    graph = {
        "nodes": [
            {"type": "MissingNode"},
            {
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["missing_model.safetensors"]
            }
        ]
    }
    object_info = {
        "KSampler": {},
        "CheckpointLoaderSimple": {
            "input": {
                "required": {
                    "ckpt_name": ["COMBO", {"options": ["found_model.safetensors"]}]
                }
            }
        }
    }

    checks = dependency_checks(entry, graph, object_info)
    assert checks["missing_nodes"] == ["MissingNode"]
    assert checks["models_checked"] == 1
    assert checks["missing_models"][0]["name"] == "missing_model.safetensors"

def test_catalog_empty(tmp_path):
    entries = catalog(tmp_path)
    assert isinstance(entries, list)
    assert len(entries) == 0

def test_catalog_with_files(tmp_path):
    workflows_dir = tmp_path / 'workflows'
    workflows_dir.mkdir()

    wf_path = workflows_dir / 'test.json'
    wf_path.write_text('{"nodes": [{"type": "CheckpointLoaderSimple", "widgets_values": ["model.safetensors"]}], "extra": {"workspace_info": {"name": "Test1"}}}')

    entries = catalog(tmp_path)
    assert len(entries) == 1
    assert entries[0]["title"] == "test"
    assert entries[0]["available"] is True
    assert entries[0]["kind"] == "workflow"

def test_selected_not_found(tmp_path):
    with pytest.raises(LibraryRequestError, match="這個項目是參考資料"):
        selected("non_existent_id", tmp_path)
