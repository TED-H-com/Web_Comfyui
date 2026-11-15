"""Session-owned provider connections. No provider I/O on reads or credential writes.

Credentials are immutable vault versions; SQLite contains references only. The
preferences database is the atomic boundary for selection, order and revisions.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import time
import uuid
from pathlib import Path

from compute_preferences import ComputePreferences
from compute_routing import ComputeRoutingError, TERMINAL
from provider_fabric.credentials import ProviderCredentialVault
from provider_fabric.config import SecretReference

PROVIDERS = ('local', 'colab', 'autoip', 'comfy_cloud', 'runninghub')
KEY_PROVIDERS = ('autoip', 'comfy_cloud', 'runninghub')
PREPARING = '金鑰已儲存；此平台功能準備中，你不需要填配方'
REASONS = {
    'local_permission_required': '尚未取得本機使用權',
    'credential_required': '請先儲存金鑰',
    'login_required': '請先使用 Google 登入',
    'connection_test_required': '請先通過測試連線',
    'recipe_not_ready': '此平台功能準備中，你不需要填配方',
    'use_disabled': '尚未開啟使用',
    'connection_unavailable': '已選用，暫時不可用',
    'test_unavailable': '連線檢查尚未開放',
    'invalid_credential': '金鑰無效，請更換',
    'provider_unavailable': '平台暫時無回應，請稍後再試',
    'migration_required': '舊金鑰等待安全遷移，無須重新輸入',
    'connection_in_use': '仍需此連線確認原任務，完成後可移除',
}


def fail(code, status=409):
    raise ComputeRoutingError(REASONS.get(code, '設定已更新，請重新整理'), code=code, status=status)


def strict(body, fields):
    if type(body) is not dict or set(body) != set(fields):
        raise ComputeRoutingError('請求欄位不正確；只能管理自己的連線', code='invalid_request', status=400)


def reference_dict(reference):
    return {'kind':reference.kind.value, 'locator':reference.locator}


def number(value):
    if type(value) not in (int, float, str):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 10**15 else None
    except (ValueError, OverflowError):
        return None


def write_hosted_reference(root, owner, provider, data):
    """Write only a vault reference; callers hold the preferences write lock."""
    from hosted_compute import HostedComputeService, PROVIDERS, _read, _write
    hosted = HostedComputeService(Path(root) / 'hosted-compute')
    folder = hosted._provider(owner, provider)
    path = folder / 'console-config.json'
    defaults = {'enabled': False, 'endpoint': PROVIDERS[provider], 'api_key': '',
                'recipes': [], 'credential_epoch': uuid.uuid4().hex}
    config = _read(path if path.exists() else folder / 'config.json', defaults)
    config.pop('api_key', None)
    config.pop('credential_ref', None)
    if data.get('credential_ref'):
        config['credential_ref'] = data['credential_ref']
    else:
        config['api_key'] = ''
    config['credential_epoch'] = str(data['credential_version'])
    _write(path, config)


class ProviderConsole:
    def __init__(self, root, runtime, user, colab, *, clock=time.time, probes=None, local_check=None):
        self.root, self.runtime, self.user, self.colab = Path(root), runtime, dict(user), colab
        self.owner = user.get('email')
        if not self.owner or user.get('disabled'):
            raise PermissionError('authentication required')
        self.actor = runtime.actor_context(self.owner, user.get('role', 'member'))
        self.prefs = ComputePreferences(self.root / 'compute-preferences.sqlite3')
        self.clock, self.probes = clock, probes
        self.local_check = local_check

    def _provider(self, provider):
        if provider not in PROVIDERS:
            raise ComputeRoutingError('不支援的平台', status=404)

    def local_allowed(self, db=None):
        if db is None:
            with self.prefs.db() as connection:
                return self.local_allowed(connection)
        row = db.execute('SELECT allowed FROM local_grants WHERE owner=?', (self.owner,)).fetchone()
        return bool(row[0]) if row else self.user.get('role') == 'admin'

    def _legacy_path(self, provider):
        return self.root / 'hosted-compute' / hashlib.sha256(self.owner.encode()).hexdigest() / provider / 'config.json'

    def _raw(self, provider, db):
        row = db.execute('SELECT value FROM connections WHERE owner=? AND provider=?', (self.owner, provider)).fetchone()
        if row:
            data = json.loads(row[0])
            if data.get('actor_id') != self.actor.user_id:
                raise PermissionError('connection owner mismatch')
            if provider == 'colab':
                connected = [a for a in self.colab.snapshot(self.owner)['accounts'] if a.get('authenticated') is True]
                data['credential_present'] = bool(connected)
                if connected and (connected[0].get('checked_at') or 0) > (data.get('checked_at') or 0):
                    data.update(connection_state='connected', checked_at=connected[0].get('checked_at'))
                elif not connected:
                    data['connection_state'] = 'not_configured'
            return data
        data = {'actor_id': self.actor.user_id, 'credential_present': False, 'credential_version': 0,
                'credential_saved_at': None, 'use_enabled': False, 'connection_state': 'not_configured',
                'checked_at': None, 'balances': [], 'revision': 0}
        if provider == 'local':
            data.update(use_enabled=self.local_allowed(db), connection_state='ready')
        elif provider == 'colab':
            accounts = self.colab.snapshot(self.owner)['accounts']
            connected = [a for a in accounts if a.get('authenticated') is True]
            data.update(credential_present=bool(connected), connection_state='connected' if connected else 'not_configured')
            if connected:
                data['checked_at'] = connected[0].get('checked_at')
                value = number(connected[0].get('balance_compute_units'))
                if value is not None:
                    data['balances'] = [{'unit': 'CU', 'value': value, 'observed_at': data['checked_at']}]
        elif provider == 'autoip':
            accounts = self.runtime.store.list_owned_provider_accounts(self.actor.user_id, provider_id='autoip')
            if len(accounts) == 1:
                account = accounts[0]
                data.update(account_id=account['account_id'], credential_present=account.get('authentication', {}).get('configured') is True,
                            use_enabled=account.get('enabled') is True)
                if data['credential_present']:
                    data['credential_ref'] = reference_dict(self.runtime.store.credential_reference_for_actor(self.actor.user_id, account['account_id']))
                    data['connection_state'] = 'untested'
            elif accounts:
                data['safe_reason'] = '請由管理員確認既有 AutoIP 帳號綁定'
                data['multiple_accounts'] = True
        elif self._legacy_path(provider).exists():
            from hosted_compute import _read
            config = _read(self._legacy_path(provider), {})
            present = bool(config.get('api_key') or config.get('credential_ref'))
            data.update(credential_present=present, use_enabled=config.get('enabled') is True,
                        connection_state='untested' if present else 'not_configured',
                        legacy_modified_at=self._legacy_path(provider).stat().st_mtime,
                        migration_required=bool(config.get('api_key')))
        return data

    def _put(self, db, provider, data):
        db.execute('INSERT INTO connections VALUES (?,?,?) ON CONFLICT(owner,provider) DO UPDATE SET value=excluded.value',
                   (self.owner, provider, json.dumps(data, allow_nan=False)))

    def _has_recipes(self, provider, data):
        from compute_parity.readiness import console_ready
        if provider in ('comfy_cloud','runninghub'):
            return console_ready(self,provider,data)
        if provider == 'autoip' and console_ready(self,provider,data): return True
        if provider != 'autoip': return True
        if not data.get('account_id'): return False
        snapshot = self.runtime.store.latest_accessible_capability_snapshot(self.actor.user_id, data['account_id'])
        return bool(snapshot and any(item.get('enabled') is True for item in snapshot.get('offerings', [])))

    def _card(self, provider, data, db):
        if provider == 'comfy_cloud' and data.get('credential_present'):
            from compute_parity.readiness import console_ready
            # Cloud has no standalone console test; authenticated private inventory
            # is the connection test. This view does not mutate preferences.
            if console_ready(self, provider, data, connection_only=True):
                data = {**data, 'connection_state': 'connected'}
        # Explicit public allowlist: never serialize references, account ids or vendor messages.
        card = {key: data.get(key) for key in ('credential_present', 'credential_version', 'credential_saved_at',
            'legacy_modified_at', 'use_enabled', 'connection_state', 'checked_at', 'balances', 'revision', 'account_type')}
        card['provider'] = provider
        if provider=='runninghub':card['key_type_label']={'NORMAL':'會員點數','SHARED':'企業錢包'}.get(data.get('account_type'),'尚未確認金鑰類型')
        reason = None
        if provider == 'local':
            if not self.local_allowed(db): reason = 'local_permission_required'
            elif data['connection_state'] == 'failed': reason = 'provider_unavailable'
        elif not data['credential_present']:
            reason = 'login_required' if provider == 'colab' else 'credential_required'
        elif data['connection_state'] != 'connected':
            reason = 'connection_test_required'
        # Hosted shared recipes deliberately remain out of this release.
        if provider in KEY_PROVIDERS and data['credential_present'] and not self._has_recipes(provider, data):
            reason = 'recipe_not_ready'
        if provider in ('runninghub','comfy_cloud'):
            from compute_parity.readiness import entries
            evidence=next((e for e in reversed(entries(self.root/'compute-parity')) if e.get('verification_note') and e.get('owner')==self.owner
                and e.get('lane')==provider and e.get('credential_version')==data.get('credential_version') and e.get('expires_at',0)>time.time()),{})
            card['verification_note']=evidence.get('verification_note','')
        if provider=='runninghub' and data.get('credential_present') and data.get('account_type')!='NORMAL':
            reason='rh_membership_required' if data.get('account_type')=='SHARED' else 'rh_key_type_unknown'
        card['readiness'] = 'ready' if reason is None else 'unavailable' 
        card['can_enable'] = reason is None
        card['disabled_code'] = reason
        card['disabled_reason'] = REASONS.get(reason, '')
        if reason in ('rh_membership_required','rh_key_type_unknown'):
            from compute_parity.runninghub_native import SHARED_REASON,UNKNOWN_REASON
            card['disabled_reason']=SHARED_REASON if reason=='rh_membership_required' else UNKNOWN_REASON
        card['safe_reason'] = data.get('safe_reason', '')
        card['status_message'] = PREPARING if reason == 'recipe_not_ready' else (
            '已選用，暫時不可用' if data['use_enabled'] and reason else
            {'connected':'已連線', 'ready':'本機使用權已開放', 'untested':'金鑰已儲存，請測試連線',
             'not_configured':'尚未登入' if provider == 'colab' else '未設定', 'failed':'連線未完成'}.get(data['connection_state'], '尚未確認'))
        if card.get('verification_note'):card['status_message'] += ' · ' + card['verification_note']
        test_reason = None
        if provider == 'local' and not self.local_allowed(db): test_reason = 'local_permission_required'
        elif provider == 'comfy_cloud': test_reason = 'test_unavailable'
        elif provider != 'local' and not data['credential_present']: test_reason = 'login_required' if provider == 'colab' else 'credential_required'
        elif data.get('migration_required'): test_reason = 'migration_required'
        card.update(test_available=test_reason is None, test_disabled_reason=REASONS.get(test_reason, ''))
        return card

    def snapshot(self, db=None):
        if db is None:
            with self.prefs.db() as connection:
                return self.snapshot(connection)
        cards = [self._card(p, self._raw(p, db), db) for p in PROVIDERS]
        prefs = self.prefs.get(self.owner, db=db, local_allowed=self.local_allowed(db))
        enabled = [c['provider'] for c in cards if c['use_enabled']]
        prefs['auto_order'] = [p for p in prefs['auto_order'] if p in enabled] + [p for p in enabled if p not in prefs['auto_order']]
        return {'owner': self.owner, 'connections': cards, 'preferences': prefs}

    def gate(self, provider, owner=None):
        if owner is not None and owner != self.owner:
            raise PermissionError('connection owner mismatch')
        self._provider(provider)
        with self.prefs.db() as db:
            card = self._card(provider, self._raw(provider, db), db)
        return card['disabled_code'] or (None if card['use_enabled'] else 'use_disabled')

    def save_preferences(self, body, feature_ids):
        def enabled(db):
            return [c['provider'] for c in self.snapshot(db)['connections'] if c['use_enabled']]
        def available(db):
            return [c['provider'] for c in self.snapshot(db)['connections'] if c['use_enabled'] and c['can_enable']]
        return self.prefs.save(self.owner, body, feature_ids, available=available, enabled=enabled, local_allowed=self.local_allowed())

    def toggle(self, provider, body):
        self._provider(provider)
        strict(body, ('use_enabled','revision'))
        if type(body['use_enabled']) is not bool: raise ComputeRoutingError('使用狀態必須是布林值')
        with self.prefs.db(write=True) as db:
            data = self._raw(provider, db)
            self._revision(data, body)
            if body['use_enabled'] and not self._card(provider, data, db)['can_enable']:
                fail(self._card(provider, data, db)['disabled_code'])
            if provider == 'autoip' and body['use_enabled']:
                self.runtime.store.set_owned_provider_account_enabled(self.actor.user_id, data['account_id'], True)
            data.update(use_enabled=body['use_enabled'], revision=data['revision']+1)
            self._put(db, provider, data)
            prefs = self.prefs.get(self.owner, db=db, local_allowed=self.local_allowed(db))
            enabled = [p for p in PROVIDERS if self._raw(p, db)['use_enabled']]
            prefs['auto_order'] = [p for p in prefs['auto_order'] if p in enabled] + [p for p in enabled if p not in prefs['auto_order']]
            if not body['use_enabled'] and prefs['default_placement'] == provider:
                prefs['default_placement'] = 'auto' if enabled else None
            prefs['revision'] += 1
            self.prefs.put(db, self.owner, prefs)
            return self.snapshot(db)

    @staticmethod
    def _revision(data, body):
        if type(body.get('revision')) is not int or body['revision'] != data['revision']:
            fail('revision_conflict')

    def active(self, provider):
        import sqlite3
        routing = self.root / 'compute-routing.sqlite3'
        if routing.exists():
            with sqlite3.connect(f'file:{routing}?mode=ro', uri=True) as db:
                if any(r[0] not in TERMINAL for r in db.execute('SELECT state FROM compute_requests WHERE owner=? AND lane=?', (self.owner,provider))): return True
        if provider in ('comfy_cloud','runninghub'):
            from hosted_compute import _read
            folder = self._legacy_path(provider).parent
            for path in folder.glob('jobs/*/job.json'):
                if _read(path, {}).get('state') not in TERMINAL: return True
        if provider == 'autoip':
            # Includes legacy fabric tasks which predate the unified router.
            with self.runtime.store._read_connection() as db:
                account_ids = [a['account_id'] for a in self.runtime.store.list_owned_provider_accounts(self.actor.user_id, provider_id='autoip')]
                for account in account_ids:
                    if db.execute("SELECT 1 FROM submission_outbox WHERE account_id=? AND state NOT IN ('done','abandoned') LIMIT 1", (account,)).fetchone(): return True
                    if db.execute("SELECT 1 FROM external_tasks WHERE account_id=? AND state NOT IN ('completed','failed','cancelled','abandoned') LIMIT 1", (account,)).fetchone(): return True
        return False

    def write_credential(self, provider, body):
        if provider not in KEY_PROVIDERS: raise ComputeRoutingError('這個平台不接受 API 金鑰')
        strict(body, ('credential','revision','operation_id'))
        try:
            operation = str(uuid.UUID(body['operation_id']))
        except (ValueError, TypeError, AttributeError):
            raise ComputeRoutingError('操作識別碼不正確') from None
        with self.prefs.db(write=True) as db:
            data = self._raw(provider, db)
            if data.get('operation_id') == operation:
                return self.snapshot(db)
            self._revision(data, body)
            if data.get('migration_required'): fail('migration_required')
            if data.get('multiple_accounts'): fail('connection_unavailable')
            if self.active(provider): fail('connection_in_use')
            vault = ProviderCredentialVault(self.runtime.config.credential_root)
            account = data.get('account_id') or 'console-' + self.actor.user_id.replace(':','-')
            version = data['credential_version'] + 1
            # New immutable slot; a crash leaves an unreachable secret, never a half-rotated live key.
            slot = 'api-key-' + uuid.uuid4().hex
            from provider_fabric.credentials import _normalize_secret, _zero
            valid = _normalize_secret(provider, 'api-key', body['credential'])
            _zero(valid)
            result = vault.provision(provider, account, body['credential'], credential_id=slot)
            if provider == 'autoip':
                from provider_fabric.autoip import AUTOIP_API_BASE
                self.runtime.store.register_owned_provider_account(self.actor.user_id, account, 'autoip', '我的 AutoIP', result.reference,
                    api_base=AUTOIP_API_BASE, enabled=False)
            data.pop('account_type',None)
            data.update(account_id=account, credential_ref=reference_dict(result.reference), credential_present=True,
                        credential_version=version, credential_saved_at=self.clock(), connection_state='untested',
                        checked_at=None, balances=[], safe_reason='', test_started_at=-1000, revision=data['revision']+1, operation_id=operation)
            self._put(db, provider, data)
            self._hosted_reference(provider, data)
            return self.snapshot(db)

    def _hosted_reference(self, provider, data):
        if provider not in ('comfy_cloud','runninghub'): return
        write_hosted_reference(self.root, self.owner, provider, data)

    def delete_credential(self, provider, body):
        if provider not in KEY_PROVIDERS: raise ComputeRoutingError('這個平台不使用 API 金鑰')
        strict(body, ('revision',))
        with self.prefs.db() as db:
            data = self._raw(provider, db)
            self._revision(data, body)
        result = self.toggle(provider, {'revision':body['revision'], 'use_enabled':False})
        if self.active(provider):
            result['notice'] = REASONS['connection_in_use']
            return result
        with self.prefs.db(write=True) as db:
            data = self._raw(provider, db)
            if data['revision'] != body['revision']+1: fail('revision_conflict')
            if data.get('migration_required'): fail('migration_required')
            if provider == 'autoip' and data.get('account_id'):
                self.runtime.store.set_owned_provider_account_enabled(self.actor.user_id, data['account_id'], False)
            reference = data.get('credential_ref')
            if reference:
                vault = ProviderCredentialVault(self.runtime.config.credential_root)
                path = Path(reference['locator'])
                if path.parent != vault.root / provider / data['account_id']: raise PermissionError('invalid credential reference')
                vault.delete(provider, data['account_id'], credential_id=path.stem, confirm=True)
            data.update(credential_present=False, connection_state='not_configured', balances=[], checked_at=None,
                        credential_version=data['credential_version']+1, revision=data['revision']+1)
            data.pop('account_type',None)
            data.pop('credential_ref',None)
            data.pop('operation_id',None)
            self._put(db, provider, data)
            self._hosted_reference(provider, data)
            return self.snapshot(db)

    def test(self, provider, body):
        self._provider(provider)
        strict(body, ('revision',))
        with self.prefs.db(write=True) as db:
            data = self._raw(provider, db)
            self._revision(data, body)
            card = self._card(provider, data, db)
            if not card['test_available']:
                raise ComputeRoutingError(card['test_disabled_reason'], code='test_unavailable', status=409)
            if self.clock()-data.get('test_started_at', -1000) < 30:
                raise ComputeRoutingError('請稍候再測試連線', code='test_rate_limited', status=429)
            ticket = uuid.uuid4().hex
            data.update(test_started_at=self.clock(), test_ticket=ticket)
            self._put(db, provider, data)
            version = data['credential_version']
        try:
            if self.probes is not None:
                result = self.probes[provider](data)
            else:
                result = self._probe(provider, data)
            state = 'connected' if result.get('connected') is True else 'failed'
            code = result.get('code') if result.get('code') in ('invalid_credential','provider_unavailable') else 'provider_unavailable'
            balances = []
            for balance in result.get('balances', [])[:8]:
                value = number(balance.get('value'))
                if balance.get('unit') in ('CU','credits','POINT','coins') and value is not None:
                    balances.append({'unit':balance['unit'], 'value':value, 'observed_at':self.clock()})
        except Exception:
            state, code, balances, result = 'failed', 'provider_unavailable', [], {}
        with self.prefs.db(write=True) as db:
            fresh = self._raw(provider, db)
            if fresh['credential_version'] != version or fresh.get('test_ticket') != ticket:
                fail('revision_conflict')
            fresh.update(connection_state=state, checked_at=self.clock(), balances=balances,
                         safe_reason='' if state == 'connected' else REASONS[code], revision=fresh['revision']+1)
            fresh.pop('account_type',None)
            if result.get('account_type') in ('NORMAL','SHARED'):
                fresh['account_type'] = result['account_type']
            if provider == 'autoip' and state == 'connected' and fresh['use_enabled']:
                self.runtime.store.set_owned_provider_account_enabled(self.actor.user_id, fresh['account_id'], True)
            self._put(db, provider, fresh)
            return self.snapshot(db)

    def _probe(self, provider, data):
        from provider_connection_probe import probe_key
        if provider == 'local':
            return {'connected':bool(self.local_check and self.local_check())}
        if provider == 'colab':
            accounts = self.colab.snapshot(self.owner)['accounts']
            account = next((a for a in accounts if a.get('authenticated')), None)
            if not account: return {'connected':False, 'code':'invalid_credential'}
            result = self.colab.account_action(self.owner, account['id'], 'refresh', {})
            return {'connected':result.get('authenticated') is True,
                    'balances':[{'unit':'CU','value':result.get('balance_compute_units')}]}
        return probe_key(provider, SecretReference.from_mapping(data['credential_ref']))

    def grant(self, owner, allowed):
        if self.user.get('role') != 'admin': raise PermissionError('admin required')
        if type(allowed) is not bool: raise ComputeRoutingError('使用權必須是布林值')
        with self.prefs.db(write=True) as db:
            db.execute('INSERT INTO local_grants VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET allowed=excluded.allowed', (owner,int(allowed)))

    def admin_snapshot(self, users):
        if self.user.get('role') != 'admin': raise PermissionError('admin required')
        result = []
        for owner,user in users.items():
            if user.get('disabled'): continue
            console = ProviderConsole(self.root, self.runtime, {**user,'email':owner}, self.colab)
            snapshot = console.snapshot()
            result.append({'email':owner, 'local_allowed':console.local_allowed(), 'connections':[
                {key:card.get(key) for key in ('provider','credential_present','use_enabled','checked_at')} for card in snapshot['connections']]})
        return {'users':result}
