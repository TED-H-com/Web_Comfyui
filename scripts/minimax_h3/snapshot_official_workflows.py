#!/usr/bin/env python3
"""Fetch immutable official MiniMax H3 workflow JSON snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from artifacts import ArtifactError, ensure_artifact, load_manifest
else:
    from .artifacts import ArtifactError, ensure_artifact, load_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/workflows.lock.json"
DEFAULT_DESTINATION = REPO_ROOT / "workflows/minimax_h3/official"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch official Comfy-Org MiniMax H3 workflows at a pinned commit."
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--destination", default=str(DEFAULT_DESTINATION))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def _validate_workflow(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid workflow JSON {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise ArtifactError(f"workflow is missing its nodes array: {path}")


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(Path(args.manifest).expanduser())
        destination_root = Path(args.destination).expanduser()
        print(
            f"Official MiniMax H3 workflows: {manifest.repository}@{manifest.revision}"
        )
        for artifact in manifest.artifacts:
            status = ensure_artifact(
                artifact,
                root=destination_root,
                url=manifest.url_for(artifact),
                verify_only=args.verify_only,
                timeout=args.timeout,
            )
            destination = destination_root / artifact.destination
            _validate_workflow(destination)
            print(f"[ok] {status}: {destination}")
    except (ArtifactError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
