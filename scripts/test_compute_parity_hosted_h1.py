"""Regression: RunningHub's real catalog exceeds the old 16 MiB ceiling."""
import io
import json
from pathlib import Path
import socket
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'webui')]
from compute_parity.transport import Transport
from compute_parity.contracts import ParityError

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)

class FakeOpener:
    def __init__(self,raw):self.raw=raw;self.calls=0
    def open(self,request,timeout):
        self.calls+=1
        return io.BytesIO(self.raw)

def transport(lane,raw):
    t=Transport(lane,'fake-owner-key',enabled=True)
    t._opener=FakeOpener(raw)
    return t

def test_large_native_catalog_parses_without_retry():
    raw=json.dumps({'Loader':{'input':{'required':{'model':[['x.safetensors']]}}},'padding':'x'*(17*1024*1024)}).encode()
    t=transport('runninghub',raw)
    assert t.request('GET','/object_info')['Loader']['input']['required']['model']==[['x.safetensors']]
    assert t._opener.calls==1

@pytest.mark.parametrize('lane,method,path,billing',[
    ('runninghub','GET','/history',False),
    ('runninghub','POST','/object_info',False),
    ('runninghub','GET','/object_info',True),
    ('comfy_cloud','GET','/api/object_info',False),
])
def test_other_json_endpoints_keep_original_ceiling(lane,method,path,billing):
    t=transport(lane,b' '*(16*1024*1024)+b'{}')
    with pytest.raises(ParityError) as error:t.request(method,path,billing=billing)
    assert error.value.code=='provider_io'
    assert 'fake-owner-key' not in str(error.value)
    assert t._opener.calls==1

def test_native_catalog_still_bounded():
    t=transport('runninghub',b' '*(64*1024*1024)+b'{}')
    with pytest.raises(ParityError) as error:t.request('GET','/object_info')
    assert error.value.code=='provider_io'
    assert t._opener.calls==1

def test_malformed_catalog_keeps_safe_failure():
    t=transport('runninghub',b'<html>fake-owner-key</html>')
    with pytest.raises(ParityError) as error:t.request('GET','/object_info')
    assert error.value.code=='provider_io'
    assert 'fake-owner-key' not in str(error.value)
