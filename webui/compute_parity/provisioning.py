"""Read-only Colab evidence, joined by owner/request/account; never rewrite history.

Legacy Colab controllers retain runtime_id after assignment, including cleanup.
Absent runtime_id plus no execution progress therefore proves never assigned for
those rows. New controllers also retain runtime_ever_assigned monotonically.
Missing or contradictory evidence never earns a cap exemption.
"""
import contextvars
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

COLAB_BUSY_RAISES = contextvars.ContextVar('colab_busy_raises', default=False)

class ColabReadBusy(sqlite3.OperationalError):
    pass

def _sqlite_busy(error):
    message = str(error).lower()
    return 'locked' in message or 'busy' in message

TERMINAL={'failed','cancelled'}
PROGRESS=('remote_phase','launch_requested_at','watchdog_armed','upload_index','runtime_ever_assigned')

def numeric(value):
    return type(value) in (int,float) and math.isfinite(value)

def states(path, owner, *, timeout=30):
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=timeout)) as db:
            records={rid:(account,json.loads(raw)) for rid,account,raw in db.execute('SELECT id,account_id,state FROM jobs WHERE owner=?',(owner,))}
            sessions={}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_sessions'").fetchone():
                sessions={sid:json.loads(raw) for sid,raw in db.execute('SELECT id,state FROM work_sessions WHERE owner=?',(owner,))}
            for account,state in records.values():
                state['_evidence_owner']=owner
                if state.get('session_id'):state['_session_evidence']=sessions.get(state['session_id'])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='request_claims'").fetchone():
                for rid,claim_state,outcome in db.execute('SELECT id,state,outcome FROM request_claims WHERE owner=?',(owner,)):
                    claim={'state':claim_state,'outcome':outcome,'owner':owner}
                    if rid in records:records[rid][1]['_claim_evidence']=claim
                    elif not db.execute('SELECT 1 FROM jobs WHERE id=?',(rid,)).fetchone():
                        records[rid]=(None,{'id':rid,'_evidence_owner':owner,'_job_absent':True,'_claim_evidence':claim})
            return records
    except sqlite3.OperationalError as error:
        if COLAB_BUSY_RAISES.get() and _sqlite_busy(error):
            raise ColabReadBusy(str(error)) from error
        return {}
    except (sqlite3.Error,ValueError,TypeError,AttributeError):
        return {}

def provisioning_only(job,record):
    if job.get('lane')!='colab' or not record:return False
    account,state=record
    if state.get('session_id'):
        session=state.get('_session_evidence')
        if not isinstance(session,dict) or session.get('id')!=state['session_id']:return False
        if session.get('runtime_id') or session.get('launch_at') or session.get('bootstrap_identity') or session.get('prepared_plans'):return False
        if session.get('phase')!='closed' or session.get('released') is not True or session.get('usage_unknown') is not False:return False
        if not numeric(session.get('units_observed')) or session['units_observed']!=0:return False
    elif state.get('session_job'):return False
    cost=job.get('actual_cost')
    return (job.get('account')==account and state.get('account_id')==account
            and state.get('id')==job.get('request_id') and job.get('remote_id')==state.get('id')
            and job.get('state') in TERMINAL and state.get('phase') in TERMINAL
            and state.get('runtime_id') is None and not any(state.get(k) for k in PROGRESS)
            and not state.get('artifacts') and not job.get('artifacts')
            and state.get('released') is True
            and numeric(state.get('units_observed')) and state['units_observed']==0
            and isinstance(cost,dict) and cost.get('unit')=='CU'
            and numeric(cost.get('amount')) and cost['amount']==0)

def classify(rows,path,owner,*,evidence=None):
    if evidence is None:evidence=states(path,owner) if any(j.get('lane')=='colab' for j in rows) else {}
    return {j['request_id'] for j in rows if provisioning_only(j,evidence.get(j['request_id']))}

# Cap-only evidence. Do NOT use this set for lane-stop or allocation retry limits.
PRE_READY_REASONS={
    'bootstrap_failed','bootstrap_process_lost','bootstrap_stalled',
    'bootstrap_timeout','bootstrap_plan_missing','watchdog_state_lost',
    'watchdog_arm_failed','account_unavailable','job_stopped',
}
EXECUTION_EVIDENCE=(
    'runtime_id','runtime_ever_assigned','watchdog_armed','launch_requested_at',
    'execution_started_at','execution_start','started_at','prompt_id','prompt_ids',
    'remote_phase','executing_at','execution_id',
)

def pre_ready_infrastructure(job,record):
    """Positive, owner-bound pre-ready evidence; missing fields stay counted.

    A session job acquires runtime_id/watchdog_armed only in attach(), after its
    plan enters prepared_plans. That monotone attachment evidence, the complete
    plan inventory and terminal meter jointly distinguish bootstrap overhead
    from job execution. closed+released+known meter is the session's settlement
    contract, including older rows without a separate settlement_status field.
    """
    if job.get('lane')!='colab' or not record:return False
    account,state=record
    session=state.get('_session_evidence')
    if not isinstance(session,dict):return False
    owner=state.get('_evidence_owner')
    if not owner or job.get('owner')!=owner or session.get('owner')!=owner:return False
    if not account or job.get('account')!=account or state.get('account_id')!=account or session.get('account_id')!=account:return False
    rid=job.get('request_id');sid=state.get('session_id');plan=state.get('bootstrap_plan_id')
    if not rid or state.get('id')!=rid or job.get('remote_id')!=rid:return False
    if not sid or session.get('id')!=sid or state.get('session_job') is not True:return False
    if session.get('phase')!='closed' or session.get('released') is not True or session.get('usage_unknown') is not False:return False
    if session.get('job_usage_unknown') or session.get('active_job') is not None:return False
    if session.get('allocation_id') and not session.get('runtime_id') and session.get('allocation_resolved_id')!=session['allocation_id']:return False
    if session.get('settlement_status','settled')!='settled' or session.get('reconciliation_pending'):return False
    if any(not numeric(session.get(k)) or session[k]<0 for k in ('units_observed','session_units','job_units')):return False
    if job.get('settlement_status','settled')!='settled':return False
    if job.get('state') not in TERMINAL or state.get('phase') not in TERMINAL or state.get('released') is not True:return False
    if any(state.get(k) or job.get(k) for k in EXECUTION_EVIDENCE):return False
    if any(k in row and row[k] is not None for row in (job,state)
           for k in ('execution_started_at','execution_start','executing_at','started_at')):return False
    if state.get('artifacts')!=[] or job.get('artifacts')!=[]:return False
    cost=job.get('actual_cost')
    if not numeric(state.get('units_observed')) or state['units_observed']!=0:return False
    if not isinstance(cost,dict) or cost.get('unit')!='CU' or not numeric(cost.get('amount')) or cost['amount']!=0:return False
    prepared=session.get('prepared_plans');catalog=session.get('plan_catalog')
    if not isinstance(prepared,list) or not isinstance(catalog,dict) or not isinstance(plan,str) or not plan or plan not in catalog or plan in prepared:return False
    if any(not isinstance(pid,str) for pid in prepared):return False
    if session.get('reason') not in PRE_READY_REASONS:return False
    # A bootstrap failure must identify this plan, not a different plan in the
    # same session. Watchdog faults may interrupt an unprepared queued plan.
    if session['reason'].startswith('bootstrap_'):
        failed=session.get('failed_plans',{})
        diagnostic=failed.get(plan) if isinstance(failed,dict) else None
        if not diagnostic and session.get('bootstrap_plan_id')==plan:diagnostic=session.get('bootstrap_diagnostics')
        if not isinstance(diagnostic,dict) or diagnostic.get('stage')!='session_bootstrap':return False
    if session['reason'] in {'watchdog_state_lost','job_stopped'}:
        fault=session.get('terminal_fault') if session['reason']=='job_stopped' else session.get('terminal_fault') or session.get('fault')
        if not isinstance(fault,dict) or fault.get('stage') not in {'observe','heartbeat','upload','bootstrap','bootstrap_probe'} or not fault.get('code'):return False
    return True

def admission_only(job,record):
    """Terminal server claim and settled zero CU; absence alone proves nothing."""
    if job.get('lane')!='colab' or not record:return False
    account,state=record;claim=state.get('_claim_evidence')
    if not isinstance(claim,dict) or claim.get('state')!='terminal':return False
    if not job.get('owner') or claim.get('owner')!=job['owner'] or state.get('_evidence_owner')!=job['owner']:return False
    if state.get('id')!=job.get('request_id') or claim.get('outcome')!=job.get('state'):return False
    if job.get('state') not in {'rejected','closed_no_execution_evidence'} or job.get('failure_kind')!='admission':return False
    if job.get('settlement_status')!='settled' or job.get('artifacts')!=[]:return False
    cost=job.get('actual_cost')
    if not isinstance(cost,dict) or cost.get('unit')!='CU' or not numeric(cost.get('amount')) or cost['amount']!=0:return False
    if any(job.get(k) for k in EXECUTION_EVIDENCE):return False
    if any(k in job and job[k] is not None for k in ('execution_started_at','execution_start','executing_at','started_at')):return False
    if state.get('_job_absent') is True:return True
    # A retained job must independently prove terminal, released, zero usage,
    # and no monotone attach/execution evidence. Never infer from a reason alone.
    return (account==job.get('account')==state.get('account_id') and
            state.get('phase') in TERMINAL and state.get('released') is True and
            state.get('artifacts')==[] and numeric(state.get('units_observed')) and state['units_observed']==0 and
            not any(state.get(k) for k in EXECUTION_EVIDENCE))

def cap_exemptions(rows,path,owner,*,evidence=None):
    """Single cap policy for admission and owner reports; never mutates rows."""
    if evidence is None:evidence=states(path,owner) if any(j.get('lane')=='colab' for j in rows) else {}
    result={rid:'provisioning_only' for rid in classify(rows,path,owner,evidence=evidence)}
    from .runninghub_native import exempt_attempts
    result.update({rid:'native_no_execution' for rid in exempt_attempts(rows)})
    for job in rows:
        if pre_ready_infrastructure(job,evidence.get(job['request_id'])):
            result[job['request_id']]='pre_ready_infrastructure'
        if admission_only(job,evidence.get(job['request_id'])):
            result[job['request_id']]='admission_only'
    return result

def remaining_attempts(rows,case_id,lane,exemptions):
    return max(0,3-sum(j['case_id']==case_id and j['lane']==lane and
                      j['request_id'] not in exemptions for j in rows))

def retry_window(rows,exempt,now,records=None):
    # Exempt historical attempts plus unresolved reservations consume retry slots.
    # Count across cases, credential rotations and epochs. BEGIN IMMEDIATE in
    # Journal.reserve serializes admission and insertion, including the sixth slot.
    attempts=[j for j in rows if j['request_id'] in exempt or j['state'] not in
              {'completed','failed','cancelled','rejected','quality_rejected'}]
    records=records or {}
    dates=[];weighted=[]
    for job in attempts:
        state=records.get(job['request_id'],(None,{}))[1]
        # An allocation can start well after reservation; never shorten spacing
        # by using only the earlier journal timestamp.
        times=[v for v in (job.get('created_at'),state.get('allocation_attempted_at')) if numeric(v)]
        if times:
            at=max(times);dates.append(at)
            count=state.get('allocation_attempts',1)
            # Older controllers could make two explicit-no-assignment allocations
            # under one job. Count both conservatively at the last allocation time.
            count=count if type(count) is int and count>0 else 1
            weighted.append((at,count))
    recent=sum(count for at,count in weighted if at>now-86400)
    return {'last_attempt_at':max(dates,default=None),'attempts_24h':recent,
            'remaining_24h':max(0,6-recent)}
