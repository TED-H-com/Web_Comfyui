"""Disk-only r35 sessions with a fixed dance bundle and materials.

No SDK instance, external HTTP, allocation, CUDA, or private voice data is used.
"""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import uuid
import pytest
from colab_service import ColabService, BridgeError, ColabError, encoded
from compute_parity.colab import submit_native, ColabNative
from compute_parity.journal import Journal
from compute_parity.runtime import Runtime
from compute_routing import _public_receipt

ROOT=Path(__file__).resolve().parents[1]
OWNER='owner'
def oid():return str(uuid.uuid4())

class FakeBridge:
    installed=True
    def __init__(self):
        self.calls=[];self.balance=1000;self.allocations=[];self.found=False;self.stopped=True;self.status={}
    def __call__(self,op,account_dir,**kw):
        self.calls.append((op,kw))
        if op=='account_identity':return {'bound':True,'subject_key':'a'*64}
        if op=='account_status':return {'authenticated':True,'balance_compute_units':self.balance,'active_assignments':0}
        if op=='runtime_allocate':
            result=self.allocations.pop(0) if self.allocations else {'found':True,'runtime_id':kw['allocation_id'],'accelerator':kw['gpu']}
            if isinstance(result,Exception):raise result
            return result
        if op=='runtime_lookup':return {'found':self.found,'runtime_id':kw['allocation_id'],'accelerator':kw['gpu']}
        if op=='runtime_stop':return {'stopped':self.stopped}
        if op=='upload':return {}
        if op=='remote_exec':
            code=kw['code']
            # Compile actual remote snippets; never execute notebook code locally.
            compile(code,'remote-code','exec')
            if "'armed':True" in code:
                import re
                sid=re.search(r"'session_id':'([^']+)'",code).group(1)
                value={'armed':True,'session_id':sid,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2}}
            elif 'archive_verified' in code:value={'archive_verified':True}
            elif 'bootstrap-status.json' in code:value={'phase':'ready','gpu':'A100','gpu_name':'NVIDIA A100-SXM4-40GB','vram_mib':40536}
            elif "'heartbeat':True" in code:value={'heartbeat':True,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2}}
            elif 'reusable-clean.json' in code:
                import re
                jid=re.search(r'/content/web-comfyui/([a-f0-9-]{36})',code).group(1)
                value={'clean':True,'job_id':jid,'manifest_sha256':self.service.job(OWNER,jid)['manifest_sha256']}
            elif "'status':read('status.json')" in code:value=self.status
            else:value={'launched':True}
            if 'r.update(heartbeat=True' in code and value.get('phase')=='ready':
                import ast
                expected=next(line.strip() for line in code.splitlines() if line.strip().startswith('expected=json.loads('))
                value.update(heartbeat=True,watchdog_identity=json.loads(ast.literal_eval(expected[len('expected=json.loads('):-1])))
                value.update(progress_at=self.service.clock(),heartbeat_cycle=ast.literal_eval(next(line.split('=',1)[1] for line in code.splitlines() if line.startswith('probe_cycle='))),heartbeat_at=float(next(line.strip().split('==')[1] for line in code.splitlines() if line.strip().startswith('assert float(lease_heartbeat.read_text())=='))))
            return {'stdout':'WEB_COMFYUI_JSON='+json.dumps(value)}
        raise AssertionError(op)

@pytest.fixture
def rig(tmp_path,monkeypatch):
    clock=[1000000.0];bridge=FakeBridge()
    monkeypatch.setattr(ColabService,'start',lambda self:None)
    service=ColabService(tmp_path/'colab',bridge,lambda:clock[0])
    bridge.service=service
    account=service.create_account(OWNER,'acct1')['id']
    service.account_action(OWNER,account,'refresh',{})
    from test_compute_parity_p1 import synthetic_material_bundle
    bundle = synthetic_material_bundle(tmp_path)
    return service,bridge,clock,account,bundle


def native(rig):
    s,b,clock,account,bundle=rig
    body={'request_id':oid(),'account_id':account,'gpu':'A100','max_minutes':60,'quote_amount':15,
          'files':[{'name':Path(i['name']).name,'data':base64.b64encode(Path(i['local_path']).read_bytes()).decode()} for i in bundle['inputs']]}
    result=submit_native(s,OWNER,body,bundle)
    return s.job(OWNER,result['id'])


def ready(rig):
    s,b,clock,account,bundle=rig
    state=s.sessions.open(OWNER,account,15)
    for _ in range(80):
        s.sessions.tick_all();state=s.sessions.get(state['id']);clock[0]+=10
        if state['phase']=='ready':return state
        assert state['phase']!='closed'
    raise AssertionError('not ready')


def attach_ready(rig,job):
    # r36 prepares the admitted plan lazily before attachment.
    s,b,clock,account,bundle=rig
    s.tick(job)
    for _ in range(8):
        s.sessions.tick_all();clock[0]+=1
        if s.job(OWNER,job['id'])['phase']=='preparing':return
    raise AssertionError('admitted plan did not become ready')


def test_open_bootstrap_once_reuse_two_jobs_and_idle_release(rig):
    s,b,clock,account,bundle=rig
    state=ready(rig)
    first=native(rig);second=native(rig)
    for job in (first,second):
        attach_ready(rig,job);job=s.job(OWNER,job['id'])
        assert job['runtime_id']==state['runtime_id'] and job['phase']=='preparing'
        assert not any(x['path'].startswith('runner/') for x in job['uploads'])
        assert json.loads((s.root/'jobs'/job['id']/'manifest.json').read_text())['job_id']==job['id']
        b.balance-=2
        job=s.update(job['id'],phase='cleanup_pending',outcome='completed')
        s._cleanup(job)
        assert s.job(OWNER,job['id'])['units_observed']==2
        assert s.job(OWNER,job['id'])['session_runtime_retained']
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1
    assert len([1 for op,k in b.calls if op=='remote_exec' and "'bootstrap'," in k['code']])==1
    # Heartbeats keep the idle lease alive until the selected idle deadline.
    for _ in range(21):clock[0]+=60;s.sessions.tick_all()
    final=s.sessions.get(state['id']);assert final['released'] and final['reason']=='idle_timeout'
    assert len([1 for op,_ in b.calls if op=='runtime_stop'])==1


def test_admitted_native_job_opens_session_and_launches_isolated_worker(rig):
    s,b,clock,account,bundle=rig;job=native(rig)
    s.tick(job)
    sid=s.job(OWNER,job['id'])['session_id']
    for _ in range(90):
        s.sessions.tick_all();s.tick(job);clock[0]+=10
        current=s.job(OWNER,job['id'])
        if current['phase']=='running':break
    assert current['phase']=='running'
    code=next(k['code'] for op,k in b.calls if op=='remote_exec' and "tmp=d/'launch.tmp'" in k['code'])
    assert 'session_worker.py' in code and "'job'" in code and sid in code and job['id'] in code
    assert 'os.link' in code  # Frozen runner bytes are verified for each job.
    remote_uploads=[k['remote_path'] for op,k in b.calls if op=='upload' and '/web-comfyui/' in k['remote_path']]
    assert remote_uploads and all('/'+job['id']+'/' in path for path in remote_uploads)
    assert not any('/runner/' in path for path in remote_uploads)


@pytest.mark.parametrize('trigger,reason',[('hard','watchdog_or_hard_cap'),('lost','watchdog_or_hard_cap'),('cu','cu_cap')])
def test_session_caps_and_watchdog_release_current_job(rig,trigger,reason):
    s,b,clock,account,bundle=rig;state=ready(rig);job=native(rig);attach_ready(rig,job)
    if trigger=='hard':s.sessions.save(state['id'],deadline_epoch=clock[0])
    elif trigger=='lost':clock[0]+=301
    else:b.balance-=120;clock[0]+=60
    s.sessions.tick_all();state=s.sessions.get(state['id'])
    assert state['released'] and state['reason']==reason
    assert s.job(OWNER,job['id'])['phase']=='failed'


def test_close_waits_for_current_and_cancels_queue(rig):
    s,b,clock,account,bundle=rig;state=ready(rig)
    job=native(rig);waiting=native(rig);attach_ready(rig,job);s.tick(waiting)
    s.sessions.close(OWNER);s.sessions.tick_all()
    assert not s.sessions.get(state['id'])['released']
    s._cleanup(s.update(job['id'],phase='cleanup_pending',outcome='completed'))
    s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert s.job(OWNER,waiting['id'])['phase']=='cancelled'
    assert s.public_job(s.job(OWNER,waiting['id']))['status_label']=='工作已取消'


@pytest.mark.parametrize('ambiguous',[False,True])
def test_gpu_fallback_order_and_original_id_lookup(rig,ambiguous):
    s,b,clock,account,bundle=rig
    b.allocations=[BridgeError('allocation_uncertain',True),BridgeError('allocation_unavailable'),{'found':True,'runtime_id':oid(),'accelerator':'G4'}] if ambiguous else [BridgeError('allocation_unavailable'),BridgeError('allocation_unavailable'),{'found':True,'runtime_id':oid(),'accelerator':'G4'}]
    state=s.sessions.open(OWNER,account,15)
    s.sessions.tick_all()
    if ambiguous:
        saved=s.sessions.get(state['id']);original=saved['allocation_id']
        s.sessions.tick_all();assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1
        clock[0]+=150;s.sessions.tick_all()
        assert all(k['allocation_id']==original for op,k in b.calls if op=='runtime_lookup')
    for _ in range(4):s.sessions.tick_all()
    assert [k['gpu'] for op,k in b.calls if op=='runtime_allocate']==['A100','H100','G4']
    assert s.sessions.get(state['id'])['gpu']=='G4'


@pytest.mark.parametrize('gpu',['L4','T4'])
def test_native_parity_rejects_small_gpu_before_allocation(rig,gpu):
    s,b,clock,account,bundle=rig
    from compute_parity.contracts import ParityError
    with pytest.raises(ParityError):submit_native(s,OWNER,{'gpu':gpu},bundle)
    assert not any(op=='runtime_allocate' for op,_ in b.calls)


def test_request_round_spacing_twelve_cap_and_zero_runtime_cost(rig):
    s,b,clock,account,bundle=rig
    b.allocations=[BridgeError('allocation_unavailable') for _ in range(36)]
    state=s.sessions.open(OWNER,account,15)
    for index in range(12):
        for _ in range(3):s.sessions.tick_all()
        saved=s.sessions.get(state['id']);assert saved['attempts']==index+1
        count=len([1 for op,_ in b.calls if op=='runtime_allocate'])
        clock[0]+=599;s.sessions.tick_all()
        assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==count
        clock[0]+=1
    s.sessions.tick_all();s.sessions.tick_all()
    final=s.sessions.get(state['id'])
    assert final['phase']=='closed' and final['reason']=='attempt_cap' and final['units_observed']==0
    with s.db() as db:dates=[r[0] for r in db.execute('SELECT at FROM session_attempts ORDER BY at')]
    assert len(dates)==12 and all(y-x>=600 for x,y in zip(dates,dates[1:]))


def test_twenty_four_rolling_lane_cap_and_expiry(rig):
    s,b,clock,account,bundle=rig;state=s.sessions.open(OWNER,account,15)
    with s.db() as db:
        for i in range(24):db.execute('INSERT INTO session_attempts VALUES (?,?,?)',(oid(),'other-owner-session',clock[0]-86400+100+i))
    s.sessions.tick_all();s.sessions.tick_all()
    assert s.sessions.get(state['id'])['reason']=='rolling_24h_cap'
    assert not any(op=='runtime_allocate' for op,_ in b.calls)
    clock[0]+=200;s.sessions.open(OWNER,account,15);s.sessions.tick_all()
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1


def test_cancel_acquisition_waits_for_uncertain_original_lease(rig):
    s,b,clock,account,bundle=rig;b.allocations=[BridgeError('allocation_uncertain',True)]
    job=native(rig);s.tick(job);state=s.sessions.rows(OWNER)[0];s.sessions.tick_all()
    s.sessions.close(OWNER);s.sessions.tick_all()
    assert not s.sessions.get(state['id'])['released']
    clock[0]+=150;s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert s.job(OWNER,job['id'])['phase']=='cancelled'
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1


def test_restart_reconciles_without_allocate_or_launch_replay(rig):
    s,b,clock,account,bundle=rig;b.allocations=[BridgeError('allocation_uncertain',True)]
    state=s.sessions.open(OWNER,account,15);s.sessions.tick_all();b.found=True
    restarted=ColabService(s.root,b,s.clock);restarted.sessions.tick_all()
    state=restarted.sessions.get(state['id'])
    assert state['runtime_id'] and state['phase']=='uploading' and state['watchdog_armed']
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1
    restarted.sessions.save(state['id'],phase='bootstrap_launch',uploads=[{}],launch_at=clock[0])
    restarted.sessions.tick_all()
    assert not any(op=='remote_exec' and "'bootstrap'," in k['code'] for op,k in b.calls)


def test_restart_after_heartbeat_gap_closes_no_reuse(rig):
    s,b,clock,account,bundle=rig;state=ready(rig);clock[0]+=301
    restarted=ColabService(s.root,b,s.clock);restarted.sessions.tick_all()
    assert restarted.sessions.get(state['id'])['released']
    assert len([1 for op,_ in b.calls if op=='runtime_allocate'])==1


def test_account_with_quote_balance_only_no_rotation(rig):
    s,b,clock,account,bundle=rig
    empty=s.create_account(OWNER,'acct2')['id'];s.account_action(OWNER,empty,'refresh',{})
    original=b.__call__
    class Accounts:
        installed=True
        def __call__(self,op,path,**kw):
            if op=='account_status' and path.name==empty:return {'authenticated':True,'balance_compute_units':0}
            return original(op,path,**kw)
    s.bridge=Accounts()
    assert s.sessions.select_account(OWNER,15)==account
    with pytest.raises(ColabError):s.sessions.select_account(OWNER,15,empty)
    b.balance=14
    with pytest.raises(ColabError):s.sessions.open(OWNER,account,15)
    assert not any(op=='runtime_allocate' for op,_ in b.calls)


def test_session_cost_separate_from_jobs_and_receipt_gpu(rig):
    s,b,clock,account,bundle=rig;state=ready(rig)
    b.balance-=3;clock[0]+=60;s.sessions.tick_all()  # bootstrap/idle
    job=native(rig);attach_ready(rig,job);job=s.job(OWNER,job['id'])
    b.balance-=2;s._cleanup(s.update(job['id'],phase='cleanup_pending',outcome='completed'))
    j=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    j.reserve(OWNER,job['id'],'colab',account,bundle['case_id'],'identity',{'unit':'CU','amount':15})
    result=ColabNative(s,j).poll(OWNER,job['id'])
    receipt=_public_receipt(Runtime.receipt(result))
    assert receipt['gpu']=='A100' and receipt['gpu_name']=='NVIDIA A100-SXM4-40GB' and receipt['session_id']==state['id']
    ledger=j.ledger(OWNER)
    assert ledger['colab']['actual']==2 and ledger['colab_sessions']['actual']==3
    assert ledger['colab_sessions']['sessions'][0]['total_observed']==5


@pytest.mark.parametrize('idle',[9,61,10.5,True])
def test_owner_idle_bounds(rig,idle):
    s,*_=rig
    with pytest.raises(ColabError):s.sessions.settings(OWNER,{'idle_minutes':idle})


@pytest.mark.parametrize('cap',[0,-1,float('nan'),float('inf'),True])
def test_owner_cu_bounds(rig,cap):
    s,*_=rig
    with pytest.raises(ColabError):s.sessions.settings(OWNER,{'max_units':cap})


def test_session_owner_isolation_and_live_setting(rig):
    s,b,clock,account,bundle=rig;state=ready(rig)
    s.sessions.close('different-owner')
    assert not s.sessions.get(state['id']).get('close_requested')
    assert s.sessions.snapshot('different-owner')['session_ledger']==[]
    s.sessions.settings(OWNER,{'idle_minutes':10,'max_units':50})
    assert s.sessions.get(state['id'])['settings']=={'idle_minutes':10,'hard_hours':6,'max_units':50}


def test_watchdog_child_is_fail_closed_and_syntax_valid():
    from scripts.colab_session_watchdog import build,WORKER
    compile(build(oid(),1000000),'watchdog-bootstrap','exec');compile(WORKER,'watchdog-worker','exec')
    assert "time.time()-float((root/'heartbeat').read_text())<300" in WORKER
    assert "while True:" in WORKER and 'runtime.unassign()' in WORKER




def load_worker(monkeypatch):
    import importlib.util
    monkeypatch.syspath_prepend(str(Path(__file__).parent/'colab_remote'))
    spec=importlib.util.spec_from_file_location('r35_worker',Path(__file__).parent/'colab_session_worker.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('gpu',['NVIDIA L4, 24000','Tesla T4, 16000','NVIDIA A100, 20000'])
def test_runtime_gpu_check_never_accepts_low_vram(monkeypatch,gpu):
    worker=load_worker(monkeypatch)
    with pytest.raises(RuntimeError):worker.verify_gpu(gpu)


@pytest.mark.parametrize('gpu',['NVIDIA A100-SXM4-40GB, 40536','NVIDIA H100 80GB HBM3, 81559','NVIDIA RTX PRO 6000 Blackwell, 97887'])
def test_runtime_gpu_check_accepts_class_memory(monkeypatch,gpu):
    worker=load_worker(monkeypatch)
    assert worker.verify_gpu(gpu)[1]>=39000


def test_bootstrap_public_once_and_no_private_plan(rig,monkeypatch):
    s,b,clock,account,bundle=rig
    state=s.sessions.open(OWNER,account,15);state=s.sessions.freeze(state)
    folder=s.root/'sessions'/state['id']
    plans=json.loads((folder/'bootstrap.json').read_text())
    assert len(plans)==1 and all(not m.get('private') for p in plans for m in p['models'])
    assert all('voice_export' not in p and 'inputs' not in p and 'graph' not in p for p in plans)
    worker=load_worker(monkeypatch);calls=[]
    monkeypatch.setattr(worker,'comfy_identity',lambda *a,**kw:{'verified':True})
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**kw:'NVIDIA A100, 40536')
    monkeypatch.setattr(worker,'bootstrap',lambda plan,cache,**kw:calls.append(plan))
    # This test stubs the entire bootstrap; actual inventory/reuse has separate tests.
    monkeypatch.setattr(worker,'verify_cached_overlays',lambda plans,cache:None)
    monkeypatch.setattr(worker,'verify_ready',lambda *a:None)  # Whole-bootstrap mock; real gate covered in r40v4.
    assert worker.bootstrap_session(folder)==0
    count=len(calls);assert count>0
    assert worker.bootstrap_session(folder)==0 and len(calls)==count
    assert json.loads((folder/'bootstrap-status.json').read_text())['phase']=='ready'


def test_real_manifest_worker_isolation_and_single_submit(rig,monkeypatch,tmp_path):
    s,b,clock,account,bundle=rig
    jobs=[native(rig),native(rig)]
    worker=load_worker(monkeypatch)
    monkeypatch.setattr(worker,'comfy_identity',lambda *a,**kw:{'verified':True})
    session=tmp_path/'remote-session';session.mkdir();cache=session/'cache';cache.mkdir();core=cache/'ComfyUI';core.mkdir()
    monkeypatch.setattr(worker,'prepared',lambda bundle,session:(core,'/mock/python'))
    monkeypatch.setattr(worker,'dependency_probe',lambda *a:{'ready':True})
    monkeypatch.setattr(worker,'output_check',lambda path,contract:None)
    monkeypatch.setattr(worker.time,'time',lambda:clock[0])
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**kw:'NVIDIA H100, 81559' if 'name,memory.total' in str(a) else '1024')
    realpath=Path
    # Redirect the frozen graph's fixed video alias onto disk for offline tests.
    monkeypatch.setattr(worker,'Path',lambda p:tmp_path/'alias' if str(p)=='/content/castle-parity-input' else realpath(p))
    commands=[];submits=[];current=[None]
    monkeypatch.setattr(worker,'stop_group',lambda proc:None)  # Exercised with real CPU process groups in safety suite.
    class Process:
        def poll(self):return None
        def terminate(self):pass
        def wait(self,**kw):return 0
    monkeypatch.setattr(worker.subprocess,'Popen',lambda args,**kw:commands.append(args) or Process())
    def request(method,path,body=None):
        if path=='/object_info':return {}
        if path=='/prompt':
            submits.append(body)
            jobroot=current[0];output=jobroot/'comfy-output';output.mkdir(exist_ok=True)
            src=next(i for i in bundle['inputs'] if i['kind']=='video')
            shutil.copyfile(src['local_path'],output/'result.mp4')
            return {'prompt_id':'accepted'}
        return {'accepted':{'status':{'completed':True},'outputs':{'1':{'videos':[{'type':'output','filename':'result.mp4'}]}}}}
    monkeypatch.setattr(worker,'local_request',request)
    for job in jobs:
        folder=s.root/'jobs'/job['id'];current[0]=folder
        assert worker.run_native(folder,job['manifest_sha256'],session)==0
        result=json.loads((folder/'artifacts.json').read_text())
        assert result['job_id']==job['id'] and result['manifest_sha256']==job['manifest_sha256']
        assert (folder/result['artifacts'][0]['path']).is_file()
        assert not (folder/'comfy-input').exists() and not (folder/'comfy-output').exists()
        assert not (tmp_path/'alias').exists()
        # A repeated launch cannot POST the graph again, even after completion.
        before=len(submits)
        with pytest.raises(FileNotFoundError):worker.run_native(folder,job['manifest_sha256'],session)
        assert json.loads((folder/'reusable-clean.json').read_text())['clean']
        assert len(submits)==before
    assert len(submits)==2 and len(commands)==2
    assert commands[0][commands[0].index('--input-directory')+1]!=commands[1][commands[1].index('--input-directory')+1]
    assert commands[0][commands[0].index('--output-directory')+1]!=commands[1][commands[1].index('--output-directory')+1]


def test_watchdog_heartbeat_failure_closes_lease(rig,monkeypatch):
    s,b,clock,account,bundle=rig;state=ready(rig);original=s._exec
    def broken(state,code):
        if "'heartbeat':True" in code:raise BridgeError('remote_execution_failed')
        return original(state,code)
    monkeypatch.setattr(s,'_exec',broken)
    s.sessions.tick_all();s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']
    assert s.sessions.get(state['id'])['reason']=='watchdog_state_lost'


def test_release_is_not_terminal_until_provider_confirms(rig):
    s,b,clock,account,bundle=rig;state=ready(rig);b.stopped=False
    s.sessions.close(OWNER);s.sessions.tick_all()
    assert not s.sessions.get(state['id'])['released']
    b.stopped=True;s.sessions.tick_all()
    assert s.sessions.get(state['id'])['released']


@pytest.mark.parametrize('heartbeat',['missing','stale','fresh_hard_cap'])
def test_runtime_watchdog_executes_release_on_lost_state(tmp_path,monkeypatch,heartbeat):
    import os,sys,time,subprocess
    from scripts.colab_session_watchdog import WORKER
    root=tmp_path/'watchdog';root.mkdir();now=time.time()
    if heartbeat!='missing':(root/'heartbeat').write_text(str(now-301 if heartbeat=='stale' else now))
    deadline=now-1 if heartbeat=='fresh_hard_cap' else now+600
    monkeypatch.setattr(sys,'argv',['watchdog',str(root),str(deadline)])
    calls=[];fds=[];real_open=os.open
    def opened(*args,**kw):
        fd=real_open(*args,**kw);fds.append(fd);return fd
    monkeypatch.setattr(os,'open',opened)
    def released(args,**kw):
        calls.append(args);raise SystemExit(0)
    monkeypatch.setattr(subprocess,'run',released)
    try:
        with pytest.raises(SystemExit):exec(compile(WORKER,'runtime-watchdog','exec'),{})
    finally:
        for fd in fds:os.close(fd)
    assert len(calls)==1 and 'runtime.unassign()' in calls[0][-1]


def test_controller_pending_session_allows_queue_not_duplicate_job(rig):
    s,b,clock,account,bundle=rig
    a=native(rig);second=native(rig)
    assert a['session_id']==second['session_id'] and a['id']!=second['id']
    assert len(s.sessions.rows(OWNER))==1
    j=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    for job in (a,second):
        _,new=j.reserve(OWNER,job['id'],'colab',account,bundle['case_id'],job['id'],{'unit':'CU','amount':15})
        assert new
    _,new=j.reserve(OWNER,a['id'],'colab',account,bundle['case_id'],a['id'],{'unit':'CU','amount':15})
    assert not new


@pytest.mark.parametrize('action',['get','open','close','settings'])
def test_real_authenticated_session_routes_are_owner_scoped(rig,action):
    import ast,types
    s,b,clock,account,bundle=rig
    tree=ast.parse((Path(__file__).resolve().parents[1]/'webui/server.py').read_text())
    method=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='api_colab')
    method.decorator_list=[]
    module=ast.Module(body=[method],type_ignores=[]);ast.fix_missing_locations(module)
    import colab_service,urllib
    scope={'get_colab_service':lambda:s,'colab_service':colab_service,'urllib':urllib,'RequestBodyError':type('RequestBodyError',(Exception,),{})}
    exec(compile(module,'session-route','exec'),scope)
    class Handler:
        def current_user(self):return {'email':OWNER}
        def read_provider_json(self,*a):return {'idle_minutes':10} if action=='settings' else {}
        def send_json(self,result,status=200,**kw):self.result=result;self.status=status
    h=Handler();scope['api_colab'](h,'/api/colab/session'+('' if action=='get' else '/'+action),action!='get')
    assert h.status==200 and h.result['ok'] and 'session_ledger' in h.result
    assert s.sessions.snapshot('other-owner')['session_ledger']==[]
    if action=='open':assert h.result['session']['phase']=='requesting'
    if action=='settings':assert h.result['settings']['idle_minutes']==10
    assert not any(op=='runtime_allocate' for op,_ in b.calls)


def test_unknown_usage_never_becomes_zero_settlement(rig,monkeypatch):
    s,b,clock,account,bundle=rig;state=ready(rig);job=native(rig);attach_ready(rig,job)
    original=s._call
    def unavailable(state,op,**kw):
        if op=='account_status':raise BridgeError('timeout')
        return original(state,op,**kw)
    monkeypatch.setattr(s,'_call',unavailable)
    s.sessions.close(OWNER);s.sessions.save(state['id'],phase='closing');s.sessions.tick_all()
    assert not s.job(OWNER,job['id'])['released']
    assert s.sessions.get(state['id'])['phase']=='released_unsettled'
    line=s.sessions.snapshot(OWNER)['session_ledger'][0]
    assert line['amount'] is None and line['usage_unknown'] and not line['settled']
    journal=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    ledger=journal.ledger(OWNER)['colab_sessions']
    assert ledger['actual_is_lower_bound'] and ledger['unsettled_sessions']==1


def test_closing_session_never_uploads_or_launches_next(rig):
    s,b,clock,account,bundle=rig;state=ready(rig);job=native(rig);attach_ready(rig,job)
    s.sessions.save(state['id'],phase='closing',reason='watchdog_state_lost')
    before=len(b.calls);s.tick(job)
    assert not any(op in {'upload','runtime_allocate'} or op=='remote_exec' and 'Popen' in k['code'] for op,k in b.calls[before:])
    assert s.job(OWNER,job['id'])['released']
