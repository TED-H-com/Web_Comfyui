"""Session-only adapters; frozen runner, bundle and runtime versions stay intact."""
import hashlib
import json
from pathlib import Path
import subprocess

REGISTRY=Path(__file__).with_name('colab_session_overlays')

class OverlayMismatch(ValueError):
    error_code='node_overlay_mismatch'

def digest(data):return hashlib.sha256(data).hexdigest()

def apply_overlay(repository,commit,root,relative,pinned,*,registry=REGISTRY):
    """An already matching pack needs no mutation. Every mismatch fails closed."""
    root=Path(root);target=root/relative
    try:
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):raise OverlayMismatch()
        data=target.read_bytes()
        if digest(data)==pinned:return False
        rows=json.loads((registry/'registry.json').read_text())
        matches=[r for r in rows if (r['repository'],r['commit'],r['path'],r['upstream_sha256'],r['pinned_sha256'])==(repository,commit,relative,digest(data),pinned)]
        if len(matches)!=1:raise OverlayMismatch()
        row=matches[0];patch=registry/row['patch']
        if patch.is_symlink() or not patch.resolve().is_relative_to(registry.resolve()) or digest(patch.read_bytes())!=row['patch_sha256']:raise OverlayMismatch()
        head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        if head!=commit:raise OverlayMismatch()
        for extra in (['--check'],[]):
            subprocess.run(['git','-C',str(root),'apply',*extra,str(patch)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if digest(target.read_bytes())!=pinned:raise OverlayMismatch()
        return True
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        raise OverlayMismatch('node_overlay_mismatch') from None

def after_checkout(plan,cache,args):
    # Called only after a successful pinned checkout, before native_runner's
    # unchanged strict valid_file loop (size AND hash).
    if len(args)!=6 or args[:2]!=['git','-C'] or args[3:5]!=['checkout','--detach']:return
    try:from native_runner import PACKS
    except ImportError:from scripts.colab_remote.native_runner import PACKS
    for name,pack in plan.get('node_packs',{}).items():
        if name=='castle_breezyvoice':continue
        root=Path(cache)/'ComfyUI/custom_nodes'/name
        if Path(args[2])!=root:continue
        if args[5]!=pack['commit']:raise OverlayMismatch('node_overlay_mismatch')
        for relative,pin in pack['files'].items():
            apply_overlay(PACKS[name],pack['commit'],root,relative,pin['sha256'])


    voice=plan.get('voice_runtime')
    if voice and Path(args[2])==Path(cache)/'BreezyVoice':
        if args[5]!=voice['commit']:raise OverlayMismatch('node_overlay_mismatch')
        marker=Path(cache)/'voice-overlay-identity'
        if not marker.exists() or marker.read_text()!=overlay_identity():invalidate_voice_ready(cache)
        rows=json.loads((REGISTRY/'registry.json').read_text())
        matches=[r for r in rows if (r['repository'],r['commit'])==(voice['repository'],voice['commit'])]
        if {r['path'] for r in matches}!={'cosyvoice/cli/frontend.py','single_inference.py'}:raise OverlayMismatch('node_overlay_mismatch')
        for row in matches:
            apply_overlay(voice['repository'],voice['commit'],Path(args[2]),row['path'],row['pinned_sha256'])
        verify_breezy_inventory(plan,cache)
        marker.write_text(overlay_identity())

def overlay_identity():
    return digest((REGISTRY/'registry.json').read_bytes()+(REGISTRY/'breezy-inventory.json').read_bytes())

def verify_breezy_inventory(plan,cache):
    try:
        inventory=json.loads((REGISTRY/'breezy-inventory.json').read_text())
        voice=plan['voice_runtime'];root=Path(cache)/'BreezyVoice'
        if (voice['repository'],voice['commit'])!=(inventory['repository'],inventory['commit']):raise OverlayMismatch()
        if subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()!=voice['commit']:raise OverlayMismatch()
        for relative,expected in inventory['files'].items():
            path=root/relative
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or digest(path.read_bytes())!=expected:raise OverlayMismatch()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):raise OverlayMismatch('node_overlay_mismatch') from None

def invalidate_voice_ready(cache):
    for name in ('voice-ready.json','voice-import-check.json'):
        (Path(cache)/name).unlink(missing_ok=True)

def verify_cached_overlays(plans,cache):
    # An attempted bootstrap cannot return its old ready status across a registry
    # change. Fail closed; no generation or bootstrap resubmission is authorized.
    for plan in plans:
        if not plan.get('voice_runtime'):continue
        try:
            if (Path(cache)/'voice-overlay-identity').read_text()!=overlay_identity():raise OverlayMismatch()
            verify_breezy_inventory(plan,cache)
        except (OSError,OverlayMismatch):
            invalidate_voice_ready(cache)
            raise OverlayMismatch('node_overlay_mismatch') from None
