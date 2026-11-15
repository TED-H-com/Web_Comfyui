"""Bounded, non-generating connection probes using fixed reviewed endpoints.

RunningHub contract: /uc/openapi/accountStatus.
AutoIP contract: provider_fabric.autoip_contract runtime account/credit_balance.
Comfy Cloud intentionally has no probe until its account contract is reviewed.
"""
import json
from hosted_compute_http import HTTPS
from provider_fabric.autoip import AUTOIP_API_BASE
from provider_fabric.autoip_contract import loads_account_response, loads_credit_balance_response


def probe_key(provider, reference, *, transport=None):
    if provider not in ('autoip', 'runninghub'):
        raise ValueError('connection probe unavailable')
    transport = transport or HTTPS()
    key = reference.resolve().reveal()
    def call(method, url, payload=None):
        response = transport.request(method, url, headers={'Authorization':'Bearer '+key, 'Content-Type':'application/json'},
            body=json.dumps(payload).encode() if payload is not None else None, timeout=10, max_bytes=128*1024)
        if response.status in (401,403): return None, 'invalid_credential'
        if response.status != 200: return None, 'provider_unavailable'
        return response.body, None
    try:
        if provider == 'runninghub':
            raw, error = call('POST', 'https://www.runninghub.ai/uc/openapi/accountStatus', {'apikey':key})
            if error: return {'connected':False, 'code':error}
            payload = json.loads(raw)
            if type(payload.get('code')) is not int or payload['code'] != 0:
                return {'connected':False, 'code':'invalid_credential'}
            data = payload['data']
            return {'connected':True, 'account_type':data.get('apiType'), 'balances':[
                {'unit':'POINT','value':data.get('remainPoint')}, {'unit':'coins','value':data.get('remainCoins')}]}
        raw, error = call('GET', AUTOIP_API_BASE + '/me')
        if error: return {'connected':False, 'code':error}
        loads_account_response(raw)
        balances = []
        try:
            raw, error = call('GET', AUTOIP_API_BASE + '/credits/balance')
            if not error:
                balances = [{'unit':'credits','value':loads_credit_balance_response(raw).available_credits}]
        except Exception:
            pass  # Unknown balance remains unknown; valid identity is separate.
        return {'connected':True, 'balances':balances}
    finally:
        key = ''
