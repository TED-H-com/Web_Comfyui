"""Provider H3 workers share the gate without inheriting ambient credentials."""
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webui"))
from webui.provider_fabric import h3_gpu_executor as executor


def test_lifecycle_overrides_survive_sanitization_without_secret_passthrough(tmp_path):
    token = 'a' * 64
    allowed = {'CASTLE_COMFY_ADMISSION_LOCK':str(tmp_path/'admission.lock'),
               'CASTLE_WEB_PYTHON':'/synthetic/web/python'}
    ambient = {**allowed, 'PATH':'/synthetic/bin',
               'GOOGLE_APPLICATION_CREDENTIALS':'/synthetic/never-inherit.json',
               'RUNNINGHUB_API_KEY':'synthetic-not-a-secret',
               'CASTLE_PROVIDER_API_KEY':'synthetic-not-a-secret',
               'PYTHONPATH':'/synthetic/injected',
               'LD_PRELOAD':'/synthetic/injected.so',
               executor._WORKER_TOKEN_ENV:'b'*64,
               'XDG_RUNTIME_DIR':'/synthetic/wrong-bus',
               'DBUS_SESSION_BUS_ADDRESS':'unix:path=/synthetic/wrong-bus'}
    actual_stat = Path.stat
    runtime = Path('/run/user') / str(os.geteuid())
    def scoped_stat(path, *args, **kwargs):
        if path == runtime:
            return SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=os.geteuid())
        return actual_stat(path,*args,**kwargs)
    with patch.dict(os.environ, ambient, clear=True), patch.object(Path,'stat',scoped_stat):
        environment=executor._worker_environment(token=token,state_root=tmp_path)
    assert {name:environment[name] for name in allowed} == allowed
    assert set(environment) == {*allowed, 'PATH','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS',
                                 executor._WORKER_TOKEN_ENV,executor._WORKER_STATE_ENV,'PYTHONUNBUFFERED'}
    assert environment[executor._WORKER_TOKEN_ENV] == token
    assert environment[executor._WORKER_STATE_ENV] == str(tmp_path)
    assert environment['XDG_RUNTIME_DIR'] == str(runtime)
    assert environment['DBUS_SESSION_BUS_ADDRESS'] == f'unix:path={runtime}/bus'


def test_default_worker_does_not_invent_lifecycle_overrides(tmp_path):
    with patch.dict(os.environ, {}, clear=True), patch.object(Path,'stat',
          return_value=SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=os.geteuid())):
        environment=executor._worker_environment(token='a'*64,state_root=tmp_path)
    assert 'CASTLE_COMFY_ADMISSION_LOCK' not in environment
    assert 'CASTLE_WEB_PYTHON' not in environment
