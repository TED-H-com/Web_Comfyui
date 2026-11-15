"""Fixed bootstrap vocabulary and private evidence; no credential text survives."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

COMMANDS={'import_ready','python_provision','venv_create','pip_core','pip_voice','pip_voxcpm','pip_node:ComfyUI-VideoHelperSuite','pip_node:ComfyUI-KJNodes'}
SUBSTAGES=COMMANDS|{'voice_environment','voice_imports','voxcpm_imports','speaker_imports','argv','import','lock','gpu_verify','disk_check','core_checkout','node_checkout','model_download','runtime_lock','plan_validation','complete','process','progress','build_toolchain'}
ERRORS={'OperationalError','DatabaseError','IntegrityError','JSONDecodeError','UnicodeDecodeError','StopIteration','OverflowError','TimeoutError','AttributeError','IndexError','SyntaxError','Exception','RuntimeError','ValueError','TypeError','KeyError','OSError','FileNotFoundError','PermissionError','ImportError','ModuleNotFoundError','CalledProcessError','TimeoutExpired','HTTPError','URLError','BlockingIOError','AssertionError'}

def plan(bundle):
    result={k:bundle[k] for k in ('core','node_packs','runtime_lock','feature','voice_runtime') if k in bundle}
    result['models']=[m for m in bundle['models'] if not m.get('private')]
    return result

def plan_id(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def sanitize_diagnostics(value):
    try:from scripts.colab_remote.remote_job import sanitize_diagnostics as base
    except ImportError:
        try:from remote_job import sanitize_diagnostics as base
        except ImportError:base=lambda _:{}
    result=base(value)
    if not isinstance(value,dict):return result
    if value.get('stage')=='session_fault':
        from scripts.colab_transport_diagnostics import fault_diagnostic
        return fault_diagnostic({**value,'stage':value.get('substage'),'code':value.get('error_code')})
    if value.get('stage')=='session_bootstrap':result['stage']='session_bootstrap'
    if value.get('stage')=='admission':
        # Same fixed vocabulary in server admission and routed receipts.
        try:
            from colab_inputs import ADMISSION_MESSAGES
            if value.get('error_code') in set(ADMISSION_MESSAGES)|{'admission_lookup_failed','admission_row_absent','admission_in_flight','admission_cancel_unconfirmed'}:
                result.update(stage='admission',error_code=value['error_code'])
        except ImportError:pass
    if isinstance(value.get('substage'),str) and value['substage'] in SUBSTAGES:result['substage']=value['substage']
    if isinstance(value.get('error_type'),str) and value['error_type'] in ERRORS:result['error_type']=value['error_type']
    if isinstance(value.get('error_code'),str) and value['error_code'] in {'node_import_failed','dependency_gap','python_mismatch','voice_env_mismatch','node_overlay_mismatch','stage_failed','bootstrap_process_lost','bootstrap_stalled','bootstrap_timeout'}:result['error_code']=value['error_code']
    if value.get('command_label') in COMMANDS:result['command_label']=value['command_label']
    token=value.get('failing_distribution')
    if isinstance(token,str) and len(token)<=160 and re.fullmatch(r'[A-Za-z0-9_.-]+(==[A-Za-z0-9_.+!-]+)?',token):result['failing_distribution']=token
    for key,low,high in [('http_status',100,599),('exit_code',-255,255)]:
        if type(value.get(key)) is int and low<=value[key]<=high:result[key]=value[key]
    return result

def failure_diagnostics(error,substage):
    try:
        try:from scripts.colab_remote.remote_job import failure_diagnostics as base
        except ImportError:from remote_job import failure_diagnostics as base
        result=base(error,'bootstrap')
    except ImportError:result={}
    result.update(stage='session_bootstrap',substage=substage,error_code='stage_failed',error_type=type(error).__name__ if type(error).__name__ in ERRORS else 'Exception')
    for attr,key in [('code','http_status'),('returncode','exit_code')]:
        if type(getattr(error,attr,None)) is int:result[key]=getattr(error,attr)
    if getattr(error,'substage',None) in SUBSTAGES:result['substage']=error.substage
    if substage in COMMANDS:result['command_label']=substage
    for key in ('command_label','failing_distribution'):
        if hasattr(error,key):result[key]=getattr(error,key)
    if getattr(error,'error_code',None) in {'node_import_failed','python_mismatch','node_overlay_mismatch','voice_env_mismatch'}:result['error_code']=error.error_code
    return sanitize_diagnostics(result)

def atomic_status(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

FRAME_FILES={'colab_session_worker.py','session_worker.py','native_runner.py','native_schema.py','session_bootstrap.py'}

def decode_tail(value):
    """One grammar for remote redaction and untrusted bridge output."""
    if not isinstance(value,str):return ''
    lines=[]
    for line in value[:8192].splitlines()[-60:]:
        if line=='[redacted]':lines.append(line);continue
        if line.endswith(': [redacted]') and line[:-12] in ERRORS:lines.append(line);continue
        match=re.fullmatch(r'frame ([a-z_]+\.py):([0-9]{1,6})',line)
        if match and match[1] in FRAME_FILES:lines.append(line)
    return '\n'.join(lines)[-4096:]

def redacted_tail(path):
    # A structural allowlist removes ALL message text, URLs/query strings,
    # credentials, addresses and absolute paths, including unknown formats.
    path=Path(path)
    if not path.is_file() or path.is_symlink():return ''
    with path.open('rb') as stream:
        stream.seek(max(0,path.stat().st_size-8192));raw=stream.read(8192).decode('utf-8','replace')
    lines=[]
    for line in raw.splitlines()[-60:]:
        frame=re.search(r'File "[^"]*[/\\](colab_session_worker.py|session_worker.py|native_runner.py|native_schema.py|session_bootstrap.py)", line ([0-9]{1,6})',line)
        error=re.match(r'^([A-Za-z]+Error|Exception|TimeoutExpired|CalledProcessError)(?::|$)',line.strip())
        if frame:lines.append('frame '+frame[1]+':'+frame[2])
        elif error and error[1] in ERRORS:lines.append(error[1]+': [redacted]')
        else:lines.append('[redacted]')
    return decode_tail('\n'.join(lines))

def telemetry(value):
    if not isinstance(value,dict):return {}
    out={}
    history=value.get('bootstrap_stages')
    if isinstance(history,list):out['bootstrap_stages']=[x for x in history[-64:] if isinstance(x,str) and x in SUBSTAGES]
    name=value.get('gpu_name')
    if isinstance(name,str) and re.fullmatch(r'NVIDIA [A-Za-z0-9 .()_-]{1,100}',name):out['gpu_name']=name
    if isinstance(value.get('gpu'),str) and value['gpu'] in {'A100','H100','G4'}:out['gpu']=value['gpu']
    if type(value.get('vram_mib')) is int and 0<value['vram_mib']<1000000:out['vram_mib']=value['vram_mib']
    disk=value.get('disk_free_gib')
    if type(disk) in (int,float) and 0<=disk<1000000:out['disk_free_gib']=round(disk,2)
    version=value.get('python_version')
    if isinstance(version,str) and re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}',version):out['python_version']=version
    system=value.get('system_python')
    if isinstance(system,dict) and re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}',str(system.get('version',''))) and all(type(system.get(k)) is bool for k in ('ensurepip_available','headers_available')):out['system_python']={k:system[k] for k in ('version','ensurepip_available','headers_available')}
    venv=value.get('venv_python_version')
    if isinstance(venv,str) and re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}',venv):out['venv_python_version']=venv
    environments=value.get('voice_environments')
    if isinstance(environments,dict):
        safe={}
        for label,entry in environments.items():
            if label not in {'voice','voxcpm'} or not isinstance(entry,dict):continue
            row={}
            for key in ('python_build','python_version','torch_version'):
                text=entry.get(key)
                if isinstance(text,str) and re.fullmatch(r'[A-Za-z0-9 .:+_-]{1,100}',text):row[key]=text
            providers=entry.get('onnx_providers')
            allowed={'CPUExecutionProvider','CUDAExecutionProvider','AzureExecutionProvider','TensorrtExecutionProvider'}
            if isinstance(providers,list) and all(x in allowed for x in providers):row['onnx_providers']=providers
            if entry.get('imports_ready') is True:row['imports_ready']=True
            safe[label]=row
        out['voice_environments']=safe
    return out


def stderr_excerpt(value):
    """Private durable evidence only. Never add this to sanitize_diagnostics."""
    if not isinstance(value,str):return ''
    # Mask before truncating so a limit cannot split a credential or URI.
    value=re.sub(r'[^\s<>@]+@[^\s<>@]+','<email>',value)
    value=re.sub(r'(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/][^\s<>]+','<path>',value)
    value=re.sub(r'\b[A-Za-z][A-Za-z0-9+.-]*:[^\s<>]+','<url>',value,flags=re.I)
    value=re.sub(r'(?:%[0-9A-Fa-f]{2}){3,}','<redacted>',value)
    value=re.sub(r'[^\s<>@]+@[^\s<>@]+\.[A-Za-z]{2,}','<email>',value)
    value=re.sub(r'(?<![A-Za-z0-9+/_-])(?:[A-Za-z]:[\\/]|/)[^\s<>"\']+','<path>',value)
    value=re.sub(r'(?i)\b(?:authorization|cookie|token|api[_-]?key|password|secret)\s*[:=]\s*[^\s]+(?:[ \t]+[^\s]+)?','<redacted>',value)
    value=re.sub(r'[A-Za-z0-9+/_=-]{32,}','<redacted>',value)
    value=''.join(c if c in '\n\t' or ord(c)>=32 and ord(c)!=127 else ' ' for c in value)
    return '\n'.join([line.strip()[:200] for line in value.splitlines() if line.strip()][-12:])

def failing_distribution(value):
    if not isinstance(value,str):return None
    patterns=[r'No matching distribution found for ([^\s]+)',r'Could not find a version that satisfies the requirement ([^\s(]+)',r'Failed building wheel for ([^\s]+)',r"Failed to build ['\"]?([^\s'\"]+)"]
    for pattern in patterns:
        for match in re.finditer(pattern,value[-65536:]):
            if re.fullmatch(r'[A-Za-z0-9_.-]+(==[A-Za-z0-9_.+!-]+)?',match[1]):return match[1]
    return None

def command_label(args):
    if args and args[0]=='git':return None
    if len(args)>2 and args[1:3]==['-m','venv']:return 'venv_create'
    if len(args)>3 and args[1:3]==['-I','-c'] and args[3]=='import torch,numpy,scipy,torchvision,torchaudio':return 'import_ready'
    if 'pip' in args:
        for pack in ('ComfyUI-VideoHelperSuite','ComfyUI-KJNodes'):
            if any('/custom_nodes/'+pack+'/requirements.txt' in str(a) for a in args):return 'pip_node:'+pack
        if any('voxcpm-venv' in str(a) for a in args):return 'pip_voxcpm'
        if any('voice-venv' in str(a) for a in args):return 'pip_voice'
        return 'pip_core'
    return None

def system_python():
    import sys,sysconfig,importlib.util
    return {'version':'.'.join(map(str,sys.version_info[:3])),
            'ensurepip_available':importlib.util.find_spec('ensurepip') is not None,
            'headers_available':(Path(sysconfig.get_path('include'))/'Python.h').is_file()}
