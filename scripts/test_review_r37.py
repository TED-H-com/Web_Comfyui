import base64,json,os,threading,uuid,sqlite3,multiprocessing
from pathlib import Path
import pytest
from review_r37_fixture import rig,reserve,submit,inputs,session
from colab_service import BridgeError,ColabError,encoded
from compute_parity.runtime import Runtime
from compute_parity.contracts import ParityError
from compute_routing import ComputeRouter,ComputeAdapter


def router(r,submitter):
    a=ComputeAdapter(lambda *a,**k:{'eligible':True,'estimated_cost':15,'currency':'compute_units','reasons':[]},submitter,lambda owner,rid:Runtime.receipt(r.a.poll(owner,rid)),lambda owner,rid:Runtime.receipt(r.a.cancel(owner,rid)))
    return ComputeRouter(r.root/'routing.sqlite3',{'colab':a},clock=r.s.clock)


def test_exact_live_shape_owner_get_close_capacity_and_fresh_id(rig):
    r=rig;rid=reserve(r,'00000000-0000-4000-8000-00000000d002');calls=[]
    def dispatch(owner,rid,request,limits):calls.append(rid);return Runtime.receipt(submit(r,rid))
    rt=router(r,dispatch)
    receipt={'state':'submission_uncertain','downstream_id':rid,'cancel_supported':True,'retry_submission_allowed':False}
    with rt._db() as db:db.execute('INSERT INTO compute_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',(r.owner,rid,'hash','colab','colab','submission_uncertain','{}','{}',json.dumps(receipt),r.t[0],r.t[0]))
    before=r.j.get(r.owner,rid)
    with pytest.raises(Exception):rt.status('other-owner',rid,refresh=True)
    assert r.j.get(r.owner,rid)==before
    assert r.s.find_job(r.owner,rid) is None and not (r.s.root/'jobs'/rid).exists()
    got=rt.status(r.owner,rid,refresh=True);assert got['state']=='closed_no_execution_evidence' and got['actual_cost']['amount']==0
    assert got['cancel_supported'] is False and got['retry_submission_allowed'] is False
    assert r.j.ledger(r.owner)['colab']['unsettled_reserved']==0
    preview=rt.preview(r.owner,{},placement='colab')
    assert next(x for x in preview['candidates'] if x['lane']=='colab')['active_requests']==0
    with rt._db() as db:assert db.execute("SELECT count(*) FROM compute_requests WHERE state='submission_uncertain'").fetchone()[0]==0
    for operation in [r.a.poll,r.a.recover,r.a.cancel]:assert operation(r.owner,rid)['state']=='closed_no_execution_evidence'
    # Journal identity remains durable: even an adapter replay cannot re-dispatch.
    with pytest.raises(ParityError):submit(r,rid)
    assert r.s.find_job(r.owner,rid) is None
    new=submit(r);assert new['state']=='running' and r.s.find_job(r.owner,new['request_id'])
    assert not any(op=='runtime_allocate' for op,_ in r.b.calls)


@pytest.mark.parametrize('point',['before_insert','after_commit','lookup_error'])
def test_submit_exception_boundary(rig,monkeypatch,point):
    r=rig;rid=str(uuid.uuid4())
    if point in {'before_insert','lookup_error'}:
        def fail(*a,**k):raise BridgeError('timeout')
        monkeypatch.setattr(r.s,'_submit',fail)
    else:
        def fail():raise OSError('not exposed')
        monkeypatch.setattr(r.s,'start',fail)
    if point=='lookup_error':
        def fail_lookup(*a):raise sqlite3.OperationalError('not exposed')
        monkeypatch.setattr(r.s,'find_job',fail_lookup)
    result=submit(r,rid)
    if point=='before_insert':assert result['state']=='rejected' and result['actual_cost']['amount']==0 and not r.s.sessions.rows()
    elif point=='after_commit':assert result['state']=='running' and len(r.s.sessions.rows())==1
    else:assert result['state']=='submission_uncertain' and 'actual_cost' not in result
    assert result.get('diagnostics') and not any(op=='runtime_allocate' for op,_ in r.b.calls)


@pytest.mark.parametrize('kind',['request','account'])
def test_other_process_lock_prevents_absence_closure(rig,kind):
    r=rig;rid=reserve(r);ctx=multiprocessing.get_context('fork');ready=ctx.Event();finish=ctx.Event()
    def holder():
        with (r.s.native_submission_guard(r.owner,rid) if kind=='request' else r.s.account_guard(r.account)):
            ready.set();assert finish.wait(15)
    p=ctx.Process(target=holder);p.start()
    try:
        assert ready.wait(5);assert r.a.poll(r.owner,rid)['state']=='submission_uncertain'
    finally:finish.set();p.join(5)
    assert p.exitcode==0;assert r.a.poll(r.owner,rid)['state']=='closed_no_execution_evidence'


def test_two_concurrent_native_submits_and_get_cannot_close_inflight(rig,monkeypatch):
    r=rig;rid=str(uuid.uuid4());entered=threading.Event();done=threading.Event();actual=r.s._submit;results=[]
    def delayed(*a,**kw):entered.set();assert done.wait(10);return actual(*a,**kw)
    monkeypatch.setattr(r.s,'_submit',delayed)
    def work():results.append(submit(r,rid))
    t=threading.Thread(target=work);t.start()
    try:
        assert entered.wait(5)
        assert r.a.poll(r.owner,rid)['state']=='submission_uncertain'
        with pytest.raises(BlockingIOError):submit(r,rid)
    finally:done.set();t.join(10)
    assert len(results)==1 and results[0]['state']=='running'
    assert len(r.s.sessions.rows())==1


def test_lane_failure_counting(rig):
    r=rig
    for _ in range(2):
        rid=reserve(r);r.j.update(r.owner,rid,state='rejected',failure_kind='admission',root_cause='invalid_input',actual_cost={'unit':'CU','amount':0})
    r.j.check_admission(r.owner,'colab','video-replace.realistic.example-b')
    for case in ['dance.anime.chatgpt','video-replace.anime.chatgpt']:
        rid=str(uuid.uuid4());r.j.reserve(r.owner,rid,'colab',r.account,case,'id',{'unit':'CU','amount':15});r.j.update(r.owner,rid,state='failed',failure_kind='execution',root_cause='native_runtime_failed')
    with pytest.raises(ParityError) as error:r.j.check_admission(r.owner,'colab','video-replace.realistic.example-b')
    assert error.value.code=='lane_stopped'




@pytest.mark.parametrize('outcome',['closed_no_execution_evidence','rejected'])
def test_confirm_closed_id_can_start_paid_work_through_existing_colab_endpoint(rig,monkeypatch,outcome):
    r=rig
    if outcome=='closed_no_execution_evidence':
        rid=reserve(r);assert r.a.poll(r.owner,rid)['state']==outcome
    else:
        rid=str(uuid.uuid4());original=r.s._submit
        def bad_input(*a,**kw):raise ColabError('不支援的參考檔案格式')
        monkeypatch.setattr(r.s,'_submit',bad_input);assert submit(r,rid)['state']==outcome
        monkeypatch.setattr(r.s,'_submit',original)
    body={'request_id':rid,'account_id':r.account,'gpu':'A100','max_units':20,'max_minutes':60,'job':{'type':'image','prompt':'offline probe'},'files':[]}
    # Same service method used by POST /api/colab/jobs, no validation bypass.
    with pytest.raises(ColabError):r.s.submit(r.owner,body)
    assert r.s.find_job(r.owner,rid) is None
    assert not any(op=='runtime_allocate' for op,_ in r.b.calls)
    assert r.a.poll(r.owner,rid)['state']==outcome
    assert r.j.get(r.owner,rid)['actual_cost']['amount']==0


def test_fresh_generic_uuid_still_admitted(rig):
    r=rig;rid=str(uuid.uuid4())
    body={'request_id':rid,'account_id':r.account,'gpu':'A100','max_units':20,'max_minutes':60,'job':{'type':'image','prompt':'offline probe'},'files':[]}
    assert r.s.submit(r.owner,body)['phase']=='queued'
    r.s.tick(r.s.job(r.owner,rid));assert any(op=='runtime_allocate' for op,_ in r.b.calls)


def test_atomic_tombstone_receipt_roll_back_together(rig,monkeypatch):
    r=rig;rid=reserve(r)
    original=r.j.update_in_transaction
    def fail(db,*a,**kw):
        original(db,*a,**kw)
        raise OSError('injected crash before commit')
    monkeypatch.setattr(r.j,'update_in_transaction',fail)
    with pytest.raises(OSError):r.s.close_admission(r.j,r.owner,rid,state='closed_no_execution_evidence',actual_cost={'unit':'CU','amount':0})
    assert r.j.get(r.owner,rid)['state']=='submission_uncertain'
    assert r.j.ledger(r.owner)['colab']['unsettled_reserved']==15
    with r.s.db() as db:assert db.execute('SELECT 1 FROM request_claims WHERE id=?',(rid,)).fetchone() is None


@pytest.mark.parametrize('foreign',[False,True])
def test_sql_trigger_cannot_bypass_terminal_or_owner_claim(rig,foreign):
    r=rig;rid=reserve(r)
    if foreign:
        with r.s.db() as db:db.execute("INSERT INTO request_claims(id,owner,state) VALUES(?,?,'pending')",(rid,'other-owner'))
    else:assert r.a.poll(r.owner,rid)['state']=='closed_no_execution_evidence'
    with pytest.raises(sqlite3.IntegrityError):
        with r.s.db() as db:db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(rid,r.owner,r.account,'hash',json.dumps({'phase':'queued'})))
    assert r.s.find_job(r.owner,rid) is None


def test_initial_arm_finishes_before_upload_and_fault_does_not_renew_tick(rig):
    r=rig;st=session(r,'arming');sid=st['id'];last=st['last_tick'];r.t[0]+=30
    def hook(op,kw):
        if op=='upload':raise BridgeError('transport_failed')
    r.b.hook=hook;r.s.sessions.tick_all();armed=r.s.sessions.get(sid)
    assert armed['last_tick']>last and not any(op=='upload' for op,_ in r.b.calls)
    r.t[0]+=30;r.s.sessions.tick_all();state=r.s.sessions.get(sid)
    assert state['fault']['stage']=='upload' and state['last_tick']==armed['last_tick']
    assert state['fault_streak']==1 and state['phase']=='uploading'




def test_nonatomic_journal_mode_retains_uncertain_reservation(rig):
    r=rig;rid=reserve(r)
    with sqlite3.connect(r.j.path) as db:assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    got=r.a.poll(r.owner,rid)
    assert got['state']=='submission_uncertain' and 'actual_cost' not in got
    assert got['diagnostics']['error_code']=='admission_lookup_failed'
    with r.s.db() as db:assert db.execute('SELECT 1 FROM request_claims WHERE id=?',(rid,)).fetchone() is None


def test_legacy_job_without_claim_cannot_be_claimed_by_another_owner(rig):
    r=rig;rid=str(uuid.uuid4())
    with r.s.db() as db:
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(rid,r.owner,r.account,'hash',json.dumps({'id':rid,'phase':'queued'})))
        db.execute('DELETE FROM request_claims WHERE id=?',(rid,))  # legacy fixture only
    with pytest.raises(ColabError):r.s.claim_request('other-owner',rid)
    with r.s.db() as db:assert db.execute('SELECT 1 FROM request_claims WHERE id=?',(rid,)).fetchone() is None
    r.s.claim_request(r.owner,rid)
    with r.s.db() as db:
        claim=db.execute('SELECT owner,state FROM request_claims WHERE id=?',(rid,)).fetchone()
        assert tuple(claim)==(r.owner,'admitted')
