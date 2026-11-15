"""Whitelisted assignment diagnostics; never expose exception text or bodies."""
HTTP_STATUSES = {400,401,403,404,408,409,412,429,500,502,503,504}
REASONS = {'GPU_UNAVAILABLE','ACCELERATOR_UNAVAILABLE','NO_ASSIGNMENT','RESOURCE_EXHAUSTED','QUOTA_EXCEEDED','PERMISSION_DENIED','UNAUTHENTICATED'}
UNAVAILABLE = {'GPU_UNAVAILABLE','ACCELERATOR_UNAVAILABLE','NO_ASSIGNMENT','RESOURCE_EXHAUSTED'}

def sanitize(value):
    value = value if isinstance(value,dict) else {}
    return {k:v for k,v in value.items() if (k=='http_status' and type(v) is int and v in HTTP_STATUSES)
        or (k=='reason_code' and isinstance(v,str) and v in REASONS)}

def classify(error):
    if type(error).__name__=='TooManyAssignmentsError' and type(error).__module__.startswith('colab_cli'):
        return 'allocation_rejected',{'http_status':412,'reason_code':'QUOTA_EXCEEDED'}
    response = getattr(error,'response',None)
    status = getattr(response,'status_code',None) or getattr(error,'status_code',None)
    if status is None and type(getattr(error,'code',None)) is int:status=error.code
    reason = getattr(error,'reason_code',None) or getattr(error,'reason',None) or getattr(error,'code',None)
    if response is not None:
        try:
            try:body=response.json()
            except Exception:
                import json
                raw=getattr(error,'response_body',None)
                if not isinstance(raw,str) or len(raw)>65536:raise ValueError('unavailable')
                body=json.loads(raw.removeprefix(")]}'\n"))
            details=body.get('error',body) if isinstance(body,dict) else {}
            if isinstance(details,dict):reason=details.get('reason') or details.get('status') or details.get('code') or reason
        except Exception:pass
    diagnostic=sanitize({'http_status':status,'reason_code':reason})
    # A network timeout or HTTP 5xx alone never proves absence of assignment.
    explicit = diagnostic.get('reason_code') in REASONS
    unavailable = explicit and diagnostic.get('reason_code') in UNAVAILABLE
    code = 'allocation_unavailable' if unavailable else ('allocation_rejected' if explicit else 'allocation_uncertain')
    return code,diagnostic
