"""Validated publication boundary for the local RTX capability catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .local_catalog import LocalProviderCatalog, build_local_provider_catalog
from .h3_catalog import validate_h3_evidence_aliases
from .store import SYSTEM_LOCAL_OWNER_ID, ProviderFabricStore


@dataclass(frozen=True, slots=True)
class LocalCatalogPublication:
    catalog: LocalProviderCatalog
    source_revision: str
    snapshot: Mapping[str, Any] | None

    @property
    def committed(self) -> bool:
        return self.snapshot is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "sourceRevision": self.source_revision,
            "catalog": self.catalog.to_dict(),
            "snapshot": dict(self.snapshot) if self.snapshot is not None else None,
        }


def publish_local_provider_catalog(
    store: ProviderFabricStore,
    account_id: str,
    feature_descriptors: Sequence[Mapping[str, Any]],
    readiness_by_variant: Mapping[str, Mapping[str, Any]],
    evidence_by_variant: Mapping[str, Mapping[str, Any]],
    *,
    source_revision: str,
    commit: bool = False,
) -> LocalCatalogPublication:
    """Validate a catalog and optionally append one immutable local snapshot.

    Disabled and unproven offerings are intentionally persisted too, so the
    inventory can explain exactly why a lane is unavailable.  The router still
    sees their fail-closed ``enabled=False`` state.
    """

    if not isinstance(store, ProviderFabricStore):
        raise TypeError("store must be a ProviderFabricStore")
    if not isinstance(commit, bool):
        raise TypeError("commit must be a boolean")
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise ValueError("source_revision is required")
    validate_h3_evidence_aliases(evidence_by_variant)
    catalog = build_local_provider_catalog(
        feature_descriptors,
        readiness_by_variant,
        evidence_by_variant,
    )
    snapshot = None
    if commit:
        snapshot = store.record_owned_capability_snapshot(
            SYSTEM_LOCAL_OWNER_ID,
            account_id,
            catalog.offerings,
            source_revision=source_revision.strip(),
        )
    return LocalCatalogPublication(catalog, source_revision.strip(), snapshot)


__all__ = ["LocalCatalogPublication", "publish_local_provider_catalog"]
