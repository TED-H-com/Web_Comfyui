#!/usr/bin/env python3
"""Exact-class safety tests for inert AutoIP option profiles."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT / "scripts")]

from provider_fabric.autoip_profiles import (  # noqa: E402
    AUTOIP_PROFILE_BY_KEY,
    candidate_autoip_profiles,
    profile_contract_summary,
    qualified_autoip_profiles,
)
from provider_fabric.models import CapabilityKind, VisualClass  # noqa: E402
from provider_fabric.quality import GoldenCase, RouteOrigin  # noqa: E402
from test_provider_quality import receipt  # noqa: E402
from provider_fabric.variant_profiles import VariantProfileError  # noqa: E402
from provider_fabric.variant_profiles import (  # noqa: E402
    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
)


def compatibility(*operations: str, current: bool = True):
    return {
        "schema_version": "castle.autoip-compatibility-receipt.v1",
        "contract_id": "@autoip/mcp@0.4.0",
        "receipt_id": "autoip-probe:" + "a" * 32,
        "supported_operations": list(operations),
        "persisted": True,
        "current": current,
    }


class AutoIPCandidateProfileTests(unittest.TestCase):
    def test_every_supported_operation_has_separate_anime_and_realistic_profiles(self):
        profiles = candidate_autoip_profiles()
        self.assertEqual(len(profiles), 14)
        pairs = {(item.capability.value, item.visual_class.value) for item in profiles}
        self.assertEqual(len(pairs), 14)
        self.assertEqual(
            {visual_class for _operation, visual_class in pairs},
            {"anime", "realistic"},
        )
        self.assertEqual(set(AUTOIP_PROFILE_BY_KEY), {item.key for item in profiles})

    def test_profiles_are_inert_until_each_exact_cell_has_all_evidence(self):
        for profile in candidate_autoip_profiles():
            with self.subTest(profile=profile.key):
                self.assertEqual(
                    profile.execution_basis,
                    EXECUTION_BASIS_TECHNICAL_COMPATIBILITY,
                )
                self.assertFalse(profile.execution_ready)
                self.assertFalse(profile.release_ready)
                self.assertFalse(profile.runtime_ready)
                self.assertFalse(profile.golden_current)
                self.assertIsNone(profile.golden_id)
                self.assertIsNone(profile.technical_evidence_id)

    def test_anime_text_mode_cannot_leak_into_realistic_profile(self):
        profiles = candidate_autoip_profiles()
        anime = next(item for item in profiles if item.capability.value == "text_to_image" and item.visual_class is VisualClass.ANIME)
        realistic = next(item for item in profiles if item.capability.value == "text_to_image" and item.visual_class is VisualClass.REALISTIC)
        self.assertEqual(anime.option_values["mode"], ("anime",))
        self.assertNotIn("anime", realistic.option_values["mode"])

    def test_unknown_and_cross_class_mutation_fail_at_profile_boundary(self):
        profile = candidate_autoip_profiles()[0]
        with self.assertRaises(VariantProfileError):
            replace(profile, visual_class=VisualClass.UNKNOWN)

    def test_multi_subject_has_exact_one_to_four_identity_cardinality(self):
        summary = profile_contract_summary()
        self.assertFalse(summary["activation_ready"])
        self.assertEqual(summary["excluded_operations"], [])
        profile = next(
            item for item in candidate_autoip_profiles()
            if item.capability.value == "multi_subject_video"
            and item.visual_class is VisualClass.ANIME
        )
        subjects = next(item for item in profile.input_slots if item.slot_id == "subjects")
        self.assertEqual((subjects.minimum_count, subjects.maximum_count), (1, 4))
        self.assertTrue(subjects.identity_bearing)

    def test_current_receipt_enables_supported_operation_without_a_golden(self):
        profiles = qualified_autoip_profiles(
            compatibility("image_to_video"), (), (),
        )
        enabled = [item for item in profiles if item.execution_ready]
        self.assertEqual(len(enabled), 2)
        self.assertEqual(
            {(item.capability.value, item.visual_class.value) for item in enabled},
            {("image_to_video", "anime"), ("image_to_video", "realistic")},
        )
        self.assertTrue(all(item.runtime_ready for item in enabled))
        self.assertTrue(all(not item.release_ready for item in enabled))
        self.assertTrue(all(not item.golden_current for item in enabled))
        self.assertTrue(all(
            item.technical_evidence_id == "autoip-probe:" + "a" * 32
            for item in enabled
        ))

    def test_exact_repeat_golden_is_optional_quality_metadata(self):
        target = next(item for item in candidate_autoip_profiles() if (
            item.capability is CapabilityKind.IMAGE_TO_VIDEO
            and item.visual_class is VisualClass.ANIME
        ))
        case = GoldenCase(
            case_id="autoip-anime-i2v-v1", capability=target.capability,
            visual_class=target.visual_class, semantics_id=target.variant_id,
            input_sha256s=("e" * 64,),
        )
        receipts = (
            receipt("provider-1", target.variant_id, RouteOrigin.EXTERNAL, 1, "3"),
            receipt("provider-2", target.variant_id, RouteOrigin.EXTERNAL, 2, "4"),
        )
        # Rebind the generic fixture receipts to this exact case.
        receipts = tuple(replace(item, case_id=case.case_id) for item in receipts)
        profiles = qualified_autoip_profiles(
            compatibility("image_to_video"), (case,), receipts,
        )
        decorated = [item for item in profiles if item.golden_current]
        self.assertEqual([item.key for item in decorated], [target.key])
        self.assertTrue(decorated[0].execution_ready)
        self.assertFalse(decorated[0].release_ready)
        self.assertRegex(decorated[0].golden_id, r"^golden:autoip:[0-9a-f]{32}$")
        self.assertEqual(
            decorated[0].execution_evidence_id,
            "autoip-probe:" + "a" * 32,
        )

    def test_stale_probe_or_one_repeat_never_activates(self):
        target = candidate_autoip_profiles()[0]
        case = GoldenCase(
            case_id="autoip-one-repeat", capability=target.capability,
            visual_class=target.visual_class, semantics_id=target.variant_id,
            input_sha256s=("e" * 64,),
        )
        one = replace(
            receipt("provider-1", target.variant_id, RouteOrigin.EXTERNAL, 1, "3"),
            case_id=case.case_id,
        )
        for current, receipts in ((False, (one,)), (True, (one,))):
            profiles = qualified_autoip_profiles(
                compatibility(target.capability.value, current=current),
                (case,), receipts,
            )
            self.assertFalse(any(item.golden_current for item in profiles))
            self.assertEqual(
                any(item.execution_ready for item in profiles),
                current,
            )

    def test_missing_receipt_identity_or_operation_scope_never_enables(self):
        valid = compatibility("image_to_video")
        cases = (
            {key: value for key, value in valid.items() if key != "receipt_id"},
            {**valid, "supported_operations": []},
            {**valid, "supported_operations": ["image_to_video", "image_to_video"]},
            {**valid, "supported_operations": ["image_to_video", "unknown"]},
            {**valid, "receipt_id": "not-a-probe"},
        )
        for receipt_value in cases:
            with self.subTest(receipt=receipt_value):
                self.assertFalse(any(
                    item.execution_ready
                    for item in qualified_autoip_profiles(receipt_value, (), ())
                ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
