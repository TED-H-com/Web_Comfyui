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

