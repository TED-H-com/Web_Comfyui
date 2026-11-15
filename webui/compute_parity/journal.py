"""Disk-backed campaign accounting and mutation journal. Unknown work stays reserved."""
import json
import sqlite3
import time
from pathlib import Path
from .contracts import UNITS, canonical, fail, number

TERMINAL = {'completed','failed','cancelled','rejected','closed_no_execution_evidence','closed_execution_outcome_unknown','quality_rejected'}
class Journal:
    def __init__(self, path, *, colab_state_path=None):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.colab_state_path=Path(colab_state_path) if colab_state_path else self.path.parent.parent/'colab/state.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (owner TEXT, request TEXT, lane TEXT, account TEXT, case_id TEXT, amount REAL, state TEXT, body TEXT, PRIMARY KEY(owner, request))')
            db.execute('CREATE TABLE IF NOT EXISTS revalidation_epochs (owner TEXT, lane TEXT, release TEXT, at REAL, PRIMARY KEY(owner,lane,release))')
        self.path.chmod(0o600)
    def db(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row; return db
    def get(self, owner, request):
        with self.db() as db: row=db.execute('SELECT body FROM jobs WHERE owner=? AND request=?',(owner,request)).fetchone()
        return json.loads(row[0]) if row else None
    def reserve(self, owner, request, lane, account, case_id, identity, quote, cap=None):
        if not owner or not account:fail('account_required','缺少本帳號綁定')
        if not isinstance(quote,dict) or quote.get('unit')!=UNITS[lane] or not number(quote.get('amount')) or (lane!='local' and quote['amount']<=0):
            fail('quote_invalid','缺少正確單位的有效估價')
        from .provisioning import COLAB_BUSY_RAISES, ColabReadBusy
        token=COLAB_BUSY_RAISES.set(True)
        try:
            for attempt in range(3):
                try:
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        row=db.execute('SELECT body FROM jobs WHERE owner=? AND request=?',(owner,request)).fetchone()
                        if row:
                            old=json.loads(row[0])
                            if old['identity']!=identity or old['account']!=account or old['lane']!=lane:fail('request_conflict','同一請求不可變更內容或帳號')
                            return old, False
                        self._admission(db,owner,lane,case_id,account,colab_timeout=2.0)
                        epoch=self._epoch(db,owner,lane)
                        job={'owner':owner,'request_id':request,'lane':lane,'account':account,'case_id':case_id,'identity':identity,
                             'epoch':epoch,'state':'reserved','cost':quote,'reserved_amount':quote['amount'],'created_at':time.time(),'artifacts':[]}
                        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',(owner,request,lane,account,case_id,quote['amount'],'reserved',canonical(job).decode()))
                        return job, True
                except ColabReadBusy as error:
                    if attempt==2 or not ('locked' in str(error).lower() or 'busy' in str(error).lower()):
                        if 'locked' in str(error).lower() or 'busy' in str(error).lower():
                            fail('colab_state_busy','Colab 狀態正在更新，請稍後再試一次')
                        raise
                    time.sleep(0.5)
        finally:
            COLAB_BUSY_RAISES.reset(token)
    @staticmethod
    def _epoch(db,owner,lane):
        row=db.execute('SELECT release FROM revalidation_epochs WHERE owner=? AND lane=? ORDER BY at DESC,rowid DESC LIMIT 1',(owner,lane)).fetchone()
        return row[0] if row else 'legacy'

    @staticmethod
    def family(case_id):
        return 'video' if case_id.startswith(('dance.','video-replace.')) else case_id.split('.')[0]

    def _admission(self,db,owner,lane,case_id,account=None,*,colab_timeout=None):
        rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM jobs WHERE owner=? AND lane=? ORDER BY rowid DESC',(owner,lane))]
        from .provisioning import classify,retry_window,states,cap_exemptions,remaining_attempts
        if lane=='colab':
            evidence=states(self.colab_state_path,owner) if colab_timeout is None else states(self.colab_state_path,owner,timeout=colab_timeout)
        else:
            evidence={}
        exempt=classify(rows,self.colab_state_path,owner,evidence=evidence) if lane=='colab' else set()
        if lane=='runninghub':
            from .runninghub_native import exempt_attempts
            exempt=exempt_attempts(rows)
        counted=[j for j in rows if j['request_id'] not in exempt]
        cap_exempt=cap_exemptions(rows,self.colab_state_path,owner,evidence=evidence)
        if remaining_attempts(rows,case_id,lane,cap_exempt)==0:fail('attempt_cap','每個案例、每個平台最多三次（跨重新驗證）')
        epoch=self._epoch(db,owner,lane)
        if lane=='colab':
            session_capable=self._session_capable() if colab_timeout is None else self._session_capable(timeout=colab_timeout)
        else:
            session_capable=False
        if lane=='colab' and not session_capable:
            window=retry_window(rows,exempt,time.time(),evidence)
            if window['attempts_24h']>=6:fail('provisioning_retry_cap','Colab 配置重試每 24 小時最多六次')
            if window['last_attempt_at'] is not None and time.time()-window['last_attempt_at']<600:
                fail('provisioning_retry_wait','Colab 配置重試至少間隔十分鐘')
        current=[j for j in counted if j.get('epoch','legacy')==epoch]
        def kind(j):
            return j.get('failure_kind') or ('quality' if j['state']=='quality_rejected' else 'execution')
        for scope in ('execution','contract'):
            relevant=[j for j in current if kind(j) not in {'quality','admission'}
                      and (scope=='execution' and kind(j)!='contract' or scope=='contract' and self.family(j['case_id'])==self.family(case_id))][:2]
            if len(relevant)==2 and all((j['state'] in {'failed','rejected','closed_no_execution_evidence','closed_execution_outcome_unknown'} or j.get('output_contract_status')=='rejected') and kind(j)==scope for j in relevant):
                roots=[j.get('contract_root_cause') or j.get('root_cause') or 'unknown' for j in relevant]
                if roots[0]==roots[1]:fail('lane_stopped','連續兩次相同原因失敗；修正後須由擁有者重新驗證')
        if account and not session_capable:
            busy=db.execute('SELECT state FROM jobs WHERE lane=? AND account=?',(lane,account)).fetchall()
            if any(r[0] not in TERMINAL for r in busy):fail('account_busy','此帳號已有執行中或待查任務')

    def _session_capable(self, *, timeout=5.0):
        # Legacy databases retain the r32 policy until the r35 controller creates
        # the session journal. No historical rows are rewritten.
        try:
            with sqlite3.connect(self.colab_state_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=timeout) as db:
                return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='work_sessions'").fetchone())
        except sqlite3.OperationalError as error:
            from .provisioning import COLAB_BUSY_RAISES, ColabReadBusy, _sqlite_busy
            if COLAB_BUSY_RAISES.get() and _sqlite_busy(error):
                raise ColabReadBusy(str(error)) from error
            return False
        except sqlite3.Error:return False

    def check_admission(self,owner,lane,case_id):
        with self.db() as db:self._admission(db,owner,lane,case_id)

    def revalidate(self,owner,lane,release,*,authorized=False):
        # A new epoch is available once per shipped fix, never a general reset.
        if not authorized or lane not in {'colab','runninghub'} or release!='20261111-r31':fail('revalidation_forbidden','此版本未核准重新驗證')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT at FROM revalidation_epochs WHERE owner=? AND lane=? AND release=?',(owner,lane,release)).fetchone()
            if old:return {'lane':lane,'epoch':release,'at':old[0]}
            jobs=[json.loads(r[0]) for r in db.execute('SELECT body FROM jobs WHERE owner=? AND lane=?',(owner,lane))]
            if any(j['state'] not in TERMINAL for j in jobs):fail('account_busy','先確認執行中或待查原任務')
            now=time.time()
            db.execute('INSERT INTO revalidation_epochs VALUES (?,?,?,?)',(owner,lane,release,now))
            return {'lane':lane,'epoch':release,'at':now}

    def update(self, owner, request, **changes):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.update_in_transaction(db,owner,request,**changes)

    def update_in_transaction(self,db,owner,request,*,table='jobs',**changes):
        # Caller owns the transaction; only these internal table names are valid.
        assert table in {'jobs','parity.jobs'}
        row=db.execute(f'SELECT body FROM {table} WHERE owner=? AND request=?',(owner,request)).fetchone()
        if not row:fail('job_not_found','找不到此帳號的任務')
        job=json.loads(row[0])
        if job['state'] in TERMINAL:
            changes.pop('state',None)
            for key in ('remote_id','artifacts','root_cause','output_links','output_metadata'):
                changes.pop(key,None)
            if job.get('actual_cost') is not None:
                for key in ('actual_cost','settlement_status','balance_observation','message'):changes.pop(key,None)
        if job.get('remote_id') and changes.get('remote_id') is None:changes.pop('remote_id',None)
        if job.get('remote_id') and changes.get('remote_id',job['remote_id'])!=job['remote_id']:
            fail('receipt_identity_changed','不可改綁原任務')
        job.update(changes)
        if job.get('state')=='quality_rejected':job['failure_kind']='quality'
        if job.get('root_cause') in {'duration_short','duration_audio_missing','duration_trim_failed','output_contract_failed'}:
            job['failure_kind']='contract'
        if job.get('actual_cost') is not None and job.get('cost'):
            job['actual_minus_quote']=job['actual_cost']['amount']-job['cost']['amount']
        # Retain reservation when actual usage is unavailable, including errors/cancels.
        amount=job['reserved_amount']
        actual=job.get('actual_cost')
        if actual is not None:
            if actual.get('unit')!=UNITS[job['lane']] or not number(actual.get('amount')):fail('usage_invalid','結算單位或數字不正確')
            amount=actual['amount']
        db.execute(f'UPDATE {table} SET state=?,amount=?,body=? WHERE owner=? AND request=?',(job['state'],amount,canonical(job).decode(),owner,request))
        return job

    def ledger(self, owner):
        with self.db() as db:
            rows=db.execute('SELECT body FROM jobs WHERE owner=? ORDER BY rowid',(owner,)).fetchall()
        result={}
        for row in rows:
            job=json.loads(row[0]); lane=job['lane']
            entry=result.setdefault(lane,{'unit':UNITS[lane],'actual':0,'unsettled_reserved':0,'attempts':0})
            entry['attempts']+=1
            if job.get('actual_cost') is not None:entry['actual']+=job['actual_cost']['amount']
            else:entry['unsettled_reserved']+=job['reserved_amount']
        session_timeout=5.0
        if self._session_capable(timeout=session_timeout):
            with sqlite3.connect(self.colab_state_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=session_timeout) as db:
                sessions=[json.loads(r[0]) for r in db.execute('SELECT state FROM work_sessions WHERE owner=?',(owner,))]
            result['colab_sessions']={'unit':'CU','actual':sum(x['session_units'] for x in sessions),
                'unsettled_reserved':0,'attempts':len(sessions),
                'actual_is_lower_bound':any(x.get('usage_unknown') for x in sessions),
                'unsettled_sessions':sum(bool(x.get('usage_unknown')) for x in sessions),
                'sessions':[{'session_id':x['id'],'actual':None if x.get('usage_unknown') else x['session_units'],'observed_actual':x['session_units'],'total_observed':x['units_observed'],
                             'job_units':x['job_units'],'gpu':x.get('gpu_name') or x.get('gpu'),
                             'settled':x.get('released',False) and not x.get('usage_unknown',False)} for x in sessions]}
        return result
