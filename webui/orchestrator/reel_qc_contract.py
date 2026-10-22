from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping


QC_TASK_SCHEMA_VERSION = "8"
QC_RELEASE_AUTHORITY_SCHEMA = "castle-reel-qc-release-authority-v1"
QC_COST_ESTIMATE_VERSION = "2"
DRAFT_COMPLETION_SCHEMA_VERSION = "reel-draft-completion-v1"
QC_DEFAULT_VISION_MODEL = "gemini-4-argon-high"
QC_QUALITY_SCHEMA_VERSION = "reel-quality-score-v2-kpi-not-gate"
QC_QUALITY_GOAL_SCORE = 80
QC_QUALITY_DIMENSION_WEIGHTS = {
    "content_fidelity": 30,
    "audio_intelligibility": 30,
    "visual_integrity": 25,
    "pacing": 15,
}
QC_CATASTROPHIC_CODES = frozenset({
    "severe_content_mismatch",
    "mostly_unintelligible_audio",
    "gross_visual_corruption",
    "unsafe_to_publish",
})
QC_CANONICAL_ENV_DEFAULTS = {
    "VISION_MODEL": QC_DEFAULT_VISION_MODEL,
    "VIS_SEV_FAIL": "4",
}
QC_FORBIDDEN_ENV_OVERRIDES = frozenset({"REACT_DIR"})

QC_FULL_CHECK_NAMES = frozenset({
    "final.playable",
    "post_render.quality-score",
    "release.bundle-authority",
})

QC_QUALITY_POLICY = {
    "schema": 2,
    "provider": "google_subscription_cli",
    "service": "agy",
    "credential_mode": "oauth_subscription_no_api_key",
    "model": QC_DEFAULT_VISION_MODEL,
    "effort": "high",
    "attempts": 1,
    "media_scope": "actual final MP4 with video and mixed audio",
    "quality_goal_score": QC_QUALITY_GOAL_SCORE,
    "quality_goal_is_gate": False,
    "dimension_weights": dict(QC_QUALITY_DIMENSION_WEIGHTS),
    "catastrophic_confidence_minimum": 90,
    "failure_mode": (
        "provider or malformed-response failures produce a completed "
        "release-eligible WARN draft; "
        "one subscription CLI turn and no paid API or provider fallback"),
}
QC_QUALITY_AUTHORITY_FINGERPRINT = hashlib.sha256(json.dumps(
    QC_QUALITY_POLICY,
    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
).encode("utf-8")).hexdigest()
QC_QUALITY_POLICY_VERSION = (
    "canonical_reel_qc_quality_v2_single_final_mp4_agy_subscription_"
    "score80_kpi_not_gate_catastrophic90_warn_on_unavailable_20260821+"
    + QC_QUALITY_AUTHORITY_FINGERPRINT
)
QC_QUALITY_AUTHORITY = {
    "policy_version": QC_QUALITY_POLICY_VERSION,
    **QC_QUALITY_POLICY,
}
# Retain old report field names as aliases while every consumer migrates. They
# identify the same single transaction and never authorize extra calls.
QC_JUDGE_POLICY = QC_QUALITY_POLICY
QC_JUDGE_POLICY_VERSION = QC_QUALITY_POLICY_VERSION
QC_JUDGE_AUTHORITY = QC_QUALITY_AUTHORITY
QC_JUDGE_AUTHORITY_FINGERPRINT = QC_QUALITY_AUTHORITY_FINGERPRINT
QC_VISION_POLICY = QC_QUALITY_POLICY
QC_VISION_POLICY_VERSION = QC_QUALITY_POLICY_VERSION
QC_VISION_AUTHORITY = QC_QUALITY_AUTHORITY
QC_VISION_AUTHORITY_FINGERPRINT = QC_QUALITY_AUTHORITY_FINGERPRINT

QC_OPTIONAL_REF_FIELDS = (
    "audio_listener_final_receipt",
    "workspace_bundle",
    "champion_bundle",
    "runtime_asr_models_lock",
    "cover",
    "cover_kv",
    "cover_authority",
    "cover_bundle",
    "render_sisters_bundle",
    "render_host_bundle",
    "qc_rig_sources_manifest",
    "qc_rig_bundle",
)
QC_REQUIRED_REF_FIELDS = (
    "final",
    "champion",
    "reel_audio_manifest",
    "host_audio_manifest",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def canonical_draft_completion(quality_verdict: object) -> dict:
    """Project a complete render result into its independent release state."""
    verdict = str(quality_verdict or "").strip().upper()
    if verdict not in {"PASS", "WARN", "FAIL"}:
        raise ValueError("draft quality verdict must be PASS, WARN, or FAIL")
    return {
        "schema_version": DRAFT_COMPLETION_SCHEMA_VERSION,
        "execution_status": "completed",
        "quality_verdict": verdict,
        "review_required": verdict == "FAIL",
        "release_eligible": verdict != "FAIL",
    }


def canonical_quality_assessment(
    *, dimensions: object, catastrophic_defects: object,
    issues: object, summary: object, assessment_status: str = "MEASURED",
) -> dict:
    if (
        not isinstance(dimensions, Mapping)
        or set(dimensions) != set(QC_QUALITY_DIMENSION_WEIGHTS)
    ):
        raise ValueError("quality dimensions have the wrong shape")
    normalized_dimensions = {}
    for name in QC_QUALITY_DIMENSION_WEIGHTS:
        score = dimensions.get(name)
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError(
                f"quality dimension {name} must be an integer from 0 to 100")
        normalized_dimensions[name] = score
    if not isinstance(catastrophic_defects, list) or len(catastrophic_defects) > 4:
        raise ValueError("catastrophic defects must be an array of at most four rows")
    normalized_defects = []
    seen = set()
    for position, row in enumerate(catastrophic_defects):
        if not isinstance(row, Mapping) or set(row) != {
            "code", "evidence", "confidence",
        }:
            raise ValueError(f"catastrophic defect {position} has the wrong shape")
        code = row.get("code")
        evidence = row.get("evidence")
        confidence = row.get("confidence")
        if code not in QC_CATASTROPHIC_CODES or code in seen:
            raise ValueError(f"catastrophic defect {position} has an invalid code")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 500:
            raise ValueError(f"catastrophic defect {position} has invalid evidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int)
            or not 90 <= confidence <= 100
        ):
            raise ValueError(
                f"catastrophic defect {position} must be at least 90% confident")
        seen.add(code)
        normalized_defects.append({
            "code": code,
            "evidence": evidence.strip(),
            "confidence": confidence,
        })
    if not isinstance(issues, list) or len(issues) > 8:
        raise ValueError("quality issues must be an array of at most eight rows")
    normalized_issues = []
    for position, issue in enumerate(issues):
        if not isinstance(issue, str) or not issue.strip() or len(issue) > 300:
            raise ValueError(f"quality issue {position} is invalid")
        normalized_issues.append(issue.strip())
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise ValueError("quality summary is invalid")
    if assessment_status not in {"MEASURED", "UNAVAILABLE"}:
        raise ValueError("quality assessment status is invalid")
    if assessment_status == "UNAVAILABLE" and normalized_defects:
        raise ValueError(
            "unavailable quality assessment cannot claim catastrophic defects")
    weighted = sum(
        normalized_dimensions[name] * weight
        for name, weight in QC_QUALITY_DIMENSION_WEIGHTS.items()
    )
    score = (weighted + 50) // 100
    verdict = (
        "FAIL" if normalized_defects
        else "WARN" if assessment_status == "UNAVAILABLE"
        else "PASS"
    )
    return {
        "schema_version": QC_QUALITY_SCHEMA_VERSION,
        "score": score,
        "goal_score": QC_QUALITY_GOAL_SCORE,
        "goal_met": (
            assessment_status == "MEASURED"
            and score >= QC_QUALITY_GOAL_SCORE
        ),
        "assessment_status": assessment_status,
        "dimensions": normalized_dimensions,
        "catastrophic_defects": normalized_defects,
        "issues": normalized_issues,
        "summary": summary.strip(),
        "verdict": verdict,
    }


def validate_quality_assessment(value: object) -> dict:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "score", "goal_score", "goal_met",
        "assessment_status", "dimensions",
        "catastrophic_defects", "issues", "summary", "verdict",
    }:
        raise ValueError("quality assessment has the wrong shape")
    expected = canonical_quality_assessment(
        dimensions=value.get("dimensions"),
        catastrophic_defects=value.get("catastrophic_defects"),
        issues=value.get("issues"),
        summary=value.get("summary"),
        assessment_status=value.get("assessment_status"),
    )
    if dict(value) != expected:
        raise ValueError("quality assessment disagrees with the KPI policy")
    return expected


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_qc_environment_errors(environ: Mapping[str, object]) -> list[str]:
    errors = []
    for key, expected in QC_CANONICAL_ENV_DEFAULTS.items():
        if key in environ and str(environ[key]) != expected:
            errors.append(f"{key}={environ[key]!r} is noncanonical (required {expected!r})")
    for key in sorted(QC_FORBIDDEN_ENV_OVERRIDES):
        if key in environ:
            errors.append(f"{key} override is forbidden for canonical Reel QC")
    return errors


def expected_check_names(*, audio_only: bool, skip_vision: bool) -> frozenset[str]:
    return QC_FULL_CHECK_NAMES


def checks_verdict(
    checks: object,
    *,
    audio_only: bool,
    skip_vision: bool,
) -> str:
    if not isinstance(checks, list):
        raise ValueError("QC checks must be an array")
    expected = expected_check_names(
        audio_only=bool(audio_only), skip_vision=bool(skip_vision))
    names: list[str] = []
    levels: list[str] = []
    for index, row in enumerate(checks):
        if not isinstance(row, dict):
            raise ValueError(f"QC check {index} is not an object")
        name = row.get("check")
        level = row.get("level")
        detail = row.get("detail")
        if not isinstance(name, str) or not name:
            raise ValueError(f"QC check {index} has no canonical name")
        if level not in {"PASS", "WARN", "FAIL"}:
            raise ValueError(f"QC check {name} has an invalid level")
        if not isinstance(detail, str):
            raise ValueError(f"QC check {name} has invalid detail")
        names.append(name)
        levels.append(level)
    if len(names) != len(set(names)) or set(names) != set(expected):
        raise ValueError("QC check set is incomplete, duplicated, or noncanonical")
    if "FAIL" in levels:
        return "FAIL"
    if "WARN" in levels:
        return "WARN"
    return "PASS"


def canonical_qc_cost(
    *,
    audio_only: bool,
    skip_vision: bool,
    request_count: object,
) -> dict:
    """Project the actual QC vision attempts into release-safe cost evidence.

    ``qc_reel5.py`` increments its counter immediately before the sole provider
    request. Full QC performs exactly one request; development modes perform none. Keep
    this validation in the shared contract so the task runner and the DB-aware
    release/R2 verifier cannot disagree about the meaning of the count.
    """
    vision_enabled = not bool(skip_vision) and not bool(audio_only)
    if (
        isinstance(request_count, bool)
        or not isinstance(request_count, int)
        or request_count < 0
        or request_count > 1
        or (vision_enabled and request_count != 1)
        or (not vision_enabled and request_count != 0)
    ):
        raise ValueError("reel QC vision request count is noncanonical")
    return {
        "provider": "google_subscription_cli",
        "service": "agy",
        "credential_mode": "oauth_subscription_no_api_key",
        "model": QC_DEFAULT_VISION_MODEL,
        "vision_enabled": vision_enabled,
        "quality_enabled": vision_enabled,
        "request_count": request_count,
        "cost_estimate_version": QC_COST_ESTIMATE_VERSION,
        "estimated_paid_api_cost_usd": 0.0,
    }


def _ref_content_hash(value: object, field: str) -> str:
    digest = value.get("content_hash") if isinstance(value, dict) else None
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"QC {field} ref has no canonical content hash")
    return digest


def qc_evidence_input_hash(
    payload: Mapping[str, object],
    *,
    qc_reel5_sh_hash: str,
    qc_reel5_py_hash: str,
) -> str:
    for label, digest in (
        ("qc_reel5_sh_hash", qc_reel5_sh_hash),
        ("qc_reel5_py_hash", qc_reel5_py_hash),
    ):
        if SHA256_RE.fullmatch(str(digest or "")) is None:
            raise ValueError(f"{label} is invalid")
    basis: dict[str, object] = {
        "task_schema_version": QC_TASK_SCHEMA_VERSION,
        "episode": str(payload.get("episode") or ""),
        "theme": str(payload.get("theme") or ""),
        "final": _ref_content_hash(payload.get("final"), "final"),
        "champion": _ref_content_hash(payload.get("champion"), "champion"),
        "reel_audio_manifest": _ref_content_hash(
            payload.get("reel_audio_manifest"), "reel_audio_manifest"),
        "host_audio_manifest": _ref_content_hash(
            payload.get("host_audio_manifest"), "host_audio_manifest"),
        "reel_subdir": str(payload.get("reel_subdir") or ""),
        "reel_aud": str(payload.get("reel_aud") or ""),
        "host_aud": str(payload.get("host_aud") or ""),
        "skip_vision": payload.get("skip_vision") is True,
        "audio_only": payload.get("audio_only") is True,
        "qc_reel5_sh_hash": qc_reel5_sh_hash,
        "qc_reel5_py_hash": qc_reel5_py_hash,
        "source_content_hash": str(payload.get("source_content_hash") or ""),
        "implementation_hash": str(payload.get("implementation_hash") or ""),
    }
    if not basis["episode"] or not basis["theme"] or not basis["source_content_hash"]:
        raise ValueError("QC evidence input identity is incomplete")
    for field in QC_OPTIONAL_REF_FIELDS:
        if field in payload:
            basis[field] = _ref_content_hash(payload.get(field), field)
    return canonical_json_sha256(basis)


def canonical_release_authority_matches(
    value: object,
    *,
    episode: str,
    theme: str,
    reel_aud: str,
    host_aud: str,
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema", "source_schema", "sister_audio_signature",
        "host_audio_signature", "authority_files",
        "authority_files_sha256", "cover",
    }:
        return False
    if (
        value.get("schema") != QC_RELEASE_AUTHORITY_SCHEMA
        or value.get("source_schema")
        != "reel5_release_authority_evidence_v2_20260711"
    ):
        return False
    if any(SHA256_RE.fullmatch(str(value.get(field) or "")) is None for field in (
        "sister_audio_signature", "host_audio_signature",
        "authority_files_sha256",
    )):
        return False
    expected_files = {
        f"turnlist_reel5_{episode}.json",
        f"{reel_aud}/.main_synth_policy.json",
        f"{reel_aud}/spoken.json",
        f"react_beats_peer_{episode}.json",
        f"turnlist_host_{episode}.json",
        f"{host_aud}/host_audio_manifest.json",
        f"react_beats_host_{episode}.json",
        f"champion_stub_{episode}.json",
        f"champion_{episode}.json",
        "champion_lines.json",
    }
    authority_files = value.get("authority_files")
    if (
        not isinstance(authority_files, dict)
        or set(authority_files) != expected_files
        or any(SHA256_RE.fullmatch(str(item or "")) is None
               for item in authority_files.values())
        or value.get("authority_files_sha256")
        != canonical_json_sha256(authority_files)
    ):
        return False
    cover = value.get("cover")
    if not isinstance(cover, dict) or set(cover) != {
        "schema", "debate_id", "theme", "mode", "prompt_sha256",
        "authority_sha256", "metadata_sha256", "key_visual_sha256",
        "final_png_sha256", "cover_audit",
    }:
        return False
    return (
        cover.get("schema") == "reel5_cover_authority_v1_20260711"
        and cover.get("debate_id") == str(episode)
        and cover.get("theme") == theme
        and cover.get("mode") == "gpt-image-2"
        and all(SHA256_RE.fullmatch(str(cover.get(field) or "")) is not None
                for field in (
                    "prompt_sha256", "authority_sha256", "metadata_sha256",
                    "key_visual_sha256", "final_png_sha256",
                ))
        and isinstance(cover.get("cover_audit"), dict)
    )
