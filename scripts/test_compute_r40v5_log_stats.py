import pytest
from review_r38_fixture import rig

@pytest.mark.parametrize('stats,expected',[
 ({'log_bytes':987654321,'previous_log_bytes':876543219},True),
 ({'log_bytes':True,'previous_log_bytes':0},False),
 ({'log_bytes':1.2,'previous_log_bytes':0},False),
 ({'log_bytes':-1,'previous_log_bytes':0},False),
 ({'log_bytes':2**40+1,'previous_log_bytes':0},False),
 ({'log_bytes':1,'previous_log_bytes':0,'other':1},False),
 ({'log_bytes':1},False),([],False),('text',False)])
def test_private_log_sizes(rig,stats,expected):
 import json
 from test_review_r38_faults import running,probe_kind
 r=rig;st,job=running(r)
 def hook(op,kw):
  if probe_kind(op,kw)=='job_probe':
   return {'stdout':'WEB_COMFYUI_JSON='+json.dumps({'status':{'job_id':job['id'],'manifest_sha256':job['manifest_sha256'],'phase':'running'},'worker_dead':False,'log_stats':stats})}
 r.b.hook=hook;r.s.tick(job);current=r.s.sessions.get(st['id'])
 assert ('private_log_stats' in current)==expected
 assert 'private_log_stats' not in str(r.s.sessions.public(current))
 assert 'log_bytes' not in str(r.s.public_job(r.s.job(r.owner,job['id']),owner=r.owner))
 assert '987654321' not in str(r.s.snapshot(r.owner)) and '876543219' not in str(r.s.snapshot(r.owner))


def test_missing_remote_log_does_not_change_probe_result(rig,tmp_path,monkeypatch):
 import contextlib,io,json
 from test_review_r38_faults import running
 r=rig;st,job=running(r);captured=[]
 def probe(state,code):
  captured.append(code)
  return {'status':{'job_id':job['id'],'manifest_sha256':job['manifest_sha256'],'phase':'running'},'worker_dead':False}
 monkeypatch.setattr(r.s,'_exec',probe);r.s.tick(job)
 code=captured[-1].replace('/content/web-comfyui/',str(tmp_path/'jobs')+'/').replace('/content/castle-sessions/',str(tmp_path/'sessions')+'/')
 out=io.StringIO()
 with contextlib.redirect_stdout(out):exec(compile(code,'remote-probe','exec'),{})
 got=json.loads(out.getvalue().split('WEB_COMFYUI_JSON=',1)[1])
 assert got=={'status':None,'artifacts':None,'worker_dead':False}
 assert 'private_log_stats' not in r.s.sessions.get(st['id'])


def test_reused_kernel_discards_previous_stats_when_lstat_fails(rig,tmp_path,monkeypatch):
 import contextlib,io,json
 from test_review_r38_faults import running
 r=rig;st,job=running(r);captured=[]
 def probe(state,code):
  captured.append(code)
  return {'status':{'job_id':job['id'],'manifest_sha256':job['manifest_sha256'],'phase':'running'},'worker_dead':False}
 monkeypatch.setattr(r.s,'_exec',probe);r.s.tick(job)
 code=captured[-1].replace('/content/web-comfyui/',str(tmp_path/'jobs')+'/').replace('/content/castle-sessions/',str(tmp_path/'sessions')+'/')
 log=tmp_path/'sessions'/st['id']/'job-evidence'/job['id']/'comfy-startup.log'
 log.parent.mkdir(parents=True);log.write_text('sample')
 namespace={}
 def run():
  out=io.StringIO()
  with contextlib.redirect_stdout(out):exec(compile(code,'remote-probe','exec'),namespace)
  return json.loads(out.getvalue().split('WEB_COMFYUI_JSON=',1)[1])
 assert run()['log_stats']=={'log_bytes':6,'previous_log_bytes':0}
 original=type(log).lstat
 def blocked(path,*a,**k):
  if path==log:raise PermissionError('private')
  return original(path,*a,**k)
 monkeypatch.setattr(type(log),'lstat',blocked)
 assert 'log_stats' not in run()
