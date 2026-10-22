#!/usr/bin/env python3
"""Preflight or run the dormant Novel Scene Art Castle worker."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"
if str(WEBUI) not in sys.path:
    sys.path.insert(0, str(WEBUI))

from novel_scene_art.errors import SceneArtError  # noqa: E402
from novel_scene_art.screening import UnavailableScreener  # noqa: E402
from novel_scene_art.worker import (  # noqa: E402
    SERVER_V2_PAYLOAD_HASH_PROFILE,
    WorkerSettings,
    build_production_worker,
    preflight,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--preflight", action="store_true")
    commands.add_argument("--once", action="store_true")
    commands.add_argument("--serve", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        settings = WorkerSettings.from_environment()
        screening = UnavailableScreener()
        report = preflight(
            settings,
            screener=screening,
            payload_hash_profile=SERVER_V2_PAYLOAD_HASH_PROFILE,
        )
        if args.preflight:
            print(json.dumps(report.public_dict(), sort_keys=True, separators=(",", ":")))
            return 0 if report.ready else 3
        # This stays unreachable while the approved release and qualified
        # output screener are deliberately absent.
        worker = build_production_worker(settings, screener=screening)
        if args.once:
            print(json.dumps({"status": worker.run_once()}, separators=(",", ":")))
            return 0
        while True:
            status = worker.run_once()
            if status in {"idle", "uncertain", "failure_pending", "delivery_pending"}:
                time.sleep(settings.idle_poll_seconds)
    except SceneArtError as error:
        print(
            json.dumps(
                {"error": error.code, "message": str(error)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 3 if error.definite_pre_submit else 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
