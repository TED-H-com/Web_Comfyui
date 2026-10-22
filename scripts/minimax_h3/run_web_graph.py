#!/usr/bin/env python3
"""Execute one authenticated MiniMax H3 Web graph under Castle's GPU flock.

Exit status 10 is reserved for failures proven to occur before the irreversible
ComfyUI ``POST /prompt`` boundary.  Every non-zero failure after that boundary
is uncertain and exits 1, after owner-scoped cancellation, global queue-idle
proof, and the controlled ComfyUI service recycle.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Mapping
import uuid


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEBUI = ROOT / "webui"
for item in (str(ROOT), str(WEBUI), str(HERE)):
    if item not in sys.path:
        sys.path.insert(0, item)

from scripts.minimax_h3 import run_native as guarded  # noqa: E402
import minimax_h3_web_executor as web_executor  # noqa: E402


_SAFE_SPEC = re.compile(r"spec-([0-9a-f]{16})\.json")


class PreSubmitError(RuntimeError):
    """A failure for which ComfyUI submission is proven not to have happened."""


def _is_memory_recovery_failure(error: BaseException) -> bool:
    """Recognize the one safe pre-submit failure exposed to the Web parent."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, guarded.RunnerError)
            and current.code == "memory_recovery_failed"
        ):
            return True
        current = current.__cause__
    return False


def _cancel_requested(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise PreSubmitError("cancellation state is unavailable") from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise PreSubmitError("cancellation state is unsafe")
    return True


def _check_stop(cancel_file: Path, *, deadline_epoch: float) -> None:
    if _cancel_requested(cancel_file):
        raise guarded.RunnerError("run_cancelled", "MiniMax H3 Web run was cancelled.")
    if time.time() >= deadline_epoch:
        raise guarded.RunnerError("run_timeout", "MiniMax H3 Web run reached its deadline.")


def _load_spec(
    path: Path, *, token: str, expected_state_root: Path
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, object]],
    web_executor.WebGraphContract,
    Path,
    Path,
]:
    try:
        state_root = guarded._ensure_private_directory(expected_state_root)
        lexical = Path(os.path.abspath(os.fspath(path)))
    except (OSError, guarded.RunnerError) as error:
        raise PreSubmitError("private Web worker state is unavailable") from error
    match = _SAFE_SPEC.fullmatch(lexical.name)
    if lexical.parent != state_root or match is None:
        raise PreSubmitError("private Web worker specification path escaped state")
    try:
        raw = guarded._read_owner_file(
            lexical,
            max_bytes=guarded.MAX_SPEC_BYTES,
            code="worker_spec_invalid",
        )
        value = guarded._safe_json_loads(raw, code="worker_spec_invalid")
    except guarded.RunnerError as error:
        raise PreSubmitError("private Web worker specification is unreadable") from error
    if (
        type(value) is not dict
        or set(value) != web_executor.WORKER_SPEC_KEYS
        or value.get("schema_version") != web_executor.WORKER_SCHEMA_VERSION
    ):
        raise PreSubmitError("private Web worker specification schema changed")
    signature = value.get("worker_hmac_sha256")
    try:
        expected_signature = guarded._worker_spec_hmac(value, token)
    except guarded.RunnerError as error:
        raise PreSubmitError("private Web worker token is invalid") from error
    if (
        type(signature) is not str
        or re.fullmatch(r"[0-9a-f]{64}", signature) is None
        or not secrets.compare_digest(signature, expected_signature)
    ):
        raise PreSubmitError("private Web worker specification is not authentic")
    if value.get("state_root") != str(state_root):
        raise PreSubmitError("private Web worker state root changed")
    nonce = match.group(1)
    receipt_path = state_root / f"receipt-{nonce}.json"
    cancel_path = state_root / f"cancel-{nonce}.json"
    if (
        value.get("receipt_path") != str(receipt_path)
        or value.get("cancel_file") != str(cancel_path)
        or receipt_path.exists()
    ):
        raise PreSubmitError("private Web worker path escaped state")
    prompt_id = value.get("prompt_id")
    try:
        canonical_prompt_id = str(uuid.UUID(str(prompt_id)))
    except (TypeError, ValueError) as error:
        raise PreSubmitError("private Web worker prompt identifier is invalid") from error
    if canonical_prompt_id != prompt_id:
        raise PreSubmitError("private Web worker prompt identifier is invalid")
    deadline = value.get("deadline_epoch")
    if (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or not math.isfinite(float(deadline))
        or float(deadline) > time.time() + web_executor.MAX_TIMEOUT_SECONDS
    ):
        raise PreSubmitError("private Web worker deadline is invalid")
    try:
        comfy_dir, input_root, output_root = web_executor._comfy_roots(
            str(value.get("comfy_dir") or "")
        )
        graph = web_executor._snapshot_graph(value.get("graph"))
        graph_sha256 = web_executor._graph_sha256(graph)
        contract = web_executor._validate_graph_contract(
            graph, input_root=input_root
        )
    except web_executor.WebGraphExecutionError as error:
        raise PreSubmitError("private Web worker graph is invalid") from error
    if graph_sha256 != value.get("graph_sha256"):
        raise PreSubmitError("private Web worker graph digest changed")
    expected_contract = web_executor._contract_payload(contract)
    if any(value.get(key) != expected_contract[key] for key in expected_contract):
        raise PreSubmitError("private Web worker graph contract changed")
    if str(comfy_dir) != value.get("comfy_dir"):
        raise PreSubmitError("private Web worker ComfyUI root changed")
    return value, graph, contract, input_root, output_root


def _bind_history_output(
    history: Mapping[str, object],
    *,
    contract: web_executor.WebGraphContract,
    output_root: Path,
) -> Path:
    outputs = history.get("outputs")
    save = outputs.get(contract.save_node_id) if isinstance(outputs, Mapping) else None
    candidates = save.get("images") if isinstance(save, Mapping) else None
    if (
        type(candidates) is not list
        or len(candidates) != 1
        or type(candidates[0]) is not dict
    ):
        raise RuntimeError("MiniMax H3 Web output metadata is invalid")
    metadata = dict(candidates[0])
    metadata["path"] = str(
        output_root.joinpath(
            *str(metadata.get("subfolder") or "").split("/"),
            str(metadata.get("filename") or ""),
        ).resolve(strict=False)
    )
    return web_executor._output_file(
        metadata, contract=contract, output_root=output_root
    )


def run(spec_path: Path, *, token: str, state_root: Path) -> None:
    spec, graph, contract, input_root, output_root = _load_spec(
        spec_path, token=token, expected_state_root=state_root
    )
    try:
        spec_path.unlink()
    except OSError as error:
        raise PreSubmitError("private Web worker specification could not be consumed") from error

    cancel_file = Path(str(spec["cancel_file"]))
    deadline_epoch = float(spec["deadline_epoch"])
    comfy_dir = Path(str(spec["comfy_dir"]))
    base_url = guarded.validate_loopback_url(guarded.DEFAULT_COMFY_URL)
    submission_attempted = False
    memory_cleanup = guarded.memory_cleanup_not_attempted()
    receipt_payload: dict[str, object] | None = None
    try:
        _check_stop(cancel_file, deadline_epoch=deadline_epoch)
        manifest = guarded.load_manifest(guarded.DEFAULT_MANIFEST)
        family = "ref" if contract.mode == "ref2va_image" else "fl"
        models_dir = comfy_dir / "models"
        guarded.require_no_incomplete_model_downloads(models_dir, manifest)
        selected_models = guarded.verify_pinned_models(
            models_dir, manifest, family=family
        )
        if len(selected_models) != 4:
            raise guarded.RunnerError(
                "models_not_verified", "MiniMax H3 pinned model scope is incomplete."
            )
        guarded.verify_loopback_listener(base_url)
        guarded.verify_comfy_runtime_contract(
            comfy_dir,
            guarded.request_json(base_url, "/system_stats", timeout=10.0),
        )
        guarded.wait_for_memory_headroom()
        guarded.require_gpu_headroom(guarded.read_gpu_snapshot())
        guarded.verify_object_info(
            guarded.request_json(base_url, "/object_info", timeout=30.0), graph
        )
        guarded.require_queue_idle(
            guarded.request_json(base_url, "/queue", timeout=10.0)
        )

        # Re-hash every materialized input after the queue wait, then recheck
        # mutable runtime gates.  RAM is intentionally the last external read
        # before the irreversible POST boundary.
        current_contract = web_executor._validate_graph_contract(
            graph, input_root=input_root
        )
        if web_executor._contract_payload(current_contract) != web_executor._contract_payload(
            contract
        ):
            raise guarded.RunnerError(
                "graph_changed", "MiniMax H3 Web graph inputs changed before submission."
            )
        guarded.require_gpu_headroom(guarded.read_gpu_snapshot())
        guarded.verify_loopback_listener(base_url)
        guarded.verify_comfy_runtime_contract(
            comfy_dir,
            guarded.request_json(base_url, "/system_stats", timeout=10.0),
        )
        guarded.verify_object_info(
            guarded.request_json(base_url, "/object_info", timeout=30.0), graph
        )
        guarded.require_queue_idle(
            guarded.request_json(base_url, "/queue", timeout=10.0)
        )
        _check_stop(cancel_file, deadline_epoch=deadline_epoch)
        guarded.require_no_incomplete_model_downloads(models_dir, manifest)
        verified_again = guarded.verify_pinned_models(
            models_dir, manifest, family=family
        )
        if tuple(verified_again) != tuple(selected_models):
            raise guarded.RunnerError(
                "models_changed", "MiniMax H3 pinned model scope changed before submission."
            )
        guarded.require_memory_headroom(guarded.read_memory_snapshot())

        # From this assignment onward a transport error may hide an accepted POST.
        submission_attempted = True
        prompt_id = guarded.submit_prompt(
            base_url,
            graph,
            client_id="castle-minimax-h3-web",
            prompt_id=str(spec["prompt_id"]),
        )
        history = guarded.wait_for_history(
            base_url,
            prompt_id,
            deadline_epoch=deadline_epoch,
            cancel_file=str(cancel_file),
        )
        output = _bind_history_output(
            history, contract=contract, output_root=output_root
        )
        output_identity = guarded._artifact_snapshot(output)
        probe = guarded.probe_output(
            output,
            resolution=(contract.width, contract.height),
            frames=contract.frames,
        )
        byte_size, digest = guarded._stable_file_sha256(output)
        if guarded._artifact_snapshot(output) != output_identity:
            raise guarded.RunnerError(
                "output_changed", "MiniMax H3 Web output changed during validation."
            )
        expected_subfolder, _token = contract.output_prefix.rsplit("/", 1)
        receipt_payload = {
            "schema_version": web_executor.RECEIPT_SCHEMA_VERSION,
            "prompt_id": prompt_id,
            "graph_sha256": str(spec["graph_sha256"]),
            "mode": contract.mode,
            "resolution": {"width": contract.width, "height": contract.height},
            "frames": contract.frames,
            "fps": guarded.native.STOCK_FPS,
            "models_verified": len(selected_models),
            "model_manifest": {
                "artifact_set": manifest.artifact_set,
                "revision": manifest.revision,
                "sha256": guarded.manifest_scope_sha256(manifest),
            },
            "output": {
                "path": str(output),
                "filename": output.name,
                "subfolder": expected_subfolder,
                "type": "output",
                "bytes": byte_size,
                "sha256": digest,
                "probe": probe,
            },
        }
    except BaseException as error:
        if submission_attempted:
            guarded.cancel_owned_prompt(
                base_url, str(spec["prompt_id"])
            )
            raise
        raise PreSubmitError("MiniMax H3 Web submission was not accepted") from error
    finally:
        if submission_attempted:
            guarded.wait_for_comfy_queue_idle(
                base_url, timeout_seconds=None
            )
            memory_cleanup = guarded.release_comfy_memory(base_url)

    if receipt_payload is None:
        raise RuntimeError("MiniMax H3 Web receipt payload is unavailable")
    receipt_payload["memory_cleanup"] = memory_cleanup
    try:
        guarded._write_exclusive_json(
            Path(str(spec["receipt_path"])), receipt_payload
        )
    except guarded.RunnerError as error:
        raise RuntimeError("MiniMax H3 Web receipt could not be written") from error


def main() -> int:
    token = os.environ.pop(web_executor.WORKER_TOKEN_ENV, "")
    state_value = os.environ.pop(web_executor.WORKER_STATE_ENV, "")
    if (
        web_executor._SAFE_TOKEN.fullmatch(token) is None
        or not state_value
        or len(sys.argv) != 2
    ):
        return 10
    try:
        run(Path(sys.argv[1]), token=token, state_root=Path(state_value))
        return 0
    except PreSubmitError as error:
        # Exit 11 is a narrow, non-secret signal: the worker still proves that
        # POST /prompt was never attempted, while the Web UI can explain the
        # memory recovery failure instead of showing an opaque generic gate.
        return 11 if _is_memory_recovery_failure(error) else 10
    except BaseException:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
