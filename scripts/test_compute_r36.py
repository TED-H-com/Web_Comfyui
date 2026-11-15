"""P1m diagnostic regressions and r36 boundaries; disk fixtures, mocked bridge."""
import importlib.util,json,os,sqlite3,sys,urllib.error,subprocess,uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_compute_r35 import load_worker,rig,native,ready,OWNER
from scripts.colab_session_diagnostics import plan,plan_id,sanitize_diagnostics,telemetry,redacted_tail
from compute_parity.provisioning import states,provisioning_only

GPU='NVIDIA RTX PRO 6000 Blackwell Server Edition'

@pytest.mark.parametrize('http',[False,True])
def test_live_bootstrap_discards_distinct_failure_diagnostics(tmp_path,monkeypatch,http):
    # Port of both P1m observed-loss probes, flipped to preserved evidence.
    worker=load_worker(monkeypatch)
    (tmp_path/'bootstrap.json').write_text(json.dumps([{'models':[]}]))
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**kw:GPU+', 97887')
    def fail(*a,**kw):
        kw['stage']('model_download')
        if http:raise urllib.error.HTTPError('https://invalid.example/?secret=hidden',503,'private',{},None)
        raise RuntimeError('private')
    monkeypatch.setattr(worker,'bootstrap',fail)
    monkeypatch.setattr(worker,'prepare_supplement',lambda *a:None)
    monkeypatch.setattr(worker,'node_gate',lambda *a:None)  # Test post-gate download exception.
    assert worker.bootstrap_session(tmp_path)==1
    status=json.loads((tmp_path/'bootstrap-status.json').read_text())
    assert status['phase']=='failed' and status['gpu_name']==GPU
    assert status['diagnostics']=={'stage':'session_bootstrap','substage':'model_download','error_type':'HTTPError' if http else 'RuntimeError','error_code':'stage_failed',**({'http_status':503} if http else {})}
    assert 'private' not in json.dumps(list(status.values())) and 'invalid.example' not in json.dumps(status)
    assert status['disk_free_gib']>0 and status['python_version']

@pytest.mark.parametrize('phase,alive,age,reason',[('failed',False,0,'bootstrap_failed'),('missing',False,0,'bootstrap_process_lost'),('running',True,121,'bootstrap_stalled'),('ready',False,0,None)])
def test_live_controller_collapses_reported_failure_and_missing_status(rig,monkeypatch,phase,alive,age,reason):
    s,b,clock,account,bundle=rig
    state=s.sessions.open(OWNER,account,15)
    state=s.sessions.save(state['id'],phase='bootstrap',uploads=[{}],launch_at=clock[0])
    result={'phase':phase,'alive':alive,'progress_at':clock[0]-age,'identity':{'pid':1,'start_ticks':'1'},'gpu':'G4','gpu_name':GPU,'vram_mib':97887}
    if phase=='failed':result['diagnostics']={'stage':'session_bootstrap','substage':'runtime_lock','error_code':'stage_failed','error_type':'CalledProcessError','exit_code':1}
    monkeypatch.setattr(s,'_exec',lambda st,code:result if 'bootstrap-status.json' in code else {'tail':'[redacted]\nframe native_runner.py:98\nhttps://secret.invalid/?token=private'})
    if reason=='bootstrap_stalled':
        from colab_service import BridgeError
        before=state['last_tick']
        for streak in (1,2):
            with pytest.raises(BridgeError) as caught:s.sessions.bootstrap(s.sessions.get(state['id']))
            s.sessions.record_fault(state,caught.value)
            current=s.sessions.get(state['id'])
            assert current['last_tick']==before and current['fault_streak']==streak
            assert current['private_rpc_error']=={'bootstrap_probe':{'type':'progress_stale'}}
        assert current['phase']=='closing'
        return
    s.sessions.bootstrap(state);current=s.sessions.get(state['id'])
    if reason:
        assert current['reason']==reason and current['phase']=='closing'
        assert current['bootstrap_diagnostics']['stage']=='session_bootstrap'
        path=s.root/'sessions'/state['id']/'bootstrap.stderr.txt'
        assert path.stat().st_mode&0o777==0o600
        assert path.read_text()=='[redacted]\nframe native_runner.py:98'
        assert 'tail' not in json.dumps(current)
    else:assert current['phase']=='ready'
    assert current['gpu_name']==GPU

@pytest.mark.parametrize('error',[subprocess.CalledProcessError(3,['secret']),ImportError('private'),PermissionError('private')])
def test_patch_keeps_safe_diagnostics_and_verified_gpu(tmp_path,monkeypatch,error):
    worker=load_worker(monkeypatch);(tmp_path/'bootstrap.json').write_text('[{"models":[]}]')
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**kw:GPU+', 97887')
    def fail(*a,**kw):raise error
    monkeypatch.setattr(worker,'bootstrap',fail)
    monkeypatch.setattr(worker,'prepare_supplement',lambda *a:None)
    monkeypatch.setattr(worker,'node_gate',lambda *a:None)  # Test post-gate download exception.
    assert worker.bootstrap_session(tmp_path)==1
    status=json.loads((tmp_path/'bootstrap-status.json').read_text())
    assert status['gpu_name']==GPU and status['diagnostics']['error_type']==type(error).__name__
    assert status['diagnostics'].get('exit_code')==(3 if isinstance(error,subprocess.CalledProcessError) else None)

@pytest.mark.parametrize('bad',[{},[],None,0,True,'https://private.invalid'])
def test_untrusted_diagnostics_and_telemetry_are_bounded(bad):
    value={k:bad for k in ['stage','substage','error_type','error_code','gpu','gpu_name','vram_mib','disk_free_gib','python_version','exit_code','http_status']}
    assert sanitize_diagnostics(value)==({'exit_code':0} if type(bad) is int and bad==0 else {})
    assert telemetry(value)==({'disk_free_gib':0} if type(bad) is int and bad==0 else {})
    from compute_routing import _public_receipt
    receipt=_public_receipt({'state':'failed','requested_gpu':bad,'assigned_gpu':bad})
    assert 'requested_gpu' not in receipt and 'assigned_gpu' not in receipt

def test_stderr_tail_drops_all_message_text_and_paths(tmp_path):
    p=tmp_path/'stderr';p.write_text('x'*20000+'\nFile "/home/private/native_runner.py", line 98\nValueError: https://example.invalid/?token=hidden\nfoo@example.invalid /home/private/file api_key=hidden\n')
    tail=redacted_tail(p)
    assert len(tail)<=4096 and 'frame native_runner.py:98' in tail
    assert all(secret not in tail for secret in ['https','hidden','@','/home/','api_key'])
    link=tmp_path/'link';link.symlink_to(p);assert redacted_tail(link)==''

@pytest.mark.parametrize('failure',['argv','import','lock'])
def test_shim_reports_before_native_import_or_lock(tmp_path,monkeypatch,failure):
    # Only tests override the hard-coded Colab path validation.
    import scripts.colab_session_bootstrap as shim
    root=tmp_path/'content/castle-sessions'/str(uuid.uuid4());root.mkdir(parents=True)
    monkeypatch.setattr(shim,'__file__',str(root/'session_bootstrap.py'))
    if failure=='argv':
        assert shim.main(['bad'])==1
    else:
        real=shim.Path
        class RemotePath(type(real())):
            def __str__(self):
                text=super().__str__()
                return '/content/castle-sessions' if text==str(root.parent) else text
        # Instead intercept the path boundary in the test copy, never product.
        source=Path(shim.__file__).parent  # no real remote directories
        code=Path(__file__).resolve().parent/'colab_session_bootstrap.py'
        local=tmp_path/'shim.py'
        local.write_text(code.read_text().replace("'/content/castle-sessions'",repr(str(root.parent))))
        spec=importlib.util.spec_from_file_location('test_shim',local);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        monkeypatch.setattr(m,'__file__',str(root/'session_bootstrap.py'))
        if failure=='import':monkeypatch.setitem(sys.modules,'session_worker',None)
        else:
            w=load_worker(monkeypatch)
            monkeypatch.setattr(w.fcntl,'flock',lambda *a:(_ for _ in ()).throw(PermissionError('private')))
            monkeypatch.setitem(sys.modules,'session_worker',w)
        assert m.main(['bootstrap',str(root)])==1
    status=json.loads((root/'bootstrap-status.json').read_text())
    assert status['phase']=='failed' and status['diagnostics']['substage']==failure


def test_worker_prepares_only_selected_plan_and_deduplicates(tmp_path,monkeypatch):
    worker=load_worker(monkeypatch);plans=[{'feature':'dance','models':[]},{'feature':'voice-speak','models':[]}]
    monkeypatch.setattr(worker,'comfy_identity',lambda *a,**kw:{'verified':True})
    (tmp_path/'bootstrap.json').write_text(json.dumps(plans+[plans[0]]));calls=[]
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**kw:GPU+', 97887')
    monkeypatch.setattr(worker,'bootstrap',lambda p,c,**kw:calls.append((p,c)))
    monkeypatch.setattr(worker,'verify_ready',lambda *a:None)  # Whole-bootstrap mock.
    for p in plans:
        ident=plan_id(p)
        assert worker.bootstrap_session(tmp_path,ident)==0
        assert worker.bootstrap_session(tmp_path,ident)==0
    assert [p['feature'] for p,c in calls]==['dance','voice-speak'] and calls[0][1]!=calls[1][1]


def test_preopen_is_lazy_then_admitted_job_plan_only(rig):
    s,b,clock,account,bundle=rig;state=ready(rig)
    assert state['prepared_plans']==[]
    assert not any(op=='remote_exec' and "'bootstrap'," in k['code'] for op,k in b.calls)
    job=native(rig)
    for _ in range(8):s.sessions.tick_all();clock[0]+=10
    state=s.sessions.get(state['id']);current=s.job(OWNER,job['id'])
    assert current['phase']=='preparing'
    assert state['prepared_plans']==[plan_id(plan(bundle))]
    assert len(state['plan_catalog'])==1


def test_failed_plan_only_fails_matching_jobs_and_reuses_other_plan(rig,monkeypatch):
    import copy
    from test_compute_parity_p1 import seal
    s,b,clock,account,bundle=rig
    other_bundle=copy.deepcopy(bundle)
    other_bundle['case_id']='dance.anime.claude'
    other_bundle['core']['commit']='c'*40
    seal(other_bundle)
    (s.root.parent/'compute-parity/bundles-r29'/('dance.anime.claude.json')).write_text(json.dumps(other_bundle))
    state=ready(rig)
    one=native(rig);first=one['bootstrap_plan_id']
    two=native((s,b,clock,account,other_bundle));other=two['bootstrap_plan_id']
    assert other!=first
    state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=first,launch_at=clock[0],assigned_gpu='G4')
    monkeypatch.setattr(s,'_exec',lambda st,code:{'clean':True})
    diagnostic={'stage':'session_bootstrap','substage':'node_checkout','error_type':'ValueError','error_code':'stage_failed'}
    s.sessions.fail_plan(state,diagnostic,'bootstrap_failed')
    assert s.job(OWNER,one['id'])['phase']=='failed' and s.job(OWNER,one['id'])['released']
    assert s.job(OWNER,one['id'])['assigned_gpu']=='G4'
    assert s.job(OWNER,two['id'])['phase']=='queued'
    state=s.sessions.get(state['id']);assert state['phase']=='ready' and first in state['failed_plans']
    s.sessions.attach(state,s.job(OWNER,two['id']))
    assert s.sessions.get(state['id'])['bootstrap_plan_id']==other
    assert not any(op=='runtime_stop' for op,k in b.calls)


def test_last_failed_plan_closes_and_cu_stays_on_session(rig,monkeypatch):
    s,b,clock,account,bundle=rig;state=ready(rig);one=native(rig)
    state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=one['bootstrap_plan_id'],launch_at=clock[0])
    monkeypatch.setattr(s,'_exec',lambda st,code:{'clean':True})
    s.sessions.fail_plan(state,{'stage':'session_bootstrap','error_code':'stage_failed'},'bootstrap_failed')
    assert s.sessions.get(state['id'])['phase']=='closing'
    b.balance-=.12;s.sessions.release(s.sessions.get(state['id']))
    state=s.sessions.get(state['id'])
    assert state['released'] and state['session_units']==pytest.approx(.12) and state['job_units']==0
    assert s.job(OWNER,one['id'])['units_observed']==0

@pytest.mark.parametrize('cause',['runtime','launch','cu','unknown','missing','wrong_owner','clean'])
def test_exemption_joins_session_by_owner_and_id(rig,cause):
    s,b,clock,account,bundle=rig;one=native(rig);sid=one['session_id']
    s.update(one['id'],phase='failed',released=True,units_observed=0)
    changes={'phase':'closed','released':True,'usage_unknown':False,'units_observed':0}
    if cause=='runtime':changes['runtime_id']='runtime'
    if cause=='launch':changes['launch_at']=clock[0]
    if cause=='cu':changes['units_observed']=.12
    if cause=='unknown':changes['usage_unknown']=True
    s.sessions.save(sid,**changes)
    if cause in {'missing','wrong_owner'}:
        with s.db() as db:
            if cause=='missing':db.execute('DELETE FROM work_sessions WHERE id=?',(sid,))
            else:db.execute('UPDATE work_sessions SET owner=? WHERE id=?',('different',sid))
    receipt={'lane':'colab','request_id':one['id'],'remote_id':one['id'],'account':account,'state':'failed','actual_cost':{'unit':'CU','amount':0}}
    assert provisioning_only(receipt,states(s.root/'state.sqlite3',OWNER)[one['id']]) is (cause=='clean')


def test_gpu_truth_and_bootstrap_diagnostic_on_receipt(rig):
    from compute_parity.colab import ColabNative
    from compute_parity.journal import Journal
    from compute_routing import _public_receipt
    s,b,clock,account,bundle=rig;ready(rig);one=native(rig)
    s.sessions.save(one['session_id'],phase='closing',assigned_gpu='G4',gpu='G4',gpu_name=None,bootstrap_diagnostics={'stage':'session_bootstrap','substage':'import','error_type':'ModuleNotFoundError','error_code':'stage_failed'})
    s.sessions.release(s.sessions.get(one['session_id']))
    journal=Journal(s.root.parent/'journal.sqlite3')
    journal.reserve(OWNER,one['id'],'colab',account,bundle['case_id'],'identity',{'unit':'CU','amount':15},None)
    result=ColabNative(s,journal)._record(OWNER,one['id'],s.public_job(s.job(OWNER,one['id'])))
    assert result['requested_gpu']=='A100' and result['assigned_gpu']=='G4' and result['gpu']=='G4' and result['gpu_name'] is None
    assert result['diagnostics']['stage']=='session_bootstrap'

@pytest.mark.parametrize('failure',['dead','stale','timeout'])
def test_plan_budget_and_unknown_cleanup_close_lease(rig,monkeypatch,failure):
    s,b,clock,account,bundle=rig;state=ready(rig);one=native(rig)
    state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=one['bootstrap_plan_id'],launch_at=clock[0]-(1801 if failure=='timeout' else 0))
    status={'phase':'running','alive':failure!='dead','progress_at':clock[0]-(121 if failure=='stale' else 0)}
    monkeypatch.setattr(s,'_exec',lambda st,code:status if 'bootstrap-status.json' in code else {'clean':False})
    if failure=='stale':
        from colab_service import BridgeError
        for _ in range(2):
            with pytest.raises(BridgeError) as caught:s.sessions.bootstrap(s.sessions.get(state['id']))
            s.sessions.record_fault(state,caught.value)
        final=s.sessions.get(state['id'])
        assert final['phase']=='closing' and final['last_tick']==state['last_tick']
        assert final['private_rpc_error']=={'bootstrap_probe':{'type':'progress_stale'}}
        assert not s.job(OWNER,one['id'])['released']
        return
    s.sessions.bootstrap(state)
    final=s.sessions.get(state['id']);assert final['phase']=='closing'
    assert final['bootstrap_diagnostics']['error_code']=={'dead':'bootstrap_process_lost','stale':'bootstrap_stalled','timeout':'bootstrap_timeout'}[failure]
    assert not s.job(OWNER,one['id'])['released']




def test_preopen_records_startup_telemetry_without_claiming_verified_gpu(rig,monkeypatch):
    s,b,clock,account,bundle=rig;state=s.sessions.open(OWNER,account,15)
    state=s.sessions.save(state['id'],runtime_id='fixture',deadline_epoch=clock[0]+21600)
    def remote(st,code):
        compile(code,'arm-probe','exec')
        assert code.index('while not alive()') < code.index("raw=subprocess.check_output(['nvidia-smi'")
        assert 'timeout=10' in code
        return {'armed':True,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2},'session_id':state['id'],'startup_telemetry':{'disk_free_gib':150.5,'python_version':'3.12.8','gpu_name':GPU,'vram_mib':97887,'raw':'secret'}}
    monkeypatch.setattr(s,'_exec',remote)
    result=s.sessions.arm(state)
    assert result['watchdog_armed'] and result['startup_telemetry']=={'disk_free_gib':150.5,'python_version':'3.12.8','gpu_name':GPU,'vram_mib':97887}
    assert result.get('gpu_name') is None


def test_queued_receipt_carries_current_session_bootstrap_stage(rig):
    from compute_parity.colab import ColabNative
    from compute_parity.journal import Journal
    s,b,clock,account,bundle=rig;one=native(rig)
    s.sessions.save(one['session_id'],phase='bootstrap',assigned_gpu='G4',bootstrap_diagnostics={'stage':'session_bootstrap','substage':'node_checkout'})
    journal=Journal(s.root.parent/'journal.sqlite3')
    journal.reserve(OWNER,one['id'],'colab',account,bundle['case_id'],'identity',{'unit':'CU','amount':15},None)
    result=ColabNative(s,journal)._record(OWNER,one['id'],s.public_job(one))
    assert result['diagnostics']=={'stage':'session_bootstrap','substage':'node_checkout'}
    assert result['requested_gpu']=='A100' and result['assigned_gpu']=='G4' and result['gpu_name'] is None

@pytest.mark.parametrize('late_lookup',[False,True])
def test_cancelled_late_assignment_keeps_assigned_gpu_without_upload(rig,monkeypatch,late_lookup):
    from colab_service import BridgeError
    s,b,clock,account,bundle=rig;one=native(rig);original=s._call
    def call(state,op,**kw):
        if op=='runtime_allocate':
            if late_lookup:raise BridgeError('allocation_uncertain',True)
            s.sessions.close(OWNER)
            return {'found':True,'runtime_id':kw['allocation_id'],'accelerator':'G4'}
        if op=='runtime_lookup':return {'found':True,'runtime_id':kw['allocation_id'],'accelerator':'G4'}
        return original(state,op,**kw)
    monkeypatch.setattr(s,'_call',call)
    s.sessions.tick_all()
    if late_lookup:s.sessions.close(OWNER);s.sessions.tick_all()
    state=s.sessions.get(one['session_id']);job=s.job(OWNER,one['id'])
    assert state['released'] and state['assigned_gpu']=='G4'
    assert job['released'] and job['requested_gpu']=='A100' and job['assigned_gpu']=='G4' and job['gpu']=='G4' and job.get('gpu_name') is None
    assert not any(op=='upload' for op,kw in b.calls)
    assert sum(op=='runtime_stop' for op,kw in b.calls)==1


def test_r35_historical_session_gpu_is_assignment_only_with_runtime(rig,monkeypatch):
    from compute_parity.colab import ColabNative
    from compute_parity.journal import Journal
    s,b,clock,account,bundle=rig;one=native(rig)
    legacy={'id':one['session_id'],'phase':'closed','gpu':'G4','runtime_id':'historical-runtime'}
    monkeypatch.setattr(s,'owner_session',lambda owner,sid:legacy if owner==OWNER else {})
    journal=Journal(s.root.parent/'journal.sqlite3')
    journal.reserve(OWNER,one['id'],'colab',account,bundle['case_id'],'identity',{'unit':'CU','amount':15},None)
    adapter=ColabNative(s,journal)
    result=adapter._record(OWNER,one['id'],s.public_job({**one,'phase':'failed','released':True,'units_observed':0}))
    assert result['assigned_gpu']=='G4' and result['gpu_name'] is None
    legacy.pop('runtime_id')
    result=adapter._record(OWNER,one['id'],s.public_job({**one,'phase':'failed','released':True,'units_observed':0}))
    assert result['assigned_gpu'] is None and result['gpu'] is None


def test_close_racing_plan_failure_keeps_cancelled_receipt_and_owner_settings(rig,monkeypatch):
    s,b,clock,account,bundle=rig;state=ready(rig);one=native(rig)
    stale=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=one['bootstrap_plan_id'],launch_at=clock[0])
    s.sessions.close(OWNER);s.sessions.settings(OWNER,{'idle_minutes':37})
    monkeypatch.setattr(s,'_exec',lambda st,code:{'clean':True})
    s.sessions.fail_plan(stale,{'stage':'session_bootstrap','error_code':'stage_failed'},'bootstrap_failed')
    current=s.sessions.get(state['id']);job=s.job(OWNER,one['id'])
    assert current['close_requested'] and current['settings']['idle_minutes']==37 and current['phase']=='closing'
    assert job['phase']=='cancelled' and job['released'] and job['cancel_requested']
