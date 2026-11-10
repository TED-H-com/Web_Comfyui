#!/usr/bin/env python3
"""Serialize local ComfyUI work, recycling idle caches before GPU admission.

Uses the fleet's existing flock. Never uses /free, never recycles a busy queue,
and never starts the child merely because VRAM became free during a restart.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minimax_h3 import run_native as guarded

DEFAULT_LOCK = Path.home() / '.cache/castle-gpu-queue/rtx5090.lock'


def gpu_memory():
    result = subprocess.run(
        ['nvidia-smi', '--query-gpu=memory.free,utilization.gpu', '--format=csv,noheader,nounits'],
        capture_output=True, text=True, check=True, timeout=10,
    )
    free, utilization = result.stdout.splitlines()[0].split(',')
    return int(free.strip()), int(utilization.strip())


def wait_until_ready(base_url, minimum_free, maximum_utilization, interval, *,
                     http=guarded.request_json, gpu=gpu_memory,
                     recycle=guarded.recycle_comfy_service, sleeper=time.sleep,
                     clock=time.monotonic):
    last_recycle = float('-inf')
    while True:
        try:
            queue = http(base_url, '/queue', timeout=5)
            guarded.require_queue_idle(queue)
            stats = http(base_url, '/system_stats', timeout=5)
            if not isinstance(stats.get('system'), dict):
                raise ValueError('ComfyUI runtime is not ready')
            free, utilization = gpu()
            if free >= minimum_free and utilization <= maximum_utilization:
                # Another direct ComfyUI caller can submit between reads.
                guarded.require_queue_idle(http(base_url, '/queue', timeout=5))
                return
            cached = sum(float(d.get('torch_vram_total', 0)) for d in stats.get('devices', []))
            if (free < minimum_free and utilization <= maximum_utilization
                    and cached >= 2048 * 1024**2 and clock() - last_recycle >= 300):
                last_recycle = clock()
                evidence = recycle(base_url)
                print(json.dumps({'event': 'idle_comfy_recycled', 'status': evidence.get('status')}), flush=True)
                # Recheck API readiness and actual free VRAM after the recycle.
                continue
        except (guarded.RunnerError, OSError, ValueError, subprocess.SubprocessError) as error:
            print('[comfy-gpu-queue] waiting: ' + getattr(error, 'code', type(error).__name__), flush=True)
        sleeper(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--min-free-mb', type=int, default=26000)
    parser.add_argument('--max-util', type=int, default=20)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--comfy-url', default=guarded.DEFAULT_COMFY_URL)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.min_free_mb < 0 or not 0 <= args.max_util <= 100 or not 0 < args.interval <= 300:
        parser.error('invalid admission settings or missing command')
    base_url = guarded.validate_loopback_url(args.comfy_url or guarded.DEFAULT_COMFY_URL)
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        wait_until_ready(base_url, args.min_free_mb, args.max_util, args.interval)
        print('[gpu-queue] running: guarded local worker', flush=True)
        return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
