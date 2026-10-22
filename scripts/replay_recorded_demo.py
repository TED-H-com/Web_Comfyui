#!/usr/bin/env python3
"""Validate a public recorded demo; submit to local ComfyUI only with --run.

Models and custom nodes must be installed separately. No environment variables,
credentials, runtime receipts or personal media are discovered by this script.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import ipaddress
import json
from pathlib import Path
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.public_demo_assets import PUBLIC_DEMO_ASSETS

# Reviewed classes used by the committed GPU recordings. Additional graph types
# require an explicit code review, even if someone updates a graph's checksum.
ALLOWED_NODES = frozenset('''
ApplyInstantID CLIPLoader CLIPTextEncode CLIPVisionEncode CLIPVisionLoader
CheckpointLoaderSimple ConditioningZeroOut ControlNetLoader DualCLIPLoader
EmptyLatentImage EmptySD3LatentImage FluxGuidance FluxKontextImageScale
GetImageSize IPAdapterAdvanced IPAdapterModelLoader InstantIDFaceAnalysis
InstantIDModelLoader KSampler LoadImage LoraLoaderModelOnly ModelSamplingSD3
ReferenceLatent ResizeImageMaskNode SAM3_VideoTrack SCAIL2ColoredMask SaveImage
UNETLoader VAEDecode VAEEncode VAELoader VHS_LoadVideo VHS_LoadVideoPath
VHS_VideoCombine WanContextWindowsManual WanImageToVideo WanSCAILToVideo
'''.split())


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def local_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'http' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use an HTTP loopback URL without credentials, query, or fragment')
    try:
        address = ipaddress.ip_address(parsed.hostname or '')
    except ValueError:
        raise ValueError('Use a numeric loopback address, such as http://127.0.0.1:8188') from None
    if not address.is_loopback:
        raise ValueError('ComfyUI must use a loopback address')
    return value.rstrip('/')


def load_record(run_id: str, examples: Path = ROOT / 'webui/docs/examples') -> tuple[dict, dict]:
    provenance = json.loads((examples / 'provenance.json').read_text())
    matches = [run for run in provenance.get('runs', []) if run['id'] == run_id]
    if len(matches) != 1:
        raise ValueError(f'Unknown or duplicate run ID: {run_id}')
    run = matches[0]
    if run['status'] != 'success':
        raise ValueError('Only successful recorded runs can be replayed')
    graph = run['workflow']['graph']
    canonical = json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if digest(canonical) != run['workflow']['sha256']:
        raise ValueError('Recorded graph hash mismatch')
    if not isinstance(graph, dict) or not graph:
        raise ValueError('Missing API graph')
    for node in graph.values():
        if not isinstance(node, dict) or node.get('class_type') not in ALLOWED_NODES:
            raise ValueError('Graph contains an unreviewed node class')
        if not isinstance(node.get('inputs'), dict):
            raise ValueError('Graph contains invalid node inputs')
    ledger = {row['file']: row for row in provenance['assets']}
    for row in run['inputs'] + run['outputs']:
        name = row['file']
        if Path(name).name != name or name not in PUBLIC_DEMO_ASSETS:
            raise ValueError('Asset is not in the public allowlist')
        source = examples / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Asset must be a regular, non-symlink file')
        checksum = digest(source.read_bytes())
        if checksum != PUBLIC_DEMO_ASSETS[name] or checksum != ledger[name]['sha256']:
            raise ValueError('Public asset hash mismatch')
        allowed_hashes = {checksum} if row in run['inputs'] else {checksum, ledger[name].get('source_sha256')}
        if row['sha256'] not in allowed_hashes:
            raise ValueError('Recorded source hash does not match the public ledger')
    return run, deepcopy(graph)


def prepare_inputs(run: dict, graph: dict, input_dir: Path,
                   examples: Path = ROOT / 'webui/docs/examples') -> dict:
    """Copy approved media under an explicitly supplied ComfyUI input directory."""
    input_dir = input_dir.expanduser().resolve(strict=True)
    if not input_dir.is_dir():
        raise ValueError('--input-dir must be an existing ComfyUI input directory')
    target = input_dir / 'web-demo-replay'
    if target.is_symlink():
        raise ValueError('Replay input subdirectory must not be a symlink')
    # Validate every input consumer before touching disk.
    mapping = {row['graph_name']: row['file'] for row in run['inputs']}
    rewrites = []
    seen = set()
    for node in graph.values():
        if not isinstance(node, dict) or not isinstance(node.get('inputs'), dict) or not node.get('class_type'):
            raise ValueError('Invalid ComfyUI API node')
        cls = node['class_type']
        field = {'LoadImage': 'image', 'VHS_LoadVideo': 'video', 'VHS_LoadVideoPath': 'video'}.get(cls)
        if field:
            value = node['inputs'].get(field)
            if value not in mapping:
                raise ValueError(f'Unrecorded {cls} input')
            name = mapping[value]
            relative = f'web-demo-replay/{name}'
            rewrites.append((node['inputs'], field,
                             str(target / name) if cls == 'VHS_LoadVideoPath' else relative))
            seen.add(value)
    if seen != set(mapping):
        raise ValueError('A recorded input has no supported graph loader')
    target.mkdir(exist_ok=True)
    for name in mapping.values():
        destination = target / name
        if destination.is_symlink():
            raise ValueError('Replay input file must not be a symlink')
        if destination.exists() and digest(destination.read_bytes()) != PUBLIC_DEMO_ASSETS[name]:
            raise ValueError('Refusing to overwrite an unrelated input file')
        if not destination.exists():
            # Exclusive creation avoids replacing an existing personal file.
            with destination.open('xb') as out, (examples / name).open('rb') as source:
                shutil.copyfileobj(source, out)
    for inputs, field, value in rewrites:
        inputs[field] = value
    return graph


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('ComfyUI redirects are not allowed')


def submit(url: str, graph: dict) -> dict:
    url = local_url(url)
    request = urllib.request.Request(url + '/prompt',
        data=json.dumps({'prompt': graph}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    # Ignore proxy environment variables; this request is local only.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', help='Stable run ID from examples/provenance.json')
    parser.add_argument('--comfy-url', default='http://127.0.0.1:8188')
    parser.add_argument('--input-dir', type=Path, help='Your local ComfyUI input directory (required for input-based runs)')
    parser.add_argument('--run', action='store_true', help='Explicitly copy approved inputs and enqueue GPU work')
    args = parser.parse_args(argv)
    try:
        url = local_url(args.comfy_url)
        run, graph = load_record(args.run_id)
        print(json.dumps({'run_id': run['id'], 'feature_id': run['feature_id'],
                          'nodes': len(graph), 'inputs': [row['file'] for row in run['inputs']],
                          'recorded_device': run['device'], 'submit': args.run}, ensure_ascii=False))
        if not args.run:
            print('Validated only. Add --run to enqueue; install the recorded models and custom nodes first.')
            return 0
        if run['inputs']:
            if args.input_dir is None:
                raise ValueError('--run requires --input-dir for this recorded run')
            graph = prepare_inputs(run, graph, args.input_dir)
        result = submit(url, graph)
        if not result.get('prompt_id') or result.get('node_errors'):
            raise ValueError(f'ComfyUI rejected the graph: {json.dumps(result)}')
        print(json.dumps({'prompt_id': result['prompt_id'], 'status': 'queued'}, ensure_ascii=False))
        print('Queued only; inspect ComfyUI for completion. Identical inputs and settings do not guarantee identical regenerated pixels.')
        return 0
    except (ValueError, KeyError, OSError, urllib.error.URLError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
