"""Credential-contained HTTP transport. Off by default; no provider calls on import."""
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .contracts import ParityError, fail, safe_relative

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class Transport:
    def __init__(self, lane, key, *, enabled=False):
        self.lane=lane; self._key=key; self.enabled=enabled
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_\-.]{8,512}',key):fail('key_invalid','金鑰格式不正確')
        self.fingerprint=hashlib.sha256((lane+'\0'+key).encode()).hexdigest()
        self._opener=urllib.request.build_opener(NoRedirect())
    @classmethod
    def for_owner(cls, console, owner, lane, *, enabled=False):
        from .owner_credentials import reference_for
        reference=reference_for(console,owner,lane)
        transport=cls(lane,reference.resolve().reveal(),enabled=enabled)
        transport.owner=owner
        if lane=='runninghub':
            with console.prefs.db() as db:transport.account_type=console._raw(lane,db).get('account_type')
        return transport
    def __repr__(self):return f'Transport(lane={self.lane!r}, key=<redacted>)'
    def scrub(self, value):
        text=str(value).replace(self._key,'[redacted]').replace(urllib.parse.quote(self._key,safe=''),'[redacted]')
        return re.sub(r'(https?://[^\s/]+/proxy(?:-plus)?/)[^/\s\"\']+',r'\1[redacted]',text)
    def request(self, method, path, *, body=None, fields=None, files=None, binary=False, billing=False, headers=None):
        if not self.enabled:fail('build_only','此版本僅建置；尚未開放供應商呼叫')
        if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or re.search(r'[\x00-\x20\\]',path):fail('path_invalid','無效服務路徑')
        roots={'autoip':'https://autoip.app','comfy_cloud':'https://cloud.comfy.org',
               'runninghub':'https://www.runninghub.ai'+('' if billing else '/proxy-plus/'+self._key)}
        headers={'Accept':'application/json','User-Agent':'castle-provider-fabric/1',**(headers or {})}
        if self.lane=='comfy_cloud' and not path.startswith('/api/v2/'):
            headers['X-API-Key']=self._key
        elif self.lane!='runninghub' or billing:headers['Authorization']='Bearer '+self._key
        if fields is not None or files:
            boundary='castle'+uuid.uuid4().hex;chunks=[]
            for name,value in (fields or {}).items():
                if not re.fullmatch('[a-zA-Z0-9_]+',name):fail('field_invalid','上傳欄位不正確')
                chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),str(value).encode(),b'\r\n']
            for name,(filename,raw) in (files or {}).items():
                if not re.fullmatch('[a-zA-Z0-9_]+',name) or not re.fullmatch('[a-zA-Z0-9_.-]+',filename):fail('file_invalid','上傳檔名不正確')
                chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),raw,b'\r\n']
            chunks.append(f'--{boundary}--\r\n'.encode());data=b''.join(chunks)
            headers['Content-Type']='multipart/form-data; boundary='+boundary
        else:
            data=json.dumps(body).encode() if body is not None else None
            if data is not None:headers['Content-Type']='application/json'
        # urllib never logs URLs. No raw provider body, chained exception or request object escapes.
        # Native object_info includes the provider's complete node/model catalog.
        # Its measured JSON exceeds 16 MiB. Keep the larger allowance narrowly
        # scoped to this read-only endpoint; errors, jobs and uploads stay bounded.
        limit=512*1024*1024 if binary else 16*1024*1024
        if self.lane=='runninghub' and method=='GET' and path=='/object_info' and not billing and not binary:
            limit=64*1024*1024
        from .diagnostics import attempt_diagnostics,prompt_identity,transport_failure
        stage='upload' if path.startswith('/upload/') else 'prompt_transport'
        # Public docs promise Comfy compatibility, not a provisioning SLA. Bound the
        # single prompt request at five minutes; never retry an ambiguous POST.
        timeout=300 if self.lane=='runninghub' and method=='POST' and path=='/prompt' and not billing else 60
        self.last_diagnostics=attempt_diagnostics(stage)
        status=None;safe=None
        try:
            req=urllib.request.Request(roots[self.lane]+path,data=data,method=method,headers=headers)
            with self._opener.open(req,timeout=timeout) as response:
                status=getattr(response,'status',None)
                raw=response.read(limit+1)
            if len(raw)>limit:
                safe=ParityError('provider_io','供應商回應超過大小上限；保留原請求')
                safe.diagnostics=attempt_diagnostics(stage,status=status,error='response_size')
                raise safe
            result=raw if binary else json.loads(raw)
            self.last_diagnostics=attempt_diagnostics('upload' if stage=='upload' else 'prompt_response',result,status=status)
            return result
        except urllib.error.HTTPError as error:
            try:
                raw=error.read(65537)
                body=json.loads(raw) if len(raw)<=65536 else {}
            except Exception:body={}
            status=error.code;error.close()
            safe=ParityError('provider_http','供應商 HTTP 回應未確認；保留原請求')
            safe.diagnostics=attempt_diagnostics(stage,body,status=status,error='http')
            rid=prompt_identity(body)
            if rid:safe.prompt_id=rid
            if isinstance(body,dict):
                value=body.get('id')
                if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',value):safe.id=value
        except ParityError:raise
        except Exception as error:
            safe=ParityError('provider_io','供應商連線未確認；請查詢原任務，勿重送')
            safe.diagnostics=attempt_diagnostics(stage,status=status,error=transport_failure(error))
        if safe is not None:raise safe

    def download_runninghub_output(self,artifact):
        """Native /view redirects stay in memory. No auth header or URL diagnostics."""
        if not self.enabled:fail('build_only','此版本僅建置；尚未開放供應商呼叫')
        if self.lane!='runninghub' or not isinstance(artifact,dict):fail('output_invalid','平台成果路徑不正確')
        name=artifact.get('filename');sub=artifact.get('subfolder','')
        checked=sub[:-1] if isinstance(sub,str) and sub.endswith('/') else sub
        if not safe_relative(name) or not isinstance(sub,str) or (sub and not safe_relative(checked)) or artifact.get('type')!='output':
            fail('output_invalid','平台成果路徑不正確')
        url='https://www.runninghub.ai/proxy-plus/'+self._key+'/view?'+urllib.parse.urlencode({'filename':name,'subfolder':sub,'type':'output'})
        allowed={'www.runninghub.ai','rh-hk-images.xiaoyaoyou.com','rh-images.xiaoyaoyou.com'}
        limit=512*1024*1024
        for hop in range(4):
            valid=False
            try:
                parsed=urllib.parse.urlsplit(url)
                valid=(parsed.scheme=='https' and parsed.hostname in allowed and parsed.port in (None,443)
                       and not parsed.username and not parsed.password and not parsed.fragment
                       and not re.search(r'[\x00-\x20\\]',url))
            except (TypeError,ValueError):pass
            if not valid:fail('output_url_invalid','RunningHub 成果轉址來源未通過檢查')
            response=None;next_url=None
            try:
                response=self._opener.open(urllib.request.Request(url,headers={'Accept':'application/octet-stream'}),timeout=60)
            except urllib.error.HTTPError as error:
                location=error.headers.get('Location') if error.headers else None
                if error.code in {301,302,303,307,308} and location and not re.search(r'[\x00-\x20\\]',location) and hop<3:
                    try:next_url=urllib.parse.urljoin(url,location)
                    except ValueError:pass
                error.close()
            except Exception:pass
            if next_url is not None:url=next_url;continue
            if response is None:fail('output_download_failed','RunningHub 成果下載未完成或轉址超過三次')
            raw=None
            try:
                with response:
                    declared=response.headers.get('Content-Length')
                    if getattr(response,'status',200)!=200 or not isinstance(declared,str) or not re.fullmatch(r'[0-9]+',declared) or not 0<int(declared)<=limit:raise ValueError()
                    raw=response.read(limit+1)
                    if not raw or len(raw)>limit or int(declared)!=len(raw):raw=None
            except Exception:raw=None
            if raw is None:fail('output_size_mismatch','RunningHub 成果大小或長度不符')
            return raw
        fail('output_download_failed','RunningHub 成果轉址超過三次')

    def download_output(self,url,*,expected_size=None):
        """Bounded Cloud content redirects. Credentials are permanently dropped on origin change."""
        if not self.enabled:fail('build_only','此版本僅建置；尚未開放供應商呼叫')
        if url.startswith('/') and not url.startswith('//'):url='https://cloud.comfy.org'+url
        allowed={'cloud.comfy.org','cdn.comfy.org','comfy-hub-assets.comfy.org','storage.googleapis.com'}
        original=urllib.parse.urlsplit(url).hostname
        auth=original=='cloud.comfy.org'
        for hop in range(4):
            try:parsed=urllib.parse.urlsplit(url);port=parsed.port
            except ValueError:fail('output_url_invalid','成果來源不在核准的 Cloud 儲存服務')
            if parsed.scheme!='https' or parsed.hostname not in allowed or parsed.username or parsed.password or port not in (None,443) or parsed.fragment or re.search(r'[\x00-\x20\\]',url):
                fail('output_url_invalid','成果來源不在核准的 Cloud 儲存服務')
            if parsed.hostname=='cloud.comfy.org' and not re.fullmatch(r'/api/v2/assets/[a-f0-9-]{36}/content',parsed.path):
                fail('output_url_invalid','Cloud 成果路徑不正確')
            if parsed.hostname!=original:auth=False
            headers={'Accept':'application/octet-stream'}
            if auth:headers['Authorization']='Bearer '+self._key
            try:
                response=self._opener.open(urllib.request.Request(url,headers=headers),timeout=60)
            except urllib.error.HTTPError as error:
                location=error.headers.get('Location')
                status=error.code;error.close()
                if status in {301,302,303,307,308} and location and hop<3:
                    url=urllib.parse.urljoin(url,location);continue
                fail('output_download_failed','Cloud 成果下載未完成')
            except Exception:fail('output_download_failed','Cloud 成果下載未完成')
            try:
                with response:
                    declared=response.headers.get('Content-Length')
                    raw=response.read(512*1024*1024+1)
                if not raw or len(raw)>512*1024*1024:raise ValueError()
                if declared is not None and int(declared)!=len(raw):raise ValueError()
                if expected_size is not None and len(raw)!=expected_size:raise ValueError()
            except Exception:fail('output_size_mismatch','Cloud 成果大小不符')
            return raw
        fail('output_download_failed','Cloud 成果轉址次數超過上限')
