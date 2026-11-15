"""Reviewable calibration runtime, with an explicit owner-bound bundle resolver.

resolver(owner, request) -> trusted bundle, owned bytes. It MUST repeat the
product's role/asset authorization; no automatic baseline or owner asset fallback.
Construction alone never enables network: injected transports retain their gate.
"""
from .contracts import FEATURES, UNITS, voice_gate, lane_description, fail
from .adapters import owned_inputs

class Runtime:
    def __init__(self, resolver, adapters, journal, quotes=None, consent=None):
        self.resolver=resolver;self.adapters=adapters;self.journal=journal;self.quotes=quotes or {};self.consent=consent
        if self.consent is None:
            from .consent import VoiceConsent
            self.consent=VoiceConsent(journal.path.parent/'voice-consent.sqlite3')
    def voice_consent(self,owner,request):
        explicit=request.get('remote_voice_consent',False) is True
        return self.consent.permits(owner,explicit) if self.consent else explicit
    def owns(self,owner,lane,rid):
        job=self.journal.get(owner,rid)
        return bool(job and job['lane']==lane)
    def inspect(self,owner,lane,request,limits):
        # No provider probes/quotes on form preview. The reviewed calibration
        # coordinator must first populate account-specific readiness and prices.
        try:
            bundle,files=self.resolver(owner,request)
            if lane=='colab':
                from colab_inputs import preflight
                preflight(bundle,files,self.adapters[(owner,lane)].service.root)
            voice_gate(bundle,lane,self.voice_consent(owner,request))
        except Exception:return {'eligible':False,'reasons':['parity_input_or_consent_required']}
        if lane_description(bundle['feature'],lane)['recipe_mode']=='impossible':return {'eligible':False,'reasons':['voice_node_contract_required']}
        entry=self.quotes.get((owner,lane,bundle['bundle_sha256']))
        if not entry:return {'eligible':False,'reasons':['parity_probe_quote_required']}
        adapter=self.adapters.get((owner,lane))
        fingerprint=getattr(getattr(adapter,'transport',None),'fingerprint',None)
        if not adapter or (lane!='colab' and entry.get('account_fingerprint')!=fingerprint):
            return {'eligible':False,'reasons':['parity_probe_quote_required']}
        if entry.get('unit')!=UNITS[lane]:return {'eligible':False,'reasons':['quote_invalid']}
        return {'eligible':True,'parity_campaign':True,'auto_eligible':False,'reasons':[],'estimated_cost':entry['amount'],
                'currency':{'colab':'compute_units','runninghub':'runninghub_points','autoip':'credits','comfy_cloud':'comfy_credits'}[lane]}
    @staticmethod
    def receipt(job):
        result = {'state':job['state'],'downstream_id':job['request_id'],
                  'cancel_supported':job.get('cancel_supported',job['lane']!='autoip'),'result_ids':[],
                  'cost':job.get('actual_cost',job.get('cost'))}
        if job['lane'] in {'runninghub','comfy_cloud','autoip','colab'}:
            result.update(model_aliases=job.get('model_aliases',[]), estimated_cost=job.get('cost'),
                          actual_cost=job.get('actual_cost'), settlement_status=job.get('settlement_status'),
                          balance_observation=job.get('balance_observation'))
        for key in ('error','diagnostics','closure_evidence','actual_minus_quote','session_id','gpu','gpu_name','session_runtime_retained'):
            if key in job:result[key]=job[key]
        return result

    def submit(self,owner,lane,rid,request,limits):
        if not self.inspect(owner,lane,request,limits)['eligible']:fail('parity_not_ready','配方、同意或帳號報價尚未就緒')
        bundle,files=self.resolver(owner,request);owned_inputs(bundle,files)
        consent=self.voice_consent(owner,request)
        adapter=self.adapters[(owner,lane)];kwargs={'voice_consent':consent}
        if lane in {'runninghub','comfy_cloud'}:kwargs.update(quote=self.quotes[(owner,lane,bundle['bundle_sha256'])])
        if lane=='colab':kwargs.update(account_id=limits['account_id'],quote=self.quotes[(owner,lane,bundle['bundle_sha256'])],max_units=None,max_minutes=limits.get('max_minutes',60))
        return self.receipt(adapter.submit(owner,rid,bundle,files,**kwargs))
    def status(self,owner,lane,rid):return self.receipt(self.adapters[(owner,lane)].poll(owner,rid))
    def cancel(self,owner,lane,rid):return self.receipt(self.adapters[(owner,lane)].cancel(owner,rid))
