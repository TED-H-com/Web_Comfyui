"""Topology, representative variants and privacy of static app diagrams."""
import ast
import json
from pathlib import Path
import shutil
import socket
import sys
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.app_graph_catalog import SOURCE_FILES, Source, graphs
from scripts.app_guide_catalog import catalog


@pytest.fixture(scope='module')
def registry():return catalog(ROOT)


@pytest.fixture
def fixture_root(tmp_path):
    for relative in SOURCE_FILES:
        dest=tmp_path/relative;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,dest)
    return tmp_path


def edges(graph):return {(x['from'],x['to'],x.get('from_slot'),x.get('to_slot')) for x in graph['links']}


def test_all_features_have_valid_bilingual_diagrams(registry):
    output=graphs(ROOT,registry)
    assert set(output)=={f['id'] for f in registry['features']}|{'character-create'}
    assert len(output)==24
    for id,g in output.items():
        assert g['kind'] in {'comfyui','process'}
        assert g['source'].startswith('webui/')
        assert g['evidence'] and g['evidence_en']
        assert len(g['notes'])==len(g['notes_en'])
        assert len(g['nodes'])>=3 and g['links']
        ids={n['id'] for n in g['nodes']}
        assert len(ids)==len(g['nodes'])
        assert all(e['from'] in ids and e['to'] in ids for e in g['links'])
        if g['kind']=='process':assert all(n.get('title_en') for n in g['nodes'])
    json.dumps(output,allow_nan=False)


def test_real_zimage_topology_and_models(registry):
    g=graphs(ROOT,registry)['text2img']
    n={n['id']:n for n in g['nodes']}
    assert n['1']['inputs']['unet_name']=='z_image_turbo_bf16.safetensors'
    assert n['2']['inputs']['clip_name']=='qwen_3_4b.safetensors'
    assert n['3']['inputs']['vae_name']=='ae.safetensors'
    assert ('1','7',0,'model') in edges(g)
    assert ('3','9',0,'vae') in edges(g)
    assert ('4','7',0,'positive') in edges(g)
    assert ('5','7',0,'negative') in edges(g)
    assert not any(n['type']=='LoraLoaderModelOnly' for n in g['nodes'])
    assert 'useDopsd=false' in g['notes_en'][0]


def test_subject_composite_preserves_original_pixel_path(registry):
    blend=graphs(ROOT,registry)['image-blend']
    assert blend['source']=='webui/server.py#subject_blend_prompt'
    types={n['type'] for n in blend['nodes']}
    assert {'SAM3_Detect', 'ThresholdMask', 'ImageCompositeMasked'} <= types
    assert not types & {'IPAdapterAdvanced', 'KSampler', 'VAEDecode', 'EmptyLatentImage'}
    connections=edges(blend)
    assert ('1','6',0,'image') in connections  # Segment the subject.
    assert ('2','3',0,'input') in connections  # Resize only the background.
    assert ('1','3',0,'resize_type.match') in connections
    assert ('6','7',0,'mask') in connections
    assert ('7','8',0,'mask') in connections
    assert ('1','8',0,'source') in connections  # Original pixels bypass resizing.
    assert ('3','8',0,'destination') in connections
    assert ('8','9',0,'images') in connections


def test_qwen_branch_connections(registry):
    output=graphs(ROOT,registry)
    q=output['qwen21-edit']
    assert ('11','4',0,'images.image_1') in edges(q)
    assert ('5','7',0,'model') in edges(q)
    assert ('4','7',2,'latent_image') in edges(q)
    assert not any(n['type']=='EmptyLatentImage' for n in q['nodes'])
    t=output['qwen21-text2img']
    assert ('5','6',0,'width') in edges(t)
    assert ('5','6',1,'height') in edges(t)


def test_h3_audio_video_and_references(registry):
    output=graphs(ROOT,registry)
    ref=output['h3-reference-video']
    e=edges(ref)
    assert ('15','5',0,'ref_images.ref_image_0') in e
    assert ('30','5',0,'ref_videos.ref_video_0') in e
    assert ('30','5',2,'ref_video_audios.ref_video_audio_0') in e
    assert ('40','5',0,'ref_audios.ref_audio_0') in e
    assert ('12','13',0,'audio') in e and ('11','13',0,'images') in e
    assert any(n.get('inputs',{}).get('unet_name')=='minimax_h3_ref2va_pruned_int8_convrot.safetensors' for n in ref['nodes'])
    assert ('15','5',0,'first_frame') in edges(output['h3-image-video'])


def test_variants_not_combined_and_character_create_is_process(registry):
    output=graphs(ROOT,registry)
    character=output['clipmode-character-photoshoot']
    assert any(n['type']=='ReActorFaceSwap' for n in character['nodes'])
    assert not any(n['type']=='LoraLoaderModelOnly' for n in character['nodes'])
    assert any('mutually exclusive' in s for s in character['notes_en'])
    create=output['character-create']
    assert create['kind']=='process'
    assert any('does not train a LoRA' in s for s in create['notes_en'])
    assert any(n['type']=='sniff_experimental_image' for n in create['nodes'])
    assert any(n['type']=='upsert_character' for n in create['nodes'])
    assert ('sniff','quota',None,None) in edges(create)


def test_source_changes_update_edges_without_execution(fixture_root,registry,monkeypatch):
    def fail(*a,**kw):raise AssertionError('Network must not be used')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(urllib.request,'urlopen',fail)
    source=fixture_root/'webui/server.py'
    text=source.read_text().replace('"z_image_turbo_bf16.safetensors"','"new_model.safetensors"')
    text += "\nraise RuntimeError('SERVER MUST NOT EXECUTE')\n"
    source.write_text(text)
    before=set(sys.modules)
    out=graphs(fixture_root,registry)
    assert any(n.get('inputs',{}).get('unet_name')=='new_model.safetensors' for n in out['text2img']['nodes'])
    assert not ({'server','webui.server','torch'}&(set(sys.modules)-before))


def test_prompts_filenames_credentials_and_paths_never_export(fixture_root,registry):
    secret='aip_'+'SecretValue123'*3
    path=fixture_root/'webui/server.py'
    s=path.read_text().replace('"unet_name": "z_image_turbo_bf16.safetensors"','"unet_name": "/home/example/private.safetensors"')
    s=s.replace('"text": positive','"text": "'+secret+'"')
    s=s.replace('"image": scene_image','"image": "private-photo.png"')
    s=s.replace('"filename_prefix": prefix','"filename_prefix": "private-output"')
    s=s.replace('"type": "lumina2"','"type": "'+secret+'"')
    path.write_text(s)
    serialized=json.dumps(graphs(fixture_root,registry))
    for forbidden in [secret,'/home/example','private.safetensors','private-photo.png','private-output']:
        assert forbidden not in serialized


def test_symlink_source_rejected(fixture_root,registry,tmp_path):
    source=fixture_root/'webui/qwen_image21.py';outside=tmp_path/'other.py'
    source.rename(outside);source.symlink_to(outside)
    with pytest.raises(ValueError,match='symlink'):graphs(fixture_root,registry)


def test_missing_source_symbol_fails_closed(fixture_root,registry):
    source=fixture_root/'webui/server.py'
    source.write_text(source.read_text().replace('def zimage_prompt(', 'def removed_zimage_prompt('))
    with pytest.raises(ValueError,match='function changed'):graphs(fixture_root,registry)


def test_arbitrary_expression_calls_are_not_evaluated(tmp_path):
    p=tmp_path/'sample.py';p.write_text("X = open('/etc/passwd').read()\n")
    source=Source(p)
    from scripts.app_graph_catalog import UNKNOWN
    assert source.value(ast.parse('X',mode='eval').body) is UNKNOWN
