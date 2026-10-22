"""Strict live AutoIP quote source for provider-fabric planning."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .autoip import (
    AUTOIP_API_BASE,
    AutoIPAccountHealth,
    AutoIPClient,
    AutoIPCreditBalance,
    AutoIPOperation,
    AutoIPPlanState,
    AutoIPPricingCatalog,
    AutoIPPricingEntry,
)
from .config import ProviderConfig
from .models import CostQuote, RouteDecision, RouteIntent
from .store import ProviderFabricStore


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AutoIPQuoteError(RuntimeError):
    """Stable quote failure that never contains provider response text."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise AutoIPQuoteError(code)


def _matching_entry(pricing: Any, operation: str, options: Mapping[str, Any]) -> Any:
    matches = []
    for entry in pricing.entries:
        if not isinstance(entry, AutoIPPricingEntry):
            _fail("pricing_unavailable")
        if entry.operation != operation:
            continue
        dimensions = tuple(entry.dimensions)
        if all(
            name in options
            and not isinstance(options[name], bool)
            and options[name] == expected
            and type(options[name]) is type(expected)
            for name, expected in dimensions
        ):
            matches.append(entry)
    if len(matches) != 1:
        _fail("pricing_ambiguous" if matches else "pricing_missing")
    return matches[0]


class AutoIPQuoteSource:
    """Callable matching ``ProviderFabricService``'s ``QuoteSource`` shape.

    Every call obtains fresh health and pricing from the exact actor-owned
    account.  Balance is attached when available as telemetry, but never gates
    an active/unlimited account.  No cached compatibility receipt is accepted
    as a price quote.
    """

    def __init__(
        self,
        store: ProviderFabricStore,
        *,
        client_factory: Callable[[ProviderConfig], AutoIPClient] = AutoIPClient,
    ) -> None:
        if not isinstance(store, ProviderFabricStore):
            raise TypeError("store must be a ProviderFabricStore")
        if not callable(client_factory):
            raise TypeError("client_factory must be callable")
        self._store = store
        self._client_factory = client_factory

    def __call__(
        self,
        actor: Any,
        account: Mapping[str, Any],
        intent: RouteIntent,
        decision: RouteDecision,
        options: Mapping[str, Any],
    ) -> CostQuote | None:
        # Quote sources are shared by the provider-neutral service.  A source
        # must decline other providers so their own pricing path can run.
        if (
            isinstance(account, Mapping)
            and isinstance(decision, RouteDecision)
            and account.get("provider_id") != "autoip"
            and decision.selected_provider_id != "autoip"
        ):
            return None
        if (
            not isinstance(account, Mapping)
            or account.get("provider_id") != "autoip"
            or account.get("enabled") is not True
            or not isinstance(decision, RouteDecision)
            or decision.selected_provider_id != "autoip"
            or not isinstance(intent, RouteIntent)
            or not isinstance(options, Mapping)
        ):
            _fail("quote_scope_mismatch")
        owner_user_id = getattr(actor, "user_id", None)
        account_id = account.get("account_id")
        if not isinstance(owner_user_id, str) or not isinstance(account_id, str):
            _fail("quote_scope_mismatch")
        try:
            operation = AutoIPOperation(intent.capability.value)
            reference = self._store.credential_reference_for_actor(
                owner_user_id, account_id
            )
            client = self._client_factory(ProviderConfig(
                provider_id="autoip",
                api_base=AUTOIP_API_BASE,
                api_key_ref=reference,
                enabled=True,
            ))
            health = client.account_health()
        except AutoIPQuoteError:
            raise
        except Exception:
            _fail("health_unavailable")
        if not isinstance(health, AutoIPAccountHealth):
            _fail("health_unavailable")
        if health.provider_reachable is not True:
            _fail("provider_unreachable")
        if health.api_healthy is not True:
            _fail("provider_unhealthy")
        if health.authenticated is not True:
            _fail("authentication_failed")
        if health.plan_state is not AutoIPPlanState.ACTIVE:
            _fail("plan_inactive")
        try:
            balance = client.credit_balance()
        except Exception:
            balance = None
        if balance is not None and not isinstance(balance, AutoIPCreditBalance):
            balance = None
        try:
            pricing = client.pricing_catalog()
        except Exception:
            _fail("pricing_unavailable")
        if (
            not isinstance(pricing, AutoIPPricingCatalog)
            or not pricing.entries
            or not all(isinstance(item, AutoIPPricingEntry) for item in pricing.entries)
            or pricing.authoritative is not True
            or not isinstance(pricing.catalog_sha256, str)
            or _SHA256.fullmatch(pricing.catalog_sha256) is None
        ):
            _fail("pricing_unavailable")
        entry = _matching_entry(pricing, operation.value, options)
        return CostQuote(
            provider_id="autoip",
            capability=intent.capability,
            credits=entry.credits,
            remaining_credits=(balance.available_credits if balance is not None else None),
            authoritative=True,
            quote_id=f"autoip-price:{pricing.catalog_sha256}",
            detail="Authoritative provider credit quote.",
            account_id=account_id,
        )


__all__ = ["AutoIPQuoteError", "AutoIPQuoteSource"]
