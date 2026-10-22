"""Lease, poll, validate, and adopt one completed AutoIP task output set."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol

from media_projects.output_binding import bind_verified_output
from media_projects.production_store import ProductionStateStore

from .autoip import AutoIPApiError, AutoIPClient, OutputDownloadReceipt
from .models import CharacterRef, MediaKind, TaskReceipt, TaskState, VisualClass
from .store import InvalidTaskTransition, ProviderFabricStore, StoreConflictError


class ReconciliationClient(Protocol):
    def poll_task(self, task_uuid: str, *, client_request_id: str) -> TaskReceipt: ...

    def download_output_to_staging(
        self, task_uuid: str, index: int, staging_root: Path, **kwargs: object
    ) -> OutputDownloadReceipt: ...


ClientResolver = Callable[[str, str], ReconciliationClient]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    task_id: str | None = None
    task_state: str | None = None
    adopted_projection_ids: tuple[str, ...] = ()
    retry_after_seconds: int | None = None


_KIND_BY_MIME = {
    "image/avif": MediaKind.IMAGE,
    "image/gif": MediaKind.IMAGE,
    "image/jpeg": MediaKind.IMAGE,
    "image/png": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE,
    "video/mp4": MediaKind.VIDEO,
    "video/quicktime": MediaKind.VIDEO,
    "video/webm": MediaKind.VIDEO,
    "audio/mpeg": MediaKind.AUDIO,
    "audio/wav": MediaKind.AUDIO,
    "audio/x-wav": MediaKind.AUDIO,
    "audio/flac": MediaKind.AUDIO,
    "audio/mp4": MediaKind.AUDIO,
    "audio/ogg": MediaKind.AUDIO,
}


def _lease_deadline(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace(
        "+00:00", "Z"
    )


def _stable_suffix(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:32]


def _rate_limit_delay(error: AutoIPApiError) -> int | None:
    """Return a validated provider delay for HTTP 429, when one was supplied."""

    if error.status != 429 or error.retry_after is None:
        return None
    try:
        seconds = int(error.retry_after)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 86_400 else None


def _defer_rate_limited_task(
    store: ProviderFabricStore,
    *,
    owner_user_id: str,
    task_id: str,
    task_state: str,
    lease_owner: str,
    error: AutoIPApiError,
    adopted_projection_ids: tuple[str, ...] = (),
) -> ReconciliationResult:
    """Release a read-only reconciliation lease without changing task state.

    A provider status/download GET returning 429 says nothing about the
    already-submitted generation. Keeping the durable task nonterminal lets a
    later worker poll the same provider task; no submission path is involved.
    """

    store.release_owned_reconciliation(
        owner_user_id,
        task_id,
        lease_owner=lease_owner,
    )
    return ReconciliationResult(
        "rate_limited",
        task_id,
        task_state,
        adopted_projection_ids,
        _rate_limit_delay(error),
    )


def _rehash(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                if total > expected_size:
                    raise ValueError("adopted output grew after validation")
                digest.update(chunk)
    except OSError as error:
        raise ValueError("validated output is unavailable") from error
    if total != expected_size:
        raise ValueError("adopted output size changed")
    return digest.hexdigest()


def adopt_output(
    store: ProviderFabricStore,
    *,
    owner_user_id: str,
    account_id: str,
    intent_record: Mapping[str, object],
    task_id: str,
    receipt: OutputDownloadReceipt,
    staging_root: Path,
    production_store: ProductionStateStore | None = None,
) -> str:
    """Idempotently project one validated local output into the unified library."""

    if not isinstance(receipt, OutputDownloadReceipt):
        raise TypeError("receipt must be an OutputDownloadReceipt")
    root = Path(staging_root).resolve(strict=True)
    path = (root / receipt.staging_name).resolve(strict=True)
    if path.parent != root or path.name != receipt.staging_name or not path.is_file():
        raise ValueError("validated output escaped its staging root")
    digest = _rehash(path, receipt.byte_size)
    if not hmac.compare_digest(digest, receipt.sha256):
        raise ValueError("validated output digest changed before adoption")
    kind = _KIND_BY_MIME.get(receipt.content_type)
    if kind is None:
        raise ValueError("validated output MIME has no Castle media kind")
    intent = intent_record.get("intent")
    if not isinstance(intent, Mapping):
        raise ValueError("intent record is invalid")
    visual_raw = intent.get("visual_class")
    visual = VisualClass(str(visual_raw))
    characters = intent.get("characters")
    if not isinstance(characters, list):
        raise ValueError("intent characters are invalid")
    character_ids = []
    for character in characters:
        if not isinstance(character, Mapping) or not isinstance(character.get("character_id"), str):
            raise ValueError("intent character identity is invalid")
        character_ids.append(str(character["character_id"]))
    suffix = _stable_suffix(receipt.task_uuid, str(receipt.index))
    projection_id = f"autoip-output:{suffix}"
    version_id = f"assetver:{suffix}"
    external_asset_id = f"{receipt.task_uuid}:output:{receipt.index}"
    intent_id = str(intent_record.get("intent_id") or "")
    metadata = {
        "source": "autoip_generation",
        "intent_id": intent_id,
        "task_id": task_id,
        "output_index": receipt.index,
    }
    store.project_owned_remote_asset(
        owner_user_id,
        projection_id,
        account_id,
        external_asset_id,
        kind,
        visual_class=visual,
        metadata=metadata,
    )
    existing = store.get_owned_remote_asset(owner_user_id, projection_id)
    versions = [item for item in existing["versions"] if item["version_id"] == version_id]
    if not versions:
        try:
            store.add_owned_remote_asset_version(
                owner_user_id,
                projection_id,
                path.as_uri(),
                version_id=version_id,
                version_number=1,
                provider_version_id=f"{receipt.task_uuid}:{receipt.index}",
                mime_type=receipt.content_type,
                byte_size=receipt.byte_size,
                sha256=receipt.sha256,
                metadata={"validated_download": True},
            )
        except StoreConflictError:
            pass
        existing = store.get_owned_remote_asset(owner_user_id, projection_id)
        versions = [item for item in existing["versions"] if item["version_id"] == version_id]
    if len(versions) != 1 or any(
        versions[0].get(key) != value
        for key, value in {
            "version_number": 1,
            "mime_type": receipt.content_type,
            "byte_size": receipt.byte_size,
            "sha256": receipt.sha256,
        }.items()
    ):
        raise StoreConflictError("AutoIP output adoption identity conflicts")
    if kind in {MediaKind.IMAGE, MediaKind.VIDEO}:
        try:
            store.put_owned_asset_catalog_annotation(
                owner_user_id,
                projection_id,
                display_name=None,
                audio_role="unclassified",
                media_role="finished",
                expected_revision=0,
            )
        except StoreConflictError:
            # Never replace an owner's catalog edit during receipt replay.
            pass
    character_by_id = {str(item["character_id"]): item for item in characters}
    for character_id in character_ids:
        character = character_by_id[character_id]
        store.put_owned_character(
            owner_user_id,
            CharacterRef(
                character_id,
                visual,
                str(character.get("display_name") or character_id),
            ),
        )
        store.link_owned_asset_character(owner_user_id, projection_id, character_id)
    instruction = intent.get("creative_instruction")
    if production_store is not None and isinstance(instruction, Mapping):
        bind_verified_output(
            production_store,
            owner_user_id=owner_user_id,
            instruction=instruction,
            asset_version_id=version_id,
            asset_sha256=receipt.sha256,
            task_id=task_id,
        )
    return projection_id


def run_once(
    store: ProviderFabricStore,
    client_resolver: ClientResolver,
    staging_root: Path,
    *,
    lease_owner: str,
    lease_seconds: int = 300,
    production_store: ProductionStateStore | None = None,
    account_id: str | None = None,
    external_task_id: str | None = None,
) -> ReconciliationResult:
    """Reconcile at most one task; never resubmit provider generation work.

    ``external_task_id`` is used by the webhook entrypoint only after signature
    verification and an exact durable-task lookup.  It narrows the normal GET
    reconciliation worker; no webhook status, output URL, or billing field is
    accepted as task state.
    """

    if not isinstance(store, ProviderFabricStore) or not callable(client_resolver):
        raise TypeError("store and client_resolver are required")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 30 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    for locator in store.list_owned_reconcilable_tasks(
        provider_id="autoip",
        account_id=account_id,
        external_task_id=external_task_id,
        limit=1 if external_task_id is not None else 16,
    ):
        try:
            task = store.claim_owned_reconciliation(
                locator["owner_user_id"],
                locator["task_id"],
                lease_owner=lease_owner,
                lease_expires_at=_lease_deadline(lease_seconds),
                allow_terminal_correction=external_task_id is not None,
            )
        except StoreConflictError:
            continue
        owner = locator["owner_user_id"]
        task_id = locator["task_id"]
        outbox = store.get_outbox(str(task["outbox_id"]))
        intent_record = store.get_intent(str(outbox["intent_id"]))
        client = client_resolver(owner, str(task["account_id"]))
        if not isinstance(client, AutoIPClient) and not all(
            callable(getattr(client, name, None))
            for name in ("poll_task", "download_output_to_staging")
        ):
            raise TypeError("reconciliation client is unavailable")
        try:
            receipt = client.poll_task(
                str(task["external_task_id"]),
                client_request_id=str(task["client_request_id"]),
            )
        except AutoIPApiError as error:
            if error.status != 429:
                raise
            return _defer_rate_limited_task(
                store,
                owner_user_id=owner,
                task_id=task_id,
                task_state=str(task["state"]),
                lease_owner=lease_owner,
                error=error,
            )
        if (
            receipt.provider_id != task["provider_id"]
            or receipt.client_request_id != task["client_request_id"]
            or receipt.external_task_id != task["external_task_id"]
        ):
            raise ValueError("provider reconciliation receipt does not match the durable task")
        adopted: list[str] = []
        if receipt.state is TaskState.COMPLETED:
            indexes = []
            for output in receipt.outputs:
                index = output.get("index")
                if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                    raise ValueError("provider output index is invalid")
                indexes.append(index)
            if not indexes or len(indexes) != len(set(indexes)):
                raise ValueError("completed provider output set is empty or ambiguous")
            for index in sorted(indexes):
                try:
                    download = client.download_output_to_staging(
                        str(task["external_task_id"]), index, Path(staging_root)
                    )
                except AutoIPApiError as error:
                    if error.status != 429:
                        raise
                    return _defer_rate_limited_task(
                        store,
                        owner_user_id=owner,
                        task_id=task_id,
                        task_state=str(task["state"]),
                        lease_owner=lease_owner,
                        error=error,
                        adopted_projection_ids=tuple(adopted),
                    )
                adopted.append(adopt_output(
                    store,
                    owner_user_id=owner,
                    account_id=str(task["account_id"]),
                    intent_record=intent_record,
                    task_id=task_id,
                    receipt=download,
                    staging_root=Path(staging_root),
                    production_store=production_store,
                ))
        durable_credits = task.get("credits_charged")
        durable_balance = task.get("remaining_credits")
        if (
            receipt.credits_charged is not None
            and durable_credits is not None
            and receipt.credits_charged != durable_credits
        ):
            raise StoreConflictError(
                "AutoIP charge receipt conflicts with the durable task"
            )
        if (
            receipt.remaining_credits is not None
            and durable_balance is not None
            and receipt.remaining_credits != durable_balance
        ):
            raise StoreConflictError(
                "AutoIP balance receipt conflicts with the durable task"
            )
        effective_credits = (
            receipt.credits_charged
            if receipt.credits_charged is not None
            else durable_credits
        )
        effective_balance = (
            receipt.remaining_credits
            if receipt.remaining_credits is not None
            else durable_balance
        )
        charge_balance_after: int | None = None
        if effective_credits is not None:
            charge_suffix = _stable_suffix(
                str(task["account_id"]), str(task["external_task_id"]), "charge"
            )
            charge_id = f"credit:{charge_suffix}"
            charge_detail = {"source": "autoip_reconciliation"}
            try:
                existing_charge = store.get_credit_entry(charge_id)
            except KeyError:
                created_charge = store.append_owned_credit_entry(
                    owner,
                    str(task["account_id"]),
                    "charge",
                    -effective_credits,
                    f"{task['external_task_id']}:charge",
                    ledger_id=charge_id,
                    balance_after=effective_balance,
                    task_id=task_id,
                    intent_id=str(outbox["intent_id"]),
                    detail=charge_detail,
                )
                charge_balance_after = created_charge.get("balance_after")
            else:
                # A nonterminal receipt may report the immutable charge before
                # AutoIP reports its final balance.  Never rewrite that charge
                # or append it twice when completion later adds the balance.
                # A changed non-null balance is still an immutable conflict.
                expected_charge = {
                    "account_id": str(task["account_id"]),
                    "task_id": task_id,
                    "intent_id": str(outbox["intent_id"]),
                    "entry_kind": "charge",
                    "delta": -effective_credits,
                    "idempotency_key": f"{task['external_task_id']}:charge",
                    "detail": charge_detail,
                }
                if any(
                    existing_charge.get(key) != value
                    for key, value in expected_charge.items()
                ) or (
                    effective_balance is not None
                    and existing_charge.get("balance_after") not in {
                        None, effective_balance
                    }
                ):
                    raise StoreConflictError(
                        "AutoIP charge receipt conflicts with the immutable ledger"
                    )
                charge_balance_after = existing_charge.get("balance_after")
        if (
            receipt.state is TaskState.COMPLETED
            and effective_balance is not None
            and charge_balance_after is None
        ):
            # balance_snapshot is the append-only refinement for providers
            # that disclose a charge first and the final balance only at
            # completion.  Its zero delta cannot charge the account again.
            balance_suffix = _stable_suffix(
                str(task["account_id"]),
                str(task["external_task_id"]),
                "completed_balance",
            )
            store.append_owned_credit_entry(
                owner,
                str(task["account_id"]),
                "balance_snapshot",
                0,
                f"{task['external_task_id']}:completed_balance",
                ledger_id=f"credit:{balance_suffix}",
                balance_after=effective_balance,
                task_id=task_id,
                intent_id=str(outbox["intent_id"]),
                detail={
                    "source": "autoip_reconciliation",
                    "state": "completed",
                },
            )
        # Re-read after provider IO and output adoption: a webhook/poller may
        # have reached a terminal state while this worker held its receipt.
        # A later authoritative completion may correct only a failed task for
        # this exact already-durable provider ID; no submission path is called.
        final_state: TaskState | None = None
        for attempt in range(2):
            current = store.get_owned_task(owner, task_id)
            current_state = TaskState(str(current["state"]))
            if receipt.state is current_state:
                try:
                    store.release_owned_reconciliation(
                        owner, task_id, lease_owner=lease_owner
                    )
                except StoreConflictError:
                    # A competing terminal transition clears the same lease.
                    pass
                final_state = current_state
                break
            if (
                receipt.state is TaskState.COMPLETED
                and current_state is TaskState.FAILED
            ):
                corrected = store.reconcile_owned_completed_task(
                    owner,
                    task_id,
                    external_task_id=receipt.external_task_id,
                    credits_charged=receipt.credits_charged,
                    remaining_credits=receipt.remaining_credits,
                    detail=receipt.detail,
                    lease_owner=lease_owner,
                )
                final_state = TaskState(str(corrected["state"]))
                break
            if current_state.terminal:
                try:
                    store.release_owned_reconciliation(
                        owner, task_id, lease_owner=lease_owner
                    )
                except StoreConflictError:
                    pass
                final_state = current_state
                break
            try:
                updated = store.transition_owned_task(
                    owner,
                    task_id,
                    receipt.state,
                    external_task_id=receipt.external_task_id,
                    credits_charged=receipt.credits_charged,
                    remaining_credits=receipt.remaining_credits,
                    detail=receipt.detail,
                )
            except InvalidTaskTransition:
                if attempt == 0:
                    continue
                raise
            final_state = TaskState(str(updated["state"]))
            break
        if final_state is None:  # pragma: no cover - defensive loop invariant
            raise StoreConflictError("provider task state could not be reconciled")
        return ReconciliationResult(
            "processed", task_id, final_state.value, tuple(adopted)
        )
    return ReconciliationResult("idle")


__all__ = [
    "ClientResolver", "ReconciliationResult", "adopt_output", "run_once",
]
