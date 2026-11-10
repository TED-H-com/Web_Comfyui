"""Subprocess fixture for real HTTP/browser tests; never contacts a provider."""
import json
import sys
from pathlib import Path

request = json.load(sys.stdin)
directory = Path(request['account_dir'])
directory.mkdir(parents=True, exist_ok=True)
op = request['op']
if op == 'oauth_begin':
    result = {'authorization_url': 'https://accounts.google.com/o/oauth2/v2/auth?synthetic=1', 'flow_id': 'synthetic-flow'}
elif op == 'oauth_finish':
    assert request['code'] == 'synthetic-code'
    (directory / 'synthetic-bound').touch()
    result = {'authenticated': True}
elif op == 'account_identity':
    result = {'bound': (directory / 'synthetic-bound').exists(), 'subject_key': 'a' * 64}
elif op == 'account_status':
    result = {'authenticated': (directory / 'synthetic-bound').exists(), 'balance_compute_units': 100, 'active_assignments': 0}
elif op in ('runtime_allocate', 'runtime_lookup'):
    result = {'found': True, 'runtime_id': request['allocation_id']}
elif op == 'runtime_stop':
    result = {'stopped': True}
else:
    raise RuntimeError('Unexpected fixture operation: ' + op)
print(json.dumps({'ok': True, 'result': result}))
