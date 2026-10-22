from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

from .config import atomic_write_private_text, ensure_private_dir
from .tasks import run_task


_CANCELLED = False


def process_start_time(pid: int | None = None) -> str:
    pid = pid or os.getpid()
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.split()[21]
    except Exception:
        return ""


def write_json(path: Path, data: dict) -> None:
    atomic_write_private_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2),
    )


def install_signal_handlers(cancel_file: Path) -> None:
    def handle(signum, _frame):
        global _CANCELLED
        _CANCELLED = True
        try:
            cancel_file.touch()
        except OSError:
            pass

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--sentinel", required=True)
    args = parser.parse_args()

    # Normalize ``..`` without resolving a leaf symlink.  ``ensure_private_dir``
    # must see the configured leaf itself so O_NOFOLLOW can reject it.
    work_dir = Path(os.path.abspath(args.work_dir))
    sentinel = Path(os.path.abspath(args.sentinel))
    ensure_private_dir(work_dir)
    if sentinel.parent != work_dir:
        raise ValueError("sentinel must stay inside the task work directory")
    cancel_file = work_dir / "cancel"
    install_signal_handlers(cancel_file)
    started = {
        "task_id": args.task_id,
        "task_type": args.task_type,
        "attempt": args.attempt,
        "pid": os.getpid(),
        "pgid": os.getpgrp(),
        "marker": args.marker,
        "process_start_time": process_start_time(),
        "started_at": time.time(),
    }
    write_json(work_dir / "started.json", started)
    try:
        payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        payload["_attempt"] = args.attempt
        outputs = run_task(args.task_type, payload, work_dir, cancel_file)
        status = "cancelled" if _CANCELLED or cancel_file.exists() else "success"
        write_json(
            sentinel,
            {
                "status": status,
                "completed_at": time.time(),
                "outputs": [] if status == "cancelled" else outputs,
            },
        )
        return 130 if status == "cancelled" else 0
    except KeyboardInterrupt:
        write_json(
            sentinel,
            {
                "status": "cancelled",
                "completed_at": time.time(),
                "outputs": [],
            },
        )
        return 130
    except Exception as error:
        write_json(
            sentinel,
            {
                "status": "failed",
                "completed_at": time.time(),
                "error": str(error),
                "traceback": traceback.format_exc(limit=8),
                "outputs": [],
            },
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
