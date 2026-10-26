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
    assert {f["id"] for f in feature["fields"]} == {"positive", "threshold", "feather"}
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
    assert len(graph) == 9


@pytest.mark.parametrize('radius,expected', [(-1, 0), (0, 0), (4, 4), (100, 16), ('invalid', 0)])
def test_optional_feather_only_changes_mask_path(radius, expected):
    graph = server.subject_blend_prompt({'positive': 'person holding a bag', 'feather': radius},
                                        {'image1': 'subject.png', 'image2': 'background.png'})
    assert graph['5']['inputs']['text'] == 'person holding a bag'
    assert graph['8']['inputs']['source'] == ['1', 0]
    assert graph['8']['inputs']['resize_source'] is False
    if not expected:
        assert len(graph) == 9
        assert graph['8']['inputs']['mask'] == ['7', 0]
    else:
        assert graph['10']['inputs']['mask'] == ['7', 0]
        assert graph['11']['class_type'] == 'ImageBlur'
        assert graph['11']['inputs']['blur_radius'] == expected
        assert graph['12']['inputs'] == {'image': ['11', 0], 'channel': 'red'}
        assert graph['8']['inputs']['mask'] == ['12', 0]
        assert len(graph) == 14
        assert graph['13']['inputs'] == {'mask': ['7', 0], 'expand': -expected, 'tapered_corners': False}
        assert graph['14']['inputs']['mask'] == ['13', 0]
        assert graph['14']['inputs']['source'] == ['1', 0]
        assert graph['14']['inputs']['destination'] == ['8', 0]
        assert graph['14']['inputs']['resize_source'] is False
        assert graph['9']['inputs']['images'] == ['14', 0]


@pytest.mark.parametrize("value,expected", [(-1, 0), (2, 1), (0.7, 0.7)])
def test_segmentation_controls(value, expected):
    graph = server.subject_blend_prompt({"positive": "  dog  ", "threshold": value}, {"image1": "subject.png", "image2": "background.png"})
    assert graph["5"]["inputs"]["text"] == "dog"
    assert graph["6"]["inputs"]["threshold"] == expected


@pytest.mark.parametrize('value', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_settings_use_safe_defaults(value):
    graph = server.subject_blend_prompt({'feather': value, 'threshold': value},
                                        {'image1': 'subject.png', 'image2': 'background.png'})
    assert len(graph) == 9
    assert graph['6']['inputs']['threshold'] == 0.5


def test_documented_graph_matches_default_composite():
    catalog = app_guide_catalog.catalog(ROOT)
    graph = app_graph_catalog.graphs(ROOT, catalog)["image-blend"]
    assert graph["source"].endswith("#subject_blend_prompt")
    assert {n["type"] for n in graph["nodes"]} == {"LoadImage", "ResizeImageMaskNode", "CheckpointLoaderSimple", "CLIPTextEncode", "SAM3_Detect", "ThresholdMask", "ImageCompositeMasked", "SaveImage"}
    assert {"from": "1", "to": "8", "from_slot": 0, "to_slot": "source"} in graph["links"]
    assert {"from": "1", "to": "3", "from_slot": 0, "to_slot": "resize_type.match"} in graph["links"]
