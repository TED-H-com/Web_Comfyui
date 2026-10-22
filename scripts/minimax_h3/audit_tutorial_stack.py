#!/usr/bin/env python3
"""Audit the pinned MiniMAX H3 tutorial stack without changing it.

The baseline H3 lock remains in ``models.lock.json``.  This audit adds the
community checkpoints, exact custom-node revisions, adapted workflows, and
the live ComfyUI node ABI used by the tutorial profiles.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/tutorial_stack.lock.json"
DEFAULT_COMFY_HOME = Path(os.environ.get("COMFY_HOME", "~/comfyui")).expanduser()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def selected(record: dict[str, Any], profile: str) -> bool:
    profiles = record.get("profiles", ["all"])
    return profile == "all" or "all" in profiles or profile in profiles


def active_declared_blockers(
    manifest: dict[str, Any], profile: str
) -> list[dict[str, Any]]:
    """Return blockers that make one explicitly selected profile unrunnable.

    The aggregate ``all`` audit still reports optional/documented alternatives
    without treating them as requirements of the deployed core stack.
    """

    if profile == "all":
        return []
    return [
        blocker
        for blocker in manifest.get("declared_blockers", [])
        if isinstance(blocker, dict) and profile in blocker.get("profiles", [])
    ]


def result(
    checks: list[dict[str, Any]],
    kind: str,
    name: str,
    status: str,
    required: bool,
    detail: str,
) -> None:
    checks.append(
        {
            "kind": kind,
            "name": name,
            "status": status,
            "required": required,
            "detail": detail,
        }
    )


def audit_file(
    checks: list[dict[str, Any]],
    *,
    kind: str,
    name: str,
    path: Path,
    record: dict[str, Any],
    skip_hashes: bool,
) -> None:
    required = bool(record.get("required", True))
    if not path.is_file():
        result(checks, kind, name, "missing", required, str(path))
        return
    expected_bytes = record.get("bytes")
    actual_bytes = path.stat().st_size
    if expected_bytes is not None and actual_bytes != expected_bytes:
        result(
            checks,
            kind,
            name,
            "size_mismatch",
            required,
            f"{path}: expected {expected_bytes}, found {actual_bytes}",
        )
        return
    if not skip_hashes and record.get("sha256"):
        actual_hash = sha256(path)
        if actual_hash != record["sha256"]:
            result(
                checks,
                kind,
                name,
                "hash_mismatch",
                required,
                f"{path}: expected {record['sha256']}, found {actual_hash}",
            )
            return
    suffix = "size only; hash skipped" if skip_hashes else "size and SHA-256"
    result(checks, kind, name, "ok", required, f"{path}: {suffix}")


def audit_workflow_contract(
    checks: list[dict[str, Any]],
    *,
    contract: dict[str, Any],
) -> None:
    required = bool(contract.get("required", True))
    path = REPO_ROOT / contract["path"]
    if not path.is_file():
        result(
            checks,
            "workflow_contract",
            contract["id"],
            "missing",
            required,
            str(path),
        )
        return
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result(
            checks,
            "workflow_contract",
            contract["id"],
            "invalid_json",
            required,
            f"{path}: {exc}",
        )
        return

    nodes_list = workflow.get("nodes")
    links_list = workflow.get("links")
    if not isinstance(nodes_list, list) or not isinstance(links_list, list):
        result(
            checks,
            "workflow_contract",
            contract["id"],
            "invalid_workflow",
            required,
            f"{path}: missing node/link arrays",
        )
        return
    try:
        nodes = {int(node["id"]): node for node in nodes_list}
    except (TypeError, ValueError, KeyError) as exc:
        result(
            checks,
            "workflow_contract",
            contract["id"],
            "invalid_workflow",
            required,
            f"{path}: invalid node id: {exc}",
        )
        return
    if len(nodes) != len(nodes_list):
        result(
            checks,
            "workflow_contract",
            contract["id"],
            "duplicate_node_id",
            required,
            str(path),
        )
        return

    errors: list[str] = []
    for expected in contract.get("nodes", []):
        node_id = int(expected["id"])
        node = nodes.get(node_id)
        if node is None:
            errors.append(f"missing node {node_id} ({expected['type']})")
            continue
        if node.get("type") != expected["type"]:
            errors.append(
                f"node {node_id} type expected {expected['type']!r}, "
                f"found {node.get('type')!r}"
            )
        if "mode" in expected and int(node.get("mode", 0)) != int(expected["mode"]):
            errors.append(
                f"node {node_id} mode expected {expected['mode']!r}, "
                f"found {node.get('mode', 0)!r}"
            )
        if "widgets_values" in expected and node.get("widgets_values") != expected["widgets_values"]:
            errors.append(
                f"node {node_id} widgets expected {expected['widgets_values']!r}, "
                f"found {node.get('widgets_values')!r}"
            )

    node_types = {str(node.get("type")) for node in nodes_list}
    for forbidden in contract.get("forbidden_node_types", []):
        if forbidden in node_types:
            errors.append(f"forbidden substituted node still present: {forbidden}")

    errors.extend(
        recursive_node_count_errors(
            workflow, contract.get("recursive_node_counts", {})
        )
    )
    workflow_subset = contract.get("workflow_subset")
    if workflow_subset is not None:
        errors.extend(
            _contract_subset_errors(
                workflow, workflow_subset, path="workflow"
            )
        )

    actual_links = {tuple(link) for link in links_list if isinstance(link, list)}
    for expected_link in contract.get("links", []):
        if tuple(expected_link) not in actual_links:
            errors.append(f"missing exact link {expected_link!r}")

    metadata_kind = contract.get("metadata_kind")
    if metadata_kind is not None:
        actual_kind = (
            workflow.get("extra", {})
            .get("castle_servasyy_article", {})
            .get("kind")
        )
        if actual_kind != metadata_kind:
            errors.append(
                f"metadata kind expected {metadata_kind!r}, found {actual_kind!r}"
            )

    result(
        checks,
        "workflow_contract",
        contract["id"],
        "contract_mismatch" if errors else "ok",
        required,
        f"{path}: " + ("; ".join(errors) if errors else "core nodes, widgets, modes, and links match"),
    )


def _contract_subset_errors(
    actual: Any, expected: Any, *, path: str
) -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path} expected object"]
        errors: list[str] = []
        for key, value in expected.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                errors.append(f"missing {child}")
                continue
            errors.extend(
                _contract_subset_errors(actual[key], value, path=child)
            )
        return errors
    if isinstance(expected, list):
        return [] if actual == expected else [f"{path} value mismatch"]
    return [] if actual == expected else [f"{path} value mismatch"]


def recursive_node_type_counts(workflow: Any) -> dict[str, int]:
    """Count node types across top-level and embedded subgraph definitions."""

    counts: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            node_type = value.get("type")
            if (
                isinstance(node_type, str)
                and "id" in value
                and ("inputs" in value or "outputs" in value)
            ):
                counts[node_type] = counts.get(node_type, 0) + 1
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(workflow)
    return counts


def recursive_node_count_errors(
    workflow: Any, expected_counts: Any
) -> list[str]:
    """Fail closed when a generated workflow gains or loses a pinned node."""

    if not isinstance(expected_counts, dict):
        return ["recursive_node_counts must be an object"]
    counts = recursive_node_type_counts(workflow)
    errors = []
    for node_type, expected in expected_counts.items():
        if not isinstance(node_type, str) or not isinstance(expected, int):
            errors.append("recursive_node_counts entries must be string integers")
            continue
        actual = counts.get(node_type, 0)
        if actual != expected:
            errors.append(
                f"recursive node {node_type!r} expected {expected}, found {actual}"
            )
    return errors


def audit_provenance_qualification_contract(
    checks: list[dict[str, Any]],
    *,
    provenance: dict[str, Any],
) -> None:
    contract = provenance.get("qualification_contract")
    if contract is None:
        return
    required = bool(provenance.get("required", True))
    name = f"{provenance['id']}-runtime-qualifications"
    path = REPO_ROOT / provenance["path"]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result(
            checks,
            "provenance_contract",
            name,
            "invalid_json",
            required,
            f"{path}: {exc}",
        )
        return
    if not isinstance(contract, dict) or not isinstance(document, dict):
        result(
            checks,
            "provenance_contract",
            name,
            "contract_invalid",
            required,
            f"{path}: qualification contract/document must be objects",
        )
        return

    errors = _contract_subset_errors(
        document, contract.get("document", {}), path="document"
    )
    expected_tasks = contract.get("tasks")
    actual_tasks = document.get("runtime_qualifications")
    if not isinstance(expected_tasks, list) or not isinstance(actual_tasks, list):
        errors.append("qualification task arrays are invalid")
    else:
        expected_map: dict[str, dict[str, Any]] = {}
        actual_map: dict[str, dict[str, Any]] = {}
        for label, records, target in (
            ("expected", expected_tasks, expected_map),
            ("actual", actual_tasks, actual_map),
        ):
            for record in records:
                task = record.get("task") if isinstance(record, dict) else None
                if not isinstance(task, str) or not task or task in target:
                    errors.append(f"{label} qualification tasks are invalid or duplicate")
                    continue
                target[task] = record
        if set(actual_map) != set(expected_map):
            errors.append(
                "qualification task set expected "
                f"{sorted(expected_map)!r}, found {sorted(actual_map)!r}"
            )
        for task, expected in expected_map.items():
            actual = actual_map.get(task)
            if actual is None:
                continue
            errors.extend(
                _contract_subset_errors(
                    actual, expected, path=f"runtime_qualifications[{task}]"
                )
            )

    result(
        checks,
        "provenance_contract",
        name,
        "qualification_contract_mismatch" if errors else "ok",
        required,
        f"{path}: "
        + (
            "; ".join(errors)
            if errors
            else "exact T2V, I2V-FLF and R2V qualification receipts match"
        ),
    )


def loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("live URL must be HTTP(S)")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname == "localhost"
    if not is_loopback:
        raise ValueError("live ComfyUI audit is restricted to loopback")
    return value.rstrip("/")


def ollama_tag_matches(
    models: list[Any], *, expected_tag: str, expected_digest: str
) -> bool:
    """Require one exact Ollama tag with its API's bare SHA-256 digest."""

    if (
        len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        return False
    matches = [
        item
        for item in models
        if isinstance(item, dict)
        and item.get("name") == expected_tag
        and item.get("digest") == expected_digest
    ]
    return len(matches) == 1


def python_module_available(python: Path, module: str) -> bool:
    """Probe one module in the pinned Comfy venv without importing Comfy/GPU."""

    probe = (
        "import importlib.util,json,sys\n"
        "try:\n"
        "    spec=importlib.util.find_spec(sys.argv[1])\n"
        "except ModuleNotFoundError:\n"
        "    spec=None\n"
        "print(json.dumps({'available': spec is not None}))\n"
    )
    completed = subprocess.run(
        [str(python), "-c", probe, module],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or not isinstance(
        payload.get("available"), bool
    ):
        raise ValueError("module probe returned an invalid payload")
    return payload["available"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--comfy-home", type=Path, default=DEFAULT_COMFY_HOME)
    parser.add_argument(
        "--profile",
        default="all",
        help=(
            "all, stock, servasyy_cache, larry, javawock, face_refine, "
            "motion, native_audio, or comparison"
        ),
    )
    parser.add_argument("--skip-hashes", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live-url", default="http://127.0.0.1:8188")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise SystemExit("unsupported tutorial-stack manifest schema")
    comfy_home = args.comfy_home.expanduser().resolve()
    checks: list[dict[str, Any]] = []

    allowed_profiles = set(manifest.get("profiles", {})) | {"all"}
    if args.profile not in allowed_profiles:
        raise SystemExit(f"unknown profile: {args.profile}")

    runtime = manifest.get("runtime", {})
    comfy_revision = git_value(comfy_home, "rev-parse", "HEAD")
    expected_comfy_revision = runtime.get("comfyui_revision")
    result(
        checks,
        "runtime",
        "comfyui-revision",
        "ok" if comfy_revision == expected_comfy_revision else "revision_mismatch",
        True,
        f"expected {expected_comfy_revision}, found {comfy_revision}",
    )

    for node in manifest.get("custom_nodes", []):
        if not selected(node, args.profile):
            continue
        required = bool(node.get("required", True))
        node_path = comfy_home / "custom_nodes" / node["destination"]
        resolved = node_path.resolve()
        if not node_path.exists():
            result(checks, "custom_node", node["id"], "missing", required, str(node_path))
            continue
        revision = git_value(resolved, "rev-parse", "HEAD")
        if revision != node["revision"]:
            result(
                checks,
                "custom_node",
                node["id"],
                "revision_mismatch",
                required,
                f"{resolved}: expected {node['revision']}, found {revision}",
            )
            continue
        origin = git_value(resolved, "remote", "get-url", "origin")
        if origin and origin.rstrip("/").removesuffix(".git") != node["repository"].rstrip("/").removesuffix(".git"):
            result(
                checks,
                "custom_node",
                node["id"],
                "origin_mismatch",
                required,
                f"{resolved}: expected {node['repository']}, found {origin}",
            )
            continue
        result(checks, "custom_node", node["id"], "ok", required, f"{resolved}: {revision}")

    for source in manifest.get("source_checkouts", []):
        if not selected(source, args.profile):
            continue
        required = bool(source.get("required", True))
        root = comfy_home if source.get("root") == "comfy_home" else REPO_ROOT
        source_path = (root / source["path"]).resolve()
        if not (source_path / ".git").exists():
            result(checks, "source_checkout", source["id"], "missing", required, str(source_path))
            continue
        revision = git_value(source_path, "rev-parse", "HEAD")
        if revision != source["revision"]:
            result(
                checks,
                "source_checkout",
                source["id"],
                "revision_mismatch",
                required,
                f"{source_path}: expected {source['revision']}, found {revision}",
            )
            continue
        origin = git_value(source_path, "remote", "get-url", "origin")
        expected_origin = source["repository"].rstrip("/").removesuffix(".git")
        actual_origin = (origin or "").rstrip("/").removesuffix(".git")
        if actual_origin != expected_origin:
            result(
                checks,
                "source_checkout",
                source["id"],
                "origin_mismatch",
                required,
                f"{source_path}: expected {source['repository']}, found {origin}",
            )
            continue
        dirty = git_value(source_path, "status", "--porcelain")
        if dirty:
            result(
                checks,
                "source_checkout",
                source["id"],
                "dirty",
                required,
                f"{source_path}: managed source checkout has local changes",
            )
            continue
        result(
            checks,
            "source_checkout",
            source["id"],
            "ok",
            required,
            f"{source_path}: {revision}",
        )

    baseline_path = REPO_ROOT / manifest["baseline_model_manifest"]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    for model in baseline.get("files", []):
        families = set(model.get("families", []))
        profile_families = set(manifest["profiles"].get(args.profile, {}).get("baseline_families", []))
        if args.profile != "all" and "common" not in families and not (families & profile_families):
            continue
        audit_file(
            checks,
            kind="baseline_model",
            name=model["destination"],
            path=comfy_home / "models" / model["destination"],
            record={**model, "required": True},
            skip_hashes=args.skip_hashes,
        )

    baseline_workflow_path = REPO_ROOT / manifest["baseline_workflow_manifest"]
    baseline_workflows = json.loads(
        baseline_workflow_path.read_text(encoding="utf-8")
    )
    if args.profile in {"all", "stock"}:
        for workflow in baseline_workflows.get("files", []):
            audit_file(
                checks,
                kind="baseline_workflow",
                name=workflow["destination"],
                path=REPO_ROOT / "workflows/minimax_h3/official" / workflow["destination"],
                record={**workflow, "required": True},
                skip_hashes=args.skip_hashes,
            )

    for model in manifest.get("model_assets", []):
        if not selected(model, args.profile):
            continue
        audit_file(
            checks,
            kind="tutorial_model",
            name=model["id"],
            path=comfy_home / "models" / model["destination"],
            record=model,
            skip_hashes=args.skip_hashes,
        )

    home_root = Path.home().resolve()
    for artifact in manifest.get("host_artifacts", []):
        if not selected(artifact, args.profile):
            continue
        required = bool(artifact.get("required", True))
        if artifact.get("root") != "home":
            result(
                checks,
                "host_artifact",
                artifact["id"],
                "root_invalid",
                required,
                "host artifacts must use the fixed home root",
            )
            continue
        path = (home_root / artifact["path"]).resolve()
        if path != home_root and home_root not in path.parents:
            result(
                checks,
                "host_artifact",
                artifact["id"],
                "path_invalid",
                required,
                str(path),
            )
            continue
        audit_file(
            checks,
            kind="host_artifact",
            name=artifact["id"],
            path=path,
            record=artifact,
            skip_hashes=args.skip_hashes,
        )

    for artifact in manifest.get("build_artifacts", []):
        if not selected(artifact, args.profile):
            continue
        root = comfy_home if artifact.get("root") == "comfy_home" else REPO_ROOT
        audit_file(
            checks,
            kind="build_artifact",
            name=artifact["id"],
            path=root / artifact["path"],
            record=artifact,
            skip_hashes=args.skip_hashes,
        )

    for workflow in manifest.get("workflows", []):
        if not selected(workflow, args.profile):
            continue
        audit_file(
            checks,
            kind="workflow",
            name=workflow["id"],
            path=REPO_ROOT / workflow["path"],
            record=workflow,
            skip_hashes=args.skip_hashes,
        )

    for provenance in manifest.get("provenance_files", []):
        if not selected(provenance, args.profile):
            continue
        audit_file(
            checks,
            kind="provenance",
            name=provenance["id"],
            path=REPO_ROOT / provenance["path"],
            record=provenance,
            skip_hashes=args.skip_hashes,
        )
        audit_provenance_qualification_contract(
            checks, provenance=provenance
        )

    for contract in manifest.get("workflow_contracts", []):
        if not selected(contract, args.profile):
            continue
        audit_workflow_contract(checks, contract=contract)

    for package in manifest.get("python_packages", []):
        if not selected(package, args.profile):
            continue
        required = bool(package.get("required", True))
        comfy_python = comfy_home / ".venv/bin/python"
        package_probe = (
            "import importlib.metadata as m,json,sys; "
            "d=m.distribution(sys.argv[1]); "
            "u=d.read_text('direct_url.json'); "
            "print(json.dumps({'version':d.version,"
            "'direct_url':json.loads(u) if u else None}))"
        )
        try:
            completed = subprocess.run(
                [str(comfy_python), "-c", package_probe, package["distribution"]],
                check=True,
                capture_output=True,
                text=True,
            )
            package_metadata = json.loads(completed.stdout)
            version = package_metadata["version"]
        except (
            OSError,
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            KeyError,
        ):
            result(checks, "python_package", package["id"], "missing", required, package["distribution"])
            continue
        expected = package.get("version")
        status = "ok" if expected is None or version == expected else "version_mismatch"
        detail = f"expected {expected}, found {version}"
        expected_direct_path = package.get("direct_url_path")
        if status == "ok" and expected_direct_path:
            direct_url = (package_metadata.get("direct_url") or {}).get("url", "")
            parsed_direct_url = urlparse(direct_url)
            expected_path = (comfy_home / expected_direct_path).resolve()
            actual_path = (
                Path(unquote(parsed_direct_url.path)).resolve()
                if parsed_direct_url.scheme == "file"
                else None
            )
            if actual_path != expected_path:
                status = "source_mismatch"
                detail += f"; expected direct source {expected_path}, found {direct_url or 'none'}"
            else:
                detail += f"; direct source {actual_path}"
        result(checks, "python_package", package["id"], status, required, detail)

    comfy_python = comfy_home / ".venv/bin/python"
    for capability in manifest.get("capability_contracts", []):
        if not selected(capability, args.profile):
            continue
        required = bool(capability.get("required", True))
        if capability.get("kind") != "python_module_absent":
            result(
                checks,
                "runtime_capability",
                capability["id"],
                "kind_invalid",
                required,
                str(capability.get("kind")),
            )
            continue
        module = capability.get("module")
        if not isinstance(module, str) or not module:
            result(
                checks,
                "runtime_capability",
                capability["id"],
                "contract_invalid",
                required,
                "python_module_absent requires a module name",
            )
            continue
        try:
            available = python_module_available(comfy_python, module)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
            result(
                checks,
                "runtime_capability",
                capability["id"],
                "probe_failed",
                required,
                str(exc),
            )
            continue
        result(
            checks,
            "runtime_capability",
            capability["id"],
            "unexpectedly_available" if available else "ok",
            required,
            (
                f"{module} unexpectedly became importable; review and requalify "
                "before changing the Sage-only derivative"
                if available
                else f"{module} is absent as pinned; generated workflow is Sage-only"
            ),
        )

    if not args.offline:
        live_url = loopback_url(args.live_url)
        try:
            with urlopen(f"{live_url}/object_info", timeout=20) as response:
                object_info = json.load(response)
        except Exception as exc:  # urllib exposes several transport exceptions
            result(checks, "live_api", "object_info", "unreachable", True, str(exc))
        else:
            for node_class in manifest.get("required_node_classes", []):
                if not selected(node_class, args.profile):
                    continue
                present = node_class["id"] in object_info
                schema_errors: list[str] = []
                if present:
                    node_info = object_info[node_class["id"]]
                    input_info = node_info.get("input", {})
                    required_inputs = input_info.get("required", {})
                    optional_inputs = input_info.get("optional", {})
                    all_inputs = {**required_inputs, **optional_inputs}
                    for input_name in node_class.get("required_inputs", []):
                        if input_name not in all_inputs:
                            schema_errors.append(f"missing input {input_name}")
                    for input_name, expected_default in node_class.get(
                        "input_defaults", {}
                    ).items():
                        schema = all_inputs.get(input_name)
                        actual_default = (
                            schema[1].get("default")
                            if isinstance(schema, list)
                            and len(schema) > 1
                            and isinstance(schema[1], dict)
                            else None
                        )
                        if actual_default != expected_default:
                            schema_errors.append(
                                f"{input_name}.default expected {expected_default!r}, "
                                f"found {actual_default!r}"
                            )
                status = (
                    "missing"
                    if not present
                    else "schema_mismatch"
                    if schema_errors
                    else "ok"
                )
                result(
                    checks,
                    "live_node",
                    node_class["id"],
                    status,
                    bool(node_class.get("required", True)),
                    (
                        "; ".join(schema_errors)
                        if schema_errors
                        else "registered with required schema"
                        if present
                        else "not returned by /object_info"
                    ),
                )
        for service in manifest.get("live_services", []):
            if not selected(service, args.profile):
                continue
            required = bool(service.get("required", True))
            if service.get("kind") != "ollama_tag":
                result(
                    checks,
                    "live_service",
                    service["id"],
                    "kind_invalid",
                    required,
                    str(service.get("kind")),
                )
                continue
            try:
                service_url = loopback_url(service["url"])
                if service_url != "http://127.0.0.1:11434":
                    raise ValueError("Ollama audit requires the fixed loopback origin")
                with urlopen(f"{service_url}/api/tags", timeout=10) as response:
                    payload = response.read(2 * 1024 * 1024 + 1)
                if len(payload) > 2 * 1024 * 1024:
                    raise ValueError("Ollama tags response is too large")
                tags = json.loads(payload)
                models = tags.get("models") if isinstance(tags, dict) else None
                if not isinstance(models, list):
                    raise ValueError("Ollama tags response is invalid")
            except Exception as exc:
                result(
                    checks,
                    "live_service",
                    service["id"],
                    "unreachable",
                    required,
                    str(exc),
                )
                continue
            expected_tag = service["tag"]
            expected_digest = service["manifest_digest"]
            tag_matches = ollama_tag_matches(
                models,
                expected_tag=expected_tag,
                expected_digest=expected_digest,
            )
            result(
                checks,
                "live_service",
                service["id"],
                "ok" if tag_matches else "tag_missing",
                required,
                (
                    f"{expected_tag} at {expected_digest}"
                    if tag_matches
                    else f"expected exact tag {expected_tag} at {expected_digest}"
                ),
            )

    active_blockers = active_declared_blockers(manifest, args.profile)
    for blocker in active_blockers:
        result(
            checks,
            "declared_blocker",
            str(blocker.get("id") or "unnamed"),
            str(blocker.get("status") or "blocked"),
            True,
            str(blocker.get("reason") or "profile is declared blocked"),
        )

    failures = [
        check
        for check in checks
        if check["required"] and check["status"] != "ok"
    ]
    report = {
        "schema_version": 1,
        "profile": args.profile,
        "ready": not failures,
        "hashes_skipped": args.skip_hashes,
        "live_skipped": args.offline,
        "summary": {
            "checks": len(checks),
            "passed": sum(item["status"] == "ok" for item in checks),
            "required_failures": len(failures),
            "active_declared_blockers": len(active_blockers),
        },
        "checks": checks,
        "declared_blockers": manifest.get("declared_blockers", []),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        for check in checks:
            marker = "OK" if check["status"] == "ok" else "FAIL"
            optional = "" if check["required"] else " (optional)"
            print(f"[{marker}] {check['kind']} {check['name']}{optional}: {check['detail']}")
        print(
            f"ready={str(report['ready']).lower()} "
            f"checks={report['summary']['checks']} "
            f"required_failures={report['summary']['required_failures']}"
        )
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
