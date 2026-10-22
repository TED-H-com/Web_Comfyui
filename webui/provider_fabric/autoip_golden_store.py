"""Owner/account-bound AutoIP golden evidence with fail-closed parsing.

The store contains hashes and bounded review evidence, never credentials or
provider payloads.  A saved manifest is usable only with the exact current
compatibility receipt that was present when it was written.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Sequence

from .autoip_profiles import candidate_autoip_profiles
from .models import CapabilityKind, VisualClass
from .quality import (
    GateResult, GateVerdict, GoldenCase, GoldenReceipt, HumanReview,
    HumanVerdict, RouteOrigin, RuntimeFingerprint,
)


SCHEMA_VERSION = "castle.autoip-golden-evidence.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_PROBE_ID = re.compile(r"^autoip-probe:[0-9a-f]{32}$")
MAX_CASES = 32
MAX_RECEIPTS = 128
MAX_BYTES = 512 * 1024


class AutoIPGoldenStoreError(ValueError):
    pass


def _hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _runtime(value: Mapping[str, Any]) -> RuntimeFingerprint:
    if not isinstance(value, Mapping) or set(value) != {
        "workflow_sha256", "model_sha256s", "engine_revision", "variant_revision",
        "attention_backend", "acceleration_profile", "settings_sha256",
    }:
        raise AutoIPGoldenStoreError("autoip_golden_runtime_invalid")
    return RuntimeFingerprint(**dict(value))


def _case(value: Mapping[str, Any]) -> GoldenCase:
    if not isinstance(value, Mapping) or set(value) != {
        "case_id", "capability", "visual_class", "semantics_id",
        "input_sha256s", "required_gates",
    }:
        raise AutoIPGoldenStoreError("autoip_golden_case_invalid")
    return GoldenCase(
        case_id=value["case_id"], capability=CapabilityKind(value["capability"]),
        visual_class=VisualClass(value["visual_class"]), semantics_id=value["semantics_id"],
        input_sha256s=tuple(value["input_sha256s"]),
        required_gates=frozenset(value["required_gates"]),
    )


def _gate(value: Mapping[str, Any]) -> GateResult:
    if not isinstance(value, Mapping) or set(value) != {
        "gate", "verdict", "evidence_ids", "metric_values",
    } or not isinstance(value["metric_values"], Mapping):
        raise AutoIPGoldenStoreError("autoip_golden_gate_invalid")
    return GateResult(
        gate=value["gate"], verdict=GateVerdict(value["verdict"]),
        evidence_ids=tuple(value["evidence_ids"]),
        metric_values=tuple(value["metric_values"].items()),
    )


def _receipt(value: Mapping[str, Any]) -> GoldenReceipt:
    required = {
        "receipt_id", "case_id", "route_variant_id", "origin", "runtime",
        "runtime_sha256", "output_sha256", "repeat_index", "gate_results",
        "human_review", "duration_seconds", "peak_vram_bytes", "peak_ram_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AutoIPGoldenStoreError("autoip_golden_receipt_invalid")
    review = value["human_review"]
    if not isinstance(review, Mapping) or set(review) != {
        "reviewer_id", "verdict", "blind_review", "review_evidence_id",
    }:
        raise AutoIPGoldenStoreError("autoip_golden_review_invalid")
    runtime = _runtime(value["runtime"])
    if value["runtime_sha256"] != runtime.digest:
        raise AutoIPGoldenStoreError("autoip_golden_runtime_hash_invalid")
    return GoldenReceipt(
        receipt_id=value["receipt_id"], case_id=value["case_id"],
        route_variant_id=value["route_variant_id"], origin=RouteOrigin(value["origin"]),
        runtime=runtime, output_sha256=value["output_sha256"],
        repeat_index=value["repeat_index"],
        gate_results=tuple(_gate(item) for item in value["gate_results"]),
        human_review=HumanReview(
            reviewer_id=review["reviewer_id"], verdict=HumanVerdict(review["verdict"]),
            blind_review=review["blind_review"], review_evidence_id=review["review_evidence_id"],
        ),
        duration_seconds=value["duration_seconds"], peak_vram_bytes=value["peak_vram_bytes"],
        peak_ram_bytes=value["peak_ram_bytes"],
    )


class AutoIPGoldenEvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AutoIPGoldenStoreError("autoip_golden_root_unsafe")

    def _path(self, owner_user_id: str, account_id: str) -> Path:
        if not isinstance(owner_user_id, str) or not owner_user_id or not isinstance(account_id, str) or not _ID.fullmatch(account_id):
            raise AutoIPGoldenStoreError("autoip_golden_identity_invalid")
        digest = hashlib.sha256((owner_user_id + "\0" + account_id).encode("utf-8")).hexdigest()
        return self.root / f"golden-{digest}.json"

    @staticmethod
    def _validate_scope(cases: Sequence[GoldenCase], receipts: Sequence[GoldenReceipt]) -> None:
        variants = {profile.variant_id: profile for profile in candidate_autoip_profiles()}
        case_ids: set[str] = set()
        for case in cases:
            profile = variants.get(case.semantics_id)
            if (
                profile is None or profile.capability is not case.capability
                or profile.visual_class is not case.visual_class or case.case_id in case_ids
            ):
                raise AutoIPGoldenStoreError("autoip_golden_case_scope_invalid")
            case_ids.add(case.case_id)
        receipt_ids: set[str] = set()
        for receipt in receipts:
            if (
                receipt.origin is not RouteOrigin.EXTERNAL
                or receipt.case_id not in case_ids
                or receipt.receipt_id in receipt_ids
            ):
                raise AutoIPGoldenStoreError("autoip_golden_receipt_scope_invalid")
            case = next(item for item in cases if item.case_id == receipt.case_id)
            if receipt.route_variant_id != case.semantics_id:
                raise AutoIPGoldenStoreError("autoip_golden_receipt_scope_invalid")
            receipt_ids.add(receipt.receipt_id)

    def record(
        self, owner_user_id: str, account_id: str, compatibility_receipt_id: str,
        cases: Sequence[GoldenCase], receipts: Sequence[GoldenReceipt],
    ) -> dict[str, Any]:
        cases, receipts = tuple(cases), tuple(receipts)
        if _PROBE_ID.fullmatch(str(compatibility_receipt_id or "")) is None:
            raise AutoIPGoldenStoreError("autoip_golden_probe_binding_invalid")
        if not 1 <= len(cases) <= MAX_CASES or not 1 <= len(receipts) <= MAX_RECEIPTS:
            raise AutoIPGoldenStoreError("autoip_golden_evidence_count_invalid")
        if any(not isinstance(item, GoldenCase) for item in cases) or any(not isinstance(item, GoldenReceipt) for item in receipts):
            raise AutoIPGoldenStoreError("autoip_golden_evidence_type_invalid")
        self._validate_scope(cases, receipts)
        material = {
            "schema_version": SCHEMA_VERSION, "owner_user_id": owner_user_id,
            "account_id": account_id, "compatibility_receipt_id": compatibility_receipt_id,
            "cases": [item.to_dict() for item in cases],
            "receipts": [item.to_dict() for item in receipts],
        }
        document = {**material, "manifest_sha256": _hash(material)}
        encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise AutoIPGoldenStoreError("autoip_golden_manifest_too_large")
        path = self._path(owner_user_id, account_id)
        descriptor, temporary = tempfile.mkstemp(prefix=".golden-", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1; stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if descriptor >= 0: os.close(descriptor)
            try: os.unlink(temporary)
            except FileNotFoundError: pass
        return {"schema_version": SCHEMA_VERSION, "manifest_sha256": document["manifest_sha256"], "case_count": len(cases), "receipt_count": len(receipts), "persisted": True}

    def current(
        self, owner_user_id: str, account_id: str, compatibility_receipt_id: str,
    ) -> tuple[tuple[GoldenCase, ...], tuple[GoldenReceipt, ...]]:
        path = self._path(owner_user_id, account_id)
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= MAX_BYTES:
                return (), ()
            raw = path.read_bytes(); after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != before.st_size:
                return (), ()
            value = json.loads(raw.decode("utf-8", "strict"))
            required = {"schema_version", "owner_user_id", "account_id", "compatibility_receipt_id", "cases", "receipts", "manifest_sha256"}
            if not isinstance(value, dict) or set(value) != required:
                return (), ()
            material = {key: value[key] for key in required - {"manifest_sha256"}}
            if value["schema_version"] != SCHEMA_VERSION or value["owner_user_id"] != owner_user_id or value["account_id"] != account_id or value["compatibility_receipt_id"] != compatibility_receipt_id or value["manifest_sha256"] != _hash(material):
                return (), ()
            cases = tuple(_case(item) for item in value["cases"])
            receipts = tuple(_receipt(item) for item in value["receipts"])
            if not 1 <= len(cases) <= MAX_CASES or not 1 <= len(receipts) <= MAX_RECEIPTS:
                return (), ()
            self._validate_scope(cases, receipts)
            return cases, receipts
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return (), ()


__all__ = ["AutoIPGoldenEvidenceStore", "AutoIPGoldenStoreError", "SCHEMA_VERSION"]
