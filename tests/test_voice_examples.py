"""Offline replay CLI boundaries; these checks do not synthesize audio."""
from pathlib import Path
import importlib.util
import hashlib

import pytest

spec = importlib.util.spec_from_file_location('voice_examples', Path(__file__).resolve().parents[1] / 'scripts/generate_voice_examples.py')
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


def test_explicit_local_paths_and_cpu_default():
    with pytest.raises(SystemExit):
        voice.parser().parse_args([])
    args = voice.parser().parse_args(['--model', '/explicit/model', '--output', '/explicit/output'])
    assert args.device == 'cpu'
    assert args.threads == 8
    assert not args.clone


def test_invalid_model_fails_before_import_or_generation(tmp_path):
    with pytest.raises(ValueError, match='required base-model file'):
        voice.main(['--model', str(tmp_path), '--output', str(tmp_path / 'outputs')])
    assert not (tmp_path / 'outputs').exists()


def test_thread_budget_is_bounded(tmp_path):
    with pytest.raises(ValueError, match='threads'):
        voice.main(['--model', str(tmp_path), '--output', str(tmp_path / 'outputs'), '--threads', '99'])


def test_hash_is_file_content(tmp_path):
    path = tmp_path / 'synthetic.bin'
    path.write_bytes(b'original synthetic demo')
    assert voice.sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_unowned_audio_is_not_overwritten(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    for name in ('model.safetensors', 'audiovae.pth', 'config.json'):
        (model / name).write_text('synthetic test placeholder')
    output = tmp_path / 'outputs'
    output.mkdir()
    audio = output / 'warm-welcome.wav'
    audio.write_bytes(b'unrelated existing file')
    with pytest.raises(ValueError, match='unrelated audio'):
        voice.main(['--model', str(model), '--output', str(output)])
    assert audio.read_bytes() == b'unrelated existing file'


def test_modified_owned_audio_is_not_overwritten(tmp_path):
    import json
    model = tmp_path / 'model'
    model.mkdir()
    for name in ('model.safetensors', 'audiovae.pth', 'config.json'):
        (model / name).write_text('synthetic test placeholder')
    output = tmp_path / 'outputs'
    output.mkdir()
    audio = output / 'warm-welcome.wav'
    audio.write_bytes(b'user changed this file')
    (output / 'receipt.json').write_text(json.dumps({'engine': 'VoxCPM2', 'samples': [
        {'id': 'warm-welcome', 'file': audio.name, 'sha256': hashlib.sha256(b'original').hexdigest()}
    ]}))
    with pytest.raises(ValueError, match='owned receipt'):
        voice.main(['--model', str(model), '--output', str(output), '--only', 'warm-welcome'])
    assert audio.read_bytes() == b'user changed this file'


def _placeholder_model(tmp_path):
    model = tmp_path / 'model'
    model.mkdir(exist_ok=True)
    for name in ('model.safetensors', 'audiovae.pth', 'config.json'):
        (model / name).write_text('synthetic test placeholder')
    return model


def test_demo2_tables_and_slugs_are_disjoint_and_valid():
    assert len(voice.DEMO2_CASES) == 8
    assert len(voice.DEMO2_CLONES) == 8
    original_slugs = [row[0] for row in voice.CASES] + ['synthetic-clone']
    demo2_case_slugs = [row[0] for row in voice.DEMO2_CASES]
    demo2_clone_slugs = [row[0] for row in voice.DEMO2_CLONES]
    all_slugs = original_slugs + demo2_case_slugs + demo2_clone_slugs
    assert len(all_slugs) == len(set(all_slugs))
    for _, _, reference_slug, _, _ in voice.DEMO2_CLONES:
        assert reference_slug in demo2_case_slugs


def test_set_defaults_to_original():
    args = voice.parser().parse_args(['--model', '/explicit/model', '--output', '/explicit/output'])
    assert args.set == 'original'


def test_only_from_other_set_fails_without_adding_files(tmp_path):
    model = _placeholder_model(tmp_path)
    output = tmp_path / 'outputs'
    output.mkdir()
    before = set(output.iterdir())
    with pytest.raises(ValueError, match='--only is not part of the selected set'):
        voice.main(['--model', str(model), '--output', str(output), '--set', 'demo2', '--only', 'warm-welcome'])
    assert set(output.iterdir()) == before
    with pytest.raises(ValueError, match='--only is not part of the selected set'):
        voice.main(['--model', str(model), '--output', str(output), '--only', 'qingqing-hello'])
    assert set(output.iterdir()) == before


def test_receipt_from_other_set_fails_without_adding_files(tmp_path):
    import json
    model = _placeholder_model(tmp_path)
    output = tmp_path / 'outputs'
    output.mkdir()
    receipt = output / 'receipt.json'
    receipt.write_text(json.dumps({'engine': 'VoxCPM2', 'samples': []}))
    before = set(output.iterdir())
    with pytest.raises(ValueError, match='Existing output receipt belongs to another set'):
        voice.main(['--model', str(model), '--output', str(output), '--set', 'demo2', '--only', 'qingqing-hello'])
    assert set(output.iterdir()) == before
    receipt.write_text(json.dumps({'engine': 'VoxCPM2', 'set': 'demo2', 'samples': []}))
    before = set(output.iterdir())
    with pytest.raises(ValueError, match='Existing output receipt belongs to another set'):
        voice.main(['--model', str(model), '--output', str(output), '--only', 'warm-welcome'])
    assert set(output.iterdir()) == before


def test_demo2_clone_without_matching_reference_fails_without_adding_files(tmp_path):
    import json
    model = _placeholder_model(tmp_path)
    output = tmp_path / 'outputs'
    output.mkdir()
    before = set(output.iterdir())
    with pytest.raises(
        ValueError,
        match="Clone requires this script's generated qingqing-hello and matching receipt",
    ):
        voice.main(['--model', str(model), '--output', str(output), '--set', 'demo2', '--only', 'qingqing-clone'])
    assert set(output.iterdir()) == before
    (output / 'receipt.json').write_text(json.dumps({'engine': 'VoxCPM2', 'set': 'demo2', 'samples': []}))
    before = set(output.iterdir())
    with pytest.raises(
        ValueError,
        match="Clone requires this script's generated qingqing-hello and matching receipt",
    ):
        voice.main(['--model', str(model), '--output', str(output), '--set', 'demo2', '--only', 'qingqing-clone'])
    assert set(output.iterdir()) == before


def test_plan_keep_segments_trims_edges_and_cuts_only_long_internal_pauses():
    sample_rate = 48000
    rms = (
        [0.0] * 135
        + [0.2] * 100
        + [0.0] * 160
        + [0.2] * 100
        + [0.0] * 50
        + [0.2] * 100
        + [0.0] * 90
    )
    n_samples = len(rms) * 480
    segments = voice.plan_keep_segments(rms, n_samples, sample_rate, voice.DEMO2_POSTPROCESS)
    assert segments == [(59040, 129600), (172800, 315360)]
    assert voice.frame_rms([3.0, 4.0, 0.0, 5.0], 2) == [pytest.approx(3.5355339), pytest.approx(3.5355339)]
    assert voice.frame_rms([2.0, 2.0, 4.0], 2) == [pytest.approx(2.0), pytest.approx(4.0)]


def test_silent_input_and_join_segments_and_postprocess_demo2():
    with pytest.raises(ValueError, match='silent waveform'):
        voice.plan_keep_segments([0.0] * 50, 24000, 48000, voice.DEMO2_POSTPROCESS)
    with pytest.raises(ValueError, match='silent waveform'):
        voice.postprocess_demo2([0.0] * 24000, 48000)

    samples = [1.0] * 10 + [3.0] * 12 + [2.0] * 8
    segments = [(0, 10), (10, 22), (22, 30)]
    crossfade = 4
    edge_fade = 3
    joined = voice.join_segments(samples, segments, crossfade, edge_fade)
    assert len(joined) == (10 + 12 + 8) - 2 * crossfade

    two_parts = voice.join_segments([1.0] * 10 + [3.0] * 10, [(0, 10), (10, 20)], crossfade=4, edge_fade=2)
    assert len(two_parts) == 20 - 4
    expected_overlap = [(1.0 - (i + 1) / 5.0) * 1.0 + ((i + 1) / 5.0) * 3.0 for i in range(4)]
    assert two_parts[6:10] == [pytest.approx(v) for v in expected_overlap]
    assert two_parts[0] == pytest.approx(1.0 * (1 / 3))
    assert two_parts[1] == pytest.approx(1.0 * (2 / 3))
    assert two_parts[2] == pytest.approx(1.0)
    assert two_parts[-3] == pytest.approx(3.0)
    assert two_parts[-2] == pytest.approx(3.0 * (2 / 3))
    assert two_parts[-1] == pytest.approx(3.0 * (1 / 3))

    with pytest.raises(ValueError, match='shorter than crossfade'):
        voice.join_segments([1.0] * 10, [(0, 3)], crossfade=4, edge_fade=2)

    raw = [0.0] * (135 * 480) + [0.2] * (100 * 480) + [0.0] * (160 * 480) + [0.2] * (100 * 480) + [0.0] * (90 * 480)
    processed, record = voice.postprocess_demo2(raw, 48000)
    assert len(processed) == (129600 - 59040) + (243360 - 172800) - 480
    assert record == {
        **voice.DEMO2_POSTPROCESS,
        'name': 'demo2-silence-trim-v1',
        'raw_duration_seconds': round(len(raw) / 48000, 3),
        'removed_seconds': round((len(raw) - len(processed)) / 48000, 3),
        'internal_cuts': 1,
    }


def test_original_set_never_calls_postprocess(tmp_path, monkeypatch):
    import json
    import math
    import sys
    import types

    def fail_postprocess(*_args, **_kwargs):
        raise AssertionError('postprocess_demo2 must not be called for --set original')

    monkeypatch.setattr(voice, 'postprocess_demo2', fail_postprocess)

    model = _placeholder_model(tmp_path)
    output_err = tmp_path / 'err_out'
    output_err.mkdir()
    (output_err / 'warm-welcome.wav').write_bytes(b'existing')
    with pytest.raises(ValueError, match='unrelated audio'):
        voice.main(['--model', str(model), '--output', str(output_err)])

    class FakeArray(list):
        def reshape(self, _shape):
            return self

        def tolist(self):
            return list(self)

        def __abs__(self):
            return FakeArray(abs(x) for x in self)

        def __pow__(self, power):
            return FakeArray(x ** power for x in self)

    fake_np = types.ModuleType('numpy')
    fake_np.float32 = float
    fake_np.asarray = lambda seq, dtype=None: FakeArray(float(x) for x in seq)
    fake_np.isfinite = lambda arr: [math.isfinite(x) for x in arr]
    fake_np.all = lambda seq: all(seq)
    fake_np.abs = lambda arr: abs(arr)
    fake_np.max = lambda arr: max(arr)
    fake_np.mean = lambda arr: sum(arr) / len(arr)
    fake_np.sqrt = lambda val: math.sqrt(val)

    fake_sf = types.ModuleType('soundfile')
    fake_sf.write = lambda path, wav, sr, subtype=None: Path(path).write_bytes(f'{sr}:{len(wav)}:{subtype}'.encode())

    fake_torch = types.ModuleType('torch')
    fake_torch.set_num_threads = lambda _n: None
    fake_torch.set_num_interop_threads = lambda _n: None

    fake_voxcpm = types.ModuleType('voxcpm')

    class FakeVoxCPM:
        def __init__(self, *_a, **_kw):
            self.tts_model = types.SimpleNamespace(sample_rate=48000)

        def generate(self, **_kw):
            return [0.25] * 4800

    fake_voxcpm.VoxCPM = FakeVoxCPM

    monkeypatch.setitem(sys.modules, 'numpy', fake_np)
    monkeypatch.setitem(sys.modules, 'soundfile', fake_sf)
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    monkeypatch.setitem(sys.modules, 'voxcpm', fake_voxcpm)

    out_orig = tmp_path / 'orig_out'
    assert voice.main(['--model', str(model), '--output', str(out_orig), '--only', 'warm-welcome']) == 0
    orig_receipt = json.loads((out_orig / 'receipt.json').read_text())
    assert 'postprocess' not in orig_receipt['samples'][0]

    out_demo2 = tmp_path / 'demo2_out'
    with pytest.raises(AssertionError, match='postprocess_demo2 must not be called'):
        voice.main(['--model', str(model), '--output', str(out_demo2), '--set', 'demo2', '--only', 'qingqing-hello'])
