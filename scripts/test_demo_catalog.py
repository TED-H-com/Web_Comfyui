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
        assert '預先準備' in example['note'] and 'not generated from your input' in example['note_en']
        assert 'benchmark' in example['note_en']


def test_outputs_match_declared_and_hash_verified_assets(app):
    root = ROOT / 'webui/docs/examples'
    provenance = json.loads((root / 'provenance.json').read_text())
    ledger = {row['file']: row for row in provenance['assets']}
    assert len(ledger) == len(provenance['assets'])
    referenced = set()
    for example in demos(app).values():
        output = example['output']
        assert output['kind'] in {'image', 'video', 'audio', 'text', 'character', 'gallery'}
        for image in output['images']:
            assert image['src'].startswith('./assets/examples/')
            name = image['src'].removeprefix('./assets/examples/')
            assert '/' not in name and name in ledger
            assert image['alt'] and image['alt_en']
            assert ledger[name]['kind'] == 'image'
            referenced.add(name)
        if output['kind'] == 'video':
            video = output['video']
            assert video['src'].startswith('./assets/examples/')
            name = video['src'].removeprefix('./assets/examples/')
            assert name in ledger and ledger[name]['kind'] == 'video'
            assert video['poster'] == output['images'][0]['src']
            assert 'camera zoom' in example['note_en']
            assert ledger[name]['audio'] is False
            assert 3 <= ledger[name]['duration_seconds'] <= 5
            assert all(source in ledger for source in ledger[name]['derived_from'])
            referenced.add(name)
        if output['kind'] == 'audio':
            assert output['text'] and output['text_en']
            assert 'audio' not in output and 'video' not in output
            assert 'transcript only' in example['note_en']
        if 'text' in output:
            assert output['text_en']
    assert referenced == set(ledger)
    for name, record in ledger.items():
        path = root / name
        assert not path.is_symlink() and path.is_file()
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record['sha256']
        assert len(payload) == record['bytes']
        assert record['source'] and record['tool']


def test_generated_images_retain_no_exif_or_text_metadata():
    root = ROOT / 'webui/docs/examples'
    provenance = json.loads((root / 'provenance.json').read_text())
    images = [row for row in provenance['assets'] if row['kind'] == 'image']
    assert 3 <= len(images) <= 5
    for record in images:
        assert record['tool'] == 'image_gen.imagegen'
        assert record['prompt'] and 'fictional' in record['prompt']
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


def test_examples_are_fresh_objects_and_do_not_modify_source_guides(app):
    original = deepcopy(app)
    first = demos(app)
    first['text2img']['steps'][0] = 'changed locally'
    first['text2img']['output']['images'][0]['alt'] = 'changed locally'
    second = demos(app)
    assert second['text2img']['steps'][0] != 'changed locally'
    assert second['text2img']['output']['images'][0]['alt'] != 'changed locally'
    assert app == original


def test_new_feature_cannot_ship_without_matching_demo_content(app):
    changed = deepcopy(app)
    changed['features'].append({'id': 'unmapped-feature'})
    with pytest.raises(ValueError, match='every app feature'):
        demos(changed)
