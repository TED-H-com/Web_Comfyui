#!/usr/bin/env python3
"""Qualify one exact-class MiniMax H3 I2V or Ref2VA image contract.

Dry-run is the default.  Execution requires an owner-controlled source image,
the current local-inference authorization, every pinned model byte, the live
native node ABI, Castle's shared GPU queue, and the same live-RAM/GPU gates as
the T2V qualification runner.  A successful run writes schema-v2 evidence for
``issue_golden.py``; it never issues a golden or exposes a product entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid
from typing import Any, Mapping, Sequence

from PIL import Image, UnidentifiedImageError


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
WEBUI_ROOT = REPO_ROOT / "webui"
for candidate in (str(HERE), str(REPO_ROOT), str(WEBUI_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import run_native as guarded  # noqa: E402
import run_provider_graph as provider_graph  # noqa: E402
from scripts.minimax_h3.artifacts import load_manifest  # noqa: E402
from scripts.minimax_h3.authorization import (  # noqa: E402
    evaluate_local_inference_authorization,
    manifest_scope_sha256,
)
from scripts.minimax_h3.issue_golden import (  # noqa: E402
    GoldenIssueError,
    validate_qualification,
)
from minimax_h3_product import character_variant, compile_product_prompt  # noqa: E402
from minimax_h3_readiness import H3_CONTRACTS, graph_contract_sha256  # noqa: E402


CONTRACT_IDS = tuple(
    contract_id
    for contract_id, contract in H3_CONTRACTS.items()
    if contract.mode in {"i2v", "ref2va_image"}
)
SPEC_SCHEMA = 1
RECEIPT_SCHEMA = 2
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
STATE_ROOT = guarded.DEFAULT_STATE_ROOT
SAFE_SUFFIXES = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"), "WEBP": (".webp", "image/webp")}
SPEC_KEYS = {
    "schema_version", "run_id", "created_at", "deadline_epoch", "contract_id",
    "prompt", "motion", "aspect", "quality", "seed", "source_path",
    "source_image", "upload_basename", "graph", "graph_sha256", "prompt_id",
    "comfy_dir", "models_dir", "output_dir", "authorization_record",
    "state_root", "cancel_file", "worker_hmac_sha256",
}


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise guarded.RunnerError("serialization_failed", "Qualification data is invalid.") from error


def _image_evidence(path_value: str | os.PathLike[str]) -> tuple[Path, dict[str, object], str]:
    path = Path(path_value).expanduser()
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
        info = lexical.lstat()
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise guarded.RunnerError("source_image_unavailable", "Source image is unavailable.") from error
    if (
        stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid() or info.st_nlink != 1
        or info.st_mode & 0o022 or not 0 < info.st_size <= MAX_SOURCE_BYTES
    ):
        raise guarded.RunnerError("source_image_unsafe", "Source image is not an owner-controlled regular file.")
    try:
        size, digest = provider_graph.stable_sha(resolved)
        with Image.open(resolved) as image:
            image.verify()
        with Image.open(resolved) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
    except (OSError, UnidentifiedImageError, provider_graph.PreSubmitError) as error:
        raise guarded.RunnerError("source_image_invalid", "Source image bytes are invalid.") from error
    suffix_and_mime = SAFE_SUFFIXES.get(image_format)
    if (
        suffix_and_mime is None or size != info.st_size
        or type(width) is not int or type(height) is not int
        or width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS
    ):
        raise guarded.RunnerError("source_image_invalid", "Source image format or dimensions are invalid.")
    suffix, mime = suffix_and_mime
    return resolved, {
        "kind": "", "bytes": size, "sha256": digest, "mime": mime,
        "width": width, "height": height,
    }, suffix


def _contract_parts(contract_id: str) -> tuple[Any, Any, str, str]:
    contract = H3_CONTRACTS.get(contract_id)
    if contract is None or contract.mode not in {"i2v", "ref2va_image"}:
        raise guarded.RunnerError("contract_invalid", "Character qualification contract is invalid.")
    action = "image-video" if contract.mode == "i2v" else "reference-video"
    variant = character_variant(contract.visual_class, action)
    if variant is None:
        raise guarded.RunnerError("contract_invalid", "Character qualification variant is unavailable.")
    feature = "h3-image-video" if contract.mode == "i2v" else "h3-reference-video"
    slot = "image" if contract.mode == "i2v" else "reference"
    return contract, variant, feature, slot


def build_contract(
    *, contract_id: str, source_path: str | os.PathLike[str], prompt: str,
    motion: str | None, aspect: str | None, quality: str, seed: int, run_id: str,
) -> tuple[dict[str, Any], Mapping[str, Mapping[str, object]]]:
    contract, variant, feature, slot = _contract_parts(contract_id)
    source, evidence, suffix = _image_evidence(source_path)
    clean_motion = motion or variant.motion_options[0]
    clean_aspect = aspect or variant.aspect_options[0]
    if clean_motion not in variant.motion_options or clean_aspect not in variant.aspect_options:
        raise guarded.RunnerError("option_invalid", "Motion or aspect is invalid for this visual class.")
    if quality != "quality" or guarded.SAFE_RUN_ID.fullmatch(run_id) is None:
        raise guarded.RunnerError("option_invalid", "Qualification quality or run identifier is invalid.")
    upload = f"castle_h3_{evidence['sha256'][:32]}{suffix}"
    output_prefix = f"Castle/minimax-h3/{contract.mode}/{run_id}"
    graph = compile_product_prompt(
        feature_id=feature,
        visual_class=contract.visual_class,
        settings={
            "description": prompt, "motionProfile": clean_motion,
            "aspect": clean_aspect, "quality": quality, "seed": seed,
        },
        uploads={slot: upload},
        output_token=run_id,
    )
    # The product compiler owns its namespace, but qualification receipts have
    # a stable mode/run namespace required by the offline issuer.
    graph["14"]["inputs"]["filename_prefix"] = output_prefix
    evidence["kind"] = "source_image" if contract.mode == "i2v" else "reference_image"
    request = {
        "contract_id": contract_id, "mode": contract.mode,
        "visual_class": contract.visual_class, "family": contract.family,
        "source_path": str(source), "source_image": evidence,
        "upload_basename": upload, "prompt": prompt,
        "motion": clean_motion, "aspect": clean_aspect, "quality": quality,
        "seed": seed, "profile": quality, "output_prefix": output_prefix,
        "graph_contract_sha256": graph_contract_sha256(contract_id),
    }
    return request, graph


def sanitized_summary(request: Mapping[str, Any], graph: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    generator = graph["5"]["inputs"]
    source = request["source_image"]
    classes: dict[str, int] = {}
    for node in graph.values():
        name = str(node.get("class_type") or "")
        classes[name] = classes.get(name, 0) + 1
    return {
        "schema_version": RECEIPT_SCHEMA, "action": "dry_run", "run_id": run_id,
        "contract_id": request["contract_id"], "mode": request["mode"],
        "visual_class": request["visual_class"], "family": request["family"],
        "profile": request["profile"], "motion": request["motion"], "aspect": request["aspect"],
        "resolution": {"width": generator["width"], "height": generator["height"]},
        "frames": generator["length"], "fps": guarded.native.STOCK_FPS,
        "seed": request["seed"],
        "prompt": {"sha256": hashlib.sha256(str(request["prompt"]).encode()).hexdigest()},
        "source_image": {key: source[key] for key in ("kind", "bytes", "sha256", "mime", "width", "height")},
        "graph": {
            "sha256": hashlib.sha256(_canonical(graph)).hexdigest(),
            "contract_sha256": request["graph_contract_sha256"],
            "node_count": len(graph), "class_counts": dict(sorted(classes.items())),
        },
        "output": {"namespace": f"Castle/minimax-h3/{request['mode']}", "container": "mp4", "audio_required": True},
    }


def _load_worker_spec(path: Path) -> dict[str, Any]:
    token = os.environ.pop("CASTLE_H3_CHARACTER_WORKER_TOKEN", "")
    state_value = os.environ.pop("CASTLE_H3_CHARACTER_STATE_ROOT", "")
    if not token or not token.isascii() or not state_value:
        raise guarded.RunnerError("worker_context_missing", "Character worker context is missing.")
    state_root = Path(state_value).resolve(strict=True)
    pending = state_root / "pending-character"
    resolved_path = Path(os.path.abspath(path))
    if resolved_path.parent != pending or resolved_path.suffix != ".json":
        raise guarded.RunnerError("worker_spec_invalid", "Character worker specification path is invalid.")
    raw = guarded._read_owner_file(path, max_bytes=2 * 1024 * 1024, code="worker_spec_invalid")
    value = guarded._safe_json_loads(raw, code="worker_spec_invalid")
    if not isinstance(value, dict) or set(value) != SPEC_KEYS or value.get("schema_version") != SPEC_SCHEMA:
        raise guarded.RunnerError("worker_spec_invalid", "Character worker specification is invalid.")
    signature = value.get("worker_hmac_sha256")
    expected = guarded._worker_spec_hmac(value, token)
    if not isinstance(signature, str) or not secrets.compare_digest(signature, expected):
        raise guarded.RunnerError("worker_spec_invalid", "Character worker specification is not authentic.")
    if str(value.get("state_root")) != str(state_root):
        raise guarded.RunnerError("worker_spec_invalid", "Character worker state root changed.")
    run_id = value.get("run_id")
    if resolved_path.name != f"{run_id}.json":
        raise guarded.RunnerError("worker_spec_invalid", "Character worker specification path changed.")
    expected_cancel = pending / f"{run_id}.cancel"
    if value.get("cancel_file") != str(expected_cancel):
        raise guarded.RunnerError("worker_spec_invalid", "Character worker cancellation path changed.")
    return value


def _write_character_receipt(state_root: Path, run_id: str, payload: Mapping[str, object]) -> Path:
    if guarded.SAFE_RUN_ID.fullmatch(run_id) is None:
        raise guarded.RunnerError("run_id_invalid", "Run identifier is invalid.")
    exact = dict(payload)
    exact["schema_version"] = RECEIPT_SCHEMA
    exact["run_id"] = run_id
    path = guarded._receipt_path(state_root, run_id)
    guarded._write_exclusive_json(path, exact)
    return path


def _worker(spec_path: Path) -> int:
    spec = _load_worker_spec(spec_path)
    try:
        spec_path.unlink()
    except OSError as error:
        raise guarded.RunnerError("worker_spec_cleanup_failed", "Character worker specification could not be consumed.") from error
    request, graph = build_contract(
        contract_id=spec["contract_id"], source_path=spec["source_path"],
        prompt=spec["prompt"], motion=spec["motion"], aspect=spec["aspect"],
        quality=spec["quality"], seed=spec["seed"], run_id=spec["run_id"],
    )
    if hashlib.sha256(_canonical(graph)).hexdigest() != spec["graph_sha256"] or graph != spec["graph"]:
        raise guarded.RunnerError("worker_graph_mismatch", "Character qualification graph changed.")
    contract = H3_CONTRACTS[spec["contract_id"]]
    manifest = load_manifest(guarded.DEFAULT_MANIFEST)
    decision = evaluate_local_inference_authorization(
        spec["authorization_record"], manifest=manifest, family=contract.family,
    )
    if not decision.authorized:
        raise guarded.RunnerError("authorization_blocked", "Local MiniMax H3 inference is not authorized.")
    models_dir = Path(spec["models_dir"])
    comfy_dir = Path(spec["comfy_dir"])
    guarded.require_no_incomplete_model_downloads(models_dir, manifest)
    models = guarded.verify_pinned_models(models_dir, manifest, family=contract.family)
    base = guarded.validate_loopback_url(guarded.DEFAULT_COMFY_URL)
    guarded.verify_loopback_listener(base)
    guarded.verify_comfy_runtime_contract(comfy_dir, guarded.request_json(base, "/system_stats"))
    guarded.verify_object_info(guarded.request_json(base, "/object_info"), graph)
    guarded.require_queue_idle(guarded.request_json(base, "/queue"))
    guarded.wait_for_memory_headroom()
    provider_graph.install_source({
        "source_path": request["source_path"], "source_sha256": request["source_image"]["sha256"],
        "upload_basename": request["upload_basename"],
    }, (comfy_dir / "input").resolve(strict=True))
    # Recheck every mutable gate immediately before submission.
    if not evaluate_local_inference_authorization(
        spec["authorization_record"], manifest=manifest, family=contract.family,
    ).authorized:
        raise guarded.RunnerError("authorization_blocked", "Local inference authorization expired.")
    guarded.require_no_incomplete_model_downloads(models_dir, manifest)
    if guarded.verify_pinned_models(models_dir, manifest, family=contract.family) != models:
        raise guarded.RunnerError("models_changed", "Pinned model scope changed.")
    guarded.require_memory_headroom(guarded.read_memory_snapshot())
    guarded.require_gpu_headroom(guarded.read_gpu_snapshot())
    guarded.verify_loopback_listener(base)
    guarded.verify_comfy_runtime_contract(comfy_dir, guarded.request_json(base, "/system_stats"))
    guarded.verify_object_info(guarded.request_json(base, "/object_info"), graph)
    guarded.require_queue_idle(guarded.request_json(base, "/queue"))
    memory_cleanup = guarded.memory_cleanup_not_attempted()
    submission_attempted = False
    cancel_file = str(spec["cancel_file"])
    try:
        guarded._check_deadline_or_cancel(
            deadline_epoch=float(spec["deadline_epoch"]),
            cancel_file=cancel_file,
        )
        # From this assignment onward a transport error may hide an accepted POST.
        submission_attempted = True
        prompt_id = guarded.submit_prompt(
            base,
            graph,
            client_id="castle-h3-character-qualification",
            prompt_id=spec["prompt_id"],
        )
        history = guarded.wait_for_history(
            base,
            prompt_id,
            deadline_epoch=spec["deadline_epoch"],
            cancel_file=cancel_file,
        )
        output = provider_graph.bind_output(history, graph, Path(spec["output_dir"]))
        generator = graph["5"]["inputs"]
        resolution = (int(generator["width"]), int(generator["height"]))
        frames = int(generator["length"])
        output_identity = guarded._artifact_snapshot(output)
        probe = guarded.probe_output(output, resolution=resolution, frames=frames)
        size, output_sha = provider_graph.stable_sha(output)
        if guarded._artifact_snapshot(output) != output_identity:
            raise guarded.RunnerError(
                "output_changed", "Character qualification output changed during validation."
            )
    except BaseException:
        if submission_attempted:
            guarded.cancel_owned_prompt(base, str(spec["prompt_id"]))
        raise
    finally:
        if submission_attempted:
            guarded.wait_for_comfy_queue_idle(base, timeout_seconds=None)
            memory_cleanup = guarded.release_comfy_memory(base)
    model_evidence = {
        "artifact_set": manifest.artifact_set, "revision": manifest.revision,
        "sha256": manifest_scope_sha256(manifest),
    }
    receipt = {
        "schema_version": RECEIPT_SCHEMA, "run_id": spec["run_id"],
        "mode": contract.mode, "status": "completed", "error_code": None,
        "created_at": spec["created_at"], "finished_at": guarded._utc_now(),
        "profile": request["profile"], "resolution": {"width": resolution[0], "height": resolution[1]},
        "frames": frames, "fps": guarded.native.STOCK_FPS, "seed": request["seed"],
        "prompt_sha256": hashlib.sha256(
            str(graph["5"]["inputs"]["prompt"]).encode("utf-8")
        ).hexdigest(),
        "graph_sha256": spec["graph_sha256"], "prompt_id": prompt_id,
        "authorization": "authorized", "models_verified": len(models),
        "model_manifest": model_evidence,
        "output": {
            "filename": output.name, "subfolder": request["output_prefix"].rsplit("/", 1)[0],
            "type": "output", "bytes": size, "sha256": output_sha, "probe": probe,
        },
        "contract_id": spec["contract_id"], "visual_class": contract.visual_class,
        "graph_contract_sha256": request["graph_contract_sha256"],
        "source_image": request["source_image"],
        "memory_cleanup": memory_cleanup,
    }
    _write_character_receipt(Path(spec["state_root"]), spec["run_id"], receipt)
    return 0


def _gpu_queue_command(queue_script: Path, spec_path: Path) -> list[str]:
    return [
        sys.executable,
        str(queue_script),
        "--min-free-mb",
        str(guarded.GPU_MIN_FREE_MIB),
        "--max-util",
        str(guarded.GPU_MAX_UTIL_PERCENT),
        "--interval",
        str(guarded.GPU_QUEUE_INTERVAL_SECONDS),
        "--comfy-url",
        "",
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        "--_worker",
        str(spec_path),
    ]


def _queue(spec: dict[str, Any], *, state_root: Path, queue_script: Path) -> int:
    pending = guarded._ensure_private_directory(state_root / "pending-character")
    spec_path = pending / f"{spec['run_id']}.json"
    cancel_path = pending / f"{spec['run_id']}.cancel"
    token = secrets.token_hex(32)
    stored = dict(spec)
    stored["cancel_file"] = str(cancel_path)
    stored["worker_hmac_sha256"] = guarded._worker_spec_hmac(stored, token)
    guarded._write_exclusive_json(spec_path, stored)
    environment = guarded._worker_environment(token=token, state_root=state_root)
    environment.pop("CASTLE_H3_WORKER_TOKEN", None)
    environment.pop("CASTLE_H3_WORKER_STATE_ROOT", None)
    environment["CASTLE_H3_CHARACTER_WORKER_TOKEN"] = token
    environment["CASTLE_H3_CHARACTER_STATE_ROOT"] = str(state_root)
    command = _gpu_queue_command(queue_script, spec_path)
    process = subprocess.Popen(command, env=environment, cwd=REPO_ROOT, start_new_session=True, close_fds=True)
    previous_handlers: dict[int, Any] = {}

    def request_cancel(_signum: int, _frame: object) -> None:
        try:
            guarded._write_exclusive_json(
                cancel_path,
                {"run_id": spec["run_id"], "error_code": "run_cancelled"},
            )
        except guarded.RunnerError:
            pass

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_cancel)
            except ValueError:
                previous_handlers.clear()
                break
        while process.poll() is None:
            try:
                time.sleep(0.25)
            except KeyboardInterrupt:
                request_cancel(signal.SIGINT, None)
        if process.returncode != 0:
            guarded.cancel_owned_prompt(guarded.DEFAULT_COMFY_URL, spec["prompt_id"])
            guarded.wait_for_comfy_queue_idle(
                guarded.DEFAULT_COMFY_URL, timeout_seconds=None
            )
            raise guarded.RunnerError("worker_failed", "Character qualification worker failed safely.")
        receipt = guarded._receipt_path(state_root, spec["run_id"])
        if not receipt.exists():
            raise guarded.RunnerError("receipt_missing", "Character qualification receipt is missing.")
        try:
            validate_qualification(
                spec["run_id"], contract_id=spec["contract_id"],
                state_root=state_root, output_root=Path(spec["output_dir"]),
            )
        except GoldenIssueError as error:
            raise guarded.RunnerError(
                "receipt_invalid", "Character qualification receipt is invalid."
            ) from error
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                pass
        spec_path.unlink(missing_ok=True)
        cancel_path.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--_worker", help=argparse.SUPPRESS)
    action = value.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--execute", action="store_true")
    value.add_argument("--contract", choices=CONTRACT_IDS)
    value.add_argument("--source-image")
    prompt = value.add_mutually_exclusive_group()
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    value.add_argument("--motion")
    value.add_argument("--aspect")
    value.add_argument("--quality", choices=("quality",), default="quality")
    value.add_argument("--seed", type=int)
    value.add_argument("--timeout-seconds", type=int, default=guarded.DEFAULT_TIMEOUT_SECONDS)
    value.add_argument("--state-dir", default=str(STATE_ROOT))
    value.add_argument("--comfy-dir", default=str(guarded.DEFAULT_COMFY_DIR))
    value.add_argument("--authorization-record", default=str(guarded.DEFAULT_AUTHORIZATION_RECORD))
    value.add_argument("--gpu-queue-script", default=str(guarded.DEFAULT_GPU_QUEUE_SCRIPT))
    return value


def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args._worker:
        try:
            return _worker(Path(args._worker))
        except BaseException:
            return 2
    try:
        if not args.contract or not args.source_image or (args.prompt is None) == (args.prompt_file is None):
            raise guarded.RunnerError("request_invalid", "Contract, source image, and exactly one prompt are required.")
        if not guarded.MIN_TIMEOUT_SECONDS <= args.timeout_seconds <= guarded.MAX_TIMEOUT_SECONDS:
            raise guarded.RunnerError("timeout_invalid", "Timeout is outside the allowed range.")
        prompt = args.prompt if args.prompt is not None else guarded._read_prompt_file(args.prompt_file)
        run_id = guarded._new_run_id()
        seed = args.seed if args.seed is not None else secrets.randbits(64)
        request, graph = build_contract(
            contract_id=args.contract, source_path=args.source_image, prompt=prompt,
            motion=args.motion, aspect=args.aspect, quality=args.quality, seed=seed,
            run_id=run_id,
        )
        summary = sanitized_summary(request, graph, run_id)
        if not args.execute:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        state_root = guarded._ensure_private_directory(args.state_dir)
        comfy_dir = Path(args.comfy_dir).expanduser().resolve(strict=True)
        deadline = time.time() + args.timeout_seconds
        spec: dict[str, Any] = {
            "schema_version": SPEC_SCHEMA, "run_id": run_id,
            "created_at": guarded._utc_now(), "deadline_epoch": deadline,
            "contract_id": args.contract, "prompt": request["prompt"],
            "motion": request["motion"], "aspect": request["aspect"],
            "quality": request["quality"], "seed": request["seed"],
            "source_path": request["source_path"], "source_image": request["source_image"],
            "upload_basename": request["upload_basename"], "graph": graph,
            "graph_sha256": summary["graph"]["sha256"], "prompt_id": str(uuid.uuid4()),
            "comfy_dir": str(comfy_dir), "models_dir": str(comfy_dir / "models"),
            "output_dir": str(comfy_dir / "output"),
            "authorization_record": str(Path(args.authorization_record).expanduser().resolve(strict=True)),
            "state_root": str(state_root),
        }
        print(json.dumps({"status": "queued", "run_id": run_id, "contract_id": args.contract}))
        return _queue(spec, state_root=state_root, queue_script=guarded._safe_queue_script(args.gpu_queue_script))
    except guarded.RunnerError as error:
        print(json.dumps({"status": "rejected", "error": error.code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
