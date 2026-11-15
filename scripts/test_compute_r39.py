"""r39 T2/T3/T4/T5: mocked subprocess/network only; real parser/privacy boundaries."""
import copy,hashlib,io,json,subprocess,tarfile
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts import colab_session_voice as v,colab_session_diagnostics as d
from scripts.colab_remote import native_runner as runner
ROOT=Path(__file__).resolve().parents[1]

def registry():return json.loads(v.REGISTRY.read_text())

@pytest.mark.parametrize('kind',['comfy','voice'])
def test_python_archive_and_executable_hashes_fail_closed(tmp_path,monkeypatch,kind):
 pin=registry()['interpreters'][kind];raw=b'bad';pin.update(bytes=len(raw))
 monkeypatch.setattr(v.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(raw))
 with pytest.raises(v.PythonMismatch):v.provision_python(tmp_path,pin,lambda *a,**k:pytest.fail('exec before hash'))
 assert list((tmp_path/'python-builds').iterdir())==[]

def test_provision_verifies_archive_binary_and_version(tmp_path,monkeypatch):
 pin=registry()['interpreters']['comfy'];buf=io.BytesIO();payload=b'python'
 with tarfile.open(fileobj=buf,mode='w:gz') as source:
  info=tarfile.TarInfo(pin['executable']);info.size=len(payload);source.addfile(info,io.BytesIO(payload))
 raw=buf.getvalue();pin.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),executable_sha256=hashlib.sha256(payload).hexdigest())
 monkeypatch.setattr(v.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(raw))
 called=[]
 def run(*a,**k):called.append(a);return NS(stdout='3.12.13\n')
 python=v.provision_python(tmp_path,pin,run);assert Path(python).read_bytes()==payload and len(called)==2
 Path(python).write_bytes(b'changed')
 with pytest.raises(v.PythonMismatch):v.provision_python(tmp_path,pin,run)





@pytest.mark.parametrize('raw,forbidden,replacement',[
 ('https://example.invalid/?secret=abc','example.invalid','<url>'),
 ('/home/private/folder/file.txt','/home','<path>'),
 ('file=/home/private/folder/file.txt','/home','<path>'),
 ('file=C:\\Users\\private\\file','Users','<path>'),
 ('person@example.invalid','person','<email>'),
 ('0123456789abcdef'*4,'0123456789','<redacted>'),
 ('abCDefGHIJKL0123+/xyz'*3,'abCDef','<redacted>'),
 ('C:\\Users\\private\\file','Users','<path>')])
def test_excerpt_masks_each_class(raw,forbidden,replacement):
 value=d.stderr_excerpt(raw);assert forbidden not in value and replacement in value

def test_excerpt_bounds_and_public_sanitizer():
 raw='\n'.join('line '+str(i)+' '+('x '*300) for i in range(30));value=d.stderr_excerpt(raw)
 assert len(value.splitlines())==12 and all(len(x)<=200 for x in value.splitlines())
 assert 'private_bootstrap_stderr' not in d.sanitize_diagnostics({'private_bootstrap_stderr':value})
 from compute_parity.adapters import public
 from compute_routing import _public_receipt
 receipt={'state':'failed','private_bootstrap_stderr':value}
 assert 'private_bootstrap_stderr' not in public(receipt)
 assert 'private_bootstrap_stderr' not in _public_receipt(receipt)

@pytest.mark.parametrize('label',sorted(d.COMMANDS))
def test_command_exit_distribution_survive(label):
 e=subprocess.CalledProcessError(1,['private']);e.command_label=label;e.failing_distribution='numpy==1.26.4'
 result=d.failure_diagnostics(e,'runtime_lock')
 assert result['command_label']==label and result['exit_code']==1 and result['failing_distribution']=='numpy==1.26.4'
 assert d.sanitize_diagnostics({'failing_distribution':'https://secret.invalid/x'})=={}



def test_worker_publishes_masked_stderr_and_command(tmp_path,monkeypatch):
 from test_compute_r35 import load_worker
 worker=load_worker(monkeypatch)
 (tmp_path/'bootstrap.json').write_text('[{"models":[]}]')
 monkeypatch.setattr(worker.subprocess,'check_output',lambda *a,**k:'NVIDIA A100, 40960')
 def bootstrap(p,cache,run,stage):run([str(cache/'venv/bin/python'),'-m','pip','install','numpy'],check=True)
 monkeypatch.setattr(worker,'bootstrap',bootstrap)
 def guarded(p,c,args,run,kw):
  run([str(c/'venv/bin/python'),'-I','-c','import sys;print(sys.version_info)'],check=True)
  return run(args,**kw)
 monkeypatch.setattr(worker,'run_command',guarded)
 def fail(args,**kw):
  if '-I' in args:return NS(stdout='3.12.13')
  kw['stderr'].write(b'ERROR: No matching distribution found for numpy==1.26.4\nhttps://private.invalid/x /home/private/file person@example.invalid\n');raise subprocess.CalledProcessError(1,args)
 monkeypatch.setattr(worker.subprocess,'run',fail)
 assert worker.bootstrap_session(tmp_path)==1
 result=json.loads((tmp_path/'bootstrap-status.json').read_text())
 assert result['diagnostics']['command_label']=='pip_core'
 assert result['diagnostics']['failing_distribution']=='numpy==1.26.4'
 assert '<url>' in result['private_bootstrap_stderr'] and 'private.invalid' not in result['private_bootstrap_stderr']
 assert result['venv_python_version']=='3.12.13'
 assert set(result['system_python'])=={'version','ensurepip_available','headers_available'}

@pytest.mark.parametrize('text',['ftp://private.invalid/file','token=short-secret','Authorization: Bearer short-secret','cookie=short-secret'])
def test_excerpt_additional_secrets(text):
 assert 'private.invalid' not in d.stderr_excerpt(text)
 assert 'short-secret' not in d.stderr_excerpt(text)

def test_private_excerpt_is_durable_but_not_public_receipt(tmp_path,monkeypatch):
 from test_compute_r35 import rig as fixture,native,ready,OWNER
 # Fixture is consumed directly through the same offline service setup.
 r=fixture.__wrapped__(tmp_path,monkeypatch);s,bridge,clock,account,bundle=r
 state=ready(r);job=native(r);pid=job['bootstrap_plan_id']
 state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=pid,private_bootstrap_stderr='ERROR: ensurepip missing')
 monkeypatch.setattr(s,'_exec',lambda *a,**k:{'clean':True})
 diagnostic={'stage':'session_bootstrap','substage':'venv_create','command_label':'venv_create','error_type':'CalledProcessError','error_code':'stage_failed','exit_code':1}
 s.sessions.fail_plan(state,diagnostic,'bootstrap_failed')
 from compute_parity.journal import Journal
 from compute_parity.colab import ColabNative
 journal=Journal(tmp_path/'parity-journal.sqlite3');journal.reserve(OWNER,job['id'],'colab',account,bundle['case_id'],'test',{'unit':'CU','amount':15})
 result=ColabNative(s,journal)._record(OWNER,job['id'],s.public_job(s.job(OWNER,job['id'])))
 assert journal.get(OWNER,job['id'])['private_bootstrap_stderr']=='ERROR: ensurepip missing'
 assert 'private_bootstrap_stderr' not in result
 assert 'ensurepip missing' not in json.dumps(s.sessions.snapshot(OWNER))
 assert 'ensurepip missing' not in json.dumps(s.snapshot(OWNER))
 assert result['diagnostics']['command_label']=='venv_create'


def test_later_job_gets_its_failed_plan_excerpt_not_another_plans(tmp_path,monkeypatch):
 from test_compute_r35 import rig as fixture,native,ready,OWNER
 r=fixture.__wrapped__(tmp_path,monkeypatch);s,bridge,clock,account,bundle=r
 state=ready(r);first=native(r);pid=first['bootstrap_plan_id']
 diagnostic={'stage':'session_bootstrap','substage':'pip_core','command_label':'pip_core','error_code':'stage_failed','exit_code':1}
 state=s.sessions.save(state['id'],phase='bootstrap',bootstrap_plan_id=pid,private_bootstrap_stderr='first plan stderr')
 monkeypatch.setattr(s,'_exec',lambda *a,**k:{'clean':True})
 s.sessions.fail_plan(state,diagnostic,'bootstrap_failed')
 # Other admitted plans can run; a later request for the already failed plan
 # must retain that plan's evidence, never the last observed plan's excerpt.
 s.sessions.save(state['id'],phase='ready',private_bootstrap_stderr='another plan stderr')
 later=native(r)
 assert later['session_id']==state['id']
 current=s.sessions.get(state['id'])
 s.sessions.fail_plan({**current,'bootstrap_plan_id':pid},diagnostic,'bootstrap_failed')
 assert s.job(OWNER,later['id'])['private_bootstrap_stderr']=='first plan stderr'
 assert 'first plan stderr' not in json.dumps(s.sessions.snapshot(OWNER))


@pytest.fixture(autouse=True)
def one_voice_module(monkeypatch):
 import sys
 monkeypatch.setitem(sys.modules,'colab_session_voice',v)
