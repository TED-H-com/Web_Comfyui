#!/usr/bin/env python3
"""Checkpoint owned completed Comfy jobs before the caller recycles its process.

The caller holds the shared lifecycle maintenance lock. Configuration comes
from the currently running Web instance's private local context, never from
caller-provided owner IDs, output paths, or provider credentials.
"""
from __future__ import annotations
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / 'webui'))
from comfy_checkpoint_context import read_checkpoint_context, loopback_url


def checkpoint(context, comfy_url, *, server_loader=None, checkpoint_fn=None):
    if loopback_url(comfy_url) != loopback_url(context['env']['COMFYUI_URL']):
        raise ValueError('Checkpoint runtime differs from the Web instance')
    os.environ.update(context['env'])
    source = Path(context['server_file'])
    sys.path[:0] = [str(source.parent), str(source.parent.parent)]
    if server_loader is None:
        def server_loader():
            spec = importlib.util.spec_from_file_location('castle_comfy_checkpoint_server', source)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    # Imports and existing helper warnings must not expose prompts or paths.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        server = server_loader()
        if loopback_url(server.DEFAULT_COMFY) != loopback_url(comfy_url):
            raise ValueError('Loaded runtime differs from checkpoint configuration')
        if checkpoint_fn is None:
            from compute_web import checkpoint_owned_history
            checkpoint_fn = checkpoint_owned_history
        history = server.request_json('/history')
        result = checkpoint_fn(server, history, receipt_root=Path(context['receipt_root']))
    if (not isinstance(result, dict) or set(result) != {'persisted', 'skipped'}
            or any(type(v) is not int or v < 0 for v in result.values())):
        raise ValueError('Checkpoint did not return verified counts')
    return {'ok': True, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy-url', required=True)
    args = parser.parse_args()
    try:
        context = read_checkpoint_context()
        loopback_url(args.comfy_url)
        if Path(sys.executable).resolve() != Path(context['python']).resolve():
            if os.environ.get('CASTLE_CHECKPOINT_REEXEC'):
                raise ValueError('Configured interpreter is unavailable')
            env = {**os.environ, **context['env'], 'CASTLE_CHECKPOINT_REEXEC': '1'}
            os.execve(context['python'], [context['python'], '-B', str(Path(__file__).resolve()),
                '--comfy-url', args.comfy_url], env)
        result = checkpoint(context, args.comfy_url)
    except Exception:
        print(json.dumps({'ok': False, 'error': 'checkpoint_failed'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
