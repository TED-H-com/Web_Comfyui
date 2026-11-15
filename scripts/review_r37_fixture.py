import sys,json,uuid,shutil,re
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
ROOT=Path(__file__).resolve().parents[1]
C=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(C/'webui'),str(C)]
from colab_service import ColabService,Bridge,BridgeError,encoded
from compute_parity.colab import ColabNative
from compute_parity.journal import Journal

class OfflineBridge:
    installed=True
    def __init__(self):self.calls=[];self.balance=1000;self.hook=None;self.found=False
    def __call__(self,op,account,**kw):
        self.calls.append((op,kw))
        if self.hook:
            result=self.hook(op,kw)
            if result is not None:return result
        if op=='account_identity':return {'bound':True,'subject_key':'b'*64}
        if op=='account_status':return {'authenticated':True,'balance_compute_units':self.balance,'active_assignments':0}
        if op=='runtime_allocate':return {'found':True,'runtime_id':kw['allocation_id'],'accelerator':kw['gpu']}
        if op=='runtime_lookup':return {'found':self.found,'runtime_id':kw['allocation_id'],'accelerator':kw['gpu']}
        if op=='runtime_stop':return {'stopped':True}
        if op=='upload':return {}
        if op=='remote_exec':
            code=kw['code'];compile(code,'offline-remote','exec')
            if "'armed':True" in code:
                sid=re.search(r"'session_id':\s*['\"]([a-f0-9-]{36})",code)[1];out={'armed':True,'session_id':sid,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2}}
            elif 'archive_verified' in code:out={'archive_verified':True}
            elif "'heartbeat':True" in code:out={'heartbeat':True,'watchdog_identity':{'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2}}
            elif 'bootstrap-status.json' in code:out={'phase':'ready','gpu':'A100','gpu_name':'NVIDIA A100','vram_mib':40960}
            elif "'clean'" in code:out={'clean':True}
            else:out={'launched':True,'pid':1,'start_ticks':'42'}
            return {'stdout':'WEB_COMFYUI_JSON='+json.dumps(out)}
        raise AssertionError('forbidden operation '+op)

@pytest.fixture
def rig(tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Real bridge forbidden')
    monkeypatch.setattr(Bridge,'__call__',forbidden)
    monkeypatch.setattr(ColabService,'start',lambda self:None)
    clock=[1000000.0];bridge=OfflineBridge();s=ColabService(tmp_path/'colab',bridge,lambda:clock[0])
    owner='owner-a';account=s.create_account(owner,'offline')['id'];s.account_action(owner,account,'refresh',{})
    from test_compute_parity_p1 import synthetic_material_bundle
    bundle = synthetic_material_bundle(tmp_path)
    j=Journal(tmp_path/'compute-parity/journal.sqlite3',colab_state_path=s.root/'state.sqlite3')
    return NS(s=s,b=bridge,t=clock,owner=owner,account=account,bundle=bundle,j=j,a=ColabNative(s,j),root=tmp_path)

def reserve(r,rid=None):
    rid=rid or str(uuid.uuid4());r.j.reserve(r.owner,rid,'colab',r.account,r.bundle['case_id'],'identity',{'unit':'CU','amount':15})
    r.j.update(r.owner,rid,state='submission_uncertain');return rid

def inputs(bundle):return {i['slot']:Path(i['local_path']).read_bytes() for i in bundle['inputs']}

def submit(r,rid=None,bundle=None):
    b=bundle or r.bundle
    return r.a.submit(r.owner,rid or str(uuid.uuid4()),b,inputs(b),account_id=r.account,quote={'unit':'CU','amount':15})

def session(r,phase='ready',**kw):
    state=r.s.sessions.new_state(r.owner,r.account,'b'*64,r.s.sessions.settings(r.owner))
    state.update(phase=phase,runtime_id='mock-runtime',gpu='A100',assigned_gpu='A100',watchdog_armed=True,watchdog_identity={'pid':1,'start_ticks':'42','deadline':1021600.0,'lock_dev':1,'lock_ino':2},watchdog_runtime_id='mock-runtime',deadline_epoch=r.t[0]+21600,idle_since=r.t[0],last_balance=1000,usage_checked_at=r.t[0],**kw)
    with r.s.db() as db:db.execute('INSERT INTO work_sessions VALUES(?,?,?)',(state['id'],r.owner,encoded(state)))
    return state
