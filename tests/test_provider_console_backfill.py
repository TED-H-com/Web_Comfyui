"""r26b regression tests; all identities, vaults and credentials are synthetic."""
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from test_provider_console import env, write, card, legacy, SECRET
from hosted_compute import HostedComputeService, PROVIDERS, _read, _write
from provider_console_backfill import backfill, main
from provider_fabric.config import SecretReference, SecretValue


def overlay(console, provider):
    return console._legacy_path(provider).with_name('console-config.json')


def state(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.stat().st_mtime_ns,
            p.read_bytes() if p.is_file() else None) for p in root.rglob('*')}


@pytest.mark.parametrize('provider', ['comfy_cloud', 'runninghub'])
def test_first_save_rotate_delete_with_fake_vault(env, monkeypatch, provider, capsys, caplog):
    values = {}
    class Vault:
        def __init__(self, root): self.root = Path(root)
        def provision(self, provider, account, secret, *, credential_id):
            path = str(self.root / provider / account / (credential_id + '.secret'))
            values[path] = secret
            return SimpleNamespace(reference=SecretReference(kind='file', locator=path))
        def delete(self, provider, account, *, credential_id, confirm):
            assert confirm
            del values[str(self.root / provider / account / (credential_id + '.secret'))]
    monkeypatch.setattr('provider_console.ProviderCredentialVault', Vault)
    monkeypatch.setattr(SecretReference, 'resolve', lambda ref: SecretValue(values[ref.locator]))
    c = env.console(); path = overlay(c, provider)
    assert not path.parent.exists()
    result = write(c, provider)
    config = _read(path)
    assert config['enabled'] is False and config['recipes'] == []
    assert config['endpoint'] == PROVIDERS[provider] and config['credential_epoch'] == '1'
    assert 'api_key' not in config and 'credential_ref' in config
    assert not c._legacy_path(provider).exists()
    assert path.stat().st_mode & 0o777 == 0o600
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert directory.stat().st_mode & 0o777 == 0o700
    service = HostedComputeService(env.root / 'hosted-compute')
    assert service._config(path.parent)['api_key'] == SECRET
    first_ref = config['credential_ref']
    write(c, provider, SECRET + '-rotated')
    assert service._config(path.parent)['api_key'] == SECRET + '-rotated'
    assert _read(path)['credential_epoch'] == '2' and _read(path)['credential_ref'] != first_ref
    c.delete_credential(provider, {'revision': card(c, provider)['revision']})
    assert service._config(path.parent)['api_key'] == ''
    assert _read(path)['credential_epoch'] == '3' and 'credential_ref' not in _read(path)
    output = capsys.readouterr()
    assert SECRET not in path.read_text() + json.dumps(result) + output.out + output.err + caplog.text


@pytest.mark.parametrize('provider', ['comfy_cloud', 'runninghub'])
def test_backfill_dry_run_idempotent_and_latest_row(env, provider, capsys, caplog):
    c = env.console(); write(c, provider)
    path = overlay(c, provider); path.unlink()
    before = state(env.root)
    assert backfill(env.root, dry_run=True) == {'ok': True, 'dry_run': True, 'needs_backfill': True, 'changed': False}
    assert state(env.root) == before
    # A rotation after the dry-run must win; apply re-reads the DB under lock.
    write(c, provider, SECRET + '-latest'); path.unlink()
    assert main(['--state-root', str(env.root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert all(type(value) is bool for value in report.values()) and report['changed']
    assert HostedComputeService(env.root / 'hosted-compute')._config(path.parent)['api_key'] == SECRET + '-latest'
    before = state(env.root)
    assert not backfill(env.root)['changed']
    assert state(env.root) == before
    assert SECRET not in path.read_text() + caplog.text


def test_backfill_legacy_preserved_and_overlay_preferred(env):
    c = env.console('owner@example.invalid'); path = legacy(env)
    # A preexisting console row is authoritative over the legacy raw key.
    with c.prefs.db(write=True) as db:
        c._put(db, 'runninghub', {'actor_id': c.actor.user_id, 'credential_present': False,
            'credential_version': 0, 'revision': 0, 'use_enabled': False, 'connection_state': 'not_configured'})
    original = path.read_bytes()
    write(c); target = overlay(c, 'runninghub')
    assert _read(target)['endpoint'] == _read(path)['endpoint']
    assert path.read_bytes() == original
    config = _read(target); config.update(recipes=[{'preserve': True}], credential_epoch='migrated-epoch')
    _write(target, config)
    before = target.read_bytes()
    assert not backfill(env.root)['changed']
    assert target.read_bytes() == before  # Matching migrated epoch must survive.
    config['credential_ref']['locator'] += '-stale'; _write(target, config)
    assert backfill(env.root)['changed']
    assert _read(target)['recipes'] == [{'preserve': True}] and _read(target)['credential_epoch'] == '1'
    assert path.read_bytes() == original and SECRET not in target.read_text()
    target.unlink(); assert backfill(env.root)['changed']
    assert _read(target)['recipes'] == [] and path.read_bytes() == original


@pytest.mark.parametrize('field', ['actor_id', 'account_id', 'credential_ref'])
def test_backfill_owner_mismatch_refuses_entire_batch(env, field, capsys):
    c = env.console(); write(c); write(c, 'comfy_cloud')
    for provider in ('comfy_cloud', 'runninghub'): overlay(c, provider).unlink()
    with c.prefs.db(write=True) as db:
        data = c._raw('runninghub', db)
        data[field] = {'kind': 'file', 'locator': '/wrong-owner/secret'} if field == 'credential_ref' else 'wrong-owner'
        c._put(db, 'runninghub', data)
    before = state(env.root)
    for dry_run in (True, False):
        with pytest.raises(PermissionError): backfill(env.root, dry_run=dry_run)
        assert state(env.root) == before
    assert main(['--state-root', str(env.root)]) == 1
    output = capsys.readouterr()
    assert all(type(v) is bool for v in json.loads(output.out).values())
    assert SECRET not in output.out + output.err and 'wrong-owner' not in output.out


@pytest.mark.parametrize('change', ['rotate', 'delete'])
def test_backfill_waits_for_console_write_lock_and_reads_committed_row(env, change, monkeypatch):
    c = env.console(); write(c); path = overlay(c, 'runninghub'); path.unlink()
    started = threading.Event(); finished = threading.Event(); errors = []
    import provider_console_backfill as module
    real_connect = module.sqlite3.connect
    def connect(*args, **kwargs):
        if threading.current_thread().name == 'backfill-test': started.set()
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    def run():
        try: backfill(env.root)
        except Exception as exc: errors.append(type(exc))
        finally: finished.set()
    # Simulate the console's transaction while the separate connection waits.
    with c.prefs.db(write=True) as db:
        data = c._raw('runninghub', db)
        thread = threading.Thread(target=run, name='backfill-test'); thread.start()
        assert started.wait(5) and not finished.wait(.15)
        if change == 'delete': data.pop('credential_ref')
        else: data['credential_version'] += 1
        c._put(db, 'runninghub', data)
    thread.join(5)
    assert finished.is_set() and not errors
    if change == 'delete': assert not path.exists()
    else: assert _read(path)['credential_epoch'] == '2'


def test_cli_dry_run_zero_writes_and_sanitized_error(env, capsys, monkeypatch):
    c = env.console(); write(c); overlay(c, 'runninghub').unlink()
    before = state(env.root)
    script = Path(__file__).resolve().parents[1] / 'webui/provider_console_backfill.py'
    result = subprocess.run([sys.executable, '-B', str(script), '--state-root', str(env.root), '--dry-run'], capture_output=True, text=True)
    assert result.returncode == 0 and state(env.root) == before
    assert all(type(v) is bool for v in json.loads(result.stdout).values())
    assert SECRET not in result.stdout + result.stderr
    def fail(*args, **kwargs): raise RuntimeError(SECRET)
    monkeypatch.setattr('provider_console_backfill.backfill', fail)
    assert main(['--state-root', str(env.root)]) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_dry_run_refuses_wal_and_absent_state_is_noop(env, tmp_path):
    c = env.console(); write(c)
    import sqlite3
    db = sqlite3.connect(c.prefs.path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO local_grants VALUES ('wal@example.test',1)"); db.commit()
        before = state(env.root)
        with pytest.raises(ValueError, match='pending journal'): backfill(env.root, dry_run=True)
        assert state(env.root) == before
    finally: db.close()
    missing = tmp_path / 'absent'
    for dry_run in (True, False): assert not backfill(missing, dry_run=dry_run)['changed']
    assert not missing.exists()
