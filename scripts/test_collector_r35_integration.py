"""Offline r35/collector integration; providers and session lifecycle forbidden."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from test_review_r35 import rig, job, CANDIDATE
from compute_collector import ReceiptCollector
from compute_routing import ComputeAdapter, ComputeRouter, STATES
from compute_parity.colab import ColabNative
from compute_parity.journal import Journal
from compute_parity.runtime import Runtime


@pytest.mark.parametrize('session_phase,job_phase,expected',[
    ('requesting','queued','running'),('allocating','queued','submission_uncertain'),
    ('uncertain','queued','submission_uncertain'),('uploading','queued','running'),
    ('bootstrap','queued','running'),('ready','running','running'),
    ('closing','cleanup_pending','cancelling'),('stopping','cleanup_pending','cancelling'),
    ('released_unsettled','cleanup_pending','cancelling')])
def test_collector_projects_session_state_without_driving_lease(rig,monkeypatch,session_phase,job_phase,expected):
    s,b,clock,account,session=rig
    s.sessions.save(session['id'],phase=session_phase,usage_unknown=session_phase=='released_unsettled')
    j=job(rig,session['id'],job_phase)
    journal=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    journal.reserve('owner',j['id'],'colab',account,'dance.anime.chatgpt','identity',{'unit':'CU','amount':15})
    native=ColabNative(s,journal)
    forbidden=Mock(side_effect=AssertionError('Collector must not drive Colab lifecycle'))
    for method in ('open','close','release','allocate','tick_all','sweep_leases'):
        monkeypatch.setattr(s.sessions,method,forbidden)
    monkeypatch.setattr(s,'bridge',forbidden);monkeypatch.setattr(s,'start',forbidden)
    def status(owner,rid):return Runtime.receipt(native.poll(owner,rid))
    path=s.root.parent/'compute-routing.sqlite3'
    router=ComputeRouter(path,{'colab':ComputeAdapter(forbidden,forbidden,status,forbidden)},clock=s.clock)
    with router._db() as db:
        db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   ('owner',j['id'],'hash','colab','colab',expected,'{}','{}',
                    json.dumps({'state':expected,'downstream_id':j['id']}),clock[0]-86400,clock[0]-86400))
    server=NS(user_for_email=lambda owner:{'email':owner,'role':'member'})
    collector=ReceiptCollector(server,path,clock=s.clock,wall_clock=s.clock)
    collector.router=lambda owner:router
    assert collector.tick()['refreshed']==1  # Old nonterminal rows remain eligible.
    receipt=router.status('owner',j['id'])
    assert receipt['state']==expected and receipt['session_id']==session['id']
    assert receipt.get('actual_cost') is None
    assert s.sessions.get(session['id'])['phase']==session_phase
    if expected=='submission_uncertain':assert receipt['diagnostics']['stage']=='provisioning'
    forbidden.assert_not_called()


def test_completed_retained_session_job_is_not_collector_work(rig,monkeypatch):
    s,b,clock,account,session=rig
    s.sessions.save(session['id'],phase='ready')
    j=job(rig,session['id'],'completed',released=True,session_runtime_retained=True,units_observed=2)
    path=s.root.parent/'compute-routing.sqlite3';router=ComputeRouter(path,{},clock=s.clock)
    with router._db() as db:
        db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   ('owner',j['id'],'hash','colab','colab','completed','{}','{}',json.dumps({
                       'state':'completed','session_id':session['id'],'session_runtime_retained':True,
                       'actual_cost':{'unit':'CU','amount':2}}),clock[0],clock[0]))
    collector=ReceiptCollector(NS(),path,clock=s.clock,wall_clock=s.clock)
    collector.router=Mock(side_effect=AssertionError('Already settled'))
    assert collector.tick()['scanned']==0
    collector.router.assert_not_called()
    assert s.sessions.get(session['id'])['phase']=='ready'


def method_scope(collector):
    source=CANDIDATE/'webui/server.py';tree=ast.parse(source.read_text())
    handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
    method=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name=='api_compute')
    scope={'_RECEIPT_COLLECTOR':collector}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),scope)
    return scope['api_compute']


@pytest.mark.parametrize('enabled,alive,exists',[(True,True,True),(True,False,True),(True,False,False),(False,False,False)])
def test_admin_health_is_read_only_and_reports_real_thread(monkeypatch,enabled,alive,exists):
    monkeypatch.setenv('CASTLE_COMPUTE_RECEIPT_COLLECTOR','1' if enabled else '0')
    collector=NS(_thread=NS(is_alive=lambda:alive)) if exists else None
    method=method_scope(collector);sent=[]
    handler=NS(current_user=lambda:{'role':'admin'},send_json=lambda value,**kw:sent.append(value))
    forbidden=Mock(side_effect=AssertionError('Health must not assemble adapters'))
    monkeypatch.setattr('compute_web.ComputeWeb',forbidden)
    method(handler,'/api/compute/collector-health',False)
    assert sent==[{'ok':True,'enabled':enabled,'thread_alive':alive}]
    forbidden.assert_not_called()


@pytest.mark.parametrize('user',[None,{'role':'member'},{'role':'operator'}])
def test_health_denies_non_admin(user):
    sent=[];handler=NS(current_user=lambda:user,send_json=lambda value,code:sent.append((value,code)))
    method_scope(None)(handler,'/api/compute/collector-health',False)
    assert sent==[({'ok':False},403)]






def test_new_r35_uncertain_refresh_uses_same_lock_as_collector(rig,monkeypatch):
    s,b,clock,account,session=rig
    j=job(rig,session['id']);s.sessions.save(session['id'],phase='uncertain')
    journal=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    journal.reserve('owner',j['id'],'colab',account,'dance.anime.chatgpt','identity',{'unit':'CU','amount':15})
    native=ColabNative(s,journal);entered=threading.Event();release=threading.Event();calls=[]
    def poll(owner,rid):
        calls.append(True);entered.set();assert release.wait(5)
        return Runtime.receipt(native.poll(owner,rid))
    path=s.root.parent/'compute-routing.sqlite3'
    router=ComputeRouter(path,{'colab':ComputeAdapter(Mock(),Mock(),poll,Mock())},clock=s.clock)
    with router._db() as db:
        db.execute('INSERT INTO compute_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   ('owner',j['id'],'hash','colab','colab','queued','{}','{}',json.dumps({'state':'queued','downstream_id':j['id']}),clock[0],clock[0]))
    collector=ReceiptCollector(NS(user_for_email=lambda owner:{'email':owner,'role':'member'}),path,clock=s.clock,wall_clock=s.clock)
    collector.router=lambda owner:router
    with ThreadPoolExecutor(max_workers=1) as pool:
        task=pool.submit(collector.tick)
        try:
            assert entered.wait(5)
            assert router.status('owner',j['id'],refresh=True)['state']=='queued'
            assert len(calls)==1
        finally:release.set()
        assert task.result(timeout=5)['refreshed']==1
    assert router.status('owner',j['id'])['state']=='submission_uncertain'
