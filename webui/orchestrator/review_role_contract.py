from __future__ import annotations

from pathlib import PurePosixPath


# Role tags are not sufficient identity: a task can legitimately emit several
# artifacts with the same descriptive metadata.  Review and remote transport
# must agree on the file-shaped artifact that is authoritative for each role.
REVIEW_ROLE_FILE_CONTRACT = {
    "final": ("video", frozenset({".mp4"})),
    "proxy": ("video", frozenset({".mp4"})),
    "cover": ("image", frozenset({".png", ".jpg", ".jpeg", ".webp"})),
    "qc": ("json", frozenset({".json"})),
    "review_page": ("html", frozenset({".html"})),
}
HARD_REVIEW_ROLES = frozenset({"final", "proxy", "cover", "qc"})


def unique_review_artifact_role(role_names: object) -> str | None:
    """Return one recognized role, rejecting ambiguous multi-role artifacts."""
    if isinstance(role_names, str):
        values = (role_names,)
    else:
        try:
            values = tuple(role_names)  # type: ignore[arg-type]
        except TypeError:
            return None
    recognized = {
        str(role) for role in values
        if str(role) in REVIEW_ROLE_FILE_CONTRACT
    }
    if len(recognized) != 1:
        return None
    return next(iter(recognized))


def required_review_artifact_ids_are_distinct(roles: object) -> bool:
    """Require every selected hard role to refer to a different artifact."""
    if not isinstance(roles, dict):
        return False
    artifact_ids = []
    for role in HARD_REVIEW_ROLES:
        row = roles.get(role)
        if row is None:
            continue
        try:
            artifact_id = dict(row).get("id")  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if not isinstance(artifact_id, str) or not artifact_id:
            return False
        artifact_ids.append(artifact_id)
    return len(artifact_ids) == len(set(artifact_ids))


def review_artifact_matches_role(row: object, role: str) -> bool:
    """Return whether one stored artifact can authoritatively fill ``role``.

    The caller may pass a sqlite Row or a normal mapping.  Unknown roles,
    missing storage paths, path traversal, wrong artifact types, and wrong
    suffixes all fail closed.
    """
    expected = REVIEW_ROLE_FILE_CONTRACT.get(str(role or ""))
    if expected is None:
        return False
    try:
        item = dict(row)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    artifact_type = item.get("artifact_type")
    relative_path = item.get("relative_path")
    if artifact_type != expected[0] or not isinstance(relative_path, str):
        return False
    if not relative_path or "\x00" in relative_path or "\\" in relative_path:
        return False
    if any(part in {"", ".", ".."} for part in relative_path.split("/")):
        return False
    path = PurePosixPath(relative_path)
    if path.is_absolute():
        return False
    return path.suffix.lower() in expected[1]
