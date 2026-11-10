#!/usr/bin/env python3
"""Bounded, restart-safe H3 batch worker for one dedicated Colab assignment.

Only main() provisions dependencies or executes a graph. Importing this module
and validate_manifest() are safe for the parent controller and offline tests.
"""
from __future__ import annotations

import argparse
import copy
try:
    import fcntl
except ImportError:  # Parent-side manifest validation also works on Windows.
    fcntl = None
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid

_HELPER_IMPORT_ERROR = None
try:
    if __package__:
        from . import h3graph as G
        from . import upstream_runtime as U
    else:
        import h3graph as G
        import upstream_runtime as U
except Exception as exc:
    # An incomplete upload must still report a bound terminal status, so the
    # controller can release its paid assignment instead of polling forever.
    G = U = None
    _HELPER_IMPORT_ERROR = exc

HERE = Path(__file__).resolve().parent
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
TERMINAL = {'completed', 'failed', 'cancelled'}
ASPECTS = {'auto', '16:9', '9:16', '1:1', '4:3', '3:4', '21:9'}
IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
VIDEO = {'.mp4', '.mov', '.webm', '.mkv', '.avi'}
AUDIO = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac'}
DIAGNOSTIC_STAGES = frozenset({'bootstrap', 'templates', 'source_checkout', 'venv',
    'torch_install', 'requirements_install', 'cuda_check', 'model_inventory',
    'model_download', 'model_verify', 'comfy_start', 'generation', 'artifact_validation',
    'watchdog_start', 'worker_status'})
DIAGNOSTIC_ERRORS = frozenset({'Exception', 'ValidationError', 'SubmissionUncertain',
    'Cancelled', 'CalledProcessError', 'TimeoutExpired', 'ModuleNotFoundError',
    'ImportError', 'SyntaxError', 'JSONDecodeError', 'KeyError', 'AttributeError',
    'TypeError', 'ValueError', 'RuntimeError', 'OSError', 'FileNotFoundError',
    'PermissionError', 'ConnectionError', 'HTTPError', 'URLError', 'WorkerExited'})


def sanitize_diagnostics(value):
    """Permit only controlled vocabulary and bounded status integers, never text."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, allowed in (('stage', DIAGNOSTIC_STAGES), ('error_type', DIAGNOSTIC_ERRORS),
                         ('cause_type', DIAGNOSTIC_ERRORS)):
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    for key, low, high in (('exit_code', -255, 255), ('http_status', 100, 599)):
        if type(value.get(key)) is int and low <= value[key] <= high:
            result[key] = value[key]
    return result


def failure_diagnostics(error, stage):
    def kind(exc):
        name = type(exc).__name__
        return name if name in DIAGNOSTIC_ERRORS else 'Exception'
    result = {'stage': stage, 'error_type': kind(error)}
    if error.__cause__ is not None:
        result['cause_type'] = kind(error.__cause__)
    if isinstance(error, subprocess.CalledProcessError):
        result['exit_code'] = error.returncode
    if isinstance(error, urllib.error.HTTPError):
        result['http_status'] = error.code
    return sanitize_diagnostics(result)


class ValidationError(ValueError):
    pass


class SubmissionUncertain(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)


def contained(root, relative, *, exists=False):
    root = Path(root)
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValidationError('Invalid relative file path')
    rel = Path(relative)
    if rel.is_absolute() or any(p in {'.', '..'} for p in rel.parts):
        raise ValidationError('File path escapes job directory')
    candidate = root / rel
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValidationError('File path escapes job directory')
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise ValidationError('Symlink paths are not accepted')
        cursor = cursor.parent
    if exists and not candidate.is_file():
        raise ValidationError('Referenced input file is missing')
    return candidate


def validate_manifest(manifest, job_dir, check_files=True):
    """Validate and return a fresh normalized batch; no network or execution.

    File paths are job-relative ``input/<generated-name.ext>``. Prior outputs
    use ``@id``. Input digests are required whenever external files are used.
    Seeds default deterministically from the immutable batch UUID and subjob ID.
    The parent must persist/upload the normalized manifest before hashing it.
    """
    if _HELPER_IMPORT_ERROR is not None:
        raise ValidationError('Remote helper bundle could not be imported') from _HELPER_IMPORT_ERROR
    if not isinstance(manifest, dict):
        raise ValidationError('Manifest must be an object')
    allowed = {'schema_version', 'job_id', 'deadline_epoch', 'jobs', 'stop_on_error', 'input_files'}
    if set(manifest) - allowed:
        raise ValidationError('Unknown manifest field')
    m = copy.deepcopy(manifest)
    if m.get('schema_version', 1) != 1:
        raise ValidationError('Unsupported manifest schema')
    m['schema_version'] = 1
    try:
        if str(uuid.UUID(m['job_id'])) != m['job_id']:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValidationError('job_id must be a canonical UUID') from None
    deadline = m.get('deadline_epoch')
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or deadline <= 0:
        raise ValidationError('deadline_epoch must be a finite positive timestamp')
    if not isinstance(m.get('stop_on_error', True), bool):
        raise ValidationError('stop_on_error must be boolean')
    m.setdefault('stop_on_error', True)
    jobs = m.get('jobs')
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 16:
        raise ValidationError('A batch needs between 1 and 16 jobs')
    input_records = m.setdefault('input_files', [])
    if not isinstance(input_records, list) or len(input_records) > 64:
        raise ValidationError('Invalid input inventory')
    inventory = {}
    for item in input_records:
        if not isinstance(item, dict) or set(item) != {'path', 'size', 'sha256'}:
            raise ValidationError('Input records need path, size and sha256')
        path = item['path']
        if not isinstance(path, str) or not re.fullmatch(r'input/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', path) or '..' in path:
            raise ValidationError('Inputs must use generated filenames under input/')
        if path in inventory or Path(path).suffix.lower() not in IMAGE | VIDEO | AUDIO:
            raise ValidationError('Duplicate or unsupported input file')
        if type(item['size']) is not int or not 0 < item['size'] <= MAX_INPUT_BYTES:
            raise ValidationError('Invalid input file size')
        if not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
            raise ValidationError('Invalid input file digest')
        if check_files:
            source = contained(job_dir, path, exists=True)
            if source.stat().st_size != item['size'] or file_digest(source) != item['sha256']:
                raise ValidationError('Input file does not match its immutable inventory')
        inventory[path] = item
    if sum(x['size'] for x in inventory.values()) > MAX_INPUT_BYTES:
        raise ValidationError('Input batch exceeds 256 MiB')
    prior = {}
    used = set()
    def reference(value, kinds):
        if not isinstance(value, str):
            raise ValidationError('Media reference must be a string')
        if value.startswith('@'):
            kind = prior.get(value[1:])
            if kind is None:
                raise ValidationError('Output references must name an earlier job')
            kind = 'audio' if kind == 'tts' else kind
        else:
            if value not in inventory:
                raise ValidationError('Every input must have a digest/size inventory record')
            used.add(value)
            ext = Path(value).suffix.lower()
            kind = 'image' if ext in IMAGE else 'video' if ext in VIDEO else 'audio'
        if kind not in kinds:
            raise ValidationError('Input media type does not fit this field')
        return kind
    common = {'id', 'type', 'prompt', 'seed'}
    specific = {'image': {'inputs', 'aspect', 'size'},
                'video': {'inputs', 'aspect', 'mode', 'seconds', 'resolution', 'intensity', 'fast', 'first', 'last', 'extend'},
                'tts': {'voices', 'seconds'}}
    for j in jobs:
        if not isinstance(j, dict) or j.get('type') not in specific:
            raise ValidationError('Unknown job type')
        kind = j['type']
        if set(j) - common - specific[kind]:
            raise ValidationError('Unknown or inapplicable job field')
        ident = j.get('id')
        if not isinstance(ident, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', ident) or ident in prior:
            raise ValidationError('Job IDs must be unique portable names')
        if not isinstance(j.get('prompt'), str) or not j['prompt'].strip() or len(j['prompt']) > 16000 or '\0' in j['prompt']:
            raise ValidationError('Prompt must contain between 1 and 16000 characters')
        if G.MENTION_RE.search(j['prompt']) and G.LITERAL_RE.search(j['prompt']):
            raise ValidationError('Use one reference notation: @ImageN or <Picture N>')
        seed = j.get('seed')
        if seed in (None, -1):
            j['seed'] = int(hashlib.sha256((m['job_id'] + '/' + ident).encode()).hexdigest()[:12], 16)
        elif type(seed) is not int or not 0 <= seed < 2**64:
            raise ValidationError('Seed must be a nonnegative 64-bit integer')
        if kind != 'tts':
            j.setdefault('aspect', 'auto')
            if j['aspect'] not in ASPECTS:
                raise ValidationError('Unsupported aspect ratio')
        if kind in {'video', 'tts'}:
            j.setdefault('seconds', 5)
            seconds = j['seconds']
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 2 <= seconds <= 15:
                raise ValidationError('Duration must be between 2 and 15 seconds')
            if kind == 'tts' and seconds != int(seconds):
                raise ValidationError('TTS duration must use whole seconds')
        if kind == 'image':
            j.setdefault('size', '2K')
            if j['size'] not in G.IMAGE_SIZES:
                raise ValidationError('Unsupported image size')
        if kind == 'video':
            j.setdefault('mode', 'text')
            j.setdefault('resolution', '480P')
            j.setdefault('intensity', 'off')
            j.setdefault('fast', True)
            if j['mode'] not in {'ref', 'frames', 'text'} or j['resolution'] not in {'480P', '720P', '1080P'} or j['intensity'] not in {'off', 'low', 'mid', 'high'} or type(j['fast']) is not bool:
                raise ValidationError('Unsupported video setting')
            if j['mode'] == 'frames' and not (j.get('first') or j.get('last')):
                raise ValidationError('Frame mode needs a first or last image')
            if j['mode'] != 'frames' and (j.get('first') or j.get('last')):
                raise ValidationError('Frame inputs require frames mode')
            if j.get('extend') and j['mode'] != 'ref':
                raise ValidationError('Extension requires ref mode')
            if j.get('extend') in prior:
                j['extend'] = '@' + j['extend']
            for field in ('first', 'last', 'extend'):
                if j.get(field):
                    reference(j[field], {'video'} if field == 'extend' else {'image'})
        for field in ('inputs', 'voices'):
            if field not in j:
                continue
            values = j[field]
            if not isinstance(values, list) or len(values) > (3 if field == 'voices' else 8):
                raise ValidationError('Too many or invalid media references')
            if kind == 'video' and j['mode'] != 'ref' and values:
                raise ValidationError('inputs requires video ref mode')
            media = [{'kind': reference(v, {'audio'} if field == 'voices' else {'image'} if kind == 'image' else {'image', 'video', 'audio'}), 'name': v} for v in values]
            if kind == 'video' and media:
                _, chosen = G.resolve_mentions(j['prompt'], media)
                if sum(len(v) for v in chosen.values()) != len(media):
                    raise ValidationError('Reference videos must explicitly mention every supplied input')
        prior[ident] = kind
    if set(inventory) != used:
        raise ValidationError('Input inventory must contain only files used by this batch')
    return m


def required_models(manifest, lock):
    """Resolve only model selections used by this batch's reviewed templates."""
    names = set()
    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and value.endswith('.safetensors'):
            names.add(Path(value.replace('\\', '/')).name)
    templates = {'image': G.PICTURE_TEMPLATE, 'video': G.VIDEO_TEMPLATE, 'tts': 'h3_tts32_api.json'}
    for job in manifest['jobs']:
        graph = G.load_template(templates[job['type']], [HERE / 'workflows'])
        if job['type'] == 'video' and job.get('intensity', 'off') == 'off':
            for node_id in G.LORAS_ACTION:
                graph.pop(node_id, None)
        visit(graph)
    result = [model for model in lock['models'] if Path(model['filename']).name in names]
    if {Path(model['filename']).name for model in result} != names:
        raise ValidationError('A template selects a model absent from the pinned bundle')
    return result


class Runner:
    def __init__(self, job_dir, manifest_sha256=None, *, transport=None, now=time.time):
        self.root = Path(job_dir)
        self.now = now
        raw = (self.root / 'manifest.json').read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValidationError('Manifest exceeds 256 KiB')
        self.digest = hashlib.sha256(raw).hexdigest()
        if manifest_sha256 is not None and self.digest != manifest_sha256:
            raise ValidationError('Manifest digest mismatch')
        self.manifest = validate_manifest(json.loads(raw), self.root)
        if self.root.name != self.manifest['job_id']:
            raise ValidationError('Job directory identity does not match manifest')
        self.deadline = self.manifest['deadline_epoch']
        self.transport = transport or self.http
        self.state_path = self.root / 'status.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            'schema_version': 1, 'job_id': self.manifest['job_id'], 'manifest_sha256': self.digest,
            'phase': 'starting', 'done': False, 'jobs': {}, 'started': self.now()}
        if self.state.get('job_id') != self.manifest['job_id'] or self.state.get('manifest_sha256') != self.digest:
            raise ValidationError('Saved job identity differs from manifest')
        self.current = None
        self.diagnostic_stage = 'bootstrap'
        self.comfy = self.root / 'runtime' / 'ComfyUI'
        self.comfy_url = 'http://127.0.0.1:8188'
        self.python = self.root / 'runtime' / 'venv' / 'bin' / 'python'
        self.lock = json.loads((HERE / 'runtime.lock.json').read_text())

    def save(self, phase=None, **updates):
        if phase:
            self.state['phase'] = phase
        self.state.update(updates, updated=self.now())
        atomic_json(self.state_path, self.state)

    def check_deadline(self):
        if (self.root / 'cancel.requested').exists():
            raise Cancelled('Cancellation requested')
        if self.now() >= self.deadline:
            raise Cancelled('Runtime deadline reached')

    def http(self, route, body=None):
        self.check_deadline()
        request = urllib.request.Request(self.comfy_url + route,
                                        data=json.dumps(body).encode() if body is not None else None,
                                        headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=min(30, max(1, self.deadline - self.now()))) as response:
                data = response.read(32 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('ComfyUI request rejected with HTTP ' + str(exc.code)) from None
        if len(data) > 32 * 1024 * 1024:
            raise RuntimeError('ComfyUI response exceeds limit')
        return json.loads(data) if data else {}

    def reconcile(self, submission):
        """Find an owned request in history/queue; absence never authorizes retry."""
        candidates = []
        history = self.transport('/history?max_items=256')
        if isinstance(history, dict):
            candidates += [v.get('prompt') for v in history.values() if isinstance(v, dict)]
        queue = self.transport('/queue')
        candidates += queue.get('queue_running', []) + queue.get('queue_pending', [])
        matches = set()
        for item in candidates:
            if not isinstance(item, list) or len(item) < 4 or not isinstance(item[3], dict):
                continue
            identity = item[3].get('web_comfyui', {})
            if identity == submission['identity']:
                if self.graph_digest(item[2]) != submission['graph_sha256']:
                    raise SubmissionUncertain('Owned request identity has a different graph')
                matches.add(item[1])
        if len(matches) == 1:
            return next(iter(matches))
        raise SubmissionUncertain('Submission cannot be reconciled; automatic resubmission is disabled')

    @staticmethod
    def graph_digest(graph):
        return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def queue(self, graph):
        self.check_deadline()
        errors = G.check_graph(graph)
        if errors:
            raise ValidationError('Graph structure failed validation')
        job = self.state['jobs'][self.current]
        digest = self.graph_digest(graph)
        submission = job.get('submission')
        if submission:
            if digest != submission['graph_sha256']:
                raise SubmissionUncertain('Rebuilt graph differs from the persisted submission')
            if submission.get('prompt_id'):
                return submission['prompt_id']
            try:
                pid = self.reconcile(submission)
            except Exception as exc:
                self.save('submission_uncertain', error='Prompt submission needs reconciliation')
                raise SubmissionUncertain('Prompt submission needs reconciliation') from exc
            submission['prompt_id'] = pid
            self.save('running')
            return pid
        identity = {'job_id': self.manifest['job_id'], 'subjob_id': self.current,
                    'manifest_sha256': self.digest,
                    'request_id': str(uuid.uuid5(uuid.UUID(self.manifest['job_id']), self.current))}
        inventory = self.transport('/object_info')
        if not isinstance(inventory, dict) or any(n['class_type'] not in inventory for n in graph.values()):
            raise ValidationError('The runtime is missing a required graph node type')
        submission = {'identity': identity, 'graph_sha256': digest, 'intent_at': self.now()}
        job['submission'] = submission
        atomic_json(self.root / 'work' / (self.current + '.prompt.json'), graph)
        self.save('running')  # durable intent BEFORE any HTTP write
        try:
            result = self.transport('/prompt', {'prompt': graph, 'client_id': self.manifest['job_id'],
                                                'extra_data': {'web_comfyui': identity}})
            pid = result.get('prompt_id')
            if not isinstance(pid, str) or not pid or len(pid) > 128:
                raise ValueError('No prompt identity')
        except Exception as exc:
            self.save('submission_uncertain', error='Prompt acceptance is unknown; resubmission disabled')
            raise SubmissionUncertain('Prompt acceptance is unknown') from exc
        submission['prompt_id'] = pid
        submission['accepted_at'] = self.now()
        self.save('running')  # prompt identity is durable BEFORE polling
        return pid

    def wait_history(self, pid, timeout=2400, what=''):
        end = min(self.deadline, self.now() + timeout)
        while self.now() < end:
            self.check_deadline()
            try:
                entry = self.transport('/history/' + urllib.parse.quote(pid, safe='')).get(pid)
            except Cancelled:
                raise
            except Exception as exc:
                raise SubmissionUncertain('Monitoring unavailable for an accepted prompt; reconcile its saved ID') from exc
            if entry:
                status = entry.get('status', {})
                if status.get('status_str') == 'error':
                    raise RuntimeError('ComfyUI graph execution failed')
                if status.get('completed') is True:
                    return entry
            time.sleep(2)
        raise Cancelled('Generation polling deadline reached')

    def command(self, args, *, stage=None, **kwargs):
        if stage is not None:
            self.diagnostic_stage = stage
        self.check_deadline()
        with (self.root / 'runtime.log').open('ab') as log:
            return subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=True,
                                  timeout=max(1, self.deadline - self.now()), **kwargs)

    def provision(self):
        self.save('installing')
        self.diagnostic_stage = 'templates'
        # Reject an incomplete/corrupt upload before installing or downloading.
        for item in self.lock['templates']:
            if file_digest(HERE / item['path']) != item['sha256']:
                raise ValidationError('Bundled template integrity check failed')
        for source in self.lock['git']:
            self.diagnostic_stage = 'source_checkout'
            dest = contained(self.root / 'runtime', source['directory'])
            if not (dest / '.git').is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                self.command(['git', 'init', str(dest)])
                self.command(['git', '-C', str(dest), 'remote', 'add', 'origin', source['url']])
                self.command(['git', '-C', str(dest), 'fetch', '--depth', '1', 'origin', source['revision']])
                self.command(['git', '-C', str(dest), 'checkout', '--detach', source['revision']])
            rev = subprocess.check_output(['git', '-C', str(dest), 'rev-parse', 'HEAD'], timeout=15).decode().strip()
            if rev != source['revision']:
                raise ValidationError('Runtime Git checkout differs from pinned revision')
        if not self.python.is_file():
            # Colab's system Python can omit ensurepip. pip >=22.3 can manage
            # this isolated interpreter without installing pip into it or
            # modifying the notebook's system packages.
            self.command([sys.executable, '-m', 'venv', '--without-pip', str(self.python.parents[1])], stage='venv')
        lock_digest = file_digest(HERE / 'runtime.lock.json')
        marker = self.root / 'runtime' / 'installed.json'
        if not marker.exists() or json.loads(marker.read_text()).get('lock_sha256') != lock_digest:
            packages = self.lock['python_packages']
            constraints = self.root / 'runtime' / 'constraints.txt'
            constraints.write_text('\n'.join(k + '==' + v for k, v in packages.items()) + '\n')
            pip = [sys.executable, '-m', 'pip', '--python', str(self.python)]
            self.command([*pip, 'install', '--index-url', self.lock['torch_index'],
                          *[k + '==' + packages[k] for k in ('torch', 'torchvision', 'torchaudio')]], stage='torch_install')
            self.command([*pip, 'install', '-c', str(constraints), '-r', str(self.comfy / 'requirements.txt'),
                          'huggingface_hub==' + packages['huggingface_hub'], 'hf_xet==' + packages['hf_xet']], stage='requirements_install')
            atomic_json(marker, {'lock_sha256': lock_digest})
        # Check in the venv interpreter, after installation; never reuse imported old torch.
        self.command([str(self.python), '-c',
                      'import torch; assert torch.cuda.is_available(), "No CUDA GPU"; '
                      'assert torch.cuda.get_device_capability(0)>=(8,0), "GPU too old"; '
                      'print(torch.__version__,torch.version.cuda,torch.cuda.get_device_name(0))'], stage='cuda_check')
        self.diagnostic_stage = 'model_inventory'
        needed = required_models(self.manifest, self.lock)
        remaining = sum(x['size'] for x in needed if not (self.comfy / x['destination']).is_file())
        if shutil.disk_usage(self.root).free < remaining + 8 * 1024**3:
            raise RuntimeError('Insufficient runtime disk space for the pinned model bundle')
        for model in needed:
            self.check_deadline()
            target = contained(self.comfy, model['destination'])
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file():
                # Passing JSON as argv avoids executing filename/repository strings as code.
                self.command([str(self.python), str(HERE / 'remote_job.py'), '--download-model',
                              json.dumps(model), '--destination', str(target), '--cache-dir', str(self.root / 'runtime' / 'hf')], stage='model_download')
            self.diagnostic_stage = 'model_verify'
            if target.stat().st_size != model['size'] or file_digest(target) != model['sha256']:
                raise RuntimeError('Pinned model integrity check failed')

    def start_comfy(self):
        self.diagnostic_stage = 'comfy_start'
        existing = self.state.get('comfy_pid')
        if existing:
            try:
                cmdline = Path('/proc') / str(existing) / 'cmdline'
                if str(self.comfy).encode() not in cmdline.read_bytes():
                    raise RuntimeError('ComfyUI process ownership cannot be verified')
                self.transport('/system_stats')
                return
            except (FileNotFoundError, ProcessLookupError):
                if any(x.get('submission') for x in self.state['jobs'].values()):
                    raise SubmissionUncertain('ComfyUI disappeared after submission; do not regenerate')
        try:
            self.transport('/system_stats')
        except (OSError, RuntimeError):
            pass
        else:
            raise RuntimeError('Port 8188 already has an unowned ComfyUI service')
        with (self.root / 'comfyui.log').open('ab') as log:
            process = subprocess.Popen([str(self.python), str(self.comfy / 'main.py'), '--listen', '127.0.0.1', '--port', '8188'],
                                       cwd=self.comfy, stdout=log, stderr=subprocess.STDOUT)
        self.state['comfy_pid'] = process.pid
        self.save('starting')
        for _ in range(180):
            self.check_deadline()
            if process.poll() is not None:
                raise RuntimeError('Pinned ComfyUI process exited during startup')
            try:
                self.transport('/system_stats')
                break
            except (OSError, RuntimeError):
                time.sleep(2)
        else:
            raise RuntimeError('ComfyUI startup timed out')
        (self.comfy / 'input').mkdir(exist_ok=True)

    def configure_helpers(self):
        U.JOB, U.OUT, U.WORK, U.COMFY = self.root, self.root / 'out', self.root / 'work', self.comfy
        U.STATUS, U.STATE, U.STAGED = self.state_path, self.state, {}
        U.LOCAL_TEST = True  # provisioning is exclusively owned by this wrapper
        U.COMFY_URL = self.comfy_url
        U.TEMPLATE_DIRS = [HERE / 'workflows']
        U.save = self.save
        U.log = lambda message, stage=None: self.save()  # don't expose upstream errors/prompts in public status
        U.queue = self.queue
        U.wait_history = self.wait_history
        U.comfy_input = lambda: self.comfy / 'input'
        U.source_path = self.source_path
        def extend_source(job):
            ref = job['extend']
            if not ref.startswith('@') and ref in self.state['jobs']:
                ref = '@' + ref
            item = U.stage(self.source_path(ref))
            if item['kind'] != 'video':
                raise ValidationError('Extension input must be video')
            return item
        U.extend_source = extend_source
        U.comfy_file = self.comfy_file
        def call(url, body=None, **kwargs):
            route = url.removeprefix(U.COMFY_URL)
            if route == '/prompt':
                return {'prompt_id': self.queue(body['prompt'])}
            return self.transport(route, body)
        U.call = call
        # TTS previously had a private direct POST/poll loop. Use the same durable path.
        U.run_tts = self.run_tts
        U.RUNNERS['tts'] = self.run_tts

    def source_path(self, ref):
        if ref.startswith('@'):
            job = self.state['jobs'].get(ref[1:], {})
            if job.get('status') != 'done':
                raise ValidationError('Earlier output is not complete')
            path = contained(self.root, 'out/' + job['file'], exists=True)
            if file_digest(path) != job['sha256'] or path.stat().st_size != job['size']:
                raise ValidationError('Earlier output integrity check failed')
            return path
        return contained(self.root, ref, exists=True)

    def comfy_file(self, record):
        if record.get('type', 'output') != 'output':
            raise ValidationError('Expected an output artifact')
        filename = record.get('filename')
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValidationError('Unsafe output filename')
        return contained(self.comfy / 'output', str(Path(record.get('subfolder') or '') / filename), exists=True)

    def run_tts(self, job):
        graph = G.load_template('h3_tts32_api.json', [HERE / 'workflows'])
        graph['7']['inputs']['prompt'] = job['prompt']
        graph['56']['inputs']['value'] = int(job['seconds'])
        graph['54']['inputs']['seed'] = job['seed']
        for i, ref in enumerate(job.get('voices', [])):
            item = U.stage(self.source_path(ref))
            graph[f'ra{i}'] = {'class_type': 'LoadAudio', 'inputs': {'audio': item['name']}}
            graph['7']['inputs'][f'ref_audios.ref_audio_{i}'] = [f'ra{i}', 0]
        pid = self.queue(graph)
        entry = self.wait_history(pid, 900, job['id'])
        for value in entry.get('outputs', {}).values():
            if value.get('audio'):
                return self.comfy_file(value['audio'][0]), {'seed': job['seed']}
        raise RuntimeError('ComfyUI completed without an audio artifact')

    def collect(self, ident, source, kind, seed):
        source = Path(source)
        if source.is_symlink() or not source.resolve().is_relative_to(self.root.resolve()):
            raise ValidationError('Artifact is outside this job directory')
        suffix = source.suffix.lower()
        extensions = IMAGE if kind == 'image' else VIDEO if kind == 'video' else AUDIO
        size = source.stat().st_size
        if suffix not in extensions or not 0 < size <= MAX_OUTPUT_BYTES:
            raise ValidationError('Invalid or oversized output artifact')
        target = contained(self.root, 'out/' + ident + suffix)
        target.parent.mkdir(exist_ok=True)
        if source != target:
            temporary = target.with_suffix(target.suffix + '.tmp')
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        result = {'path': target.relative_to(self.root).as_posix(), 'size': size, 'sha256': file_digest(target),
                  'kind': 'audio' if kind == 'tts' else kind, 'seed': seed,
                  'prompt_id': self.state['jobs'][ident]['submission']['prompt_id']}
        return result

    def cancel_owned(self):
        """Only a dedicated, process-owned ComfyUI may be interrupted."""
        try:
            pid = self.state.get('comfy_pid')
            if not pid or str(self.comfy).encode() not in (Path('/proc') / str(pid) / 'cmdline').read_bytes():
                return
            # Deadline may be elapsed; kill the exclusively owned process. No unowned endpoint is interrupted.
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    def execute(self):
        self.check_deadline()
        if self.deadline - self.now() > 24 * 3600:
            raise ValidationError('Runtime deadline exceeds 24 hours')
        if self.state['phase'] in TERMINAL:
            return self.state
        for job in self.manifest['jobs']:
            self.state['jobs'].setdefault(job['id'], {'type': job['type'], 'status': 'queued'})
        self.save()
        self.provision()
        self.start_comfy()
        self.configure_helpers()
        artifacts = []
        for job in self.manifest['jobs']:
            self.check_deadline()
            self.current = job['id']
            record = self.state['jobs'][self.current]
            if record.get('status') == 'done':
                source = self.source_path('@' + self.current)
                artifacts.append(self.collect(self.current, source, job['type'], job['seed']))
                continue
            record.update(status='running', seed=job['seed'])
            self.save('running')
            try:
                self.diagnostic_stage = 'generation'
                source, info = U.RUNNERS[job['type']](job)
                if info.get('warning'):
                    raise RuntimeError('Extension did not produce the complete joined video')
                self.diagnostic_stage = 'artifact_validation'
                artifact = self.collect(self.current, source, job['type'], job['seed'])
                artifacts.append(artifact)
                record.update(status='done', file=Path(artifact['path']).name, size=artifact['size'], sha256=artifact['sha256'])
            except (SubmissionUncertain, Cancelled):
                raise
            except Exception as error:
                traceback.print_exc()
                record.update(status='error', error='Subjob failed; inspect private runtime logs')
                self.save('running', diagnostics=failure_diagnostics(error, self.diagnostic_stage))
                if self.manifest['stop_on_error']:
                    break
            self.save('running')
        atomic_json(self.root / 'artifacts.json', {'schema_version': 1, 'job_id': self.manifest['job_id'],
                    'manifest_sha256': self.digest, 'artifacts': artifacts})
        failed = any(x.get('status') != 'done' for x in self.state['jobs'].values())
        self.save('failed' if failed else 'completed', done=True, finished=self.now(), artifact_count=len(artifacts))
        return self.state


def watchdog(job_dir, deadline, runner_pid):
    """Best-effort runtime-side fallback; parent still owns verified release."""
    root = Path(job_dir)
    while time.time() < deadline and not (root / 'cancel.requested').exists():
        time.sleep(min(15, max(.1, deadline - time.time())))
    (root / 'cancel.requested').touch()
    try:
        # Isolate a possibly blocking notebook RPC and bound its duration.
        subprocess.run([sys.executable, '-c', 'from google.colab import runtime; runtime.unassign()'],
                       timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.getpgid(runner_pid) == runner_pid:
            os.killpg(runner_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def run_job(job_dir, manifest_sha256=None):
    """Run under the durable lock; kept separate for real offline subprocess tests."""
    root = Path(job_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.runner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            digest = file_digest(root / 'manifest.json')
            if manifest_sha256 and digest != manifest_sha256:
                raise ValidationError('Manifest digest differs from launch request')
            state_path = root / 'status.json'
            current = json.loads(state_path.read_text()) if state_path.exists() else {}
            if current.get('manifest_sha256', digest) != digest:
                raise ValidationError('Running worker has a different manifest')
            print('WEB_COMFYUI_JOB ' + json.dumps({'job_id': root.name, 'manifest_sha256': digest, 'already_running': True}))
            return 0
        try:
            runner = Runner(root, manifest_sha256)
            runner.save()
        except Exception as error:
            traceback.print_exc()
            # The launch hash binds even a broken/missing manifest to the paid
            # assignment. Never overwrite state belonging to another launch.
            state_path = root / 'status.json'
            if state_path.exists():
                try:
                    existing = json.loads(state_path.read_text())
                except (ValueError, OSError):
                    existing = {}
                if not isinstance(existing, dict):
                    existing = {}
                if existing and (existing.get('job_id') != root.name or
                                 existing.get('manifest_sha256') != manifest_sha256):
                    return 1
            digest = manifest_sha256
            if digest is None and (root / 'manifest.json').is_file():
                digest = file_digest(root / 'manifest.json')
            state = {'schema_version': 1, 'job_id': root.name, 'manifest_sha256': digest,
                     'phase': 'failed', 'done': True, 'jobs': {}, 'updated': time.time(),
                     'error': 'Remote worker bootstrap failed; inspect private runtime logs',
                     'diagnostics': failure_diagnostics(error, 'bootstrap')}
            atomic_json(state_path, state)
            print('WEB_COMFYUI_JOB ' + json.dumps(state))
            return 1
        if runner.state['phase'] in TERMINAL:
            print('WEB_COMFYUI_JOB ' + json.dumps(runner.state))
            return 0
        try:
            # Parent launches this process detached. A separate watchdog survives its death.
            runner.diagnostic_stage = 'watchdog_start'
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--watchdog', '--job-dir', str(root),
                              '--deadline', str(runner.deadline), '--runner-pid', str(os.getpid())],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            runner.execute()
        except SubmissionUncertain as error:
            runner.save('submission_uncertain', done=False, error='Prompt acceptance requires reconciliation; automatic retry disabled',
                        diagnostics=failure_diagnostics(error, runner.diagnostic_stage))
        except Cancelled:
            runner.cancel_owned()
            runner.save('cancelled', done=True, error='Cancelled or runtime deadline reached')
        except Exception as error:
            traceback.print_exc()
            runner.cancel_owned()
            runner.save('failed', done=True, error='Remote execution failed; inspect private runtime logs',
                        diagnostics=failure_diagnostics(error, runner.diagnostic_stage))
        print('WEB_COMFYUI_JOB ' + json.dumps(runner.state))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--watchdog', action='store_true')
    parser.add_argument('--deadline', type=float)
    parser.add_argument('--runner-pid', type=int)
    parser.add_argument('--download-model')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    args = parser.parse_args(argv)
    if args.download_model:
        from huggingface_hub import hf_hub_download
        model = json.loads(args.download_model)
        source = hf_hub_download(repo_id=model['repository'], filename=model['filename'], revision=model['revision'],
                                 cache_dir=str(args.cache_dir), token=False)
        if Path(source).stat().st_size != model['size'] or file_digest(source) != model['sha256']:
            raise RuntimeError('Downloaded model integrity check failed')
        # Models and the HF cache share a per-job filesystem. A hard link avoids
        # doubling the 50+ GiB bundle; no mutable external model cache is trusted.
        os.link(Path(source).resolve(), args.destination)
        return 0
    if args.watchdog:
        watchdog(args.job_dir, args.deadline, args.runner_pid)
        return 0
    if args.job_dir is None or args.job_dir.parent != Path('/content/web-comfyui') or args.job_dir.is_symlink():
        parser.error('--job-dir must be /content/web-comfyui/<UUID>')
    if fcntl is None:
        parser.error('The remote worker requires Linux file locking')
    return run_job(args.job_dir, args.manifest_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
