"""Session node inventory gate and private startup evidence. No graph execution."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import stat

try:
    from colab_session_diagnostics import stderr_excerpt, plan_id, atomic_status
except ImportError:
    from scripts.colab_session_diagnostics import stderr_excerpt, plan_id, atomic_status

REGISTRY=Path(__file__).with_name('colab_session_overlays')/'comfy-node-supplements.json'

class NodeImportFailed(RuntimeError):
    error_code='node_import_failed'
    substage='import_ready'
    def __init__(self,private=None):
        super().__init__('node_import_failed');self.private_dependency_diagnostics=private or {}


def private_diagnostics(value):
    """Names only; bounded evidence, deliberately absent from public sanitizers."""
    if not isinstance(value,dict):return {}
    result={}
    for key in ('missing_nodes','missing_models','missing_inputs','distributions'):
        values=value.get(key)
        if not isinstance(values,list):continue
        names=[]
        for item in values[:128]:
            if not isinstance(item,str):continue
            # Graph model entries may contain a relative directory. Never carry it.
            name=item.replace('\\','/').rsplit('/',1)[-1]
            if len(name)<=160 and re.fullmatch(r'[A-Za-z0-9_. ()-]+',name) and '@' not in item and ':' not in item:
                names.append(name)
        result[key]=sorted(set(names))
    if value.get('supplement_detail') in {'supplement_requirements_changed','supplement_requirement_unlisted','supplement_delta_unexpected'}:result['supplement_detail']=value['supplement_detail']
    tail=value.get('startup_tail')
    if isinstance(tail,str):
        # Mask whole lines BEFORE applying either line or byte limits.
        lines=[stderr_excerpt(line) for line in tail.splitlines()[-200:]]
        result['startup_tail']='\n'.join(lines).encode('utf-8')[-16384:].decode('utf-8','ignore')
    return result


LOG_BUDGET=8*1024*1024
LOG_KEEP=256*1024
LOG_RUNS=32


def _read_tail(path,limit):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):raise ValueError()
        stream.seek(max(0,info.st_size-limit));return stream.read(limit)


def _combined_tail(path,limit):
    result=b''
    for file in (Path(str(path)+'.prev'),Path(path)):
        try:result=(result+_read_tail(file,limit))[-limit:]
        except FileNotFoundError:pass
    return result


def log_tail(path):
    try:
        raw=_combined_tail(path,LOG_KEEP)
        if len(raw)==LOG_KEEP:raw=raw.partition(b'\n')[2]
        return private_diagnostics({'startup_tail':raw.decode('utf-8','replace')}).get('startup_tail','')
    except Exception:return ''


def note_log(path,code):
    # Fixed vocabulary only, private sidecar; logging errors never affect work.
    if code not in {'log_bound_failed','log_compaction_failed','log_retention_failed'}:return
    try:
        file=Path(str(path)+'.notes.json');notes=[]
        if file.exists():notes=json.loads(file.read_text())
        atomic_status(file,sorted(set(notes+[code])))
    except Exception:pass


def private_log(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    os.fchmod(fd,0o600)
    return os.fdopen(fd,'w')


def bound_log(path,budget=LOG_BUDGET,keep=LOG_KEEP):
    """Best effort copy-truncate; regular O_APPEND child fd is never changed.

    Not a quota: max live growth is budget + write rate * poll interval.
    Failure of the bounder leaves the original file usable by the child.
    """
    temp=None
    try:
        path=Path(path)
        with os.fdopen(os.open(path,os.O_RDWR|os.O_NOFOLLOW),'r+b') as live:
            info=os.fstat(live.fileno())
            if not stat.S_ISREG(info.st_mode):raise ValueError()
            if info.st_size<=budget:return
            live.seek(max(0,info.st_size-keep));tail=live.read(keep)
            temp=Path(str(path)+'.'+uuid.uuid4().hex+'.tmp')
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out:out.write(tail)
            os.replace(temp,Path(str(path)+'.prev'));temp=None
            live.truncate(0)
    except Exception:note_log(path,'log_bound_failed')
    finally:
        if temp is not None:
            try:temp.unlink(missing_ok=True)
            except Exception:pass


def compact_log(path,session,keep=LOG_KEEP,runs=LOG_RUNS):
    # Called only at run end, after diagnostic extraction and child cleanup.
    try:
        path=Path(path);tail=_combined_tail(path,keep)
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_NOFOLLOW),'wb') as out:
            out.write(tail);out.truncate(len(tail));os.fchmod(out.fileno(),0o600)
        Path(str(path)+'.prev').unlink(missing_ok=True)
    except Exception:note_log(path,'log_compaction_failed')
    try:
        logs=[p for p in Path(session).rglob('comfy-*.log') if not p.is_symlink() and p.is_file()]
        for old in sorted(logs,key=lambda p:p.stat().st_mtime_ns,reverse=True)[runs:]:
            old.unlink()
            for suffix in ('.prev','.notes.json'):Path(str(old)+suffix).unlink(missing_ok=True)
    except Exception:note_log(path,'log_retention_failed')


def comfy_args(python,core,root):
    return [str(python),str(Path(core)/'main.py'),'--listen','127.0.0.1','--port','8189','--disable-auto-launch',
            '--input-directory',str(Path(root)/'comfy-input'),'--output-directory',str(Path(root)/'comfy-output'),
            '--temp-directory',str(Path(root)/'comfy-temp')]


def supplement(plan):
    return json.loads(REGISTRY.read_text()).get(plan_id(plan),{}).get('packs',{})


def spec_failure(cause,names=()):
    error=RuntimeError('Supplement verification failed')
    error.command_label='pip_node:ComfyUI-KJNodes'
    error.substage='pip_node:ComfyUI-KJNodes'
    error.private_dependency_diagnostics=private_diagnostics({'supplement_detail':{'specification':'supplement_requirements_changed','inventory':'supplement_requirement_unlisted','delta':'supplement_delta_unexpected'}[cause],'distributions':list(names)})
    return error


def verify_requirements(path,pack):
    file=Path(path)/'requirements.txt'
    try:
        meta=pack['requirements']
        with os.fdopen(os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size!=meta['bytes']:raise ValueError()
            raw=stream.read(meta['bytes']+1)
        if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError()
        return raw.decode('utf-8')
    except (OSError,ValueError,KeyError,TypeError):raise spec_failure('specification') from None


def verify_supplement(plan,cache):
    try:
        from native_runner import valid_file
    except ImportError:
        from scripts.colab_remote.native_runner import valid_file
    for name,pack in supplement(plan).items():
        path=Path(cache)/'ComfyUI/custom_nodes'/name
        try:
            head=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
            if head!=pack['commit'] or not all(valid_file(path/n,m) for n,m in pack['files'].items()):raise NodeImportFailed()
            verify_requirements(path,pack)
        except (OSError,subprocess.SubprocessError):raise NodeImportFailed() from None


def normalized(name):return re.sub(r'[-_.]+','-',name).lower()


def pins(values):
    result={}
    for pin in values:
        match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9_.+!-]+)',pin)
        if not match or normalized(match[1]) in result:raise spec_failure('inventory')
        result[normalized(match[1])]=match[2]
    return result


def installed(python,run):
    script="import importlib.metadata as m,json; print(json.dumps([[d.metadata['Name'],d.version] for d in m.distributions()]))"
    try:
        rows=json.loads(run([str(python),'-I','-c',script],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout)
        return pins([n+'=='+v for n,v in rows])
    except (ValueError,TypeError,KeyError,subprocess.SubprocessError):raise spec_failure('delta') from None


def prepare_supplement(plan,cache,run,stage):
    try:
        from native_runner import git_checkout,PACKS
    except ImportError:
        from scripts.colab_remote.native_runner import git_checkout,PACKS
    for name,pack in supplement(plan).items():
        stage('pip_node:'+name)
        path=Path(cache)/'ComfyUI/custom_nodes'/name
        git_checkout(PACKS[name],pack['commit'],path,lambda a:run(a,check=True))
        verify_supplement(plan,cache)
        original=pins(plan['runtime_lock']['comfy']);declared=pins(pack['install_pins'])
        if any(n in original and original[n]!=v for n,v in declared.items()):raise spec_failure('inventory')
        permitted={**original,**declared};requirements=path/'requirements.txt'
        for line in verify_requirements(path,pack).splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\s*(?:==|>=|<=|~=|!=|>|<)[A-Za-z0-9_.+!,-]+)?',line)
            if not match or normalized(match[1]) not in permitted:raise spec_failure('inventory',[match[1]] if match else [])
        # Exact pins cover direct AND transitive additions; never resolve outside
        # the reviewed set. No change to the base flow's resolver or lock.
        python=Path(cache)/'venv/bin/python';before=installed(python,run)
        audit=Path(cache)/'supplement-distributions.json'
        atomic_status(audit,{'before':before,'declared':declared})
        constraint=Path(cache)/'supplement-constraints.txt'
        constraint.write_text(''.join(n+'=='+v+'\n' for n,v in sorted(permitted.items())))
        stage('pip_node:'+name)
        verify_requirements(path,pack) # Immediately before pip, after all probes.
        run([str(python),'-m','pip','install','--no-deps','-c',str(Path(cache)/'constraints.txt'),'-c',str(constraint),'-r',str(requirements),*pack['install_pins']],check=True)
        after=installed(python,run)
        changed=[n for n,v in before.items() if after.get(n)!=v]
        added={n:v for n,v in after.items() if n not in before}
        atomic_status(audit,{'before':before,'after':after,'declared':declared,'added':added,'changed':changed})
        bad=changed+[n for n,v in added.items() if declared.get(n)!=v]
        if bad:raise spec_failure('delta',bad)


def gate_identity(plan,session):
    try:
        inventory=json.loads((Path(session)/'node-requirements.json').read_text())[plan_id(plan)]
        if not isinstance(inventory,list) or not inventory or not all(isinstance(n,str) and re.fullmatch(r'[A-Za-z0-9_. ()-]{1,160}',n) for n in inventory):raise ValueError()
        return {'plan_id':plan_id(plan),'classes':sorted(set(inventory)),
                'supplement_sha256':hashlib.sha256(REGISTRY.read_bytes()).hexdigest()}
    except (OSError,ValueError,KeyError,TypeError):raise NodeImportFailed() from None


def verify_ready(plan,cache,session):
    try:
        if json.loads((Path(cache)/'node-import-ready.json').read_text())!=gate_identity(plan,session):raise NodeImportFailed()
        verify_supplement(plan,cache)
    except (OSError,ValueError):raise NodeImportFailed() from None


def node_gate(plan,cache,session,stop_group,*,popen=subprocess.Popen,request=None):
    """Starts once, queries object_info only, kills the entire group before ready."""
    try:
        from native_runner import local_request
        from colab_session_voice import comfy_identity
    except ImportError:
        from scripts.colab_remote.native_runner import local_request
        from scripts.colab_session_voice import comfy_identity
    request=request or local_request
    cache=Path(cache);identity=gate_identity(plan,session)
    comfy_identity(plan,cache,require_ready=True)
    root=cache.parent/'node-import-probe';root.mkdir(exist_ok=True,mode=0o700)
    for name in ('comfy-input','comfy-output','comfy-temp'):(root/name).mkdir(exist_ok=True,mode=0o700)
    logfile=cache.parent/'comfy-bootstrap.log';proc=None;private={};success=False
    attempt={'attempt':str(uuid.uuid4()),'plan_id':identity['plan_id']}
    atomic_status(cache.parent/'node-probe-attempt.json',attempt)
    with private_log(logfile) as log:
        try:
            proc=popen(comfy_args(cache/'venv/bin/python',cache/'ComfyUI',root),cwd=cache/'ComfyUI',
                       env={**os.environ,'CASTLE_PARITY_CACHE':str(cache),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'},
                       stdout=log,stderr=log,start_new_session=True)
            until=time.monotonic()+180;info=None;next_bound=0
            while time.monotonic()<until:
                if time.monotonic()>=next_bound:
                    next_bound=time.monotonic()+2
                    try:bound_log(logfile)
                    except Exception:note_log(logfile,'log_bound_failed')
                if proc.poll() is not None:raise NodeImportFailed()
                try:info=request('GET','/object_info');break
                except Exception:time.sleep(1)
            if not isinstance(info,dict):raise NodeImportFailed()
            private={'missing_nodes':sorted(set(identity['classes'])-info.keys()),'missing_models':[],'missing_inputs':[]}
            if private['missing_nodes']:raise NodeImportFailed(private)
            success=True
        except Exception as error:
            private.update(getattr(error,'private_dependency_diagnostics',{}))
            log.flush();private['startup_tail']=log_tail(logfile)
            raise NodeImportFailed(private_diagnostics(private)) from None
        finally:
            # No success marker can be published while a child is still alive.
            try:
                stop_group(proc)
                atomic_status(cache.parent/'node-probe-clean.json',{**attempt,'clean':True})
            finally:
                log.flush();compact_log(logfile,session)
    if success:atomic_status(cache/'node-import-ready.json',identity)
