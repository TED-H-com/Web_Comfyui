"""Fixed upload diagnostics. No exception strings, response bodies or URLs."""
SUBSTAGES = frozenset({'runtime_lookup', 'directory_put', 'file_put', 'local_validation'})
CODES = frozenset({'upload_http_error', 'upload_timeout', 'upload_transport_failed',
                   'upload_local_invalid', 'upload_integrity_error'})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (10, 30)

def sanitize(value):
    if not isinstance(value, dict): return {}
    result = {}
    for key, allowed in (('upload_substage', SUBSTAGES), ('bridge_error_code', CODES)):
        if isinstance(value.get(key), str) and value[key] in allowed: result[key] = value[key]
    status = value.get('http_status')
    if type(status) is int and 100 <= status <= 599: result['http_status'] = status
    return result

def classify(error, substage=None):
    result = sanitize(getattr(error, 'diagnostics', None))
    if substage in SUBSTAGES: result['upload_substage'] = substage
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    if status is None: status = getattr(error, 'status_code', None)
    if status is None and type(getattr(error, 'code', None)) is int: status = error.code
    if type(status) is int and 100 <= status <= 599: result['http_status'] = status
    code = getattr(error, 'code', None)
    if code in ('timeout', 'operation_timeout') or isinstance(error, TimeoutError) or type(error).__name__ in {'Timeout', 'ReadTimeout', 'ConnectTimeout', 'TimeoutExpired'}:
        code = 'upload_timeout'
    elif code not in CODES:
        code = result.get('bridge_error_code') or ('upload_http_error' if 'http_status' in result else 'upload_local_invalid' if substage == 'local_validation' else 'upload_transport_failed')
    return {'stage': 'upload', 'error_code': 'input_upload_failed', **result, 'bridge_error_code': code}
