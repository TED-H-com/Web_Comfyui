"""Owner-authorized r31 revalidation and one reviewed r30 pre-reserve rejection."""
import json,time
from pathlib import Path
from compute_routing import ComputeRoutingError

def revalidate(web,lane):
    from .contracts import fail
    if lane not in {'colab','runninghub'}:fail('revalidation_forbidden','此版本僅核准 Colab／RunningHub 修正後重新驗證')
    reviewed=json.loads(Path(__file__).with_name('reviewed_predispatch.json').read_text())
    if lane=='colab' and reviewed['owner']==web.owner:
        try:row=web.router._get(web.owner,reviewed['request_id'])
        except ComputeRoutingError as e:
            if e.code!='not_found':raise
            row=None
        if row is not None and row['state']=='submission_uncertain':
            rid=reviewed['request_id'];receipt=json.loads(row['receipt_json'])
            if row['lane']!='colab' or row['request_hash']!=reviewed['request_hash'] or row['created']!=reviewed['created'] or receipt.get('downstream_id'):
                fail('revalidation_evidence_changed','原請求綁定已變更')
            if web.parity.journal.get(web.owner,rid):fail('revalidation_evidence_changed','原請求已有派送記錄')
            # Exact durable Colab journal lookup; never a provider query.
            with web.colab.db() as db:
                exists=db.execute('SELECT 1 FROM jobs WHERE id=?',(rid,)).fetchone()
            if exists:fail('revalidation_evidence_changed','原請求已有 Colab 工作')
            with web.router._db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT * FROM compute_requests WHERE owner=? AND request_id=?',(web.owner,rid)).fetchone()
                if current['state']!='submission_uncertain' or current['receipt_json']!=row['receipt_json']:
                    fail('revalidation_evidence_changed','原請求已更新')
                receipt.update(state='rejected',cancel_supported=False,error={'code':'lane_stopped','message':'原請求在派送前遭停止鎖拒絕'},
                    dispatch_evidence={'stage':'pre_dispatch_reserve','parity_job_absent':True,'colab_job_absent':True,
                                       'owner_authorized':True,'reviewed_release':'20261111-r30','at':time.time(),'new_submissions':0})
                db.execute('UPDATE compute_requests SET state=?,receipt_json=?,updated=? WHERE owner=? AND request_id=?',
                           ('rejected',json.dumps(receipt),time.time(),web.owner,rid))
    return web.parity.journal.revalidate(web.owner,lane,'20261111-r31',authorized=True)
