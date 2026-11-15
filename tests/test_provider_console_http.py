"""Real Handler + session cookies + isolated SQLite/vault, with provider fakes."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
import uuid
from types import SimpleNamespace

import pytest
from test_provider_console import env, card, write, SECRET

@pytest.fixture
def http_console(env,monkeypatch):
    root=env.root
    monkeypatch.setenv('XDG_DATA_HOME',str(root/'xdg'))
    monkeypatch.setenv('CASTLE_COMFY_AUTH_PATH',str(root/'auth.json'))
    monkeypatch.setenv('CASTLE_PROVENANCE_KEY_PATH',str(root/'provenance.key'))
    monkeypatch.setenv('CASTLE_COMFY_USERS_PATH',str(root/'users.json'))
    monkeypatch.setenv('CASTLE_COMFY_SESSION_STORE_PATH',str(root/'auth'/'sessions.json'))
    monkeypatch.setenv('CASTLE_COLAB_ROOT',str(root/'colab'))
    spec=importlib.util.spec_from_file_location('console_test_server',Path(__file__).resolve().parents[1]/'webui/server.py')
    server=importlib.util.module_from_spec(spec);sys.modules[spec.name]=server;spec.loader.exec_module(server)
    server._COLAB_SERVICE=env.colab
    from hosted_compute import HostedComputeService
    server._HOSTED_COMPUTE_SERVICE=HostedComputeService(root/'hosted-compute')
    users={email:{'role':role,'displayName':name,'passwordHash':server.password_hash('synthetic-test-password'),'mustSetPassword':False,'disabled':False}
        for email,role,name in [('other@example.invalid','user','Alice'),('bob@example.test','user','Bob'),('owner@example.invalid','admin','Owner（測試帳號）')]}
    server.save_users(users)
    sessions={email:server.session_create({**value,'email':email}) for email,value in users.items()}
    logs=[];calls=[]
    class Handler(server.Handler):
        def _provider_runtime(self): return env.runtime
        def _provider_console(self):
            user=self.current_user() or {}
            return env.console(user.get('email'),user.get('role'),probes={p:lambda data,p=p: calls.append(p) or {'connected':True,'balances':[]} for p in ('local','autoip','colab','runninghub')},local_check=lambda:True)
        def log_message(self,fmt,*args): logs.append(fmt%args)
        def do_GET(self):
            if self.path.startswith('/api/auth/status'):
                self.send_json({'ok':True,'configured':True,'authenticated':bool(self.current_user()),'user':self.current_user(),'lane':'local','needsSetup':False});return
            if self.path.startswith(('/api/compute/connections','/api/compute/preferences','/api/compute/platform-admin','/api/compute/jobs','/api/colab','/static/')) or self.path=='/':
                return super().do_GET()
            if self.path=='/api/compute/providers':
                self.send_json({'providers':[{'provider':'runninghub','credential_configured':True,'enabled':False,'endpoint':'https://www.runninghub.ai','recipes':[]}]});return
            if self.path=='/api/compute/capabilities':self.send_json({'ok':True,'features':[]});return
            # Unrelated app requests have inert data; never touch real runtime/state.
            self.send_json({'ok':True,'features':[],'studios':[],'characters':[],'actions':[],'jobs':[],'items':[],'providers':[],'assets':[],'tasks':[],'runs':[]})
        def do_POST(self):
            if self.path.startswith(('/api/compute/connections','/api/compute/preferences','/api/colab')):return super().do_POST()
            self.send_json({'ok':False},404)
    http=server.CastleThreadingHTTPServer(('127.0.0.1',0),Handler)
    origin=f'http://127.0.0.1:{http.server_port}';server.ALLOWED_ORIGIN_HOSTS={f'127.0.0.1:{http.server_port}'}
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    def request(method,path,body=None,owner='other@example.invalid',origin_value=origin):
        headers={'Content-Type':'application/json','Origin':origin_value}
        if owner:headers['Cookie']=f'{server.SESSION_COOKIE}={sessions[owner]}'
        req=urllib.request.Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        try:response=urllib.request.urlopen(req,timeout=10)
        except urllib.error.HTTPError as error:response=error
        return response.status,json.loads(response.read()),dict(response.headers)
    yield SimpleNamespace(env=env,request=request,origin=origin,logs=logs,calls=calls,sessions=sessions,server=server)
    http.shutdown();http.server_close();thread.join(5)


def test_http_write_only_and_session_owner(http_console):
    h=http_console;operation=str(uuid.uuid4());payload={'credential':SECRET,'revision':0,'operation_id':operation}
    status,result,headers=h.request('PUT','/api/compute/connections/runninghub/credential',payload)
    assert status==200 and headers['Cache-Control']=='private, no-store'
    assert SECRET not in json.dumps(result) and not h.calls
    for owner in ('bob@example.test','owner@example.invalid'):
        status,result,_=h.request('GET','/api/compute/connections',owner=owner)
        assert status==200 and not next(c for c in result['connections'] if c['provider']=='runninghub')['credential_present']
        assert h.request('POST','/api/compute/connections/runninghub/test',{'revision':0},owner=owner)[0]==409
    assert h.request('GET','/api/compute/connections/runninghub/credential')[0]==404
    assert h.request('GET','/api/compute/connections?owner=bob@example.test')[0]==400
    assert h.request('PUT','/api/compute/connections/runninghub/credential',{**payload,'owner':'bob@example.test'})[0]==400
    assert h.request('GET','/api/compute/connections',owner=None)[0]==401
    assert h.request('PUT','/api/compute/connections/runninghub/credential',payload,origin_value='https://evil.example')[0]==403
    assert SECRET not in '\n'.join(h.logs)
    assert h.request('PATCH','/api/compute/platform-admin/local-grant',{'email':'bob@example.test','allowed':True})[0]==403
    assert h.request('GET','/api/compute/platform-admin')[0]==403
    status,result,_=h.request('GET','/api/compute/platform-admin',owner='owner@example.invalid')
    assert status==200 and 'balances' not in json.dumps(result) and SECRET not in json.dumps(result)
    assert h.request('POST','/api/compute/connections/runninghub/test',{'revision':1})[0]==200
    assert h.calls==['runninghub']


def test_http_colab_cross_owner_and_disabled_session(http_console):
    h=http_console
    _,account,_=h.request('POST','/api/colab/accounts',{'label':'我的 Google 帳號'})
    assert h.request('POST',f"/api/colab/accounts/{account['id']}/login",{},owner='bob@example.test')[0]==400
    assert h.request('POST',f"/api/colab/accounts/{account['id']}/refresh",{'owner':'bob@example.test'})[0]==400
    status,snapshot,_=h.request('GET','/api/colab',owner='bob@example.test');assert snapshot['accounts']==[]
    users=h.server.load_users();users['other@example.invalid']['disabled']=True;h.server.save_users(users)
    assert h.request('GET','/api/compute/connections')[0]==401


def test_http_cross_owner_status_route(http_console):
    from compute_routing import ComputeRouter,ComputeAdapter
    h=http_console
    router=ComputeRouter(h.env.root/'compute-routing.sqlite3',{'local':ComputeAdapter(lambda *a:{'eligible':True},lambda *a:{'state':'queued','downstream_id':'synthetic-task'})})
    rid=str(uuid.uuid4());router.submit('other@example.invalid',rid,{},'local')
    assert h.request('GET','/api/compute/jobs/'+rid,owner='bob@example.test')[0]==404
    assert h.request('GET','/api/compute/jobs/'+rid,owner='owner@example.invalid')[0]==404
