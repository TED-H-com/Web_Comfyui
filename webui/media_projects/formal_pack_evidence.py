"""Resolve private Castle stores into safe formal-pack evidence bindings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from provider_fabric.h3_catalog import H3_VARIANT_CONTRACTS, h3_golden_id
from provider_fabric.store import ProviderFabricStore, ProviderFabricStoreError

from .rights_store import CharacterRightsStore


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _owner_only_bytes(path: Path, maximum: int = 64 * 1024) -> bytes:
    info = path.lstat()
    parent = path.parent.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
        or not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700
        or not 0 < info.st_size <= maximum
    ):
        raise ValueError("private evidence record is unsafe")
    raw = path.read_bytes()
    after = path.lstat()
    if len(raw) != info.st_size or (after.st_dev, after.st_ino, after.st_mtime_ns) != (
        info.st_dev, info.st_ino, info.st_mtime_ns,
    ):
        raise ValueError("private evidence record changed")
    return raw


def resolve_formal_pack_evidence(
    proposal: Mapping[str, Any],
    *,
    owner_user_id: str,
    provider_store: ProviderFabricStore,
    rights_store: CharacterRightsStore,
    h3_audit: Mapping[str, Any],
    h3_golden_record: Path | None = None,
) -> dict[str, Any]:
    """Return compiler inputs without exposing private record paths or raw receipts."""

    if (
        not isinstance(proposal, Mapping)
        or proposal.get("schema_version") != "castle.production-pack-proposal.v1"
        or not isinstance(provider_store, ProviderFabricStore)
        or not isinstance(rights_store, CharacterRightsStore)
        or not isinstance(h3_audit, Mapping)
    ):
        raise ValueError("formal pack evidence request is invalid")
    evidence = {}
    routes = proposal.get("route_variants")
    entities = proposal.get("entities")
    if not isinstance(routes, list) or not isinstance(entities, list):
        raise ValueError("formal pack proposal registries are invalid")

    accounts = {}
    required_providers = {
        item.get("provider_id") for item in routes if isinstance(item, Mapping)
    }
    if (
        len(required_providers) > 2
        or any(
            not isinstance(provider_id, str)
            or provider_id not in {"local_comfy", "autoip"}
            for provider_id in required_providers
        )
    ):
        raise ValueError("formal pack provider is unsupported")
    accessible = provider_store.list_accessible_provider_accounts(
        owner_user_id, include_disabled=False
    )
    for provider_id in sorted(required_providers):
        matches = [
            item for item in accessible
            if item.get("provider_id") == provider_id
            and item.get("enabled") is True
        ]
        if len(matches) != 1:
            raise ValueError(
                "formal pack provider account is ambiguous or unavailable"
            )
        accounts[provider_id] = matches[0]["account_id"]

    h3_routes = [
        item for item in routes
        if isinstance(item, Mapping) and item.get("provider_id") == "local_comfy"
    ]
    quality_h3_routes = [
        route for route in h3_routes
        if route.get("execution_basis", "quality_golden") == "quality_golden"
    ]
    quality_receipt_contracts: set[object] = set()
    quality_digest: str | None = None
    if quality_h3_routes:
        if h3_golden_record is None:
            raise ValueError("H3 golden evidence is not currently valid")
        raw = _owner_only_bytes(Path(h3_golden_record))
        try:
            receipt = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("H3 golden receipt is invalid") from error
        quality_receipt_contracts = {
            item.get("contract_id") for item in receipt.get("receipts", ()) if isinstance(item, Mapping)
            and item.get("accepted") is True and item.get("revoked") is False
        } if isinstance(receipt, Mapping) else set()
        quality_digest = hashlib.sha256(raw).hexdigest()

    contract_states = h3_audit.get("contracts")
    for route in h3_routes:
        variant_id = route.get("variant_id")
        contract_id = H3_VARIANT_CONTRACTS.get(variant_id)
        execution_basis = route.get("execution_basis", "quality_golden")
        if (
            not isinstance(contract_states, Mapping)
            or contract_states.get(contract_id) is not True
            or not isinstance(contract_id, str)
        ):
            raise ValueError("H3 technical evidence is not currently valid")
        if execution_basis == "technical_compatibility":
            technical_id = route.get("technical_evidence_id")
            expected_id = f"h3-runtime:{contract_id}"
            if (
                technical_id != expected_id
                or route.get("execution_evidence_id") != expected_id
            ):
                raise ValueError("H3 technical evidence is not currently valid")
            material = {
                "schema_version": "castle.h3-technical-evidence.v1",
                "provider_id": "local_comfy",
                "provider_account_id": accounts["local_comfy"],
                "variant_id": variant_id,
                "contract_id": contract_id,
                "technical_evidence_id": technical_id,
                "contract_ready": True,
            }
            digest = hashlib.sha256(json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")).hexdigest()
            evidence[technical_id] = {
                "evidence_id": technical_id,
                "kind": "technical_compatibility",
                "title": "Current MiniMAX H3 technical compatibility",
                "source_uri": (
                    f"castle://minimax-h3-readiness/{contract_id}.json"
                ),
                "source_hash": digest,
                "is_private": True,
            }
        elif execution_basis == "quality_golden":
            golden_id = route.get("golden_id")
            if (
                contract_id not in quality_receipt_contracts
                or golden_id != h3_golden_id(contract_id)
                or route.get("execution_evidence_id", golden_id) != golden_id
                or quality_digest is None
            ):
                raise ValueError("H3 golden evidence is not currently valid")
            evidence[golden_id] = {
                "evidence_id": golden_id, "kind": "golden",
                "title": "Accepted MiniMAX H3 golden receipt",
                "source_uri": f"castle://minimax-h3-goldens/{golden_id}.json",
                "source_hash": quality_digest, "is_private": True,
            }
        else:
            raise ValueError("H3 execution evidence basis is invalid")

    autoip_routes = [
        item for item in routes
        if isinstance(item, Mapping) and item.get("provider_id") == "autoip"
    ]
    if autoip_routes:
        account_id = accounts["autoip"]
        try:
            snapshot = provider_store.latest_accessible_capability_snapshot(
                owner_user_id, account_id
            )
        except (KeyError, ValueError, ProviderFabricStoreError) as error:
            raise ValueError(
                "AutoIP technical evidence is unavailable"
            ) from error
        if not isinstance(snapshot, Mapping):
            raise ValueError("AutoIP technical evidence is unavailable")
        offerings = snapshot.get("offerings")
        content_sha256 = snapshot.get("content_sha256")
        if (
            snapshot.get("account_id") != account_id
            or not isinstance(offerings, list)
            or not isinstance(content_sha256, str)
            or _SHA256.fullmatch(content_sha256) is None
        ):
            raise ValueError("AutoIP technical evidence is unavailable")
        for route in autoip_routes:
            technical_id = route.get("technical_evidence_id")
            if (
                route.get("execution_basis") != "technical_compatibility"
                or not isinstance(technical_id, str)
                or route.get("execution_evidence_id") != technical_id
                or snapshot.get("source_revision") != technical_id
            ):
                raise ValueError("AutoIP technical evidence is not current")
            matches = [
                offering for offering in offerings
                if isinstance(offering, Mapping)
                and offering.get("variant_id") == route.get("variant_id")
                and offering.get("capability") == route.get("capability")
                and offering.get("evidence") == "verified"
                and offering.get("evidence_id") == technical_id
                and offering.get("enabled") is True
                and isinstance(offering.get("visual_classes"), list)
                and route.get("visual_class") in (
                    offering.get("visual_classes") or ()
                )
                and isinstance(offering.get("option_keys"), list)
                and set(offering.get("option_keys") or ())
                    == set(route.get("option_keys") or ())
            ]
            if len(matches) != 1:
                raise ValueError("AutoIP technical evidence is not current")
            evidence[technical_id] = {
                "evidence_id": technical_id,
                "kind": "technical_compatibility",
                "title": "Current AutoIP account compatibility",
                "source_uri": (
                    f"castle://autoip-compatibility/{technical_id}.json"
                ),
                "source_hash": content_sha256,
                "is_private": True,
            }
    rights = {}
    for entity in entities:
        if not isinstance(entity, Mapping) or entity.get("visual_class") != "realistic":
            continue
        character_id = entity.get("entity_id")
        record = rights_store.get(owner_user_id, character_id)
        if record is None or record.revoked:
            raise ValueError("real person rights evidence is unavailable")
        rights[character_id] = {
            **record.public_summary(),
            "consent_ref": record.consent_ref,
            "evidence_id": record.evidence_id,
        }
        evidence[record.evidence_id] = {
            "evidence_id": record.evidence_id, "kind": "consent",
            "title": "Private real-person consent evidence",
            "source_uri": f"castle://character-rights/{record.evidence_id}.json",
            "source_hash": record.evidence_sha256, "is_private": True,
        }
    return {
        "evidence_by_id": evidence,
        "rights_by_character": rights,
        "provider_accounts": accounts,
        "private_paths_exposed": False,
    }


__all__ = ["resolve_formal_pack_evidence"]
