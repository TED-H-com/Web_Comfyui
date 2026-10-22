"""Identity preservation is a direct source-pixel path, never diffusion."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))
sys.path.insert(0, str(ROOT / "scripts"))
import server
import app_guide_catalog
import app_graph_catalog


def test_default_feature_uses_subject_composite_and_only_two_inputs():
    feature = server.FEATURE_BY_ID["image-blend"]
    assert server.BUILDERS[feature["builder"]] is server.subject_blend_prompt
    assert [(u["id"], u["required"]) for u in feature["uploads"]] == [("image1", True), ("image2", True)]
    assert {f["id"] for f in feature["fields"]} == {"positive", "threshold"}
    assert feature["requiredModels"] == ["sam3.1_multiplex_fp16.safetensors"]


@pytest.mark.parametrize("uploads", [{}, {"image1": "subject.png"}, {"image2": "background.png"}])
def test_both_semantic_inputs_are_required(uploads):
    with pytest.raises(ValueError):
        server.subject_blend_prompt({}, uploads)


def test_original_subject_bypasses_resize_and_generation():
    graph = server.subject_blend_prompt({"steps": 40, "width": 512, "weight": 1.5}, {"image1": "subject.png", "image2": "background.png"})
    assert graph["1"]["inputs"]["image"] == "subject.png"
    assert graph["2"]["inputs"]["image"] == "background.png"
    assert graph["3"]["inputs"] == {"input": ["2", 0], "resize_type": "match size", "resize_type.match": ["1", 0], "resize_type.crop": "center", "scale_method": "lanczos"}
    assert graph["8"]["inputs"] == {"destination": ["3", 0], "source": ["1", 0], "x": 0, "y": 0, "resize_source": False, "mask": ["7", 0]}
    assert graph["7"]["inputs"] == {"mask": ["6", 0], "value": 0.5}
    assert graph["6"]["inputs"]["individual_masks"] is False
    assert graph["6"]["inputs"]["refine_iterations"] == 2
    assert graph["5"]["inputs"]["text"] == "person"
    assert graph["6"]["inputs"]["threshold"] == 0.5
    assert not {n["class_type"] for n in graph.values()} & {"KSampler", "VAEDecode", "IPAdapterAdvanced"}
    assert graph["9"]["inputs"]["images"] == ["8", 0]


@pytest.mark.parametrize("value,expected", [(-1, 0), (2, 1), (0.7, 0.7)])
def test_segmentation_controls(value, expected):
    graph = server.subject_blend_prompt({"positive": "  dog  ", "threshold": value}, {"image1": "subject.png", "image2": "background.png"})
    assert graph["5"]["inputs"]["text"] == "dog"
    assert graph["6"]["inputs"]["threshold"] == expected


def test_documented_graph_matches_default_composite():
    catalog = app_guide_catalog.catalog(ROOT)
    graph = app_graph_catalog.graphs(ROOT, catalog)["image-blend"]
    assert graph["source"].endswith("#subject_blend_prompt")
    assert {n["type"] for n in graph["nodes"]} == {"LoadImage", "ResizeImageMaskNode", "CheckpointLoaderSimple", "CLIPTextEncode", "SAM3_Detect", "ThresholdMask", "ImageCompositeMasked", "SaveImage"}
    assert {"from": "1", "to": "8", "from_slot": 0, "to_slot": "source"} in graph["links"]
    assert {"from": "1", "to": "3", "from_slot": 0, "to_slot": "resize_type.match"} in graph["links"]
