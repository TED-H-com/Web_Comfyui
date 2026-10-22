"""Issue one exact AutoIP golden from adopted Castle output lineage."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping, Sequence

from .autoip_contract import CONTRACT_ID
from .autoip_golden_store import AutoIPGoldenEvidenceStore
from .autoip_profiles import PROFILE_REVISION, candidate_autoip_profiles
from .models import CapabilityKind, VisualClass
from .quality import (
    GateResult, GateVerdict, GoldenCase, GoldenReceipt, HumanReview,
    HumanVerdict, RouteOrigin, RuntimeFingerprint, evaluate_route_golden,
)
from .store import ProviderFabricStore


ACCEPTANCE_PHRASE = "I_APPROVE_BOTH_AUTOIP_OUTPUTS"
REQUIRED_GATES = ("format", "rights_consent", "visual_class", "identity", "outfit")


class AutoIPGoldenIssueError(ValueError):
    pass


def _sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lineage(store: ProviderFabricStore, owner: str, projection_id: str) -> dict:
    try:
        asset = store.get_owned_remote_asset(owner, projection_id)
    except KeyError as error:
        raise AutoIPGoldenIssueError("autoip_golden_output_not_owned") from error
    metadata = asset.get("metadata")
    versions = asset.get("versions")
    if (
        asset.get("account_id") is None or asset.get("visual_class") not in {"anime", "realistic"}
        or not isinstance(metadata, Mapping) or metadata.get("source") != "autoip_generation"
        or not isinstance(metadata.get("task_id"), str) or not isinstance(metadata.get("intent_id"), str)
        or not isinstance(versions, list) or len(versions) != 1
    ):
        raise AutoIPGoldenIssueError("autoip_golden_output_lineage_invalid")
    version = versions[0]
    if (
        version.get("source_available") is not True
        or not isinstance(version.get("sha256"), str)
        or not isinstance(version.get("byte_size"), int) or version["byte_size"] <= 0
        or not isinstance(version.get("mime_type"), str)
    ):
        raise AutoIPGoldenIssueError("autoip_golden_output_not_adopted")
    try:
        task = store.get_owned_task(owner, metadata["task_id"])
        outbox = store.get_outbox(task["outbox_id"])
        decision = store.get_route_decision(outbox["decision_id"])
        intent = store.get_intent(outbox["intent_id"])
    except KeyError as error:
        raise AutoIPGoldenIssueError("autoip_golden_task_lineage_missing") from error
    if any((
        task.get("provider_id") != "autoip", task.get("state") != "completed",
        task.get("account_id") != asset.get("account_id"),
        outbox.get("account_id") != asset.get("account_id"),
        outbox.get("intent_id") != metadata.get("intent_id"),
        decision.get("intent_id") != metadata.get("intent_id"),
        decision.get("selected_provider_id") != "autoip",
        intent.get("intent_id") != metadata.get("intent_id"),
        intent.get("visual_class") != asset.get("visual_class"),
    )):
        raise AutoIPGoldenIssueError("autoip_golden_task_lineage_mismatch")
    intent_body = intent.get("intent")
    assets = intent_body.get("assets") if isinstance(intent_body, Mapping) else None
    inputs = tuple(sorted(
        item["sha256"] for item in (assets or [])
        if isinstance(item, Mapping) and isinstance(item.get("sha256"), str)
    ))
    if not inputs:
        # Text-only routes still bind their immutable prompt/payload identity.
        inputs = (_sha({"intent": intent_body, "payload": intent.get("payload")}),)
    return {
        "asset": asset, "version": version, "task": task, "decision": decision,
        "intent": intent, "input_sha256s": inputs,
    }


def issue_autoip_golden(
    store: ProviderFabricStore,
    golden_store: AutoIPGoldenEvidenceStore,
    *, owner_user_id: str, account_id: str, compatibility_receipt: Mapping[str, object],
    projection_ids: Sequence[str], gate_evidence: Mapping[str, Sequence[str]],
    review_evidence_ids: Sequence[str], acceptance_phrase: str,
    reviewer_id: str = "operator",
) -> dict[str, object]:
    """Validate two adopted outputs and persist one exact activatable cell."""

    if acceptance_phrase != ACCEPTANCE_PHRASE:
        raise AutoIPGoldenIssueError("autoip_golden_explicit_approval_missing")
    if len(projection_ids) != 2 or len(set(projection_ids)) != 2 or len(review_evidence_ids) != 2:
        raise AutoIPGoldenIssueError("autoip_golden_two_outputs_required")
    if set(gate_evidence) != set(REQUIRED_GATES) or any(
        not isinstance(values, Sequence) or isinstance(values, (str, bytes))
        or len(values) != 2 or any(not isinstance(item, str) for item in values)
        for values in gate_evidence.values()
    ):
        raise AutoIPGoldenIssueError("autoip_golden_gate_evidence_invalid")
    probe_id = compatibility_receipt.get("receipt_id")
    if compatibility_receipt.get("current") is not True or not isinstance(probe_id, str):
        raise AutoIPGoldenIssueError("autoip_golden_compatibility_not_current")
    lines = [_lineage(store, owner_user_id, item) for item in projection_ids]
    first = lines[0]
    variant_id = first["decision"]["selected_variant_id"]
    if any(
        line["asset"]["account_id"] != account_id
        or line["decision"]["selected_variant_id"] != variant_id
        or line["intent"]["capability"] != first["intent"]["capability"]
        or line["intent"]["visual_class"] != first["intent"]["visual_class"]
        or line["input_sha256s"] != first["input_sha256s"]
        for line in lines
    ):
        raise AutoIPGoldenIssueError("autoip_golden_repeats_not_equivalent")
    profiles = {item.variant_id: item for item in candidate_autoip_profiles()}
    profile = profiles.get(variant_id)
    if profile is None or profile.capability.value != first["intent"]["capability"] or profile.visual_class.value != first["intent"]["visual_class"]:
        raise AutoIPGoldenIssueError("autoip_golden_profile_scope_invalid")
    case = GoldenCase(
        case_id="autoip-case:" + _sha({"variant": variant_id, "inputs": first["input_sha256s"]})[:32],
        capability=CapabilityKind(first["intent"]["capability"]),
        visual_class=VisualClass(first["intent"]["visual_class"]),
        semantics_id=variant_id, input_sha256s=first["input_sha256s"],
    )
    runtime = RuntimeFingerprint(
        workflow_sha256=_sha({"contract": CONTRACT_ID, "variant": variant_id}),
        model_sha256s=(_sha({"provider_contract": CONTRACT_ID}),),
        engine_revision=CONTRACT_ID,
        variant_revision=PROFILE_REVISION,
        attention_backend="provider", acceleration_profile="provider",
        settings_sha256=_sha({"probe": probe_id, "variant": variant_id}),
    )
    receipts = tuple(GoldenReceipt(
        receipt_id="autoip-golden-run:" + _sha({"task": line["task"]["task_id"], "output": line["version"]["sha256"]})[:32],
        case_id=case.case_id, route_variant_id=variant_id, origin=RouteOrigin.EXTERNAL,
        runtime=runtime, output_sha256=line["version"]["sha256"], repeat_index=index,
        gate_results=tuple(GateResult(gate, GateVerdict.PASS, (gate_evidence[gate][index - 1],)) for gate in REQUIRED_GATES),
        human_review=HumanReview(reviewer_id, HumanVerdict.APPROVE, True, review_evidence_ids[index - 1]),
        duration_seconds=max(float(line["version"].get("duration_seconds") or 0.001), 0.001),
    ) for index, line in enumerate(lines, 1))
    verdict = evaluate_route_golden(case, variant_id, RouteOrigin.EXTERNAL, receipts)
    if not verdict.activatable:
        raise AutoIPGoldenIssueError("autoip_golden_quality_contract_failed")
    persisted = golden_store.record(owner_user_id, account_id, probe_id, (case,), receipts)
    return {**persisted, **verdict.to_dict(), "variant_id": variant_id, "visual_class": profile.visual_class.value, "capability": profile.capability.value}


def autoip_golden_candidates(
    store: ProviderFabricStore, *, owner_user_id: str, account_id: str,
) -> tuple[dict[str, object], ...]:
    """Project bounded adopted outputs grouped by exact repeat-equivalent cell."""

    page = store.list_owned_remote_assets(
        owner_user_id, account_id=account_id, provider_id="autoip", limit=100,
    )
    rows = page.get("items") if isinstance(page, Mapping) else None
    result = []
    for asset in rows if isinstance(rows, list) else ():
        projection_id = asset.get("projection_id") if isinstance(asset, Mapping) else None
        if not isinstance(projection_id, str):
            continue
        try:
            line = _lineage(store, owner_user_id, projection_id)
        except AutoIPGoldenIssueError:
            continue
        decision, intent = line["decision"], line["intent"]
        cell = {
            "account_id": account_id,
            "variant_id": decision["selected_variant_id"],
            "capability": intent["capability"],
            "visual_class": intent["visual_class"],
            "input_sha256s": list(line["input_sha256s"]),
        }
        result.append({
            "projection_id": projection_id,
            "media_kind": line["asset"]["media_kind"],
            "visual_class": intent["visual_class"],
            "character_ids": list(line["asset"].get("character_ids") or ()),
            "variant_id": decision["selected_variant_id"],
            "capability": intent["capability"],
            "cell_id": "autoip-cell:" + _sha(cell)[:32],
            "preview_url": f"/api/assets/{projection_id}/preview",
        })
    return tuple(sorted(result, key=lambda item: (item["cell_id"], item["projection_id"])))


__all__ = ["ACCEPTANCE_PHRASE", "AutoIPGoldenIssueError", "autoip_golden_candidates", "issue_autoip_golden"]
