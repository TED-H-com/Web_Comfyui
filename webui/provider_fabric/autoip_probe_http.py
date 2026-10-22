"""Actor-bound AutoIP technical-readiness probe HTTP adapter."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .autoip import AUTOIP_API_BASE, AutoIPClient, AutoIPPlanState
from .autoip_contract import CONTRACT_ID
from .autoip_profiles import candidate_autoip_profiles
from .autoip_readiness import SCHEMA_VERSION, credential_reference_fingerprint
from .config import ProviderConfig
from .http_api import HttpResponse
from .models import CapabilityOffering, EvidenceState
from .service import ActorContext
from .store import ProviderFabricStore, ProviderFabricStoreError


_RECEIPT_ID = re.compile(r"^autoip-probe:[0-9a-f]{32}$")


class _RequestSchemaInvalid(ValueError):
    pass


class AutoIPProbeHttpApi:
    """Verify an owner account and enable only after durable readiness evidence."""

    def __init__(
        self,
        store: ProviderFabricStore,
        *,
        client_factory: Callable[[ProviderConfig], AutoIPClient] = AutoIPClient,
        receipt_recorder: Callable[..., Mapping[str, Any]] | None = None,
        credential_fingerprint_source: Callable[..., str] = credential_reference_fingerprint,
    ) -> None:
        if not isinstance(store, ProviderFabricStore):
            raise TypeError("store must be a ProviderFabricStore")
        if not callable(client_factory):
            raise TypeError("client_factory must be callable")
        self._store = store
        self._client_factory = client_factory
        if receipt_recorder is not None and not callable(receipt_recorder):
            raise TypeError("receipt_recorder must be callable")
        self._receipt_recorder = receipt_recorder
        if not callable(credential_fingerprint_source):
            raise TypeError("credential_fingerprint_source must be callable")
        self._credential_fingerprint_source = credential_fingerprint_source

    @staticmethod
    def _offerings(
        supported_operations: tuple[str, ...],
        receipt_id: str,
    ) -> tuple[CapabilityOffering, ...]:
        supported = frozenset(supported_operations)
        return tuple(
            CapabilityOffering(
                provider_id="autoip",
                capability=profile.capability,
                variant_id=profile.variant_id,
                visual_classes=frozenset({profile.visual_class}),
                evidence=EvidenceState.VERIFIED,
                evidence_id=receipt_id,
                enabled=True,
                priority=100,
                # Persist the full exact option signature.  Required keys are
                # the minimum; bounded optional values (seed/timing) must also
                # survive routing when selected.
                option_keys=frozenset(profile.option_values),
            )
            for profile in candidate_autoip_profiles()
            if profile.capability.value in supported
        )

    @staticmethod
    def _error(status: int, code: str, message: str) -> HttpResponse:
        return HttpResponse(status, {"error": {"code": code, "message": message, "reason_codes": []}})

    @staticmethod
    def _actor(actor: ActorContext | None) -> ActorContext:
        if actor is None:
            raise PermissionError("authentication_required")
        if actor.role not in {"admin", "operator"}:
            raise PermissionError("administrator_required")
        return actor

    def post_probe(
        self,
        actor: ActorContext | None,
        account_id: str,
        body: Mapping[str, object],
    ) -> HttpResponse:
        try:
            current = self._actor(actor)
            if (
                not isinstance(body, Mapping)
                or set(body) != {"schema_version"}
                or body.get("schema_version") != 1
            ):
                raise _RequestSchemaInvalid("request_schema_invalid")
            account = self._store.get_owned_provider_account(current.user_id, account_id)
            if account["provider_id"] != "autoip":
                raise KeyError(account_id)
            reference = self._store.credential_reference_for_actor(
                current.user_id, account_id
            )
            credential_fingerprint = self._credential_fingerprint_source(reference)
            client = self._client_factory(
                ProviderConfig(
                    provider_id="autoip",
                    api_base=AUTOIP_API_BASE,
                    api_key_ref=reference,
                    # The probe is explicitly read-only. Keeping the client
                    # disabled prevents this object from being reused for a
                    # chargeable submission path.
                    enabled=False,
                )
            )
            health = client.account_health()
            balance = None
            pricing = None
            reasons: list[str] = []
            if not health.provider_reachable:
                reasons.append("provider_unreachable")
            if not health.api_healthy:
                reasons.append("provider_unhealthy")
            if not health.authenticated:
                reasons.append("authentication_failed")
            if health.plan_state is AutoIPPlanState.INACTIVE:
                reasons.append("plan_inactive")
            if health.authenticated and health.api_healthy:
                try:
                    balance = client.credit_balance()
                except Exception:
                    reasons.append("balance_unavailable")
                try:
                    pricing = client.pricing_catalog()
                except Exception:
                    reasons.append("pricing_unavailable")
            if pricing is not None and pricing.authoritative is not True:
                reasons.append("pricing_unavailable")
            supported_operations = tuple(sorted({
                entry.operation for entry in pricing.entries
            })) if pricing is not None else ()
            candidate_operations = frozenset(
                profile.capability.value for profile in candidate_autoip_profiles()
            )
            supported_operations = tuple(
                operation for operation in supported_operations
                if operation in candidate_operations
            )
            if pricing is not None and not supported_operations:
                reasons.append("supported_operations_unavailable")
            ready = (
                not reasons
                and balance is not None
                and pricing is not None
                and pricing.authoritative is True
                and bool(supported_operations)
            )
            compatibility_receipt = None
            if ready:
                if self._receipt_recorder is None:
                    raise RuntimeError("autoip readiness receipt store is unavailable")
                if self._credential_fingerprint_source(reference) != credential_fingerprint:
                    raise RuntimeError("autoip credential changed during readiness probe")
                compatibility_receipt = dict(self._receipt_recorder(
                    current.user_id,
                    account_id,
                    health=health.to_dict(),
                    available_credits=balance.available_credits,
                    pricing_catalog_sha256=pricing.catalog_sha256,
                    pricing_authoritative=pricing.authoritative,
                    credential_fingerprint=credential_fingerprint,
                    supported_operations=supported_operations,
                ))
                receipt_id = compatibility_receipt.get("receipt_id")
                if (
                    compatibility_receipt.get("schema_version") != SCHEMA_VERSION
                    or compatibility_receipt.get("contract_id") != CONTRACT_ID
                    or compatibility_receipt.get("persisted") is not True
                    or not isinstance(receipt_id, str)
                    or _RECEIPT_ID.fullmatch(receipt_id) is None
                ):
                    raise RuntimeError("autoip readiness receipt is invalid")
                offerings = self._offerings(supported_operations, receipt_id)
                if not offerings:
                    raise RuntimeError("autoip capability snapshot is empty")
                self._store.record_owned_capability_snapshot(
                    current.user_id,
                    account_id,
                    offerings,
                    source_revision=receipt_id,
                )
            successful = ready and compatibility_receipt is not None
            response = HttpResponse(200, {
                "schema_version": 1,
                "provider_id": "autoip",
                "account_id": account_id,
                "ready": successful,
                "reason_codes": list(dict.fromkeys(reasons)),
                "health": health.to_dict(),
                "available_credits": (
                    balance.available_credits if balance is not None else None
                ),
                "pricing": pricing.to_dict() if pricing is not None else None,
                "compatibility_receipt": compatibility_receipt,
                "generation_enabled": successful,
                "provider_write_performed": False,
                "credits_spent": 0,
            })
            if successful:
                # Enabling is the final fallible step: no failed credential,
                # plan, pricing, receipt, catalog, or response projection can
                # turn on chargeable execution.
                self._store.set_owned_provider_account_enabled(
                    current.user_id, account_id, True
                )
            return response
        except PermissionError as error:
            code = str(error)
            return self._error(
                401 if code == "authentication_required" else 403,
                code,
                "Authentication is required."
                if code == "authentication_required"
                else "Administrator access is required.",
            )
        except KeyError:
            return self._error(404, "provider_account_not_found", "Provider account was not found.")
        except ProviderFabricStoreError:
            return self._error(
                503,
                "provider_readiness_unavailable",
                "Provider readiness could not be stored safely.",
            )
        except _RequestSchemaInvalid:
            return self._error(400, "request_schema_invalid", "Request is invalid.")
        except (TypeError, ValueError):
            return self._error(
                503,
                "provider_probe_unavailable",
                "Provider probe is temporarily unavailable.",
            )
        except Exception:
            return self._error(503, "provider_probe_unavailable", "Provider probe is temporarily unavailable.")


__all__ = ["AutoIPProbeHttpApi"]
