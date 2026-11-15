import json,hashlib,subprocess
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.review_r36_support import rig,allocated,job
from scripts import colab_session_voice as voice
from scripts.colab_session_diagnostics import decode_tail,ERRORS,failure_diagnostics

@pytest.mark.parametrize('verified,phase,assigned',[(False,'queued','G4'),(True,'failed','G4'),(False,'failed',None),(False,'queued',None)])
def test_owner_gpu_projection_on_read(rig,verified,phase,assigned):
    s,b,clock,account,initial=rig;st=allocated(rig,'bootstrap')
    s.sessions.save(st['id'],assigned_gpu=assigned,gpu_name='NVIDIA RTX PRO 6000 Blackwell Server Edition' if verified else None)
    j=job(rig,st['id'],phase,requested_gpu='A100')
    result=s.snapshot('owner')['jobs'][0]
    assert result['assigned_gpu']==assigned and result['gpu']==assigned
    assert result['requested_gpu']=='A100' and bool(result['gpu_name'])==verified
    assert s.project_session_gpu('different-owner',j)['assigned_gpu'] is None
    assert s.job('owner',j['id'])['gpu']=='A100'  # Read projection never rewrites history.

@pytest.mark.parametrize('error',sorted(ERRORS))
def test_tail_exact_error_vocabulary(error):
    assert decode_tail(error+': [redacted]')==error+': [redacted]'
    assert decode_tail('FORGED'+error+': [redacted]')==''



def test_voice_registry_has_no_home_paths_and_all_three_routes():
    text=voice.REGISTRY.read_text();r=json.loads(text)
    assert '/home/' not in text
    assert {(x['feature'],x['lock']) for x in r['entries']}=={('voice-speak','voice'),('voxcpm-tts','voice'),('voxcpm-tts','voxcpm')}
    assert r['interpreter']['version_info']==[3,11,15]
    assert len(r['interpreter']['sha256'])==64
    assert all(x['install_mode']=='no-deps' for x in r['environments'].values())
    assert next(x for x in r['environments'].values() if x['provenance']['label']=='local voice venv')['last']==['onnxruntime']


@pytest.mark.parametrize('mutation',['none','version','binary','missing_dist','extra_dist','duplicate_dist','hash','extra_file','missing_file','symlink'])
def test_voice_selfcheck_exact_distribution_set_and_overlap(tmp_path,monkeypatch,mutation):
    file=tmp_path/'shared/x.py';file.parent.mkdir();file.write_bytes(b'known')
    binary=tmp_path/'python';binary.write_bytes(b'python')
    expected={'dists':{'one':'1'},'overlap_roots':['shared'],'overlap_files':{'shared/x.py':voice.sha(file)}}
    registry={'interpreter':{'version_info':[3,11,15],'version':'build','sha256':voice.sha(binary)}}
    distributions=[NS(metadata={'Name':'one'},version='1')]
    if mutation=='missing_dist':distributions=[]
    if mutation=='extra_dist':distributions.append(NS(metadata={'Name':'other'},version='1'))
    if mutation=='duplicate_dist':distributions*=2
    if mutation=='hash':file.write_bytes(b'changed')
    if mutation=='extra_file':(file.parent/'extra').touch()
    if mutation=='missing_file':file.unlink()
    if mutation=='symlink':file.rename(tmp_path/'outside');file.symlink_to(tmp_path/'outside')
    if mutation=='binary':binary.write_bytes(b'changed')
    monkeypatch.setattr(voice,'sys',NS(version_info=(3,11,14) if mutation=='version' else (3,11,15),version='build',executable=str(binary)))
    monkeypatch.setattr(voice,'sysconfig',NS(get_path=lambda _:str(tmp_path)))
    monkeypatch.setattr(voice,'importlib',NS(metadata=NS(distributions=lambda **kw:distributions)))
    if mutation=='none':voice.verify_environment(registry,expected)
    else:
        with pytest.raises(voice.VoiceEnvironmentMismatch) as e:voice.verify_environment(registry,expected)
        assert failure_diagnostics(e.value,'runtime_lock')['error_code']=='voice_env_mismatch'

def test_runtime_tool_install_has_hashes_and_never_user_uv_store(tmp_path,monkeypatch):
    root=tmp_path/'voice-tools';py=root/'python/build/bin/python3.11';calls=[]
    registry={'interpreter':{'build_label':'build','sha256':hashlib.sha256(b'python').hexdigest()},'tool_requirements':['uv==0.11.20 --hash=sha256:'+'a'*64]}
    def run(args,**kw):
        calls.append((args,kw))
        if 'venv' in args:
            p=Path(args[-1])/'bin/python';p.parent.mkdir(parents=True);p.touch()
        if '3.11.15' in args:py.parent.mkdir(parents=True);py.write_bytes(b'python')
    py.parent.mkdir(parents=True);py.write_bytes(b'python')
    registry['interpreters']={'comfy':{'version':'3.12.13'},'voice':{'version':'3.11.15'}}
    monkeypatch.setattr(voice,'provision_python',lambda *a:str(py))
    monkeypatch.setattr(voice,'verify_python',lambda *a:None)
    voice.tools(tmp_path,registry,run,{})
    assert any('--require-hashes' in args for args,kw in calls)
    assert not any('python' in args and 'install' in args for args,kw in calls)  # shared archive provisioner, no user uv installation
    for args,kw in calls:
        for key in ['UV_PYTHON_INSTALL_DIR','UV_CACHE_DIR','UV_TOOL_DIR','UV_PYTHON_BIN_DIR','PIP_CACHE_DIR']:
            assert Path(kw['env'][key]).is_relative_to(root)




def test_missing_voxcpm_wheel_fails_closed(tmp_path, monkeypatch):
    registry=json.loads(voice.REGISTRY.read_text())
    environment=next(v for v in registry['environments'].values() if v.get('package_wheel'))
    package=environment['package_wheel']
    assert not (voice.REGISTRY.parent / package['filename']).exists()
    pins=['unit-test==1']
    plan={'feature':'voxcpm-tts','runtime_lock':{'voxcpm':pins}}
    (tmp_path/'voxcpm-constraints.txt').write_text('\n'.join(pins)+'\n')
    expected={'last':[], 'build_constraint':'', 'package_wheel':package}
    monkeypatch.setattr(voice,'entry',lambda *args:(registry,expected,'\n'.join(pins)+'\n'))
    monkeypatch.setattr(voice,'tools',lambda *args:('test-python','test-tool',{}))
    monkeypatch.setattr(voice,'verify_python',lambda *args:None)
    calls=[]
    with pytest.raises(voice.VoiceEnvironmentMismatch,match='voice_env_mismatch'):
        voice.run_command(plan,tmp_path,[str(tmp_path/'voxcpm-venv/bin/python'),'-m','pip','install','-r',str(tmp_path/'voxcpm-constraints.txt')],lambda args,**kw:calls.append(args),{})
    assert not any('--no-index' in args for args in calls)
