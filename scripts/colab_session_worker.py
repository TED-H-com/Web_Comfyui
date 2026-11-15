"""Session entry point, reusing the pinned native schema/compiler/bootstrap.

Execution body is derived from native_runner.run_native: only GPU admission,
shared prepared environment, process lock and per-job directories differ.
No generation retry, graph rewrite or model substitution.
"""
import sys
import signal
import threading
import tempfile
import re
from pathlib import Path
from types import MappingProxyType
sys.path.insert(0,str(Path(__file__).resolve().parent/'runner'))
from native_runner import *
try:from colab_session_inputs import validate_native_manifest, bindings
except ImportError:from scripts.colab_session_inputs import validate_native_manifest, bindings
try:from colab_session_diagnostics import failure_diagnostics, sanitize_diagnostics, atomic_status, plan_id, COMMANDS, command_label, stderr_excerpt, failing_distribution, system_python
except ImportError:from scripts.colab_session_diagnostics import failure_diagnostics, sanitize_diagnostics, atomic_status, plan_id, COMMANDS, command_label, stderr_excerpt, failing_distribution, system_python


session_failure_diagnostics=failure_diagnostics
session_sanitize_diagnostics=sanitize_diagnostics
try:from colab_session_probe import prepare_supplement, node_gate, verify_ready, private_diagnostics, private_log, log_tail, comfy_args, bound_log, compact_log, note_log
except ImportError:from scripts.colab_session_probe import prepare_supplement, node_gate, verify_ready, private_diagnostics, private_log, log_tail, comfy_args, bound_log, compact_log, note_log

try:from colab_session_dependencies import after_checkout, verify_cached_overlays
except ImportError:from scripts.colab_session_dependencies import after_checkout, verify_cached_overlays

try:from colab_session_voice import run_command, comfy_identity
except ImportError:from scripts.colab_session_voice import run_command, comfy_identity


def verify_gpu(raw):
    name, memory = raw.rsplit(',',1)
    name=name.strip()
    if any(x in name.upper().split() for x in ('L4','T4')) or int(memory)<39000:
        raise RuntimeError('Native parity requires >= A100-class VRAM; no shrink')
    if not ('A100' in name or 'H100' in name or 'RTX PRO 6000' in name.upper() and 'BLACKWELL' in name.upper()):
        raise RuntimeError('Unexpected native GPU family')
    if len(name)>128 or any(ord(c)<32 for c in name):
        raise ValueError('Invalid GPU identity')
    return name,int(memory)


def bootstrap_session(session,selected=None):
    session=Path(session);status_root=session/'plans'/selected if selected else session
    status_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    progress={'step':0,'diagnostics':{'stage':'session_bootstrap','substage':'lock'}}
    done=threading.Event();reporter=None;lock=None
    process_identity=MappingProxyType({})
    def publish(phase='running'):
        atomic_status(status_root/'bootstrap-status.json',{'phase':phase,'progress_at':time.time(),**process_identity,**progress})
    def stage(value):
        progress['diagnostics']={'stage':'session_bootstrap','substage':value}
        history=progress.setdefault('bootstrap_stages',[])
        if not history or history[-1]!=value:history.append(value)
        progress['bootstrap_stages']=history[-64:]
        publish()
    try:
        process_identity=MappingProxyType({'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split(') ',1)[1].split()[19]})
        lock=(session/'bootstrap.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Bootstrap lock busy')
        if (status_root/'bootstrap-attempted').exists():
            cached_plans=json.loads((session/'bootstrap.json').read_text())
            if selected:cached_plans=[p for p in cached_plans if plan_id(p)==selected]
            verify_cached_overlays(cached_plans,status_root/'cache')
            for plan in cached_plans:
                comfy_identity(plan,status_root/'cache',require_ready=True)
                verify_ready(plan,status_root/'cache',session)
            return 0
        publish()
        (status_root/'bootstrap-attempted').touch(mode=0o600)
        atomic_status(status_root/'bootstrap-process.json',dict(process_identity))
        def heartbeat():
            while not done.is_set():publish();done.wait(15)
        reporter=threading.Thread(target=heartbeat,daemon=True);reporter.start()
        stage('disk_check')
        progress['system_python']=system_python()
        progress.update(disk_free_gib=round(shutil.disk_usage(session).free/1024**3,2),python_version='.'.join(map(str,sys.version_info[:3])))
        stage('gpu_verify')
        raw=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True).strip()
        name,memory=verify_gpu(raw)
        progress.update(gpu_name=name,vram_mib=memory,gpu='H100' if 'H100' in name else 'A100' if 'A100' in name else 'G4');publish()
        stage('plan_validation')
        plans=json.loads((session/'bootstrap.json').read_text())
        if selected:
            plans=[p for p in plans if plan_id(p)==selected]
            if not plans:raise ValueError('Unknown public bootstrap plan')
        cache=status_root/'cache';cache.mkdir(exist_ok=True,mode=0o700)
        models={};seen=set()
        for plan in plans:
            for model in plan['models']:
                previous=models.setdefault(model['target'],model['sha256'])
                if previous!=model['sha256']:raise ValueError('Public model cache conflict')
            plan_digest=plan_id(plan)
            if plan_digest in seen:continue
            seen.add(plan_digest)
            # Separate plan environments; immutable public weights share inodes.
            pool=session/'public-models'
            for model in plan['models']:
                source=pool/model['sha256'];target=cache/model['target']
                if valid_file(source,model) and not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True);os.link(source,target)
            phase={'downloaded':False}
            def native_stage(value):
                if value=='model_download':
                    prepare_supplement(plan,cache,run,stage)
                    stage('import_ready')
                    node_gate(plan,cache,session,stop_group)
                    phase['downloaded']=True;stage('model_download')
                elif value in COMMANDS:stage(value)
                else:stage('runtime_lock' if phase['downloaded'] else 'disk_check')
            def run(args,**kw):
                if args[0]=='git':stage('node_checkout' if 'custom_nodes' in str(args) else 'core_checkout')
                elif command_label(args):stage('python_provision' if command_label(args)=='venv_create' else command_label(args))
                def execute(argv,**options):
                    label=command_label(argv)
                    if label:stage(label)
                    # Version probes need their small stdout result; command stderr
                    # goes to a private temporary file, never the browser/log stream.
                    with tempfile.TemporaryFile(mode='w+b') as err:
                        options['stderr']=err
                        try:
                            result=subprocess.run(argv,**options)
                            if Path(argv[0])==cache/'venv/bin/python' and argv[1:3]==['-I','-c'] and 'sys.version_info' in argv[3]:
                                version=result.stdout.strip()
                                if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):
                                    progress['venv_python_version']=version;publish()
                            return result
                        except Exception as error:
                            err.seek(0,2);err.seek(max(0,err.tell()-65536));raw=err.read().decode('utf-8','replace')
                            error.command_label=label or progress['diagnostics'].get('substage','python_provision')
                            error.private_bootstrap_stderr=stderr_excerpt(raw)
                            token=failing_distribution(raw)
                            if token:error.failing_distribution=token
                            raise
                try:result=run_command(plan,cache,args,execute,kw)
                finally:
                    for label in ('voice','voxcpm'):
                        check=cache/(label+'-import-check.json')
                        if check.exists():
                            from colab_session_diagnostics import telemetry
                            safe=telemetry({'voice_environments':{label:json.loads(check.read_text())}})
                            progress.setdefault('voice_environments',{}).update(safe.get('voice_environments',{}));publish()
                if len(args)>3 and Path(args[0])==cache/'venv/bin/python' and args[1:4]==['-m','pip','install']:
                    progress['venv_python_version']='3.12.13';publish()
                after_checkout(plan,cache,args)
                return result
            bootstrap(plan,cache,run=run,stage=native_stage)
            progress['venv_python_version']='3.12.13';publish()
            for model in plan['models']:
                source=cache/model['target'];target=pool/model['sha256']
                if valid_file(source,model) and not target.exists():
                    pool.mkdir(exist_ok=True,mode=0o700);os.link(source,target)
            progress['step']+=1
        atomic_status(status_root/'prepared.json',{'plans':plans,'models':models})
        stage('complete');done.set()
        if reporter:reporter.join()
        publish('ready');return 0
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        done.set()
        if reporter:reporter.join()
        progress['diagnostics']=failure_diagnostics(error,progress['diagnostics'].get('substage','lock'))
        progress['private_bootstrap_stderr']=stderr_excerpt(getattr(error,'private_bootstrap_stderr',''))
        progress['private_dependency_diagnostics']=private_diagnostics(getattr(error,'private_dependency_diagnostics',{}))
        publish('failed');return 1
    finally:
        done.set()
        if lock:lock.close()


def prepared(bundle,session):
    expected={k:bundle[k] for k in ('core','node_packs','runtime_lock','feature','voice_runtime') if k in bundle}
    expected['models']=[m for m in bundle['models'] if not m.get('private')]
    location=session/'plans'/plan_id(expected)
    if not location.exists():location=session
    data=json.loads((location/'prepared.json').read_text())
    if expected not in data['plans']:raise ValueError('Bundle differs from prepared session')
    cache=location/'cache'
    comfy_identity(expected,cache,require_ready=True)
    verify_ready(expected,cache,session)
    for model in bundle['models']:
        if not model.get('private') and not valid_file(cache/model['target'],model):raise ValueError('Model cache changed')
    return cache/'ComfyUI',str(cache/'venv/bin/python')


def cache_view(root,session,shared=None):
    """Directory skeleton with only public files linked; job writes stay private.

    Pinned voice consumers use CACHE/ComfyUI/input explicitly. The view preserves
    that contract without sharing any job's references, exports or weights.
    """
    root=Path(root);view=root/'cache';shared=Path(shared) if shared is not None else Path(session)/'cache'
    view.mkdir(mode=0o700)
    for source in sorted(shared.rglob('*')):
        relative=source.relative_to(shared)
        if relative.parts[0]=='voice-export.json' or relative.parts[:2] in {
                ('ComfyUI','input'),('ComfyUI','output'),('ComfyUI','temp')}:
            continue
        target=view/relative
        if source.is_symlink():
            target.parent.mkdir(parents=True,exist_ok=True)
            target.symlink_to(source.resolve(),target_is_directory=source.is_dir())
        elif source.is_dir():target.mkdir(parents=True,exist_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            target.symlink_to(source.resolve())
    (root/'comfy-input').mkdir(exist_ok=True)
    (view/'ComfyUI').mkdir(exist_ok=True)
    (view/'ComfyUI/input').symlink_to(root/'comfy-input',target_is_directory=True)
    return view


def stop_group(proc):
    if proc is None:return
    # The Comfy child owns a new session/process group. Kill the entire group,
    # including voice workers, then verify no non-zombie group members remain.
    pgid=proc.pid
    try:os.killpg(pgid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:pass
    try:os.killpg(pgid,signal.SIGKILL)
    except ProcessLookupError:pass
    proc.wait(timeout=10)
    until=time.monotonic()+10
    while True:
        live=False
        for entry in Path('/proc').glob('[0-9]*/stat'):
            try:
                stat=entry.read_text().rsplit(')',1)[1].split()
                if int(stat[2])==pgid and stat[0]!='Z':live=True
            except (OSError,ValueError,IndexError):continue
        if not live:return
        if time.monotonic()>=until:raise RuntimeError('Job process group remains alive')
        time.sleep(.05)


def remove_verified(path):
    if path.is_symlink() or path.is_file():path.unlink()
    elif path.exists():shutil.rmtree(path)
    if path.exists() or path.is_symlink():raise RuntimeError('Private cleanup incomplete')


def clean_job(root,proc):
    stop_group(proc)
    # Keep only verified outputs and public control records for collection.
    keep={'out','artifacts.json','status.json','launch.json','native-submission.json','native-telemetry.json'}
    for path in root.iterdir():
        if path.name not in keep:remove_verified(path)
    remove_verified(Path('/content/castle-parity-input'))


def run_native(root,manifest_sha256,session):
    root=Path(root);manifest_path=root/'manifest.json'
    if file_hash(manifest_path)!=manifest_sha256:raise ValueError('Manifest digest mismatch')
    manifest=validate_native_manifest(json.loads(manifest_path.read_text()),root)
    bundle=manifest['jobs'][0]['bundle'];job=manifest['job_id'];deadline=manifest['deadline_epoch'];proc=None
    state={'job_id':job,'manifest_sha256':manifest_sha256,'phase':'running'}
    startup_log=session/'job-evidence'/job/'comfy-startup.log';log=None;private={};next_log_bound=0
    def maintain_log():
        nonlocal next_log_bound
        if time.monotonic()>=next_log_bound:
            next_log_bound=time.monotonic()+2
            try:bound_log(startup_log)
            except Exception:note_log(startup_log,'log_bound_failed')
    def save(phase,**extra):atomic(root/'status.json',{**state,'phase':phase,**extra})
    current={'stage':'bootstrap'}
    def stage(value):
        current.clear();current.update(stage=value)
        save('running',diagnostics=dict(current))
    def guard():
        if time.time()>=deadline or (root/'cancel').exists():raise RuntimeError('Cancelled or deadline exceeded')
    # Prevent two processes in one lease, even after a lost launch response.
    lock=(session/'job.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return 0
    if (root/'native-submission.json').exists():
        save('failed',done=True,message='Submission already attempted; never resubmit');return 1
    save('running')
    try:
        guard()
        gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True).strip()
        verify_gpu(gpu)
        core,python=prepared(bundle,session);source_cache=core.parent;guard()
        cache=cache_view(root,session,core.parent)
        core=cache/'ComfyUI'
        for item in bundle['models']:
            if item.get('private'):
                assemble_private_model(root,cache,item)
        if bundle.get('voice_export'):
            atomic(cache/'voice-export.json',bundle['voice_export'])
            (cache/'voice-export.json').chmod(0o600)
        for item in bundle['inputs']:
            name=bindings(manifest)[item['slot']]
            dest=root/'comfy-input'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,dest)
            if item['kind']=='video':
                alias=Path('/content/castle-parity-input')/name;alias.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,alias)
        env={**os.environ,'CASTLE_PARITY_CACHE':str(cache),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
        stage('node_start')
        comfy_identity({**{k:bundle[k] for k in ('core','node_packs','runtime_lock','feature','voice_runtime') if k in bundle},'models':[m for m in bundle['models'] if not m.get('private')]},source_cache,require_ready=True)
        log=private_log(startup_log)
        proc=subprocess.Popen(comfy_args(python,core,root),cwd=core,env=env,stdout=log,stderr=log,start_new_session=True)
        info=None
        for _ in range(180):
            maintain_log();guard()
            if proc.poll() is not None:raise RuntimeError('Native Comfy startup failed')
            try:info=local_request('GET','/object_info');break
            except Exception:time.sleep(1)
        if info is None:raise RuntimeError('Native Comfy startup timed out')
        stage('prompt_validation')
        plan=compile_request(bundle,'colab',request_id=job,bindings=bindings(manifest),voice_consent=manifest['jobs'][0].get('voice_consent',False))
        probe=dependency_probe(plan['json']['prompt'],info)
        if not probe['ready']:
            private=private_diagnostics(probe)
            current['error_code']='dependency_gap'
            raise RuntimeError('Native dependency gap')
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
            maintain_log();guard()
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
                    src=(root/'comfy-output'/item.get('subfolder','')/item['filename']).resolve()
                    if not src.is_relative_to((root/'comfy-output').resolve()):raise ValueError('Output path escaped')
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
        import traceback
        traceback.print_exc(file=sys.stderr)
        private.update(getattr(error,'private_dependency_diagnostics',{}))
        if log:log.flush()
        private['startup_tail']=log_tail(startup_log) or private.get('startup_tail','')
        if getattr(error,'error_code',None) in {'python_mismatch','node_import_failed'}:
            save('failed',done=True,diagnostics=session_failure_diagnostics(error,'runtime_lock'),private_dependency_diagnostics=private_diagnostics(private),message='遠端環境缺少必要元件，尚未開始生成');return 1
        try:from .remote_job import failure_diagnostics, sanitize_diagnostics
        except ImportError:from remote_job import failure_diagnostics, sanitize_diagnostics
        diagnostic={**failure_diagnostics(error,current['stage']),**current,**getattr(error,'native_diagnostics',{})}
        if proc is not None and current['stage'] in {'node_start','execute'} and proc.poll() is not None:diagnostic['exit_code']=proc.poll()
        diagnostic.setdefault('error_code','output_contract_failed' if current['stage']=='ffprobe' else 'stage_failed')
        if log:log.flush()
        private.update(startup_tail=log_tail(startup_log))
        message='遠端環境缺少必要元件，尚未開始生成' if diagnostic.get('error_code')=='dependency_gap' else '原生圖執行失敗；請查看階段診斷'
        save('failed',done=True,diagnostics=session_sanitize_diagnostics(diagnostic),private_dependency_diagnostics=private_diagnostics(private),message=message);return 1
    finally:
        try:
            clean_job(root,proc)
            atomic(root/'reusable-clean.json',{'job_id':job,'manifest_sha256':manifest_sha256,'clean':True})
        finally:
            if log:
                log.close();compact_log(startup_log,session)
            lock.close()


if __name__=='__main__':
    import uuid
    mode=sys.argv[1];session=Path(sys.argv[2])
    assert session.parent==Path('/content/castle-sessions') and str(uuid.UUID(session.name))==session.name
    assert not session.is_symlink()
    if mode=='bootstrap':raise SystemExit(bootstrap_session(session))
    assert mode=='job'
    root=Path(sys.argv[3]);checksum=sys.argv[4]
    assert root.parent==Path('/content/web-comfyui') and str(uuid.UUID(root.name))==root.name and not root.is_symlink()
    raise SystemExit(run_native(root,checksum,session))
