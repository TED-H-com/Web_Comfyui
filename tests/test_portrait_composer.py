"""Public portrait graphs and catalog boundaries, using synthetic inputs only."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import pytest
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
import server
import reference_sources as refs
from qwen_image21 import compile_character_prompt


@pytest.mark.parametrize('style',['anime','realistic'])
def test_identity_graph_and_whole_scene_style(style):
    graph=compile_character_prompt({'instruction':'花園，外套，站姿','visualStyle':style},{'image':'synthetic.png'},seed=1,prefix='test')
    assert graph['11']['inputs']['image']=='synthetic.png'
    assert graph['4']['inputs']['images.image_1']==['11',0]
    assert '人物、身體、服裝、道具、場景與背景全部' in graph['4']['inputs']['prompt']
    assert ('全部以二維動畫插畫呈現' if style=='anime' else '全部以自然攝影呈現') in graph['4']['inputs']['prompt']
    assert graph['7']['inputs']['latent_image']==['4',2]
    with pytest.raises(ValueError):
        compile_character_prompt({'instruction':'花園'},{'image':'a.png','image2':'b.png'},seed=1,prefix='test')


@pytest.mark.parametrize('style',['anime','realistic'])
def test_character_capability_binds_avatar_and_rejects_replacement(monkeypatch,tmp_path,style):
    character={'id':'fixture','name':'Fixture','visualClass':style,'kind':'custom','actions':['photoshoot']}
    cap=next(c for c in server.character_capabilities(character) if c['featureId']=='character-photoshoot')
    assert cap['enabled'] and cap['serverBoundUploads']==['image']
    path=tmp_path/'fixture.png';Image.new('RGB',(32,40),'gray').save(path)
    monkeypatch.setattr(server,'character_asset_path',lambda *args:path)
    files,receipt=server.bind_character_capability_files(character,cap,{}, {})
    assert files['image']['_characterAssetPath']==str(path)
    assert receipt['sha256']==server.sha256_file(path)
    with pytest.raises(ValueError):server.bind_character_capability_files(character,cap,{'image':'other.png'}, {})
    disabled={**character,'actions':[]}
    with pytest.raises(ValueError):server.resolve_character_capability(disabled,'photoshoot','character-photoshoot',cap['variantId'])
    with pytest.raises(ValueError):server.apply_character_capability_settings(character,cap,{'visualStyle':'realistic' if style=='anime' else 'anime'})


def test_catalog_style_filters_search_random_and_custom_tags(monkeypatch):
    image='https://omilic.com/custom/fixture.png'
    raw=[]
    for style,source,tag in [('anime','https://ai-costume-anime-gallery.pages.dev/','photo'),('realistic','https://ai-costume-gallery.pages.dev/','anime'),('anime','custom','anime'),('realistic','custom','photo')]:
        raw.append({'id':style+source,'kind':'costume','source':source,'title':{'zh':'外套','en':'Coat'},'category':{'id':'street','zh':'日常穿搭'},'tags':[{'id':tag,'zh':tag}],'variants':[{'id':'front','label':{'zh':'正面'},'image':image,'original':image,'thumbnail':image}]})
    monkeypatch.setattr(refs,'catalog',lambda:refs.normalize({'items':raw}))
    for style in ['anime','realistic']:
        data=refs.listing({'kind':'costume','visualStyle':style,'category':'street','q':'Coat','random':'1'})
        assert data['total']==2 and data['randomItem']['visualStyle']==style
        assert all(i['visualStyle']==style for i in data['items'])
        assert refs.listing({'kind':'costume','visualStyle':style,'page':'2'})['items']==[]


def test_catalog_http_requires_login_and_validates_style(test_server):
    import urllib.request
    import urllib.error
    base,opener,cookie=test_server
    url=base+'/api/reference-sources?kind=costume&visualStyle=invalid'
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(url)
    assert error.value.code==401
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(urllib.request.Request(url,headers={'Cookie':cookie}))
    assert error.value.code==400


def test_parent_categories_cover_each_existing_detail_once():
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    module=Path(__file__).resolve().parents[1]/'webui/static/photo-presets.js'
    result=subprocess.run([node,'-e','const p=require(process.argv[1]);console.log(JSON.stringify({groups:p.groups,parents:p.parentGroups}));',str(module)],
                          capture_output=True,text=True,check=True,timeout=10)
    data=json.loads(result.stdout)
    for kind,count in [('outfit',5),('pose',6)]:
        parents=data['parents'][kind]
        children=[child for parent in parents for child in parent['categories']]
        assert len(parents)==count
        assert len(children)==len(set(children))
        assert set(children)==set(data['groups'][kind])


@pytest.mark.parametrize('style',['anime','realistic'])
def test_parent_filter_limits_pages_search_and_random_with_style(monkeypatch,style):
    items=[]
    for visual in ['anime','realistic']:
        for category in ['street','summer','suit']:
            for index in range(20):
                items.append({'id':f'{visual}-{category}-{index}','kind':'costume','visualStyle':visual,
                              'category':category,'categoryLabel':category,'source':'synthetic',
                              'title':'外套','englishTitle':'Coat','code':str(index),'keywords':'','tags':[]})
    monkeypatch.setattr(refs,'catalog',lambda:items)
    params={'kind':'costume','visualStyle':style,'categories':'street,summer','q':'Coat','random':'1'}
    first=refs.listing(params);second=refs.listing({**params,'page':'2'})
    assert first['total']==second['total']==40
    assert len(first['items'])==24 and len(second['items'])==16
    assert {item['id'] for item in first['items']}.isdisjoint(item['id'] for item in second['items'])
    for item in [*first['items'],*second['items'],first['randomItem'],second['randomItem']]:
        assert item['visualStyle']==style and item['category'] in {'street','summer'}
    detail=refs.listing({**params,'category':'summer'})
    assert detail['total']==20 and detail['randomItem']['category']=='summer'
    outside=refs.listing({**params,'category':'suit'})
    assert outside['items']==[] and outside['randomItem'] is None
    with pytest.raises(ValueError):refs.listing({**params,'categories':','.join(str(i) for i in range(33))})
