"""Repair hosted overlays without reading provider keys or contacting providers.

Apply uses BEGIN IMMEDIATE on the same preferences DB as ProviderConsole.
Dry-run is immutable/read-only and refuses a pending WAL/journal; stop the web
and retry if writers prevent a stable dry-run. No runtime/store initialization.
"""
import sys

# Also keep direct CLI dry-runs from creating import caches.
if __name__ == '__main__':
    sys.dont_write_bytecode = True

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from hosted_compute import _read
from provider_console import write_hosted_reference
from provider_console_migration import fabric_database, read_rows
from provider_fabric.config import SecretReference
from provider_fabric.runtime import actor_context_from_castle

QUERY = "SELECT owner,provider,value FROM connections WHERE provider IN ('comfy_cloud','runninghub')"


def pending_overlays(root, rows):
    rows = [(row['owner'], row['provider'], json.loads(row['value'])) for row in rows]
    rows = [(owner, provider, data) for owner, provider, data in rows if data.get('credential_ref')]
    if not rows:
        return []
    # Existing identity only; never create an identity or resolve provider secrets.
    identity = SecretReference(kind='file', locator=str(fabric_database(root).parent / 'actor-identity.key')).resolve()
    pending = []
    for owner, provider, data in rows:
        if type(owner) is not str or '@' not in owner or owner != owner.strip().lower():
            raise PermissionError('connection owner mismatch')
        actor = actor_context_from_castle(owner, 'member', identity_key=identity)
        account = 'console-' + actor.user_id.replace(':', '-')
        if data.get('actor_id') != actor.user_id or data.get('account_id') != account:
            raise PermissionError('connection owner mismatch')
        reference = SecretReference.from_mapping(data['credential_ref'])
        locator = Path(reference.locator)
        if reference.kind.value != 'file' or locator.parent != root / 'provider-secrets' / provider / account:
            raise PermissionError('connection owner mismatch')
        if type(data.get('credential_version')) is not int or data['credential_version'] < 1:
            raise ValueError('invalid credential version')
        folder = root / 'hosted-compute' / hashlib.sha256(owner.encode()).hexdigest() / provider
        if any(path.is_symlink() for path in (folder.parent.parent, folder.parent, folder, folder / 'console-config.json', folder / 'config.json')):
            raise ValueError('unsafe hosted path')
        overlay = _read(folder / 'console-config.json', {})
        # Migration intentionally preserves legacy epochs. A matching reference
        # must not reset that epoch and invalidate existing job fingerprints.
        if overlay.get('credential_ref') != data['credential_ref'] or overlay.get('api_key'):
            # Validate every source before the first write (including legacy JSON).
            source = overlay if (folder / 'console-config.json').exists() else _read(folder / 'config.json', {})
            if not isinstance(source, dict):
                raise ValueError('invalid hosted config')
            pending.append((owner, provider, data))
    return pending


def backfill(root, *, dry_run=False):
    root = Path(root).absolute()
    path = root / 'compute-preferences.sqlite3'
    report = {'ok': True, 'dry_run': dry_run, 'needs_backfill': False, 'changed': False}
    if path.is_symlink():
        raise ValueError('unsafe preferences database')
    if not path.exists():
        return report
    if dry_run:
        pending = pending_overlays(root, read_rows(path, QUERY))
        report['needs_backfill'] = bool(pending)
        return report
    # mode=rw prevents silently creating an empty DB. BEGIN IMMEDIATE is the
    # console's exact cross-process write lock; hold it through all file writes.
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=15)
    try:
        db.row_factory = sqlite3.Row
        with db:
            db.execute('BEGIN IMMEDIATE')
            pending = pending_overlays(root, db.execute(QUERY).fetchall())
            report['needs_backfill'] = bool(pending)
            for owner, provider, data in pending:
                write_hosted_reference(root, owner, provider, data)
            report['changed'] = bool(pending)
    finally:
        db.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = backfill(args.state_root, dry_run=args.dry_run)
    except Exception:
        # Never serialize exception text, owners, references, or secret values.
        print(json.dumps({'ok': False, 'dry_run': args.dry_run, 'retry_or_review_required': True}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
