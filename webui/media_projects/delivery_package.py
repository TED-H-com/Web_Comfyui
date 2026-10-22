"""Deterministic owner-scoped delivery package for accepted productions."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .production_edit_api import resolve_production_edit_output
from .edit_timeline import compile_edit_timeline
from .production_state import ProductionPhase
from .production_store import ProductionStateStore, ProductionStoreConflict


DELIVERY_PACKAGE_VERSION = "castle.production-delivery-package.v1"
_LOCK = threading.Lock()
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class DeliveryPackage:
    path: Path
    filename: str
    byte_size: int
    sha256: str
    package_hash: str
    manifest: dict[str, object]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(path: Path) -> tuple[int, str]:
    before = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(before.st_mode) or path.is_symlink()
        or before.st_uid != os.geteuid() or before.st_nlink != 1
    ):
        raise ProductionStoreConflict("delivery package source is unsafe")
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            value.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ProductionStoreConflict("delivery package source changed")
    return size, value.hexdigest()


def _entry(name: str, *, compressed: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    info.external_attr = 0o100600 << 16
    info.create_system = 3
    return info


def _copy_into(archive: zipfile.ZipFile, info: zipfile.ZipInfo, path: Path) -> None:
    with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)


def _verify_archive(archive: zipfile.ZipFile, manifest: dict[str, object]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ProductionStoreConflict("delivery package manifest changed")
    expected_names = {item.get("path") for item in files if isinstance(item, dict)}
    archive_names = archive.namelist()
    if (
        len(expected_names) != len(files)
        or len(archive_names) != len(files) + 1
        or set(archive_names) != expected_names | {"manifest.json"}
    ):
        raise ProductionStoreConflict("delivery package contents changed")
    if json.loads(archive.read("manifest.json")) != manifest:
        raise ProductionStoreConflict("delivery package manifest changed")
    for item in files:
        name = item.get("path")
        expected_size = item.get("byte_size")
        expected_sha = item.get("sha256")
        digest = hashlib.sha256()
        size = 0
        with archive.open(name, "r") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size != expected_size or digest.hexdigest() != expected_sha:
            raise ProductionStoreConflict("delivery package member changed")


def _authority(store: ProductionStateStore, owner: str, project: str):
    state = store.get(owner, project)
    if state.phase is not ProductionPhase.READY:
        raise ValueError("production delivery package requires accepted final review")
    receipt = store.get_latest_edit_receipt_for_project(owner, project)
    review = store.get_delivery_review(owner, project, str(receipt.get("receipt_hash") or ""))
    if review.get("decision") != "accepted":
        raise ValueError("production delivery package requires accepted final review")
    output, exact = resolve_production_edit_output(
        store, owner, project, str(receipt.get("receipt_hash") or "")
    )
    if exact != receipt:
        raise ProductionStoreConflict("delivery package edit receipt changed")
    return state, receipt, review, output


def build_delivery_package(
    store: ProductionStateStore, owner_user_id: str, project_id: str,
) -> DeliveryPackage:
    state, receipt, review, output = _authority(store, owner_user_id, project_id)
    output_size, output_sha = _digest(output)
    if output_size != receipt.get("byte_size") or output_sha != receipt.get("output_sha256"):
        raise ProductionStoreConflict("delivery package output differs from receipt")
    has_contact_sheet = receipt.get("schema_version") in {
        "castle.production-edit-receipt.v2", "castle.production-edit-receipt.v3",
        "castle.production-edit-receipt.v4",
        "castle.production-edit-receipt.v5",
    }
    contact_sheet = output.with_name(output.stem + ".contact.jpg") if has_contact_sheet else None
    contact_size, contact_sha = _digest(contact_sheet) if contact_sheet is not None else (None, None)
    contact_receipt = receipt.get("contact_sheet")
    if has_contact_sheet and (
        not isinstance(contact_receipt, dict)
        or contact_receipt.get("byte_size") != contact_size
        or contact_receipt.get("sha256") != contact_sha
        or contact_receipt.get("width") != 960
        or not isinstance(contact_receipt.get("height"), int)
        or contact_receipt["height"] <= 0
    ):
        raise ProductionStoreConflict("delivery contact sheet differs from receipt")
    subtitle = output.with_suffix(".srt")
    subtitle_record = None
    if subtitle.exists():
        subtitle_size, subtitle_sha = _digest(subtitle)
        if subtitle_size > 5 * 1024 * 1024:
            raise ProductionStoreConflict("delivery package subtitle is too large")
        subtitle_record = {
            "path": "captions.srt", "byte_size": subtitle_size, "sha256": subtitle_sha,
        }
    files = [{
        "path": "final.mp4", "byte_size": output_size, "sha256": output_sha,
        "mime_type": "video/mp4",
    }]
    if has_contact_sheet:
        files.append({
            "path": "contact-sheet.jpg", "byte_size": contact_size, "sha256": contact_sha,
            "mime_type": "image/jpeg",
        })
    if subtitle_record is not None:
        files.append({**subtitle_record, "mime_type": "application/x-subrip"})
    edit_state = store.get_revision(
        owner_user_id, project_id, str(receipt["production_state_hash"])
    )
    timeline = compile_edit_timeline(store.get_pack(owner_user_id, project_id), edit_state)
    if timeline.timeline_hash != receipt["timeline_hash"]:
        raise ProductionStoreConflict("delivery package timeline differs from receipt")
    soundtrack_mixed = timeline.soundtrack_asset_version_id is not None
    if receipt.get("schema_version") in {
        "castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4",
        "castle.production-edit-receipt.v5",
    }:
        if receipt.get("soundtrack_mixed") is not soundtrack_mixed:
            raise ProductionStoreConflict("delivery soundtrack evidence differs")
    if receipt.get("schema_version") in {
        "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5",
    }:
        if receipt.get("soundtrack_ducking_applied") is not soundtrack_mixed:
            raise ProductionStoreConflict("delivery soundtrack ducking evidence differs")
    if receipt.get("schema_version") == "castle.production-edit-receipt.v5":
        if receipt.get("sound_effect_count") != len(timeline.sound_effects):
            raise ProductionStoreConflict("delivery sound effect evidence differs")
    if (
        receipt.get("subtitle_emitted") is not (subtitle_record is not None)
        or receipt.get("subtitle_burned_in") is not (subtitle_record is not None)
    ):
        raise ProductionStoreConflict("delivery package subtitle evidence differs")
    timeline_bytes = _canonical(timeline.to_dict())
    files.append({
        "path": "edit-timeline.json", "byte_size": len(timeline_bytes),
        "sha256": hashlib.sha256(timeline_bytes).hexdigest(),
        "mime_type": "application/json",
    })
    qa_report = {
        "schema_version": "castle.production-delivery-qa.v1",
        "project_id": project_id,
        "edit_receipt_hash": receipt["receipt_hash"],
        "timeline_hash": receipt["timeline_hash"],
        "output_sha256": output_sha,
        "byte_size": output_size,
        "duration_seconds": receipt["duration_seconds"],
        "video": receipt["video"],
        "subtitle_emitted": subtitle_record is not None,
        "subtitle_burned_in": receipt["subtitle_burned_in"],
        **({"contact_sheet": contact_receipt} if has_contact_sheet else {}),
        **({"soundtrack_mixed": soundtrack_mixed}
           if receipt.get("schema_version") in {
               "castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4",
               "castle.production-edit-receipt.v5",
           } else {}),
        **({"soundtrack_ducking_applied": soundtrack_mixed}
           if receipt.get("schema_version") in {
               "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5",
           } else {}),
        **({"sound_effect_count": len(timeline.sound_effects)}
           if receipt.get("schema_version") == "castle.production-edit-receipt.v5" else {}),
        "mechanical_qa_passed": receipt["mechanical_qa_passed"] is True,
        "human_delivery_review": "accepted",
    }
    qa_bytes = _canonical(qa_report)
    files.append({
        "path": "qa-report.json", "byte_size": len(qa_bytes),
        "sha256": hashlib.sha256(qa_bytes).hexdigest(),
        "mime_type": "application/json",
    })
    material = {
        "schema_version": DELIVERY_PACKAGE_VERSION,
        "project_id": project_id,
        "pack_hash": state.pack_hash,
        "production_state_hash": state.state_hash,
        "edit_receipt_hash": receipt["receipt_hash"],
        "delivery_review_hash": review["review_hash"],
        "files": files,
        "publishing_authorized": False,
    }
    package_hash = hashlib.sha256(_canonical(material)).hexdigest()
    manifest = {**material, "package_hash": package_hash}
    destination = output.parent / f"castle_delivery_{package_hash}.zip"
    with _LOCK:
        if not destination.exists():
            fd, staged_name = tempfile.mkstemp(prefix=".castle-delivery-", suffix=".zip", dir=output.parent)
            os.close(fd)
            staged = Path(staged_name)
            try:
                os.chmod(staged, 0o600)
                with zipfile.ZipFile(staged, "w", allowZip64=True) as archive:
                    _copy_into(archive, _entry("final.mp4", compressed=False), output)
                    if contact_sheet is not None:
                        _copy_into(archive, _entry("contact-sheet.jpg", compressed=False), contact_sheet)
                    if subtitle_record is not None:
                        _copy_into(archive, _entry("captions.srt", compressed=True), subtitle)
                    archive.writestr(_entry("edit-timeline.json", compressed=True), timeline_bytes)
                    archive.writestr(_entry("qa-report.json", compressed=True), qa_bytes)
                    archive.writestr(_entry("manifest.json", compressed=True), _canonical(manifest))
                os.link(staged, destination)
            finally:
                staged.unlink(missing_ok=True)
    package_size, package_sha = _digest(destination)
    try:
        with zipfile.ZipFile(destination, "r") as archive:
            _verify_archive(archive, manifest)
    except ProductionStoreConflict:
        raise
    except (KeyError, ValueError, RuntimeError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ProductionStoreConflict("delivery package archive changed") from error
    return DeliveryPackage(
        destination, f"castle-{project_id}-delivery.zip", package_size,
        package_sha, package_hash, manifest,
    )


__all__ = ["DELIVERY_PACKAGE_VERSION", "DeliveryPackage", "build_delivery_package"]
