import pytest
from webui.workflow_guides import _nodes, active_nodes, _parameters, _methods, _recipe_value, _recipe, enrich_entry

def test_nodes_visual_format():
    graph = {
        "nodes": [
            {"type": "KSampler", "mode": 0},
            {"type": "CheckpointLoaderSimple", "mode": 0},
            {"type": "Note", "mode": 2} # mode 2 is bypassed/muted
        ]
    }
    nodes = _nodes(graph)
    assert len(nodes) == 2
    types = [kind for kind, _, _ in nodes]
    assert "KSampler" in types
    assert "CheckpointLoaderSimple" in types
    assert "Note" not in types

def test_nodes_api_format():
    graph = {
        "1": {"class_type": "KSampler"},
        "2": {"class_type": "CheckpointLoaderSimple"}
    }
    nodes = _nodes(graph)
    assert len(nodes) == 2
    types = [kind for kind, _, _ in nodes]
    assert "KSampler" in types
    assert "CheckpointLoaderSimple" in types

def test_active_nodes():
    graph = {
        "nodes": [
            {"type": "KSampler", "mode": 0},
            {"type": "Note", "mode": 4} # mode 4 is disabled/never execute
        ]
    }
    nodes = active_nodes(graph)
    assert len(nodes) == 1
    assert nodes[0]["type"] == "KSampler"

def test_parameters_extraction():
    nodes = [
        ("KSampler", {"widgets_values": [123, "fixed", 20, 8.0, "euler", "normal", 1.0]}, False),
        ("CheckpointLoaderSimple", {"widgets_values": ["model.safetensors"]}, False)
    ]
    params = _parameters(nodes)

    param_names = [p["name"] for p in params]
    assert "KSampler #1 · steps" in param_names
    assert "KSampler #1 · sampler_name" in param_names

    steps_param = next(p for p in params if p["name"] == "KSampler #1 · steps")
    assert steps_param["value"] == 20

    sampler_param = next(p for p in params if p["name"] == "KSampler #1 · sampler_name")
    assert sampler_param["value"] == "euler"

def test_methods_extraction():
    types = ["KSampler", "CheckpointLoaderSimple", "ControlNetLoader"]
    entry = {"node_types": types}
    flags = _methods(types, entry)

    assert flags["diffusion"] is True
    assert flags["control"] is True
    assert flags["outpaint"] is False

def test_recipe_value():
    settings = {
        "first_pass": {
            "scheduler": "normal",
            "steps": 20
        },
        "quality": "HIGH"
    }
    assert _recipe_value(settings, "first_pass.scheduler") == "normal"
    assert _recipe_value(settings, "quality") == "HIGH"
    assert _recipe_value(settings, "not.found") is None

def test_recipe():
    entry = {
        "id": "recipe-moss-character-pilot",
        "settings": {
            "steps": 20,
            "export_format": "comfy"
        }
    }
    result = _recipe(entry)

    assert "summary" in result
    assert "inputs" in result
    assert "steps" in result
    assert len(result["parameters"]) > 0

def test_enrich_entry_recipe():
    entry = {
        "kind": "recipe",
        "id": "recipe-moss-character-pilot",
        "settings": {}
    }
    result = enrich_entry(entry)
    assert result["evidence"] == '依已收錄的來源配方整理；數值僅來自 settings 白名單欄位，未執行訓練或 GPU 生成。'

def test_enrich_entry_workflow():
    entry = {
        "kind": "workflow",
        "node_types": ["KSampler", "CheckpointLoaderSimple", "SaveImage"]
    }
    graph = {
        "nodes": [
            {"type": "KSampler", "mode": 0},
            {"type": "CheckpointLoaderSimple", "mode": 0},
            {"type": "SaveImage", "mode": 0}
        ]
    }
    result = enrich_entry(entry, graph)

    assert result["output"] == "處理後的影像或中間素材；實際輸出節點為準。"
    assert len(result["tips"]) > 0
    assert len(result["steps"]) > 0
    assert "summary" in result

def test_enrich_entry_workflow_no_graph():
    entry = {
        "kind": "workflow",
        "node_types": ["KSampler", "CheckpointLoaderSimple", "SaveImage"]
    }
    result = enrich_entry(entry, None)

    assert "inventory-only" in result["evidence"]
