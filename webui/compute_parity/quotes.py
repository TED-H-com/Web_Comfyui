"""Explicit refresh obtains account quotes, never allocates compute or submits work."""
import logging
import time
from .contracts import fail,number,voice_gate,lane_description
from .readiness import save

logger = logging.getLogger(__name__)

def refresh(runtime,owner,request,limits,estimates=None):
    b=runtime.materials.bundle(owner,request)
    result=[]
    for lane in ('autoip','colab'):
        with runtime.web.console.prefs.db() as db:data=runtime.web.console._raw(lane,db)
        e={'owner':owner,'lane':lane,'bundle_sha256':b['bundle_sha256'],'credential_version':data.get('credential_version'),
           'expires_at':time.time()+300,'dependencies_ready':False,'reason_code':'parity_probe_quote_required'}
        try:
            stage='recipe'
            if lane_description(b['feature'],lane)['recipe_mode']=='impossible':fail('voice_node_contract_required','不支援')
            stage='attempts'
            runtime.journal.check_admission(owner,lane,b['case_id'])
            stage='credential'
            if not data.get('credential_present'):fail('credential_required','請先設定自己的連線')
            stage='voice_gate'
            voice_gate(b,lane,runtime.voice_consent(owner,request))
            if lane=='autoip':
                stage='autoip_quote'
                a=runtime.adapter(owner,lane)
                evidence=a.quote(b,voice_consent=runtime.voice_consent(owner,request))
                q=a.normalize_quote(evidence,evidence['operation'])
                # Compile the exact request before advertising ready (e.g. prompt length).
                stage='autoip_compile'
                from .contracts import compile_request
                compile_request(b,lane,request_id='00000000-0000-0000-0000-000000000000',voice_consent=runtime.voice_consent(owner,request))
                e.update(quote=q,dependencies_ready=True,account_fingerprint=a.transport.fingerprint,reason_code=None)
            else:
                stage='colab_accounts'
                accounts=runtime.web.colab.snapshot(owner)['accounts']
                accounts=[a for a in accounts if a.get('authenticated') and (not limits.get('account_id') or a['id']==limits['account_id'])]
                if not accounts:fail('colab_account_unavailable','請先登入 Colab')
                manager=getattr(runtime.web.colab,'sessions',None)
                active=next((x for x in manager.rows(owner) if x['phase']!='closed'),None) if manager else None
                if active:accounts.sort(key=lambda x:x['id']!=active['account_id'])
                stage='colab_estimate'
                required=(estimates or {}).get('colab_cu_per_hour',0)*limits.get('max_minutes',60)/60
                eligible=[]
                for candidate in accounts:
                    stage='colab_account_status'
                    observed=runtime.web.colab.account_action(owner,candidate['id'],'refresh',{})
                    balance=observed.get('balance_compute_units')
                    if observed.get('authenticated') and number(balance) and balance>0 and balance>=required:
                        eligible.append((candidate,observed))
                if not eligible:fail('colab_account_unavailable','沒有 CU 足以涵蓋估價的已登入帳號')
                account,status=eligible[0]
                # account_status reports current consumption, NOT an A100 offer.
                # Only a GPU-specific quote may publish this recipe ready.

                stage='colab_estimate'
                minutes=limits.get('max_minutes',60)
                e.update(account_id=account['id'],max_minutes=minutes,reason_code='colab_a100_quote_required')
                # Optional owner-entered A100 estimate from their Colab resource UI.
                # It is labelled an estimate, never claimed to be a provider quote.
                rate=(estimates or {}).get('colab_cu_per_hour')
                balance=status.get('balance_compute_units')
                if rate is not None:
                    if not number(rate) or rate<=0 or not isinstance(minutes,int) or not 5<=minutes<=180:fail('quote_invalid','估價或時間不正確')
                    amount=rate*minutes/60
                    if not number(balance) or balance<amount:fail('balance_unavailable','帳號額度不足以涵蓋估計執行時間')
                    if status.get('authenticated') is not True:fail('colab_account_unavailable','帳號尚未連線')
                    e.update(quote={'unit':'CU','amount':amount,'basis':'owner_A100_hourly_estimate','rate_per_hour':rate},dependencies_ready=True,reason_code=None)
        except Exception as error:
            if not hasattr(error,'code'):
                logger.warning('quote_diagnostic lane=%s case=%s stage=%s exception=%s',lane,b.get('case_id'),stage,type(error).__name__)
            e['reason_code']=getattr(error,'code','quote_unavailable')
        save(runtime.root,e)
        result.append({'lane':lane,'ready':e['dependencies_ready'],'reason':e['reason_code'],'quote':e.get('quote')})
    from .hosted_readiness import refresh as refresh_hosted
    for lane in ('runninghub','comfy_cloud'):
        try:
            runtime.journal.check_admission(owner,lane,b['case_id'])
            result.append(refresh_hosted(runtime.web.console,b,lane,estimates))
        except Exception as error:result.append({'lane':lane,'ready':False,'reason':getattr(error,'code','probe_unavailable')})
    return {'lanes':result}
