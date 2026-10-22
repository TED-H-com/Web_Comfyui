"""Framework-neutral HTTP contract for owner-scoped offline imports."""

from __future__ import annotations

import re
import secrets
import time
from typing import Any, Callable, Mapping

from .http_api import HttpResponse
from .import_storage import (
    ImportStageError,
    ImportStageNotFound,
    PrivateImportStageStore,
)
from .media_materializer import MediaMaterializerError, PrivateProviderMediaStore
from .owner_import import OwnerImportPlanError, build_owner_import_decision_plan
from .owner_import_renditions import (
    OwnerImportRenditionError,
    build_owner_profile_renditions,
)
from .owner_import_previews import build_owner_asset_previews
from .owner_import_confirm import OwnerImportConfirmationError
from .owner_import_coordinator import confirm_staged_owner_import
from .owner_import_store import StoreOwnerImportPlanError
from .owner_import_token import (
    OwnerImportDecisionTokenCodec,
    OwnerImportDecisionTokenError,
)
from .service import ActorContext
from .store import ProviderFabricStore, ProviderFabricStoreError


OWNER_IMPORT_HTTP_SCHEMA_VERSION = 1


class OwnerImportHttpApi:
    """Actor-bound stage/plan/confirm/cancel/receipt operations.

    The embedding web server remains responsible for session authentication,
    same-origin/CSRF enforcement, strict request size, and content-type parsing.
    """

    def __init__(
        self,
        *,
        stage_store: PrivateImportStageStore,
        media_store: PrivateProviderMediaStore,
        provider_store: ProviderFabricStore,
        decision_tokens: OwnerImportDecisionTokenCodec,
        account_id: str | Callable[[str], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(stage_store, PrivateImportStageStore):
            raise TypeError("stage_store must be a PrivateImportStageStore")
        if not isinstance(media_store, PrivateProviderMediaStore):
            raise TypeError("media_store must be a PrivateProviderMediaStore")
        if not isinstance(provider_store, ProviderFabricStore):
            raise TypeError("provider_store must be a ProviderFabricStore")
        if not isinstance(decision_tokens, OwnerImportDecisionTokenCodec):
            raise TypeError("decision_tokens must be an OwnerImportDecisionTokenCodec")
        if not isinstance(account_id, str) and not callable(account_id):
            raise TypeError("account_id must be a stable identifier or resolver")
        if isinstance(account_id, str) and not re.fullmatch(
            r"[a-z0-9][a-z0-9._:-]{0,127}", account_id
        ):
            raise ValueError("account_id is invalid")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._stages = stage_store
        self._media = media_store
        self._store = provider_store
        self._tokens = decision_tokens
        self._account_resolver = (
            (lambda _owner: account_id) if isinstance(account_id, str) else account_id
        )
        self._clock = clock

    @staticmethod
    def _response(body: Mapping[str, Any], status: int = 200) -> HttpResponse:
        return HttpResponse(status, body)

    @staticmethod
    def _error(code: str, message: str, status: int) -> HttpResponse:
        return HttpResponse(
            status,
            {"error": {"code": code, "message": message, "reason_codes": []}},
        )

    @staticmethod
    def _actor(actor: ActorContext | None) -> ActorContext:
        if not isinstance(actor, ActorContext):
            raise PermissionError("authentication_required")
        if actor.role != "admin":
            raise PermissionError("administrator_required")
        return actor

    @staticmethod
    def _exact(value: Any, keys: frozenset[str]) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or set(value) != keys:
            raise ValueError("request_schema_invalid")
        return value

    def _owned_account(self, actor: ActorContext) -> str:
        try:
            account_id = self._account_resolver(actor.user_id)
        except Exception:
            raise ImportStageNotFound("provider import stage was not found") from None
        if not isinstance(account_id, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._:-]{0,127}", account_id
        ):
            raise ImportStageNotFound("provider import stage was not found")
        try:
            account = self._store.get_owned_provider_account(
                actor.user_id, account_id
            )
        except KeyError:
            try:
                account = self._store.register_owned_manual_import_account(
                    actor.user_id, account_id, "ClipMode imported library"
                )
            except (ValueError, ProviderFabricStoreError):
                raise ImportStageNotFound(
                    "provider import stage was not found"
                ) from None
        if account["provider_id"] != "clipmode":
            raise ImportStageNotFound("provider import stage was not found")
        return account_id

    @staticmethod
    def _suggested_decision(staged, decision) -> dict[str, Any]:
        """Propose declared profile media without turning it into authority."""

        characters = {item.record_id: item for item in staged.bundle.characters}
        media = {item.record_id: item for item in staged.bundle.media}
        selections: list[dict[str, str]] = []
        for character in decision.characters:
            records = sorted(
                (characters[item] for item in character.source_record_ids),
                key=lambda item: (
                    item.updated_at or "",
                    item.created_at or "",
                    item.external_ref.version,
                    item.record_id,
                ),
                reverse=True,
            )
            selected = next(
                (
                    media[item.profile_media_record_id]
                    for item in records
                    if item.profile_media_record_id in media
                ),
                None,
            )
            if (
                selected is None
                or selected.kind.value != "image"
                or selected.availability != "bundled"
                or selected.mime_type not in {"image/jpeg", "image/png", "image/webp"}
                or len(selected.character_record_ids) != 1
                or selected.character_record_ids[0] not in character.source_record_ids
            ):
                continue
            selections.append(
                {
                    "source_character_key": character.source_character_key,
                    "media_record_id": selected.record_id,
                }
            )
        return {
            "schema_version": 1,
            "character_mappings": [],
            "profile_selections": selections,
        }

    @staticmethod
    def _receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": OWNER_IMPORT_HTTP_SCHEMA_VERSION,
            "receipt_id": receipt["receipt_id"],
            "state": receipt["state"],
            "replayed": bool(receipt.get("replayed", False)),
            "catalog_state": "available",
            "membership_counts": dict(receipt["membership_counts"]),
            "provider_transport_performed": False,
            "credits_spent": 0,
        }

    def _translate(self, callback: Callable[[], Mapping[str, Any]]) -> HttpResponse:
        try:
            return self._response(callback())
        except PermissionError as error:
            code = str(error)
            return self._error(
                code,
                "Authentication is required."
                if code == "authentication_required"
                else "Administrator access is required.",
                401 if code == "authentication_required" else 403,
            )
        except ImportStageNotFound:
            return self._error("import_stage_not_found", "Import stage was not found.", 404)
        except OwnerImportDecisionTokenError as error:
            return self._error(error.code, str(error), 409)
        except (OwnerImportPlanError, StoreOwnerImportPlanError) as error:
            return self._error(getattr(error, "code", "import_plan_invalid"), str(error), 409)
        except OwnerImportConfirmationError as error:
            return self._error(error.code, str(error), 409)
        except OwnerImportRenditionError as error:
            return self._error(error.code, str(error), 409)
        except (ImportStageError, MediaMaterializerError, ProviderFabricStoreError):
            return self._error("import_unavailable", "Import is temporarily unavailable.", 409)
        except (TypeError, ValueError):
            return self._error("request_schema_invalid", "Request is invalid.", 400)

    def post_stage(
        self,
        actor: ActorContext | None,
        *,
        source: bytes,
        content_type: str,
    ) -> HttpResponse:
        def operation() -> Mapping[str, Any]:
            current = self._actor(actor)
            account_id = self._owned_account(current)
            staged = self._stages.create_stage(
                current.user_id, account_id, content_type, source
            )
            empty_request = {
                "schema_version": 1,
                "character_mappings": [],
                "profile_selections": [],
            }
            empty_decision = build_owner_import_decision_plan(
                staged.bundle,
                empty_request,
                owner_user_id=current.user_id,
                account_id=account_id,
            )
            suggested_request = self._suggested_decision(staged, empty_decision)
            decision = build_owner_import_decision_plan(
                staged.bundle,
                suggested_request,
                owner_user_id=current.user_id,
                account_id=account_id,
            )
            return {
                **staged.to_public_dict(),
                "stage_token": staged.token,
                "default_review": decision.to_public_dict(),
                "suggested_decision": suggested_request,
                "confirmation_available": False,
                "provider_transport_performed": False,
                "credits_spent": 0,
            }

        return self._translate(operation)

    def post_plan(
        self,
        actor: ActorContext | None,
        stage_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        def operation() -> Mapping[str, Any]:
            current = self._actor(actor)
            account_id = self._owned_account(current)
            request = self._exact(
                body, frozenset({"schema_version", "stage_token", "decision"})
            )
            if request["schema_version"] != OWNER_IMPORT_HTTP_SCHEMA_VERSION:
                raise ValueError("request_schema_invalid")
            staged = self._stages.load_stage(
                current.user_id,
                account_id,
                stage_id,
                request["stage_token"],
            )
            decision = build_owner_import_decision_plan(
                staged.bundle,
                request["decision"],
                owner_user_id=current.user_id,
                account_id=account_id,
            )
            profile_renditions = build_owner_profile_renditions(staged, decision)
            store_plan = self._store.plan_owned_provider_import(
                current.user_id,
                account_id,
                staged.bundle,
                request["decision"],
                decision,
                profile_renditions=profile_renditions,
                asset_previews=build_owner_asset_previews(
                    staged, decision, profile_renditions
                ),
            )
            if store_plan.active_receipt_id is not None:
                receipt = self._store.get_owned_provider_import_receipt(
                    current.user_id, store_plan.active_receipt_id
                )
                return {
                    **store_plan.to_public_dict(),
                    "already_imported": True,
                    "confirmation_available": False,
                    "commit_available": False,
                    "receipt": self._receipt(receipt),
                }
            token = self._tokens.issue(
                staged, decision, store_plan, now=int(self._clock())
            )
            public = store_plan.to_public_dict()
            return {
                **public,
                "confirmation_available": True,
                "commit_available": True,
                "decision_token": token,
                "expires_at": min(staged.expires_at, int(self._clock()) + 15 * 60),
            }

        return self._translate(operation)

    def post_confirm(
        self,
        actor: ActorContext | None,
        stage_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        def operation() -> Mapping[str, Any]:
            current = self._actor(actor)
            account_id = self._owned_account(current)
            request = self._exact(
                body,
                frozenset(
                    {
                        "schema_version",
                        "stage_token",
                        "decision_token",
                        "decision",
                        "confirmed",
                    }
                ),
            )
            if (
                request["schema_version"] != OWNER_IMPORT_HTTP_SCHEMA_VERSION
                or request["confirmed"] is not True
            ):
                raise ValueError("request_schema_invalid")
            owner_binding = self._stages.owner_binding(current.user_id)
            verified = self._tokens.verify(
                request["decision_token"],
                owner_binding=owner_binding,
                stage_id=stage_id,
                stage_token=request["stage_token"],
                now=int(self._clock()),
            )
            if verified.account_id != account_id:
                raise OwnerImportDecisionTokenError(
                    "decision_token_invalid", "Decision token is invalid."
                )
            try:
                consumed = self._stages.load_consumed_stage(
                    current.user_id,
                    account_id,
                    stage_id,
                    request["stage_token"],
                    import_decision_sha256=verified.import_decision_sha256,
                    decision_plan_sha256=verified.decision_plan_sha256,
                    plan_sha256=verified.store_plan_sha256,
                    stable_graph_sha256=verified.stable_graph_sha256,
                )
            except ImportStageNotFound:
                consumed = None
            if consumed is not None:
                receipt = self._store.get_owned_provider_import_receipt(
                    current.user_id, consumed.receipt_id
                )
                receipt["replayed"] = True
                return self._receipt(receipt)

            staged = self._stages.load_stage(
                current.user_id,
                account_id,
                stage_id,
                request["stage_token"],
            )
            decision = build_owner_import_decision_plan(
                staged.bundle,
                request["decision"],
                owner_user_id=current.user_id,
                account_id=account_id,
            )
            evidence = {
                "raw_source_sha256": decision.raw_source_sha256,
                "manifest_sha256": decision.manifest_sha256,
                "semantic_sha256": decision.semantic_sha256,
                "mapping_sha256": decision.mapping_sha256,
                "profile_selection_sha256": decision.profile_selection_sha256,
                "account_binding_sha256": decision.account_binding_sha256,
                "import_decision_sha256": decision.import_decision_sha256,
                "decision_plan_sha256": decision.plan_sha256,
            }
            if any(getattr(verified, key) != value for key, value in evidence.items()):
                raise OwnerImportDecisionTokenError(
                    "decision_token_invalid", "Decision token is invalid."
                )
            receipt = confirm_staged_owner_import(
                stage_store=self._stages,
                media_store=self._media,
                provider_store=self._store,
                owner_user_id=current.user_id,
                account_id=account_id,
                stage_id=stage_id,
                stage_token=request["stage_token"],
                receipt_id=f"receipt:{secrets.token_hex(16)}",
                request=request["decision"],
                decision_plan=decision,
                expected_plan_sha256=verified.store_plan_sha256,
                expected_graph_sha256=verified.graph_sha256,
                expected_stable_graph_sha256=verified.stable_graph_sha256,
                expected_snapshot_sha256=verified.snapshot_sha256,
            )
            return self._receipt(receipt)

        return self._translate(operation)

    def delete_stage(
        self,
        actor: ActorContext | None,
        stage_id: str,
        body: Mapping[str, Any],
    ) -> HttpResponse:
        def operation() -> Mapping[str, Any]:
            current = self._actor(actor)
            account_id = self._owned_account(current)
            request = self._exact(
                body, frozenset({"schema_version", "stage_token"})
            )
            if request["schema_version"] != OWNER_IMPORT_HTTP_SCHEMA_VERSION:
                raise ValueError("request_schema_invalid")
            self._stages.cancel_stage(
                current.user_id,
                account_id,
                stage_id,
                request["stage_token"],
            )
            return {"schema_version": OWNER_IMPORT_HTTP_SCHEMA_VERSION, "cancelled": True}

        return self._translate(operation)

    def get_receipt(
        self, actor: ActorContext | None, receipt_id: str
    ) -> HttpResponse:
        def operation() -> Mapping[str, Any]:
            current = self._actor(actor)
            try:
                receipt = self._store.get_owned_provider_import_receipt(
                    current.user_id, receipt_id
                )
            except KeyError:
                raise ImportStageNotFound("provider import stage was not found") from None
            return self._receipt(receipt)

        return self._translate(operation)


__all__ = ["OWNER_IMPORT_HTTP_SCHEMA_VERSION", "OwnerImportHttpApi"]
