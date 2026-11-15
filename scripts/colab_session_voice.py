"""Exact observed voice environments, isolated from the Comfy interpreter.

No dependency resolution, changed runtime pins, user uv stores or model reads.
"""
import hashlib
import importlib.metadata
import importlib.util
import platform
import runpy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
import shutil
import tarfile
import urllib.request
import uuid

REGISTRY=Path(__file__).with_name('colab_session_overlays')/'voice-environments.json'

class VoiceEnvironmentMismatch(ValueError):
    error_code='voice_env_mismatch'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
    return h.hexdigest()

def canonical_name(name):return re.sub(r'[-_.]+','-',name).lower()

def entry(plan,label,registry=None):
    registry=registry or json.loads(REGISTRY.read_text())
    pins=plan.get('runtime_lock',{}).get(label,[])
    text='\n'.join(pins)+'\n';checksum=hashlib.sha256(text.encode()).hexdigest()
    key={'feature':plan.get('feature'),'lock':label,'sha256':checksum}
    if key not in registry['entries']:raise VoiceEnvironmentMismatch('voice_env_mismatch')
    return registry,registry['environments'][checksum],text

def ignored(path):
    return path.name in {'RECORD','INSTALLER','REQUESTED','direct_url.json'} or '__pycache__' in path.parts or path.suffix=='.pyc'

def verify_environment(registry,expected):
    pin=registry['interpreter']
    if list(sys.version_info[:3])!=pin['version_info'] or sys.version!=pin['version'] or sha(sys.executable)!=pin['sha256']:
        raise VoiceEnvironmentMismatch('voice_env_mismatch')
    site=Path(sysconfig.get_path('purelib'));found={}
    for dist in importlib.metadata.distributions(path=[str(site)]):
        name=canonical_name(dist.metadata['Name'])
        if name in found:raise VoiceEnvironmentMismatch('voice_env_mismatch')
        found[name]=dist.version
    if found!=expected['dists']:raise VoiceEnvironmentMismatch('voice_env_mismatch')
    files={}
    for root in expected['overlap_roots']:
        for path in (site/root).rglob('*'):
            relative=path.relative_to(site)
            if ignored(relative):continue
            if path.is_symlink():raise VoiceEnvironmentMismatch('voice_env_mismatch')
            if path.is_file():files[relative.as_posix()]=sha(path)
    if files!=expected['overlap_files']:raise VoiceEnvironmentMismatch('voice_env_mismatch')

def import_smoke(expected,cache,label,feature):
    """Real job-time imports only, no model construction or inference."""
    substage='voxcpm_imports' if label=='voxcpm' else 'voice_imports' if feature=='voice-speak' else 'speaker_imports'
    import socket
    connect,connection=socket.socket.connect,socket.create_connection
    def denied(*args,**kw):raise OSError('voice_import_network_forbidden')
    socket.socket.connect=socket.create_connection=denied
    observed={'python_build':platform.python_build()[0]+' '+platform.python_build()[1],
              'python_version':platform.python_version()}
    try:
        os.environ.update(CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',MODELSCOPE_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1')
        import torch
        observed['torch_version']=str(torch.__version__)
        providers=[]
        if importlib.util.find_spec('onnxruntime'):
            import onnxruntime
            providers=onnxruntime.get_available_providers()
        observed['onnx_providers']=providers
        import pkg_resources
        # Exercise metadata access used by transitive job-time imports, including
        # editable VoxCPM's version lookup which the stdlib-only check missed.
        for name,version in expected['dists'].items():
            if importlib.metadata.version(name)!=version:raise VoiceEnvironmentMismatch()
        if label=='voxcpm':
            from voxcpm import VoxCPM
            runpy.run_path(str(REGISTRY.parent/'voxcpm_tts.py'),run_name='import_probe')
        elif feature=='voice-speak':
            voice=Path(cache)/'BreezyVoice'
            sys.path[:0]=[str(voice),str(voice/'third_party/Matcha-TTS')]
            os.chdir(voice)
            import wetext.wetext
            from g2pw import G2PWConverter
            from single_inference import CustomCosyVoice,single_inference
        else:
            runpy.run_path(str(REGISTRY.parent/'speaker_similarity.py'),run_name='import_probe')
        return {**observed,'imports_ready':True}
    except Exception:
        failure=VoiceEnvironmentMismatch('voice_env_mismatch');failure.substage=substage
        failure.telemetry=observed
        raise failure from None
    finally:
        socket.socket.connect,socket.create_connection=connect,connection

class PythonMismatch(ValueError):
    error_code='python_mismatch'

def verify_python(python,version,run=subprocess.run):
    result=run([str(python),'-I','-c','import sys;print(".".join(map(str,sys.version_info[:3])))'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if result.stdout.strip()!=version:raise PythonMismatch('python_mismatch')

def _provision_python(cache,pin,run=subprocess.run):
    # A single provisioner serves both source-Python registry entries.
    root=Path(cache)/'python-builds';root.mkdir(exist_ok=True)
    target=root/pin['sha256'];python=target/pin['executable']
    if not target.exists() and not target.is_symlink():
        pending=root/('pending-'+uuid.uuid4().hex);pending.mkdir(mode=0o700)
        archive=pending/'python.tar.gz'
        try:
            h=hashlib.sha256();size=0
            with urllib.request.urlopen(pin['url'],timeout=60) as response,archive.open('wb') as output:
                while block:=response.read(1024**2):
                    size+=len(block)
                    if size>pin['bytes']:raise PythonMismatch('python_mismatch')
                    h.update(block);output.write(block)
            if size!=pin['bytes'] or h.hexdigest()!=pin['sha256']:raise PythonMismatch('python_mismatch')
            with tarfile.open(archive,'r:gz') as source:source.extractall(pending,filter='data')
            archive.unlink()
            if sha(pending/pin['executable'])!=pin['executable_sha256']:raise PythonMismatch('python_mismatch')
            verify_python(pending/pin['executable'],pin['version'],run)
            pending.rename(target)
        finally:
            if pending.exists():shutil.rmtree(pending)
    if sha(python)!=pin['executable_sha256']:raise PythonMismatch('python_mismatch')
    verify_python(python,pin['version'],run)
    return str(python)

def provision_python(cache,pin,run=subprocess.run):
    try:return _provision_python(cache,pin,run)
    except PythonMismatch:raise
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        raise PythonMismatch('python_mismatch') from None

def comfy_identity(plan,cache,run=None,require_ready=False):
    """Verify bytes before executing even a version probe; never repair a cache."""
    run=run or subprocess.run
    try:
        from colab_session_diagnostics import plan_id, plan as public_plan
    except ImportError:
        from scripts.colab_session_diagnostics import plan_id, plan as public_plan
    try:
        registry=json.loads(REGISTRY.read_text());comfy_entry(plan,registry)
        pin=registry['interpreters']['comfy'];cache=Path(cache)
        base=cache/'python-builds'/pin['sha256']/pin['executable']
        python=cache/'venv/bin/python'
        if base.resolve(strict=True)!=cache.resolve()/'python-builds'/pin['sha256']/pin['executable']:raise PythonMismatch('python_mismatch')
        # Venv copies and symlinks are allowed only with the exact pinned bytes.
        if sha(base.resolve(strict=True))!=pin['executable_sha256'] or sha(python.resolve(strict=True))!=pin['executable_sha256']:
            raise PythonMismatch('python_mismatch')
        cfg={}
        for line in (cache/'venv/pyvenv.cfg').read_text().splitlines():
            if '=' in line:
                key,value=line.split('=',1)
                if key.strip() in cfg:raise PythonMismatch('python_mismatch')
                cfg[key.strip()]=value.strip()
        if Path(cfg['home']).resolve(strict=True)!=base.parent.resolve(strict=True):raise PythonMismatch('python_mismatch')
        verify_python(base,pin['version'],run)
        verify_python(python,pin['version'],run)
        identity={'executable_sha256':pin['executable_sha256'],'python_version':pin['version'],
                  'plan_id':plan_id(public_plan(plan)),'lock_sha256':hashlib.sha256(('\n'.join(plan['runtime_lock']['comfy'])+'\n').encode()).hexdigest()}
        marker=cache/'environment-ready.json'
        if require_ready or marker.exists():
            if json.loads(marker.read_text())!=identity:raise PythonMismatch('python_mismatch')
        return identity
    except PythonMismatch:raise
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        raise PythonMismatch('python_mismatch') from None


def comfy_entry(plan,registry):
    lock=hashlib.sha256(('\n'.join(plan['runtime_lock']['comfy'])+'\n').encode()).hexdigest()
    row=registry['comfy_locks'].get(lock)
    if row is None or row['python']!=registry['interpreters']['comfy']['version']:raise PythonMismatch('python_mismatch')
    return row


def tools(cache,registry,run,kw):
    root=Path(cache)/'voice-tools';root.mkdir(exist_ok=True)
    env={**os.environ,**kw.get('env',{}),'UV_PYTHON_INSTALL_DIR':str(root/'python'),
         'UV_CACHE_DIR':str(root/'uv-cache'),'UV_TOOL_DIR':str(root/'uv-tools'),
         'UV_PYTHON_BIN_DIR':str(root/'bin'),'PIP_CACHE_DIR':str(root/'pip-cache'),
         'PIP_CONFIG_FILE':'/dev/null','PIP_INDEX_URL':'https://pypi.org/simple','PIP_NO_INPUT':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1','UV_NO_CONFIG':'1','PYTHONDONTWRITEBYTECODE':'1',
         'CC':'gcc -pthread','CXX':'g++ -pthread','AR':'ar'}
    options={**kw,'env':env,'check':True}
    tool=root/'venv';python=tool/'bin/python';uv=tool/'bin/uv'
    source=provision_python(cache,registry['interpreters']['comfy'],run)
    if not python.exists():run([source,'-m','venv',str(tool)],**options)
    verify_python(python,registry['interpreters']['comfy']['version'],run)
    requirement=root/'requirements.txt';requirement.write_text('\n'.join(registry['tool_requirements'])+'\n')
    if not (root/'tools-ready').exists():
        run([str(python),'-m','pip','--isolated','install','--disable-pip-version-check','--index-url','https://pypi.org/simple','--cache-dir',str(root/'pip-cache'),'--only-binary=:all:','--no-deps','--require-hashes','-r',str(requirement)],**options)
        (root/'tools-ready').touch()
    selected=provision_python(cache,registry['interpreters']['voice'],run)
    if sha(selected)!=registry['interpreter']['sha256']:raise VoiceEnvironmentMismatch('voice_env_mismatch')
    return selected,str(python),options

def run_command(plan,cache,args,run,kw):
    cache=Path(cache);label=None;creating=False
    registry=json.loads(REGISTRY.read_text())
    if (len(args)==4 and args[1:3]==['-m','venv'] and Path(args[3])==cache/'venv'):
        comfy_entry(plan,registry)
        if (cache/'venv').exists() or (cache/'venv').is_symlink():raise PythonMismatch('python_mismatch')
        source=provision_python(cache,registry['interpreters']['comfy'],run)
        result=run([source,*args[1:]],**kw)
        comfy_identity(plan,cache,run)
        return result
    if args and Path(args[0])==cache/'venv/bin/python':
        comfy_identity(plan,cache,run)
    if len(args)>=4 and Path(args[0])==cache/'venv/bin/python' and args[1:4]==['-m','pip','install']:
        row=comfy_entry(plan,registry)
        if row['sdist_allowlist']:raise PythonMismatch('python_mismatch')
        return run([*args,'--only-binary=:all:'],**kw)
    if len(args)==4 and args[1:3]==['-m','venv']:
        for item in ('voice','voxcpm'):
            if Path(args[3])==cache/(item+'-venv'):label=item;creating=True
    elif len(args)>=6 and args[1:4]==['-m','pip','install']:
        for item in ('voice','voxcpm'):
            if Path(args[0])==cache/(item+'-venv/bin/python') and args[-2:]==['-r',str(cache/(item+'-constraints.txt'))]:label=item
    if label is None:return run(args,**kw)
    try:
        registry,expected,text=entry(plan,label)
        interpreter,toolpython,options=tools(cache,registry,run,kw)
        target=cache/(label+'-venv');python=target/'bin/python'
        if creating:return run([interpreter,'-m','venv','--without-pip',str(target)],**options)
        verify_python(python,'.'.join(map(str,registry['interpreter']['version_info'])),run)
        lock=cache/(label+'-constraints.txt')
        if lock.read_text()!=text:raise VoiceEnvironmentMismatch('voice_env_mismatch')
        pins={canonical_name(p.split('==')[0]):p for p in text.splitlines()}
        first=cache/(label+'-install-first.txt');first.write_text('\n'.join(pins[n] for n in sorted(pins) if n not in expected['last'])+'\n')
        build=cache/(label+'-build-constraints.txt');build.write_text(expected['build_constraint'])
        def install(args,**options):
            verify_python(toolpython,registry['interpreters']['comfy']['version'],run)
            verify_python(python,'.'.join(map(str,registry['interpreter']['version_info'])),run)
            return run(args,**options)
        base=[toolpython,'-m','pip','--python',str(python),'install','--no-deps','--extra-index-url','https://download.pytorch.org/whl/cu128','--build-constraint',str(build)]
        env=options.get('env')
        search=(env if env is not None else os.environ).get('PATH',os.defpath)
        missing=[tool for tool in ('gcc','g++','ar') if shutil.which(tool,path=search) is None]
        if missing:
            failure=VoiceEnvironmentMismatch('voice_env_mismatch');failure.substage='build_toolchain';failure.command_label='pip_'+label;raise failure
        install([*base,'-r',str(first)],**options)
        if expected['last']:
            install([*base,'--force-reinstall',*[pins[n] for n in expected['last']]],**options)
        if expected.get('extra_wheels'):
            req=cache/(label+'-tools-pinned.txt')
            req.write_text(''.join(n+'=='+v['version']+' --hash=sha256:'+v['sha256']+'\n' for n,v in sorted(expected['extra_wheels'].items())))
            install([toolpython,'-m','pip','--isolated','--python',str(python),'install','--no-deps','--only-binary=:all:','--require-hashes','-r',str(req)],**options)
        if expected.get('package_wheel'):
            wheel=expected['package_wheel'];path=REGISTRY.parent/wheel['filename']
            if path.is_symlink() or sha(path)!=wheel['sha256']:raise VoiceEnvironmentMismatch('voice_env_mismatch')
            req=cache/(label+'-package-pinned.txt');req.write_text(str(path)+' --hash=sha256:'+wheel['sha256']+'\n')
            install([toolpython,'-m','pip','--isolated','--python',str(python),'install','--no-deps','--no-index','--require-hashes','-r',str(req)],**options)
        # Native runner writes ready only after this hook returns successfully.
        check=cache/(label+'-expected.json');check.write_text(json.dumps({'interpreter':registry['interpreter'],'expected':expected,'cache':str(cache),'feature':plan['feature'],'label':label}))
        try:return run([str(python),'-I','-B',str(Path(__file__).resolve()),str(check)],**options)
        except subprocess.CalledProcessError as error:
            failure=VoiceEnvironmentMismatch('voice_env_mismatch');failure.returncode=error.returncode
            result=json.loads((cache/(label+'-import-check.json')).read_text()) if (cache/(label+'-import-check.json')).exists() else {}
            failure.substage=result.get('substage','voice_environment')
            raise failure from None
    except (VoiceEnvironmentMismatch,PythonMismatch):raise
    except (OSError,KeyError,ValueError,subprocess.SubprocessError) as exc:
        failure=VoiceEnvironmentMismatch('voice_env_mismatch')
        for key in ('command_label','failing_distribution','private_bootstrap_stderr'):
            if hasattr(exc,key):setattr(failure,key,getattr(exc,key))
        if isinstance(getattr(exc,'returncode',None),int):failure.returncode=exc.returncode
        raise failure from None

if __name__=='__main__':
    data=json.loads(Path(sys.argv[1]).read_text());target=Path(data['cache'])/(data['label']+'-import-check.json')
    try:
        verify_environment(data,data['expected'])
        result=import_smoke(data['expected'],data['cache'],data['label'],data['feature'])
    except Exception as error:
        target.write_text(json.dumps({**getattr(error,'telemetry',{}),'ready':False,'substage':getattr(error,'substage','voice_environment'),'error_code':'voice_env_mismatch'}));raise SystemExit(1)
    target.write_text(json.dumps(result))
