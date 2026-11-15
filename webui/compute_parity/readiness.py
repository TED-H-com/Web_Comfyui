"""Account/version/bundle-scoped evidence; console reads never contact a provider."""
import json
import time
from pathlib import Path
from .contracts import UNITS,number

def entries(root):
    try:return json.loads((Path(root)/'readiness.json').read_text()).get('entries',[])
    except (OSError,ValueError):return []

def match(root,owner,lane,version,bundle=None,now=None):
    now=time.time() if now is None else now
    return next((e for e in entries(root) if e.get('owner')==owner and e.get('lane')==lane
        and e.get('credential_version')==version and (bundle is None or e.get('bundle_sha256')==bundle)
        and number(e.get('expires_at')) and e['expires_at']>now),None)

def dependencies_usable(e,lane):
    if not e or not e.get('dependencies_ready'):return False
    if lane in {'runninghub','comfy_cloud'}:
        from compute_parity.model_aliases import VERSION, reviewed_map
        try: reviewed_map()
        except (ValueError, OSError): return False
        if e.get('alias_map_version') != VERSION or e.get('alias_evidence_verified') is not True:return False
    if lane=='runninghub':
        if e.get('hashes_verified') is not True:return False
        if str(e.get('lifecycle_mode','')).startswith('stopped_'):return False
        if e.get('lifecycle_verified') is not True and e.get('lifecycle_mode')!='provisional_monitored':return False
    if lane=='comfy_cloud' and e.get('hashes_verified') is not True:
        if not (e.get('hash_mode')=='name_size_provisional' and e.get('lora_sha_verified') is True):return False
    return True

def usable(e,lane):
    if not dependencies_usable(e,lane):return False
    q=e.get('quote',{})
    return q.get('unit')==UNITS[lane] and number(q.get('amount')) and q['amount']>0 and bool(q.get('basis'))

def console_ready(console,lane,data, *, connection_only=False):
    root=console.root/'compute-parity'
    try:
        catalog=json.loads((root/'bundles-r29/catalog.json').read_text())
        bundles={c['bundle_sha256'] for c in catalog['cases'] if c.get('owner')==console.owner and c.get('ready')}
    except (OSError,ValueError,KeyError):return False
    # Requires at least one exact authorized bundle; no global "enabled" switch.
    return any((dependencies_usable(e,lane) if lane in {"runninghub","comfy_cloud"} else usable(e,lane)) for e in entries(root) if e.get('owner')==console.owner
        and e.get('lane')==lane and e.get('bundle_sha256') in bundles and e.get('credential_version')==data.get('credential_version')
        and e.get('expires_at',0)>time.time()
        and (not connection_only or (e.get('connection_verified') is True
            and e.get('observed_at',0) >= (data.get('checked_at') or 0))))


def save(root,entry):
    import fcntl,os,tempfile
    root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/'readiness.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        identity=lambda e:tuple(e.get(k) for k in ('owner','lane','bundle_sha256'))
        rows=[e for e in entries(root) if identity(e)!=identity(entry)]+[entry]
        fd,name=tempfile.mkstemp(dir=root)
        try:
            with os.fdopen(fd,'w') as f:json.dump({'entries':rows},f);f.flush();os.fsync(f.fileno())
            os.replace(name,root/'readiness.json')
        finally:Path(name).unlink(missing_ok=True)
