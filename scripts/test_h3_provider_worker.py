#!/usr/bin/env python3
"""Exact-class tests for confirmed H3 graph preparation."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from minimax_h3_product import ANIME_GUARDRAIL, REALISTIC_GUARDRAIL  # noqa: E402
from provider_fabric.h3_catalog import H3_VARIANT_CONTRACTS  # noqa: E402
from provider_fabric.h3_profiles import h3_character_profiles  # noqa: E402
from provider_fabric.h3_worker import (  # noqa: E402
    H3QueueRequest,
    H3SubmissionProcessor,
    h3_upload_basename,
    profile_binding,
)
from provider_fabric.models import (  # noqa: E402
    CapabilityKind,
    CharacterRef,
    MediaAsset,
    MediaKind,
    RouteDecision,
    RouteIntent,
    TaskReceipt,
    TaskState,
    VisualClass,
)
from provider_fabric.provider_worker import DefiniteLocalFailure  # noqa: E402


def creative_instruction() -> dict:
    material = {
        "schema_version": "castle.creative-instruction.v1",
        "shot_id": "shot-001",
        "frame_intent": "Hero stands beside a canvas in a garden.",
        "observable_action": "Hero lifts a brush and paints one flower.",
        "camera": "Stable medium shot, slow push-in.",
        "audio_intent": "Soft birds and one short greeting.",
        "language": "zh-TW",
    }
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {**material, "content_hash": hashlib.sha256(raw).hexdigest()}


class H3ProviderWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "avatar.png"
        self.path.write_bytes(b"h3-avatar")
        self.observed = []

    def profile(self, key):
        variant_id = key.removeprefix("local_comfy:")
        contract_id = H3_VARIANT_CONTRACTS[variant_id]
        return {
            item.key: item for item in h3_character_profiles({contract_id})
        }[key]

    def bundle(self, visual=VisualClass.ANIME):
        variant = (
            "image-video.anime.h3-fl2va.v1"
            if visual is VisualClass.ANIME
            else "image-video.realistic.h3-fl2va.v1"
        )
        character = CharacterRef("castle:hero", visual, "Hero")
        asset = MediaAsset(
            "castle-character:castle:hero:avatar",
            MediaKind.IMAGE,
            "castle://character-assets/hero/avatar",
            visual,
            (character.character_id,),
            "image/png",
            self.path.stat().st_size,
            hashlib.sha256(self.path.read_bytes()).hexdigest(),
        )
        intent = RouteIntent(
            CapabilityKind.IMAGE_TO_VIDEO,
            visual,
            (character,),
            (asset,),
            frozenset({"aspect", "motion_profile", "quality"}),
            ("local_comfy",),
        )
        decision = RouteDecision(
            "local_comfy", variant, (f"local_comfy:{variant}",), (), "router-v1"
        )
        profile = self.profile(f"local_comfy:{variant}")
        return {
            "owner_user_id": "member-one",
            "intent": {
                "intent": intent.to_dict(),
                "payload": {
                    "options": {
                        "aspect": "portrait",
                        "motion_profile": "gentle" if visual is VisualClass.ANIME else "natural",
                        "quality": "quality",
                    },
                    "creative_instruction": creative_instruction(),
                    "capability_grant": profile_binding(profile),
                },
            },
            "decision": decision.to_dict(),
            "task": {"task_id": "task-h3-001", "client_request_id": "request-h3-001"},
        }

    def executor(self, request):
        self.observed.append(request)
        return TaskReceipt(
            "local_comfy",
            request.client_request_id,
            TaskState.SUBMITTED,
            external_task_id="comfy-prompt-001",
        )

    def processor(self):
        return H3SubmissionProcessor(
            lambda owner, asset: self.path,
            self.executor,
            profile_resolver=self.profile,
        )

    def test_anime_and_realistic_compile_distinct_guardrails(self):
        for visual, expected, forbidden in (
            (VisualClass.ANIME, ANIME_GUARDRAIL, REALISTIC_GUARDRAIL),
            (VisualClass.REALISTIC, REALISTIC_GUARDRAIL, ANIME_GUARDRAIL),
        ):
            processor = self.processor()
            processor(self.bundle(visual))
            request = self.observed[-1]
            self.assertIsInstance(request, H3QueueRequest)
            encoded = json.dumps(request.graph, ensure_ascii=False)
            self.assertIn(expected, encoded)
            self.assertNotIn(forbidden, encoded)
            self.assertRegex(request.graph_sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(
                request.upload_basename,
                h3_upload_basename(processor._upload_name_key, request.source_sha256, ".png"),
            )
            self.assertIn(request.upload_basename, encoded)

    def test_changed_avatar_never_reaches_gpu_executor(self):
        bundle = self.bundle()
        self.path.write_bytes(b"changed")
        with self.assertRaisesRegex(DefiniteLocalFailure, "input bytes changed"):
            self.processor()(bundle)
        self.assertEqual(self.observed, [])

    def test_missing_technical_readiness_never_reaches_gpu_executor(self):
        processor = H3SubmissionProcessor(
            lambda owner, asset: self.path,
            self.executor,
            profile_resolver=lambda key: {item.key: item for item in h3_character_profiles()}[key],
        )
        with self.assertRaisesRegex(DefiniteLocalFailure, "not technically"):
            processor(self.bundle())
        self.assertEqual(self.observed, [])

    def test_changed_technical_binding_never_reaches_gpu_executor(self):
        bundle = self.bundle()
        bundle["intent"]["payload"]["capability_grant"][
            "technical_evidence_id"
        ] = "h3-runtime:other-contract"
        with self.assertRaisesRegex(DefiniteLocalFailure, "evidence changed"):
            self.processor()(bundle)
        self.assertEqual(self.observed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
