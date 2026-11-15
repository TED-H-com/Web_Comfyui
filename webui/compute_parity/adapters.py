"""Native RunningHub, public AutoIP 0.17.0 and Cloud manifest probe adapters.

Callers provide server-resolved owned bytes. No workflow, voice or asset ID is
borrowed from a baseline bundle. Transport is injected; default HTTP is disabled.
"""
import hashlib
import time
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode, quote
from .contracts import (ParityError, compile_request, dependency_probe, digest, fail,
                        number, safe_relative, validate_bundle, voice_gate)
from .journal import TERMINAL

def identity(bundle, lane, consent, account):
    return digest({'bundle':bundle['bundle_sha256'],'lane':lane,'consent':consent,'account':account})

def call(transport, method, path, **kw):
    # Transport exceptions can contain a key in the URL; never propagate/chains/log them.
    safe=None
    try: result=transport.request(method,path,**kw)
    except ParityError:raise
    except Exception as error:
        from .diagnostics import attempt_diagnostics,transport_failure
        safe=ParityError('provider_io','供應商回應未確認；保留原請求並禁止重送')
        safe.diagnostics=attempt_diagnostics('upload' if path.startswith('/upload/') else 'prompt_transport',error=transport_failure(error))
    if safe is not None:raise safe
    return result

def remote_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',value):fail('receipt_invalid','平台任務識別碼不正確')
    return value

def owned_inputs(bundle, files):
    expected={i['slot']:i for i in bundle['inputs']}
    if set(expected)!=set(files):fail('input_invalid','素材欄位與基準不一致')
    for slot,item in expected.items():
        raw=files[slot]
        if not isinstance(raw,bytes) or len(raw)!=item['bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:
            fail('input_hash_mismatch','素材與已核准基準不同')
    return expected

def public(job):
    return {k:job[k] for k in ('error','cancel_supported','session_id','gpu','requested_gpu','assigned_gpu','gpu_name','session_runtime_retained','request_id','lane','case_id','state','cost','actual_cost','artifacts','message','remote_id','probe','root_cause','settlement_status','balance_observation','model_aliases','diagnostics','closure_evidence','actual_minus_quote','failure_kind','output_contract_status','contract_root_cause') if k in job}

class RunningHubNative:
    lane='runninghub'
    def __init__(self, transport, journal):self.transport=transport;self.journal=journal
    def _owner(self,owner):
        bound=getattr(self.transport,'owner',None)
        if bound is not None and owner!=bound:fail('credential_owner','只能使用自己的平台連線')
    def _job(self, owner, request):
        self._owner(owner)
        j=self.journal.get(owner,request)
        if not j or j['lane']!=self.lane or j['account']!=self.transport.fingerprint:fail('job_not_found','找不到此帳號的任務；換 key 後須重新初始化')
        return j
    def probe(self,bundle, *, voice_consent=False):
        plan=compile_request(bundle,self.lane,request_id=str(uuid.uuid4()),voice_consent=voice_consent)
        return dependency_probe(plan['json']['prompt'],call(self.transport,'GET','/object_info'))
    def submit(self, owner, request_id, bundle, files, *, quote, cap=None, voice_consent=False):
        self._owner(owner)
        if str(uuid.UUID(request_id))!=request_id:fail('request_invalid','請求識別碼須為 UUID')
        validate_bundle(bundle);voice_gate(bundle,self.lane,voice_consent)
        if self.lane=='runninghub' and bundle['feature']=='voice-speak':
            fail('voice_node_contract_required','私人權重可上傳收藏；尚缺相同聲音節點與權重綁定契約')
        expected=owned_inputs(bundle,files)
        ident=identity(bundle,self.lane,voice_consent,self.transport.fingerprint)
        old=self.journal.get(owner,request_id)
        if old:
            if old['identity']!=ident:fail('request_conflict','原請求內容或帳號已變更')
            return public(self._job(owner,request_id))
        if self.lane=='runninghub':
            from .runninghub_native import require_membership
            require_membership(getattr(self.transport,'account_type',None))
        # Admission precedes provider probe/upload, so an invalid budget sends nothing.
        job,new=self.journal.reserve(owner,request_id,self.lane,self.transport.fingerprint,bundle['case_id'],ident,quote,cap)
        if not new:return public(job)
        from .diagnostics import attempt_diagnostics,prompt_identity
        self.journal.update(owner,request_id,diagnostics=attempt_diagnostics('upload'))
        try:
            probe=self.probe(bundle,voice_consent=voice_consent)
            self.journal.update(owner,request_id,probe=probe)
            if not probe['ready']:fail('initialisation_gap',probe['reason'])
            bindings={}
            for slot,item in expected.items():
                name=hashlib.sha256((owner+request_id+slot).encode()).hexdigest()+Path(item['name']).suffix
                result=call(self.transport,'POST','/upload/image',fields={'type':'input','overwrite':'false'},files={'image':(name,files[slot])})
                self.journal.update(owner,request_id,diagnostics=attempt_diagnostics('upload',result,status=getattr(self.transport,'last_diagnostics',{}).get('http_status')))
                if not isinstance(result,dict):fail('upload_invalid','平台上傳回應不正確')
                filename=result.get('name');subfolder=result.get('subfolder','')
                if not safe_relative(filename) or (subfolder and not safe_relative(subfolder)) or result.get('type','input')!='input':fail('upload_invalid','平台上傳回應不正確')
                bindings[slot]=(subfolder+'/' if subfolder else '')+filename
            plan=compile_request(bundle,self.lane,request_id=request_id,bindings=bindings,voice_consent=voice_consent)
        except ParityError as e:
            return public(self.journal.update(owner,request_id,state='rejected',root_cause=e.code,message=str(e),diagnostics={**self.journal.get(owner,request_id).get('diagnostics',attempt_diagnostics('upload')),**getattr(e,'diagnostics',{}),'stage':'upload'}))
        self.journal.update(owner,request_id,model_aliases=plan.get('model_aliases',[]))
        # Durable intent BEFORE /prompt. There is deliberately no retry of this mutation.
        from .diagnostics import response_diagnostics, validation_rejected
        # Observation precedes durable submission time and the only POST.
        baseline=self._point_observation()
        self.journal.update(owner,request_id,state='submission_uncertain',submitted_at=time.time(),
                            pre_submission_point=baseline,diagnostics=attempt_diagnostics('prompt_transport'))
        try:
            response=call(self.transport,'POST','/prompt',body=plan['json'])
            rid=prompt_identity(response)
            # Identity is durable before processing any optional response fields.
            if rid:self.journal.update(owner,request_id,remote_id=rid,state='queued')
            diagnostic=attempt_diagnostics('prompt_response' if rid else 'id_parse',response,
                status=getattr(self.transport,'last_diagnostics',{}).get('http_status'))
        except ParityError as error:
            rid=getattr(error,'prompt_id',None)
            if rid:self.journal.update(owner,request_id,remote_id=remote_id(rid),state='queued')
            diagnostic={**attempt_diagnostics('prompt_transport'),**getattr(error,'diagnostics',{})}
        except (AttributeError,TypeError):diagnostic=attempt_diagnostics('id_parse')
        job=self._job(owner,request_id)
        changes={'diagnostics':diagnostic}
        # A prompt ID means accepted work can exist even alongside node_errors.
        if not job.get('remote_id') and validation_rejected(diagnostic):
            changes.update(state='rejected',root_cause='workflow_validation_rejected',message='平台確認工作流驗證拒絕')
        return public(self.journal.update(owner,request_id,**changes))
    def _point_observation(self):
        observer=getattr(self,'observe_point',None)
        if observer is None:return None
        try:
            value=observer()
            if number(value):return {'at':time.time(),'unit':'POINT','value':value,'account':self.transport.fingerprint}
        except Exception:pass
        return None
    def _close_uncertain(self,owner,job,snapshot):
        now=time.time();submitted=job.get('submitted_at',job['created_at'])
        evidence=job.get('closure_evidence',{})
        observations=evidence.get('observations',[])
        observation=self._point_observation() if not observations or now-observations[-1]['at']>=60 else None
        dropped=evidence.get('unexplained_drop',False)
        if observation:
            if observations and observation['value']<observations[-1]['value']:dropped=True
            observations=observations+[observation]
            if len(observations)>128:observations=observations[:1]+observations[-127:]
        baseline=job.get('pre_submission_point')
        same=lambda o: isinstance(o,dict) and o.get('account')==job['account'] and o.get('unit')=='POINT' and number(o.get('value'))
        before=same(baseline) and baseline['at']<=submitted
        eligible=False
        if observation and before:
            eligible=observation['value']==baseline['value'] and all(o['value']>=baseline['value'] for o in observations)
        elif observation and not before:
            first=next((o for o in observations if same(o)),None)
            eligible=bool(first and now-first['at']>=1800 and observation['value']==first['value']
                          and all(o['value']>=first['value'] for o in observations))
        evidence={**snapshot,'checked_at':now,'submitted_at':submitted,'queue_absent':True,'history_absent':True,
                  'pre_submission_point':baseline if before else None,'observations':observations,'unexplained_drop':dropped,
                  'minimum_age_seconds':3600,'minimum_observation_span_seconds':0 if before else 1800,
                  'new_submissions':0,'account':job['account']}
        changes={'closure_evidence':evidence}
        if now-submitted>=3600 and eligible and not dropped:
            changes.update(state='closed_no_execution_evidence',message='無執行證據，原請求已結案；新嘗試另計次數',
                           settlement_status='unknown',root_cause='no_execution_evidence')
        return public(self.journal.update(owner,job['request_id'],**changes))
    def recover(self,owner,request_id):
        job=self._job(owner,request_id)
        if job.get('remote_id'):return self.poll(owner,request_id)
        if job['state']!='submission_uncertain':return public(job)
        history=call(self.transport,'GET','/history');queue=call(self.transport,'GET','/queue')
        if not isinstance(history,dict) or not isinstance(queue,dict) or any(not isinstance(queue.get(k),list) for k in ('queue_running','queue_pending')):
            fail('recovery_incomplete','平台佇列或歷史格式未確認')
        prompts=[(rid,item.get('prompt')) for rid,item in history.items() if isinstance(item,dict)]
        prompts += [(p[1],p) for k in ('queue_running','queue_pending') for p in queue.get(k,[]) if isinstance(p,list) and len(p)>3]
        matches=[];unknown=False
        total=len(history)+len(queue['queue_running'])+len(queue['queue_pending'])
        if len(prompts)!=total:unknown=True
        for rid,p in prompts:
            if not isinstance(p,list) or len(p)<=3 or not isinstance(p[3],dict):unknown=True;continue
            marker=p[3].get('castle_request_id')
            graph=p[2] if isinstance(p[2],dict) else {}
            prefixes=[n['inputs']['filename_prefix'] for n in graph.values()
                      if isinstance(n,dict) and isinstance(n.get('inputs'),dict) and 'filename_prefix' in n['inputs']]
            if marker==request_id or ('parity/'+request_id in prefixes):matches.append(remote_id(rid))
            elif not isinstance(marker,str) and not prefixes:unknown=True
        if len(set(matches))==1:
            return public(self.journal.update(owner,request_id,state='queued',remote_id=matches[0]))
        # Unrelated identifiable jobs do not deadlock this request; opaque entries do.
        if not matches and not unknown:
            return self._close_uncertain(owner,job,{'history_count':len(history),
                'queue_count':len(queue['queue_running'])+len(queue['queue_pending']),
                'snapshot_sha256':digest({'history_ids':sorted(history),'queue_ids':[p[1] for k in ('queue_running','queue_pending') for p in queue[k]]})})
        return public(job)
    def poll(self,owner,request_id):
        job=self._job(owner,request_id)
        if not job.get('remote_id'):return self.recover(owner,request_id)
        if job['state'] in TERMINAL:return public(job)
        response=call(self.transport,'GET','/history/'+remote_id(job['remote_id']))
        entry=response.get(job['remote_id'])
        if not entry:return public(job)
        status=entry.get('status',{})
        if status.get('status_str')=='error':return public(self.journal.update(owner,request_id,state='failed',root_cause='provider_generation_failed',message='平台回報生成失敗'))
        if status.get('status_str')!='success' and status.get('completed') is not True:
            return public(self.journal.update(owner,request_id,state='cancelling' if job['state']=='cancelling' else 'running'))
        artifacts=[]
        for output in entry.get('outputs',{}).values():
            for kind in ('images','gifs','audio','videos'):
                for file in output.get(kind,[]):
                    if not isinstance(file,dict) or not safe_relative(file.get('filename')):fail('output_invalid','平台成果路徑不正確')
                    sub=file.get('subfolder','')
                    checked=sub[:-1] if isinstance(sub,str) and sub.endswith('/') else sub
                    if not isinstance(sub,str) or (sub and not safe_relative(checked)):fail('output_invalid','平台成果路徑不正確')
                    if file.get('type')!='output':continue
                    artifacts.append({'filename':file['filename'],'subfolder':sub,'type':'output'})
        if not artifacts:fail('output_missing','平台回報完成但沒有成果')
        # Native prompt IDs may not be accepted as v2 task IDs. Failed billing
        # lookup retains the POINT reservation; never interpret coins as POINT.
        actual=None
        try:
            usage=call(self.transport,'POST','/openapi/v2/query',body={'taskId':job['remote_id']},billing=True).get('usage',{})
            value=usage.get('chargedPoint')
            if isinstance(value,str):value=float(value)
            if number(value):actual={'unit':'POINT','amount':value}
        except (ParityError,ValueError):pass
        changes={'state':'completed','artifacts':artifacts}
        after=self._point_observation()
        if after:
            changes['balance_observation']={**after,'status':'observed'}
            before=job.get('pre_submission_point') or {}
            if before.get('account')==after['account'] and number(before.get('value')):
                changes['point_balance_delta']=before['value']-after['value']
        # Balance movement is an account observation, not an invented task bill.
        if actual is not None:changes['actual_cost']=actual
        return public(self.journal.update(owner,request_id,**changes))
    def cancel(self,owner,request_id):
        job=self._job(owner,request_id)
        if job['state'] in TERMINAL:return public(job)
        if not job.get('remote_id'):
            self.recover(owner,request_id);job=self._job(owner,request_id)
            if not job.get('remote_id'):return public(self.journal.update(owner,request_id,message='提交待查；尚無可取消任務 ID，未重送'))
        rid=job['remote_id'];queue=call(self.transport,'GET','/queue')
        running=[p[1] for p in queue.get('queue_running',[]) if len(p)>1]
        pending=[p[1] for p in queue.get('queue_pending',[]) if len(p)>1]
        if rid in running:
            # /interrupt is global to the proxy. Do not interrupt unrelated work.
            if running!=[rid]:fail('cancel_scope_unknown','帳號另有執行工作，無法安全取消')
            call(self.transport,'POST','/interrupt',body={'prompt_id':rid})
        elif rid in pending:call(self.transport,'POST','/queue',body={'delete':[rid]})
        else:return self.poll(owner,request_id)
        return public(self.journal.update(owner,request_id,state='cancelling',message='已要求取消；待平台確認'))
    def download(self,owner,request_id,index=0):
        job=self._job(owner,request_id)
        if job['state']!='completed' or type(index) is not int or not 0<=index<len(job['artifacts']):fail('output_not_ready','成果尚未就緒')
        return self.transport.download_runninghub_output(job['artifacts'][index])

class AutoIPPublic(RunningHubNative):
    lane='autoip'
    def quote(self, bundle, *, voice_consent=False):
        if bundle['feature']=='voxcpm-tts':fail('voice_node_contract_required','不支援此聲音執行契約')
        voice_gate(bundle,self.lane,voice_consent)
        pricing=call(self.transport,'GET','/api/v1/pricing')
        balance=call(self.transport,'GET','/api/v1/credits/balance')
        # Return evidence, not an invented price parser. normalize_quote below
        # accepts an explicitly matched operation price from this account's data.
        return {'pricing':pricing,'balance':balance,'unit':'credits',
                'operation':'voice_clone' if bundle['feature']=='voice-speak' else 'character_dance'}
    @staticmethod
    def normalize_quote(evidence, operation):
        data=evidence['pricing'].get('data',{})
        # Public response shape must positively identify the service price;
        # unknown tiers/per-character prices require a revised parser, never a guess.
        value=data.get(operation) if isinstance(data,dict) else None
        off_peak=value.get('off_peak_credits') if isinstance(value,dict) else None
        if isinstance(off_peak,dict):off_peak=off_peak.get('credits')
        active=evidence['pricing'].get('off_peak',{}).get('active_now') is True
        if isinstance(value,dict):value=value.get('credits') if (value.get('unit') or 'credits')=='credits' and (value.get('billing') or 'fixed')=='fixed' else None
        if not number(value) or value<=0:fail('quote_unavailable','AutoIP 此服務報價格式未確認；不會提交')
        balance=evidence['balance'].get('data',evidence['balance'])
        available=next((balance[k] for k in ('available_credits','credits','balance') if k in balance),None) if isinstance(balance,dict) else None
        if not number(available) or available<value:fail('balance_unavailable','AutoIP 餘額未確認或不足')
        return {'unit':'credits','amount':value,'basis':'provider_pricing','upper_bound':True,
                'base_amount':value,'off_peak_amount':off_peak if number(off_peak) else None,
                'active_now':active,'observed_at':time.time(),'operation':operation,
                'label':'報價上限（原價）；離峰折扣可能適用，非保證'}
    def submit(self,owner,request_id,bundle,files,*,cap=None,voice_consent=False,expected_quote=None,**_):
        self._owner(owner)
        if str(uuid.UUID(request_id))!=request_id:fail('request_invalid','請求識別碼須為 UUID')
        plan=compile_request(bundle,self.lane,request_id=request_id,voice_consent=voice_consent)
        expected=owned_inputs(bundle,files);ident=identity(bundle,self.lane,voice_consent,self.transport.fingerprint)
        old=self.journal.get(owner,request_id)
        if old:
            if old['identity']!=ident:fail('request_conflict','原請求內容或帳號已變更')
            return public(self._job(owner,request_id))
        self.journal.check_admission(owner,self.lane,bundle['case_id'])
        evidence=self.quote(bundle,voice_consent=voice_consent)
        q=self.normalize_quote(evidence,evidence['operation'])
        if expected_quote is not None and (q['amount']!=expected_quote['amount'] or q['unit']!=expected_quote['unit']):fail('quote_changed','報價已變更，沒有提交')
        job,new=self.journal.reserve(owner,request_id,self.lane,self.transport.fingerprint,bundle['case_id'],ident,q,cap)
        if not new:return public(job)
        formfiles={}
        for field,binding in plan['files'].items():
            slot=next(s for s,i in expected.items() if i['name']==binding)
            formfiles[field]=(Path(binding).name,files[slot])
        if 'reference_video' in formfiles:
            from .autoip_duration import prepare
            try:
                name,raw=formfiles['reference_video']
                raw,contract=prepare(raw,bundle['output_contract'])
                formfiles['reference_video']=(name,raw)
                self.journal.update(owner,request_id,duration_contract=contract)
            except (ParityError,KeyError) as error:
                return public(self.journal.update(owner,request_id,state='rejected',root_cause=getattr(error,'code','duration_contract_missing')))
        self.journal.update(owner,request_id,state='submission_uncertain')
        try:
            response=call(self.transport,'POST',plan['path'],fields=plan['fields'],files=formfiles)
            rid=remote_id(response.get('task_uuid') or response.get('data',{}).get('task_uuid'))
        except (ParityError,AttributeError):return public(self._job(owner,request_id))
        return public(self.journal.update(owner,request_id,state='queued',remote_id=rid))
    def recover(self,owner,request_id):
        job=self._job(owner,request_id)
        if job.get('remote_id'):return self.poll(owner,request_id)
        return public(self.journal.update(owner,request_id,message='AutoIP 0.17.0 無 client ID 查回接口；請由帳號紀錄確認原任務，禁止重送'))
    def poll(self,owner,request_id):
        job=self._job(owner,request_id)
        if not job.get('remote_id'):return self.recover(owner,request_id)
        if job['state'] in TERMINAL and (job['state']!='completed' or job.get('actual_cost') is not None):return public(job)
        response=call(self.transport,'GET','/api/v1/tasks/'+remote_id(job['remote_id']))
        state=response.get('status')
        if state not in {'pending','queued','processing','running','completed','failed','cancelled','timeout'}:fail('status_unknown','AutoIP 回報未知任務狀態')
        state={'pending':'queued','processing':'running','timeout':'failed'}.get(state,state)
        if job['state']=='completed' and state!='completed':return public(job)
        outputs=response.get('outputs',[])
        if state=='completed' and (not isinstance(outputs,list) or not outputs):fail('output_missing','AutoIP 完成但沒有成果')
        artifacts=[{'index':i} for i in range(len(outputs))] if state=='completed' else []
        changes = dict(state=state,artifacts=artifacts,root_cause='provider_generation_failed' if state=='failed' else None)
        if state == 'completed':
            # Task-scoped explicit charge only. Neither estimates nor balance
            # deltas establish the charge attributable to this task.
            charge = next((response[k] for k in ('charged_credits','credits_charged','actual_credits') if k in response), None)
            if isinstance(charge,str):
                try:charge=float(charge)
                except ValueError:charge=None
            if number(charge) and charge > 0:
                changes.update(actual_cost={'unit':'credits','amount':charge},settlement_status='settled',message='已完成、費用已對帳')
            else:
                observation={'at':time.time(),'unit':'credits','value':None,'status':'unavailable'}
                try:
                    data=call(self.transport,'GET','/api/v1/credits/balance')
                    data=data.get('data',data)
                    value=next((data[k] for k in ('available_credits','credits','balance') if k in data),None)
                    if number(value):observation.update(value=value,status='observed')
                except (ParityError,TypeError,AttributeError):pass
                changes.update(settlement_status='pending',message='已完成、費用待對帳',balance_observation=observation)
        return public(self.journal.update(owner,request_id,**changes))
    def cancel(self,owner,request_id):
        job=self._job(owner,request_id)
        if job['state'] in TERMINAL:return public(job)
        return public(self.journal.update(owner,request_id,message='AutoIP 0.17.0 未公開取消接口；任務可能繼續計費，請到供應商帳號處理'))
    def download(self,owner,request_id,index=0):
        job=self._job(owner,request_id)
        if job['state']!='completed' or type(index) is not int or not 0<=index<len(job['artifacts']):fail('output_not_ready','成果尚未就緒')
        raw=call(self.transport,'GET',f'/api/v1/tasks/{remote_id(job["remote_id"])}/outputs/{index}',binary=True)
        if job.get('duration_contract'):
            from .autoip_duration import trim
            raw=trim(raw,job['duration_contract'])
        return raw

class CloudManifestProbe:
    def __init__(self,transport):self.transport=transport
    def probe(self,bundle):
        # Never sends graph, text, audio, persona data or uploads: only GET schema.
        validate_bundle(bundle)
        graph=compile_request(bundle,'comfy_cloud',request_id=str(uuid.uuid4()),voice_consent=True)['probe_graph']
        info=call(self.transport,'GET','/api/object_info')
        from .cloud_inventory import private_aliases
        allowed, inventory = private_aliases(bundle, graph, self.transport)
        result=dependency_probe(graph,info,model_inventory=allowed)
        if not all(row['present'] for row in inventory):result['ready']=False
        result['model_inventory']=inventory
        result['reason']='' if result['ready'] else 'Comfy Cloud 缺少所需節點或模型；未部署 Build'
        result.update(account_fingerprint=self.transport.fingerprint,submit_allowed=False)
        return result


class CloudNative(RunningHubNative):
    """Shared Cloud API v2; voice waits for a separately reviewed Build."""
    lane='comfy_cloud'
    def probe(self,bundle,**_):return CloudManifestProbe(self.transport).probe(bundle)
    def submit(self,owner,request_id,bundle,files,*,quote,cap=None,voice_consent=False):
        self._owner(owner)
        if str(uuid.UUID(request_id))!=request_id:fail('request_invalid','請求識別碼須為 UUID')
        plan=compile_request(bundle,self.lane,request_id=request_id,voice_consent=voice_consent)
        if not plan['submit_allowed']:fail('cloud_voice_build_required','共用環境缺聲音執行節點；須先準備獨立 Build')
        expected=owned_inputs(bundle,files);ident=identity(bundle,self.lane,voice_consent,self.transport.fingerprint)
        job,new=self.journal.reserve(owner,request_id,self.lane,self.transport.fingerprint,bundle['case_id'],ident,quote)
        if not new:return public(self._job(owner,request_id))
        try:
            probe=self.probe(bundle)
            if not probe['ready']:fail('initialisation_gap',probe['reason'])
            bindings={}
            for slot,item in expected.items():
                response=call(self.transport,'POST','/api/v2/assets',fields={'file_path':Path(item['name']).name,'content_type':__import__('mimetypes').guess_type(item['name'])[0] or 'application/octet-stream'},files={'file':(Path(item['name']).name,files[slot])})
                bindings[slot]={'__type':'core/ASSET','info':{'id':remote_id(response.get('id'))}}
            plan=compile_request(bundle,self.lane,request_id=request_id,bindings=bindings,voice_consent=voice_consent)
        except ParityError as error:
            return public(self.journal.update(owner,request_id,state='rejected',root_cause=error.code,message=str(error)))
        self.journal.update(owner,request_id,model_aliases=plan.get('model_aliases',[]))
        self.journal.update(owner,request_id,state='submission_uncertain')
        try:
            response=call(self.transport,'POST',plan['path'],body=plan['json'],headers=plan['headers'])
            rid=self._uuid(response.get('id'))
            # Persist identity independently of untrusted provider-returned links.
            self.journal.update(owner,request_id,state='queued',remote_id=rid)
            return public(self.journal.update(owner,request_id,links=self._job_links(rid)))
        except ParityError as error:
            if getattr(error,'id',None):
                try:self.journal.update(owner,request_id,remote_id=self._uuid(error.id),state='queued')
                except ParityError:pass
            return public(self._job(owner,request_id))
        except AttributeError:return public(self._job(owner,request_id))
    @staticmethod
    def _uuid(value):
        try:
            if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError()
        except (ValueError,TypeError,AttributeError):fail('receipt_invalid','Cloud 任務或素材識別碼須為 UUID')
        return value
    @classmethod
    def _job_links(cls,rid):
        path='/api/v2/jobs/'+cls._uuid(rid)
        return {'self':path,'cancel':path+'/cancel'}
    @staticmethod
    def _link(url):
        from urllib.parse import urlsplit
        if not isinstance(url,str):fail('cloud_link_invalid','平台未提供後續操作連結')
        parsed=urlsplit(url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme!='https' or parsed.netloc!='cloud.comfy.org':fail('cloud_link_invalid','平台連結來源不符')
        if not parsed.path.startswith('/api/v2/') or parsed.fragment:fail('cloud_link_invalid','平台連結格式不符')
        return parsed.path+('?' + parsed.query if parsed.query else '')
    def recover(self,owner,request_id):
        job=self._job(owner,request_id)
        if job.get('remote_id'):return self.poll(owner,request_id)
        if job['state']!='submission_uncertain':return public(job)
        # /api/v2/jobs listing returns 501. Match executed graph, never timestamp.
        response=call(self.transport,'GET','/api/jobs?limit=100')
        rows=response.get('jobs') if isinstance(response,dict) else None
        if not isinstance(rows,list):fail('recovery_incomplete','Cloud 工作清單未確認')
        pagination=response.get('pagination',{})
        for page in range(1,5):
            if not pagination.get('has_more'):break
            more=call(self.transport,'GET','/api/jobs?'+urlencode({'limit':100,'offset':page*100}))
            if not isinstance(more,dict) or not isinstance(more.get('jobs'),list):fail('recovery_incomplete','Cloud 工作清單未確認')
            rows+=more['jobs'][:100];pagination=more.get('pagination',{})
        if pagination.get('has_more'):return public(self.journal.update(owner,request_id,message='原請求超過恢復掃描範圍，須保留原 ID 繼續對帳；未重送'))
        matches=[]
        for row in rows[:500]:
            try:rid=self._uuid(row.get('id'))
            except (ParityError,AttributeError):continue
            executed=call(self.transport,'GET',self._job_links(rid)['self']+'/workflow')
            graph=executed.get('workflow',{})
            if not isinstance(graph,dict):continue
            prefixes=[n['inputs']['filename_prefix'] for n in graph.values()
                      if isinstance(n,dict) and isinstance(n.get('inputs'),dict) and 'filename_prefix' in n['inputs']]
            if prefixes and all(p=='parity/'+request_id for p in prefixes):matches.append(rid)
        if len(set(matches))==1:
            rid=matches[0]
            self.journal.update(owner,request_id,state='queued',remote_id=rid,links=self._job_links(rid),
                recovery_evidence={'method':'executed_graph_exact_request_prefix','at':time.time(),'remote_id':rid,'new_submissions':0})
            return self.poll(owner,request_id)
        return public(self.journal.update(owner,request_id,message='原請求尚無唯一執行圖匹配；禁止重送'))
    def poll(self,owner,request_id):
        job=self._job(owner,request_id)
        if job['state'] in TERMINAL:
            if job['state']=='completed' and job.get('actual_cost') is None:return self._observe_usage(owner,request_id)
            return public(job)
        if not job.get('remote_id'):return self.recover(owner,request_id)
        response=call(self.transport,'GET',self._job_links(job['remote_id'])['self']);state=response.get('status')
        state={'pending':'queued','succeeded':'completed','error':'failed','canceled':'cancelled','canceling':'cancelling','expired':'failed'}.get(state,state)
        if state not in {'queued','running','completed','failed','cancelled','cancelling'}:fail('status_unknown','Cloud 任務狀態未確認')
        outputs=response.get('outputs',[]) if state=='completed' else []
        if state=='completed' and not outputs:fail('output_missing','Cloud 完成但沒有成果')
        # A verified asset UUID selects a fixed official endpoint; HTTP links are ignored.
        links=['/api/v2/assets/'+self._uuid(item['id'])+'/content' if item.get('id') else self._output_link(item['url']) for item in outputs]
        metadata=[{'size_bytes':item.get('size_bytes'),'hash':item.get('hash')} for item in outputs]
        for item in metadata:
            if type(item['size_bytes']) is not int or not 0<item['size_bytes']<=512*1024*1024:
                fail('output_size_missing','Cloud 成果缺少有效大小；保留原任務')
        artifacts=[{'index':i} for i in range(len(links))]
        job=self.journal.update(owner,request_id,state=state,artifacts=artifacts,output_links=links,output_metadata=metadata,root_cause='provider_generation_failed' if state=='failed' else None)
        return self._observe_usage(owner,request_id) if state=='completed' else public(job)
    def _observe_usage(self,owner,request_id):
        from .cloud_usage import observe
        job=self._job(owner,request_id)
        if time.time()-(job.get('balance_observation') or {}).get('at',0)<60:return public(job)
        return public(self.journal.update(owner,request_id,settlement_status='pending',
            message='已完成、費用待對帳；workspace usage／餘額不是單筆實扣',
            balance_observation=observe(self.transport)))
    def cancel(self,owner,request_id):
        job=self._job(owner,request_id)
        if job['state'] in TERMINAL:return public(job)
        if not job.get('remote_id'):return self.recover(owner,request_id)
        call(self.transport,'POST',self._job_links(job['remote_id'])['cancel'])
        return public(self.journal.update(owner,request_id,state='cancelling'))
    def download(self,owner,request_id,index=0):
        job=self._job(owner,request_id)
        if job['state']!='completed' or type(index) is not int or not 0<=index<len(job['artifacts']):fail('output_not_ready','成果尚未就緒')
        metadata=job.get('output_metadata')
        if not metadata:
            # Upgrade old completed receipts using the original job only.
            response=call(self.transport,'GET',self._job_links(job['remote_id'])['self'])
            outputs=response.get('outputs',[])
            links=['/api/v2/assets/'+self._uuid(o['id'])+'/content' for o in outputs]
            metadata=[{'size_bytes':o.get('size_bytes')} for o in outputs]
            if len(links)!=len(job['artifacts']):fail('output_changed','Cloud 成果清單不一致')
            self.journal.update(owner,request_id,download_metadata=metadata,download_links=links)
        else:links=job['output_links']
        size=metadata[index].get('size_bytes')
        if type(size) is not int or not 0<size<=512*1024*1024:fail('output_size_missing','Cloud 成果大小未確認')
        raw=self.transport.download_output(links[index],expected_size=size)
        if len(raw)!=size:fail('output_size_mismatch','Cloud 成果大小不符')
        checks=dict(job.get('download_checks',{}));checks[str(index)]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        self.journal.update(owner,request_id,download_checks=checks)
        return raw
    @classmethod
    def _output_link(cls,url):
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        if parsed.scheme=='https' and parsed.hostname in {'cdn.comfy.org','comfy-hub-assets.comfy.org','storage.googleapis.com'} and not parsed.username and not parsed.password and parsed.port in (None,443) and not parsed.fragment:
            return url
        return cls._link(url)
