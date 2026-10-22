"""Dormant end-to-end Novel Scene Art Castle worker."""

from __future__ import annotations

import hashlib
import os
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol

from .compiler import CompiledGraph, compile_graph, deterministic_prompt_id
from .comfy_client import ComfyClient, require_gpu_headroom, verify_loopback_listener
from .contracts import RenderClaim, canonical_render_receipt, parse_claim, strict_json
from .errors import SceneArtError, fail
from .gpu_lock import PRODUCTION_GPU_LOCK, SharedGpuLock
from .journal import Attempt, RenderJournal
from .manifests import (
    ACTIVATION_BLOCKERS,
    ManifestPreflight,
    REPO_ROOT,
    RenderRelease,
    inspect_release_manifest,
    load_approved_release,
    verify_release_artifacts,
)
from .png import sanitize_png
from .protocol import (
    OUTPUT_HEIGHT,
    OUTPUT_MIME,
    OUTPUT_WIDTH,
    RENDER_RECEIPT_SCHEMA,
    WORKFLOW_CONTRACT_REVISION,
    safe_id,
)
from .screening import OutputScreener, UnavailableScreener, require_passed_decision
from .secure_path import (
    DIRECTORY_FLAGS,
    READ_FILE_FLAGS,
    close_descriptors,
    open_absolute_directory,
)
from .transport import OracleTransport, SecretToken, validate_oracle_origin


DEFAULT_DISABLED_MANIFEST = REPO_ROOT / "manifests/novel_scene_art/render-release.disabled.json"
PRODUCTION_STATE_ROOT = (Path.home() / '.local/share/web-comfyui/novel-scene-art')
PRODUCTION_COMFY_OUTPUT_ROOT = (Path.home() / 'comfyui/output')
PRODUCTION_TOKEN_FILE = (Path.home() / '.config/web-comfyui-novel-scene-art.token')
PRODUCTION_ENV_FILE = (Path.home() / '.config/web-comfyui-novel-scene-art.env')
DEFAULT_STATE_ROOT = PRODUCTION_STATE_ROOT
RUN_MODES = frozenset({"off", "preflight", "run"})
SERVER_V2_PAYLOAD_HASH_PROFILE = "canonical-render-request-envelope-v2"
CAPABILITY_FENCE_ERRORS = frozenset(
    {"capability_rejected", "capability_expiry_invalid", "capability_changed"}
)


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    mode: str
    worker_id: str
    oracle_origin: str
    manifest_path: Path
    journal_path: Path
    comfy_output_root: Path
    gpu_lock_path: Path
    render_timeout_seconds: int
    idle_poll_seconds: int

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        mode = os.environ.get("CASTLE_NOVEL_SCENE_ART_MODE", "off")
        if mode not in RUN_MODES:
            fail("worker_mode_invalid", "Scene-art worker mode is invalid")
        worker_id = os.environ.get("CASTLE_NOVEL_SCENE_ART_WORKER_ID", "local-workstation-scene-art")
        safe_id(worker_id, "worker_id", worker=True)
        configured_origin = os.environ.get("AISISTER_BASE", "").strip()
        oracle_origin = validate_oracle_origin(configured_origin) if configured_origin else ""
        if mode == "run" and not oracle_origin:
            fail("oracle_origin_unconfigured", "Set AISISTER_BASE before enabling the remote worker")

        def absolute(name: str, default: Path) -> Path:
            raw = Path(os.environ.get(name, str(default))).expanduser()
            if not raw.is_absolute() or "\x00" in os.fspath(raw):
                fail("worker_path_invalid", f"{name} must be an absolute path")
            return Path(os.path.abspath(os.fspath(raw)))

        def bounded_integer(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError as error:
                raise SceneArtError("worker_setting_invalid", f"{name} is invalid") from error
            if value < low or value > high:
                fail("worker_setting_invalid", f"{name} is out of range")
            return value

        settings = cls(
            mode,
            worker_id,
            oracle_origin,
            absolute("CASTLE_NOVEL_SCENE_ART_MANIFEST", DEFAULT_DISABLED_MANIFEST),
            absolute("CASTLE_NOVEL_SCENE_ART_JOURNAL", DEFAULT_STATE_ROOT / "journal.sqlite3"),
            absolute("CASTLE_COMFY_OUTPUT_ROOT", PRODUCTION_COMFY_OUTPUT_ROOT),
            absolute("CASTLE_GPU_LOCK_PATH", PRODUCTION_GPU_LOCK),
            bounded_integer("CASTLE_NOVEL_SCENE_ART_RENDER_TIMEOUT_SECONDS", 6_000, 60, 7_000),
            bounded_integer("CASTLE_NOVEL_SCENE_ART_IDLE_POLL_SECONDS", 15, 2, 300),
        )
        if mode == "run":
            validate_production_paths(settings)
            validate_environment_file()
        return settings


def validate_production_paths(settings: WorkerSettings) -> None:
    if (
        settings.gpu_lock_path != PRODUCTION_GPU_LOCK
        or settings.comfy_output_root != PRODUCTION_COMFY_OUTPUT_ROOT
        or settings.journal_path.parent != PRODUCTION_STATE_ROOT
        or settings.manifest_path.parent != PRODUCTION_STATE_ROOT
    ):
        fail("worker_path_not_pinned", "Production worker paths are not exactly pinned")
    for path in (settings.comfy_output_root, settings.journal_path.parent):
        try:
            info = path.lstat()
        except OSError as error:
            raise SceneArtError("worker_path_unavailable", "A pinned production root is unavailable") from error
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
        ):
            fail("worker_path_unsafe", "A pinned production root is unsafe")


def validate_environment_file(
    path: Path = PRODUCTION_ENV_FILE,
    *,
    allowed_path: Path = PRODUCTION_ENV_FILE,
) -> None:
    """Require the non-secret systemd settings file itself to be owner-private."""

    if path != allowed_path or not path.is_absolute() or "\x00" in os.fspath(path):
        fail("worker_environment_file_invalid", "Worker environment file path is invalid")
    try:
        parent = path.parent.lstat()
        info = path.lstat()
    except OSError as error:
        raise SceneArtError(
            "worker_environment_file_missing", "Worker environment file is unavailable"
        ) from error
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o022
        or not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
    ):
        fail("worker_environment_file_unsafe", "Worker environment file is not owner-private")


@dataclass(frozen=True, slots=True)
class WorkerPreflight:
    ready: bool
    blockers: tuple[str, ...]
    mode: str
    manifest: ManifestPreflight

    def public_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "mode": self.mode,
            "blockers": list(self.blockers),
            "manifest": self.manifest.public_dict(),
        }


def preflight(
    settings: WorkerSettings,
    *,
    screener: OutputScreener | None = None,
    payload_hash_profile: str | None = SERVER_V2_PAYLOAD_HASH_PROFILE,
) -> WorkerPreflight:
    placeholder = settings.manifest_path == DEFAULT_DISABLED_MANIFEST
    manifest = inspect_release_manifest(
        settings.manifest_path,
        require_private_manifest=not placeholder,
        require_production_runtime=True,
    )
    blockers = list(manifest.blockers)
    # These are code/runtime qualification gates, not manifest deficiencies.
    # Report them even when the checked-in disabled descriptor is incomplete.
    blockers.extend(ACTIVATION_BLOCKERS)
    if settings.mode != "run":
        blockers.append("worker_mode_disabled")
    if payload_hash_profile != SERVER_V2_PAYLOAD_HASH_PROFILE:
        blockers.append("server_v2_payload_hash_profile_unconfigured")
    if screener is None or not screener.ready:
        blockers.append("output_screening_adapter_unavailable")
    if settings.mode == "run":
        try:
            validate_production_paths(settings)
        except SceneArtError as error:
            blockers.append(error.code)
    try:
        repository = REPO_ROOT.resolve(strict=True)
        manifest_path = settings.manifest_path.resolve(strict=False)
        if settings.mode == "run" and os.path.commonpath((str(repository), str(manifest_path))) == str(repository):
            blockers.append("production_manifest_must_be_private")
    except (OSError, ValueError):
        blockers.append("manifest_path_unavailable")
    return WorkerPreflight(
        not blockers and manifest.ready,
        tuple(dict.fromkeys(blockers)),
        settings.mode,
        manifest,
    )


def token_from_environment(*, allowed_path: Path = PRODUCTION_TOKEN_FILE) -> SecretToken:
    """Load only an owner-private token file; raw token environment values are refused."""

    if os.environ.get("NOVEL_SCENE_ART_WORKER_TOKEN"):
        fail("worker_token_source_unsafe", "Raw worker tokens in the environment are forbidden")
    raw_path = os.environ.get(
        "NOVEL_SCENE_ART_WORKER_TOKEN_FILE",
        str(PRODUCTION_TOKEN_FILE),
    )
    path = Path(raw_path)
    if not path.is_absolute() or "\x00" in raw_path or path != allowed_path:
        fail("worker_token_file_invalid", "Worker token file path is invalid")
    descriptors: list[int] = []
    try:
        descriptors = open_absolute_directory(
            path.parent,
            code="worker_token_missing",
            message="Worker token directory is unavailable",
        )
        parent_fd = descriptors[-1]
        parent_info = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or parent_info.st_mode & 0o022
        ):
            fail("worker_token_file_unsafe", "Worker token directory is unsafe")
        descriptor = os.open(path.name, READ_FILE_FLAGS, dir_fd=parent_fd)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
            or info.st_size < 32
            or info.st_size > 513
        ):
            fail("worker_token_file_unsafe", "Worker token file is not owner-private")
        raw = os.read(descriptor, 514)
        after = os.fstat(descriptor)
        if (
            len(raw) != info.st_size
            or (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            fail("worker_token_file_changed", "Worker token file changed while being read")
    except SceneArtError:
        raise
    except OSError as error:
        raise SceneArtError("worker_token_missing", "Worker token file is unavailable") from error
    finally:
        close_descriptors(descriptors)
    try:
        value = raw.decode("ascii", "strict").rstrip("\n")
    except UnicodeError as error:
        raise SceneArtError("worker_token_invalid", "Worker token is invalid") from error
    if "\n" in value or "\r" in value:
        fail("worker_token_invalid", "Worker token file has extra lines")
    return SecretToken(value)


class GpuLock(Protocol):
    def try_acquire(self) -> bool: ...

    def release(self) -> None: ...


class SceneArtWorker:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        release: RenderRelease,
        oracle: OracleTransport,
        comfy: ComfyClient,
        journal: RenderJournal,
        screener: OutputScreener,
        gpu_lock_factory: Callable[[], GpuLock] | None = None,
        gpu_headroom_check: Callable[[], object] = require_gpu_headroom,
        listener_check: Callable[[], object] = verify_loopback_listener,
        artifact_check: Callable[[RenderRelease], None] = verify_release_artifacts,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.mode != "run":
            fail("worker_mode_disabled", "Worker execution is disabled")
        if release.activation_blockers:
            fail("worker_activation_blocked", "Renderer runtime qualifications are incomplete")
        if not screener.ready:
            fail("output_screening_unavailable", "Output screening adapter is unavailable")
        self.settings = settings
        self.release = release
        self.oracle = oracle
        self.comfy = comfy
        self.journal = journal
        self.screener = screener
        self._gpu_lock_factory = gpu_lock_factory or (lambda: SharedGpuLock(settings.gpu_lock_path))
        self._gpu_headroom_check = gpu_headroom_check
        self._listener_check = listener_check
        self._artifact_check = artifact_check
        self._clock = clock
        self._sleep = sleep

    def _capability_handshake(self) -> tuple[str, int]:
        response = self.oracle.put_capability(self.settings.worker_id, self.release)
        expires_at = int(response["expiresAt"])
        now_ms = int(self._clock() * 1_000)
        if expires_at <= now_ms or expires_at > now_ms + 90_000:
            fail("capability_expiry_invalid", "Oracle capability freshness window is invalid")
        return self.release.capability_digest, expires_at

    def _renew_if_needed(self, claim: RenderClaim, lease_expires_ms: int, *, force: bool = False) -> int:
        if not force and lease_expires_ms - int(self._clock() * 1_000) > 240_000:
            return lease_expires_ms
        renewed = self.oracle.renew_lease(claim.job_id, claim.request_id, claim.lease_token)
        if renewed <= int(self._clock() * 1_000) + 30_000:
            fail("lease_refresh_invalid", "Oracle lease was not renewed safely")
        return renewed

    @contextmanager
    def _acquire_gpu(
        self,
        claim: RenderClaim,
        capability_digest: str,
        capability_expires_ms: int,
    ) -> Iterator[None]:
        lock = self._gpu_lock_factory()
        deadline = self._clock() + min(300, self.settings.render_timeout_seconds)
        lease_expires = claim.lease_expires_at_ms
        try:
            while not lock.try_acquire():
                if self._clock() >= deadline:
                    fail("gpu_lock_timeout", "Timed out waiting for the shared GPU")
                if capability_expires_ms - int(self._clock() * 1_000) <= 30_000:
                    refreshed, capability_expires_ms = self._capability_handshake()
                    if refreshed != capability_digest:
                        fail("capability_changed", "Worker capability changed while waiting")
                lease_expires = self._renew_if_needed(claim, lease_expires)
                self._sleep(1.0)
            # Revalidate both network fences while holding the exact shared lock.
            refreshed, _expires = self._capability_handshake()
            if refreshed != capability_digest:
                fail("capability_changed", "Worker capability changed before submission")
            self._renew_if_needed(claim, lease_expires, force=True)
            yield
        finally:
            lock.release()

    def _wait_for_history(
        self,
        claim: RenderClaim,
        compiled: CompiledGraph,
        capability_digest: str,
        capability_expires_ms: int,
    ):
        deadline = self._clock() + self.settings.render_timeout_seconds
        lease_expires = claim.lease_expires_at_ms
        while self._clock() < deadline:
            history = self.comfy.history(compiled.prompt_id)
            if history is not None:
                self.comfy.verify_history_graph(history, compiled)
                status = history.get("status")
                if not isinstance(status, dict):
                    fail("comfy_history_invalid", "ComfyUI history status is invalid", definite_pre_submit=False)
                if status.get("status_str") != "success" or status.get("completed") is not True:
                    fail("comfy_render_failed", "ComfyUI render failed", definite_pre_submit=False)
                return history
            if capability_expires_ms - int(self._clock() * 1_000) <= 30_000:
                refreshed, capability_expires_ms = self._capability_handshake()
                if refreshed != capability_digest:
                    fail(
                        "capability_changed",
                        "Worker capability changed during rendering",
                        definite_pre_submit=False,
                    )
            lease_expires = self._renew_if_needed(claim, lease_expires)
            self._sleep(1.0)
        fail("comfy_history_timeout", "ComfyUI render deadline expired", definite_pre_submit=False)

    def _render_or_adopt(
        self,
        claim: RenderClaim,
        compiled: CompiledGraph,
        attempt: Attempt,
        capability_digest: str,
        capability_expires_ms: int,
    ) -> Attempt | None:
        with self._acquire_gpu(claim, capability_digest, capability_expires_ms):
            state = attempt.state
            if state == "claimed":
                self._artifact_check(self.release)
                self.comfy.verify_graph_nodes(compiled)
                refreshed, _expires = self._capability_handshake()
                if refreshed != capability_digest:
                    fail("capability_changed", "Worker capability changed before submission")
                self._renew_if_needed(claim, claim.lease_expires_at_ms, force=True)
                self._listener_check()
                self.comfy.require_queue_idle()
                self._gpu_headroom_check()
                if not self.journal.begin_submit(
                    claim.job_id,
                    compiled_graph_sha256=compiled.graph_sha256,
                    output_node_id=compiled.output_node_id,
                    output_prefix=compiled.output_prefix,
                ):
                    fail("journal_state_conflict", "Submission intent changed concurrently")
                try:
                    self.comfy.submit(compiled)
                except BaseException:
                    # The POST can have succeeded even when its reply was lost.
                    self.journal.mark_uncertain(claim.job_id, "comfy_submission_outcome_unknown")
                    return None
                self.journal.mark_submitted(claim.job_id)
            elif state in {"submit_intent", "uncertain"}:
                history = self.comfy.history(compiled.prompt_id)
                if history is None:
                    if state == "submit_intent":
                        self.journal.mark_uncertain(claim.job_id, "comfy_submission_outcome_unknown")
                    return None
                self.comfy.verify_history_graph(history, compiled)
                self.journal.mark_adopted(claim.job_id)
            elif state not in {
                "submitted",
                "renderer_bound",
                "output_staged",
                "result_intent",
                "result_admitted",
                "upload_intent",
                "uploaded",
                "completed",
                "rejected_cleanup",
                "failed",
            }:
                fail("journal_state_conflict", "Render attempt cannot resume")

            refreshed = self.journal.get(claim.job_id)
            assert refreshed is not None
            if refreshed.state == "rejected_cleanup":
                self._cleanup_rejected(refreshed)
                self.journal.mark_rejected_cleaned(refreshed.job_id)
                return self.journal.get(refreshed.job_id)
            if refreshed.state == "failed":
                return refreshed
            if refreshed.state not in {"submitted", "renderer_bound"}:
                return refreshed
            if refreshed.state == "submitted":
                history = self._wait_for_history(
                    claim,
                    compiled,
                    capability_digest,
                    capability_expires_ms,
                )
                bound = self.comfy.bind_output(
                    history,
                    compiled,
                    maximum_bytes=claim.max_output_bytes,
                )
                renderer_sha256 = hashlib.sha256(bound.data).hexdigest()
                self.journal.bind_renderer_output(
                    claim.job_id,
                    path=bound.path,
                    sha256=renderer_sha256,
                )
                renderer_path = bound.path
                renderer_data = bound.data
            else:
                assert refreshed.renderer_output_path is not None
                assert refreshed.renderer_output_sha256 is not None
                renderer_path = Path(refreshed.renderer_output_path)
                renderer_sha256 = refreshed.renderer_output_sha256
                renderer_data = self._read_bound_file(
                    refreshed.renderer_output_path,
                    refreshed.renderer_output_sha256,
                    None,
                    self.comfy.output_root,
                    maximum=claim.max_output_bytes,
                    require_private_mode=True,
                )
            try:
                sanitized = sanitize_png(
                    renderer_data,
                    expected_width=OUTPUT_WIDTH,
                    expected_height=OUTPUT_HEIGHT,
                    maximum_bytes=claim.max_output_bytes,
                )
                content_sha256 = hashlib.sha256(sanitized.data).hexdigest()
                plan_identity = claim.render_plan["identity_manifest"]
                assert isinstance(plan_identity, dict)
                identity_slots = plan_identity["slots"]
                assert isinstance(identity_slots, list)
                decision = self.screener.screen(sanitized.data, self.release, identity_slots)
                require_passed_decision(
                    decision,
                    data=sanitized.data,
                    release=self.release,
                    identity_slots=identity_slots,
                )
                receipt: Mapping[str, object] = {
                    "schema_version": RENDER_RECEIPT_SCHEMA,
                    "workflow_contract_revision": WORKFLOW_CONTRACT_REVISION,
                    "job_id": claim.job_id,
                    "request_id": claim.request_id,
                    "generation_key": claim.generation_key,
                    "scene_pack_hash": claim.scene_pack_hash,
                    "render_plan_hash": claim.render_plan_hash,
                    "renderer_release_digest": claim.renderer_release_digest,
                    "variation_index": claim.variation_index,
                    "seed": claim.seed,
                    "execution": {
                        "adapter_id": self.release.adapter["id"],
                        "adapter_revision": self.release.adapter["revision"],
                        "prompt_id": compiled.prompt_id,
                        "compiled_intent_sha256": compiled.compiled_intent_sha256,
                        "positive_prompt_sha256": compiled.positive_prompt_sha256,
                        "negative_prompt_sha256": compiled.negative_prompt_sha256,
                        "compiled_graph_sha256": compiled.graph_sha256,
                        "screening_receipt_sha256": decision.receipt_sha256,
                    },
                    "output": {
                        "mime_type": OUTPUT_MIME,
                        "sha256": content_sha256,
                        "byte_count": len(sanitized.data),
                        "width": OUTPUT_WIDTH,
                        "height": OUTPUT_HEIGHT,
                    },
                }
                _receipt, receipt_hash = canonical_render_receipt(
                    receipt, maximum_bytes=claim.max_output_bytes
                )
                staged_path = self.journal.write_staged_png(claim.job_id, sanitized.data)
                self.journal.stage_output(
                    claim.job_id,
                    path=staged_path,
                    sha256=content_sha256,
                    byte_count=len(sanitized.data),
                    screening_receipt_sha256=decision.receipt_sha256,
                    renderer_output_path=renderer_path,
                    renderer_output_sha256=renderer_sha256,
                    render_receipt=receipt,
                    render_receipt_hash=receipt_hash,
                )
                return self.journal.get(claim.job_id)
            except SceneArtError as error:
                self.journal.begin_rejected_cleanup(claim.job_id, error.code)
                rejected = self.journal.get(claim.job_id)
                assert rejected is not None
                self._cleanup_rejected(rejected)
                self.journal.mark_rejected_cleaned(claim.job_id)
                raise

    @staticmethod
    def _relative_parts(root: Path, path_value: str) -> tuple[str, ...]:
        path = Path(path_value)
        try:
            relative = path.relative_to(root)
        except ValueError:
            fail("artifact_path_invalid", "Artifact escaped its private root", definite_pre_submit=False)
        parts = relative.parts
        if not parts or any(part in {"", ".", ".."} or "/" in part or "\x00" in part for part in parts):
            fail("artifact_path_invalid", "Artifact path is invalid", definite_pre_submit=False)
        return parts

    @classmethod
    def _read_bound_file(
        cls,
        path_value: str | None,
        expected_sha256: str | None,
        expected_bytes: int | None,
        root: Path,
        *,
        maximum: int,
        require_private_mode: bool = False,
    ) -> bytes:
        if path_value is None or expected_sha256 is None:
            fail("staged_output_missing", "Durable output binding is missing", definite_pre_submit=False)
        parts = cls._relative_parts(root, path_value)
        descriptors: list[int] = []
        try:
            descriptors = open_absolute_directory(
                root,
                code="staged_output_missing",
                message="Durable output root is unavailable",
                definite_pre_submit=False,
            )
            descriptor = descriptors[-1]
            root_info = os.fstat(descriptor)
            if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
                fail("artifact_path_invalid", "Artifact root is not owner-controlled", definite_pre_submit=False)
            for component in parts[:-1]:
                descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
                descriptors.append(descriptor)
                directory_info = os.fstat(descriptor)
                if directory_info.st_uid != os.geteuid() or directory_info.st_mode & 0o077:
                    fail("artifact_path_invalid", "Artifact directory is not owner-controlled", definite_pre_submit=False)
            descriptor = os.open(
                parts[-1],
                READ_FILE_FLAGS,
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or before.st_size <= 0
                or before.st_size > maximum
                or (expected_bytes is not None and before.st_size != expected_bytes)
                or (require_private_mode and stat.S_IMODE(before.st_mode) != 0o600)
            ):
                fail("staged_output_changed", "Durable output binding changed", definite_pre_submit=False)
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
        except SceneArtError:
            raise
        except OSError as error:
            raise SceneArtError(
                "staged_output_missing", "Durable output is unavailable", definite_pre_submit=False
            ) from error
        finally:
            close_descriptors(descriptors)
        bound = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if (
            len(data) != before.st_size
            or bound(before) != bound(after)
            or hashlib.sha256(data).hexdigest() != expected_sha256
        ):
            fail("staged_output_changed", "Durable output binding changed", definite_pre_submit=False)
        return data

    def _read_exact_staged(self, attempt: Attempt) -> bytes:
        return self._read_bound_file(
            attempt.output_path,
            attempt.output_sha256,
            attempt.output_bytes,
            self.journal.state_root,
            maximum=24 * 1024 * 1024,
            require_private_mode=True,
        )

    @staticmethod
    def _receipt(attempt: Attempt) -> tuple[Mapping[str, object], str]:
        if attempt.render_receipt_json is None or attempt.render_receipt_hash is None:
            fail("render_receipt_missing", "Durable render receipt is missing", definite_pre_submit=False)
        value = strict_json(attempt.render_receipt_json.encode("utf-8"), maximum=8 * 1024)
        receipt, receipt_hash = canonical_render_receipt(value, maximum_bytes=24 * 1024 * 1024)
        execution = receipt["execution"]
        output = receipt["output"]
        assert isinstance(execution, dict) and isinstance(output, dict)
        if (
            receipt_hash != attempt.render_receipt_hash
            or receipt["job_id"] != attempt.job_id
            or receipt["request_id"] != attempt.request_id
            or receipt["generation_key"] != attempt.generation_key
            or receipt["scene_pack_hash"] != attempt.scene_pack_hash
            or receipt["render_plan_hash"] != attempt.render_plan_hash
            or receipt["renderer_release_digest"] != attempt.release_sha256
            or receipt["variation_index"] != attempt.variation_index
            or receipt["seed"] != attempt.seed
            or execution["prompt_id"] != attempt.prompt_id
            or execution["screening_receipt_sha256"] != attempt.screening_receipt_sha256
            or output["sha256"] != attempt.output_sha256
            or output["byte_count"] != attempt.output_bytes
        ):
            fail("render_receipt_changed", "Durable render receipt changed", definite_pre_submit=False)
        return receipt, receipt_hash

    def _deliver(self, attempt: Attempt) -> Attempt:
        current = attempt
        receipt, receipt_hash = self._receipt(current)
        if current.state == "output_staged":
            self.journal.begin_result_intent(current.job_id)
            current = self.journal.get(current.job_id)
            assert current is not None
        if current.state == "result_intent":
            self._capability_handshake()
            submission_id, _replayed = self.oracle.create_result_intent(
                job_id=current.job_id,
                request_id=current.request_id,
                lease_token=current.lease_token,
                render_receipt=receipt,
                receipt_hash=receipt_hash,
            )
            self.journal.admit_result(current.job_id, submission_id)
            current = self.journal.get(current.job_id)
            assert current is not None
        if current.state == "result_admitted":
            self.journal.begin_upload(current.job_id)
            current = self.journal.get(current.job_id)
            assert current is not None
        if current.state == "upload_intent":
            self._capability_handshake()
            data = self._read_exact_staged(current)
            if current.submission_id is None or current.output_sha256 is None:
                fail("result_binding_missing", "Durable upload binding is missing", definite_pre_submit=False)
            self.oracle.upload_result(
                job_id=current.job_id,
                submission_id=current.submission_id,
                request_id=current.request_id,
                lease_token=current.lease_token,
                receipt_hash=receipt_hash,
                content_sha256=current.output_sha256,
                data=data,
            )
            self.journal.mark_uploaded(current.job_id)
            current = self.journal.get(current.job_id)
            assert current is not None
        return current

    @classmethod
    def _delete_if_exact(cls, path_value: str | None, expected_sha256: str | None, root: Path) -> None:
        if path_value is None or expected_sha256 is None:
            return
        parts = cls._relative_parts(root, path_value)
        descriptors: list[int] = []
        try:
            descriptors = open_absolute_directory(
                root,
                code="cleanup_path_invalid",
                message="Cleanup root is unavailable",
                definite_pre_submit=False,
            )
            descriptor = descriptors[-1]
            root_info = os.fstat(descriptor)
            if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
                fail("cleanup_path_invalid", "Cleanup root is not owner-controlled", definite_pre_submit=False)
            for component in parts[:-1]:
                descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
                descriptors.append(descriptor)
                directory_info = os.fstat(descriptor)
                if directory_info.st_uid != os.geteuid() or directory_info.st_mode & 0o077:
                    fail("cleanup_path_invalid", "Cleanup directory is not owner-controlled", definite_pre_submit=False)
            parent_fd = descriptor
            try:
                file_fd = os.open(
                    parts[-1],
                    READ_FILE_FLAGS,
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                return
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or before.st_size < 0
                or before.st_size > 24 * 1024 * 1024
            ):
                fail("cleanup_path_invalid", "Cleanup target is unsafe", definite_pre_submit=False)
            digestor = hashlib.sha256()
            total = 0
            while chunk := os.read(file_fd, 1024 * 1024):
                total += len(chunk)
                if total > 24 * 1024 * 1024:
                    fail("cleanup_path_invalid", "Cleanup target is too large", definite_pre_submit=False)
                digestor.update(chunk)
            linked = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            bound = lambda item: (
                item.st_dev,
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )
            if digestor.hexdigest() != expected_sha256 or bound(before) != bound(linked):
                fail("cleanup_binding_changed", "Cleanup target binding changed", definite_pre_submit=False)
            os.unlink(parts[-1], dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileNotFoundError:
            return
        except SceneArtError:
            raise
        except OSError as error:
            raise SceneArtError(
                "cleanup_path_invalid", "Cleanup target is unavailable", definite_pre_submit=False
            ) from error
        finally:
            close_descriptors(descriptors)

    def _cleanup_acknowledged(self, attempt: Attempt) -> None:
        if attempt.state != "uploaded":
            return
        self._delete_if_exact(attempt.output_path, attempt.output_sha256, self.journal.state_root)
        self._delete_if_exact(
            attempt.renderer_output_path,
            attempt.renderer_output_sha256,
            self.comfy.output_root,
        )

    def _cleanup_rejected(self, attempt: Attempt) -> None:
        if attempt.state != "rejected_cleanup":
            return
        self.journal.cleanup_unbound_staged_png(attempt.job_id)
        self._delete_if_exact(
            attempt.renderer_output_path,
            attempt.renderer_output_sha256,
            self.comfy.output_root,
        )

    def _report_failure_intent(self, attempt: Attempt) -> None:
        if attempt.state != "failure_intent" or attempt.last_error_code is None:
            fail("journal_state_conflict", "Failure report intent is incomplete")
        self._capability_handshake()
        self.oracle.report_failure(
            attempt.job_id,
            attempt.request_id,
            attempt.lease_token,
            attempt.last_error_code,
        )
        self.journal.mark_failure_reported(attempt.job_id)

    def _recover_renderer_outputs(self) -> None:
        """Recover only outputs proven by exact, graph-bound Comfy history.

        Missing or incomplete history can describe a prompt that is still
        queued or running, so Phase 2 only rotates those rows.  It deliberately
        never scans or unlinks an unjournaled namespace.  That unresolved crash
        window is an immutable activation blocker until a qualified terminal
        proof and reconciliation design is implemented.
        """

        output = self.release.public_release["output"]
        assert isinstance(output, dict)
        maximum_bytes = int(output["max_bytes"])
        for attempt in self.journal.renderer_recovery_ready():
            try:
                if (
                    attempt.compiled_graph_sha256 is None
                    or attempt.output_node_id is None
                    or attempt.output_prefix is None
                ):
                    fail(
                        "journal_submission_binding_missing",
                        "Durable submission binding is incomplete",
                        definite_pre_submit=False,
                    )
                history = self.comfy.history(attempt.prompt_id)
                if history is None:
                    self.journal.touch_renderer_recovery(attempt.job_id, attempt.state)
                    continue
                ComfyClient.verify_history_binding(
                    history,
                    prompt_id=attempt.prompt_id,
                    graph_sha256=attempt.compiled_graph_sha256,
                )
                status = history.get("status")
                if not isinstance(status, dict):
                    fail(
                        "comfy_history_invalid",
                        "ComfyUI history status is invalid",
                        definite_pre_submit=False,
                    )
                if status.get("completed") is not True:
                    self.journal.touch_renderer_recovery(attempt.job_id, attempt.state)
                    continue
                if status.get("status_str") != "success":
                    # Some failed graphs still expose an exact SaveImage result.
                    # Bind that result only through history metadata so cleanup is
                    # durable.  Never fall back to a namespace scan: a missing
                    # output record is covered by the activation blocker.
                    history_outputs = history.get("outputs")
                    has_output_items = isinstance(history_outputs, dict) and any(
                        isinstance(node_output, dict)
                        and isinstance(node_output.get("images"), list)
                        and any(
                            isinstance(item, dict) and item.get("type") == "output"
                            for item in node_output["images"]
                        )
                        for node_output in history_outputs.values()
                    )
                    if has_output_items:
                        bound = self.comfy.bind_persisted_output(
                            history,
                            prompt_id=attempt.prompt_id,
                            graph_sha256=attempt.compiled_graph_sha256,
                            output_node_id=attempt.output_node_id,
                            output_prefix=attempt.output_prefix,
                            maximum_bytes=maximum_bytes,
                        )
                        renderer_sha256 = hashlib.sha256(bound.data).hexdigest()
                        self.journal.bind_failed_renderer_output(
                            attempt.job_id,
                            path=bound.path,
                            sha256=renderer_sha256,
                            error_code="comfy_render_failed",
                        )
                    else:
                        self.journal.begin_renderer_failure_intent(
                            attempt.job_id,
                            "comfy_render_failed",
                        )
                    continue
                bound = self.comfy.bind_persisted_output(
                    history,
                    prompt_id=attempt.prompt_id,
                    graph_sha256=attempt.compiled_graph_sha256,
                    output_node_id=attempt.output_node_id,
                    output_prefix=attempt.output_prefix,
                    maximum_bytes=maximum_bytes,
                )
                renderer_sha256 = hashlib.sha256(bound.data).hexdigest()
                if attempt.state != "submitted":
                    self.journal.mark_adopted(attempt.job_id)
                self.journal.bind_renderer_output(
                    attempt.job_id,
                    path=bound.path,
                    sha256=renderer_sha256,
                )
            except SceneArtError as error:
                current = self.journal.get(attempt.job_id)
                assert current is not None
                if current.state in {"submit_intent", "submitted", "uncertain"}:
                    self.journal.mark_renderer_recovery_pending(current.job_id, error.code)

    def recover_durable_deliveries(self) -> None:
        self._recover_renderer_outputs()
        for attempt in self.journal.renderer_orphan_ready():
            try:
                self.journal.begin_rejected_cleanup(
                    attempt.job_id,
                    "comfy_output_recovery_abandoned",
                )
            except SceneArtError:
                current = self.journal.get(attempt.job_id)
                assert current is not None
                if current.state == "renderer_bound":
                    # Keep the exact raw binding for the next bounded retry.
                    continue
        for attempt in self.journal.rejection_ready():
            try:
                self._cleanup_rejected(attempt)
                self.journal.mark_rejected_cleaned(attempt.job_id)
            except SceneArtError as error:
                current = self.journal.get(attempt.job_id)
                assert current is not None
                self.journal.record_recovery_error(current.job_id, current.state, error.code)
        for attempt in self.journal.failure_ready():
            try:
                self._report_failure_intent(attempt)
            except SceneArtError as error:
                if error.code in CAPABILITY_FENCE_ERRORS:
                    raise
                current = self.journal.get(attempt.job_id)
                assert current is not None
                self.journal.record_recovery_error(current.job_id, current.state, error.code)
        for attempt in self.journal.delivery_ready():
            try:
                current = self._deliver(attempt) if attempt.state != "uploaded" else attempt
                if current.state == "uploaded":
                    self._cleanup_acknowledged(current)
                    self.journal.mark_completed(current.job_id)
            except SceneArtError as error:
                if error.code in CAPABILITY_FENCE_ERRORS:
                    raise
                current = self.journal.get(attempt.job_id)
                assert current is not None
                self.journal.record_recovery_error(current.job_id, current.state, error.code)

    def run_once(self) -> str:
        capability_digest, capability_expires = self._capability_handshake()
        # Exact receipt/output/lease bindings make recovery independent of the
        # claim endpoint. In particular an ambiguous successful PUT is replayed.
        self.recover_durable_deliveries()
        # Recovery is bounded but can still consume most of the 90-second
        # capability window. Refresh immediately before asking for a live lease.
        capability_digest, capability_expires = self._capability_handshake()
        raw_claim = self.oracle.claim(self.settings.worker_id, capability_digest)
        if raw_claim is None:
            return "idle"
        claim = parse_claim(raw_claim, release=self.release)
        attempt = self.journal.register_claim(
            job_id=claim.job_id,
            request_id=claim.request_id,
            lease_token=claim.lease_token,
            payload_hash=claim.payload_hash,
            generation_key=claim.generation_key,
            scene_pack_hash=claim.scene_pack_hash,
            render_plan_hash=claim.render_plan_hash,
            release_sha256=claim.renderer_release_digest,
            variation_index=claim.variation_index,
            seed=claim.seed,
            prompt_id=deterministic_prompt_id(claim),
        )
        try:
            compiled = compile_graph(claim, self.release)
            rendered = self._render_or_adopt(
                claim,
                compiled,
                attempt,
                capability_digest,
                capability_expires,
            )
        except SceneArtError as error:
            current = self.journal.get(claim.job_id)
            assert current is not None
            if current.state == "claimed":
                self.journal.begin_failure_intent(claim.job_id, error.code)
                current = self.journal.get(claim.job_id)
                assert current is not None
            elif current.state == "rejected_cleanup":
                self.journal.record_recovery_error(current.job_id, current.state, error.code)
                return "failure_pending"
            if current.state != "failure_intent":
                raise
            try:
                self._report_failure_intent(current)
            except SceneArtError as report_error:
                if report_error.code in CAPABILITY_FENCE_ERRORS:
                    raise
                pending = self.journal.get(claim.job_id)
                assert pending is not None
                self.journal.record_recovery_error(
                    pending.job_id,
                    pending.state,
                    report_error.code,
                )
                return "failure_pending"
            return "failed"
        if rendered is None:
            return "uncertain"
        if rendered.state in {
            "output_staged",
            "result_intent",
            "result_admitted",
            "upload_intent",
            "uploaded",
        }:
            if rendered.state != "uploaded":
                try:
                    rendered = self._deliver(rendered)
                except SceneArtError as error:
                    if error.code in CAPABILITY_FENCE_ERRORS:
                        raise
                    pending = self.journal.get(rendered.job_id)
                    assert pending is not None
                    self.journal.record_recovery_error(
                        pending.job_id,
                        pending.state,
                        error.code,
                    )
                    return "delivery_pending"
        if rendered.state == "uploaded":
            self._cleanup_acknowledged(rendered)
            self.journal.mark_completed(rendered.job_id)
            rendered = self.journal.get(rendered.job_id)
            assert rendered is not None
        return rendered.state


def build_production_worker(
    settings: WorkerSettings,
    *,
    screener: OutputScreener | None = None,
) -> SceneArtWorker:
    screening = screener or UnavailableScreener()
    report = preflight(settings, screener=screening)
    if not report.ready:
        fail("worker_preflight_blocked", "Scene-art worker preflight is blocked")
    validate_production_paths(settings)
    validate_environment_file()
    release = load_approved_release(
        settings.manifest_path,
        require_production_runtime=True,
    )
    return SceneArtWorker(
        settings=settings,
        release=release,
        oracle=OracleTransport(settings.oracle_origin, token_from_environment()),
        comfy=ComfyClient(output_root=settings.comfy_output_root),
        journal=RenderJournal(settings.journal_path),
        screener=screening,
    )
