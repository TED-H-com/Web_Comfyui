"""Bounded, read-only preview for sanitized ClipMode export bundles.

This is the only bridge used by the HTTP preview endpoint.  It accepts bytes
already bounded by Castle's outer HTTP server, applies a second smaller set of
import limits, and plans a projection against one explicitly configured
credentialless ClipMode account.  It never extracts an archive, persists the
source, mutates the provider store, performs provider transport, or spends
credits.

The full importer bundle and projection plan can be large.  Public responses
therefore contain complete aggregate counts and hashes, but only bounded
samples of characters, actions, and conflicts.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Any, Mapping

from .clipmode_import import (
    ClipModeImportError,
    ImportLimits,
    import_clipmode_export,
)
from .clipmode_projection import ClipModeProjectionError, plan_clipmode_projection
from .models import MediaKind, VisualClass
from .store import ProviderFabricStore


PREVIEW_SCHEMA_VERSION = 1
MAX_PREVIEW_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PREVIEW_CHARACTERS = 100
MAX_PREVIEW_ACTIONS = 100
MAX_PREVIEW_CONFLICTS = 50

PREVIEW_IMPORT_LIMITS = ImportLimits(
    max_source_bytes=MAX_PREVIEW_SOURCE_BYTES,
    max_manifest_bytes=8 * 1024 * 1024,
    max_members=2_000,
    max_member_bytes=128 * 1024 * 1024,
    max_total_uncompressed_bytes=256 * 1024 * 1024,
    max_compression_ratio=120.0,
    max_characters=5_000,
    max_media=10_000,
    max_lineage_edges=20_000,
    max_text_media_bytes=2 * 1024 * 1024,
)

_PUBLIC_CODE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


class ClipModePreviewError(RuntimeError):
    """A sanitized preview failure suitable for the HTTP adapter."""

    __slots__ = ("code", "status", "reason_code")

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        reason_code: str | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.reason_code = (
            reason_code
            if isinstance(reason_code, str) and _PUBLIC_CODE.fullmatch(reason_code)
            else None
        )
        super().__init__(message)


def _counter(values: Any, allowed: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(str(value) for value in values)
    return {key: int(counts.get(key, 0)) for key in allowed}


def _bounded_rows(rows: tuple[Any, ...], maximum: int) -> tuple[list[dict[str, Any]], bool]:
    selected = [item.to_dict() for item in rows[:maximum]]
    return selected, len(rows) > maximum


def build_clipmode_import_preview(
    source: bytes,
    store: ProviderFabricStore,
    *,
    account_id: str,
) -> dict[str, Any]:
    """Validate and dry-run one raw JSON/ZIP export without side effects."""

    if type(source) is not bytes:
        raise ClipModePreviewError(
            "clipmode_preview_source_invalid",
            "ClipMode preview requires raw JSON or ZIP bytes.",
        )
    if not isinstance(store, ProviderFabricStore):
        raise TypeError("store must be a ProviderFabricStore")
    if len(source) > MAX_PREVIEW_SOURCE_BYTES:
        raise ClipModePreviewError(
            "clipmode_preview_too_large",
            "ClipMode preview source exceeds the allowed size.",
            status=413,
        )

    try:
        bundle = import_clipmode_export(source, limits=PREVIEW_IMPORT_LIMITS)
    except ClipModeImportError as error:
        raise ClipModePreviewError(
            "clipmode_import_invalid",
            "ClipMode export did not pass offline validation.",
            reason_code=error.code,
        ) from error

    try:
        plan = plan_clipmode_projection(bundle, store, account_id=account_id)
    except (ClipModeProjectionError, sqlite3.Error, OSError) as error:
        reason_code = getattr(error, "code", None)
        raise ClipModePreviewError(
            "clipmode_preview_unavailable",
            "ClipMode preview is temporarily unavailable.",
            status=503,
            reason_code=reason_code,
        ) from error

    character_rows = [
        {
            "record_id": item.record_id,
            "display_name": item.display_name,
            "visual_class": item.visual_class.value,
            "catalog_only": item.catalog_only,
            "has_profile_media": item.profile_media_record_id is not None,
        }
        for item in bundle.characters[:MAX_PREVIEW_CHARACTERS]
    ]
    action_rows, actions_truncated = _bounded_rows(plan.actions, MAX_PREVIEW_ACTIONS)
    conflict_rows, conflicts_truncated = _bounded_rows(
        plan.conflicts, MAX_PREVIEW_CONFLICTS
    )
    duplicate_count = sum(item.occurrences - 1 for item in bundle.duplicates)

    visual_values = tuple(value.value for value in VisualClass)
    kind_values = tuple(value.value for value in MediaKind)
    account_requirement: Mapping[str, Any] = plan.account_requirement
    return {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "provider_id": "clipmode",
        "mode": "offline_preview",
        "ready": plan.ready,
        "confirmation_available": False,
        "source": {
            "schema": bundle.provenance.schema_name,
            "schema_version": bundle.provenance.schema_version,
            "source_kind": bundle.provenance.source_kind,
            "export_id": bundle.provenance.export_id,
            "exported_at": bundle.provenance.exported_at,
            "source_sha256": bundle.provenance.source_sha256,
            "semantic_sha256": bundle.provenance.semantic_sha256,
        },
        "account": {
            "account_id": plan.account_id,
            "provider_scope": plan.provider_scope,
            "account_binding_sha256": plan.account_binding_sha256,
            "exists": account_requirement.get("exists") is True,
            "provider_matches": account_requirement.get("providerMatches") is True,
            "enabled": account_requirement.get("enabled") is True,
            "scope_observed": account_requirement.get("scopeObserved") is True,
            "scope_matches": account_requirement.get("scopeMatches") is True,
            "scope_bound": account_requirement.get("scopeBound") is True,
        },
        "plan": {
            "bridge_version": plan.bridge_version,
            "plan_hash": plan.plan_hash,
            "mapping_sha256": plan.mapping_sha256,
            "counts": dict(plan.counts),
        },
        "summary": {
            "characters": len(bundle.characters),
            "media": len(bundle.media),
            "lineage": len(bundle.lineage),
            "duplicates": duplicate_count,
            "character_visual_classes": _counter(
                (item.visual_class.value for item in bundle.characters), visual_values
            ),
            "media_visual_classes": _counter(
                (item.visual_class.value for item in bundle.media), visual_values
            ),
            "media_kinds": _counter(
                (item.kind.value for item in bundle.media), kind_values
            ),
        },
        "characters": character_rows,
        "actions": action_rows,
        "conflicts": conflict_rows,
        "truncated": {
            "characters": len(bundle.characters) > MAX_PREVIEW_CHARACTERS,
            "actions": actions_truncated,
            "conflicts": conflicts_truncated,
        },
        "provider_transport_performed": False,
        # Runtime construction can maintain Castle's fixed disabled provider
        # account rows before this function is called.  This narrower proof is
        # the invariant the import preview itself can guarantee end to end.
        "source_derived_records_mutated": False,
        "source_persisted": False,
        "archive_extracted": False,
        "credits_spent": 0,
    }


__all__ = [
    "ClipModePreviewError",
    "MAX_PREVIEW_ACTIONS",
    "MAX_PREVIEW_CHARACTERS",
    "MAX_PREVIEW_CONFLICTS",
    "MAX_PREVIEW_SOURCE_BYTES",
    "PREVIEW_IMPORT_LIMITS",
    "PREVIEW_SCHEMA_VERSION",
    "build_clipmode_import_preview",
]
