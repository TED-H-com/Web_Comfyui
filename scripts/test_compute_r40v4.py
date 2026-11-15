"""P1m6 dependency gap, private evidence, and pre-model gate. Offline mocks only."""
import copy,json,os,stat,subprocess
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts import colab_session_probe as p,colab_session_diagnostics as d,colab_session_voice as voice
from test_compute_r35 import rig,load_worker,native,OWNER
from compute_parity.adapters import public
from compute_parity.provisioning import pre_ready_infrastructure
ROOT=Path(__file__).resolve().parents[1]



@pytest.mark.parametrize('raw,secret',[
 ('https://example.invalid/private?token=abc','example.invalid'),('user:pass@host','user:pass'),
 ('/home/private/material.wav','/home'),('person@example.invalid','person'),
 ('file:relative/private','relative'),('data:opaque-secret','opaque-secret'),
 ('%41%42%43%44','%41'),('a'*64,'a'*32),('token=secret-value','secret-value')])
def test_private_log_masks_before_limits(tmp_path,raw,secret):
    f=tmp_path/'log'
    with p.private_log(f) as stream:stream.write(('normal\n'*300)+raw+'\n')
    tail=p.log_tail(f)
    assert secret not in tail and len(tail.splitlines())<=200 and len(tail.encode())<=16384
    assert stat.S_IMODE(f.stat().st_mode)==0o600
    assert 'startup_tail' not in d.sanitize_diagnostics({'startup_tail':tail})

def test_names_only_and_private_projection():
    evidence=p.private_diagnostics({'missing_nodes':['GetImageRangeFromBatch','https://private.invalid/node'],'missing_models':['nested/weights.safetensors'],'missing_inputs':['Node.field','file:private'],'startup_tail':'failure\n'})
    assert evidence['missing_nodes']==['GetImageRangeFromBatch']
    assert evidence['missing_models']==['weights.safetensors']
    assert evidence['missing_inputs']==['Node.field']
    assert 'private_dependency_diagnostics' not in public({'private_dependency_diagnostics':evidence})
    assert d.sanitize_diagnostics({'error_code':'node_import_failed','stage':'session_bootstrap','private_dependency_diagnostics':evidence})=={'stage':'session_bootstrap','error_code':'node_import_failed'}








def test_real_worker_dependency_gap_never_posts_prompt(rig,monkeypatch,tmp_path):
    s,b,clock,account,bundle=rig;job=native(rig);worker=load_worker(monkeypatch);session=tmp_path/'remote-session';core=session/'cache/ComfyUI';core.mkdir(parents=True)
    monkeypatch.setattr(worker,'prepared',lambda *a:(core,'/mock/python'))
    monkeypatch.setattr(worker,'comfy_identity',lambda *a,**k:{})
    monkeypatch.setattr(worker.time,'time',lambda:clock[0])
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**k:'NVIDIA A100, 40960')
    realpath=Path;monkeypatch.setattr(worker,'Path',lambda x:tmp_path/'alias' if str(x)=='/content/castle-parity-input' else realpath(x))
    monkeypatch.setattr(worker,'stop_group',lambda p:None)
    def popen(*a,**k):k['stderr'].write('ImportError: missing node\n');k['stderr'].flush();return NS(poll=lambda:None)
    monkeypatch.setattr(worker.subprocess,'Popen',popen);calls=[]
    def request(method,path,*args):calls.append((method,path));assert method=='GET';return {}
    monkeypatch.setattr(worker,'local_request',request)
    monkeypatch.setattr(worker,'dependency_probe',lambda *a:{'ready':False,'missing_nodes':['GetImageRangeFromBatch'],'missing_models':[],'missing_inputs':[]})
    root=s.root/'jobs'/job['id'];assert worker.run_native(root,job['manifest_sha256'],session)==1
    status=json.loads((root/'status.json').read_text())
    assert status['diagnostics']['error_code']=='dependency_gap'
    assert status['private_dependency_diagnostics']['missing_nodes']==['GetImageRangeFromBatch']
    assert 'ImportError' in status['private_dependency_diagnostics']['startup_tail']
    assert not (root/'native-submission.json').exists() and calls==[('GET','/object_info')]
    assert (root/'reusable-clean.json').exists()
    assert stat.S_IMODE((session/'job-evidence'/job['id']/'comfy-startup.log').stat().st_mode)==0o600

@pytest.mark.parametrize('later',[False,True])
def test_failed_plan_private_evidence_reaches_durable_receipt_only(rig,tmp_path,monkeypatch,later):
    from test_compute_r35 import ready
    from compute_parity.colab import ColabNative
    from compute_parity.journal import Journal
    from compute_routing import _public_receipt
    s,bridge,clock,account,bundle=rig;state=ready(rig);job=native(rig);pid=job['bootstrap_plan_id']
    private={'missing_nodes':['GetImageRangeFromBatch'],'missing_models':[],'missing_inputs':[],'startup_tail':'ImportError: missing component'}
    diagnostic={'stage':'session_bootstrap','substage':'import_ready','error_code':'node_import_failed'}
    state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=pid,private_dependency_diagnostics=private)
    monkeypatch.setattr(s,'_exec',lambda *a,**k:{'clean':True})
    s.sessions.fail_plan(state,diagnostic,'bootstrap_failed')
    if later:
        s.sessions.save(state['id'],phase='ready',private_dependency_diagnostics={'missing_nodes':['OtherPlan']})
        job=native(rig);current=s.sessions.get(state['id']);s.sessions.fail_plan({**current,'bootstrap_plan_id':pid},diagnostic,'bootstrap_failed')
    j=Journal(tmp_path/'receipt.sqlite3');j.reserve(OWNER,job['id'],'colab',account,bundle['case_id'],'test',{'unit':'CU','amount':15})
    receipt=ColabNative(s,j)._record(OWNER,job['id'],s.public_job(s.job(OWNER,job['id'])))
    assert j.get(OWNER,job['id'])['private_dependency_diagnostics']==private
    assert receipt['diagnostics']['error_code']=='node_import_failed' and '尚未開始生成' in receipt['message']
    for projection in (receipt,_public_receipt(receipt),s.snapshot(OWNER),s.sessions.snapshot(OWNER)):
        assert 'private_dependency_diagnostics' not in json.dumps(projection)
        assert 'missing component' not in json.dumps(projection) and 'GetImageRangeFromBatch' not in json.dumps(projection)


@pytest.mark.parametrize('marker',['missing','different_attempt','different_plan','clean','legacy'])
def test_controller_requires_probe_cleanup_evidence(rig,tmp_path,monkeypatch,marker):
    import contextlib,io
    from test_compute_r35 import ready
    s,b,clock,account,bundle=rig;state=ready(rig);job=native(rig);pid=job['bootstrap_plan_id']
    state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=pid)
    root=tmp_path/'remote'/pid;root.mkdir(parents=True)
    (root/'bootstrap-process.json').write_text(json.dumps({'pid':999999999,'start_ticks':'0'}))
    if marker!='legacy':(root/'node-probe-attempt.json').write_text(json.dumps({'attempt':'probe-a','plan_id':pid}))
    if marker not in {'missing','legacy'}:
        (root/'node-probe-clean.json').write_text(json.dumps({'attempt':'other' if marker=='different_attempt' else 'probe-a','plan_id':'other' if marker=='different_plan' else pid,'clean':True}))
    def execute(state,code):
        assert 'probe_clean' in code
        code=code.replace('/content/castle-sessions/'+state['id']+'/plans/'+pid,str(root))
        # No process operations in this actual generated-code regression.
        code=code.replace("'/proc/'",repr(str(tmp_path/'no-proc')+'/')).replace("'/proc'",repr(str(tmp_path/'no-proc')))
        monkeypatch.setattr(os,'killpg',lambda *a:pytest.fail('No live process signal'))
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):exec(compile(code,'generated-cleanup','exec'),{})
        return json.loads(stream.getvalue().split('WEB_COMFYUI_JSON=')[1])
    monkeypatch.setattr(s,'_exec',execute)
    s.sessions.fail_plan(state,{'stage':'session_bootstrap','substage':'import_ready','error_code':'node_import_failed'},'bootstrap_failed')
    current=s.sessions.get(state['id'])
    assert current['phase']=='closing'
    assert current['reason']==('bootstrap_failed' if marker in {'clean','legacy'} else 'bootstrap_process_lost')


REQUIREMENTS=b'pillow>=10.3.0\ncolor-matcher\nmatplotlib\nmss\nopencv-python-headless\n'





@pytest.mark.parametrize('cause',['specification','inventory','delta'])
def test_supplement_failure_is_private_pre_ready_uncounted(rig,tmp_path,monkeypatch,cause):
    from test_compute_r35 import ready
    from compute_parity.colab import ColabNative
    from compute_parity.journal import Journal
    from compute_parity.provisioning import states
    error=p.spec_failure(cause,['safe-package']);private=error.private_dependency_diagnostics
    assert private['supplement_detail'] in {'supplement_requirements_changed','supplement_requirement_unlisted','supplement_delta_unexpected'}
    assert 'private_dependency_diagnostics' not in public({'private_dependency_diagnostics':private})
    diagnostic=d.failure_diagnostics(error,'pip_node:ComfyUI-KJNodes')
    assert diagnostic['error_code']=='stage_failed' and diagnostic['command_label']=='pip_node:ComfyUI-KJNodes'
    s,bridge,clock,account,bundle=rig;state=ready(rig);job=native(rig);pid=job['bootstrap_plan_id']
    state=s.sessions.save(state['id'],phase='bootstrap',prepared_plans=[],bootstrap_plan_id=pid,private_dependency_diagnostics=private)
    monkeypatch.setattr(s,'_exec',lambda *a,**k:{'clean':True})
    s.sessions.fail_plan(state,diagnostic,'bootstrap_failed')
    for _ in range(3):s.sessions.tick_all()
    j=Journal(tmp_path/'receipt.sqlite3',colab_state_path=s.root/'state.sqlite3')
    j.reserve(OWNER,job['id'],'colab',account,bundle['case_id'],'test',{'unit':'CU','amount':15})
    ColabNative(s,j)._record(OWNER,job['id'],s.public_job(s.job(OWNER,job['id'])))
    row=j.get(OWNER,job['id']);evidence=states(s.root/'state.sqlite3',OWNER)
    assert pre_ready_infrastructure(row,evidence[job['id']])
    assert row['actual_cost']=={'amount':0.0,'unit':'CU'}


def test_copy_truncate_append_tail_and_modes(tmp_path):
    import fcntl
    path=tmp_path/'comfy-bootstrap.log'
    with p.private_log(path) as stream:
        inode=path.stat().st_ino
        assert stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
        assert fcntl.fcntl(stream.fileno(),fcntl.F_GETFL)&os.O_APPEND
        for i in range(30):
            stream.write('newest-line-'+str(i)+'\n'+'x'*1000+'\n');stream.flush()
            assert path.stat().st_size<=2048+1100
            p.bound_log(path,budget=2048,keep=1024)
            assert path.stat().st_size<=2048
            if Path(str(path)+'.prev').exists():assert Path(str(path)+'.prev').stat().st_size<=1024
        stream.write('tail-before-truncate\n');stream.flush();p.bound_log(path,budget=1,keep=1024)
        assert 'tail-before-truncate' in p.log_tail(path)
        stream.write('tail-after-truncate\n');stream.flush()
        assert path.stat().st_size==len('tail-after-truncate\n') and path.stat().st_ino==inode
    p.compact_log(path,tmp_path,keep=1024)
    assert path.stat().st_size<=1024 and not Path(str(path)+'.prev').exists()
    assert 'tail-after-truncate' in p.log_tail(path) and stat.S_IMODE(path.stat().st_mode)==0o600


def test_compacted_log_retains_only_newest_32(tmp_path):
    for i in range(40):
        path=tmp_path/'job-evidence'/str(i)/'comfy-startup.log'
        with p.private_log(path) as stream:stream.write('x'*2000)
        p.bound_log(path,budget=1000,keep=256)
        assert stat.S_IMODE(Path(str(path)+'.prev').stat().st_mode)==0o600
        p.compact_log(path,tmp_path,keep=256)
    logs=list(tmp_path.rglob('comfy-*.log'))
    assert len(logs)==32 and sum(f.stat().st_size for f in logs)<=32*256
    assert not list(tmp_path.rglob('*.prev')) and not (tmp_path/'job-evidence/0/comfy-startup.log').exists()


def test_bounder_exception_is_private_and_child_file_unchanged(tmp_path,monkeypatch):
    path=tmp_path/'comfy-bootstrap.log'
    with p.private_log(path) as stream:
        stream.write('before\n'*100);stream.flush()
        real=p.os.replace
        monkeypatch.setattr(p.os,'replace',lambda a,b:(_ for _ in ()).throw(OSError()) if str(b).endswith('.prev') else real(a,b))
        p.bound_log(path,budget=10,keep=8)
        stream.write('after\n');stream.flush()
        assert path.read_text().endswith('after\n') and len(path.read_bytes())>700
    assert json.loads(Path(str(path)+'.notes.json').read_text())==['log_bound_failed']


@pytest.mark.parametrize('broken',[False,True])
def test_real_worker_execution_log_bounder_never_changes_result(rig,tmp_path,monkeypatch,broken):
    s,b,clock,account,bundle=rig;job=native(rig);worker=load_worker(monkeypatch)
    session=tmp_path/'remote-session';core=session/'cache/ComfyUI';core.mkdir(parents=True)
    monkeypatch.setattr(worker,'prepared',lambda *a:(core,'/mock/python'))
    monkeypatch.setattr(worker,'comfy_identity',lambda *a,**k:{})
    monkeypatch.setattr(worker.time,'time',lambda:clock[0]);ticks=[0.]
    monkeypatch.setattr(worker.time,'monotonic',lambda:ticks[0])
    monkeypatch.setattr(worker.time,'sleep',lambda n:ticks.__setitem__(0,ticks[0]+n))
    monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**k:'NVIDIA A100, 40960')
    realpath=Path;monkeypatch.setattr(worker,'Path',lambda x:tmp_path/'alias' if str(x)=='/content/castle-parity-input' else realpath(x))
    monkeypatch.setattr(worker,'stop_group',lambda proc:None);handles=[];calls=[]
    def launch(*a,**kw):handles.append(kw['stdout']);return NS(poll=lambda:None)
    monkeypatch.setattr(worker.subprocess,'Popen',launch)
    monkeypatch.setattr(worker,'dependency_probe',lambda *a:{'ready':True})
    monkeypatch.setattr(worker,'output_check',lambda *a:None)
    def bound(path):
        calls.append(ticks[0])
        if broken:raise RuntimeError('simulated bounder error')
        p.bound_log(path,budget=1024,keep=256)
    monkeypatch.setattr(worker,'bound_log',bound)
    root=s.root/'jobs'/job['id'];out=root/'comfy-output';out.mkdir();(out/'output.mp4').write_bytes(b'offline output')
    def request(method,path,*args):
        if path=='/object_info':return {}
        if path=='/prompt':return {'prompt_id':'offline'}
        handles[0].write('warning\n'*100);handles[0].flush()
        if not broken:assert (session/'job-evidence'/job['id']/'comfy-startup.log').stat().st_size<=1024+1600
        if ticks[0]<20:return {}
        return {'offline':{'status':{'completed':True},'outputs':{'1':{'videos':[{'type':'output','filename':'output.mp4'}]}}}}
    monkeypatch.setattr(worker,'local_request',request)
    assert worker.run_native(root,job['manifest_sha256'],session)==0
    assert json.loads((root/'status.json').read_text())['phase']=='completed'
    assert calls and all(b-a>=2 for a,b in zip(calls,calls[1:]))
    logfile=session/'job-evidence'/job['id']/'comfy-startup.log'
    assert logfile.stat().st_size<=p.LOG_KEEP and not Path(str(logfile)+'.prev').exists()
    if broken:assert json.loads(Path(str(logfile)+'.notes.json').read_text())==['log_bound_failed']
