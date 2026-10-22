"""Strict browser-safe API helpers for real-person rights records."""

from __future__ import annotations

from typing import Any, Mapping

from .rights_store import CharacterRightsRecord, CharacterRightsStore


PUT_VERSION = "castle.character-rights-put.v1"
REVOKE_VERSION = "castle.character-rights-revoke.v1"
RESPONSE_VERSION = "castle.character-rights.response.v1"


def _exact(body: Mapping[str, Any], fields: set[str]) -> None:
    if not isinstance(body, Mapping) or set(body) != fields:
        raise ValueError("character rights request fields are invalid")


def rights_response(record: CharacterRightsRecord | None, character_id: str) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_VERSION,
        "ok": True,
        "character_id": character_id,
        "rights": None if record is None else record.public_summary(),
        "authority": {
            "owner_scoped": True,
            "private_evidence_exposed": False,
            "generation_authorized": False,
        },
    }


def get_character_rights_api(
    store: CharacterRightsStore, owner_user_id: str, character_id: str,
) -> dict[str, Any]:
    return rights_response(store.get(owner_user_id, character_id), character_id)


def put_character_rights_api(
    store: CharacterRightsStore,
    owner_user_id: str,
    character_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    _exact(body, {
        "schema_version", "expected_revision", "consent_state", "consent_ref",
        "evidence_id", "evidence_sha256", "allowed_provider_ids",
        "allowed_capabilities", "allowed_usage_scopes",
    })
    if body["schema_version"] != PUT_VERSION:
        raise ValueError("character rights request version is invalid")
    expected = body["expected_revision"]
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ValueError("expected_revision is invalid")
    record = CharacterRightsRecord(
        owner_user_id=owner_user_id,
        character_id=character_id,
        consent_state=body["consent_state"],
        consent_ref=body["consent_ref"],
        evidence_id=body["evidence_id"],
        evidence_sha256=body["evidence_sha256"],
        allowed_provider_ids=tuple(body["allowed_provider_ids"]),
        allowed_capabilities=tuple(body["allowed_capabilities"]),
        allowed_usage_scopes=tuple(body["allowed_usage_scopes"]),
    )
    return rights_response(store.put(record, expected_revision=expected), character_id)


def revoke_character_rights_api(
    store: CharacterRightsStore,
    owner_user_id: str,
    character_id: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    _exact(body, {"schema_version", "expected_revision", "confirmed"})
    expected = body["expected_revision"]
    if (
        body["schema_version"] != REVOKE_VERSION or body["confirmed"] is not True
        or isinstance(expected, bool) or not isinstance(expected, int) or expected < 1
    ):
        raise ValueError("character rights revocation is invalid")
    return rights_response(
        store.revoke(owner_user_id, character_id, expected_revision=expected), character_id,
    )


__all__ = [
    "PUT_VERSION", "RESPONSE_VERSION", "REVOKE_VERSION",
    "get_character_rights_api", "put_character_rights_api", "revoke_character_rights_api",
]
