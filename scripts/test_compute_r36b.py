"""Exact source overlay and scoped build-tool regressions; all offline."""
import difflib,json,subprocess
from pathlib import Path
import pytest
from scripts import colab_session_dependencies as dep
from scripts.colab_session_diagnostics import failure_diagnostics,sanitize_diagnostics

@pytest.fixture
def overlay(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir();registry=tmp_path/'registry';registry.mkdir()
    old=b'first\nold\nlast\n';new=b'first\nnew\nlast\n'
    (root/'file.py').write_bytes(old)
    patch=''.join(difflib.unified_diff(old.decode().splitlines(True),new.decode().splitlines(True),fromfile='a/file.py',tofile='b/file.py')).encode()
    (registry/'fix.patch').write_bytes(patch)
    row=dict(repository='public-repo',commit='a'*40,path='file.py',upstream_sha256=dep.digest(old),pinned_sha256=dep.digest(new),patch='fix.patch',patch_sha256=dep.digest(patch))
    (registry/'registry.json').write_text(json.dumps([row]))
    monkeypatch.setattr(dep.subprocess,'check_output',lambda *a,**kw:'a'*40+'\n')
    return root,registry,row,old,new

def apply(f,**changes):
    root,registry,row,*_=f
    args={k:row[k] for k in ('repository','commit')};args.update(root=root,relative=row['path'],pinned=row['pinned_sha256'],registry=registry);args.update(changes)
    return dep.apply_overlay(**args)

def test_overlay_apply_verify_exact_bytes(overlay):
    assert apply(overlay)
    assert (overlay[0]/'file.py').read_bytes()==overlay[4]

@pytest.mark.parametrize('change',['upstream','result','repository','commit','path','head','patch','symlink'])
def test_overlay_rejects_wrong_identity_or_bytes(overlay,change,monkeypatch):
    root,registry,row,old,new=overlay;changes={}
    if change=='upstream':(root/'file.py').write_text('unknown source\n')
    if change=='result':
        row['pinned_sha256']='0'*64;(registry/'registry.json').write_text(json.dumps([row]))
    if change in {'repository','commit'}:changes[change]='different'
    if change=='path':changes['relative']='other.py'
    if change=='head':monkeypatch.setattr(dep.subprocess,'check_output',lambda *a,**kw:'b'*40)
    if change=='patch':(registry/'fix.patch').write_text('changed patch')
    if change=='symlink':
        (root/'file.py').rename(root/'real.py');(root/'file.py').symlink_to('real.py')
    with pytest.raises(dep.OverlayMismatch) as caught:apply(overlay,**changes)
    diagnostic=failure_diagnostics(caught.value,'node_checkout')
    assert sanitize_diagnostics(diagnostic)['error_code']=='node_overlay_mismatch'

def test_matching_pack_has_no_overlay_or_subprocess(overlay,monkeypatch):
    root,registry,row,old,new=overlay;(root/'file.py').write_bytes(new)
    (registry/'registry.json').unlink()
    monkeypatch.setattr(dep.subprocess,'run',lambda *a,**kw:pytest.fail('No overlay for matching bytes'))
    assert apply(overlay) is False

def test_voice_build_constraint_is_scoped_and_pinned(tmp_path):
    from scripts import colab_session_voice as voice
    registry=json.loads(voice.REGISTRY.read_text())
    envs={x['provenance']['label']:x['build_constraint'] for x in registry['environments'].values()}
    assert envs=={'local voice venv':'cython==3.2.5\nnumpy==1.26.4\npackaging==26.3\nsetuptools==70.2.0\nwheel==0.48.0\n','local voxcpm venv':'setuptools==70.2.0\n'}
    assert all('--hash=sha256:' in x for x in registry['tool_requirements'])
    args=['unrelated','command'];calls=[]
    voice.run_command({},tmp_path,args,lambda *a,**k:calls.append(a),{})
    assert calls==[(args,)]

def test_voice_build_refuses_changed_tool_pins(tmp_path):
    from scripts import colab_session_voice as voice
    with pytest.raises(voice.VoiceEnvironmentMismatch):voice.entry({'feature':'voice-speak','runtime_lock':{'voice':['pip==different']}},'voice')

def test_registry_matches_frozen_disk_pin():
    row=json.loads((dep.REGISTRY/'registry.json').read_text())[0]
    assert row['commit']=='4ee72c065db22c9d96c2427954dc69e7b908444b'
    assert row['pinned_sha256']=='57a5ca6b4d3ea6c2306675235711ab82f7c3ceac6aab87441b7f9e357ccff425'
    assert row['upstream_sha256']=='ea3d02e0f41dc6b59685fcf6de9217d4b7729766e05e44200433c74e75466a6a'
    assert dep.digest((dep.REGISTRY/row['patch']).read_bytes())==row['patch_sha256']
    assert 'GPL-3.0' in (dep.REGISTRY/'NOTICE.md').read_text()
