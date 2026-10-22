"""Fail-closed Castle render releases projected onto the exact Oracle schema."""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .errors import SceneArtError, fail
from .protocol import (
    CAPABILITY_SCHEMA,
    HASH,
    MAX_OUTPUT_BYTES,
    OUTPUT_HEIGHT,
    OUTPUT_MIME,
    OUTPUT_WIDTH,
    RENDER_PLAN_SCHEMA,
    RENDER_RELEASE_SCHEMA,
    RENDER_REQUEST_SCHEMA,
    SAFE_ID,
    SEED_POLICY,
    WORKFLOW_CONTRACT_REVISION,
)
from .secure_path import (
    DIRECTORY_FLAGS as _DIRECTORY_FLAGS,
    READ_FILE_FLAGS as _FILE_FLAGS,
    close_descriptors,
    open_absolute_directory as _open_absolute_directory,
)


MANIFEST_SCHEMA = "castle.novel-scene-art-render-release.v3"
RUNTIME_BINDING_SCHEMA = "castle.novel-scene-art-runtime-binding.v1"
RUNTIME_RELEASE_BINDING_SCHEMA = "castle.novel-scene-art-private-release-binding.v1"
RUNTIME_QUALIFICATION_SCHEMA = "castle.novel-scene-art-runtime-qualification.v1"
MAX_MANIFEST_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024**3
MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
MAX_RUNTIME_MODULE_BYTES = 2 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_RUNTIME_RELEASE_PARENT = Path("/opt/web-comfyui/novel-scene-art/releases")
SAFE_RELEASE_DIRECTORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SAFE_BINDING = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
CANONICAL_PERSONA_IDS = frozenset(
    {
        "claude",
        "chatgpt",
        "gemini",
        "grok",
        "deepseek",
        "qwen",
        "mistral",
        "venice",
        "sakana",
        "perplexity",
        "glm",
        "kimi",
        "hunyuan",
        "minimax",
        "nemotron",
        "cohere",
        "mimo",
    }
)

ARTIFACT_KEYS = frozenset({"id", "path", "sha256"})
QUALIFICATION_KEYS = frozenset({"status", "receipt"})
RUNTIME_MODULE_KEYS = frozenset({"id", "path", "sha256"})
RUNTIME_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "revision",
        "modules",
        "module_manifest_sha256",
        "release_binding_sha256",
        "qualification",
    }
)
RUNTIME_QUALIFICATION_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "runtime_id",
        "runtime_revision",
        "module_manifest_sha256",
        "release_binding_sha256",
        "review_policy_revision",
        "evidence_sha256",
    }
)
COMPONENT_KEYS = frozenset(
    {"id", "revision", "path", "sha256", "provenance_ref", "license_ref", "qualification"}
)
MODEL_KEYS = frozenset(
    {
        "id",
        "role",
        "comfy_name",
        "path",
        "sha256",
        "provenance_ref",
        "license_ref",
        "visual_class",
        "qualification",
    }
)
IDENTITY_ARTIFACT_KEYS = frozenset({"id", "path", "sha256", "loader", "comfy_name"})
MODEL_CATALOG_DIRECTORIES = {
    "checkpoint": "checkpoints",
    "vae": "vae",
    "clip_vision": "clip_vision",
    "ipadapter": "ipadapter",
    "controlnet": "controlnet",
    "lora": "loras",
}
IDENTITY_LOADERS = frozenset({"input_image", "lora"})
ACTIVATION_BLOCKERS = (
    "runtime_implementation_binding_unqualified",
    "comfy_identity_staging_unqualified",
    "comfy_prompt_validation_canary_missing",
    "sqlite_journal_dirfd_reopen_unqualified",
    "comfy_orphan_reconciliation_unqualified",
    "exclusive_comfy_instance_unqualified",
    "worker_credential_isolation_unqualified",
)
IDENTITY_KEYS = frozenset(
    {
        "persona_id",
        "identity_pack_revision",
        "adult_attestation_revision",
        "provenance_ref",
        "license_ref",
        "artifacts",
        "bindings",
        "qualification",
    }
)
TEMPLATE_KEYS = frozenset(
    {
        "id",
        "scene_families",
        "identity_slot_counts",
        "positive_prompt",
        "negative_prompt",
        "graph_bindings",
        "output_node_id",
        "content_class",
        "review_policy_revision",
        "qualification",
    }
)
TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "approved",
        "profile_id",
        "adapter",
        "workflow",
        "compiler",
        "model_manifest",
        "identity_manifest",
        "template_catalog",
        "screening",
        "runtime_binding",
        "max_identity_slots",
        "output",
    }
)

# This is deliberately a fixed, code-reviewed disk-source inventory rather
# than directory discovery. It is not the complete Python import closure or a
# pre-execution trust anchor. Changing a listed source requires a new runtime
# manifest and qualification receipt.
RUNTIME_MODULE_LAYOUT = (
    ("entrypoint", "scripts/run_novel_scene_art_worker.py"),
    ("package", "webui/novel_scene_art/__init__.py"),
    ("comfy_client", "webui/novel_scene_art/comfy_client.py"),
    ("compiler", "webui/novel_scene_art/compiler.py"),
    ("contracts", "webui/novel_scene_art/contracts.py"),
    ("errors", "webui/novel_scene_art/errors.py"),
    ("gpu_lock", "webui/novel_scene_art/gpu_lock.py"),
    ("journal", "webui/novel_scene_art/journal.py"),
    ("manifests", "webui/novel_scene_art/manifests.py"),
    ("png", "webui/novel_scene_art/png.py"),
    ("protocol", "webui/novel_scene_art/protocol.py"),
    ("screening", "webui/novel_scene_art/screening.py"),
    ("secure_path", "webui/novel_scene_art/secure_path.py"),
    ("transport", "webui/novel_scene_art/transport.py"),
    ("worker", "webui/novel_scene_art/worker.py"),
)
RUNTIME_MODULE_IMPORTS = {
    "entrypoint": "__main__",
    "package": "novel_scene_art",
    **{
        module_id: f"novel_scene_art.{module_id}"
        for module_id, _relative_path in RUNTIME_MODULE_LAYOUT[2:]
    },
}


def default_artifact_roots() -> tuple[Path, ...]:
    private_root = Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    ) / "web-comfyui/novel-scene-art"
    return (
        REPO_ROOT / "workflows",
        Path.home() / "comfyui/models",
        private_root,
    )


def canonical_json(value: object) -> bytes:
    """Match the server's recursively sorted, compact UTF-8 JSON bytes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise SceneArtError("manifest_invalid", "Value is not canonical JSON") from error


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def json_no_duplicates(raw: bytes, *, code: str = "manifest_invalid") -> object:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        reject_invalid_unicode(value)
        return value
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise SceneArtError(code, "JSON is invalid") from error


def reject_invalid_unicode(value: object) -> None:
    """Reject escaped lone surrogates that Python's JSON decoder otherwise accepts."""

    if isinstance(value, str):
        value.encode("utf-8", "strict")
        return
    if isinstance(value, list):
        for item in value:
            reject_invalid_unicode(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key.encode("utf-8", "strict")
            reject_invalid_unicode(item)


def _exact_object(value: object, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail("manifest_invalid", f"{label} shape is invalid")
    return value


def _safe(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        fail("manifest_invalid", f"{label} is invalid")
    return value


def _absolute(path: Path, label: str) -> Path:
    if not path.is_absolute() or "\x00" in os.fspath(path):
        fail("manifest_invalid", f"{label} path must be absolute")
    return Path(os.path.abspath(os.fspath(path)))


def _open_file_beneath(
    path: Path,
    root: Path,
    *,
    expected_uid: int | None = None,
) -> list[int]:
    """Bind a regular-file candidate beneath an owner-controlled root."""

    path = _absolute(path, "artifact")
    root = _absolute(root, "artifact root")
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail("manifest_path_outside_roots", "Artifact escaped approved artifact roots")
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} or "/" in part for part in parts):
        fail("manifest_path_unsafe", "An artifact path component is invalid")
    descriptors: list[int] = []
    owner_uid = os.geteuid() if expected_uid is None else expected_uid
    try:
        descriptors = _open_absolute_directory(
            root,
            code="manifest_path_unsafe",
            message="An artifact directory component is unsafe",
        )
        descriptor = descriptors[-1]
        root_info = os.fstat(descriptor)
        if root_info.st_uid != owner_uid or root_info.st_mode & 0o022:
            fail("manifest_path_unsafe", "An artifact root is not owner-controlled")
        for component in parts[:-1]:
            descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            descriptors.append(descriptor)
            directory_info = os.fstat(descriptor)
            if directory_info.st_uid != owner_uid or directory_info.st_mode & 0o022:
                fail("manifest_path_unsafe", "An artifact directory is not owner-controlled")
        descriptor = os.open(parts[-1], _FILE_FLAGS, dir_fd=descriptor)
        descriptors.append(descriptor)
        return descriptors
    except BaseException:
        close_descriptors(descriptors)
        raise


def _approved_file(value: object, label: str, roots: tuple[Path, ...]) -> tuple[Path, Path]:
    if not isinstance(value, str) or not value or "\x00" in value:
        fail("manifest_invalid", f"{label} path is invalid")
    path = _absolute(Path(value), label)
    for candidate in roots:
        try:
            root = _absolute(candidate, "approved root")
            if os.path.commonpath((str(root), str(path))) != str(root) or path == root:
                continue
            return path, root
        except (OSError, ValueError, SceneArtError):
            continue
    fail("manifest_path_outside_roots", f"{label} escaped approved artifact roots")


def _stable_file_sha256(
    path: Path,
    root: Path,
    *,
    maximum: int,
    expected_uid: int | None = None,
) -> str:
    descriptors: list[int] = []
    try:
        descriptors = _open_file_beneath(path, root, expected_uid=expected_uid)
        descriptor = descriptors[-1]
    except OSError as error:
        raise SceneArtError("manifest_unavailable", "A pinned file is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != (os.geteuid() if expected_uid is None else expected_uid)
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            fail("manifest_unsafe", "A pinned artifact is not an immutable owner file")
        digestor = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, 8 * 1024 * 1024):
            total += len(chunk)
            digestor.update(chunk)
        after = os.fstat(descriptor)
        bound = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if total != before.st_size or bound(before) != bound(after):
            fail("manifest_changed", "A pinned artifact changed while being read")
        return digestor.hexdigest()
    finally:
        close_descriptors(descriptors)


def _stable_file_bytes(path: Path, root: Path, *, maximum: int) -> bytes:
    """Read one small pinned artifact through retained beneath-root descriptors."""

    descriptors: list[int] = []
    try:
        descriptors = _open_file_beneath(path, root)
        descriptor = descriptors[-1]
    except OSError as error:
        raise SceneArtError("manifest_unavailable", "A pinned file is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            fail("manifest_unsafe", "A pinned artifact is not a bounded owner file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        bound = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if len(raw) != before.st_size or bound(before) != bound(after):
            fail("manifest_changed", "A pinned artifact changed while being read")
        return raw
    finally:
        close_descriptors(descriptors)


def _manifest_bytes(path: Path, *, require_private: bool) -> bytes:
    path = _absolute(path, "manifest")
    descriptors: list[int] = []
    try:
        descriptors = _open_absolute_directory(
            path.parent,
            code="manifest_unavailable",
            message="Render manifest directory is unavailable",
        )
        parent = os.fstat(descriptors[-1])
        if require_private and (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o022
        ):
            fail("manifest_unsafe", "Render manifest directory is not owner-controlled")
        descriptor = os.open(path.name, _FILE_FLAGS, dir_fd=descriptors[-1])
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or (require_private and stat.S_IMODE(before.st_mode) != 0o600)
            or before.st_size <= 0
            or before.st_size > MAX_MANIFEST_BYTES
        ):
            fail("manifest_unsafe", "Render manifest must be an owner-private regular file")
        chunks: list[bytes] = []
        remaining = MAX_MANIFEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            fail("manifest_changed", "Render manifest changed while being read")
        return raw
    except SceneArtError:
        raise
    except OSError as error:
        raise SceneArtError("manifest_unavailable", "Render manifest is unavailable") from error
    finally:
        close_descriptors(descriptors)


def _private_manifest_bytes(path: Path) -> bytes:
    return _manifest_bytes(path, require_private=True)


def _public_manifest_bytes(path: Path) -> bytes:
    return _manifest_bytes(path, require_private=False)


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    path: Path
    sha256: str
    approved_root: Path
    maximum_bytes: int
    comfy_name: str | None = None
    loader: str | None = None
    expected_uid: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    runtime_id: str
    revision: str
    module_manifest_sha256: str
    release_binding_sha256: str
    qualification_receipt_sha256: str
    binding_digest: str
    modules: tuple[Artifact, ...]
    public_binding: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class IdentityPack:
    persona_id: str
    revision: str
    artifact_manifest_sha256: str
    artifacts: tuple[Artifact, ...]
    bindings: Mapping[str, str]
    public_identity: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SceneTemplate:
    template_id: str
    scene_families: tuple[str, ...]
    identity_slot_counts: tuple[int, ...]
    positive_prompt: str
    negative_prompt: str
    graph_bindings: Mapping[str, object]
    output_node_id: str
    review_policy_revision: str
    qualification_receipt_sha256: str
    digest: str


@dataclass(frozen=True, slots=True)
class RenderRelease:
    profile_id: str
    adapter: Mapping[str, str]
    adapter_artifact: Artifact
    workflow: Artifact
    workflow_graph: Mapping[str, object]
    compiler: Mapping[str, str]
    compiler_artifact: Artifact
    models: tuple[tuple[str, Artifact], ...]
    identities: Mapping[tuple[str, str], IdentityPack]
    templates: tuple[SceneTemplate, ...]
    screening: Artifact
    screening_policy_revision: str
    runtime_binding: RuntimeBinding
    public_release: Mapping[str, object]
    release_digest: str
    capability: Mapping[str, object]
    capability_digest: str
    verified_artifacts: tuple[Artifact, ...]
    source_manifest_path: Path
    source_manifest_sha256: str
    activation_blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManifestPreflight:
    ready: bool
    blockers: tuple[str, ...]
    release: RenderRelease | None = None

    def public_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "ready": self.ready,
            "blockers": list(self.blockers),
        }
        if self.release is not None:
            value.update(
                {
                    "renderer_release_digest": self.release.release_digest,
                    "render_release": self.release.public_release,
                    "capability_digest": self.release.capability_digest,
                    "runtime_binding_digest": self.release.runtime_binding.binding_digest,
                    "runtime_release_binding_sha256": (
                        self.release.runtime_binding.release_binding_sha256
                    ),
                }
            )
        return value


def _artifact(
    value: Mapping[str, Any],
    label: str,
    roots: tuple[Path, ...],
    *,
    keys: frozenset[str],
    verify_files: bool,
    maximum: int = MAX_ARTIFACT_BYTES,
    comfy_name: str | None = None,
    loader: str | None = None,
) -> Artifact:
    item = _exact_object(value, keys, label)
    artifact_id = _safe(item.get("id"), SAFE_ID, f"{label}.id")
    expected = _safe(item.get("sha256"), HASH, f"{label}.sha256")
    path, root = _approved_file(item.get("path"), label, roots)
    if verify_files and _stable_file_sha256(path, root, maximum=maximum) != expected:
        fail("manifest_digest_mismatch", f"{label} digest does not match")
    return Artifact(artifact_id, path, expected, root, maximum, comfy_name, loader)


def _comfy_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8", "strict")) > 256:
        fail("manifest_invalid", f"{label} is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
        or "\x00" in value
        or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", part) for part in path.parts)
    ):
        fail("manifest_invalid", f"{label} is not a safe Comfy catalog name")
    return value


def _require_catalog_path(artifact: Artifact, category: str) -> None:
    if artifact.comfy_name is None or artifact.approved_root.name != "models":
        fail("manifest_catalog_mismatch", "A Comfy model is outside its exact catalog root")
    expected = Path(
        os.path.abspath(
            os.fspath(artifact.approved_root / category / PurePosixPath(artifact.comfy_name))
        )
    )
    if expected != artifact.path:
        fail("manifest_catalog_mismatch", "A Comfy model name does not bind its pinned file")


def _reference(value: object, label: str) -> str:
    return _safe(value, SAFE_ID, label)


def _qualification(
    value: object,
    label: str,
    roots: tuple[Path, ...],
    *,
    verify_files: bool,
) -> Artifact:
    item = _exact_object(value, QUALIFICATION_KEYS, label)
    if item.get("status") != "passed":
        fail("manifest_not_qualified", f"{label} has not passed qualification")
    return _artifact(
        item.get("receipt"),
        f"{label}.receipt",
        roots,
        keys=ARTIFACT_KEYS,
        verify_files=verify_files,
    )


def _component(
    value: object,
    label: str,
    roots: tuple[Path, ...],
    *,
    verify_files: bool,
    workflow: bool = False,
) -> tuple[Mapping[str, str], Artifact, Artifact]:
    item = _exact_object(value, COMPONENT_KEYS, label)
    revision = _safe(item.get("revision"), SAFE_ID, f"{label}.revision")
    _reference(item.get("provenance_ref"), f"{label}.provenance_ref")
    _reference(item.get("license_ref"), f"{label}.license_ref")
    qualification = _qualification(
        item.get("qualification"),
        f"{label}.qualification",
        roots,
        verify_files=verify_files,
    )
    artifact = _artifact(
        item,
        label,
        roots,
        keys=COMPONENT_KEYS,
        verify_files=verify_files,
        maximum=MAX_WORKFLOW_BYTES if workflow else MAX_MANIFEST_BYTES,
    )
    public = {
        "id": artifact.artifact_id,
        "revision": revision,
        ("graph_sha256" if workflow else "manifest_sha256"): artifact.sha256,
    }
    return public, artifact, qualification


def runtime_release_binding_sha256(manifest: Mapping[str, Any]) -> str:
    """Bind all private release material without creating a receipt cycle.

    The runtime-binding object is excluded because it contains the
    qualification receipt that attests this digest. Every other private
    manifest field, including artifact paths, digests, provenance,
    qualifications, output policy, and approval bit, remains covered.
    """

    material = {
        key: manifest.get(key)
        for key in sorted(TOP_LEVEL_KEYS - {"runtime_binding"})
    }
    return sha256_json(
        {
            "schema_version": RUNTIME_RELEASE_BINDING_SCHEMA,
            "release_material": material,
        }
    )


def current_runtime_module_rows(
    *, source_root: Path = REPO_ROOT
) -> tuple[Mapping[str, str], ...]:
    """Return the exact disk-source inventory for qualification tooling.

    This is intentionally read-only. It does not create or approve a receipt.
    Production loading uses the same fixed layout and repeats every hash.
    """

    root = _absolute(source_root, "runtime source root")
    rows: list[Mapping[str, str]] = []
    for module_id, relative_path in RUNTIME_MODULE_LAYOUT:
        path = root / PurePosixPath(relative_path)
        rows.append(
            {
                "id": module_id,
                "path": relative_path,
                "sha256": _stable_file_sha256(
                    path,
                    root,
                    maximum=MAX_RUNTIME_MODULE_BYTES,
                ),
            }
        )
    return tuple(rows)


def validate_production_runtime_source_root(
    source_root: Path,
    *,
    release_parent: Path = PRODUCTION_RUNTIME_RELEASE_PARENT,
    expected_uid: int = 0,
) -> int:
    """Require one exact root-owned release directory, never a moving symlink."""

    root = _absolute(source_root, "runtime source root")
    parent = _absolute(release_parent, "runtime release parent")
    if (
        root.parent != parent
        or SAFE_RELEASE_DIRECTORY.fullmatch(root.name) is None
    ):
        fail(
            "runtime_source_root_unqualified",
            "Runtime source is not an exact installed release path",
        )
    descriptors: list[int] = []
    try:
        descriptors = _open_absolute_directory(
            parent,
            code="runtime_source_root_unqualified",
            message="Runtime release parent is unavailable or contains a symlink",
        )
        parent_descriptor = descriptors[-1]
        parent_info = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != expected_uid
            or parent_info.st_mode & 0o022
        ):
            fail(
                "runtime_source_root_unqualified",
                "Runtime release parent is not root-controlled",
            )
        root_descriptor = os.open(root.name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        descriptors.append(root_descriptor)
        root_info = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != expected_uid
            or root_info.st_mode & 0o022
        ):
            fail(
                "runtime_source_root_unqualified",
                "Runtime release directory is not root-controlled",
            )
    except SceneArtError:
        raise
    except OSError as error:
        raise SceneArtError(
            "runtime_source_root_unqualified",
            "Runtime release directory is unavailable or is a symlink",
        ) from error
    finally:
        close_descriptors(descriptors)
    return expected_uid


def validate_production_runtime_invocation(source_root: Path) -> None:
    """Pin import origin and interpreter isolation for production evidence.

    Source hashes remain disk-source evidence. This invocation fence ensures
    that production starts the exact entrypoint from that root with Python's
    isolated/no-site/no-bytecode flags and that each listed loaded package
    module reports the same reviewed source path. It does not prove the full
    Python import closure.
    """

    root = _absolute(source_root, "runtime source root")
    expected_entrypoint = root / RUNTIME_MODULE_LAYOUT[0][1]
    argv0 = Path(sys.argv[0])
    flags = sys.flags
    if (
        Path(sys.executable) != Path("/usr/bin/python3")
        or not argv0.is_absolute()
        or Path(os.path.abspath(os.fspath(argv0))) != expected_entrypoint
        or not flags.isolated
        or not flags.no_site
        or not flags.dont_write_bytecode
        or not flags.safe_path
    ):
        fail(
            "runtime_invocation_unqualified",
            "Runtime was not started by the pinned isolated interpreter command",
        )
    expected_import_root = root / "webui"
    if not sys.path or Path(sys.path[0]) != expected_import_root:
        fail(
            "runtime_invocation_unqualified",
            "Runtime package import root is not exact",
        )
    for module_id, relative_path in RUNTIME_MODULE_LAYOUT:
        module_name = RUNTIME_MODULE_IMPORTS[module_id]
        module = sys.modules.get(module_name)
        origin = getattr(module, "__file__", None)
        loader = getattr(module, "__loader__", None)
        expected_path = root / relative_path
        if (
            not isinstance(origin, str)
            or not Path(origin).is_absolute()
            or Path(os.path.abspath(origin)) != expected_path
            or not isinstance(loader, importlib.machinery.SourceFileLoader)
        ):
            fail(
                "runtime_invocation_unqualified",
                "A loaded runtime module did not originate from the pinned release",
            )
        source_path = root / relative_path
        if source_path.with_suffix(".pyc").exists() or (source_path.parent / "__pycache__").exists():
            fail(
                "runtime_invocation_unqualified",
                "Runtime release contains bytecode cache material",
            )


def _runtime_binding(
    value: object,
    manifest: Mapping[str, Any],
    roots: tuple[Path, ...],
    *,
    verify_files: bool,
    source_root: Path,
    require_production_runtime: bool,
) -> tuple[RuntimeBinding, Artifact]:
    item = _exact_object(value, RUNTIME_BINDING_KEYS, "runtime_binding")
    if item.get("schema_version") != RUNTIME_BINDING_SCHEMA:
        fail("runtime_binding_invalid", "Runtime binding schema is invalid")
    runtime_id = _safe(item.get("id"), SAFE_ID, "runtime_binding.id")
    revision = _safe(item.get("revision"), SAFE_ID, "runtime_binding.revision")
    rows = item.get("modules")
    if not isinstance(rows, list) or len(rows) != len(RUNTIME_MODULE_LAYOUT):
        fail("runtime_binding_invalid", "Runtime module inventory is incomplete")

    root = _absolute(source_root, "runtime source root")
    if require_production_runtime:
        runtime_owner_uid = validate_production_runtime_source_root(root)
        validate_production_runtime_invocation(root)
    else:
        # Test-only release fixtures may use a temporary owner-controlled root,
        # but the resulting binding is never production qualification.
        runtime_owner_uid = os.geteuid()
    normalized_rows: list[Mapping[str, str]] = []
    modules: list[Artifact] = []
    for index, ((expected_id, expected_path), raw) in enumerate(
        zip(RUNTIME_MODULE_LAYOUT, rows, strict=True)
    ):
        row = _exact_object(raw, RUNTIME_MODULE_KEYS, f"runtime_binding.modules[{index}]")
        module_id = _safe(row.get("id"), SAFE_ID, f"runtime_binding.modules[{index}].id")
        relative_path = row.get("path")
        expected_sha256 = _safe(
            row.get("sha256"), HASH, f"runtime_binding.modules[{index}].sha256"
        )
        if module_id != expected_id or relative_path != expected_path:
            fail("runtime_binding_invalid", "Runtime module inventory does not match code")
        path = root / PurePosixPath(expected_path)
        if verify_files:
            actual_sha256 = _stable_file_sha256(
                path,
                root,
                maximum=MAX_RUNTIME_MODULE_BYTES,
                expected_uid=runtime_owner_uid,
            )
            if actual_sha256 != expected_sha256:
                fail(
                    "runtime_module_digest_mismatch",
                    "An executable runtime module does not match its qualification",
                )
        normalized_rows.append(
            {"id": module_id, "path": expected_path, "sha256": expected_sha256}
        )
        modules.append(
            Artifact(
                artifact_id=module_id,
                path=path,
                sha256=expected_sha256,
                approved_root=root,
                maximum_bytes=MAX_RUNTIME_MODULE_BYTES,
                expected_uid=runtime_owner_uid,
            )
        )

    module_manifest_sha256 = _safe(
        item.get("module_manifest_sha256"), HASH, "runtime_binding.module_manifest_sha256"
    )
    if module_manifest_sha256 != sha256_json(normalized_rows):
        fail("runtime_binding_digest_mismatch", "Runtime module manifest digest is invalid")
    release_binding_sha256 = _safe(
        item.get("release_binding_sha256"), HASH, "runtime_binding.release_binding_sha256"
    )
    if release_binding_sha256 != runtime_release_binding_sha256(manifest):
        fail("runtime_release_binding_mismatch", "Runtime qualification targets another release")

    qualification = _qualification(
        item.get("qualification"),
        "runtime_binding.qualification",
        roots,
        verify_files=verify_files,
    )
    receipt_raw = _stable_file_bytes(
        qualification.path,
        qualification.approved_root,
        maximum=MAX_MANIFEST_BYTES,
    )
    if hashlib.sha256(receipt_raw).hexdigest() != qualification.sha256:
        fail("manifest_digest_mismatch", "Runtime qualification receipt digest changed")
    receipt = _exact_object(
        json_no_duplicates(receipt_raw, code="runtime_qualification_invalid"),
        RUNTIME_QUALIFICATION_RECEIPT_KEYS,
        "runtime qualification receipt",
    )
    if (
        receipt.get("schema_version") != RUNTIME_QUALIFICATION_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("runtime_id") != runtime_id
        or receipt.get("runtime_revision") != revision
        or receipt.get("module_manifest_sha256") != module_manifest_sha256
        or receipt.get("release_binding_sha256") != release_binding_sha256
        or not isinstance(receipt.get("review_policy_revision"), str)
        or SAFE_ID.fullmatch(str(receipt.get("review_policy_revision"))) is None
        or not isinstance(receipt.get("evidence_sha256"), str)
        or HASH.fullmatch(str(receipt.get("evidence_sha256"))) is None
    ):
        fail(
            "runtime_qualification_mismatch",
            "Runtime qualification receipt does not bind this code and release",
        )
    public_binding: Mapping[str, object] = {
        "schema_version": RUNTIME_BINDING_SCHEMA,
        "id": runtime_id,
        "revision": revision,
        "module_manifest_sha256": module_manifest_sha256,
        "release_binding_sha256": release_binding_sha256,
        "qualification_receipt_sha256": qualification.sha256,
    }
    return (
        RuntimeBinding(
            runtime_id,
            revision,
            module_manifest_sha256,
            release_binding_sha256,
            qualification.sha256,
            sha256_json(public_binding),
            tuple(modules),
            public_binding,
        ),
        qualification,
    )


def _parse_complete(
    manifest: Mapping[str, Any],
    source_path: Path,
    source_sha256: str,
    *,
    roots: tuple[Path, ...],
    verify_files: bool,
    runtime_source_root: Path,
    require_production_runtime: bool,
) -> RenderRelease:
    if set(manifest) != TOP_LEVEL_KEYS or manifest.get("schema_version") != MANIFEST_SCHEMA:
        fail("manifest_invalid", "Render release manifest schema is invalid")
    if manifest.get("approved") is not True:
        fail("manifest_not_approved", "Render release is not approved")
    profile_id = _safe(manifest.get("profile_id"), SAFE_ID, "profile_id")
    adapter, adapter_artifact, adapter_receipt = _component(
        manifest.get("adapter"), "adapter", roots, verify_files=verify_files
    )
    workflow_public, workflow, workflow_receipt = _component(
        manifest.get("workflow"), "workflow", roots, verify_files=verify_files, workflow=True
    )
    compiler, compiler_artifact, compiler_receipt = _component(
        manifest.get("compiler"), "compiler", roots, verify_files=verify_files
    )
    graph_raw = _stable_file_bytes(
        workflow.path,
        workflow.approved_root,
        maximum=MAX_WORKFLOW_BYTES,
    )
    if hashlib.sha256(graph_raw).hexdigest() != workflow.sha256:
        fail("manifest_digest_mismatch", "Workflow graph digest changed")
    workflow_graph = json_no_duplicates(graph_raw)
    if not isinstance(workflow_graph, dict) or not workflow_graph:
        fail("manifest_invalid", "Workflow must be a non-empty ComfyUI API graph")

    model_manifest = _exact_object(
        manifest.get("model_manifest"), frozenset({"id", "revision", "models"}), "model_manifest"
    )
    model_manifest_id = _safe(model_manifest.get("id"), SAFE_ID, "model_manifest.id")
    model_manifest_revision = _safe(
        model_manifest.get("revision"), SAFE_ID, "model_manifest.revision"
    )
    rows = model_manifest.get("models")
    if not isinstance(rows, list) or not rows:
        fail("manifest_models_missing", "No approved model artifacts are pinned")
    models: list[tuple[str, Artifact]] = []
    model_public_rows: list[dict[str, object]] = []
    qualifications: list[Artifact] = [adapter_receipt, workflow_receipt, compiler_receipt]
    roles: set[str] = set()
    for index, raw in enumerate(rows):
        item = _exact_object(raw, MODEL_KEYS, f"models[{index}]")
        role = _safe(item.get("role"), SAFE_ROLE, f"models[{index}].role")
        if (
            role in roles
            or role not in MODEL_CATALOG_DIRECTORIES
            or item.get("visual_class") != "fictional_animated_adult"
        ):
            fail("manifest_invalid", "Model role/content class is invalid")
        roles.add(role)
        comfy_name = _comfy_name(item.get("comfy_name"), f"models[{index}].comfy_name")
        provenance = _reference(item.get("provenance_ref"), f"models[{index}].provenance_ref")
        license_ref = _reference(item.get("license_ref"), f"models[{index}].license_ref")
        qualification = _qualification(
            item.get("qualification"),
            f"models[{index}].qualification",
            roots,
            verify_files=verify_files,
        )
        artifact = _artifact(
            item,
            f"models[{index}]",
            roots,
            keys=MODEL_KEYS,
            verify_files=verify_files,
            comfy_name=comfy_name,
            loader=role,
        )
        _require_catalog_path(artifact, MODEL_CATALOG_DIRECTORIES[role])
        qualifications.append(qualification)
        models.append((role, artifact))
        model_public_rows.append(
            {
                "id": artifact.artifact_id,
                "role": role,
                "comfy_name": comfy_name,
                "sha256": artifact.sha256,
                "provenance_ref": provenance,
                "license_ref": license_ref,
                "visual_class": "fictional_animated_adult",
                "qualification_receipt_sha256": qualification.sha256,
            }
        )
    model_public_rows.sort(key=lambda row: (str(row["role"]), str(row["id"])))
    model_public = {
        "id": model_manifest_id,
        "revision": model_manifest_revision,
        "manifest_sha256": sha256_json(model_public_rows),
    }

    identity_manifest = _exact_object(
        manifest.get("identity_manifest"),
        frozenset({"id", "revision", "identities"}),
        "identity_manifest",
    )
    identity_manifest_id = _safe(identity_manifest.get("id"), SAFE_ID, "identity_manifest.id")
    identity_manifest_revision = _safe(
        identity_manifest.get("revision"), SAFE_ID, "identity_manifest.revision"
    )
    identity_rows = identity_manifest.get("identities")
    if (
        not isinstance(identity_rows, list)
        or not identity_rows
        or len(identity_rows) > len(CANONICAL_PERSONA_IDS)
    ):
        fail("manifest_identities_missing", "No approved identity packs are pinned")
    identities: dict[tuple[str, str], IdentityPack] = {}
    seen_identity_personas: set[str] = set()
    identity_public_rows: list[Mapping[str, object]] = []
    identity_artifacts: list[Artifact] = []
    for index, raw in enumerate(identity_rows):
        item = _exact_object(raw, IDENTITY_KEYS, f"identities[{index}]")
        persona = _safe(item.get("persona_id"), SAFE_ID, "persona_id")
        if persona not in CANONICAL_PERSONA_IDS or persona in seen_identity_personas:
            fail("manifest_invalid", "Identity persona is not in the canonical roster")
        seen_identity_personas.add(persona)
        revision = _safe(item.get("identity_pack_revision"), SAFE_ID, "identity_pack_revision")
        attestation = _safe(
            item.get("adult_attestation_revision"), SAFE_ID, "adult_attestation_revision"
        )
        provenance = _reference(item.get("provenance_ref"), f"identities[{index}].provenance_ref")
        license_ref = _reference(item.get("license_ref"), f"identities[{index}].license_ref")
        qualification = _qualification(
            item.get("qualification"),
            f"identities[{index}].qualification",
            roots,
            verify_files=verify_files,
        )
        qualifications.append(qualification)
        raw_artifacts = item.get("artifacts")
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            fail("manifest_invalid", "Identity pack has no artifacts")
        parsed_artifacts: list[Artifact] = []
        for offset, entry in enumerate(raw_artifacts):
            artifact_row = _exact_object(
                entry,
                IDENTITY_ARTIFACT_KEYS,
                f"identities[{index}].artifacts[{offset}]",
            )
            loader = artifact_row.get("loader")
            if loader not in IDENTITY_LOADERS:
                fail("manifest_invalid", "Identity artifact loader is invalid")
            comfy_name = _comfy_name(
                artifact_row.get("comfy_name"),
                f"identities[{index}].artifacts[{offset}].comfy_name",
            )
            artifact = _artifact(
                artifact_row,
                f"identities[{index}].artifacts[{offset}]",
                roots,
                keys=IDENTITY_ARTIFACT_KEYS,
                verify_files=verify_files,
                comfy_name=comfy_name,
                loader=str(loader),
            )
            if loader == "lora":
                _require_catalog_path(artifact, MODEL_CATALOG_DIRECTORIES["lora"])
            else:
                suffix = artifact.path.suffix.lower()
                expected_name = f"NovelSceneArt/identities/{artifact.sha256}{suffix}"
                if suffix not in {".png", ".jpg", ".jpeg", ".webp"} or comfy_name != expected_name:
                    fail("manifest_catalog_mismatch", "Identity input staging name is invalid")
            parsed_artifacts.append(artifact)
        artifacts = tuple(parsed_artifacts)
        if len({entry.artifact_id for entry in artifacts}) != len(artifacts):
            fail("manifest_invalid", "Identity artifact identifiers must be unique")
        bindings = item.get("bindings")
        artifact_ids = {entry.artifact_id for entry in artifacts}
        if (
            not isinstance(bindings, dict)
            or not bindings
            or any(
                not isinstance(key, str)
                or SAFE_BINDING.fullmatch(key) is None
                or not isinstance(value, str)
                or value not in artifact_ids
                for key, value in bindings.items()
            )
        ):
            fail("manifest_invalid", "Identity bindings are invalid")
        artifact_manifest = {
            "artifacts": sorted(
                (
                    {
                        "id": entry.artifact_id,
                        "sha256": entry.sha256,
                        "loader": entry.loader,
                        "comfy_name": entry.comfy_name,
                    }
                    for entry in artifacts
                ),
                key=lambda row: str(row["id"]),
            ),
            "bindings": dict(sorted(bindings.items())),
        }
        public_identity: Mapping[str, object] = {
            "persona_id": persona,
            "identity_pack_revision": revision,
            "artifact_manifest_sha256": sha256_json(artifact_manifest),
            "visual_class": "fictional_animated_adult",
            "adult_attestation_revision": attestation,
            "provenance_ref": provenance,
            "license_ref": license_ref,
            "qualification_receipt_sha256": qualification.sha256,
        }
        key = (persona, revision)
        if key in identities:
            fail("manifest_invalid", "Identity pack keys must be unique")
        identities[key] = IdentityPack(
            persona,
            revision,
            str(public_identity["artifact_manifest_sha256"]),
            artifacts,
            dict(sorted(bindings.items())),
            public_identity,
        )
        identity_public_rows.append(public_identity)
        identity_artifacts.extend(artifacts)
    identity_public_rows.sort(key=lambda row: str(row["persona_id"]))
    identity_public = {
        "id": identity_manifest_id,
        "revision": identity_manifest_revision,
        "manifest_sha256": sha256_json(identity_public_rows),
        "personas": identity_public_rows,
    }

    template_catalog = _exact_object(
        manifest.get("template_catalog"),
        frozenset({"id", "revision", "templates"}),
        "template_catalog",
    )
    template_id = _safe(template_catalog.get("id"), SAFE_ID, "template_catalog.id")
    template_revision = _safe(
        template_catalog.get("revision"), SAFE_ID, "template_catalog.revision"
    )
    template_rows = template_catalog.get("templates")
    if not isinstance(template_rows, list) or not template_rows:
        fail("manifest_templates_missing", "No approved scene templates are pinned")
    templates: list[SceneTemplate] = []
    template_public_rows: list[dict[str, object]] = []
    seen_template_ids: set[str] = set()
    for index, raw in enumerate(template_rows):
        item = _exact_object(raw, TEMPLATE_KEYS, f"templates[{index}]")
        item_id = _safe(item.get("id"), SAFE_ID, "template.id")
        families = item.get("scene_families")
        counts = item.get("identity_slot_counts")
        positive = item.get("positive_prompt")
        negative = item.get("negative_prompt")
        bindings = item.get("graph_bindings")
        output_node_id = item.get("output_node_id")
        review_revision = item.get("review_policy_revision")
        qualification = _qualification(
            item.get("qualification"),
            f"templates[{index}].qualification",
            roots,
            verify_files=verify_files,
        )
        qualifications.append(qualification)
        if (
            item_id in seen_template_ids
            or not isinstance(families, list)
            or not families
            or any(not isinstance(value, str) or SAFE_ID.fullmatch(value) is None for value in families)
            or len(set(families)) != len(families)
            or not isinstance(counts, list)
            or not counts
            or any(type(value) is not int or value < 1 or value > 8 for value in counts)
            or len(set(counts)) != len(counts)
            or not isinstance(positive, str)
            or not positive.strip()
            or len(positive.encode("utf-8")) > 8_192
            or not isinstance(negative, str)
            or len(negative.encode("utf-8")) > 8_192
            or not isinstance(bindings, dict)
            or not bindings
            or not isinstance(output_node_id, str)
            or SAFE_ID.fullmatch(output_node_id) is None
            or item.get("content_class") != "fictional_animated_adult"
            or not isinstance(review_revision, str)
            or SAFE_ID.fullmatch(review_revision) is None
        ):
            fail("manifest_invalid", "Scene template is invalid")
        seen_template_ids.add(item_id)
        definition = {
            "id": item_id,
            "scene_families": families,
            "identity_slot_counts": counts,
            "positive_prompt": positive,
            "negative_prompt": negative,
            "graph_bindings": bindings,
            "output_node_id": output_node_id,
            "content_class": "fictional_animated_adult",
            "review_policy_revision": review_revision,
            "qualification_receipt_sha256": qualification.sha256,
        }
        definition_hash = sha256_json(definition)
        template_public_rows.append({"id": item_id, "sha256": definition_hash})
        templates.append(
            SceneTemplate(
                item_id,
                tuple(families),
                tuple(counts),
                positive,
                negative,
                dict(bindings),
                output_node_id,
                review_revision,
                qualification.sha256,
                definition_hash,
            )
        )
    template_public_rows.sort(key=lambda row: str(row["id"]))
    template_public = {
        "id": template_id,
        "revision": template_revision,
        "manifest_sha256": sha256_json(template_public_rows),
    }

    screening_row = _exact_object(
        manifest.get("screening"),
        frozenset(
            {"id", "path", "sha256", "policy_revision", "provenance_ref", "license_ref", "qualification"}
        ),
        "screening",
    )
    screening_policy = _safe(
        screening_row.get("policy_revision"), SAFE_ID, "screening.policy_revision"
    )
    _reference(screening_row.get("provenance_ref"), "screening.provenance_ref")
    _reference(screening_row.get("license_ref"), "screening.license_ref")
    screening_receipt = _qualification(
        screening_row.get("qualification"),
        "screening.qualification",
        roots,
        verify_files=verify_files,
    )
    qualifications.append(screening_receipt)
    screening = _artifact(
        screening_row,
        "screening",
        roots,
        keys=frozenset(
            {"id", "path", "sha256", "policy_revision", "provenance_ref", "license_ref", "qualification"}
        ),
        verify_files=verify_files,
    )
    runtime_binding, runtime_receipt = _runtime_binding(
        manifest.get("runtime_binding"),
        manifest,
        roots,
        verify_files=verify_files,
        source_root=runtime_source_root,
        require_production_runtime=require_production_runtime,
    )
    qualifications.append(runtime_receipt)

    max_slots = manifest.get("max_identity_slots")
    output = _exact_object(
        manifest.get("output"), frozenset({"mime_type", "width", "height", "max_bytes"}), "output"
    )
    if (
        type(max_slots) is not int
        or max_slots < 1
        or max_slots > 8
        or output
        != {
            "mime_type": OUTPUT_MIME,
            "width": OUTPUT_WIDTH,
            "height": OUTPUT_HEIGHT,
            "max_bytes": MAX_OUTPUT_BYTES,
        }
    ):
        fail("manifest_invalid", "Renderer output profile is invalid")
    public_release: Mapping[str, object] = {
        "schema_version": RENDER_RELEASE_SCHEMA,
        "workflow_contract_revision": WORKFLOW_CONTRACT_REVISION,
        "profile_id": profile_id,
        "adapter": adapter,
        "workflow": workflow_public,
        "compiler": compiler,
        "model_manifest": model_public,
        "identity_manifest": identity_public,
        "template_catalog": template_public,
        "screening": {
            "policy_revision": screening_policy,
            "manifest_sha256": screening.sha256,
        },
        "max_identity_slots": max_slots,
        "seed_policy": SEED_POLICY,
        "output": dict(output),
    }
    release_digest = sha256_json(public_release)
    capability: Mapping[str, object] = {
        "schema_version": CAPABILITY_SCHEMA,
        "workflow_contract_revision": WORKFLOW_CONTRACT_REVISION,
        "renderer_release_digest": release_digest,
        "profile_id": profile_id,
        "render_request_schema": RENDER_REQUEST_SCHEMA,
        "render_plan_schema": RENDER_PLAN_SCHEMA,
        "adapter": adapter,
        "workflow": workflow_public,
        "compiler": compiler,
        "model_manifest": model_public,
        "identity_manifest": {
            "id": identity_manifest_id,
            "revision": identity_manifest_revision,
            "manifest_sha256": identity_public["manifest_sha256"],
        },
        "template_catalog": template_public,
        "screening": public_release["screening"],
        "max_identity_slots": max_slots,
        "seed_policy": SEED_POLICY,
        "output": dict(output),
        "ready": True,
        "blockers": [],
    }
    all_artifacts = (
        adapter_artifact,
        workflow,
        compiler_artifact,
        *runtime_binding.modules,
        *(entry for _role, entry in models),
        *identity_artifacts,
        screening,
        *qualifications,
    )
    return RenderRelease(
        profile_id=profile_id,
        adapter=adapter,
        adapter_artifact=adapter_artifact,
        workflow=workflow,
        workflow_graph=workflow_graph,
        compiler=compiler,
        compiler_artifact=compiler_artifact,
        models=tuple(models),
        identities=identities,
        templates=tuple(templates),
        screening=screening,
        screening_policy_revision=screening_policy,
        runtime_binding=runtime_binding,
        public_release=public_release,
        release_digest=release_digest,
        capability=capability,
        capability_digest=sha256_json(capability),
        verified_artifacts=tuple(all_artifacts),
        source_manifest_path=source_path,
        source_manifest_sha256=source_sha256,
        activation_blockers=ACTIVATION_BLOCKERS,
    )


def verify_release_artifacts(release: RenderRelease) -> None:
    """Re-open and bind every local artifact immediately before submission."""

    if hashlib.sha256(_private_manifest_bytes(release.source_manifest_path)).hexdigest() != release.source_manifest_sha256:
        fail("manifest_changed", "Render manifest changed after preflight")
    seen: set[tuple[Path, str]] = set()
    for artifact in release.verified_artifacts:
        key = (artifact.path, artifact.sha256)
        if key in seen:
            continue
        seen.add(key)
        actual = _stable_file_sha256(
            artifact.path,
            artifact.approved_root,
            maximum=artifact.maximum_bytes,
            expected_uid=artifact.expected_uid,
        )
        if actual != artifact.sha256:
            fail("manifest_digest_mismatch", "A pinned artifact changed after preflight")


def inspect_release_manifest(
    path: Path,
    *,
    verify_files: bool = True,
    require_private_manifest: bool = True,
    allowed_roots: tuple[Path, ...] | None = None,
    runtime_source_root: Path = REPO_ROOT,
    require_production_runtime: bool = True,
) -> ManifestPreflight:
    try:
        if require_private_manifest:
            raw = _private_manifest_bytes(path)
        else:
            raw = _public_manifest_bytes(path)
        value = json_no_duplicates(raw)
    except (OSError, SceneArtError) as error:
        code = error.code if isinstance(error, SceneArtError) else "manifest_unavailable"
        return ManifestPreflight(False, (code,))
    if not isinstance(value, dict):
        return ManifestPreflight(False, ("manifest_invalid",))
    blockers: list[str] = []
    if value.get("approved") is not True:
        blockers.append("render_release_not_approved")
    for key, code in (
        ("adapter", "adapter_manifest_missing"),
        ("workflow", "workflow_manifest_missing"),
        ("compiler", "compiler_manifest_missing"),
        ("model_manifest", "model_manifest_missing"),
        ("identity_manifest", "identity_manifest_missing"),
        ("template_catalog", "template_manifest_missing"),
        ("screening", "screening_manifest_missing"),
        ("runtime_binding", "runtime_binding_manifest_missing"),
    ):
        if value.get(key) in (None, [], {}):
            blockers.append(code)
    if blockers:
        return ManifestPreflight(False, tuple(blockers))
    try:
        release = _parse_complete(
            value,
            _absolute(path, "manifest"),
            hashlib.sha256(raw).hexdigest(),
            roots=allowed_roots or default_artifact_roots(),
            verify_files=verify_files,
            runtime_source_root=runtime_source_root,
            require_production_runtime=require_production_runtime,
        )
    except SceneArtError as error:
        return ManifestPreflight(False, (error.code,))
    return ManifestPreflight(True, (), release)


def load_approved_release(
    path: Path,
    *,
    verify_files: bool = True,
    allowed_roots: tuple[Path, ...] | None = None,
    runtime_source_root: Path = REPO_ROOT,
    require_production_runtime: bool = True,
) -> RenderRelease:
    report = inspect_release_manifest(
        path,
        verify_files=verify_files,
        allowed_roots=allowed_roots,
        runtime_source_root=runtime_source_root,
        require_production_runtime=require_production_runtime,
    )
    if not report.ready or report.release is None:
        fail("render_release_not_ready", "Render release is blocked: " + ",".join(report.blockers))
    return report.release
