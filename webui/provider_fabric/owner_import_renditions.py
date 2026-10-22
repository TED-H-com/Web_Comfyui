"""Rebuild deterministic safe profile renditions from one authenticated stage."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field

from .import_storage import StagedImport
from .owner_import import OwnerImportDecisionPlan, ProfileChoice
from .profile_rendition import ProfileRenditionError, render_profile_rendition


class OwnerImportRenditionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise OwnerImportRenditionError(code, message)


@dataclass(frozen=True, slots=True)
class OwnerProfileRendition:
    source_character_key: str
    canonical_character_id: str = field(repr=False)
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

    def hash_dict(self) -> dict[str, object]:
        return {
            "byte_size": self.byte_size,
            "canonical_character_id": self.canonical_character_id,
            "height": self.height,
            "mime_type": self.mime_type,
            "projection_id": self.projection_id,
            "rendition_sha256": self.rendition_sha256,
            "signature_family": self.signature_family,
            "source_character_key": self.source_character_key,
            "source_sha256": self.source_sha256,
            "version_id": self.version_id,
            "width": self.width,
        }


def _source_bytes(archive: zipfile.ZipFile, profile: ProfileChoice) -> bytes:
    try:
        info = archive.getinfo(profile.archive_member)
    except KeyError as error:
        raise OwnerImportRenditionError(
            "profile_source_unavailable", "Selected profile source is unavailable."
        ) from error
    if info.is_dir() or info.file_size != profile.byte_size:
        _fail("profile_source_mismatch", "Selected profile source no longer matches.")
    try:
        with archive.open(info, "r") as source:
            content = source.read(profile.byte_size + 1)
            if len(content) != profile.byte_size or source.read(1):
                _fail("profile_source_mismatch", "Selected profile source no longer matches.")
    except OwnerImportRenditionError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as error:
        raise OwnerImportRenditionError(
            "profile_source_unavailable", "Selected profile source is unavailable."
        ) from error
    if hashlib.sha256(content).hexdigest() != profile.source_sha256:
        _fail("profile_source_mismatch", "Selected profile source no longer matches.")
    return content


def build_owner_profile_renditions(
    staged: StagedImport,
    decision_plan: OwnerImportDecisionPlan,
) -> tuple[OwnerProfileRendition, ...]:
    """Render every explicit profile choice from the exact staged ZIP bytes."""

    if not isinstance(staged, StagedImport):
        raise TypeError("staged must be a StagedImport")
    if not isinstance(decision_plan, OwnerImportDecisionPlan):
        raise TypeError("decision_plan must be an OwnerImportDecisionPlan")
    if (
        staged.account_id != decision_plan.account_id
        or staged.source_sha256 != decision_plan.raw_source_sha256
        or staged.manifest_sha256 != decision_plan.manifest_sha256
        or staged.semantic_sha256 != decision_plan.semantic_sha256
    ):
        _fail("profile_stage_mismatch", "Profile stage does not match the import decision.")
    if not decision_plan.profiles:
        return ()
    if staged.bundle.provenance.source_kind != "zip":
        _fail("profile_source_unavailable", "Selected profile source is unavailable.")

    result: list[OwnerProfileRendition] = []
    try:
        with zipfile.ZipFile(io.BytesIO(staged.source), "r") as archive:
            for profile in sorted(
                decision_plan.profiles, key=lambda item: item.source_character_key
            ):
                rendered = render_profile_rendition(
                    _source_bytes(archive, profile), profile.mime_type
                )
                result.append(
                    OwnerProfileRendition(
                        source_character_key=profile.source_character_key,
                        canonical_character_id=profile.canonical_character_id,
                        projection_id=profile.projection_id,
                        version_id=profile.version_id,
                        source_sha256=profile.source_sha256,
                        rendition_sha256=rendered.sha256,
                        content=rendered.content,
                        mime_type=rendered.mime_type,
                        signature_family=rendered.signature_family,
                        byte_size=rendered.byte_size,
                        width=rendered.width,
                        height=rendered.height,
                    )
                )
    except OwnerImportRenditionError:
        raise
    except ProfileRenditionError as error:
        raise OwnerImportRenditionError(error.code, str(error)) from error
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as error:
        raise OwnerImportRenditionError(
            "profile_source_unavailable", "Selected profile source is unavailable."
        ) from error
    return tuple(result)


__all__ = [
    "OwnerImportRenditionError",
    "OwnerProfileRendition",
    "build_owner_profile_renditions",
]
