#!/usr/bin/env python3
"""Focused tests for rebuilding confirmed AutoIP work exactly once."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from provider_fabric.autoip import AutoIPApiError, AutoIPFile  # noqa: E402
from provider_fabric.autoip_profiles import AUTOIP_PROFILE_BY_KEY  # noqa: E402
from provider_fabric.autoip_worker import AutoIPSubmissionProcessor  # noqa: E402
from provider_fabric.models import (  # noqa: E402
    CapabilityKind,
    CharacterRef,
    CostQuote,
    MediaAsset,
    MediaKind,
    RouteDecision,
    RouteIntent,
    TaskReceipt,
    TaskState,
    VisualClass,
)
from provider_fabric.provider_worker import (  # noqa: E402
    DefiniteLocalFailure,
    DefiniteProviderRejection,
)


def instruction(**extras) -> dict:
    material = {
        "schema_version": "castle.creative-instruction.v1",
        "shot_id": "shot-001",
        "frame_intent": "Hero stands in a quiet garden.",
        "observable_action": "Hero turns toward camera and waves once.",
        "camera": "Stable medium shot with a slow push-in.",
        "audio_intent": "No dialogue; soft garden ambience.",
        "language": "zh-TW",
        **extras,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**material, "content_hash": hashlib.sha256(encoded).hexdigest()}


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, intent, decision, job, *, client_request_id, quote):
        self.calls.append((intent, decision, job, client_request_id, quote))
        return TaskReceipt(
            provider_id="autoip",
            client_request_id=client_request_id,
            state=TaskState.SUBMITTED,
            external_task_id="autoip-task-001",
        )


class RejectingClient(FakeClient):
    def __init__(self, status: int, *, retry_after: str | None = None) -> None:
        super().__init__()
        self.status = status
        self.retry_after = retry_after

    def submit(self, intent, decision, job, *, client_request_id, quote):
        self.calls.append((intent, decision, job, client_request_id, quote))
        raise AutoIPApiError(
            self.status,
            "rate_limited" if self.status == 429 else "provider_error",
            "provider response content must remain redacted",
            retry_after=self.retry_after,
        )


class AutoIPWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "hero.png"
        self.path.write_bytes(b"immutable-test-image")
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.asset = MediaAsset(
            asset_id="castle-character:hero:avatar",
            kind=MediaKind.IMAGE,
            uri="castle://character-assets/hero/avatar",
            visual_class=VisualClass.ANIME,
            character_ids=("hero",),
            mime_type="image/png",
            byte_size=self.path.stat().st_size,
            sha256=self.digest,
        )
        self.intent = RouteIntent(
            capability=CapabilityKind.IMAGE_TO_VIDEO,
            visual_class=VisualClass.ANIME,
            characters=(CharacterRef("hero", VisualClass.ANIME, "Hero"),),
            assets=(self.asset,),
            required_options=frozenset({"duration_seconds", "resolution", "aspect_ratio"}),
            preferred_providers=("autoip",),
        )
        self.variant_id = "image_to_video.anime.autoip-0.4.0.v1"
        self.decision = RouteDecision(
            selected_provider_id="autoip",
            selected_variant_id=self.variant_id,
            candidate_keys=(f"autoip:{self.variant_id}",),
            rejections=(),
            policy_version="router-v1",
        )
        self.quote = CostQuote(
            provider_id="autoip",
            capability=CapabilityKind.IMAGE_TO_VIDEO,
            credits=7,
            remaining_credits=100,
            authoritative=True,
            quote_id="quote-001",
            account_id="autoip-main",
        )
        self.client = FakeClient()

    def profile(self, owner_user_id: str, account_id: str, key: str):
        self.assertEqual(owner_user_id, "member-one")
        self.assertEqual(account_id, "autoip-main")
        profile = AUTOIP_PROFILE_BY_KEY[key]
        return replace(
            profile,
            runtime_ready=True,
            technical_evidence_id="autoip-probe:" + "a" * 32,
        )

    def bundle(self, *, intent: RouteIntent | None = None) -> dict:
        selected = intent or self.intent
        profile = self.profile("member-one", "autoip-main", f"autoip:{self.variant_id}")
        identity_bound = bool(selected.characters) or any(
            asset.character_ids for asset in selected.assets
        )
        return {
            "owner_user_id": "member-one",
            "intent": {
                "intent": selected.to_dict(),
                "payload": {
                    "options": {
                        "duration_seconds": 5,
                        "resolution": "480p",
                        "aspect_ratio": "9:16",
                    },
                    "spend_guard": {
                        "max_credits": None,
                        "quoted_credits": 7,
                        "currency": "provider_credits",
                    },
                    "capability_grant": ({
                        "action_id": profile.action_id,
                        "feature_id": profile.feature_id,
                        "profile_key": profile.key,
                        "profile_revision": profile.profile_revision,
                        "technical_evidence_id": profile.technical_evidence_id,
                    } if identity_bound else None),
                    "creative_instruction": instruction(),
                },
            },
            "decision": {
                "selected_provider_id": "autoip",
                "selected_variant_id": self.variant_id,
                "selected_account_id": "autoip-main",
                "candidate_keys": [f"autoip:{self.variant_id}"],
                "rejections": [],
                "policy_version": "router-v1",
                "quote": self.quote.to_dict(),
            },
            "task": {"client_request_id": "request-001"},
        }

    def processor(self, *, loader=None):
        return AutoIPSubmissionProcessor(
            lambda owner, account: self.client,
            loader or (lambda owner, asset, field: AutoIPFile(
                field, self.path, asset.visual_class, asset.mime_type
            )),
            profile_resolver=self.profile,
            quote_verifier=lambda owner, account, intent, decision, options: self.quote,
        )

    def test_rebuilds_exact_prompt_options_input_and_quote(self):
        receipt = self.processor()(self.bundle())
        self.assertEqual(receipt.external_task_id, "autoip-task-001")
        self.assertEqual(len(self.client.calls), 1)
        intent, decision, job, request_id, quote = self.client.calls[0]
        self.assertEqual(intent.to_dict(), self.intent.to_dict())
        self.assertEqual(decision.to_dict(), self.decision.to_dict())
        self.assertEqual(request_id, "request-001")
        self.assertEqual(quote.to_dict(), self.quote.to_dict())
        self.assertEqual(job.files[0].field_name, "first_frame")
        self.assertEqual(job.fields["duration_seconds"], 5)
        self.assertIn("action: Hero turns toward camera and waves once.", job.fields["prompt"])
        self.assertIsNone(job.max_credits)

    def test_all_seven_routes_build_executable_jobs_with_optional_fields(self):
        paths: dict[str, Path] = {}

        def asset(
            name: str,
            kind: MediaKind,
            *,
            character_ids=(),
            mime_type: str,
            visual_class: VisualClass | None = None,
        ) -> MediaAsset:
            path = Path(self.temporary.name) / f"{name}.bin"
            path.write_bytes(f"immutable-{name}".encode("ascii"))
            item = MediaAsset(
                asset_id=f"asset:{name}",
                kind=kind,
                uri=f"castle://assets/{name}",
                visual_class=visual_class,
                character_ids=tuple(character_ids),
                mime_type=mime_type,
                byte_size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            paths[item.asset_id] = path
            return item

        hero = CharacterRef("hero", VisualClass.ANIME, "Hero")
        hero_two = CharacterRef("hero-two", VisualClass.ANIME, "Hero Two")
        hero_image = asset(
            "hero-image", MediaKind.IMAGE, character_ids=("hero",),
            mime_type="image/png", visual_class=VisualClass.ANIME,
        )
        hero_two_image = asset(
            "hero-two-image", MediaKind.IMAGE, character_ids=("hero-two",),
            mime_type="image/png", visual_class=VisualClass.ANIME,
        )
        audio = asset("voice", MediaKind.AUDIO, mime_type="audio/wav")
        motion = asset("motion", MediaKind.VIDEO, mime_type="video/mp4")
        cases = (
            (
                CapabilityKind.TEXT_TO_IMAGE,
                (), (), {"mode": "anime", "size": "1024x1024"},
                {"negative_prompt": "identity drift"},
            ),
            (
                CapabilityKind.IMAGE_TO_IMAGE,
                (hero,), (hero_image,),
                {"mode": "hd_4k", "size": "original", "strength": 0.75},
                {"negative_prompt": "identity drift"},
            ),
            (
                CapabilityKind.IMAGE_TO_VIDEO,
                (hero,), (hero_image,),
                {
                    "duration_seconds": 5, "resolution": "480p",
                    "aspect_ratio": "9:16", "seed": "random",
                },
                {"seed": "random"},
            ),
            (
                CapabilityKind.TEXT_TO_VIDEO,
                (), (),
                {
                    "duration_seconds": 5, "resolution": "480p",
                    "aspect_ratio": "9:16", "seed": "random",
                },
                {"seed": "random"},
            ),
            (
                CapabilityKind.LIP_SYNC,
                (hero,), (hero_image, audio),
                {"aspect_ratio": "9:16"},
                {},
            ),
            (
                CapabilityKind.DANCE,
                (hero,), (hero_image, motion),
                {"motion_source": "dance_video"},
                {},
            ),
            (
                CapabilityKind.MULTI_SUBJECT_VIDEO,
                (hero, hero_two), (hero_image, hero_two_image, audio),
                {
                    "duration_seconds": 5, "resolution": "480p",
                    "aspect_ratio": "9:16", "seed": "random",
                    "native_audio": True,
                },
                {
                    "subject_descriptions": ["Hero", "Hero Two"],
                    "dialogue_text": "我們開始吧。",
                    "negative_prompt": "identity drift, extra people",
                    "seed": "random",
                },
            ),
        )

        for capability, characters, assets, options, extras in cases:
            with self.subTest(capability=capability.value):
                variant_id = f"{capability.value}.anime.autoip-0.4.0.v1"
                profile = replace(
                    AUTOIP_PROFILE_BY_KEY[f"autoip:{variant_id}"],
                    runtime_ready=True,
                    technical_evidence_id="autoip-probe:" + "e" * 32,
                )
                intent = RouteIntent(
                    capability=capability,
                    visual_class=VisualClass.ANIME,
                    characters=characters,
                    assets=assets,
                    required_options=frozenset(options),
                    preferred_providers=("autoip",),
                )
                decision = RouteDecision(
                    selected_provider_id="autoip",
                    selected_variant_id=variant_id,
                    candidate_keys=(profile.key,),
                    rejections=(),
                    policy_version="router-v1",
                )
                quote = CostQuote(
                    provider_id="autoip",
                    capability=capability,
                    credits=1,
                    remaining_credits=100,
                    authoritative=True,
                    quote_id=f"quote-{capability.value}",
                    account_id="autoip-main",
                )
                identity_bound = bool(characters) or any(
                    item.character_ids for item in assets
                )
                bundle = {
                    "owner_user_id": "member-one",
                    "intent": {
                        "intent": intent.to_dict(),
                        "payload": {
                            "options": options,
                            "spend_guard": {
                                "max_credits": None,
                                "quoted_credits": 1,
                                "currency": "provider_credits",
                            },
                            "capability_grant": ({
                                "action_id": profile.action_id,
                                "feature_id": profile.feature_id,
                                "profile_key": profile.key,
                                "profile_revision": profile.profile_revision,
                                "technical_evidence_id": profile.technical_evidence_id,
                            } if identity_bound else None),
                            "creative_instruction": instruction(**extras),
                        },
                    },
                    "decision": {
                        "selected_provider_id": "autoip",
                        "selected_variant_id": variant_id,
                        "selected_account_id": "autoip-main",
                        "candidate_keys": [profile.key],
                        "rejections": [],
                        "policy_version": "router-v1",
                        "quote": quote.to_dict(),
                    },
                    "task": {"client_request_id": f"request-{capability.value}"},
                }
                client = FakeClient()
                processor = AutoIPSubmissionProcessor(
                    lambda _owner, _account: client,
                    lambda _owner, selected, field: AutoIPFile(
                        field, paths[selected.asset_id], selected.visual_class,
                        selected.mime_type,
                    ),
                    profile_resolver=lambda _owner, _account, key, current=profile: (
                        current if key == current.key else None
                    ),
                    quote_verifier=lambda *_arguments, current=quote: current,
                )

                processor(bundle)

                job = client.calls[0][2]
                self.assertEqual(job.operation.value, capability.value)
                self.assertNotIn("seed", job.fields)
                self.assertNotIn("native_audio", job.fields)
                self.assertNotIn("motion_source", job.fields)
                if capability is CapabilityKind.IMAGE_TO_IMAGE:
                    self.assertEqual(job.fields["strength"], 0.75)
                    self.assertEqual(job.fields["negative_prompt"], "identity drift")
                if capability is CapabilityKind.LIP_SYNC:
                    self.assertEqual(job.fields["audio_start_time"], 0)
                    self.assertEqual(job.fields["audio_duration"], 5)
                if capability is CapabilityKind.DANCE:
                    self.assertEqual(
                        [item.field_name for item in job.files],
                        ["image", "dance_video"],
                    )
                if capability is CapabilityKind.MULTI_SUBJECT_VIDEO:
                    self.assertEqual(
                        job.fields["descriptions[]"], ("Hero", "Hero Two")
                    )
                    self.assertEqual(job.fields["dialogue_text"], "我們開始吧。")
                    self.assertEqual(
                        job.fields["negative_prompt"],
                        "identity drift, extra people",
                    )

    def test_rebuilds_three_confirmed_production_subjects_as_repeated_images(self):
        variant_id = "multi_subject_video.anime.autoip-0.4.0.v1"
        profile = replace(
            AUTOIP_PROFILE_BY_KEY[f"autoip:{variant_id}"],
            runtime_ready=True,
            technical_evidence_id="autoip-probe:" + "c" * 32,
        )
        characters = tuple(
            CharacterRef(f"hero-{ordinal}", VisualClass.ANIME, f"Hero {ordinal}")
            for ordinal in range(1, 4)
        )
        paths = {}
        assets = []
        for ordinal, character in enumerate(characters, start=1):
            path = Path(self.temporary.name) / f"hero-{ordinal}.png"
            path.write_bytes(f"immutable-subject-{ordinal}".encode("ascii"))
            asset = MediaAsset(
                asset_id="production-media:" + str(ordinal) * 64,
                kind=MediaKind.IMAGE,
                uri=f"castle://provider-assets/hero-{ordinal}/version-{ordinal}",
                visual_class=VisualClass.ANIME,
                character_ids=(character.character_id,),
                mime_type="image/png",
                byte_size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            paths[asset.asset_id] = path
            assets.append(asset)
        intent = RouteIntent(
            capability=CapabilityKind.MULTI_SUBJECT_VIDEO,
            visual_class=VisualClass.ANIME,
            characters=characters,
            assets=tuple(assets),
            required_options=frozenset({
                "duration_seconds", "resolution", "aspect_ratio",
            }),
            preferred_providers=("autoip",),
        )
        quote = CostQuote(
            provider_id="autoip",
            capability=CapabilityKind.MULTI_SUBJECT_VIDEO,
            credits=60,
            remaining_credits=16_485,
            authoritative=True,
            quote_id="quote-multi-subject-001",
            account_id="autoip-main",
        )
        production_instruction = instruction()
        production_instruction.pop("content_hash")
        production_instruction["production_binding"] = {
            "schema_version": "castle.production-binding.v1",
            "project_id": "project-multi-subject",
            "pack_hash": "a" * 64,
            "state_hash": "b" * 64,
            "shot_id": "shot-001",
            "route_variant_id": variant_id,
        }
        encoded = json.dumps(
            production_instruction,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        production_instruction["content_hash"] = hashlib.sha256(encoded).hexdigest()
        bundle = {
            "owner_user_id": "member-one",
            "intent": {
                "intent": intent.to_dict(),
                "payload": {
                    "options": {
                        "duration_seconds": 5,
                        "resolution": "768p",
                        "aspect_ratio": "9:16",
                    },
                    "spend_guard": {
                        "max_credits": None,
                        "quoted_credits": 60,
                        "currency": "provider_credits",
                    },
                    "capability_grant": {
                        "action_id": profile.action_id,
                        "feature_id": profile.feature_id,
                        "profile_key": profile.key,
                        "profile_revision": profile.profile_revision,
                        "technical_evidence_id": profile.technical_evidence_id,
                    },
                    "creative_instruction": production_instruction,
                },
            },
            "decision": {
                "selected_provider_id": "autoip",
                "selected_variant_id": variant_id,
                "selected_account_id": "autoip-main",
                "candidate_keys": [f"autoip:{variant_id}"],
                "rejections": [],
                "policy_version": "router-v1",
                "quote": quote.to_dict(),
            },
            "task": {"client_request_id": "request-multi-subject"},
        }
        processor = AutoIPSubmissionProcessor(
            lambda _owner, _account: self.client,
            lambda _owner, asset, field: AutoIPFile(
                field, paths[asset.asset_id], asset.visual_class, asset.mime_type,
            ),
            profile_resolver=lambda _owner, _account, key: (
                profile if key == profile.key else None
            ),
            quote_verifier=lambda *_arguments: quote,
        )

        receipt = processor(bundle)

        self.assertEqual(receipt.external_task_id, "autoip-task-001")
        _intent, _decision, job, request_id, _quote = self.client.calls[0]
        self.assertEqual(request_id, "request-multi-subject")
        self.assertEqual(
            [item.field_name for item in job.files],
            ["images[]", "images[]", "images[]"],
        )
        self.assertEqual(job.operation.value, "multi_subject_video")

    def test_verified_unlinked_owner_asset_executes_without_character_grant(self):
        generic_asset = MediaAsset(
            asset_id="provider-version:unlinked-version",
            kind=MediaKind.IMAGE,
            uri="castle://provider-assets/unlinked/unlinked-version",
            visual_class=VisualClass.ANIME,
            character_ids=(),
            mime_type="image/png",
            byte_size=self.path.stat().st_size,
            sha256=self.digest,
        )
        generic_intent = RouteIntent(
            capability=CapabilityKind.IMAGE_TO_VIDEO,
            visual_class=VisualClass.ANIME,
            characters=(),
            assets=(generic_asset,),
            required_options=self.intent.required_options,
            preferred_providers=("autoip",),
        )
        receipt = self.processor()(self.bundle(intent=generic_intent))
        self.assertEqual(receipt.external_task_id, "autoip-task-001")
        submitted_intent, _decision, job, _request_id, _quote = self.client.calls[0]
        self.assertEqual(submitted_intent.characters, ())
        self.assertEqual(submitted_intent.assets[0].character_ids, ())
        self.assertIsNone(job.max_credits)

    def test_changed_asset_digest_is_definite_and_never_submitted(self):
        self.path.write_bytes(b"changed-after-confirmation")
        with self.assertRaisesRegex(DefiniteLocalFailure, "size changed|digest changed"):
            self.processor()(self.bundle())
        self.assertEqual(self.client.calls, [])

    def test_identity_asset_cannot_be_silently_dropped_by_text_only_operation(self):
        text_intent = RouteIntent(
            capability=CapabilityKind.TEXT_TO_VIDEO,
            visual_class=VisualClass.ANIME,
            characters=self.intent.characters,
            assets=self.intent.assets,
            required_options=self.intent.required_options,
            preferred_providers=("autoip",),
        )
        with self.assertRaises(DefiniteLocalFailure):
            self.processor()(self.bundle(intent=text_intent))
        self.assertEqual(self.client.calls, [])

    def test_definite_4xx_is_safe_rejection_not_submission_uncertainty(self):
        self.client = RejectingClient(429, retry_after="37")

        with self.assertRaises(DefiniteProviderRejection) as caught:
            self.processor()(self.bundle())

        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.error_code, "rate_limited")
        self.assertEqual(caught.exception.retry_after_seconds, 37)
        self.assertNotIn("provider response", caught.exception.durable_detail)
        self.assertEqual(len(self.client.calls), 1)

    def test_408_remains_ambiguous_for_outer_worker(self):
        self.client = RejectingClient(408)

        with self.assertRaises(AutoIPApiError) as caught:
            self.processor()(self.bundle())

        self.assertFalse(caught.exception.definite_submission_rejection)


if __name__ == "__main__":
    unittest.main(verbosity=2)
