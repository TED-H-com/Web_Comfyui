#!/usr/bin/env python3
"""Crash-safe parallel range downloader for pinned Hugging Face artifacts.

The command is a dry-run unless ``--execute`` is supplied.  Execute mode first
performs two read-only byte-range probes and requires a stable byte count and
entity validator.  Only then does it create an owner-only staging directory.

Every segment is fetched from the pinned ``huggingface.co`` URL through a
manually validated Hugging Face CDN redirect chain.  Starting at the origin for
each request safely refreshes expiring signed CDN URLs.  A final file is
published only after sequential assembly, exact byte-count and SHA256 checks,
``fsync``, and an atomic ``os.replace``.  A differing existing final is never
overwritten, and staging is intentionally retained after failures.

An explicit ``--seed-prefix`` may copy a paused contiguous curl prefix into the
same locked staging.  The source is opened read-only, never renamed or removed,
and must remain the same owner-controlled regular file throughout the copy.
Existing staged overlap must match byte-for-byte; the final pinned SHA256 is
still authoritative, so an incorrect seed can never be published.

This module uses Python HTTP APIs directly; it never constructs or executes a
shell command.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import http.client
import json
import os
import re
import secrets
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webui"))
import file_lock as fcntl

REPORT_SCHEMA = "castle.minimax-h3.pinned-range-download.v1"
STAGE_SCHEMA = 1
SEGMENT_SCHEMA = 1
SEED_SCHEMA = 1
DEFAULT_LANES = 4
DEFAULT_SEGMENT_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 90.0
BUFFER_BYTES = 1024 * 1024
MAX_LANES = 16
MAX_REDIRECTS = 6
MAX_SIGNED_REFRESHES = 3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
CDN_LFS_RE = re.compile(r"^cdn-lfs(?:-[a-z0-9-]+)?\.(?:hf|huggingface)\.co$")
PINNED_RESOLVE_RE = re.compile(
    r"^/[^/]+/[^/]+/resolve/[0-9a-f]{40}/[^/].*$"
)
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
AUTH_FAILURE_CODES = frozenset({401, 403})
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class DownloadError(RuntimeError):
    """A sanitized, machine-readable download failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    url: str
    destination: Path
    expected_bytes: int
    sha256: str
    lanes: int = DEFAULT_LANES
    segment_bytes: int = DEFAULT_SEGMENT_BYTES
    timeout: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class Validator:
    kind: str
    value: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.value.encode("utf-8", "strict")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProbeResult:
    total: int
    validator: Validator
    first_byte: bytes
    final_endpoint: str


@dataclass(frozen=True, slots=True)
class Segment:
    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


@dataclass(slots=True)
class OpenedRange:
    response: Any
    final_url: str
    header_sets: list[Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class SeedPrefix:
    path: Path
    descriptor: int
    info: os.stat_result

    @property
    def size(self) -> int:
        return self.info.st_size


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Mapping[str, str],
        new_url: str,
    ) -> None:
        return None


def _fail(code: str, message: str) -> None:
    raise DownloadError(code, message)


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise DownloadError("json_invalid", "Download state is not serializable.") from error


def _url_parts(raw: str) -> urllib.parse.SplitResult:
    if not isinstance(raw, str) or not raw or CONTROL_RE.search(raw):
        _fail("url_invalid", "The source URL must be non-empty and contain no controls.")
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port
    except ValueError as error:
        raise DownloadError("url_invalid", "The source URL is malformed.") from error
    if parts.username is not None or parts.password is not None or parts.fragment:
        _fail("url_invalid", "URL credentials and fragments are not allowed.")
    if not parts.hostname or not parts.path.startswith("/"):
        _fail("url_invalid", "The source URL must include a host and absolute path.")
    return parts


def _is_hf_cdn_host(host: str) -> bool:
    return (
        host.endswith(".cdn.hf.co")
        or host.endswith(".xethub.hf.co")
        or CDN_LFS_RE.fullmatch(host) is not None
    )


def _validate_network_url(
    raw: str,
    *,
    initial: bool,
    allow_loopback_for_tests: bool,
) -> urllib.parse.SplitResult:
    parts = _url_parts(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if allow_loopback_for_tests and host in {"127.0.0.1", "localhost", "::1"}:
        if parts.scheme not in {"http", "https"}:
            _fail("url_invalid", "Loopback test URLs must use HTTP or HTTPS.")
        return parts
    if parts.port not in (None, 443):
        _fail("url_invalid", "Hugging Face HTTPS URLs must use port 443.")
    if parts.scheme != "https":
        _fail("url_invalid", "Hugging Face downloads must use HTTPS.")
    if initial:
        if host != "huggingface.co":
            _fail("url_host_denied", "The source URL must be on huggingface.co.")
        if PINNED_RESOLVE_RE.fullmatch(parts.path) is None:
            _fail(
                "url_not_pinned",
                "The source URL must use /resolve/<40-character commit>/file.",
            )
    elif host != "huggingface.co" and not _is_hf_cdn_host(host):
        _fail("redirect_host_denied", "A redirect left the Hugging Face CDN allowlist.")
    return parts


def _safe_endpoint(raw: str) -> str:
    """Return a query-free endpoint suitable for JSON reports and errors."""

    parts = urllib.parse.urlsplit(raw)
    host = (parts.hostname or "invalid").lower()
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    return urllib.parse.urlunsplit((parts.scheme, f"{host}{port}", parts.path, "", ""))


def _validate_destination(destination: Path) -> Path:
    value = str(destination)
    if not value or CONTROL_RE.search(value) or not destination.is_absolute():
        _fail("destination_invalid", "Destination must be an absolute control-free path.")
    if destination.name in {"", ".", ".."}:
        _fail("destination_invalid", "Destination must name a file.")

    current = Path(destination.anchor)
    for component in destination.parts[1:-1]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            _fail("destination_parent_missing", "Destination parent must already exist.")
        except OSError as error:
            raise DownloadError(
                "destination_parent_unreadable", "Destination parent cannot be inspected."
            ) from error
        if stat.S_ISLNK(info.st_mode):
            _fail("destination_parent_symlink", "Destination parents must not be symlinks.")
        if not stat.S_ISDIR(info.st_mode):
            _fail("destination_parent_invalid", "Destination parent is not a directory.")

    try:
        parent_info = os.lstat(destination.parent)
    except OSError as error:
        raise DownloadError(
            "destination_parent_unreadable", "Destination parent cannot be inspected."
        ) from error
    if parent_info.st_uid != os.geteuid():
        _fail("destination_parent_owner", "Destination parent must be owned by this user.")
    return destination


def _validate_seed_prefix_path(
    seed_prefix: Path | None, *, destination: Path
) -> Path | None:
    """Validate seed syntax without opening or reading it.

    Dry-run deliberately stops at this static validation so even a very large
    prefix is not read.  Execute mode performs the owner, type, size, and
    stability checks after the remote entity probe succeeds.
    """

    if seed_prefix is None:
        return None
    value = str(seed_prefix)
    if (
        not value
        or CONTROL_RE.search(value)
        or not seed_prefix.is_absolute()
        or seed_prefix.name in {"", ".", ".."}
        or ".." in seed_prefix.parts
    ):
        _fail("seed_path_invalid", "Seed prefix must be an absolute control-free file path.")
    if seed_prefix == destination:
        _fail("seed_path_conflict", "Seed prefix and final destination must differ.")
    return seed_prefix


def validate_spec(
    spec: DownloadSpec, *, allow_loopback_for_tests: bool = False
) -> DownloadSpec:
    _validate_network_url(
        spec.url,
        initial=True,
        allow_loopback_for_tests=allow_loopback_for_tests,
    )
    destination = _validate_destination(Path(spec.destination))
    if type(spec.expected_bytes) is not int or spec.expected_bytes <= 0:
        _fail("bytes_invalid", "Expected bytes must be a positive integer.")
    if not isinstance(spec.sha256, str) or SHA256_RE.fullmatch(spec.sha256) is None:
        _fail("sha256_invalid", "SHA256 must be exactly 64 lowercase hexadecimal characters.")
    if type(spec.lanes) is not int or not 1 <= spec.lanes <= MAX_LANES:
        _fail("lanes_invalid", f"Lanes must be between 1 and {MAX_LANES}.")
    if type(spec.segment_bytes) is not int or spec.segment_bytes <= 0:
        _fail("segment_bytes_invalid", "Segment bytes must be a positive integer.")
    if spec.segment_bytes > spec.expected_bytes:
        segment_bytes = spec.expected_bytes
    else:
        segment_bytes = spec.segment_bytes
    if isinstance(spec.timeout, bool) or not isinstance(spec.timeout, (int, float)):
        _fail("timeout_invalid", "Timeout must be numeric.")
    if not 1.0 <= float(spec.timeout) <= 3600.0:
        _fail("timeout_invalid", "Timeout must be between 1 and 3600 seconds.")
    return DownloadSpec(
        url=spec.url,
        destination=destination,
        expected_bytes=spec.expected_bytes,
        sha256=spec.sha256,
        lanes=spec.lanes,
        segment_bytes=segment_bytes,
        timeout=float(spec.timeout),
    )


def file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(BUFFER_BYTES)
                if not block:
                    break
                count += len(block)
                digest.update(block)
    except OSError as error:
        raise DownloadError("file_read_failed", "A download file could not be read.") from error
    return count, digest.hexdigest()


def _inspect_existing_final(spec: DownloadSpec) -> str:
    try:
        info = os.lstat(spec.destination)
    except FileNotFoundError:
        return "missing"
    except OSError as error:
        raise DownloadError("final_inspect_failed", "Existing final cannot be inspected.") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail("existing_final_unsafe", "Existing final must be a regular non-symlink file.")
    if info.st_uid != os.geteuid():
        _fail("existing_final_owner", "Existing final must be owned by this user.")
    if info.st_size != spec.expected_bytes:
        _fail("existing_final_differs", "Existing final has a different byte count.")
    size, digest = file_digest(spec.destination)
    if size != spec.expected_bytes or digest != spec.sha256:
        _fail("existing_final_differs", "Existing final does not match the pinned SHA256.")
    return "verified"


def stage_path_for(spec: DownloadSpec) -> Path:
    fingerprint = hashlib.sha256(
        _canonical_json(
            {
                "url": spec.url,
                "destination": str(spec.destination),
                "bytes": spec.expected_bytes,
                "sha256": spec.sha256,
            }
        )
    ).hexdigest()[:16]
    return spec.destination.parent / f".{spec.destination.name}.ranges-{fingerprint}"


def segment_plan(spec: DownloadSpec) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    start = 0
    index = 0
    while start < spec.expected_bytes:
        end = min(start + spec.segment_bytes, spec.expected_bytes) - 1
        segments.append(Segment(index=index, start=start, end=end))
        index += 1
        start = end + 1
    return tuple(segments)


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    return int(status or 0)


def _header_map(response: Any) -> Mapping[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    return headers


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    return str(value).strip() if value is not None else None


def _valid_validator_value(value: str) -> bool:
    return bool(value) and len(value) <= 512 and CONTROL_RE.search(value) is None


def _select_validator(header_sets: Iterable[Mapping[str, str]]) -> Validator:
    all_headers = list(header_sets)
    for headers in reversed(all_headers):
        value = _header(headers, "ETag")
        if value and not value.lower().startswith("w/") and _valid_validator_value(value):
            return Validator("etag", value)
    for name, kind in (("X-Xet-Hash", "x-xet-hash"), ("X-Linked-Etag", "x-linked-etag")):
        for headers in all_headers:
            value = _header(headers, name)
            if value and _valid_validator_value(value):
                return Validator(kind, value)
    _fail("validator_missing", "Hugging Face did not return a stable entity validator.")


class RangeDownloader:
    """Download one immutable artifact using validated, resumable ranges."""

    def __init__(
        self,
        spec: DownloadSpec,
        *,
        seed_prefix: Path | None = None,
        allow_loopback_for_tests: bool = False,
    ) -> None:
        self._allow_loopback_for_tests = allow_loopback_for_tests
        self.spec = validate_spec(
            spec, allow_loopback_for_tests=allow_loopback_for_tests
        )
        self.seed_prefix = _validate_seed_prefix_path(
            seed_prefix, destination=self.spec.destination
        )
        if self.seed_prefix is not None:
            stage = stage_path_for(self.spec)
            if self.seed_prefix == stage or stage in self.seed_prefix.parents:
                _fail(
                    "seed_path_conflict",
                    "Seed prefix must be outside this download's staging directory.",
                )

    def _new_opener(self) -> urllib.request.OpenerDirector:
        return urllib.request.build_opener(_NoRedirect())

    def _request_once(
        self,
        url: str,
        *,
        start: int,
        end: int,
        validator: Validator | None,
        is_origin: bool,
    ) -> Any:
        headers = {
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "Range": f"bytes={start}-{end}",
            "User-Agent": "web-comfyui-minimax-h3-ranges/1",
        }
        if validator is not None and validator.kind == "etag" and not is_origin:
            headers["If-Match"] = validator.value
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            return self._new_opener().open(request, timeout=self.spec.timeout)
        except urllib.error.HTTPError as error:
            return error
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ) as error:
            raise DownloadError(
                "network_error", "A Hugging Face range request failed before a response."
            ) from error

    def _open_range(
        self, start: int, end: int, *, validator: Validator | None
    ) -> OpenedRange:
        for refresh in range(MAX_SIGNED_REFRESHES):
            current = self.spec.url
            seen: set[str] = set()
            header_sets: list[Mapping[str, str]] = []
            signed_expired = False
            for redirect_count in range(MAX_REDIRECTS + 1):
                if current in seen:
                    _fail("redirect_loop", "Hugging Face returned a redirect loop.")
                seen.add(current)
                is_origin = current == self.spec.url
                response = self._request_once(
                    current,
                    start=start,
                    end=end,
                    validator=validator,
                    is_origin=is_origin,
                )
                status = _response_status(response)
                headers = _header_map(response)
                if status in REDIRECT_CODES:
                    location = _header(headers, "Location")
                    response.close()
                    if not location:
                        _fail("redirect_invalid", "Hugging Face returned a redirect without Location.")
                    next_url = urllib.parse.urljoin(current, location)
                    _validate_network_url(
                        next_url,
                        initial=False,
                        allow_loopback_for_tests=self._allow_loopback_for_tests,
                    )
                    header_sets.append(headers)
                    current = next_url
                    continue
                if status in AUTH_FAILURE_CODES and not is_origin:
                    response.close()
                    signed_expired = True
                    break
                if status >= 400 or status == 0:
                    response.close()
                    _fail("http_status", f"Hugging Face returned HTTP {status}.")
                _validate_network_url(
                    current,
                    initial=is_origin,
                    allow_loopback_for_tests=self._allow_loopback_for_tests,
                )
                header_sets.append(headers)
                return OpenedRange(
                    response=response,
                    final_url=current,
                    header_sets=header_sets,
                )
            else:
                _fail("redirect_limit", "Hugging Face exceeded the redirect limit.")
            if not signed_expired:
                break
            if refresh + 1 == MAX_SIGNED_REFRESHES:
                _fail("signed_url_expired", "Signed Hugging Face CDN URLs could not be refreshed.")
        _fail("range_open_failed", "The range response could not be opened.")

    def _validate_opened(
        self,
        opened: OpenedRange,
        *,
        start: int,
        end: int,
        validator: Validator | None,
    ) -> Validator:
        response = opened.response
        status = _response_status(response)
        headers = _header_map(response)
        if status != 206:
            _fail("range_status", "Every segment response must be HTTP 206.")
        content_range = _header(headers, "Content-Range")
        match = CONTENT_RANGE_RE.fullmatch(content_range or "")
        if (
            match is None
            or int(match.group(1)) != start
            or int(match.group(2)) != end
            or int(match.group(3)) != self.spec.expected_bytes
        ):
            _fail("content_range_invalid", "A segment returned an unexpected Content-Range.")
        expected = end - start + 1
        content_length = _header(headers, "Content-Length")
        if content_length is not None:
            try:
                parsed_length = int(content_length)
            except ValueError:
                parsed_length = -1
            if parsed_length != expected:
                _fail("content_length_invalid", "A segment returned an unexpected byte count.")
        encoding = (_header(headers, "Content-Encoding") or "identity").lower()
        if encoding != "identity":
            _fail("content_encoding_invalid", "Compressed range responses are not accepted.")
        current_validator = _select_validator(opened.header_sets)
        if validator is not None and current_validator != validator:
            _fail("validator_changed", "The artifact validator changed during download.")
        return current_validator

    def _probe_once(self) -> ProbeResult:
        opened = self._open_range(0, 0, validator=None)
        try:
            validator = self._validate_opened(
                opened, start=0, end=0, validator=None
            )
            body = opened.response.read(2)
            if len(body) != 1 or opened.response.read(1):
                _fail("probe_byte_count", "The range probe did not return exactly one byte.")
            return ProbeResult(
                total=self.spec.expected_bytes,
                validator=validator,
                first_byte=body,
                final_endpoint=_safe_endpoint(opened.final_url),
            )
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise DownloadError("probe_read_failed", "The range probe body could not be read.") from error
        finally:
            opened.response.close()

    def stable_probe(self) -> ProbeResult:
        first = self._probe_once()
        second = self._probe_once()
        if (
            first.total != self.spec.expected_bytes
            or second.total != self.spec.expected_bytes
        ):
            _fail("probe_total_changed", "The remote total does not match pinned bytes.")
        if first.validator != second.validator:
            _fail("probe_validator_changed", "The remote validator was not stable across probes.")
        if first.first_byte != second.first_byte:
            _fail("probe_content_changed", "The remote probe content changed between requests.")
        return second

    def _open_seed_prefix(self) -> SeedPrefix | None:
        path = self.seed_prefix
        if path is None:
            return None

        current = Path(path.anchor)
        for component in path.parts[1:-1]:
            current /= component
            try:
                parent_info = os.lstat(current)
            except OSError as error:
                raise DownloadError(
                    "seed_parent_unreadable", "A seed-prefix parent cannot be inspected."
                ) from error
            if stat.S_ISLNK(parent_info.st_mode):
                _fail("seed_parent_symlink", "Seed-prefix parents must not be symlinks.")
            if not stat.S_ISDIR(parent_info.st_mode):
                _fail("seed_parent_invalid", "A seed-prefix parent is not a directory.")

        try:
            path_info = os.lstat(path)
        except OSError as error:
            raise DownloadError("seed_unreadable", "Seed prefix cannot be inspected.") from error
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or path_info.st_nlink != 1
        ):
            _fail("seed_unsafe", "Seed prefix must be one regular non-symlink file.")
        if path_info.st_uid != os.geteuid():
            _fail("seed_owner", "Seed prefix must be owned by this user.")
        if not 0 < path_info.st_size <= self.spec.expected_bytes:
            _fail("seed_size_invalid", "Seed-prefix bytes must be between one and the pinned size.")

        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise DownloadError("seed_open_failed", "Seed prefix could not be opened read-only.") from error
        try:
            opened_info = os.fstat(descriptor)
            if (
                opened_info.st_dev != path_info.st_dev
                or opened_info.st_ino != path_info.st_ino
                or opened_info.st_uid != os.geteuid()
                or opened_info.st_nlink != 1
                or not stat.S_ISREG(opened_info.st_mode)
                or opened_info.st_size != path_info.st_size
            ):
                _fail("seed_changed", "Seed prefix changed while it was being opened.")
            return SeedPrefix(path=path, descriptor=descriptor, info=opened_info)
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _seed_identity(source: SeedPrefix) -> dict[str, object]:
        return {
            "schema_version": SEED_SCHEMA,
            "path": str(source.path),
            "device": source.info.st_dev,
            "inode": source.info.st_ino,
            "bytes": source.info.st_size,
            "uid": source.info.st_uid,
            "mtime_ns": source.info.st_mtime_ns,
            "ctime_ns": source.info.st_ctime_ns,
        }

    @classmethod
    def _assert_seed_unchanged(cls, source: SeedPrefix) -> None:
        try:
            current = os.fstat(source.descriptor)
            path_info = os.lstat(source.path)
        except OSError as error:
            raise DownloadError("seed_read_failed", "Seed prefix can no longer be inspected.") from error
        expected = cls._seed_identity(source)
        actual = {
            "schema_version": SEED_SCHEMA,
            "path": str(source.path),
            "device": current.st_dev,
            "inode": current.st_ino,
            "bytes": current.st_size,
            "uid": current.st_uid,
            "mtime_ns": current.st_mtime_ns,
            "ctime_ns": current.st_ctime_ns,
        }
        if (
            actual != expected
            or current.st_nlink != 1
            or not stat.S_ISREG(current.st_mode)
            or stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or path_info.st_dev != current.st_dev
            or path_info.st_ino != current.st_ino
        ):
            _fail("seed_changed", "Seed prefix changed during its read-only import.")

    @staticmethod
    def _read_seed(source: SeedPrefix, offset: int, count: int) -> bytes:
        try:
            block = os.pread(source.descriptor, count, offset)
        except OSError as error:
            raise DownloadError("seed_read_failed", "Seed-prefix bytes could not be read.") from error
        if not block:
            _fail("seed_changed", "Seed prefix became shorter during import.")
        return block

    def _seed_metadata_status(self, stage: Path, source: SeedPrefix) -> tuple[Path, bool]:
        metadata_path = stage / "seed-prefix.json"
        expected = self._seed_identity(source)
        if metadata_path.exists() or metadata_path.is_symlink():
            self._assert_owner_file(metadata_path)
            try:
                actual = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise DownloadError(
                    "seed_metadata_invalid", "Seed-prefix staging metadata is unreadable."
                ) from error
            if actual != expected:
                _fail(
                    "seed_metadata_mismatch",
                    "Existing staging was seeded from a different source snapshot.",
                )
            return metadata_path, True
        return metadata_path, False

    def _compare_seed_overlap(
        self,
        source: SeedPrefix,
        part: Path,
        *,
        source_offset: int,
        count: int,
    ) -> None:
        if count <= 0:
            return
        self._assert_owner_file(part)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(part, flags)
        except OSError as error:
            raise DownloadError("stage_file_unreadable", "A staged segment cannot be opened.") from error
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size < count
            ):
                _fail("stage_file_unsafe", "A staged segment changed during seed validation.")
            compared = 0
            while compared < count:
                amount = min(BUFFER_BYTES, count - compared)
                seed_block = self._read_seed(source, source_offset + compared, amount)
                try:
                    staged_block = os.pread(descriptor, len(seed_block), compared)
                except OSError as error:
                    raise DownloadError(
                        "stage_file_unreadable", "Staged seed overlap could not be read."
                    ) from error
                if staged_block != seed_block:
                    _fail(
                        "seed_stage_conflict",
                        "Seed prefix disagrees with bytes already present in staging.",
                    )
                compared += len(seed_block)
        finally:
            os.close(descriptor)

    def _import_seed_prefix(
        self, stage: Path, source: SeedPrefix, validator: Validator
    ) -> dict[str, object]:
        metadata_path, metadata_exists = self._seed_metadata_status(stage, source)
        plan: list[tuple[Segment, Path, int, int]] = []
        matched = 0

        for segment in segment_plan(self.spec):
            if segment.start >= source.size:
                break
            coverage = min(segment.size, source.size - segment.start)
            part, marker = self._segment_paths(stage, segment)
            if marker.exists() or marker.is_symlink():
                self._verified_segment(stage, segment, validator)
            if part.exists() or part.is_symlink():
                info = self._assert_owner_file(part)
                if info.st_size > segment.size:
                    _fail("segment_too_large", "A staged segment exceeds its expected byte count.")
                overlap = min(info.st_size, coverage)
                self._compare_seed_overlap(
                    source,
                    part,
                    source_offset=segment.start,
                    count=overlap,
                )
                current_size = info.st_size
                matched += overlap
            else:
                current_size = 0
            plan.append((segment, part, coverage, current_size))

        self._assert_seed_unchanged(source)
        if not metadata_exists:
            self._write_json_atomic(metadata_path, self._seed_identity(source))

        appended = 0
        for segment, part, coverage, current_size in plan:
            if current_size >= coverage:
                continue
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(part, flags, 0o600)
            except OSError as error:
                raise DownloadError("seed_import_failed", "A seeded segment cannot be opened.") from error
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_nlink != 1
                    or info.st_size != current_size
                ):
                    _fail("seed_stage_conflict", "A staged segment changed before seed import.")
                os.fchmod(descriptor, 0o600)
                offset = segment.start + current_size
                remaining = coverage - current_size
                while remaining:
                    block = self._read_seed(source, offset, min(BUFFER_BYTES, remaining))
                    view = memoryview(block)
                    while view:
                        try:
                            written = os.write(descriptor, view)
                        except OSError as error:
                            raise DownloadError(
                                "seed_import_failed", "Seed-prefix bytes could not be staged."
                            ) from error
                        if written <= 0:
                            _fail("seed_import_short", "A seeded segment write ended early.")
                        view = view[written:]
                        offset += written
                        remaining -= written
                        appended += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

        self._assert_seed_unchanged(source)
        return {
            "status": "imported" if appended else "reused",
            "path": str(source.path),
            "bytes": source.size,
            "appended_bytes": appended,
            "matched_staging_bytes": matched,
            "source_modified": False,
        }

    def _stage_metadata(self, probe: ProbeResult) -> dict[str, object]:
        return {
            "schema_version": STAGE_SCHEMA,
            "source_url_sha256": hashlib.sha256(
                self.spec.url.encode("utf-8", "strict")
            ).hexdigest(),
            "source_endpoint": _safe_endpoint(self.spec.url),
            "destination": str(self.spec.destination),
            "bytes": self.spec.expected_bytes,
            "sha256": self.spec.sha256,
            "segment_bytes": self.spec.segment_bytes,
            "segment_count": len(segment_plan(self.spec)),
            "validator": {
                "kind": probe.validator.kind,
                "sha256": probe.validator.digest,
            },
        }

    @staticmethod
    def _assert_owner_file(path: Path, *, mode: int = 0o600) -> os.stat_result:
        try:
            info = os.lstat(path)
        except OSError as error:
            raise DownloadError("stage_file_unreadable", "A staging file cannot be inspected.") from error
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
        ):
            _fail("stage_file_unsafe", "Staging files must be regular non-symlink files.")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
            _fail("stage_permissions", "Staging files must be owner-only.")
        return info

    @staticmethod
    def _write_json_atomic(path: Path, payload: object) -> None:
        temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            data = _canonical_json(payload)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            os.close(descriptor)

    def _prepare_stage(self, probe: ProbeResult) -> tuple[Path, Any]:
        stage = stage_path_for(self.spec)
        try:
            stage.mkdir(mode=0o700)
            os.chmod(stage, 0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise DownloadError("stage_create_failed", "Owner-only staging could not be created.") from error
        try:
            info = os.lstat(stage)
        except OSError as error:
            raise DownloadError("stage_inspect_failed", "Staging cannot be inspected.") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            _fail("stage_unsafe", "Staging must be a regular directory, not a symlink.")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            _fail("stage_permissions", "Staging must be owned by this user with mode 0700.")

        lock_path = stage / "download.lock"
        lock_flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(lock_path, lock_flags, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            raise DownloadError("stage_lock_failed", "Staging could not be locked.") from error
        lock_handle = os.fdopen(descriptor, "r+b")

        metadata_path = stage / "request.json"
        expected = self._stage_metadata(probe)
        if metadata_path.exists() or metadata_path.is_symlink():
            self._assert_owner_file(metadata_path)
            try:
                actual = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                lock_handle.close()
                raise DownloadError("stage_metadata_invalid", "Staging metadata is unreadable.") from error
            if actual != expected:
                lock_handle.close()
                _fail("stage_metadata_mismatch", "Existing staging belongs to a different request.")
        else:
            self._write_json_atomic(metadata_path, expected)
        return stage, lock_handle

    @staticmethod
    def _segment_paths(stage: Path, segment: Segment) -> tuple[Path, Path]:
        stem = f"segment-{segment.index:08d}-{segment.start}-{segment.end}"
        return stage / f"{stem}.part", stage / f"{stem}.json"

    def _marker_payload(
        self, segment: Segment, digest: str, validator: Validator
    ) -> dict[str, object]:
        return {
            "schema_version": SEGMENT_SCHEMA,
            "index": segment.index,
            "start": segment.start,
            "end": segment.end,
            "bytes": segment.size,
            "sha256": digest,
            "validator": {"kind": validator.kind, "sha256": validator.digest},
        }

    def _verified_segment(
        self, stage: Path, segment: Segment, validator: Validator
    ) -> tuple[bool, str | None]:
        part, marker = self._segment_paths(stage, segment)
        if marker.exists() or marker.is_symlink():
            self._assert_owner_file(marker)
            self._assert_owner_file(part)
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise DownloadError("segment_marker_invalid", "A segment marker is unreadable.") from error
            size, digest = file_digest(part)
            if size != segment.size or payload != self._marker_payload(segment, digest, validator):
                _fail("segment_marker_mismatch", "A completed segment failed its local marker check.")
            return True, digest
        return False, None

    def _download_segment(
        self, stage: Path, segment: Segment, validator: Validator
    ) -> dict[str, object]:
        verified, digest = self._verified_segment(stage, segment, validator)
        if verified:
            return {
                "index": segment.index,
                "status": "reused",
                "bytes": segment.size,
                "sha256": digest,
            }

        part, marker = self._segment_paths(stage, segment)
        if part.exists() or part.is_symlink():
            info = self._assert_owner_file(part)
            current_size = info.st_size
        else:
            current_size = 0
        if current_size > segment.size:
            _fail("segment_too_large", "A staged segment exceeds its expected byte count.")
        if current_size == segment.size:
            size, digest = file_digest(part)
            if size != segment.size:
                _fail("segment_size_changed", "A staged segment changed while being inspected.")
            self._write_json_atomic(marker, self._marker_payload(segment, digest, validator))
            return {
                "index": segment.index,
                "status": "recovered",
                "bytes": segment.size,
                "sha256": digest,
            }

        request_start = segment.start + current_size
        opened = self._open_range(request_start, segment.end, validator=validator)
        try:
            self._validate_opened(
                opened,
                start=request_start,
                end=segment.end,
                validator=validator,
            )
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(part, flags, 0o600)
            received = 0
            expected_response = segment.end - request_start + 1
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "ab", closefd=False) as handle:
                    while True:
                        block = opened.response.read(BUFFER_BYTES)
                        if not block:
                            break
                        if received + len(block) > expected_response:
                            _fail("segment_overflow", "A segment response exceeded its requested range.")
                        handle.write(block)
                        received += len(block)
                    handle.flush()
                    os.fsync(handle.fileno())
            except (OSError, TimeoutError, http.client.HTTPException) as error:
                raise DownloadError(
                    "segment_read_failed", "A segment body failed; its valid prefix was preserved."
                ) from error
            finally:
                os.close(descriptor)
            if received != expected_response:
                _fail("segment_short", "A segment ended early; its valid prefix was preserved.")
        finally:
            opened.response.close()

        size, digest = file_digest(part)
        if size != segment.size:
            _fail("segment_size_invalid", "A completed segment has an unexpected byte count.")
        self._write_json_atomic(marker, self._marker_payload(segment, digest, validator))
        return {
            "index": segment.index,
            "status": "resumed" if current_size else "downloaded",
            "bytes": segment.size,
            "sha256": digest,
        }

    def _download_all_segments(
        self, stage: Path, validator: Validator
    ) -> list[dict[str, object]]:
        segments = segment_plan(self.spec)
        results: list[dict[str, object]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.spec.lanes, len(segments)),
            thread_name_prefix="h3-range",
        ) as executor:
            futures = {
                executor.submit(self._download_segment, stage, segment, validator): segment
                for segment in segments
            }
            try:
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        return sorted(results, key=lambda item: int(item["index"]))

    def _assemble_candidate(self, stage: Path, validator: Validator) -> Path:
        candidate = stage / "candidate.part"
        if candidate.exists() or candidate.is_symlink():
            self._assert_owner_file(candidate)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_TRUNC
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(candidate, flags, 0o600)
        digest = hashlib.sha256()
        total = 0
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as destination:
                for segment in segment_plan(self.spec):
                    verified, _ = self._verified_segment(stage, segment, validator)
                    if not verified:
                        _fail("segment_missing", "A segment is not locally verified.")
                    part, _ = self._segment_paths(stage, segment)
                    with part.open("rb") as source:
                        segment_total = 0
                        while True:
                            block = source.read(BUFFER_BYTES)
                            if not block:
                                break
                            destination.write(block)
                            digest.update(block)
                            total += len(block)
                            segment_total += len(block)
                    if segment_total != segment.size:
                        _fail("segment_size_changed", "A segment changed during assembly.")
                destination.flush()
                os.fsync(destination.fileno())
        finally:
            os.close(descriptor)
        if total != self.spec.expected_bytes:
            _fail("assembled_bytes_mismatch", "Candidate byte count does not match the pin.")
        if digest.hexdigest() != self.spec.sha256:
            _fail("assembled_sha256_mismatch", "Candidate SHA256 does not match the pin.")
        return candidate

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _base_report(self) -> dict[str, object]:
        segments = segment_plan(self.spec)
        return {
            "schema_version": REPORT_SCHEMA,
            "source": {
                "endpoint": _safe_endpoint(self.spec.url),
                "url_sha256": hashlib.sha256(
                    self.spec.url.encode("utf-8", "strict")
                ).hexdigest(),
            },
            "destination": str(self.spec.destination),
            "staging": str(stage_path_for(self.spec)),
            "expected": {"bytes": self.spec.expected_bytes, "sha256": self.spec.sha256},
            "lanes": self.spec.lanes,
            "segment_bytes": self.spec.segment_bytes,
            "segment_count": len(segments),
            "seed_prefix": (
                None
                if self.seed_prefix is None
                else {"path": str(self.seed_prefix), "access": "read_only_copy"}
            ),
        }

    def dry_run(self) -> dict[str, object]:
        existing = _inspect_existing_final(self.spec)
        report = self._base_report()
        report.update(
            {
                "action": "dry_run",
                "status": "already_verified" if existing == "verified" else "planned",
                "execution_performed": False,
                "network_performed": False,
                "destination_written": False,
            }
        )
        return report

    def execute(self) -> dict[str, object]:
        if _inspect_existing_final(self.spec) == "verified":
            report = self._base_report()
            report.update(
                {
                    "action": "execute",
                    "status": "already_verified",
                    "execution_performed": False,
                    "network_performed": False,
                    "destination_written": False,
                }
            )
            return report

        probe = self.stable_probe()
        seed_source = self._open_seed_prefix()
        try:
            stage, lock_handle = self._prepare_stage(probe)
            try:
                seed_result = (
                    None
                    if seed_source is None
                    else self._import_seed_prefix(stage, seed_source, probe.validator)
                )
                if _inspect_existing_final(self.spec) == "verified":
                    status = "already_verified"
                    segment_results: list[dict[str, object]] = []
                    destination_written = False
                else:
                    segment_results = self._download_all_segments(stage, probe.validator)
                    candidate = self._assemble_candidate(stage, probe.validator)
                    if _inspect_existing_final(self.spec) == "verified":
                        status = "already_verified"
                        destination_written = False
                    else:
                        os.replace(candidate, self.spec.destination)
                        self._fsync_directory(self.spec.destination.parent)
                        status = "downloaded"
                        destination_written = True
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
        finally:
            if seed_source is not None:
                os.close(seed_source.descriptor)

        report = self._base_report()
        report.update(
            {
                "action": "execute",
                "status": status,
                "execution_performed": True,
                "network_performed": True,
                "destination_written": destination_written,
                "validator": {
                    "kind": probe.validator.kind,
                    "sha256": probe.validator.digest,
                },
                "probe_endpoint": probe.final_endpoint,
                "segments": segment_results,
                "seed_prefix_result": seed_result,
            }
        )
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely download one exact pinned Hugging Face artifact in ranges."
    )
    parser.add_argument("--url", required=True, help="exact pinned huggingface.co resolve URL")
    parser.add_argument("--destination", required=True, type=Path, help="absolute final path")
    parser.add_argument("--bytes", required=True, type=int, dest="expected_bytes")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--lanes", type=int, default=DEFAULT_LANES)
    parser.add_argument("--segment-bytes", type=int, default=DEFAULT_SEGMENT_BYTES)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--seed-prefix",
        type=Path,
        help="absolute contiguous prefix to copy read-only into validated staging",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true", help="perform network and filesystem writes")
    action.add_argument("--dry-run", action="store_true", help="static read-only plan (default)")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        downloader = RangeDownloader(
            DownloadSpec(
                url=args.url,
                destination=args.destination,
                expected_bytes=args.expected_bytes,
                sha256=args.sha256,
                lanes=args.lanes,
                segment_bytes=args.segment_bytes,
                timeout=args.timeout,
            ),
            seed_prefix=args.seed_prefix,
        )
        report = downloader.execute() if args.execute else downloader.dry_run()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except DownloadError as error:
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": "error",
            "error": {"code": error.code, "message": error.message},
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
