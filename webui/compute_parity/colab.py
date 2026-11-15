"""Native graphs use ColabService's OAuth, journal, lease, watchdog and cleanup."""
import base64
from pathlib import Path
from .contracts import compile_request, validate_bundle, voice_gate, fail
from .adapters import owned_inputs

def submit_native(service, owner, body, bundle, *, voice_consent=False):
    """Server-only entry: caller selects a trusted bundle; browser cannot send a graph."""
    validate_bundle(bundle); voice_gate(bundle,'colab',voice_consent)
    if body.get('gpu') not in {'A100','H100','G4'}:fail('gpu_required','P1 原生圖使用 A100／H100／G4；不允許縮小畫布或幀數避開記憶體不足')
    service.account(owner,body['account_id'])
    service.claim_request(owner,body['request_id'])
    spec={'type':'tts' if bundle['feature'] in {'voice-speak','voxcpm-tts'} else 'video', 'engine':'castle_native_p1',
          'bundle':bundle,'voice_consent':voice_consent}
    try:
        with service.account_guard(body['account_id'],blocking=False):
            return service._submit(owner,body,_native=spec)
    except BlockingIOError:
        from colab_service import BridgeError
        raise BridgeError('account_busy') from None

class ColabNative:
    lane='colab'
    def __init__(self, service, journal):
        self.service=service;self.journal=journal
        if service is not None:service.parity_journal_path=journal.path
    def submit(self,owner,request_id,bundle,files,**kwargs):
        with self.service.native_submission_guard(owner,request_id):
            result=self._submit(owner,request_id,bundle,files,**kwargs)
        return self.poll(owner,request_id) if result is None else result
    def _submit(self,owner,request_id,bundle,files,*,account_id,cap=None,quote=None,max_units=None,max_minutes=60,voice_consent=False):
        from .contracts import digest
        from .adapters import public
        validate_bundle(bundle);voice_gate(bundle,'colab',voice_consent);owned_inputs(bundle,files)
        body={'request_id':request_id,'account_id':account_id,'gpu':'A100','max_units':max_units,'max_minutes':max_minutes,
              'quote_amount':(quote or {}).get('amount',0),'files':[{'name':Path(i['name']).name,'data':base64.b64encode(files[i['slot']]).decode()} for i in bundle['inputs']]}
        ident=digest({'bundle':bundle['bundle_sha256'],'body':body,'consent':voice_consent})
        self.service.account(owner,account_id)
        if self.journal.get(owner,request_id) is None:self.service.claim_request(owner,request_id)
        job,new=self.journal.reserve(owner,request_id,'colab',account_id,bundle['case_id'],ident,quote,cap)
        if not new:return None
        self.journal.update(owner,request_id,state='submission_uncertain')
        try: remote=submit_native(self.service,owner,body,bundle,voice_consent=voice_consent)
        except Exception as error:
            from colab_inputs import ADMISSION_MESSAGES
            from colab_service import ColabError,BridgeError
            code=error.code if isinstance(error,ColabError) and error.code in ADMISSION_MESSAGES else 'admission_unavailable'
            from scripts.colab_session_diagnostics import ERRORS,sanitize_diagnostics
            diagnostic=sanitize_diagnostics({'stage':'admission','error_code':code,
                'error_type':type(error).__name__ if type(error).__name__ in ERRORS else 'Exception',
                'http_status':getattr(error,'diagnostics',{}).get('http_status') if isinstance(error,BridgeError) else None})
            from scripts.colab_private_diagnostics import capture
            private=capture(error)
            if isinstance(error,BridgeError):private['bridge']=error.private_diagnostics
            self.journal.update(owner,request_id,diagnostics=diagnostic,private_admission_error=private)
            try:durable=self.service.find_job(owner,request_id)
            except Exception as lookup_error:
                return public(self.journal.update(owner,request_id,diagnostics={'stage':'admission','error_code':'admission_lookup_failed'},private_admission_lookup_error=capture(lookup_error)))
            if durable is not None:return self._record(owner,request_id,self.service.public_job(durable),diagnostic)
            return self._close(owner,request_id,state='rejected',failure_kind='admission',root_cause=code,
                error={'code':code,'message':ADMISSION_MESSAGES[code]},message=ADMISSION_MESSAGES[code],diagnostics=diagnostic,
                actual_cost={'unit':'CU','amount':0},settlement_status='settled',cancel_supported=False)
        return self._record(owner,request_id,remote)
    def _close(self,owner,rid,**changes):
        from .adapters import public
        try:
            closed,remote=self.service.close_admission(self.journal,owner,rid,**changes)
            if remote is not None:return self._record(owner,rid,self.service.public_job(remote))
            return public(closed)
        except Exception as error:
            from scripts.colab_private_diagnostics import capture
            return public(self.journal.update(owner,rid,diagnostics={'stage':'admission','error_code':'admission_lookup_failed'},private_admission_lookup_error=capture(error)))
    def _record(self,owner,rid,job,admission_diagnostic=None):
        from .adapters import public
        state={'completed':'completed','failed':'failed','cancelled':'cancelled','allocation_uncertain':'submission_uncertain','cleanup_pending':'cancelling'}.get(job['phase'],'running')
        from scripts.colab_session_diagnostics import sanitize_diagnostics
        diagnostic=sanitize_diagnostics(job.get('diagnostics')) or admission_diagnostic or {}
        from scripts.colab_allocation_diagnostics import sanitize as allocation_safe
        allocation_code=job.get('allocation_code')
        if job.get('session_id'):
            session=self.service.owner_session(owner,job['session_id'])
            job=self.service.project_session_gpu(owner,job)
            if job['phase']=='queued' and session.get('phase') in {'bootstrap','bootstrap_launch'}:
                diagnostic=sanitize_diagnostics(session.get('bootstrap_diagnostics'))
            if job['phase']=='queued' and session.get('phase') in {'allocating','uncertain'}:
                allocation_code='allocation_uncertain'
                job={**job,'allocation_diagnostics':allocation_safe(session.get('allocation_diagnostics'))}

        previous=self.journal.get(owner,rid)
        if not allocation_code and (job['phase']=='allocation_uncertain' or
                job['phase']=='cleanup_pending' and previous.get('state')=='submission_uncertain'):
            allocation_code='allocation_uncertain'
        if not job.get('runtime_id') and allocation_code in {
                'allocation_unavailable','allocation_rejected','allocation_uncertain'}:
            diagnostic=sanitize_diagnostics({'stage':'provisioning','error_code':allocation_code,
                                            **allocation_safe(job.get('allocation_diagnostics'))})
            if allocation_code=='allocation_uncertain' and not job.get('released'):
                state='submission_uncertain'
        changes={'session_id':job.get('session_id'),'gpu':job.get('gpu'),'requested_gpu':job.get('requested_gpu'),'assigned_gpu':job.get('assigned_gpu'),'gpu_name':job.get('gpu_name'),'session_runtime_retained':job.get('session_runtime_retained'),'diagnostics':diagnostic,'state':state,'root_cause':diagnostic.get('error_code') if diagnostic.get('stage')=='session_fault' else 'native_runtime_failed' if state=='failed' else None,'remote_id':job['id'],'artifacts':job.get('artifacts',[])}
        if diagnostic.get('error_code') in {'node_import_failed','dependency_gap'} or diagnostic.get('stage') in {'node_start','execute','prompt_validation'}:
            from scripts.colab_session_probe import private_diagnostics
            durable=self.service.find_job(owner,job['id']) if hasattr(self.service,'find_job') else None
            if durable:changes['private_dependency_diagnostics']=private_diagnostics(durable.get('private_dependency_diagnostics',{}))
        if diagnostic.get('error_code') in {'node_import_failed','dependency_gap'}:
            changes['message']='遠端環境缺少必要元件，尚未開始生成'
        if diagnostic.get('stage')=='session_bootstrap':
            from scripts.colab_session_diagnostics import stderr_excerpt
            durable=self.service.find_job(owner,job['id'])
            if durable:changes['private_bootstrap_stderr']=stderr_excerpt(durable.get('private_bootstrap_stderr',''))
        if state in {'completed','failed','cancelled'} and job.get('released') and isinstance(job.get('units_observed'),(int,float)):
            changes['actual_cost']={'unit':'CU','amount':job['units_observed']}
        return public(self.journal.update(owner,rid,**changes))
    def poll(self,owner,rid):
        from .adapters import public
        from .journal import TERMINAL
        job=self.journal.get(owner,rid)
        if not job or job['lane']!='colab':fail('job_not_found','找不到原任務')
        if job['state'] in TERMINAL:return public(job)
        try:
            with self.service.native_submission_guard(owner,rid):
                # Legacy submit holds account_guard through _submit; also exclude
                # it before proving absence. New submit holds both locks.
                with self.service.account_guard(job['account'],blocking=False):
                    remote=self.service.find_job(owner,rid)
                    if remote is not None:return self._record(owner,rid,self.service.public_job(remote))
                    if job['state']=='submission_uncertain':
                        return self._close(owner,rid,state='closed_no_execution_evidence',
                            failure_kind='admission',root_cause='no_execution_evidence',cancel_supported=False,
                            message='無執行證據，原請求已結案；可建立新請求',actual_cost={'unit':'CU','amount':0},settlement_status='settled',
                            diagnostics={'stage':'admission','error_code':'admission_row_absent'},
                            closure_evidence={'job_row_absent':True,'submission_lock_held':True,'account_lock_held':True,'new_submissions':0})
        except BlockingIOError:
            return public(self.journal.update(owner,rid,diagnostics={'stage':'admission','error_code':'admission_in_flight'}))
        except Exception as error:
            from scripts.colab_private_diagnostics import capture
            return public(self.journal.update(owner,rid,diagnostics={'stage':'admission','error_code':'admission_lookup_failed'},private_admission_lookup_error=capture(error)))
        return public(job)
    recover=poll
    def cancel(self,owner,rid):
        from .journal import TERMINAL
        result=self.poll(owner,rid)
        if result['state'] in TERMINAL:return result
        try:self.service.cancel(owner,rid)
        except Exception:
            self.journal.update(owner,rid,diagnostics={'stage':'admission','error_code':'admission_cancel_unconfirmed'})
        return self.poll(owner,rid)
    def download(self,owner,rid,name):return self.service.artifact(owner,rid,name)
