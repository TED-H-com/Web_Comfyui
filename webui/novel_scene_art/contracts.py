"""Strict, canonical Oracle-to-Castle Novel Scene Art contracts."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import SceneArtError, fail
from .manifests import RenderRelease, canonical_json, reject_invalid_unicode
from .protocol import (
    ADULT_EVIDENCE_POLICY_REVISION,
    HASH,
    MAX_SAFE_INTEGER,
    OUTPUT_HEIGHT,
    OUTPUT_MIME,
    OUTPUT_WIDTH,
    RECIPE_ID,
    RENDER_PLAN_SCHEMA,
    RENDER_RECEIPT_SCHEMA,
    RENDER_REQUEST_SCHEMA,
    SCENE_PACK_SCHEMA,
    SEED_POLICY,
    STYLE_PRESET,
    WORKFLOW_CONTRACT_REVISION,
    digest,
    safe_id,
)


MAX_CLAIM_BYTES = 96 * 1024
MAX_SCENE_PACK_BYTES = 64 * 1024
MAX_RENDER_PLAN_BYTES = 32 * 1024
UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
)
CLAIM_KEYS = frozenset(
    {"requestId", "jobId", "leaseToken", "leaseExpiresAt", "attempt", "payloadHash", "renderRequest"}
)
RENDER_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "job_id",
        "request_id",
        "recipe_id",
        "style_preset",
        "workflow_contract_revision",
        "generation_key",
        "scene_pack_hash",
        "scene_pack",
        "render_plan_hash",
        "render_plan",
        "output",
    }
)
SCENE_PACK_KEYS = frozenset(
    {
        "schema_version",
        "recipe",
        "source",
        "moment",
        "point_of_view",
        "participants",
        "identity_slots",
        "presentation",
        "story_context",
        "render",
    }
)
IDENTITY_PUBLIC_KEYS = frozenset(
    {
        "persona_id",
        "identity_pack_revision",
        "artifact_manifest_sha256",
        "visual_class",
        "adult_attestation_revision",
        "provenance_ref",
        "license_ref",
        "qualification_receipt_sha256",
    }
)
PLAN_KEYS = frozenset(
    {
        "schema_version",
        "workflow_contract_revision",
        "renderer_release_digest",
        "profile_id",
        "adapter",
        "workflow",
        "compiler",
        "model_manifest",
        "identity_manifest",
        "template_catalog",
        "screening",
        "max_identity_slots",
        "seed_policy",
        "variation_index",
        "seed",
        "output",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "workflow_contract_revision",
        "job_id",
        "request_id",
        "generation_key",
        "scene_pack_hash",
        "render_plan_hash",
        "renderer_release_digest",
        "variation_index",
        "seed",
        "execution",
        "output",
    }
)


def strict_json(raw: bytes, *, maximum: int = MAX_CLAIM_BYTES) -> object:
    if not raw or len(raw) > maximum:
        fail("contract_invalid", "JSON body has an invalid size")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        reject_invalid_unicode(value)
        return value
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise SceneArtError("contract_invalid", "JSON body is invalid") from error


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail("contract_invalid", f"{label} shape is invalid")
    return value


def _keys(value: object, required: set[str], allowed: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(allowed):
        fail("contract_invalid", f"{label} shape is invalid")
    return value


def _text(value: object, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    try:
        encoded_length = len(value.encode("utf-8", "strict")) if isinstance(value, str) else -1
    except UnicodeError:
        encoded_length = -1
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or encoded_length < 0
        or encoded_length > maximum
        or "\x00" in value
    ):
        fail("contract_invalid", f"{label} is invalid")
    return value


def _integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or value < low or value > high:
        fail("contract_invalid", f"{label} is invalid")
    return value


def _canonical_bounded(value: object, label: str, maximum: int) -> bytes:
    try:
        encoded = canonical_json(value)
    except SceneArtError as error:
        raise SceneArtError("contract_invalid", f"{label} is not canonical JSON") from error
    if len(encoded) > maximum:
        fail("contract_invalid", f"{label} exceeds its canonical size limit")
    return encoded


def _validate_scene_pack(value: object) -> Mapping[str, Any]:
    pack = _object(value, SCENE_PACK_KEYS, "scene_pack")
    if pack.get("schema_version") != SCENE_PACK_SCHEMA:
        fail("contract_invalid", "ScenePack schema is unsupported")
    recipe = _object(
        pack.get("recipe"),
        frozenset({"id", "style_preset", "workflow_contract_revision"}),
        "scene_pack.recipe",
    )
    if recipe != {
        "id": RECIPE_ID,
        "style_preset": STYLE_PRESET,
        "workflow_contract_revision": WORKFLOW_CONTRACT_REVISION,
    }:
        fail("contract_invalid", "ScenePack recipe binding changed")
    source = _object(
        pack.get("source"),
        frozenset({"book_id", "book_revision", "chapter_ordinal", "source_revision", "output_locale"}),
        "scene_pack.source",
    )
    _text(source.get("book_id"), "book_id", 256)
    digest(source.get("book_revision"), "book_revision")
    _integer(source.get("chapter_ordinal"), "chapter_ordinal", 1, 1_000_000)
    digest(source.get("source_revision"), "source_revision")
    _text(source.get("output_locale"), "output_locale", 32)

    moment = _object(
        pack.get("moment"),
        frozenset(
            {
                "revision",
                "cast_revision",
                "source_beat_ordinal",
                "source_beat_offset",
                "active_scene_id",
                "scene_certificate",
                "stage_revision",
                "character_profile_hash",
                "continuity_digest_hash",
                "content_route",
                "adult_evidence_policy_revision",
                "adult_evidence_set_hash",
            }
        ),
        "scene_pack.moment",
    )
    for key in ("revision", "cast_revision", "source_beat_ordinal", "source_beat_offset"):
        _integer(moment.get(key), key, 0, MAX_SAFE_INTEGER)
    if moment.get("active_scene_id") is not None:
        _text(moment.get("active_scene_id"), "active_scene_id", 256)
    for key in (
        "scene_certificate",
        "stage_revision",
        "character_profile_hash",
        "continuity_digest_hash",
        "adult_evidence_set_hash",
    ):
        digest(moment.get(key), key)
    if moment.get("content_route") != "explicit":
        fail("contract_invalid", "Only the server-proven explicit route is accepted")
    if moment.get("adult_evidence_policy_revision") != ADULT_EVIDENCE_POLICY_REVISION:
        fail("contract_invalid", "Adult evidence policy revision changed")

    point_of_view = _object(
        pack.get("point_of_view"),
        frozenset({"reader_character_id", "reader_role_name", "depiction"}),
        "scene_pack.point_of_view",
    )
    reader_id = _text(point_of_view.get("reader_character_id"), "reader_character_id", 256)
    _text(point_of_view.get("reader_role_name"), "reader_role_name", 320)
    if point_of_view.get("depiction") != "context_only":
        fail("contract_invalid", "Reader depiction policy changed")

    participants = pack.get("participants")
    if not isinstance(participants, list) or not participants or len(participants) > 257:
        fail("contract_invalid", "Participant list is invalid")
    participant_ids: set[str] = set()
    drawable_ids: set[str] = set()
    for index, raw in enumerate(participants):
        item = _object(
            raw,
            frozenset({"character_id", "character_name", "adult_status", "adult_evidence_digest", "depiction"}),
            f"participants[{index}]",
        )
        character_id = _text(item.get("character_id"), "character_id", 256)
        if character_id in participant_ids:
            fail("contract_invalid", "Participant identifiers must be unique")
        participant_ids.add(character_id)
        _text(item.get("character_name"), "character_name", 320)
        if item.get("adult_status") != "adult":
            fail("contract_invalid", "Every participant must be server-attested adult")
        digest(item.get("adult_evidence_digest"), "adult_evidence_digest")
        if item.get("depiction") not in {"context_only", "identity_slot"}:
            fail("contract_invalid", "Participant depiction is invalid")
        if item.get("depiction") == "identity_slot":
            drawable_ids.add(character_id)
    if reader_id not in participant_ids or reader_id in drawable_ids:
        fail("contract_invalid", "Reader must remain context-only")

    slots = pack.get("identity_slots")
    if not isinstance(slots, list) or not slots or len(slots) > 8:
        fail("contract_invalid", "Identity slot list is invalid")
    seen_personas: set[str] = set()
    seen_characters: set[str] = set()
    for index, raw in enumerate(slots):
        item = _object(
            raw,
            frozenset(
                {
                    "character_id",
                    "character_name",
                    "persona_id",
                    "identity_pack_revision",
                    "identity_artifact_manifest_sha256",
                    "visual_class",
                    "adult_attestation_revision",
                    "provenance_ref",
                    "license_ref",
                    "qualification_receipt_sha256",
                    "visual_profile",
                }
            ),
            f"identity_slots[{index}]",
        )
        character_id = _text(item.get("character_id"), "character_id", 256)
        persona_id = safe_id(item.get("persona_id"), "persona_id")
        if (
            character_id not in drawable_ids
            or character_id in seen_characters
            or persona_id in seen_personas
            or character_id == reader_id
        ):
            fail("contract_invalid", "Identity slot mapping is invalid")
        seen_characters.add(character_id)
        seen_personas.add(persona_id)
        _text(item.get("character_name"), "character_name", 320)
        safe_id(item.get("identity_pack_revision"), "identity_pack_revision")
        digest(item.get("identity_artifact_manifest_sha256"), "identity_artifact_manifest_sha256")
        if item.get("visual_class") != "fictional_animated_adult":
            fail("contract_invalid", "Identity visual class is invalid")
        for key in ("adult_attestation_revision", "provenance_ref", "license_ref"):
            safe_id(item.get(key), key)
        digest(item.get("qualification_receipt_sha256"), "qualification_receipt_sha256")
        visual = _object(
            item.get("visual_profile"),
            frozenset({"data_boundary", "appearance", "digest"}),
            "visual_profile",
        )
        if visual.get("data_boundary") != "untrusted_frozen_character_metadata":
            fail("contract_invalid", "Visual profile boundary is invalid")
        appearance = _text(visual.get("appearance"), "appearance", 1_920)
        profile_digest = digest(visual.get("digest"), "visual_profile.digest")
        expected_profile_digest = hashlib.sha256(
            canonical_json(
                {
                    "schemaVersion": "novel-scene-art-visual-profile-v1",
                    "characterId": character_id,
                    "appearance": appearance,
                }
            )
        ).hexdigest()
        if profile_digest != expected_profile_digest:
            fail("contract_invalid", "Visual profile digest does not match")
    if seen_characters != drawable_ids:
        fail("contract_invalid", "Every drawable participant needs one identity slot")

    presentation = _keys(
        pack.get("presentation"),
        {"book_title", "chapter_title", "scene_heading", "scene_ordinal"},
        {"book_title", "chapter_title", "scene_heading", "scene_ordinal", "scene_family"},
        "scene_pack.presentation",
    )
    for key in ("book_title", "chapter_title", "scene_heading"):
        _text(presentation.get(key), key, 640, allow_empty=True)
    _integer(presentation.get("scene_ordinal"), "scene_ordinal", 0, 1_000_000)
    if "scene_family" in presentation:
        safe_id(presentation.get("scene_family"), "scene_family")

    story = _object(
        pack.get("story_context"),
        frozenset({"data_boundary", "chapter_summary", "recent_cues"}),
        "scene_pack.story_context",
    )
    if story.get("data_boundary") != "untrusted_story_data":
        fail("contract_invalid", "Story context boundary is invalid")
    _text(story.get("chapter_summary"), "chapter_summary", 4_000, allow_empty=True)
    cues = story.get("recent_cues")
    if not isinstance(cues, list) or len(cues) > 12:
        fail("contract_invalid", "Recent cue list is invalid")
    for index, raw in enumerate(cues):
        item = _keys(
            raw,
            {"event_type", "kind", "text", "text_provenance"},
            {"event_type", "kind", "text", "text_provenance", "speaker_name", "actor_name"},
            f"recent_cues[{index}]",
        )
        for key, item_value in item.items():
            _text(item_value, f"recent_cues[{index}].{key}", 2_048, allow_empty=True)
    render = _object(
        pack.get("render"),
        frozenset({"medium", "width", "height", "identity_policy"}),
        "scene_pack.render",
    )
    if render != {
        "medium": "image",
        "width": OUTPUT_WIDTH,
        "height": OUTPUT_HEIGHT,
        "identity_policy": "assigned_non_reader_slots_only",
    }:
        fail("contract_invalid", "Scene render profile changed")
    return pack


def _identity_from_slot(slot: Mapping[str, Any]) -> Mapping[str, object]:
    return {
        "persona_id": slot["persona_id"],
        "identity_pack_revision": slot["identity_pack_revision"],
        "artifact_manifest_sha256": slot["identity_artifact_manifest_sha256"],
        "visual_class": slot["visual_class"],
        "adult_attestation_revision": slot["adult_attestation_revision"],
        "provenance_ref": slot["provenance_ref"],
        "license_ref": slot["license_ref"],
        "qualification_receipt_sha256": slot["qualification_receipt_sha256"],
    }


def _expected_plan(
    value: object,
    scene_pack: Mapping[str, Any],
    release: RenderRelease,
) -> tuple[Mapping[str, Any], int, int]:
    plan = _object(value, PLAN_KEYS, "render_plan")
    variation = _integer(plan.get("variation_index"), "variation_index", 0, 1_000_000)
    seed = _integer(plan.get("seed"), "seed", 0, MAX_SAFE_INTEGER)
    pack_slots = scene_pack["identity_slots"]
    assert isinstance(pack_slots, list)
    maximum_slots = release.public_release["max_identity_slots"]
    assert isinstance(maximum_slots, int)
    if len(pack_slots) > maximum_slots:
        fail("render_release_mismatch", "ScenePack exceeds the renderer identity-slot limit")
    expected_slots: list[Mapping[str, object]] = []
    for slot in pack_slots:
        assert isinstance(slot, dict)
        identity = release.identities.get((str(slot["persona_id"]), str(slot["identity_pack_revision"])))
        if identity is None or _identity_from_slot(slot) != identity.public_identity:
            fail("render_release_mismatch", "ScenePack identity is not in the Castle release")
        expected_slots.append(identity.public_identity)
    expected_slots.sort(key=lambda row: str(row["persona_id"]))
    public = release.public_release
    public_identity = public["identity_manifest"]
    assert isinstance(public_identity, dict)
    expected: Mapping[str, Any] = {
        "schema_version": RENDER_PLAN_SCHEMA,
        "workflow_contract_revision": WORKFLOW_CONTRACT_REVISION,
        "renderer_release_digest": release.release_digest,
        "profile_id": public["profile_id"],
        "adapter": public["adapter"],
        "workflow": public["workflow"],
        "compiler": public["compiler"],
        "model_manifest": public["model_manifest"],
        "identity_manifest": {
            "id": public_identity["id"],
            "revision": public_identity["revision"],
            "manifest_sha256": public_identity["manifest_sha256"],
            "slots": expected_slots,
        },
        "template_catalog": public["template_catalog"],
        "screening": public["screening"],
        "max_identity_slots": public["max_identity_slots"],
        "seed_policy": SEED_POLICY,
        "variation_index": variation,
        "seed": seed,
        "output": public["output"],
    }
    if plan != expected:
        fail("render_release_mismatch", "Render plan does not match the Castle release")
    return plan, variation, seed


@dataclass(frozen=True, slots=True)
class RenderClaim:
    request_id: str
    job_id: str
    lease_token: str
    lease_expires_at_ms: int
    attempt: int
    payload_hash: str
    generation_key: str
    scene_pack_hash: str
    scene_pack: Mapping[str, Any]
    render_plan_hash: str
    render_plan: Mapping[str, Any]
    renderer_release_digest: str
    variation_index: int
    seed: int
    max_output_bytes: int


def parse_claim(value: object, *, release: RenderRelease) -> RenderClaim:
    claim = _object(value, CLAIM_KEYS, "claim")
    _canonical_bounded(claim, "Claim", MAX_CLAIM_BYTES)
    request_id = safe_id(claim.get("requestId"), "requestId")
    job_id = safe_id(claim.get("jobId"), "jobId")
    lease_token = safe_id(claim.get("leaseToken"), "leaseToken")
    lease_expires = _integer(claim.get("leaseExpiresAt"), "leaseExpiresAt", 1, MAX_SAFE_INTEGER)
    attempt = _integer(claim.get("attempt"), "attempt", 1, 1_000)
    payload_hash = digest(claim.get("payloadHash"), "payloadHash")
    request = _object(claim.get("renderRequest"), RENDER_REQUEST_KEYS, "renderRequest")
    if (
        request.get("schema_version") != RENDER_REQUEST_SCHEMA
        or request.get("job_id") != job_id
        or request.get("request_id") != request_id
        or request.get("recipe_id") != RECIPE_ID
        or request.get("style_preset") != STYLE_PRESET
        or request.get("workflow_contract_revision") != WORKFLOW_CONTRACT_REVISION
    ):
        fail("contract_invalid", "Render request binding is invalid")
    scene_pack_hash = digest(request.get("scene_pack_hash"), "scene_pack_hash")
    raw_scene_pack = request.get("scene_pack")
    scene_pack_bytes = _canonical_bounded(
        raw_scene_pack,
        "ScenePack",
        MAX_SCENE_PACK_BYTES,
    )
    scene_pack = _validate_scene_pack(raw_scene_pack)
    if hashlib.sha256(scene_pack_bytes).hexdigest() != scene_pack_hash:
        fail("scene_pack_hash_mismatch", "ScenePack canonical hash does not match")
    render_plan_hash = digest(request.get("render_plan_hash"), "render_plan_hash")
    raw_render_plan = request.get("render_plan")
    render_plan_bytes = _canonical_bounded(
        raw_render_plan,
        "RenderPlan",
        MAX_RENDER_PLAN_BYTES,
    )
    render_plan, variation, seed = _expected_plan(raw_render_plan, scene_pack, release)
    if hashlib.sha256(render_plan_bytes).hexdigest() != render_plan_hash:
        fail("render_plan_hash_mismatch", "Render plan canonical hash does not match")
    seed_hash = hashlib.sha256(
        canonical_json(
            {
                "schemaVersion": SEED_POLICY,
                "rendererReleaseDigest": release.release_digest,
                "scenePackHash": scene_pack_hash,
                "variationIndex": variation,
            }
        )
    ).hexdigest()
    if seed != int(seed_hash[:13], 16):
        fail("render_plan_seed_mismatch", "Render plan seed is not server-derived")
    output = _object(
        request.get("output"), frozenset({"mime_type", "width", "height", "max_bytes"}), "output"
    )
    if output != release.public_release["output"] or output != render_plan["output"]:
        fail("contract_invalid", "Output profile changed")
    generation_key = digest(request.get("generation_key"), "generation_key")
    expected_generation = hashlib.sha256(
        canonical_json(
            {
                "recipeId": RECIPE_ID,
                "stylePreset": STYLE_PRESET,
                "workflowContractRevision": WORKFLOW_CONTRACT_REVISION,
                "scenePackHash": scene_pack_hash,
                "renderPlanHash": render_plan_hash,
                "rendererReleaseDigest": release.release_digest,
                "variationIndex": variation,
                "seed": seed,
                "output": [OUTPUT_MIME, OUTPUT_WIDTH, OUTPUT_HEIGHT],
            }
        )
    ).hexdigest()
    if generation_key != expected_generation:
        fail("generation_key_mismatch", "Generation key does not match the exact render plan")
    expected_payload = hashlib.sha256(
        canonical_json(
            {
                "schemaVersion": RENDER_REQUEST_SCHEMA,
                "workflowContractRevision": WORKFLOW_CONTRACT_REVISION,
                "jobId": job_id,
                "requestId": request_id,
                "generationKey": generation_key,
                "scenePackHash": scene_pack_hash,
                "renderPlanHash": render_plan_hash,
                "rendererReleaseDigest": release.release_digest,
            }
        )
    ).hexdigest()
    if payload_hash != expected_payload:
        fail("payload_hash_mismatch", "Oracle outbox hash does not match the v2 formula")
    return RenderClaim(
        request_id,
        job_id,
        lease_token,
        lease_expires,
        attempt,
        payload_hash,
        generation_key,
        scene_pack_hash,
        scene_pack,
        render_plan_hash,
        render_plan,
        release.release_digest,
        variation,
        seed,
        int(output["max_bytes"]),
    )


def canonical_render_receipt(
    value: object,
    *,
    maximum_bytes: int,
) -> tuple[Mapping[str, Any], str]:
    receipt = _object(value, RECEIPT_KEYS, "render_receipt")
    execution = _object(
        receipt.get("execution"),
        frozenset(
            {
                "adapter_id",
                "adapter_revision",
                "prompt_id",
                "compiled_intent_sha256",
                "positive_prompt_sha256",
                "negative_prompt_sha256",
                "compiled_graph_sha256",
                "screening_receipt_sha256",
            }
        ),
        "render_receipt.execution",
    )
    output = _object(
        receipt.get("output"),
        frozenset({"mime_type", "sha256", "byte_count", "width", "height"}),
        "render_receipt.output",
    )
    if (
        receipt.get("schema_version") != RENDER_RECEIPT_SCHEMA
        or receipt.get("workflow_contract_revision") != WORKFLOW_CONTRACT_REVISION
        or output.get("mime_type") != OUTPUT_MIME
        or output.get("width") != OUTPUT_WIDTH
        or output.get("height") != OUTPUT_HEIGHT
    ):
        fail("render_receipt_invalid", "Render receipt profile is invalid")
    for key in ("job_id", "request_id"):
        safe_id(receipt.get(key), f"render_receipt.{key}")
    for key in ("generation_key", "scene_pack_hash", "render_plan_hash", "renderer_release_digest"):
        digest(receipt.get(key), f"render_receipt.{key}")
    _integer(receipt.get("variation_index"), "variation_index", 0, 1_000_000)
    _integer(receipt.get("seed"), "seed", 0, MAX_SAFE_INTEGER)
    safe_id(execution.get("adapter_id"), "adapter_id")
    safe_id(execution.get("adapter_revision"), "adapter_revision")
    prompt_id = execution.get("prompt_id")
    try:
        canonical_uuid = str(uuid.UUID(str(prompt_id)))
    except (ValueError, AttributeError) as error:
        raise SceneArtError("render_receipt_invalid", "Prompt ID is invalid") from error
    if prompt_id != canonical_uuid or UUID_RE.fullmatch(canonical_uuid) is None:
        fail("render_receipt_invalid", "Prompt ID is not canonical")
    for key in (
        "compiled_intent_sha256",
        "positive_prompt_sha256",
        "negative_prompt_sha256",
        "compiled_graph_sha256",
        "screening_receipt_sha256",
    ):
        digest(execution.get(key), key)
    digest(output.get("sha256"), "output.sha256")
    _integer(output.get("byte_count"), "output.byte_count", 1, maximum_bytes)
    encoded = canonical_json(receipt)
    if len(encoded) > 8 * 1024:
        fail("render_receipt_invalid", "Render receipt is too large")
    return receipt, hashlib.sha256(encoded).hexdigest()
