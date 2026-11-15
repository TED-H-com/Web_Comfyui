"""Transport aliases only; frozen bundle, graph and strict runner pins unchanged."""
import copy,hashlib
from pathlib import Path
SUFFIXES={'.png','.jpg','.jpeg','.webp','.mp4','.mov','.webm','.wav','.mp3','.flac','.m4a'}
def validate_native_manifest(manifest,job_dir,check_files=True):
    try:from native_schema import validate_native_manifest as frozen
    except ImportError:from scripts.colab_remote.native_schema import validate_native_manifest as frozen
    original=copy.deepcopy(manifest);items=original['jobs'][0]['bundle']['inputs']
    expected=[{'path':i['name'],'size':i['bytes'],'sha256':i['sha256']} for i in items]
    actual=manifest.get('input_files')
    if not isinstance(actual,list) or len(actual)!=len(expected):raise ValueError('Native transport inventory mismatch')
    original['input_files']=expected
    frozen(original,job_dir,check_files=False)
    for old,new in zip(expected,actual):
        name=new.get('path','');suffix=Path(name).suffix.lower()
        allowed=name==old['path'] or (Path(old['path']).suffix.lower() not in SUFFIXES and suffix in SUFFIXES and name==old['path']+suffix)
        if not allowed or new.get('size')!=old['size'] or new.get('sha256')!=old['sha256']:raise ValueError('Native transport identity mismatch')
        p=Path(job_dir)/name
        if check_files and (p.is_symlink() or not p.resolve().is_relative_to(Path(job_dir).resolve()) or p.stat().st_size!=new['size'] or hashlib.sha256(p.read_bytes()).hexdigest()!=new['sha256']):raise ValueError('Native input integrity failure')
    if check_files:
        for name,item in original['jobs'][0]['bundle'].get('runner_sources',{}).items():
            p=Path(job_dir)/'runner'/name
            if p.is_symlink() or not p.is_file() or p.stat().st_size!=item['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Runner differs from frozen bundle')
    return copy.deepcopy(manifest)

def bindings(manifest):
    return {item['slot']:entry['path'] for item,entry in zip(manifest['jobs'][0]['bundle']['inputs'],manifest['input_files'])}
