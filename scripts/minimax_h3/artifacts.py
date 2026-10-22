#!/usr/bin/env python3
"""Pinned artifact manifests and crash-safe, hash-verified downloads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse


BUFFER_BYTES = 8 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")


class ArtifactError(RuntimeError):
    """Base error for artifact provisioning."""


class ManifestError(ArtifactError):
    """The checked-in lock manifest is malformed or unsafe."""


class IntegrityError(ArtifactError):
    """An artifact does not match its pinned byte count and SHA256."""


class IncompleteDownloadError(ArtifactError):
    """A clean HTTP response ended early; its valid partial is resumable."""


@dataclass(frozen=True)
class ArtifactSpec:
    remote_path: str
    destination: str
    expected_bytes: int
    sha256: str
    families: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactManifest:
    path: Path
    artifact_set: str
    repository: str
    revision: str
    url_template: str
    artifacts: tuple[ArtifactSpec, ...]
    raw: Mapping[str, Any]

    def url_for(self, artifact: ArtifactSpec) -> str:
        try:
            url = self.url_template.format(
                repository=self.repository,
                revision=self.revision,
                path=quote(artifact.remote_path, safe="/"),
            )
        except (KeyError, ValueError) as exc:
            raise ManifestError(f"invalid download_url_template: {exc}") from exc
        if urlparse(url).scheme != "https":
            raise ManifestError("artifact URLs must use HTTPS")
        return url


def _safe_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    if "\\" in value:
        raise ManifestError(f"{field} must use POSIX separators: {value!r}")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value or ".." in parsed.parts:
        raise ManifestError(f"{field} is not a safe relative path: {value!r}")
    return value


def load_manifest(path: Path) -> ArtifactManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ManifestError("manifest schema_version must be 1")

    artifact_set = raw.get("artifact_set")
    source = raw.get("source")
    files = raw.get("files")
    if not isinstance(artifact_set, str) or not artifact_set:
        raise ManifestError("manifest artifact_set must be a non-empty string")
    if not isinstance(source, dict):
        raise ManifestError("manifest source must be an object")
    if not isinstance(files, list) or not files:
        raise ManifestError("manifest files must be a non-empty array")

    repository = source.get("repository")
    revision = source.get("revision")
    url_template = source.get("download_url_template")
    if not isinstance(repository, str) or not repository or repository.startswith("/"):
        raise ManifestError("source.repository must be a non-empty repository name")
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        raise ManifestError("source.revision must be a pinned 40-character commit SHA")
    if not isinstance(url_template, str) or "{path}" not in url_template:
        raise ManifestError("source.download_url_template must contain {path}")

    artifacts: list[ArtifactSpec] = []
    destinations: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ManifestError(f"files[{index}] must be an object")
        remote_path = _safe_relative_path(item.get("path"), f"files[{index}].path")
        destination = _safe_relative_path(
            item.get("destination"), f"files[{index}].destination"
        )
        if destination in destinations:
            raise ManifestError(f"duplicate destination: {destination}")
        destinations.add(destination)

        expected_bytes = item.get("bytes")
        sha256 = item.get("sha256")
        families_raw = item.get("families", [])
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes <= 0
        ):
            raise ManifestError(f"files[{index}].bytes must be a positive integer")
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise ManifestError(f"files[{index}].sha256 must be lowercase SHA256")
        if not isinstance(families_raw, list) or any(
            not isinstance(value, str) or not value for value in families_raw
        ):
            raise ManifestError(f"files[{index}].families must be an array of strings")
        if len(families_raw) != len(set(families_raw)):
            raise ManifestError(f"files[{index}].families contains duplicates")
        artifacts.append(
            ArtifactSpec(
                remote_path=remote_path,
                destination=destination,
                expected_bytes=expected_bytes,
                sha256=sha256,
                families=tuple(families_raw),
            )
        )

    manifest = ArtifactManifest(
        path=path,
        artifact_set=artifact_set,
        repository=repository,
        revision=revision,
        url_template=url_template,
        artifacts=tuple(artifacts),
        raw=raw,
    )
    # Expand one URL during validation so malformed format fields fail before writes.
    manifest.url_for(manifest.artifacts[0])
    return manifest


def select_family(artifacts: Sequence[ArtifactSpec], family: str) -> tuple[ArtifactSpec, ...]:
    if family not in {"fl", "ref", "all"}:
        raise ManifestError(f"unsupported MiniMax H3 family: {family}")
    if family == "all":
        selected = tuple(artifacts)
    else:
        selected = tuple(
            artifact
            for artifact in artifacts
            if family in artifact.families or "common" in artifact.families
        )
    if not selected:
        raise ManifestError(f"manifest has no artifacts for family {family}")
    return selected


def file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(BUFFER_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def verify_file(path: Path, artifact: ArtifactSpec) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size != artifact.expected_bytes:
            return False
        size, digest = file_digest(path)
    except OSError:
        return False
    return size == artifact.expected_bytes and digest == artifact.sha256


def partial_path_for(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.partial")


def artifact_destination(root: Path, relative: str) -> tuple[Path, Path]:
    """Return a destination under a resolved root, rejecting symlink parents."""

    root_resolved = root.expanduser().resolve(strict=False)
    relative_path = PurePosixPath(_safe_relative_path(relative, "destination"))
    destination = root_resolved.joinpath(*relative_path.parts)
    try:
        parent_parts = destination.parent.relative_to(root_resolved).parts
    except ValueError as exc:
        raise ArtifactError(f"artifact destination escapes root: {relative}") from exc

    current = root_resolved
    for part in parent_parts:
        current /= part
        if current.is_symlink():
            raise ArtifactError(f"refusing symlink artifact parent: {current}")
        if current.exists() and not current.is_dir():
            raise ArtifactError(f"artifact parent is not a directory: {current}")
    return root_resolved, destination


def _prepare_artifact_parent(root_resolved: Path, destination: Path) -> None:
    """Create private parents one component at a time without following links."""

    if root_resolved.is_symlink():
        # ``resolve`` normally makes this impossible; keep the invariant explicit.
        raise ArtifactError(f"resolved artifact root is a symlink: {root_resolved}")
    if root_resolved.exists() and not root_resolved.is_dir():
        raise ArtifactError(f"artifact root is not a directory: {root_resolved}")
    root_resolved.mkdir(mode=0o700, parents=True, exist_ok=True)

    current = root_resolved
    for part in destination.parent.relative_to(root_resolved).parts:
        current /= part
        if current.is_symlink():
            raise ArtifactError(f"refusing symlink artifact parent: {current}")
        if current.exists():
            if not current.is_dir():
                raise ArtifactError(f"artifact parent is not a directory: {current}")
            continue
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            # Another process may have created it. Re-check without following a
            # newly introduced symlink.
            if current.is_symlink() or not current.is_dir():
                raise ArtifactError(f"unsafe artifact parent appeared: {current}")


def _open_partial(partial: Path, mode: str):
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if mode == "ab":
        flags |= os.O_APPEND
    elif mode == "wb":
        flags |= os.O_TRUNC
    else:
        raise ValueError(f"unsupported partial mode: {mode}")
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(partial, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, mode)


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    return int(status or 200)


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get(name)
    return str(value) if value is not None else None


def _download_once(
    *,
    url: str,
    partial: Path,
    artifact: ArtifactSpec,
    resume_at: int,
    timeout: float,
    opener: Callable[..., Any],
    progress: Callable[[int, int], None] | None,
) -> None:
    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": "web-comfyui-minimax-h3/1",
    }
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"
    request = urllib.request.Request(url, headers=headers)

    with opener(request, timeout=timeout) as response:
        status = _response_status(response)
        if resume_at and status == 206:
            content_range = _response_header(response, "Content-Range")
            match = CONTENT_RANGE_RE.fullmatch(content_range or "")
            if not match or int(match.group(1)) != resume_at:
                raise IntegrityError(
                    f"server returned an invalid Content-Range for {artifact.remote_path}"
                )
            if match.group(3) != "*" and int(match.group(3)) != artifact.expected_bytes:
                raise IntegrityError(
                    f"server size changed for {artifact.remote_path}: {match.group(3)}"
                )
            mode = "ab"
            received = resume_at
        elif status == 200:
            # Some CDNs ignore Range. Restarting the .partial file is safe.
            mode = "wb"
            received = 0
        elif not resume_at and status == 206:
            content_range = _response_header(response, "Content-Range")
            match = CONTENT_RANGE_RE.fullmatch(content_range or "")
            if not match or int(match.group(1)) != 0:
                raise IntegrityError(
                    f"server returned an invalid initial range for {artifact.remote_path}"
                )
            mode = "wb"
            received = 0
        else:
            raise ArtifactError(f"unexpected HTTP status {status} for {artifact.remote_path}")

        with _open_partial(partial, mode) as handle:
            while True:
                chunk = response.read(BUFFER_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > artifact.expected_bytes:
                    raise IntegrityError(
                        f"download exceeded pinned size for {artifact.remote_path}"
                    )
                handle.write(chunk)
                if progress is not None:
                    progress(received, artifact.expected_bytes)
            handle.flush()
            os.fsync(handle.fileno())


def _remove_corrupt_partial(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def ensure_artifact(
    artifact: ArtifactSpec,
    *,
    root: Path,
    url: str,
    verify_only: bool = False,
    timeout: float = 60.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Verify or fetch one artifact, returning verified/downloaded/promoted.

    Existing final files are never overwritten. Downloads remain in a sibling
    ``.partial`` file until both the pinned size and SHA256 match.
    """

    root_resolved, destination = artifact_destination(root, artifact.destination)
    partial = partial_path_for(destination)
    if destination.is_symlink() or partial.is_symlink():
        raise ArtifactError(f"refusing symlink artifact path: {destination}")

    if destination.exists():
        if verify_file(destination, artifact):
            return "verified"
        raise IntegrityError(
            f"existing artifact does not match lock manifest: {destination}"
        )
    if verify_only:
        raise IntegrityError(f"artifact is missing: {destination}")

    _prepare_artifact_parent(root_resolved, destination)
    if partial.exists():
        partial_size = partial.stat().st_size
        if partial_size == artifact.expected_bytes:
            if verify_file(partial, artifact):
                os.replace(partial, destination)
                return "promoted"
            _remove_corrupt_partial(partial)
        elif partial_size > artifact.expected_bytes:
            _remove_corrupt_partial(partial)

    resume_at = partial.stat().st_size if partial.exists() else 0
    _download_once(
        url=url,
        partial=partial,
        artifact=artifact,
        resume_at=resume_at,
        timeout=timeout,
        opener=opener,
        progress=progress,
    )
    partial_size = partial.stat().st_size
    if partial_size < artifact.expected_bytes:
        raise IncompleteDownloadError(
            f"download ended before pinned size for {artifact.remote_path}: "
            f"received {partial_size} of {artifact.expected_bytes} bytes; "
            "partial preserved for resume"
        )
    if partial_size > artifact.expected_bytes:
        _remove_corrupt_partial(partial)
        raise IntegrityError(
            f"download exceeded pinned size for {artifact.remote_path}"
        )
    if not verify_file(partial, artifact):
        _remove_corrupt_partial(partial)
        if resume_at:
            # A stale prefix can be the only bad part. Retry once from byte zero.
            _download_once(
                url=url,
                partial=partial,
                artifact=artifact,
                resume_at=0,
                timeout=timeout,
                opener=opener,
                progress=progress,
            )
            retry_size = partial.stat().st_size
            if retry_size < artifact.expected_bytes:
                raise IncompleteDownloadError(
                    f"download ended before pinned size for {artifact.remote_path}: "
                    f"received {retry_size} of {artifact.expected_bytes} bytes; "
                    "partial preserved for resume"
                )
            if retry_size > artifact.expected_bytes:
                _remove_corrupt_partial(partial)
                raise IntegrityError(
                    f"download exceeded pinned size for {artifact.remote_path}"
                )
        if not verify_file(partial, artifact):
            _remove_corrupt_partial(partial)
            raise IntegrityError(
                f"download does not match pinned size/SHA256: {artifact.remote_path}"
            )

    os.replace(partial, destination)
    return "downloaded"


def expected_bytes(artifacts: Iterable[ArtifactSpec]) -> int:
    return sum(artifact.expected_bytes for artifact in artifacts)
