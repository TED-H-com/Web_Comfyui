#!/usr/bin/env python3
"""Download the official Qwen Image 2.1 INT8 set; verify bytes and SHA256.

Uses the shared resumable range downloader. Dry-run unless --execute is given.
Models stay in the existing ComfyUI model directories, outside this repository.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from minimax_h3.download_pinned_ranges import DownloadSpec, RangeDownloader


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--models-dir", type=Path, default=Path.home() / "comfyui/models")
    args = parser.parse_args()
    lock = json.loads((ROOT / "manifests/qwen_image21/models.lock.json").read_text())
    specs = [DownloadSpec(
        url=f"https://huggingface.co/{lock['repository']}/resolve/{lock['revision']}/{model['path']}",
        destination=args.models_dir.resolve() / model["path"],
        expected_bytes=model["bytes"],
        sha256=model["sha256"],
        lanes=4,
    ) for model in lock["models"]]
    if not args.execute:
        for spec in specs:
            print(json.dumps(RangeDownloader(spec).dry_run(), ensure_ascii=False))
        return 0
    failures = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for spec in specs:
            print(f"Downloading {spec.destination.name}: {spec.expected_bytes:,} bytes", flush=True)
            futures[pool.submit(RangeDownloader(spec).execute)] = spec
        for future in as_completed(futures):
            spec = futures[future]
            try:
                report = future.result()
                print(json.dumps({"file": spec.destination.name, "status": report["status"],
                                  "sha256": spec.sha256}), flush=True)
            except Exception as error:
                failures += 1
                print(json.dumps({"file": spec.destination.name, "error": str(error)}), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
