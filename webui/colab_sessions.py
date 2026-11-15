"""Durable native work sessions. The existing bridge remains the lease authority.

Allocation rounds are serialized across owners by the controller flock and SQLite.
An ambiguous request is looked up by its original UUID, never POSTed again.
"""
import hashlib
import inspect
import json
import logging
import math
import sqlite3
import time
from pathlib import Path
import uuid

logger = logging.getLogger(__name__)

GPUS = ('A100', 'H100', 'G4')
CLOSED = {'closed'}
HEARTBEAT = 300
FAULT_LIMIT = 2
FAULT_SPAN = 90
UNSENT_FAULT_LIMIT = 6
UNSENT_FAULT_SPAN = 180
UNSENT_STAGES = frozenset({'heartbeat','observe','bootstrap_probe','job_probe'})
assert FAULT_SPAN <= UNSENT_FAULT_SPAN < HEARTBEAT
ADMISSION_PHASES = {'requesting','allocating','uncertain','allocation_absent','arming','uploading','bootstrap_launch','bootstrap','ready'}


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def bootstrap_progress_fault(now, progress, previous=None):
    """One grammar on both sides of the RPC; allow at most 1 s clock skew."""
    import math
    if type(progress) not in (int, float) or not math.isfinite(progress):
        return 'progress_nonfinite'
    if type(now) not in (int, float) or not math.isfinite(now):
        return 'progress_clock_invalid'
    if progress > now + 1:
        return 'progress_future'
    if now - progress > 120:
        return 'progress_stale'
    if previous is not None:
        if type(previous) not in (int, float) or not math.isfinite(previous):
            return 'progress_previous_invalid'
        if progress < previous:
            return 'progress_regressed'
    return None


def admission_observations(value):
    """Recorded event times, excluding scheduled retry/deadline timestamps."""
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key == 'private_log_stats':
            continue
        if (key.endswith('_at') and key != 'retry_at') or key in {
                'last_tick', 'fault_since', 'bootstrap_progress', 'idle_since', 'at'}:
            if item is not None:
                yield item
        elif isinstance(item, dict):
            yield from admission_observations(item)
        elif isinstance(item, list):
            for child in item:
                yield from admission_observations(child)


class WorkSessions:
    def __init__(self, service):
        self.s = service
        self._tick_stage = {}
        self._monotonic = time.monotonic
        self._recovery_anchors = {}
        self._admission_anchors = {}
        with service.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS work_sessions (id TEXT PRIMARY KEY, owner TEXT, state TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS session_settings (owner TEXT PRIMARY KEY, state TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS session_attempts (id TEXT PRIMARY KEY, session_id TEXT, at REAL)')

    def rows(self, owner=None):
        with self.s.db() as db:
            rows = db.execute('SELECT state FROM work_sessions' + (' WHERE owner=?' if owner is not None else '') + ' ORDER BY rowid', (owner,) if owner is not None else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, sid):
        return next(s for s in self.rows() if s['id'] == sid)

    def save(self, sid, **changes):
        from colab_service import encoded
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?', (sid,)).fetchone()[0])
            success=bool({'last_tick','archive_uploaded','archive_verified','upload_completed_at','digest_verified_at'} & changes.keys())
            observed_code=self.budget_status(state)[1]
            code=observed_code if success or observed_code=='clock_inconsistent' else None
            if code:changes=self.budget_close(state,code)
            state.update(changes)
            db.execute('UPDATE work_sessions SET state=? WHERE id=?', (encoded(state), sid))
        if code and success:
            from colab_service import BridgeError
            raise BridgeError(code,code!='clock_inconsistent')
        return state

    def settings(self, owner, values=None):
        from colab_service import ColabError, encoded
        with self.s.db() as db:
            if values is not None:db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM session_settings WHERE owner=?', (owner,)).fetchone()
            result = json.loads(row[0]) if row else {'idle_minutes': 20, 'hard_hours': 6, 'max_units': 120}
            if values is not None:
                result.update({k: values[k] for k in result if k in values})
                if (type(result['idle_minutes']) is not int or not 10 <= result['idle_minutes'] <= 60
                        or result['hard_hours'] != 6 or not finite(result['max_units']) or result['max_units'] <= 0):
                    raise ColabError('閒置時間須為 10–60 分鐘，CU 上限須為正數；工作階段最長六小時')
                db.execute('INSERT OR REPLACE INTO session_settings VALUES (?,?)', (owner, encoded(result)))
                rows=db.execute('SELECT id,state FROM work_sessions WHERE owner=?',(owner,)).fetchall()
                for sid,raw in rows:
                    state=json.loads(raw)
                    if state['phase']!='closed':
                        state['settings']=result
                        db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),sid))
        return result

    def public(self, state):
        if not state:
            return {'phase': 'closed', 'status_label': '未開啟'}
        phase = state['phase']
        label = '未開啟' if phase == 'closed' else '關閉中' if state.get('close_requested') or phase in {'closing','stopping','released_unsettled'} else '可用' if phase == 'ready' else '搶機中'
        fields = ('id','phase','gpu','gpu_name','units_observed','session_units','job_units','attempts','retry_at','active_job','close_requested','settings','released','reason','fault')
        result = {k: state.get(k) for k in fields}
        result['diagnostics']={'fault':state.get('fault')} if state.get('fault') else {}
        result['retry_at']=state.get('retry_at') or (state.get('round_at',self.s.clock())+600 if state['attempts'] else self.s.clock())
        result.update(status_label=label, idle_seconds=max(0, int(state.get('idle_since', self.s.clock()) + state['settings']['idle_minutes']*60 - self.s.clock())) if phase == 'ready' and not state.get('active_job') else None)
        return result

    def snapshot(self, owner):
        rows = self.rows(owner)
        active = next((s for s in reversed(rows) if s['phase'] != 'closed'), None)
        return {'session': self.public(active or (rows[-1] if rows else None)), 'settings': self.settings(owner),
                'session_ledger': [{'session_id':s['id'], 'unit':'CU', 'amount':None if s.get('usage_unknown') else s['session_units'], 'observed_amount':s['session_units'], 'usage_unknown':s.get('usage_unknown',False), 'total_observed':s['units_observed'], 'job_units':s['job_units'], 'settled':s.get('released',False) and not s.get('usage_unknown',False), 'gpu':s.get('gpu_name') or s.get('gpu')} for s in rows]}

    def select_account(self, owner, quote=0, preferred=None):
        from colab_service import ColabError
        accounts = self.s.snapshot(owner)['accounts']
        if preferred:
            accounts = [a for a in accounts if a['id'] == preferred]
        for account in accounts:
            # No credential rotation on failure. Only authenticated funded accounts
            # are candidates, and the selected account is fixed for this request.
            if not account.get('authenticated'):
                continue
            status = self.s.account_action(owner, account['id'], 'refresh', {})
            balance = status.get('balance_compute_units')
            if status.get('authenticated') and finite(balance) and balance > 0 and balance >= quote:
                return account['id']
        raise ColabError('沒有已登入且 CU 足以涵蓋估價的 Colab 帳號')

    def open(self, owner, account_id=None, quote=0):
        from colab_service import ColabError, encoded
        active = next((s for s in self.rows(owner) if s['phase'] != 'closed'), None)
        if active:
            if active.get('close_requested'):
                raise ColabError('工作階段關閉中，請等候釋放')
            if account_id and active['account_id'] != account_id:
                raise ColabError('已開啟工作階段的帳號與估價不符')
            return active
        account_id = self.select_account(owner, quote, account_id)
        identity = self.s.bridge('account_identity', self.s.account_dir(account_id))
        subject = identity.get('subject_key')
        if not identity.get('bound') or not isinstance(subject, str) or len(subject) != 64:
            raise ColabError('請先確認 Google 帳號身分')
        state = self.new_state(owner, account_id, subject, self.settings(owner))
        with self.s.db() as db:
            snapshot=json.loads(db.execute('SELECT snapshot FROM accounts WHERE owner=? AND id=?',(owner,account_id)).fetchone()[0])
        state['admission_verification']={'owner':owner,'account_id':account_id,'subject_key':subject,'identity_at':self.s.clock(),
                                       'balance_at':snapshot.get('checked_at'),'balance':snapshot.get('balance_compute_units')}
        self.admission_fresh(state,state['admission_verification'],self.s.clock())
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            active = [json.loads(r[0]) for r in db.execute('SELECT state FROM work_sessions')]
            for other in active:
                if other['phase'] != 'closed' and (other['owner'] == owner or other['subject_key'] == subject):
                    if other['owner'] == owner and not other.get('close_requested'):
                        return other
                    raise ColabError('這個帳號仍有待回收的工作階段')
            jobs = [json.loads(r[0]) for r in db.execute('SELECT state FROM jobs')]
            if any(j['phase'] not in {'completed','failed','cancelled'} and not j.get('session_job') and (j['account_id'] == account_id or j.get('subject_key') == subject) for j in jobs):
                raise ColabError('這個帳號已有執行中或待回收的任務')
            db.execute('INSERT INTO work_sessions VALUES (?,?,?)', (state['id'], owner, encoded(state)))
        self.s.start()
        return state

    def admission_fresh(self,state,v,now):
        if (not finite(now) or not all(finite(state.get(k)) for k in ('created_at','last_tick'))
                or any(not finite(at) or at > now for at in admission_observations(state))):
            return False
        # Do not rejuvenate an observed snapshot on wall-clock rewind, even
        # before last_tick advances. A restart gets only the durable remainder.
        key=(state['id'],v.get('identity_at'),v.get('balance_at'))
        if not all(finite(v.get(k)) for k in ('identity_at','balance_at')):
            return False
        mono=self._monotonic()
        if not finite(mono):return False
        anchor=self._admission_anchors.setdefault(key, (now,mono,mono+600-max(now-v['identity_at'],now-v['balance_at'])))
        if now<anchor[0] or mono<anchor[1] or mono>anchor[2]:
            return False
        self._admission_anchors[key]=(now,mono,anchor[2])
        return True

    def admission_snapshot(self,owner,account_id):
        now=self.s.clock()
        for state in self.rows(owner):
            v=state.get('admission_verification')
            if (state.get('owner')!=owner or state.get('account_id')!=account_id or state['phase'] not in ADMISSION_PHASES or state.get('close_requested') or state.get('released') or not isinstance(v,dict)):continue
            if not self.admission_fresh(state,v,now):continue
            if (v.get('owner')==owner and v.get('account_id')==account_id and v.get('subject_key')==state.get('subject_key')
                    and isinstance(v.get('subject_key'),str) and len(v['subject_key'])==64
                    and all(c in '0123456789abcdef' for c in v['subject_key'])
                    and all(finite(v.get(k)) and 0<=now-v[k]<=600 for k in ('identity_at','balance_at'))
                    and finite(v.get('balance')) and v['balance']>0):return v
        return None

    def new_state(self, owner, account_id, subject, settings):
        return {'id':str(uuid.uuid4()), 'owner':owner, 'account_id':account_id, 'subject_key':subject,
                'phase':'requesting', 'created_at':self.s.clock(), 'settings':settings,
                'attempts':0, 'gpu_index':0, 'retry_at':0, 'units_observed':0, 'session_units':0,
                'job_units':0, 'last_tick':self.s.clock(), 'released':False}

    def bind_admission(self, db, owner, job):
        """Called inside the job INSERT transaction, before the worker can see it."""
        from colab_service import encoded, ColabError
        rows=[json.loads(r[0]) for r in db.execute('SELECT state FROM work_sessions')]
        state=next((r for r in rows if r['phase']!='closed' and r['owner']==owner),None)
        if state:
            if state.get('close_requested') or state['account_id']!=job['account_id']:
                raise ColabError('工作階段關閉中或帳號不符')
        else:
            row=db.execute('SELECT state FROM session_settings WHERE owner=?',(owner,)).fetchone()
            settings=json.loads(row[0]) if row else {'idle_minutes':20,'hard_hours':6,'max_units':120}
            state=self.new_state(owner,job['account_id'],job['subject_key'],settings)
            state['admission_created']=True
            state['admission_verification']=job.get('admission_verification')
            if isinstance(state['admission_verification'],dict):
                self.admission_fresh(state,state['admission_verification'],self.s.clock())
            db.execute('INSERT INTO work_sessions VALUES (?,?,?)',(state['id'],owner,encoded(state)))
        job['session_id']=state['id']

    def close(self, owner):
        for state in self.rows(owner):
            if state['phase'] != 'closed':
                self.save(state['id'], close_requested=True, reason='owner_close')
                with self.s.db() as db:
                    jobs=[json.loads(r[0]) for r in db.execute('SELECT state FROM jobs WHERE owner=?',(owner,))]
                for job in jobs:
                    if job.get('session_job') and job['phase']=='queued' and job.get('session_id') in (None,state['id']):
                        self.s.update(job['id'],session_id=state['id'],cancel_requested=True)
        self.s.start()
        return self.snapshot(owner)

    def _meter(self, db, sid, balance, session_only=False):
        from colab_service import encoded, TERMINAL
        state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(sid,)).fetchone()[0])
        code=self.budget_status(state)[1]
        if code:
            from colab_service import BridgeError
            raise BridgeError(code,code!='clock_inconsistent')
        spent=max(0,state.get('last_balance',balance)-balance)
        job=None
        if state.get('active_job') and not session_only:
            row=db.execute('SELECT state FROM jobs WHERE id=?',(state['active_job'],)).fetchone()
            job=json.loads(row[0]) if row else None
        # Once observable as settled, a job's meter is immutable.
        if job and not job.get('released') and job['phase'] not in TERMINAL:
            job['units_observed']=job.get('units_observed',0)+spent
            db.execute('UPDATE jobs SET state=? WHERE id=?',(encoded(job),job['id']))
            state['job_units']+=spent
        else:
            state['session_units']+=spent
        state.update(last_balance=balance,units_observed=state['units_observed']+spent,usage_checked_at=self.s.clock())
        db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),sid))
        return state

    def balance(self,state):
        self.operation(state,'observe')
        status=self.s._call(state,'account_status')
        value=status.get('balance_compute_units')
        if not finite(value) or not status.get('authenticated'):raise ValueError('usage_unavailable')
        return value

    def observe(self, state):
        from colab_service import encoded
        remembered_owner, remembered_account, remembered_subject = state.get('owner'), state.get('account_id'), state.get('subject_key')
        self.operation(state,'observe')
        t0=self.s.clock()
        status=self.s._call(state,'account_status')
        balance=status.get('balance_compute_units')
        if not finite(balance) or not status.get('authenticated'):raise ValueError('usage_unavailable')
        refreshed=None
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            if (current.get('owner'), current.get('account_id'), current.get('subject_key'))!=(remembered_owner, remembered_account, remembered_subject):
                logger.warning('colab_observe_binding_changed session=%s', state['id'])
                return current
            current=self._meter(db,state['id'],balance)
            old=current.get('admission_verification')
            subject=current.get('subject_key')
            now=self.s.clock()
            if (status.get('authenticated') is True and isinstance(subject,str) and len(subject)==64
                    and all(c in '0123456789abcdef' for c in subject) and status.get('subject_key')==subject
                    and finite(balance) and balance>0 and current.get('phase') in ADMISSION_PHASES
                    and not current.get('close_requested') and not current.get('released')
                    and finite(t0) and finite(now) and t0<=now
                    and (old is None or isinstance(old,dict) and all(finite(old.get(k)) and old[k]<=t0 for k in ('identity_at','balance_at')))):
                refreshed={'owner':current['owner'],'account_id':current['account_id'],'subject_key':subject,
                           'identity_at':t0,'balance_at':t0,'balance':balance,'source':'session_observe'}
                current['admission_verification']=refreshed
                db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(current),state['id']))
        if refreshed is not None:
            key=(state['id'],t0,t0)
            for existing in list(self._admission_anchors):
                if existing[0]==state['id'] and existing!=key:self._admission_anchors.pop(existing,None)
            self.admission_fresh(current,refreshed,self.s.clock())
        return current

    @staticmethod
    def closed_needs_reconciliation(state):
        # Older code could close an absent UUID before its dispatch grace ended.
        # A terminal label alone is not evidence that this allocation resolved.
        return (state['phase']=='closed' and bool(state.get('allocation_id'))
                and not state.get('runtime_id')
                and state.get('allocation_resolved_id')!=state['allocation_id'])

    def absence_grace_at(self, state):
        """Use only this UUID's committed intent; legacy/pre-intent rows fail safe.

        Without a bound intent we cannot prove when POST could have happened.
        Persist the first recovery observation as the latest plausible dispatch
        time, once per UUID. Restarts/negative lookups never slide this fallback.
        This changes absence reconciliation only, never the hard lease deadline.
        """
        from colab_service import encoded
        def bound_time(row):
            aid=row.get('allocation_id')
            for id_key,time_key in (('dispatch_intent_id','dispatch_intent_at'),
                                    ('absence_grace_id','absence_grace_at')):
                if aid and row.get(id_key)==aid and finite(row.get(time_key)):
                    return row[time_key]
        at=bound_time(state)
        if at is not None:return at
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            if current.get('allocation_id')!=state.get('allocation_id'):
                raise ValueError('Allocation identity changed during reconciliation')
            at=bound_time(current)
            if at is None:
                at=max([self.s.clock()]+[current[k] for k in ('allocation_at','dispatch_intent_at') if finite(current.get(k))])
                current.update(absence_grace_id=current['allocation_id'],absence_grace_at=at)
                db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(current),current['id']))
        return at

    def operation(self,state,stage):
        self._tick_stage[state['id']]=stage

    def budget_status(self,state):
        """Pure check, also safe inside a write transaction. Cleanup is exempt."""
        if state['phase'] in {'closing','stopping','released_unsettled','closed'}:return None,None
        now=self.s.clock()
        observations=[state.get('last_tick'),(state.get('fault') or {}).get('at'),state.get('fault_since')]
        if (not finite(now) or any(value is not None and (not finite(value) or value>now) for value in observations)
                or state.get('fault_streak') and not finite(state.get('fault_since'))):
            return 0,'clock_inconsistent'
        if not state.get('fault_streak'):return None,None
        span = UNSENT_FAULT_SPAN if state.get('fault_streak_unsent') is True else FAULT_SPAN
        since=state['fault_since'];remaining=span-(now-since);mono=self._monotonic()
        anchor=self._recovery_anchors.get(state['id'])
        if anchor is None or anchor[0]!=since:
            # Restart: at most the durable remainder, never a new 90 seconds.
            anchor=(since,mono+min(span,max(0,remaining)))
            self._recovery_anchors[state['id']]=anchor
        remaining=min(remaining,anchor[1]-mono)
        return remaining,'timeout' if remaining<=0 else None

    def budget_close(self,state,code):
        fault=state.get('terminal_fault') or state.get('fault')
        changes={'phase':'closing','reason':'watchdog_state_lost','close_requested':True,'cycle_faulted':True}
        if code=='clock_inconsistent':
            now=self.s.clock()
            fault={'stage':'state','code':code,'at':now if finite(now) else 0,
                   'count':(state.get('fault') or {}).get('count',0)+1,'ambiguous':False}
            changes['fault']=fault
            # Corrupt non-JSON numbers cannot be persisted by encoded(). Retain
            # the fixed fault, neutralize only unusable recovery timestamps.
            if not finite(state.get('last_tick')):changes['last_tick']=0
            if state.get('fault_since') is not None and not finite(state['fault_since']):changes['fault_since']=None
        changes['terminal_fault']=fault
        return changes

    def recovery_remaining(self, state):
        """Wall and process-monotonic bounds both apply before/after each RPC."""
        current=self.get(state.get('session_id') or state['id'])
        remaining,code=self.budget_status(current)
        if code:
            self.save(current['id'],**self.budget_close(current,code))
            from colab_service import BridgeError
            raise BridgeError(code,code!='clock_inconsistent',{'timeout_kind':'process','transport_phase':'wait'} if code=='timeout' else {})
        return remaining

    def finish_cycle(self, sid, job_id=None):
        """Only the controller's complete session + active-job cycle may renew."""
        from colab_service import encoded
        self.recovery_remaining(self.get(sid))
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(sid,)).fetchone()[0])
            if (state['phase'] in {'closing','stopping','released_unsettled','closed'}
                    or not state.get('cycle_session_ok') or state.get('cycle_faulted')
                    or state.get('cycle_job')!=job_id):return
            now=self.s.clock()
            code=self.budget_status(state)[1]
            if code:
                state.update(self.budget_close(state,code))
            else:
                state.update(last_tick=now,fault_streak=0,fault_since=None,fault_streak_unsent=False,cycle_session_ok=False,cycle_job=None)
                self._recovery_anchors.pop(sid,None)
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),sid))

    def tick_all(self):
        for state in self.rows():
            if state['phase']=='closed' and not self.closed_needs_reconciliation(state):continue
            self.operation(state,'state')
            # Starting another cycle never clears the durable recovery budget.
            self.save(state['id'],cycle_session_ok=False,cycle_faulted=False,cycle_job=None)
            try:
                self.tick(state)
                current=self.get(state['id'])
                self.recovery_remaining(current)
                self.save(state['id'],cycle_session_ok=True,cycle_job=current.get('active_job'))
                if not current.get('active_job'):self.finish_cycle(state['id'])
            except Exception as error:
                self.record_fault(state,error)

    def record_fault(self,state,error,reconciling_launch=False):
        from colab_service import BridgeError
        from scripts.colab_session_diagnostics import ERRORS
        current=self.get(state['id']);stage=self._tick_stage.get(state['id'],'state')
        previous=current.get('fault',{});now=self.s.clock()
        if self.budget_status(current)[1]=='clock_inconsistent':error=BridgeError('clock_inconsistent')
        streak=current.get('fault_streak',0)+1;since=current.get('fault_since')
        if since is None:since=now
        bridge=isinstance(error,BridgeError)
        unsent=(bridge and error.code=='transport_not_ready' and not error.ambiguous
                and stage in UNSENT_STAGES and not reconciling_launch)
        unsent_only=unsent and (not current.get('fault_streak') or current.get('fault_streak_unsent') is True)
        limit,span=(UNSENT_FAULT_LIMIT,UNSENT_FAULT_SPAN) if unsent_only else (FAULT_LIMIT,FAULT_SPAN)
        if not current.get('fault_streak'):
            self._recovery_anchors[state['id']]=(since,self._monotonic()+span)
        store_busy=isinstance(error,sqlite3.OperationalError) and ('locked' in str(error).lower() or 'busy' in str(error).lower())
        codes={'transport_not_ready','clock_inconsistent','dependency_missing', 'operation_timeout', 'authentication_required', 'oauth_flow_missing', 'not_installed', 'runtime_unavailable', 'upload_transport_failed', 'runtime_missing', 'download_too_large', 'allocation_uncertain', 'oauth_flow_expired', 'invalid_remote_directory', 'account_identity_mismatch', 'upload_local_invalid', 'upload_failed', 'transport_failed', 'invalid_download', 'upload_integrity_error', 'bridge_unavailable', 'oauth_exchange_failed', 'transport_error', 'invalid_remote_output', 'runtime_unknown', 'invalid_runtime', 'invalid_request', 'output_too_large', 'upload_timeout', 'legacy_identity_unverified', 'redirect_rejected', 'stop_uncertain', 'upload_rejected', 'execution_uncertain', 'upload_http_error', 'input_too_large', 'account_busy', 'timeout', 'dependency_version', 'oauth_flow_mismatch', 'invalid_account_status', 'private_path', 'stop_pending', 'runtime_unreconciled', 'remote_execution_failed', 'allocation_already_attempted', 'allocation_conflict', 'identity_unverified'}
        code='store_busy' if store_busy else (error.code if error.code in codes else 'bridge_error') if bridge else (type(error).__name__ if type(error).__name__ in ERRORS else 'Exception')
        from scripts.colab_transport_diagnostics import sanitize as transport_safe
        fault={'stage':stage,'code':code,'at':now,'count':previous.get('count',0)+1,'ambiguous':bool(error.ambiguous) if bridge else False,**transport_safe(error.diagnostics if bridge else {})}
        if not bridge:fault['error_type']=type(error).__name__ if type(error).__name__ in ERRORS else 'Exception'
        self.save(state['id'],fault=fault,fault_streak=streak,fault_since=since,cycle_faulted=True,private_rpc_error=getattr(error,'private_diagnostics',{}),fault_streak_unsent=unsent_only)
        bounds=streak<limit and now-since<span and now-current['last_tick']<HEARTBEAT and now<current.get('deadline_epoch',0)
        transient=(bridge and (not error.ambiguous or stage in {'heartbeat','bootstrap_probe','job_probe'} or reconciling_launch) and (error.code in {'account_busy','transport_not_ready','execution_uncertain','timeout','operation_timeout','bridge_unavailable','runtime_unavailable','upload_failed','upload_timeout','upload_transport_failed','transport_error','transport_failed'} or error.code=='upload_http_error' and error.diagnostics.get('http_status') in {429,500,502,503,504})
                   and (stage in {'observe','heartbeat','upload','bootstrap_probe','job_probe'} or reconciling_launch) and bounds) or (store_busy and bounds)
        if not transient and current['phase'] not in {'closed','stopping','released_unsettled'}:
            self.save(state['id'],phase='closing',reason='watchdog_state_lost',close_requested=True,terminal_fault=current.get('terminal_fault') or fault)
        # No partial cycle (including a successful job poll after a session
        # fault) can renew last_tick or reset the shared recovery budget.

    def tick(self, state):
        from colab_service import BridgeError
        state = self.get(state['id'])
        now = self.s.clock()
        try:self.recovery_remaining(state)
        except BridgeError:state=self.get(state['id'])
        if state['phase'] == 'closed':
            return self.release(state) if self.closed_needs_reconciliation(state) else None
        if state['phase'] in {'closing','stopping','released_unsettled'}:
            return self.release(state)
        if state.get('runtime_id'):
            if now-state['last_tick'] > HEARTBEAT or now >= state['deadline_epoch']:
                return self.release(self.save(state['id'], phase='closing', reason='watchdog_or_hard_cap'))
            state = self.observe(state) if now-state.get('usage_checked_at',0) >= 60 else state
            if state['units_observed'] >= state['settings']['max_units'] or state.get('last_balance',1) <= 0:
                return self.release(self.save(state['id'], phase='closing', reason='cu_cap'))
            combined=state['phase'] in {'bootstrap','bootstrap_launch'}
            if combined:
                self.bootstrap(state)  # Validate identity/progress BEFORE renewing heartbeat.
                state=self.get(state['id'])
                if state['phase']=='closing':return self.release(state)
            if state.get('watchdog_armed') and not combined:
                self.operation(state,'heartbeat')
                from scripts.colab_session_watchdog import heartbeat
                assert state.get('watchdog_runtime_id') == state['runtime_id']
                ack = self.s._exec(state, heartbeat(state['id'],state.get('watchdog_identity'),state['last_tick']))
                assert ack.get('heartbeat') and ack.get('watchdog_identity') == state['watchdog_identity']
            state = self.get(state['id'])
            if state.get('close_requested') and not state.get('active_job'):
                return self.release(self.save(state['id'],phase='closing'))
        elif state.get('close_requested'):
            return self.release(self.save(state['id'],phase='closing'))
        if state['phase']=='allocation_absent':return self.next_gpu(state)
        if state['phase'] in {'requesting','allocating','uncertain'}:
            return self.allocate(state)
        if state['phase'] in {'arming','uploading'}:
            return self.bootstrap(state)
        if state['phase'] == 'ready' and not state.get('active_job'):
            with self.s.db() as db:
                jobs = [json.loads(r[0]) for r in db.execute('SELECT state FROM jobs WHERE owner=? ORDER BY rowid',(state['owner'],))]
            waiting = [j for j in jobs if j.get('session_job') and j['phase']=='queued' and not j.get('cancel_requested') and j.get('session_id') == state['id']]
            if waiting:
                return self.attach(state, waiting[0])
            if now >= state.get('idle_since',now) + state['settings']['idle_minutes']*60:
                return self.release(self.save(state['id'],phase='closing',reason='idle_timeout'))

    def allocation_allowed(self,db,state,phase):
        from colab_service import encoded, TERMINAL
        if state.get('close_requested') or state['phase']!=phase:return False
        if state.get('admission_created'):
            jobs=[json.loads(r[0]) for r in db.execute('SELECT state FROM jobs WHERE owner=?',(state['owner'],))]
            if not any(j.get('session_id')==state['id'] and not j.get('cancel_requested') and j['phase'] not in TERMINAL for j in jobs):
                state.update(close_requested=True,reason='queued_jobs_cancelled')
                db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
                return False
        return True

    def allocate(self, state):
        self.operation(state,'allocation')
        from colab_service import BridgeError
        state=self.get(state['id'])
        now = self.s.clock()
        if state.get('close_requested'):return self.release(self.save(state['id'],phase='closing'))
        if state['phase'] in {'allocating','uncertain'}:
            grace_at=self.absence_grace_at(state)
            result = self.s._call(state,'runtime_lookup',allocation_id=state['allocation_id'],gpu=state['gpu'])
            if result.get('found'):
                return self.assigned(state,result)
            if self.s.clock() < grace_at+150:
                return
            # Existing bridge lookup is the authority for absence, after grace.
            return self.next_gpu(state)
        if now < state['retry_at']:
            return
        if state.get('gpu_index',0) == 0:
            with self.s.db() as db:
                db.execute('BEGIN IMMEDIATE')
                state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
                if not self.allocation_allowed(db,state,'requesting'):return
                rows = db.execute('SELECT at FROM session_attempts WHERE at>? ORDER BY at',(now-86400,)).fetchall()
                # Include pre-r35 controller attempts, across owners/accounts.
                legacy = [json.loads(r[0]) for r in db.execute('SELECT state FROM jobs')]
                old = [j.get('allocation_attempted_at') for j in legacy for _ in range(max(1,j.get('allocation_attempts',1))) if not j.get('session_job') and finite(j.get('allocation_attempted_at')) and j['allocation_attempted_at']>now-86400]
                dates = [r[0] for r in rows]+old
                if state['attempts'] >= 12:
                    return self._close_after_transaction(db,state,'attempt_cap')
                if len(dates) >= 24:
                    return self._close_after_transaction(db,state,'rolling_24h_cap')
                elif dates and now-max(dates)<600:
                    retry = max(dates)+600
                else:
                    retry = None
                if retry is not None:
                    from colab_service import encoded
                    state.update(retry_at=retry)
                    db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
                    return
                db.execute('INSERT INTO session_attempts VALUES (?,?,?)',(str(uuid.uuid4()),state['id'],now))
                from colab_service import encoded
                state.update(attempts=state['attempts']+1,round_at=now)
                db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
        gpu = GPUS[state['gpu_index']]
        status=self.s._call(state,'account_status')
        balance=status.get('balance_compute_units')
        if not status.get('authenticated') or not finite(balance) or balance<=0:
            return self.save(state['id'],phase='closing',reason='account_unavailable')
        from colab_service import encoded
        # Commit the original lease identity/deadline before ANY allocation POST.
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            if not self.allocation_allowed(db,state,'requesting'):return
            allocation_id=str(uuid.uuid4())
            history=state.get('allocation_history',[])+[{'allocation_id':allocation_id,'gpu':gpu,'at':now,'round':state['attempts']}]
            state.update(phase='allocating',gpu=gpu,allocation_id=allocation_id,allocation_at=now,
                         dispatch_intent_id=None,dispatch_intent_at=None,absence_grace_id=None,absence_grace_at=None,allocation_resolved_id=None,
                         deadline_epoch=now+state['settings']['hard_hours']*3600,
                         last_balance=balance,allocation_history=history)
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
        # Commit dispatch intent with a fresh cancellation check. Never hold a
        # SQLite writer across an RPC: close/settings must remain available.
        # A crash after this commit is lookup-only under the original UUID.
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            if not self.allocation_allowed(db,state,'allocating'):return
            state.update(dispatch_intent_id=state['allocation_id'],dispatch_intent_at=self.s.clock())
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
        try:
            self.operation(state,'allocation')
            result=self.s._call(state,'runtime_allocate',allocation_id=state['allocation_id'],gpu=gpu)
        except BridgeError as error:
            from scripts.colab_allocation_diagnostics import sanitize
            absent=error.code in {'allocation_unavailable','allocation_rejected'} and not error.ambiguous
            state=self.save(state['id'],phase='allocation_absent' if absent else 'uncertain',
                            allocation_diagnostics=sanitize(error.diagnostics))
            if state.get('close_requested'):return self.release(self.save(state['id'],phase='closing'))
            if absent:return self.next_gpu(state)
            return
        if result and result.get('found') and result.get('runtime_id'):return self.assigned(state,result)
        state=self.save(state['id'],phase='uncertain')
        if state.get('close_requested'):return self.release(self.save(state['id'],phase='closing'))

    @staticmethod
    def _close_after_transaction(db,state,reason):
        from colab_service import encoded
        state.update(phase='closing',reason=reason)
        db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))

    def next_gpu(self,state):
        index = state['gpu_index']+1
        exhausted = index == len(GPUS)
        self.save(state['id'],phase='requesting',gpu_index=0 if exhausted else index,
                  allocation_id=None, retry_at=max(self.s.clock(),state['round_at']+600) if exhausted else 0)

    def assigned(self,state,result):
        self.operation(state,'allocation')
        from colab_service import encoded
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            # A bound job may have been cancelled while allocation was in flight.
            allowed=self.allocation_allowed(db,state,state['phase'])
            assigned_gpu=result.get('accelerator',state.get('gpu'))
            state.update(runtime_id=result['runtime_id'],assigned_gpu=assigned_gpu if assigned_gpu in GPUS else None,phase='arming' if allowed else 'closing',assigned_at=self.s.clock())
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
        # Missing old lease deadlines fail closed; recovery never extends one.
        if (state.get('close_requested') or self.s.clock()>=state.get('deadline_epoch',0)
                or self.s.clock()-state.get('allocation_at',0)>HEARTBEAT
                or result.get('accelerator',state['gpu']) not in GPUS):
            return self.release(self.save(state['id'],phase='closing',reason='lease_expired_or_invalid'))
        state=self.save(state['id'],gpu=result.get('accelerator',state['gpu']),assigned_gpu=result.get('accelerator',state['gpu']))
        try:
            self.arm(state)
        except Exception:
            return self.release(self.save(state['id'],phase='closing',reason='watchdog_arm_failed'))

    def arm(self,state):
        self.operation(state,'arm')
        from scripts.colab_session_watchdog import build
        # Probe only after the watchdog is armed, including owner pre-open.
        # Observed startup hardware is distinct from worker-verified gpu_name.
        probe=("\nimport shutil,re,sysconfig,importlib.util\ninfo={}\n"+
               "try:\n info.update(disk_free_gib=round(shutil.disk_usage(p).free/1024**3,2),python_version='.'.join(map(str,sys.version_info[:3])))\n"+
               " info['system_python']={'version':info['python_version'],'ensurepip_available':importlib.util.find_spec('ensurepip') is not None,'headers_available':(Path(sysconfig.get_path('include'))/'Python.h').is_file()}\n"+
               " raw=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True,stderr=subprocess.DEVNULL,timeout=10).strip()\n"+
               " name,memory=raw.rsplit(',',1)\n if re.fullmatch(r'NVIDIA [A-Za-z0-9 .()_-]{1,100}',name.strip()):info.update(gpu_name=name.strip(),vram_mib=int(memory))\n"+
               "except Exception:pass\n"+
               f"print('WEB_COMFYUI_JSON='+json.dumps({{'armed':True,'session_id':{state['id']!r},'startup_telemetry':info,'watchdog_identity':identity}}))")
        ack=self.s._exec(state,build(state['id'],state['deadline_epoch'])+probe)
        if ack.get('session_id')!=state['id'] or not ack.get('armed'):raise ValueError('watchdog_ack_invalid')
        from scripts.colab_session_diagnostics import telemetry
        return self.save(state['id'],watchdog_armed=True,watchdog_identity=ack['watchdog_identity'],watchdog_runtime_id=state['runtime_id'],phase='uploading',startup_telemetry=telemetry(ack.get('startup_telemetry')))

    def freeze(self,state):
        from colab_service import ROOT, private_write, digest, encoded
        directory = self.s.root/'sessions'/state['id']
        entries=[]
        for path in sorted((ROOT/'scripts/colab_remote').rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and (path.suffix in {'.py','.json'} or path.name in {'LICENSE.upstream','NOTICE.md'}):
                entries.append(('runner/'+path.relative_to(ROOT/'scripts/colab_remote').as_posix(),path.read_bytes()))
        entries.append(('session_worker.py',(ROOT/'scripts/colab_session_worker.py').read_bytes()))
        entries.append(('session_bootstrap.py',(ROOT/'scripts/colab_session_bootstrap.py').read_bytes()))
        entries.append(('colab_session_diagnostics.py',(ROOT/'scripts/colab_session_diagnostics.py').read_bytes()))
        entries.append(('colab_session_probe.py',(ROOT/'scripts/colab_session_probe.py').read_bytes()))
        entries.append(('colab_session_inputs.py',(ROOT/'scripts/colab_session_inputs.py').read_bytes()))
        entries.append(('colab_session_voice.py',(ROOT/'scripts/colab_session_voice.py').read_bytes()))
        entries.append(('colab_session_dependencies.py',(ROOT/'scripts/colab_session_dependencies.py').read_bytes()))
        for path in sorted((ROOT/'scripts/colab_session_overlays').iterdir()):
            if path.is_file():entries.append(('colab_session_overlays/'+path.name,path.read_bytes()))
        catalog = self.s.root.parent/'compute-parity/bundles-r29'
        plans=[];node_requirements={}
        from scripts.colab_session_diagnostics import plan_id
        for path in sorted(catalog.glob('*.json')):
            bundle=json.loads(path.read_text())
            if not bundle.get('bundle_sha256'):
                continue
            from compute_parity.contracts import validate_bundle
            validate_bundle(bundle)
            plan={k:bundle[k] for k in ('core','node_packs','runtime_lock','feature','voice_runtime') if k in bundle}
            plan['models']=[m for m in bundle['models'] if not m.get('private')]
            plans.append(plan)
            node_requirements.setdefault(plan_id(plan),set()).update(n['class_type'] for n in bundle['graph'].values())
        if not plans:
            raise ValueError('session_catalog_missing')
        entries.append(('bootstrap.json',encoded(plans).encode()))
        entries.append(('node-requirements.json',encoded({k:sorted(v) for k,v in node_requirements.items()}).encode()))
        uploads=[]
        for name,raw in entries:
            private_write(directory/name,raw)
            uploads.append({'path':name,'size':len(raw),'sha256':digest(raw)})
        from scripts.colab_session_diagnostics import plan_id
        from scripts.colab_session_archive import pack
        archive=pack(entries);private_write(directory/'bootstrap.tar',archive)
        return self.save(state['id'],bootstrap_archive={'sha256':digest(archive),'size':len(archive)},archive_uploaded=False,archive_verified=False,uploads=uploads,upload_index=0,plan_catalog={plan_id(p):p for p in plans},prepared_plans=[],failed_plans={})

    def waiting(self,state):
        from colab_service import TERMINAL
        with self.s.db() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT state FROM jobs WHERE owner=? ORDER BY rowid',(state['owner'],))]
        return [j for j in rows if j.get('session_id')==state['id'] and j['phase']=='queued' and not j.get('cancel_requested')]

    def fail_plan(self,state,diagnostic,reason):
        self.operation(state,'bootstrap')
        from colab_service import encoded
        from scripts.colab_session_diagnostics import sanitize_diagnostics
        diagnostic=sanitize_diagnostics(diagnostic)
        pid=state.get('bootstrap_plan_id')
        if not pid:return self.save(state['id'],phase='closing',reason=reason,bootstrap_diagnostics=diagnostic)
        # A failed plan never contaminates another plan's isolated environment.
        # The process must be gone before preparing or attaching anything else.
        remote='/content/castle-sessions/'+state['id']+'/plans/'+pid
        code=("import json,os,signal,time\nfrom pathlib import Path\n"+f"root=Path({remote!r})\n"+
              "p=root/'bootstrap-process.json';i=json.loads(p.read_text()) if p.exists() else {}\n"+
              "def alive():\n try:\n  s=Path('/proc/'+str(i['pid'])+'/stat').read_text().split(') ',1)[1].split();return s[0]!='Z' and s[19]==i['start_ticks']\n except (OSError,KeyError):return False\n"+
              "def members():\n result=[]\n for p in Path('/proc').glob('[0-9]*/stat'):\n  try:\n   s=p.read_text().split(') ',1)[1].split()\n   if s[0]!='Z' and int(s[2])==i.get('pid'):result.append(s)\n  except (OSError,ValueError,IndexError):pass\n return result\n"+
              "if alive():os.killpg(i['pid'],signal.SIGKILL)\n"+
              "for _ in range(50):\n if not members():break\n time.sleep(.1)\n"+
              "probe=root/'node-probe-attempt.json';clean=root/'node-probe-clean.json';probe_clean=True\n"+
              "if probe.exists():\n a=json.loads(probe.read_text());c=json.loads(clean.read_text()) if clean.exists() else {}\n probe_clean=bool(a.get('attempt')) and a.get('plan_id')==root.name and c.get('attempt')==a['attempt'] and c.get('plan_id')==a['plan_id'] and c.get('clean') is True\n"+
              "print('WEB_COMFYUI_JSON='+json.dumps({'clean':bool(i) and not members() and probe_clean}))")
        if not self.s._exec(state,code).get('clean'):
            return self.save(state['id'],phase='closing',reason='bootstrap_process_lost',bootstrap_diagnostics=diagnostic)
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            failed=dict(current.get('failed_plans',{}))
            excerpts=dict(current.get('failed_plan_private_stderr',{}))
            evidence=dict(current.get('failed_plan_private_dependency',{}))
            if pid not in failed:evidence[pid]=current.get('private_dependency_diagnostics',{})
            if pid not in failed:excerpts[pid]=current.get('private_bootstrap_stderr','')
            failed[pid]=diagnostic
            for row in db.execute('SELECT id,state FROM jobs WHERE owner=?',(state['owner'],)).fetchall():
                job=json.loads(row[1])
                if job.get('session_id')==state['id'] and job['phase']=='queued' and job.get('bootstrap_plan_id')==pid:
                    job.update(phase='cancelled' if job.get('cancel_requested') or current.get('reason')=='owner_close' else 'failed',released=True,units_observed=0,remote_diagnostics=diagnostic,private_bootstrap_stderr=excerpts.get(pid,''),private_dependency_diagnostics=evidence.get(pid,{}),
                               gpu=current.get('assigned_gpu',current.get('gpu')),assigned_gpu=current.get('assigned_gpu',current.get('gpu')),gpu_name=current.get('gpu_name'))
                    db.execute('UPDATE jobs SET state=? WHERE id=?',(encoded(job),row[0]))
            current.update(failed_plans=failed,failed_plan_private_stderr=excerpts,failed_plan_private_dependency=evidence,phase='ready',bootstrap_plan_id=None,bootstrap_diagnostics=diagnostic,idle_since=self.s.clock())
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(current),state['id']))
        if not self.waiting(current):self.save(state['id'],phase='closing',reason=reason)

    def bootstrap(self,state):
        self.operation(state,'bootstrap')
        from colab_service import digest,private_write
        from scripts.colab_session_diagnostics import sanitize_diagnostics,telemetry
        session_remote='/content/castle-sessions/'+state['id']
        if state['phase']=='arming':return self.arm(state)
        if not state.get('uploads'):state=self.freeze(state)
        if state['phase']=='uploading':
            if state.get('bootstrap_archive'):
                if not state.get('archive_uploaded'):
                    self.operation(state,'upload')
                    path=self.s.root/'sessions'/state['id']/'bootstrap.tar';entry=state['bootstrap_archive']
                    if path.is_symlink() or path.stat().st_size!=entry['size'] or digest(path.read_bytes())!=entry['sha256']:raise ValueError('session_upload_changed')
                    if not state.get('upload_started_at'):state=self.save(state['id'],upload_started_at=self.s.clock())
                    self.s._call(state,'upload',runtime_id=state['runtime_id'],local_path=str(path),remote_path=session_remote+'/bootstrap.tar',max_bytes=32*1024*1024)
                    self.save(state['id'],archive_uploaded=True,upload_completed_at=self.s.clock(),upload_index=len(state['uploads']));return
                if not state.get('archive_verified'):
                    from scripts.colab_session_archive import verification_code
                    self.operation(state,'upload')
                    code=verification_code(session_remote,state['uploads'],state['bootstrap_archive']['sha256'])
                    ack=self.s._exec(state,code+"\nimport json\nprint('WEB_COMFYUI_JSON='+json.dumps({'archive_verified':True}))")
                    if not ack.get('archive_verified'):raise ValueError('archive_verification_failed')
                    state=self.save(state['id'],archive_verified=True,digest_verified_at=self.s.clock())
            else:
                # Existing r36 sessions retain their frozen per-file inventory.
                pos=state['upload_index']
                if pos<len(state['uploads']):
                    self.operation(state,'upload')
                    entry=state['uploads'][pos];path=self.s.root/'sessions'/state['id']/entry['path']
                    if path.is_symlink() or digest(path.read_bytes())!=entry['sha256']:raise ValueError('session_upload_changed')
                    self.s._call(state,'upload',runtime_id=state['runtime_id'],local_path=str(path),remote_path=session_remote+'/'+entry['path'],max_bytes=32*1024*1024)
                    self.save(state['id'],upload_index=pos+1);return
            self.operation(state,'bootstrap')
            if 'plan_catalog' in state and not state.get('bootstrap_plan_id'):
                waiting=self.waiting(state)
                if not waiting:return self.save(state['id'],phase='ready',idle_since=self.s.clock())
                pid=waiting[0].get('bootstrap_plan_id')
                if pid not in state['plan_catalog']:return self.save(state['id'],phase='closing',reason='bootstrap_plan_missing')
                state=self.save(state['id'],bootstrap_plan_id=pid)
            pid=state.get('bootstrap_plan_id')
            remote=session_remote+('/plans/'+pid if pid else '')
            self.save(state['id'],phase='bootstrap_launch',launch_at=self.s.clock(),bootstrap_progress=None,bootstrap_diagnostics={})
            verify=("import hashlib\nfrom pathlib import Path\n"+f"base=Path({session_remote!r});root=Path({remote!r});root.mkdir(parents=True,exist_ok=True,mode=0o700)\n"+
                    f"entries={state['uploads']!r}\n"+
                    "for e in entries:\n p=base/e['path']\n assert not p.is_symlink() and p.stat().st_size==e['size'] and hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256']\n")
            args=[session_remote+'/session_bootstrap.py','bootstrap',session_remote]+([pid] if pid else [])
            code=("import subprocess,sys,json,os\n"+verify+
                  "fd=os.open(root/'bootstrap-stderr.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600);os.fchmod(fd,0o600)\n"+
                  f"with os.fdopen(fd,'w') as err:p=subprocess.Popen([sys.executable]+{args!r},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=err,start_new_session=True)\n"+
                  "identity={'pid':p.pid,'start_ticks':Path('/proc/'+str(p.pid)+'/stat').read_text().split(') ',1)[1].split()[19]}\n"+
                  "temp=root/'bootstrap-launch.tmp';temp.write_text(json.dumps(identity));os.replace(temp,root/'bootstrap-process.json')\n"+
                  "print('WEB_COMFYUI_JSON='+json.dumps({'launched':True,**identity}))")
            identity=self.s._exec(state,code)
            self.save(state['id'],phase='bootstrap',bootstrap_identity={k:identity.get(k) for k in ('pid','start_ticks')});return
        pid=state.get('bootstrap_plan_id');remote=session_remote+('/plans/'+pid if pid else '')
        code=("import json,time\nfrom pathlib import Path\n"+f"root=Path({remote!r})\n"+
              "p=root/'bootstrap-status.json';r=json.loads(p.read_text()) if p.exists() else {}\n"+
              "i=root/'bootstrap-process.json';identity=json.loads(i.read_text()) if i.exists() else {}\n"+
              "alive=False\ntry:\n stat=Path('/proc/'+str(identity['pid'])+'/stat').read_text().split(') ',1)[1].split()\n alive=stat[0]!='Z' and stat[19]==identity['start_ticks']\nexcept (OSError,KeyError):pass\n"+
              "r.update(alive=alive,identity=identity)\n")
        if state.get('bootstrap_identity'):
            code+=f"alive=alive and identity=={state['bootstrap_identity']!r}\nr['alive']=alive\n"
        cycle=str(uuid.uuid4())
        code+=inspect.getsource(bootstrap_progress_fault)
        code+=f"probe_cycle={cycle!r}\nprobe_previous={state.get('bootstrap_progress')!r}\n"
        code+="r['probe_fault']=bootstrap_progress_fault(time.time(),r.get('progress_at'),probe_previous)\n"
        if state.get('watchdog_armed'):
            from scripts.colab_session_watchdog import heartbeat
            assert state.get('watchdog_runtime_id')==state['runtime_id']
            hb=heartbeat(state['id'],state.get('watchdog_identity'),state['last_tick'])
            hb=hb[:hb.rindex("print('WEB_COMFYUI_JSON=")]
            # The same RPC must prove fresh progress, a still-live lease, the
            # watchdog identity/lock, and a read-back of this cycle's heartbeat.
            code+=f"if time.time()<={state.get('launch_at',0)+1800!r} and r.get('phase')!='failed' and (alive or r.get('phase')=='ready') and r['probe_fault'] is None:\n"
            code+=f" lease_heartbeat=Path({session_remote!r})/'heartbeat'\n"
            code+=" try:\n  old_heartbeat=float(lease_heartbeat.read_text())\n  import math\n  lease_valid=math.isfinite(old_heartbeat) and -1<=time.time()-old_heartbeat<=300\n except (OSError,ValueError):lease_valid=False\n"
            code+=" if not lease_valid:r['probe_fault']='heartbeat_unavailable'\n else:\n"
            code+='\n'.join('  '+line for line in hb.splitlines())+"\n"
            code+=f"  assert float(lease_heartbeat.read_text())=={state['last_tick']!r}\n"
            code+="  r.update(heartbeat=True,watchdog_identity=actual,heartbeat_cycle=probe_cycle,heartbeat_at=float(lease_heartbeat.read_text()))\n"
        code+="print('WEB_COMFYUI_JSON='+json.dumps(r))"
        self.operation(state,'bootstrap_probe')
        result=self.s._exec(state,code);safe=telemetry(result);diagnostic=sanitize_diagnostics(result.get('diagnostics'))
        continuing=(result.get('phase')!='failed' and self.s.clock()<=state.get('launch_at',0)+1800
                    and (result.get('alive') or result.get('phase')=='ready'))
        if continuing:
            fault=bootstrap_progress_fault(self.s.clock(),result.get('progress_at'),state.get('bootstrap_progress'))
            if not fault and result.get('probe_fault'):
                fault='heartbeat_unavailable' if result['probe_fault']=='heartbeat_unavailable' else 'progress_remote_invalid'
            if not fault and state.get('watchdog_armed'):
                if result.get('heartbeat') is not True: fault='heartbeat_ack_missing'
                elif result.get('watchdog_identity')!=state['watchdog_identity']: fault='heartbeat_identity_mismatch'
                elif result.get('heartbeat_cycle')!=cycle: fault='heartbeat_cycle_mismatch'
                elif result.get('heartbeat_at')!=state['last_tick']: fault='heartbeat_timestamp_mismatch'
            if fault:
                from colab_service import BridgeError
                raise BridgeError('execution_uncertain',True,private_diagnostics={'bootstrap_probe':{'type':fault}})
        from scripts.colab_session_diagnostics import stderr_excerpt
        from scripts.colab_session_probe import private_diagnostics
        self.save(state['id'],private_bootstrap_stderr=stderr_excerpt(result.get('private_bootstrap_stderr','')),private_dependency_diagnostics=private_diagnostics(result.get('private_dependency_diagnostics',{})))
        self.save(state['id'],bootstrap_identity=result.get('identity'),bootstrap_progress=result.get('progress_at'),bootstrap_diagnostics=diagnostic,**safe)
        reason=None
        if result.get('phase')=='failed':reason='bootstrap_failed'
        elif self.s.clock()>state.get('launch_at',0)+1800:reason='bootstrap_timeout'
        elif result.get('phase')=='ready':
            if safe.get('gpu') not in GPUS or safe.get('vram_mib',0)<39000:raise ValueError('gpu_mismatch')
            current=self.get(state['id']) if pid else state
            return self.save(state['id'],phase='ready',prepared_plans=list(dict.fromkeys(current.get('prepared_plans',[])+([pid] if pid else []))),bootstrap_plan_id=None,idle_since=self.s.clock(),**safe)
        elif not result.get('alive'):reason='bootstrap_process_lost'
        elif not finite(result.get('progress_at')) or self.s.clock()-result['progress_at']>120:reason='bootstrap_stalled'
        if reason:
            diagnostic=sanitize_diagnostics({'stage':'session_bootstrap','error_code':reason if reason!='bootstrap_failed' else 'stage_failed','substage':'progress' if reason=='bootstrap_stalled' else 'process',**diagnostic})
            # Only a bounded structural/redacted tail crosses the bridge. It is
            # stored privately, never in session/public JSON or a receipt.
            code=("import sys,json\n"+f"sys.path.insert(0,{session_remote!r})\nfrom colab_session_diagnostics import redacted_tail\n"+
                  f"print('WEB_COMFYUI_JSON='+json.dumps({{'tail':redacted_tail({remote+'/bootstrap-stderr.log'!r})}}))")
            try:
                tail=self.s._exec(state,code).get('tail','')
                # Remote output is untrusted: enforce the same structural grammar.
                from scripts.colab_session_diagnostics import decode_tail
                private_write(self.s.root/'sessions'/state['id']/((pid or 'bootstrap')+'.stderr.txt'),decode_tail(tail).encode())
            except Exception:pass
            self.fail_plan(self.get(state['id']) if pid else state,diagnostic,reason)

    def attach(self,state,job):
        self.operation(state,'attach')
        from colab_service import encoded,private_write,digest
        if job['account_id']!=state['account_id']:
            return
        if 'plan_catalog' in state:
            pid=job.get('bootstrap_plan_id')
            if pid in state.get('failed_plans',{}):return self.fail_plan({**state,'bootstrap_plan_id':pid},state['failed_plans'][pid],'bootstrap_failed')
            if pid not in state.get('prepared_plans',[]):
                return self.save(state['id'],phase='uploading',bootstrap_plan_id=pid,launch_at=None,bootstrap_progress=None)
        state=self.observe(state)
        self.operation(state,'attach')
        deadline=min(self.s.clock()+job['max_minutes']*60,state['deadline_epoch'])
        path=self.s.root/'jobs'/job['id']/'manifest.json'
        manifest=json.loads(path.read_text());manifest['deadline_epoch']=deadline
        raw=encoded(manifest).encode();private_write(path,raw)
        uploads=[u for u in job['uploads'] if not u['path'].startswith('runner/')]
        for item in uploads:
            if item['path']=='manifest.json':item.update(size=len(raw),sha256=digest(raw))
        # A transaction binds the job lease and meter together. Restart never
        # binds the same runtime to a second active job.
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            if state.get('close_requested') or state.get('active_job') or state['phase']!='ready':return
            fresh=json.loads(db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
            if fresh.get('cancel_requested') or fresh['phase']!='queued':return
            job=fresh
            job.update(phase='preparing',session_id=state['id'],runtime_id=state['runtime_id'],runtime_ever_assigned=True,
                       gpu=state.get('assigned_gpu',state['gpu']),assigned_gpu=state.get('assigned_gpu',state['gpu']),gpu_name=state.get('gpu_name'),watchdog_armed=True,uploads=uploads,
                       deadline_epoch=deadline,manifest_sha256=digest(raw),units_observed=0)
            state.update(active_job=job['id'])
            db.execute('UPDATE jobs SET state=? WHERE id=?',(encoded(job),job['id']))
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))

    def finish_job(self,job):
        state=self.get(job['session_id'])
        if state['phase']=='closed':return
        if state['phase'] in {'closing','stopping','released_unsettled'} or job.get('outcome')!='completed':
            if state['phase'] not in {'stopping','released_unsettled'}:
                state=self.save(state['id'],phase='closing',reason='job_stopped')
            return self.release(state)
        # Completion/artifacts alone are not evidence that a killed worker ran
        # its finally block. Only the post-cleanup identity-bound marker is.
        code=("import json,shutil\nfrom pathlib import Path\n"+
              f"root=Path('/content/web-comfyui/{job['id']}');p=root/'reusable-clean.json'\n"+
              "marker=json.loads(p.read_text()) if p.is_file() and not p.is_symlink() else {}\n"+
              f"if marker.get('job_id')=={job['id']!r} and marker.get('manifest_sha256')=={job.get('manifest_sha256')!r} and marker.get('clean') is True:\n"+
              " assert not root.is_symlink()\n shutil.rmtree(root)\n assert not root.exists()\n"+
              "print('WEB_COMFYUI_JSON='+json.dumps(marker))")
        marker=self.s._exec(state,code)
        if (marker.get('job_id')!=job['id'] or marker.get('manifest_sha256')!=job.get('manifest_sha256')
                or marker.get('clean') is not True):
            return self.release(self.save(state['id'],phase='closing',reason='isolation_unproven'))
        balance=self.balance(state)
        from colab_service import encoded
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self._meter(db,state['id'],balance)
            job=json.loads(db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
            if state.get('active_job')!=job['id'] or job.get('released'):return
            job.update(phase='completed',released=True,session_runtime_retained=True)
            state.update(active_job=None,idle_since=self.s.clock())
            db.execute('UPDATE jobs SET state=? WHERE id=?',(encoded(job),job['id']))
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))

    def release(self,state):
        self.operation(state,'release')
        from colab_service import encoded, TERMINAL
        state=self.get(state['id'])
        if state['phase']=='closed':
            if not self.closed_needs_reconciliation(state):return
            state=self.save(state['id'],phase='closing',released=False,close_requested=True)
        runtime=state.get('runtime_id')
        if not runtime and state.get('allocation_id'):
            grace_at=self.absence_grace_at(state)
            result=self.s._call(state,'runtime_lookup',allocation_id=state['allocation_id'],gpu=state['gpu'])
            if result.get('found'):
                runtime=result['runtime_id'];assigned_gpu=result.get('accelerator',state.get('gpu'))
                state=self.save(state['id'],runtime_id=runtime,assigned_gpu=assigned_gpu if assigned_gpu in GPUS else None)
            elif self.s.clock()<grace_at+150:return
        if runtime and state['phase'] not in {'stopping','released_unsettled'}:
            try:balance=self.balance(state)
            except Exception:balance=None
            # Meter the active job and end its metering interval atomically,
            # before stop. Shutdown and every recovery delta belong to session.
            with self.s.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if balance is not None:state=self._meter(db,state['id'],balance)
                else:
                    state=json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
                    state['job_usage_unknown']=bool(state.get('active_job'))
                state.update(phase='stopping',active_job=None,usage_unknown=True)
                db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))
        if runtime and state['phase']!='released_unsettled':
            result=self.s._call(state,'runtime_stop',runtime_id=runtime)
            if not (result.get('stopped') or result.get('released')):return
            state=self.save(state['id'],phase='released_unsettled',released=True,usage_unknown=True)
        try:balance=self.balance(state) if runtime else None
        except Exception:return  # Remain reserved/unknown, retry observation only.
        # No receipt can observe terminal/released before the final meter and
        # session close commit together. A crash rolls the entire boundary back.
        with self.s.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self._meter(db,state['id'],balance,session_only=True) if balance is not None else json.loads(db.execute('SELECT state FROM work_sessions WHERE id=?',(state['id'],)).fetchone()[0])
            jobs=[json.loads(r[0]) for r in db.execute('SELECT state FROM jobs WHERE owner=?',(state['owner'],))]
            for job in jobs:
                if job.get('session_id')==state['id'] and job['phase'] not in TERMINAL:
                    if job['phase']=='queued':
                        job.update(remote_diagnostics=state.get('bootstrap_diagnostics',{}),private_dependency_diagnostics=state.get('private_dependency_diagnostics',{}),assigned_gpu=state.get('assigned_gpu'),gpu=state.get('assigned_gpu'),gpu_name=state.get('gpu_name'))
                    terminal_fault=state.get('terminal_fault') or (state.get('fault') if state.get('reason')=='watchdog_state_lost' else None)
                    if terminal_fault:
                        from scripts.colab_transport_diagnostics import fault_diagnostic
                        job.update(remote_diagnostics=fault_diagnostic(terminal_fault))
                    job.update(phase='cancelled' if job.get('cancel_requested') else job.get('outcome','failed' if terminal_fault else 'cancelled' if state.get('reason')=='owner_close' else 'failed'),released=True,
                               allocation_code=None if runtime else 'allocation_unavailable',
                               units_observed=None if state.get('job_usage_unknown') and job.get('runtime_id') else job.get('units_observed',0))
                    db.execute('UPDATE jobs SET state=? WHERE id=?',(encoded(job),job['id']))
            state.update(phase='closed',released=True,active_job=None,closed_at=self.s.clock(),usage_unknown=bool(state.get('job_usage_unknown')),
                         allocation_resolved_id=state.get('allocation_id'))
            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(state),state['id']))

    def sweep_leases(self, only=None):
        """External flock holder: lookup/stop/account observation only, never exec/POST."""
        for state in self.rows():
            if only is not None and state['id']!=only:continue
            if state['phase']=='closed':
                if self.closed_needs_reconciliation(state):self.release(state)
                continue
            if (state.get('runtime_id') or state.get('allocation_id')) and (
                    not state.get('watchdog_armed') or self.s.clock()>=state.get('deadline_epoch',0)
                    or self.s.clock()-state['last_tick']>HEARTBEAT
                    or state['phase'] in {'closing','stopping','released_unsettled'}):
                if state['phase'] not in {'stopping','released_unsettled'}:
                    state=self.save(state['id'],phase='closing',reason='independent_lease_sweep')
                self.release(state)

