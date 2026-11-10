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
import math
import os
from pathlib import Path, PurePosixPath
import sqlite3
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import uuid

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
GPU_TYPES = {"L4", "G4", "A100", "H100"}
MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
STATES = {
    "queued": "等待啟動", "allocating": "正在申請 GPU", "allocation_uncertain": "確認 GPU 申請結果",
    "preparing": "準備雲端環境", "launching": "正在啟動", "launch_uncertain": "確認啟動結果",
    "running": "雲端執行中", "downloading": "下載成果", "cleanup_pending": "等待確認 GPU 已釋放",
    "completed": "完成，GPU 已釋放", "failed": "失敗，GPU 已釋放", "cancelled": "已取消，GPU 已釋放",
}


class ColabError(ValueError):
    pass


class BridgeError(ColabError):
    def __init__(self, code="bridge_unavailable", ambiguous=False):
        self.code, self.ambiguous = code, ambiguous
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


def validate_media(path):
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
    result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)], capture_output=True, timeout=30)
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
        request = {"op": op, "account_dir": str(account_dir), **values}
        # No credential discovery from ambient application environments.
        env = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT", "SSL_CERT_FILE") if key in os.environ}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        try:
            response = subprocess.run(
                [self.python, str(ROOT / "scripts/colab_bridge.py")],
                input=encoded(request), capture_output=True, text=True, env=env,
                timeout=150 if op in {"upload", "download"} else 75,
            )
            if len(response.stdout) > 2 * 1024 * 1024:
                raise BridgeError()
            payload = json.loads(response.stdout)
            if response.returncode or not payload.get("ok"):
                error = payload.get("error") or {}
                raise BridgeError(str(error.get("code", "bridge_unavailable")), bool(error.get("ambiguous")))
            return payload["result"]
        except subprocess.TimeoutExpired as error:
            raise BridgeError("timeout", op in {"runtime_allocate", "remote_exec", "runtime_stop"}) from error
        except (OSError, KeyError, ValueError) as error:
            if isinstance(error, BridgeError):
                raise
            raise BridgeError() from error


class ColabService:
    def __init__(self, root, bridge=None, clock=time.time):
        self.root = Path(root).expanduser()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.bridge = bridge or Bridge(os.environ.get("WEB_COMFYUI_COLAB_PYTHON", os.environ.get("CASTLE_COLAB_PYTHON", "")))
        self.clock = clock
        self._thread = None
        self._start_lock = threading.Lock()
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, label TEXT NOT NULL, snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, account_id TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL)")
        os.chmod(self.root / "state.sqlite3", 0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / "state.sqlite3", timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
    def account_guard(self, account_id):
        with (self.account_dir(account_id) / "controller.lock").open("a") as lock:
            file_lock.flock(lock.fileno(), file_lock.LOCK_EX)
            yield

    def _account_action(self, owner, account_id, action, values):
        self.account(owner, account_id)
        if action in {"login", "finish"}:
            with self.db() as db:
                states = [json.loads(row[0]) for row in db.execute("SELECT state FROM jobs WHERE account_id=?", (account_id,))]
            if any(state["phase"] not in TERMINAL for state in states):
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
        result = self.bridge("account_status", path)
        snapshot = {key: result.get(key) for key in ("authenticated", "balance_compute_units", "consumption_rate_per_hour", "active_assignments")}
        snapshot["checked_at"] = self.clock()
        with self.db() as db:
            db.execute("UPDATE accounts SET snapshot=? WHERE id=?", (encoded(snapshot), account_id))
        return snapshot

    def snapshot(self, owner):
        with self.db() as db:
            accounts = [{"id": r["id"], "label": r["label"], **json.loads(r["snapshot"])} for r in db.execute("SELECT * FROM accounts WHERE owner=? ORDER BY rowid", (owner,))]
            jobs = [self.public_job(json.loads(r["state"])) for r in db.execute("SELECT state FROM jobs WHERE owner=? ORDER BY rowid DESC LIMIT 100", (owner,))]
        return {"installed": self.bridge.installed, "accounts": accounts, "jobs": jobs}

    def job(self, owner, job_id):
        canonical_uuid(job_id)
        with self.db() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if not row:
            raise ColabError("找不到這個任務")
        return json.loads(row["state"])

    @staticmethod
    def public_job(state):
        fields = ("id", "account_id", "phase", "created_at", "updated_at", "deadline_epoch", "gpu", "max_units", "max_minutes", "units_observed", "remote_phase", "message", "artifacts", "cancel_requested", "runtime_id", "released")
        result = {key: state.get(key) for key in fields}
        from scripts.colab_remote.remote_job import sanitize_diagnostics
        result['diagnostics'] = sanitize_diagnostics(state.get('remote_diagnostics'))
        result["status_label"] = STATES.get(state["phase"], state["phase"])
        return result

    def update(self, job_id, **changes):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            state.update(changes, updated_at=self.clock())
            db.execute("UPDATE jobs SET state=? WHERE id=?", (encoded(state), job_id))
        return state

    def submit(self, owner, body):
        self.account(owner, canonical_uuid(body.get("account_id")))
        with self.account_guard(body["account_id"]):
            return self._submit(owner, body)

    def _submit(self, owner, body):
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
            max_units = float(body.get("max_units", 20))
            max_minutes = int(body.get("max_minutes", 60))
        except (ValueError, TypeError) as error:
            raise ColabError("點數及時間上限必須是數字") from error
        if not math.isfinite(max_units) or not 5 <= max_units <= 2000 or not 5 <= max_minutes <= 180:
            raise ColabError("點數上限須為 5–2000；執行時間須為 5–180 分鐘")
        spec = body.get("job")
        if not isinstance(spec, dict):
            raise ColabError("缺少生成設定")
        # The browser submits choices, never filesystem paths or executable graphs.
        allowed = {"type", "prompt", "seconds", "resolution", "aspect", "mode", "seed", "intensity", "fast", "size"}
        if set(spec) - allowed:
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
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".wav", ".mp3", ".flac", ".m4a"}:
                raise ColabError("不支援的參考檔案格式")
            try:
                raw = base64.b64decode(entry.get("data", ""), validate=True)
            except (ValueError, TypeError) as error:
                raise ColabError("無效的參考檔案") from error
            total += len(raw)
            if not raw or total > MAX_INPUT:
                raise ColabError("參考檔案合計上限為 32 MB")
            name = f"input/{index + 1}{suffix}"
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
            manifest = validate_manifest(manifest, directory, check_files=False)
        except ValueError as error:
            raise ColabError("生成設定或參考素材不符合這個模式；請檢查秒數、素材種類及描述中的參考編號") from error
        # Reject corrupt selected media before a paid GPU is requested. This
        # reads only the files explicitly submitted with this request.
        with tempfile.TemporaryDirectory(prefix="preflight-", dir=self.root) as temporary:
            for name, raw in raw_files:
                target = Path(temporary) / Path(name).name
                private_write(target, raw)
                validate_media(target)
        status = self.account_action(owner, account_id, "refresh", {})
        identity = self.bridge("account_identity", self.account_dir(account_id))
        subject_key = identity.get("subject_key")
        if not identity.get("bound") or not isinstance(subject_key, str) or len(subject_key) != 64 or any(c not in "0123456789abcdef" for c in subject_key):
            raise ColabError("請重新連接 Google 帳號以確認身分，再開始生成")
        balance = status.get("balance_compute_units")
        if not status.get("authenticated") or not isinstance(balance, (int, float)) or not math.isfinite(balance) or balance < max_units:
            raise ColabError("請先連接帳號並確認點數餘額足夠；點數上限不能高於目前餘額")
        state = {"id": job_id, "account_id": account_id, "phase": "queued", "gpu": gpu, "max_units": max_units, "max_minutes": max_minutes,
                 "created_at": now, "updated_at": now, "deadline_epoch": manifest["deadline_epoch"], "allocation_id": str(uuid.uuid4()),
                 "subject_key": subject_key, "output_kind": "audio" if spec["type"] == "tts" else spec["type"],
                 "baseline_units": balance, "last_balance": balance, "units_observed": 0, "artifacts": [], "released": False, "cancel_requested": False}
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute("SELECT owner,request_hash,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if duplicate:
                if duplicate["owner"] != owner or duplicate["request_hash"] != request_hash:
                    raise ColabError("同一請求識別碼不能用於不同內容")
                return self.public_job(json.loads(duplicate["state"]))
            active = [json.loads(r[0]) for r in db.execute("SELECT state FROM jobs")]
            if any(item["phase"] not in TERMINAL and (item["account_id"] == account_id or item.get("subject_key") == subject_key) for item in active):
                raise ColabError("這個帳號已有執行中或待回收的任務，請先完成或釋放 GPU")
            for name, raw in raw_files:
                private_write(directory / name, raw)
            manifest_bytes = encoded(manifest).encode()
            private_write(directory / "manifest.json", manifest_bytes)
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
                private_write(directory / relative, raw)
                uploads.append({"path": relative, "size": len(raw), "sha256": digest(raw)})
            uploads.append({"path": "manifest.json", "size": len(manifest_bytes), "sha256": state["manifest_sha256"]})
            uploads.extend(file_manifest)
            state["uploads"] = uploads
            from scripts.colab_watchdog import build_watchdog_code
            watchdog = build_watchdog_code(job_id, state["deadline_epoch"], state["manifest_sha256"]).encode()
            private_write(directory / "watchdog.py", watchdog)
            state["watchdog_sha256"] = digest(watchdog)
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (job_id, owner, account_id, request_hash, encoded(state)))
        self.start()
        return self.public_job(state)

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
            self._thread = threading.Thread(target=self._loop, name="web-comfyui-colab", daemon=True)
            self._thread.start()

    def _loop(self):
        # A separate process can take over only after the former worker exits.
        with (self.root / "worker.lock").open("a") as lock:
            try:
                file_lock.flock(lock.fileno(), file_lock.LOCK_EX | file_lock.LOCK_NB)
            except BlockingIOError:
                return
            while True:
                with self.db() as db:
                    states = [json.loads(row[0]) for row in db.execute("SELECT state FROM jobs")]
                active = [s for s in states if s["phase"] not in TERMINAL]
                if not active:
                    time.sleep(10)
                    continue
                for state in active:
                    try:
                        self.tick(state)
                    except Exception:
                        # Never place raw provider messages, prompts, tokens or paths in UI/logs.
                        self.update(state["id"], message="連線暫時中斷；保留任務識別碼，正在確認狀態")
                time.sleep(10)

    def _call(self, state, op, **values):
        return self.bridge(op, self.account_dir(state["account_id"]), **values)

    def _exec(self, state, code):
        result = self._call(state, "remote_exec", runtime_id=state["runtime_id"], code=code, timeout_seconds=45)
        output = result.get("stdout", "")
        marker = "WEB_COMFYUI_JSON="
        lines = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
        if not lines:
            raise BridgeError()
        return json.loads(lines[-1])

    def tick(self, state):
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
        expired = self.clock() >= state["deadline_epoch"]
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
            state = self.update(job_id, phase="allocating", allocation_attempted_at=self.clock())  # durable BEFORE the paid mutation
            try:
                runtime = self._call(state, "runtime_allocate", allocation_id=state["allocation_id"], gpu=state["gpu"])
            except BridgeError:
                self.update(job_id, phase="allocation_uncertain", message="正在確認 GPU 申請結果；不會重複申請")
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
        if self.clock() - state.get("usage_checked_at", 0) >= 60:
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
            if spent >= state["max_units"] or balance <= 0:
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
                if local.is_symlink() or not local.is_file() or not local.resolve().is_relative_to(directory.resolve()) or local.stat().st_size != entry["size"] or digest(local.read_bytes()) != entry["sha256"]:
                    state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="執行器或素材內容驗證失敗，正在釋放 GPU")
                    return self._cleanup(state)
                with self.db() as db:
                    fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
                if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                    return self.tick(fresh)
                self._call(state, "upload", runtime_id=state["runtime_id"], local_path=str(local), remote_path=remote + "/" + relative, max_bytes=MAX_INPUT)
                self.update(job_id, upload_index=position + 1)
                return
            with self.db() as db:
                fresh = json.loads(db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            if fresh.get("cancel_requested") or self.clock() >= fresh["deadline_epoch"]:
                return self.tick(fresh)
            state = self.update(job_id, phase="launching", launch_requested_at=self.clock())
            code = ("import subprocess,sys,json\nfrom pathlib import Path\n"
                    f"d=Path({remote!r})\n"
                    "with (d/'runner.log').open('ab') as log:\n"
                    f" p=subprocess.Popen([sys.executable,str(d/'runner/remote_job.py'),'--job-dir',str(d),'--manifest-sha256',{state['manifest_sha256']!r}],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)\n"
                    f"identity={{'job_id':{job_id!r},'manifest_sha256':{state['manifest_sha256']!r},'pid':p.pid}}\n"
                    "try: identity['start_ticks']=Path('/proc/'+str(p.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]\n"
                    "except OSError: identity['start_ticks']=None\n"
                    "tmp=d/'launch.tmp';tmp.write_text(json.dumps(identity));tmp.replace(d/'launch.json')\n"
                    "print('WEB_COMFYUI_JSON='+json.dumps({'launched':True}))")
            try:
                self._exec(state, code)
                self.update(job_id, phase="running")
            except Exception:
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
            report = self._exec(state, code)
            status = report.get("status") or {}
            if status.get("job_id") != job_id or status.get("manifest_sha256") != state["manifest_sha256"]:
                if report.get("worker_dead") or self.clock() >= state.get("launch_requested_at", state["created_at"]) + 120:
                    state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="執行器未成功啟動，正在釋放 GPU",
                                        remote_diagnostics={'stage': 'worker_status', 'error_type': 'WorkerExited'})
                    return self._cleanup(state)
                return
            remote_phase = status.get("phase", status.get("state", ""))
            self.update(job_id, remote_phase=remote_phase)
            if remote_phase in {"failed", "ambiguous", "timeout", "cancelled", "submission_uncertain"}:
                from scripts.colab_remote.remote_job import sanitize_diagnostics
                state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="雲端生成未完成；保留紀錄並釋放 GPU",
                                    remote_diagnostics=sanitize_diagnostics(status.get('diagnostics')))
                return self._cleanup(state)
            if remote_phase not in {"complete", "completed"}:
                if report.get("worker_dead"):
                    state = self.update(job_id, phase="cleanup_pending", outcome="failed", message="雲端執行器已停止，正在釋放 GPU",
                                        remote_diagnostics={'stage': 'worker_status', 'error_type': 'WorkerExited'})
                    return self._cleanup(state)
                return
            state = self.update(job_id, phase="downloading")
            try:
                outputs = self._download(state, report.get("artifacts"), remote)
            except (BridgeError, OSError):
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

    def _cleanup(self, state):
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
