"""Calibration accounting around the existing product's local admission adapter.

The delegate is ComputeWeb's local adapter: never a direct GPU/Comfy submitter.
"""
import time
from .contracts import digest,fail
from .adapters import owned_inputs,public
from .journal import TERMINAL
from compute_routing import ComputeRejected

class LocalBaseline:
    lane='local'
    def __init__(self, delegate, journal, downloader):
        self.delegate,self.journal,self.downloader=delegate,journal,downloader
    def submit(self,owner,request_id,bundle,files,*,request,limits=None):
        owned_inputs(bundle,files)
        identity=digest({'bundle':bundle['bundle_sha256'],'request':request,'owner':owner})
        job,new=self.journal.reserve(owner,request_id,'local','product-local-queue',bundle['case_id'],identity,{'unit':'local','amount':0})
        if not new:return self.poll(owner,request_id)
        self.journal.update(owner,request_id,state='submission_uncertain',bundle_sha256=bundle['bundle_sha256'])
        try:receipt=self.delegate.submit(owner,request_id,request,limits or {})
        except ComputeRejected as error:
            return public(self.journal.update(owner,request_id,state='rejected',root_cause=error.code,
                message=str(error),actual_cost={'unit':'local','amount':0},reservation_released=True))
        except Exception:return public(self.journal.get(owner,request_id))
        return self._record(owner,request_id,receipt)
    def _record(self,owner,rid,receipt):
        if not receipt:return public(self.journal.get(owner,rid))
        state=receipt['state']
        job=self.journal.get(owner,rid);measurement={}
        if state=='completed' and job['state']!='completed':
            now=time.time();measurement={'completed_at':now,'wall_seconds':max(0,now-job['created_at']),
                                       'measurement_version':'local-wall-r30'}
        return public(self.journal.update(owner,rid,state=state,remote_id=receipt.get('downstream_id'),**measurement,
            artifacts=receipt.get('artifacts',[]),actual_cost={'unit':'local','amount':0},
            diagnostics=receipt.get('diagnostics',{}),failure_kind='quality' if state=='quality_rejected' else 'execution',
            root_cause='speaker_gate' if state=='quality_rejected' else ('local_execution_failed' if state in {'failed','rejected'} else None)))
    def _job(self,owner,rid):
        job=self.journal.get(owner,rid)
        if not job or job['lane']!='local':fail('job_not_found','找不到本機校準任務')
        return job
    def poll(self,owner,rid):
        job=self._job(owner,rid)
        if job['state'] in TERMINAL:return public(job)
        fn=self.delegate.recover if job['state']=='submission_uncertain' else self.delegate.status
        if not fn:return public(job)
        return self._record(owner,rid,fn(owner,job.get('remote_id') or rid))
    recover=poll
    def cancel(self,owner,rid):
        job=self._job(owner,rid)
        if job['state'] in TERMINAL:return public(job)
        return self._record(owner,rid,self.delegate.cancel(owner,job.get('remote_id') or rid))
    def download(self,owner,rid,index=0):
        job=self._job(owner,rid)
        if job['state']!='completed':fail('output_not_ready','成果尚未就緒')
        return self.downloader(owner,rid,index)
