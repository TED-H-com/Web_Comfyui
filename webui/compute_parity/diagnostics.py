"""Provider diagnostics are controlled vocabulary, never provider prose."""
VALIDATION = {'prompt_outputs_failed_validation', 'prompt_no_outputs', 'invalid_prompt',
              'required_input_missing', 'value_not_in_list', 'return_type_mismatch',
              'exception_during_inner_validation', 'invalid_input_type'}
ERRORS = VALIDATION | {'execution_error', 'OutOfMemoryError', 'RuntimeError', 'ValueError', 'TypeError'}
CLASSES = {'VHS_LoadVideo', 'VHS_LoadVideoPath', 'VHS_VideoCombine', 'BatchImagesNode',
           'LoadImage', 'UNETLoader', 'CLIPLoader', 'VAELoader', 'LoraLoader',
           'LoraLoaderModelOnly', 'KSampler', 'WanVideoSampler', 'SaveImage'}

CLASSES.update(['BasicScheduler', 'BatchImagesNode', 'CLIPLoader', 'CLIPTextEncode', 'CLIPVisionEncode', 'CLIPVisionLoader', 'CastleBreezyVoiceTTS', 'CastleVoxCPM', 'CheckpointLoaderSimple', 'GetImageRangeFromBatch', 'GetImageSize', 'ImageResizeKJv2', 'KSampler', 'KSamplerSelect', 'LoadImage', 'LoraLoaderModelOnly', 'ModelSamplingSD3', 'RepeatImageBatch', 'ResizeImageMaskNode', 'SAM3_VideoTrack', 'SCAIL2ColoredMask', 'SamplerCustom', 'SaveAudio', 'UNETLoader', 'VAEDecode', 'VAELoader', 'VHS_LoadVideoPath', 'VHS_VideoCombine', 'WanContextWindowsManual', 'WanSCAILToVideo'])

def sanitize(value):
    if not isinstance(value, dict): return {}
    result = {}
    for key in ('response_kind','data_kind'):
        if isinstance(value.get(key),str) and value[key] in JSON_KINDS:result[key]=value[key]
    if type(value.get('response_empty')) is bool:result['response_empty']=value['response_empty']
    if isinstance(value.get('response_length_bucket'),str) and value['response_length_bucket'] in LENGTH_BUCKETS:result['response_length_bucket']=value['response_length_bucket']
    unknown=value.get('unknown_keys')
    if isinstance(unknown,dict):
        result['unknown_keys']={level:sorted({k for k in unknown.get(level,[]) if safe_key(k) and k not in SHAPE_KEYS})[:16]
                                for level in ('top_level','data_level') if isinstance(unknown.get(level),list)}
    counts=value.get('unknown_key_count')
    if isinstance(counts,dict):
        result['unknown_key_count']={level:counts[level] for level in ('top_level','data_level')
                                     if type(counts.get(level)) is int and 0<=counts[level]<=1000000}

    if isinstance(value.get('error_code'),str) and value['error_code'] in HOSTED_ERROR_CODES:
        result['error_code']=value['error_code']
    if isinstance(value.get('stage'),str) and value['stage'] in STAGES:result['stage']=value['stage']
    if 'transport_error_type' in value and (isinstance(value.get('transport_error_type'),str) and value['transport_error_type'] in TRANSPORT_ERRORS or value.get('transport_error_type') is None):
        result['transport_error_type']=value.get('transport_error_type')
    for key in ('response_keys','data_keys'):
        if isinstance(value.get(key),list):result[key]=sorted(k for k in value[key] if isinstance(k,str) and k in SHAPE_KEYS)
    if type(value.get('prompt_id_present')) is bool:result['prompt_id_present']=value['prompt_id_present']

    if 'stage' in result and 'http_status' in value and value['http_status'] is None:result['http_status']=None
    status = value.get('http_status')
    if type(status) is int and 100 <= status <= 599: result['http_status'] = status
    code = value.get('reason_code')
    if (type(code) is int and -1000000 <= code <= 1000000) or (isinstance(code,str) and code in ERRORS):
        result['reason_code'] = code
    nodes = []
    source=value.get('nodes',[])
    for n in (source[:50] if isinstance(source,list) else []):
        if not isinstance(n, dict): continue
        row = {}
        nid = n.get('node_id')
        if isinstance(nid,str) and nid.isascii() and nid.isdigit() and len(nid) <= 8: row['node_id'] = nid
        if isinstance(n.get('node_class'),str) and n['node_class'] in CLASSES: row['node_class'] = n['node_class']
        if isinstance(n.get('error_type'),str) and n['error_type'] in ERRORS: row['error_type'] = n['error_type']
        if row: nodes.append(row)
    if nodes: result['nodes'] = nodes
    return result

def response_diagnostics(body, status=None):
    if not isinstance(body,dict): return sanitize({'http_status':status})
    error = body.get('error', {})
    code = error.get('type') if isinstance(error,dict) else None
    nodes = []
    errors = body.get('node_errors', {})
    for nid,n in (errors.items() if isinstance(errors,dict) else []):
        if not isinstance(n,dict): continue
        details=n.get('errors',[])
        for e in (details[:20] if isinstance(details,list) else []):
            if isinstance(e,dict): nodes.append({'node_id':nid,'node_class':n.get('class_type'), 'error_type':e.get('type')})
    return sanitize({'http_status':status,'reason_code':code or body.get('code'),'nodes':nodes})

def validation_rejected(diagnostics):
    return diagnostics.get('reason_code') in VALIDATION

# Known structural keys. Other names pass only the bounded safe-name policy below.
SHAPE_KEYS = {'prompt_id','taskId','id','data','error','node_errors','code','msg','number','status','name','subfolder','type'}
STAGES = {'upload','prompt_transport','prompt_response','id_parse'}
TRANSPORT_ERRORS = {'timeout','connection','tls','json_decode','response_size','http'}
HOSTED_ERROR_CODES = {'empty_response_object','missing_prompt_id'}

def prompt_identity(body):
    import re
    if not isinstance(body,dict):return None
    nested=body.get('data')
    for source in (body,nested if isinstance(nested,dict) else {}):
        for key in ('prompt_id','taskId'):
            value=source.get(key)
            if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',value):return value
    return None

def attempt_diagnostics(stage,body=None,*,status=None,error=None):
    shape=body if isinstance(body,dict) else {}
    nested=shape.get('data')
    result = {**response_diagnostics(body,status),'stage':stage,
            'transport_error_type':error if error in TRANSPORT_ERRORS else None,
            'http_status':status if type(status) is int and 100<=status<=599 else None,
            'response_keys':sorted(SHAPE_KEYS.intersection(shape)),
            'data_keys':sorted(SHAPE_KEYS.intersection(nested)) if isinstance(nested,dict) else [],
            'prompt_id_present':prompt_identity(body) is not None,
            **envelope_shape(body)}
    if stage == 'id_parse' and not result['prompt_id_present']:
        result['error_code'] = 'empty_response_object' if type(body) is dict and not body else 'missing_prompt_id'
    return result

def transport_failure(error):
    import ssl,urllib.error,json
    reason=error.reason if isinstance(error,urllib.error.URLError) else error
    if isinstance(reason,TimeoutError):return 'timeout'
    if isinstance(reason,ssl.SSLError):return 'tls'
    if isinstance(reason,json.JSONDecodeError):return 'json_decode'
    return 'connection'


JSON_KINDS={'object','array','string','number','bool','null'}
LENGTH_BUCKETS={'0','1-64','65-1024','1025-16384','16385+'}

def safe_key(key):
    import re
    return isinstance(key,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,40}',key) is not None

def json_kind(value):
    if value is None:return 'null'
    if type(value) is bool:return 'bool'
    if isinstance(value,dict):return 'object'
    if isinstance(value,list):return 'array'
    if isinstance(value,str):return 'string'
    return 'number'

def envelope_shape(body):
    top=body if isinstance(body,dict) else {}
    data=top.get('data')
    levels={'top_level':top,'data_level':data if isinstance(data,dict) else {}}
    unknown={level:[k for k in obj if k not in SHAPE_KEYS] for level,obj in levels.items()}
    result={'response_kind':json_kind(body),'response_empty':body is None or isinstance(body,(dict,list,str)) and len(body)==0,
            'data_kind':json_kind(data),
            'unknown_keys':{level:sorted(k for k in keys if safe_key(k))[:16] for level,keys in unknown.items()},
            # Total count includes invalid names and names beyond the 16-name limit; never their values.
            'unknown_key_count':{level:len(keys) for level,keys in unknown.items()}}
    if isinstance(body,str):
        length=len(body)
        result['response_length_bucket']=('0' if length==0 else '1-64' if length<=64 else '65-1024' if length<=1024 else '1025-16384' if length<=16384 else '16385+')
    return result
