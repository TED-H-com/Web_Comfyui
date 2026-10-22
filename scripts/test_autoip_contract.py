#!/usr/bin/env python3
"""Offline synthetic tests for the pinned AutoIP 0.4.0 contract freeze."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from provider_fabric.autoip_contract import (  # noqa: E402
    AUTOIP_ROUTES,
    CONTRACT_ID,
    PACKAGE_INTEGRITY,
    PACKAGE_NAME,
    PACKAGE_VERSION,
    AutoIPContractValidationError,
    assert_contract_matches_legacy_operation_paths,
    loads_account_response,
    loads_credit_balance_response,
    loads_dance_template_catalog,
    loads_error_envelope,
    loads_generation_request,
    loads_pricing_response,
    loads_submission_response,
    loads_task_list_page,
    loads_task_status,
    normalize_dance_template_catalog,
    normalize_account_response,
    normalize_credit_balance_response,
    normalize_error_envelope,
    normalize_generation_request,
    normalize_pricing_response,
    normalize_output_declaration,
    normalize_submission_response,
    normalize_task_list_page,
    normalize_task_status,
    render_route,
)


def media(
    field_name: str,
    asset_version_id: str,
    mime_type: str,
    signature_family: str,
    visual_class: str | None,
    *,
    byte_size: int = 1024,
) -> dict[str, object]:
    return {
        "field_name": field_name,
        "asset_version_id": asset_version_id,
        "sha256": "a" * 64,
        "mime_type": mime_type,
        "byte_size": byte_size,
        "signature_family": signature_family,
        "visual_class": visual_class,
    }


def request(
    operation: str,
    visual_class: str,
    fields: dict[str, object],
    inputs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "contract": CONTRACT_ID,
        "schema_version": 1,
        "operation": operation,
        "visual_class": visual_class,
        "fields": fields,
        "inputs": inputs or [],
    }


def official_account(plan: str = "pro") -> dict[str, Any]:
    return {
        "api_key": {
            "created_at": "2026-08-12T20:01:00Z",
            "expires_at": None,
            "key_prefix": "synthetic-prefix",
            "last_used_at": "2026-08-14T08:00:00Z",
            "name": "Castle test key",
        },
        "limits": {
            "max_concurrent_tasks": 3,
            "rate_limit_per_minute": 60,
        },
        "plan": plan,
    }


def official_video_prices() -> dict[str, dict[str, int]]:
    """2026-09-16 線上的形狀: 720p 已下架, 換成 768p 與 2k, 480p 長到 15 秒。"""

    return {
        "480p": {str(second): 6 * second for second in range(5, 16)},
        "768p": {str(second): 15 * second for second in range(5, 11)},
        "2k": {str(second): 36 * second for second in range(5, 11)},
    }


def official_pricing() -> dict[str, Any]:
    return {
        "data": {
            "dance": {
                "credits": 99,
                "endpoint": "/api/v1/videos/dance",
            },
            "image_to_image": {
                "credits_by_mode": {"hd_4k": 30, "standard": 15},
                "endpoint": "/api/v1/images/image-to-image",
            },
            "image_to_video": {
                "credits_by_resolution_and_duration": official_video_prices(),
                "endpoint": "/api/v1/videos/image-to-video",
            },
            "lip_sync": {
                "credits": 50,
                "endpoint": "/api/v1/videos/lip-sync",
            },
            "multi_subject_reference": {
                "credits_by_resolution_and_duration": official_video_prices(),
                "endpoint": "/api/v1/videos/multi-subject-reference",
            },
            "text_to_image": {
                "credits_by_mode": {
                    "anime": 15,
                    "hd_4k": 20,
                    "standard": 10,
                    "v2": 15,
                    "v3": 15,
                },
                "endpoint": "/api/v1/images/text-to-image",
            },
            "text_to_video": {
                "credits_by_resolution_and_duration": official_video_prices(),
                "endpoint": "/api/v1/videos/text-to-video",
            },
        }
    }


class PinnedSurfaceTests(unittest.TestCase):
    def test_package_identity_and_integrity_are_frozen(self) -> None:
        self.assertEqual(PACKAGE_NAME, "@autoip/mcp")
        self.assertEqual(PACKAGE_VERSION, "0.4.0")
        self.assertEqual(CONTRACT_ID, "@autoip/mcp@0.4.0")
        self.assertEqual(
            PACKAGE_INTEGRITY,
            "sha512-b+6LkXMANGI7Xe4sYX/609VMVQy9taxuOn0oL18fctTsqwNEQ0TikVP9YphpAdTf2jJHjTDbpNBivC/9qM8i8A==",
        )

    def test_allowlist_has_exact_relative_routes(self) -> None:
        self.assertEqual(
            {name: (route.method, route.path_template, route.chargeable) for name, route in AUTOIP_ROUTES.items()},
            {
                "text_to_image": ("POST", "/images/text-to-image", True),
                "image_to_image": ("POST", "/images/image-to-image", True),
                "image_to_video": ("POST", "/videos/image-to-video", True),
                "text_to_video": ("POST", "/videos/text-to-video", True),
                "lip_sync": ("POST", "/videos/lip-sync", True),
                "dance": ("POST", "/videos/dance", True),
                "multi_subject_video": (
                    "POST",
                    "/videos/multi-subject-reference",
                    True,
                ),
                "dance_templates": ("GET", "/dance-templates", False),
                "account": ("GET", "/me", False),
                "credit_balance": ("GET", "/credits/balance", False),
                "pricing": ("GET", "/pricing", False),
                "task_list": ("GET", "/tasks", False),
                "task_status": ("GET", "/tasks/{task_uuid}", False),
                "task_output": (
                    "GET",
                    "/tasks/{task_uuid}/outputs/{index}",
                    False,
                ),
            },
        )
        self.assertTrue(all(route.path_template.startswith("/") for route in AUTOIP_ROUTES.values()))
        self.assertTrue(all("://" not in route.path_template for route in AUTOIP_ROUTES.values()))

    def test_route_renderer_is_exact_and_path_safe(self) -> None:
        self.assertEqual(
            render_route("task_output", task_uuid="task-abc_7", index=3),
            ("GET", "/tasks/task-abc_7/outputs/3"),
        )
        for route_name, kwargs in (
            ("not_real", {}),
            ("task_status", {"task_uuid": "../../secret"}),
            ("task_output", {"task_uuid": "task-1", "index": True}),
            ("task_list", {"task_uuid": "task-1"}),
        ):
            with self.subTest(route=route_name), self.assertRaises(AutoIPContractValidationError):
                render_route(route_name, **kwargs)

    def test_legacy_operation_paths_match_the_freeze(self) -> None:
        self.assertIsNone(assert_contract_matches_legacy_operation_paths())


class GenerationRequestTests(unittest.TestCase):
    def image(self, field_name: str, asset_id: str, visual_class: str) -> dict[str, object]:
        return media(field_name, asset_id, "image/png", "png", visual_class)

    def test_all_seven_generation_operations_normalize_offline(self) -> None:
        fixtures = (
            request(
                "text_to_image",
                "anime",
                {"prompt": "anime portrait", "mode": "anime"},
            ),
            request(
                "image_to_image",
                "realistic",
                {"prompt": "change the background", "strength": 0.6},
                [self.image("image", "asset.i2i", "realistic")],
            ),
            request(
                "image_to_video",
                "anime",
                {"prompt": "character turns toward camera", "duration_seconds": 10, "resolution": "768p"},
                [self.image("first_frame", "asset.first", "anime")],
            ),
            request(
                "text_to_video",
                "realistic",
                {"prompt": "cinematic city at night", "duration_seconds": 5, "resolution": "480p"},
            ),
            request(
                "lip_sync",
                "realistic",
                {"audio_duration": 10},
                [
                    self.image("image", "asset.portrait", "realistic"),
                    media("audio", "asset.audio", "audio/mpeg", "mpeg_audio", None),
                ],
            ),
            request(
                "dance",
                "anime",
                {"dance_template_id": 7},
                [self.image("image", "asset.dancer", "anime")],
            ),
            request(
                "multi_subject_video",
                "anime",
                {
                    "prompt": "two characters enter the same scene",
                    "duration_seconds": 10,
                    "resolution": "768p",
                    "descriptions[]": ["hero"],
                    "dialogue_text": "你好",
                },
                [
                    self.image("images[]", "asset.hero", "anime"),
                    self.image("images[]", "asset.friend", "anime"),
                    media("voice_sample", "asset.voice", "audio/wav", "wav", None),
                ],
            ),
        )
        for fixture in fixtures:
            with self.subTest(operation=fixture["operation"]):
                normalized = normalize_generation_request(fixture)
                public = normalized.to_dict()
                self.assertEqual(public["operation"], fixture["operation"])
                self.assertEqual(public["visual_class"], fixture["visual_class"])
                self.assertEqual(normalized.method, "POST")
                self.assertEqual(normalized.path, AUTOIP_ROUTES[fixture["operation"]].path_template)
        multi = normalize_generation_request(fixtures[-1])
        self.assertEqual(multi.fields["descriptions[]"], ("hero", ""))

    def test_visual_class_is_exact_and_never_coerced(self) -> None:
        for visual_class in ("illustration", "unknown", "ANIME", " realistic ", None, 1):
            with self.subTest(visual_class=visual_class), self.assertRaisesRegex(
                AutoIPContractValidationError, "exact allowlisted"
            ):
                normalize_generation_request(
                    request("text_to_video", visual_class, {
                        "prompt": "a bounded prompt",
                        "duration_seconds": 5,
                        "resolution": "480p",
                    })
                )

    def test_text_to_image_class_and_mode_must_match(self) -> None:
        cases = (("anime", "standard"), ("realistic", "anime"))
        for visual_class, mode in cases:
            with self.subTest(case=(visual_class, mode)), self.assertRaisesRegex(
                AutoIPContractValidationError, "visual class"
            ):
                normalize_generation_request(
                    request("text_to_image", visual_class, {"prompt": "portrait", "mode": mode})
                )

    def test_video_duration_uses_oct22_operation_and_resolution_bounds(self) -> None:
        for operation, inputs in (
            ("text_to_video", []),
            ("image_to_video", [self.image("first_frame", "asset.first", "realistic")]),
            ("multi_subject_video", [self.image("images[]", "asset.subject", "realistic")]),
        ):
            for resolution in ("480p", "768p", "2k"):
                minimum = 5 if resolution == "2k" or operation == "multi_subject_video" else 2
                maximum = 15 if resolution == "480p" else 10
                for duration in range(1, 17):
                    with self.subTest(operation=operation, resolution=resolution, duration=duration):
                        value = request(operation, "realistic", {
                            "prompt": "a sufficiently long prompt",
                            "duration_seconds": duration, "resolution": resolution,
                        }, inputs)
                        if minimum <= duration <= maximum:
                            self.assertEqual(normalize_generation_request(value).fields["duration_seconds"], duration)
                        else:
                            with self.assertRaises(AutoIPContractValidationError):
                                normalize_generation_request(value)

    def test_unknown_fields_and_user_webhook_are_rejected(self) -> None:
        for field in ("webhook_url", "api_key", "download_url", "model_override"):
            value = request("text_to_image", "anime", {"prompt": "portrait", "mode": "anime", field: "secret"})
            with self.subTest(field=field), self.assertRaisesRegex(
                AutoIPContractValidationError, "unknown field"
            ) as raised:
                normalize_generation_request(value)
            self.assertNotIn("secret", str(raised.exception))

    def test_exact_envelope_and_operation_are_required(self) -> None:
        base = request("text_to_image", "anime", {"prompt": "portrait", "mode": "anime"})
        for mutate in (
            lambda value: value.update(extra=True),
            lambda value: value.update(contract="@autoip/mcp@latest"),
            lambda value: value.update(schema_version=True),
            lambda value: value.update(operation="../../submit"),
            lambda value: value.pop("inputs"),
        ):
            value = copy.deepcopy(base)
            mutate(value)
            with self.assertRaises(AutoIPContractValidationError):
                normalize_generation_request(value)

    def test_input_mime_signature_class_and_cardinality_are_exact(self) -> None:
        base = request(
            "image_to_image",
            "anime",
            {"prompt": "change background"},
            [self.image("image", "asset.image", "anime")],
        )
        mutations = (
            lambda value: value["inputs"][0].update(mime_type="image/gif", signature_family="gif"),
            lambda value: value["inputs"][0].update(signature_family="jpeg"),
            lambda value: value["inputs"][0].update(visual_class="realistic"),
            lambda value: value["inputs"][0].update(sha256="A" * 64),
            lambda value: value["inputs"][0].update(asset_version_id="asset latest"),
            lambda value: value["inputs"].append(copy.deepcopy(value["inputs"][0])),
            lambda value: value["inputs"].clear(),
        )
        for mutate in mutations:
            value = copy.deepcopy(base)
            mutate(value)
            with self.assertRaises(AutoIPContractValidationError):
                normalize_generation_request(value)

    def test_nonvisual_inputs_cannot_claim_a_character_class(self) -> None:
        value = request(
            "lip_sync",
            "realistic",
            {},
            [
                self.image("image", "asset.person", "realistic"),
                media("audio", "asset.audio", "audio/wav", "wav", "realistic"),
            ],
        )
        with self.assertRaisesRegex(AutoIPContractValidationError, "must not claim"):
            normalize_generation_request(value)

    def test_dance_source_is_exactly_one_template_or_video(self) -> None:
        image = self.image("image", "asset.dancer", "realistic")
        dance_video = media(
            "dance_video", "asset.motion", "video/mp4", "mp4", None
        )
        for fields, inputs in (({}, [image]), ({"dance_template_id": 7}, [image, dance_video])):
            with self.subTest(fields=fields), self.assertRaisesRegex(
                AutoIPContractValidationError, "exactly one"
            ):
                normalize_generation_request(request("dance", "realistic", fields, inputs))
        normalized = normalize_generation_request(
            request("dance", "realistic", {}, [image, dance_video])
        )
        self.assertNotIn("dance_template_id", normalized.fields)

    def test_voice_sample_requires_dialogue_and_descriptions_cannot_outnumber_images(self) -> None:
        image = self.image("images[]", "asset.subject", "anime")
        voice = media("voice_sample", "asset.voice", "audio/flac", "flac", None)
        base_fields = {
            "prompt": "character speaks to camera",
            "duration_seconds": 5,
            "resolution": "480p",
        }
        with self.assertRaisesRegex(AutoIPContractValidationError, "dialogue"):
            normalize_generation_request(
                request("multi_subject_video", "anime", base_fields, [image, voice])
            )
        too_many = {**base_fields, "descriptions[]": ["one", "two"]}
        with self.assertRaisesRegex(AutoIPContractValidationError, "descriptions"):
            normalize_generation_request(
                request("multi_subject_video", "anime", too_many, [image])
            )

    def test_duplicate_json_keys_are_rejected_before_mapping_loss(self) -> None:
        document = (
            '{"contract":"@autoip/mcp@0.4.0","schema_version":1,'
            '"operation":"text_to_image","operation":"text_to_video",'
            '"visual_class":"anime","fields":{"prompt":"portrait","mode":"anime"},"inputs":[]}'
        )
        with self.assertRaisesRegex(AutoIPContractValidationError, "duplicate field") as raised:
            loads_generation_request(document)
        self.assertEqual(raised.exception.code, "duplicate_field")

    def test_json_document_size_is_bounded_before_decoding(self) -> None:
        with self.assertRaises(AutoIPContractValidationError) as raised:
            loads_generation_request(" " * (128 * 1024 + 1))
        self.assertEqual(raised.exception.code, "json_too_large")

        # Python character count is not a UTF-8 allocation bound.
        astral_oversized = '"' + "😀" * 40_000 + '"'
        with self.assertRaises(AutoIPContractValidationError) as raised:
            loads_generation_request(astral_oversized)
        self.assertEqual(raised.exception.code, "json_too_large")

    def test_noncanonical_and_lone_surrogate_text_fail_closed(self) -> None:
        for prompt in ("Cafe\u0301 portrait", "portrait \ud800"):
            with self.subTest(prompt=repr(prompt)), self.assertRaises(
                AutoIPContractValidationError
            ):
                normalize_generation_request(
                    request("text_to_image", "anime", {"prompt": prompt, "mode": "anime"})
                )

    def test_string_bounds_match_zod_utf16_code_units(self) -> None:
        # One astral character is two JavaScript UTF-16 code units, so it meets
        # Zod's min(2) even though Python len() reports one code point.
        one_astral = normalize_generation_request(
            request("text_to_image", "anime", {"prompt": "😀", "mode": "anime"})
        )
        self.assertEqual(one_astral.fields["prompt"], "😀")

        accepted = normalize_generation_request(
            request(
                "text_to_image",
                "anime",
                {"prompt": "😀" * 1000, "mode": "anime"},
            )
        )
        self.assertEqual(len(accepted.fields["prompt"]), 1000)
        with self.assertRaises(AutoIPContractValidationError) as raised:
            normalize_generation_request(
                request(
                    "text_to_image",
                    "anime",
                    {"prompt": "😀" * 1001, "mode": "anime"},
                )
            )
        self.assertEqual(raised.exception.code, "field_value_invalid")

    def test_pathological_json_has_stable_explicit_bounds(self) -> None:
        cases = (
            ("[" * 33 + "]" * 33, "json_depth_invalid"),
            ('{"value":' + "9" * 129 + "}", "json_number_invalid"),
            ('{"value":9223372036854775808}', "json_number_invalid"),
            ('{"value":1e999}', "json_number_invalid"),
            ('{"value":NaN}', "json_number_invalid"),
            ("[" + ",".join("0" for _ in range(8_200)) + "]", "json_token_count_invalid"),
        )
        for document, expected_code in cases:
            with self.subTest(code=expected_code), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                loads_generation_request(document)
            self.assertEqual(raised.exception.code, expected_code)

    def test_strict_json_validates_every_string_and_key_before_projection(self) -> None:
        cases = (
            '{"nested":{"Cafe\\u0301":"value"}}',
            '{"nested":{"key":"Cafe\\u0301"}}',
            '{"nested":{"key":"bad\\u0001control"}}',
            '{"nested":{"key":"\\ud800"}}',
            '{"bad\\u007fkey":1}',
        )
        for document in cases:
            with self.subTest(document=document), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                loads_generation_request(document)
            self.assertEqual(raised.exception.code, "json_text_invalid")

        allowed_controls = loads_generation_request(json.dumps(request(
            "text_to_image",
            "anime",
            {"prompt": "line one\n\tline two", "mode": "anime"},
        )))
        self.assertEqual(allowed_controls.fields["prompt"], "line one\n\tline two")

        carriage_return_whitespace = json.dumps(request(
            "text_to_image", "anime", {"prompt": "portrait", "mode": "anime"}
        )).replace(",", ",\r", 1)
        with self.assertRaises(AutoIPContractValidationError) as raised:
            loads_generation_request(carriage_return_whitespace)
        self.assertEqual(raised.exception.code, "json_text_invalid")

        with self.assertRaises(AutoIPContractValidationError) as raised:
            loads_generation_request(bytearray(b" " * (128 * 1024 + 1)))
        self.assertEqual(raised.exception.code, "json_too_large")


class ResponseNormalizationTests(unittest.TestCase):
    def test_account_balance_and_pricing_loaders_are_strict_and_inert(self) -> None:
        account = loads_account_response(
            '{"data":{"id":7,"subscription":{"status":"active"},'
            '"email":"private@example.invalid"}}'
        )
        self.assertEqual(account.account_identity, "7")
        self.assertEqual(account.plan_state, "active")
        self.assertNotIn("private@example.invalid", json.dumps(account.to_dict()))

        balance = loads_credit_balance_response(
            '{"balance":150,"data":{"available_credits":150}}'
        )
        self.assertEqual(balance.available_credits, 150)

        pricing = loads_pricing_response(
            '{"data":[{"operation":"text_to_image","credits":8,'
            '"mode":"anime"},{"operation":"text_to_video","credits":80,'
            '"resolution":"720p"}]}'
        )
        self.assertEqual(len(pricing.entries), 2)
        self.assertEqual(pricing.entries[0].credits, 8)

    def test_official_account_tiers_are_active_and_sensitive_fields_are_inert(self) -> None:
        for tier in ("starter", "pro", "enterprise", "custom"):
            with self.subTest(tier=tier):
                account = loads_account_response(json.dumps(official_account(tier)))
                self.assertIsNone(account.account_identity)
                self.assertEqual(account.plan_state, "active")
                self.assertEqual(account.plan_tier, tier)
                self.assertEqual(account.max_concurrent_tasks, 3)
                self.assertEqual(account.rate_limit_per_minute, 60)
                self.assertEqual(
                    account.to_dict(),
                    {
                        "account_identity": None,
                        "plan_state": "active",
                        "plan_tier": tier,
                        "max_concurrent_tasks": 3,
                        "rate_limit_per_minute": 60,
                    },
                )
                rendered = json.dumps(account.to_dict(), sort_keys=True)
                self.assertNotIn("synthetic-prefix", rendered)
                self.assertNotIn("Castle test key", rendered)
                self.assertNotIn("api_key", rendered)

    def test_official_account_schema_is_exact_and_unknown_tiers_fail_closed(self) -> None:
        invalid: list[dict[str, Any]] = []
        for tier in ("free", "inactive", "unknown", "PRO", ""):
            invalid.append(official_account(tier))
        for plan in (True, None, {}, []):
            malformed_plan = official_account()
            malformed_plan["plan"] = plan
            invalid.append(malformed_plan)

        missing_top = official_account()
        missing_top.pop("limits")
        invalid.append(missing_top)
        for container_name in ("api_key", "limits"):
            malformed_container = official_account()
            malformed_container[container_name] = []
            invalid.append(malformed_container)

        for field in ("created_at", "expires_at", "key_prefix", "last_used_at", "name"):
            missing = official_account()
            del missing["api_key"][field]
            invalid.append(missing)
        for field, value in (
            ("created_at", 7),
            ("expires_at", 7),
            ("key_prefix", None),
            ("last_used_at", False),
            ("name", []),
        ):
            malformed = official_account()
            malformed["api_key"][field] = value
            invalid.append(malformed)

        for field in ("max_concurrent_tasks", "rate_limit_per_minute"):
            missing = official_account()
            del missing["limits"][field]
            invalid.append(missing)
        for field, value in (
            ("max_concurrent_tasks", 0),
            ("max_concurrent_tasks", True),
            ("rate_limit_per_minute", "60"),
        ):
            malformed = official_account()
            malformed["limits"][field] = value
            invalid.append(malformed)

        duplicate_current = json.dumps(official_account(), separators=(",", ":")).replace(
            '"plan":"pro"', '"plan":"pro","plan":"starter"', 1
        )
        with self.assertRaises(AutoIPContractValidationError) as raised:
            loads_account_response(duplicate_current)
        self.assertEqual(raised.exception.code, "duplicate_field")

        for value in invalid:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ):
                normalize_account_response(value)

    def test_official_pricing_shape_flattens_to_canonical_legacy_entries(self) -> None:
        catalog = loads_pricing_response(json.dumps(official_pricing()))
        self.assertEqual(len(catalog.entries), 78)
        indexed = {
            (entry.operation, entry.dimensions): entry.credits
            for entry in catalog.entries
        }
        self.assertEqual(indexed[("text_to_image", (("mode", "standard"),))], 10)
        self.assertEqual(indexed[("image_to_image", (("mode", "hd_4k"),))], 30)
        self.assertEqual(indexed[("dance", ())], 99)
        self.assertEqual(indexed[("lip_sync", ())], 50)
        self.assertEqual(
            indexed[
                (
                    "image_to_video",
                    (("duration_seconds", 10), ("resolution", "768p")),
                )
            ],
            150,
        )
        self.assertEqual(
            indexed[
                (
                    "multi_subject_video",
                    (("duration_seconds", 5), ("resolution", "480p")),
                )
            ],
            30,
        )
        rendered = json.dumps(catalog.to_dict(), sort_keys=True)
        self.assertNotIn("endpoint", rendered)
        self.assertNotIn("multi_subject_reference", rendered)
        self.assertNotIn("credits_by_", rendered)

        # Mapping insertion order is provider-controlled and must not change
        # the canonical catalog or its eventual authoritative hash.
        reversed_shape = official_pricing()
        reversed_shape["data"] = dict(
            reversed(list(reversed_shape["data"].items()))
        )
        self.assertEqual(
            catalog.to_dict(), normalize_pricing_response(reversed_shape).to_dict()
        )

    def test_provider_additions_are_ignored_instead_of_killing_the_account(self) -> None:
        """2026-09-16 的真實形狀: autoip.app 一路加東西, 我們不准因此死掉。

        8/25 到 9/16 整條 AutoIP 線是停的, 原因就在這裡: /me 多了 nsfw_enabled /
        strict_content_review / min_app_version, /pricing 多了七個我們沒有轉接器
        的操作, 封閉世界的驗證於是把整個帳號判成 invalid_account_contract。
        加法不該是致命的。該致命的是「我們要綁的欄位不見了」或「值本身是壞的」
        —— 那些留在上面幾個測試裡。
        """

        account = official_account()
        account["nsfw_enabled"] = True
        account["strict_content_review"] = False
        account["min_app_version"] = "1.0.0"
        account["api_key"]["raw_key"] = "must-not-be-surfaced"
        account["limits"]["burst"] = 2
        normalized = normalize_account_response(account)
        self.assertEqual(normalized.plan_tier, "pro")
        self.assertEqual(normalized.max_concurrent_tasks, 3)
        # 忽略不等於收下: 多出來的東西一個字都不准進我們的投影。
        public = json.dumps(normalized.to_dict())
        for absent in ("raw_key", "must-not-be-surfaced", "nsfw", "burst", "min_app"):
            self.assertNotIn(absent, public)

        pricing = official_pricing()
        pricing["meta"] = {"generated_at": "2026-09-16T00:00:00Z"}
        for operation, endpoint in (
            ("image_to_prompt", "/api/v1/images/image-to-prompt"),
            ("image_mix", "/api/v1/images/image-mix"),
            ("keyframe", "/api/v1/videos/keyframe"),
            ("voice_clone", "/api/v1/voice/clone"),
        ):
            pricing["data"][operation] = {"credits": 10, "endpoint": endpoint}
        pricing["data"]["text_to_image"]["credits_by_mode"]["v4"] = 12
        pricing["data"]["dance"]["provider_note"] = "ignored"
        catalog = normalize_pricing_response(pricing)
        # 沒有轉接器的操作不進目錄 —— 進去的話路由會以為自己送得出去。
        self.assertNotIn("image_to_prompt", {entry.operation for entry in catalog.entries})
        self.assertEqual(
            catalog.to_dict(), normalize_pricing_response(official_pricing()).to_dict()
        )

        # 但同一個操作底下多出來的座標是「價格事實」, 要收進目錄。
        extended = official_pricing()
        extended["data"]["image_to_video"][
            "credits_by_resolution_and_duration"
        ]["4k"] = {"5": 999}
        recorded = {
            (
                dict(entry.dimensions).get("resolution"),
                dict(entry.dimensions).get("duration_seconds"),
            )
            for entry in normalize_pricing_response(extended).entries
            if entry.operation == "image_to_video"
        }
        self.assertIn(("4k", 5), recorded)
        self.assertIn(("480p", 15), recorded)

    def test_official_pricing_rejects_operation_field_and_endpoint_drift(self) -> None:
        mutations: list[dict[str, Any]] = []

        missing_operation = official_pricing()
        del missing_operation["data"]["dance"]
        mutations.append(missing_operation)
        mixed_legacy = official_pricing()
        mixed_legacy["pricing"] = [
            {"operation": "text_to_image", "credits": 1}
        ]
        mutations.append(mixed_legacy)

        for operation in (
            "dance",
            "image_to_image",
            "image_to_video",
            "lip_sync",
            "multi_subject_reference",
            "text_to_image",
            "text_to_video",
        ):
            correct_endpoint = str(
                official_pricing()["data"][operation]["endpoint"]
            )
            for endpoint in (
                correct_endpoint.removeprefix("/api/v1"),
                correct_endpoint + "/",
                correct_endpoint + "?mode=test",
                "/api/v1/not-allowlisted",
            ):
                wrong_endpoint = official_pricing()
                wrong_endpoint["data"][operation]["endpoint"] = endpoint
                mutations.append(wrong_endpoint)

        for value in mutations:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ):
                normalize_pricing_response(value)

    def test_official_pricing_rejects_noncanonical_modes_and_credit_values(self) -> None:
        mutations: list[dict[str, Any]] = []
        for operation, mode in (
            ("text_to_image", "standard"),
            ("image_to_image", "hd_4k"),
        ):
            missing = official_pricing()
            del missing["data"][operation]["credits_by_mode"][mode]
            mutations.append(missing)
            for credits in (True, "10", -1, 1.5, 2**31):
                malformed = official_pricing()
                malformed["data"][operation]["credits_by_mode"][mode] = credits
                mutations.append(malformed)

        wrong_container = official_pricing()
        wrong_container["data"]["text_to_image"]["credits_by_mode"] = []
        mutations.append(wrong_container)

        for credits in (False, "99", -1, 0.5, 2**31):
            malformed_flat = official_pricing()
            malformed_flat["data"]["dance"]["credits"] = credits
            mutations.append(malformed_flat)

        for value in mutations:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ):
                normalize_pricing_response(value)

    def test_official_pricing_rejects_noncanonical_video_matrices(self) -> None:
        mutations: list[dict[str, Any]] = []
        operation = "image_to_video"

        missing_floor = official_pricing()
        del missing_floor["data"][operation][
            "credits_by_resolution_and_duration"
        ]["480p"]
        mutations.append(missing_floor)
        # 「05」跟「5.0」仍然是壞的: 同一個秒數兩種寫法會在目錄裡變成兩筆價。
        for duration in ("05", "0", "5.0", "5s", ""):
            malformed_duration = official_pricing()
            malformed_duration["data"][operation][
                "credits_by_resolution_and_duration"
            ]["480p"][duration] = 1
            mutations.append(malformed_duration)
        empty_durations = official_pricing()
        empty_durations["data"][operation][
            "credits_by_resolution_and_duration"
        ]["768p"] = {}
        mutations.append(empty_durations)
        for credits in (False, "60", -1, 0.5, 2**31):
            malformed = official_pricing()
            malformed["data"][operation][
                "credits_by_resolution_and_duration"
            ]["480p"]["5"] = credits
            mutations.append(malformed)

        poisoned_matrix = official_pricing()
        poisoned_matrix["data"][operation][
            "credits_by_resolution_and_duration"
        ] = []
        mutations.append(poisoned_matrix)
        poisoned_durations = official_pricing()
        poisoned_durations["data"][operation][
            "credits_by_resolution_and_duration"
        ]["480p"] = 60
        mutations.append(poisoned_durations)

        for value in mutations:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ):
                normalize_pricing_response(value)

    def test_official_pricing_duplicate_json_keys_fail_before_flattening(self) -> None:
        document = json.dumps(official_pricing(), separators=(",", ":"))
        duplicate_operation = document.replace(
            '"dance":{"credits":99,"endpoint":"/api/v1/videos/dance"}',
            '"dance":{"credits":99,"endpoint":"/api/v1/videos/dance"},'
            '"dance":{"credits":99,"endpoint":"/api/v1/videos/dance"}',
            1,
        )
        duplicate_mode = document.replace(
            '"standard":10', '"standard":10,"standard":11', 1
        )
        duplicate_duration = document.replace(
            '"5":30', '"5":30,"5":31', 1
        )
        for poisoned in (duplicate_operation, duplicate_mode, duplicate_duration):
            with self.subTest(poisoned=poisoned), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                loads_pricing_response(poisoned)
            self.assertEqual(raised.exception.code, "duplicate_field")

    def test_account_loaders_reject_duplicates_conflicts_and_poisoned_shapes(self) -> None:
        invalid_documents = (
            '{"plan_status":"active","plan_status":"inactive"}',
            '{"id":"one","data":{"id":"two"}}',
            '{"plan_status":"active","data":{"subscription_status":"inactive"}}',
            '{"data":[]}',
            '{"plan_status":"mystery"}',
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(
                AutoIPContractValidationError
            ):
                loads_account_response(document)

        invalid_balances = (
            '{"balance":10,"balance":11}',
            '{"balance":10,"data":{"credits":11}}',
            '{"balance":"10"}',
            '{"balance":9223372036854775808}',
        )
        for document in invalid_balances:
            with self.subTest(document=document), self.assertRaises(
                AutoIPContractValidationError
            ):
                loads_credit_balance_response(document)

    def test_pricing_loader_rejects_any_poison_instead_of_dropping_rows(self) -> None:
        invalid_documents = (
            '{"data":[{"operation":"text_to_image","credits":8,"credits":9}]}',
            '{"data":[{"operation":"text_to_image","credits":"8"}]}',
            '{"data":[{"operation":"../../secret","credits":1}]}',
            '{"data":[{"operation":"text_to_image","credits":8,'
            '"mode":{"nested":true}}]}',
            '{"pricing":[{"operation":"text_to_image","credits":8}],'
            '"data":[{"operation":"text_to_image","credits":9}]}',
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(
                AutoIPContractValidationError
            ):
                loads_pricing_response(document)

    def test_submission_response_projects_provider_extensions_and_is_inert(self) -> None:
        normalized = normalize_submission_response(
            {
                "data": {
                    "task_uuid": "task-123",
                    "credits_charged": 8,
                    "remaining_credits": 92,
                    "provider_trace": "private",
                },
                "unknown": "private",
            }
        )
        self.assertEqual(normalized.to_dict()["task_uuid"], "task-123")
        self.assertNotIn("private", json.dumps(normalized.to_dict()))
        for invalid in (
            {"task_uuid": "task", "credits_charged": True, "remaining_credits": 1},
            {"task_uuid": "../../task", "credits_charged": 1, "remaining_credits": 1},
            {"credits_charged": 1, "remaining_credits": 1},
            {"task_uuid": "task", "remaining_credits": 1},
        ):
            with self.assertRaises(AutoIPContractValidationError) as raised:
                normalize_submission_response(invalid)
            self.assertNotIn("aip_secret", str(raised.exception))

        with self.assertRaisesRegex(AutoIPContractValidationError, "duplicate field"):
            loads_submission_response(
                '{"task_uuid":"task","task_uuid":"other","credits_charged":1,"remaining_credits":2}'
            )

    def test_submission_aliases_must_agree_across_envelope_layers(self) -> None:
        matching = normalize_submission_response(
            {
                "task_uuid": "task-same",
                "credits_charged": 3,
                "remaining_credits": 7,
                "data": {
                    "id": "task-same",
                    "credits_charged": 3,
                    "remaining_credits": 7,
                },
            }
        )
        self.assertEqual(matching.task_uuid, "task-same")
        mutations = (
            {"data": {"id": "task-other", "credits_charged": 3, "remaining_credits": 7}},
            {"data": {"id": "task-same", "credits_charged": 4, "remaining_credits": 7}},
            {"data": {"id": "task-same", "credits_charged": 3, "remaining_credits": 8}},
        )
        for mutation in mutations:
            value = {
                "task_uuid": "task-same",
                "credits_charged": 3,
                "remaining_credits": 7,
                **mutation,
            }
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_submission_response(value)
            self.assertEqual(raised.exception.code, "alias_mismatch")

    def test_task_output_drops_provider_url_and_filename(self) -> None:
        secret = "aip_reflected-secret"
        output = normalize_output_declaration(
            {
                "index": 0,
                "mime_type": "video/mp4",
                "size_bytes": 1234,
                "signature_family": "mp4",
                "download_url": f"https://example.invalid/file?token={secret}",
                "filename": f"private-{secret}.mp4",
            }
        )
        public = json.dumps(output.to_dict(), sort_keys=True)
        self.assertEqual(output.provider_mime_hint, "video/mp4")
        self.assertEqual(output.provider_byte_size_hint, 1234)
        self.assertFalse(output.to_dict()["signature_verified"])
        self.assertIsNone(output.to_dict()["mime_type"])
        self.assertIsNone(output.to_dict()["byte_size"])
        self.assertNotIn("http", public)
        self.assertNotIn("filename", public)
        self.assertNotIn(secret, public)

    def test_output_metadata_is_only_a_bounded_untrusted_hint(self) -> None:
        valid = normalize_output_declaration(
            {"index": 1, "content_type": "image/png", "byte_size": 8}
        )
        self.assertEqual(valid.provider_mime_hint, "image/png")
        self.assertNotIn("image/png", json.dumps(valid.to_dict()))
        # MIME is not an adoption allowlist here; authenticated download byte
        # validation owns that decision in a later layer.
        unrecognized = normalize_output_declaration(
            {"type": "application/provider-specific", "filename": "x.bin"},
            fallback_index=3,
        )
        self.assertEqual(unrecognized.index, 3)
        self.assertEqual(unrecognized.provider_mime_hint, "application/provider-specific")
        invalid = (
            {"index": 0, "mime_type": "video/mp4", "byte_size": 0},
            {"index": True},
            {"mime_type": "video/mp4"},
            {"index": 0, "filename": 7},
            {"index": 0, "mime_type": {"nested": "value"}},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(AutoIPContractValidationError):
                normalize_output_declaration(value)

        for value in (
            {"index": 1, "output_index": 2},
            {"index": 1, "mime_type": "video/mp4", "content_type": "image/png"},
            {"index": 1, "byte_size": 4, "size_bytes": 5},
        ):
            with self.subTest(alias=value), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_output_declaration(value)
            self.assertEqual(raised.exception.code, "alias_mismatch")

    def test_task_status_uses_route_bound_id_and_projects_unknown_fields(self) -> None:
        normalized = normalize_task_status(
            {
                "status": "completed",
                "outputs": [
                    {"download_url": "https://example.invalid/private"},
                    {"filename": "private.mp4", "mime_type": "video/mp4"},
                ],
                "prompt": "private prompt",
            },
            expected_task_id="task-route-bound",
        )
        self.assertEqual(normalized.task_uuid, "task-route-bound")
        self.assertEqual([output.index for output in normalized.outputs], [0, 1])
        public = json.dumps(normalized.to_dict())
        self.assertNotIn("private", public)
        self.assertNotIn("video/mp4", public)

        corroborated = normalize_task_status(
            {"data": {"id": "task-route-bound", "state": "running", "other": 1}},
            expected_task_id="task-route-bound",
        )
        self.assertEqual(corroborated.status, "running")

    def test_task_status_aliases_must_agree_across_envelope_layers(self) -> None:
        base = {
            "task_uuid": "task-one",
            "status": "running",
            "credits_charged": 3,
            "remaining_credits": 7,
        }
        matching = normalize_task_status(
            {
                **base,
                "data": {
                    "id": "task-one",
                    "state": "running",
                    "credits_charged": 3,
                    "remaining_credits": 7,
                },
            },
            expected_task_id="task-one",
        )
        self.assertEqual(matching.status, "running")
        nested_mutations = (
            {"id": "task-two", "state": "running", "credits_charged": 3, "remaining_credits": 7},
            {"id": "task-one", "state": "failed", "credits_charged": 3, "remaining_credits": 7},
            {"id": "task-one", "state": "running", "credits_charged": 4, "remaining_credits": 7},
            {"id": "task-one", "state": "running", "credits_charged": 3, "remaining_credits": 8},
        )
        for nested in nested_mutations:
            with self.subTest(nested=nested), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_task_status(
                    {**base, "data": nested}, expected_task_id="task-one"
                )
            self.assertEqual(raised.exception.code, "alias_mismatch")

    def test_unknown_status_maps_to_a_non_terminal_state(self) -> None:
        # 契約收下了那個字之後, 對照表也不准把它變成例外 —— 這兩半是同一條規則。
        from provider_fabric.autoip import _TASK_STATE_BY_STATUS, _task_state
        from provider_fabric.models import TaskState

        for unknown in ("mystery", "canceled", "post_processing"):
            with self.subTest(status=unknown):
                self.assertNotIn(unknown, _TASK_STATE_BY_STATUS)
                self.assertEqual(_task_state(unknown), TaskState.RUNNING)
        self.assertEqual(_task_state("completed"), TaskState.COMPLETED)

    def test_task_status_accepts_unknown_status_token_but_still_checks_shape(self) -> None:
        # 沒見過的狀態要能過, 否則對方多一個中間狀態就會讓 reconciler 停機。
        for unknown in ("mystery", "canceled", "in-progress", "post_processing", "step.2"):
            with self.subTest(status=unknown):
                task = normalize_task_status(
                    {"task_uuid": "task", "status": unknown, "outputs": []},
                    expected_task_id="task",
                )
                self.assertEqual(task.status, unknown)
        # 但形狀還是要驗: 空字串、大寫、太長、夾雜空白或 URL 都不是 token。
        for bad in ("", "Running", "a" * 41, "has space", "https://x/y?q=1", 7, None):
            with self.subTest(status=bad), self.assertRaises(AutoIPContractValidationError):
                normalize_task_status(
                    {"task_uuid": "task", "status": bad, "outputs": []},
                    expected_task_id="task",
                )

    def test_task_status_rejects_identity_mismatch_duplicate_and_nonterminal_outputs(self) -> None:
        output = {"index": 0, "mime_type": "image/png", "byte_size": 8}
        # 2026-09-16: "mystery" / "canceled" 從這張拒收清單移走了。狀態值是對方的詞彙,
        # 用封閉列舉擋它等於把對方每一次改版變成我們停機 —— 形狀的檢查在下面那個
        # test_task_status_accepts_unknown_status_token 裡, 值的對照在 _task_state()。
        cases = (
            {"task_uuid": "task", "status": "completed", "outputs": [output, output]},
            {"task_uuid": "task", "status": "running", "outputs": [output]},
            {"task_uuid": "other", "status": "completed", "outputs": []},
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(AutoIPContractValidationError) as raised:
                normalize_task_status(value, expected_task_id="task")
            self.assertNotIn("aip_secret", str(raised.exception))

        with self.assertRaisesRegex(AutoIPContractValidationError, "duplicate field"):
            loads_task_status(
                '{"task_uuid":"task","status":"completed","outputs":['
                '{"index":0,"index":1,"mime_type":"image/png"}]}',
                expected_task_id="task",
            )

    def test_completed_task_serialization_is_owner_safe(self) -> None:
        normalized = normalize_task_status(
            {
                "status": "completed",
                "credits_charged": 50,
                "remaining_credits": 500,
                "outputs": [
                    {
                        "index": 0,
                        "content_type": "image/png",
                        "byte_size": 12,
                        "download_url": "https://example.invalid/private",
                        "filename": "private.png",
                    }
                ],
            },
            expected_task_id="task-safe",
        )
        public = json.dumps(normalized.to_dict(), sort_keys=True)
        self.assertIn('"requires_authorization": true', public)
        self.assertNotIn("example.invalid", public)
        self.assertNotIn("private.png", public)

    def test_task_list_page_is_bounded_and_sanitized(self) -> None:
        normalized = normalize_task_list_page(
            {
                "data": [
                    {
                        "id": "task-one",
                        "state": "completed",
                        "prompt": "private prompt",
                        "outputs": [{"download_url": "https://example.invalid/private"}],
                    },
                    {"task_uuid": "task-two", "status": "running"},
                ],
                "meta": {
                    "current_page": 2,
                    "per_page": 20,
                    "total": 22,
                    "last_page": 2,
                },
                "unknown": "private",
            },
            expected_page=2,
            expected_per_page=20,
        )
        self.assertEqual([item.task_uuid for item in normalized.tasks], ["task-one", "task-two"])
        self.assertEqual(normalized.total, 22)
        public = json.dumps(normalized.to_dict())
        self.assertNotIn("private", public)
        self.assertNotIn("download_url", public)
        loaded = loads_task_list_page(
            '{"tasks":[{"task_id":"task-json","status":"queued"}],"page":1,"per_page":1}',
            expected_page=1,
            expected_per_page=1,
        )
        self.assertEqual(loaded.tasks[0].task_uuid, "task-json")

        with self.assertRaises(AutoIPContractValidationError):
            normalize_task_list_page(
                {"tasks": [{"id": "same", "status": "queued"}] * 2}
            )
        with self.assertRaises(AutoIPContractValidationError) as raised:
            normalize_task_list_page(
                {"tasks": [{"id": f"task-{index}", "status": "queued"} for index in range(101)]}
            )
        self.assertEqual(raised.exception.code, "schema_invalid")

    def test_task_list_aliases_and_page_invariants_fail_closed(self) -> None:
        alias_conflicts = (
            {"tasks": [{"id": "task-one", "task_uuid": "task-two", "status": "queued"}]},
            {"tasks": [{"id": "task-one", "status": "queued", "state": "running"}]},
            {"tasks": [], "page": 1, "current_page": 2},
            {"tasks": [], "per_page": 10, "page_size": 20},
            {"tasks": [], "total": 1, "total_count": 2},
            {"tasks": [], "total_pages": 1, "last_page": 2},
            {"tasks": [], "page": 1, "meta": {"page": 2}},
            {"tasks": [], "page": 1, "data": {"items": [], "pagination": {"current_page": 2}}},
            {"tasks": [], "data": {"items": [{"id": "other", "status": "queued"}]}},
        )
        for value in alias_conflicts:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_task_list_page(value)
            self.assertEqual(raised.exception.code, "alias_mismatch")

        task = {"id": "task-one", "status": "queued"}
        page_conflicts = (
            {"tasks": [task, {"id": "task-two", "status": "queued"}], "per_page": 1},
            {"tasks": [task], "per_page": 10, "total": 0},
            {"tasks": [], "page": 3, "per_page": 10, "total_pages": 2},
            {"tasks": [task], "page": 1, "per_page": 10, "total": 1, "total_pages": 0},
            {"tasks": [], "page": 2, "per_page": 10, "total": 0, "total_pages": 0},
            {"tasks": [task, {"id": "task-two", "status": "queued"}], "page": 2, "per_page": 20, "total": 21, "total_pages": 2},
            {"tasks": [task], "page": 1, "per_page": 20, "total": 21, "total_pages": 1},
            {"tasks": [task], "page": 1, "per_page": 20, "total": 1},
            {"tasks": [task], "page": 1, "per_page": 20, "total_pages": 1},
        )
        for value in page_conflicts:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_task_list_page(value)
            self.assertEqual(raised.exception.code, "task_page_invalid")

        empty = normalize_task_list_page(
            {"tasks": [], "page": 1, "per_page": 20, "total": 0, "total_pages": 0}
        )
        self.assertEqual(empty.tasks, ())
        provider_empty = loads_task_list_page(
            '{"data":[],"meta":{"current_page":1,"last_page":1,'
            '"per_page":100,"total":0}}',
            expected_page=1,
            expected_per_page=100,
        )
        self.assertEqual(provider_empty.tasks, ())
        self.assertEqual(provider_empty.total, 0)
        self.assertEqual(provider_empty.total_pages, 1)
        sparse_last = normalize_task_list_page(
            {
                "tasks": [task, {"id": "task-two", "status": "queued"}],
                "page": 2,
                "per_page": 20,
                "total": 22,
                "total_pages": 2,
            }
        )
        self.assertEqual(len(sparse_last.tasks), 2)

    def test_dance_catalog_is_review_only_and_drops_provider_urls(self) -> None:
        catalog = normalize_dance_template_catalog(
            {
                "data": {
                    "templates": [
                        {
                            "id": 7,
                            "name": "Short dance",
                            "duration_seconds": 12,
                            "thumbnail_url": "https://example.invalid/private",
                            "provider_extra": "private",
                        }
                    ]
                }
            }
        )
        self.assertEqual(catalog.templates[0].template_id, 7)
        public = json.dumps(catalog.to_dict())
        self.assertIn('"transport_allowlisted": true', public)
        self.assertIn('"product_visibility_granted": false', public)
        self.assertIn('"selectable": false', public)
        self.assertNotIn("example.invalid", public)
        self.assertNotIn("provider_extra", public)
        loaded = loads_dance_template_catalog(
            '[{"template_id":8,"title":"Long dance","duration":30}]'
        )
        self.assertEqual(loaded.templates[0].name, "Long dance")
        with self.assertRaises(AutoIPContractValidationError):
            normalize_dance_template_catalog(
                [
                    {"id": index + 1, "name": "x", "duration": 1}
                    for index in range(501)
                ]
            )

    def test_dance_template_aliases_must_agree(self) -> None:
        for template in (
            {"id": 1, "template_id": 2, "name": "Dance", "duration": 5},
            {"id": 1, "name": "Dance", "title": "Other", "duration": 5},
            {"id": 1, "name": "Dance", "duration": 5, "duration_seconds": 6},
        ):
            with self.subTest(template=template), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_dance_template_catalog([template])
            self.assertEqual(raised.exception.code, "alias_mismatch")
        with self.assertRaises(AutoIPContractValidationError) as raised:
            normalize_dance_template_catalog(
                {
                    "templates": [{"id": 1, "name": "A", "duration": 5}],
                    "data": {"items": [{"id": 2, "name": "B", "duration": 5}]},
                }
            )
        self.assertEqual(raised.exception.code, "alias_mismatch")

    def test_error_envelope_is_bounded_and_redacted(self) -> None:
        secret = "aip_reflected-secret"
        normalized = normalize_error_envelope(
            {
                "error_code": "concurrency_limit",
                "message": f"provider reflected {secret}",
                "errors": [{"key": secret}],
            }
        )
        public = json.dumps(normalized.to_dict())
        self.assertEqual(normalized.error_code, "concurrency_limit")
        self.assertNotIn(secret, public)
        self.assertIn("redacted", public)
        loaded = loads_error_envelope(
            '{"error_code":"validation_failed","message":"bad input","unknown":1}'
        )
        self.assertEqual(loaded.error_code, "validation_failed")
        reviewed_codes = {
            "concurrency_limit",
            "insufficient_credits",
            "plan_required",
            "prompt_rejected",
            "requires_age_verification",
            "validation_failed",
        }
        self.assertEqual(
            {
                normalize_error_envelope(
                    {"error_code": code, "message": "redacted"}
                ).error_code
                for code in reviewed_codes
            },
            reviewed_codes,
        )
        for unknown in (
            {"error_code": "Bad Code", "message": "x"},
            {"error_code": "upstream_unavailable", "message": "x"},
            {"error_code": 7, "message": "x"},
        ):
            normalized = normalize_error_envelope(unknown)
            self.assertEqual(normalized.error_code, "provider_error")
        for invalid in (
            {"error_code": "http_error"},
            {"error_code": "http_error", "message": "x" * 2001},
        ):
            with self.assertRaises(AutoIPContractValidationError):
                normalize_error_envelope(invalid)

    def test_error_aliases_agree_before_unknown_codes_are_collapsed(self) -> None:
        same_unknown = normalize_error_envelope({
            "error_code": "provider-private-one",
            "message": "same",
            "data": {"error_code": "provider-private-one", "message": "same"},
        })
        self.assertEqual(same_unknown.error_code, "provider_error")

        conflicts = (
            {
                "error_code": "provider-private-one",
                "message": "same",
                "data": {"error_code": "provider-private-two", "message": "same"},
            },
            {
                "error_code": "validation_failed",
                "message": "outer",
                "data": {"error_code": "validation_failed", "message": "nested"},
            },
        )
        for value in conflicts:
            with self.subTest(value=value), self.assertRaises(
                AutoIPContractValidationError
            ) as raised:
                normalize_error_envelope(value)
            self.assertEqual(raised.exception.code, "alias_mismatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
