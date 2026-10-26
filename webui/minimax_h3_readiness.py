"""Read-only, fail-closed readiness evidence for native MiniMax H3.

This module is the boundary between installation state and execution.  It
never downloads models, queues a ComfyUI prompt, writes a receipt, or contacts
an external provider.  A product contract is executable only when its
configuration and four independent evidence components are current:

* the existing owner-only authorization record permits ``local_inference``;
* every family artifact matches the pinned byte count and SHA-256;
* the exact native node set is present at Castle's fixed loopback ComfyUI;
* an owner-only, short-lived golden receipt binds that exact contract graph,
  live node schemas, visual class, and current model/workflow locks.

The public projection contains only stable status codes and counts.  Paths,
authorization contents, golden receipt identifiers, prompts, and model names
never cross this boundary.
"""

from __future__ import annotations

if __package__:
    from . import file_lock as fcntl
else:
    import file_lock as fcntl
import hashlib
import http.client
import importlib
import importlib.util
import json
import os
import re
import stat
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping, Sequence

if __package__:
    from . import minimax_h3_native as _native
    from . import minimax_h3_product as _product
else:
    import minimax_h3_native as _native
    import minimax_h3_product as _product


REPO_ROOT: Final = Path(__file__).resolve().parents[1]


def _load_pinned_support_module(name: str) -> Any:
    """Load the sibling support package without changing process import paths.

    Castle runs ``webui/server.py`` as a script, so only ``webui/`` is
    guaranteed to be importable.  Registering this exact, file-backed private
    package keeps the readiness module deployable in that context while
    avoiding global ``sys.path`` mutation.
    """

    package_name = "_castle_minimax_h3_readiness_support"
    support_root = REPO_ROOT / "scripts" / "minimax_h3"
    initializer = support_root / "__init__.py"
    package = sys.modules.get(package_name)
    if package is None:
        spec = importlib.util.spec_from_file_location(
            package_name,
            initializer,
            submodule_search_locations=[os.fspath(support_root)],
        )
        if spec is None or spec.loader is None:
            raise ImportError("MiniMax H3 readiness support package is unavailable")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        try:
            spec.loader.exec_module(package)
        except BaseException:
            sys.modules.pop(package_name, None)
            raise
    return importlib.import_module(f"{package_name}.{name}")


_artifacts = _load_pinned_support_module("artifacts")
_authorization = _load_pinned_support_module("authorization")
ArtifactError = _artifacts.ArtifactError
ArtifactManifest = _artifacts.ArtifactManifest
ArtifactSpec = _artifacts.ArtifactSpec
artifact_destination = _artifacts.artifact_destination
load_manifest = _artifacts.load_manifest
partial_path_for = _artifacts.partial_path_for
select_family = _artifacts.select_family
default_authorization_record_path = _authorization.default_authorization_record_path
evaluate_local_inference_authorization = (
    _authorization.evaluate_local_inference_authorization
)
manifest_scope_sha256 = _authorization.manifest_scope_sha256

DEFAULT_MODEL_MANIFEST: Final = REPO_ROOT / "manifests/minimax_h3/models.lock.json"
DEFAULT_WORKFLOW_MANIFEST: Final = REPO_ROOT / "manifests/minimax_h3/workflows.lock.json"
DEFAULT_MODELS_DIR: Final = Path(os.environ.get("COMFYUI_MODELS_DIR", Path.home() / "comfyui/models")).expanduser()
DEFAULT_GOLDEN_RECORD: Final = (
    Path.home()
    / ".local"
    / "share"
    / "web-comfyui"
    / "minimax-h3-readiness"
    / "golden-receipt.json"
)
COMFY_ORIGIN: Final = "http://127.0.0.1:8188"
OBJECT_INFO_PATH: Final = "/object_info"

READINESS_SCHEMA_VERSION: Final = 1
GOLDEN_SCHEMA_VERSION: Final = 1
PRIVATE_DIRECTORY_MODE: Final = 0o700
PRIVATE_FILE_MODE: Final = 0o600
MAX_PRIVATE_RECORD_BYTES: Final = 64 * 1024
MAX_OBJECT_INFO_BYTES: Final = 64 * 1024 * 1024
MAX_DIGEST_CACHE_ENTRIES: Final = 128
MODEL_HASH_CHUNK_BYTES: Final = 8 * 1024 * 1024
DEFAULT_DIGEST_WAIT_SECONDS: Final = 5.0
MAX_GOLDEN_VALIDITY_SECONDS: Final = 31 * 24 * 60 * 60

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,199}$")
_RFC3339_UTC_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)

# The checked-in workflow lock covers the native H3 implementation surface.
# ``LoadImage`` is intentionally contract-specific: text-to-video must not be
# held behind an image node, while both image-bearing modes must attest it.
_COMMON_REQUIRED_NODES: Final = frozenset(
    {
        "BasicGuider",
        "BasicScheduler",
        "CLIPLoader",
        "CreateVideo",
        "KSamplerSelect",
        "RandomNoise",
        "SamplerCustomAdvanced",
        "SaveVideo",
        "UNETLoader",
        "VAEDecode",
        "VAEDecodeAudio",
        "VAELoader",
    }
)
_LOCKED_REQUIRED_NODES: Final = _COMMON_REQUIRED_NODES | {
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3ReferenceToVideo",
}


@dataclass(frozen=True, slots=True)
class H3ReadinessContract:
    contract_id: str
    family: str
    mode: str
    visual_class: str | None
    required_nodes: frozenset[str]


_FL_NODES: Final = _COMMON_REQUIRED_NODES | {"MiniMaxH3ImageToVideo"}
_REF_NODES: Final = _COMMON_REQUIRED_NODES | {"MiniMaxH3ReferenceToVideo"}
H3_CONTRACTS: Final = MappingProxyType({
    contract.contract_id: contract
    for contract in (
        H3ReadinessContract(
            "h3.t2v.stock.v1", "fl", "t2v", None, _FL_NODES
        ),
        H3ReadinessContract(
            "h3.i2v.anime.stock.v1",
            "fl",
            "i2v",
            "anime",
            _FL_NODES | {"LoadImage"},
        ),
        H3ReadinessContract(
            "h3.i2v.realistic.stock.v1",
            "fl",
            "i2v",
            "realistic",
            _FL_NODES | {"LoadImage"},
        ),
        H3ReadinessContract(
            "h3.ref2va-image.anime.stock.v1",
            "ref",
            "ref2va_image",
            "anime",
            _REF_NODES | {"LoadImage"},
        ),
        H3ReadinessContract(
            "h3.ref2va-image.realistic.stock.v1",
            "ref",
            "ref2va_image",
            "realistic",
            _REF_NODES | {"LoadImage"},
        ),
    )
})
KNOWN_CONTRACT_IDS: Final = tuple(H3_CONTRACTS)


class MiniMaxH3ReadinessError(RuntimeError):
    """A sanitized readiness configuration or evidence failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _PrivateRecordError(MiniMaxH3ReadinessError):
    pass


class _ModelEvidenceError(MiniMaxH3ReadinessError):
    pass


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """All mutable filesystem identity fields that bind one cached digest."""

    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileIdentity":
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
        )


DigestReader = Callable[[int], str]
ObjectInfoFetcher = Callable[[str, float], Mapping[str, Any]]


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, MODEL_HASH_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


class ModelDigestCache:
    """Thread-safe in-memory digest cache bound to complete file identity.

    Concurrent callers share one in-flight hash.  An inode, size, mtime, or
    ctime change produces a different key and therefore can never inherit an
    older result.  The path is re-stat'ed after hashing so replacement races
    fail closed as well.
    """

    def __init__(
        self,
        *,
        digest_reader: DigestReader = _sha256_fd,
        max_entries: int = MAX_DIGEST_CACHE_ENTRIES,
        wait_timeout_seconds: float = DEFAULT_DIGEST_WAIT_SECONDS,
    ) -> None:
        if not callable(digest_reader):
            raise TypeError("digest_reader must be callable")
        if type(max_entries) is not int or not 1 <= max_entries <= 4096:
            raise ValueError("max_entries is invalid")
        if (
            isinstance(wait_timeout_seconds, bool)
            or not isinstance(wait_timeout_seconds, (int, float))
            or not 0.01 <= float(wait_timeout_seconds) <= 30.0
        ):
            raise ValueError("wait_timeout_seconds is invalid")
        self._digest_reader = digest_reader
        self._max_entries = max_entries
        self._wait_timeout_seconds = float(wait_timeout_seconds)
        self._entries: OrderedDict[FileIdentity, str] = OrderedDict()
        self._inflight: dict[FileIdentity, threading.Event] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _open_regular(path: Path, expected_size: int) -> tuple[int, FileIdentity]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except (FileNotFoundError, NotADirectoryError) as error:
            raise _ModelEvidenceError("model_missing") from error
        except OSError as error:
            raise _ModelEvidenceError("model_unsafe") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise _ModelEvidenceError("model_unsafe")
            if info.st_size != expected_size:
                raise _ModelEvidenceError("model_size_mismatch")
            return descriptor, FileIdentity.from_stat(info)
        except BaseException:
            os.close(descriptor)
            raise

    def digest(self, path: Path, *, expected_size: int) -> str:
        if type(expected_size) is not int or expected_size <= 0:
            raise ValueError("expected_size is invalid")
        descriptor, identity = self._open_regular(path, expected_size)
        owner = False
        event: threading.Event | None = None
        try:
            while True:
                with self._lock:
                    cached = self._entries.get(identity)
                    if cached is not None:
                        try:
                            current = FileIdentity.from_stat(
                                os.stat(path, follow_symlinks=False)
                            )
                        except OSError as error:
                            raise _ModelEvidenceError(
                                "model_changed_during_hash"
                            ) from error
                        if current != identity:
                            raise _ModelEvidenceError("model_changed_during_hash")
                        self._entries.move_to_end(identity)
                        return cached
                    event = self._inflight.get(identity)
                    if event is None:
                        event = threading.Event()
                        self._inflight[identity] = event
                        owner = True
                        break
                if not event.wait(self._wait_timeout_seconds):
                    raise _ModelEvidenceError("model_digest_busy")

            try:
                try:
                    digest = self._digest_reader(descriptor)
                    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
                        raise _ModelEvidenceError("model_digest_failed")
                    after = FileIdentity.from_stat(os.fstat(descriptor))
                    try:
                        current = os.stat(path, follow_symlinks=False)
                    except OSError as error:
                        raise _ModelEvidenceError("model_changed_during_hash") from error
                except _ModelEvidenceError:
                    raise
                except Exception as error:
                    raise _ModelEvidenceError("model_digest_failed") from error
                if after != identity or FileIdentity.from_stat(current) != identity:
                    raise _ModelEvidenceError("model_changed_during_hash")
                with self._lock:
                    self._entries[identity] = digest
                    self._entries.move_to_end(identity)
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
                return digest
            finally:
                if owner:
                    with self._lock:
                        completed = self._inflight.pop(identity, None)
                        if completed is not None:
                            completed.set()
        finally:
            os.close(descriptor)


_GLOBAL_DIGEST_CACHE = ModelDigestCache()


@dataclass(frozen=True, slots=True)
class ComponentReadiness:
    status: str
    reason: str
    required_count: int | None = None
    ready_count: int | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
        }
        if self.required_count is not None:
            result["required_count"] = self.required_count
        if self.ready_count is not None:
            result["ready_count"] = self.ready_count
        return result


@dataclass(frozen=True, slots=True)
class ContractReadiness:
    contract_id: str
    family: str
    visual_class: str | None
    configuration: ComponentReadiness
    authorization: ComponentReadiness
    models: ComponentReadiness
    runtime: ComponentReadiness
    golden: ComponentReadiness

    @property
    def execution_enabled(self) -> bool:
        """Technical local readiness; quality goldens are informational only."""

        return all(
            component.ready
            for component in (
                self.configuration,
                self.authorization,
                self.models,
                self.runtime,
            )
        )

    @property
    def status(self) -> str:
        if self.configuration.ready is False:
            return "configuration_invalid"
        if self.authorization.ready is False:
            return "authorization_blocked"
        if self.models.ready is False:
            return "models_not_ready"
        if self.runtime.ready is False:
            return "runtime_not_ready"
        return "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "family": self.family,
            "visual_class": self.visual_class,
            "status": self.status,
            "execution_enabled": self.execution_enabled,
            "configuration": self.configuration.to_dict(),
            "authorization": self.authorization.to_dict(),
            "models": self.models.to_dict(),
            "runtime": self.runtime.to_dict(),
            "golden": self.golden.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MiniMaxH3ReadinessSnapshot:
    contracts: tuple[ContractReadiness, ...]
    loopback_probe_attempted: bool
    prompt_queued: bool = False

    def __post_init__(self) -> None:
        names = tuple(item.contract_id for item in self.contracts)
        if not names or len(names) != len(set(names)) or any(
            name not in H3_CONTRACTS for name in names
        ):
            raise ValueError("readiness contracts are invalid")
        if type(self.loopback_probe_attempted) is not bool:
            raise ValueError("loopback_probe_attempted must be boolean")
        if self.prompt_queued is not False:
            raise ValueError("readiness snapshots can never queue prompts")

    @property
    def execution_enabled(self) -> bool:
        return all(item.execution_enabled for item in self.contracts)

    @property
    def status(self) -> str:
        if any(not item.configuration.ready for item in self.contracts):
            return "configuration_invalid"
        if self.execution_enabled:
            return "ready"
        return "not_ready"

    def for_contract(self, contract_id: str) -> ContractReadiness:
        for item in self.contracts:
            if item.contract_id == contract_id:
                return item
        raise KeyError(contract_id)

    def to_inventory(self) -> dict[str, Any]:
        return {
            "schema_version": READINESS_SCHEMA_VERSION,
            "status": self.status,
            "execution_enabled": self.execution_enabled,
            "contracts": {
                item.contract_id: item.to_dict() for item in self.contracts
            },
            "loopback_probe_attempted": self.loopback_probe_attempted,
            "prompt_queued": self.prompt_queued,
            "external_provider_contacted": False,
            "filesystem_mutated": False,
        }


@dataclass(frozen=True, slots=True)
class MiniMaxH3ReadinessConfig:
    model_manifest_path: Path = DEFAULT_MODEL_MANIFEST
    workflow_manifest_path: Path = DEFAULT_WORKFLOW_MANIFEST
    models_dir: Path = DEFAULT_MODELS_DIR
    authorization_record_path: Path = field(default_factory=default_authorization_record_path)
    golden_record_path: Path = DEFAULT_GOLDEN_RECORD
    comfy_origin: str = COMFY_ORIGIN
    timeout_seconds: float = 5.0
    contract_ids: tuple[str, ...] = KNOWN_CONTRACT_IDS

    def __post_init__(self) -> None:
        for name in (
            "model_manifest_path",
            "workflow_manifest_path",
            "models_dir",
            "authorization_record_path",
            "golden_record_path",
        ):
            value = Path(getattr(self, name)).expanduser()
            if not value.is_absolute() or "\x00" in os.fspath(value):
                raise ValueError(f"{name} must be an absolute path")
            object.__setattr__(self, name, Path(os.path.abspath(os.fspath(value))))
        if self.comfy_origin != COMFY_ORIGIN:
            raise ValueError("MiniMax H3 readiness only supports the fixed loopback origin")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0.1 <= float(self.timeout_seconds) <= 30.0
        ):
            raise ValueError("timeout_seconds is invalid")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if (
            type(self.contract_ids) is not tuple
            or not self.contract_ids
            or len(self.contract_ids) != len(set(self.contract_ids))
            or any(
                type(contract_id) is not str or contract_id not in H3_CONTRACTS
                for contract_id in self.contract_ids
            )
        ):
            raise ValueError("contract_ids must be a unique tuple of known H3 contracts")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _external_record_path(value: Path) -> Path:
    path = Path(os.path.abspath(os.fspath(value.expanduser())))
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise _PrivateRecordError("private_record_path_unsafe")
    try:
        in_repository = os.path.commonpath((os.fspath(path), os.fspath(REPO_ROOT))) == os.fspath(
            REPO_ROOT
        )
    except ValueError:
        in_repository = False
    if in_repository:
        raise _PrivateRecordError("private_record_inside_repository")
    return path


def _open_private_parent(path: Path) -> int:
    parts = path.parent.parts
    if not parts or parts[0] != os.path.sep:
        raise _PrivateRecordError("private_record_path_unsafe")
    names = parts[1:]
    if not names or any(name in {"", ".", ".."} for name in names):
        raise _PrivateRecordError("private_record_path_unsafe")
    try:
        current = os.open(os.path.sep, _directory_flags())
    except OSError as error:
        raise _PrivateRecordError("private_record_path_unsafe") from error
    try:
        for name in names:
            try:
                following = os.open(name, _directory_flags(), dir_fd=current)
            except FileNotFoundError as error:
                raise _PrivateRecordError("golden_record_missing") from error
            except OSError as error:
                raise _PrivateRecordError("private_record_path_unsafe") from error
            os.close(current)
            current = following
        info = os.fstat(current)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != PRIVATE_DIRECTORY_MODE
        ):
            raise _PrivateRecordError("golden_parent_not_owner_only")
        return current
    except BaseException:
        os.close(current)
        raise


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_owner_only_json(path_value: Path) -> Mapping[str, Any]:
    path = _external_record_path(path_value)
    parent = _open_private_parent(path)
    descriptor = -1
    try:
        try:
            descriptor = os.open(path.name, _file_flags(), dir_fd=parent)
        except FileNotFoundError as error:
            raise _PrivateRecordError("golden_record_missing") from error
        except OSError as error:
            raise _PrivateRecordError("golden_record_unsafe") from error
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != PRIVATE_FILE_MODE
        ):
            raise _PrivateRecordError("golden_record_not_owner_only")
        if not 0 < opened.st_size <= MAX_PRIVATE_RECORD_BYTES:
            raise _PrivateRecordError("golden_record_size_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise _PrivateRecordError("golden_record_busy") from error
        except OSError as error:
            raise _PrivateRecordError("golden_record_unreadable") from error
        before_info = os.fstat(descriptor)
        if (
            before_info.st_dev != opened.st_dev
            or before_info.st_ino != opened.st_ino
            or before_info.st_mode != opened.st_mode
            or before_info.st_nlink != opened.st_nlink
            or before_info.st_uid != opened.st_uid
            or before_info.st_size != opened.st_size
            or before_info.st_mtime_ns != opened.st_mtime_ns
            or before_info.st_ctime_ns != opened.st_ctime_ns
        ):
            raise _PrivateRecordError("golden_record_changed_during_read")
        chunks: list[bytes] = []
        remaining = MAX_PRIVATE_RECORD_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after_info = os.fstat(descriptor)
        if (
            after_info.st_dev != before_info.st_dev
            or after_info.st_ino != before_info.st_ino
            or after_info.st_mode != before_info.st_mode
            or after_info.st_nlink != before_info.st_nlink
            or after_info.st_uid != before_info.st_uid
            or after_info.st_size != before_info.st_size
            or after_info.st_mtime_ns != before_info.st_mtime_ns
            or after_info.st_ctime_ns != before_info.st_ctime_ns
            or len(raw) != before_info.st_size
        ):
            raise _PrivateRecordError("golden_record_changed_during_read")
        try:
            payload = json.loads(
                raw.decode("utf-8", "strict"),
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise _PrivateRecordError("golden_record_invalid_json") from error
        if not isinstance(payload, dict):
            raise _PrivateRecordError("golden_record_schema_invalid")
        return payload
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        os.close(parent)


def _exact_keys(value: Any, keys: frozenset[str]) -> bool:
    return isinstance(value, dict) and frozenset(value) == keys


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", "strict")
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or _RFC3339_UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return int(parsed.timestamp())


def _product_contract_payload(contract: H3ReadinessContract) -> dict[str, Any]:
    """Bind class routing, allowed options, and the server-composed prompt.

    The native DAG is identical for anime and realistic I2V, but the product
    contract is intentionally not: guardrails and motion options are part of
    what the operator reviews.  Compile every allowed class-specific option through the
    real product adapter so a prompt guardrail or routing change expires only
    that contract's golden.
    """

    description_slot = "CASTLE_H3_DESCRIPTION_SLOT"
    feature_id = {
        "t2v": "h3-text-video",
        "i2v": "h3-image-video",
        "ref2va_image": "h3-reference-video",
    }[contract.mode]
    def compiled_binding(graph: Mapping[str, Any], *, expected_prompt: str) -> dict[str, Any]:
        try:
            inputs = graph["5"]["inputs"]
            prompt = inputs["prompt"]
            width = inputs["width"]
            height = inputs["height"]
            length = inputs["length"]
        except (KeyError, TypeError) as error:
            raise MiniMaxH3ReadinessError("product_contract_invalid") from error
        if (
            not isinstance(prompt, str)
            or prompt.count(description_slot) != 1
            or prompt.replace(description_slot, "${DESCRIPTION}") != expected_prompt
            or type(width) is not int
            or type(height) is not int
            or type(length) is not int
        ):
            raise MiniMaxH3ReadinessError("product_contract_invalid")
        return {
            "prompt": expected_prompt,
            "width": width,
            "height": height,
            "length": length,
        }

    if contract.mode == "t2v":
        compiled: dict[str, dict[str, Any]] = {}
        expected_prompt = (
            "integrated_multimodal_description:\n${DESCRIPTION}\n\n"
            "overall_soundscape:\nCASTLE_H3_SOUNDSCAPE_SLOT\n\n"
            "non_diegetic_music:\nCASTLE_H3_MUSIC_SLOT"
        )
        for aspect in (
            "portrait", "landscape", "square",
            "portrait-preview", "landscape-preview", "square-preview",
        ):
            graph = _product.compile_product_prompt(
                feature_id=feature_id,
                visual_class=None,
                settings={
                    "integrated_multimodal_description": description_slot,
                    "overall_soundscape": "CASTLE_H3_SOUNDSCAPE_SLOT",
                    "non_diegetic_music": "CASTLE_H3_MUSIC_SLOT",
                    "aspect": aspect,
                    "frames": "124",
                    "acceleration": "stock",
                    "seed": 42,
                },
                uploads={},
                output_token="contract-product",
            )
            compiled[aspect] = compiled_binding(
                graph, expected_prompt=expected_prompt
            )
        return {
            "schema_version": 2,
            "feature_id": feature_id,
            "operator_only": False,
            "visual_class": None,
            "allowed_options": {
                "aspect": [
                    "portrait", "landscape", "square",
                    "portrait-preview", "landscape-preview", "square-preview",
                ],
                "frames": list(_product.LEGAL_FRAMES),
                "acceleration": list(_product.ACCELERATION_OPTIONS),
            },
            "compiled_options": compiled,
        }

    action_id = "image-video" if contract.mode == "i2v" else "reference-video"
    variant = _product.character_variant(contract.visual_class, action_id)
    if variant is None or variant.feature_id != feature_id:
        raise MiniMaxH3ReadinessError("product_contract_invalid")
    upload_slot = variant.server_bound_uploads[0]
    upload_value = (
        "contract-input.png" if contract.mode == "i2v" else "contract-reference.png"
    )
    compiled: dict[str, dict[str, Any]] = {}
    for motion in variant.motion_options:
        for aspect in variant.aspect_options:
            graph = _product.compile_product_prompt(
                feature_id=feature_id,
                visual_class=contract.visual_class,
                settings={
                    "description": description_slot,
                    "motionProfile": motion,
                    "aspect": aspect,
                    "quality": "quality",
                    "seed": 42,
                },
                uploads={upload_slot: upload_value},
                output_token="contract-product",
            )
            prompt = graph["5"]["inputs"].get("prompt")
            if not isinstance(prompt, str):
                raise MiniMaxH3ReadinessError("product_contract_invalid")
            expected_prompt = prompt.replace(description_slot, "${DESCRIPTION}")
            compiled[f"{motion}:{aspect}"] = compiled_binding(
                graph, expected_prompt=expected_prompt
            )
    return {
        "schema_version": 1,
        "feature_id": feature_id,
        "variant": variant.public_contract(),
        "compiled_options": compiled,
    }


def _graph_contract_payload(contract: H3ReadinessContract) -> dict[str, Any]:
    """Derive an execution ABI from the native builder, not a hand-written DAG.

    User-controlled values are replaced at their exact graph positions.  Fixed
    model names, node links, sampler settings, codec settings, allowed canvases,
    and duration profiles remain in the canonical payload and therefore rotate
    the digest when the native execution contract changes.
    """

    common: dict[str, object] = {
        "prompt": "CASTLE_H3_PROMPT_SLOT",
        "resolution": (864, 480),
        "profile": "quality",
        "seed": 42,
    }
    if contract.mode == "t2v":
        graph = _native.build_t2v_prompt(
            **common,
            output_prefix="Castle/minimax-h3/t2v/contract-digest",
        )
    elif contract.mode == "i2v":
        graph = _native.build_i2v_prompt(
            **common,
            output_prefix="Castle/minimax-h3/i2v/contract-digest",
            first_frame="contract-input.png",
        )
    elif contract.mode == "ref2va_image":
        graph = _native.build_ref2va_image_prompt(
            **{**common, "prompt": "<Picture 1> CASTLE_H3_PROMPT_SLOT"},
            output_prefix="Castle/minimax-h3/ref2va_image/contract-digest",
            reference_image="contract-reference.png",
        )
    else:  # pragma: no cover - H3_CONTRACTS is immutable checked-in data.
        raise MiniMaxH3ReadinessError("graph_contract_invalid")

    conditioning = graph["5"]["inputs"]
    assert isinstance(conditioning, dict)
    conditioning["prompt"] = {"slot": "prompt"}
    conditioning["width"] = {"slot": "width"}
    conditioning["height"] = {"slot": "height"}
    conditioning["length"] = {"slot": "length"}
    noise = graph["6"]["inputs"]
    output = graph["14"]["inputs"]
    assert isinstance(noise, dict) and isinstance(output, dict)
    noise["noise_seed"] = {"slot": "seed"}
    output["filename_prefix"] = {"slot": "output_prefix"}
    if contract.mode in {"i2v", "ref2va_image"}:
        image_inputs = graph["15"]["inputs"]
        assert isinstance(image_inputs, dict)
        image_inputs["image"] = {"slot": "server_bound_image"}

    return {
        "schema_version": 1,
        "mode": contract.mode,
        "visual_class": contract.visual_class,
        "allowed_resolutions": sorted(
            [list(value) for value in _native.ALLOWED_RESOLUTIONS]
        ),
        "duration_profiles": dict(sorted(_native.DURATION_PROFILES.items())),
        "product": _product_contract_payload(contract),
        "graph": graph,
    }


def graph_contract_sha256(contract_id: str) -> str:
    contract = H3_CONTRACTS.get(contract_id)
    if contract is None:
        raise ValueError("contract_id is invalid")
    return _canonical_digest(_graph_contract_payload(contract))


def _runtime_schema_digest(
    object_info: Mapping[str, Any], required_nodes: frozenset[str]
) -> str:
    schemas: dict[str, Any] = {}
    for node in sorted(required_nodes):
        value = object_info.get(node)
        if not isinstance(value, dict):
            raise MiniMaxH3ReadinessError("required_nodes_missing")
        schemas[node] = value
    try:
        return _canonical_digest({"schema_version": 1, "nodes": schemas})
    except (TypeError, ValueError, UnicodeError) as error:
        raise MiniMaxH3ReadinessError("runtime_response_invalid") from error


def _golden_components(
    *,
    record_path: Path,
    contracts: Sequence[H3ReadinessContract],
    model_manifest: ArtifactManifest,
    workflow_manifest: ArtifactManifest,
    runtime_schema_digests: Mapping[str, str | None],
    now_epoch: int,
) -> dict[str, ComponentReadiness]:
    blocked = {
        contract.contract_id: ComponentReadiness(
            "not_ready", "golden_record_missing"
        )
        for contract in contracts
    }
    try:
        payload = _read_owner_only_json(record_path)
    except _PrivateRecordError as error:
        return {
            contract.contract_id: ComponentReadiness("not_ready", error.code)
            for contract in contracts
        }
    if (
        not _exact_keys(payload, frozenset({"schema_version", "receipts"}))
        or payload.get("schema_version") != GOLDEN_SCHEMA_VERSION
        or isinstance(payload.get("schema_version"), bool)
    ):
        return {
            contract.contract_id: ComponentReadiness(
                "not_ready", "golden_record_schema_invalid"
            )
            for contract in contracts
        }
    receipts = payload.get("receipts")
    if not isinstance(receipts, list) or len(receipts) > len(H3_CONTRACTS):
        return {
            contract.contract_id: ComponentReadiness(
                "not_ready", "golden_record_schema_invalid"
            )
            for contract in contracts
        }

    receipt_keys = frozenset(
        {
            "contract_id",
            "family",
            "visual_class",
            "receipt_id",
            "accepted",
            "tested_at",
            "expires_at",
            "repeat_count",
            "graph_contract_sha256",
            "runtime_schema_sha256",
            "model_manifest_sha256",
            "model_revision",
            "workflow_manifest_sha256",
            "workflow_revision",
            "revoked",
        }
    )
    indexed: dict[str, Mapping[str, Any]] = {}
    for receipt in receipts:
        if not _exact_keys(receipt, receipt_keys):
            return {
                contract.contract_id: ComponentReadiness(
                    "not_ready", "golden_record_schema_invalid"
                )
                for contract in contracts
            }
        contract_id = receipt.get("contract_id")
        if contract_id not in H3_CONTRACTS or contract_id in indexed:
            return {
                contract.contract_id: ComponentReadiness(
                    "not_ready", "golden_record_schema_invalid"
                )
                for contract in contracts
            }
        indexed[contract_id] = receipt

    expected_model = manifest_scope_sha256(model_manifest)
    expected_workflow = manifest_scope_sha256(workflow_manifest)
    result = dict(blocked)
    for contract in contracts:
        contract_id = contract.contract_id
        receipt = indexed.get(contract_id)
        if receipt is None:
            continue
        identifier = receipt.get("receipt_id")
        tested_at = _timestamp(receipt.get("tested_at"))
        expires_at = _timestamp(receipt.get("expires_at"))
        repeat_count = receipt.get("repeat_count")
        if (
            not isinstance(identifier, str)
            or _OPAQUE_ID_RE.fullmatch(identifier) is None
            or receipt.get("accepted") is not True
            or receipt.get("revoked") is not False
            or receipt.get("family") != contract.family
            or receipt.get("visual_class") != contract.visual_class
            or tested_at is None
            or expires_at is None
            or tested_at > now_epoch
            or not tested_at < expires_at
            or expires_at - tested_at > MAX_GOLDEN_VALIDITY_SECONDS
            or now_epoch >= expires_at
            or type(repeat_count) is not int
            or not 2 <= repeat_count <= 1000
        ):
            result[contract_id] = ComponentReadiness(
                "not_ready", "golden_receipt_invalid"
            )
            continue
        runtime_digest = runtime_schema_digests.get(contract_id)
        if runtime_digest is None:
            result[contract_id] = ComponentReadiness(
                "not_ready", "golden_runtime_schema_unavailable"
            )
            continue
        if (
            receipt.get("graph_contract_sha256")
            != graph_contract_sha256(contract_id)
            or receipt.get("runtime_schema_sha256") != runtime_digest
            or receipt.get("model_manifest_sha256") != expected_model
            or receipt.get("model_revision") != model_manifest.revision
            or receipt.get("workflow_manifest_sha256") != expected_workflow
            or receipt.get("workflow_revision") != workflow_manifest.revision
        ):
            result[contract_id] = ComponentReadiness(
                "not_ready", "golden_receipt_stale"
            )
            continue
        result[contract_id] = ComponentReadiness(
            "ready", "golden_receipt_valid"
        )
    return result


def _artifact_path(models_dir: Path, artifact: ArtifactSpec) -> Path:
    if artifact.destination.endswith(".partial"):
        raise _ModelEvidenceError("model_partial")
    try:
        _root, destination = artifact_destination(models_dir, artifact.destination)
    except ArtifactError as error:
        raise _ModelEvidenceError("model_unsafe") from error
    return destination


def _model_component(
    *,
    models_dir: Path,
    artifacts: Sequence[ArtifactSpec],
    digest_cache: ModelDigestCache,
) -> ComponentReadiness:
    required_count = len(artifacts)
    paths: list[tuple[Path, ArtifactSpec]] = []

    # Cheap preflight first.  A missing/partial family is already not ready, so
    # do not spend minutes hashing the common files on every installation poll.
    for artifact in artifacts:
        try:
            destination = _artifact_path(models_dir, artifact)
            if partial_path_for(destination).exists():
                return ComponentReadiness(
                    "not_ready", "model_partial", required_count, 0
                )
            info = destination.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or destination.is_symlink()
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                return ComponentReadiness(
                    "not_ready", "model_unsafe", required_count, 0
                )
            if info.st_size != artifact.expected_bytes:
                return ComponentReadiness(
                    "not_ready", "model_size_mismatch", required_count, 0
                )
            paths.append((destination, artifact))
        except FileNotFoundError:
            return ComponentReadiness("not_ready", "model_missing", required_count, 0)
        except OSError:
            return ComponentReadiness("not_ready", "model_unreadable", required_count, 0)
        except _ModelEvidenceError as error:
            return ComponentReadiness("not_ready", error.code, required_count, 0)

    ready_count = 0
    for destination, artifact in paths:
        try:
            digest = digest_cache.digest(
                destination,
                expected_size=artifact.expected_bytes,
            )
        except _ModelEvidenceError as error:
            return ComponentReadiness(
                "not_ready", error.code, required_count, ready_count
            )
        if digest != artifact.sha256:
            return ComponentReadiness(
                "not_ready", "model_digest_mismatch", required_count, ready_count
            )
        ready_count += 1
    return ComponentReadiness(
        "ready", "models_exact", required_count, ready_count
    )


def _fetch_object_info(origin: str, timeout: float) -> Mapping[str, Any]:
    if origin != COMFY_ORIGIN:
        raise MiniMaxH3ReadinessError("runtime_origin_unsafe")
    connection = http.client.HTTPConnection("127.0.0.1", 8188, timeout=timeout)
    try:
        connection.request(
            "GET",
            OBJECT_INFO_PATH,
            headers={
                "Accept": "application/json",
                "User-Agent": "castle-h3-readiness/1",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise MiniMaxH3ReadinessError("runtime_http_unavailable")
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                announced = int(length)
            except ValueError as error:
                raise MiniMaxH3ReadinessError("runtime_response_invalid") from error
            if announced < 0 or announced > MAX_OBJECT_INFO_BYTES:
                raise MiniMaxH3ReadinessError("runtime_response_too_large")
        raw = response.read(MAX_OBJECT_INFO_BYTES + 1)
        if len(raw) > MAX_OBJECT_INFO_BYTES:
            raise MiniMaxH3ReadinessError("runtime_response_too_large")
        try:
            payload = json.loads(
                raw.decode("utf-8", "strict"),
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise MiniMaxH3ReadinessError("runtime_response_invalid") from error
        if not isinstance(payload, dict):
            raise MiniMaxH3ReadinessError("runtime_response_invalid")
        return payload
    except (OSError, http.client.HTTPException) as error:
        raise MiniMaxH3ReadinessError("runtime_unreachable") from error
    finally:
        connection.close()


def _runtime_components(
    *,
    origin: str,
    timeout: float,
    contracts: Sequence[H3ReadinessContract],
    object_info_fetcher: ObjectInfoFetcher,
) -> tuple[dict[str, ComponentReadiness], dict[str, str | None]]:
    try:
        object_info = object_info_fetcher(origin, timeout)
        if not isinstance(object_info, Mapping):
            raise MiniMaxH3ReadinessError("runtime_response_invalid")
    except MiniMaxH3ReadinessError as error:
        return (
            {
                contract.contract_id: ComponentReadiness(
                    "not_ready", error.code, len(contract.required_nodes), 0
                )
                for contract in contracts
            },
            {contract.contract_id: None for contract in contracts},
        )
    except Exception:
        return (
            {
                contract.contract_id: ComponentReadiness(
                    "not_ready", "runtime_unreachable", len(contract.required_nodes), 0
                )
                for contract in contracts
            },
            {contract.contract_id: None for contract in contracts},
        )

    result: dict[str, ComponentReadiness] = {}
    digests: dict[str, str | None] = {}
    for contract in contracts:
        required = contract.required_nodes
        ready_count = sum(
            1 for node in required if isinstance(object_info.get(node), dict)
        )
        if ready_count != len(required):
            result[contract.contract_id] = ComponentReadiness(
                "not_ready", "required_nodes_missing", len(required), ready_count
            )
            digests[contract.contract_id] = None
            continue
        try:
            schema_digest = _runtime_schema_digest(object_info, required)
        except MiniMaxH3ReadinessError as error:
            result[contract.contract_id] = ComponentReadiness(
                "not_ready", error.code, len(required), 0
            )
            digests[contract.contract_id] = None
            continue
        result[contract.contract_id] = ComponentReadiness(
            "ready", "required_node_schemas_exact", len(required), ready_count
        )
        digests[contract.contract_id] = schema_digest
    return result, digests


def audit_minimax_h3_readiness(
    config: MiniMaxH3ReadinessConfig | None = None,
    *,
    digest_cache: ModelDigestCache | None = None,
    object_info_fetcher: ObjectInfoFetcher = _fetch_object_info,
    now_epoch: int | None = None,
) -> MiniMaxH3ReadinessSnapshot:
    """Collect all four gates without writes, generation, or provider I/O."""

    selected = config or MiniMaxH3ReadinessConfig()
    if not isinstance(selected, MiniMaxH3ReadinessConfig):
        raise TypeError("config must be MiniMaxH3ReadinessConfig")
    if digest_cache is not None and not isinstance(digest_cache, ModelDigestCache):
        raise TypeError("digest_cache must be ModelDigestCache")
    if not callable(object_info_fetcher):
        raise TypeError("object_info_fetcher must be callable")
    current = (
        int(datetime.now(timezone.utc).timestamp())
        if now_epoch is None
        else now_epoch
    )
    if type(current) is not int or current < 0:
        raise ValueError("now_epoch is invalid")

    contracts = tuple(H3_CONTRACTS[value] for value in selected.contract_ids)
    try:
        model_manifest = load_manifest(selected.model_manifest_path)
        workflow_manifest = load_manifest(selected.workflow_manifest_path)
        raw_nodes = workflow_manifest.raw.get("required_object_info_nodes")
        if (
            not isinstance(raw_nodes, list)
            or len(raw_nodes) != len(set(raw_nodes))
            or any(type(node) is not str for node in raw_nodes)
            or frozenset(raw_nodes) != _LOCKED_REQUIRED_NODES
        ):
            raise ArtifactError("workflow node lock does not match the native H3 contract")
        family_artifacts = {
            family: select_family(model_manifest.artifacts, family)
            for family in {contract.family for contract in contracts}
        }
    except (ArtifactError, OSError, ValueError):
        invalid = ComponentReadiness("invalid", "readiness_manifest_invalid")
        skipped = ComponentReadiness("not_ready", "configuration_invalid")
        return MiniMaxH3ReadinessSnapshot(
            tuple(
                ContractReadiness(
                    contract.contract_id,
                    contract.family,
                    contract.visual_class,
                    invalid,
                    skipped,
                    skipped,
                    skipped,
                    skipped,
                )
                for contract in contracts
            ),
            loopback_probe_attempted=False,
        )

    configuration = ComponentReadiness("ready", "configuration_valid")
    cache = digest_cache or _GLOBAL_DIGEST_CACHE
    authorization_by_family: dict[str, ComponentReadiness] = {}
    models_by_family: dict[str, ComponentReadiness] = {}
    for family, artifacts in family_artifacts.items():
        try:
            decision = evaluate_local_inference_authorization(
                selected.authorization_record_path,
                manifest=model_manifest,
                family=family,
                now_epoch=current,
            )
            authorization_by_family[family] = ComponentReadiness(
                "ready" if decision.authorized else "blocked",
                decision.reason,
            )
        except Exception:
            authorization_by_family[family] = ComponentReadiness(
                "blocked", "authorization_evaluation_failed"
            )
        models_by_family[family] = _model_component(
            models_dir=selected.models_dir,
            artifacts=artifacts,
            digest_cache=cache,
        )
    runtime, runtime_schema_digests = _runtime_components(
        origin=selected.comfy_origin,
        timeout=selected.timeout_seconds,
        contracts=contracts,
        object_info_fetcher=object_info_fetcher,
    )
    golden = _golden_components(
        record_path=selected.golden_record_path,
        contracts=contracts,
        model_manifest=model_manifest,
        workflow_manifest=workflow_manifest,
        runtime_schema_digests=runtime_schema_digests,
        now_epoch=current,
    )
    return MiniMaxH3ReadinessSnapshot(
        tuple(
            ContractReadiness(
                contract_id=contract.contract_id,
                family=contract.family,
                visual_class=contract.visual_class,
                configuration=configuration,
                authorization=authorization_by_family[contract.family],
                models=models_by_family[contract.family],
                runtime=runtime[contract.contract_id],
                golden=golden[contract.contract_id],
            )
            for contract in contracts
        ),
        loopback_probe_attempted=True,
    )


def golden_binding_values(
    *,
    contract_id: str,
    object_info: Mapping[str, Any],
    model_manifest_path: Path = DEFAULT_MODEL_MANIFEST,
    workflow_manifest_path: Path = DEFAULT_WORKFLOW_MANIFEST,
) -> dict[str, Any]:
    """Return public lock bindings an offline receipt issuer must attest.

    This helper performs no writes and intentionally does not issue, approve,
    or store a golden receipt.
    """

    contract = H3_CONTRACTS.get(contract_id)
    if contract is None:
        raise ValueError("contract_id is invalid")
    if not isinstance(object_info, Mapping):
        raise ValueError("object_info must be a mapping")
    model_manifest = load_manifest(Path(model_manifest_path))
    workflow_manifest = load_manifest(Path(workflow_manifest_path))
    raw_nodes = workflow_manifest.raw.get("required_object_info_nodes")
    if (
        not isinstance(raw_nodes, list)
        or len(raw_nodes) != len(set(raw_nodes))
        or any(type(node) is not str for node in raw_nodes)
        or frozenset(raw_nodes) != _LOCKED_REQUIRED_NODES
    ):
        raise ArtifactError("workflow node lock does not match the native H3 contract")
    return {
        "contract_id": contract.contract_id,
        "family": contract.family,
        "visual_class": contract.visual_class,
        "graph_contract_sha256": graph_contract_sha256(contract.contract_id),
        "runtime_schema_sha256": _runtime_schema_digest(
            object_info, contract.required_nodes
        ),
        "model_manifest_sha256": manifest_scope_sha256(model_manifest),
        "model_revision": model_manifest.revision,
        "workflow_manifest_sha256": manifest_scope_sha256(workflow_manifest),
        "workflow_revision": workflow_manifest.revision,
    }


__all__ = [
    "COMFY_ORIGIN",
    "DEFAULT_GOLDEN_RECORD",
    "DEFAULT_MODEL_MANIFEST",
    "DEFAULT_MODELS_DIR",
    "DEFAULT_WORKFLOW_MANIFEST",
    "ComponentReadiness",
    "ContractReadiness",
    "FileIdentity",
    "H3_CONTRACTS",
    "H3ReadinessContract",
    "KNOWN_CONTRACT_IDS",
    "MiniMaxH3ReadinessConfig",
    "MiniMaxH3ReadinessError",
    "MiniMaxH3ReadinessSnapshot",
    "ModelDigestCache",
    "audit_minimax_h3_readiness",
    "graph_contract_sha256",
    "golden_binding_values",
]
