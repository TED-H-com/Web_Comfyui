"""Exact authenticated Oracle transport with proxies and redirects disabled."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import SceneArtError, fail
from .manifests import RenderRelease, canonical_json, reject_invalid_unicode
from .protocol import (
    CAPABILITY_PATH,
    CLAIM_PATH,
    HASH,
    MAX_OUTPUT_BYTES,
    OUTPUT_MIME,
    digest,
    error_code as validate_error_code,
    fail_path,
    lease_path,
    result_bytes_path,
    result_intent_path,
    safe_id,
)


MAX_JSON_RESPONSE_BYTES = 128 * 1024
PRODUCTION_ORACLE_ORIGINS = frozenset(filter(None, [os.environ.get("AISISTER_BASE", "").strip().rstrip("/")]))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True, slots=True)
class SecretToken:
    _value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, str)
            or len(self._value) < 32
            or len(self._value) > 512
            or not self._value.isascii()
            or any(character.isspace() or ord(character) < 0x21 for character in self._value)
        ):
            fail("worker_token_invalid", "Worker token is invalid")

    def reveal_for_authorization_header(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


def validate_oracle_origin(
    value: str,
    *,
    allowed_origins: frozenset[str] = PRODUCTION_ORACLE_ORIGINS,
) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    except (TypeError, ValueError, UnicodeError) as error:
        raise SceneArtError("oracle_origin_invalid", "Oracle origin is invalid") from error
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port not in {None, 443}
    ):
        fail("oracle_origin_invalid", "Oracle must be a credential-free HTTPS origin")
    normalized = f"https://{host}"
    if normalized not in allowed_origins:
        fail("oracle_origin_not_pinned", "Oracle origin is not allowlisted")
    return normalized


def build_secure_opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )


class OracleTransport:
    def __init__(
        self,
        origin: str,
        token: SecretToken,
        *,
        opener: urllib.request.OpenerDirector | None = None,
        allowed_origins: frozenset[str] = PRODUCTION_ORACLE_ORIGINS,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.origin = validate_oracle_origin(origin, allowed_origins=allowed_origins)
        self._token = token
        self._opener = opener or build_secure_opener()
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0 or timeout_seconds > 120:
            fail("oracle_timeout_invalid", "Oracle timeout is invalid")
        self._timeout = float(timeout_seconds)

    def _request(
        self,
        path: str,
        *,
        method: str,
        payload: object | None = None,
        binary: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        expected_statuses: frozenset[int] = frozenset({200}),
    ) -> bytes:
        if not path.startswith("/api/internal/novel-scene-art/") and path not in {
            CAPABILITY_PATH,
            CLAIM_PATH,
        }:
            fail("oracle_path_invalid", "Oracle path is outside the scene-art API")
        if "//" in path or "\x00" in path or "?" in path or "#" in path:
            fail("oracle_path_invalid", "Oracle path is invalid")
        if payload is not None and binary is not None:
            fail("oracle_request_invalid", "Oracle request body is ambiguous")
        body = canonical_json(payload) if payload is not None else binary
        request_headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token.reveal_for_authorization_header(),
            "Cache-Control": "no-store",
            "User-Agent": "web-comfyui-novel-scene-art-worker/2",
            **dict(headers or {}),
        }
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if body is not None:
            request_headers["Content-Length"] = str(len(body))
        request = urllib.request.Request(
            self.origin + path,
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status = int(response.status)
                if status not in expected_statuses:
                    raise SceneArtError("oracle_status_unexpected", f"Oracle returned HTTP {status}")
                length = response.headers.get("Content-Length")
                if length is not None:
                    declared = int(length)
                    if declared < 0 or declared > MAX_JSON_RESPONSE_BYTES:
                        fail("oracle_response_too_large", "Oracle response is too large")
                raw = response.read(MAX_JSON_RESPONSE_BYTES + 1)
        except SceneArtError:
            raise
        except urllib.error.HTTPError as error:
            # Never read or copy a response body that may contain private detail.
            raise SceneArtError(
                "oracle_http_error", f"Oracle rejected the request with HTTP {error.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise SceneArtError("oracle_transport_error", "Oracle request did not complete") from None
        if len(raw) > MAX_JSON_RESPONSE_BYTES:
            fail("oracle_response_too_large", "Oracle response is too large")
        return raw

    @staticmethod
    def _json_object(raw: bytes) -> Mapping[str, Any]:
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
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
            raise SceneArtError("oracle_response_invalid", "Oracle returned invalid JSON") from error
        if not isinstance(value, dict):
            fail("oracle_response_invalid", "Oracle response must be an object")
        return value

    def put_capability(self, worker_id: str, release: RenderRelease) -> Mapping[str, Any]:
        payload = {
            "workerId": safe_id(worker_id, "worker_id", worker=True),
            "capability": release.capability,
        }
        response = self._json_object(
            self._request(CAPABILITY_PATH, method="PUT", payload=payload)
        )
        if (
            set(response) != {"capabilityDigest", "ready", "blockers", "expiresAt"}
            or response.get("capabilityDigest") != release.capability_digest
            or response.get("ready") is not True
            or response.get("blockers") != []
            or type(response.get("expiresAt")) is not int
        ):
            fail("capability_rejected", "Oracle did not accept the exact Castle capability")
        return response

    def claim(self, worker_id: str, capability_digest: str) -> Mapping[str, Any] | None:
        if HASH.fullmatch(capability_digest) is None:
            fail("contract_invalid", "Capability digest is invalid")
        response = self._json_object(
            self._request(
                CLAIM_PATH,
                method="POST",
                payload={
                    "workerId": safe_id(worker_id, "worker_id", worker=True),
                    "capabilityDigest": capability_digest,
                },
            )
        )
        if set(response) != {"job"}:
            fail("oracle_response_invalid", "Claim response shape is invalid")
        job = response["job"]
        if job is not None and not isinstance(job, dict):
            fail("oracle_response_invalid", "Claim job is invalid")
        return job

    def renew_lease(self, job_id: str, request_id: str, lease_token: str) -> int:
        response = self._json_object(
            self._request(
                lease_path(job_id),
                method="POST",
                payload={
                    "requestId": safe_id(request_id, "request_id"),
                    "leaseToken": safe_id(lease_token, "lease_token"),
                },
            )
        )
        if set(response) != {"leaseExpiresAt"} or type(response["leaseExpiresAt"]) is not int:
            fail("oracle_response_invalid", "Lease response is invalid")
        return int(response["leaseExpiresAt"])

    def report_failure(self, job_id: str, request_id: str, lease_token: str, error_code: str) -> None:
        response = self._json_object(
            self._request(
                fail_path(job_id),
                method="POST",
                payload={
                    "requestId": safe_id(request_id, "request_id"),
                    "leaseToken": safe_id(lease_token, "lease_token"),
                    "errorCode": validate_error_code(error_code),
                },
            )
        )
        if response != {"ok": True}:
            fail("oracle_response_invalid", "Failure receipt is invalid")

    def create_result_intent(
        self,
        *,
        job_id: str,
        request_id: str,
        lease_token: str,
        render_receipt: Mapping[str, object],
        receipt_hash: str,
    ) -> tuple[str, bool]:
        digest(receipt_hash, "receipt_hash")
        if hashlib.sha256(canonical_json(render_receipt)).hexdigest() != receipt_hash:
            fail("contract_invalid", "Render receipt hash is invalid")
        response = self._json_object(
            self._request(
                result_intent_path(job_id),
                method="POST",
                payload={
                    "requestId": safe_id(request_id, "request_id"),
                    "leaseToken": safe_id(lease_token, "lease_token"),
                    "renderReceipt": render_receipt,
                },
            )
        )
        if (
            set(response) != {"submissionId", "receiptHash", "replayed"}
            or response.get("receiptHash") != receipt_hash
            or type(response.get("replayed")) is not bool
        ):
            fail("oracle_response_invalid", "Result-intent response is invalid")
        return safe_id(response["submissionId"], "submission_id"), bool(response["replayed"])

    def upload_result(
        self,
        *,
        job_id: str,
        submission_id: str,
        request_id: str,
        lease_token: str,
        receipt_hash: str,
        content_sha256: str,
        data: bytes,
    ) -> Mapping[str, Any]:
        job_id = safe_id(job_id, "job_id")
        submission_id = safe_id(submission_id, "submission_id")
        request_id = safe_id(request_id, "request_id")
        lease_token = safe_id(lease_token, "lease_token")
        receipt_hash = digest(receipt_hash, "receipt_hash")
        content_sha256 = digest(content_sha256, "content_sha256")
        if (
            not isinstance(data, bytes)
            or not data
            or len(data) > MAX_OUTPUT_BYTES
            or hashlib.sha256(data).hexdigest() != content_sha256
        ):
            fail("contract_invalid", "Result bytes do not match their content binding")
        response = self._json_object(
            self._request(
                result_bytes_path(job_id),
                method="PUT",
                binary=data,
                headers={
                    "Content-Type": OUTPUT_MIME,
                    "X-Scene-Art-Request-Id": request_id,
                    "X-Scene-Art-Lease-Token": lease_token,
                    "X-Scene-Art-Submission-Id": submission_id,
                },
            )
        )
        expected = {
            "ok": True,
            "jobId": job_id,
            "submissionId": submission_id,
            "receiptHash": receipt_hash,
            "contentHash": content_sha256,
            "publicationStatus": "stored_private",
        }
        if response != expected:
            fail("result_ack_invalid", "Oracle durable result acknowledgement is not exact")
        return response
