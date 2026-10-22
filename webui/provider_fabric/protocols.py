"""Interfaces implemented by local and remote provider adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .models import (
    CapabilityOffering,
    CostQuote,
    PreflightResult,
    RouteDecision,
    RouteIntent,
    TaskReceipt,
)


@runtime_checkable
class ProviderAdapter(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def offerings(self) -> tuple[CapabilityOffering, ...]: ...

    def quote(self, intent: RouteIntent, payload: Mapping[str, Any]) -> CostQuote: ...

    def preflight(
        self,
        intent: RouteIntent,
        decision: RouteDecision,
        payload: Mapping[str, Any],
        quote: CostQuote | None = None,
    ) -> PreflightResult: ...

    def submit(
        self,
        intent: RouteIntent,
        decision: RouteDecision,
        payload: Mapping[str, Any],
        *,
        client_request_id: str,
    ) -> TaskReceipt: ...

    def get_task(self, external_task_id: str, *, client_request_id: str) -> TaskReceipt: ...

    def output_metadata(self, external_task_id: str, index: int) -> Mapping[str, Any]: ...


@runtime_checkable
class ProviderAccountProbe(Protocol):
    """Optional adapter facet for redacted account readiness probes."""

    def account_health(self) -> object: ...

    def account_preflight(self, *, minimum_credits: int = 0) -> object: ...


@runtime_checkable
class ProviderOutputDownloader(Protocol):
    """Optional adapter facet for authenticated, bounded output adoption."""

    def download_output_to_staging(
        self,
        external_task_id: str,
        index: int,
        staging_root: Path,
        *,
        max_bytes: int,
        metadata: object | None = None,
    ) -> object: ...
