"""Post-completion observation, not attribution of workspace-wide charges to a job."""
import time
from .contracts import number

def observe(transport):
    result = {'at': time.time(), 'status': 'unavailable', 'scope': 'workspace',
              'source': '/api/billing/usage/timeseries', 'reporting_may_lag': True}
    try:
        response = transport.request('GET', result['source'])
        summary = response.get('summary') or {}; balance = summary.get('balance') or {}
        # Documented API: spend_micros is USD micros, balance *_micros is cents.
        if balance.get('currency') == 'USD':
            value = balance.get('amount_cents', balance.get('amount_micros'))
            if number(value): result.update(status='observed', balance_usd_cents=value)
        if number(summary.get('spend_micros')):result['workspace_spend_usd_micros']=summary['spend_micros']
    except Exception:pass
    return result
