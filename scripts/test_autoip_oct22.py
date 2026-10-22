"""Offline regressions for the user-supplied 2026-10-22 REST skill."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webui"))
from provider_fabric.autoip_contract import (
    AutoIPContractValidationError, normalize_generation_request, normalize_pricing_response,
)
from provider_fabric.autoip_profiles import candidate_autoip_profiles
from provider_fabric.autoip_quote import AutoIPQuoteError, _matching_entry
from provider_fabric.autoip import AutoIPPricingCatalog, AutoIPPricingEntry
from test_autoip_contract import media, request, official_pricing


def image_request(**fields):
    return request("image_to_image", "realistic", {"prompt": "edit the background", **fields},
                   [media("image", "image-1", "image/png", "png", "realistic")])


def test_i2i_v3_and_portrait_size():
    normalized = normalize_generation_request(image_request(mode="v3", size="540x960", style="cinematic"))
    assert normalized.fields["mode"] == "v3"
    assert normalized.fields["size"] == "540x960"
    assert normalized.fields["style"] == "cinematic"


def test_i2i_negative_prompt_only_effective_mode():
    normalized = normalize_generation_request(image_request(mode="hd_4k", negative_prompt="x" * 2000))
    assert len(normalized.fields["negative_prompt"]) == 2000
    for mode in ("standard", "v3"):
        with pytest.raises(AutoIPContractValidationError, match="only takes effect"):
            normalize_generation_request(image_request(mode=mode, negative_prompt="blur"))
    with pytest.raises(AutoIPContractValidationError):
        normalize_generation_request(image_request(mode="hd_4k", negative_prompt="x" * 2001))


def test_optional_i2i_v3_price_does_not_break_old_snapshots():
    old = official_pricing()
    assert not any(e.operation == "image_to_image" and dict(e.dimensions).get("mode") == "v3"
                   for e in normalize_pricing_response(old).entries)
    old["data"]["image_to_image"]["credits_by_mode"]["v3"] = 123
    assert any(e.operation == "image_to_image" and dict(e.dimensions).get("mode") == "v3" and e.credits == 123
               for e in normalize_pricing_response(old).entries)


def test_live_pricing_preserves_two_second_cells_without_inventing_prices():
    payload = official_pricing()
    payload["data"]["text_to_video"]["credits_by_resolution_and_duration"]["480p"]["2"] = 17
    normalized = normalize_pricing_response(payload)
    catalog = AutoIPPricingCatalog(
        entries=tuple(AutoIPPricingEntry(e.operation, e.credits, e.dimensions) for e in normalized.entries),
        authoritative=True, catalog_sha256="b" * 64,
    )
    assert _matching_entry(catalog, "text_to_video", {"resolution": "480p", "duration_seconds": 2}).credits == 17
    with pytest.raises(AutoIPQuoteError, match="pricing_missing"):
        _matching_entry(catalog, "text_to_video", {"resolution": "768p", "duration_seconds": 2})


def test_profiles_offer_short_shots_and_extended_480p_without_changing_default():
    for profile in candidate_autoip_profiles():
        if profile.capability.value in {"image_to_video", "text_to_video", "multi_subject_video"}:
            durations = profile.option_values["duration_seconds"]
            assert durations[0] == 5
            assert 15 in durations
            assert (2 in durations) == (profile.capability.value != "multi_subject_video")
            assert profile.profile_revision.endswith("v4")
