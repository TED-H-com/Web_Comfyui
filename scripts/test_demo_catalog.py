"""Prepared examples are complete, attributed, local, and honestly labeled."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.app_guide_catalog import catalog
from scripts.demo_catalog import demos


@pytest.fixture(scope='module')
def app():
    return catalog(ROOT)


def test_every_real_feature_and_character_creation_has_bilingual_content(app):
    examples = demos(app)
    assert set(examples) == {row['id'] for row in app['features']} | {'character-create'}
    assert len(examples) == 24
    guides = {row['id']: row for row in app['guides']}
    for identifier, example in examples.items():
        assert identifier == example['id']
        for field in ('title', 'title_en', 'prompt', 'prompt_en', 'note', 'note_en'):
            assert isinstance(example[field], str) and example[field].strip()
        assert len(example['steps']) == len(example['steps_en']) >= 2
        assert all(isinstance(step, str) and step for step in example['steps'] + example['steps_en'])
        if identifier in guides:
            assert example['steps'] == guides[identifier]['steps']
        assert 'not generated from your input' in example['note_en']


def test_recorded_outputs_need_evidence_and_prepared_examples_are_distinct(app):
    root = ROOT / 'webui/docs/examples'
    provenance = json.loads((root / 'provenance.json').read_text())
    ledger = {row['file']: row for row in provenance['assets']}
    runs = {row['id']: row for row in provenance.get('runs', [])}
    assert len(ledger) == len(provenance['assets'])
    assert len(runs) == len(provenance.get('runs', []))
    verified = []
    for identifier, example in demos(app).items():
        output = example['output']
        assert output['kind'] in {'image', 'video', 'audio', 'text', 'character', 'gallery', 'unavailable'}
        if identifier == 'character-create':
            # Character creation is a form preview, not a claimed model result.
            assert output == {'kind': 'character', 'images': []}
            assert example['evidence'] == {'kind': 'interface-simulation'}
            assert example['inputs'][0]['slot'] == 'photos'
            assert example['inputs'][0]['src'].removeprefix('./assets/examples/') in ledger
            assert 'nothing is saved' in example['note_en']
            continue
        assert output['kind'] != 'unavailable', identifier
        if example.get('evidence', {}).get('kind') == 'prepared-example':
            assert not output.get('run_id') and not example['evidence'].get('run_id')
            assert 'not a verified run of this workflow' in example['note_en']
            media = output.get('images', []) + output.get('audio_tracks', [])
            if output.get('video'):
                media.append(output['video'])
            assert media or output.get('text'), identifier
            for item in [*media, *example.get('inputs', [])]:
                assert item['src'].startswith('./assets/examples/')
                name = item['src'].removeprefix('./assets/examples/')
                assert name in ledger
                if item in output.get('audio_tracks', []):
                    assert ledger[name]['kind'] == 'audio'
                    assert item['transcript'] and item['title_en']
            continue
        # Every claimed result must refer to a successful run of THIS feature;
        # generic illustrations, transcripts and pan videos are not evidence.
        assert output['run_id'] in runs
        run = runs[output['run_id']]
        assert run['feature_id'] == identifier
        assert run['status'] == 'success'
        assert example['evidence']['run_id'] == run['id']
        verified.append(identifier)
        recorded_outputs = {row['file']: row['sha256'] for row in run['outputs']}
        for asset in output.get('images', []):
            assert asset['src'].startswith('./assets/examples/')
            name = asset['src'].removeprefix('./assets/examples/')
            assert name in ledger and name in recorded_outputs
            assert asset['alt'] and asset['alt_en']
        if output['kind'] == 'video':
            name = output['video']['src'].removeprefix('./assets/examples/')
            assert name in ledger and name in recorded_outputs
            assert ledger[name]['kind'] == 'video'
    assert verified, 'The public demo must include actual workflow results'
    for name, record in ledger.items():
        path = root / name
        assert '/' not in name and not path.is_symlink() and path.is_file()
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record['sha256']
        assert len(payload) == record['bytes']
        assert record['source'] and record['tool']


def test_voice_examples_are_original_recorded_audio_with_bound_transcripts():
    import wave

    root = ROOT / 'webui/docs/examples'
    provenance = json.loads((root / 'provenance.json').read_text())
    assets = {row['file']: row for row in provenance['assets']}
    runs = provenance['audio_runs']
    assert len(runs) == 9
    assert len({run['sample']['sha256'] for run in runs}) == 9
    for run in runs:
        sample = run['sample']
        path = root / sample['file']
        assert run['status'] == 'success' and run['engine'] == 'VoxCPM2'
        assert run['device'] == 'CPU' and run['settings']['device'] == 'cpu'
        assert run['model_revision'] and run['code_revision'] and run['license_source']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sample['sha256'] == assets[path.name]['sha256']
        review = run['validation']['transcription_review']
        assert review['audio_sha256'] == sample['sha256']
        assert review['expected'] == sample['text'] and review['normalized_content_match'] is True
        with wave.open(str(path)) as audio:
            assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
            assert audio.getframerate() == sample['sample_rate'] == 48000
            assert audio.getnframes() / audio.getframerate() == pytest.approx(sample['duration_seconds'])
        assert 0 < sample['rms'] < 1 and sample['peak'] < 1
        # PCM-only files cannot carry arbitrary tags or embedded personal metadata.
        data = path.read_bytes()
        assert data[:4] == b'RIFF' and data[8:12] == b'WAVE'
        offset = 12
        while offset < len(data):
            assert data[offset:offset + 4] in {b'fmt ', b'data'}
            size = struct.unpack('<I', data[offset + 4:offset + 8])[0]
            offset += 8 + size + size % 2
        assert offset == len(data)
    assert not any(prefix in json.dumps(runs) for prefix in ('/home/', '/mnt/', '/tmp/'))


def test_public_run_evidence_is_complete_and_bound_to_assets():
    provenance = json.loads((ROOT / 'webui/docs/examples/provenance.json').read_text())
    ledger = {row['file']: row for row in provenance['assets']}
    assert provenance.get('runs'), 'Missing GPU execution evidence'
    for run in provenance['runs']:
        assert run['status'] == 'success'
        assert run['executed_at'] and run['device']
        assert run['duration_seconds'] > 0
        workflow = run['workflow']
        graph = workflow['graph']
        assert graph and all(node.get('class_type') for node in graph.values())
        canonical = json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        assert hashlib.sha256(canonical).hexdigest() == workflow['sha256']
        assert run['outputs']
        for record in run['inputs']:
            assert record['sha256'] == ledger[record['file']]['sha256']
        for record in run['outputs']:
            asset = ledger[record['file']]
            assert record['sha256'] in {asset['sha256'], asset.get('source_sha256')}
        # A copied input must never become a claimed workflow output.
        assert not ({row['sha256'] for row in run['inputs']} &
                    {row['sha256'] for row in run['outputs']})
        serialized = json.dumps(run)
        assert not any(prefix in serialized for prefix in ('/home/', '/mnt/', '/media/', '/tmp/'))


def test_generated_images_retain_no_exif_or_text_metadata():
    root = ROOT / 'webui/docs/examples'
    provenance = json.loads((root / 'provenance.json').read_text())
    images = [row for row in provenance['assets'] if row['kind'] == 'image']
    assert images
    for record in images:
        payload = (root / record['file']).read_bytes()
        assert payload[:4] == b'RIFF' and payload[8:12] == b'WEBP'
        offset = 12
        kinds = []
        while offset < len(payload):
            size = struct.unpack('<I', payload[offset + 4:offset + 8])[0]
            kinds.append(payload[offset:offset + 4])
            offset += 8 + size + size % 2
        assert offset == len(payload)
        assert set(kinds) <= {b'VP8 ', b'VP8L', b'VP8X', b'ALPH'}
        assert b'VP8 ' in kinds or b'VP8L' in kinds


def test_subject_demo_preserves_person_pixels_and_changes_background():
    from PIL import Image, ImageChops

    root = ROOT / 'webui/docs/examples'
    example = demos(catalog(ROOT))['image-blend']
    provenance = json.loads((root / 'provenance.json').read_text())
    run = next(row for row in provenance['runs'] if row['id'] == example['output']['run_id'])
    proof = run['pixel_verification']
    source = Image.open(root / proof['source']).convert('RGB')
    result = Image.open(root / proof['output']).convert('RGB')
    mask = Image.open(root / proof['mask']).convert('L')
    assert source.size == result.size == mask.size
    assert {value for value, count in enumerate(mask.histogram()) if count} == {0, 255}
    assert mask.histogram()[255] == proof['subject_pixels'] > 100_000
    difference = ImageChops.difference(source, result)
    masked = Image.composite(difference, Image.new('RGB', source.size), mask)
    maximum = max(high for _, high in masked.getextrema())
    assert maximum == proof['max_subject_channel_difference'] <= 2
    assert mask.crop(proof['face_rectangle_xyxy']).getextrema() == (255, 255)
    face_difference = max(high for _, high in difference.crop(proof['face_rectangle_xyxy']).getextrema())
    assert face_difference == proof['max_face_channel_difference'] <= 2
    assert max(high for _, high in difference.getextrema()) > 50, 'The background must visibly change'
    assert example['evidence']['workflow'] == 'subject_blend_prompt'
    assert example['inputs'][0]['src'].endswith('/' + proof['source'])
    assert example['output']['images'][0]['src'].endswith('/' + proof['output'])


def test_examples_are_fresh_objects_and_do_not_modify_source_guides(app):
    original = deepcopy(app)
    first = demos(app)
    first['text2img']['steps'][0] = 'changed locally'
    first['text2img']['output']['images'].append({'alt': 'changed locally'})
    second = demos(app)
    assert second['text2img']['steps'][0] != 'changed locally'
    assert all(row['alt'] != 'changed locally' for row in second['text2img']['output']['images'])
    assert app == original


def test_new_feature_cannot_ship_without_matching_demo_content(app):
    changed = deepcopy(app)
    changed['features'].append({'id': 'unmapped-feature'})
    with pytest.raises(ValueError, match='every app feature'):
        demos(changed)
