#!/usr/bin/env python3
"""Fetch immutable RunningHub exports linked by Servasyy's MiniMax H3 article.

The endpoint returns the browser workflow JSON directly.  A response is only
published when its byte length and SHA-256 match the reviewed snapshot, so a
future edit on RunningHub cannot silently rewrite tutorial provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
DESTINATION = REPO_ROOT / "workflows/minimax_h3/community/servasyy_article"
EXPORT_URL = "https://www.runninghub.ai/api/workflow/export"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

SOURCES = (
    {
        "task": "r2v",
        "workflow_id": "2084127823018946561",
        "filename": "h3-r2v-runninghub-2084127823018946561.json",
        "bytes": 46387,
        "sha256": "8ac489422009fab42c720c402c3f4c859aa8a8ec5b28556f2162fd49eae8ef3f",
    },
    {
        "task": "i2v-flf",
        "workflow_id": "2084620086811410433",
        "filename": "h3-i2v-flf-runninghub-2084620086811410433.json",
        "bytes": 51567,
        "sha256": "a49039b0ebaaa4f19cd6509b7b5ba129ac220b4dafe35df8b9c7499a225c7222",
    },
    {
        "task": "t2v",
        "workflow_id": "2084626786919878657",
        "filename": "h3-t2v-runninghub-2084626786919878657.json",
        "bytes": 34648,
        "sha256": "8b190846243edfc58ab45b82dc73a73d9163e98cf200168a602555b847059d82",
    },
)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate(payload: bytes, source: dict[str, object]) -> None:
    expected_bytes = int(source["bytes"])
    expected_hash = str(source["sha256"])
    if len(payload) != expected_bytes:
        raise RuntimeError(
            f"{source['task']}: expected {expected_bytes} bytes, got {len(payload)}"
        )
    actual_hash = digest(payload)
    if actual_hash != expected_hash:
        raise RuntimeError(
            f"{source['task']}: expected SHA-256 {expected_hash}, got {actual_hash}"
        )
    workflow = json.loads(payload)
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
        raise RuntimeError(f"{source['task']}: response is not a Comfy workflow")
    if not isinstance(workflow.get("links"), list):
        raise RuntimeError(f"{source['task']}: workflow links are missing")


def fetch(source: dict[str, object]) -> bytes:
    body = json.dumps(
        {"workflowId": str(source["workflow_id"])}, separators=(",", ":")
    ).encode("utf-8")
    request = Request(
        EXPORT_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "web-comfyui-provenance/1",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"{source['task']}: response exceeds safety limit")
    validate(payload, source)
    return payload


def publish(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"refusing to overwrite changed source snapshot: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def main() -> int:
    for source in SOURCES:
        payload = fetch(source)
        path = DESTINATION / str(source["filename"])
        publish(path, payload)
        print(
            f"{source['task']}: {path.relative_to(REPO_ROOT)} "
            f"{len(payload)} bytes sha256={digest(payload)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
