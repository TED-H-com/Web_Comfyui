#!/usr/bin/env python3
"""Offline strict live-quote tests for AutoIP."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from provider_fabric.autoip import (  # noqa: E402
    AutoIPAccountHealth,
    AutoIPCreditBalance,
    AutoIPPricingCatalog,
    AutoIPPricingEntry,
    AutoIPPlanState,
)
from provider_fabric.autoip_quote import AutoIPQuoteError, AutoIPQuoteSource  # noqa: E402
from provider_fabric.config import SecretKind, SecretReference  # noqa: E402
from provider_fabric.models import (  # noqa: E402
    CapabilityKind,
    RouteDecision,
    RouteIntent,
    VisualClass,
)
from provider_fabric.service import ActorContext  # noqa: E402
from provider_fabric.store import ProviderFabricStore  # noqa: E402


class FakeClient:
    plan_state = AutoIPPlanState.ACTIVE
    entries = (
        AutoIPPricingEntry("image_to_video", 8, (("resolution", "480p"),)),
        AutoIPPricingEntry("image_to_video", 12, (("resolution", "720p"),)),
    )

    def __init__(self, config) -> None:
        self.config = config

    def account_health(self):
        return AutoIPAccountHealth(
            provider_id="autoip", provider_reachable=True, api_healthy=True,
            authenticated=True, configured_enabled=self.config.enabled,
            plan_state=self.plan_state,
        )

    def credit_balance(self):
        return AutoIPCreditBalance(40)

    def pricing_catalog(self):
        return AutoIPPricingCatalog(
            entries=self.entries,
            catalog_sha256="b" * 64,
            authoritative=True,
        )


class AutoIPQuoteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = ProviderFabricStore(Path(temporary.name) / "provider.sqlite3")
        self.owner, self.account_id = "owner-one", "autoip-one"
        self.store.register_owned_provider_account(
            self.owner, self.account_id, "autoip", "AutoIP",
            SecretReference(SecretKind.FILE, "/private/autoip-one.key"),
            api_base="https://autoip.app/api/v1", enabled=True,
        )
        self.actor = ActorContext(self.owner, "admin")
        self.account = self.store.get_owned_provider_account(
            self.owner, self.account_id
        )
        self.intent = RouteIntent(
            CapabilityKind.IMAGE_TO_VIDEO, VisualClass.ANIME,
            required_options=frozenset({"resolution"}),
        )
        self.decision = RouteDecision(
            "autoip", "image_to_video.anime.autoip-0.4.0.v1",
            ("autoip:image_to_video.anime.autoip-0.4.0.v1",), (), "policy-v1",
        )

    def test_live_quote_is_exact_authoritative_and_account_bound(self) -> None:
        clients = []
        source = AutoIPQuoteSource(
            self.store,
            client_factory=lambda config: clients.append(FakeClient(config)) or clients[-1],
        )
        quote = source(
            self.actor, self.account, self.intent, self.decision,
            {"resolution": "720p"},
        )
        self.assertTrue(clients[0].config.enabled)
        self.assertEqual(quote.credits, 12)
        self.assertEqual(quote.remaining_credits, 40)
        self.assertTrue(quote.authoritative)
        self.assertEqual(quote.account_id, self.account_id)
        self.assertEqual(quote.quote_id, "autoip-price:" + "b" * 64)

    def test_missing_or_ambiguous_pricing_fails_closed(self) -> None:
        source = AutoIPQuoteSource(self.store, client_factory=FakeClient)
        with self.assertRaisesRegex(AutoIPQuoteError, "pricing_missing"):
            source(
                self.actor, self.account, self.intent, self.decision,
                {"resolution": "1080p"},
            )

        class AmbiguousClient(FakeClient):
            entries = (
                AutoIPPricingEntry("image_to_video", 12, (("resolution", "720p"),)),
                AutoIPPricingEntry("image_to_video", 13, (("resolution", "720p"),)),
            )

        ambiguous = AutoIPQuoteSource(self.store, client_factory=AmbiguousClient)
        with self.assertRaisesRegex(AutoIPQuoteError, "pricing_ambiguous"):
            ambiguous(
                self.actor, self.account, self.intent, self.decision,
                {"resolution": "720p"},
            )

    def test_active_account_quote_does_not_depend_on_balance_endpoint(self) -> None:
        class UnlimitedClient(FakeClient):
            def credit_balance(self):
                raise RuntimeError("unlimited plan has no balance response")

        quote = AutoIPQuoteSource(
            self.store, client_factory=UnlimitedClient,
        )(
            self.actor, self.account, self.intent, self.decision,
            {"resolution": "720p"},
        )
        self.assertEqual(quote.credits, 12)
        self.assertIsNone(quote.remaining_credits)
        self.assertTrue(quote.authoritative)

    def test_inactive_plan_is_rejected_before_balance_or_pricing(self) -> None:
        class InactiveClient(FakeClient):
            plan_state = AutoIPPlanState.INACTIVE

            def credit_balance(self):
                self.fail = True
                raise AssertionError("balance must not be read")

        source = AutoIPQuoteSource(self.store, client_factory=InactiveClient)
        with self.assertRaisesRegex(AutoIPQuoteError, "plan_inactive"):
            source(
                self.actor, self.account, self.intent, self.decision,
                {"resolution": "720p"},
            )

    def test_non_autoip_route_is_declined_for_provider_neutral_fallback(self) -> None:
        source = AutoIPQuoteSource(
            self.store,
            client_factory=lambda _config: self.fail("must not create client"),
        )
        local_intent = RouteIntent(
            CapabilityKind.TEXT_TO_IMAGE, VisualClass.ANIME,
        )
        local_decision = RouteDecision(
            "local_comfy", "local-anime-v1",
            ("local_comfy:local-anime-v1",), (), "policy-v1",
        )
        self.assertIsNone(source(
            self.actor,
            {"account_id": "local-main", "provider_id": "local_comfy", "enabled": True},
            local_intent,
            local_decision,
            {},
        ))


if __name__ == "__main__":
    unittest.main()
