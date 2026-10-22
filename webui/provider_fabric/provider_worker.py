"""At-most-once worker boundary for confirmed provider submissions.

The module contains no polling loop and no provider implementation.  A service
wrapper must explicitly invoke ``run_once`` with a processor that can rebuild
and revalidate one immutable request.  Once claimed, unexpected processor
failure is conservatively terminal-uncertain and can never be blindly retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Protocol, Sequence

from .models import TaskReceipt, TaskState
from .store import ProviderFabricStore, StoreConflictError


class DefiniteLocalFailure(RuntimeError):
    """The provider transport was definitely not attempted."""


class DefiniteProviderRejection(DefiniteLocalFailure):
    """The provider definitively rejected the POST without creating a task.

    Only inert machine-readable fields are retained.  Provider response bodies
    and messages are deliberately excluded so a reflected credential or input
    can never reach the durable task detail.
    """

    def __init__(
        self,
        status: int,
        error_code: str,
        *,
        retry_after: str | None = None,
    ) -> None:
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 400 <= status < 500
            or status == 408
        ):
            raise ValueError("status is not a definite provider rejection")
        normalized_code = str(error_code or "provider_error").strip().lower()
        if (
            not normalized_code
            or len(normalized_code) > 80
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in normalized_code
            )
        ):
            normalized_code = "provider_error"
        normalized_retry_after: int | None = None
        if retry_after is not None:
            try:
                candidate = int(retry_after)
            except (TypeError, ValueError):
                candidate = -1
            if 0 <= candidate <= 86_400:
                normalized_retry_after = candidate
        self.status = status
        self.error_code = normalized_code
        self.retry_after_seconds = normalized_retry_after
        super().__init__(self.durable_detail)

    @property
    def durable_detail(self) -> str:
        detail = (
            f"definite_provider_rejection:http_{self.status}:{self.error_code}"
        )
        if self.retry_after_seconds is not None:
            detail += f":retry_after_{self.retry_after_seconds}"
        return detail


class AmbiguousSubmission(RuntimeError):
    """Transport may have accepted the request; retry is forbidden."""


class SubmissionProcessor(Protocol):
    def __call__(self, bundle: Mapping[str, object]) -> TaskReceipt: ...


@dataclass(frozen=True, slots=True)
class WorkerResult:
    status: str
    outbox_id: str | None = None
    task_id: str | None = None
    task_state: str | None = None
    retry_after_seconds: int | None = None


def _utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def run_once(
    store: ProviderFabricStore,
    processor: SubmissionProcessor,
    *,
    lease_owner: str,
    provider_id: str | None = None,
    variant_ids: Sequence[str] | None = None,
    lease_seconds: int = 300,
) -> WorkerResult:
    """Claim at most one confirmed outbox and advance it exactly once."""

    if not isinstance(store, ProviderFabricStore) or not callable(processor):
        raise TypeError("store and processor are required")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 30 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    deferred = store.list_deferred_owned_submissions(
        provider_id=provider_id, variant_ids=variant_ids, limit=16
    )
    pending = store.list_pending_owned_submissions(
        provider_id=provider_id, variant_ids=variant_ids, limit=16
    )
    for locator in (*deferred, *pending):
        try:
            if "task_id" in locator:
                task = store.reclaim_owned_deferred_autoip_submission(
                    locator["owner_user_id"],
                    locator["task_id"],
                    lease_owner=lease_owner,
                    lease_expires_at=_utc_after(lease_seconds),
                )
            else:
                task = store.claim_owned_submission(
                    locator["owner_user_id"],
                    locator["outbox_id"],
                    lease_owner=lease_owner,
                    lease_expires_at=_utc_after(lease_seconds),
                )
        except (KeyError, StoreConflictError):
            continue
        owner_id = locator["owner_user_id"]
        outbox_id = locator["outbox_id"]
        task_id = str(task["task_id"])
        outbox = store.get_outbox(outbox_id)
        bundle = {
            "owner_user_id": owner_id,
            "outbox": outbox,
            "intent": store.get_intent(str(outbox["intent_id"])),
            "decision": store.get_route_decision(str(outbox["decision_id"])),
            "task": task,
        }
        try:
            receipt = processor(bundle)
            if not isinstance(receipt, TaskReceipt):
                raise AmbiguousSubmission("processor returned no trustworthy receipt")
            state = receipt.state
            if (
                receipt.provider_id != task["provider_id"]
                or receipt.client_request_id != task["client_request_id"]
                or state not in {
                    TaskState.SUBMITTED, TaskState.QUEUED, TaskState.RUNNING,
                    TaskState.SUBMISSION_UNCERTAIN, TaskState.COMPLETED,
                }
            ):
                raise AmbiguousSubmission("processor receipt does not match the claimed submission")
            updated = store.transition_owned_task(
                owner_id,
                task_id,
                state,
                external_task_id=receipt.external_task_id,
                credits_charged=receipt.credits_charged,
                remaining_credits=receipt.remaining_credits,
                detail=receipt.detail,
            )
        except DefiniteProviderRejection as error:
            if error.status == 429:
                # A documented 429 proves that no task was created.  Keep the
                # same durable request reservation and retry it only after a
                # bounded delay; this is safe where retrying an ambiguous POST
                # would not be.
                retry_after = max(8, error.retry_after_seconds or 15)
                updated = store.defer_owned_autoip_submission(
                    owner_id,
                    task_id,
                    lease_owner=lease_owner,
                    retry_at=_utc_after(retry_after),
                    detail=(
                        "deferred_provider_rejection:"
                        f"http_429:{error.error_code}:retry_after_{retry_after}"
                    ),
                )
                return WorkerResult(
                    status="deferred",
                    outbox_id=outbox_id,
                    task_id=task_id,
                    task_state=str(updated["state"]),
                    retry_after_seconds=retry_after,
                )
            updated = store.transition_owned_task(
                owner_id,
                task_id,
                TaskState.CANCELLED,
                detail=error.durable_detail,
            )
        except DefiniteLocalFailure as error:
            updated = store.transition_owned_task(
                owner_id, task_id, TaskState.CANCELLED,
                detail=f"definite_local_failure:{type(error).__name__}",
            )
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                # Persist uncertainty before respecting process interruption.
                store.transition_owned_task(
                    owner_id, task_id, TaskState.SUBMISSION_UNCERTAIN,
                    detail=f"submission_uncertain:{type(error).__name__}",
                )
                raise
            updated = store.transition_owned_task(
                owner_id, task_id, TaskState.SUBMISSION_UNCERTAIN,
                detail=f"submission_uncertain:{type(error).__name__}",
            )
        return WorkerResult(
            status="processed", outbox_id=outbox_id, task_id=task_id,
            task_state=str(updated["state"]),
        )
    return WorkerResult(status="idle")


__all__ = [
    "AmbiguousSubmission", "DefiniteLocalFailure", "DefiniteProviderRejection",
    "SubmissionProcessor", "WorkerResult", "run_once",
]
