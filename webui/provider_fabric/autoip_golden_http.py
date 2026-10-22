"""Admin-only HTTP adapter for issuing one exact AutoIP golden."""

from __future__ import annotations

from typing import Mapping

from .autoip_golden_issue import AutoIPGoldenIssueError, REQUIRED_GATES, autoip_golden_candidates, issue_autoip_golden
from .autoip_golden_store import AutoIPGoldenEvidenceStore
from .autoip_readiness import AutoIPCompatibilityReceiptStore
from .http_api import HttpResponse
from .service import ActorContext
from .store import ProviderFabricStore, ProviderFabricStoreError


class AutoIPGoldenHttpApi:
    def __init__(self, store: ProviderFabricStore, receipts: AutoIPCompatibilityReceiptStore, goldens: AutoIPGoldenEvidenceStore) -> None:
        if not isinstance(store, ProviderFabricStore):
            raise TypeError("store must be a ProviderFabricStore")
        self._store, self._receipts, self._goldens = store, receipts, goldens

    @staticmethod
    def _error(status: int, code: str, message: str) -> HttpResponse:
        return HttpResponse(status, {"error": {"code": code, "message": message, "reason_codes": []}})

    def post_issue(self, actor: ActorContext | None, account_id: str, body: Mapping[str, object]) -> HttpResponse:
        try:
            if actor is None:
                raise PermissionError("authentication_required")
            if actor.role not in {"admin", "operator"}:
                raise PermissionError("administrator_required")
            required = {"schema_version", "projection_ids", "gate_evidence", "review_evidence_ids", "acceptance_phrase"}
            if set(body) != required or body.get("schema_version") != 1:
                raise ValueError("request_schema_invalid")
            account = self._store.get_owned_provider_account(actor.user_id, account_id)
            if account.get("provider_id") != "autoip":
                raise KeyError(account_id)
            compatibility = self._receipts.current(actor.user_id, account_id)
            if compatibility is None:
                return self._error(409, "autoip_compatibility_not_current", "Run the non-spending AutoIP probe again before review.")
            projections, reviews, gates = body["projection_ids"], body["review_evidence_ids"], body["gate_evidence"]
            if not isinstance(projections, list) or not isinstance(reviews, list) or not isinstance(gates, Mapping):
                raise ValueError("request_schema_invalid")
            result = issue_autoip_golden(
                self._store, self._goldens, owner_user_id=actor.user_id,
                account_id=account_id, compatibility_receipt=compatibility,
                projection_ids=tuple(projections), gate_evidence={
                    gate: tuple(gates.get(gate, ())) for gate in REQUIRED_GATES
                }, review_evidence_ids=tuple(reviews),
                acceptance_phrase=body["acceptance_phrase"], reviewer_id="operator",
            )
            return HttpResponse(201, {"schema_version": 1, **result, "generation_enabled": True})
        except PermissionError as error:
            code = str(error)
            return self._error(401 if code == "authentication_required" else 403, code, "Authentication is required." if code == "authentication_required" else "Administrator access is required.")
        except (KeyError, ProviderFabricStoreError):
            return self._error(404, "provider_account_or_output_not_found", "The account or reviewed output was not found.")
        except AutoIPGoldenIssueError as error:
            return self._error(409, str(error), "The reviewed outputs do not satisfy the exact golden contract.")
        except (TypeError, ValueError):
            return self._error(400, "request_schema_invalid", "Request is invalid.")
        except Exception:
            return self._error(503, "autoip_golden_unavailable", "Golden review is temporarily unavailable.")

    def get_candidates(self, actor: ActorContext | None, account_id: str) -> HttpResponse:
        try:
            if actor is None:
                raise PermissionError("authentication_required")
            if actor.role not in {"admin", "operator"}:
                raise PermissionError("administrator_required")
            account = self._store.get_owned_provider_account(actor.user_id, account_id)
            if account.get("provider_id") != "autoip":
                raise KeyError(account_id)
            compatibility = self._receipts.current(actor.user_id, account_id)
            candidates = autoip_golden_candidates(
                self._store, owner_user_id=actor.user_id, account_id=account_id,
            )
            return HttpResponse(200, {
                "schema_version": 1, "account_id": account_id,
                "compatibility_current": compatibility is not None,
                "candidates": list(candidates), "provider_transport_performed": False,
                "credits_spent": 0,
            })
        except PermissionError as error:
            code = str(error)
            return self._error(401 if code == "authentication_required" else 403, code, "Authentication is required." if code == "authentication_required" else "Administrator access is required.")
        except (KeyError, ProviderFabricStoreError):
            return self._error(404, "provider_account_not_found", "Provider account was not found.")
        except Exception:
            return self._error(503, "autoip_golden_candidates_unavailable", "Golden candidates are temporarily unavailable.")


__all__ = ["AutoIPGoldenHttpApi"]
