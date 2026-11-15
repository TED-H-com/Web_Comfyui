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
    assert len(voice.DEMO2_CASES) == 10
    assert len(voice.DEMO2_CLONES) == 2
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
