#!/usr/bin/env python3
"""Issue an owner-only H3 golden after explicit human review.

This command never runs a workflow and never decides visual quality.  It only
turns two operator-selected, completed stock qualification receipts into the
short-lived binding consumed by ``minimax_h3_readiness``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence
from urllib.request import Request, urlopen
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
WEBUI_ROOT = REPO_ROOT / "webui"
for candidate in (str(REPO_ROOT), str(WEBUI_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from minimax_h3_readiness import (  # noqa: E402
    COMFY_ORIGIN,
    DEFAULT_GOLDEN_RECORD,
    H3_CONTRACTS,
    golden_binding_values,
    graph_contract_sha256,
)
from scripts.minimax_h3.artifacts import load_manifest  # noqa: E402
from scripts.minimax_h3.authorization import manifest_scope_sha256  # noqa: E402
import minimax_h3_native as native  # noqa: E402


CONTRACT_ID = "h3.t2v.stock.v1"  # Backwards-compatible public constant.
ACCEPTANCE_PHRASE = "I_ACCEPT_BOTH_H3_T2V_OUTPUTS"
CHARACTER_ACCEPTANCE_PHRASE = "I_ACCEPT_BOTH_H3_CHARACTER_OUTPUTS"
DEFAULT_STATE_ROOT = Path.home() / ".local/share/web-comfyui/minimax-h3-native-runs"
DEFAULT_OUTPUT_ROOT = Path.home() / "comfyui/output"
DEFAULT_MODEL_MANIFEST = REPO_ROOT / "manifests/minimax_h3/models.lock.json"
RUN_ID = re.compile(r"h3-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
HEX64 = re.compile(r"[0-9a-f]{64}")
MAX_RECEIPT_BYTES = 64 * 1024
GOLDEN_RECEIPT_KEYS = {
    "contract_id", "family", "visual_class", "receipt_id", "accepted",
    "tested_at", "expires_at", "repeat_count", "graph_contract_sha256",
    "runtime_schema_sha256", "model_manifest_sha256", "model_revision",
    "workflow_manifest_sha256", "workflow_revision", "revoked",
}


class GoldenIssueError(ValueError):
    pass


def _exact_object(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as error:
        raise GoldenIssueError("qualification_receipt_missing") from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 0 < info.st_size <= MAX_RECEIPT_BYTES
    ):
        raise GoldenIssueError("qualification_receipt_unsafe")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8", "strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GoldenIssueError("qualification_receipt_invalid") from error
    if not isinstance(value, dict):
        raise GoldenIssueError("qualification_receipt_invalid")
    return value


def _stable_sha256(path: Path) -> tuple[int, str]:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise GoldenIssueError("qualification_output_unsafe")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        after = path.lstat()
    except OSError as error:
        raise GoldenIssueError("qualification_output_missing") from error
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if identity(before) != identity(after) or size != before.st_size:
        raise GoldenIssueError("qualification_output_changed")
    return size, digest.hexdigest()


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed


def _contract(contract_id: str) -> Any:
    contract = H3_CONTRACTS.get(contract_id)
    if contract is None:
        raise GoldenIssueError("contract_not_qualified")
    return contract


def _memory_cleanup_valid(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    legacy = {
        "attempted", "status", "error_code", "unload_models", "free_memory",
    }
    recycle = legacy | {
        "method", "service", "queue_idle_before", "previous_main_pid",
        "previous_invocation_id", "current_main_pid", "current_invocation_id",
        "loopback_ready_after", "queue_idle_after", "memory_available_bytes_after",
        "minimum_memory_available_bytes",
    }
    keys = set(value)
    if keys != legacy and keys != recycle:
        return False
    if (
        value.get("attempted") is not True
        or value.get("unload_models") is not True
        or value.get("free_memory") is not True
    ):
        return False
    status = value.get("status")
    error_code = value.get("error_code")
    if keys == legacy and status == "accepted":
        return value.get("error_code") is None
    if keys == recycle:
        if (
            value.get("method") != "systemd_service_restart"
            or value.get("service") != "comfyui.service"
            or value.get("minimum_memory_available_bytes") != 32 * 1024**3
        ):
            return False
        if status == "service_recycled":
            previous_pid = value.get("previous_main_pid")
            current_pid = value.get("current_main_pid")
            previous_invocation = value.get("previous_invocation_id")
            current_invocation = value.get("current_invocation_id")
            available = value.get("memory_available_bytes_after")
            minimum = value.get("minimum_memory_available_bytes")
            return all((
                error_code is None,
                type(previous_pid) is int and previous_pid > 1,
                type(current_pid) is int and current_pid > 1,
                previous_pid != current_pid,
                isinstance(previous_invocation, str)
                and re.fullmatch(r"[0-9a-f]{32}", previous_invocation) is not None,
                isinstance(current_invocation, str)
                and re.fullmatch(r"[0-9a-f]{32}", current_invocation) is not None,
                previous_invocation != current_invocation,
                value.get("queue_idle_before") is True,
                value.get("loopback_ready_after") is True,
                value.get("queue_idle_after") is True,
                type(minimum) is int and minimum == 32 * 1024**3,
                type(available) is int and type(minimum) is int
                and available >= minimum,
            ))
    return (
        status == "failed"
        and isinstance(error_code, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is not None
    )


def _acceptance_phrase(contract_id: str) -> str:
    return ACCEPTANCE_PHRASE if contract_id == CONTRACT_ID else CHARACTER_ACCEPTANCE_PHRASE


def validate_qualification(
    run_id: str, *, contract_id: str = CONTRACT_ID, state_root: Path, output_root: Path
) -> dict[str, Any]:
    if RUN_ID.fullmatch(run_id) is None:
        raise GoldenIssueError("run_id_invalid")
    receipt = _exact_object(state_root / "receipts" / f"{run_id}.json")
    contract = _contract(contract_id)
    common = {
        "schema_version", "run_id", "mode", "status", "error_code", "created_at",
        "finished_at", "profile", "resolution", "frames", "fps", "seed",
        "prompt_sha256", "graph_sha256", "prompt_id", "authorization",
        "models_verified", "model_manifest", "output",
    }
    character_mode = contract.mode in {"i2v", "ref2va_image"}
    required = common | ({
        "contract_id", "visual_class", "graph_contract_sha256", "source_image",
    } if character_mode else set())
    expected_schema = 2 if character_mode else 1
    receipt_keys = set(receipt)
    if receipt_keys not in (required, required | {"memory_cleanup"}) or not all((
        receipt.get("schema_version") == expected_schema,
        receipt.get("run_id") == run_id,
        receipt.get("mode") == contract.mode,
        receipt.get("status") == "completed",
        receipt.get("error_code") is None,
        receipt.get("authorization") == "authorized",
        receipt.get("models_verified") == 4,
        isinstance(receipt.get("output"), dict),
    )):
        raise GoldenIssueError("qualification_receipt_not_completed")
    if "memory_cleanup" in receipt and not _memory_cleanup_valid(
        receipt.get("memory_cleanup")
    ):
        raise GoldenIssueError("qualification_receipt_not_completed")
    created = _timestamp(receipt.get("created_at"))
    finished = _timestamp(receipt.get("finished_at"))
    resolution = receipt.get("resolution")
    prompt_id = receipt.get("prompt_id")
    profile = receipt.get("profile")
    expected_frames = native.DURATION_PROFILES.get(profile) if isinstance(profile, str) else None
    try:
        canonical_prompt_id = str(uuid.UUID(str(prompt_id)))
    except (TypeError, ValueError):
        canonical_prompt_id = None
    if not all((
        created is not None and finished is not None and finished >= created,
        isinstance(profile, str) and profile in native.DURATION_PROFILES,
        isinstance(resolution, dict) and set(resolution) == {"width", "height"},
        isinstance(resolution, dict) and (resolution.get("width"), resolution.get("height")) in native.ALLOWED_RESOLUTIONS,
        type(receipt.get("frames")) is int and receipt.get("frames") == expected_frames,
        receipt.get("fps") == native.STOCK_FPS,
        type(receipt.get("seed")) is int and 0 <= receipt["seed"] <= 2**64 - 1,
        isinstance(receipt.get("prompt_sha256"), str) and HEX64.fullmatch(receipt["prompt_sha256"]) is not None,
        isinstance(receipt.get("graph_sha256"), str) and HEX64.fullmatch(receipt["graph_sha256"]) is not None,
        canonical_prompt_id == prompt_id,
    )):
        raise GoldenIssueError("qualification_execution_evidence_invalid")
    if character_mode:
        source = receipt.get("source_image")
        expected_kind = "source_image" if contract.mode == "i2v" else "reference_image"
        if not isinstance(source, dict) or set(source) != {
            "kind", "bytes", "sha256", "mime", "width", "height"
        }:
            raise GoldenIssueError("qualification_source_evidence_invalid")
        if not all((
            receipt.get("contract_id") == contract_id,
            receipt.get("visual_class") == contract.visual_class,
            receipt.get("graph_contract_sha256") == graph_contract_sha256(contract_id),
            source.get("kind") == expected_kind,
            type(source.get("bytes")) is int and source["bytes"] > 0,
            isinstance(source.get("sha256"), str) and HEX64.fullmatch(source["sha256"]) is not None,
            source.get("mime") in {"image/jpeg", "image/png", "image/webp"},
            type(source.get("width")) is int and source["width"] > 0,
            type(source.get("height")) is int and source["height"] > 0,
        )):
            raise GoldenIssueError("qualification_source_evidence_invalid")
    output = receipt["output"]
    if set(output) != {"filename", "subfolder", "type", "bytes", "sha256", "probe"}:
        raise GoldenIssueError("qualification_output_invalid")
    filename, subfolder = output.get("filename"), output.get("subfolder")
    if (
        not isinstance(filename, str)
        or re.fullmatch(re.escape(run_id) + r"_[0-9]{5}_\.mp4", filename) is None
        or subfolder != f"Castle/minimax-h3/{contract.mode}"
        or output.get("type") != "output"
        or type(output.get("bytes")) is not int
        or output["bytes"] <= 0
        or not isinstance(output.get("sha256"), str)
        or HEX64.fullmatch(output["sha256"]) is None
    ):
        raise GoldenIssueError("qualification_output_invalid")
    manifest = load_manifest(DEFAULT_MODEL_MANIFEST)
    if receipt.get("model_manifest") != {
        "artifact_set": manifest.artifact_set,
        "revision": manifest.revision,
        "sha256": manifest_scope_sha256(manifest),
    }:
        raise GoldenIssueError("qualification_model_evidence_stale")
    probe = output.get("probe")
    expected_probe = {
        "container", "duration_seconds", "width", "height", "fps",
        "video_codec", "audio_codec", "video_streams", "audio_streams",
    }
    if (
        not isinstance(probe, dict)
        or set(probe) != expected_probe
        or not isinstance(resolution, dict)
        or probe.get("container") != "mp4"
        or probe.get("video_codec") != "h264"
        or probe.get("width") != resolution.get("width")
        or probe.get("height") != resolution.get("height")
        or not isinstance(probe.get("fps"), (int, float))
        or isinstance(probe.get("fps"), bool)
        or abs(float(probe["fps"]) - native.STOCK_FPS) > 0.02
        or not isinstance(probe.get("duration_seconds"), (int, float))
        or isinstance(probe.get("duration_seconds"), bool)
        or abs(float(probe["duration_seconds"]) - receipt["frames"] / native.STOCK_FPS)
        > max(0.35, (receipt["frames"] / native.STOCK_FPS) * 0.08)
        or probe.get("video_streams") != 1
        or type(probe.get("audio_streams")) is not int
        or probe["audio_streams"] < 1
        or not isinstance(probe.get("audio_codec"), str)
        or not probe["audio_codec"]
    ):
        raise GoldenIssueError("qualification_probe_invalid")
    candidate = output_root.joinpath(*subfolder.split("/"), filename)
    root = output_root.resolve(strict=True)
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise GoldenIssueError("qualification_output_missing") from error
    if os.path.commonpath((str(root), str(resolved_parent))) != str(root):
        raise GoldenIssueError("qualification_output_unsafe")
    size, digest = _stable_sha256(candidate)
    if size != output["bytes"] or digest != output["sha256"]:
        raise GoldenIssueError("qualification_output_mismatch")
    return receipt


def _repeat_contract(receipts: Sequence[Mapping[str, Any]]) -> None:
    keys = ("profile", "resolution", "frames", "fps", "prompt_sha256")
    first = tuple(receipts[0].get(key) for key in keys)
    if any(tuple(receipt.get(key) for key in keys) != first for receipt in receipts[1:]):
        raise GoldenIssueError("qualification_repeat_contract_mismatch")
    sources = [receipt.get("source_image") for receipt in receipts]
    if any(source is not None for source in sources):
        first_source = sources[0]
        if first_source is None or any(source != first_source for source in sources[1:]):
            raise GoldenIssueError("qualification_repeat_source_mismatch")
    hashes = {receipt["output"]["sha256"] for receipt in receipts}
    if len(hashes) != len(receipts):
        raise GoldenIssueError("qualification_outputs_not_distinct")


def _object_info(origin: str) -> Mapping[str, Any]:
    if origin != COMFY_ORIGIN:
        raise GoldenIssueError("comfy_origin_invalid")
    try:
        with urlopen(Request(origin + "/object_info", headers={"Accept": "application/json"}), timeout=20) as response:
            value = json.load(response)
    except Exception as error:
        raise GoldenIssueError("runtime_object_info_unavailable") from error
    if not isinstance(value, dict):
        raise GoldenIssueError("runtime_object_info_invalid")
    return value


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700 or path.parent.stat().st_uid != os.geteuid():
        raise GoldenIssueError("golden_parent_not_owner_only")
    descriptor, temporary = tempfile.mkstemp(prefix=".golden-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _retained_receipts(path: Path, *, replacing_contract: str) -> list[Mapping[str, Any]]:
    if not path.exists():
        return []
    payload = _exact_object(path)
    receipts = payload.get("receipts") if payload.get("schema_version") == 1 else None
    if (
        set(payload) != {"schema_version", "receipts"}
        or not isinstance(receipts, list)
        or len(receipts) > len(H3_CONTRACTS)
        or any(not isinstance(item, dict) or set(item) != GOLDEN_RECEIPT_KEYS for item in receipts)
    ):
        raise GoldenIssueError("golden_record_invalid")
    contract_ids = [item.get("contract_id") for item in receipts]
    if (
        any(value not in H3_CONTRACTS for value in contract_ids)
        or len(contract_ids) != len(set(contract_ids))
    ):
        raise GoldenIssueError("golden_record_invalid")
    for item in receipts:
        contract = H3_CONTRACTS[item["contract_id"]]
        if item.get("family") != contract.family or item.get("visual_class") != contract.visual_class:
            raise GoldenIssueError("golden_record_invalid")
    return [item for item in receipts if item.get("contract_id") != replacing_contract]


def issue(args: argparse.Namespace) -> dict[str, Any]:
    contract = _contract(args.contract)
    if args.operator_accept_quality != _acceptance_phrase(args.contract):
        raise GoldenIssueError("explicit_human_acceptance_required")
    if len(args.run_id) != 2 or len(set(args.run_id)) != 2:
        raise GoldenIssueError("two_distinct_runs_required")
    receipts = [
        validate_qualification(
            run_id, contract_id=args.contract,
            state_root=args.state_dir, output_root=args.output_dir,
        )
        for run_id in args.run_id
    ]
    _repeat_contract(receipts)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bindings = golden_binding_values(
        contract_id=args.contract,
        object_info=_object_info(args.comfy_url),
    )
    receipt = {
        **bindings,
        "receipt_id": "h3-golden-" + hashlib.sha256("\n".join(sorted(args.run_id)).encode()).hexdigest()[:24],
        "accepted": True,
        "tested_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=args.valid_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repeat_count": 2,
        "revoked": False,
    }
    retained = _retained_receipts(args.golden_record, replacing_contract=args.contract)
    _write_private_json(
        args.golden_record,
        {"schema_version": 1, "receipts": [*retained, receipt]},
    )
    return {"status": "issued", "contract_id": contract.contract_id, "receipt_id": receipt["receipt_id"], "repeat_count": 2}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--contract", required=True)
    value.add_argument("--run-id", action="append", required=True)
    value.add_argument(
        "--operator-accept-quality", required=True,
        help=f"T2V: {ACCEPTANCE_PHRASE}; character video: {CHARACTER_ACCEPTANCE_PHRASE}",
    )
    value.add_argument("--valid-days", type=int, choices=range(1, 31), default=30)
    value.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_ROOT)
    value.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    value.add_argument("--golden-record", type=Path, default=DEFAULT_GOLDEN_RECORD)
    value.add_argument("--comfy-url", default=COMFY_ORIGIN)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(issue(args), sort_keys=True))
        return 0
    except GoldenIssueError as error:
        print(json.dumps({"status": "rejected", "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
