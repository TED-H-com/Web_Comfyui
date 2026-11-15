"""Verify private persona binding and immutable bytes without emitting voice data."""
from pathlib import Path
import hashlib
import json
from .contracts import digest, fail


def fingerprint(path):
    path=Path(path);before=path.stat();h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):h.update(chunk)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        fail('persona_hash_mismatch','角色聲音已變更，請重新固定校準素材')
    return {'sha256':h.hexdigest(),'bytes':after.st_size}


def verify_persona(bundle, persona, *, root=None):
    root = Path(root) if root is not None else Path.home() / '.local/share/web-comfyui/integrations/voice'
    if persona != bundle['material_settings'].get('persona') or persona != bundle['voice_export'].get('persona'):
        fail('persona_hash_mismatch','角色聲音綁定已變更')
    proof=bundle.get('persona_verification')
    if not proof:fail('persona_hash_mismatch','缺少固定角色聲音驗證資料')
    try:
        reference=json.loads((root/'refs.json').read_text())['refs'][persona]
        selected={'variant':proof['variant'],'persona':persona,
            'reference':fingerprint(root/reference['wav']),
            'reference_text_sha256':hashlib.sha256(reference['text'].strip().encode()).hexdigest(),
            'models':[{'target':m['target'],**fingerprint(m['path'])} for m in bundle['models']],
            'daemon_source':fingerprint(root/'breezy_server.py')}
        valid=digest(selected)==bundle['voice_identity_sha256']
        for source in proof['service_sources']:
            valid=valid and fingerprint(source['path'])=={k:source[k] for k in ('sha256','bytes')}
    except (OSError,ValueError,KeyError,TypeError):
        valid=False
    if not valid:fail('persona_hash_mismatch','角色聲音已變更，請重新固定校準素材')
