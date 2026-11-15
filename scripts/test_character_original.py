"""A8: canonical source survives binding and reaches an executed conditioning node."""
import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'webui'))
spec=importlib.util.spec_from_file_location('a8_server',ROOT/'webui/server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

@pytest.fixture
def character(tmp_path):
    image=tmp_path/'canonical.png';image.write_bytes(b'canonical test image')
    return {'id':'fixture','name':'Fixture','kind':'example-character','visualClass':'anime','actions':[], '_test_path':image}

@pytest.mark.parametrize('feature', sorted(server.CHARACTER_REFERENCE_SLOTS))
def test_default_and_override(character,feature):
    with patch.object(server,'character_asset_path',return_value=character['_test_path']):
        files,receipt=server.bind_character_original(character,feature,{})
        slot=server.CHARACTER_REFERENCE_SLOTS[feature]
        assert files[slot]['_characterAssetPath']==str(character['_test_path'])
        assert receipt['sha256']==server.sha256_file(character['_test_path'])
        override={slot:{'serverFile':'replacement.png'}}
        bound,receipt=server.bind_character_original(character,feature,override)
        assert bound==override and receipt['kind']=='character-reference-override'
        assert override=={slot:{'serverFile':'replacement.png'}}

@pytest.mark.parametrize('feature',sorted(set(server.CHARACTER_REFERENCE_SLOTS) & set(server.FEATURE_BY_ID)))
def test_original_is_executed_conditioning(feature):
    character={'id':'fixture','visualClass':'anime'}
    f=server.character_feature_definition(server.FEATURE_BY_ID[feature],character)
    settings={x['id']:x['default'] for x in f.get('fields',[]) if 'default' in x}
    settings.update(seed=20261111,instruction='同一角色的自然半身肖像。',positive='portrait, natural expression, soft studio lighting',
                    _characterId='fixture',_characterVisualClass='anime',description='角色自然站立，輕微呼吸與眨眼。')
    if feature.startswith('h3-'):
        settings={'seed':'20261111','_characterId':'fixture','_characterVisualClass':'anime','description':'角色自然站立，輕微呼吸與眨眼。','aspect':'portrait','quality':'quality','motionProfile':'gentle'}
    uploads={server.CHARACTER_REFERENCE_SLOTS[feature]:'canonical.png'}
    if feature=='image-blend':uploads['image2']='other.png'
    if feature in ('dance','video-replace'):
        uploads['video']='motion.mp4';settings['length']=81
    if feature=='lipsync':uploads.update(video='video.mp4',voice='voice.wav')
    graph=server.BUILDERS[f['builder']](settings,uploads)
    loads=[key for key,node in graph.items() if node.get('class_type')=='LoadImage' and node['inputs'].get('image')=='canonical.png']
    assert loads,feature
    # Walk backwards from executed output nodes. A decorative disconnected LoadImage fails.
    executed=set()
    def visit(key):
        if key in executed:return
        executed.add(key)
        for value in graph[key].get('inputs',{}).values():
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and value[0] in graph:visit(value[0])
    for key,node in graph.items():
        if node['class_type'] in {'SaveImage','VHS_VideoCombine','SaveVideo','SaveAudio','SaveAudioMP3'}:visit(key)
    assert set(loads)&executed,(feature,[n['class_type'] for n in graph.values()])

@pytest.mark.parametrize('feature', sorted(server.FEATURE_BY_ID))
def test_public_feature_unchanged(feature):
    f=server.FEATURE_BY_ID[feature];before=copy.deepcopy(f)
    assert server.character_feature_definition(f,None) is f
    assert f==before



def test_missing_original_fails_closed(character):
    with patch.object(server,'character_asset_path',return_value=None):
        with pytest.raises(ValueError,match='原圖'):
            server.bind_character_original(character,'text2img',{})


def test_sdxl_original_preserves_lora_and_trigger():
    graph={'base':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'sd_xl_base_1.0.safetensors'}},
           'lora':{'class_type':'LoraLoaderModelOnly','inputs':{'model':['base',0],'lora_name':'existing.safetensors','strength_model':1}},
           'text':{'class_type':'CLIPTextEncode','inputs':{'text':'existing_trigger, portrait','clip':['base',1]}},
           'sample':{'class_type':'KSampler','inputs':{'model':['lora',0],'positive':['text',0]}}}
    bound=server.attach_sdxl_original(graph,'canonical.png')
    assert bound['lora']==graph['lora'] and bound['text']==graph['text']
    condition=bound[bound['sample']['inputs']['model'][0]]
    assert condition['inputs']['model']==['lora',0]
    assert bound[condition['inputs']['image'][0]]['inputs']['image']=='canonical.png'
    assert graph['sample']['inputs']['model']==['lora',0]



def test_public_lipsync_keeps_original_builder():
    public=server.character_feature_definition(server.FEATURE_BY_ID['lipsync'],None)
    graph=server.BUILDERS[public['builder']]({}, {'video':'video.mp4','voice':'voice.wav'})
    assert graph['3']['inputs']['images']==['1',0]
    assert all(n['class_type']!='ReActorFaceSwap' for n in graph.values())


@pytest.mark.parametrize('feature,builder', [('text2img','zimage'),('qwen21-text2img','qwen21_t2i'),('h3-text-video','minimax_h3_t2v'),('character-angles','qwen21_angles')])
def test_public_engine_contract(feature,builder):
    assert server.character_feature_definition(server.FEATURE_BY_ID[feature],None)['builder']==builder


def test_angles_character_only_engine():
    public=server.FEATURE_BY_ID['character-angles']
    role=server.character_feature_definition(public,{'id':'fixture','visualClass':'anime'})
    assert role['builder']=='character_angles'
    assert '2511' in role['subtitle']
    assert public['builder']=='qwen21_angles'
    assert next(f['default'] for f in public['fields'] if f['id']=='batchSize')==2
    assert next(f['default'] for f in role['fields'] if f['id']=='batchSize')==3







def test_clipmode_override_is_sealed_and_worker_rebuilds_same_graph(tmp_path):
    from PIL import Image
    upload=tmp_path/'override.png';Image.new('RGB',(32,32),'green').save(upload)
    settings={'seed':1,'prompt':'portrait','prefix':'test/original','_characterGender':'female'}
    verified={'identity_route':server.CLIPMODE_REALISTIC_IDENTITY_ROUTE,'profile_input_name':'canonical.png'}
    with patch.object(server,'COMFY_INPUT',tmp_path), patch.object(server,'verify_clipmode_character_photoshoot_files',return_value=verified):
        staged=server.bind_clipmode_reference_override(settings,upload.name)
        initial=server.clipmode_character_photoshoot_prompt(settings,{'image':staged})
        rebuilt=server.clipmode_character_photoshoot_prompt(settings,{})
        assert initial==rebuilt
        assert initial['2']['inputs']['image']==staged
        assert settings['_characterReferenceOverride']['sha256']==server.sha256_file(tmp_path/staged)
        (tmp_path/staged).write_bytes(b'tampered')
        with pytest.raises(ValueError,match='驗證'):
            server.clipmode_character_photoshoot_prompt(settings,{})



def test_provider_default_binds_full_original_and_override_wins(tmp_path):
    original=tmp_path/'full-original.png';original.write_bytes(b'full-resolution-original')
    c={'id':'clipmode.character:fixture','kind':'provider_character','visualClass':'realistic'}
    reference={'input_name':original.name,'sha256':server.sha256_file(original)}
    with patch.object(server,'COMFY_INPUT',tmp_path):
        files,receipt=server.bind_character_original(c,'text2img',{},reference)
        assert files['image']['_characterAssetPath']==str(original)
        assert receipt['sha256']==reference['sha256']
        replacement={'image':{'serverFile':'selected.png'}}
        assert server.bind_character_original(c,'text2img',replacement,None)[0]==replacement


def test_public_angles_requirements_match_qwen_edit():
    edit = next(feature for feature in server.qwen21_feature_definitions() if feature['id'] == 'qwen21-edit')
    angles = server.FEATURE_BY_ID['character-angles']
    assert angles['requiredModels'] == edit['requiredModels']
    assert angles['requiredNodes'] == edit['requiredNodes']


def test_provider_character_studio_entry_uses_store_name():
    with patch.dict(sys.modules, {'character_identity': None, 'provider_library': None}):
        row = {'character_id': 'clipmode.character:fixture', 'display_name': 'Fixture Name', 'visual_class': 'anime'}
        assert server.provider_character_studio_entry(row) == {
            'id': 'clipmode.character:fixture', 'name': 'Fixture Name', 'kind': 'provider_character',
            'visualClass': server.normalized_visual_class('anime'), 'actions': []}
        unnamed = {'character_id': 'clipmode.character:fixture', 'display_name': ''}
        assert server.provider_character_studio_entry(unnamed)['name'] == 'clipmode.character:fixture'
