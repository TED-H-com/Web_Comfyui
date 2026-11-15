"""Transport metadata only. Never inspect exception strings or request objects."""
CLASSES={'Exception','OSError','TimeoutError','TimeoutExpired','ConnectionError','ConnectionResetError','ConnectionAbortedError','BrokenPipeError','ConnectError','ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','TimeoutException','HTTPError','HTTPStatusError','HTTPStatusException','WebSocketTimeoutException','WebSocketConnectionClosedException','ProtocolError','RemoteProtocolError','RuntimeError','ValueError','KeyError','AttributeError','AssertionError'}
PHASES={'connect','send','wait','read'}
TIMEOUTS={'connect','read','write','pool','execution','process','unknown'}
CODES={'transport_not_ready','clock_inconsistent','input_too_large', 'upload_integrity_error', 'legacy_identity_unverified', 'allocation_conflict', 'oauth_flow_mismatch', 'runtime_missing', 'allocation_uncertain', 'runtime_unavailable', 'allocation_already_attempted', 'identity_unverified', 'invalid_download', 'timeout', 'transport_error', 'not_installed', 'execution_uncertain', 'invalid_runtime', 'account_identity_mismatch', 'invalid_remote_directory', 'invalid_request', 'invalid_account_status', 'private_path', 'stop_pending', 'transport_failed', 'runtime_unreconciled', 'upload_rejected', 'oauth_exchange_failed', 'upload_transport_failed', 'authentication_required', 'upload_http_error', 'upload_failed', 'bridge_unavailable', 'stop_uncertain', 'remote_execution_failed', 'redirect_rejected', 'oauth_flow_missing', 'invalid_remote_output', 'output_too_large', 'operation_timeout', 'dependency_version', 'download_too_large', 'oauth_flow_expired', 'upload_timeout', 'runtime_unknown', 'dependency_missing', 'account_busy', 'upload_local_invalid'}
STAGES={'observe','heartbeat','upload','bootstrap','bootstrap_probe','job_probe','arm','attach','allocation','release','state'}

def sanitize(value):
    if not isinstance(value,dict):return {}
    out={}
    for key,allowed in [('error_type',CLASSES),('transport_phase',PHASES),('timeout_kind',TIMEOUTS)]:
        if isinstance(value.get(key),str) and value[key] in allowed:out[key]=value[key]
    if type(value.get('http_status')) is int and 100<=value['http_status']<=599:out['http_status']=value['http_status']
    return out

def capture(error,phase='wait'):
    name=type(error).__name__
    result={'error_type':name if name in CLASSES else 'Exception','transport_phase':phase if phase in PHASES else 'wait'}
    mapping={'ConnectTimeout':('connect','connect'),'ConnectError':('connect',None),'ReadTimeout':('read','read'),'WriteTimeout':('send','write'),'PoolTimeout':('connect','pool'),'TimeoutExpired':('wait','process'),'TimeoutError':('wait','execution'),'WebSocketTimeoutException':('read','read'),'TimeoutException':('wait','unknown')}
    if name in mapping:
        result['transport_phase'],kind=mapping[name]
        if kind:result['timeout_kind']=kind
    for source in (error,getattr(error,'response',None)):
        for key in ('status_code','status'):
            value=getattr(source,key,None)
            if type(value) is int and 100<=value<=599:result['http_status']=value
    return sanitize(result)

def fault_diagnostic(value):
    if not isinstance(value,dict):return {}
    result=sanitize(value)
    result['stage']='session_fault'
    if isinstance(value.get('stage'),str) and value['stage'] in STAGES:result['substage']=value['stage']
    from scripts.colab_session_diagnostics import ERRORS
    if isinstance(value.get('error_type'),str) and value['error_type'] in ERRORS:result['error_type']=value['error_type']
    code=value.get('code')
    result['error_code']=code if isinstance(code,str) and code in CODES|ERRORS else 'bridge_error'
    if type(value.get('ambiguous')) is bool:result['ambiguous']=value['ambiguous']
    return result
