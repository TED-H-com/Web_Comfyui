"""Offline, explicit hosted-key migration. Never invoked by a GET or startup.

Stop configuration writers before apply/rollback. Backups contain secrets and
stay in the private state filesystem. No provider requests are made here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from compute_preferences import ComputePreferences
from hosted_compute import _read, _write, _directory
from provider_console import reference_dict
from provider_fabric.credentials import ProviderCredentialVault
from provider_fabric.runtime import actor_context_from_castle


def inventory(root, owners):
    root = Path(root)
    known = {hashlib.sha256(owner.encode()).hexdigest():owner for owner in owners}
    rows = []
    for path in sorted((root/'hosted-compute').glob('*/*/config.json')):
        value = _read(path, {})
        rows.append({'owner':known.get(path.parent.parent.name), 'provider':path.parent.name,
                     'has_legacy_key':bool(value.get('api_key')), 'enabled':value.get('enabled') is True,
                     'recipes_count':len(value.get('recipes', [])),
                     'already_migrated':path.with_name('console-config.json').exists()})
    return rows


def fabric_database(root):
    nested = Path(root)/'provider-fabric'/'provider-fabric.sqlite3'
    return nested if nested.exists() else Path(root)/'provider-fabric.sqlite3'


def read_rows(path, query, params=()):
    """No store constructors, schema changes, chmod or provider calls in inventory."""
    if not path.exists(): return []
    if path.is_symlink(): raise ValueError('unsafe inventory database')
    # mode=ro alone can create WAL/SHM files. Immutable prevents that, but
    # cannot consume a live WAL: fail closed instead of returning stale data.
    sidecars = [Path(str(path)+suffix) for suffix in ('-wal','-journal')]
    def signature():
        if any(p.exists() and p.stat().st_size for p in sidecars):
            raise ValueError('database has pending journal; quiesce writers and retry')
        info = path.stat()
        return (info.st_ino, info.st_size, info.st_mtime_ns)
    before = signature()
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows = [dict(row) for row in db.execute(query, params)]
    if signature() != before: raise ValueError('database changed during inventory; retry')
    return rows


def scoped_accounts(root, vault_root, identity_key, owners):
    # Existing immutable owner bindings are authoritative. Never adopt unowned rows.
    root, vault_root = Path(root), Path(vault_root)
    from provider_fabric.config import SecretReference
    identity = SecretReference(kind='file',locator=str(identity_key)).resolve()
    actors = {actor_context_from_castle(o,'member',identity_key=identity).user_id:o for o in owners}
    autoip = read_rows(fabric_database(root),
        "SELECT a.account_id,a.credential_kind,a.credential_locator,a.enabled,o.owner_user_id FROM provider_accounts a LEFT JOIN provider_account_owners o USING(account_id) WHERE a.provider_id='autoip'")
    for row in autoip:
        row['owner'] = actors.get(row['owner_user_id'])
        row['has_key'] = False
        if row['credential_kind'] == 'file' and row['credential_locator']:
            path = Path(row['credential_locator'])
            if path.parent != vault_root/'autoip'/row['account_id']: raise ValueError('unsafe AutoIP reference')
            from provider_fabric.config import SecretReference
            row['has_key'] = bool(SecretReference(kind='file',locator=str(path)).resolve().reveal())
    colab = read_rows(root/'colab'/'state.sqlite3', 'SELECT id,owner,snapshot FROM accounts')
    for row in colab:
        row['has_key'] = json.loads(row.pop('snapshot')).get('authenticated') is True
        if row['owner'] not in owners: row['owner'] = None
    return autoip, colab


def dry_run(root, vault_root, identity_key, owners):
    """Boolean-only public report; OAuth files are never opened or copied."""
    hosted = inventory(root, owners)
    autoip, colab = scoped_accounts(root, vault_root, identity_key, owners)
    report = {}
    for provider in ('comfy_cloud','runninghub'):
        rows = [r for r in hosted if r['provider']==provider and r['owner'] in owners]
        count = sum(r['recipes_count'] for r in rows)
        report[provider] = {'has_key':any(r['has_legacy_key'] or r['already_migrated'] for r in rows),
            'enabled':any(r['enabled'] for r in rows), 'recipes_count_is_zero':count==0,
            'recipes_present':count>0}
    known_auto = [r for r in autoip if r['owner'] in owners]
    known_colab = [r for r in colab if r['owner'] in owners]
    report['autoip'] = {'has_key':any(r['has_key'] for r in known_auto),
        'enabled':any(r['enabled'] for r in known_auto),
        'recipes_count_unavailable':True,
        'owner_review_required':any(r['owner'] is None for r in autoip)}
    report['colab'] = {'has_key':any(r['has_key'] for r in known_colab),
        'enabled':False, 'recipes_count_unavailable':True,
        'owner_scoped':all(r['owner'] is not None for r in colab),
        'oauth_untouched':True}
    report['checks'] = {'read_only':True, 'hosted_owners_resolved':all(r['owner'] is not None for r in hosted),
        'autoip_multiple_accounts':any(sum(r['owner']==o for r in autoip)>1 for o in owners)}
    return report


def private_copy(source, target):
    # Backup is an exact private snapshot, rejecting links, special files and hardlinks.
    import stat
    info = source.lstat()
    if stat.S_ISLNK(info.st_mode) or (source.is_file() and info.st_nlink != 1):
        raise ValueError('unsafe migration source')
    if source.is_dir():
        target.mkdir(mode=0o700)
        for child in source.iterdir(): private_copy(child, target/child.name)
    elif source.is_file():
        with source.open('rb') as inp, target.open('xb') as out:
            os.chmod(target,0o600)
            shutil.copyfileobj(inp,out)
            out.flush(); os.fsync(out.fileno())
    else: raise ValueError('unsafe migration source')


def rollback(backup):
    backup = Path(backup)
    manifest = _read(backup/'manifest.json')
    for item in manifest['items']:
        target, saved = Path(item['target']), backup/item['name']
        if target.is_symlink(): raise ValueError('unsafe rollback target')
        if target.suffix == '.sqlite3':
            # Offline only: stale WAL/journal files must not replay post-backup writes.
            for suffix in ('-wal','-shm','-journal'):
                Path(str(target)+suffix).unlink(missing_ok=True)
        if target.exists():
            if target.is_dir(): shutil.rmtree(target)
            else: target.unlink()
        if item['existed']:
            private_copy(saved, target)
    _write(backup/'result.json', {'state':'rolled_back'})


def migrate(root, vault_root, identity_key, owners, backup, *, fault=None):
    root, vault_root, identity_key, backup = map(Path,(root,vault_root,identity_key,backup))
    if any(not p.is_absolute() for p in (root,vault_root,identity_key,backup)): raise ValueError('absolute paths required')
    if not owners or any('@' not in owner or owner != owner.strip().lower() or owner.endswith('@castle.internal') for owner in owners):
        raise ValueError('explicit canonical authenticated owners required')
    report = inventory(root, owners)
    if any(row['owner'] is None or row['provider'] not in ('comfy_cloud','runninghub') for row in report):
        raise ValueError('unresolved owner or provider; migration refused')
    if (backup/'manifest.json').exists() and _read(backup/'result.json',{}).get('state') == 'prepared':
        raise ValueError('interrupted migration requires explicit rollback of the prepared snapshot')
    autoip, colab = scoped_accounts(root, vault_root, identity_key, owners)
    # A crash can switch a file before SQLite commits. Never bless that split state.
    for row in report:
        if not row['already_migrated']: continue
        database = root/'compute-preferences.sqlite3'
        if not database.exists(): raise ValueError('incomplete migration; rollback required')
        with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as db:
            stored = db.execute('SELECT value FROM connections WHERE owner=? AND provider=?',(row['owner'],row['provider'])).fetchone()
        path = root/'hosted-compute'/hashlib.sha256(row['owner'].encode()).hexdigest()/row['provider']/'console-config.json'
        if not stored or json.loads(stored[0]).get('credential_ref') != _read(path,{})['credential_ref']:
            raise ValueError('incomplete migration; rollback required')
    pending = [r for r in report if r['has_legacy_key'] and not r['already_migrated']]
    existing = read_rows(root/'compute-preferences.sqlite3', 'SELECT owner,provider FROM connections') if (root/'compute-preferences.sqlite3').exists() and read_rows(root/'compute-preferences.sqlite3', "SELECT name FROM sqlite_master WHERE name='connections'") else []
    present = {(r['owner'],r['provider']) for r in existing}
    imports = []
    for owner in owners:
        rows = [r for r in autoip if r['owner']==owner]
        if len(rows)==1 and (owner,'autoip') not in present: imports.append(('autoip',rows[0]))
        rows = [r for r in colab if r['owner']==owner]
        if rows and (owner,'colab') not in present: imports.append(('colab',{'owner':owner,'has_key':any(r['has_key'] for r in rows)}))
    if not pending and not imports: return {'migrated':0, 'state':'unchanged'}
    # Existing identity only: never invent an owner mapping in a migration.
    from provider_fabric.config import SecretReference
    identity = SecretReference(kind='file',locator=str(identity_key)).resolve()
    _directory(backup.parent)
    backup.mkdir(mode=0o700)  # Reuse of a backup path is forbidden.
    sources = [root/'hosted-compute', root/'compute-preferences.sqlite3', vault_root, identity_key,
               fabric_database(root), root/'compute-routing.sqlite3']
    if any(backup == source or source in backup.parents for source in sources): raise ValueError('backup must be outside migrated trees')
    items = []
    for index,source in enumerate(sources):
        item = {'target':str(source), 'name':str(index), 'existed':source.exists()}
        if source.exists():
            if source.suffix == '.sqlite3':
                if source.is_symlink(): raise ValueError('unsafe database')
                with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as src, sqlite3.connect(backup/str(index)) as dst:
                    src.backup(dst)
                os.chmod(backup/str(index),0o600)
            else: private_copy(source,backup/str(index))
        items.append(item)
    _write(backup/'manifest.json', {'version':1, 'items':items})
    _write(backup/'result.json', {'state':'prepared'})
    try:
        prefs = ComputePreferences(root/'compute-preferences.sqlite3')
        vault = ProviderCredentialVault(vault_root)
        migrated = 0
        with prefs.db(write=True) as db:
            for row in pending:
                owner,provider = row['owner'],row['provider']
                actor = actor_context_from_castle(owner,'member',identity_key=identity)
                account = 'console-'+actor.user_id.replace(':','-')
                folder = root/'hosted-compute'/hashlib.sha256(owner.encode()).hexdigest()/provider
                config = _read(folder/'config.json')
                modified = (folder/'config.json').stat().st_mtime
                result = vault.provision(provider,account,config['api_key'],credential_id='api-key-'+uuid.uuid4().hex)
                data = {'actor_id':actor.user_id, 'account_id':account, 'credential_ref':reference_dict(result.reference),
                        'credential_present':True, 'credential_version':1, 'credential_saved_at':None,
                        'legacy_modified_at':modified, 'use_enabled':config.get('enabled') is True,
                        'connection_state':'untested', 'checked_at':None, 'balances':[], 'revision':1}
                if db.execute('SELECT 1 FROM connections WHERE owner=? AND provider=?',(owner,provider)).fetchone():
                    raise ValueError('existing connection conflicts with legacy migration')
                db.execute('INSERT INTO connections VALUES (?,?,?)',(owner,provider,json.dumps(data)))
                # Preserve recipes, epoch and old disabled flag, including an owner's disabled RunningHub flag.
                config.pop('api_key',None)
                config['credential_ref'] = reference_dict(result.reference)
                _write(folder/'console-config.json',config)
                if fault: fault('after_config')
                old = db.execute('SELECT value FROM preferences WHERE owner=?',(owner,)).fetchone()
                if old:
                    value = json.loads(old[0])
                    value.setdefault('auto_policy','legacy_load')
                    value.setdefault('auto_order',[])
                    value.setdefault('revision',0)
                    prefs.put(db,owner,value)
                migrated += 1
            for provider,row in imports:
                owner = row['owner']
                actor = actor_context_from_castle(owner,'member',identity_key=identity)
                data = {'actor_id':actor.user_id, 'credential_present':row['has_key'],
                    'credential_version':1 if row['has_key'] else 0, 'credential_saved_at':None,
                    'use_enabled':bool(row.get('enabled',False)), 'connection_state':'untested',
                    'checked_at':None, 'balances':[], 'revision':1}
                if provider == 'autoip':
                    data['account_id'] = row['account_id']
                    if row['has_key']: data['credential_ref'] = {'kind':'file','locator':row['credential_locator']}
                db.execute('INSERT INTO connections VALUES (?,?,?)',(owner,provider,json.dumps(data)))
                migrated += 1
        _write(backup/'result.json', {'state':'applied','migrated':migrated})
        return {'state':'applied','migrated':migrated}
    except BaseException:
        rollback(backup)
        raise RuntimeError('migration failed; complete private snapshot restored') from None


def main():
    parser = argparse.ArgumentParser(description='Offline provider-console migration; no network')
    parser.add_argument('--state-root', type=Path)
    parser.add_argument('--vault-root',type=Path)
    parser.add_argument('--identity-key',type=Path)
    parser.add_argument('--owner',action='append',default=[])
    parser.add_argument('--backup',type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--rollback',action='store_true')
    args = parser.parse_args()
    try:
        if args.rollback:
            rollback(args.backup); result={'state':'rolled_back'}
        elif args.apply:
            result=migrate(args.state_root,args.vault_root or args.state_root/'provider-secrets',args.identity_key or fabric_database(args.state_root).parent/'actor-identity.key',args.owner,args.backup)
        else: result=dry_run(args.state_root,args.vault_root or args.state_root/'provider-secrets',args.identity_key or fabric_database(args.state_root).parent/'actor-identity.key',args.owner)
        print(json.dumps(result,ensure_ascii=False))
    except Exception:
        result = {'dry_run_failed':True} if not (args.apply or args.rollback) else {'state':'failed','message':'遷移未完成；檢查私有日誌與備份，不要啟動新版本'}
        print(json.dumps(result,ensure_ascii=False))
        raise SystemExit(1) from None

if __name__ == '__main__': main()
