from __future__ import annotations

import hashlib
import json
import re


COVER_AUDIT_SCHEMA = "castle-reel-cover-audit-v1"
COVER_AUTHORITY_SCHEMA = "reel5_cover_authority_v1_20260711"
COVER_METADATA_SCHEMA = "reel5_cover_meta_v4_20260715"
COVER_TITLE_LAYOUT_SCHEMA = "reel5_cover_title_layout_v1_20260715"
COVER_COMPOSITION_SCHEMA = "reel5_cover_composition_v1_20260715"
CANONICAL_COVER_MODE = "gpt-image-2"

SHA256_RE = re.compile(r"[0-9a-f]{64}")


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} fields are noncanonical")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a sha256 digest")
    return value


def _int_box(value: object, length: int, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != length
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{label} is malformed")
    return list(value)


def _inside(inner: list[int], outer: list[int]) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
        and inner[0] < inner[2]
        and inner[1] < inner[3]
    )


def validate_cover_audit(
    value: object,
    *,
    expected_final_sha256: str | None = None,
    expected_metadata_sha256: str | None = None,
    expected_authority_sha256: str | None = None,
) -> dict:
    """Validate the compact, path-free projection of canonical cover authority."""
    audit = _exact_keys(
        value,
        {
            "schema",
            "authority_schema",
            "authority_sha256",
            "metadata_schema",
            "metadata_sha256",
            "mode",
            "prompt_sha256",
            "key_visual_sha256",
            "final_png_sha256",
            "title_layout",
            "canonical_composition",
        },
        "cover audit",
    )
    if audit["schema"] != COVER_AUDIT_SCHEMA:
        raise ValueError("cover audit schema is noncanonical")
    if audit["authority_schema"] != COVER_AUTHORITY_SCHEMA:
        raise ValueError("cover authority schema is noncanonical")
    if audit["metadata_schema"] != COVER_METADATA_SCHEMA:
        raise ValueError("cover metadata schema is noncanonical")
    if audit["mode"] != CANONICAL_COVER_MODE:
        raise ValueError("cover audit mode is noncanonical")

    authority_sha = _sha(audit["authority_sha256"], "cover authority sha256")
    metadata_sha = _sha(audit["metadata_sha256"], "cover metadata sha256")
    _sha(audit["prompt_sha256"], "cover prompt sha256")
    _sha(audit["key_visual_sha256"], "cover key visual sha256")
    final_sha = _sha(audit["final_png_sha256"], "cover final sha256")

    layout = _exact_keys(
        audit["title_layout"],
        {
            "schema",
            "policy_fingerprint",
            "evidence_sha256",
            "title_sha256",
            "canvas",
            "safe_box",
            "content_bbox",
            "typography_bbox",
        },
        "cover title layout audit",
    )
    if layout["schema"] != COVER_TITLE_LAYOUT_SCHEMA:
        raise ValueError("cover title layout schema is noncanonical")
    _sha(layout["policy_fingerprint"], "cover title policy fingerprint")
    _sha(layout["evidence_sha256"], "cover title evidence sha256")
    _sha(layout["title_sha256"], "cover title sha256")
    canvas = _int_box(layout["canvas"], 2, "cover title canvas")
    if canvas != [1080, 1920]:
        raise ValueError("cover title canvas is noncanonical")
    canvas_box = [0, 0, canvas[0], canvas[1]]
    safe_box = _int_box(layout["safe_box"], 4, "cover title safe box")
    content_box = _int_box(layout["content_bbox"], 4, "cover title content box")
    typography_box = _int_box(
        layout["typography_bbox"], 4, "cover title typography box")
    if (
        not _inside(safe_box, canvas_box)
        or not _inside(content_box, safe_box)
        or not _inside(typography_box, safe_box)
    ):
        raise ValueError("cover title audit bounds are noncanonical")

    composition = _exact_keys(
        audit["canonical_composition"],
        {
            "schema",
            "policy_fingerprint",
            "evidence_sha256",
            "key_visual_size",
            "key_visual_pixel_sha256",
            "final_size",
            "final_mode",
            "final_png_sha256",
            "final_pixel_sha256",
            "title_layout_policy_fingerprint",
        },
        "cover composition audit",
    )
    if composition["schema"] != COVER_COMPOSITION_SCHEMA:
        raise ValueError("cover composition schema is noncanonical")
    _sha(composition["policy_fingerprint"], "cover composition policy fingerprint")
    _sha(composition["evidence_sha256"], "cover composition evidence sha256")
    _sha(composition["key_visual_pixel_sha256"], "cover key visual pixel sha256")
    _sha(composition["final_pixel_sha256"], "cover final pixel sha256")
    composition_final_sha = _sha(
        composition["final_png_sha256"], "cover composition final sha256")
    key_visual_size = _int_box(
        composition["key_visual_size"], 2, "cover key visual size")
    final_size = _int_box(composition["final_size"], 2, "cover final size")
    if any(item <= 0 for item in key_visual_size):
        raise ValueError("cover key visual size is noncanonical")
    if final_size != canvas or composition["final_mode"] != "RGB":
        raise ValueError("cover composition final raster is noncanonical")
    if composition_final_sha != final_sha:
        raise ValueError("cover composition final digest differs from cover authority")
    if composition["title_layout_policy_fingerprint"] != layout["policy_fingerprint"]:
        raise ValueError("cover composition title policy differs from title layout")

    for actual, expected, label in (
        (final_sha, expected_final_sha256, "cover final"),
        (metadata_sha, expected_metadata_sha256, "cover metadata"),
        (authority_sha, expected_authority_sha256, "cover authority"),
    ):
        if expected is not None and actual != _sha(expected, f"expected {label} sha256"):
            raise ValueError(f"{label} digest differs from compact audit")
    return audit


def build_cover_audit(*, authority: dict, metadata: dict) -> dict:
    """Project canonical verifier output to a compact, reusable audit record."""
    layout = metadata.get("title_layout")
    composition = metadata.get("canonical_composition")
    if not isinstance(layout, dict) or not isinstance(composition, dict):
        raise ValueError("canonical cover composition evidence is missing")
    audit = {
        "schema": COVER_AUDIT_SCHEMA,
        "authority_schema": authority.get("schema"),
        "authority_sha256": authority.get("authority_sha256"),
        "metadata_schema": metadata.get("schema"),
        "metadata_sha256": authority.get("metadata_sha256"),
        "mode": metadata.get("mode"),
        "prompt_sha256": metadata.get("prompt_sha256"),
        "key_visual_sha256": authority.get("key_visual_sha256"),
        "final_png_sha256": authority.get("final_png_sha256"),
        "title_layout": {
            "schema": layout.get("schema"),
            "policy_fingerprint": layout.get("policy_fingerprint"),
            "evidence_sha256": canonical_json_sha256(layout),
            "title_sha256": layout.get("title_sha256"),
            "canvas": layout.get("canvas"),
            "safe_box": layout.get("safe_box"),
            "content_bbox": layout.get("content_bbox"),
            "typography_bbox": layout.get("typography_bbox"),
        },
        "canonical_composition": {
            "schema": composition.get("schema"),
            "policy_fingerprint": composition.get("policy_fingerprint"),
            "evidence_sha256": canonical_json_sha256(composition),
            "key_visual_size": composition.get("key_visual_size"),
            "key_visual_pixel_sha256": composition.get(
                "key_visual_pixel_sha256"),
            "final_size": composition.get("final_size"),
            "final_mode": composition.get("final_mode"),
            "final_png_sha256": composition.get("final_png_sha256"),
            "final_pixel_sha256": composition.get("final_pixel_sha256"),
            "title_layout_policy_fingerprint": composition.get(
                "title_layout_policy_fingerprint"),
        },
    }
    return validate_cover_audit(audit)
