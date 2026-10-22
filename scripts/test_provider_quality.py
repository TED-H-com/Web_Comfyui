#!/usr/bin/env python3
"""Offline tests for narrow, evidence-backed provider parity claims."""

from __future__ import annotations

import dataclasses
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from provider_fabric.models import CapabilityKind, VisualClass  # noqa: E402
from provider_fabric.quality import (  # noqa: E402
    GateResult,
    GateVerdict,
    GoldenCase,
    GoldenReceipt,
    HumanReview,
    HumanVerdict,
    RouteOrigin,
    RuntimeFingerprint,
    evaluate_provider_parity,
    evaluate_route_golden,
)


def runtime(seed: str = "a") -> RuntimeFingerprint:
    return RuntimeFingerprint(
        workflow_sha256=seed * 64,
        model_sha256s=("b" * 64, "c" * 64),
        engine_revision="comfyui-v0.32.0",
        variant_revision="anime.i2v.quality.v1",
        attention_backend="stock",
        acceleration_profile="quality",
        settings_sha256="d" * 64,
    )


def golden_case() -> GoldenCase:
    return GoldenCase(
        case_id="anime-hero-i2v",
        capability=CapabilityKind.IMAGE_TO_VIDEO,
        visual_class=VisualClass.ANIME,
        semantics_id="portrait-wave-5s",
        input_sha256s=("e" * 64, "f" * 64),
    )


def gates(*, identity=GateVerdict.PASS) -> tuple[GateResult, ...]:
    values = []
    for gate in ("format", "rights_consent", "visual_class", "identity", "outfit"):
        verdict = identity if gate == "identity" else GateVerdict.PASS
        values.append(GateResult(gate, verdict, (f"evidence-{gate}",)))
    return tuple(values)


def receipt(
    receipt_id: str,
    variant: str,
    origin: RouteOrigin,
    repeat: int,
    output: str,
    *,
    runtime_value: RuntimeFingerprint | None = None,
    identity=GateVerdict.PASS,
    blind=True,
    human=HumanVerdict.APPROVE,
) -> GoldenReceipt:
    return GoldenReceipt(
        receipt_id=receipt_id,
        case_id="anime-hero-i2v",
        route_variant_id=variant,
        origin=origin,
        runtime=runtime_value or runtime(),
        output_sha256=output * 64,
        repeat_index=repeat,
        gate_results=gates(identity=identity),
        human_review=HumanReview("operator", human, blind, f"review-{receipt_id}"),
        duration_seconds=42.5,
        peak_vram_bytes=24_000_000_000,
    )


class ProviderQualityTests(unittest.TestCase):
    def accepted_receipts(self):
        return [
            receipt("local-1", "local-anime-i2v", RouteOrigin.LOCAL, 1, "1"),
            receipt("local-2", "local-anime-i2v", RouteOrigin.LOCAL, 2, "2"),
            receipt("provider-1", "autoip-anime-i2v", RouteOrigin.EXTERNAL, 1, "3"),
        ]

    def test_two_local_repeats_and_blind_provider_reference_can_claim(self):
        verdict = evaluate_provider_parity(
            golden_case(),
            "local-anime-i2v",
            "autoip-anime-i2v",
            self.accepted_receipts(),
            current_local_runtime=runtime(),
        )
        self.assertTrue(verdict.claimable, verdict.to_dict())
        self.assertEqual(verdict.reason_codes, ())
        self.assertEqual(verdict.local_receipt_ids, ("local-1", "local-2"))

    def test_one_lucky_local_output_never_claims_parity(self):
        values = self.accepted_receipts()
        values.pop(1)
        verdict = evaluate_provider_parity(
            golden_case(), "local-anime-i2v", "autoip-anime-i2v", values,
            current_local_runtime=runtime(),
        )
        self.assertFalse(verdict.claimable)
        self.assertIn("local_repeats_missing", verdict.reason_codes)

    def test_hard_gate_warn_or_human_pending_blocks(self):
        values = self.accepted_receipts()
        values[0] = receipt(
            "local-1", "local-anime-i2v", RouteOrigin.LOCAL, 1, "1",
            identity=GateVerdict.WARN,
        )
        values[2] = receipt(
            "provider-1", "autoip-anime-i2v", RouteOrigin.EXTERNAL, 1, "3",
            human=HumanVerdict.PENDING,
        )
        verdict = evaluate_provider_parity(
            golden_case(), "local-anime-i2v", "autoip-anime-i2v", values,
            current_local_runtime=runtime(),
        )
        self.assertIn("required_gate_not_passed", verdict.reason_codes)
        self.assertIn("human_approval_missing", verdict.reason_codes)

    def test_runtime_attention_or_settings_change_invalidates_receipts(self):
        changed = dataclasses.replace(runtime(), attention_backend="sageattention")
        verdict = evaluate_provider_parity(
            golden_case(), "local-anime-i2v", "autoip-anime-i2v",
            self.accepted_receipts(), current_local_runtime=changed,
        )
        self.assertFalse(verdict.claimable)
        self.assertIn("local_runtime_changed", verdict.reason_codes)

    def test_duplicate_output_and_nonblind_review_block(self):
        values = self.accepted_receipts()
        values[1] = receipt(
            "local-2", "local-anime-i2v", RouteOrigin.LOCAL, 2, "1", blind=False,
        )
        verdict = evaluate_provider_parity(
            golden_case(), "local-anime-i2v", "autoip-anime-i2v", values,
            current_local_runtime=runtime(),
        )
        self.assertIn("local_repeat_outputs_not_distinct", verdict.reason_codes)
        self.assertIn("blind_review_missing", verdict.reason_codes)

    def test_unknown_class_and_missing_hard_gate_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            dataclasses.replace(golden_case(), visual_class=VisualClass.UNKNOWN)
        with self.assertRaisesRegex(ValueError, "hard gate"):
            dataclasses.replace(golden_case(), required_gates=frozenset({"format"}))

    def test_receipts_are_inert_deterministic_public_evidence(self):
        item = self.accepted_receipts()[0]
        rendered = json.dumps(item.to_dict(), sort_keys=True)
        self.assertNotIn("prompt", rendered)
        self.assertNotIn("url", rendered)
        self.assertEqual(item.runtime.digest, runtime().digest)

    def test_exact_external_route_requires_two_approved_distinct_repeats(self):
        values = [
            receipt("provider-1", "autoip-anime-i2v", RouteOrigin.EXTERNAL, 1, "3"),
            receipt("provider-2", "autoip-anime-i2v", RouteOrigin.EXTERNAL, 2, "4"),
        ]
        verdict = evaluate_route_golden(
            golden_case(), "autoip-anime-i2v", RouteOrigin.EXTERNAL, values,
        )
        self.assertTrue(verdict.activatable)
        self.assertRegex(verdict.golden_id, r"^golden:autoip:[0-9a-f]{32}$")
        other = evaluate_route_golden(
            golden_case(), "autoip-real-i2v", RouteOrigin.EXTERNAL, values,
        )
        self.assertFalse(other.activatable)
        self.assertIn("route_repeats_missing", other.reason_codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
