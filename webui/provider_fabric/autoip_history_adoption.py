"""Explicitly adopt completed legacy AutoIP task outputs into Castle media."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any, Mapping, Protocol

from .autoip import OutputDownloadReceipt
from .autoip_catalog_sync import AutoIPCatalogSnapshotStore
from .models import MediaKind, TaskReceipt, TaskState
from .store import ProviderFabricStore, StoreConflictError


_KIND_BY_MIME = {
    "image/avif": MediaKind.IMAGE, "image/gif": MediaKind.IMAGE,
    "image/jpeg": MediaKind.IMAGE, "image/png": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE, "video/mp4": MediaKind.VIDEO,
    "video/quicktime": MediaKind.VIDEO, "video/webm": MediaKind.VIDEO,
    "audio/mpeg": MediaKind.AUDIO, "audio/wav": MediaKind.AUDIO,
    "audio/x-wav": MediaKind.AUDIO, "audio/flac": MediaKind.AUDIO,
    "audio/mp4": MediaKind.AUDIO, "audio/ogg": MediaKind.AUDIO,
}


class AutoIPHistoryAdoptionError(ValueError):
    pass


class HistoryClient(Protocol):
    def poll_task(self, task_uuid: str, *, client_request_id: str) -> TaskReceipt: ...
    def download_output_to_staging(
        self, task_uuid: str, index: int, staging_root: Path, **kwargs: object,
    ) -> OutputDownloadReceipt: ...


def _suffix(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32]


def _rehash(path: Path, expected_size: int) -> str:
    digest, total = hashlib.sha256(), 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > expected_size:
                    raise AutoIPHistoryAdoptionError("autoip_history_output_changed")
                digest.update(chunk)
    except OSError as error:
        raise AutoIPHistoryAdoptionError("autoip_history_output_unavailable") from error
    if total != expected_size:
        raise AutoIPHistoryAdoptionError("autoip_history_output_changed")
    return digest.hexdigest()


def _adopt_one(
    store: ProviderFabricStore,
    *, owner_user_id: str, account_id: str, task_uuid: str,
    receipt: OutputDownloadReceipt, staging_root: Path,
) -> tuple[int, str, str]:
    if not isinstance(receipt, OutputDownloadReceipt) or receipt.task_uuid != task_uuid:
        raise AutoIPHistoryAdoptionError("autoip_history_download_receipt_invalid")
    root = Path(staging_root).expanduser().resolve(strict=True)
    path = (root / receipt.staging_name).resolve(strict=True)
    if path.parent != root or path.name != receipt.staging_name or not path.is_file():
        raise AutoIPHistoryAdoptionError("autoip_history_output_escaped_staging")
    if not hmac.compare_digest(_rehash(path, receipt.byte_size), receipt.sha256):
        raise AutoIPHistoryAdoptionError("autoip_history_output_digest_changed")
    kind = _KIND_BY_MIME.get(receipt.content_type)
    if kind is None:
        raise AutoIPHistoryAdoptionError("autoip_history_output_kind_unsupported")
    suffix = _suffix(owner_user_id, account_id, task_uuid, str(receipt.index))
    projection_id = f"autoip-history:{suffix}"
    version_id = f"assetver:autoip-history:{suffix}"
    external_asset_id = f"history:{task_uuid}:output:{receipt.index}"
    metadata = {
        "source": "autoip_history", "task_id": task_uuid,
        "output_index": receipt.index, "classification_required": True,
    }
    try:
        projection = store.get_owned_remote_asset(owner_user_id, projection_id)
    except KeyError:
        projection = store.project_owned_remote_asset(
            owner_user_id, projection_id, account_id,
            external_asset_id, kind,
            visual_class=None,
            metadata=metadata,
        )
    else:
        # A later explicit classification is a one-way local fact.  Re-adopting
        # the same completed provider task must validate its deterministic
        # identity without trying to overwrite that class or its character
        # links back to the original unknown state.
        if any(
            projection.get(key) != value for key, value in {
                "account_id": account_id,
                "external_asset_id": external_asset_id,
                "media_kind": kind.value,
                "metadata": metadata,
            }.items()
        ):
            raise StoreConflictError("AutoIP history projection identity conflicts")
        classification = store.get_owned_remote_asset_classification(
            owner_user_id, projection_id,
        )
        if classification is None and (
            projection.get("visual_class") not in {None, "unknown"}
            or projection.get("character_ids")
        ):
            raise StoreConflictError(
                "AutoIP history classification lacks sealed evidence"
            )
    versions = [item for item in projection["versions"] if item["version_id"] == version_id]
    if not versions:
        try:
            store.add_owned_remote_asset_version(
                owner_user_id, projection_id, path.as_uri(),
                version_id=version_id, version_number=1,
                provider_version_id=f"history:{task_uuid}:{receipt.index}",
                mime_type=receipt.content_type, byte_size=receipt.byte_size,
                sha256=receipt.sha256,
                metadata={"validated_download": True, "legacy_history": True},
            )
        except StoreConflictError:
            pass
        projection = store.get_owned_remote_asset(owner_user_id, projection_id)
        versions = [item for item in projection["versions"] if item["version_id"] == version_id]
    if len(versions) != 1 or any(
        versions[0].get(key) != value for key, value in {
            "mime_type": receipt.content_type, "byte_size": receipt.byte_size,
            "sha256": receipt.sha256,
        }.items()
    ):
        raise StoreConflictError("AutoIP history output identity conflicts")
    if kind in {MediaKind.IMAGE, MediaKind.VIDEO}:
        try:
            store.put_owned_asset_catalog_annotation(
                owner_user_id, projection_id, display_name=None,
                audio_role="unclassified", media_role="finished",
                expected_revision=0,
            )
        except StoreConflictError:
            pass
    return receipt.index, projection_id, version_id


def adopt_autoip_history_task(
    provider_store: ProviderFabricStore,
    snapshot_store: AutoIPCatalogSnapshotStore,
    client: HistoryClient,
    staging_root: Path,
    *, owner_user_id: str, account_id: str, task_uuid: str,
) -> dict[str, Any]:
    """Recheck, download, verify, and adopt every output without class inference."""

    snapshot = snapshot_store.get(owner_user_id, account_id)
    matches = [item for item in snapshot.get("tasks", ()) if (
        isinstance(item, Mapping) and item.get("task_uuid") == task_uuid
    )]
    if len(matches) != 1 or matches[0].get("status") != "completed":
        raise AutoIPHistoryAdoptionError("autoip_history_task_not_completed_in_snapshot")
    request_id = f"autoip-history:{_suffix(owner_user_id, account_id, task_uuid)}"
    receipt = client.poll_task(task_uuid, client_request_id=request_id)
    if (
        not isinstance(receipt, TaskReceipt) or receipt.provider_id != "autoip"
        or receipt.client_request_id != request_id
        or receipt.external_task_id != task_uuid or receipt.state is not TaskState.COMPLETED
    ):
        raise AutoIPHistoryAdoptionError("autoip_history_task_not_completed")
    indexes = []
    for item in receipt.outputs:
        index = item.get("index")
        if type(index) is not int or index < 0:
            raise AutoIPHistoryAdoptionError("autoip_history_output_index_invalid")
        indexes.append(index)
    if not indexes or len(indexes) != len(set(indexes)) or len(indexes) > 16:
        raise AutoIPHistoryAdoptionError("autoip_history_output_set_invalid")
    expected_indexes = sorted(indexes)
    expected_assets = []
    for index in expected_indexes:
        suffix = _suffix(owner_user_id, account_id, task_uuid, str(index))
        expected_assets.append({
            "output_index": index,
            "projection_id": f"autoip-history:{suffix}",
            "asset_version_id": f"assetver:autoip-history:{suffix}",
        })
    provider_store.preflight_owned_autoip_history_adoption(
        owner_user_id,
        account_id,
        task_uuid,
        expected_assets,
    )
    root = Path(staging_root).expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    downloads = [
        client.download_output_to_staging(task_uuid, index, root)
        for index in expected_indexes
    ]
    if any(
        not isinstance(download, OutputDownloadReceipt)
        or download.task_uuid != task_uuid
        or download.index != expected_index
        for expected_index, download in zip(
            expected_indexes, downloads, strict=True,
        )
    ):
        raise AutoIPHistoryAdoptionError(
            "autoip_history_download_output_set_invalid"
        )
    adopted = [
        _adopt_one(
            provider_store, owner_user_id=owner_user_id, account_id=account_id,
            task_uuid=task_uuid, receipt=download, staging_root=root,
        )
        for download in downloads
    ]
    provider_store.seal_owned_autoip_history_adoption(
        owner_user_id,
        account_id,
        task_uuid,
        [
            {
                "output_index": output_index,
                "projection_id": projection_id,
                "asset_version_id": asset_version_id,
            }
            for output_index, projection_id, asset_version_id in adopted
        ],
    )
    assets = []
    for _output_index, projection_id, asset_version_id in adopted:
        projection = provider_store.get_owned_remote_asset(
            owner_user_id, projection_id,
        )
        visual_class = projection.get("visual_class")
        visual_class = (
            visual_class
            if visual_class in {"anime", "realistic", "illustration"}
            else "unknown"
        )
        character_ids = sorted(set(projection.get("character_ids") or ()))
        assets.append({
            "projection_id": projection_id,
            "asset_version_id": asset_version_id,
            "visual_class": visual_class,
            "character_ids": character_ids,
            "classification_required": visual_class == "unknown",
        })
    return {
        "schema_version": "castle.autoip-history-adoption.response.v2",
        "ok": True, "provider_id": "autoip", "account_id": account_id,
        "task_uuid": task_uuid,
        "assets": assets,
        "classification_inferred": False,
        "provider_write_performed": False, "credits_spent": 0,
    }


__all__ = [
    "AutoIPHistoryAdoptionError", "adopt_autoip_history_task",
]
