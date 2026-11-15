"""Speaker gate diagnostics contain no text, paths, audio or command lines."""
import json,math

def sanitize(value):
    if not isinstance(value,dict) or value.get('stage')!='speaker_gate':return {}
    if any(type(value.get(k)) not in (float,int) or not math.isfinite(value[k]) or not -1<=value[k]<=1 for k in ('best_similarity','threshold')):return {}
    if type(value.get('attempts')) is not int or not 1<=value['attempts']<=100:return {}
    return {'stage':'speaker_gate',**{k:value[k] for k in ('best_similarity','threshold','attempts')}}

class QualityRejected(ValueError):
    def __init__(self,diagnostics):
        self.diagnostics=sanitize(diagnostics)
        super().__init__(json.dumps({'code':'quality_rejected','diagnostics':self.diagnostics},sort_keys=True))

def from_error(value):
    try:body=json.loads(value)
    except (ValueError,TypeError):return {}
    return sanitize(body.get('diagnostics')) if isinstance(body,dict) and body.get('code')=='quality_rejected' else {}
