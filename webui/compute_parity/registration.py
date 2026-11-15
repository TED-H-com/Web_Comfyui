"""Small route hook. Remote P1 stays disabled until a reviewed runtime is installed.

A runtime is an explicit server-side object, never a browser flag or an env key.
Default staging/deployed code cannot contact paid providers through these routes.
"""
from compute_routing import ComputeAdapter, ComputeRejected
from .contracts import FEATURES, UNITS, lane_description

BUILD_REASON='parity_build_only'

def describe(request,lane):
    settings=request.get('settings') or {}
    return lane_description(request.get('featureId'),lane,
        private=request.get('featureId')=='voice-speak' and settings.get('voiceSource','persona')!='library')

def register(web):
    runtime=getattr(web.s,'compute_parity_runtime',None)
    if runtime is None and hasattr(web,'console'):
        from .product import ProductRuntime
        runtime=ProductRuntime(web)
    web.parity=runtime
    for lane,old in list(web.router.adapters.items()):
        def inspect(owner,request,limits,lane=lane,old=old):
            if request.get('featureId') not in FEATURES:return old.inspect(owner,request,limits)
            description=describe(request,lane)
            if hasattr(web,'_gate'):
                blocked=web._gate(owner,lane)
                if blocked:return {**blocked,**description}
            if lane=='local' and not request.get('parity_case_id'):return {**old.inspect(owner,request,limits),**description}
            reason=BUILD_REASON
            if runtime is not None:
                return {**runtime.inspect(owner,lane,request,limits),**description}
            return {'eligible':False,'auto_eligible':False,'reasons':[reason],**description}
        def submit(owner,rid,request,limits,lane=lane,old=old):
            if request.get('featureId') not in FEATURES or (lane=='local' and not request.get('parity_case_id')):return old.submit(owner,rid,request,limits)
            if runtime is None:raise ComputeRejected('原生配方已建置，待校準授權；尚未提交',code=BUILD_REASON)
            return runtime.submit(owner,lane,rid,request,limits)
        def status(owner,rid,lane=lane,old=old):
            if runtime is not None and runtime.owns(owner,lane,rid):return runtime.status(owner,lane,rid)
            return old.status(owner,rid) if old.status else None
        def cancel(owner,rid,lane=lane,old=old):
            if runtime is not None and runtime.owns(owner,lane,rid):return runtime.cancel(owner,lane,rid)
            return old.cancel(owner,rid) if old.cancel else None
        def recover(owner,rid,lane=lane,old=old):
            if runtime is not None and runtime.owns(owner,lane,rid):return runtime.status(owner,lane,rid)
            return old.recover(owner,rid) if old.recover else None
        web.router.adapters[lane]=ComputeAdapter(inspect,submit,status,cancel,recover)

def annotate_preview(result,request):
    for row in result.get('candidates',[]):
        desc=describe(request,row['lane'])
        if desc:row.update(desc)
    return result
