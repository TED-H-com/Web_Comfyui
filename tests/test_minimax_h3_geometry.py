"""No-GPU contract tests for complete-source H3 keyframe fitting."""
import copy
import hashlib
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'webui'))
from webui import minimax_h3_native as N
from webui import minimax_h3_web_executor as E
from webui import minimax_h3_readiness as R


def graph(frames=('first_frame','last_frame'), resolution=(864,480)):
    return N.build_i2v_prompt(prompt='Preserve the supplied adult character. Quiet room.',
        resolution=resolution, frames=124, seed=73, acceleration='stock',
        output_prefix='Castle/minimax-h3/i2v/test',
        **{name:f'{name}.png' for name in frames})


@pytest.mark.parametrize('frames',[('first_frame',),('last_frame',),('first_frame','last_frame')])
@pytest.mark.parametrize('resolution',sorted(N.ALLOWED_RESOLUTIONS))
def test_every_canvas_and_keyframe_keeps_loader_identity(frames,resolution):
    g=graph(frames,resolution)
    assert g['6']['inputs']['noise_seed']==73
    assert g['1']['inputs']['unet_name']==N.FL2VA_MODEL
    for index,field in enumerate(frames):
        loader,pad=str(15+index),str(17+index)
        assert g[loader]=={'class_type':'LoadImage','inputs':{'image':f'{field}.png'}}
        assert g['5']['inputs'][field]==[pad,0]
        assert g[pad]=={'class_type':'ResizeAndPadImage','inputs':{
            'image':[loader,0],'target_width':resolution[0],'target_height':resolution[1],
            'padding_color':'black','interpolation':'lanczos'}}
    assert {node['class_type'] for key,node in g.items() if int(key)>=17}=={'ResizeAndPadImage'}
    assert set(g['5']['inputs']) & {'first_frame','last_frame'}==set(frames)


def test_validated_media_hashes_still_bind_original_bytes(tmp_path):
    g=graph()
    for name in ('first_frame','last_frame'):
        (tmp_path/f'{name}.png').write_bytes(f'original-{name}'.encode())
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    contract=E._validate_graph_contract(g,input_root=tmp_path)
    assert len(contract.media_evidence)==2
    for media in contract.media_evidence:
        assert media['sha256']==hashlib.sha256(before[media['basename']]).hexdigest()
    assert {path.name:path.read_bytes() for path in tmp_path.iterdir()}==before


@pytest.mark.parametrize('mutation',[
    lambda g:g['17']['inputs'].__setitem__('target_width',480),
    lambda g:g['17']['inputs'].__setitem__('target_height',True),
    lambda g:g['17']['inputs'].__setitem__('padding_color','white'),
    lambda g:g['17']['inputs'].__setitem__('interpolation','bilinear'),
    lambda g:g['17']['inputs'].__setitem__('crop','center'),
    lambda g:g['17']['inputs'].__setitem__('image',['16',0]),
    lambda g:g['17']['inputs'].__setitem__('image',['15',False]),
    lambda g:g['17']['inputs'].__setitem__('image',['missing',0]),
    lambda g:g['5']['inputs'].__setitem__('first_frame',['15',0]),
    lambda g:g['5']['inputs'].__setitem__('last_frame',['17',0]),
    lambda g:g.__setitem__('19',copy.deepcopy(g['17'])),
])
def test_invalid_fitting_rejected_before_worker_submission(tmp_path,mutation):
    for name in ('first_frame','last_frame'):
        (tmp_path/f'{name}.png').write_bytes(b'original source')
    g=graph();mutation(g)
    with pytest.raises(E.WebGraphExecutionError) as caught:
        E._validate_graph_contract(g,input_root=tmp_path)
    assert caught.value.code=='graph_geometry_invalid'


def test_missing_runtime_fit_node_is_a_preflight_failure():
    g=graph()
    object_info={node['class_type']:{} for node in g.values() if node['class_type']!='ResizeAndPadImage'}
    with pytest.raises(E.guarded.RunnerError) as caught:
        E.guarded.verify_object_info(object_info,g)
    assert caught.value.code=='comfy_nodes_missing'


def test_only_i2v_requires_fit_node_and_rotates_graph_digest():
    current={key:R.graph_contract_sha256(key) for key in R.H3_CONTRACTS}
    original=R._native.build_i2v_prompt
    def legacy(**kwargs):
        g=original(**kwargs)
        for field in ('first_frame','last_frame'):
            if field in g['5']['inputs']:
                pad=g['5']['inputs'][field][0]
                g['5']['inputs'][field]=g.pop(pad)['inputs']['image']
        return g
    with patch.object(R._native,'build_i2v_prompt',legacy):
        old={key:R.graph_contract_sha256(key) for key in R.H3_CONTRACTS}
    for key,contract in R.H3_CONTRACTS.items():
        assert ('ResizeAndPadImage' in contract.required_nodes)==(contract.mode=='i2v')
        assert (current[key]!=old[key])==(contract.mode=='i2v')
        if contract.mode=='i2v':
            payload=R._graph_contract_payload(contract)
            assert payload['graph']['17']['inputs']['target_width']=={'slot':'width'}
            assert payload['graph']['17']['inputs']['target_height']=={'slot':'height'}


@pytest.mark.parametrize('variant',['first','last','first-last'])
@pytest.mark.parametrize('aspect',['portrait','landscape','square','portrait-preview','landscape-preview','square-preview'])
def test_hosted_six_aspects_bind_every_pad_and_keep_uploaded_slots(variant,aspect):
    from webui import compute_recipe_catalog as C
    from webui import hosted_compute as H
    template=next(t for t in C.recipe_catalog()['templates'] if t['id']==f'native-h3-{variant}-v2')
    assert template['adaptation']=='preserve_aspect_black_pad'
    recipe=template['recipe']
    settings={'integrated_multimodal_description':'A synthetic adult smiles.',
              'overall_soundscape':'Quiet room.','non_diegetic_music':'No music.',
              'aspect':aspect,'frames':'124','seed':73,'acceleration':'stock'}
    request={'featureId':'h3-image-video','settings':settings,
             'files':{slot:{} for slot in template['input_slots']}}
    uploaded={slot:f'owned-{slot}.png' for slot in template['input_slots']}
    compiled=copy.deepcopy(recipe['workflow'])
    mappings=H._compile(recipe,request,uploaded,run_seed=73)
    for binding,value in mappings:
        compiled[binding['node_id']]['inputs'][binding['input']]=value
    width,height=C.ASPECTS[aspect]
    assert (compiled['5']['inputs']['width'],compiled['5']['inputs']['height'])==(width,height)
    for index,slot in enumerate(template['input_slots']):
        assert compiled[str(15+index)]['inputs']['image']==uploaded[slot]
        pad=compiled[str(17+index)]['inputs']
        assert (pad['target_width'],pad['target_height'])==(width,height)
        assert pad['image']==[str(15+index),0]
    # RunningHub gets identical semantic overrides after the same graph is
    # published. This is a mapping test, not a provider availability claim.
    rh=copy.deepcopy(recipe);rh.pop('workflow');rh['workflow_id']='synthetic-workflow'
    assert H._compile(rh,request,uploaded,run_seed=73)==mappings


def test_existing_i2v_description_explains_local_black_padding():
    from webui import minimax_h3_product as P
    features={item['id']:item for item in P.feature_definitions()}
    image=features['h3-image-video']
    assert '本機原生流程：首尾格保留完整畫面，比例不同時等比例補黑邊。' in image['note']
    assert 'ResizeAndPadImage' in image['requiredNodes']
    for feature in ('h3-text-video','h3-reference-video'):
        assert 'ResizeAndPadImage' not in features[feature]['requiredNodes']
        assert '等比例補黑邊' not in features[feature]['note']
