"""Signed, actor-bound decision authority for staged owner imports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from .import_storage import StagedImport
from .owner_import import OwnerImportDecisionPlan
from .owner_import_store import StoreOwnerImportPlan


TOKEN_SCHEMA_VERSION = 1
TOKEN_TYPE = "castle.provider-owner-import-decision.v1"
_MAX_TOKEN_BYTES = 8192
_MAX_LIFETIME_SECONDS = 15 * 60
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_STAGE_RE = re.compile(r"^stage_[0-9a-f]{32}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_KEYS = frozenset(
    {
        "type",
        "schema_version",
        "stage_id",
        "stage_token_sha256",
        "owner_binding",
        "account_id",
        "raw_source_sha256",
        "manifest_sha256",
        "semantic_sha256",
        "mapping_sha256",
        "profile_selection_sha256",
        "account_binding_sha256",
        "import_decision_sha256",
        "decision_plan_sha256",
        "store_plan_sha256",
        "graph_sha256",
        "stable_graph_sha256",
        "snapshot_sha256",
        "issued_at",
        "expires_at",
    }
)


class OwnerImportDecisionTokenError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedOwnerImportDecisionToken:
    stage_id: str
    account_id: str
    raw_source_sha256: str
    manifest_sha256: str
    semantic_sha256: str
    mapping_sha256: str
    profile_selection_sha256: str
    account_binding_sha256: str
    import_decision_sha256: str
    decision_plan_sha256: str
    store_plan_sha256: str
    graph_sha256: str
    stable_graph_sha256: str
    snapshot_sha256: str
    expires_at: int


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise OwnerImportDecisionTokenError("decision_token_invalid", "Decision token is invalid.")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as error:
        raise OwnerImportDecisionTokenError(
            "decision_token_invalid", "Decision token is invalid."
        ) from error


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise OwnerImportDecisionTokenError(
            "decision_token_invalid", "Decision token is invalid."
        ) from error


def _json_object(raw: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise OwnerImportDecisionTokenError(
                    "decision_token_invalid", "Decision token is invalid."
                )
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=pairs)
    except OwnerImportDecisionTokenError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OwnerImportDecisionTokenError(
            "decision_token_invalid", "Decision token is invalid."
        ) from error
    if not isinstance(value, dict) or set(value) != _PAYLOAD_KEYS:
        raise OwnerImportDecisionTokenError(
            "decision_token_invalid", "Decision token is invalid."
        )
    return value


class OwnerImportDecisionTokenCodec:
    def __init__(self, signing_key: bytes) -> None:
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("decision token signing key must contain at least 32 bytes")
        self._key = bytes(signing_key)

    def _signature(self, payload: bytes) -> bytes:
        return hmac.new(
            self._key,
            b"castle-provider-owner-import-decision-token-v1\0" + payload,
            hashlib.sha256,
        ).digest()

    def issue(
        self,
        staged: StagedImport,
        decision_plan: OwnerImportDecisionPlan,
        store_plan: StoreOwnerImportPlan,
        *,
        now: int,
    ) -> str:
        if not isinstance(staged, StagedImport):
            raise TypeError("staged must be a StagedImport")
        if not isinstance(decision_plan, OwnerImportDecisionPlan):
            raise TypeError("decision_plan must be an OwnerImportDecisionPlan")
        if not isinstance(store_plan, StoreOwnerImportPlan):
            raise TypeError("store_plan must be a StoreOwnerImportPlan")
        if type(now) is not int or now < 0:
            raise ValueError("now must be a non-negative integer")
        if (
            staged.account_id != decision_plan.account_id
            or decision_plan.account_id != store_plan.account_id
            or decision_plan.owner_user_id != store_plan.owner_user_id
            or staged.provider_scope != decision_plan.provider_scope
            or decision_plan.provider_scope != store_plan.provider_scope
            or staged.source_sha256 != decision_plan.raw_source_sha256
            or staged.manifest_sha256 != decision_plan.manifest_sha256
            or staged.semantic_sha256 != decision_plan.semantic_sha256
            or decision_plan.plan_sha256 != store_plan.decision_plan_sha256
            or decision_plan.import_decision_sha256
            != store_plan.import_decision_sha256
        ):
            raise ValueError("decision token inputs do not share one exact binding")
        expires_at = min(staged.expires_at, now + _MAX_LIFETIME_SECONDS)
        if expires_at <= now:
            raise OwnerImportDecisionTokenError(
                "decision_token_expired", "Decision token has expired."
            )
        payload = {
            "type": TOKEN_TYPE,
            "schema_version": TOKEN_SCHEMA_VERSION,
            "stage_id": staged.stage_id,
            "stage_token_sha256": hashlib.sha256(
                staged.token.encode("ascii")
            ).hexdigest(),
            "owner_binding": staged.owner_binding,
            "account_id": staged.account_id,
            "raw_source_sha256": decision_plan.raw_source_sha256,
            "manifest_sha256": decision_plan.manifest_sha256,
            "semantic_sha256": decision_plan.semantic_sha256,
            "mapping_sha256": decision_plan.mapping_sha256,
            "profile_selection_sha256": decision_plan.profile_selection_sha256,
            "account_binding_sha256": decision_plan.account_binding_sha256,
            "import_decision_sha256": decision_plan.import_decision_sha256,
            "decision_plan_sha256": decision_plan.plan_sha256,
            "store_plan_sha256": store_plan.plan_sha256,
            "graph_sha256": store_plan.graph_sha256,
            "stable_graph_sha256": store_plan.stable_graph_sha256,
            "snapshot_sha256": store_plan.snapshot_sha256,
            "issued_at": now,
            "expires_at": expires_at,
        }
        encoded = _canonical(payload)
        return f"{_b64encode(encoded)}.{_b64encode(self._signature(encoded))}"

    def verify(
        self,
        token: str,
        *,
        owner_binding: str,
        stage_id: str,
        stage_token: str,
        now: int,
    ) -> VerifiedOwnerImportDecisionToken:
        if (
            not isinstance(token, str)
            or len(token) > _MAX_TOKEN_BYTES
            or not _TOKEN_RE.fullmatch(token)
            or not isinstance(owner_binding, str)
            or not _SHA_RE.fullmatch(owner_binding)
            or not isinstance(stage_id, str)
            or not _STAGE_RE.fullmatch(stage_id)
            or not isinstance(stage_token, str)
            or type(now) is not int
            or now < 0
        ):
            raise OwnerImportDecisionTokenError(
                "decision_token_invalid", "Decision token is invalid."
            )
        payload_text, signature_text = token.split(".", 1)
        payload_bytes = _b64decode(payload_text)
        supplied = _b64decode(signature_text)
        if len(supplied) != 32 or not hmac.compare_digest(
            supplied, self._signature(payload_bytes)
        ):
            raise OwnerImportDecisionTokenError(
                "decision_token_invalid", "Decision token is invalid."
            )
        payload = _json_object(payload_bytes)
        if not hmac.compare_digest(payload_bytes, _canonical(payload)):
            raise OwnerImportDecisionTokenError(
                "decision_token_invalid", "Decision token is invalid."
            )
        if (
            payload["type"] != TOKEN_TYPE
            or payload["schema_version"] != TOKEN_SCHEMA_VERSION
            or payload["stage_id"] != stage_id
            or not isinstance(payload["owner_binding"], str)
            or not hmac.compare_digest(payload["owner_binding"], owner_binding)
            or not isinstance(payload["stage_token_sha256"], str)
            or not hmac.compare_digest(
                payload["stage_token_sha256"],
                hashlib.sha256(stage_token.encode("utf-8")).hexdigest(),
            )
            or not isinstance(payload["account_id"], str)
            or not _ID_RE.fullmatch(payload["account_id"])
        ):
            raise OwnerImportDecisionTokenError(
                "decision_token_invalid", "Decision token is invalid."
            )
        for key in _PAYLOAD_KEYS & {
            "stage_token_sha256",
            "owner_binding",
            "raw_source_sha256",
            "manifest_sha256",
            "semantic_sha256",
            "mapping_sha256",
            "profile_selection_sha256",
            "account_binding_sha256",
            "import_decision_sha256",
            "decision_plan_sha256",
            "store_plan_sha256",
            "graph_sha256",
            "stable_graph_sha256",
            "snapshot_sha256",
        }:
            if not isinstance(payload[key], str) or not _SHA_RE.fullmatch(payload[key]):
                raise OwnerImportDecisionTokenError(
                    "decision_token_invalid", "Decision token is invalid."
                )
        issued_at = payload.get("issued_at")
        expires_at = payload.get("expires_at")
        if (
            type(issued_at) is not int
            or type(expires_at) is not int
            or issued_at > now + 30
            or expires_at <= issued_at
            or expires_at - issued_at > _MAX_LIFETIME_SECONDS
        ):
            raise OwnerImportDecisionTokenError(
                "decision_token_invalid", "Decision token is invalid."
            )
        if expires_at <= now:
            raise OwnerImportDecisionTokenError(
                "decision_token_expired", "Decision token has expired."
            )
        return VerifiedOwnerImportDecisionToken(
            stage_id=stage_id,
            account_id=payload["account_id"],
            raw_source_sha256=payload["raw_source_sha256"],
            manifest_sha256=payload["manifest_sha256"],
            semantic_sha256=payload["semantic_sha256"],
            mapping_sha256=payload["mapping_sha256"],
            profile_selection_sha256=payload["profile_selection_sha256"],
            account_binding_sha256=payload["account_binding_sha256"],
            import_decision_sha256=payload["import_decision_sha256"],
            decision_plan_sha256=payload["decision_plan_sha256"],
            store_plan_sha256=payload["store_plan_sha256"],
            graph_sha256=payload["graph_sha256"],
            stable_graph_sha256=payload["stable_graph_sha256"],
            snapshot_sha256=payload["snapshot_sha256"],
            expires_at=expires_at,
        )


__all__ = [
    "OwnerImportDecisionTokenCodec",
    "OwnerImportDecisionTokenError",
    "TOKEN_SCHEMA_VERSION",
    "TOKEN_TYPE",
    "VerifiedOwnerImportDecisionToken",
]
