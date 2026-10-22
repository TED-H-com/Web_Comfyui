"""Static app guides stay tied to real forms/builders without server execution."""
import ast
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.app_guide_catalog import SOURCE_FILES, StaticSource, catalog


@pytest.fixture
def fixture_root(tmp_path):
    for relative in SOURCE_FILES:
        target=tmp_path/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    return tmp_path


def test_actual_forms_and_builder_models():
    result=catalog(ROOT)
    assert len(result['studios'])==4
    assert len(result['features'])==len(result['guides'])==23
    features={f['id']:f for f in result['features']}
    guides={g['id']:g for g in result['guides']}
    z=guides['text2img']
    assert z['templates']==[{'path':'webui/server.py#zimage_prompt','role':'實際程式圖建構器；不從社群 JSON 載入'}]
    assert z['builder_symbol']=='zimage_prompt'
    assert z['defaults']['steps']==8 and z['defaults']['cfg']==1
    assert z['defaults']['useDopsd'] is False
    assert z['builder_defaults']['sampler_name']=='res_multistep'
    assert z['builder_defaults']['scheduler']=='simple'
    models={m['name']:m for m in z['models']}
    assert models['qwen_3_4b.safetensors']['directory_hint']=='text_encoders'
    assert models['ae.safetensors']['directory_hint']=='vae'
    assert 'optional' in models['dopsd_white_zimage_turbo_comfy.safetensors']['note']
    assert 'workflows/' not in z['templates'][0]['path']
    for field in features['qwen21-text2img']['fields']:
        if field['type']=='select':assert all(isinstance(opt,dict) for opt in field['options'])
    assert next(f for f in features['text2img']['fields'] if f['id']=='steps')['advanced'] is True
    assert features['text2img']['icon']=='🎨'
    assert next(f for f in features['text2img']['fields'] if f['id']=='positive')['presets']
    assert features['first-last-frame']['uiHidden'] is True
    assert features['first-last-frame']['apiEnabled'] is False
    assert features['clipmode-character-photoshoot']['characterOnly'] is True
    assert guides['video-loop']['models']==[]
    assert guides['post-generator']['templates']==[]
    assert guides['voice-library']['templates']==[]


def test_h3_qwen_metadata_and_reference_templates():
    result=catalog(ROOT)
    guides={g['id']:g for g in result['guides']}
    features={f['id']:f for f in result['features']}
    assert len(features['h3-reference-video']['uploads'])==15
    seed=next(f for f in features['h3-text-video']['fields'] if f['id']=='seed')
    assert seed['type']=='integer-string' and seed['maxLength']==20
    for id in ['h3-text-video','h3-image-video','h3-reference-video']:
        guide=guides[id]
        assert guide['builder_defaults']['steps']==20
        assert guide['builder_defaults']['fps']==24
        assert len(guide['models'])==4
        assert '參考' in guide['templates'][1]['role']
    assert {m['name'] for m in guides['qwen21-edit']['models']}=={
        'qwen_image_2.1_int8_convrot.safetensors','qwen3vl_8b_int8_convrot.safetensors','qwen_image_2.1_vae_bf16.safetensors'}
    assert guides['qwen21-edit']['builder_defaults']['sampler_name']=='euler'


def test_never_executes_server_or_reads_environment(fixture_root,monkeypatch):
    monkeypatch.setenv('CASTLE_VOICE_SPEAK_MAX_CHARS','1')
    source=fixture_root/'webui/server.py'
    source.write_text(source.read_text()+"\nraise RuntimeError('MUST NOT EXECUTE SERVER')\n")
    imported_before={name for name in ('server','webui.server') if name in sys.modules}
    result=catalog(fixture_root)
    voice=next(f for f in result['features'] if f['id']=='voice-speak')
    assert next(f for f in voice['fields'] if f['id']=='text')['maxLength']==2400
    assert {name for name in ('server','webui.server') if name in sys.modules}==imported_before


def test_form_and_loader_updates_are_derived_from_source(fixture_root):
    source=fixture_root/'webui/server.py'
    text=source.read_text().replace('z_image_turbo_bf16.safetensors','replacement_model.safetensors')
    text=text.replace('"title": "文生圖",','"title": "測試文生圖",',1)
    source.write_text(text)
    result=catalog(fixture_root)
    guide=next(g for g in result['guides'] if g['id']=='text2img')
    assert guide['title']=='測試文生圖'
    assert 'replacement_model.safetensors' in {m['name'] for m in guide['models']}


@pytest.mark.parametrize('expression',["__import__('os').system('false')", "open('/etc/passwd').read()", "os.environ.get('SECRET')", "(1).__class__"])
def test_unapproved_ast_never_runs(expression,tmp_path):
    source=tmp_path/'source.py';source.write_text('X='+expression)
    with pytest.raises(ValueError):StaticSource(source).name('X',{})


@pytest.mark.parametrize('field,value', [('source','../../outside.py'),('source_files',['/etc/passwd']),('templates',[{'path':'../private.json','role':'x'}])])
def test_manifest_cannot_expand_file_reads(fixture_root,field,value):
    path=fixture_root/'manifests/app_guides/catalog.json'
    data=json.loads(path.read_text());data['features']['text2img'][field]=value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Undeclared'):catalog(fixture_root)


def test_symlink_source_rejected(fixture_root,tmp_path):
    path=fixture_root/'webui/qwen_image21.py'
    content=path.read_text();path.unlink()
    target=tmp_path/'outside.py';target.write_text(content)
    path.symlink_to(target)
    with pytest.raises(ValueError,match='symlink'):catalog(fixture_root)


def test_guide_cannot_claim_unrelated_app_builder(fixture_root):
    path=fixture_root/'manifests/app_guides/catalog.json'
    data=json.loads(path.read_text());data['features']['text2img']['symbol']='ip_blend_prompt'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='registered app builder'):catalog(fixture_root)
