"""Real UI integration. Preview is read-only; paid mutations use durable adapters."""
import hashlib
import mimetypes
import time
from pathlib import Path
from .contracts import ParityError,UNITS,voice_gate,lane_description,fail
from .journal import Journal
from .materials import Materials
from .readiness import match,usable
from .runtime import Runtime
from .transport import Transport
from .adapters import RunningHubNative,AutoIPPublic,CloudNative,owned_inputs
from .colab import ColabNative
from .local import LocalBaseline

class ProductRuntime(Runtime):
    def __init__(self,web):
        self.web=web;self.root=web.console.root/'compute-parity'
        self.materials=Materials(web,self.root/'bundles-r29')
        journal=Journal(self.root/'journal.sqlite3')
        super().__init__(self.materials,{},journal)
        from .lifecycle import start_monitor
        start_monitor(web.console,journal)
        self.local=web.router.adapters['local']
        self.adapters[(web.owner,'local')]=LocalBaseline(self.local,journal,None)
        self.adapters[(web.owner,'colab')]=ColabNative(web.colab,journal)
    def adapter(self,owner,lane):
        if owner!=self.web.owner:fail('credential_owner','只能使用自己的平台連線')
        key=(owner,lane)
        if key not in self.adapters:
            self.adapters[key]={'runninghub':RunningHubNative,'autoip':AutoIPPublic,'comfy_cloud':CloudNative}[lane](Transport.for_owner(self.web.console,owner,lane,enabled=True),self.journal)
        adapter=self.adapters[key]
        if lane=='runninghub':
            from .lifecycle import balance
            def observe():
                current=Transport.for_owner(self.web.console,owner,lane,enabled=True)
                if current.fingerprint!=adapter.transport.fingerprint:fail('credential_changed','餘額帳號已變更')
                return balance(self.web.console)
            adapter.observe_point=observe
        return adapter
    def evidence(self,owner,lane,bundle,limits):
        if lane=='local':return {'quote':{'unit':'local','amount':0}}
        if lane=='runninghub':
            from .lifecycle import pending_monitor
            if pending_monitor(self.journal,owner):fail('lifecycle_monitoring','首筆完成後正在觀察 POINT 餘額；完成對帳前暫停新工作')
        with self.web.console.prefs.db() as db:data=self.web.console._raw(lane,db)
        if lane=='runninghub':
            from .runninghub_native import require_membership
            require_membership(data.get('account_type'))
        entry=match(self.root,owner,lane,data.get('credential_version'),bundle['bundle_sha256'])
        if not usable(entry,lane):fail((entry or {}).get('reason_code','parity_probe_quote_required'),'此帳號的模型或報價尚未就緒')
        if lane=='autoip' and entry.get('quote',{}).get('upper_bound') is not True:fail('parity_probe_quote_required','請更新原價上限報價')
        if lane=='colab':
            account=entry.get('account_id')
            if limits.get('account_id') and limits['account_id']!=account:fail('parity_probe_quote_required','此 Colab 帳號尚未取得報價')
            self.web.colab.account(owner,account)
            manager=getattr(self.web.colab,'sessions',None)
            active=next((x for x in manager.rows(owner) if x['phase']!='closed'),None) if manager else None
            if active and (active['account_id']!=account or active.get('close_requested')):
                fail('parity_probe_quote_required','既有工作階段帳號不符或關閉中，請重新確認估價')
            if entry.get('max_minutes')!=limits.get('max_minutes',60):fail('parity_probe_quote_required','時間已變更，請重新確認估價')
        elif entry.get('account_fingerprint')!=self.adapter(owner,lane).transport.fingerprint:
            fail('parity_probe_quote_required','金鑰已變更，請重新確認模型與報價')
        return entry
    def inspect(self,owner,lane,request,limits):
        try:
            b=self.materials.bundle(owner,request)
            self.journal.check_admission(owner,lane,b['case_id'])
            if lane=='colab':
                from colab_inputs import preflight
                from colab_service import ColabError
                try:preflight(b,{i['slot']:self.materials.read(i) for i in b['inputs']},self.web.colab.root)
                except ColabError as error:return {'eligible':False,'auto_eligible':False,'reasons':[error.code]}
            voice_gate(b,lane,self.voice_consent(owner,request))
            if lane_description(b['feature'],lane)['recipe_mode']=='impossible':fail('voice_node_contract_required','聲音執行環境尚未支援')
            e=self.evidence(owner,lane,b,limits)
            if lane=='local':return {**self.local.inspect(owner,request,limits),'parity_campaign':True}
            return {'eligible':True,'auto_eligible':False,'parity_campaign':True,'reasons':[],
                **({'warnings':[e['quote'].get('label','估計，非官方報價') + '；來源：' + e['quote'].get('measurement',{}).get('case_id','帳號估計') + ' Local wall；測量時間：' + e['quote'].get('measured_at','未提供')]} if lane in {'runninghub','comfy_cloud'} else {}),
                **({'session':self.web.colab.sessions.snapshot(owner)['session']} if lane=='colab' else {}),
                'estimated_cost':e['quote']['amount'],'currency':{'colab':'compute_units','autoip':'credits','comfy_cloud':'comfy_credits','runninghub':'runninghub_points'}[lane]}
        except ParityError as error:return {'eligible':False,'auto_eligible':False,'reasons':[error.code]}
        except Exception:return {'eligible':False,'auto_eligible':False,'reasons':['parity_input_or_consent_required']}
    def submit(self,owner,lane,rid,request,limits):
        # Replays/recovery never re-resolve changed inputs or acquire a new quote.
        if self.owns(owner,lane,rid):return self.status(owner,lane,rid)
        from compute_routing import ComputeRejected
        try:
            b,files=self.materials(owner,request);consent=self.voice_consent(owner,request)
            self.journal.check_admission(owner,lane,b['case_id'])
            if lane_description(b['feature'],lane)['recipe_mode']=='impossible':fail('voice_node_contract_required','不支援')
            voice_gate(b,lane,consent);entry=self.evidence(owner,lane,b,limits)
            a=self.adapter(owner,lane)
            if lane=='autoip':
                evidence=a.quote(b,voice_consent=consent)
                fresh=a.normalize_quote(evidence,evidence['operation'])
                if fresh['amount']!=entry['quote']['amount']:fail('quote_changed','報價已變更，請重新預覽')
            kwargs={'voice_consent':consent}
            if lane=='autoip':kwargs['expected_quote']=entry['quote']
            if lane in {'runninghub','comfy_cloud'}:kwargs['quote']=entry['quote']
            elif lane=='colab':kwargs.update(account_id=entry['account_id'],quote=entry['quote'],max_minutes=limits.get('max_minutes',60))
            elif lane=='local':kwargs={'request':self.materials.local_request(b,files),'limits':limits}
        except ParityError as e:raise ComputeRejected(str(e),code=e.code) from None
        # Exceptions after reserve are ambiguous, not retry permission.
        try:job=a.submit(owner,rid,b,files,**kwargs)
        except ParityError as error:
            # A reserve race may reject after preview. Only absence of the durable
            # journal plus a known admission code proves that dispatch never began.
            if not self.journal.get(owner,rid) and error.code in {'lane_stopped','attempt_cap','account_busy','provisioning_retry_wait','provisioning_retry_cap'}:
                raise ComputeRejected(str(error),code=error.code) from None
            raise
        return self.receipt(job)
    def receipt(self,job):
        result=super().receipt(job)
        result['state']='submission_uncertain' if job['state']=='reserved' else job['state']
        if job['lane']=='local':
            result['artifacts']=job.get('artifacts',[])
            return result
        if job['state']=='completed':
            if job['lane'] in {'autoip','comfy_cloud','runninghub'}:
                if job.get('output_contract_status')=='rejected':return {**result,'state':'failed','artifacts':[]}
                try:
                    for index in range(len(job.get('artifacts',[]))):self.artifact(self.web.owner,job['request_id'],index)
                except ParityError as error:
                    if error.code not in {'duration_short','duration_audio_missing','duration_trim_failed','duration_audio_mismatch'}:raise
                    # Execution finished with output; the delivery contract failed.
                    self.journal.update(self.web.owner,job['request_id'],output_contract_status='rejected',
                        contract_root_cause=error.code,failure_kind='contract')
                    return {**result,'state':'failed','artifacts':[]}
            result['artifacts']=[{'name':'成果 '+str(i+1),'url':f'/api/compute/parity/files/{job["request_id"]}/{i}'} for i in range(len(job.get('artifacts',[])))]
        return result
    def status(self,owner,lane,rid):
        job=self.adapter(owner,lane).poll(owner,rid)
        if lane=='runninghub' and job['state']=='completed':
            from .lifecycle import ensure_monitor
            ensure_monitor(self.web.console,self.journal,owner,rid)
        return self.receipt(job)
    def cancel(self,owner,lane,rid):return self.receipt(self.adapter(owner,lane).cancel(owner,rid))
    def artifact(self,owner,rid,index):
        from compute_routing import _request_id
        _request_id(rid)
        j=self.journal.get(owner,rid)
        if not j or j['state']!='completed' or not 0<=index<len(j['artifacts']):fail('output_not_ready','成果尚未就緒')
        item=j['artifacts'][index]
        if j['lane']=='local':fail('output_not_ready','請從本機任務成果檢視')
        if j['lane']=='colab':
            path=self.web.colab.artifact(owner,rid,item['name'])
            return path[0] if isinstance(path,tuple) else path
        folder=self.root/'outputs'/hashlib.sha256(owner.encode()).hexdigest()/rid;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=folder/(str(index)+('.wav' if j['case_id'].startswith(('voice','voxcpm')) else '.mp4'))
        if not path.exists():
            raw=self.adapter(owner,j['lane']).download(owner,rid,index)
            if not raw:fail('output_not_ready','成果下載未完成')
            import os,tempfile
            fd,name=tempfile.mkstemp(dir=folder)
            try:
                with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                os.replace(name,path)
            finally:Path(name).unlink(missing_ok=True)
        return path
