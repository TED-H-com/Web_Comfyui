#!/usr/bin/env python3
"""Run one hardened Reel stage without the legacy pipeline driver.

This module is deliberately a narrow CLI boundary.  It invokes only the
reviewed backends listed in ``STAGE_BACKENDS``, always invokes the selected
stage (stage reuse belongs to Castle), and emits a content-hashed result
manifest containing paths relative to the supplied source root.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


RUNNER_VERSION = (
    "reel-native-stage-v43_frozen_local_authority_20260720"
)
RESULT_SCHEMA_VERSION = "castle-reel-native-stage-result-v1"
ALLOWED_STAGES = (
    "pull",
    "forum_meta",
    "script",
    "script_approved",
    "wardrobe_gate",
    "scene",
    "host_rig",
    "synth",
    "beats",
    "peer_synth",
    "host_reaction_bank",
    "host_text",
    "host_synth",
    "champion_synth",
    "audio_listener_clips",
    "audio_listener_final",
)
# Native runner changes are intentionally stage-scoped.  Castle hashes the
# selected contract version plus that stage's declared backend/dependency
# closure; unrelated edits in this dispatcher must not invalidate every Reel
# stage.  Every allowlisted stage must have an explicit entry.
STAGE_RUNNER_CONTRACT_VERSIONS: dict[str, str] = {
    "pull": "pull-v1",
    "forum_meta": "forum-meta-v1",
    "script": "script-v42-one-shot-review-voice-authority",
    "script_approved": "script-approved-v2-local-source-meta-authority",
    "wardrobe_gate": "wardrobe-gate-v2",
    "scene": "scene-v2-pinned-runtime-asset",
    "host_rig": "host-rig-v1",
    "synth": "synth-v2-authority-cache",
    "beats": "beats-v1",
    "peer_synth": "peer-synth-v2-authority-cache",
    "host_reaction_bank": "host-reaction-bank-v2-authority-cache",
    "host_text": "host-text-v2",
    "host_synth": "host-synth-v2-authority-cache",
    "champion_synth": "champion-synth-v1-immutable-authority",
    "audio_listener_clips": (
        "audio-listener-clips-v3-audio-ready-nonreproducible-downgrade"
    ),
    "audio_listener_final": "audio-listener-final-v1",
}
if set(STAGE_RUNNER_CONTRACT_VERSIONS) != set(ALLOWED_STAGES):
    raise RuntimeError("native Reel stage runner contracts differ from allowlist")

# These producers own verified immutable/content-addressed authority.  A
# successful backend invocation followed by a fail-closed collector may
# legitimately return byte-identical files on a healthy cache/approval hit.
AUTHORITY_VERIFIED_IDENTICAL_OUTPUT_STAGES = frozenset({
    "script_approved",
    "synth",
    "peer_synth",
    "host_reaction_bank",
    "host_synth",
    "champion_synth",
    "audio_listener_clips",
})
THEMES = {
    "tech", "finance", "politics", "education", "health", "environment",
    "law", "relationship", "family", "workplace", "science", "culture",
    "sports", "food", "travel", "psychology", "philosophy",
    "international", "media", "festival",
}
THEME_STAGES = {"wardrobe_gate", "scene", "host_rig"}
SAFE_PERSONA_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
SAFE_OUTPUT_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,120}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
LISTENER_PASS = "PASS"
LISTENER_WARN = "WARN"
LISTENER_FAIL = "FAIL"
DRAFT_COMPLETION_SCHEMA_VERSION = "reel-draft-completion-v1"
NON_REPRODUCIBLE_FAIL_PROBLEM = "non_reproducible_fail"
NON_REPRODUCIBLE_FAIL_FLAG = "non_reproducible_fail_downgraded"
SCRIPT_SELFCHECK_VERSION = (
    "script_selfcheck_v21_top_level_fullstop_only_tts_units_target24_max32_"
    "codex_cli_chatgpt_low_one_shot_600s_structured_json_schema_"
    "complete_script_one_request_exact_tts_spoken_units_"
    "parent_context_modality_natural_collocation_audible_female_referent_"
    "result_patient_alignment_inanimate_relationship_entry_"
    "deterministic_precheck_no_repair_no_regeneration_total_attestation_"
    "predicate_coinage_opaque_face_drama_relation_domain_cross_persona_claim_frames_two_pass_"
    "source_turns_unit_map_hash_receipt_"
    "exact_draft_fallback_20260720"
)
SCRIPT_WRITER_PROVIDER = "codex_cli_subscription"
SCRIPT_WRITER_MODEL = "gpt-5.5"
SCRIPT_JUDGE_PROVIDER = "codex_cli"
SCRIPT_JUDGE_MODEL = "codex-default-chatgpt"
SCRIPT_JUDGE_POLICY_FINGERPRINT = (
    "5d488b3c1d05ef8ca49361dc40f893751d1c1101f9475f791613d3c3d8f86b20"
)
SCRIPT_JUDGE_POLICY_VERSION = (
    "script_independent_gate_v12_codex_subscription_writer_and_judge_"
    "exact_login_status_across_stdout_stderr_"
    "foreground_600s_process_group_exact_structured_json_schema_complete_script_"
    "tts_spoken_units_parent_context_modality_natural_collocation_"
    "result_target_alignment_inanimate_relationship_entry_"
    "channel_domain_opaque_face_drama_receipt_no_api_fallback_20260724+"
    + SCRIPT_JUDGE_POLICY_FINGERPRINT
)
SCRIPT_SPOKEN_UNIT_POLICY_FINGERPRINT = (
    "7eaa8898f68e8ef9850344f44da0923f1da66e089f4142f46fd1725e7f6c7553"
)
SCRIPT_SPOKEN_PROJECTION_POLICY_FINGERPRINT = (
    "6e27458020fb85c2acf4dead3cacdfdfe27f85fbbdb5d4c96388b0f6a9b24c0e"
)
SCRIPT_JUDGE_BOOLEAN_FIELDS = (
    "source_supported",
    "predicate_arguments_complete",
    "standalone_concrete",
    "stance_modality_ok",
    "relation_direction_ok",
    "identity_ok",
    "first_hearing_natural",
)
SCRIPT_JUDGE_ROW_KEYS = {"unit_id", *SCRIPT_JUDGE_BOOLEAN_FIELDS, "why"}
SCRIPT_JUDGE_INPUT_UNIT_KEYS = {"unit_id", "unit_idx", "text"}
SCRIPT_JUDGE_UNIT_MAP_ROW_KEYS = {
    "unit_id", "turn_idx", "unit_idx", "text", "text_sha256",
}
SCRIPT_UNIT_ID_RE = re.compile(
    r"turn:(0|[1-9][0-9]*)/unit:(0|[1-9][0-9]*)"
)
# Longest first so a plural referent produces one precise diagnostic instead
# of being misreported as the single-character pronoun at its start.
WRITTEN_ONLY_FEMALE_PRONOUNS = ("她們", "她们", "她")

# These controls shape generated speech but are intentionally not stage inputs.
# A Castle worker must never inherit an operator shell's experimental values;
# the backend's canonical policy owns the defaults and receipts.
OUTPUT_SHAPING_ENV_CONTROLS = (
    "PROVIDER", "MODEL", "SCRIPT_JUDGE_PROVIDER", "SCRIPT_JUDGE_MODEL",
    "SCRIPT_JUDGE_MODE",
    "REPHRASE_MODEL", "REPHRASE", "MAX_TRIES", "TURN_TRIES",
    "SEED_OFFSET", "QC_MODEL", "VERIFY_MODEL", "CHAMP_TRIES",
    "PEER_GUARD_TRIES", "QC", "FAST", "FROZEN_EXACT_TEXT",
)
PULL_TRANSPORT_ENV_CONTROLS = (
    "REEL_PULL_SSH_ATTEMPTS",
    "REEL_PULL_SSH_CONNECT_TIMEOUT_SECONDS",
    "REEL_PULL_SSH_HARD_TIMEOUT_SECONDS",
    "REEL_PULL_SSH_RETRY_DELAY_SECONDS",
)


# This registry is both an execution allowlist and an auditable declaration of
# every external backend the runner may enter. ``forum_meta`` is derived from
# the just-pulled atomic debate snapshot, so it deliberately has no second
# network route.
STAGE_BACKENDS: dict[str, tuple[str, ...]] = {
    "pull": ("pull_debate.sh",),
    "forum_meta": ("derived:debate_raw_post_metadata",),
    "script": ("build_reel5.py", "script_receipt_authority.py"),
    "script_approved": ("script_receipt_authority.py",),
    "wardrobe_gate": ("check_wardrobe_approval.py", "wardrobe_integrity.py"),
    "scene": ("derived:pinned_runtime_scene_asset",),
    "host_rig": ("host_doll_to_parts.py",),
    "synth": ("gen_audio_v2.py", "main_audio_policy.py"),
    "beats": ("author_peer_beats.py",),
    "peer_synth": ("synth_peer_beats.py", "peer_audio_authority.py"),
    "host_reaction_bank": ("gen_host_reactions.py",),
    "host_text": (
        "champion_stub_authority.py",
        "build_host5.py",
        "build_host_beats.py",
    ),
    "host_synth": ("host/clone_host.py", "host_audio_authority.py"),
    "champion_synth": ("gen_champion.py", "champion_authority.py"),
    "audio_listener_clips": (
        "audio_listener_gate.py",
        "audio_ready_authority.py",
    ),
    "audio_listener_final": ("audio_listener_gate.py",),
}

# Shared helpers and policy modules that can change a stage's accepted output
# without being a stable direct import of its executable backend.  Keep these
# separate from STAGE_BACKENDS: they are hash dependencies, never extra commands.
# The hash layer still walks ordinary local imports recursively; these explicit
# seeds make reuse fail closed when a dependency is dynamic, conditional, or
# enforced indirectly through a manifest/receipt policy.
STAGE_IMPLEMENTATION_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "script": (
        "script_independent_gate.py",
        "spoken_units.py",
        "taiwan_terms.py",
        "canonical_workflow_policy.py",
    ),
    "script_approved": (
        "spoken_units.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
    ),
    "synth": (
        "main_audio_draft.py",
        "main_audio_cache_integration.py",
        "verified_main_audio_cache.py",
        "listener_rejection_ledger.py",
        "main_audio_rules.py",
        "rejected_audio_review.py",
        "spoken_units.py",
        "tts_rephrase_portfolio.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
    ),
    "peer_synth": (
        "listener_rejection_ledger.py",
        "bv_guard.py",
        "aux_asr_insertion_consensus.py",
        "tts_rephrase_portfolio.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
        "spoken_reel_terms.py",
    ),
    "host_reaction_bank": (
        "bv_guard.py",
        "aux_asr_insertion_consensus.py",
        "tts_rephrase_portfolio.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
        "durable_authority.py",
    ),
    "host_text": (
        "listener_rejection_ledger.py",
        "host_beats_authority.py",
        "gen_host_reactions.py",
        "bv_guard.py",
        "aux_asr_insertion_consensus.py",
        "tts_rephrase_portfolio.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
        "durable_authority.py",
        "title_policy.py",
    ),
    # clone_host imports the parent-directory guard only inside its canonical
    # host factory.  Seed the complete guard policy boundary explicitly so a
    # dynamic-import refactor can never leave reusable host audio on an old
    # auxiliary-ASR contract.
    "host_synth": (
        "bv_guard.py",
        "aux_asr_insertion_consensus.py",
        "tts_rephrase_portfolio.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
    ),
    "champion_synth": (
        "champion_stub_authority.py",
        "champion_lines.json",
        "durable_authority.py",
        "bv_guard.py",
        "aux_asr_insertion_consensus.py",
        "tts_rephrase_portfolio.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
    ),
    "audio_listener_clips": (
        "main_audio_policy.py",
        "main_audio_rules.py",
        "listener_rejection_ledger.py",
        "peer_audio_authority.py",
        "host_audio_authority.py",
        "host_beats_authority.py",
        "champion_authority.py",
        "champion_stub_authority.py",
        "durable_authority.py",
        "canonical_workflow_policy.py",
        "reel_draft_completion.py",
    ),
    "audio_listener_final": (
        "render_host.sh",
        "assemble_reel5.sh",
        "reel_draft_completion.py",
    ),
}

# M4 tasks that do not execute through this native runner still need the same
# explicit reuse boundary.  tasks.py consumes this declaration when hashing the
# external QC implementation.
EXTERNAL_IMPLEMENTATION_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    # gen_cover5 imports this today, so the local-import closure also finds it.
    # Keep the title policy explicit because it is part of the cover acceptance
    # boundary even if the worker later switches to a dynamic import or an
    # allowlisted external worker override.
    "cover": (
        "cover_authority.py",
        "cover_title_layout.py",
    ),
    # render_host is a pure consumer of pre-render champion/audio-ready
    # authority.  TTS, model and listener policy belongs to their producer
    # stages and must not invalidate visual rendering independently.
    "render_host": (),
    "qc": (
        "main_audio_policy.py",
        "main_audio_rules.py",
        "tts_rephrase_portfolio.py",
        "bv_guard.py",
        "aux_asr_insertion_consensus.py",
        "gen_host_reactions.py",
        "host_beats_authority.py",
        "peer_audio_authority.py",
        "champion_authority.py",
        "author_peer_beats.py",
        "slow_asr.py",
        "brand_semantics.py",
        "numeric_semantics.py",
        "canonical_workflow_policy.py",
        "taiwan_terms.py",
        "durable_authority.py",
        "title_policy.py",
        "reel_draft_completion.py",
    ),
}


class NativeStageError(RuntimeError):
    """A fail-closed validation or backend error."""


@dataclass(frozen=True)
class StageContext:
    source_root: Path
    episode: str
    theme: str | None
    result_manifest: Path
    python: Path
    main_audio_resynth_idxs: tuple[int, ...] = ()
    main_audio_cache_revision: str = ""


@dataclass(frozen=True)
class BackendCommand:
    argv: tuple[str, ...]
    env_overlay: tuple[tuple[str, str], ...] = ()
    capture_stdout: bool = False


@dataclass(frozen=True)
class NamedOutput:
    name: str
    path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise NativeStageError("result manifest may not be a symlink")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _regular_file(path: Path, label: str, *, root: Path | None = None) -> Path:
    try:
        info = path.lstat()
    except OSError as error:
        raise NativeStageError(f"{label} is missing") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise NativeStageError(f"{label} must be a regular non-symlink file")
    resolved = path.resolve()
    if root is not None:
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise NativeStageError(f"{label} escapes the source root") from error
    return resolved


def _source_root(value: str) -> Path:
    raw = Path(value).expanduser().absolute()
    try:
        info = raw.lstat()
    except OSError as error:
        raise NativeStageError("source root is missing") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise NativeStageError("source root must be a real directory, not a symlink")
    return raw.resolve()


def _python_path() -> Path:
    configured = os.environ.get(
        "CASTLE_REEL_NATIVE_PYTHON",
        str(Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python"),
    )
    path = Path(configured).expanduser().absolute()
    if not path.exists() or not path.is_file() or not os.access(path, os.X_OK):
        raise NativeStageError("configured Reel Python interpreter is unavailable")
    return path


def _backend(ctx: StageContext, relative: str) -> Path:
    if relative.startswith("ssh:"):
        raise NativeStageError("remote route is not a source-root backend")
    candidate = ctx.source_root / relative
    return _regular_file(candidate, f"backend {relative}", root=ctx.source_root)


def _audio_ready_authority_schema_version(ctx: StageContext) -> str:
    path = _backend(ctx, "audio_ready_authority.py")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise NativeStageError(
            "audio-ready authority schema version is unavailable"
        ) from error
    values = []
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION"
            for target in node.targets
        ):
            value_node = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "SCHEMA_VERSION"
            and node.value is not None
        ):
            value_node = node.value
        if value_node is not None:
            values.append(value_node)
    if len(values) != 1:
        raise NativeStageError("audio-ready authority schema version is unavailable")
    try:
        version = ast.literal_eval(values[0])
    except (ValueError, TypeError) as error:
        raise NativeStageError(
            "audio-ready authority schema version is unavailable"
        ) from error
    if not isinstance(version, str) or not version:
        raise NativeStageError("audio-ready authority schema version is unavailable")
    return version


def _input(ctx: StageContext, relative: str, label: str) -> Path:
    return _regular_file(ctx.source_root / relative, label, root=ctx.source_root)


def _relative_member(base: Path, value: object, label: str) -> Path:
    text = str(value or "").strip()
    relative = Path(text)
    if not text or relative.is_absolute() or ".." in relative.parts:
        raise NativeStageError(f"{label} must be a safe relative path")
    return base / relative


def _clean_child_env(overlay: Iterable[tuple[str, str]]) -> dict[str, str]:
    env = dict(os.environ)
    # Caller-provided stage controls may not change the declared route.
    for key in (
        "AUDIO_DIR", "AUDIO_LISTENER_BLOCKING", "CHAMPION", "CHAMPION_STUB",
        "CHAMPION_AUDIO_DIR", "CHAMPION_DIR", "CHAMPION_MANIFEST",
        "CHAMP_SEED", "DEBATE_ID", "FINAL_MP4", "FORCE", "FORCE_HOST_REACTIONS",
        "FORCE_PEER_SYNTH", "HOST_AUDIO_MANIFEST", "HOST_GAP_MS",
        "HOST_REACTION_CAST", "PEER_BEATS_OUT",
        "REACT_BEATS_PEER", "REACT_BEATS_REEL", "REACT_DIR", "REEL_TL",
        "RESYNTH_IDXS", "SCENE_FORCE", "TURNLIST", "TURNLIST_HOST",
        "TURNLIST_REEL", "WARDROBE_APPROVAL_GATE",
    ):
        env.pop(key, None)
    for key in OUTPUT_SHAPING_ENV_CONTROLS:
        env.pop(key, None)
    # Pull owns a reviewed, bounded transport policy. An operator shell may not
    # silently turn one Castle task into minutes of retries or disable them.
    for key in PULL_TRANSPORT_ENV_CONTROLS:
        env.pop(key, None)
    env.update(dict(overlay))
    return env


_BACKEND_FAILURE_DETAIL_CHARS = 600


def _backend_failure_detail(error: subprocess.CalledProcessError) -> str:
    """Return the tail of what a captured backend said before it exited."""
    for stream, label in ((error.stderr, "stderr"), (error.stdout, "stdout")):
        if not isinstance(stream, str):
            continue
        text = stream.strip()
        if not text:
            continue
        if len(text) > _BACKEND_FAILURE_DETAIL_CHARS:
            text = "..." + text[-_BACKEND_FAILURE_DETAIL_CHARS:]
        return f": {label}: {text}"
    return ""


def _run_command(ctx: StageContext, command: BackendCommand) -> str:
    if not command.argv:
        raise NativeStageError("empty backend command")
    try:
        completed = subprocess.run(
            list(command.argv),
            cwd=str(ctx.source_root),
            env=_clean_child_env(command.env_overlay),
            check=True,
            text=True,
            stdout=subprocess.PIPE if command.capture_stdout else None,
            stderr=subprocess.PIPE if command.capture_stdout else None,
        )
    except subprocess.CalledProcessError as error:
        # A captured backend writes nothing to the task log, so its own account
        # of the failure lives only on the exception. Dropping it leaves an
        # operator with "exited 1" and no way to tell a stale receipt from a
        # missing asset, which is how a single wall gets hit one episode at a
        # time. Carry the tail of what the backend actually said.
        detail = _backend_failure_detail(error)
        wrapped = NativeStageError(
            f"{ctx.episode}/{ctx.theme or 'no-theme'} backend exited "
            f"{error.returncode}{detail}"
        )
        wrapped.returncode = error.returncode
        raise wrapped from error
    except OSError as error:
        raise NativeStageError("could not start the declared stage backend") from error
    return completed.stdout or ""


def _champion_stub_code() -> str:
    return (
        "import sys; from champion_stub_authority import write_champion_stub; "
        "write_champion_stub(output_path=sys.argv[1], debate_id=sys.argv[2], "
        "reel_turnlist_path=sys.argv[3], react_beats_peer_path=sys.argv[4], "
        "project_root=sys.argv[5])"
    )


def _peer_verify_code() -> str:
    return (
        "import json,sys; from synth_peer_beats import "
        "verified_peer_audio,verified_peer_header; "
        "d=json.load(open(sys.argv[1],encoding='utf-8')); "
        "verified_peer_header(d); "
        "[(verified_peer_audio(r,did=sys.argv[2],idx=int(k),"
        "reactor=r.get('reactor'),here=sys.argv[3])) "
        "for k in sorted((x for x in d if str(x).isdigit()),key=int) "
        "for r in d[k].get('reactors',[]) if r.get('show') is True]"
    )


def backend_commands(ctx: StageContext, stage: str) -> tuple[BackendCommand, ...]:
    """Return the exact, shell-free backend route for one allowlisted stage."""
    if stage not in ALLOWED_STAGES:
        raise NativeStageError("stage is not allowlisted")
    episode = ctx.episode
    theme = ctx.theme or ""
    py = str(ctx.python)
    turnlist = f"turnlist_reel5_{episode}.json"
    peer_beats = f"react_beats_peer_{episode}.json"
    host_turnlist = f"turnlist_host_{episode}.json"
    host_audio = f"audio_host_{episode}"

    if stage == "pull":
        return (BackendCommand((str(_backend(ctx, "pull_debate.sh")), episode)),)
    if stage == "forum_meta":
        return ()
    if stage == "script":
        return (
            BackendCommand(
                (py, str(_backend(ctx, "build_reel5.py")), episode),
                (
                    ("MAX_LINE_CHARS", "70"),
                    ("SCRIPT_JUDGE_MODE", "deferred"),
                ),
            ),
            BackendCommand(
                (
                    py,
                    str(_backend(ctx, "script_receipt_authority.py")),
                    "verify",
                    turnlist,
                ),
                capture_stdout=True,
            ),
        )
    if stage == "script_approved":
        return (BackendCommand(
            (
                py,
                str(_backend(ctx, "script_receipt_authority.py")),
                "verify",
                turnlist,
            ),
            capture_stdout=True,
        ),)
    if stage == "wardrobe_gate":
        return (BackendCommand(
            (
                py, str(_backend(ctx, "check_wardrobe_approval.py")), episode,
                "--theme", theme, "--turnlist", turnlist,
            ),
            (("WARDROBE_APPROVAL_GATE", "1"),),
        ),)
    if stage == "scene":
        # Episode runs may only adopt the scene bytes already verified by
        # Castle's scene_assets runtime lock. Art generation is an explicit
        # maintenance workflow and is never reachable from this DAG.
        return ()
    if stage == "host_rig":
        return (BackendCommand(
            (py, str(_backend(ctx, "host_doll_to_parts.py")), theme),
        ),)
    if stage == "synth":
        audio_dir = f"audio_reel5_{episode}"
        turnlist_data = _turnlist(ctx)
        synth_env = [
            ("TURNLIST", turnlist),
            ("AUDIO_DIR", audio_dir),
            (
                "RESYNTH_IDXS",
                ",".join(str(index) for index in ctx.main_audio_resynth_idxs),
            ),
        ]
        if ctx.main_audio_resynth_idxs:
            synth_env.append((
                "MAIN_AUDIO_CACHE_REVISION",
                ctx.main_audio_cache_revision,
            ))
        if _frozen_editorial_receipt(ctx, turnlist_data) is not None:
            synth_env.append(("FROZEN_EXACT_TEXT", "1"))
        return (
            BackendCommand(
                (py, str(_backend(ctx, "gen_audio_v2.py")), episode),
                tuple(synth_env),
            ),
            BackendCommand((
                py, str(_backend(ctx, "main_audio_policy.py")), "check",
                audio_dir, turnlist,
            )),
        )
    if stage == "beats":
        return (BackendCommand(
            (py, str(_backend(ctx, "author_peer_beats.py")), episode),
            (
                ("PEER_MAX_LINE_CHARS", "30"),
                ("TURNLIST_REEL", turnlist),
                ("PEER_BEATS_OUT", peer_beats),
            ),
        ),)
    if stage == "peer_synth":
        return (
            BackendCommand(
                (py, str(_backend(ctx, "synth_peer_beats.py")), episode),
                (("PEER_BEATS_OUT", peer_beats),),
            ),
            BackendCommand((
                py, "-c", _peer_verify_code(),
                str(ctx.source_root / peer_beats), episode, str(ctx.source_root),
            )),
        )
    if stage == "host_reaction_bank":
        cast = ",".join(_turnlist_cast(ctx))
        return (BackendCommand(
            (py, str(_backend(ctx, "gen_host_reactions.py"))),
            (("HOST_REACTION_CAST", cast),),
        ),)
    if stage == "host_text":
        stub = f"champion_stub_{episode}.json"
        return (
            BackendCommand((
                py, "-c", _champion_stub_code(),
                str(ctx.source_root / stub), episode,
                str(ctx.source_root / turnlist), str(ctx.source_root / peer_beats),
                str(ctx.source_root),
            )),
            BackendCommand(
                (py, str(_backend(ctx, "build_host5.py")), episode),
                (
                    ("CHAMPION_STUB", stub),
                    ("REEL_TL", turnlist),
                    ("REACT_BEATS_PEER", peer_beats),
                ),
            ),
            BackendCommand(
                (py, str(_backend(ctx, "build_host_beats.py")), episode),
                (("CHAMPION_STUB", stub), ("REACT_BEATS_PEER", peer_beats)),
            ),
        )
    if stage == "host_synth":
        manifest = f"{host_audio}/host_audio_manifest.json"
        return (
            BackendCommand(
                (py, str(_backend(ctx, "host/clone_host.py"))),
                (
                    ("DEBATE_ID", episode),
                    ("HOST_GAP_MS", "160"),
                    ("TURNLIST_HOST", host_turnlist),
                    ("AUDIO_DIR", host_audio),
                    ("HOST_AUDIO_MANIFEST", manifest),
                ),
            ),
            BackendCommand((
                py, str(_backend(ctx, "host_audio_authority.py")), "check",
                "--manifest", manifest, "--turnlist", host_turnlist,
                "--audio-dir", host_audio, "--root", str(ctx.source_root),
                "--debate-id", episode,
            )),
        )
    if stage == "champion_synth":
        champion_audio = f"audio_champion_{episode}"
        champion_manifest = f"champion_{episode}.json"
        authority_args = (
            "--manifest", champion_manifest,
            "--debate-id", episode,
            "--host-turnlist", host_turnlist,
            "--reel-turnlist", turnlist,
            "--react-beats", peer_beats,
            "--audio-dir", champion_audio,
            "--champion-lines", "champion_lines.json",
            "--root", str(ctx.source_root),
            "--seed", episode,
        )
        return (
            BackendCommand(
                (py, str(_backend(ctx, "gen_champion.py"))),
                (
                    ("DEBATE_ID", episode),
                    ("TURNLIST_HOST", host_turnlist),
                    ("TURNLIST_REEL", turnlist),
                    ("REACT_BEATS_REEL", peer_beats),
                    ("CHAMPION_DIR", champion_audio),
                    ("CHAMPION_AUDIO_DIR", champion_audio),
                    ("CHAMPION_MANIFEST", champion_manifest),
                    ("CHAMP_SEED", episode),
                ),
            ),
            BackendCommand((
                py, str(_backend(ctx, "champion_authority.py")), "check",
                *authority_args,
            )),
        )
    if stage == "audio_listener_clips":
        report = f"audio_listener_clips_{episode}.json"
        audio_ready = f"audio_ready_{episode}.json"
        return (
            BackendCommand(
                (
                    py, str(_backend(ctx, "audio_listener_gate.py")), episode,
                    "--stage", "clips", "--report", report,
                ),
                (("AUDIO_LISTENER_BLOCKING", "1"),),
            ),
            BackendCommand((
                py, str(_backend(ctx, "audio_ready_authority.py")),
                "write", episode, "--authority", audio_ready,
                "--root", str(ctx.source_root),
            )),
            BackendCommand((
                py, str(_backend(ctx, "audio_ready_authority.py")),
                "check", episode, "--authority", audio_ready,
                "--root", str(ctx.source_root),
            )),
        )
    if stage == "audio_listener_final":
        report = f"audio_listener_final_{episode}.json"
        final = str(ctx.source_root / f"reel{episode}" / f"reel5_{episode}_final.mp4")
        return (BackendCommand(
            (
                py, str(_backend(ctx, "audio_listener_gate.py")), episode,
                "--stage", "final", "--report", report,
            ),
            (("FINAL_MP4", final),),
        ),)
    raise NativeStageError("stage has no native route")


def _load_json(path: Path, label: str) -> object:
    _regular_file(path, label)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise NativeStageError(f"{label} is not valid JSON") from error


def _turnlist(ctx: StageContext) -> dict:
    path = ctx.source_root / f"turnlist_reel5_{ctx.episode}.json"
    data = _load_json(path, "reel turnlist")
    if not isinstance(data, dict) or not isinstance(data.get("turns"), list) or not data["turns"]:
        raise NativeStageError("reel turnlist must contain turns")
    return data


def _frozen_editorial_receipt(ctx: StageContext, turnlist: dict) -> dict | None:
    """Bind exact-text synth mode to the verified upstream stage receipt."""
    receipt_path = (
        ctx.source_root / ".castle-native" / ctx.episode /
        "script_approved_verification.json"
    )
    declares_editorial = isinstance(
        turnlist.get("_editorial_approval"), dict)
    if not receipt_path.is_file() or receipt_path.is_symlink():
        if declares_editorial:
            raise NativeStageError(
                "frozen editorial turnlist has no verified stage receipt")
        return None
    receipt = _load_json(receipt_path, "frozen editorial stage receipt")
    valid = (
        isinstance(receipt, dict)
        and receipt.get("ok") is True
        and receipt.get("authority_type") == "editorial_approval"
        and receipt.get("synthesis_mode") == "frozen_exact_text"
        and receipt.get("exact_text") is True
        and receipt.get("turnlist_sha256") == _sha256(
            ctx.source_root / f"turnlist_reel5_{ctx.episode}.json")
        and receipt.get("raw_sha256") == _sha256(
            ctx.source_root / f"debate_{ctx.episode}_raw.json")
        and receipt.get("turn_count") == len(turnlist.get("turns") or [])
    )
    if declares_editorial and not valid:
        raise NativeStageError(
            "frozen editorial stage receipt does not bind current bytes")
    return receipt if declares_editorial and valid else None


def _ordered_turns(ctx: StageContext) -> list[dict]:
    rows = _turnlist(ctx)["turns"]
    clean = []
    for position, turn in enumerate(rows):
        if not isinstance(turn, dict) or not isinstance(turn.get("idx"), int):
            raise NativeStageError(f"turnlist row {position} has invalid idx")
        if turn["idx"] < 0:
            raise NativeStageError(f"turnlist row {position} has negative idx")
        clean.append(turn)
    return sorted(clean, key=lambda row: row["idx"])


def _turnlist_cast(ctx: StageContext) -> list[str]:
    cast: list[str] = []
    for position, turn in enumerate(_ordered_turns(ctx)):
        if str(turn.get("kind") or "line") != "line":
            continue
        speaker = str(turn.get("speaker") or "")
        if not SAFE_PERSONA_RE.fullmatch(speaker):
            raise NativeStageError(f"turnlist row {position} has unsafe speaker")
        if speaker not in cast:
            cast.append(speaker)
    if not cast:
        raise NativeStageError("turnlist contains no sister cast")
    return cast


def _preflight(ctx: StageContext, stage: str) -> None:
    if stage in THEME_STAGES and not ctx.theme:
        raise NativeStageError(f"{stage} requires --theme")
    episode = ctx.episode
    required: dict[str, tuple[str, ...]] = {
        "forum_meta": (f"debate_{episode}_raw.json",),
        "script": (f"debate_{episode}_raw.json", f"forum_meta_{episode}.json"),
        "script_approved": (
            f"debate_{episode}_raw.json",
            f"turnlist_reel5_{episode}.json",
        ),
        "wardrobe_gate": (f"turnlist_reel5_{episode}.json",),
        "synth": (f"turnlist_reel5_{episode}.json",),
        "beats": (f"debate_{episode}_raw.json", f"turnlist_reel5_{episode}.json"),
        "peer_synth": (f"react_beats_peer_{episode}.json",),
        "host_reaction_bank": (f"turnlist_reel5_{episode}.json",),
        "host_text": (
            f"turnlist_reel5_{episode}.json",
            f"react_beats_peer_{episode}.json",
            "audio_react/host_reactions_manifest.json",
        ),
        "host_synth": (f"turnlist_host_{episode}.json",),
        "champion_synth": (
            f"turnlist_reel5_{episode}.json",
            f"turnlist_host_{episode}.json",
            f"react_beats_peer_{episode}.json",
            "champion_lines.json",
        ),
        "audio_listener_clips": (
            f"turnlist_reel5_{episode}.json",
            f"react_beats_peer_{episode}.json",
            f"turnlist_host_{episode}.json",
            f"react_beats_host_{episode}.json",
            f"audio_reel5_{episode}/spoken.json",
            f"audio_reel5_{episode}/timing_{episode}.json",
            f"audio_reel5_{episode}/.main_synth_policy.json",
            f"audio_host_{episode}/host_audio_manifest.json",
            "audio_react/host_reactions_manifest.json",
            f"champion_{episode}.json",
            "champion_lines.json",
        ),
        "audio_listener_final": (f"reel{episode}/reel5_{episode}_final.mp4",),
    }
    for relative in required.get(stage, ()):
        _input(ctx, relative, f"{stage} input {Path(relative).name}")


def _write_forum_meta(ctx: StageContext) -> None:
    rows = _load_json(
        ctx.source_root / f"debate_{ctx.episode}_raw.json",
        "raw debate metadata",
    )
    if not isinstance(rows, list) or not rows or any(
            not isinstance(row, dict) for row in rows):
        raise NativeStageError("raw debate metadata must be a non-empty row array")
    expected_post_id = int(ctx.episode)
    post_ids: set[int] = set()
    titles: set[str] = set()
    categories: set[str] = set()
    updated_values: set[int] = set()
    for row in rows:
        post_id = row.get("post_id")
        if type(post_id) is not int or post_id <= 0:
            raise NativeStageError("raw debate post id is invalid")
        if post_id != expected_post_id:
            raise NativeStageError("raw debate post id does not match episode")
        title = row.get("topic")
        if not isinstance(title, str) or not title.strip():
            raise NativeStageError("raw debate post title is invalid")
        category = row.get("category")
        if not isinstance(category, str) or category not in THEMES:
            raise NativeStageError("raw debate post category is not canonical")
        updated_at = row.get("post_updated_at")
        if type(updated_at) is not int or updated_at <= 0:
            raise NativeStageError("raw debate post revision is invalid")
        post_ids.add(post_id)
        titles.add(title)
        categories.add(category)
        updated_values.add(updated_at)
    if post_ids != {expected_post_id}:
        raise NativeStageError("raw debate post id does not match episode")
    if len(titles) != 1 or len(categories) != 1:
        raise NativeStageError("raw debate post metadata is inconsistent")
    category = next(iter(categories))
    if len(updated_values) != 1:
        raise NativeStageError("raw debate post revision is inconsistent")
    updated_at = next(iter(updated_values))
    clean = {
        "id": expected_post_id,
        "title": next(iter(titles)),
        "category": category,
        "updated_at": updated_at,
    }
    _atomic_json(ctx.source_root / f"forum_meta_{ctx.episode}.json", clean)


def _write_wardrobe_receipt(ctx: StageContext) -> Path:
    turnlist = _input(
        ctx, f"turnlist_reel5_{ctx.episode}.json", "wardrobe turnlist"
    )
    approval_path = _input(ctx, "wardrobe_approval.json", "wardrobe approval")
    approval = _load_json(approval_path, "wardrobe approval")
    items = approval.get("items") if isinstance(approval, dict) else None
    if not isinstance(items, dict):
        raise NativeStageError("wardrobe approval items are missing")
    selected = []
    for persona in _turnlist_cast(ctx):
        key = f"{persona}__{ctx.theme}"
        row = items.get(key)
        if not isinstance(row, dict) or row.get("status") != "approved":
            raise NativeStageError(f"wardrobe approval changed after gate: {key}")
        doll = _input(
            ctx,
            f"tachie/outfits_v2/doll_{persona}__{ctx.theme}.png",
            f"wardrobe doll {key}",
        )
        normalized = _input(
            ctx,
            f"tachie/outfits_v2_norm/doll_{persona}__{ctx.theme}.png",
            f"normalized wardrobe doll {key}",
        )
        parts_dir = (
            ctx.source_root / "tachie" / "parts" /
            f"{persona}__{ctx.theme}__v2_parts"
        )
        source_metadata = _regular_file(
            parts_dir / "source.json", f"wardrobe rig source metadata {key}",
            root=ctx.source_root,
        )
        parts_manifest = _regular_file(
            parts_dir / "parts.json", f"wardrobe rig manifest {key}",
            root=ctx.source_root,
        )
        manifest = _load_json(parts_manifest, f"wardrobe rig manifest {key}")
        manifest_parts = manifest.get("parts") if isinstance(manifest, dict) else None
        if not isinstance(manifest_parts, list) or not manifest_parts:
            raise NativeStageError(f"wardrobe rig manifest has no parts: {key}")
        part_files = []
        seen_part_files: set[str] = set()
        for position, part in enumerate(manifest_parts):
            if not isinstance(part, dict):
                raise NativeStageError(f"wardrobe rig part row is malformed: {key}")
            filename = str(part.get("file") or "")
            if (
                not filename
                or Path(filename).name != filename
                or filename in seen_part_files
            ):
                raise NativeStageError(f"wardrobe rig part path is unsafe: {key}")
            seen_part_files.add(filename)
            part_path = _regular_file(
                parts_dir / filename,
                f"wardrobe rig part {key} row {position}",
                root=ctx.source_root,
            )
            part_files.append({
                "path": part_path.relative_to(ctx.source_root).as_posix(),
                "size": part_path.stat().st_size,
                "sha256": _sha256(part_path),
            })
        part_files.sort(key=lambda item: item["path"])
        part_tree_payload = json.dumps(
            part_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        selected.append({
            "key": key,
            "approval": row,
            "doll_path": doll.relative_to(ctx.source_root).as_posix(),
            "doll_size": doll.stat().st_size,
            "doll_sha256": _sha256(doll),
            "render_rig": {
                "normalized_source_path": normalized.relative_to(ctx.source_root).as_posix(),
                "normalized_source_size": normalized.stat().st_size,
                "normalized_source_sha256": _sha256(normalized),
                "source_metadata_path": source_metadata.relative_to(ctx.source_root).as_posix(),
                "source_metadata_size": source_metadata.stat().st_size,
                "source_metadata_sha256": _sha256(source_metadata),
                "parts_manifest_path": parts_manifest.relative_to(ctx.source_root).as_posix(),
                "parts_manifest_size": parts_manifest.stat().st_size,
                "parts_manifest_sha256": _sha256(parts_manifest),
                "part_tree_sha256": hashlib.sha256(
                    part_tree_payload.encode("utf-8")
                ).hexdigest(),
                "part_files": part_files,
            },
        })
    output = ctx.source_root / ".castle-native" / ctx.episode / "wardrobe_gate.json"
    _atomic_json(output, {
        "schema_version": "castle-reel-wardrobe-gate-v1",
        "stage": "wardrobe_gate",
        "episode": ctx.episode,
        "theme": ctx.theme,
        "turnlist_sha256": _sha256(turnlist),
        "selected": selected,
    })
    return output


def _load_spoken_unit_authority(ctx: StageContext):
    """Load the exact script/synth splitter from the configured Reel source.

    Castle deliberately does not carry a second splitter implementation. The
    source module is an explicit implementation-hash dependency of both script
    and synth, so the receipt is checked against the same text transactions the
    current synth will consume.
    """
    path = _regular_file(
        ctx.source_root / "spoken_units.py",
        "script spoken-unit authority",
        root=ctx.source_root,
    )
    module_name = (
        "_castle_reel_spoken_units_"
        + hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    )
    previous_path = list(sys.path)
    shadowed = {
        name: sys.modules.pop(name)
        for name in ("brand_semantics", "spoken_units")
        if name in sys.modules
    }
    module = None
    try:
        sys.path.insert(0, str(ctx.source_root))
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise NativeStageError(
                "script spoken-unit authority cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except NativeStageError:
        raise
    except Exception as error:
        raise NativeStageError(
            "script spoken-unit authority failed to load") from error
    finally:
        sys.path[:] = previous_path
        sys.modules.pop(module_name, None)
        sys.modules.pop("brand_semantics", None)
        sys.modules.pop("spoken_units", None)
        sys.modules.update(shadowed)

    fingerprint = getattr(module, "SPOKEN_UNIT_POLICY_FINGERPRINT", None)
    splitter = getattr(module, "split_authored_sentences", None)
    normalizer = getattr(module, "normalize_spoken_unit", None)
    if fingerprint != SCRIPT_SPOKEN_UNIT_POLICY_FINGERPRINT:
        raise NativeStageError(
            "script spoken-unit authority fingerprint is not canonical")
    if not callable(splitter) or not callable(normalizer):
        raise NativeStageError(
            "script spoken-unit authority has no canonical splitter")
    return splitter, normalizer


def _validate_script_output(ctx: StageContext) -> None:
    data = _turnlist(ctx)
    selfcheck_version = data.get("_script_selfcheck_version")
    if not isinstance(selfcheck_version, str):
        raise NativeStageError(
            "script output _script_selfcheck_version must be a string"
        )
    if selfcheck_version != SCRIPT_SELFCHECK_VERSION:
        raise NativeStageError(
            "script output _script_selfcheck_version does not match canonical "
            "authority"
        )

    expected_scalars = {
        "_script_writer_provider": SCRIPT_WRITER_PROVIDER,
        "_script_writer_model": SCRIPT_WRITER_MODEL,
        "_script_judge_provider": SCRIPT_JUDGE_PROVIDER,
        "_script_judge_model": SCRIPT_JUDGE_MODEL,
        "_script_judge_policy_version": SCRIPT_JUDGE_POLICY_VERSION,
        "_script_judge_policy_fingerprint":
            SCRIPT_JUDGE_POLICY_FINGERPRINT,
        "_script_spoken_unit_policy_fingerprint":
            SCRIPT_SPOKEN_UNIT_POLICY_FINGERPRINT,
        "_script_spoken_projection_policy_fingerprint":
            SCRIPT_SPOKEN_PROJECTION_POLICY_FINGERPRINT,
    }
    for field, expected in expected_scalars.items():
        if data.get(field) != expected:
            raise NativeStageError(
                f"script output {field} does not match canonical authority"
            )
    if data.get("_script_selfcheck_flags") != []:
        raise NativeStageError(
            "script output contains residual self-check flags")

    source = _regular_file(
        ctx.source_root / f"debate_{ctx.episode}_raw.json",
        "script raw debate", root=ctx.source_root,
    )
    if data.get("_script_source_sha256") != _sha256(source):
        raise NativeStageError(
            "script output source digest does not match current raw debate")
    turns = data["turns"]
    if data.get("_script_turns_sha256") != _stable_json_sha256(turns):
        raise NativeStageError(
            "script output turns digest does not match current turn bytes")
    input_digest = data.get("_script_judge_input_sha256")
    if not isinstance(input_digest, str) or not SHA256_RE.fullmatch(input_digest):
        raise NativeStageError(
            "script output independent judge input digest is invalid")

    actual_indices = [
        turn.get("idx") if isinstance(turn, dict) else None for turn in turns
    ]
    expected_indices = list(range(len(turns)))
    if (actual_indices != expected_indices
            or any(type(idx) is not int for idx in actual_indices)):
        raise NativeStageError(
            "script output turn idx coverage must be exact and ordered")

    raw_data = _load_json(source, "script raw debate")
    if not isinstance(raw_data, list) or not raw_data:
        raise NativeStageError("script raw debate must contain turns")
    order: list[str] = []
    for position, row in enumerate(raw_data):
        if not isinstance(row, dict):
            raise NativeStageError(
                f"script raw debate row {position} is not an object")
        speaker = row.get("speaker")
        if not isinstance(speaker, str) or not speaker:
            raise NativeStageError(
                f"script raw debate row {position} has invalid speaker")
        if order and speaker == order[0]:
            break
        if speaker in order:
            raise NativeStageError(
                "script raw debate repeats a speaker before round one completes")
        order.append(speaker)
    if len(order) < 2:
        raise NativeStageError("script raw debate has fewer than two speakers")

    target = len(order) * 5
    normalized_sources: list[dict] = []
    for row in raw_data:
        if len(normalized_sources) >= target:
            break
        expected_speaker = order[len(normalized_sources) % len(order)]
        speaker = row.get("speaker")
        text = row.get("text")
        if speaker == expected_speaker:
            normalized_sources.append(row)
            continue
        is_retry_placeholder = (
            isinstance(text, str)
            and "狀況不太好" in text
            and "暫時沒有回應" in text
            and "重試" in text
        )
        if is_retry_placeholder:
            continue
        raise NativeStageError(
            "script raw debate is not a clean five-round rotation")
    if len(normalized_sources) != target:
        raise NativeStageError(
            "script raw debate does not contain five complete rounds")
    if len(normalized_sources) != len(turns):
        raise NativeStageError(
            "script output turn count does not match normalized raw debate")

    unit_map = data.get("_script_judge_unit_map")
    unit_map_digest = data.get("_script_judge_unit_map_sha256")
    if not isinstance(unit_map, list) or not unit_map:
        raise NativeStageError(
            "script output has no independent judge unit map")
    if (not isinstance(unit_map_digest, str)
            or not SHA256_RE.fullmatch(unit_map_digest)
            or unit_map_digest != _stable_json_sha256(unit_map)):
        raise NativeStageError(
            "script output independent judge unit map digest mismatch")

    splitter, normalizer = _load_spoken_unit_authority(ctx)
    judge_rows = []
    expected_unit_ids: list[str] = []
    map_position = 0
    for position, (turn, raw_row) in enumerate(zip(turns, normalized_sources)):
        if not isinstance(turn, dict):
            raise NativeStageError(
                f"script output turn {position} is not an object")
        if turn.get("speaker") != raw_row.get("speaker"):
            raise NativeStageError(
                f"script output turn {position} speaker does not match source")
        source_text = raw_row.get("text")
        line = turn.get("text")
        if (not isinstance(source_text, str) or not source_text.strip()
                or not isinstance(line, str) or not line.strip()):
            raise NativeStageError(
                f"script judge input row {position} is incomplete")
        try:
            split_texts = splitter(line, normalizer)
        except Exception as error:
            raise NativeStageError(
                f"script spoken-unit split failed for turn {position}") from error
        if (not isinstance(split_texts, list) or not split_texts
                or any(not isinstance(text, str) for text in split_texts)
                or "".join(split_texts) != line):
            raise NativeStageError(
                f"script spoken-unit split is invalid for turn {position}")

        spoken_units = []
        for unit_idx, text in enumerate(split_texts):
            try:
                speakable = normalizer(text)
            except Exception as error:
                raise NativeStageError(
                    f"script spoken-unit normalizer failed for turn {position}"
                ) from error
            if not speakable:
                raise NativeStageError(
                    f"script spoken unit {position}/{unit_idx} is empty")
            if map_position >= len(unit_map):
                raise NativeStageError(
                    "script output independent judge unit map is incomplete")
            row = unit_map[map_position]
            expected_id = f"turn:{position}/unit:{unit_idx}"
            expected_text_sha256 = hashlib.sha256(
                text.encode("utf-8")).hexdigest()
            if (not isinstance(row, dict)
                    or set(row) != SCRIPT_JUDGE_UNIT_MAP_ROW_KEYS):
                raise NativeStageError(
                    f"script output unit map row {map_position} schema mismatch")
            if (type(row["turn_idx"]) is not int
                    or type(row["unit_idx"]) is not int):
                raise NativeStageError(
                    f"script output unit map row {map_position} indices are invalid")
            if (not isinstance(row["unit_id"], str)
                    or not SCRIPT_UNIT_ID_RE.fullmatch(row["unit_id"])
                    or row["unit_id"] != expected_id):
                raise NativeStageError(
                    f"script output unit map row {map_position} unit_id mismatch")
            if (not isinstance(row["text"], str)
                    or row["text"] != text
                    or not isinstance(row["text_sha256"], str)
                    or not SHA256_RE.fullmatch(row["text_sha256"])
                    or row["text_sha256"] != expected_text_sha256
                    or row["turn_idx"] != position
                    or row["unit_idx"] != unit_idx):
                raise NativeStageError(
                    f"script output unit map row {map_position} differs from "
                    "the canonical spoken unit")
            expected_unit_ids.append(expected_id)
            spoken_units.append({
                "unit_id": expected_id,
                "unit_idx": unit_idx,
                "text": text,
            })
            map_position += 1
        judge_rows.append({
            "idx": position,
            "speaker": turn.get("speaker"),
            "source": source_text,
            "line": line,
            "spoken_units": spoken_units,
        })
    if map_position != len(unit_map):
        raise NativeStageError(
            "script output independent judge unit map has extra rows")
    if input_digest != _stable_json_sha256(judge_rows):
        raise NativeStageError(
            "script output independent judge input digest does not match "
            "current source, turn, and spoken-unit bytes")

    attestations = data.get("_script_judge_attestations")
    attestations_digest = data.get("_script_judge_attestations_sha256")
    if not isinstance(attestations, list):
        raise NativeStageError(
            "script output has no independent judge attestations")
    if (not isinstance(attestations_digest, str)
            or not SHA256_RE.fullmatch(attestations_digest)
            or attestations_digest != _stable_json_sha256(attestations)):
        raise NativeStageError(
            "script output independent judge attestations digest mismatch")
    if len(attestations) != len(expected_unit_ids):
        raise NativeStageError(
            "script output independent judge unit coverage mismatch")
    for position, row in enumerate(attestations):
        if not isinstance(row, dict) or set(row) != SCRIPT_JUDGE_ROW_KEYS:
            raise NativeStageError(
                f"script output independent judge row {position} schema mismatch")
        unit_id = row["unit_id"]
        if (not isinstance(unit_id, str)
                or not SCRIPT_UNIT_ID_RE.fullmatch(unit_id)
                or unit_id != expected_unit_ids[position]):
            raise NativeStageError(
                f"script output independent judge row {position} unit_id mismatch")
        if any(row[field] is not True for field in SCRIPT_JUDGE_BOOLEAN_FIELDS):
            raise NativeStageError(
                f"script output independent judge row {position} is not PASS")
        if row["why"] != "":
            raise NativeStageError(
                f"script output independent judge row {position} has a reason")

    for position, turn in enumerate(turns):
        if not isinstance(turn, dict) or not str(turn.get("text") or "").strip():
            raise NativeStageError(f"script output turn {position} is empty")
        if str(turn.get("kind") or "line") != "line":
            continue
        text = str(turn["text"])
        written_only = next(
            (token for token in WRITTEN_ONLY_FEMALE_PRONOUNS if token in text),
            None,
        )
        if written_only is not None:
            raise NativeStageError(
                f"script output spoken turn {position} contains written-only "
                f"female pronoun {written_only!r}; use an audible role or noun"
            )


def _manifest_referenced_outputs(
    ctx: StageContext,
    manifest_path: Path,
    rows: Iterable[dict],
    *,
    base: Path,
    prefix: str,
    path_key: str = "audio",
) -> list[NamedOutput]:
    """Verify every manifest row, but snapshot each immutable member once.

    Content-addressed manifests may legitimately map several semantic rows to
    one byte-identical object (for example, two reviewed reaction tags with the
    same spoken text).  The manifest remains the row-level authority; Castle's
    stage bundle is a file set and must contain each referenced path only once.
    Do not skip validation for duplicate references: every row still has to
    declare the shared object's exact size and digest.
    """
    outputs = [NamedOutput(f"{prefix}_manifest", manifest_path)]
    seen_members: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise NativeStageError(f"{prefix} manifest row is not an object")
        candidate = _relative_member(base, row.get(path_key), f"{prefix} audio")
        verified = _regular_file(candidate, f"{prefix} audio", root=ctx.source_root)
        declared_size = row.get("size")
        declared_hash = str(row.get("sha256") or "")
        if not isinstance(declared_size, int) or declared_size != verified.stat().st_size:
            raise NativeStageError(f"{prefix} manifest size mismatch")
        if not SHA256_RE.fullmatch(declared_hash) or declared_hash != _sha256(verified):
            raise NativeStageError(f"{prefix} manifest digest mismatch")
        member = verified.relative_to(ctx.source_root).as_posix()
        if member in seen_members:
            continue
        seen_members.add(member)
        outputs.append(NamedOutput(f"{prefix}_{len(seen_members) - 1:03d}", verified))
    return outputs


def _champion_authority_outputs(ctx: StageContext) -> list[NamedOutput]:
    """Collect one complete immutable champion generation, fail closed."""
    root = ctx.source_root
    episode = ctx.episode
    manifest_path = root / f"champion_{episode}.json"
    manifest = _load_json(manifest_path, "champion authority")
    if not isinstance(manifest, dict):
        raise NativeStageError("champion authority must be an object")
    commit_id = str(manifest.get("commit_id") or "")
    generation_relative = f"audio_champion_{episode}/.generations/{commit_id}"
    assets = manifest.get("assets")
    cast = _turnlist_cast(ctx)
    if (
        manifest.get("schema") != "champion_audio_authority_v1"
        or str(manifest.get("debate_id") or "") != episode
        or not re.fullmatch(r"[a-z0-9-]+", commit_id)
        or manifest.get("generation") != generation_relative
        or not isinstance(assets, list)
        or len(assets) != len(cast) + 2
        or manifest.get("winner") not in cast
        or type(manifest.get("winner_idx")) is not int
        or manifest.get("seed") != int(episode)
    ):
        raise NativeStageError("champion authority identity or asset set is invalid")
    generation = root / generation_relative
    try:
        generation_info = generation.lstat()
    except OSError as error:
        raise NativeStageError("champion authority generation is missing") from error
    if generation.is_symlink() or not stat.S_ISDIR(generation_info.st_mode):
        raise NativeStageError("champion authority generation must be a real directory")

    outputs = [NamedOutput("champion_authority", manifest_path)]
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    role_counts: dict[str, int] = {}
    for position, row in enumerate(assets):
        if not isinstance(row, dict):
            raise NativeStageError("champion authority asset row is malformed")
        asset_id = str(row.get("asset_id") or "")
        role = str(row.get("role") or "")
        relative = str(row.get("audio") or "")
        if not asset_id or asset_id in seen_ids or relative in seen_paths:
            raise NativeStageError("champion authority has duplicate asset identity")
        seen_ids.add(asset_id)
        seen_paths.add(relative)
        role_counts[role] = role_counts.get(role, 0) + 1
        audio = _relative_member(root, relative, "champion audio")
        verified = _regular_file(
            audio, f"champion audio {position}", root=root)
        if verified.parent != generation.resolve():
            raise NativeStageError("champion authority audio escapes immutable generation")
        declared_size = row.get("size")
        declared_hash = str(row.get("sha256") or "")
        duration = row.get("duration")
        if (
            type(declared_size) is not int
            or declared_size <= 0
            or declared_size != verified.stat().st_size
            or SHA256_RE.fullmatch(declared_hash) is None
            or declared_hash != _sha256(verified)
            or not isinstance(duration, (int, float))
            or float(duration) <= 0
        ):
            raise NativeStageError("champion authority asset bytes or duration mismatch")
        if role != "cheers" and (
            row.get("persona") not in cast or not str(row.get("text") or "").strip()
        ):
            raise NativeStageError("champion authority speech identity is invalid")
        outputs.append(NamedOutput(f"champion_audio_{position:03d}", verified))
    expected_roles = {
        "declare": 1,
        "congrats": len(cast) - 1,
        "final_declare": 1,
        "cheers": 1,
    }
    if role_counts != expected_roles:
        raise NativeStageError("champion authority roles are incomplete or excessive")
    return outputs


def _listener_item_has_downgrade_evidence(item: Mapping[str, object]) -> bool:
    if item.get(NON_REPRODUCIBLE_FAIL_FLAG) is True:
        return True
    issues = item.get("issues") or []
    if not isinstance(issues, list):
        issues = [issues]
    return any(
        isinstance(issue, Mapping)
        and str(issue.get("problem") or "") == NON_REPRODUCIBLE_FAIL_PROBLEM
        for issue in issues
    )


def _listener_item_has_clean_fail_confirmation(
    item: Mapping[str, object],
) -> bool:
    confirmation = item.get("fail_confirmation")
    if not isinstance(confirmation, Mapping):
        return False
    return (
        str(confirmation.get("verdict") or "").upper() == LISTENER_PASS
        and not bool(confirmation.get("content_core_lost"))
    )


def _listener_nonreproducible_downgrade_is_render_tolerated(
    item: Mapping[str, object],
) -> bool:
    return (
        str(item.get("verdict") or "").upper() == LISTENER_WARN
        and str(item.get("defect_class") or "") == "other"
        and not bool(item.get("content_core_lost"))
        and _listener_item_has_downgrade_evidence(item)
        and _listener_item_has_clean_fail_confirmation(item)
    )


def _audio_ready_outputs(ctx: StageContext) -> list[NamedOutput]:
    """Verify complete listener evidence and its exact audio byte set."""
    root = ctx.source_root
    episode = ctx.episode
    report_path = root / f"audio_listener_clips_{episode}.json"
    authority_path = root / f"audio_ready_{episode}.json"
    report = _load_json(report_path, "clip listener report")
    authority = _load_json(authority_path, "audio-ready authority")
    if not isinstance(report, dict) or not isinstance(authority, dict):
        raise NativeStageError("audio-ready evidence must be JSON objects")
    items = report.get("items")
    jobs = authority.get("jobs")
    listener_version = str(report.get("_listener_version") or "")
    authority_schema_version = _audio_ready_authority_schema_version(ctx)
    quality_verdict = str(report.get("verdict") or "")
    completion = report.get("draft_completion")
    expected_completion = {
        "schema_version": DRAFT_COMPLETION_SCHEMA_VERSION,
        "execution_status": "completed",
        "quality_verdict": quality_verdict,
        "review_required": quality_verdict != LISTENER_PASS,
        "release_eligible": quality_verdict == LISTENER_PASS,
    }
    if (
        report.get("did") != episode
        or report.get("stage") != "clips"
        or quality_verdict not in {LISTENER_PASS, LISTENER_WARN, LISTENER_FAIL}
        or completion != expected_completion
        or not listener_version
        or not str(report.get("_fast_model") or "")
        or not str(report.get("_smart_model") or "")
        or not isinstance(items, list)
        or not items
        or authority.get("schema_version") != authority_schema_version
        or authority.get("debate_id") != episode
        or authority.get("verdict") != "PASS"
        or authority.get("quality_verdict") != quality_verdict
        or authority.get("review_required") != expected_completion["review_required"]
        or authority.get("release_eligible") != expected_completion["release_eligible"]
        or authority.get("draft_completion") != expected_completion
        or authority.get("listener_version") != listener_version
        or authority.get("report") != report_path.name
        or authority.get("report_size") != report_path.stat().st_size
        or authority.get("report_sha256") != _sha256(report_path)
        or not isinstance(jobs, list)
        or authority.get("job_count") != len(jobs)
        or len(jobs) != len(items)
    ):
        raise NativeStageError(
            "audio-ready authority is not a complete current draft receipt")

    report_by_id: dict[str, dict] = {}
    for item in items:
        item_id = str(item.get("id") or "") if isinstance(item, dict) else ""
        if (
            not item_id
            or item_id in report_by_id
            or item.get("verdict") not in {
                LISTENER_PASS, LISTENER_WARN, LISTENER_FAIL
            }
            or item.get("listener_version") != listener_version
            or SHA256_RE.fullmatch(str(item.get("audio_sha256") or "")) is None
            or SHA256_RE.fullmatch(str(item.get("expected_sha256") or "")) is None
        ):
            raise NativeStageError("clip listener item is malformed or duplicated")
        report_by_id[item_id] = item

    seen_paths: set[str] = set()
    seen_jobs: set[str] = set()
    normalized_jobs = []
    for position, row in enumerate(jobs):
        if not isinstance(row, dict):
            raise NativeStageError("audio-ready job row is malformed")
        job_id = str(row.get("id") or "")
        relative = str(row.get("audio") or "")
        if not job_id or job_id in seen_jobs or relative in seen_paths:
            raise NativeStageError("audio-ready jobs contain duplicate IDs or paths")
        seen_jobs.add(job_id)
        seen_paths.add(relative)
        audio = _regular_file(
            _relative_member(root, relative, "audio-ready WAV"),
            f"audio-ready WAV {position}", root=root)
        digest = str(row.get("audio_sha256") or "")
        expected_digest = str(row.get("expected_sha256") or "")
        report_item = report_by_id.get(job_id)
        if (
            report_item is None
            or type(row.get("size")) is not int
            or row["size"] != audio.stat().st_size
            or SHA256_RE.fullmatch(digest) is None
            or digest != _sha256(audio)
            or SHA256_RE.fullmatch(expected_digest) is None
            or report_item.get("audio_sha256") != digest
            or report_item.get("expected_sha256") != expected_digest
            or not str(row.get("kind") or "")
            or not str(row.get("speaker") or "")
        ):
            raise NativeStageError("audio-ready job does not bind current listener/audio bytes")
        normalized_jobs.append(row)
    if set(report_by_id) != seen_jobs:
        raise NativeStageError("audio-ready listener coverage differs from current jobs")
    if authority.get("job_set_sha256") != _stable_json_sha256(normalized_jobs):
        raise NativeStageError("audio-ready job-set digest mismatch")
    return [
        NamedOutput("audio_listener_clips", report_path),
        NamedOutput("audio_ready", authority_path),
    ]


def _audio_listener_final_outputs(ctx: StageContext) -> list[NamedOutput]:
    path = ctx.source_root / f"audio_listener_final_{ctx.episode}.json"
    report = _load_json(path, "final listener report")
    verdict = str(report.get("verdict") or "") if isinstance(report, dict) else ""
    expected_completion = {
        "schema_version": DRAFT_COMPLETION_SCHEMA_VERSION,
        "execution_status": "completed",
        "quality_verdict": verdict,
        "review_required": verdict != LISTENER_PASS,
        "release_eligible": verdict == LISTENER_PASS,
    }
    final_relative = str(report.get("final_mp4") or "")
    final_path = (
        _relative_member(
            ctx.source_root, final_relative, "final listener MP4")
        if final_relative
        else None
    )
    if (
        not isinstance(report, dict)
        or report.get("did") != ctx.episode
        or report.get("stage") != "final"
        or verdict not in {LISTENER_PASS, LISTENER_WARN, LISTENER_FAIL}
        or report.get("draft_completion") != expected_completion
        or final_path is None
        or not final_path.is_file()
        or report.get("final_mp4_sha256") != _sha256(final_path)
    ):
        raise NativeStageError(
            "final listener report has no current completed-draft receipt")
    return [NamedOutput("audio_listener_final", path)]


def _collect_outputs(ctx: StageContext, stage: str) -> list[NamedOutput]:
    episode = ctx.episode
    root = ctx.source_root
    if stage == "pull":
        path = root / f"debate_{episode}_raw.json"
        data = _load_json(path, "raw debate")
        if not isinstance(data, list) or not data:
            raise NativeStageError("raw debate must be a non-empty array")
        return [NamedOutput("raw_debate", path)]
    if stage == "forum_meta":
        return [NamedOutput("forum_meta", root / f"forum_meta_{episode}.json")]
    if stage == "script":
        return [
            NamedOutput(
                "turnlist_reel",
                root / f"turnlist_reel5_{episode}.json",
            ),
            NamedOutput(
                "script_verification",
                root / ".castle-native" / episode /
                "script_generated_verification.json",
            ),
        ]
    if stage == "script_approved":
        return [
            NamedOutput(
                "turnlist_reel",
                root / f"turnlist_reel5_{episode}.json",
            ),
            NamedOutput(
                "editorial_verification",
                root / ".castle-native" / episode /
                "script_approved_verification.json",
            ),
            NamedOutput(
                "raw_debate",
                root / f"debate_{episode}_raw.json",
            ),
            NamedOutput(
                "forum_meta",
                root / f"forum_meta_{episode}.json",
            ),
        ]
    if stage == "wardrobe_gate":
        receipt_path = _write_wardrobe_receipt(ctx)
        receipt = _load_json(receipt_path, "wardrobe gate receipt")
        selected = receipt.get("selected") if isinstance(receipt, dict) else None
        if not isinstance(selected, list) or not selected:
            raise NativeStageError("wardrobe gate receipt has no selected dolls")
        outputs = [
            NamedOutput("wardrobe_gate", receipt_path),
            NamedOutput(
                "wardrobe_gate_turnlist",
                root / f"turnlist_reel5_{ctx.episode}.json",
            ),
        ]
        for index, row in enumerate(selected):
            if not isinstance(row, dict):
                raise NativeStageError("wardrobe gate receipt selected row is malformed")
            outputs.append(NamedOutput(
                f"wardrobe_gate_doll_{index:03d}",
                _relative_member(root, row.get("doll_path"), "wardrobe doll path"),
            ))
            render_rig = row.get("render_rig")
            if not isinstance(render_rig, dict):
                raise NativeStageError("wardrobe gate receipt render rig is malformed")
            outputs.extend((
                NamedOutput(
                    f"wardrobe_gate_normalized_{index:03d}",
                    _relative_member(
                        root, render_rig.get("normalized_source_path"),
                        "normalized wardrobe path",
                    ),
                ),
                NamedOutput(
                    f"wardrobe_gate_source_meta_{index:03d}",
                    _relative_member(
                        root, render_rig.get("source_metadata_path"),
                        "wardrobe source metadata path",
                    ),
                ),
                NamedOutput(
                    f"wardrobe_gate_parts_manifest_{index:03d}",
                    _relative_member(
                        root, render_rig.get("parts_manifest_path"),
                        "wardrobe parts manifest path",
                    ),
                ),
            ))
            part_files = render_rig.get("part_files")
            if not isinstance(part_files, list) or not part_files:
                raise NativeStageError("wardrobe gate receipt has no rig part files")
            for part_index, part in enumerate(part_files):
                if not isinstance(part, dict):
                    raise NativeStageError("wardrobe gate receipt rig part is malformed")
                outputs.append(NamedOutput(
                    f"wardrobe_gate_part_{index:03d}_{part_index:03d}",
                    _relative_member(
                        root, part.get("path"), "wardrobe rig part path"),
                ))
        return outputs
    if stage == "scene":
        return [NamedOutput("scene", root / "tachie" / "scenes" / f"{ctx.theme}.png")]
    if stage == "host_rig":
        base = root / "tachie" / "parts" / f"host_{ctx.theme}_parts"
        return [
            NamedOutput("host_body", base / "00_body.png"),
            NamedOutput("host_face", base / "01_face.png"),
            NamedOutput("host_mouth", base / "02_mouth.png"),
            NamedOutput("host_parts", base / "parts.json"),
        ]
    if stage == "synth":
        audio = root / f"audio_reel5_{episode}"
        outputs = [
            NamedOutput("spoken", audio / "spoken.json"),
            NamedOutput("timing", audio / f"timing_{episode}.json"),
            NamedOutput("swaps", audio / f"swaps_{episode}.json"),
            NamedOutput("main_audio_policy", audio / ".main_synth_policy.json"),
        ]
        for position, turn in enumerate(_ordered_turns(ctx)):
            speaker = str(turn.get("speaker") or "")
            if not SAFE_PERSONA_RE.fullmatch(speaker):
                raise NativeStageError(f"turnlist row {position} has unsafe speaker")
            outputs.append(NamedOutput(
                f"sister_audio_{turn['idx']:03d}_{speaker}",
                audio / f"{turn['idx']:02d}_{speaker}.wav",
            ))
            draft_receipt = (
                audio / ".draft-fallback" /
                f"{turn['idx']:02d}_{speaker}.json"
            )
            if draft_receipt.is_file():
                outputs.append(NamedOutput(
                    f"sister_audio_draft_{turn['idx']:03d}_{speaker}",
                    draft_receipt,
                ))
        return outputs
    if stage == "beats":
        return [NamedOutput("peer_beats", root / f"react_beats_peer_{episode}.json")]
    if stage == "peer_synth":
        beats_path = root / f"react_beats_peer_{episode}.json"
        data = _load_json(beats_path, "peer beats")
        if not isinstance(data, dict):
            raise NativeStageError("peer beats must be an object")
        outputs = [NamedOutput("peer_beats", beats_path)]
        rows = []
        for key in sorted((key for key in data if str(key).isdigit()), key=int):
            beat = data[key]
            if not isinstance(beat, dict) or not isinstance(beat.get("reactors"), list):
                raise NativeStageError("peer beats row is malformed")
            rows.extend(row for row in beat["reactors"] if row.get("show") is True)
        for index, row in enumerate(rows):
            path = _relative_member(root, row.get("audio"), "peer audio")
            verified = _regular_file(path, f"peer audio {index}", root=root)
            if row.get("size") != verified.stat().st_size or row.get("sha256") != _sha256(verified):
                raise NativeStageError("peer authority bytes do not match peer beats")
            outputs.append(NamedOutput(f"peer_audio_{index:03d}", path))
        return outputs
    if stage == "host_reaction_bank":
        manifest_path = root / "audio_react" / "host_reactions_manifest.json"
        data = _load_json(manifest_path, "host reaction manifest")
        if not isinstance(data, dict) or not isinstance(data.get("rows"), dict):
            raise NativeStageError("host reaction manifest is malformed")
        cast = set(_turnlist_cast(ctx))
        rows = sorted([
            row for row in data["rows"].values()
            if isinstance(row, dict) and row.get("persona") in cast
        ], key=lambda row: (
            str(row.get("persona") or ""),
            str(row.get("tag") or ""),
            str(row.get("audio") or ""),
        ))
        if not rows:
            raise NativeStageError("host reaction manifest has no rows for this cast")
        return _manifest_referenced_outputs(
            ctx, manifest_path, rows, base=manifest_path.parent,
            prefix="host_reaction",
        )
    if stage == "host_text":
        return [
            NamedOutput("champion_stub", root / f"champion_stub_{episode}.json"),
            NamedOutput("turnlist_host", root / f"turnlist_host_{episode}.json"),
            NamedOutput("host_beats", root / f"react_beats_host_{episode}.json"),
        ]
    if stage == "host_synth":
        manifest_path = root / f"audio_host_{episode}" / "host_audio_manifest.json"
        data = _load_json(manifest_path, "host audio manifest")
        if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
            raise NativeStageError("host audio manifest is malformed")
        return _manifest_referenced_outputs(
            ctx, manifest_path, data["artifacts"], base=root,
            prefix="host_audio",
        )
    if stage == "champion_synth":
        return _champion_authority_outputs(ctx)
    if stage == "audio_listener_clips":
        return _audio_ready_outputs(ctx)
    if stage == "audio_listener_final":
        return _audio_listener_final_outputs(ctx)
    raise NativeStageError("stage has no output collector")


def _regular_file_fingerprint(path: Path) -> tuple | None:
    try:
        verified = _regular_file(path, path.name)
        info = verified.stat()
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            _sha256(verified),
        )
    except (NativeStageError, OSError):
        return None


# Both shapes that Voice's `verify` can return are gate-passed receipts, and
# synth accepts both. The review lane promotes a draft into the second one, so
# naming only the first here silently kept overwriting exactly the receipts
# this guard exists to protect. Naming them rather than trusting `ok` alone
# means a third authority arriving later is a decision, not a default.
_CANONICAL_RECEIPT_AUTHORITY_TYPES = (
    "editorial_approval",
    "generated_script_receipt",
)


def _canonical_turnlist_has_current_receipt(
    ctx: StageContext,
    turnlist: Path,
) -> bool:
    if not turnlist.exists():
        return False
    command = BackendCommand(
        (
            str(ctx.python),
            str(_backend(ctx, "script_receipt_authority.py")),
            "verify",
            turnlist.name,
        ),
        capture_stdout=True,
    )
    try:
        output = _run_command(ctx, command)
        verification = json.loads(output.strip())
    except (NativeStageError, json.JSONDecodeError):
        return False
    raw_path = ctx.source_root / f"debate_{ctx.episode}_raw.json"
    try:
        turnlist_sha = _sha256(turnlist)
        raw_sha = _sha256(raw_path)
    except NativeStageError:
        return False
    return (
        isinstance(verification, dict)
        and verification.get("ok") is True
        and verification.get("authority_type")
        in _CANONICAL_RECEIPT_AUTHORITY_TYPES
        and verification.get("turnlist_sha256") == turnlist_sha
        and verification.get("raw_sha256") == raw_sha
        and isinstance(verification.get("turn_count"), int)
        and verification["turn_count"] >= 1
    )


def _verified_script_needs_review_result(
    ctx: StageContext,
    before: tuple | None,
) -> dict:
    """Adopt exit-5 only after Voice verifies the newly written draft."""
    draft = (
        ctx.source_root /
        f"turnlist_reel5_{ctx.episode}.needs_review.json"
    )
    after = _regular_file_fingerprint(draft)
    if after is None or after == before:
        raise NativeStageError(
            "script exit 5 did not publish a fresh needs-review draft")
    command = BackendCommand(
        (
            str(ctx.python),
            str(_backend(ctx, "script_receipt_authority.py")),
            "verify-draft",
            draft.name,
        ),
        capture_stdout=True,
    )
    output = _run_command(ctx, command)
    try:
        verification = json.loads(output.strip())
    except json.JSONDecodeError as error:
        raise NativeStageError(
            "Voice needs-review verifier returned invalid JSON") from error
    if (
        not isinstance(verification, dict)
        or verification.get("ok") is not True
        or verification.get("authority_type") != "needs_review_draft"
        or verification.get("status") != "needs_review"
        or verification.get("release_eligible") is not False
        or not isinstance(verification.get("review_error_type"), str)
        or verification.get("draft_sha256") != _sha256(draft)
        or verification.get("raw_sha256") != _sha256(
            ctx.source_root / f"debate_{ctx.episode}_raw.json")
        or not isinstance(verification.get("turn_count"), int)
        or verification["turn_count"] < 1
    ):
        raise NativeStageError(
            "Voice needs-review verifier rejected current draft authority")
    receipt = (
        ctx.source_root / ".castle-native" / ctx.episode /
        "script_needs_review_verification.json"
    )
    if verification["review_error_type"] == "ScriptReviewRejected":
        verification["review_summary"] = _script_review_rejection_summary(draft)
    _atomic_json(receipt, verification)
    turnlist = ctx.source_root / f"turnlist_reel5_{ctx.episode}.json"
    # Publish the verified draft at the canonical turnlist path too. Every
    # downstream stage - wardrobe_gate, synth, beats, host_reaction_bank - reads
    # turnlist_reel5_<ep>.json, so leaving the draft under its own name starved
    # the whole pipeline. Publication is still withheld by publish_mode being
    # pinned to 'review_only' in scripts/rolling_reel_portfolio.py:175 and by
    # the operator approving the finished reel; see
    # webui/orchestrator/worker.py:1069 for why release_eligible is not the
    # downstream release guard.
    # If review already promoted this episode's current raw into a gate-passed
    # receipt at the canonical path, keep those bytes: a judged script beats an
    # unjudged one. Otherwise copy the draft bytes, not a re-serialisation:
    # draft_sha256 is the verified authority above, so a canonical draft copy
    # has to hash identically.
    if not _canonical_turnlist_has_current_receipt(ctx, turnlist):
        scratch = turnlist.with_name(turnlist.name + ".needs_review.tmp")
        scratch.write_bytes(draft.read_bytes())
        os.replace(scratch, turnlist)
        if _sha256(turnlist) != verification["draft_sha256"]:
            raise NativeStageError(
                "needs-review draft did not publish to the canonical turnlist")
    outputs = [
        NamedOutput("turnlist_reel", turnlist),
        NamedOutput("script_needs_review", draft),
        NamedOutput("script_review_verification", receipt),
    ]
    payload = _result_payload(ctx, "script", outputs)
    _atomic_json(ctx.result_manifest, payload)
    return payload


def _script_review_rejection_summary(draft: Path) -> str:
    try:
        payload = json.loads(draft.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NativeStageError(
            "Voice script judge rejected needs-review draft but rejection "
            "flags could not be read") from error
    if not isinstance(payload, dict):
        raise NativeStageError(
            "Voice script judge rejected needs-review draft but draft JSON "
            "is not an object")
    flags = payload.get("_script_review_flags")
    if not isinstance(flags, list) or not flags:
        raise NativeStageError(
            "Voice script judge rejected needs-review draft but rejection "
            "flags are missing or invalid")
    rows = []
    for flag in flags:
        if not isinstance(flag, dict):
            raise NativeStageError(
                "Voice script judge rejected needs-review draft but rejection "
                "flags are invalid")
        idx = flag.get("idx")
        rule = flag.get("rule")
        if not isinstance(rule, str):
            raise NativeStageError(
                "Voice script judge rejected needs-review draft but rejection "
                "flags are invalid")
        why = flag.get("why")
        detail = f"idx={idx} rule={rule}"
        if isinstance(why, str) and why:
            detail += f" why={why}"
        rows.append(detail)
    return "; ".join(rows)


def _audio_review_manifest_fingerprints(ctx: StageContext) -> dict[str, tuple]:
    quarantine = (
        ctx.source_root / ".castle-native" / ctx.episode /
        "audio-review-quarantine"
    )
    fingerprints: dict[str, tuple] = {}
    try:
        manifests = list(quarantine.glob("*/manifest.json"))
    except OSError:
        return fingerprints
    for manifest in manifests:
        try:
            path = _regular_file(
                manifest, "audio review quarantine manifest",
                root=ctx.source_root,
            )
            info = path.stat()
            fingerprints[path.relative_to(ctx.source_root).as_posix()] = (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                _sha256(path),
            )
        except (NativeStageError, OSError, ValueError):
            continue
    return fingerprints


def _authored_sentence_at(
    ctx: StageContext,
    text: object,
    sentence_idx: int,
) -> str:
    """Resolve the failed sentence with the splitter synth itself used.

    `failed_sentence_idx` indexes the canonical spoken units, and that
    segmentation is not punctuation splitting: the policy merges short
    sentences and splits long ones at commas, so a local regex resolves the
    index against a different list and names the wrong line. The review desk
    acts on this string, so naming the wrong line is worse than naming none -
    on any doubt return empty and leave the index and the authored text, which
    are authority, to speak for themselves.
    """
    source = text if isinstance(text, str) else ""
    if not source:
        return ""
    try:
        splitter, _ = _load_spoken_unit_authority(ctx)
        parts = list(splitter(source))
    except (NativeStageError, OSError, ValueError, TypeError):
        return ""
    if 0 <= sentence_idx < len(parts):
        part = parts[sentence_idx]
        return part if isinstance(part, str) else ""
    return ""


def _synth_sentence_rejection(ctx: StageContext, manifest: dict) -> dict | None:
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        return None
    for row in candidates:
        if (
            isinstance(row, dict)
            and row.get("scope") == "sentence_rejected"
            and row.get("failed_sentence_idx") is not None
        ):
            try:
                sentence_idx = int(row["failed_sentence_idx"])
            except (TypeError, ValueError):
                return None
            return {
                "episode": manifest.get("episode"),
                "turn_idx": manifest.get("turn_idx"),
                "speaker": manifest.get("speaker"),
                "failed_sentence_idx": sentence_idx,
                "authored_text": manifest.get("authored_text"),
                "failed_sentence": _authored_sentence_at(
                    ctx, manifest.get("authored_text"), sentence_idx),
            }
    return None


def _verified_synth_needs_review_error(
    ctx: StageContext,
    before: dict[str, tuple],
) -> NativeStageError:
    after = _audio_review_manifest_fingerprints(ctx)
    fresh = sorted(
        relative for relative, fingerprint in after.items()
        if before.get(relative) != fingerprint
    )
    for relative in fresh:
        path = ctx.source_root / relative
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        rejection = _synth_sentence_rejection(ctx, manifest)
        if rejection is None:
            continue
        receipt = {
            "authority_type": "synth_sentence_hard_fail",
            "ok": True,
            "status": "needs_review",
            "failure_class": "sentence_hard_fail",
            "manifest": relative,
            "manifest_sha256": _sha256(path),
            "transaction_outcome": manifest.get("transaction_outcome"),
            **rejection,
        }
        receipt_path = (
            ctx.source_root / ".castle-native" / ctx.episode /
            "synth_needs_review_verification.json"
        )
        _atomic_json(receipt_path, receipt)
        marker = json.dumps(
            {
                "reason": "synth_sentence_hard_fail",
                **receipt,
                "manifest": relative,
                "verification": receipt_path.relative_to(
                    ctx.source_root).as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        error = NativeStageError(f"synth_sentence_hard_fail: {marker}")
        error.returncode = 5
        return error
    raise NativeStageError(
        "synth exit 5 did not publish a verified sentence rejection manifest")


def _existing_output_fingerprints(ctx: StageContext, stage: str) -> dict[str, tuple]:
    """Capture valid old outputs so they cannot be adopted as fresh success."""
    if stage == "wardrobe_gate":
        # The gate output is a derived receipt and collecting it performs the
        # derivation.  Do not write it before the approval backend has passed.
        return {}
    try:
        outputs = _collect_outputs(ctx, stage)
    except (NativeStageError, OSError, ValueError, KeyError, TypeError):
        return {}
    fingerprints = {}
    for output in outputs:
        try:
            path = _regular_file(output.path, output.name, root=ctx.source_root)
            info = path.stat()
            fingerprints[path.relative_to(ctx.source_root).as_posix()] = (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                _sha256(path),
            )
        except (NativeStageError, OSError, ValueError):
            continue
    return fingerprints


def _reject_stale_outputs(
    ctx: StageContext,
    stage: str,
    before: dict[str, tuple],
    outputs: list[NamedOutput],
) -> None:
    if stage in AUTHORITY_VERIFIED_IDENTICAL_OUTPUT_STAGES:
        # These routes either publish immutable generations/content-addressed
        # caches or a complete audio-ready proof.  Each backend was invoked and
        # the stage-specific collector above revalidated the exact current byte
        # authority, so identical bytes are a healthy cache hit, not stale
        # adoption.
        return
    stale = []
    for output in outputs:
        path = _regular_file(output.path, output.name, root=ctx.source_root)
        relative = path.relative_to(ctx.source_root).as_posix()
        previous = before.get(relative)
        if previous is None:
            continue
        info = path.stat()
        current = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            _sha256(path),
        )
        if current == previous:
            stale.append(output.name)
    if stale:
        names = ", ".join(sorted(stale)[:5])
        raise NativeStageError(f"backend left declared output stale: {names}")


def _result_payload(ctx: StageContext, stage: str, outputs: list[NamedOutput]) -> dict:
    rows = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for output in outputs:
        if not SAFE_OUTPUT_NAME_RE.fullmatch(output.name):
            raise NativeStageError("output has an unsafe name")
        if output.name in seen_names:
            raise NativeStageError("stage produced duplicate output names")
        path = _regular_file(output.path, output.name, root=ctx.source_root)
        relative = path.relative_to(ctx.source_root).as_posix()
        if relative in seen_paths:
            raise NativeStageError("stage produced duplicate output paths")
        seen_names.add(output.name)
        seen_paths.add(relative)
        rows.append({
            "name": output.name,
            "path": relative,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    rows.sort(key=lambda row: (row["name"], row["path"]))
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "stage_version": STAGE_RUNNER_CONTRACT_VERSIONS[stage],
        "stage": stage,
        "episode": ctx.episode,
        "theme": ctx.theme,
        "outputs": rows,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if str(ctx.source_root) in encoded or re.search(r'"path"\s*:\s*"/', encoded):
        raise NativeStageError("result manifest contains an absolute source path")
    return payload


def run_native_stage(ctx: StageContext, stage: str) -> dict:
    _preflight(ctx, stage)
    before = _existing_output_fingerprints(ctx, stage)
    draft_before = (
        _regular_file_fingerprint(
            ctx.source_root /
            f"turnlist_reel5_{ctx.episode}.needs_review.json"
        )
        if stage == "script"
        else None
    )
    synth_review_before = (
        _audio_review_manifest_fingerprints(ctx)
        if stage == "synth"
        else {}
    )
    commands = backend_commands(ctx, stage)
    command_outputs = []
    try:
        for command in commands:
            command_outputs.append(_run_command(ctx, command))
    except NativeStageError as error:
        if stage == "script" and getattr(error, "returncode", None) == 5:
            return _verified_script_needs_review_result(ctx, draft_before)
        if stage == "synth" and getattr(error, "returncode", None) == 5:
            raise _verified_synth_needs_review_error(
                ctx, synth_review_before) from error
        raise
    if stage == "forum_meta":
        _write_forum_meta(ctx)
    if stage in {"script", "script_approved"}:
        try:
            verification = json.loads(command_outputs[-1].strip())
        except (IndexError, json.JSONDecodeError) as error:
            raise NativeStageError(
                "Voice editorial verifier returned invalid JSON") from error
        allowed_authorities = (
            {
                ("editorial_approval", "frozen_exact_text", True),
                ("generated_script_receipt", "canonical", False),
            }
            if stage == "script_approved"
            else {("generated_script_receipt", "canonical", False)}
        )
        turnlist_path = (
            ctx.source_root / f"turnlist_reel5_{ctx.episode}.json")
        raw_path = ctx.source_root / f"debate_{ctx.episode}_raw.json"
        turnlist = _turnlist(ctx)
        if (
            not isinstance(verification, dict)
            or verification.get("ok") is not True
            or (
                verification.get("authority_type"),
                verification.get("synthesis_mode"),
                verification.get("exact_text"),
            ) not in allowed_authorities
            or verification.get("turnlist_sha256") != _sha256(turnlist_path)
            or verification.get("raw_sha256") != _sha256(raw_path)
            or verification.get("turn_count") != len(turnlist["turns"])
        ):
            raise NativeStageError(
                "Voice script verifier rejected current authority")
        _atomic_json(
            ctx.source_root / ".castle-native" / ctx.episode /
            (
                "script_approved_verification.json"
                if stage == "script_approved"
                else "script_generated_verification.json"
            ),
            verification,
        )
        if stage == "script_approved":
            # Frozen recovery never performs a fresh transport. Derive the
            # sanitized metadata locally from the exact raw bytes the Voice
            # editorial authority just verified and snapshot both downstream.
            _write_forum_meta(ctx)
    outputs = _collect_outputs(ctx, stage)
    if stage not in {"scene", "wardrobe_gate"}:
        _reject_stale_outputs(ctx, stage, before, outputs)
    payload = _result_payload(ctx, stage, outputs)
    _atomic_json(ctx.result_manifest, payload)
    return payload


def _episode(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,7}", text):
        raise argparse.ArgumentTypeError("episode must be 1-8 digits and non-zero")
    return text


def _theme(value: str) -> str:
    text = str(value or "").strip()
    if text not in THEMES:
        raise argparse.ArgumentTypeError("theme is not one of the canonical Reel themes")
    return text


def _main_audio_resynth_idxs(value: str) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    clean: list[int] = []
    seen: set[int] = set()
    for raw in text.split(","):
        part = raw.strip()
        if not part.isdigit():
            raise argparse.ArgumentTypeError(
                "main audio resynth indexes must be comma-separated integers"
            )
        index = int(part)
        if index < 0 or index > 99:
            raise argparse.ArgumentTypeError(
                "main audio resynth indexes must be 0..99"
            )
        if index in seen:
            continue
        seen.add(index)
        clean.append(index)
    if len(clean) > 8:
        raise argparse.ArgumentTypeError(
            "main audio resynth index list is too large for one repair"
        )
    return tuple(sorted(clean))


def _main_audio_cache_revision(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", text):
        raise argparse.ArgumentTypeError(
            "main audio cache revision must be 1-128 safe ASCII characters"
        )
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one Castle-owned native Reel stage")
    parser.add_argument("--stage", choices=ALLOWED_STAGES, required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--episode", type=_episode, required=True)
    parser.add_argument("--theme", type=_theme)
    parser.add_argument("--result-manifest", required=True)
    parser.add_argument(
        "--main-audio-resynth-idxs",
        type=_main_audio_resynth_idxs,
        default=(),
    )
    parser.add_argument(
        "--main-audio-cache-revision",
        type=_main_audio_cache_revision,
        default=None,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = Path(args.result_manifest).expanduser().absolute()
        if result.exists():
            if result.is_symlink() or not result.is_file():
                raise NativeStageError("existing result manifest must be a regular file")
            result.unlink()
        ctx = StageContext(
            source_root=_source_root(args.source_root),
            episode=args.episode,
            theme=args.theme,
            result_manifest=result,
            python=_python_path(),
            main_audio_resynth_idxs=args.main_audio_resynth_idxs,
            main_audio_cache_revision=args.main_audio_cache_revision or "",
        )
        if ctx.main_audio_resynth_idxs:
            if args.stage != "synth":
                raise NativeStageError(
                    "main audio resynth indexes are only valid for synth"
                )
            if not ctx.main_audio_cache_revision:
                raise NativeStageError(
                    "main audio resynth requires a cache revision"
                )
        elif ctx.main_audio_cache_revision:
            raise NativeStageError(
                "main audio cache revision requires resynth indexes"
            )
        run_native_stage(ctx, args.stage)
    except NativeStageError as error:
        print(f"reel_native_stage failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
