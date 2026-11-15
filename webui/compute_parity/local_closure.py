"""Explicit owner closure of an old routed local ID. No inference of non-execution."""
import hashlib,json,time
from urllib.parse import quote
from compute_routing import ComputeRoutingError

STATE='closed_execution_outcome_unknown'
def close(web,rid,body):
    if body.get('authorize') is not True:raise ComputeRoutingError('需要擁有者明確授權',code='closure_authorization_required')
    row=web.router._get(web.owner,rid)
    receipt=json.loads(row['receipt_json']);downstream=receipt.get('downstream_id')
    if row['lane']!='local' or not downstream or body.get('downstream_id')!=downstream:
        raise ComputeRoutingError('原任務綁定不符',code='closure_identity_mismatch')
    if row['state']==STATE:return web.router._view(row)
    if row['state']!='submission_uncertain' or time.time()-row['created']<3600:
        raise ComputeRoutingError('原任務尚不符合未知結果結案條件',code='closure_not_ready')
    if getattr(web,'parity',None) and web.parity.owns(web.owner,'local',rid):
        raise ComputeRoutingError('此操作只適用舊本機 routed 回執',code='closure_not_legacy')
    # Read the exact executor through the same configured adapter used for status.
    indexed=web.s.load_job_index().get(downstream,{})
    if indexed.get('email')!=web.owner or not web.s.user_can_access_job(web.user,downstream):
        raise PermissionError('找不到自己的原任務')
    if (web._durable_local_result(downstream) is not None or web._direct_local_result(downstream) is not None
            or any(x.get('promptId')==downstream for x in web.s.local_job_snapshot())):
        raise ComputeRoutingError('已有持久化回執或成果；請查詢原任務',code='closure_execution_present')
    history=web.s.request_json('/history/'+quote(downstream,safe=''));queue=web.s.request_json('/queue')
    if not isinstance(history,dict) or not isinstance(queue,dict) or any(not isinstance(queue.get(k),list) for k in ('queue_running','queue_pending')):
        raise ComputeRoutingError('執行器查核未完成',code='closure_evidence_incomplete')
    entries=queue['queue_running']+queue['queue_pending']
    if downstream in history or any(not isinstance(x,(tuple,list)) or len(x)<2 or x[1]==downstream for x in entries):
        raise ComputeRoutingError('原任務有執行記錄或佇列查核不完整',code='closure_execution_present')
    executor=hashlib.sha256(str(getattr(web.s,'DEFAULT_COMFY','http://127.0.0.1:8188')).encode()).hexdigest()
    observation={'at':time.time(),'executor':executor,'history_absent':True,'queue_absent':True,
                 'durable_absent':True,'sidecar_absent':True,'job_index_owner_verified':True,
                 'queue_count':len(entries),'snapshot_sha256':hashlib.sha256(json.dumps([sorted(history),[x[1] for x in entries]],sort_keys=True).encode()).hexdigest()}
    with web.router._db() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?',(web.owner,rid)).fetchone()
        if current['state']==STATE:return web.router._view(current)
        if current['receipt_json']!=row['receipt_json'] or current['state']!=row['state']:
            raise ComputeRoutingError('原任務已更新；請重新查核',code='closure_changed')
        evidence=receipt.get('closure_evidence',{});observations=evidence.get('observations',[])
        if not observations or observation['at']-observations[-1]['at']>=60:observations=(observations+[observation])[-32:]
        ready=any(o['executor']==executor and observation['at']-o['at']>=60 for o in observations)
        receipt.update(closure_evidence={'observations':observations,'owner_authorized':True,'new_submissions':0},
                       execution_outcome='unknown',artifact_outcome='unknown')
        if ready:receipt.update(state=STATE,cancel_supported=False,settlement_status='unknown')
        db.execute('UPDATE compute_requests SET state=?,receipt_json=?,updated=? WHERE owner=? AND request_id=?',
                   (receipt['state'],json.dumps(receipt),time.time(),web.owner,rid))
    return web.router.status(web.owner,rid,refresh=False)

def reconcile(web,rid,body):
    if body.get('authorize') is not True:raise ComputeRoutingError('需要擁有者明確授權')
    row=web.router._get(web.owner,rid);receipt=json.loads(row['receipt_json']);did=receipt.get('downstream_id')
    if row['lane']!='local' or row['state']!=STATE or body.get('downstream_id')!=did:raise ComputeRoutingError('原任務綁定不符')
    result=web.status_local(web.owner,did)
    if result.get('downstream_id')!=did or result.get('state')!='completed' or not result.get('artifacts'):
        raise ComputeRoutingError('尚無可信的晚到成果',code='reconciliation_not_ready')
    from compute_routing import _public_receipt
    verified=_public_receipt(result)
    with web.router._db() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?',(web.owner,rid)).fetchone()
        if current['receipt_json']!=row['receipt_json']:raise ComputeRoutingError('原任務已更新')
        receipt.setdefault('late_reconciliation',{'at':time.time(),'owner_authorized':True,'receipt':verified})
        db.execute('UPDATE compute_requests SET receipt_json=?,updated=? WHERE owner=? AND request_id=?',(json.dumps(receipt),time.time(),web.owner,rid))
    return web.router.status(web.owner,rid,refresh=False)
