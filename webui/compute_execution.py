"""Socket-free task execution and verified cross-provider media handoff."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from compute_routing import ComputeRejected


class ExecutionContext:
    """Resolve current authorization on each call; never retain a web session."""
    def __init__(self, server, owner):
        self.s, self.owner = server, owner
        self._compute_dispatch_started = False
        self.current_user()

    def current_user(self):
        user = self.s.user_for_email(self.owner)
        if not user:
            # These two existing login identities are deliberately absent from
            # the named-user store. They remain valid only while local login
            # credentials are configured; never invent an arbitrary owner.
            for name in ('LOCAL_SESSION_USER', 'LEGACY_SESSION_USER'):
                candidate = getattr(self.s, name, None)
                if (isinstance(candidate, dict) and candidate.get('email') == self.owner
                        and getattr(self.s, 'local_credential_version', lambda: None)()):
                    user = dict(candidate)
                    break
        if (not user or user.get('disabled') or user.get('mustSetPassword')
                or user.get('email') != self.owner):
            raise PermissionError('帳號目前無法執行製作任務')
        return dict(user)

    def is_orchestrator_service_authenticated(self):
        return False

    def _generate_result(self, *args, **kwargs):
        return self.s.Handler._generate_result(self, *args, **kwargs)

    def _cancel_job_result(self, *args, **kwargs):
        return self.s.Handler._cancel_job_result(self, *args, **kwargs)

    def _provider_runtime(self):
        return self.s.Handler._provider_runtime(self)

    def _provider_runtime_actor(self):
        return self.s.Handler._provider_runtime_actor(self)


class PipelineExecution:
    def __init__(self, server):
        from compute_pipeline import ComputePipeline
        self.s = server
        self.root = server.get_colab_service().root.parent
        self.engine = ComputePipeline(self.root / 'compute-pipelines.sqlite3',
            router_factory=self.router, artifact_resolver=self.resolve_artifact)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None

    def context(self, owner):
        return ExecutionContext(self.s, owner)

    def router(self, owner):
        from compute_web import ComputeWeb
        return ComputeWeb(self.context(owner), self.s).router

    def resolve_artifact(self, owner, receipt, index):
        context = self.context(owner)
        user = context.current_user()
        artifacts = receipt.get('artifacts') or []
        if receipt.get('state') != 'completed' or type(index) is not int or not 0 <= index < len(artifacts):
            raise ComputeRejected('前一步成果尚未完成驗證', code='artifact_unverified')
        artifact = artifacts[index]
        parsed = urlsplit(artifact.get('url') or '')
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ComputeRejected('成果必須由平台保管', code='artifact_unverified')
        task_id = receipt.get('downstream_id')
        if parsed.path == '/api/view':
            query = parse_qs(parsed.query, strict_parsing=True)
            if set(query) != {'filename', 'subfolder', 'type', 'promptId'}:
                # parse_qs normally omits the empty root subfolder.
                query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
            if set(query) != {'filename', 'subfolder', 'type', 'promptId'} or any(len(v) != 1 for v in query.values()):
                raise ComputeRejected('成果路徑格式不正確', code='artifact_unverified')
            q = {k: v[0] for k, v in query.items()}
            if q['type'] != 'output' or q['promptId'] != task_id or not self.s.user_can_access_view_item(user, q['filename'], q['subfolder'], 'output', task_id):
                raise PermissionError('找不到這個成果')
            source = self.s.COMFY_OUTPUT / q['subfolder'] / q['filename']
            if source.is_symlink() or not source.resolve().is_relative_to(self.s.COMFY_OUTPUT.resolve()):
                raise PermissionError('成果路徑不正確')
        elif match := re.fullmatch(r'/api/colab/jobs/([^/]+)/files/([^/]+)', parsed.path):
            if unquote(match[1]) != task_id:
                raise PermissionError('成果與原任務不一致')
            source = self.s.get_colab_service().artifact(owner, unquote(match[1]), unquote(match[2]))
        elif match := re.fullmatch(r'/api/compute/hosted/(comfy_cloud|runninghub)/([^/]+)/files/([^/]+)', parsed.path):
            if unquote(match[2]) != task_id:
                raise PermissionError('成果與原任務不一致')
            source = self.s.get_hosted_compute_service().artifact(owner, match[1], unquote(match[2]), unquote(match[3]))
            if isinstance(source, tuple):
                source = source[0]
        elif match := re.fullmatch(r'/api/assets/([a-z0-9][a-z0-9._:-]{0,127})/source', parsed.path):
            runtime, actor = context._provider_runtime_actor()
            # Artifact IDs are supplied only by the server's exact-intent output
            # receipt. Resolve an owned, hash-verified immutable version.
            source = self.s.resolve_owned_media_source(runtime.store, runtime.provider_media_store,
                actor.user_id, match[1], allowed_file_roots=(self.s.minimax_h3_output_root(),
                self.s.provider_database_path().parent / 'autoip-outputs',
                self.s.provider_database_path().parent / 'production-edits')).path
        else:
            raise ComputeRejected('這類成果尚未支援跨步驟傳遞', code='artifact_unsupported')
        expected = receipt.get('output_kind')
        if expected not in {'image', 'audio', 'video'}:
            expected = None
        return self.freeze_media(owner, user, Path(source), expected_kind=expected)

    def freeze_image(self, owner, user, source):
        """Compatibility entry point for existing image-only admission."""
        return self.freeze_media(owner, user, source, expected_kind='image')

    def freeze_media(self, owner, user, source, *, expected_kind=None):
        """Freeze owned media and validate its decoded image/stream metadata.

        The caller must establish source ownership before calling this method.
        Every accepted file is at most 64 MiB; images at most 40 MP, and audio
        or video at most one hour. No client URL or path enters the resolver.
        """
        from PIL import Image
        from colab_service import validate_media
        source = Path(source)
        suffix = source.suffix.lower()
        kinds = {'.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
                 '.wav': 'audio', '.mp3': 'audio', '.flac': 'audio', '.m4a': 'audio',
                 '.mp4': 'video', '.mov': 'video', '.webm': 'video'}
        kind = kinds.get(suffix)
        if kind is None or (expected_kind is not None and expected_kind != kind):
            raise ComputeRejected('成果類型不符合此素材欄位', code='artifact_kind_mismatch')
        extension = '.jpg' if suffix == '.jpeg' else suffix
        root = self.s.COMFY_INPUT
        root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as reader, tempfile.NamedTemporaryFile(dir=root, prefix='.pipeline-', suffix=extension, delete=False) as writer:
                temporary = Path(writer.name)
                before = os.fstat(reader.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 64 * 1024 * 1024:
                    raise ValueError('invalid source size')
                digest = hashlib.sha256()
                count = 0
                while chunk := reader.read(1024 * 1024):
                    count += len(chunk)
                    if count > 64 * 1024 * 1024:
                        raise ValueError('source exceeds size limit')
                    writer.write(chunk)
                    digest.update(chunk)
                writer.flush()
                os.fsync(writer.fileno())
                after = os.fstat(reader.fileno())
                fingerprint = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if count != before.st_size or fingerprint(before) != fingerprint(after):
                    raise ValueError('source changed during copy')
            if kind == 'image':
                with Image.open(temporary) as image:
                    actual_extension = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
                    if actual_extension != extension or image.width * image.height > 40_000_000:
                        raise ValueError('unsupported image')
            decoded = validate_media(temporary)
            if decoded.get('kind') != kind:
                raise ValueError('decoded media kind does not match')
            if kind == 'image':
                media = {key: decoded[key] for key in ('kind', 'width', 'height')}
            else:
                duration = decoded.get('duration')
                if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 3600:
                    raise ValueError('invalid media duration')
                media = {'kind': kind, 'duration': duration}
            user = self.context(owner).current_user()
            sha = digest.hexdigest()
            owner_key = hashlib.sha256(owner.encode()).hexdigest()[:24]
            target = root / f'pipeline-{owner_key}-{sha}{extension}'
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or target.stat().st_size != count or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                    raise ValueError('frozen input changed')
            temporary.unlink()
            temporary = None
            self.s.record_input_upload_owner(target.name, user)
            return {'file': {'serverFile': target.name}, 'sha256': sha,
                    'artifact_id': f'{kind}:{owner_key}:{sha}', 'media': media}
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ComputeRejected('前一步素材未通過完整性驗證', code='artifact_unverified') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def start(self):
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name='compute-pipelines', daemon=True)
                self._thread.start()
            self._wake.set()

    def _loop(self):
        while True:
            try:
                for work in self.engine.work_items(limit=50):
                    try:
                        self.engine.tick(work['owner'], work['pipeline_id'], max_steps=4)
                    except Exception:
                        # Authorization/status failures never trigger another
                        # submission. Keep the original durable identity.
                        continue
            except Exception:
                pass
            self._wake.wait(5)
            self._wake.clear()
