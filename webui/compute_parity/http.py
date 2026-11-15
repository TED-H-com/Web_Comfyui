"""Authenticated owner-only calibration catalog and results."""
def handle(web,handler,parts,mutate,body):
    if not parts or parts[0]!='parity':return False
    from compute_routing import ComputeRoutingError
    if mutate and parts==['parity','refresh']:
        from .quotes import refresh
        if set(body)-{'request','limits','estimates'}:raise ComputeRoutingError('無效欄位')
        try:result=refresh(web.parity,web.owner,body.get('request') or {},body.get('limits') or {},body.get('estimates') or {})
        except Exception:raise ComputeRoutingError('請先選擇自己的校準素材',code='parity_case_required') from None
        handler.send_json(result,cache_control='private, no-store');return True
    if mutate and parts==['parity','revalidate']:
        from .contracts import ParityError
        if set(body)-{'lane','authorize'} or body.get('authorize') is not True:raise ComputeRoutingError('需要擁有者明確授權',code='revalidation_forbidden')
        from .revalidation import revalidate
        try:result=revalidate(web,body.get('lane'))
        except ParityError as error:raise ComputeRoutingError(str(error),code=error.code) from None
        handler.send_json(result,cache_control='private, no-store');return True
    if mutate:raise ComputeRoutingError('不支援的校準操作',status=405)
    if parts==['parity','cases']:
        from .contracts import FEATURES
        handler.send_json({'cases':[c for f in sorted(FEATURES) for c in web.parity.materials.choices(f)]},cache_control='private, no-store')
    elif parts==['parity','ledger']:
        handler.send_json({'lanes':web.parity.journal.ledger(web.owner)},cache_control='private, no-store')
    elif len(parts)==4 and parts[1]=='files':
        try:path=web.parity.artifact(web.owner,parts[2],int(parts[3]))
        except Exception:raise ComputeRoutingError('找不到自己的成果',code='not_found',status=404) from None
        handler.send_local_file(path,inline_filename=path.name,cache_control='private, no-store')
    else:raise ComputeRoutingError('找不到校準操作',status=404)
    return True
