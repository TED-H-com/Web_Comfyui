"""Optional native node test; no model loading, downloads, or GPU access."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webui'))
import server


@pytest.mark.skipif(not os.environ.get('COMFYUI_TEST_PYTHON'), reason='Set COMFYUI_TEST_PYTHON and COMFY_HOME for native CPU checks')
def test_native_feather_preserves_png_interiors_and_background(tmp_path):
    comfy_home = Path(os.environ['COMFY_HOME']).resolve()
    graphs = {radius: server.subject_blend_prompt({'feather': radius}, {'image1': 'source.png', 'image2': 'bg.png'})
              for radius in range(17)}
    graph_path = tmp_path / 'graphs.json'
    graph_path.write_text(json.dumps(graphs))
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='', TMPDIR=str(tmp_path), OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
    result = subprocess.run(
        [os.environ['COMFYUI_TEST_PYTHON'], str(ROOT / 'tests/native_subject_blend_probe.py'), str(comfy_home), str(graph_path)],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report == {'passed_cases': 102, 'cuda_initialized': False}
