from __future__ import annotations

from collections.abc import Mapping, Sequence


SCHEMA_VERSION = "reel-production-kpis-v2"
RECENT_ATTEMPT_WINDOW = 10
COMPLETION_RATE_GOAL_PERCENT = 80.0
QUALITY_SCORE_GOAL = 80


def evaluate_production_kpis(
    records: Sequence[Mapping[str, object]],
    *,
    autopublish_enabled: bool = False,
) -> dict:
    """Report production KPIs without turning them into publication gates.

    Records must be chronological. A completed record has a valid final MP4;
    human approval and QC score are independent observations.
    """
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("production KPI records must be an array")
    normalized = []
    for position, row in enumerate(records):
        if not isinstance(row, Mapping):
            raise ValueError(f"production KPI record {position} is malformed")
        score = row.get("quality_score")
        if score is not None and (
            isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100
        ):
            raise ValueError(
                f"production KPI record {position} has invalid quality score")
        defects = row.get("catastrophic_defects", [])
        if not isinstance(defects, list):
            raise ValueError(
                f"production KPI record {position} has invalid defects")
        normalized.append({
            "completed": row.get("completed") is True,
            "human_decision": str(row.get("human_decision") or "pending"),
            "quality_score": score,
            "quality_verdict": str(row.get("quality_verdict") or "UNKNOWN").upper(),
            "catastrophic_defects": defects,
        })

    recent = normalized[-RECENT_ATTEMPT_WINDOW:]
    attempt_count = len(recent)
    completed_count = sum(1 for row in recent if row["completed"])
    completion_rate = (
        round(completed_count * 100.0 / attempt_count, 1)
        if attempt_count else 0.0
    )
    measured_scores = [
        row["quality_score"] for row in recent
        if isinstance(row["quality_score"], int)
    ]
    average_quality = (
        round(sum(measured_scores) / len(measured_scores), 1)
        if measured_scores else None
    )
    consecutive_approved = 0
    for row in reversed(recent):
        if (
            row["completed"]
            and row["human_decision"] == "approved"
        ):
            consecutive_approved += 1
            continue
        break

    enabled = bool(autopublish_enabled)
    return {
        "schema_version": SCHEMA_VERSION,
        "goals": {
            "recent_attempt_window": RECENT_ATTEMPT_WINDOW,
            "completion_rate_percent": COMPLETION_RATE_GOAL_PERCENT,
            "quality_score": QUALITY_SCORE_GOAL,
            "gates_publication": False,
        },
        "cohort": {
            "attempts": attempt_count,
            "completed": completed_count,
            "completion_rate_percent": completion_rate,
            "completion_goal_met": (
                completion_rate >= COMPLETION_RATE_GOAL_PERCENT
            ),
            "quality_measurements": len(measured_scores),
            "average_quality_score": average_quality,
            "quality_goal_met": (
                average_quality is not None
                and average_quality >= QUALITY_SCORE_GOAL
            ),
            "consecutive_manual_approvals": consecutive_approved,
            "catastrophic_reels": sum(
                bool(row["catastrophic_defects"]) for row in recent),
        },
        "autopublish_enabled": enabled,
        "publishing_active": enabled,
    }


# Compatibility alias for callers outside this repository. The result is KPI
# telemetry only; the operator-owned flag remains the sole publication control.
evaluate_autopublish_readiness = evaluate_production_kpis


__all__ = [
    "COMPLETION_RATE_GOAL_PERCENT",
    "QUALITY_SCORE_GOAL",
    "RECENT_ATTEMPT_WINDOW",
    "SCHEMA_VERSION",
    "evaluate_autopublish_readiness",
    "evaluate_production_kpis",
]
