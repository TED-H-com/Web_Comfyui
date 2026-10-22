"""Fail-closed CPU tasks for the production-pack qualification boundary.

These handlers deliberately stop before provider submission, ComfyUI, GPU
work, credential access, or credit authorization.  The first handler rebuilds
the pure qualification plan and publishes immutable JSON contracts.  The
second consumes exactly one bound contract from the orchestrator artifact
store and records that the contract was checked -- not that production ran.
Receipt bytes contain no run, task, artifact, pack, or private-source identity;
transport/reuse provenance remains authoritative in the orchestrator database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import settings_from_env
from .db import connect, json_loads
from .store import artifact_abs_path


PACK_VALIDATE_INPUT_VERSION = "castle.project-pack-validate.input.v1"
CONTRACT_STAGE_INPUT_VERSION = "castle.project-contract-stage.input.v1"
STAGE_RECEIPT_VERSION = "castle.project-stage-receipt.v1"
PROJECT_DAG_FLAG = "CASTLE_PROJECT_DAG_ADMIN"

MAX_DESCRIPTOR_BYTES = 256 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,383}$")
_OUTPUT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


def project_qualification_enabled() -> bool:
    """Return the live rollout gate used at admission and execution time."""

    return str(os.environ.get(PROJECT_DAG_FLAG, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def require_project_qualification_enabled() -> None:
    if not project_qualification_enabled():
        raise ValueError("Castle project qualification is disabled")


def _cancelled(cancel_file: Path) -> bool:
    return cancel_file.exists()


def _check_cancel(cancel_file: Path) -> None:
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()


def _canonical_json_bytes(value: Any, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain JSON values only") from error


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact_non_runtime_fields(
    payload: Mapping[str, Any],
    required: set[str],
    label: str,
) -> None:
    if not isinstance(payload, Mapping) or any(not isinstance(key, str) for key in payload):
        raise ValueError(f"{label} must be an object with string keys")
    fields = {key for key in payload if not key.startswith("_")}
    extra = sorted(fields - required)
    missing = sorted(required - fields)
    if extra:
        raise ValueError(f"{label} has unknown fields: {', '.join(extra)}")
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")


def _require_string(value: Any, label: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{label} must be a non-empty bounded string")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA256 digest")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except AttributeError:  # pragma: no cover - Python 3.8 compatibility
        return os.path.commonpath((str(path), str(root))) == str(root)


def _safe_output_dir(work_dir: Path) -> Path:
    root = work_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("task work directory is unavailable")
    candidate = root / "tmp"
    candidate.mkdir(mode=0o700, exist_ok=True)
    output_dir = candidate.resolve()
    if not output_dir.is_dir() or not _inside(output_dir, root):
        raise ValueError("task output directory escapes the work directory")
    return output_dir


def _write_canonical_json(
    work_dir: Path,
    filename: str,
    value: Any,
    *,
    cancel_file: Path,
) -> tuple[Path, bytes]:
    """Atomically write owner-only canonical JSON inside ``work_dir/tmp``."""

    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise ValueError("JSON output filename is unsafe")
    raw = _canonical_json_bytes(value, filename)
    _check_cancel(cancel_file)
    output_dir = _safe_output_dir(work_dir)
    target = output_dir / filename
    temporary = output_dir / f".{filename}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while creating JSON artifact")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    if _cancelled(cancel_file):
        temporary.unlink(missing_ok=True)
        raise KeyboardInterrupt()
    try:
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(target, 0o600)
    return target, raw


def _qualification_api():
    # The web process imports ``orchestrator`` and ``media_projects`` as
    # siblings from webui/.  Keeping this import lazy also prevents a worker
    # that never runs a project task from loading the project compiler.
    try:
        from media_projects.execution import (  # type: ignore[import-not-found]
            parse_project_run_request,
            prepare_qualification_plan,
            qualification_node_hash,
            validate_qualification_node_descriptor,
        )
    except ModuleNotFoundError:  # imported as webui.orchestrator in unit tests
        from webui.media_projects.execution import (  # type: ignore[import-not-found]
            parse_project_run_request,
            prepare_qualification_plan,
            qualification_node_hash,
            validate_qualification_node_descriptor,
        )

    return (
        parse_project_run_request,
        prepare_qualification_plan,
        qualification_node_hash,
        validate_qualification_node_descriptor,
    )


def _plan_hash(plan: Mapping[str, Any]) -> str:
    return _require_sha256(plan.get("plan_hash"), "qualification plan hash")


def _plan_descriptors(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    descriptors = plan.get("nodes")
    if not isinstance(descriptors, list):
        raise ValueError("qualification plan must contain a nodes array")
    if not descriptors or len(descriptors) > 63:
        raise ValueError("qualification plan descriptor count is invalid")
    if any(not isinstance(item, Mapping) for item in descriptors):
        raise ValueError("qualification plan descriptors must be objects")
    return list(descriptors)


def _validated_descriptor(
    raw: Mapping[str, Any],
    validator,
) -> dict[str, Any]:
    result = validator(raw)
    descriptor = result if isinstance(result, Mapping) else raw
    # Copy through a canonical JSON round trip: this rejects non-JSON values
    # and prevents later mutation through a Mapping implementation.
    return json.loads(_canonical_json_bytes(descriptor, "qualification node descriptor"))


def _descriptor_node_id(descriptor: Mapping[str, Any]) -> str:
    value = descriptor.get("node_id")
    if not isinstance(value, str) or _NODE_ID_RE.fullmatch(value) is None:
        raise ValueError("qualification descriptor node_id is invalid")
    return value


def _descriptor_output_name(descriptor: Mapping[str, Any]) -> str:
    value = descriptor.get("output_name")
    if not isinstance(value, str) or _OUTPUT_NAME_RE.fullmatch(value) is None:
        raise ValueError("qualification descriptor output_name is invalid")
    if value in {"project_pack", "qualification_plan", "receipt"}:
        raise ValueError("qualification descriptor output_name is reserved")
    return value


def run_project_pack_validate(
    payload: dict,
    work_dir: Path,
    cancel_file: Path,
) -> list[dict]:
    """Rebuild and persist the no-spend production qualification contracts."""

    require_project_qualification_enabled()

    _exact_non_runtime_fields(
        payload,
        {"schema_version", "request", "expected_plan_hash"},
        "project pack validation input",
    )
    if payload["schema_version"] != PACK_VALIDATE_INPUT_VERSION:
        raise ValueError(f"schema_version must be {PACK_VALIDATE_INPUT_VERSION}")
    request = payload["request"]
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    expected_plan_hash = _require_sha256(payload["expected_plan_hash"], "expected_plan_hash")
    _check_cancel(cancel_file)

    parse_request, prepare_plan, node_hash, validate_descriptor = _qualification_api()
    canonical_request_value = parse_request(request)
    if not isinstance(canonical_request_value, Mapping):
        raise ValueError("qualification request parser returned an invalid document")
    canonical_request = json.loads(
        _canonical_json_bytes(canonical_request_value, "qualification request"))
    plan_value = prepare_plan(canonical_request)
    if not isinstance(plan_value, Mapping):
        raise ValueError("qualification plan builder returned an invalid document")
    plan = json.loads(_canonical_json_bytes(plan_value, "qualification plan"))
    actual_plan_hash = _plan_hash(plan)
    if actual_plan_hash != expected_plan_hash:
        raise ValueError("qualification plan hash differs from the submitted contract")

    pack_value = canonical_request.get("production_pack")
    if not isinstance(pack_value, Mapping):
        raise ValueError("qualification request has no production_pack object")
    # Reparse independently so the published pack is the canonical strict
    # contract, rather than caller-controlled key order or object subclasses.
    try:
        from media_projects.contracts import parse_production_pack  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # imported as webui.orchestrator in unit tests
        from webui.media_projects.contracts import parse_production_pack  # type: ignore[import-not-found]

    pack = parse_production_pack(pack_value)
    canonical_pack = pack.to_dict()
    pack_hash = pack.content_hash
    declared_pack_hash = plan.get("pack_hash")
    if declared_pack_hash != pack_hash:
        raise ValueError("qualification plan does not bind the canonical production pack")

    descriptors: list[tuple[dict[str, Any], str, str]] = []
    seen_nodes: set[str] = set()
    seen_outputs: set[str] = set()
    for raw_descriptor in _plan_descriptors(plan):
        descriptor = _validated_descriptor(raw_descriptor, validate_descriptor)
        node_id = _descriptor_node_id(descriptor)
        output_name = _descriptor_output_name(descriptor)
        if node_id in seen_nodes or output_name in seen_outputs:
            raise ValueError("qualification descriptor identities must be unique")
        descriptor_hash = _require_sha256(
            node_hash(descriptor), "qualification descriptor hash")
        canonical_hash = _sha256(_canonical_json_bytes(descriptor, "qualification node descriptor"))
        if descriptor_hash != canonical_hash:
            raise ValueError("qualification descriptor hash is not its canonical byte hash")
        seen_nodes.add(node_id)
        seen_outputs.add(output_name)
        descriptors.append((descriptor, descriptor_hash, output_name))

    _check_cancel(cancel_file)
    pack_path, pack_bytes = _write_canonical_json(
        work_dir, "project-pack.json", canonical_pack, cancel_file=cancel_file)
    plan_path, plan_bytes = _write_canonical_json(
        work_dir, "qualification-plan.json", plan, cancel_file=cancel_file)
    if _sha256(pack_bytes) != pack_hash:
        raise ValueError("canonical project pack bytes do not match the pack hash")

    outputs: list[dict] = [
        {
            "path": str(pack_path),
            "name": pack_path.name,
            "artifact_type": "json",
            "output_name": "project_pack",
            "metadata": {
                "schema_version": canonical_pack["version"],
                "task_schema_version": PACK_VALIDATE_INPUT_VERSION,
                "project_id": pack.project.project_id,
                "production_pack_hash": pack_hash,
                "owner_only": True,
                "qualification_only": True,
            },
            "tags": ["project:qualification", "role:production-pack"],
        },
        {
            "path": str(plan_path),
            "name": plan_path.name,
            "artifact_type": "json",
            "output_name": "qualification_plan",
            "metadata": {
                "schema_version": plan["schema_version"],
                "task_schema_version": PACK_VALIDATE_INPUT_VERSION,
                "project_id": pack.project.project_id,
                "production_pack_hash": pack_hash,
                "qualification_plan_hash": actual_plan_hash,
                "artifact_content_hash": _sha256(plan_bytes),
                "owner_only": True,
                "qualification_only": True,
            },
            "tags": ["project:qualification", "role:qualification-plan"],
        },
    ]
    for descriptor, descriptor_hash, output_name in descriptors:
        _check_cancel(cancel_file)
        descriptor_path, descriptor_bytes = _write_canonical_json(
            work_dir,
            f"{output_name}.json",
            descriptor,
            cancel_file=cancel_file,
        )
        if _sha256(descriptor_bytes) != descriptor_hash:
            raise ValueError("persisted descriptor bytes differ from its contract hash")
        outputs.append(
            {
                "path": str(descriptor_path),
                "name": descriptor_path.name,
                "artifact_type": "json",
                "output_name": output_name,
                "metadata": {
                    "schema_version": descriptor["schema_version"],
                    "task_schema_version": PACK_VALIDATE_INPUT_VERSION,
                    "node_id": _descriptor_node_id(descriptor),
                    "descriptor_sha256": descriptor_hash,
                    "production_pack_hash": pack_hash,
                    "owner_only": True,
                    "qualification_only": True,
                },
                "tags": ["project:qualification", "role:node-contract"],
            }
        )
    _check_cancel(cancel_file)
    return outputs


def _read_artifact_bytes(path: Path, artifact_root: Path) -> bytes:
    root = artifact_root.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not _inside(resolved, root):
        raise ValueError("descriptor artifact is outside the configured artifact store")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("descriptor artifact is not a regular file")
        if details.st_size > MAX_DESCRIPTOR_BYTES:
            raise ValueError(f"descriptor artifact exceeds {MAX_DESCRIPTOR_BYTES} bytes")
        chunks: list[bytes] = []
        remaining = MAX_DESCRIPTOR_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            raise ValueError(f"descriptor artifact exceeds {MAX_DESCRIPTOR_BYTES} bytes")
        return raw
    finally:
        os.close(descriptor)


def _bound_artifact(payload: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    artifact_id = _require_string(payload["descriptor_artifact_id"], "descriptor_artifact_id")
    if _ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        raise ValueError("descriptor_artifact_id is invalid")
    declared_hash = _require_sha256(payload["descriptor_content_hash"], "descriptor_content_hash")
    expected_hash = _require_sha256(
        payload["expected_descriptor_sha256"], "expected_descriptor_sha256")
    if declared_hash != expected_hash:
        raise ValueError("bound descriptor hashes disagree")
    run_id = _require_string(payload.get("_run_id"), "_run_id")
    task_id = _require_string(payload.get("_task_id"), "_task_id")
    settings = settings_from_env()

    with connect(settings) as conn:
        task = conn.execute(
            "select id, run_id from tasks where id = ?", (task_id,)).fetchone()
        if task is None or task["run_id"] != run_id:
            raise ValueError("current qualification task does not belong to the declared run")
        bindings = conn.execute(
            """
            select name, artifact_id, value_json from task_inputs
            where task_id = ? and name in ('descriptor_artifact_id','descriptor_content_hash')
            """,
            (task_id,),
        ).fetchall()
        by_name = {str(row["name"]): row for row in bindings}
        if set(by_name) != {"descriptor_artifact_id", "descriptor_content_hash"}:
            raise ValueError("descriptor artifact must be supplied by both orchestrator bindings")
        for name, expected_value in (
            ("descriptor_artifact_id", artifact_id),
            ("descriptor_content_hash", declared_hash),
        ):
            row = by_name[name]
            if row["artifact_id"] != artifact_id:
                raise ValueError("descriptor bindings do not resolve to one artifact")
            if json_loads(row["value_json"], None) != expected_value:
                raise ValueError(f"{name} differs from its bound value")

        row = conn.execute(
            """
            select a.*, t.run_id as producer_run_id
            from artifacts a
            left join tasks t on t.id = a.producing_task_id
            where a.id = ?
            """,
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise ValueError("descriptor artifact does not exist")
        artifact = dict(row)
        if (
            artifact.get("run_id") != run_id
            or artifact.get("producer_run_id") != run_id
            or artifact.get("status") != "available"
            or artifact.get("artifact_type") != "json"
            or artifact.get("inline_json") not in {None, ""}
        ):
            raise ValueError("descriptor artifact is not an available JSON file from this run")
        stored_hash = _require_sha256(artifact.get("content_hash"), "stored artifact content hash")
        if stored_hash != declared_hash:
            raise ValueError("stored descriptor hash differs from the bound contract")
        artifact_path = artifact_abs_path(conn, row)
        if artifact_path is None:
            raise ValueError("descriptor artifact has no safe file path")
        raw = _read_artifact_bytes(artifact_path, settings.artifact_root)

    if _sha256(raw) != declared_hash:
        raise ValueError("descriptor file bytes differ from all declared hashes")
    return artifact, raw


def _receipt_contract(descriptor: Mapping[str, Any], descriptor_hash: str) -> dict[str, Any]:
    """Copy only execution-control fields; never copy pack/source payloads."""

    contract: dict[str, Any] = {
        "schema_version": descriptor.get("schema_version"),
        "node_id": _descriptor_node_id(descriptor),
        "descriptor_sha256": descriptor_hash,
    }
    for key in (
        "task_key",
        "output_name",
        "source_key",
        "source_task_type",
        "source_lane",
        "compiler_input_hash",
        "depends_on",
        "route_binding",
        "resource_locks",
        "reuse_accepted_output",
    ):
        value = descriptor.get(key)
        if value is not None:
            contract[key] = value
    contract["retry_policy"] = descriptor.get("retry_policy")
    contract["gate"] = descriptor.get("gate")
    contract["authority"] = descriptor.get("authority")
    return contract


def run_project_contract_stage(
    payload: dict,
    work_dir: Path,
    cancel_file: Path,
) -> list[dict]:
    """Verify one bound node contract and emit a truthful no-spend receipt."""

    require_project_qualification_enabled()

    required = {
        "schema_version",
        "expected_node_id",
        "expected_descriptor_sha256",
        "descriptor_artifact_id",
        "descriptor_content_hash",
    }
    _exact_non_runtime_fields(payload, required, "project contract stage input")
    if payload["schema_version"] != CONTRACT_STAGE_INPUT_VERSION:
        raise ValueError(f"schema_version must be {CONTRACT_STAGE_INPUT_VERSION}")
    expected_node_id = payload["expected_node_id"]
    if not isinstance(expected_node_id, str) or _NODE_ID_RE.fullmatch(expected_node_id) is None:
        raise ValueError("expected_node_id is invalid")
    expected_hash = _require_sha256(
        payload["expected_descriptor_sha256"], "expected_descriptor_sha256")
    _check_cancel(cancel_file)

    _parse_request, _prepare_plan, node_hash, validate_descriptor = _qualification_api()
    _artifact, raw = _bound_artifact(payload)
    _check_cancel(cancel_file)
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("descriptor artifact is not valid UTF-8 JSON") from error
    if not isinstance(decoded, Mapping):
        raise ValueError("descriptor artifact must contain a JSON object")
    descriptor = _validated_descriptor(decoded, validate_descriptor)
    descriptor_hash = _require_sha256(
        node_hash(descriptor), "qualification descriptor hash")
    if descriptor_hash != expected_hash or descriptor_hash != _sha256(raw):
        raise ValueError("validated descriptor identity differs from its artifact contract")
    if _descriptor_node_id(descriptor) != expected_node_id:
        raise ValueError("validated descriptor node_id differs from the expected node")

    receipt = {
        "schema_version": STAGE_RECEIPT_VERSION,
        "qualification_only": True,
        "provider_execution_authorized": False,
        "credits_authorized": 0,
        "production_complete": False,
        "source_contract": _receipt_contract(descriptor, descriptor_hash),
    }
    _check_cancel(cancel_file)
    safe_node = re.sub(r"[^a-z0-9._-]+", "-", expected_node_id).strip("-")[:96] or "node"
    receipt_path, _receipt_bytes = _write_canonical_json(
        work_dir,
        f"project-stage-{safe_node}-receipt.json",
        receipt,
        cancel_file=cancel_file,
    )
    _check_cancel(cancel_file)
    return [
        {
            "path": str(receipt_path),
            "name": receipt_path.name,
            "artifact_type": "json",
            "output_name": "receipt",
            "metadata": {
                "schema_version": STAGE_RECEIPT_VERSION,
                "node_id": expected_node_id,
                "descriptor_sha256": descriptor_hash,
                "qualification_only": True,
                "provider_execution_authorized": False,
                "credits_authorized": 0,
                "production_complete": False,
            },
            "tags": ["project:qualification", "role:stage-receipt"],
        }
    ]


__all__ = [
    "CONTRACT_STAGE_INPUT_VERSION",
    "MAX_DESCRIPTOR_BYTES",
    "PACK_VALIDATE_INPUT_VERSION",
    "PROJECT_DAG_FLAG",
    "STAGE_RECEIPT_VERSION",
    "project_qualification_enabled",
    "require_project_qualification_enabled",
    "run_project_contract_stage",
    "run_project_pack_validate",
]
