"""Optional, durable, owner-scoped Colab jobs. Google credentials stay in the bridge.

This lane never changes COMFYUI_URL or retries an ambiguous allocation/submission.
The database is the source of truth; a process lock elects one reconciler after a
restart. Compute-unit limits are conservative account-delta stop thresholds, not
a provider-enforced billing cap (Google does not expose one).
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import uuid

logger = logging.getLogger(__name__)
_unreadable_job_ids = set()
_unreadable_job_ids_lock = threading.Lock()

def _safe_warning(message, *args):
    try:
        logger.warning(message, *args)
    except Exception:
        pass

try:
    import file_lock
except ImportError:
    try:
        from . import file_lock
    except ImportError:
        import fcntl as file_lock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TERMINAL = {"completed", "failed", "cancelled"}
WORKER_MAX_UNSUPERVISED = 3
WORKER_RESTART_HOLD_SECONDS = 900
GPU_TYPES = {"L4", "G4", "A100", "H100"}
MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
STATES = {
    "allocation_retry_wait": "A100 暫不可用，五分鐘後再試一次", "queued": "等待啟動", "allocating": "正在申請 GPU", "allocation_uncertain": "確認 GPU 申請結果",
    "preparing": "準備雲端環境", "launching": "正在啟動", "launch_uncertain": "確認啟動結果",
    "running": "雲端執行中", "downloading": "下載成果", "cleanup_pending": "等待確認 GPU 已釋放",
    "completed": "完成，GPU 已釋放", "failed": "失敗，GPU 已釋放", "cancelled": "已取消，GPU 已釋放",
}


class ColabError(ValueError):
    def __init__(self,message,code=None):
        from colab_inputs import ADMISSION_MESSAGES
        self.code=code or getattr(self,'code',None) or next((k for k,v in ADMISSION_MESSAGES.items() if v==message),'admission_unavailable')
        super().__init__(message)


class BridgeError(ColabError):
    def __init__(self, code="bridge_unavailable", ambiguous=False, diagnostics=None, private_diagnostics=None):
        self.code, self.ambiguous = code, ambiguous
        self.private_diagnostics=private_diagnostics or {}
        from scripts.colab_allocation_diagnostics import sanitize
        from scripts.colab_upload_diagnostics import sanitize as upload_safe, CODES
        self.diagnostics = sanitize(diagnostics)
        from scripts.colab_transport_diagnostics import sanitize as transport_safe
        if code in {'transport_not_ready','execution_uncertain','timeout','operation_timeout','bridge_unavailable','transport_error','transport_failed'}:
            self.diagnostics.update(transport_safe(diagnostics))
        if code in CODES or code in {'timeout','operation_timeout'}:
            self.diagnostics.update(upload_safe(diagnostics))
        super().__init__({
            "not_installed": "請先安裝 Colab 選用環境並設定 WEB_COMFYUI_COLAB_PYTHON。",
            "authentication_required": "請先連接這個 Google 帳號。",
            "account_identity_mismatch": "請登入原本綁定的 Google 帳號；其他帳號請新增一份連線設定。",
            "legacy_identity_unverified": "舊連線尚未確認 Google 身分，請先在 Google 確認執行階段已結束。",
            "timeout": "Google 連線逾時；會先確認既有任務，不會重複申請 GPU。",
        }.get(code, "Colab 連線未完成，請重新整理狀態；既有任務會保留。"))


def canonical_uuid(value):
    try:
        result = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ColabError("無效的任務或帳號識別碼") from error
    if result != value:
        raise ColabError("無效的任務或帳號識別碼")
    return result


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private_write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_media(path, timeout=30):
    """Decode images; probe audio/video streams without loading user libraries."""
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        from PIL import Image
        try:
            with Image.open(path) as picture:
                picture.verify()
            with Image.open(path) as picture:
                picture.load()
                return {"kind": "image", "width": picture.width, "height": picture.height}
        except Exception as error:
            raise ColabError("下載成果不是有效的圖片") from error
    probe = shutil.which("ffprobe")
    if not probe:
        raise ColabError("下載影音成果需要本機安裝 ffmpeg／ffprobe")
    result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)], capture_output=True, timeout=timeout)
    try:
        if result.returncode or len(result.stdout) > 128 * 1024:
            raise ValueError()
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        kind = "video" if path.suffix.lower() in {".mp4", ".mov", ".webm"} else "audio"
        if not math.isfinite(duration) or duration <= 0 or not any(stream.get("codec_type") == kind for stream in info["streams"]):
            raise ValueError()
        return {"kind": kind, "duration": duration}
    except (ValueError, KeyError, TypeError) as error:
        raise ColabError("下載成果沒有有效的影音串流") from error


class Bridge:
    def __init__(self, python=""):
        self.python = python

    @property
    def installed(self):
        return bool(self.python and Path(self.python).is_absolute() and Path(self.python).is_file())

    def __call__(self, op, account_dir, **values):
        if not self.installed:
            raise BridgeError("not_installed")
        rpc_budget=values.pop("_timeout_seconds",None)
        request = {"op": op, "account_dir": str(account_dir), **values}
        # No credential discovery from ambient application environments.
        env = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT", "SSL_CERT_FILE") if key in os.environ}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        try:
            response = subprocess.run(
                [self.python, str(ROOT / "scripts/colab_bridge.py")],
                input=encoded(request), capture_output=True, text=True, env=env,
                timeout=min(rpc_budget,150 if op in {"upload","download"} else 75) if type(rpc_budget) in (int,float) and rpc_budget>0 else 150 if op in {"upload", "download"} else 75,
            )
            if len(response.stdout) > 2 * 1024 * 1024:
                raise BridgeError()
            payload = json.loads(response.stdout)
            if response.returncode or not payload.get("ok"):
                error = payload.get("error") or {}
                raise BridgeError(str(error.get("code", "bridge_unavailable")), bool(error.get("ambiguous")), error.get("diagnostics"), error.get("private_diagnostics"))
            return payload["result"]
        except subprocess.TimeoutExpired as error:
            from scripts.colab_transport_diagnostics import capture
            from scripts.colab_private_diagnostics import capture as private_capture
            raise BridgeError("timeout", op in {"runtime_allocate", "remote_exec", "runtime_stop"}, capture(error), private_capture(error,op)) from None
        except (OSError, KeyError, ValueError) as error:
            if isinstance(error, BridgeError):
                raise
            from scripts.colab_private_diagnostics import capture
            raise BridgeError(private_diagnostics=capture(error,op)) from None


class ColabService:
    def __init__(self, root, bridge=None, clock=time.time):
        self.root = Path(root).expanduser()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.bridge = bridge or Bridge(os.environ.get("WEB_COMFYUI_COLAB_PYTHON", os.environ.get("CASTLE_COLAB_PYTHON", "")))
        self.clock = clock
        self._thread = None
        self._start_lock = threading.Lock()
        self._yield_marker_unreadable_logged = False
        self._held_yields_logged = set()
        self._db_local = threading.local()
        self._db_strict = os.environ.get('CASTLE_COLAB_DB_NESTING') == 'strict'
        self.nested_db_events = 0
        self.nested_db_sites = set()
        self.worker_iteration_failures = 0
        self.unreadable_job_rows = 0
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, label TEXT NOT NULL, snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, account_id TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS request_claims (id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','admitted','terminal')), outcome TEXT)")
            # SQL enforcement also covers an old submitter that resumes after a
            # new controller closes its request. Never depend on Python locks.
            db.execute("""CREATE TRIGGER IF NOT EXISTS claim_before_job BEFORE INSERT ON jobs BEGIN
                SELECT CASE WHEN EXISTS(SELECT 1 FROM request_claims WHERE id=NEW.id AND (owner!=NEW.owner OR state!='pending')) THEN RAISE(ABORT,'request_claim_closed') END;
                INSERT OR IGNORE INTO request_claims(id,owner,state) VALUES(NEW.id,NEW.owner,'pending');
            END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS claim_after_job AFTER INSERT ON jobs BEGIN
                UPDATE request_claims SET state='admitted' WHERE id=NEW.id;
            END""")
        os.chmod(self.root / "state.sqlite3", 0o600)
        from colab_sessions import WorkSessions
        self.sessions = WorkSessions(self)

    @contextmanager
    def db(self):
        local = self._db_local
        depth = getattr(local, 'depth', 0)
        if depth and getattr(local, 'nested_ok_depth', None) != depth:
            frame = sys._getframe(1)
            while frame and (frame.f_code.co_filename.endswith('contextlib.py') or
                             (frame.f_code.co_filename == __file__ and frame.f_code.co_name == 'db')):
                frame = frame.f_back
            site = (Path(frame.f_code.co_filename).name, frame.f_lineno) if frame else ('unknown', 0)
            self.nested_db_events += 1
            if site not in self.nested_db_sites and len(self.nested_db_sites) < 20:
                self.nested_db_sites.add(site)
                _safe_warning('colab_db_nested site=%s:%s', *site)
            if self._db_strict:
                raise AssertionError('colab_db_nested %s:%s' % site)
        connection = sqlite3.connect(self.root / "state.sqlite3", timeout=15)
        connection.row_factory = sqlite3.Row
        local.depth = getattr(local, 'depth', 0) + 1
        try:
            with connection:
                yield connection
        finally:
            try:
                connection.close()
            finally:
                local.depth -= 1

    @contextmanager
    def db_nested_ok(self):
        local = self._db_local
        previous = getattr(local, 'nested_ok_depth', None)
        local.nested_ok_depth = getattr(local, 'depth', 0)
        try:
            yield
        finally:
            local.nested_ok_depth = previous

    def claim_request(self, owner, rid):
        canonical_uuid(rid)
        with self.db() as db:
            parity=getattr(self,'parity_journal_path',self.root.parent/'compute-parity'/'journal.sqlite3')
            if parity.exists():db.execute('ATTACH DATABASE ? AS parity',(str(parity),))
            db.execute('BEGIN IMMEDIATE')
            if parity.exists():
                # Serialize claim ownership with the legacy journal. An existing
                # orphan belongs to its original owner, even without a job row.
                legacy=db.execute('SELECT owner,body FROM parity.jobs WHERE request=?',(rid,)).fetchall()
                for entry in legacy:
                    body=json.loads(entry['body'])
                    if body['lane']=='colab' and entry['owner']!=owner:raise ColabError('原請求不可重用')
                    if body['lane']=='colab':
                        from compute_parity.journal import TERMINAL as PARITY_TERMINAL
                        if body['state'] in PARITY_TERMINAL:raise ColabError('原請求已結案或不可重用；請建立新請求')
            existing=db.execute('SELECT owner FROM jobs WHERE id=?',(rid,)).fetchone()
            if existing and existing['owner']!=owner:raise ColabError('原請求不可重用')
            row=db.execute('SELECT owner,state FROM request_claims WHERE id=?',(rid,)).fetchone()
            if row and (row['owner']!=owner or row['state']=='terminal' or
                    row['state']=='admitted' and not db.execute('SELECT 1 FROM jobs WHERE id=? AND owner=?',(rid,owner)).fetchone()):
                raise ColabError('原請求已結案或不可重用；請建立新請求')
            db.execute('INSERT OR IGNORE INTO request_claims(id,owner,state) VALUES(?,?,?)',(rid,owner,'admitted' if existing else 'pending'))

    def close_admission(self, journal, owner, rid, **changes):
        """Tombstone and zero-execution receipt commit together, or neither does.

        Both durable stores use rollback journals on the same filesystem. SQLite's
        super-journal makes the attached transaction atomic even on process loss.
        A nonstandard journal mode fails closed, retaining the reservation.
        """
        canonical_uuid(rid)
        with self.db() as db:
            db.execute('ATTACH DATABASE ? AS parity',(str(journal.path),))
            if any(db.execute('PRAGMA '+name+'.journal_mode').fetchone()[0] not in {'delete','truncate','persist'} for name in ('main','parity')):
                raise ColabError('暫時無法確認原請求狀態')
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT owner,state FROM jobs WHERE id=?',(rid,)).fetchone()
            if row:
                if row['owner']!=owner:raise ColabError('原請求不可重用')
                return None,json.loads(row['state'])
            claim=db.execute('SELECT owner,state FROM request_claims WHERE id=?',(rid,)).fetchone()
            if claim and (claim['owner']!=owner or claim['state']=='admitted'):
                raise ColabError('暫時無法確認原請求狀態')
            job=db.execute('SELECT body FROM parity.jobs WHERE owner=? AND request=?',(owner,rid)).fetchone()
            if not job or json.loads(job[0])['lane']!='colab':raise ColabError('找不到原請求')
            if claim and claim['state']=='terminal':return json.loads(job[0]),None
            db.execute("INSERT INTO request_claims(id,owner,state,outcome) VALUES(?,?,'terminal',?) ON CONFLICT(id) DO UPDATE SET state='terminal',outcome=excluded.outcome",(rid,owner,changes['state']))
            return journal.update_in_transaction(db,owner,rid,table='parity.jobs',**changes),None

    def account(self, owner, account_id):
        canonical_uuid(account_id)
        with self.db() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=? AND owner=?", (account_id, owner)).fetchone()
        if not row:
            raise ColabError("找不到這個帳號")
        return dict(row)

    def account_dir(self, account_id):
        path = self.root / "accounts" / canonical_uuid(account_id)
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        return path

    def create_account(self, owner, label):
        label = str(label).strip()
        if not label or len(label) > 64:
            raise ColabError("請填寫 1–64 字的帳號名稱")
        account_id = str(uuid.uuid4())
        with self.db() as db:
            if db.execute("SELECT count(*) FROM accounts WHERE owner=?", (owner,)).fetchone()[0] >= 10:
                raise ColabError("每位使用者最多建立 10 個帳號設定")
            db.execute("INSERT INTO accounts VALUES (?,?,?,?)", (account_id, owner, label, "{}"))
        return {"id": account_id, "label": label}

    def account_action(self, owner, account_id, action, values):
        self.account(owner, account_id)
        if action in {"login", "finish"}:
            with self.account_guard(account_id):
                return self._account_action(owner, account_id, action, values)
        return self._account_action(owner, account_id, action, values)

    @contextmanager
    def native_submission_guard(self,owner,request_id):
        # Nonblocking cross-process lock covers reserve through INSERT/start.
        # Recovery never interprets a row absence while submit holds this lock.
        canonical_uuid(request_id)
        directory=self.root/'submission-locks';directory.mkdir(mode=0o700,exist_ok=True)
        with (directory/(digest((owner+'\0'+request_id).encode())+'.lock')).open('a') as lock:
            file_lock.flock(lock.fileno(),file_lock.LOCK_EX|file_lock.LOCK_NB)
            yield

    def find_job(self,owner,request_id):
        with self.db() as db:
            row=db.execute('SELECT state FROM jobs WHERE owner=? AND id=?',(owner,request_id)).fetchone()
        return json.loads(row[0]) if row else None

    @contextmanager
    def account_guard(self, account_id, blocking=True):
        with (self.account_dir(account_id) / "controller.lock").open("a") as lock:
            file_lock.flock(lock.fileno(), file_lock.LOCK_EX | (0 if blocking else file_lock.LOCK_NB))
            yield

    def _account_action(self, owner, account_id, action, values):
        self.account(owner, account_id)
        if action in {"login", "finish"}:
            with self.db() as db:
                states = [json.loads(row[0]) for row in db.execute("SELECT state FROM jobs WHERE account_id=?", (account_id,))]
            if any(state["phase"] not in TERMINAL for state in states) or any(s['account_id']==account_id and s['phase']!='closed' for s in self.sessions.rows()):
                identity = self.bridge("account_identity", self.account_dir(account_id))
                if not identity.get("bound"):
                    raise ColabError("舊連線仍有待回收的 GPU，且尚無已驗證身分；請先在 Google 確認執行階段已結束")
        path = self.account_dir(account_id)
        if action == "login":
            return self.bridge("oauth_begin", path)
        if action == "finish":
            result = self.bridge("oauth_finish", path, flow_id=str(values.get("flow_id", "")), code=str(values.get("code", "")))
            return {"authenticated": bool(result.get("authenticated"))}
        if action != "refresh":
            raise ColabError("不支援的帳號操作")
        result = self.bridge("account_status", path, **({"_timeout_seconds":values["_timeout_seconds"]} if "_timeout_seconds" in values else {}))
        snapshot = {key: result.get(key) for key in ("authenticated", "balance_compute_units", "consumption_rate_per_hour", "active_assignments")}
        snapshot["checked_at"] = self.clock()
        with self.db() as db:
            db.execute("UPDATE accounts SET snapshot=? WHERE id=?", (encoded(snapshot), account_id))
        return snapshot

    def admission_account(self,owner,account_id,deadline=None):
        """Two read-only bridge calls share one 8-second deadline. No RPC retry."""
        deadline=deadline if deadline is not None else time.monotonic()+8
        def remaining():
            value=deadline-time.monotonic()
            if value<=0:raise BridgeError('account_busy')
            return value
        try:
            status=self.account_action(owner,account_id,'refresh',{'_timeout_seconds':remaining()})
            identity=self.bridge('account_identity',self.account_dir(account_id),_timeout_seconds=remaining())
            remaining()
            identity={**identity,'verified_at':self.clock()}
            return status,identity
        except BridgeError as error:
            if error.code!='account_busy':raise
            snapshot=self.sessions.admission_snapshot(owner,account_id)
            if snapshot is None:raise
            return {'authenticated':True,'balance_compute_units':snapshot['balance'],'checked_at':snapshot['balance_at']},{'bound':True,'subject_key':snapshot['subject_key'],'verified_at':snapshot['identity_at']}

    def snapshot(self, owner):
        with self.db() as db:
            account_rows = db.execute("SELECT * FROM accounts WHERE owner=? ORDER BY rowid", (owner,)).fetchall()
            job_rows = db.execute("SELECT state FROM jobs WHERE owner=? ORDER BY rowid DESC LIMIT 100", (owner,)).fetchall()
        accounts = [{"id": r["id"], "label": r["label"], **json.loads(r["snapshot"])} for r in account_rows]
        sessions = {s['id']: s for s in self.sessions.rows(owner)}
        jobs = [self.public_job(json.loads(r["state"]), owner=owner, sessions=sessions) for r in job_rows]
        return {"installed": self.bridge.installed, "accounts": accounts, "jobs": jobs, **self.sessions.snapshot(owner)}

    def job(self, owner, job_id):
        canonical_uuid(job_id)
        with self.db() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if not row:
            raise ColabError("找不到這個任務")
        return json.loads(row["state"])

    def owner_session(self,owner,session_id):
        if not owner or not session_id:return {}
        with self.db() as db:
            row=db.execute("SELECT state FROM work_sessions WHERE id=? AND owner=?",(session_id,owner)).fetchone()
        return json.loads(row[0]) if row else {}

    def project_session_gpu(self,owner,job,sessions=None):
        from scripts.colab_session_diagnostics import telemetry
        session=(sessions.get(job.get('session_id')) or {}) if isinstance(sessions,dict) else self.owner_session(owner,job.get('session_id'))
        requested=job.get('requested_gpu') or ('A100' if job.get('native_parity') else job.get('gpu'))
        assigned=session.get('assigned_gpu')
        if 'assigned_gpu' not in session and session.get('runtime_id') and session.get('gpu') in {'A100','H100','G4'}:assigned=session['gpu']
        if assigned not in {'A100','H100','G4'}:assigned=None
        return {**job,'requested_gpu':requested,'assigned_gpu':assigned,'gpu':assigned,
                'gpu_name':telemetry(session).get('gpu_name') if assigned else None}

    def public_job(self,state,owner=None,sessions=None):
        if state.get('session_id'):
            if owner is None:
                with self.db() as db:
                    row=db.execute('SELECT owner FROM jobs WHERE id=?',(state['id'],)).fetchone()
                owner=row[0] if row else None
            state=self.project_session_gpu(owner,state,sessions=sessions)
        fields = ("id", "account_id", "phase", "created_at", "updated_at", "deadline_epoch", "gpu", "max_units", "max_minutes", "units_observed", "remote_phase", "message", "artifacts", "cancel_requested", "runtime_id", "released")
        result = {key: state.get(key) for key in fields}
        from scripts.colab_session_diagnostics import sanitize_diagnostics
        result['diagnostics'] = sanitize_diagnostics(state.get('remote_diagnostics'))
        from scripts.colab_allocation_diagnostics import sanitize
        result['allocation_diagnostics'] = sanitize(state.get('allocation_diagnostics'))
        result['allocation_code'] = state.get('allocation_code') if state.get('allocation_code') in {'allocation_unavailable','allocation_rejected','allocation_uncertain'} else None
        result['allocation_retry_at'] = state.get('allocation_retry_at')
        result['allocation_attempts'] = state.get('allocation_attempts',0)
        result.update({k:state.get(k) for k in ('session_id','gpu_name','requested_gpu','assigned_gpu','session_runtime_retained')})
        result['requested_gpu']=result.get('requested_gpu') or state.get('gpu')
        result["status_label"] = ('完成，工作階段保留' if state.get('session_runtime_retained') else STATES.get(state["phase"], state["phase"]))
        if state.get('session_id') and state['phase'] in TERMINAL and not state.get('session_runtime_retained'):
            result['status_label']={'completed':'工作已完成','failed':'工作失敗','cancelled':'工作已取消'}[state['phase']]
        return result

    def update(self, job_id, **changes):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            if state.get('runtime_id') and 'runtime_id' in changes and changes['runtime_id']!=state['runtime_id']:
                raise ColabError('不可清除或改綁已配置的 runtime')
            changes['runtime_ever_assigned']=bool(state.get('runtime_ever_assigned') or state.get('runtime_id') or changes.get('runtime_id'))
            state.update(changes, updated_at=self.clock())
            db.execute("UPDATE jobs SET state=? WHERE id=?", (encoded(state), job_id))
        return state

    def submit(self, owner, body):
        self.account(owner, canonical_uuid(body.get("account_id")))
        self.claim_request(owner,body["request_id"])
        with self.account_guard(body["account_id"]):
            return self._submit(owner, body)

    def _submit(self, owner, body, *, _native=None):
        admission_deadline=time.monotonic()+8
        self.account(owner,canonical_uuid(body.get("account_id")))
        self.claim_request(owner,body["request_id"])
        from scripts.colab_remote.remote_job import validate_manifest

        if not self.bridge.installed:
            raise BridgeError("not_installed")
        job_id = canonical_uuid(body.get("request_id"))
        account_id = canonical_uuid(body.get("account_id"))
        self.account(owner, account_id)
        request_hash = digest(encoded(body).encode())
        with self.db() as db:
            existing = db.execute("SELECT owner,request_hash,state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if existing:
            if existing["owner"] != owner or existing["request_hash"] != request_hash:
                raise ColabError("同一請求識別碼不能用於不同內容")
            return self.public_job(json.loads(existing["state"]))
        gpu = str(body.get("gpu", "L4"))
        if gpu not in GPU_TYPES:
            raise ColabError("不支援這個 GPU 選項")
        try:
            max_units = None if _native is not None else float(body.get("max_units", 20))
            max_minutes = int(body.get("max_minutes", 60))
        except (ValueError, TypeError) as error:
            raise ColabError("點數及時間上限必須是數字") from error
        if (_native is None and (not math.isfinite(max_units) or not 5 <= max_units <= 2000)) or not 5 <= max_minutes <= 180:
            raise ColabError("點數上限須為 5–2000；執行時間須為 5–180 分鐘")
        spec = _native if _native is not None else body.get("job")
        if not isinstance(spec, dict):
            raise ColabError("缺少生成設定")
        # The browser submits choices, never filesystem paths or executable graphs.
        allowed = {"type", "prompt", "seconds", "resolution", "aspect", "mode", "seed", "intensity", "fast", "size"}
        if _native is None and set(spec) - allowed:
            raise ColabError("不支援的生成設定")
        spec = {**spec, "id": "output"}
        if spec.get("type") in {"video", "tts"} and not shutil.which("ffprobe"):
            raise ColabError("請先安裝 ffmpeg／ffprobe，才能驗證生成的影音成果")
        files = body.get("files", [])
        if not isinstance(files, list) or len(files) > 8:
            raise ColabError("最多提供 8 個參考檔案")
        raw_files, inputs, file_manifest = [], [], []
        total = 0
        for index, entry in enumerate(files):
            if not isinstance(entry, dict):
                raise ColabError("無效的參考檔案")
            suffix = Path(str(entry.get("name", ""))).suffix.lower()
            if _native is None and suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".wav", ".mp3", ".flac", ".m4a"}:
                raise ColabError("不支援的參考檔案格式")
            try:
                raw = base64.b64decode(entry.get("data", ""), validate=True)
            except (ValueError, TypeError) as error:
                raise ColabError("無效的參考檔案") from error
            total += len(raw)
            if not raw or total > MAX_INPUT:
                raise ColabError("參考檔案合計上限為 32 MB")
            name = f"input/{index + 1}{suffix}"
            if _native is not None:
                from colab_inputs import input_name
                try:item=_native['bundle']['inputs'][index]
                except (KeyError,IndexError):raise ColabError('無效的參考檔案',code='invalid_input') from None
                name=input_name(item['name'],raw,item['kind'])
            inputs.append(name)
            raw_files.append((name, raw))
            file_manifest.append({"path": name, "size": len(raw), "sha256": digest(raw)})
        if spec.get("type") == "tts":
            spec["voices"] = inputs
        elif spec.get("mode") == "extend":
            if len(inputs) != 1:
                raise ColabError("延長影片需要一段來源影片")
            spec["extend"], spec["mode"] = inputs[0], "ref"
        elif spec.get("mode") == "frames":
            if not 1 <= len(inputs) <= 2:
                raise ColabError("首尾幀模式需要 1–2 張圖片")
            spec["first"] = inputs[0]
            if len(inputs) == 2:
                spec["last"] = inputs[1]
        else:
            spec["inputs"] = inputs
        now = self.clock()
        manifest = {"schema_version": 1, "job_id": job_id, "deadline_epoch": now + max_minutes * 60, "stop_on_error": True, "jobs": [spec], "input_files": file_manifest}
        directory = self.root / "jobs" / job_id
        # Validate before consuming a GPU or accepting the request.
        try:
            if _native is not None:
                from scripts.colab_session_inputs import validate_native_manifest
                manifest = validate_native_manifest(manifest, directory, check_files=False)
            else:
                manifest = validate_manifest(manifest, directory, check_files=False)
        except ValueError as error:
            raise ColabError("生成設定或參考素材不符合這個模式；請檢查秒數、素材種類及描述中的參考編號") from error
        # Reject corrupt selected media before a paid GPU is requested. This
        # reads only the files explicitly submitted with this request.
        with tempfile.TemporaryDirectory(prefix="preflight-", dir=self.root) as temporary:
            for name, raw in raw_files:
                target = Path(temporary) / Path(name).name
                private_write(target, raw)
                if time.monotonic()>=admission_deadline:raise BridgeError("timeout")
                validate_media(target,timeout=max(.001,admission_deadline-time.monotonic()))
        status,identity = self.admission_account(owner,account_id,admission_deadline)
        subject_key = identity.get("subject_key")
        if not identity.get("bound") or not isinstance(subject_key, str) or len(subject_key) != 64 or any(c not in "0123456789abcdef" for c in subject_key):
            raise ColabError("請重新連接 Google 帳號以確認身分，再開始生成")
        balance = status.get("balance_compute_units")
        if not status.get("authenticated") or not isinstance(balance, (int, float)) or not math.isfinite(balance) or balance <= 0 or (max_units is not None and balance < max_units) or (_native is not None and balance < body.get("quote_amount",0)):
            raise ColabError("請先連接帳號並確認點數餘額足夠；點數上限不能高於目前餘額")
        state = {"session_job": _native is not None, "native_parity": _native is not None, "id": job_id, "account_id": account_id, "phase": "queued", "gpu": gpu, "requested_gpu": gpu, "max_units": max_units, "max_minutes": max_minutes,
                 "quote_amount": body.get("quote_amount",0), "created_at": now, "updated_at": now, "deadline_epoch": manifest["deadline_epoch"], "allocation_id": str(uuid.uuid4()),
                 "subject_key": subject_key, "output_kind": "audio" if spec["type"] == "tts" else spec["type"],
                 "baseline_units": balance, "last_balance": balance, "units_observed": 0, "artifacts": [], "released": False, "cancel_requested": False}
        if _native is not None:
            state['admission_verification']={'owner':owner,'account_id':account_id,'subject_key':subject_key,'identity_at':identity.get('verified_at'),'balance_at':status.get('checked_at'),'balance':balance}
            from scripts.colab_session_diagnostics import plan, plan_id
            state['bootstrap_plan_id']=plan_id(plan(_native['bundle']))
        staging = self.root / "jobs" / f".staging-{job_id}-{secrets.token_hex(8)}"
        published = False
        placed = False
        try:
            staging.mkdir(mode=0o700, parents=True)
            os.chmod(staging, 0o700)
            for name, raw in raw_files:
                private_write(staging / name, raw)
            manifest_bytes = encoded(manifest).encode()
            private_write(staging / "manifest.json", manifest_bytes)
            state["manifest_sha256"] = digest(manifest_bytes)
            # A deployment cannot change partially uploaded worker code. Freeze
            # source bytes and transfer order with the original immutable job.
            source = ROOT / "scripts/colab_remote"
            uploads = []
            for path in sorted(source.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts or not (path.suffix in {".py", ".json"} or path.name in {"LICENSE.upstream", "NOTICE.md"}):
                    continue
                if path.is_symlink():
                    raise ColabError("執行器來源不能是符號連結")
                relative = "runner/" + path.relative_to(source).as_posix()
                raw = path.read_bytes()
                private_write(staging / relative, raw)
                uploads.append({"path": relative, "size": len(raw), "sha256": digest(raw)})
            uploads.append({"path": "manifest.json", "size": len(manifest_bytes), "sha256": state["manifest_sha256"]})
            if _native is not None:
                from compute_parity.private_assets import freeze_private_models
                uploads.extend(freeze_private_models(_native["bundle"], staging))
            uploads.extend(file_manifest)
            state["uploads"] = uploads
            from scripts.colab_watchdog import build_watchdog_code
            watchdog = build_watchdog_code(job_id, state["deadline_epoch"], state["manifest_sha256"]).encode()
            private_write(staging / "watchdog.py", watchdog)
            state["watchdog_sha256"] = digest(watchdog)
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                duplicate = db.execute("SELECT owner,request_hash,state FROM jobs WHERE id=?", (job_id,)).fetchone()
                if duplicate:
                    if duplicate["owner"] != owner or duplicate["request_hash"] != request_hash:
                        raise ColabError("同一請求識別碼不能用於不同內容")
                    # The outer write transaction has finished its query; the nested reads are safe.
                    with self.db_nested_ok():
                        return self.public_job(json.loads(duplicate["state"]))
                claim=db.execute('SELECT owner,state FROM request_claims WHERE id=?',(job_id,)).fetchone()
                if not claim or claim['owner']!=owner or claim['state']!='pending':
                    raise ColabError('原請求已結案或不可重用；請建立新請求')
                active = [json.loads(r[0]) for r in db.execute("SELECT state FROM jobs")]
                if any(item["phase"] not in TERMINAL and not (_native is not None and item.get("session_job")) and (item["account_id"] == account_id or item.get("subject_key") == subject_key) for item in active):
                    raise ColabError("這個帳號已有執行中或待回收的任務，請先完成或釋放 GPU")
                sessions = [json.loads(r[0]) for r in db.execute('SELECT state FROM work_sessions')]
                if _native is not None and any(x['phase']!='closed' and (x['owner']==owner or x['subject_key']==subject_key) and
                        (x.get('close_requested') or x['owner']!=owner or x['account_id']!=account_id) for x in sessions):
                    raise ColabError('既有工作階段帳號不符或關閉中，請先確認釋放')
                if _native is None and any(x['phase']!='closed' and (x['account_id']==account_id or x['subject_key']==subject_key) for x in sessions):
                    raise ColabError('這個帳號已有工作階段；請先關閉並等候釋放')
                if directory.exists():
                    os.replace(directory, self.root / "jobs" / f".stale-{job_id}-{secrets.token_hex(8)}")
                os.replace(staging, directory)
                placed = True
                if _native is not None:self.sessions.bind_admission(db,owner,state)
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (job_id, owner, account_id, request_hash, encoded(state)))
            published = True
            self.start()
            return self.public_job(state)
        finally:
            if not published:
                if placed and directory.exists():
                    try:
                        os.replace(directory, self.root / "jobs" / f".stale-{job_id}-{secrets.token_hex(8)}")
                    except OSError:
                        pass
                shutil.rmtree(staging, ignore_errors=True)

    def cancel(self, owner, job_id):
        state = self.job(owner, job_id)
        if state["phase"] not in TERMINAL:
            state = self.update(job_id, cancel_requested=True, message="已要求停止；正在確認 GPU 釋放")
        self.start()
        return self.public_job(state)

    def artifact(self, owner, job_id, name):
        state = self.job(owner, job_id)
        entry = next((item for item in state.get("artifacts", []) if item["name"] == name), None)
        if not entry or Path(name).name != name:
            raise ColabError("找不到這個成果")
        path = self.root / "jobs" / job_id / "results" / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size"]:
            raise ColabError("成果檔案未通過驗證")
        return path

    def start(self):
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            if self._restart_held():
                return
            self._thread = threading.Thread(target=self._loop, name="web-comfyui-colab", daemon=True)
            self._thread.start()

    @staticmethod
    def _valid_sweeper_run_id(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None

    def _sweeper_run_id(self):
        try:
            marker = json.loads((self.root / 'sweeper-last-run.json').read_text())
        except (OSError, ValueError, RecursionError):
            return None
        value = marker.get('run_id') if isinstance(marker, dict) else None
        return value if self._valid_sweeper_run_id(value) else None

    def _restart_held(self):
        def unreadable():
            if not self._yield_marker_unreadable_logged:
                self._yield_marker_unreadable_logged = True
                _safe_warning('colab_worker_yield_marker_unreadable')

        def remove_marker():
            try:
                yield_path.unlink(missing_ok=True)
            except OSError:
                pass

        def finite_number(value):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            try:
                return math.isfinite(value)
            except OverflowError:
                return False

        yield_path = self.root / 'worker-yield.json'
        try:
            marker = json.loads(yield_path.read_text())
        except FileNotFoundError:
            return False
        except OSError:
            unreadable()
            remove_marker()
            return False
        except (ValueError, RecursionError):
            marker = None
        yielded_at = marker.get('yielded_at') if isinstance(marker, dict) else None
        sweep_run_id = marker.get('sweep_run_id') if isinstance(marker, dict) else None
        if (not isinstance(marker, dict) or 'sweep_run_id' not in marker or
                not finite_number(yielded_at) or
                (sweep_run_id is not None and not self._valid_sweeper_run_id(sweep_run_id))):
            unreadable()
            remove_marker()
            return False

        current = self._sweeper_run_id()
        if current is not None and current != sweep_run_id:
            remove_marker()
            return False
        now = self.clock()
        if now < yielded_at or now >= yielded_at + WORKER_RESTART_HOLD_SECONDS:
            remove_marker()
            return False
        held_yield = (yielded_at, sweep_run_id)
        if held_yield not in self._held_yields_logged:
            self._held_yields_logged.add(held_yield)
            _safe_warning('colab_worker_start_held')
        return True

    def _job_states(self):
        with self.db() as db:
            rows = db.execute("SELECT id, state FROM jobs").fetchall()
        states = []
        for row_id, raw in rows:
            try:
                state = json.loads(raw)
                if not (isinstance(state, dict) and isinstance(state.get('id'), str)
                        and isinstance(state.get('phase'), str)):
                    raise ValueError('invalid job state')
            except (TypeError, ValueError, RecursionError):
                self.unreadable_job_rows += 1
                key = (type(row_id).__name__, row_id)
                with _unreadable_job_ids_lock:
                    should_log = key not in _unreadable_job_ids and len(_unreadable_job_ids) < 20
                    if should_log:
                        _unreadable_job_ids.add(key)
                if should_log:
                    try:
                        safe_id = row_id if isinstance(row_id, str) and str(uuid.UUID(row_id)) == row_id else 'invalid'
                    except (ValueError, AttributeError):
                        safe_id = 'invalid'
                    _safe_warning('colab_job_row_unreadable id=%s', safe_id)
                continue
            states.append(state)
        return states

    def _loop(self):
        # A separate process can take over only after the former worker exits.
        with (self.root / "worker.lock").open("a") as lock:
            while True:
                try:
                    file_lock.flock(lock.fileno(), file_lock.LOCK_EX | file_lock.LOCK_NB)
                    break
                except BlockingIOError:
                    # The independent startup sweeper may own the same lock.
                    # Stay available to reconcile once it completes.
                    time.sleep(1)
            consecutive = 0
            while True:
                try:
                    states = self._job_states()
                except Exception as error:
                    states = None
                    self.worker_iteration_failures += 1
                    _safe_warning('colab_worker_iteration_failed stage=%s exception=%s', 'job_scan', type(error).__name__)
                try:
                    self.sessions.tick_all()
                    supervised = True
                except Exception as error:
                    supervised = False
                    self.worker_iteration_failures += 1
                    _safe_warning('colab_worker_iteration_failed stage=%s exception=%s', 'sessions', type(error).__name__)
                if supervised and states is not None:
                    for state in states:
                        if state['phase'] in TERMINAL:
                            continue
                        try:
                            self.tick(state)
                        except Exception as error:
                            self.worker_iteration_failures += 1
                            _safe_warning('colab_worker_iteration_failed stage=%s exception=%s', 'job_update', type(error).__name__)
                            try:
                                # Never place raw provider messages, prompts, tokens or paths in UI/logs.
                                self.update(state["id"], message="連線暫時中斷；保留任務識別碼，正在確認狀態")
                            except Exception as error:
                                self.worker_iteration_failures += 1
                                _safe_warning('colab_worker_iteration_failed stage=%s exception=%s', 'job_update', type(error).__name__)
                consecutive = 0 if supervised else consecutive + 1
                if consecutive >= WORKER_MAX_UNSUPERVISED:
                    _safe_warning('colab_worker_yield consecutive=%d', consecutive)
                    try:
                        sweep_run_id = self._sweeper_run_id()
                        private_write(self.root / 'worker-yield.json', encoded({'yielded_at': self.clock(), 'sweep_run_id': sweep_run_id}).encode())
                    except Exception as error:
                        _safe_warning('colab_worker_yield_marker_failed exception=%s', type(error).__name__)
                    return
                time.sleep(10)

    def _orphan_cleanup_call(self,state,op,values):
        # The missing-row exception grants cleanup only, using durable job
        # identity. A caller-supplied orphan flag never grants work RPCs.
        with self.db() as db:
            row=db.execute('SELECT state FROM jobs WHERE id=?',(state['id'],)).fetchone()
        job=json.loads(row[0]) if row else {}
        allowed=(job.get('orphan_session') and job.get('phase')=='cleanup_pending'
                 and job.get('session_id')==state.get('session_id')
                 and job.get('account_id')==state.get('account_id'))
        if op=='runtime_stop':allowed=allowed and bool(job.get('runtime_id')) and values.get('runtime_id')==job['runtime_id']
        elif op=='runtime_lookup':allowed=allowed and bool(job.get('allocation_id')) and values.get('allocation_id')==job['allocation_id'] and values.get('gpu')==job.get('gpu')
        elif op!='account_status':allowed=False
        if not allowed:raise BridgeError('runtime_missing')
        return self.bridge(op,self.account_dir(job['account_id']),**values)

    def _rpc_recovery_remaining(self,state,op):
        current=self.sessions.get(state.get('session_id') or state['id'])
        if current['phase'] in {'closing','stopping','released_unsettled','closed'} and op not in {'runtime_lookup','runtime_stop','account_status'}:
            code='clock_inconsistent' if (current.get('terminal_fault') or {}).get('code')=='clock_inconsistent' else 'runtime_missing'
            raise BridgeError(code)
        return self.sessions.recovery_remaining(current)

    def _call(self, state, op, **values):
        session_id=state.get('session_id') or (state['id'] if 'settings' in state else None)
        recovery_deadline=None
        monotonic_deadline=None
        if session_id:
            started=self.clock();started_mono=self.sessions._monotonic()
            try:remaining=self._rpc_recovery_remaining(state,op)
            except StopIteration:return self._orphan_cleanup_call(state,op,values)
            if remaining is not None:
                recovery_deadline=started+remaining
                monotonic_deadline=started_mono+remaining
                values['_timeout_seconds']=min(values.get('_timeout_seconds',150 if op in {'upload','download'} else 75),remaining)
                if op=='remote_exec':values['timeout_seconds']=max(1,int(min(values.get('timeout_seconds',45),remaining)))
        result=self.bridge(op, self.account_dir(state["account_id"]), **values)
        if session_id and isinstance(result,dict) and result.get('private_rpc'):
            self.sessions.save(session_id,private_rpc=result['private_rpc'])
        # A late success is not success. Do not publish upload/meter/progress
        # acknowledgements after the session's absolute recovery deadline.
        if recovery_deadline is not None and (self.clock()>=recovery_deadline or self.sessions._monotonic()>=monotonic_deadline):
            current=self.sessions.get(session_id)
            if current['phase'] not in {'closed','stopping','released_unsettled'}:
                self.sessions.save(session_id,phase='closing',reason='watchdog_state_lost',close_requested=True,
                                   terminal_fault=current.get('terminal_fault') or current.get('fault'),cycle_faulted=True)
            raise BridgeError('timeout',True,{'timeout_kind':'process','transport_phase':'wait'})
        if session_id:self._rpc_recovery_remaining(state,op)
        return result

    def _exec(self, state, code):
        result = self._call(state, "remote_exec", runtime_id=state["runtime_id"], code=code, timeout_seconds=45,_timeout_seconds=75)
        output = result.get("stdout", "")
        marker = "WEB_COMFYUI_JSON="
        lines = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
        if not lines:
            raise BridgeError()
        return json.loads(lines[-1])

    def tick(self, state):
        sid=state.get('session_id')
        if not sid:return self._tick(state)
        try:self.sessions.get(sid)
        except StopIteration:return self._tick(state)
        self.sessions.operation({'id':sid},'attach')
        try:
            result=self._tick(state)
            try:self.sessions.get(sid)
            except StopIteration:return result
            self.sessions.finish_cycle(sid,state['id'])
            return result
        except Exception as error:
            try:self.sessions.get(sid)
            except StopIteration:return self._tick(state)
            self.sessions.record_fault({'id':sid},error)

    def _tick(self, state):
        # Another account's slow provider call may have delayed this queued
        # snapshot. Observe cancellations committed since the sweep started.
        with self.db() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (state["id"],)).fetchone()
        if row is None:
            return
        state = json.loads(row[0])
        job_id, phase = state["id"], state["phase"]
        if phase in TERMINAL:
            return
        if state.get('session_id'):
            try:
                session=self.sessions.get(state['session_id'])
            except StopIteration:
                state=self.update(job_id,phase='cleanup_pending',outcome='failed',orphan_session=True)
                return self._cleanup(state)
            if session['phase'] in {'closing','stopping','released_unsettled','closed'} and (phase!='queued' or session['phase']!='closed'):
                state=self.update(job_id,phase='cleanup_pending',outcome='cancelled' if state.get('cancel_requested') else state.get('outcome','failed'))
                return self._cleanup(state)
        if state.get('session_job') and phase == 'queued':
            if state.get('cancel_requested'):
                self.update(job_id,phase='cancelled',released=True)
                return
            with self.db() as db:
                owner=db.execute('SELECT owner FROM jobs WHERE id=?',(job_id,)).fetchone()[0]
            if state.get('session_id'):
                session=self.sessions.get(state['session_id'])
                if session['phase']=='closed':
                    self.update(job_id,phase='failed',released=True,allocation_code='allocation_unavailable')
                    return
            else:
                self.update(job_id,phase='failed',released=True,allocation_code='allocation_unavailable',message='工作階段關聯遺失；請檢查原請求')
            return
        expired = self.clock() >= state["deadline_epoch"]
        if phase == 'allocation_retry_wait':
            if state.get('cancel_requested') or expired:
                self.update(job_id,phase='cancelled' if state.get('cancel_requested') else 'failed',released=True)
                return
            if self.clock() < state['allocation_retry_at']:return
            # The previous attempt has explicit no-assignment evidence. Preserve
            # its ID and allocate the same GPU only once more under the same job.
            state=self.update(job_id,phase='queued',allocation_id=str(uuid.uuid4()))
            phase='queued'
        if phase == "queued" and (state.get("cancel_requested") or expired):
            self.update(job_id, phase="cancelled" if state.get("cancel_requested") else "failed", released=True)
            return
        if phase == "cleanup_pending":
            return self._cleanup(state)
        if state.get("cancel_requested") or expired:
            state = self.update(job_id, phase="cleanup_pending", outcome="cancelled" if state.get("cancel_requested") else "failed", message="已要求停止並釋放 GPU" if not expired else "已達時間上限，正在釋放 GPU")
            return self._cleanup(state)
        if phase == "queued":
            if not state.get("uploads") or not state.get("watchdog_sha256"):
                self.update(job_id, phase="failed", released=True, message="任務缺少固定的執行器版本，請重新建立")
                return
            state = self.update(job_id, phase="allocating", allocation_attempted_at=self.clock(), allocation_attempts=state.get("allocation_attempts",0)+1)  # durable BEFORE the paid mutation
            try:
                runtime = self._call(state, "runtime_allocate", allocation_id=state["allocation_id"], gpu=state["gpu"])
            except BridgeError as error:
                code = error.code if error.code in {'allocation_unavailable','allocation_rejected'} and not error.ambiguous else 'allocation_uncertain'
                observations=state.get('allocation_observations',[])+[{'at':self.clock(),'allocation_id':state['allocation_id'],'code':code,**error.diagnostics}]
                changes=dict(allocation_code=code,allocation_diagnostics=error.diagnostics,allocation_observations=observations)
                if code == 'allocation_unavailable' and not state.get('native_parity') and state['gpu']=='A100' and state['allocation_attempts'] < 2:
                    self.update(job_id,phase='allocation_retry_wait',allocation_retry_at=self.clock()+300,
                        message='A100 明確未獲配；至少五分鐘後只重試一次，不更換 GPU',**changes)
                elif code in {'allocation_unavailable','allocation_rejected'}:
                    self.update(job_id,phase='failed',released=True,message='GPU 明確未獲配；已停止申請',**changes)
                else:
                    self.update(job_id,phase='allocation_uncertain',message='連線結果未確認；保留原配置 ID 查回，不重送',**changes)
                return
            if not runtime.get("found") or not runtime.get("runtime_id"):
                self.update(job_id, phase="allocation_uncertain")
                return
            self.update(job_id, phase="preparing", runtime_id=runtime["runtime_id"])
            return
        if phase in {"allocating", "allocation_uncertain"}:
            runtime = self._call(state, "runtime_lookup", allocation_id=state["allocation_id"], gpu=state["gpu"])
            if runtime.get("found"):
                self.update(job_id, phase="preparing", runtime_id=runtime["runtime_id"])
            return
        if not state.get("session_id") and self.clock() - state.get("usage_checked_at", 0) >= 60:
            try:
                usage = self._call(state, "account_status")
            except BridgeError:
                state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="無法確認點數，正在停止 GPU")
                return self._cleanup(state)
            balance = usage.get("balance_compute_units")
            if not isinstance(balance, (int, float)) or not math.isfinite(balance):
                state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="無法確認點數，正在停止 GPU")
                return self._cleanup(state)
            spent = state.get("units_observed", 0) + max(state.get("last_balance", state["baseline_units"]) - balance, 0)
            state = self.update(job_id, units_observed=spent, last_balance=balance, usage_checked_at=self.clock())
            if (state.get("max_units") is not None and spent >= state["max_units"]) or balance <= 0:
                state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="已達點數停止門檻，正在釋放 GPU")
                return self._cleanup(state)
        remote = "/content/web-comfyui/" + job_id
        if phase == "preparing":
            if not state.get("watchdog_armed"):
                try:
                    watchdog = (self.root / "jobs" / job_id / "watchdog.py").read_bytes()
                    if digest(watchdog) != state["watchdog_sha256"]:
                        raise ColabError("停止監測程式驗證失敗")
                    acknowledgement = self._exec(state, watchdog.decode())
                    if not acknowledgement.get("armed") or acknowledgement.get("job_id") != job_id or acknowledgement.get("manifest_sha256") != state["manifest_sha256"] or acknowledgement.get("deadline_epoch") != state["deadline_epoch"]:
                        raise ColabError("雲端停止期限未確認")
                    self.update(job_id, watchdog_armed=True)
                except Exception as error:
                    # Reconcile only the idempotent watchdog in this same runtime.
                    # A cold Jupyter kernel may time out before returning its ack.
                    # Never allocate again or start the generation worker here.
                    attempts = state.get("watchdog_attempts", 0) + 1
                    code = (error.code if isinstance(error, BridgeError) and error.code in {
                        "timeout", "execution_uncertain", "remote_execution_failed", "invalid_runtime",
                        "authentication_required", "invalid_remote_output", "bridge_unavailable"
                    } else "watchdog_ack_invalid")
                    if code in {"timeout", "execution_uncertain"} and attempts < 3:
                        self.update(job_id, watchdog_attempts=attempts, preparation_error_code=code,
                            message="雲端執行環境尚未回應，正在確認同一台 GPU 的停止監測")
                        return
                    state = self.update(job_id, phase="cleanup_pending", outcome="failed",
                        watchdog_attempts=attempts, preparation_error_code=code,
                        message="無法確認雲端停止期限，正在釋放 GPU")
                    return self._cleanup(state)
                return
            directory = self.root / "jobs" / job_id
            paths = state["uploads"]
            # One transfer per reconciliation pass keeps other jobs, cancellation
            # and usage checks responsive even when a media upload is slow.
            position = state.get("upload_index", 0)
            if position < len(paths):
                entry = paths[position]
                relative = entry["path"]
                local = directory / relative
                from scripts.colab_upload_diagnostics import classify, MAX_ATTEMPTS, BACKOFF_SECONDS
                attempts = state.get('upload_attempts', 0) if state.get('upload_attempt_index') == position else 0
                if attempts >= MAX_ATTEMPTS:
                    return self._upload_failed(state, state.get('remote_diagnostics') or classify(BridgeError()))
                if self.clock() < state.get('upload_retry_at', 0): return
                try:
                    valid = (not local.is_symlink() and local.is_file() and local.resolve().is_relative_to(directory.resolve())
                             and local.stat().st_size == entry['size'] and digest(local.read_bytes()) == entry['sha256'])
                except OSError:
                    valid = False
                if not valid:
                    return self._upload_failed(state, classify(BridgeError('upload_local_invalid'), 'local_validation'))
                with self.db() as db:
                    fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
                if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                    return self.tick(fresh)
                # Persist the attempt BEFORE PUT. Restart resumes this same shard;
                # even crashes/response loss cannot grant an unbounded retry budget.
                attempts += 1
                state = self.update(job_id, upload_attempt_index=position, upload_attempts=attempts,
                                    upload_retry_at=self.clock()+BACKOFF_SECONDS[min(attempts-1, len(BACKOFF_SECONDS)-1)])
                if state.get('session_id'):self.sessions.operation({'id':state['session_id']},'upload')
                try:
                    self._call(state, "upload", runtime_id=state["runtime_id"], local_path=str(local), remote_path=remote + "/" + relative, max_bytes=MAX_INPUT)
                except Exception as error:
                    if state.get('session_id'):self.sessions.record_fault({'id':state['session_id']},error)
                    diagnostic = classify(error)
                    if attempts >= MAX_ATTEMPTS:
                        return self._upload_failed(state, diagnostic)
                    self.update(job_id, remote_diagnostics=diagnostic,
                                upload_retry_at=self.clock()+BACKOFF_SECONDS[attempts-1],
                                message="素材傳輸未完成，稍後重試同一區塊")
                    return
                self.update(job_id, upload_index=position + 1, upload_attempts=0,
                            upload_attempt_index=position+1, upload_retry_at=0,
                            remote_diagnostics={}, message=None)
                return
            with self.db() as db:
                fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                return self.tick(fresh)
            state = self.update(job_id, phase="launching", launch_requested_at=self.clock())
            code = ("import subprocess,sys,json\nfrom pathlib import Path\n"
                    f"d=Path({remote!r})\n"
                    f"p=subprocess.Popen([sys.executable,str(d/'runner/remote_job.py'),'--job-dir',str(d),'--manifest-sha256',{state['manifest_sha256']!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n"
                    f"identity={{'job_id':{job_id!r},'manifest_sha256':{state['manifest_sha256']!r},'pid':p.pid}}\n"
                    "try: identity['start_ticks']=Path('/proc/'+str(p.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]\n"
                    "except OSError: identity['start_ticks']=None\n"
                    "tmp=d/'launch.tmp';tmp.write_text(json.dumps(identity));tmp.replace(d/'launch.json')\n"
                    "print('WEB_COMFYUI_JSON='+json.dumps({'launched':True}))")
            if state.get('session_id'):
                session_remote='/content/castle-sessions/'+state['session_id']
                setup=("import os\n"+f"src=Path({session_remote!r})/'runner'\n(d/'runner').mkdir(exist_ok=True)\n"+
                       "for f in src.iterdir():\n if f.is_file() and not (d/'runner'/f.name).exists():os.link(f,d/'runner'/f.name)\n")
                code=code.replace("p=subprocess.Popen",setup+"p=subprocess.Popen")
                old="str(d/'runner/remote_job.py'),'--job-dir',str(d),'--manifest-sha256'"
                new=repr(session_remote+'/session_worker.py')+",'job',"+repr(session_remote)+",str(d)"
                code=code.replace(old,new)
            try:
                self._exec(state, code)
                self.update(job_id, phase="running")
            except Exception as error:
                # The original launch is only reconciled, never dispatched again.
                if state.get('session_id'):self.sessions.record_fault({'id':state['session_id']},error,reconciling_launch=True)
                self.update(job_id, phase="launch_uncertain", message="正在確認已送出的啟動指令")
            return
        if phase in {"launching", "launch_uncertain", "running", "downloading"}:
            code = ("import json\nfrom pathlib import Path\n"
                    f"d=Path({remote!r})\n"
                    "def read(name):\n p=d/name\n return json.loads(p.read_text()) if p.is_file() and p.stat().st_size<524288 else None\n"
                    "launch=read('launch.json');dead=False\n"
                    f"if launch and launch.get('job_id')=={job_id!r} and launch.get('manifest_sha256')=={state['manifest_sha256']!r}:\n"
                    " try:\n  proc=Path('/proc/'+str(int(launch['pid']))+'/stat').read_text().rsplit(')',1)[1].split()\n  dead=proc[0]=='Z' or proc[19]!=launch.get('start_ticks')\n"
                    " except (OSError,ValueError,KeyError,IndexError): dead=True\n"
                    "print('WEB_COMFYUI_JSON='+json.dumps({'status':read('status.json'),'artifacts':read('artifacts.json'),'worker_dead':dead}))")
            if state.get('session_id'):
                self.sessions.operation({'id':state['session_id']},'job_probe')
                log_path='/content/castle-sessions/'+state['session_id']+'/job-evidence/'+job_id+'/comfy-startup.log'
                addition=(f"log_path=Path({log_path!r})\n"
                          "log_stats=None\n"
                          "try:\n log_bytes=log_path.lstat().st_size\n"
                          " try: previous_log_bytes=Path(str(log_path)+'.prev').lstat().st_size\n"
                          " except FileNotFoundError: previous_log_bytes=0\n"
                          " log_stats={'log_bytes':log_bytes,'previous_log_bytes':previous_log_bytes}\n"
                          "except Exception: pass\n")
                code=code.replace("print('WEB_COMFYUI_JSON='+json.dumps(",addition+"print('WEB_COMFYUI_JSON='+json.dumps(")
                code=code.replace("'worker_dead':dead}","'worker_dead':dead,**({'log_stats':log_stats} if log_stats is not None else {})}")
            report = self._exec(state, code)
            stats=report.get('log_stats') if isinstance(report,dict) else None
            if (state.get('session_id') and isinstance(stats,dict) and set(stats)=={'log_bytes','previous_log_bytes'}
                    and all(type(stats[k]) is int and 0<=stats[k]<=2**40 for k in stats)):
                # Private accounting is best effort and cannot affect the probe outcome.
                try:
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        row=db.execute('SELECT state FROM work_sessions WHERE id=?',(state['session_id'],)).fetchone()
                        if row:
                            session=json.loads(row[0]);prior=session.get('private_log_stats') or {}
                            session['private_log_stats']={'observed_at':self.clock(),**stats,
                                'max_log_bytes':max(prior.get('max_log_bytes',0),stats['log_bytes']),
                                'previous_seen':bool(prior.get('previous_seen') or stats['previous_log_bytes'])}
                            db.execute('UPDATE work_sessions SET state=? WHERE id=?',(encoded(session),state['session_id']))
                except Exception:pass
            status = report.get("status") or {}
            if status.get("job_id") != job_id or status.get("manifest_sha256") != state["manifest_sha256"]:
                if report.get("worker_dead") or self.clock() >= state.get("launch_requested_at", state["created_at"]) + 120:
                    state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="執行器未成功啟動，正在釋放 GPU",
                                        remote_diagnostics={'stage': 'worker_status', 'error_type': 'WorkerExited'})
                    return self._cleanup(state)
                return
            remote_phase = status.get("phase", status.get("state", ""))
            self.update(job_id, remote_phase=remote_phase, message=None)
            if remote_phase in {"failed", "ambiguous", "timeout", "cancelled", "submission_uncertain"}:
                from scripts.colab_session_diagnostics import sanitize_diagnostics
                from scripts.colab_session_probe import private_diagnostics
                diagnostic=sanitize_diagnostics(status.get('diagnostics'))
                message="遠端環境缺少必要元件，尚未開始生成" if diagnostic.get('error_code')=='dependency_gap' else "雲端生成未完成；保留紀錄並釋放 GPU"
                state = self.update(job_id, phase="cleanup_pending", outcome="failed", message=message,
                                    remote_diagnostics=diagnostic,private_dependency_diagnostics=private_diagnostics(status.get('private_dependency_diagnostics',{})))
                return self._cleanup(state)
            if remote_phase not in {"complete", "completed"}:
                if report.get("worker_dead"):
                    state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="雲端執行器已停止，正在釋放 GPU",
                                        remote_diagnostics={'stage': 'worker_status', 'error_type': 'WorkerExited'})
                    return self._cleanup(state)
                return
            if state.get('session_id') and not report.get('worker_dead'):
                return
            state = self.update(job_id, phase="downloading")
            try:
                outputs = self._download(state, report.get("artifacts"), remote)
            except (BridgeError, OSError) as error:
                if state.get('session_id'):self.sessions.record_fault({'id':state['session_id']},error)
                self.update(job_id, message="成果尚未通過下載驗證，會在時間上限內重新取得")
                return
            except (ColabError, ValueError):
                with self.db() as db:
                    fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
                if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                    return self.tick(fresh)
                state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="成果清單、格式或內容驗證失敗，停止重試並釋放 GPU")
                return self._cleanup(state)
            with self.db() as db:
                fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                return self.tick(fresh)
            state = self.update(job_id, phase="cleanup_pending", outcome="completed", artifacts=outputs, message="成果已驗證，正在釋放 GPU")
            return self._cleanup(state)

    def _download(self, state, manifest, remote):
        if not isinstance(manifest, dict) or manifest.get("job_id") != state["id"] or manifest.get("manifest_sha256") != state["manifest_sha256"]:
            raise ColabError("成果清單身分不符")
        entries = manifest.get("artifacts", [])
        if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
            raise ColabError("成果清單無效")
        outputs = []
        for index, entry in enumerate(entries):
            with self.db() as db:
                fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (state["id"],)).fetchone()[0])
            if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                raise ColabError("任務已要求停止")
            path = PurePosixPath(str(entry.get("path", "")))
            size, checksum = entry.get("size"), str(entry.get("sha256", ""))
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "out" or len(path.parts) != 2 or "\\" in str(path):
                raise ColabError("成果路徑無效")
            if type(size) is not int or not 0 < size <= MAX_OUTPUT or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise ColabError("成果驗證資料無效")
            if path.suffix.lower() not in {".png", ".jpg", ".webp", ".mp4", ".webm", ".wav", ".flac", ".mp3"}:
                raise ColabError("成果格式無效")
            name = f"{index + 1}{path.suffix.lower()}"
            local = self.root / "jobs" / state["id"] / "results" / name
            local.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._call(state, "download", runtime_id=state["runtime_id"], local_path=str(local), remote_path=remote + "/" + str(path), max_bytes=size)
            sha = hashlib.sha256()
            with local.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(chunk)
            if local.stat().st_size != size or sha.hexdigest() != checksum:
                local.unlink(missing_ok=True)
                raise ColabError("成果內容與清單不符")
            os.chmod(local, 0o600)
            metadata = validate_media(local)
            if state.get("output_kind") and metadata["kind"] != state["output_kind"]:
                raise ColabError("成果種類與請求不符")
            outputs.append({"name": name, "size": size, "sha256": checksum, **metadata, "url": f"/api/colab/jobs/{state['id']}/files/{name}"})
        return outputs

    def _upload_failed(self, state, diagnostic):
        from scripts.colab_session_diagnostics import sanitize_diagnostics
        state = self.update(state['id'], phase='cleanup_pending', outcome='failed',
                            remote_diagnostics=sanitize_diagnostics(diagnostic),
                            message='素材傳輸失敗，正在確認 GPU 釋放')
        return self._cleanup(state)

    def _cleanup(self, state):
        if state.get('session_id') and not state.get('orphan_session'):
            return self.sessions.finish_job(state)
        # Fetch only the bounded structured status before releasing a native lease.
        # Cancellation/deadline paths also reach here without normal status polling.
        if not state.get('orphan_session') and state.get('native_parity') and state.get('runtime_id') and not state.get('diagnostics_fetched_at') and (state.get('remote_diagnostics') or {}).get('stage') != 'upload':
            try:
                from scripts.colab_session_diagnostics import sanitize_diagnostics
                remote='/content/web-comfyui/'+state['id']
                code=("import json\nfrom pathlib import Path\n"+
                      f"p=Path({remote!r})/'status.json'\n"+
                      "s=json.loads(p.read_text()) if p.is_file() and p.stat().st_size<524288 else {}\n"+
                      "print('WEB_COMFYUI_JSON='+json.dumps({'status':s}))")
                status=self._exec(state,code).get('status',{})
                if status.get('job_id')==state['id'] and status.get('manifest_sha256')==state.get('manifest_sha256'):
                    diagnostic=sanitize_diagnostics(status.get('diagnostics'))
                    if diagnostic:state=self.update(state['id'],remote_diagnostics=diagnostic,diagnostics_fetched_at=self.clock())
            except Exception:pass  # Cleanup must still release the GPU on diagnostic read failure.
        if not state.get("runtime_id"):

            runtime = self._call(state, "runtime_lookup", allocation_id=state["allocation_id"], gpu=state["gpu"])
            if runtime.get("found"):
                state = self.update(state["id"], runtime_id=runtime["runtime_id"])
            else:
                # A timed-out allocation can still be completing remotely.
                # Reconcile through a grace period instead of declaring it gone
                # on the first immediately empty lookup after cancellation.
                if self.clock() < state.get("allocation_attempted_at", 0) + 150:
                    self.update(state["id"], message="仍在確認未回應的 GPU 申請，尚未宣告釋放")
                    return
                self.update(state["id"], phase=state.get("outcome", "failed"), released=True)
                return
        result = self._call(state, "runtime_stop", runtime_id=state["runtime_id"])
        if result.get("stopped") or result.get("released"):
            self.update(state["id"], phase=state.get("outcome", "failed"), released=True)
