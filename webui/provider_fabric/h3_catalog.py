"""Deterministic MiniMAX H3 exact-class catalog candidates and readiness."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping


H3_VARIANT_CONTRACTS: Mapping[str, str] = MappingProxyType({
    "image-video.anime.h3-fl2va.v1": "h3.i2v.anime.stock.v1",
    "image-video.realistic.h3-fl2va.v1": "h3.i2v.realistic.stock.v1",
    "reference-video.anime.h3-ref2va-image.v1": "h3.ref2va-image.anime.stock.v1",
    "reference-video.realistic.h3-ref2va-image.v1": "h3.ref2va-image.realistic.stock.v1",
})


def h3_golden_id(contract_id: str) -> str:
    if contract_id not in H3_VARIANT_CONTRACTS.values():
        raise ValueError("unknown H3 contract")
    return "golden-" + contract_id.replace(".", "-")


def validate_h3_evidence_aliases(evidence_by_variant: Mapping[str, Mapping[str, Any]]) -> None:
    """Validate optional quality aliases while the private receipt stays private.

    H3 execution does not depend on these aliases.  They remain useful for
    publishing an operator-reviewed quality result without confusing it with
    the local model/node/license readiness decision.
    """

    for variant_id, contract_id in H3_VARIANT_CONTRACTS.items():
        evidence = evidence_by_variant.get(variant_id)
        if evidence is None:
            continue
        if not isinstance(evidence, Mapping):
            raise ValueError("H3 evidence must be an object")
        expected = h3_golden_id(contract_id)
        golden = evidence.get("golden")
        if (
            evidence.get("evidenceId") != expected
            or not isinstance(golden, Mapping)
            or golden.get("goldenId") != expected
            or golden.get("variantId") != variant_id
        ):
            raise ValueError("H3 evidence alias does not match its exact variant")


def h3_feature_descriptors() -> tuple[dict[str, Any], ...]:
    result = []
    for variant_id, contract_id in H3_VARIANT_CONTRACTS.items():
        visual_class = "anime" if ".anime." in variant_id else "realistic"
        reference = variant_id.startswith("reference-video.")
        result.append({
            "id": f"h3-{'reference' if reference else 'image'}-video-{visual_class}",
            "title": f"MiniMAX H3 {'參考影片' if reference else '圖生影片'} {visual_class}",
            "builder": "minimax_h3_ref2va" if reference else "minimax_h3_fl2va",
            "variantId": variant_id,
            "capability": "image_to_video",
            "visualClasses": [visual_class],
            "optionKeys": ["aspect", "motion_profile", "quality"],
            "apiEnabled": True,
            "uiHidden": False,
            "releaseReady": True,
            "licenseRequirement": "operator_authorization",
            "priority": 20,
            "readinessContractId": contract_id,
        })
    return tuple(sorted(result, key=lambda item: item["variantId"]))


def h3_readiness_by_variant(snapshot: object, *, source_revision: str) -> dict[str, dict[str, Any]]:
    """Project technical readiness while keeping quality evidence informational."""

    contracts = getattr(snapshot, "contracts", ())
    by_id = {getattr(item, "contract_id", ""): item for item in contracts}
    result = {}
    for variant_id, contract_id in H3_VARIANT_CONTRACTS.items():
        item = by_id.get(contract_id)
        if item is None:
            result[variant_id] = {}
            continue
        configuration_ready = getattr(item.configuration, "ready", False) is True
        authorization_ready = getattr(item.authorization, "ready", False) is True
        models_ready = getattr(item.models, "ready", False) is True
        runtime_ready = getattr(item.runtime, "ready", False) is True
        technical_ready = all((
            configuration_ready,
            authorization_ready,
            models_ready,
            runtime_ready,
        ))
        result[variant_id] = {
            "installed": configuration_ready,
            "nodesReady": runtime_ready,
            "modelsReady": models_ready,
            "workflowReady": configuration_ready,
            "serviceReady": runtime_ready,
            "runtimeReady": runtime_ready,
            # LocalRuntimeSnapshot retains this historical field name.  For
            # H3 it now means the complete technical preflight passed; a
            # human quality golden is reported independently below.
            "smokeTestPassed": technical_ready,
            "qualityGoldenCurrent": getattr(item.golden, "ready", False) is True,
            "licenseStatus": "approved" if authorization_ready else "license_blocked",
            "licenseEvidenceId": (
                "minimax-h3-operator-authorization-v1" if authorization_ready else None
            ),
            "sourceRevision": source_revision,
        }
    return result


__all__ = [
    "H3_VARIANT_CONTRACTS",
    "h3_feature_descriptors",
    "h3_golden_id",
    "h3_readiness_by_variant",
    "validate_h3_evidence_aliases",
]
