"""Versioned, owner-scoped upload derivatives; canonical assets stay untouched.

The only qualified canvas here is AutoIP H3 480p landscape (864 x 480).
Unknown targets are not inferred from a display label. Old jobs without this
explicit policy never enter this module.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FIT_POLICY = 'contain-black-h3-480p-v1'
FIT_PROFILE_REVISION = 'autoip-0.4.0-options-v5-frame-fit'
LEGACY_PROFILE_REVISION = 'autoip-0.4.0-options-v4'
KNOWN_CANVASES = {('480p', '16:9'): (864, 480)}
_MAX_BYTES = 32 * 1024 * 1024
_SAFE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}')
_MIMES = {'PNG': ('image/png', '.png'), 'JPEG': ('image/jpeg', '.jpg'), 'WEBP': ('image/webp', '.webp')}


def default_geometry_root():
    configured = os.environ.get('CASTLE_AUTOIP_INPUT_GEOMETRY_ROOT')
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'castle-comfy-web' / 'autoip-input-geometry'


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValueError('input preparation file is invalid')
        content = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if len(content) != before.st_size or identity(before) != identity(after):
            raise ValueError('input preparation file changed')
        return content


def _publish(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.prepare-', delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read(path, max(_MAX_BYTES, len(content))) != content:
                raise ValueError('input preparation publication conflicts')
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class AutoIPInputGeometry:
    """Prepare once and reuse an exact source/policy-bound upload receipt.

    Call only after the worker's canonical ownership, rights and asset digest
    checks. The returned file is a transport derivative, never a replacement
    for an intent asset or a pipeline dependency hash.
    """

    def __init__(self, root=None):
        self.root = Path(root if root is not None else default_geometry_root()).absolute()

    def _directory(self, owner, request_id):
        if not all(isinstance(x, str) and _SAFE.fullmatch(x) for x in (owner, request_id)):
            raise ValueError('input preparation scope is invalid')
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('input preparation root cannot be a symlink')
        directory = self.root / _digest(owner) / _digest(request_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (self.root, directory.parent, directory):
            if path.is_symlink() or not path.is_dir():
                raise ValueError('input preparation directory changed')
            path.chmod(0o700)
        return directory

    def receipt(self, owner, request_id, field_name):
        if field_name not in {'first_frame', 'last_frame'}:
            raise ValueError('input preparation role is invalid')
        directory = self._directory(owner, request_id)
        return json.loads(_read(directory / (field_name + '.json'), 64 * 1024))

    def __call__(self, owner, request_id, asset, loaded, *, policy, resolution, aspect_ratio):
        from PIL import Image, ImageOps
        from .autoip import AutoIPFile

        if policy != FIT_POLICY or (resolution, aspect_ratio) not in KNOWN_CANVASES:
            raise ValueError('input preparation canvas is not qualified')
        if loaded.field_name not in {'first_frame', 'last_frame'} or asset.kind.value != 'image':
            raise ValueError('input preparation applies only to video frames')
        content = _read(Path(loaded.path), _MAX_BYTES)
        source_sha = hashlib.sha256(content).hexdigest()
        if source_sha != asset.sha256 or len(content) != asset.byte_size:
            raise ValueError('canonical source changed before preparation')
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in _MIMES or _MIMES[original.format][0] != asset.mime_type:
                raise ValueError('canonical image format changed')
            if not 0 < original.width * original.height <= 40_000_000 or getattr(original, 'n_frames', 1) != 1:
                raise ValueError('input preparation image bounds are invalid')
            orientation = original.getexif().get(274, 1)
            image = ImageOps.exif_transpose(original)
            image.load()
            width, height = image.size
            mime, suffix = _MIMES[original.format]
        target_width, target_height = KNOWN_CANVASES[(resolution, aspect_ratio)]
        identity = width * target_height == height * target_width and orientation == 1
        scale = min(target_width / width, target_height / height)
        inner = (max(1, min(target_width, round(width * scale))), max(1, min(target_height, round(height * scale))))
        offset = ((target_width - inner[0]) // 2, (target_height - inner[1]) // 2)
        binding = {
            'schema_version': 'castle.autoip-input-geometry.v1',
            'owner_user_id': owner, 'client_request_id': request_id, 'field_name': loaded.field_name,
            'policy': policy,
            'source': {'asset_id': asset.asset_id, 'sha256': source_sha, 'bytes': len(content),
                       'mime_type': asset.mime_type, 'width': width, 'height': height,
                       'exif_orientation': orientation, 'visual_class': asset.visual_class.value,
                       'character_ids': list(asset.character_ids)},
            'target': {'resolution': resolution, 'aspect_ratio': aspect_ratio, 'width': target_width, 'height': target_height},
            'transform': {'operation': 'identity' if identity else 'contain', 'crop': False,
                          'fill': '#000000', 'content_size': list((width, height) if identity else inner),
                          'offset': [0, 0] if identity else list(offset), 'resample': 'lanczos'},
        }
        directory = self._directory(owner, request_id)
        lock_fd = os.open(directory / '.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt_path = directory / (loaded.field_name + '.json')
            if receipt_path.exists() or receipt_path.is_symlink():
                receipt = json.loads(_read(receipt_path, 64 * 1024))
                if {k: v for k, v in receipt.items() if k != 'upload'} != binding:
                    raise ValueError('input preparation receipt binding changed')
                upload = receipt.get('upload')
                if (not isinstance(upload, dict) or set(upload) != {'filename', 'sha256', 'bytes', 'mime_type', 'width', 'height'}
                        or not re.fullmatch(r'upload-[0-9a-f]{64}\.(png|jpg|webp)', str(upload.get('filename', '')))):
                    raise ValueError('input preparation receipt is invalid')
                data = _read(directory / upload['filename'], _MAX_BYTES)
                if len(data) != upload['bytes'] or hashlib.sha256(data).hexdigest() != upload['sha256']:
                    raise ValueError('input preparation upload changed')
                expected_mime = mime if identity else 'image/png'
                expected_size = (width, height) if identity else (target_width, target_height)
                if (upload['mime_type'] != expected_mime or (upload['width'], upload['height']) != expected_size
                        or upload['filename'] != 'upload-' + upload['sha256'] + (suffix if identity else '.png')
                        or (identity and upload['sha256'] != source_sha)):
                    raise ValueError('input preparation upload metadata changed')
            else:
                if identity:
                    data = content
                    upload_size = (width, height)
                else:
                    # Black compositing prevents alpha from becoming an implicit
                    # provider-specific fill. Contain never crops the subject.
                    if image.mode in {'RGBA', 'LA'} or 'transparency' in image.info:
                        rgba = image.convert('RGBA')
                        rgb = Image.new('RGBA', rgba.size, (0, 0, 0, 255))
                        rgb.alpha_composite(rgba)
                        image = rgb.convert('RGB')
                    else:
                        image = image.convert('RGB')
                    canvas = Image.new('RGB', (target_width, target_height), 'black')
                    canvas.paste(image.resize(inner, Image.Resampling.LANCZOS), offset)
                    output = io.BytesIO()
                    canvas.save(output, format='PNG', optimize=False, compress_level=6)
                    data = output.getvalue()
                    mime, suffix = 'image/png', '.png'
                    upload_size = (target_width, target_height)
                if not 0 < len(data) <= _MAX_BYTES:
                    raise ValueError('prepared upload exceeds image limit')
                upload_sha = hashlib.sha256(data).hexdigest()
                upload = {'filename': 'upload-' + upload_sha + suffix, 'sha256': upload_sha, 'bytes': len(data),
                          'mime_type': mime, 'width': upload_size[0], 'height': upload_size[1]}
                _publish(directory / upload['filename'], data)
                receipt = {**binding, 'upload': upload}
                _publish(receipt_path, (json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n').encode())
            return AutoIPFile(loaded.field_name, directory / upload['filename'], asset.visual_class, upload['mime_type'])
