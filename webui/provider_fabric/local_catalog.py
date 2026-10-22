"""Fail-closed adapter from Castle feature descriptors to local offerings.

This module intentionally does not import ``webui.server``.  Callers pass a
bounded snapshot of the server-owned feature descriptors plus independently
collected runtime and quality evidence.  The catalog never infers a capability
from a title, builder name, installed node, or filename.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from .models import CapabilityKind, CapabilityOffering, EvidenceState, VisualClass


CATALOG_SCHEMA_VERSION = "castle.local-provider-catalog.v1"
LOCAL_PROVIDER_ID = "local_comfy"
MAX_FEATURES = 500

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,300}$")
_KNOWN_CLASSES = {
    VisualClass.ANIME,
    VisualClass.REALISTIC,
    VisualClass.ILLUSTRATION,
}


class LicenseState(str, Enum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    LICENSE_BLOCKED = "license_blocked"
    UNKNOWN = "unknown"


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    cleaned = value.strip().lower()
    if not _ID_RE.fullmatch(cleaned):
        raise ValueError(f"{label} must be a lowercase stable identifier")
    return cleaned


def _safe_text(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    return cleaned if _SAFE_TEXT_RE.fullmatch(cleaned) else fallback


def _strict_bool(raw: Mapping[str, Any], key: str, issues: list[str], *, default: bool = False) -> bool:
    if key not in raw:
        issues.append(f"{key}_missing")
        return default
    value = raw[key]
    if not isinstance(value, bool):
        issues.append(f"{key}_invalid")
        return default
    return value


def _strict_optional_id(raw: Mapping[str, Any], key: str, issues: list[str]) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    try:
        return _identifier(value, key)
    except ValueError:
        issues.append(f"{key}_invalid")
        return None


def _compat_value(
    raw: Mapping[str, Any],
    canonical_key: str,
    legacy_key: str,
    issues: list[str],
) -> Any:
    """Read one legacy H3 spelling without publishing it as a new contract."""

    has_canonical = canonical_key in raw
    has_legacy = legacy_key in raw
    if has_canonical and has_legacy and raw[canonical_key] != raw[legacy_key]:
        issues.append(f"{canonical_key}_conflict")
        return None
    if has_canonical:
        return raw[canonical_key]
    if has_legacy:
        return raw[legacy_key]
    return None


def _parse_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


@dataclass(frozen=True, slots=True)
class LocalRuntimeSnapshot:
    installed: bool
    nodes_ready: bool
    models_ready: bool
    workflow_ready: bool
    service_ready: bool
    runtime_ready: bool
    smoke_test_passed: bool
    license_state: LicenseState
    license_evidence_id: str | None
    source_revision: str | None
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "installed", "nodes_ready", "models_ready", "workflow_ready",
            "service_ready", "runtime_ready", "smoke_test_passed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.license_state, LicenseState):
            object.__setattr__(self, "license_state", LicenseState(self.license_state))
        if self.license_evidence_id is not None:
            object.__setattr__(
                self,
                "license_evidence_id",
                _identifier(self.license_evidence_id, "license_evidence_id"),
            )
        if self.source_revision is not None:
            object.__setattr__(self, "source_revision", _identifier(self.source_revision, "source_revision"))
        object.__setattr__(self, "issues", tuple(sorted(set(str(item) for item in self.issues))))

    @classmethod
    def missing(cls) -> "LocalRuntimeSnapshot":
        return cls(
            installed=False,
            nodes_ready=False,
            models_ready=False,
            workflow_ready=False,
            service_ready=False,
            runtime_ready=False,
            smoke_test_passed=False,
            license_state=LicenseState.UNKNOWN,
            license_evidence_id=None,
            source_revision=None,
            issues=("readiness_missing",),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LocalRuntimeSnapshot":
        if not isinstance(raw, Mapping):
            return cls.missing()
        issues: list[str] = []
        installed = _strict_bool(raw, "installed", issues)
        nodes_ready = _strict_bool(raw, "nodesReady", issues)
        models_ready = _strict_bool(raw, "modelsReady", issues)
        workflow_ready = _strict_bool(raw, "workflowReady", issues)
        service_ready = _strict_bool(raw, "serviceReady", issues)
        runtime_ready = _strict_bool(raw, "runtimeReady", issues)
        smoke_test_passed = _strict_bool(raw, "smokeTestPassed", issues)
        for key, label in (("missingNodes", "nodes"), ("missingModels", "models")):
            if key not in raw:
                continue
            missing = raw[key]
            if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
                issues.append(f"{key}_invalid")
                if label == "nodes":
                    nodes_ready = False
                else:
                    models_ready = False
            elif missing:
                issues.append(f"{key}_reported")
                if label == "nodes":
                    nodes_ready = False
                else:
                    models_ready = False
        if "runnable" in raw:
            if not isinstance(raw["runnable"], bool):
                issues.append("runnable_invalid")
                workflow_ready = False
            elif not raw["runnable"]:
                issues.append("runnable_false")
                workflow_ready = False
        if "ready" in raw:
            if not isinstance(raw["ready"], bool):
                issues.append("ready_invalid")
                runtime_ready = False
            elif not raw["ready"]:
                issues.append("ready_false")
                runtime_ready = False
        raw_license_state = _compat_value(raw, "licenseStatus", "licenceStatus", issues)
        if raw_license_state == "licence_blocked":
            raw_license_state = "license_blocked"
        try:
            license_state = LicenseState(raw_license_state)
        except (TypeError, ValueError):
            license_state = LicenseState.UNKNOWN
            issues.append(
                "licenseStatus_invalid"
                if "licenseStatus" in raw or "licenceStatus" in raw
                else "licenseStatus_missing"
            )
        raw_license_evidence = _compat_value(raw, "licenseEvidenceId", "licenceEvidenceId", issues)
        try:
            license_evidence_id = (
                _identifier(raw_license_evidence, "licenseEvidenceId")
                if raw_license_evidence is not None
                else None
            )
        except ValueError:
            license_evidence_id = None
            issues.append("licenseEvidenceId_invalid")
        source_revision = _strict_optional_id(raw, "sourceRevision", issues)
        if source_revision is None:
            issues.append("sourceRevision_missing")
        return cls(
            installed=installed,
            nodes_ready=nodes_ready,
            models_ready=models_ready,
            workflow_ready=workflow_ready,
            service_ready=service_ready,
            runtime_ready=runtime_ready,
            smoke_test_passed=smoke_test_passed,
            license_state=license_state,
            license_evidence_id=license_evidence_id,
            source_revision=source_revision,
            issues=tuple(sorted(set(issues))),
        )

    @property
    def technically_ready(self) -> bool:
        return all((
            self.installed,
            self.nodes_ready,
            self.models_ready,
            self.workflow_ready,
            self.service_ready,
            self.runtime_ready,
            self.smoke_test_passed,
            bool(self.source_revision),
        ))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "nodesReady": self.nodes_ready,
            "modelsReady": self.models_ready,
            "workflowReady": self.workflow_ready,
            "serviceReady": self.service_ready,
            "runtimeReady": self.runtime_ready,
            "smokeTestPassed": self.smoke_test_passed,
            "licenseStatus": self.license_state.value,
            "sourceRevision": self.source_revision,
        }


@dataclass(frozen=True, slots=True)
class GoldenSnapshot:
    accepted: bool
    golden_id: str | None
    variant_id: str | None
    visual_class: VisualClass | None
    tested_at: str | None
    repeat_count: int
    workflow_revision: str | None
    model_revision: str | None
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("golden accepted must be a boolean")
        for name in ("golden_id", "variant_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identifier(value, name))
        if self.visual_class is not None and not isinstance(self.visual_class, VisualClass):
            object.__setattr__(self, "visual_class", VisualClass(self.visual_class))
        if self.tested_at is not None and not _parse_timestamp(self.tested_at):
            raise ValueError("golden tested_at must be a timezone-aware timestamp")
        if isinstance(self.repeat_count, bool) or not isinstance(self.repeat_count, int) or self.repeat_count < 0:
            raise ValueError("golden repeat_count must be a non-negative integer")
        for name in ("workflow_revision", "model_revision"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not _SAFE_TEXT_RE.fullmatch(value)):
                raise ValueError(f"{name} is invalid")
        object.__setattr__(self, "issues", tuple(sorted(set(str(item) for item in self.issues))))

    @classmethod
    def missing(cls) -> "GoldenSnapshot":
        return cls(False, None, None, None, None, 0, None, None, ("golden_missing",))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "GoldenSnapshot":
        if not isinstance(raw, Mapping):
            return cls.missing()
        issues: list[str] = []
        accepted = _strict_bool(raw, "accepted", issues)
        golden_id = _strict_optional_id(raw, "goldenId", issues)
        variant_id = _strict_optional_id(raw, "variantId", issues)
        visual_class: VisualClass | None = None
        try:
            visual_class = VisualClass(raw.get("visualClass"))
        except (TypeError, ValueError):
            issues.append("golden_visualClass_invalid")
        tested_at = raw.get("testedAt")
        if not _parse_timestamp(tested_at):
            issues.append("golden_testedAt_invalid")
            tested_at = None
        repeat_count = raw.get("repeatCount")
        if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count < 0:
            issues.append("golden_repeatCount_invalid")
            repeat_count = 0
        workflow_revision = _safe_text(raw.get("workflowRevision"), "") or None
        model_revision = _safe_text(raw.get("modelRevision"), "") or None
        if workflow_revision is None:
            issues.append("golden_workflowRevision_missing")
        if model_revision is None:
            issues.append("golden_modelRevision_missing")
        if accepted:
            if not golden_id:
                issues.append("golden_id_missing")
            if repeat_count < 2:
                issues.append("golden_repeat_required")
        return cls(
            accepted=accepted,
            golden_id=golden_id,
            variant_id=variant_id,
            visual_class=visual_class,
            tested_at=tested_at,
            repeat_count=repeat_count,
            workflow_revision=workflow_revision,
            model_revision=model_revision,
            issues=tuple(sorted(set(issues))),
        )


@dataclass(frozen=True, slots=True)
class LocalEvidenceSnapshot:
    declared_state: EvidenceState
    evidence_id: str | None
    golden: GoldenSnapshot
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.declared_state, EvidenceState):
            object.__setattr__(self, "declared_state", EvidenceState(self.declared_state))
        if self.evidence_id is not None:
            object.__setattr__(self, "evidence_id", _identifier(self.evidence_id, "evidence_id"))
        if not isinstance(self.golden, GoldenSnapshot):
            raise ValueError("golden must be a GoldenSnapshot")
        object.__setattr__(self, "issues", tuple(sorted(set(str(item) for item in self.issues))))

    @classmethod
    def missing(cls) -> "LocalEvidenceSnapshot":
        return cls(EvidenceState.UNPROVEN, None, GoldenSnapshot.missing(), ("evidence_missing",))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LocalEvidenceSnapshot":
        if not isinstance(raw, Mapping):
            return cls.missing()
        issues: list[str] = []
        try:
            state = EvidenceState(raw.get("state"))
        except (TypeError, ValueError):
            state = EvidenceState.UNPROVEN
            issues.append("evidence_state_invalid" if "state" in raw else "evidence_state_missing")
        evidence_id = _strict_optional_id(raw, "evidenceId", issues)
        if state is not EvidenceState.UNPROVEN and not evidence_id:
            issues.append("evidence_id_missing")
        golden = GoldenSnapshot.from_mapping(raw.get("golden"))
        return cls(state, evidence_id, golden, tuple(sorted(set(issues))))


@dataclass(frozen=True, slots=True)
class LocalInventoryItem:
    feature_id: str
    title: str
    variant_id: str | None
    capability: CapabilityKind | None
    visual_classes: tuple[VisualClass, ...]
    option_keys: tuple[str, ...]
    api_enabled: bool
    ui_hidden: bool
    release_ready: bool
    license_requirement: str | None
    runtime: LocalRuntimeSnapshot
    evidence_state: EvidenceState
    evidence_id: str | None
    golden_id: str | None
    golden_accepted: bool
    runnable: bool
    readiness_status: str
    blocker_codes: tuple[str, ...]

    @property
    def installed(self) -> bool:
        return self.runtime.installed

    def to_dict(self) -> dict[str, Any]:
        return {
            "featureId": self.feature_id,
            "title": self.title,
            "variantId": self.variant_id,
            "capability": self.capability.value if self.capability else None,
            "visualClasses": [item.value for item in self.visual_classes],
            "optionKeys": list(self.option_keys),
            "apiEnabled": self.api_enabled,
            "uiHidden": self.ui_hidden,
            "releaseReady": self.release_ready,
            "licenseRequirement": self.license_requirement,
            **self.runtime.to_public_dict(),
            "evidenceState": self.evidence_state.value,
            "evidenceId": self.evidence_id,
            "goldenId": self.golden_id,
            "goldenAccepted": self.golden_accepted,
            "runnable": self.runnable,
            "readinessStatus": self.readiness_status,
            "blockerCodes": list(self.blocker_codes),
        }


@dataclass(frozen=True, slots=True)
class LocalProviderCatalog:
    offerings: tuple[CapabilityOffering, ...]
    inventory: tuple[LocalInventoryItem, ...]
    schema_version: str = CATALOG_SCHEMA_VERSION
    provider_id: str = LOCAL_PROVIDER_ID

    def __post_init__(self) -> None:
        if self.schema_version != CATALOG_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {CATALOG_SCHEMA_VERSION}")
        if self.provider_id != LOCAL_PROVIDER_ID:
            raise ValueError(f"provider_id must be {LOCAL_PROVIDER_ID}")
        offerings = tuple(self.offerings)
        inventory = tuple(self.inventory)
        if any(not isinstance(item, CapabilityOffering) for item in offerings):
            raise ValueError("offerings must contain CapabilityOffering objects")
        if any(item.provider_id != LOCAL_PROVIDER_ID for item in offerings):
            raise ValueError("local catalog cannot contain another provider")
        if len({item.variant_id for item in offerings}) != len(offerings):
            raise ValueError("local offering variant IDs must be unique")
        if any(not isinstance(item, LocalInventoryItem) for item in inventory):
            raise ValueError("inventory must contain LocalInventoryItem objects")
        object.__setattr__(self, "offerings", offerings)
        object.__setattr__(self, "inventory", inventory)

    @property
    def enabled_offerings(self) -> tuple[CapabilityOffering, ...]:
        return tuple(item for item in self.offerings if item.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "providerId": self.provider_id,
            "offerings": [item.to_dict() for item in self.offerings],
            "inventory": [item.to_dict() for item in self.inventory],
        }


@dataclass(frozen=True, slots=True)
class _FeatureContract:
    feature_id: str
    title: str
    variant_id: str | None
    capability: CapabilityKind | None
    visual_classes: tuple[VisualClass, ...]
    option_keys: tuple[str, ...]
    priority: int
    api_enabled: bool
    ui_hidden: bool
    release_ready: bool
    has_server_binding: bool
    license_requirement: str | None
    core_valid: bool
    issues: tuple[str, ...]


def _parse_feature(raw: Mapping[str, Any], index: int) -> _FeatureContract:
    issues: list[str] = []
    try:
        feature_id = _identifier(raw.get("id"), "feature id")
    except ValueError:
        feature_id = f"invalid-feature-{index}"
        issues.append("feature_id_invalid")
    title = _safe_text(raw.get("title"), feature_id)
    variant_id = _strict_optional_id(raw, "variantId", issues)
    if variant_id is None:
        issues.append("variantId_missing")
    capability: CapabilityKind | None = None
    try:
        capability = CapabilityKind(raw.get("capability"))
    except (TypeError, ValueError):
        issues.append("capability_invalid" if "capability" in raw else "capability_missing")
    visual_classes: tuple[VisualClass, ...] = ()
    raw_classes = raw.get("visualClasses")
    if not isinstance(raw_classes, list):
        issues.append("visualClasses_missing" if "visualClasses" not in raw else "visualClasses_invalid")
    else:
        parsed_classes: list[VisualClass] = []
        for value in raw_classes:
            try:
                parsed_classes.append(VisualClass(value))
            except (TypeError, ValueError):
                issues.append("visualClass_invalid")
        visual_classes = tuple(parsed_classes)
        if any(item not in _KNOWN_CLASSES for item in visual_classes):
            issues.append("unknown_visual_class")
        if len(visual_classes) != 1 or len(set(visual_classes)) != len(visual_classes):
            issues.append("visual_class_must_be_exactly_one")
    option_keys: tuple[str, ...] = ()
    raw_options = raw.get("optionKeys")
    if not isinstance(raw_options, list):
        issues.append("optionKeys_missing" if "optionKeys" not in raw else "optionKeys_invalid")
    else:
        parsed_options: list[str] = []
        for value in raw_options:
            try:
                parsed_options.append(_identifier(value, "option key"))
            except ValueError:
                issues.append("option_key_invalid")
        if len(parsed_options) != len(set(parsed_options)):
            issues.append("option_keys_duplicate")
        option_keys = tuple(sorted(set(parsed_options)))
    api_enabled = _strict_bool(raw, "apiEnabled", issues)
    # A missing/invalid uiHidden is treated as hidden, never as visible.
    ui_hidden = _strict_bool(raw, "uiHidden", issues, default=True)
    release_ready = _strict_bool(raw, "releaseReady", issues)
    binding = raw.get("serverBinding", raw.get("builder"))
    has_server_binding = isinstance(binding, str) and bool(binding.strip())
    if not has_server_binding:
        issues.append("server_binding_missing")
    raw_license_requirement = _compat_value(
        raw,
        "licenseRequirement",
        "licenceRequirement",
        issues,
    )
    try:
        license_requirement = (
            _identifier(raw_license_requirement, "licenseRequirement")
            if raw_license_requirement is not None
            else None
        )
    except ValueError:
        license_requirement = None
        issues.append("licenseRequirement_invalid")
    if license_requirement is None:
        issues.append("licenseRequirement_missing")
    priority = raw.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 10_000:
        priority = 100
        issues.append("priority_invalid")
    core_blockers = {
        "feature_id_invalid", "variantId_missing", "variantId_invalid", "capability_missing", "capability_invalid",
        "visualClasses_missing", "visualClasses_invalid", "visualClass_invalid",
        "unknown_visual_class", "visual_class_must_be_exactly_one", "optionKeys_missing",
        "optionKeys_invalid", "option_key_invalid", "option_keys_duplicate",
    }
    return _FeatureContract(
        feature_id=feature_id,
        title=title,
        variant_id=variant_id,
        capability=capability,
        visual_classes=visual_classes,
        option_keys=option_keys,
        priority=priority,
        api_enabled=api_enabled,
        ui_hidden=ui_hidden,
        release_ready=release_ready,
        has_server_binding=has_server_binding,
        license_requirement=license_requirement,
        core_valid=not bool(set(issues) & core_blockers),
        issues=tuple(sorted(set(issues))),
    )


def _license_ready(contract: _FeatureContract, runtime: LocalRuntimeSnapshot, blockers: list[str]) -> bool:
    requirement = contract.license_requirement
    if requirement is None:
        blockers.append("license_requirement_missing")
        return False
    if requirement == "none":
        if runtime.license_state is not LicenseState.NOT_REQUIRED:
            blockers.append("license_state_mismatch")
            return False
        return True
    if runtime.license_state is LicenseState.LICENSE_BLOCKED:
        blockers.append("license_blocked")
        return False
    if runtime.license_state is not LicenseState.APPROVED:
        blockers.append("license_unknown")
        return False
    if not runtime.license_evidence_id:
        blockers.append("license_attestation_missing")
        return False
    return True


def _quality_state(
    contract: _FeatureContract,
    evidence: LocalEvidenceSnapshot,
    blockers: list[str],
) -> tuple[EvidenceState, bool]:
    golden = evidence.golden
    blockers.extend(evidence.issues)
    blockers.extend(golden.issues)
    exact_visual_class = contract.visual_classes[0] if len(contract.visual_classes) == 1 else None
    golden_matches = all((
        golden.accepted,
        golden.golden_id,
        contract.variant_id,
        golden.variant_id == contract.variant_id,
        exact_visual_class is not None,
        golden.visual_class == exact_visual_class,
        golden.tested_at,
        golden.repeat_count >= 2,
        golden.workflow_revision,
        golden.model_revision,
    ))
    if golden.accepted and golden.variant_id != contract.variant_id:
        blockers.append("golden_variant_mismatch")
    if golden.accepted and exact_visual_class is not None and golden.visual_class != exact_visual_class:
        blockers.append("golden_visual_class_mismatch")
    verified = (
        evidence.declared_state is EvidenceState.VERIFIED
        and bool(evidence.evidence_id)
        and bool(golden_matches)
        and not evidence.issues
        and not golden.issues
    )
    if not verified:
        blockers.append("evidence_not_verified")
        blockers.append("golden_not_accepted")
    effective = EvidenceState.VERIFIED if verified else (
        EvidenceState.PROBED
        if evidence.declared_state in {EvidenceState.PROBED, EvidenceState.VERIFIED} and evidence.evidence_id
        else EvidenceState.UNPROVEN
    )
    return effective, verified


def _readiness_status(
    contract: _FeatureContract,
    runtime: LocalRuntimeSnapshot,
    quality_ready: bool,
    runnable: bool,
) -> str:
    if not runtime.installed:
        return "not_installed"
    if runtime.license_state is LicenseState.LICENSE_BLOCKED:
        return "license_blocked"
    if contract.license_requirement == "none" and runtime.license_state is not LicenseState.NOT_REQUIRED:
        return "license_state_mismatch"
    if contract.license_requirement not in {None, "none"} and runtime.license_state is not LicenseState.APPROVED:
        return "license_unknown"
    if contract.license_requirement not in {None, "none"} and not runtime.license_evidence_id:
        return "license_unattested"
    if not runtime.nodes_ready:
        return "nodes_missing"
    if not runtime.models_ready:
        return "models_missing"
    if not runtime.workflow_ready:
        return "workflow_unready"
    if not runtime.service_ready:
        return "service_unready"
    if not runtime.runtime_ready:
        return "runtime_unready"
    if not runtime.smoke_test_passed:
        return "smoke_unverified"
    if runtime.issues or not runtime.source_revision:
        return "readiness_snapshot_invalid"
    if not contract.core_valid:
        return "contract_invalid"
    if not quality_ready:
        return "quality_unverified"
    if not contract.api_enabled:
        return "api_disabled"
    if contract.ui_hidden:
        return "ui_hidden"
    if not contract.release_ready:
        return "release_not_ready"
    return "ready" if runnable else "blocked"


def build_local_provider_catalog(
    feature_descriptors: Sequence[Mapping[str, Any]],
    readiness_by_variant: Mapping[str, Mapping[str, Any] | LocalRuntimeSnapshot],
    evidence_by_variant: Mapping[str, Mapping[str, Any] | LocalEvidenceSnapshot],
) -> LocalProviderCatalog:
    """Build a deterministic local catalog without importing the web server.

    Descriptor fields are intentionally explicit: ``variantId``, ``capability``,
    one ``visualClasses`` value, ``optionKeys``, ``apiEnabled``, ``uiHidden``,
    ``releaseReady``, a server binding, and ``licenseRequirement``.  Missing
    booleans do not inherit the historical server defaults.
    """

    if not isinstance(feature_descriptors, (list, tuple)):
        raise ValueError("feature_descriptors must be a list or tuple")
    if len(feature_descriptors) > MAX_FEATURES:
        raise ValueError(f"feature_descriptors cannot exceed {MAX_FEATURES} items")
    if not isinstance(readiness_by_variant, Mapping) or not isinstance(evidence_by_variant, Mapping):
        raise ValueError("readiness/evidence inputs must be mappings")

    parsed: list[_FeatureContract] = []
    seen_variants: set[str] = set()
    for index, raw in enumerate(feature_descriptors):
        if not isinstance(raw, Mapping):
            raise ValueError(f"feature descriptor {index} must be an object")
        contract = _parse_feature(raw, index)
        if contract.variant_id:
            if contract.variant_id in seen_variants:
                raise ValueError(f"duplicate local variantId: {contract.variant_id}")
            seen_variants.add(contract.variant_id)
        parsed.append(contract)

    offerings: list[CapabilityOffering] = []
    inventory: list[LocalInventoryItem] = []
    for contract in parsed:
        raw_runtime = readiness_by_variant.get(contract.variant_id or "")
        runtime = (
            raw_runtime
            if isinstance(raw_runtime, LocalRuntimeSnapshot)
            else LocalRuntimeSnapshot.from_mapping(raw_runtime)
            if isinstance(raw_runtime, Mapping)
            else LocalRuntimeSnapshot.missing()
        )
        raw_evidence = evidence_by_variant.get(contract.variant_id or "")
        evidence = (
            raw_evidence
            if isinstance(raw_evidence, LocalEvidenceSnapshot)
            else LocalEvidenceSnapshot.from_mapping(raw_evidence)
            if isinstance(raw_evidence, Mapping)
            else LocalEvidenceSnapshot.missing()
        )
        blockers = list(contract.issues) + list(runtime.issues)
        license_ready = _license_ready(contract, runtime, blockers)
        evidence_state, quality_ready = _quality_state(contract, evidence, blockers)
        if not contract.api_enabled:
            blockers.append("api_disabled")
        if contract.ui_hidden:
            blockers.append("ui_hidden")
        if not contract.release_ready:
            blockers.append("release_not_ready")
        if not contract.has_server_binding:
            blockers.append("server_binding_missing")
        if not runtime.technically_ready:
            blockers.append("runtime_not_ready")
        runnable = all((
            contract.core_valid,
            not contract.issues,
            contract.api_enabled,
            not contract.ui_hidden,
            contract.release_ready,
            contract.has_server_binding,
            runtime.technically_ready,
            not runtime.issues,
            license_ready,
            quality_ready,
        ))
        blocker_codes = tuple(sorted(set(blockers)))
        status = _readiness_status(contract, runtime, quality_ready, runnable)
        golden_accepted = quality_ready
        inventory.append(LocalInventoryItem(
            feature_id=contract.feature_id,
            title=contract.title,
            variant_id=contract.variant_id,
            capability=contract.capability,
            visual_classes=contract.visual_classes,
            option_keys=contract.option_keys,
            api_enabled=contract.api_enabled,
            ui_hidden=contract.ui_hidden,
            release_ready=contract.release_ready,
            license_requirement=contract.license_requirement,
            runtime=runtime,
            evidence_state=evidence_state,
            evidence_id=evidence.evidence_id,
            golden_id=evidence.golden.golden_id if golden_accepted else None,
            golden_accepted=golden_accepted,
            runnable=runnable,
            readiness_status=status,
            blocker_codes=blocker_codes,
        ))
        if contract.core_valid and contract.variant_id and contract.capability and contract.visual_classes:
            offerings.append(CapabilityOffering(
                provider_id=LOCAL_PROVIDER_ID,
                capability=contract.capability,
                variant_id=contract.variant_id,
                visual_classes=frozenset(contract.visual_classes),
                evidence=evidence_state,
                evidence_id=evidence.evidence_id,
                enabled=runnable,
                priority=contract.priority,
                option_keys=frozenset(contract.option_keys),
            ))

    offerings.sort(key=lambda item: (item.capability.value, item.variant_id))
    inventory.sort(key=lambda item: (item.variant_id or "~", item.feature_id))
    return LocalProviderCatalog(tuple(offerings), tuple(inventory))
