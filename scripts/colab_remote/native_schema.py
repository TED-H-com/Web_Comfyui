"""Offline validation for server-selected native P1 bundles."""
import copy
import hashlib
import math
import uuid
from pathlib import Path
try:
    from .native_contracts import validate_bundle, voice_gate, compile_request
except ImportError:
    from native_contracts import validate_bundle, voice_gate, compile_request

def validate_native_manifest(manifest, job_dir, check_files=True):
    m=copy.deepcopy(manifest)
    if m.get('schema_version')!=1 or str(uuid.UUID(m['job_id']))!=m['job_id']:raise ValueError('Invalid native manifest identity')
    if type(m.get('deadline_epoch')) not in (int,float) or not math.isfinite(m['deadline_epoch']) or m['deadline_epoch']<=0:raise ValueError('Invalid native deadline')
    if len(m.get('jobs',[]))!=1:raise ValueError('One native graph per runtime lease')
    spec=m['jobs'][0]
    if spec.get('engine')!='castle_native_p1':raise ValueError('Unknown native engine')
    bundle=validate_bundle(spec['bundle']);voice_gate(bundle,'colab',spec.get('voice_consent',False))
    if spec.get('type')!=('tts' if bundle['feature'] in {'voice-speak','voxcpm-tts'} else 'video'):raise ValueError('Wrong output kind')
    expected=[{'path':i['name'],'size':i['bytes'],'sha256':i['sha256']} for i in bundle['inputs']]
    if m.get('input_files')!=expected:raise ValueError('Native input inventory mismatch')
    for i in expected:
        p=Path(job_dir)/i['path']
        if check_files and (p.is_symlink() or not p.resolve().is_relative_to(Path(job_dir).resolve()) or p.stat().st_size!=i['size'] or hashlib.sha256(p.read_bytes()).hexdigest()!=i['sha256']):raise ValueError('Native input integrity failure')
    for name,item in bundle.get('runner_sources',{}).items():
        if not name or Path(name).name!=name:raise ValueError('Invalid runner source name')
        path=Path(job_dir)/'runner'/name
        if check_files and (path.is_symlink() or not path.is_file() or path.stat().st_size!=item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']):raise ValueError('Runner differs from frozen bundle')
    compile_request(bundle,'colab',request_id=m['job_id'],voice_consent=spec.get('voice_consent',False))
    return m
