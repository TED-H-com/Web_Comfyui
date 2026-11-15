import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'webui'))
from provider_console import ProviderConsole, PREPARING
from provider_fabric.store import ProviderFabricStore
from provider_fabric.runtime import actor_context_from_castle, load_or_create_identity_key
from provider_fabric.config import SecretValue, SecretReference
from provider_fabric.credentials import ProviderCredentialVault
from colab_service import ColabService
from compute_preferences import ComputePreferences
from compute_routing import ComputeRouter, ComputeAdapter, ComputeRoutingError
from provider_console_migration import migrate, rollback, inventory

SECRET=('test-' + 'key-not-' + 'secret-0001')
AUTOIP='aip_sy' + 'ntheti' + 'c_only' + '_do_no' + 't_use_' + '123456' + '789'

@pytest.fixture
def env(tmp_path):
    root=tmp_path/'state'; root.mkdir(mode=0o700)
    key=load_or_create_identity_key(root/'actor-identity.key')
    runtime=SimpleNamespace(config=SimpleNamespace(credential_root=root/'provider-secrets'),store=ProviderFabricStore(root/'provider-fabric.sqlite3'),
        actor_context=lambda email,role:actor_context_from_castle(email,role,identity_key=key))
    class Bridge:
        installed=True
        def __call__(self,op,path,**kwargs):
            if op=='account_status': return {'authenticated':True,'balance_compute_units':25}
            if op=='oauth_begin': return {'flow_id':'synthetic-flow','authorization_url':'https://accounts.google.com/o/oauth2/auth?state=synthetic'}
            if op=='oauth_finish': return {'authenticated':True}
            raise AssertionError('unexpected bridge operation')
    colab=ColabService(root/'colab',bridge=Bridge())
    def console(owner='other@example.invalid',role='member',**kwargs):
        return ProviderConsole(root,runtime,{'email':owner,'role':role},colab,**kwargs)
    return SimpleNamespace(root=root,runtime=runtime,colab=colab,console=console)

def card(c,p): return next(x for x in c.snapshot()['connections'] if x['provider']==p)
def write(c,p='runninghub',secret=SECRET,operation=None):
    return c.write_credential(p,{'credential':secret,'revision':card(c,p)['revision'],'operation_id':operation or str(uuid.uuid4())})
def probe(c,p='runninghub'):
    return c.test(p,{'revision':card(c,p)['revision']})
def toggle(c,p,enabled): return c.toggle(p,{'use_enabled':enabled,'revision':card(c,p)['revision']})

def test_write_only_isolated_and_never_echoed(env,capsys):
    c=env.console(); before=c.snapshot(); assert not list(env.root.glob('hosted-compute/*'))
    operation=str(uuid.uuid4()); result=write(c,operation=operation)
    assert SECRET not in json.dumps(result)
    assert 'credential_ref' not in json.dumps(result)
    assert card(c,'runninghub')['disabled_code']=='rh_key_type_unknown'
    assert not card(c,'runninghub')['use_enabled']
    assert not card(env.console('bob@example.test'),'runninghub')['credential_present']
    assert not card(env.console('admin@example.test','admin'),'runninghub')['credential_present']
    with pytest.raises(PermissionError): c.gate('runninghub','bob@example.test')
    with pytest.raises(ComputeRoutingError): c.write_credential('runninghub',{'credential':SECRET,'owner':'bob@example.test','revision':1,'operation_id':operation})
    # Replay of a successful write is idempotent and cannot mutate another secret.
    assert write(c,secret=('test-' + 'key-not-' + 'secret-0001'),operation=operation)['connections']==result['connections']
    assert SECRET.encode() not in (env.root/'compute-preferences.sqlite3').read_bytes()
    assert SECRET not in capsys.readouterr().out
    assert before['preferences']['default_placement'] is None

def test_vault_permissions_and_exact_secret(env):
    c=env.console(); write(c)
    files=list((env.root/'provider-secrets').rglob('*.secret')); assert len(files)==1
    assert files[0].read_text()==SECRET
    assert files[0].stat().st_mode&0o777==0o600
    for directory in (files[0].parent,files[0].parent.parent,env.root/'provider-secrets'):
        assert directory.stat().st_mode&0o777==0o700
    with pytest.raises(ValueError): write(c,secret=' '+SECRET)
    assert card(c,'runninghub')['credential_version']==1
    assert files[0].read_text()==SECRET

@pytest.mark.parametrize('unsafe',['symlink','hardlink','permissions'])
def test_vault_rejects_unsafe_files(env,tmp_path,unsafe):
    c=env.console();write(c); path=next((env.root/'provider-secrets').rglob('*.secret'))
    if unsafe=='symlink':
        target=tmp_path/'target'; path.rename(target); path.symlink_to(target)
    elif unsafe=='hardlink': os.link(path,tmp_path/'linked')
    else: path.chmod(0o644)
    with pytest.raises(PermissionError): SecretReference(kind='file',locator=str(path)).resolve()

@pytest.mark.parametrize('provider,reason',[('local','local_permission_required'),('colab','login_required'),('autoip','credential_required'),('runninghub','credential_required'),('comfy_cloud','credential_required')])
def test_disabled_reasons(env,provider,reason):
    c=env.console(); assert card(c,provider)['disabled_code']==reason
    with pytest.raises(ComputeRoutingError):toggle(c,provider,True)
    toggle(c,provider,False)  # Turning off is always allowed.

def test_identity_balance_and_no_recipe_success(env):
    c=env.console(probes={'runninghub':lambda _: {'connected':True,'account_type':'SHARED','balances':[{'unit':'POINT','value':1000},{'unit':'coins','value':0}]}})
    write(c);probe(c)
    value=card(c,'runninghub'); assert value['connection_state']=='connected'
    assert value['balances'][0]['value']==1000 and value['balances'][1]['value']==0
    assert value['disabled_code']=='rh_membership_required'
    with pytest.raises(ComputeRoutingError): toggle(c,'runninghub',True)
    with pytest.raises(ComputeRoutingError) as error: probe(c)
    assert error.value.status==429

def test_unknown_balance_and_malicious_upstream(env,capsys):
    c=env.console(probes={'runninghub':lambda _: {'connected':True,'email':SECRET,'account_type':SECRET,'balances':[{'unit':SECRET,'value':1},{'unit':'POINT','value':SECRET}]}})
    write(c);result=probe(c)
    assert SECRET not in json.dumps(result) and card(c,'runninghub')['balances']==[]
    def explode(_): raise RuntimeError(SECRET)
    c=env.console('b@example.test',probes={'runninghub':explode});write(c);result=probe(c)
    assert SECRET not in json.dumps(result)
    assert card(c,'runninghub')['safe_reason']=='平台暫時無回應，請稍後再試'
    assert SECRET not in capsys.readouterr().err

def test_stale_test_cannot_overwrite_rotation(env):
    started,finish=threading.Event(),threading.Event()
    def delayed(_): started.set(); assert finish.wait(5); return {'connected':True}
    c=env.console(probes={'runninghub':delayed});write(c);errors=[]
    def run():
        try: probe(c)
        except ComputeRoutingError as e: errors.append(e.code)
    thread=threading.Thread(target=run);thread.start();assert started.wait(5)
    write(c,secret=SECRET+'new');finish.set();thread.join(5)
    assert errors==['revision_conflict']
    assert card(c,'runninghub')['credential_version']==2
    assert card(c,'runninghub')['connection_state']=='untested'

def test_toggle_default_and_order_atomic(env,monkeypatch):
    monkeypatch.setattr(env.runtime.store,"latest_accessible_capability_snapshot",lambda owner,account:{"offerings":[{"enabled":True}]})
    c=env.console('admin@example.test','admin',probes={'autoip':lambda _: {'connected':True}})
    write(c,'autoip',AUTOIP);probe(c,'autoip');toggle(c,'autoip',True)
    prefs=c.snapshot()['preferences']; assert prefs['auto_order']==['local','autoip']
    saved=c.save_preferences({'default_placement':'autoip','feature_placements':{'video':'autoip'},'revision':prefs['revision']}, {'video'})
    result=toggle(c,'autoip',False)
    assert result['preferences']['default_placement']=='auto'
    assert result['preferences']['auto_order']==['local']
    assert result['preferences']['feature_placements']=={'video':'autoip'}
    result=toggle(c,'local',False);assert result['preferences']['default_placement']=='auto'
    # Closing the actual default with no other enabled lanes yields unset.
    toggle(c,'local',True);prefs=c.snapshot()['preferences']
    c.save_preferences({'default_placement':'local','feature_placements':{},'revision':prefs['revision']},{})
    assert toggle(c,'local',False)['preferences']['default_placement'] is None
    with pytest.raises(ComputeRoutingError): c.save_preferences({'default_placement':'autoip','feature_placements':{},'revision':saved['revision']},{})

def test_legacy_load_migration_only_explicit_order_changes_policy(env):
    c=env.console('admin@example.test','admin')
    with c.prefs.db(write=True) as db:c.prefs.put(db,c.owner,{'default_placement':'auto','feature_placements':{'video':'local'}})
    prefs=c.snapshot()['preferences'];assert prefs['auto_policy']=='legacy_load'
    c.save_preferences({'default_placement':'auto','feature_placements':{'video':'local'},'revision':0},{'video'})
    assert c.snapshot()['preferences']['auto_policy']=='legacy_load'
    c.save_preferences({'default_placement':'auto','feature_placements':{},'auto_order':['local'],'revision':1},{})
    assert c.snapshot()['preferences']['auto_policy']=='user_order'
    for order in (['local','local'],['runninghub'],['bad']):
        with pytest.raises(ComputeRoutingError): c.save_preferences({'default_placement':'auto','feature_placements':{},'auto_order':order,'revision':2},{})
    assert c.snapshot()['preferences']['revision']==2

def test_router_policy_and_replay(env):
    from compute_routing import ComputeRejected
    policy={'auto_policy':'legacy_load','auto_order':['local','autoip']};calls=[]
    def adapter(lane,depth):return ComputeAdapter(lambda *args:{'eligible':True,'queue_depth':depth},lambda *args:calls.append(lane) or {'state':'queued','downstream_id':'task-1'})
    r=ComputeRouter(env.root/'routing-test.sqlite3',{'local':adapter('local',10),'autoip':adapter('autoip',0)},policy=lambda owner:policy)
    assert r.preview('a',{},'auto')['selected_lane']=='autoip'
    policy['auto_policy']='user_order';assert r.preview('a',{},'auto')['selected_lane']=='local'
    request=str(uuid.uuid4());r.submit('a',request,{},'auto');policy['auto_order']=['autoip'];r.submit('a',request,{},'auto');assert calls==['local']
    with pytest.raises(ComputeRoutingError):r.status('b',request)
    r.admission=lambda *args: (_ for _ in ()).throw(ComputeRejected('使用已關閉',code='use_disabled'))
    assert r.submit('a',str(uuid.uuid4()),{},'auto')['state']=='rejected';assert calls==['local']

def legacy(env,owner='owner@example.invalid'):
    folder=env.root/'hosted-compute'/hashlib.sha256(owner.encode()).hexdigest()/'runninghub';folder.mkdir(parents=True,mode=0o700)
    for parent in (folder.parent,folder.parent.parent):parent.chmod(0o700)
    from hosted_compute import _write
    _write(folder/'config.json',{'api_key':SECRET,'enabled':False,'endpoint':'https://www.runninghub.ai','recipes':[],'credential_epoch':'keep-this-epoch'})
    return folder/'config.json'

def test_migration_idempotency_and_rollback(env):
    path=legacy(env); c=env.console('owner@example.invalid')
    with c.prefs.db(write=True) as db:c.prefs.put(db,c.owner,{'default_placement':'auto','feature_placements':{'video':'local'}})
    args=(env.root,env.runtime.config.credential_root,env.root/'actor-identity.key',['owner@example.invalid'])
    original=path.read_bytes();backup=env.root/'backup'
    result=migrate(*args,backup);assert result['migrated']==1
    assert path.read_bytes()==original
    overlay=path.with_name('console-config.json')
    assert SECRET not in overlay.read_text();assert not json.loads(overlay.read_text())['enabled']
    assert json.loads(path.read_text())['credential_epoch']=='keep-this-epoch'
    assert c.snapshot()['preferences']['auto_policy']=='legacy_load'
    assert card(c,'runninghub')['legacy_modified_at'] and card(c,'runninghub')['credential_saved_at'] is None
    assert migrate(*args,env.root/'unused-backup')['state']=='unchanged'
    from hosted_compute import HostedComputeService
    hosted=HostedComputeService(env.root/'hosted-compute');assert hosted._config(path.parent)['api_key']==SECRET
    assert SECRET not in json.dumps(hosted.public_config('owner@example.invalid'))
    rollback(backup);assert path.read_bytes()==original
    assert not (env.root/'provider-secrets').exists()

def test_migration_failure_and_unknown_owner(env):
    path=legacy(env);original=path.read_bytes()
    with pytest.raises(ValueError):migrate(env.root,env.runtime.config.credential_root,env.root/'actor-identity.key',['wrong@example.test'],env.root/'unknown')
    def fault(_):raise OSError('synthetic disk failure')
    with pytest.raises(RuntimeError):migrate(env.root,env.runtime.config.credential_root,env.root/'actor-identity.key',['owner@example.invalid'],env.root/'backup',fault=fault)
    assert path.read_bytes()==original
    assert not (env.root/'provider-secrets').exists()

def test_active_jobs_preserve_credentials_and_disable_new_work(env):
    c=env.console();write(c)
    r=ComputeRouter(env.root/'compute-routing.sqlite3',{'runninghub':ComputeAdapter(lambda *args:{'eligible':True},lambda *args:{'state':'running','downstream_id':'task-1'})})
    request=str(uuid.uuid4());r.submit(c.owner,request,{},'runninghub')
    with pytest.raises(ComputeRoutingError):write(c,secret=SECRET+'new')
    result=c.delete_credential('runninghub',{'revision':card(c,'runninghub')['revision']})
    assert '仍需此連線' in result['notice']
    assert card(c,'runninghub')['credential_present'] and not card(c,'runninghub')['use_enabled']
    assert next((env.root/'provider-secrets').rglob('*.secret')).read_text()==SECRET

def test_colab_owner_and_admin_projection(env):
    a=env.colab.create_account('other@example.invalid','我的 Google 帳號')
    with pytest.raises(ValueError):env.colab.account_action('bob@example.test',a['id'],'refresh',{})
    env.colab.account_action('other@example.invalid',a['id'],'refresh',{})
    assert card(env.console(),'colab')['credential_present']
    assert not card(env.console('bob@example.test'),'colab')['credential_present']
    admin=env.console('owner@example.invalid','admin');write(env.console())
    users={'other@example.invalid':{'role':'member'},'bob@example.test':{'role':'member'}}
    result=admin.admin_snapshot(users)
    assert 'balances' not in json.dumps(result) and SECRET not in json.dumps(result)
    admin.grant('other@example.invalid',True);assert env.console().local_allowed()
    admin.grant('other@example.invalid',False);assert env.console().gate('local')=='local_permission_required'
    with pytest.raises(PermissionError):env.console().grant('bob@example.test',True)
    with pytest.raises(PermissionError):env.console().admin_snapshot(users)

def test_probe_only_fixed_account_endpoints(env):
    from provider_connection_probe import probe_key
    from hosted_compute_http import Response
    c=env.console();write(c)
    with c.prefs.db() as db:ref=SecretReference.from_mapping(c._raw('runninghub',db)['credential_ref'])
    class Transport:
        def __init__(self):self.calls=[]
        def request(self,method,url,**kwargs):
            self.calls.append((method,url,kwargs))
            return Response(200,{},json.dumps({'code':0,'data':{'remainPoint':12,'remainCoins':'0','apiType':'SHARED'}}).encode())
    transport=Transport();result=probe_key('runninghub',ref,transport=transport)
    assert result['connected'];assert [(m,u) for m,u,_ in transport.calls]==[('POST','https://www.runninghub.ai/uc/openapi/accountStatus')]
    assert transport.calls[0][2]['timeout']==10
    with pytest.raises(ValueError):probe_key('comfy_cloud',ref,transport=transport)
    assert len(transport.calls)==1


def test_autoip_without_owner_recipes_stays_disabled(env):
    c=env.console(probes={'autoip':lambda _: {'connected':True}})
    write(c,'autoip',AUTOIP);probe(c,'autoip')
    assert card(c,'autoip')['status_message']==PREPARING
    with pytest.raises(ComputeRoutingError):toggle(c,'autoip',True)


def test_r26_dry_run_read_only_and_apply_reads_latest(env):
    from provider_console_migration import dry_run
    from hosted_compute import _read, _write
    path=legacy(env);root=env.root
    args=(root,env.runtime.config.credential_root,root/'actor-identity.key',['owner@example.invalid'])
    def state():return {str(p.relative_to(root)):(p.stat().st_mtime_ns,p.stat().st_mode,p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    before=state();report=dry_run(*args);assert state()==before
    assert all(type(v) is bool for row in report.values() for v in row.values())
    assert report['runninghub']['has_key'] and report['runninghub']['recipes_count_is_zero']
    fresh=SECRET+'-entered-after-dry-run';value=_read(path);value['api_key']=fresh;_write(path,value)
    original=path.read_bytes()
    migrate(*args,root/'backup')
    assert path.read_bytes()==original
    overlay=_read(path.with_name('console-config.json'))
    assert SecretReference.from_mapping(overlay['credential_ref']).resolve().reveal()==fresh
    assert migrate(*args,root/'unused')['state']=='unchanged'


def test_r26_migration_reuses_autoip_and_colab_owner_bindings(env):
    root=env.root;owner='other@example.invalid';actor=env.runtime.actor_context(owner,'member')
    vault=ProviderCredentialVault(env.runtime.config.credential_root)
    ref=vault.provision('autoip','existing-account',AUTOIP).reference
    env.runtime.store.register_owned_provider_account(actor.user_id,'existing-account','autoip','Existing',ref,enabled=False)
    account=env.colab.create_account(owner,'Existing Google');env.colab.account_action(owner,account['id'],'refresh',{})
    # Opaque bridge token remains byte-identical; migration must not open/copy it.
    token=env.colab.account_dir(account['id'])/'token.json';token.write_text('synthetic-bridge-token');token.chmod(0o600)
    before={p:p.read_bytes() for p in (root/'provider-secrets').rglob('*.secret')}
    colab_before={p:p.read_bytes() for p in (root/'colab').rglob('*') if p.is_file()}
    args=(root,env.runtime.config.credential_root,root/'actor-identity.key',[owner])
    result=migrate(*args,root/'backup');assert result['migrated']==2
    assert {p:p.read_bytes() for p in before}==before
    assert {p:p.read_bytes() for p in colab_before}==colab_before
    assert card(env.console(owner),'autoip')['credential_present']
    assert not card(env.console('bob@example.test'),'autoip')['credential_present']
    assert card(env.console(owner),'colab')['credential_present']
    assert migrate(*args,root/'unused')['state']=='unchanged'
    rollback(root/'backup');assert {p:p.read_bytes() for p in colab_before}==colab_before


def test_r26_overlay_rotation_and_deletion_never_rewrite_rollback_original(env):
    from hosted_compute import HostedComputeService
    path=legacy(env);original=path.read_bytes();owner='owner@example.invalid'
    migrate(env.root,env.runtime.config.credential_root,env.root/'actor-identity.key',[owner],env.root/'backup')
    c=env.console(owner);write(c,secret=SECRET+'rotated')
    service=HostedComputeService(env.root/'hosted-compute')
    assert service._config(path.parent)['api_key']==SECRET+'rotated'
    assert path.read_bytes()==original
    c.delete_credential('runninghub',{'revision':card(c,'runninghub')['revision']})
    assert not service._config(path.parent)['api_key']
    assert path.read_bytes()==original


def test_r26_read_only_inventory_refuses_uncheckpointed_wal(env):
    from provider_console_migration import read_rows
    path=env.root/'probe.sqlite3'
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE test (value TEXT)');db.commit()
    try:
        with pytest.raises(ValueError,match='pending journal'):read_rows(path,'SELECT * FROM test')
    finally:db.close()


def test_cloud_authenticated_inventory_opens_card_without_preferences_write(env):
    from compute_parity.readiness import save
    import time
    c=env.console();write(c,p='comfy_cloud')
    catalog=env.root/'compute-parity/bundles-r29';catalog.mkdir(parents=True)
    (catalog/'catalog.json').write_text(json.dumps({'cases':[{'owner':c.owner,'ready':True,'bundle_sha256':'fake-bundle'}]}))
    with c.prefs.db() as db:before=c._raw('comfy_cloud',db)
    evidence={'owner':c.owner,'lane':'comfy_cloud','credential_version':before['credential_version'],
        'bundle_sha256':'fake-bundle','observed_at':time.time(),'expires_at':time.time()+300,
        'connection_verified':True,'dependencies_ready':True,'hash_mode':'name_size_provisional',
        'lora_sha_verified':True,'alias_map_version':'20261111-r29b','alias_evidence_verified':True}
    save(env.root/'compute-parity',evidence)
    assert card(c,'comfy_cloud')['can_enable']
    with c.prefs.db() as db:assert c._raw('comfy_cloud',db)==before
    for change in ({'connection_verified':False},{'credential_version':999},{'owner':'other'},
                   {'expires_at':0},{'alias_evidence_verified':False}):
        path=env.root/'compute-parity/readiness.json'
        path.write_text(json.dumps({'entries':[{**evidence,**change}]}))
        assert not card(c,'comfy_cloud')['can_enable']

@pytest.mark.parametrize('kind,label,reason',[('NORMAL','會員點數',None),('SHARED','企業錢包','rh_membership_required'),('OTHER','尚未確認金鑰類型','rh_key_type_unknown')])
def test_r34_detected_type_is_safe_and_cleared_on_rotation(env,kind,label,reason):
 c=env.console();write(c)
 c._probe=lambda *a:{'connected':True,'account_type':kind,'balances':[{'unit':'POINT','value':1000}]}
 probe(c);row=card(c,'runninghub')
 assert row['key_type_label']==label
 if reason:assert row['disabled_code']==reason and not row['can_enable']
 write(c,secret=('test-' + 'key-not-' + 'secret-0002'))
 assert card(c,'runninghub')['key_type_label']=='尚未確認金鑰類型' and card(c,'runninghub')['account_type'] is None
