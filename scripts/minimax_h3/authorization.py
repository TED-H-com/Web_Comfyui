#!/usr/bin/env python3
"""Read-only MiniMax H3 license/authorization scope gate.

The authorization record is an operator assertion, not a license grant.  This
module deliberately does not create that record, accept authorization through
environment variables, contact a network service, or print record contents.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from artifacts import ArtifactError, ArtifactManifest, load_manifest, select_family
else:
    from .artifacts import ArtifactError, ArtifactManifest, load_manifest, select_family


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "manifests/minimax_h3/models.lock.json"
DEFAULT_RECORD = (
    Path.home()
    / ".local"
    / "share"
    / "web-comfyui"
    / "minimax-h3-authorization"
    / "authorization.json"
)

SCHEMA_VERSION = 1
EXPECTED_LICENSE_ID = "minimax-h3-community-license"
EXPECTED_OPERATOR_ID = "operator"
REQUIRED_TERRITORY = "US"
KNOWN_USES = frozenset({"model_download", "local_inference"})
KNOWN_FAMILIES = frozenset({"fl", "ref"})
MAX_RECORD_BYTES = 16 * 1024
MAX_APPROVAL_SECONDS = 366 * 24 * 60 * 60
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,199}$")
TERRITORY_RE = re.compile(r"^[A-Z]{2}$")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Secret-free result suitable for logs and CLI output."""

    status: str
    reason: str

    @property
    def authorized(self) -> bool:
        return self.status == "authorized"


class _RecordFailure(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def default_authorization_record_path() -> Path:
    """Return the fixed out-of-repository record path without creating it."""

    return DEFAULT_RECORD


def _blocked(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(status="blocked", reason=reason)


def _authorized() -> AuthorizationDecision:
    return AuthorizationDecision(status="authorized", reason="authorization_valid")


def manifest_scope_sha256(manifest: ArtifactManifest) -> str:
    """Return the stable SHA-256 of every parsed lock-manifest field."""

    try:
        canonical = json.dumps(
            manifest.raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ArtifactError("model lock manifest cannot be canonicalized") from error
    return hashlib.sha256(canonical).hexdigest()


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


def _normalized_record_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or "\x00" in os.fspath(path):
        raise _RecordFailure("authorization_path_unsafe")
    normalized = Path(os.path.abspath(os.fspath(path)))
    if normalized.name in {"", ".", ".."}:
        raise _RecordFailure("authorization_path_unsafe")
    try:
        inside_repository = os.path.commonpath(
            (os.fspath(normalized), os.fspath(REPO_ROOT))
        ) == os.fspath(REPO_ROOT)
    except ValueError:
        inside_repository = False
    if inside_repository:
        raise _RecordFailure("authorization_path_inside_repository")
    return normalized


def _open_private_parent(path: Path) -> int:
    parts = path.parent.parts
    if not parts or parts[0] != os.path.sep:
        raise _RecordFailure("authorization_path_unsafe")
    names = parts[1:]
    if not names or any(name in {"", ".", ".."} for name in names):
        raise _RecordFailure("authorization_path_unsafe")
    try:
        current_fd = os.open(os.path.sep, _directory_flags())
    except OSError as error:
        raise _RecordFailure("authorization_path_unsafe") from error
    try:
        for name in names:
            try:
                next_fd = os.open(name, _directory_flags(), dir_fd=current_fd)
            except FileNotFoundError as error:
                raise _RecordFailure("authorization_record_missing") from error
            except OSError as error:
                raise _RecordFailure("authorization_path_unsafe") from error
            os.close(current_fd)
            current_fd = next_fd

        info = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != PRIVATE_DIRECTORY_MODE
        ):
            raise _RecordFailure("authorization_parent_not_owner_only")
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_record(path_value: str | os.PathLike[str]) -> Mapping[str, Any]:
    path = _normalized_record_path(path_value)
    parent_fd = _open_private_parent(path)
    fd = -1
    try:
        try:
            fd = os.open(path.name, _file_flags(), dir_fd=parent_fd)
        except FileNotFoundError as error:
            raise _RecordFailure("authorization_record_missing") from error
        except OSError as error:
            raise _RecordFailure("authorization_record_unsafe") from error

        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != PRIVATE_FILE_MODE
        ):
            raise _RecordFailure("authorization_record_not_owner_only")
        if opened.st_size <= 0 or opened.st_size > MAX_RECORD_BYTES:
            raise _RecordFailure("authorization_record_size_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise _RecordFailure("authorization_record_busy") from error
        except OSError as error:
            raise _RecordFailure("authorization_record_unreadable") from error
        before = os.fstat(fd)
        if (
            before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or before.st_mode != opened.st_mode
            or before.st_nlink != opened.st_nlink
            or before.st_uid != opened.st_uid
            or before.st_size != opened.st_size
            or before.st_mtime_ns != opened.st_mtime_ns
            or before.st_ctime_ns != opened.st_ctime_ns
        ):
            raise _RecordFailure("authorization_record_changed_during_read")

        chunks: list[bytes] = []
        remaining = MAX_RECORD_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        if (
            len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise _RecordFailure("authorization_record_changed_during_read")
        try:
            decoded = raw.decode("utf-8", "strict")
            payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_key)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise _RecordFailure("authorization_record_invalid_json") from error
        if not isinstance(payload, dict):
            raise _RecordFailure("authorization_schema_invalid")
        return payload
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        os.close(parent_fd)


def _exact_keys(value: Any, required: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == required


def _string_list(value: Any) -> list[str] | None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        return None
    return value


def _timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or RFC3339_UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return int(parsed.timestamp())


def _validate_record(
    payload: Mapping[str, Any],
    *,
    manifest: ArtifactManifest,
    manifest_sha256: str,
    family: str,
    required_use: str,
    now_epoch: int,
) -> AuthorizationDecision:
    if not _exact_keys(
        payload,
        {
            "schema_version",
            "authorization_id",
            "model_scope",
            "license_scope",
            "operator_approval",
            "revoked",
        },
    ):
        return _blocked("authorization_schema_invalid")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        return _blocked("authorization_schema_invalid")
    authorization_id = payload.get("authorization_id")
    if not isinstance(authorization_id, str) or OPAQUE_ID_RE.fullmatch(authorization_id) is None:
        return _blocked("authorization_schema_invalid")
    if payload.get("revoked") is True:
        return _blocked("authorization_revoked")
    if payload.get("revoked") is not False:
        return _blocked("authorization_schema_invalid")

    model_scope = payload.get("model_scope")
    if not _exact_keys(
        model_scope,
        {
            "artifact_set",
            "repository",
            "revision",
            "manifest_sha256",
            "families",
        },
    ):
        return _blocked("authorization_model_scope_invalid")
    families = _string_list(model_scope.get("families"))
    if families is None or not set(families).issubset(KNOWN_FAMILIES):
        return _blocked("authorization_model_scope_invalid")
    required_families = {"fl", "ref"} if family == "all" else {family}
    if (
        model_scope.get("artifact_set") != manifest.artifact_set
        or model_scope.get("repository") != manifest.repository
        or model_scope.get("revision") != manifest.revision
        or model_scope.get("manifest_sha256") != manifest_sha256
        or not required_families.issubset(set(families))
    ):
        return _blocked("authorization_model_scope_mismatch")

    license_scope = payload.get("license_scope")
    if not _exact_keys(
        license_scope, {"license_id", "uses", "territories", "evidence_reference"}
    ):
        return _blocked("authorization_license_scope_invalid")
    uses = _string_list(license_scope.get("uses"))
    territories = _string_list(license_scope.get("territories"))
    evidence = license_scope.get("evidence_reference")
    if (
        license_scope.get("license_id") != EXPECTED_LICENSE_ID
        or uses is None
        or not set(uses).issubset(KNOWN_USES)
        or territories is None
        or any(TERRITORY_RE.fullmatch(item) is None for item in territories)
        or not isinstance(evidence, str)
        or OPAQUE_ID_RE.fullmatch(evidence) is None
    ):
        return _blocked("authorization_license_scope_invalid")
    if required_use not in uses:
        return _blocked("authorization_use_scope_mismatch")
    if REQUIRED_TERRITORY not in territories:
        return _blocked("authorization_territory_mismatch")

    approval = payload.get("operator_approval")
    if not _exact_keys(
        approval, {"approved", "operator_id", "approved_at", "expires_at"}
    ):
        return _blocked("authorization_operator_approval_invalid")
    if approval.get("approved") is not True or approval.get("operator_id") != EXPECTED_OPERATOR_ID:
        return _blocked("authorization_operator_not_approved")
    approved_at = _timestamp(approval.get("approved_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    if approved_at is None or expires_at is None or expires_at <= approved_at:
        return _blocked("authorization_operator_approval_invalid")
    if approved_at > now_epoch:
        return _blocked("authorization_not_yet_valid")
    if expires_at <= now_epoch:
        return _blocked("authorization_expired")
    if expires_at - approved_at > MAX_APPROVAL_SECONDS:
        return _blocked("authorization_expiry_too_distant")
    return _authorized()


def evaluate_authorization(
    record_path: str | os.PathLike[str],
    *,
    manifest: ArtifactManifest,
    family: str,
    required_use: str,
    now_epoch: int | None = None,
) -> AuthorizationDecision:
    """Evaluate one exact use scope without writes or network."""

    try:
        if required_use not in KNOWN_USES:
            return _blocked("authorization_use_unsupported")
        # Validate the requested family against the immutable manifest before
        # reading approval.  This is read-only and catches unsupported scopes.
        select_family(manifest.artifacts, family)
        manifest_sha256 = manifest_scope_sha256(manifest)
        payload = _read_record(record_path)
    except _RecordFailure as error:
        return _blocked(error.reason)
    except (ArtifactError, OSError):
        return _blocked("authorization_evaluation_failed")
    current = int(datetime.now(timezone.utc).timestamp()) if now_epoch is None else now_epoch
    if not isinstance(current, int) or isinstance(current, bool) or current < 0:
        return _blocked("authorization_evaluation_failed")
    return _validate_record(
        payload,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        family=family,
        required_use=required_use,
        now_epoch=current,
    )


def evaluate_model_download_authorization(
    record_path: str | os.PathLike[str],
    *,
    manifest: ArtifactManifest,
    family: str,
    now_epoch: int | None = None,
) -> AuthorizationDecision:
    """Require the model-download grant for an exact manifest and family."""

    return evaluate_authorization(
        record_path,
        manifest=manifest,
        family=family,
        required_use="model_download",
        now_epoch=now_epoch,
    )


def evaluate_local_inference_authorization(
    record_path: str | os.PathLike[str],
    *,
    manifest: ArtifactManifest,
    family: str,
    now_epoch: int | None = None,
) -> AuthorizationDecision:
    """Require a distinct local-inference grant; does not activate a runtime."""

    return evaluate_authorization(
        record_path,
        manifest=manifest,
        family=family,
        required_use="local_inference",
        now_epoch=now_epoch,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("fl", "ref", "all"), default="fl")
    parser.add_argument(
        "--use",
        choices=tuple(sorted(KNOWN_USES)),
        default="model_download",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--record", default=str(default_authorization_record_path()))
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(Path(args.manifest).expanduser())
    except (ArtifactError, OSError):
        decision = _blocked("authorization_manifest_invalid")
    else:
        decision = evaluate_authorization(
            args.record,
            manifest=manifest,
            family=args.family,
            required_use=args.use,
        )
    print(f"status={decision.status} reason={decision.reason}")
    return 0 if decision.authorized else 3


if __name__ == "__main__":
    raise SystemExit(run())
