#!/usr/bin/env python3
"""Download pinned MiniMax H3 baseline models into a ComfyUI models tree."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from artifacts import ArtifactError, ensure_artifact, expected_bytes, load_manifest, select_family
    from authorization import (
        default_authorization_record_path,
        evaluate_model_download_authorization,
    )
else:
    from .artifacts import (
        ArtifactError,
        ensure_artifact,
        expected_bytes,
        load_manifest,
        select_family,
    )
    from .authorization import (
        default_authorization_record_path,
        evaluate_model_download_authorization,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/models.lock.json"


def _human_bytes(value: int) -> str:
    return f"{value / (1024 ** 3):.2f} GiB"


def _progress_reporter(label: str):
    last_bucket = -1

    def report(received: int, total: int) -> None:
        nonlocal last_bucket
        bucket = min(20, int(received * 20 / total))
        if bucket != last_bucket:
            last_bucket = bucket
            print(
                f"[..] {label}: {bucket * 5:3d}% "
                f"({_human_bytes(received)} / {_human_bytes(total)})",
                flush=True,
            )

    return report


def build_parser() -> argparse.ArgumentParser:
    default_models_dir = os.environ.get(
        "CASTLE_COMFY_MODELS_DIR", str(Path.home() / "comfyui/models")
    )
    parser = argparse.ArgumentParser(
        description=(
            "Download MiniMax H3 artifacts from an immutable lock manifest. "
            "A final model file is never overwritten."
        )
    )
    parser.add_argument("--family", choices=("fl", "ref", "all"), default="fl")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="DESTINATION",
        help=(
            "download/verify only this exact destination from the selected family; "
            "repeat to define a crash-resumable download order"
        ),
    )
    parser.add_argument("--models-dir", default=default_models_dir)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--authorization-record",
        default=str(default_authorization_record_path()),
        help=(
            "owner-only authorization record (must be outside the repository; "
            "ignored by --verify-only)"
        ),
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="hash selected local files without downloading or creating directories",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(Path(args.manifest).expanduser())
        selected = select_family(manifest.artifacts, args.family)
        if args.artifact:
            requested = list(args.artifact)
            if len(requested) != len(set(requested)):
                raise ArtifactError("duplicate --artifact destination")
            available = {artifact.destination: artifact for artifact in selected}
            unknown = [destination for destination in requested if destination not in available]
            if unknown:
                raise ArtifactError(
                    "artifact is not in the selected family: " + ", ".join(unknown)
                )
            selected = tuple(available[destination] for destination in requested)
        if not args.verify_only:
            decision = evaluate_model_download_authorization(
                args.authorization_record,
                manifest=manifest,
                family=args.family,
            )
            if not decision.authorized:
                print(
                    f"status={decision.status} reason={decision.reason}",
                    file=sys.stderr,
                )
                return 3
        models_dir = Path(args.models_dir).expanduser()
        print(
            f"MiniMax H3 {args.family}: {len(selected)} files, "
            f"{_human_bytes(expected_bytes(selected))} pinned at {manifest.revision}"
        )
        for artifact in selected:
            destination = models_dir / artifact.destination
            status = ensure_artifact(
                artifact,
                root=models_dir,
                url=manifest.url_for(artifact),
                verify_only=args.verify_only,
                timeout=args.timeout,
                progress=None
                if args.verify_only
                else _progress_reporter(artifact.destination),
            )
            print(f"[ok] {status}: {destination}", flush=True)
    except (ArtifactError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
