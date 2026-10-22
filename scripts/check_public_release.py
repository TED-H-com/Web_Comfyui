#!/usr/bin/env python3
"""Fail publication on forbidden artifacts and recognizable credential formats.

This complements a secret scanner and human source review; it is not proof that
all sensitive information can be detected automatically. With Git initialized,
the index is the source of truth, including staged bytes rather than worktree bytes.
"""
from pathlib import Path
import argparse
import hashlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.public_demo_assets import PUBLIC_DEMO_ASSETS
FORBIDDEN_DIRS = {'characters', 'voices', 'user-saved', 'node_modules', '__pycache__', '.venv', 'memory', '.openclaw'}
FORBIDDEN_NAMES = {'users.json', '.auth.json', '.provenance.key', 'MEMORY.md', 'SOUL.md', 'USER.md', 'IDENTITY.md'}
FORBIDDEN_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.key', '.pem', '.log', '.pyc', '.safetensors', '.ckpt', '.gguf', '.pth', '.mp4', '.mp3', '.wav', '.png', '.jpg', '.jpeg', '.webp', '.zip'}
SECRETS = (
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{40,}\b'),
    re.compile(rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{40,}\b'),
    re.compile(rb'\baip_[A-Za-z0-9_-]{24,}\b'),
    re.compile(rb'\bhf_[A-Za-z0-9]{30,}\b'),
)


def files(root):
    if (root / '.git').exists():
        names = subprocess.check_output(['git','ls-files','--stage','-z'], cwd=root).split(b'\0')
        for record in filter(None, names):
            metadata, name = record.split(b'\t', 1)
            relative = name.decode()
            if metadata.split()[0] == b'120000':
                raise ValueError('Symlink source is not publishable: ' + relative)
            data = subprocess.check_output(['git', 'show', ':' + relative], cwd=root)
            yield Path(relative), data
    else:
        for path in root.rglob('*'):
            if path.is_symlink():
                raise ValueError('Symlink artifact is not publishable: ' + str(path.relative_to(root)))
            if path.is_file() and not set(path.relative_to(root).parts) & {'.git', '.venv', '__pycache__', '.pytest_cache'}:
                yield path.relative_to(root), path.read_bytes()


def inspect(relative, data):
    issues = []
    reviewed_media = (relative.parent.as_posix() in {'webui/docs/examples', 'assets/examples'}
        and relative.name in PUBLIC_DEMO_ASSETS
        and hashlib.sha256(data).hexdigest() == PUBLIC_DEMO_ASSETS[relative.name])
    if (set(relative.parts) & FORBIDDEN_DIRS or relative.name in FORBIDDEN_NAMES
        or (relative.suffix.lower() in FORBIDDEN_SUFFIXES and not reviewed_media)
        or (relative.name.startswith('.env') and relative.name != '.env.example')
        or '.before-' in relative.name or '.bak-' in relative.name):
        issues.append('runtime/private/binary artifact')
    if b'\x00' in data and not reviewed_media:
        issues.append('binary file')
    if any(pattern.search(data) for pattern in SECRETS):
        issues.append('credential-shaped value')
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    count = 0; failed = False
    try:
        for relative, data in files(args.root):
            count += 1
            for reason in inspect(relative, data):
                print(f'{relative}: {reason}'); failed = True
    except ValueError as error:
        print(str(error)); failed = True
    print(f'Public release check: {count} source files; ' + ('FAILED' if failed else 'passed'))
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
