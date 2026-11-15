"""Membership-native admission and derived historical no-execution exemptions."""
from .contracts import fail

SHARED_REASON='企業共享金鑰不接受原生工作；請在 RunningHub 建立會員金鑰後更換'
UNKNOWN_REASON='尚未確認 RunningHub 金鑰類型；請先在我的運算平台測試連線'

def require_membership(account_type):
    if account_type=='SHARED':fail('rh_membership_required',SHARED_REASON)
    if account_type!='NORMAL':fail('rh_key_type_unknown',UNKNOWN_REASON)

def no_execution_attempt(job):
    if job.get('lane')!='runninghub' or job.get('state')!='closed_no_execution_evidence':return False
    if any(job.get(k) for k in ('remote_id','prompt_id','taskId','artifacts','actual_cost')):return False
    d=job.get('diagnostics') or {};e=job.get('closure_evidence') or {}
    if not isinstance(d,dict) or not isinstance(e,dict):return False
    if (type(d.get('http_status')) is not int or d['http_status']!=200 or d.get('stage')!='id_parse'
        or d.get('prompt_id_present') is not False or d.get('transport_error_type') is not None):return False
    # r31/r32 receipts predate the named error code. Their durable structural
    # fields establish the same missing-ID response, without rewriting history.
    missing=(d.get('error_code') in ('empty_response_object','missing_prompt_id') or
             (not d.get('error_code') and d.get('response_keys')==[] and d.get('data_keys')==[]))
    return bool(missing and e.get('queue_absent') is True and e.get('history_absent') is True
                and e.get('unexplained_drop') is False and e.get('new_submissions')==0
                and e.get('account')==job.get('account') and job.get('account'))

def exempt_attempts(jobs):
    return {j['request_id'] for j in jobs if no_execution_attempt(j)}
