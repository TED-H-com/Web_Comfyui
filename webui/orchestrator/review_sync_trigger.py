from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping


RUN_ID_RE = re.compile(
    r"(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9_-]{0,78}[A-Za-z0-9])"
)
GENERATION_RE = re.compile(r"[0-9a-f]{20}")
REQUIRED_ROLES = frozenset({"final", "proxy", "cover", "qc"})
ELIGIBLE_STATUSES = frozenset({"completed", "needs_review", "completed_approved"})
TRUTHY = frozenset({"1", "true", "yes", "on"})


def review_is_ready_for_remote_sync(review_result: object) -> bool:
    """Require the exact review bundle before scheduling external transport."""
    if not isinstance(review_result, dict):
        return False
    status = str(review_result.get("run_status") or review_result.get("status") or "")
    missing = review_result.get("missing_roles")
    roles = review_result.get("required_roles")
    generation = review_result.get("transport_generation")
    return (
        status in ELIGIBLE_STATUSES
        and isinstance(missing, list)
        and not missing
        and isinstance(roles, list)
        and REQUIRED_ROLES.issubset({str(role) for role in roles})
        and isinstance(generation, str)
        and GENERATION_RE.fullmatch(generation) is not None
    )


def reel_review_sync_unit(run_id: str, review_result: object) -> str | None:
    """Return the deterministic systemd instance for one ready review."""
    run_id = str(run_id or "")
    if RUN_ID_RE.fullmatch(run_id) is None:
        return None
    if not review_is_ready_for_remote_sync(review_result):
        return None
    generation = str(review_result["transport_generation"])
    return f"web-comfyui-reel-sync@{run_id}.{generation}.service"


def trigger_reel_review_sync(
    run_id: str,
    review_result: object,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict:
    """Ask systemd to run one exact Reel sync without blocking generation.

    The hook is inert until the operator explicitly enables it.  R2 credentials
    remain isolated in ``web-comfyui-reel-sync@.service``'s EnvironmentFile; they are
    never inherited by or passed through the Castle web process.
    """
    run_id = str(run_id or "")
    if RUN_ID_RE.fullmatch(run_id) is None:
        return {"status": "invalid", "reason": "invalid_run_id"}
    unit = reel_review_sync_unit(run_id, review_result)
    if unit is None:
        return {"status": "not_ready", "reason": "review_bundle_incomplete"}
    values = os.environ if environ is None else environ
    enabled = str(values.get("CASTLE_REEL_SYNC_ON_COMPLETE", "")).strip().lower()
    if enabled not in TRUTHY:
        return {"status": "disabled", "reason": "completion_hook_disabled"}

    command = ["systemctl", "--user", "--no-block", "start", unit]
    try:
        completed = runner(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except FileNotFoundError:
        return {"status": "failed", "reason": "systemctl_unavailable", "unit": unit}
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": "systemctl_timeout", "unit": unit}
    except Exception:
        return {"status": "failed", "reason": "systemctl_error", "unit": unit}
    if int(getattr(completed, "returncode", 1)) != 0:
        return {"status": "failed", "reason": "systemctl_exit", "unit": unit}
    return {"status": "queued", "reason": "", "unit": unit}
