"""Offline regressions: synthetic frames and detector, no model/GPU/network."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wav2lip_patch', ROOT / 'scripts/patch_wav2lip.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


@pytest.fixture
def node():
    np = pytest.importorskip('numpy')
    predictions = [[1, 1, 10, 10] for _ in range(4)]

    class Detector:
        def get_detections_for_batch(self, images):
            return [predictions[int(image[0, 0, 0])] for image in images]

    namespace = {
        'np': np, 'device': 'cpu', 'tqdm': lambda rows: rows,
        'face_detection': SimpleNamespace(
            FaceAlignment=lambda *args, **kwargs: Detector(),
            LandmarksType=SimpleNamespace(_2D=1)),
        'cv2': SimpleNamespace(resize=lambda face, size: np.full(
            (size[1], size[0], 3), face[0, 0, 0], dtype=np.uint8)),
        'get_smoothened_boxes': lambda boxes, T: boxes,
    }
    # Execute only the maintained replacement functions, never upstream imports.
    exec(patch.FACE_DETECT + '\n' + patch.DATAGEN, namespace)
    frames = [np.full((20, 20, 3), index, dtype=np.uint8) for index in range(4)]
    mels = [np.full((80, 16), index, dtype=float) for index in range(8)]
    return namespace, predictions, frames, mels


@pytest.mark.parametrize('mode,expected', [
    ('sequential', [0, 0, 1, 1, 2, 2, 3, 3]),
    ('repetitive', [0, 1, 2, 3, 0, 1, 2, 3]),
])
def test_complete_detection_preserves_every_mel_and_frame_alignment(node, mode, expected):
    ns, predictions, frames, mels = node
    batches = list(ns['datagen'](frames, mels, 2, mode))
    assert [int(frame[0, 0, 0]) for batch in batches for frame in batch[2]] == expected
    assert sum(len(batch[1]) for batch in batches) == len(mels)


@pytest.mark.parametrize('mode', ['sequential', 'repetitive'])
@pytest.mark.parametrize('missing', [0, 1, 3])
def test_missing_face_never_shifts_boxes_or_returns_shortened_video(node, mode, missing):
    ns, predictions, frames, mels = node
    predictions[missing] = None
    with pytest.raises(ValueError, match=f'第 {missing + 1} 個影格未偵測到人臉'):
        next(ns['datagen'](frames, mels, 2, mode))
    # No cv2.imwrite exists in the stub: failure must not write a private frame.


@pytest.mark.parametrize('rect', [[15, 1, 10, 10], [1, 40, 10, 45], [float('nan'), 1, 10, 10]])
def test_invalid_boxes_fail_before_cropping(node, rect):
    ns, predictions, frames, mels = node
    predictions[2] = rect
    with pytest.raises(ValueError, match='第 3 個影格'):
        list(ns['datagen'](frames, mels, 2, 'sequential'))


def test_incomplete_detector_response_fails(node):
    ns, _, frames, _ = node
    ns['face_detection'].FaceAlignment = lambda *a, **k: SimpleNamespace(
        get_detections_for_batch=lambda images: [])
    with pytest.raises(ValueError, match='影格數不一致'):
        ns['face_detect'](frames, 2)


def test_datagen_cannot_swallow_a_changed_detector_contract(node):
    ns, _, frames, mels = node
    ns['face_detect'] = lambda *args: []
    with pytest.raises(ValueError, match='影格數不一致'):
        list(ns['datagen'](frames, mels, 2, 'sequential'))


def test_crop_resize_error_propagates_instead_of_dropping_audio_frames(node):
    ns, _, frames, mels = node
    def broken_resize(*args):
        raise RuntimeError('synthetic resize failure')
    ns['cv2'].resize = broken_resize
    with pytest.raises(RuntimeError, match='synthetic resize failure'):
        list(ns['datagen'](frames, mels, 2, 'sequential'))


def test_batch_boundary_keeps_all_130_audio_chunks(node):
    ns, _, frames, mels = node
    mels = [mels[0]] * 130
    batches = list(ns['datagen'](frames, mels, 2, 'sequential'))
    assert [len(batch[2]) for batch in batches] == [128, 2]


@pytest.mark.parametrize('case', ['frames', 'mels', 'mode'])
def test_invalid_generator_inputs_fail_clearly(node, case):
    ns, _, frames, mels = node
    with pytest.raises(ValueError):
        list(ns['datagen']([] if case == 'frames' else frames,
            [] if case == 'mels' else mels, 2, 'unknown' if case == 'mode' else 'sequential'))


@pytest.fixture
def synthetic_source(monkeypatch):
    # Deliberately small authored fixture; no upstream module import or model.
    source = ('def face_detect(images, batch):\n    return []\n\n'
              'def datagen(frames, mels, batch, mode):\n    yield None\n\n'
              'def wav2lip_(out_images, mel_chunks):\n'
              '    mel_idx_multiplier = 80./30\n'
              '    return out_images\n')
    monkeypatch.setattr(patch, 'SOURCE_SHA256', patch.digest(source))
    result = patch.patched_source(source)
    monkeypatch.setattr(patch, 'PATCHED_SHA256', patch.digest(result))
    return source, result


def test_generated_output_count_is_checked_and_fps_unchanged(synthetic_source):
    _, result = synthetic_source
    fn = next(node for node in ast.parse(result).body
              if isinstance(node, ast.FunctionDef) and node.name == 'wav2lip_')
    namespace = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<synthetic>', 'exec'), namespace)
    assert namespace['wav2lip_']([1, 2], [1, 2]) == [1, 2]
    with pytest.raises(ValueError, match='拒絕回傳截短影片'):
        namespace['wav2lip_']([1], [1, 2])
    assert 'mel_idx_multiplier = 80./30' in result


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_patch_check_apply_and_idempotence_on_disk(tmp_path, synthetic_source, newline):
    source, result = synthetic_source
    target = tmp_path / patch.RELATIVE_SOURCE
    target.parent.mkdir()
    original = source.replace('\n', newline).encode()
    target.write_bytes(original)
    target.chmod(0o640)
    assert patch.patch_node(tmp_path) == 'ready'
    assert target.read_bytes() == original
    assert patch.patch_node(tmp_path, apply=True) == 'applied'
    assert target.read_bytes() == result.replace('\n', newline).encode()
    assert target.stat().st_mode & 0o777 == 0o640
    assert patch.patch_node(tmp_path, apply=True) == 'already_applied'
    assert not list(target.parent.glob('.wav2lip-patch-*'))


def test_unreviewed_source_is_never_modified(tmp_path):
    target = tmp_path / patch.RELATIVE_SOURCE
    target.parent.mkdir()
    original = b'# a different upstream version\n'
    target.write_bytes(original)
    with pytest.raises(ValueError, match='原始碼版本不符'):
        patch.patch_node(tmp_path, apply=True)
    assert target.read_bytes() == original


def test_installer_invokes_optional_archive_compatible_patch():
    install = (ROOT / 'install.sh').read_text()
    assert 'scripts/patch_wav2lip.py' in install
    assert '--node-dir "$wav2lip_dir" --apply' in install
    assert '[ -f "$wav2lip_dir/Wav2Lip/wav2lip_node.py" ]' in install
