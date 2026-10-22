"""Actor-bound HTTP adapter for explicit read-only AutoIP catalog sync."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from .autoip import AUTOIP_API_BASE, AutoIPClient
from .autoip_catalog_sync import (
    AutoIPCatalogSnapshotStore, public_autoip_catalog_snapshot, sync_autoip_catalog,
)
from .autoip_readiness import AutoIPCompatibilityReceiptStore
from .autoip_history_adoption import (
    AutoIPHistoryAdoptionError,
    adopt_autoip_history_task,
)
from .config import ProviderConfig
from .http_api import HttpResponse
from .service import ActorContext
from .store import (
    ProviderFabricStore,
    ProviderFabricStoreError,
    StoreConflictError,
)


class AutoIPCatalogHttpApi:
    def __init__(
        self,
        provider_store: ProviderFabricStore,
        receipt_store: AutoIPCompatibilityReceiptStore,
        snapshot_store: AutoIPCatalogSnapshotStore,
        history_staging_root: Path,
        *,
        client_factory: Callable[[ProviderConfig], AutoIPClient] = AutoIPClient,
    ) -> None:
        if not isinstance(provider_store, ProviderFabricStore):
            raise TypeError("provider_store must be a ProviderFabricStore")
        if not isinstance(receipt_store, AutoIPCompatibilityReceiptStore):
            raise TypeError("receipt_store must be an AutoIPCompatibilityReceiptStore")
        if not isinstance(snapshot_store, AutoIPCatalogSnapshotStore):
            raise TypeError("snapshot_store must be an AutoIPCatalogSnapshotStore")
        if not callable(client_factory):
            raise TypeError("client_factory must be callable")
        self._provider_store = provider_store
        self._receipt_store = receipt_store
        self._snapshot_store = snapshot_store
        self._history_staging_root = Path(history_staging_root).expanduser().absolute()
        self._client_factory = client_factory

    @staticmethod
    def _actor(actor: ActorContext | None) -> ActorContext:
        if actor is None:
            raise PermissionError("authentication_required")
        if actor.role not in {"admin", "operator"}:
            raise PermissionError("administrator_required")
        return actor

    @staticmethod
    def _error(status: int, code: str, message: str) -> HttpResponse:
        return HttpResponse(status, {
            "error": {"code": code, "message": message, "reason_codes": []},
        })

    def _account(self, actor: ActorContext | None, account_id: str):
        current = self._actor(actor)
        account = self._provider_store.get_owned_provider_account(current.user_id, account_id)
        if account.get("provider_id") != "autoip":
            raise KeyError(account_id)
        return current, account

    def _adopted_task_ids(self, owner_user_id: str, account_id: str) -> frozenset[str]:
        result = set()
        after = None
        for _page in range(20):
            page = self._provider_store.list_owned_autoip_history_adoptions(
                owner_user_id, account_id, after=after, limit=100,
            )
            for item in page["items"]:
                task_id = item.get("task_uuid")
                if isinstance(task_id, str):
                    result.add(task_id)
            after = page["next_after"]
            if after is None:
                break
        return frozenset(result)

    def _public_snapshot(self, owner_user_id: str, account_id: str, snapshot):
        return public_autoip_catalog_snapshot(
            snapshot,
            adopted_task_ids=self._adopted_task_ids(owner_user_id, account_id),
        )

    def post_sync(
        self, actor: ActorContext | None, account_id: str, body: Mapping[str, object],
    ) -> HttpResponse:
        try:
            current, _account = self._account(actor, account_id)
            if set(body) != {"schema_version"} or body.get("schema_version") != 1:
                raise ValueError("request_schema_invalid")
            receipt = self._receipt_store.current(current.user_id, account_id)
            if receipt is None:
                return self._error(
                    409, "compatibility_probe_required",
                    "Run a current non-spending compatibility probe before synchronizing.",
                )
            reference = self._provider_store.credential_reference_for_actor(
                current.user_id, account_id,
            )
            client = self._client_factory(ProviderConfig(
                provider_id="autoip", api_base=AUTOIP_API_BASE,
                api_key_ref=reference, enabled=False,
            ))
            snapshot = sync_autoip_catalog(
                client, self._snapshot_store, current.user_id, account_id,
                compatibility_receipt=receipt,
            )
            return HttpResponse(200, self._public_snapshot(current.user_id, account_id, snapshot))
        except PermissionError as error:
            code = str(error)
            return self._error(
                401 if code == "authentication_required" else 403, code,
                "Authentication is required." if code == "authentication_required"
                else "Administrator access is required.",
            )
        except (KeyError, ProviderFabricStoreError):
            return self._error(404, "provider_account_not_found", "Provider account was not found.")
        except (TypeError, ValueError):
            return self._error(400, "request_schema_invalid", "Request is invalid.")
        except Exception:
            return self._error(503, "catalog_sync_unavailable", "AutoIP catalog sync is temporarily unavailable.")

    def get_snapshot(self, actor: ActorContext | None, account_id: str) -> HttpResponse:
        try:
            current, _account = self._account(actor, account_id)
            snapshot = self._snapshot_store.get(current.user_id, account_id)
            return HttpResponse(200, self._public_snapshot(current.user_id, account_id, snapshot))
        except PermissionError as error:
            code = str(error)
            return self._error(
                401 if code == "authentication_required" else 403, code,
                "Authentication is required." if code == "authentication_required"
                else "Administrator access is required.",
            )
        except FileNotFoundError:
            return self._error(404, "catalog_not_synced", "AutoIP catalog has not been synchronized.")
        except (KeyError, ProviderFabricStoreError):
            return self._error(404, "provider_account_not_found", "Provider account was not found.")
        except Exception:
            return self._error(503, "catalog_unavailable", "AutoIP catalog is temporarily unavailable.")

    def post_adopt_task(
        self, actor: ActorContext | None, account_id: str, task_uuid: str,
        body: Mapping[str, object],
    ) -> HttpResponse:
        try:
            current, _account = self._account(actor, account_id)
            if set(body) != {"schema_version", "confirmed"} or body.get("schema_version") != 1 or body.get("confirmed") is not True:
                raise ValueError("request_schema_invalid")
            if self._receipt_store.current(current.user_id, account_id) is None:
                return self._error(
                    409, "compatibility_probe_required",
                    "Run a current non-spending compatibility probe before adopting history.",
                )
            reference = self._provider_store.credential_reference_for_actor(
                current.user_id, account_id,
            )
            client = self._client_factory(ProviderConfig(
                provider_id="autoip", api_base=AUTOIP_API_BASE,
                api_key_ref=reference,
                # This dedicated adapter invokes only exact task/output GETs.
                # The client flag is needed for authenticated streaming; no
                # generation method is exposed by this HTTP surface.
                enabled=True,
            ))
            result = adopt_autoip_history_task(
                self._provider_store, self._snapshot_store, client,
                self._history_staging_root,
                owner_user_id=current.user_id, account_id=account_id,
                task_uuid=task_uuid,
            )
            return HttpResponse(201, result)
        except PermissionError as error:
            code = str(error)
            return self._error(
                401 if code == "authentication_required" else 403, code,
                "Authentication is required." if code == "authentication_required"
                else "Administrator access is required.",
            )
        except KeyError:
            return self._error(404, "provider_account_not_found", "Provider account was not found.")
        except (
            AutoIPHistoryAdoptionError,
            StoreConflictError,
            TypeError,
            ValueError,
        ):
            return self._error(422, "history_adoption_unavailable", "Completed task outputs could not be safely adopted.")
        except ProviderFabricStoreError:
            return self._error(503, "history_adoption_unavailable", "AutoIP history adoption is temporarily unavailable.")
        except Exception:
            return self._error(503, "history_adoption_unavailable", "AutoIP history adoption is temporarily unavailable.")


__all__ = ["AutoIPCatalogHttpApi"]
