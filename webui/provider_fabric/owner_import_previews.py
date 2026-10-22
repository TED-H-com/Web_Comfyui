"""Select and render one immutable safe preview per imported image projection."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .import_storage import StagedImport
from .owner_import import OwnerImportDecisionPlan, ServerMediaIdentity
from .owner_import_renditions import OwnerProfileRendition
from .profile_rendition import ProfileRenditionError, render_profile_rendition


@dataclass(frozen=True, slots=True)
class OwnerAssetPreviewRendition:
    source_media_record_id: str
    projection_id: str = field(repr=False)
    version_id: str = field(repr=False)
    source_sha256: str = field(repr=False)
    rendition_sha256: str = field(repr=False)
    content: bytes = field(repr=False)
    mime_type: str = "image/png"
    signature_family: str = "png"
    byte_size: int = 0
    width: int = 0
    height: int = 0
    selection_source: str = "explicit_import"

    def hash_dict(self) -> dict[str, object]:
        return {
            "byte_size": self.byte_size,
            "height": self.height,
            "mime_type": self.mime_type,
            "projection_id": self.projection_id,
            "rendition_sha256": self.rendition_sha256,
            "selection_source": self.selection_source,
            "signature_family": self.signature_family,
            "source_media_record_id": self.source_media_record_id,
            "source_sha256": self.source_sha256,
            "version_id": self.version_id,
            "width": self.width,
        }


def _read_source(
    archive: zipfile.ZipFile, identity: ServerMediaIdentity
) -> bytes:
    if (
        identity.archive_member is None
        or identity.byte_size is None
        or identity.content_sha256 is None
    ):
        raise ValueError("preview source evidence is unavailable")
    info = archive.getinfo(identity.archive_member)
    if info.is_dir() or info.file_size != identity.byte_size:
        raise ValueError("preview source evidence changed")
    with archive.open(info, "r") as source:
        content = source.read(identity.byte_size + 1)
        if len(content) != identity.byte_size or source.read(1):
            raise ValueError("preview source evidence changed")
    if hashlib.sha256(content).hexdigest() != identity.content_sha256:
        raise ValueError("preview source evidence changed")
    return content


def _from_profile(
    identity: ServerMediaIdentity, profile: OwnerProfileRendition
) -> OwnerAssetPreviewRendition:
    if (
        profile.projection_id != identity.projection_id
        or profile.version_id != identity.version_id
        or profile.source_sha256 != identity.content_sha256
    ):
        raise ValueError("profile rendition does not match its media version")
    return OwnerAssetPreviewRendition(
        source_media_record_id=identity.source_media_record_id,
        projection_id=identity.projection_id,
        version_id=identity.version_id,
        source_sha256=profile.source_sha256,
        rendition_sha256=profile.rendition_sha256,
        content=profile.content,
        mime_type=profile.mime_type,
        signature_family=profile.signature_family,
        byte_size=profile.byte_size,
        width=profile.width,
        height=profile.height,
        selection_source="explicit_profile",
    )


def build_owner_asset_previews(
    staged: StagedImport,
    decision_plan: OwnerImportDecisionPlan,
    profile_renditions: Sequence[OwnerProfileRendition],
) -> tuple[OwnerAssetPreviewRendition, ...]:
    """Return one exact clean preview for each decodable bundled image asset."""

    if not isinstance(staged, StagedImport):
        raise TypeError("staged must be a StagedImport")
    if not isinstance(decision_plan, OwnerImportDecisionPlan):
        raise TypeError("decision_plan must be an OwnerImportDecisionPlan")
    if isinstance(profile_renditions, (str, bytes, bytearray)) or not isinstance(
        profile_renditions, Sequence
    ):
        raise TypeError("profile_renditions must be a sequence")
    if (
        staged.account_id != decision_plan.account_id
        or staged.source_sha256 != decision_plan.raw_source_sha256
        or staged.manifest_sha256 != decision_plan.manifest_sha256
        or staged.semantic_sha256 != decision_plan.semantic_sha256
    ):
        raise ValueError("preview stage does not match its decision")
    profiles: dict[str, OwnerProfileRendition] = {}
    for item in profile_renditions:
        if not isinstance(item, OwnerProfileRendition):
            raise TypeError("profile_renditions contains an invalid item")
        if item.projection_id in profiles:
            raise ValueError("one projection has multiple profile renditions")
        profiles[item.projection_id] = item

    source_media = {item.record_id: item for item in staged.bundle.media}
    groups: dict[str, list[ServerMediaIdentity]] = {}
    for identity in decision_plan.media:
        if (
            identity.kind == "image"
            and identity.availability == "bundled"
            and identity.mime_type in {"image/jpeg", "image/png", "image/webp"}
        ):
            groups.setdefault(identity.projection_id, []).append(identity)
    if not groups:
        return ()
    if staged.bundle.provenance.source_kind != "zip":
        raise ValueError("bundled preview source requires a ZIP stage")

    result: list[OwnerAssetPreviewRendition] = []
    with zipfile.ZipFile(io.BytesIO(staged.source), "r") as archive:
        for projection_id in sorted(groups):
            candidates = sorted(
                groups[projection_id],
                key=lambda identity: (
                    source_media[identity.source_media_record_id].created_at or "",
                    identity.external_version,
                    identity.source_media_record_id,
                ),
                reverse=True,
            )
            profile = profiles.get(projection_id)
            if profile is not None:
                exact = next(
                    (
                        item
                        for item in candidates
                        if item.version_id == profile.version_id
                    ),
                    None,
                )
                if exact is None:
                    raise ValueError("profile preview version is unavailable")
                result.append(_from_profile(exact, profile))
                continue
            for identity in candidates:
                try:
                    rendered = render_profile_rendition(
                        _read_source(archive, identity), identity.mime_type
                    )
                except (ProfileRenditionError, OSError, RuntimeError, EOFError, ValueError):
                    continue
                result.append(
                    OwnerAssetPreviewRendition(
                        source_media_record_id=identity.source_media_record_id,
                        projection_id=identity.projection_id,
                        version_id=identity.version_id,
                        source_sha256=identity.content_sha256,
                        rendition_sha256=rendered.sha256,
                        content=rendered.content,
                        mime_type=rendered.mime_type,
                        signature_family=rendered.signature_family,
                        byte_size=rendered.byte_size,
                        width=rendered.width,
                        height=rendered.height,
                    )
                )
                break
    return tuple(result)


__all__ = ["OwnerAssetPreviewRendition", "build_owner_asset_previews"]
