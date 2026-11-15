"""Pinned native Comfy graph runner, invoked only by the existing Colab lease.

No downscale/retry fallback. A failed bootstrap or unknown /prompt is terminal
for this runtime; the parent watchdog and cleanup remain the authority.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import zipfile
try:
    from .native_contracts import canonical, compile_request, dependency_probe
    from .native_schema import validate_native_manifest
except ImportError:
    from native_contracts import canonical, compile_request, dependency_probe
    from native_schema import validate_native_manifest

PACKS={'ComfyUI-VideoHelperSuite':'https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git',
       'ComfyUI-KJNodes':'https://github.com/kijai/ComfyUI-KJNodes.git'}

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_bytes(canonical(value));tmp.replace(path)

def valid_file(path,item):
    return path.is_file() and not path.is_symlink() and path.stat().st_size==item['bytes'] and file_hash(path)==item['sha256']

def install_model(item,cache,fetch=None):
    target=cache/item['target'];target.parent.mkdir(parents=True,exist_ok=True)
    if valid_file(target,item):return target
    url=item.get('download_url') or item.get('url','').replace('/blob/','/resolve/')
    if not url.startswith(('https://huggingface.co/','https://storage.googleapis.com/esun-ai/','https://www.modelscope.cn/')):
        raise ValueError('Pinned public model source missing')
    part=target.with_suffix(target.suffix+'.part')
    try:
        if fetch:fetch(url,part)
        else:
            with urllib.request.urlopen(url,timeout=60) as response,part.open('wb') as out:
                size=0
                while block:=response.read(4*1024*1024):
                    size+=len(block)
                    if size>item['bytes']:raise ValueError('Model larger than manifest')
                    out.write(block)
        if not valid_file(part,item):raise ValueError('Model hash/size mismatch')
        part.replace(target)
    finally:part.unlink(missing_ok=True)
    return target

def assemble_private_model(root,cache,item):
    target=cache/item['target'];target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    parts=root/'private-models'/(item['target']+'.parts')
    chunk_bytes=16*1024*1024;count=(item['bytes']+chunk_bytes-1)//chunk_bytes
    part=target.with_suffix(target.suffix+'.assembling')
    try:
        with part.open('wb') as out:
            part.chmod(0o600)
            for index in range(count):
                source=parts/str(index)
                expected=min(chunk_bytes,item['bytes']-index*chunk_bytes)
                if source.is_symlink() or source.stat().st_size!=expected:raise ValueError('Private shard changed')
                with source.open('rb') as stream:shutil.copyfileobj(stream,out,1024*1024)
        if not valid_file(part,item):raise ValueError('Private upload integrity failure')
        part.replace(target)
    finally:part.unlink(missing_ok=True)
    return target

def git_checkout(url,commit,path,run):
    if not path.exists():run(['git','clone','--no-checkout',url,str(path)])
    run(['git','-C',str(path),'checkout','--detach',commit])
    actual=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    if actual!=commit:raise ValueError('Revision mismatch')

def bootstrap(bundle,cache,run=subprocess.run,stage=lambda value:None):
    try:from colab_session_voice import comfy_identity
    except ImportError:from scripts.colab_session_voice import comfy_identity
    stage('bootstrap')
    def checked(args):
        if Path(args[0])==cache/'venv/bin/python':comfy_identity(bundle,cache)
        if args[0]!='git':
            label='venv_create' if args[1:3]==['-m','venv'] else 'pip_core'
            if 'voice-venv' in str(args[0]):label='pip_voice'
            if 'voxcpm-venv' in str(args[0]):label='pip_voxcpm'
            if args[1:3]==['-I','-c']:label='import_ready'
            for pack in PACKS:
                if any('/custom_nodes/'+pack+'/requirements.txt' in str(a) for a in args):label='pip_node:'+pack
            stage(label)
        return run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    cache.mkdir(parents=True,exist_ok=True)
    needed=sum(m['bytes'] for m in bundle['models'])+15*1024**3
    existing=sum((cache/m['target']).stat().st_size for m in bundle['models'] if (cache/m['target']).is_file())
    if shutil.disk_usage(cache).free<needed-existing:raise RuntimeError('Session SSD capacity insufficient')
    core=cache/'ComfyUI';git_checkout(bundle['core']['repository'],bundle['core']['commit'],core,checked)
    for name,pack in bundle['node_packs'].items():
        if name=='castle_breezyvoice':continue
        path=core/'custom_nodes'/name;git_checkout(PACKS[name],pack['commit'],path,checked)
        for name,meta in pack['files'].items():
            if not valid_file(path/name,meta):raise ValueError('Node pack source differs from P0 pin')
    stage('bootstrap')
    env=cache/'venv'
    if not (env/'bin/python').exists():checked([sys.executable,'-m','venv',str(env)])
    python=str(env/'bin/python');lock=cache/'constraints.txt'
    lock.write_text('\n'.join(bundle['runtime_lock']['comfy'])+'\n')
    identity=comfy_identity(bundle,cache)
    if not (cache/'environment-ready.json').exists():
        checked([python,'-m','pip','install','--extra-index-url','https://download.pytorch.org/whl/cu128','-c',str(lock),'-r',str(core/'requirements.txt')])
        for name in bundle['node_packs']:
            requirements=core/'custom_nodes'/name/'requirements.txt'
            if requirements.exists():checked([python,'-m','pip','install','-c',str(lock),'-r',str(requirements)])
    if bundle['feature']=='voice-speak':
        voice=cache/'BreezyVoice';git_checkout(bundle['voice_runtime']['repository'],bundle['voice_runtime']['commit'],voice,checked)
        # Separate Python environment: local voice torch differs from Comfy torch.
        venv=cache/'voice-venv'
        if not (venv/'bin/python').exists():checked([sys.executable,'-m','venv',str(venv)])
        voicepython=str(venv/'bin/python');vlock=cache/'voice-constraints.txt';vlock.write_text('\n'.join(bundle['runtime_lock']['voice'])+'\n')
        if not (cache/'voice-ready.json').exists():
            checked([voicepython,'-m','pip','install','--extra-index-url','https://download.pytorch.org/whl/cu128','-r',str(vlock)])
            atomic(cache/'voice-ready.json',{'lock':bundle['runtime_lock']['voice']})
        node=core/'custom_nodes/castle_parity_voice';node.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path(__file__).with_name('native_voice_node.py'),node/'__init__.py')
        shutil.copyfile(Path(__file__).with_name('native_voice_worker.py'),node/'native_voice_worker.py')
    if bundle['feature']=='voxcpm-tts':
        for label,lockname in [('voxcpm','voxcpm'),('voice','voice')]:
            venv=cache/(label+'-venv')
            if not (venv/'bin/python').exists():checked([sys.executable,'-m','venv',str(venv)])
            lock=cache/(label+'-constraints.txt');lock.write_text('\n'.join(bundle['runtime_lock'][lockname])+'\n')
            if not (cache/(label+'-ready.json')).exists():
                checked([str(venv/'bin/python'),'-m','pip','install','--extra-index-url','https://download.pytorch.org/whl/cu128','-r',str(lock)])
                atomic(cache/(label+'-ready.json'),{'lock':bundle['runtime_lock'][lockname]})
        node=core/'custom_nodes/castle_parity_voice';node.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path(__file__).with_name('native_voice_node.py'),node/'__init__.py')
        shutil.copyfile(Path(__file__).with_name('native_voxcpm_worker.py'),node/'native_voxcpm_worker.py')
    checked([python,'-I','-c','import torch,numpy,scipy,torchvision,torchaudio'])
    # A ready record certifies both the immutable identity and a successful import.
    identity=comfy_identity(bundle,cache)
    atomic(cache/'environment-ready.json',identity)
    stage('model_download')
    g2pw=[m for m in bundle['models'] if m['target'].startswith('g2pw/')]
    if g2pw and any(not valid_file(cache/m['target'],m) for m in g2pw):
        archive=cache/'g2pw.zip'
        try:
            with urllib.request.urlopen('https://storage.googleapis.com/esun-ai/g2pW/G2PWModel-v2-onnx.zip',timeout=60) as response,archive.open('wb') as out:
                size=0
                while block:=response.read(4*1024*1024):
                    size+=len(block)
                    if size>1024**3:raise ValueError('G2PW archive too large')
                    out.write(block)
            with zipfile.ZipFile(archive) as z:
                for item in g2pw:
                    name='G2PWModel/'+item['target'].split('/',1)[1]
                    if z.getinfo(name).file_size!=item['bytes']:raise ValueError('G2PW size mismatch')
                    target=cache/item['target'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
                    if not valid_file(target,item):raise ValueError('G2PW content mismatch')
        finally:archive.unlink(missing_ok=True)
    for item in bundle['models']:
        if item.get('private'):
            if not valid_file(cache/item['target'],item):raise ValueError('Private weights missing or changed')
            continue
        if not item['target'].startswith('g2pw/'):install_model(item,cache)
        if item['target'].startswith('models/'):
            dest=core/item['target'];dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():os.link(cache/item['target'],dest)
            if not valid_file(dest,item):raise ValueError('Comfy model cache changed')
    return core,python

def local_request(method,path,body=None):
    request=urllib.request.Request('http://127.0.0.1:8189'+path,data=canonical(body) if body is not None else None,
        headers={'Content-Type':'application/json'},method=method)
    try:
        with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        try:body=json.loads(error.read(65537))
        except Exception:body={}
        diagnostic={}
        errors=body.get('node_errors',{}) if isinstance(body,dict) else {}
        if isinstance(errors,dict) and errors:
            nid,node=next(iter(errors.items()))
            if isinstance(node,dict):diagnostic={'node_id':nid,'node_class':node.get('class_type'),'error_code':'node_validation_failed'}
        error.native_diagnostics=diagnostic
        raise

def output_check(path,contract):
    metadata=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-show_streams','-show_format','-of','json',str(path)]))
    duration=float(metadata['format']['duration'])
    if contract['kind']=='video':
        video=next(s for s in metadata['streams'] if s['codec_type']=='video')
        if not contract['duration_min_seconds']<=duration<=contract['duration_max_seconds']:raise ValueError('Full duration contract failed')
        if int(video.get('nb_frames',0))!=contract['frames']:raise ValueError('Frame count contract failed')
        if contract['source_audio'] and not any(s['codec_type']=='audio' for s in metadata['streams']):raise ValueError('Source audio missing')
    elif duration<=0:raise ValueError('Empty audio')
    return metadata

def run_native(root,manifest_sha256):
    root=Path(root);manifest_path=root/'manifest.json'
    if file_hash(manifest_path)!=manifest_sha256:raise ValueError('Manifest digest mismatch')
    manifest=validate_native_manifest(json.loads(manifest_path.read_text()),root)
    bundle=manifest['jobs'][0]['bundle'];job=manifest['job_id'];deadline=manifest['deadline_epoch'];proc=None
    state={'job_id':job,'manifest_sha256':manifest_sha256,'phase':'running'}
    def save(phase,**extra):atomic(root/'status.json',{**state,'phase':phase,**extra})
    current={'stage':'bootstrap'}
    def stage(value):
        current.clear();current.update(stage=value)
        save('running',diagnostics=dict(current))
    def guard():
        if time.time()>=deadline or (root/'cancel').exists():raise RuntimeError('Cancelled or deadline exceeded')
    # Prevent two processes in one lease, even after a lost launch response.
    lock=(root/'native.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return 0
    if (root/'native-submission.json').exists():
        save('failed',done=True,message='Submission already attempted; never resubmit');return 1
    save('running')
    try:
        guard()
        gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True).strip()
        if 'A100' not in gpu or int(gpu.rsplit(',',1)[1])<39000:raise RuntimeError('P1 requires A100 >=40 GB')
        cache=Path('/content/castle-parity-cache')/bundle['bundle_sha256']
        cache.mkdir(parents=True,exist_ok=True,mode=0o700)
        for item in bundle['models']:
            if item.get('private'):
                assemble_private_model(root,cache,item)
        core,python=bootstrap(bundle,cache,stage=stage);guard()
        if bundle.get('voice_export'):
            atomic(cache/'voice-export.json',bundle['voice_export'])
            (cache/'voice-export.json').chmod(0o600)
        for item in bundle['inputs']:
            dest=core/'input'/item['name'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/item['name'],dest)
            if item['kind']=='video':
                alias=Path('/content/castle-parity-input')/item['name'];alias.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,alias)
        env={**os.environ,'CASTLE_PARITY_CACHE':str(cache),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
        stage('node_start')
        proc=subprocess.Popen([python,str(core/'main.py'),'--listen','127.0.0.1','--port','8189','--disable-auto-launch'],cwd=core,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        info=None
        for _ in range(180):
            guard()
            if proc.poll() is not None:raise RuntimeError('Native Comfy startup failed')
            try:info=local_request('GET','/object_info');break
            except Exception:time.sleep(1)
        if info is None:raise RuntimeError('Native Comfy startup timed out')
        stage('prompt_validation')
        plan=compile_request(bundle,'colab',request_id=job,voice_consent=manifest['jobs'][0].get('voice_consent',False))
        probe=dependency_probe(plan['json']['prompt'],info)
        if not probe['ready']:raise RuntimeError('Native dependency gap')
        atomic(root/'native-submission.json',{'attempted':True,'request_id':job})
        response=local_request('POST','/prompt',plan['json'])
        if response.get('node_errors'):
            nid,n=next(iter(response['node_errors'].items()))
            current.update(node_id=nid,node_class=n.get('class_type'),error_code='node_validation_failed')
        rid=response['prompt_id']
        atomic(root/'native-submission.json',{'attempted':True,'request_id':job,'prompt_id':rid})
        stage('execute')
        entry=None;peak=0
        while True:
            guard()
            if proc.poll() is not None:raise RuntimeError('Native Comfy exited')
            entry=local_request('GET','/history/'+rid).get(rid)
            if entry and entry.get('status',{}).get('status_str')=='error':
                for message in entry.get('status',{}).get('messages',[]):
                    if isinstance(message,list) and len(message)==2 and message[0]=='execution_error' and isinstance(message[1],dict):
                        current.update(node_id=message[1].get('node_id'),node_class=message[1].get('node_type'),error_code='node_execution_failed')
                raise RuntimeError('Native graph failed; no fallback')
            if entry and entry.get('status',{}).get('completed'):break
            try:peak=max(peak,int(subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()[0]))
            except (ValueError,subprocess.SubprocessError):pass
            time.sleep(1)
        artifacts=[]
        for value in entry.get('outputs',{}).values():
            for field in ('gifs','videos','audio','images'):
                for item in value.get(field,[]):
                    if item.get('type')!='output':continue
                    src=(core/'output'/item.get('subfolder','')/item['filename']).resolve()
                    if not src.is_relative_to((core/'output').resolve()):raise ValueError('Output path escaped')
                    suffix=src.suffix.lower()
                    if suffix not in ({'.mp4','.webm'} if bundle['output_contract']['kind']=='video' else {'.flac','.wav','.mp3'}):continue
                    stage('ffprobe')
                    output_check(src,bundle['output_contract'])
                    target=root/'out'/f'{len(artifacts)+1}{suffix}';target.parent.mkdir(exist_ok=True);shutil.copyfile(src,target)
                    artifacts.append({'path':target.relative_to(root).as_posix(),'size':target.stat().st_size,'sha256':file_hash(target)})
        if not artifacts:raise ValueError('No matching artifacts')
        atomic(root/'artifacts.json',{'schema_version':1,'job_id':job,'manifest_sha256':manifest_sha256,'artifacts':artifacts})
        atomic(root/'native-telemetry.json',{'gpu':gpu,'peak_vram_mib_sampled':peak,'bundle':bundle['bundle_sha256'],'finished':time.time()})
        save('completed',done=True);return 0
    except Exception as error:
        try:from .remote_job import failure_diagnostics, sanitize_diagnostics
        except ImportError:from remote_job import failure_diagnostics, sanitize_diagnostics
        diagnostic={**failure_diagnostics(error,current['stage']),**current,**getattr(error,'native_diagnostics',{})}
        if proc is not None and current['stage'] in {'node_start','execute'} and proc.poll() is not None:diagnostic['exit_code']=proc.poll()
        diagnostic.setdefault('error_code','output_contract_failed' if current['stage']=='ffprobe' else 'stage_failed')
        save('failed',done=True,diagnostics=sanitize_diagnostics(diagnostic),message='原生圖執行失敗；請查看階段診斷');return 1
    finally:
        if proc is not None:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        # Source voice/audio and transcripts are job data, never shared cache.
        if 'core' in locals():shutil.rmtree(core/'input',ignore_errors=True)
        if 'cache' in locals():
            (cache/'voice-export.json').unlink(missing_ok=True)
            for item in bundle['models']:
                if item.get('private'):(cache/item['target']).unlink(missing_ok=True)
        shutil.rmtree('/content/castle-parity-input',ignore_errors=True)
        lock.close()
