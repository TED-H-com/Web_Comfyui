"""The demo-media exception permits only reviewed bytes at two exact locations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_public_release import inspect
from scripts.public_demo_assets import PUBLIC_DEMO_ASSETS

EXAMPLES = ROOT / 'webui/docs/examples'


@pytest.mark.parametrize('parent', ['webui/docs/examples', 'assets/examples'])
@pytest.mark.parametrize('name', sorted(PUBLIC_DEMO_ASSETS))
def test_only_reviewed_demo_bytes_are_allowed(parent, name):
    payload = (EXAMPLES / name).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == PUBLIC_DEMO_ASSETS[name]
    assert inspect(Path(parent) / name, payload) == []


@pytest.mark.parametrize('parent', ['webui/docs/examples', 'assets/examples'])
def test_modified_approved_media_is_rejected_even_with_original_filename(parent):
    name = next(iter(PUBLIC_DEMO_ASSETS))
    original = (EXAMPLES / name).read_bytes()
    # Both replacement content and a single appended metadata chunk lose the
    # exception. Updating a separate provenance file cannot change the pin.
    for payload in (b'RIFF\0private runtime fixture', original + b'private metadata fixture'):
        assert inspect(Path(parent) / name, payload)


@pytest.mark.parametrize('relative', [
    'assets/examples/renamed.webp',
    'assets/examples/subfolder/mountain-teahouse.webp',
    'webui/docs/examples/private-image.webp',
    'characters/mountain-teahouse.webp',
    'workflows/user-saved/mountain-teahouse.webp',
    'runtime/mountain-teahouse.webp',
    'assets/mountain-teahouse.webp',
    'mountain-teahouse.webp',
])
def test_reviewed_bytes_are_not_a_blanket_media_or_runtime_exception(relative):
    payload = (EXAMPLES / 'mountain-teahouse.webp').read_bytes()
    assert inspect(Path(relative), payload)


@pytest.mark.parametrize('relative', ['assets/examples/new.mp4', 'assets/examples/new.webp',
                                     'assets/examples/users.json', 'assets/examples/.env',
                                     'assets/examples/private.bin'])
def test_unlisted_binary_and_runtime_data_remain_forbidden(relative):
    assert inspect(Path(relative), b'\0synthetic private runtime fixture')


def test_pins_match_provenance_and_only_reviewed_assets_ship():
    provenance = json.loads((EXAMPLES / 'provenance.json').read_text())
    assets = provenance['assets']
    assert {row['file']: row['sha256'] for row in assets} == PUBLIC_DEMO_ASSETS
    assert assets
    assert {path.name for path in EXAMPLES.iterdir()} == set(PUBLIC_DEMO_ASSETS) | {'provenance.json'}
    serialized = json.dumps(provenance)
    assert not any(prefix in serialized for prefix in ('/home/', '/mnt/', '/media/', '/tmp/'))
    assert not inspect(Path('webui/docs/examples/provenance.json'), serialized.encode())


def scan(root):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/check_public_release.py'), '--root', str(root)],
                          capture_output=True, text=True, timeout=20)


def test_standalone_artifact_scan_accepts_only_listed_media(tmp_path):
    examples = tmp_path / 'assets/examples'
    examples.mkdir(parents=True)
    for name in PUBLIC_DEMO_ASSETS:
        (examples / name).write_bytes((EXAMPLES / name).read_bytes())
    assert scan(tmp_path).returncode == 0
    (examples / 'unexpected-upload.webp').write_bytes(b'\0synthetic user upload')
    assert scan(tmp_path).returncode == 1


@pytest.mark.parametrize('directory_link', [False, True])
def test_artifact_scan_rejects_symlinks_even_to_exact_approved_media(tmp_path, directory_link):
    artifact = tmp_path / 'site'
    (artifact / 'assets').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    name = next(iter(PUBLIC_DEMO_ASSETS))
    (outside / name).write_bytes((EXAMPLES / name).read_bytes())
    if directory_link:
        (artifact / 'assets/examples').symlink_to(outside, target_is_directory=True)
    else:
        examples = artifact / 'assets/examples'
        examples.mkdir()
        (examples / name).symlink_to(outside / name)
    result = scan(artifact)
    assert result.returncode == 1
    assert 'Symlink' in result.stdout


def test_staged_git_symlink_is_rejected_before_its_contents_are_interpreted(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
    path = root / 'webui/docs/examples/mountain-teahouse.webp'
    path.parent.mkdir(parents=True)
    path.symlink_to(EXAMPLES / path.name)
    subprocess.run(['git', 'add', 'webui/docs/examples/mountain-teahouse.webp'],
                   cwd=root, check=True, capture_output=True)
    result = scan(root)
    assert result.returncode == 1
    assert 'Symlink' in result.stdout


def test_git_scan_checks_staged_media_not_a_clean_worktree_replacement(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
    path = root / 'webui/docs/examples/mountain-teahouse.webp'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\0synthetic private upload')
    subprocess.run(['git', 'add', 'webui/docs/examples/mountain-teahouse.webp'],
                   cwd=root, check=True, capture_output=True)
    path.write_bytes((EXAMPLES / path.name).read_bytes())
    assert scan(root).returncode == 1
