"""Independent safe-expectation adaptation of the original 30 probes. Snapshot only.
Schema/media are mocked where snapshot omits catalog; lifecycle code is real.
"""
import ast
import importlib.util
import json
from pathlib import Path
import socket
import sys
import uuid
import pytest

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CANDIDATE/'webui'), str(CANDIDATE)]
from colab_service import ColabService, BridgeError, encoded
from compute_parity.colab import ColabNative
from compute_parity.journal import Journal

class Crash(BaseException): pass

def uid(): return str(uuid.uuid4())

class Bridge:
    installed = True
    def __init__(self):
        self.balance = 1000
        self.calls = []
        self.uncertain = False
        self.found = False
        self.stop_cost = 0
    def __call__(self, op, account_dir, **kw):
        self.calls.append((op, kw))
        if op == 'account_identity': return {'bound':True, 'subject_key':'a'*64}
        if op == 'account_status': return {'authenticated':True, 'balance_compute_units':self.balance}
        if op == 'runtime_allocate':
            if self.uncertain: raise BridgeError('allocation_uncertain', True)
            return {'found':True, 'runtime_id':kw['allocation_id'], 'accelerator':kw['gpu']}
        if op == 'runtime_lookup': return {'found':self.found, 'runtime_id':kw['allocation_id'], 'accelerator':kw['gpu']}
        if op == 'runtime_stop':
            self.balance -= self.stop_cost
            self.stop_cost = 0
            return {'stopped':True}
        if op == 'remote_exec':
            compile(kw['code'], 'remote', 'exec')
            result = {'phase':'running'} if 'bootstrap-status.json' in kw['code'] else {'heartbeat':True, 'clean':True,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2}}
            if "'armed':True" in kw['code']:
                import re
                result={'armed':True,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2},'session_id':re.search(r"'session_id':'([^']+)'",kw['code']).group(1)}
            return {'stdout':'WEB_COMFYUI_JSON='+json.dumps(result)}
        raise AssertionError(op)

@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(ColabService, 'start', lambda self:None)
    def forbidden(*args, **kw): raise AssertionError('network prohibited')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    b = Bridge(); clock = [1000000.0]
    s = ColabService(tmp_path/'colab', b, lambda:clock[0])
    account = s.create_account('owner','test')['id']
    s.account_action('owner', account, 'refresh', {})
    state = s.sessions.open('owner', account, 15)
    return s,b,clock,account,state

def allocated(rig, phase='ready'):
    s,b,clock,account,state = rig
    s.sessions.tick_all()
    return s.sessions.save(state['id'], phase=phase, watchdog_armed=True,
        gpu_name='NVIDIA A100', idle_since=clock[0], uploads=[{}], launch_at=clock[0])

def job(rig, sid, phase='queued', **kw):
    s,b,clock,account,state=rig
    j={'id':uid(), 'account_id':account, 'session_job':True, 'native_parity':True,
       'phase':phase, 'gpu':'A100', 'units_observed':0, 'released':False,
       'created_at':clock[0], 'deadline_epoch':clock[0]+3600, 'max_minutes':60,
       'artifacts':[], 'uploads':[], 'quote_amount':15, **kw}
    if sid: j['session_id']=sid
    with s.db() as db: db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(j['id'],'owner',account,'identity',encoded(j)))
    return j

def test_recovered_assignment_gets_an_extra_six_hours(rig):
    s,b,clock,account,state=rig
    b.uncertain=True
    s.sessions.tick_all()
    original=s.sessions.get(state['id'])
    assert original['phase']=='uncertain' and not original.get('watchdog_armed')
    clock[0]+=7*3600; b.found=True
    s=ColabService(s.root,b,s.clock)
    s.sessions.tick_all()
    recovered=s.sessions.get(state['id'])
    assert recovered['phase']=='closed' and recovered['released']
    assert recovered['deadline_epoch']==original['allocation_at']+6*3600
    assert any(op=='runtime_stop' for op,_ in b.calls)

def test_dead_bootstrap_keeps_idle_runtime_for_thirty_minutes(rig):
    s,b,clock,account,state=rig
    allocated(rig,'bootstrap')
    # A killed bootstrap leaves the actual worker's persisted phase=running.
    for _ in range(30):
        clock[0]+=60
        s=ColabService(s.root,b,s.clock)
        s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['phase']=='closed' and not final.get('active_job')
    assert any(op=='runtime_stop' for op,_ in b.calls)

def test_close_between_snapshot_and_allocate_is_lost(rig):
    s,b,clock,account,state=rig
    stale=s.sessions.get(state['id'])
    s.sessions.close('owner')
    assert s.sessions.get(state['id'])['close_requested']
    s.sessions.tick(stale)
    final=s.sessions.get(state['id'])
    assert final['phase']=='closed' and final.get('close_requested')
    assert not any(op=='runtime_allocate' for op,_ in b.calls)

def test_job_unbound_at_crash_opens_second_session(rig,monkeypatch):
    from contextlib import contextmanager
    import scripts.colab_session_inputs as schema
    import compute_parity.private_assets as assets
    s,b,clock,account,state=rig
    allocated(rig)
    # Snapshot contains no catalog/materials: mock validation only; execute real
    # admission, session binding, SQL commit and restart code with the original fault.
    monkeypatch.setattr(schema,'validate_native_manifest',lambda m,*a,**kw:m)
    monkeypatch.setattr(assets,'freeze_private_models',lambda *a:[])
    body={'request_id':uid(),'account_id':account,'gpu':'A100','max_minutes':60,'quote_amount':15,'files':[]}
    spec={'type':'video','engine':'castle_native_p1','bundle':{'models':[]}}
    real_db=s.db
    class DB:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            result=self.db.execute(sql,args)
            if sql.startswith('INSERT INTO jobs'):raise Crash()
            return result
    @contextmanager
    def crash_db():
        with real_db() as db:yield DB(db)
    with monkeypatch.context() as m:
        m.setattr(s,'db',crash_db)
        with pytest.raises(Crash):s._submit('owner',body,_native=spec)
    with s.db() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    s._submit('owner',body,_native=spec)
    j=s.job('owner',body['request_id'])
    assert j['session_id']==state['id']
    clock[0]+=301
    s=ColabService(s.root,b,s.clock);s.sessions.tick_all();s.tick(j)
    assert s.sessions.get(state['id'])['released']
    assert s.job('owner',j['id'])['phase']=='failed'
    # Also exercise a persisted pre-fix unbound row, not only rollback.
    old=job(rig,None);s.tick(old)
    assert s.job('owner',old['id'])['phase']=='failed'
    clock[0]+=300;s.sessions.tick_all()
    assert len([op for op,_ in b.calls if op=='runtime_allocate'])==1


def test_release_crash_makes_journal_and_session_disagree(rig,monkeypatch):
    from contextlib import contextmanager
    s,b,clock,account,state=rig
    state=allocated(rig)
    j=job(rig,state['id'],'cleanup_pending',runtime_id=state['runtime_id'],outcome='failed')
    state=s.sessions.save(state['id'],active_job=j['id'],phase='closing')
    journal=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    journal.reserve('owner',j['id'],'colab',account,'dance.anime.chatgpt','identity',{'unit':'CU','amount':15})
    b.balance-=2;b.stop_cost=1
    real_db=s.db;injected=[]
    class DB:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            result=self.db.execute(sql,args)
            if sql.startswith('UPDATE work_sessions SET state=') and json.loads(args[0])['phase']=='closed':
                injected.append(True);raise Crash()
            return result
    @contextmanager
    def crash_db():
        with real_db() as db:yield DB(db)
    with monkeypatch.context() as m:
        m.setattr(s,'db',crash_db)
        with pytest.raises(Crash):s.sessions.release(state)
    assert injected and not s.job('owner',j['id'])['released']
    assert s.sessions.get(state['id'])['phase']=='released_unsettled'
    ColabNative(s,journal).poll('owner',j['id'])
    assert journal.get('owner',j['id']).get('actual_cost') is None
    s=ColabService(s.root,b,s.clock);s.sessions.tick_all()
    ColabNative(s,journal).poll('owner',j['id'])
    ledger=journal.ledger('owner')
    assert s.job('owner',j['id'])['units_observed']==ledger['colab']['actual']==2
    assert ledger['colab_sessions']['sessions'][0]['job_units']==2
    assert ledger['colab_sessions']['actual']==1
    assert ledger['colab_sessions']['sessions'][0]['settled']


def test_release_omits_usage_during_stop(rig):
    s,b,clock,account,state=rig
    state=allocated(rig)
    b.balance-=2; b.stop_cost=1
    s.sessions.release(s.sessions.save(state['id'],phase='closing'))
    line=s.sessions.snapshot('owner')['session_ledger'][0]
    assert line['total_observed']==3 and line['settled']
    assert 1000-b.balance==3

def test_uncertain_session_is_reported_as_running_job(rig):
    s,b,clock,account,state=rig
    j=job(rig,state['id'])
    journal=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    journal.reserve('owner',j['id'],'colab',account,'dance.anime.chatgpt','identity',{'unit':'CU','amount':15})
    b.uncertain=True; s.sessions.tick_all()
    assert s.sessions.get(state['id'])['phase']=='uncertain'
    receipt=ColabNative(s,journal).poll('owner',j['id'])
    assert receipt['state']=='submission_uncertain'
    assert receipt['diagnostics']['stage']=='provisioning'
    assert receipt['diagnostics']['error_code']=='allocation_uncertain'

def test_close_late_assignment_and_absence_grace_control(rig):
    s,b,clock,account,state=rig
    b.uncertain=True; s.sessions.tick_all()
    allocation=s.sessions.get(state['id'])['allocation_id']
    s.sessions.close('owner'); clock[0]+=149; s.sessions.tick_all()
    assert not s.sessions.get(state['id'])['released']
    b.found=True; s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert [kw['runtime_id'] for op,kw in b.calls if op=='runtime_stop']==[allocation]
    assert len([op for op,_ in b.calls if op=='runtime_allocate'])==1

def test_voice_consumer_still_uses_removed_shared_input_path(tmp_path):
    # Load only the pure arguments function: no voice runtime import or data.
    p=CANDIDATE/'scripts/colab_remote/native_voxcpm_worker.py'
    tree=ast.parse(p.read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='arguments')
    scope={}; exec(compile(ast.Module(body=[fn],type_ignores=[]),str(p),'exec'),scope)
    export={'settings':{'cfg_value':2,'inference_timesteps':10,'normalize':True,'denoise':True},
            'text':'', 'reference_name':'reference.wav','reference_text':'',
            'speaker_threshold':0.6,'speaker_max_attempts':1}
    # Read required option keys without any private voice data.
    for n in ast.walk(fn):
        if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Name) and n.value.id=='settings' and isinstance(n.slice,ast.Constant):
            export['settings'].setdefault(n.slice.value,1)
    view_tree=ast.parse((CANDIDATE/'scripts/colab_session_worker.py').read_text())
    view_fn=next(n for n in view_tree.body if isinstance(n,ast.FunctionDef) and n.name=='cache_view')
    view_scope={'Path':Path};exec(compile(ast.Module(body=[view_fn],type_ignores=[]),'view','exec'),view_scope)
    (tmp_path/'job').mkdir();(tmp_path/'session/cache').mkdir(parents=True)
    cache=view_scope['cache_view'](tmp_path/'job',tmp_path/'session')
    (tmp_path/'job/comfy-input/reference.wav').write_bytes(b'offline')
    args=scope['arguments'](cache,export,tmp_path/'out.wav')
    reference=Path(args[args.index('--reference-audio')+1])
    assert reference.resolve()==tmp_path/'job/comfy-input/reference.wav'
    assert reference.is_file()

def test_success_marker_survives_worker_death_before_private_cleanup(tmp_path,monkeypatch,rig):
    import os
    sys.path.insert(0,str(CANDIDATE/'scripts/colab_remote'))
    spec=importlib.util.spec_from_file_location('review_worker',CANDIDATE/'scripts/colab_session_worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    monkeypatch.setattr(worker,'comfy_identity',lambda *a,**kw:{'verified':True})
    source=tmp_path/'source.mp4';source.write_bytes(b'offline media mock')
    bundle={'inputs':[{'slot':'video','name':'input/1.mp4','kind':'video','local_path':str(source)}],
            'models':[],'bundle_sha256':'a'*64,'output_contract':{'kind':'video'}}
    monkeypatch.setattr(worker,'compile_request',lambda *a,**kw:{'json':{'prompt':{}}})
    root=tmp_path/'job';root.mkdir()
    session=tmp_path/'session';session.mkdir();cache=session/'cache';cache.mkdir()
    # Sentinel left by an earlier voice operation; never read private voice data.
    private_marker=cache/'voice-export.json';private_marker.write_text('{}')
    manifest={'jobs':[{'bundle':bundle}], 'input_files':[{'path':i['name']} for i in bundle['inputs']], 'job_id':uid(),'deadline_epoch':10**12}
    (root/'manifest.json').write_text(json.dumps(manifest))
    for item in bundle['inputs']:
        dest=root/item['name'];dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(Path(item['local_path']).read_bytes())
    monkeypatch.setattr(worker,'validate_native_manifest',lambda m,r:m)
    monkeypatch.setattr(worker,'prepared',lambda b,s:(cache/'ComfyUI','mock-python'))
    monkeypatch.setattr(worker,'dependency_probe',lambda *a:{'ready':True})
    monkeypatch.setattr(worker,'output_check',lambda *a:None)
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**k:'NVIDIA A100, 40536')
    RealPath=Path
    monkeypatch.setattr(worker,'Path',lambda p:tmp_path/'alias' if str(p)=='/content/castle-parity-input' else RealPath(p))
    terminated=tmp_path/'child-terminated'
    class Process:
        def poll(self):return None
        def terminate(self):terminated.touch()
        def wait(self,**kw):return 0
    monkeypatch.setattr(worker.subprocess,'Popen',lambda *a,**kw:Process())
    def request(method,path,body=None):
        if path=='/object_info':return {}
        if path=='/prompt':
            out=root/'comfy-output';out.mkdir()
            src=next(i for i in bundle['inputs'] if i['kind']=='video')
            (out/'result.mp4').write_bytes(Path(src['local_path']).read_bytes())
            return {'prompt_id':'accepted'}
        return {'accepted':{'status':{'completed':True},'outputs':{'1':{'videos':[{'type':'output','filename':'result.mp4'}]}}}}
    monkeypatch.setattr(worker,'local_request',request)
    real_atomic=worker.atomic
    def kill_after_completed(path,value):
        real_atomic(path,value)
        if path==root/'status.json' and value.get('phase')=='completed':
            os._exit(0)  # Same cleanup semantics as SIGKILL at this exact boundary.
    monkeypatch.setattr(worker,'atomic',kill_after_completed)
    pid=os.fork()
    if pid==0:
        try: worker.run_native(root,worker.file_hash(root/'manifest.json'),session)
        except BaseException: os._exit(2)
        os._exit(3)
    _,status=os.waitpid(pid,0)
    assert os.waitstatus_to_exitcode(status)==0
    assert json.loads((root/'status.json').read_text())['phase']=='completed'
    assert (root/'artifacts.json').exists()
    assert private_marker.exists() and not terminated.exists()
    assert (tmp_path/'alias').exists()
    assert not (root/'reusable-clean.json').exists()
    # Drive the real controller from the killed worker's exact persisted records.
    s,b,clock,account,state=rig;state=allocated(rig)
    j=job(rig,state['id'],'running',id=manifest['job_id'],runtime_id=state['runtime_id'],
          manifest_sha256=json.loads((root/'status.json').read_text())['manifest_sha256'])
    s.sessions.save(state['id'],active_job=j['id'])
    calls=[]
    def remote(state,code):
        calls.append(code)
        if "'status':read('status.json')" in code:
            return {'status':json.loads((root/'status.json').read_text()),
                    'artifacts':json.loads((root/'artifacts.json').read_text()),'worker_dead':True}
        if 'reusable-clean.json' in code:
            import io,contextlib
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(code.replace('/content/web-comfyui/'+j['id'],str(root)),{})
            return json.loads(output.getvalue().split('WEB_COMFYUI_JSON=')[1])
        raise AssertionError('Unexpected remote operation')
    monkeypatch.setattr(s,'_exec',remote);monkeypatch.setattr(s,'_download',lambda *a:[])
    s.tick(j)
    assert s.sessions.get(state['id'])['phase']=='closed'
    assert not s.job('owner',j['id']).get('session_runtime_retained')
    assert any('reusable-clean.json' in c for c in calls)

def test_retry_rounds_and_lane_cap_survive_restarts(rig,monkeypatch):
    s,b,clock,account,state=rig
    original=s._call
    def unavailable(state,op,**kw):
        if op=='runtime_allocate':
            b.calls.append((op,kw));raise BridgeError('allocation_unavailable')
        return original(state,op,**kw)
    for index in range(12):
        s=ColabService(s.root,b,s.clock)
        monkeypatch.setattr(s,'_call',unavailable)
        for _ in range(3):s.sessions.tick_all()
        assert s.sessions.get(state['id'])['attempts']==index+1
        clock[0]+=599;s.sessions.tick_all()
        assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==3*(index+1)
        clock[0]+=1
    s.sessions.tick_all();s.sessions.tick_all()
    assert s.sessions.get(state['id'])['reason']=='attempt_cap'
    # Synthetic durable records represent another owner's lane usage.
    with s.db() as db:
        for _ in range(12): db.execute('INSERT INTO session_attempts VALUES (?,?,?)',(uid(),'other-owner-session',clock[0]-601))
    next_session=s.sessions.open('owner',account,15)
    s=ColabService(s.root,b,s.clock);s.sessions.tick_all();s.sessions.tick_all()
    assert s.sessions.get(next_session['id'])['reason']=='rolling_24h_cap'
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==36

@pytest.mark.parametrize('phase',['arming','uploading','bootstrap_launch','bootstrap','ready','closing'])
def test_restart_stale_runtime_states_release_without_reallocation(rig,phase):
    s,b,clock,account,state=rig
    allocated(rig,phase)
    clock[0]+=301
    s=ColabService(s.root,b,s.clock);s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1

@pytest.mark.parametrize('phase',['arming','uploading','bootstrap_launch','bootstrap'])
def test_close_during_bootstrap_releases(rig,phase):
    s,b,clock,account,state=rig
    allocated(rig,phase)
    s.sessions.close('owner');s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']

def test_meter_transaction_rolls_back_both_rows_on_crash(rig,monkeypatch):
    from contextlib import contextmanager
    s,b,clock,account,state=rig
    state=allocated(rig)
    j=job(rig,state['id'],'running',runtime_id=state['runtime_id'])
    state=s.sessions.save(state['id'],active_job=j['id'])
    real_db=s.db
    class DB:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            if sql.startswith('UPDATE work_sessions SET state='): raise Crash()
            return self.db.execute(sql,args)
    @contextmanager
    def crash_db():
        with real_db() as db:yield DB(db)
    b.balance-=2
    with monkeypatch.context() as m:
        m.setattr(s,'db',crash_db)
        with pytest.raises(Crash):s.sessions.observe(state)
    assert s.job('owner',j['id'])['units_observed']==0
    assert s.sessions.get(state['id'])['units_observed']==0
    s.sessions.observe(s.sessions.get(state['id']))
    assert s.job('owner',j['id'])['units_observed']==2
    assert s.sessions.get(state['id'])['job_units']==2

def test_session_cu_cap_and_other_owner_controls(rig):
    s,b,clock,account,state=rig
    allocated(rig)
    assert s.sessions.snapshot('other-owner')['session_ledger']==[]
    s.sessions.close('other-owner')
    assert not s.sessions.get(state['id']).get('close_requested')
    b.balance-=120;clock[0]+=60
    s=ColabService(s.root,b,s.clock);s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert s.sessions.get(state['id'])['reason']=='cu_cap'

@pytest.mark.parametrize('raw,accepted',[
    ('NVIDIA L4, 24000',False),('Tesla T4, 16000',False),
    ('NVIDIA L4, 48000',False),('NVIDIA A100, 20000',False),
    ('NVIDIA A100, 40536',True),('NVIDIA H100, 81559',True),
    ('NVIDIA RTX PRO 6000 Blackwell, 97887',True)])
def test_gpu_runtime_controls(raw,accepted):
    p=CANDIDATE/'scripts/colab_session_worker.py'
    tree=ast.parse(p.read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify_gpu')
    scope={};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(p),'exec'),scope)
    if accepted: assert scope['verify_gpu'](raw)[1]>=39000
    else:
        with pytest.raises(RuntimeError):scope['verify_gpu'](raw)

