"""Durable RunningHub POINT observation via the same console test operation as HTTP.

Only the first completed calibration starts this monitor. A pending observation
blocks further jobs, so new generation spend cannot contaminate the idle check.
"""
import json
import threading
import time
from .readiness import entries, save
from .contracts import number

_offsets=(300,900,1800,3600)
_workers=set()
_lock=threading.Lock()

def monitor_jobs(journal,owner):
    with journal.db() as db:
        rows=db.execute("SELECT body FROM jobs WHERE owner=? AND lane='runninghub'",(owner,)).fetchall()
    return [json.loads(row[0]) for row in rows if json.loads(row[0]).get('lifecycle_monitor')]

def pending_monitor(journal,owner):
    monitors=monitor_jobs(journal,owner)
    if monitors:return any(j['lifecycle_monitor']['state']!='verified' for j in monitors)
    with journal.db() as db:
        return db.execute("SELECT 1 FROM jobs WHERE owner=? AND lane='runninghub' AND state='completed' LIMIT 1",(owner,)).fetchone() is not None

def balance(console):
    # Console.test is the implementation of POST console/<provider>/test.
    with console.prefs.db() as db:data=console._raw('runninghub',db)
    result=console.test('runninghub',{'revision':data['revision']})
    card=next(c for c in result['connections'] if c['provider']=='runninghub')
    value=next((b['value'] for b in card.get('balances',[]) if b.get('unit')=='POINT'),None)
    if card.get('connection_state')!='connected' or not number(value):
        raise ValueError('point_balance_unavailable')
    return value

def ensure_monitor(console,journal,owner,rid,now=None):
    now=time.time() if now is None else now
    job=journal.get(owner,rid)
    if job.get('lane')!='runninghub' or job.get('state')!='completed':return
    if job.get('lifecycle_monitor') or monitor_jobs(journal,owner):return
    with console.prefs.db() as db:version=console._raw('runninghub',db)['credential_version']
    journal.update(owner,rid,lifecycle_monitor={'state':'pending','completed_at':now,'credential_version':version,'observations':[]})

def tick(console,journal,now=None,observe=None):
    now=time.time() if now is None else now;observe=observe or (lambda:balance(console))
    for job in monitor_jobs(journal,console.owner):
        m=job['lifecycle_monitor']
        if m['state']!='pending':continue
        with console.prefs.db() as db:version=console._raw('runninghub',db)['credential_version']
        if version!=m['credential_version']:continue  # never observe a different account/key
        seen={o['offset_seconds'] for o in m['observations']}
        due=next((o for o in (0,)+_offsets if o not in seen and now>=m['completed_at']+o),None)
        if due is None:continue
        try:value=observe()
        except Exception:continue  # no raw errors; persist intent and retry read only
        m['observations'].append({'at':now,'offset_seconds':due,'POINT':value,'drift':value-m['observations'][0]['POINT'] if m['observations'] else 0})
        if due and value<m['observations'][0]['POINT']:m['state']='stopped_drain'
        elif due==3600:
            m['state']='verified' if all(0<=o['at']-m['completed_at']-o['offset_seconds']<=90 for o in m['observations']) else 'observation_gap'
        journal.update(console.owner,job['request_id'],lifecycle_monitor=m)
        if m['state']!='pending':
            for e in entries(console.root/'compute-parity'):
                if e.get('owner')==console.owner and e.get('lane')=='runninghub' and e.get('credential_version')==version:
                    e.update(lifecycle_mode='lifecycle_verified' if m['state']=='verified' else ('stopped_drain' if m['state']=='stopped_drain' else 'stopped_observation_gap'),
                        lifecycle_verified=m['state']=='verified',lifecycle_observations=m['observations'],
                        verification_note='POINT 閒置觀察通過（實測）' if m['state']=='verified' else ('POINT 餘額下降，平台已停止，待對帳' if m['state']=='stopped_drain' else '錯過指定監測時間，平台暫停，待對帳'))
                    save(console.root/'compute-parity',e)

def start_monitor(console,journal):
    if not callable(getattr(console,'test',None)):return
    key=(str(journal.path),console.owner)
    with _lock:
        if key in _workers:return
        _workers.add(key)
    def run():
        pause=threading.Event()
        while True:
            try:tick(console,journal)
            except Exception:pass
            pause.wait(30)
    threading.Thread(target=run,name='runninghub-point-monitor',daemon=True).start()
