"""Minimal ComfyUI client for one pinned, locally compiled image graph."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .compiler import CompiledGraph
from .errors import SceneArtError, fail
from .manifests import canonical_json, reject_invalid_unicode
from .secure_path import (
    DIRECTORY_FLAGS,
    READ_FILE_FLAGS,
    close_descriptors,
    normalized_absolute,
    open_absolute_directory,
)


COMFY_ORIGIN = "http://127.0.0.1:8188"
MAX_JSON_BYTES = 32 * 1024 * 1024
SAFE_PNG_NAME = re.compile(r"^[a-f0-9-]{36}_[0-9]{5}_\.png$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def build_loopback_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPHandler(),
    )


def validate_comfy_origin(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
    except (TypeError, ValueError) as error:
        raise SceneArtError("comfy_origin_invalid", "ComfyUI origin is invalid") from error
    if (
        parsed.scheme != "http"
        or address != ipaddress.ip_address("127.0.0.1")
        or parsed.port != 8188
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        fail("comfy_origin_invalid", "ComfyUI must use exact numeric loopback port 8188")
    return COMFY_ORIGIN


def _proc_address(encoded: str, *, ipv6: bool) -> ipaddress._BaseAddress:
    try:
        raw = bytes.fromhex(encoded)
        if ipv6:
            if len(raw) != 16:
                raise ValueError
            return ipaddress.ip_address(
                b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
            )
        return ipaddress.ip_address(struct.pack("<I", int(encoded, 16)))
    except (ValueError, struct.error) as error:
        raise SceneArtError("comfy_listener_invalid", "ComfyUI listener state is invalid") from error


def _listeners(path: Path, *, ipv6: bool) -> list[ipaddress._BaseAddress]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()[1:]
    except FileNotFoundError:
        return []
    except OSError as error:
        raise SceneArtError("comfy_listener_unavailable", "ComfyUI listener state is unavailable") from error
    result: list[ipaddress._BaseAddress] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[3] != "0A":
            continue
        encoded, separator, port = fields[1].rpartition(":")
        if not separator:
            fail("comfy_listener_invalid", "ComfyUI listener state is invalid")
        try:
            port_value = int(port, 16)
        except ValueError as error:
            raise SceneArtError("comfy_listener_invalid", "ComfyUI listener port is invalid") from error
        if port_value == 8188:
            result.append(_proc_address(encoded, ipv6=ipv6))
    return result


def verify_loopback_listener(
    *,
    tcp_path: Path = Path("/proc/net/tcp"),
    tcp6_path: Path = Path("/proc/net/tcp6"),
) -> None:
    listeners = _listeners(tcp_path, ipv6=False) + _listeners(tcp6_path, ipv6=True)
    if not listeners:
        fail("comfy_listener_missing", "ComfyUI has no listener on port 8188")
    expected = ipaddress.ip_address("127.0.0.1")
    if not listeners or any(address != expected for address in listeners):
        fail("comfy_listener_exposed", "ComfyUI listener is not confined to exact IPv4 loopback")


@dataclass(frozen=True, slots=True)
class BoundOutput:
    path: Path
    data: bytes


class ComfyClient:
    def __init__(
        self,
        *,
        origin: str = COMFY_ORIGIN,
        output_root: Path,
        opener: urllib.request.OpenerDirector | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.origin = validate_comfy_origin(origin)
        self.output_root = normalized_absolute(
            output_root,
            code="comfy_output_unavailable",
            message="ComfyUI output root is unavailable",
        )
        descriptors = open_absolute_directory(
            self.output_root,
            code="comfy_output_unavailable",
            message="ComfyUI output root is unavailable",
            definite_pre_submit=False,
        )
        try:
            root_info = os.fstat(descriptors[-1])
            if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
                fail("comfy_output_unsafe", "ComfyUI output root is unsafe")
        finally:
            close_descriptors(descriptors)
        self._opener = opener or build_loopback_opener()
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0 or timeout_seconds > 120:
            fail("comfy_timeout_invalid", "ComfyUI timeout is invalid")
        self._timeout = float(timeout_seconds)

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: object | None = None,
    ) -> Mapping[str, Any]:
        if not path.startswith("/") or "//" in path or "\x00" in path or "?" in path or "#" in path:
            fail("comfy_path_invalid", "ComfyUI path is invalid")
        body = canonical_json(payload) if payload is not None else None
        headers = {"Accept": "application/json", "User-Agent": "web-comfyui-novel-scene-art-worker/2"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.origin + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if int(response.status) != 200:
                    fail("comfy_status_unexpected", "ComfyUI returned an unexpected status", definite_pre_submit=False)
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > MAX_JSON_BYTES:
                    fail("comfy_response_too_large", "ComfyUI response is too large", definite_pre_submit=False)
                raw = response.read(MAX_JSON_BYTES + 1)
        except SceneArtError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise SceneArtError(
                "comfy_transport_error",
                "ComfyUI request did not complete",
                definite_pre_submit=False,
            ) from None
        if len(raw) > MAX_JSON_BYTES:
            fail("comfy_response_too_large", "ComfyUI response is too large", definite_pre_submit=False)
        def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in values:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = item
            return result

        try:
            value = json.loads(
                raw.decode("utf-8", "strict"),
                object_pairs_hook=pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            reject_invalid_unicode(value)
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
            raise SceneArtError(
                "comfy_response_invalid",
                "ComfyUI returned invalid JSON",
                definite_pre_submit=False,
            ) from error
        if not isinstance(value, dict):
            fail("comfy_response_invalid", "ComfyUI response must be an object", definite_pre_submit=False)
        return value

    def verify_graph_nodes(self, compiled: CompiledGraph) -> None:
        object_info = self._request("/object_info")
        for node in compiled.graph.values():
            if not isinstance(node, dict):
                fail("comfy_graph_invalid", "Compiled graph contains an invalid node")
            class_type = node.get("class_type")
            if not isinstance(class_type, str) or class_type not in object_info:
                fail("comfy_node_unavailable", "A pinned ComfyUI node is unavailable")

    def require_queue_idle(self) -> None:
        queue = self._request("/queue")
        if set(queue) != {"queue_running", "queue_pending"}:
            fail("comfy_queue_invalid", "ComfyUI queue response is invalid")
        if (
            not isinstance(queue["queue_running"], list)
            or not isinstance(queue["queue_pending"], list)
            or queue["queue_running"]
            or queue["queue_pending"]
        ):
            fail("comfy_queue_busy", "ComfyUI queue is not idle")

    def submit(self, compiled: CompiledGraph) -> None:
        response = self._request(
            "/prompt",
            method="POST",
            payload={
                "prompt": compiled.graph,
                "client_id": "web-comfyui-novel-scene-art-v2",
                "prompt_id": compiled.prompt_id,
            },
        )
        if response.get("prompt_id") != compiled.prompt_id or response.get("node_errors") not in ({}, None):
            fail("comfy_submission_rejected", "ComfyUI did not accept the exact prompt ID", definite_pre_submit=False)

    def history(self, prompt_id: str) -> Mapping[str, Any] | None:
        try:
            canonical = str(__import__("uuid").UUID(prompt_id))
        except (ValueError, AttributeError) as error:
            raise SceneArtError("prompt_id_invalid", "Prompt ID is invalid") from error
        if canonical != prompt_id:
            fail("prompt_id_invalid", "Prompt ID is not canonical")
        response = self._request("/history/" + urllib.parse.quote(prompt_id, safe=""))
        if not response:
            return None
        if set(response) != {prompt_id} or not isinstance(response[prompt_id], dict):
            fail("comfy_history_invalid", "ComfyUI history binding is invalid", definite_pre_submit=False)
        return response[prompt_id]

    @staticmethod
    def verify_history_binding(
        history: Mapping[str, Any],
        *,
        prompt_id: str,
        graph_sha256: str,
    ) -> None:
        prompt = history.get("prompt")
        if (
            not isinstance(prompt, list)
            or len(prompt) < 3
            or prompt[1] != prompt_id
            or not isinstance(prompt[2], dict)
            or hashlib.sha256(canonical_json(prompt[2])).hexdigest() != graph_sha256
        ):
            fail(
                "comfy_history_graph_mismatch",
                "ComfyUI history is not bound to the compiled workflow graph",
                definite_pre_submit=False,
            )

    @classmethod
    def verify_history_graph(cls, history: Mapping[str, Any], compiled: CompiledGraph) -> None:
        cls.verify_history_binding(
            history,
            prompt_id=compiled.prompt_id,
            graph_sha256=compiled.graph_sha256,
        )

    def wait_for_history(
        self,
        prompt_id: str,
        *,
        deadline_epoch: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Mapping[str, Any]:
        while time.time() < deadline_epoch:
            history = self.history(prompt_id)
            if history is not None:
                # This method has no compiled graph and is retained only for
                # callers that separately verify it. The worker uses its own
                # bound wait loop.
                status = history.get("status")
                if not isinstance(status, dict):
                    fail("comfy_history_invalid", "ComfyUI history status is invalid", definite_pre_submit=False)
                if status.get("status_str") != "success" or status.get("completed") is not True:
                    fail("comfy_render_failed", "ComfyUI render did not complete successfully", definite_pre_submit=False)
                return history
            sleep(1.0)
        fail("comfy_history_timeout", "ComfyUI history deadline expired", definite_pre_submit=False)

    def bind_output(
        self,
        history: Mapping[str, Any],
        compiled: CompiledGraph,
        *,
        maximum_bytes: int,
    ) -> BoundOutput:
        return self.bind_persisted_output(
            history,
            prompt_id=compiled.prompt_id,
            graph_sha256=compiled.graph_sha256,
            output_node_id=compiled.output_node_id,
            output_prefix=compiled.output_prefix,
            maximum_bytes=maximum_bytes,
        )

    def bind_persisted_output(
        self,
        history: Mapping[str, Any],
        *,
        prompt_id: str,
        graph_sha256: str,
        output_node_id: str,
        output_prefix: str,
        maximum_bytes: int,
    ) -> BoundOutput:
        self.verify_history_binding(
            history,
            prompt_id=prompt_id,
            graph_sha256=graph_sha256,
        )
        outputs = history.get("outputs")
        if not isinstance(outputs, dict):
            fail("comfy_output_binding_invalid", "ComfyUI output metadata is missing", definite_pre_submit=False)
        expected_directory, expected_token = output_prefix.rsplit("/", 1)
        expected_item: Mapping[str, Any] | None = None
        output_items = 0
        for node_id, node_output in outputs.items():
            if not isinstance(node_output, dict):
                continue
            images = node_output.get("images")
            if not isinstance(images, list):
                continue
            for item in images:
                if isinstance(item, dict) and item.get("type") == "output":
                    output_items += 1
                    if node_id == output_node_id:
                        if expected_item is not None:
                            fail("comfy_output_binding_invalid", "Output cardinality changed", definite_pre_submit=False)
                        expected_item = item
        if output_items != 1 or expected_item is None:
            fail("comfy_output_binding_invalid", "Exact output node binding failed", definite_pre_submit=False)
        filename = expected_item.get("filename")
        if (
            expected_item.get("subfolder") != expected_directory
            or not isinstance(filename, str)
            or SAFE_PNG_NAME.fullmatch(filename) is None
            or re.fullmatch(re.escape(expected_token) + r"_[0-9]{5}_\.png", filename) is None
        ):
            fail("comfy_output_binding_invalid", "Output filename escaped its namespace", definite_pre_submit=False)
        parts = expected_directory.split("/")
        if len(parts) != 2 or parts[0] != "NovelSceneArt" or not re.fullmatch(r"[a-f0-9]{32}", parts[1]):
            fail("comfy_output_binding_invalid", "Output directory binding is invalid", definite_pre_submit=False)
        return self._bind_exact_output(parts, filename, maximum_bytes=maximum_bytes)

    def bind_orphaned_output(
        self,
        *,
        output_prefix: str,
        maximum_bytes: int,
    ) -> BoundOutput | None:
        """Find one deterministic crash-orphan for cleanup, never publication."""

        try:
            expected_directory, expected_token = output_prefix.rsplit("/", 1)
        except ValueError:
            fail("comfy_output_binding_invalid", "Output prefix is invalid", definite_pre_submit=False)
        parts = expected_directory.split("/")
        if (
            len(parts) != 2
            or parts[0] != "NovelSceneArt"
            or not re.fullmatch(r"[a-f0-9]{32}", parts[1])
            or SAFE_PNG_NAME.fullmatch(f"{expected_token}_00001_.png") is None
        ):
            fail("comfy_output_binding_invalid", "Output namespace is invalid", definite_pre_submit=False)
        descriptors: list[int] = []
        try:
            descriptors = open_absolute_directory(
                self.output_root,
                code="comfy_output_unavailable",
                message="ComfyUI output root is unavailable",
                definite_pre_submit=False,
            )
            descriptor = descriptors[-1]
            root_info = os.fstat(descriptor)
            if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
                fail(
                    "comfy_output_unsafe",
                    "Output root is not owner-controlled",
                    definite_pre_submit=False,
                )
            for component in parts:
                try:
                    descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    return None
                descriptors.append(descriptor)
                directory_info = os.fstat(descriptor)
                if directory_info.st_uid != os.geteuid() or directory_info.st_mode & 0o077:
                    fail(
                        "comfy_output_unsafe",
                        "Output directory is not owner-controlled",
                        definite_pre_submit=False,
                    )
            pattern = re.compile(re.escape(expected_token) + r"_[0-9]{5}_\.png")
            matches: list[str] = []
            with os.scandir(descriptor) as entries:
                for count, entry in enumerate(entries, start=1):
                    if count > 16:
                        fail(
                            "comfy_output_binding_invalid",
                            "Crash output namespace has unexpected cardinality",
                            definite_pre_submit=False,
                        )
                    if pattern.fullmatch(entry.name):
                        matches.append(entry.name)
            if not matches:
                return None
            if len(matches) != 1:
                fail(
                    "comfy_output_binding_invalid",
                    "Crash output cardinality changed",
                    definite_pre_submit=False,
                )
            filename = matches[0]
        except SceneArtError:
            raise
        except OSError as error:
            raise SceneArtError(
                "comfy_output_unavailable",
                "Crash output namespace is unavailable",
                definite_pre_submit=False,
            ) from error
        finally:
            close_descriptors(descriptors)
        return self._bind_exact_output(parts, filename, maximum_bytes=maximum_bytes)

    def _bind_exact_output(
        self,
        parts: list[str],
        filename: str,
        *,
        maximum_bytes: int,
    ) -> BoundOutput:
        lexical = self.output_root.joinpath(*parts, filename)
        descriptors: list[int] = []
        try:
            descriptors = open_absolute_directory(
                self.output_root,
                code="comfy_output_unavailable",
                message="Bound ComfyUI output could not be read",
                definite_pre_submit=False,
            )
            descriptor = descriptors[-1]
            root_info = os.fstat(descriptor)
            if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
                fail(
                    "comfy_output_unsafe",
                    "Output root is not owner-controlled",
                    definite_pre_submit=False,
                )
            for component in parts:
                descriptor = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
                descriptors.append(descriptor)
                directory_info = os.fstat(descriptor)
                if directory_info.st_uid != os.geteuid() or directory_info.st_mode & 0o077:
                    fail("comfy_output_unsafe", "Output directory is not owner-controlled", definite_pre_submit=False)
            descriptor = os.open(filename, READ_FILE_FLAGS, dir_fd=descriptor)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or before.st_mode & 0o022
                or before.st_size <= 0
                or before.st_size > maximum_bytes
            ):
                fail("comfy_output_unsafe", "Bound ComfyUI output is unsafe", definite_pre_submit=False)
            # The Comfy service may create a 0644 output under a private tree.
            # Quarantine it on the already bound descriptor before reading.
            os.fchmod(descriptor, 0o600)
            before = os.fstat(descriptor)
            if stat.S_IMODE(before.st_mode) != 0o600:
                fail("comfy_output_unsafe", "Bound output is not owner-private", definite_pre_submit=False)
            chunks: list[bytes] = []
            remaining = maximum_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        except OSError as error:
            raise SceneArtError(
                "comfy_output_unavailable",
                "Bound ComfyUI output could not be read",
                definite_pre_submit=False,
            ) from error
        finally:
            close_descriptors(descriptors)
        data = b"".join(chunks)
        if (
            len(data) != before.st_size
            or len(data) > maximum_bytes
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns)
        ):
            fail("comfy_output_changed", "Bound ComfyUI output changed while reading", definite_pre_submit=False)
        return BoundOutput(lexical, data)


def require_gpu_headroom(
    *,
    minimum_free_mib: int = 26_000,
    maximum_utilization_percent: int = 20,
    command: tuple[str, ...] = (
        "/usr/bin/nvidia-smi",
        "--query-gpu=memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ),
) -> tuple[int, int]:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
        if len(rows) != 1:
            raise ValueError
        fields = [field.strip() for field in rows[0].split(",")]
        if len(fields) != 2:
            raise ValueError
        free_mib, utilization = (int(value) for value in fields)
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise SceneArtError("gpu_status_unavailable", "RTX 5090 status is unavailable") from error
    if free_mib < minimum_free_mib or utilization > maximum_utilization_percent:
        fail("gpu_headroom_unavailable", "RTX 5090 does not have safe render headroom")
    return free_mib, utilization
