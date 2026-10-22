"""Verify and publish ClipMode SDXL LoRAs into canonical character bindings.

The command is dry-run by default.  ``--execute`` is the only mode that opens
the provider store through :class:`ProviderFabricStore` and appends bindings.
All artifact paths come from operator-owned files beneath fixed roots; only
safe relative model names and immutable digests enter the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from .store import (
    CHARACTER_LORA_CAPABILITY_ID,
    ProviderFabricStore,
    StoreConflictError,
    default_database_path,
)


STATE_SCHEMA = "castle.clipmode-training-batch-state.v1"
BATCH_SCHEMA = "castle.clipmode-training-batch.v1"
PLAN_SCHEMA = "castle.clipmode-sdxl-pilot-plan.v1"
RECEIPT_SCHEMA = "castle.clipmode-sdxl-training-receipt.v1"
OUTPUT_SCHEMA = "castle.character-model-binding-publication.v1"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_MODEL_BYTES = 16 * 1024 * 1024 * 1024
_HEX = frozenset("0123456789abcdef")


class CharacterModelPublishError(RuntimeError):
    """A publication input failed an immutable provenance check."""


def _fail(code: str) -> None:
    raise CharacterModelPublishError(code)


def _regular_file(path: Path, *, maximum: int, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CharacterModelPublishError(code) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not 0 < metadata.st_size <= maximum
    ):
        _fail(code)
    return metadata


def _read_file(path: Path, *, maximum: int, code: str) -> bytes:
    before = _regular_file(path, maximum=maximum, code=code)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    _fail(code)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CharacterModelPublishError(code) from error
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        _fail(code)
    return b"".join(chunks)


def _json_file(path: Path, *, code: str) -> tuple[dict[str, Any], str]:
    raw = _read_file(path, maximum=MAX_JSON_BYTES, code=code)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CharacterModelPublishError(code) from error
    if not isinstance(value, dict):
        _fail(code)
    return value, hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path, *, maximum: int, code: str) -> str:
    return hashlib.sha256(_read_file(path, maximum=maximum, code=code)).hexdigest()


def _sha256(value: object, code: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(char not in _HEX for char in normalized):
        _fail(code)
    return normalized


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _safe_child(raw: object, root: Path, code: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        _fail(code)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise CharacterModelPublishError(code) from error
    return resolved


def _relative_model_name(path: Path, root: Path) -> str:
    try:
        value = path.relative_to(root).as_posix()
    except ValueError as error:
        raise CharacterModelPublishError("published_model_outside_root") from error
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in parts)
        or not value.lower().endswith(".safetensors")
    ):
        _fail("published_model_name_invalid")
    return value


def _resolve_character_target(
    database_path: Path,
    external_character_id: str,
) -> dict[str, str]:
    try:
        connection = sqlite3.connect(
            f"file:{database_path}?mode=ro", uri=True, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT owner.owner_user_id, link.character_id, link.account_id,
                      character.visual_class
                 FROM provider_character_links AS link
                 JOIN provider_accounts AS account USING(account_id)
                 JOIN canonical_character_owners AS owner
                   ON owner.character_id = link.character_id
                 JOIN canonical_characters AS character
                   ON character.character_id = link.character_id
                WHERE account.provider_id = 'clipmode'
                  AND link.external_character_id = ?
                ORDER BY owner.owner_user_id, link.character_id, link.account_id""",
            (external_character_id,),
        ).fetchall()
    except sqlite3.Error as error:
        raise CharacterModelPublishError("provider_character_lookup_failed") from error
    finally:
        if "connection" in locals():
            connection.close()
    if len(rows) != 1:
        _fail("provider_character_owner_not_exact")
    row = rows[0]
    if row["visual_class"] not in {"anime", "realistic", "illustration"}:
        _fail("provider_character_visual_class_unresolved")
    return {
        "owner_user_id": str(row["owner_user_id"]),
        "character_id": str(row["character_id"]),
        "account_id": str(row["account_id"]),
        "visual_class": str(row["visual_class"]),
    }


def build_publication_plan(
    state_path: str | os.PathLike[str],
    database_path: str | os.PathLike[str],
    *,
    artifact_root: str | os.PathLike[str],
    lora_root: str | os.PathLike[str],
    checkpoint_root: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    """Verify the complete artifact chain and return trusted binding facts."""

    artifact_root_path = Path(artifact_root).expanduser().resolve(strict=True)
    lora_root_path = Path(lora_root).expanduser().resolve(strict=True)
    checkpoint_root_path = Path(checkpoint_root).expanduser().resolve(strict=True)
    digest_cache: dict[Path, str] = {}

    def cached_digest(path: Path, *, code: str) -> str:
        digest = digest_cache.get(path)
        if digest is None:
            digest = _sha256_file(path, maximum=MAX_MODEL_BYTES, code=code)
            digest_cache[path] = digest
        return digest

    state_file = _safe_child(os.fspath(state_path), artifact_root_path, "state_path_invalid")
    database_file = Path(database_path).expanduser().resolve(strict=True)
    _regular_file(database_file, maximum=MAX_MODEL_BYTES, code="database_invalid")
    state, _ = _json_file(state_file, code="state_invalid")
    if (
        state.get("schema") != STATE_SCHEMA
        or state.get("schemaVersion") != 1
        or state.get("mode") != "execute"
        or state.get("status") != "complete"
    ):
        _fail("state_not_complete")
    items = state.get("items")
    summary = _mapping(state.get("summary"), "state_summary_invalid")
    if (
        not isinstance(items, list)
        or not items
        or summary.get("completed") != len(items)
        or summary.get("total") != len(items)
    ):
        _fail("state_item_count_invalid")

    manifest_ref = _mapping(state.get("manifest"), "batch_manifest_reference_invalid")
    if manifest_ref.get("schema") != BATCH_SCHEMA:
        _fail("batch_manifest_reference_invalid")
    manifest_file = _safe_child(
        manifest_ref.get("path"), artifact_root_path, "batch_manifest_path_invalid"
    )
    manifest, manifest_sha256 = _json_file(
        manifest_file, code="batch_manifest_invalid"
    )
    if (
        manifest.get("schema") != BATCH_SCHEMA
        or manifest.get("schemaVersion") != 1
        or manifest_sha256 != _sha256(
            manifest_ref.get("sha256"), "batch_manifest_sha256_invalid"
        )
    ):
        _fail("batch_manifest_mismatch")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != len(items):
        _fail("batch_manifest_dataset_count_invalid")
    datasets_by_external: dict[str, Mapping[str, Any]] = {}
    for dataset in datasets:
        dataset_map = _mapping(dataset, "batch_dataset_invalid")
        external_ref = _mapping(dataset_map.get("externalRef"), "batch_external_ref_invalid")
        external_id = external_ref.get("externalId")
        if not isinstance(external_id, str) or not external_id or external_id in datasets_by_external:
            _fail("batch_external_ref_invalid")
        datasets_by_external[external_id] = dataset_map

    bindings: list[dict[str, Any]] = []
    seen_characters: set[str] = set()
    seen_models: set[str] = set()
    for item_value in items:
        item = _mapping(item_value, "state_item_invalid")
        if (
            item.get("status") != "completed"
            or item.get("receiptState") != "COMPLETE"
            or _mapping(item.get("result"), "training_result_invalid").get("state")
            != "COMPLETE"
        ):
            _fail("training_item_not_complete")
        external_ref = _mapping(item.get("externalRef"), "state_external_ref_invalid")
        external_id = external_ref.get("externalId")
        if not isinstance(external_id, str) or external_id not in datasets_by_external:
            _fail("state_external_ref_invalid")
        dataset = datasets_by_external[external_id]
        if dataset.get("externalRef") != dict(external_ref):
            _fail("dataset_external_ref_mismatch")
        trigger_token = item.get("triggerToken")
        if (
            not isinstance(trigger_token, str)
            or not trigger_token
            or trigger_token != dataset.get("triggerToken")
        ):
            _fail("trigger_token_mismatch")
        dataset_sha256 = _sha256(
            item.get("manifestSha256"), "dataset_manifest_sha256_invalid"
        )
        if dataset_sha256 != _sha256(
            dataset.get("manifestSha256"), "dataset_manifest_sha256_invalid"
        ):
            _fail("dataset_manifest_sha256_mismatch")

        plan_file = _safe_child(
            item.get("planPath"), artifact_root_path, "training_plan_path_invalid"
        )
        plan, plan_sha256 = _json_file(plan_file, code="training_plan_invalid")
        item_plan = _mapping(item.get("plan"), "state_plan_invalid")
        if (
            plan.get("schema") != PLAN_SCHEMA
            or plan.get("schemaVersion") != 1
            or plan_sha256 != _sha256(item_plan.get("sha256"), "training_plan_sha256_invalid")
            or plan.get("runId") != item_plan.get("runId")
            or plan.get("dedupeKey") != item_plan.get("dedupeKey")
        ):
            _fail("training_plan_mismatch")
        plan_dataset = _mapping(plan.get("dataset"), "training_plan_dataset_invalid")
        if (
            plan_dataset.get("externalRef") != dict(external_ref)
            or plan_dataset.get("triggerToken") != trigger_token
            or _sha256(
                plan_dataset.get("manifestSha256"), "training_plan_dataset_sha256_invalid"
            )
            != dataset_sha256
        ):
            _fail("training_plan_dataset_mismatch")

        receipt_file = _safe_child(
            item.get("receiptPath"), artifact_root_path, "training_receipt_path_invalid"
        )
        receipt, receipt_sha256 = _json_file(
            receipt_file, code="training_receipt_invalid"
        )
        receipt_publication = _mapping(
            receipt.get("publication"), "training_receipt_publication_invalid"
        )
        receipt_verification = _mapping(
            receipt.get("verification"), "training_receipt_verification_invalid"
        )
        result = _mapping(item.get("result"), "training_result_invalid")
        result_publication = _mapping(
            result.get("publication"), "training_result_publication_invalid"
        )
        publication_sha256 = _sha256(
            receipt_publication.get("sha256"), "published_model_sha256_invalid"
        )
        if (
            receipt.get("schema") != RECEIPT_SCHEMA
            or receipt.get("schemaVersion") != 1
            or receipt.get("state") != "COMPLETE"
            or receipt.get("runId") != plan.get("runId")
            or receipt.get("dedupeKey") != plan.get("dedupeKey")
            or _sha256(receipt.get("planSha256"), "receipt_plan_sha256_invalid")
            != plan_sha256
            or _sha256(
                receipt.get("datasetManifestSha256"),
                "receipt_dataset_manifest_sha256_invalid",
            )
            != dataset_sha256
            or publication_sha256
            != _sha256(
                receipt_verification.get("sha256"), "verification_model_sha256_invalid"
            )
            or publication_sha256
            != _sha256(
                result_publication.get("sha256"), "result_model_sha256_invalid"
            )
            or receipt_publication.get("path") != result_publication.get("path")
        ):
            _fail("training_receipt_mismatch")

        checkpoint = _mapping(plan.get("checkpoint"), "checkpoint_binding_invalid")
        base_model_name = checkpoint.get("name")
        if not isinstance(base_model_name, str) or "/" in base_model_name or "\\" in base_model_name:
            _fail("checkpoint_name_invalid")
        checkpoint_file = _safe_child(
            checkpoint.get("path"), checkpoint_root_path, "checkpoint_path_invalid"
        )
        if checkpoint_file.name != base_model_name:
            _fail("checkpoint_name_mismatch")
        base_model_sha256 = _sha256(
            checkpoint.get("sha256"), "checkpoint_sha256_invalid"
        )
        if (
            base_model_sha256
            != _sha256(receipt.get("checkpointSha256"), "receipt_checkpoint_sha256_invalid")
            or cached_digest(checkpoint_file, code="checkpoint_invalid") != base_model_sha256
        ):
            _fail("checkpoint_digest_mismatch")

        model_file = _safe_child(
            receipt_publication.get("path"), lora_root_path, "published_model_path_invalid"
        )
        model_name = _relative_model_name(model_file, lora_root_path)
        if cached_digest(model_file, code="published_model_invalid") != publication_sha256:
            _fail("published_model_digest_mismatch")

        target = _resolve_character_target(database_file, external_id)
        if target["character_id"] in seen_characters:
            _fail("duplicate_canonical_character_binding")
        if model_name in seen_models:
            _fail("duplicate_published_model_binding")
        seen_characters.add(target["character_id"])
        seen_models.add(model_name)
        bindings.append({
            **target,
            "external_character_id": external_id,
            "capability_id": CHARACTER_LORA_CAPABILITY_ID,
            "base_model_name": base_model_name,
            "base_model_sha256": base_model_sha256,
            "model_name": model_name,
            "model_sha256": publication_sha256,
            "trigger_token": trigger_token,
            "model_strength": 1.0,
            "clip_strength": 1.0,
            "training_receipt_schema": RECEIPT_SCHEMA,
            "training_receipt_sha256": receipt_sha256,
            "qualification_state": "structurally_verified",
            "qualification_revision": 1,
            "revision": 1,
        })
    return bindings


def publish_bindings(
    database_path: str | os.PathLike[str],
    bindings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Idempotently append a previously verified binding plan."""

    store = ProviderFabricStore(database_path)
    published: list[dict[str, Any]] = []
    for binding in bindings:
        try:
            character = store.get_owned_character(
                str(binding["owner_user_id"]), str(binding["character_id"])
            )
            if character["visual_class"] != binding["visual_class"]:
                _fail("provider_character_changed_after_plan")
            published.append(store.append_owned_character_model_binding(
                str(binding["owner_user_id"]),
                str(binding["character_id"]),
                capability_id=str(binding["capability_id"]),
                base_model_name=str(binding["base_model_name"]),
                base_model_sha256=str(binding["base_model_sha256"]),
                model_name=str(binding["model_name"]),
                model_sha256=str(binding["model_sha256"]),
                trigger_token=str(binding["trigger_token"]),
                model_strength=float(binding["model_strength"]),
                clip_strength=float(binding["clip_strength"]),
                training_receipt_schema=str(binding["training_receipt_schema"]),
                training_receipt_sha256=str(binding["training_receipt_sha256"]),
                qualification_state=str(binding["qualification_state"]),
                qualification_revision=int(binding["qualification_revision"]),
                revision=int(binding["revision"]),
            ))
        except (KeyError, StoreConflictError) as error:
            raise CharacterModelPublishError("binding_publish_conflict") from error
    return published


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        default=(
            "~/.local/share/web-comfyui-clipmode/bulk-training/"
            "clipmode-819d9bfb-v1.state.json"
        ),
    )
    parser.add_argument("--database", default=os.fspath(default_database_path()))
    parser.add_argument("--artifact-root", default="~/.local/share/web-comfyui-clipmode")
    parser.add_argument("--lora-root", default="~/comfyui/models/loras")
    parser.add_argument("--checkpoint-root", default="~/comfyui/models/checkpoints")
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    bindings = build_publication_plan(
        arguments.state,
        arguments.database,
        artifact_root=arguments.artifact_root,
        lora_root=arguments.lora_root,
        checkpoint_root=arguments.checkpoint_root,
    )
    published = publish_bindings(arguments.database, bindings) if arguments.execute else []
    output = {
        "schema": OUTPUT_SCHEMA,
        "schema_version": 1,
        "mode": "execute" if arguments.execute else "dry_run",
        "verified_count": len(bindings),
        "published_count": len(published),
        "bindings": [
            {
                "character_id": binding["character_id"],
                "external_character_id": binding["external_character_id"],
                "model_name": binding["model_name"],
                "model_sha256": binding["model_sha256"],
                "revision": binding["revision"],
            }
            for binding in bindings
        ],
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
