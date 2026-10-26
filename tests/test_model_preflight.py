from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.minimax_h3.run_native import RunnerError, verify_object_info


@pytest.mark.parametrize('descriptor', [[['model.safetensors']], ['COMBO', {'options': ['model.safetensors']}]])
def test_preflight_accepts_both_combo_schemas(descriptor):
    graph = {'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'model.safetensors'}}}
    verify_object_info({'UNETLoader': {'input': {'required': {'unet_name': descriptor}}}}, graph)


@pytest.mark.parametrize('descriptor', [['COMBO'], ['COMBO', {}], ['COMBO', {'options': []}], ['COMBO', {'options': ['other.safetensors']}], ['STRING']])
def test_preflight_never_allows_missing_or_unknown_models(descriptor):
    graph = {'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'model.safetensors'}}}
    with pytest.raises(RunnerError):
        verify_object_info({'UNETLoader': {'input': {'required': {'unet_name': descriptor}}}}, graph)
